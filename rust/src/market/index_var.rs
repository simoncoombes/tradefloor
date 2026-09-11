//! The index's own one-day-ahead conditional variance — the quantity a VIX
//! prices, and the one the engine never computed.
//!
//! # Why this module exists
//!
//! The only place the market's own volatility reached the VIX was
//! `engine.rs`'s read-back, which returned the market FACTOR's conditional
//! sigma scaled by `market_vol_vix_anchor / market_factor_sigma` — 2105.1
//! VIX points per unit of daily sigma where the identity (one point is one
//! per cent annualised) is `100 * sqrt(252)` = 1587.5. Two errors in one
//! line: a conversion 1.326x the identity's, and the wrong referent. The
//! index is not the factor. Measured on the certified roster the index
//! carries **2.05x the factor's variance** — factor 55 per cent, jumps 23,
//! sector and idiosyncratic 11, the intraday curve's inflation 4.6, news 3
//! — and none of the remainder ever reached the VIX.
//!
//! What this module computes is the right-hand side of
//!
//! ```text
//! V_t = K * [ beta_w^2 * v_f                                  the market factor
//!           + sum_s (sum_{i in s} w_i L_i)^2 * sigma_s,t^2    the sector factors
//!           + sum_i w_i^2 * (sigma_i,t * iota_i * c_i)^2 ]    per-name noise
//!     + lambda_m,t * (mu_m^2 + sigma_m^2)                     the market jump
//!     + sum_i w_i^2 * lambda_i,t * sigma_J^2                  company jumps
//!     + nu * sigma_news^2 * sum_i w_i^2                       endogenous news
//! ```
//!
//! in fraction² per session, from states the engine already holds at the
//! close. Every term names the line it is read from in
//! [`index_conditional_variance`].
//!
//! # What it leaves out, and how much that is
//!
//! Stated rather than absorbed, because a variance that silently omits a
//! term is the same class of defect as one that reads the wrong process:
//!
//! - **The downside transmission tilt's RECENTRING RESIDUAL**, which is the
//!   half of the tilt that is a drift and not a variance. The tilt itself
//!   is carried — see [`IndexVarianceTerms::tilt_raw`] — but
//!   `market_beta_down_asym` scales one side of a zero-mean draw and so
//!   moves its MEAN, and `market_beta_down_asym_recentre` gives back
//!   exactly the unamplified, unlagged mean. What is left over is
//!   `d beta_w (L N / 2 - recentre / sqrt(2 pi))` per tick, where `N` is
//!   `E[|z| A(|z|)]` and `L` the lag multiplier: zero on an unlagged
//!   session with the amplifier silent, and a genuine daily drift on a
//!   lagged one. A drift is not variance, so leaving it out makes this
//!   variance a SECOND MOMENT rather than a central one and therefore
//!   slightly HIGH. Measured on the tick by
//!   `the_recentring_residual_is_the_size_it_is_claimed_to_be`: on a lagged
//!   session at pt-v19's dials it is **0.29 per cent of the market block
//!   and 0.21 per cent of `V_t`** at the anchor, rising to 0.62 and 0.56
//!   per cent at the factor variance the pin ladder's deepest rung runs at.
//!   It is the one item on this list whose sign is known and whose omission
//!   is not conservative: every other one makes `V_t` low and this one
//!   makes it high.
//! - **Reversion, momentum, crowd lean and the squeeze**, measured together
//!   at 0.6 per cent of the index's variance, and the cross-covariances
//!   between components, measured at 2.6 per cent.
//! - **`crisis_blend_variance_damp`**, which scales the blend's injection by
//!   a clamped fractional power of the draw itself. That is not a polynomial
//!   in `z`, so it has no moment in `phi` and `Phi` the way the two
//!   mechanisms B4 closed do. It is 0.0 in every shipped preset and inert there
//!   through `factors.rs`'s own branch; on a preset that set it, the crisis
//!   term below prices the UNDAMPED blend and overstates the regime.
//! - **The crash amplifier's and the tilt's effect on each name's GARCH rest
//!   point.** [`resting_garch_variances`] solves the rest point from a noise
//!   variance that carries the market factor unamplified and untilted, so the
//!   anchor's per-name block is short wherever the tick's absolute floor does
//!   not already bind — which on the shipped preset is nearly nowhere.
//!
//! **"Together those are worth about five per cent of `V_t`" used to stand
//! here, and it is no longer true.** It was true of the list this one
//! replaced, which had four items and the largest of them at 1.5 per cent.
//! The figure is withdrawn rather than re-estimated: nobody has measured
//! the new list and a number nobody measured is what this section exists to
//! refuse. What survives unchanged is the principle — they are not quietly
//! folded into a coefficient, and the residual is the residual.
//!
//! # The downside transmission tilt and its lagged wire
//!
//! **The largest omission this module ever had, and it was on the residual
//! list above rather than in the sum.** `market_beta_down_asym` scales one
//! side of a zero-mean draw, which raises its variance by `a + a^2/2` — 2.5
//! per cent of the factor term at the shipped 0.025.
//! `market_beta_down_asym_lag`, which pt-v18 introduced at **0.375**, scales
//! the whole transmission by `1 + lag` on the session after a down day
//! whatever the tick's own sign — so it multiplies the factor term by
//! `(1 + lag)^2` = **1.891** on those days, not by 1.025. `prev_day_down`
//! reads `prev_day_factor < 0.0` on a zero-mean accumulated sum, so it is
//! true on about half of all sessions, and the read-back was short by 89
//! per cent of the factor block on every one of them.
//!
//! It is carried now, and it needed no new machinery: the wire is a
//! deterministic one-bit state the close already holds
//! (`factor_vol::prev_day_down`, passed in rather than re-derived), and the
//! tilt is elementary because `z^2 A^2` is EVEN — the amplifier cannot tell
//! the two half-lines apart, so the tilt splits the loading and not the
//! moment. See [`IndexVarianceTerms::tilt_raw`] for the derivation and
//! [`transmission_loadings`] for the two loadings it is built from.
//!
//! What it costs: the read-back rises by `(1 + (1 + a)^2)/2 - 1` of the
//! market block on a lagged session with `a = lag` — 89 per cent — and by
//! 2.5 per cent of it on every session through the tick-sign tilt. That is
//! a level change on the anchor as well as on the conditional read, which
//! is why [`index_unconditional_variance`] now averages the identity over
//! the lag bit instead of evaluating it at the unlagged branch: the bit is
//! a fair coin on a zero-mean sum, and an anchor read at one face of it
//! would be the mean of nothing.
//!
//! # The crash amplifier and the crisis blend — charter bar B4
//!
//! **These two used to be on the list above**, described as "conditional on
//! a tail the closed form has no moment for … silent through the calm range
//! this variance is read over, and understated in a crisis". The second half
//! of that was true and the first half was not: the moments exist and are
//! elementary, and this module now carries them. See
//! [`amplifier_moments`] for the derivation and
//! [`IndexVarianceTerms::crash_raw`] / [`IndexVarianceTerms::crisis_raw`]
//! for what each buys.
//!
//! The consequence of leaving them out was not a rounding error. The
//! loop-gain run (`programme/results/loopgain2/loopgain-report.md`, P3)
//! measured the index realising **4.0 to 4.9 times** the variance `V_t`
//! priced at pins above the crisis threshold, against 1.22 to 1.44 below
//! it — a step, in the one place the read-back was blind. `vix_target_shock_cap`
//! was the brake holding the resulting divergence, which made a boundary
//! condition into a shape parameter and flattened the fear response inside
//! the range the tape can grade. That is charter bar B4, and no shipped
//! preset had ever met it.
//!
//! # WHAT PRICING THE REGIME EXPOSES, AND IT IS NOT A SMALL THING
//!
//! **The old read-back's conservatism was load-bearing for the loop's
//! stability, and the size of that is now measured rather than suspected.**
//! §9 of the loop-gain report lists "the size of the crisis-regime loop's
//! gain" as undetermined by its design. It is determinable from here, on a
//! pin ladder: pin the VIX at `x * anchor` before every open and read what
//! this identity implies, and the static gain is
//! `d ln(implied) / d ln(pinned)`. Measured on seeds 101-103, 80 scored
//! days per seed per pin, pt-v19's dials:
//!
//! ```text
//! pin x          0.70   1.00   1.50   1.60  | 1.75   2.00   2.50   3.00
//! pinned VIX     13.5   19.3   28.9   30.9  | 33.8   38.6   48.2   57.9
//! implied/pinned
//!   before B4    0.980  0.835  0.745  0.736 | 0.727  0.716  0.698  0.689
//!   after  B4    0.963  0.828  0.764  0.761 | 1.156  1.267  1.335  1.425
//! ```
//!
//! The bar is `crisis_vix_threshold`, 30.88. **Below it the two agree to
//! within two per cent and the map stays under one, so the calm regime is
//! untouched.** Above it the old read-back stayed at 0.70 — a contraction,
//! which is the brake — and this one crosses one and keeps climbing. The
//! between-pin gain runs 1.23 to 1.68 up there against 0.87 to 0.93 before.
//! A map above one has no fixed point, so a state that crosses the
//! threshold runs to `vix_ceiling`: measured over ten seeds at 252 days,
//! days above the threshold go from 1.03 per cent to 17.90 per cent and the
//! ceiling, never touched before, is visited on 37 of 2,520.
//!
//! **That is not this module being wrong.** The identity here is asserted
//! against `factors::calculate_live_factors` itself, over the quadrature,
//! by `the_regime_terms_reproduce_the_index_the_tick_builds`: the engine
//! really does realise that much variance in a crisis, and the blend really
//! does raise every name's market loading from `beta_i` to
//! `beta_i + crisis_blend_gain * spike` — 1.81 against 1.0 at saturation,
//! which is 3.3x on the factor block before the amplifier touches it. What
//! was wrong was a VIX that could not see it.
//!
//! **What it means is that `crisis_blend_gain` and `crisis_vix_threshold`
//! are now un-derived.** Both were searched against a read-back blind to
//! them, so neither was ever constrained by what it does to the loop. The
//! loop-gain report's §8.2 anticipated exactly this and said whose call it
//! is: "Either the identity prices the regime it runs in, or the blend's
//! own dials are re-derived so the regime is entered by genuine sessions.
//! This widens §2.2's scope to the crisis dials and is Simon's call;
//! flagged, not proposed." The identity half is done here. The dials are
//! not touched, and pt-v19's certified panel does not survive this: the
//! down-tail row `index_tail_dn3_pct` reads 5.14 per cent against a band of
//! 0.47 to 1.96 where it read 1.5671 before.
//!
//! # THE STABILITY CONDITION, AND WHY IT DOES NOT BIND ON THE CRISIS DIALS
//!
//! The condition the crisis dials would be derived from is writable now.
//! Under `vix_level_identity` the VIX's deterministic map is
//! `v -> implied(v)` — the fear excursion is made zero-mean by
//! `expected_return_spike`, and on a pinned ladder the loop's own fixed
//! point sits within a fifth of a VIX point of the identity's — pinned at
//! 14 with the blend off, `implied / pinned` reads 1.007 while the day's
//! own update reads -0.015 — so that is a measurement and not an
//! assumption. The VIX lives on `[10, vix_ceiling]`, and the top of that
//! interval is absorbing exactly when `implied(v) >= v` near it. So:
//!
//! > **(S)** `implied(v) < v` for every `v` in
//! > `(crisis_vix_threshold, vix_ceiling]`.
//!
//! `implied(v) / v` rises on the saturated range — `amplifier_moments`' own
//! second moment rises with the regime ratio while every other block is at
//! most quadratic in `v` — and the spike is saturated at the ceiling for any
//! threshold under `vix_ceiling - ramp * cap`. **So (S) binds at the ceiling
//! and there alone, and the binding constraint does not contain
//! `crisis_vix_threshold` at all.** With `crisis_blend_source` 1.0 the
//! sector leak `C` is zero, `P_2` is a quadratic in the gain, and (S) at
//! equality is a quadratic whose positive root is closed form:
//!
//! ```text
//! R [ (b L)^2 tau + b L (2 + d) cap g + cap^2 g^2 ] + Q
//!     = vix_ceiling^2 / ((1 + premium)^2 * 100^2 * 252)
//! ```
//!
//! with `R = k v_f E[z^2 A^2]`, `Q` everything the blend does not touch,
//! `tau = (1 + (1 + d)^2) / 2` and `b L` the transmitted loading. Measured
//! on seeds 101-103 that root is **0.1496** against the shipped 0.8276,
//! and re-measuring the ladder at it confirms `implied(80)/80 = 0.942`.
//!
//! **And it does not settle anything, because the runaway is not the
//! blend's.** With `crisis_blend_gain` set to exactly **0.0** — the blend
//! switched off, not merely reduced — the VIX still reaches `vix_ceiling`
//! on 81 of 7,560 seed-days over the certification protocol's thirty
//! rosters, on three of them (110, 114 and 115); at `48cfcab`, without the
//! tilt term, 78 on the same three. Before B4 it reached the ceiling on 0
//! of 7,560 and no seed exceeded 60, so the runaway arrived with B4 and is
//! not the tilt's.
//!
//! **Over 120 rosters it is 11 of 120 at a gain of zero, and it follows the
//! DRAW STREAM rather than the roster.** Hold the roster at one universe
//! and vary the market seed: 14 of 120 runs pin the VIX at its ceiling.
//! Hold the market seed and vary the roster: **0 of 120**, and the highest
//! VIX any of those 120 rosters reaches is 68.37. The runaway seeds are the
//! same seeds either way. Roster properties barely separate the runs that
//! reach the ceiling from those that do not (`beta_w` 0.983 against 0.968);
//! the factor variance's own peak over baseline separates them sevenfold,
//! 21.68 against 3.05. The blend is a multiplier and a large one — the
//! shipped gain takes 11 runs of 120 to 30, and 164 ceiling days to 1,477 —
//! and it is not the cause. Measured by `b4read1`, whose registration and
//! result live in the design repository.
//!
//! The chain, measured:
//!
//! - the market factor's variance process makes excursions of 20 to 50
//!   times its target lasting tens of sessions, **and they are ordinary**.
//!   The fast component alone fails the fourth-moment condition
//!   (`3 alpha^2 + 2 alpha beta + beta^2` = 1.1035), which was already on
//!   the record; the SHIPPED process is a 0.65/0.35 mixture whose own
//!   condition is the spectral radius of a 4x4 matrix and reads 0.9870,
//!   **under one**, so the shipped factor variance has a finite fourth
//!   moment. Its dispersion is heavy and finite — implied factor kurtosis
//!   13 against the tape's own GARCH at 11.3 — and is the finite-sample
//!   dispersion of any GARCH at this persistence. The excursion this
//!   read-back turns into a ceiling-pinned VIX is the variance process
//!   behaving like the tape, which makes the finding worse and not better;
//! - those excursions are upstream of everything here, and the read-back
//!   cannot reach them. Pinned at VIX 14 — the loop cut, the target held at
//!   0.45 times base — seed 114's factor variance sits at a median 11.7
//!   times base over eighty scored sessions on pt-v19 before B4, at
//!   `48cfcab` and at this commit alike, the three agreeing to within one
//!   per cent, where seed 101 reads 0.38. The residual is the derived
//!   anchor, which differs between the builds and so moves the target;
//! - at the deepest point of that excursion, 14.0 times base, the regime
//!   ratio is 3.74 and the amplifier's threshold sits half a conditional
//!   sigma out, so `amplifier_moments`' second moment is 3.48: the
//!   amplifier term is two and a half times the factor block and this
//!   identity reads back an implied VIX of 157 from a state the old
//!   read-back reported as a high but bounded one. Correctly — it is
//!   asserted against the tick.
//!
//! So the dial whose loop gain the read-back has newly exposed is
//! `crash_amplifier_slope`, against `market_vol_alpha` and
//! `market_vol_beta`; `crisis_blend_gain` sits on top of that and can make
//! it worse but cannot make it well. A gain near 0.05 passes the certified
//! bands at both horizons and leaves the ceiling reached on 93 of 7,560
//! seed-days against 81 at a gain of zero, which is the shape of the
//! problem: the bands can be satisfied while the loop stays broken, so the
//! bands are not what decides this. The routes that would settle it, none
//! of them a dial on this module: normalise `crash_amplifier`'s
//! `shock_magnitude` by the CONDITIONAL sigma rather than the base one —
//! the alternative `factors.rs` weighs and rejects, whose cost it has
//! already measured — which makes `E[z^2 A^2]` flat in the regime and
//! removes the superlinear term from (S) entirely; or recalibrate
//! `market_vol_alpha` and `market_vol_beta`, which the design repository has
//! already derived from the tape at 0.1059 and 0.8787 against the shipped
//! 0.28035 and 0.69245, and which moves every preset from pt-v13 on; or give `crisis_blend_variance_damp` a moment so it can be used to
//! bound the blend's own level effect, which is an incomplete-gamma
//! integral rather than the `phi` and `Phi` the rest of this module needs.
//!
//! # Draws
//!
//! None, anywhere in this module. It reads state and parameters.

use crate::mathx;
use crate::params::ModelParams;


/// One session's worth of the market's tick grid, which is what the
/// intraday curve is averaged over.
const TICKS_PER_SESSION: usize = 390;

/// One name's contribution to the index, as the variance identity reads it.
///
/// `weight` is the previous-close capitalisation weight — the same weights
/// `Engine::advance_day_with` builds the day's index return from — and
/// `sector` indexes the engine's own `sector_keys` order.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct NameVariance {
    pub weight: f64,
    pub beta: f64,
    pub sector: usize,
    /// The name's GARCH variance state, BEFORE the tick's floor.
    pub garch_variance: f64,
    pub market_cap: f64,
}

/// The session mean of the intraday multiplier SQUARED — `K` in the
/// identity above.
///
/// `hours::intraday_vol` is `1 + 0.2 (2t - 1)^4` and multiplies the whole
/// noise term at `tick.rs:979,1011`, market factor included, while the
/// factor's own variance state accumulates the RAW draw
/// (`engine.rs`, `accumulate(shared_factors.market_factor)`). So every
/// noise source realises `K` times the variance its own state says, and a
/// VIX read off the state without it is 4.1 per cent low in volatility.
///
/// Computed over the tick grid the session actually runs rather than taken
/// as the integral `1 + 2/25 + 1/225 = 1.084444...`: the ticks are the 390
/// minutes of a session at `t = i/390`, and the discrete mean is what the
/// engine realises. Derived, not chosen — there is no constant here to
/// disagree with.
pub fn intraday_variance_factor() -> f64 {
    let mut total = 0.0;
    for i in 0..TICKS_PER_SESSION {
        let t = i as f64 / TICKS_PER_SESSION as f64;
        let m = crate::market::hours::intraday_vol(t);
        total += m * m;
    }
    total / TICKS_PER_SESSION as f64
}

/// `1 / sqrt(2 pi)`, the standard normal's density at zero. A literal
/// because `sqrt` is not `const`, and asserted against `mathx::sqrt` beside
/// this module in the form `factors.rs` asserts `SQRT_TWO_PI`.
const INV_SQRT_TWO_PI: f64 = 0.3989422804014327;

/// `sqrt(2)`, the argument scale between `erfc` and the normal's tail.
const SQRT_TWO: f64 = 1.4142135623730951;

/// The standard normal density at `c`.
fn standard_normal_pdf(c: f64) -> f64 {
    INV_SQRT_TWO_PI * mathx::exp(-0.5 * c * c)
}

/// The standard normal's UPPER tail, `Q(c) = 1 - Phi(c)`.
///
/// Written as `erfc(c / sqrt 2) / 2` rather than `1 - Phi(c)`: at the
/// exceedances this module evaluates — three to nine sigmas in a floored
/// regime — `Phi(c)` is one to within a double's last places and the
/// subtraction would return zero where the true tail is 1e-12. The
/// complement is computed directly and never cancels.
fn standard_normal_upper_tail(c: f64) -> f64 {
    0.5 * mathx::erfc(c / SQRT_TWO)
}

/// **The crash amplifier's first and second moments, in closed form.** The
/// pair `(E[z² A], E[z² A²])` for a standard normal `z`, where `A` is the
/// amplifier the tick applies.
///
/// # The set-up
///
/// The tick draws `f = z * sigma_tick` with `sigma_tick = sqrt(v_f) / sqrt(390)`
/// (`tick.rs`, `market_factor`), and normalises the shock by the BASE sigma
/// `base = market_factor_sigma / sqrt(390)` — a constant, deliberately
/// (`factors.rs`: "extreme stays denominated in absolute units … a
/// high-variance factor regime pushes MORE ticks past the threshold"). So
/// with the **regime ratio**
///
/// ```text
/// s = sigma_tick / base = sqrt(v_f) / market_factor_sigma
/// ```
///
/// the shock magnitude is `|f| / base = s|z|` and the amplifier
/// (`factors.rs:551`) is
///
/// ```text
/// A(z) = 1 + m * max(0, s|z| - T)  =  1 + a (|z| - c)  for |z| > c, else 1
/// with a = m s  and  c = T / s.
/// ```
///
/// **`s` is what makes the amplifier's contribution regime-dependent**, and
/// it is why this cannot be a constant inflation: at `s` 0.3 the threshold
/// sits 6.7 sigmas out and `A` is 1 for every tick of a year; at `s` 2.0 it
/// sits at one sigma and a third of the session is amplified.
///
/// # The moments
///
/// `f A(|f|)` is ODD in `f`, so it has no mean and the amplifier moves no
/// first moment — only the second. With `u = |z|` half-normal (density
/// `2 phi(u)`) and `M_k(c) = E[u^k 1{u > c}]`, expanding `A` and `A²` gives
///
/// ```text
/// E[z² A ] = 1 +   a (M_3 - c M_2)
/// E[z² A²] = 1 + 2 a (M_3 - c M_2) + a² (M_4 - 2 c M_3 + c² M_2)
/// ```
///
/// and the three truncated moments are elementary:
///
/// ```text
/// M_2(c) = 2 [ c phi(c) + Q(c) ]
/// M_3(c) = 2 (c² + 2) phi(c)
/// M_4(c) = 2 [ (c³ + 3c) phi(c) + 3 Q(c) ]
/// ```
///
/// with `phi` the standard normal density and `Q = 1 - Phi` its upper tail.
/// `E[z²] = 1` is the `1` each series opens with, so at `a = 0` — or at any
/// `c` past which the tail underflows — both return exactly 1.0 and the
/// amplifier costs nothing.
///
/// # What it is NOT
///
/// This is the amplifier's moment **per tick**, and 390 independent ticks
/// make the day, so it multiplies the factor block and does not add to it.
/// It says nothing about the amplifier's effect on the downside transmission
/// tilt's recentring, which is still on the residual list above and is about
/// one per cent of the tilt.
///
/// Returns `(1.0, 1.0)` when the amplifier cannot fire — `slope` zero, or a
/// regime whose sigma is zero — through a branch, so a preset with the
/// amplifier off is bit-identical to one that never had this term.
pub fn amplifier_moments(slope: f64, threshold: f64, regime_ratio: f64) -> (f64, f64) {
    if slope == 0.0 || !(regime_ratio > 0.0) {
        return (1.0, 1.0);
    }
    let a = slope * regime_ratio;
    let c = threshold / regime_ratio;
    let phi = standard_normal_pdf(c);
    let q = standard_normal_upper_tail(c);
    let m2 = 2.0 * (c * phi + q);
    let m3 = 2.0 * (c * c + 2.0) * phi;
    let m4 = 2.0 * ((c * c * c + 3.0 * c) * phi + 3.0 * q);
    // The two brackets, named because each is a moment of the amplified
    // process and not an intermediate: the first is `E[z² (A - 1)] / a` and
    // the second `E[z² (A - 1)²] / a²`. Both are non-negative — the second
    // is an expectation of a square, and the first is `E[u²(u - c) 1{u>c}]`,
    // whose integrand is non-negative wherever the indicator is on.
    let linear = m3 - c * m2;
    let quadratic = m4 - 2.0 * c * m3 + c * c * m2;
    (
        1.0 + a * linear,
        1.0 + 2.0 * a * linear + a * a * quadratic,
    )
}

/// **The index's market loading on an up tick and on a down tick**, as the
/// transmission tilt splits it — and the first and second moments of that
/// split loading, which is what the variance identity needs.
///
/// # What the tick does
///
/// `factors::calculate_live_factors` builds a name's market component in
/// three steps, and the ORDER of them is the whole content of this
/// function:
///
/// ```text
/// factor_through = beta_i * F                      the draw
///                * (1 + d)      if F < 0           the tick-sign tilt
///                * (1 + lag)    if prev_day_down   the lagged wire
/// market_component = factor_through + q g p F      the crisis injection
/// random_noise    += market_component * A(|F|)     the crash amplifier
/// ```
///
/// So the tilt and the lag multiply `beta_i` and **not** the crisis
/// injection, which is added after them; and the amplifier multiplies both,
/// because it is applied to the sum. Cap-weighting over the roster and
/// using `sum_i w_i = 1`, the index's market-driven tick return is
/// `F A(|F|) * (beta_w L (1 + d 1{F<0}) + G)` with `L = 1 + lag` on a
/// lagged session and `G = q g p`.
///
/// # The moments
///
/// The loading takes exactly two values, one per half-line, and `z^2 A(|z|)`
/// and `z^2 A(|z|)^2` are both EVEN — the amplifier normalises by `|F|`, so
/// it cannot tell the half-lines apart. Each half therefore carries exactly
/// half of whichever amplifier moment it multiplies, and
///
/// ```text
/// E[z^2 A^2 (loading)^2] = (up^2 + down^2) / 2  *  E[z^2 A^2]
/// E[z^2 A   (loading)  ] = (up + down) / 2      *  E[z^2 A]
/// ```
///
/// which is why this returns `((up + down)/2, (up^2 + down^2)/2)` and the
/// identity multiplies each by the amplifier moment of the matching order.
/// At `d = 0` both collapse to `up` and `up^2` and the tilt has cost
/// nothing; at `lag = 0` on an unlagged session `L` is 1 and `up` is the
/// `beta_w + G` the calm identity already had.
///
/// The `(1 + (1 + d)^2) / 2` the module's residual list used to quote is
/// this function at `G = 0` and `L = 1`, and it is `1 + d + d^2/2` — the
/// `a + a^2/2` the same bullet quoted, which is the check that the two
/// statements of the tilt were ever the same statement.
///
/// # What it is NOT
///
/// The tilt also moves the loading's MEAN, because it scales one side of a
/// zero-mean draw. That is a drift and not a variance, it is given back (at
/// `L = 1`, and only there) by `market_beta_down_asym_recentre`, and the
/// leftover is on the residual list at the top of this module with its
/// measured size. Nothing here carries it.
pub fn transmission_loadings(
    beta_w: f64,
    lag_multiplier: f64,
    tilt: f64,
    injection: f64,
) -> (f64, f64) {
    let carried = beta_w * lag_multiplier;
    let up = carried + injection;
    let down = carried * (1.0 + tilt) + injection;
    ((up + down) * 0.5, (up * up + down * down) * 0.5)
}

/// The idiosyncratic sigma the TICK draws with for one name: its GARCH
/// state through the floor, its beta-dependent scale and its size
/// multiplier, in daily units.
///
/// The three factors are `factors.rs`'s own, called rather than restated,
/// so a change to either of them moves this identity with it.
fn idio_sigma_daily(p: &ModelParams, name: &NameVariance) -> f64 {
    let daily_sigma = mathx::sqrt(mathx::max(name.garch_variance, p.idio_sigma_floor));
    daily_sigma
        * crate::market::factors::idio_scale_for(p, name.beta)
        * crate::market::factors::cap_size_multiplier_with(p, name.market_cap)
}

/// The variance of ONE name's daily NOISE — the quantity the close hands
/// the GARCH as its innovation (`market/daily.rs`: "the innovation is the
/// day's NOISE, not its return").
///
/// Market factor, sector factor and idiosyncratic draw, each through the
/// intraday curve. No jumps and no news: those reach `s` outside the tick
/// loop and outside the attribution slot the innovation is summed from.
fn name_noise_variance(
    p: &ModelParams,
    name: &NameVariance,
    factor_variance: f64,
    sector_sigma: f64,
    k: f64,
) -> f64 {
    let loading = crate::market::factors::sector_loading_for(p, name.beta);
    let idio = idio_sigma_daily(p, name);
    k * (name.beta * name.beta * factor_variance
        + loading * loading * sector_sigma * sector_sigma
        + idio * idio)
}

/// The jump arrival rates at a given rate scale, in the spelling
/// `Engine::apply_jumps` uses — the branch at zero coupling included, so
/// the two cannot drift apart.
fn jump_intensities(p: &ModelParams, rate_scale: f64) -> (f64, f64) {
    if p.jump_vix_coupling == 0.0 {
        (p.jump_intensity_market, p.jump_intensity_idio)
    } else {
        (
            p.jump_intensity_market * rate_scale,
            p.jump_intensity_idio * rate_scale,
        )
    }
}

/// The six pieces [`index_conditional_variance`] sums, kept apart.
///
/// The `_raw` three are the PRE-`K` sums: the intraday curve multiplies
/// the noise block and not the jumps, which the test named
/// `the_intraday_curve_does_not_reach_the_jumps` pins, so a struct that
/// stored them already scaled would have thrown away the only distinction
/// between the two halves of the identity. `k` travels with them for the same reason: the total is
/// not reconstructible from the terms without it.
///
/// Fraction squared per session throughout, except `k`, which is
/// dimensionless.
///
/// This type exists so a measurement can read WHICH term of `V_t` a VIX
/// move went into — the loop closes through the factor, the sector and
/// the jump rate at three different couplings, and the sum alone cannot
/// say which. Nothing in the engine reads it back.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct IndexVarianceTerms {
    /// `beta_w^2 * v_f`, the market factor's block before `K`.
    pub factor_raw: f64,
    /// `sum_s (sum_{i in s} w_i L_i)^2 sigma_s^2`, before `K`.
    pub sector_raw: f64,
    /// `sum_i w_i^2 idio_i^2`, before `K`.
    pub idio_raw: f64,
    /// **The crash amplifier's contribution**, before `K`:
    /// `beta_w^2 v_f (E[z² A²] - 1)`, with the moment from
    /// [`amplifier_moments`] at the regime ratio
    /// `sqrt(v_f) / market_factor_sigma`.
    ///
    /// The amplifier MULTIPLIES the factor block, so what is kept here is
    /// the increment it adds to `factor_raw` — which is what makes the
    /// split readable: `factor_raw` stays the block the calm identity had,
    /// and this is what the tail is worth on top of it.
    ///
    /// Exactly `0.0` when `crash_amplifier_slope` is zero, through
    /// [`amplifier_moments`]' own branch. NOT zero in the calm range: at
    /// the anchor the threshold sits about 2.07 conditional sigmas out and
    /// the amplifier is worth about four per cent of the factor block. The
    /// module's old claim that it was "silent through the calm range" held
    /// only for a FLOORED factor regime.
    pub crash_raw: f64,
    /// **The crisis blend's contribution**, before `K`, and zero below
    /// `crisis_vix_threshold` through a branch on the spike.
    ///
    /// The blend is a KNOWN scalar at read-back time — `crisis_spike` is
    /// [`crate::market::tick::crisis_spike_for`] on the close's VIX, not a
    /// random variable — so it enters as a loading shift rather than as a
    /// moment. With `q = crisis_blend_source`, `g = crisis_blend_gain`,
    /// `p` the spike, `beta_w = sum w_i beta_i` and `L_w = sum w_i L_i`:
    ///
    /// ```text
    /// B = beta_w + q g p     the market loading the amplifier multiplies
    /// C = L_w p (1 - q)      the market factor leaked through the SECTOR
    ///                        slot, which the amplifier does not touch
    /// h = 1 - p (1 - q)      what survives of the sector draw itself
    /// ```
    ///
    /// so the index's market-driven variance is
    /// `v_f (B² E[z²A²] + 2 B C E[z²A] + C²)` and the sector block scales
    /// by `h²`. This field carries all of that less what `factor_raw` and
    /// `crash_raw` already hold, plus the sector attenuation:
    ///
    /// ```text
    /// crisis_raw = v_f [ (B² - beta_w²) E[z²A²] + 2 B C E[z²A] + C² ]
    ///            + (h² - 1) sector_raw
    /// ```
    ///
    /// At `crisis_blend_source` 1.0 — pt-v7 onward — `C` is 0 and `h` is 1,
    /// the sector draw arrives intact and the whole term is the loading
    /// shift `B² - beta_w²` on the amplified factor. At source 0.0 the
    /// blend rides the sector slot instead and both `C` and `h` do the
    /// work; `tick.rs`'s own branch is reproduced rather than assumed.
    ///
    /// Unmodelled here: `crisis_blend_variance_damp`, which scales the
    /// injection by `|f / base|^-d` and makes it a non-polynomial function
    /// of the draw. No shipped preset sets it and the field is asserted to
    /// be zero at the call site rather than silently ignored.
    pub crisis_raw: f64,
    /// **The downside transmission tilt and its lagged wire**, before `K`,
    /// and exactly `0.0` when neither dial is live on this session.
    ///
    /// The tick's market loading is not `beta_w` but a two-valued random
    /// variable — `beta_w L` on an up tick, `beta_w L (1 + d)` on a down one
    /// — and the crisis injection is added AFTER both, so it does not
    /// receive them. [`transmission_loadings`] returns that loading's first
    /// and second moments `(P_1, P_2)` and the market-driven block is
    ///
    /// ```text
    /// v_f [ P_2 E[z^2 A^2] + 2 C P_1 E[z^2 A] + C^2 ]
    /// ```
    ///
    /// with `C` the unamplified, untilted leak through the sector slot. This
    /// field carries what that is over and above what `factor_raw`,
    /// `crash_raw` and `crisis_raw` already hold between them — those three
    /// sum to the same expression at `P_1 = B`, `P_2 = B^2` — so
    ///
    /// ```text
    /// tilt_raw = v_f [ (P_2 - B^2) E[z^2 A^2] + 2 C (P_1 - B) E[z^2 A] ]
    /// ```
    ///
    /// and the sector attenuation is untouched, because neither wire reaches
    /// the sector slot.
    ///
    /// **`lag_multiplier` is state, not a parameter.** It is `1 + lag` on a
    /// session whose predecessor accumulated a down market factor and `1`
    /// otherwise, read from `factor_vol::prev_day_down` — the same bit
    /// `compute_tick` reads, passed in rather than re-derived, for the
    /// reason `crisis_spike_for` is shared. On the shipped 0.375 that is
    /// 1.891 on the market block, on about half of all sessions.
    ///
    /// A BRANCH at "neither wire live", so a preset before pt-v18 on a
    /// session with no tilt lands on exactly `0.0` and the sum below
    /// reproduces on bits.
    pub tilt_raw: f64,
    /// The intraday curve's second moment, [`intraday_variance_factor`].
    pub k: f64,
    /// `lambda_m (mu^2 + sigma_m^2) - (lambda_m mu)^2`.
    pub market_jump: f64,
    /// `sum_i w_i^2 lambda_i sigma_J^2`.
    pub idio_jump: f64,
    /// `nu sigma_news^2 sum_i w_i^2`.
    pub news: f64,
}

impl IndexVarianceTerms {
    /// The index's conditional variance: what
    /// [`index_conditional_variance`] returns.
    ///
    /// THE SAME OPERATIONS IN THE SAME ORDER as the expression this
    /// replaced, which is a stronger claim than equality and the only one
    /// worth making here. Floating-point addition does not associate, so
    /// `k * (a + b + c) + d + e + f` regrouped anywhere is a different
    /// number in the last place — and the last place is not nothing: this
    /// variance is the VIX, the VIX is the factor's target, and a run
    /// diverges from a run that differs by an ULP. Asserted on bits by
    /// `the_terms_sum_to_the_variance_they_were_split_from`.
    ///
    /// **The regime and tilt terms are appended to the noise block, not
    /// inserted into it.** `crash_raw`, `crisis_raw` and `tilt_raw` are
    /// added AFTER `idio_raw`, so on a state where all three are exactly
    /// zero — an amplifier switched off, a VIX under the crisis threshold
    /// and neither transmission wire live — the sum is
    /// `k * (a + b + c + 0.0 + 0.0 + 0.0) + …`, which is bit-identical
    /// to what stood here: IEEE-754 addition of `+0.0` is exact for every
    /// finite operand. That is the property
    /// `the_calm_regime_reproduces_the_sum_it_replaced` asserts, and it is
    /// why the three new terms could be given their own fields rather than
    /// folded into `factor_raw`.
    pub fn total(&self) -> f64 {
        self.k
            * (self.factor_raw + self.sector_raw + self.idio_raw + self.crash_raw
                + self.crisis_raw + self.tilt_raw)
            + self.market_jump
            + self.idio_jump
            + self.news
    }
}

/// The cap-weighted index's one-day-ahead conditional variance, in
/// fraction² per session.
///
/// `factor_variance` is the market factor's variance state as it stands
/// after the close (`MarketVarianceState`, so already tomorrow's),
/// `sector_sigma` is `tick::sector_sigma_for` at the close's VIX, and
/// `jump_rate_scale` is `apply_jumps`' own `(1 - c) + c * ratio^2`.
///
/// # Term by term
///
/// | term | read from |
/// |---|---|
/// | `beta_w = sum w_i beta_i` | `factors.rs`, `beta * shared.market_factor` |
/// | sector loadings `L_i` | `factors::sector_loading_for` |
/// | idiosyncratic sigma | `factors::idio_scale_for`, `cap_size_multiplier_with`, the tick's floor |
/// | `K` | [`intraday_variance_factor`], `hours::intraday_vol` |
/// | market jump | `engine.rs`, `apply_jumps`, `jump_mean_market`/`jump_sigma_market` |
/// | company jumps | the same, `jump_sigma_idio` |
/// | news | `engine.rs`, `open_market`'s endogenous news, impact `sigma * z` spread over the session |
///
/// The market jump's second moment is `lambda (mu^2 + sigma^2)` and its
/// variance is that less `(lambda mu)^2`; the compensator
/// (`jump_mean_compensated`) subtracts a deterministic offset, which moves
/// no central moment. The `(lambda mu)^2` correction is 5 per cent of the
/// market-jump term at the shipped intensity and is TAKEN, because it costs
/// one multiply and leaving it out would be a term dropped for tidiness.
///
/// `sector_count` bounds the sector accumulator; a name whose sector index
/// is outside it contributes no sector term, which is
/// `SharedFactors::sector`'s own "an absent sector is zero, not a panic".
pub fn index_conditional_variance(
    p: &ModelParams,
    names: &[NameVariance],
    sector_count: usize,
    factor_variance: f64,
    sector_sigma: f64,
    jump_rate_scale: f64,
    crisis_spike: f64,
    prev_day_down: bool,
    k: f64,
) -> f64 {
    index_conditional_variance_terms(
        p, names, sector_count, factor_variance, sector_sigma, jump_rate_scale,
        crisis_spike, prev_day_down, k)
        .total()
}

/// [`index_conditional_variance`] with the terms kept rather than summed.
///
/// Same arithmetic, same order, same arguments; the only difference is
/// that the six pieces the sum is built from survive it, so a measurement
/// can ask which term of `V_t` moved rather than only that `V_t` did.
/// [`IndexVarianceTerms::total`] is the sum, and
/// [`index_conditional_variance`] is now this function followed by that
/// one — so the two cannot disagree, and the test beside this module
/// asserts the equality ON BITS rather than to a tolerance.
///
/// Nothing here is computed that the summing form did not compute. This is
/// a read-only instrument: it adds no draw, no state and no term.
pub fn index_conditional_variance_terms(
    p: &ModelParams,
    names: &[NameVariance],
    sector_count: usize,
    factor_variance: f64,
    sector_sigma: f64,
    jump_rate_scale: f64,
    crisis_spike: f64,
    prev_day_down: bool,
    k: f64,
) -> IndexVarianceTerms {
    let mut beta_w = 0.0;
    let mut weight_sq = 0.0;
    let mut idio_var = 0.0;
    // The index's TOTAL sector loading, which is not the same object as the
    // per-sector accumulator beside it: the sector block needs each sector's
    // own loaded weight squared, and the crisis blend's leak through the
    // sector slot is the market factor arriving through EVERY sector at
    // once, so it loads on the sum. A name outside the sector table
    // contributes to neither, which is `SharedFactors::sector`'s "an absent
    // sector is zero, not a panic" read on both.
    let mut sector_loaded_total = 0.0;
    let mut sector_loaded = vec![0.0; sector_count];
    for name in names {
        beta_w += name.weight * name.beta;
        weight_sq += name.weight * name.weight;
        let idio = idio_sigma_daily(p, name);
        idio_var += name.weight * name.weight * idio * idio;
        if name.sector < sector_count {
            let loaded = name.weight * crate::market::factors::sector_loading_for(p, name.beta);
            sector_loaded[name.sector] += loaded;
            sector_loaded_total += loaded;
        }
    }
    let mut sector_var = 0.0;
    for loaded in sector_loaded.iter() {
        sector_var += loaded * loaded * sector_sigma * sector_sigma;
    }

    let factor_raw = beta_w * beta_w * factor_variance;

    // ── The regime this variance is read in ───────────────────────────────
    //
    // `regime_ratio` is the conditional factor sigma over the BASE sigma the
    // amplifier normalises by — the ratio `factors.rs` builds
    // `shock_magnitude` from, written here in daily units because the
    // `sqrt(390)` on each side cancels. See `amplifier_moments`.
    let regime_ratio = mathx::sqrt(factor_variance) / p.market_factor_sigma;
    let (amp_1, amp_2) =
        amplifier_moments(p.crash_amplifier_slope, p.crash_amplifier_threshold, regime_ratio);
    // `E[z² A²] - 1` rather than `E[z² A²]`: `factor_raw` already carries
    // the 1, and keeping the increment is what lets the calm sum reproduce
    // the sum this replaced on bits.
    let crash_raw = factor_raw * (amp_2 - 1.0);

    // ── The transmission the tick runs, which is not `beta_w` ─────────────
    //
    // The lagged wire is a bit of STATE the close already holds, passed in
    // rather than re-derived here, for the same reason `crisis_spike_for` is
    // shared with the tick: the read-back and the tick must not be able to
    // disagree about which session is which. A branch at each dial's zero,
    // so a preset without the wire lands on exactly 1.0.
    let lag_multiplier = if p.market_beta_down_asym_lag == 0.0 || !prev_day_down {
        1.0
    } else {
        1.0 + p.market_beta_down_asym_lag
    };

    // The crisis blend, at the spike the close's VIX implies. A BRANCH at
    // zero spike, so every session under `crisis_vix_threshold` — and every
    // preset read at a VIX below it — lands on exactly `0.0` rather than on
    // a product of zeros that is only probably zero.
    let crisis_raw = if crisis_spike == 0.0 {
        0.0
    } else {
        let q = p.crisis_blend_source;
        // `B`: the market loading the amplifier multiplies. The injection
        // `source * gain * spike * market_factor` (`factors.rs:471`) is
        // added to `beta * market_factor` INSIDE `market_component`, which
        // is what `crash_amplifier` then scales, and it carries no beta —
        // every name receives the same injection, so it loads on the weights
        // and not on `beta_w`.
        let b = beta_w + q * p.crisis_blend_gain * crisis_spike;
        // `C`: the market factor leaking through the SECTOR slot
        // (`tick.rs`, `kept`), which the amplifier never sees. Zero at
        // source 1.0, where the sector draw is kept whole.
        let c = sector_loaded_total * crisis_spike * (1.0 - q);
        // `h`: what survives of the sector's own draw.
        let h = 1.0 - crisis_spike * (1.0 - q);
        factor_variance * ((b * b - beta_w * beta_w) * amp_2 + 2.0 * b * c * amp_1 + c * c)
            + (h * h - 1.0) * sector_var
    };

    // ── The tilt, on top of the loading the three terms above priced ──────
    //
    // `b` and the leak `c` are rebuilt here rather than lifted out of the
    // branch above, so that branch stays the arithmetic it was, bit for bit,
    // on every state this one is inert on. A BRANCH at "neither wire live",
    // for the same reason: at `tilt == 0.0` and `lag_multiplier == 1.0` the
    // two loadings below are the same double and the corrections are exactly
    // `0.0` by algebra — but a branch says so without asking the reader to
    // check that `(x + x) / 2` is `x`.
    let tilt_raw = if p.market_beta_down_asym == 0.0 && lag_multiplier == 1.0 {
        0.0
    } else {
        let injection = p.crisis_blend_source * p.crisis_blend_gain * crisis_spike;
        let b = beta_w + injection;
        let c = sector_loaded_total * crisis_spike * (1.0 - p.crisis_blend_source);
        let (p1, p2) = transmission_loadings(
            beta_w, lag_multiplier, p.market_beta_down_asym, injection);
        factor_variance * ((p2 - b * b) * amp_2 + 2.0 * c * (p1 - b) * amp_1)
    };

    let (lambda_m, lambda_i) = jump_intensities(p, jump_rate_scale);
    let mu = p.jump_mean_market;
    let sig = p.jump_sigma_market;
    let market_jump = lambda_m * (mu * mu + sig * sig) - (lambda_m * mu) * (lambda_m * mu);
    let idio_jump = weight_sq * lambda_i * p.jump_sigma_idio * p.jump_sigma_idio;

    let news = weight_sq * p.endogenous_news_intensity
        * p.endogenous_news_sigma
        * p.endogenous_news_sigma;

    IndexVarianceTerms {
        factor_raw,
        sector_raw: sector_var,
        idio_raw: idio_var,
        crash_raw,
        crisis_raw,
        tilt_raw,
        k,
        market_jump,
        idio_jump,
        news,
    }
}

/// How many passes the resting-variance contraction below takes.
///
/// The map is `v -> garch_rest(name_noise_variance(v))`. Its derivative is
/// the product of two bounded factors: the GARCH's own
/// `(alpha + gamma/2) / (1 - beta)`, which the stationarity ceiling holds
/// under 1, and the idiosyncratic share of a name's noise variance, which
/// is under a third on any roster where the market factor and the sector
/// exist at all. On the shipped preset the product is 0.09, so each pass
/// removes an order of magnitude and a dozen would be machine precision.
///
/// A FIXED COUNT and not a tolerance, deliberately. A tolerance that can
/// fail needs a code path for failing, and a value written under a name
/// that asserts convergence when the tolerance never fired is the exact
/// defect `vix_target_offset`'s `converged_offset` was. A contraction with
/// a bounded rate needs no such claim: the iterate is where it is after a
/// stated number of passes, and the test beside this module pins that the
/// last pass moves it by less than a part in 10^12.
const RESTING_VARIANCE_PASSES: usize = 32;

/// Each name's GARCH variance where its own process rests, given the market
/// it sits in.
///
/// The single-component update (`garch::update_garch_variance_for`) is
/// `v' = omega + alpha r^2 + gamma r^2 1{r<0} + beta v`, clamped to
/// `[floor, ceiling]` multiples of the name's sector base variance. Taking
/// `E[r^2] = R` and `E[1{r<0} r^2] = R/2` for a symmetric innovation, the
/// resting level is
///
/// ```text
/// v = (omega + (alpha + gamma / 2) * R) / (1 - beta_i)
/// ```
///
/// clamped the same way — and `R` is the name's own noise variance, which
/// depends on `v` through the idiosyncratic term. Hence the iteration.
///
/// **This is the level the engine actually runs at, and it is not the
/// sector base variance the roster is seeded with.** On the shipped preset
/// `omega` is 2e-06 for every sector, so the resting level lands under the
/// tick's absolute floor for nearly every name and the identity must read
/// the floor. Seeding the anchor off the roster's opening state instead
/// would put the per-name block two to four times too high, the anchor
/// about six per cent high, and — through the loop's own amplification —
/// the resting VIX about eighteen per cent low. Which is the defect this
/// whole change exists to remove, one level down.
///
/// The cascade path (`garch_cascade_components >= 1`) is NOT modelled: no
/// shipped or candidate preset runs it, and a resting level asserted for a
/// process this function does not solve would be a claim without a
/// derivation. Under a cascade preset the single-component resting level is
/// used and the residual is unmeasured.
pub fn resting_garch_variances(
    p: &ModelParams,
    names: &[NameVariance],
    sector_base_variances: &[f64],
    factor_variance: f64,
    sector_sigma: f64,
    k: f64,
) -> Vec<f64> {
    let mut v: Vec<f64> = names.iter().map(|n| n.garch_variance).collect();
    let shock_share = p.garch_alpha + p.garch_gamma / 2.0;
    for _ in 0..RESTING_VARIANCE_PASSES {
        for (i, name) in names.iter().enumerate() {
            let base = sector_base_variances.get(i).copied().unwrap_or(v[i]);
            let beta_i = crate::market::garch::garch_beta_for(p, name.market_cap);
            if !(beta_i < 1.0) {
                continue;
            }
            let probe = NameVariance { garch_variance: v[i], ..*name };
            let r2 = name_noise_variance(p, &probe, factor_variance, sector_sigma, k);
            let rest = (p.garch_omega + shock_share * r2) / (1.0 - beta_i);
            v[i] = mathx::max(
                mathx::min(rest, base * p.garch_ceiling_multiple),
                base * p.garch_floor_multiple,
            );
        }
    }
    v
}

/// The index's variance at the point every VIX coupling reads ONE — the
/// unconditional form of the identity, and therefore the definition of
/// `market_vol_vix_anchor`.
///
/// At `vix == anchor` the factor's target is exactly its baseline
/// (`factor_vol.rs`: the blend is `1 - c + c * 1`), `sector_sigma_for`
/// returns `sector_factor_sigma` exactly, the per-name clamp reference is
/// the sector's own base variance, and `apply_jumps`' rate scale is exactly
/// one. So this point needs no anchor to evaluate, which is what makes the
/// derivation below non-circular.
///
/// # The two regime terms at this point
///
/// **The crisis blend is off and the crash amplifier is not.** The spike is
/// passed as `0.0` rather than derived: the anchor is what the VIX reads
/// when nothing is happening, and `crisis_vix_threshold` is 30.88 against a
/// derived anchor near 20, so a crisis at the anchor would be a
/// contradiction in terms. The amplifier, by contrast, fires at the anchor —
/// the regime ratio there is exactly 1.0, the threshold sits 2.0 sigmas out
/// and the amplifier is worth about four per cent of the factor block. It is
/// part of the index's unconditional variance and belongs in the anchor.
///
/// # The lagged wire at this point, which is a COIN and not a coupling
///
/// `market_beta_down_asym_lag` is not a VIX coupling and does not read one at
/// the anchor: it is a bit of state, true on the session after a down market
/// factor. `prev_day_factor` is a zero-mean accumulated sum, so the bit is a
/// fair coin, and the index's unconditional variance is the MEAN over it —
/// which this takes literally, evaluating the identity at both faces and
/// averaging. `the_anchor_averages_over_the_coin_it_cannot_read` asserts the
/// average is the average, and measures the gap: on that test's roster the
/// two faces read 19.45 and 24.58 VIX points and the unlagged one alone is
/// 14 per cent low. The gap is a property of the roster as well as of the
/// dial, so the test asserts it is large rather than asserting a number.
///
/// Evaluating at the unlagged face instead would be the defect this whole
/// module exists to remove, one level up: a level constant read at one face
/// of a state that spends half its time at the other. And the average is
/// still a point needing no anchor to evaluate, so the derivation below
/// stays non-circular.
///
/// **What is still left out here**, and is now the largest named residual in
/// this module: [`resting_garch_variances`] solves each name's GARCH rest
/// point from `name_noise_variance`, which carries the market factor WITHOUT
/// the amplifier and WITHOUT the tilt. The resting level is under the tick's
/// absolute floor for nearly every name on the shipped preset, so the
/// omission is inert where the floor binds and understates the rest point
/// where it does not. It is stated rather than absorbed, on the same footing
/// as the rest of the list in the module docs.
pub fn index_unconditional_variance(
    p: &ModelParams,
    names: &[NameVariance],
    sector_count: usize,
    sector_base_variances: &[f64],
) -> f64 {
    let k = intraday_variance_factor();
    let factor_variance = p.market_factor_sigma * p.market_factor_sigma;
    let sector_sigma = p.sector_factor_sigma;
    let resting = resting_garch_variances(
        p, names, sector_base_variances, factor_variance, sector_sigma, k);
    let at_rest: Vec<NameVariance> = names
        .iter()
        .enumerate()
        .map(|(i, n)| NameVariance { garch_variance: resting[i], ..*n })
        .collect();
    // Both faces of the coin, averaged. A BRANCH at "no lagged wire", so
    // every preset before pt-v18 evaluates the identity exactly once and
    // lands on the bits it always did: `0.5 * (x + x)` is `x` for every
    // finite double, but the second evaluation is work done to learn
    // nothing and the branch says which presets it is for.
    if p.market_beta_down_asym_lag == 0.0 {
        return index_conditional_variance(
            p, &at_rest, sector_count, factor_variance, sector_sigma, 1.0, 0.0, false, k);
    }
    let unlagged = index_conditional_variance(
        p, &at_rest, sector_count, factor_variance, sector_sigma, 1.0, 0.0, false, k);
    let lagged = index_conditional_variance(
        p, &at_rest, sector_count, factor_variance, sector_sigma, 1.0, 0.0, true, k);
    0.5 * (unlagged + lagged)
}

/// A variance in fraction² per session, as a VIX level: the identity
/// `VIX = (1 + pi) * 100 * sqrt(252 * V)`.
///
/// One VIX point is one per cent of ANNUALISED volatility and the engine's
/// year is 252 sessions (`economy/daily.rs`, `p_daily = intensity / 252`),
/// so the conversion is `100 * sqrt(252)` = 1587.45 points per unit of
/// daily sigma and there is no constant in it to set.
///
/// `pi` is the variance risk premium, and equality is the WRONG identity: a
/// real VIX sits above its index's realised volatility, at a per-252-session
/// window median of 1.252 over 1990-2025. See
/// [`ModelParams::vix_variance_premium`](crate::params::ModelParams::vix_variance_premium)
/// for the measurement, its spread, and the estimators that disagree.
pub fn vix_from_variance(premium: f64, variance: f64) -> f64 {
    (1.0 + premium) * 100.0 * mathx::sqrt(252.0 * variance)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::params::PT_V18;

    fn roster() -> Vec<NameVariance> {
        (0..8)
            .map(|i| NameVariance {
                weight: 1.0 / 8.0,
                beta: 0.8 + 0.05 * i as f64,
                sector: i % 3,
                garch_variance: 0.000225,
                market_cap: 2.0e10,
            })
            .collect()
    }

    /// `K` is the intraday curve's own second moment, so the DISCRETE mean
    /// over the session's ticks must sit beside the integral it
    /// approximates rather than anywhere convenient.
    ///
    /// The integral of `(1 + 0.2 (2t-1)^4)^2` over one session is
    /// `1 + 2/25 + 1/225`. The tick grid is 390 left endpoints, so the two
    /// differ in the fourth decimal and the difference is the grid, not an
    /// error.
    #[test]
    fn the_intraday_factor_is_the_curves_own_second_moment() {
        let k = intraday_variance_factor();
        let integral = 1.0 + 2.0 / 25.0 + 1.0 / 225.0;
        assert!((k - integral).abs() < 5e-4, "K = {k}, integral = {integral}");
        assert!(k > 1.0, "the curve must inflate variance, not deflate it");
    }

    /// THE INSTRUMENT, asserted at construction rather than trusted: a
    /// variance that ignored a term would still be positive, still move
    /// with the factor, and still look like a variance.
    ///
    /// Each term is switched on alone and must carry the share the algebra
    /// says it does.
    #[test]
    fn each_term_enters_at_the_size_the_identity_gives_it() {
        let names = roster();
        let k = intraday_variance_factor();
        let mut p = PT_V18;
        // Factor alone.
        p.sector_factor_sigma = 0.0;
        p.jump_intensity_market = 0.0;
        p.jump_intensity_idio = 0.0;
        p.endogenous_news_intensity = 0.0;
        p.garch_omega = 0.0;
        // The amplifier off, so this stays a test of the CALM identity's
        // shares. It has its own closed-form and Monte Carlo tests below,
        // and folded in here it would only make this one harder to read.
        p.crash_amplifier_slope = 0.0;
        let v_f = p.market_factor_sigma * p.market_factor_sigma;
        let only_factor =
            index_conditional_variance(&p, &names, 3, v_f, 0.0, 1.0, 0.0, false, k);
        let beta_w: f64 = names.iter().map(|n| n.weight * n.beta).sum();
        // The idiosyncratic block is still there: subtract it explicitly
        // rather than trying to switch it off, because the tick's floor
        // means it cannot BE switched off.
        let idio: f64 = names
            .iter()
            .map(|n| {
                let s = idio_sigma_daily(&p, n);
                n.weight * n.weight * s * s
            })
            .sum();
        // The TICK-SIGN tilt is live on pt-v18 whatever the lag bit says, so
        // the factor block is not `beta_w^2 v_f` but that times the loading's
        // own second moment. Written out here as `(1 + (1 + d)^2) / 2` rather
        // than taken from `transmission_loadings`, so this stays a check of
        // the identity against algebra and not against itself.
        let d = p.market_beta_down_asym;
        let tilt_2 = (1.0 + (1.0 + d) * (1.0 + d)) / 2.0;
        let want = k * (beta_w * beta_w * v_f * tilt_2 + idio);
        assert!((only_factor - want).abs() < 1e-18, "{only_factor} vs {want}");
        // And the lag is a multiplier on top of that, on the session after a
        // down day: `(1 + lag)^2` on the whole block, 1.891 at pt-v18's
        // 0.375. The one term in this module that reads a bit of state.
        let lagged =
            index_conditional_variance(&p, &names, 3, v_f, 0.0, 1.0, 0.0, true, k);
        let l = 1.0 + p.market_beta_down_asym_lag;
        let want_lagged = k * (beta_w * beta_w * v_f * l * l * tilt_2 + idio);
        assert!((lagged - want_lagged).abs() < 1e-18, "{lagged} vs {want_lagged}");

        // News alone, on top: an exactly computable increment.
        let mut with_news = p.clone();
        with_news.endogenous_news_intensity = 0.05;
        with_news.endogenous_news_sigma = 0.02;
        let news = index_conditional_variance(&with_news, &names, 3, v_f, 0.0, 1.0, 0.0, false, k)
            - only_factor;
        let weight_sq: f64 = names.iter().map(|n| n.weight * n.weight).sum();
        assert!((news - weight_sq * 0.05 * 0.02 * 0.02).abs() < 1e-20, "news {news}");
    }

    /// The intraday curve reaches the noise block and NOT the jumps: a jump
    /// is written to `s` at the close, outside the tick loop the curve
    /// shapes. A `K` applied to everything would be 8 per cent too much
    /// variance on a quarter of the total and nothing would fail.
    #[test]
    fn the_intraday_curve_does_not_reach_the_jumps() {
        let names = roster();
        let k = intraday_variance_factor();
        let mut p = PT_V18;
        p.sector_factor_sigma = 0.0;
        p.endogenous_news_intensity = 0.0;
        let v_f = p.market_factor_sigma * p.market_factor_sigma;
        let at_k = index_conditional_variance(&p, &names, 3, v_f, 0.0, 1.0, 0.0, false, k);
        let at_one = index_conditional_variance(&p, &names, 3, v_f, 0.0, 1.0, 0.0, false, 1.0);
        // Only the noise block scaled, so the difference is (k-1) times the
        // noise block and the jump block is common to both.
        let noise_at_one = (at_k - at_one) / (k - 1.0);
        let jumps = at_one - noise_at_one;
        assert!(jumps > 0.0, "the jump block vanished: {jumps}");
        let (lm, li) = jump_intensities(&p, 1.0);
        let mu = p.jump_mean_market;
        let sg = p.jump_sigma_market;
        let weight_sq: f64 = names.iter().map(|n| n.weight * n.weight).sum();
        let want = lm * (mu * mu + sg * sg) - (lm * mu) * (lm * mu)
            + weight_sq * li * p.jump_sigma_idio * p.jump_sigma_idio;
        assert!((jumps - want).abs() < 1e-15, "{jumps} vs {want}");
    }

    /// The contraction has to have CONVERGED, not merely run: the last pass
    /// must move the iterate by less than a part in 10^12 of itself.
    #[test]
    fn the_resting_variance_iteration_has_stopped_moving() {
        let names = roster();
        let bases = vec![0.000225; names.len()];
        let k = intraday_variance_factor();
        let p = PT_V18;
        let v_f = p.market_factor_sigma * p.market_factor_sigma;
        let settled = resting_garch_variances(
            &p, &names, &bases, v_f, p.sector_factor_sigma, k);
        let restarted: Vec<NameVariance> = names
            .iter()
            .enumerate()
            .map(|(i, n)| NameVariance { garch_variance: settled[i], ..*n })
            .collect();
        let again = resting_garch_variances(
            &p, &restarted, &bases, v_f, p.sector_factor_sigma, k);
        for (i, (a, b)) in settled.iter().zip(again.iter()).enumerate() {
            assert!((a - b).abs() <= a.abs() * 1e-12, "name {i}: {a} then {b}");
        }
    }

    /// The resting level is NOT the seeded one, and the direction matters:
    /// the roster opens at its sector base variance and the process settles
    /// well below it, which is why the anchor cannot be derived off the
    /// opening state.
    #[test]
    fn the_process_rests_below_the_variance_the_roster_opens_at() {
        let names = roster();
        let bases = vec![0.000225; names.len()];
        let k = intraday_variance_factor();
        let p = PT_V18;
        let settled = resting_garch_variances(
            &p, &names, &bases, p.market_factor_sigma * p.market_factor_sigma,
            p.sector_factor_sigma, k);
        for (i, v) in settled.iter().enumerate() {
            assert!(*v < 0.000225, "name {i} rests at {v}, at or above its seed");
        }
    }

    /// A ROSTER WITH A NAME OUTSIDE THE SECTOR TABLE, which is the branch
    /// `sector < sector_count` guards. Kept beside `roster` so the spread
    /// below covers the accumulator's skip as well as its sum.
    fn roster_with_a_stray_sector() -> Vec<NameVariance> {
        let mut names = roster();
        names[3].sector = 99;
        names[5].market_cap = 4.0e11;
        names[6].garch_variance = 0.0009;
        names
    }

    /// THE EXPRESSION THAT STOOD IN `index_conditional_variance` BEFORE
    /// THE TERMS WERE KEPT, written out here so the equality has two
    /// independent sides.
    ///
    /// Asserting `index_conditional_variance` against
    /// `index_conditional_variance_terms(..).total()` would prove nothing
    /// at all -- the first is now DEFINED as the second. The claim worth
    /// asserting is that `total()` is the ORIGINAL sum in the ORIGINAL
    /// grouping, and that needs the original written down.
    ///
    /// Copied deliberately rather than factored out: a shared helper would
    /// move with the code it is supposed to be checking.
    fn the_sum_as_it_was_written(
        p: &ModelParams,
        names: &[NameVariance],
        sector_count: usize,
        factor_variance: f64,
        sector_sigma: f64,
        jump_rate_scale: f64,
        k: f64,
    ) -> f64 {
        let mut beta_w = 0.0;
        let mut weight_sq = 0.0;
        let mut idio_var = 0.0;
        let mut sector_loaded = vec![0.0; sector_count];
        for name in names {
            beta_w += name.weight * name.beta;
            weight_sq += name.weight * name.weight;
            let idio = idio_sigma_daily(p, name);
            idio_var += name.weight * name.weight * idio * idio;
            if name.sector < sector_count {
                sector_loaded[name.sector] +=
                    name.weight * crate::market::factors::sector_loading_for(p, name.beta);
            }
        }
        let mut sector_var = 0.0;
        for loaded in sector_loaded.iter() {
            sector_var += loaded * loaded * sector_sigma * sector_sigma;
        }

        let noise = k * (beta_w * beta_w * factor_variance + sector_var + idio_var);

        let (lambda_m, lambda_i) = jump_intensities(p, jump_rate_scale);
        let mu = p.jump_mean_market;
        let sig = p.jump_sigma_market;
        let market_jump = lambda_m * (mu * mu + sig * sig) - (lambda_m * mu) * (lambda_m * mu);
        let idio_jump = weight_sq * lambda_i * p.jump_sigma_idio * p.jump_sigma_idio;

        let news = weight_sq * p.endogenous_news_intensity
            * p.endogenous_news_sigma
            * p.endogenous_news_sigma;

        noise + market_jump + idio_jump + news
    }

    /// The parameter vectors the spread below runs, each reaching a branch
    /// the others do not: the shipped preset, `jump_intensities`' own
    /// zero-coupling branch, the news term switched on, and the jumps
    /// switched off.
    fn parameter_spread() -> Vec<(&'static str, ModelParams)> {
        let mut uncoupled = PT_V18;
        uncoupled.jump_vix_coupling = 0.0;
        let mut noisy = PT_V18;
        noisy.endogenous_news_intensity = 0.35;
        noisy.endogenous_news_sigma = 0.031;
        noisy.jump_intensity_idio = 0.0;
        let mut no_jumps = PT_V18;
        no_jumps.jump_intensity_market = 0.0;
        no_jumps.jump_intensity_idio = 0.0;
        vec![
            ("pt-v18", PT_V18),
            ("uncoupled jumps", uncoupled),
            ("news on", noisy),
            ("no jumps", no_jumps),
        ]
    }

    /// KEEPING THE TERMS DOES NOT MOVE THE SUM, ON BITS, IN THE CALM
    /// REGIME THE OLD IDENTITY WAS THE WHOLE OF.
    ///
    /// Floating-point addition does not associate, so a regrouping of
    /// `k * (a + b + c) + d + e + f` is a different number in the last
    /// place -- and a last place is not nothing here. Under
    /// `vix_level_identity` this variance IS the VIX, the VIX is the
    /// factor's target and the jump arrival rate, and a run that differs
    /// by an ULP is a different market inside a year. So the assertion is
    /// `assert_eq!` over a spread of every argument, against the
    /// expression that stood in the function rather than against a
    /// number, in the form `economy/daily.rs` asserts `return_spike_for`
    /// and `expected_return_spike` in.
    ///
    /// **What B4 and the tilt changed and what they did not.** `total()` now
    /// sums three more terms inside the `k` group. Where all three are
    /// exactly zero -- the amplifier switched off, no crisis, and neither
    /// transmission wire live -- `a + b + c + 0.0 + 0.0 + 0.0` is
    /// `a + b + c` to the bit, because IEEE-754 addition of a positive zero
    /// is exact for every finite operand. So the old expression is still
    /// the answer in the regime it was the whole of, and this test says so
    /// with the amplifier slope and both tilt dials zeroed and the spike
    /// passed as 0.0. The new terms have their own tests; what this one
    /// protects is that nothing was reassociated on the way past.
    ///
    /// **Both faces of the lag bit are swept**, because a preset with the
    /// wire off must be bit-identical on a lagged session too -- that is
    /// what makes the state this module now reads inert where the dial is.
    #[test]
    fn the_calm_regime_reproduces_the_sum_it_replaced() {
        let k_curve = intraday_variance_factor();
        for (label, mut p) in parameter_spread() {
            p.crash_amplifier_slope = 0.0;
            p.market_beta_down_asym = 0.0;
            p.market_beta_down_asym_lag = 0.0;
            for names in [roster(), roster_with_a_stray_sector()] {
                for &sector_count in &[3usize, 5] {
                    for i in 0..=12 {
                        let factor_variance = 1.0e-6 + i as f64 * 8.3e-5;
                        for j in 0..=6 {
                            let sector_sigma = j as f64 * 0.0061;
                            for &scale in &[0.0, 0.25, 1.0, 1.7, 4.9, 27.5] {
                                for &k in &[1.0, k_curve, 2.25] {
                                  for &lagged in &[false, true] {
                                    let want = the_sum_as_it_was_written(
                                        &p, &names, sector_count, factor_variance,
                                        sector_sigma, scale, k);
                                    let terms = index_conditional_variance_terms(
                                        &p, &names, sector_count, factor_variance,
                                        sector_sigma, scale, 0.0, lagged, k);
                                    assert_eq!(terms.crash_raw, 0.0, "{label}: amplifier off");
                                    assert_eq!(terms.crisis_raw, 0.0, "{label}: no crisis");
                                    assert_eq!(terms.tilt_raw, 0.0, "{label}: no tilt");
                                    assert_eq!(
                                        terms.total(), want,
                                        "{label}: sectors {sector_count}, v_f \
                                         {factor_variance}, sigma_s {sector_sigma}, \
                                         scale {scale}, k {k}, lagged {lagged}");
                                    assert_eq!(
                                        index_conditional_variance(
                                            &p, &names, sector_count, factor_variance,
                                            sector_sigma, scale, 0.0, lagged, k),
                                        want,
                                        "{label}: the summing form moved");
                                  }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    /// AND EACH TERM IS THE BLOCK ITS NAME CLAIMS.
    ///
    /// The test above cannot see a mislabelling: a struct that put the
    /// sector block in `idio_raw` and the idiosyncratic block in
    /// `sector_raw` sums to the same variance and passes it. Since the
    /// point of the split is to say WHICH term of `V_t` a VIX move went
    /// into, the labels ARE the instrument and they are asserted
    /// separately -- each against its own closed form, and then by moving
    /// one dial at a time and requiring that exactly one field responds.
    #[test]
    fn each_term_carries_the_block_its_name_says() {
        let k = intraday_variance_factor();
        let p = PT_V18;
        let names = roster_with_a_stray_sector();
        // A crisis ON, so `crisis_raw` is exercised here rather than in a
        // separate test that could drift away from the labelling checks.
        const SPIKE: f64 = 0.61;
        let (v_f, sigma_s, scale) = (0.00021, 0.0093, 1.6);
        let terms = index_conditional_variance_terms(
            &p, &names, 3, v_f, sigma_s, scale, SPIKE, false, k);

        let beta_w: f64 = names.iter().map(|n| n.weight * n.beta).sum();
        assert_eq!(terms.factor_raw, beta_w * beta_w * v_f, "factor_raw");

        let mut loaded = vec![0.0; 3];
        for n in names.iter() {
            if n.sector < 3 {
                loaded[n.sector] +=
                    n.weight * crate::market::factors::sector_loading_for(&p, n.beta);
            }
        }
        let mut sector_var = 0.0;
        for l in loaded.iter() {
            sector_var += l * l * sigma_s * sigma_s;
        }
        assert_eq!(terms.sector_raw, sector_var, "sector_raw");

        let mut idio_var = 0.0;
        for n in names.iter() {
            let s = idio_sigma_daily(&p, n);
            idio_var += n.weight * n.weight * s * s;
        }
        assert_eq!(terms.idio_raw, idio_var, "idio_raw");

        // The two regime blocks, each against its own closed form written
        // out here rather than against the function that produced it.
        let s = mathx::sqrt(v_f) / p.market_factor_sigma;
        let (amp_1, amp_2) =
            amplifier_moments(p.crash_amplifier_slope, p.crash_amplifier_threshold, s);
        assert!(amp_2 > amp_1 && amp_1 > 1.0, "the moments ordered wrong: {amp_1}, {amp_2}");
        assert_eq!(terms.crash_raw, beta_w * beta_w * v_f * (amp_2 - 1.0), "crash_raw");

        let l_w: f64 = names
            .iter()
            .filter(|n| n.sector < 3)
            .map(|n| n.weight * crate::market::factors::sector_loading_for(&p, n.beta))
            .sum();
        let q = p.crisis_blend_source;
        let b = beta_w + q * p.crisis_blend_gain * SPIKE;
        let c = l_w * SPIKE * (1.0 - q);
        let h = 1.0 - SPIKE * (1.0 - q);
        assert_eq!(
            terms.crisis_raw,
            v_f * ((b * b - beta_w * beta_w) * amp_2 + 2.0 * b * c * amp_1 + c * c)
                + (h * h - 1.0) * sector_var,
            "crisis_raw");

        assert_eq!(terms.k, k, "k is the argument, not a recomputation");

        let (lm, li) = jump_intensities(&p, scale);
        let (mu, sg) = (p.jump_mean_market, p.jump_sigma_market);
        assert_eq!(terms.market_jump,
                   lm * (mu * mu + sg * sg) - (lm * mu) * (lm * mu), "market_jump");
        let weight_sq: f64 = names.iter().map(|n| n.weight * n.weight).sum();
        assert_eq!(terms.idio_jump,
                   weight_sq * li * p.jump_sigma_idio * p.jump_sigma_idio, "idio_jump");
        assert_eq!(terms.news,
                   weight_sq * p.endogenous_news_intensity
                       * p.endogenous_news_sigma * p.endogenous_news_sigma, "news");

        // One dial at a time, and exactly one field may respond. This is
        // what catches a swap between two blocks whose closed forms were
        // copied from the same place.
        let no_sector = index_conditional_variance_terms(
            &p, &names, 3, v_f, 0.0, scale, SPIKE, false, k);
        assert_eq!(no_sector.sector_raw, 0.0,
                   "the sector block did not follow its sigma");
        assert_eq!(no_sector.idio_raw, terms.idio_raw,
                   "the sector sigma reached idio_raw");
        assert_eq!(no_sector.factor_raw, terms.factor_raw,
                   "the sector sigma reached factor_raw");

        let mut quiet = p.clone();
        quiet.endogenous_news_intensity = 0.0;
        let no_news = index_conditional_variance_terms(
            &quiet, &names, 3, v_f, sigma_s, scale, SPIKE, false, k);
        assert_eq!(no_news.news, 0.0,
                   "the news block did not follow its intensity");
        assert_eq!(no_news.idio_jump, terms.idio_jump,
                   "the news intensity reached idio_jump");

        let no_factor = index_conditional_variance_terms(
            &p, &names, 3, 0.0, sigma_s, scale, SPIKE, false, k);
        assert_eq!(no_factor.factor_raw, 0.0,
                   "the factor block did not follow its variance");
        assert_eq!(no_factor.sector_raw, terms.sector_raw,
                   "the factor variance reached sector_raw");

        // The two regime dials, one at a time, on the same rule: switching
        // one off must zero its own field and leave the calm blocks where
        // they were. `crisis_raw` at source 1.0 is a pure loading shift, so
        // the sector block does not move with the spike either.
        let mut unamplified = p.clone();
        unamplified.crash_amplifier_slope = 0.0;
        let flat = index_conditional_variance_terms(
            &unamplified, &names, 3, v_f, sigma_s, scale, SPIKE, false, k);
        assert_eq!(flat.crash_raw, 0.0, "the amplifier block did not follow its slope");
        assert_eq!(flat.factor_raw, terms.factor_raw, "the slope reached factor_raw");
        assert_eq!(flat.sector_raw, terms.sector_raw, "the slope reached sector_raw");
        assert!(flat.crisis_raw > 0.0 && flat.crisis_raw < terms.crisis_raw,
                "the blend must survive the amplifier being off, and be smaller \
                 without it: {} against {}", flat.crisis_raw, terms.crisis_raw);

        let calm = index_conditional_variance_terms(
            &p, &names, 3, v_f, sigma_s, scale, 0.0, false, k);
        assert_eq!(calm.crisis_raw, 0.0, "the blend block did not follow its spike");
        assert_eq!(calm.crash_raw, terms.crash_raw, "the spike reached crash_raw");
        assert_eq!(calm.sector_raw, terms.sector_raw, "the spike reached sector_raw");
        assert_eq!(calm.factor_raw, terms.factor_raw, "the spike reached factor_raw");
        assert!(terms.total() > calm.total(),
                "a crisis must cost more variance than no crisis");
    }

    /// AT SOURCE 0.0 THE BLEND RIDES THE SECTOR SLOT, and that is a
    /// different piece of arithmetic with a different sign in it.
    ///
    /// pt-v1 to pt-v6 ship `crisis_blend_source` 0.0: `tick.rs` attenuates
    /// the sector draw by `(1 - spike)` and injects the market factor
    /// through it instead. So the blend TAKES sector variance away while it
    /// adds market variance, and a term that only ever added would be wrong
    /// for six presets in a direction no test would catch from the total
    /// alone. Asserted here against the `h² - 1` the identity carries.
    #[test]
    fn the_blend_at_source_zero_takes_from_the_sector_and_gives_to_the_market() {
        let k = intraday_variance_factor();
        let mut p = PT_V18;
        p.crisis_blend_source = 0.0;
        p.crash_amplifier_slope = 0.0;
        let names = roster();
        let (v_f, sigma_s, spike) = (0.00021, 0.0093, 0.55);
        let terms =
            index_conditional_variance_terms(&p, &names, 3, v_f, sigma_s, 1.0, spike, false, k);

        let beta_w: f64 = names.iter().map(|n| n.weight * n.beta).sum();
        let l_w: f64 = names
            .iter()
            .map(|n| n.weight * crate::market::factors::sector_loading_for(&p, n.beta))
            .sum();
        // `B` is unmoved at source 0.0 -- nothing is injected into the
        // market component -- so the whole term is the sector slot's.
        let c = l_w * spike;
        let h = 1.0 - spike;
        let want = v_f * (2.0 * beta_w * c + c * c) + (h * h - 1.0) * terms.sector_raw;
        assert_eq!(terms.crisis_raw, want, "the source-zero branch");
        assert!(terms.crash_raw == 0.0, "the amplifier was switched off");

        // And the sector block genuinely loses: the attenuation is the
        // negative half of the term, and at a spike of 0.55 it is most of
        // the sector block.
        let attenuation = (h * h - 1.0) * terms.sector_raw;
        assert!(attenuation < 0.0, "the attenuation must be negative: {attenuation}");
        assert!((attenuation / terms.sector_raw + 0.7975).abs() < 1e-9,
                "1 - (1 - 0.55)^2 is 0.7975 of the sector block");
    }

    // ── The regime terms, against the process they are moments of ─────────

    /// A GRID QUADRATURE over the standard normal, used as the independent
    /// side of both regime tests below.
    ///
    /// Deterministic rather than sampled, on purpose: a Monte Carlo check of
    /// a FOURTH moment needs millions of draws to see three decimals, and
    /// the answer would still be a number with a seed in it. Simpson's rule
    /// on 240,001 points over ±12 sigma integrates `phi` against a smooth
    /// integrand to about a part in 10^12, which is tight enough for the
    /// closed form to be WRONG if it is wrong — and it converges, so a
    /// tolerance here is a statement about quadrature and not about luck.
    ///
    /// The amplifier's kink at `|z| = c` is the one place the integrand is
    /// not smooth, and Simpson degrades to about the trapezoid's order
    /// across a single panel there. At this step that panel is 1e-4 wide and
    /// contributes about 1e-13 of the integral, so the kink is inside the
    /// tolerance rather than the reason for it.
    fn integrate_against_the_normal(f: impl Fn(f64) -> f64) -> f64 {
        const HALF_WIDTH: f64 = 12.0;
        const PANELS: usize = 240_000;
        let h = 2.0 * HALF_WIDTH / PANELS as f64;
        let mut total = 0.0;
        for i in 0..=PANELS {
            let z = -HALF_WIDTH + i as f64 * h;
            let w = if i == 0 || i == PANELS {
                1.0
            } else if i % 2 == 1 {
                4.0
            } else {
                2.0
            };
            total += w * f(z) * standard_normal_pdf(z);
        }
        total * h / 3.0
    }

    /// **THE DERIVATION, CHECKED AGAINST THE THING IT IS A DERIVATION OF.**
    ///
    /// `amplifier_moments` claims two closed forms in `phi` and `Phi` for
    /// `E[z² A]` and `E[z² A²]`. Here `A` is written out as the tick writes
    /// it — the branch at the threshold included — and both moments are
    /// integrated directly. A sign error in `M_4 - 2c M_3 + c² M_2`, or a
    /// truncated moment taken for the whole-line one, fails this and passes
    /// every other test in the file.
    ///
    /// Swept across the regime ratios the engine actually visits: 0.3 is a
    /// floored factor regime where the threshold sits 6.7 sigmas out and the
    /// amplifier is silent; 0.965 is the pinned anchor; 2.04 is the deepest
    /// pin the loop-gain run measured, where the threshold is inside one
    /// sigma.
    #[test]
    fn the_amplifier_moments_are_the_moments_they_claim() {
        for &m in &[0.05, 0.2, 0.5] {
            for &t in &[1.5, 2.0, 3.0] {
                for &s in &[0.3, 0.6, 0.965, 1.32, 1.5, 2.04, 3.0] {
                    let amp = |z: f64| {
                        let mag = z.abs() * s;
                        if mag > t { 1.0 + (mag - t) * m } else { 1.0 }
                    };
                    let want_1 = integrate_against_the_normal(|z| z * z * amp(z));
                    let want_2 = integrate_against_the_normal(|z| z * z * amp(z) * amp(z));
                    let (got_1, got_2) = amplifier_moments(m, t, s);
                    assert!((got_1 - want_1).abs() < 1e-9 * want_1,
                            "E[z^2 A] at m {m}, T {t}, s {s}: {got_1} against {want_1}");
                    assert!((got_2 - want_2).abs() < 1e-9 * want_2,
                            "E[z^2 A^2] at m {m}, T {t}, s {s}: {got_2} against {want_2}");
                }
            }
        }
        // The two degenerate branches return exactly one, so a preset with
        // the amplifier off pays nothing and lands on the calm sum's bits.
        assert_eq!(amplifier_moments(0.0, 2.0, 1.0), (1.0, 1.0));
        assert_eq!(amplifier_moments(0.2, 2.0, 0.0), (1.0, 1.0));
    }

    /// AND THE REGIME DEPENDENCE IS THE POINT, so it is asserted as a
    /// SHAPE rather than as a set of values.
    ///
    /// The amplifier's normaliser is the BASE sigma, a constant
    /// (`factors.rs`: "extreme stays denominated in absolute units"), so its
    /// contribution must rise monotonically with the regime ratio and go to
    /// nothing in a floored regime. A version that normalised by the
    /// conditional sigma instead — the alternative `factors.rs` weighs and
    /// rejects — would be FLAT in `s`, and flat is exactly the defect B4
    /// exists to remove.
    #[test]
    fn the_amplifier_costs_more_the_deeper_the_regime() {
        let p = PT_V18;
        // NON-DECREASING everywhere, and STRICTLY rising once the tail is
        // representable at all. Below about s 0.35 the exceedance is past
        // 5.7 sigmas and `phi(c)` underflows to zero, so two neighbouring
        // ratios both return exactly 1.0 — which is the term being silent,
        // not the shape failing, and demanding a strict rise there would be
        // demanding that a double resolve 1e-320.
        let mut last = 0.0;
        for i in 1..=40 {
            let s = i as f64 * 0.075;
            let (_, amp_2) =
                amplifier_moments(p.crash_amplifier_slope, p.crash_amplifier_threshold, s);
            assert!(amp_2 >= last, "the moment fell from {last} at s {s}");
            assert!(amp_2 >= 1.0, "the amplifier cannot reduce variance: {amp_2}");
            if s >= 0.4 {
                assert!(amp_2 > last, "the moment stopped rising at s {s}: {amp_2}");
            }
            last = amp_2;
        }
        // In a floored regime it is genuinely silent: at s 0.3 the threshold
        // is 6.7 sigmas out and the term is under a part in 10^6.
        let (_, quiet) =
            amplifier_moments(p.crash_amplifier_slope, p.crash_amplifier_threshold, 0.3);
        assert!(quiet - 1.0 < 1e-6, "silent means silent: {quiet}");
        // At the anchor it is not: the ratio is 1.0 there by construction.
        let (_, at_anchor) =
            amplifier_moments(p.crash_amplifier_slope, p.crash_amplifier_threshold, 1.0);
        assert!(at_anchor > 1.03 && at_anchor < 1.08,
                "the amplifier at the anchor reads {at_anchor}");
    }

    /// A generator that draws nothing, so the per-name idiosyncratic term
    /// drops out of the end-to-end check below and what is left is the
    /// market's own transmission.
    struct NoNoise;
    impl crate::rng::Rng for NoNoise {
        fn next_f64(&mut self) -> f64 {
            0.5
        }
        fn next_normal(&mut self) -> f64 {
            0.0
        }
    }

    /// **THE IDENTITY AGAINST THE ENGINE, not against itself.**
    ///
    /// Every test above compares one piece of algebra with another. This one
    /// drives `factors::calculate_live_factors` — the production path, the
    /// amplifier's own branch, the blend's own injection — over the same
    /// quadrature, builds the cap-weighted index's per-tick variance from
    /// what the tick actually returns, and requires the identity's factor,
    /// amplifier and blend terms to add up to it.
    ///
    /// That is the claim worth making: not that the moments are moments, but
    /// that they are the moments OF THE PROCESS THE ENGINE RUNS. The
    /// idiosyncratic and sector draws are switched off — a zero generator
    /// and zero sector factors — so the residual on the other side is the
    /// market-driven block alone and a discrepancy has nowhere to hide.
    ///
    /// Run at `crisis_blend_source` 1.0 and 0.0, because those are two
    /// different wirings of the same blend and the second one also removes
    /// sector variance; and at both faces of the lag bit with the tilt on
    /// and off, because the ORDER the tick applies the tilt, the injection
    /// and the amplifier in is the whole content of
    /// [`transmission_loadings`] and is the one thing algebra cannot check
    /// about itself. A version that let the lag multiply the crisis
    /// injection — which reads more natural than what `factors.rs` does —
    /// passes every other test in this file and fails this one by 30 per
    /// cent at saturation.
    #[test]
    fn the_regime_terms_reproduce_the_index_the_tick_builds() {
        let k = intraday_variance_factor();
        let names = roster();
        // An explicit case list and not a cross product: a preset with the
        // lag at 0.0 reads the same on both faces of the bit, so half of a
        // cross product would be the same quadrature run twice, and this
        // integral is the most expensive thing in the file.
        for &source in &[1.0, 0.0] {
          for &(tilt, lag, lagged) in &[
              (0.0, 0.0, false),      // neither wire: the calm identity
              (0.025, 0.0, false),    // the tick-sign tilt alone
              (0.025, 0.375, false),  // pt-v19's pair, unlagged session
              (0.025, 0.375, true),   // pt-v19's pair, lagged session
              (0.2, 1.1, true),       // far outside any preset, both large
          ] {
            for &spike in &[0.0, 0.42, 0.98] {
                for &v_f in &[3.0e-5, 5.766e-5, 2.4e-4] {
                    let mut p = PT_V18;
                    p.crisis_blend_source = source;
                    p.market_beta_down_asym = tilt;
                    p.market_beta_down_asym_lag = lag;

                    let sigma_tick = mathx::sqrt(v_f) / mathx::sqrt(390.0);
                    // The index's per-tick market-driven return at a given
                    // standard normal `z`, straight out of the tick.
                    let index_at = |z: f64| {
                        let mut total = 0.0;
                        for (i, n) in names.iter().enumerate() {
                            let company = crate::market::factors::FactorCompany {
                                id: format!("N{i}"),
                                sector: format!("S{}", n.sector),
                                beta: Some(n.beta),
                                market_cap: n.market_cap,
                                avg_volume: 1.0e7,
                                shares_outstanding: 1.0e9,
                                short_interest: 0.0,
                                float: 1.0e9,
                                garch_variance: n.garch_variance,
                                last_daily_return: Some(0.0),
                            };
                            let shared = crate::market::factors::SharedFactors {
                                market_factor: z * sigma_tick,
                                // Every sector present and drawn at zero, so
                                // the sector slot carries only what the blend
                                // injects into it.
                                sector_factors: (0..3)
                                    .map(|s| (format!("S{s}"), 0.0))
                                    .collect(),
                                crisis_spike: spike,
                                prev_day_down: lagged,
                                market_sigma_tick: sigma_tick,
                            };
                            let out = crate::market::factors::calculate_live_factors(
                                &company, &[], 0.0, 1.0, &shared, &p, &mut NoNoise);
                            total += n.weight * out.random_noise;
                        }
                        total
                    };
                    // At source 0.0 the blend injects the market factor
                    // through the sector slot, and the slot is supplied
                    // above rather than drawn -- so the injection has to be
                    // applied here, exactly as `tick.rs` applies it, or the
                    // reference side would be missing the term under test.
                    let index_at = |z: f64| {
                        let base = index_at(z);
                        if source == 1.0 || spike == 0.0 {
                            base
                        } else {
                            // `kept` at a zero sector draw is
                            // `market_factor * spike * (1 - source)`, which
                            // reaches each name through its own loading.
                            let leak: f64 = names
                                .iter()
                                .map(|n| {
                                    n.weight
                                        * crate::market::factors::sector_loading_for(&p, n.beta)
                                })
                                .sum::<f64>()
                                * z * sigma_tick * spike * (1.0 - source);
                            base + leak
                        }
                    };

                    // THE RECENTRING OFFSET, TAKEN FROM THE ENGINE AND NOT
                    // FROM ALGEBRA. `tilt_recentre` is a deterministic
                    // constant added to every tick's noise, and it is a mean
                    // correction rather than a variance term — this module
                    // does not carry it and says so. At `z = 0` the market
                    // factor is zero, so the amplifier is one and the
                    // market, sector and idiosyncratic components are all
                    // exactly zero: what the tick returns there IS the
                    // offset. Reading it off the tick rather than
                    // reconstructing `recentre * d * beta_w * sigma / sqrt(2 pi)`
                    // keeps this test independent of the expression it is
                    // checking, which is the whole point of the file.
                    let offset = index_at(0.0);
                    if tilt == 0.0 {
                        assert_eq!(offset, 0.0, "no tilt, no recentring");
                    }
                    // The measured per-tick SECOND MOMENT of the index's
                    // market-driven return, which is what this module sums.
                    let per_tick = integrate_against_the_normal(|z| {
                        let centred = index_at(z) - offset;
                        centred * centred
                    });
                    let measured_daily = 390.0 * per_tick;

                    let terms = index_conditional_variance_terms(
                        &p, &names, 3, v_f, 0.0, 1.0, spike, lagged, k);
                    // `idio_raw` is what the generator would have drawn and
                    // did not, and `sector_raw` is zero at a zero sector
                    // sigma. What is left is the block under test.
                    let want = terms.factor_raw + terms.crash_raw + terms.crisis_raw
                        + terms.tilt_raw;
                    assert!(
                        (want - measured_daily).abs() < 1e-9 * measured_daily,
                        "source {source}, tilt {tilt}, lag {lag}, lagged {lagged}, \
                         spike {spike}, v_f {v_f}: identity {want} \
                         against the tick's own {measured_daily}");

                    // THE MEAN, which is the residual and not the term. With
                    // the tilt off it is zero by symmetry; with it on, the
                    // tilt moves the draw's mean and
                    // `market_beta_down_asym_recentre` gives back exactly the
                    // UNAMPLIFIED, UNLAGGED part of it, so what is left is a
                    // drift this variance does not subtract. Asserted at both
                    // ends: exactly zero where it must be, and bounded by the
                    // size the module's residual list claims where it is not.
                    let mean = integrate_against_the_normal(index_at);
                    let drift_share = (390.0 * mean) * (390.0 * mean) / measured_daily;
                    if tilt == 0.0 {
                        assert!(mean.abs() < 1e-14,
                                "an untilted transmission has a mean: {mean}");
                    }
                    if tilt == 0.025 && lag == 0.375 {
                        assert!(drift_share < 0.005,
                                "the recentring residual is {:.4} of the market block \
                                 at lagged {lagged}, spike {spike}, v_f {v_f}",
                                drift_share);
                    }
                }
            }
          }
        }
    }

    /// **THE ANCHOR IS A MEAN OVER A COIN, NOT A READING AT ONE FACE.**
    ///
    /// `market_beta_down_asym_lag` is state and not a coupling: it does not
    /// read one at the anchor the way `sector_vix_coupling` and the jump
    /// rate scale do, so [`index_unconditional_variance`] cannot evaluate it
    /// "at the point every coupling reads one". `prev_day_factor` is a
    /// zero-mean accumulated sum, so the bit is a fair coin and the
    /// unconditional variance is the average of the two faces — which is
    /// what that function takes, and what this asserts.
    ///
    /// The gap between the two faces is the reason it matters. Evaluating at
    /// the unlagged face alone — the obvious reading, and the one that would
    /// have left `pt-v19`'s anchor where it was — is measured here, and it
    /// is not a rounding difference.
    ///
    /// A preset with the wire OFF must be untouched, and is: the function
    /// branches before the second evaluation, so `pt-v16` and everything
    /// before it land on the bits they always did.
    #[test]
    fn the_anchor_averages_over_the_coin_it_cannot_read() {
        let names = roster();
        let bases: Vec<f64> = names.iter().map(|n| n.garch_variance).collect();
        let p = crate::params::PT_V19;
        let k = intraday_variance_factor();
        let v_f = p.market_factor_sigma * p.market_factor_sigma;
        let resting = resting_garch_variances(
            &p, &names, &bases, v_f, p.sector_factor_sigma, k);
        let at_rest: Vec<NameVariance> = names
            .iter()
            .enumerate()
            .map(|(i, n)| NameVariance { garch_variance: resting[i], ..*n })
            .collect();
        let unlagged = index_conditional_variance(
            &p, &at_rest, 3, v_f, p.sector_factor_sigma, 1.0, 0.0, false, k);
        let lagged = index_conditional_variance(
            &p, &at_rest, 3, v_f, p.sector_factor_sigma, 1.0, 0.0, true, k);
        let got = index_unconditional_variance(&p, &names, 3, &bases);
        assert_eq!(got, 0.5 * (unlagged + lagged), "the average is the average");
        // The two faces, as VIX levels, which is the unit the anchor is in.
        let vix = |v: f64| vix_from_variance(p.vix_variance_premium, v);
        let shortfall = vix(got) / vix(unlagged) - 1.0;
        println!(
            "anchor: unlagged {:.4}, lagged {:.4}, mean {:.4}; the unlagged face is \
             {:.2}% low", vix(unlagged), vix(lagged), vix(got), 100.0 * shortfall);
        assert!(lagged > unlagged, "the lagged face must be the larger");
        assert!(shortfall > 0.05,
                "if the two faces agreed this would not be worth branching on: \
                 {:.4}", shortfall);
        // A preset without the wire evaluates once and lands on that.
        let mut no_wire = p.clone();
        no_wire.market_beta_down_asym_lag = 0.0;
        let single = index_conditional_variance(
            &no_wire, &at_rest, 3, v_f, p.sector_factor_sigma, 1.0, 0.0, false, k);
        assert_eq!(
            index_unconditional_variance(&no_wire, &names, 3, &bases), single,
            "a preset without the lagged wire must evaluate once, on the bits");
    }

    /// **THE ONE RESIDUAL WHOSE SIGN IS KNOWN, MEASURED RATHER THAN
    /// ASSERTED TO BE SMALL.**
    ///
    /// The tilt moves the transmission's MEAN as well as its variance, and
    /// `market_beta_down_asym_recentre` gives back exactly the unamplified,
    /// unlagged part of that. What is left on a lagged session is a genuine
    /// daily drift, and this module's sum does not subtract it — so `V_t` is
    /// the transmission's second moment where a variance would be that less
    /// the squared mean, and is therefore HIGH by the squared mean.
    ///
    /// Every other item on the residual list is a term left out, which makes
    /// `V_t` low. This one makes it high, so the list would be misleading
    /// without its size, and a size nobody measured is what that section
    /// exists to refuse. Measured here over the pin ladder's own regime
    /// range, on the tick itself, at pt-v19's dials.
    #[test]
    fn the_recentring_residual_is_the_size_it_is_claimed_to_be() {
        let k = intraday_variance_factor();
        let names = roster();
        let p = crate::params::PT_V19;
        let base_v = p.market_factor_sigma * p.market_factor_sigma;
        let mut worst_block = 0.0f64;
        let mut worst_total = 0.0f64;
        let mut at_anchor = (0.0f64, 0.0f64);
        // The regime ratios the ladder visits: the anchor, and the factor
        // variance at pins up to x 3.
        for &fvar_ratio in &[1.0, 1.742, 2.246, 4.162, 9.0] {
            let v_f = base_v * fvar_ratio;
            let sigma_tick = mathx::sqrt(v_f) / mathx::sqrt(390.0);
            let spike = 0.0;
            let index_at = |z: f64| {
                let mut total = 0.0;
                for (i, n) in names.iter().enumerate() {
                    let company = crate::market::factors::FactorCompany {
                        id: format!("N{i}"),
                        sector: format!("S{}", n.sector),
                        beta: Some(n.beta),
                        market_cap: n.market_cap,
                        avg_volume: 1.0e7,
                        shares_outstanding: 1.0e9,
                        short_interest: 0.0,
                        float: 1.0e9,
                        garch_variance: n.garch_variance,
                        last_daily_return: Some(0.0),
                    };
                    let shared = crate::market::factors::SharedFactors {
                        market_factor: z * sigma_tick,
                        sector_factors: (0..3).map(|s| (format!("S{s}"), 0.0)).collect(),
                        crisis_spike: spike,
                        prev_day_down: true,
                        market_sigma_tick: sigma_tick,
                    };
                    let out = crate::market::factors::calculate_live_factors(
                        &company, &[], 0.0, 1.0, &shared, &p, &mut NoNoise);
                    total += n.weight * out.random_noise;
                }
                total
            };
            let offset = index_at(0.0);
            let block = 390.0
                * integrate_against_the_normal(
                    |z| (index_at(z) - offset) * (index_at(z) - offset));
            let mean_day = 390.0 * integrate_against_the_normal(index_at);
            let terms = index_conditional_variance_terms(
                &p, &names, 3, v_f, p.sector_factor_sigma, 1.0, spike, true, k);
            let of_block = mean_day * mean_day / block;
            let of_total = mean_day * mean_day / terms.total();
            if fvar_ratio == 1.0 {
                at_anchor = (of_block, of_total);
            }
            worst_block = mathx::max(worst_block, of_block);
            worst_total = mathx::max(worst_total, of_total);
            println!(
                "fvar x{fvar_ratio}: drift {:.6e}/day, {:.4}% of the market block, \
                 {:.4}% of V_t", mean_day, 100.0 * of_block, 100.0 * of_total);
        }
        // The drift is negative — the tilt takes more off the down side than
        // the recentring gives back once the lag has scaled it — so it is a
        // real downward pressure on the index and not a rounding artefact.
        assert!(at_anchor.0 > 0.0, "the residual vanished at the anchor");
        // The claims in the module docs, as bounds rather than as equalities:
        // a quadrature figure quoted to four places would be a claim about
        // the quadrature.
        assert!(at_anchor.0 < 0.004, "at the anchor it is {:.5} of the block", at_anchor.0);
        assert!(at_anchor.1 < 0.003, "at the anchor it is {:.5} of V_t", at_anchor.1);
        assert!(worst_block < 0.008,
                "the worst on the ladder is {:.5} of the block", worst_block);
        assert!(worst_total < 0.008,
                "the worst on the ladder is {:.5} of V_t", worst_total);
    }

    /// **AND THE STEP IS THE SIZE THE LOOP-GAIN RUN MEASURED.**
    ///
    /// P3 of `loopgain-report.md` measured realised variance over `V_t` at
    /// nine pins: 1.22 to 1.44 for pins at or below x 1.5, then 3.99, 4.30
    /// and 4.92 at x 1.75, 2.0 and 2.5. The ratio is flat below the crisis
    /// threshold and steps by about 2.8x across it, and a read-back that
    /// priced its own regime would carry that step rather than leave it in
    /// the residual.
    ///
    /// So this asserts the STEP and not the level: the identity's own ratio
    /// `V_t(regime) / V_t(calm)` across the threshold, at the factor
    /// variances and VIX levels those pins ran at. The level cannot be
    /// asserted here — the measured ratio also carries the between-day
    /// variance of `V_t` itself, the jumps and the tilt — but a step of the
    /// right size in the right place is what B4 asks for and what nine
    /// presets did not have.
    #[test]
    fn the_read_back_carries_the_step_the_tape_measured() {
        let k = intraday_variance_factor();
        let names = roster();
        let p = crate::params::PT_V19;
        let base_v = p.market_factor_sigma * p.market_factor_sigma;
        // `fvar/base` and the pin VIX, from the loop-gain table.
        let pins = [
            (1.00, 0.931, 19.53),
            (1.50, 1.742, 29.29),
            (1.75, 2.246, 34.17),
            (2.50, 4.162, 48.82),
        ];
        let mut read = Vec::new();
        for &(_x, fvar_ratio, vix) in pins.iter() {
            let spike = crate::market::tick::crisis_spike_for(&p, vix, 0.0);
            let terms = index_conditional_variance_terms(
                &p, &names, 3, base_v * fvar_ratio, p.sector_factor_sigma, 1.0, spike, false, k);
            // Against the SAME state with the regime terms silenced, which
            // is what the old read-back returned there.
            let mut calm = p.clone();
            calm.crash_amplifier_slope = 0.0;
            let was = index_conditional_variance_terms(
                &calm, &names, 3, base_v * fvar_ratio, p.sector_factor_sigma, 1.0, 0.0, false, k);
            read.push((vix, spike, terms.total() / was.total()));
        }
        // Below the threshold the blend is off and only the amplifier acts,
        // so the ratio is modest and rising.
        assert_eq!(read[0].1, 0.0, "the anchor is not a crisis");
        assert_eq!(read[1].1, 0.0, "x 1.5 is under the threshold at VIX 29.29");
        assert!(read[0].2 > 1.0 && read[0].2 < 1.10,
                "the calm range must not be inflated: {:.3}", read[0].2);
        assert!(read[1].2 > read[0].2, "the amplifier must rise with the regime");
        assert!(read[1].2 < 1.25, "still the calm range: {:.3}", read[1].2);
        // Across the threshold the blend saturates almost at once -- 34.17
        // is 3.3 points above 30.88 on a ramp of 1.4 against a cap of 0.98 --
        // so the step is a step and not a slope.
        assert!(read[2].1 >= p.crisis_blend_cap, "the spike saturates by x 1.75");
        let step = read[2].2 / read[1].2;
        assert!(step > 2.0 && step < 4.0,
                "the step across the threshold reads {step:.2}x, where the tape's \
                 realised variance steps 2.78x (3.992 over 1.438)");
        assert!(read[3].2 > read[2].2, "and it keeps rising above the threshold");
    }

    /// The identity converts a variance to VIX points through
    /// `100 * sqrt(252)` and nothing else. Asserted against the two
    /// quantities it is built from rather than against a number, so a
    /// change to either side is caught.
    #[test]
    fn the_conversion_is_one_point_per_per_cent_annualised() {
        // A 1 per cent daily sd is 15.874 per cent annualised, and at zero
        // premium that is the VIX.
        let v = 0.01 * 0.01;
        assert!((vix_from_variance(0.0, v) - 100.0 * mathx::sqrt(252.0) * 0.01).abs() < 1e-12);
        // The premium is multiplicative on the whole level.
        assert!((vix_from_variance(0.25, v) - 1.25 * vix_from_variance(0.0, v)).abs() < 1e-12);
    }
}
