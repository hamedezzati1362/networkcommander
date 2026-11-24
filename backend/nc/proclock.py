"""Cross-process exclusive file lock (Windows: msvcrt, others: fcntl)."""
from __future__ import annotations

import os
import time


def try_lock(path):
    """Return an open locked file object, or None if another process holds the lock."""
    f = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            f.seek(0)
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        f.close()
        return None
    return f


def lock_wait(path, timeout: float = 60.0):
    end = time.time() + timeout
    while True:
        f = try_lock(path)
        if f is not None:
            return f
        if time.time() > end:
            raise TimeoutError(f"could not get lock {path}")
        time.sleep(0.2)
