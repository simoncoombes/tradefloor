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
#
# v4: the 2026-08 era boundary. The daily macro step now runs at every
# close (advance_day was previously unreachable from Python) and
# avg_volume no longer feeds back on itself (the reference's EMA
# compounded without bound). Both change every seed's trajectory.
#
# v5: same era boundary, third and fourth changes, one bump. First,
# MARKET_FACTOR_SIGMA recalibrated from the reference's 0.003
# (cross-sectional correlation 0.02 against a real 0.25-0.35, design
# findings 7-9); the sweep behind the chosen value is
# tools/calibration/sweep_market_factor_sigma.py. Second, the RNG stream
# split: the engine now derives three independent substreams (market,
# economy, external) from the root seed instead of running everything off
# GameRng(seed, 99), and settlement's four uniforms are drawn
# unconditionally per active company per open tick. Each changes every
# trajectory; the direct GameRng section below moves for neither, because
# the Layer-1 generator API is untouched.
#
# v6: same era boundary, fifth change. The variance process gains a
# GJR asymmetry term (GAMMA = 0.34, funded by ALPHA 0.09 -> 0.02 and
# BETA 0.90 -> 0.80 at held persistence 0.99), because the symmetric
# GARCH squares the return at the only site where a realised return
# touches future variance, so no parameter of it could produce the
# leverage effect real equities have (design finding 8). The term is
# written to be bit-identical at GAMMA = 0 -- proven against the v5
# digest -- so this bump is the calibration, not the code structure;
# the chosen point is the seventeen-point sweep in
# tools/calibration/results/gjr-gamma-2026-08-21.json. It changes every
# trajectory; the direct GameRng section below does not move.
#
# v7: same era boundary, sixth change, three parts, one bump. First, the
# shared market factor gains its own conditional-variance process
# (rust/src/market/factor_vol.rs): a daily GARCH(1,1) on the factor's
# accumulated innovation, consuming zero draws, so the factor's sigma is
# a regime rather than the constant it was -- the escape finding 14
# named from the correlation-for-kurtosis trade it measured. Its baseline
# MARKET_FACTOR_SIGMA moves 0.0075 -> 0.016, funded by IDIO_SIGMA_SCALE
# 0.84 on the per-name idiosyncratic sigma, so total volatility falls
# while the correlated share triples; the sweep behind the vector is
# tools/calibration/sweep_market_factor_vol.py, results committed. At
# the shipped point, cross-sectional correlation (0.260) and excess
# kurtosis (3.14) sit inside their real bands together for the first
# time. Second, the crisis-correlation blend in tick.rs is re-sited from
# the dead `vix > 40` / `/30` ramp to CRISIS_VIX_THRESHOLD (25.5) with a
# /1.4 ramp, in step with the economy's re-sited crisis gates: endogenous
# VIX has a measured hard ceiling of 26.57, so the old trigger could
# never fire. Third, the crash amplifier's threshold stays denominated in
# the BASELINE sigma by name, so factor-variance regimes raise its firing
# rate -- the measured crisis-correlation channel. Every trajectory
# changes; the direct GameRng section below does not move.
#
# v8: same era boundary, seventh change, one constant. The market
# factor's variance target is now coupled to VIX
# (MARKET_VOL_VIX_COUPLING 0.0 -> 1.0 in rust/src/market/factor_vol.rs):
# the reversion target scales by (VIX/15)^2, VIX read as the factor's
# implied volatility and anchored at the endogenous mean. This is the
# era decision that closed design finding 6's open question -- the v7
# process was built with the coupling measured and deliberately OFF,
# because flipping it silently would have falsified the tested claims
# in pretium.scenario and docs/scenarios.md that VIX does not drive
# volatility; those claims were rewritten in the change that flipped
# it. Endogenously the coupled panel is statistically indistinguishable
# from v7's (correlation 0.257 vs 0.260, kurtosis 3.11 vs 3.14); under
# a pinned crisis VIX it reaches crisis correlation 0.712/0.779 at VIX
# 45/65, the real crisis band nothing else has touched. Zero new
# draws, so the trajectory change is arithmetic only: every close's
# target now carries the day's VIX, and the KAT macro starts in
# contraction at VIX 19.5, so every session past the first close
# moves. The direct GameRng section below does not.
# v11: the pt-v12 era boundary (§114). Two changes, one of them a
# trajectory change and one deliberately not.
#
# The trajectory change is the default preset: pt-v10 to pt-v12, which is
# pt-v11's crisis work plus `volume_move_cap` 4.0 -> 12.0. The cap was a
# literal in `tick.rs` from the first version and saturated a name's volume
# response at a four percent day, so every crisis session traded exactly as
# much as a bad Tuesday. Unpinning it makes pt-v12 the first preset to hold
# all fourteen realism statistics in band at BOTH 252 and 504 days, and on a
# held-out universe. Zero new draws: the cap is arithmetic on a value the
# tick already had.
#
# The change that is NOT a trajectory change, recorded because it looks like
# one: endogenous news moved from `run_session` to `open_market`, and its
# chaining to `tick_inner` (§117). That fixed a tick-driven day rolling the
# day's news 390 times, and `Engine::tick` seeing no endogenous news at all.
# Every path that measures this model runs one session per day, so the
# trajectory fingerprint did not move -- `b1bbc17be7bf6aee` before and after.
# 2026-08-28: the pt-v12 to pt-v14 era boundary. Every seeded run changes,
# so the digest must. pt-v14 is the sector-block vector: `sector_factor_sigma`
# carries more of the systematic variance while the market factor's
# persistence compensates, which pulls crisis co-movement off the ceiling
# pt-v12 sits against. It holds the 504-day panel on 11 seed blocks of 13
# where pt-v12 holds 3, halves the crisis lever error, and is never worse on
# any block measured. Two new dials ship inert with it
# (`crisis_blend_variance_damp`, `qe_pe_gain`) and do not move the
# trajectory at their defaults.
KAT_VERSION = 12

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


def simulation_buffer() -> bytes:
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

    return bytes(buf)


def metadata_buffer() -> bytes:
    """The REPORTED model preset, hashed separately from the simulation.

    # Why this is not part of the simulation buffer

    It used to be, and that conflation cost a day. `model_preset()` reports
    coefficients; it does not run anything. When its default argument was
    found frozen at "pt-v1" while engines ran pt-v3, fixing the report moved
    the combined digest -- and the gate could not tell that from a simulation
    change, because both landed in one hash.

    That left two bad doors. Bumping `KAT_VERSION` would assert the
    simulation had changed, which was false. Regenerating without a bump
    would defeat the drift check the version exists to enforce. Neither was
    right, because the defect was the conflation rather than the choice.

    So the digests are split. `simulation_digest()` is the cross-platform
    determinism gate and moves ONLY when a trajectory moves.
    `metadata_digest()` covers what the library says about itself, and can be
    re-based when a reporting bug is fixed without anyone claiming the market
    changed.

    Sorted by key so the digest cannot depend on dict ordering.
    """
    buf = bytearray()
    preset = pretium.model_preset()
    for key in sorted(k for k in preset if k != "name"):
        _f64(buf, float(preset[key]))
    return bytes(buf)


def known_answer_buffer() -> bytes:
    """Both halves, concatenated -- the historical shape, byte for byte."""
    return simulation_buffer() + metadata_buffer()


def simulation_digest() -> str:
    """The determinism gate. Moves only when a trajectory moves."""
    return hashlib.sha256(simulation_buffer()).hexdigest()


def metadata_digest() -> str:
    """What the library reports about itself. Not a trajectory claim."""
    return hashlib.sha256(metadata_buffer()).hexdigest()


def known_answer_digest() -> str:
    return hashlib.sha256(known_answer_buffer()).hexdigest()


if __name__ == "__main__":
    data = known_answer_buffer()
    print(f"pretium known-answer test v{KAT_VERSION}")
    print(f"  package  {pretium.version()}")
    print(f"  bytes    {len(data)}")
    print(f"  sha256   {known_answer_digest()}")
    print(f"  sim      {simulation_digest()}")
    print(f"  meta     {metadata_digest()}")
