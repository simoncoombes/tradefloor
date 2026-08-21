"""pretium — a deterministic market simulator with a real limit order book.

The compiled engine lives in ``pretium._core``. This package re-exports it and
adds the parts that are better written in Python than in Rust: JSON
round-trips, and process-level parallelism for seed sweeps. Forcing those into
the extension would mean hand-rolling JSON escaping and reimplementing a
process pool, both worse than the standard library versions.

Determinism is the point. The same seed and the same inputs are designed to
produce bit-identical output on Linux, macOS and Windows, because the library
ships its own transcendental maths rather than calling the platform's libm.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from . import _core
from .portfolio import Portfolio, Position
from . import harness as _harness
from .harness import Agent, Observation, Scorecard, evaluate, leaderboard
from .replay import replay
from . import edgar
from . import baselines
from . import tca
from .tca import Execution
from .baselines import capture_ratio, reference_agents
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
    MispricingState,
    News,
    NewsImpact,
    OrderBook,
    OrderError,
    PriceLevel,
    SweepCost,
    TickResult,
    ValidationError,
    apply_mispricing,
    characteristic_root_moduli,
    check_rate,
    crowd_adjusted_root_moduli,
    fair_value,
    impulse_response,
    market_status,
    model_preset,
    sector_daily_sigma,
    sectors,
    stationary_sigma,
    step_mispricing_daily,
    version,
)

__version__ = _core.__version__
__all__ = [
    "ArrowStream", "Engine", "EngineBatch", "FairValue", "Fill", "GameRng", "Instrument", "Macro",
    "MatchResult", "MispricingState", "News", "NewsImpact", "OrderBook",
    "OrderError", "PriceLevel",
    "SweepCost", "TickResult", "Universe", "ValidationError", "Counterfactual",
    "counterfactual", "Portfolio", "Position", "Agent", "Observation",
    "Scorecard", "evaluate", "leaderboard", "replay", "edgar",
    "baselines", "reference_agents", "capture_ratio", "tca", "Execution",
    "apply_mispricing", "characteristic_root_moduli", "check_rate",
    "crowd_adjusted_root_moduli", "fair_value", "impulse_response",
    "market_status", "model_preset", "run_many", "sector_daily_sigma", "sectors",
    "stationary_sigma", "step_mispricing_daily", "version",
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

    A ``list`` subclass, deliberately. Roster order is contractual — the engine
    iterates instruments in index order and draws as it goes, so a reordered
    universe is a different market from the same seed. Modelling it as a
    mapping would invite exactly the ``{ticker: instrument}`` dict that has no
    stable order, and a ``sort_by(ticker)`` somewhere upstream would be a
    silent, total divergence.

    Subclassing ``list`` also means it is accepted anywhere a list is, so the
    engine needs no special case.
    """

    @classmethod
    def random(cls, n: int = 108, *, seed: int = 0) -> "Universe":
        """Generate ``n`` plausible instruments.

        A generator is not a convenience here. A realistic study needs on the
        order of a hundred names, and nobody hand-authors a hundred rosters —
        so without this the practical universe size is "however many someone
        was willing to type", which is not a modelling choice anyone made.

        ``seed`` is the UNIVERSE seed and is independent of the simulation
        seed. That separation is what makes "same universe, different market
        draws" expressible, which is the standard design for variance
        estimation.

        The generated cross-section is plausible rather than uniform: market
        caps are log-distributed across all four spread tiers, P/E ratios
        scatter around each sector's anchor, and about one name in nine is a
        loss-maker so the book-value valuation path is actually exercised.
        Those ranges are editorial and are not pinned by any golden vector;
        what IS guaranteed is that ``(n, seed)`` gives the same universe on
        every platform, which is what makes ``random(108, seed=7)`` citable.
        """
        return cls(_core.random_instruments(n, seed=seed))

    @classmethod
    def from_edgar(cls, snapshot, **kwargs: Any) -> "Universe":
        """Build a universe from an EDGAR snapshot. Pure and reproducible.

        Takes a :class:`pretium.edgar.Snapshot` or a path to a saved one.
        Extra keyword arguments are the macro conditions the fair values are
        computed under, and they must match the macro the engine then runs --
        otherwise every company starts mispriced by the difference, which is a
        quiet way to get a universe nobody specified.
        """
        from . import edgar as _edgar

        if isinstance(snapshot, str):
            snapshot = _edgar.Snapshot.load(snapshot)
        return cls(_edgar.to_instruments(snapshot, **kwargs))

    def to_json(self, **kwargs: Any) -> str:
        """Serialise to JSON.

        A citable specification has to be able to name its universe exactly,
        and ``random(108, seed=7)`` is only citable while the generator is
        versioned with the model. Serialising the roster itself is the escape
        hatch: it pins the exact instruments regardless of what any later
        generator would produce.
        """
        payload = {
            "schema": _UNIVERSE_SCHEMA,
            "instruments": [
                {field: getattr(inst, field) for field in _INSTRUMENT_FIELDS}
                for inst in self
            ],
        }
        kwargs.setdefault("indent", 2)
        return json.dumps(payload, **kwargs)

    @classmethod
    def from_json(cls, text: str) -> "Universe":
        """Rebuild a universe from :meth:`to_json` output.

        Order is preserved exactly, because it is contractual.

        A newer schema is refused rather than read on a best-effort basis. A
        field this version does not know about would silently take a default,
        and the resulting universe would be one nobody specified — the same
        class of failure as a truncated golden file passing quietly.
        """
        payload = json.loads(text)
        if not isinstance(payload, dict) or "instruments" not in payload:
            raise ValidationError("not a pretium universe document")

        schema = payload.get("schema", 0)
        if schema > _UNIVERSE_SCHEMA:
            raise ValidationError(
                f"universe schema {schema} is newer than this version "
                f"understands ({_UNIVERSE_SCHEMA}). Upgrade pretium rather "
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
# Seed sweeps
# --------------------------------------------------------------------------

def _run_one(args: tuple) -> Any:
    """One seed, in one worker. Module-level so it is picklable.

    The universe crosses as JSON rather than as objects. That is not a
    workaround — a serialised universe is the same universe by construction
    (there is a test), and it means a worker rebuilds from a specification
    rather than depending on whatever a pickle happened to preserve.
    """
    seed, universe_json, macro_kwargs, days, ticks, hour, minute, day_of_week, collect = args
    universe = Universe.from_json(universe_json)
    engine = Engine(
        seed=seed,
        universe=universe,
        macro_state=Macro(**macro_kwargs) if macro_kwargs is not None else None,
    )
    for _ in range(days):
        engine.open_market()
        engine.run_session(hour, minute, day_of_week, ticks)
        engine.close_market()

    if collect == "prices":
        return engine.prices()
    if collect == "attribution":
        # FACTOR_NAMES rather than Engine.FACTORS: same four names, but as
        # literals a checker can match against what attribution() accepts.
        return {name: engine.attribution(name) for name in _harness.FACTOR_NAMES}
    if collect == "summary":
        return {
            "seed": seed,
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
) -> list[Any]:
    """Run one simulation per seed, in parallel, and return results in order.

    ``results[i]`` is always the result for ``seeds[i]`` — ordered by INPUT
    POSITION, never by completion order. A sweep whose output order depended on
    which worker finished first would be non-deterministic in the one way this
    library exists to avoid, and the bug would look like noise.

    # Per seed is the only safe boundary

    This parallelises across seeds and nothing else. Internally the engine uses
    ONE shared RNG stream across the market, the economy and the
    microstructure, with the Box-Muller spare cached on it — so even the parity
    of normal draws is shared state. There is no per-module or per-company
    decomposition that preserves the draw schedule.

    Parallelising *within* a run is therefore off the table by construction,
    not merely unimplemented. If you find yourself wanting ``n_threads=``, the
    honest answer is that it could only be honoured by changing the market.

    Each worker constructs its own engine from its own seed, so the isolation
    is total: two workers share no state, and running with ``workers=1``
    produces byte-identical results to any other worker count. That is
    asserted by a test rather than assumed.

    ``collect`` chooses what comes back: ``"prices"`` (raw f64 bytes),
    ``"attribution"`` (the seven component columns), or ``"summary"`` (prices,
    draw count and tickers).

    # Threads, and why that is not a compromise

    The engine releases the GIL for the whole session compute, so a thread
    pool gives real parallelism with no serialisation of the universe into
    each worker and no pickling of results back.

    It also WORKS in the places a process pool does not. Windows spawns rather
    than forks, and spawning re-imports ``__main__`` in every child -- which a
    notebook, a REPL and a piped script do not have. The children die, the
    parent waits, and the sweep hangs with no error. That is the single most
    likely way to use this library, so the process pool was not an
    optimisation with an edge case; it was broken where it mattered most.

    ``workers`` is still not defaulted to the core count: a sweep small enough
    to be interactive can be faster serially, and quietly making it slower
    would be a poor default dressed as a good one.

    A single seed always runs in-process, whatever ``workers`` says.
    """
    seeds = list(seeds)
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
    payloads = [
        (seed, universe_json, macro_kwargs, days, ticks, hour, minute, day_of_week, collect)
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
    # this library is most likely to be used, and that is exactly where the
    # process pool could not run.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        # `map` yields in INPUT order regardless of completion order, which is
        # the property this function promises. Completion order would be
        # faster to yield and wrong.
        return list(pool.map(_run_one, payloads))


# --------------------------------------------------------------------------
# Counterfactuals
# --------------------------------------------------------------------------

class Counterfactual:
    """What a synthetic order-flow imbalance did to the market.

    .. note::

       For **what your trading cost you**, use :func:`pretium.tca.analyse`.
       It runs an agent that actually executes against the book and prices
       every fill against the untraded world.

       This measures one specific, narrower thing: the effect of an order
       IMBALANCE fed to the factor model, which is the information channel
       alone. No order is submitted and no liquidity is consumed, so the book
       channel -- where a large trade's cost actually comes from -- is not
       exercised at all.

       That channel is also bounded at both ends. Below about 1.3x the
       average minute volume a floor applies and the response is flat; above
       10x it saturates and is flat again. Doubling the flow outside the band
       between them changes nothing, so this is the wrong tool for asking how
       cost scales with size.

    
    Two runs of the SAME seed — one with the trader's order flow, one without —
    and the difference between them.

    This is the measurement no real market can provide. In a real market you
    observe the price you got; you can never observe the price you would have
    got had you not traded, because your trading is part of why that price
    happened. Here both worlds are runnable, so impact is measured rather than
    estimated from a model of impact.

    ``impact[i]`` is ``actual[i] - baseline[i]`` for instrument ``i``: positive
    means the trader pushed the price up. For a buyer that is a cost — they
    moved the market against themselves — which is why ``cost_bps`` flips sign
    by side rather than reporting a signed impact and leaving the reader to
    work out which direction hurt.
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

        Basis points rather than currency because impact is only comparable
        across instruments once it is relative — a penny on a $3 stock and a
        penny on a $600 stock are not the same event.
        """
        return [
            (a - b) / b * 10_000 if b != 0 else float("nan")
            for a, b in zip(self.actual, self.baseline)
        ]

    def cost_bps(self, ticker: str) -> float:
        """Impact expressed as a COST to the trader, in basis points.

        Signed so that positive always means worse for them: a buyer who
        pushed the price up paid for it, and a seller who pushed it down did
        too. Reporting raw signed impact and leaving the caller to reason
        about direction is how sign errors get into published numbers.
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

        Normally EMPTY, and the reason is what makes this measurement clean:
        order flow consumes no draws. It is an input to the factor
        calculation, not a call on the generator, so adding flow to one name
        leaves the shared draw schedule byte-identical and every other name
        follows exactly the path it would have followed.

        Measured, not assumed: a 390-tick session consumes 19,110 draws with
        or without flow. (Adding an INSTRUMENT is a different matter and does
        shift the schedule -- 4,900 draws at six names against 5,500 at seven
        -- which is why a roster edit is not a counterfactual.)

        So impact is exactly attributable to the names traded, rather than
        being a signal buried in a shifted market. This accessor exists to
        prove that rather than to explain it away: a non-empty result means
        something leaked, and is worth investigating.
        """
        touched = set(self._flow)
        return [
            t for t, a, b in zip(self.tickers, self.actual, self.baseline)
            if t not in touched and a != b
        ]

    def __repr__(self) -> str:
        traded = self.traded()
        return (
            f"Counterfactual(seed={self.seed}, traded={traded}, "
            f"impact_bps={[round(self.cost_bps(t), 2) for t in traded]})"
        )


def counterfactual(
    *,
    seed: int,
    universe: Sequence[Instrument],
    order_flow: dict[str, tuple[float, float]],
    macro: Macro | None = None,
    days: int = 1,
    ticks: int = 390,
    start: tuple[int, int, int] = (9, 30, 3),
) -> Counterfactual:
    """Measure what a trader's own flow did to the market.

    Runs the same seed twice — once with ``order_flow`` and once without — and
    returns both worlds plus their difference.

    The two runs are otherwise identical by construction: same seed, same
    universe, same macro, same session. The ONLY difference is the flow, which
    is what makes the subtraction meaningful. Anything else that differed
    between them would show up as impact and be wrong.
    """
    if not order_flow:
        raise ValidationError(
            "order_flow is empty - a counterfactual with no trading has "
            "nothing to measure"
        )

    def run(flow):
        engine = Engine(seed=seed, universe=universe, macro_state=macro)
        for _ in range(days):
            engine.open_market()
            engine.run_session(*start, ticks, order_flow=flow)
            engine.close_market()
        return engine

    # The FLOW world first, so a validation error in the flow surfaces before
    # any work is done rather than after half of it.
    with_flow = run(order_flow)
    without = run(None)

    import struct
    def unpack(buf):
        return list(struct.unpack("<%dd" % (len(buf) // 8), buf))

    return Counterfactual(
        tickers=with_flow.tickers,
        baseline=unpack(without.prices()),
        actual=unpack(with_flow.prices()),
        seed=seed,
        flow=order_flow,
    )
