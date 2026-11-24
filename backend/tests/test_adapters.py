import pytest

from nc.core.errors import NCError, Unsupported
from nc.engine.specs import parse_spec
from nc.vendors.base import DeviceContext
from nc.vendors.registry import get_adapter

IOSXE = """Cisco IOS XE Software, Version 17.06.03
Cisco IOS Software [Bengaluru], Catalyst L3 Switch Software (CAT9K_LITE_IOSXE), Version 17.6.3, RELEASE SOFTWARE (fc4)
sw-core uptime is 2 weeks, 1 day, 3 hours
cisco C9300-24P (X86) processor with 1331000K/6147K bytes of memory.
Processor board ID FCW2233L0AB
"""
IOS_OLD = """Cisco IOS Software, C2960 Software (C2960-LANBASEK9-M), Version 15.0(2)SE11, RELEASE SOFTWARE (fc3)
sw-old uptime is 3 years, 1 week
cisco WS-C2960-24TT-L (PowerPC405) processor (revision N0) with 65536K bytes of memory.
Processor board ID FOC1234X0YZ
"""
NXOS = """Cisco Nexus Operating System (NX-OS) Software
  NXOS: version 9.3(8)
  cisco Nexus9000 C9336C-FX2 Chassis
  Processor Board ID FDO22010ABC
  Device name: n9k-1
Kernel uptime is 10 day(s), 2 hour(s)
"""
ROS = """                   uptime: 3d4h5m
                  version: 7.11.2 (stable)
              board-name: CRS326-24G-2S+
                 platform: MikroTik
"""
FGT = """Version: FortiGate-60F v7.2.5,build1517,230606 (GA.M)
Serial-Number: FGT60FTK20000000
Hostname: fw-edge
Uptime: 12 days, 3 hours, 4 minutes
"""


def ctx(vendor, os_family=None, ver=None, caps=None):
    c = {k: {"status": "supported"} for k in (caps or [])}
    return DeviceContext("10.0.0.2", vendor, os_family, ver, None, "dev1", {}, c)


def test_cisco_identity_variants():
    a = get_adapter("cisco")
    i = a.parse_identity({"show version": IOSXE})
    assert (i.os_family, i.os_version, i.model, i.serial, i.hostname, i.role) == ("iosxe", "17.06.03", "C9300-24P", "FCW2233L0AB", "sw-core", "switch")
    i = a.parse_identity({"show version": IOS_OLD})
    assert (i.os_family, i.os_version, i.model, i.role) == ("ios", "15.0(2)SE11", "WS-C2960-24TT-L", "switch")
    i = a.parse_identity({"show version": NXOS})
    assert (i.os_family, i.os_version, i.hostname) == ("nxos", "9.3(8)", "n9k-1")


def test_wrong_vendor_is_not_accepted():
    with pytest.raises(NCError) as e:
        get_adapter("cisco").parse_identity({"show version": FGT})
    assert e.value.code == "not_cisco"
    with pytest.raises(NCError):
        get_adapter("mikrotik").parse_identity({"/system resource print": IOSXE})
    with pytest.raises(NCError):
        get_adapter("fortinet").parse_identity({"get system status": ROS})


def test_mikrotik_and_forti_identity():
    i = get_adapter("mikrotik").parse_identity({"/system resource print": ROS, "/system routerboard print": "serial-number: ABC", "/system identity print": "name: rb"})
    assert (i.os_family, i.os_version, i.model, i.role, i.hostname) == ("routeros7", "7.11.2", "CRS326-24G-2S+", "switch", "rb")
    i = get_adapter("fortinet").parse_identity({"get system status": FGT})
    assert (i.model, i.os_version, i.serial, i.hostname, i.role) == ("FortiGate-60F", "7.2.5", "FGT60FTK20000000", "fw-edge", "firewall")


def test_probe_classification():
    a = get_adapter("cisco")
    assert a.classify_probe("% Invalid input detected at '^' marker.")[0] == "unsupported"
    assert a.classify_probe("% Authorization failed.")[0] == "requires_privilege"
    assert a.classify_probe("")[0] == "supported"
    assert a.classify_probe(None)[0] == "not_detected"


def test_cisco_vlan_translation():
    a = get_adapter("cisco")
    p = a.translate(parse_spec({"module": "vlan", "vlan_id": 20, "name": "Users"}), ctx("cisco", "iosxe", "17", ["vlan_database"]))
    assert p.commands == ["vlan 20", "name Users"] and p.rollback == ["no vlan 20"]
    with pytest.raises(Unsupported):
        a.translate(parse_spec({"module": "vlan", "vlan_id": 1}), ctx("cisco", "iosxe", "17", ["vlan_database"]))
    with pytest.raises(Unsupported):
        a.translate(parse_spec({"module": "vlan", "vlan_id": 20, "kind": "vlan_interface", "parent_interface": "Gi0"}), ctx("cisco"))


def test_cisco_ios_vs_nxos_syntax():
    s = parse_spec({"module": "static_route", "destination": "10.1.0.0/16", "next_hop": "192.168.1.1"})
    assert get_adapter("cisco").translate(s, ctx("cisco", "iosxe")).commands == ["ip route 10.1.0.0 255.255.0.0 192.168.1.1"]
    assert get_adapter("cisco").translate(s, ctx("cisco", "nxos")).commands == ["ip route 10.1.0.0/16 192.168.1.1"]
    sl = parse_spec({"module": "syslog", "servers": ["10.9.9.9"]})
    assert get_adapter("cisco").translate(sl, ctx("cisco", "nxos")).commands == ["logging server 10.9.9.9"]
    assert get_adapter("cisco").translate(sl, ctx("cisco", "ios")).commands == ["logging host 10.9.9.9"]


def test_mikrotik_version_aware_ntp():
    s = parse_spec({"module": "ntp", "servers": ["10.0.0.1", "10.0.0.2"]})
    a = get_adapter("mikrotik")
    assert any("ntp client servers add" in c for c in a.translate(s, ctx("mikrotik", "routeros7", "7.11.2")).commands)
    assert a.translate(s, ctx("mikrotik", "routeros6", "6.49.10")).commands == ["/system ntp client set enabled=yes primary-ntp=10.0.0.1 secondary-ntp=10.0.0.2"]
    three = parse_spec({"module": "ntp", "servers": ["10.0.0.1", "10.0.0.2", "10.0.0.3"]})
    with pytest.raises(Unsupported):
        a.translate(three, ctx("mikrotik", "routeros6", "6.49.10"))


def test_fortinet_native_concepts():
    a = get_adapter("fortinet")
    p = a.translate(parse_spec({"module": "vlan", "kind": "vlan_interface", "vlan_id": 30, "parent_interface": "port2", "ipv4": "10.30.0.1/24"}), ctx("fortinet", "fortios", "7.2.5"))
    assert 'edit "vlan30"' in p.commands and "set vlanid 30" in p.commands and "set ip 10.30.0.1 255.255.255.0" in p.commands
    with pytest.raises(Unsupported):
        a.translate(parse_spec({"module": "vlan", "vlan_id": 30}), ctx("fortinet", "fortios", "7.2.5"))
    with pytest.raises(NCError):  # FortiGate routes need an egress device
        a.translate(parse_spec({"module": "static_route", "destination": "10.0.0.0/8", "next_hop": "1.1.1.1"}), ctx("fortinet", "fortios", "7.2.5"))
    with pytest.raises(NCError):  # SNMP community needs hosts on FortiGate
        a.translate(parse_spec({"module": "snmp", "community": "Abc123x"}), ctx("fortinet", "fortios", "7.2.5"))


def test_secret_masked_in_summary():
    p = get_adapter("cisco").translate(parse_spec({"module": "snmp", "community": "Sup3rSecret"}), ctx("cisco", "iosxe"))
    assert "Sup3rSecret" not in " ".join(p.summary)


def test_reboot_needs_name():
    with pytest.raises(NCError):
        get_adapter("cisco").translate(parse_spec({"module": "system", "action": "reboot"}), ctx("cisco"))


def test_metric_parsers():
    assert get_adapter("cisco").parse_metrics({"show processes cpu | include CPU utilization": "CPU utilization for five seconds: 7%/1%; one minute: 5%; five minutes: 4%",
                                                "show processes memory | include Processor Pool": "Processor Pool Total: 1000 Used: 250 Free: 750"}) == {"cpu": 4.0, "mem": 25.0}
    r = get_adapter("mikrotik").parse_metrics({"/system resource print": "cpu-load: 6%\nfree-memory: 180.2MiB\ntotal-memory: 256.0MiB"})
    assert r["cpu"] == 6.0 and 29 < r["mem"] < 30
    assert get_adapter("fortinet").parse_metrics({"get system performance status": "CPU states: 3% user 2% system 0% nice 95% idle\nMemory: 100k total, 50k used (50.6%)"}) == {"cpu": 5.0, "mem": 50.6}


def test_neighbor_parsers():
    lldp = "------\nLocal Intf: Gi1/0/1\nChassis id: aa\nPort id: Gi0/2\nSystem Name: core.lab.local\n"
    n = get_adapter("cisco").parse_neighbors(lldp)
    assert n == [{"local_if": "Gi1/0/1", "remote_name": "core", "remote_if": "Gi0/2", "source": "lldp"}]
    n = get_adapter("mikrotik").parse_neighbors(' 0 interface=ether1 identity="sw2" interface-name=ether5 platform=MikroTik')
    assert n[0]["remote_name"] == "sw2" and n[0]["local_if"] == "ether1"
