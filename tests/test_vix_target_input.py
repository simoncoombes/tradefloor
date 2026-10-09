"""The host's input to the VIX target (#275): a premium that fades on its
own half-life, and a floor held until cleared.

The Rust side holds the market's response (`rust/tests/vix_target_input.rs`).
This file holds what crosses into Python: the bindings and their refusals,
the run log and its replay, the snapshot through its lossless JSON form
with `manifest.state_hash` agreeing with the engine's, and the flow tally.
"""
from __future__ import annotations

import json
import pathlib
import struct
import sys

import pytest

import tradefloor as tf
from tradefloor import envelope as env
from tradefloor.manifest import state_hash

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import snapshot_codec  # noqa: E402

UNIVERSE = tf.Universe.random(6, seed=5)
TICKS = 78


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def engine(seed=3):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v21"))


def vix(e):
    return e.economy()["vix"]


def test_the_bindings_read_back_what_they_wrote():
    e = engine()
    assert e.vix_target_premium() == (0.0, 0.0)
    assert e.vix_target_floor() is None
    e.set_vix_target_premium(4.0, 13.0)
    e.set_vix_target_floor(22.5)
    assert e.vix_target_premium() == (4.0, 13.0)
    assert e.vix_target_floor() == 22.5
    e.set_vix_target_premium(3.0)
    assert e.vix_target_premium() == (3.0, 0.0)
    e.set_vix_target_premium(0.0, 9.0)
    assert e.vix_target_premium() == (0.0, 0.0)
    e.set_vix_target_floor(None)
    assert e.vix_target_floor() is None


@pytest.mark.parametrize("points, half_life", [
    (float("nan"), 10.0), (float("inf"), 10.0), (2.0, -1.0), (2.0, float("inf")),
])
def test_a_premium_that_is_not_one_is_refused(points, half_life):
    e = engine()
    with pytest.raises(tf.ValidationError, match="premium"):
        e.set_vix_target_premium(points, half_life)
    assert e.vix_target_premium() == (0.0, 0.0)


@pytest.mark.parametrize("floor", [0.0, -3.0, float("nan"), float("inf")])
def test_a_floor_that_is_not_one_is_refused(floor):
    e = engine()
    with pytest.raises(tf.ValidationError, match="floor"):
        e.set_vix_target_floor(floor)
    assert e.vix_target_floor() is None


def test_a_premium_lifts_the_vix_and_fades_on_its_half_life():
    base, lifted = engine(), engine()
    for e in (base, lifted):
        e.run_days(3, record=False, ticks_per_day=TICKS)
    lifted.set_vix_target_premium(6.0, 13.0)
    b, p = [], []
    for _ in range(26):
        for e, out in ((base, b), (lifted, p)):
            e.run_days(1, record=False, ticks_per_day=TICKS)
            out.append(vix(e))
    assert sum(p) / len(p) > sum(b) / len(b) + 2.0
    points, half_life = lifted.vix_target_premium()
    assert half_life == 13.0
    assert points == pytest.approx(6.0 * 0.25, rel=1e-12)


def test_the_log_carries_the_input_and_a_replay_reproduces_the_run():
    e = engine(seed=7)
    e.run_days(2, record=False, ticks_per_day=TICKS)
    e.set_vix_target_premium(5.0, 8.0)
    e.run_days(2, record=False, ticks_per_day=TICKS)
    e.set_vix_target_floor(24.0)
    e.run_days(2, record=False, ticks_per_day=TICKS)
    e.set_vix_target_floor(None)
    e.run_days(1, record=False, ticks_per_day=TICKS)
    ops = [x["op"] for x in e.order_log]
    assert ops.count("set_vix_target_premium") == 1
    assert ops.count("set_vix_target_floor") == 2
    log = json.loads(json.dumps(e.order_log, allow_nan=False))
    replayed = tf.replay(log, seed=7, universe=UNIVERSE,
                         model=tf.ModelParams.from_preset("pt-v21"))
    assert arr(replayed.prices()) == arr(e.prices())
    assert vix(replayed) == vix(e)
    assert replayed.vix_target_premium() == e.vix_target_premium()


def _mid_fade():
    e = engine()
    e.run_days(3, record=False, ticks_per_day=TICKS)
    e.set_vix_target_premium(4.0, 13.0)
    e.set_vix_target_floor(21.0)
    e.run_days(3, record=False, ticks_per_day=TICKS)
    e.open_market()
    e.run_session(9, 30, 3, 40)
    return e


def test_a_snapshot_mid_fade_round_trips_through_json_with_the_hash_agreeing():
    original = _mid_fade()
    snapshot = original.state_snapshot()
    assert set(snapshot["vix_target_premium"]) == {"points", "half_life"}
    assert snapshot["vix_target_floor"] == 21.0
    assert state_hash(snapshot) == original.state_hash()
    decoded = snapshot_codec.loads(snapshot_codec.dumps(snapshot))
    resumed = engine(seed=11)
    resumed.restore_state(decoded)
    assert resumed.state_hash() == original.state_hash()
    for e in (original, resumed):
        e.run_session(10, 10, 3, TICKS - 40)
        e.close_market()
        e.run_days(5, record=False, ticks_per_day=TICKS)
    assert arr(resumed.prices()) == arr(original.prices())
    assert resumed.state_hash() == original.state_hash()


def test_a_snapshot_without_the_input_is_the_one_it_was():
    plain, touched = engine(), engine()
    touched.set_vix_target_premium(0.0)
    touched.set_vix_target_floor(None)
    for e in (plain, touched):
        e.run_days(3, record=False, ticks_per_day=TICKS)
    snapshot = touched.state_snapshot()
    assert "vix_target_premium" not in snapshot
    assert "vix_target_floor" not in snapshot
    assert touched.state_hash() == plain.state_hash() == state_hash(snapshot)


def test_the_hash_refuses_a_premium_block_it_does_not_cover():
    snapshot = _mid_fade().state_snapshot()
    snapshot["vix_target_premium"] = {"points": 4.0}
    with pytest.raises(tf.ValidationError, match="vix_target_premium"):
        state_hash(snapshot)


def test_the_flow_tally_counts_the_input_apart_from_vix_writes():
    assert env.external_flow(sessions=100, names=40)
    v = env.external_flow(sessions=100, names=40, vix_target_premiums=[6.0, -2.0])
    assert not v
    assert any("premium on the VIX target" in r for r in v.reasons)
    v = env.external_flow(sessions=100, names=40, vix_target_floor_sessions=30)
    assert not v
    assert any("floor under the VIX target" in r for r in v.reasons)
    with pytest.raises(tf.ValidationError, match="vix_target_floor_sessions"):
        env.external_flow(sessions=100, names=40, vix_target_floor_sessions=-1)
    with pytest.raises(tf.ValidationError, match="vix_target_premiums"):
        env.external_flow(sessions=100, names=40, vix_target_premiums=[float("nan")])
