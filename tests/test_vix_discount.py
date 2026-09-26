"""Volatility feedback on fair value (`fair_value_vix_discount`, `fair_value_vix_knee`).

While the VIX is above the knee, every name's fair value is scaled by
`exp(-gain * beta * ln(vix / knee))`: a discount that is there while fear is
high and goes as the VIX falls, with no state of its own. These tests hold
that it is off on every shipped preset, that it reads nothing below the knee,
that a VIX held above the knee lowers prices by the formula on a market with
nothing else moving, and that the discount is given back when the VIX falls.
"""

import math
import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(gain, **extra):
    return tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset(
        "pt-v20", fair_value_vix_discount=gain, **extra))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["fair_value_vix_discount"] == 0.0


def test_nothing_moves_below_the_knee():
    # A knee above any VIX the market reaches: the gain reads nothing.
    runs = []
    for gain in (0.0, 0.3):
        e = engine(gain, fair_value_vix_knee=200.0)
        e.run_days(20, record=False)
        runs.append(floats(e.prices()))
    assert runs[0] == runs[1]


def test_a_high_vix_discounts_by_the_formula_and_gives_it_back():
    # Pin the VIX above the knee for a day, then back below it. On the same
    # draws, the discounted market sits below the undiscounted one by about
    # beta_i * gain * ln(vix / knee) in log price while the VIX is high (the
    # price is fair value times exp(s)), and the gap closes
    # once the VIX is back under the knee.
    gain, knee, vix = 0.2, 30.0, 60.0
    engines = [engine(0.0, macro_publication_repricing=1.0),
               engine(gain, macro_publication_repricing=1.0)]
    for e in engines:
        e.run_days(3, record=False)
        e.pin_macro(vix=vix)
        e.run_days(1, record=False, first_day=3)
    a, b = (floats(e.prices()) for e in engines)
    now = engines[1].state_snapshot()["economy"]["vix"]
    assert now > knee
    betas = [ins.beta for ins in UNIVERSE]
    gaps = [math.log(y / x) for x, y in zip(a, b)]
    # The VIX the close left (the pin holds for the session; the close steps
    # it) prices the discount at the close's re-mark. The buyback term reads
    # the lower price as a higher yield and hands a little back, so the gap
    # is the formula to within a fifth.
    want = [-gain * beta * math.log(now / knee) for beta in betas]
    for g, w in zip(gaps, want):
        assert g < 0.0
        assert g == pytest.approx(w, rel=0.2)
    for e in engines:
        e.pin_macro(vix=15.0)
        e.run_days(1, record=False, first_day=4)
    a, b = (floats(e.prices()) for e in engines)
    after = [abs(math.log(y / x)) for x, y in zip(a, b)]
    assert max(after) < 0.25 * max(abs(w) for w in want)


@pytest.mark.parametrize("name,value", [("fair_value_vix_discount", -0.1),
                                        ("fair_value_vix_discount", 1.5),
                                        ("fair_value_vix_knee", 0.0)])
def test_the_ranges(name, value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **{name: value})


def test_the_smoothed_exposure_builds_and_decays_and_is_carried_only_while_set():
    # With a half-life the discount reads a level the close pulls toward the
    # VIX's log excess: after one held session it is a fraction of the
    # excess, it keeps building while the VIX stays high, and the snapshot
    # carries it (and only then); a restore reproduces the market.
    gain, knee, vix, h = 0.2, 30.0, 60.0, 5.0
    e = engine(gain, fair_value_vix_half_life=h)
    assert "vix_feedback" in e.state_snapshot()["economy"]
    assert "vix_feedback" not in engine(gain).state_snapshot()["economy"]
    assert "vix_feedback" not in engine(0.0, fair_value_vix_half_life=h).state_snapshot()["economy"]
    e.run_days(2, record=False)
    levels = []
    for day in range(2, 8):
        e.pin_macro(vix=vix)
        e.run_days(1, record=False, first_day=day)
        levels.append(e.state_snapshot()["economy"]["vix_feedback"])
    excess = math.log(vix / knee)
    assert 0.0 < levels[0] < 0.5 * excess
    assert all(b > a for a, b in zip(levels, levels[1:]))
    assert levels[-1] < excess
    snap = e.state_snapshot()
    twin = engine(gain, fair_value_vix_half_life=h)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(3, record=False, first_day=8)
    assert floats(e.prices()) == floats(twin.prices())


def test_the_smoothed_discount_moves_prices_less_on_the_day_than_the_direct_one():
    # The same VIX spike: the direct form takes the whole discount at the
    # close, the smoothed one a fraction of it.
    gaps = {}
    for h in (0.0, 10.0):
        pair = [engine(0.0, macro_publication_repricing=1.0),
                engine(0.2, macro_publication_repricing=1.0, fair_value_vix_half_life=h)]
        for x in pair:
            x.run_days(3, record=False)
            x.pin_macro(vix=60.0)
            x.run_days(1, record=False, first_day=3)
        a, b = (floats(x.prices()) for x in pair)
        gaps[h] = max(abs(math.log(y / x)) for x, y in zip(a, b))
    assert gaps[10.0] < 0.25 * gaps[0.0]
