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

## Several agents in one world

A world takes ``agents={"a": agent_a, "b": agent_b}`` in place of ``agent=``,
and then holds one :class:`~tradefloor.Portfolio` per label against one
engine, which `portfolio.py` was written to allow. Within a step every agent
sees the same prices and the same book, each over its own portfolio; they are
asked in label order, they execute in label order against the shared book, so
a later agent's fills are priced into liquidity an earlier one has already
consumed; every portfolio's pending flow is merged per ticker and reaches the
market as the one ``order_flow`` argument of the one session. Agents see each
other's impact and never each other's orders, and :meth:`Scenario.apply` runs
once a day for the whole cohort.

Label order is sorted order, so the same labels give the same market whatever
order the mapping was built in. A dict literal's own order would make the
market a property of how the caller typed it.

The single-agent form is a one-element cohort under its old names.
:attr:`World.agent` and :attr:`World.portfolio` read that one element and
raise on a cohort; :attr:`World.agents` and :attr:`World.portfolios` are the
per-label collections. A single-agent trace row is the row it always was. A
cohort row carries the shared fields and an ``agents`` map of the per-agent
ones, and :func:`agree` grows one row per label rather than one row for a
collection nobody can read back.

:meth:`World.without` is the removal :mod:`tradefloor.externality` measures:
a fork in which one agent sends no orders from the fork day on, its positions
left where they were and still marked to that arm's market.

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
import difflib
import json
import re
import statistics
import struct
from collections import Counter
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

#: The label the single-agent form holds its one agent under. Empty, because
#: a world built with `agent=` was never given a label for it and inventing
#: one would put a name in a trace row that the caller never wrote.
SOLO = ""


def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def _macro(engine: Engine) -> dict[str, Any]:
    state = engine.macro_state
    return {field: getattr(state, field) for field in MACRO_FIELDS}


_REFUSAL_TYPES: tuple[type[BaseException], type[BaseException]] | None = None


def _refusal_types() -> tuple[type[BaseException], type[BaseException]]:
    """`(DecisionError, ReplayMiss)`, resolved late.

    `integrations.common` imports this module for `MACRO_FIELDS`, so the
    import cannot sit at the top of the file. It is resolved once, when a
    world is built with `on_refusal="skip"`, rather than on every step.

    Two types because the skip policy applies to one and not the other.
    See :meth:`World._ask`.
    """
    global _REFUSAL_TYPES
    if _REFUSAL_TYPES is None:
        from .integrations.common import DecisionError, ReplayMiss

        _REFUSAL_TYPES = (DecisionError, ReplayMiss)
    return _REFUSAL_TYPES


def _cohort(agent: Any, agents: dict[str, Any] | None
            ) -> tuple[bool, dict[str, Any]]:
    """The agents a world was built with, as one mapping, in label order.

    Sorted rather than in the order the mapping was written. Label order is
    the order agents are asked in and the order they execute in, so it is a
    property of the market; taking it from a dict literal would make the
    market a property of how the caller typed the call, and two callers
    naming the same cohort would get two markets.
    """
    if (agent is None) == (agents is None):
        raise ValidationError(
            "a World takes agent= or agents=, and exactly one of them. "
            "agent=MyAgent() is one trader with one portfolio; "
            "agents={'a': A(), 'b': B()} is a cohort in one market, each "
            "with its own portfolio and its own leverage limit.")
    if agents is None:
        return True, {SOLO: agent}
    if not isinstance(agents, dict):
        raise ValidationError(
            f"agents= takes a mapping of label to agent, got "
            f"{type(agents).__name__}. The label names the agent in the "
            f"trace, in agree() and in an externality matrix.")
    if not agents:
        raise ValidationError(
            "agents={} builds a world with nobody in it. Name at least one "
            "agent, or use agent= for the single-agent form.")
    for key in agents:
        if not isinstance(key, str) or not key:
            raise ValidationError(
                f"agent labels are non-empty strings, got {key!r}. The "
                f"empty label is the single-agent form's own, so a cohort "
                f"cannot take it.")
    return False, {key: agents[key] for key in sorted(agents)}


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

    ``on_refusal`` decides what an agent that cannot produce a decision
    costs. ``"raise"`` is the default and ends the run, which is what this
    class has always done. ``"skip"`` records the refusal, trades nothing
    that step, and carries on; see :meth:`_ask`. Either way the count is in
    the trace and in :meth:`summary` under ``unusable_responses``, kept
    apart from the market-side ``refused`` so a comparison cannot add them.

    ``agents`` is the cohort form, ``{label: agent}``, and exactly one of it
    and ``agent`` is given. Each label gets its own portfolio against this
    one engine, on the terms the module docstring sets out. ``cash`` and
    ``max_leverage`` are per agent, so a three-agent cohort starts with
    three times the capital of a one-agent world and each of the three is
    capped on its own book.
    """

    __slots__ = ("label", "seed", "universe", "macro", "model", "cash",
                 "max_leverage", "steps_per_day", "ticks_per_step", "start",
                 "engine", "_portfolios", "_agents", "_single", "_frozen",
                 "trace", "pins",
                 "interventions", "applied", "rejected", "fork_step",
                 "on_refusal", "_day", "_step", "_adv", "_ran")

    def __init__(
        self,
        *,
        seed: int,
        universe: Sequence[Instrument],
        agent: Any = None,
        agents: dict[str, Any] | None = None,
        pins: dict[str, Any] | None = None,
        macro: Macro | None = None,
        cash: float = 1_000_000.0,
        max_leverage: float | None = 2.0,
        steps_per_day: int = 6,
        ticks_per_step: int = 65,
        start: tuple[int, int, int] = (9, 30, 3),
        model: str | ModelParams | None = None,
        label: str = "",
        on_refusal: str = "raise",
    ) -> None:
        if steps_per_day < 1 or ticks_per_step < 1:
            raise ValidationError(
                "steps_per_day and ticks_per_step must be >= 1")
        if on_refusal not in ("raise", "skip"):
            raise ValidationError(
                f"on_refusal must be 'raise' or 'skip', got {on_refusal!r}")
        self._single, self._agents = _cohort(agent, agents)
        self._frozen: frozenset[str] = frozenset()
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
        self.on_refusal = on_refusal
        if on_refusal == "skip":
            # Resolved here rather than on the step it first matters, so a
            # missing integrations layer is a construction error and not a
            # surprise forty paid calls into a live run.
            _refusal_types()
        self.pins = dict(pins or {})
        self.interventions: list[dict[str, Any]] = []
        # Interventions from a scenario handed to `apply`, already rebased
        # onto this world's own day numbering. Separate from `interventions`
        # above, which is this module's own one-field-at-a-time record.
        self.applied: list[Intervention] = []
        self._ran: Scenario | None = None
        self.engine = Engine(seed=self.seed, universe=self.universe,
                             macro_state=macro, model=model)
        # One book per label, against the one engine above. `cash` and
        # `max_leverage` are per agent: a cohort is several traders in one
        # market, and pooling their capital would make each one's limit a
        # function of how many others happened to be in the room.
        self._portfolios: dict[str, Portfolio] = {
            key: Portfolio(cash=self.cash, max_leverage=max_leverage)
            for key in self._agents}
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
        #: The depth the agent is shown and the participation cap is sized
        #: against. Seeded from the universe and RE-READ from the engine at
        #: the top of every day, because a scenario can move it and this
        #: used to be read once and never again. What that cost:
        #: `liquidity_crisis` takes quoted depth to 40%, and the agent went
        #: on seeing the pre-crisis figure and went on being allowed the
        #: pre-crisis order size -- in a book with 40% of the ladder. The
        #: clip that exists to keep an order realistic was sized against a
        #: market that no longer existed.
        self._adv = [instrument.avg_volume for instrument in self.universe]

    # -- who is in this world ---------------------------------------------

    @property
    def is_cohort(self) -> bool:
        """True when this world was built with ``agents=``."""
        return not self._single

    @property
    def agents(self) -> dict[str, Any]:
        """Every agent here, keyed by label, in the order they are asked in.

        A world built with ``agent=`` returns that one agent under the empty
        label, because the single-agent form is a one-element cohort. The
        mapping is a copy and the agents in it are the live ones.
        """
        return dict(self._agents)

    @property
    def portfolios(self) -> dict[str, Portfolio]:
        """Every portfolio here, keyed by the label of the agent it belongs
        to. A copy of the mapping, holding the live portfolios."""
        return dict(self._portfolios)

    @property
    def frozen(self) -> tuple[str, ...]:
        """The labels that send no orders, from :meth:`without`.

        A frozen agent is asked nothing and executes nothing. It keeps the
        positions it held when it was frozen and they go on being marked to
        this world's market, so its net worth still moves.
        """
        return tuple(label for label in self._agents
                     if label in self._frozen)

    @property
    def agent(self) -> Any:
        """The one agent, on a world built with ``agent=``."""
        return self._agents[self._solo("agent")]

    @agent.setter
    def agent(self, value: Any) -> None:
        self._agents[self._solo("agent")] = value

    @property
    def portfolio(self) -> Portfolio:
        """The one portfolio, on a world built with ``agent=``."""
        return self._portfolios[self._solo("portfolio")]

    @portfolio.setter
    def portfolio(self, value: Portfolio) -> None:
        self._portfolios[self._solo("portfolio")] = value

    def _solo(self, field: str) -> str:
        """The single-agent label, or a message naming the cohort's labels."""
        if self._single:
            return SOLO
        plural = {"agent": "agents", "portfolio": "portfolios"}[field]
        first = next(iter(self._agents))
        raise ValidationError(
            f"this world holds {len(self._agents)} agents "
            f"({', '.join(self._agents)}), so .{field} names none of them. "
            f"Read .{plural}[label], as in "
            f"world.{plural}[{first!r}].")

    def _label_for(self, agent: str | None, what: str) -> str:
        """The label a per-agent call is about, or why the call is ambiguous.

        `summary` and `compare` each report one agent's numbers. On a cohort
        the caller says which, because a table headed with one agent's P&L
        and filled with another's is the failure this refuses to have.
        """
        if agent is None:
            if self._single:
                return SOLO
            first = next(iter(self._agents))
            raise ValidationError(
                f"{what}() reports one agent's numbers and this world holds "
                f"{len(self._agents)} ({', '.join(self._agents)}). Name the "
                f"one it is about: {what}(agent={first!r}).")
        if self._single:
            raise ValidationError(
                f"{what}(agent={agent!r}) names a label and this world holds "
                f"one agent built with agent=, which carries no label. Drop "
                f"agent=, or build the world with agents={{{agent!r}: ...}}.")
        if agent not in self._agents:
            raise ValidationError(
                f"no agent is labelled {agent!r} here; this world holds "
                f"{', '.join(self._agents)}.")
        return agent

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
            # Re-read the depth AFTER the scenario has fired. `avg_volume`
            # is the column the market maker quotes off, `market.liquidity`
            # scales it, and nothing else in the engine writes it -- so
            # once a day, here, is both sufficient and the earliest point
            # at which today's value is known.
            self._adv = _f64(self.engine.column("avg_volume"))
            macro = _macro(self.engine)
            self.engine.open_market()

            for _ in range(self.steps_per_day):
                prices = _f64(self.engine.prices())
                tick = ((self._step % self.steps_per_day)
                        * self.ticks_per_step)
                # Every agent is shown the same cross-section and the same
                # book, each over its own portfolio, and all of them are
                # asked before any of them executes. That is what makes a
                # cohort simultaneous within a step: they see each other's
                # impact from the sessions already run and never each
                # other's orders from this one.
                observed: dict[str, Observation] = {}
                for label, portfolio in self._portfolios.items():
                    observed[label] = Observation(
                        self._step, day, tickers, prices, portfolio,
                        self.engine, self._adv, self.steps_per_day)
                    portfolio.stamp(day, self._step, tick)

                asked = {label: self._ask_agent(label, obs)
                         for label, obs in observed.items()}
                # Execution in label order, against the one book, so a later
                # agent pays the levels an earlier one has already taken.
                done = {label: self._execute(asked[label][0], tickers, label)
                        for label in self._agents}

                self.engine.run_session(
                    *session_clock((hour, minute, day_of_week),
                                   self._step % self.steps_per_day,
                                   self.ticks_per_step),
                    self.ticks_per_step,
                    order_flow=self._merged_flow())
                for portfolio in self._portfolios.values():
                    portfolio.clear_flow()

                self.trace.append(self._row(day, macro, asked, done))
                self._step += 1

            self.engine.close_market()
            self._day += 1
        return self

    def _row(self, day: int, macro: dict[str, Any], asked: dict, done: dict
             ) -> dict[str, Any]:
        """One trace row, in whichever of the two shapes this world has.

        A single-agent row carries the per-agent fields at the top level, in
        the order and under the names it has always used, because those rows
        are pinned. A cohort row carries the shared fields and an `agents`
        map keyed by label, since there is no single decision, order set or
        net worth for a step in which three agents traded.
        """
        # End of step: what this step's session produced. The orders below
        # are what opened it. Both in one row, so a divergence can be
        # attributed to a decision or to the market that answered it.
        prices = _f64(self.engine.prices())
        row: dict[str, Any] = {
            "step": self._step,
            "day": day,
            "step_of_day": self._step % self.steps_per_day,
            "macro": macro,
        }
        if not self._single:
            row["prices"] = prices
            row["agents"] = {label: self._fields(label, asked, done)
                             for label in self._agents}
            return row
        fields = self._fields(SOLO, asked, done)
        for name in ("decision", "orders", "fills", "refused", "unusable"):
            row[name] = fields[name]
        row["prices"] = prices
        for name in ("cash", "net_worth", "exposure", "positions"):
            row[name] = fields[name]
        return row

    def _fields(self, label: str, asked: dict, done: dict) -> dict[str, Any]:
        """One agent's half of a trace row."""
        orders, unusable, decision = asked[label]
        fills, refused = done[label]
        portfolio = self._portfolios[label]
        return {
            "decision": decision,
            "orders": {t: q for t, q in orders.items() if q},
            "fills": fills,
            "refused": refused,
            # Market-side above, agent-side here, and they are two
            # different failures: `refused` is an order this market would
            # not take, `unusable` is output that was never an order. One
            # column covering both would read an agent that cannot format
            # an answer as an illiquid market.
            "unusable": unusable,
            "cash": portfolio.cash,
            "net_worth": portfolio.net_worth(self.engine),
            "exposure": portfolio.leverage(self.engine),
            "positions": {t: p.quantity
                          for t, p in sorted(portfolio.positions.items())},
        }

    def _merged_flow(self) -> dict[str, tuple[float, float]]:
        """Every portfolio's pending flow, summed per ticker.

        One `order_flow` argument reaches the session, so a cohort's
        footprint is what the market sees rather than one agent's. The sum
        runs in label order, which fixes the order the floats are added in.

        A one-portfolio world passes its own mapping straight through, so
        the bytes the engine is handed are the bytes it was handed before
        this method existed.
        """
        if len(self._portfolios) == 1:
            return next(iter(self._portfolios.values())).pending_flow()
        merged: dict[str, tuple[float, float]] = {}
        for label in self._agents:
            for ticker, (buy, sell) in (
                    self._portfolios[label].pending_flow().items()):
                have = merged.get(ticker)
                merged[ticker] = ((buy, sell) if have is None
                                  else (have[0] + buy, have[1] + sell))
        return merged

    def _ask_agent(self, label: str, obs: "Observation"
                   ) -> tuple[dict[str, float], str | None, Any]:
        """One agent's orders, its refusal if it gave one, and its decision.

        A frozen agent is not called at all. `without` freezes it to remove
        it from the market, and calling `act` and discarding the answer
        would still spend whatever the call costs and still advance
        whatever state the agent keeps.
        """
        if label in self._frozen:
            return {}, None, None
        agent = self._agents[label]
        orders, unusable = self._ask(agent, obs)
        # No decision on a refused step. The adapter's last decision is
        # still the one BEFORE this step, and reading it here would put a
        # decision the agent did not take into the row that records it not
        # taking one -- and `compare` finds the divergence step by
        # comparing exactly this field.
        return orders, unusable, (None if unusable else self._decision(agent))

    def _ask(self, agent: Any,
             obs: "Observation") -> tuple[dict[str, float], str | None]:
        """The agent's orders for this step, and its refusal if it gave one.

        Under ``on_refusal="raise"`` -- the default, and what this module
        has always done -- an agent that cannot produce a decision ends the
        run. Under ``"skip"`` the step trades nothing, the refusal is
        recorded, and the run continues.

        The case for ``"skip"``: an agent that returns unusable output is
        an agent behaving badly, and behaving badly is a measurement. A
        malformed response measured at 1 in 35 on a live 60-decision run
        ended it on call 36 and took 35 recorded interactions, 20 days of
        shared history and both arms of a fork with it. A run long enough
        to be interesting is a run long enough to hit that.

        The case for ``"raise"`` staying the default: a run that quietly
        skipped every decision would report a flat agent rather than a
        broken one, and nobody asked for it.

        :class:`~tradefloor.integrations.common.DecisionError` only, which
        covers both stages a decision can fail at -- output that does not
        parse, and a well-formed order in a symbol this market does not
        list. A :class:`FrameworkError` is not caught: the call never
        completed, the agent produced nothing to judge, and charging it a
        step would score a dropped connection as bad behaviour.

        :class:`~tradefloor.integrations.common.ReplayMiss` is not caught
        either, and it is the exception that matters most. A recording with
        no answer for this input is a broken EXPERIMENT, not a badly
        behaved agent: skipping it turns a transcript that covers nothing
        into an agent that refused everything, and the run then completes
        and publishes that. Measured, before this exemption: two arms
        replayed against a transcript covering neither, reported twenty
        refusals each, and produced an empty series.
        """
        if self.on_refusal == "raise":
            return agent.act(obs) or {}, None
        refusal, miss = _refusal_types()
        try:
            return agent.act(obs) or {}, None
        except miss:
            raise
        except refusal as exc:
            return {}, f"{type(exc).__name__}: {exc}"

    def _decision(self, agent: Any) -> Any:
        """Whatever the agent chose to publish about its last decision."""
        hook = getattr(agent, "decision", None)
        return hook() if callable(hook) else None

    def _execute(self, orders: dict[str, float], tickers: Sequence[str],
                 label: str = SOLO) -> tuple[list[dict], list[str]]:
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
        portfolio = self._portfolios[label]
        # The world-level list names the agent on a cohort and reads as it
        # always did on a single-agent world, where there is one agent for
        # a label to point at.
        where = f"step {self._step}" if self._single \
            else f"step {self._step} {label}"
        for ticker, quantity in orders.items():
            if not quantity:
                continue
            book = self.engine.book(ticker)
            mid = book.mid_price
            try:
                fill = portfolio.execute(self.engine, ticker, quantity)
            except (OrderError, ValidationError) as exc:
                refused.append(f"{ticker}: {exc}")
                self.rejected.append(f"{where} {ticker}: {exc}")
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

        A cohort forks whole: every agent and every portfolio is copied,
        under the labels they had, and the arms carry the same frozen set.
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
            copies = {key: self._fork_agent(agent)
                      for key, agent in self._agents.items()}
            child = World(seed=self.seed, universe=self.universe,
                          agent=copies[SOLO] if self._single else None,
                          agents=None if self._single else copies,
                          pins=self.pins,
                          macro=self.macro, cash=self.cash,
                          max_leverage=self.max_leverage,
                          steps_per_day=self.steps_per_day,
                          ticks_per_step=self.ticks_per_step,
                          start=self.start, model=self.model, label=label,
                          # Carried, like every other setting a fork must
                          # not silently change: an arm that reverted to
                          # "raise" would die on output its sibling counted
                          # and continued past, and the surviving arm's
                          # column would be the only one anybody read.
                          on_refusal=self.on_refusal)
            child.engine = engine
            child._portfolios = {key: copy.deepcopy(book)
                                 for key, book in self._portfolios.items()}
            child._frozen = self._frozen
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

    def _fork_agent(self, agent: Any) -> Any:
        hook = getattr(agent, "fork", None)
        return hook() if callable(hook) else copy.deepcopy(agent)

    def without(self, label: str) -> "World":
        """A fork in which ``label`` sends no orders from this day on.

        The removal an externality matrix measures. The named agent is asked
        nothing and executes nothing in the arm this returns; it keeps the
        positions it held at the fork and they go on being marked to the
        arm's own market, so its net worth still moves with prices it no
        longer influences.

        Removal is inaction from the fork day on rather than a world the
        agent never traded in. The shared history stands in both arms, which
        is what makes them comparable at all: a world where the agent had
        never existed would differ from day zero and there would be no fork
        to measure from.
        """
        if self._single:
            raise ValidationError(
                "without() takes one agent out of a cohort and this world "
                "holds one agent built with agent=. Build it with "
                "agents={label: agent} to have a cohort to remove from.")
        if label not in self._agents:
            raise ValidationError(
                f"no agent is labelled {label!r} here; this world holds "
                f"{', '.join(self._agents)}.")
        (child,) = self.fork(f"without {label}")
        child._frozen = self._frozen | {label}
        return child

    def remove(self, label: str) -> "World":
        """Alias of :meth:`without`."""
        return self.without(label)

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

    def net_worth(self, *, agent: str | None = None) -> float:
        """One agent's cash plus marked positions. ``agent`` names which on
        a cohort and is left out on a single-agent world."""
        return self._portfolios[
            self._label_for(agent, "net_worth")].net_worth(self.engine)

    def summary(self, *, since: int | None = None,
                agent: str | None = None) -> dict[str, Any]:
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

        ``agent`` names which agent on a cohort, where every number here
        belongs to one of them, and is left out on a single-agent world. A
        cohort summary carries the label back under ``agent``.
        """
        label = self._label_for(agent, "summary")
        portfolio = self._portfolios[label]
        start = self.fork_step if since is None else since
        start = 0 if start is None else max(0, min(start, len(self.trace)))
        window = [_fields_of(row, label) for row in self.trace[start:]]
        fills = [f for row in window for f in row["fills"]]
        turnover = sum(abs(f["notional"]) for f in fills)
        cost = _execution_cost(fills)

        base = (_fields_of(self.trace[start - 1], label)["net_worth"]
                if start > 0 else self.cash)
        peak, drawdown = base, 0.0
        for row in window:
            peak = max(peak, row["net_worth"])
            if peak > 0:
                drawdown = max(drawdown, (peak - row["net_worth"]) / peak)

        last = _fields_of(self.trace[-1], label) if self.trace else None
        worth = portfolio.net_worth(self.engine)
        out = {
            "label": self.label,
            "days": self._day,
            "steps": self._step,
            "measured_from_step": start,
            "rebalances": sum(1 for row in window if row["orders"]),
            "orders": sum(len(row["orders"]) for row in window),
            "trades": len(fills),
            "refused": sum(len(row["refused"]) for row in window),
            "unusable_responses": sum(1 for row in window
                                      if row.get("unusable")),
            "partial_fills": sum(1 for f in fills if f["partial"]),
            "turnover": turnover,
            "execution_cost": cost,
            "execution_cost_bps": (cost / turnover * 10_000
                                   if turnover else 0.0),
            "exposure": last["exposure"] if last else 0.0,
            "cash": portfolio.cash,
            "positions": dict(last["positions"]) if last else {},
            "final_net_worth": worth,
            "value_at_start": base,
            "pnl": worth - self.cash,
            "return_pct": (worth - self.cash) / self.cash * 100.0,
            "pnl_since": worth - base,
            "return_since_pct": ((worth - base) / base * 100.0
                                 if base else 0.0),
            "max_drawdown_pct": drawdown * 100.0,
            "market_digest": self.digest(),
            "model_fingerprint": self.engine.model_fingerprint,
            "interventions": copy.deepcopy(self.interventions),
        }
        if not self._single:
            # Only on a cohort. A single-agent summary keeps the keys it
            # has always had, and those are pinned.
            out["agent"] = label
            out["frozen"] = label in self._frozen
        return out

    def __repr__(self) -> str:
        who = ("" if self._single
               else f", agents={', '.join(self._agents)}")
        return (f"World({self.label!r}, seed={self.seed}, day={self._day}, "
                f"step={self._step}, {len(self.universe)} instruments"
                f"{who})")


def _fields_of(row: dict[str, Any], label: str) -> dict[str, Any]:
    """The per-agent half of a trace row, whichever shape the row has.

    A single-agent row carries those fields at the top level and a cohort
    row carries them under ``agents[label]``. Every reader goes through
    here, so the shape is known in one place rather than at each of the
    seven sites that read a row.
    """
    agents = row.get("agents")
    return row if agents is None else agents[label]


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

    Between two cohorts the portfolio and agent rows become one row per
    label, named ``portfolio[label]`` and ``agent state[label]``, so a check
    count grows with the cohort and a difference names the agent it belongs
    to. A label present in one world and absent from the other is a
    difference rather than a skipped row.
    """
    prices_a, prices_b = a.engine.prices(), b.engine.prices()
    snap_a, snap_b = a.engine.state_snapshot(), b.engine.state_snapshot()
    bits_a, bits_b = _bits(snap_a), _bits(snap_b)
    book_a, book_b = _books(a.engine), _books(b.engine)
    solo = a._single and b._single
    labels = sorted(set(a._agents) | set(b._agents))

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
    ]
    for label in labels:
        name = "portfolio" if solo else f"portfolio[{label}]"
        missing = _missing(a, b, label)
        if missing:
            checks.append((name, False, missing))
            continue
        port_a = _portfolio_state(a._portfolios[label])
        port_b = _portfolio_state(b._portfolios[label])
        checks.append((name, port_a == port_b,
                       f"${port_a['cash']:,.0f} cash, "
                       f"{len(port_a['positions'])} positions"))
    for label in labels:
        name = "agent state" if solo else f"agent state[{label}]"
        missing = _missing(a, b, label)
        if missing:
            checks.append((name, False, missing))
            continue
        agent_a = _agent_state(a._agents[label])
        agent_b = _agent_state(b._agents[label])
        checks.append((name, _bits(agent_a) == _bits(agent_b),
                       "no state() hook" if agent_a is None
                       else f"{len(agent_a)} fields"))
    checks.append(
        ("shared history", a.trace == b.trace, f"{len(a.trace)} steps"))
    return Agreement(checks)


def _missing(a: World, b: World, label: str) -> str:
    """Which of two worlds lacks an agent, as a detail line, or empty."""
    absent = [world.label or "the other world"
              for world in (a, b) if label not in world._agents]
    if not absent:
        return ""
    return f"no agent {label!r} in {' and '.join(absent)}"


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


def _column_name(summary: dict[str, Any], fallback: str) -> str:
    """The heading one arm's column takes.

    The arm's label, and on a cohort the agent's label after it, because two
    arms of a cohort experiment differ in the world AND in which of several
    traders the column belongs to.
    """
    label = summary.get("label") or fallback
    agent = summary.get("agent")
    return f"{label}:{agent}" if agent else label


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
        ("unusable responses", "unusable_responses", "{:,.0f}"),
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
        left = _column_name(self.control, "control")
        right = _column_name(self.treatment, "treatment")
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
            *, agreement: Agreement | None = None,
            agent: str | None = None) -> Comparison:
    """Put two forked worlds side by side and find where they came apart.

    ``agreement`` is the :class:`Agreement` taken at the fork, carried through
    so the comparison document says on its face that the arms started
    identical. Optional, because a comparison is still computable without it
    -- but a published one without it is asking to be believed rather than
    checked.

    ``agent`` names the agent the comparison is about, and a cohort world
    requires it: two columns of behaviour and P&L belong to one trader, and a
    cohort has several. The shared rows, macro and prices, come from the
    world either way; decisions, orders and net worth come from that agent.
    Both arms are read under the same label, so the arms must hold it.
    """
    if control.steps_per_day != treatment.steps_per_day:
        raise ValidationError(
            f"arms ran at {control.steps_per_day} and "
            f"{treatment.steps_per_day} steps per day, so their traces do not "
            "line up step for step and no divergence step is meaningful.")
    label_a = control._label_for(agent, "compare")
    label_b = treatment._label_for(agent, "compare")

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

    def first(field: str, shared: bool = False) -> int | None:
        for row_a, row_b in zip(a, b):
            left = row_a if shared else _fields_of(row_a, label_a)
            right = row_b if shared else _fields_of(row_b, label_b)
            if left[field] != right[field]:
                return int(row_a["step"])
        return None

    return Comparison(
        control=control.summary(agent=agent),
        treatment=treatment.summary(since=control.fork_step, agent=agent),
        agreement=agreement,
        divergence=Divergence(
            intervention_step=None if intervention is None
            else intervention["step"],
            intervention_day=None if intervention is None
            else intervention["day"],
            macro=first("macro", shared=True),
            decision=first("decision"),
            orders=first("orders"),
            prices=first("prices", shared=True),
            portfolio=first("net_worth"),
            steps_per_day=control.steps_per_day,
        ),
    )


# ---------------------------------------------------------------------------
# The agent's own noise floor
# ---------------------------------------------------------------------------

def _shape(decision: Any) -> tuple:
    """One answer's orders, canonical and hashable.

    Sorted, so two answers naming the same trades in a different order are
    the same answer. The quantity is in, because "buy 2,000" and "buy 200"
    are different decisions and collapsing them would understate the
    spread this function exists to measure.
    """
    return tuple(sorted((action.symbol, action.side, float(action.quantity))
                        for action in decision.actions))


def _net(decision: Any) -> float:
    """Buys minus sells, counted in ACTIONS rather than shares.

    The direction of the answer, at the granularity the agent chose it: an
    agent that turned one buy into one sell moved by 2 whether the position
    was a hundred shares or a million. `_gross` carries the size.
    """
    sides = [action.side.upper() for action in decision.actions]
    return float(sides.count("BUY") - sides.count("SELL"))


def _gross(decision: Any) -> float:
    """Shares moved, both directions."""
    return float(sum(abs(action.quantity) for action in decision.actions
                     if action.side.upper() in ("BUY", "SELL")))


def _lines(prompt: Any) -> list[str]:
    """A prompt as comparable lines, whatever shape the adapter sent.

    Text is already lines. Anything else -- a message list, a payload
    mapping -- is rendered as sorted, indented JSON, which puts one field
    per line so a diff names fields rather than offsets.
    """
    if isinstance(prompt, str):
        return prompt.splitlines()
    return json.dumps(_jsonable_prompt(prompt), indent=2,
                      sort_keys=True, default=str).splitlines()


def _jsonable_prompt(prompt: Any) -> Any:
    from .integrations.common import jsonable

    return jsonable(prompt)


def _field_of(line: str) -> str:
    """The field a diff line is about, for an error a reader can act on."""
    match = re.match(r'\s*"?([A-Za-z_][\w.\-]*)"?\s*[:=]', line)
    return match.group(1) if match else line.strip()[:60]


class Resample:
    """One decision point, asked N times per arm.

    `compare` reports one trajectory each, which is the whole answer for a
    deterministic policy and half of it for a language model: the agent is
    the one stochastic component left in an otherwise bit-identical
    experiment, and a single pair of trajectories cannot separate "the
    agent responded to the intervention" from "the agent answered the same
    question two ways".

    Everything else has already been eliminated by construction, which is
    what makes a small N enough here. :func:`agree` verifies the whole
    engine state at the fork, and the two arms' inputs at the first
    post-fork decision differ only in the intervened fields -- checked, not
    assumed, by :attr:`identical_inputs`. So this measures exactly one
    thing.

    Read :attr:`separation` against :attr:`noise`: a between-arm gap
    smaller than an arm's own within-arm spread is not a finding. Read
    ``distinct`` and ``modal_share`` as well, because two arms can differ
    in decision STABILITY at the same mean -- one distinct answer in eight
    calls against four is a real difference that a mean hides.
    """

    __slots__ = ("at", "n", "control", "treatment", "noise", "separation",
                 "identical_inputs", "differing_lines", "intervened_fields")

    def __init__(self, *, at: int, n: int, control: str, treatment: str,
                 noise: dict, separation: dict, identical_inputs: bool,
                 differing_lines: list, intervened_fields: list) -> None:
        self.at = at
        self.n = n
        self.control = control
        self.treatment = treatment
        #: Per arm: sample count, refusals, distinct answers, modal share,
        #: and the mean and population stdev of `net` and `gross`.
        self.noise = noise
        #: The between-arm gap in units of the larger within-arm stdev.
        #: `None` where neither arm varied: a ratio over zero is not a
        #: large number, it is an undefined one, and `inf` in a published
        #: table reads as a result.
        self.separation = separation
        #: True when the two inputs are BYTE-IDENTICAL, which means the
        #: intervention had not reached the agent by this step. Worth
        #: knowing before reading a gap: with identical inputs the two
        #: arms answered the same question, so the whole gap is agent
        #: noise and there is no intervention effect in it to find. False
        #: is the ordinary case, and the differences are in
        #: :attr:`differing_lines`; anything they cannot account for was
        #: already refused.
        self.identical_inputs = identical_inputs
        #: The lines the two inputs differ on, for the write-up. Every one
        #: of them is attributable to an intervened field, because a
        #: difference that was not would have raised.
        self.differing_lines = differing_lines
        self.intervened_fields = intervened_fields

    def as_dict(self) -> dict[str, Any]:
        """Artifact-shaped, like :meth:`Comparison.as_dict`.

        JSON-serialisable and credential-free: counts, symbol names, and
        the differing prompt lines, which come from the allowlisted
        observation and carry nothing the agent was not shown.
        """
        return {
            "at": self.at,
            "n": self.n,
            "arms": {"control": self.control, "treatment": self.treatment},
            "identical_inputs": self.identical_inputs,
            "intervened_fields": list(self.intervened_fields),
            "differing_lines": [dict(row) for row in self.differing_lines],
            "noise": copy.deepcopy(self.noise),
            "separation": dict(self.separation),
        }

    ROWS = (
        ("calls made", "samples", "{:,.0f}"),
        ("unusable answers", "refusals", "{:,.0f}"),
        ("distinct answers", "distinct", "{:,.0f}"),
        ("modal share", "modal_share", "{:.0%}"),
        ("net (buys - sells)", "mean_net", "{:+.2f}"),
        ("its own stdev", "stdev_net", "{:.2f}"),
        ("gross shares", "mean_gross", "{:,.0f}"),
        ("its own stdev", "stdev_gross", "{:,.0f}"),
    )

    def render(self, width: int = 24) -> str:
        left, right = self.control, self.treatment
        out = [f"  resampled step {self.at}, {self.n} calls per arm",
               f"  {'':<{width}} {left:>16} {right:>16}",
               "  " + "-" * (width + 34)]
        for label, key, fmt in self.ROWS:
            a = self.noise[self.control][key]
            b = self.noise[self.treatment][key]
            out.append(f"  {label:<{width}} {fmt.format(a):>16} "
                       f"{fmt.format(b):>16}")
        out.append("  " + "-" * (width + 34))
        for measure in ("net", "gross"):
            gap = self.separation[f"gap_{measure}"]
            floor = self.separation[f"floor_{measure}"]
            ratio = self.separation[measure]
            said = ("undefined: neither arm varied" if ratio is None
                    else f"{ratio:.2f} within-arm stdevs")
            out.append(f"  {measure + ' gap':<{width}} "
                       f"{gap:+.2f} against a noise floor of {floor:.2f} "
                       f"-- {said}")
        if self.identical_inputs:
            out.append("  the two inputs are identical: the intervention "
                       "had not reached the agent by this step, so the "
                       "gap above is agent noise and nothing else")
        else:
            fields = ", ".join(self.intervened_fields) or "nothing"
            out.append(f"  inputs differ in {fields} and nowhere else")
        return "\n".join(out)

    def __repr__(self) -> str:
        return (f"Resample(at={self.at}, n={self.n}, "
                f"{self.control!r} vs {self.treatment!r})")


def _intervened_fields(*worlds: World) -> list[str]:
    touched: set[str] = set()
    for world in worlds:
        for entry in world.interventions:
            touched.update(entry["fields"])
        for applied in world.applied:
            target = getattr(applied, "target", None)
            if target:
                touched.add(str(target))
    return sorted(touched)


def _entry_at(world: World, at: int) -> dict[str, Any]:
    record = getattr(world.agent, "record", None)
    if not record:
        raise ValidationError(
            f"arm {world.label!r} has no decision record. resample replays "
            "the input an adapter recorded; a plain policy has none, and a "
            "world that has not run has nothing to resample.")
    for entry in record:
        if entry.get("step") == at:
            return entry
    steps = [entry.get("step") for entry in record]
    raise ValidationError(
        f"arm {world.label!r} took no decision at step {at}. It decided at "
        f"{steps}. The agent is asked every {getattr(world.agent, 'every', 1)} "
        "steps, so pass one of those; the first post-fork decision is the "
        "step a reader is about to draw a conclusion from.")


def _diff(control_entry: dict, treatment_entry: dict,
          touched: Sequence[str]) -> list[dict[str, str]]:
    """The differing prompt lines, refusing any the intervention cannot own.

    Two arms whose inputs differ for a second reason are not a controlled
    resample. This is the check that makes the measurement one, and today
    that difference is silent.
    """
    left = _lines(control_entry.get("prompt"))
    right = _lines(treatment_entry.get("prompt"))
    rows: list[dict[str, str]] = []
    stray: list[str] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(
            None, left, right).get_opcodes():
        if tag == "equal":
            continue
        rows.append({"control": "\n".join(left[i1:i2]),
                     "treatment": "\n".join(right[j1:j2])})
        # Line by line rather than block by block. One replace opcode
        # can span a changed rate AND a changed price, and asking
        # whether the BLOCK mentions an intervened field lets the price
        # ride in on the rate's ticket -- which is the single thing this
        # check exists to catch.
        for line in left[i1:i2] + right[j1:j2]:
            if not any(field in line for field in touched):
                stray.append(_field_of(line))
    if stray:
        named = ", ".join(sorted(set(stray))[:8])
        raise ValidationError(
            f"the two arms' inputs differ in {named}, which no intervention "
            f"touched (the interventions moved {list(touched) or 'nothing'}). "
            "Two arms answering different questions are not a controlled "
            "resample, and the difference is invisible in the result. This "
            "is for the FIRST post-fork decision, where the arms have the "
            "same prices, the same positions and the same history; by the "
            "second the market has already answered the intervention and "
            "every line differs.")
    return rows


def _statistics(decisions: Sequence[Any], refusals: int,
                n: int) -> dict[str, Any]:
    shapes = [_shape(decision) for decision in decisions]
    nets = [_net(decision) for decision in decisions]
    grosses = [_gross(decision) for decision in decisions]
    modal = Counter(shapes).most_common(1)
    bought, sold = set(), set()
    for decision in decisions:
        for action in decision.actions:
            if action.side.upper() == "BUY":
                bought.add(action.symbol)
            elif action.side.upper() == "SELL":
                sold.add(action.symbol)
    return {
        "samples": n,
        "parsed": len(decisions),
        "refusals": refusals,
        "distinct": len(set(shapes)),
        "modal_share": (modal[0][1] / len(shapes)) if shapes else 0.0,
        "mean_net": statistics.fmean(nets) if nets else 0.0,
        "stdev_net": statistics.pstdev(nets) if nets else 0.0,
        "mean_gross": statistics.fmean(grosses) if grosses else 0.0,
        "stdev_gross": statistics.pstdev(grosses) if grosses else 0.0,
        "bought": sorted(bought),
        "sold": sorted(sold),
    }


def _ask_again(world: World, entry: dict, n: int) -> tuple[list, int]:
    """``n`` fresh answers to one recorded input. No market is advanced.

    A call that fails to parse is counted as a refusal and never retried.
    An agent that returns unusable output on three of twenty calls is a
    finding, and sampling until twenty parse would hide it.
    """
    from .integrations.common import DecisionError, parse_decision

    reask = getattr(world.agent, "reask", None)
    if not callable(reask):
        raise ValidationError(
            f"arm {world.label!r} runs an agent with no reask(). resample "
            "asks one recorded input again; a FrameworkAdapter implements "
            "it, and a plain policy has no framework to ask.")
    decisions, refusals = [], 0
    for _ in range(n):
        try:
            decisions.append(parse_decision(reask(entry)))
        except DecisionError:
            refusals += 1
    return decisions, refusals


def resample(control: World, treatment: World, *, at: int | None = None,
             n: int = 8) -> Resample:
    """Ask one decision point ``n`` times per arm, and report the spread.

    The measurement that keeps a false finding out of a paper. Worked
    example, live, both arms at temperature 0: at the first post-fork
    decision the prompts differed in 2 lines of 376, and the recorded
    trajectories then diverged readably -- control bought the dip, the
    shock arm cut exposure "to manage downside risk". Resampling those two
    exact prompts eight times each: control gave 4 distinct answers with a
    net of 0.62 +/- 0.99, the shock arm gave 1 answer in 8 calls with a net
    of 0.00 +/- 0.00. The between-arm gap of 0.62 sits inside control's own
    spread. The recorded split was one of control's four available answers,
    and nothing in this library would have said so.

    The same numbers carry a second reading worth keeping: four distinct
    answers against one is a difference in decision STABILITY rather than
    in direction, it is invisible to :func:`compare`, and it is only
    observable because the input was byte-identical eight times.

    ``at`` defaults to the treatment arm's fork step, the first decision
    the intervention could have reached. No market is advanced: this
    replays two frozen inputs, so N paired samples cost N calls rather than
    N re-simulations.

    No p-value. The gap is reported in units of the noise floor and the
    reader judges; a significance test would imply an inference model
    nobody here has argued for.
    """
    if n < 2:
        raise ValidationError(
            f"resample needs at least 2 calls per arm to have a spread to "
            f"report, got n={n}.")
    if at is None:
        at = treatment.fork_step
    if at is None:
        raise ValidationError(
            "resample needs a step. Neither arm was forked, so there is no "
            "fork_step to default to; pass at=<decision step>.")
    at = int(at)

    left = control.label or "control"
    right = treatment.label or "treatment"
    if left == right:
        raise ValidationError(
            f"both arms are labelled {left!r}, so one arm's numbers would "
            "land on top of the other's and the result would describe one "
            "arm twice. Label them apart; `fork` refuses duplicate labels "
            "for the same reason.")

    touched = _intervened_fields(control, treatment)
    control_entry = _entry_at(control, at)
    treatment_entry = _entry_at(treatment, at)
    differing = _diff(control_entry, treatment_entry, touched)

    noise: dict[str, Any] = {}
    for world, entry, label in ((control, control_entry, left),
                                (treatment, treatment_entry, right)):
        decisions, refusals = _ask_again(world, entry, n)
        noise[label] = _statistics(decisions, refusals, n)

    separation: dict[str, Any] = {}
    for measure in ("net", "gross"):
        gap = (noise[right][f"mean_{measure}"]
               - noise[left][f"mean_{measure}"])
        floor = max(noise[left][f"stdev_{measure}"],
                    noise[right][f"stdev_{measure}"])
        separation[f"gap_{measure}"] = gap
        separation[f"floor_{measure}"] = floor
        # None rather than inf. A ratio over zero is undefined, not large,
        # and `inf` printed in a table reads as an overwhelming result.
        separation[measure] = abs(gap) / floor if floor > 0 else None

    return Resample(at=at, n=n, control=left, treatment=right, noise=noise,
                    separation=separation, identical_inputs=not differing,
                    differing_lines=differing, intervened_fields=touched)
