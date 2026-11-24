"""Application factory + launcher entry point."""
from __future__ import annotations

import logging
import logging.handlers
import secrets
import threading
import webbrowser
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from nc import __version__
from nc.api.routes import router
from nc.config import Settings, load_settings
from nc.core.errors import NCError
from nc.db import make_engine, make_sessionmaker, migrate
from nc.security.auth import CSRF_COOKIE, SESSION_COOKIE, AuthMiddleware, load_or_create_token, session_value
from nc.security.secretbox import default_box
from nc.services.monitoring import Scheduler
from nc.transport.factory import make_transport, set_simulator_factory


def setup_logging(settings: Settings) -> None:
    h = logging.handlers.RotatingFileHandler(settings.log_dir / "networkcommander.log", maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if not any(isinstance(x, logging.handlers.RotatingFileHandler) for x in root.handlers):
        root.addHandler(h)
    # Never log credentials: paramiko/netmiko debug stays off.
    for n in ("paramiko", "netmiko"):
        logging.getLogger(n).setLevel(logging.WARNING)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    setup_logging(settings)
    migrate(settings.db_url)
    engine = make_engine(settings.db_url)
    app = FastAPI(title="Network Commander", version=__version__, docs_url="/api/docs", openapi_url="/api/openapi.json")
    st = app.state
    st.settings, st.engine, st.sm = settings, engine, make_sessionmaker(engine)
    st.box = default_box(settings.data_dir)
    st.token = load_or_create_token(settings.data_dir)

    def tf_for_db(db):
        return lambda device: make_transport(db, st.box, device, settings.enable_simulator)
    st.tf_for_db = tf_for_db

    if settings.enable_simulator:
        from nc.simulator import SimCisco, SimFortinet, SimMikrotik
        sims: dict = {}
        st.sims = sims

        def sim_factory(device):
            if device.host not in sims:
                if device.vendor == "cisco":
                    sims[device.host] = SimCisco(kind="router" if "sim:router" in (device.tags or []) else "switch", name=device.name, host=device.host)
                elif device.vendor == "mikrotik":
                    sims[device.host] = SimMikrotik(name=device.name, host=device.host)
                else:
                    sims[device.host] = SimFortinet(name=device.name, host=device.host)
            return sims[device.host]
        set_simulator_factory(sim_factory)

    allowed = {"127.0.0.1", "localhost", "[::1]", "testserver"} if settings.host in ("127.0.0.1", "localhost") else None
    app.add_middleware(AuthMiddleware, token=st.token, allowed_hosts=allowed or set())

    @app.exception_handler(NCError)
    async def _nc(_: Request, e: NCError):
        return JSONResponse(e.to_dict(), status_code=e.status)

    @app.exception_handler(Exception)
    async def _any(_: Request, e: Exception):
        logging.getLogger("nc").exception("unhandled error")
        return JSONResponse({"code": "internal_error", "message": "Unexpected server error (details in the log file)."}, status_code=500)

    @app.get("/auth/bootstrap")
    def bootstrap(token: str):
        if not secrets.compare_digest(token, st.token):
            return JSONResponse({"code": "bad_token", "message": "Wrong access token"}, status_code=403)
        r = RedirectResponse("/", status_code=303)
        r.set_cookie(SESSION_COOKIE, session_value(st.token), httponly=True, samesite="strict", max_age=60 * 60 * 24 * 30)
        r.set_cookie(CSRF_COOKIE, secrets.token_urlsafe(24), httponly=False, samesite="strict", max_age=60 * 60 * 24 * 30)
        return r

    app.include_router(router)

    fe = settings.frontend_dir
    if fe and (fe / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=fe / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            f = (fe / path).resolve()
            if path and f.is_file() and fe.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(fe / "index.html")

    if settings.start_scheduler:
        sch = Scheduler(st.sm, tf_for_db)
        st.scheduler = sch

        @app.on_event("startup")
        def _start():
            sch.start()

        @app.on_event("shutdown")
        def _stop():
            sch.stop()
    return app


def run() -> None:
    """Launcher: start server on localhost and open the browser with the one-time bootstrap link."""
    import uvicorn
    settings = load_settings()
    if settings.host not in ("127.0.0.1", "localhost"):
        print("WARNING: listening on a non-local address. Traffic is NOT encrypted (no TLS). Use only on a trusted LAN.")
    app = create_app(settings)
    url = f"http://{settings.host}:{settings.port}/auth/bootstrap?token={app.state.token}"
    threading.Timer(1.5, lambda: webbrowser.open(url)).start()
    print(f"Network Commander {__version__} running on http://{settings.host}:{settings.port}  (data: {settings.data_dir})")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    run()
