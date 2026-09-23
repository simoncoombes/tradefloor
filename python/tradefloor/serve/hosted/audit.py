"""The audit log: one JSON line per mutating call, append-only.

Every call that changes state (open, place_order, cancel_order, advance, fork,
close), every expiry the server performs on its own, and every refusal of such
a call (unauthorised, rate limited, over quota, refused by the core) gets a
line. Reads are metered but not audited, unless `audit_reads` is on.

A line:

    {"ts": "2026-09-23T14:02:11.482Z", "owner": "acme", "key_id": "3f9c0a1b2d4e",
     "call": "advance", "session_id": "9b1e...", "outcome": "ok",
     "duration_ms": 41.7, "detail": {"steps": 13, "until": "steps", "sim_ticks": 390}}

`outcome` is "ok" or the ServeError code; `error` carries the message on a
refusal. Files roll daily (`audit-YYYY-MM-DD.jsonl`, UTC) and are opened with
O_APPEND, so lines from concurrent writers never interleave mid-line. Setting
`stream` (the server passes stdout) copies each line there as well, which is
how it reaches CloudWatch in the AWS path; the files remain the record.
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any, Callable, Iterator

MUTATING_CALLS = ("open", "place_order", "cancel_order", "advance", "fork", "close")


class AuditLog:
    def __init__(self, directory: str | Path, *, clock: Callable[[], float] = time.time,
                 stream: IO[str] | None = None, fsync: bool = False) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.stream = stream
        self.fsync = fsync
        self._lock = threading.Lock()

    def _path_for(self, t: float) -> Path:
        return self.dir / f"audit-{datetime.fromtimestamp(t, timezone.utc):%Y-%m-%d}.jsonl"

    def record(self, *, call: str, outcome: str, owner: str | None = None,
               key_id: str | None = None, session_id: str | None = None,
               duration_ms: float | None = None, error: str | None = None,
               detail: dict[str, Any] | None = None, actor: str = "api") -> dict[str, Any]:
        t = self.clock()
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "actor": actor,
            "owner": owner,
            "key_id": key_id,
            "call": call,
            "session_id": session_id,
            "outcome": outcome,
        }
        if duration_ms is not None:
            entry["duration_ms"] = round(duration_ms, 3)
        if error is not None:
            entry["error"] = error[:500]
        if detail:
            entry["detail"] = detail
        line = json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str) + "\n"
        data = line.encode("utf-8")
        with self._lock:
            fd = os.open(self._path_for(t), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            try:
                os.write(fd, data)
                if self.fsync:
                    os.fsync(fd)
            finally:
                os.close(fd)
            if self.stream is not None:
                try:
                    self.stream.write("AUDIT " + line)
                    self.stream.flush()
                except Exception:  # a broken stdout must not fail a call
                    pass
        return entry

    def files(self) -> list[Path]:
        return sorted(self.dir.glob("audit-*.jsonl"))

    def read(self, *, owner: str | None = None, call: str | None = None,
             session_id: str | None = None) -> Iterator[dict[str, Any]]:
        for p in self.files():
            with open(p, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    e = json.loads(line)
                    if owner is not None and e.get("owner") != owner:
                        continue
                    if call is not None and e.get("call") != call:
                        continue
                    if session_id is not None and e.get("session_id") != session_id:
                        continue
                    yield e
