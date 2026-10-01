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
well. This is a *model* market with knowable structure, a mean-reverting
mispricing process anchored to a computable fair value, and a determined agent
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
NOT carry ``mispricing_s``, fair value, or the factor attribution. Those are
what the agent is supposed to infer, and handing them over would make the
exercise trivial. They are used for SCORING, on the other side of the wall.

Until 0.8.5 that was a statement about the fields and not about the engine:
``obs.engine`` was the live engine, and an agent could fork it and trade on
the fork's future or write the market with ``set_fundamentals``. It is now a
read-only :class:`~tradefloor.sandbox.MarketView`, the portfolio is a
read-only :class:`~tradefloor.sandbox.PortfolioView`, and the harness checks
the engine's state hash around every call into agent code. An agent that
needs hidden state declares ``privileged = True`` and reads ``obs.hidden``;
research that needs the live engine passes ``trusted_agents=True``. The
scorecard records all three. See :mod:`tradefloor.sandbox`.
"""

from __future__ import annotations

import math
import statistics
import struct
import warnings
from collections import Counter
from typing import TYPE_CHECKING, Any, Literal, Protocol, Sequence

from . import _checks
from ._arith import ordered_sum
from ._core import Engine, Instrument, Macro, ModelParams, OrderError, ValidationError
from ._core import check_seed
from .portfolio import (Cancel, LeverageError, Limit, Portfolio, check_order,
                        order_items)
from .sandbox import (PUBLISHED_MACRO, HiddenState, MarketView,
                      PortfolioView, TamperGuard, declares_hidden_state)
from .universe_util import fingerprint_of

if TYPE_CHECKING:
    # Runtime import happens inside evaluate(): spec builds agents from
    # baselines, baselines imports this module, so a top-level import here
    # would be a cycle.
    from .spec import StrategySpec


# The eleven components, as literals a checker can match against
# Engine.attribution's accepted values. Engine.FACTORS returns the same names
# at runtime, but as plain strings.
#
# Three of them -- reversion, momentum, crowd_lean -- are the model's own
# dynamics rather than shocks, and they are here because they genuinely move
# prices. An "explanation" that could only ever name a shock would be unable
# to say "nothing happened; it drifted back toward fair value", which is the
# correct answer most of the time.
# The ninth, `jump`, arrived on 2026-08-26: `apply_jumps` moves `s` after
# the tick loop, so the eight above did not reconstruct a day on which one
# fired, on any preset carrying jumps (§74). The tenth, `overnight`, arrived
# on 2026-09-04: `apply_overnight` moves `s` at the open before any tick,
# and the tape books it on the day's first row. The eleventh,
# `fair_value_shift`, arrived with pt-v20 (0.8.5): the part of the day's news
# and noise that changed the name's fair value for good, entered as a negative
# because it left the mispricing. The ten above report the whole shock, which
# is what moved the price; zero on every preset through pt-v19.
FACTOR_NAMES: tuple[
    Literal["reversion"], Literal["momentum"], Literal["crowd_lean"],
    Literal["company_news"], Literal["order_flow_impact"],
    Literal["short_squeeze_effect"], Literal["random_noise"],
    Literal["circuit_breaker"], Literal["jump"], Literal["overnight"],
    Literal["fair_value_shift"],
] = ("reversion", "momentum", "crowd_lean", "company_news",
     "order_flow_impact", "short_squeeze_effect", "random_noise",
     "circuit_breaker", "jump", "overnight", "fair_value_shift")

# The answers ``explain`` is scored against: the ten factors that move a
# price. ``fair_value_shift`` is left out because it moves no price. It books
# the part of a shock that left the mispricing for fair value, and the shock's
# own column (``random_noise``, ``company_news`` or ``jump``) already holds the
# whole move. Until 0.8.5's decision 8 the scorer ranked all eleven, so a
# permanent shock counted twice, once in its own column and once, with the
# sign flipped, in ``fair_value_shift``. On pt-v20 that made
# ``fair_value_shift`` the scored answer on about a third of days although it
# moved nothing.
DRIVER_NAMES: tuple[
    Literal["reversion"], Literal["momentum"], Literal["crowd_lean"],
    Literal["company_news"], Literal["order_flow_impact"],
    Literal["short_squeeze_effect"], Literal["random_noise"],
    Literal["circuit_breaker"], Literal["jump"], Literal["overnight"],
] = FACTOR_NAMES[:10]


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
    and there is no call to intercept, so the answer is to make the
    within-day index a thing you can ASK for rather than a thing you have to
    derive. Use :attr:`step_of_day`, or the two predicates:

    ```python
    if not obs.is_first_step_of_day:
        return {}                     # once a day, correctly
    ```

    ``step`` itself stays a run-wide counter: it is what makes an
    observation's position in the run unambiguous, it is what the fills table
    stamps, and changing its meaning would silently re-time every agent
    already written against it, which is the same defect in a new place.
    """

    __slots__ = ("step", "day", "tickers", "prices", "portfolio", "engine",
                 "_adv", "steps_per_day", "hidden", "history")

    def __init__(self, step, day, tickers, prices, portfolio, engine, adv,
                 steps_per_day=1, hidden=None, history=None):
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
        # Under every harness in this package these are a read-only
        # `PortfolioView` and `MarketView` (see `tradefloor.sandbox`), unless
        # the run passed `trusted_agents=True`, in which case they are the
        # live objects. An observation built by hand holds whatever it was
        # given.
        self.portfolio = portfolio
        self.engine = engine
        self._adv = adv
        #: Read-only hidden state, for an agent that declared
        #: ``privileged = True``; None for every other agent.
        self.hidden = hidden
        #: The run's :class:`History`: a daily bar per name and the
        #: published macro for every day closed so far, the warm-up days
        #: first. :func:`evaluate`, :func:`tradefloor.rank`,
        #: :class:`tradefloor.World` and :func:`tradefloor.tca.analyse`
        #: fill it; an observation built by hand has None.
        self.history = history

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

    def _index(self, ticker: str) -> int:
        """Where ``ticker`` sits in :attr:`tickers`, or a refusal that names
        it and, for a slip of case or spacing, the ticker meant.

        ``list.index`` said only "'ZZZZ' is not in list", which is how an
        agent's error line read until 0.8.5.
        """
        try:
            return self.tickers.index(ticker)
        except ValueError:
            raise ValidationError(_unknown_ticker(ticker, self.tickers)) from None

    def price(self, ticker: str) -> float:
        return self.prices[self._index(ticker)]

    def book(self, ticker: str):
        """The live order book, as a trader would see the depth."""
        self._index(ticker)
        return self.engine.book(ticker)

    def avg_volume(self, ticker: str) -> float:
        """Average daily volume, which is public information a real trader has.

        Exposed because it is how size should be reasoned about. Impact scales
        with participation, not with notional: 13.7 million shares is 0.05x a
        day's volume in one name here and 407x in another, and the same order
        moves the first by nothing and the second by 47%. An agent sizing in
        flat share counts is choosing a different experiment per instrument
        rather than a position.
        """
        return self._adv[self._index(ticker)]

    def participation(self, ticker: str, shares: float) -> float:
        """``shares`` as a fraction of the instrument's average daily volume."""
        adv = self.avg_volume(ticker)
        return abs(shares) / adv if adv > 0 else float("inf")

    def position(self, ticker: str) -> float:
        """Shares held in ``ticker``, signed, or 0.0 with no position.

        Asks the portfolio's ``quantity_of`` when it has one, as
        :class:`~tradefloor.Portfolio` and the sandbox's read-only view
        both do, so no holding is copied. A portfolio built by hand without
        it is read through ``positions`` as before.
        """
        quantity_of = getattr(self.portfolio, "quantity_of", None)
        if quantity_of is not None:
            return quantity_of(ticker)
        held = self.portfolio.positions.get(ticker)
        return held.quantity if held else 0.0

    def __repr__(self) -> str:
        # `step` is printed as "N of M" so that anyone who prints an
        # observation while debugging a per-day guard sees immediately that it
        # counts the run rather than the day. That is where the trap is
        # usually discovered, or missed.
        return (f"Observation(day={self.day}, "
                f"step_of_day={self.step_of_day}/{self.steps_per_day}, "
                f"step={self.step} of run, n={len(self.tickers)})")


#: The public columns a day's bar is read from, in the order a row holds
#: them. ``price`` before the close is the day's last print, which is the
#: close ``Engine.bars(grain="day")`` reports, and ``volume`` before the
#: close is the shares the day has traded, the day bar's volume.
_BAR_COLUMNS = ("open", "high", "low", "price", "volume")
_BAR_FIELDS = ("open", "high", "low", "close", "volume")


class History:
    """The days a run has closed: one daily bar per name and the published
    macro, the warm-up days first.

    Handed to agents as ``obs.history`` by :func:`evaluate`,
    :func:`tradefloor.rank`, :class:`tradefloor.World` and
    :func:`tradefloor.tca.analyse`. It needs no extra package, and it is
    there whether or not the agent was given the live engine.

    A run with ``history_days=N`` runs the market for N days before day 0,
    with nobody trading, and those N days are here before the agent's first
    decision, labelled ``-N`` to ``-1``. Each scored day is added after its
    last step, labelled with its ``obs.day``, so at the open of day 3 the
    history ends at day 2. The day in progress is never here: read today's
    open, high and low from ``obs.engine.column(...)``.

    A bar is read from the public columns just before the close: the day's
    open, its high and low, its last print as the close and its volume in
    shares. The open, high, low, close and volume are the ones
    ``Engine.bars(grain="day")`` gives for a recorded day: the volume is
    the shares traded that day. The macro row is :attr:`MarketView.macro_fields
    <tradefloor.sandbox.MarketView.macro_fields>` at the same moment, the
    published figures the day traded under, so ``cycle`` and ``gdp_growth``
    are the published ones.

    A bar's high and low come from one print a tick, the tick's last trade,
    so a resting limit order the market's flow filled earlier in a tick can
    show a fill price below that day's low or above its high, and a check
    of fills against these bars will disagree with the engine on it.

    On pt-v20 the close is the day's last print, and it is not the price
    the next session starts from. The market's close comes after the bar is
    read and re-marks every name, and the next day's first step shows the
    re-marked price. On 20 names over 40 days (``Universe.random(20,
    seed=5)``, seed 3) the next day's first price differed from the bar's
    close by 14.8 bp at the median and 57.6 bp at most, and never matched
    it. On presets through pt-v19 the close re-marks nothing and the two
    are equal. A broker's daily bar closes at the official close, so on
    pt-v20 an ATR or a breakout level computed from these bars sits a
    little off one computed from a broker's.

    ```python
    def act(self, obs):
        bars = obs.history.bars("AAA", last=20)
        if len(bars) < 20:
            return {}
        if obs.price("AAA") > max(bar["high"] for bar in bars):
            return {"AAA": 100}
        return {}
    ```
    """

    __slots__ = ("_days", "_warmup")

    def __init__(self, warmup_days: int = 0) -> None:
        # One tuple per closed day: (day, tickers, opens, highs, lows,
        # closes, volumes, macro items). Tuples, so a row handed out as a
        # dict is a copy and nothing an agent does to it reaches the next.
        self._days: list[tuple] = []
        self._warmup = int(warmup_days)

    @property
    def warmup_days(self) -> int:
        """How many days ran before day 0: the run's ``history_days``."""
        return self._warmup

    def __len__(self) -> int:
        """The number of days held."""
        return len(self._days)

    def bars(self, ticker: str | None = None, *,
             last: int | None = None) -> list[dict[str, Any]]:
        """Daily bars, oldest first.

        Each row is ``{"day", "ticker", "open", "high", "low", "close",
        "volume"}``. With ``ticker`` the rows are that name's alone, one a
        day; without it every name's bar for each day, in roster order.
        ``last=n`` keeps the last ``n`` days. Empty before any day has
        closed, which is the first day of a run without ``history_days``.
        """
        days = self._last(last)
        if (ticker is not None and days
                and not any(ticker in d[1] for d in days)):
            raise ValidationError(_unknown_ticker(ticker, days[-1][1]))
        rows = []
        for day, tickers, *columns, _macro in days:
            for i, name in enumerate(tickers):
                if ticker is not None and name != ticker:
                    continue
                row: dict[str, Any] = {"day": day, "ticker": name}
                for field, column in zip(_BAR_FIELDS, columns):
                    row[field] = column[i]
                rows.append(row)
        return rows

    def macro(self, *, last: int | None = None) -> list[dict[str, Any]]:
        """The published macro figures, one row a day, oldest first.

        Each row is ``{"day": d}`` and the fields of
        :data:`tradefloor.sandbox.PUBLISHED_MACRO` as they stood before
        day ``d`` closed. ``last=n`` keeps the last ``n`` days.
        """
        return [{"day": day, **dict(items)}
                for day, *_columns, items in self._last(last)]

    def _last(self, last: int | None) -> list[tuple]:
        if last is None:
            return self._days
        from . import _checks
        last = _checks.whole_number("last", last)
        return self._days[-last:]

    def _close(self, engine: Any, day: int) -> None:
        """Add ``day``'s bars and macro, read from ``engine`` before its
        close. Called by the harnesses only."""
        columns = tuple(tuple(_f64(engine.column(field)))
                        for field in _BAR_COLUMNS)
        macro = tuple((key, value)
                      for key, value in engine.macro_fields.items()
                      if key in PUBLISHED_MACRO)
        self._days.append((int(day), tuple(engine.tickers), *columns, macro))

    def _copy(self) -> "History":
        """An independent copy, for a fork or a second agent."""
        copied = History(self._warmup)
        copied._days = list(self._days)
        return copied

    def __repr__(self) -> str:
        if not self._days:
            return f"History(no days, warmup_days={self._warmup})"
        return (f"History(days {self._days[0][0]} to {self._days[-1][0]}, "
                f"warmup_days={self._warmup})")


def _warm_up(engine: Engine, days: int, *, steps_per_day: int,
            ticks_per_step: int, start: tuple[int, int, int],
            history: History) -> None:
    """Run ``days`` untraded days on ``engine`` and add each to ``history``.

    The harness's own day: ``steps_per_day`` sessions of ``ticks_per_step``
    ticks on the advancing clock, as :func:`_run_untraded` runs a day, so
    a warm-up day is the day an idle agent would have seen. No scenario
    applies: a scenario's day 0 is the first scored day.
    """
    hour, minute, day_of_week = start
    for k in range(days):
        engine.open_market()
        for step in range(steps_per_day):
            engine.run_session(*session_clock((hour, minute, day_of_week),
                                              step, ticks_per_step),
                               ticks_per_step)
        history._close(engine, k - days)
        engine.close_market()


class Agent(Protocol):
    """The interface an agent implements.

    ``act`` returns a mapping keyed by ticker. A value is a number of
    shares for a market order, positive buys and negative sells, or a
    :class:`tradefloor.Limit` or :class:`tradefloor.Cancel`. A ticker left
    out, or a value of zero or None, does nothing, and so does returning
    None or ``{}``. A share count is anything ``float()`` reads as a finite
    number, so a numpy scalar, a ``Decimal`` or a torch scalar tensor
    trades. A string, a ``bool``, a complex number, NaN or an infinity is
    refused as an order. A return that is not a mapping at all, such as a
    list of pairs, trades nothing that step, and that includes an empty
    list, ``0`` or ``False``, which before 0.8.5 passed as no trade.
    :func:`evaluate` records both in the scorecard's ``errors``;
    :class:`tradefloor.World` raises on a bad return under its default
    ``on_refusal="raise"``, so a World run ends there. See
    :func:`tradefloor.portfolio.check_order`.

    Numbers of shares, not portfolio weights: ``{'AAA': 0.2}`` buys a fifth
    of one share. :func:`evaluate` warns when every order in a step is such a
    fraction.

    ``explain`` is optional. When present, ``explain(day)`` returns the
    factor the agent believes moved prices most that day, one of
    :data:`DRIVER_NAMES`. The harness calls it after the day's close and
    scores it against the engine's attribution for the whole day, open to
    close: for each factor it adds up the size of its push on every name's
    price, up or down alike, and the factor with the largest total is the
    right answer. ``fair_value_shift`` is never the answer, because it moves
    no price (see :data:`DRIVER_NAMES`). That is what lets the harness ask
    whether the agent was right for the right reasons, rather than only
    whether it made money.

    ``refusals`` is optional too. When present, :func:`evaluate` calls it
    after every ``act`` and writes each string it returns to the
    scorecard's ``errors``, prefixed with the step. The LLM adapters in
    :mod:`tradefloor.integrations` use it for an action they refused on its
    own while the rest of the decision traded.
    """

    def act(self, obs: Observation
            ) -> dict[str, float | Limit | Cancel] | None: ...


class Scorecard:
    """One agent's result.

    Attributes are declared rather than assigned through a ``setattr`` loop.
    The loop was shorter and made the class opaque: nothing could introspect
    it, no checker could see a field, and a mistyped key would have set
    nothing and read back ``None``.

    Read ``errors`` before the P&L. It holds every step at which the agent
    raised, returned something that is not an order mapping, or sent an
    order the market refused, and a step with an error traded nothing for
    that order. An agent that broke on step 3 of 30 and held cash from then
    on scores like an agent that chose to hold cash. The repr shows
    ``errors=N`` when there are any.

    ``equity_curve`` is net worth after each day's close, one value per
    day, the last equal to ``final_net_worth``. ``max_drawdown_pct`` is the
    largest fall from a running peak along it, starting from the cash the
    agent was given, so it is measured on closes and misses a dip that
    recovered inside a day, and it passes 100 when net worth goes below
    zero. ``ruined`` is True when net worth was at or
    below zero at any close. There is no margin call: under
    ``max_leverage=None`` a ruined agent keeps its positions and the
    scorecard reports what they did. With a leverage limit set, every
    order is refused once net worth is gone, because leverage over a net
    worth at or below zero is infinite.

    ``sharpe``, ``volatility_pct``, ``time_in_market`` and
    ``avg_gross_exposure`` are read-only properties computed from the card,
    and are not in :meth:`as_dict`. The first two read the daily returns of
    ``equity_curve``, starting from the cash the agent was given, and are
    annualised over 252 days. Sharpe subtracts no risk-free rate. Both are
    None with fewer than two days, and once net worth has been at or below
    zero; Sharpe is None too when the returns did not vary.
    ``exposure_curve`` is gross exposure as a multiple of net worth after
    each step's session (infinite once net worth is gone), and the other two
    read it: the share of steps that ended holding any position, and the
    mean multiple over the steps with a finite one.

    ``rejected`` counts the orders the market refused and the actions an
    LLM adapter refused on its own (its ``refusals()``, such as a ticker
    the roster does not have), each with its line in ``errors``.
    ``leverage_refusals`` is the part of ``rejected`` that the leverage
    limit refused. ``explanation_baseline`` is what always giving the same
    answer to ``explain`` would have scored on the same days: the share of
    scored days won by the factor that won most often.
    ``explanation_edge`` is ``explanation_accuracy`` minus that baseline,
    and the repr prints all three together. On pt-v20 a constant answer
    scores near the top: ``random_noise`` moves prices most on 294 of 300
    days over three rosters and five seeds, and ``jump`` on five, so
    answering ``random_noise`` every day scores 0.95 to 1.0. Only the edge
    says anything about the agent. An accuracy of 0.97 against a baseline
    of 0.98 is an agent that did worse than naming one factor every day.
    """

    __slots__ = ("name", "pnl", "return_pct", "trades", "turnover", "impact_bps",
                 "max_leverage", "rejected", "explanations", "explanation_accuracy",
                 "final_net_worth", "errors", "seed", "universe_fingerprint",
                 "strategy_fingerprint", "model_fingerprint", "trusted",
                 "uses_hidden_state", "tampered", "equity_curve",
                 "max_drawdown_pct", "ruined", "leverage_refusals",
                 "explanation_baseline", "partial_fills", "history_days",
                 "margin_interest", "exposure_curve")

    #: Slots :meth:`as_dict` leaves out. ``exposure_curve`` is the input of
    #: two read-only properties and is read off the fills and the prices,
    #: which a traded digest already covers.
    _NOT_IN_DICT = frozenset({"exposure_curve"})

    def __init__(
        self, *, name: str, pnl: float, return_pct: float, trades: int,
        turnover: float, impact_bps: float, max_leverage: float, rejected: int,
        explanations: list[tuple[str, str]], explanation_accuracy: float | None,
        final_net_worth: float, errors: list[str], seed: int = -1,
        universe_fingerprint: str = "", strategy_fingerprint: str = "",
        model_fingerprint: str = "", trusted: bool = False,
        uses_hidden_state: bool = False, tampered: bool = False,
        equity_curve: list[float] | None = None, max_drawdown_pct: float = 0.0,
        ruined: bool = False, leverage_refusals: int = 0,
        explanation_baseline: float | None = None,
        partial_fills: list[str] | None = None,
        history_days: int = 0,
        margin_interest: bool = True,
        exposure_curve: list[float] | None = None,
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
        #: The agent was handed the live engine (``trusted_agents=True``)
        #: rather than the read-only market view. Nothing on the card can
        #: say what it did with it, so a trusted result is not a peer of a
        #: sandboxed one.
        self.trusted = trusted
        #: The agent declared ``privileged = True`` and was handed hidden
        #: state as ``obs.hidden``: a measuring instrument like the Oracle,
        #: not a competitor.
        self.uses_hidden_state = uses_hidden_state
        #: Agent code changed the engine or its own portfolio outside the
        #: order path. ``errors`` names the step. The score is of a market
        #: the agent rewrote and ranks nothing.
        self.tampered = tampered
        #: Net worth after each day's close, in day order.
        self.equity_curve = list(equity_curve or [])
        #: The largest fall from a running peak of ``equity_curve``, which
        #: starts at the cash the agent was given, in percent.
        self.max_drawdown_pct = max_drawdown_pct
        #: Net worth was at or below zero at some close.
        self.ruined = ruined
        #: How many of ``rejected`` the leverage limit refused.
        self.leverage_refusals = leverage_refusals
        #: The accuracy a constant answer to ``explain`` would have had on
        #: the same days. None when nothing was scored.
        self.explanation_baseline = explanation_baseline
        #: One line per market order the book could not fill in full, such
        #: as "step 0: asked to buy 1,000,000,000,000 AAA; the book held
        #: 5,529,929, and the rest did not fill." Kept apart from
        #: ``errors``: the order traded, only less of it than was asked.
        self.partial_fills = list(partial_fills or [])
        #: How many untraded days ran before day 0 (``history_days``). The
        #: scored days of a run with a warm-up are later days of the
        #: seed's market, so the seed alone no longer names them.
        self.history_days = history_days
        #: Borrowing paid the policy rate. False only for a run that passed
        #: ``margin_interest=False``, whose repr then says "free-borrowing":
        #: a levered score from such a run is not comparable to one that
        #: paid for its leverage.
        self.margin_interest = bool(margin_interest)
        #: Gross exposure as a multiple of net worth after each step's
        #: session, in step order; infinite where net worth was at or below
        #: zero. Read by `time_in_market` and `avg_gross_exposure`.
        self.exposure_curve = list(exposure_curve or [])

    def _daily_returns(self) -> list[float] | None:
        """Each day's return along ``equity_curve``, from the starting
        cash, or None once net worth has been at or below zero."""
        worth = [self.final_net_worth - self.pnl, *self.equity_curve]
        if any(w <= 0 for w in worth):
            return None
        return [b / a - 1.0 for a, b in zip(worth, worth[1:])]

    @property
    def volatility_pct(self) -> float | None:
        """Annualised volatility of the daily returns, in percent: their
        sample standard deviation times the square root of 252. None with
        fewer than two days or once net worth went to zero."""
        daily = self._daily_returns()
        if daily is None or len(daily) < 2:
            return None
        return statistics.stdev(daily) * math.sqrt(252) * 100.0

    @property
    def sharpe(self) -> float | None:
        """Annualised Sharpe ratio of the daily returns: their mean over
        their sample standard deviation, times the square root of 252, with
        no risk-free rate subtracted. None with fewer than two days, once
        net worth went to zero, or when the returns did not vary (an agent
        that never traded). Five or ten days give a very noisy figure."""
        daily = self._daily_returns()
        if daily is None or len(daily) < 2:
            return None
        sd = statistics.stdev(daily)
        if sd == 0.0:
            return None
        return statistics.fmean(daily) / sd * math.sqrt(252)

    @property
    def time_in_market(self) -> float | None:
        """The share of steps that ended with any position open, from 0
        to 1. None for a card with no ``exposure_curve``."""
        if not self.exposure_curve:
            return None
        held = sum(1 for x in self.exposure_curve if x > 0.0)
        return held / len(self.exposure_curve)

    @property
    def avg_gross_exposure(self) -> float | None:
        """Mean gross exposure as a multiple of net worth, over the steps
        where net worth was above zero: 1.0 is fully invested, 2.0 is the
        default leverage limit, 0.0 never held anything. Longs and shorts
        both add. None for a card with no finite reading."""
        finite = [x for x in self.exposure_curve if math.isfinite(x)]
        return statistics.fmean(finite) if finite else None

    @property
    def explanation_edge(self) -> float | None:
        """``explanation_accuracy`` minus ``explanation_baseline``: how much
        better the agent's answers to ``explain`` scored than naming the
        most common answer every day. None when the agent has no
        ``explain`` or answered on no day. On pt-v20 the baseline is 0.95
        to 1.0, so this is the figure to compare, not the accuracy."""
        if self.explanation_accuracy is None or self.explanation_baseline is None:
            return None
        return self.explanation_accuracy - self.explanation_baseline

    def as_dict(self) -> dict[str, Any]:
        # `history_days` only when there was a warm-up, so the card of a
        # run without one is the dict, and the digest, it always was.
        # The figures computed from the card (`sharpe` and the rest) are
        # properties and stay out with their input, so a traded known
        # answer hashes what it always did.
        return {slot: getattr(self, slot) for slot in self.__slots__
                if (slot != "history_days" or self.history_days)
                and slot not in self._NOT_IN_DICT}

    def __repr__(self) -> str:
        flags = "".join(
            f", {flag}" for flag, on in (("TAMPERED", self.tampered),
                                         ("RUINED", self.ruined),
                                         ("trusted", self.trusted),
                                         ("hidden-state",
                                          self.uses_hidden_state),
                                         ("free-borrowing",
                                          not self.margin_interest)) if on)
        if self.history_days:
            flags += f", history_days={self.history_days}"
        # Counts, so an agent that failed on every step does not print like
        # one that chose to hold cash. The lines are in `errors` and
        # `partial_fills`.
        counts = "".join(
            f", {label}={len(lines)}"
            for label, lines in (("errors", self.errors),
                                 ("partial_fills", self.partial_fills))
            if lines)
        # The accuracy only beside its baseline and the difference: on
        # pt-v20 a constant answer scores 0.95 to 1.0, so a bare accuracy
        # of 0.97 reads as good when it is below what naming one factor
        # every day would have scored.
        explained = ""
        if self.explanation_edge is not None:
            explained = (f", explanation={self.explanation_accuracy:.3f} vs "
                         f"baseline {self.explanation_baseline:.3f} "
                         f"(edge {self.explanation_edge:+.3f})")
        return (
            f"Scorecard({self.name!r}, pnl={self.pnl:,.0f}, "
            f"return={self.return_pct:+.2f}%, trades={self.trades}, "
            f"impact={self.impact_bps:+.2f}bps{self._risk_text()}"
            f"{explained}{counts}{flags})"
        )

    def _risk_text(self) -> str:
        """Sharpe, volatility, time in market and gross exposure for the
        repr, or nothing for a card with no equity curve."""
        if not self.equity_curve:
            return ""
        sharpe, vol = self.sharpe, self.volatility_pct
        text = (f", sharpe={sharpe:+.2f}" if sharpe is not None
                else ", sharpe=n/a")
        if vol is not None:
            text += f", vol={vol:.1f}%"
        held, gross = self.time_in_market, self.avg_gross_exposure
        if held is not None:
            text += f", in_market={held * 100:.0f}%"
        if gross is not None:
            text += f", exposure={gross:.2f}x"
        return text


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
    """The factor that moved prices most today, of :data:`DRIVER_NAMES`.

    Summed over instruments in absolute value, because a factor that pushed two
    names in opposite directions still explains both moves. Netting them would
    report a busy factor as an idle one. Read after the close, so a jump at
    the close counts for the day it moved. ``fair_value_shift`` is not ranked:
    it moves no price, and ranking it counted every permanent shock twice.
    """
    best, best_size = None, 0.0
    for name in DRIVER_NAMES:
        size = ordered_sum(abs(x) for x in _f64(engine.attribution(name)))
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
    cash_interest: bool = False,
    trusted_agents: bool = False,
    history_days: int = 0,
    margin_interest: bool = True,
) -> dict[str, Scorecard]:
    """Run every agent against an identical market and score them.

    One market. Every agent meets the same one, so the comparison is exact
    -- but a verdict from a single seed is a measurement of
    that seed as much as of the agents. See :func:`tradefloor.rank` for the
    across-seed version, and :func:`leaderboard` for the measured size of the
    effect. ``seed`` is any integer from 0 to ``2**64 - 1``; every seed below
    ``2**32`` is the market it was when seeds were 32-bit.

    ``max_leverage`` defaults to 2x rather than to unlimited. An agent that can
    trade arbitrary size is not being tested against the market: the book makes
    large trades expensive, but with no funding limit arbitrarily large is
    always available and "trade everything" wins. Pass ``None`` deliberately if
    that is what you want to study.

    A value in ``agents`` may be a :class:`tradefloor.StrategySpec` instead of a
    built agent. The spec is built HERE, freshly, on every call, which is
    both what makes a spec-carrying result citable (the scorecard's
    ``strategy_fingerprint`` names exactly what ran) and what closes a real
    trap: agents are stateful, and a built instance reused across two
    evaluations carries the first market's history into the second with no
    visible symptom. A spec cannot, because it is not the agent; it is the
    instruction for building one.

    ``model`` selects the coefficient set every engine in the evaluation
    runs, either a preset name or a :class:`tradefloor.ModelParams`, and defaults
    to the shipped preset. One model for the whole evaluation, baseline
    included: scoring agents across different models would compare markets,
    not agents. Each scorecard records ``model_fingerprint``.

    ``cash_interest=True`` pays each agent's uninvested cash the policy rate,
    one day's worth before each close (:meth:`Portfolio.accrue`). Off by
    default: cash earns nothing, as it always has here.

    ``margin_interest`` charges borrowing the policy rate, and is on by
    default. An agent whose cash goes negative, holding more than it is
    worth under ``max_leverage``, pays a day's interest on the balance
    before each close, at the policy rate the market publishes that day.
    This changes scores and never prices: the market is the same run with or
    without it. ``margin_interest=False`` lets a levered agent borrow for
    free, as every run did before 0.8.5, and its scorecard says
    ``free-borrowing``.

    Agents are sandboxed. ``obs.engine`` is a read-only
    :class:`~tradefloor.sandbox.MarketView` and ``obs.portfolio`` a read-only
    :class:`~tradefloor.sandbox.PortfolioView`; an agent with
    ``privileged = True`` also gets ``obs.hidden`` and its card says
    ``uses_hidden_state``. ``trusted_agents=True`` hands every agent the live
    engine and portfolio instead, and every card says ``trusted``. Either
    way the engine's state hash is compared around each ``act`` and
    ``explain``, and an agent that changed anything is scored
    ``tampered=True`` with an error line naming the step. See
    :mod:`tradefloor.sandbox`.

    An agent that raises, or returns something that is not an order
    mapping, is scored rather than allowed to end the run: the step trades
    nothing and its card's ``errors`` names the step and what came back. A
    bad entry in a good mapping, an unknown ticker or a quantity of
    ``"100"``, is refused and counted in ``rejected``, and the rest of the
    mapping trades. See :class:`Agent` for what ``act`` may return. A
    :class:`tradefloor.Limit` rests in the engine's book as it does under
    :class:`tradefloor.World`, and what fills later is collected after each
    session.

    ``history_days=N`` runs the market for N days before day 0 with nobody
    trading, so an agent that needs a lookback has one at its first
    decision: ``obs.history`` holds those N days' bars and published macro
    (see :class:`History`), and adds each scored day after it closes. The
    scored days then continue that market, so on the same seed they are
    different days from a run without the warm-up, and the scorecard
    records ``history_days``. The untraded baseline runs the same warm-up.
    N is at most 2520, ten 252-day years. No scenario applies during it: a scenario's day 0 is the first scored
    day. The reference agents and :class:`tradefloor.StrategySpec`
    strategies keep their own price history and do not read
    ``obs.history``. Left at 0, the run is the one it always was.

    One exception does end the run.
    :class:`~tradefloor.integrations.common.ReplayMiss` means a recording
    has no answer for this input, usually because the seed, the roster or
    the prompt changed after it was made. It propagates with a note naming
    the step, the agent and the seed. Scored as an error, it would read as
    an agent that held cash from that step on.

    Every engine in one call, the untraded baseline's and each agent's, is
    a copy (:meth:`Engine.fork`) of one engine built once, so every agent
    trades the same market to the bit.

    The arguments are checked before any market runs. An entry of
    ``agents`` that is a class rather than an instance, a function, or
    anything else without an ``act(obs)`` method is refused then, as is a
    scenario passed by name. Three things are warned about and not
    refused, because the run is still valid: an agent whose every step
    raised or was refused (its card reads like one that chose not to
    trade), an agent whose orders in a step were all fractions of a share
    (``act`` returns shares, not portfolio weights), and a spec whose
    ``top_k`` is more than the universe has room for on each side. A market
    order the book could not fill in full is listed in the card's
    ``partial_fills``.

    Returns a scorecard per agent, keyed by name.
    """
    from .spec import StrategySpec
    seed = check_seed(seed)
    agents = _checks.agents(agents)
    if not agents:
        raise ValidationError("no agents given")
    days = _checks.whole_number("days", days)
    steps_per_day = _checks.whole_number("steps_per_day", steps_per_day)
    ticks_per_step = _checks.whole_number("ticks_per_step", ticks_per_step)
    history_days = _checks.history_days(history_days)
    cash_interest = _checks.flag("cash_interest", cash_interest)
    margin_interest = _checks.flag("margin_interest", margin_interest)
    _checks.number("cash", cash)
    if max_leverage is not None:
        _checks.number("max_leverage", max_leverage)
    macro = _checks.macro(macro)
    scenario = _checks.scenario(scenario)
    hour, minute, day_of_week = _checks.start_clock(start)
    # Every entry, before any market runs. Until 0.8.5 a class or a
    # function here ran to the end, raised on every step and scored zero.
    for name, entry in agents.items():
        _checks.agent(f"Agent {name!r}", entry)
    # The portfolio's own checks on cash and max_leverage (finite, above
    # zero), run before the untraded market rather than after it: that run
    # costs as much as one agent's, and a bad argument should not wait for it.
    Portfolio(cash=cash, max_leverage=max_leverage, cash_interest=cash_interest,
              margin_interest=margin_interest)
    results: dict[str, Scorecard] = {}

    # The baseline market: the same seed with nobody trading. Every agent's
    # impact is measured against this, so it is computed once rather than per
    # agent -- and because it is the same run each time, the comparison between
    # two agents' impact is a comparison and not two separate experiments.
    # Computed ONCE for the whole evaluation. Every agent runs the same
    # market, so a per-agent hash would be the same value hashed N times.
    fingerprint = fingerprint_of(universe)

    # Built once and copied for the baseline and for each agent. A copy of
    # a fresh engine has its state hash and runs to the same prices.
    template = Engine(seed=seed, universe=universe, macro_state=macro,
                      model=model)
    # The warm-up runs once, on the template, so the baseline and every
    # agent start day 0 from the same market and the same history.
    warmed = History(history_days)
    _warm_up(template, history_days, steps_per_day=steps_per_day,
             ticks_per_step=ticks_per_step,
             start=(hour, minute, day_of_week), history=warmed)

    baseline = _run_untraded(seed, universe, macro, days, steps_per_day,
                             ticks_per_step, hour, minute, day_of_week,
                             scenario, model, engine=template.fork(1)[0])

    for name, entry in agents.items():
        agent = entry.build() if isinstance(entry, StrategySpec) else entry
        # Built agents carry their spec (build() attaches it), so the
        # fingerprint flows whether the caller passed the spec or the agent
        # it built. A hand-written agent has none, and its card says so.
        declared = getattr(agent, "spec", None)
        strategy_fingerprint = (declared.fingerprint
                                if isinstance(declared, StrategySpec) else "")
        if isinstance(declared, StrategySpec):
            _warn_top_k(name, declared, len(universe))
        results[name] = _evaluate_one(
            name, agent, seed, universe, macro, days, steps_per_day,
            ticks_per_step, cash, max_leverage, hour, minute, day_of_week,
            baseline, scenario, fingerprint, strategy_fingerprint, model,
            cash_interest, bool(trusted_agents),
            engine=template.fork(1)[0], history=warmed._copy(),
            margin_interest=margin_interest,
        )
        _warn_if_every_step_failed(results[name], days * steps_per_day)
    return results


def _warn_top_k(name: str, spec: "StrategySpec", size: int) -> None:
    """Warn when a spec asks for more names on each side than the universe
    holds.

    The ranked agents take ``min(top_k, n // 2)`` names on each side, so on
    five names ``top_k`` of 2, 5 and 50 run the same trades under three
    different strategy fingerprints: two cards would cite different
    strategies for one set of trades. A warning and not a refusal, because
    the default ``top_k=5`` on a small universe is a common first run, and
    the trades are what they always were.
    """
    top_k = spec.as_dict().get("portfolio", {}).get("top_k")
    room = size // 2
    if top_k is None or top_k <= room:
        return
    warnings.warn(
        f"Agent {name!r}: top_k={top_k} asks for {top_k} names on each side, "
        f"but a {size}-name universe has room for {room}, so the run used "
        f"{room}. Its strategy fingerprint names top_k={top_k}. Pass "
        f"top_k={room} or a bigger universe.", stacklevel=3)


def _steps_with_errors(card: "Scorecard") -> set[int]:
    """The steps an ``errors`` line names, leaving out tamper lines and
    ``explain()`` lines, which do not cost a step its orders."""
    steps: set[int] = set()
    for line in card.errors:
        head, _, rest = line.partition(": ")
        if not head.startswith("step ") or rest.startswith("tampered:"):
            continue
        try:
            steps.add(int(head[5:]))
        except ValueError:
            continue
    return steps


def _warn_if_every_step_failed(card: "Scorecard", steps: int) -> None:
    """Warn once when an agent traded nothing and every one of its steps
    raised or had its orders refused.

    Its card reads ``pnl=0.00`` like an agent that chose to hold cash, and
    the README's first run reads only the headline figures, so the errors
    list was never seen.
    """
    if card.trades or steps < 1 or not card.errors:
        return
    if len(_steps_with_errors(card)) < steps:
        return
    first = next(line for line in card.errors
                 if line.startswith("step ")
                 and not line.partition(": ")[2].startswith("tampered:"))
    warnings.warn(
        f"Agent {card.name!r} failed on all {steps} of its steps, so its "
        f"score is empty. First error: {first.rstrip('.')}. The rest are in "
        f"scores[{card.name!r}].errors.", stacklevel=3)


def _partial_fill_lines(fills: list[dict[str, Any]]) -> list[str]:
    """One line per market order that filled only in part."""
    lines = []
    for fill in fills:
        if not fill.get("partial"):
            continue
        requested = abs(float(fill.get("requested", 0.0)))
        filled = abs(float(fill["quantity"]))
        side = "buy" if float(fill["quantity"]) > 0 else "sell"
        lines.append(
            f"step {fill.get('step', 0)}: asked to {side} {requested:,.0f} "
            f"{fill['ticker']}; the book held {filled:,.0f}, and the rest did "
            "not fill.")
    return lines


def _warn_fractional(name: str, fills: list[dict[str, Any]]) -> None:
    """Warn once when every order an agent sent in some step was a fraction
    of a share: the portfolio-weights mistake.

    ``{t: 0.2 for t in tickers}`` reads as 20% in each name and buys a
    fifth of a share of each. It trades, is refused nowhere and scores a
    P&L of a few units of currency. Nothing about what is executed
    changes; this only says so.
    """
    by_step: dict[int, list[dict[str, Any]]] = {}
    for fill in fills:
        by_step.setdefault(int(fill.get("step", 0)), []).append(fill)
    for step in sorted(by_step):
        asked = [(fill["ticker"], float(fill.get("requested",
                                                 fill["quantity"])))
                 for fill in by_step[step]]
        if not asked or any(abs(q) >= 1.0 for _, q in asked):
            continue
        shown = ", ".join(f"{q:g} of {t}" for t, q in asked[:3])
        more = ", ..." if len(asked) > 3 else ""
        ticker = asked[0][0]
        warnings.warn(
            f"Agent {name!r} asked for fractions of a share ({shown}{more}). "
            "act() returns numbers of shares, not portfolio weights. To put "
            f"20% of your money in {ticker}, send 0.2 * net_worth / "
            f"obs.price({ticker!r}) shares.", stacklevel=4)
        return


def _unknown_ticker(ticker: Any, tickers: Sequence[str]) -> str:
    """The refusal for a ticker that is not in the market, with the ticker
    meant when the difference is only case or spacing."""
    hint = ""
    if isinstance(ticker, str):
        wanted = ticker.strip().upper()
        near = next((t for t in tickers if t.upper() == wanted), None)
        if near is not None and near != ticker:
            hint = f" Did you mean {near!r}?"
    return (f"No ticker {ticker!r} in this market.{hint} obs.tickers lists "
            f"all {len(tickers)}.")


def _run_untraded(seed, universe, macro, days, steps_per_day, ticks_per_step,
                  hour, minute, day_of_week, scenario=None,
                  model=None, *, engine=None) -> list[float]:
    if engine is None:
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


def _refusals_of(agent: Any) -> list[str]:
    """The refusal lines an agent reports for the ``act()`` it just ran.

    An agent may define ``refusals()``, returning one string per order it
    refused on its own during that call. The framework adapters in
    :mod:`tradefloor.integrations` do: from decision schema 2 they refuse a
    bad action and trade the rest of the decision, so nothing is raised for
    the harness to see. An agent without the method reports none.
    """
    hook = getattr(agent, "refusals", None)
    if not callable(hook):
        return []
    return [str(line) for line in (hook() or [])]


def _is_replay_miss(exc: BaseException) -> bool:
    """Whether ``exc`` is a recording with no answer for this input.

    Imported late and only when an agent has raised: the integrations
    import the counterfactual module, which imports this one, and an
    evaluation whose agents never raise has no reason to load them.
    """
    from .integrations.common import ReplayMiss
    return isinstance(exc, ReplayMiss)


def _evaluate_one(name, agent, seed, universe, macro, days, steps_per_day,
                  ticks_per_step, cash, max_leverage, hour, minute,
                  day_of_week, baseline, scenario=None,
                  fingerprint="", strategy_fingerprint="",
                  model=None, cash_interest=False,
                  trusted=False, *, engine=None,
                  history: History | None = None,
                  margin_interest=True) -> Scorecard:
    if engine is None:
        engine = Engine(seed=seed, universe=universe, macro_state=macro,
                        model=model)
    portfolio = Portfolio(cash=cash, max_leverage=max_leverage,
                          cash_interest=cash_interest,
                          margin_interest=margin_interest)
    if history is None:
        history = History()
    tickers = engine.tickers
    adv = [inst.avg_volume for inst in universe]
    # What the agent is handed. Built once: every view reads the live
    # engine when it is called, so it is always the present.
    privileged = declares_hidden_state(agent)
    shown_engine = engine if trusted else MarketView(engine)
    shown_portfolio = portfolio if trusted else PortfolioView(portfolio, engine)
    hidden = HiddenState(engine) if privileged else None
    guard = TamperGuard(engine, (portfolio,), trusted=trusted)
    tampered = False

    trades = 0
    turnover = 0.0
    rejected = 0
    leverage_refusals = 0
    errors: list[str] = []
    peak_leverage = 0.0
    explanations: list[tuple[str, str]] = []
    equity_curve: list[float] = []
    exposure_curve: list[float] = []

    step = 0
    for day in range(days):
        # Applied before the day opens, so day zero already runs under the
        # path rather than under whatever the engine was constructed with.
        if scenario is not None:
            scenario.apply(engine, day)
            # Re-read the depth the agent is shown and the participation
            # cap is sized against, because a scenario can move it and
            # this was read once, before the loop. Only under a scenario:
            # with none, nothing in the engine writes this column, and a
            # daily re-read of every ordinary run would be a column copy
            # nobody asked for.
            adv = _f64(engine.column("avg_volume"))
        engine.open_market()
        for _ in range(steps_per_day):
            # The roster and the depth are copies, so an agent that sorts or
            # edits what it was shown edits its own copy and not the lists
            # this loop indexes by.
            obs = Observation(step, day, list(tickers), _f64(engine.prices()),
                              shown_portfolio, shown_engine,
                              adv if trusted else tuple(adv), steps_per_day,
                              hidden=hidden, history=history)
            # The within-day tick the fill lands on: agents act at the START of
            # a step, so `ticks_per_step` ticks per completed step have
            # run this day. This is what makes the fills table joinable
            # to bars and truth on (day, tick, instrument_id).
            portfolio.stamp(day, step, (step % steps_per_day) * ticks_per_step)
            orders = None
            try:
                with guard:
                    orders = agent.act(obs)
                    # Actions an LLM adapter refused on their own, while
                    # the rest of its decision trades. They never raise, so
                    # they are asked for, and each is an error line.
                    # Each is counted in `rejected` as well, as a refused
                    # order is, so `rank` does not read it as a raise.
                    for line in _refusals_of(agent):
                        errors.append(f"step {step}: {line}")
                        rejected += 1
            except Exception as exc:                      # noqa: BLE001
                if _is_replay_miss(exc):
                    # A broken recording is a broken experiment. Scored as
                    # an error it would read as an agent that held cash
                    # from this step on, and the run would publish that.
                    exc.add_note(f"tradefloor.evaluate stopped at step {step} "
                                 f"(day {day}) of agent {name!r}, seed {seed}.")
                    raise
                # An agent that throws is scored, not crashed. A harness that
                # died on one bad agent would lose every other agent's result
                # in the same run.
                errors.append(f"step {step}: {type(exc).__name__}: {exc}")
                orders = None
            if guard.tampered:
                tampered = True
                errors.append(f"step {step}: tampered: agent code changed "
                              f"or copied the market during act() "
                              f"({guard.what})")

            # What came back, checked for shape first. A list of pairs or a
            # string is a step that trades nothing and an error line, never
            # an exception out of the harness: that would lose every other
            # agent's result in the same run.
            try:
                entries = order_items(orders)
            except ValidationError as exc:
                errors.append(f"step {step}: {exc}")
                entries = []

            for ticker, value in entries:
                try:
                    order = check_order(ticker, value)
                    if order is None:
                        continue
                    if isinstance(order, Cancel):
                        portfolio.cancel(engine, ticker=ticker)
                        continue
                    if isinstance(order, Limit):
                        # As World does it: a new limit on a name replaces
                        # the one waiting there. What fills at once is a
                        # trade now, and what fills later is collected
                        # after the session below.
                        portfolio.cancel(engine, ticker=ticker)
                        report = portfolio.submit_limit(
                            engine, ticker, order.quantity, order.price)
                        if report["filled"] > 0:
                            trades += 1
                            turnover += ordered_sum(
                                f["quantity"] * f["price"]
                                for f in report["fills"])
                        continue
                    fill = portfolio.execute(engine, ticker, order)
                    trades += 1
                    turnover += abs(fill["notional"])
                except (OrderError, ValidationError) as exc:
                    # Refused trades are counted rather than raised. Being
                    # unable to size a position is information about the agent.
                    rejected += 1
                    if isinstance(exc, LeverageError):
                        leverage_refusals += 1
                    errors.append(f"step {step}: {exc}")

            # The clock advances with the step, so a day of N steps traverses
            # a trading day instead of replaying its first minutes N times.
            step_hour, step_minute, step_dow = session_clock(
                (hour, minute, day_of_week), step % steps_per_day,
                ticks_per_step)
            # `fills`, once, on the step's first tick: the minute after the
            # agent filled. Until 0.8.5 this was `order_flow`, held on every
            # one of the step's ticks, so an order was counted 65 times and
            # the agent collected its own impact. See `Engine.run_session`.
            engine.run_session(step_hour, step_minute, step_dow, ticks_per_step,
                               fills=portfolio.pending_flow())
            portfolio.clear_flow()
            # What a resting limit order filled during the session, as
            # World.run collects it. Asks the engine nothing for a portfolio
            # that never sent an order to the book.
            for taken in portfolio.sync(engine):
                trades += 1
                turnover += taken["quantity"] * taken["price"]

            leverage = portfolio.leverage(engine)
            if leverage != float("inf"):
                peak_leverage = max(peak_leverage, leverage)
            exposure_curve.append(leverage)
            step += 1

        # A day's interest on cash at the rate the day traded under, before
        # the close's macro step can move it: earned on a positive balance
        # with `cash_interest` on, charged on a negative one with
        # `margin_interest` on, the default.
        portfolio.accrue(engine)
        # The day's bar, read before the close re-marks the prices, so
        # `explain(day)` below already sees today in `obs.history`.
        history._close(engine, day)
        engine.close_market()
        # Marked after the close, which on pt-v20 re-marks every name, so
        # the last value is the final net worth below.
        equity_curve.append(portfolio.net_worth(engine))
        # Asked after the close, so the scored day is the whole day: a jump
        # at the close moves prices today, and until 0.8.5 the scorer read
        # the attribution before it, so `jump` could never be the answer.
        explain = getattr(agent, "explain", None)
        if callable(explain):
            actual = _dominant_factor(engine)
            try:
                with guard:
                    claimed = explain(day)
            except Exception as exc:                      # noqa: BLE001
                if _is_replay_miss(exc):
                    exc.add_note(f"tradefloor.evaluate stopped at day {day}'s "
                                 f"explain() of agent {name!r}, seed {seed}.")
                    raise
                errors.append(f"day {day} explain: {type(exc).__name__}: {exc}")
                claimed = None
            if guard.tampered:
                tampered = True
                errors.append(f"day {day} explain: tampered: agent code "
                              f"changed or copied the market ({guard.what})")
            if claimed is not None and actual is not None:
                explanations.append((claimed, actual))

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
    # What a constant answer scores on the same days: the share won by the
    # factor that won most often. One or two factors win most days, so
    # this is far above one in eleven, and accuracy means little without it.
    explanation_baseline = (
        Counter(actual for _, actual in explanations).most_common(1)[0][1]
        / len(explanations)
        if explanations else None
    )

    peak, drawdown = cash, 0.0
    for worth in equity_curve:
        peak = max(peak, worth)
        if peak > 0:
            drawdown = max(drawdown, (peak - worth) / peak)
    _warn_fractional(name, portfolio.fills)

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
        trusted=trusted,
        uses_hidden_state=privileged,
        tampered=tampered,
        equity_curve=equity_curve,
        max_drawdown_pct=drawdown * 100.0,
        ruined=any(worth <= 0 for worth in equity_curve),
        leverage_refusals=leverage_refusals,
        explanation_baseline=explanation_baseline,
        partial_fills=_partial_fill_lines(portfolio.fills),
        history_days=history.warmup_days,
        margin_interest=portfolio.margin_interest,
        exposure_curve=exposure_curve,
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

    total = ordered_sum(traded.values())
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
       NAMES the across-seed leader, nine times in twelve, but what it
       says that leader is worth ranges from a capture of -0.776 to +0.836
       depending only on which market it drew.

       Use this to read one market. To rank agents, use :func:`tradefloor.rank`,
       which takes the verdict across seeds and reports a paired sign test
       saying whether the ordering is established at all -- because even the
       across-seed aggregate can order two agents that a paired test cannot
       separate.

    A tampered card (``Scorecard.tampered``) is sorted after every other
    card whatever it scored, because its score is of a market the agent
    rewrote. :func:`tradefloor.rank` leaves such an agent out altogether.
    A card from an agent that read hidden state (``uses_hidden_state``, the
    Oracle for one) or that was handed the live engine (``trusted``) is
    ranked with the rest, and its repr says ``hidden-state`` or
    ``trusted``, since neither is a peer of a sandboxed agent. The repr
    also says ``TAMPERED``, ``RUINED`` and ``errors=N``, so printing the
    list shows each of these next to the score.
    """
    if by not in ("pnl", "return_pct", "impact_bps", "turnover"):
        raise ValidationError(
            "leaderboard ranks by pnl, return_pct, impact_bps or turnover; "
            f"got {by!r}.")
    # Impact is a cost, so less is better; everything else is more-is-better.
    #
    # The direction is applied by NEGATING the metric rather than by
    # `reverse=True`, because reverse would flip the name tiebreak too and
    # rank ties reverse-alphabetically. That is a real bug I shipped and a
    # test caught: two agents with identical scores came back zeta-then-alpha.
    descending = by != "impact_bps"
    sign = -1.0 if descending else 1.0
    return sorted(scores.values(),
                  key=lambda s: (bool(getattr(s, "tampered", False)),
                                 sign * getattr(s, by), s.name))
