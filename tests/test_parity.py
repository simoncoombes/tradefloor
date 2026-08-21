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

# The goldens are NOT in this repository, and that is a decision rather than an
# oversight. They are 135 MB, and they are produced by a reference
# implementation that is not public -- so committing them would bloat the repo
# permanently with fixtures nobody outside can regenerate or check.
#
# What IS public is the determinism contract: the known-answer digest, which
# needs no fixtures and runs on every wheel target. Parity against the
# reference is a development gate, not a user-facing guarantee.
#
# The hazard that leaves is a green run that verified nothing. Thirty-four
# tests skipping still reports success, and "goldens absent" looks like a
# tidy skip rather than the parity suite not having executed. So a gate that
# claims to check parity sets PRETIUM_REQUIRE_GOLDENS=1 and gets a hard
# failure instead of a quiet pass.
import os

_REQUIRED = os.environ.get("PRETIUM_REQUIRE_GOLDENS") == "1"

if _REQUIRED and not GOLDENS.exists():
    raise RuntimeError(
        "PRETIUM_REQUIRE_GOLDENS=1 but rust/goldens is absent. The parity "
        "suite would have skipped all 34 tests and reported success. Run "
        "`python rust/sync-goldens.py` or unset the variable."
    )

pytestmark = pytest.mark.skipif(
    not GOLDENS.exists(),
    reason="goldens absent - run `python rust/sync-goldens.py`, or set "
           "PRETIUM_REQUIRE_GOLDENS=1 to make this a failure",
)


def f64(hexbits):
    """Decode a golden's big-endian f64 hex. `None` stays `None`."""
    if hexbits is None:
        return None
    return struct.unpack(">d", bytes.fromhex(hexbits))[0]


def bits(value):
    """Encode an f64 as big-endian hex. `None` stays `None`.

    The None case is load-bearing: an empty book side has no best price, and
    the goldens record that as null rather than as a sentinel value.
    """
    if value is None:
        return None
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


# --------------------------------------------------------------------------
# The daily mispricing model
# --------------------------------------------------------------------------

def _series(field, n):
    """Goldens store a constant series compressed. Handle both encodings."""
    if isinstance(field, list):
        return [f64(h) for h in field]
    if isinstance(field, dict) and "constantBits" in field:
        return [f64(field["constantBits"])] * field.get("length", n)
    raise TypeError(f"unexpected series encoding: {type(field).__name__}")


def test_daily_step_matches_the_reference_bit_for_bit():
    """All 58,080 recorded step cases, including out-of-cap states.

    These use independent `s` and `s_prev`, which is why the constructor
    accepts both: mid-trajectory they genuinely differ, and that difference IS
    the momentum term. Building them through the clamping constructor would
    zero the momentum and quietly test a different process.
    """
    golden = load("mispricing-step-cases.json")
    ix = {name: i for i, name in enumerate(golden["columns"])}
    checked = 0

    for row in golden["rows"]:
        s, s_prev, innovation, shock = (
            f64(row[ix[c]]) for c in ("inS", "inSPrev", "innovation", "shock")
        )
        if not all(math.isfinite(v) for v in (s, s_prev, innovation, shock)):
            continue
        got = pretium.step_mispricing_daily(
            pretium.MispricingState(s, s_prev), innovation=innovation, shock=shock
        )
        assert bits(got.s) == row[ix["outS"]].lower(), row
        assert bits(got.s_prev) == row[ix["outSPrev"]].lower(), row
        checked += 1

    assert checked > 50_000, f"only {checked} cases ran"


@pytest.mark.parametrize(
    "name",
    ["calm", "news-shocks", "garch-clustered", "extreme-clamped", "denormal-drift"],
)
def test_hundred_thousand_step_trajectories_do_not_drift(name):
    """A long trajectory is the test a single step cannot be.

    Per-step parity does not imply trajectory parity: a one-ULP disagreement
    that recurs feeds its own successor, so drift compounds. Five scenarios,
    100,000 steps each, every step checked -- including `denormal-drift`,
    which pushes `s` into subnormal territory where the arithmetic is least
    forgiving, and `extreme-clamped`, where the cap binds repeatedly.
    """
    golden = load(f"mispricing-trajectory-{name}.json")
    expected = golden["outputSBits"]
    n = len(expected)
    innovations = _series(golden["innovations"], n)
    shocks = _series(golden["shocks"], n)

    state = pretium.MispricingState(f64(golden["trajectory"]["initialSInput"]["bits"]))
    for i in range(n):
        state = pretium.step_mispricing_daily(
            state, innovation=innovations[i], shock=shocks[i]
        )
        if bits(state.s) != expected[i].lower():
            pytest.fail(f"{name} diverged at step {i}: {bits(state.s)} != {expected[i]}")


def test_model_constants_match_the_reference():
    golden = load("mispricing-constants.json")["constants"]
    preset = pretium.model_preset()
    pairs = {
        "MISPRICING_PHI": "mispricing_phi",
        "MOMENTUM_THETA": "momentum_theta",
        "MISPRICING_CAP": "mispricing_cap",
        "DAILY_SHOCK_CAP": "daily_shock_cap",
        "MISPRICING_HALF_LIFE_DAYS": "mispricing_half_life_days",
    }
    for golden_name, preset_key in pairs.items():
        assert bits(preset[preset_key]) == golden[golden_name]["bits"].lower(), preset_key


def test_the_preset_is_named_and_carries_only_live_coefficients():
    preset = pretium.model_preset()
    assert preset["name"] == "pt-v1"
    # Dead coefficients must not appear. A preset listing knobs wired to
    # nothing is a documentation lie, and this model has two such constants
    # (mean-reversion) that belong to a discarded factor.
    assert not any("mean_reversion" in k for k in preset)


def test_the_daily_process_is_provably_stationary():
    # The reason the daily step is the public model rather than the tick
    # variant: this is provable in closed form, not merely observed.
    moduli = pretium.characteristic_root_moduli()
    assert len(moduli) == 2
    assert all(m < 1.0 for m in moduli), moduli
    # Crowd feedback must not push it outside the unit circle either.
    assert all(m < 1.0 for m in pretium.crowd_adjusted_root_moduli())


def test_impulse_response_decays():
    ir = pretium.impulse_response(400)
    assert ir[0] == 1.0
    # Momentum makes it rise before it falls, so the assertion is about the
    # tail, not monotonicity.
    assert abs(ir[-1]) < abs(ir[0])
    assert abs(ir[-1]) < 0.05


def test_apply_mispricing_never_returns_a_negative_price():
    assert pretium.apply_mispricing(-50.0, 0.1) > 0
    assert pretium.apply_mispricing(0.0, 0.0) > 0


def test_resuming_a_trajectory_preserves_momentum():
    # The reason `s_prev` is constructible. Rebuilding a mid-trajectory state
    # through the single-argument constructor zeroes the momentum term and
    # produces a different path -- silently.
    state = pretium.MispricingState(0.0)
    for _ in range(5):
        state = pretium.step_mispricing_daily(state, innovation=0.01)

    resumed_correctly = pretium.step_mispricing_daily(
        pretium.MispricingState(state.s, state.s_prev), innovation=0.01
    )
    resumed_wrongly = pretium.step_mispricing_daily(
        pretium.MispricingState(state.s), innovation=0.01
    )
    continued = pretium.step_mispricing_daily(state, innovation=0.01)

    assert resumed_correctly.s == continued.s
    assert resumed_wrongly.s != continued.s


# --------------------------------------------------------------------------
# The order book
# --------------------------------------------------------------------------

def test_order_book_replays_the_reference_exactly():
    """Replay the recorded 75-step operation log and match state at each step.

    Two notes on how this replay is constructed.

    The golden includes an `appendMakerLevel` op. I first replayed it through
    `post_limit` on the belief that the two differ only in insertion strategy.
    They do not: at the depth cap `append_maker_level` REFUSES and consumes no
    sequence number, while `post_limit` accepts and truncates the far end. The
    golden's cap-probe steps exercise exactly that boundary, and the
    substitution desynchronised order ids from step 74 onward -- visible only
    because fills carry maker ids, since the level counts still matched.

    Five ops in the golden are invalid input (zero, negative and NaN sizes and
    prices). The core no-ops them; this binding raises. Either way the book
    must be UNCHANGED, so they are executed, caught, and the state compared
    anyway rather than skipped.
    """
    golden = load("orderbook.json")
    book = pretium.OrderBook("ACME")
    rejected = 0

    for step in golden["steps"]:
        op = step["op"]
        kind = op["op"]
        try:
            if kind == "postLimit":
                book.post_limit(
                    op["side"], f64(op["price"]), f64(op["quantity"]),
                    owner=op["ownerId"], order_id=op.get("orderId"),
                )
            elif kind == "appendMakerLevel":
                book.append_maker_level(
                    op["side"], f64(op["price"]), f64(op["quantity"]),
                    owner=op["ownerId"],
                )
            elif kind == "submit":
                result = book.submit(
                    op["side"], f64(op["quantity"]), taker=op["takerId"],
                    limit_price=f64(op.get("limitPrice")),
                    post_remainder=op.get("postRemainder", False),
                    order_id=op.get("orderId"),
                )
                expected = step.get("result") or {}
                for got_fill, want_fill in zip(result.fills, expected.get("fills", [])):
                    assert bits(got_fill.price) == want_fill["price"].lower(), step
                    assert bits(got_fill.quantity) == want_fill["quantity"].lower(), step
                    assert got_fill.maker_order_id == want_fill["makerOrderId"], step
                if expected.get("averagePrice") is not None:
                    assert bits(result.average_price) == expected["averagePrice"].lower(), step
            elif kind == "cancel":
                book.cancel_order(op["orderId"])
            elif kind == "cancelAllFor":
                book.cancel_all_for(op["ownerId"])
            elif kind == "sweepCost":
                got = book.sweep_cost(op["side"], f64(op["quantity"]))
                want = step.get("result")
                if want is not None and got is not None:
                    assert bits(got.average_price) == want["averagePrice"].lower(), step
                    assert bits(got.worst_price) == want["worstPrice"].lower(), step
                    assert bits(got.filled) == want["filled"].lower(), step
        except (pretium.ValidationError, pretium.OrderError):
            rejected += 1

        state = step["state"]
        assert bits(book.best_bid) == ((state["bestBid"] or "").lower() or None), step
        assert bits(book.best_ask) == ((state["bestAsk"] or "").lower() or None), step
        assert sum(l.orders for l in book.price_levels("buy", 64)) == len(state["bids"]), step
        assert sum(l.orders for l in book.price_levels("sell", 64)) == len(state["asks"]), step

    assert rejected == 5, f"expected 5 invalid ops to be refused, got {rejected}"


def test_slippage_is_emergent_not_a_coefficient():
    """A large order pays more because it ATE levels, not because a formula said so.

    This is the package's distinguishing claim about execution, so it is
    asserted rather than described: build a ladder, take a small bite and a
    large one, and the large one's average price must be strictly worse -- and
    worse specifically because it reached deeper levels.
    """
    book = pretium.OrderBook("ACME")
    for i in range(10):
        book.post_limit("sell", 100.0 + i, 100, owner="mm")

    small = book.submit("buy", 50)
    assert small.average_price == 100.0  # entirely inside the best level

    book2 = pretium.OrderBook("ACME")
    for i in range(10):
        book2.post_limit("sell", 100.0 + i, 100, owner="mm")
    large = book2.submit("buy", 550)

    assert large.average_price > small.average_price
    # 550 shares spans six levels (100..105), so the worst price paid is 105.
    assert max(f.price for f in large.fills) == 105.0
    assert len(large.fills) == 6


def test_fills_take_the_resting_price_not_the_incoming_one():
    book = pretium.OrderBook("ACME")
    book.post_limit("sell", 100.0, 100, owner="mm")
    # A buyer willing to pay 110 still fills at the maker's 100.
    result = book.submit("buy", 50, limit_price=110.0)
    assert result.fills[0].price == 100.0


def test_sweep_cost_does_not_execute():
    book = pretium.OrderBook("ACME")
    book.post_limit("sell", 100.0, 100, owner="mm")
    before = book.depth("sell")
    cost = book.sweep_cost("buy", 50)
    assert cost is not None and cost.filled == 50
    assert book.depth("sell") == before, "sweep_cost must be a quote, not a trade"


def test_market_order_remainder_never_rests():
    book = pretium.OrderBook("ACME")
    book.post_limit("sell", 100.0, 10, owner="mm")
    result = book.submit("buy", 100, post_remainder=True)  # market order
    assert result.unfilled == 90
    assert result.resting_order_id is None
    assert book.depth("buy") == 0


def test_the_boundary_refuses_what_the_core_silently_ignores():
    # The core returns an empty result for these, reproducing the reference.
    # A silently ignored order is the worst failure available: the caller
    # believes they traded and nothing disagrees out loud.
    book = pretium.OrderBook("ACME")
    for bad in (0, -10, float("nan"), float("inf")):
        with pytest.raises(pretium.ValidationError):
            book.submit("buy", bad)
        with pytest.raises(pretium.ValidationError):
            book.post_limit("buy", 100.0, bad, owner="x")


def test_unknown_side_is_rejected():
    book = pretium.OrderBook("ACME")
    with pytest.raises(pretium.ValidationError, match="buy"):
        book.submit("BUY", 10)


def test_order_error_is_distinct_from_validation_error():
    # Both subclass ValueError, but they mean different things: a malformed
    # scenario versus a market rule hit during a run.
    assert issubclass(pretium.OrderError, ValueError)
    assert issubclass(pretium.ValidationError, ValueError)
    assert not issubclass(pretium.OrderError, pretium.ValidationError)


def test_append_and_post_diverge_at_the_depth_cap():
    """They are not interchangeable, and the difference is silent.

    Pinned because I assumed they were and was wrong. At MAX_DEPTH_PER_SIDE
    (32) `append_maker_level` refuses and consumes no sequence number, while
    `post_limit` accepts and truncates the far end. Substituting one for the
    other desynchronises every subsequent order id -- and nothing about the
    level counts reveals it, so the symptom appears far from the cause.
    """
    appended = pretium.OrderBook("A")
    for i in range(40):
        appended.append_maker_level("sell", 100.0 + i, 10, owner="mm")

    posted = pretium.OrderBook("A")
    for i in range(40):
        posted.post_limit("sell", 100.0 + i, 10, owner="mm")

    # Both sides end capped at 32 levels...
    assert sum(l.orders for l in appended.price_levels("sell", 64)) == 32
    assert sum(l.orders for l in posted.price_levels("sell", 64)) == 32

    # ...but they kept DIFFERENT levels. Append refused everything past the
    # cap, so it holds the first 32. Post truncated the far end, so it holds
    # the best 32 -- identical here because price order matches insert order,
    # which is exactly why this is easy to miss.
    assert appended.best_ask == 100.0
    assert posted.best_ask == 100.0

    # The observable difference is the id counter: append stopped consuming
    # sequence numbers at the cap, post did not.
    next_appended = appended.append_maker_level("buy", 1.0, 1, owner="mm")
    next_posted = posted.post_limit("buy", 1.0, 1, owner="mm")
    assert next_appended != next_posted
