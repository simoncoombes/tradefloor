//! GJR-GARCH(1,1) volatility, extending the GARCH(1,1) ported from
//! `src/lib/engine/market.ts:36` with a leverage-effect asymmetry term.
//!
//! **Tier 1: no transcendentals, no RNG.** A handful of multiplies, one
//! sign compare, a clamp, and a lookup — held to bit-parity.
//!
//! # What GARCH is doing here
//!
//! Volatility CLUSTERS: a large move today makes a large move tomorrow more
//! likely, which is why real markets have quiet months and violent weeks
//! rather than uniform noise. `ALPHA` is how much yesterday's surprise feeds
//! through; `BETA` is how much of yesterday's volatility persists; `GAMMA`
//! is how much MORE a negative surprise feeds through than a positive one
//! of the same size — the leverage effect, which real equities have and a
//! symmetric GARCH structurally cannot (the return enters squared, so its
//! sign is destroyed; design finding 8, CALIBRATION.md §3.5). The effective
//! persistence is `ALPHA + BETA + GAMMA/2` (the asymmetry term is live on
//! roughly half of days) — close to 1, so shocks decay slowly, which is
//! what produces multi-week crises rather than one bad afternoon.
//!
//! GJR (Glosten–Jagannathan–Runkle) rather than EGARCH, deliberately:
//! EGARCH's log-variance form needs `exp`/`log` per name per day, which
//! would drag the volatility process out of Tier 1 and into the bit-parity
//! budget. GJR is a compare and a multiply.
//!
//! # The JS-fallback form is the contract, plus one argued divergence
//!
//! `updateGarchVariance` tries WASM first and falls back to this arithmetic.
//! `WASM-ORACLE.md` §3 establishes the two agree on all reachable inputs, and
//! decisions D1–D3 already settled that the new era takes the JS side where
//! they differ. There is no WASM in this crate, so the fallback is simply
//! what the function is — with one deliberate divergence: the reference has
//! no `GAMMA` term. The term is written as a guarded `+=` AFTER the
//! reference's three-term sum, never folded into it, so that at `GAMMA = 0`
//! the arithmetic is bit-identical to the reference form (floating-point
//! addition is not associative; a re-associated four-term sum would change
//! every trajectory even at zero). The shipped `GAMMA` is nonzero — an era
//! decision, calibrated by `tools/calibration/sweep_gjr_gamma.py` — and the
//! zero-`GAMMA` bit-identity is what keeps the term revisitable without
//! re-litigating the structure.

use crate::mathx;

/// Long-run variance weight.
pub const OMEGA: f64 = 0.000002;
/// Weight on yesterday's squared return — the surprise term, for BOTH signs.
///
/// Recalibrated from the reference's symmetric 0.09 when `GAMMA` was
/// introduced: GJR stationarity needs `ALPHA + BETA + GAMMA/2 < 1`, so the
/// asymmetry is paid for out of the symmetric coefficients. Deliberately
/// small but strictly positive — at zero, a large POSITIVE surprise would
/// not raise tomorrow's variance at all, which deletes a day-one invariant
/// (the property test below pins it).
pub const ALPHA: f64 = 0.02;
/// Weight on yesterday's variance — the persistence term.
///
/// Recalibrated from the reference's 0.90 to fund `GAMMA`; the effective
/// persistence `ALPHA + BETA + GAMMA/2` is held at the reference's 0.99, so
/// shocks decay at the same expected rate — more of the decay now travels
/// through re-excitation by realised down moves and less through the smooth
/// carry, which is measurably what raises volatility clustering into its
/// real-world band (|r| acf(1) 0.124 → 0.186).
pub const BETA: f64 = 0.80;
/// Extra weight on yesterday's squared return when the return was NEGATIVE —
/// the GJR leverage-effect term. Zero recovers the reference's symmetric
/// GARCH(1,1) bit-for-bit.
///
/// Chosen by the seventeen-point sweep in
/// `tools/calibration/results/gjr-gamma-2026-08-21.json`
/// (`tools/calibration/sweep_gjr_gamma.py`, published method, six seeds):
/// 0.34 is the smallest measured value that lands the median leverage
/// effect inside the real band of −0.30 to −0.10 (measured −0.107) while
/// keeping `ALPHA` positive. It reads large against literature GJR fits
/// (index estimates run ~0.10) because the panel statistic, not the
/// coefficient, is the target: the day's return here carries factor and
/// microstructure noise the variance process does not drive, and the
/// sector ceiling clamp truncates the biggest asymmetric responses, so a
/// larger structural asymmetry is needed to produce the same measured
/// correlation.
pub const GAMMA: f64 = 0.34;

/// Ceiling as a multiple of the sector's long-run variance.
///
/// Reduced from 10x to 5x in "Cycle 71" to limit crisis volatility. It is a
/// cap on how far a crisis can run, not a modelling refinement.
pub const CEILING_MULTIPLE: f64 = 5.0;
/// Floor as a multiple of the sector's long-run variance, so a quiet stretch
/// cannot drive volatility to zero and freeze the price.
pub const FLOOR_MULTIPLE: f64 = 0.25;

/// One day's GARCH(1,1) update.
///
/// `sector_base_variance` is the sector's long-run daily variance and sets
/// both bounds — the process mean-reverts toward its sector rather than
/// toward a single global level.
/// The GJR persistence a name's variance process must stay below.
///
/// `alpha + beta + gamma/2` at or above one is a variance that grows without
/// bound. pt-v6 sits at 0.8364, so there is real headroom, but a dispersion
/// wide enough to spend all of it would produce a name whose volatility only
/// ever climbs until a guard truncates it, which is not fat tails.
pub const GARCH_PERSISTENCE_CEILING: f64 = 0.97;

/// Reference capitalisation, in dollars, at which a name gets exactly
/// `garch_beta`. The median of `Universe.random`'s cap distribution rounds
/// near here, so the spread is roughly balanced across a generated roster.
const PERSISTENCE_REFERENCE_CAP: f64 = 2.0e10;

/// How wide a cap range the spread is drawn over, in natural logs. At 3.0 a
/// name about twenty times smaller or larger than the reference reaches the
/// bound; everything between is linear in log-cap.
const PERSISTENCE_CAP_SCALE: f64 = 3.0;

/// This name's `garch_beta`, given its size.
///
/// At `garch_beta_dispersion` of zero this returns `params.garch_beta` BY
/// BRANCH, so every preset before pt-v7 is bit-identical and owes nothing to
/// an argument about how a zero-width spread rounds. Same discipline as
/// [`crate::market::factors::cap_size_multiplier_with`] at zero smoothness.
///
/// The map is deterministic in the roster, not drawn, so the RNG stream
/// schedule does not move.
pub fn garch_beta_for(params: &crate::params::ModelParams, market_cap: f64) -> f64 {
    let d = params.garch_beta_dispersion;
    if d == 0.0 {
        return params.garch_beta;
    }
    // A non-positive cap has no size to speak of, so it gets the reference
    // persistence rather than the log of a non-positive number.
    if market_cap <= 0.0 {
        return params.garch_beta;
    }
    // A bounded, piecewise-linear map rather than a tanh. `mathx` has no
    // tanh, and adding one would mean a new transcendental with its own
    // cross-platform parity surface for a shape nothing here depends on.
    // `clamp` on the log-ratio gives the same bounded spread from primitives
    // that already exist.
    let z = mathx::clamp(
        mathx::log(market_cap / PERSISTENCE_REFERENCE_CAP) / PERSISTENCE_CAP_SCALE,
        -1.0,
        1.0,
    );
    let beta = params.garch_beta + d * z;
    // Stationarity is not negotiable: clamp against the GJR persistence,
    // and never below zero.
    let headroom =
        GARCH_PERSISTENCE_CEILING - params.garch_alpha - 0.5 * params.garch_gamma;
    mathx::max(0.0, mathx::min(beta, headroom))
}

pub fn update_garch_variance(
    current_variance: f64,
    last_daily_return: f64,
    sector_base_variance: f64,
) -> f64 {
    update_garch_variance_with(
        &crate::params::PT_V1,
        current_variance,
        last_daily_return,
        sector_base_variance,
    )
}

/// [`update_garch_variance`] under explicit model parameters (the runtime
/// seam, CALIBRATION.md §5.3). At [`crate::params::PT_V1`] this is the
/// shipped arithmetic bit for bit: same values, same operations, same
/// order — the constants above remain the definition of the preset.
pub fn update_garch_variance_with(
    params: &crate::params::ModelParams,
    current_variance: f64,
    last_daily_return: f64,
    sector_base_variance: f64,
) -> f64 {
    update_garch_variance_for(
        params,
        params.garch_beta,
        current_variance,
        last_daily_return,
        sector_base_variance,
    )
}

/// [`update_garch_variance_with`] under an explicit per-name `beta`.
///
/// The seam heterogeneous persistence needs. Passing `params.garch_beta`
/// reproduces the homogeneous arithmetic exactly, which is what the wrapper
/// above does and why no earlier preset moves.
pub fn update_garch_variance_for(
    params: &crate::params::ModelParams,
    garch_beta: f64,
    current_variance: f64,
    last_daily_return: f64,
    sector_base_variance: f64,
) -> f64 {
    let mut new_var = params.garch_omega
        + params.garch_alpha * last_daily_return * last_daily_return
        + garch_beta * current_variance;
    // The GJR asymmetry: a negative return feeds through with weight
    // ALPHA + GAMMA, a positive one with ALPHA alone. A guarded `+=` after
    // the reference sum, NOT a fourth term inside it: at GAMMA = 0 this adds
    // +0.0 to a strictly positive sum, which is bit-identical, where a
    // re-associated four-term sum would not be. The evaluation order here is
    // contractual, like every other arithmetic statement in this crate.
    if last_daily_return < 0.0 {
        new_var += params.garch_gamma * last_daily_return * last_daily_return;
    }
    // `Math.max(Math.min(newVar, ceiling), floor)` — written in that order in
    // the original, and the order is visible when the two bounds cross (a
    // zero or negative `sector_base_variance` makes them do exactly that).
    mathx::max(
        mathx::min(new_var, sector_base_variance * params.garch_ceiling_multiple),
        sector_base_variance * params.garch_floor_multiple,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A representative sector daily sigma of 1.5%, so base variance is
    /// 0.000225 — `sectorBaseDailyVariance`'s default.
    const BASE: f64 = 0.015 * 0.015;

    #[test]
    fn persistence_is_high_enough_for_crises_to_last() {
        // ALPHA + BETA + GAMMA/2 is the decay rate of a shock (the GJR term
        // is live on roughly half of days, hence the half-weight — the
        // GJR-GARCH stationarity condition for symmetric innovations). At
        // 0.99 a shock has half-decayed after ~69 days; at 0.9 it would be
        // gone in a week and the model would have no crises, only bad
        // afternoons.
        let persistence = ALPHA + BETA + GAMMA / 2.0;
        assert!(
            (0.98..1.0).contains(&persistence),
            "persistence {persistence} — below 0.98 crises evaporate, at or above 1.0 the \
             process is non-stationary and volatility runs away"
        );
    }

    #[test]
    fn a_down_day_raises_tomorrows_volatility_at_least_as_much_as_an_up_day() {
        // The GJR property, the reason the term exists. At GAMMA = 0 the two
        // sides are exactly equal — the symmetric reference, bit-for-bit —
        // and any positive GAMMA makes the down side strictly larger, which
        // is the leverage effect real equities have (design finding 8).
        let up = update_garch_variance(BASE, 0.02, BASE);
        let down = update_garch_variance(BASE, -0.02, BASE);
        assert!(
            down >= up,
            "a −2% day must raise variance at least as much as a +2% day: {down} vs {up}"
        );
        if GAMMA > 0.0 {
            assert!(
                down > up,
                "with GAMMA = {GAMMA} a −2% day must raise variance STRICTLY more \
                 than a +2% day: {down} vs {up}"
            );
        } else {
            assert!(
                down == up,
                "at GAMMA = 0 the update must be exactly the symmetric reference: \
                 {down} vs {up}"
            );
        }
    }

    // ── INVARIANTS 2.7 / 5.5: volatility clustering ───────────────────────

    #[test]
    fn a_large_return_raises_tomorrows_volatility() {
        // The defining property. Named as a day-one property test in
        // INVARIANTS and, until now, written nowhere.
        let calm = update_garch_variance(BASE, 0.001, BASE);
        let shocked = update_garch_variance(BASE, 0.08, BASE);
        assert!(
            shocked > calm,
            "an 8% day must raise variance above a 0.1% day: {shocked} vs {calm}"
        );
    }

    #[test]
    fn volatility_clusters_rather_than_resetting() {
        // A shock must still be visible days later — that is what
        // "clustering" means, as opposed to a one-day spike.
        let mut shocked = update_garch_variance(BASE, 0.08, BASE);
        let quiet_after_shock = {
            // Five quiet days FOLLOWING the shock.
            for _ in 0..5 {
                shocked = update_garch_variance(shocked, 0.0, BASE);
            }
            shocked
        };
        let never_shocked = {
            let mut v = BASE;
            for _ in 0..6 {
                v = update_garch_variance(v, 0.0, BASE);
            }
            v
        };
        assert!(
            quiet_after_shock > never_shocked * 1.2,
            "five days after an 8% move, variance must still be elevated: {quiet_after_shock} \
             vs {never_shocked}"
        );
    }

    #[test]
    fn the_shock_does_eventually_decay() {
        // Clustering that never decays is a runaway, not a cluster.
        let mut v = update_garch_variance(BASE, 0.15, BASE);
        for _ in 0..400 {
            v = update_garch_variance(v, 0.0, BASE);
        }
        assert!(
            v <= BASE * FLOOR_MULTIPLE * 1.000001,
            "after 400 quiet days variance should have fallen to its floor, got {v}"
        );
    }

    #[test]
    fn variance_stays_inside_its_sector_bounds_however_hard_it_is_driven() {
        let mut v = BASE;
        for day in 0..1000 {
            // Alternating extreme and zero returns — the worst case for a
            // process with 0.99 persistence.
            let ret = if day % 2 == 0 { 0.9 } else { 0.0 };
            v = update_garch_variance(v, ret, BASE);
            assert!(
                v <= BASE * CEILING_MULTIPLE * 1.000001,
                "day {day}: variance {v} escaped its {CEILING_MULTIPLE}x ceiling"
            );
            assert!(
                v >= BASE * FLOOR_MULTIPLE * 0.999999,
                "day {day}: variance {v} fell through its {FLOOR_MULTIPLE}x floor"
            );
        }
    }

    #[test]
    fn a_higher_volatility_sector_carries_a_higher_floor_and_ceiling() {
        // The bounds are per-sector, so a utility and a biotech do not
        // converge on the same volatility after a long quiet stretch.
        let calm_sector = 0.008 * 0.008;
        let wild_sector = 0.035 * 0.035;
        let settle = |base: f64| {
            let mut v = base;
            for _ in 0..500 {
                v = update_garch_variance(v, 0.0, base);
            }
            v
        };
        assert!(settle(wild_sector) > settle(calm_sector));
    }

    #[test]
    fn the_bounds_are_applied_max_of_min_and_the_order_shows() {
        // With a non-positive base variance the ceiling falls below the
        // floor, and `max(min(x, hi), lo)` then returns `lo` for every input.
        // Not reachable from the live tick loop, but it is what the original does and
        // the two orderings disagree here.
        assert_eq!(update_garch_variance(0.01, 0.5, 0.0), 0.0);
        for v in [0.0, 0.001, 1.0] {
            assert_eq!(update_garch_variance(v, 0.0, 0.0), 0.0);
        }
    }
}
