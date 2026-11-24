"""Device identification and honest capability detection."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from nc.core.audit import audit
from nc.core.errors import NCError, TransportError
from nc.models import Device
from nc.vendors.base import NOT_DETECTED
from nc.vendors.registry import get_adapter


def identify(db: Session, device: Device, transport_factory) -> Device:
    """Connect, read identity, probe each capability with a real read-only command."""
    adapter = get_adapter(device.vendor)
    t = transport_factory(device)
    try:
        t.connect()
        outs = {c: t.run(c) for c in adapter.identify_commands()}
        ident = adapter.parse_identity(outs)  # raises NCError if the output is another vendor
        caps: dict = {}
        for probe in adapter.probes(ident):
            try:
                out = t.run(probe.command)
                status, reason = adapter.classify_probe(out)
            except TransportError as e:
                status, reason = NOT_DETECTED, e.message
            for cap in probe.capabilities:
                caps[cap] = {"status": status, "reason": reason, "probe": probe.command}
        caps = adapter.refine_capabilities(caps, ident)
        if adapter.iface_ip_command:
            ip_out = {adapter.iface_ip_command: t.run(adapter.iface_ip_command)}
            device.iface_ips = adapter.parse_interface_ips(ip_out)
        if ident.vendor == "cisco" and caps.get("vlan_database", {}).get("status") == "supported":
            ident.role = "switch"  # measured, overrides the model-name heuristic
        elif ident.vendor == "cisco" and caps.get("vlan_database", {}).get("status") == "unsupported" and ident.role is None:
            ident.role = "router"
        device.os_family, device.os_version, device.model = ident.os_family, ident.os_version, ident.model
        device.serial, device.hostname, device.uptime, device.role = ident.serial, ident.hostname, ident.uptime, ident.role
        device.capabilities = caps
        device.status, device.status_detail = "online", None
        device.last_seen = device.last_identified = datetime.now(UTC)
        db.commit()
        audit(db, "device.identify", device.name, detail={"os": ident.os_family, "version": ident.os_version, "model": ident.model})
        return device
    except TransportError as e:
        device.status = "auth_failed" if e.code == "auth_failed" else ("offline" if e.code in ("unreachable", "timeout") else "error")
        device.status_detail = e.message
        db.commit()
        audit(db, "device.identify", device.name, "failed", {"error": e.message})
        raise
    except NCError as e:
        device.status, device.status_detail = "error", e.message
        db.commit()
        audit(db, "device.identify", device.name, "failed", {"error": e.message})
        raise
    finally:
        t.disconnect()
