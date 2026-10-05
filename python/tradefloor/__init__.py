"""tradefloor: a deterministic market simulator with a real limit order book.

The compiled engine lives in ``tradefloor._core``. This package re-exports it and
adds the parts that are better written in Python than in Rust: JSON
round-trips, and process-level parallelism for seed sweeps. Forcing those into
the extension would mean hand-rolling JSON escaping and reimplementing a
process pool, both worse than the standard library versions.

The same seed and the same inputs are designed to produce bit-identical
output on Linux, macOS and Windows, because the library ships its own
transcendental maths instead of calling the platform's libm.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from . import _core
from .portfolio import Cancel, Limit, Portfolio, Position
from . import harness as _harness
from . import universe_util as _universe_util
from .harness import Agent, History, Observation, Scorecard, evaluate, leaderboard
from .population import Population
from . import sandbox
from .sandbox import HiddenState, MarketView, PortfolioView, SandboxError
from .replay import replay
from . import edgar
from . import envelope
from .records import preset_record, available as preset_records
from . import atlas
from . import baselines
from . import tca
from . import scenario as _scenario_mod
from . import facts
from . import loss
from .checkpoint import Checkpoint, branch
from . import counterfactual
from . import noise
from .counterfactual import (Agreement, Comparison, Divergence, Invariance,
                             Resample, World, agree, compare, invariance,
                             resample)
from . import externality
from .externality import Externality, externalities
from . import render
from .render import JSONRenderer, Renderer, TextRenderer
from .sweep import sweep
from . import boundary
from .boundary import BoundaryMap, Flip, flip, map_boundaries

from . import explain
from .explain import Explanation, Node
from . import manifest
from .manifest import DayLedger, RunManifest, Verification
from . import spec
from .spec import SPEC_VERSION, StrategySpec
from .scenario import Scenario, run_scenario
from . import interventions
from .interventions import (
    Firing, Intervention, ScenarioValidationError, TARGETS,
    UNSUPPORTED as UNSUPPORTED_TARGETS,
)
from . import yaml_subset
from .tca import Execution
from .baselines import (capture_ratio, capture_withheld, oracle_is_ceiling,
                        reference_agents, versus_buy_and_hold)
from .ranking import AgentRecord, Ranking, rank
from . import fingerprint
from .fingerprint import (
    BATTERY_VERSION, Battery, Cell, Fingerprint, FingerprintComparison,
    battery, commit, reveal, sealed_battery,
)
from ._core import (  # noqa: F401
    ArrowStream,
    Engine,
    EngineBatch,
    FairValue,
    Fill,
    GameRng,
    Instrument,
    Macro,
    MatchResult,
    ModelParams,
    News,
    NewsImpact,
    OrderBook,
    OrderError,
    PriceLevel,
    SweepCost,
    TickResult,
    ValidationError,
    check_seed as _check_seed,
    fair_value,
    market_status,
    model_preset,
    preset_names,
    sectors,
    version,
)
from ._api import DEPRECATED as _DEPRECATED
from ._api import REMOVED as _REMOVED


def __getattr__(name: str) -> Any:
    """Serve an engine internal that used to be exported here, with a warning.

    The names are listed in ``tradefloor._api.DEPRECATED``, empty since
    0.10.0; a name 0.10.0 removed (``_api.REMOVED``) raises an
    AttributeError that says where it lives now. ``from tradefloor
    import X`` reads the attribute twice, once from inside importlib to see
    whether X is a submodule and once for the import itself, so the first
    read is answered without a warning and the caller sees one.
    """
    home = _DEPRECATED.get(name)
    if home is None:
        if name in _REMOVED:
            from ._api import removal_message
            raise AttributeError(removal_message(name))
        raise AttributeError(f"module 'tradefloor' has no attribute {name!r}")
    import importlib
    import sys
    import warnings

    from ._api import deprecation_message

    caller = sys._getframe(1)
    if not (caller.f_code.co_filename.startswith("<frozen importlib")
            or _is_star_import(caller)):
        warnings.warn(deprecation_message(name), DeprecationWarning,
                      stacklevel=2)
    return getattr(importlib.import_module(home), name)


def _is_star_import(frame: Any) -> bool:
    """Whether `frame` is executing ``from tradefloor import *``.

    A star import reads every name in ``__all__``, the deprecated ones
    included, so it would warn ten times about names the caller may never
    use. It binds them silently instead, and the warning comes when the
    caller reads one through the package. 3.11 runs the import as
    IMPORT_STAR; 3.12 and later as CALL_INTRINSIC_1 with oparg 2
    (INTRINSIC_IMPORT_STAR). An opcode this does not know is read as "not a
    star import", which warns, the safe direction.
    """
    import dis

    code, at = frame.f_code.co_code, frame.f_lasti
    if not 0 <= at < len(code) - 1:
        return False
    op = dis.opname[code[at]]
    return op == "IMPORT_STAR" or (op == "CALL_INTRINSIC_1" and code[at + 1] == 2)


__version__ = _core.__version__
__all__ = [
    "atlas",
    "envelope",
    "preset_record",
    "preset_records",
    "ArrowStream", "Engine", "EngineBatch", "FairValue", "Fill", "GameRng", "Instrument", "Macro",
    "MatchResult", "ModelParams", "News", "NewsImpact", "OrderBook",
    "OrderError", "PriceLevel",
    "SweepCost", "TickResult", "Universe", "ValidationError", "FlowImpact",
    "flow_impact", "Portfolio", "Position", "Limit", "Cancel", "Agent", "History", "Observation",
    "sandbox", "MarketView", "HiddenState", "PortfolioView", "SandboxError",
    "Scorecard", "evaluate", "leaderboard", "replay", "edgar",
    "baselines", "reference_agents", "capture_ratio", "capture_withheld",
    "oracle_is_ceiling", "versus_buy_and_hold", "tca", "Execution",
    "rank", "Ranking", "AgentRecord",
    "spec", "StrategySpec", "SPEC_VERSION",
    "Scenario", "run_scenario", "facts", "loss", "Checkpoint", "branch", "sweep",
    "counterfactual", "World", "agree", "compare", "resample",
    "population", "Population",
    "noise",
    "externality", "Externality", "externalities",
    "Agreement", "Comparison", "Resample",
    "Divergence",
    "boundary", "flip", "map_boundaries", "Flip", "BoundaryMap",
    "invariance", "Invariance", "render", "Renderer", "TextRenderer",
    "JSONRenderer",
    "explain", "Explanation", "Node",
    "Intervention", "Firing", "ScenarioValidationError", "interventions",
    "TARGETS", "UNSUPPORTED_TARGETS", "yaml_subset",
    "manifest", "RunManifest", "DayLedger", "Verification",
    "fingerprint", "BATTERY_VERSION", "Battery", "Cell", "Fingerprint",
    "FingerprintComparison", "battery", "commit", "reveal", "sealed_battery",
    "fair_value",
    "market_status", "model_preset", "preset_names", "run_many",
    "bonds", "rate_specs", "RATE_TICKERS", "RATE_SECTOR",
    "sectors", "version",
    "__version__",
]

# The fields an Instrument round-trips through JSON. Declared once, in one
# order, so serialising and deserialising cannot drift apart the way two
# hand-written field lists eventually do.
_INSTRUMENT_FIELDS = (
    "ticker", "sector", "initial_price", "shares_outstanding", "eps",
    "book_value_per_share", "revenue_growth", "avg_volume", "beta",
    "short_interest",
)

# Bumped when the serialised shape changes. A file written by a newer version
# is refused rather than silently misread, because a missing field would
# quietly become a default and produce a universe nobody specified.
_UNIVERSE_SCHEMA = 1


class Universe(list):
    """An ordered sequence of instruments.

    It is a ``list`` subclass because roster order is part of the contract.
    The engine iterates instruments in index order and draws as it goes, so
    a reordered universe is a different market from the same seed. A
    ``{ticker: instrument}`` mapping has no stable order, and a
    ``sort_by(ticker)`` somewhere upstream would silently change every
    price.

    Subclassing ``list`` also means it is accepted anywhere a list is, so the
    engine needs no special case.
    """

    @classmethod
    def random(cls, n: int = 108, *, seed: int = 0,
               bonds: bool = False) -> "Universe":
        """Generate ``n`` plausible instruments.

        ``bonds=True`` appends the three simulated rate indices, ``UST2Y``,
        ``UST10Y`` and ``IGCORP``, after the ``n`` equities (see
        :func:`tradefloor.bonds`). The equities are the same ``n`` names
        either way, and so is their market: the indices take no draws and
        write nothing back to the economy, so every equity price is
        bit-identical with or without them. Off by default.

        A realistic study needs on the order of a hundred names, and nobody
        hand-authors a hundred rosters. Without a generator the universe size
        would be however many names someone was willing to type.

        ``seed`` is the UNIVERSE seed and is independent of the simulation
        seed, so you can run the same universe under different market
        draws, which is the standard design for variance estimation. It is any integer from 0 to ``2**64 - 1``, like the
        simulation seed, and every seed below ``2**32`` draws the roster it
        drew when seeds were 32-bit.

        Market caps are log-distributed across all four spread tiers, P/E
        ratios scatter around each sector's anchor, and about one name in
        nine is a loss-maker so the book-value valuation path gets used.
        Those ranges are editorial and no golden vector pins them. What is
        guaranteed is that ``(n, seed)`` gives the same universe on every
        platform, so ``random(108, seed=7)`` is citable.
        """
        universe = cls(_core.random_instruments(n, seed=seed))
        if bonds:
            universe.extend(_core.rate_instruments())
        return universe

    def with_bonds(self, tickers: Sequence[str] | None = None) -> "Universe":
        """This universe with the simulated rate indices appended.

        ``tickers`` picks among ``UST2Y``, ``UST10Y`` and ``IGCORP`` and
        defaults to all three, in that order. Returns a new universe and
        leaves this one alone. Refuses a universe that already holds one of
        them, since each index is one instrument.
        """
        wanted = list(RATE_TICKERS) if tickers is None else list(tickers)
        held = {inst.ticker for inst in self if inst.sector == RATE_SECTOR}
        repeated = sorted(held & set(wanted))
        if repeated:
            raise ValidationError(
                f"this universe already holds {', '.join(repeated)}")
        return Universe(list(self) + list(_core.rate_instruments(wanted)))

    def equities(self) -> "Universe":
        """The equities, without any rate indices, in roster order."""
        return Universe(inst for inst in self if inst.sector != RATE_SECTOR)

    @classmethod
    def from_edgar(cls, snapshot, **kwargs: Any) -> "Universe":
        """Build a universe from an EDGAR snapshot. Pure and reproducible.

        Takes a :class:`tradefloor.edgar.Snapshot` or a path to a saved one.
        Extra keyword arguments are the macro conditions the fair values are
        computed under, and they must match the macro the engine then runs.
        If they don't, every company starts mispriced by the difference and
        nothing warns you.
        """
        from . import edgar as _edgar

        if isinstance(snapshot, str):
            snapshot = _edgar.Snapshot.load(snapshot)
        return cls(_edgar.to_instruments(snapshot, **kwargs))

    @property
    def fingerprint(self) -> str:
        """sha256 over the roster's canonical serialisation.

        Use this to identify a universe. The ticker list can't do it, because
        tickers are generated positionally: ``random(40, seed=1)`` and
        ``random(40, seed=99)`` share every name and share no earnings,
        sectors or share counts.

        Computed over the same field list ``to_json`` uses, with sorted keys
        and no indentation, so the hash depends on the content and not on
        formatting or key order.
        """
        import hashlib

        canonical = self.to_json(sort_keys=True, separators=(",", ":"),
                                 indent=None)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_json(self, **kwargs: Any) -> str:
        """Serialise to JSON.

        ``random(108, seed=7)`` names a universe only while the generator is
        versioned with the model. The JSON pins the exact instruments
        whatever a later generator would produce, so a specification that
        carries it names its universe exactly.
        """
        payload = {
            "schema": _UNIVERSE_SCHEMA,
            "instruments": [
                {field: getattr(inst, field) for field in _INSTRUMENT_FIELDS}
                for inst in self
            ],
        }
        kwargs.setdefault("indent", 2)
        if kwargs.get("indent") is None:
            kwargs.pop("indent")
        return json.dumps(payload, **kwargs)

    @classmethod
    def from_json(cls, text: str) -> "Universe":
        """Rebuild a universe from :meth:`to_json` output.

        Order is preserved exactly, because it is part of the contract.

        A newer schema is refused. Reading it anyway would give any field
        this version does not know about a silent default, and the result
        would be a universe nobody specified.
        """
        payload = json.loads(text)
        if not isinstance(payload, dict) or "instruments" not in payload:
            raise ValidationError("not a tradefloor universe document")

        schema = payload.get("schema", 0)
        if schema > _UNIVERSE_SCHEMA:
            raise ValidationError(
                f"universe schema {schema} is newer than this version "
                f"understands ({_UNIVERSE_SCHEMA}). Upgrade tradefloor rather "
                "than reading it partially."
            )

        out = cls()
        for i, row in enumerate(payload["instruments"]):
            unknown = set(row) - set(_INSTRUMENT_FIELDS)
            if unknown:
                raise ValidationError(
                    f"instrument {i} has unknown field(s): {sorted(unknown)}"
                )
            try:
                out.append(Instrument(
                    row["ticker"], row["sector"],
                    **{f: row[f] for f in _INSTRUMENT_FIELDS[2:] if f in row},
                ))
            except KeyError as exc:
                raise ValidationError(
                    f"instrument {i} is missing required field {exc.args[0]!r}"
                ) from None
        return out

    def tickers(self) -> list[str]:
        """Tickers, in roster order."""
        return [inst.ticker for inst in self]

    def __repr__(self) -> str:
        return f"Universe({len(self)} instruments)"


def _rebuild(instruments: Sequence[Instrument]) -> Universe:
    return Universe(instruments)


# --------------------------------------------------------------------------
# Rate instruments
# --------------------------------------------------------------------------

#: The sector a rate index carries. Not an equity sector: an engine splits
#: these instruments out before any equity code sees them.
RATE_SECTOR = "rates"

#: The simulated rate indices this build prices, in their default order.
RATE_TICKERS: tuple[str, ...] = tuple(s["ticker"] for s in _core.rate_specs())


def bonds(tickers: Sequence[str] | None = None) -> list[Instrument]:
    """The simulated rate indices, as instruments with their default terms.

    ``UST2Y``, ``UST10Y`` and ``IGCORP`` are simulated constant-maturity
    indices, not real securities: a 2-year and a 10-year treasury index and
    an investment-grade corporate bond index, priced off the engine's own
    curve (``treasury_yield_2y``, ``treasury_yield_10y``, and the 10-year
    plus the credit spread the engine sets). Each day an index returns

        carry - D * dy + 0.5 * C * dy**2,  carry = yield / 252,

    with duration and convexity of 1.9 and 4.6, 8.5 and 84, and 7.0 and 100.
    :func:`rate_specs` returns every term, and ``rust/src/rates.rs`` documents
    where each comes from.

    They trade like any other instrument: ``engine.book(ticker)``,
    ``Portfolio.execute``, ``fills=`` on ``run_session``, the tape, TCA. They
    must come after every equity in a roster, which :meth:`Universe.with_bonds`
    and ``Universe.random(..., bonds=True)`` arrange.
    """
    return list(_core.rate_instruments(None if tickers is None else list(tickers)))


def rate_specs() -> list[dict[str, Any]]:
    """Each rate index's fixed terms: name, curve point, duration,
    convexity, quoted spread in basis points, and default depth and level."""
    return list(_core.rate_specs())


# --------------------------------------------------------------------------
# Seed sweeps
# --------------------------------------------------------------------------

def _run_one(args: tuple) -> Any:
    """One seed, in one worker. Module-level so it is picklable.

    The universe crosses as JSON rather than as objects. That is not a
    workaround, since a serialised universe is the same universe by construction
    (there is a test), and it means a worker rebuilds from a specification
    rather than depending on whatever a pickle happened to preserve.

    A scenario crosses as its REALISED PATH, one dict per day, for the same
    reason and one more: the workers are threads sharing an address space, so
    handing them a driver that closed over mutable state would be a race
    waiting to happen. A list of dicts cannot be.
    """
    (seed, universe_json, macro_kwargs, days, ticks, hour, minute, day_of_week,
     collect, path, fingerprint, model) = args
    universe = Universe.from_json(universe_json)
    # The model crosses as the OBJECT, unlike the universe. Same reasoning,
    # opposite conclusion: what makes thread-shared state safe here is
    # immutability, and ModelParams is immutable by construction where a
    # Universe is a mutable list. Serialising it would round-trip through
    # from_dict for no isolation gained.
    engine = Engine(
        seed=seed,
        universe=universe,
        macro_state=Macro(**macro_kwargs) if macro_kwargs is not None else None,
        model=model,
    )
    for day in range(days):
        if path is not None:
            engine.pin_macro(**path[day])
        engine.open_market()
        engine.run_session(hour, minute, day_of_week, ticks)
        engine.close_market()

    if collect == "prices":
        return engine.prices()
    if collect == "attribution":
        # FACTOR_NAMES rather than Engine.FACTORS: same four names, but as
        # literals a checker can match against what attribution() accepts.
        return {
            # Stamped like the summary rows. This mode already returned a
            # dict, so there was room; `prices` returns raw bytes and has
            # none, so it is the one mode without provenance.
            "seed": seed,
            "universe_fingerprint": fingerprint,
            "model_fingerprint": engine.model_fingerprint,
            "columns": {name: engine.attribution(name)
                        for name in _harness.FACTOR_NAMES},
        }
    if collect == "summary":
        return {
            "seed": seed,
            # Which market this row belongs to. A sweep is the case where it
            # matters most: a hundred rows keyed only by seed look
            # interchangeable, and two sweeps over different rosters merge
            # into a table that is wrong in no visible way.
            #
            # `tickers` is deliberately NOT that identity. Tickers are
            # generated positionally, so two universes share every name and
            # no fundamentals.
            "universe_fingerprint": fingerprint,
            # And which model. One sweep runs one model, but the row is
            # what travels, and a row that cannot name its coefficient set
            # merges silently with rows from a different model's sweep.
            "model_fingerprint": engine.model_fingerprint,
            "prices": engine.prices(),
            "draws_consumed": engine.draws_consumed,
            "tickers": engine.tickers,
        }
    raise ValidationError(
        f"unknown collect {collect!r}. Valid: prices, attribution, summary"
    )


def run_many(
    seeds: Iterable[int],
    *,
    universe: Sequence[Instrument],
    macro: Macro | None = None,
    days: int = 1,
    ticks: int = 390,
    start: tuple[int, int, int] = (9, 30, 3),
    workers: int | None = None,
    collect: str = "prices",
    scenario: Any = None,
    model: str | ModelParams | None = None,
) -> list[Any]:
    """Run one simulation per seed, in parallel, and return results in order.

    ``results[i]`` is always the result for ``seeds[i]``. Results are ordered
    by input position and never by completion order, because an order that
    depended on which worker finished first would be non-deterministic, and
    the bug would look like noise.

    # Per seed is the only safe boundary

    This parallelises across seeds and nothing else, and that survives the
    2026-08 split of the engine's RNG into seven per-domain substreams
    (market, economy, external, jumps, volume, news and volume_idio, see
    ``tradefloor-docs: docs/rng-streams.md``), because the split is by
    domain and not by unit of work. The market stream alone serves every draw
    in a tick: the market factor, each sector factor, per-company noise, the
    intraday volume noise and book settlement, in one fixed order across the
    whole roster, with the Box-Muller spare cached per stream so even the
    parity of normal draws is that stream's state. Any within-run
    decomposition would repartition a single sequence, and the draw schedule
    does not survive that. The economy's separate stream means a pinned
    macro path no longer reshuffles the market's noise. It adds no
    concurrency, because the day-close economy step feeds the next day's
    pricing, so the domains run in sequence whatever their streams do.

    So a run cannot be parallelised *within* itself without changing the
    market. An ``n_threads=`` option could only be honoured that way.

    Each worker constructs its own engine from its own seed, so two workers
    share no state, and running with ``workers=1`` produces byte-identical
    results to any other worker count. A test asserts this.

    ``collect`` chooses what comes back: ``"prices"`` (raw f64 bytes),
    ``"attribution"`` (the seven component columns), or ``"summary"`` (prices,
    draw count and tickers).

    ``model`` selects the coefficient set, either a preset name or a
    :class:`tradefloor.ModelParams`, and every seed runs it. A sweep is many
    draws of one market, and members under different models would be a
    model comparison presented as a seed distribution. The ``summary``
    and ``attribution`` rows record ``model_fingerprint``.

    # Threads

    The engine releases the GIL for the whole session compute, so a thread
    pool gives real parallelism with no serialisation of the universe into
    each worker and no pickling of results back.

    Threads also work where a process pool does not. Windows spawns rather
    than forks, and spawning re-imports ``__main__`` in every child, which a
    notebook, a REPL and a piped script do not have. The children die, the
    parent waits, and the sweep hangs with no error. Those are the most
    likely ways to use this library, so a process pool would fail in the
    common case.

    ``workers`` does not default to the core count, because a sweep small
    enough to be interactive can be faster serially.

    A single seed always runs in-process, whatever ``workers`` says.

    Each seed is any integer from 0 to ``2**64 - 1``, checked here before any
    worker starts.
    """
    seeds = [_check_seed(s) for s in seeds]
    if not seeds:
        raise ValidationError("no seeds given")
    if days < 1 or ticks < 1:
        raise ValidationError("days and ticks must both be at least 1")

    universe_json = (
        universe.to_json() if isinstance(universe, Universe)
        else Universe(universe).to_json()
    )
    macro_kwargs = None if macro is None else {
        "vix": macro.vix,
        "federal_funds_rate": macro.federal_funds_rate,
        "corporate_bond_yield": macro.corporate_bond_yield,
        "inflation_rate": macro.inflation_rate,
        "qe_pe_boost": macro.qe_pe_boost,
        "fear_greed_index": macro.fear_greed_index,
        "cycle": macro.cycle,
    }
    hour, minute, day_of_week = start
    # The scenario is passed as its REALISED PATH rather than as the object.
    # A path is plain data, so a worker cannot be handed a driver that closes
    # over shared state, and the sweep records exactly what it ran.
    # `_pin_kwargs` is `at` plus the forced-VIX mark on the days a scenario
    # with `vix_sets_variance` on pins the VIX, so a sweep forces the same
    # sessions `Scenario.apply` would.
    path = None if scenario is None else [scenario._pin_kwargs(d) for d in range(days)]
    # Hashed ONCE, not per worker. The universe is the same for every seed,
    # and hashing it N times would be N times the work for one answer.
    fingerprint = _universe_util.fingerprint_of(universe)
    payloads = [
        (seed, universe_json, macro_kwargs, days, ticks, hour, minute,
         day_of_week, collect, path, fingerprint, model)
        for seed in seeds
    ]

    # One seed is not worth a pool.
    if workers is not None and workers <= 1 or len(seeds) == 1:
        return [_run_one(p) for p in payloads]

    from concurrent.futures import ThreadPoolExecutor

    # THREADS, not processes, because the engine releases the GIL for the
    # whole session compute. Three things follow, and the third is why this
    # was changed:
    #
    #   - no universe serialised into every worker
    #   - no pickling of results back
    #   - it works from a notebook, a REPL or a piped script
    #
    # A process pool did not. On Windows the spawn start method re-imports
    # `__main__` in each child, and a REPL, a notebook or a script fed on
    # stdin has no importable `__main__` -- so the children die and the parent
    # waits for results that will never arrive. Measured as a ten-minute hang
    # on a twenty-seed sweep, with no error and no output. Jupyter is where
    # this library is most likely to be used, and the process pool could not
    # run there.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # `map` yields in INPUT order regardless of completion order, which is
        # the property this function promises. Completion order would be
        # faster to yield and wrong.
        return list(pool.map(_run_one, payloads))


# --------------------------------------------------------------------------
# Order-flow impact
# --------------------------------------------------------------------------

class FlowImpact:
    """What a synthetic order-flow imbalance did to the market.

    It was called `Counterfactual` until the library had three
    counterfactuals (this, :func:`tradefloor.tca.analyse`, and
    :func:`tradefloor.scenario.compare`). This one does the narrowest job,
    so it is now named for what it measures.

    .. note::

       For **what your trading cost you**, use :func:`tradefloor.tca.analyse`.
       It runs an agent that actually executes against the book and prices
       every fill against the untraded world.

       This class measures the effect of an order imbalance fed to the
       factor model, which is the information channel alone. No order is
       submitted and no liquidity is consumed, so the book channel, where a
       large trade's cost actually comes from, is not exercised at all.

       The information channel is also bounded at both ends. Below about 1.3x the
       average minute volume a floor applies and the response is flat; above
       10x it saturates and is flat again. Doubling the flow outside the band
       between them changes nothing, so this is the wrong tool for asking how
       cost scales with size.

       Under the shipped default it also divides by each name's depth
       twice, so at equal participation a thin name is charged far more
       than a liquid one. Pass a model with ``order_flow_depth_law=1.0`` to
       compare names; it divides once.

    
    Two runs of the SAME seed, one with the trader's order flow and one without,
    and the difference between them.

    A real market can't give you this. You observe the price you got, but
    never the price you would have got had you not traded, because your
    trading is part of why that price happened. Here both worlds run, so
    impact is measured instead of estimated from a model of impact.

    ``impact[i]`` is ``actual[i] - baseline[i]`` for instrument ``i``. Positive
    means the trader pushed the price up, which is a cost for a buyer, so
    ``cost_bps`` flips the sign by side.
    """

    __slots__ = ("tickers", "baseline", "actual", "seed", "_flow")

    def __init__(self, tickers, baseline, actual, seed, flow):
        self.tickers = list(tickers)
        self.baseline = list(baseline)
        self.actual = list(actual)
        self.seed = seed
        self._flow = dict(flow)

    @property
    def impact(self) -> list[float]:
        """Price difference caused by the trader, per instrument."""
        return [a - b for a, b in zip(self.actual, self.baseline)]

    @property
    def impact_bps(self) -> list[float]:
        """Impact in basis points of the baseline price.

        Basis points because impact is only comparable across instruments
        once it is relative. A penny on a $3 stock and a penny on a $600
        stock are different moves.
        """
        return [
            (a - b) / b * 10_000 if b != 0 else float("nan")
            for a, b in zip(self.actual, self.baseline)
        ]

    def cost_bps(self, ticker: str) -> float:
        """Impact expressed as a COST to the trader, in basis points.

        Positive always means worse for the trader. A buyer who pushed the
        price up paid for it, and so did a seller who pushed it down.
        """
        i = self.tickers.index(ticker)
        bps = self.impact_bps[i]
        buy, sell = self._flow.get(ticker, (0.0, 0.0))
        if buy == sell:
            return abs(bps)
        return bps if buy > sell else -bps

    def traded(self) -> list[str]:
        """Tickers the trader actually touched, in roster order."""
        return [t for t in self.tickers if t in self._flow]

    def untouched_moved(self) -> list[str]:
        """Instruments the trader did not touch, whose price still moved.

        Empty on a one-day run where the close writes no price, because
        order flow consumes no draws. Flow is an input to the factor
        calculation and makes no call on the generator, so adding flow to
        one name leaves the shared draw schedule byte-identical. Every other
        name sees exactly the noise it would have seen, and its prints
        through the session are the same to the bit on every preset.

        The prices compared here are read after the close, and on pt-v20
        and pt-v21, the default, the close re-marks every name to the macro state it
        publishes (``macro_publication_repricing``). The close's macro step
        reads the session's index return, which the flow moved (the VIX,
        the 10-year's flight to quality, the corporate yield that follows
        it), so on a one-day run the untouched names end apart by the
        difference in their re-marks: +0.03 to +0.04 bps against +2.3 on
        the traded name in ``tests/test_flow_impact.py``. That is the
        market-wide channel described below arriving at the first close. It
        is not a leak. This function cannot pin the macro path.
        ``tradefloor.tca.analyse`` with
        ``scenario=Scenario().hold(vix=..., corporate_bond_yield=...)``
        can, and a model with ``macro_publication_repricing`` 0 writes no
        price at the close.

        Measured on this build, a 390-tick session consumes 19,110 draws
        with or without flow. Adding an instrument does shift the schedule
        (4,900 draws at six names against 5,500 at seven), so a roster edit
        does not give you a counterfactual.

        Over several days one non-noise channel qualifies the emptiness,
        since the 2026-08 VIX coupling. Flow that moves the cap-weighted
        market return moves the same-day VIX, VIX sets the shared factor's
        variance target, and untraded names feel it two closes later. The
        effect is intermittent, because the VIX reaction clamps the market
        return at +/-0.03%, but it is real. Measured on this build, flow of
        200,000 shares against the first name of
        ``Universe.random(20, seed=7)`` run for ten days leaks nothing at
        sim seeds 2026 and 7 and moves one untouched name +22.8 bps at sim
        seed 11. Pin VIX in both worlds to restore byte-exactness, and on
        pt-v20 the corporate bond yield too, which that preset moves at
        every close with the market. The ``moved()`` docstring in
        ``tradefloor.tca`` has the full measurement of the channel.

        So on a one-day run impact is exactly attributable to the names
        traded up to the close's re-mark on pt-v20 (exactly, where the
        close writes no price), and on a longer one it is attributable up
        to the fear gauge. Use this accessor to check that. A result that a
        pinned VIX does not empty means something leaked, and is worth
        investigating.
        """
        touched = set(self._flow)
        return [
            t for t, a, b in zip(self.tickers, self.actual, self.baseline)
            if t not in touched and a != b
        ]

    def __repr__(self) -> str:
        traded = self.traded()
        return (
            f"FlowImpact(seed={self.seed}, traded={traded}, "
            f"impact_bps={[round(self.cost_bps(t), 2) for t in traded]})"
        )


def flow_impact(
    *,
    seed: int,
    universe: Sequence[Instrument],
    order_flow: dict[str, tuple[float, float]],
    macro: Macro | None = None,
    days: int = 1,
    ticks: int = 390,
    start: tuple[int, int, int] = (9, 30, 3),
    model: str | ModelParams | None = None,
) -> FlowImpact:
    """Measure what an order-flow imbalance does to the market.

    Runs the same seed twice, once with ``order_flow`` and once without, and
    returns both worlds plus their difference.

    ``order_flow`` is a standing rate: ``{ticker: (bought, sold)}`` shares
    on every tick of each day's session, the ``flow_per_tick`` argument of
    :meth:`Engine.run_session`. So ``(6e6, 0.0)`` over the default 390 ticks
    is a day-long program of 2.34 billion shares. One agent's one trade is
    ``fills`` instead, which reaches the market once, and
    :func:`tradefloor.tca.analyse` measures that.

    The two runs are otherwise identical: same seed, same universe, same
    macro, same session, and the same ``model``, either a preset name or a
    :class:`tradefloor.ModelParams`, applied to both worlds. A baseline
    under other coefficients would measure the model and not the flow.
    Anything else that differed between the runs would show up as impact
    and be wrong.
    """
    if not order_flow:
        raise ValidationError(
            "order_flow is empty - a counterfactual with no trading has "
            "nothing to measure"
        )

    def run(flow):
        engine = Engine(seed=seed, universe=universe, macro_state=macro,
                        model=model)
        for _ in range(days):
            engine.open_market()
            engine.run_session(*start, ticks, flow_per_tick=flow)
            engine.close_market()
        return engine

    # The FLOW world first, so a validation error in the flow surfaces before
    # any work is done rather than after half of it.
    with_flow = run(order_flow)
    without = run(None)

    import struct
    def unpack(buf):
        return list(struct.unpack("<%dd" % (len(buf) // 8), buf))

    return FlowImpact(
        tickers=with_flow.tickers,
        baseline=unpack(without.prices()),
        actual=unpack(with_flow.prices()),
        seed=seed,
        flow=order_flow,
    )
