"""A host's share counts (#274): `Engine.set_shares_outstanding`.

A host whose companies buy back stock or issue it writes the counts, and the
engine weights by them from then on. The write is an input: the order log
carries it and a replay makes it again. A snapshot carries the counts, and
the state hash covers them, only once they have moved, so an engine never
told anything snapshots and hashes as it did before they were settable.
"""
from __future__ import annotations

import json
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest as mf

UNIVERSE = tf.Universe.random(8, seed=111)


def _engine(seed=7, **dials):
    model = tf.ModelParams.from_preset("pt-v21", **dials)
    return tf.Engine(seed=seed, universe=UNIVERSE, model=model)


def _floats(raw):
    return struct.unpack(f"<{len(raw) // 8}d", raw)


def _moved(engine):
    return [s * (0.8 if i % 2 == 0 else 1.2)
            for i, s in enumerate(engine.shares_outstanding())]


def test_the_counts_are_the_rosters_and_the_market_cap_follows_a_write():
    e = _engine()
    assert e.shares_outstanding() == [i.shares_outstanding for i in UNIVERSE]
    e.run_days(2, record=False)
    shares = _moved(e)
    e.set_shares_outstanding(shares)
    assert e.shares_outstanding() == shares
    caps, prices = _floats(e.column("market_cap")), _floats(e.prices())
    assert list(caps) == [p * s for p, s in zip(prices, shares)]
    e.run_days(1, record=False)
    caps, prices = _floats(e.column("market_cap")), _floats(e.prices())
    assert list(caps) == [p * s for p, s in zip(prices, shares)]


@pytest.mark.parametrize("bad", [0.0, -5.0, float("nan"), float("inf")])
def test_a_bad_count_is_refused_by_name_and_writes_nothing(bad):
    e = _engine()
    e.run_days(1, record=False)
    before = e.state_hash()
    shares = e.shares_outstanding()
    shares[1] = bad
    with pytest.raises(tf.ValidationError, match=r"shares_outstanding\[1\]"):
        e.set_shares_outstanding(shares)
    with pytest.raises(tf.ValidationError, match="values for"):
        e.set_shares_outstanding(shares[:-1])
    assert e.state_hash() == before
    assert not any(x["op"] == "set_shares_outstanding" for x in e.order_log)


def test_the_counts_are_in_the_hash_the_snapshot_and_the_restore():
    untouched, moved = _engine(seed=5), _engine(seed=5)
    for engine in (untouched, moved):
        engine.run_days(5, record=False)
    assert "shares_outstanding" not in untouched.state_snapshot()
    moved.set_shares_outstanding(_moved(moved))
    assert moved.state_hash() != untouched.state_hash()
    snapshot = moved.state_snapshot()
    assert "shares_outstanding" in snapshot
    assert mf.state_hash(snapshot) == moved.state_hash()
    restored = _engine(seed=5)
    restored.restore_state(snapshot)
    assert restored.shares_outstanding() == moved.shares_outstanding()
    assert restored.state_hash() == moved.state_hash()
    for engine in (moved, restored):
        engine.run_days(10, record=False)
    assert _floats(restored.prices()) == _floats(moved.prices())


def test_a_snapshot_without_counts_restores_the_construction_counts():
    clean = _engine(seed=5)
    clean.run_days(3, record=False)
    snapshot = clean.state_snapshot()
    target = _engine(seed=5)
    target.set_shares_outstanding(_moved(target))
    target.restore_state(snapshot)
    assert target.shares_outstanding() == clean.shares_outstanding()
    assert target.state_hash() == clean.state_hash()


def test_the_ledger_codec_carries_the_counts():
    e = _engine(seed=5)
    e.run_days(2, record=False)
    e.set_shares_outstanding(_moved(e))
    snapshot = e.state_snapshot()
    payload = json.loads(json.dumps(mf._snapshot_to_json(snapshot)))
    back = mf._snapshot_from_json(payload)
    assert back["shares_outstanding"] == snapshot["shares_outstanding"]
    assert mf.state_hash(back) == e.state_hash()


def test_a_count_write_is_logged_and_replayed():
    e = _engine()
    e.open_market()
    e.run_session(9, 30, 3, 30)
    e.set_shares_outstanding(_moved(e))
    e.run_session(10, 0, 3, 30)
    e.close_market()
    writes = [x for x in e.order_log if x["op"] == "set_shares_outstanding"]
    assert len(writes) == 1 and writes[0]["shares"] == e.shares_outstanding()
    log = json.loads(json.dumps(e.order_log, allow_nan=False))
    replayed = tf.replay(log, seed=7, universe=UNIVERSE,
                         model=tf.ModelParams.from_preset("pt-v21"))
    assert _floats(replayed.prices()) == _floats(e.prices())
    assert replayed.shares_outstanding() == e.shares_outstanding()


def test_the_listed_index_keeps_its_level_across_a_write():
    e = _engine(index_level_listed=1.0)
    e.run_days(2, record=False)
    e.open_market()
    e.run_session(9, 30, 3, 10)
    before = e.index_level
    e.set_shares_outstanding(_moved(e))
    after = e.index_level
    assert after["level"] == pytest.approx(before["level"], rel=1e-12)
    assert after["divisor"] != before["divisor"]
