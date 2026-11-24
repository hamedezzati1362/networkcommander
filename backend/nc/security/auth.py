"""Local access control.

A random access token is created per data dir. The launcher opens
/auth/bootstrap?token=... which sets an HttpOnly session cookie plus a CSRF cookie.
Every API call needs the session cookie; mutating calls also need the matching
X-NC-CSRF header (double-submit) and an allowed Origin/Host.
"""
from __future__ import annotations

import hmac
import secrets
from pathlib import Path

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

SESSION_COOKIE = "nc_session"
CSRF_COOKIE = "nc_csrf"
OPEN_PATHS = ("/auth/bootstrap", "/api/v1/health")


def load_or_create_token(data_dir: Path) -> str:
    p = data_dir / "access.token"
    if p.exists():
        return p.read_text().strip()
    tok = secrets.token_urlsafe(32)
    p.write_text(tok)
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return tok


def session_value(token: str) -> str:
    return hmac.new(token.encode(), b"nc-session-v1", "sha256").hexdigest()


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, token: str, allowed_hosts: set[str]):
        super().__init__(app)
        self.token, self.allowed_hosts = token, allowed_hosts

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        host = (request.headers.get("host") or "").split(":")[0].lower()
        if self.allowed_hosts and host not in self.allowed_hosts:  # DNS-rebinding defence
            return JSONResponse({"code": "bad_host", "message": "Host not allowed"}, 403)
        if path.startswith("/api/") and path not in OPEN_PATHS:
            cookie = request.cookies.get(SESSION_COOKIE, "")
            if not hmac.compare_digest(cookie, session_value(self.token)):
                return JSONResponse({"code": "unauthenticated", "message": "Open the app from the launcher link."}, 401)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                hdr, ck = request.headers.get("x-nc-csrf", ""), request.cookies.get(CSRF_COOKIE, "")
                if not hdr or not hmac.compare_digest(hdr, ck):
                    return JSONResponse({"code": "csrf", "message": "CSRF check failed"}, 403)
                origin = request.headers.get("origin")
                if origin and origin.split("://", 1)[-1].split(":")[0].lower() not in self.allowed_hosts | {host}:
                    return JSONResponse({"code": "bad_origin", "message": "Origin not allowed"}, 403)
        return await call_next(request)
