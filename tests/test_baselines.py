"""The reference agents, and the Oracle as a measuring instrument.

Numbers asserted here were measured, not chosen. Where a test pins an ordering
or a ratio it is recording what the model actually does, so a change in the
model shows up as a failing test rather than as a quietly different leaderboard.
"""

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
    assert ranked == ["oracle", "momentum", "buy_and_hold", "random",
                      "mean_reversion"]


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
    assert capture_ratio(short)["momentum"] < 0.4
    assert capture_ratio(long)["momentum"] > 0.8


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
