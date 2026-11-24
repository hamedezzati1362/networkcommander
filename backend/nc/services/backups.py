from __future__ import annotations

import difflib
import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from nc.core.errors import NCError
from nc.models import Backup, Device
from nc.vendors.registry import get_adapter

_SECRET_LINE = re.compile(r"(?i)(password|secret|community|key|passphrase|auth-?pass|priv-?pass|psksecret|token)(\s+|=|\")")
_SECRET_VAL = re.compile(r"(?i)((?:password|secret|community|key|passphrase|psksecret|token)[\s=\"]+(?:\d\s+)?)(\S+)")


def mask_secrets(text: str) -> str:
    out = []
    for ln in text.splitlines():
        out.append(_SECRET_VAL.sub(lambda m: m[1] + "********", ln) if _SECRET_LINE.search(ln) else ln)
    return "\n".join(out)


def take_backup(db: Session, box, device: Device, transport, reason="manual", change_id=None, kinds=("running",)) -> list[Backup]:
    adapter = get_adapter(device.vendor)
    cmds = adapter.backup_commands()
    made = []
    for kind in kinds:
        if kind not in cmds:
            continue
        text = transport.run(cmds[kind])
        errs = adapter.find_errors(text)
        if (errs or not text.strip()) and kind != "running":
            continue  # startup config is best effort (e.g. not present on a fresh device)
        if errs or not text.strip():
            raise NCError("backup_failed", f"Backup ({kind}) failed: {errs[0] if errs else 'empty output'}", 502)
        b = Backup(device_id=device.id, kind=kind, reason=reason, content_blob=box.protect(text.encode()),
                   sha256=hashlib.sha256(text.encode()).hexdigest(), size=len(text), change_id=change_id)
        db.add(b)
        made.append(b)
    db.commit()
    return made


def read_backup(box, b: Backup, mask=True) -> str:
    t = box.unprotect(b.content_blob).decode()
    return mask_secrets(t) if mask else t


def unified_diff(a: str, b: str, la="before", lb="after") -> str:
    return "\n".join(difflib.unified_diff(a.splitlines(), b.splitlines(), la, lb, lineterm="", n=2))


def list_backups(db: Session, device_id: int):
    return db.scalars(select(Backup).where(Backup.device_id == device_id).order_by(Backup.id.desc())).all()
