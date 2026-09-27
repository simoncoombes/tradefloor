"""The market factor's variance response to a fall (crash-vol-state).

Two dial families on `MarketVarianceState` (rust/src/market/factor_vol.rs):

- `market_vol_slow_gamma`, GJR asymmetry on the slow variance component with
  its persistence kept, and
- the return memory: `market_vol_leverage`, `market_vol_leverage_half_life`
  and `market_vol_leverage_down`, an exponentially weighted memory of the day
  factor that multiplies the variance the next session draws with.

Both are 0.0 on every shipped preset and change nothing there; the known-answer
digests hold that. These tests hold that they are off on every preset, that
each moves the market when set, that the return memory is carried by the
snapshot and both state hashes only while it is set and a restore reproduces
the run, and the validation. The arithmetic (a fall raises and a rise lowers
the next session's variance, the half-life, the multiplier's mean, the slow
component's persistence) is held by the Rust tests in `factor_vol.rs`,
module `crash_vol_state`.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("market_vol_slow_gamma", "market_vol_leverage",
         "market_vol_leverage_half_life", "market_vol_leverage_down")
MEMORY = {"market_vol_leverage": 3.0, "market_vol_leverage_half_life": 40.0}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(**dials):
    return tf.Engine(seed=7, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def prices(days=15, **dials):
    e = engine(**dials)
    e.run_days(days, record=False)
    return floats(e.prices())


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert all(values[name] == 0.0 for name in DIALS)


def test_setting_them_to_zero_changes_nothing():
    assert prices(**{name: 0.0 for name in DIALS}) == prices()


@pytest.mark.parametrize("dials", [
    {"market_vol_slow_gamma": 0.6},
    MEMORY,
    {**MEMORY, "market_vol_leverage_down": 1.0},
], ids=["slow_gamma", "memory", "memory_down"])
def test_each_moves_the_market(dials):
    assert prices(**dials) != prices()


def test_the_half_life_and_the_down_share_are_unread_without_the_memory():
    assert prices(market_vol_leverage_half_life=40.0,
                  market_vol_leverage_down=1.0) == prices()


def test_the_memory_is_carried_only_while_set_and_a_restore_reproduces_the_run():
    assert "market_vol_leverage_memory" not in engine().state_snapshot()
    assert "market_vol_leverage_memory" not in engine(
        market_vol_leverage_half_life=40.0).state_snapshot()
    e = engine(**MEMORY)
    assert e.state_snapshot()["market_vol_leverage_memory"] == 0.0
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert snap["market_vol_leverage_memory"] != 0.0
    # The Python mirror of the state hash covers it, in the engine's order.
    assert state_hash(snap) == e.state_hash()
    twin = engine(**MEMORY)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    # The state hash reads it: the same engine with the memory nudged
    # hashes differently.
    other = engine(**MEMORY)
    nudged = dict(snap, market_vol_leverage_memory=snap["market_vol_leverage_memory"] + 0.01)
    other.restore_state(nudged)
    fresh = engine(**MEMORY)
    fresh.restore_state(snap)
    assert other.state_hash() != fresh.state_hash()


def test_a_snapshot_without_the_memory_restores_it_to_zero():
    e = engine(**MEMORY)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    stripped = {k: v for k, v in snap.items() if k != "market_vol_leverage_memory"}
    twin = engine(**MEMORY)
    twin.run_days(3, record=False)
    twin.restore_state(stripped)
    assert twin.state_snapshot()["market_vol_leverage_memory"] == 0.0


def test_the_state_hash_is_unchanged_while_off():
    # Off, the snapshot carries no new key and the mirror still covers
    # exactly what the engine hashes.
    e = engine()
    e.run_days(3, record=False)
    assert state_hash(e.state_snapshot()) == e.state_hash()


@pytest.mark.parametrize("dials", [
    {"market_vol_slow_gamma": -0.1},
    {"market_vol_slow_gamma": 1.5},
    # pt-v20's slow component carries (1 - 0.05) * 0.9913 of its persistence
    # on its level, so a gamma over twice that is refused.
    {"market_vol_slow_gamma": 1.0, "market_vol_slow_persistence": 0.5},
    {"market_vol_leverage": -1.0, "market_vol_leverage_half_life": 40.0},
    {"market_vol_leverage": 60.0, "market_vol_leverage_half_life": 40.0},
    {"market_vol_leverage": 3.0},
    {"market_vol_leverage_half_life": -1.0},
    {"market_vol_leverage_half_life": 3000.0},
    {"market_vol_leverage_down": 1.5},
    {"market_vol_leverage_down": -0.5},
])
def test_the_ranges(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)
