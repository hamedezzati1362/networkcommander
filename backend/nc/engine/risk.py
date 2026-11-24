"""Risk classification: low | medium | high | critical, with reasons."""
from __future__ import annotations

import ipaddress

ORDER = ["low", "medium", "high", "critical"]


def classify(spec, ctx) -> tuple[str, list[str]]:
    level, why = "low", []

    def bump(l, reason):
        nonlocal level
        why.append(reason)
        if ORDER.index(l) > ORDER.index(level):
            level = l

    m = spec.module
    mgmt_ifaces = {n for n, ip in ctx.iface_ips.items() if ip == ctx.host}
    if m == "system" and spec.action == "reboot":
        bump("critical", "Reboot interrupts all traffic and the management session.")
    elif m == "interface":
        mgmt = spec.name in mgmt_ifaces
        if mgmt and (spec.enabled is False or spec.ipv4 or spec.mode):
            bump("critical", f"{spec.name} carries the management IP {ctx.host}; this change can lock you out.")
        elif not ctx.iface_ips:
            if spec.enabled is False or spec.ipv4:
                bump("high", "The management interface is unknown (device not identified); the change could cut the management path.")
        elif spec.enabled is False:
            bump("medium", "Shutting down an interface drops its traffic.")
        if spec.mode in ("trunk", "access") or spec.ipv4:
            bump("medium", "Changing switchport mode / addressing can interrupt connected hosts.")
    elif m == "static_route":
        if spec.destination == "0.0.0.0/0":
            bump("high", "Default route change can break all off-subnet connectivity, including management.")
        elif spec.action == "absent":
            bump("medium", "Removing a route can break reachability.")
        try:
            if ipaddress.ip_address(ctx.host) in ipaddress.ip_network(spec.destination):
                bump("high", "The destination covers the management address of this device.")
        except ValueError:
            pass
    elif m == "vlan":
        if spec.action == "absent":
            bump("high", "Removing a VLAN disconnects every port/interface in it.")
        elif spec.kind != "vlan_database":
            bump("medium", "Creates a Layer 3 interface.")
    elif m == "snmp" and spec.community:
        bump("medium", "Creates an SNMP community (information exposure if allowed hosts are broad).")
    elif m in ("ntp", "dns", "syslog") and spec.action == "absent":
        bump("medium", f"Removing {m} servers changes device behaviour.")
    elif m == "hostname":
        bump("low", "Changes the device identity string.")
    return level, why
