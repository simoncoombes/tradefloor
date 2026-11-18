"""A pinned change of phase takes the growth shock a natural one takes.

MODEL.md's known gaps: on pt-v20 and every earlier preset a pin that changes
the cycle phase leaves the phase's age where the old phase had it, and the
phase-entry growth shock's gate reads the age, so the pinned phase takes no
shock. pt-v21 and pt-v22 close it through `macro_pins_hold`: a pin that
changes the phase starts the new phase's clock (`Engine::pin_cycle_phase`),
so the shock fires on its first two sessions, as the gate fires it on a
natural entry (`tests/test_phase_entry_shock_once.py`). These tests pin
that on the default preset, for a pin made once and a pin held, through
`pin_macro` and the scenario layer, and the gap on pt-v20.
"""

import pytest

import tradefloor as tf
from tradefloor.scenario import Scenario

UNIVERSE = list(tf.Universe.random(3, seed=3))
#: The fixed entry shocks (`economy::daily::phase_entry_growth_shock`).
FIXED = {"trough": -0.5, "recovery": 1.0, "expansion": 0.5}
#: Growth's monthly step runs on a game day that is a multiple of the
#: month; the closes read below, days 5 to 8, take none.
MONTH = 21
START = 5


def economy(e):
    return e.state_snapshot()["economy"]


def growth_moves(preset, seed, pin, closes=4):
    """Growth's move over each of `closes` closes from day START, measured
    from the morning (after the day's pin) to the close; and the phase
    standing before the first pin."""
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=tf.ModelParams.from_preset(preset))
    e.run_days(START, record=False)
    before = economy(e)["cycle_phase"]
    moves = []
    for k in range(closes):
        day = START + k
        assert (day + 1) % MONTH and (day + 1) % (3 * MONTH)
        pin(e, k, before)
        g = economy(e)["gdp_growth"]
        e.run_days(1, record=False, first_day=day)
        moves.append(economy(e)["gdp_growth"] - g)
    return moves, before, economy(e)


def other(phase):
    return "recovery" if phase != "recovery" else "expansion"


def pin_once(e, k, before):
    if k == 0:
        e.pin_macro(cycle=other(before))


def pin_every_day(e, k, before):
    e.pin_macro(cycle=other(before))


def test_pt_v22_holds_the_pins_it_reads():
    d = tf.ModelParams.from_preset("pt-v22").to_dict()
    assert d["macro_pins_hold"] == 1.0
    assert d["phase_entry_shock_once"] == 0.0


@pytest.mark.parametrize("seed", [11, 12, 13])
@pytest.mark.parametrize("pin", [pin_once, pin_every_day], ids=["once", "held"])
def test_on_pt_v22_a_pinned_change_takes_the_entry_shock(seed, pin):
    moves, before, after = growth_moves("pt-v22", seed, pin)
    target = other(before)
    assert after["cycle_phase"] == target
    shock = FIXED[target]
    # The first two sessions, as the gate fires on a natural entry; none
    # after.
    assert moves == pytest.approx([shock, shock, 0.0, 0.0], abs=1e-12)


@pytest.mark.parametrize("seed", [11, 13])
def test_a_pin_of_the_standing_phase_takes_none(seed):
    def same(e, k, before):
        e.pin_macro(cycle=before)
    moves, _, _ = growth_moves("pt-v22", seed, same)
    assert moves == [0.0] * 4


def test_growth_pinned_the_same_day_holds_through_the_shock():
    def with_growth(e, k, before):
        e.pin_macro(cycle=other(before), gdp_growth=economy(e)["gdp_growth"] / 100.0)
    moves, _, _ = growth_moves("pt-v22", 11, with_growth)
    assert moves == pytest.approx([0.0] * 4, abs=1e-12)


@pytest.mark.parametrize("how", ["hold", "intervention"])
def test_the_scenario_layer_takes_it_too(how):
    scenario = (Scenario("t").hold(cycle="recovery") if how == "hold" else
                Scenario("t").shock("macro.cycle", operation="set", value="recovery", at=0))

    def apply(e, k, before):
        assert before != "recovery"
        scenario.apply(e, k)

    moves, _, _ = growth_moves("pt-v22", 11, apply)
    assert moves == pytest.approx([1.0, 1.0, 0.0, 0.0], abs=1e-12)


def test_pt_v20_still_has_the_gap():
    """Without `macro_pins_hold` the pinned phase inherits the old one's
    age, and the gate does not fire."""
    assert tf.ModelParams.from_preset("pt-v20").to_dict()["macro_pins_hold"] == 0.0
    moves, before, after = growth_moves("pt-v20", 11, pin_once)
    assert after["cycle_phase"] == other(before)
    assert moves == [0.0] * 4
