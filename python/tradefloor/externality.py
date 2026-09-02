"""What each agent in a cohort did to each other agent, under the same draws.

Several traders in one market affect each other, and no run of that market
says by how much. The P&L each one ends with is the P&L it ended with in a
market the others were also trading; the part of it that belongs to the
others is not a column anywhere. Estimating it needs a market without one of
them, and a market without one of them is a market nobody ran.

Here every one of them is runnable. The cohort forks once per agent, one
agent is frozen in each fork, and every arm carries the same engine state,
the same generator position and the same shared history:

```python
world = World(seed=42, universe=roster,
              agents={"momentum": Momentum(), "reversion": Reversion()})
world.run(days=5)
result = externalities(world, days=10)
print(result.render())
```

``matrix[a][b]`` is what b's P&L since the fork changes by when a stops
trading. ``diagonal[a]`` is what a's own execution cost against the market
it would have traded into had it not been there, which is implementation
shortfall against the world-without-a. On a one-agent cohort forked at day
zero it equals :func:`tradefloor.tca.analyse` to the bit, because the
world-without-a is then the nobody-trades world and the two measurements
cover the same steps. On a cohort that has already run they measure
different windows: this leaves out the fills before the fork, which both
arms share, and ``analyse`` has no shared history to leave out.

## Why the arms are exact rather than estimated

Order flow consumes no draws. The engine derives its market, economy and
external streams from the root seed, and the flow an agent sends changes
prices without moving any stream's position, which `rust/src/engine.rs`
states and `tca.py` measures. So an arm that removes one agent draws the
same numbers in the same order as the arm that keeps it, and every
difference between them descends from the orders that agent did not send.
The arms are forks of one state rather than reruns from one seed, so the
shared history is the same object in all of them.

## What one number in the matrix holds

An effect on b is b's P&L change. It arrives as prices, and two things
reach b through prices: the impact a's flow left on the market, and b's own
reaction to the market a made. Both are in the number and this cannot split
them. Splitting them needs a third arm in which b sees a's prices and
answers as though it did not, and there is no such arm.

The channel is impact rather than liquidity. Agents in a cohort take no
levels from each other, because :meth:`Portfolio.execute` reads the ladder
and removes nothing, so a's whole effect on b travels through the merged
``order_flow`` of a session and the prices it produced. `counterfactual.py`
carries the measurement.

Removal is inaction from the fork day on. The removed agent keeps the
positions it held at the fork, and a frozen holding sends no flow, so it
moves no price in the arm and enters no number here. A world where the
agent had never existed would differ from day zero and there would be no
fork to measure from. :meth:`Externality.caveats` names that choice on
every result, computed from the arms rather than typed here.
"""

from __future__ import annotations

from typing import Any

from ._core import ValidationError
from . import tca
from .counterfactual import Agreement, World, _f64, agree


class Externality:
    """One cohort's agents, measured against the cohorts without each of them.

    ``matrix[a][b]`` is b's ``pnl_since`` in the arm where a was removed
    minus b's ``pnl_since`` in the arm where the whole cohort ran. Positive
    means b did better without a. ``matrix[a][a]`` is the diagonal entry,
    which is a different quantity in the same currency: a's own execution
    shortfall against the market without it, positive for a cost. A row does
    not sum to anything, and :meth:`render` says so.

    ``cohort_pnl[b]`` is b's ``pnl_since`` with everyone present, which is
    what each effect is a change to. ``trades[b]`` counts b's fills over the
    same window. ``agreement`` carries one row per arm and is identical only
    when every arm started from the state the cohort was forked at.
    """

    __slots__ = ("labels", "matrix", "diagonal", "diagonal_bps", "cohort_pnl",
                 "trades", "agreement", "days", "fork_day", "fork_step",
                 "held_at_fork")

    #: The columns :meth:`table` produces, in order. One row per ordered
    #: pair of labels, so an N-agent cohort is N squared rows.
    COLUMNS = ("removed", "affected", "kind", "value")

    def __init__(self, *, labels, matrix, diagonal, diagonal_bps, cohort_pnl,
                 trades, agreement, days, fork_day, fork_step,
                 held_at_fork) -> None:
        self.labels = tuple(labels)
        self.matrix = matrix
        self.diagonal = diagonal
        #: The diagonal in basis points of the notional that agent traded
        #: over the window. Currency alone does not compare a $10m programme
        #: to a $100k one, which is the same argument `tca.py` makes.
        self.diagonal_bps = diagonal_bps
        self.cohort_pnl = cohort_pnl
        self.trades = trades
        self.agreement = agreement
        self.days = days
        self.fork_day = fork_day
        self.fork_step = fork_step
        #: The agents holding a position when the fork was taken. Their
        #: removal freezes those positions rather than unwinding them.
        self.held_at_fork = tuple(held_at_fork)

    # -- reading it -------------------------------------------------------

    def effect_on(self, label: str) -> dict[str, float]:
        """Every other agent's effect on ``label``, keyed by the agent
        removed. The column of the matrix rather than the row."""
        if label not in self.labels:
            raise ValidationError(
                f"no agent is labelled {label!r} here; this result covers "
                f"{', '.join(self.labels)}.")
        return {a: self.matrix[a][label] for a in self.labels if a != label}

    def caveats(self) -> list[str]:
        """What this particular result cannot support, computed from it.

        Each line below fires on a property of the run, so a result never
        carries a caveat that does not apply to it, which keeps the ones it
        does carry worth reading. The rule and its reasoning are
        `python/tradefloor/mcp.py`'s.
        """
        held = (f" At this fork {_names(self.held_at_fork)} held a "
                f"position, so those arms are markets in which an agent "
                f"stopped trading with a position on." if self.held_at_fork
                else "")
        out = [
            "An effect is a P&L change and nothing finer. The impact the "
            "removed agent's flow left on prices and the affected agent's "
            "own reaction to those prices are both in the number, because "
            "both arrive as prices.",
            f"Removal is inaction from day {self.fork_day} on. The removed "
            f"agent sends no order and is asked nothing; it keeps the "
            f"positions it held at the fork, and a frozen holding sends no "
            f"flow, so it moves no price in the arm and enters no number "
            f"here.{held}",
        ]
        if not self.agreement.identical:
            out.append(
                "THE ARMS DID NOT START IDENTICAL, so every number here "
                "describes more than one changed variable. The arms differ "
                "in " + ", ".join(self.agreement.differences) + ".")
        idle = [b for b in self.labels if not self.trades[b]]
        if idle:
            out.append(
                f"{len(idle)} of {len(self.labels)} agents filled no trade "
                f"over the {self.days} measured {_days(self.days)} "
                f"({', '.join(idle)}). An agent that filled no trade sent "
                f"the market nothing, since a refused order reaches neither "
                f"the book nor the session, so each of those rows is zero; "
                f"each of those columns still moves, because the prices its "
                f"holdings mark against moved.")
        flat = sum(1 for a in self.labels for b in self.labels
                   if a != b and self.matrix[a][b] == 0.0)
        pairs = len(self.labels) * (len(self.labels) - 1)
        if pairs and flat == pairs:
            out.append(
                f"Every off-diagonal entry is zero. Over "
                f"{self.days} {_days(self.days)} on this roster the cohort "
                f"is separable: each agent's P&L is what it would have "
                f"been alone.")
        if self.days == 1:
            out.append(
                "The window is one day. An effect that needs one session to "
                "reach prices and another to reach a decision has had one "
                "of the two.")
        return out

    def table(self):
        """The matrix as one Arrow table, one row per ordered pair.

        Columns are :attr:`COLUMNS`: ``removed`` and ``affected`` name the
        two agents, ``kind`` is ``externality`` off the diagonal and
        ``shortfall`` on it, and ``value`` is the currency figure. The kind
        column is there because the two are different quantities and a
        reader summing a column without it would be adding a P&L change to
        an execution cost.
        """
        try:
            import pyarrow as pa
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Externality.table() returns an Arrow table and needs "
                "pyarrow. Install it with: pip install tradefloor[arrow]"
            ) from exc

        removed: list[str] = []
        affected: list[str] = []
        kind: list[str] = []
        value: list[float] = []
        for a in self.labels:
            for b in self.labels:
                removed.append(a)
                affected.append(b)
                kind.append("shortfall" if a == b else "externality")
                value.append(self.matrix[a][b])
        return pa.table({"removed": removed, "affected": affected,
                         "kind": kind, "value": value})

    def as_dict(self) -> dict[str, Any]:
        """Artifact-shaped, like :meth:`Comparison.as_dict`. JSON-serialisable
        and carrying nothing an agent was not shown."""
        return {
            "labels": list(self.labels),
            "days": self.days,
            "fork_day": self.fork_day,
            "fork_step": self.fork_step,
            "cohort_pnl": dict(self.cohort_pnl),
            "trades": dict(self.trades),
            "matrix": {a: dict(row) for a, row in self.matrix.items()},
            "diagonal": dict(self.diagonal),
            "diagonal_bps": dict(self.diagonal_bps),
            "held_at_fork": list(self.held_at_fork),
            "agreement": self.agreement.as_dict(),
            "caveats": self.caveats(),
        }

    def render(self, width: int = 18) -> str:
        longest = max((len(label) for label in self.labels), default=8)
        width = max(width, longest + 9)
        cell = max(14, longest + 3)
        out = [
            f"  externalities over {self.days} {_days(self.days)} from "
            f"day {self.fork_day}, {len(self.labels)} agents",
            "",
            "  P&L since the fork, whole cohort present",
        ]
        for b in self.labels:
            out.append(f"    {b:<{width}}"
                       f"{'$' + format(self.cohort_pnl[b], '+,.0f'):>{cell}}"
                       f"   {self.trades[b]:,} trades")
        out += [
            "",
            "  effect on the column agent of removing the row agent, "
            "in dollars",
            "    " + " " * width + "".join(f"{b:>{cell}}"
                                           for b in self.labels),
        ]
        for a in self.labels:
            row = "".join(
                f"{self.diagonal[a]:>+{cell - 1},.0f}*" if a == b
                else f"{self.matrix[a][b]:>+{cell - 1},.0f} "
                for b in self.labels)
            out.append(f"    {'remove ' + a:<{width}}{row}")
        out += [
            "",
            "  * the row agent's own execution shortfall against the market "
            "without it, positive for a cost",
        ]
        for a in self.labels:
            out.append(f"    {a:<{width}}{self.diagonal[a]:>+{cell},.0f}"
                       f"   {self.diagonal_bps[a]:+.2f} bps")
        out += ["", "  arms at the fork"]
        for line in self.agreement.render(width).splitlines():
            out.append("  " + line)
        out += ["", "  caveats"]
        for line in self.caveats():
            out.append(f"    - {line}")
        return "\n".join(out)

    def __repr__(self) -> str:
        return (f"Externality({', '.join(self.labels)}, days={self.days}, "
                f"identical={self.agreement.identical})")


def _days(count: int) -> str:
    """"day" or "days", so a rendered line does not read "1 days"."""
    return "day" if count == 1 else "days"


def _names(labels: "tuple[str, ...]") -> str:
    """Labels as an English list, so a caveat reads as a sentence."""
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def externalities(world: World, days: int = 1) -> Externality:
    """Fork a cohort, remove each agent in turn, and measure what changed.

    ``world`` is a cohort built with ``agents={label: agent}``, at a day
    boundary, run to wherever the measurement should start from. It is not
    advanced: every arm is a fork of it, so calling this twice on one world
    gives the same numbers twice.

    One arm keeps the whole cohort and one arm per agent freezes that agent,
    each forked from the same state and each running the same ``days`` under
    the same draws. The cost is one simulation per agent plus one, so an
    eight-agent cohort runs nine markets.

    Returns an :class:`Externality`. Read :meth:`Externality.caveats` beside
    the numbers: they say what the arms were and what the matrix holds.
    """
    if not isinstance(world, World):
        raise ValidationError(
            f"externalities() takes a World, got {type(world).__name__}. "
            f"Build one with World(seed=..., universe=..., "
            f"agents={{'a': A(), 'b': B()}}).")
    if not world.is_cohort:
        raise ValidationError(
            "externalities() measures what each agent does to the others "
            "and this world holds one agent built with agent=. Build it "
            "with agents={label: agent}. For what one agent's trading cost "
            "against a market it never traded, use tradefloor.tca.analyse.")
    if days < 1:
        raise ValidationError(
            f"days must be at least 1 to measure anything, got {days}")

    labels = tuple(world.agents)
    fork_step = world.step
    fork_day = world.day
    # The cross-section every arm opens on. A trace row records the prices
    # its session left, so the fork state carries the one price row no row
    # can, and `_path` needs it to line a fill up with the step it happened
    # on.
    at_fork = _f64(world.engine.prices())
    held = tuple(label for label in labels
                 if any(position.quantity
                        for position in world.portfolios[label].positions
                        .values()))

    (full,) = world.fork("full")
    arms = {label: world.without(label) for label in labels}
    # Taken before anything runs. An agreement measured after the arms have
    # diverged would report the divergence rather than the fork.
    checks = [(f"without {label}", *_arm_check(agree(full, arm)))
              for label, arm in arms.items()]
    agreement = Agreement(checks)

    full.run(days=days)
    for arm in arms.values():
        arm.run(days=days)

    present = {b: full.summary(agent=b) for b in labels}
    cohort_pnl = {b: present[b]["pnl_since"] for b in labels}
    trades = {b: present[b]["trades"] for b in labels}

    matrix: dict[str, dict[str, float]] = {}
    diagonal: dict[str, float] = {}
    diagonal_bps: dict[str, float] = {}
    for a in labels:
        arm = arms[a]
        execution = _execution(full, arm, a, fork_step, at_fork)
        diagonal[a] = execution.shortfall()
        diagonal_bps[a] = execution.shortfall_bps()
        matrix[a] = {
            b: (diagonal[a] if b == a
                else arm.summary(agent=b)["pnl_since"] - cohort_pnl[b])
            for b in labels}

    return Externality(labels=labels, matrix=matrix, diagonal=diagonal,
                       diagonal_bps=diagonal_bps, cohort_pnl=cohort_pnl,
                       trades=trades, agreement=agreement, days=days,
                       fork_day=fork_day, fork_step=fork_step,
                       held_at_fork=held)


def _arm_check(arm: Agreement) -> tuple[bool, str]:
    """One arm's whole agreement, as one row of the result's own.

    A cohort agreement is seven shared checks and two per label, so an
    eight-agent cohort taken against eight arms is 184 rows. One row per arm
    keeps the table readable and loses nothing: a row that is not identical
    names the checks that differed, which is what a reader acts on.
    """
    if arm.identical:
        return True, f"{len(arm.checks)} checks identical"
    return False, "differs in " + ", ".join(arm.differences)


def _execution(full: World, arm: World, label: str, fork_step: int,
               at_fork: list[float]) -> tca.Execution:
    """One agent's fills in the full cohort, priced against the arm without it.

    :class:`tradefloor.Execution` does the arithmetic, so the diagonal is
    implementation shortfall as `tca.py` defines it rather than a second
    implementation of the same sum. What this supplies is the baseline: the
    price path of the arm in which this agent did not trade, in place of the
    nobody-trades path :func:`tradefloor.tca.analyse` builds.

    Steps count from the fork rather than from the world's day zero. Fills
    before the fork are left out: the arms share that history exactly, so
    those fills price against their own market and their cost is the shared
    history's, not this agent's externality.
    """
    fills = [dict(fill, step=fill["step"] - fork_step)
             for fill in full.portfolios[label].fills
             if fill["step"] >= fork_step]
    return tca.Execution(
        tickers=full.engine.tickers,
        fills=fills,
        baseline_path=_path(arm, fork_step, at_fork),
        actual_path=_path(full, fork_step, at_fork),
        seed=full.seed,
        portfolio=full.portfolios[label],
        steps=full.step - fork_step,
        universe_fingerprint=full.universe_fingerprint,
        model_fingerprint=full.engine.model_fingerprint,
    )


def _path(world: World, fork_step: int,
          at_fork: list[float]) -> list[list[float]]:
    """The cross-section every measured step opened on, then the final one.

    The shape :func:`tradefloor.tca.analyse` builds: one row per step, read
    before that step's orders, and the end-of-run row appended. A trace row
    records the prices its own session left, so row k-1 carries step k's
    opening cross-section and the fork state carries step zero's.

    Crossing a day boundary costs nothing here because ``close_market`` and
    ``open_market`` move no price, which ``tests/test_externality.py`` pins
    directly rather than leaving to this comment.
    """
    return [at_fork] + [list(row["prices"])
                        for row in world.trace[fork_step:]]
