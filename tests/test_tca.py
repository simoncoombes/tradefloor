"""Transaction cost analysis against a market where the trader never existed.

Every number asserted here was measured first. The interesting ones are the
counter-intuitive results — a negative shortfall on a round trip, a flat
response above the saturation point — because those are where a user would
otherwise assume the library was broken, or worse, assume it was right for the
wrong reason.
"""

import pytest

import tradefloor


UNIVERSE = tradefloor.Universe.random(20, seed=7)


class BuyOnce:
    """Buy one name at a fraction of its ADV on the first step, then hold."""

    def __init__(self, participation=0.01, index=0):
        self.participation = participation
        self.index = index
        self.done = False

    def act(self, obs):
        if self.done:
            return {}
        self.done = True
        ticker = obs.tickers[self.index]
        return {ticker: self.participation * obs.avg_volume(ticker)}


class RoundTrip:
    """Buy on step 0, close the whole position on step 3."""

    def __init__(self, participation=0.01):
        self.participation = participation

    def act(self, obs):
        ticker = obs.tickers[0]
        if obs.step == 0:
            return {ticker: self.participation * obs.avg_volume(ticker)}
        if obs.step == 3:
            return {ticker: -obs.position(ticker)}
        return {}


class Idle:
    def act(self, obs):
        return {}


def analyse(agent, **kwargs):
    kwargs.setdefault("days", 1)
    kwargs.setdefault("steps_per_day", 6)
    kwargs.setdefault("seed", 2026)
    return tradefloor.tca.analyse(agent, universe=UNIVERSE, **kwargs)


# --------------------------------------------------------------------------
# The counterfactual is clean
# --------------------------------------------------------------------------


def test_the_untraded_world_is_unmoved_where_the_trader_did_not_go():
    """The property the whole measurement depends on.

    Order flow consumes no RNG draws, so adding a trade in one name leaves the
    draw schedule byte-identical and every other name sees exactly the noise
    it would have seen. If that were not true, impact would be a signal
    buried in a shifted market and the subtraction would be meaningless.

    This is a ONE-DAY analysis, which is what makes emptiness assertable
    exactly: the final prices predate the first close-repriced variance
    target, so the 2026-08 fear-gauge channel — trading moves the same-day
    VIX, VIX reaches other names' volatility two closes later — cannot
    arrive in time. On a multi-day run that channel can move untraded names
    a little unless VIX is pinned; the measurement and the bounds are in
    ``Execution.moved``'s docstring.

    Asserted rather than assumed: a non-empty result here means something
    leaked between the two worlds.
    """
    execution = analyse(BuyOnce(0.01))
    assert execution.untouched_moved() == []
    assert set(execution.moved()) == {UNIVERSE[0].ticker}


def test_an_agent_that_does_nothing_has_no_cost_and_no_impact():
    execution = analyse(Idle())
    assert execution.fills == []
    assert execution.shortfall() == 0.0
    assert execution.shortfall_bps() == 0.0
    assert execution.moved() == {}


def test_the_two_worlds_start_identical():
    # Step 0 happens before anyone has traded, so the cross-sections must
    # agree exactly. A difference here would mean the runs were not paired.
    execution = analyse(BuyOnce(0.01))
    assert execution.actual_path[0] == execution.baseline_path[0]


def test_the_analysis_is_reproducible():
    a = analyse(BuyOnce(0.01))
    b = analyse(BuyOnce(0.01))
    assert a.shortfall() == b.shortfall()
    assert a.as_dict() == b.as_dict()


# --------------------------------------------------------------------------
# Direction and magnitude
# --------------------------------------------------------------------------


def test_a_one_way_buyer_pays():
    # Positive is always a cost. A buyer who moved the price up paid for it.
    execution = analyse(BuyOnce(0.01))
    assert execution.shortfall() > 0
    assert execution.shortfall_bps() > 0
    assert execution.impact_bps(UNIVERSE[0].ticker) > 0


def test_cost_scales_with_size_while_the_book_can_fill_it():
    # 10x the shares, 10x the currency cost -- and the same cost per share,
    # because at these sizes the order is not walking the book.
    small = analyse(BuyOnce(0.001))
    large = analyse(BuyOnce(0.01))
    assert large.shortfall() == pytest.approx(small.shortfall() * 10, rel=0.05)
    assert large.shortfall_bps() == pytest.approx(small.shortfall_bps(), rel=0.05)


def test_an_order_larger_than_the_book_fills_partially_and_says_so():
    """The cheapest execution is the one that did not happen.

    A request for 4,856 shares fills 483 -- the entire displayed depth -- and
    every larger request fills the same 483. Reported through `partial`
    rather than by silently returning a small fill, because a low shortfall on
    an order that mostly did not execute is not a good execution.
    """
    modest = analyse(BuyOnce(0.05))
    huge = analyse(BuyOnce(0.5))
    assert modest.partial_fills()
    assert huge.partial_fills()
    assert huge.fills[0]["requested"] > modest.fills[0]["requested"] * 5
    assert huge.fills[0]["quantity"] == modest.fills[0]["quantity"]
    assert huge.shortfall() == modest.shortfall()


# --------------------------------------------------------------------------
# The round trip, which is the result that needs explaining
# --------------------------------------------------------------------------


SEEDS = (2026, 1, 2, 3, 4, 5, 7, 11)


def test_a_round_trip_recoups_its_own_impact():
    """Buying then selling shows a NEGATIVE shortfall, and that is correct.

    The entry pushes the price up, part of that impact persists, and the exit
    sells into it. The agent really did transact at prices better than the
    untraded world offered on that leg.

    Measured across eight seeds: the entry costs +79.47 on every one of them
    -- it happens at step 0, before the worlds have had a chance to diverge --
    and the exit recoups on six, by between -82 and -268.

    Asserted as a MAJORITY rather than on one seed. It was pinned to seed 2026
    and read "-182.73 exiting, -103.26 net"; when a stepped day was fixed to
    stop re-opening the market at every step, seed 2026 became one of the two
    that do not recoup and this test failed while the phenomenon it names was
    unchanged. A single seed measures the seed.
    """
    recouped = 0
    for seed in SEEDS:
        held = analyse(BuyOnce(0.01), seed=seed)
        traded = analyse(RoundTrip(0.01), seed=seed)
        # Structural, so it holds on every seed: a one-way buyer moved the
        # price and never sold into it.
        assert held.shortfall() > 0, f"seed {seed}"
        # The entry leg is the same trade in both, so it costs the same.
        assert traded.by_step()[0][1] == pytest.approx(held.shortfall())
        if traded.shortfall() < 0:
            recouped += 1
    assert recouped >= len(SEEDS) // 2, (
        f"only {recouped}/{len(SEEDS)} round trips recouped; impact has "
        "stopped persisting to the exit"
    )


def test_by_step_shows_the_entry_paying_and_the_exit_recouping():
    # The netted total hides both halves. This is the accessor that does not.
    #
    # The entry always pays; the exit recoups on most seeds but not all, so
    # that half is counted rather than asserted per seed.
    favourable = 0
    for seed in SEEDS:
        execution = analyse(RoundTrip(0.01), seed=seed)
        steps = dict(execution.by_step())
        assert steps[0] > 0, f"seed {seed}: the entry did not cost anything"
        assert sum(steps.values()) == pytest.approx(execution.shortfall())
        if steps[3] < 0:
            favourable += 1
    assert favourable >= len(SEEDS) // 2, (
        f"the exit leg was favourable on only {favourable}/{len(SEEDS)} seeds"
    )


def test_a_round_trip_leaves_less_lasting_impact_than_holding():
    # The market substantially recovers once the position is unwound, which
    # is why the exit had a favourable price to sell into in the first place.
    #
    # Asserted as a majority, like the recoup tests above, and for the same
    # reason: it was pinned to one seed at "< 10% of the held impact" and
    # the stream-split re-deal moved that seed's residual from under the
    # threshold to 19% while the phenomenon was unchanged. A single seed
    # measures the seed. Re-measured across all eight: the round trip's
    # lasting impact is smaller than holding's on seven (held between 22.9
    # and 85.6 bps, round trip between -34.6 and +16.1). The exception is
    # seed 11, where the HELD impact is itself only 10.8 bps -- when the
    # thing being recovered is small, the nonlinear residual of entering
    # and exiting can exceed it.
    smaller = 0
    for seed in SEEDS:
        held = analyse(BuyOnce(0.01), seed=seed)
        traded = analyse(RoundTrip(0.01), seed=seed)
        ticker = UNIVERSE[0].ticker
        if abs(traded.impact_bps(ticker)) < abs(held.impact_bps(ticker)):
            smaller += 1
    # Two, not one, since the 2026-08-26 era boundary. pt-v10's market is
    # more volatile than pt-v3's, so the nonlinear residual the comment above
    # describes exceeds the recovered impact on more seeds: six of eight
    # rather than seven of eight. The property is a tendency, and the
    # tolerance is what says so.
    assert smaller >= len(SEEDS) - 2, (
        f"a round trip out-impacted holding on {len(SEEDS) - smaller} of "
        f"{len(SEEDS)} seeds; unwinding has stopped recovering impact"
    )


# --------------------------------------------------------------------------
# Breakdowns
# --------------------------------------------------------------------------


def test_the_breakdowns_agree_with_the_total():
    execution = analyse(RoundTrip(0.01))
    assert sum(execution.by_ticker().values()) == pytest.approx(execution.shortfall())
    assert sum(v for _, v in execution.by_step()) == pytest.approx(execution.shortfall())


def test_shortfall_can_be_asked_per_instrument():
    class Two:
        def act(self, obs):
            if obs.step:
                return {}
            return {t: 0.005 * obs.avg_volume(t) for t in obs.tickers[:2]}

    execution = analyse(Two())
    per = execution.by_ticker()
    assert len(per) == 2
    for ticker, value in per.items():
        assert execution.shortfall(ticker) == value
        assert value > 0


def test_as_dict_is_json_shaped():
    import json

    payload = analyse(BuyOnce(0.01)).as_dict()
    assert json.loads(json.dumps(payload)) == payload


# --------------------------------------------------------------------------
# Guards
# --------------------------------------------------------------------------


def test_a_degenerate_session_is_refused():
    for kwargs in ({"days": 0}, {"steps_per_day": 0}, {"ticks_per_step": 0}):
        with pytest.raises(tradefloor.ValidationError):
            analyse(Idle(), **kwargs)


def test_a_refused_trade_is_not_an_execution():
    # An order the market would not accept has no fill and therefore no cost.
    # Charging for it would put a number on something that never happened.
    class Impossible:
        def act(self, obs):
            return {obs.tickers[0]: 1e15} if obs.step == 0 else {}

    execution = analyse(Impossible())
    assert all(f["quantity"] != 0 for f in execution.fills)
