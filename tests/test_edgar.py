"""Real fundamentals as initial conditions.

Everything here is offline. The network fetch and the universe construction
are separate operations precisely because they have different determinism
properties, and the pure half is the half worth testing hard.
"""

import json
import struct

import pytest

import pretium
from pretium.edgar import LOADER_VERSION, Snapshot, filter_rows, to_instruments

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
    with pytest.raises(pretium.ValidationError, match="newer"):
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
        expected = pretium.fair_value(
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
    with pytest.raises(pretium.ValidationError, match="shares_outstanding"):
        to_instruments(Snapshot(as_of="x", rows=rows), **MACRO)


# --------------------------------------------------------------------------
# Using it
# --------------------------------------------------------------------------

def test_a_loaded_universe_drives_the_engine():
    u = pretium.Universe.from_edgar(snapshot(), **MACRO)
    engine = pretium.Engine(seed=1, universe=u, macro_state=pretium.Macro(**MACRO))
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
    u = pretium.Universe.from_edgar(snapshot(), **MACRO)

    matched = pretium.Engine(seed=1, universe=u, macro_state=pretium.Macro(**MACRO))
    matched.open_market()
    matched.tick(9, 30, 3)

    mismatched = pretium.Engine(seed=1, universe=u, macro_state=pretium.Macro(
        federal_funds_rate=0.08, corporate_bond_yield=0.10))
    mismatched.open_market()
    mismatched.tick(9, 30, 3)

    near = max(abs(x) for x in arr(matched.column("mispricing_s")))
    far = max(abs(x) for x in arr(mismatched.column("mispricing_s")))
    assert near < 0.01
    assert far > 10 * near


def test_loading_is_reproducible():
    a = pretium.Universe.from_edgar(snapshot(), **MACRO)
    b = pretium.Universe.from_edgar(snapshot(), **MACRO)
    assert [i.initial_price for i in a] == [i.initial_price for i in b]
    assert [i.beta for i in a] == [i.beta for i in b]


def test_from_edgar_accepts_a_path(tmp_path):
    path = tmp_path / "snap.json"
    snapshot().save(str(path))
    u = pretium.Universe.from_edgar(str(path), **MACRO)
    assert u.tickers() == [r["ticker"] for r in ROWS]


# --------------------------------------------------------------------------
# The unbuilt half
# --------------------------------------------------------------------------

def test_fetch_says_plainly_that_it_is_not_built():
    # Better than a stub that returns something plausible. A loader that
    # quietly produced empty or fabricated data would be discovered late.
    with pytest.raises(NotImplementedError, match="not implemented"):
        pretium.edgar.fetch(as_of="2024-06-30")
