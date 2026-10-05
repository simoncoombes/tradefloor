"""The business cycle in the market factor's volatility (bear-dynamics).

`market_vol_cycle_ratio` sets the market factor's volatility in a TRUE
contraction or trough over its volatility in every other phase;
`market_vol_cycle_expansion` is the multiplier outside them (0.0 derives it
from the stationary phase shares), `market_vol_cycle_half_life` smooths the
log multiplier toward its phase's value, and `market_vol_cycle_relative` and
`market_vol_cycle_relative_calm` are the powers of the multiplier the VIX's
reading of fear is scaled by, at or over one and under one
(rust/src/params.rs, `ModelParams::market_vol_cycle_ratio`).

All five are 0.0 on every shipped preset and change nothing there; the
known-answer digests hold that. These tests hold that they are off on every
preset, that the ratio and its companions move the market when set, that the
multiplier is carried by the snapshot and both state hashes only while set
and a restore reproduces the run, and the validation. The arithmetic (the
target by phase, the half-life, the derived multiplier's share-weighted
variance, the VIX scale's two powers and its floor, the anchor memory's and
the anchor level's scaling, a free close's scaling and a forced close's
pass-through) is held by the Rust tests in engine.rs, module
`market_vol_cycle`.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("market_vol_cycle_ratio", "market_vol_cycle_expansion",
         "market_vol_cycle_half_life", "market_vol_cycle_relative",
         "market_vol_cycle_relative_calm", "market_vol_cycle_cap_relative",
         "market_vol_cycle_pin_neutral", "market_vol_cycle_pin_phase",
         "market_vol_cycle_trough_release", "market_vol_cycle_release_half_life",
         "market_vol_cycle_recovery_release", "market_vol_cycle_recovery_scale")
# The setting the bear-dynamics fix recommends for pt-v20.
CYCLE = {"market_vol_cycle_ratio": 2.5, "market_vol_cycle_expansion": 0.8,
         "market_vol_cycle_half_life": 21.0, "market_vol_cycle_relative": 0.75,
         "market_vol_cycle_relative_calm": 0.0, "market_vol_cycle_cap_relative": 1.0}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(**dials):
    return tf.Engine(seed=7, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def prices(days=15, **dials):
    e = engine(**dials)
    e.run_days(days, record=False)
    return floats(e.prices())


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert all(values[name] == 0.0 for name in DIALS)


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "market_vol_cycle_ratio",
        "market_vol_cycle_expansion",
        "market_vol_cycle_half_life",
        "market_vol_cycle_relative",
        "market_vol_cycle_relative_calm",
        "market_vol_cycle_cap_relative",
        "market_vol_cycle_pin_neutral",
        "market_vol_cycle_pin_phase",
        "market_vol_cycle_trough_release",
        "market_vol_cycle_release_half_life",
        "market_vol_cycle_recovery_release",
        "market_vol_cycle_recovery_scale",
    )} == {
        "market_vol_cycle_ratio": 2.4705882352941178,
        "market_vol_cycle_expansion": 0.82,
        "market_vol_cycle_half_life": 10.0,
        "market_vol_cycle_relative": 0.75,
        "market_vol_cycle_relative_calm": 0.0,
        "market_vol_cycle_cap_relative": 1.0,
        "market_vol_cycle_pin_neutral": 1.0,
        "market_vol_cycle_pin_phase": 1.0,
        "market_vol_cycle_trough_release": 0.0,
        "market_vol_cycle_release_half_life": 0.0,
        "market_vol_cycle_recovery_release": 0.45,
        "market_vol_cycle_recovery_scale": 0.1,
    }


def test_setting_them_to_zero_changes_nothing():
    assert prices(**{name: 0.0 for name in DIALS}) == prices()


def test_the_companions_are_unread_without_the_ratio():
    assert prices(market_vol_cycle_expansion=0.75, market_vol_cycle_half_life=21.0,
                  market_vol_cycle_relative=1.0,
                  market_vol_cycle_relative_calm=1.0,
                  market_vol_cycle_cap_relative=1.0,
                  market_vol_cycle_pin_neutral=1.0,
                  market_vol_cycle_pin_phase=1.0,
                  market_vol_cycle_trough_release=1.0,
                  market_vol_cycle_release_half_life=5.0,
                  market_vol_cycle_recovery_release=1.0,
                  market_vol_cycle_recovery_scale=0.1) == prices()


# The bearcycle fix's switches and release (sim/r15-bearcycle).
PINS = {"market_vol_cycle_pin_neutral": 1.0, "market_vol_cycle_pin_phase": 1.0}


def test_the_pin_switches_change_nothing_on_a_run_nothing_pins():
    assert prices(days=30, **CYCLE) == prices(days=30, **CYCLE, **PINS)


def pinned_prices(days=30, vix=None, cycle=None, **dials):
    e = engine(**dials)
    for day in range(days):
        pins = {}
        if vix is not None:
            pins["vix"] = vix
        if cycle is not None:
            pins["cycle"] = cycle
        e.pin_macro(**pins)
        e.run_days(1, record=False, first_day=day)
    return floats(e.prices()), e.state_snapshot().get("market_vol_cycle_log")


def test_a_run_whose_every_vix_is_pinned_prices_as_the_model_without_the_cycle():
    # The first close's step lands on the target, which a pin makes one,
    # and nothing the multiplier scales reads anything but one after it:
    # the prices are the cycle-off engine's to the bit. Without the switch
    # the cycle's expansion multiplier moves them.
    off, _ = pinned_prices(vix=35.0)
    on, log = pinned_prices(vix=35.0, **CYCLE, market_vol_cycle_pin_neutral=1.0)
    assert on == off and log == 0.0
    plain, plain_log = pinned_prices(vix=35.0, **CYCLE)
    assert plain != off and plain_log != 0.0


def test_a_run_whose_every_phase_is_pinned_prices_as_the_model_without_the_cycle():
    off, _ = pinned_prices(cycle="contraction")
    on, log = pinned_prices(cycle="contraction", **CYCLE, market_vol_cycle_pin_phase=1.0)
    assert on == off and log == 0.0
    # A phase pin does not trigger the VIX switch, nor a VIX pin the phase's.
    assert pinned_prices(cycle="contraction", **CYCLE,
                         market_vol_cycle_pin_neutral=1.0)[0] != off
    off_v, _ = pinned_prices(vix=35.0)
    assert pinned_prices(vix=35.0, **CYCLE, market_vol_cycle_pin_phase=1.0)[0] != off_v


def test_a_released_multiplier_steps_from_one_toward_its_phase():
    # Ten pinned sessions, then free: the multiplier leaves one at its
    # half-life rather than jumping to the phase's value.
    e = engine(**CYCLE, **PINS)
    for day in range(10):
        e.pin_macro(vix=30.0)
        e.run_days(1, record=False, first_day=day)
    assert e.state_snapshot()["market_vol_cycle_log"] == 0.0
    e.run_days(1, record=False, first_day=10)
    l = e.state_snapshot()["market_vol_cycle_log"]
    phase = e.state_snapshot()["economy"]["cycle_phase"]
    import math
    k = CYCLE["market_vol_cycle_expansion"] * (
        CYCLE["market_vol_cycle_ratio"] if phase in ("contraction", "trough") else 1.0)
    a = 1.0 - math.exp(-math.log(2.0) / CYCLE["market_vol_cycle_half_life"])
    assert abs(l - a * math.log(k)) < 1e-12


def test_the_trough_release_and_the_release_half_life_move_a_turning_market():
    # Pinned into a contraction for 15 sessions and then into a trough and
    # a recovery: the multiplier falls, so the release half-life moves the
    # market, and the trough's target is read, so its release does.
    def run(**dials):
        e = engine(**CYCLE, **dials)
        e.run_days(3, record=False)
        e.pin_macro(cycle="contraction")
        e.run_days(15, record=False, first_day=3)
        e.pin_macro(cycle="trough")
        e.run_days(10, record=False, first_day=18)
        e.pin_macro(cycle="recovery")
        e.run_days(10, record=False, first_day=28)
        return floats(e.prices())
    base = run()
    assert run(market_vol_cycle_release_half_life=5.0) != base
    assert run(market_vol_cycle_trough_release=1.0) != base


# The rally off the low (sim/r20-mktrelease).
RALLY = {"market_vol_cycle_recovery_release": 1.0, "market_vol_cycle_recovery_scale": 0.1}


def test_the_rally_release_moves_a_contraction_and_keeps_its_window():
    # Pinned into a contraction for 20 sessions: the index rallies off a
    # low on some session, the target is read on the rally and the market
    # moves; held in an expansion the release is never read.
    def run(pin, **dials):
        e = engine(**CYCLE, **dials)
        e.run_days(3, record=False)
        e.pin_macro(cycle=pin)
        e.run_days(20, record=False, first_day=3)
        return floats(e.prices()), e
    base, _ = run("contraction")
    on, e = run("contraction", **RALLY)
    assert on != base
    assert run("expansion", **RALLY)[0] == run("expansion")[0]
    # The window is kept with the release alone, as with fed_drawdown_hold.
    snap = e.state_snapshot()
    assert "fed_drawdown_returns" in snap and "fed_drawdown_mcap_prev" in snap
    assert "fed_drawdown_returns" not in engine(**CYCLE).state_snapshot()


def test_the_rally_window_is_hashed_and_a_restore_reproduces_the_run():
    e = engine(**CYCLE, **RALLY)
    e.run_days(3, record=False)
    e.pin_macro(cycle="contraction")
    e.run_days(10, record=False, first_day=3)
    snap = e.state_snapshot()
    assert state_hash(snap) == e.state_hash()
    twin = engine(**CYCLE, **RALLY)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(5, record=False, first_day=13)
    assert floats(e.prices()) == floats(twin.prices())
    # An engine with neither the release nor fed_drawdown_hold refuses it.
    with pytest.raises(Exception):
        engine(**CYCLE).restore_state(snap)


@pytest.mark.parametrize("dials", [
    {"market_vol_cycle_ratio": 2.0},
    CYCLE,
    {**CYCLE, "market_vol_cycle_relative": 0.0},
    {**CYCLE, "market_vol_cycle_relative_calm": 1.0},
], ids=["ratio_derived", "centre", "relative_zero", "calm_one"])
def test_each_moves_the_market(dials):
    assert prices(**dials) != prices()


def test_the_calm_power_moves_a_calm_phase():
    # Under one outside a contraction, so the calm-side power is read there.
    assert prices(**CYCLE) != prices(**{**CYCLE, "market_vol_cycle_relative_calm": 0.5})


def test_the_cap_power_moves_a_stormy_phase_only():
    # Over one (an expansion multiplier of 2, read at power 0 so the fear
    # loop lets the factor's sigma rise past the cap's unscaled ceiling
    # inside 15 sessions) the ceiling is scaled and the market moves; under
    # one (0.75 outside a contraction) it is not, and the run is
    # bit-identical.
    stormy = {**CYCLE, "market_vol_cycle_ratio": 1.0, "market_vol_cycle_expansion": 2.0,
              "market_vol_cycle_relative": 0.0, "market_vol_cycle_half_life": 0.0,
              "market_vol_cycle_cap_relative": 0.0}
    assert prices(**stormy) != prices(**{**stormy, "market_vol_cycle_cap_relative": 1.0})
    calm = {**CYCLE, "market_vol_cycle_ratio": 1.0, "market_vol_cycle_expansion": 0.75,
            "market_vol_cycle_cap_relative": 0.0}
    assert prices(**calm) == prices(**{**calm, "market_vol_cycle_cap_relative": 1.0})


def index_moves(days=60, pin=None, **dials):
    # The cap-weighted index's daily moves and the true phase each session.
    e = engine(**dials)
    if pin:
        e.pin_macro(cycle=pin)
    shares = [u.shares_outstanding for u in UNIVERSE]
    caps, phases = [], []
    for day in range(days):
        e.run_days(1, record=False, first_day=day)
        caps.append(sum(p * s for p, s in zip(floats(e.prices()), shares)))
        phases.append(e.state_snapshot()["economy"]["cycle_phase"])
    return [b / a - 1.0 for a, b in zip(caps, caps[1:])], phases


@pytest.mark.parametrize("calm", [0.0, 1.0])
def test_an_expansion_multiplier_under_one_calms_an_expansion(calm):
    # A 60-session history held in an expansion (seed 7 turns to a
    # contraction inside 60 sessions unpinned), with the multiplier at 0.8:
    # the index's daily moves are smaller than the off engine's, at either
    # calm-side power.
    import statistics

    on, phases = index_moves(pin="expansion", **{**CYCLE, "market_vol_cycle_relative_calm": calm})
    off, off_phases = index_moves(pin="expansion")
    assert set(phases) == set(off_phases) == {"expansion"}
    assert statistics.pstdev(on[20:]) < statistics.pstdev(off[20:])


def test_a_calm_power_of_zero_calms_an_expansion_further_than_one():
    # At power 0 the coupling's reference is not lowered with the variance,
    # so the fear loop reads the calm as calm and deepens it; at power 1 it
    # reads fear against the lowered level and does not.
    import statistics

    zero, _ = index_moves(pin="expansion", **{**CYCLE, "market_vol_cycle_relative_calm": 0.0})
    one, _ = index_moves(pin="expansion", **{**CYCLE, "market_vol_cycle_relative_calm": 1.0})
    assert statistics.pstdev(zero[20:]) < statistics.pstdev(one[20:])


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
    {"market_vol_cycle_relative_calm": -0.1},
    {"market_vol_cycle_relative_calm": 1.5},
    {"market_vol_cycle_cap_relative": -0.1},
    {"market_vol_cycle_cap_relative": 1.5},
    {"market_vol_cycle_pin_neutral": 0.5},
    {"market_vol_cycle_pin_neutral": -1.0},
    {"market_vol_cycle_pin_phase": 0.5},
    {"market_vol_cycle_pin_phase": 2.0},
    {"market_vol_cycle_trough_release": -0.1},
    {"market_vol_cycle_trough_release": 1.5},
    {"market_vol_cycle_release_half_life": -1.0},
    {"market_vol_cycle_release_half_life": 3000.0},
    {"market_vol_cycle_recovery_release": -0.1, "market_vol_cycle_recovery_scale": 0.1},
    {"market_vol_cycle_recovery_release": 1.5, "market_vol_cycle_recovery_scale": 0.1},
    {"market_vol_cycle_recovery_release": 1.0},
    {"market_vol_cycle_recovery_release": 1.0, "market_vol_cycle_recovery_scale": 2.5},
    {"market_vol_cycle_recovery_scale": -0.1},
    {"market_vol_cycle_recovery_scale": 3.0},
])
def test_the_ranges(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)
