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
    "MatchResult", "MispricingState", "OrderBook", "OrderError", "PriceLevel",
    "SweepCost", "TickResult", "Universe", "ValidationError",
    "apply_mispricing", "characteristic_root_moduli", "check_rate",
    "crowd_adjusted_root_moduli", "fair_value", "impulse_response",
    "market_status", "model_preset", "sectors", "step_mispricing_daily",
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
