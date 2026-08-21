//! Mispricing — the daily `s`-process, ported from `src/lib/engine/mispricing.ts`.
//!
//! # What this module is, and what it is NOT
//!
//! **It is not the model the tick loop runs.** `mispricing.ts` has zero callers in
//! `src/`: the shipping game uses a per-tick inline variant inside `market.ts`
//! (WP4), which applies the same ideas at 1/390 of a day per tick and never
//! calls through here. The TypeScript file's own header claims to own the live
//! price model, and that claim is wrong — see the port plan, Phase 2 course
//! correction, item 4. It is deliberately not repeated above.
//!
//! What this module actually is: the **daily-step API for the Python library**
//! and the home of the closed-form analytics ([`characteristic_root_moduli`],
//! [`impulse_response`]) that let a test PROVE stationarity rather than observe
//! it over a long simulation.
//!
//! # The process
//!
//! ```text
//! s_t = φ·s_{t-1} + θ·(s_{t-1} − s_{t-2}) + ε_t + shocks_t
//! ```
//!
//! φ comes from a stated half-life rather than a bare coefficient; θ is a
//! bounded momentum term — yesterday's *change* in mispricing partially
//! continues, which is what herding is. As an AR(2) with `a₁ = φ+θ` and
//! `a₂ = −θ`, it is stationary iff `a₂ > −1`, `a₁ + a₂ < 1`, `a₂ − a₁ < 1`,
//! which for `0 ≤ φ,θ < 1` reduces to exactly `φ < 1` and `θ < 1`.
//!
//! Pure and deterministic: the caller supplies the innovation, already drawn
//! from the seeded, GARCH-scaled stream. No RNG in here.
//!
//! # Tier
//!
//! **Tier 1 — hard bit-identical — for everything except [`apply_mispricing`]**,
//! whose single `exp` makes it Tier 2. In practice that `exp` sees arguments
//! bounded to `[-0.9, 0.9]`, and all 375 recorded cases match exactly.
//!
//! # Faithfulness notes
//!
//! - **`clamp` is a ternary, not `f64::clamp`.** NaN passes through unchanged
//!   and `-0` stays `-0`. See [`crate::mathx::clamp`].
//! - **Evaluation order in [`step_mispricing`] is contractual**, and `momentum`
//!   is computed from the UNCLAMPED `s` and `s_prev`. No reassociation, and
//!   emphatically no `mul_add`: a fused multiply-add changes the last bit.
//! - **`MISPRICING_PHI` is hardcoded from its recorded bits**, never computed.
//!   See the constant.

use crate::mathx::{self, clamp};

/// Trading days for half of a mispricing to decay, absent shocks.
pub const MISPRICING_HALF_LIFE_DAYS: f64 = 60.0;

/// Daily AR(1) coefficient implied by the half-life.
///
/// **Hardcoded from V8's recorded bits, deliberately not computed.** The
/// TypeScript evaluates `Math.pow(0.5, 1/60)` at module load, so the value is
/// a transcendental result and φ compounds through every step of the process
/// — a one-ULP difference is not a rounding curiosity here, it is a different
/// half-life applied 100,000 times.
///
/// Measured on this toolchain, both `f64::powf` and `libm::pow` reproduce
/// V8's bits exactly for this input, so the hardcode is belt-and-braces
/// rather than a workaround for a known-wrong `pow`. It stays hardcoded
/// anyway: agreeing today on one platform is not a guarantee, and the bits
/// ARE the contract — `mispricing-constants.json` records them, and
/// `tests/mispricing_parity.rs` checks this constant against that file.
pub const MISPRICING_PHI: f64 = f64::from_bits(0x3FEF_A1E8_27A1_B38C);

/// Herding: fraction of yesterday's re-rating that continues today.
pub const MOMENTUM_THETA: f64 = 0.25;

/// Hard bound on `|s|`.
///
/// `exp(±0.9) ≈ [0.41, 2.46]×` fair value — beyond that a "mispricing" story
/// stops being credible even in a mania, and the bound guarantees no numeric
/// excursion regardless of shock pathology.
pub const MISPRICING_CAP: f64 = 0.9;

/// Bound on any single day's total shock input (news + flow + events).
pub const DAILY_SHOCK_CAP: f64 = 0.15;

/// Crowd valuation gain per day on `s`. Subtracts from the effective φ.
pub const CROWD_VALUATION_GAIN: f64 = 0.006;

/// Crowd herding gain per day on yesterday's Δs. Adds to the effective θ.
pub const CROWD_MOMENTUM_GAIN: f64 = 0.02;

/// Bound on the crowd's daily log-price shock.
///
/// Saturation only ever helps: a capped valuation lean reverts more slowly
/// than an uncapped one would, and a capped momentum lean stops herding
/// exactly when herding is largest. Well inside [`DAILY_SHOCK_CAP`], so the
/// crowd can never dominate a news day.
pub const CROWD_LEAN_CAP: f64 = 0.02;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MispricingState {
    /// Current log-mispricing.
    pub s: f64,
    /// Previous day's value, for the momentum term.
    pub s_prev: f64,
}

/// The TypeScript default is `createMispricingState(0)`.
impl Default for MispricingState {
    fn default() -> Self {
        create_mispricing_state(0.0)
    }
}

pub fn create_mispricing_state(initial: f64) -> MispricingState {
    let s = clamp(initial, -MISPRICING_CAP, MISPRICING_CAP);
    MispricingState { s, s_prev: s }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MispricingInputs {
    /// GARCH-scaled daily innovation, zero-mean and already sized by the
    /// caller. **Not clamped** — only `shock` is.
    pub innovation: f64,
    /// Sum of the day's directional shocks in log-price terms: news impacts,
    /// net order-flow pressure, squeeze/cascade effects, whale events.
    pub shock: f64,
}

/// One daily step. Pure: returns the new state, does not mutate.
pub fn step_mispricing(state: &MispricingState, inputs: &MispricingInputs) -> MispricingState {
    let shock = clamp(inputs.shock, -DAILY_SHOCK_CAP, DAILY_SHOCK_CAP);
    // From the UNCLAMPED s and s_prev, and before the sum.
    let momentum = MOMENTUM_THETA * (state.s - state.s_prev);
    // `((φ·s + momentum) + innovation) + shock`, strictly left to right.
    // Rust's `+` is left-associative and does not auto-fuse into an FMA, so
    // this expression is the contract as written — but do not "simplify" it
    // with `mul_add`, which would change the last bit.
    let next = MISPRICING_PHI * state.s + momentum + inputs.innovation + shock;
    MispricingState {
        s: clamp(next, -MISPRICING_CAP, MISPRICING_CAP),
        // The INPUT s, unclamped — so a hand-built state with |s| > cap
        // propagates that value into s_prev exactly once.
        s_prev: state.s,
    }
}

/// `price = fairValue × exp(s)`, floored to stay a valid price.
///
/// The floor is `Math.max`, so a NaN propagates rather than being replaced by
/// `0.01` — which is what `f64::max` would do. A negative fair value survives
/// the multiply and is then floored, so this never returns a negative price;
/// NaN is the sole exception.
pub fn apply_mispricing(fair_value: f64, s: f64) -> f64 {
    mathx::max(
        0.01,
        fair_value * mathx::exp(clamp(s, -MISPRICING_CAP, MISPRICING_CAP)),
    )
}

/// The crowd's net flow lean for one day, in log-price terms.
///
/// The crowd net-buys what trades below fair value and net-sells what trades
/// above it (the restoring force), and chases yesterday's re-rating a little
/// (herding, deliberately much smaller).
///
/// * `s` — current log-mispricing.
/// * `momentum` — previous day's change in `s`.
pub fn crowd_lean(s: f64, momentum: f64) -> f64 {
    // The unary minus binds to the CONSTANT, so `s = -0` gives `+0` here
    // rather than `-0`. Rust's precedence matches JavaScript's.
    let lean = -CROWD_VALUATION_GAIN * s + CROWD_MOMENTUM_GAIN * momentum;
    clamp(lean, -CROWD_LEAN_CAP, CROWD_LEAN_CAP)
}

/// AR(2) characteristic-root moduli with the crowd folded in.
///
/// In the linear (unsaturated) region the valuation lean lowers φ and the
/// momentum lean raises θ. Stationary iff both moduli are < 1 — the
/// checkable inequality the design demands of every gain living inside the
/// `s`-dynamics.
pub fn crowd_adjusted_root_moduli() -> (f64, f64) {
    characteristic_root_moduli(
        Some(MISPRICING_PHI - CROWD_VALUATION_GAIN),
        Some(MOMENTUM_THETA + CROWD_MOMENTUM_GAIN),
    )
}

/// Moduli of the AR(2) characteristic roots. Stationary iff both < 1.
///
/// `z² − (φ+θ)z + θ = 0`.
///
/// `None` selects the TypeScript default argument, so a `None` call depends
/// on [`MISPRICING_PHI`] — a φ one ULP out shows up here too.
pub fn characteristic_root_moduli(phi: Option<f64>, theta: Option<f64>) -> (f64, f64) {
    let phi = phi.unwrap_or(MISPRICING_PHI);
    let theta = theta.unwrap_or(MOMENTUM_THETA);

    let a1 = phi + theta;
    let a2 = -theta;
    let disc = a1 * a1 + 4.0 * a2;

    // `disc >= 0`, so a disc of exactly -0 takes the REAL branch: `-0 >= 0`
    // is true. Written as the source writes it.
    //
    // Worth knowing, though: at the boundary the two branches AGREE, so the
    // choice is unobservable through the return value. `disc == 0` means
    // `a₁² = 4θ`, and then the real branch gives `|a₁/2|` twice while the
    // complex branch gives `√θ = |a₁|/2` — the same number. Mutation-tested:
    // replacing `>=` with `>` changes no output, including on the two corpus
    // cases whose discriminant is exactly zero (φ=θ=0 and φ=θ=1). The `>=`
    // stays because it is what the original says, not because anything
    // downstream can tell.
    if disc >= 0.0 {
        let sq = mathx::sqrt(disc);
        (((a1 + sq) / 2.0).abs(), ((a1 - sq) / 2.0).abs())
    } else {
        // Complex pair: |z|² = product of roots = −a₂ = θ.
        let modulus = mathx::sqrt(-a2);
        (modulus, modulus)
    }
}

/// Deterministic impulse response: the `s` path after a one-off unit shock,
/// with no further inputs.
///
/// The analytic replacement for a two-world simulation harness at this layer
/// — no RNG, so no de-sync problem, and instant.
///
/// `horizon_days` is an `i64` even though the TypeScript parameter is a
/// `number`, and unlike `market_maker::LadderParams::levels` that narrowing
/// is faithful. The loop is `for (d = 1; d <= horizonDays; d++)` with an
/// integer `d`, so `d <= 2.5` and `d <= 2` admit the same iterations, and a
/// NaN horizon gives zero iterations exactly as a cast-to-zero does. Only an
/// infinite horizon differs, and it hangs in both languages.
pub fn impulse_response(horizon_days: i64, phi: Option<f64>, theta: Option<f64>) -> Vec<f64> {
    let phi = phi.unwrap_or(MISPRICING_PHI);
    let theta = theta.unwrap_or(MOMENTUM_THETA);

    let mut out = vec![1.0];
    let mut prev = 0.0f64;
    let mut curr = 1.0f64;
    for _ in 1..=horizon_days {
        let next = phi * curr + theta * (curr - prev);
        prev = curr;
        curr = next;
        out.push(curr);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phi_matches_the_recorded_v8_bits() {
        assert_eq!(MISPRICING_PHI.to_bits(), 0x3FEF_A1E8_27A1_B38C);
        assert_eq!(MISPRICING_PHI, 0.9885140203528962);
    }

    #[test]
    fn phi_delivers_the_stated_half_life() {
        // The point of deriving φ from a half-life: 60 steps must halve it.
        //
        // Compounded by plain multiplication rather than `powi` or
        // `mathx::pow` — 60 multiplies involve no libm at all, so this cannot
        // fail for a reason that belongs to the exponential rather than to φ.
        // The tolerance allows for 60 roundings, not for a wrong half-life.
        let mut decayed = 1.0f64;
        for _ in 0..60 {
            decayed *= MISPRICING_PHI;
        }
        assert!((decayed - 0.5).abs() < 1e-13, "60-day decay was {decayed}");
    }

    // ── INVARIANT 1.1–1.4: the process is stationary by construction ──────

    #[test]
    fn both_characteristic_roots_lie_inside_the_unit_circle() {
        let (r1, r2) = characteristic_root_moduli(None, None);
        assert!(
            r1 < 1.0 && r2 < 1.0,
            "roots {r1}, {r2} — process is not stationary"
        );
    }

    #[test]
    fn the_crowd_gains_do_not_push_the_roots_out() {
        // The inequality the design demands be CHECKED, not assumed: the
        // valuation lean lowers φ and the momentum lean raises θ, and the
        // second must not undo the first.
        let (r1, r2) = crowd_adjusted_root_moduli();
        assert!(
            r1 < 1.0 && r2 < 1.0,
            "crowd-adjusted roots {r1}, {r2} are not stationary"
        );
    }

    #[test]
    fn the_crowd_strengthens_reversion_rather_than_weakening_it() {
        let (bare, _) = characteristic_root_moduli(None, None);
        let (crowded, _) = crowd_adjusted_root_moduli();
        assert!(
            crowded < bare,
            "crowd should tighten the dominant root: {crowded} vs {bare}"
        );
    }

    #[test]
    fn a_unit_root_stays_exactly_one() {
        // φ=1, θ=0 — the exactness canary from the goldens. Any drift here
        // means the arithmetic itself is wrong.
        let path = impulse_response(64, Some(1.0), Some(0.0));
        assert!(
            path.iter().all(|&x| x == 1.0),
            "unit root drifted: {path:?}"
        );
    }

    #[test]
    fn an_impulse_decays_to_nothing() {
        let path = impulse_response(5000, None, None);
        assert_eq!(path[0], 1.0);
        // Momentum makes it RISE first — herding continues the move — before
        // reverting. Both halves matter.
        assert!(
            path[3] > path[0],
            "momentum should extend the impulse first"
        );
        assert!(path.last().unwrap().abs() < 1e-9, "impulse never decayed");
    }

    #[test]
    fn a_non_positive_horizon_returns_exactly_one_entry() {
        for horizon in [0, -1, -100] {
            assert_eq!(impulse_response(horizon, None, None), vec![1.0]);
        }
    }

    // ── INVARIANT 2.1–2.6: caps and clamps ────────────────────────────────

    #[test]
    fn s_never_escapes_the_cap_however_hard_it_is_pushed() {
        let mut state = create_mispricing_state(0.0);
        for _ in 0..1000 {
            state = step_mispricing(
                &state,
                &MispricingInputs {
                    innovation: 10.0,
                    shock: 100.0,
                },
            );
            assert!(state.s.abs() <= MISPRICING_CAP, "s escaped to {}", state.s);
        }
        assert_eq!(state.s, MISPRICING_CAP);
    }

    #[test]
    fn the_initial_state_is_clamped_too() {
        assert_eq!(create_mispricing_state(50.0).s, MISPRICING_CAP);
        assert_eq!(create_mispricing_state(-50.0).s, -MISPRICING_CAP);
    }

    #[test]
    fn negative_zero_survives_state_creation() {
        // The clamp does not normalise it, and the vectors pin this.
        assert_eq!(
            create_mispricing_state(-0.0).s.to_bits(),
            (-0.0f64).to_bits()
        );
    }

    #[test]
    fn s_prev_takes_the_unclamped_input_exactly_once() {
        // A hand-built out-of-range state must propagate its s into s_prev
        // verbatim, even though the new s is clamped.
        let rogue = MispricingState {
            s: 5.0,
            s_prev: 0.0,
        };
        let next = step_mispricing(
            &rogue,
            &MispricingInputs {
                innovation: 0.0,
                shock: 0.0,
            },
        );
        assert_eq!(next.s_prev, 5.0);
        assert_eq!(next.s, MISPRICING_CAP);
    }

    #[test]
    fn the_shock_is_capped_but_the_innovation_is_not() {
        let state = create_mispricing_state(0.0);
        // A shock far beyond the cap contributes exactly DAILY_SHOCK_CAP.
        let shocked = step_mispricing(
            &state,
            &MispricingInputs {
                innovation: 0.0,
                shock: 99.0,
            },
        );
        assert_eq!(shocked.s, DAILY_SHOCK_CAP);
        // The same magnitude as an innovation is NOT capped, so it reaches
        // the outer cap instead.
        let innovated = step_mispricing(
            &state,
            &MispricingInputs {
                innovation: 99.0,
                shock: 0.0,
            },
        );
        assert_eq!(innovated.s, MISPRICING_CAP);
    }

    #[test]
    fn nan_passes_through_the_clamp_rather_than_becoming_a_bound() {
        let state = create_mispricing_state(0.0);
        let out = step_mispricing(
            &state,
            &MispricingInputs {
                innovation: f64::NAN,
                shock: 0.0,
            },
        );
        assert!(out.s.is_nan(), "NaN was swallowed into {}", out.s);
    }

    // ── crowdLean ─────────────────────────────────────────────────────────

    #[test]
    fn the_crowd_leans_against_mispricing() {
        assert!(crowd_lean(0.5, 0.0) < 0.0, "overvalued should draw selling");
        assert!(
            crowd_lean(-0.5, 0.0) > 0.0,
            "undervalued should draw buying"
        );
    }

    #[test]
    fn the_crowd_is_flat_at_fair_value_with_no_momentum() {
        assert_eq!(crowd_lean(0.0, 0.0), 0.0);
    }

    #[test]
    fn the_crowd_chases_yesterdays_re_rating() {
        assert!(crowd_lean(0.0, 0.02) > 0.0);
        assert!(crowd_lean(0.0, -0.02) < 0.0);
    }

    #[test]
    fn the_crowd_lean_is_bounded_including_for_pathological_inputs() {
        // The table from `crowd-flow.test.ts`, ported as the spec asks.
        for (s, momentum) in [
            (5.0, 5.0),
            (-5.0, -5.0),
            (0.9, 0.5),
            (-0.9, -0.5),
            (1e9, 1e9),
            (100.0, -100.0),
        ] {
            let lean = crowd_lean(s, momentum);
            assert!(lean.abs() <= CROWD_LEAN_CAP, "lean {lean} escaped its cap");
        }
    }

    #[test]
    fn the_crowd_lean_stays_well_inside_the_daily_shock_cap() {
        // Structural, not incidental: the crowd must never dominate a news
        // day. A const block makes it a COMPILE-time failure — the relation
        // is between two constants, so a runtime check would be discovering
        // at test time something the compiler already knows.
        //
        // The bound is HALF the daily shock cap, not merely under it. That is
        // what `crowd-flow.test.ts` asserts, and the weaker form would still
        // pass while the crowd had grown to rival a news day.
        const { assert!(CROWD_LEAN_CAP < DAILY_SHOCK_CAP / 2.0) };
    }

    #[test]
    fn the_valuation_lean_dominates_the_momentum_lean_at_realistic_magnitudes() {
        // Typical |s| is about one unconditional sd (~0.10); typical |Δs| is
        // about one daily sigma (~0.015). The design note is "momentum lean
        // ≪ valuation lean", and 2× dominance is asserted so a future retune
        // cannot quietly invert the hierarchy — which would turn the crowd
        // from a restoring force into an amplifier.
        let valuation = crowd_lean(-0.10, 0.0).abs();
        let momentum = crowd_lean(0.0, 0.015).abs();
        assert!(
            valuation >= momentum * 2.0,
            "valuation lean {valuation} should be at least twice the momentum lean {momentum}"
        );
    }

    #[test]
    fn the_crowd_adjusted_analytics_stay_in_sync_with_the_actual_gains() {
        // If someone retunes CROWD_VALUATION_GAIN or CROWD_MOMENTUM_GAIN
        // without updating `crowd_adjusted_root_moduli`, the stationarity
        // proof would still pass while proving something about the wrong
        // model. This pins the relationship.
        let (r1, r2) = characteristic_root_moduli(
            Some(MISPRICING_PHI - CROWD_VALUATION_GAIN),
            Some(MOMENTUM_THETA + CROWD_MOMENTUM_GAIN),
        );
        let (c1, c2) = crowd_adjusted_root_moduli();
        assert_eq!(c1.to_bits(), r1.to_bits());
        assert_eq!(c2.to_bits(), r2.to_bits());
    }

    #[test]
    fn a_negative_zero_s_yields_positive_zero() {
        // `-CROWD_VALUATION_GAIN * -0` is `+0`, because the unary minus binds
        // to the constant. The vectors pin this.
        assert_eq!(crowd_lean(-0.0, 0.0).to_bits(), 0.0f64.to_bits());
    }

    #[test]
    fn opposing_infinities_yield_a_nan_that_the_clamp_passes_through() {
        assert!(crowd_lean(f64::INFINITY, f64::INFINITY).is_nan());
    }

    // ── applyMispricing ───────────────────────────────────────────────────

    #[test]
    fn a_zero_mispricing_prices_at_fair_value() {
        assert_eq!(apply_mispricing(100.0, 0.0), 100.0);
    }

    #[test]
    fn the_price_floor_holds_even_for_a_negative_fair_value() {
        assert_eq!(apply_mispricing(-100.0, 0.0), 0.01);
        assert_eq!(apply_mispricing(0.0, 0.0), 0.01);
    }

    #[test]
    fn a_nan_propagates_rather_than_becoming_the_floor() {
        // `f64::max(0.01, NaN)` would return 0.01 — a poisoned input laundered
        // into a plausible price.
        assert!(apply_mispricing(f64::NAN, 0.0).is_nan());
    }

    #[test]
    fn s_is_clamped_before_the_exponential() {
        // Beyond the cap, the price stops moving.
        assert_eq!(
            apply_mispricing(100.0, 50.0),
            apply_mispricing(100.0, MISPRICING_CAP)
        );
    }
}

/// Stationary variance of the daily `s` process, for a given innovation
/// variance.
///
/// # The process in AR(2) form
///
/// The step is `s' = phi*s + theta*(s - s_prev) + e`, which collects to
/// `s' = (phi + theta)*s - theta*s_prev + e`. So in the standard
/// `x_t = a1*x_{t-1} + a2*x_{t-2} + e` form, `a1 = phi + theta` and
/// `a2 = -theta`.
///
/// The stationary variance is then the textbook AR(2) result:
///
/// ```text
///            sigma^2 * (1 - a2)
/// gamma0 = ------------------------------
///          (1 + a2) * ((1 - a2)^2 - a1^2)
/// ```
///
/// # What it is for
///
/// A universe priced exactly at fair value starts with `s = 0` everywhere and
/// therefore ZERO cross-sectional mispricing dispersion. Any strategy that
/// harvests mispricing sees nothing until shocks accumulate -- on the order of
/// one 60-day half-life. Drawing initial `s` from this distribution starts the
/// universe where a long-running one would already be.
///
/// Returns `None` when the parameters are not stationary, rather than a
/// negative variance dressed up as a number.
pub fn stationary_variance(
    phi: Option<f64>,
    theta: Option<f64>,
    innovation_variance: f64,
) -> Option<f64> {
    let phi = phi.unwrap_or(MISPRICING_PHI);
    let theta = theta.unwrap_or(MOMENTUM_THETA);
    let a1 = phi + theta;
    let a2 = -theta;

    let denominator = (1.0 + a2) * ((1.0 - a2) * (1.0 - a2) - a1 * a1);
    if !(denominator > 0.0) || !innovation_variance.is_finite() || innovation_variance < 0.0 {
        return None;
    }
    let gamma0 = innovation_variance * (1.0 - a2) / denominator;
    if gamma0.is_finite() && gamma0 >= 0.0 {
        Some(gamma0)
    } else {
        None
    }
}

/// Stationary standard deviation, the form a caller actually wants.
pub fn stationary_sigma(phi: Option<f64>, theta: Option<f64>, innovation_sigma: f64) -> Option<f64> {
    stationary_variance(phi, theta, innovation_sigma * innovation_sigma)
        .map(crate::mathx::sqrt)
}

#[cfg(test)]
mod stationary_tests {
    use super::*;
    use crate::rng::GameRng;

    /// The analytic value must match a long simulation, or the algebra is
    /// decoration. Derived formulas are exactly the kind of thing that looks
    /// right and is off by a factor.
    #[test]
    fn the_analytic_variance_matches_a_simulated_one() {
        let sigma = 0.012;
        let analytic = stationary_sigma(None, None, sigma).expect("stationary");

        let mut rng = GameRng::new(4242, 99);
        let mut state = MispricingState::default();
        // Burn in past the transient before measuring.
        for _ in 0..5_000 {
            state = step_mispricing(
                &state,
                &MispricingInputs { innovation: rng.next_normal() * sigma, shock: 0.0 },
            );
        }
        let (mut sum, mut sum_sq, n) = (0.0, 0.0, 400_000);
        for _ in 0..n {
            state = step_mispricing(
                &state,
                &MispricingInputs { innovation: rng.next_normal() * sigma, shock: 0.0 },
            );
            sum += state.s;
            sum_sq += state.s * state.s;
        }
        let mean = sum / n as f64;
        let simulated = crate::mathx::sqrt(sum_sq / n as f64 - mean * mean);

        let ratio = simulated / analytic;
        assert!(
            ratio > 0.9 && ratio < 1.1,
            "analytic {analytic:.6} vs simulated {simulated:.6} (ratio {ratio:.4})"
        );
    }

    #[test]
    fn a_bigger_innovation_gives_a_wider_distribution() {
        let small = stationary_sigma(None, None, 0.005).unwrap();
        let large = stationary_sigma(None, None, 0.020).unwrap();
        // Linear in sigma: the process is linear, so scaling the input scales
        // the output exactly.
        assert!((large / small - 4.0).abs() < 1e-9);
    }

    #[test]
    fn non_stationary_parameters_return_none_rather_than_a_number() {
        // A unit root gives an infinite variance. Returning a large finite
        // number would be worse than returning nothing: it would be used.
        assert!(stationary_variance(Some(1.0), Some(0.0), 0.0001).is_none());
        assert!(stationary_variance(Some(1.5), Some(0.25), 0.0001).is_none());
    }

    #[test]
    fn the_shipped_parameters_are_stationary() {
        assert!(stationary_sigma(None, None, 0.015).is_some());
        let (a, b) = characteristic_root_moduli(None, None);
        assert!(a < 1.0 && b < 1.0);
    }
}
