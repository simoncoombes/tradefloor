"""A pinned VIX priced as a share of its gap, and its priced move taken out of
the session's market draw (`pinned_vix_feedback` in [0, 1],
`pinned_vix_variance_share`; 0.8.5, r15 scenario).

`pinned_vix_feedback` was a switch (r13): 1.0 priced the whole gap between
the smoothed exposure and the pinned VIX's excess in the pin's re-mark. It
is now the share of that gap the pin closes; 1.0 is the switch as it stood.

`pinned_vix_variance_share` is 0.0 on every shipped preset and is read only
with `pinned_vix_feedback` on and a VIX pinned that session. The pin records
the discount's change `J` (`fair_value_vix_discount` times the exposure's
change), and that session's market-factor draws take
`sigma * sqrt(max(1 - J^2 / v, 1 - share))`, so the priced move is part of
the day's variance rather than added to it. The variance state reads each
draw rescaled to its own sigma. These tests hold the share, the recorded
move, the scaled draw, the state that ignores the scale, the close that
clears the move and a restore taken between the pin and the close.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
KNEE = 40.0      # pt-v20's fair_value_vix_knee
GAIN = 0.35      # pt-v20's fair_value_vix_discount


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def economy(e):
    return e.state_snapshot()["economy"]


def index(e):
    return sum(math.log(p) for p in floats(e.prices())) / len(UNIVERSE)


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_the_share_is_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["pinned_vix_variance_share"] == 0.0


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "pinned_vix_variance_share",
    )} == {
        "pinned_vix_variance_share": 0.7,
    }


def test_the_share_is_refused_outside_zero_to_one():
    tf.ModelParams.from_preset("pt-v20", pinned_vix_variance_share=0.7)
    for bad in (-0.1, 1.5):
        with pytest.raises(Exception):
            tf.ModelParams.from_preset("pt-v20", pinned_vix_variance_share=bad)


def test_inert_on_a_run_nobody_pins():
    runs = []
    for d in ({}, {"pinned_vix_feedback": 0.8, "pinned_vix_variance_share": 0.7}):
        e = engine(**d)
        e.run_days(60, record=False)
        runs.append((floats(e.prices()), e.draws_by_stream(), e.state_hash()))
    assert runs[0][:2] == runs[1][:2]


def test_the_pin_closes_its_share_of_the_gap():
    for w in (0.5, 0.8):
        e = engine(pinned_vix_feedback=w)
        e.run_days(5, record=False)
        before = economy(e)["vix_feedback"]
        e.pin_macro(vix=80.0)
        after = economy(e)["vix_feedback"]
        target = math.log(80.0 / KNEE)
        assert after == pytest.approx(before + w * (target - before), rel=1e-12)
        # The close holds it there, as at 1.0.
        e.run_days(1, record=False, first_day=5)
        assert economy(e)["vix_feedback"] == after


def test_the_whole_gap_at_one_is_the_switch_as_it_stood():
    e = engine(pinned_vix_feedback=1.0)
    e.run_days(5, record=False)
    e.pin_macro(vix=80.0)
    assert economy(e)["vix_feedback"] == math.log(80.0 / KNEE)


def test_the_priced_move_is_recorded_and_the_close_clears_it():
    dials = {"pinned_vix_feedback": 0.8, "pinned_vix_variance_share": 0.7}
    e = engine(**dials)
    e.run_days(5, record=False)
    before = economy(e)["vix_feedback"]
    e.pin_macro(vix=80.0)
    snap = e.state_snapshot()
    jump = GAIN * (snap["economy"]["vix_feedback"] - before)
    assert snap["pinned_vix_jump"] == pytest.approx(jump, rel=1e-12)
    # A second pin the same session adds its own change.
    e.pin_macro(vix=90.0)
    after = economy(e)["vix_feedback"]
    assert e.state_snapshot()["pinned_vix_jump"] == pytest.approx(
        GAIN * (after - before), rel=1e-12)
    e.run_days(1, record=False, first_day=5)
    assert "pinned_vix_jump" not in e.state_snapshot()
    # Without the share nothing is recorded.
    f = engine(pinned_vix_feedback=0.8)
    f.run_days(5, record=False)
    f.pin_macro(vix=80.0)
    assert "pinned_vix_jump" not in f.state_snapshot()


def session_moves(share, seeds=range(11, 19)):
    """The equal-weighted index's move from the open to the close on the
    session a VIX of 80 is pinned, one per seed."""
    out = []
    for seed in seeds:
        e = engine(seed=seed, pinned_vix_feedback=1.0, pinned_vix_variance_share=share)
        e.run_days(5, record=False)
        e.pin_macro(vix=80.0)
        e.open_market()
        opened = index(e)
        e.run_session(9, 30, 3, 390)
        e.close_market()
        out.append(index(e) - opened)
    return out


def test_a_large_priced_move_takes_the_sessions_market_draw():
    # A VIX of 80 prices 0.35 x log 2, far over a day's variance, so at a
    # share of 1.0 the session draws no market factor at all, and the
    # equal-weighted index moves only by what the names do on their own.
    full = session_moves(0.0)
    none = session_moves(1.0)
    assert sum(abs(x) for x in none) < 0.7 * sum(abs(x) for x in full)


def test_the_variance_state_reads_the_draw_at_its_own_sigma():
    # The state's update reads each scaled draw rescaled to the state's
    # sigma, so after the close it stands where it would have without the
    # scale (up to rounding).
    states = []
    for share in (0.0, 0.7):
        e = engine(seed=5, pinned_vix_feedback=1.0, pinned_vix_variance_share=share)
        e.run_days(5, record=False)
        e.pin_macro(vix=80.0)
        e.run_days(1, record=False, first_day=5)
        states.append(e.state_snapshot()["market_variance"])
    assert states[1] == pytest.approx(states[0], rel=1e-9, abs=1e-15)


def test_a_snapshot_between_the_pin_and_the_close_restores_to_the_same_close():
    dials = {"macro_pins_hold": 1.0, "pinned_vix_feedback": 0.8,
             "pinned_vix_variance_share": 0.7}
    a = engine(**dials)
    a.run_days(5, record=False)
    a.pin_macro(vix=70.0)
    snap = a.state_snapshot()
    assert snap["pinned_vix_jump"] != 0.0
    assert manifest.state_hash(snap) == a.state_hash()
    b = engine(**dials)
    b.restore_state(snap)
    assert b.state_hash() == a.state_hash()
    for e in (a, b):
        e.run_days(1, record=False, first_day=5)
    assert b.state_hash() == a.state_hash()
    assert floats(b.prices()) == floats(a.prices())
    # A restore that dropped the move would draw the session at full
    # variance and close elsewhere.
    c = engine(**dials)
    lost = dict(snap)
    del lost["pinned_vix_jump"]
    c.restore_state(lost)
    c.run_days(1, record=False, first_day=5)
    assert floats(c.prices()) != floats(a.prices())
