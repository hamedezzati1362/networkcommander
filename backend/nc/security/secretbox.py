"""Protect secrets at rest.

Windows: DPAPI (CryptProtectData), bound to the Windows user account.
Other OS (development/tests only): Fernet with a key file (mode 0600) in the data dir.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from cryptography.fernet import Fernet


class SecretBox(Protocol):
    name: str

    def protect(self, data: bytes) -> bytes: ...
    def unprotect(self, blob: bytes) -> bytes: ...


class FernetBox:
    name = "fernet-keyfile"

    def __init__(self, key_path: Path):
        if key_path.exists():
            key = key_path.read_bytes()
        else:
            key = Fernet.generate_key()
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        self._f = Fernet(key)

    def protect(self, data: bytes) -> bytes:
        return self._f.encrypt(data)

    def unprotect(self, blob: bytes) -> bytes:
        return self._f.decrypt(blob)


class DpapiBox:
    """Windows Data Protection API through ctypes (no extra dependency)."""

    name = "windows-dpapi"

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

        self._ct, self._blob = ctypes, DATA_BLOB
        self._crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        self._kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    def _call(self, fn, data: bytes) -> bytes:
        ct = self._ct
        buf = ct.create_string_buffer(data, len(data))
        inb = self._blob(len(data), ct.cast(buf, ct.POINTER(ct.c_char)))
        out = self._blob()
        # flags=1: CRYPTPROTECT_UI_FORBIDDEN
        if not fn(ct.byref(inb), None, None, None, None, 1, ct.byref(out)):
            raise OSError("DPAPI call failed")
        try:
            return ct.string_at(out.pbData, out.cbData)
        finally:
            self._kernel32.LocalFree(out.pbData)

    def protect(self, data: bytes) -> bytes:
        return self._call(self._crypt32.CryptProtectData, data)

    def unprotect(self, blob: bytes) -> bytes:
        ct = self._ct
        buf = ct.create_string_buffer(blob, len(blob))
        inb = self._blob(len(blob), ct.cast(buf, ct.POINTER(ct.c_char)))
        out = self._blob()
        if not self._crypt32.CryptUnprotectData(ct.byref(inb), None, None, None, None, 1, ct.byref(out)):
            raise OSError("DPAPI unprotect failed (different Windows user?)")
        try:
            return ct.string_at(out.pbData, out.cbData)
        finally:
            self._kernel32.LocalFree(out.pbData)


def default_box(data_dir: Path) -> SecretBox:
    if os.name == "nt":
        return DpapiBox()
    return FernetBox(data_dir / "dev-secret.key")
