"""Real fundamentals as initial conditions.

Everything here is offline. The network fetch and the universe construction
are separate operations precisely because they have different determinism
properties, and the pure half is the half worth testing hard.
"""

import json
import struct

import pytest

import tradefloor
from tradefloor.edgar import LOADER_VERSION, Snapshot, filter_rows, to_instruments

ROWS = [
    dict(ticker="ALPHA", sector="technology", eps=6.10,
         book_value_per_share=18.0, revenue_growth=0.19, shares_outstanding=1.5e10),
    dict(ticker="BETA", sector="energy", eps=8.40,
         book_value_per_share=52.0, revenue_growth=0.02, shares_outstanding=4.2e9),
    dict(ticker="GAMMA", sector="utilities", eps=3.05,
         book_value_per_share=41.0, revenue_growth=0.01, shares_outstanding=8.0e8),
    dict(ticker="DELTA", sector="healthcare", eps=-1.20,
         book_value_per_share=12.0, revenue_growth=0.31, shares_outstanding=2.4e8),
]
ZOMBIE = dict(ticker="ZOMBIE", sector="industrials", eps=-4.0,
              book_value_per_share=-9.0, revenue_growth=-0.2, shares_outstanding=1.0e8)

MACRO = dict(federal_funds_rate=0.03, corporate_bond_yield=0.055)


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def snapshot(rows=None, excluded=()):
    return Snapshot(as_of="2024-06-30", rows=rows if rows is not None else ROWS,
                    excluded=excluded)


# --------------------------------------------------------------------------
# The snapshot is the artifact
# --------------------------------------------------------------------------

def test_the_hash_identifies_content_not_key_order():
    """A hash that moved when a dict order changed would be useless as an id.

    The snapshot is what a citable specification names, alongside the seed and
    the preset, so it has to be a property of the content.
    """
    a = snapshot()
    b = Snapshot(as_of="2024-06-30", rows=[dict(reversed(list(r.items()))) for r in ROWS])
    assert a.hash == b.hash


def test_different_content_gives_a_different_hash():
    changed = [dict(ROWS[0], eps=6.11)] + ROWS[1:]
    assert snapshot(changed).hash != snapshot().hash


def test_a_snapshot_round_trips_through_disk(tmp_path):
    original = snapshot(excluded=[dict(ZOMBIE, reason="test")])
    path = tmp_path / "snap.json"
    written = original.save(str(path))
    reloaded = Snapshot.load(str(path))
    assert reloaded.hash == original.hash == written
    assert len(reloaded) == len(original)
    assert reloaded.excluded == original.excluded


def test_a_reloaded_snapshot_keeps_the_loader_version_that_built_it():
    # A snapshot built by loader 1 and read by loader 2 is still a loader-1
    # artifact. Relabelling it would erase the provenance that makes it
    # citable.
    payload = snapshot().to_dict()
    payload["loader_version"] = 0
    assert Snapshot.from_dict(payload).loader_version == 0
    assert snapshot().loader_version == LOADER_VERSION


def test_a_newer_schema_is_refused():
    payload = snapshot().to_dict()
    payload["schema"] = 99
    with pytest.raises(tradefloor.ValidationError, match="newer"):
        Snapshot.from_dict(payload)


# --------------------------------------------------------------------------
# Exclusions
# --------------------------------------------------------------------------

def test_negative_equity_loss_makers_are_excluded_with_a_reason():
    """Such a name has no fundamental anchor at all.

    Negative EPS takes the book-value path, and negative book value there
    produces a negative fair value the floor then clamps -- so it would trade
    at the floor forever. That is not a stock, it is a constant.
    """
    keep, dropped = filter_rows(ROWS + [ZOMBIE])
    assert [r["ticker"] for r in keep] == [r["ticker"] for r in ROWS]
    assert len(dropped) == 1
    assert dropped[0]["ticker"] == "ZOMBIE"
    assert "negative" in dropped[0]["reason"]


def test_a_loss_maker_with_positive_equity_is_kept():
    # Loss-makers are a real and load-bearing part of a cross-section, and
    # they exercise the book-value path. Excluding them all would be a
    # different market.
    keep, _ = filter_rows(ROWS)
    assert "DELTA" in [r["ticker"] for r in keep]


def test_exclusions_are_returned_not_silently_dropped():
    # A loader that quietly discarded a tenth of the market would produce a
    # universe whose composition nobody chose -- and the exclusions are often
    # the interesting part.
    keep, dropped = filter_rows(ROWS + [ZOMBIE, dict(ticker="NOEPS", sector="materials",
                                                     shares_outstanding=1e8)])
    assert len(keep) + len(dropped) == len(ROWS) + 2
    assert {d["reason"] for d in dropped} == {
        "loss-maker with negative or missing equity", "no eps"}


# --------------------------------------------------------------------------
# Derived fields
# --------------------------------------------------------------------------

def test_prices_start_at_fair_value():
    """EDGAR has no market data and the loader invents none.

    Every company starts at its own computed fair value, so initial mispricing
    is zero. The honest cost, which the docstring states: a universe that
    starts perfectly priced has no mispricing dispersion, so a strategy that
    harvests mispricing sees nothing until shocks accumulate.
    """
    instruments = to_instruments(snapshot(), **MACRO)
    for inst, row in zip(instruments, ROWS):
        expected = tradefloor.fair_value(
            eps=row["eps"], sector=row["sector"],
            revenue_growth=row["revenue_growth"],
            book_value_per_share=row["book_value_per_share"], **MACRO)
        assert inst.initial_price == expected.fair_value


def test_a_loss_maker_is_priced_off_book():
    instruments = {i.ticker: i for i in to_instruments(snapshot(), **MACRO)}
    # 12.0 book x the loss-making multiple.
    assert instruments["DELTA"].initial_price == pytest.approx(12.0 * 1.2)


def test_beta_carries_sector_structure_without_an_rng_draw():
    # A flat 1.0 would erase the structure the spread model wants. Drawing a
    # random beta would make building a universe perturb the market it is
    # built for.
    instruments = {i.ticker: i for i in to_instruments(snapshot(), **MACRO)}
    assert instruments["GAMMA"].beta < instruments["BETA"].beta, "utilities below energy"
    assert to_instruments(snapshot(), **MACRO)[0].beta == instruments["ALPHA"].beta


def test_avg_volume_is_materialised_rather_than_left_to_a_fallback():
    # A citable snapshot should contain the numbers actually used, not a hole
    # the engine fills invisibly through its falsy chain.
    instruments = to_instruments(snapshot(), **MACRO)
    for inst, row in zip(instruments, ROWS):
        assert inst.avg_volume == pytest.approx(row["shares_outstanding"] * 0.005)


def test_an_explicit_field_overrides_the_derivation():
    rows = [dict(ROWS[0], beta=1.75, avg_volume=1234.0)]
    inst = to_instruments(Snapshot(as_of="x", rows=rows), **MACRO)[0]
    assert inst.beta == 1.75
    assert inst.avg_volume == 1234.0


def test_a_missing_required_field_is_named():
    rows = [dict(ticker="X", sector="technology")]
    with pytest.raises(tradefloor.ValidationError, match="shares_outstanding"):
        to_instruments(Snapshot(as_of="x", rows=rows), **MACRO)


# --------------------------------------------------------------------------
# Using it
# --------------------------------------------------------------------------

def test_a_loaded_universe_drives_the_engine():
    u = tradefloor.Universe.from_edgar(snapshot(), **MACRO)
    engine = tradefloor.Engine(seed=1, universe=u, macro_state=tradefloor.Macro(**MACRO))
    engine.open_market()
    engine.run_session(9, 30, 3, 60)
    assert all(p > 0 for p in arr(engine.prices()))
    assert engine.tickers == [r["ticker"] for r in ROWS]


def test_matching_the_macro_matters():
    """A universe priced under one regime and run under another starts
    mispriced by the difference -- quietly, since both look reasonable.

    Measured: matching macro leaves |s| around 1e-3 after one tick, which is
    the tick's own noise. A mismatched rate regime leaves it around 1e-1, two
    orders of magnitude larger.
    """
    u = tradefloor.Universe.from_edgar(snapshot(), **MACRO)

    matched = tradefloor.Engine(seed=1, universe=u, macro_state=tradefloor.Macro(**MACRO))
    matched.open_market()
    matched.tick(9, 30, 3)

    mismatched = tradefloor.Engine(seed=1, universe=u, macro_state=tradefloor.Macro(
        federal_funds_rate=0.08, corporate_bond_yield=0.10))
    mismatched.open_market()
    mismatched.tick(9, 30, 3)

    near = max(abs(x) for x in arr(matched.column("mispricing_s")))
    far = max(abs(x) for x in arr(mismatched.column("mispricing_s")))
    assert near < 0.01
    assert far > 10 * near


def test_loading_is_reproducible():
    a = tradefloor.Universe.from_edgar(snapshot(), **MACRO)
    b = tradefloor.Universe.from_edgar(snapshot(), **MACRO)
    assert [i.initial_price for i in a] == [i.initial_price for i in b]
    assert [i.beta for i in a] == [i.beta for i in b]


def test_from_edgar_accepts_a_path(tmp_path):
    path = tmp_path / "snap.json"
    snapshot().save(str(path))
    u = tradefloor.Universe.from_edgar(str(path), **MACRO)
    assert u.tickers() == [r["ticker"] for r in ROWS]


# --------------------------------------------------------------------------
# The unbuilt half
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# The network half, exercised without a network
# --------------------------------------------------------------------------
#
# fetch() is pure given a transport, and that is the point of taking one. The
# fetch itself can never be deterministic -- EDGAR restates -- so the boundary
# is drawn tightly around the socket and everything on this side of it is
# tested exactly like the rest of the library.

import json as _json

from tradefloor import ValidationError
from tradefloor.edgar import FetchError, fetch, sector_for_sic

UA = "Test Runner test@example.org"


def frame_url(tag, unit, period, taxonomy="us-gaap"):
    return f"https://data.sec.gov/api/xbrl/frames/{taxonomy}/{tag}/{unit}/{period}.json"


def frame(values):
    return {"data": [{"cik": cik, "val": val} for cik, val in values.items()]}


class FakeEdgar:
    """Recorded EDGAR responses. Records every URL asked for."""

    def __init__(self, *, eps, equity, shares, submissions, revenue=None,
                 revenue_prior=None, missing=(), fy=2023):
        self.calls = []
        self.missing = set(missing)
        self.routes = {
            frame_url("EarningsPerShareDiluted", "USD-per-shares", f"CY{fy}"): frame(eps),
            frame_url("StockholdersEquity", "USD", f"CY{fy}Q4I"): frame(equity),
            frame_url("EntityCommonStockSharesOutstanding", "shares",
                      f"CY{fy}Q4I", taxonomy="dei"): frame(shares),
        }
        if revenue:
            self.routes[frame_url(
                "RevenueFromContractWithCustomerExcludingAssessedTax", "USD",
                f"CY{fy}")] = frame(revenue)
        if revenue_prior:
            self.routes[frame_url(
                "RevenueFromContractWithCustomerExcludingAssessedTax", "USD",
                f"CY{fy - 1}")] = frame(revenue_prior)
        for cik, meta in submissions.items():
            self.routes[f"https://data.sec.gov/submissions/CIK{cik:010d}.json"] = meta

    def __call__(self, url):
        self.calls.append(url)
        if url in self.missing:
            raise FetchError(f"HTTP 404 Not Found for {url}")
        if url not in self.routes:
            # A frame nobody reported is a 404 from EDGAR, not an error. The
            # fake reproduces that rather than inventing an empty body, so the
            # code under test meets the shape it will meet in production.
            raise FetchError(f"HTTP 404 Not Found for {url}")
        return _json.dumps(self.routes[url]).encode()


def submission(name, ticker, sic):
    return {"name": name, "tickers": [ticker] if ticker else [], "sic": sic}


def small_market():
    return FakeEdgar(
        eps={320193: 6.13, 789019: 11.06, 1045810: 1.19, 66740: 7.09},
        equity={320193: 62e9, 789019: 206e9, 1045810: 42e9, 66740: 47e9},
        shares={320193: 15.5e9, 789019: 7.4e9, 1045810: 24.6e9, 66740: 1.2e9},
        revenue={320193: 383e9, 789019: 211e9, 1045810: 60e9, 66740: 32e9},
        revenue_prior={320193: 394e9, 789019: 198e9, 1045810: 26e9},
        submissions={
            320193: submission("Apple Inc.", "AAPL", "3571"),
            789019: submission("Microsoft Corp", "MSFT", "7372"),
            1045810: submission("NVIDIA Corp", "NVDA", "3674"),
            66740: submission("3M Co", "MMM", "3841"),
        },
    )


def test_a_fetch_produces_a_usable_snapshot():
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=small_market())
    assert [r["ticker"] for r in snap.rows] == ["MSFT", "AAPL", "MMM", "NVDA"]
    assert snap.notes["fiscal_year"] == 2023
    assert snap.notes["ranked_by"] == "stockholders_equity"


def test_companies_are_ranked_by_equity_not_by_response_order():
    # The response lists Apple first; Microsoft has more equity and must come
    # first regardless. Ordering is contractual here -- roster order decides
    # the RNG draw order, so a universe that reordered would be a different
    # market.
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=small_market())
    assert snap.rows[0]["ticker"] == "MSFT"


def test_ties_break_by_cik_rather_than_by_iteration_order():
    tied = FakeEdgar(
        eps={100: 1.0, 200: 1.0, 300: 1.0},
        equity={100: 5e9, 200: 5e9, 300: 5e9},
        shares={100: 1e9, 200: 1e9, 300: 1e9},
        submissions={c: submission(f"C{c}", f"T{c}", "7372")
                     for c in (100, 200, 300)},
    )
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=tied)
    assert [r["cik"] for r in snap.rows] == [100, 200, 300]


def test_the_derivation_is_deterministic_even_though_the_fetch_is_not():
    a = fetch(as_of="2024-06-30", user_agent=UA, transport=small_market())
    b = fetch(as_of="2024-06-30", user_agent=UA, transport=small_market())
    assert a.hash == b.hash


def test_book_value_per_share_is_derived_from_equity_and_share_count():
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=small_market())
    apple = next(r for r in snap.rows if r["ticker"] == "AAPL")
    assert apple["book_value_per_share"] == pytest.approx(62e9 / 15.5e9)


def test_revenue_growth_is_computed_only_when_both_years_exist():
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=small_market())
    apple = next(r for r in snap.rows if r["ticker"] == "AAPL")
    assert apple["revenue_growth"] == pytest.approx(383 / 394 - 1.0)
    # 3M has a current-year revenue but no prior year in the fake. An absent
    # growth figure is right; a zero would be a claim that revenue was flat.
    mmm = next(r for r in snap.rows if r["ticker"] == "MMM")
    assert "revenue_growth" not in mmm


def test_a_filer_reporting_only_the_older_revenue_tag_still_gets_growth():
    # ASC 606 filers use RevenueFromContractWithCustomer...; financials and
    # older filers use Revenues. The fallback chain is the point.
    fake = small_market()
    fake.routes[frame_url("Revenues", "USD", "CY2023")] = frame({66740: 32e9})
    fake.routes[frame_url("Revenues", "USD", "CY2022")] = frame({66740: 34e9})
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=fake)
    mmm = next(r for r in snap.rows if r["ticker"] == "MMM")
    assert mmm["revenue_growth"] == pytest.approx(32 / 34 - 1.0)


def test_the_newer_tag_wins_when_a_filer_reports_both():
    fake = small_market()
    fake.routes[frame_url("Revenues", "USD", "CY2023")] = frame({320193: 1.0})
    fake.routes[frame_url("Revenues", "USD", "CY2022")] = frame({320193: 2.0})
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=fake)
    apple = next(r for r in snap.rows if r["ticker"] == "AAPL")
    assert apple["revenue_growth"] == pytest.approx(383 / 394 - 1.0)


def test_a_missing_frame_is_empty_rather_than_fatal():
    # Nobody reported a concept that period -> 404. That is normal and must
    # not take the whole fetch down.
    fake = small_market()
    fake.missing.add(frame_url(
        "RevenueFromContractWithCustomerExcludingAssessedTax", "USD", "CY2023"))
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=fake)
    assert len(snap.rows) == 4
    assert all("revenue_growth" not in r for r in snap.rows)


def test_a_filer_with_no_ticker_is_excluded_with_a_reason():
    fake = small_market()
    fake.routes["https://data.sec.gov/submissions/CIK0000789019.json"] = \
        submission("Bond Only Corp", None, "7372")
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=fake)
    assert "MSFT" not in [r["ticker"] for r in snap.rows]
    assert any(e["reason"] == "not listed" for e in snap.excluded)


def test_an_unmappable_sic_is_excluded_rather_than_guessed():
    # A blank-check shell has a share count and no business. Putting it in a
    # sector would give it that sector's volatility and anchor P/E.
    fake = small_market()
    fake.routes["https://data.sec.gov/submissions/CIK0001045810.json"] = \
        submission("Shell Acquisition Corp", "SPAC", "6770")
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=fake)
    assert "SPAC" not in [r["ticker"] for r in snap.rows]
    assert any(e["reason"] == "SIC maps to no sector" for e in snap.excluded)


def test_a_duplicate_ticker_is_kept_once():
    fake = small_market()
    fake.routes["https://data.sec.gov/submissions/CIK0000066740.json"] = \
        submission("Not Actually Apple", "AAPL", "3841")
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=fake)
    assert [r["ticker"] for r in snap.rows].count("AAPL") == 1
    assert any(e["reason"] == "duplicate ticker" for e in snap.excluded)


def test_the_submissions_endpoint_is_hit_only_until_the_limit_is_reached():
    # The cost model is the reason the frames API is used at all: five
    # market-wide requests plus one per company KEPT. Walking every filer's
    # submissions would be thousands of requests for a hundred-name universe.
    fake = small_market()
    fetch(as_of="2024-06-30", user_agent=UA, limit=2, transport=fake)
    submissions = [u for u in fake.calls if "/submissions/" in u]
    assert len(submissions) == 2


def test_a_user_agent_without_a_contact_is_refused():
    # Not pedantry: the SEC blocks by User-Agent, so one library shipping a
    # default would get every user of it blocked under the same string.
    with pytest.raises(ValidationError, match="contact address"):
        fetch(as_of="2024-06-30", user_agent="pretium", transport=small_market())
    with pytest.raises(ValidationError, match="contact address"):
        fetch(as_of="2024-06-30", user_agent="", transport=small_market())


def test_a_malformed_as_of_is_refused():
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        fetch(as_of="June 2024", user_agent=UA, transport=small_market())
    with pytest.raises(ValidationError, match="YYYY-MM-DD"):
        fetch(as_of="2024-13-01", user_agent=UA, transport=small_market())


def test_an_empty_market_is_an_error_not_an_empty_universe():
    empty = FakeEdgar(eps={}, equity={}, shares={}, submissions={})
    with pytest.raises(FetchError, match="no usable"):
        fetch(as_of="2024-06-30", user_agent=UA, transport=empty)


def test_the_fiscal_year_steps_back_before_annual_filings_land():
    # Before April, CY(Y-1) holds only the early filers -- large, clean-audit
    # companies. That is a selection bias that looks like data.
    january = FakeEdgar(fy=2022, eps={320193: 6.11}, equity={320193: 50e9},
                        shares={320193: 15.8e9},
                        submissions={320193: submission("Apple", "AAPL", "3571")})
    snap = fetch(as_of="2024-01-15", user_agent=UA, transport=january)
    assert snap.notes["fiscal_year"] == 2022


def test_an_explicit_fiscal_year_overrides_the_rule():
    fake = FakeEdgar(fy=2019, eps={320193: 2.97}, equity={320193: 90e9},
                     shares={320193: 17.7e9},
                     submissions={320193: submission("Apple", "AAPL", "3571")})
    snap = fetch(as_of="2024-06-30", user_agent=UA, fiscal_year=2019,
                 transport=fake)
    assert snap.notes["fiscal_year"] == 2019


def test_negative_equity_filers_are_excluded_by_the_same_filter():
    fake = FakeEdgar(
        eps={100: -2.0, 200: 3.0},
        equity={100: -1e9, 200: 5e9},
        shares={100: 1e9, 200: 1e9},
        submissions={100: submission("Underwater Inc", "UND", "7372"),
                     200: submission("Solid Inc", "SOL", "7372")},
    )
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=fake)
    assert [r["ticker"] for r in snap.rows] == ["SOL"]
    assert any("loss-maker" in e["reason"] for e in snap.excluded)


def test_progress_reports_something_per_company():
    seen = []
    fetch(as_of="2024-06-30", user_agent=UA, transport=small_market(),
          progress=seen.append)
    assert any("AAPL" in m for m in seen)


def test_a_fetched_snapshot_drives_an_engine():
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=small_market())
    universe = tradefloor.Universe.from_edgar(snap, federal_funds_rate=0.03)
    engine = tradefloor.Engine(seed=1, universe=universe,
                            macro_state=tradefloor.Macro(federal_funds_rate=0.03))
    engine.run_days(3)
    import struct
    prices = struct.unpack("<%dd" % len(universe), engine.prices())
    assert all(p > 0 for p in prices)


def test_a_fetched_snapshot_round_trips_and_keeps_its_hash(tmp_path):
    snap = fetch(as_of="2024-06-30", user_agent=UA, transport=small_market())
    path = str(tmp_path / "snap.json")
    snap.save(path)
    assert tradefloor.edgar.Snapshot.load(path).hash == snap.hash


def test_the_rate_limiter_actually_waits():
    # Eight a second, under the SEC's ten. Verified by measurement, with an
    # injected clock -- a sleep the test cannot see is a sleep that could be
    # removed by accident.
    from tradefloor.edgar import default_transport

    slept = []
    ticks = [0.0]

    def clock():
        return ticks[0]

    def sleep(seconds):
        slept.append(seconds)
        ticks[0] += seconds

    get = default_transport(UA, clock=clock, sleep=sleep)
    calls = []

    class _Response:
        headers = {}

        def read(self):
            return b"{}"

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    import urllib.request
    original = urllib.request.urlopen
    urllib.request.urlopen = lambda *a, **k: (calls.append(a) or _Response())
    try:
        for _ in range(3):
            get("https://data.sec.gov/x.json")
    finally:
        urllib.request.urlopen = original

    assert len(calls) == 3
    # First call does not wait; the next two each wait a full interval,
    # because the fake clock only advances when the limiter sleeps.
    assert slept == [pytest.approx(0.125), pytest.approx(0.125)]


def test_sic_mapping_puts_modern_industries_under_their_1987_codes():
    # The overrides are where the accuracy lives: SIC has no software
    # division, so software sits under business services and semiconductors
    # under electronic equipment.
    assert sector_for_sic(7372) == "technology"
    assert sector_for_sic(3674) == "technology"
    assert sector_for_sic(2834) == "healthcare"
    assert sector_for_sic(6798) == "real_estate"
    assert sector_for_sic(3711) == "consumer_discretionary"
    assert sector_for_sic(4813) == "telecommunications"
    assert sector_for_sic(4832) == "consumer_discretionary"
    assert sector_for_sic(4610) == "energy"


def test_every_sic_mapping_names_a_real_sector():
    # A typo in the table would put a company in a sector the engine does not
    # know, and the failure would surface as a construction error on some
    # unlucky user's universe rather than here.
    valid = set(tradefloor.sectors())
    for code in range(0, 10000):
        sector = sector_for_sic(code)
        assert sector is None or sector in valid, (code, sector)


def test_sic_codes_outside_the_table_map_to_nothing():
    assert sector_for_sic(9995) is None
    assert sector_for_sic("") is None
    assert sector_for_sic(None) is None
    assert sector_for_sic("not a code") is None


# --------------------------------------------------------------------------
# Stationary initialisation
# --------------------------------------------------------------------------

import math
import statistics


def wide_snapshot(n=120):
    secs = tradefloor.sectors()
    return Snapshot(as_of="2024-06-30", rows=[
        dict(ticker="T%03d" % i, sector=secs[i % 12], eps=2.0 + i * 0.05,
             book_value_per_share=15.0 + i * 0.2, revenue_growth=0.05,
             shares_outstanding=1e9)
        for i in range(n)
    ])


def test_the_stationary_width_is_the_model_s_own_not_a_chosen_number():
    """Derived from the AR(2) parameters, and checked against a simulation.

    The Rust side asserts the analytic variance matches a 400,000-step
    simulation. That check matters because a derived formula is exactly the
    kind of thing that looks right and is off by a factor.
    """
    for sector in tradefloor.sectors():
        daily = tradefloor.sector_daily_sigma(sector)
        width = tradefloor.stationary_sigma(daily)
        assert width is not None
        # The process amplifies its innovations about 7.6x at rest.
        assert width / daily == pytest.approx(7.636, rel=1e-3)


def test_stationary_gives_immediate_cross_sectional_dispersion():
    snap = wide_snapshot()
    zero = to_instruments(snap, initial_s="zero", **MACRO)
    spread = to_instruments(snap, initial_s="stationary", s_seed=11, **MACRO)

    s_values = [math.log(a.initial_price / b.initial_price)
                for a, b in zip(spread, zero)]
    assert statistics.pstdev(s_values) > 0.05
    assert abs(statistics.mean(s_values)) < 0.05, "should be centred on fair value"


def test_zero_start_takes_about_a_half_life_to_catch_up():
    """The limitation this option exists to fix, measured.

    A universe priced at fair value has no mispricing dispersion, so a strategy
    that harvests it sees nothing until shocks accumulate. Measured across 60
    trading days: a zero-start universe climbs from 0.014 to 0.091 while a
    stationary-start one sits near 0.10 the whole time.
    """
    snap = wide_snapshot(40)

    def dispersion_after(mode, days):
        u = tradefloor.Universe(to_instruments(snap, initial_s=mode, s_seed=11, **MACRO))
        e = tradefloor.Engine(seed=5, universe=u, macro_state=tradefloor.Macro(**MACRO))
        e.run_days(days, ticks_per_day=390, record=False)
        return statistics.pstdev(arr(e.column("mispricing_s")))

    assert dispersion_after("stationary", 1) > 4 * dispersion_after("zero", 1)


def test_a_tail_draw_cannot_start_outside_the_model_s_cap():
    # A company beginning outside the range the process can reach would be a
    # state the model cannot produce.
    cap = tradefloor.model_preset()["mispricing_cap"]
    spread = to_instruments(wide_snapshot(300), initial_s="stationary",
                            s_seed=3, **MACRO)
    zero = to_instruments(wide_snapshot(300), initial_s="zero", **MACRO)
    for a, b in zip(spread, zero):
        assert abs(math.log(a.initial_price / b.initial_price)) <= cap


def test_stationary_initialisation_is_reproducible_and_seed_sensitive():
    snap = wide_snapshot(30)
    a = to_instruments(snap, initial_s="stationary", s_seed=11, **MACRO)
    b = to_instruments(snap, initial_s="stationary", s_seed=11, **MACRO)
    c = to_instruments(snap, initial_s="stationary", s_seed=12, **MACRO)
    assert [i.initial_price for i in a] == [i.initial_price for i in b]
    assert [i.initial_price for i in a] != [i.initial_price for i in c]


def test_the_dispersion_seed_does_not_perturb_the_market():
    """Its own stream, so seeding a universe cannot change the market.

    If they shared a stream, choosing a different dispersion seed would give a
    different market, and "same universe, different draws" would be a lie.
    """
    snap = wide_snapshot(20)
    zero = tradefloor.Universe(to_instruments(snap, initial_s="zero", **MACRO))

    def prices(seed):
        e = tradefloor.Engine(seed=99, universe=zero, macro_state=tradefloor.Macro(**MACRO))
        e.run_days(2, ticks_per_day=100, record=False)
        return arr(e.prices())

    # The market is untouched by anything the loader drew.
    assert prices(1) == prices(2)


def test_an_unknown_mode_is_refused():
    with pytest.raises(tradefloor.ValidationError, match='"zero" or "stationary"'):
        to_instruments(wide_snapshot(3), initial_s="wishful", **MACRO)


def test_stationary_sigma_refuses_nonsense_and_reports_non_stationarity():
    with pytest.raises(tradefloor.ValidationError, match="finite"):
        tradefloor.stationary_sigma(float("nan"))
    # A unit root has infinite variance. None rather than a large finite
    # number, which would be worse: it would get used.
    assert tradefloor.stationary_sigma(0.01, phi=1.0, theta=0.0) is None


# --------------------------------------------------------------------------
# Against the real SEC
# --------------------------------------------------------------------------
#
# Opt-in, because a test suite that needs a network is a test suite that fails
# on a train. Run with PRETIUM_NETWORK_TESTS=1 and a contact address in
# PRETIUM_SEC_USER_AGENT.
#
# Everything above drives `fetch` through an injected transport, which proves
# the DERIVATION and says nothing about whether the SEC returns the shape it
# was written against. This is the half that only reality can answer, and it
# has been answered once by hand -- these record what was found so a change at
# the SEC surfaces as a failure rather than as an empty universe.

import os

NETWORK = os.environ.get("PRETIUM_NETWORK_TESTS") == "1"
SEC_UA = os.environ.get("PRETIUM_SEC_USER_AGENT", "")

network = pytest.mark.skipif(
    not NETWORK or "@" not in SEC_UA,
    reason="set PRETIUM_NETWORK_TESTS=1 and PRETIUM_SEC_USER_AGENT='Name you@example.org'",
)


@network
def test_the_frames_api_returns_the_shape_this_was_written_against():
    from tradefloor.edgar import _frame, default_transport

    get = default_transport(SEC_UA)
    eps = _frame(get, "us-gaap", "EarningsPerShareDiluted", "USD-per-shares",
                 "CY2023")
    # Verified live: 5,716 filers reported diluted EPS for CY2023. The exact
    # count moves as companies amend, so this asserts the ORDER of magnitude,
    # which is what would change if the endpoint or the tag did.
    assert len(eps) > 3_000
    assert all(isinstance(k, int) and isinstance(v, float) for k, v in
               list(eps.items())[:20])


@network
def test_a_missing_frame_really_does_404():
    # The fallback chain depends on it. Verified live: SalesRevenueNet has no
    # CY2023 frame and returns 404, which `_frame` turns into an empty dict
    # rather than an exception -- so the chain moves on instead of dying.
    from tradefloor.edgar import _frame, default_transport

    get = default_transport(SEC_UA)
    assert _frame(get, "us-gaap", "SalesRevenueNet", "USD", "CY2023") == {}


@network
def test_merging_both_share_tags_reaches_far_more_filers():
    """The defect this found, pinned against the live API.

    Measured on CY2023: `dei` covers 2,717 filers and `us-gaap` 4,971. Of the
    5,716 with diluted EPS, dei alone reaches 1,966 and the union reaches
    4,733 -- so taking dei and falling back only when it came back EMPTY
    dropped more than half the usable universe, invisibly.
    """
    from tradefloor.edgar import _frame, default_transport

    get = default_transport(SEC_UA)
    dei = _frame(get, "dei", "EntityCommonStockSharesOutstanding", "shares",
                 "CY2023Q4I")
    gaap = _frame(get, "us-gaap", "CommonStockSharesOutstanding", "shares",
                  "CY2023Q4I")
    assert len(gaap) > len(dei), "us-gaap is the broader tag"
    assert len(set(dei) | set(gaap)) > len(gaap), "each carries filers the other lacks"


@network
def test_a_real_fetch_builds_a_universe_that_drives_an_engine():
    snapshot = tradefloor.edgar.fetch(as_of="2024-06-30", user_agent=SEC_UA,
                                   limit=20)
    assert len(snapshot.rows) == 20
    assert snapshot.notes["candidates"] > 3_000
    for row in snapshot.rows:
        assert row["sector"] in tradefloor.sectors()
        assert row["shares_outstanding"] > 0

    universe = tradefloor.Universe.from_edgar(snapshot, federal_funds_rate=0.05,
                                           corporate_bond_yield=0.055)
    engine = tradefloor.Engine(
        seed=1, universe=universe,
        macro_state=tradefloor.Macro(federal_funds_rate=0.05,
                                  corporate_bond_yield=0.055))
    engine.run_days(3)
    assert all(p > 0 for p in arr(engine.prices()))
