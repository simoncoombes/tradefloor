"""S3Store: the SessionStore protocol on Amazon S3, with FileStore's guarantees.

S3 has no append and no rename, so the commit protocol is built from what it
does guarantee: a PUT is atomic (a reader sees the whole old object or the
whole new one), reads after a write are strongly consistent (since December
2020), and a PUT can be conditional on the current ETag (`If-Match`, November
2024) or on the object not existing (`If-None-Match: *`, August 2024).

Layout under `s3://<bucket>/<prefix>`:

    sessions/<sid>/head.json                       THE COMMIT POINT
    sessions/<sid>/record-<seq>-<nonce>.json       the record at that seq
    sessions/<sid>/chunk-<seq>-<nonce>.jsonl       every stream entry one commit appended
    sessions/<sid>/base-<name>-<seq>-<nonce>.jsonl one stream, compacted
    owners/<owner>/<sid>.json                      an empty marker: which sessions an owner has

`head.json` names the committed seq, the record object, and for each stream a
list of SEGMENTS `[object, first_line, n_lines]` whose lines, in order, are
the stream. A commit PUTs one chunk object holding all of its appends (the
lines of each stream contiguous, so each stream gets one segment) and the
record, both under fresh names nothing references yet, then PUTs `head.json`
conditionally on the ETag it last saw. So:

- a reader sees a commit whole or not at all: it reads `head.json`, then only
  objects that head names, all written before it;
- a process that dies mid-commit leaves objects no head names; they are
  invisible, and `gc` removes them;
- a second process committing the same session gets 412 on `head.json` and a
  `StoreConflict` instead of silently interleaving. Every object has a fresh
  nonce in its name, so the loser never overwrites anything the winner's
  head names. (FileStore does not detect a second writer; the contract says
  one writer per session, and here it is enforced.)

`trim_stream` (contract 0.4) is a rewrite of `head.json` alone: it drops the
leading segments and narrows the first one it keeps. No stream data is copied,
so trimming the core's 20-session step-bar window at a session boundary costs
one PUT. It is atomic the same way a commit is: readers see the head before
or after the trim.

After a new head lands, objects the old head named and the new one does not
(the previous record, trimmed-away chunks, chunks a compaction replaced) are
deleted. A reader in another thread that read the old head a moment earlier
may find one gone; it re-reads the head once and retries. (Within one
process the core serialises calls on a session anyway.) A stream is compacted
into one base object when it reaches `compact_every` segments, so reading a
long history stays a handful of GETs.

Cost (HOSTED.md, "Storage"): a commit is 3 PUTs (chunk, record, head), 2 when
it appends nothing, plus one per compaction; a trim is 1. S3 charges per
request, not per byte, so record size does not matter here the way it does
on EFS.

The encoding is `tradefloor.serve.store.encode` / `decode`, so floats come
back bit for bit (NaN payloads, infinities, -0.0), exactly as from FileStore.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Mapping, Sequence

_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_STREAM = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


class StoreConflict(RuntimeError):
    """Another writer changed this session since this process last read it."""


class _Missing(RuntimeError):
    """An object the head names is gone (a newer head superseded it)."""


def _codec():
    from tradefloor.serve.store import decode, encode  # the core's codec: bit-exact floats
    return encode, decode


def _err_code(e: Exception) -> str | None:
    resp = getattr(e, "response", None)
    if isinstance(resp, dict):
        return resp.get("Error", {}).get("Code")
    return None


def _missing(e: Exception) -> bool:
    return _err_code(e) in ("NoSuchKey", "404", "NotFound")


def _precondition(e: Exception) -> bool:
    return _err_code(e) in ("PreconditionFailed", "412", "ConditionalRequestConflict")


def _check_stream(name: Any) -> str:
    if not isinstance(name, str) or not _STREAM.match(name):
        raise ValueError(f"bad stream name {name!r}")
    return name


def _live(head: dict[str, Any]) -> set[str]:
    """The object names (within the session) a head depends on."""
    keys = {"head.json", head["record"]}
    for st in head["streams"].values():
        keys |= {seg[0] for seg in st["segments"]}
    return keys


class S3Store:
    """See the module docstring. `client` is a boto3 S3 client (or anything
    with the same five methods); by default one is made from the environment."""

    def __init__(self, bucket: str, prefix: str = "", *, client: Any = None,
                 compact_every: int = 32, trust_cache: bool = True,
                 list_workers: int = 16) -> None:
        if client is None:
            import boto3  # optional dependency, only for this store
            client = boto3.client("s3")
        self.s3 = client
        self.bucket = bucket
        self.prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self.compact_every = max(2, int(compact_every))
        self.trust_cache = trust_cache
        self.list_workers = list_workers
        self._heads: dict[str, tuple[str, dict[str, Any]]] = {}   # sid -> (etag, head.json)
        self._lock = threading.Lock()
        self._encode, self._decode = _codec()

    # -- keys and raw I/O ---------------------------------------------------------

    def _sid(self, session_id: str) -> str:
        if not isinstance(session_id, str) or not _ID.match(session_id):
            raise KeyError(session_id)
        return session_id

    def _k(self, *parts: str) -> str:
        return self.prefix + "/".join(parts)

    def _put(self, key: str, body: str, **cond: str) -> str:
        r = self.s3.put_object(Bucket=self.bucket, Key=key, Body=body.encode("utf-8"),
                               ContentType="application/json", **cond)
        return r.get("ETag", "")

    def _get(self, key: str) -> tuple[bytes, str] | None:
        try:
            r = self.s3.get_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            if _missing(e):
                return None
            raise
        return r["Body"].read(), r.get("ETag", "")

    def _delete(self, key: str) -> None:
        try:
            self.s3.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:  # best effort: garbage, not state
            if not _missing(e):
                raise

    def _list_objects(self, prefix: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            r = self.s3.list_objects_v2(**kw)
            out += r.get("Contents", [])
            if not r.get("IsTruncated"):
                return out
            token = r.get("NextContinuationToken")

    def _dumps(self, value: Any) -> str:
        return json.dumps(self._encode(value), separators=(",", ":"), allow_nan=False)

    def _loads(self, data: bytes | str) -> Any:
        return self._decode(json.loads(data))

    # -- head.json -------------------------------------------------------------------

    def _read_head(self, sid: str, *, fresh: bool = False) -> tuple[str, dict[str, Any]] | None:
        if not fresh and self.trust_cache:
            with self._lock:
                got = self._heads.get(sid)
            if got is not None:
                return got
        raw = self._get(self._k("sessions", sid, "head.json"))
        if raw is None:
            with self._lock:
                self._heads.pop(sid, None)
            return None
        got = (raw[1], json.loads(raw[0]))
        with self._lock:
            self._heads[sid] = got
        return got

    def _write_head(self, sid: str, etag: str | None, old: dict[str, Any] | None,
                    new: dict[str, Any], written: set[str] = frozenset()) -> None:
        """The commit point: PUT head.json if nobody else has since `etag`,
        then delete what only the old head named, and anything this commit
        wrote that the new head does not name (a chunk compacted at once)."""
        cond = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        try:
            new_etag = self._put(self._k("sessions", sid, "head.json"),
                                 json.dumps(new, separators=(",", ":"), allow_nan=False), **cond)
        except Exception as e:
            if _precondition(e):
                with self._lock:
                    self._heads.pop(sid, None)
                raise StoreConflict(f"session {sid}: another writer changed it first; "
                                    "one process per session (see HOSTED.md)") from e
            raise
        with self._lock:
            self._heads[sid] = (new_etag, new)
        dead = (_live(old) if old is not None else set()) | set(written)
        for key in sorted(dead - _live(new)):
            self._delete(self._k("sessions", sid, key))

    def _reading(self, sid: str, fn):
        """Run `fn(head)` on the current head; if an object it names has just
        been superseded and deleted, re-read the head once and retry."""
        for attempt in (0, 1):
            got = self._read_head(sid, fresh=attempt > 0 or not self.trust_cache)
            if got is None:
                return None
            try:
                return fn(got[1])
            except _Missing:
                if attempt:
                    raise RuntimeError(f"session {sid}: an object named by the head is "
                                       "missing; the store is damaged") from None

    def _lines(self, sid: str, segments: list[list[Any]]) -> list[str]:
        out: list[str] = []
        for key, first, n in segments:
            raw = self._get(self._k("sessions", sid, key))
            if raw is None:
                raise _Missing(key)
            lines = raw[0].decode("utf-8").split("\n")
            out += lines[first:first + n]
        return out

    # -- the SessionStore protocol ------------------------------------------------------

    def commit(self, session_id: str, record: dict[str, Any],
               appends: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        sid = self._sid(session_id)
        seq = int(record["seq"])
        for name in appends:
            _check_stream(name)
        prev = self._read_head(sid)
        etag, old = prev if prev is not None else (None, None)
        streams = {n: {"segments": [list(s) for s in st["segments"]], "count": st["count"]}
                   for n, st in (old["streams"] if old else {}).items()}
        # Every object this attempt writes gets a fresh name, so a writer about
        # to lose the conditional PUT below never overwrites what the winner's
        # head names.
        tag = f"{seq:012d}-{secrets.token_hex(4)}"

        written: set[str] = set()
        chunk_lines: list[str] = []
        spans: dict[str, tuple[int, int]] = {}
        for name, entries in appends.items():
            if entries:
                spans[name] = (len(chunk_lines), len(entries))
                chunk_lines += [self._dumps(e) for e in entries]
        if chunk_lines:
            chunk_key = f"chunk-{tag}.jsonl"
            self._put(self._k("sessions", sid, chunk_key), "\n".join(chunk_lines) + "\n")
            written.add(chunk_key)
            for name, (first, n) in spans.items():
                st = streams.setdefault(name, {"segments": [], "count": 0})
                st["segments"].append([chunk_key, first, n])
                st["count"] += n
                if len(st["segments"]) >= self.compact_every:
                    written.add(self._compact(sid, name, st, tag))

        record_key = f"record-{tag}.json"
        self._put(self._k("sessions", sid, record_key), self._dumps(record))
        owner = str(record["head"].get("owner", ""))
        if old is None and owner:
            # The owner marker is written BEFORE the first head exists, so a
            # crash in between leaves a marker `heads` skips, never a committed
            # session `heads` cannot find.
            self._put(self._k("owners", owner, f"{sid}.json"), "{}")
        new = {"seq": seq, "record": record_key, "head": self._encode(record["head"]),
               "streams": streams}
        self._write_head(sid, etag, old, new, written)

    def _compact(self, sid: str, name: str, st: dict[str, Any], tag: str) -> str:
        lines = self._lines(sid, st["segments"])
        base_key = f"base-{name}-{tag}.jsonl"
        self._put(self._k("sessions", sid, base_key), "".join(line + "\n" for line in lines))
        st["segments"] = [[base_key, 0, len(lines)]]
        return base_key

    def trim_stream(self, session_id: str, name: str, keep_last: int) -> None:
        """Keep only the last `keep_last` entries of stream `name` (contract
        0.4). One conditional PUT of the head; no data is copied."""
        _check_stream(name)
        if isinstance(keep_last, bool) or not isinstance(keep_last, int) or keep_last < 0:
            raise ValueError(f"keep_last must be a non-negative integer, got {keep_last!r}")
        try:
            sid = self._sid(session_id)
        except KeyError:
            return
        prev = self._read_head(sid)
        if prev is None:
            return
        etag, old = prev
        st = old["streams"].get(name)
        if st is None or st["count"] <= keep_last:
            return
        kept: list[list[Any]] = []
        need = keep_last
        for key, first, n in reversed(st["segments"]):
            if need <= 0:
                break
            take = min(n, need)
            kept.append([key, first + n - take, take])
            need -= take
        kept.reverse()
        streams = dict(old["streams"])
        streams[name] = {"segments": kept, "count": keep_last}
        self._write_head(sid, etag, old, {**old, "streams": streams})

    def load(self, session_id: str) -> dict[str, Any] | None:
        try:
            sid = self._sid(session_id)
        except KeyError:
            return None

        def read(head: dict[str, Any]) -> dict[str, Any]:
            raw = self._get(self._k("sessions", sid, head["record"]))
            if raw is None:
                raise _Missing(head["record"])
            return self._loads(raw[0])
        return self._reading(sid, read)

    def read_stream(self, session_id: str, name: str) -> list[dict[str, Any]]:
        _check_stream(name)
        try:
            sid = self._sid(session_id)
        except KeyError:
            return []

        def read(head: dict[str, Any]) -> list[dict[str, Any]]:
            st = head["streams"].get(name)
            if st is None:
                return []
            return [self._loads(line) for line in self._lines(sid, st["segments"])]
        return self._reading(sid, read) or []

    def version(self, session_id: str) -> int | None:
        try:
            sid = self._sid(session_id)
        except KeyError:
            return None
        got = self._read_head(sid, fresh=not self.trust_cache)
        return None if got is None else int(got[1]["seq"])

    def heads(self, owner: str) -> list[dict[str, Any]]:
        """Every session's head for `owner`: the owner index gives the ids, and
        each session's own head.json (the commit point) gives the head."""
        sids = sorted(o["Key"].rsplit("/", 1)[-1][:-5]
                      for o in self._list_objects(self._k("owners", owner) + "/")
                      if o["Key"].endswith(".json"))

        def fetch(sid: str) -> dict[str, Any] | None:
            got = self._read_head(sid, fresh=not self.trust_cache) if _ID.match(sid) else None
            return None if got is None else self._decode(got[1]["head"])
        with ThreadPoolExecutor(max_workers=max(1, min(self.list_workers, len(sids) or 1))) as ex:
            got = list(ex.map(fetch, sids))
        return [h for h in got if h is not None and h.get("owner") == owner]

    # -- housekeeping (not part of the protocol) -------------------------------------------

    def gc(self, session_id: str) -> int:
        """Delete this session's objects the committed head does not name:
        leftovers of interrupted commits. Returns the number deleted. Safe
        while no commit to this session is in flight."""
        sid = self._sid(session_id)
        got = self._read_head(sid, fresh=True)
        if got is None:
            return 0
        live = _live(got[1])
        n = 0
        for o in self._list_objects(self._k("sessions", sid) + "/"):
            if o["Key"].rsplit("/", 1)[-1] not in live:
                self._delete(o["Key"])
                n += 1
        return n

    def size_of(self, session_ids: Sequence[str]) -> int:
        """Bytes stored for these sessions (a storage meter for the hosted layer)."""
        return sum(int(o.get("Size", 0)) for sid in session_ids
                   for o in self._list_objects(self._k("sessions", self._sid(sid)) + "/"))
