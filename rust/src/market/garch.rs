//! GJR-GARCH(1,1) volatility, extending the GARCH(1,1) ported from
//! the reference implementation's GARCH update, with a leverage-effect
//! asymmetry term.
//!
//! **Tier 1: no transcendentals, no RNG.** A handful of multiplies, one
//! sign compare, a clamp, and a lookup — held to bit-parity.
//!
//! # What GARCH is doing here
//!
//! Volatility CLUSTERS: a large move today makes a large move tomorrow more
//! likely, which is why real markets have quiet months and violent weeks
//! rather than uniform noise. `ALPHA` is how much yesterday's surprise feeds
//! through; `BETA` is how much of yesterday's volatility persists; `GAMMA`
//! is how much MORE a negative surprise feeds through than a positive one
//! of the same size — the leverage effect, which real equities have and a
//! symmetric GARCH structurally cannot (the return enters squared, so its
//! sign is destroyed; design finding 8, CALIBRATION.md §3.5). The effective
//! persistence is `ALPHA + BETA + GAMMA/2`, the asymmetry term being live on
//! roughly half of days.
//!
//! **The constants below are not what any shipped preset runs.** This module
//! reads 0.99, a 69-day half-life. Every preset from pt-v6 onward carries a
//! recalibrated `garch_beta` and `garch_gamma` and lands at 0.8364, which is
//! a half-life of 3.9 DAYS. That was measured on 2026-08-26 (§115) and it is
//! the whole of the `decay-shape` gap: the latent variance autocorrelation
//! is already negative by lag 20, and neither the sector clamps nor
//! measurement noise account for it. This paragraph exists because the text
//! above it said the persistence was "held at the reference's 0.99" for
//! three eras after it stopped being true, and the test that would have
//! caught it asserts on these constants rather than on a preset.
//!
//! GJR (Glosten–Jagannathan–Runkle) rather than EGARCH, deliberately:
//! EGARCH's log-variance form needs `exp`/`log` per name per day, which
//! would drag the volatility process out of Tier 1 and into the bit-parity
//! budget. GJR is a compare and a multiply.
//!
//! # The JS-fallback form is the contract, plus one argued divergence
//!
//! `updateGarchVariance` tries WASM first and falls back to this arithmetic.
//! `WASM-ORACLE.md` §3 establishes the two agree on all reachable inputs, and
//! decisions D1–D3 already settled that the new era takes the JS side where
//! they differ. There is no WASM in this crate, so the fallback is simply
//! what the function is — with one deliberate divergence: the reference has
//! no `GAMMA` term. The term is written as a guarded `+=` AFTER the
//! reference's three-term sum, never folded into it, so that at `GAMMA = 0`
//! the arithmetic is bit-identical to the reference form (floating-point
//! addition is not associative; a re-associated four-term sum would change
//! every trajectory even at zero). The shipped `GAMMA` is nonzero — an era
//! decision, calibrated by `tools/calibration/sweep_gjr_gamma.py` — and the
//! zero-`GAMMA` bit-identity is what keeps the term revisitable without
//! re-litigating the structure.

use crate::mathx;

/// Long-run variance weight.
pub const OMEGA: f64 = 0.000002;
/// Weight on yesterday's squared return — the surprise term, for BOTH signs.
///
/// Recalibrated from the reference's symmetric 0.09 when `GAMMA` was
/// introduced: GJR stationarity needs `ALPHA + BETA + GAMMA/2 < 1`, so the
/// asymmetry is paid for out of the symmetric coefficients. Deliberately
/// small but strictly positive — at zero, a large POSITIVE surprise would
/// not raise tomorrow's variance at all, which deletes a day-one invariant
/// (the property test below pins it).
pub const ALPHA: f64 = 0.02;
/// Weight on yesterday's variance — the persistence term.
///
/// Recalibrated from the reference's 0.90 to fund `GAMMA`; the effective
/// persistence `ALPHA + BETA + GAMMA/2` is held at the reference's 0.99, so
/// shocks decay at the same expected rate — more of the decay now travels
/// through re-excitation by realised down moves and less through the smooth
/// carry, which is measurably what raises volatility clustering into its
/// real-world band (|r| acf(1) 0.124 → 0.186).
pub const BETA: f64 = 0.80;
/// Extra weight on yesterday's squared return when the return was NEGATIVE —
/// the GJR leverage-effect term. Zero recovers the reference's symmetric
/// GARCH(1,1) bit-for-bit.
///
/// Chosen by the seventeen-point sweep in
/// `tools/calibration/results/gjr-gamma-2026-08-21.json`
/// (`tools/calibration/sweep_gjr_gamma.py`, published method, six seeds):
/// 0.34 is the smallest measured value that lands the median leverage
/// effect inside the real band of −0.30 to −0.10 (measured −0.107) while
/// keeping `ALPHA` positive. It reads large against literature GJR fits
/// (index estimates run ~0.10) because the panel statistic, not the
/// coefficient, is the target: the day's return here carries factor and
/// microstructure noise the variance process does not drive, and the
/// sector ceiling clamp truncates the biggest asymmetric responses, so a
/// larger structural asymmetry is needed to produce the same measured
/// correlation.
pub const GAMMA: f64 = 0.34;

/// Ceiling as a multiple of the sector's long-run variance.
///
/// Reduced from 10x to 5x in "Cycle 71" to limit crisis volatility. It is a
/// cap on how far a crisis can run, not a modelling refinement.
pub const CEILING_MULTIPLE: f64 = 5.0;
/// Floor as a multiple of the sector's long-run variance, so a quiet stretch
/// cannot drive volatility to zero and freeze the price.
pub const FLOOR_MULTIPLE: f64 = 0.25;

/// The most components a variance cascade may carry.
///
/// Fixed rather than heap-allocated: eight f64 per name is 64 bytes, and a
/// `Vec` per company would put an allocation in the close path for every
/// name every day. Eight spans lags 1 to 2187 at ratio 3, which is past any
/// horizon this model is measured at.
pub const CASCADE_MAX: usize = 8;

/// One day's update for a multi-component variance cascade.
///
/// **Why a cascade at all.** A single GJR recursion decays exponentially. Real
/// volatility memory decays hyperbolically, and the `decay-shape` gap has
/// carried that difference since it was written, with the note that "a
/// two-component mixture was tried and is not sufficient".
///
/// Two was never the interesting number. A superposition of exponentials with
/// GEOMETRICALLY SPACED timescales approximates a power law closely over the
/// range those timescales span, and the count you need is about
/// `log(range)/log(ratio)` -- six components at ratio 3 to cover lags 1 to 60,
/// not two. Measured on a toy validated against this engine's own one-component
/// reading (CALIBRATION-FOLLOWUPS §122): the latent log-log slope over lags
/// 1-20 goes from -1.273 at one component to -0.536 at six, against a real
/// -0.436, and lag-60 autocorrelation turns positive for the first time.
///
/// **How the components relate.** Component 0 IS today's process: it takes the
/// name's own `garch_beta`, so the per-name persistence dispersion `pt-v7`
/// introduced survives untouched. Component `k` has a half-life `ratio^k`
/// times component 0's, achieved by moving beta alone -- `alpha` and `gamma`
/// stay put, so every component re-excites on the same surprise with the same
/// leverage asymmetry. Each carries its own `omega`, scaled to hold its
/// unconditional variance at the sector base, which is what stops the mix from
/// drifting as weights change.
///
/// Weights are FLAT across components. Weighting evenly across geometrically
/// spaced timescales is what approximates a power law; tilting toward the fast
/// end undoes it, measured at -1.077 for `1/sqrt(tau)` weights against -0.536
/// for flat ones.
///
/// **What the blend weight costs in the fourth moment.** The returned `h` is
/// a weighted average of the UPDATED components and nothing else:
///
/// ```text
/// h    = sum_i wt_i v_i + (1 - w) (omega_legacy - omega_0)
/// wt_i = w/K + (1 - w) [i == 0]
/// ```
///
/// which is why the legacy term reads component 0's previous variance. With
/// that spelling the fourth-moment operator `cascade-crossing.md` publishes
/// for the flat blend is the operator at every `w` in [0, 1]; substitute `wt`
/// for the flat `1/K` and no new algebra is needed. The radius rises
/// monotonically in `w`, because tilting weight onto component 0 tilts it
/// onto the FASTEST component and that is the one with the lowest
/// coefficient. At the pt-v19 triple and `kappa^2 = 1` it runs 0.957440 at
/// `w = 0`, which is the single-component coefficient exactly, to 1.018982 at
/// `w = 1` and `K = 3`. The largest `w` whose fourth moment is finite there
/// is 0.662766 at K = 3, 0.439410 at K = 6 and 0.402387 at K = 8; K = 1 and
/// K = 2 clear the whole interval.
///
/// None of that binds on what the engine wires AT pt-v19. `daily.rs` does not
/// feed this recursion the name's own return, so the shock is attenuated by
/// `kappa^2 = idio_sigma_scale^2 * cap_mult^2 * volatility_multiplier^2 *
/// intraday_variance_factor`, which is 0.182366 to 0.729463 across the four
/// cap tiers at pt-v19's `idio_sigma_scale` of 0.5125981926
/// (`cascade-crossing.md` section 4). The worst radius over every `w` in
/// [0, 1], every `K` up to [`CASCADE_MAX`] and every tier is 0.940249.
///
/// THAT RANGE IS A PROPERTY OF THE PRESET AND NOT OF THE ENGINE.
/// `idio_sigma_scale` was 0.84 at pt-v1 and 0.8146 from pt-v2 to pt-v6, where
/// the same identity gives 0.489720, 0.765187, 1.293166 and 1.958879 across
/// the four tiers. `kappa^2` above 1 is reachable on a shipped preset, so
/// read the `kappa^2 = 1` numbers rather than this paragraph's whenever the
/// preset is not pt-v16 or later. A caller supplying `daily_innovations`
/// itself is in the same position: `engine.rs` measures the day's return
/// against the noise column at 0.82 at the median and 3.20 at the ninetieth
/// percentile.
///
/// Returns the blended variance and updates `cascade` in place. At
/// `components == 0` this is never called and the single-component path runs
/// bit-for-bit.
pub fn update_garch_cascade(
    params: &crate::params::ModelParams,
    garch_beta: f64,
    cascade: &mut [f64; CASCADE_MAX],
    last_daily_return: f64,
    sector_base_variance: f64,
) -> f64 {
    // A COUNT, so it truncates rather than rounds, and it is clamped to the
    // array. `usize::min` on an integer is not the NaN-swallowing `f64::min`
    // the guard is about, but the guard matches on text and is right to: a
    // reader scanning for float mins should not have to check the type.
    let requested = params.garch_cascade_components as usize;
    let k = if requested > CASCADE_MAX { CASCADE_MAX } else { requested };
    let asym = if last_daily_return < 0.0 { params.garch_gamma } else { 0.0 };
    let shock = (params.garch_alpha + asym) * last_daily_return * last_daily_return;

    // Component 0's half-life, from the name's OWN effective persistence, so
    // the cascade is anchored to the process it replaces rather than to a
    // constant. `persistence` at or above 1 cannot happen -- the settable
    // range is bounded below GARCH_PERSISTENCE_CEILING -- but a caller can
    // build a custom preset, and a non-finite half-life here would poison
    // every component, so it is guarded rather than asserted.
    let persistence = params.garch_alpha + garch_beta + params.garch_gamma / 2.0;
    if !(persistence > 0.0 && persistence < 1.0) {
        return update_garch_variance_for(
            params, garch_beta, cascade[0], last_daily_return, sector_base_variance);
    }
    // `mathx::log`, not `f64::ln`. The cascade has no reference-implementation counterpart to
    // hold parity with -- it is new mechanism and ships switched off -- but
    // `no_std_transcendentals_outside_mathx` is a rule about the crate, not
    // about the parity corpus, and it caught the first version of this line.
    // Routing through libm also makes the cascade reproduce across platforms,
    // which a mechanism that will be calibrated needs anyway.
    let base_half_life = mathx::log(0.5) / mathx::log(persistence);

    // The legacy term's own state, READ BEFORE the loop, because the loop
    // overwrites `cascade[0]` in place and the legacy recursion wants
    // component 0's PREVIOUS variance rather than the value that already has
    // today's shock in it. Reading it afterwards put the shock into `h`
    // twice: the returned variance then responded to the day at a gain of
    // `1 + beta` and to the day `j` back at `beta^(j+1)` instead of `beta^j`.
    // Both impulse responses sum to `1/(1 - beta)`, so the unconditional
    // level was right either way and nothing below the second moment of `h`
    // could see it. See the blend paragraph in this function's doc for what
    // it cost in the fourth moment.
    let previous_component_0 = cascade[0];

    let mut total = 0.0;
    for i in 0..k {
        let half_life = base_half_life * powi(params.garch_cascade_ratio, i);
        // beta that puts THIS component at that half-life, with alpha and
        // gamma/2 already spending part of the persistence budget.
        let target = pow_half(half_life);
        let beta_i = target - params.garch_alpha - params.garch_gamma / 2.0;
        let beta_i = if beta_i < 0.0 { 0.0 } else { beta_i };
        let pers_i = params.garch_alpha + beta_i + params.garch_gamma / 2.0;
        let omega_i = sector_base_variance * (1.0 - pers_i);
        let raw = omega_i + shock + beta_i * cascade[i];
        cascade[i] = mathx::max(
            mathx::min(raw, sector_base_variance * params.garch_ceiling_multiple),
            sector_base_variance * params.garch_floor_multiple,
        );
        total += cascade[i];
    }
    let cascade_variance = total / (k as f64);

    // The blend is a dial rather than a switch, so a preset can take part of
    // the cascade's shape without paying all of its cost.
    let w = params.garch_cascade_weight;
    let legacy = update_garch_variance_for(
        params, garch_beta, previous_component_0, last_daily_return,
        sector_base_variance);
    (1.0 - w) * legacy + w * cascade_variance
}

/// `0.5^(1/half_life)`, the AR(1) coefficient with that half-life.
fn pow_half(half_life: f64) -> f64 {
    mathx::exp(mathx::log(0.5) / half_life)
}

/// Integer power, so the component spacing needs no `powf`.
fn powi(base: f64, n: usize) -> f64 {
    let mut out = 1.0;
    for _ in 0..n {
        out *= base;
    }
    out
}

/// One day's GARCH(1,1) update.
///
/// `sector_base_variance` is the sector's long-run daily variance and sets
/// both bounds — the process mean-reverts toward its sector rather than
/// toward a single global level.
/// The GJR persistence a name's variance process must stay below.
///
/// `alpha + beta + gamma/2` at or above one is a variance that grows without
/// bound. pt-v6 sits at 0.8364, so there is real headroom, but a dispersion
/// wide enough to spend all of it would produce a name whose volatility only
/// ever climbs until a guard truncates it, which is not fat tails.
///
/// **THIS IS A FIRST-MOMENT GUARD AND IT SAYS NOTHING ABOUT THE FOURTH.** At
/// pt-v19's alpha and gamma it admits a per-name beta up to
/// `0.97 - alpha - gamma/2` = 0.8189001070794508, whose single-component GJR
/// fourth-moment coefficient `3a^2 + 3ag + 1.5g^2 + 2ab + bg + b^2` is
/// 1.0117300128859839 at `kappa^2 = 1`, against a crossing at
/// 0.8128347454829143. So the ceiling admits a beta whose fourth moment does
/// not exist on the single-component path that ships. `garch_beta_dispersion`
/// is 0.0 on every preset, so no name reaches it today.
///
/// It is left at 0.97 rather than retuned or paired with a fourth-moment
/// clamp, for two reasons that are worth having written down before someone
/// widens the dispersion.
///
/// The bound it would have to carry is not a persistence at all. Solving the
/// coefficient for beta at a threshold of 1 and adding `alpha + gamma/2` back
/// gives a crossing persistence of 0.996453 at `gamma` 0.0, 0.973109 at 0.15,
/// 0.963935 at the shipped 0.18318536187800277 and 0.893386 at 0.35. That is
/// a spread of 0.1031 over the gamma range the presets have used. No single
/// constant is both guards at once, and moving 0.97 down to 0.9639 would be
/// right at one gamma and a silent first-moment tightening at every other.
///
/// The name that can reach this beta is the one furthest from the condition.
/// [`garch_beta_for`] gives the LARGEST beta to the LARGEST cap, and
/// [`crate::market::factors::cap_size_multiplier`] gives the SMALLEST
/// multiplier to the largest cap, so the ceiling beta is reachable only at or
/// above `PERSISTENCE_REFERENCE_CAP * e^PERSISTENCE_CAP_SCALE` = $401.7 B,
/// which sits in the over-50 B tier at a `cap_mult` of 0.8 and therefore at
/// `kappa^2` 0.182366. The coefficient at that beta and that attenuation is
/// 0.718843. A clamp here would refuse dispersions the wired process
/// tolerates, which is the error [`crate::market::factor_vol`]'s
/// `alpha_beta_at` records itself making in the other direction and leaves
/// alone for the same reason: new arithmetic in a per-name path for a dial
/// that ships at zero.
///
/// What is not left to the reader is noticing. A `garch_beta_dispersion` of
/// 0.022335 puts the largest name past the `kappa^2 = 1` fourth-moment bound
/// and 0.028400 puts it on this ceiling, so the fourth moment goes first, by
/// 0.006065. `the_widest_beta_a_preset_admits_has_a_finite_fourth_moment`
/// fails on the preset that crosses that line.
pub const GARCH_PERSISTENCE_CEILING: f64 = 0.97;

/// Reference capitalisation, in dollars, at which a name gets exactly
/// `garch_beta`. The median of `Universe.random`'s cap distribution rounds
/// near here, so the spread is roughly balanced across a generated roster.
const PERSISTENCE_REFERENCE_CAP: f64 = 2.0e10;

/// How wide a cap range the spread is drawn over, in natural logs. At 3.0 a
/// name about twenty times smaller or larger than the reference reaches the
/// bound; everything between is linear in log-cap.
const PERSISTENCE_CAP_SCALE: f64 = 3.0;

/// This name's `garch_beta`, given its size.
///
/// At `garch_beta_dispersion` of zero this returns `params.garch_beta` BY
/// BRANCH, so every preset before pt-v7 is bit-identical and owes nothing to
/// an argument about how a zero-width spread rounds. Same discipline as
/// [`crate::market::factors::cap_size_multiplier_with`] at zero smoothness.
///
/// The map is deterministic in the roster, not drawn, so the RNG stream
/// schedule does not move.
pub fn garch_beta_for(params: &crate::params::ModelParams, market_cap: f64) -> f64 {
    let d = params.garch_beta_dispersion;
    if d == 0.0 {
        return params.garch_beta;
    }
    // A non-positive cap has no size to speak of, so it gets the reference
    // persistence rather than the log of a non-positive number.
    if market_cap <= 0.0 {
        return params.garch_beta;
    }
    // A bounded, piecewise-linear map rather than a tanh. `mathx` has no
    // tanh, and adding one would mean a new transcendental with its own
    // cross-platform parity surface for a shape nothing here depends on.
    // `clamp` on the log-ratio gives the same bounded spread from primitives
    // that already exist.
    let z = mathx::clamp(
        mathx::log(market_cap / PERSISTENCE_REFERENCE_CAP) / PERSISTENCE_CAP_SCALE,
        -1.0,
        1.0,
    );
    let beta = params.garch_beta + d * z;
    // Stationarity is not negotiable: clamp against the GJR persistence,
    // and never below zero.
    let headroom =
        GARCH_PERSISTENCE_CEILING - params.garch_alpha - 0.5 * params.garch_gamma;
    mathx::max(0.0, mathx::min(beta, headroom))
}

pub fn update_garch_variance(
    current_variance: f64,
    last_daily_return: f64,
    sector_base_variance: f64,
) -> f64 {
    update_garch_variance_with(
        &crate::params::PT_V1,
        current_variance,
        last_daily_return,
        sector_base_variance,
    )
}

/// [`update_garch_variance`] under explicit model parameters (the runtime
/// seam, CALIBRATION.md §5.3). At [`crate::params::PT_V1`] this is the
/// shipped arithmetic bit for bit: same values, same operations, same
/// order — the constants above remain the definition of the preset.
pub fn update_garch_variance_with(
    params: &crate::params::ModelParams,
    current_variance: f64,
    last_daily_return: f64,
    sector_base_variance: f64,
) -> f64 {
    update_garch_variance_for(
        params,
        params.garch_beta,
        current_variance,
        last_daily_return,
        sector_base_variance,
    )
}

/// [`update_garch_variance_with`] under an explicit per-name `beta`.
///
/// The seam heterogeneous persistence needs. Passing `params.garch_beta`
/// reproduces the homogeneous arithmetic exactly, which is what the wrapper
/// above does and why no earlier preset moves.
pub fn update_garch_variance_for(
    params: &crate::params::ModelParams,
    garch_beta: f64,
    current_variance: f64,
    last_daily_return: f64,
    sector_base_variance: f64,
) -> f64 {
    // THE LONG-RUN LEVEL, and the one inconsistency this dial removes.
    //
    // `garch_omega` is ONE constant for every sector, so this recursion's
    // unconditional variance -- `omega / (1 - alpha - beta - gamma/2)` --
    // is the same for a utility as for a technology name, while the clamp
    // band built around it two statements below is a multiple of the
    // SECTOR's own variance. The level is global and the band is per
    // sector, so the sector table's 3.1x spread in `daily_sigma` reaches
    // the price as about 1.4x.
    //
    // The cascade spelling of this same process does not have the problem.
    // [`update_garch_cascade`] builds `omega_i = sector_base_variance *
    // (1 - pers_i)`, which is the identity that makes each component's
    // unconditional level EQUAL its sector's base. That is also what this
    // module's own doc claims the single-component path does: "the process
    // mean-reverts toward its sector rather than toward a single global
    // level". It does not; it is HELD near its sector by the clamps.
    //
    // So this is one process spelled two ways, and this dial removes the
    // difference rather than introducing a rule. At 0.0 the shipped
    // constant is read BY BRANCH and every preset before the dial is
    // bit-identical; at 1.0 the cascade's identity is evaluated exactly,
    // not approached through a blend, because `a + w * (b - a)` at `w = 1`
    // is not bit-equal to `b`.
    let omega = if params.garch_omega_sector_scaled == 0.0 {
        params.garch_omega
    } else {
        // `1 - persistence` is clamped at zero. A persistence at or above
        // one would otherwise make the identity NEGATIVE, and a negative
        // omega is a different process, not a smaller one. Dial vectors
        // reaching persistence above 1.0 are not hypothetical: a
        // `garch_gamma` of 0.55 on the shipped alpha and beta reaches
        // 1.0198 and is held finite only by the ceiling below.
        let persistence = params.garch_alpha + garch_beta + params.garch_gamma / 2.0;
        let identity = sector_base_variance * mathx::max(0.0, 1.0 - persistence);
        if params.garch_omega_sector_scaled == 1.0 {
            identity
        } else {
            (1.0 - params.garch_omega_sector_scaled) * params.garch_omega
                + params.garch_omega_sector_scaled * identity
        }
    };
    let mut new_var = omega
        + params.garch_alpha * last_daily_return * last_daily_return
        + garch_beta * current_variance;
    // The GJR asymmetry: a negative return feeds through with weight
    // ALPHA + GAMMA, a positive one with ALPHA alone. A guarded `+=` after
    // the reference sum, NOT a fourth term inside it: at GAMMA = 0 this adds
    // +0.0 to a strictly positive sum, which is bit-identical, where a
    // re-associated four-term sum would not be. The evaluation order here is
    // contractual, like every other arithmetic statement in this crate.
    if last_daily_return < 0.0 {
        new_var += params.garch_gamma * last_daily_return * last_daily_return;
    }
    // `Math.max(Math.min(newVar, ceiling), floor)` — written in that order in
    // the original, and the order is visible when the two bounds cross (a
    // zero or negative `sector_base_variance` makes them do exactly that).
    mathx::max(
        mathx::min(new_var, sector_base_variance * params.garch_ceiling_multiple),
        sector_base_variance * params.garch_floor_multiple,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::ModelParams;

    /// A representative sector daily sigma of 1.5%, so base variance is
    /// 0.000225 — `sectorBaseDailyVariance`'s default.
    const BASE: f64 = 0.015 * 0.015;

    /// Every SHIPPED preset stays below the stationarity ceiling.
    ///
    /// The test below this one asserts on the module constants, which no
    /// preset uses; it therefore passed for three eras while the shipped
    /// persistence sat at 0.8364 against a documented 0.99 (§115). This one
    /// asserts on what actually runs. It pins the LOW side loosely on
    /// purpose: 0.8364 is a 3.9-day half-life and probably too short, but
    /// that is a calibration question with a gate attached, not something a
    /// unit test should decide. What it must not do is drift unnoticed
    /// again, so the reading is written into the failure message.
    ///
    /// The history the sweep turned up, half-life in trading days:
    /// pt-v1 0.9900 (69d), pt-v2 0.9551 (15d), pt-v3 through pt-v12 0.8364
    /// (3.9d). The memory was lost in the pt-v2 to pt-v3 calibration and has
    /// been frozen across ten presets since.
    /// At 0.0 the sector scaling is bit-identical, by branch.
    ///
    /// The point of the branch is that every preset predating the dial owes
    /// nothing to an argument about how a zero-weight multiply rounds, so
    /// the assertion is on BITS and not on a tolerance.
    #[test]
    fn the_omega_scaling_is_bit_identical_at_zero() {
        let base = ModelParams::pt_v16();
        assert_eq!(base.garch_omega_sector_scaled, 0.0, "the dial must ship off");
        for r in [-0.03_f64, -0.005, 0.0, 0.004, 0.02] {
            for v in [BASE * 0.3, BASE, BASE * 2.0] {
                let with = update_garch_variance_for(&base, base.garch_beta, v, r, BASE);
                let mut off = ModelParams::pt_v16();
                off.garch_omega_sector_scaled = 0.0;
                let without = update_garch_variance_for(&off, off.garch_beta, v, r, BASE);
                assert_eq!(with.to_bits(), without.to_bits());
            }
        }
    }

    /// At 1.0 the recursion's unconditional variance IS the sector's base.
    ///
    /// This is the identity the dial exists to impose, so it is asserted as
    /// an identity rather than against a recorded number: iterate the
    /// recursion with a return of exactly zero, which strips the alpha and
    /// gamma terms and leaves `V' = omega + beta V`, whose fixed point is
    /// `omega / (1 - beta)`. Under the scaling `omega` is
    /// `base * (1 - alpha - beta - gamma/2)`, so the fixed point lands on
    /// `base` only when alpha and gamma are zero -- which is why the test
    /// sets them to zero rather than working around them. With them
    /// non-zero the shock terms supply the rest of the persistence budget,
    /// and that is measured on the box, not here.
    #[test]
    fn the_omega_scaling_puts_the_unconditional_level_on_the_sector_base() {
        let mut p = ModelParams::pt_v16();
        p.garch_omega_sector_scaled = 1.0;
        p.garch_alpha = 0.0;
        p.garch_gamma = 0.0;
        p.garch_beta = 0.85;
        // Start away from the fixed point in both directions and let it
        // converge; the clamp band is [0.25, 5] x base and the target is
        // base itself, so the band never intervenes.
        for start in [BASE * 0.3, BASE * 4.0] {
            let mut v = start;
            for _ in 0..400 {
                v = update_garch_variance_for(&p, p.garch_beta, v, 0.0, BASE);
            }
            assert!(
                (v / BASE - 1.0).abs() < 1e-9,
                "unconditional variance {v} is not the sector base {BASE} (from {start})"
            );
        }
    }

    /// The scaled omega is never negative, however the dials are set.
    ///
    /// `1 - persistence` goes negative above a persistence of one, and a
    /// negative omega is a different process rather than a smaller one. A
    /// `garch_gamma` of 0.55 on the shipped alpha and beta reaches 1.0198,
    /// so this is a reachable state and not a hypothetical.
    #[test]
    fn a_non_stationary_persistence_cannot_make_omega_negative() {
        let mut p = ModelParams::pt_v16();
        p.garch_omega_sector_scaled = 1.0;
        p.garch_gamma = 0.55;
        let persistence = p.garch_alpha + p.garch_beta + p.garch_gamma / 2.0;
        assert!(persistence > 1.0, "this test needs a divergent persistence, got {persistence}");
        let out = update_garch_variance_for(&p, p.garch_beta, BASE, 0.0, BASE);
        assert!(out > 0.0, "variance went non-positive: {out}");
    }

    /// The blend IS a weighted average of the updated components.
    ///
    /// WHAT THIS CATCHES: the legacy term reading `cascade[0]` AFTER the loop
    /// has already written today's shock into it. That spelling shipped until
    /// 2026-09-14 and returns an `h` that responds to the day's shock at a
    /// gain of `1 + (1 - w) * beta` instead of 1, which is a different
    /// process from the one every published fourth-moment condition is
    /// written for. No first moment can see it: both impulse responses sum to
    /// `1/(1 - beta)`, and the simulated `E[h]` agrees to six figures either
    /// way. It shows up here as a failure of
    ///
    ///     h = sum_i wt_i v_i,   wt_i = w/K + (1 - w) [i == 0]
    ///
    /// which is the identity that makes `cascade-crossing.md`'s operator on
    /// `E[v v']` the right operator at every `w` and not only at `w = 1`. At
    /// the settings below the old spelling misses by 6 to 16 per cent of `h`,
    /// against a tolerance of 1e-12.
    ///
    /// `garch_omega_sector_scaled` is 1.0 so the legacy term and component 0
    /// carry the same omega and the identity is exact rather than exact up to
    /// a constant. The returns and the starting state are chosen to keep
    /// every component inside `[0.25, 5] x base`, since a clamp that binds
    /// breaks the identity by design.
    #[test]
    fn the_cascade_blend_is_a_weighted_average_of_its_components() {
        for k in [1_usize, 2, 3, 6, 8] {
            for w in [0.0_f64, 0.25, 0.5, 0.75, 1.0] {
                for ret in [0.03_f64, -0.03, 0.0, -0.005] {
                    let mut p = ModelParams::pt_v19();
                    p.garch_omega_sector_scaled = 1.0;
                    p.garch_cascade_components = k as f64;
                    p.garch_cascade_ratio = 3.0;
                    p.garch_cascade_weight = w;
                    let mut cascade = [BASE; CASCADE_MAX];
                    let h = update_garch_cascade(
                        &p, p.garch_beta, &mut cascade, ret, BASE);
                    // The tilted weights, read off the blend the function
                    // documents rather than off its arithmetic.
                    let mut expected = 0.0;
                    for (i, v) in cascade.iter().take(k).enumerate() {
                        let wt = w / (k as f64)
                            + if i == 0 { 1.0 - w } else { 0.0 };
                        expected += wt * v;
                    }
                    // Nothing may have clamped, or the identity is not the
                    // thing under test.
                    for v in cascade.iter().take(k) {
                        assert!(
                            *v > BASE * p.garch_floor_multiple * 1.000001
                                && *v < BASE * p.garch_ceiling_multiple
                                    * 0.999999,
                            "k={k} w={w} ret={ret}: component {v} is on a \
                             clamp, so this case cannot test the identity"
                        );
                    }
                    let err = (h - expected).abs() / expected;
                    assert!(
                        err < 1e-12,
                        "k={k} w={w} ret={ret}: blend {h} is not the tilted \
                         average {expected} of its components, off by \
                         {:.3}% of h. The legacy term is reading a cascade[0] \
                         that already carries today's shock; see the blend \
                         paragraph on update_garch_cascade.",
                        100.0 * err
                    );
                    // At w = 1.0 the legacy term is multiplied by exactly
                    // 0.0, so which `cascade[0]` it reads cannot reach the
                    // result AT ALL, not merely within a tolerance. This is
                    // the assertion that says the repair above could not have
                    // moved a preset that switches the cascade on at the
                    // shipped weight, and it is on BITS for that reason.
                    if w == 1.0 {
                        let mut total = 0.0;
                        for v in cascade.iter().take(k) {
                            total += v;
                        }
                        assert_eq!(
                            h.to_bits(),
                            (total / (k as f64)).to_bits(),
                            "k={k} ret={ret}: at weight 1.0 the blend must be \
                             the component mean to the bit"
                        );
                    }
                }
            }
        }
    }

    /// The widest beta any SHIPPED preset admits has a finite fourth moment.
    ///
    /// WHAT THIS CATCHES: a preset setting `garch_beta_dispersion` wide
    /// enough that [`garch_beta_for`] hands some name a beta past the
    /// single-component GJR fourth-moment crossing.
    /// [`GARCH_PERSISTENCE_CEILING`] does not stop that and is not meant to:
    /// at pt-v19's alpha and gamma it admits 0.8189001070794508 where the
    /// crossing is 0.8128347454829143, so a dispersion of 0.022335 clears the
    /// fourth moment 0.006065 of dispersion before it clears the ceiling.
    /// Today every preset is at dispersion 0.0 and the widest beta is
    /// pt-v19's own 0.7905, which reads 0.957440 with 0.043 of margin.
    ///
    /// The bound asserted is the `kappa^2 = 1` one, which is CONSERVATIVE for
    /// the process `daily.rs` wires: the innovation that reaches this
    /// recursion is attenuated by `idio_sigma_scale^2 * cap_mult^2 *
    /// volatility_multiplier^2 * intraday_variance_factor`, at most 0.729463,
    /// and the ceiling beta reads 0.900957 there. A failure here is therefore
    /// a prompt to read `cascade-crossing.md` section 4 and decide, not proof
    /// that the preset diverges. It is written at `kappa^2 = 1` because that
    /// is the condition for a caller that supplies `daily_innovations`
    /// itself, and because a bound that moves with the cap tier does not
    /// belong in a test about a preset.
    ///
    /// TWO PRESETS ARE ALREADY OVER THE LINE, and they are listed here with
    /// the value they read rather than excused by a version cutoff. pt-v1's
    /// own shipped triple (0.02 / 0.80 / 0.34) reads 1.139000 and pt-v2's
    /// reads 1.110801, both at dispersion 0.0, so on those two presets the
    /// per-name variance has no fourth moment at `kappa^2 = 1` before
    /// anything is dialled. Pinning the readings means the list cannot grow
    /// without someone editing it, and a listed preset cannot drift.
    #[test]
    fn the_widest_beta_a_preset_admits_has_a_finite_fourth_moment() {
        // The presets that are over the bound, with what they read. See
        // `garchlatent.md` in the design repository for what it costs them:
        // an excess kurtosis that is set by `garch_ceiling_multiple` rather
        // than by the GJR coefficients.
        const OVER: [(&str, f64); 2] = [
            ("pt-v1", 1.1390000000000002),
            ("pt-v2", 1.1108006659369685),
        ];
        // Both ends of the log-cap clamp and past them, so the sweep finds
        // the extreme whichever sign the dispersion carries.
        let caps = [0.0_f64, 1.0e6, 1.0e9, 2.0e10, 4.1e11, 1.0e14];
        for name in ModelParams::preset_names() {
            let p = ModelParams::preset(name).unwrap();
            let (a, g) = (p.garch_alpha, p.garch_gamma);
            let m4 = |b: f64| {
                3.0 * a * a + 3.0 * a * g + 1.5 * g * g
                    + 2.0 * a * b + b * g + b * b
            };
            let mut worst_beta = 0.0_f64;
            let mut worst = 0.0_f64;
            for cap in caps {
                let b = garch_beta_for(&p, cap);
                if m4(b) > worst {
                    worst = m4(b);
                    worst_beta = b;
                }
            }
            if let Some((_, recorded)) = OVER.iter().find(|(n, _)| *n == *name) {
                assert!(
                    (worst - recorded).abs() < 1e-12,
                    "{name} is on the known-over list at {recorded} and now \
                     reads {worst} (beta {worst_beta}). If the coefficients \
                     moved on purpose, update the list and say so; if this \
                     went DOWN through 1.0, take the preset off the list."
                );
                continue;
            }
            assert!(
                worst < 1.0,
                "{name}: garch_beta_for reaches beta {worst_beta} at some \
                 cap, whose fourth-moment coefficient is {worst} at \
                 kappa^2 = 1. GARCH_PERSISTENCE_CEILING is a first-moment \
                 guard and admits it. Read cascade-crossing.md section 4 and \
                 the note on GARCH_PERSISTENCE_CEILING before widening \
                 garch_beta_dispersion further. Check the WIRED attenuation \
                 too: it is `idio_sigma_scale^2 * cap_mult^2 * \
                 volatility_multiplier^2 * intraday_variance_factor`, which \
                 is at most 0.729463 at pt-v19's idio_sigma_scale of 0.5126 \
                 but 1.9589 at pt-v1's 0.84, so a preset can clear this \
                 assertion and still be over the line where it runs."
            );
        }

        // And the check is not vacuous. pt-v19 clears it at dispersion 0.0
        // with 0.043 of margin; at 0.0284, the dispersion that first puts a
        // mega cap on GARCH_PERSISTENCE_CEILING, the same sweep goes over.
        let mut p = ModelParams::pt_v19();
        let (a, g) = (p.garch_alpha, p.garch_gamma);
        let m4 = |b: f64| {
            3.0 * a * a + 3.0 * a * g + 1.5 * g * g + 2.0 * a * b + b * g
                + b * b
        };
        assert!(p.garch_beta_dispersion == 0.0, "pt-v19 must ship at 0.0");
        assert!(m4(garch_beta_for(&p, 4.1e11)) < 1.0);
        p.garch_beta_dispersion = 0.0284;
        let widened = garch_beta_for(&p, 4.1e11);
        assert!(
            m4(widened) > 1.0,
            "the sweep above cannot catch anything: a dispersion of 0.0284 \
             reaches beta {widened}, which should be past the fourth-moment \
             crossing at 0.8128347454829143"
        );
    }

    #[test]
    fn every_shipped_preset_has_a_stationary_variance_process() {
        for name in ModelParams::preset_names() {
            let p = ModelParams::preset(name).unwrap();
            let persistence = p.garch_alpha + p.garch_beta + p.garch_gamma / 2.0;
            let half_life = (0.5f64).ln() / persistence.ln();
            // 1.0, not GARCH_PERSISTENCE_CEILING. That constant is the
            // bound a calibration SEARCH is held inside, not the point the
            // process diverges: pt-v1 ships at 0.99 and is perfectly
            // stationary. Asserting the search bound here failed pt-v1 on
            // the first run of this test.
            assert!(
                persistence < 1.0,
                "{name}: persistence {persistence} at or above 1.0 — variance \
                 grows without bound"
            );
            assert!(
                (0.75..1.0).contains(&persistence),
                "{name}: persistence {persistence}, shock half-life \
                 {half_life:.1} days. Below 0.75 there is no volatility \
                 clustering left to speak of. If this moved on purpose, \
                 update the module note and §115."
            );
        }
    }

    #[test]
    fn persistence_is_high_enough_for_crises_to_last() {
        // ALPHA + BETA + GAMMA/2 is the decay rate of a shock (the GJR term
        // is live on roughly half of days, hence the half-weight — the
        // GJR-GARCH stationarity condition for symmetric innovations). At
        // 0.99 a shock has half-decayed after ~69 days; at 0.9 it would be
        // gone in a week and the model would have no crises, only bad
        // afternoons.
        let persistence = ALPHA + BETA + GAMMA / 2.0;
        assert!(
            (0.98..1.0).contains(&persistence),
            "persistence {persistence} — below 0.98 crises evaporate, at or above 1.0 the \
             process is non-stationary and volatility runs away"
        );
    }

    #[test]
    fn a_down_day_raises_tomorrows_volatility_at_least_as_much_as_an_up_day() {
        // The GJR property, the reason the term exists. At GAMMA = 0 the two
        // sides are exactly equal — the symmetric reference, bit-for-bit —
        // and any positive GAMMA makes the down side strictly larger, which
        // is the leverage effect real equities have (design finding 8).
        let up = update_garch_variance(BASE, 0.02, BASE);
        let down = update_garch_variance(BASE, -0.02, BASE);
        assert!(
            down >= up,
            "a −2% day must raise variance at least as much as a +2% day: {down} vs {up}"
        );
        if GAMMA > 0.0 {
            assert!(
                down > up,
                "with GAMMA = {GAMMA} a −2% day must raise variance STRICTLY more \
                 than a +2% day: {down} vs {up}"
            );
        } else {
            assert!(
                down == up,
                "at GAMMA = 0 the update must be exactly the symmetric reference: \
                 {down} vs {up}"
            );
        }
    }

    // ── INVARIANTS 2.7 / 5.5: volatility clustering ───────────────────────

    #[test]
    fn a_large_return_raises_tomorrows_volatility() {
        // The defining property. Named as a day-one property test in
        // INVARIANTS and, until now, written nowhere.
        let calm = update_garch_variance(BASE, 0.001, BASE);
        let shocked = update_garch_variance(BASE, 0.08, BASE);
        assert!(
            shocked > calm,
            "an 8% day must raise variance above a 0.1% day: {shocked} vs {calm}"
        );
    }

    #[test]
    fn volatility_clusters_rather_than_resetting() {
        // A shock must still be visible days later — that is what
        // "clustering" means, as opposed to a one-day spike.
        let mut shocked = update_garch_variance(BASE, 0.08, BASE);
        let quiet_after_shock = {
            // Five quiet days FOLLOWING the shock.
            for _ in 0..5 {
                shocked = update_garch_variance(shocked, 0.0, BASE);
            }
            shocked
        };
        let never_shocked = {
            let mut v = BASE;
            for _ in 0..6 {
                v = update_garch_variance(v, 0.0, BASE);
            }
            v
        };
        assert!(
            quiet_after_shock > never_shocked * 1.2,
            "five days after an 8% move, variance must still be elevated: {quiet_after_shock} \
             vs {never_shocked}"
        );
    }

    #[test]
    fn the_shock_does_eventually_decay() {
        // Clustering that never decays is a runaway, not a cluster.
        let mut v = update_garch_variance(BASE, 0.15, BASE);
        for _ in 0..400 {
            v = update_garch_variance(v, 0.0, BASE);
        }
        assert!(
            v <= BASE * FLOOR_MULTIPLE * 1.000001,
            "after 400 quiet days variance should have fallen to its floor, got {v}"
        );
    }

    #[test]
    fn variance_stays_inside_its_sector_bounds_however_hard_it_is_driven() {
        let mut v = BASE;
        for day in 0..1000 {
            // Alternating extreme and zero returns — the worst case for a
            // process with 0.99 persistence.
            let ret = if day % 2 == 0 { 0.9 } else { 0.0 };
            v = update_garch_variance(v, ret, BASE);
            assert!(
                v <= BASE * CEILING_MULTIPLE * 1.000001,
                "day {day}: variance {v} escaped its {CEILING_MULTIPLE}x ceiling"
            );
            assert!(
                v >= BASE * FLOOR_MULTIPLE * 0.999999,
                "day {day}: variance {v} fell through its {FLOOR_MULTIPLE}x floor"
            );
        }
    }

    #[test]
    fn a_higher_volatility_sector_carries_a_higher_floor_and_ceiling() {
        // The bounds are per-sector, so a utility and a biotech do not
        // converge on the same volatility after a long quiet stretch.
        let calm_sector = 0.008 * 0.008;
        let wild_sector = 0.035 * 0.035;
        let settle = |base: f64| {
            let mut v = base;
            for _ in 0..500 {
                v = update_garch_variance(v, 0.0, base);
            }
            v
        };
        assert!(settle(wild_sector) > settle(calm_sector));
    }

    #[test]
    fn the_bounds_are_applied_max_of_min_and_the_order_shows() {
        // With a non-positive base variance the ceiling falls below the
        // floor, and `max(min(x, hi), lo)` then returns `lo` for every input.
        // Not reachable from the live tick loop, but it is what the original does and
        // the two orderings disagree here.
        assert_eq!(update_garch_variance(0.01, 0.5, 0.0), 0.0);
        for v in [0.0, 0.001, 1.0] {
            assert_eq!(update_garch_variance(v, 0.0, 0.0), 0.0);
        }
    }
}
