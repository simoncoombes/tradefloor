//! American options: a Leisen-Reimer binomial tree with discrete cash
//! dividends, its early-exercise boundary, and the exercise decision the
//! option dealer will take from it.
//!
//! Library code for pt-v22's single-stock options (phase 3 of
//! `programme/PLAN-pt-v22.md`). Nothing in the engine calls it yet: no
//! switch reads it, no state holds it, and no draw is taken, so every preset
//! is bit-identical with it compiled in.
//!
//! # The lattice
//!
//! Leisen and Reimer, "Binomial models for option valuation -- examining and
//! improving convergence", Applied Mathematical Finance 3(4), 1996. The up
//! and down moves are set from the Peizer-Pratt inversion of the normal
//! distribution so that the tree's terminal distribution is centred on the
//! strike, which makes a European price converge to Black-Scholes at order
//! `1 / n^2` and smoothly, where the Cox-Ross-Rubinstein tree converges at
//! `1 / n` and oscillates with the strike's position between nodes. The
//! smooth convergence is why it is the plan's choice: a chain of quotes from
//! one surface must not wobble between strikes by a lattice artefact, or it
//! hands an agent a butterfly the surface does not contain. `n` must be odd.
//!
//! The Cox-Ross-Rubinstein lattice ([`Lattice::CoxRossRubinstein`]) is here
//! for one reason: Hull's worked examples are on it, so it is what lets the
//! tests check the machinery (dividends, exercise, induction) against
//! published numbers to the cent, step for step.
//!
//! # Dividends
//!
//! Cash dividends are known amounts at known times, so the tree is built on
//! the escrowed price, the spot less the present value of the dividends
//! paid before expiry, and each node's spot is that price plus the present
//! value, at the node's time, of the dividends still to come (Hull, Options,
//! Futures, and Other Derivatives, "Known dollar dividend", in the chapter
//! on binomial trees). The escrowed tree recombines, which a tree that drops
//! the spot by each amount does not. A node at or before a dividend's time
//! holds it (the spot is cum-dividend there), and the first node after holds
//! it no longer, so exercising at the last node before an ex-date captures
//! the dividend, as exercising at the close before the ex-date open does.
//!
//! Times are years from the valuation. [`CashDividend::from_ex_day`] puts a
//! dividend that goes ex at the open of session `e`, valued at the close of
//! session `d`, at `e - d - 1` sessions: at the close of the session before
//! its ex-date, the last moment a holder can exercise and still be paid. A
//! dividend going ex at the next open is therefore at time zero, on the root.
//! The amounts come from [`crate::market::dividends::lookahead`]: the
//! declared amount inside the declaration lead and the rule's projection
//! beyond.
//!
//! A dividend that falls between two nodes is held by the earlier one, so a
//! tree with steps longer than a session would let a call capture a dividend
//! a session early. [`steps_for`] gives at least two steps a session.
//!
//! # Exercise
//!
//! At each node the American value is the larger of exercising now and the
//! discounted expectation of the next step (the hold value). The boundary
//! is where the two meet. [`early_exercise`] is the dealer's rule: exercise
//! when exercising now is worth more than holding, as its own pricer says.
//! For a call that happens only on the session before an ex-date, when the
//! dividend is worth more than the call's time value; for a put, when it is
//! deep enough that the interest on the strike is worth more than the
//! insurance left (high rates). [`exercised_at_expiry`] is the OCC's
//! exercise-by-exception rule at expiry.

use super::calendar::SESSIONS_PER_YEAR;
use super::symbol::Right;
use crate::mathx;

/// The fewest steps [`steps_for`] gives a tree, whatever its life.
pub const MIN_STEPS: usize = 101;

/// The amount in the money, in price units, at which an option is
/// exercised at expiry without an instruction: the OCC's
/// exercise-by-exception threshold of one cent.
pub const EXERCISE_BY_EXCEPTION: f64 = 0.01;

/// Two times closer than this (in years, about a four-millionth of a
/// session) are the same time: a dividend at a node's time is held at it.
const TIME_TOLERANCE: f64 = 1e-9;

/// Exercising must beat holding by this much, times the strike, to count.
/// Without it a deep call at a zero rate, whose hold value equals its
/// exercise value in exact arithmetic, would read as exercised on the last
/// bit of rounding, and the boundary would carry nodes that are not on it.
const EXERCISE_TOLERANCE: f64 = 1e-10;

/// An option to price: its right, the spot, the strike, the continuously
/// compounded annual rate, the annual volatility and the years to expiry.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct OptionSpec {
    pub right: Right,
    /// The spot, cum any dividend in the option's life.
    pub spot: f64,
    pub strike: f64,
    /// Continuously compounded, a year.
    pub rate: f64,
    /// Annual volatility of the escrowed price.
    pub vol: f64,
    /// Years to expiry.
    pub expiry: f64,
}

impl OptionSpec {
    pub fn new(right: Right, spot: f64, strike: f64, rate: f64, vol: f64, expiry: f64) -> Self {
        Self { right, spot, strike, rate, vol, expiry }
    }

    /// The payoff of exercising at spot `s`.
    pub fn intrinsic(&self, s: f64) -> f64 {
        intrinsic(self.right, self.strike, s)
    }
}

/// A cash dividend: the amount per share and its time in years from the
/// valuation, the last moment at which a holder is still paid it.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct CashDividend {
    pub time: f64,
    pub amount: f64,
}

impl CashDividend {
    pub fn new(time: f64, amount: f64) -> Self {
        Self { time, amount }
    }

    /// A dividend going ex at the open of session `ex_day`, valued at the
    /// close of session `day`: at `ex_day - day - 1` sessions, the close
    /// before the ex-date. An ex-date at the next open is at time zero.
    pub fn from_ex_day(day: i64, ex_day: i64, amount: f64) -> Self {
        Self { time: years(ex_day - day - 1), amount }
    }
}

/// `sessions` in years of [`SESSIONS_PER_YEAR`].
pub fn years(sessions: i64) -> f64 {
    sessions as f64 / SESSIONS_PER_YEAR as f64
}

/// Whether the option may be exercised before expiry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum Style {
    American,
    European,
}

/// The tree's up and down moves. See the module docs.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum Lattice {
    /// Leisen-Reimer, with the Peizer-Pratt inversion: the pricer's.
    LeisenReimer,
    /// Cox-Ross-Rubinstein, `u = exp(vol sqrt(dt))`, `d = 1 / u`: the
    /// textbook's, kept to check the machinery against worked examples.
    CoxRossRubinstein,
}

/// The early-exercise boundary at one step of the tree, as the two nodes
/// that bracket it.
///
/// The tree decides at its nodes only, so the boundary at a step is known
/// to lie between the last node exercised and the first held beyond it.
/// An interpolated crossing between them was tried and stepped backwards
/// by up to 0.16 of a node spacing from one step to the next (three
/// at-the-money puts at 401 steps, rates 5% to 10%), because the gap
/// between exercising and holding is near zero on one side of the
/// boundary and quadratic on the other (smooth pasting), so the bracket is
/// what is reported.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct BoundaryPoint {
    /// Years from the valuation.
    pub time: f64,
    /// The exercised node next to the boundary: the highest exercised spot
    /// for a put, the lowest for a call.
    pub exercised: f64,
    /// The held node next to it, beyond the boundary; `None` when every
    /// node at the step is exercised.
    pub held: Option<f64>,
}

/// A tree's answer.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct TreeValue {
    /// The option's value.
    pub value: f64,
    /// The value of holding it to the next step: the discounted expectation
    /// of the first step's values. Equal to `value` unless exercising now is
    /// worth more.
    pub hold: f64,
    /// The first step's slope of value in spot.
    pub delta: f64,
    /// The second step's change in that slope per unit of spot.
    pub gamma: f64,
    /// The steps the tree took.
    pub steps: usize,
    /// The early-exercise boundary at each step where some node is
    /// exercised, in time order; empty for a European option and for one
    /// that is never exercised early.
    pub boundary: Vec<BoundaryPoint>,
}

/// The steps for a tree on an option with `sessions` sessions to expiry:
/// odd, at least [`MIN_STEPS`] and at least two a session.
pub fn steps_for(sessions: i64) -> usize {
    let per_session = if sessions > 0 { 2 * sessions as usize } else { 0 };
    core::cmp::max(MIN_STEPS, per_session) | 1
}

fn intrinsic(right: Right, strike: f64, s: f64) -> f64 {
    match right {
        Right::Call => mathx::max(s - strike, 0.0),
        Right::Put => mathx::max(strike - s, 0.0),
    }
}

/// The Peizer-Pratt inversion (method 2) of the normal distribution for an
/// `n`-step tree: the probability whose binomial tail matches `N(z)`.
fn peizer_pratt(z: f64, n: usize) -> f64 {
    let n = n as f64;
    let a = z / (n + 1.0 / 3.0 + 0.1 / (n + 1.0));
    let root = mathx::sqrt(0.25 - 0.25 * mathx::exp(-a * a * (n + 1.0 / 6.0)));
    if z >= 0.0 {
        0.5 + root
    } else {
        0.5 - root
    }
}

/// Price an option on a binomial tree of `steps` steps.
///
/// `dividends` may hold any dividends: those before the valuation or at or
/// after expiry are ignored. The spot less the present value of the rest
/// must be positive. An option at expiry (`expiry` zero) is worth its
/// intrinsic value.
pub fn price(
    spec: &OptionSpec,
    dividends: &[CashDividend],
    steps: usize,
    style: Style,
    lattice: Lattice,
) -> Result<TreeValue, String> {
    let OptionSpec { right, spot, strike, rate, vol, expiry } = *spec;
    if !(spot > 0.0 && spot.is_finite()) {
        return Err(format!("spot must be positive and finite, got {spot}"));
    }
    if !(strike > 0.0 && strike.is_finite()) {
        return Err(format!("strike must be positive and finite, got {strike}"));
    }
    if !(vol > 0.0 && vol.is_finite()) {
        return Err(format!("volatility must be positive and finite, got {vol}"));
    }
    if !rate.is_finite() {
        return Err(format!("rate must be finite, got {rate}"));
    }
    if !(expiry >= 0.0 && expiry.is_finite()) {
        return Err(format!("expiry must be non-negative and finite, got {expiry}"));
    }
    if let Some(d) = dividends.iter().find(|d| !(d.amount >= 0.0 && d.amount.is_finite() && d.time.is_finite())) {
        return Err(format!("a dividend must have a finite time and a non-negative amount, got {d:?}"));
    }
    if expiry == 0.0 {
        let value = spec.intrinsic(spot);
        let in_money = value > 0.0;
        let delta = match (right, in_money) {
            (Right::Call, true) => 1.0,
            (Right::Put, true) => -1.0,
            _ => 0.0,
        };
        return Ok(TreeValue { value, hold: value, delta, gamma: 0.0, steps: 0, boundary: Vec::new() });
    }
    if steps < 2 {
        return Err(format!("a tree needs at least 2 steps, got {steps}"));
    }
    if lattice == Lattice::LeisenReimer && steps % 2 == 0 {
        return Err(format!("a Leisen-Reimer tree needs an odd number of steps, got {steps}"));
    }

    // The dividends in the option's life: at or after the valuation and
    // before expiry. One at expiry goes ex after the option has settled.
    let live: Vec<CashDividend> = dividends
        .iter()
        .copied()
        .filter(|d| d.amount > 0.0 && d.time >= -TIME_TOLERANCE && d.time < expiry - TIME_TOLERANCE)
        .map(|d| CashDividend { time: mathx::max(d.time, 0.0), amount: d.amount })
        .collect();

    let n = steps;
    let dt = expiry / n as f64;
    // The present value at step i of the dividends still held there.
    let held: Vec<f64> = (0..=n)
        .map(|i| {
            let t = i as f64 * dt;
            live.iter()
                .filter(|d| d.time >= t - TIME_TOLERANCE)
                .map(|d| d.amount * mathx::exp(-rate * (d.time - t)))
                .sum()
        })
        .collect();
    let escrowed = spot - held[0];
    if !(escrowed > 0.0) {
        return Err(format!(
            "the dividends' present value {} is not below the spot {spot}",
            held[0]
        ));
    }

    let growth = mathx::exp(rate * dt);
    let disc = mathx::exp(-rate * dt);
    let (u, d, p) = match lattice {
        Lattice::LeisenReimer => {
            let sd = vol * mathx::sqrt(expiry);
            let d1 = (mathx::log(escrowed / strike) + (rate + 0.5 * vol * vol) * expiry) / sd;
            let d2 = d1 - sd;
            let p = peizer_pratt(d2, n);
            let p_bar = peizer_pratt(d1, n);
            let u = growth * p_bar / p;
            let d = (growth - p * u) / (1.0 - p);
            (u, d, p)
        }
        Lattice::CoxRossRubinstein => {
            let u = mathx::exp(vol * mathx::sqrt(dt));
            let d = 1.0 / u;
            (u, d, (growth - d) / (u - d))
        }
    };
    if !(p > 0.0 && p < 1.0 && d > 0.0 && u > d) {
        return Err(format!(
            "the lattice has no risk-neutral probability (u {u}, d {d}, p {p}); take more steps"
        ));
    }
    let q = 1.0 - p;
    let ratio = u / d;

    // The spot at node j of step i: the escrowed price moved up j times
    // and down i - j, plus the dividends still held.
    let spots = |i: usize, out: &mut Vec<f64>| {
        out.clear();
        let mut s = escrowed * mathx::pow(d, i as f64);
        for _ in 0..=i {
            out.push(s + held[i]);
            s *= ratio;
        }
    };

    let mut s_row = Vec::with_capacity(n + 1);
    spots(n, &mut s_row);
    let mut v: Vec<f64> = s_row.iter().map(|&s| spec.intrinsic(s)).collect();
    let american = style == Style::American;
    let tol = EXERCISE_TOLERANCE * strike;
    let mut boundary = Vec::new();
    let mut gaps = Vec::with_capacity(n + 1);
    let mut step2: Option<(Vec<f64>, Vec<f64>)> = None;
    let mut step1: Option<(Vec<f64>, Vec<f64>)> = None;
    let mut hold_at_root = 0.0;

    for i in (0..n).rev() {
        spots(i, &mut s_row);
        gaps.clear();
        let mut exercised_any = false;
        for j in 0..=i {
            let hold = disc * (p * v[j + 1] + q * v[j]);
            let exercise = spec.intrinsic(s_row[j]);
            let gap = exercise - hold;
            if american && exercise > 0.0 && gap > tol {
                v[j] = exercise;
                exercised_any = true;
            } else {
                v[j] = hold;
            }
            gaps.push(gap);
            if i == 0 {
                hold_at_root = hold;
            }
        }
        if exercised_any {
            boundary.push(boundary_at(right, i as f64 * dt, &s_row, &gaps, tol));
        }
        if i == 2 {
            step2 = Some((s_row.clone(), v[..3].to_vec()));
        } else if i == 1 {
            step1 = Some((s_row.clone(), v[..2].to_vec()));
        }
    }
    boundary.reverse();

    let (s1, v1) = step1.expect("a tree of two or more steps has a first step");
    let delta = (v1[1] - v1[0]) / (s1[1] - s1[0]);
    let (s2, v2) = step2.expect("a tree of two or more steps has a second step");
    let slope_up = (v2[2] - v2[1]) / (s2[2] - s2[1]);
    let slope_down = (v2[1] - v2[0]) / (s2[1] - s2[0]);
    let gamma = (slope_up - slope_down) / (0.5 * (s2[2] - s2[0]));

    Ok(TreeValue { value: v[0], hold: hold_at_root, delta, gamma, steps: n, boundary })
}

/// The boundary at one step from each node's exercise-less-hold gap. A
/// put's exercised nodes run up from the lowest spot, a call's down from the
/// highest.
fn boundary_at(right: Right, time: f64, spots: &[f64], gaps: &[f64], tol: f64) -> BoundaryPoint {
    let exercised = |j: usize| gaps[j] > tol;
    let (edge, next) = match right {
        Right::Put => {
            let edge = (0..spots.len()).rev().find(|&j| exercised(j)).expect("a step with an exercised node");
            (edge, if edge + 1 < spots.len() { Some(edge + 1) } else { None })
        }
        Right::Call => {
            let edge = (0..spots.len()).find(|&j| exercised(j)).expect("a step with an exercised node");
            (edge, if edge > 0 { Some(edge - 1) } else { None })
        }
    };
    BoundaryPoint { time, exercised: spots[edge], held: next.map(|k| spots[k]) }
}

/// The dealer's early-exercise decision on an American option it holds,
/// at a close, from its own pricer.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct ExerciseDecision {
    /// Whether to exercise at this close.
    pub exercise: bool,
    /// The payoff of exercising now.
    pub intrinsic: f64,
    /// The tree's value of holding to the next step.
    pub hold: f64,
}

/// Whether to exercise an American option now: when its payoff beats the
/// value of holding it, on a tree of `steps` steps.
///
/// A call is exercised only when a dividend falls at time zero, an ex-date
/// at the next open: with a non-negative rate a call is never worth more
/// dead than alive except to collect a dividend (Merton, "Theory of rational
/// option pricing", Bell Journal of Economics 4(1), 1973), and a dividend
/// further off is better collected by exercising at its own eve, when the
/// tree may say so again. The guard makes plan row AX1 (calls only on the
/// session before an ex-date) hold by construction, including at a negative
/// rate, where the tree alone could exercise a deep call. An option at
/// expiry is not exercised early; [`exercised_at_expiry`] settles it.
pub fn early_exercise(spec: &OptionSpec, dividends: &[CashDividend], steps: usize) -> Result<ExerciseDecision, String> {
    let intrinsic = spec.intrinsic(spec.spot);
    if spec.expiry == 0.0 {
        price(spec, dividends, steps, Style::American, Lattice::LeisenReimer)?;
        return Ok(ExerciseDecision { exercise: false, intrinsic, hold: intrinsic });
    }
    let tree = price(spec, dividends, steps, Style::American, Lattice::LeisenReimer)?;
    let eve = dividends
        .iter()
        .any(|d| d.amount > 0.0 && d.time.abs() <= TIME_TOLERANCE && d.time < spec.expiry);
    let allowed = match spec.right {
        Right::Call => eve,
        Right::Put => true,
    };
    let exercise = allowed && intrinsic > 0.0 && intrinsic - tree.hold > EXERCISE_TOLERANCE * spec.strike;
    Ok(ExerciseDecision { exercise, intrinsic, hold: tree.hold })
}

/// Whether an option is exercised at expiry on a settlement price of
/// `settlement`: in the money by at least [`EXERCISE_BY_EXCEPTION`], the
/// OCC's exercise-by-exception rule. Prices are in cents, so the comparison
/// allows for the last bit of a difference of two of them.
pub fn exercised_at_expiry(right: Right, strike: f64, settlement: f64) -> bool {
    intrinsic(right, strike, settlement) >= EXERCISE_BY_EXCEPTION - 1e-9
}

#[cfg(test)]
mod tests {
    use super::*;

    const LR: Lattice = Lattice::LeisenReimer;
    const CRR: Lattice = Lattice::CoxRossRubinstein;

    fn norm_cdf(x: f64) -> f64 {
        0.5 * mathx::erfc(-x / core::f64::consts::SQRT_2)
    }

    /// Black-Scholes on a spot with no dividends: the reference the
    /// European tree converges to. Kept here so this module does not
    /// depend on the index options' pricer.
    fn black_scholes(spec: &OptionSpec) -> f64 {
        let OptionSpec { right, spot, strike, rate, vol, expiry } = *spec;
        let sd = vol * mathx::sqrt(expiry);
        let d1 = (mathx::log(spot / strike) + (rate + 0.5 * vol * vol) * expiry) / sd;
        let d2 = d1 - sd;
        let df = mathx::exp(-rate * expiry);
        match right {
            Right::Call => spot * norm_cdf(d1) - strike * df * norm_cdf(d2),
            Right::Put => strike * df * norm_cdf(-d2) - spot * norm_cdf(-d1),
        }
    }

    fn value(spec: &OptionSpec, divs: &[CashDividend], n: usize, style: Style, lattice: Lattice) -> f64 {
        price(spec, divs, n, style, lattice).unwrap().value
    }

    #[test]
    fn the_reference_black_scholes_reads_hulls_example() {
        // Hull, Example 15.6: S 42, K 40, r 10%, vol 20%, six months.
        let c = black_scholes(&OptionSpec::new(Right::Call, 42.0, 40.0, 0.10, 0.20, 0.5));
        let p = black_scholes(&OptionSpec::new(Right::Put, 42.0, 40.0, 0.10, 0.20, 0.5));
        assert!((c - 4.76).abs() < 0.005, "{c}");
        assert!((p - 0.81).abs() < 0.005, "{p}");
    }

    #[test]
    fn a_european_tree_converges_to_black_scholes_at_second_order() {
        for right in [Right::Call, Right::Put] {
            for &(s, k, r, vol, t) in &[
                (100.0, 100.0, 0.05, 0.20, 1.0),
                (42.0, 40.0, 0.10, 0.20, 0.5),
                (50.0, 60.0, 0.03, 0.45, 0.25),
                (80.0, 70.0, 0.00, 0.30, 2.0),
            ] {
                let spec = OptionSpec::new(right, s, k, r, vol, t);
                let bs = black_scholes(&spec);
                let errs: Vec<f64> = [51, 101, 201, 401]
                    .iter()
                    .map(|&n| (value(&spec, &[], n, Style::European, LR) - bs).abs())
                    .collect();
                assert!(errs[3] < 1e-4, "{right:?} {s} {k}: {errs:?}");
                // Doubling the steps cuts the error by about four: the
                // order Leisen and Reimer prove for odd trees.
                for w in errs.windows(2) {
                    assert!(w[1] < w[0] / 3.0 || w[1] < 1e-9, "{right:?} {s} {k}: {errs:?}");
                }
            }
        }
    }

    #[test]
    fn an_american_call_without_dividends_is_the_european() {
        for &(s, k, r, vol, t) in &[
            (100.0, 100.0, 0.05, 0.20, 1.0),
            (150.0, 100.0, 0.08, 0.30, 0.5),
            (60.0, 100.0, 0.02, 0.50, 3.0),
            (100.0, 90.0, 0.00, 0.25, 1.0),
        ] {
            let spec = OptionSpec::new(Right::Call, s, k, r, vol, t);
            let a = price(&spec, &[], 201, Style::American, LR).unwrap();
            let e = value(&spec, &[], 201, Style::European, LR);
            assert!((a.value - e).abs() <= 1e-12 * s, "{s} {k}: {} against {e}", a.value);
            assert!(a.boundary.is_empty(), "{s} {k}: exercised at {:?}", a.boundary[0]);
            let decision = early_exercise(&spec, &[], 201).unwrap();
            assert!(!decision.exercise);
        }
    }

    #[test]
    fn an_american_put_is_worth_more_than_the_european() {
        for &(s, k, r, vol, t) in &[
            (100.0, 100.0, 0.05, 0.20, 1.0),
            (50.0, 50.0, 0.10, 0.40, 5.0 / 12.0),
            (80.0, 100.0, 0.08, 0.30, 0.5),
            (120.0, 100.0, 0.03, 0.25, 2.0),
        ] {
            let spec = OptionSpec::new(Right::Put, s, k, r, vol, t);
            let a = value(&spec, &[], 201, Style::American, LR);
            let e = value(&spec, &[], 201, Style::European, LR);
            assert!(a > e + 1e-4, "{s} {k}: {a} against {e}");
            assert!(a >= spec.intrinsic(s));
        }
        // At a zero rate a put is never exercised early, so the two agree.
        let spec = OptionSpec::new(Right::Put, 90.0, 100.0, 0.0, 0.30, 1.0);
        let a = price(&spec, &[], 201, Style::American, LR).unwrap();
        assert!((a.value - value(&spec, &[], 201, Style::European, LR)).abs() < 1e-12);
        assert!(a.boundary.is_empty());
    }

    #[test]
    fn hulls_five_step_american_put() {
        // Hull, Example 21.1 (Figure 21.4 in the 8th edition): S 50, K 50,
        // r 10%, vol 40%, five months, five one-month steps: 4.49.
        let spec = OptionSpec::new(Right::Put, 50.0, 50.0, 0.10, 0.40, 5.0 / 12.0);
        let v = value(&spec, &[], 5, Style::American, CRR);
        assert!((v - 4.49).abs() < 0.005, "{v}");
        // Hull gives the converged value as 4.28 (4.263, 4.272, 4.278 and
        // 4.283 at 30, 50, 100 and 500 steps); Leisen-Reimer reaches it at
        // 101 steps, and the European is Black-Scholes' 4.08.
        let lr = value(&spec, &[], 101, Style::American, LR);
        assert!((lr - 4.28).abs() < 0.005, "{lr}");
        let crr = value(&spec, &[], 500, Style::American, CRR);
        assert!((crr - 4.283).abs() < 0.0005, "{crr}");
        assert!((lr - crr).abs() < 0.002, "{lr} against {crr}");
        assert!((black_scholes(&spec) - 4.08).abs() < 0.005);
    }

    #[test]
    fn hulls_five_step_put_with_a_known_dividend() {
        // Hull, Example 21.3 (Figure 21.10 in the 8th edition): S 52, K 50,
        // r 10%, vol 40%, five months, a dividend of 2.06 in three and a
        // half months, five steps on the escrowed price: 4.44.
        let spec = OptionSpec::new(Right::Put, 52.0, 50.0, 0.10, 0.40, 5.0 / 12.0);
        let divs = [CashDividend::new(3.5 / 12.0, 2.06)];
        let v = value(&spec, &divs, 5, Style::American, CRR);
        assert!((v - 4.44).abs() < 0.005, "{v}");
        // The escrowed price is 50.00, as Hull's tree starts.
        let pv = 2.06 * mathx::exp(-0.10 * 3.5 / 12.0);
        assert!((52.0 - pv - 50.0).abs() < 0.005);
    }

    #[test]
    fn hulls_dividend_call_and_blacks_approximation() {
        // Hull, Example 15.12: S 40, K 40, r 9%, vol 30%, six months, 0.50
        // in two and in five months. The European on the escrowed price is
        // 3.67, the one expiring at the last ex-date 3.52, so Black's
        // approximation to the American is 3.67.
        let spec = OptionSpec::new(Right::Call, 40.0, 40.0, 0.09, 0.30, 0.5);
        let divs = [CashDividend::new(2.0 / 12.0, 0.5), CashDividend::new(5.0 / 12.0, 0.5)];
        let pv: f64 = divs.iter().map(|d| d.amount * mathx::exp(-0.09 * d.time)).sum();
        let escrowed = OptionSpec { spot: 40.0 - pv, ..spec };
        assert!((black_scholes(&escrowed) - 3.67).abs() < 0.005);
        let short = OptionSpec { spot: 40.0 - 0.5 * mathx::exp(-0.09 * 2.0 / 12.0), expiry: 5.0 / 12.0, ..spec };
        assert!((black_scholes(&short) - 3.52).abs() < 0.005);
        // The European tree with the dividends converges to the escrowed
        // Black-Scholes, and the American is at or above Black's value.
        let e = value(&spec, &divs, 401, Style::European, LR);
        assert!((e - black_scholes(&escrowed)).abs() < 1e-4, "{e}");
        let a = value(&spec, &divs, 401, Style::American, LR);
        assert!(a >= 3.67 - 0.005 && a > e, "{a} {e}");
    }

    #[test]
    fn the_put_boundary_rises_to_the_strike() {
        for &(r, vol, t) in &[(0.10, 0.40, 5.0 / 12.0), (0.05, 0.20, 1.0), (0.08, 0.30, 2.0)] {
            let spec = OptionSpec::new(Right::Put, 100.0, 100.0, r, vol, t);
            let tree = price(&spec, &[], 401, Style::American, LR).unwrap();
            let b = &tree.boundary;
            assert!(b.len() > 300, "{r} {vol} {t}: {} points", b.len());
            for w in b.windows(2) {
                assert!(w[1].time > w[0].time);
                let held = w[1].held.expect("a node above the boundary is held");
                assert!(w[1].exercised < held && w[1].exercised < 100.0);
                // The exercise region never shrinks: every node at the next
                // step at or below a spot exercised now is exercised too.
                assert!(held > w[0].exercised, "{r} {vol} {t}: {:?} then {:?}", w[0], w[1]);
            }
            // The bracket's midpoint, a tenth of the boundary's steps
            // apart, rises strictly.
            let mid = |p: &BoundaryPoint| 0.5 * (p.exercised + p.held.unwrap());
            let stride = b.len() / 10;
            for k in (stride..b.len()).step_by(stride) {
                assert!(mid(&b[k]) > mid(&b[k - stride]), "{r} {vol} {t}: {:?} then {:?}", b[k - stride], b[k]);
            }
            // At the last step before expiry the boundary is at the strike,
            // and at the first it sits well below.
            let last = b.last().unwrap();
            assert!(last.time > 0.99 * t && last.held.unwrap() > 99.0, "{last:?}");
            assert!(mid(&b[0]) < mid(last) - 5.0, "{:?} {last:?}", b[0]);
        }
    }

    #[test]
    fn a_call_is_exercised_only_on_the_eve_and_deeper_with_more_life_left() {
        // A call is exercised early only at the node just before an
        // ex-date, and the deeper the boundary the longer the option has
        // left after it.
        let mut previous = 0.0;
        for t in [0.15, 0.25, 0.4] {
            let spec = OptionSpec::new(Right::Call, 100.0, 100.0, 0.05, 0.25, t);
            let divs = [CashDividend::new(0.1, 3.0)];
            let tree = price(&spec, &divs, 401, Style::American, LR).unwrap();
            assert_eq!(tree.boundary.len(), 1, "{t}: {:?}", tree.boundary);
            let point = tree.boundary[0];
            assert!(point.time <= 0.1 && point.time > 0.1 - t / 401.0, "{point:?}");
            let held = point.held.expect("a node below the boundary is held");
            assert!(held < point.exercised && held > 100.0, "{t}: {point:?}");
            assert!(point.exercised > previous, "{t}: {point:?}");
            previous = point.exercised;
        }
        // A dividend worth less than the interest on the strike over the
        // life left after it is not exercised for at all.
        let long = OptionSpec::new(Right::Call, 100.0, 100.0, 0.05, 0.25, 1.0);
        let tree = price(&long, &[CashDividend::new(0.1, 3.0)], 401, Style::American, LR).unwrap();
        assert!(tree.boundary.is_empty(), "{:?}", tree.boundary);
    }

    #[test]
    fn dividends_count_only_before_their_ex_dates() {
        let spec = OptionSpec::new(Right::Call, 100.0, 100.0, 0.05, 0.25, 0.5);
        let none = value(&spec, &[], 201, Style::American, LR);
        // A dividend at or after expiry, or before the valuation, changes
        // nothing.
        for time in [0.5, 0.75, -0.01] {
            let v = value(&spec, &[CashDividend::new(time, 2.0)], 201, Style::American, LR);
            assert_eq!(v, none, "{time}");
        }
        // One inside the life lowers the call and raises the put, and the
        // call's American premium is the dividend's capture.
        let divs = [CashDividend::new(0.25, 2.0)];
        let with = value(&spec, &divs, 201, Style::American, LR);
        let euro = value(&spec, &divs, 201, Style::European, LR);
        assert!(with < none && with > euro, "{with} {euro} {none}");
        let put = OptionSpec { right: Right::Put, ..spec };
        assert!(value(&put, &divs, 201, Style::American, LR) > value(&put, &[], 201, Style::American, LR));
        // The tree reads the spot as cum-dividend: a dividend paid at the
        // next open is captured by exercising now.
        let deep = OptionSpec::new(Right::Call, 150.0, 100.0, 0.05, 0.25, 0.5);
        let eve = [CashDividend::from_ex_day(10, 11, 5.0)];
        assert_eq!(eve[0].time, 0.0);
        let decision = early_exercise(&deep, &eve, 201).unwrap();
        assert!(decision.exercise && decision.intrinsic > decision.hold, "{decision:?}");
        let tree = price(&deep, &eve, 201, Style::American, LR).unwrap();
        assert_eq!(tree.value, 50.0);
        // A session earlier the dividend is a session off, so the call is
        // held tonight, however the tree places it.
        let ahead = [CashDividend::from_ex_day(10, 12, 5.0)];
        assert!(!early_exercise(&deep, &ahead, 201).unwrap().exercise);
        // A dividend smaller than the time value is not worth exercising
        // for.
        let small = [CashDividend::from_ex_day(10, 11, 0.01)];
        let atm = OptionSpec { spot: 101.0, ..deep };
        let d = early_exercise(&atm, &small, 201).unwrap();
        assert!(!d.exercise && d.hold > d.intrinsic, "{d:?}");
    }

    #[test]
    fn a_european_with_a_dividend_is_black_scholes_on_the_escrowed_spot() {
        // On a two-step-a-session tree, the tree's European call with a
        // dividend is Black-Scholes on the escrowed spot, wherever the
        // dividend falls between nodes.
        let spec = OptionSpec::new(Right::Call, 100.0, 95.0, 0.04, 0.30, years(42));
        for ex in [1, 2, 11, 30, 42] {
            let divs = [CashDividend::from_ex_day(0, ex, 1.5)];
            let e = value(&spec, &divs, steps_for(42), Style::European, LR);
            let pv = if divs[0].time < spec.expiry { 1.5 * mathx::exp(-0.04 * divs[0].time) } else { 0.0 };
            let bs = black_scholes(&OptionSpec { spot: 100.0 - pv, ..spec });
            assert!((e - bs).abs() < 2e-4, "{ex}: {e} against {bs}");
        }
    }

    #[test]
    fn the_dealer_exercises_a_deep_put_when_rates_are_high() {
        let deep = OptionSpec::new(Right::Put, 60.0, 100.0, 0.08, 0.20, 0.5);
        let d = early_exercise(&deep, &[], 201).unwrap();
        assert!(d.exercise && d.intrinsic == 40.0 && d.hold < 40.0, "{d:?}");
        // The same put at a zero rate is held, and so is one near the money.
        assert!(!early_exercise(&OptionSpec { rate: 0.0, ..deep }, &[], 201).unwrap().exercise);
        assert!(!early_exercise(&OptionSpec { spot: 95.0, ..deep }, &[], 201).unwrap().exercise);
        // A dividend at the next open keeps a put alive: the price drops.
        let eve = [CashDividend::new(0.0, 3.0)];
        let near = OptionSpec { spot: 78.0, ..deep };
        assert!(early_exercise(&near, &[], 201).unwrap().exercise);
        assert!(!early_exercise(&near, &eve, 201).unwrap().exercise);
    }

    #[test]
    fn the_greeks_match_black_scholes_on_a_european() {
        let spec = OptionSpec::new(Right::Call, 100.0, 100.0, 0.05, 0.20, 1.0);
        let tree = price(&spec, &[], 401, Style::European, LR).unwrap();
        let sd = 0.20;
        let d1 = (0.05 + 0.5 * 0.04) / sd;
        let bs_delta = norm_cdf(d1);
        let bs_gamma = mathx::exp(-0.5 * d1 * d1) / mathx::sqrt(2.0 * core::f64::consts::PI) / (100.0 * sd);
        assert!((tree.delta - bs_delta).abs() < 2e-3, "{} {bs_delta}", tree.delta);
        assert!((tree.gamma - bs_gamma).abs() < 2e-3, "{} {bs_gamma}", tree.gamma);
        let put = price(&OptionSpec { right: Right::Put, ..spec }, &[], 401, Style::European, LR).unwrap();
        assert!((put.delta - (bs_delta - 1.0)).abs() < 2e-3);
    }

    #[test]
    fn exercise_by_exception_at_a_cent() {
        assert!(exercised_at_expiry(Right::Call, 50.0, 50.01));
        assert!(!exercised_at_expiry(Right::Call, 50.0, 50.0));
        assert!(!exercised_at_expiry(Right::Call, 50.0, 50.009));
        assert!(exercised_at_expiry(Right::Put, 42.5, 42.49));
        assert!(!exercised_at_expiry(Right::Put, 42.5, 42.5));
        // At expiry a tree is worth its payoff and nothing is exercised
        // early.
        let spec = OptionSpec::new(Right::Put, 40.0, 42.5, 0.05, 0.3, 0.0);
        assert_eq!(value(&spec, &[], 101, Style::American, LR), 2.5);
        assert!(!early_exercise(&spec, &[], 101).unwrap().exercise);
    }

    #[test]
    fn bad_inputs_are_refused() {
        let ok = OptionSpec::new(Right::Put, 100.0, 100.0, 0.05, 0.2, 1.0);
        assert!(price(&ok, &[], 100, Style::American, LR).is_err());
        assert!(price(&ok, &[], 1, Style::American, CRR).is_err());
        assert!(price(&OptionSpec { vol: 0.0, ..ok }, &[], 101, Style::American, LR).is_err());
        assert!(price(&OptionSpec { spot: f64::NAN, ..ok }, &[], 101, Style::American, LR).is_err());
        assert!(price(&ok, &[CashDividend::new(0.5, 120.0)], 101, Style::American, LR).is_err());
        assert!(price(&ok, &[CashDividend::new(0.5, -1.0)], 101, Style::American, LR).is_err());
        assert_eq!(steps_for(0), MIN_STEPS);
        assert_eq!(steps_for(21), MIN_STEPS);
        assert_eq!(steps_for(252), 505);
        assert_eq!(steps_for(51) % 2, 1);
    }

    #[test]
    fn the_lookahead_feeds_the_tree() {
        use crate::market::dividends::{lookahead, phase_for, DividendState};
        use crate::params::ModelParams;
        let mut p = ModelParams::pt_v1();
        p.dividend_payout_share = 1.0;
        let state = DividendState {
            payout: 0.5,
            target_yield: 0.03,
            price_ema: 100.0,
            amount: 0.75,
            declared: true,
            accrual: 0.0,
            paid_today: 0.0,
        };
        let ph = phase_for("ACME");
        // The session before an ex-date, an option expiring 84 sessions on.
        let day = ph + 63 * 2 - 1;
        let expiry = day + 84;
        let ahead = lookahead(&p, "ACME", Some(&state), day, 100.0, expiry);
        assert_eq!(ahead.len(), 2);
        let divs: Vec<CashDividend> =
            ahead.iter().map(|u| CashDividend::from_ex_day(day, u.ex_day, u.amount)).collect();
        assert_eq!(divs[0].time, 0.0);
        assert_eq!(divs[1].time, years(63));
        let spec = OptionSpec::new(Right::Call, 100.0, 90.0, 0.04, 0.25, years(expiry - day));
        let with = value(&spec, &divs, steps_for(84), Style::American, LR);
        let without = value(&spec, &[], steps_for(84), Style::American, LR);
        assert!(with < without);
    }
}
