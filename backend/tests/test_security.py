import json
import os
import sqlite3

import pytest
from fastapi.testclient import TestClient

from nc.config import Settings
from nc.core.audit import scrub
from nc.main import create_app
from nc.security.secretbox import FernetBox
from nc.services.backups import mask_secrets


def raw_client(app):
    return TestClient(app)


def test_api_requires_session(app):
    c = raw_client(app)
    assert c.get("/api/v1/devices").status_code == 401
    assert c.get("/api/v1/health").status_code == 200  # only health is open


def test_wrong_bootstrap_token_rejected(app):
    assert raw_client(app).get("/auth/bootstrap?token=nope", follow_redirects=False).status_code == 403


def test_csrf_required_for_mutations(api):
    c = api.c
    hdr = c.headers.pop("X-NC-CSRF")
    r = c.post("/api/v1/credentials", json={"name": "a", "username": "u", "password": "p"})
    assert r.status_code == 403 and r.json()["code"] == "csrf"
    c.headers["X-NC-CSRF"] = "wrong"
    assert c.post("/api/v1/credentials", json={"name": "a", "username": "u", "password": "p"}).status_code == 403
    c.headers["X-NC-CSRF"] = hdr
    assert c.post("/api/v1/credentials", json={"name": "a", "username": "u", "password": "p"}).status_code == 201


def test_foreign_origin_and_host_rejected(api):
    r = api.c.post("/api/v1/credentials", json={"name": "b", "username": "u", "password": "p"}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    r = api.c.get("/api/v1/devices", headers={"Host": "evil.example"})
    assert r.status_code == 403  # DNS rebinding defence


def test_credentials_never_returned_nor_stored_plain(api, app, tmp_path):
    r = api.post("/credentials", {"name": "lab", "username": "admin", "password": "Sup3rS3cret!", "enable": "EnableS3cret"})
    assert r.status_code == 201
    assert "Sup3rS3cret" not in r.text and "EnableS3cret" not in r.text
    assert "Sup3rS3cret" not in api.get("/credentials").text
    db_bytes = (tmp_path / "networkcommander.db").read_bytes()
    assert b"Sup3rS3cret" not in db_bytes and b"EnableS3cret" not in db_bytes


def test_secretbox_roundtrip_and_key_permissions(tmp_path):
    box = FernetBox(tmp_path / "k")
    blob = box.protect(b"hello")
    assert blob != b"hello" and box.unprotect(blob) == b"hello"
    if os.name != "nt":
        assert oct((tmp_path / "k").stat().st_mode & 0o777) == "0o600"


def test_audit_scrubs_secrets():
    d = scrub({"password": "x", "spec": {"community": "abc", "name": "n"}, "list": [{"token": "t"}]})
    assert d == {"password": "***", "spec": {"community": "***", "name": "n"}, "list": [{"token": "***"}]}


def test_backups_encrypted_at_rest_and_masked_in_api(api, app, tmp_path):
    from nc.simulator import SimCisco
    sw = SimCisco(name="sw1", host="10.0.0.2")
    sw.top.append("username admin secret 5 $1$abcd$hashhash")
    sw.top.append("snmp-server community Hidd3nCommunity RO")
    did = api.add_sim("cisco", "sw1", "10.0.0.2", sw)
    r = api.post(f"/devices/{did}/backups")
    assert r.status_code == 201
    bid = r.json()[0]["id"]
    assert b"Hidd3nCommunity" not in (tmp_path / "networkcommander.db").read_bytes()
    masked = api.get(f"/backups/{bid}").json()
    assert "Hidd3nCommunity" not in masked["content"] and masked["masked"]
    assert "Hidd3nCommunity" in api.get(f"/backups/{bid}?reveal=true").json()["content"]
    acts = [a["action"] for a in api.get("/audit").json()]
    assert "backup.reveal" in acts  # revealing secrets is audited


def test_community_not_persisted_in_clear(api, app, tmp_path):
    from nc.simulator import SimCisco
    did = api.add_sim("cisco", "sw1", "10.0.0.2", SimCisco(name="sw1", host="10.0.0.2"))
    api.change(did, {"module": "snmp", "community": "Zz9-persist-me"})
    con = sqlite3.connect(tmp_path / "networkcommander.db")
    dump = "\n".join(con.iterdump())
    assert "Zz9-persist-me" not in dump


def test_mask_secrets_helper():
    t = "username a password 7 0822455D0A16\nsnmp-server community pubX RO\nset psksecret ENC abc123\nhostname x"
    m = mask_secrets(t)
    assert "0822455D0A16" not in m and "pubX" not in m and "hostname x" in m


def test_simulator_disabled_by_default(tmp_path):
    app = create_app(Settings(data_dir=tmp_path, enable_simulator=False, start_scheduler=False))
    from conftest import Api
    api = Api(app)
    did = api.post("/devices", {"name": "s", "host": "1.2.3.4", "vendor": "cisco", "transport": "simulator"}).json()["id"]
    r = api.post(f"/devices/{did}/identify")
    assert r.status_code == 403 and r.json()["code"] == "simulator_disabled"


def test_ssh_device_requires_trusted_host_key_and_credential(api):
    did = api.post("/devices", {"name": "real", "host": "192.0.2.10", "vendor": "cisco"}).json()["id"]
    r = api.post(f"/devices/{did}/identify")
    assert r.status_code == 422 and r.json()["code"] == "no_credential"


def test_input_validation_rejects_bad_host(api):
    r = api.post("/devices", {"name": "x", "host": "bad host;rm", "vendor": "cisco"})
    assert r.status_code == 422


def test_unhandled_errors_do_not_leak_details(app):
    @app.get("/boom")
    def boom():
        raise RuntimeError("secret internals")
    c = TestClient(app, raise_server_exceptions=False)
    r = c.get("/boom")
    assert r.status_code == 500 and "secret internals" not in r.text
