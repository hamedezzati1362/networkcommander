from __future__ import annotations

from pathlib import Path

import time

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic"


def make_engine(url: str):
    engine = create_engine(url, connect_args={"check_same_thread": False}, pool_pre_ping=True)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.close()

    return engine


def migrate(url: str) -> None:
    """Run Alembic migrations to head (creates the DB on first start)."""
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    from sqlalchemy.engine import make_url

    from nc.proclock import lock_wait
    dbfile = make_url(url).database
    lock = lock_wait(str(dbfile) + ".migrate.lock") if dbfile else None
    try:
        _upgrade(cfg)
    finally:
        if lock:
            lock.close()


def _upgrade(cfg) -> None:
    # Two starts at the same moment can race on creating alembic_version; the loser simply retries
    # (the winner's tables are then already there and the upgrade is a no-op).
    for attempt in range(5):
        try:
            command.upgrade(cfg, "head")
            return
        except OperationalError as e:
            if "already exists" in str(e) and attempt < 4:
                time.sleep(0.4 * (attempt + 1))
                continue
            raise


def make_sessionmaker(engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
