"""Run one agent in two worlds that differ by one variable, and measure the gap.

The library already had both halves of a controlled experiment and no way to
join them. :func:`tradefloor.branch` forks a running engine, and
:func:`tradefloor.evaluate` runs an agent against a market -- but `evaluate`
runs a whole evaluation start to finish, so there is no moment inside it at
which a caller can stop, fork, change one thing and continue. Reaching a state
and then asking what happens NEXT needed a run loop you can pause.

That is what a :class:`World` is: a market, an agent, a portfolio and a macro
path advancing together, one day at a time, with the pause built in.

```python
world = World(seed=7, universe=roster, agent=MyAgent(), pins={...})
world.run(days=20)                       # the shared history

control, shock = world.fork("control", "+200bps")
shock.intervene(federal_funds_rate=0.06)  # the one changed variable

control.run(days=20)
shock.run(days=20)

print(compare(control, shock).render())
```

## What makes this an experiment rather than two runs

Two runs from one seed are not a controlled comparison the moment an agent
trades, because the agent's own orders move the market it is trading. Change
anything and the whole trajectory moves with it, and there is no way to say
how much of the difference was the intervention.

A fork removes that. Both arms leave the SAME state -- the same prices, the
same order book, the same generator position, the same portfolio, the same
agent -- so everything that differs afterwards descends from the one field
that was changed. :func:`agree` is the proof, run before the intervention and
reported rather than assumed, because "identical" is a claim the reader should
not have to take on trust.

## Fork on a day boundary, always

:class:`World` runs whole days and forks between them, and does so for a
reason. `checkpoint.py` records a defect where a snapshot taken BETWEEN
two sessions of the same day lost the day's accumulators, re-opened the day on
its next session, and priced differently from the parent it was a copy of.
Every test forked on a day boundary, so nothing caught it. This module cannot
reach that state: :meth:`World.run` takes whole days, and :meth:`World.fork`
refuses an open market.

## The agent is the subject, not the apparatus

Everything here takes the agent as a parameter and touches nothing about it
except :meth:`act`. Swapping a deterministic policy for an LLM-driven one, or
for an adapter around somebody else's framework, changes no line of the
market, the checkpoint, the fork, the intervention, the execution or the
comparison. The experiment belongs to Tradefloor; the agent is what is being
measured.

Two optional hooks, neither required:

- ``decision()`` returns whatever the agent wants recorded about the reasoning
  behind its last :meth:`act`, as JSON-able data. It is what makes "the first
  step at which the agent DECIDED differently" answerable separately from "the
  first step at which it TRADED differently", which is a real distinction: a
  policy can change its target and still send no order that step.
- ``fork()`` returns an independent copy of the agent. Without it a fork falls
  back to :func:`copy.deepcopy`, which is right for a policy holding plain
  Python state and wrong for one holding a socket, a session or a file handle.

## What this deliberately does not do

It does not score. :class:`~tradefloor.Scorecard` and :func:`tradefloor.rank`
already answer "which agent is better", across many seeds and with a paired
test, and that asks something different from "what did this one change do".
A counterfactual is one seed by construction -- that is the point of it -- so
a single arm's return here is a measurement of this market as much as of the
agent, and :class:`Comparison` reports behaviour before it reports P&L for
exactly that reason.
"""

from __future__ import annotations

import copy
import struct
from typing import Any, Sequence

from ._core import (Engine, Instrument, Macro, ModelParams, OrderError,
                    ValidationError)
from .checkpoint import Checkpoint, branch
from .harness import Observation, session_clock
from .manifest import RunManifest, market_digest
from .portfolio import Portfolio
from .interventions import Intervention
from .scenario import Scenario
from .universe_util import fingerprint_of

#: Macro fields reported in a trace row and in the fork agreement. Not every
#: field the engine carries -- these are the ones a macro experiment is about,
#: and a row is meant to be readable.
MACRO_FIELDS = ("federal_funds_rate", "corporate_bond_yield", "vix",
                "inflation_rate", "cycle")


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def _macro(engine: Engine) -> dict[str, Any]:
    state = engine.macro_state
    return {field: getattr(state, field) for field in MACRO_FIELDS}


class World:
    """A market, an agent trading it, and the macro path they run under.

    Advanced a day at a time by :meth:`run`, forked by :meth:`fork`, and
    changed -- in one arm only -- by :meth:`intervene`.

    ``pins`` are the macro fields held constant from day zero, and they stay
    the day-ZERO levels for the life of the world: an intervention does not
    rewrite them, it is recorded beside them. That is what makes
    :meth:`scenario` reconstructible -- the constant levels, then one
    :meth:`Scenario.step` per intervened field on the day it happened. Written
    the other way round first, with ``intervene`` updating ``pins`` in place,
    the derived scenario put the post-intervention rate on day zero and
    described a world where the shock had always been true.

    ``max_leverage`` defaults to 2x, matching :func:`tradefloor.evaluate`. An
    agent with no funding limit is not being tested against the market: large
    trades cost more through the book, but arbitrarily large is always
    available and "trade everything" wins.
    """

    __slots__ = ("label", "seed", "universe", "macro", "model", "cash",
                 "max_leverage", "steps_per_day", "ticks_per_step", "start",
                 "engine", "portfolio", "agent", "trace", "pins",
                 "interventions", "applied", "rejected", "fork_step",
                 "_day", "_step", "_adv", "_ran")

    def __init__(
        self,
        *,
        seed: int,
        universe: Sequence[Instrument],
        agent: Any,
        pins: dict[str, Any] | None = None,
        macro: Macro | None = None,
        cash: float = 1_000_000.0,
        max_leverage: float | None = 2.0,
        steps_per_day: int = 6,
        ticks_per_step: int = 65,
        start: tuple[int, int, int] = (9, 30, 3),
        model: str | ModelParams | None = None,
        label: str = "",
    ) -> None:
        if steps_per_day < 1 or ticks_per_step < 1:
            raise ValidationError(
                "steps_per_day and ticks_per_step must be >= 1")
        self.label = label
        self.seed = int(seed)
        self.universe = list(universe)
        self.macro = macro
        self.model = model
        self.cash = float(cash)
        self.max_leverage = max_leverage
        self.steps_per_day = int(steps_per_day)
        self.ticks_per_step = int(ticks_per_step)
        self.start = start
        self.pins = dict(pins or {})
        self.interventions: list[dict[str, Any]] = []
        # Interventions from a scenario handed to `apply`, already rebased
        # onto this world's own day numbering. Separate from `interventions`
        # above, which is this module's own one-field-at-a-time record.
        self.applied: list[Intervention] = []
        self._ran: Scenario | None = None
        self.agent = agent
        self.engine = Engine(seed=self.seed, universe=self.universe,
                             macro_state=macro, model=model)
        self.portfolio = Portfolio(cash=self.cash, max_leverage=max_leverage)
        self.trace: list[dict[str, Any]] = []
        self.rejected: list[str] = []
        # The step this world was forked at, or None for a root. It is
        # what lets a comparison quote the window the intervention acted
        # on rather than the whole run: both arms carry the same shared
        # history, so counting it into both inflates every level in both
        # columns and buries the difference between them.
        self.fork_step: int | None = None
        self._day = 0
        self._step = 0
        self._adv = [instrument.avg_volume for instrument in self.universe]

    # -- identity ---------------------------------------------------------

    @property
    def day(self) -> int:
        """The next day this world will run. Also the count of days run."""
        return self._day

    @property
    def step(self) -> int:
        """The next decision step, counting the whole run."""
        return self._step

    @property
    def universe_fingerprint(self) -> str:
        return fingerprint_of(self.universe)

    @property
    def order_log(self) -> list[dict[str, Any]]:
        """Every input that reached this world's engine, from day zero.

        The engine's own, on a root and on a fork alike. This carried a
        separately inherited copy of the parent's log until `Engine.fork`
        started copying the engine rather than restoring a list of fields into
        a fresh one: a branched engine's log used to begin empty, so an arm
        had to carry its parent's history itself or its checkpoint and its
        manifest would describe a run that began at the fork. Both layers
        fixing the same defect meant the shared history appeared twice, and a
        manifest built from it replayed a market that ran the first twenty
        days over again.
        """
        return [dict(entry) for entry in self.engine.order_log]

    def digest(self) -> str:
        """sha256 over the engine's market state.

        :func:`tradefloor.manifest.market_digest`, which covers the continuous
        internals tomorrow's prices depend on and not only the prices a cent
        grid has already rounded. Two worlds with equal digests are on the
        same market to the bit.

        Comparable between worlds at the same fork depth, the pairing an
        experiment puts side by side, and NOT between a fork and its parent. The
        digest folds in ``draws_consumed``, and a branched engine's counter
        restarts at zero while every column is carried, so a fork and its
        parent report different digests for the same market.
        ``test_a_branch_does_not_carry_the_draw_counter_or_the_log`` pins it.
        """
        return market_digest(self.engine)

    # -- running ----------------------------------------------------------

    def run(self, days: int = 1) -> "World":
        """Advance ``days`` whole days, agent trading, and record every step.

        Whole days only. A world stopped mid-day could not be forked safely,
        and offering a half-day advance would mean offering the fork that goes
        with it. See the module docstring.

        The loop is the one :func:`tradefloor.evaluate` runs, deliberately:
        the same clock, the same order of observe-execute-settle, and the
        same feedback of the agent's own flow into the next session. A
        counterfactual run that stepped the market differently from the
        library's own evaluation would be measuring a second engine.
        """
        if days < 0:
            raise ValidationError(f"days cannot be negative, got {days}")
        scenario = self.scenario()
        # Kept, so `firings` can report what the interventions actually saw.
        # A fresh object per call, so the trail is this call's.
        self._ran = scenario
        hour, minute, day_of_week = self.start
        tickers = self.engine.tickers

        for _ in range(days):
            day = self._day
            # Pinned before the market opens, so day zero already runs under
            # the path rather than under whatever the engine was built with.
            # `pin_macro` is recorded in the order log, which puts the
            # intervention inside the checkpoint and the manifest rather
            # than only in this object's memory.
            scenario.apply(self.engine, day)
            macro = _macro(self.engine)
            self.engine.open_market()

            for _ in range(self.steps_per_day):
                prices = _f64(self.engine.prices())
                obs = Observation(self._step, day, tickers, prices,
                                  self.portfolio, self.engine, self._adv,
                                  self.steps_per_day)
                self.portfolio.stamp(
                    day, self._step,
                    (self._step % self.steps_per_day) * self.ticks_per_step)

                orders = self.agent.act(obs) or {}
                decision = self._decision()
                fills, refused = self._execute(orders, tickers)

                self.engine.run_session(
                    *session_clock((hour, minute, day_of_week),
                                   self._step % self.steps_per_day,
                                   self.ticks_per_step),
                    self.ticks_per_step,
                    order_flow=self.portfolio.pending_flow())
                self.portfolio.clear_flow()

                self.trace.append({
                    "step": self._step,
                    "day": day,
                    "step_of_day": self._step % self.steps_per_day,
                    "macro": macro,
                    "decision": decision,
                    "orders": {t: q for t, q in orders.items() if q},
                    "fills": fills,
                    "refused": refused,
                    # End of step: what this step's session produced. The
                    # orders above are what opened it. Both in one row, so a
                    # divergence can be attributed to a decision or to the
                    # market that answered it.
                    "prices": _f64(self.engine.prices()),
                    "cash": self.portfolio.cash,
                    "net_worth": self.portfolio.net_worth(self.engine),
                    "exposure": self.portfolio.leverage(self.engine),
                    "positions": {t: p.quantity
                                  for t, p in sorted(
                                      self.portfolio.positions.items())},
                })
                self._step += 1

            self.engine.close_market()
            self._day += 1
        return self

    def _decision(self) -> Any:
        """Whatever the agent chose to publish about its last decision."""
        hook = getattr(self.agent, "decision", None)
        return hook() if callable(hook) else None

    def _execute(self, orders: dict[str, float],
                 tickers: Sequence[str]) -> tuple[list[dict], list[str]]:
        """Send the agent's orders, recording each fill against its arrival mid.

        The mid is read BEFORE the sweep, which makes the recorded
        slippage a cost rather than a tautology: after the sweep the book has
        already moved to where the trade left it.

        Refused trades are recorded and counted, not raised. Being unable to
        size a position is information about the agent, and information the
        comparison wants -- an arm that hit its leverage cap and an arm
        that did not are behaving differently.
        """
        fills: list[dict] = []
        refused: list[str] = []
        for ticker, quantity in orders.items():
            if not quantity:
                continue
            book = self.engine.book(ticker)
            mid = book.mid_price
            try:
                fill = self.portfolio.execute(self.engine, ticker, quantity)
            except (OrderError, ValidationError) as exc:
                refused.append(f"{ticker}: {exc}")
                self.rejected.append(f"step {self._step} {ticker}: {exc}")
                continue
            fills.append({
                "ticker": fill["ticker"],
                "quantity": fill["quantity"],
                "price": fill["price"],
                "worst_price": fill["worst_price"],
                "notional": fill["notional"],
                "partial": fill["partial"],
                "mid": mid,
            })
        return fills, refused

    # -- the macro path ---------------------------------------------------

    def scenario(self) -> Scenario:
        """This world's macro path, as a :class:`~tradefloor.Scenario`.

        Built fresh from the constant pins, one step per :meth:`intervene`,
        and the rebased interventions of anything handed to :meth:`apply` --
        so it is always what this world ran rather than a description written
        alongside it. Serialise it into a manifest and the reader has the
        experiment as data: the field, the day, the value before, the value
        after, and for a scenario the shocks kept apart from the assumptions.
        """
        scenario = Scenario(label=self.label)
        if self.pins:
            scenario.hold(**self.pins)
        current = dict(self.pins)
        for entry in self.interventions:
            for field, after in sorted(entry["fields"].items()):
                scenario.step(field, before=current.get(field, after),
                              after=after, at=entry["day"])
                current[field] = after
        # Anything handed to `apply`, already rebased onto this world's days.
        # One object carries both halves so that `run` applies one thing and
        # a manifest records one thing, rather than the run and the document
        # disagreeing about what the experiment was.
        for item in self.applied:
            scenario.intervene(item)
        return scenario

    def apply(self, scenario: Scenario) -> "World":
        """Drive this world from a scenario document, from today on.

        ```python
        stress.apply(tf.Scenario.load("liquidity_crisis"))
        ```

        The complement of :meth:`intervene`, and worth having beside it
        rather than instead of it. `intervene` changes one macro field to one
        absolute value and reads perfectly for the canonical experiment: one
        variable, named on the line that changes it. A scenario reaches what
        that cannot -- relative operations, whose value depends on where the
        endogenous chain has arrived; `market.liquidity`, which is not a
        macro field at all and is the only lever that touches execution;
        windows that end; and the split between what a scenario asserts
        happened and what it merely assumes followed.

        # Timing, and why this rebases

        A scenario counts `at` from where the run loop starts applying it.
        A `World` counts days from the beginning of its own history, which a
        forked arm shares with its sibling -- so an arm forked on day 20 is
        on day 20, not day 0. Handing the same file to both would otherwise
        mean two different experiments.

        So each intervention is rebased by the day it is applied on: a file
        that says `at: 50` fires on this world's day 20 + 50. What the
        manifest records is the rebased form, which is the one that says
        which days things actually fired.

        The scenario is not stored by reference. Rebasing produces new
        :class:`~tradefloor.Intervention` objects, so applying one document
        to two arms on different days gives each the timing it asked for and
        neither can perturb the other.
        """
        if not isinstance(scenario, Scenario):
            raise ValidationError(
                f"apply() takes a Scenario, got {type(scenario).__name__}. "
                f"Load one with tf.Scenario.load('liquidity_crisis'), read a "
                f"file with tf.Scenario.from_yaml(path), or build one with "
                f"tf.Scenario(name=...).shock(...).")
        if not scenario.interventions:
            raise ValidationError(
                f"{scenario.name or 'this scenario'} declares no "
                f"interventions, so applying it would leave this world "
                f"indistinguishable from one that ran without it. A scenario "
                f"that carries only a macro path belongs in `pins` at "
                f"construction, which drives the whole run rather than "
                f"starting here.")
        self._refuse_open_market("apply")
        for item in scenario.interventions:
            self.applied.append(Intervention(
                item.target, operation=item.operation, value=item.value,
                at=item.at + self._day, duration=item.duration,
                shape=item.shape, role=item.role))
        return self

    @property
    def firings(self) -> tuple:
        """Every intervention that fired in the last :meth:`run`, with the
        values it saw. Empty until something has run."""
        return self._ran.log if self._ran is not None else ()

    def intervene(self, **fields: Any) -> "World":
        """Change one or more macro fields, from this world's next day on.

        What the module exists for, and deliberately narrow: it writes
        macro fields and nothing else. An intervention that could also reach
        into the portfolio, the book or the agent would not be a controlled
        variable, it would be a second experiment.

        Recorded three times over, because a counterfactual whose intervention
        is not written down is an anecdote: here on the world, in the
        :meth:`scenario` it derives, and -- once the next day opens and
        ``pin_macro`` runs -- in the engine's own order log, which travels
        inside a :class:`~tradefloor.Checkpoint` and a
        :class:`~tradefloor.RunManifest`.
        """
        if not fields:
            raise ValidationError(
                "intervene() with no fields changes nothing. Name the field "
                "and the value it takes.")
        current = dict(self.pins)
        for entry in self.interventions:
            current.update(entry["fields"])
        self.interventions.append({
            "day": self._day, "step": self._step,
            "fields": dict(fields),
            "before": {field: current.get(field) for field in fields},
        })
        return self

    # -- forking ----------------------------------------------------------

    def checkpoint(self, label: str = "") -> Checkpoint:
        """This world's history as data, replayable without this process.

        The log-based fork rather than the snapshot one. It costs what the run
        cost, and what it buys is that the point the experiment starts from is
        a few kilobytes of JSON somebody else can resume, rather than a memory
        image of this interpreter.
        """
        self._refuse_open_market("checkpoint")
        default = ModelParams.from_preset().fingerprint
        return Checkpoint(
            seed=self.seed, universe=self.universe, log=self.order_log,
            macro=self.macro, label=label or self.label,
            model=(dict(self.engine.model_params)
                   if self.engine.model_fingerprint != default else None))

    def fork(self, *labels: str) -> list["World"]:
        """Independent continuations of this world, one per label.

        Independent in the strong sense: separate engines, separate
        portfolios, separate agents. Driving one cannot perturb another, which
        is what makes the arms a controlled comparison rather than two runs
        that started similarly.

        The agent is copied by its own ``fork()`` if it has one and by
        :func:`copy.deepcopy` otherwise. A policy holding plain Python state
        copies correctly either way; one holding a client, a socket or a file
        handle needs to say what a copy of it means, and the hook is where it
        says so.
        """
        if len(labels) < 1:
            raise ValidationError("fork needs at least one label")
        self._refuse_open_market("fork")
        if len(set(labels)) != len(labels):
            raise ValidationError(
                f"fork labels must be distinct, got {list(labels)}. A "
                "comparison between two arms with one name is unreadable.")

        engines = branch(self.engine, len(labels), universe=self.universe,
                         seed=self.seed, macro=self.macro)
        out: list[World] = []
        for label, engine in zip(labels, engines):
            child = World(seed=self.seed, universe=self.universe,
                          agent=self._fork_agent(), pins=self.pins,
                          macro=self.macro, cash=self.cash,
                          max_leverage=self.max_leverage,
                          steps_per_day=self.steps_per_day,
                          ticks_per_step=self.ticks_per_step,
                          start=self.start, model=self.model, label=label)
            child.engine = engine
            child.portfolio = copy.deepcopy(self.portfolio)
            child.trace = copy.deepcopy(self.trace)
            child.rejected = list(self.rejected)
            child.interventions = copy.deepcopy(self.interventions)
            # Interventions are immutable value objects, so the list is
            # copied and its contents shared safely. Each arm may then
            # `apply` a scenario of its own on top.
            child.applied = list(self.applied)
            child._day = self._day
            child._step = self._step
            child.fork_step = self._step
            out.append(child)
        return out

    def _fork_agent(self) -> Any:
        hook = getattr(self.agent, "fork", None)
        return hook() if callable(hook) else copy.deepcopy(self.agent)

    def _refuse_open_market(self, what: str) -> None:
        """Refuse to fork or checkpoint a world whose day is half-finished.

        :meth:`run` cannot leave one -- it takes whole days and closes each
        one -- so this guards against a caller who reached into ``.engine``
        and drove it directly. The hazard it closes is documented in
        `checkpoint.py`: an engine forked between two sessions of the same day
        carries per-day accumulators the copy has to carry with it, and a copy
        that misses one restores a market that looks right and prices
        differently tomorrow. Refusing is cheap; finding that later is not.
        """
        if self.engine.state_snapshot()["market_open"]:
            raise ValidationError(
                f"cannot {what} a world with the market open. Days are the "
                "safe boundary: mid-day state includes the day's own "
                "accumulators, and a fork taken there restores a market that "
                "looks correct and diverges from its parent tomorrow. Close "
                "the day first.")

    # -- reporting --------------------------------------------------------

    def manifest(self, *, strategy: Any = None, label: str = "") -> RunManifest:
        """This world's run as one shareable, self-verifying document.

        Written from a REPLAY of the full log, not from the live engine.
        A forked arm's engine holds only its own post-fork log, so a manifest
        taken straight off it records a run that begins at the fork: it
        reproduces cleanly, into a market nobody ran. Measured before this
        was fixed, on a four-day fork of a four-day history, ``reproduce()``
        rebuilt a different market and said so.

        The replay is checked against the live engine before the manifest is
        built, so a fork that did not carry its state faithfully fails here
        rather than shipping a document that disagrees with the run it came
        from.
        """
        engine = self.replay()
        if engine.prices() != self.engine.prices():
            raise ValidationError(
                "replaying this world's log did not rebuild this world's "
                "market, so a manifest of it would not reproduce. The log is "
                "the reproduction mechanism, and something reached the engine "
                "without going through it.")
        return RunManifest.of(engine, seed=self.seed,
                              universe=self.universe, macro=self.macro,
                              scenario=(self.scenario()
                                        if self.pins or self.applied
                                        else None),
                              strategy=strategy,
                              label=label or self.label)

    def replay(self) -> Engine:
        """A fresh engine rebuilt from this world's whole log.

        The canonical form of the run: an engine anybody with the log can
        reconstruct, carrying its own draw count and its own log, which a
        branched engine does not.
        """
        from .replay import replay as _replay

        return _replay(self.order_log, seed=self.seed,
                       universe=self.universe, macro=self.macro,
                       model=self.model)

    def net_worth(self) -> float:
        return self.portfolio.net_worth(self.engine)

    def summary(self, *, since: int | None = None) -> dict[str, Any]:
        """The numbers a comparison quotes for one arm.

        Behaviour first, P&L last, in the order the module argues they should
        be read. Every value is computed from the trace or the portfolio;
        nothing here is stated for presentation.

        ``since`` is the step the activity counts start from, defaulting to
        this world's own :attr:`fork_step` and to zero for a root. It matters:
        both arms of a fork carry the same shared history, so a turnover
        figure that counts it is mostly prologue in both columns and the
        difference between them is buried. The END state -- value, cash,
        exposure, holdings -- is always the end state, and ``pnl`` is always
        measured from the starting capital, because neither is a windowed
        quantity. ``pnl_since`` is the windowed one, and for a forked arm it
        is the number the experiment is actually about.
        """
        start = self.fork_step if since is None else since
        start = 0 if start is None else max(0, min(start, len(self.trace)))
        window = self.trace[start:]
        fills = [f for row in window for f in row["fills"]]
        turnover = sum(abs(f["notional"]) for f in fills)
        cost = _execution_cost(fills)

        base = self.trace[start - 1]["net_worth"] if start > 0 else self.cash
        peak, drawdown = base, 0.0
        for row in window:
            peak = max(peak, row["net_worth"])
            if peak > 0:
                drawdown = max(drawdown, (peak - row["net_worth"]) / peak)

        return {
            "label": self.label,
            "days": self._day,
            "steps": self._step,
            "measured_from_step": start,
            "rebalances": sum(1 for row in window if row["orders"]),
            "orders": sum(len(row["orders"]) for row in window),
            "trades": len(fills),
            "refused": sum(len(row["refused"]) for row in window),
            "partial_fills": sum(1 for f in fills if f["partial"]),
            "turnover": turnover,
            "execution_cost": cost,
            "execution_cost_bps": (cost / turnover * 10_000
                                   if turnover else 0.0),
            "exposure": self.trace[-1]["exposure"] if self.trace else 0.0,
            "cash": self.portfolio.cash,
            "positions": (dict(self.trace[-1]["positions"])
                          if self.trace else {}),
            "final_net_worth": self.net_worth(),
            "value_at_start": base,
            "pnl": self.net_worth() - self.cash,
            "return_pct": (self.net_worth() - self.cash) / self.cash * 100.0,
            "pnl_since": self.net_worth() - base,
            "return_since_pct": ((self.net_worth() - base) / base * 100.0
                                 if base else 0.0),
            "max_drawdown_pct": drawdown * 100.0,
            "market_digest": self.digest(),
            "model_fingerprint": self.engine.model_fingerprint,
            "interventions": copy.deepcopy(self.interventions),
        }

    def __repr__(self) -> str:
        return (f"World({self.label!r}, seed={self.seed}, day={self._day}, "
                f"step={self._step}, {len(self.universe)} instruments)")


def _execution_cost(fills: Sequence[dict]) -> float:
    """What the fills cost against the mid they were sent into.

    Signed so positive is a cost to the trader: a buyer who paid above the
    mid and a seller who received below it both add. Fills with no mid --
    a book with one side empty -- are skipped rather than priced against a
    guess, which is the same rule :class:`tradefloor.Execution` applies to a
    fill it cannot reference.
    """
    total = 0.0
    for fill in fills:
        mid = fill.get("mid")
        if mid is None:
            continue
        total += fill["quantity"] * (fill["price"] - mid)
    return total


# ---------------------------------------------------------------------------
# Before the intervention: are these really the same world?
# ---------------------------------------------------------------------------

class Agreement:
    """What two worlds share, checked rather than asserted.

    Every check is something Tradefloor can genuinely compare. Nothing is
    listed that the library cannot read back off the two objects, because a
    verification table with a decorative row in it is worse than no table.
    """

    __slots__ = ("checks",)

    def __init__(self, checks: list[tuple[str, bool, str]]) -> None:
        self.checks = checks

    @property
    def identical(self) -> bool:
        return all(ok for _, ok, _ in self.checks)

    @property
    def differences(self) -> list[str]:
        return [name for name, ok, _ in self.checks if not ok]

    def as_dict(self) -> dict[str, Any]:
        return {"identical": self.identical,
                "checks": {name: {"identical": ok, "value": detail}
                           for name, ok, detail in self.checks}}

    def render(self, width: int = 22) -> str:
        lines = []
        for name, ok, detail in self.checks:
            mark = "identical" if ok else "DIFFERENT"
            lines.append(f"  {name:<{width}} {mark:<10} {detail}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"Agreement(identical={self.identical}, "
                f"{len(self.checks)} checks)")


def _bits(value: Any) -> Any:
    """A value's identity for comparison, with floats taken bitwise.

    ``==`` is the wrong operator on engine state and gives a FALSE
    DIFFERENCE, not a false match. ``Engine.state_snapshot()`` returns the
    generator position as f64s carrying u64 bit patterns, and four of the
    twenty-one are patterns that happen to be NaN -- which never compares
    equal to itself. So two snapshots of the same engine come back unequal
    under ``==``, and the obvious way to verify a fork reports that the
    experiment has no control when it has one. Measured: the raw dicts differ,
    the bit patterns are identical.

    Comparing the bytes also removes the -0.0 == 0.0 case, which is the same
    class of mistake pointing the other way -- two states that are genuinely
    different comparing equal.
    """
    if isinstance(value, float):
        return struct.pack("<d", value)
    if isinstance(value, dict):
        return {k: _bits(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bits(v) for v in value]
    return value


def agree(a: World, b: World) -> Agreement:
    """Compare two worlds field by field, and say what matched.

    Run this immediately after a fork and before an intervention. If it does
    not come back identical the experiment has no control, and every number
    downstream of it is describing two different markets.

    Nine checks, every one read back off the two objects. The engine state
    snapshot subsumes several of the others on its own -- it carries every
    column, the generator position, the per-day accumulators, the macro chain
    and the central bank -- but they are listed beside it rather than folded
    into it, because a reader checking that a portfolio survived a fork should
    not have to know that a dict of eighteen columns implies it.

    What is NOT here, and why. ``draws_consumed`` and ``order_log`` are both
    zero and empty on a freshly branched engine -- :func:`tradefloor.branch`
    builds new engines and restores state into them, and neither the counter
    nor the log is part of that state. Comparing them across two branches
    would pass every time, on nothing. The generator POSITION is a real check
    and it is here, inside the snapshot's ``rng``.
    """
    prices_a, prices_b = a.engine.prices(), b.engine.prices()
    snap_a, snap_b = a.engine.state_snapshot(), b.engine.state_snapshot()
    bits_a, bits_b = _bits(snap_a), _bits(snap_b)
    book_a, book_b = _books(a.engine), _books(b.engine)
    port_a, port_b = _portfolio_state(a.portfolio), _portfolio_state(b.portfolio)
    agent_a, agent_b = _agent_state(a.agent), _agent_state(b.agent)

    checks = [
        ("market columns", bits_a["columns"] == bits_b["columns"],
         f"{len(snap_a['columns'])} columns x {len(a.engine.tickers)}"),
        ("prices", prices_a == prices_b,
         "  ".join(f"{p:.2f}" for p in _f64(prices_a))),
        ("order book", book_a == book_b,
         f"{sum(len(v) for v in book_a.values())} levels"),
        ("generator state", bits_a["rng"] == bits_b["rng"],
         f"{len(snap_a['rng'])} words"),
        ("macro chain", (bits_a["economy"] == bits_b["economy"]
                         and bits_a["central_bank"] == bits_b["central_bank"]),
         "  ".join(f"{k}={v}" for k, v in _macro(a.engine).items()
                   if k in ("federal_funds_rate", "corporate_bond_yield"))),
        ("whole engine state", bits_a == bits_b,
         f"{len(snap_a)} fields, day {snap_a['day_count']}"),
        ("portfolio", port_a == port_b,
         f"${port_a['cash']:,.0f} cash, "
         f"{len(port_a['positions'])} positions"),
        ("agent state", _bits(agent_a) == _bits(agent_b),
         "no state() hook" if agent_a is None
         else f"{len(agent_a)} fields"),
        ("shared history", a.trace == b.trace, f"{len(a.trace)} steps"),
    ]
    return Agreement(checks)


def _books(engine: Engine) -> dict[str, list]:
    out = {}
    for ticker in engine.tickers:
        book = engine.book(ticker)
        out[ticker] = [
            (side, level.price, level.quantity, level.orders)
            for side in ("buy", "sell")
            for level in book.price_levels(side, 32)
        ]
    return out


def _portfolio_state(portfolio: Portfolio) -> dict[str, Any]:
    return {
        "cash": portfolio.cash,
        "positions": {t: (p.quantity, p.avg_cost, p.realised)
                      for t, p in sorted(portfolio.positions.items())},
        "fills": len(portfolio.fills),
    }


def _agent_state(agent: Any) -> Any:
    hook = getattr(agent, "state", None)
    return hook() if callable(hook) else None


# ---------------------------------------------------------------------------
# After: where and how far the two worlds came apart
# ---------------------------------------------------------------------------

class Divergence:
    """The first step at which each part of the experiment came apart.

    Four separate answers, and they are usually four different steps. The
    order they arrive in is the causal chain the experiment exists to show:
    the macro changed, the agent's target changed, its orders changed, the
    market it traded changed, and the portfolio followed.

    ``None`` means that part never diverged, which is a finding rather than a
    gap: an intervention that moved prices and never changed a decision says
    the agent is not macro-aware, and the table should be able to say so.
    """

    __slots__ = ("intervention_step", "intervention_day", "macro", "decision",
                 "orders", "prices", "portfolio", "steps_per_day")

    def __init__(self, *, intervention_step, intervention_day, macro,
                 decision, orders, prices, portfolio, steps_per_day) -> None:
        self.intervention_step = intervention_step
        self.intervention_day = intervention_day
        self.macro = macro
        self.decision = decision
        self.orders = orders
        self.prices = prices
        self.portfolio = portfolio
        self.steps_per_day = steps_per_day

    def as_dict(self) -> dict[str, Any]:
        return {slot: getattr(self, slot) for slot in self.__slots__}

    def render(self) -> str:
        def where(step: int | None) -> str:
            if step is None:
                return "never"
            return f"step {step:<5} (day {step // self.steps_per_day})"

        return "\n".join([
            f"  {'intervention applied':<26} {where(self.intervention_step)}",
            f"  {'macro state differs':<26} {where(self.macro)}",
            f"  {'agent decision differs':<26} {where(self.decision)}",
            f"  {'first different order':<26} {where(self.orders)}",
            f"  {'prices differ':<26} {where(self.prices)}",
            f"  {'portfolio paths differ':<26} {where(self.portfolio)}",
        ])

    def __repr__(self) -> str:
        return (f"Divergence(intervention={self.intervention_step}, "
                f"decision={self.decision}, orders={self.orders}, "
                f"prices={self.prices})")


class Comparison:
    """Two arms of one experiment, side by side."""

    __slots__ = ("control", "treatment", "divergence", "agreement")

    def __init__(self, *, control: dict, treatment: dict,
                 divergence: Divergence, agreement: Agreement | None) -> None:
        self.control = control
        self.treatment = treatment
        self.divergence = divergence
        self.agreement = agreement

    def as_dict(self) -> dict[str, Any]:
        return {
            "control": self.control,
            "treatment": self.treatment,
            "divergence": self.divergence.as_dict(),
            "fork_agreement": (None if self.agreement is None
                               else self.agreement.as_dict()),
        }

    #: (row label, key, format). Behaviour first, then execution, then P&L,
    #: because the experiment is about what the agent DID and a reader who
    #: reads top-down should meet that before the money.
    ROWS = (
        ("--- agent behaviour ---", None, None),
        ("final gross exposure", "exposure", "{:.2f}x"),
        ("steps it traded on", "rebalances", "{:,.0f}"),
        ("orders sent", "orders", "{:,.0f}"),
        ("turnover", "turnover", "${:,.0f}"),
        ("--- execution ---", None, None),
        ("trades filled", "trades", "{:,.0f}"),
        ("partial fills", "partial_fills", "{:,.0f}"),
        ("refused trades", "refused", "{:,.0f}"),
        ("cost against arrival", "execution_cost", "${:,.0f}"),
        ("the same, in bps", "execution_cost_bps", "{:+.2f} bps"),
        ("--- portfolio ---", None, None),
        ("cash", "cash", "${:,.0f}"),
        ("final value", "final_net_worth", "${:,.0f}"),
        ("P&L since the fork", "pnl_since", "${:+,.0f}"),
        ("return since the fork", "return_since_pct", "{:+.2f}%"),
        ("max drawdown since", "max_drawdown_pct", "{:.2f}%"),
        ("P&L since inception", "pnl", "${:+,.0f}"),
        ("return since inception", "return_pct", "{:+.2f}%"),
    )

    def render(self, width: int = 24) -> str:
        left = self.control.get("label", "control")
        right = self.treatment.get("label", "treatment")
        lines = [f"  {'':<{width}} {left:>16} {right:>16}",
                 "  " + "-" * (width + 34)]
        for label, key, fmt in self.ROWS:
            if key is None:
                lines.append(f"  {label}")
                continue
            a, b = self.control[key], self.treatment[key]
            lines.append(f"  {label:<{width}} {fmt.format(a):>16} "
                         f"{fmt.format(b):>16}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"Comparison({self.control.get('label')!r} vs "
                f"{self.treatment.get('label')!r}, "
                f"divergence at {self.divergence.decision})")


def compare(control: World, treatment: World,
            *, agreement: Agreement | None = None) -> Comparison:
    """Put two forked worlds side by side and find where they came apart.

    ``agreement`` is the :class:`Agreement` taken at the fork, carried through
    so the comparison document says on its face that the arms started
    identical. Optional, because a comparison is still computable without it
    -- but a published one without it is asking to be believed rather than
    checked.
    """
    if control.steps_per_day != treatment.steps_per_day:
        raise ValidationError(
            f"arms ran at {control.steps_per_day} and "
            f"{treatment.steps_per_day} steps per day, so their traces do not "
            "line up step for step and no divergence step is meaningful.")

    a, b = control.trace, treatment.trace
    # When the arm was driven by `intervene`, the first entry carries both
    # the day and the step it happened on. When it was driven by `apply`, the
    # scenario knows the DAY and this module knows how many steps a day is --
    # without which the comparison reports "intervention: none" beside four
    # divergence steps it cannot attribute to anything.
    intervention = (treatment.interventions[0] if treatment.interventions
                    else None)
    if intervention is None and treatment.applied:
        earliest = min(item.at for item in treatment.applied)
        intervention = {"day": earliest,
                        "step": earliest * treatment.steps_per_day}

    def first(field: str) -> int | None:
        for row_a, row_b in zip(a, b):
            if row_a[field] != row_b[field]:
                return int(row_a["step"])
        return None

    return Comparison(
        control=control.summary(),
        treatment=treatment.summary(since=control.fork_step),
        agreement=agreement,
        divergence=Divergence(
            intervention_step=None if intervention is None
            else intervention["step"],
            intervention_day=None if intervention is None
            else intervention["day"],
            macro=first("macro"),
            decision=first("decision"),
            orders=first("orders"),
            prices=first("prices"),
            portfolio=first("net_worth"),
            steps_per_day=control.steps_per_day,
        ),
    )
