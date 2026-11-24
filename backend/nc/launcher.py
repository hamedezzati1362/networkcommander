"""Windows launcher entry point (PyInstaller target).

  NetworkCommander.exe            start (or just open the browser if already running)
  NetworkCommander.exe --stop     stop the running service
  NetworkCommander.exe --no-browser
"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser

import httpx

from nc.config import load_settings


def _running(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _frontend_dir():
    base = getattr(sys, "_MEIPASS", None)
    from pathlib import Path
    return Path(base) / "frontend" if base else None


def main() -> int:
    s = load_settings()
    if s.frontend_dir is None:
        s.frontend_dir = _frontend_dir()
    token_file = s.data_dir / "access.token"
    if "--stop" in sys.argv:
        if not _running(s.host, s.port) or not token_file.exists():
            print("Network Commander is not running.")
            return 0
        with httpx.Client(base_url=f"http://{s.host}:{s.port}") as c:
            c.get("/auth/bootstrap", params={"token": token_file.read_text().strip()}, follow_redirects=False)
            c.post("/api/v1/system/shutdown", headers={"X-NC-CSRF": c.cookies.get("nc_csrf", "")})
        print("Stopped.")
        return 0
    if _running(s.host, s.port):
        if "--no-browser" not in sys.argv and token_file.exists():
            webbrowser.open(f"http://{s.host}:{s.port}/auth/bootstrap?token={token_file.read_text().strip()}")
        return 0
    import uvicorn

    from nc.main import create_app
    app = create_app(s)
    if "--no-browser" not in sys.argv:
        url = f"http://{s.host}:{s.port}/auth/bootstrap?token={app.state.token}"
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=s.host, port=s.port, log_level="warning", log_config=None)
    return 0


if __name__ == "__main__":
    sys.exit(main())
