"""SIMULATED devices (labelled SIMULATION in the UI; only active with NC_ENABLE_SIMULATOR=1 or in tests).

They model just enough CLI behaviour to exercise the configuration engine end to end.
They do NOT prove behaviour of real Cisco / MikroTik / FortiGate firmware.
"""
from __future__ import annotations

import re

from nc.core.errors import TransportError


class SimBase:
    vendor = ""

    def __init__(self, name="sim", host="10.255.0.1", **opts):
        self.name, self.host = name, host
        self.reject_contains: list[str] = opts.get("reject_contains", [])  # config lines that make the device answer with an error
        self.drop_on: str | None = opts.get("drop_on")                     # simulate a lost connection while applying this line
        self.readonly: bool = opts.get("readonly", False)
        self.log: list[str] = []          # every config line received (for assertions)
        self.connected = False
        self.rebooted = False

    # Transport API ------------------------------------------------------
    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def run(self, command: str) -> str:
        return self._show(command.strip())

    def run_config(self, commands: list[str]) -> str:
        out = []
        for c in commands:
            if self.drop_on and self.drop_on in c:
                self.drop_on = None
                raise TransportError("Connection lost while applying configuration (simulated)", "connection_lost")
            self.log.append(c)
            if self.readonly:
                out.append(self._privilege_error())
            elif any(x in c for x in self.reject_contains):
                out.append(self._reject_error(c))
            else:
                out.append(self._config(c))
        return "\n".join(x for x in out if x)

    def run_interactive(self, command: str) -> str:
        self.rebooted = True
        self.log.append(command)
        return "System is going down"


# =============================== Cisco =====================================
class SimCisco(SimBase):
    vendor = "cisco"
    PHYS = re.compile(r"^(GigabitEthernet|FastEthernet|TenGigabitEthernet|Ethernet|Gi|Fa|Te|Eth)[\d/.]+$", re.I)

    def __init__(self, kind="switch", os_family="iosxe", model=None, version="17.06.03", iface_ips=None, **kw):
        super().__init__(**kw)
        self.kind, self.os_family, self.version = kind, os_family, version
        self.model = model or ("C9300-24P" if kind == "switch" else "ISR4331/K9")
        self.top: list[str] = [f"hostname {self.name}"]
        self.sections: dict[str, list[str]] = {"vlan 1": []}
        self.startup: str = ""
        self.iface_ips = iface_ips or {"GigabitEthernet0/0": self.host}
        self.sections[f"interface GigabitEthernet0/0"] = [f" ip address {self.host} 255.255.255.0"]
        self.neighbors: list[tuple[str, str, str]] = []  # (local_if, remote, remote_if)
        self.startup = "\n".join(self._running_lines())

    def _privilege_error(self):
        return "% Authorization failed."

    def _reject_error(self, c):
        return f"% Invalid input detected at '^' marker.\n   {c}"

    def _running_lines(self):
        lines = list(self.top)
        for k, body in self.sections.items():
            lines.append(k)
            lines += body
        return lines

    def _show(self, cmd):
        if cmd == "show version":
            if self.os_family == "nxos":
                return (f"Cisco Nexus Operating System (NX-OS) Software\nNXOS: version {self.version}\n"
                        f"  cisco Nexus9000 {self.model} Chassis\n  Processor Board ID FDO22010ABC\n  Device name: {self.name}\n"
                        "Kernel uptime is 10 day(s), 2 hour(s), 3 minute(s)")
            soft = (f"Cisco IOS XE Software, Version {self.version}" if self.os_family == "iosxe"
                    else f"Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version {self.version}, RELEASE SOFTWARE")
            return (f"{soft}\ncisco {self.model} (X86) processor with 1331000K bytes of memory.\n"
                    f"Processor board ID FCW2233L0AB\n{self.name} uptime is 2 weeks, 1 day, 3 hours, 5 minutes")
        if cmd == "show vlan brief" or cmd == "show interfaces switchport":
            return "VLAN Name Status Ports" if self.kind == "switch" else "% Invalid input detected at '^' marker."
        if m := re.fullmatch(r"show vlan id (\d+)", cmd):
            n = m[1]
            if self.kind != "switch":
                return "% Invalid input detected at '^' marker."
            if f"vlan {n}" in self.sections:
                name = next((ln.split(None, 1)[1] for ln in self.sections[f"vlan {n}"] if ln.strip().startswith("name ")), f"VLAN{int(n):04d}")
                return f"VLAN Name                             Status    Ports\n---- -------------------------------- --------- ----\n{n}   {name.strip():<32} active"
            return f"VLAN id {n} not found in current VLAN database"
        if cmd == "show ip interface brief":
            rows = ["Interface              IP-Address      OK? Method Status                Protocol"]
            for k, ip in self.iface_ips.items():
                rows.append(f"{k:<22} {ip:<15} YES manual up                    up")
            return "\n".join(rows)
        if cmd in ("show running-config", "show startup-config"):
            return "\n".join(self._running_lines()) if cmd == "show running-config" else self.startup
        if m := re.fullmatch(r"show running-config interface (.+)", cmd):
            key = re.sub(r"(?i)^vlan\s*(\d+)$", r"Vlan\1", m[1])
            key = f"interface {key}"
            if key in self.sections:
                return "Building configuration...\n" + "\n".join([key] + self.sections[key])
            if self.PHYS.match(m[1]):
                return f"Building configuration...\n{key}\n no ip address"
            return "% Invalid input detected at '^' marker."
        if m := re.fullmatch(r"show (running|startup)-config \| include (.+)", cmd):
            if m[1] == "startup":
                lines = self.startup.splitlines()
            else:
                lines = self._running_lines()
            pat = m[2].strip().strip('"')
            return "\n".join(ln for ln in lines if re.search(pat, ln))
        if cmd.startswith("show processes cpu"):
            return "CPU utilization for five seconds: 7%/1%; one minute: 5%; five minutes: 4%"
        if cmd.startswith("show processes memory"):
            return "Processor Pool Total:  1000000 Used:  250000 Free:  750000"
        if cmd == "show lldp neighbors detail":
            blocks = [f"------------------------------------------------\nLocal Intf: {l}\nChassis id: aabb.cc00.0100\nPort id: {rif}\nSystem Name: {r}.lab.local"
                      for l, r, rif in self.neighbors]
            return "\n".join(blocks)
        if cmd == "show cdp neighbors detail":
            return ""
        if cmd.startswith("ping"):
            return "Success rate is 100 percent (4/4), round-trip min/avg/max = 1/2/4 ms"
        return "% Invalid input detected at '^' marker."

    REPL = ("description", "switchport mode", "switchport access vlan", "switchport trunk allowed vlan",
            "switchport trunk native vlan", "ip address", "mtu", "name")
    KNOWN_GLOBAL = ("hostname", "ntp server", "ip name-server", "snmp-server", "logging host", "logging server", "ip route", "write memory", "copy running-config")
    KNOWN_IFACE = ("description", "switchport", "ip address", "mtu", "shutdown", "no shutdown", "no switchport", "name")

    def _config(self, c):
        ctx = getattr(self, "_ctx", None)
        invalid = "% Invalid input detected at '^' marker."
        if m := re.fullmatch(r"(no )?interface (.+)", c):
            key = re.sub(r"(?i)^vlan\s*(\d+)$", r"Vlan\1", m[2])
            key = f"interface {key}"
            if m[1]:
                self.sections.pop(key, None)
                return ""
            self.sections.setdefault(key, [])
            self._ctx = key
            return ""
        if m := re.fullmatch(r"(no )?vlan (\d+)", c):
            if self.kind != "switch":
                return invalid
            key = f"vlan {m[2]}"
            if m[1]:
                self.sections.pop(key, None)
            else:
                self.sections.setdefault(key, [])
                self._ctx = key
            return ""
        if ctx and ctx.startswith("vlan") and c.startswith("name "):
            self._set_line(self.sections[ctx], c)
            return ""
        if ctx and ctx.startswith("interface") and any(c.startswith(k) for k in self.KNOWN_IFACE):
            if c.startswith("switchport") and self.kind != "switch":
                return invalid
            self._apply(self.sections[ctx], c)
            return ""
        if c in ("write memory", "copy running-config startup-config"):
            self.startup = "\n".join(self._running_lines())
            return "[OK]"
        if any(c.startswith(k) or c.startswith("no " + k) for k in self.KNOWN_GLOBAL):
            self._apply(self.top, c, global_=True)
            return ""
        return invalid

    @staticmethod
    def _set_line(body, line):
        key = line.split(None, 1)[0]
        body[:] = [b for b in body if b.strip().split(None, 1)[0] != key]
        body.append(" " + line)

    def _apply(self, body, c, global_=False):
        ind = "" if global_ else " "
        if c == "shutdown":
            if ind + "shutdown" not in body:
                body.append(ind + "shutdown")
            return
        if c == "no shutdown":
            body[:] = [b for b in body if b.strip() != "shutdown"]
            return
        if c == "no switchport":
            body[:] = [b for b in body if "switchport" not in b]
            body.append(ind + "no switchport")
            return
        if c.startswith("no "):
            tgt = c[3:]
            body[:] = [b for b in body if b.strip() != tgt]
            return
        if not global_:
            for p in self.REPL:
                if c.startswith(p):
                    body[:] = [b for b in body if not b.strip().startswith(p)]
                    break
        elif c.startswith(("hostname", "snmp-server location", "snmp-server contact")):
            key = " ".join(c.split()[: 1 if c.startswith("hostname") else 2])
            body[:] = [b for b in body if not b.startswith(key)]
            if c.startswith("hostname"):
                self.name = c.split()[1]
        if (ind + c) not in body:
            body.append(ind + c)


# ============================== MikroTik ===================================
_KV = re.compile(r'([\w-]+)=("[^"]*"|\S+)')


def _kvs(text: str) -> dict:
    return {k: v.strip('"') for k, v in _KV.findall(text)}


class SimMikrotik(SimBase):
    vendor = "mikrotik"
    SINGLE = {"/system identity", "/ip dns", "/system ntp client", "/snmp"}
    TABLES = {"/interface", "/interface vlan", "/ip address", "/ip route", "/system ntp client servers", "/snmp community",
              "/system logging action", "/system logging"}

    def __init__(self, version="7.11.2", board="RB5009UG+S+", ip=None, **kw):
        super().__init__(**kw)
        self.version, self.board = version, board
        self.single = {"/system identity": {"name": self.name}, "/ip dns": {"servers": ""}, "/system ntp client": {"enabled": "no"},
                       "/snmp": {"enabled": "no"}}
        self.tables = {t: [] for t in self.TABLES}
        self.tables["/interface"] = [{"name": f"ether{i}"} for i in range(1, 6)]
        self.tables["/ip address"] = [{"address": f"{ip or self.host}/24", "interface": "ether1"}]
        self.neighbors: list[tuple[str, str, str]] = []

    def _privilege_error(self):
        return "failure: not enough permissions (9)"

    def _reject_error(self, c):
        return "syntax error (line 1 column 1)"

    def _rows(self, path, crit):
        return [r for r in self.tables[path] if all(r.get(k) == v for k, v in crit.items())]

    def _show(self, cmd):
        if cmd == "/system resource print":
            major = self.version.split(".")[0]
            return (f"                   uptime: 3d4h5m\n                  version: {self.version} (stable)\n"
                    f"               free-memory: 180.2MiB\n              total-memory: 256.0MiB\n"
                    f"                 cpu-load: 6%\n         architecture-name: arm64\n                board-name: {self.board}\n"
                    f"                  platform: MikroTik\n")
        if cmd == "/system routerboard print":
            return f"routerboard: yes\n       model: {self.board}\n serial-number: HEX0A1B2C3D"
        if cmd == "/export":
            out = []
            for path, rows in self.tables.items():
                for r in rows:
                    out.append(f"{path} add " + " ".join(f"{k}={v}" for k, v in r.items()))
            for path, d in self.single.items():
                out.append(f"{path} set " + " ".join(f"{k}={v}" for k, v in d.items()))
            return "# software id = SIM\n" + "\n".join(out)
        if cmd == "/ip neighbor print detail without-paging":
            return "\n".join(f" {i} interface={l} identity=\"{r}\" interface-name={rif} platform=MikroTik" for i, (l, r, rif) in enumerate(self.neighbors))
        if cmd.startswith("/ping"):
            return "sent=4 received=4 packet-loss=0% min-rtt=1ms avg-rtt=2ms max-rtt=3ms"
        m = re.match(r"^(/[\w /-]+?) print(?: terse)?(?: where (.*))?$", cmd)
        if not m:
            return "bad command name"
        path = m[1].strip()
        if path in self.single:
            return "\n".join(f"{k:>18}: {v}" for k, v in self.single[path].items())
        if path not in self.tables:
            return "bad command name"
        crit = _kvs(m[2] or "")
        rows = self._rows(path, crit)
        out = []
        for i, r in enumerate(rows):
            flag = "X" if r.get("disabled") == "yes" else " "
            out.append(f" {i} {flag}  " + " ".join(f"{k}={v}" for k, v in r.items() if k != "disabled"))
        return "\n".join(out)

    def _config(self, c):
        if m := re.match(r"^:if \(\[:len \[(/[\w /-]+?) find ([^\]]*)\]\]=0\) do=\{(.*?)\}(?: else=\{(.*)\})?$", c):
            path, crit, do, other = m[1], _kvs(m[2]), m[3], m[4]
            if not self._rows(path, crit):
                return self._config(do)
            return self._config(other) if other else ""
        m = re.match(r"^(/[\w /-]+?) (add|set|remove)(?:\s+(.*))?$", c)
        if not m:
            if c == "/system reboot":
                self.rebooted = True
                return ""
            return "bad command name"
        path, verb, rest = m[1].strip(), m[2], m[3] or ""
        if path in self.single:
            if verb != "set":
                return "bad command name"
            self.single[path].update(_kvs(rest))
            if path == "/system identity":
                self.name = self.single[path]["name"]
            return ""
        if path not in self.tables:
            return "bad command name"
        fm = re.match(r"\[find ([^\]]*)\]\s*(.*)$", rest)
        if verb == "add":
            row = _kvs(rest)
            if path == "/interface vlan" and not {"name", "vlan-id", "interface"} <= row.keys():
                return "failure: missing required parameter"
            if path == "/interface vlan" and not any(r["name"] == row["interface"] for r in self.tables["/interface"]):
                return f"failure: no such item (interface={row['interface']})"
            if path == "/interface vlan" and self._rows(path, {"name": row["name"]}):
                return "failure: already have interface with such name"
            self.tables[path].append(row)
            if path == "/interface vlan":
                self.tables["/interface"].append({"name": row["name"]})
            return ""
        if not fm:
            return "expected end of command (line 1 column 12)"
        rows = self._rows(path, _kvs(fm[1]))
        if verb == "remove":
            for r in rows:
                self.tables[path].remove(r)
                if path == "/interface vlan":
                    self.tables["/interface"] = [x for x in self.tables["/interface"] if x["name"] != r["name"]]
            return ""
        if not rows:
            return "no such item"
        for r in rows:
            r.update(_kvs(fm[2]))
        return ""


# ============================== Fortinet ===================================
class _Node(dict):
    def __init__(self):
        super().__init__(sets={}, tables={})


class SimFortinet(SimBase):
    vendor = "fortinet"

    def __init__(self, version="7.2.5", model="FortiGate-60F", **kw):
        super().__init__(**kw)
        self.version, self.model = version, model
        self.root: dict[str, dict[str, _Node]] = {}
        self.frames: list[dict] = []
        n = _Node()
        n["sets"].update(hostname=f'"{self.name}"')
        self.root["system global"] = {"": n}
        for p in ("port1", "port2", "port3"):
            e = _Node()
            e["sets"].update(vdom='"root"', ip="192.168.1.99 255.255.255.0" if p == "port1" else "0.0.0.0 0.0.0.0")
            self.root.setdefault("system interface", {})[p] = e
        self.root["system interface"]["port1"]["sets"]["ip"] = f"{self.host} 255.255.255.0"

    def _privilege_error(self):
        return "Command fail. Return code -37"

    def _reject_error(self, c):
        return "command parse error before '" + c.split()[-1] + "'\nCommand fail. Return code -651"

    def _fmt(self, name, table, pick=None, indent=0):
        pad = "    " * indent
        lines = [f"{pad}config {name}"]
        for eid, node in table.items():
            if pick is not None and eid != pick:
                continue
            ind = indent
            if eid != "":
                lines.append(f'{pad}    edit {eid if eid.isdigit() else chr(34) + eid + chr(34)}')
                ind += 1
            for k, v in node["sets"].items():
                lines.append(f"{'    ' * (ind + 1)}set {k} {v}")
            for tn, tt in node["tables"].items():
                lines += self._fmt(tn, tt, indent=ind + 1)
            if eid != "":
                lines.append(f"{pad}    next")
        lines.append(f"{pad}end")
        return lines

    def _show(self, cmd):
        if cmd == "get system status":
            host = self.root["system global"][""]["sets"]["hostname"].strip('"')
            return (f"Version: {self.model} v{self.version},build1517,230606 (GA.M)\nSerial-Number: FGT60FTK20000000\n"
                    f"Hostname: {host}\nOperation Mode: NAT\nSystem time: Mon Nov 20 10:00:00 2026\nUptime: 12 days, 3 hours, 4 minutes")
        if cmd == "get system performance status":
            return "CPU states: 3% user 2% system 0% nice 95% idle 0% iowait\nMemory: 2043788k total, 1034112k used (50.6%), 800000k free"
        if cmd == "get system interface physical":
            out = []
            for n, e in self.root["system interface"].items():
                out += [f"== [ {n} ]", f"        ip: {e['sets'].get('ip', '0.0.0.0 0.0.0.0')} mode: static"]
            return "\n".join(out)
        if cmd.startswith("execute ping"):
            return "5 packets transmitted, 5 packets received, 0% packet loss"
        m = re.match(r"^(?:show full-configuration|show)(?: (.+))?$", cmd)
        if not m:
            return "Unknown action 0"
        words = (m[1] or "").split()
        if not words:
            return "\n".join(l for k, t in self.root.items() for l in self._fmt(k, t))
        for take in (3, 2):
            key = " ".join(words[:take])
            if key in self.root:
                name = words[take] if len(words) > take else None
                tbl = self.root[key]
                if name is not None and name not in tbl:
                    return "entry not found in datasource\n\nCommand fail. Return code -3"
                return "\n".join(self._fmt(key, tbl, pick=name))
        return "config " + m[1] + "\nend" if len(words) >= 2 else "Unknown action 0"

    def _config(self, c):
        c = c.strip()
        if m := re.fullmatch(r"config (.+)", c):
            if not self.frames:
                key = m[1]
                tbl = self.root.setdefault(key, {})
            else:
                fr = self.frames[-1]
                if fr["entry"] is None:
                    fr["entry"] = fr["tbl"].setdefault("", _Node())
                tbl = fr["entry"]["tables"].setdefault(m[1], {})
            self.frames.append({"tbl": tbl, "entry": None})
            return ""
        if not self.frames:
            return "Unknown action 0"
        fr = self.frames[-1]
        if m := re.fullmatch(r"edit (.+)", c):
            eid = m[1].strip('"')
            if eid == "0":
                eid = str(max([int(k) for k in fr["tbl"] if k.isdigit()] or [0]) + 1)
            fr["entry"] = fr["tbl"].setdefault(eid, _Node())
            return ""
        if c == "next":
            fr["entry"] = None
            return ""
        if c == "end":
            self.frames.pop()
            return ""
        if m := re.fullmatch(r"delete (.+)", c):
            if m[1].strip('"') not in fr["tbl"]:
                return "entry not found in datasource\nCommand fail. Return code -3"
            fr["tbl"].pop(m[1].strip('"'))
            return ""
        if m := re.fullmatch(r"set (\S+) (.*)", c):
            if fr["entry"] is None:
                fr["entry"] = fr["tbl"].setdefault("", _Node())
            fr["entry"]["sets"][m[1]] = m[2]
            if m[1] == "hostname":
                self.name = m[2].strip('"')
            return ""
        return "Unknown action 0"
