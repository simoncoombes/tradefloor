"""Transaction cost analysis against a market where the trader never existed.

Every number asserted here was measured first. The interesting ones are the
counter-intuitive results -- a negative shortfall on a round trip, a flat
response above the saturation point -- because those are where a user would
otherwise assume the library was broken, or worse, assume it was right for the
wrong reason.

The flat response is the shipped default's, not the model's: see
``order_flow_impact_law``. On this surface it is usually the book that
binds first anyway, because ``analyse`` fills against displayed depth and
identical fills mean identical flow.
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

    This is a ONE-DAY analysis, which makes emptiness assertable
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


def test_a_round_trip_does_not_recoup_its_own_impact():
    """Buying then selling costs on both legs, on every seed.

    Until 0.9.0 this test was `a round trip recoups its own impact`, and it
    read a NEGATIVE shortfall as correct: the entry pushed the price up, the
    impact persisted, and the exit sold into it. What persisted was the
    harness counting the entry's flow on every one of the step's 65 ticks,
    so the agent sold into 65 times its own impact. Under 0.8.1 the entry
    cost +96.01 on all eight seeds and the exit recouped on seven, by -59.8
    to -224.2, for a round trip of -13.5 to +3.8 bp.

    With the fills applied once the entry still costs +96.01 on every seed
    and the exit costs too, +26.4 to +174.9, so the round trip is +12.7 to
    +28.8 bp and positive on all eight. How much the exit costs is the
    market's call; that it costs something is the point.
    """
    for seed in SEEDS:
        held = analyse(BuyOnce(0.01), seed=seed)
        traded = analyse(RoundTrip(0.01), seed=seed)
        # Structural, so it holds on every seed: a one-way buyer moved the
        # price and never sold into it.
        assert held.shortfall() > 0, f"seed {seed}"
        # The entry leg is the same trade in both, so it costs the same.
        assert traded.by_step()[0][1] == pytest.approx(held.shortfall())
        assert traded.shortfall() > held.shortfall() > 0, (
            f"seed {seed}: the round trip cost {traded.shortfall():.2f}, no "
            f"more than its entry's {held.shortfall():.2f}; the exit sold "
            "into impact the entry should not have left")


def test_by_step_shows_both_legs_paying():
    # The netted total hides both halves. This is the accessor that does not.
    #
    # Until 0.9.0 it showed the entry paying and the exit recouping on most
    # seeds, and that recoup was the agent's own impact counted on every
    # tick of the step. Now both legs pay on every seed.
    for seed in SEEDS:
        execution = analyse(RoundTrip(0.01), seed=seed)
        steps = dict(execution.by_step())
        assert steps[0] > 0, f"seed {seed}: the entry did not cost anything"
        assert steps[3] > 0, f"seed {seed}: the exit recouped"
        assert sum(steps.values()) == pytest.approx(execution.shortfall())


def test_a_round_trip_leaves_no_lasting_information_impact():
    """The information channel, read where it lives: in `s`.

    Until 0.9.0 this was asserted on the end-of-day print, where a held
    buy's impact stood well above a round trip's on six of eight seeds
    (held +34.8 to +76.2 bp, where it was the harness counting the flow
    on every tick). A 1% buy applied once moves `s` by under a basis point,
    and the print after a day sits anywhere within the tape's own noise of
    that: measured on the same eight seeds, held -28.6 to +6.7 bp and
    round trip -58.0 to +11.5, neither reliably larger. The print cannot
    carry the claim any more.

    `s` can, exactly. The day's `order_flow_impact` attribution after a
    held buy is the buy's permanent impact, and after the round trip it is
    that impact less the sell's, which for the same quantity in the same
    name is zero to the bit: the imbalance of a sell is the negative of the
    buy's.
    """
    from tradefloor import Engine, Portfolio
    from tradefloor.harness import session_clock
    import struct

    def lasting(unwind: bool) -> float:
        engine = Engine(seed=2026, universe=UNIVERSE)
        portfolio = Portfolio()
        ticker = engine.tickers[0]
        engine.open_market()
        for step in range(6):
            if step == 0:
                portfolio.execute(engine, ticker, 0.01 * UNIVERSE[0].avg_volume)
            if step == 3 and unwind:
                portfolio.execute(engine, ticker,
                                  -portfolio.positions[ticker].quantity)
            engine.run_session(*session_clock((9, 30, 3), step, 65), 65,
                               fills=portfolio.pending_flow())
            portfolio.clear_flow()
        column = engine.attribution("order_flow_impact")
        return struct.unpack("<%dd" % (len(column) // 8), column)[0]

    held = lasting(unwind=False)
    assert held > 0, "the buy left no information impact at all"
    assert lasting(unwind=True) == 0.0


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
