"""The market factor's fast return memory (`market_vol_leverage_fast`).

A second exponentially weighted memory of the day factor, beside
`market_vol_leverage`'s, at its own half-life
(`market_vol_leverage_fast_half_life`); the variance the next session draws
with is the mixture times both memories' multipliers, and the VIX's read-back
takes the fast one at its mean over the VIX's 21-session window.

0.0 on every shipped preset and changes nothing there; the known-answer digests
hold that. These tests hold that it is off on every preset, that it moves the
market when set, that its memory is carried by the snapshot and both state
hashes only while it is set and a restore reproduces the run, and the
validation. The arithmetic (the sign of the response, the half-life, the
window's mean, a forced close) is held by the Rust tests in `factor_vol.rs`,
module `fast_leverage`.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("market_vol_leverage_fast", "market_vol_leverage_fast_half_life")
FAST = {"market_vol_leverage_fast": 2.0, "market_vol_leverage_fast_half_life": 5.0}
KEY = "market_vol_leverage_fast_memory"


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(preset="pt-v20", **dials):
    return tf.Engine(seed=7, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset(preset, **dials))


def prices(days=15, preset="pt-v20", **dials):
    e = engine(preset, **dials)
    e.run_days(days, record=False)
    return floats(e.prices())


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert all(values[name] == 0.0 for name in DIALS)


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v21"])
def test_setting_them_to_zero_changes_nothing(preset):
    assert prices(preset=preset, **{name: 0.0 for name in DIALS}) == prices(preset=preset)


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v21"])
def test_it_moves_the_market(preset):
    assert prices(preset=preset, **FAST) != prices(preset=preset)


def test_the_half_life_is_unread_without_the_gain():
    assert prices(market_vol_leverage_fast_half_life=5.0) == prices()


def test_the_vix_moves_with_it():
    e, f = engine(), engine(**FAST)
    for x in (e, f):
        x.run_days(15, record=False)
    assert e.macro_fields["vix"] != f.macro_fields["vix"]


def test_the_memory_is_carried_only_while_set_and_a_restore_reproduces_the_run():
    assert KEY not in engine().state_snapshot()
    assert KEY not in engine(market_vol_leverage_fast_half_life=5.0).state_snapshot()
    e = engine(**FAST)
    assert e.state_snapshot()[KEY] == 0.0
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert snap[KEY] != 0.0
    # The Python mirror of the state hash covers it, in the engine's order.
    assert state_hash(snap) == e.state_hash()
    twin = engine(**FAST)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    # The state hash reads it.
    other = engine(**FAST)
    other.restore_state(dict(snap, **{KEY: snap[KEY] + 0.01}))
    fresh = engine(**FAST)
    fresh.restore_state(snap)
    assert other.state_hash() != fresh.state_hash()


def test_both_memories_together_restore():
    dials = dict(FAST, market_vol_leverage=2.25, market_vol_leverage_half_life=15.0,
                 market_vol_leverage_standardise=1.0)
    e = engine(**dials)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert snap[KEY] != 0.0 and snap["market_vol_leverage_memory"] != 0.0
    assert state_hash(snap) == e.state_hash()
    twin = engine(**dials)
    twin.restore_state(snap)
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())


def test_a_snapshot_without_the_memory_is_refused_by_name():
    e = engine(**FAST)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    stripped = {k: v for k, v in snap.items() if k != KEY}
    twin = engine(**FAST)
    twin.run_days(3, record=False)
    with pytest.raises(tf.ValidationError, match=f"{KEY}.*market_vol_leverage_fast"):
        twin.restore_state(stripped)


def test_the_forecast_carries_it_and_is_a_pure_function_of_the_state():
    dials = dict(FAST, forecast_horizon_sessions=63.0)
    e = engine(**dials)
    e.run_days(6, record=False)
    a = e.forecast()
    assert a is not None
    twin = engine(**dials)
    twin.restore_state(e.state_snapshot())
    assert twin.forecast() == a
    off = engine(forecast_horizon_sessions=63.0)
    off.run_days(6, record=False)
    assert off.forecast() != a


@pytest.mark.parametrize("dials, match", [
    ({"market_vol_leverage_fast": 2.0}, "market_vol_leverage_fast_half_life"),
    ({"market_vol_leverage_fast": -1.0, "market_vol_leverage_fast_half_life": 5.0},
     "market_vol_leverage_fast"),
    ({"market_vol_leverage_fast": 51.0, "market_vol_leverage_fast_half_life": 5.0},
     "market_vol_leverage_fast"),
    ({"market_vol_leverage_fast_half_life": -1.0}, "market_vol_leverage_fast_half_life"),
])
def test_validation(dials, match):
    with pytest.raises(tf.ValidationError, match=match):
        tf.ModelParams.from_preset("pt-v20", **dials)
