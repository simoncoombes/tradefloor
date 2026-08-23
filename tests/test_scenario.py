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


def test_the_policy_rate_alone_moves_nothing_before_the_first_meeting():
    """The measurement that justifies the whole rate_shock constructor.

    Not a defect: equities discount off the corporate bond yield, and the
    policy rate is only the fallback when no yield is present. Inside the
    engine the economy always carries one, so the policy rate never reaches
    fair value DIRECTLY.

    Since the macro chain began running endogenously (2026-08), the trap is
    horizon-dependent rather than absolute: the corporate yield is recomputed
    from the 10Y at central-bank MEETINGS, and the first meeting is scheduled
    45 days out. Inside that window a policy-only ramp still moves exactly
    nothing -- measured 0.00% at 40 days -- which is this test. Past it, the
    chain transmits: see the companion test below.

    It is pinned as a test because it is SILENT. A user ramps the rate over a
    month, sees nothing move, and concludes the model does not care about
    rates. It cares -- at meeting cadence, through the curve.
    """
    policy_only = Scenario().ramp("federal_funds_rate",
                                  start=0.025, end=0.05, over=30)
    result = run(policy_only)
    assert result["median_pct"] == pytest.approx(0.0, abs=1e-9)
    assert result["worst_pct"] == pytest.approx(0.0, abs=1e-9)


def test_the_policy_rate_reaches_the_curve_at_the_first_meeting():
    # The other half of the horizon dependence: past the first central-bank
    # meeting (day 45), the pinned policy path feeds the Taylor-rule state,
    # the meeting recomputes the corporate yield off the 10Y, and equities
    # reprice. Measured at 60 days: median -3.99%, worst -5.56%.
    policy_only = Scenario().ramp("federal_funds_rate",
                                  start=0.025, end=0.05, over=30)
    result = run(policy_only, days=60)
    assert result["median_pct"] < -1.0
    assert result["worst_pct"] < result["median_pct"]


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


@pytest.mark.xfail(
    reason=(
        "KNOWN REGRESSION at the pt-v3 era boundary, left red on purpose. "
        "On this fixture the shock/flat ratio at seeds 3, 5, 7 reads "
        "x1.064, x1.062, x1.037 against pt-v1's x1.23, x1.25, x1.17.\n\n"
        "CORRECTED 2026-08-23, and the correction matters. An earlier "
        "version of this note said the era boundary had HALVED the VIX "
        "volatility lever, x2.51 to x1.54. That came from an instrument "
        "measuring its held-VIX half at one hardcoded seed. Re-measured at "
        "thirty, the steady-state lever is x3.22 at pt-v1 against x3.07 at "
        "pt-v3 -- 95.2% retained, essentially intact.\n\n"
        "What is real is the TRANSIENT, which held at both sample sizes: "
        "27.6% of pt-v1's shock response retained. That distinction is "
        "physical. The market factor's variance reverts to a target "
        "scaling with (VIX/15)^2, and pt-v3's 63-day half-life still "
        "reaches the right level for a HELD VIX; what it cannot do is "
        "track a twenty-day spike. So this test fails for a narrower and "
        "better-understood reason than it was first given.\n\n"
        "Both levers are far below real markets' x6.16 anyway (17.2% "
        "annualised at VIX<12 against 106.1% at VIX 45+, measured on the "
        "40-name reference roster), so 'restore pt-v1's lever' was never "
        "the right target.\n\n"
        "Not fixed by search: ONE variance timescale is doing two jobs. "
        "Within-year clustering wants long memory and transient VIX "
        "tracking wants short. The repair is the multi-timescale variance "
        "process, where a fast component tracks VIX and a slow one carries "
        "clustering. Tracked by tools/calibration/scenario_response.py, "
        "which now defaults to thirty seeds precisely because three was "
        "how this got mis-stated."
    ),
    strict=True,
)
def test_a_vix_shock_raises_realised_volatility():
    """The claim the 2026-08 coupling made true, pinned from the tests.

    This test replaces one asserting a vix_shock moves dispersion more than
    level -- which held exactly while VIX had no volatility channel, and
    stopped holding the day it gained one: the spike now scales the shared
    factor's variance, so the same market draws produce amplified factor
    moves and the median is whatever the shocked window's draws happened to
    sum to (measured +0.87% at seed 5, against <0.1% before the coupling).
    Direction is seed luck; the variance rise is the mechanism, so the
    variance rise is what gets asserted. Measured pooled close-to-close
    vol, shocked over flat on identical draws: 1.23x at seed 3, 1.25x at
    seed 5, 1.17x at seed 7.
    """
    import math
    import statistics
    import pyarrow as pa

    def pooled_vol(scenario, seed):
        engine = run_scenario(scenario, seed=seed, universe=UNIVERSE, days=40,
                              ticks_per_day=80, record=True)
        bars = pa.table(engine.bars(grain="day")).to_pydict()
        series = {}
        for ticker, close in zip(bars["instrument_id"], bars["close"]):
            series.setdefault(ticker, []).append(close)
        returns = []
        for closes in series.values():
            returns += [math.log(closes[i + 1] / closes[i])
                        for i in range(len(closes) - 1)]
        return statistics.pstdev(returns)

    shock = Scenario.vix_shock(calm=15.0, peak=45.0, at=10, over=20)
    flat = Scenario().hold(vix=15.0)
    for seed in (3, 5, 7):
        ratio = pooled_vol(shock, seed) / pooled_vol(flat, seed)
        assert ratio > 1.05, (
            f"seed {seed}: a spike to VIX 45 must raise realised volatility "
            f"over the flat run on identical draws, got x{ratio:.3f}"
        )


# --------------------------------------------------------------------------
# How exact the counterfactual is
# --------------------------------------------------------------------------


def test_the_macro_counterfactual_is_exact_on_the_market_stream():
    """A macro path cannot shift the market's draw schedule any more.

    Before the RNG stream split, a macro path changed prices, prices changed
    which settlement branch drew four uniforms, and the shared schedule
    could shift -- an older build measured -4 in 425,600, and this test
    asserted the multiple-of-four shape rather than zero. The split made
    the market schedule a pure function of (market status, active roster,
    sector count), and `compare` now reports `draw_delta` from the market
    stream, so for scenarios that neither halt the market nor change the
    roster the delta must be exactly zero.

    A failure here means a scenario changed the market's own draw schedule
    -- a halt or a delisting -- or the market stream has stopped being
    trajectory-independent, which would put the whole counterfactual back
    where it was before the split.
    """
    for scenario in (Scenario.rate_shock(start=0.025, end=0.05, over=30),
                     Scenario.vix_shock(),
                     Scenario.rate_shock(start=0.025, end=0.10, over=30)):
        result = run(scenario)
        assert result["draw_delta"] == 0
        assert result["exact"] is True


def test_a_flat_scenario_against_its_default_baseline_is_refused():
    """The zero this used to assert was a world differenced against itself.

    The old form of this test ran a `hold`-only scenario through `compare`
    with no baseline and asserted `median_pct == 0`. It passed for a reason
    that had nothing to do with the engine: the default baseline is
    `hold(**scenario.at(0))`, which for a constant path IS the scenario, so
    the zero was arithmetic rather than measurement. The same call is how a
    user asks "what does a held VIX 65 do to prices?" and gets told
    "nothing", confidently, with no warning -- which is the defect.

    So the refusal is the property now, and the exactness claim the old test
    was reaching for is asserted below on a comparison that actually differs.
    """
    flat = Scenario().hold(federal_funds_rate=0.03, corporate_bond_yield=0.05)
    with pytest.raises(pretium.ValidationError) as excinfo:
        run(flat)
    message = str(excinfo.value)
    # Every refusal names the fields, the conflict and the fix.
    assert "federal_funds_rate=0.03" in message
    assert "corporate_bond_yield=0.05" in message
    assert "CONSTANT" in message
    assert "baseline=" in message


def test_a_held_level_measures_against_an_explicit_baseline():
    """The fix the refusal names, and the exactness claim it used to carry."""
    result = run(Scenario().hold(federal_funds_rate=0.03,
                                 corporate_bond_yield=0.05),
                 baseline=Scenario().hold(federal_funds_rate=0.02,
                                          corporate_bond_yield=0.04))
    assert result["draw_delta"] == 0
    assert result["exact"] is True
    # And it is a measurement rather than a tautology: the worlds differ.
    assert result["median_pct"] != 0.0


def test_a_shock_that_starts_after_the_run_ends_is_refused_by_name():
    """The other way to get a confident zero out of the default baseline.

    A ramp beginning on day 60 in a 40-day run never moves, so the scenario
    is constant over the horizon and the default baseline is the scenario
    again -- but the caller's mistake is a horizon, not a missing baseline,
    and the message has to say which.
    """
    with pytest.raises(pretium.ValidationError) as excinfo:
        run(Scenario().ramp("vix", start=15.0, end=45.0, over=10, begin=60))
    message = str(excinfo.value)
    assert "'vix'" in message
    assert "day 60" in message
    assert "at least 61 days" in message


def test_an_explicit_baseline_identical_to_the_scenario_is_refused():
    """The same defect arriving by the other route.

    An explicit baseline is normally the fix. An explicit baseline that
    realises the SAME path is the original mistake with more typing, and it
    reports the same clean zero.
    """
    with pytest.raises(pretium.ValidationError, match="identical worlds"):
        run(Scenario().hold(vix=45.0), baseline=Scenario().hold(vix=45.0))


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


# --------------------------------------------------------------------------
# Two pins on one field
# --------------------------------------------------------------------------


def test_two_pins_on_one_field_layer_as_consecutive_segments():
    """The step-then-decay path, which used to open the run in crisis.

    Before pins layered, the second driver overwrote the first in a dict and,
    because every driver is total over the days, the survivor back-filled the
    whole run: this exact pair read VIX 48 on DAY ZERO. There is no ordering
    that fixed it -- the other order discarded the decay instead -- so it was
    never a documentation problem.
    """
    scenario = (Scenario()
                .step("vix", before=15.0, after=48.0, at=60)
                .ramp("vix", start=48.0, end=22.0, over=45, begin=75))
    assert scenario.at(0)["vix"] == 15.0        # the whole point: NOT 48
    assert scenario.at(59)["vix"] == 15.0
    assert scenario.at(60)["vix"] == 48.0       # the step lands
    assert scenario.at(74)["vix"] == 48.0       # and holds until the decay
    assert 22.0 < scenario.at(97)["vix"] < 48.0  # mid-decay
    assert scenario.at(120)["vix"] == 22.0
    assert scenario.at(9_999)["vix"] == 22.0    # defined past the horizon


def test_hold_then_ramp_is_the_idiom_for_a_level_and_then_an_episode():
    """A ramp starts AT its start value, so this is a jump and then a decay.

    Written with one pin per phase, which is what the same-day refusal points
    callers at.
    """
    scenario = (Scenario().hold(vix=15.0)
                .ramp("vix", start=48.0, end=18.0, over=40, begin=60))
    assert scenario.at(59)["vix"] == 15.0
    assert scenario.at(60)["vix"] == 48.0
    assert scenario.at(100)["vix"] == 18.0


def test_one_pin_on_a_field_behaves_exactly_as_it_did_before_layering():
    """Layering must not be a change to any scenario anybody already wrote."""
    ramp = Scenario().ramp("vix", start=15.0, end=45.0, over=10, begin=5)
    assert [ramp.at(d)["vix"] for d in (0, 5, 15, 9_999)] == [15.0, 15.0, 45.0, 45.0]
    assert ramp.at(10)["vix"] == pytest.approx(30.0)
    step = Scenario().step("vix", before=15.0, after=45.0, at=10)
    assert [step.at(d)["vix"] for d in (0, 9, 10, 9_999)] == [15.0, 15.0, 45.0, 45.0]


def test_pins_declared_out_of_order_are_refused_and_name_both():
    """The reverse ordering, which used to leave a crisis that never subsided."""
    with pytest.raises(pretium.ValidationError) as excinfo:
        (Scenario()
         .ramp("vix", start=48.0, end=22.0, over=45, begin=75)
         .step("vix", before=15.0, after=48.0, at=60))
    message = str(excinfo.value)
    assert "'vix'" in message                # the field
    assert "day 75" in message               # both pins, by their own args
    assert "at day 60" in message
    assert "Swap the two calls" in message   # the fix


def test_two_pins_claiming_the_same_day_are_refused():
    """One of the two would be dead code the caller believes is running."""
    with pytest.raises(pretium.ValidationError) as excinfo:
        (Scenario()
         .step("vix", before=15.0, after=48.0, at=60)
         .ramp("vix", start=48.0, end=18.0, over=40, begin=60))
    message = str(excinfo.value)
    assert "both begin on day 60" in message
    assert ".hold(vix=" in message           # the composing form
    assert "begin=60" in message

    # Two whole-run paths on one field is the same conflict at day zero, and
    # the fix there is to keep one rather than to split them.
    with pytest.raises(pretium.ValidationError, match="Keep one of them"):
        Scenario().hold(vix=15.0).hold(vix=20.0)


def test_a_refused_hold_leaves_the_scenario_untouched():
    """A half-applied hold would be the same quiet wrongness one level down."""
    scenario = Scenario().hold(vix=15.0)
    with pytest.raises(pretium.ValidationError):
        scenario.hold(inflation_rate=0.06, vix=20.0)
    assert scenario.fields == ("vix",)
    assert scenario.at(0) == {"vix": 15.0}


def test_layered_pins_survive_the_json_round_trip():
    """The serialised form is the PATH, so a layered scenario cites cleanly."""
    scenario = (Scenario("layered")
                .hold(vix=15.0)
                .ramp("vix", start=48.0, end=18.0, over=10, begin=6))
    restored = Scenario.from_json(scenario.to_json(20))
    assert [restored.at(d)["vix"] for d in range(20)] == \
           [scenario.at(d)["vix"] for d in range(20)]


# --------------------------------------------------------------------------
# The ready-made shapes are constructors
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["rate_shock", "vix_shock", "vol_shock",
                                  "from_json"])
def test_a_constructor_called_on_an_instance_is_refused(name):
    """They look chainable and are not. Python will not stop the caller.

    `Scenario().ramp(...).vix_shock(...)` used to return a NEW scenario
    driving only `vix`: the ramp vanished with no error and no symptom until
    somebody read `.fields`. A plain classmethod cannot tell the two call
    forms apart -- it is handed `cls` either way -- so the refusal lives in a
    descriptor, which can.
    """
    base = Scenario().ramp("federal_funds_rate", start=0.02, end=0.05, over=30)
    with pytest.raises(pretium.ValidationError) as excinfo:
        getattr(base, name)()
    message = str(excinfo.value)
    assert f"Scenario.{name}" in message
    assert "federal_funds_rate" in message   # what would have been discarded
    assert "CONSTRUCTOR" in message
    # The base is not mutated by the attempt either.
    assert base.fields == ("federal_funds_rate",)


def test_the_constructors_still_build_from_the_class():
    """The refusal must cost nothing to the way they are meant to be used."""
    assert set(Scenario.rate_shock().fields) == {
        "federal_funds_rate", "corporate_bond_yield"}
    assert Scenario.vix_shock().fields == ("vix",)
    assert Scenario.from_json(Scenario.vix_shock().to_json(4)).fields == ("vix",)
    # And the docstrings survive the descriptor, since that is how anybody
    # discovers what these shapes do.
    assert "curve" in Scenario.rate_shock.__doc__


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


# --------------------------------------------------------------------------
# Scenarios reach the other runners
# --------------------------------------------------------------------------


def test_a_sweep_runs_under_a_scenario():
    import struct
    import statistics

    calm = Scenario().hold(federal_funds_rate=0.025, corporate_bond_yield=0.045)
    hike = Scenario.rate_shock(start=0.025, end=0.05, over=15)
    n = len(UNIVERSE)

    def medians(scenario):
        results = pretium.run_many(seeds=list(range(8)), universe=UNIVERSE,
                                   days=20, ticks=80, workers=4,
                                   scenario=scenario, collect="prices")
        return statistics.median(
            statistics.median(struct.unpack("<%dd" % n, r)) for r in results)

    # The same direction the single-seed comparison gives, across a sweep.
    assert medians(hike) < medians(calm)


def test_a_scenario_crosses_to_workers_as_a_path_not_an_object():
    # The workers are threads sharing an address space. A driver closing over
    # mutable state would be a race waiting to happen; a list of dicts cannot
    # be. Checked by confirming the sweep matches a hand-run seed exactly.
    import struct

    scenario = Scenario.rate_shock(start=0.02, end=0.04, over=5)
    swept = pretium.run_many(seeds=[3], universe=UNIVERSE, days=6, ticks=40,
                             scenario=scenario, collect="prices")[0]
    engine = run_scenario(scenario, seed=3, universe=UNIVERSE, days=6,
                          ticks_per_day=40)
    assert struct.unpack("<%dd" % len(UNIVERSE), swept) == \
        struct.unpack("<%dd" % len(UNIVERSE), engine.prices())


def test_execution_costs_more_in_a_volatile_regime():
    """A question real TCA cannot answer, answered exactly.

    You cannot re-run your execution in the same week with the volatility
    turned down. Here you can, and both worlds run the identical macro path,
    so the difference is the trading rather than the regime.

    Measured over sixteen seeds, paired: the volatile regime costs more in
    13 of 16, paired median delta +0.63 bps. A win count and a paired delta
    rather than one seed, because a single comparison of an 11-fill programme
    is noise. The seed count doubled (8 -> 16) and the required majority
    loosened when the VIX coupling landed: a VIX 45 regime is now genuinely
    volatile rather than merely wide-spread, and a rotating programme in a
    high-variance market can luck into favourable fills (seed 2 reads -2.84
    bps), so unanimity was never going to survive the regime becoming real.
    The effect is robust in direction and not in magnitude, so the assertion
    is on direction.
    """
    import statistics

    class Rotating:
        def __init__(self):
            self.i = 0

        def act(self, obs):
            self.i += 1
            ticker = obs.tickers[self.i % len(obs.tickers)]
            return {ticker: 0.004 * obs.avg_volume(ticker)}

    deltas = []
    wins = 0
    for seed in range(16):
        calm = pretium.tca.analyse(Rotating(), seed=seed, universe=UNIVERSE,
                                   days=10, scenario=Scenario().hold(vix=15.0))
        spike = pretium.tca.analyse(Rotating(), seed=seed, universe=UNIVERSE,
                                    days=10, scenario=Scenario().hold(vix=45.0))
        deltas.append(spike.shortfall_bps() - calm.shortfall_bps())
        wins += spike.shortfall_bps() > calm.shortfall_bps()

    # A strong majority, not unanimity. Requiring every seed to agree would
    # be pinning noise, and it would fail on a change that left the effect
    # intact -- which has now happened twice (the universe generator, then
    # the volatility coupling).
    assert wins >= 11, deltas
    assert statistics.median(deltas) > 0.2


def test_the_tca_counterfactual_stays_clean_under_a_scenario():
    # Both worlds run the identical macro path, so the untraded names must
    # still be untouched. If the scenario were applied to only one of them,
    # every name would move and this would catch it.
    class Buyer:
        def act(self, obs):
            return {obs.tickers[0]: 0.002 * obs.avg_volume(obs.tickers[0])}

    execution = pretium.tca.analyse(
        Buyer(), seed=11, universe=UNIVERSE, days=5,
        scenario=Scenario.rate_shock(start=0.025, end=0.05, over=3))
    assert execution.untouched_moved() == []


def test_a_scenario_run_replays_from_its_own_log():
    # pin_macro is logged, so a scenario is captured in the archive with no
    # special handling -- the log alone reproduces the run, path included.
    scenario = Scenario.rate_shock(start=0.025, end=0.05, over=5)
    engine = run_scenario(scenario, seed=7, universe=UNIVERSE, days=8,
                          ticks_per_day=60)
    archived = json.loads(json.dumps(engine.order_log))
    assert sum(1 for e in archived if e["op"] == "pin_macro") == 8
    replayed = pretium.replay(archived, seed=7, universe=UNIVERSE)
    assert replayed.prices() == engine.prices()
    assert replayed.draws_consumed == engine.draws_consumed


def test_a_pin_holds_for_the_whole_day_it_was_applied_to():
    import pyarrow as pa

    engine = pretium.Engine(seed=5, universe=UNIVERSE,
                            macro_state=pretium.Macro(federal_funds_rate=0.02,
                                                      corporate_bond_yield=0.04))
    wanted = [0.04 + 0.005 * day for day in range(5)]
    for day in range(5):
        engine.pin_macro(corporate_bond_yield=wanted[day])
        engine.open_market()
        engine.run_session(9, 30, 3, 60)
        # Record before the close: the close advances the macro chain into
        # the next day, and the row for THIS day must carry this day's pin.
        engine.record(day)
        engine.close_market()
    recorded = pa.table(engine.macro_table()).to_pydict()["corporate_bond_yield"]
    assert recorded == pytest.approx(wanted)


# --------------------------------------------------------------------------
# The rename, and the coupling that later made the old name true again
# --------------------------------------------------------------------------


def test_vol_shock_still_works_and_says_it_is_deprecated():
    """The old name is kept so nobody's archived script breaks in silence."""
    with pytest.warns(DeprecationWarning, match="vix_shock"):
        old = Scenario.vol_shock(calm=15.0, peak=45.0, at=10, over=20)
    new = Scenario.vix_shock(calm=15.0, peak=45.0, at=10, over=20)
    # The PATH is what defines a scenario, and the two are the same path, so
    # a run under either name reproduces the other exactly.
    assert old.to_json(40) == new.to_json(40)


def test_a_low_vix_calms_the_market_from_the_second_day_on_the_same_draws():
    """The inertness claim, inverted by the coupling -- and pinned again.

    This test used to assert VIX 5 and VIX 15 produce bit-identical prices,
    because below 15 the spread multiplier floored at 1.0 and the correlation
    blend had not started -- there was no channel left. The 2026-08 coupling
    added one: every close feeds the day's VIX into the market factor's
    variance target, so a sub-15 pin is now a CALMING lever rather than a
    no-op. The divergence starts exactly at the second day -- day one trades
    at the fresh baseline state and only the first close reads the pin --
    and the draw schedule does not move at all, because the target reads
    macro state rather than drawing anything: same market noise, different
    variance, different prices.
    """
    day_one_calm = run_scenario(Scenario().hold(vix=15.0), seed=3,
                                universe=UNIVERSE, days=1)
    day_one_low = run_scenario(Scenario().hold(vix=5.0), seed=3,
                               universe=UNIVERSE, days=1)
    assert day_one_calm.prices() == day_one_low.prices()

    calm = run_scenario(Scenario().hold(vix=15.0), seed=3,
                        universe=UNIVERSE, days=4)
    low = run_scenario(Scenario().hold(vix=5.0), seed=3,
                       universe=UNIVERSE, days=4)
    assert calm.prices() != low.prices()
    assert calm.draws_consumed == low.draws_consumed


def test_a_vix_spike_widens_the_quoted_spread():
    """The channel VIX DOES have, asserted so a silent regression is caught."""
    def median_spread_bps(vix):
        engine = run_scenario(Scenario().hold(vix=vix), seed=3,
                              universe=UNIVERSE, days=4)
        out = []
        for ticker in engine.tickers:
            book = engine.book(ticker)
            if book.best_bid and book.best_ask and book.best_ask > book.best_bid:
                mid = (book.best_bid + book.best_ask) / 2
                out.append((book.best_ask - book.best_bid) / mid * 1e4)
        return sum(out) / len(out)

    assert median_spread_bps(65.0) > median_spread_bps(15.0) * 1.5


# --------------------------------------------------------------------------
# Three more of the same class, found while fixing the four
# --------------------------------------------------------------------------


def test_a_misspelt_cycle_phase_is_refused_where_it_is_written():
    """`_check` waved `cycle` through entirely; only the engine caught it.

    That is late. A scenario is built in one place and run in another -- a
    manifest, a sweep worker, a doc example -- so the traceback arrived at
    the run rather than at the typo, and a phase name is exactly the kind of
    string nobody re-reads.
    """
    with pytest.raises(pretium.ValidationError) as excinfo:
        Scenario().hold(cycle="contration")
    assert "contration" in str(excinfo.value)
    assert "contraction" in str(excinfo.value)     # names the valid ones
    for phase in ("expansion", "peak", "contraction", "trough", "recovery"):
        assert Scenario().hold(cycle=phase).at(0)["cycle"] == phase


def test_a_zero_day_serialisation_round_trip_is_refused_at_both_ends():
    """It used to round-trip into a scenario driving NOTHING.

    `to_json(0)` wrote an empty path, `from_json` read it back, the field
    loop never ran, and what came out drove no fields at all -- so a run
    reproduced from that document applied no pins and was indistinguishable
    from a scenario run. Refused where it is written and again where it is
    read, because a document can arrive from anywhere.
    """
    scenario = Scenario.rate_shock(start=0.03, end=0.05, over=5)
    with pytest.raises(pretium.ValidationError, match="at least 1"):
        scenario.to_json(0)
    with pytest.raises(pretium.ValidationError, match="empty path"):
        Scenario.from_json(json.dumps({"schema": 1, "label": "", "days": 0,
                                       "path": []}))
    # One day is a real, if short, path and still round-trips.
    assert Scenario.from_json(scenario.to_json(1)).at(0) == scenario.at(0)


def test_an_undefined_percentage_move_is_refused_rather_than_sorted():
    """A NaN in `move_pct` corrupts all three summary statistics, not one row.

    `sorted` with a NaN present is ordered arbitrarily and `min`/`max` return
    whichever value the comparison chain started from, so median, worst and
    best would all be meaningless -- and printed to two decimal places like
    any other result. Asserted on the arithmetic rather than on a zero-priced
    market, because the point is that the failure is not localised.
    """
    values = [1.0, float("nan"), -5.0, 3.0]
    assert sorted(values)[0] != min(values) or max(values) != 3.0, (
        "a NaN no longer breaks sort/min/max; the guard in compare() can go"
    )
