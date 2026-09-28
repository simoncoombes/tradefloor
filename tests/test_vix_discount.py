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
    # The knee at 30 and the exposure unsmoothed unless a test says
    # otherwise: the defaults these tests were written on. pt-v20 carries
    # a knee of 40 and a 5-session half-life since its graded arm
    # (2026-09-26).
    extra = {"fair_value_vix_knee": 30.0, "fair_value_vix_half_life": 0.0, **extra}
    return tf.Engine(seed=7, universe=UNIVERSE, model=tf.ModelParams.from_preset(
        "pt-v20", fair_value_vix_discount=gain, **extra))


# Every preset through pt-v19. pt-v20 sets fair_value_vix_discount to 0.35 since its graded
# arm (2026-09-26; design repository, programme/ptv20-registration.md),
# which the test below holds. Was parametrized over every preset.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v20"])
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["fair_value_vix_discount"] == 0.0


def test_pt_v20_sets_the_graded_arms_value():
    assert tf.ModelParams.from_preset("pt-v20").to_dict()["fair_value_vix_discount"] == 0.35


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


# ---- fair_value_vix_release_half_life (r16 spike): the discount is built at
# `fair_value_vix_half_life` and given back at this. Off (0.0) everywhere.

@pytest.mark.parametrize("preset", tf.preset_names())
def test_the_release_half_life_is_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["fair_value_vix_release_half_life"] == 0.0


def _exposures(release, pinned=6, after=6, **extra):
    # The VIX pinned at 60 (over the knee of 30) for `pinned` sessions, then
    # at 20 (under it) for `after`: the target falls to zero.
    e = engine(0.2, fair_value_vix_half_life=5.0, fair_value_vix_release_half_life=release, **extra)
    e.run_days(2, record=False)
    out = []
    for day in range(2, 2 + pinned + after):
        e.pin_macro(vix=60.0 if day < 2 + pinned else 20.0)
        e.run_days(1, record=False, first_day=day)
        out.append(e.state_snapshot()["economy"]["vix_feedback"])
    return out, floats(e.prices()), e.state_hash()


def test_the_release_half_life_at_zero_is_the_one_half_life():
    # 0.0 explicit and the dial left alone are the same market to the bit.
    a = _exposures(0.0)
    e = engine(0.2, fair_value_vix_half_life=5.0)
    e.run_days(2, record=False)
    for day in range(2, 14):
        e.pin_macro(vix=60.0 if day < 8 else 20.0)
        e.run_days(1, record=False, first_day=day)
    assert a[1] == floats(e.prices())


def test_the_release_half_life_builds_as_before_and_gives_back_slower():
    # While the pinned VIX holds the target above the exposure, the release
    # half-life is not read: the build is the same to the bit. Once the VIX
    # falls under the knee, the exposure falls slower with a longer release.
    fast, _, _ = _exposures(0.0)
    slow, _, _ = _exposures(60.0)
    assert fast[:6] == slow[:6]
    assert fast[-1] < fast[5]
    assert slow[-1] > fast[-1]
    # The first close under the knee pulls toward a target of zero, so the
    # step keeps 0.5 ** (1 / 60) of the level at a release of 60 and
    # 0.5 ** (1 / 5) at the build's own half-life. (Later closes read the
    # VIX the close computes, which the pin at the open does not fix.)
    assert abs(slow[6] - slow[5] * 0.5 ** (1 / 60)) < 1e-12
    assert abs(fast[6] - fast[5] * 0.5 ** (1 / 5)) < 1e-12


def test_the_release_half_life_survives_a_restore():
    e = engine(0.2, fair_value_vix_half_life=5.0, fair_value_vix_release_half_life=60.0)
    e.run_days(2, record=False)
    for day in range(2, 6):
        e.pin_macro(vix=60.0)
        e.run_days(1, record=False, first_day=day)
    twin = engine(0.2, fair_value_vix_half_life=5.0, fair_value_vix_release_half_life=60.0)
    twin.restore_state(e.state_snapshot())
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


@pytest.mark.parametrize("value", [-1.0, 2521.0])
def test_the_release_half_life_is_bounded(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", fair_value_vix_release_half_life=value)
