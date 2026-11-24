"""Audit trail. Secrets are scrubbed from details before storing."""
from __future__ import annotations

from sqlalchemy.orm import Session

from nc.models import AuditLog

_SECRET_KEYS = ("password", "secret", "token", "private_key", "community", "enable", "key")


def scrub(obj):
    if isinstance(obj, dict):
        return {k: ("***" if any(s in k.lower() for s in _SECRET_KEYS) and v else scrub(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def audit(db: Session, action: str, target: str | None = None, result: str = "ok", detail: dict | None = None) -> None:
    db.add(AuditLog(action=action, target=target, result=result, detail=scrub(detail or {})))
    db.commit()
