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

use super::tick::TickCompany;
use crate::mathx;

/// Rolling-average-volume EMA span, in days.
const AVG_VOLUME_EMA_DAYS: f64 = 20.0;

/// Mean of the intraday volume curve across a session, as the reference
/// implementation assumed it. Used only under
/// [`AvgVolumePolicy::ReferenceEma`]; see that variant for why the shipped
/// path does not divide by this.
const INTRADAY_VOLUME_MEAN: f64 = 1.45;

/// Mean of the per-tick `volumeScale` term, as the reference assumed it.
const VOLUME_SCALE_MEAN: f64 = 1.4;

/// How the close treats `avg_volume`.
///
/// # Why this is a policy rather than a behaviour
///
/// This is the one place the shipped engine deliberately diverges from the
/// reference implementation, and the port's faithfulness rule says a
/// divergence must be argued, not slipped in. Keeping both paths explicit
/// keeps the argument visible AND keeps the reference path testable: the
/// TS-tape parity gates replay recorded reference runs, where bit-fidelity
/// to the reference -- including its EMA -- is the property under test.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum AvgVolumePolicy {
    /// Hold the calibrated level. The shipped default.
    ///
    /// The reference feeds realised volume back into a 20-day EMA, divided
    /// by assumed multiplier means (intraday curve ~1.45, volumeScale ~1.4)
    /// to remove the volume model's own multiplicative bias
    /// (`transitions.ts:119-125`). The correction is present, deliberate,
    /// and correctly reasoned -- and calibrated for a market this engine
    /// does not produce. Measured here (20 instruments, 252 days, seed 3):
    /// realised volume runs well above the assumed product, so the EMA is
    /// fed more than the level it tracks every single day and `avg_volume`
    /// compounds at ~1.7% a day -- 59x over one simulated year on this
    /// build, +8.5%/day and a company trading 5.2e9x its float daily on the
    /// build where it was first measured.
    ///
    /// The structural fact that makes holding correct rather than a tuning
    /// choice: realised volume in this engine is a PURE FUNCTION of
    /// `avg_volume` -- every tick prints `avg_volume/390` times bounded
    /// multipliers, and nothing else reaches the tape (agent flow moves
    /// price through the book but never adds volume). An exact
    /// realised-multiplier normalisation would reduce the EMA input to
    /// `avg_volume` itself, a no-op; any other divisor injects pure bias
    /// that compounds. The feedback carries no information either way.
    /// Re-tuning the assumed means to this build's realised behaviour would
    /// stop the divergence while cementing the excess volatility that
    /// drives the mismatch.
    ///
    /// So `avg_volume` stays what the universe calibrated it to be. An
    /// embedder that wants to move it writes `PriceField::AvgVolume`; if a
    /// genuine exogenous volume source ever reaches the tape, an EMA over
    /// THAT signal is the right reintroduction.
    #[default]
    Hold,
    /// The reference implementation's EMA feedback, bit-for-bit.
    ///
    /// Exists for the TS-tape parity gates, which replay recorded reference
    /// runs and must reproduce the reference's state evolution exactly --
    /// divergence and all. Nothing in the shipped path selects this.
    ReferenceEma,
}

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
    /// The day's VIX, read only when `garch_vix_coupling` is non-zero, to
    /// scale the clamp reference above into the regime the market is in.
    /// See §78.
    pub vix: f64,
    /// How the close treats `avg_volume`. [`AvgVolumePolicy::Hold`] unless
    /// you are replaying a reference tape.
    pub avg_volume: AvgVolumePolicy,
}

/// Close-of-day bookkeeping for one company.
///
/// Order matters: `daily_return` is computed from the pre-update state and is
/// then used twice — as the GARCH fallback innovation and as the stored
/// `last_daily_return` that tomorrow's squeeze and cascade logic reads.
pub fn close_day(company: &mut TickCompany, inputs: &CloseInputs) {
    close_day_with(&crate::params::PT_V1, company, inputs);
}

/// [`close_day`] under explicit model parameters (the runtime seam,
/// CALIBRATION.md §5.3): the GARCH update reads the params' coefficients.
/// What the engine calls; at [`crate::params::PT_V1`] it is the shipped
/// close bit for bit.
pub fn close_day_with(
    params: &crate::params::ModelParams,
    company: &mut TickCompany,
    inputs: &CloseInputs,
) {
    // Read before the mutable borrow below: per-name persistence needs the
    // company's size, and `stock` borrows `company` for the rest of the fn.
    let market_cap = company.stock.market_cap;
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
    // Per-name persistence. At zero dispersion `garch_beta_for` returns
    // `params.garch_beta` by branch, so this is the shipped arithmetic.
    let beta = super::garch::garch_beta_for(params, market_cap);
    // THE REGIME A NAME'S OWN VARIANCE IS IN. The clamps inside are multiples
    // of a STATIC per-sector variance, and the GARCH's own unconditional
    // level sits under the floor those clamps impose, so without this a
    // name's variance hovers near a constant whatever the market is doing
    // while the market factor's tracks the VIX squared. At coupling zero the
    // branch is not taken and every preset is bit-identical. See §78.
    let base_variance = if params.garch_vix_coupling == 0.0 {
        inputs.sector_base_daily_variance
    } else {
        let ratio = inputs.vix / params.market_vol_vix_anchor;
        let c = params.garch_vix_coupling;
        inputs.sector_base_daily_variance * (1.0 - c + c * ratio * ratio)
    };
    stock.garch_variance = super::garch::update_garch_variance_for(
        params,
        beta,
        stock.garch_variance,
        innovation,
        base_variance,
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

    // The one argued divergence from the reference: under the shipped
    // policy, `avg_volume` is not updated from realised volume at all. The
    // full argument lives on [`AvgVolumePolicy::Hold`]; in one line, the
    // reference's EMA feedback tracks a quantity that is a pure function of
    // `avg_volume` itself, so it carries no information and any
    // normalisation mismatch compounds exponentially (measured at ~1.7%/day
    // on this build, +8.5%/day where first found).
    //
    // The `ReferenceEma` arm is the reference bit-for-bit, for tape replay.
    // `Math.round`, not `f64::round` -- see `mathx::js_round`. The update is
    // skipped when the normalised volume is not positive, so a closed or
    // untraded day leaves `avgVolume` alone instead of decaying it toward
    // zero. (On this domain the two rounding functions are
    // indistinguishable; mutation-tested, `js_round` stays because it is
    // what the source says.)
    if inputs.avg_volume == AvgVolumePolicy::ReferenceEma {
        let alpha = 2.0 / (AVG_VOLUME_EMA_DAYS + 1.0);
        let normalised_daily_vol = stock.volume / (INTRADAY_VOLUME_MEAN * VOLUME_SCALE_MEAN);
        if normalised_daily_vol > 0.0 {
            stock.avg_volume =
                mathx::js_round(stock.avg_volume * (1.0 - alpha) + normalised_daily_vol * alpha);
        }
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
            vix: 15.0,
            avg_volume: AvgVolumePolicy::Hold,
        }
    }
    fn reference_inputs(innovation: Option<f64>) -> CloseInputs {
        CloseInputs {
            avg_volume: AvgVolumePolicy::ReferenceEma,
            ..inputs(innovation)
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
    fn the_shipped_close_never_moves_average_volume() {
        // The argued divergence: realised volume is a pure function of
        // `avg_volume`, so feeding it back was a positive feedback loop with
        // no information in it, and it compounded at ~1.7%/day on this
        // build. Under the shipped policy the close leaves the calibrated
        // level exactly alone, whatever the day did.
        for daily_volume in [0.0, 1.0, 2_030_000.0, 4.2e15] {
            let mut c = company();
            c.stock.avg_volume = 1_000_000.0;
            c.stock.volume = daily_volume;
            close_day(&mut c, &inputs(None));
            assert_eq!(
                c.stock.avg_volume, 1_000_000.0,
                "a day of volume {daily_volume} must leave the average alone"
            );
        }
    }

    #[test]
    fn the_reference_ema_is_preserved_for_tape_replay() {
        // The `ReferenceEma` arm must keep behaving exactly as the reference
        // does, because the TS-tape parity gates replay recorded reference
        // runs through it. A day at the assumed biased mean is a fixed
        // point; a heavier day moves the average by one EMA step.
        let mut c = company();
        c.stock.avg_volume = 1_000_000.0;
        c.stock.volume = 1_000_000.0 * INTRADAY_VOLUME_MEAN * VOLUME_SCALE_MEAN;
        close_day(&mut c, &reference_inputs(None));
        assert_eq!(c.stock.avg_volume, 1_000_000.0);

        let mut c = company();
        c.stock.avg_volume = 1_000_000.0;
        c.stock.volume = 5_000_000.0 * INTRADAY_VOLUME_MEAN * VOLUME_SCALE_MEAN;
        close_day(&mut c, &reference_inputs(None));
        let alpha = 2.0 / 21.0;
        let expected = mathx::js_round(1_000_000.0 * (1.0 - alpha) + 5_000_000.0 * alpha);
        assert_eq!(c.stock.avg_volume, expected);
    }

    #[test]
    fn an_untraded_day_leaves_the_reference_average_alone() {
        let mut c = company();
        c.stock.avg_volume = 1_000_000.0;
        c.stock.volume = 0.0;
        close_day(&mut c, &reference_inputs(None));
        assert_eq!(
            c.stock.avg_volume, 1_000_000.0,
            "a zero-volume day must not decay the average toward zero"
        );
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
