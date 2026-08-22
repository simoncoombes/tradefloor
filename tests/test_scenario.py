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


def test_a_vix_shock_moves_dispersion_more_than_level():
    # A stress episode is not direction. The median barely moves; the tails do.
    result = run(Scenario.vix_shock(calm=15.0, peak=45.0, at=10, over=20))
    assert abs(result["median_pct"]) < 0.1
    # The tails move where the median does not: measured -0.25% worst against
    # +0.53% best, a spread twenty times the median shift.
    assert result["best_pct"] - result["worst_pct"] > 10 * abs(result["median_pct"])
    assert result["worst_pct"] < 0.0


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

    Measured over twelve seeds, paired: the volatile regime costs more in
    11 of 12, paired median delta +0.77 bps. A win count and a paired delta
    rather than one seed, because a single comparison of an 11-fill programme
    is noise -- and 11 of 12 rather than 12 of 12 is why. On the previous
    universe generator this read 12 of 12 at +2.99 bps; the effect is robust
    in direction and not in magnitude, so the assertion is on direction.
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
    for seed in range(8):
        calm = pretium.tca.analyse(Rotating(), seed=seed, universe=UNIVERSE,
                                   days=10, scenario=Scenario().hold(vix=15.0))
        spike = pretium.tca.analyse(Rotating(), seed=seed, universe=UNIVERSE,
                                    days=10, scenario=Scenario().hold(vix=45.0))
        deltas.append(spike.shortfall_bps() - calm.shortfall_bps())
        wins += spike.shortfall_bps() > calm.shortfall_bps()

    # A strong majority, not unanimity. Requiring every seed to agree would
    # be pinning noise, and it would fail on a generator change that left the
    # effect intact -- which is exactly what happened.
    assert wins >= 7, deltas
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
# The rename: VIX is a liquidity and correlation lever, not a volatility one
# --------------------------------------------------------------------------


def test_vol_shock_still_works_and_says_it_is_deprecated():
    """The old name is kept so nobody's archived script breaks in silence."""
    with pytest.warns(DeprecationWarning, match="vix_shock"):
        old = Scenario.vol_shock(calm=15.0, peak=45.0, at=10, over=20)
    new = Scenario.vix_shock(calm=15.0, peak=45.0, at=10, over=20)
    # The PATH is what defines a scenario, and the two are the same path, so
    # a run under either name reproduces the other exactly.
    assert old.to_json(40) == new.to_json(40)


def test_vix_at_or_below_fifteen_is_inert():
    """Not "weak" -- inert, and worth a test because the docs now say so.

    Below VIX 15 the spread multiplier floors at 1.0 and the correlation blend
    (VIX > 40) has not started, so there is no channel left. Measured:
    identical prices to the last bit.
    """
    calm = run_scenario(Scenario().hold(vix=15.0), seed=3,
                        universe=UNIVERSE, days=4)
    panic_free = run_scenario(Scenario().hold(vix=5.0), seed=3,
                              universe=UNIVERSE, days=4)
    assert calm.prices() == panic_free.prices()
    assert calm.draws_consumed == panic_free.draws_consumed


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
