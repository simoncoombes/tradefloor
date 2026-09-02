"""Find the smallest intervention that flips an agent's decision, and map
where its behaviour is discontinuous across the shipped scenarios.

:func:`tradefloor.compare` says whether one intervention changed what an
agent did. This module asks the next question: how far does a lever have
to move before the decision changes. It bisects one intervention target
between two values, one fresh fork and one day per probe, and closes on the
narrowest bracket whose two ends draw different decisions.

```python
found = flip(world, "macro.policy_rate", operation="set",
             bracket=(0.04, 0.08), steps=8)
if found:
    print(found.render())

atlas = map_boundaries(world, bracket=(0.5, 2.0))
print(atlas.render())
```

## Method

:func:`search` forks the world at the day asked for (today by default) and
forks two arms off that base, one per bracket end. :func:`tradefloor.agree`
checks the arms before anything is written to either; a fork that did not
start identical is refused rather than measured. Each arm then takes one
intervention on the target and runs one day, and its decision is read from
the trace at the first decision step of that day. A macro target is written
through :meth:`World.intervene` in the engine's own field, at the absolute
value the operation produces from the level the target had at the fork;
``market.liquidity`` is a column and is written through
:meth:`World.apply`. Bisection then halves the bracket, one fresh fork per
probe, keeping the half whose ends still disagree, and stops after ``steps``
halvings or when the two ends print alike in the target's own format.

A decision differs when its shape differs: the canonical sorted order set
:func:`tradefloor.counterfactual._shape` uses, which is symbol, side and
quantity and ignores the rationale.

## The floor

A closed bracket is a candidate, and a language model is the one
stochastic part of the experiment. Before a flip is reported,
:func:`tradefloor.resample` asks the two bracketing arms' exact inputs
:data:`FLOOR_CALLS` times each, and the flip is reported only when the
gap between the two arms' mean ``net`` answers (buys minus sells, in
actions) exceeds one within-arm standard deviation, the ``separation``
that function computes. Two arms that answered identically every time
have a floor of zero, and a non-zero gap stands on it.

Three things stop a candidate short of that test, each with its own
status. Two arms whose inputs were byte-identical never report a flip
(``unseen``): the intervention did not reach the agent, so the recorded
difference is agent noise. Two recorded decisions that agree in ``net``
(``same net``), a change of quantity or of symbol, cannot clear a floor
measured on ``net``, and no floor is asked for. And a floor rests on the
answers that parsed: ``resample`` averages an arm over its executable
answers, so an arm whose re-asks all refused reads as a perfectly stable
arm with no spread. An arm with fewer than two parsed answers therefore
leaves the floor unmeasured (``floor unmeasurable``), as does an agent
that cannot be asked again at all, because it has no ``reask`` hook or is
replaying a recording. The search says which in its caveats.

## What it measures, and what it cannot

It measures where one agent's decision changes under one intervention on
one day of one seeded market, with the agent's own sampling noise measured
on the bracketing prompts rather than assumed. The bracket that closed
holds one change of decision; an agent whose decision changes several times
inside the original bracket has the other changes unreported, and the
caveats count the distinct decisions the probes saw. It cannot say why the
decision changed: the decision text is recorded, and the market's own
attribution is :meth:`tradefloor.Engine.attribution`.

Two targets cannot have a floor measured through :func:`resample` as it
stands. ``macro.vix`` and ``market.liquidity`` reach the quoted book on the
day they move, so the two arms' inputs differ in bid and ask lines beside
the intervened field, and ``resample`` refuses inputs that differ on a line
no intervention names. Such a search closes its bracket and reports the
floor as unmeasurable, with the refusal in its caveats.

Against a recording, the search is exact replay. A probe whose prompt the
recording does not hold raises, and the probe is recorded as unreachable
rather than skipped. Measured on the shipped FinRobot recording,
``tests/fixtures/finrobot/rate-shock.json``, on its own four-name roster at
seed 4242 after the twenty recorded days, with ``multiply`` over the
bracket (0.5, 1.4) and two halvings, on the engine, roster and recording
of commit f47c149 (main) with the search in this module: of the nine
targets a multiplier can move there, the five the prompt shows (the two
rates, VIX, inflation and quoted depth) are unreachable at the first probe,
and the four it does not show (oil, growth, unemployment, the tariff rate)
replay the recorded control decision at both bracket ends and report no
flip, with the two inputs byte-identical. Every shipped scenario's first
shock day lies past the recording's last day, so a scenario row is
unreachable before its first probe. ``tests/test_boundary.py`` derives
those five and four from the adapter's observable macro fields and asserts
them.

## Scenarios

:func:`map_boundaries` runs one search per target per scenario. For each
named scenario it forks the world, applies the scenario and runs it to its
first shock day, so the probe day is the day the first shock fires. A
target the scenario itself writes on that day is reported as shadowed and
is not searched, because a probe under a scenario's own write would be
written over or stacked on and would measure the scenario rather than the
probe. ``None`` in the scenario list means the world as it stands.

The library imports nothing at runtime. :meth:`BoundaryMap.table` imports
``pyarrow`` when called, and the agent is the caller's: nothing here calls
a provider.
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from ._core import Engine, ValidationError, random_instruments
from .counterfactual import (Agreement, Resample, World, _net, _shape, agree,
                             resample)
from .interventions import (CYCLES, OPERATIONS, TARGETS, Intervention,
                            Target, apply_operation, resolve, summarise)
from .scenario import Scenario
from .universe_util import fingerprint_of

#: Calls per arm when the floor is measured. :func:`tradefloor.resample`'s
#: own default, so a floor read here and a floor read there rest on the
#: same sample.
FLOOR_CALLS = 8

#: Every outcome a search can end in. ``flip`` is the only one that reports
#: a boundary; the rest say why the search did not.
STATUSES = (
    "flip",                # the bracket closed and the gap clears the floor
    "inside floor",        # the bracket closed and the gap does not
    "unseen",              # the bracketing arms sent identical inputs
    "same net",            # the two decisions agree in net: a change of
                           # quantity or symbol the floor cannot see
    "floor unmeasurable",  # the bracket closed and no floor was measured
    "no flip",             # the two bracket ends drew the same decision
    "unreachable",         # a probe's prompt is outside the recording
    "unusable",            # a probe's agent returned no executable decision
    "shadowed",            # the scenario writes the target on the probe day
    "refused",             # the target or the bracket cannot be searched
    "failed",              # the framework call itself did not complete
)

#: The outcomes one probe can have.
OUTCOMES = ("decided", "unusable", "unreachable")

#: The columns of :meth:`BoundaryMap.table`, in order. Two gaps, and they
#: are different numbers whenever the agent is not deterministic:
#: ``net_gap`` is between the two RECORDED decisions the bracket closed
#: on, ``floor_gap`` between the two arms' RESAMPLED mean answers, and
#: ``separation`` is ``floor_gap`` over ``floor``. Those three are null
#: on every row whose floor was not measured, the ``floor unmeasurable``
#: rows included: a resample that ran and was gated is carried in full
#: on :attr:`Search.floor` and in :meth:`BoundaryMap.as_dict`, and stays
#: out of the columns a reader sorts on.
COLUMNS = (
    "scenario", "target", "operation", "day", "step", "status",
    "low", "high", "seen_low", "seen_high",
    "decision_low", "decision_high", "net_gap", "floor_gap", "floor",
    "separation", "reported", "probes", "unreachable", "caveats",
)

_SYMBOL = {"multiply": "x", "add": "", "set": "="}

_MISS_TYPES: tuple | None = None


def _errors() -> tuple:
    """`(DecisionError, ReplayMiss, FrameworkError)`, resolved late.

    `integrations.common` imports `counterfactual`, which this module
    imports, so the import cannot sit at the top of the file; and
    `import tradefloor` must not import the integrations subpackage.
    """
    global _MISS_TYPES
    if _MISS_TYPES is None:
        from .integrations.common import (DecisionError, FrameworkError,
                                          ReplayMiss)

        _MISS_TYPES = (DecisionError, ReplayMiss, FrameworkError)
    return _MISS_TYPES


# ---------------------------------------------------------------------------
# Targets: which engine field a target writes, and how a value is shown
# ---------------------------------------------------------------------------

_FIELD_OF: dict[str, str | None] = {}


def macro_field_of(target: Target) -> str | None:
    """The engine macro field this target writes, or None for a column.

    Derived rather than listed. The registry in `interventions.py` keeps
    the field name inside each target's read and write closures, and a
    second table here naming the same fields would be one more thing the
    registry could drift away from. So the target is written once on a
    scratch engine, and the one key of ``Engine.macro_fields`` that moved
    is the field. ``market.liquidity`` moves none, and is driven through a
    scenario instead. The answer is cached per process.
    """
    if target.name in _FIELD_OF:
        return _FIELD_OF[target.name]
    engine = Engine(seed=0, universe=random_instruments(2, seed=0))
    before = dict(engine.macro_fields)
    current = target.read(engine)
    if isinstance(current, tuple):
        nudged = tuple(v * 1.01 for v in current)
    elif isinstance(current, (int, float)):
        nudged = current + (abs(current) * 0.01 or 0.01)
    else:
        nudged = next(c for c in CYCLES if c != current)
    target.write(engine, nudged)
    after = engine.macro_fields
    moved = sorted(k for k in after if after[k] != before.get(k))
    if len(moved) > 1:
        raise ValidationError(
            f"writing {target.name} moved {len(moved)} macro fields "
            f"({', '.join(moved)}), so the target cannot be pinned through "
            "one of them. The registry has changed shape since this module "
            "was written.")
    _FIELD_OF[target.name] = moved[0] if moved else None
    return _FIELD_OF[target.name]


def _shown(target: Target, absolute: Any) -> str:
    return target.show(summarise(absolute))


def _label(target: str, operation: str, value: float) -> str:
    if operation == "add":
        return f"{target} {value:+.6g}"
    return f"{target} {_SYMBOL[operation]}{value:.6g}"


def _bracket(bracket: Any) -> tuple[float, float]:
    try:
        low, high = bracket
        low, high = float(low), float(high)
    except (TypeError, ValueError):
        raise ValidationError(
            f"bracket must be two numbers (low, high), got {bracket!r}."
        ) from None
    for value in (low, high):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValidationError(f"bracket must be finite, got {bracket!r}.")
    if not low < high:
        raise ValidationError(
            f"bracket must satisfy low < high, got {bracket!r}. Bisection "
            "halves the distance between two different values.")
    return low, high


def _reference(agent: Any) -> str | None:
    """The agent's own citation, for the manifests, when it has one."""
    info = getattr(agent, "info", None)
    reference = getattr(info, "reference", None)
    return reference() if callable(reference) else None


def _describe(agent: Any) -> str:
    """One line naming the agent, computed from what it publishes.

    A replaying agent is described by its recording's ``meta``, because a
    replay's provenance is the recording's: the framework version and the
    model that produced the answers, not whatever is installed today.
    """
    mode = getattr(agent, "mode", None)
    if mode == "replay":
        transcript = getattr(agent, "transcript", None)
        meta = dict(getattr(transcript, "meta", None) or {})
        parts = [" ".join(str(meta[k]) for k in ("framework",
                                                  "framework_version")
                          if meta.get(k)),
                 " ".join(str(meta[k]) for k in ("provider", "model")
                          if meta.get(k))]
        named = ", ".join(p for p in parts if p) or type(agent).__name__
        return (f"{named}, replaying a recording of "
                f"{len(transcript) if transcript is not None else 0} "
                "interactions")
    reference = _reference(agent) or type(agent).__name__
    if mode == "live":
        return f"{reference}, live"
    return reference


# ---------------------------------------------------------------------------
# Decisions: reading one off a trace, and comparing two
# ---------------------------------------------------------------------------


def _decision_row(arm: World, first: int) -> tuple[dict | None, dict | None]:
    """The trace row the agent decided on during the day that began at step
    ``first``, and its decision.

    Every framework adapter's ``decision()`` returns its LAST decision on
    every step, so a row on a non-decision step carries the previous
    decision. The decision that belongs to a row is the one stamped with
    that row's step. A row marked unusable is the decision row too: the
    agent was asked there and produced nothing executable.
    """
    for row in arm.trace[first:first + arm.steps_per_day]:
        if row.get("unusable"):
            return row, None
        decision = row.get("decision")
        if isinstance(decision, dict) and decision.get("step") == row["step"]:
            return row, decision
    return None, None


def _parsed(decision: dict) -> Any:
    """The decision through the shared validator, as a Decision object."""
    from .integrations.common import DecisionError, parse_decision

    try:
        return parse_decision({"actions": decision["actions"],
                               "rationale": decision.get("rationale", "")})
    except (KeyError, TypeError, DecisionError) as exc:
        raise ValidationError(
            f"cannot read a decision shape out of {decision!r}: {exc}. flip "
            "compares decisions by their actions, as resample does, and reads "
            "them from a framework adapter's decision(). Wrap a native policy "
            "in tradefloor.integrations.callable.CallableAgentAdapter."
        ) from None


def _shape_of(decision: dict) -> tuple:
    return _shape(_parsed(decision))


def _net_of(decision: dict) -> float:
    return _net(_parsed(decision))


def describe_shape(shape: tuple | None) -> str:
    """A shape as one readable line: ``BUY 100 NOVA; SELL 50 HELX``."""
    if shape is None:
        return "-"
    if not shape:
        return "no change"
    return "; ".join(f"{side} {quantity:,.0f} {symbol}"
                     if side != "HOLD" else f"HOLD {symbol}"
                     for symbol, side, quantity in shape)


def _entry_prompt(arm: World, step: int) -> str | None:
    """The input the agent recorded at ``step``, canonical, or None."""
    from .integrations.common import jsonable

    for entry in getattr(arm.agent, "record", None) or ():
        if entry.get("step") == step:
            return json.dumps(jsonable(entry.get("prompt")), sort_keys=True,
                              default=str)
    return None


# ---------------------------------------------------------------------------
# One probe
# ---------------------------------------------------------------------------


class Probe:
    """One fork, one intervention value, one day, one outcome."""

    __slots__ = ("value", "seen", "label", "step", "outcome", "decision",
                 "shape", "detail")

    def __init__(self, *, value: float, seen: Any, label: str,
                 step: int | None, outcome: str, decision: dict | None,
                 shape: tuple | None, detail: str) -> None:
        #: The intervention value, in the operation's own units.
        self.value = value
        #: The absolute level written: the column total for a column target.
        self.seen = seen
        self.label = label
        #: The step the decision was read at, or None when none was.
        self.step = step
        self.outcome = outcome
        self.decision = decision
        self.shape = shape
        #: The adapter's own message, for a probe that did not decide.
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value, "seen": self.seen, "label": self.label,
            "step": self.step, "outcome": self.outcome,
            "decision": self.decision,
            "shape": None if self.shape is None
            else [list(action) for action in self.shape],
            "detail": self.detail,
        }

    def __repr__(self) -> str:
        return (f"Probe({self.label!r}, {self.outcome}, "
                f"{describe_shape(self.shape)})")


def _probe(base: World, target: Target, field: str | None, operation: str,
           value: float, arm: World | None = None) -> tuple[Probe, World]:
    """Run one probe on a fresh fork of ``base`` and read its decision.

    ``arm`` is the fork to use when the caller already took one (the two
    bracket ends are forked together so :func:`agree` can compare them
    before either is written to); otherwise a fresh fork is taken here.
    """
    label = _label(target.name, operation, value)
    if arm is None:
        arm, = base.fork(label)
    current = target.read(arm.engine)
    absolute = apply_operation(operation, current, value)
    if field is not None:
        arm.intervene(**{field: absolute})
    else:
        arm.apply(Scenario(name=label).shock(
            target.name, operation=operation, value=value, at=0))

    first = arm.step
    DecisionError, ReplayMiss, _ = _errors()
    try:
        arm.run(days=1)
    except (DecisionError, ReplayMiss) as exc:
        replaying = (isinstance(exc, ReplayMiss)
                     or getattr(arm.agent, "mode", None) == "replay")
        return Probe(value=value, seen=summarise(absolute), label=label,
                     step=None,
                     outcome="unreachable" if replaying else "unusable",
                     decision=None, shape=None,
                     detail=f"{type(exc).__name__}: {exc}"), arm

    row, decision = _decision_row(arm, first)
    if row is None:
        every = getattr(arm.agent, "every", None)
        cadence = (f" It decides every {every} steps." if every else "")
        raise ValidationError(
            f"the agent took no decision on day {arm.day - 1} (steps {first} "
            f"to {first + arm.steps_per_day - 1}), so there is nothing to "
            f"compare.{cadence} flip reads the decision the agent took on the "
            "probe day from the trace, as a framework adapter records it, "
            "stamped with the step it was taken at.")
    if decision is None:
        return Probe(value=value, seen=summarise(absolute), label=label,
                     step=row["step"], outcome="unusable", decision=None,
                     shape=None, detail=str(row["unusable"])), arm
    return Probe(value=value, seen=summarise(absolute), label=label,
                 step=row["step"], outcome="decided", decision=decision,
                 shape=_shape_of(decision), detail=""), arm


# ---------------------------------------------------------------------------
# The result of a search
# ---------------------------------------------------------------------------


class Flip:
    """One boundary: the narrowest bracket that closed on two decisions.

    ``low`` and ``high`` are the closed bracket in the operation's own
    units, ``seen_low`` and ``seen_high`` the absolute levels the two arms
    opened the day with, and ``decision_low`` and ``decision_high`` the two
    decisions as the trace recorded them, rationale included. ``floor`` is
    :meth:`tradefloor.Resample.as_dict` for the two bracketing arms, and
    ``manifests`` are the two arms' :class:`tradefloor.RunManifest`, each
    replay-verified as :meth:`World.manifest` verifies it.
    """

    __slots__ = ("target", "operation", "day", "step", "scenario", "low",
                 "high", "seen_low", "seen_high", "decision_low",
                 "decision_high", "net_gap", "floor_gap", "separation",
                 "floor", "manifests", "agreement", "probes", "caveats")

    def __init__(self, *, target: str, operation: str, day: int, step: int,
                 scenario: str, low: float, high: float, seen_low: Any,
                 seen_high: Any, decision_low: dict, decision_high: dict,
                 net_gap: float, floor_gap: float, separation: float | None,
                 floor: dict | None, manifests: tuple, agreement: Agreement,
                 probes: list, caveats: list[str]) -> None:
        self.target = target
        self.operation = operation
        self.day = day
        self.step = step
        self.scenario = scenario
        self.low = low
        self.high = high
        self.seen_low = seen_low
        self.seen_high = seen_high
        self.decision_low = decision_low
        self.decision_high = decision_high
        #: Net of the RECORDED high decision minus net of the recorded low
        #: one, in actions: the gap between the two decisions the bracket
        #: closed on, one draw each.
        self.net_gap = net_gap
        #: The gap between the two arms' RESAMPLED mean net answers, the
        #: number ``separation`` and the floor are computed against. It
        #: equals ``net_gap`` only for a deterministic agent.
        self.floor_gap = floor_gap
        #: ``floor_gap`` in within-arm standard deviations, or None where
        #: neither arm varied.
        self.separation = separation
        self.floor = floor
        self.manifests = manifests
        self.agreement = agreement
        self.probes = probes
        self.caveats = caveats

    @property
    def shape_low(self) -> tuple:
        return _shape_of(self.decision_low)

    @property
    def shape_high(self) -> tuple:
        return _shape_of(self.decision_high)

    def as_dict(self) -> dict[str, Any]:
        """JSON-able, manifests included, for an artifact."""
        return {
            "target": self.target, "operation": self.operation,
            "day": self.day, "step": self.step, "scenario": self.scenario,
            "low": self.low, "high": self.high,
            "seen_low": self.seen_low, "seen_high": self.seen_high,
            "decision_low": self.decision_low,
            "decision_high": self.decision_high,
            "net_gap": self.net_gap, "floor_gap": self.floor_gap,
            "separation": self.separation, "floor": self.floor,
            "manifests": [json.loads(m.to_json()) for m in self.manifests],
            "agreement": self.agreement.as_dict(),
            "probes": [p.as_dict() for p in self.probes],
            "caveats": list(self.caveats),
        }

    def render(self) -> str:
        target = TARGETS[self.target]
        lines = [
            f"  {self.target} ({self.operation}) flips between "
            f"{self.low:g} and {self.high:g} on day {self.day}",
            f"  {'seen by the market':<22} {target.show(self.seen_low)}  ->  "
            f"{target.show(self.seen_high)}",
            f"  {'decision below':<22} {describe_shape(self.shape_low)}",
            f"  {'decision above':<22} {describe_shape(self.shape_high)}",
            f"  {'net gap, recorded':<22} {self.net_gap:+.2f}",
            f"  {'net gap, resampled':<22} {self.floor_gap:+.2f} between "
            "the arms' mean answers",
            f"  {'separation':<22} "
            + ("zero floor" if self.separation is None
               else f"{self.separation:.2f} within-arm stdevs"),
            f"  {'probes':<22} {len(self.probes)}",
        ]
        for caveat in self.caveats:
            lines.append(f"  - {caveat}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"Flip({self.target!r}, {self.operation}, "
                f"{self.low:g}..{self.high:g}, day={self.day})")


class Search:
    """Everything one call to :func:`search` did, whatever it found.

    ``status`` is one of :data:`STATUSES`. ``flip`` is set only under
    ``"flip"``. ``probes`` holds every probe in the order it ran, so an
    unreachable or unusable one is named rather than skipped.
    """

    __slots__ = ("scenario", "target", "operation", "day", "step",
                 "bracket", "probes", "status", "low", "high", "seen_low",
                 "seen_high", "decision_low", "decision_high", "floor",
                 "net_gap", "floor_gap", "separation", "agreement", "flip",
                 "caveats")

    def __init__(self, *, scenario: str, target: str, operation: str,
                 day: int, bracket: tuple[float, float]) -> None:
        self.scenario = scenario
        self.target = target
        self.operation = operation
        self.day = day
        self.bracket = bracket
        self.step: int | None = None
        self.probes: list[Probe] = []
        self.status = "refused"
        self.low: float | None = None
        self.high: float | None = None
        self.seen_low: Any = None
        self.seen_high: Any = None
        self.decision_low: dict | None = None
        self.decision_high: dict | None = None
        self.floor: dict | None = None
        #: Between the two recorded decisions; see :class:`Flip`.
        self.net_gap: float | None = None
        #: Between the two arms' resampled mean answers; see :class:`Flip`.
        self.floor_gap: float | None = None
        self.separation: float | None = None
        self.agreement: Agreement | None = None
        self.flip: Flip | None = None
        self.caveats: list[str] = []

    @property
    def reported(self) -> bool:
        return self.flip is not None

    @property
    def unreachable(self) -> list[Probe]:
        return [p for p in self.probes if p.outcome == "unreachable"]

    @property
    def shape_low(self) -> tuple | None:
        if self.decision_low is None:
            return None
        return _shape_of(self.decision_low)

    @property
    def shape_high(self) -> tuple | None:
        if self.decision_high is None:
            return None
        return _shape_of(self.decision_high)

    @property
    def floor_net(self) -> float | None:
        """The larger within-arm stdev, for a floor that was measured.

        None where no floor was measured, including a resample that ran
        and was gated for an arm with fewer than two parsed answers: its
        numbers are still in :attr:`floor`, and they describe the answers
        that parsed rather than the agent's spread.
        """
        if self.floor is None or self.status == "floor unmeasurable":
            return None
        return self.floor["separation"]["floor_net"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario, "target": self.target,
            "operation": self.operation, "day": self.day, "step": self.step,
            "bracket": list(self.bracket), "status": self.status,
            "low": self.low, "high": self.high,
            "seen_low": self.seen_low, "seen_high": self.seen_high,
            "decision_low": self.decision_low,
            "decision_high": self.decision_high,
            "net_gap": self.net_gap, "floor_gap": self.floor_gap,
            "separation": self.separation, "floor": self.floor,
            "agreement": (None if self.agreement is None
                          else self.agreement.as_dict()),
            "reported": self.reported,
            "flip": None if self.flip is None else self.flip.as_dict(),
            "probes": [p.as_dict() for p in self.probes],
            "caveats": list(self.caveats),
        }

    def row(self) -> dict[str, Any]:
        """One :meth:`BoundaryMap.table` row."""
        return {
            "scenario": self.scenario, "target": self.target,
            "operation": self.operation, "day": self.day, "step": self.step,
            "status": self.status, "low": self.low, "high": self.high,
            "seen_low": (None if self.seen_low is None
                         else float(self.seen_low)),
            "seen_high": (None if self.seen_high is None
                          else float(self.seen_high)),
            "decision_low": describe_shape(self.shape_low),
            "decision_high": describe_shape(self.shape_high),
            "net_gap": self.net_gap, "floor_gap": self.floor_gap,
            "floor": self.floor_net,
            "separation": self.separation, "reported": self.reported,
            "probes": len(self.probes), "unreachable": len(self.unreachable),
            "caveats": list(self.caveats),
        }

    def render(self) -> str:
        bracket = (f"{self.low:g}..{self.high:g}" if self.low is not None
                   else f"{self.bracket[0]:g}..{self.bracket[1]:g}")
        floor = ("-" if self.floor_net is None
                 else f"{self.floor_net:.2f}")
        head = (f"  {self.scenario:<22} {self.target:<24} {self.status:<18} "
                f"{bracket:<22} {describe_shape(self.shape_low)} -> "
                f"{describe_shape(self.shape_high)}  floor {floor}")
        out = [head]
        for caveat in self.caveats:
            out.append(f"      - {caveat}")
        return "\n".join(out)

    def __repr__(self) -> str:
        return (f"Search({self.scenario!r}, {self.target!r}, "
                f"{self.status!r}, {len(self.probes)} probes)")


# ---------------------------------------------------------------------------
# The search
# ---------------------------------------------------------------------------


def _resolved(target: Target, seen_low: Any, seen_high: Any) -> bool:
    """True when the two bracket ends print alike in the target's format."""
    return _shown(target, seen_low) == _shown(target, seen_high)


def _shadowing(base: World, target: Target, day: int) -> Intervention | None:
    """The scenario intervention that writes ``target`` on ``day``, if any.

    Two kinds of write: a firing of an intervention active that day, and
    the release that puts a non-restoring target's level back the day after
    its last window closes. Either would land on top of a probe.
    """
    for item in base.applied:
        if item.target != target.name:
            continue
        if item.active_on(day):
            return item
        if (not target.restores and item.shape in ("hold", "ramp")
                and item.last_day is not None and day == item.last_day + 1):
            return item
    return None


def _pinned_today(base: World, field: str | None, day: int) -> str | None:
    """Why a pin on ``field`` beginning on ``day`` would be refused.

    A world's construction pins hold from day 0, and each earlier
    :meth:`World.intervene` is a step pin on its own day. A probe is
    written through the same mechanism, and two pins on one field
    beginning on the same day are one path stated twice, which
    :class:`Scenario` refuses when the day runs. Caught here, before a
    fork is taken, with the pin named.
    """
    if field is None:
        return None
    if day == 0 and field in base.pins:
        return f"the construction pin {field}={base.pins[field]!r}"
    for entry in base.interventions:
        if entry["day"] == day and field in entry["fields"]:
            return (f"an intervene() on day {day} setting "
                    f"{field}={entry['fields'][field]!r}")
    return None


def _measure_floor(low_arm: World, high_arm: World, step: int,
                   floor: Resample | None) -> tuple[Resample | None, str]:
    """The floor from resample, or the reason there is none."""
    if floor is not None:
        return floor, ""
    try:
        return resample(low_arm, high_arm, at=step, n=FLOOR_CALLS), ""
    except ValidationError as exc:
        return None, str(exc)


def _parsed_counts(measured: Resample) -> str:
    left, right = measured.control, measured.treatment
    noise = measured.noise
    return (f"the low arm parsed {noise[left]['parsed']} of "
            f"{noise[left]['samples']} calls into {noise[left]['distinct']} "
            f"distinct answer(s) and the high arm {noise[right]['parsed']} "
            f"of {noise[right]['samples']} into {noise[right]['distinct']}")


def _refusal_caveat(measured: Resample) -> list[str]:
    """The refusals inside the floor, when there were any."""
    noise = measured.noise
    refusals = (noise[measured.control]["refusals"]
                + noise[measured.treatment]["refusals"])
    if not refusals:
        return []
    return [f"{refusals} of the {2 * measured.n} floor calls returned no "
            "executable decision and were counted as refusals, not "
            "retried; the means and spreads rest on the answers that "
            "parsed"]


def _unmeasured(measured: Resample) -> str | None:
    """Why the floor is unmeasured, or None when both arms carry a spread.

    :func:`resample` averages an arm over the answers that parsed, so an
    arm whose re-asks all refused comes back with a mean of zero and a
    spread of zero, the same numbers a perfectly stable arm reports. One
    parsed answer has a spread of zero by construction as well. Neither
    is a floor of zero; both are no floor, and a flip must not be
    reported over them.
    """
    for label in (measured.control, measured.treatment):
        stats = measured.noise[label]
        if stats["parsed"] < 2:
            return (f"arm {label!r} returned {stats['parsed']} executable "
                    f"decision(s) in {stats['samples']} calls "
                    f"({stats['refusals']} refused), and a spread needs at "
                    "least 2")
    return None


def _floor_caveats(measured: Resample, supplied: bool) -> list[str]:
    """What a measured floor says, computed from the resample it came
    from. Called once :func:`_unmeasured` has passed both arms."""
    out = []
    left, right = measured.control, measured.treatment
    sep = measured.separation["net"]
    gap = measured.separation["gap_net"]
    floor = measured.separation["floor_net"]
    if supplied:
        out.append(
            f"the floor was supplied by the caller: {measured.n} calls per "
            f"arm at step {measured.at} on arms {left!r} and {right!r}, and "
            "was not measured on this search's bracketing arms")
    if measured.identical_inputs:
        out.append(
            "the floor's two inputs were byte-identical, so the gap of "
            f"{gap:+.2f} between its arms' mean answers is agent noise and "
            "nothing else, and no flip is reported over it")
    parsed = _parsed_counts(measured)
    if sep is None:
        out.append(
            f"the floor is zero: {parsed}, with no spread in either, so the "
            f"gap of {gap:+.2f} between the arms' mean answers stands "
            "against no within-arm spread at all")
    else:
        verdict = ("and the flip clears it" if sep > 1.0
                   else "and the flip sits inside the agent's own spread, so "
                        "it is not reported")
        out.append(
            f"the gap of {gap:+.2f} between the arms' mean answers is "
            f"{sep:.2f} times the larger within-arm stdev of {floor:.2f}; "
            f"{parsed}, {verdict}")
    return out + _refusal_caveat(measured)


def _clears(measured: Resample) -> bool:
    """The floor rule, on a floor :func:`_unmeasured` has passed."""
    if measured.identical_inputs:
        return False
    sep = measured.separation["net"]
    if sep is None:
        return measured.separation["gap_net"] != 0.0
    return sep > 1.0


def _context_caveats(base: World, day: int, step: int | None) -> list[str]:
    where = f"day {day}" + ("" if step is None else f", step {step}")
    return [
        f"one day of one market: seed {base.seed}, {len(base.universe)} "
        f"instruments (universe {base.universe_fingerprint[:12]}), {where}; "
        "another day or another seed is another measurement",
        f"agent: {_describe(base.agent)}",
    ]


def _failed_probe(result: Search, probe: Probe) -> Search:
    result.status = probe.outcome
    result.caveats = [f"probe {probe.label!r} was {probe.outcome}: "
                      f"{probe.detail}"]
    return result


def search(world: World, target: str, *, at: int | None = None,
           operation: str = "multiply", bracket: tuple[float, float],
           steps: int = 8, floor: Resample | None = None) -> Search:
    """Bisect one target on one day, and report everything the search did.

    The record behind :func:`flip`. Where ``flip`` returns ``None``, this
    says why: the bracket ends agreed, a probe was unreachable or unusable,
    the floor could not be measured, or the candidate fell inside it. See
    the module docstring for the method.

    ``at`` is the day to fork on. Today by default; a later day runs a
    private fork forward to it, and the caller's world is left where it
    was. An earlier day is refused, because a world cannot rewind.
    ``bracket`` is two values in the operation's own units, and both ends
    are checked against the target's domain before any probe runs.
    ``floor`` is a :class:`tradefloor.Resample` to use in place of one
    measured here, and the caveats say so when it is.
    """
    target_obj = resolve(target)
    if not target_obj.numeric:
        raise ValidationError(
            f"{target_obj.name} takes a {target_obj.units}, and a name has "
            "no midpoint: bisection needs a numeric target.")
    if operation not in OPERATIONS:
        raise ValidationError(
            f"unknown operation {operation!r}; the vocabulary is "
            f"{', '.join(OPERATIONS)}.")
    low, high = _bracket(bracket)
    target_obj.check(operation, low)
    target_obj.check(operation, high)
    if not isinstance(steps, int) or isinstance(steps, bool) or steps < 0:
        raise ValidationError(
            f"steps must be a whole number >= 0, got {steps!r}.")
    if floor is not None and not isinstance(floor, Resample):
        raise ValidationError(
            f"floor takes a tradefloor.Resample, got {type(floor).__name__}.")
    day = world.day if at is None else int(at)
    if day < world.day:
        raise ValidationError(
            f"cannot fork at day {day}: the world is at day {world.day} and "
            "cannot rewind. Resume a checkpoint taken earlier, or run a "
            "fresh world to the day wanted.")

    result = Search(scenario=world.label or "base", target=target_obj.name,
                    operation=operation, day=day, bracket=(low, high))
    DecisionError, ReplayMiss, _ = _errors()

    base, = world.fork(f"boundary base day {day}")
    if day > world.day:
        try:
            base.run(days=day - world.day)
        except (DecisionError, ReplayMiss) as exc:
            result.status = "unreachable"
            result.caveats = [
                f"the agent could not be run from day {world.day} to day "
                f"{day}: {type(exc).__name__}: {exc}"]
            return result

    field = macro_field_of(target_obj)
    current = target_obj.read(base.engine)
    seen = {}
    for value in (low, high):
        absolute = apply_operation(operation, current, value)
        reason = target_obj.outside_domain(absolute)
        if reason is not None:
            raise ValidationError(
                f"bracket end {value:g} would write {reason} to "
                f"{target_obj.name}: {operation} {value:g} applied to "
                f"{_shown(target_obj, current)} gives "
                f"{_shown(target_obj, absolute)}. Choose a bracket inside "
                "the target's domain.")
        seen[value] = absolute
    if summarise(seen[low]) == summarise(seen[high]):
        raise ValidationError(
            f"the bracket ({low:g}, {high:g}) maps to one level, "
            f"{_shown(target_obj, seen[low])}, at both ends: {operation} on "
            f"{target_obj.name} from {_shown(target_obj, current)} moves "
            "nothing. Use `set` or `add` for a target at zero.")

    pinned = _pinned_today(base, field, day)
    if pinned is not None:
        raise ValidationError(
            f"cannot probe {target_obj.name} on day {day}: the world already "
            f"pins {field} from that day ({pinned}), and a probe is a second "
            "pin on the same field beginning the same day, which the "
            "scenario builder refuses as one path stated twice. Probe a "
            "later day, or run the world one day first.")

    shadow = _shadowing(base, target_obj, day)
    if shadow is not None:
        result.status = "shadowed"
        result.caveats = [
            f"the scenario writes {target_obj.name} on day {day} "
            f"({shadow.describe().strip()}), so a probe would be written "
            "over or stacked on; the target is not searched on that day"]
        return result

    low_arm, high_arm = base.fork(_label(target_obj.name, operation, low),
                                  _label(target_obj.name, operation, high))
    result.agreement = agree(low_arm, high_arm)
    if not result.agreement.identical:
        raise ValidationError(
            "the two bracketing arms did not start identical, so the search "
            f"has no control: {result.agreement.differences}")

    arms: dict[float, World] = {}
    probes: dict[float, Probe] = {}
    for value, arm in ((low, low_arm), (high, high_arm)):
        probes[value], arms[value] = _probe(base, target_obj, field,
                                            operation, value, arm)
        result.probes.append(probes[value])
        if probes[value].outcome != "decided":
            return _failed_probe(result, probes[value])
    result.step = probes[low].step

    if probes[low].shape == probes[high].shape:
        result.status = "no flip"
        result.low, result.high = low, high
        result.seen_low, result.seen_high = probes[low].seen, probes[high].seen
        result.decision_low = probes[low].decision
        result.decision_high = probes[high].decision
        result.caveats = [
            f"both bracket ends, {_shown(target_obj, seen[low])} and "
            f"{_shown(target_obj, seen[high])}, drew the same decision: no "
            "boundary lies inside the bracket on this day"]
        left = _entry_prompt(low_arm, result.step)
        right = _entry_prompt(high_arm, result.step)
        if left is not None and left == right:
            result.caveats.append(
                "the two bracket ends sent the agent byte-identical inputs: "
                f"a move on {target_obj.name} does not reach what this agent "
                "is shown by the first decision of the day, so no bracket on "
                "it can flip the agent there")
        result.caveats += _context_caveats(base, day, result.step)
        return result

    halvings = 0
    while halvings < steps and not _resolved(target_obj, probes[low].seen,
                                             probes[high].seen):
        mid = (low + high) / 2.0
        probe, arm = _probe(base, target_obj, field, operation, mid)
        result.probes.append(probe)
        halvings += 1
        if probe.outcome != "decided":
            return _failed_probe(result, probe)
        probes[mid], arms[mid] = probe, arm
        if probe.shape == probes[low].shape:
            low = mid
        else:
            high = mid

    result.low, result.high = low, high
    result.seen_low, result.seen_high = probes[low].seen, probes[high].seen
    result.decision_low = probes[low].decision
    result.decision_high = probes[high].decision
    result.net_gap = (_net_of(probes[high].decision)
                      - _net_of(probes[low].decision))
    distinct = {p.shape for p in result.probes}

    caveats = []
    if _resolved(target_obj, probes[low].seen, probes[high].seen):
        caveats.append(
            f"{halvings} halving(s) closed the bracket to ({low:g}, "
            f"{high:g}), whose ends both print as "
            f"{_shown(target_obj, probes[low].seen)} in the target's own "
            "format: the bracket is at the target's resolution")
    else:
        caveats.append(
            f"{halvings} halving(s) closed the bracket to ({low:g}, "
            f"{high:g}), which the target shows as "
            f"{_shown(target_obj, probes[low].seen)} and "
            f"{_shown(target_obj, probes[high].seen)}: the ends still print "
            "apart, so the boundary lies inside a bracket wider than the "
            "target's resolution and more steps would narrow it")
    if len(distinct) > 2:
        caveats.append(
            f"{len(distinct)} distinct decisions across {len(result.probes)} "
            "probes: the closed bracket holds one change of decision and the "
            "others are unreported")
    # Two verdicts need no floor, and a floor costs a live agent
    # 2 * FLOOR_CALLS calls, so they come first. Identical inputs mean
    # the intervention never reached the agent; equal nets mean the
    # change is one the floor's measure cannot see.
    left = _entry_prompt(arms[low], result.step)
    right = _entry_prompt(arms[high], result.step)
    if left is not None and left == right:
        result.status = "unseen"
        result.caveats = [
            "the two bracketing arms sent the agent byte-identical inputs "
            f"at step {result.step}: the intervention had not reached the "
            "agent, so the difference between the recorded decisions is "
            "agent noise; no flip is reported and no floor was asked for"
        ] + caveats + _context_caveats(base, day, result.step)
        return result
    if result.net_gap == 0.0:
        result.status = "same net"
        result.caveats = [
            "the two decisions differ in shape and agree in net (buys minus "
            "sells): a change of quantity or of symbol, which a floor "
            "measured on net cannot see; no flip is reported and no floor "
            "was asked for"
        ] + caveats + _context_caveats(base, day, result.step)
        return result

    measured, reason = _measure_floor(arms[low], arms[high], result.step,
                                      floor)
    if measured is None:
        result.status = "floor unmeasurable"
        result.caveats = [
            "the bracket closed and no flip is reported: the agent's floor "
            f"could not be measured. {reason}"] + caveats
        result.caveats += _context_caveats(base, day, result.step)
        return result

    # The resample is carried whatever the gate says, so a reader can see
    # what came back. The three scalars a table sorts on are set only
    # once the gate has passed: a gap read off an arm that parsed nothing
    # is one arm's answer minus the absence of one, and a published
    # column cannot carry the qualifier the caveat does.
    result.floor = measured.as_dict()
    unmeasured = _unmeasured(measured)
    if unmeasured is not None:
        result.status = "floor unmeasurable"
        result.caveats = [
            "the bracket closed and no flip is reported: the agent's floor "
            f"is unmeasured. {unmeasured}; {_parsed_counts(measured)}"
        ] + _refusal_caveat(measured) + caveats
        result.caveats += _context_caveats(base, day, result.step)
        return result
    result.floor_gap = measured.separation["gap_net"]
    result.separation = measured.separation["net"]

    caveats = _floor_caveats(measured, floor is not None) + caveats
    if not _clears(measured):
        result.status = "inside floor"
        result.caveats = caveats + _context_caveats(base, day, result.step)
        return result

    caveats.append(
        "the decisions are recorded and the reason is not: the flip locates "
        "a change of decision and does not explain it")
    caveats += _context_caveats(base, day, result.step)
    reference = _reference(base.agent)
    manifests = tuple(
        arms[value].manifest(strategy=reference, label=arms[value].label)
        for value in (low, high))
    result.status = "flip"
    result.caveats = caveats
    result.flip = Flip(
        target=target_obj.name, operation=operation, day=day,
        step=result.step, scenario=result.scenario, low=low, high=high,
        seen_low=probes[low].seen, seen_high=probes[high].seen,
        decision_low=probes[low].decision,
        decision_high=probes[high].decision, net_gap=result.net_gap,
        floor_gap=result.floor_gap, separation=result.separation,
        floor=result.floor, manifests=manifests,
        agreement=result.agreement, probes=list(result.probes),
        caveats=list(caveats))
    return result


def flip(world: World, target: str, *, at: int | None = None,
         operation: str = "multiply", bracket: tuple[float, float],
         steps: int = 8, floor: Resample | None = None) -> Flip | None:
    """The smallest move on ``target`` that flips the agent, or None.

    A :class:`Flip` comes back only when the bracket closed on two
    different decisions AND the gap between them clears the agent's own
    floor. ``None`` covers everything else: the bracket ends agreed, a
    probe was unreachable or unusable, the floor could not be measured, or
    the candidate fell inside it. :func:`search` returns the same search
    with its status, its probes and its caveats, for a reader who needs to
    know which.

    Arguments are :func:`search`'s.
    """
    return search(world, target, at=at, operation=operation, bracket=bracket,
                  steps=steps, floor=floor).flip


# ---------------------------------------------------------------------------
# The map
# ---------------------------------------------------------------------------


class BoundaryMap:
    """Every search :func:`map_boundaries` ran, and the flips among them."""

    __slots__ = ("searches", "seed", "day", "universe_fingerprint",
                 "instruments", "caveats")

    def __init__(self, searches: Sequence[Search], *, seed: int, day: int,
                 universe_fingerprint: str, instruments: int) -> None:
        self.searches = list(searches)
        self.seed = seed
        self.day = day
        self.universe_fingerprint = universe_fingerprint
        self.instruments = instruments
        self.caveats = self._caveats()

    @property
    def flips(self) -> list[Flip]:
        """The reported boundaries, in search order."""
        return [s.flip for s in self.searches if s.flip is not None]

    @property
    def unreachable(self) -> list[Search]:
        return [s for s in self.searches if s.status == "unreachable"]

    def counts(self) -> dict[str, int]:
        out = {status: 0 for status in STATUSES}
        for item in self.searches:
            out[item.status] += 1
        return {k: v for k, v in out.items() if v}

    def _caveats(self) -> list[str]:
        counts = self.counts()
        total = len(self.searches)
        said = ", ".join(f"{n} {status}" for status, n in counts.items())
        out = [f"{counts.get('flip', 0)} flip(s) reported across {total} "
               f"search(es): {said or 'nothing searched'}"]
        unmeasured = [s for s in self.searches
                      if s.status == "floor unmeasurable"]
        if unmeasured:
            names = sorted({s.target for s in unmeasured})
            out.append(
                f"{len(unmeasured)} search(es) closed a bracket without a "
                f"floor ({', '.join(names)}): no flip is reported where the "
                "agent's own spread could not be measured")
        if self.unreachable:
            out.append(
                f"{len(self.unreachable)} search(es) hit a probe outside the "
                "agent's recording and are named rather than skipped")
        out.append(
            f"one market: seed {self.seed}, {self.instruments} instruments "
            f"(universe {self.universe_fingerprint[:12]}), from day "
            f"{self.day}; another seed is another map")
        return out

    def as_dict(self) -> dict[str, Any]:
        return {
            "seed": self.seed, "day": self.day,
            "universe_fingerprint": self.universe_fingerprint,
            "instruments": self.instruments,
            "counts": self.counts(),
            "searches": [s.as_dict() for s in self.searches],
            "caveats": list(self.caveats),
        }

    def rows(self) -> list[dict[str, Any]]:
        return [s.row() for s in self.searches]

    def table(self) -> Any:
        """One row per search, as a ``pyarrow.Table`` with :data:`COLUMNS`.

        ``pyarrow`` is imported here and only here; the library depends on
        nothing.
        """
        import pyarrow as pa

        types = {
            "scenario": pa.string(), "target": pa.string(),
            "operation": pa.string(), "day": pa.int64(), "step": pa.int64(),
            "status": pa.string(), "low": pa.float64(),
            "high": pa.float64(), "seen_low": pa.float64(),
            "seen_high": pa.float64(), "decision_low": pa.string(),
            "decision_high": pa.string(), "net_gap": pa.float64(),
            "floor_gap": pa.float64(), "floor": pa.float64(),
            "separation": pa.float64(),
            "reported": pa.bool_(), "probes": pa.int64(),
            "unreachable": pa.int64(), "caveats": pa.list_(pa.string()),
        }
        rows = self.rows()
        return pa.table({column: pa.array([row[column] for row in rows],
                                          type=types[column])
                         for column in COLUMNS})

    def render(self) -> str:
        out = [f"  boundary map: seed {self.seed}, {self.instruments} "
               f"instruments (universe {self.universe_fingerprint[:12]}), "
               f"from day {self.day}",
               f"  {'scenario':<22} {'target':<24} {'status':<18} "
               f"{'bracket':<22} decisions, low -> high"]
        for item in self.searches:
            out.append(item.render())
        for caveat in self.caveats:
            out.append(f"  - {caveat}")
        return "\n".join(out)

    def __repr__(self) -> str:
        return (f"BoundaryMap({len(self.searches)} searches, "
                f"{len(self.flips)} flips)")


def _scenario_of(item: Any) -> tuple[str, Scenario | None]:
    if item is None:
        return "", None
    if isinstance(item, Scenario):
        return item.name or "scenario", item
    if isinstance(item, str):
        return item, Scenario.load(item)
    raise ValidationError(
        f"scenarios holds a {type(item).__name__}; each entry is a shipped "
        "scenario's name, a tradefloor.Scenario, or None for the world as "
        "it stands.")


def _first_shock_day(scenario: Scenario) -> int:
    items = scenario.shocks or scenario.interventions
    return min(item.at for item in items)


def _refused(scenario: str, target: str, operation: str, day: int,
             bracket: Any, status: str, reason: str) -> Search:
    try:
        pair = _bracket(bracket)
    except ValidationError:
        pair = (float("nan"), float("nan"))
    out = Search(scenario=scenario, target=target, operation=operation,
                 day=day, bracket=pair)
    out.status = status
    out.caveats = [reason]
    return out


def map_boundaries(world: World, targets: Sequence[str] | None = None,
                   scenarios: Sequence[Any] | None = None,
                   **kw: Any) -> BoundaryMap:
    """One :func:`search` per target per scenario, as a :class:`BoundaryMap`.

    ``targets`` defaults to every numeric target in
    :data:`tradefloor.TARGETS`. ``scenarios`` defaults to
    :meth:`tradefloor.Scenario.available`; an entry may also be a
    :class:`tradefloor.Scenario` of the caller's, or ``None`` for the world
    as it stands. For each named scenario the world is forked, the scenario
    applied and run to its first shock day, and the searches fork from
    there, so the probe day is the day the first shock fires. Keyword
    arguments go to :func:`search`: ``operation``, ``steps`` and the
    required ``bracket``, which is one pair for every target or a mapping
    from target name to pair. ``at`` and ``floor`` are refused, because the
    map sets its own day per scenario and measures its own floor per
    search.

    A target the scenario writes on the probe day is reported as shadowed;
    a bracket a target cannot take is reported as refused; a scenario the
    agent's recording cannot reach is reported as unreachable for every
    target. A framework call that does not complete is reported as failed
    for that search and the map goes on, so one dropped connection does
    not lose the rest.
    """
    if "at" in kw:
        raise ValidationError(
            "map_boundaries forks each scenario at its first shock day; "
            "pass at= to flip() or search() for a single search on a day "
            "of your choosing.")
    if "floor" in kw:
        raise ValidationError(
            "a supplied floor is one decision point's measurement; the map "
            "measures its own floor on every search's bracketing arms.")
    if "bracket" not in kw:
        raise ValidationError(
            "map_boundaries needs bracket=(low, high) in the operation's "
            "units, or a mapping from target name to such a pair.")
    bracket = kw.pop("bracket")
    operation = kw.get("operation", "multiply")
    unknown = sorted(set(kw) - {"operation", "steps"})
    if unknown:
        raise ValidationError(
            f"unknown keyword(s) {', '.join(unknown)}; the map takes "
            "operation, steps and bracket.")

    if targets is None:
        targets = tuple(name for name, t in TARGETS.items() if t.numeric)
    targets = [resolve(name).name for name in targets]
    if isinstance(bracket, dict):
        missing = [name for name in targets if name not in bracket]
        if missing:
            raise ValidationError(
                f"bracket names no pair for {', '.join(missing)}.")
        brackets = {name: bracket[name] for name in targets}
    else:
        brackets = {name: bracket for name in targets}
    if scenarios is None:
        scenarios = Scenario.available()

    DecisionError, ReplayMiss, FrameworkError = _errors()
    searches: list[Search] = []
    for item in scenarios:
        name, scenario = _scenario_of(item)
        if scenario is None:
            arm, name = world, world.label or "base"
        else:
            arm, = world.fork(name)
            arm.apply(scenario)
            first = _first_shock_day(scenario)
            try:
                arm.run(days=first)
            except (DecisionError, ReplayMiss) as exc:
                reason = (f"{name} could not be run to its first shock day "
                          f"(day {world.day + first}): the agent stopped at "
                          f"day {arm.day}, step {arm.step}. "
                          f"{type(exc).__name__}: {exc}")
                for target in targets:
                    searches.append(_refused(
                        name, target, operation, world.day + first,
                        brackets[target], "unreachable", reason))
                continue
        for target in targets:
            try:
                found = search(arm, target, bracket=brackets[target], **kw)
            except FrameworkError as exc:
                found = _refused(name, target, operation, arm.day,
                                 brackets[target], "failed",
                                 f"the framework call did not complete: "
                                 f"{type(exc).__name__}: {exc}")
            except ValidationError as exc:
                found = _refused(name, target, operation, arm.day,
                                 brackets[target], "refused", str(exc))
            found.scenario = name
            searches.append(found)

    return BoundaryMap(searches, seed=world.seed, day=world.day,
                       universe_fingerprint=fingerprint_of(world.universe),
                       instruments=len(world.universe))
