from __future__ import annotations

import json
from typing import Callable

from nc.core.errors import NCError
from nc.models import Credential, Device
from nc.vendors.registry import get_adapter

# Tests / simulator can register a factory for transport == "simulator"
_SIM_FACTORY: Callable[[Device], object] | None = None


def set_simulator_factory(fn) -> None:
    global _SIM_FACTORY
    _SIM_FACTORY = fn


def make_transport(db, box, device: Device, allow_simulator: bool, override_fp: str | None = None):
    if device.transport == "simulator":
        if not allow_simulator or _SIM_FACTORY is None:
            raise NCError("simulator_disabled", "Simulated devices are disabled. Start with NC_ENABLE_SIMULATOR=1.", 403)
        return _SIM_FACTORY(device)
    from nc.transport.ssh import SshTransport
    cred = db.get(Credential, device.credential_id) if device.credential_id else None
    if not cred:
        raise NCError("no_credential", "This device has no credential profile.", 422)
    secrets = json.loads(box.unprotect(cred.secret_blob))
    adapter = get_adapter(device.vendor)
    return SshTransport(device.host, device.port, cred.username, secrets, adapter.netmiko_type,
                        override_fp or (device.host_key_fp if device.host_key_trusted else None))
