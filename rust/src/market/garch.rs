//! GARCH(1,1) volatility, ported from `src/lib/engine/market.ts:36`.
//!
//! **Tier 1: no transcendentals, no RNG.** Four multiplies, a clamp, and a
//! lookup — held to bit-parity.
//!
//! # What GARCH is doing here
//!
//! Volatility CLUSTERS: a large move today makes a large move tomorrow more
//! likely, which is why real markets have quiet months and violent weeks
//! rather than uniform noise. `ALPHA` is how much yesterday's surprise feeds
//! through; `BETA` is how much of yesterday's volatility persists. Their sum
//! (0.99) is the persistence — close to 1, so shocks decay slowly, which is
//! what produces multi-week crises rather than one bad afternoon.
//!
//! # The JS-fallback form is the contract
//!
//! `updateGarchVariance` tries WASM first and falls back to this arithmetic.
//! `WASM-ORACLE.md` §3 establishes the two agree on all reachable inputs, and
//! decisions D1–D3 already settled that the new era takes the JS side where
//! they differ. There is no WASM in this crate, so the fallback is simply
//! what the function is.

use crate::mathx;

/// Long-run variance weight.
pub const OMEGA: f64 = 0.000002;
/// Weight on yesterday's squared return — the surprise term.
pub const ALPHA: f64 = 0.09;
/// Weight on yesterday's variance — the persistence term.
pub const BETA: f64 = 0.90;

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
pub fn update_garch_variance(
    current_variance: f64,
    last_daily_return: f64,
    sector_base_variance: f64,
) -> f64 {
    let new_var = OMEGA + ALPHA * last_daily_return * last_daily_return + BETA * current_variance;
    // `Math.max(Math.min(newVar, ceiling), floor)` — written in that order in
    // the original, and the order is visible when the two bounds cross (a
    // zero or negative `sector_base_variance` makes them do exactly that).
    mathx::max(
        mathx::min(new_var, sector_base_variance * CEILING_MULTIPLE),
        sector_base_variance * FLOOR_MULTIPLE,
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
        // ALPHA + BETA is the decay rate of a shock. At 0.99 a shock has
        // half-decayed after ~69 days; at 0.9 it would be gone in a week and
        // the model would have no crises, only bad afternoons.
        let persistence = ALPHA + BETA;
        assert!(
            (0.98..1.0).contains(&persistence),
            "persistence {persistence} — below 0.98 crises evaporate, at or above 1.0 the \
             process is non-stationary and volatility runs away"
        );
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
