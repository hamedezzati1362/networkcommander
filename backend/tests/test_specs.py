import pytest

from nc.engine.specs import parse_spec


def bad(d):
    with pytest.raises(Exception):
        parse_spec(d)


def test_vlan_ok_and_ranges():
    assert parse_spec({"module": "vlan", "vlan_id": 20, "name": "Users"}).vlan_id == 20
    bad({"module": "vlan", "vlan_id": 0})
    bad({"module": "vlan", "vlan_id": 4095})
    bad({"module": "vlan", "vlan_id": 5, "name": "bad name\nexit"})


def test_vlan_interface_needs_parent():
    bad({"module": "vlan", "vlan_id": 20, "kind": "vlan_interface"})
    parse_spec({"module": "vlan", "vlan_id": 20, "kind": "vlan_interface", "parent_interface": "ether2"})


def test_no_cli_injection_in_text_fields():
    bad({"module": "interface", "name": "Gi1/0/1\nreload", "description": "x"})
    bad({"module": "interface", "name": "Gi1/0/1", "description": "x\nreload"})
    bad({"module": "interface", "name": "Gi1/0/1", "description": 'x"; /system reset-configuration'})
    bad({"module": "hostname", "hostname": "a;b"})


def test_interface_dependencies():
    bad({"module": "interface", "name": "Gi1/0/1", "mode": "access"})            # needs access_vlan
    bad({"module": "interface", "name": "Gi1/0/1", "access_vlan": 10})           # vlan without mode
    bad({"module": "interface", "name": "Gi1/0/1", "mode": "trunk", "ipv4": "10.0.0.1/24"})
    bad({"module": "interface", "name": "Gi1/0/1"})                              # nothing to change
    bad({"module": "interface", "name": "Gi1/0/1", "mode": "trunk", "allowed_vlans": "10,abc"})
    parse_spec({"module": "interface", "name": "Gi1/0/1", "mode": "trunk", "allowed_vlans": "10,20,30-40"})


def test_route_and_ip_validation():
    bad({"module": "static_route", "destination": "10.0.0.1/24", "next_hop": "1.1.1.1"})   # host bits
    bad({"module": "static_route", "destination": "10.0.0.0/24", "next_hop": "999.1.1.1"})
    parse_spec({"module": "static_route", "destination": "0.0.0.0/0", "next_hop": "192.168.1.1"})


def test_snmp_rules():
    bad({"module": "snmp", "community": "public"})
    bad({"module": "snmp", "community": "bad community"})
    bad({"module": "snmp", "allowed_hosts": ["10.0.0.0/24"], "location": "x"})
    parse_spec({"module": "snmp", "community": "S3cret-ro", "allowed_hosts": ["10.0.0.0/24"]})


def test_unknown_field_and_module_rejected():
    bad({"module": "vlan", "vlan_id": 5, "command": "reload"})
    bad({"module": "nope"})
