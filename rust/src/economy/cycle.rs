//! Business-cycle transitions, ported from the reference implementation's
//! economy module.
//!
//! Phase duration is modelled as a Weibull hazard rather than a fixed length,
//! so how long a phase has already lasted changes how likely it is to end.
//! `shape > 1` means an ageing phase grows fragile (expansions); `shape < 1`
//! means early exits dominate and late ones linger (contractions).
//!
//! # The clock every rate here is on
//!
//! Every scale in [`cycle_hazard_params`] is in MONTHS, so
//! [`weibull_hazard`] returns a rate per month, and so does the condition
//! ladder that adds to it and the clamp that bounds it.
//! `months_in_current_phase` advances by `1/30` a day, which makes the month
//! this model keeps 30 days long. [`check_cycle_transition`] draws once a
//! day, so the monthly rate is converted at the draw under
//! `cycle_hazard_per_month`. At 0.0 it is drawn as written, which is the
//! reference implementation's reading and about thirty times the rate its
//! own parameters state.

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

/// The monthly hazard read on the day it is drawn.
///
/// A BRANCH at 0.0, so every preset before pt-v18 compares the monthly rate
/// against the uniform exactly as the reference implementation does. At 1.0
/// the arithmetic is `monthly / 30.0` to the last bit, because `30 - 29` is
/// exact and `x / 1.0` is `x`. In between, the share of the correction
/// applied is linear in the probability.
///
/// See [`crate::params::ModelParams::cycle_hazard_per_month`] for why the
/// conversion belongs here rather than inside [`weibull_hazard`] or before
/// the clamp.
#[inline]
fn per_day(monthly: f64, per_month: f64) -> f64 {
    if per_month == 0.0 {
        monthly
    } else {
        monthly * (30.0 - 29.0 * per_month) / 30.0
    }
}

/// Phase-specific Weibull parameters.
///
/// Every scale is in MONTHS. See this module's own note on the clock.
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
pub fn get_cycle_transition_probability(
    economy: &EconomyState,
    per_month: f64,
) -> (f64, CyclePhase) {
    let phase = phase_characteristics(economy.cycle_phase);
    let months = economy.months_in_current_phase;
    if months < phase.min_months {
        return (0.0, phase.next_phase);
    }

    let (shape, scale) = cycle_hazard_params(economy.cycle_phase);
    let p = adjust_transition_probability(economy, weibull_hazard(months, shape, scale));

    (per_day(clamp(p, 0.0, 0.3), per_month), phase.next_phase)
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
pub fn check_cycle_transition(
    economy: &EconomyState,
    rng: &mut impl Rng,
    per_month: f64,
) -> EconomyState {
    let phase = phase_characteristics(economy.cycle_phase);
    let months = economy.months_in_current_phase;

    if months < phase.min_months {
        return economy.clone();
    }

    let (shape, scale) = cycle_hazard_params(economy.cycle_phase);
    let p = adjust_transition_probability(economy, weibull_hazard(months, shape, scale));
    // The cap on the hazard, at 0.3 of whatever unit the hazard carries.
    // Under the reference implementation's reading that is a 30 per cent
    // chance on any one day; under the monthly reading it caps a rate per
    // month and the largest daily probability is 0.01. On the hazard alone
    // it binds for a peak past month 5.40 and a trough past month 2.57, and
    // under either reading a trough with its own ladder saturates it on the
    // first eligible roll. See `ModelParams::cycle_hazard_per_month` for
    // the counts.
    let transition_probability = per_day(clamp(p, 0.0, 0.3), per_month);

    if rng.next_f64() < transition_probability {
        let mut next = economy.clone();
        next.cycle_phase = phase.next_phase;
        next.months_in_current_phase = 0.0;
        return next;
    }

    economy.clone()
}

// ── The stationary law of (phase, age) ────────────────────────────────────
//
// A run whose day-zero phase is a POINT starts a cohort: thirty seeds leave
// their first expansion at nearly the same age and move through the first
// cycle in step, so year two is a synchronised recession. The fixed minimum
// durations make the cycle's length nearly deterministic, so the cohort does
// not damp away on any horizon a user runs. The remedy is to open at rest,
// and that needs the law below.

/// The five phases in the chain's own cyclic order.
///
/// Walked from `next_phase` rather than restated. The order is
/// [`phase_characteristics`]'s to declare, and a list here would be a
/// second spelling of it -- the class of defect that put a variance floor
/// in three places and let two of them disagree.
pub fn phase_cycle() -> [CyclePhase; 5] {
    let mut out = [CyclePhase::Expansion; 5];
    for k in 1..out.len() {
        out[k] = phase_characteristics(out[k - 1]).next_phase;
    }
    out
}

/// The transition probability [`check_cycle_transition`] compares against
/// its uniform on the `days`-th day of a phase, on the HAZARD ALONE.
///
/// That function's own arithmetic with [`adjust_transition_probability`]
/// taken as the identity, in the same order: the hazard, then the clamp,
/// then the per-day conversion.
///
/// # Why the ladder is absent, and where it is handled instead
///
/// `adjust_transition_probability` adds to the hazard from the MACRO
/// STATE -- inflation above 4, a policy rate above 5, an inverted curve, a
/// market P/E above 28 in expansion -- so the true stationary law depends
/// on the fields, which depend on the phase path. There is no closed form
/// and this does not pretend one. The hazard-only law is what is DRAWN
/// from; the fields are then relaxed under the drawn phase by the
/// free-running burn-in, during which the ladder acts on the mix. The
/// residual is the difference between the two laws, measurable as the
/// occupancy of a long economy-only run.
///
/// # Days, not months
///
/// `months_in_current_phase` advances by `1/30` a day
/// (`economy/daily.rs`) and `check_cycle_transition` reads it AFTER that
/// advance, so the `d`-th day of a phase is checked at `d / 30` months and
/// the first check of a fresh phase is at `1/30`.
fn hazard_only_transition_probability(phase: CyclePhase, days: i64, per_month: f64) -> f64 {
    let months = days as f64 / 30.0;
    if months < phase_characteristics(phase).min_months {
        return 0.0;
    }
    let (shape, scale) = cycle_hazard_params(phase);
    per_day(clamp(weibull_hazard(months, shape, scale), 0.0, 0.3), per_month)
}

/// Walk a phase's hazard-only survival in days, and return the sum of it.
///
/// The visitor sees `(a, C(a))` for `a = 0, 1, 2, ...`, where
///
/// ```text
/// S(d) = prod_{t=1}^{d-1} (1 - p(t))     P(the sojourn survives d - 1 checks)
/// C(a) = sum_{d=1}^{a+1} S(d)            E[T] * P(age <= a)
/// ```
///
/// so the walk's own return value is `E[T] = sum_{d>=1} S(d)` when the
/// visitor never stops it, and the visitor is handed the unnormalised CDF
/// of the age at every step. Returning `false` stops the walk.
///
/// # Where it stops, and why that is exact rather than a tolerance
///
/// The walk ends when adding the next term leaves the f64 sum UNCHANGED --
/// the point past which no arithmetic this function could do would move
/// its answer. Nothing is chosen: the stopping rule is a property of the
/// representation. It always arrives, because `S` is non-increasing (the
/// transition probability is clamped to 0.3, so every factor is in
/// `[0.7, 1]`), is positive past the minimum duration, and the sum is
/// bounded above by `E[T]`.
///
/// Expressing `E[T]` and the age's CDF on ONE walk is what makes the age
/// law's support the mean's support by construction, rather than by two
/// truncations that happen to agree.
fn walk_survival(
    phase: CyclePhase,
    per_month: f64,
    mut visit: impl FnMut(i64, f64) -> bool,
) -> f64 {
    let (mut s, mut acc, mut a) = (1.0, 0.0, 0i64);
    loop {
        let next = acc + s;
        if next == acc {
            return acc;
        }
        acc = next;
        if !visit(a, acc) {
            return acc;
        }
        s *= 1.0 - hazard_only_transition_probability(phase, a + 1, per_month);
        a += 1;
    }
}

/// `E[T]` for one phase, in DAYS, on the hazard alone.
///
/// `sum_{d>=1} S(d)`, which is the mean of a non-negative integer sojourn
/// written as the sum of its survival function.
pub fn mean_sojourn_days(phase: CyclePhase, per_month: f64) -> f64 {
    walk_survival(phase, per_month, |_, _| true)
}

/// The stationary share of DAYS the chain spends in each phase, in
/// [`phase_cycle`] order, as unnormalised weights and their total.
///
/// ```text
/// pi_i = E[T_i] / sum_j E[T_j]
/// ```
///
/// The renewal-reward identity for a cyclic semi-Markov chain that visits
/// every phase exactly once per cycle: each phase is entered once per
/// cycle, so the share of days is the share of the cycle's length. That
/// the chain is cyclic is [`phase_cycle`]'s own assertion, not an
/// assumption made here.
pub fn stationary_phase_shares(per_month: f64) -> ([f64; 5], f64) {
    let mut mean = [0.0; 5];
    let mut cycle = 0.0;
    for (k, &phase) in phase_cycle().iter().enumerate() {
        mean[k] = mean_sojourn_days(phase, per_month);
        cycle += mean[k];
    }
    (mean, cycle)
}

/// The day-zero `(phase, months_in_current_phase)` drawn from the cycle's
/// own stationary law, from two uniforms.
///
/// # The identity
///
/// ```text
/// pi_i    = E[T_i] / sum_j E[T_j]         the share of days in phase i
/// f_i(a)  = S_i(a + 1) / E[T_i]           the age within phase i, in days
/// P(i, a) = S_i(a + 1) / sum_j E[T_j]     the joint law
/// ```
///
/// The second line is the renewal identity for the BACKWARD RECURRENCE
/// TIME: in equilibrium the chance of finding a phase `a` days old is
/// proportional to the chance a sojourn in it lasts longer than `a`. It is
/// the part that cannot be skipped. Drawing the phase and setting the age
/// to zero would start a smaller cohort at the same point, and at pt-v16's
/// clock 73 per cent of stationary expansions are younger than the 180-day
/// minimum a fresh one has to clear.
///
/// `a` runs over `0, 1, 2, ...` because the engine's own age does: a
/// transition sets `months_in_current_phase` to 0.0, so a phase entered
/// yesterday opens a day at age zero, and a sojourn that ends on its
/// `j`-th check has been observed at the ages `0 .. j - 1`.
///
/// # The two draws
///
/// `u_phase` picks the phase from the cumulative shares and `u_age` the age
/// from `f_i`. Both are compared against the CDF as written -- the first
/// phase, and then the first age, whose cumulative probability reaches the
/// uniform. The last phase absorbs a uniform that rounds past the final
/// cumulative share, which is a rounding fallback and not a sixth branch.
pub fn stationary_opening(per_month: f64, u_phase: f64, u_age: f64) -> (CyclePhase, f64) {
    let phases = phase_cycle();
    let (mean, cycle) = stationary_phase_shares(per_month);

    let mut cumulative = 0.0;
    let mut pick = phases.len() - 1;
    for k in 0..phases.len() {
        cumulative += mean[k];
        if cumulative / cycle >= u_phase {
            pick = k;
            break;
        }
    }

    let phase = phases[pick];
    let mut age = 0;
    walk_survival(phase, per_month, |a, cdf| {
        age = a;
        cdf / mean[pick] < u_age
    });
    (phase, age as f64 / 30.0)
}

#[cfg(test)]
mod stationary_law {
    use super::*;

    /// The order is walked, so the guard is that it CLOSES: five distinct
    /// phases and the fifth's successor is the first. A `next_phase` that
    /// stopped being a cycle would break the renewal identity above --
    /// `pi_i = E[T_i] / sum_j E[T_j]` holds because every phase is entered
    /// exactly once per cycle -- and would fail here rather than silently
    /// return a law for a chain the engine no longer runs.
    #[test]
    fn the_phase_order_closes_on_itself() {
        let phases = phase_cycle();
        assert_eq!(phase_characteristics(phases[4]).next_phase, phases[0]);
        for i in 0..5 {
            for j in (i + 1)..5 {
                assert_ne!(phases[i], phases[j], "{i} and {j} are the same phase");
            }
        }
    }

    /// The law's own arithmetic must be the ENGINE's, so it is compared
    /// against `get_cycle_transition_probability` -- the UI-safe form of
    /// the roll, which computes the same probability without drawing -- on
    /// an economy whose fields fire no ladder condition. Where the hazard
    /// alone decides is where this law claims anything.
    #[test]
    fn the_hazard_only_probability_is_the_transition_the_engine_rolls() {
        for per_month in [0.0, 1.0] {
            for phase in phase_cycle() {
                for &days in &[1, 59, 60, 119, 120, 179, 180, 181, 400, 2000] {
                    let mut e = ladder_free_economy();
                    e.cycle_phase = phase;
                    e.months_in_current_phase = days as f64 / 30.0;
                    let p = hazard_only_transition_probability(phase, days, per_month);
                    let (engine_p, _) = get_cycle_transition_probability(&e, per_month);
                    assert_eq!(p, engine_p, "{phase:?} at {days} days, clock {per_month}");
                }
            }
        }
    }

    /// The sojourn's mean must be the mean of the sojourn: the survival
    /// sum is checked against `sum_j j * P(T = j)` accumulated the other
    /// way round, over the SAME support -- the walk's own horizon -- so
    /// the two truncations cannot silently differ.
    #[test]
    fn the_survival_sum_is_the_mean_of_the_sojourn() {
        for per_month in [0.0, 1.0] {
            for phase in phase_cycle() {
                let sum = mean_sojourn_days(phase, per_month);
                let mut horizon = 0;
                walk_survival(phase, per_month, |a, _| {
                    horizon = a;
                    true
                });
                let mut survival = 1.0;
                let mut mean = 0.0;
                for j in 1..=(horizon + 1) {
                    let p = hazard_only_transition_probability(phase, j, per_month);
                    mean += j as f64 * survival * p;
                    survival *= 1.0 - p;
                }
                assert!((mean - sum).abs() / sum < 1e-9,
                        "{phase:?} clock {per_month}: {mean} against {sum}");
            }
        }
    }

    /// A phase cannot be found younger than its minimum duration more
    /// often than the minimum's own share of the mean sojourn, because the
    /// survival function is exactly 1 there. This is the arithmetic behind
    /// "73 per cent of stationary expansions are younger than the
    /// minimum", and it is an identity: `P(age < min) = min_days / E[T]`.
    #[test]
    fn the_share_below_the_minimum_is_the_minimum_over_the_mean() {
        for per_month in [0.0, 1.0] {
            for phase in phase_cycle() {
                let min_days = (phase_characteristics(phase).min_months * 30.0) as i64;
                let mean = mean_sojourn_days(phase, per_month);
                let mut below = 0.0;
                walk_survival(phase, per_month, |a, cdf| {
                    if a == min_days - 1 {
                        below = cdf / mean;
                        return false;
                    }
                    true
                });
                let identity = min_days as f64 / mean;
                assert!((below - identity).abs() < 1e-12,
                        "{phase:?} clock {per_month}: {below} against {identity}");
            }
        }
    }

    /// The joint law is a law: the phase shares sum to one, and so does
    /// the age density inside each phase.
    #[test]
    fn the_shares_and_the_age_density_are_both_distributions() {
        for per_month in [0.0, 1.0] {
            let (mean, cycle) = stationary_phase_shares(per_month);
            let total: f64 = mean.iter().sum::<f64>() / cycle;
            assert!((total - 1.0).abs() < 1e-12, "shares sum to {total}");
            for (k, &phase) in phase_cycle().iter().enumerate() {
                let mut last = 0.0;
                walk_survival(phase, per_month, |_, cdf| {
                    last = cdf / mean[k];
                    true
                });
                assert!((last - 1.0).abs() < 1e-12,
                        "{phase:?} clock {per_month}: age density sums to {last}");
            }
        }
    }

    /// The phase draw is an inversion of `pi`, asserted at the INTERVAL
    /// EDGES rather than by a histogram: a uniform just inside each end of
    /// a phase's cumulative interval must select that phase, so the point
    /// just above one phase's top selects the next. That is where an
    /// off-by-one in the cumulative sum would live, and a histogram over a
    /// grid is the one instrument that cannot see it.
    #[test]
    fn the_draw_picks_the_phase_whose_interval_holds_the_uniform() {
        for per_month in [0.0, 1.0] {
            let (mean, cycle) = stationary_phase_shares(per_month);
            let phases = phase_cycle();
            let mut lower = 0.0;
            for k in 0..phases.len() {
                let upper = lower + mean[k] / cycle;
                let inside = (upper - lower) * 1e-6;
                for u in [lower + inside, (lower + upper) / 2.0, upper - inside] {
                    let (got, _) = stationary_opening(per_month, u, 0.5);
                    assert_eq!(got, phases[k],
                               "u = {u} clock {per_month} wanted {:?}", phases[k]);
                }
                lower = upper;
            }
            // And the whole of the unit interval is covered, so no uniform
            // falls through to the rounding fallback by accident.
            assert!((lower - 1.0).abs() < 1e-12, "the intervals end at {lower}");
        }
    }

    /// The age draw is an inversion of `f_i`: the drawn age must be the
    /// first whose cumulative probability reaches the uniform, computed
    /// independently from the walk, and it must arrive in the engine's own
    /// 30-day months.
    ///
    /// The monotonicity the inversion needs is asserted where it comes
    /// from -- the CDF being non-decreasing -- rather than sampled out of
    /// the inversion, which would need a grid fine enough to be its own
    /// measurement.
    #[test]
    fn the_drawn_age_is_the_quantile_of_the_age_density_in_engine_months() {
        for per_month in [0.0, 1.0] {
            let (mean, cycle) = stationary_phase_shares(per_month);
            for (k, &phase) in phase_cycle().iter().enumerate() {
                let mut cdf = Vec::new();
                walk_survival(phase, per_month, |_, c| {
                    cdf.push(c / mean[k]);
                    true
                });
                for w in cdf.windows(2) {
                    assert!(w[1] >= w[0], "{phase:?} clock {per_month}: the CDF fell");
                }
                let u_phase = (mean[..k].iter().sum::<f64>() + mean[k] / 2.0) / cycle;
                for &u in &[1e-9, 0.1, 0.25, 0.5, 0.731, 0.9, 0.999] {
                    let want = cdf.iter().position(|&c| c >= u).unwrap() as i64;
                    let (got_phase, months) = stationary_opening(per_month, u_phase, u);
                    assert_eq!(got_phase, phase, "clock {per_month} at u = {u}");
                    assert_eq!(months, want as f64 / 30.0,
                               "{phase:?} clock {per_month} at u = {u}: wanted {want} days");
                }
            }
        }
    }

    /// A degenerate uniform must not walk off either end: zero takes the
    /// first phase at age zero and a uniform at the last representable
    /// value below one takes a real phase and a finite age.
    #[test]
    fn the_ends_of_the_uniform_stay_inside_the_law() {
        let below_one = 1.0 - f64::EPSILON / 2.0;
        for per_month in [0.0, 1.0] {
            let (first, age) = stationary_opening(per_month, 0.0, 0.0);
            assert_eq!(first, phase_cycle()[0]);
            assert_eq!(age, 0.0);
            let (last, age) = stationary_opening(per_month, below_one, below_one);
            assert!(phase_cycle().contains(&last));
            assert!(age.is_finite() && age > 0.0, "age {age}");
        }
    }

    /// An economy on which no ladder condition fires, so the hazard alone
    /// decides. Every field is set against the branch that reads it in
    /// `adjust_transition_probability`, and the expansion guard is left
    /// unfired too (unemployment under 8, growth over 1).
    fn ladder_free_economy() -> EconomyState {
        let mut e = create_initial_economy_state(&InitialEconomyOptions::default());
        e.inflation_rate = 2.0;
        e.federal_funds_rate = 3.0;
        e.treasury_yield_2y = 2.0;
        e.treasury_yield_10y = 3.0;
        e.market_pe = Some(18.0);
        e.unemployment_rate = 4.0;
        e.gdp_growth = 2.0;
        e
    }
}
