"""Small POSIX advisory lock helper for shared MPAS-Workflow caches."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import fcntl


@contextmanager
def file_lock(path: str | Path):
    """Hold an exclusive lock until the context exits.

    The lock file itself is persistent and harmless; the kernel lock is released
    automatically if a task exits unexpectedly, avoiding stale mkdir-locks.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a+") as fh:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        try:
            yield p
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
