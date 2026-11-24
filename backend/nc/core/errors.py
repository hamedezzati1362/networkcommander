"""One error type with a stable machine code and a human message."""
from __future__ import annotations


class NCError(Exception):
    def __init__(self, code: str, message: str, status: int = 400, hint: str | None = None):
        super().__init__(message)
        self.code, self.message, self.status, self.hint = code, message, status, hint

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


class Unsupported(NCError):
    """The vendor/OS/version cannot do this (documented limitation)."""

    def __init__(self, message: str, hint: str | None = None):
        super().__init__("unsupported", message, 422, hint)


class TransportError(NCError):
    def __init__(self, message: str, code: str = "connection_failed", hint: str | None = None):
        super().__init__(code, message, 502, hint)
