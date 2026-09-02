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

## The tree

The root is the move, ``log(close / previous close)``, with the close
read from the replayed day and the previous close from the copy taken
before the open.

Its children are eleven contributions, of kind ``factor``. Nine are the
``truth()`` columns for the name on that day, in ``Engine.FACTORS``
order, and they sum to the day's change in ``mispricing_s``. Two more
close the arithmetic: ``fair_value`` is the day's change in log
fundamental value and ``book`` is the change in the log distance from the
model price to the print. All eleven are measured, so their sum against
the move is an identity the engine can fail;
:meth:`Explanation.check` states the residual rather than asserting it.
Where the day before is not on the tape its closing levels are unknown,
``fair_value`` reads zero and ``book`` is what the mispricing leaves
over, which the caveats say.

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

The tree reads the day's draw log, and the market stream's log is the
size of the tape: 673 draws a tick at a hundred names. On
``Universe.random(n, seed=111)`` at engine seed 42, three days,
``pt-v16``, at 8273f24, one ``explain`` call and one ``check`` on it
cost:

===== ========== ========= =========
names  explain     check      peak
===== ========== ========= =========
   12     1.03 s    0.29 s    38.7 MB
   40     2.30 s    0.96 s    94.3 MB
  100     5.57 s    2.27 s   224.0 MB
===== ========== ========= =========

The addressed draws under one name are 2,736 at every roster size, so
what grows is the log the call reads rather than the tree it builds. A
filtered read on the extension side would remove it, and this build does
not have one.

Keeping a window costs a run some time and some memory of its own: at a
hundred names over twenty days, 3.13 seconds against 2.43 seconds
without one, on the same roster and seed.

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


#: The Rust that produced each contribution. The nine ``truth()`` columns
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
        dials=("order_flow_coefficient", "informed_flow_fraction"),
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
        via=("market::tick::simulate_market_tick",),
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
        factor="fair_value",
        function="fair_value::compute_fair_value_with",
        macro=("qe_pe_boost",),
        dials=("fair_value_book_floor", "qe_pe_gain", "qe_pe_stock_gain"),
        via=("market::tick::simulate_market_tick",
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

#: The contributions the root carries, in order: the nine ``truth()``
#: columns and the two that close the arithmetic to the printed move.
FACTORS: tuple[str, ...] = tuple(m.factor for m in MECHANISMS)

#: The kinds a node can be.
KINDS = ("move", "factor", "mechanism", "state", "draw")

#: How close the eleven contributions have to come to the move before
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
    addresses delivered. ``inputs`` are the dials and macro fields the
    node read, by name. ``addresses`` are the draws under this node
    alone, and ``children`` the nodes under it.
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


def _count(n: int, noun: str) -> str:
    """``n`` of ``noun``, pluralised. A caveat that says "1 streams" reads
    as a template rather than as a measurement, and the reader stops
    trusting the number in front of it."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _f64(buf: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(buf) // 8}d", buf)


def _column(engine: Engine, field: str) -> tuple[float, ...]:
    return _f64(engine.column(field))


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


def _explain(engine: Engine, ticker: str, day: int, sector: int,
             window: tuple[int, int] | None, kept: Sequence[int],
             opened: Engine | None,
             inputs: Sequence[dict] | None) -> "Explanation":
    """Build the explanation ``Engine.explain`` returns.

    Called from the binding, which owns the store and hands over what it
    holds: the engine, the name's sector index, the window asked for, the
    days kept, the copy taken before this day's open and this day's own
    inputs. The sector index comes from the binding because the sector
    factor's tag is a position in the engine's own sector table and the
    engine publishes no column for it.
    """
    if ticker not in engine.tickers:
        raise ValidationError(
            f"{ticker!r} is not in this engine's roster, which starts "
            f"{list(engine.tickers[:8])}")
    if opened is None or inputs is None:
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
    return Explanation(engine=engine, ticker=ticker, day=int(day),
                       sector=int(sector), window=window,
                       kept=tuple(int(d) for d in kept),
                       opened=opened, inputs=tuple(inputs))


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
                 sector: int, window: tuple[int, int] | None,
                 kept: tuple[int, ...], opened: Engine,
                 inputs: tuple[dict, ...]) -> None:
        self.ticker = ticker
        self.day = int(day)
        self._engine = engine
        self._window = window
        self._kept = kept
        self._opened = opened
        self._inputs = inputs
        self._index = engine.tickers.index(ticker)
        self._sector = int(sector)
        self._dials = dict(engine.model_params)
        #: What every logged draw of the day delivered, by address. The
        #: values a replay installs, so a replay of a node reproduces the
        #: run rather than perturbing it.
        self._logged, self._traced = _logged_draws(engine, self.day)
        #: What each address delivered, by address. A scan of the log per
        #: address turned one `check()` on a twelve-name day into 78
        #: million comparisons, which is the whole of its cost.
        self._values = {entry.address: entry.value
                        for entries in self._logged.values()
                        for entry in entries}
        self._previous = _levels(engine, self.day - 1, self._index)
        self._runs: dict[tuple, _Day] = {}
        base = self._run(())
        self._base = base
        self.move = base.move
        self.root = self._tree(base)
        self._walk = tuple(_walk(self.root, "", None))
        self._by_path = {path: node for path, node, _ in self._walk}
        self._recorded = _recorded_factors(engine, self.day, self._index)
        #: The close the run itself printed on this day, where its tape
        #: holds one. The nine columns agreeing says the mispricing path
        #: was rebuilt; the print is settled through the book after that,
        #: so it is compared on its own.
        self._recorded_close = _recorded_close(engine, self.day,
                                               self._index)
        self.caveats = self._caveats()

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
        return Node(kind="factor", name=mech.factor, value=value,
                    inputs={}, addresses=(),
                    children=(self._mechanism(mech, value, base),))

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
            out.append(Node(
                kind="draw", name=site,
                value=math.fsum(entry.value for entry in entries),
                inputs={"count": float(len(entries)),
                        "day": float(self.day + mech.offset)},
                addresses=tuple(entry.address for entry in entries),
                children=()))
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
        _replay_inputs(fork, self._inputs, self.day)
        measured = self._measure(fork)
        self._runs[key] = measured
        return measured

    def _measure(self, fork: Engine) -> _Day:
        """Read the day off a run of it."""
        i = self._index
        previous_close = _column(self._opened, "price")[i]
        close = _column(fork, "price")[i]
        move = math.log(close / previous_close)
        table = _table(fork.truth(day=self.day))
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
        return _Day(move=move, factors=factors, state=state, levels=levels)

    def replay(self, node: Node) -> float:
        """Run the day again under ``node``'s draws and read it back.

        A fork of the copy taken before the day opened, the node's logged
        draw values installed as patches at their addresses, the day's own
        inputs replayed, and the enclosing contribution measured on that
        run. For a leaf that is its parent's value, which is the claim
        "replaying any leaf reproduces its parent"; :meth:`check` makes it
        for every node.

        The patches are the values the draws already delivered, so a
        replay reproduces the day rather than perturbing it. What it
        establishes is that the copy, the addresses and the log agree: a
        patch aimed at the wrong address installs a different draw and
        the day comes back different.
        """
        path, _ = self._locate(node)
        measured = self._run(self._patches(node))
        return _value_of(self._target(path), measured)

    def check(self) -> list[str]:
        """Replay every node, and report what did not come back.

        Three claims, each stated as a line per miss. The eleven
        contributions sum to the move. Every node's replay reproduces the
        contribution it sits under. And where the run recorded this day,
        the replay reproduces what the run itself recorded.
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
            expected = _value_of(self._target(path), self._base)
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
        """The contribution a node's replay reports.

        The nearest enclosing ``move`` or ``factor`` node, the node
        itself where it is one. A ``mechanism``, ``state`` or ``draw``
        node is not a quantity the engine recomputes on its own, so a
        replay of one reports the contribution it sits under.
        """
        while path:
            node = self._by_path[path]
            if node.kind in ("move", "factor"):
                return node
            path = path.rsplit(".", 1)[0] if "." in path else ""
        return self.root

    def _patches(self, node: Node) -> tuple[Patch, ...]:
        out: list[Patch] = []
        for address in _addresses(node):
            out.append(Patch(address, self._logged_value(address)))
        return tuple(out)

    def _logged_value(self, address: DrawAddress) -> float:
        try:
            return self._values[address]
        except KeyError:  # pragma: no cover - the tree builds these
            raise ValidationError(
                f"{address} is in the tree and not in the day's draw "
                "log") from None

    # -- what this call could and could not do ----------------------------

    def _caveats(self) -> list[str]:
        out: list[str] = []
        out.append(
            "Every number here was measured by running day "
            f"{self.day} again from the copy taken before it opened, under "
            f"the {len(self._inputs)} inputs the run log holds for it.")
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
                ", ".join(blind) + " draw and have no leaf here, because "
                "the log holds no draw of theirs for this day.")
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
        if hasattr(Engine, "prints"):
            out.append(
                "The book contribution carries the order book and the "
                "second circuit breaker together; Engine.prints() separates "
                "them per print, as absorbed and clamp.")
        else:
            out.append(
                "This build has no Engine.prints(), so the book "
                "contribution is one number for the day and is not "
                "decomposed into the order book's share and the circuit "
                "breaker's.")
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
#: address. The other three of the seven seed the macro chain, the shared
#: volume level and the embedder's own subsystems, and no contribution
#: here names them.
_STREAMS: tuple[str, ...] = tuple(
    dict.fromkeys(stream for m in MECHANISMS for stream, _, _ in m.sites))


def _logged_draws(engine: Engine, day: int
                  ) -> tuple[dict[tuple[str, int], tuple], set[str]]:
    """Every draw logged for this day and the one before it.

    Keyed by ``(stream, day)``. The day before is here because the jump a
    day carries was drawn at that close. The second return is the streams
    that delivered a logged draw, which is what the caveats report as on.

    Only the streams :data:`_STREAMS` names are read, because the log of
    one of them is the size of the tape: the market stream takes 673
    draws per tick at a hundred names, and fetching the four the tree
    cannot address would double what a call holds for nothing.
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


def _addresses(node: Node) -> tuple[DrawAddress, ...]:
    out = list(node.addresses)
    for child in node.children:
        out.extend(_addresses(child))
    return tuple(out)


def _value_of(target: Node, day: _Day) -> float:
    if target.kind == "move":
        return day.move
    return day.factors[target.name]


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


__all__ = ["Explanation", "Node", "Mechanism", "MECHANISMS", "FACTORS",
           "KINDS", "TOLERANCE"]
