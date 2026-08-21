"""The known-answer test: one fixed simulation, hashed.

This is the artifact behind the library's headline claim. The release pipeline
runs this on every wheel target -- manylinux x86_64 and aarch64, macOS arm64
and x86_64, Windows x86_64 -- and compares digests. Any platform disagreeing
with any other fails the release, so no wheel ships that has not proven the
determinism claim for its own platform.

It is deliberately small. The full parity corpus answers a different question
("does this match the reference implementation?") and needs the reference to
be present; this answers "do all our builds agree with each other?", which
needs nothing but the package.

## Canonicalisation

Everything hashed goes in as raw big-endian IEEE-754 f64 bytes. No decimal
formatting anywhere: `repr(float)` is platform- and version-sensitive in ways
that would make this test fail for reasons having nothing to do with the
simulation, or -- far worse -- pass while hiding a real difference in the low
bits. Booleans and counts are encoded as f64 too, so there is exactly one
encoding rule.

Ordering is fixed by construction: the instruments are a list in a written
order, never a dict or set, and every loop is over an index range. A set
iteration or a dict key order would introduce a platform-dependent ordering
that has nothing to do with arithmetic.

## What it exercises

Both layers, in one chain, because a digest over only one module would pass
while another drifted:

  - the RNG, uniform and normal, including the Box-Muller spare parity
  - fair value across all twelve sectors, both the earnings and book paths
  - the daily mispricing step over a long horizon, where 1-ULP errors compound
  - the order book: ladder construction, a sweep, partial fills, cancellation
  - the engine: twelve instruments across five sessions, including the day
    close, where the coupled system can disagree in ways no single module does
"""

import hashlib
import struct

import pretium

# Bumped only when the simulation itself changes. A digest change WITHOUT a
# version bump means a platform disagreed, which is the thing this exists to
# catch; a digest change WITH one is an intended new baseline.
KAT_VERSION = 2

SEED = 20260820
DAYS = 250


def _f64(buf: bytearray, value: float) -> None:
    """Append one f64 in canonical form.

    NaN is normalised to a single quiet-NaN bit pattern. IEEE-754 does not
    specify the sign or payload of a produced NaN, and platforms genuinely
    differ -- we saw exactly that against the reference, where a NaN came back
    as 7ff8... on one side and fff8... on the other. Hashing the raw bits would
    turn that non-difference into a release failure.
    """
    if value != value:  # NaN
        buf.extend(b"\x7f\xf8\x00\x00\x00\x00\x00\x00")
    else:
        buf.extend(struct.pack(">d", value))


def _opt(buf: bytearray, value) -> None:
    """Append an optional f64. Absence is distinct from any real value."""
    if value is None:
        buf.extend(b"\xff\xff\xff\xff\xff\xff\xff\xff")
    else:
        _f64(buf, value)


def known_answer_buffer() -> bytes:
    """Run the fixed simulation and return its canonical output buffer."""
    buf = bytearray()

    # --- 1. The generator, both draw kinds, interleaved -------------------
    #
    # Interleaved on purpose: the Box-Muller spare means the parity of normal
    # draws is generator state, so an implementation that produced correct
    # normals but accounted for them differently would still diverge here.
    rng = pretium.GameRng(SEED, 99)
    for i in range(64):
        _f64(buf, rng.next_float())
        _f64(buf, rng.next_normal())
        if i % 3 == 0:
            _f64(buf, float(rng.next_int(-1000, 1000)))
            _f64(buf, 1.0 if rng.next_bool(0.5) else 0.0)

    # --- 2. Fair value across every sector, both valuation paths ---------
    sectors = pretium.sectors()
    assert len(sectors) == 12, "sector table changed; KAT_VERSION must bump"

    instruments = []
    for index, sector in enumerate(sectors):
        # Deterministic, spread across the plausible range. Index 3 and 7 are
        # loss-makers so the book-value path is covered, and negative EPS is
        # legal by design rather than an edge case.
        eps = -1.5 if index in (3, 7) else 0.5 + index * 0.75
        instrument = {
            "sector": sector,
            "eps": eps,
            "revenue_growth": -0.05 + index * 0.04,
            "book_value_per_share": 8.0 + index,
        }
        instruments.append(instrument)

        value = pretium.fair_value(
            eps=instrument["eps"],
            sector=sector,
            revenue_growth=instrument["revenue_growth"],
            federal_funds_rate=0.02 + index * 0.002,
            corporate_bond_yield=None if index % 4 == 0 else 0.03 + index * 0.003,
            qe_pe_boost=0.0 if index % 2 else 0.05,
            book_value_per_share=instrument["book_value_per_share"],
        )
        _f64(buf, value.fair_value)
        _f64(buf, value.target_pe)
        _f64(buf, value.sector_anchor_pe)
        _f64(buf, value.rate_adjustment)
        _f64(buf, value.qe_adjustment)
        _f64(buf, 1.0 if value.book_value_path else 0.0)
        instrument["fair_value"] = value.fair_value

    # --- 3. A long mispricing trajectory per instrument ------------------
    #
    # 250 days x 12 instruments. Long enough that a single-ULP disagreement
    # compounds into something visible rather than being lost in rounding.
    for instrument in instruments:
        state = pretium.MispricingState(0.0)
        for day in range(DAYS):
            innovation = rng.next_normal() * 0.012
            shock = 0.02 if day % 61 == 60 else 0.0
            state = pretium.step_mispricing_daily(
                state, innovation=innovation, shock=shock
            )
            if day % 25 == 0:
                _f64(buf, state.s)
                _f64(buf, state.s_prev)
                _f64(buf, pretium.apply_mispricing(instrument["fair_value"], state.s))

    # --- 4. The order book ------------------------------------------------
    book = pretium.OrderBook("KAT", 100.0)
    for level in range(16):
        book.post_limit("sell", 100.0 + level * 0.25, 500 + level * 10, owner="mm")
        book.post_limit("buy", 99.75 - level * 0.25, 500 + level * 10, owner="mm")

    _opt(buf, book.best_bid)
    _opt(buf, book.best_ask)
    _opt(buf, book.mid_price)
    _opt(buf, book.spread)

    quote = book.sweep_cost("buy", 3000)
    _f64(buf, quote.average_price)
    _f64(buf, quote.worst_price)
    _f64(buf, quote.filled)

    # A taker large enough to walk several levels, so the digest covers the
    # emergent-impact path and not merely a top-of-book fill.
    result = book.submit("buy", 3000, taker="kat")
    _opt(buf, result.average_price)
    _f64(buf, result.unfilled)
    _f64(buf, float(len(result.fills)))
    for fill in result.fills:
        _f64(buf, fill.price)
        _f64(buf, fill.quantity)

    # A resting remainder, then a cancellation, so book mutation is covered.
    book.submit("buy", 50_000, taker="kat", limit_price=101.0, post_remainder=True,
                order_id="KAT-REST")
    _f64(buf, book.depth("buy"))
    _f64(buf, 1.0 if book.cancel_order("KAT-REST") else 0.0)
    _f64(buf, book.depth("buy"))
    _f64(buf, float(book.cancel_all_for("mm")))

    for level in book.price_levels("sell", 32):
        _f64(buf, level.price)
        _f64(buf, level.quantity)
        _f64(buf, float(level.orders))

    # --- 5. The engine: a whole market stepped through time ---------------
    #
    # The largest and most valuable part of the digest. Everything above
    # exercises one module at a time; this runs the coupled system, where the
    # shared factor structure, the order book and the macro state all feed each
    # other. A disagreement that cancelled out in isolation shows up here.
    sector_names = pretium.sectors()
    instruments = [
        pretium.Instrument(
            f"KAT{i}",
            sector_names[i % 12],
            initial_price=20.0 + i * 7.5,
            shares_outstanding=2.5e8 + i * 1e7,
            eps=(-1.0 if i in (5, 11) else 1.0 + i * 0.6),
            book_value_per_share=10.0 + i * 2.0,
            revenue_growth=-0.02 + i * 0.03,
            avg_volume=250_000 + i * 100_000,
            beta=0.7 + i * 0.1,
        )
        for i in range(12)
    ]

    engine = pretium.Engine(
        seed=SEED,
        universe=instruments,
        macro_state=pretium.Macro(
            vix=19.5, federal_funds_rate=0.0425, corporate_bond_yield=0.0610,
            inflation_rate=0.031, qe_pe_boost=0.0, fear_greed_index=38.0,
            cycle="contraction",
        ),
    )

    # Five sessions, so the day boundary and its close bookkeeping are covered
    # rather than only the intraday path. A GARCH update that drifted would
    # never show up in a single-session digest.
    for day in range(5):
        engine.open_market()
        engine.run_session(9, 30, 3, 78, volatility=1.0)
        engine.close_market()

        for field in (
            "price", "previous_close", "open", "high", "low",
            "volume", "market_cap", "mispricing_s", "garch_variance",
        ):
            for value in struct.unpack(
                "<%dd" % len(instruments), engine.column(field)
            ):
                _f64(buf, value)

    # The full final session buffer, which is where a row-major indexing error
    # would surface: a transposed read gives the right values in the wrong
    # order, and a digest over the whole buffer catches that where spot checks
    # would not.
    for value in struct.unpack(
        "<%dd" % (engine.session_ticks_written * len(instruments)),
        engine.session_prices(),
    ):
        _f64(buf, value)

    _f64(buf, float(engine.draws_consumed))

    # --- 6. The model preset ---------------------------------------------
    #
    # Sorted by key so the digest cannot depend on dict ordering.
    preset = pretium.model_preset()
    for key in sorted(k for k in preset if k != "name"):
        _f64(buf, float(preset[key]))

    return bytes(buf)


def known_answer_digest() -> str:
    return hashlib.sha256(known_answer_buffer()).hexdigest()


if __name__ == "__main__":
    data = known_answer_buffer()
    print(f"pretium known-answer test v{KAT_VERSION}")
    print(f"  package  {pretium.version()}")
    print(f"  bytes    {len(data)}")
    print(f"  sha256   {known_answer_digest()}")
