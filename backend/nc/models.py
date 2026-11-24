"""Database models (SQLite via SQLAlchemy). Schema changes go through Alembic."""
from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class Credential(Base):
    __tablename__ = "credentials"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    username: Mapped[str] = mapped_column(String(120))
    secret_blob: Mapped[bytes] = mapped_column(LargeBinary)  # protected JSON: password / private_key / enable
    has_key: Mapped[bool] = mapped_column(Boolean, default=False)
    has_enable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Device(Base):
    __tablename__ = "devices"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    vendor: Mapped[str] = mapped_column(String(20))  # cisco | mikrotik | fortinet
    transport: Mapped[str] = mapped_column(String(20), default="ssh")  # ssh | simulator
    credential_id: Mapped[int | None] = mapped_column(ForeignKey("credentials.id"), nullable=True)
    group: Mapped[str | None] = mapped_column(String(80), nullable=True)
    location: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    # filled by identification (never invented)
    os_family: Mapped[str | None] = mapped_column(String(30), nullable=True)
    os_version: Mapped[str | None] = mapped_column(String(60), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    serial: Mapped[str | None] = mapped_column(String(80), nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(120), nullable=True)
    uptime: Mapped[str | None] = mapped_column(String(80), nullable=True)
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)  # switch|router|firewall|other (heuristic)
    capabilities: Mapped[dict] = mapped_column(JSON, default=dict)
    iface_ips: Mapped[dict] = mapped_column(JSON, default=dict)  # interface -> ip, to detect the management interface
    status: Mapped[str] = mapped_column(String(20), default="unknown")  # unknown|online|offline|auth_failed|error
    status_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_identified: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    host_key_fp: Mapped[str | None] = mapped_column(String(120), nullable=True)
    host_key_trusted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Backup(Base):
    __tablename__ = "backups"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # running | startup
    reason: Mapped[str] = mapped_column(String(40), default="manual")  # manual | pre-change | scheduled
    content_blob: Mapped[bytes] = mapped_column(LargeBinary)  # encrypted at rest
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(Integer)
    change_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)


class ChangeSet(Base):
    __tablename__ = "change_sets"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    module: Mapped[str] = mapped_column(String(30))
    spec: Mapped[dict] = mapped_column(JSON)
    plan: Mapped[dict] = mapped_column(JSON)
    risk: Mapped[str] = mapped_column(String(10))  # low|medium|high|critical
    risk_reasons: Mapped[list] = mapped_column(JSON, default=list)
    current_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    already_compliant: Mapped[bool] = mapped_column(Boolean, default=False)
    secrets_blob: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)  # protected: secret values removed from spec/plan
    status: Mapped[str] = mapped_column(String(20), default="planned")
    # planned|applied|failed|rolled_back|skipped|unknown_state|cancelled
    report: Mapped[list] = mapped_column(JSON, default=list)
    after_state: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))  # bulk_apply | bulk_backup | bulk_identify
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed|cancelled
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    items: Mapped[list] = mapped_column(JSON, default=list)  # per-device result rows
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    actor: Mapped[str] = mapped_column(String(60), default="local-user")
    action: Mapped[str] = mapped_column(String(60), index=True)
    target: Mapped[str | None] = mapped_column(String(160), nullable=True)
    result: Mapped[str] = mapped_column(String(20), default="ok")
    detail: Mapped[dict] = mapped_column(JSON, default=dict)


class Sample(Base):
    __tablename__ = "samples"
    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    metric: Mapped[str] = mapped_column(String(30))  # up | cpu | mem
    value: Mapped[float] = mapped_column(Float)


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, index=True)
    device_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    severity: Mapped[str] = mapped_column(String(10))  # info|warning|critical
    kind: Mapped[str] = mapped_column(String(30))
    message: Mapped[str] = mapped_column(Text)


class Link(Base):
    __tablename__ = "links"
    id: Mapped[int] = mapped_column(primary_key=True)
    local_device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    local_if: Mapped[str] = mapped_column(String(80))
    remote_name: Mapped[str] = mapped_column(String(160))
    remote_if: Mapped[str | None] = mapped_column(String(80), nullable=True)
    remote_device_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(10))  # lldp | cdp | manual
    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Template(Base):
    __tablename__ = "templates"
    __table_args__ = (UniqueConstraint("name", "version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80))
    version: Mapped[int] = mapped_column(Integer, default=1)
    module: Mapped[str] = mapped_column(String(30))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec: Mapped[dict] = mapped_column(JSON)  # string values may contain {{variable}}
    variables: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)
