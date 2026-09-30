"""A fixed battery of worlds, and a hash of what an agent orders on it.

Two ways to ask "did this agent change" already exist and both answer a
different question. :func:`tradefloor.rank` scores agents against many
seeds and says which one is better. :func:`tradefloor.counterfactual.compare`
forks one running world and says where two arms of ONE run came apart. This
module answers a third, narrower question: run the SAME fixed set of
worlds against an agent twice -- at two prompts, in two frameworks, on two
platforms -- and say whether it ordered the same things both times.

```python
battery = tf.fingerprint.battery()
before = tf.fingerprint.fingerprint(agent)
after = tf.fingerprint.fingerprint(agent_with_a_changed_prompt)
print(before.compare(after, floor=None).differing)
```

## What a fingerprint is a hash OF

Every cell in the battery is run as a plain, single-arm
:class:`~tradefloor.counterfactual.World`. At every step the agent's own
:meth:`decision` publish changes -- the same publish
:func:`tradefloor.counterfactual.compare` already reads to find where two
arms diverged -- the decision is canonicalised with
:func:`tradefloor.counterfactual._shape`: sorted by symbol, side and
quantity, and blind to everything else. :attr:`Fingerprint.digest` is
sha256 over that list, in cell then step order. It does not see a price, a
fill, a rationale or a rendered prompt, and the FinRobot fixture test in
`tests/test_fingerprint.py` demonstrates this by construction: the
extraction reads `trace[i]["decision"]` alone, so mutating every other
column of a copied trace before re-extracting produces the identical list.

`agent.decision()` is not optional here the way it is for
:class:`~tradefloor.counterfactual.World` in general. It has to publish the
`{"actions": [...], "rationale": ...}` shape every
:class:`~tradefloor.integrations.common.FrameworkAdapter` already produces,
because that is what :func:`~tradefloor.counterfactual._shape` reads.
:func:`fingerprint` raises naming the cell and step the first time a
publish does not fit, rather than hashing a fallback that would make two
unrelated agents with no `decision()` compare identical.

## What it cannot say

It cannot say which agent is BETTER, only whether they ordered the same
things: ranking is :func:`tradefloor.rank`. A sampled agent -- one call
into an LLM -- moves on its own between two identical asks, and
:meth:`Fingerprint.compare` never hides that: given no ``floor`` it reports
every difference and says plainly that none of them is separated from the
agent's own noise; given a
:class:`~tradefloor.counterfactual.Resample` floor, measured at one
decision point on the SAME agent, it reports how many of the differing
decisions are larger, in net or gross shares, than the agent's own
within-arm spread at that point. A `Resample` needs `reask()`, which only
a live, callable agent has -- a replayed transcript cannot answer a
question it was not asked, so a floor over one is not offered here.

## The battery

:data:`BATTERY_VERSION` names the cell set :func:`battery` returns by
default. A version is immutable: `battery(1)` builds the same six cells,
byte for byte, on every call and on every future release, because a
fingerprint is only comparable to another taken against the SAME worlds.
Extending the battery -- another cell, a different roster size, a longer
run -- is a new version, never an edit to `1`.

Each cell is one :class:`~tradefloor.counterfactual.World`, seeded and
rostered by :func:`tradefloor.Universe.random`, running one shipped
:class:`~tradefloor.Scenario` from day zero at the library's own six
steps a day. Version 1 has one cell for each of the six scenarios that
shipped when it was fixed: ``geopolitical_conflict``, ``liquidity_crisis``,
``oil_price_spike``, ``policy_regime_shift``, ``rate_shock`` and
``recession``. It pins those names, not the live directory.
`Scenario.available()` lists seven today, and ``curve_shock``, packaged
later, is not in version 1 and cannot be added to it.

Every version 1 cell runs 60 days, which is short for these scenarios.
Five of the six fire their main shock at day 50, so their cells see ten
days after it. ``policy_regime_shift`` moves credit at day 30, tariffs at
day 40 and inflation at day 50. Several shocks are still running when a
cell stops: ``liquidity_crisis`` holds its depth and volatility shock to
day 74, ``geopolitical_conflict`` its volatility to day 79, and
``recession`` its contraction to day 364. So a version 1 fingerprint
hashes how an agent reacts in the first ten days of a shock, and says
nothing about how it trades through the rest. A longer battery would be
version 2, which does not exist yet. The ``at:`` and ``duration:`` fields
under ``python/tradefloor/scenarios/`` give each day, and
`tests/test_fingerprint.py` checks the ones quoted here.

A :class:`Cell` is one un-forked :meth:`~tradefloor.counterfactual.World.run`
and cannot express a checkpoint-fork-intervene experiment -- two arms
sharing a history, one of them changed. That is a real gap, not a
hypothetical one: the FinRobot fixture this package tests against
(`tests/fixtures/finrobot/rate-shock.json`) is recorded exactly that
shape, so no cell built here can replay it, and
`tests/test_fingerprint.py` reproduces that world by hand instead of
going through :func:`battery`.

:attr:`Battery.renderer_key` names P6's own default rendering, a
:class:`~tradefloor.render.TextRenderer` at every default --
`TextRenderer().key()`, computed rather than typed, so this file cannot go
stale the way a retyped copy would. It is the battery's OWN reference
convention, not a claim about what any given agent rendered with: three of
the four shipped adapters default to
:class:`~tradefloor.render.JSONRenderer` instead (`LangGraphAdapter`,
`PydanticAIAdapter`, `OpenAIAgentsAdapter`; only `FinRobotAdapter` defaults
to text), and the battery does not touch an agent's own `renderer`.
:func:`fingerprint` reads what actually happened from the agent's OWN
:meth:`~tradefloor.integrations.common.FrameworkAdapter.provenance`, when
it has one, and says so in :attr:`Fingerprint.caveats` -- naming the
mismatch when the agent's renderer is not the battery's reference, and
naming the gap when the agent exposes no provenance at all, rather than
asserting either silently.

## Commit-reveal

Commit-reveal keeps a battery's market seeds secret until after the run,
so the agent's author cannot tune against them, and lets anyone check
afterwards that the seeds were fixed before the results were in. Draw the
seeds, publish the commitment, run, then publish the seeds and the salt:

```python
import secrets

seeds = [secrets.randbits(64) for _ in tf.battery().cells]
salt = secrets.token_bytes(16)
commitment = tf.commit(seeds, salt)          # publish this before the run
result = tf.fingerprint.fingerprint(agent, tf.sealed_battery(seeds, salt))
# Later, publish seeds and salt. Anyone can then check them:
assert tf.reveal(commitment, seeds, salt)
```

:func:`commit` is sha256 over the sorted seed list and the salt.
:func:`reveal` recomputes it and answers True or False. :func:`sealed_battery`
puts the seeds into the cells in the order given and keeps everything else
(roster seed, scenario, days, steps) as the battery version pins it. The
commitment covers the set of seeds and not which cell got which, so the
same seeds in another order also pass :func:`reveal` and build a different
battery. A verifier who cares about that assignment has to get the order
from a record kept with the reveal.

Draw sealed seeds from the whole range, ``secrets.randbits(64)`` for each
cell. A seed is any integer from 0 to ``2**64 - 1``, and a hidden seed is
only as hidden as the range it was drawn from: below ``2**32`` there are
2**32 markets per roster, few enough to find by simulating every one
against a market's first prices, and a sealed battery drawn there can be
opened without the reveal. Across 64 bits that search is 2**32 times
longer.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import TYPE_CHECKING, Any, NamedTuple, Sequence

from ._core import ValidationError, check_seed
from .counterfactual import Resample, World, _gross, _net, _shape
from .render import TextRenderer
from .scenario import Scenario

if TYPE_CHECKING:
    # Runtime imports happen inside the two functions that build one:
    # `integrations.common` pulls in the `integrations` subpackage, and
    # `import tradefloor` must never do that on its own -- see
    # `tests/test_integrations.py::
    # test_importing_tradefloor_does_not_import_the_integrations`, which
    # a top-level import here failed.
    from .integrations.common import Action, Decision

#: The cell set :func:`battery` returns by default. Bump this, and add a
#: new entry to the version table below, to ship a different battery; `1`
#: itself never changes shape. See the module docstring.
BATTERY_VERSION = 1

#: Instruments per cell. Fixed across every cell and every version so that
#: only the seed varies what a roster IS, not how big it is. Small enough
#: to keep the whole six-cell, sixty-day battery cheap against a scripted
#: agent, large enough that a decision naming one symbol is not a coin
#: flip.
_ROSTER_SIZE = 6


class Cell(NamedTuple):
    """One fixed world: a market seed, a roster seed, a scenario, a length.

    ``seed`` is the simulation seed :class:`~tradefloor.counterfactual.World`
    runs on. ``roster_seed`` builds the cell's roster with
    :func:`tradefloor.Universe.random` -- independent of ``seed``, the
    library's own convention for keeping "which market" and "which
    companies" separately citable. ``scenario`` is a name
    :meth:`~tradefloor.Scenario.load` accepts. ``days`` is how long the
    cell runs; ``steps`` is
    :attr:`~tradefloor.counterfactual.World.steps_per_day`.
    """

    seed: int
    roster_seed: int
    scenario: str
    days: int
    steps: int


class Battery:
    """A fixed, versioned set of :class:`Cell` and a reference renderer key.

    Two batteries of the same version are equal; see the module docstring
    for what the version pins and what :attr:`renderer_key` does and does
    not claim.
    """

    __slots__ = ("cells", "renderer_key", "version")

    def __init__(self, *, cells: Sequence[Cell], renderer_key: str,
                version: int) -> None:
        self.cells: tuple[Cell, ...] = tuple(cells)
        self.renderer_key = renderer_key
        self.version = version

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, Battery) and self.version == other.version
                and self.cells == other.cells
                and self.renderer_key == other.renderer_key)

    def __repr__(self) -> str:
        return (f"Battery(version={self.version}, "
               f"cells={len(self.cells)}, renderer_key={self.renderer_key!r})")


#: Battery version 1's six cells, one per shipped scenario, named rather
#: than read from `Scenario.available()` at build time -- a scenario added
#: to the package after this version shipped must not silently grow it.
#: Seeds are well clear of the ones the shipped examples and fixtures use
#: (4242, 11, 101, ...), so a battery run can never collide with a
#: recorded transcript's own world. `days=60` and `steps=6` are fixed for
#: every cell. Five of the six scenarios fire their main shock at day 50,
#: so those cells see ten days after it (see the module docstring). 6 steps
#: a day is the library's own decision cadence -- see `World`'s default
#: and `examples/rate-shock/counterfactual.py`. Never edit these. A longer
#: or different set is a new version.
_CELLS: dict[int, tuple[Cell, ...]] = {
    1: (
        Cell(90_000, 91_000, "geopolitical_conflict", 60, 6),
        Cell(90_001, 91_001, "liquidity_crisis", 60, 6),
        Cell(90_002, 91_002, "oil_price_spike", 60, 6),
        Cell(90_003, 91_003, "policy_regime_shift", 60, 6),
        Cell(90_004, 91_004, "rate_shock", 60, 6),
        Cell(90_005, 91_005, "recession", 60, 6),
    ),
}


def _build(version: int) -> Battery:
    try:
        cells = _CELLS[version]
    except KeyError:
        raise ValidationError(
            f"no battery version {version}; this release ships "
            f"{sorted(_CELLS)}.") from None
    return Battery(cells=cells, renderer_key=TextRenderer().key(),
                   version=version)


def battery(version: int = BATTERY_VERSION) -> Battery:
    """The named battery version. Immutable: every call returns an equal one.

    ``renderer_key`` is computed from a fresh
    :class:`~tradefloor.render.TextRenderer` on every call rather than
    stored as a literal, so it cannot drift from what P6 actually
    considers its default. See the module docstring for what the battery
    pins and what depending on P6 means here.
    """
    return _build(version)


# ---------------------------------------------------------------------------
# Reading decisions off a run
# ---------------------------------------------------------------------------

def _decision_from_publish(raw: dict[str, Any], *, cell: int,
                           step: int) -> Decision:
    """Rebuild the object :func:`~tradefloor.counterfactual._shape` reads
    from what ``agent.decision()`` publishes.

    A `FrameworkAdapter.decision()` -- what every shipped adapter
    implements -- returns `{"step": ..., **Decision.as_dict()}`: the
    actions and the rationale, JSON-shaped rather than the attribute
    access `_shape` uses. Rebuilt here rather than asking `_shape` to read
    two shapes, so the one function this package was told to share with
    `counterfactual` stays the only place a decision is canonicalised.
    """
    from .integrations.common import Action, Decision

    try:
        actions = [Action(a["symbol"], a["side"], a["quantity"])
                  for a in raw["actions"]]
    except (KeyError, TypeError, ValidationError) as exc:
        raise ValidationError(
            f"cell {cell} step {step}: agent.decision() published "
            f"{raw!r}, which is not the {{'actions': [...], 'rationale': "
            "...} shape fingerprint() reads. Every FrameworkAdapter "
            "publishes this shape; a scripted agent under test has to "
            "publish it too, or there is nothing here to hash.") from exc
    return Decision(actions, str(raw.get("rationale", "")))


def _decisions_for_trace(trace: Sequence[dict[str, Any]], *,
                         cell: int) -> list[dict[str, Any]]:
    """One canonical entry per GENUINE decision in one cell's trace, in order.

    `World.trace` carries one row per simulation step, not one row per
    decision. An agent asked every ``every`` steps -- a `FrameworkAdapter`'s
    own cadence, which the battery does not touch -- shows the SAME
    `decision()` publish at every row in between, because `World` re-reads
    the agent's last publish on every step rather than only on the ones it
    changed. A row is kept here only when its publish differs from the
    last REAL publish seen. For a `FrameworkAdapter` that is exactly the
    steps a call happened: its published dict carries the step it was
    asked on, so two genuinely distinct real answers can never compare
    equal even when their actions do, and a repeated stale publish always
    does. A scripted agent that publishes the identical dict twice IN A
    ROW has the repeat folded into one entry here -- if keeping two such
    decisions distinct matters, publish something that says which call it
    was, the way every adapter already does.

    A refused row -- ``row["decision"]`` is `None`, which `World` writes
    on a step `on_refusal="skip"` skipped rather than on a step nobody
    has asked yet -- is skipped here too, and comparison against it is
    not: the row before a refusal and the row after it are compared to
    EACH OTHER, not through a `None` in between. The alternative --
    treating a refusal as resetting what "the last decision" means --
    would double-count the agent's next stable answer as a fresh
    decision for no reason but a step it never got to publish through,
    which is exactly the kind of thing this function exists not to see.
    """
    out: list[dict[str, Any]] = []
    previous: Any = None
    for row in trace:
        raw = row.get("decision")
        if raw is None:
            continue
        if raw != previous:
            step = row["step"]
            shape = _shape(_decision_from_publish(raw, cell=cell, step=step))
            out.append({"cell": cell, "step": step,
                       "shape": [list(entry) for entry in shape]})
        previous = raw
    return out


def _digest(decisions: Sequence[dict[str, Any]]) -> str:
    canonical = json.dumps(list(decisions), sort_keys=True,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _group_by_cell(
    decisions: Sequence[dict[str, Any]],
) -> dict[int, list[tuple[int, list]]]:
    groups: dict[int, list[tuple[int, list]]] = {}
    for entry in decisions:
        groups.setdefault(entry["cell"], []).append(
            (entry["step"], entry["shape"]))
    for rows in groups.values():
        rows.sort(key=lambda pair: pair[0])
    return groups


def _decision_from_shape(shape: Sequence[Sequence[Any]]) -> Decision:
    from .integrations.common import Action, Decision

    return Decision([Action(symbol, side, quantity)
                     for symbol, side, quantity in shape])


# ---------------------------------------------------------------------------
# Fingerprinting
# ---------------------------------------------------------------------------

def fingerprint(agent: Any,
                battery: Battery | None = None) -> "Fingerprint":
    """Run ``agent`` on every cell of ``battery`` and hash what it ordered.

    ``battery`` left at ``None`` (its default) builds
    :func:`battery`'s own default version fresh, inside this call rather
    than once at import time -- a default built at import time would be
    a single shared object no later change to :func:`battery` could ever
    reach, silently, and the one that matters here is
    :attr:`Battery.renderer_key`, which :func:`battery` documents as
    computed live specifically so it cannot drift.

    ``agent`` gets an independent copy per cell -- ``agent.fork()`` when
    the agent has one, the same
    :func:`copy.deepcopy` fallback
    :class:`~tradefloor.counterfactual.World` itself uses otherwise -- so
    that one cell's price history and decision record cannot leak into
    the next. Each cell runs `on_refusal="skip"`: one bad response costs
    that step, named in :attr:`Fingerprint.caveats`, not the rest of the
    battery.

    Raises :class:`~tradefloor.ValidationError` before running anything if
    ``agent`` has no callable `decision()` -- see the module docstring for
    why a fingerprint needs one.
    """
    if not callable(getattr(agent, "decision", None)):
        raise ValidationError(
            f"{type(agent).__name__} has no decision(). fingerprint() "
            "hashes what agent.decision() publishes; a FrameworkAdapter "
            "implements it, and a plain policy under test has to publish "
            "{'actions': [...], 'rationale': ...} the same way to be "
            "fingerprinted.")

    if battery is None:
        battery = _build(BATTERY_VERSION)

    from . import Universe  # deferred: Universe lives on the package
                             # __init__, which is still running the first
                             # time this module is imported from it.

    decisions: list[dict[str, Any]] = []
    unusable = 0
    for index, cell in enumerate(battery.cells):
        cell_agent = (agent.fork() if callable(getattr(agent, "fork", None))
                     else copy.deepcopy(agent))
        roster = Universe.random(_ROSTER_SIZE, seed=cell.roster_seed)
        world = World(seed=cell.seed, universe=list(roster), agent=cell_agent,
                     steps_per_day=cell.steps, on_refusal="skip",
                     label=f"fingerprint battery {battery.version} cell "
                           f"{index}")
        world.apply(Scenario.load(cell.scenario))
        world.run(days=cell.days)
        unusable += sum(1 for row in world.trace if row["unusable"])
        decisions.extend(_decisions_for_trace(world.trace, cell=index))

    decisions.sort(key=lambda entry: (entry["cell"], entry["step"]))

    renderer_key = None
    provenance = getattr(agent, "provenance", None)
    if callable(provenance):
        renderer_key = provenance().get("renderer")

    caveats: list[str] = []
    if renderer_key is None:
        caveats.append(
            "agent exposes no provenance()/renderer: the rendering "
            "convention it saw while deciding cannot be named.")
    elif renderer_key != battery.renderer_key:
        caveats.append(
            f"agent rendered with {renderer_key!r}, not the battery's "
            f"reference renderer {battery.renderer_key!r}. A fingerprint "
            "is comparable only to another taken with the same renderer.")
    if unusable:
        caveats.append(
            f"{unusable} step(s) across the battery produced no usable "
            "decision and were skipped rather than hashed.")

    return Fingerprint(digest=_digest(decisions), decisions=decisions,
                       battery=battery.version, renderer_key=renderer_key,
                       caveats=caveats)


class Fingerprint:
    """What one agent ordered across one battery, as a digest and a record.

    ``digest`` is sha256 over :attr:`decisions`, cell then step order --
    see :func:`_digest`. ``decisions`` is the canonical list itself, one
    entry per genuine decision: ``{"cell": int, "step": int, "shape":
    [[symbol, side, quantity], ...]}``. ``battery`` is the battery
    VERSION, an int, not the :class:`Battery` object -- two fingerprints
    from the same version are comparable by :meth:`compare` because their
    cells are known to match without carrying the cells themselves.
    ``renderer_key`` and ``caveats`` are documented on :func:`fingerprint`,
    which computes them; this class only carries what it is given.
    """

    __slots__ = ("digest", "decisions", "battery", "renderer_key", "caveats")

    def __init__(self, *, digest: str, decisions: Sequence[dict[str, Any]],
                battery: int, renderer_key: str | None,
                caveats: Sequence[str]) -> None:
        self.digest = digest
        self.decisions = [dict(entry) for entry in decisions]
        self.battery = battery
        self.renderer_key = renderer_key
        self.caveats = list(caveats)

    def __eq__(self, other: Any) -> bool:
        return (isinstance(other, Fingerprint)
                and self.digest == other.digest
                and self.battery == other.battery
                and self.decisions == other.decisions
                and self.renderer_key == other.renderer_key
                and self.caveats == other.caveats)

    def to_json(self) -> str:
        """This fingerprint, complete, as indented JSON.

        Every field :meth:`from_json` needs to rebuild an equal
        :class:`Fingerprint`, including :attr:`caveats` -- a caveat is a
        fact about the run that produced this fingerprint, not something
        a reader should have to recompute to see.
        """
        return json.dumps({
            "digest": self.digest,
            "battery": self.battery,
            "renderer_key": self.renderer_key,
            "caveats": list(self.caveats),
            "decisions": self.decisions,
        }, indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> "Fingerprint":
        """The inverse of :meth:`to_json`.

        Not part of the frozen design note's own grammar; added because a
        digest published without a way to read it back is not a citable
        one.
        """
        payload = json.loads(text)
        return cls(digest=payload["digest"], decisions=payload["decisions"],
                   battery=payload["battery"],
                   renderer_key=payload.get("renderer_key"),
                   caveats=payload.get("caveats", []))

    def compare(self, other: "Fingerprint",
                floor: Resample | None) -> "FingerprintComparison":
        """Cell by cell, where ``self`` and ``other`` ordered different things.

        Both fingerprints must carry the same :attr:`battery` version --
        different versions are different worlds, and a per-cell alignment
        between them would compare runs that were never the same
        experiment. Within a cell, decisions are aligned by ORDINAL
        position (first decision against first decision, and so on), not
        by step: two agents at different decision cadences still align
        this way, and a cell where one side made more decisions than the
        other reports every position past the shorter side's end as
        differing.

        ``floor`` is a
        :class:`~tradefloor.counterfactual.Resample` measured on ONE of
        the two agents at one decision point -- pass ``None`` where none
        was measured. Given one, a differing decision EXCEEDS it when the
        gap between the two sides' :func:`~tradefloor.counterfactual._net`
        or :func:`~tradefloor.counterfactual._gross` is larger than that
        agent's own within-arm stdev in the same measure; this is a
        single floor applied uniformly across the whole battery, a coarse
        instrument the module docstring already says not to expect more
        of than that.
        """
        if self.battery != other.battery:
            raise ValidationError(
                f"comparing battery version {self.battery} against "
                f"{other.battery}: their cells are not the same worlds, "
                "so a per-cell alignment would compare unrelated runs.")

        mine = _group_by_cell(self.decisions)
        theirs = _group_by_cell(other.decisions)
        n_cells = 1 + max([-1, *mine, *theirs])

        floor_net = floor_gross = None
        if floor is not None:
            floor_net = max(floor.noise[arm]["stdev_net"]
                           for arm in (floor.control, floor.treatment))
            floor_gross = max(floor.noise[arm]["stdev_gross"]
                             for arm in (floor.control, floor.treatment))

        cells: list[dict[str, Any]] = []
        differing_total = 0
        compared_total = 0
        exceeding_total = 0 if floor is not None else None
        for index in range(n_cells):
            a = mine.get(index, [])
            b = theirs.get(index, [])
            width = max(len(a), len(b))
            differing = 0
            exceeding = 0 if floor is not None else None
            for position in range(width):
                shape_a = a[position][1] if position < len(a) else None
                shape_b = b[position][1] if position < len(b) else None
                if shape_a == shape_b:
                    continue
                differing += 1
                if floor is not None:
                    gap_net = abs(
                        _net(_decision_from_shape(shape_a or []))
                        - _net(_decision_from_shape(shape_b or [])))
                    gap_gross = abs(
                        _gross(_decision_from_shape(shape_a or []))
                        - _gross(_decision_from_shape(shape_b or [])))
                    if gap_net > floor_net or gap_gross > floor_gross:
                        exceeding += 1
            cells.append({"cell": index, "self_decisions": len(a),
                         "other_decisions": len(b), "differing": differing})
            differing_total += differing
            compared_total += width
            if floor is not None:
                exceeding_total += exceeding

        caveats: list[str] = []
        if floor is None:
            caveats.append(
                "compared with no floor: every difference is reported, "
                "and none is separated from the agent's own noise.")
        else:
            caveats.append(
                f"compared against a floor measured at step {floor.at} "
                f"over {floor.n} calls per arm ({floor.control!r} vs "
                f"{floor.treatment!r}); a differing decision counts as "
                "exceeding it when its net or gross gap is larger than "
                "the larger of the two arms' own stdev in that measure.")

        return FingerprintComparison(
            battery=self.battery, cells=cells, differing=differing_total,
            total=compared_total, floor_given=floor is not None,
            exceeding_floor=exceeding_total, caveats=caveats)

    def __repr__(self) -> str:
        return (f"Fingerprint(battery={self.battery}, "
               f"digest={self.digest[:12]}..., "
               f"{len(self.decisions)} decisions)")


class FingerprintComparison:
    """The result of :meth:`Fingerprint.compare`. See that method.

    Named apart from :class:`tradefloor.counterfactual.Comparison`, which
    already owns the name ``Comparison`` at package level and answers a
    different question -- where one forked run came apart, not whether
    two independent battery runs ordered the same things.
    """

    __slots__ = ("battery", "cells", "differing", "total", "floor_given",
                "exceeding_floor", "caveats")

    def __init__(self, *, battery: int, cells: Sequence[dict[str, Any]],
                differing: int, total: int, floor_given: bool,
                exceeding_floor: int | None,
                caveats: Sequence[str]) -> None:
        self.battery = battery
        self.cells = [dict(row) for row in cells]
        self.differing = differing
        self.total = total
        self.floor_given = floor_given
        self.exceeding_floor = exceeding_floor
        self.caveats = list(caveats)

    def as_dict(self) -> dict[str, Any]:
        return {"battery": self.battery, "cells": list(self.cells),
               "differing": self.differing, "total": self.total,
               "floor_given": self.floor_given,
               "exceeding_floor": self.exceeding_floor,
               "caveats": list(self.caveats)}

    def __repr__(self) -> str:
        return (f"FingerprintComparison(battery={self.battery}, "
               f"differing={self.differing}/{self.total}, "
               f"floor_given={self.floor_given})")


# ---------------------------------------------------------------------------
# Commit-reveal
# ---------------------------------------------------------------------------

def commit(seeds: Sequence[int], salt: bytes) -> str:
    """sha256 of the sorted seed list and ``salt``, in hex. Publish it
    before the run.

    ``salt`` must be ``bytes``. A ``str`` is refused because it would have
    to be encoded, and two salts that look the same on screen could then
    hash differently. Keep the salt private until the reveal. The library
    never stores it.

    Each seed must be an integer from 0 to ``2**64 - 1``. They are checked
    here, so a list the engine would refuse cannot be committed to. Draw
    them with ``secrets.randbits(64)``; the module docstring says why the
    full range matters.

    ```python
    seeds = [secrets.randbits(64) for _ in tf.battery().cells]
    salt = secrets.token_bytes(16)
    commitment = tf.commit(seeds, salt)
    ```
    """
    if isinstance(salt, str):
        raise ValidationError(
            "salt must be bytes, not str. A caller-supplied salt is "
            "hashed as raw bytes; encoding a str implicitly is a choice "
            "this function will not make silently, because two salts "
            "that read identically could then hash differently.")
    canonical = json.dumps(sorted(check_seed(s) for s in seeds),
                           separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical + bytes(salt)).hexdigest()


def reveal(commitment: str, seeds: Sequence[int], salt: bytes) -> bool:
    """Whether ``seeds`` and ``salt`` reproduce ``commitment``.

    Returns False for a different seed list or a different salt, and for a
    list holding something that could never have been committed to (a
    negative integer, one of ``2**64`` or more, a float). It raises only for
    a ``str`` salt, as :func:`commit` does. The order of ``seeds`` does not
    matter, because :func:`commit` sorts them.

    ```python
    tf.reveal(commitment, seeds, salt)         # True
    tf.reveal(commitment, seeds[::-1], salt)   # also True
    tf.reveal(commitment, seeds, b"other")     # False
    ```
    """
    try:
        seeds = [check_seed(s) for s in seeds]
    except ValidationError:
        return False
    return commit(seeds, salt) == commitment


def sealed_battery(seeds: Sequence[int], salt: bytes,
                   version: int = BATTERY_VERSION) -> Battery:
    """Battery ``version`` with ``seeds`` as its cells' market seeds.

    The seeds go to the cells in the order given, one per cell, and a list
    of any other length is refused. Everything else (roster seed, scenario,
    days, steps, the reference renderer key) stays as ``version`` pins it.

    This does not check a commitment, because it is not given one. Call
    :func:`reveal` for that. ``salt`` is taken so the same ``(seeds, salt)``
    pair passes through all three functions, and a ``str`` salt is refused
    as in :func:`commit`. Nothing else is done with it.

    ```python
    sealed = tf.sealed_battery(seeds, salt)
    result = tf.fingerprint.fingerprint(agent, sealed)
    ```
    """
    base = _build(version)
    seeds = [check_seed(s) for s in seeds]
    if len(seeds) != len(base.cells):
        raise ValidationError(
            f"sealed_battery needs {len(base.cells)} seeds for battery "
            f"version {version}, one per cell in cell order, got "
            f"{len(seeds)}.")
    if not isinstance(salt, (bytes, bytearray)):
        raise ValidationError(
            "salt must be bytes, not str, matching commit()'s own "
            "requirement -- sealed_battery takes the same (seeds, salt) "
            "pair a caller reveals.")
    cells = tuple(cell._replace(seed=seed)
                  for cell, seed in zip(base.cells, seeds))
    return Battery(cells=cells, renderer_key=base.renderer_key,
                  version=version)
