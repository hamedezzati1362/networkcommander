"""Normalized, vendor-independent configuration requests (validated with Pydantic).

The UI sends these; vendor translators turn them into device operations.
All free-text fields use a strict charset so no value can smuggle extra CLI lines.
"""
from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
IFACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9/.:_-]{0,62}$")
TEXT_RE = re.compile(r"^[A-Za-z0-9 _.,/:@#()+=-]{1,80}$")
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
SECRET_RE = re.compile(r"^[A-Za-z0-9_.@#%+=!-]{1,64}$")


def _ip(v: str, version: int | None = None) -> str:
    try:
        a = ipaddress.ip_address(v)
    except ValueError as e:
        raise ValueError(f"'{v}' is not a valid IP address") from e
    if version and a.version != version:
        raise ValueError(f"'{v}' must be an IPv{version} address")
    return str(a)


def _cidr_if(v: str) -> str:
    try:
        i = ipaddress.ip_interface(v)
    except ValueError as e:
        raise ValueError(f"'{v}' must be an address with prefix length, e.g. 192.168.10.1/24") from e
    if i.version != 4:
        raise ValueError("only IPv4 interface addresses are supported in this version")
    return str(i)


def _cidr_net(v: str) -> str:
    try:
        n = ipaddress.ip_network(v, strict=True)
    except ValueError as e:
        raise ValueError(f"'{v}' must be a network like 10.0.0.0/24 (host bits must be zero)") from e
    if n.version != 4:
        raise ValueError("only IPv4 routes are supported in this version")
    return str(n)


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class HostnameSpec(_Base):
    module: Literal["hostname"] = "hostname"
    hostname: str = Field(description="New device hostname")

    @field_validator("hostname")
    @classmethod
    def _h(cls, v):
        if not NAME_RE.match(v):
            raise ValueError("hostname may contain letters, digits, '-', '_' and '.' only")
        return v


class VlanSpec(_Base):
    module: Literal["vlan"] = "vlan"
    action: Literal["present", "absent"] = "present"
    kind: Literal["vlan_database", "svi", "vlan_interface"] = Field(
        "vlan_database",
        description="vlan_database: L2 VLAN (Cisco switches). svi: Cisco 'interface vlan N'. "
        "vlan_interface: tagged sub-interface (MikroTik / FortiGate).",
    )
    vlan_id: int = Field(ge=1, le=4094)
    name: str | None = Field(None, description="VLAN name")
    parent_interface: str | None = Field(None, description="Parent interface (vlan_interface only)")
    ipv4: str | None = Field(None, description="Interface address with prefix, e.g. 10.20.0.1/24 (svi / vlan_interface)")

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        if v is not None and not NAME_RE.match(v):
            raise ValueError("name may contain letters, digits, '-', '_' and '.' only (max 63)")
        return v

    @field_validator("parent_interface")
    @classmethod
    def _p(cls, v):
        if v is not None and not IFACE_RE.match(v):
            raise ValueError("invalid interface name")
        return v

    @field_validator("ipv4")
    @classmethod
    def _i(cls, v):
        return _cidr_if(v) if v else v

    @model_validator(mode="after")
    def _deps(self):
        if self.kind == "vlan_interface" and not self.parent_interface:
            raise ValueError("parent_interface is required for kind=vlan_interface")
        if self.kind == "vlan_database" and (self.ipv4 or self.parent_interface):
            raise ValueError("ipv4 / parent_interface do not apply to a VLAN database entry")
        if self.action == "absent" and self.ipv4:
            raise ValueError("ipv4 cannot be given when removing")
        return self


class InterfaceSpec(_Base):
    module: Literal["interface"] = "interface"
    name: str = Field(description="Interface name exactly as on the device, e.g. GigabitEthernet1/0/5, ether3, port2")
    description: str | None = None
    enabled: bool | None = Field(None, description="Administrative state (false = shutdown)")
    mode: Literal["access", "trunk", "routed"] | None = Field(None, description="Switchport mode (Cisco)")
    access_vlan: int | None = Field(None, ge=1, le=4094)
    native_vlan: int | None = Field(None, ge=1, le=4094)
    allowed_vlans: str | None = Field(None, description="Trunk allowed VLANs, e.g. 10,20,30-40")
    ipv4: str | None = Field(None, description="Address with prefix, e.g. 10.0.0.1/30")
    mtu: int | None = Field(None, ge=576, le=9216)

    @field_validator("name")
    @classmethod
    def _n(cls, v):
        if not IFACE_RE.match(v):
            raise ValueError("invalid interface name")
        return v

    @field_validator("description")
    @classmethod
    def _d(cls, v):
        if v is not None and not TEXT_RE.match(v):
            raise ValueError("description: letters, digits, spaces and _ . , / : @ # ( ) + = - only (max 80)")
        return v

    @field_validator("ipv4")
    @classmethod
    def _i(cls, v):
        return _cidr_if(v) if v else v

    @field_validator("allowed_vlans")
    @classmethod
    def _a(cls, v):
        if v is None:
            return v
        for part in v.split(","):
            m = re.fullmatch(r"(\d{1,4})(?:-(\d{1,4}))?", part.strip())
            if not m:
                raise ValueError("allowed_vlans must look like 10,20,30-40")
            lo, hi = int(m[1]), int(m[2] or m[1])
            if not (1 <= lo <= hi <= 4094):
                raise ValueError("VLAN ids must be 1-4094 and ranges ascending")
        return v.replace(" ", "")

    @model_validator(mode="after")
    def _deps(self):
        if self.mode == "access" and not self.access_vlan:
            raise ValueError("access_vlan is required when mode=access")
        if self.mode != "access" and self.access_vlan:
            raise ValueError("access_vlan only applies to mode=access")
        if self.mode != "trunk" and (self.allowed_vlans or self.native_vlan):
            raise ValueError("allowed_vlans / native_vlan only apply to mode=trunk")
        if self.mode in ("access", "trunk") and self.ipv4:
            raise ValueError("an IP address cannot be set on a switchport; use mode=routed")
        if not any([self.description is not None, self.enabled is not None, self.mode, self.ipv4, self.mtu]):
            raise ValueError("nothing to change: set at least one field")
        return self


class StaticRouteSpec(_Base):
    module: Literal["static_route"] = "static_route"
    action: Literal["present", "absent"] = "present"
    destination: str = Field(description="Network, e.g. 10.10.0.0/16 (use 0.0.0.0/0 for the default route)")
    next_hop: str
    distance: int | None = Field(None, ge=1, le=255)
    device: str | None = Field(None, description="Egress interface (required on FortiGate)")

    @field_validator("destination")
    @classmethod
    def _d(cls, v):
        return _cidr_net(v)

    @field_validator("next_hop")
    @classmethod
    def _n(cls, v):
        return _ip(v, 4)

    @field_validator("device")
    @classmethod
    def _dev(cls, v):
        if v is not None and not IFACE_RE.match(v):
            raise ValueError("invalid interface name")
        return v


class NtpSpec(_Base):
    module: Literal["ntp"] = "ntp"
    action: Literal["present", "absent"] = "present"
    servers: list[str] = Field(min_length=1, max_length=4)

    @field_validator("servers")
    @classmethod
    def _s(cls, v):
        out = []
        for s in v:
            if not HOST_RE.match(s):
                raise ValueError(f"'{s}' is not a valid host name or IP")
            out.append(s)
        return out


class DnsSpec(_Base):
    module: Literal["dns"] = "dns"
    action: Literal["present", "absent"] = "present"
    servers: list[str] = Field(min_length=1, max_length=3)

    @field_validator("servers")
    @classmethod
    def _s(cls, v):
        return [_ip(x, 4) for x in v]


class SyslogSpec(_Base):
    module: Literal["syslog"] = "syslog"
    action: Literal["present", "absent"] = "present"
    servers: list[str] = Field(min_length=1, max_length=4)

    @field_validator("servers")
    @classmethod
    def _s(cls, v):
        return [_ip(x, 4) for x in v]


class SnmpSpec(_Base):
    module: Literal["snmp"] = "snmp"
    version: Literal["v2c"] = "v2c"
    community: str | None = Field(None, description="Read-only community (secret)")
    allowed_hosts: list[str] = Field(default_factory=list, description="Networks allowed to poll, e.g. 10.0.0.0/24")
    location: str | None = None
    contact: str | None = None

    @field_validator("community")
    @classmethod
    def _c(cls, v):
        if v is not None:
            if not SECRET_RE.match(v):
                raise ValueError("community: letters, digits and _ . @ # % + = ! - only")
            if v.lower() in ("public", "private"):
                raise ValueError("'public'/'private' are well-known defaults and are refused")
        return v

    @field_validator("allowed_hosts")
    @classmethod
    def _h(cls, v):
        return [_cidr_net(x) for x in v]

    @field_validator("location", "contact")
    @classmethod
    def _t(cls, v):
        if v is not None and not TEXT_RE.match(v):
            raise ValueError("letters, digits, spaces and _ . , / : @ # ( ) + = - only (max 80)")
        return v

    @model_validator(mode="after")
    def _deps(self):
        if not any([self.community, self.location, self.contact]):
            raise ValueError("nothing to change: set community, location or contact")
        if self.allowed_hosts and not self.community:
            raise ValueError("allowed_hosts needs a community")
        return self


class SystemSpec(_Base):
    module: Literal["system"] = "system"
    action: Literal["save", "reboot"]
    confirm_name: str | None = Field(None, description="Type the device name to confirm a reboot")


Spec = Annotated[
    HostnameSpec | VlanSpec | InterfaceSpec | StaticRouteSpec | NtpSpec | DnsSpec | SyslogSpec | SnmpSpec | SystemSpec,
    Field(discriminator="module"),
]
_adapter = TypeAdapter(Spec)

MODULE_MODELS = {
    m.model_fields["module"].default: m
    for m in (HostnameSpec, VlanSpec, InterfaceSpec, StaticRouteSpec, NtpSpec, DnsSpec, SyslogSpec, SnmpSpec, SystemSpec)
}

MODULE_TITLES = {
    "hostname": "Hostname", "vlan": "VLANs", "interface": "Interfaces", "static_route": "Static routes",
    "ntp": "NTP", "dns": "DNS", "syslog": "Syslog", "snmp": "SNMP v2c", "system": "System (save / reboot)",
}


def parse_spec(data: dict):
    return _adapter.validate_python(data)
