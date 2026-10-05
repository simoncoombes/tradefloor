"""Macro pins that hold, a pinned VIX priced when it is published, and the
pinned corporate spread (`macro_pins_hold`, `pinned_vix_feedback`,
`pin_macro(corporate_spread=...)`; 0.8.5, r13 scenario-frontrun).

Both switches are 0.0 on every shipped preset and read nothing on a session
nobody pins. These tests hold that, and the three mechanisms:

- under `macro_pins_hold` a pinned field is the pinned value at the close:
  a held phase is never rolled off at a close and keeps ageing, a pinned
  10-year and a pinned policy rate hold through a central-bank meeting, and
  every draw the close takes is still taken;
- under `pinned_vix_feedback` a pinned VIX's discount lands in the pin's own
  re-mark rather than over the following weeks, the exposure holds through
  the close, and a snapshot taken mid-hold restores to the same close;
- a pinned spread holds over the 10-year through the close while the
  10-year moves the level, and travels in the snapshot and both state
  hashes only while it is set.
"""

import math
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def economy(e):
    return e.state_snapshot()["economy"]


def index(e):
    # Equal-weighted log level: enough to see a market-wide re-mark.
    return sum(math.log(p) for p in floats(e.prices())) / len(UNIVERSE)


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_both_switches_are_off_on_every_shipped_preset(preset):
    dials = tf.ModelParams.from_preset(preset).to_dict()
    assert dials["macro_pins_hold"] == 0.0
    assert dials["pinned_vix_feedback"] == 0.0


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "macro_pins_hold",
        "pinned_vix_feedback",
    )} == {
        "macro_pins_hold": 1.0,
        "pinned_vix_feedback": 0.8,
    }


def test_the_hold_refuses_anything_but_zero_or_one():
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", macro_pins_hold=0.5)


def test_the_priced_share_is_refused_outside_zero_to_one():
    # A share since r15: 1.0 is the switch as it stood.
    tf.ModelParams.from_preset("pt-v20", pinned_vix_feedback=0.5)
    for bad in (-0.1, 1.5):
        with pytest.raises(Exception):
            tf.ModelParams.from_preset("pt-v20", pinned_vix_feedback=bad)


@pytest.mark.parametrize("dials", [
    {"macro_pins_hold": 1.0},
    {"pinned_vix_feedback": 1.0},
    {"macro_pins_hold": 1.0, "pinned_vix_feedback": 1.0},
])
def test_inert_on_a_run_nobody_pins(dials):
    runs = []
    for d in ({}, dials):
        e = engine(**d)
        e.run_days(60, record=False)
        runs.append((floats(e.prices()), e.draws_by_stream(),
                     sorted(economy(e).items())))
    assert runs[0] == runs[1]


def pinned_run(days, pin, **dials):
    """Pin every morning from day 3 for `days` sessions; the economy after
    each close."""
    e = engine(**dials)
    e.run_days(3, record=False)
    after = []
    for d in range(3, 3 + days):
        pin(e)
        e.run_days(1, record=False, first_day=d)
        after.append(economy(e))
    return e, after


def test_a_held_phase_is_the_pinned_phase_at_every_close_and_keeps_ageing():
    def pin(e):
        e.pin_macro(cycle="contraction")
    _, on = pinned_run(200, pin, macro_pins_hold=1.0)
    _, off = pinned_run(200, pin)
    assert all(x["cycle_phase"] == "contraction" for x in on)
    # The phase's clock starts at the pin that changed it, and runs.
    months = [x["months_in_current_phase"] for x in on]
    assert months == sorted(months) and months[-1] > months[0] > 0.0
    # Without the switch the close rolls it off.
    assert any(x["cycle_phase"] != "contraction" for x in off)


def test_a_held_close_takes_every_draw_the_close_takes():
    # One session, the same pins, the hold on and off: the close is handed
    # the same state and takes the same draws, and the hold only drops what
    # they moved.
    draws = []
    for hold in (0.0, 1.0):
        e = engine(macro_pins_hold=hold)
        e.run_days(40, record=False)
        e.pin_macro(cycle="contraction", treasury_yield_10y=0.05,
                    treasury_yield_2y=0.04, federal_funds_rate=0.04,
                    gdp_growth=-0.02, vix=35.0)
        e.run_days(1, record=False, first_day=40)
        draws.append(e.draws_by_stream())
    assert draws[0] == draws[1]


def test_a_pinned_ten_year_and_policy_rate_hold_through_a_meeting():
    def pin(e):
        e.pin_macro(treasury_yield_10y=0.05, federal_funds_rate=0.04)
    meetings = set()

    e, after = pinned_run(90, pin, macro_pins_hold=1.0)
    assert all(x["treasury_yield_10y"] == 5.0 for x in after)
    assert all(x["federal_funds_rate"] == 4.0 for x in after)
    # The corporate yield still moves (the VIX term), off the held 10-year.
    assert len({x["corporate_bond_yield"] for x in after}) > 10
    # A meeting fell inside the window.
    e2 = engine(macro_pins_hold=1.0)
    e2.run_days(3, record=False)
    for d in range(3, 93):
        pin(e2)
        e2.run_days(1, record=False, first_day=d)
        meetings.add(e2.state_snapshot()["central_bank"]["last_meeting_date"])
    assert len(meetings) >= 2
    # Without the switch the close moves both.
    _, off = pinned_run(90, pin)
    assert len({x["treasury_yield_10y"] for x in off}) > 10


def test_a_pinned_vix_is_priced_in_the_pins_own_re_mark():
    moves = {}
    for pvf in (0.0, 1.0):
        e = engine(pinned_vix_feedback=pvf)
        e.run_days(5, record=False)
        before = index(e)
        e.pin_macro(vix=80.0)
        moves[pvf] = index(e) - before
        exposure = economy(e)["vix_feedback"]
        if pvf:
            assert exposure == pytest.approx(math.log(80.0 / 40.0), rel=1e-12)
            # The close holds it there.
            e.run_days(1, record=False, first_day=5)
            assert economy(e)["vix_feedback"] == exposure
    # Off, the smoothed exposure has not moved, so the morning's re-mark
    # carries no discount; on, the whole of it lands (pt-v20: a gain of
    # 0.35 on log(80/40), times each name's beta).
    assert abs(moves[0.0]) < 1e-3
    assert moves[1.0] < -0.15


def test_the_exposure_resumes_its_pull_the_first_session_nobody_pins():
    e = engine(pinned_vix_feedback=1.0)
    e.run_days(5, record=False)
    e.pin_macro(vix=80.0)
    e.run_days(1, record=False, first_day=5)
    held = economy(e)["vix_feedback"]
    e.run_days(1, record=False, first_day=6)
    now = economy(e)
    target = max(0.0, math.log(now["vix"] / 40.0))
    pull = 1.0 - 0.5 ** (1.0 / 5.0)
    assert now["vix_feedback"] == pytest.approx(held + pull * (target - held), rel=1e-12)


def test_a_snapshot_taken_mid_hold_restores_to_the_same_close():
    dials = {"macro_pins_hold": 1.0, "pinned_vix_feedback": 1.0}
    a = engine(**dials)
    a.run_days(5, record=False)
    a.pin_macro(vix=70.0, cycle="contraction", corporate_spread=0.03,
                treasury_yield_10y=0.045)
    snap = a.state_snapshot()
    assert snap["macro_pins_today"] & 0x4000
    assert manifest.state_hash(snap) == a.state_hash()
    b = engine(**dials)
    b.restore_state(snap)
    assert b.state_hash() == a.state_hash()
    for e in (a, b):
        e.run_days(1, record=False, first_day=5)
    assert b.state_hash() == a.state_hash()
    assert economy(b)["cycle_phase"] == "contraction"
    assert economy(b)["treasury_yield_10y"] == 4.5


def test_the_vix_credit_leg_charges_the_pins_change_once():
    dials = {"macro_pins_hold": 1.0, "pinned_vix_feedback": 1.0}
    e = engine(**dials)
    e.run_days(5, record=False)
    x = economy(e)
    multiplier = {"contraction": 2.8, "trough": 3.5, "recovery": 1.4,
                  "peak": 1.1, "expansion": 1.0}[x["cycle_phase"]]
    e.pin_macro(vix=x["vix"] + 20.0)
    y = economy(e)
    assert y["corporate_bond_yield"] == pytest.approx(
        x["corporate_bond_yield"] + 0.02 * multiplier * 20.0, abs=1e-12)
    # The same pin tomorrow finds the VIX where it held it, and charges
    # nothing more.
    e.run_days(1, record=False, first_day=5)
    before = economy(e)["corporate_bond_yield"]
    e.pin_macro(vix=x["vix"] + 20.0)
    assert economy(e)["corporate_bond_yield"] == before
    # Never over a level or a spread pinned today.
    e.run_days(1, record=False, first_day=6)
    e.pin_macro(corporate_spread=0.025, vix=x["vix"] + 30.0)
    z = economy(e)
    assert z["corporate_bond_yield"] == pytest.approx(z["treasury_yield_10y"] + 2.5, abs=1e-12)


def test_off_a_vix_pin_charges_credit_nothing():
    e = engine()
    e.run_days(5, record=False)
    x = economy(e)["corporate_bond_yield"]
    e.pin_macro(vix=60.0)
    assert economy(e)["corporate_bond_yield"] == x
    assert "pinned_corporate_spread" not in e.state_snapshot()


def test_a_pinned_spread_holds_over_a_moving_ten_year():
    e = engine()
    e.run_days(3, record=False)
    tens = []
    for d in range(3, 83):
        e.pin_macro(corporate_spread=0.03)
        x = economy(e)
        assert x["corporate_bond_yield"] == pytest.approx(x["treasury_yield_10y"] + 3.0, abs=1e-12)
        e.run_days(1, record=False, first_day=d)
        x = economy(e)
        # Through the close, a meeting's re-anchoring included.
        assert x["corporate_bond_yield"] == pytest.approx(x["treasury_yield_10y"] + 3.0, abs=1e-12)
        tens.append(x["treasury_yield_10y"])
    assert len(set(tens)) > 40
    # Released, the chain takes it from where it stands.
    e.run_days(1, record=False, first_day=83)
    assert "pinned_corporate_spread" not in e.state_snapshot()


def test_the_spread_target_reads_and_writes_the_spread():
    target = tf.TARGETS["macro.corporate_spread"]
    e = engine()
    e.run_days(3, record=False)
    target.write(e, 0.021)
    assert target.read(e) == pytest.approx(0.021, abs=1e-12)
    # Held within the meeting formula's 0.8 to 6 per cent.
    target.write(e, 0.001)
    assert target.read(e) == pytest.approx(0.008, abs=1e-12)


def test_a_level_and_a_spread_in_one_call_are_refused():
    e = engine()
    with pytest.raises(tf.ValidationError):
        e.pin_macro(corporate_bond_yield=0.05, corporate_spread=0.02)
    with pytest.raises(tf.ValidationError):
        e.pin_macro(corporate_spread=0.5)


def test_across_two_calls_the_later_pin_holds():
    e = engine()
    e.run_days(3, record=False)
    e.pin_macro(corporate_spread=0.03)
    e.pin_macro(corporate_bond_yield=0.07)
    assert "pinned_corporate_spread" not in e.state_snapshot()
    e.run_days(1, record=False, first_day=3)
    assert economy(e)["corporate_bond_yield"] == pytest.approx(7.0, abs=1e-12)
    e.pin_macro(corporate_bond_yield=0.07)
    e.pin_macro(corporate_spread=0.03)
    e.run_days(1, record=False, first_day=4)
    x = economy(e)
    assert x["corporate_bond_yield"] == pytest.approx(x["treasury_yield_10y"] + 3.0, abs=1e-12)


def test_a_snapshot_with_a_spread_but_no_mark_is_refused():
    e = engine()
    e.run_days(3, record=False)
    e.pin_macro(corporate_spread=0.03)
    snap = e.state_snapshot()
    snap["macro_pins_today"] &= ~0x4000
    b = engine()
    with pytest.raises(Exception):
        b.restore_state(snap)
