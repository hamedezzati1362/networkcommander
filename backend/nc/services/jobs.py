"""Multi-device jobs with sequential/parallel execution and concurrency limits."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nc.core.audit import audit
from nc.core.errors import NCError
from nc.engine.executor import apply_change
from nc.models import Device, Job
from nc.services.backups import take_backup
from nc.services.inventory import identify


def create_job(db: Session, kind: str, params: dict, items: list[dict]) -> Job:
    j = Job(kind=kind, params=params, items=items, status="queued")
    db.add(j)
    db.commit()
    audit(db, f"job.{kind}", f"job#{j.id}", detail={"devices": [i["device"] for i in items], **{k: v for k, v in params.items() if k != "spec"}})
    return j


def run_job(sessionmaker_, box, transport_factory_for_db, job_id: int, wait=False) -> threading.Thread:
    def work():
        with sessionmaker_() as db:
            job = db.get(Job, job_id)
            job.status = "running"
            db.commit()
            p = job.params
            mode = p.get("mode", "sequential")
            workers = 1 if mode == "sequential" else max(1, min(int(p.get("concurrency", 3)), 10))
            stop = threading.Event()
            lock = threading.Lock()

            def one(idx: int):
                with sessionmaker_() as s2:
                    jb = s2.get(Job, job_id)
                    if jb.cancel_requested or stop.is_set():
                        _set(s2, job_id, idx, {"status": "cancelled", "detail": "Job cancelled before this device was touched."}, lock)
                        return
                    item = jb.items[idx]
                    dev = s2.get(Device, item["device_id"])
                    tf = transport_factory_for_db(s2)
                    try:
                        if jb.kind == "bulk_apply":
                            cs = apply_change(s2, box, item["change_id"], tf, ack_risk=p.get("ack_risk", False),
                                              rollback_on_failure=p.get("rollback_on_failure", True), save=p.get("save", False))
                            res = {"status": cs.status, "detail": "; ".join(f"{r['step']}:{r['status']}" for r in cs.report)}
                            if cs.status not in ("applied", "skipped") and p.get("stop_on_failure", True):
                                stop.set()
                        elif jb.kind == "bulk_backup":
                            t = tf(dev)
                            t.connect()
                            try:
                                b = take_backup(s2, box, dev, t, "manual", kinds=("running", "startup"))
                            finally:
                                t.disconnect()
                            res = {"status": "applied", "detail": f"{len(b)} backup(s) stored"}
                        elif jb.kind == "bulk_identify":
                            identify(s2, dev, tf)
                            res = {"status": "applied", "detail": f"{dev.os_family} {dev.os_version}"}
                        else:
                            res = {"status": "failed", "detail": "unknown job kind"}
                    except NCError as e:
                        res = {"status": "failed", "detail": e.message}
                        if jb.kind == "bulk_apply" and p.get("stop_on_failure", True):
                            stop.set()
                    except Exception as e:  # noqa: BLE001
                        res = {"status": "failed", "detail": f"unexpected error: {e}"}
                    _set(s2, job_id, idx, res, lock)

            n = len(job.items)
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(one, range(n)))
            job = db.get(Job, job_id)
            db.refresh(job)
            bad = [i for i in job.items if i.get("status") not in ("applied", "skipped")]
            job.status = "cancelled" if job.cancel_requested else ("done" if not bad else "failed")
            job.finished_at = datetime.now(UTC)
            db.commit()

    th = threading.Thread(target=work, daemon=True, name=f"job-{job_id}")
    th.start()
    if wait:
        th.join()
    return th


def _set(db, job_id, idx, res, lock):
    with lock:
        jb = db.get(Job, job_id)
        db.refresh(jb)  # another worker may have written other rows
        items = [dict(i) for i in jb.items]
        items[idx].update(res)
        jb.items = items
        db.commit()
