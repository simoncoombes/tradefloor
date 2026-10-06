"""A cache of measured readings, one file per (build, model, protocol, seed).

A screen measures each candidate on many protocols (a 252-day panel, a
504-day panel, a held VIX, a held-out universe and so on), each on many
seeds. Most of those readings do not change from one candidate to the next:
the same build and the same coefficients give the same numbers bit for bit,
and a dial that cannot reach a protocol leaves that protocol's readings
where they were. This module stores every reading once and hands it back.

The key has four parts:

* the simulator build digest (`build_digest`): sha256 over the engine's
  source files, so any code change starts a new part of the cache;
* the model digest (`params_digest`): sha256 over the coefficient vector,
  the same bytes `ModelParams.digest` hashes in Rust, so a switch listed in
  `ModelParams.digest_silent_at_zero()` and left at 0.0 does not change it;
* the protocol id (`protocol_id`): the measurement's name and every setting
  that changes its output, such as the horizon or the universe;
* the seed.

A value is any JSON object. Writes are atomic and write-once: writing a
different value under a key that already holds one raises `CacheConflict`,
because two runs of one deterministic measurement that disagree are a
determinism failure and must not be papered over by the later write.

Reading a baseline's value for a protocol the candidate's changed dials
cannot reach is `resolve`'s job. `verify` mode measures everything anyway and
reports every cached or baseline value that disagrees with the fresh one;
each such disagreement on a baseline read is a reach claim the measurement
refutes.

This file uses only the standard library, so box scripts and graders that
run without the engine installed can import it.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import os
import pathlib
import struct
import tempfile
from typing import Any, Iterable, Mapping

SCHEMA = 1

#: The source trees whose content decides what a measurement returns. The
#: Rust crate is the simulation; the Python package holds the statistics
#: (`facts`, `envelope`) that turn a simulated market into a reading.
BUILD_SOURCES = ("rust/src", "rust/Cargo.toml", "rust/Cargo.lock",
                 "python/tradefloor", "pyproject.toml")

#: File suffixes under BUILD_SOURCES that are hashed. Bytecode, build
#: products and editor files are not source and would make two checkouts of
#: one commit disagree.
SOURCE_SUFFIXES = (".rs", ".py", ".pyi", ".toml", ".lock", ".yml", ".yaml",
                   ".json", ".csv", ".txt", ".md")


class CacheConflict(RuntimeError):
    """A key already holds a different value."""


def _engine_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def build_digest(root: str | os.PathLike | None = None) -> str:
    """sha256 over the engine's source files, as relative path and content.

    In a git checkout the files are the ones git tracks under BUILD_SOURCES
    (`tracked_sources`); elsewhere, every source file found there. Two
    checkouts of one commit give one digest on any platform, whether or not
    either has built the engine, and any edit to the Rust crate or the Python
    package gives another. The compiled
    extension is not hashed: it is built from these files, and the
    known-answer test is what proves that every platform's build of them
    computes the same bits.
    """
    base = pathlib.Path(root) if root else _engine_root()
    h = hashlib.sha256()
    files = tracked_sources(base)
    if files is not None:
        if not files:
            raise FileNotFoundError(f"no tracked engine sources under {base}")
        for f in files:
            rel = f.relative_to(base).as_posix().encode("utf-8")
            data = f.read_bytes()
            h.update(len(rel).to_bytes(4, "big") + rel)
            h.update(len(data).to_bytes(8, "big") + data)
        return h.hexdigest()
    files = []
    for rel in BUILD_SOURCES:
        p = base / rel
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(q for q in p.rglob("*")
                         if q.is_file() and q.suffix in SOURCE_SUFFIXES
                         and "__pycache__" not in q.parts)
    if not files:
        raise FileNotFoundError(f"no engine sources under {base}")
    for f in sorted(files, key=lambda q: q.relative_to(base).as_posix()):
        rel = f.relative_to(base).as_posix().encode("utf-8")
        data = f.read_bytes()
        h.update(len(rel).to_bytes(4, "big") + rel)
        h.update(len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


def tracked_sources(base: pathlib.Path) -> list[pathlib.Path] | None:
    """The files git tracks under BUILD_SOURCES, sorted, or None outside git.

    Only tracked files: a build writes files beside the sources that git
    ignores, and `rust/Cargo.lock` is one. Hashing it gave two boxes on one
    commit two digests, because the first built the engine (and so wrote the
    lock) and the second installed a cached wheel (and did not). The content
    hashed is the working tree's, so an uncommitted edit to a tracked source
    still moves the digest.
    """
    import subprocess  # noqa: PLC0415
    try:
        out = subprocess.run(["git", "-C", str(base), "ls-files", "-z", "--", *BUILD_SOURCES],
                             check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    names = sorted(n for n in out.decode("utf-8").split("\0") if n)
    return [base / n for n in names if (base / n).is_file()]


def params_digest(model: Any) -> str:
    """The model's full sha256, the bytes `ModelParams.digest` hashes in Rust.

    Names sorted, each as `name=` then the value's IEEE-754 bits big-endian
    then a newline, with every name in `digest_silent_at_zero()` left out
    while it is 0.0 and every name in `digest_silent_at_default()` left out
    while it holds that default. So the first eight hex characters are a
    custom model's fingerprint suffix, and a new switch left at zero keys
    the same entries as the model before it existed.

    `model` is a `ModelParams` or a plain mapping of its `to_dict()`; the
    mapping form needs the silent names passed through `__silent__` and the
    silent defaults through `__silent_default__`.
    """
    if isinstance(model, Mapping):
        values = dict(model)
        silent = set(values.pop("__silent__", ()))
        silent_default = dict(values.pop("__silent_default__", {}))
    else:
        values = model.to_dict()
        silent = set(type(model).digest_silent_at_zero())
        silent_default = dict(type(model).digest_silent_at_default())
    values.pop("name", None)
    h = hashlib.sha256()
    for name in sorted(values):
        v = float(values[name])
        if v == 0.0 and name in silent:
            continue
        if name in silent_default and struct.pack(">d", v) == struct.pack(
            ">d", float(silent_default[name])
        ):
            continue
        h.update(name.encode("utf-8") + b"=" + struct.pack(">d", v) + b"\n")
    return h.hexdigest()


def protocol_id(name: str, **settings: Any) -> str:
    """A protocol's cache name: its own name plus every setting, canonical.

    `protocol_id("p252", universe_seed=111, universe_n=40)` is
    `p252[universe_n=40,universe_seed=111]`. A setting left out of the id is
    a setting two different measurements would share a key under, so pass
    every one that changes the output.
    """
    if any(c in name for c in "/[]") or not name:
        raise ValueError(f"protocol name {name!r} must be non-empty without / [ ]")
    if not settings:
        return name
    body = ",".join(f"{k}={settings[k]!r}" if isinstance(settings[k], str)
                    else f"{k}={settings[k]}" for k in sorted(settings))
    if "/" in body:
        raise ValueError("a protocol setting may not contain '/'")
    return f"{name}[{body}]"


@dataclasses.dataclass(frozen=True)
class Key:
    build: str
    params: str
    protocol: str
    seed: int | str

    def relpath(self) -> str:
        safe = hashlib.sha256(self.protocol.encode("utf-8")).hexdigest()[:16]
        return f"{self.build[:16]}/{self.params[:16]}/{safe}/{self.seed}.json"

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


def _canonical(value: Any) -> Any:
    """JSON-ready, with non-finite floats spelled so they survive a round trip."""
    if isinstance(value, float):
        if math.isnan(value):
            return {"__float__": "nan"}
        if math.isinf(value):
            return {"__float__": "inf" if value > 0 else "-inf"}
        return value
    if isinstance(value, Mapping):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        return _canonical(value.item())          # a numpy scalar
    return value


def _restore(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"__float__"}:
            return float(value["__float__"])
        return {k: _restore(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_restore(v) for v in value]
    return value


def differences(cached: Any, measured: Any, *, rel_tol: float = 0.0,
                path: str = "") -> list[str]:
    """Where two readings disagree, as a list of `path: cached != measured`.

    Exact by default: a deterministic measurement repeated on one build must
    agree to the last bit, and NaN equals NaN here. `rel_tol` loosens float
    comparison for readings that cross builds a reach claim says are
    equivalent but cannot prove to the bit.
    """
    cached, measured = _restore(_canonical(cached)), _restore(_canonical(measured))
    out: list[str] = []
    if isinstance(cached, dict) and isinstance(measured, dict):
        for k in sorted(set(cached) | set(measured)):
            if k not in cached or k not in measured:
                out.append(f"{path}{k}: present in only one")
                continue
            out += differences(cached[k], measured[k], rel_tol=rel_tol,
                               path=f"{path}{k}.")
        return out
    if isinstance(cached, list) and isinstance(measured, list):
        if len(cached) != len(measured):
            return [f"{path.rstrip('.')}: length {len(cached)} != {len(measured)}"]
        for i, (a, b) in enumerate(zip(cached, measured)):
            out += differences(a, b, rel_tol=rel_tol, path=f"{path}{i}.")
        return out
    if isinstance(cached, (int, float)) and isinstance(measured, (int, float)) \
            and not isinstance(cached, bool) and not isinstance(measured, bool):
        a, b = float(cached), float(measured)
        if math.isnan(a) and math.isnan(b):
            return []
        if a == b or (rel_tol and math.isclose(a, b, rel_tol=rel_tol, abs_tol=0.0)):
            return []
        return [f"{path.rstrip('.')}: {cached!r} != {measured!r}"]
    if cached != measured:
        return [f"{path.rstrip('.')}: {cached!r} != {measured!r}"]
    return []


class ResultCache:
    """A directory of readings, one JSON file per key.

    Many worker processes may write at once: every file is written to a
    temporary name in its own directory and renamed into place, which is
    atomic on POSIX, and no two workers measure one key in one run.
    """

    def __init__(self, root: str | os.PathLike):
        self.root = pathlib.Path(root)

    def path(self, key: Key) -> pathlib.Path:
        return self.root / key.relpath()

    def get(self, key: Key) -> Any | None:
        p = self.path(key)
        if not p.exists():
            return None
        doc = json.loads(p.read_text(encoding="utf-8"))
        if doc.get("key") != key.as_dict():
            # A truncated prefix collided, or the file was copied in by hand.
            raise CacheConflict(f"{p} holds {doc.get('key')}, not {key.as_dict()}")
        return _restore(doc["value"])

    def put(self, key: Key, value: Any, meta: Mapping[str, Any] | None = None) -> pathlib.Path:
        p = self.path(key)
        old = self.get(key)
        if old is not None:
            diff = differences(old, value)
            if diff:
                raise CacheConflict(
                    f"{key.protocol} seed {key.seed}: a second measurement on the "
                    f"same build and model disagrees with the cached one: {diff[:3]}")
            return p
        p.parent.mkdir(parents=True, exist_ok=True)
        doc = {"schema": SCHEMA, "key": key.as_dict(), "meta": dict(meta or {}),
               "value": _canonical(value)}
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
        os.replace(tmp, p)
        return p

    def entries(self) -> Iterable[tuple[Key, Any]]:
        for p in sorted(self.root.rglob("*.json")):
            if p.name.startswith(".tmp-"):
                continue
            doc = json.loads(p.read_text(encoding="utf-8"))
            yield Key(**doc["key"]), _restore(doc["value"])


@dataclasses.dataclass(frozen=True)
class Task:
    """One (protocol, seed) of one candidate, and where its reading comes from.

    `source` is `cached` (the candidate's own entry), `baseline` (the
    baseline model's entry, because no changed dial reaches the protocol),
    or `measure`. In verify mode every task is measured and `expect` holds
    the cached or baseline reading it must agree with, or None.
    """
    protocol: str
    seed: int | str
    key: Key
    source: str
    expect_from: Key | None = None


def resolve(cache: ResultCache, *, build: str, params: str,
            baseline_build: str | None, baseline_params: str | None,
            protocols: Iterable[str], seeds: Iterable[int | str],
            reachable: set[str], verify: bool = False) -> list[Task]:
    """Decide, task by task, whether to read or to measure.

    A protocol in `reachable` is read only from the candidate's own entries.
    A protocol outside it may be read from the baseline's entries, since the
    reach map says no changed dial can move it. Verify mode measures every
    task and keeps the key whose value the fresh reading must reproduce.
    """
    seeds = list(seeds)
    out: list[Task] = []
    have_base = baseline_build is not None and baseline_params is not None
    for proto in protocols:
        for s in seeds:
            own = Key(build, params, proto, s)
            base = Key(baseline_build, baseline_params, proto, s) if have_base else None
            own_hit = cache.get(own) is not None
            base_hit = (base is not None and proto not in reachable
                        and cache.get(base) is not None)
            if verify:
                exp = own if own_hit else (base if base_hit else None)
                out.append(Task(proto, s, own, "measure", exp))
            elif own_hit:
                out.append(Task(proto, s, own, "cached"))
            elif base_hit:
                out.append(Task(proto, s, own, "baseline", base))
            else:
                out.append(Task(proto, s, own, "measure"))
    return out


def summary(tasks: Iterable[Task]) -> dict[str, int]:
    out: dict[str, int] = {"cached": 0, "baseline": 0, "measure": 0}
    for t in tasks:
        out[t.source] += 1
    return out
