"""Small file helpers the hosted layer shares: atomic JSON writes and a lock.

The accounts file is written by two processes, the server and the admin CLI,
so every read-modify-write takes an exclusive `flock` on a sidecar lock file
and every write goes through a temporary file and `os.replace`. A reader never
sees half a file, and two writers never interleave.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterator

try:  # POSIX only; the hosted layer runs in a Linux container.
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


def atomic_write_json(path: Path, data: Any, *, mode: int = 0o600) -> None:
    """Write `data` as JSON to `path` so a reader sees the old file or the new
    one, never a torn one. The file is created owner-readable only: the
    accounts file holds key hashes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise
    with contextlib.suppress(OSError):  # make the rename itself durable
        dfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)


def read_json(path: Path, default: Any) -> Any:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


@contextlib.contextmanager
def locked(path: Path) -> Iterator[None]:
    """An exclusive advisory lock on `<path>.lock`, held for the block."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    with open(lock_path, "a+") as f:
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
