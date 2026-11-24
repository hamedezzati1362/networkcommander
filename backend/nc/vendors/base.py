"""Vendor adapter contract and shared data types."""
from __future__ import annotations

import ipaddress
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field

# Capability status values shown in the UI
SUPPORTED, PARTIAL, UNSUPPORTED, NOT_DETECTED, NEEDS_PRIV = (
    "supported", "partial", "unsupported", "not_detected", "requires_privilege")


@dataclass
class Identity:
    vendor: str
    os_family: str
    os_version: str | None = None
    model: str | None = None
    serial: str | None = None
    hostname: str | None = None
    uptime: str | None = None
    role: str | None = None  # heuristic


@dataclass
class Probe:
    capabilities: list[str]
    command: str


@dataclass
class Check:
    """Read-only verification: run `command`, every `present` regex must match and no `absent` regex may."""
    command: str
    present: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)


@dataclass
class Plan:
    commands: list[str]
    summary: list[str]                         # human readable, secrets masked
    checks: list[Check]                        # post-change verification (also used to detect "already done")
    state_command: str | None = None           # read-only command showing the affected section
    existence: Check | None = None             # matches if the object existed before the change
    rollback: list[str] = field(default_factory=list)  # valid only when the object did NOT exist before
    warnings: list[str] = field(default_factory=list)
    interactive: list[tuple[str, str]] = field(default_factory=list)  # (command, answer) for reboot prompts
    expects_disconnect: bool = False
    save_commands: list[str] = field(default_factory=list)
    no_op_reason: str | None = None


@dataclass
class DeviceContext:
    host: str
    vendor: str
    os_family: str | None
    os_version: str | None
    role: str | None
    name: str
    iface_ips: dict[str, str] = field(default_factory=dict)
    caps: dict = field(default_factory=dict)

    def has(self, cap: str) -> bool:
        return (self.caps.get(cap) or {}).get("status") in ("supported", "partial")

    @property
    def major(self) -> int:
        m = re.match(r"(\d+)", self.os_version or "")
        return int(m[1]) if m else 0


def mask(cidr: str) -> tuple[str, str]:
    i = ipaddress.ip_interface(cidr)
    return str(i.ip), str(i.network.netmask)


class VendorAdapter(ABC):
    key: str
    netmiko_type: str
    error_patterns: list[str]
    privilege_patterns = [r"permission denied", r"not authorized", r"authorization failed", r"not permitted",
                          r"insufficient privilege", r"-37\b"]

    # ---- identification -------------------------------------------------
    @abstractmethod
    def identify_commands(self) -> list[str]: ...
    @abstractmethod
    def parse_identity(self, outputs: dict[str, str]) -> Identity: ...
    @abstractmethod
    def probes(self, identity: Identity) -> list[Probe]: ...
    def refine_capabilities(self, caps: dict, identity: Identity) -> dict:
        return caps

    def parse_interface_ips(self, outputs: dict[str, str]) -> dict[str, str]:
        return {}
    iface_ip_command: str | None = None

    # ---- backups --------------------------------------------------------
    @abstractmethod
    def backup_commands(self) -> dict[str, str]: ...  # kind -> command

    # ---- configuration --------------------------------------------------
    @abstractmethod
    def translate(self, spec, ctx: DeviceContext) -> Plan: ...

    # ---- monitoring / topology / diagnostics ----------------------------
    metrics_commands: list[str] = []
    def parse_metrics(self, outputs: dict[str, str]) -> dict[str, float]:
        return {}
    neighbor_commands: list[str] = []
    def parse_neighbors(self, output: str) -> list[dict]:
        return []
    @abstractmethod
    def diag_command(self, kind: str, arg: str | None) -> str: ...

    # ---- helpers --------------------------------------------------------
    def find_errors(self, output: str) -> list[str]:
        return [ln.strip() for ln in output.splitlines() if any(re.search(p, ln, re.I) for p in self.error_patterns)]

    def classify_probe(self, output: str | None) -> tuple[str, str]:
        """Map a probe's output to a capability status."""
        if output is None:
            return NOT_DETECTED, "probe could not run"
        for p in self.privilege_patterns:
            if re.search(p, output, re.I):
                return NEEDS_PRIV, "the account lacks privilege for this command"
        errs = self.find_errors(output)
        if errs:
            return UNSUPPORTED, errs[0][:120]
        return SUPPORTED, "probe command accepted by the device"
