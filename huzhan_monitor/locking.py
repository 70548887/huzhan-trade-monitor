"""Cross-platform non-blocking locks for collectors and scheduler leadership."""
from __future__ import annotations

import errno
import os
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def file_lock(path: Path):
    """Yield whether acquired; only the acquiring thread releases the lock.

    File preparation is outside the contention handler, so permission and path
    errors are surfaced instead of being mistaken for an occupied lock.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        if os.name == "nt":
            import msvcrt

            def acquire():
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

            def release():
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            def acquire():
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            def release():
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            # msvcrt reports an occupied byte range as EACCES; flock may use
            # EACCES/EAGAIN. Other lock errors remain errors for the caller.
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                raise
            yield False
            return
        try:
            yield True
        finally:
            release()
