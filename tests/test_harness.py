"""The agent evaluation harness."""

import pytest

import struct

import pretium
from pretium import harness


def _f64(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))

UNIVERSE = pretium.Universe.random(8, seed=5)
# The roster spans four orders of magnitude of liquidity by design, so the
# first instrument is whichever one an ADV-sized order can actually move.
UNIVERSE = pretium.Universe(
    sorted(UNIVERSE, key=lambda i: i.avg_volume)
)


class Idle:
    def act(self, obs):
        return {}


class BuyFirst:
    def act(self, obs):
        return {obs.tickers[0]: 5_000} if obs.step == 0 else {}


class Churner:
    """Trades constantly, in size proportional to its own buying power.

    Sized as a fraction of net worth rather than as a flat share count, which
    took two attempts to get right. 40,000 shares moved nothing measurable --
    too small against a book holding millions -- and 900,000 traded nothing at
    all, because the default 2x leverage cap rejected every order. Impact is
    about size relative to the MARKET, and leverage about size relative to
    CAPITAL; a flat share count is wrong against both.
    """

    def act(self, obs):
        # Sized as a multiple of average daily volume, which is how impact
        # actually scales. A flat share count is a different experiment per
        # instrument: 13.7M shares is 0.05x ADV in one name here and 407x in
        # another, and moves the first by nothing.
        target = obs.tickers[0]
        shares = obs.avg_volume(target) * 3.0
        return {target: shares if obs.step % 2 == 0 else -shares}


class Broken:
    def act(self, obs):
        raise RuntimeError("bad strategy")


class Explainer:
    def __init__(self, claim):
        self.claim = claim

    def act(self, obs):
        return {}

    def explain(self, day):
        return self.claim


def run(agents, **kw):
    # Capital deliberately large relative to the roster. A trader with two
    # million dollars against books holding millions of shares leaves no
    # measurable footprint, so an impact test on that scale would assert
    # nothing while appearing to pass.
    params = dict(seed=2026, universe=UNIVERSE, days=3, steps_per_day=4,
                  ticks_per_step=60, cash=200_000_000)
    params.update(kw)
    return pretium.evaluate(agents, **params)


# --------------------------------------------------------------------------
# Fair comparison
# --------------------------------------------------------------------------

def test_every_agent_faces_the_same_market():
    """Identical conditions, not similar ones.

    Each agent gets its own engine from the same seed, so two agents that do
    nothing must end identically. If they did not, any difference between two
    real agents would be partly the market rather than the agents.
    """
    scores = run({"a": Idle(), "b": Idle()})
    assert scores["a"].final_net_worth == scores["b"].final_net_worth


def test_agents_do_not_trade_against_each_other():
    # Deliberate: agents sharing one market would interact realistically, but
    # then a ranking could move because an unrelated competitor changed
    # strategy, which is not a comparison.
    alone = run({"churner": Churner()})
    crowded = run({"churner": Churner(), "noise": Churner()})
    assert alone["churner"].pnl == crowded["churner"].pnl


def test_the_whole_evaluation_is_reproducible():
    assert run({"x": Churner()})["x"].as_dict() == run({"x": Churner()})["x"].as_dict()


# --------------------------------------------------------------------------
# The agent does not see the answer
# --------------------------------------------------------------------------

def test_the_observation_carries_no_ground_truth():
    """Prices, book and own position — nothing only the simulator knows.

    Handing an agent `mispricing_s` or the factor attribution would make the
    exercise trivial: the thing it is supposed to infer would be an input.
    """
    seen = {}

    class Peeker:
        def act(self, obs):
            seen["attrs"] = [a for a in dir(obs) if not a.startswith("_")]
            return {}

    run({"p": Peeker()})
    assert "mispricing_s" not in seen["attrs"]
    assert "fair_value" not in seen["attrs"]
    assert "attribution" not in seen["attrs"]
    assert set(seen["attrs"]) >= {"prices", "book", "position", "portfolio"}


def test_the_observation_exposes_a_tradable_book():
    depth = {}

    class Looker:
        def act(self, obs):
            depth["ask"] = obs.book(obs.tickers[0]).best_ask
            depth["price"] = obs.price(obs.tickers[0])
            return {}

    run({"l": Looker()})
    assert depth["ask"] > 0
    # The ask sits above the last print, which is why buying costs the spread.
    assert depth["ask"] >= depth["price"]


# --------------------------------------------------------------------------
# Impact-aware scoring
# --------------------------------------------------------------------------

def test_an_agent_that_trades_heavily_pays_for_its_own_footprint():
    scores = run({"churner": Churner(), "idle": Idle()})
    assert scores["churner"].impact_bps != 0
    assert scores["idle"].impact_bps == 0
    assert scores["churner"].trades > 10


def test_a_leverage_cap_binds():
    # An agent that can trade arbitrary size is not being tested against the
    # market: with no funding limit, "trade everything" wins.
    # The cap has to bind against the size this agent can ACTUALLY get, which
    # is far below what it asks for. It requests three times average daily
    # volume, but the least liquid name's book holds only about ten thousand
    # shares in total, so the fill is partial and peak leverage reaches only
    # ~0.004x on $200m. A 0.5x cap would never fire, and the test would assert
    # nothing while looking green.
    #
    # That the book caps the position before the risk limit does is itself the
    # point: you cannot buy what is not there.
    capped = run({"churner": Churner()}, max_leverage=0.001)
    loose = run({"churner": Churner()}, max_leverage=None)
    assert capped["churner"].rejected > 0
    assert capped["churner"].max_leverage <= 0.001
    assert loose["churner"].max_leverage > capped["churner"].max_leverage


def test_turnover_and_trades_are_counted():
    scores = run({"churner": Churner()})
    assert scores["churner"].turnover > 0
    assert scores["churner"].trades > 0


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------

def test_a_broken_agent_is_scored_not_crashed():
    # A harness that died on one bad agent would lose every other agent's
    # result in the same run.
    scores = run({"broken": Broken(), "fine": BuyFirst()})
    assert scores["broken"].pnl == 0
    assert len(scores["broken"].errors) > 0
    assert "bad strategy" in scores["broken"].errors[0]
    assert scores["fine"].trades == 1


def test_a_refused_trade_is_information_not_an_exception():
    scores = run({"churner": Churner()}, max_leverage=0.0001)
    assert scores["churner"].rejected > 0
    assert scores["churner"].errors


# --------------------------------------------------------------------------
# Reasoning
# --------------------------------------------------------------------------

def test_explanations_are_scored_against_the_actual_dominant_factor():
    # The distinctive check: was the agent right for the right REASONS, not
    # only profitable. Nothing real can be asked this.
    scores = run({"right": Explainer("order_flow_impact"),
                  "wrong": Explainer("company_news")})
    for name in ("right", "wrong"):
        assert scores[name].explanation_accuracy is not None
        assert len(scores[name].explanations) == 3


def test_an_agent_without_explain_is_simply_not_scored_on_it():
    scores = run({"idle": Idle()})
    assert scores["idle"].explanation_accuracy is None


# --------------------------------------------------------------------------
# Leaderboard
# --------------------------------------------------------------------------

def test_the_leaderboard_orders_best_first_and_breaks_ties_by_name():
    # A leaderboard whose order depended on dict insertion would rank
    # differently for reasons that have nothing to do with the agents.
    scores = run({"zeta": Idle(), "alpha": Idle()})
    ranked = pretium.leaderboard(scores)
    assert [s.name for s in ranked] == ["alpha", "zeta"]


def test_impact_ranks_as_a_cost_so_lower_is_better():
    # impact_bps is signed as a COST to the agent, so ranking ascending puts
    # the cheapest footprint first. An agent whose own trading happened to
    # move the market in its favour scores better than one that paid for it,
    # which is the correct reading of a cost.
    scores = run({"churner": Churner(), "idle": Idle()})
    ranked = pretium.leaderboard(scores, by="impact_bps")
    assert ranked[0].impact_bps <= ranked[-1].impact_bps


def test_an_unknown_ranking_key_is_refused():
    with pytest.raises(pretium.ValidationError, match="cannot rank"):
        pretium.leaderboard(run({"idle": Idle()}), by="vibes")


def test_no_agents_is_refused():
    with pytest.raises(pretium.ValidationError, match="no agents"):
        pretium.evaluate({}, seed=1, universe=UNIVERSE)


# --------------------------------------------------------------------------
# Stepping is how the agent gets a turn, not a change to the market
# --------------------------------------------------------------------------


def test_a_stepped_day_is_the_same_market_as_one_session():
    """The property the whole evaluation harness rests on.

    An agent is given turns by splitting the day into steps. If that split
    changed the market, every score would be measured somewhere the model does
    not describe.

    It did. Each step started at the harness's `start` time, so a six-step day
    replayed the market open six times instead of traversing a trading day.
    Measured on twenty names: 1,840,015,161 shares of volume against
    1,181,790,628 for the same day as one 390-tick session -- 56% too much,
    because the busiest hour was counted six times.

    With the clock advancing it is bit-identical, and that is asserted for
    several splits, because a single split could agree by coincidence.
    """
    universe = pretium.Universe.random(20, seed=5)
    reference = pretium.Engine(seed=1, universe=universe)
    reference.open_market()
    reference.run_session(9, 30, 3, 390)
    reference.close_market()

    for steps, per_step in ((2, 195), (3, 130), (6, 65)):
        stepped = harness._run_untraded(
            1, universe, None, 1, steps, per_step, 9, 30, 3)
        assert stepped == _f64(reference.prices()), (
            f"{steps} steps of {per_step} ticks is not the same day"
        )


def test_the_clock_advances_by_a_minute_a_tick():
    from pretium.harness import session_clock

    assert [session_clock((9, 30, 3), k, 65) for k in range(6)] == [
        (9, 30, 3), (10, 35, 3), (11, 40, 3),
        (12, 45, 3), (13, 50, 3), (14, 55, 3),
    ]


def test_the_clock_wraps_rather_than_running_past_midnight():
    from pretium.harness import session_clock

    # The engine refuses an hour outside 0..24, so a long configuration must
    # wrap. The day of week deliberately does not advance -- a "day" here is
    # the caller's loop iteration, and rolling it silently would drop a
    # Saturday into the middle of a five-day evaluation.
    assert session_clock((23, 30, 3), 1, 60) == (0, 30, 3)
    assert session_clock((9, 30, 3), 30, 60) == (15, 30, 3)
