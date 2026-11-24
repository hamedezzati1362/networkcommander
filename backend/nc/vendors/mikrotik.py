"""MikroTik RouterOS 6/7 adapter (SSH CLI)."""
from __future__ import annotations

import re

from nc.core.errors import NCError, Unsupported
from nc.vendors.base import Check, DeviceContext, Identity, Plan, Probe, VendorAdapter


def _re(s):
    return re.escape(s)


def _kv(text: str) -> dict[str, str]:
    d = {}
    for ln in text.splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            d[k.strip().lower()] = v.strip()
    return d


class MikrotikAdapter(VendorAdapter):
    key = "mikrotik"
    netmiko_type = "mikrotik_routeros"
    error_patterns = [r"^failure:", r"syntax error", r"expected end of command", r"no such item", r"bad command name",
                      r"invalid value", r"input does not match", r"^invalid internal item number", r"not enough permissions"]
    iface_ip_command = "/ip address print terse"
    metrics_commands = ["/system resource print"]
    neighbor_commands = ["/ip neighbor print detail without-paging"]

    def identify_commands(self):
        return ["/system resource print", "/system routerboard print", "/system identity print"]

    def parse_identity(self, outputs):
        res = _kv(outputs.get("/system resource print", ""))
        if "version" not in res or res.get("platform", "").lower() != "mikrotik":
            raise NCError("not_mikrotik", "The device output does not look like MikroTik RouterOS.", 422)
        ver = re.match(r"([\d.]+)", res["version"])
        version = ver[1] if ver else res["version"]
        board = res.get("board-name")
        rb = _kv(outputs.get("/system routerboard print", ""))
        serial = rb.get("serial-number")
        ident = _kv(outputs.get("/system identity print", "")).get("name")
        role = None
        if board:
            b = board.upper()
            role = "switch" if b.startswith("CRS") else "router"
        elif "x86" in res.get("architecture-name", "") or res.get("platform", "").lower() == "mikrotik":
            role = "router"
        major = version.split(".")[0]
        return Identity("mikrotik", f"routeros{major}", version, board or res.get("architecture-name"), serial,
                        ident, res.get("uptime"), role)

    def probes(self, identity):
        return [Probe(["interface", "vlan_interface"], "/interface print terse"),
                Probe(["static_route"], "/ip route print terse"),
                Probe(["ntp"], "/system ntp client print"),
                Probe(["dns"], "/ip dns print"),
                Probe(["snmp"], "/snmp print"),
                Probe(["syslog"], "/system logging action print terse"),
                Probe(["hostname"], "/system identity print"),
                Probe(["system"], "/system resource print")]

    def refine_capabilities(self, caps, identity):
        caps["vlan_database"] = {"status": "unsupported", "reason": "RouterOS has no VLAN database; use vlan_interface. Bridge VLAN filtering is not implemented yet."}
        caps["svi"] = {"status": "unsupported", "reason": "Use kind=vlan_interface on RouterOS"}
        caps["switchport"] = {"status": "unsupported", "reason": "Switchport modes (bridge port PVID/VLAN filtering) are not implemented yet"}
        caps["metrics"] = {"status": "supported", "reason": "/system resource"}
        if caps.get("ntp", {}).get("status") == "supported" and identity.os_family == "routeros6":
            caps["ntp"] = {"status": "partial", "reason": "RouterOS 6 supports at most two NTP servers (primary/secondary)"}
        caps["system"] = {**caps.get("system", {}), "reason": "save is automatic on RouterOS; reboot supported"}
        return caps

    def parse_interface_ips(self, outputs):
        res = {}
        for ln in outputs.get(self.iface_ip_command, "").splitlines():
            a = re.search(r"address=(\d+\.\d+\.\d+\.\d+)", ln)
            i = re.search(r"interface=(\S+)", ln)
            if a and i:
                res[i[1]] = a[1]
        return res

    def backup_commands(self):
        return {"running": "/export"}

    # ---------------- configuration ----------------
    def translate(self, spec, ctx):
        return getattr(self, f"_t_{spec.module}")(spec, ctx)

    def _t_hostname(self, s, ctx):
        return Plan([f"/system identity set name={s.hostname}"], [f"Set identity to {s.hostname}"],
                    [Check("/system identity print", [rf"name:\s*{_re(s.hostname)}\s*$"])],
                    state_command="/system identity print")

    def _t_vlan(self, s, ctx):
        if s.kind != "vlan_interface":
            raise Unsupported("RouterOS has no VLAN database or SVI.",
                              "Use kind=vlan_interface with a parent interface (bridge VLAN filtering is not implemented yet).")
        name = s.name or f"vlan{s.vlan_id}"
        find = f"[/interface vlan find name={name}]"
        if s.action == "absent":
            return Plan([f"/interface vlan remove {find}"], [f"Remove VLAN interface {name}"],
                        [Check(f"/interface vlan print terse where name={name}", [], [rf"name={_re(name)}\b"])],
                        state_command=f"/interface vlan print terse where name={name}")
        cmds = [f":if ([:len {find}]=0) do={{/interface vlan add name={name} vlan-id={s.vlan_id} interface={s.parent_interface}}}"]
        present = [rf"name={_re(name)}\b.*vlan-id={s.vlan_id}\b|vlan-id={s.vlan_id}\b.*name={_re(name)}\b"]
        checks = [Check(f"/interface vlan print terse where name={name}", present)]
        summ = f"VLAN {s.vlan_id} as {name} on {s.parent_interface}"
        if s.ipv4:
            cmds.append(f":if ([:len [/ip address find address={s.ipv4} interface={name}]]=0) do={{/ip address add address={s.ipv4} interface={name}}}")
            checks.append(Check(f"/ip address print terse where interface={name}", [rf"address={_re(s.ipv4)}\b"]))
            summ += f", address {s.ipv4}"
        return Plan(cmds, [summ], checks, state_command=f"/interface vlan print terse where name={name}",
                    existence=Check(f"/interface vlan print terse where name={name}", [rf"name={_re(name)}\b"]),
                    rollback=[f"/interface vlan remove {find}"],
                    warnings=["If the VLAN interface already exists with other settings, those are not modified."])

    def _t_interface(self, s, ctx):
        if s.mode in ("access", "trunk") or s.access_vlan or s.allowed_vlans or s.native_vlan:
            raise Unsupported("Switchport modes are not implemented for RouterOS yet (they need bridge ports / VLAN filtering).")
        if s.mtu:
            raise Unsupported("Setting MTU is not implemented for RouterOS yet (it depends on the interface type).")
        sel = f"[find name={s.name}]"
        cmds, summ, checks = [], [], []
        if s.description is not None:
            cmds.append(f'/interface set {sel} comment="{s.description}"')
            summ.append(f"comment = '{s.description}'")
            checks.append(Check(f"/interface print terse where name={s.name}", [rf'comment="?{_re(s.description)}"?(\s|$)']))
        if s.enabled is not None:
            cmds.append(f"/interface set {sel} disabled={'no' if s.enabled else 'yes'}")
            summ.append("enabled" if s.enabled else "DISABLED")
            # terse shows an X flag for disabled interfaces
            chk = Check(f"/interface print terse where name={s.name}",
                        [] if s.enabled else [r"^\s*\d+\s+[A-Z]*X[A-Z]*\s"], [r"^\s*\d+\s+[A-Z]*X[A-Z]*\s"] if s.enabled else [])
            checks.append(chk)
        if s.ipv4:
            cmds.append(f":if ([:len [/ip address find address={s.ipv4} interface={s.name}]]=0) do={{/ip address add address={s.ipv4} interface={s.name}}}")
            summ.append(f"IPv4 {s.ipv4}")
            checks.append(Check(f"/ip address print terse where interface={s.name}", [rf"address={_re(s.ipv4)}\b"]))
        return Plan(cmds, [f"{s.name}: " + "; ".join(summ)], checks, state_command=f"/interface print terse where name={s.name}")

    def _t_static_route(self, s, ctx):
        dist = f" distance={s.distance}" if s.distance else ""
        sel = f"[find dst-address={s.destination} gateway={s.next_hop}]"
        chk = Check("/ip route print terse", [rf"dst-address={_re(s.destination)}\b.*gateway={_re(s.next_hop)}\b|gateway={_re(s.next_hop)}\b.*dst-address={_re(s.destination)}\b"])
        if s.action == "present":
            add = f"/ip route add dst-address={s.destination} gateway={s.next_hop}{dist}"
            return Plan([f":if ([:len [/ip route find dst-address={s.destination} gateway={s.next_hop}]]=0) do={{{add}}}"],
                        [f"Route {s.destination} via {s.next_hop}"], [chk], state_command="/ip route print terse",
                        rollback=[f"/ip route remove {sel}"])
        return Plan([f"/ip route remove {sel}"], [f"Remove route {s.destination} via {s.next_hop}"],
                    [Check("/ip route print terse", [], chk.present)], state_command="/ip route print terse")

    def _t_ntp(self, s, ctx):
        if ctx.major >= 7:
            if s.action == "present":
                cmds = ["/system ntp client set enabled=yes"] + [
                    f":if ([:len [/system ntp client servers find address={x}]]=0) do={{/system ntp client servers add address={x}}}" for x in s.servers]
                chk = Check("/system ntp client servers print terse", [rf"address={_re(x)}\b" for x in s.servers])
            else:
                cmds = [f"/system ntp client servers remove [find address={x}]" for x in s.servers]
                chk = Check("/system ntp client servers print terse", [], [rf"address={_re(x)}\b" for x in s.servers])
            return Plan(cmds, [f"NTP servers {s.action}: {', '.join(s.servers)}"], [chk],
                        state_command="/system ntp client servers print terse")
        if len(s.servers) > 2:
            raise Unsupported("RouterOS 6 supports at most two NTP servers (primary and secondary).")
        if s.action == "absent":
            raise Unsupported("Removing individual NTP servers is not implemented for RouterOS 6.")
        cmd = "/system ntp client set enabled=yes primary-ntp=" + s.servers[0]
        pats = [rf"primary-ntp:\s*{_re(s.servers[0])}\s*$"]
        if len(s.servers) > 1:
            cmd += f" secondary-ntp={s.servers[1]}"
            pats.append(rf"secondary-ntp:\s*{_re(s.servers[1])}\s*$")
        return Plan([cmd], [f"NTP client servers: {', '.join(s.servers)}"],
                    [Check("/system ntp client print", pats)], state_command="/system ntp client print",
                    warnings=["Existing primary/secondary servers are replaced."])

    def _t_dns(self, s, ctx):
        csv = ",".join(s.servers)
        if s.action == "present":
            return Plan([f"/ip dns set servers={csv}"], [f"DNS servers = {csv} (replaces the current list)"],
                        [Check("/ip dns print", [rf"servers:\s*{_re(csv)}\s*$"])], state_command="/ip dns print",
                        warnings=["The whole server list is replaced."])
        return Plan(['/ip dns set servers=""'], ["Clear DNS server list"],
                    [Check("/ip dns print", [], [rf"servers:\s*\S"])], state_command="/ip dns print")

    def _t_syslog(self, s, ctx):
        if len(s.servers) > 1:
            raise Unsupported("This module configures one remote syslog action (nc-remote) on RouterOS; give one server.")
        x = s.servers[0]
        if s.action == "absent":
            return Plan(["/system logging action set [find name=nc-remote] remote=0.0.0.0"], ["Point nc-remote to 0.0.0.0 (disabled)"],
                        [Check("/system logging action print terse", [], [rf"remote={_re(x)}\b"])],
                        state_command="/system logging action print terse")
        cmds = [":if ([:len [/system logging action find name=nc-remote]]=0) do={/system logging action add name=nc-remote target=remote remote=%s} else={/system logging action set [find name=nc-remote] remote=%s}" % (x, x),
                ":if ([:len [/system logging find action=nc-remote]]=0) do={/system logging add action=nc-remote topics=info}"]
        return Plan(cmds, [f"Remote syslog {x} (action nc-remote, topic info)"],
                    [Check("/system logging action print terse", [rf"name=nc-remote\b.*remote={_re(x)}\b"])],
                    state_command="/system logging action print terse")

    def _t_snmp(self, s, ctx):
        cmds, summ, checks = [], [], []
        sets = ["enabled=yes"]
        if s.location:
            sets.append(f'location="{s.location}"')
            checks.append(Check("/snmp print", [rf"location:\s*{_re(s.location)}\s*$"]))
        if s.contact:
            sets.append(f'contact="{s.contact}"')
            checks.append(Check("/snmp print", [rf"contact:\s*{_re(s.contact)}\s*$"]))
        cmds.append("/snmp set " + " ".join(sets))
        summ.append("SNMP enabled" + (f", location '{s.location}'" if s.location else "") + (f", contact '{s.contact}'" if s.contact else ""))
        if s.community:
            addr = ",".join(s.allowed_hosts) or "0.0.0.0/0"
            cmds.append(f":if ([:len [/snmp community find name={s.community}]]=0) do={{/snmp community add name={s.community} addresses={addr} read-access=yes write-access=no}}")
            summ.append("read-only community '***'" + (f" from {addr}" if s.allowed_hosts else " from ANY address"))
            checks.append(Check("/snmp community print terse", [rf"name={_re(s.community)}\b"]))
        return Plan(cmds, ["; ".join(summ)], checks, state_command="/snmp print",
                    warnings=[] if s.allowed_hosts or not s.community else ["No allowed_hosts given: the community accepts any source."])

    def _t_system(self, s, ctx):
        if s.action == "save":
            return Plan([], ["Nothing to do: RouterOS persists configuration immediately"], [], no_op_reason="RouterOS saves configuration automatically.")
        if s.confirm_name != ctx.name:
            raise NCError("confirmation_required", "Type the exact device name to confirm a reboot.", 422)
        return Plan([], ["REBOOT the device"], [], interactive=[("/system reboot", "")], expects_disconnect=True)

    # ---------------- metrics / neighbors / diagnostics ----------------
    def parse_metrics(self, outputs):
        kv = _kv(outputs.get(self.metrics_commands[0], ""))
        res = {}
        if "cpu-load" in kv:
            res["cpu"] = float(kv["cpu-load"].rstrip("%"))

        def mem(v):
            m = re.match(r"([\d.]+)\s*([KMG]i?B)?", v or "", re.I)
            if not m:
                return None
            mult = {"": 1, "KIB": 1024, "MIB": 1024**2, "GIB": 1024**3, "KB": 1000, "MB": 1000**2, "GB": 1000**3}
            return float(m[1]) * mult.get((m[2] or "").upper(), 1)

        t, f = mem(kv.get("total-memory")), mem(kv.get("free-memory"))
        if t and f is not None:
            res["mem"] = round((t - f) * 100 / t, 1)
        return res

    def parse_neighbors(self, output):
        res = []
        for ln in re.split(r"\n(?=\s*\d+\s)", output):
            ident = re.search(r'identity="?([^"\s]+)"?', ln)
            ifs = re.search(r"\binterface=([^\s,]+)", ln)
            if ident and ifs:
                port = re.search(r"interface-name=\"?([^\"\s]+)", ln)
                res.append({"local_if": ifs[1], "remote_name": ident[1], "remote_if": port[1] if port else None, "source": "lldp"})
        return res

    def diag_command(self, kind, arg):
        return {"ping": f"/ping {arg} count=4", "traceroute": f"/tool traceroute {arg} count=1", "arp": "/ip arp print",
                "mac": "/interface bridge host print", "routes": "/ip route print", "interfaces": "/interface print",
                "neighbors": "/ip neighbor print"}[kind]
