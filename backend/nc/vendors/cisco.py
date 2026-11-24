"""Cisco IOS / IOS XE / NX-OS adapter."""
from __future__ import annotations

import re

from nc.core.errors import NCError, Unsupported
from nc.vendors.base import PARTIAL, Check, DeviceContext, Identity, Plan, Probe, VendorAdapter, mask


def _re(s: str) -> str:
    return re.escape(s)


class CiscoAdapter(VendorAdapter):
    key = "cisco"
    netmiko_type = "cisco_ios"
    error_patterns = [r"^% ?(Invalid|Incomplete|Ambiguous|Unrecognized|Bad|Access denied|Cannot|Failed|Error)",
                      r"Invalid input detected", r"Invalid command", r"Syntax error", r"^ERROR:"]
    iface_ip_command = "show ip interface brief"
    metrics_commands = ["show processes cpu | include CPU utilization", "show processes memory | include Processor Pool"]
    neighbor_commands = ["show lldp neighbors detail", "show cdp neighbors detail"]

    # ---------------- identification ----------------
    def identify_commands(self):
        return ["show version"]

    def parse_identity(self, outputs):
        out = outputs.get("show version", "")
        nx = "NX-OS" in out or "Nexus" in out
        xe = "IOS XE" in out or "IOS-XE" in out
        if not (nx or xe or re.search(r"Cisco IOS Software|Cisco Internetwork Operating System", out)):
            raise NCError("not_cisco", "The device output does not look like Cisco IOS / IOS XE / NX-OS.", 422)
        family = "nxos" if nx else ("iosxe" if xe else "ios")
        ver = None
        for pat in (r"NXOS: version\s+(\S+)", r"system:\s+version\s+(\S+)", r"IOS XE Software, Version\s+(\S+)",
                    r"Cisco IOS Software.*?Version\s+([^,\s]+)", r"Version\s+(\d[\w().]+)"):
            if m := re.search(pat, out):
                ver = m[1].rstrip(",")
                break
        model = None
        if m := re.search(r"^\s*[Cc]isco\s+(\S+)(?:\s+\(.*?\))?\s+(?:processor|Chassis)", out, re.M):
            model = m[1]
        elif m := re.search(r"^\s*[Cc]isco\s+(Nexus\S*\s+\S+)\s+Chassis", out, re.M):
            model = m[1]
        serial = None
        if m := re.search(r"(?:Processor board ID|System serial number\s*:)\s*(\S+)", out):
            serial = m[1]
        host = None
        if m := re.search(r"Device name:\s*(\S+)", out):
            host = m[1]
        elif m := re.search(r"^(?!Kernel)(\S+)\s+uptime is", out, re.M):
            host = m[1]
        up = None
        if m := re.search(r"uptime is\s+(.+)$", out, re.M):
            up = m[1].strip()
        elif m := re.search(r"Kernel uptime is\s+(.+)$", out, re.M):
            up = m[1].strip()
        role = None
        if model:
            if re.match(r"(WS-C|C9[2-6]\d\d|C3\d\d\d|C2\d\d\d|C1[0-9]{3}-|N\d|Nexus|C45|C65)", model, re.I):
                role = "switch"
            elif re.match(r"(ISR|ASR|CSR|C8\d\d\d|C11\d\d|C1[0-9]\d\d-|IR|CISCO\d)", model, re.I):
                role = "router"
        return Identity("cisco", family, ver, model, serial, host, up, role)

    def probes(self, identity):
        inc = "show running-config | include "
        return [
            Probe(["vlan_database"], "show vlan brief"),
            Probe(["switchport"], "show interfaces switchport"),
            Probe(["interface"], "show ip interface brief"),
            Probe(["static_route"], inc + "^ip route"),
            Probe(["ntp"], inc + "^ntp"),
            Probe(["dns"], inc + "^ip name-server"),
            Probe(["snmp"], inc + "^snmp-server"),
            Probe(["syslog"], inc + "^logging"),
            Probe(["hostname"], inc + "^hostname"),
            Probe(["system"], "show startup-config | include ^hostname"),
        ]

    def refine_capabilities(self, caps, identity):
        if identity.os_family == "nxos":
            caps["metrics"] = {"status": "unsupported", "reason": "NX-OS metric parsers are not implemented yet"}
        else:
            caps.setdefault("metrics", {"status": "supported", "reason": "IOS/IOS XE CPU and memory commands"})
        vdb = caps.get("vlan_database", {})
        caps["svi"] = {**vdb, "reason": "SVI (interface vlan) needs VLAN/switching support: " + vdb.get("reason", "")}
        caps["vlan_interface"] = {"status": "unsupported", "reason": "Cisco uses VLAN database + SVI (kind=svi), not tagged sub-interfaces"}
        if caps.get("static_route", {}).get("status") == "supported":
            pass
        return caps

    def parse_interface_ips(self, outputs):
        res = {}
        for ln in outputs.get(self.iface_ip_command or "", "").splitlines():
            m = re.match(r"^(\S+)\s+(\d{1,3}(?:\.\d{1,3}){3})\s+", ln)
            if m:
                res[m[1]] = m[2]
        return res

    # ---------------- backups ----------------
    def backup_commands(self):
        return {"running": "show running-config", "startup": "show startup-config"}

    # ---------------- configuration ----------------
    def translate(self, spec, ctx: DeviceContext) -> Plan:
        fn = getattr(self, f"_t_{spec.module}")
        return fn(spec, ctx)

    def _nx(self, ctx):
        return ctx.os_family == "nxos"

    def _save_cmds(self, ctx):
        return ["copy running-config startup-config"] if self._nx(ctx) else ["write memory"]

    def _t_hostname(self, s, ctx):
        return Plan([f"hostname {s.hostname}"], [f"Set hostname to {s.hostname}"],
                    [Check("show running-config | include ^hostname", [rf"^hostname {_re(s.hostname)}$"])],
                    state_command="show running-config | include ^hostname",
                    warnings=["The CLI prompt and device identity change immediately."])

    def _t_vlan(self, s, ctx):
        n = s.vlan_id
        if s.kind == "vlan_interface":
            raise Unsupported("Cisco devices do not use tagged sub-interface VLANs here.",
                              "Use kind=vlan_database for the VLAN and kind=svi for its Layer 3 interface.")
        if s.kind == "vlan_database":
            if not ctx.has("vlan_database"):
                raise Unsupported("This device did not accept VLAN database commands (not a switch or no VLAN license).")
            if n == 1 or 1002 <= n <= 1005:
                raise Unsupported(f"VLAN {n} is reserved on Cisco switches and cannot be created or removed.")
            chk = Check(f"show vlan id {n}", [rf"^{n}\s+" + (_re(s.name) if s.name else r"\S+")], [])
            exists = Check(f"show vlan id {n}", [rf"^{n}\s+\S+"])
            if s.action == "present":
                cmds = [f"vlan {n}"] + ([f"name {s.name}"] if s.name else [])
                return Plan(cmds, [f"Create/update VLAN {n}" + (f" named {s.name}" if s.name else "")], [chk],
                            state_command=f"show vlan id {n}", existence=exists, rollback=[f"no vlan {n}"])
            return Plan([f"no vlan {n}"], [f"Delete VLAN {n} (ports in it lose connectivity)"],
                        [Check(f"show vlan id {n}", [], [rf"^{n}\s+\S+"])], state_command=f"show vlan id {n}",
                        warnings=["Ports assigned to this VLAN become inactive until reassigned."])
        # svi
        if s.action == "absent":
            return Plan([f"no interface vlan {n}"], [f"Remove interface Vlan{n}"],
                        [Check(f"show running-config | include ^interface Vlan{n}$", [], [rf"^interface Vlan{n}$"])],
                        state_command=f"show running-config interface vlan {n}")
        cmds = [f"interface vlan {n}"]
        present = []
        if s.ipv4:
            ip, m = mask(s.ipv4)
            cmds.append(f"ip address {ip}/{s.ipv4.split('/')[1]}" if self._nx(ctx) else f"ip address {ip} {m}")
            present.append(rf"^\s*ip address {_re(ip)}[ /]")
        cmds.append("no shutdown")
        return Plan(cmds, [f"Configure SVI Vlan{n}" + (f" with {s.ipv4}" if s.ipv4 else "")],
                    [Check(f"show running-config interface vlan {n}", present, [r"^\s*shutdown\s*$"])],
                    state_command=f"show running-config interface vlan {n}",
                    existence=Check(f"show running-config | include ^interface Vlan{n}$", [rf"^interface Vlan{n}$"]),
                    rollback=[f"no interface vlan {n}"])

    def _t_interface(self, s, ctx):
        cmds, summ, present, absent, warn = [f"interface {s.name}"], [], [], [], []
        if s.description is not None:
            cmds.append(f"description {s.description}")
            summ.append(f"description = '{s.description}'")
            present.append(rf"^\s*description {_re(s.description)}\s*$")
        if s.mode:
            if not ctx.has("switchport") and s.mode != "routed":
                raise Unsupported("Switchport commands were not accepted by this device (probably a router).")
            if s.mode == "access":
                cmds += ["switchport mode access", f"switchport access vlan {s.access_vlan}"]
                present += [r"^\s*switchport mode access", rf"^\s*switchport access vlan {s.access_vlan}\s*$"]
                summ.append(f"access port in VLAN {s.access_vlan}")
            elif s.mode == "trunk":
                if not self._nx(ctx):
                    warn.append("Older IOS switches require 'switchport trunk encapsulation dot1q' first; this is not sent automatically.")
                cmds.append("switchport mode trunk")
                present.append(r"^\s*switchport mode trunk")
                if s.allowed_vlans:
                    cmds.append(f"switchport trunk allowed vlan {s.allowed_vlans}")
                    present.append(rf"^\s*switchport trunk allowed vlan {_re(s.allowed_vlans)}\s*$")
                if s.native_vlan:
                    cmds.append(f"switchport trunk native vlan {s.native_vlan}")
                    present.append(rf"^\s*switchport trunk native vlan {s.native_vlan}\s*$")
                summ.append("trunk port" + (f", allowed {s.allowed_vlans}" if s.allowed_vlans else ""))
            else:
                if ctx.has("switchport"):
                    cmds.append("no switchport")
                    absent.append(r"^\s*switchport\s*$")
                summ.append("routed port")
        if s.ipv4:
            ip, m = mask(s.ipv4)
            cmds.append(f"ip address {ip}/{s.ipv4.split('/')[1]}" if self._nx(ctx) else f"ip address {ip} {m}")
            present.append(rf"^\s*ip address {_re(ip)}[ /]")
            summ.append(f"IPv4 {s.ipv4}")
        if s.mtu:
            cmds.append(f"mtu {s.mtu}")
            present.append(rf"^\s*mtu {s.mtu}\s*$")
            summ.append(f"MTU {s.mtu}")
        if s.enabled is not None:
            cmds.append("no shutdown" if s.enabled else "shutdown")
            (absent if s.enabled else present).append(r"^\s*shutdown\s*$")
            summ.append("administratively " + ("up" if s.enabled else "DOWN"))
        return Plan(cmds, [f"{s.name}: " + "; ".join(summ)],
                    [Check(f"show running-config interface {s.name}", present, absent)],
                    state_command=f"show running-config interface {s.name}", warnings=warn)

    def _t_static_route(self, s, ctx):
        import ipaddress
        net = ipaddress.ip_network(s.destination)
        if self._nx(ctx):
            body = f"ip route {s.destination} {s.next_hop}" + (f" {s.distance}" if s.distance else "")
            find = rf"^ip route {_re(s.destination)} {_re(s.next_hop)}"
        else:
            body = f"ip route {net.network_address} {net.netmask} {s.next_hop}" + (f" {s.distance}" if s.distance else "")
            find = rf"^ip route {_re(str(net.network_address))} {_re(str(net.netmask))} {_re(s.next_hop)}"
        chk = Check("show running-config | include ^ip route", [find + (rf" {s.distance}\b" if s.distance else r"(\s|$)")])
        if s.action == "present":
            return Plan([body], [f"Route {s.destination} via {s.next_hop}"], [chk],
                        state_command="show running-config | include ^ip route", rollback=["no " + body])
        return Plan(["no " + body], [f"Remove route {s.destination} via {s.next_hop}"],
                    [Check("show running-config | include ^ip route", [], [find])],
                    state_command="show running-config | include ^ip route")

    def _t_ntp(self, s, ctx):
        pre = "" if s.action == "present" else "no "
        cmds = [f"{pre}ntp server {x}" for x in s.servers]
        cmd = "show running-config | include ^ntp"
        if s.action == "present":
            chk = Check(cmd, [rf"^ntp server (?:vrf \S+ )?{_re(x)}(\s|$)" for x in s.servers])
        else:
            chk = Check(cmd, [], [rf"^ntp server (?:vrf \S+ )?{_re(x)}(\s|$)" for x in s.servers])
        return Plan(cmds, [f"NTP servers {s.action}: {', '.join(s.servers)}"], [chk], state_command=cmd)

    def _t_dns(self, s, ctx):
        pre = "" if s.action == "present" else "no "
        cmd = "show running-config | include ^ip name-server"
        cmds = [f"{pre}ip name-server {' '.join(s.servers)}"]
        pats = [rf"^ip name-server .*{_re(x)}" for x in s.servers]
        chk = Check(cmd, pats) if s.action == "present" else Check(cmd, [], pats)
        return Plan(cmds, [f"DNS servers {s.action}: {', '.join(s.servers)}"], [chk], state_command=cmd)

    def _t_syslog(self, s, ctx):
        word = "logging server" if self._nx(ctx) else "logging host"
        pre = "" if s.action == "present" else "no "
        cmd = "show running-config | include ^logging"
        pats = [rf"^logging (?:host|server) {_re(x)}(\s|$)" for x in s.servers]
        chk = Check(cmd, pats) if s.action == "present" else Check(cmd, [], pats)
        return Plan([f"{pre}{word} {x}" for x in s.servers], [f"Syslog servers {s.action}: {', '.join(s.servers)}"],
                    [chk], state_command=cmd)

    def _t_snmp(self, s, ctx):
        cmds, summ, present, warn = [], [], [], []
        if s.community:
            cmds.append(f"snmp-server community {s.community} RO")
            summ.append("read-only community '***'")
            present.append(rf"^snmp-server community {_re(s.community)} RO")
            if s.allowed_hosts:
                warn.append("allowed_hosts is not applied on Cisco in this version (it needs an ACL); the community is open to any source.")
        if s.location:
            cmds.append(f"snmp-server location {s.location}")
            summ.append(f"location '{s.location}'")
            present.append(rf"^snmp-server location {_re(s.location)}\s*$")
        if s.contact:
            cmds.append(f"snmp-server contact {s.contact}")
            summ.append(f"contact '{s.contact}'")
            present.append(rf"^snmp-server contact {_re(s.contact)}\s*$")
        cmd = "show running-config | include ^snmp-server"
        return Plan(cmds, ["SNMP v2c: " + ", ".join(summ)], [Check(cmd, present)], state_command=cmd, warnings=warn)

    def _t_system(self, s, ctx):
        if s.action == "save":
            cmds = self._save_cmds(ctx)
            return Plan(cmds, ["Save running configuration to startup configuration"], [], state_command=None)
        if s.confirm_name != ctx.name:
            raise NCError("confirmation_required", "Type the exact device name to confirm a reboot.", 422)
        return Plan([], ["REBOOT the device (all traffic through it stops)"], [], interactive=[("reload", "")],
                    expects_disconnect=True,
                    warnings=["Unsaved configuration is lost. Save first if you want to keep it."])

    # ---------------- metrics / neighbors / diagnostics ----------------
    def parse_metrics(self, outputs):
        res = {}
        if m := re.search(r"five minutes:\s*(\d+)%", outputs.get(self.metrics_commands[0], "")):
            res["cpu"] = float(m[1])
        if m := re.search(r"Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)", outputs.get(self.metrics_commands[1], "")):
            tot, used = int(m[1]), int(m[2])
            if tot:
                res["mem"] = round(used * 100 / tot, 1)
        return res

    def parse_neighbors(self, output):
        res = []
        for blk in re.split(r"^-{10,}\s*$", output, flags=re.M):
            if m := re.search(r"Local Intf:\s*(\S+)", blk):  # LLDP
                rem = re.search(r"System Name:\s*(\S+)", blk) or re.search(r"Chassis id:\s*(\S+)", blk)
                port = re.search(r"Port id:\s*(\S+)", blk)
                if rem:
                    res.append({"local_if": m[1], "remote_name": rem[1].split(".")[0], "remote_if": port[1] if port else None, "source": "lldp"})
            elif (d := re.search(r"Device ID:\s*(\S+)", blk)) and (i := re.search(r"Interface:\s*([^,\s]+)", blk)):  # CDP
                p = re.search(r"Port ID \(outgoing port\):\s*(\S+)", blk)
                res.append({"local_if": i[1], "remote_name": d[1].split(".")[0], "remote_if": p[1] if p else None, "source": "cdp"})
        return res

    def diag_command(self, kind, arg):
        nx = False
        return {
            "ping": f"ping {arg} repeat 4", "traceroute": f"traceroute {arg}", "arp": "show ip arp",
            "mac": "show mac address-table", "routes": "show ip route", "interfaces": "show ip interface brief",
            "neighbors": "show lldp neighbors" if not nx else "show lldp neighbors",
        }[kind]
