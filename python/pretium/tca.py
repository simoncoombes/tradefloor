"""Transaction cost analysis against a market where you never traded.

Real TCA has one irreducible problem: the benchmark does not exist. You want
to know what you would have paid had you not traded, and you cannot observe it,
because your trading is part of why the prices you see happened. So the
industry estimates it — arrival price, VWAP, a fitted impact model — and every
one of those is a proxy standing in for a counterfactual nobody can run.

Here it runs. Same seed, same universe, same macro, same session; in one world
the trader executes, in the other nobody does. Every fill is then priced
against what that instrument was doing in the world where the trader never
existed.

## Two channels, and they are not the same size

Trading moves the price twice over, through mechanisms worth keeping apart:

**The book channel.** An order matches against resting liquidity with
price-time priority. A large order walks up the levels and pays worse prices
as it goes. This is emergent — nothing multiplies size by a coefficient, the
order simply consumes what is there — and it is where a big trade's cost
actually comes from.

**The information channel.** Order imbalance feeds the factor model as a
signal, moving the mispricing itself. This one PERSISTS: the book recovers as
liquidity replenishes, but a shift in `s` is a new level.

The split matters because they decay differently, and every serious execution
model is built on the distinction. Elsewhere it is fitted from data with
heroic assumptions. Here both worlds are runnable and the split is measured.

Be aware of the bounds before sizing an experiment. The information
channel's imbalance term is clamped per tick: below about 1.33x the
instrument's average minute volume a floor applies and the term is flat,
above 10x it is capped and flat again, and between them it scales — both
constants live in ``order_imbalance`` in ``rust/src/market/factors.rs``. In
practice an ``analyse`` run hits a harder bound first: the book caps the
fill at the displayed depth, and identical fills mean identical flow and
identical numbers. Measured on this build — the first name of
``Universe.random(20, seed=7)``, sim seed 2026, one six-step day — requests
of 20x and 100x the average minute volume both fill the same 483 shares and
land exactly the same 260.02 bps of end-of-run impact. The response is
linear in what actually fills, not in what you ask for.

## What the number means

Implementation shortfall, signed so positive is always a cost:

    shortfall = Σ quantity × (fill price − counterfactual price at that step)

A buyer who paid more than the untraded world's price has a positive
shortfall. So does a seller who received less. Reporting a signed difference
and leaving the reader to work out which direction hurt is how sign errors get
into published numbers.

## This is an execution measure, not a strategy P&L, and a round trip shows why

Measured on this build — the first instrument of ``Universe.random(20,
seed=7)``, sim seed 2026, one six-step day, trading 1% of ADV: buying 97
shares and holding costs **+16.71 bps**. Buying the same 97 shares and
selling them three steps later comes to **-13.57 bps** — a gain, and not a
quirk of the seed: the round trip ends negative on seven of the eight seeds
the test suite measures.

Nothing is wrong. The entry pushed the price up, part of that impact persisted,
and the exit sold into it. The agent really did transact at prices better than
the untraded world offered, on that leg.

What it means is that shortfall answers "what did each execution cost against a
market where I never traded", which is the execution desk's question. It does
not answer "did this strategy make money" — for that, read `pnl` from
:func:`pretium.evaluate`, which marks the portfolio to the market the agent
actually created. A strategy that round-trips can show a negative shortfall and
still lose, and the two numbers are not in conflict because they are answers to
different questions.

Use :meth:`Execution.by_step` when the split matters: it shows the entry paying
and the exit recouping, rather than one netted figure that hides both.
"""

from __future__ import annotations

import struct
from typing import Any, Sequence

from ._core import Engine, Instrument, Macro, OrderError, ValidationError
from .harness import Observation, session_clock
from .portfolio import Portfolio
from .universe_util import as_universe, fingerprint_of


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


class Execution:
    """What one trader's activity cost, measured against not having traded."""

    __slots__ = ("tickers", "fills", "baseline_path", "actual_path",
                 "baseline_final", "actual_final", "seed", "portfolio",
                 "steps", "universe_fingerprint")

    def __init__(self, *, tickers, fills, baseline_path, actual_path, seed,
                 portfolio, steps, universe_fingerprint=""):
        self.tickers = list(tickers)
        self.fills = list(fills)
        # One cross-section per decision step, in both worlds. The path rather
        # than the endpoint, because a fill has to be priced against what the
        # untraded market was doing AT THAT MOMENT -- comparing it to a closing
        # price would charge the trader for the market's own drift.
        self.baseline_path = baseline_path
        self.actual_path = actual_path
        self.baseline_final = baseline_path[-1] if baseline_path else []
        self.actual_final = actual_path[-1] if actual_path else []
        self.seed = seed
        self.portfolio = portfolio
        self.steps = steps
        # Which market this cost was paid in. A shortfall in basis points is
        # meaningless without the book it was paid against, and the book is a
        # property of the roster.
        self.universe_fingerprint = universe_fingerprint

    # -- shortfall --------------------------------------------------------

    def shortfall(self, ticker: str | None = None) -> float:
        """Implementation shortfall in currency. Positive is a cost."""
        total = 0.0
        for fill in self.fills:
            if ticker is not None and fill["ticker"] != ticker:
                continue
            reference = self._reference(fill)
            if reference is None:
                continue
            # quantity is signed: positive bought, negative sold. Multiplying
            # by the price difference gives a cost in both directions without
            # a branch -- a seller who received less has a negative quantity
            # and a negative difference.
            total += fill["quantity"] * (fill["price"] - reference)
        return total

    def shortfall_bps(self, ticker: str | None = None) -> float:
        """Shortfall as basis points of the notional traded.

        Currency alone is not comparable between a $10m programme and a
        $100k one, and bps is the unit every execution desk already reads.
        """
        notional = sum(
            abs(f["notional"]) for f in self.fills
            if ticker is None or f["ticker"] == ticker
        )
        if notional == 0:
            return 0.0
        return self.shortfall(ticker) / notional * 10_000

    def _reference(self, fill) -> float | None:
        """The untraded world's price for this instrument at this fill's step.

        The fill happened BEFORE the step's session ran, so it is priced
        against the cross-section the trader saw when deciding — index `step`,
        not `step + 1`. Using the post-session price would credit the trader
        with the market's own move over the interval they traded in.
        """
        step = fill.get("step")
        if step is None or step >= len(self.baseline_path):
            return None
        try:
            i = self.tickers.index(fill["ticker"])
        except ValueError:
            return None
        return self.baseline_path[step][i]

    def by_step(self) -> list[tuple[int, float]]:
        """Shortfall per decision step, in currency.

        A single netted figure hides the structure that matters: entering
        pays, and unwinding into your own impact recoups. Both are visible
        here and neither is visible in the total.
        """
        buckets: dict[int, float] = {}
        for fill in self.fills:
            reference = self._reference(fill)
            if reference is None:
                continue
            step = fill["step"]
            buckets[step] = buckets.get(step, 0.0) +                 fill["quantity"] * (fill["price"] - reference)
        return sorted(buckets.items())

    def by_ticker(self) -> dict[str, float]:
        """Shortfall per instrument, in currency."""
        return {
            ticker: self.shortfall(ticker)
            for ticker in sorted({f["ticker"] for f in self.fills})
        }

    def partial_fills(self) -> list[dict]:
        """Fills that could not be completed at the size requested.

        Worth reading before believing a low shortfall. An order that only
        half filled only paid half the impact, and the untraded half cost
        nothing precisely because it never happened -- measured on this
        build (half the ADV of the first name of ``Universe.random(20,
        seed=7)``, sim seed 2026), a request for 4,856 shares filled 483,
        because that was the whole displayed depth. The cheapest execution
        is the one that did not occur, which is not a result anyone should
        quote.
        """
        return [f for f in self.fills if f.get("partial")]

    # -- impact decomposition ---------------------------------------------

    def impact_bps(self, ticker: str) -> float:
        """Total end-of-run price impact, in basis points."""
        i = self.tickers.index(ticker)
        base = self.baseline_final[i]
        return (self.actual_final[i] - base) / base * 10_000 if base else float("nan")

    def moved(self) -> dict[str, float]:
        """Every instrument whose final price differs, in bps.

        Includes names the trader never touched. Those should be absent, and
        this exists to check rather than to assume: order flow consumes no RNG
        draws, so the untraded names follow byte-identical paths. Anything
        here that was not traded means something leaked between the worlds.
        """
        out = {}
        for i, ticker in enumerate(self.tickers):
            if self.actual_final[i] != self.baseline_final[i]:
                out[ticker] = self.impact_bps(ticker)
        return out

    def untouched_moved(self) -> list[str]:
        traded = {f["ticker"] for f in self.fills}
        return sorted(t for t in self.moved() if t not in traded)

    def as_dict(self) -> dict[str, Any]:
        traded = sorted({f["ticker"] for f in self.fills})
        return {
            "seed": self.seed,
            "universe_fingerprint": self.universe_fingerprint,
            "steps": self.steps,
            "fills": len(self.fills),
            "traded": traded,
            "notional": sum(abs(f["notional"]) for f in self.fills),
            "shortfall": self.shortfall(),
            "shortfall_bps": self.shortfall_bps(),
            "impact_bps": {t: self.impact_bps(t) for t in traded},
            "untouched_moved": self.untouched_moved(),
        }

    def __repr__(self) -> str:
        return (
            f"Execution(seed={self.seed}, fills={len(self.fills)}, "
            f"shortfall={self.shortfall():,.2f}, "
            f"{self.shortfall_bps():+.2f}bps)"
        )


def analyse(
    agent,
    *,
    seed: int,
    universe: Sequence[Instrument],
    macro: Macro | None = None,
    days: int = 1,
    steps_per_day: int = 6,
    ticks_per_step: int = 65,
    cash: float = 1_000_000.0,
    max_leverage: float | None = 2.0,
    start: tuple[int, int, int] = (9, 30, 3),
    scenario: Any = None,
) -> Execution:
    """Run an agent, then run the same market without it, and price the gap.

    The two runs differ in exactly one thing: whether the trader exists. Same
    seed, same universe, same macro, same session length, same tick schedule,
    and the same ``scenario`` if one is given. Anything else that differed
    between them would surface as impact and be wrong.

    Passing a ``scenario`` asks a question real TCA cannot: what did this
    execution cost DURING a shock, against the same shock without it. Both
    worlds run the identical macro path, so the difference is still the
    trading and not the regime.

    Returns an :class:`Execution`. Its ``shortfall`` is the measurement real
    TCA cannot make, because the benchmark it compares against is a market
    that never happened.
    """
    universe = as_universe(universe)
    if days < 1 or steps_per_day < 1 or ticks_per_step < 1:
        raise ValidationError("days, steps_per_day and ticks_per_step must be >= 1")

    hour, minute, day_of_week = start
    tickers = None
    adv = [instrument.avg_volume for instrument in universe]

    def fresh():
        return Engine(seed=seed, universe=universe, macro_state=macro)

    # -- world A: the trader exists ---------------------------------------
    engine = fresh()
    tickers = engine.tickers
    portfolio = Portfolio(cash=cash, max_leverage=max_leverage)
    actual_path: list[list[float]] = []
    step = 0
    for day in range(days):
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        for _ in range(steps_per_day):
            prices = _f64(engine.prices())
            actual_path.append(prices)
            # The within-day tick the fill lands on: agents act at the START of
            # a step, so `ticks_per_step` ticks per completed step have
            # run this day. This is what makes the fills table joinable
            # to bars and truth on (day, tick, instrument_id).
            portfolio.stamp(day, step, (step % steps_per_day) * ticks_per_step)
            obs = Observation(step, day, tickers, prices, portfolio, engine,
                              adv, steps_per_day)
            for ticker, quantity in (agent.act(obs) or {}).items():
                if not quantity:
                    continue
                try:
                    portfolio.execute(engine, ticker, quantity)
                except (OrderError, ValidationError):
                    # A refused trade is not an execution and has no cost. It
                    # is the agent's problem, not the analysis's.
                    pass
            engine.run_session(*session_clock((hour, minute, day_of_week),
                                              step % steps_per_day,
                                              ticks_per_step),
                               ticks_per_step,
                               order_flow=portfolio.pending_flow())
            portfolio.clear_flow()
            step += 1
        engine.close_market()
    actual_path.append(_f64(engine.prices()))

    # -- world B: nobody trades -------------------------------------------
    #
    # Run second, so an agent that raises does so before this work is spent.
    quiet = fresh()
    baseline_path: list[list[float]] = []
    for day in range(days):
        if scenario is not None:
            scenario.apply(quiet, day)
        quiet.open_market()
        for step in range(steps_per_day):
            baseline_path.append(_f64(quiet.prices()))
            # Identical clock to world A. The two worlds must differ only by
            # the agent's orders.
            quiet.run_session(*session_clock((hour, minute, day_of_week),
                                             step, ticks_per_step),
                              ticks_per_step)
        quiet.close_market()
    baseline_path.append(_f64(quiet.prices()))

    return Execution(
        tickers=tickers,
        fills=portfolio.fills,
        baseline_path=baseline_path,
        actual_path=actual_path,
        seed=seed,
        portfolio=portfolio,
        steps=step,
        universe_fingerprint=fingerprint_of(universe),
    )
