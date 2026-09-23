"""Where sessions live between calls: the SessionStore protocol and two stores.

The service (`tradefloor.serve.core`) commits once per mutating call. A commit
is one RECORD (the session's whole live state, replaced each time) plus
entries appended to named STREAMS (history that only grows: fills, finished
orders, the call log). Splitting the two keeps a commit small on a long
session: the record is the size of the market, not the size of its history.

## What a store must do

- `commit(session_id, record, appends)`: append each stream's entries, then
  replace the record, so that a reader sees either the whole call or none of
  it. `record["seq"]` is an integer that grows by at least one per commit, and
  `record["head"]` is a small dict (it carries `"owner"`) kept for listing.
- `load(session_id)`: the last committed record, or None.
- `read_stream(session_id, name)`: that stream's committed entries, in order.
  Entries appended by a commit that never completed are NOT returned, and the
  next commit overwrites them.
- `version(session_id)`: the committed `seq`, or None. Cheap: the service
  calls it on every request to notice a record written by another instance.
- `heads(owner)`: the `head` of every session that owner has.

Values are JSON-like (dict, list, str, int, float, bool, None) plus `bytes`.
Every float must come back BIT FOR BIT, including NaN payloads, infinities
and -0.0: the engine's snapshot carries its generator position as f64s whose
bit patterns are sometimes NaN, and a store that let `json` turn them into
plain NaN would resume a different market. `encode`/`decode` below do this
for any store that writes JSON; both stores here use them, and an S3 or
database store should too.

## Crash safety of FileStore

Layout, one directory per session:

    <root>/<session_id>/head.json          seq, head, committed stream sizes
    <root>/<session_id>/record-<seq>.json  the record at that seq
    <root>/<session_id>/<stream>.jsonl     one entry per line

A commit truncates each stream to its committed size, appends, writes
`record-<seq>.json`, and then replaces `head.json` (temp file + rename). That
rename is the commit point. A process killed anywhere before it leaves the
previous commit intact and readable; killed after it, the new one. Old record
files are removed after the rename. `fsync=True` also flushes to the device
(slower; guards against power loss, which a killed process does not need).

One process writes a given root at a time. Two live processes committing the
same session concurrently is not supported in 0.1 (no cross-process lock).
"""

from __future__ import annotations

import base64
import json
import math
import os
import re
import struct
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

__all__ = ["SessionStore", "FileStore", "MemoryStore", "encode", "decode",
           "default_root"]

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STREAM = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_F64 = "$f64"
_BYTES = "$bytes"


def default_root() -> Path:
    """`~/.tradefloor/sessions`, the self-run default."""
    return Path.home() / ".tradefloor" / "sessions"


# -- the codec -----------------------------------------------------------------

def encode(value: Any) -> Any:
    """A JSON-safe copy of `value` that `decode` turns back bit for bit.

    Finite floats pass through (Python's `json` writes the shortest repr,
    which round-trips exactly, -0.0 included). Non-finite floats become
    `{"$f64": "<16 hex digits>"}` so a NaN keeps its payload, and bytes
    become `{"$bytes": "<base64>"}`.
    """
    if isinstance(value, float):
        if math.isfinite(value):
            return value
        return {_F64: struct.pack("<d", value).hex()}
    if isinstance(value, (bytes, bytearray)):
        return {_BYTES: base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(v) for v in value]
    return value


def decode(value: Any) -> Any:
    """The inverse of `encode`."""
    if isinstance(value, dict):
        if len(value) == 1:
            if _F64 in value:
                return struct.unpack("<d", bytes.fromhex(value[_F64]))[0]
            if _BYTES in value:
                return base64.b64decode(value[_BYTES])
        return {k: decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [decode(v) for v in value]
    return value


def _dumps(value: Any) -> str:
    return json.dumps(encode(value), separators=(",", ":"), allow_nan=False)


def _loads(text: str | bytes) -> Any:
    return decode(json.loads(text))


def _check_id(session_id: str) -> str:
    if not isinstance(session_id, str) or not _ID.match(session_id):
        raise KeyError(session_id)
    return session_id


def _check_stream(name: str) -> str:
    if not isinstance(name, str) or not _STREAM.match(name):
        raise ValueError(f"bad stream name {name!r}")
    return name


# -- the protocol ----------------------------------------------------------------

@runtime_checkable
class SessionStore(Protocol):
    """Persistence for `LocalSessionService`. See the module docstring."""

    def commit(self, session_id: str, record: dict[str, Any],
               appends: Mapping[str, Sequence[dict[str, Any]]]) -> None: ...
    def load(self, session_id: str) -> dict[str, Any] | None: ...
    def read_stream(self, session_id: str, name: str) -> list[dict[str, Any]]: ...
    def version(self, session_id: str) -> int | None: ...
    def heads(self, owner: str) -> list[dict[str, Any]]: ...


# -- in memory -------------------------------------------------------------------

class MemoryStore:
    """A store in this process's memory, for tests and throwaway sessions.

    Everything is serialised on the way in and parsed on the way out, the
    same bytes FileStore writes, so a session that survives MemoryStore
    survives FileStore, and nothing a caller holds aliases what is stored.
    """

    def __init__(self) -> None:
        self._records: dict[str, tuple[int, str, dict[str, Any]]] = {}
        self._streams: dict[str, dict[str, list[str]]] = {}
        self._lock = threading.Lock()

    def commit(self, session_id: str, record: dict[str, Any],
               appends: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        _check_id(session_id)
        text = _dumps(record)
        lines = {_check_stream(n): [_dumps(e) for e in entries]
                 for n, entries in appends.items()}
        with self._lock:
            streams = self._streams.setdefault(session_id, {})
            for name, new in lines.items():
                streams.setdefault(name, []).extend(new)
            self._records[session_id] = (int(record["seq"]), text,
                                         json.loads(_dumps(record["head"])))

    def load(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            got = self._records.get(session_id)
        return None if got is None else _loads(got[1])

    def read_stream(self, session_id: str, name: str) -> list[dict[str, Any]]:
        with self._lock:
            lines = list(self._streams.get(session_id, {}).get(name, []))
        return [_loads(line) for line in lines]

    def version(self, session_id: str) -> int | None:
        with self._lock:
            got = self._records.get(session_id)
        return None if got is None else got[0]

    def heads(self, owner: str) -> list[dict[str, Any]]:
        with self._lock:
            heads = [decode(h) for _, _, h in self._records.values()]
        return [h for h in heads if h.get("owner") == owner]


# -- on disk -----------------------------------------------------------------------

class FileStore:
    """Sessions as files under `root`. See the module docstring for the layout."""

    def __init__(self, root: str | os.PathLike[str] | None = None, *,
                 fsync: bool = False) -> None:
        self.root = Path(root) if root is not None else default_root()
        self.root.mkdir(parents=True, exist_ok=True)
        self.fsync = bool(fsync)

    # -- paths and small helpers --

    def _dir(self, session_id: str) -> Path:
        return self.root / _check_id(session_id)

    def _write_atomic(self, path: Path, text: str) -> None:
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.",
                                   suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
                fh.flush()
                if self.fsync:
                    os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        if self.fsync:
            self._fsync_dir(path.parent)

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        try:
            fd = os.open(path, os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _head(self, session_id: str) -> dict[str, Any] | None:
        try:
            path = self._dir(session_id) / "head.json"
        except KeyError:
            return None
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return json.loads(text)

    # -- the protocol --

    def commit(self, session_id: str, record: dict[str, Any],
               appends: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        d = self._dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        seq = int(record["seq"])
        prev = self._head(session_id) or {"seq": None, "streams": {}}
        sizes: dict[str, list[int]] = {k: list(v) for k, v in prev["streams"].items()}

        for name, entries in appends.items():
            _check_stream(name)
            count, size = sizes.get(name, [0, 0])
            path = d / f"{name}.jsonl"
            mode = "r+b" if path.exists() else "w+b"
            with open(path, mode) as fh:
                # Anything past the committed size belongs to a commit that
                # never reached its rename. Drop it before appending.
                fh.truncate(size)
                fh.seek(size)
                for entry in entries:
                    fh.write(_dumps(entry).encode("utf-8"))
                    fh.write(b"\n")
                fh.flush()
                if self.fsync:
                    os.fsync(fh.fileno())
                sizes[name] = [count + len(entries), fh.tell()]

        self._write_atomic(d / f"record-{seq}.json", _dumps(record))
        head = {"seq": seq, "head": encode(record["head"]), "streams": sizes}
        self._write_atomic(d / "head.json",
                           json.dumps(head, separators=(",", ":"), allow_nan=False))
        # The commit is done; older records are garbage now.
        for old in d.glob("record-*.json"):
            if old.name != f"record-{seq}.json":
                try:
                    old.unlink()
                except OSError:
                    pass

    def load(self, session_id: str) -> dict[str, Any] | None:
        head = self._head(session_id)
        if head is None:
            return None
        path = self._dir(session_id) / f"record-{head['seq']}.json"
        return _loads(path.read_text(encoding="utf-8"))

    def read_stream(self, session_id: str, name: str) -> list[dict[str, Any]]:
        _check_stream(name)
        head = self._head(session_id)
        if head is None or name not in head["streams"]:
            return []
        count, size = head["streams"][name]
        path = self._dir(session_id) / f"{name}.jsonl"
        with open(path, "rb") as fh:
            data = fh.read(size)
        lines = data.split(b"\n")
        out = [_loads(line) for line in lines[:count] if line]
        return out

    def version(self, session_id: str) -> int | None:
        head = self._head(session_id)
        return None if head is None else int(head["seq"])

    def heads(self, owner: str) -> list[dict[str, Any]]:
        out = []
        for d in sorted(self.root.iterdir()):
            if not d.is_dir() or not _ID.match(d.name):
                continue
            head = self._head(d.name)
            if head is None:
                continue
            h = decode(head["head"])
            if h.get("owner") == owner:
                out.append(h)
        return out
