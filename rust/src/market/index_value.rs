//! Market-index arithmetic, ported from `src/lib/engine/market.ts:750`.
//!
//! **Tier 1: division and comparison only.**
//!
//! # Why there is no rate-of-change limit
//!
//! An index is a DERIVED quantity: its value is whatever its constituents'
//! market caps imply, and nothing else. There used to be a ±0.5%/tick clamp
//! here and removing it was a bug fix, not a relaxation — the failure it
//! caused is worth keeping in view, because it is the most expensive kind
//! of feedback loop this engine has produced.
//!
//! Clamping made the published value lag the true aggregate. The divisor
//! recalibration in `updateMarketIndices` then read that lag as a change in
//! index COMPOSITION and adjusted the divisor to compensate — which moved the
//! published value further from the aggregate, which looked like more
//! composition change. Measured on seed 31337 over one simulated year:
//! constituent market cap ended at 0.909x with all 50 companies alive and
//! zero bankruptcies, while the published index read **0.028x** and the
//! divisor had inflated **32.9x**.
//!
//! Under `STATIONARY_PRICE_MODEL` there is no per-tick constituent clamp
//! either: names are bounded only by the ±25% session breaker, so an earnings
//! gap legitimately moves a constituent — and the index — in a single tick.
//! That is designed behaviour and the divisor logic does not depend on any
//! per-tick bound.

/// The value, absolute change and percentage change of an index.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct IndexValue {
    pub value: f64,
    pub change: f64,
    pub change_percent: f64,
}

/// One constituent, as the index reads it.
#[derive(Debug, Clone, PartialEq)]
pub struct IndexConstituent {
    pub id: String,
    pub market_cap: f64,
    pub is_bankrupt: bool,
}

/// Recompute an index from its constituents.
///
/// Returns the PREVIOUS value unchanged on every degenerate input — an empty
/// or wholly-bankrupt roster, a non-positive divisor, or a ratio that is not
/// finite and positive. Stalling is the correct failure here: an index that
/// lurches to zero on a divisor glitch is worse than one that holds its last
/// good print.
pub fn calculate_market_index(
    companies: &[IndexConstituent],
    component_ids: &[String],
    previous_value: f64,
    divisor: f64,
) -> IndexValue {
    let mut total_market_cap = 0.0;
    for company in companies {
        if !component_ids.iter().any(|id| id == &company.id) {
            continue;
        }
        // Bankrupt names leave the index rather than contributing a stale cap.
        if company.is_bankrupt {
            continue;
        }
        total_market_cap += company.market_cap;
    }

    let unchanged = IndexValue {
        value: previous_value,
        change: 0.0,
        change_percent: 0.0,
    };

    // `divisor <= 0` rather than `< 0` is what the original writes, and the
    // two are EQUIVALENT here — mutation-verified. A divisor of exactly zero
    // (or `-0`) makes the ratio infinite, which the `is_finite` guard below
    // rejects anyway, and a zero total cap is already handled by the first
    // clause. The `<=` is kept because it is what the source says and because
    // it states the intent locally rather than relying on a later guard.
    if total_market_cap == 0.0 || divisor <= 0.0 {
        return unchanged;
    }

    let raw_value = total_market_cap / divisor;
    // `!Number.isFinite(x) || x <= 0` — the negation catches NaN, which a
    // `>` test alone would let through.
    if !raw_value.is_finite() || raw_value <= 0.0 {
        return unchanged;
    }

    let change = raw_value - previous_value;
    // Guarded on the PREVIOUS value, not the new one, so the first print of a
    // fresh index reports 0% rather than infinity.
    let change_percent = if previous_value > 0.0 {
        (change / previous_value) * 100.0
    } else {
        0.0
    };

    IndexValue {
        value: raw_value,
        change,
        change_percent,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn c(id: &str, cap: f64, bankrupt: bool) -> IndexConstituent {
        IndexConstituent {
            id: id.to_string(),
            market_cap: cap,
            is_bankrupt: bankrupt,
        }
    }

    fn ids(names: &[&str]) -> Vec<String> {
        names.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn the_index_is_the_aggregate_cap_over_the_divisor() {
        let roster = vec![c("a", 1e11, false), c("b", 5e10, false)];
        let out = calculate_market_index(&roster, &ids(&["a", "b"]), 100.0, 1e9);
        assert_eq!(out.value, 150.0);
        assert_eq!(out.change, 50.0);
        assert_eq!(out.change_percent, 50.0);
    }

    #[test]
    fn bankrupt_constituents_leave_the_index() {
        let roster = vec![c("a", 1e11, false), c("b", 5e10, true)];
        let out = calculate_market_index(&roster, &ids(&["a", "b"]), 100.0, 1e9);
        assert_eq!(
            out.value, 100.0,
            "the bankrupt name must not contribute its cap"
        );
    }

    #[test]
    fn non_constituents_are_ignored_however_large() {
        let roster = vec![c("a", 1e11, false), c("whale", 9e15, false)];
        let out = calculate_market_index(&roster, &ids(&["a"]), 100.0, 1e9);
        assert_eq!(out.value, 100.0);
    }

    #[test]
    fn every_degenerate_input_holds_the_previous_value() {
        let roster = vec![c("a", 1e11, false)];
        let all = ids(&["a"]);
        for (note, companies, divisor) in [
            ("zero divisor", roster.clone(), 0.0),
            ("negative divisor", roster.clone(), -5.0),
            ("NaN divisor", roster.clone(), f64::NAN),
            ("empty roster", vec![], 1e9),
            ("all bankrupt", vec![c("a", 1e11, true)], 1e9),
            ("zero cap", vec![c("a", 0.0, false)], 1e9),
        ] {
            let out = calculate_market_index(&companies, &all, 4000.0, divisor);
            assert_eq!(out.value, 4000.0, "{note}: must hold the previous value");
            assert_eq!(out.change, 0.0, "{note}");
            assert_eq!(out.change_percent, 0.0, "{note}");
        }
    }

    #[test]
    fn a_divisor_small_enough_to_overflow_holds_rather_than_printing_infinity() {
        // `1e11 / 1e-300` overflows to +inf, and an infinite index is worse
        // than a stale one.
        let roster = vec![c("a", 1e11, false)];
        let out = calculate_market_index(&roster, &ids(&["a"]), 4000.0, 1e-300);
        assert_eq!(out.value, 4000.0);
    }

    #[test]
    fn a_non_positive_previous_value_reports_zero_percent_rather_than_infinity() {
        let roster = vec![c("a", 1e11, false)];
        for previous in [0.0, -1.0] {
            let out = calculate_market_index(&roster, &ids(&["a"]), previous, 1e9);
            assert_eq!(out.change_percent, 0.0);
            // The VALUE is still published — only the percentage is suppressed.
            assert_eq!(out.value, 100.0);
        }
    }

    #[test]
    fn there_is_no_rate_of_change_limit() {
        // The removed clamp, asserted absent. A constituent that gaps on
        // earnings must move the index in one tick — see the module header
        // for what reintroducing a limit cost last time.
        let calm = vec![c("a", 1e11, false)];
        let gapped = vec![c("a", 5e11, false)];
        let before = calculate_market_index(&calm, &ids(&["a"]), 100.0, 1e9);
        let after = calculate_market_index(&gapped, &ids(&["a"]), before.value, 1e9);
        assert_eq!(after.value, 500.0, "a 5x cap move must print in full");
        assert_eq!(after.change_percent, 400.0);
    }
}
