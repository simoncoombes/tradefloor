//! Business-cycle transitions, ported from the reference implementation's
//! economy module.
//!
//! Phase duration is modelled as a Weibull hazard rather than a fixed length,
//! so how long a phase has already lasted changes how likely it is to end.
//! `shape > 1` means an ageing phase grows fragile (expansions); `shape < 1`
//! means early exits dominate and late ones linger (contractions).

use super::state::*;
use crate::mathx::{self, clamp_via_min_max as clamp};
use crate::rng::Rng;

/// Weibull hazard, capped at 0.8.
///
/// `pow(t, shape - 1)` changes character across `shape == 1`: below it the
/// exponent is negative and the hazard falls with duration, above it the
/// hazard rises. The configured shapes straddle that boundary (0.7 for
/// contraction, 1.3–2.0 elsewhere), so both regimes are live.
pub fn weibull_hazard(months: f64, shape: f64, scale: f64) -> f64 {
    // `months <= 0` — NOT the negated form used elsewhere in the crate,
    // because the original is `if (months <= 0) return 0`. A NaN month
    // therefore falls THROUGH here and produces NaN, where `!(months > 0)`
    // would have returned 0.
    if months <= 0.0 {
        return 0.0;
    }
    let t = months / scale;
    mathx::min(0.8, (shape / scale) * mathx::pow(t, shape - 1.0))
}

/// Phase-specific Weibull parameters.
pub fn cycle_hazard_params(phase: CyclePhase) -> (f64, f64) {
    match phase {
        // Long expansions get increasingly fragile.
        CyclePhase::Expansion => (1.8, 36.0),
        // Peaks are unstable and transition quickly.
        CyclePhase::Peak => (2.0, 6.0),
        // Early contractions exit fast, late ones linger.
        CyclePhase::Contraction => (0.7, 12.0),
        // Troughs self-limit fairly quickly.
        CyclePhase::Trough => (1.5, 4.0),
        // Recovery builds momentum, then transitions.
        CyclePhase::Recovery => (1.3, 12.0),
    }
}

/// The daily transition probability and the phase it would move to.
///
/// This is `getCycleTransitionProbability` — the UI-safe form that computes
/// the probability **without rolling the dice**. The original duplicates the
/// condition ladder between this and [`check_cycle_transition`]; here the two
/// share [`adjust_transition_probability`], on the evidence recorded there.
pub fn get_cycle_transition_probability(economy: &EconomyState) -> (f64, CyclePhase) {
    let phase = phase_characteristics(economy.cycle_phase);
    let months = economy.months_in_current_phase;
    if months < phase.min_months {
        return (0.0, phase.next_phase);
    }

    let (shape, scale) = cycle_hazard_params(economy.cycle_phase);
    let p = adjust_transition_probability(economy, weibull_hazard(months, shape, scale));

    (clamp(p, 0.0, 0.3), phase.next_phase)
}

/// The condition ladder shared, in the original, by both entry points.
///
/// Factored out here only because the two copies in the reference implementation are
/// semantically identical — compared branch by branch, not assumed. They are
/// not textually identical (one inlines the inversion depth, the other binds
/// it to a temporary), but every condition, constant and operation matches.
/// If they ever diverge upstream this must split back into two.
fn adjust_transition_probability(economy: &EconomyState, mut p: f64) -> f64 {
    match economy.cycle_phase {
        CyclePhase::Expansion => {
            if economy.inflation_rate > 4.0 {
                p += 0.1;
            }
            if economy.federal_funds_rate > 5.0 {
                p += 0.1;
            }
            // Curve inversion has preceded every US recession since 1955.
            if economy.treasury_yield_2y > economy.treasury_yield_10y {
                let inversion_depth = economy.treasury_yield_2y - economy.treasury_yield_10y;
                p += mathx::min(0.15, inversion_depth * 0.08);
            }
            // Expensive markets are fragile.
            let mkt_pe = economy.market_pe.unwrap_or(18.0);
            if mkt_pe > 28.0 {
                p += mathx::min(0.1, (mkt_pe - 28.0) * 0.005);
            }
            // Guard: an expansion should not peak while unemployment is still
            // high or GDP is negative — the economy has not recovered yet.
            if economy.unemployment_rate > 8.0 || economy.gdp_growth < 1.0 {
                p = mathx::max(0.0, p - 0.2);
            }
        }
        CyclePhase::Recovery => {
            if economy.unemployment_rate > 10.0 {
                p = mathx::max(0.0, p - 0.1);
            }
        }
        CyclePhase::Contraction => {
            if economy.federal_funds_rate < 1.0 {
                p += 0.1;
            }
            // An economy cannot contract forever; it bottoms out even without
            // rate cuts.
            if economy.gdp_growth < -2.0 {
                p += 0.05;
            }
            if economy.unemployment_rate > 10.0 {
                p += 0.05;
            }
            // A steepening curve signals recovery approaching.
            if economy.treasury_yield_10y - economy.treasury_yield_2y > 1.5 {
                p += 0.05;
            }
        }
        CyclePhase::Trough => {
            if economy.federal_funds_rate < 3.0 {
                p += 0.1;
            }
            // Very high unemployment builds pent-up demand.
            if economy.unemployment_rate > 8.0 {
                p += 0.05;
            }
        }
        CyclePhase::Peak => {}
    }
    p
}

/// Roll for a cycle transition.
///
/// **Draw schedule:** exactly **one uniform**, and only when the minimum
/// phase duration has elapsed. Below `min_months` the function returns before
/// drawing, so the count is 0-or-1 and never anything else.
pub fn check_cycle_transition(economy: &EconomyState, rng: &mut impl Rng) -> EconomyState {
    let phase = phase_characteristics(economy.cycle_phase);
    let months = economy.months_in_current_phase;

    if months < phase.min_months {
        return economy.clone();
    }

    let (shape, scale) = cycle_hazard_params(economy.cycle_phase);
    let p = adjust_transition_probability(economy, weibull_hazard(months, shape, scale));
    // Max 30% chance on any one day.
    let transition_probability = clamp(p, 0.0, 0.3);

    if rng.next_f64() < transition_probability {
        let mut next = economy.clone();
        next.cycle_phase = phase.next_phase;
        next.months_in_current_phase = 0.0;
        return next;
    }

    economy.clone()
}
