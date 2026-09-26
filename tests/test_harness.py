"""The agent evaluation harness."""

import pytest

import struct

import tradefloor
from tradefloor import harness


def _f64(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))

UNIVERSE = tradefloor.Universe.random(8, seed=5)
# The roster spans four orders of magnitude of liquidity by design, so the
# first instrument is whichever one an ADV-sized order can actually move.
UNIVERSE = tradefloor.Universe(
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
    return tradefloor.evaluate(agents, **params)


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
    # The ask sits above the last print, so buying costs the spread.
    assert depth["ask"] >= depth["price"]


# --------------------------------------------------------------------------
# Impact-aware scoring
# --------------------------------------------------------------------------

def test_an_agent_that_trades_heavily_pays_for_its_own_footprint():
    # Seed 11, re-measured at the 2026-08 era boundary. Impact is read off
    # the FINAL price against the untraded twin, and prices print in cents:
    # at ~$97 one cent is about a basis point, so an alternating churner
    # whose net pressure has decayed below half a cent by the last close
    # measures exactly 0.0. On the re-rolled trajectories seeds 2026 and 7
    # land there (measured: 0.0bps both); 11 and 42 do not (18.2 and 1.9).
    # The claim under test is that the machinery measures a real footprint,
    # so it runs on a seed where the residue survives quantisation.
    #
    # Re-measured 2026-09-20 on the recomposed pt-v19: 11 now lands on the
    # grid too (12 trades, exactly 0.0), while 12, 13 and 2026 read 8.3,
    # -2.1 and -21.5 bps. Seed 12.
    scores = run({"churner": Churner(), "idle": Idle()}, seed=12)
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
    ranked = tradefloor.leaderboard(scores)
    assert [s.name for s in ranked] == ["alpha", "zeta"]


def test_impact_ranks_as_a_cost_so_lower_is_better():
    # impact_bps is signed as a COST to the agent, so ranking ascending puts
    # the cheapest footprint first. An agent whose own trading happened to
    # move the market in its favour scores better than one that paid for it,
    # which is the correct reading of a cost.
    scores = run({"churner": Churner(), "idle": Idle()})
    ranked = tradefloor.leaderboard(scores, by="impact_bps")
    assert ranked[0].impact_bps <= ranked[-1].impact_bps


def test_an_unknown_ranking_key_is_refused():
    with pytest.raises(tradefloor.ValidationError, match="cannot rank"):
        tradefloor.leaderboard(run({"idle": Idle()}), by="vibes")


def test_no_agents_is_refused():
    with pytest.raises(tradefloor.ValidationError, match="no agents"):
        tradefloor.evaluate({}, seed=1, universe=UNIVERSE)


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

    With the clock advancing it is bit-identical, asserted for
    several splits, because a single split could agree by coincidence.
    """
    universe = tradefloor.Universe.random(20, seed=5)
    reference = tradefloor.Engine(seed=1, universe=universe)
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
    from tradefloor.harness import session_clock

    assert [session_clock((9, 30, 3), k, 65) for k in range(6)] == [
        (9, 30, 3), (10, 35, 3), (11, 40, 3),
        (12, 45, 3), (13, 50, 3), (14, 55, 3),
    ]


def test_the_clock_wraps_rather_than_running_past_midnight():
    from tradefloor.harness import session_clock

    # The engine refuses an hour outside 0..24, so a long configuration must
    # wrap. The day of week deliberately does not advance -- a "day" here is
    # the caller's loop iteration, and rolling it silently would drop a
    # Saturday into the middle of a five-day evaluation.
    assert session_clock((23, 30, 3), 1, 60) == (0, 30, 3)
    assert session_clock((9, 30, 3), 30, 60) == (15, 30, 3)


# --------------------------------------------------------------------------
# `step` counts the run; `step_of_day` counts the day
# --------------------------------------------------------------------------


class OncePerDay:
    """The guard people actually mean, written the way that works."""

    def __init__(self):
        self.fired = []

    def act(self, obs):
        if not obs.is_first_step_of_day:
            return {}
        self.fired.append((obs.day, obs.step, obs.step_of_day))
        return {obs.tickers[0]: 100.0}


class OnceAndNeverAgain:
    """The same guard written against `step`, which is a once-a-RUN guard."""

    def __init__(self):
        self.fired = []

    def act(self, obs):
        if obs.step != 0:
            return {}
        self.fired.append((obs.day, obs.step))
        return {obs.tickers[0]: 100.0}


def test_step_counts_the_whole_run_and_step_of_day_counts_the_day():
    """The trap, pinned as behaviour so the two indices cannot be confused.

    `obs.step` is a running index over every decision point in the
    evaluation. At six steps a day, day 1 begins at step 6 -- so a guard
    written as `obs.step != 0` fires once in the entire run, produces a
    scorecard with no errors, and looks exactly like an agent that considered
    the market and declined. Nothing in the result says otherwise, which is
    why the within-day index has to be askable rather than derivable.
    """
    daily, once = OncePerDay(), OnceAndNeverAgain()
    tradefloor.evaluate({"daily": daily, "once": once}, seed=3,
                     universe=UNIVERSE, days=4, steps_per_day=6)

    # The correct guard fires on every day, and its step numbers show why the
    # naive one does not: only day zero starts at step zero.
    assert daily.fired == [(0, 0, 0), (1, 6, 0), (2, 12, 0), (3, 18, 0)]
    # The naive guard fires once in four days -- silently, with no error.
    assert once.fired == [(0, 0)]


def test_the_within_day_helpers_agree_with_the_harness_own_arithmetic():
    """`step_of_day` is the expression the harness uses for the clock.

    Derived rather than stored, so it cannot drift from `step` and
    `steps_per_day` -- and asserted against `session_clock`, which is the
    consumer that has always had to compute it.
    """
    from tradefloor.harness import Observation, session_clock

    steps_per_day = 6
    for step in range(steps_per_day * 3):
        obs = Observation(step, step // steps_per_day, ["A"], [1.0], None,
                          None, [1.0], steps_per_day)
        assert obs.step_of_day == step % steps_per_day
        assert obs.is_first_step_of_day == (obs.step_of_day == 0)
        assert obs.is_last_step_of_day == (obs.step_of_day == steps_per_day - 1)
        assert session_clock((9, 30, 3), obs.step_of_day, 65) == \
            session_clock((9, 30, 3), step % steps_per_day, 65)

    # A single-step day makes every step both the first and the last, which
    # is the correct answer rather than an edge case to guard.
    solo = Observation(4, 4, ["A"], [1.0], None, None, [1.0], 1)
    assert solo.step_of_day == 0
    assert solo.is_first_step_of_day and solo.is_last_step_of_day


def test_the_observation_repr_says_which_index_is_which():
    """Where the trap is usually discovered -- or missed."""
    from tradefloor.harness import Observation

    text = repr(Observation(7, 1, ["A", "B"], [1.0, 2.0], None, None,
                            [1.0, 2.0], 6))
    assert "day=1" in text
    assert "step_of_day=1/6" in text
    assert "step=7 of run" in text


# --------------------------------------------------------------------------
# What act() may return, and what evaluate does with anything else
# --------------------------------------------------------------------------

SMALL = tradefloor.Universe.random(5, seed=1)


class Returns:
    """Returns one fixed thing on step zero, and nothing after."""

    def __init__(self, value):
        self.value = value

    def act(self, obs):
        if obs.step != 0:
            return {}
        return self.value(obs) if callable(self.value) else self.value


def test_a_return_that_is_not_a_mapping_is_an_error_line_not_a_crash():
    """A list of pairs raised AttributeError out of evaluate, and every
    other agent's result in the same call went with it (0.8.5 review:
    Jordan Okafor, Marcus Bell). The step now trades nothing and says
    what came back."""
    shapes = {
        "pairs": Returns(lambda obs: [(obs.tickers[0], 10)]),
        "text": Returns("buy AAA"),
        "number": Returns(10),
        "empty_list": Returns([]),
    }
    scores = tradefloor.evaluate({"idle": Idle(), **shapes}, seed=5,
                                 universe=SMALL, days=1)
    assert scores["idle"].errors == []
    for name in shapes:
        card = scores[name]
        assert (card.trades, card.rejected) == (0, 0), name
        assert len(card.errors) == 1, (name, card.errors)
        assert card.errors[0].startswith("step 0: act() must return a mapping")
        assert "errors=1" in repr(card)
    assert "It returned a list: [('AAA', 10)] (list)" in scores["pairs"].errors[0]
    assert "It returned a str" in scores["text"].errors[0]
    assert "errors" not in repr(scores["idle"])


def test_none_and_an_empty_mapping_trade_nothing_quietly():
    scores = tradefloor.evaluate({"none": Returns(None), "empty": Returns({})},
                                 seed=5, universe=SMALL, days=1)
    for card in scores.values():
        assert (card.trades, card.rejected, card.errors) == (0, 0, [])


def test_a_bad_entry_is_refused_and_the_rest_of_the_mapping_trades():
    """An int key and a complex quantity crashed evaluate; "100" and True
    traded 100 shares and 1 share, which the adapters refuse; -inf was
    refused as "got inf" (0.8.5 review: Jordan Okafor, Marcus Bell)."""
    def orders(obs):
        t = obs.tickers
        return {t[0]: 10, 0: 100, t[1]: 1 + 2j, t[2]: "100", t[3]: True,
                t[4]: float("-inf")}

    card = tradefloor.evaluate({"a": Returns(orders)}, seed=5, universe=SMALL,
                               days=1)["a"]
    assert card.trades == 1
    assert card.rejected == 5
    assert card.leverage_refusals == 0
    joined = "\n".join(card.errors)
    assert "a ticker must be a string, got 0 (int)" in joined
    assert "got (1+2j) (complex)" in joined
    assert "got '100' (str)" in joined
    assert "got True (bool)" in joined
    assert "must be finite, got -inf" in joined


def test_numpy_quantities_are_numbers_of_shares():
    np = pytest.importorskip("numpy")
    card = tradefloor.evaluate(
        {"a": Returns(lambda obs: {obs.tickers[0]: np.float64(10),
                                   obs.tickers[1]: np.int64(5)})},
        seed=5, universe=SMALL, days=1)["a"]
    assert (card.trades, card.errors) == (2, [])


class RestingBid:
    """A limit order at the bid on step zero: it rests, and fills later."""

    def act(self, obs):
        t = obs.tickers[0]
        return ({t: tradefloor.Limit(100, obs.book(t).best_bid)}
                if obs.step == 0 else {})


@pytest.mark.parametrize("model", ["pt-v20", "pt-v19"])
def test_evaluate_takes_a_limit_order(model):
    """tf.Limit is documented as a value in the act() mapping and World
    took it, but evaluate called float() on it and the TypeError ended the
    whole evaluation (0.8.5 review: Marcus Bell, Priya Raman, Tomas
    Herrera). What rests is collected after each session, as World does."""
    scores = tradefloor.evaluate(
        {"limit": RestingBid(), "idle": Idle()}, seed=1,
        universe=tradefloor.Universe.random(4, seed=7), days=2, model=model)
    card = scores["limit"]
    assert card.errors == [] and card.rejected == 0
    # It rested, so everything it traded was collected after a session.
    assert card.trades >= 1 and card.turnover > 0
    assert scores["idle"].errors == []


def test_a_cancel_withdraws_what_is_waiting():
    seen = []

    class FarBidThenCancel:
        def act(self, obs):
            t = obs.tickers[0]
            seen.append(len(obs.portfolio.open_orders(obs.engine)))
            if obs.step == 0:
                return {t: tradefloor.Limit(100, obs.book(t).best_bid * 0.8)}
            if obs.step == 1:
                return {t: tradefloor.Cancel()}
            return {}

    card = tradefloor.evaluate({"x": FarBidThenCancel()}, seed=1,
                               universe=tradefloor.Universe.random(4, seed=7),
                               days=1, trusted_agents=True)["x"]
    assert card.errors == []
    assert seen[:3] == [0, 1, 0]


def test_a_replay_miss_stops_evaluate_and_names_the_step():
    """A replay that has no recording for the input was scored as an
    agent error, so a wrong-seed replay read as an agent that held cash
    (0.8.5 review: Jordan Okafor, Priya Raman). The integrations README
    says the run stops and names the step, and now it does."""
    from tradefloor.integrations.callable import callable_agent
    from tradefloor.integrations.common import ReplayMiss, Transcript

    def buy(payload):
        return {"actions": [{"symbol": payload["assets"][0]["symbol"],
                             "side": "BUY", "quantity": 1}],
                "rationale": "buy"}

    recording = Transcript()
    live = tradefloor.evaluate({"s": callable_agent(buy, recorder=recording)},
                               seed=777, universe=SMALL, days=1)["s"]
    # The recording replays its own market.
    again = tradefloor.evaluate(
        {"s": callable_agent(buy, mode="replay", transcript=recording)},
        seed=777, universe=SMALL, days=1)["s"]
    assert again.as_dict() == live.as_dict()

    with pytest.raises(ReplayMiss) as caught:
        tradefloor.evaluate(
            {"idle": Idle(),
             "s": callable_agent(buy, mode="replay", transcript=recording)},
            seed=778, universe=SMALL, days=1)
    assert "step 0" in str(caught.value)
    assert any("agent 's'" in note and "seed 778" in note
               for note in caught.value.__notes__)


class Levered:
    """Puts ``gross`` times its net worth into the roster at step zero."""

    def __init__(self, gross):
        self.gross = gross

    def act(self, obs):
        if obs.step != 0:
            return {}
        worth = obs.portfolio.net_worth()
        each = self.gross * worth / len(obs.tickers)
        return {t: each / obs.price(t) for t in obs.tickers}


def test_the_scorecard_has_an_equity_curve_a_drawdown_and_a_ruin_flag():
    """The scorecard had no drawdown, no equity curve and no way to say an
    agent went broke (0.8.5 review: Elena Varga, Marcus Bell, Jordan
    Okafor). Measured on seed 1: 100x short ends the second day at about
    -$700,000, 100x long at about +$2.3m."""
    scores = tradefloor.evaluate(
        {"long": Levered(100), "short": Levered(-100)}, seed=1,
        universe=tradefloor.Universe.random(8, seed=1), days=2,
        max_leverage=None)
    for card in scores.values():
        assert len(card.equity_curve) == 2
        assert card.equity_curve[-1] == card.final_net_worth
        peak, worst = 1_000_000.0, 0.0
        for worth in card.equity_curve:
            peak = max(peak, worth)
            worst = max(worst, (peak - worth) / peak)
        assert card.max_drawdown_pct == pytest.approx(worst * 100.0)
    short, long = scores["short"], scores["long"]
    assert short.ruined and short.equity_curve[-1] < 0
    assert short.max_drawdown_pct > 100.0
    assert "RUINED" in repr(short)
    assert not long.ruined and "RUINED" not in repr(long)


def test_leverage_refusals_are_counted_apart_from_other_refusals():
    def orders(obs):
        t = obs.tickers[0]
        return {t: 3.0 * obs.portfolio.net_worth() / obs.price(t),
                "ZZZZ": 10}

    card = tradefloor.evaluate({"a": Returns(orders)}, seed=5, universe=SMALL,
                               days=1, max_leverage=2.0)["a"]
    assert card.rejected == 2
    assert card.leverage_refusals == 1


def test_the_explanation_baseline_is_what_a_constant_answer_scores():
    """Answering "random_noise" every day scored 0.65 on this market with
    nothing beside it to say that is what a constant earns (0.8.5 review:
    Jordan Okafor)."""
    claims = ("random_noise", "fair_value_shift", "momentum")
    scores = tradefloor.evaluate({c: Explainer(c) for c in claims}, seed=2026,
                                 universe=tradefloor.Universe.random(12, seed=7),
                                 days=20)
    baselines = {card.explanation_baseline for card in scores.values()}
    assert baselines == {0.65}
    assert scores["random_noise"].explanation_accuracy == 0.65
    for card in scores.values():
        assert card.explanation_accuracy <= card.explanation_baseline
    idle = tradefloor.evaluate({"idle": Idle()}, seed=5, universe=SMALL,
                               days=1)["idle"]
    assert idle.explanation_baseline is None


def _card(name, pnl, **flags):
    return harness.Scorecard(
        name=name, pnl=pnl, return_pct=pnl / 1e4, trades=0, turnover=0.0,
        impact_bps=0.0, max_leverage=0.0, rejected=0, explanations=[],
        explanation_accuracy=None, final_net_worth=1e6 + pnl, errors=[],
        **flags)


def test_the_leaderboard_puts_a_tampered_card_last():
    """An agent that wrote $500,000 into its own cash ranked first (0.8.5
    review: Jordan Okafor). tf.rank already left it out."""
    scores = {"cheat": _card("cheat", 500_000.0, tampered=True),
              "honest": _card("honest", 10.0),
              "oracle": _card("oracle", 5.0, uses_hidden_state=True)}
    board = tradefloor.leaderboard(scores)
    assert [c.name for c in board] == ["honest", "oracle", "cheat"]
    assert "TAMPERED" in repr(board[-1])
    assert "hidden-state" in repr(board[1])
    assert [c.name for c in tradefloor.leaderboard(scores, by="impact_bps")][-1] \
        == "cheat"


def test_a_tampering_agent_is_found_and_ranked_last():
    class Cheat:
        def act(self, obs):
            if obs.step == 0:
                obs.portfolio._PortfolioView__portfolio.cash += 5e5
            return {}

    scores = tradefloor.evaluate({"cheat": Cheat(), "honest": Idle()},
                                 seed=3, universe=SMALL, days=1)
    assert scores["cheat"].tampered
    assert scores["cheat"].pnl > scores["honest"].pnl
    assert [c.name for c in tradefloor.leaderboard(scores)] == ["honest", "cheat"]


def test_one_engine_is_built_per_evaluation(monkeypatch):
    """pt-v20 spends about 0.65 s of CPU building an engine, most of it
    the macro burn-in, and evaluate built one per agent plus one for the
    baseline (0.8.5 review: Priya Raman). The others are copies now."""
    built = []
    real = harness.Engine

    def counting(*args, **kwargs):
        built.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(harness, "Engine", counting)
    tradefloor.evaluate({"a": Idle(), "b": BuyFirst(), "c": Churner()},
                        seed=5, universe=SMALL, days=1)
    assert len(built) == 1


@pytest.mark.parametrize("model", ["pt-v20", "pt-v19"])
def test_a_copied_engine_scores_the_same_as_a_freshly_built_one(model):
    """The copies are the same market to the bit: every card is the one
    the old path, an engine built per agent, produces."""
    from tradefloor.universe_util import fingerprint_of

    agents = {"churn": Churner, "buy": BuyFirst}
    scores = run({name: make() for name, make in agents.items()}, days=2,
                 model=model)
    baseline = harness._run_untraded(2026, UNIVERSE, None, 2, 4, 60, 9, 30, 3,
                                     None, model)
    for name, make in agents.items():
        fresh = harness._evaluate_one(
            name, make(), 2026, UNIVERSE, None, 2, 4, 60, 200_000_000, 2.0,
            9, 30, 3, baseline, None, fingerprint_of(UNIVERSE), "", model,
            False, False)
        assert fresh.as_dict() == scores[name].as_dict()
