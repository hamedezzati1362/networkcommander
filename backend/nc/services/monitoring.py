from __future__ import annotations

import socket
import threading
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from nc.core.errors import TransportError
from nc.models import Device, Event, Link, Sample, Setting
from nc.vendors.registry import get_adapter

DEFAULTS = {"interval_s": 300, "cpu_warn": 85, "mem_warn": 90, "enabled": True, "retention_days": 30}


def get_settings(db: Session) -> dict:
    s = db.get(Setting, "monitoring")
    return {**DEFAULTS, **(s.value if s else {})}


def put_settings(db: Session, value: dict) -> dict:
    cur = get_settings(db)
    for k in DEFAULTS:
        if k in value:
            cur[k] = type(DEFAULTS[k])(value[k])
    s = db.get(Setting, "monitoring")
    if s:
        s.value = cur
    else:
        db.add(Setting(key="monitoring", value=cur))
    db.commit()
    return cur


def tcp_up(host: str, port: int, timeout=3.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _event(db, dev_id, sev, kind, msg):
    db.add(Event(device_id=dev_id, severity=sev, kind=kind, message=msg))


def poll_device(db: Session, device: Device, transport_factory, with_metrics=True) -> dict:
    cfg = get_settings(db)
    sim = device.transport == "simulator"
    up = True if sim else tcp_up(device.host, device.port)
    prev = db.scalars(select(Sample).where(Sample.device_id == device.id, Sample.metric == "up").order_by(Sample.id.desc()).limit(1)).first()
    db.add(Sample(device_id=device.id, metric="up", value=1.0 if up else 0.0))
    if prev is not None and (prev.value == 1.0) != up:
        _event(db, device.id, "critical" if not up else "info", "availability", f"{device.name} is {'reachable again' if up else 'DOWN'}")
    device.status = "online" if up else "offline"
    if up:
        device.last_seen = datetime.now(UTC)
    res = {"up": up}
    adapter = get_adapter(device.vendor)
    caps = device.capabilities or {}
    if up and with_metrics and (caps.get("metrics") or {}).get("status") == "supported" and adapter.metrics_commands and device.host_key_trusted:
        t = transport_factory(device)
        try:
            t.connect()
            m = adapter.parse_metrics({c: t.run(c) for c in adapter.metrics_commands})
            for k, v in m.items():
                db.add(Sample(device_id=device.id, metric=k, value=v))
                if k == "cpu" and v >= cfg["cpu_warn"]:
                    _event(db, device.id, "warning", "cpu", f"{device.name}: CPU {v}% >= {cfg['cpu_warn']}%")
                if k == "mem" and v >= cfg["mem_warn"]:
                    _event(db, device.id, "warning", "memory", f"{device.name}: memory {v}% >= {cfg['mem_warn']}%")
            res.update(m)
        except TransportError as e:
            _event(db, device.id, "warning", "metrics", f"{device.name}: metrics failed: {e.message}")
        finally:
            t.disconnect()
    db.commit()
    return res


def collect_neighbors(db: Session, device: Device, transport_factory) -> int:
    """Store only what the device itself reports (LLDP/CDP/IP neighbor tables)."""
    adapter = get_adapter(device.vendor)
    if not adapter.neighbor_commands:
        raise TransportError(f"{device.vendor} neighbor discovery is not implemented", "unsupported")
    t = transport_factory(device)
    found = []
    try:
        t.connect()
        for c in adapter.neighbor_commands:
            found += adapter.parse_neighbors(t.run(c))
    finally:
        t.disconnect()
    for l in db.scalars(select(Link).where(Link.local_device_id == device.id, Link.source != "manual")).all():
        db.delete(l)
    names = {(d.hostname or "").lower(): d.id for d in db.scalars(select(Device)).all() if d.hostname}
    names.update({d.name.lower(): d.id for d in db.scalars(select(Device)).all()})
    for n in found:
        db.add(Link(local_device_id=device.id, local_if=n["local_if"], remote_name=n["remote_name"], remote_if=n.get("remote_if"),
                    remote_device_id=names.get(n["remote_name"].lower()), source=n["source"]))
    db.commit()
    return len(found)


class Scheduler(threading.Thread):
    def __init__(self, sessionmaker_, transport_factory_for_db):
        super().__init__(daemon=True, name="nc-scheduler")
        self.sm, self.tf, self._stop_evt = sessionmaker_, transport_factory_for_db, threading.Event()

    def stop(self):
        self._stop_evt.set()

    def run(self):
        while not self._stop_evt.is_set():
            interval = 300
            try:
                with self.sm() as db:
                    cfg = get_settings(db)
                    interval = max(30, cfg["interval_s"])
                    if cfg["enabled"]:
                        for d in db.scalars(select(Device)).all():
                            if self._stop_evt.is_set():
                                break
                            poll_device(db, d, self.tf(db))
                        cutoff = datetime.now(UTC) - timedelta(days=cfg["retention_days"])
                        for s in db.scalars(select(Sample).where(Sample.ts < cutoff)).all():
                            db.delete(s)
                        db.commit()
            except Exception:  # noqa: BLE001  never let the poller die
                import logging
                logging.getLogger("nc.scheduler").exception("poll cycle failed")
            self._stop_evt.wait(interval)
