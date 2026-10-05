"""A pinned VIX priced below the knee, and a held pin priced once
(`pinned_vix_calm_knee`, `pinned_vix_calm_share`, `pinned_vix_priced_cap`;
0.8.5, r17 sf1).

With `pinned_vix_feedback` on, a pin moves the volatility feedback's smoothed
exposure toward the pinned VIX's log excess over `fair_value_vix_knee` (40 on
pt-v20). A pin under the knee has no excess, so a scenario that forces the
VIX from 12 to 30 moves no price, and SF1 (the share of the paired move
priced the day the pin lands) is a ratio of noise on every such seed.

`pinned_vix_calm_knee` and `pinned_vix_calm_share` add a second, shallower
line: the pin's target is the larger of the knee's excess and
`share * ln(vix / calm_knee)`. `pinned_vix_priced_cap` stops a VIX held at
one pinned level from closing the rest of the gap over the sessions after
(`pinned_vix_feedback` 0.8 closes 80 per cent on the day and 96 by the next).

All three are 0.0 on every shipped preset and are read only on a session a
caller pinned the VIX. These tests hold the defaults, the ranges, the
unpinned run, the target, the paired move and the cap.
"""

import math
import struct

import pytest

import tradefloor as tf

UNIVERSE = list(tf.Universe.random(12, seed=3))
KNEE = 40.0      # pt-v20's fair_value_vix_knee
GAIN = 0.35      # pt-v20's fair_value_vix_discount
CALM = {"pinned_vix_calm_knee": 17.6, "pinned_vix_calm_share": 0.3}
NAMES = ("pinned_vix_calm_knee", "pinned_vix_calm_share", "pinned_vix_priced_cap")


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def exposure(e):
    return e.state_snapshot()["economy"]["vix_feedback"]


def index(e):
    return sum(math.log(p) for p in floats(e.prices())) / len(UNIVERSE)


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert [d[n] for n in NAMES] == [0.0, 0.0, 0.0]


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "pinned_vix_calm_knee",
        "pinned_vix_calm_share",
        "pinned_vix_priced_cap",
    )} == {
        "pinned_vix_calm_knee": 17.6,
        "pinned_vix_calm_share": 0.2,
        "pinned_vix_priced_cap": 1.0,
    }


def test_ranges():
    tf.ModelParams.from_preset("pt-v20", pinned_vix_calm_knee=17.6, pinned_vix_calm_share=0.3,
                               pinned_vix_priced_cap=1.0)
    for name, bad in (("pinned_vix_calm_knee", -1.0), ("pinned_vix_calm_knee", 250.0),
                      ("pinned_vix_calm_share", -0.1), ("pinned_vix_calm_share", 1.5),
                      ("pinned_vix_priced_cap", 2.0)):
        with pytest.raises(Exception):
            tf.ModelParams.from_preset("pt-v20", **{name: bad})


def test_inert_on_a_run_nobody_pins():
    runs = []
    for d in ({"pinned_vix_feedback": 0.8},
              {"pinned_vix_feedback": 0.8, **CALM, "pinned_vix_priced_cap": 1.0}):
        e = engine(**d)
        e.run_days(60, record=False)
        runs.append((floats(e.prices()), e.draws_by_stream()))
    assert runs[0] == runs[1]


def test_without_the_feedback_a_pin_reads_neither_dial():
    runs = []
    for d in ({}, {**CALM, "pinned_vix_priced_cap": 1.0}):
        e = engine(**d)
        e.run_days(5, record=False)
        e.pin_macro(vix=30.0)
        e.run_days(3, record=False, first_day=5)
        runs.append((floats(e.prices()), e.draws_by_stream(), exposure(e)))
    assert runs[0] == runs[1]


def test_a_pin_under_the_knee_takes_the_calm_line():
    e = engine(pinned_vix_feedback=0.8, **CALM)
    e.run_days(5, record=False)
    before = exposure(e)
    e.pin_macro(vix=30.0)
    target = 0.3 * math.log(30.0 / 17.6)
    assert exposure(e) == pytest.approx(before + 0.8 * (target - before), rel=1e-12)
    # Without the calm line the same pin has no target over the knee.
    f = engine(pinned_vix_feedback=0.8)
    f.run_days(5, record=False)
    b = exposure(f)
    f.pin_macro(vix=30.0)
    assert exposure(f) == pytest.approx(b + 0.8 * (0.0 - b), rel=1e-12, abs=1e-15)


def test_the_larger_line_wins_above_the_knee():
    for vix in (45.0, 80.0):
        e = engine(pinned_vix_feedback=1.0, **CALM)
        e.run_days(5, record=False)
        e.pin_macro(vix=vix)
        assert exposure(e) == max(math.log(vix / KNEE), 0.3 * math.log(vix / 17.6))


def test_a_pin_under_the_calm_knee_is_the_knee_alone():
    e = engine(pinned_vix_feedback=1.0, **CALM)
    e.run_days(5, record=False)
    e.pin_macro(vix=15.0)
    assert exposure(e) == 0.0


def test_the_calm_pin_moves_the_paired_index_the_day_it_lands():
    """The paired move of the pin's own session is the calm line's discount."""
    moves = []
    for seed in range(11, 15):
        pair = []
        for d in ({"pinned_vix_feedback": 0.8}, {"pinned_vix_feedback": 0.8, **CALM}):
            e = engine(seed=seed, **d)
            e.run_days(5, record=False)
            e.pin_macro(vix=30.0)
            e.run_days(1, record=False, first_day=5)
            pair.append(index(e))
        moves.append(pair[1] - pair[0])
    priced = GAIN * 0.8 * 0.3 * math.log(30.0 / 17.6)
    assert all(m < -0.5 * priced for m in moves)


def test_the_cap_holds_a_held_pin_at_its_priced_share():
    out = {}
    for cap in (0.0, 1.0):
        e = engine(pinned_vix_feedback=0.8, pinned_vix_priced_cap=cap, **CALM)
        e.run_days(5, record=False)
        path = []
        for day in range(5, 9):
            e.pin_macro(vix=30.0)
            path.append(exposure(e))
            e.run_days(1, record=False, first_day=day)
        out[cap] = path
    target = 0.3 * math.log(30.0 / 17.6)
    # The first pin is the same step either way.
    assert out[1.0][0] == out[0.0][0]
    # Without the cap the held pin keeps closing the gap; with it, it holds.
    assert out[0.0][1] > out[0.0][0] and out[0.0][-1] == pytest.approx(target, rel=0.01)
    assert out[1.0][1:] == [out[1.0][0]] * 3


def test_the_cap_leaves_a_step_down_as_it_was():
    out = []
    for cap in (0.0, 1.0):
        e = engine(pinned_vix_feedback=0.8, pinned_vix_priced_cap=cap)
        e.run_days(5, record=False)
        e.pin_macro(vix=90.0)
        e.run_days(1, record=False, first_day=5)
        e.pin_macro(vix=50.0)
        out.append(exposure(e))
    assert out[0] == out[1]


def test_the_cap_at_full_feedback_is_the_switch_as_it_stood():
    out = []
    for cap in (0.0, 1.0):
        e = engine(pinned_vix_feedback=1.0, pinned_vix_priced_cap=cap)
        e.run_days(5, record=False)
        e.pin_macro(vix=80.0)
        out.append(exposure(e))
    assert out[0] == out[1] == math.log(80.0 / KNEE)
