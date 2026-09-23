"""S3Store: the SessionStore protocol on Amazon S3, with FileStore's guarantees.

S3 has no append and no rename, so the commit protocol is built from the two
things it does guarantee: a PUT is atomic (a reader sees the whole old object
or the whole new one), reads after a write are strongly consistent (since
December 2020), and a PUT can be made conditional on the current ETag
(`If-Match`, November 2024) or on the object not existing (`If-None-Match: *`,
August 2024).

Layout under `s3://<bucket>/<prefix>`:

    sessions/<sid>/head.json                        THE COMMIT POINT
    sessions/<sid>/record-<seq>-<nonce>.json        the record at that seq
    sessions/<sid>/stream-<name>-<seq>-<nonce>.jsonl        entries one commit appended
    sessions/<sid>/stream-<name>-base-<seq>-<nonce>.jsonl   a compaction of older chunks
    owners/<owner>/<sid>.json                       an empty marker: which sessions an owner has

`head.json` names the committed seq, the record object, and for each stream
its base and the chunks after it. A commit PUTs the stream chunks and the
record first (new keys, nothing references them yet), then PUTs `head.json`
conditionally on the ETag it last saw. So:

- a reader sees a commit whole or not at all: it reads `head.json` and then
  only objects that head names, all written before it;
- a process that dies mid-commit leaves objects no head names; they are
  invisible, and the next commit's keys overwrite or ignore them;
- a second process committing the same session gets 412 on `head.json` and a
  `StoreConflict`, instead of silently interleaving (FileStore does not detect
  this; the contract says one writer per session, and here it is enforced).
  Every object a commit writes has a fresh nonce in its name, so the loser's
  objects never overwrite the winner's; they are garbage for `gc`.

Streams are compacted into one base object every `compact_every` chunks, so
reading a long session's history stays a handful of GETs.

Cost, which decides where this store belongs (HOSTED.md, "Storage"): a commit
is 2 PUTs plus one per stream it appends to, about 3 in practice, and a PUT
costs $0.005 per 1,000 in us-east-1. At one mutating call per bot every 5 s,
50 bots make about 2.6 million PUTs a day, roughly $13 a day, several times
what the same writes cost on EFS. S3 suits low call rates, archives and
backups; the launch default is FileStore on EFS.

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
    """Another writer committed this session since this process last read it."""


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

    def _list(self, prefix: str) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            r = self.s3.list_objects_v2(**kw)
            keys += [o["Key"] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return keys
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
            return None
        got = (raw[1], json.loads(raw[0]))
        with self._lock:
            self._heads[sid] = got
        return got

    # -- the SessionStore protocol ------------------------------------------------------

    def commit(self, session_id: str, record: dict[str, Any],
               appends: Mapping[str, Sequence[dict[str, Any]]]) -> None:
        sid = self._sid(session_id)
        seq = int(record["seq"])
        prev = self._read_head(sid)
        etag, head = prev if prev is not None else (None, {"seq": None, "streams": {}})
        # Every object this attempt writes gets a fresh name, so a writer that
        # is about to lose the conditional PUT below can never overwrite an
        # object the winning head names.
        tag = f"{seq:012d}-{secrets.token_hex(4)}"
        streams = {n: {"base": s.get("base"), "chunks": [list(c) for c in s["chunks"]],
                       "count": s["count"]} for n, s in head["streams"].items()}

        for name, entries in appends.items():
            if not isinstance(name, str) or not _STREAM.match(name):
                raise ValueError(f"bad stream name {name!r}")
            if not entries:
                continue
            st = streams.setdefault(name, {"base": None, "chunks": [], "count": 0})
            body = "".join(self._dumps(e) + "\n" for e in entries)
            chunk_key = f"stream-{name}-{tag}.jsonl"
            self._put(self._k("sessions", sid, chunk_key), body)
            st["chunks"].append([chunk_key, len(entries)])
            st["count"] += len(entries)
            if len(st["chunks"]) >= self.compact_every:
                self._compact(sid, name, st, tag)

        record_key = f"record-{tag}.json"
        self._put(self._k("sessions", sid, record_key), self._dumps(record))
        owner = str(record["head"].get("owner", ""))
        if prev is None and owner:
            # The owner index names the session BEFORE its first head exists,
            # so a crash in between leaves an entry `heads` skips, never a
            # committed session `heads` cannot find.
            self._put(self._k("owners", owner, f"{sid}.json"), "{}")
        new_head = {"seq": seq, "record": record_key, "head": self._encode(record["head"]),
                    "streams": streams}
        cond = {"IfMatch": etag} if etag else {"IfNoneMatch": "*"}
        try:
            new_etag = self._put(self._k("sessions", sid, "head.json"),
                                 json.dumps(new_head, separators=(",", ":"), allow_nan=False), **cond)
        except Exception as e:
            if _precondition(e):
                with self._lock:
                    self._heads.pop(sid, None)
                raise StoreConflict(f"session {sid}: another writer committed first; "
                                    "one process per session (see HOSTED.md)") from e
            raise
        with self._lock:
            self._heads[sid] = (new_etag, new_head)

        if head.get("record") and head["record"] != record_key:
            self._delete(self._k("sessions", sid, head["record"]))

    def _compact(self, sid: str, name: str, st: dict[str, Any], tag: str) -> None:
        entries = self._stream_lines(sid, name, st)
        base_key = f"stream-{name}-base-{tag}.jsonl"
        self._put(self._k("sessions", sid, base_key), "".join(line + "\n" for line in entries))
        st["base"] = [base_key, len(entries)]
        st["chunks"] = []
        # The superseded chunks stay until `gc`; the head that names them may
        # still be the committed one if this commit does not complete.

    def _stream_lines(self, sid: str, name: str, st: dict[str, Any]) -> list[str]:
        keys = []
        if st.get("base"):
            keys.append((st["base"][0], st["base"][1]))
        keys += [(key, n) for key, n in st["chunks"]]
        out: list[str] = []
        for key, n in keys:
            raw = self._get(self._k("sessions", sid, key))
            if raw is None:
                raise RuntimeError(f"session {sid}: stream object {key} named by the head is missing")
            lines = [line for line in raw[0].decode("utf-8").split("\n") if line]
            out += lines[:n]
        return out

    def load(self, session_id: str) -> dict[str, Any] | None:
        try:
            sid = self._sid(session_id)
        except KeyError:
            return None
        got = self._read_head(sid, fresh=not self.trust_cache)
        if got is None:
            return None
        raw = self._get(self._k("sessions", sid, got[1]["record"]))
        if raw is None:
            raise RuntimeError(f"session {sid}: record {got[1]['record']} named by the head is missing")
        return self._loads(raw[0])

    def read_stream(self, session_id: str, name: str) -> list[dict[str, Any]]:
        if not isinstance(name, str) or not _STREAM.match(name):
            raise ValueError(f"bad stream name {name!r}")
        try:
            sid = self._sid(session_id)
        except KeyError:
            return []
        got = self._read_head(sid, fresh=not self.trust_cache)
        if got is None or name not in got[1]["streams"]:
            return []
        return [self._loads(line) for line in self._stream_lines(sid, name, got[1]["streams"][name])]

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
        sids = sorted(k.rsplit("/", 1)[-1][:-5] for k in self._list(self._k("owners", owner) + "/")
                      if k.endswith(".json"))

        def fetch(sid: str) -> dict[str, Any] | None:
            got = self._read_head(sid, fresh=not self.trust_cache) if _ID.match(sid) else None
            return None if got is None else self._decode(got[1]["head"])
        with ThreadPoolExecutor(max_workers=max(1, min(self.list_workers, len(sids) or 1))) as ex:
            got = list(ex.map(fetch, sids))
        return [h for h in got if h is not None and h.get("owner") == owner]

    # -- housekeeping (not part of the protocol) -------------------------------------------

    def gc(self, session_id: str) -> int:
        """Delete this session's objects the committed head does not name:
        leftovers of interrupted commits and compacted chunks. Returns the
        number deleted. Safe while no commit to this session is in flight."""
        sid = self._sid(session_id)
        got = self._read_head(sid, fresh=True)
        if got is None:
            return 0
        head = got[1]
        live = {"head.json", head["record"]}
        for name, st in head["streams"].items():
            if st.get("base"):
                live.add(st["base"][0])
            live |= {key for key, _ in st["chunks"]}
        n = 0
        for key in self._list(self._k("sessions", sid) + "/"):
            if key.rsplit("/", 1)[-1] not in live:
                self._delete(key)
                n += 1
        return n

    def size_of(self, session_ids: Sequence[str]) -> int:
        """Bytes stored for these sessions (a storage meter for the hosted layer)."""
        total = 0
        for sid in session_ids:
            token = None
            while True:
                kw = {"Bucket": self.bucket, "Prefix": self._k("sessions", self._sid(sid)) + "/"}
                if token:
                    kw["ContinuationToken"] = token
                r = self.s3.list_objects_v2(**kw)
                total += sum(int(o.get("Size", 0)) for o in r.get("Contents", []))
                if not r.get("IsTruncated"):
                    break
                token = r.get("NextContinuationToken")
        return total
