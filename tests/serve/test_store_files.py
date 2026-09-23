"""SessionStore implementations: the codec, FileStore's crash safety, and
MemoryStore behaving as FileStore does."""
from __future__ import annotations

import json
import math
import struct

import pytest

from tradefloor.serve import store as store_mod
from tradefloor.serve.store import (FileStore, MemoryStore, SessionStore, decode,
                                    encode)


def bits(x: float) -> str:
    return struct.pack("<d", x).hex()


def odd_floats():
    payload_nan = struct.unpack("<d", bytes.fromhex("0100000000f8ff7f"))[0]
    neg_nan = struct.unpack("<d", bytes.fromhex("000000000000f8ff"))[0]
    return [payload_nan, neg_nan, float("inf"), float("-inf"), -0.0, 0.0,
            5e-324, 1.7976931348623157e308, 0.1 + 0.2]


# -- the codec ----------------------------------------------------------------------

def test_codec_round_trips_floats_bit_for_bit():
    values = odd_floats()
    text = json.dumps(encode({"v": values, "t": tuple(values)}), allow_nan=False)
    back = decode(json.loads(text))
    assert [bits(x) for x in back["v"]] == [bits(x) for x in values]
    assert [bits(x) for x in back["t"]] == [bits(x) for x in values]


def test_codec_round_trips_bytes_and_structure():
    value = {"a": b"\x00\xffxyz", "b": [1, True, None, "s", {"c": bytearray(b"q")}],
             "n": 3}
    back = decode(json.loads(json.dumps(encode(value), allow_nan=False)))
    assert back == {"a": b"\x00\xffxyz", "b": [1, True, None, "s", {"c": b"q"}], "n": 3}
    assert isinstance(back["n"], int)


def test_plain_json_would_have_lost_the_nan_payload():
    """Why the codec exists: json turns every NaN into the same NaN."""
    payload_nan = odd_floats()[0]
    plain = json.loads(json.dumps(payload_nan))
    assert math.isnan(plain) and bits(plain) != bits(payload_nan)


def test_engine_snapshot_survives_the_store(tmp_path):
    import tradefloor as tf

    e = tf.Engine(seed=2, universe=tf.Universe.random(5, seed=111))
    e.open_market()
    e.run_session(9, 30, 3, 30)
    snap = e.state_snapshot()
    assert any(isinstance(v, float) and math.isnan(v) for v in snap["rng"])
    fs = FileStore(tmp_path)
    fs.commit("s1", {"seq": 1, "head": {"owner": "o"}, "snap": snap}, {})
    back = fs.load("s1")["snap"]
    e2 = tf.Engine(seed=2, universe=tf.Universe.random(5, seed=111))
    e2.restore_state(back)
    assert e2.state_hash() == e.state_hash()


# -- the protocol, for both stores --------------------------------------------------------

@pytest.fixture(params=["file", "memory"])
def any_store(request, tmp_path):
    return FileStore(tmp_path) if request.param == "file" else MemoryStore()


def rec(seq, owner="o", **extra):
    return {"seq": seq, "head": {"owner": owner, "n": seq}, **extra}


def test_store_protocol(any_store):
    s = any_store
    assert isinstance(s, SessionStore)
    assert s.load("abc") is None and s.version("abc") is None
    assert s.read_stream("abc", "fills") == []
    s.commit("abc", rec(1, x=[1.5]), {"calls": [{"i": 1}], "fills": []})
    s.commit("abc", rec(2, x=[2.5]), {"calls": [{"i": 2}], "fills": [{"f": b"b"}]})
    s.commit("def", rec(1, owner="p"), {"calls": [{"i": 9}]})
    assert s.version("abc") == 2 and s.load("abc")["x"] == [2.5]
    assert s.read_stream("abc", "calls") == [{"i": 1}, {"i": 2}]
    assert s.read_stream("abc", "fills") == [{"f": b"b"}]
    assert s.read_stream("abc", "orders") == []
    assert [h["n"] for h in s.heads("o")] == [2]
    assert [h["owner"] for h in s.heads("p")] == ["p"]
    assert s.heads("nobody") == []


def test_loaded_records_do_not_alias(any_store):
    record = rec(1, x=[1.0])
    any_store.commit("abc", record, {})
    record["x"].append(2.0)
    got = any_store.load("abc")
    got["x"].append(3.0)
    assert any_store.load("abc")["x"] == [1.0]


@pytest.mark.parametrize("bad", ["../x", "a/b", "", "x" * 65, ".hidden"])
def test_unsafe_ids_are_refused(tmp_path, bad):
    fs = FileStore(tmp_path)
    assert fs.load(bad) is None and fs.version(bad) is None
    with pytest.raises(KeyError):
        fs.commit(bad, rec(1), {})
    assert list(tmp_path.iterdir()) == []


def test_bad_stream_names_are_refused(any_store):
    with pytest.raises(ValueError):
        any_store.commit("abc", rec(1), {"../x": [{}]})


# -- FileStore crash safety -------------------------------------------------------------------

def test_layout_and_no_leftovers(tmp_path):
    fs = FileStore(tmp_path)
    for seq in range(1, 4):
        fs.commit("abc", rec(seq), {"calls": [{"i": seq}]})
    names = sorted(p.name for p in (tmp_path / "abc").iterdir())
    assert names == ["calls.jsonl", "head.json", "record-3.json"]


def test_crash_before_the_head_rename_keeps_the_last_commit(tmp_path, monkeypatch):
    fs = FileStore(tmp_path)
    fs.commit("abc", rec(1, v=1), {"calls": [{"i": 1}], "fills": [{"f": 1}]})
    real = FileStore._write_atomic

    def die_on_head(self, path, text):
        if path.name == "head.json":
            raise KeyboardInterrupt("killed")
        return real(self, path, text)

    monkeypatch.setattr(FileStore, "_write_atomic", die_on_head)
    with pytest.raises(KeyboardInterrupt):
        fs.commit("abc", rec(2, v=2), {"calls": [{"i": 2}], "fills": [{"f": 2}]})
    monkeypatch.setattr(FileStore, "_write_atomic", real)

    fresh = FileStore(tmp_path)
    assert fresh.version("abc") == 1 and fresh.load("abc")["v"] == 1
    assert fresh.read_stream("abc", "calls") == [{"i": 1}]
    assert fresh.read_stream("abc", "fills") == [{"f": 1}]
    # The orphaned lines are on disk, uncommitted...
    assert len((tmp_path / "abc" / "calls.jsonl").read_text().splitlines()) == 2
    # ...and the next commit replaces them.
    fresh.commit("abc", rec(2, v=3), {"calls": [{"i": 3}]})
    assert fresh.read_stream("abc", "calls") == [{"i": 1}, {"i": 3}]
    assert fresh.read_stream("abc", "fills") == [{"f": 1}]
    lines = (tmp_path / "abc" / "calls.jsonl").read_text().splitlines()
    assert [json.loads(x) for x in lines] == [{"i": 1}, {"i": 3}]
    assert fresh.load("abc")["v"] == 3


def test_crash_while_appending_keeps_the_last_commit(tmp_path, monkeypatch):
    fs = FileStore(tmp_path)
    fs.commit("abc", rec(1, v=1), {"calls": [{"i": 1}]})
    calls = {"n": 0}
    real = store_mod._dumps

    def die_midway(value):
        calls["n"] += 1
        if calls["n"] == 2:
            raise KeyboardInterrupt("killed")
        return real(value)

    monkeypatch.setattr(store_mod, "_dumps", die_midway)
    with pytest.raises(KeyboardInterrupt):
        fs.commit("abc", rec(2, v=2), {"calls": [{"i": 2}, {"i": 3}]})
    monkeypatch.setattr(store_mod, "_dumps", real)
    assert fs.version("abc") == 1 and fs.read_stream("abc", "calls") == [{"i": 1}]


def test_temp_files_do_not_survive_a_failed_write(tmp_path, monkeypatch):
    fs = FileStore(tmp_path)
    fs.commit("abc", rec(1), {})

    def boom(*a, **kw):
        raise OSError("no space")

    monkeypatch.setattr(store_mod.os, "replace", boom)
    with pytest.raises(OSError):
        fs.commit("abc", rec(2), {})
    monkeypatch.undo()
    assert not [p for p in (tmp_path / "abc").iterdir() if p.name.endswith(".tmp")]
    assert fs.version("abc") == 1


def test_fsync_mode_works(tmp_path):
    fs = FileStore(tmp_path, fsync=True)
    fs.commit("abc", rec(1), {"calls": [{"i": 1}]})
    assert FileStore(tmp_path).read_stream("abc", "calls") == [{"i": 1}]


def test_default_root_is_under_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert store_mod.default_root() == tmp_path / ".tradefloor" / "sessions"
    fs = FileStore()
    assert fs.root == tmp_path / ".tradefloor" / "sessions" and fs.root.is_dir()


def test_heads_ignore_stray_files(tmp_path):
    fs = FileStore(tmp_path)
    fs.commit("abc", rec(1), {})
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "empty").mkdir()
    assert [h["n"] for h in fs.heads("o")] == [1]


def test_history_goes_to_streams_so_the_record_stays_flat(tmp_path):
    """A long session's commit is the size of the market, not of its past."""
    from tradefloor.serve.core import LocalSessionService
    from tradefloor.serve.types import OrderRequest, SessionConfig

    svc = LocalSessionService(FileStore(tmp_path))
    sid = svc.open("o", SessionConfig(seed=2, universe_size=6)).session_id
    t = svc.info("o", sid).tickers[0]

    def record_size():
        (rec,) = [p for p in (tmp_path / sid).iterdir() if p.name.startswith("record-")]
        return rec.stat().st_size

    svc.advance("o", sid)
    base = record_size()
    for k in range(60):
        svc.place_order("o", sid, OrderRequest(ticker=t, side="buy" if k % 2 else "sell",
                                               quantity=10))
        svc.advance("o", sid)
    assert len(svc.fills("o", sid)) == 60
    assert record_size() < base * 1.2
    fills = (tmp_path / sid / "fills.jsonl").read_text().splitlines()
    assert len(fills) == 60
