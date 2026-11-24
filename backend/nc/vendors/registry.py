from __future__ import annotations

from nc.core.errors import NCError
from nc.vendors.base import VendorAdapter
from nc.vendors.cisco import CiscoAdapter
from nc.vendors.fortinet import FortinetAdapter
from nc.vendors.mikrotik import MikrotikAdapter

_ADAPTERS: dict[str, VendorAdapter] = {}


def register(adapter: VendorAdapter) -> None:
    """Plugin entry point: add a new vendor adapter."""
    _ADAPTERS[adapter.key] = adapter


for _a in (CiscoAdapter(), MikrotikAdapter(), FortinetAdapter()):
    register(_a)


def get_adapter(vendor: str) -> VendorAdapter:
    try:
        return _ADAPTERS[vendor]
    except KeyError as e:
        raise NCError("unknown_vendor", f"No adapter for vendor '{vendor}'", 422) from e


def vendors() -> list[str]:
    return sorted(_ADAPTERS)
