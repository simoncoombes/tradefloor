"""The business cycle in the market factor's volatility (bear-dynamics).

`market_vol_cycle_ratio` sets the market factor's volatility in a TRUE
contraction or trough over its volatility in every other phase;
`market_vol_cycle_expansion` is the multiplier outside them (0.0 derives it
from the stationary phase shares), `market_vol_cycle_half_life` smooths the
log multiplier toward its phase's value, and `market_vol_cycle_relative` is
the power of the multiplier the VIX's reading of fear is scaled by
(rust/src/params.rs, `ModelParams::market_vol_cycle_ratio`).

All four are 0.0 on every shipped preset and change nothing there; the
known-answer digests hold that. These tests hold that they are off on every
preset, that the ratio and its companions move the market when set, that the
multiplier is carried by the snapshot and both state hashes only while set
and a restore reproduces the run, and the validation. The arithmetic (the
target by phase, the half-life, the derived multiplier's share-weighted
variance, a free close's scaling and a forced close's pass-through) is held
by the Rust tests in engine.rs, module `market_vol_cycle`.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("market_vol_cycle_ratio", "market_vol_cycle_expansion",
         "market_vol_cycle_half_life", "market_vol_cycle_relative")
CYCLE = {"market_vol_cycle_ratio": 2.25, "market_vol_cycle_expansion": 0.75,
         "market_vol_cycle_half_life": 21.0, "market_vol_cycle_relative": 1.0}


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


def test_the_companions_are_unread_without_the_ratio():
    assert prices(market_vol_cycle_expansion=0.75, market_vol_cycle_half_life=21.0,
                  market_vol_cycle_relative=1.0) == prices()


@pytest.mark.parametrize("dials", [
    {"market_vol_cycle_ratio": 2.0},
    CYCLE,
    {**CYCLE, "market_vol_cycle_relative": 0.0},
], ids=["ratio_derived", "design_centre", "relative_zero"])
def test_each_moves_the_market(dials):
    assert prices(**dials) != prices()


def test_an_expansion_multiplier_under_one_calms_an_expansion():
    # A 60-session history on the default opening (an expansion at seed 7)
    # with the multiplier at 0.75 and the ratio at 2.25: the index's daily
    # moves are smaller than the off engine's.
    import statistics

    def index_moves(**dials):
        e = engine(**dials)
        shares = [u.shares_outstanding for u in UNIVERSE]
        caps = []
        for day in range(60):
            e.run_days(1, record=False, first_day=day)
            caps.append(sum(p * s for p, s in zip(floats(e.prices()), shares)))
        assert e.state_snapshot()["economy"]["cycle_phase"] in (
            "expansion", "peak", "recovery", "contraction", "trough")
        return [b / a - 1.0 for a, b in zip(caps, caps[1:])]

    on = index_moves(**CYCLE)
    off = index_moves()
    assert statistics.pstdev(on[20:]) < statistics.pstdev(off[20:])


def test_the_multiplier_is_carried_only_while_set_and_a_restore_reproduces_the_run():
    assert "market_vol_cycle_log" not in engine().state_snapshot()
    assert "market_vol_cycle_log" not in engine(
        market_vol_cycle_half_life=21.0).state_snapshot()
    e = engine(**CYCLE)
    # Unset until the first close puts it on its phase's value.
    assert "market_vol_cycle_log" not in e.state_snapshot()
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert snap["market_vol_cycle_log"] != 0.0
    # The Python mirror of the state hash covers it, in the engine's order.
    assert state_hash(snap) == e.state_hash()
    twin = engine(**CYCLE)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    # The state hash reads it: the same engine with the multiplier nudged
    # hashes differently.
    other = engine(**CYCLE)
    other.restore_state(dict(snap, market_vol_cycle_log=snap["market_vol_cycle_log"] + 0.01))
    fresh = engine(**CYCLE)
    fresh.restore_state(snap)
    assert other.state_hash() != fresh.state_hash()


def test_a_snapshot_without_the_multiplier_restores_it_unset():
    e = engine(**CYCLE)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    stripped = {k: v for k, v in snap.items() if k != "market_vol_cycle_log"}
    twin = engine(**CYCLE)
    twin.run_days(3, record=False)
    twin.restore_state(stripped)
    assert "market_vol_cycle_log" not in twin.state_snapshot()
    # The next close sets it again, on its phase's value.
    twin.run_days(1, record=False, first_day=6)
    assert "market_vol_cycle_log" in twin.state_snapshot()


def test_a_snapshot_without_the_multiplier_diverges_after_a_turn():
    # Off its target only after the true phase turns: pinned into a
    # contraction, the multiplier is part way to it after three closes at a
    # 21-session half-life, and an engine restored without it restarts on
    # the target instead.
    e = engine(**CYCLE)
    e.run_days(3, record=False)
    e.pin_macro(cycle="contraction")
    e.run_days(3, record=False, first_day=3)
    snap = e.state_snapshot()
    assert snap["economy"]["cycle_phase"] == "contraction"
    whole = engine(**CYCLE)
    whole.restore_state(snap)
    stripped = engine(**CYCLE)
    stripped.restore_state({k: v for k, v in snap.items() if k != "market_vol_cycle_log"})
    for x in (whole, stripped):
        x.run_days(3, record=False, first_day=6)
    assert floats(whole.prices()) != floats(stripped.prices())
    e.run_days(3, record=False, first_day=6)
    assert floats(whole.prices()) == floats(e.prices())


def test_the_state_hash_is_unchanged_while_off():
    e = engine()
    e.run_days(3, record=False)
    assert state_hash(e.state_snapshot()) == e.state_hash()


@pytest.mark.parametrize("dials", [
    {"market_vol_cycle_ratio": -1.0},
    {"market_vol_cycle_ratio": 6.0},
    {"market_vol_cycle_expansion": 2.5},
    {"market_vol_cycle_expansion": -0.5},
    {"market_vol_cycle_half_life": -1.0},
    {"market_vol_cycle_half_life": 3000.0},
    {"market_vol_cycle_relative": -0.1},
    {"market_vol_cycle_relative": 1.5},
])
def test_the_ranges(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)
