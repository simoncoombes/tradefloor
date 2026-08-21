"""The reference agents, and the Oracle as a measuring instrument.

Numbers asserted here were measured, not chosen. Where a test pins an ordering
or a ratio it is recording what the model actually does, so a change in the
model shows up as a failing test rather than as a quietly different leaderboard.
"""

import statistics
import struct

import pytest

import pretium
from pretium.baselines import (
    BuyAndHold,
    MeanReversion,
    Momentum,
    Oracle,
    RandomTrader,
    capture_ratio,
    rebalance,
    reference_agents,
)
from pretium.harness import Observation


UNIVERSE = pretium.Universe.random(40, seed=7)
SMALL = pretium.Universe.random(12, seed=3)


@pytest.fixture(scope="module")
def scores():
    return pretium.evaluate(reference_agents(seed=3), seed=2026,
                            universe=UNIVERSE, days=5)


# --------------------------------------------------------------------------
# The reference set as a whole
# --------------------------------------------------------------------------


def test_every_reference_agent_runs_without_error(scores):
    assert set(scores) == {"buy_and_hold", "random", "momentum",
                           "mean_reversion", "oracle"}
    for card in scores.values():
        assert card.errors == [], card.errors
    # An error-free run of agents that never traded is not a passing run. The
    # emptiness above is satisfied just as well by every agent returning {}.
    for name, card in scores.items():
        assert card.trades > 0, name


def test_the_oracle_sets_the_ceiling(scores):
    # Perfect information about mispricing beats every strategy that has to
    # infer it. If this ever fails, either the Oracle stopped reading the
    # truth column or a baseline started seeing something it should not.
    ceiling = scores["oracle"].pnl
    for name, card in scores.items():
        if name != "oracle":
            assert card.pnl < ceiling, name


def test_the_ordering_of_the_reference_set_is_the_measured_one(scores):
    ranked = [card.name for card in pretium.leaderboard(scores)]
    # The top and bottom are the robust part; the middle two are separated by
    # under half a percentage point and have swapped places twice now under
    # model changes that left everything else intact. Pinned as a full order
    # anyway, because a leaderboard IS an ordering -- but if this fails on a
    # deliberate change, re-measure rather than assuming a regression.
    assert ranked == ["oracle", "momentum", "buy_and_hold", "mean_reversion",
                      "random"]


def test_random_trading_is_close_to_flat_over_a_short_run(scores):
    # The noise floor really is a floor: a coin flip neither makes nor loses
    # much over five days, it just pays costs. Any strategy near this number
    # is measuring its own transaction costs.
    assert abs(scores["random"].return_pct) < 0.5


def test_random_trading_bleeds_over_a_longer_run():
    # And over sixty days the costs compound into a real loss. This is why
    # "beat random" is a weaker bar than it sounds over short horizons and a
    # meaningful one over long ones.
    long_run = pretium.evaluate({"random": RandomTrader(seed=3)}, seed=2026,
                                universe=UNIVERSE, days=60)
    assert long_run["random"].pnl < 0


def test_a_capture_ratio_is_meaningless_without_its_horizon():
    """The measurement that says why the ratio must be quoted with a horizon.

    Mispricing mean-reverts on a sixty-day half-life, so a five-day evaluation
    sees only the beginning of the convergence the Oracle is trading -- and
    momentum, whose signal is the price trend that convergence produces, sees
    even less of it. Measured on this seed: momentum captures 27% of the
    ceiling over five days and 94% over sixty.

    The same agent, the same market, the same Oracle. Only the horizon
    changed, and the headline number more than tripled.
    """
    short = pretium.evaluate({"oracle": Oracle(), "momentum": Momentum()},
                             seed=2026, universe=UNIVERSE, days=5)
    long = pretium.evaluate({"oracle": Oracle(), "momentum": Momentum()},
                            seed=2026, universe=UNIVERSE, days=60)
    assert long["oracle"].pnl > short["oracle"].pnl
    assert capture_ratio(long)["momentum"] > capture_ratio(short)["momentum"]
    assert capture_ratio(short)["momentum"] < 0.55


def test_the_reference_agents_stay_inside_the_leverage_limit(scores):
    # A baseline that fights the leverage cap is not measuring its signal, it
    # is measuring the cap. This failed before `_book` targeted the whole
    # roster: the trend agents never unwound names that dropped out of their
    # top-k, so gross exposure ratcheted up and 72 and 82 trades were refused.
    for card in scores.values():
        assert card.rejected == 0, (card.name, card.rejected)
        assert card.max_leverage <= 2.0


# --------------------------------------------------------------------------
# The Oracle's explanation is a self-test of the scorer
# --------------------------------------------------------------------------


def test_the_oracle_explains_itself_perfectly(scores):
    # Correct by construction: it reads the attribution the scorer checks
    # against. A value below 1.0 means the explanation scorer is broken, not
    # that the Oracle guessed wrong.
    assert scores["oracle"].explanation_accuracy == 1.0
    assert len(scores["oracle"].explanations) == 5


def test_an_agent_without_explain_is_not_scored_on_it(scores):
    # None, not zero. An agent that made no claim did not make a wrong one.
    assert scores["momentum"].explanation_accuracy is None


def test_the_oracle_explains_nothing_before_it_has_acted():
    assert Oracle().explain(0) is None


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_the_whole_evaluation_is_reproducible():
    a = pretium.evaluate(reference_agents(seed=3), seed=11, universe=SMALL, days=3)
    b = pretium.evaluate(reference_agents(seed=3), seed=11, universe=SMALL, days=3)
    assert {k: v.pnl for k, v in a.items()} == {k: v.pnl for k, v in b.items()}


def test_the_random_baseline_is_seeded_not_random():
    a = pretium.evaluate({"r": RandomTrader(seed=5)}, seed=11, universe=SMALL, days=3)
    b = pretium.evaluate({"r": RandomTrader(seed=5)}, seed=11, universe=SMALL, days=3)
    c = pretium.evaluate({"r": RandomTrader(seed=6)}, seed=11, universe=SMALL, days=3)
    assert a["r"].pnl == b["r"].pnl
    assert a["r"].pnl != c["r"].pnl


def test_no_agent_perturbs_the_market_s_draw_schedule():
    """The property the whole same-seed comparison rests on.

    Order flow consumes zero RNG draws, so every agent -- and nobody at all --
    faces the identical sequence of market events. If trading shifted the draw
    schedule, two agents would be running two different experiments and the
    leaderboard would be comparing markets rather than strategies.

    Measured across the full reference set plus an untraded run: 103,740 draws
    in every case.
    """
    counts = set()
    for agent in list(reference_agents(seed=3).values()) + [None]:
        engine = pretium.Engine(seed=99, universe=SMALL)
        portfolio = pretium.Portfolio(cash=1_000_000.0, max_leverage=2.0)
        adv = [i.avg_volume for i in SMALL]
        for day in range(3):
            engine.open_market()
            for step in range(4):
                if agent is not None:
                    prices = list(struct.unpack("<%dd" % len(SMALL),
                                                engine.prices()))
                    obs = Observation(step, day, list(engine.tickers), prices,
                                      portfolio, engine, adv)
                    for ticker, quantity in agent.act(obs).items():
                        try:
                            portfolio.execute(engine, ticker, quantity)
                        except (pretium.OrderError, pretium.ValidationError):
                            pass
                engine.run_session(9, 30, 3, 65,
                                   order_flow=portfolio.pending_flow())
                portfolio.clear_flow()
            engine.close_market()
        counts.add(engine.draws_consumed)
    assert len(counts) == 1, counts


# --------------------------------------------------------------------------
# Individual agents
# --------------------------------------------------------------------------


def test_buy_and_hold_trades_once_and_stops(scores):
    # Not rebalanced, deliberately: a rebalanced equal-weight portfolio is a
    # mean-reversion strategy in disguise and would stop being the null
    # hypothesis.
    assert scores["buy_and_hold"].trades == len(UNIVERSE)


def test_a_trend_agent_holds_cash_through_its_warm_up():
    agent = Momentum(lookback=3)
    engine = pretium.Engine(seed=1, universe=SMALL)
    portfolio = pretium.Portfolio()
    prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
    adv = [i.avg_volume for i in SMALL]
    for step in range(3):
        obs = Observation(step, 0, list(engine.tickers), prices, portfolio,
                          engine, adv)
        # Guessing during warm-up would make the first few steps measure the
        # guess rather than the signal.
        assert agent.act(obs) == {}
    obs = Observation(3, 0, list(engine.tickers), prices, portfolio, engine, adv)
    agent.act(obs)  # the fourth observation completes the lookback


def test_momentum_and_reversion_are_exact_opposites():
    # Driven with synthetic prices rather than a live engine, so the returns
    # are known: AAA..AAF rise by increasing amounts, AAG..AAL fall. Whoever
    # momentum goes long, reversion must go short.
    engine = pretium.Engine(seed=4, universe=SMALL)
    portfolio = pretium.Portfolio()
    adv = [i.avg_volume for i in SMALL]
    tickers = list(engine.tickers)
    first = [100.0] * len(tickers)
    second = [100.0 + (i - len(tickers) / 2) for i in range(len(tickers))]

    up, down = Momentum(lookback=1, top_k=3), MeanReversion(lookback=1, top_k=3)
    warm = Observation(0, 0, tickers, first, portfolio, engine, adv)
    assert up.act(warm) == {} and down.act(warm) == {}

    # Exactly one further observation: a third would append a duplicate, make
    # every return zero, and quietly turn the assertion into a test of the
    # alphabetical tie-break.
    obs = Observation(1, 0, tickers, second, portfolio, engine, adv)
    winners = {t for t, q in up.act(obs).items() if q > 0}
    shorted = {t for t, q in down.act(obs).items() if q < 0}
    assert winners == {"AAJ", "AAK", "AAL"}
    assert winners == shorted


def test_a_lookback_below_one_is_refused():
    with pytest.raises(ValueError, match="lookback"):
        Momentum(lookback=0)


def test_only_the_oracle_is_marked_privileged():
    assert Oracle.privileged is True
    for agent in (BuyAndHold(), RandomTrader(), Momentum(), MeanReversion()):
        assert not getattr(agent, "privileged", False)


# --------------------------------------------------------------------------
# rebalance
# --------------------------------------------------------------------------


def test_rebalance_caps_at_the_participation_limit():
    engine = pretium.Engine(seed=1, universe=SMALL)
    portfolio = pretium.Portfolio(cash=1e12)   # enough to want far too much
    prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
    adv = [i.avg_volume for i in SMALL]
    obs = Observation(0, 0, list(engine.tickers), prices, portfolio, engine, adv)
    orders = rebalance(obs, {t: 1.0 for t in obs.tickers}, max_participation=0.01)
    for ticker, quantity in orders.items():
        assert obs.participation(ticker, quantity) <= 0.01 + 1e-9


def test_rebalance_ignores_dust():
    engine = pretium.Engine(seed=1, universe=SMALL)
    portfolio = pretium.Portfolio()
    prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
    adv = [i.avg_volume for i in SMALL]
    obs = Observation(0, 0, list(engine.tickers), prices, portfolio, engine, adv)
    assert rebalance(obs, {t: 0.0 for t in obs.tickers}) == {}


# --------------------------------------------------------------------------
# capture_ratio
# --------------------------------------------------------------------------


def test_capture_ratio_is_a_fraction_of_the_ceiling(scores):
    ratios = capture_ratio(scores)
    assert "oracle" not in ratios
    assert ratios["momentum"] == pytest.approx(
        scores["momentum"].pnl / scores["oracle"].pnl)
    assert 0.0 < ratios["momentum"] < 1.0


def test_capture_ratio_declines_to_answer_when_the_oracle_lost_money():
    # A ratio against a negative denominator flips sign and ranks the worst
    # agent first. Saying "not measurable here" is true; a confidently wrong
    # table is not.
    class Card:
        def __init__(self, pnl):
            self.pnl = pnl

    assert capture_ratio({"oracle": Card(-1.0), "a": Card(-2.0)}) == {}
    assert capture_ratio({"a": Card(1.0)}) == {}


# --------------------------------------------------------------------------
# Horizons, cadence, and the cost of trading often
# --------------------------------------------------------------------------


def test_lookback_is_counted_in_steps_not_days():
    # The agent sees one observation per decision step, so six is six STEPS.
    # It equals a day only when steps_per_day is six -- which is the harness
    # default, so the default agent is a one-day trader by two defaults
    # happening to match rather than by contract.
    agent = Momentum(lookback=3)
    engine = pretium.Engine(seed=1, universe=SMALL)
    portfolio = pretium.Portfolio()
    prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
    adv = [i.avg_volume for i in SMALL]
    for step in range(3):
        assert agent.act(Observation(step, 0, list(engine.tickers), prices,
                                     portfolio, engine, adv, 6)) == {}
    # The fourth observation completes three steps of history, regardless of
    # how many days that is.
    agent.act(Observation(3, 0, list(engine.tickers), prices, portfolio,
                          engine, adv, 6))


def test_lookback_days_holds_a_horizon_across_cadences():
    """Say what you mean, and it survives a change to steps_per_day.

    Resolved from the observation rather than at construction, because the
    agent cannot know the harness's cadence until it is handed one.
    """
    for steps_per_day, expected in ((3, 3), (6, 6), (12, 12)):
        agent = Momentum(lookback_days=1.0)
        engine = pretium.Engine(seed=1, universe=SMALL)
        portfolio = pretium.Portfolio()
        prices = list(struct.unpack("<%dd" % len(SMALL), engine.prices()))
        adv = [i.avg_volume for i in SMALL]
        agent.act(Observation(0, 0, list(engine.tickers), prices, portfolio,
                              engine, adv, steps_per_day))
        assert agent.lookback == expected


def test_trading_more_often_loses_money_on_the_same_signal():
    """The impact model making "trade more" unprofitable, on its own.

    Horizon held at exactly one day; only the rebalance frequency changes.
    Measured on seed 2026, 40 instruments, 30 days:

        3 steps/day   +88.72%
        6 steps/day   +30.89%
       12 steps/day   -13.18%

    Nothing charges a fee. The orders simply cross a real spread and consume
    real depth four times as often. This is the same mechanism that makes
    "trade bigger" unprofitable, and it is why an agent cannot win here by
    turning the dial up.
    """
    returns = []
    for steps_per_day in (3, 6, 12):
        scores = pretium.evaluate(
            {"m": Momentum(lookback_days=1.0)}, seed=2026, universe=UNIVERSE,
            days=30, steps_per_day=steps_per_day,
            ticks_per_step=390 // steps_per_day)
        returns.append(scores["m"].return_pct)
    assert returns[0] > returns[1] > returns[2]
    assert returns[0] > 50.0
    assert returns[2] < 0.0


def test_the_observation_carries_the_cadence():
    # Without it, "a one-day lookback" is unwriteable except by hard-coding
    # the harness default and hoping nobody changes it.
    seen = []

    class Probe:
        def act(self, obs):
            seen.append(obs.steps_per_day)
            return {}

    pretium.evaluate({"p": Probe()}, seed=1, universe=SMALL, days=2,
                     steps_per_day=4, ticks_per_step=20)
    assert set(seen) == {4}


def test_a_nonsense_lookback_days_is_refused():
    with pytest.raises(ValueError, match="lookback_days"):
        Momentum(lookback_days=0.0)
    with pytest.raises(ValueError, match="lookback_days"):
        MeanReversion(lookback_days=-1.0)


# --------------------------------------------------------------------------
# The Oracle is a reference, not a maximum
# --------------------------------------------------------------------------


def test_only_agents_trading_the_oracles_own_signal_beat_it():
    """The mechanism, not just the phenomenon.

    Measured over 384 agent-seed pairs: mean-reversion beats the Oracle 31.2%
    of the time, momentum 8.3%, and buy-and-hold and random never once in 192
    pairs. That is the evidence that the edge is portfolio construction rather
    than information -- the agents with no mispricing signal at all cannot
    beat it, while the one that estimates the very quantity the Oracle reads
    exactly beats it routinely.

    Asserted as the ORDERING rather than as those rates, which belong to those
    rosters. What must hold is that the mispricing traders beat it strictly
    more often than the agents that do not trade mispricing.
    """
    mispricing_traders = 0
    non_traders = 0
    for useed in (3, 42):
        universe = pretium.Universe.random(20, seed=useed)
        for seed in range(4):
            scores = pretium.evaluate(reference_agents(seed=3), seed=seed,
                                      universe=universe, days=30)
            ratios = capture_ratio(scores)
            if not ratios:
                continue
            mispricing_traders += sum(
                ratios[n] > 1.0 for n in ("mean_reversion", "momentum")
                if n in ratios)
            non_traders += sum(
                ratios[n] > 1.0 for n in ("buy_and_hold", "random")
                if n in ratios)
    assert mispricing_traders > 0, (
        "no mispricing trader beat the Oracle at all -- the demonstration is "
        "vacuous and the rest of this test proves nothing"
    )
    assert non_traders < mispricing_traders, (
        f"agents with no mispricing signal beat the Oracle {non_traders} "
        f"times against {mispricing_traders} for those that trade it; the "
        "documented mechanism does not hold"
    )


def test_an_agent_can_beat_the_oracle():
    """Pinned so nobody "fixes" it into an upper bound.

    The Oracle sees the true mispricing, and it does not follow that nothing
    can beat it. It gets the same gross exposure and participation cap as
    every other baseline and spends them on a naive rule -- equal weight
    across the top_k most mispriced names -- so an agent whose selection suits
    the constraint better out-earns it.

    Measured across eight seeds: momentum beat it twice and mean-reversion
    once, three of thirty-two agent-seed pairs. This asserts the phenomenon
    exists rather than a specific count, because the count is a property of
    the seeds.
    """
    universe = pretium.Universe.random(30, seed=11)
    beaten = 0
    for seed in range(8):
        scores = pretium.evaluate(reference_agents(seed=3), seed=seed,
                                  universe=universe, days=10)
        ceiling = scores["oracle"].pnl
        if any(card.pnl > ceiling for name, card in scores.items()
               if name != "oracle"):
            beaten += 1
    assert beaten > 0, (
        "nothing beat the Oracle in eight seeds -- either the baselines got "
        "worse or the Oracle stopped being a same-constraints reference"
    )


def test_the_oracle_is_capital_limited_not_information_limited():
    """What actually raises the ceiling is gross exposure, not information.

    Spreading the SAME perfect information across three times as many names
    makes it worse -- each position shrinks and turnover rises. Doubling the
    gross makes it dominate. That is the evidence for calling it a reference
    portfolio rather than a maximum.
    """
    universe = pretium.Universe.random(30, seed=11)

    def median_pnl(make_oracle):
        pnls = []
        for seed in range(4):
            scores = pretium.evaluate({"o": make_oracle()}, seed=seed,
                                      universe=universe, days=10)
            pnls.append(scores["o"].pnl)
        return statistics.median(pnls)

    narrow = median_pnl(lambda: Oracle())
    wide = median_pnl(lambda: Oracle(top_k=15))
    levered = median_pnl(lambda: Oracle(top_k=15, gross=2.0))

    assert wide < narrow, "spreading the same information should dilute it"
    assert levered > narrow, "gross exposure is what raises the ceiling"


def test_capture_ratio_reports_above_one_rather_than_clamping():
    # A ratio above 1.0 is a finding about portfolio construction. Clamping it
    # to 1.0 would hide the most interesting result the harness can produce.
    class Card:
        def __init__(self, pnl):
            self.pnl = pnl

    ratios = capture_ratio({"oracle": Card(100.0), "better": Card(150.0)})
    assert ratios["better"] == pytest.approx(1.5)
