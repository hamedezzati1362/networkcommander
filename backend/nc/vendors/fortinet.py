"""Fortinet FortiGate / FortiOS adapter (SSH CLI)."""
from __future__ import annotations

import re

from nc.core.errors import NCError, Unsupported
from nc.vendors.base import Check, DeviceContext, Identity, Plan, Probe, VendorAdapter, mask


def _re(s):
    return re.escape(s)


class FortinetAdapter(VendorAdapter):
    key = "fortinet"
    netmiko_type = "fortinet"
    error_patterns = [r"^Command fail", r"Unknown action", r"value parse error", r"command parse error",
                      r"^node_check_object fail", r"Return code -\d+", r"entry not found", r"object check error"]
    privilege_patterns = [r"Permission denied", r"Return code -37"]
    iface_ip_command = "get system interface physical"
    metrics_commands = ["get system performance status"]
    neighbor_commands: list[str] = []

    def identify_commands(self):
        return ["get system status"]

    def parse_identity(self, outputs):
        out = outputs.get("get system status", "")
        m = re.search(r"^Version:\s*(\S+)\s+v?([\d.]+)", out, re.M)
        if not m or not m[1].lower().startswith("forti"):
            raise NCError("not_fortinet", "The device output does not look like FortiOS.", 422)
        model = m[1]
        ver = m[2]
        serial = (re.search(r"Serial-Number:\s*(\S+)", out) or [None, None])[1]
        host = (re.search(r"^Hostname:\s*(\S+)", out, re.M) or [None, None])[1]
        up = (re.search(r"Uptime:\s*(.+)$", out, re.M) or [None, None])[1]
        role = "firewall" if re.match(r"Forti(Gate|Wifi|Firewall)", model, re.I) else "other"
        return Identity("fortinet", "fortios", ver, model, serial, host, up.strip() if up else None, role)

    def probes(self, identity):
        return [Probe(["interface", "vlan_interface"], "show system interface"),
                Probe(["static_route"], "show router static"),
                Probe(["ntp"], "show system ntp"),
                Probe(["dns"], "show system dns"),
                Probe(["snmp"], "show system snmp sysinfo"),
                Probe(["syslog"], "show log syslogd setting"),
                Probe(["hostname", "system"], "get system status")]

    def refine_capabilities(self, caps, identity):
        caps["vlan_database"] = {"status": "unsupported", "reason": "FortiGate has no switch VLAN database; use kind=vlan_interface"}
        caps["svi"] = {"status": "unsupported", "reason": "Use kind=vlan_interface on FortiGate"}
        caps["switchport"] = {"status": "unsupported", "reason": "Switchport modes are FortiSwitch/hardware-switch features, not implemented"}
        caps["metrics"] = {"status": "supported", "reason": "get system performance status"}
        if caps.get("static_route", {}).get("status") == "supported":
            caps["static_route"] = {"status": "partial", "reason": "Adding routes works; removing routes is not implemented (needs entry id)"}
        caps["firewall_policy"] = {"status": "unsupported", "reason": "Firewall policy / object workflows are not implemented in this version"}
        return caps

    def parse_interface_ips(self, outputs):
        res, cur = {}, None
        for ln in outputs.get(self.iface_ip_command, "").splitlines():
            if m := re.match(r"==\s*\[\s*(\S+)\s*\]", ln):
                cur = m[1]
            elif cur and (m := re.search(r"\bip:\s*(\d+\.\d+\.\d+\.\d+)", ln)):
                res[cur] = m[1]
        return res

    def backup_commands(self):
        return {"running": "show full-configuration"}

    # ---------------- configuration ----------------
    def translate(self, spec, ctx):
        return getattr(self, f"_t_{spec.module}")(spec, ctx)

    def _t_hostname(self, s, ctx):
        return Plan(["config system global", f'set hostname "{s.hostname}"', "end"], [f"Set hostname to {s.hostname}"],
                    [Check("get system status", [rf"^Hostname:\s*{_re(s.hostname)}\s*$"])], state_command="get system status")

    def _t_vlan(self, s, ctx):
        if s.kind != "vlan_interface":
            raise Unsupported("FortiGate has no VLAN database or SVI.", "Use kind=vlan_interface with a parent interface.")
        name = s.name or f"vlan{s.vlan_id}"
        show = f"show system interface {name}"
        if s.action == "absent":
            return Plan(["config system interface", f'delete "{name}"', "end"], [f"Delete VLAN interface {name} (fails if referenced by policies/routes)"],
                        [Check(show, [], [r"set vlanid"])], state_command=show,
                        warnings=["FortiOS refuses deletion while the interface is referenced by policies, routes or DHCP."])
        cmds = ["config system interface", f'edit "{name}"', 'set vdom "root"', f'set interface "{s.parent_interface}"', f"set vlanid {s.vlan_id}"]
        present = [rf"set vlanid {s.vlan_id}\b", rf'set interface "?{_re(s.parent_interface)}"?']
        if s.ipv4:
            ip, m = mask(s.ipv4)
            cmds += ["set mode static", f"set ip {ip} {m}"]
            present.append(rf"set ip {_re(ip)} {_re(m)}")
        cmds += ["next", "end"]
        return Plan(cmds, [f"VLAN {s.vlan_id} interface {name} on {s.parent_interface}" + (f", {s.ipv4}" if s.ipv4 else "")],
                    [Check(show, present)], state_command=show, existence=Check(show, [r"set vlanid"]),
                    rollback=["config system interface", f'delete "{name}"', "end"],
                    warnings=["The VDOM is fixed to 'root' in this version."])

    def _t_interface(self, s, ctx):
        if s.mode in ("access", "trunk") or s.access_vlan or s.allowed_vlans or s.native_vlan:
            raise Unsupported("Switchport modes are not implemented for FortiGate.")
        cmds = ["config system interface", f'edit "{s.name}"']
        present, absent, summ = [], [], []
        if s.description is not None:
            cmds.append(f'set description "{s.description}"')
            present.append(rf'set description "{_re(s.description)}"')
            summ.append(f"description = '{s.description}'")
        if s.enabled is not None:
            cmds.append(f"set status {'up' if s.enabled else 'down'}")
            (absent if s.enabled else present).append(r"set status down")
            summ.append("up" if s.enabled else "DOWN")
        if s.ipv4:
            ip, m = mask(s.ipv4)
            cmds += ["set mode static", f"set ip {ip} {m}"]
            present.append(rf"set ip {_re(ip)} {_re(m)}")
            summ.append(f"IPv4 {s.ipv4}")
        if s.mtu:
            cmds += ["set mtu-override enable", f"set mtu {s.mtu}"]
            present.append(rf"set mtu {s.mtu}\b")
            summ.append(f"MTU {s.mtu}")
        cmds += ["next", "end"]
        show = f"show full-configuration system interface {s.name}"
        return Plan(cmds, [f"{s.name}: " + "; ".join(summ)], [Check(show, present, absent)], state_command=show)

    def _t_static_route(self, s, ctx):
        if s.action == "absent":
            raise Unsupported("Removing a FortiGate static route needs its entry id; this is not implemented yet.")
        if not s.device:
            raise NCError("missing_field", "FortiGate static routes need the egress interface (device).", 422)
        import ipaddress
        n = ipaddress.ip_network(s.destination)
        dst = f"{n.network_address} {n.netmask}"
        cmds = ["config router static", "edit 0", f"set dst {dst}", f"set gateway {s.next_hop}", f'set device "{s.device}"']
        if s.distance:
            cmds.append(f"set distance {s.distance}")
        cmds += ["next", "end"]
        pat = rf"(?s)set dst {_re(str(n.network_address))} {_re(str(n.netmask))}\s*\n(?:(?!\bnext\b).)*?set gateway {_re(s.next_hop)}"
        return Plan(cmds, [f"Route {s.destination} via {s.next_hop} dev {s.device}"],
                    [Check("show router static", [pat])], state_command="show router static",
                    warnings=["A new route entry is created; removing it later must be done by entry id (not implemented)."])

    def _t_ntp(self, s, ctx):
        if s.action == "absent":
            raise Unsupported("Removing individual NTP servers is not implemented for FortiGate.")
        cmds = ["config system ntp", "set ntpsync enable", "set type custom", "config ntpserver"]
        for i, x in enumerate(s.servers, 1):
            cmds += [f"edit {i}", f'set server "{x}"', "next"]
        cmds += ["end", "end"]
        return Plan(cmds, [f"NTP servers: {', '.join(s.servers)} (entries 1..{len(s.servers)} overwritten)"],
                    [Check("show system ntp", [rf'set server "?{_re(x)}"?' for x in s.servers])], state_command="show system ntp",
                    warnings=["Existing NTP server entries with the same ids are overwritten."])

    def _t_dns(self, s, ctx):
        if s.action == "absent":
            raise Unsupported("Clearing FortiGate DNS servers is not implemented (it falls back to FortiGuard).")
        cmds = ["config system dns", f"set primary {s.servers[0]}"]
        pats = [rf"set primary {_re(s.servers[0])}\b"]
        if len(s.servers) > 1:
            cmds.append(f"set secondary {s.servers[1]}")
            pats.append(rf"set secondary {_re(s.servers[1])}\b")
        if len(s.servers) > 2:
            raise Unsupported("FortiGate system DNS has primary and secondary only.")
        cmds.append("end")
        return Plan(cmds, [f"DNS servers: {', '.join(s.servers)}"], [Check("show system dns", pats)], state_command="show system dns")

    def _t_syslog(self, s, ctx):
        if len(s.servers) > 1:
            raise Unsupported("This module configures the primary syslogd only; give one server.")
        x = s.servers[0]
        if s.action == "absent":
            return Plan(["config log syslogd setting", "set status disable", "end"], ["Disable remote syslog"],
                        [Check("show log syslogd setting", [], [r"set status enable"])], state_command="show log syslogd setting")
        return Plan(["config log syslogd setting", "set status enable", f"set server {x}", "end"], [f"Remote syslog {x}"],
                    [Check("show log syslogd setting", [rf'set server "?{_re(x)}"?', r"set status enable"])],
                    state_command="show log syslogd setting")

    def _t_snmp(self, s, ctx):
        cmds, summ, checks = [], [], []
        if s.location or s.contact:
            cmds += ["config system snmp sysinfo", "set status enable"]
            if s.location:
                cmds.append(f'set location "{s.location}"')
                checks.append(Check("show system snmp sysinfo", [rf'set location "{_re(s.location)}"']))
            if s.contact:
                cmds.append(f'set contact-info "{s.contact}"')
                checks.append(Check("show system snmp sysinfo", [rf'set contact-info "{_re(s.contact)}"']))
            cmds.append("end")
            summ.append("sysinfo enabled")
        if s.community:
            if not s.allowed_hosts:
                raise NCError("missing_field", "FortiGate SNMP communities need at least one allowed host/network.", 422)
            import ipaddress
            cmds += ["config system snmp community", "edit 0", f'set name "{s.community}"', "set status enable", "config hosts"]
            for i, h in enumerate(s.allowed_hosts, 1):
                n = ipaddress.ip_network(h)
                cmds += [f"edit {i}", f"set ip {n.network_address} {n.netmask}", "next"]
            cmds += ["end", "next", "end"]
            summ.append("community '***' for " + ", ".join(s.allowed_hosts))
            checks.append(Check("show system snmp community", [rf'set name "{_re(s.community)}"']))
        return Plan(cmds, ["SNMP: " + "; ".join(summ)], checks, state_command="show system snmp sysinfo")

    def _t_system(self, s, ctx):
        if s.action == "save":
            return Plan([], ["Nothing to do: FortiOS applies and stores configuration immediately"], [], no_op_reason="FortiOS saves configuration automatically.")
        if s.confirm_name != ctx.name:
            raise NCError("confirmation_required", "Type the exact device name to confirm a reboot.", 422)
        return Plan([], ["REBOOT the firewall"], [], interactive=[("execute reboot", "y")], expects_disconnect=True)

    # ---------------- metrics / diagnostics ----------------
    def parse_metrics(self, outputs):
        out = outputs.get(self.metrics_commands[0], "")
        res = {}
        if m := re.search(r"CPU states:.*?(\d+)% idle", out):
            res["cpu"] = float(100 - int(m[1]))
        if m := re.search(r"Memory:.*?\(([\d.]+)%\)", out):
            res["mem"] = float(m[1])
        return res

    def diag_command(self, kind, arg):
        return {"ping": f"execute ping {arg}", "traceroute": f"execute traceroute {arg}", "arp": "get system arp",
                "mac": "diagnose netlink brctl list", "routes": "get router info routing-table all",
                "interfaces": "get system interface physical", "neighbors": "get system arp"}[kind]
