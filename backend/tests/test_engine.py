"""End-to-end through the REST API against SIMULATED devices (proves engine logic, not real firmware)."""
import pytest

from nc.simulator import SimCisco, SimFortinet, SimMikrotik

VLAN = {"module": "vlan", "vlan_id": 20, "name": "Users"}


def test_identify_and_capabilities_cisco_switch(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    d = api.get(f"/devices/{did}").json()
    assert d["os_family"] == "iosxe" and d["role"] == "switch" and d["simulated"]
    assert d["capabilities"]["vlan_database"]["status"] == "supported"
    assert d["capabilities"]["vlan_interface"]["status"] == "unsupported"


def test_router_is_not_offered_vlan_database(api):
    did = api.add_sim("cisco", "r1", "10.0.0.3", SimCisco(kind="router", name="r1", host="10.0.0.3"))
    assert api.get(f"/devices/{did}").json()["capabilities"]["vlan_database"]["status"] == "unsupported"
    r = api.post(f"/devices/{did}/changes/plan", {"spec": VLAN})
    assert r.status_code == 422 and r.json()["code"] == "unsupported"
    mods = {m["module"]: m for m in api.get(f"/devices/{did}/modules").json()}
    assert mods["vlan"]["status"] == "unsupported" and mods["ntp"]["status"] == "supported"


def test_unidentified_device_cannot_be_configured(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw, identify=False)
    r = api.post(f"/devices/{did}/changes/plan", {"spec": VLAN})
    assert r.status_code == 422 and "Identify" in r.json()["message"]


def test_plan_apply_verify_backup_and_idempotence(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    plan, a = api.change(did, VLAN)
    assert plan["plan"]["commands"] == ["vlan 20", "name Users"] and plan["risk"] == "low" and not plan["already_compliant"]
    res = a.json()
    assert res["status"] == "applied"
    assert [r["step"] for r in res["report"]][:4] == ["connect", "backup", "apply", "verify"]
    assert "vlan 20" in "\n".join(cisco_sw.sections.keys())
    assert api.get(f"/devices/{did}/backups").json()[0]["reason"] == "pre-change"
    # running again: device already compliant -> nothing is sent
    sent = len(cisco_sw.log)
    plan2, a2 = api.change(did, VLAN)
    assert plan2["already_compliant"] and a2.json()["status"] == "skipped" and len(cisco_sw.log) == sent


def test_cannot_apply_twice(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    plan, a = api.change(did, VLAN)
    assert api.post(f"/changes/{plan['id']}/apply", {}).status_code == 409


def test_device_rejects_command_and_new_object_is_rolled_back(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    cisco_sw.reject_contains = ["name Users"]  # device accepts 'vlan 20' but refuses the name line
    _, a = api.change(did, VLAN)
    res = a.json()
    assert res["status"] == "rolled_back"
    steps = {r["step"]: r["status"] for r in res["report"]}
    assert steps["apply"] == "failed" and steps["rollback"] == "ok"
    assert "vlan 20" not in cisco_sw.sections


def test_modifying_existing_object_is_not_blindly_rolled_back(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    api.change(did, VLAN)
    cisco_sw.reject_contains = ["name Staff"]
    _, a = api.change(did, {"module": "vlan", "vlan_id": 20, "name": "Staff"})
    res = a.json()
    assert res["status"] == "failed"
    rb = [r for r in res["report"] if r["step"] == "rollback"][0]
    assert rb["status"] == "skipped" and "backup" in rb["detail"]
    assert "vlan 20" in cisco_sw.sections  # the pre-existing VLAN was NOT deleted


def test_connection_loss_marks_unknown_state(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    cisco_sw.drop_on = "name Users"
    _, a = api.change(did, VLAN)
    res = a.json()
    assert res["status"] == "unknown_state"
    assert any("partially configured" in r["detail"] for r in res["report"])


def test_readonly_account_failure_is_reported(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    cisco_sw.readonly = True
    _, a = api.change(did, VLAN)
    assert a.json()["status"] in ("failed", "rolled_back")
    assert "Authorization" in str(a.json()["report"]) or "failed" in str(a.json()["report"])


def test_risk_default_route_needs_ack(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    plan, a = api.change(did, {"module": "static_route", "destination": "0.0.0.0/0", "next_hop": "10.0.0.1"})
    assert plan["risk"] == "high" and a.status_code == 409 and a.json()["code"] == "risk_ack_required"
    r = api.post(f"/changes/{plan['id']}/apply", {"ack_risk": True})
    assert r.json()["status"] == "applied"
    assert any(ln.startswith("ip route 0.0.0.0 0.0.0.0 10.0.0.1") for ln in cisco_sw.top)


def test_management_interface_change_is_critical(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    plan, a = api.change(did, {"module": "interface", "name": "GigabitEthernet0/0", "enabled": False}, ack_risk=True)
    assert plan["risk"] == "critical" and a.status_code == 409 and a.json()["code"] == "confirmation_required"
    assert not any("shutdown" in l for l in cisco_sw.sections["interface GigabitEthernet0/0"])


def test_reboot_requires_name_and_confirmation(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    r = api.post(f"/devices/{did}/changes/plan", {"spec": {"module": "system", "action": "reboot"}})
    assert r.status_code == 422
    plan, a = api.change(did, {"module": "system", "action": "reboot", "confirm_name": "sw1"})
    assert plan["risk"] == "critical" and a.status_code == 409
    r = api.post(f"/changes/{plan['id']}/apply", {"ack_risk": True, "confirm_text": "sw1"})
    assert r.json()["status"] == "applied" and cisco_sw.rebooted


def test_interface_access_port_verified(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    api.change(did, VLAN)
    _, a = api.change(did, {"module": "interface", "name": "GigabitEthernet1/0/5", "description": "PC-Alice", "mode": "access", "access_vlan": 20, "enabled": True})
    assert a.json()["status"] == "applied"
    body = cisco_sw.sections["interface GigabitEthernet1/0/5"]
    assert " switchport access vlan 20" in body and " description PC-Alice" in body


def test_hostname_dns_ntp_snmp_syslog_cisco(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    for spec in ({"module": "ntp", "servers": ["10.1.1.1", "10.1.1.2"]}, {"module": "dns", "servers": ["8.8.8.8"]},
                 {"module": "syslog", "servers": ["10.5.5.5"]}, {"module": "snmp", "community": "R0cks-ro", "location": "Rack 4"},
                 {"module": "hostname", "hostname": "core-sw1"}):
        _, a = api.change(did, spec)
        assert a.json()["status"] == "applied", (spec, a.json())
    assert "ntp server 10.1.1.2" in cisco_sw.top and "snmp-server community R0cks-ro RO" in cisco_sw.top
    # the community must never appear in the stored/returned plan
    ch = api.get("/changes").json()
    assert "R0cks-ro" not in str(ch)


def test_ntp_remove(api, cisco_sw):
    did = api.add_sim("cisco", "sw1", "10.0.0.2", cisco_sw)
    api.change(did, {"module": "ntp", "servers": ["10.1.1.1"]})
    _, a = api.change(did, {"module": "ntp", "action": "absent", "servers": ["10.1.1.1"]})
    assert a.json()["status"] == "applied" and "ntp server 10.1.1.1" not in cisco_sw.top


# ----------------------------- MikroTik ------------------------------------
@pytest.mark.parametrize("version,expect", [("7.11.2", "servers add"), ("6.49.10", "primary-ntp")])
def test_mikrotik_ntp_version_aware(api, version, expect):
    sim = SimMikrotik(version=version, name="rb1", host="10.0.1.1")
    did = api.add_sim("mikrotik", "rb1", "10.0.1.1", sim)
    plan, a = api.change(did, {"module": "ntp", "servers": ["10.1.1.1"]})
    assert expect in " ".join(plan["plan"]["commands"]) and a.json()["status"] == "applied", a.json()


def test_mikrotik_vlan_interface_and_idempotent_guard(api):
    sim = SimMikrotik(name="rb1", host="10.0.1.1")
    did = api.add_sim("mikrotik", "rb1", "10.0.1.1", sim)
    spec = {"module": "vlan", "kind": "vlan_interface", "vlan_id": 20, "parent_interface": "ether2", "ipv4": "10.20.0.1/24"}
    _, a = api.change(did, spec)
    assert a.json()["status"] == "applied", a.json()
    assert len(sim.tables["/interface vlan"]) == 1 and sim.tables["/ip address"][-1]["address"] == "10.20.0.1/24"
    _, a2 = api.change(did, spec)
    assert a2.json()["status"] == "skipped" and len(sim.tables["/interface vlan"]) == 1
    # RouterOS does not offer a VLAN database
    r = api.post(f"/devices/{did}/changes/plan", {"spec": VLAN_DB()})
    assert r.status_code == 422


def VLAN_DB():
    return {"module": "vlan", "vlan_id": 30}


def test_mikrotik_vlan_on_missing_parent_is_reported_and_rolled_back(api):
    sim = SimMikrotik(name="rb1", host="10.0.1.1")
    did = api.add_sim("mikrotik", "rb1", "10.0.1.1", sim)
    _, a = api.change(did, {"module": "vlan", "kind": "vlan_interface", "vlan_id": 20, "parent_interface": "nope9"})
    assert a.json()["status"] in ("failed", "rolled_back") and "no such item" in str(a.json()["report"])


def test_mikrotik_interface_disable_verified(api):
    sim = SimMikrotik(name="rb1", host="10.0.1.1")
    did = api.add_sim("mikrotik", "rb1", "10.0.1.1", sim)
    _, a = api.change(did, {"module": "interface", "name": "ether3", "enabled": False, "description": "spare"})
    assert a.json()["status"] == "applied", a.json()


# ----------------------------- Fortinet ------------------------------------
def test_fortinet_vlan_interface_route_ntp(api):
    sim = SimFortinet(name="fw1", host="10.0.2.1")
    did = api.add_sim("fortinet", "fw1", "10.0.2.1", sim)
    _, a = api.change(did, {"module": "vlan", "kind": "vlan_interface", "vlan_id": 30, "parent_interface": "port2", "ipv4": "10.30.0.1/24"})
    assert a.json()["status"] == "applied", a.json()
    _, a = api.change(did, {"module": "static_route", "destination": "10.50.0.0/16", "next_hop": "10.30.0.254", "device": "vlan30"})
    assert a.json()["status"] == "applied", a.json()
    _, a = api.change(did, {"module": "ntp", "servers": ["10.1.1.1"]})
    assert a.json()["status"] == "applied", a.json()
    caps = api.get("/devices/%d" % did).json()["capabilities"]
    assert caps["static_route"]["status"] == "partial" and caps["vlan_database"]["status"] == "unsupported"
    r = api.post(f"/devices/{did}/changes/plan", {"spec": {"module": "static_route", "action": "absent", "destination": "10.50.0.0/16", "next_hop": "10.30.0.254", "device": "vlan30"}})
    assert r.status_code == 422


def test_fortinet_hostname(api):
    sim = SimFortinet(name="fw1", host="10.0.2.1")
    did = api.add_sim("fortinet", "fw1", "10.0.2.1", sim)
    _, a = api.change(did, {"module": "hostname", "hostname": "fw-edge-2"})
    assert a.json()["status"] == "applied" and sim.name == "fw-edge-2"


def test_wrong_vendor_selected_fails_identification(api):
    sim = SimFortinet(name="fw1", host="10.0.2.2")
    api.app.state.sims["10.0.2.2"] = sim
    did = api.post("/devices", {"name": "x", "host": "10.0.2.2", "vendor": "cisco", "transport": "simulator"}).json()["id"]
    r = api.post(f"/devices/{did}/identify")
    assert r.status_code == 422 and r.json()["code"] == "not_cisco"
    assert api.get(f"/devices/{did}").json()["status"] == "error"
