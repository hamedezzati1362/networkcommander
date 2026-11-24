"""REST API v1. Thin layer: validation + calls into services/engine."""
from __future__ import annotations

import csv
import io
import ipaddress
import json
import re
import socket
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from nc import __version__
from nc.core.audit import audit
from nc.core.errors import NCError, TransportError
from nc.engine import executor
from nc.engine.specs import HOST_RE, MODULE_MODELS, MODULE_TITLES
from nc.models import AuditLog, Backup, ChangeSet, Credential, Device, Event, Job, Link, Sample, Template
from nc.services import backups as bk
from nc.services import inventory, jobs, monitoring
from nc.transport.factory import make_transport
from nc.transport.ssh import fetch_host_key, suggest_vendor
from nc.vendors.registry import get_adapter, vendors

router = APIRouter(prefix="/api/v1")


def db_dep(request: Request):
    with request.app.state.sm() as db:
        yield db


def tf_dep(request: Request):
    st = request.app.state
    with st.sm() as tdb:
        yield lambda device: make_transport(tdb, st.box, device, st.settings.enable_simulator)


def dev_or_404(db: Session, device_id: int) -> Device:
    d = db.get(Device, device_id)
    if not d:
        raise NCError("not_found", "Device not found", 404)
    return d


# ------------------------------------------------------------- meta
@router.get("/health")
def health(request: Request):
    return {"status": "ok", "version": __version__, "secret_store": request.app.state.box.name,
            "simulator_enabled": request.app.state.settings.enable_simulator}


@router.get("/meta")
def meta(request: Request):
    return {"vendors": vendors(), "modules": MODULE_TITLES, "simulator_enabled": request.app.state.settings.enable_simulator,
            "secret_store": request.app.state.box.name}


@router.post("/system/shutdown")
def shutdown(request: Request, db: Session = Depends(db_dep)):
    """Stop the local service (used by the 'Stop Network Commander' shortcut)."""
    import os
    import threading
    audit(db, "system.shutdown", None)
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


@router.get("/modules/schema")
def module_schema():
    return {k: {"title": MODULE_TITLES[k], "schema": m.model_json_schema()} for k, m in MODULE_MODELS.items()}


# ------------------------------------------------------------- credentials
class CredIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    username: str = Field(min_length=1, max_length=120)
    password: str | None = None
    private_key: str | None = None
    enable: str | None = None


def cred_out(c: Credential):
    return {"id": c.id, "name": c.name, "username": c.username, "has_key": c.has_key, "has_enable": c.has_enable,
            "secret": "stored (hidden)"}


@router.get("/credentials")
def list_creds(db: Session = Depends(db_dep)):
    return [cred_out(c) for c in db.scalars(select(Credential)).all()]


@router.post("/credentials", status_code=201)
def add_cred(body: CredIn, request: Request, db: Session = Depends(db_dep)):
    if not body.password and not body.private_key:
        raise NCError("validation_failed", "Provide a password or a private key.", 422)
    if db.scalars(select(Credential).where(Credential.name == body.name)).first():
        raise NCError("duplicate", "A credential profile with this name exists.", 409)
    blob = request.app.state.box.protect(json.dumps({"password": body.password, "private_key": body.private_key, "enable": body.enable}).encode())
    c = Credential(name=body.name, username=body.username, secret_blob=blob, has_key=bool(body.private_key), has_enable=bool(body.enable))
    db.add(c)
    db.commit()
    audit(db, "credential.create", c.name)
    return cred_out(c)


@router.delete("/credentials/{cid}")
def del_cred(cid: int, db: Session = Depends(db_dep)):
    c = db.get(Credential, cid)
    if not c:
        raise NCError("not_found", "Not found", 404)
    if db.scalars(select(Device).where(Device.credential_id == cid)).first():
        raise NCError("in_use", "This credential profile is used by devices.", 409)
    db.delete(c)
    db.commit()
    audit(db, "credential.delete", c.name)
    return {"ok": True}


# ------------------------------------------------------------- devices
class DeviceIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    host: str
    port: int = Field(22, ge=1, le=65535)
    vendor: str
    transport: str = "ssh"
    credential_id: int | None = None
    group: str | None = None
    location: str | None = None
    tags: list[str] = []
    notes: str | None = None


def dev_out(d: Device):
    return {k: getattr(d, k) for k in ("id", "name", "host", "port", "vendor", "transport", "credential_id", "group", "location",
                                       "tags", "notes", "os_family", "os_version", "model", "serial", "hostname", "uptime", "role",
                                       "capabilities", "status", "status_detail", "host_key_fp", "host_key_trusted")} | {
        "last_seen": d.last_seen, "last_identified": d.last_identified, "simulated": d.transport == "simulator"}


def _check_dev_in(b: DeviceIn):
    get_adapter(b.vendor)
    if not HOST_RE.match(b.host):
        raise NCError("validation_failed", "Invalid host name or IP address.", 422)
    if b.transport not in ("ssh", "simulator"):
        raise NCError("validation_failed", "transport must be ssh or simulator", 422)


@router.get("/devices")
def list_devices(q: str | None = None, vendor: str | None = None, role: str | None = None, group: str | None = None,
                 status: str | None = None, db: Session = Depends(db_dep)):
    stmt = select(Device).order_by(Device.name)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Device.name.ilike(like), Device.host.ilike(like), Device.model.ilike(like), Device.location.ilike(like)))
    for col, v in ((Device.vendor, vendor), (Device.role, role), (Device.group, group), (Device.status, status)):
        if v:
            stmt = stmt.where(col == v)
    return [dev_out(d) for d in db.scalars(stmt).all()]


@router.post("/devices", status_code=201)
def add_device(b: DeviceIn, db: Session = Depends(db_dep)):
    _check_dev_in(b)
    if db.scalars(select(Device).where(Device.name == b.name)).first():
        raise NCError("duplicate", "A device with this name exists.", 409)
    d = Device(**b.model_dump())
    if d.transport == "simulator":
        d.host_key_trusted, d.host_key_fp = True, "SIMULATED"
    db.add(d)
    db.commit()
    audit(db, "device.create", d.name, detail={"host": d.host, "vendor": d.vendor})
    return dev_out(d)


@router.get("/devices/{device_id}")
def get_device(device_id: int, db: Session = Depends(db_dep)):
    return dev_out(dev_or_404(db, device_id))


@router.put("/devices/{device_id}")
def update_device(device_id: int, b: DeviceIn, db: Session = Depends(db_dep)):
    d = dev_or_404(db, device_id)
    _check_dev_in(b)
    host_changed = (b.host, b.port) != (d.host, d.port)
    for k, v in b.model_dump().items():
        setattr(d, k, v)
    if host_changed and d.transport == "ssh":
        d.host_key_trusted, d.host_key_fp = False, None
    db.commit()
    audit(db, "device.update", d.name)
    return dev_out(d)


@router.delete("/devices/{device_id}")
def delete_device(device_id: int, db: Session = Depends(db_dep)):
    d = dev_or_404(db, device_id)
    db.delete(d)
    db.commit()
    audit(db, "device.delete", d.name)
    return {"ok": True}


@router.post("/devices/{device_id}/host-key")
def get_host_key(device_id: int, db: Session = Depends(db_dep)):
    d = dev_or_404(db, device_id)
    if d.transport == "simulator":
        return {"type": "simulated", "fingerprint": "SIMULATED", "banner": "SIMULATION", "suggested_vendor": d.vendor}
    k = fetch_host_key(d.host, d.port)
    return {**k, "suggested_vendor": suggest_vendor(k["banner"]), "pinned": d.host_key_fp,
            "changed": bool(d.host_key_fp and d.host_key_fp != k["fingerprint"])}


class TrustIn(BaseModel):
    fingerprint: str


@router.post("/devices/{device_id}/trust-host-key")
def trust_host_key(device_id: int, b: TrustIn, db: Session = Depends(db_dep)):
    d = dev_or_404(db, device_id)
    live = fetch_host_key(d.host, d.port) if d.transport == "ssh" else {"fingerprint": "SIMULATED"}
    if live["fingerprint"] != b.fingerprint:
        raise NCError("host_key_mismatch", "The key presented now differs from the one you reviewed.", 409)
    d.host_key_fp, d.host_key_trusted = b.fingerprint, True
    db.commit()
    audit(db, "device.trust_host_key", d.name, detail={"fingerprint": b.fingerprint})
    return dev_out(d)


@router.post("/devices/{device_id}/identify")
def identify_device(device_id: int, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    d = dev_or_404(db, device_id)
    return dev_out(inventory.identify(db, d, tf))


@router.get("/devices/{device_id}/modules")
def device_modules(device_id: int, db: Session = Depends(db_dep)):
    d = dev_or_404(db, device_id)
    out = []
    for m in MODULE_TITLES:
        st, reason = executor.module_support(d, m)
        out.append({"module": m, "title": MODULE_TITLES[m], "status": st, "reason": reason,
                    "kinds": [k for k in executor.MODULE_CAPS[m] if (d.capabilities.get(k) or {}).get("status") in ("supported", "partial")]})
    return out


@router.post("/devices/import")
def import_devices(payload: dict, db: Session = Depends(db_dep)):
    """CSV text with header: name,host,vendor[,port,group,location,credential]"""
    rows, added, errors = csv.DictReader(io.StringIO(payload.get("csv", ""))), 0, []
    for i, r in enumerate(rows, 2):
        try:
            cred = db.scalars(select(Credential).where(Credential.name == (r.get("credential") or ""))).first()
            _check_dev_in(DeviceIn(name=r["name"], host=r["host"], vendor=r["vendor"], port=int(r.get("port") or 22)))
            if db.scalars(select(Device).where(Device.name == r["name"])).first():
                raise NCError("duplicate", "name exists", 409)
            db.add(Device(name=r["name"], host=r["host"], vendor=r["vendor"], port=int(r.get("port") or 22),
                          group=r.get("group") or None, location=r.get("location") or None, credential_id=cred.id if cred else None))
            db.commit()
            added += 1
        except (NCError, KeyError, ValueError) as e:
            db.rollback()
            errors.append({"line": i, "error": getattr(e, "message", f"bad row: {e}")})
    audit(db, "device.import", None, detail={"added": added, "errors": len(errors)})
    return {"added": added, "errors": errors}


class ScanIn(BaseModel):
    cidr: str
    port: int = 22


@router.post("/discovery/scan")
def scan(b: ScanIn, db: Session = Depends(db_dep)):
    """TCP connect scan for SSH + banner. Does not log in."""
    try:
        net = ipaddress.ip_network(b.cidr, strict=False)
    except ValueError as e:
        raise NCError("validation_failed", "Invalid CIDR", 422) from e
    if net.version != 4 or net.num_addresses > 1024:
        raise NCError("validation_failed", "Scan at most 1024 IPv4 addresses (e.g. a /22).", 422)

    def probe(ip):
        try:
            with socket.create_connection((str(ip), b.port), timeout=0.8) as s:
                s.settimeout(1.0)
                banner = s.recv(128).decode(errors="replace").strip()
            return {"ip": str(ip), "banner": banner, "suggested_vendor": suggest_vendor(banner)}
        except OSError:
            return None

    with ThreadPoolExecutor(64) as pool:
        found = [r for r in pool.map(probe, net.hosts() if net.num_addresses > 2 else [net.network_address]) if r]
    known = {d.host for d in db.scalars(select(Device)).all()}
    for f in found:
        f["known"] = f["ip"] in known
    audit(db, "discovery.scan", b.cidr, detail={"found": len(found)})
    return {"found": found, "note": "Vendor is only a banner heuristic; identification happens after login."}


# ------------------------------------------------------------- changes
class PlanIn(BaseModel):
    spec: dict


def change_out(c: ChangeSet):
    return {"id": c.id, "device_id": c.device_id, "module": c.module, "spec": json.loads(json.dumps(c.spec).replace(executor.PH, "********")), "plan": _mask_plan(c.plan, c.spec), "risk": c.risk,
            "risk_reasons": c.risk_reasons, "current_state": c.current_state, "already_compliant": c.already_compliant,
            "status": c.status, "report": c.report, "after_state": c.after_state, "created_at": c.created_at, "applied_at": c.applied_at}


def _mask_plan(plan: dict, spec: dict) -> dict:
    return json.loads(json.dumps(plan).replace(executor.PH, "********"))


@router.post("/devices/{device_id}/changes/plan")
def plan_change(device_id: int, b: PlanIn, request: Request, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    d = dev_or_404(db, device_id)
    return change_out(executor.plan_change(db, d, b.spec, tf, request.app.state.box))


class ApplyIn(BaseModel):
    ack_risk: bool = False
    confirm_text: str | None = None
    rollback_on_failure: bool = True
    save: bool = False


@router.post("/changes/{change_id}/apply")
def apply_change(change_id: int, b: ApplyIn, request: Request, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    cs = executor.apply_change(db, request.app.state.box, change_id, tf, ack_risk=b.ack_risk, confirm_text=b.confirm_text,
                               rollback_on_failure=b.rollback_on_failure, save=b.save)
    return change_out(cs)


@router.get("/changes")
def list_changes(device_id: int | None = None, limit: int = 100, db: Session = Depends(db_dep)):
    stmt = select(ChangeSet).order_by(ChangeSet.id.desc()).limit(min(limit, 500))
    if device_id:
        stmt = stmt.where(ChangeSet.device_id == device_id)
    return [change_out(c) for c in db.scalars(stmt).all()]


@router.get("/changes/{change_id}")
def get_change(change_id: int, db: Session = Depends(db_dep)):
    c = db.get(ChangeSet, change_id)
    if not c:
        raise NCError("not_found", "Change not found", 404)
    return change_out(c)


# ------------------------------------------------------------- bulk
class BulkPlanIn(BaseModel):
    device_ids: list[int] = Field(min_length=1)
    spec: dict


@router.post("/bulk/plan")
def bulk_plan(b: BulkPlanIn, request: Request, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    rows = []
    for did in b.device_ids:
        d = db.get(Device, did)
        if not d:
            rows.append({"device_id": did, "device": f"#{did}", "ok": False, "error": "unknown device"})
            continue
        try:
            c = executor.plan_change(db, d, b.spec, tf, request.app.state.box)
            rows.append({"device_id": did, "device": d.name, "ok": True, "change": change_out(c)})
        except NCError as e:
            rows.append({"device_id": did, "device": d.name, "ok": False, "error": e.message})
    return {"results": rows, "compatible": sum(r["ok"] for r in rows), "unsupported": sum(not r["ok"] for r in rows)}


class BulkApplyIn(BaseModel):
    change_ids: list[int] = Field(min_length=1)
    mode: str = "sequential"
    concurrency: int = 3
    stop_on_failure: bool = True
    ack_risk: bool = False
    rollback_on_failure: bool = True
    save: bool = False


@router.post("/bulk/apply")
def bulk_apply(b: BulkApplyIn, request: Request, db: Session = Depends(db_dep)):
    items = []
    for cid in b.change_ids:
        c = db.get(ChangeSet, cid)
        if not c or c.status != "planned":
            raise NCError("bad_state", f"Change {cid} is not in 'planned' state.", 409)
        if c.risk == "critical":
            raise NCError("critical_not_bulk", f"Change {cid} is critical risk and cannot run in a bulk job; apply it on the device page.", 409)
        if c.risk == "high" and not b.ack_risk:
            raise NCError("risk_ack_required", f"Change {cid} is high risk; acknowledge to continue.", 409)
        d = db.get(Device, c.device_id)
        items.append({"device_id": d.id, "device": d.name, "change_id": cid, "status": "queued", "detail": "", "risk": c.risk})
    mode = "sequential" if any(i["risk"] == "high" for i in items) else b.mode
    if mode not in ("sequential", "parallel"):
        raise NCError("validation_failed", "mode must be sequential or parallel", 422)
    job = jobs.create_job(db, "bulk_apply", {"mode": mode, "concurrency": b.concurrency, "stop_on_failure": b.stop_on_failure,
                                              "ack_risk": b.ack_risk, "rollback_on_failure": b.rollback_on_failure, "save": b.save}, items)
    jobs.run_job(request.app.state.sm, request.app.state.box, request.app.state.tf_for_db, job.id)
    return job_out(job) | {"forced_sequential": mode != b.mode}


class BulkDevicesIn(BaseModel):
    device_ids: list[int] = Field(min_length=1)
    mode: str = "parallel"
    concurrency: int = 3


@router.post("/bulk/backup")
def bulk_backup(b: BulkDevicesIn, request: Request, db: Session = Depends(db_dep)):
    return _simple_job("bulk_backup", b, request, db)


@router.post("/bulk/identify")
def bulk_identify(b: BulkDevicesIn, request: Request, db: Session = Depends(db_dep)):
    return _simple_job("bulk_identify", b, request, db)


def _simple_job(kind, b, request, db):
    items = [{"device_id": d.id, "device": d.name, "status": "queued", "detail": ""} for d in (db.get(Device, i) for i in b.device_ids) if d]
    job = jobs.create_job(db, kind, {"mode": b.mode, "concurrency": b.concurrency}, items)
    jobs.run_job(request.app.state.sm, request.app.state.box, request.app.state.tf_for_db, job.id)
    return job_out(job)


def job_out(j: Job):
    return {"id": j.id, "kind": j.kind, "status": j.status, "params": j.params, "items": j.items, "created_at": j.created_at, "finished_at": j.finished_at,
            "cancel_requested": j.cancel_requested}


@router.get("/jobs")
def list_jobs(db: Session = Depends(db_dep)):
    return [job_out(j) for j in db.scalars(select(Job).order_by(Job.id.desc()).limit(100)).all()]


@router.get("/jobs/{job_id}")
def get_job(job_id: int, db: Session = Depends(db_dep)):
    j = db.get(Job, job_id)
    if not j:
        raise NCError("not_found", "Job not found", 404)
    return job_out(j)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: int, db: Session = Depends(db_dep)):
    j = db.get(Job, job_id)
    if not j:
        raise NCError("not_found", "Job not found", 404)
    if j.status in ("done", "failed", "cancelled"):
        raise NCError("bad_state", "The job already finished.", 409)
    j.cancel_requested = True
    db.commit()
    audit(db, "job.cancel", f"job#{j.id}")
    return {"ok": True, "note": "Devices not started yet are skipped; a device already being changed finishes safely."}


# ------------------------------------------------------------- backups
@router.post("/devices/{device_id}/backups", status_code=201)
def make_backup(device_id: int, request: Request, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    d = dev_or_404(db, device_id)
    t = tf(d)
    try:
        t.connect()
        made = bk.take_backup(db, request.app.state.box, d, t, "manual", kinds=("running", "startup"))
    finally:
        t.disconnect()
    audit(db, "backup.create", d.name, detail={"ids": [b.id for b in made]})
    return [backup_out(b) for b in made]


def backup_out(b: Backup):
    return {"id": b.id, "device_id": b.device_id, "kind": b.kind, "reason": b.reason, "sha256": b.sha256, "size": b.size,
            "change_id": b.change_id, "created_at": b.created_at}


@router.get("/devices/{device_id}/backups")
def device_backups(device_id: int, db: Session = Depends(db_dep)):
    return [backup_out(b) for b in bk.list_backups(db, device_id)]


@router.get("/backups/{bid}")
def get_backup(bid: int, request: Request, reveal: bool = False, db: Session = Depends(db_dep)):
    b = db.get(Backup, bid)
    if not b:
        raise NCError("not_found", "Backup not found", 404)
    if reveal:
        audit(db, "backup.reveal", f"backup#{bid}")
    return backup_out(b) | {"content": bk.read_backup(request.app.state.box, b, mask=not reveal), "masked": not reveal}


@router.get("/backups/diff/{a}/{b}")
def diff_backups(a: int, b: int, request: Request, db: Session = Depends(db_dep)):
    ba, bb = db.get(Backup, a), db.get(Backup, b)
    if not ba or not bb:
        raise NCError("not_found", "Backup not found", 404)
    box = request.app.state.box
    return {"diff": bk.unified_diff(bk.read_backup(box, ba), bk.read_backup(box, bb), f"backup#{a}", f"backup#{b}")}


@router.post("/backups/{bid}/restore-preview")
def restore_preview(bid: int, request: Request, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    """Shows what restoring would change. Applying a full restore is intentionally NOT implemented."""
    b = db.get(Backup, bid)
    if not b:
        raise NCError("not_found", "Backup not found", 404)
    d = db.get(Device, b.device_id)
    t = tf(d)
    try:
        t.connect()
        cur = t.run(get_adapter(d.vendor).backup_commands()[b.kind])
    finally:
        t.disconnect()
    box = request.app.state.box
    diff = bk.unified_diff(bk.mask_secrets(cur), bk.read_backup(box, b), "device now", f"backup#{bid}")
    return {"diff": diff, "lines_changed": sum(1 for ln in diff.splitlines() if ln[:1] in "+-" and not ln.startswith(("+++", "---"))),
            "note": "Full-configuration restore is not applied automatically (risk of lock-out). Review the diff and use the modules to revert."}


# ------------------------------------------------------------- audit / events
@router.get("/audit")
def audit_list(q: str | None = None, action: str | None = None, limit: int = 200, offset: int = 0, db: Session = Depends(db_dep)):
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)).offset(offset)
    if action:
        stmt = stmt.where(AuditLog.action.like(f"{action}%"))
    if q:
        stmt = stmt.where(or_(AuditLog.target.ilike(f"%{q}%"), AuditLog.action.ilike(f"%{q}%")))
    return [{"id": a.id, "ts": a.ts, "actor": a.actor, "action": a.action, "target": a.target, "result": a.result, "detail": a.detail}
            for a in db.scalars(stmt).all()]


@router.get("/events")
def events(device_id: int | None = None, q: str | None = None, limit: int = 200, db: Session = Depends(db_dep)):
    stmt = select(Event).order_by(Event.id.desc()).limit(min(limit, 1000))
    if device_id:
        stmt = stmt.where(Event.device_id == device_id)
    if q:
        stmt = stmt.where(Event.message.ilike(f"%{q}%"))
    return [{"id": e.id, "ts": e.ts, "device_id": e.device_id, "severity": e.severity, "kind": e.kind, "message": e.message}
            for e in db.scalars(stmt).all()]


# ------------------------------------------------------------- monitoring
@router.get("/monitoring/settings")
def mon_settings(db: Session = Depends(db_dep)):
    return monitoring.get_settings(db)


@router.put("/monitoring/settings")
def mon_put(body: dict, db: Session = Depends(db_dep)):
    return monitoring.put_settings(db, body)


@router.post("/devices/{device_id}/poll")
def poll_now(device_id: int, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    return monitoring.poll_device(db, dev_or_404(db, device_id), tf)


@router.get("/devices/{device_id}/metrics")
def metrics(device_id: int, metric: str = "cpu", limit: int = 288, db: Session = Depends(db_dep)):
    rows = db.scalars(select(Sample).where(Sample.device_id == device_id, Sample.metric == metric).order_by(Sample.id.desc()).limit(min(limit, 5000))).all()
    return [{"ts": s.ts, "value": s.value} for s in reversed(rows)]


@router.get("/dashboard")
def dashboard(db: Session = Depends(db_dep)):
    devs = db.scalars(select(Device)).all()
    by = lambda f: {k: sum(1 for d in devs if f(d) == k) for k in {f(d) for d in devs}}  # noqa: E731
    return {"devices": len(devs), "by_status": by(lambda d: d.status), "by_vendor": by(lambda d: d.vendor),
            "unidentified": sum(1 for d in devs if not d.capabilities),
            "recent_events": events(None, None, 10, db),
            "recent_changes": [change_out(c) for c in db.scalars(select(ChangeSet).order_by(ChangeSet.id.desc()).limit(8)).all()]}


# ------------------------------------------------------------- topology
@router.post("/devices/{device_id}/neighbors/collect")
def collect(device_id: int, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    d = dev_or_404(db, device_id)
    n = monitoring.collect_neighbors(db, d, tf)
    audit(db, "topology.collect", d.name, detail={"neighbors": n})
    return {"neighbors": n}


class LinkIn(BaseModel):
    local_device_id: int
    local_if: str
    remote_name: str
    remote_if: str | None = None
    remote_device_id: int | None = None


@router.post("/topology/links", status_code=201)
def add_link(b: LinkIn, db: Session = Depends(db_dep)):
    dev_or_404(db, b.local_device_id)
    l = Link(**b.model_dump(), source="manual")
    db.add(l)
    db.commit()
    audit(db, "topology.link.add", b.remote_name)
    return {"id": l.id}


@router.delete("/topology/links/{lid}")
def del_link(lid: int, db: Session = Depends(db_dep)):
    l = db.get(Link, lid)
    if l:
        db.delete(l)
        db.commit()
    return {"ok": True}


@router.get("/topology")
def topology(db: Session = Depends(db_dep)):
    devs = db.scalars(select(Device)).all()
    nodes = [{"id": f"d{d.id}", "device_id": d.id, "label": d.name, "role": d.role or "other", "status": d.status, "ip": d.host,
              "vendor": d.vendor, "inventory": True} for d in devs]
    extra, links = {}, []
    for l in db.scalars(select(Link)).all():
        rid = f"d{l.remote_device_id}" if l.remote_device_id else f"x:{l.remote_name}"
        if not l.remote_device_id:
            extra[rid] = {"id": rid, "label": l.remote_name, "role": "unmanaged", "status": "unknown", "inventory": False}
        links.append({"id": l.id, "source": f"d{l.local_device_id}", "target": rid, "local_if": l.local_if, "remote_if": l.remote_if,
                      "origin": l.source})
    return {"nodes": nodes + list(extra.values()), "links": links,
            "note": "Links come only from LLDP/CDP/IP-neighbor tables read from devices, or were added manually."}


# ------------------------------------------------------------- diagnostics
class DiagIn(BaseModel):
    kind: str
    target: str | None = None


@router.post("/devices/{device_id}/diagnostics")
def diagnostics(device_id: int, b: DiagIn, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    d = dev_or_404(db, device_id)
    if b.kind not in ("ping", "traceroute", "arp", "mac", "routes", "interfaces", "neighbors"):
        raise NCError("validation_failed", "Unknown diagnostic", 422)
    if b.kind in ("ping", "traceroute") and not (b.target and HOST_RE.match(b.target)):
        raise NCError("validation_failed", "A valid host name or IP target is required.", 422)
    adapter = get_adapter(d.vendor)
    cmd = adapter.diag_command(b.kind, b.target)
    t = tf(d)
    try:
        t.connect()
        out = t.run(cmd)
    finally:
        t.disconnect()
    summary = None
    if b.kind == "ping":
        m = re.search(r"Success rate is (\d+) percent", out) or re.search(r"packet-loss=(\d+)%", out) or re.search(r"(\d+)% packet loss", out)
        if m:
            summary = (f"Success rate {m[1]}%" if "Success" in m[0] else f"Packet loss {m[1]}%")
    audit(db, "diagnostic.run", d.name, detail={"kind": b.kind, "target": b.target})
    return {"command": cmd, "output": out, "summary": summary}


@router.get("/diagnostics/dns")
def dns_lookup(name: str):
    if not HOST_RE.match(name):
        raise NCError("validation_failed", "Invalid name", 422)
    try:
        res = sorted({ai[4][0] for ai in socket.getaddrinfo(name, None)})
        return {"name": name, "addresses": res, "from": "this PC's resolver"}
    except socket.gaierror as e:
        return {"name": name, "addresses": [], "error": str(e), "from": "this PC's resolver"}


# ------------------------------------------------------------- templates
class TemplateIn(BaseModel):
    name: str
    module: str
    description: str | None = None
    spec: dict
    variables: list[str] = []


def render_spec(spec, values: dict):
    if isinstance(spec, str):
        return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(values.get(m[1], m[0])), spec)
    if isinstance(spec, list):
        return [render_spec(x, values) for x in spec]
    if isinstance(spec, dict):
        return {k: render_spec(v, values) for k, v in spec.items()}
    return spec


@router.post("/templates", status_code=201)
def save_template(b: TemplateIn, db: Session = Depends(db_dep)):
    if b.module not in MODULE_MODELS:
        raise NCError("validation_failed", "Unknown module", 422)
    last = db.scalars(select(Template).where(Template.name == b.name).order_by(Template.version.desc())).first()
    t = Template(name=b.name, version=(last.version + 1) if last else 1, module=b.module, description=b.description,
                 spec=b.spec, variables=b.variables or sorted(set(re.findall(r"\{\{\s*(\w+)\s*\}\}", json.dumps(b.spec)))))
    db.add(t)
    db.commit()
    audit(db, "template.save", b.name, detail={"version": t.version})
    return {"id": t.id, "name": t.name, "version": t.version, "variables": t.variables}


@router.get("/templates")
def list_templates(db: Session = Depends(db_dep)):
    return [{"id": t.id, "name": t.name, "version": t.version, "module": t.module, "description": t.description, "spec": t.spec,
             "variables": t.variables} for t in db.scalars(select(Template).order_by(Template.name, Template.version.desc())).all()]


class RenderIn(BaseModel):
    values: dict = {}
    device_ids: list[int] = []


@router.post("/templates/{tid}/plan")
def template_plan(tid: int, b: RenderIn, request: Request, db: Session = Depends(db_dep), tf=Depends(tf_dep)):
    t = db.get(Template, tid)
    if not t:
        raise NCError("not_found", "Template not found", 404)
    missing = [v for v in t.variables if v not in b.values]
    if missing:
        raise NCError("validation_failed", f"Missing values for: {', '.join(missing)}", 422)
    spec = render_spec(t.spec, b.values)
    spec.setdefault("module", t.module)
    return bulk_plan(BulkPlanIn(device_ids=b.device_ids, spec=spec), request, db, tf)
