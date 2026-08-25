//! Fair value — Layer 2 of the stationary price decomposition.
//!
//! Ported from `src/lib/engine/fairValue.ts`.
//!
//! # Why this module is held to a stricter standard than the rest
//!
//! `fairValue.ts` contains **zero transcendental calls** — `Math.max` and
//! ordinary arithmetic only. Every operation is exactly specified by IEEE-754,
//! so unlike the mispricing path (where Chrome, Firefox and Safari disagree
//! with each other about `Math.cos`; see the determinism notes) this
//! module can and must be **bit-identical** to the TypeScript.
//!
//! `tests/fair_value_parity.rs` therefore gates on exact equality across 250
//! recorded cases. Any mismatch is a defect here, not a rounding disagreement.
//!
//! # The conserved quantity
//!
//! The original module comment explains the design and is worth preserving in
//! substance: the old price layer integrated fourteen forces with no conserved
//! quantity, which is why no amount of coefficient tuning stabilised it. Fair
//! value is a **pure function** of current fundamentals and macro state —
//! recomputed, never integrated — so it cannot drift from fundamentals by
//! construction. Price is then `fairValue * exp(s)` with `s` stationary.
//!
//! Purity is not decoration here; it is the property the whole model rests on,
//! and it is why this struct holds no state and the functions take everything
//! they need as arguments.

/// Corporate bond yield at which `targetPE` sits exactly on its sector anchor.
use crate::mathx;

pub const NEUTRAL_DISCOUNT_RATE: f64 = 0.04;

/// PE compression per unit of discount rate above neutral.
pub const RATE_PE_SENSITIVITY: f64 = 1.5;

/// Floor on the rate adjustment, so extreme yields cannot zero a valuation.
pub const RATE_ADJUSTMENT_FLOOR: f64 = 0.5;

/// How much an earnings-growth premium extends duration.
///
/// Growth companies discount more value from far-future cash flows, so the
/// same rate move compresses their multiple more.
pub const GROWTH_DURATION_SCALE: f64 = 2.0;

/// Valuation anchor for loss-making companies, as a multiple of book value.
pub const LOSS_MAKING_PRICE_TO_BOOK: f64 = 1.2;

/// Absolute floor, so a degenerate balance sheet yields a positive finite
/// value rather than a NaN. Delisting is the engine's job, not arithmetic's.
pub const FAIR_VALUE_FLOOR: f64 = 0.01;

/// Fallback sector anchor.
///
/// The TypeScript writes `sectorConfig?.avgPe || 18`. That `||` is
/// **truthiness**, not a null check — see [`sector_anchor_pe`].
pub const DEFAULT_SECTOR_ANCHOR_PE: f64 = 18.0;

/// The exactly four company fields the valuation reads.
///
/// Deliberately not a port of the whole `Company` type. `computeFairValue`
/// touches `sector`, `financials.eps`, `financials.bookValuePerShare` and
/// `financials.revenueGrowth` and nothing else, so widening this struct would
/// invent coupling the original does not have.
///
/// `Option` mirrors JavaScript's optional-chaining: `None` is the `undefined`
/// that `?? 0` turns into zero.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CompanyValuationInputs {
    /// The sector's average PE, already looked up from `SECTOR_CONFIGS`.
    ///
    /// Passed in rather than looked up here so this crate does not have to
    /// own a copy of the sector table, which would be a second source of
    /// truth for numbers the TypeScript already owns.
    pub sector_avg_pe: Option<f64>,
    pub eps: Option<f64>,
    pub book_value_per_share: Option<f64>,
    pub revenue_growth: Option<f64>,
}

/// The three economy fields the valuation reads.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EconomyValuationInputs {
    /// `None` means the field is absent, and the policy rate is used instead.
    /// A `Some(0.0)` is a real zero yield and MUST be used — see
    /// [`discount_rate`].
    pub corporate_bond_yield: Option<f64>,
    pub federal_funds_rate: f64,
    pub qe_pe_boost: Option<f64>,
}

/// The decomposition, as the attribution UI renders it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct FairValueBreakdown {
    pub fair_value: f64,
    /// The multiple actually applied. Zero on the book-value path.
    pub target_pe: f64,
    pub sector_anchor_pe: f64,
    pub rate_adjustment: f64,
    pub qe_adjustment: f64,
    /// True when valued off book because EPS <= 0.
    pub book_value_path: bool,
}

/// Just the multiple and its parts.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct TargetPe {
    pub target_pe: f64,
    pub sector_anchor_pe: f64,
    pub rate_adjustment: f64,
    pub qe_adjustment: f64,
}

/// `sectorConfig?.avgPe || 18` — **truthiness**, deliberately.
///
/// In JavaScript `||` falls back on any falsy value, so an `avgPe` of `0`
/// yields 18, and so does `NaN`. A Rust `unwrap_or(18.0)` would keep the zero
/// and silently value every company in that sector at nothing.
///
/// Ten lines below, [`discount_rate`] uses `??`, which does the opposite. The
/// two coercions sit in the same function in the original and mean different
/// things; conflating them is the single most likely way to get this module
/// subtly wrong.
fn sector_anchor_pe(avg_pe: Option<f64>) -> f64 {
    match avg_pe {
        Some(pe) if pe != 0.0 && !pe.is_nan() => pe,
        _ => DEFAULT_SECTOR_ANCHOR_PE,
    }
}

/// `economy.corporateBondYield ?? economy.federalFundsRate` — **nullish**.
///
/// Only absence falls through. A corporate bond yield of exactly `0.0` is a
/// real observation and is used as-is, which is the opposite of
/// [`sector_anchor_pe`]'s behaviour on zero.
fn discount_rate(economy: &EconomyValuationInputs) -> f64 {
    let yield_pct = economy
        .corporate_bond_yield
        .unwrap_or(economy.federal_funds_rate);
    yield_pct / 100.0
}

/// Company-specific target PE: anchor x rate adjustment x QE adjustment.
pub fn compute_target_pe(
    company: &CompanyValuationInputs,
    economy: &EconomyValuationInputs,
) -> TargetPe {
    let sector_anchor_pe = sector_anchor_pe(company.sector_avg_pe);
    let discount = discount_rate(economy);

    // Negative growth clamps to zero, so duration never falls below 1 — a
    // shrinking company is not *less* rate-sensitive than a flat one.
    let revenue_growth = company.revenue_growth.unwrap_or(0.0);
    let duration_multiplier = 1.0 + mathx::max(0.0, revenue_growth) * GROWTH_DURATION_SCALE;

    let rate_adjustment = mathx::max(
        RATE_ADJUSTMENT_FLOOR,
        1.0 - (discount - NEUTRAL_DISCOUNT_RATE) * RATE_PE_SENSITIVITY * duration_multiplier,
    );

    let qe_adjustment = 1.0 + economy.qe_pe_boost.unwrap_or(0.0);

    TargetPe {
        target_pe: sector_anchor_pe * rate_adjustment * qe_adjustment,
        sector_anchor_pe,
        rate_adjustment,
        qe_adjustment,
    }
}

/// Fair value per share, with its decomposition.
///
/// Two paths. Profitable companies are valued on earnings times the target
/// multiple. Loss-makers are valued off book — which closes a real gap in the
/// old model, where an unprofitable company had no fundamental anchor at all,
/// precisely when it was most distressed.
pub fn compute_fair_value(
    company: &CompanyValuationInputs,
    economy: &EconomyValuationInputs,
) -> FairValueBreakdown {
    let eps = company.eps.unwrap_or(0.0);

    if eps > 0.0 {
        let pe = compute_target_pe(company, economy);
        return FairValueBreakdown {
            fair_value: mathx::max(FAIR_VALUE_FLOOR, eps * pe.target_pe),
            target_pe: pe.target_pe,
            sector_anchor_pe: pe.sector_anchor_pe,
            rate_adjustment: pe.rate_adjustment,
            qe_adjustment: pe.qe_adjustment,
            book_value_path: false,
        };
    }

    let book_value = company.book_value_per_share.unwrap_or(0.0);
    FairValueBreakdown {
        fair_value: mathx::max(FAIR_VALUE_FLOOR, book_value * LOSS_MAKING_PRICE_TO_BOOK),
        target_pe: 0.0,
        // Note: the anchor is still reported on the book path, but the rate
        // and QE adjustments are hardcoded to 1 rather than computed. That is
        // the TypeScript's behaviour and callers may read it, so it is
        // reproduced rather than "improved" into a full breakdown.
        sector_anchor_pe: sector_anchor_pe(company.sector_avg_pe),
        rate_adjustment: 1.0,
        qe_adjustment: 1.0,
        book_value_path: true,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn econ(y: Option<f64>, fed: f64, qe: Option<f64>) -> EconomyValuationInputs {
        EconomyValuationInputs {
            corporate_bond_yield: y,
            federal_funds_rate: fed,
            qe_pe_boost: qe,
        }
    }
    fn co(
        pe: Option<f64>,
        eps: Option<f64>,
        book: Option<f64>,
        g: Option<f64>,
    ) -> CompanyValuationInputs {
        CompanyValuationInputs {
            sector_avg_pe: pe,
            eps,
            book_value_per_share: book,
            revenue_growth: g,
        }
    }

    /// TRAP 1: `||` is truthiness — a zero anchor becomes 18, not zero.
    #[test]
    fn zero_sector_anchor_falls_back_to_default() {
        assert_eq!(sector_anchor_pe(Some(0.0)), DEFAULT_SECTOR_ANCHOR_PE);
        assert_eq!(sector_anchor_pe(None), DEFAULT_SECTOR_ANCHOR_PE);
        assert_eq!(sector_anchor_pe(Some(f64::NAN)), DEFAULT_SECTOR_ANCHOR_PE);
        assert_eq!(sector_anchor_pe(Some(32.0)), 32.0);
    }

    /// TRAP 2: `??` is nullish — a zero yield is REAL and must be kept.
    ///
    /// The mirror image of trap 1, in the same function.
    #[test]
    fn zero_bond_yield_is_kept_not_replaced_by_the_policy_rate() {
        let kept = discount_rate(&econ(Some(0.0), 9.0, None));
        assert_eq!(kept, 0.0, "a 0% yield must be used, not treated as absent");

        let fell_through = discount_rate(&econ(None, 9.0, None));
        assert_eq!(
            fell_through, 0.09,
            "an absent yield falls through to fedRate"
        );
    }

    /// TRAP 3: negative growth clamps, so duration stays at 1.
    #[test]
    fn negative_growth_does_not_shrink_duration() {
        let shrinking = compute_target_pe(
            &co(Some(20.0), Some(1.0), None, Some(-0.9)),
            &econ(Some(10.0), 3.5, None),
        );
        let flat = compute_target_pe(
            &co(Some(20.0), Some(1.0), None, Some(0.0)),
            &econ(Some(10.0), 3.5, None),
        );
        assert_eq!(
            shrinking.rate_adjustment.to_bits(),
            flat.rate_adjustment.to_bits()
        );
    }

    #[test]
    fn rate_adjustment_never_falls_below_its_floor() {
        let extreme = compute_target_pe(
            &co(Some(32.0), Some(4.0), None, Some(0.5)),
            &econ(Some(40.0), 3.5, None),
        );
        assert_eq!(extreme.rate_adjustment, RATE_ADJUSTMENT_FLOOR);
    }

    #[test]
    fn loss_makers_take_the_book_path() {
        for eps in [0.0, -0.01, -5.0] {
            let fv = compute_fair_value(
                &co(Some(24.0), Some(eps), Some(10.0), None),
                &econ(Some(4.0), 3.5, None),
            );
            assert!(fv.book_value_path);
            assert_eq!(fv.target_pe, 0.0);
            assert_eq!(fv.fair_value, 12.0, "10 x 1.2");
        }
    }

    #[test]
    fn the_floor_binds_on_both_paths() {
        let degenerate_book = compute_fair_value(
            &co(Some(24.0), Some(-1.0), Some(-50.0), None),
            &econ(Some(4.0), 3.5, None),
        );
        assert_eq!(degenerate_book.fair_value, FAIR_VALUE_FLOOR);

        let tiny_eps = compute_fair_value(
            &co(Some(14.0), Some(1e-12), Some(5.0), None),
            &econ(Some(4.0), 3.5, None),
        );
        assert_eq!(tiny_eps.fair_value, FAIR_VALUE_FLOOR);
    }

    /// Purity is the property the whole decomposition rests on: same inputs,
    /// same output, no hidden state, no ordering effect.
    #[test]
    fn is_a_pure_function() {
        let c = co(Some(32.0), Some(4.2), Some(31.7), Some(0.15));
        let e = econ(Some(4.0), 3.5, Some(0.05));
        let first = compute_fair_value(&c, &e);
        for _ in 0..100 {
            assert_eq!(compute_fair_value(&c, &e), first);
        }
    }
}
