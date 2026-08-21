"""The package's own parity and determinism suite.

WP7's acceptance asks for "a determinism test in the package's own suite (same
seed -> same bits, run twice, plus a committed known-answer file)". This is
that, plus the stronger claim: the Python surface reproduces the TypeScript
reference bit-for-bit, not merely itself.

Run with the goldens present:

    python rust/sync-goldens.py      # once, to fetch them
    pytest tests/
"""

import json
import math
import struct
from pathlib import Path

import pytest

import pretium

GOLDENS = Path(__file__).resolve().parent.parent / "rust" / "goldens"

pytestmark = pytest.mark.skipif(
    not GOLDENS.exists(),
    reason="goldens absent - run `python rust/sync-goldens.py`",
)


def f64(hexbits):
    """Decode a golden's big-endian f64 hex. `None` stays `None`."""
    if hexbits is None:
        return None
    return struct.unpack(">d", bytes.fromhex(hexbits))[0]


def bits(value):
    return struct.pack(">d", value).hex()


def load(name):
    return json.loads((GOLDENS / name).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------

def test_same_seed_same_draws():
    a = pretium.GameRng(42, 99)
    b = pretium.GameRng(42, 99)
    assert [a.next_float() for _ in range(64)] == [b.next_float() for _ in range(64)]


def test_different_seed_diverges():
    a = pretium.GameRng(42, 99)
    b = pretium.GameRng(43, 99)
    assert a.next_float() != b.next_float()


def test_streams_are_independent():
    # Two generators in one process must not share state. If they did,
    # "same seed, same market" would hold only for the first one constructed.
    a = pretium.GameRng(1, 99)
    first = a.next_float()
    b = pretium.GameRng(1, 99)  # constructed AFTER a has drawn
    assert b.next_float() == first


def test_normal_draws_carry_the_box_muller_spare():
    """Two normals cost two uniforms, not four -- the second is free.

    Box-Muller produces a PAIR from two uniforms and caches the second. So the
    parity of how many normals have been drawn is part of the generator's
    state, and draw accounting is not simply one-uniform-per-value.

    Measured rather than assumed, because the obvious guess is wrong: an
    interleaved uniform does NOT perturb the following normal, since that
    normal is served from the cache and touches the stream not at all.

    A "tidy-up" that dropped the cache would consume four uniforms for two
    normals, shifting every subsequent draw and changing every market -- while
    still passing any test that only ever drew normals.
    """
    fresh = pretium.GameRng(7, 99)
    uniforms = [fresh.next_float() for _ in range(4)]

    one = pretium.GameRng(7, 99)
    one.next_normal()
    # The first normal consumed exactly two uniforms.
    assert one.next_float() == uniforms[2]

    two = pretium.GameRng(7, 99)
    two.next_normal()
    two.next_normal()
    # The second consumed none: the stream is in the same place.
    assert two.next_float() == uniforms[2]


# --------------------------------------------------------------------------
# Parity against the TypeScript reference
# --------------------------------------------------------------------------

def test_uniform_draws_match_the_reference_bit_for_bit():
    golden = load("prng-floats.json")
    checked = 0
    for series in golden["series"]:
        spec = series["input"]
        rng = pretium.GameRng(spec["seed"], spec["sequence"])
        for expected in series.get("decimalPreview") or []:
            assert bits(rng.next_float()) == expected["bits"].lower()
            checked += 1
    assert checked > 0, "golden yielded no cases - the test would pass vacuously"


def test_fair_value_matches_the_reference_bit_for_bit():
    """Every case the API admits must reproduce the reference exactly.

    The goldens carry inputs in the CORE's percent denomination, so they are
    divided by 100 here and pass back through the boundary's x100. That
    round-trip is two roundings and was not assumed to be exact -- it is
    measured by this test, and currently is, for every admitted case.

    Cases the boundary rejects (NaN, out-of-band rates) are counted, not
    skipped silently: they exist because the core reproduces the TypeScript's
    NaN behaviour faithfully while the boundary refuses to let a user reach
    it. Both halves of that policy are asserted.
    """
    golden = load("fairvalue.json")
    matched = rejected = 0

    for case in golden["cases"]:
        i = case["in"]
        expected = case["computeFairValue"]
        ffr = f64(i["federalFundsRate"])
        cby = f64(i["corporateBondYield"])

        kwargs = dict(
            eps=f64(i["eps"]),
            sector=i["sector"],
            revenue_growth=f64(i["revenueGrowth"]),
            federal_funds_rate=0.0 if ffr is None else ffr / 100.0,
            corporate_bond_yield=None if cby is None else cby / 100.0,
            qe_pe_boost=f64(i["qePeBoost"]),
            book_value_per_share=f64(i["bookValuePerShare"]),
        )

        try:
            got = pretium.fair_value(**kwargs)
        except ValueError:
            rejected += 1
            continue

        assert bits(got.fair_value) == expected["fairValue"].lower(), i
        assert bits(got.target_pe) == expected["targetPE"].lower(), i
        assert got.book_value_path == expected["bookValuePath"], i
        matched += 1

    assert matched > 200, f"only {matched} cases admitted - suite may be vacuous"
    assert rejected > 0, "no case exercised the validation boundary"


# --------------------------------------------------------------------------
# The units boundary
# --------------------------------------------------------------------------

def test_rates_are_fractional_not_percent():
    # The whole point of the convention. 4.5 means 450%, not 4.5%, and must
    # be refused with a message that says so.
    with pytest.raises(ValueError, match="percent"):
        pretium.fair_value(eps=4.0, sector="technology", federal_funds_rate=4.5)


def test_growth_is_not_a_rate_and_is_not_rescaled():
    # revenue_growth is fractional in BOTH denominations. If the boundary
    # scaled it like a rate, 22% growth would become 2200% and produce a
    # large, finite, entirely wrong multiple -- with no error anywhere.
    low = pretium.fair_value(eps=4.0, sector="technology", revenue_growth=0.0,
                             federal_funds_rate=0.05)
    high = pretium.fair_value(eps=4.0, sector="technology", revenue_growth=0.22,
                              federal_funds_rate=0.05)
    # Growth raises duration, which deepens the rate penalty above neutral.
    assert high.rate_adjustment < low.rate_adjustment
    assert math.isfinite(high.fair_value)


def test_absent_yield_falls_through_but_zero_does_not():
    # Absence versus zero, the distinction the binding must not collapse.
    absent = pretium.fair_value(eps=4.0, sector="technology",
                                federal_funds_rate=0.08, corporate_bond_yield=None)
    zero = pretium.fair_value(eps=4.0, sector="technology",
                              federal_funds_rate=0.08, corporate_bond_yield=0.0)
    assert absent.rate_adjustment != zero.rate_adjustment


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def test_nan_is_refused_at_the_boundary():
    # The core reproduces the TypeScript's NaN behaviour on purpose; the
    # boundary makes sure no user arrives there by accident, because a NaN
    # fair value freezes a book rather than raising anything.
    for field in ("eps", "revenue_growth", "qe_pe_boost", "book_value_per_share"):
        with pytest.raises(ValueError, match="finite"):
            pretium.fair_value(**{
                "eps": 4.0, "sector": "technology", field: float("nan")
            })


def test_unknown_sector_lists_the_valid_ones():
    with pytest.raises(ValueError, match="technology"):
        pretium.fair_value(eps=4.0, sector="tecnology")


def test_negative_eps_is_legal_and_uses_the_book_path():
    # A universe without loss-makers is unrealistic; validation must not
    # "fix" a negative EPS.
    got = pretium.fair_value(eps=-2.0, sector="technology",
                             book_value_per_share=10.0, federal_funds_rate=0.04)
    assert got.book_value_path is True
    assert got.fair_value > 0


def test_sectors_are_the_twelve_in_declared_order():
    keys = pretium.sectors()
    assert len(keys) == 12
    assert keys[0] == "technology"
    assert keys[-1] == "transportation"
