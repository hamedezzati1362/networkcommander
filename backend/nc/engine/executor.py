"""Configuration engine: plan -> (preview) -> backup -> apply -> verify -> rollback/report."""
from __future__ import annotations

import json
import re
import threading
from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nc.core.audit import audit
from nc.core.errors import NCError, TransportError, Unsupported
from nc.engine import risk as riskmod
from nc.engine.specs import MODULE_TITLES, parse_spec
from nc.models import ChangeSet, Device
from nc.services.backups import mask_secrets, take_backup, unified_diff
from nc.vendors.base import Check, DeviceContext, Plan
from nc.vendors.registry import get_adapter

# module -> capability keys, any of which allows the module (kind-specific check happens in the translator)
MODULE_CAPS = {
    "hostname": ["hostname"], "vlan": ["vlan_database", "svi", "vlan_interface"], "interface": ["interface"],
    "static_route": ["static_route"], "ntp": ["ntp"], "dns": ["dns"], "syslog": ["syslog"], "snmp": ["snmp"], "system": ["system"],
}
_locks: dict[int, threading.Lock] = {}
_locks_guard = threading.Lock()


def device_lock(device_id: int) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(device_id, threading.Lock())


def context_for(device: Device) -> DeviceContext:
    return DeviceContext(device.host, device.vendor, device.os_family, device.os_version, device.role, device.name,
                         device.iface_ips or {}, device.capabilities or {})


def module_support(device: Device, module: str) -> tuple[str, str]:
    caps = device.capabilities or {}
    if not caps:
        return "not_detected", "Run 'Identify & detect capabilities' on this device first."
    best = ("unsupported", "")
    rank = {"supported": 3, "partial": 2, "requires_privilege": 1, "not_detected": 0, "unsupported": 0}
    for k in MODULE_CAPS[module]:
        c = caps.get(k) or {"status": "not_detected", "reason": "not probed"}
        if rank.get(c["status"], 0) >= rank.get(best[0], 0) and (c["status"] != "unsupported" or not best[1]):
            best = (c["status"], c.get("reason", ""))
        if c["status"] in ("supported", "partial"):
            return c["status"], c.get("reason", "")
    return best


def _eval(check: Check, out: str) -> tuple[bool, list[str]]:
    problems = []
    for p in check.present:
        if not re.search(p, out, re.M):
            problems.append(f"expected pattern not found: {p}")
    for p in check.absent:
        if re.search(p, out, re.M):
            problems.append(f"unexpected pattern still present: {p}")
    return (not problems), problems


def _checks(transport, checks: list[Check]) -> tuple[bool, list[str]]:
    ok, probs, cache = True, [], {}
    for c in checks:
        out = cache.get(c.command)
        if out is None:
            out = cache[c.command] = transport.run(c.command)
        good, p = _eval(c, out)
        ok &= good
        probs += p
    return ok, probs


PH = "@@SECRET:community@@"


def _sub(obj, old: str, new: str, esc_old: str | None = None, esc_new: str | None = None):
    """Replace secret text in a nested structure (raw in commands, escaped inside regex patterns)."""
    if isinstance(obj, str):
        if esc_old:
            obj = obj.replace(esc_old, esc_new)
        return obj.replace(old, new)
    if isinstance(obj, list):
        return [_sub(x, old, new, esc_old, esc_new) for x in obj]
    if isinstance(obj, dict):
        return {k: _sub(v, old, new, esc_old, esc_new) for k, v in obj.items()}
    return obj


def plan_change(db: Session, device: Device, data: dict, transport_factory, box=None) -> ChangeSet:
    try:
        spec = parse_spec(data)
    except Exception as e:  # pydantic ValidationError -> readable
        msgs = getattr(e, "errors", lambda: [])()
        text = "; ".join(f"{'.'.join(str(x) for x in m['loc'])}: {m['msg']}" for m in msgs) or str(e)
        raise NCError("validation_failed", text, 422) from e
    status, reason = module_support(device, spec.module)
    if status in ("not_detected", "unsupported"):
        raise Unsupported(f"{MODULE_TITLES[spec.module]} is not available on this device: {reason}")
    ctx = context_for(device)
    adapter = get_adapter(device.vendor)
    plan = adapter.translate(spec, ctx)
    level, why = riskmod.classify(spec, ctx)
    if status == "requires_privilege":
        plan.warnings.append("The account may lack privilege for this module; the change may be refused by the device.")
    current, compliant = None, False
    if plan.state_command or plan.checks:
        t = transport_factory(device)
        try:
            t.connect()
            if plan.state_command:
                current = t.run(plan.state_command)
            if plan.checks and spec.module != "system" and (getattr(spec, "action", "present") != "reboot"):
                ok, _ = _checks(t, plan.checks)
                compliant = ok
        except TransportError as e:
            plan.warnings.append(f"Could not read current state ({e.message}); preview shows the change only.")
        finally:
            t.disconnect()
    if plan.no_op_reason:
        compliant = True
        plan.warnings.append(plan.no_op_reason)
    spec_d, plan_d, blob = spec.model_dump(), asdict(plan), None
    secret = getattr(spec, "community", None)
    if secret:  # never persist the secret in clear: keep it protected and use a placeholder
        if box is None:
            raise NCError("internal_error", "secret store unavailable", 500)
        blob = box.protect(json.dumps({"community": secret}).encode())
        spec_d["community"] = PH
        plan_d = _sub(plan_d, secret, PH, re.escape(secret), PH)
    cs = ChangeSet(device_id=device.id, module=spec.module, spec=spec_d, plan=plan_d, risk=level, secrets_blob=blob,
                   risk_reasons=why, current_state=mask_secrets(current) if current else current, already_compliant=compliant,
                   status="planned")
    db.add(cs)
    db.commit()
    audit(db, "change.plan", device.name, detail={"change_id": cs.id, "module": spec.module, "risk": level, "spec": spec_d})
    return cs


def _step(report: list, step: str, status: str, detail: str = ""):
    report.append({"step": step, "status": status, "detail": detail, "ts": datetime.now(UTC).isoformat()})


def apply_change(db: Session, box, change_id: int, transport_factory, *, ack_risk=False, confirm_text: str | None = None,
                 rollback_on_failure=True, save=False) -> ChangeSet:
    cs = db.get(ChangeSet, change_id)
    if not cs:
        raise NCError("not_found", "Change not found", 404)
    if cs.status != "planned":
        raise NCError("bad_state", f"This change is already {cs.status}; create a new plan.", 409)
    device = db.get(Device, cs.device_id)
    if cs.risk in ("high", "critical") and not ack_risk:
        raise NCError("risk_ack_required", f"Risk is {cs.risk}: {'; '.join(cs.risk_reasons)} Acknowledge the risk to continue.", 409)
    if cs.risk == "critical" and confirm_text != device.name:
        raise NCError("confirmation_required", "Critical change: type the exact device name to confirm.", 409)
    planned = cs.plan
    if cs.secrets_blob:
        secret = json.loads(box.unprotect(cs.secrets_blob))["community"]
        planned = _sub(cs.plan, PH, secret)
        planned["checks"] = _sub(cs.plan["checks"], PH, re.escape(secret))
    plan = Plan(**{**planned, "checks": [Check(**c) for c in planned["checks"]],
                   "existence": Check(**cs.plan["existence"]) if cs.plan.get("existence") else None,
                   "interactive": [tuple(x) for x in cs.plan.get("interactive", [])]})
    lock = device_lock(device.id)
    if not lock.acquire(blocking=False):
        raise NCError("device_busy", "Another change is running on this device.", 409)
    report: list = []
    adapter = get_adapter(device.vendor)
    t = transport_factory(device)
    cs.status = "failed"
    try:
        if cs.already_compliant and not plan.interactive:
            _step(report, "pre-check", "skipped", "Device already matches the requested state; nothing sent.")
            cs.status = "skipped"
            return _finish(db, cs, report, device)
        t.connect()
        _step(report, "connect", "ok")
        exists_before = False
        if plan.existence:
            exists_before = _eval(plan.existence, t.run(plan.existence.command))[0]
        before = t.run(plan.state_command) if plan.state_command else ""
        if module_is_config(cs.module):
            b = take_backup(db, box, device, t, "pre-change", cs.id)
            _step(report, "backup", "ok", f"running-config backup #{b[0].id} saved before the change")
        if plan.interactive:
            for cmd, _ in plan.interactive:
                _step(report, "send", "ok", cmd)
                try:
                    t.run_interactive(cmd)
                except TransportError:
                    pass  # expected: the device drops the session while rebooting
            _step(report, "verify", "skipped", "Device is rebooting; verification is not possible until it is back.")
            cs.status = "applied"
            audit(db, "change.apply", device.name, detail={"change_id": cs.id, "module": cs.module, "reboot": True})
            return _finish(db, cs, report, device)
        out = t.run_config(plan.commands)
        errs = adapter.find_errors(out)
        if errs:
            _step(report, "apply", "failed", "; ".join(errs[:3]))
            _rollback(t, plan, exists_before, rollback_on_failure, report)
            cs.status = "rolled_back" if any(r["step"] == "rollback" and r["status"] == "ok" for r in report) else "failed"
            return _finish(db, cs, report, device)
        _step(report, "apply", "ok", f"{len(plan.commands)} operation(s) accepted")
        ok, probs = _checks(t, plan.checks)
        if not ok:
            _step(report, "verify", "failed", "; ".join(probs[:3]))
            _rollback(t, plan, exists_before, rollback_on_failure, report)
            cs.status = "rolled_back" if any(r["step"] == "rollback" and r["status"] == "ok" for r in report) else "failed"
            return _finish(db, cs, report, device)
        _step(report, "verify", "ok", "device state matches the request")
        after = t.run(plan.state_command) if plan.state_command else ""
        cs.after_state = unified_diff(mask_secrets(before), mask_secrets(after), "before", "after") or "(no textual difference in the affected section)"
        if save and plan.save_commands is not None:
            sv = adapter.translate(parse_spec({"module": "system", "action": "save"}), context_for(device))
            if sv.commands:
                o2 = t.run_config(sv.commands)
                e2 = adapter.find_errors(o2)
                _step(report, "save", "failed" if e2 else "ok", e2[0] if e2 else "configuration saved")
            else:
                _step(report, "save", "skipped", sv.no_op_reason or "")
        else:
            _step(report, "save", "skipped", "Not saved to startup configuration (running config only)." if device.vendor == "cisco" else "")
        cs.status = "applied"
        return _finish(db, cs, report, device)
    except TransportError as e:
        _step(report, "connection", "failed", e.message)
        cs.status = "unknown_state"
        report.append({"step": "note", "status": "failed", "detail":
                       "Connection was lost: the device may be partially configured. Compare with the pre-change backup before retrying.",
                       "ts": datetime.now(UTC).isoformat()})
        return _finish(db, cs, report, device)
    except NCError as e:
        _step(report, "error", "failed", e.message)
        return _finish(db, cs, report, device)
    finally:
        t.disconnect()
        lock.release()


def module_is_config(module: str) -> bool:
    return module != "system"


def _rollback(t, plan: Plan, exists_before: bool, enabled: bool, report: list):
    if not enabled:
        _step(report, "rollback", "skipped", "Rollback disabled for this run.")
    elif exists_before or not plan.rollback:
        _step(report, "rollback", "skipped",
              "No automatic rollback: the object existed before (or the change is not reversible by a single command). "
              "Use the pre-change backup to review and restore manually.")
    else:
        try:
            out = t.run_config(plan.rollback)
            _step(report, "rollback", "ok", "Newly created objects were removed again.")
        except TransportError as e:
            _step(report, "rollback", "failed", e.message)


def _finish(db, cs, report, device):
    cs.report = report
    cs.applied_at = datetime.now(UTC)
    db.commit()
    audit(db, "change.apply", device.name, result="ok" if cs.status in ("applied", "skipped") else "failed",
          detail={"change_id": cs.id, "module": cs.module, "status": cs.status})
    return cs
