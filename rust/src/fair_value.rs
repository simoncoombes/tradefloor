//! Fair value — Layer 2 of the stationary price decomposition.
//!
//! Ported from the reference implementation's fair-value module.
//!
//! # Why this module is held to a stricter standard than the rest
//!
//! The reference implementation's fair-value module contains **zero
//! transcendental calls** — `Math.max` and
//! ordinary arithmetic only. Every operation is exactly specified by IEEE-754,
//! so unlike the mispricing path (where Chrome, Firefox and Safari disagree
//! with each other about `Math.cos`; see the determinism notes) this
//! module can and must be **bit-identical** to the reference implementation.
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
/// The reference implementation writes `sectorConfig?.avgPe || 18`. That `||` is
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
    /// truth for numbers the reference implementation already owns.
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
    /// Fed securities held outright over a neutral baseline. `1.0` is
    /// neutral and contributes nothing; `None` means the caller does not
    /// model the stock and is treated as neutral. Consumed only by the
    /// stock channel (`qe_pe_stock_gain`), which every preset before
    /// pt-v16-era work ships at 0.0.
    pub qe_assets_ratio: Option<f64>,
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

/// The STOCK channel of QE valuation: concave in the level of holdings.
///
/// The flow channel above it is linear in monthly purchases and measured
/// against a proxy; fed the real series it overshoots (the 2020 peak reads
/// 1.29 on a scale built for 0.10). The portfolio-balance literature puts
/// the valuation effect on the STOCK of holdings, with diminishing returns,
/// so this term is `gain * ln(holdings / baseline)`: zero at the neutral
/// baseline, concave above it, and symmetric-in-log below. Gain 0.0 -- every
/// preset today -- contributes literal `+0.0`, which is bit-inert.
fn qe_stock_term(gain: f64, ratio: Option<f64>) -> f64 {
    if gain == 0.0 {
        return 0.0;
    }
    match ratio {
        // Floored away from zero so a malformed ratio cannot mint a NaN
        // that would freeze every book downstream.
        Some(r) => gain * mathx::log(mathx::max(r, 1e-6)),
        None => 0.0,
    }
}

/// Company-specific target PE: anchor x rate adjustment x QE adjustment.
pub fn compute_target_pe(
    company: &CompanyValuationInputs,
    economy: &EconomyValuationInputs,
    qe_gain: f64,
    qe_stock_gain: f64,
    neutral_rate: f64,
) -> TargetPe {
    let sector_anchor_pe = sector_anchor_pe(company.sector_avg_pe);
    let discount = discount_rate(economy);

    // Negative growth clamps to zero, so duration never falls below 1 — a
    // shrinking company is not *less* rate-sensitive than a flat one.
    let revenue_growth = company.revenue_growth.unwrap_or(0.0);
    let duration_multiplier = 1.0 + mathx::max(0.0, revenue_growth) * GROWTH_DURATION_SCALE;

    let rate_adjustment = mathx::max(
        RATE_ADJUSTMENT_FLOOR,
        1.0 - (discount - neutral_rate) * RATE_PE_SENSITIVITY * duration_multiplier,
    );

    // `qe_pe_gain` is 1.0 on every preset before it, so this is bit-inert
    // there. It exists because this is the only macro channel in the model
    // with no gain between input and response, and round 76 measured it
    // carrying more of the driven-window excess than the VIX channel does.
    let qe_adjustment = 1.0 + qe_gain * economy.qe_pe_boost.unwrap_or(0.0)
        + qe_stock_term(qe_stock_gain, economy.qe_assets_ratio);

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
    compute_fair_value_with(company, economy, 0.0, 1.0, 0.0, NEUTRAL_DISCOUNT_RATE)
}

/// [`compute_fair_value`] with the book floor extended to profitable companies.
///
/// `book_floor` is `ModelParams::fair_value_book_floor`. At 0.0 this is exactly
/// the function above and the reference implementation's behaviour; `fair_value_parity` calls
/// the two-argument form and is untouched.
///
/// # The discontinuity this exists to close
///
/// The reference switches hard at `eps > 0`. Measured, book value 20.00 and a
/// transportation anchor:
///
/// | eps | fair value |
/// |---|---|
/// | 4.30 | 64.07 |
/// | 1.00 | 14.90 |
/// | 0.00 | 24.00 |
/// | -19.49 | 24.00 |
///
/// Fair value FALLS to 14.90 and then JUMPS UP to 24.00 as earnings cross into
/// loss, so a barely profitable company is worth less than a bankrupt one with
/// the same book. Today that is close to harmless, because `eps` is fixed when
/// an instrument is built and nothing walks it through zero. A time-varying
/// earnings path -- an airline going 4.30 to -19.49 across 2020 -- drives
/// straight through it, and the price chases a fair value that dips and then
/// inverts.
///
/// At 1.0 the floor applies on both sides, so fair value is continuous at zero
/// and non-decreasing in earnings.
///
/// # Why it is off by default
///
/// It is not a small correction. 46.7% of instruments from
/// `Universe.random(4000, seed=7)` have `eps * pe` BELOW
/// `book * LOSS_MAKING_PRICE_TO_BOOK`, some at a fifth of
/// it, so switching it on re-values a large part of a typical universe and
/// re-bases every calibrated statistic. Adopting it is an era boundary and a
/// recalibration, not a bug fix, and it ships inert until that work is done.
///
/// That figure was 42.8% before the universe generator reconciled a drawn
/// roster to its own fair value, and it named no roster to be re-measured
/// on. It is measured here on the opening macro state at the commit that
/// reconciled the generator; a single 40-name roster reads a long way from
/// it either side, so the large draw is the one to quote.
pub fn compute_fair_value_with(
    company: &CompanyValuationInputs,
    economy: &EconomyValuationInputs,
    book_floor: f64,
    qe_gain: f64,
    qe_stock_gain: f64,
    neutral_rate: f64,
) -> FairValueBreakdown {
    let eps = company.eps.unwrap_or(0.0);

    if eps > 0.0 {
        let pe = compute_target_pe(company, economy, qe_gain, qe_stock_gain, neutral_rate);
        // The floor is a BRANCH at zero, not arithmetic, for the same reason
        // `market_vol_slow_weight` is: every preset before this parameter
        // existed must reproduce bit for bit, and that is the only spelling
        // that owes nothing to an argument about how floats behave.
        let earnings_value = mathx::max(FAIR_VALUE_FLOOR, eps * pe.target_pe);
        let fair_value = if book_floor == 0.0 {
            earnings_value
        } else {
            let floor = company.book_value_per_share.unwrap_or(0.0)
                * LOSS_MAKING_PRICE_TO_BOOK
                * book_floor;
            mathx::max(earnings_value, floor)
        };
        return FairValueBreakdown {
            fair_value,
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
        // the reference implementation's behaviour and callers may read it, so it is
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
            qe_assets_ratio: None,
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
    fn the_qe_gain_is_inert_at_one_and_scales_the_channel() {
        let c = co(Some(20.0), Some(1.0), None, Some(0.1));
        let mut e = econ(Some(10.0), 3.5, None);
        e.qe_pe_boost = Some(0.20);

        // 1.0 is what every preset before the dial carried, and the whole
        // channel is `1 + gain * boost`, so this must be bit-identical to
        // the arithmetic it replaced.
        let shipped = compute_target_pe(&c, &e, 1.0, 0.0, NEUTRAL_DISCOUNT_RATE);
        assert_eq!(shipped.qe_adjustment.to_bits(), (1.20f64).to_bits());

        // Half the gain is half the boost.
        assert_eq!(compute_target_pe(&c, &e, 0.5, 0.0, NEUTRAL_DISCOUNT_RATE).qe_adjustment.to_bits(),
                   (1.10f64).to_bits());
        // Zero removes the channel without removing the valuation.
        let off = compute_target_pe(&c, &e, 0.0, 0.0, NEUTRAL_DISCOUNT_RATE);
        assert_eq!(off.qe_adjustment.to_bits(), (1.0f64).to_bits());
        assert!(off.target_pe > 0.0);
    }

    /// The stock channel: off is bit-inert whatever the ratio says, the
    /// neutral ratio contributes nothing at any gain, and the response is
    /// concave -- doubling holdings adds less the second time.
    #[test]
    fn qe_stock_channel_is_inert_off_and_concave_on() {
        let base = econ(Some(10.0), 3.5, Some(0.05));
        let mut high = base.clone();
        high.qe_assets_ratio = Some(2.2);
        let c = co(Some(20.0), Some(4.0), None, Some(0.1));

        // gain 0.0: the ratio cannot matter, to the bit
        let off_base = compute_target_pe(&c, &base, 1.0, 0.0, NEUTRAL_DISCOUNT_RATE);
        let off_high = compute_target_pe(&c, &high, 1.0, 0.0, NEUTRAL_DISCOUNT_RATE);
        assert_eq!(off_base.qe_adjustment.to_bits(), off_high.qe_adjustment.to_bits());

        // neutral ratio: no contribution at any gain
        let mut neutral = base.clone();
        neutral.qe_assets_ratio = Some(1.0);
        assert_eq!(
            compute_target_pe(&c, &neutral, 1.0, 0.13, NEUTRAL_DISCOUNT_RATE).qe_adjustment.to_bits(),
            off_base.qe_adjustment.to_bits(),
        );

        // concave: 1.0 -> 2.0 adds more than 2.0 -> 4.0... no: ln doubles
        // equally per doubling; concavity in the LEVEL: equal increments of
        // ratio add less and less.
        let term = |r: f64| {
            let mut e = base.clone();
            e.qe_assets_ratio = Some(r);
            compute_target_pe(&c, &e, 1.0, 0.13, NEUTRAL_DISCOUNT_RATE).qe_adjustment
        };
        let d1 = term(2.0) - term(1.0);
        let d2 = term(3.0) - term(2.0);
        assert!(d1 > d2 && d2 > 0.0, "equal level steps must add less: {d1} then {d2}");
    }

    #[test]
    fn negative_growth_does_not_shrink_duration() {
        let shrinking = compute_target_pe(
            &co(Some(20.0), Some(1.0), None, Some(-0.9)),
            &econ(Some(10.0), 3.5, None),
            1.0,
            0.0,
            NEUTRAL_DISCOUNT_RATE,
        );
        let flat = compute_target_pe(
            &co(Some(20.0), Some(1.0), None, Some(0.0)),
            &econ(Some(10.0), 3.5, None),
            1.0,
            0.0,
            NEUTRAL_DISCOUNT_RATE,
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
            1.0,
            0.0,
            NEUTRAL_DISCOUNT_RATE,
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
