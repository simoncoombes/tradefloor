"""One name's day, from its move down to the draws that seeded it.

``Engine.explain(ticker, day)`` returns a tree. The root is the day's log
move in the printed price, its children are the contributions that make
that move up, each contribution names the Rust that computed it and the
state and dials that Rust read at the open, and each mechanism's leaves
are the addresses of the draws it consumed. Every node is replayable: a
fork of the engine as it stood before the day opened, the node's logged
draw values installed at their addresses, the day's own inputs replayed,
and the node's quantity measured again on that run.

## What has to be on before it works

``Engine.keep_explanations(from_day, to_day)`` turns on two records over
those days: the draw log on every stream, and a copy of the engine at
each day's open. Both are read-only, so a market with the window open is
the market it would have been without one. ``explain`` on a day outside
the window raises and names the days that were kept.

A window costs one engine copy per day kept, and a fork pays it again
per arm. Measured as the PROCESS peak working set, which is the figure
that sees the store, at forty names over thirty days on the same box and
with ``record=True``: 436 to 457 MB with a thirty-day window against 89
to 92 MB with none, the lower of each pair the reviewer's and the higher
this author's. A fork of a sixty-day window takes 0.13 seconds.

Recorded because ``explain`` reads the day off ``truth()``, so a window
is only useful on a run that recorded. The same pair unrecorded is 333
and 28 MB, and quoting those would describe a configuration this feature
refuses: 61 MB of the difference is the tape itself. Ask for the days
you mean to explain.

``explain`` also needs pyarrow, because the tree's contributions are the
``truth()`` table's columns and that table arrives as an Arrow stream. A
default install raises from the first call with the extra to install; the
library itself still imports nothing.

## The tree

The root is the move, ``log(close / previous close)``, with the close
read from the replayed day and the previous close from the copy taken
before the open.

Its children are twelve contributions, of kind ``factor``. Ten are the
``truth()`` columns for the name on that day, in ``Engine.FACTORS``
order, and they sum to the day's change in ``mispricing_s``. Two more
close the arithmetic: ``fair_value`` is the day's change in log
fundamental value and ``book`` is the change in the log distance from the
model price to the print. All twelve are measured, so their sum against
the move is an identity the engine can fail;
:meth:`Explanation.check` states the residual rather than asserting it.
Where the day before is not on the tape its closing levels are unknown,
``fair_value`` reads zero and ``book`` is what the mispricing leaves
over, which the caveats say.

``fair_value`` is zero on a day the valuation holds still, which is most
days of most presets: earnings and the sector anchor are fixed for a run,
the QE channel is off, and the discount rate moves only on the days the
corporate bond yield does. It moves under a scenario that moves the macro
path, and ``tests/test_explain.py`` measures one. Under pt-v18 it moves
on every day, because ``earnings_nominal_growth`` restates earnings and
book value in the nominal output the economy integrates. That output is
``gdp`` times ``cpi``, and ``Engine.macro_fields`` carries neither level,
so the table below declares the dial and not the two levels behind it.

Under each contribution sits one ``mechanism`` node, named for the Rust
function that computes it, carrying the dials that function reads as its
``inputs``. Under the mechanism sit the state it read at the open, one
``state`` node per engine column, and the draws it consumed, one ``draw``
node per site with every address of that site for that day. A mechanism
with no draw of its own has no ``draw`` child, and
:attr:`Explanation.caveats` names those mechanisms.

## What it measures, and what it does not

It measures which draws and which state the recorded move came from,
under the engine's own attribution. It cannot say what the move would
have been WITHOUT a draw. That is a counterfactual and it needs an arm
per draw, which is :func:`tradefloor.noise.attribute`;
:meth:`Explanation.render` names it. Nothing here re-implements a
formula: a replay is the engine running the same day from the same state
under the same draws, which is what ``CONTRIBUTING.md`` requires of
anything that decides a price.

## What a call costs

What a call does is fixed and what it takes in wall time is not, so the
counts come first. A ``check()`` runs the day once per DISTINCT overlay
rather than once per node, since a replay is a function of its patch set:
19 runs, whatever the roster, the 15 before the overnight contribution
and its three sites plus their union. The tree is 63 nodes where
``Engine.prints()`` splits the book contribution and 61 where the build
has no print table, the 55 and 53 before plus the overnight
contribution's eight, and the addressed draws under one name are 2,739 at
every roster size, the 2,736 before plus one per overnight site. What
grows is the log the call reads, because the market stream's log is the
size of the tape at 613 of that stream's draws a tick.

On ``Universe.random(n, seed=111)`` at engine seed 42, three days,
``pt-v16``, at 099eae7, on one Windows 11 box, three repetitions:

===== ========= ========== ==========
names    logged     peak A     peak B
===== ========= ========== ==========
   12    66,400      31 MB      28 MB
   40   197,664      92 MB      83 MB
  100   478,944     220 MB     200 MB
===== ========= ========== ==========

Peak here is PYTHON-SIDE allocation, from ``tracemalloc``, over one
``explain`` and one ``check``. It counts the draw log the call
materialises and not the engine copies the store holds, which are Rust
memory ``tracemalloc`` cannot see and which report as zero to it. The
window's own cost below is a process figure and the two are not
comparable.

Two columns because two readers measured that same quantity on the same
machine and got answers ten per cent apart, each repeating to the tenth
of a megabyte across its own runs. A is this author's and B is the
reviewer's. Neither is quoted as the number.

No wall time is quoted at all. The same two readers measured a
``check()`` at a third of an ``explain`` and at 1.1 to 1.3 times one, and
a ratio a reader would take as a fact is worse than no number. The counts
above are what both reproduced exactly. A filtered read on the extension
side would remove most of what a call holds, and this build does not have
one.

Keeping a window costs a run a copy of the engine per kept day. Its cost
in time is inside the noise at this size: over three repetitions at a
hundred names for twenty days, a windowed run took 0.95, 1.02 and 1.56
times an unwindowed one, so the measurement says the overhead is smaller
than what else the machine is doing.

## The jump slot's day

``Engine::apply_jumps`` runs at a close and moves ``mispricing_s`` after
the tick loop, so the jump a day's ``truth()`` table carries was drawn at
the close of the day BEFORE it, and the draw log stamps those draws with
that earlier day. ``keep_explanations`` therefore starts the draw log one
day early, and the jump node's addresses are read from ``day - 1``.
"""
from __future__ import annotations

import json
import math
import struct
from typing import Any, NamedTuple, Sequence

from . import noise
from ._core import Engine, ValidationError
from .noise import DrawAddress, Patch


class Mechanism(NamedTuple):
    """What produced one contribution, and what it read to do it.

    ``function`` is the Rust that computes the contribution, as a path
    into ``rust/src``. ``via`` names further Rust that the same
    contribution passes through, which is where a dial read by the caller
    and handed on as an argument is found: ``compute_fair_value_with``
    takes the book floor and the two QE gains as arguments, so those
    names are in the tick that calls it. ``tests/test_explain.py`` checks
    every name below against the source of the function it is declared
    on, so a dial or a field this table names is one that Rust reads.

    ``state`` are engine columns, read from the copy taken before the
    day's open. ``macro`` are macro fields, read from the same copy.
    ``dials`` are ``ModelParams`` keys. ``sites`` are
    ``(stream, site, scope)`` triples from :data:`tradefloor.noise.SITES`,
    where the scope says which tag belongs to the name being explained:
    ``company`` its own index, ``sector`` its sector's index, ``market``
    every tag the site has. ``offset`` is the day the draws are logged
    under, relative to the day being explained.
    """

    factor: str
    function: str
    state: tuple[str, ...] = ()
    macro: tuple[str, ...] = ()
    dials: tuple[str, ...] = ()
    sites: tuple[tuple[str, str, str], ...] = ()
    via: tuple[str, ...] = ()
    offset: int = 0


#: The Rust that produced each contribution. The ten ``truth()`` columns
#: in ``Engine.FACTORS`` order, then the two that close the arithmetic
#: between the mispricing decomposition and the tape.
MECHANISMS: tuple[Mechanism, ...] = (
    Mechanism(
        factor="reversion",
        function="market::tick::simulate_market_tick",
        state=("mispricing_s",),
        dials=("s_phi_tick",),
    ),
    Mechanism(
        factor="momentum",
        function="market::tick::simulate_market_tick",
        state=("mispricing_momentum",),
        dials=("momentum_theta",),
    ),
    Mechanism(
        factor="crowd_lean",
        function="market::tick::simulate_market_tick",
        state=("mispricing_s", "mispricing_momentum", "beta"),
        macro=("vix",),
        dials=("crowd_valuation_gain", "crowd_momentum_gain",
               "crowd_lean_cap", "forced_flow_gain",
               "forced_flow_threshold", "forced_flow_beta_exponent"),
        via=("mispricing::crowd_lean_with",),
    ),
    Mechanism(
        factor="company_news",
        function="market::factors::calculate_live_factors",
        dials=("news_sector_weight", "news_market_weight",
               "news_peer_weight", "news_peer_weight_down",
               "news_peer_vix_coupling", "endogenous_news_intensity",
               "endogenous_news_sigma"),
        sites=(("news", "news_u", "company"),
               ("news", "news_z", "company")),
        via=("engine::Engine::open_market",),
    ),
    Mechanism(
        factor="order_flow_impact",
        function="market::factors::calculate_live_factors",
        state=("avg_volume",),
        dials=("order_flow_coefficient", "informed_flow_fraction",
               "order_flow_impact_law"),
        # `order_flow_impact_law` is read where the imbalance is COMPUTED and
        # not where it is consumed: the tick calls `order_imbalance_with` and
        # hands `calculate_live_factors` the product. That is the mirror of
        # the case this field was added for, where a dial is read by the
        # caller and passed in as an argument.
        via=("market::factors::order_imbalance_with",),
    ),
    Mechanism(
        factor="short_squeeze_effect",
        function="market::factors::calculate_live_factors",
        state=("short_interest", "last_daily_return"),
    ),
    Mechanism(
        factor="random_noise",
        function="market::factors::calculate_live_factors",
        state=("garch_variance", "beta", "market_cap"),
        macro=("vix",),
        dials=("idio_sigma_scale", "idio_sigma_beta_exponent",
               "sector_loading", "sector_loading_beta_slope",
               "market_factor_sigma", "market_beta_down_asym",
               "crisis_blend_source", "crisis_blend_gain",
               "crash_amplifier_slope", "crash_amplifier_threshold"),
        sites=(("market", "market_factor_z", "market"),
               ("market", "sector_z", "sector"),
               ("market", "factor_idio_z", "company")),
        # The sector loading and the idiosyncratic scale moved out of the
        # factor function into two helpers the overnight move shares.
        via=("market::tick::simulate_market_tick",
             "market::factors::sector_loading_for",
             "market::factors::idio_scale_for"),
    ),
    Mechanism(
        factor="circuit_breaker",
        function="market::tick::simulate_market_tick",
        state=("price",),
        # The band multipliers the tick compares against are DERIVED once
        # in the params constructor and are not in the dict, so the dial a
        # caller sets is the fraction they are derived from.
        dials=("price_breaker_fraction", "price_hard_cap"),
        via=("params::ModelParams::with_override",),
    ),
    Mechanism(
        factor="jump",
        function="engine::Engine::apply_jumps",
        state=("mispricing_s", "mispricing_s_prev_close"),
        macro=("vix",),
        dials=("jump_intensity_market", "jump_intensity_idio",
               "jump_mean_market", "jump_sigma_market", "jump_sigma_idio",
               "jump_vix_coupling", "jump_momentum_share",
               "market_vol_vix_anchor"),
        sites=(("jumps", "jump_market_u", "market"),
               ("jumps", "jump_market_z", "market"),
               ("jumps", "jump_company_u", "company"),
               ("jumps", "jump_company_z", "company")),
        offset=-1,
    ),
    Mechanism(
        factor="overnight",
        function="engine::Engine::apply_overnight",
        state=("mispricing_s", "mispricing_s_prev_close", "price"),
        dials=("overnight_variance_ratio",),
        sites=(("overnight", "overnight_market_z", "market"),
               ("overnight", "overnight_sector_z", "sector"),
               ("overnight", "overnight_idio_z", "company")),
        via=("market::factors::sector_loading_for",
             "market::factors::idio_scale_for",
             "market::tick::sector_sigma_for"),
    ),
    Mechanism(
        factor="fair_value",
        function="fair_value::compute_fair_value_with",
        macro=("qe_pe_boost",),
        dials=("fair_value_book_floor", "qe_pe_gain", "qe_pe_stock_gain",
               "earnings_nominal_growth"),
        via=("market::tick::simulate_market_tick",
             "market::tick::nominal_scale",
             "fair_value::compute_target_pe"),
    ),
    Mechanism(
        factor="book",
        function="microstructure::settle_price_through_book",
        state=("price",),
        macro=("vix",),
        sites=(("market", "settle_u", "company"),),
    ),
)

#: The contributions the root carries, in order: the ten ``truth()``
#: columns and the two that close the arithmetic to the printed move.
#:
#: Named for what they are rather than ``FACTORS``, which is what
#: ``Engine.FACTORS`` calls the ten. Two names for two different lists
#: is a trap for anyone importing both.
CONTRIBUTIONS: tuple[str, ...] = tuple(m.factor for m in MECHANISMS)

#: How the book contribution splits when ``Engine.prints()`` is on the
#: build, as ``(name, Rust function)`` in the order they are reported.
#:
#: The three are per-tick sums off the print table and their parent is a
#: change in a level, so they are not a re-split of it in any obvious
#: sense; that they add up to it is arithmetic worth stating. Writing A
#: for the anchor's move, which is the other ten contributions, the
#: identity is that summed shock plus summed absorbed telescopes to the
#: printed move, so summed absorbed plus (summed shock minus A) is the
#: move minus A, which is the book contribution. Each is measured on its
#: own rather than one being taken as what the others leave over.
#:
#: The third has a closed form: summed shock minus A works out to minus
#: the sum of the log distances from the anchor to the print, taken at
#: the tick BEFORE each settlement. Each tick measures its shock from the
#: last print, so wherever the tape sits away from the model that gap
#: enters the shock and leaves again through absorbed.
DEPTH: tuple[tuple[str, str], ...] = (
    ("order_book", "microstructure::settle_price_through_book"),
    ("circuit_breaker_two", "market::tick::simulate_market_tick"),
    ("anchor_pull", "microstructure::decompose"),
)

#: The kinds a node can be.
KINDS = ("move", "factor", "mechanism", "state", "draw")

#: How close the twelve contributions have to come to the move before
#: :meth:`Explanation.check` calls it a miss, and how close a replayed
#: value has to come to the recorded one. The truth test holds the
#: decomposition to 1e-15 over one day's rows; this is the same order,
#: loosened for the eleven-term sum and the two logs the move is taken
#: through.
TOLERANCE = 1e-12


class Node(NamedTuple):
    """One node of an explanation.

    ``kind`` is one of :data:`KINDS`. ``name`` is a ``truth()`` column, a
    Rust function, an engine column or a draw site, by kind. ``value`` is
    the quantity the node states, in log price units for a ``move``,
    ``factor`` or ``mechanism`` node and in the field's own units for a
    ``state`` node; a ``draw`` node states the sum of the values its
    addresses delivered. ``addresses`` are the draws under this node
    alone, and ``children`` the nodes under it.

    ``inputs`` differs by kind rather than being one thing, and the
    Arrow table's ``inputs`` column is this map as JSON, so a reader
    taking it for one thing reads a draw's count as a dial. A
    ``mechanism`` node's inputs are the dials that function reads, which
    is the case the name was written for. The ``move`` node's are the
    close, the previous close and the sum of the contributions under it.
    A ``draw`` node's are how many draws it holds and the day they were
    logged under. A ``factor`` node's and a ``state`` node's are empty.
    The macro fields a mechanism read are ``state`` children beside the
    engine columns rather than inputs.
    """

    kind: str
    name: str
    value: float
    inputs: dict[str, float]
    addresses: tuple[DrawAddress, ...]
    children: tuple["Node", ...]


class _Day(NamedTuple):
    """One run of the day being explained, measured.

    ``factors`` is the day's ``truth()`` column sums for the name,
    ``state`` the columns and macro fields read from the copy the day
    started from, and ``levels`` the day's closing fundamental value and
    anchor price, which the next day's decomposition reads.
    """

    move: float
    factors: dict[str, float]
    state: dict[str, float]
    levels: dict[str, float]
    #: The book contribution's three readings off ``prints()``, or an
    #: empty map on a build without it.
    depth: dict[str, float]


def _count(n: int, noun: str) -> str:
    """``n`` of ``noun``, pluralised. A caveat that says "1 streams" reads
    as a template rather than as a measurement, and the reader stops
    trusting the number in front of it."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _f64(buf: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(buf) // 8}d", buf)


def _column(engine: Engine, field: str) -> tuple[float, ...]:
    return _f64(engine.column(field))


def _record_label(inputs: Sequence[dict], day: int) -> int:
    """The label the run's own ``record`` gave this day, or ``day``.

    The store keys an open by the day the engine stamped it with, and
    ``record`` takes a label of the caller's choosing. Every path the
    library drives passes the same number to both, so the two agree; a
    hand loop can pass anything. The tape is keyed by the label, so a
    tape read at the store's key would be a different day's rows, with
    the right shape and the wrong values.
    """
    for entry in inputs:
        if entry["op"] == "record":
            return int(entry["day"])
    return int(day)


def _the_slot_names_the_name(roster: Sequence[str], slot: int,
                             ticker: str, whose: str) -> None:
    """Refuse a slot that does not hold the name it was resolved for.

    An invariant rather than a test, because every claim this module
    makes resolves the slot the same way and so agrees with a wrong one:
    a tree built at another company's slot is a self-consistent
    description of that company under this name, and check() reports
    nothing. Written once here and called wherever a slot is resolved.
    """
    if not 0 <= slot < len(roster) or roster[slot] != ticker:
        raise ValidationError(
            f"slot {slot} of {whose} holds "
            f"{roster[slot] if 0 <= slot < len(roster) else 'nothing'!r} "
            f"and was resolved for {ticker!r}. Every column, tag and tape "
            "row this explanation reads is at that slot, so it would "
            "describe another company under this name.")


def _levels(engine: Engine, day: int, index: int) -> dict[str, float] | None:
    """The day's closing fundamental value and anchor price for one name.

    ``None`` when the day is not on this engine's tape. Both are read at
    the name's last row of the day, which is its close.

    The count of recorded days is checked first, because ``truth`` on an
    engine that recorded NOTHING falls back to the last session and takes
    the ``day`` argument as the label on those rows. Read without the
    guard, a run that never recorded answered a question about day 1 with
    the last day it ran.
    """
    # The day before day zero does not exist, and `truth` takes an
    # unsigned day, so asking for it raises out of the extension rather
    # than answering. Refused here, where the answer is "no tape".
    if int(day) < 0 or engine.recorded_days == 0:
        return None
    try:
        stream = engine.truth(day=int(day))
    except ValidationError:
        return None
    table = _table(stream)
    ids = table["instrument_id"]
    rows = [k for k in range(len(ids)) if ids[k] == index]
    if not rows:
        return None
    last = rows[-1]
    return {"fundamental_value": float(table["fundamental_value"][last]),
            "anchor_price": float(table["anchor_price"][last])}


def _table(stream: Any) -> dict[str, list]:
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover - exercised by the extra
        raise ImportError(
            "explain reads the truth table, which arrives as an Arrow "
            "stream, so it needs pyarrow. Install it with: pip install "
            "tradefloor[arrow]") from exc
    return pa.table(stream).to_pydict()


def _explain(engine: Engine, ticker: str, day: int, sector: str,
             window: tuple[int, int] | None, kept: Sequence[int],
             opened: Engine | None, inputs: Sequence[dict] | None,
             before: Sequence[str] | None = None,
             ops: Sequence[tuple[str, str]] = ()) -> "Explanation":
    """Build the explanation ``Engine.explain`` returns.

    Called from the binding, which owns the store and hands over what it
    holds: the engine, the name's sector, the window asked for, the days
    kept, the copy taken before this day's open and this day's own
    inputs. The sector arrives as its KEY and the tag is derived here
    from :func:`tradefloor.sectors`, because a tag the binding worked out
    and handed over would be one derivation used twice, and the check on
    it would agree with a wrong answer.
    """
    if opened is None or inputs is None:
        if ticker not in engine.tickers:
            raise ValidationError(
                f"{ticker!r} is not in this engine's roster, which starts "
                f"{list(engine.tickers[:8])}")
        if window is None:
            raise ValidationError(
                f"day {day} has no explanation kept, and none was asked "
                "for. Call Engine.keep_explanations(from_day, to_day) "
                "before running those days.")
        held = ", ".join(str(d) for d in kept) if kept else "none"
        raise ValidationError(
            f"day {day} has no explanation kept. The window asked for is "
            f"days {window[0]} to {window[1]} and the days kept are "
            f"{held}. A day is kept at its open, so a window opened after "
            "the day ran cannot reach it.")
    # Against the roster the DAY had, which the copy carries, rather than
    # the roster the engine has now. A delisting shifts every slot below
    # it, so a name resolved against the current roster addresses another
    # company in a day kept before the change and every read follows it:
    # the state columns, the tape's instrument_id filter and the company
    # tag on the draws. A name delisted since is still explainable on a
    # day it traded; a name listed since was not there to explain.
    if ticker not in opened.tickers:
        raise ValidationError(
            f"{ticker!r} was not on the roster when day {day} opened, "
            f"which held {len(opened.tickers)} names starting "
            f"{list(opened.tickers[:8])}. A name listed after a day "
            "cannot be explained on it.")
    return Explanation(engine=engine, ticker=ticker, day=int(day),
                       sector=sector, window=window,
                       kept=tuple(int(d) for d in kept),
                       opened=opened, inputs=tuple(inputs),
                       before=None if before is None else tuple(before),
                       ops=tuple(tuple(op) for op in ops))


class Explanation:
    """One name's day, decomposed and replayable.

    Built by ``Engine.explain``. Every number on it is measured when it
    is built: the day is run again from the copy taken before its open,
    under the inputs the run log holds for it, and the tree is read off
    that run. :meth:`replay` runs it again under one node's draws and
    :meth:`check` does that for every node and reports what did not come
    back.
    """

    #: The columns of :meth:`to_arrow`, in order, with their Arrow types.
    COLUMNS = (("path", "string"), ("parent", "string"), ("depth", "int64"),
               ("kind", "string"), ("name", "string"),
               ("value", "float64"), ("stream", "string"),
               ("draws", "int64"), ("first_index", "int64"),
               ("inputs", "string"))

    def __init__(self, *, engine: Engine, ticker: str, day: int,
                 sector: str, window: tuple[int, int] | None,
                 kept: tuple[int, ...], opened: Engine,
                 inputs: tuple[dict, ...],
                 before: tuple[str, ...] | None = None,
                 ops: tuple[tuple[str, str], ...] = ()) -> None:
        self.ticker = ticker
        self.day = int(day)
        self._engine = engine
        self._window = window
        self._kept = kept
        self._opened = opened
        self._inputs = inputs
        self._index = opened.tickers.index(ticker)
        # The slot names the name, checked here rather than tested for.
        # Every read downstream is at this index: the state columns, the
        # tape's instrument filter, the previous close and the company
        # tag on the draws. All four of check()'s claims resolve it the
        # same way, so all four agree with a wrong slot, and two tests
        # catch only the roster shapes they build. This fires on every
        # call instead.
        _the_slot_names_the_name(opened.tickers, self._index, ticker,
                                 f"the copy day {day} was kept from")
        # From the library's published order rather than from a number
        # the binding computed. The data has one origin, since
        # `tradefloor.sectors()` wraps the same array the engine reads
        # and an engine refuses an unknown sector at construction, so the
        # two cannot disagree. What changes is that the tag is now
        # RECOMPUTABLE from published values: a check can derive it from
        # the roster and that order, both of which it reads independently
        # of the code under it, where before it could only read back the
        # integer the binding handed over.
        from . import sectors as _sector_keys

        order = list(_sector_keys())
        if sector not in order:
            raise ValidationError(
                f"{ticker!r} is in sector {sector!r}, which is not one of "
                "the twelve tradefloor.sectors() publishes: "
                + ", ".join(order))
        self._sector_key = sector
        self._sector = order.index(sector)
        #: The roster the day ran under, and whether it is still the
        #: engine's. Read from the copy, so a delisting since the day is
        #: visible to the caveats rather than silent.
        self._roster = tuple(opened.tickers)
        self._roster_moved = self._roster != tuple(engine.tickers)
        #: The label this day's rows carry on the tape, which is the one
        #: the run's own `record` call gave them. It is the day the store
        #: keyed the open by on every path the library drives, and a hand
        #: loop can give a day any label it likes; the tape is read at the
        #: label and the caveats name both when they differ.
        self._label = _record_label(inputs, self.day)
        self._dials = dict(engine.model_params)
        #: What every logged draw of the day delivered, by address. The
        #: values a replay installs, so a replay of a node reproduces the
        #: run rather than perturbing it.
        self._logged, self._traced = _logged_draws(engine, self.day)
        #: The roster the day before opened on, where that day was kept,
        #: and how the two days were compared. Rosters name the slot the
        #: name held then, which is what the previous day's tape is keyed
        #: by; widths only say the roster is the same size, and a listing
        #: paired with a delisting in one day leaves the width alone
        #: while moving every slot between them.
        self._before = before
        #: Every roster operation the run log holds between the previous
        #: day's open and this one. The log is what KNOWS: a width
        #: comparison misses a listing paired with a delisting, and a
        #: roster comparison needs the day before to have been kept.
        self._ops = tuple(ops)
        self._compared = ("rosters" if before is not None
                          else "the run log")
        self._previous_index, self._previous_moved = self._slot_before(
            engine, ticker)
        self._previous = (
            None if self._previous_index is None
            else _levels(engine, self._label - 1, self._previous_index))
        #: Each draw node's values, in the order the log delivered
        #: them, keyed by the node. A replay installs these against
        #: the node's own addresses, which is what makes a
        #: mis-addressed node change the day it replays.
        self._draw_values: dict[int, tuple[float, ...]] = {}
        #: The three readings under the book contribution, by node. A
        #: node here states a quantity the day recomputes, so a replay
        #: reports it rather than the contribution above it, which keeps
        #: a leaf replaying to its own parent.
        self._depth_nodes: dict[int, str] = {}
        self._runs: dict[tuple, _Day] = {}
        base = self._run(())
        self._base = base
        self.move = base.move
        self.root = self._tree(base)
        self._walk = tuple(_walk(self.root, "", None))
        self._by_path = {path: node for path, node, _ in self._walk}
        self._recorded = _recorded_factors(engine, self._label,
                                           self._index)
        #: The close the run itself printed on this day, where its tape
        #: holds one. The nine columns agreeing says the mispricing path
        #: was rebuilt; the print is settled through the book after that,
        #: so it is compared on its own.
        self._recorded_close = _recorded_close(engine, self._label,
                                               self._index)
        self.caveats = self._caveats()

    def _slot_before(self, engine: Engine,
                     ticker: str) -> tuple[int | None, bool]:
        """Where this name sat on the day before, and whether it moved.

        The previous day's tape is keyed by the slots that day had, so
        the levels are read at the slot the name held THEN rather than at
        the slot it holds now. Where the day before was kept its copy
        carries that roster and the answer is exact; a name absent from
        it has no previous levels at all. Where it was not kept, the
        tape's instrument counts are all there is, and equal counts do
        not prove the roster held: a listing paired with a delisting in
        one day leaves the count alone and moves every slot between them.
        The caveats say which of the two was done.
        """
        if self._before is not None:
            if ticker not in self._before:
                return None, True
            slot = list(self._before).index(ticker)
            _the_slot_names_the_name(self._before, slot, ticker,
                                     f"the copy day {self.day - 1} was "
                                     "kept from")
            return slot, slot != self._index
        # No copy to compare against, so the run log decides. It records
        # every listing and delisting with its position, so an operation
        # between the two opens is a fact rather than an inference, and
        # the levels are refused rather than read at a slot that may name
        # another company.
        #
        # A width comparison stood here as a second check and is gone.
        # A roster moves only through `list_instrument` or `delist`, the
        # run log records both, and the log is asked first, so the widths
        # could only ever agree with the answer already given. That is a
        # claim about the log being complete, which is checkable against
        # it. An earlier draft of this comment said instead that nobody
        # could construct the state reaching the branch, which was
        # measured and withdrawn: the branch was entered on the ordinary
        # path below and only its refusing arm was unreachable. A wrong
        # reason beside a right change is how the change gets undone.
        if self._ops:
            return None, True
        return self._index, False

    # -- the tree ---------------------------------------------------------

    def _tree(self, base: _Day) -> Node:
        children = tuple(self._factor(m, base) for m in MECHANISMS)
        total = math.fsum(child.value for child in children)
        return Node(
            kind="move", name=self.ticker, value=base.move,
            inputs={"close": base.levels["close"],
                    "previous_close": base.levels["previous_close"],
                    "contributions": total},
            addresses=(), children=children)

    def _factor(self, mech: Mechanism, base: _Day) -> Node:
        value = base.factors[mech.factor]
        children = (self._mechanism(mech, value, base),)
        if mech.factor == "book" and base.depth:
            # The book's own share keeps the settlement's state and draws;
            # the other two are readings of the same print table and carry
            # neither, since neither takes a draw of its own.
            first, rest = children[0], []
            for name, function in DEPTH[1:]:
                node = Node(kind="mechanism", name=function,
                            value=base.depth[name], inputs={},
                            addresses=(), children=())
                self._depth_nodes[id(node)] = name
                rest.append(node)
            first = first._replace(value=base.depth[DEPTH[0][0]])
            self._depth_nodes[id(first)] = DEPTH[0][0]
            children = (first, *rest)
        return Node(kind="factor", name=mech.factor, value=value,
                    inputs={}, addresses=(), children=children)

    def _mechanism(self, mech: Mechanism, value: float,
                   base: _Day) -> Node:
        dials = {name: float(self._dials[name]) for name in mech.dials
                 if name in self._dials}
        state = tuple(
            Node(kind="state", name=name, value=base.state[name],
                 inputs={}, addresses=(), children=())
            for name in mech.state + mech.macro if name in base.state)
        draws = tuple(self._draws(mech))
        return Node(kind="mechanism", name=mech.function, value=value,
                    inputs=dials, addresses=(),
                    children=state + draws)

    def _draws(self, mech: Mechanism) -> list[Node]:
        out: list[Node] = []
        for stream, site, scope in mech.sites:
            tag = {"company": self._index, "sector": self._sector,
                   "market": None}[scope]
            entries = [entry for entry in
                       self._logged.get((stream, self.day + mech.offset), ())
                       if entry.site == site
                       and (tag is None or entry.tag == tag)]
            if not entries:
                continue
            node = Node(
                kind="draw", name=site,
                value=math.fsum(entry.value for entry in entries),
                inputs={"count": float(len(entries)),
                        "day": float(self.day + mech.offset)},
                addresses=tuple(entry.address for entry in entries),
                children=())
            # The values a replay installs, kept in the order the log
            # delivered them rather than re-read at the address they are
            # installed at. Read back at the address, a patch is the value
            # that address already delivers and the overlay is a no-op
            # whatever the address is, so a node addressing the wrong draw
            # replayed clean and `check()` could not fail on it.
            self._draw_values[id(node)] = tuple(entry.value
                                                for entry in entries)
            out.append(node)
        return out

    # -- running the day again --------------------------------------------

    def _run(self, patches: tuple[Patch, ...]) -> _Day:
        """Run the day once under ``patches`` and measure it.

        Keyed by the patch set, because a replay is a function of it: the
        fork, the inputs and the day are the same on every call here, so
        two nodes whose draws are the same install the same overlay and
        get the same day. That is what keeps :meth:`check` to one run per
        distinct overlay rather than one per node.
        """
        key = tuple(sorted((tuple(p.address), p.value) for p in patches))
        if key in self._runs:
            return self._runs[key]
        fork, = self._opened.fork(1)
        if patches:
            noise.patch_draws(fork, patches)
        _replay_inputs(fork, self._inputs, self._label)
        measured = self._measure(fork)
        self._runs[key] = measured
        return measured

    def _measure(self, fork: Engine) -> _Day:
        """Read the day off a run of it."""
        i = self._index
        previous_close = _column(self._opened, "price")[i]
        close = _column(fork, "price")[i]
        move = math.log(close / previous_close)
        table = _table(fork.truth(day=self._label))
        ids = table["instrument_id"]
        rows = [k for k in range(len(ids)) if ids[k] == i]
        factors = {name: math.fsum(table[name][k] for k in rows)
                   for name in Engine.FACTORS}
        mispricing = math.fsum(factors.values())
        levels = {"close": close, "previous_close": previous_close,
                  "fundamental_value": table["fundamental_value"][rows[-1]],
                  "anchor_price": table["anchor_price"][rows[-1]]}
        # log(close) is log(fundamental value) + mispricing_s + the log
        # distance from the anchor to the print, so the day's move is the
        # change in each of the three. Both of the two here are MEASURED
        # against the day before's closing levels rather than taken as
        # what the mispricing leaves over: a remainder would make the
        # eleven sum to the move whatever the engine had done, and the
        # sum is the claim.
        #
        # Those levels are on the tape of the day before. Without it the
        # valuation and the book are one number, `fair_value` reads zero
        # and `book` is the remainder, which the caveats say and
        # `check()` cannot then contradict.
        if self._previous is None:
            factors["fair_value"] = 0.0
            factors["book"] = move - mispricing
        else:
            factors["fair_value"] = math.log(
                levels["fundamental_value"]
                / self._previous["fundamental_value"])
            factors["book"] = (
                math.log(levels["close"] / levels["anchor_price"])
                - math.log(levels["previous_close"]
                           / self._previous["anchor_price"]))
        state = {name: value for name, value
                 in self._opened.macro_fields.items()
                 if isinstance(value, (int, float))}
        for name in _STATE_FIELDS:
            state[name] = _column(self._opened, name)[i]
        depth = _depth(fork, self._label, i, anchor=move - factors["book"])
        return _Day(move=move, factors=factors, state=state, levels=levels,
                    depth=depth)

    def replay(self, node: Node) -> float:
        """Run the day again under ``node``'s draws and read it back.

        A fork of the copy taken before the day opened, the node's logged
        draw values installed as patches at their addresses, the day's own
        inputs replayed, and the enclosing contribution measured on that
        run. For a leaf that is its parent's value, which is the claim
        "replaying any leaf reproduces its parent"; :meth:`check` makes it
        for every node.

        The patches are the values the log delivered, installed
        against the node's own addresses in log order, so a replay of a
        correctly addressed node reproduces the day. What it establishes
        is that the copy, the log and the overlay rebuild the day, and
        that the addresses a node carries are the ones its values came
        from wherever the difference reaches this name: a node aimed one
        draw late installs the right values in the wrong places.

        How far that reaches is measured rather than assumed. On the
        market factor and on this name's settlement uniforms a slip of
        one address moves the close; on its idiosyncratic, sector, news
        and jump draws the slip lands on a neighbour's slot and the close
        is bit-identical inside the day. Nor does a replay establish that
        a node addresses the right NAME at all, since a node built off
        another company's tag carries that company's addresses and values
        together. `tests/test_explain.py` measures both limits and checks
        the tags against the log and the market slots against
        `noise.market_day_layout`.
        """
        path, _ = self._locate(node)
        measured = self._run(self._patches(node))
        return self._value_of(self._target(path), measured)

    def check(self) -> list[str]:
        """Replay every node, and report what did not come back.

        Four claims, each stated as a line per miss. The eleven
        contributions sum to the move. Every node's replay reproduces the
        contribution it sits under. And where the run recorded this day,
        the replay reproduces both the nine columns the run recorded and
        the close it printed.
        """
        misses: list[str] = []
        total = math.fsum(child.value for child in self.root.children)
        residual = self.move - total
        if abs(residual) > TOLERANCE:
            misses.append(
                f"the {len(self.root.children)} contributions sum to "
                f"{total!r} against a move of {self.move!r}, a residual of "
                f"{residual!r}, over a tolerance of {TOLERANCE!r}")
        for path, node, _ in self._walk:
            expected = self._value_of(self._target(path), self._base)
            got = self.replay(node)
            if abs(got - expected) > TOLERANCE:
                misses.append(
                    f"{path} replayed to {got!r} against {expected!r} on "
                    f"the recorded run, a difference of {got - expected!r}")
        for name, recorded in (self._recorded or {}).items():
            got = self._base.factors[name]
            if abs(got - recorded) > TOLERANCE:
                misses.append(
                    f"{name} replayed to {got!r} against {recorded!r} on "
                    "the tape the run itself recorded")
        if self._recorded_close is not None:
            got = self._base.levels["close"]
            if got != self._recorded_close:
                misses.append(
                    f"the replayed close is {got!r} against "
                    f"{self._recorded_close!r} on the tape the run itself "
                    "recorded")
        return misses

    # -- walking the tree -------------------------------------------------

    def _locate(self, node: Node) -> tuple[str, Node | None]:
        for path, other, parent in self._walk:
            if other is node:
                return path, parent
        raise ValidationError(
            f"that {node.kind} node is not in this explanation's tree. "
            "replay takes a node reached from .root of this object.")

    def _target(self, path: str) -> Node:
        """The quantity a node's replay reports.

        The nearest enclosing node the day recomputes on its own: the
        move, a contribution, or one of the three readings the print
        table splits the book contribution into. A plain ``mechanism``
        node carries its contribution's value, and ``state`` and ``draw``
        nodes are not quantities the engine recomputes, so a replay of
        one reports what it sits under.
        """
        while path:
            node = self._by_path[path]
            if node.kind in ("move", "factor") or \
                    id(node) in self._depth_nodes:
                return node
            path = path.rsplit(".", 1)[0] if "." in path else ""
        return self.root

    def _value_of(self, target: Node, day: _Day) -> float:
        """What ``target`` states, read off one run of the day."""
        if target.kind == "move":
            return day.move
        key = self._depth_nodes.get(id(target))
        return day.depth[key] if key else day.factors[target.name]

    def _patches(self, node: Node) -> tuple[Patch, ...]:
        """The overlay a replay of ``node`` installs.

        Each draw node's addresses are paired POSITIONALLY with the values
        the log delivered at that site, in log order, rather than each
        address being looked up to find its own value. The difference is
        the whole of what a replay can catch: read back at the address, a
        patch carries the value that address already delivers, so it is a
        no-op whatever the address is and a node aimed one draw late
        replayed clean.
        """
        out: list[Patch] = []
        for leaf in _draw_nodes(node):
            values = self._draw_values.get(id(leaf), ())
            if len(values) != len(leaf.addresses):
                raise ValidationError(  # pragma: no cover - built together
                    f"the {leaf.name} node carries "
                    f"{len(leaf.addresses)} addresses and "
                    f"{len(values)} logged values")
            out.extend(Patch(address, value)
                       for address, value in zip(leaf.addresses, values))
        return tuple(out)

    # -- what this call could and could not do ----------------------------

    def _caveats(self) -> list[str]:
        out: list[str] = []
        out.append(
            "Every number here was measured by running day "
            f"{self.day} again from the copy taken before it opened, under "
            f"the {len(self._inputs)} inputs the run log holds for it.")
        if self._roster_moved:
            out.append(
                f"The roster held {_count(len(self._roster), 'name')} when "
                f"this day opened and holds {len(self._engine.tickers)} now, "
                f"so {self.ticker} is read at the slot it had on the day "
                "rather than the slot it has now. A tape row older than the "
                "change carries the older slots too.")
        if self._label != self.day:
            out.append(
                f"The run recorded this day under day {self._label} while "
                f"the engine opened it as day {self.day}, so the tape is "
                f"read at {self._label} and the tree is the day the store "
                f"kept as {self.day}. Both paths the library drives pass "
                "one number to both.")
        if self._previous_moved and self._previous is not None:
            out.append(
                f"{self.ticker} sat at slot {self._previous_index} on day "
                f"{self._label - 1} and sits at slot {self._index} on day "
                f"{self._label}, so the previous close's levels are read "
                "at the slot it held then. The roster the day before "
                "opened on is what says which slot that was.")
        elif self._previous_moved:
            if self._ops:
                named = ", ".join(f"{kind} {what}" for kind, what
                                  in self._ops)
                why = ("the run log holds "
                       + _count(len(self._ops), "roster operation")
                       + f" between the two opens ({named})")
            elif self._compared == "rosters":
                why = "the roster the day before opened on names it nowhere"
            else:
                why = ("the tape holds a different number of names on the "
                       "two days")
            out.append(
                f"{self.ticker} has no levels on day {self._label - 1}, "
                f"because {why}, so the valuation is not separated from "
                "the book on this day.")
        elif self._compared == "the run log" and self._previous is not None:
            out.append(
                f"Day {self.day - 1} was not kept, so the roster it ran "
                "under is not here to compare against name by name. The "
                "run log holds no listing or delisting between the two "
                "opens, and a roster moves only through one of those, so "
                "the previous close's levels are read at this name's own "
                "slot. This is the weakest evidence any path here reads "
                "them on.")
        silent = [m.factor for m in MECHANISMS if not m.sites]
        blind = [m.factor for m in MECHANISMS
                 if m.sites and not any(child.kind == "draw" for child
                                        in self._child_of(m.factor))]
        if silent:
            out.append(
                ", ".join(silent) + " take no draw of their own, so those "
                "contributions have no draw leaf; they are functions of the "
                "state and the dials under them.")
        if blind:
            out.append(
                _count(len(blind), "contribution") + " names a draw site "
                f"the log holds no draw of for this day "
                f"({', '.join(blind)}), so it has no leaf here.")
        off = [stream for stream in _STREAMS
               if stream not in self._traced]
        if off:
            out.append(
                _count(len(off), "stream") + " this tree reads took no "
                f"logged draw over the day ({', '.join(off)}), so a "
                "contribution it seeds has no address here. The draw log "
                "is opt-in, and keep_explanations turns it on over the "
                "days it keeps.")
        if self._previous is None:
            out.append(
                f"day {self.day - 1} is not on this engine's tape, so the "
                "fundamental value at the previous close is unknown and the "
                "valuation's own move is not separated from the book's. The "
                "book contribution carries both and fair_value reads zero.")
        else:
            out.append(
                "The valuation's move is measured against the fundamental "
                f"value on day {self.day - 1}'s tape, and the book's share "
                "is what the valuation and the mispricing do not account "
                "for.")
        if self._recorded is None:
            out.append(
                f"day {self.day} is not on this engine's tape, so check() "
                "compares the replay against nothing the run itself "
                "recorded.")
        else:
            out.append(
                f"day {self.day} is on this engine's tape, so check() "
                "compares every contribution against what the run recorded "
                "for it.")
        jump = self.day - 1
        out.append(
            "The jump a day carries was drawn at the close before it, so "
            f"the jump node's addresses are day {jump}'s and a window that "
            "starts at this day still reaches them.")
        if self._base.depth:
            three = math.fsum(self._base.depth[name] for name, _ in DEPTH)
            book = self._base.factors["book"]
            out.append(
                "The book contribution splits three ways under it, from "
                f"Engine.prints(): the order book's own share, the second "
                "circuit breaker's, and what the model price picks up by "
                "being measured from the last print rather than from the "
                "anchor. Each is a sum over the day's prints and the "
                "parent is the change in one distance across the day, so "
                f"they are three readings that add to it ({three!r} "
                f"against {book!r}) rather than a division of it.")
        else:
            out.append(
                "This build has no Engine.prints(), so the book "
                "contribution is one number for the day and is not "
                "decomposed into the order book's share and the circuit "
                "breaker's.")
        if self._base.depth and self._base.depth["circuit_breaker_two"] == 0.0:
            out.append(
                "The second circuit breaker did not bind on any print of "
                "this day, so its share is exactly zero and the order "
                "book's share is the whole of the absorption.")
        out.append(
            "It measures where the move came from. It does not measure "
            "what the move would have been without a draw, which needs an "
            "arm per draw: tradefloor.noise.attribute.")
        return out

    def _child_of(self, factor: str) -> tuple[Node, ...]:
        for child in self.root.children:
            if child.name == factor:
                return child.children[0].children
        return ()

    # -- output -----------------------------------------------------------

    def to_json(self) -> str:
        """The explanation as JSON: the tree, the move and the caveats."""
        return json.dumps({
            "ticker": self.ticker, "day": self.day, "move": self.move,
            "caveats": list(self.caveats), "root": _node_to_json(self.root),
        }, sort_keys=True, indent=2)

    def to_arrow(self) -> Any:
        """One row per node, as a ``pyarrow.Table``.

        ``path`` is the node's place in the tree and ``parent`` the path
        above it, so the tree rebuilds from the table. ``inputs`` is the
        node's dials and macro fields as canonical JSON, because a column
        of maps would not round-trip through every Arrow reader.
        """
        try:
            import pyarrow as pa
        except ImportError as exc:  # pragma: no cover
            raise ImportError(
                "Explanation.to_arrow builds an Arrow table and needs "
                "pyarrow. Install it with: pip install tradefloor[arrow]; "
                "the tree is on .root without it.") from exc
        rows = [_row(path, node, parent) for path, node, parent in self._walk]
        schema = pa.schema(
            [(name, getattr(pa, kind)()) for name, kind in self.COLUMNS],
            # One entry, because arrow-schema serialises a metadata map in
            # iteration order at the boundary and more than one key gives
            # non-deterministic bytes.
            metadata={"caveats": " ".join(self.caveats)})
        columns = {name: [row[name] for row in rows]
                   for name, _ in self.COLUMNS}
        return pa.table(columns, schema=schema)

    def render(self, depth: int = 3) -> str:
        """The tree to ``depth`` levels, then the caveats."""
        under = len(_addresses(self.root))
        lines = [f"{self.ticker} day {self.day}: move {self.move:+.6g} "
                 f"({len(self._walk)} nodes over "
                 f"{_count(under, 'addressed draw')})"]
        for path, node, _ in self._walk:
            level = path.count(".")
            if level > depth:
                continue
            draws = ("  " + _count(len(node.addresses), "draw")
                     if node.addresses else "")
            lines.append(f"{'  ' * level}{node.kind:<10} {node.name:<40}"
                         f"{node.value:>+16.8g}{draws}")
        for caveat in self.caveats:
            lines.append(f"  caveat: {caveat}")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (f"Explanation({self.ticker!r}, day={self.day}, "
                f"move={self.move:+.6g}, {len(self._walk)} nodes)")


#: The engine columns a state node can carry, gathered from the table so
#: one read of each serves every mechanism that names it.
_STATE_FIELDS: tuple[str, ...] = tuple(
    dict.fromkeys(name for m in MECHANISMS for name in m.state))


#: The streams the mechanism table reads, which are the ones a tree can
#: address. The other four of the seven seed the macro chain, the shared
#: volume level, the per-name volume level and the embedder's own
#: subsystems, and no contribution here names them.
_STREAMS: tuple[str, ...] = tuple(
    dict.fromkeys(stream for m in MECHANISMS for stream, _, _ in m.sites))


def _logged_draws(engine: Engine, day: int
                  ) -> tuple[dict[tuple[str, int], tuple], set[str]]:
    """Every draw logged for this day and the one before it.

    Keyed by ``(stream, day)``. The day before is here because the jump a
    day carries was drawn at that close. The second return is the streams
    that delivered a logged draw, which is what the caveats report as on.

    Only the streams :data:`_STREAMS` names are read. The four the tree
    cannot address are cheap rather than costly: on day 1 at a hundred
    names they hold 111 draws against the three read streams' 239,472, so
    skipping them saves 0.05 per cent of what a call holds. They are
    skipped because nothing here can address them, and the caveats
    report silence over the streams a contribution names.
    """
    out: dict[tuple[str, int], tuple] = {}
    traced: set[str] = set()
    for stream in _STREAMS:
        for which in (day - 1, day):
            entries = tuple(noise.draw_log(engine, stream, which, which))
            if entries:
                out[(stream, which)] = entries
                traced.add(stream)
    return out, traced


def _recorded_factors(engine: Engine, day: int,
                      index: int) -> dict[str, float] | None:
    """The day's ``truth()`` column sums for one name, off the run's tape.

    ``None`` when the run did not record the day. What :meth:`check`
    compares the replay against, so a replay that rebuilt a different day
    is reported rather than believed. Guarded on the count of recorded
    days for the reason :func:`_levels` gives.
    """
    if engine.recorded_days == 0:
        return None
    try:
        stream = engine.truth(day=int(day))
    except ValidationError:
        return None
    table = _table(stream)
    ids = table["instrument_id"]
    rows = [k for k in range(len(ids)) if ids[k] == index]
    if not rows:
        return None
    return {name: math.fsum(table[name][k] for k in rows)
            for name in Engine.FACTORS}


def _depth(engine: Engine, day: int, index: int,
           *, anchor: float) -> dict[str, float]:
    """The book contribution's three readings, off one day's prints.

    Empty on a build without ``Engine.prints()``, which is what makes the
    child appear when P2 lands rather than on an edit here. ``anchor`` is
    the anchor's own move over the day, which is every contribution but
    the book, and the third reading is the summed shock measured against
    it rather than taken as a remainder.
    """
    if not hasattr(engine, "prints"):
        return {}
    try:
        table = _table(engine.prints(day=int(day)))
    except ValidationError:  # pragma: no cover - the fork records the day
        return {}
    ids = table["instrument_id"]
    rows = [k for k in range(len(ids)) if ids[k] == index]
    if not rows:  # pragma: no cover - a name with no prints on the day
        return {}
    absorbed = math.fsum(table["absorbed"][k] for k in rows)
    clamp = math.fsum(table["clamp"][k] for k in rows)
    shock = math.fsum(table["shock"][k] for k in rows)
    return {"order_book": absorbed - clamp,
            "circuit_breaker_two": clamp,
            "anchor_pull": shock - anchor,
            "shock": shock,
            "absorbed": absorbed}


def _recorded_close(engine: Engine, day: int, index: int) -> float | None:
    """The close one name printed on ``day``, off the run's own tape.

    ``None`` when the run did not record the day. Compared to the bit
    rather than to a tolerance: a replay of the same day from the same
    state under the same draws prints the same price, and a difference of
    one cent is a difference.
    """
    if int(day) < 0 or engine.recorded_days == 0:
        return None
    try:
        stream = engine.bars(day=int(day), grain="day")
    except ValidationError:
        return None
    table = _table(stream)
    ids = table["instrument_id"]
    rows = [k for k in range(len(ids)) if ids[k] == index]
    if not rows:
        return None
    return float(table["close"][rows[-1]])


def _replay_inputs(engine: Engine, inputs: Sequence[dict],
                   day: int) -> None:
    """Replay one day's log entries onto ``engine``.

    The same inputs the run gave the day, in the order it gave them, so
    the fork reruns the day rather than a default version of it. A day
    the run never recorded is recorded here, because the tree reads the
    day off the ``truth()`` table and a record is the only way onto it;
    the record happens after the close, where the day's tape is still
    whole.
    """
    recorded = False
    for entry in inputs:
        op = entry["op"]
        if op == "open_market":
            engine.open_market()
        elif op == "close_market":
            engine.close_market()
        elif op == "record":
            # Under the day the store keyed this open by, not under the
            # label the run gave it. The two agree for `run_days` and for
            # `World.run`, and where they do not the tree still reads the
            # day it was asked for.
            engine.record(int(day))
            recorded = True
        elif op == "run_session":
            engine.run_session(
                int(entry["hour"]), int(entry["minute"]),
                int(entry["day_of_week"]), int(entry["ticks"]),
                volatility=float(entry["volatility"]),
                close_at_end=bool(entry["close_at_end"]),
                news=_news(entry["news"]),
                order_flow=_flow(entry["order_flow"]))
        elif op == "tick":
            engine.tick(int(entry["hour"]), int(entry["minute"]),
                        int(entry["day_of_week"]),
                        volatility=float(entry["volatility"]),
                        news=_news(entry["news"]),
                        order_flow=_flow(entry["order_flow"]))
        elif op == "pin_macro":
            engine.pin_macro(**entry["fields"])
        elif op == "set_avg_volume":
            engine.set_avg_volume(entry["values"])
        elif op == "draw_uniform":
            engine.draw_uniform()
        elif op == "draw_normal":
            engine.draw_normal()
        else:
            raise ValidationError(
                f"a day of inputs carries {op!r}, which a replay of one day "
                "does not run. A roster edit inside the day being explained "
                "is the case, and it moves the slot every column is read at.")
    if not recorded:
        engine.record(int(day))


def _news(entries: Sequence[dict]) -> list | None:
    from ._core import News

    if not entries:
        return None
    return [News(ticker=e["ticker"], sector=e["sector"],
                 price_impact=e["price_impact"]) for e in entries]


def _flow(entry: dict) -> dict | None:
    if not entry:
        return None
    return {name: (float(pair[0]), float(pair[1]))
            for name, pair in entry.items()}


def _walk(node: Node, path: str, parent: Node | None):
    here = f"{path}.{node.name}" if path else node.name
    yield here, node, parent
    for child in node.children:
        yield from _walk(child, here, node)


def _draw_nodes(node: Node) -> tuple[Node, ...]:
    """Every draw node at or under ``node``, in tree order."""
    if node.kind == "draw":
        return (node,)
    out: list[Node] = []
    for child in node.children:
        out.extend(_draw_nodes(child))
    return tuple(out)


def _addresses(node: Node) -> tuple[DrawAddress, ...]:
    out = list(node.addresses)
    for child in node.children:
        out.extend(_addresses(child))
    return tuple(out)





def _row(path: str, node: Node, parent: Node | None) -> dict:
    streams = {address.stream for address in node.addresses}
    return {
        "path": path,
        "parent": path.rsplit(".", 1)[0] if "." in path else "",
        "depth": path.count("."),
        "kind": node.kind,
        "name": node.name,
        "value": float(node.value),
        "stream": streams.pop() if len(streams) == 1 else None,
        "draws": len(node.addresses),
        "first_index": (int(node.addresses[0].index)
                        if node.addresses else None),
        "inputs": json.dumps(node.inputs, sort_keys=True),
    }


def _node_to_json(node: Node) -> dict:
    return {
        "kind": node.kind, "name": node.name, "value": node.value,
        "inputs": dict(node.inputs),
        "addresses": [list(address) for address in node.addresses],
        "children": [_node_to_json(child) for child in node.children],
    }


def _node_from_json(payload: dict) -> Node:
    """Rebuild a node from :meth:`Explanation.to_json`.

    Here rather than on the class because a tree read back from JSON has
    no engine behind it and cannot replay, so it is not an
    :class:`Explanation`. ``tests/test_explain.py`` uses it to state that
    the JSON carries the whole tree.
    """
    return Node(
        kind=payload["kind"], name=payload["name"],
        value=payload["value"], inputs=dict(payload["inputs"]),
        addresses=tuple(DrawAddress(*a) for a in payload["addresses"]),
        children=tuple(_node_from_json(c) for c in payload["children"]))


__all__ = ["Explanation", "Node", "Mechanism", "MECHANISMS", "CONTRIBUTIONS",
           "KINDS", "TOLERANCE"]
