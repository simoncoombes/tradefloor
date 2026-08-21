"""Real fundamentals as initial conditions.

Nothing in the model requires fictional companies. Fair value reads
`eps`, `book_value_per_share`, `revenue_growth` and a sector anchor; the
liquidity dial reads shares and volume. Those are numbers, and the SEC
publishes them, structured, in the public domain.

Seeding from real filings gives an experiment whose **cross-sectional
structure is real** — the true dispersion of valuations, actual sector
weights, real loss-makers in realistic proportion — while every price path
stays synthetic. For anything cross-sectional that is a materially better
test bed than a generated universe, which only has the dispersion its
generator was told to have.

## Be precise about which of three things this is

1. **A synthetic universe** — ``Universe.random``. Works.
2. **Real fundamentals as initial conditions** — this. Works.
3. **Replicating a specific company's realised behaviour** — does **not**
   work, and this needs saying before a user discovers it. The dynamics are
   the preset's, not the company's: the GARCH coefficients are model-global,
   base variance and anchor P/E are sector-level, and beta and spread are
   fitted to no name's history. A loaded ticker is *a stock with that
   company's fundamentals under this model's assumptions* — not that company,
   not its volatility, not its microstructure. Making mode 3 real needs a
   per-name calibration layer, which is a different product.

## The fetch and the universe are separate operations

Deliberately, because they have different determinism properties. Fetching
does I/O and cannot be reproducible: **EDGAR is not append-only.** Companies
amend and restate, so the same query run today and next year returns
different numbers.

So the *snapshot* is the artifact, not the query. ``fetch`` produces a frozen,
hashable snapshot; ``Universe.from_edgar`` is pure and takes one. Re-running
``fetch`` is expected to produce a different hash — that is the design
working, not failing.

This is the same discipline applied to the golden vectors: pin the artifact,
because a rebuild would silently change the reference.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Sequence

from ._core import (
    GameRng,
    Instrument,
    ValidationError,
    apply_mispricing,
    fair_value,
    model_preset,
    sector_daily_sigma,
    sectors,
    stationary_sigma,
)

# Bumped when a derivation or the SIC mapping changes. A snapshot records the
# version that built it, because changing a derivation changes universes — the
# same reason coefficients ship as a versioned preset rather than as defaults.
LOADER_VERSION = 1

# 0.5% daily turnover. The engine's own fallback assumption, materialised into
# the snapshot rather than left as an absent field: a citable snapshot should
# contain the numbers actually used, not a hole the engine fills invisibly.
TURNOVER = 0.005

# The stream initial mispricing is drawn from. Distinct from the market stream
# and from universe generation, so seeding a universe's dispersion cannot
# perturb the market it is built for -- the same isolation the generator has.
MISPRICING_STREAM = 37


def _beta_for(sector: str) -> float:
    """Deterministic sector beta.

    A flat 1.0 would erase the sector structure the spread model wants. This
    is the deterministic centre of the generator a synthetic universe uses,
    so loaded and generated universes get the same cross-sector beta
    structure — without consuming an RNG draw, which would make loading a
    universe perturb a market.

    Fitting real betas needs return history. That is the calibration layer
    mode 3 would require, not this loader.
    """
    from ._core import sector_volatility

    return 0.3 + 0.7 * sector_volatility(sector)


class Snapshot:
    """A frozen set of filings, hashable and serialisable.

    The reproducible input to an experiment. A citable specification names it
    by hash alongside the seed, preset and macro path.
    """

    __slots__ = ("as_of", "source", "loader_version", "rows", "excluded", "notes")

    def __init__(self, *, as_of: str, rows: Sequence[dict], source: str = "edgar",
                 excluded: Sequence[dict] = (), notes: dict | None = None):
        self.as_of = as_of
        self.source = source
        self.loader_version = LOADER_VERSION
        self.rows = [dict(r) for r in rows]
        self.excluded = [dict(r) for r in excluded]
        self.notes = dict(notes or {})

    # -- serialisation ----------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": 1,
            "source": self.source,
            "as_of": self.as_of,
            "loader_version": self.loader_version,
            "rows": self.rows,
            "excluded": self.excluded,
            "notes": self.notes,
        }

    def to_json(self) -> str:
        # sort_keys and a fixed separator, so the serialisation is canonical
        # and the hash is a property of the CONTENT rather than of dict
        # ordering. A hash that moved when a key order changed would be
        # useless as an identifier.
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))

    @property
    def hash(self) -> str:
        """sha256 over the canonical serialisation."""
        return hashlib.sha256(self.to_json().encode("utf-8")).hexdigest()

    def save(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(self.to_dict(), sort_keys=True, indent=2))
        return self.hash

    @classmethod
    def load(cls, path: str) -> "Snapshot":
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        return cls.from_dict(payload)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Snapshot":
        if payload.get("schema", 0) > 1:
            raise ValidationError(
                f"snapshot schema {payload['schema']} is newer than this "
                "version understands. Upgrade pretium rather than reading it "
                "partially."
            )
        snap = cls(
            as_of=payload["as_of"],
            rows=payload["rows"],
            source=payload.get("source", "edgar"),
            excluded=payload.get("excluded", ()),
            notes=payload.get("notes"),
        )
        # Preserve the version that BUILT it, not the version reading it. A
        # snapshot built by loader 1 and read by loader 2 is still a loader-1
        # artifact, and relabelling it would erase the provenance that makes
        # it citable.
        snap.loader_version = payload.get("loader_version", LOADER_VERSION)
        return snap

    def __len__(self) -> int:
        return len(self.rows)

    def __repr__(self) -> str:
        return (
            f"Snapshot({self.source!r}, as_of={self.as_of!r}, "
            f"{len(self.rows)} filers, {len(self.excluded)} excluded, "
            f"hash={self.hash[:12]}...)"
        )


def to_instruments(
    snapshot: Snapshot,
    *,
    federal_funds_rate: float = 0.025,
    corporate_bond_yield: float | None = None,
    qe_pe_boost: float = 0.0,
    initial_s: str = "zero",
    s_seed: int = 0,
) -> list[Instrument]:
    """Build instruments from a snapshot. Pure and reproducible.

    # Prices start at fair value

    EDGAR has no market data, and the loader does not invent any. Every
    company starts at its own computed fair value, so initial mispricing is
    exactly zero.

    That is well-defined, needs no second data source, and is consistent with
    a fundamentals-anchored model. The honest cost, stated rather than hidden:
    a universe that starts perfectly priced has **no initial mispricing
    dispersion**, so a strategy that harvests mispricing sees nothing until
    shocks accumulate — on the order of one 60-day half-life. Run a burn-in
    before handing control to an agent if that matters.

    # initial_s="stationary" starts the universe where a long run would be

    ``"zero"`` (the default) prices everything at fair value, which is honest
    and has the cost above. ``"stationary"`` instead draws each company's
    mispricing from the distribution the process settles into, so the universe
    begins with realistic cross-sectional dispersion — around 19% for a
    technology name and 6% for consumer staples, from each sector's own
    long-run volatility.

    That is not a fudge: it is the distribution the model itself implies, and
    the width is computed from the AR(2) parameters rather than chosen. The
    draw uses its own RNG stream, so seeding a universe's dispersion cannot
    perturb the market it is built for.

    The macro arguments are the conditions the fair value is computed under.
    They must match the macro the engine then runs, or every company starts
    mispriced by the difference — which is a subtle way to get a universe
    nobody specified.
    """
    if initial_s not in ("zero", "stationary"):
        raise ValidationError(
            f"initial_s must be \"zero\" or \"stationary\", got {initial_s!r}"
        )
    rng = GameRng(int(s_seed), MISPRICING_STREAM)
    cap = model_preset()["mispricing_cap"]
    out: list[Instrument] = []
    for i, row in enumerate(snapshot.rows):
        missing = {"ticker", "sector", "eps", "shares_outstanding"} - set(row)
        if missing:
            raise ValidationError(f"row {i} is missing {sorted(missing)}")

        value = fair_value(
            eps=row["eps"],
            sector=row["sector"],
            revenue_growth=row.get("revenue_growth", 0.0),
            federal_funds_rate=federal_funds_rate,
            corporate_bond_yield=corporate_bond_yield,
            qe_pe_boost=qe_pe_boost,
            book_value_per_share=row.get("book_value_per_share"),
        )
        shares = row["shares_outstanding"]

        price = value.fair_value
        if initial_s == "stationary":
            width = stationary_sigma(sector_daily_sigma(row["sector"]))
            if width is None:
                # Non-stationary parameters cannot produce a distribution to
                # draw from. Falling back to fair value is right: it is the
                # documented default, not a guess.
                pass
            else:
                s_value = rng.next_normal() * width
                # Clamped to the model's own cap, so a tail draw cannot start
                # a company outside the range the process can reach.
                s_value = max(-cap, min(cap, s_value))
                price = apply_mispricing(value.fair_value, s_value)

        out.append(Instrument(
            row["ticker"], row["sector"],
            initial_price=price,
            shares_outstanding=shares,
            eps=row["eps"],
            book_value_per_share=row.get("book_value_per_share"),
            revenue_growth=row.get("revenue_growth", 0.0),
            # Materialised, not left to the engine's fallback chain: a citable
            # snapshot should contain the numbers actually used.
            avg_volume=row.get("avg_volume", max(1.0, shares * TURNOVER)),
            beta=row.get("beta", _beta_for(row["sector"])),
        ))
    return out


def filter_rows(rows: Sequence[dict], *, exclude_negative_equity: bool = True):
    """Split rows into keepers and exclusions, with a reason on each.

    Negative-equity loss-makers are excluded by default. A company with
    negative EPS takes the book-value valuation path, and negative book value
    there produces a negative fair value which the floor then clamps — so the
    name would trade at the floor with no fundamental anchor at all. That is
    not a stock, it is a constant.

    Excluded rows are RETURNED, not dropped. A loader that silently discarded
    a tenth of the market would produce a universe whose composition nobody
    chose, and the exclusions are often the interesting part.
    """
    keep, drop = [], []
    for row in rows:
        eps = row.get("eps")
        book = row.get("book_value_per_share")
        if eps is None:
            drop.append({**row, "reason": "no eps"})
        elif row.get("shares_outstanding", 0) <= 0:
            drop.append({**row, "reason": "no share count"})
        elif exclude_negative_equity and eps <= 0 and (book is None or book <= 0):
            drop.append({**row, "reason": "loss-maker with negative or missing equity"})
        else:
            keep.append(row)
    return keep, drop


def fetch(*args, **kwargs):
    """Fetch filings from EDGAR. Requires the ``edgar`` extra.

    Separated from :func:`to_instruments` because the two have different
    determinism properties: this does I/O and cannot be reproducible, while
    building a universe from a snapshot is pure. The engine never touches a
    socket.
    """
    raise NotImplementedError(
        "pretium.edgar.fetch is not implemented yet. The pure half -- Snapshot, "
        "filter_rows and to_instruments -- is complete and testable offline; "
        "the network fetcher is the remaining piece. Build a Snapshot by hand "
        "or load one with Snapshot.load(path) in the meantime."
    )
