//! The daily lifecycle, ported from `src/lib/stores/tick/transitions.ts`
//! and `market.ts:1708`.
//!
//! # These are state transitions, not glue
//!
//! They live outside `market.ts` in the TypeScript, which makes them easy to
//! mistake for store plumbing. They are not. Two of them are what make the
//! `s`-process and GARCH behave across DAYS rather than within one:
//!
//! - **The momentum roll.** `mispricingMomentum = s − sPrevClose`, then
//!   `sPrevClose = s`. Yesterday's re-rating becomes today's herding input —
//!   the `θ` term in the AR(2). Without it `MOMENTUM_THETA` multiplies a value
//!   that never changes, the process collapses to AR(1), and every claim in
//!   `mispricing.rs` about the characteristic roots stops applying.
//! - **The GARCH innovation.** The variance update is fed the day's
//!   accumulated `randomNoise`, NOT the day's total return. Those differ:
//!   the total return includes fair-value moves, and feeding those back into
//!   the volatility model would make an earnings gap look like a volatility
//!   regime change.
//!
//! A Rust engine that ran 390 ticks without these would produce a plausible
//! single day and a wrong week.
//!
//! # Draws
//!
//! **Zero.** Every function here is a pure state transition. That is worth
//! asserting rather than assuming, because a draw hidden in the close would
//! desynchronise the stream once per simulated day — slowly enough to look
//! like a modelling difference rather than a bug.

use super::garch::update_garch_variance;
use super::tick::TickCompany;
use crate::mathx;

/// Rolling-average-volume EMA span, in days.
const AVG_VOLUME_EMA_DAYS: f64 = 20.0;

/// Mean of the intraday volume curve across a session.
///
/// The daily volume total carries both this and [`VOLUME_SCALE_MEAN`] as
/// multiplicative bias. Dividing them out is what stops `avgVolume` drifting
/// upward every day — and `avgVolume` feeds the volume model and the maker's
/// quote size, so a drift there compounds into wider books and larger prints.
const INTRADAY_VOLUME_MEAN: f64 = 1.45;

/// Mean of the per-tick `volumeScale` term.
const VOLUME_SCALE_MEAN: f64 = 1.4;

/// Reset the daily bars at the open (`market.ts:1708`).
///
/// `previousClose` is set from the price at the OPEN, not at the close. That
/// is what makes the circuit-breaker band a session band: it is measured
/// against where the name started today, so the overnight gap sits outside it
/// by construction. See D6 in the port notes.
pub fn reset_daily_prices(companies: &mut [TickCompany]) {
    for company in companies.iter_mut() {
        let s = &mut company.stock;
        s.previous_close = s.price;
        s.open = s.price;
        s.high = s.price;
        s.low = s.price;
        s.volume = 0.0;
    }
}

/// What the close needs beyond the company's own state.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CloseInputs {
    /// The day's accumulated `randomNoise` from the factor attribution.
    ///
    /// `None` means attribution was unavailable — a company's first day, or
    /// one that did not trade — and the day's total return is used instead.
    /// That fallback is the pre-attribution behaviour and is preserved.
    pub daily_innovation: Option<f64>,
    /// `sectorBaseDailyVariance(sector)` — the sector's long-run daily
    /// variance, which sets both GARCH bounds.
    pub sector_base_daily_variance: f64,
}

/// Close-of-day bookkeeping for one company.
///
/// Order matters: `daily_return` is computed from the pre-update state and is
/// then used twice — as the GARCH fallback innovation and as the stored
/// `last_daily_return` that tomorrow's squeeze and cascade logic reads.
pub fn close_day(company: &mut TickCompany, inputs: &CloseInputs) {
    let stock = &mut company.stock;

    // Guarded on `previousClose > 0`, so a newly listed name reports a flat
    // day rather than dividing by zero and poisoning GARCH with a NaN.
    let daily_return = if stock.previous_close > 0.0 {
        (stock.price - stock.previous_close) / stock.previous_close
    } else {
        0.0
    };

    // The innovation is the day's NOISE, not its return. `Number.isFinite`
    // in the original — so a NaN or infinite accumulator falls back rather
    // than propagating into the variance.
    let innovation = match inputs.daily_innovation {
        Some(noise) if noise.is_finite() => noise,
        _ => daily_return,
    };
    stock.garch_variance = update_garch_variance(
        stock.garch_variance,
        innovation,
        inputs.sector_base_daily_variance,
    );

    stock.last_daily_return = Some(daily_return);

    // The momentum roll. Gated on `mispricingS !== undefined`: a company that
    // has never ticked has no re-rating to carry, and writing a zero momentum
    // would be a different statement from writing nothing.
    if let Some(s) = stock.mispricing_s {
        let prev_close_s = stock.mispricing_s_prev_close.unwrap_or(s);
        stock.mispricing_momentum = Some(s - prev_close_s);
        stock.mispricing_s_prev_close = Some(s);
    }

    // 20-day EMA of volume, with the intraday and scale biases divided out.
    //
    // `Math.round`, not `f64::round` — see `mathx::js_round`. And the whole
    // update is skipped when the normalised volume is not positive, so a
    // closed or untraded day leaves `avgVolume` alone instead of decaying it
    // toward zero.
    //
    // Worth knowing: on THIS domain the two rounding functions are
    // indistinguishable. They differ only on negative halves — `js_round`
    // rounds half up, `f64::round` half away from zero — and both operands
    // here are positive, guaranteed by the guard above and by `avgVolume`
    // being a volume. Mutation-tested: swapping in `f64::round` changes no
    // output across 43,200 cases and three 500-day chains. `js_round` stays
    // because it is what the source says, not because anything downstream can
    // currently tell.
    let alpha = 2.0 / (AVG_VOLUME_EMA_DAYS + 1.0);
    let normalised_daily_vol = stock.volume / (INTRADAY_VOLUME_MEAN * VOLUME_SCALE_MEAN);
    if normalised_daily_vol > 0.0 {
        stock.avg_volume =
            mathx::js_round(stock.avg_volume * (1.0 - alpha) + normalised_daily_vol * alpha);
    }
}

/// Close-of-day for a whole roster.
pub fn close_day_all(companies: &mut [TickCompany], inputs: &[CloseInputs]) {
    assert_eq!(
        companies.len(),
        inputs.len(),
        "close_day_all needs one CloseInputs per company"
    );
    for (company, input) in companies.iter_mut().zip(inputs) {
        close_day(company, input);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::market::tick::TickStock;

    fn stock() -> TickStock {
        TickStock {
            price: 110.0,
            previous_close: 100.0,
            previous_tick_price: Some(109.0),
            open: 100.0,
            high: 112.0,
            low: 99.0,
            volume: 2_030_000.0,
            avg_volume: 1_000_000.0,
            shares_outstanding: 1e8,
            market_cap: 110.0 * 1e8,
            mispricing_s: Some(0.10),
            mispricing_s_prev_close: Some(0.04),
            mispricing_momentum: Some(0.0),
            maker_inventory: Some(0.0),
            garch_variance: 0.015 * 0.015,
            last_daily_return: None,
            beta: Some(1.0),
            short_interest: 0.0,
            float: 1e8,
        }
    }

    fn company() -> TickCompany {
        TickCompany {
            id: "ACME".into(),
            ticker: "ACME".into(),
            sector: "technology".into(),
            is_bankrupt: false,
            is_public: true,
            stock: stock(),
            sector_volatility: Some(1.0),
            sector_avg_pe: Some(32.0),
            eps: Some(4.0),
            book_value_per_share: Some(20.0),
            revenue_growth: Some(0.1),
        }
    }

    const BASE: f64 = 0.015 * 0.015;
    fn inputs(innovation: Option<f64>) -> CloseInputs {
        CloseInputs {
            daily_innovation: innovation,
            sector_base_daily_variance: BASE,
        }
    }

    // ── The momentum roll ─────────────────────────────────────────────────

    #[test]
    fn the_momentum_roll_carries_yesterdays_re_rating_into_today() {
        let mut c = company();
        close_day(&mut c, &inputs(None));
        // s moved 0.04 -> 0.10, so today's herding input is +0.06.
        assert!((c.stock.mispricing_momentum.unwrap() - 0.06).abs() < 1e-15);
        // And the reference advances, so tomorrow measures from here.
        assert_eq!(c.stock.mispricing_s_prev_close, Some(0.10));
    }

    #[test]
    fn a_second_close_with_no_movement_reports_zero_momentum() {
        // The roll must not be cumulative: a flat day is flat, not still
        // carrying yesterday's move.
        let mut c = company();
        close_day(&mut c, &inputs(None));
        close_day(&mut c, &inputs(None));
        assert_eq!(c.stock.mispricing_momentum, Some(0.0));
    }

    #[test]
    fn a_company_that_has_never_ticked_gets_no_momentum_written() {
        let mut c = company();
        c.stock.mispricing_s = None;
        c.stock.mispricing_momentum = None;
        close_day(&mut c, &inputs(None));
        assert_eq!(
            c.stock.mispricing_momentum, None,
            "writing a zero here would assert something the original does not"
        );
        assert_eq!(c.stock.mispricing_s_prev_close, Some(0.04));
    }

    #[test]
    fn the_first_close_measures_against_s_itself() {
        // `sPrevClose ?? s` — an absent reference means no re-rating yet.
        let mut c = company();
        c.stock.mispricing_s_prev_close = None;
        close_day(&mut c, &inputs(None));
        assert_eq!(c.stock.mispricing_momentum, Some(0.0));
    }

    // ── GARCH innovation ──────────────────────────────────────────────────

    #[test]
    fn the_variance_is_fed_the_days_noise_not_its_return() {
        // The distinction that matters: a +10% day driven by an earnings
        // re-rating carries almost no noise, and must NOT look like a
        // volatility regime change.
        let mut on_noise = company();
        close_day(&mut on_noise, &inputs(Some(0.001)));

        let mut on_return = company();
        close_day(&mut on_return, &inputs(None)); // falls back to +10%

        assert!(
            on_noise.stock.garch_variance < on_return.stock.garch_variance,
            "a quiet re-rating must not raise variance like a violent day: {} vs {}",
            on_noise.stock.garch_variance,
            on_return.stock.garch_variance
        );
    }

    #[test]
    fn a_non_finite_innovation_falls_back_to_the_return() {
        for bad in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
            let mut c = company();
            close_day(&mut c, &inputs(Some(bad)));
            let mut fallback = company();
            close_day(&mut fallback, &inputs(None));
            assert_eq!(
                c.stock.garch_variance, fallback.stock.garch_variance,
                "innovation {bad} should have fallen back"
            );
            assert!(c.stock.garch_variance.is_finite());
        }
    }

    #[test]
    fn a_newly_listed_company_reports_a_flat_day_rather_than_dividing_by_zero() {
        let mut c = company();
        c.stock.previous_close = 0.0;
        close_day(&mut c, &inputs(None));
        assert_eq!(c.stock.last_daily_return, Some(0.0));
        assert!(c.stock.garch_variance.is_finite());
    }

    #[test]
    fn the_stored_daily_return_is_what_tomorrows_forced_flow_reads() {
        // `last_daily_return` drives the squeeze and cascade branches in
        // `factors.rs`, which is why it must be the COMPLETED day's return.
        let mut c = company();
        close_day(&mut c, &inputs(None));
        assert!((c.stock.last_daily_return.unwrap() - 0.10).abs() < 1e-15);
    }

    // ── Average volume ────────────────────────────────────────────────────

    #[test]
    fn average_volume_is_stable_when_the_day_is_typical() {
        // A day whose volume is exactly the biased mean must leave avgVolume
        // where it was. If the normalisation were wrong, avgVolume would
        // ratchet upward every single day — and it feeds both the volume
        // model and the maker's quote size.
        let mut c = company();
        c.stock.avg_volume = 1_000_000.0;
        c.stock.volume = 1_000_000.0 * INTRADAY_VOLUME_MEAN * VOLUME_SCALE_MEAN;
        close_day(&mut c, &inputs(None));
        assert_eq!(c.stock.avg_volume, 1_000_000.0);
    }

    #[test]
    fn average_volume_does_not_drift_over_a_long_quiet_run() {
        let mut c = company();
        c.stock.avg_volume = 1_000_000.0;
        for _ in 0..500 {
            c.stock.volume = 1_000_000.0 * INTRADAY_VOLUME_MEAN * VOLUME_SCALE_MEAN;
            close_day(&mut c, &inputs(None));
        }
        assert_eq!(
            c.stock.avg_volume, 1_000_000.0,
            "500 typical days must leave the average exactly where it started"
        );
    }

    #[test]
    fn an_untraded_day_leaves_the_average_alone() {
        let mut c = company();
        c.stock.avg_volume = 1_000_000.0;
        c.stock.volume = 0.0;
        close_day(&mut c, &inputs(None));
        assert_eq!(
            c.stock.avg_volume, 1_000_000.0,
            "a zero-volume day must not decay the average toward zero"
        );
    }

    #[test]
    fn a_heavy_day_raises_the_average_by_roughly_one_ema_step() {
        let mut c = company();
        c.stock.avg_volume = 1_000_000.0;
        c.stock.volume = 5_000_000.0 * INTRADAY_VOLUME_MEAN * VOLUME_SCALE_MEAN;
        close_day(&mut c, &inputs(None));
        let alpha = 2.0 / 21.0;
        let expected = mathx::js_round(1_000_000.0 * (1.0 - alpha) + 5_000_000.0 * alpha);
        assert_eq!(c.stock.avg_volume, expected);
    }

    // ── Open reset ────────────────────────────────────────────────────────

    #[test]
    fn the_open_reset_anchors_the_breaker_band_to_todays_open() {
        // D6: `previousClose` is set from the price at the OPEN, which is
        // what makes the +/-25% band a session band and leaves the overnight
        // gap outside it by construction.
        let mut roster = vec![company()];
        roster[0].stock.price = 137.0;
        roster[0].stock.volume = 999.0;
        reset_daily_prices(&mut roster);
        let s = &roster[0].stock;
        assert_eq!(s.previous_close, 137.0);
        assert_eq!(s.open, 137.0);
        assert_eq!(s.high, 137.0);
        assert_eq!(s.low, 137.0);
        assert_eq!(s.volume, 0.0);
    }

    #[test]
    fn the_close_touches_nothing_it_should_not() {
        // Price, shares and inventory survive the close untouched — the
        // bookkeeping is about state for TOMORROW, not a re-pricing.
        let before = company();
        let mut after = company();
        close_day(&mut after, &inputs(Some(0.001)));
        assert_eq!(after.stock.price, before.stock.price);
        assert_eq!(after.stock.previous_close, before.stock.previous_close);
        assert_eq!(
            after.stock.shares_outstanding,
            before.stock.shares_outstanding
        );
        assert_eq!(after.stock.maker_inventory, before.stock.maker_inventory);
        assert_eq!(after.stock.mispricing_s, before.stock.mispricing_s);
    }
}
