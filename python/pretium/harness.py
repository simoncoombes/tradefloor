"""Evaluate trading agents against identical markets.

## What this measures, and what it does not

It compares agents against each other on a market that never existed. That is
worth being precise about, because the obvious stronger claim is false.

What it gives you:

- **No contamination.** The market was generated, not recorded, so no model has
  read its history. A backtest on real data cannot say that.
- **Identical conditions.** Every agent gets its own engine built from the same
  seed, so they face the same market, not a similar one.
- **Ground truth.** The simulator knows why each price moved, so an agent's
  stated reasoning can be checked rather than only its P&L.
- **Real impact.** Fills come from the order book and trading feeds back as
  pressure, so size costs money and cannot be ignored.

What it does NOT give you: evidence that an agent would trade real markets
well. This is a *model* market with knowable structure — a mean-reverting
mispricing process anchored to a computable fair value — and a determined agent
can learn that structure in ways that will not transfer. Use it to rank agents
against each other, not to certify one as good at trading.

## Each agent gets its own market

They do not trade against each other. That is a deliberate choice and it is a
trade-off: agents sharing one market would interact realistically, but then
each agent's result would depend on what the others did, and a comparison in
which the ranking moves when an unrelated competitor changes strategy is not a
comparison. Identical independent markets keep the contrast clean.

## The agent does not see the answer

The observation carries prices, the book and the agent's own portfolio. It does
NOT carry ``mispricing_s``, fair value, or the factor attribution — those are
what the agent is supposed to infer, and handing them over would make the
exercise trivial. They are used for SCORING, on the other side of the wall.
"""

from __future__ import annotations

import struct
from typing import Any, Literal, Protocol, Sequence

from ._core import Engine, Instrument, Macro, OrderError, ValidationError
from .portfolio import Portfolio
from .universe_util import fingerprint_of


# The seven components, as literals a checker can match against
# Engine.attribution's accepted values. Engine.FACTORS returns the same names
# at runtime, but as plain strings.
#
# Three of them -- reversion, momentum, crowd_lean -- are the model's own
# dynamics rather than shocks, and they are here because they genuinely move
# prices. An "explanation" that could only ever name a shock would be unable
# to say "nothing happened; it drifted back toward fair value", which is the
# correct answer most of the time.
FACTOR_NAMES: tuple[
    Literal["reversion"], Literal["momentum"], Literal["crowd_lean"],
    Literal["company_news"], Literal["order_flow_impact"],
    Literal["short_squeeze_effect"], Literal["random_noise"],
] = ("reversion", "momentum", "crowd_lean", "company_news",
     "order_flow_impact", "short_squeeze_effect", "random_noise")


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


class Observation:
    """What an agent sees at one decision point.

    Deliberately narrow. Everything here is something a real trader could
    observe: prices, the book, their own position. Nothing here is something
    only the simulator knows.
    """

    __slots__ = ("step", "day", "tickers", "prices", "portfolio", "engine",
                 "_adv", "steps_per_day")

    def __init__(self, step, day, tickers, prices, portfolio, engine, adv,
                 steps_per_day=1):
        self.step = step
        self.day = day
        # Exposed because an agent reasoning about a HORIZON needs it and
        # cannot infer it: at step zero there is nothing to infer it from.
        # Without this, "a one-day lookback" is unwriteable except by
        # hard-coding the harness's default and hoping it is not changed.
        self.steps_per_day = steps_per_day
        self.tickers = tickers
        self.prices = prices
        self.portfolio = portfolio
        self.engine = engine
        self._adv = adv

    def price(self, ticker: str) -> float:
        return self.prices[self.tickers.index(ticker)]

    def book(self, ticker: str):
        """The live order book, as a trader would see the depth."""
        return self.engine.book(ticker)

    def avg_volume(self, ticker: str) -> float:
        """Average daily volume — public information a real trader has.

        Exposed because it is how size should be reasoned about. Impact scales
        with participation, not with notional: 13.7 million shares is 0.05x a
        day's volume in one name here and 407x in another, and the same order
        moves the first by nothing and the second by 47%. An agent sizing in
        flat share counts is not choosing a position, it is choosing a
        different experiment per instrument.
        """
        return self._adv[self.tickers.index(ticker)]

    def participation(self, ticker: str, shares: float) -> float:
        """``shares`` as a fraction of the instrument's average daily volume."""
        adv = self.avg_volume(ticker)
        return abs(shares) / adv if adv > 0 else float("inf")

    def position(self, ticker: str) -> float:
        held = self.portfolio.positions.get(ticker)
        return held.quantity if held else 0.0

    def __repr__(self) -> str:
        return f"Observation(step={self.step}, day={self.day}, n={len(self.tickers)})"


class Agent(Protocol):
    """The interface an agent implements.

    ``act`` returns share quantities keyed by ticker: positive buys, negative
    sells, omitted or zero does nothing.

    ``explain`` is optional. When present it returns the factor the agent
    believes drove the largest recent move — one of ``Engine.FACTORS``. That is
    what lets the harness ask whether the agent was right for the right
    reasons, rather than only whether it made money.
    """

    def act(self, obs: Observation) -> dict[str, float]: ...


class Scorecard:
    """One agent's result.

    Attributes are declared rather than assigned through a ``setattr`` loop.
    The loop was shorter and made the class opaque: nothing could introspect
    it, no checker could see a field, and a mistyped key would have set
    nothing and read back ``None``.
    """

    __slots__ = ("name", "pnl", "return_pct", "trades", "turnover", "impact_bps",
                 "max_leverage", "rejected", "explanations", "explanation_accuracy",
                 "final_net_worth", "errors", "seed", "universe_fingerprint")

    def __init__(
        self, *, name: str, pnl: float, return_pct: float, trades: int,
        turnover: float, impact_bps: float, max_leverage: float, rejected: int,
        explanations: list[tuple[str, str]], explanation_accuracy: float | None,
        final_net_worth: float, errors: list[str], seed: int = -1,
        universe_fingerprint: str = "",
    ) -> None:
        self.name = name
        self.pnl = pnl
        self.return_pct = return_pct
        self.trades = trades
        self.turnover = turnover
        self.impact_bps = impact_bps
        self.max_leverage = max_leverage
        self.rejected = rejected
        self.explanations = explanations
        self.explanation_accuracy = explanation_accuracy
        self.final_net_worth = final_net_worth
        self.errors = errors
        # What market this score came from. A leaderboard without it is a
        # table of numbers that cannot be re-run: the seed alone does not
        # identify a market, because the same seed over a different roster is
        # a different market -- and tickers do not distinguish rosters, since
        # they are generated positionally.
        self.seed = seed
        self.universe_fingerprint = universe_fingerprint

    def as_dict(self) -> dict[str, Any]:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    def __repr__(self) -> str:
        return (
            f"Scorecard({self.name!r}, pnl={self.pnl:,.0f}, "
            f"return={self.return_pct:+.2f}%, trades={self.trades}, "
            f"impact={self.impact_bps:+.2f}bps)"
        )


def _dominant_factor(engine: Engine) -> str | None:
    """The factor with the largest absolute contribution across the roster.

    Summed over instruments in absolute value, because a factor that pushed two
    names in opposite directions still explains both moves. Netting them would
    report a busy factor as an idle one.
    """
    best, best_size = None, 0.0
    for name in FACTOR_NAMES:
        size = sum(abs(x) for x in _f64(engine.attribution(name)))
        if size > best_size:
            best, best_size = name, size
    return best


def evaluate(
    agents: dict[str, Agent],
    *,
    seed: int,
    universe: Sequence[Instrument],
    macro: Macro | None = None,
    days: int = 5,
    steps_per_day: int = 6,
    ticks_per_step: int = 65,
    cash: float = 1_000_000.0,
    max_leverage: float | None = 2.0,
    start: tuple[int, int, int] = (9, 30, 3),
    scenario: Any = None,
) -> dict[str, Scorecard]:
    """Run every agent against an identical market and score them.

    ``max_leverage`` defaults to 2x rather than to unlimited. An agent that can
    trade arbitrary size is not being tested against the market: the book makes
    large trades expensive, but with no funding limit arbitrarily large is
    always available and "trade everything" wins. Pass ``None`` deliberately if
    that is what you want to study.

    Returns a scorecard per agent, keyed by name.
    """
    if not agents:
        raise ValidationError("no agents given")
    if days < 1 or steps_per_day < 1 or ticks_per_step < 1:
        raise ValidationError("days, steps_per_day and ticks_per_step must be >= 1")

    hour, minute, day_of_week = start
    results: dict[str, Scorecard] = {}

    # The baseline market: the same seed with nobody trading. Every agent's
    # impact is measured against this, so it is computed once rather than per
    # agent -- and because it is the same run each time, the comparison between
    # two agents' impact is a comparison and not two separate experiments.
    # Computed ONCE for the whole evaluation. Every agent runs the same
    # market, so a per-agent hash would be the same value hashed N times.
    fingerprint = fingerprint_of(universe)

    baseline = _run_untraded(seed, universe, macro, days, steps_per_day,
                             ticks_per_step, hour, minute, day_of_week,
                             scenario)

    for name, agent in agents.items():
        results[name] = _evaluate_one(
            name, agent, seed, universe, macro, days, steps_per_day,
            ticks_per_step, cash, max_leverage, hour, minute, day_of_week,
            baseline, scenario, fingerprint,
        )
    return results


def _run_untraded(seed, universe, macro, days, steps_per_day, ticks_per_step,
                  hour, minute, day_of_week, scenario=None) -> list[float]:
    engine = Engine(seed=seed, universe=universe, macro_state=macro)
    for day in range(days):
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        for _ in range(steps_per_day):
            engine.run_session(hour, minute, day_of_week, ticks_per_step)
        engine.close_market()
    return _f64(engine.prices())


def _evaluate_one(name, agent, seed, universe, macro, days, steps_per_day,
                  ticks_per_step, cash, max_leverage, hour, minute,
                  day_of_week, baseline, scenario=None,
                  fingerprint="") -> Scorecard:
    engine = Engine(seed=seed, universe=universe, macro_state=macro)
    portfolio = Portfolio(cash=cash, max_leverage=max_leverage)
    tickers = engine.tickers
    adv = [inst.avg_volume for inst in universe]

    trades = 0
    turnover = 0.0
    rejected = 0
    errors: list[str] = []
    peak_leverage = 0.0
    explanations: list[tuple[str, str]] = []

    step = 0
    for day in range(days):
        # Applied before the day opens, so day zero already runs under the
        # path rather than under whatever the engine was constructed with.
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        for _ in range(steps_per_day):
            obs = Observation(step, day, tickers, _f64(engine.prices()),
                              portfolio, engine, adv, steps_per_day)
            portfolio.stamp(day, step)
            try:
                orders = agent.act(obs) or {}
            except Exception as exc:                      # noqa: BLE001
                # An agent that throws is scored, not crashed. A harness that
                # died on one bad agent would lose every other agent's result
                # in the same run.
                errors.append(f"step {step}: {type(exc).__name__}: {exc}")
                orders = {}

            for ticker, quantity in orders.items():
                if not quantity:
                    continue
                try:
                    fill = portfolio.execute(engine, ticker, quantity)
                    trades += 1
                    turnover += abs(fill["notional"])
                except (OrderError, ValidationError) as exc:
                    # Refused trades are counted rather than raised. Being
                    # unable to size a position is information about the agent.
                    rejected += 1
                    errors.append(f"step {step}: {exc}")

            engine.run_session(hour, minute, day_of_week, ticks_per_step,
                               order_flow=portfolio.pending_flow())
            portfolio.clear_flow()

            leverage = portfolio.leverage(engine)
            if leverage != float("inf"):
                peak_leverage = max(peak_leverage, leverage)
            step += 1

        explain = getattr(agent, "explain", None)
        if callable(explain):
            actual = _dominant_factor(engine)
            try:
                claimed = explain(day)
            except Exception as exc:                      # noqa: BLE001
                errors.append(f"day {day} explain: {type(exc).__name__}: {exc}")
                claimed = None
            if claimed is not None and actual is not None:
                explanations.append((claimed, actual))

        engine.close_market()

    final = portfolio.net_worth(engine)
    actual_prices = _f64(engine.prices())

    # Impact: what this agent's own trading did to the market it traded in.
    # Weighted by the notional it put through each name, so a big move in a
    # name it barely touched does not dominate a small move in the one it
    # traded all day.
    impact_bps = _impact_bps(portfolio, tickers, baseline, actual_prices)

    accuracy = (
        sum(1 for claimed, actual in explanations if claimed == actual)
        / len(explanations)
        if explanations else None
    )

    return Scorecard(
        name=name,
        pnl=final - cash,
        return_pct=(final - cash) / cash * 100.0,
        trades=trades,
        turnover=turnover,
        impact_bps=impact_bps,
        max_leverage=peak_leverage,
        rejected=rejected,
        explanations=explanations,
        explanation_accuracy=accuracy,
        final_net_worth=final,
        errors=errors,
        seed=seed,
        universe_fingerprint=fingerprint,
    )


def _impact_bps(portfolio, tickers, baseline, actual) -> float:
    """Notional-weighted impact, signed so positive is worse for the trader."""
    traded: dict[str, float] = {}
    direction: dict[str, float] = {}
    for fill in portfolio.fills:
        traded[fill["ticker"]] = traded.get(fill["ticker"], 0.0) + abs(fill["notional"])
        direction[fill["ticker"]] = direction.get(fill["ticker"], 0.0) + fill["quantity"]

    if not traded:
        return 0.0

    total = sum(traded.values())
    weighted = 0.0
    for ticker, notional in traded.items():
        i = tickers.index(ticker)
        if baseline[i] == 0:
            continue
        bps = (actual[i] - baseline[i]) / baseline[i] * 10_000
        # A buyer who lifted the price paid for it; a seller who pushed it down
        # did too. Signed so positive always means worse for the agent.
        if direction[ticker] < 0:
            bps = -bps
        weighted += bps * (notional / total)
    return weighted


def leaderboard(scores: dict[str, Scorecard], by: str = "pnl") -> list[Scorecard]:
    """Scorecards sorted best-first.

    Ties break on name, so the order is total and reproducible. A leaderboard
    whose order depended on dict insertion would rank differently for reasons
    that have nothing to do with the agents.
    """
    if by not in ("pnl", "return_pct", "impact_bps", "turnover"):
        raise ValidationError(f"cannot rank by {by!r}")
    # Impact is a cost, so less is better; everything else is more-is-better.
    #
    # The direction is applied by NEGATING the metric rather than by
    # `reverse=True`, because reverse would flip the name tiebreak too and
    # rank ties reverse-alphabetically. That is a real bug I shipped and a
    # test caught: two agents with identical scores came back zeta-then-alpha.
    descending = by != "impact_bps"
    sign = -1.0 if descending else 1.0
    return sorted(scores.values(), key=lambda s: (sign * getattr(s, by), s.name))
