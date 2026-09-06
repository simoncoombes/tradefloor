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

/// Below this survival probability the sojourn sum stops.
///
/// `S_i(d)` falls geometrically once the hazard is live, so the mass beyond
/// the stop is bounded by `S_i(D) / p_i(D)` — the survival at the stop over
/// the smallest per-day probability from there on. The worst case among the
/// ten shipped (phase, clock) pairs is a contraction on pt-v18's clock,
/// whose shape of 0.7 makes the hazard FALL with age: it stops at day
/// 57,799, where `p` is 4.2e-4 a day, so the omitted mass is under 2.4e-12
/// days against a mean sojourn of 696.6. Every other pair stops sooner and
/// bounds tighter.
const SURVIVAL_FLOOR: f64 = 1e-15;

/// A hard stop on both sums, so a `per_month` outside `[0, 1]` cannot spin.
///
/// [`per_day`] scales by `(30 - 29 * per_month) / 30`, which is NEGATIVE
/// above `30/29`. A negative probability makes `1 - p` exceed one and the
/// survival grow without bound, so the floor above would never be reached.
/// Nothing validates `cycle_hazard_per_month`, so the guard is here rather
/// than assumed away. It is never reached on either shipped clock.
const AGE_HORIZON: u64 = 200_000;

/// The five phases in the order the chain walks them, read off the engine.
///
/// Derived rather than restated: start at expansion and follow
/// [`phase_characteristics`]`().next_phase` five times. A sixth step returns
/// to expansion, which `the_phase_cycle_closes_on_itself` asserts.
pub fn phase_cycle() -> [CyclePhase; 5] {
    let mut out = [CyclePhase::Expansion; 5];
    let mut phase = CyclePhase::Expansion;
    for slot in out.iter_mut() {
        *slot = phase;
        phase = phase_characteristics(phase).next_phase;
    }
    out
}

/// The day's transition probability on the HAZARD ALONE, at a phase age of
/// `age_days` whole days.
///
/// This is [`check_cycle_transition`]'s arithmetic with
/// `adjust_transition_probability` taken as the identity. The ladder is
/// macro-state dependent — inflation, the policy rate, the curve, the
/// market's P/E — so there is no closed form for the law under it, and none
/// is claimed. See [`stationary_opening`] for how the ladder is handled
/// instead.
///
/// The age is in WHOLE DAYS because that is what the engine rolls on:
/// `update_economy_daily` adds `1/30` to `months_in_current_phase` and
/// [`Engine::advance_day`] then calls [`check_cycle_transition`], so the
/// roll on the `d`-th day of a phase sees `d/30` months. `d as f64 / 30.0`
/// is that quantity computed once rather than accumulated, which differs
/// from the engine's repeated addition in the last bits; the law is a
/// statement about the process, not a bit-reproduction of one path's
/// accumulator.
///
/// [`Engine::advance_day`]: crate::engine::Engine::advance_day
pub fn hazard_only_transition_probability(
    phase: CyclePhase,
    age_days: u64,
    per_month: f64,
) -> f64 {
    let months = age_days as f64 / 30.0;
    if months < phase_characteristics(phase).min_months {
        return 0.0;
    }
    let (shape, scale) = cycle_hazard_params(phase);
    per_day(clamp(weibull_hazard(months, shape, scale), 0.0, 0.3), per_month)
}

/// `E[T_i]`, the mean sojourn in a phase, in days.
///
/// ```text
/// S_i(d)  = prod_{t=1}^{d-1} (1 - p_i(t))    P(the sojourn reaches day d)
/// E[T_i]  = sum_{d>=1} S_i(d)                the mean, by the tail sum
/// ```
///
/// The tail sum for a non-negative integer variable, with `p_i` from
/// [`hazard_only_transition_probability`]. At pt-v16's clock this reads
/// 246.4, 67.1, 131.7, 62.7 and 131.5 days for a cycle of 639.4 days =
/// 2.54 trading years; at pt-v18's, 994.2, 186.5, 696.6, 160.0 and 404.0
/// for 2441.2 days = 9.69 years. `cycle_hazard_per_month`'s own docstring
/// states 2.6 and 9.7, so this reproduces the engine's record of itself
/// before it is asked for anything new.
pub fn mean_sojourn_days(phase: CyclePhase, per_month: f64) -> f64 {
    let mut survival = 1.0;
    let mut total = 0.0;
    let mut day: u64 = 1;
    loop {
        total += survival;
        survival *= 1.0 - hazard_only_transition_probability(phase, day, per_month);
        if survival < SURVIVAL_FLOOR || day >= AGE_HORIZON {
            return total;
        }
        day += 1;
    }
}

/// The day-zero cycle state, drawn from the chain's own stationary law.
///
/// # The identity
///
/// The cycle is a cyclic semi-Markov chain — expansion, peak, contraction,
/// trough, recovery, back to expansion ([`phase_cycle`]) — with one roll a
/// day once the minimum duration has elapsed. Writing `S_i` and `E[T_i]` as
/// in [`mean_sojourn_days`]:
///
/// ```text
/// pi_i    = E[T_i] / sum_j E[T_j]      the share of days spent in phase i
/// P(K=k)  = S_i(k+1) / E[T_i]          the age within phase i, k = 0, 1, 2, ...
/// ```
///
/// The second line is the renewal identity for the BACKWARD RECURRENCE TIME
/// of a discrete renewal process: a sojourn of length `T` is lived through
/// on `T` days, and on the day whose roll is the `(k+1)`-th it has already
/// survived `k` rolls, so the chance of finding it at age `k` is
/// proportional to `P(T > k) = S_i(k+1)`. It sums to one because
/// `sum_{k>=0} P(T > k)` IS `E[T]`, the same tail sum [`mean_sojourn_days`]
/// computes.
///
/// **The age is not optional.** Drawing the phase and setting the age to
/// zero starts a smaller cohort at the same point: 73 per cent of stationary
/// expansions are younger than the 180-day minimum at pt-v16's clock, with a
/// median age of 123 days.
///
/// `k` counts rolls ALREADY SURVIVED, so `k = 0` is a phase that has just
/// begun and `months_in_current_phase` is `k / 30`. The engine increments
/// that by `1/30` before it rolls, so a state opened at `k/30` is next
/// rolled at `(k+1)/30` — the `(k+1)`-th roll, which is what "survived `k`
/// rolls" means.
///
/// # The ladder, handled by construction and not by algebra
///
/// `adjust_transition_probability` adds to the hazard from the macro state,
/// so the true stationary law depends on fields that depend on the phase
/// path, and there is no closed form. This draw is the hazard-only law;
/// `Engine::relax_economy` then runs `macro_burn_in_days` of free economy
/// under the drawn phase, during which the ladder acts on the mix. The
/// residual is the difference between the two laws, measurable as the
/// occupancy of a long economy-only run and not measured here.
///
/// # The draws
///
/// Two uniforms, both from the ECONOMY substream — the stream the burn-in
/// already consumes — so the market's day-zero draws sit where they sat.
/// `u_phase` picks the phase from `pi` by inverse CDF; `u_age` picks the age
/// from `P(K = k)` the same way. Returns the phase and its
/// `months_in_current_phase`.
pub fn stationary_opening(per_month: f64, u_phase: f64, u_age: f64) -> (CyclePhase, f64) {
    let order = phase_cycle();
    let mut means = [0.0f64; 5];
    let mut cycle_days = 0.0;
    for (slot, phase) in means.iter_mut().zip(order.iter()) {
        *slot = mean_sojourn_days(*phase, per_month);
        cycle_days += *slot;
    }

    // The phase: the first one whose cumulative share reaches `u_phase`.
    // The LAST phase is the initial value rather than a special case, so a
    // `u_phase` of 1.0 — or a cumulative sum a bit short of the total —
    // lands on it instead of falling through.
    let mut chosen = order[4];
    let mut index = 4usize;
    let target_phase = u_phase * cycle_days;
    let mut accumulated = 0.0;
    for (i, phase) in order.iter().enumerate() {
        accumulated += means[i];
        if accumulated >= target_phase {
            chosen = *phase;
            index = i;
            break;
        }
    }

    // The age: the first `k` whose cumulative `sum_{d=1}^{k+1} S(d)` reaches
    // `u_age * E[T]`. `survival` holds `S(k+1)` at the top of each pass,
    // which is 1 at `k = 0` — the empty product.
    let expected = means[index];
    let target_age = u_age * expected;
    let mut survival = 1.0;
    let mut accumulated = 0.0;
    let mut age: u64 = 0;
    loop {
        accumulated += survival;
        if accumulated >= target_age {
            break;
        }
        survival *= 1.0 - hazard_only_transition_probability(chosen, age + 1, per_month);
        if survival < SURVIVAL_FLOOR || age >= AGE_HORIZON {
            break;
        }
        age += 1;
    }

    (chosen, age as f64 / 30.0)
}

#[cfg(test)]
mod stationary_law {
    use super::*;

    /// Both shipped clocks: `cycle_hazard_per_month` at 0.0 (pt-v1 through
    /// pt-v16, the rate read as written) and at 1.0 (pt-v18, per month).
    const CLOCKS: [f64; 2] = [0.0, 1.0];

    /// The chain is cyclic and closes on itself, so five steps of
    /// `next_phase` from expansion enumerate every phase once and the sixth
    /// returns to the start. The order this asserts is the one
    /// [`phase_cycle`] derives rather than a list typed beside it.
    #[test]
    fn the_phase_cycle_closes_on_itself() {
        let order = phase_cycle();
        assert_eq!(order[0], CyclePhase::Expansion);
        assert_eq!(order[1], CyclePhase::Peak);
        assert_eq!(order[2], CyclePhase::Contraction);
        assert_eq!(order[3], CyclePhase::Trough);
        assert_eq!(order[4], CyclePhase::Recovery);
        assert_eq!(
            phase_characteristics(order[4]).next_phase,
            CyclePhase::Expansion,
            "the sixth step must return to the start or the mean sojourns \
             do not sum to a cycle"
        );
    }

    /// The instrument reproduces the engine's own record of itself before it
    /// is asked for anything new: `cycle_hazard_per_month`'s docstring states
    /// a full cycle of 2.6 trading years as written and 9.7 read per month,
    /// and the sum of the mean sojourns gives 2.537 and 9.687.
    #[test]
    fn the_mean_sojourns_reproduce_the_documented_cycle_lengths() {
        let cycle = |per_month: f64| -> f64 {
            phase_cycle()
                .iter()
                .map(|p| mean_sojourn_days(*p, per_month))
                .sum()
        };
        let fast = cycle(0.0);
        let slow = cycle(1.0);
        assert!((fast - 639.390695).abs() < 1e-3, "as written: {fast}");
        assert!((slow - 2441.194589).abs() < 1e-3, "per month: {slow}");
        assert!((fast / 252.0 - 2.6).abs() < 0.1, "{} trading years", fast / 252.0);
        assert!((slow / 252.0 - 9.7).abs() < 0.1, "{} trading years", slow / 252.0);
        // 3.8 and not 30, and the difference is the finding rather than a
        // slack tolerance: `per_day` scales the HAZARD by a thirtieth and
        // the minimum durations are not on that clock at all, so 540 days
        // of every cycle are the same length under both readings. That is
        // also why the fast clock's cohort does not disperse -- the fixed
        // minimums make its cycle nearly deterministic.
        assert!(
            (slow / fast - 3.818).abs() < 0.01,
            "the ratio of the two clocks: {}",
            slow / fast
        );
    }

    /// `pi_i = E[T_i] / sum_j E[T_j]`, per phase, at both clocks.
    #[test]
    fn the_stationary_mix_is_each_phases_share_of_the_cycle() {
        let expected: [[f64; 5]; 2] = [
            [0.385377, 0.104976, 0.205925, 0.098051, 0.205671],
            [0.407254, 0.076380, 0.285345, 0.065528, 0.165493],
        ];
        for (c, per_month) in CLOCKS.iter().enumerate() {
            let means: Vec<f64> = phase_cycle()
                .iter()
                .map(|p| mean_sojourn_days(*p, *per_month))
                .collect();
            let cycle: f64 = means.iter().sum();
            let mut total = 0.0;
            for (i, phase) in phase_cycle().iter().enumerate() {
                let share = means[i] / cycle;
                total += share;
                assert!(
                    (share - expected[c][i]).abs() < 1e-5,
                    "{phase:?} at per_month {per_month}: {share} against {}",
                    expected[c][i]
                );
            }
            assert!((total - 1.0).abs() < 1e-12, "the mix must sum to one");
        }
    }

    /// THE AGE LAW, AS AN IDENTITY RATHER THAN A TABLE.
    ///
    /// Below the minimum duration `p_i` is exactly zero, so `S_i(d)` is
    /// exactly 1 for every `d` up to it, and the stationary chance of finding
    /// a phase younger than its minimum is therefore `min_days / E[T_i]` with
    /// no approximation anywhere. That is a sharp check on the whole
    /// recursion: it fails if the survival, the mean or the minimum is off by
    /// one day.
    #[test]
    fn a_phase_younger_than_its_minimum_has_an_exact_share() {
        for per_month in CLOCKS {
            let order = phase_cycle();
            let means: Vec<f64> = order
                .iter()
                .map(|p| mean_sojourn_days(*p, per_month))
                .collect();
            let cycle: f64 = means.iter().sum();
            let mut lower = 0.0;
            for (i, phase) in order.into_iter().enumerate() {
                // The u_phase that lands in the MIDDLE of this phase's band,
                // so the age assertions below are about the phase named.
                let u_phase = (lower + 0.5 * means[i]) / cycle;
                lower += means[i];
                let min_days = (phase_characteristics(phase).min_months * 30.0).round() as u64;
                let expected = means[i];
                // The largest u_age still giving an age below the minimum.
                let boundary = min_days as f64 / expected;
                let (drawn, below) = stationary_opening(per_month, u_phase, boundary * (1.0 - 1e-12));
                assert_eq!(drawn, phase, "the band midpoint must select the phase");
                let (_, at) = stationary_opening(per_month, u_phase, boundary * (1.0 + 1e-12));
                assert!(
                    (below * 30.0).round() as u64 == min_days - 1,
                    "{phase:?} at per_month {per_month}: {} days just under the \
                     boundary, wanted {}",
                    below * 30.0,
                    min_days - 1
                );
                assert!(
                    (at * 30.0).round() as u64 == min_days,
                    "{phase:?} at per_month {per_month}: {} days just over the \
                     boundary, wanted {min_days}",
                    at * 30.0
                );
            }
        }
    }

    /// The figures the design note's table carries, for the phase that
    /// decides the cohort: 73.05 per cent of stationary expansions are
    /// younger than the 180-day minimum at pt-v16's clock, the median age is
    /// 123 days, and the mean is 129.4. **This is why the age is drawn and
    /// not set to zero.**
    #[test]
    fn a_stationary_expansion_is_usually_younger_than_its_own_minimum() {
        let expected = mean_sojourn_days(CyclePhase::Expansion, 0.0);
        assert!((expected - 246.406548).abs() < 1e-4, "E[T] {expected}");
        assert!(
            (180.0 / expected - 0.7305).abs() < 1e-4,
            "P(age below the minimum) {}",
            180.0 / expected
        );
        let (phase, months) = stationary_opening(0.0, 0.0, 0.5);
        assert_eq!(phase, CyclePhase::Expansion, "u_phase 0.0 is the first phase");
        assert_eq!(
            (months * 30.0).round() as u64,
            123,
            "the median stationary expansion age, in days"
        );
    }

    /// The phase draw is the inverse CDF of `pi`, asserted at the four
    /// interior boundaries rather than by sweeping: either side of each
    /// cumulative share must give the two phases that share it, and 0.0 and
    /// 1.0 must give the first and the last rather than falling through.
    ///
    /// Boundaries, not a sweep, because one call recomputes the whole law
    /// for five phases -- 78,000 multiplies at pt-v18's clock -- and a
    /// sweep fine enough to resolve a share to four places would be a
    /// billion of them for a weaker statement than this one.
    #[test]
    fn the_phase_draw_is_the_inverse_cdf_of_the_stationary_mix() {
        for per_month in CLOCKS {
            let order = phase_cycle();
            let means: Vec<f64> = order
                .iter()
                .map(|p| mean_sojourn_days(*p, per_month))
                .collect();
            let cycle: f64 = means.iter().sum();
            let mut cumulative = 0.0;
            for i in 0..4 {
                cumulative += means[i];
                let u = cumulative / cycle;
                assert_eq!(
                    stationary_opening(per_month, u * (1.0 - 1e-12), 0.0).0,
                    order[i],
                    "just under the {i}th boundary at per_month {per_month}"
                );
                assert_eq!(
                    stationary_opening(per_month, u * (1.0 + 1e-12), 0.0).0,
                    order[i + 1],
                    "just over the {i}th boundary at per_month {per_month}"
                );
            }
            assert_eq!(stationary_opening(per_month, 0.0, 0.0).0, CyclePhase::Expansion);
            assert_eq!(stationary_opening(per_month, 1.0, 0.0).0, CyclePhase::Recovery);
        }
    }

    /// `u_age` at zero is a phase that has just begun, which is the one age
    /// the old construction ever produced.
    #[test]
    fn a_zero_age_draw_is_a_phase_that_has_just_begun() {
        for per_month in CLOCKS {
            for u_phase in [0.0, 0.2, 0.4, 0.6, 0.8, 1.0] {
                let (_, months) = stationary_opening(per_month, u_phase, 0.0);
                assert_eq!(months, 0.0, "per_month {per_month}, u_phase {u_phase}");
            }
        }
    }

    /// The truncation at [`SURVIVAL_FLOOR`] is worth nothing measurable.
    ///
    /// Recomputed at a floor a million times looser, every mean sojourn
    /// agrees to under a ten-thousandth of a day, so the mass beyond the
    /// shipped stop cannot matter to any quantity derived from it. The
    /// contraction on pt-v18's clock is the worst case, and it is the one
    /// whose shape below 1.0 makes the hazard FALL with age.
    #[test]
    fn the_truncated_tail_is_negligible() {
        let loose = |phase: CyclePhase, per_month: f64| -> f64 {
            let mut survival = 1.0;
            let mut total = 0.0;
            let mut day: u64 = 1;
            loop {
                total += survival;
                survival *= 1.0 - hazard_only_transition_probability(phase, day, per_month);
                if survival < 1e-9 || day >= AGE_HORIZON {
                    return total;
                }
                day += 1;
            }
        };
        for per_month in CLOCKS {
            for phase in phase_cycle() {
                let shipped = mean_sojourn_days(phase, per_month);
                let coarse = loose(phase, per_month);
                assert!(
                    (shipped - coarse).abs() < 1e-4,
                    "{phase:?} at per_month {per_month}: {shipped} against {coarse}"
                );
            }
        }
    }

    /// The hazard-only probability is [`check_cycle_transition`]'s own
    /// arithmetic with the ladder taken as the identity, so the two must
    /// agree wherever the ladder adds nothing -- which is every phase whose
    /// macro conditions are unmet. Asserted through
    /// [`get_cycle_transition_probability`], the UI-safe form that computes
    /// the same number without rolling.
    #[test]
    fn the_hazard_only_law_is_the_engines_own_arithmetic() {
        // A macro state on which no ladder condition fires: inflation under
        // 4, the policy rate under 5 and over 3, an upward curve under 1.5
        // of spread, a P/E under 28, unemployment under 8 and growth over 1.
        let mut economy = crate::economy::create_initial_economy_state(
            &crate::economy::InitialEconomyOptions::default(),
        );
        economy.inflation_rate = 2.0;
        economy.federal_funds_rate = 4.0;
        economy.treasury_yield_2y = 3.0;
        economy.treasury_yield_10y = 4.0;
        economy.market_pe = Some(18.0);
        economy.unemployment_rate = 4.0;
        economy.gdp_growth = 2.5;
        for per_month in CLOCKS {
            for phase in phase_cycle() {
                economy.cycle_phase = phase;
                for age_days in [1u64, 59, 60, 119, 120, 179, 180, 240, 400, 900] {
                    economy.months_in_current_phase = age_days as f64 / 30.0;
                    let (engine_p, _) = get_cycle_transition_probability(&economy, per_month);
                    let law = hazard_only_transition_probability(phase, age_days, per_month);
                    assert_eq!(
                        engine_p.to_bits(),
                        law.to_bits(),
                        "{phase:?} at day {age_days}, per_month {per_month}"
                    );
                }
            }
        }
    }
}
