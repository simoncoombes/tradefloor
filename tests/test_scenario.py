"""Macro paths, and the silent trap they exist to close.

Every figure here was measured. The one that matters most is a zero: pinning
the policy rate alone moves nothing, and a user who does not know that will
conclude the model is indifferent to rates.
"""

import json

import pytest

import pretium
from pretium.baselines import BuyAndHold
from pretium.scenario import Scenario, compare, run_scenario

UNIVERSE = pretium.Universe.random(20, seed=4)


def run(scenario, **kwargs):
    kwargs.setdefault("days", 40)
    kwargs.setdefault("ticks_per_day", 80)
    return compare(scenario, seed=5, universe=UNIVERSE, **kwargs)


# --------------------------------------------------------------------------
# The trap
# --------------------------------------------------------------------------


def test_the_policy_rate_alone_moves_nothing():
    """The measurement that justifies the whole rate_shock constructor.

    Not a defect: equities discount off the corporate bond yield, and the
    policy rate is only the fallback when no yield is present. Inside the
    engine the economy always carries one, so the policy rate never reaches
    fair value.

    It is pinned as a test because it is SILENT. A user ramps the rate, sees
    nothing move, and concludes the model does not care about rates. If this
    ever starts moving prices, the semantics changed and the documentation
    around it is now wrong.
    """
    policy_only = Scenario().ramp("federal_funds_rate",
                                  start=0.025, end=0.05, over=30)
    result = run(policy_only)
    assert result["median_pct"] == pytest.approx(0.0, abs=1e-9)
    assert result["worst_pct"] == pytest.approx(0.0, abs=1e-9)


def test_rate_shock_moves_the_whole_curve_and_prices_fall():
    # Both legs, which is what a rate shock is. Measured: -4.74% median on a
    # 250bp hike over thirty days.
    result = run(Scenario.rate_shock(start=0.025, end=0.05, over=30))
    assert result["median_pct"] < -3.0
    assert set(Scenario.rate_shock().fields) == {
        "federal_funds_rate", "corporate_bond_yield"}


def test_a_rate_shock_does_not_move_every_name_equally():
    # A scenario that shifted the whole cross-section by one number would tell
    # a cross-sectional strategy nothing. Measured: -6.41% worst, +0.25% best.
    result = run(Scenario.rate_shock(start=0.025, end=0.05, over=30))
    assert result["best_pct"] - result["worst_pct"] > 3.0


def test_a_bigger_hike_hurts_more():
    small = run(Scenario.rate_shock(start=0.025, end=0.035, over=30))
    large = run(Scenario.rate_shock(start=0.025, end=0.075, over=30))
    assert large["median_pct"] < small["median_pct"]


def test_a_volatility_shock_moves_dispersion_more_than_level():
    # Volatility is not direction. The median barely moves; the tails do.
    result = run(Scenario.vol_shock(calm=15.0, peak=45.0, at=10, over=20))
    assert abs(result["median_pct"]) < 0.5
    assert result["worst_pct"] < -0.5


# --------------------------------------------------------------------------
# How exact the counterfactual is
# --------------------------------------------------------------------------


def test_the_draw_divergence_is_reported_rather_than_hidden():
    """A macro counterfactual is near-exact, not exact.

    Unlike order flow, which consumes zero draws, a macro path changes prices,
    prices change which branch the book settlement takes, and that branch
    draws four uniforms or none. Measured across scenarios the delta is always
    zero or a multiple of four — 4 in 425,600, which is 0.00094%.

    Reported rather than asserted away, because a user comparing two worlds
    should know whether they saw the same random numbers.
    """
    for scenario in (Scenario.rate_shock(start=0.025, end=0.05, over=30),
                     Scenario.vol_shock(),
                     Scenario.rate_shock(start=0.025, end=0.10, over=30)):
        result = run(scenario)
        assert result["draw_delta"] % 4 == 0
        assert abs(result["draw_delta"]) <= 16
        assert result["exact"] is (result["draw_delta"] == 0)


def test_a_flat_scenario_is_exactly_its_own_baseline():
    flat = Scenario().hold(federal_funds_rate=0.03, corporate_bond_yield=0.05)
    result = run(flat)
    assert result["draw_delta"] == 0
    assert result["exact"] is True
    assert result["median_pct"] == pytest.approx(0.0, abs=1e-12)


# --------------------------------------------------------------------------
# Path construction
# --------------------------------------------------------------------------


def test_a_ramp_is_defined_before_and_after_its_window():
    # A path with holes would make the RUN LENGTH change the scenario.
    scenario = Scenario().ramp("vix", start=15.0, end=45.0, over=10, begin=5)
    assert scenario.at(0)["vix"] == 15.0
    assert scenario.at(5)["vix"] == 15.0
    assert scenario.at(10)["vix"] == pytest.approx(30.0)
    assert scenario.at(15)["vix"] == 45.0
    assert scenario.at(9_999)["vix"] == 45.0


def test_a_step_is_a_discontinuity_and_a_ramp_is_not():
    step = Scenario().step("vix", before=15.0, after=45.0, at=10)
    assert step.at(9)["vix"] == 15.0
    assert step.at(10)["vix"] == 45.0
    ramp = Scenario().ramp("vix", start=15.0, end=45.0, over=10, begin=9)
    assert 15.0 < ramp.at(10)["vix"] < 45.0


def test_scenarios_compose_across_fields():
    scenario = (Scenario()
                .ramp("federal_funds_rate", start=0.02, end=0.04, over=10)
                .hold(vix=30.0)
                .step("cycle", before="expansion", after="contraction", at=5))
    day = scenario.at(7)
    assert day["vix"] == 30.0
    assert day["cycle"] == "contraction"
    assert 0.02 < day["federal_funds_rate"] < 0.04


def test_a_percent_where_a_fraction_belongs_is_refused_at_construction():
    # Caught where the mistake is visible, not sixty days into a run.
    with pytest.raises(pretium.ValidationError, match="FRACTIONS"):
        Scenario().ramp("federal_funds_rate", start=2.5, end=5.0, over=30)
    with pytest.raises(pretium.ValidationError, match="FRACTIONS"):
        Scenario().hold(corporate_bond_yield=5.0)


def test_an_unknown_field_is_refused_and_names_the_valid_ones():
    # A typo that silently did nothing would be the same class of failure this
    # whole module exists to close.
    with pytest.raises(pretium.ValidationError, match="interest_rate"):
        Scenario().hold(interest_rate=0.05)
    with pytest.raises(pretium.ValidationError, match="federal_funds_rate"):
        Scenario().hold(interest_rate=0.05)


def test_a_degenerate_ramp_is_refused():
    with pytest.raises(pretium.ValidationError):
        Scenario().ramp("vix", start=15.0, end=45.0, over=0)
    with pytest.raises(pretium.ValidationError):
        Scenario().ramp("vix", start=15.0, end=45.0, over=10, begin=-1)


def test_the_realised_path_serialises_for_citation():
    scenario = Scenario.rate_shock(start=0.025, end=0.05, over=10)
    payload = json.loads(scenario.to_json(12))
    assert payload["days"] == 12
    assert len(payload["path"]) == 12
    assert payload["path"][0]["federal_funds_rate"] == 0.025
    assert payload["path"][-1]["federal_funds_rate"] == pytest.approx(0.05)
    # Canonical, so the same path always serialises the same bytes.
    assert scenario.to_json(12) == scenario.to_json(12)


# --------------------------------------------------------------------------
# Integration
# --------------------------------------------------------------------------


def test_a_scenario_reaches_the_evaluation_harness():
    """Agents can be scored under a shock, which is the point of all this.

    Measured on seed 7 over twenty days, a 250bp hike over fifteen: buy and
    hold gives up 4.37 percentage points, momentum GAINS 1.73 because it can
    rotate, and the oracle is untouched at +0.05 because it trades mispricing
    and the shock moves fair value with it.
    """
    calm = Scenario().hold(federal_funds_rate=0.025, corporate_bond_yield=0.045)
    shock = Scenario.rate_shock(start=0.025, end=0.05, over=15)
    agents = {"hold": BuyAndHold()}

    quiet = pretium.evaluate(agents, seed=7, universe=UNIVERSE, days=20,
                             scenario=calm)
    hiked = pretium.evaluate({"hold": BuyAndHold()}, seed=7,
                             universe=UNIVERSE, days=20, scenario=shock)
    assert hiked["hold"].return_pct < quiet["hold"].return_pct - 2.0


def test_running_a_scenario_is_reproducible():
    scenario = Scenario.rate_shock(start=0.025, end=0.05, over=10)
    a = run_scenario(scenario, seed=3, universe=UNIVERSE, days=5,
                     ticks_per_day=40)
    b = run_scenario(scenario, seed=3, universe=UNIVERSE, days=5,
                     ticks_per_day=40)
    assert a.prices() == b.prices()
    assert a.draws_consumed == b.draws_consumed


def test_a_scenario_can_be_inspected_before_it_is_run():
    # An off-by-one in a ramp produces a plausible result rather than an
    # error, so being able to look at the path first is the only defence.
    table = Scenario.rate_shock(start=0.02, end=0.06, over=4).table(6)
    assert [row["day"] for row in table] == [0, 1, 2, 3, 4, 5]
    assert table[0]["federal_funds_rate"] == 0.02
    assert table[4]["federal_funds_rate"] == pytest.approx(0.06)
    assert table[5]["federal_funds_rate"] == pytest.approx(0.06)
