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
from typing import TYPE_CHECKING, Any, Literal, Protocol, Sequence

from ._core import Engine, Instrument, Macro, ModelParams, OrderError, ValidationError
from .portfolio import Portfolio
from .universe_util import fingerprint_of

if TYPE_CHECKING:
    # Runtime import happens inside evaluate(): spec builds agents from
    # baselines, baselines imports this module, so a top-level import here
    # would be a cycle.
    from .spec import StrategySpec


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

    ## ``step`` counts the WHOLE RUN, not the day

    It is a running index over every decision point in the evaluation, so at
    the harness default of six steps a day, day 1 begins at ``step == 6`` and
    day 3 at ``step == 18``. Only day zero starts at zero.

    That has cost people real runs. ``if obs.step != 0: return {}`` reads like
    a once-a-day guard and is a once-a-RUN guard: the agent trades on the
    first step of day zero and never again, produces a scorecard with
    ``trades=1`` and an empty ``errors`` list, and looks exactly like an agent
    that considered the market and declined. Nothing in the result says
    otherwise.

    The library cannot refuse that, because it is arithmetic on an integer
    and there is no call to intercept — so the answer is to make the
    within-day index a thing you can ASK for rather than a thing you have to
    derive. Use :attr:`step_of_day`, or the two predicates:

    ```python
    if not obs.is_first_step_of_day:
        return {}                     # once a day, correctly
    ```

    ``step`` itself stays a run-wide counter: it is what makes an
    observation's position in the run unambiguous, it is what the fills table
    stamps, and changing its meaning would silently re-time every agent
    already written against it — the same defect in a new place.
    """

    __slots__ = ("step", "day", "tickers", "prices", "portfolio", "engine",
                 "_adv", "steps_per_day")

    def __init__(self, step, day, tickers, prices, portfolio, engine, adv,
                 steps_per_day=1):
        # Run-wide, NOT within-day. See the class docstring: `step_of_day` is
        # the one that resets, and is what a per-day guard wants.
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

    @property
    def step_of_day(self) -> int:
        """This step's index WITHIN the day: 0 at every open.

        The value ``obs.step`` is usually mistaken for. Derived rather than
        stored so it cannot disagree with ``step`` and ``steps_per_day``,
        which is the same expression the harness itself uses to advance the
        session clock and to stamp fills.
        """
        return self.step % self.steps_per_day

    @property
    def is_first_step_of_day(self) -> bool:
        """True on the day's opening decision point.

        The once-a-day guard, spelled so it cannot be confused with
        once-a-run: ``if not obs.is_first_step_of_day: return {}``.
        """
        return self.step_of_day == 0

    @property
    def is_last_step_of_day(self) -> bool:
        """True on the day's final decision point, before the close.

        The other half of a daily cadence: flattening or rebalancing into the
        close is a different decision from the one at the open, and both need
        a name that does not depend on the caller knowing ``steps_per_day``.
        """
        return self.step_of_day == self.steps_per_day - 1

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
        # `step` is printed as "N of M" so that anyone who prints an
        # observation while debugging a per-day guard sees immediately that it
        # counts the run rather than the day -- which is the point at which
        # the trap is usually discovered, or missed.
        return (f"Observation(day={self.day}, "
                f"step_of_day={self.step_of_day}/{self.steps_per_day}, "
                f"step={self.step} of run, n={len(self.tickers)})")


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
                 "final_net_worth", "errors", "seed", "universe_fingerprint",
                 "strategy_fingerprint", "model_fingerprint")

    def __init__(
        self, *, name: str, pnl: float, return_pct: float, trades: int,
        turnover: float, impact_bps: float, max_leverage: float, rejected: int,
        explanations: list[tuple[str, str]], explanation_accuracy: float | None,
        final_net_worth: float, errors: list[str], seed: int = -1,
        universe_fingerprint: str = "", strategy_fingerprint: str = "",
        model_fingerprint: str = "",
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
        # And what STRATEGY earned it. Filled when the agent was built from a
        # StrategySpec (or carries one); empty for a hand-written Python
        # agent, which is the honest reading -- such a result is reproducible
        # only by citing code at a commit, not from this card.
        self.strategy_fingerprint = strategy_fingerprint
        # And what MODEL priced it. A shipped preset's name, or
        # custom-XXXXXXXX for a run under a modified coefficient set --
        # the same honesty mechanism as the strategy fingerprint, so a
        # leaderboard row under a non-shipped model can never present as
        # the benchmark market.
        self.model_fingerprint = model_fingerprint

    def as_dict(self) -> dict[str, Any]:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    def __repr__(self) -> str:
        return (
            f"Scorecard({self.name!r}, pnl={self.pnl:,.0f}, "
            f"return={self.return_pct:+.2f}%, trades={self.trades}, "
            f"impact={self.impact_bps:+.2f}bps)"
        )


def session_clock(start: tuple[int, int, int], step_within_day: int,
                  ticks_per_step: int) -> tuple[int, int, int]:
    """The wall-clock time step ``step_within_day`` begins at.

    A tick is a minute, so a step of ``ticks_per_step`` ticks advances the
    clock by that many minutes. Without this every step of a day started at
    ``start`` and the market open was replayed N times instead of a trading
    day being traversed.

    That was not cosmetic. Time of day drives the intraday activity profile:
    measured on twenty names, a day run as six 65-tick steps all starting at
    09:30 produced **1,840,015,161** shares of volume against **1,181,790,628**
    for the same day run as one 390-tick session -- 56% too much, because the
    busiest hour was counted six times.

    With the clock advancing, a stepped day is **bit-identical** to the single
    session: prices, GARCH variance and draw count, for every split tried
    (2x195, 3x130, 4x100, 6x65). That is the property an evaluation harness
    needs -- stepping is how the agent is given a turn, and it must not be a
    change to the market.

    Steps that run past the close are allowed rather than refused. The engine
    models after-hours as reduced activity rather than as nothing (measured:
    about 25 draws a tick against 49 while open), so a caller who configures
    more minutes than a session holds gets a modelled evening, not silence.
    """
    hour, minute, day_of_week = start
    total = hour * 60 + minute + step_within_day * ticks_per_step
    # Wrapped rather than allowed to exceed 24, which the engine refuses. The
    # day of week is deliberately NOT advanced: a "day" here is the caller's
    # loop iteration, and rolling it silently would put a Saturday in the
    # middle of someone's five-day evaluation.
    return (total // 60) % 24, total % 60, day_of_week


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
    agents: dict[str, Agent | StrategySpec],
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
    model: str | ModelParams | None = None,
) -> dict[str, Scorecard]:
    """Run every agent against an identical market and score them.

    One market. Every agent meets the same one, which is what makes the
    comparison exact -- but a verdict from a single seed is a measurement of
    that seed as much as of the agents. See :func:`pretium.rank` for the
    across-seed version, and :func:`leaderboard` for the measured size of the
    effect.

    ``max_leverage`` defaults to 2x rather than to unlimited. An agent that can
    trade arbitrary size is not being tested against the market: the book makes
    large trades expensive, but with no funding limit arbitrarily large is
    always available and "trade everything" wins. Pass ``None`` deliberately if
    that is what you want to study.

    A value in ``agents`` may be a :class:`pretium.StrategySpec` instead of a
    built agent. The spec is built HERE, freshly, on every call — which is
    both what makes a spec-carrying result citable (the scorecard's
    ``strategy_fingerprint`` names exactly what ran) and what closes a real
    trap: agents are stateful, and a built instance reused across two
    evaluations carries the first market's history into the second with no
    visible symptom. A spec cannot, because it is not the agent; it is the
    instruction for building one.

    ``model`` selects the coefficient set every engine in the evaluation
    runs — a preset name or a :class:`pretium.ModelParams` — and defaults
    to the shipped preset. One model for the whole evaluation, baseline
    included: scoring agents across different models would compare markets,
    not agents. Each scorecard records ``model_fingerprint``.

    Returns a scorecard per agent, keyed by name.
    """
    from .spec import StrategySpec
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
                             scenario, model)

    for name, entry in agents.items():
        agent = entry.build() if isinstance(entry, StrategySpec) else entry
        # Built agents carry their spec (build() attaches it), so the
        # fingerprint flows whether the caller passed the spec or the agent
        # it built. A hand-written agent has none, and its card says so.
        declared = getattr(agent, "spec", None)
        strategy_fingerprint = (declared.fingerprint
                                if isinstance(declared, StrategySpec) else "")
        results[name] = _evaluate_one(
            name, agent, seed, universe, macro, days, steps_per_day,
            ticks_per_step, cash, max_leverage, hour, minute, day_of_week,
            baseline, scenario, fingerprint, strategy_fingerprint, model,
        )
    return results


def _run_untraded(seed, universe, macro, days, steps_per_day, ticks_per_step,
                  hour, minute, day_of_week, scenario=None,
                  model=None) -> list[float]:
    engine = Engine(seed=seed, universe=universe, macro_state=macro,
                    model=model)
    for day in range(days):
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        for step in range(steps_per_day):
            # The same advancing clock as the traded run. If this stepped
            # differently the two worlds would differ for a reason that had
            # nothing to do with the agent.
            engine.run_session(*session_clock((hour, minute, day_of_week),
                                              step, ticks_per_step),
                               ticks_per_step)
        engine.close_market()
    return _f64(engine.prices())


def _evaluate_one(name, agent, seed, universe, macro, days, steps_per_day,
                  ticks_per_step, cash, max_leverage, hour, minute,
                  day_of_week, baseline, scenario=None,
                  fingerprint="", strategy_fingerprint="",
                  model=None) -> Scorecard:
    engine = Engine(seed=seed, universe=universe, macro_state=macro,
                    model=model)
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
            # The within-day tick the fill lands on: agents act at the START of
            # a step, so `ticks_per_step` ticks per completed step have
            # run this day. This is what makes the fills table joinable
            # to bars and truth on (day, tick, instrument_id).
            portfolio.stamp(day, step, (step % steps_per_day) * ticks_per_step)
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

            # The clock advances with the step, so a day of N steps traverses
            # a trading day instead of replaying its first minutes N times.
            step_hour, step_minute, step_dow = session_clock(
                (hour, minute, day_of_week), step % steps_per_day,
                ticks_per_step)
            engine.run_session(step_hour, step_minute, step_dow, ticks_per_step,
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
        strategy_fingerprint=strategy_fingerprint,
        model_fingerprint=engine.model_fingerprint,
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
    """Scorecards sorted best-first, for ONE market.

    Ties break on name, so the order is total and reproducible. A leaderboard
    whose order depended on dict insertion would rank differently for reasons
    that have nothing to do with the agents.

    .. warning::

       This ranks the seed at least as much as the agents, and the effect is
       not subtle. Measured on the reference agents over twelve ten-day
       markets on ``Universe.random(30, seed=11)``, a single seed usually
       NAMES the across-seed leader — nine times in twelve — but what it
       says that leader is worth ranges from a capture of +0.007 to +2.834
       depending only on which market it drew.

       Use this to read one market. To rank agents, use :func:`pretium.rank`,
       which takes the verdict across seeds and reports a paired sign test
       saying whether the ordering is established at all -- because even the
       across-seed aggregate can order two agents that a paired test cannot
       separate.
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
