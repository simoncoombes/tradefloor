"""A pinned VIX's same-day return (`pinned_vix_return`).

0.0 on every shipped preset, and a session nobody pins reads nothing. Off
zero, a VIX pin sets the session's mean market move to `-b` times the pin's
change, less what the `pinned_vix_feedback` discount priced; these tests hold
that the dial is inert where it should be, that the mean lands, and that a
snapshot taken after a pin restores to the same close.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
B = 0.005375


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def index(e):
    return sum(math.log(p) for p in floats(e.prices())) / len(UNIVERSE)


@pytest.mark.parametrize("preset", tf.preset_names())
def test_it_is_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["pinned_vix_return"] == 0.0


def test_it_is_refused_outside_its_range():
    tf.ModelParams.from_preset("pt-v20", pinned_vix_return=0.05)
    for bad in (-0.001, 0.06, float("nan")):
        with pytest.raises(Exception):
            tf.ModelParams.from_preset("pt-v20", pinned_vix_return=bad)


def test_a_session_nobody_pins_is_bit_identical():
    a, b = engine(), engine(pinned_vix_return=B)
    for e in (a, b):
        e.run_days(20, record=False)
    assert a.prices() == b.prices()
    assert "pinned_vix_move" not in b.state_snapshot()


def test_a_pin_that_does_not_move_the_vix_moves_nothing():
    a, b = engine(), engine(pinned_vix_return=B)
    for e in (a, b):
        e.run_days(5, record=False)
        e.pin_macro(vix=e.state_snapshot()["economy"]["vix"])
        e.run_days(1, record=False, first_day=5)
    assert a.prices() == b.prices()


def test_a_pinned_rise_lowers_the_session_by_b_a_point_on_average():
    # Paired seeds, the same pin of +15 points: the mean difference in the
    # session's equal-weighted log return is about -b * 15 (the names' betas
    # average near one), far outside the paired noise.
    diffs = []
    for seed in range(40):
        moves = []
        for dials in ({}, {"pinned_vix_return": B}):
            e = engine(seed=seed, **dials)
            e.run_days(5, record=False)
            before = index(e)
            e.pin_macro(vix=e.state_snapshot()["economy"]["vix"] + 15.0)
            e.run_days(1, record=False, first_day=5)
            moves.append(index(e) - before)
        diffs.append(moves[1] - moves[0])
    mean = sum(diffs) / len(diffs)
    assert -1.4 * B * 15 < mean < -0.6 * B * 15


def test_a_snapshot_taken_after_a_pin_restores_to_the_same_close():
    a = engine(pinned_vix_return=B)
    a.run_days(5, record=False)
    a.pin_macro(vix=a.state_snapshot()["economy"]["vix"] + 20.0)
    snap = a.state_snapshot()
    assert snap["pinned_vix_move"] < 0.0 and snap["pinned_vix_mean"] < 0.0
    assert manifest.state_hash(snap) == a.state_hash()
    b = engine(pinned_vix_return=B)
    b.restore_state(snap)
    assert b.state_hash() == a.state_hash()
    for e in (a, b):
        e.run_days(1, record=False, first_day=5)
    assert b.state_hash() == a.state_hash()
    assert "pinned_vix_mean" not in a.state_snapshot()
