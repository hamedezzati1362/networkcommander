import pytest
from fastapi.testclient import TestClient

from nc.config import Settings
from nc.main import create_app
from nc.simulator import SimCisco, SimFortinet, SimMikrotik


class Api:
    def __init__(self, app):
        self.app = app
        self.c = TestClient(app)
        r = self.c.get(f"/auth/bootstrap?token={app.state.token}", follow_redirects=False)
        assert r.status_code == 303
        self.c.headers["X-NC-CSRF"] = self.c.cookies.get("nc_csrf")

    def get(self, url, **kw):
        return self.c.get("/api/v1" + url, **kw)

    def post(self, url, json=None, **kw):
        return self.c.post("/api/v1" + url, json=json or {}, **kw)

    def put(self, url, json=None):
        return self.c.put("/api/v1" + url, json=json or {})

    def delete(self, url):
        return self.c.delete("/api/v1" + url)

    def add_sim(self, vendor, name, host, sim=None, tags=None, identify=True):
        if sim is not None:
            self.app.state.sims[host] = sim
        r = self.post("/devices", {"name": name, "host": host, "vendor": vendor, "transport": "simulator", "tags": tags or []})
        assert r.status_code == 201, r.text
        did = r.json()["id"]
        if identify:
            ir = self.post(f"/devices/{did}/identify")
            assert ir.status_code == 200, ir.text
        return did

    def change(self, did, spec, **apply_kw):
        p = self.post(f"/devices/{did}/changes/plan", {"spec": spec})
        assert p.status_code == 200, p.text
        cid = p.json()["id"]
        a = self.post(f"/changes/{cid}/apply", apply_kw)
        return p.json(), a


@pytest.fixture
def app(tmp_path, monkeypatch):
    s = Settings(data_dir=tmp_path, enable_simulator=True, start_scheduler=False)
    return create_app(s)


@pytest.fixture
def api(app):
    return Api(app)


@pytest.fixture
def cisco_sw():
    return SimCisco(kind="switch", name="sw1", host="10.0.0.2")
