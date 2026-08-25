"""Real fundamentals as initial conditions.

Nothing in the model requires fictional companies. Fair value reads
`eps`, `book_value_per_share`, `revenue_growth` and a sector anchor; the
liquidity dial reads shares and volume. Those are numbers, and the SEC
publishes them, structured, in the public domain.

Seeding from real filings gives an experiment whose **cross-sectional
structure is real**, meaning the true dispersion of valuations, actual sector
weights and real loss-makers in realistic proportion, while every price path
stays synthetic. For anything cross-sectional that is a materially better
test bed than a generated universe, which only has the dispersion its
generator was told to have.

## Be precise about which of three things this is

1. **A synthetic universe**, via ``Universe.random``. Works.
2. **Real fundamentals as initial conditions**, which is this. Works.
3. **Replicating a specific company's realised behaviour**, which does **not**
   work, and this needs saying before a user discovers it. The dynamics are
   the preset's, not the company's: the GARCH coefficients are model-global,
   base variance and anchor P/E are sector-level, and beta and spread are
   fitted to no name's history. A loaded ticker is *a stock with that
   company's fundamentals under this model's assumptions*, not that company,
   not its volatility, not its microstructure. Making mode 3 real needs a
   per-name calibration layer, which is a different product.

## The fetch and the universe are separate operations

Deliberately, because they have different determinism properties. Fetching
does I/O and cannot be reproducible: **EDGAR is not append-only.** Companies
amend and restate, so the same query run today and next year returns
different numbers.

So the *snapshot* is the artifact, not the query. ``fetch`` produces a frozen,
hashable snapshot; ``Universe.from_edgar`` is pure and takes one. Re-running
``fetch`` is expected to produce a different hash, and that is the design
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
# version that built it, because changing a derivation changes universes: the
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
    structure, without consuming an RNG draw, which would make loading a
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
    shocks accumulate, on the order of one 60-day half-life. Run a burn-in
    before handing control to an agent if that matters.

    # initial_s="stationary" starts the universe where a long run would be

    ``"zero"`` (the default) prices everything at fair value, which is honest
    and has the cost above. ``"stationary"`` instead draws each company's
    mispricing from the distribution the process settles into, so the universe
    begins with realistic cross-sectional dispersion, around 19% for a
    technology name and 6% for consumer staples, from each sector's own
    long-run volatility.

    That is not a fudge: it is the distribution the model itself implies, and
    the width is computed from the AR(2) parameters rather than chosen. The
    draw uses its own RNG stream, so seeding a universe's dispersion cannot
    perturb the market it is built for.

    The macro arguments are the conditions the fair value is computed under.
    They must match the macro the engine then runs, or every company starts
    mispriced by the difference, which is a subtle way to get a universe
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
    there produces a negative fair value which the floor then clamps, so the
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


# ---------------------------------------------------------------------------
# The network half
# ---------------------------------------------------------------------------

# The frames API returns every filer that reported one concept for one period
# in a single response. That is the whole reason a market-wide fetch is
# practical: five requests instead of one per company. The per-company
# submissions endpoint is then hit only for the names actually kept, and only
# because SIC is the one field the frames do not carry.
_FRAMES = "https://data.sec.gov/api/xbrl/frames"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"

# EPS, equity and share count each have one obvious tag. Revenue does not:
# ASC 606 filers use RevenueFromContractWithCustomer..., older and financial
# filers use Revenues or SalesRevenueNet. Tried in order, first hit wins, and
# a company matching none simply has no growth figure rather than a zero --
# which would be a claim of flat revenue rather than an absence.
#: Implied price per share, `public_float / shares_outstanding`, outside
#: which a filing is treated as mis-tagged rather than as a very large or
#: very small company.
#:
#: Rejecting on a quantity that MEANS something, rather than on the float
#: value itself, is what makes the filter explicable: "this filing implies a
#: share price of ninety-seven thousand dollars" is a diagnosis, "this number
#: is too big" is a guess.
#:
#: The ceiling was $1,000,000 on the first attempt, chosen so Berkshire's A
#: shares near $700k would survive. Measured against the live SEC, that was
#: an order of magnitude too loose to catch anything: the scale errors imply
#: prices of $97k (ONTO), $116k (MGRC) and $143k (OLED), all comfortably
#: under a million, and they ranked ABOVE Nvidia -- whose own filing is
#: correct at an implied $163. A filter that admits every error it was
#: written to reject is worse than none, because it looks like diligence.
#:
#: $10,000 is the working ceiling. It clears the genuinely high-priced US
#: listings -- NVR near $7k, Booking near $5k -- and rejects every scale
#: error seen. It also rejects Berkshire's A shares, and that is the
#: deliberate trade: one real company excluded, against several hundred
#: mis-tagged filings admitted. B shares are unaffected.
PLAUSIBLE_IMPLIED_PRICE = (0.5, 10_000.0)

_REVENUE_TAGS = (
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax", "USD"),
    ("us-gaap", "Revenues", "USD"),
    ("us-gaap", "SalesRevenueNet", "USD"),
)

# The SEC asks for a declaring User-Agent and no more than ten requests a
# second. Both are conditions of access, not politeness: the rate limiter is
# built in and the User-Agent is a required argument with no default, because
# a library that shipped a fake one would get every one of its users blocked
# under the same string.
_MAX_REQUESTS_PER_SECOND = 8.0


class FetchError(RuntimeError):
    """A request to EDGAR failed, or returned something unusable."""


def default_transport(user_agent: str, *, timeout: float = 30.0, clock=None,
                      sleep=None):
    """A rate-limited urllib transport. Standard library only.

    Injectable, and the injection point is the whole design: :func:`fetch` is
    pure given a transport, so the derivation is tested against recorded
    responses with no socket in the test suite. That matters more here than it
    usually would -- the one part of this library that cannot be
    deterministic is the part that talks to the network, so the boundary is
    drawn tightly around it.
    """
    import time
    import urllib.error
    import urllib.request

    now = clock or time.monotonic
    wait = sleep or time.sleep
    interval = 1.0 / _MAX_REQUESTS_PER_SECOND
    last = [now() - interval]

    def get(url: str) -> bytes:
        gap = now() - last[0]
        if gap < interval:
            wait(interval - gap)
        last[0] = now()
        request = urllib.request.Request(url, headers={
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    import gzip
                    raw = gzip.decompress(raw)
                return raw
        except urllib.error.HTTPError as exc:
            raise FetchError(f"HTTP {exc.code} {exc.reason} for {url}") from exc
        except urllib.error.URLError as exc:
            raise FetchError(f"could not reach {url}: {exc.reason}") from exc

    return get


class _Missing(Exception):
    """A 404 from the frames API: nobody reported that concept that period."""


def _get_json(transport, url: str):
    try:
        raw = transport(url)
    except FetchError as exc:
        if "HTTP 404" in str(exc):
            raise _Missing(url) from exc
        raise
    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise FetchError(f"{url} did not return JSON") from exc


def _frame(transport, taxonomy: str, tag: str, unit: str, period: str):
    """One frame as {cik: value}. A missing frame is empty, not fatal."""
    url = f"{_FRAMES}/{taxonomy}/{tag}/{unit}/{period}.json"
    try:
        payload = _get_json(transport, url)
    except _Missing:
        return {}
    out: dict[int, float] = {}
    for point in payload.get("data", ()):
        cik, val = point.get("cik"), point.get("val")
        if cik is None or val is None:
            continue
        # Frames are one point per filer per period, but a duplicate would
        # otherwise resolve by whichever the JSON listed last -- an ordering
        # dependency. Stated rather than incidental.
        out[int(cik)] = float(val)
    return out


def _fiscal_year_for(as_of: str, fiscal_year: int | None) -> int:
    """Which annual frame to ask for, given a date.

    Annual reports for calendar year Y are filed between February and April of
    Y+1, so before April the CY(Y-1) frame holds a fraction of the market --
    and not a random fraction. Early filers are large, well-resourced and
    clean-audit; a universe built from them is biased in a way that looks like
    data rather than like a sampling artifact. Hence the shift back a year.
    """
    parts = as_of.split("-")
    try:
        if len(parts) != 3:
            raise ValueError(as_of)
        year, month = int(parts[0]), int(parts[1])
        if not 1 <= month <= 12:
            raise ValueError(as_of)
    except ValueError as exc:
        raise ValidationError(f"as_of must be YYYY-MM-DD, got {as_of!r}") from exc
    if fiscal_year is None:
        return year - 1 if month >= 4 else year - 2
    return int(fiscal_year)


def fetch(
    *,
    as_of: str,
    user_agent: str,
    limit: int = 100,
    fiscal_year: int | None = None,
    transport=None,
    progress=None,
    exclude_negative_equity: bool = True,
    rank_by: str = "equity",
) -> Snapshot:
    """Build a :class:`Snapshot` from SEC filings.

    # Which companies you get, and why it matters

    ``rank_by`` decides which ``limit`` companies are taken, and the choice
    changes the roster's SHAPE, not just its membership.

    ``"equity"`` (the default, and what this function has always done) ranks
    by shareholders' equity. That is a book quantity, and book equity bears a
    wildly different relation to size across sectors: a bank carries enormous
    equity against its market value while a software company carries almost
    none. Measured on the live SEC for CY2025, the top 150 by equity came
    back **27% financial services and 17% technology**, with five banks in
    the top ten -- against roughly 13% and 30% for the S&P 500. So the
    default roster is bank-heavy by construction, and any realism measured on
    it inherits that.

    ``"public_float"`` ranks by ``dei:EntityPublicFloat`` instead -- the
    aggregate market value of stock held by non-affiliates, filed on the 10-K
    cover page. It is the one market-derived number in EDGAR, and it produces
    a roster whose composition resembles a real index.

    Its two costs are real and are not hidden. It is **stale**: as-of the
    last business day of the most recently completed second fiscal quarter,
    so six to eighteen months old depending on the filer. And it is
    **float, not capitalisation** -- it excludes affiliate and insider
    holdings, which understates founder-controlled companies specifically.

    It is also visibly mis-tagged in places: XBRL scale errors put several
    filers above any real company's market value. Rather than a magic
    threshold, the implausible ones are rejected by a quantity that means
    something -- the implied price per share, ``public_float / shares``,
    which must land in ``PLAUSIBLE_IMPLIED_PRICE``. A filer whose filing
    implies a share price of eight million dollars has a units error, and
    saying so in those terms beats saying "too big".

    Neither ranking is a market-cap ranking, because EDGAR has no prices.
    For that, set ``initial_price`` yourself from a market data source.

    ``user_agent`` is required and must identify you -- the SEC's fair-access
    policy asks for a name and a contact address, e.g.
    ``"Jane Roe jane@example.org"``. Requests without one are refused at the
    edge, and this function will not invent one on your behalf.

    Companies are ranked by shareholders' equity and the largest ``limit``
    that map to a sector are kept. Equity is a mediocre size proxy -- it
    understates asset-light companies badly -- and it is used anyway because
    the alternative is market capitalisation, which EDGAR does not carry.
    Said plainly rather than discovered later: **the ranking is by book size,
    not market size**, so the universe skews towards balance-sheet-heavy
    names. Take a larger ``limit`` and filter yourself if that matters.

    Roughly ``5 + limit`` requests, rate limited to eight a second.

    The result is a frozen artifact. Save it, hash it, cite it -- and do not
    expect a re-fetch to reproduce it. EDGAR is not append-only: companies
    amend and restate, so the same query returns different numbers next year.
    That is exactly why the snapshot, not the query, is the input to
    everything downstream.
    """
    if not user_agent or "@" not in user_agent:
        raise ValidationError(
            'user_agent must identify you with a contact address, e.g. '
            '"Jane Roe jane@example.org". The SEC requires it and refuses '
            'requests without one. pretium will not send a fabricated '
            'User-Agent on your behalf.'
        )
    if limit < 1:
        raise ValidationError(f"limit must be >= 1, got {limit}")

    fy = _fiscal_year_for(as_of, fiscal_year)
    duration, instant = f"CY{fy}", f"CY{fy}Q4I"
    get = transport if transport is not None else default_transport(user_agent)
    say = progress if progress is not None else (lambda _message: None)

    say(f"fundamentals for {duration}")
    eps = _frame(get, "us-gaap", "EarningsPerShareDiluted", "USD-per-shares",
                 duration)
    equity = _frame(get, "us-gaap", "StockholdersEquity", "USD", instant)
    # BOTH share tags, merged, not one-or-the-other.
    #
    # Measured against the live SEC for CY2023: `dei` covers 2,717 filers and
    # `us-gaap` covers 4,971, overlapping partially. Of the 5,716 filers with
    # diluted EPS, the dei tag alone reaches 1,966 -- the union reaches 4,733.
    # Taking dei and only falling back when it came back EMPTY dropped more
    # than half the usable universe, and it did so invisibly: the result was a
    # perfectly good smaller universe with no indication that most of the
    # market had been filtered out for want of a share count.
    #
    # dei wins a tie because it is the cover-page figure -- as-of the filing
    # date rather than the period end, so it is the more current of the two.
    shares = _frame(get, "us-gaap", "CommonStockSharesOutstanding", "shares",
                    instant)
    shares.update(_frame(get, "dei", "EntityCommonStockSharesOutstanding",
                         "shares", instant))

    revenue_now: dict[int, float] = {}
    revenue_prior: dict[int, float] = {}
    for taxonomy, tag, unit in _REVENUE_TAGS:
        for cik, value in _frame(get, taxonomy, tag, unit, duration).items():
            revenue_now.setdefault(cik, value)
        for cik, value in _frame(get, taxonomy, tag, unit, f"CY{fy - 1}").items():
            revenue_prior.setdefault(cik, value)

    if not eps or not shares:
        raise FetchError(
            f"no usable {duration} data came back. Either the fiscal year is "
            "too recent to have been filed, or the requests were refused -- "
            "check the User-Agent."
        )

    if rank_by not in ("equity", "public_float"):
        raise ValidationError(
            f'rank_by must be "equity" or "public_float", got {rank_by!r}'
        )

    usable = [cik for cik in eps if cik in shares and shares[cik] > 0]

    if rank_by == "public_float":
        # Filed as-of the fiscal SECOND quarter, not the fourth: the cover
        # page reports it at the last business day of the most recently
        # completed Q2. Asking for the Q4 instant returns almost nothing,
        # which would silently degrade to an empty ranking.
        say(f"public float for CY{fy}Q2I")
        floats = _frame(get, "dei", "EntityPublicFloat", "USD", f"CY{fy}Q2I")
        lo, hi = PLAUSIBLE_IMPLIED_PRICE
        ranked, rejected = {}, 0
        for cik in usable:
            value = floats.get(cik)
            if value is None or value <= 0:
                continue
            implied = value / shares[cik]
            if lo <= implied <= hi:
                ranked[cik] = value
            else:
                rejected += 1
        say(f"{len(ranked)} filers have a plausible public float "
            f"({rejected} rejected on implied price)")
        if not ranked:
            raise FetchError(
                f"no filer had a usable dei:EntityPublicFloat for CY{fy}Q2I. "
                'Fall back to rank_by="equity", which needs no market data.'
            )
        # Filers without a float sort last, keeping the roster fillable while
        # the ranked ones lead. Ranked value descending, CIK ascending.
        candidates = sorted(
            usable, key=lambda cik: (-ranked.get(cik, 0.0), cik)
        )
    else:
        # Equity descending, CIK ascending. The tie-break is not decoration:
        # without it, two filers with identical equity would order by whichever
        # the response happened to list first, and the universe would depend on a
        # detail of the JSON rather than on the data.
        candidates = sorted(usable, key=lambda cik: (-equity.get(cik, 0.0), cik))
    say(f"{len(candidates)} filers have EPS and a share count")

    rows: list[dict] = []
    excluded: list[dict] = []
    seen: set[str] = set()

    for cik in candidates:
        if len(rows) >= limit:
            break
        try:
            meta = _get_json(get, _SUBMISSIONS.format(cik=cik))
        except (_Missing, FetchError):
            excluded.append({"cik": cik, "reason": "no submissions record"})
            continue

        tickers = meta.get("tickers") or []
        name = meta.get("name", "")
        if not tickers:
            # No ticker means no listed equity: a bond-only filer, or a
            # subsidiary filing because of a debt covenant.
            excluded.append({"cik": cik, "name": name, "reason": "not listed"})
            continue
        ticker = str(tickers[0]).upper()
        if ticker in seen:
            excluded.append({"cik": cik, "name": name, "ticker": ticker,
                             "reason": "duplicate ticker"})
            continue

        sector = sector_for_sic(meta.get("sic"))
        if sector is None:
            excluded.append({"cik": cik, "name": name, "ticker": ticker,
                             "sic": meta.get("sic"),
                             "reason": "SIC maps to no sector"})
            continue

        count = shares[cik]
        book = equity.get(cik)
        row = {
            "ticker": ticker,
            "sector": sector,
            "cik": cik,
            "name": name,
            "sic": str(meta.get("sic", "")),
            "eps": eps[cik],
            "shares_outstanding": count,
            "book_value_per_share": (book / count) if book is not None else None,
        }
        prior, current = revenue_prior.get(cik), revenue_now.get(cik)
        if prior is not None and current is not None and prior > 0:
            row["revenue_growth"] = current / prior - 1.0
        seen.add(ticker)
        rows.append(row)
        say(f"{len(rows)}/{limit} {ticker}")

    keep, dropped = filter_rows(
        rows, exclude_negative_equity=exclude_negative_equity)
    return Snapshot(
        as_of=as_of,
        rows=keep,
        excluded=excluded + dropped,
        notes={
            "fiscal_year": fy,
            "duration_frame": duration,
            "instant_frame": instant,
            # Reports what the ranking ACTUALLY was. This read
            # "stockholders_equity" unconditionally when `rank_by` landed,
            # so a float-ranked snapshot carried a note saying it was
            # equity-ranked -- the identical bug class as `model_preset()`'s
            # hardcoded "pt-v1" default, fixed hours earlier in this same
            # session, reintroduced by the person who fixed it. A provenance
            # field that does not follow the thing it describes is worse than
            # no field: it is a confident wrong answer.
            "ranked_by": (
                "public_float" if rank_by == "public_float"
                else "stockholders_equity"
            ),
            "candidates": len(candidates),
            "requested": limit,
        },
    )


# ---------------------------------------------------------------------------
# SIC -> sector
# ---------------------------------------------------------------------------

# The SEC classifies filers by SIC, a 1987 scheme with no "software" division
# and no "semiconductors" division, so the mapping below is a JUDGEMENT and is
# written out rather than hidden in a dict comprehension. Two consequences a
# user should know before citing a universe built this way:
#
#   - It is coarse. SIC 7372 is prepackaged software, which covers both an
#     operating-system vendor and a two-person game studio; both land in
#     `technology` and get the same anchor P/E and base variance.
#   - It is stale by construction. A company whose business changed since it
#     registered keeps its original code, and the SEC does not backfill.
#
# Overrides are checked before ranges, so a four-digit code can escape its
# major group -- pharmaceuticals out of chemicals, computers out of machinery,
# REITs out of finance. That is where most of the accuracy lives.

_SIC_OVERRIDES: dict[int, str] = {}

# Codes that map to no sector rather than to a wrong one. 6770 is blank
# checks -- SPACs and shells, which have a share count and no business -- and
# 9995/9999 are the SEC's own "nonclassifiable" bucket.
_SIC_EXCLUDE = frozenset({0, 6770, 9995, 9999})


def _override(codes, sector: str) -> None:
    for code in codes:
        _SIC_OVERRIDES[code] = sector


# Drugs and biologicals sit inside the chemicals major group (28).
_override(range(2833, 2837), "healthcare")
# Computers, storage and peripherals inside industrial machinery (35).
_override(range(3570, 3580), "technology")
# Semiconductors and related devices inside electronic equipment (36).
_override([3672, 3674, 3675, 3676, 3677, 3678, 3679], "technology")
# Telephone and broadcast equipment: the maker is technology; the CARRIER,
# further down in major group 48, is telecommunications.
_override([3661, 3663, 3669], "technology")
# Motor vehicles inside transportation equipment (37) -- consumer, not
# industrial, because demand is discretionary household spending.
_override(range(3711, 3717), "consumer_discretionary")
# Medical instruments inside instruments (38).
_override(range(3841, 3852), "healthcare")
# Pipelines inside transportation (46) belong with energy: the economics are
# throughput of hydrocarbons, not a logistics network.
_override([4610, 4612, 4613, 4619], "energy")
# Broadcasting and cable inside communications (48). Media revenue is
# advertising and subscriptions, which behave like consumer spending, not like
# the regulated-utility economics of a telephone carrier.
_override([4832, 4833, 4841], "consumer_discretionary")
# Drugs wholesale inside wholesale nondurable (51).
_override([5122], "healthcare")
# Food stores and drug stores inside retail (52-59): staples, not
# discretionary. Grocery demand does not cycle.
_override([5411, 5412, 5912], "consumer_staples")
# Real estate and REITs inside the finance division (60-67).
_override(list(range(6500, 6600)) + [6798], "real_estate")
# Prepackaged software, data processing and computer services inside business
# services (73) -- this is where most of the modern technology sector lives,
# under a code written before the industry existed.
_override(range(7370, 7380), "technology")
# Commercial physical and biological research inside engineering services (87):
# contract research organisations and pre-revenue biotech.
_override([8731], "healthcare")

# Major-group ranges, checked only when no override matched. Inclusive on both
# ends, ordered as SIC orders them.
_SIC_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "consumer_staples"),        # agricultural production
    (1000, 1099, "materials"),             # metal mining
    (1200, 1299, "energy"),                # coal
    (1300, 1399, "energy"),                # oil and gas extraction
    (1400, 1499, "materials"),             # nonmetallic minerals
    (1500, 1799, "industrials"),           # construction
    (2000, 2199, "consumer_staples"),      # food and tobacco
    (2200, 2399, "consumer_discretionary"),  # textiles and apparel
    (2400, 2499, "materials"),             # lumber and wood
    (2500, 2599, "consumer_discretionary"),  # furniture
    (2600, 2699, "materials"),             # paper
    (2700, 2799, "consumer_discretionary"),  # printing and publishing
    (2800, 2899, "materials"),             # chemicals
    (2900, 2999, "energy"),                # petroleum refining
    (3000, 3099, "materials"),             # rubber and plastics
    (3100, 3199, "consumer_discretionary"),  # leather
    (3200, 3299, "materials"),             # stone, clay and glass
    (3300, 3399, "materials"),             # primary metal
    (3400, 3499, "industrials"),           # fabricated metal
    (3500, 3599, "industrials"),           # industrial machinery
    (3600, 3699, "technology"),            # electronic equipment
    (3700, 3799, "industrials"),           # transportation equipment
    (3800, 3899, "technology"),            # instruments
    (3900, 3999, "consumer_discretionary"),  # miscellaneous manufacturing
    (4000, 4099, "transportation"),        # railroads
    (4100, 4299, "transportation"),        # transit and trucking
    (4400, 4599, "transportation"),        # water and air
    (4600, 4699, "transportation"),        # pipelines (overridden to energy)
    (4700, 4799, "transportation"),        # transportation services
    (4800, 4899, "telecommunications"),    # communications
    (4900, 4999, "utilities"),             # electric, gas, sanitary
    (5000, 5099, "industrials"),           # wholesale durable goods
    (5100, 5199, "consumer_staples"),      # wholesale nondurable goods
    (5200, 5999, "consumer_discretionary"),  # retail
    (6000, 6499, "financial_services"),    # depository, credit, insurance
    (6600, 6799, "financial_services"),    # investment offices, holding
    (7000, 7299, "consumer_discretionary"),  # hotels and personal services
    (7300, 7399, "industrials"),           # business services
    (7500, 7999, "consumer_discretionary"),  # auto services, entertainment
    (8000, 8099, "healthcare"),            # health services
    (8100, 8299, "consumer_discretionary"),  # legal and educational
    (8300, 8399, "healthcare"),            # social services
    (8700, 8799, "industrials"),           # engineering and accounting
)


def sector_for_sic(sic) -> str | None:
    """Map an SEC SIC code to one of the model's twelve sectors.

    Returns ``None`` for a code with no sensible home -- 6770 blank checks,
    9995 nonclassifiable, an empty string on a filer that never got one. A
    guess would be worse than an exclusion: it would put a shell company in a
    sector and give it that sector's volatility and anchor P/E.
    """
    if sic is None or sic == "":
        return None
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return None
    if code in _SIC_EXCLUDE:
        return None
    if code in _SIC_OVERRIDES:
        return _SIC_OVERRIDES[code]
    for low, high, sector in _SIC_RANGES:
        if low <= code <= high:
            return sector
    return None
