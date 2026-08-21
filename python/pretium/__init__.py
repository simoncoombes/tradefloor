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
from ._core import (  # noqa: F401
    Engine,
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
    sectors,
    step_mispricing_daily,
)

__version__ = _core.__version__
__all__ = [
    "Engine", "FairValue", "Fill", "GameRng", "Instrument", "Macro",
    "MatchResult", "MispricingState", "News", "NewsImpact", "OrderBook",
    "OrderError", "PriceLevel",
    "SweepCost", "TickResult", "Universe", "ValidationError",
    "apply_mispricing", "characteristic_root_moduli", "check_rate",
    "crowd_adjusted_root_moduli", "fair_value", "impulse_response",
    "market_status", "model_preset", "run_many", "sectors", "step_mispricing_daily",
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
        return {name: engine.attribution(name) for name in Engine.FACTORS}
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
    ``"attribution"`` (the four factor columns), or ``"summary"`` (prices,
    draw count and tickers).

    # Workers are not free, and below a threshold they lose

    Measured on 60 instruments, one session each:

        4 seeds    serial 0.23s    8 workers 0.47s    0.50x  (slower)
        16 seeds   serial 1.04s    8 workers 0.58s    1.80x

    Spawning a pool and shipping the universe to each worker costs more than a
    handful of short runs. The crossover sits somewhere around a dozen seeds on
    this machine, and it moves with instrument count and session length. So
    ``workers`` is not defaulted to the core count: a sweep small enough to be
    interactive is faster serially, and quietly making it slower would be a
    poor default dressed as a good one.

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

    # One seed is not worth a process. Spawning a pool costs more than the run.
    if workers is not None and workers <= 1 or len(seeds) == 1:
        return [_run_one(p) for p in payloads]

    import multiprocessing

    with multiprocessing.Pool(processes=workers) as pool:
        # `map` preserves input order regardless of completion order, which is
        # the property this function promises. `imap_unordered` would be
        # faster and wrong.
        return pool.map(_run_one, payloads)
