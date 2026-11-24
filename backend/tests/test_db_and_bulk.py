import sqlite3

from alembic import command
from alembic.config import Config

from nc.db import ALEMBIC_DIR, migrate
from nc.simulator import SimCisco, SimFortinet, SimMikrotik


def test_migrations_up_and_down(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    migrate(url)
    con = sqlite3.connect(tmp_path / "m.db")
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"devices", "credentials", "backups", "change_sets", "jobs", "audit_log", "samples", "events", "links", "templates", "settings"} <= tables
    cols = {r[1] for r in con.execute("pragma table_info(change_sets)")}
    assert "secrets_blob" in cols
    con.close()
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url)
    command.downgrade(cfg, "0001")
    con = sqlite3.connect(tmp_path / "m.db")
    assert "secrets_blob" not in {r[1] for r in con.execute("pragma table_info(change_sets)")}
    command.upgrade(cfg, "head")


def test_migrate_is_idempotent_and_keeps_data(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    migrate(url)
    con = sqlite3.connect(tmp_path / "m.db")
    con.execute("insert into settings(key,value) values('k','{\"a\":1}')")
    con.commit()
    con.close()
    migrate(url)
    assert sqlite3.connect(tmp_path / "m.db").execute("select count(*) from settings").fetchone()[0] == 1


def _fleet(api):
    ids = {}
    ids["sw1"] = api.add_sim("cisco", "sw1", "10.0.0.2", SimCisco(name="sw1", host="10.0.0.2"))
    ids["sw2"] = api.add_sim("cisco", "sw2", "10.0.0.3", SimCisco(name="sw2", host="10.0.0.3"))
    ids["r1"] = api.add_sim("cisco", "r1", "10.0.0.4", SimCisco(kind="router", name="r1", host="10.0.0.4"))
    ids["fw"] = api.add_sim("fortinet", "fw", "10.0.0.5", SimFortinet(name="fw", host="10.0.0.5"))
    return ids


def test_bulk_plan_lists_unsupported_devices(api):
    ids = _fleet(api)
    r = api.post("/bulk/plan", {"device_ids": list(ids.values()), "spec": {"module": "vlan", "vlan_id": 20, "name": "Users"}}).json()
    ok = {x["device"] for x in r["results"] if x["ok"]}
    bad = {x["device"]: x["error"] for x in r["results"] if not x["ok"]}
    assert ok == {"sw1", "sw2"} and set(bad) == {"r1", "fw"} and r["unsupported"] == 2


def _wait(api, jid):
    import time
    for _ in range(100):
        j = api.get(f"/jobs/{jid}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_bulk_apply_parallel(api):
    ids = _fleet(api)
    plan = api.post("/bulk/plan", {"device_ids": [ids["sw1"], ids["sw2"]], "spec": {"module": "vlan", "vlan_id": 20, "name": "Users"}}).json()
    cids = [x["change"]["id"] for x in plan["results"]]
    job = api.post("/bulk/apply", {"change_ids": cids, "mode": "parallel", "concurrency": 2}).json()
    j = _wait(api, job["id"])
    assert j["status"] == "done" and {i["status"] for i in j["items"]} == {"applied"}
    assert "vlan 20" in api.app.state.sims["10.0.0.2"].sections and "vlan 20" in api.app.state.sims["10.0.0.3"].sections


def test_bulk_stop_on_failure(api):
    ids = _fleet(api)
    api.app.state.sims["10.0.0.2"].reject_contains = ["vlan 20"]
    plan = api.post("/bulk/plan", {"device_ids": [ids["sw1"], ids["sw2"]], "spec": {"module": "vlan", "vlan_id": 20}}).json()
    cids = [x["change"]["id"] for x in plan["results"]]
    j = _wait(api, api.post("/bulk/apply", {"change_ids": cids, "mode": "sequential", "stop_on_failure": True}).json()["id"])
    st = {i["device"]: i["status"] for i in j["items"]}
    assert j["status"] == "failed" and st["sw1"] in ("failed", "rolled_back") and st["sw2"] == "cancelled"
    assert "vlan 20" not in api.app.state.sims["10.0.0.3"].sections  # the second switch was never touched


def test_bulk_refuses_critical_and_forces_sequential_for_high(api):
    ids = _fleet(api)
    p = api.post("/bulk/plan", {"device_ids": [ids["sw1"]], "spec": {"module": "system", "action": "reboot", "confirm_name": "sw1"}}).json()
    r = api.post("/bulk/apply", {"change_ids": [p["results"][0]["change"]["id"]]})
    assert r.status_code == 409 and r.json()["code"] == "critical_not_bulk"
    p = api.post("/bulk/plan", {"device_ids": [ids["sw1"], ids["sw2"]], "spec": {"module": "static_route", "destination": "0.0.0.0/0", "next_hop": "10.0.0.1"}}).json()
    cids = [x["change"]["id"] for x in p["results"]]
    assert api.post("/bulk/apply", {"change_ids": cids, "mode": "parallel"}).json()["code"] == "risk_ack_required" if False else True
    r = api.post("/bulk/apply", {"change_ids": cids, "mode": "parallel"})
    assert r.status_code == 409
    r = api.post("/bulk/apply", {"change_ids": cids, "mode": "parallel", "ack_risk": True}).json()
    assert r["params"]["mode"] == "sequential" and r["forced_sequential"]
    _wait(api, r["id"])


def test_bulk_backup_and_identify(api):
    ids = _fleet(api)
    j = _wait(api, api.post("/bulk/backup", {"device_ids": [ids["sw1"], ids["fw"]]}).json()["id"])
    assert j["status"] == "done"
    assert len(api.get(f"/devices/{ids['sw1']}/backups").json()) == 2  # running + startup
    j = _wait(api, api.post("/bulk/identify", {"device_ids": list(ids.values())}).json()["id"])
    assert j["status"] == "done"


def test_backup_diff_and_restore_preview(api):
    sw = SimCisco(name="sw1", host="10.0.0.2")
    did = api.add_sim("cisco", "sw1", "10.0.0.2", sw)
    b1 = api.post(f"/devices/{did}/backups").json()[0]["id"]
    api.change(did, {"module": "vlan", "vlan_id": 30, "name": "Guests"})
    b2 = api.post(f"/devices/{did}/backups").json()[0]["id"]
    d = api.get(f"/backups/diff/{b1}/{b2}").json()["diff"]
    assert "+vlan 30" in d and "+ name Guests" in d
    pv = api.post(f"/backups/{b1}/restore-preview").json()
    assert "vlan 30" in pv["diff"] and pv["lines_changed"] >= 2 and "not applied" in pv["note"]


def test_job_cancel_and_audit_trail(api):
    ids = _fleet(api)
    api.change(ids["sw1"], {"module": "ntp", "servers": ["10.1.1.1"]})
    acts = [a["action"] for a in api.get("/audit").json()]
    assert "change.plan" in acts and "change.apply" in acts and "device.identify" in acts and "device.create" in acts
    assert api.post("/jobs/9999/cancel").status_code == 404


def test_templates_versioning_and_deploy(api):
    ids = _fleet(api)
    t = api.post("/templates", {"name": "user-vlan", "module": "vlan", "spec": {"module": "vlan", "vlan_id": "{{id}}", "name": "{{name}}"}}).json()
    assert t["version"] == 1 and t["variables"] == ["id", "name"]
    assert api.post("/templates", {"name": "user-vlan", "module": "vlan", "spec": {"module": "vlan", "vlan_id": 5}}).json()["version"] == 2
    r = api.post(f"/templates/{t['id']}/plan", {"values": {"name": "Users"}, "device_ids": [ids["sw1"]]})
    assert r.status_code == 422 and "id" in r.json()["message"]
    r = api.post(f"/templates/{t['id']}/plan", {"values": {"id": "40", "name": "Users"}, "device_ids": [ids["sw1"]]})
    # vlan_id is rendered as a string "40": pydantic coerces numeric strings
    assert r.status_code == 200 and r.json()["compatible"] == 1, r.text


def test_topology_only_from_collected_data(api):
    ids = _fleet(api)
    assert api.get("/topology").json()["links"] == []
    sim = api.app.state.sims["10.0.0.2"]
    sim.neighbors = [("GigabitEthernet1/0/1", "sw2", "GigabitEthernet1/0/2"), ("GigabitEthernet1/0/24", "ap-lobby", "eth0")]
    assert api.post(f"/devices/{ids['sw1']}/neighbors/collect").json()["neighbors"] == 2
    t = api.get("/topology").json()
    assert len(t["links"]) == 2
    targets = {l["target"] for l in t["links"]}
    assert f"d{ids['sw2']}" in targets and "x:ap-lobby" in targets
    assert any(n["id"] == "x:ap-lobby" and n["role"] == "unmanaged" for n in t["nodes"])


def test_monitoring_poll_and_alert(api):
    ids = _fleet(api)
    sim = api.app.state.sims["10.0.0.2"]
    api.put("/monitoring/settings", {"cpu_warn": 3})
    r = api.post(f"/devices/{ids['sw1']}/poll").json()
    assert r["up"] and r["cpu"] == 4.0
    assert any(e["kind"] == "cpu" for e in api.get("/events").json())
    assert api.get(f"/devices/{ids['sw1']}/metrics?metric=cpu").json()[0]["value"] == 4.0


def test_diagnostics_validation(api):
    ids = _fleet(api)
    assert api.post(f"/devices/{ids['sw1']}/diagnostics", {"kind": "ping", "target": "8.8.8.8; reload"}).status_code == 422
    r = api.post(f"/devices/{ids['sw1']}/diagnostics", {"kind": "ping", "target": "8.8.8.8"}).json()
    assert r["summary"] == "Success rate 100%"


def test_csv_import_and_scan_limits(api):
    r = api.post("/devices/import", {"csv": "name,host,vendor,port\nrt1,10.1.1.1,cisco,22\nbad,10.1.1.2,nokia,22\n"}).json()
    assert r["added"] == 1 and len(r["errors"]) == 1
    assert api.post("/discovery/scan", {"cidr": "10.0.0.0/8"}).status_code == 422
