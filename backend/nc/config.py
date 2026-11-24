"""Runtime settings and the local data directory."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

APP_NAME = "NetworkCommander"


def default_data_dir() -> Path:
    env = os.environ.get("NC_DATA_DIR")
    if env:
        p = Path(env)
    elif os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        p = Path(base) / APP_NAME
    else:
        p = Path.home() / ".network-commander"
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Settings:
    data_dir: Path = field(default_factory=default_data_dir)
    host: str = "127.0.0.1"
    port: int = 8765
    # Simulated devices are only allowed when explicitly enabled (labelled SIMULATION in the UI).
    enable_simulator: bool = False
    # Background monitoring thread (tests turn it off).
    start_scheduler: bool = True
    frontend_dir: Path | None = None

    @property
    def db_url(self) -> str:
        return f"sqlite:///{(self.data_dir / 'networkcommander.db').as_posix()}"

    @property
    def log_dir(self) -> Path:
        p = self.data_dir / "logs"
        p.mkdir(parents=True, exist_ok=True)
        return p


def load_settings() -> Settings:
    s = Settings()
    s.host = os.environ.get("NC_HOST", s.host)
    s.port = int(os.environ.get("NC_PORT", s.port))
    s.enable_simulator = os.environ.get("NC_ENABLE_SIMULATOR") == "1"
    fe = os.environ.get("NC_FRONTEND_DIR")
    if fe:
        s.frontend_dir = Path(fe)
    return s
