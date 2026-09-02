"""The draw stream as an addressable, editable object.

Every random number the engine consumes has an address: the stream it
came from, whether it was a uniform or a normal, and how many draws of
that kind the stream had taken since the engine was built. This module
names those addresses, installs substitutions at them, reads the log of
what a stream delivered, and runs one day under a set of substitutions
in a fork so the parent is untouched.

Nothing here changes a trajectory on its own. An engine with an empty
overlay produces the known-answer digest; a patched draw costs the same
generator step as an unpatched one, so every address after it keeps its
value and ``draws_by_stream`` reads the same with and without the
overlay. The Rust side (``rust/src/rng.rs``) states the contract; this
module is the Python surface over it.

## Addresses

``DrawAddress(stream, kind, index)``. Streams are the seven the engine
derives from its seed: ``market``, ``economy``, ``external``, ``jumps``,
``volume``, ``news`` and ``volume_idio``. ``kind`` is ``"uniform"`` or
``"normal"``. Normals index by normal count and uniforms by uniform
count, counted apart: Box-Muller takes two uniforms from the generator
for every two normals it returns and caches the second as a spare, so
counting the underlying uniforms would leave every second normal without
an address of its own. The two uniforms Box-Muller consumes are not on
the uniform index.

## What an address means across days

The index runs from engine construction, so the same address names the
same draw in every fork of the same run and in any restore of a snapshot
that carries the counts. A snapshot written before draw addressing
restores with counts of zero: its addresses restart at the restore, and a
patch written against the original run does not land where it did.

## Sites

The engine tags each group of draws with the call site that takes them.
:data:`SITES` lists them in schedule order per stream; the test
``test_the_site_sequence_is_the_schedule`` walks one day and asserts the
sequence, so the list is the draw schedule made legible rather than a
description of it.
"""
from __future__ import annotations

import math
import struct
from typing import Any, Callable, NamedTuple, Sequence

from ._core import Engine

STREAMS = ("market", "economy", "external", "jumps", "volume", "news",
           "volume_idio")
KINDS = ("uniform", "normal")

#: The call sites, in the order each stream meets them. The market
#: stream's order is per open tick; the others are per day. The economy
#: chain's sites are groups: the daily chain, the cycle roll and the
#: central bank each take a state-dependent number of draws, so their
#: draws are addressed by index and named by group.
SITES = {
    "market": ("market_factor_z", "sector_z", "factor_idio_z", "stash_u",
               "settle_u"),
    "jumps": ("jump_market_u", "jump_market_z", "jump_company_u",
              "jump_company_z"),
    "volume": ("volume_z",),
    "volume_idio": ("volume_idio_z",),
    "news": ("news_u", "news_z"),
    "economy": ("economy_daily", "economy_cycle", "central_bank"),
    "external": ("external",),
}


class DrawAddress(NamedTuple):
    """One draw: the stream, the kind and the index of that kind."""

    stream: str
    kind: str
    index: int

    def check(self) -> "DrawAddress":
        if self.stream not in STREAMS:
            raise ValueError(
                f"unknown stream {self.stream!r}; one of {', '.join(STREAMS)}")
        if self.kind not in KINDS:
            raise ValueError(
                f"unknown draw kind {self.kind!r}; uniform or normal")
        if self.index < 0:
            raise ValueError(f"a draw index is non-negative, got {self.index}")
        return self


class Patch(NamedTuple):
    """One substitution: the address and the value the consumer receives.

    A uniform must lie in ``[0, 1)`` to be a uniform the consumer can
    read; a normal takes any finite value. Neither is enforced here, so a
    caller that wants an event to fire can set a uniform to ``0.0`` and one
    that wants it unfired can set ``1.0``, and the comparison the consumer
    makes is what decides (``World.unfire`` documents the jump site's).
    """

    address: DrawAddress
    value: float


class LoggedDraw(NamedTuple):
    """One recorded draw: what the consumer received, when and where."""

    address: DrawAddress
    value: float
    day: int
    site: str
    tag: int


def patch_draws(engine: Engine, patches: Sequence[Patch]) -> None:
    """Install ``patches`` on ``engine``.

    Each generator still advances at every address; only the value the
    consumer receives changes, so the draw counts and every unpatched
    address are identical with and without the overlay. A patch at an
    address the engine has already passed is kept and never lands; the
    caller reads ``engine.stream_positions()`` to see where each stream is.
    """
    engine.patch_draws([(p.address.check().stream, p.address.kind,
                         int(p.address.index), float(p.value))
                        for p in patches])


def draw_log(engine: Engine, stream: str, from_day: int,
             to_day: int) -> list[LoggedDraw]:
    """The draws ``stream`` delivered on days ``from_day..to_day``.

    Logging is opt-in: ``engine.trace_draws(stream, from_day, to_day)``
    must have been called before those days ran, or the log is empty. A
    second call widens the range and keeps what was recorded.
    ``run_day_with`` traces the day it runs. The value logged is the one
    the consumer received, so a patched draw shows its patched value.
    """
    return [LoggedDraw(DrawAddress(*addr), value, day, site, tag)
            for addr, value, day, site, tag
            in engine.draw_log(stream, from_day, to_day)]


def run_day_with(engine: Engine, day: int, patches: Sequence[Patch],
                 *, hour: int = 9, minute: int = 30, day_of_week: int = 3,
                 ticks_per_day: int = 390, volatility: float = 1.0,
                 streams: Sequence[str] = STREAMS) -> "DayResult":
    """Fork ``engine``, install ``patches``, run ``day`` once, and return
    the day's closes and its ground-truth attribution.

    The primitive every other instrument in this module uses. The fork is
    a copy, so the parent is untouched whatever the patches do; the fork's
    draw log is traced on every stream in ``streams`` for that day, so the
    result carries what each stream delivered.

    ``day`` numbers the day the way ``Engine.run_days(first_day=...)``
    does. The fork opens the market at that day, runs ``ticks_per_day``
    ticks, records the day, and closes it, which advances the macro chain.

    What this cannot tell a caller: whether the closes it returns are
    reachable by any draw vector at all. A circuit breaker or the order
    book clamps the price, and a patched draw large enough to cross a
    clamp produces the clamped close, not a proportional one. Phase 4's
    solver reports the binding clamp; this function reports the close.
    """
    forks = engine.fork(1)
    fork = forks[0]
    patch_draws(fork, patches)
    for stream in streams:
        fork.trace_draws(stream, day, day)
    fork.run_days(1, hour=hour, minute=minute, day_of_week=day_of_week,
                  ticks_per_day=ticks_per_day, volatility=volatility,
                  record=True, first_day=day)
    raw = fork.prices()
    closes = dict(zip(fork.tickers,
                      struct.unpack(f"<{len(raw) // 8}d", raw)))
    truth = fork.truth(day=day)
    return DayResult(day=day, engine=fork, closes=closes, truth=truth,
                     patches=tuple(patches))


class DayResult(NamedTuple):
    """One day run under a set of patches, in a fork."""

    day: int
    engine: Engine
    closes: dict[str, float]
    truth: Any
    patches: tuple[Patch, ...]

    def log(self, stream: str) -> list[LoggedDraw]:
        return draw_log(self.engine, stream, self.day, self.day)


def market_day_layout(engine: Engine, day: int) -> dict[int, tuple[int, int, int]]:
    """Where each active company's market-stream normals sit on ``day``.

    Returns ``{company_index: (first, stride, ticks)}``: the company's
    normal at tick ``t`` of that day has index ``first + t * stride``.
    Derived from the day mark the engine records at the open (the stream
    positions, the active roster and the sector count) and the per-tick
    schedule the site sequence pins: one normal for the market factor, one
    per sector, then one per active company. The draw log is the check on
    the arithmetic and ``test_the_layout_matches_the_log`` makes it.
    """
    layout = engine.market_day_layout(day)
    if layout is None:
        raise ValueError(f"day {day} was never opened on this engine")
    return {company: (first, stride, ticks)
            for company, first, stride, ticks in layout}


#: The uniform that keeps a jump from firing. ``Engine::apply_jumps``
#: fires the market jump when its uniform is below the day's intensity,
#: and a uniform lies in ``[0, 1)``, so ``1.0`` is never below an
#: intensity of at most one. ``World.unfire`` documents the comparison.
NO_FIRE = 1.0


def surgery_patches(seed: int, stream: str, surgery_seed: int,
                    addresses: Sequence[DrawAddress]) -> list[Patch]:
    """Patches that re-randomise ``addresses`` of ``stream``.

    One value per address, in order, from a generator derived from the
    root ``seed``, the stream and ``surgery_seed`` per the surgery
    derivation contract in ``rust/src/rng.rs`` (``GameRng::surgery``):
    the stream's own mix, mixed again with the surgery seed and a tag,
    on a sequence no stream uses. Uniforms and normals are drawn from it
    in the order the addresses ask, so the same addresses under the same
    seeds give the same patches, and a different order gives different
    ones.

    The addresses are the caller's to know. ``World.window`` finds them
    by running the days once in a fork with the stream traced; this
    function only turns them into values.
    """
    checked = [DrawAddress(*a).check() for a in addresses]
    for a in checked:
        if a.stream != stream:
            raise ValueError(
                f"a surgery of {stream} cannot patch {a.stream}; one "
                f"stream per surgery, so the record says what moved")
    values = Engine.surgery_draws(int(seed), stream, int(surgery_seed),
                                  [a.kind for a in checked])
    return [Patch(a, v) for a, v in zip(checked, values)]


# -- attribution --------------------------------------------------------------

#: The sites whose uniforms decide an event. A small change to one of these
#: either does nothing or flips the event, so they are forced to each end of
#: the unit interval rather than perturbed: 0.0 fires, 1.0 does not.
EVENT_SITES = frozenset({"jump_market_u", "jump_company_u", "news_u"})
#: The streams attributed at event level: one row per logged draw.
EVENT_STREAMS = ("jumps", "economy", "news", "volume", "volume_idio")
#: The market stream's sites that are aggregated by day.
DAY_SITES = {"factor_idio_z": "company", "market_factor_z": "market",
             "sector_z": "sector"}


class Target(NamedTuple):
    """What an attribution measures, per arm.

    Build one with :func:`statistic`, :func:`pnl` or :func:`column`, or pass
    a callable ``f(world) -> float`` to :func:`attribute` and it is wrapped.
    """

    kind: str
    name: str | None = None
    day: int | None = None
    ticker: str | None = None
    fn: Any = None

    def label(self) -> str:
        if self.kind == "statistic":
            return str(self.name)
        if self.kind == "pnl":
            return "pnl_since_fork"
        if self.kind == "column":
            who = self.ticker or "roster mean"
            return f"{self.name}[{who}] at day {self.day}"
        return getattr(self.fn, "__name__", "callable")


def statistic(name: str) -> Target:
    """A panel statistic from :func:`tradefloor.facts.panel_statistics`, by key.

    The arms record every day they run and the statistic is read off the
    daily bars at the horizon, so the run must be long enough for it:
    ``facts`` needs ``min_observations`` daily returns per name (thirty by
    default), which is thirty-one recorded days.
    """
    return Target("statistic", name=name)


def pnl() -> Target:
    """The agent's P&L since the fork, ``World.summary()["pnl_since"]``."""
    return Target("pnl")


def column(name: str, day: int, ticker: str | None = None) -> Target:
    """An engine column at the close of ``day``: one name's value, or the
    mean across the roster when ``ticker`` is ``None``.

    Read at ``day``, whatever the horizon. The arms run to ``day`` when the
    window ends earlier, and past it when the window ends later; in the
    second case the value is taken as the arm passes ``day`` rather than
    where the arm stops. Read at the stop, a window of ``(1, 3)`` with a
    target at day 1 reported day 3's mean price under a label that read
    ``at day 1``: 98.275 against day 1's own 98.2075, on a four-name roster
    at seed 99.
    """
    return Target("column", name=name, day=int(day), ticker=ticker)


class Attribution:
    """The effect of each draw, or each day of a name's draws, on a target.

    ``rows`` is one dict per perturbation: the address (or the first
    address of a day aggregate and how many it covers), the day, the site
    and its tag, the ticker where the site names one, the granularity, the
    perturbation and its size, the target under the control and under the
    perturbation, and the effect as their difference. ``control`` is the
    target's value on the untouched arm and ``caveats`` what this call
    could not do, computed from what it was asked.

    :meth:`table` is the same rows as an Arrow table, on the results
    surface the rest of the library uses. Rows are also plain data, so a
    caller without pyarrow still has the answer.
    """

    def __init__(self, *, target: Target, window: tuple[int, int],
                 horizon: int, control: float, rows: list[dict],
                 caveats: list[str], delta: float, joint: float = 0.0,
                 interaction: float = 0.0, joint_rows: int = 0,
                 plan_caveats: list[str] | None = None) -> None:
        #: The caveats whose text counts rows, so a caller that shards the
        #: plan can drop them and restate them over the merged rows.
        #: :func:`row_caveats` rebuilds all but the interaction line, which
        #: needs an arm rather than a row.
        self.plan_caveats = list(plan_caveats or ())
        #: The target's move with every changing patch installed at once,
        #: as a difference from ``control``.
        self.joint = joint
        #: ``joint`` minus the sum of the rows that make it up. Non-zero
        #: because the market has feedback, so the rows do not decompose
        #: the target and are never presented as a total.
        self.interaction = interaction
        #: How many rows the joint arm installed.
        self.joint_rows = joint_rows
        self.target = target
        self.window = window
        self.horizon = horizon
        self.control = control
        self.rows = rows
        self.caveats = caveats
        self.delta = delta

    #: The columns of :meth:`table`, in order, with their Arrow types.
    COLUMNS = (("stream", "string"), ("kind", "string"), ("index", "int64"),
               ("count", "int64"), ("day", "int64"), ("site", "string"),
               ("tag", "int64"), ("ticker", "string"),
               ("granularity", "string"), ("perturbation", "string"),
               ("delta", "float64"), ("control", "float64"),
               ("treatment", "float64"), ("effect", "float64"))

    def table(self) -> Any:
        """The rows as a ``pyarrow.Table``."""
        try:
            import pyarrow as pa
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Attribution.table builds an Arrow table and needs pyarrow. "
                "Install it with: pip install tradefloor[arrow]; the rows "
                "are on .rows without it.") from exc
        schema = pa.schema([(name, getattr(pa, kind)())
                            for name, kind in self.COLUMNS])
        columns = {name: [row.get(name) for row in self.rows]
                   for name, _ in self.COLUMNS}
        return pa.table(columns, schema=schema)

    def ranked(self) -> list[dict]:
        """Rows by absolute effect, largest first."""
        return sorted(self.rows, key=lambda r: -abs(r["effect"]))

    def as_dict(self) -> dict[str, Any]:
        return {"target": self.target.label(), "window": list(self.window),
                "horizon": self.horizon, "control": self.control,
                "delta": self.delta, "rows": self.rows,
                "caveats": list(self.caveats)}

    def render(self, top: int = 20) -> str:
        lines = [f"  target {self.target.label()}: control {self.control:.6g}",
                 f"  window days {self.window[0]}..{self.window[1]}, run to "
                 f"day {self.horizon}, {len(self.rows)} rows",
                 f"  {'stream':<12}{'site':<18}{'day':>5}{'tag':>5}"
                 f"{'perturbation':>16}{'effect':>14}"]
        for row in self.ranked()[:top]:
            lines.append(f"  {row['stream']:<12}{row['site']:<18}"
                         f"{row['day']:>5}{row['tag']:>5}"
                         f"{row['perturbation']:>16}{row['effect']:>14.6g}")
        for caveat in self.caveats:
            lines.append(f"  caveat: {caveat}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"Attribution({self.target.label()!r}, "
                f"window={self.window}, {len(self.rows)} rows)")


def attribute(world: Any, window: Any, target: Any,
              granularity: str = "both", *, streams: Sequence[str] | None = None,
              delta: float = 1.0, horizon: int | None = None,
              shard: tuple[int, int] | None = None) -> Attribution:
    """Finite differences of ``target`` in each draw, under common random
    numbers, over the days in ``window``.

    # What it measures

    A control arm is forked from ``world`` and run to the horizon with the
    chosen streams traced over the window; the target is read off it. Then
    one arm per perturbation is forked from the same state, one patch set
    is installed, the arm runs the same days with the same agent, and the
    target is read again. The effect is the difference. Every arm shares
    every other draw with the control, so the difference is the draw's and
    not a reshuffle: ``stream_positions`` is identical across arms, on all
    seven streams. ``draws_by_stream`` reports three of them and cannot
    see jumps, news, volume or the per-name volume stream, which is four
    of the five attributed at event level.

    Two perturbations, by what a draw is:

    - A normal moves by ``delta`` in z units, at the value the control
      received. One row.
    - A uniform at an event site (:data:`EVENT_SITES`) is forced to each
      end of the unit interval: 0.0 fires the event and 1.0 does not. Two
      rows, ``fire`` and ``unfire``. One of them is the control's own
      state and has an effect of exactly zero, which says which state the
      control was in. A small change to an event uniform either does
      nothing or flips the event, so a finite difference in it is not a
      derivative and is not taken.

    # Granularity

    ``"event"`` attributes the jumps, economy, news, volume and per-name
    volume streams one logged draw at a time. ``"day"`` attributes the
    market stream by day aggregate: all of one name's factor normals on
    one day move together by ``delta / sqrt(ticks)``, and so do the day's
    market factor normals and each sector's. ``"both"`` does both, and
    ``streams`` narrows either.

    The day aggregate is the identified quantity. One name's daily return
    is a sum over its ``T`` tick normals, and that sum has a standard
    deviation of ``sqrt(T)`` in the units of one tick's noise. A common
    shift of ``delta / sqrt(T)`` on each of them moves the sum by
    ``delta * sqrt(T)`` in those units, which is ``delta`` standard
    deviations OF THE DAY'S OWN SUM. So a row's ``delta`` is per tick and
    the quantity it states is per day, and ``delta * sqrt(count)`` recovers
    the day-sigma step the row was taken at. The effect therefore grows
    with ``T`` at a fixed ``delta``: 0.0725, 0.19, 0.25 and 0.4225 on the
    market factor over 20, 40, 80 and 160 ticks, four names at seed 99.
    The effect of one tick normal on its own is not identified from a daily
    target, and a row for it would be a number without a meaning. The
    market stream's other draws (the stash and settlement uniforms) drive
    microstructure and are not attributed;
    the caveats say so whenever the market stream is in the call.

    # What it cannot do

    The agent runs in every arm. An agent whose decisions are a function
    of what it sees gives the same answer in the same state; one that is
    not (a model sampled at temperature) puts its own noise into every
    effect, and nothing here separates the two. An effect measured across
    a circuit breaker or a book clamp is the clamped effect. A window on
    the economy chain is aimed by the control's schedule and the chain's
    draw count depends on its state, so a perturbation that moves the
    chain moves later addresses too; the row still reports what the arm
    did.

    ``horizon`` is the last day the arms run, inclusive, defaulting to the
    window's last day, or the target's day for a column. ``shard=(i, n)``
    keeps every n-th row from the i-th, for a caller spreading the arms
    over processes; each shard computes its own control.

    Cost: one run of the arms' days per row. A day of the market stream
    at forty names is forty-one company and market rows plus one per
    sector; a day of the jumps stream at forty names is forty-one
    uniforms, at two rows each, plus forty-one normals.
    """
    from .counterfactual import World, _days
    from ._core import ValidationError

    if not isinstance(world, World):
        raise ValidationError(
            f"attribute takes a World, got {type(world).__name__}")
    first, last = _days(window)
    if first < world.day:
        raise ValidationError(
            f"day {first} has run; this world is on day {world.day}. A "
            "draw already taken cannot be perturbed.")
    if granularity not in ("event", "day", "both"):
        raise ValidationError(
            f"granularity is 'event', 'day' or 'both', got {granularity!r}")
    if callable(target) and not isinstance(target, Target):
        target = Target("callable", fn=target)
    if not isinstance(target, Target):
        raise ValidationError(
            "target is noise.statistic(name), noise.pnl(), noise.column(...) "
            f"or a callable of the world, got {target!r}")
    if target.kind == "column" and int(target.day) < world.day:
        raise ValidationError(
            f"the target reads day {target.day}, which has run; this world "
            f"is on day {world.day}. A value fixed before the window cannot "
            "move with a draw inside it.")
    wanted = list(STREAMS if streams is None else streams)
    for s in wanted:
        DrawAddress(s, "uniform", 0).check()
    event_streams = [s for s in wanted if s in EVENT_STREAMS
                     and granularity in ("event", "both")]
    day_streams = [s for s in wanted if s == "market"
                   and granularity in ("day", "both")]
    traced = event_streams + day_streams
    if not traced:
        raise ValidationError(
            f"nothing to attribute: {granularity!r} over {wanted} names no "
            "stream. Event level covers " + ", ".join(EVENT_STREAMS)
            + "; day aggregate covers the market stream.")
    # An event lands at its day's close and is first seen at the next open,
    # so a horizon that stops on the window's last day gives every event row
    # nothing to move and every one of them measures exactly zero. The
    # default therefore reaches one day past the window whenever an event
    # stream is attributed. A caller who passes a horizon gets it as given,
    # and the caveats say what a column of zeros means under it.
    if horizon is None:
        horizon = last + 1 if event_streams else last
        if target.kind == "column" and int(target.day) > horizon:
            horizon = int(target.day)
    else:
        horizon = int(horizon)
    if horizon < last:
        raise ValidationError(
            f"the horizon (day {horizon}) is before the window's last day "
            f"({last}); the arms must run through the window.")
    days = horizon - world.day + 1
    # A statistic reads the daily bars, so the arms record; whether the run
    # is long enough for it is facts' to say, on the real bars.
    record = target.kind == "statistic"

    control, = world.fork("control")
    for s in traced:
        control.trace_draws(s, first, last)
    base = _run_arm(target, control, horizon, record)

    plan: list[tuple[dict, list[Patch]]] = []
    for s in event_streams:
        for entry in control.draws(s, first, last):
            head = {"stream": s, "kind": entry.address.kind,
                    "index": entry.address.index, "count": 1,
                    "day": entry.day, "site": entry.site, "tag": entry.tag,
                    "ticker": _ticker(world, entry.site, entry.tag),
                    "granularity": "event"}
            if entry.address.kind == "normal":
                plan.append((dict(head, perturbation="z+delta", delta=delta),
                             [Patch(entry.address, entry.value + delta)]))
            elif entry.site in EVENT_SITES:
                plan.append((dict(head, perturbation="fire", delta=0.0),
                             [Patch(entry.address, 0.0)]))
                plan.append((dict(head, perturbation="unfire", delta=1.0),
                             [Patch(entry.address, NO_FIRE)]))
            else:
                plan.append((dict(head, perturbation="u=0", delta=0.0),
                             [Patch(entry.address, 0.0)]))
                plan.append((dict(head, perturbation="u=1", delta=1.0),
                             [Patch(entry.address, NO_FIRE)]))
    for s in day_streams:
        groups: dict[tuple, list] = {}
        for entry in control.draws(s, first, last):
            role = DAY_SITES.get(entry.site)
            if role is None:
                continue
            key = (entry.day, entry.site, entry.tag if role != "market" else 0)
            groups.setdefault(key, []).append(entry)
        for (day, site, tag), entries in sorted(groups.items()):
            ticks = len(entries)
            step = delta / math.sqrt(ticks)
            head = {"stream": s, "kind": "normal",
                    "index": entries[0].address.index, "count": ticks,
                    "day": day, "site": site, "tag": tag,
                    "ticker": _ticker(world, site, tag),
                    "granularity": "day", "perturbation": "z+delta/sqrt(T)",
                    "delta": step}
            plan.append((head, [Patch(e.address, e.value + step)
                                for e in entries]))
    if shard is not None:
        i, n = int(shard[0]), int(shard[1])
        if n < 1 or not 0 <= i < n:
            raise ValidationError(f"shard is (i, n) with 0 <= i < n, got {shard!r}")
        plan = plan[i::n]

    rows: list[dict] = []
    # Common random numbers is the whole claim, so it is measured rather
    # than assumed: every arm's stream positions are compared against the
    # control's, on all seven streams. The economy chain is the one that
    # can break it, because its draw count depends on its own state.
    control_positions = control.engine.stream_positions()
    moved: list[str] = []
    for head, patches in plan:
        arm, = world.fork("arm")
        patch_draws(arm.engine, patches)
        value = _run_arm(target, arm, horizon, record)
        if arm.engine.stream_positions() != control_positions:
            moved.append(f"{head['stream']} {head['kind']} {head['index']} "
                         f"({head['perturbation']})")
        rows.append(dict(head, control=base, treatment=value,
                         effect=value - base))

    # The rows are single-draw finite differences through a market with
    # feedback, so they do not decompose the target and their sum is not
    # the joint effect. One more arm installs every changing patch at once
    # and the gap is measured rather than left to the reader: nine unfire
    # rows over an eight-name roster summed to -3.96 against a joint of
    # -3.85, a residual of 0.106.
    joint_patches: list[Patch] = []
    joint_rows: list[dict] = []
    seen: set = set()
    for (head, patches), row in zip(plan, rows):
        # a zero row is the control's own state, so it installs nothing
        if row["effect"] == 0.0:
            continue
        key = (row["stream"], row["kind"], row["index"])
        if key in seen:
            continue
        seen.add(key)
        joint_patches.extend(patches)
        joint_rows.append(row)
    joint_effect = 0.0
    interaction = 0.0
    if joint_patches:
        joint, = world.fork("joint")
        patch_draws(joint.engine, joint_patches)
        joint_effect = _run_arm(target, joint, horizon, record) - base
        interaction = joint_effect - sum(r["effect"] for r in joint_rows)

    caveats: list[str] = []
    if day_streams:
        caveats.append(
            "market stream rows are day aggregates: one name's factor "
            "normals on one day moved together by delta/sqrt(T). The day "
            "is the identified quantity; one tick normal is not.")
        caveats.append(
            "the market stream's stash and settlement uniforms drive "
            "microstructure and were not perturbed.")
    plan_caveats = row_caveats(rows, target=target, last=last,
                               horizon=horizon,
                               event_streams=event_streams)
    caveats.extend(plan_caveats)
    if horizon > last:
        caveats.append(
            f"the arms ran to day {horizon}, past the window's last day "
            f"{last}; each effect includes everything downstream of the "
            "draw through the horizon.")
    if target.kind == "pnl" or getattr(world.agent, "act", None) is not None:
        if not hasattr(world.agent, "state"):
            caveats.append(
                "the agent runs in every arm and publishes no state() hook; "
                "an agent whose decisions are not a function of what it "
                "sees puts its own noise into every effect.")
    if shard is not None:
        caveats.append(f"shard {shard[0]} of {shard[1]}: every "
                       f"{shard[1]}-th row from the {shard[0]}-th.")
    if "economy" in event_streams:
        caveats.append(
            "a window on the economy chain is aimed by the control's "
            "schedule, and the chain's draw count depends on its own "
            "state, so a perturbation that moves the chain moves the "
            "addresses after it and the rows below it are aimed at draws "
            "the arm did not take. Every arm's stream positions were "
            "compared against the control's, and the next caveat names "
            "any that moved.")
    if moved:
        caveats.append(
            f"{len(moved)} of {len(plan)} arms consumed a different number "
            "of draws from the control, so their rows are not a common "
            "random numbers comparison: " + ", ".join(moved[:5])
            + ("" if len(moved) <= 5 else ", and more."))
    else:
        caveats.append(
            f"all {len(plan)} arms matched the control's draw positions on "
            "all seven streams, so every effect is the draw's rather than "
            "a reshuffle.")
    dropped = [s for s in wanted if s not in traced]
    if dropped:
        caveats.append(
            ", ".join(dropped) + " named and not attributed: event level "
            "covers " + ", ".join(EVENT_STREAMS) + " and day aggregate "
            "covers the market stream.")
    counted_joint: list[str] = []
    if joint_patches:
        summed = sum(r["effect"] for r in joint_rows)
        counted_joint.append(
            f"the {len(joint_rows)} rows with a non-zero effect do not "
            "decompose the target. Installed together they move it by "
            f"{joint_effect!r}, against {summed!r} added row by row, an "
            f"interaction residual of {interaction!r}. Each row is a "
            "single-draw finite difference through a market with feedback, "
            "so the rows are not additive and no total is claimed for "
            "them.")
    caveats.extend(counted_joint)
    return Attribution(target=target, window=(first, last), horizon=horizon,
                       control=base, rows=rows, caveats=caveats, delta=delta,
                       joint=joint_effect, interaction=interaction,
                       joint_rows=len(joint_rows),
                       plan_caveats=plan_caveats + counted_joint)


def row_caveats(rows: Sequence[dict], *, target: Any, last: int,
                horizon: int, event_streams: Sequence[str]) -> list[str]:
    """The caveats whose text carries a count of rows.

    Separated from the rest so a caller that shards a plan across workers
    and merges the rows can state them over the merged set. A tool that
    took them from a one-row probe published "1 event uniforms were forced
    to each end" above a table holding 18 of them.

    ``rows`` is what :class:`Attribution` carries, from one call or from
    several merged. The other arguments describe the call the rows came
    from and are what makes a column of zeros legible.
    """
    out: list[str] = []
    fired = sum(1 for r in rows if r["perturbation"] == "fire")
    if fired:
        # Which row is the control's own state is readable only where
        # exactly one of the pair is zero. Where both are zero the pair
        # says nothing about the control, and at a horizon that has not
        # reached the effect every pair is in that state.
        pairs: dict = {}
        for r in rows:
            if r["perturbation"] in ("fire", "unfire"):
                key = (r["stream"], r["kind"], r["index"])
                pairs.setdefault(key, []).append(r["effect"])
        one_zero = sum(1 for v in pairs.values()
                       if len(v) == 2 and sum(1 for e in v if e == 0.0) == 1)
        both_zero = sum(1 for v in pairs.values()
                        if len(v) == 2 and all(e == 0.0 for e in v))
        text = (f"{fired} event uniforms were forced to each end of the "
                "unit interval (fire, unfire) rather than perturbed.")
        if one_zero:
            text += (f" On {one_zero} of them exactly one row is zero, "
                     "which is the control's own state.")
        if both_zero:
            text += (f" On {both_zero} both rows are zero, so neither "
                     "names the control's state.")
        out.append(text)
    odd = sum(1 for r in rows if r["perturbation"] in ("u=0", "u=1"))
    if odd:
        out.append(
            f"{odd} uniforms outside the event sites were forced to 0 and "
            "to 1; which end is which event is their consumer's to say.")
    # An event lands at its day's close and is first seen at the next open,
    # so a column of exact zeros usually means the measurement stopped
    # before the effect rather than that the draws did nothing. Which one
    # stopped it is computed here rather than guessed at by the reader.
    for s in event_streams:
        stream_rows = [r for r in rows if r["stream"] == s]
        if not stream_rows or any(r["effect"] != 0.0 for r in stream_rows):
            continue
        column_target = getattr(target, "kind", None) == "column"
        if column_target and int(target.day) <= last:
            why = (f"the target reads day {target.day}, and an event of "
                   f"day {target.day} or later is first seen at the open "
                   "after it, so no draw in the window can reach this "
                   "value")
        elif horizon <= last:
            why = (f"the horizon is day {horizon} and the window ends on "
                   f"day {last}, so the last day's events are never seen")
        else:
            why = ("the horizon reaches past the window, so this is the "
                   "draws saying nothing rather than the measurement "
                   "stopping short")
        out.append(f"every {s} row measured exactly zero: {why}.")
    return out


def _ticker(world: Any, site: str, tag: int) -> str | None:
    if site in ("factor_idio_z", "stash_u", "settle_u", "jump_company_u",
                "jump_company_z", "volume_idio_z", "news_u", "news_z"):
        tickers = world.engine.tickers
        return tickers[tag] if 0 <= tag < len(tickers) else None
    return None


def _run_arm(target: Target, arm: Any, horizon: int, record: bool) -> float:
    """Run one arm to ``horizon``, reading the target where it is defined.

    Every target but a column is a property of the whole run and is read at
    the horizon. A column names a day, so the arm stops there, the column
    is read, and the remaining days run afterwards. Reading at the horizon
    instead reported a later day's value under the target's own label.
    """
    if target.kind == "column":
        to_target = int(target.day) - arm.day + 1
        if to_target > 0:
            arm.run(to_target, record=record)
        value = _evaluate(target, arm)
        rest = horizon - arm.day + 1
        if rest > 0:
            arm.run(rest, record=record)
        return value
    days = horizon - arm.day + 1
    if days > 0:
        arm.run(days, record=record)
    return _evaluate(target, arm)


def _evaluate(target: Target, arm: Any) -> float:
    from ._core import ValidationError

    if target.kind == "pnl":
        return float(arm.summary()["pnl_since"])
    if target.kind == "callable":
        return float(target.fn(arm))
    if target.kind == "column":
        values = struct.unpack(f"<{len(arm.engine.tickers)}d",
                               arm.engine.column(target.name))
        if target.ticker is None:
            return float(sum(values) / len(values))
        try:
            return float(values[arm.engine.tickers.index(target.ticker)])
        except ValueError:
            raise ValidationError(
                f"{target.ticker!r} is not in this world's roster")
    if target.kind == "statistic":
        from . import facts
        stats = facts.panel_statistics(arm.engine.bars(grain="day"), arm.universe)
        if target.name not in stats:
            raise ValidationError(
                f"{target.name!r} is not a panel statistic; one of "
                + ", ".join(sorted(stats)))
        value = stats[target.name]
        if value is None:
            raise ValidationError(
                f"{target.name} could not be measured on this run; facts "
                "returns None where a statistic has no observations")
        return float(value)
    raise ValidationError(f"unknown target kind {target.kind!r}")


__all__ = ["DrawAddress", "Patch", "LoggedDraw", "DayResult", "SITES",
           "STREAMS", "KINDS", "NO_FIRE", "EVENT_SITES", "EVENT_STREAMS",
           "Target", "Attribution", "patch_draws", "draw_log",
           "run_day_with", "market_day_layout", "surgery_patches",
           "attribute", "statistic", "pnl", "column"]
