//! Conditional volatility for the SHARED market factor — the structural
//! change finding 14 named, and the escape from the trade it measured.
//!
//! # Why the factor needs its own variance process
//!
//! The market factor was drawn iid Gaussian at constant sigma. Because of
//! that, its contribution to cross-sectional correlation WAS its share of
//! variance, and every point of share it took both Gaussian-diluted the
//! fat tails the per-name GARCH produces and added to total volatility.
//! The eight-point sweep behind finding 14 proved the real correlation
//! band (0.25–0.35) unreachable by that constant at any value: the band
//! arrives only at sigma ≈ 0.021, where excess kurtosis has collapsed to
//! 1.26 against a band floor of 3. Correlation was being bought with
//! kurtosis at a fixed rate.
//!
//! A factor whose variance is itself persistent and shock-driven inverts
//! the trade. Variance mixing makes the factor's unconditional
//! distribution fat-tailed, so the correlated share of every name's
//! return now CONTRIBUTES kurtosis instead of spending it; the variance
//! persistence is market-wide volatility clustering, which the per-name
//! GARCH alone could never produce (the factor's share of every name was
//! iid by construction and actively diluted the per-name clustering); and
//! a high-variance factor regime is a high-correlation regime, which is
//! what crisis correlation is. The increase in the factor's share is
//! FUNDED: [`IDIO_SIGMA_SCALE`] scales the per-name idiosyncratic noise
//! down, so total volatility falls rather than absorbing the new factor
//! variance on top.
//!
//! Measured at the shipped constants (six-seed medians, the published
//! method, committed in `tools/calibration/results/market-factor-vol-*`):
//! cross-sectional correlation **0.260** and excess kurtosis **3.14** —
//! both inside their real bands for the first time in this model's
//! history — with volatility clustering 0.245 (in band), pooled
//! volatility 41.8% (down from 48.3%), and the leverage effect intact at
//! −0.094. The constant-sigma sweep bought correlation 0.089 at the same
//! kurtosis floor; the process does not move along that curve, it
//! dissolves it.
//!
//! # The process
//!
//! GARCH(1,1) on the factor's own daily innovation, at daily scale,
//! reverting to the baseline [`MARKET_FACTOR_SIGMA`]²:
//!
//! ```text
//! v' = (1 − α − β)·target + α·ε² + β·v      then clamped to
//!      [FLOOR_MULTIPLE·base, CEILING_MULTIPLE·base]
//! ```
//!
//! where ε is the day's accumulated market factor — the sum of the tick
//! draws, whose conditional variance over a 390-tick session is exactly
//! `v` — and `target` is the baseline variance scaled by (VIX/anchor)²:
//! VIX read as the factor's implied volatility, per
//! [`MARKET_VOL_VIX_COUPLING`]. The coupling ships ON (1.0) — the era
//! decision that closed finding 6's open question; the constant's doc
//! carries the measurement and the argument.
//!
//! # Draw schedule: ZERO new draws
//!
//! The state is a function of values already drawn (the tick's market
//! factor, accumulated) and of macro state already evolved (VIX, when
//! coupled). The market stream's schedule — 1 normal per tick for the
//! factor, drawn in `simulate_market_tick` — is untouched in count, kind
//! and position. This is the same trick the per-name GARCH uses in
//! `daily.rs`, and it is what keeps the 2026-08 stream split's alignment
//! guarantee intact: the schedule remains a pure function of (status,
//! active set, sector count).
//!
//! # Stationarity, with the arithmetic — and one condition knowingly traded
//!
//! - **Mean reversion / covariance stationarity of the recursion**:
//!   α + β = 0.45 + 0.5 = 0.95 < 1. The unclamped process reverts to the
//!   target with a shock half-life of ln(2)/ln(1/0.95) ≈ 13.5 days —
//!   volatile stretches are two-to-five-week regimes, several per year.
//! - **Boundedness**: the clamp confines the state to
//!   [0.05·base, 8·base] absolutely, so every moment of the SHIPPED
//!   process exists trivially, and the quiet-run fixed point
//!   ω/(1−β) = 0.1·base sits ABOVE the floor — the floor is unreachable
//!   from any admissible state (v' ≥ ω + β·floor = 0.075·base) and is a
//!   pure worst-case guarantee, not a regime the process lives in.
//! - **The condition deliberately not satisfied**: the unclamped fourth-
//!   moment condition (α+β)² + 2α² < 1 evaluates to 0.9025 + 0.405 =
//!   1.31 — violated, so the UNCLAMPED factor's kurtosis would diverge;
//!   the ceiling is load-bearing for the fourth moment, and it does bind
//!   in bursts at these parameters (a 2σ day from twice-baseline variance
//!   reaches it in two steps). This is a measured trade, not an
//!   oversight: the realism panel measures ONE 252-day window, the factor
//!   is one shared path per seed, and every parameterisation satisfying
//!   the fourth-moment condition was measured to deliver a median
//!   one-year SAMPLE kurtosis of at most ~1.7 (α 0.15–0.25 at
//!   persistence 0.95–0.985, ceilings 5–20, standalone recursion over 300
//!   windows) — the asymptotic tails such processes do have live at
//!   horizons the panel cannot see. The shipped α trades asymptotic
//!   niceness for tails that EXIST in the year being measured, and the
//!   clamp supplies the boundedness the dropped condition used to.
//!
//! The same trade is why α = 0.45 sits far above real index-GARCH
//! estimates (~0.1): a real index's kurtosis is estimated across decades;
//! this factor must express its tails inside the single year the panel
//! sees. The per-name GARCH (α 0.09, near-unit-root persistence) keeps
//! the long-memory role; the factor's process is deliberately the bursty
//! one.
//!
//! # Tier 1
//!
//! Multiplies, adds, one `mathx::max`/`min` pair, and a `sqrt`
//! (IEEE-exact on every platform) at the day boundary. No
//! transcendentals, no RNG — the same discipline as `garch.rs`, and the
//! reason GARCH was chosen over an EGARCH/log-variance form, which would
//! have dragged `exp`/`log` into the daily state chain.

use crate::mathx;

#[cfg(test)]
use super::tick::MARKET_FACTOR_SIGMA;

/// Weight on the day's squared factor innovation — the surprise term.
///
/// Far above the per-name ALPHA (0.09), deliberately: the factor is one
/// shared path, and its fat tails must arrive within the one-year window
/// the realism panel measures. See the module header for the
/// window-sampling measurement behind the choice and the fourth-moment
/// condition it knowingly trades away.
pub const MARKET_VOL_ALPHA: f64 = 0.45;

/// Weight on yesterday's factor variance — the persistence term.
///
/// α + β = 0.95: a factor-variance shock half-decays in ~13.5 days, so a
/// volatile stretch is a multi-week regime and a year contains several,
/// which is exactly what lets the panel's window see them.
pub const MARKET_VOL_BETA: f64 = 0.5;

/// Ceiling as a multiple of the baseline variance `MARKET_FACTOR_SIGMA`².
///
/// Load-bearing, not cosmetic: the unclamped fourth moment diverges at
/// the shipped α and β (module header), so the ceiling is what bounds
/// the factor's tail — crisis sigma at most √8 ≈ 2.8x baseline. It binds
/// in bursts at these parameters; sweeping it at shapes where it never
/// binds produced bit-identical panels (ceil 16 vs 20, committed in the
/// stage results), which is how "never binds" was measured rather than
/// assumed.
pub const MARKET_VOL_CEILING_MULTIPLE: f64 = 8.0;

/// Floor as a multiple of the baseline variance.
///
/// A worst-case guarantee only: the quiet-run fixed point ω/(1−β) is
/// 0.1·base and v' ≥ ω + β·floor = 0.075·base, so the floor is
/// unreachable at the shipped shape. It is kept — and kept LOW — because
/// a floor above the quiet fixed point (the 0.25 the per-name process
/// uses) was measured to pin calm periods and destroy the variance
/// dispersion the factor's kurtosis is made of.
pub const MARKET_VOL_FLOOR_MULTIPLE: f64 = 0.05;

/// How much the process's reversion target follows VIX², 0 (autonomous)
/// to 1 (target fully proportional to VIX², i.e. VIX read as the
/// factor's implied volatility).
///
/// **Ships at 1.0 — coupled. This is the era decision that closed
/// finding 6's open question ("should VIX drive the variance
/// process?"), taken on the committed measurement rather than on the
/// name.** Both variants were measured at the shipped constants
/// (`results/market-factor-vol-2026-08-22-stageJ-coupled.json` beside
/// the autonomous stage results):
///
/// - On the ENDOGENOUS panel the two are statistically
///   indistinguishable — correlation 0.253 vs 0.254, kurtosis 3.03 vs
///   3.04, clustering 0.214 vs 0.216 — because endogenous VIX spans only
///   ~13–21 (measured mean 15.1, hard ceiling 26.57), a mild variance
///   modulation the process's own innovations dwarf. Coupling therefore
///   costs the in-band panel nothing.
/// - Under PINNED crisis VIX they are different models: coupled reaches
///   crisis correlation 0.664 at VIX 45 and 0.727 at VIX 65 — the real
///   crisis band (0.6+) this model has never reached by any other
///   mechanism — against 0.365/0.361 autonomous, with crisis volatility
///   82–99% annualised for a HELD pin at those levels.
///
/// **The coupling is undamped, and the ceiling is why that is safe.**
/// The unclamped stationary variance of the recursion equals its target,
/// so a held pin saturates the state at the ceiling once
/// (VIX/anchor)² > `MARKET_VOL_CEILING_MULTIPLE` — above VIX ≈ 42 the
/// response is flat, and a pinned VIX 100 buys essentially the VIX-65
/// market, not a quadratically more violent one. Inside the plausible
/// band the response is quadratic, which is what reading VIX as implied
/// volatility means; a damping factor would make the model realise less
/// volatility than its own implied level says, re-creating in softer
/// form the exact falsehood the coupling removes. A test pins the
/// saturation arithmetic so a recalibration that moves the anchor or the
/// ceiling revisits this argument deliberately.
///
/// The flip landed together with what it falsified: `tradefloor.scenario`'s
/// "What a VIX path actually moves" and `docs/scenarios.md`'s "VIX does
/// not drive volatility" were rewritten in the same change, their tests
/// re-grounded, and the KAT bumped — a constant that silently inverted
/// shipped documentation would have been a lie regardless of what it
/// bought.
pub const MARKET_VOL_VIX_COUPLING: f64 = 1.0;

/// The VIX level at which a coupled target equals the baseline variance.
///
/// Measured, not assumed: mean endogenous VIX over the published panel
/// method (`Universe.random(40, seed=111)`, 252 days, seeds 1–3) is
/// 15.1, range 12.9–21.0. Anchoring at the endogenous mean makes the
/// coupled and autonomous processes agree on the unconditional level, so
/// the coupling question stays isolated from a level change.
pub const MARKET_VOL_VIX_ANCHOR: f64 = 15.0;

/// Scale on the per-name idiosyncratic GARCH sigma — the FUNDING side of
/// the reallocation.
///
/// The factor's variance share rises from ~8% to ~26% of a typical
/// name's realised variance; left unfunded that would land on top of a
/// total volatility already above its band. This constant moves variance
/// OUT of the idiosyncratic per-name noise instead — 0.84 on sigma is
/// ~29% off idiosyncratic variance — which is why pooled volatility FELL
/// from 48.3% to 41.8% while the correlated share tripled. Applied in
/// `factors.rs::calculate_live_factors`, to the idiosyncratic term only;
/// news, flow and squeeze are untouched. At 1.0 the multiply is
/// bit-inert.
///
/// The value comes from the same sweep as `MARKET_FACTOR_SIGMA`; change
/// the pair together or not at all.
pub const IDIO_SIGMA_SCALE: f64 = 0.84;

/// One day's update of the market-factor variance, at daily scale.
///
/// `day_factor` is the day's accumulated market factor (the sum of the
/// per-tick draws); `vix` is the macro VIX at the close. Pure and
/// draw-free.
pub fn update_market_variance(current_variance: f64, day_factor: f64, vix: f64) -> f64 {
    update_market_variance_with(&crate::params::PT_V1, current_variance, day_factor, vix)
}

/// [`update_market_variance`] under explicit model parameters (the runtime
/// seam, CALIBRATION.md §5.3). At [`crate::params::PT_V1`] this is the
/// shipped arithmetic bit for bit: same values, same operations, same
/// order — the constants above remain the definition of the preset.
pub fn update_market_variance_with(
    params: &crate::params::ModelParams,
    current_variance: f64,
    day_factor: f64,
    vix: f64,
) -> f64 {
    let base = params.market_factor_sigma * params.market_factor_sigma;
    // The reversion target: baseline variance, VIX-scaled when coupled.
    // The blend form is kept even at coupling 1.0 because the constant is
    // the calibration seam the sweep patches; the evaluation order is
    // contractual. Two exactness properties anchor it: at coupling 0 the
    // parenthesised blend is exactly 1.0 and `base * 1.0` is `base` to
    // the bit (the autonomous branch stays measurable with no arithmetic
    // residue), and at `vix == MARKET_VOL_VIX_ANCHOR` the ratio is
    // exactly 1.0 at any coupling, so an anchor-level VIX reproduces the
    // autonomous update bit-for-bit — tests pin both.
    let vix_ratio = vix / params.market_vol_vix_anchor;
    let target = base
        * (1.0 - params.market_vol_vix_coupling
            + params.market_vol_vix_coupling * vix_response(params, vix_ratio));
    update_toward_with(params, current_variance, day_factor, target)
}

/// The GARCH step against an explicit target, so the coupling decision is
/// testable on both branches without recompiling a constant.
#[cfg(test)]
fn update_toward(current_variance: f64, day_factor: f64, target_variance: f64) -> f64 {
    update_toward_with(
        &crate::params::PT_V1,
        current_variance,
        day_factor,
        target_variance,
    )
}

/// One variance component's GARCH step against a shared target.
///
/// Both components of the two-timescale process use this, with their own
/// `(alpha, beta)`; the persistence of a component is `alpha + beta`, so
/// its autocorrelation decays at that rate and its half-life is
/// `ln(0.5)/ln(alpha+beta)`.
///
/// Why a MIXTURE and not an additive deviation. The first version of this
/// added `weight * (slow - base)` on top of the fast update, and a sweep
/// killed it in ninety seconds: over a 504-day window a 17-day component
/// is nearly constant, so an additive term acts as a LEVEL SHIFT and adds
/// roughly the same autocorrelation at every lag. The measured curve came
/// out flat -- 0.070 at lag 20 against 0.044 at lag 60, where real markets
/// read 0.029 and 0.005 -- and the parameters barely moved it, which is
/// the signature of a dial that is not connected to the thing it names.
///
/// A mixture `w_f*c_f + w_s*c_s` of two components reverting to the SAME
/// target genuinely superposes two decay rates, which is what the fitted
/// structure needs.
fn component_step(
    current: f64,
    day_factor: f64,
    target: f64,
    alpha: f64,
    beta: f64,
    gamma: f64,
) -> f64 {
    // The GJR term, spelled exactly as `update_toward_with` spells it: a
    // down day loads `alpha + gamma`, omega gives back `gamma/2`. The slow
    // component always passes 0.0 -- the leverage effect is a same-week
    // phenomenon and the slow timescale carries clustering, not asymmetry
    // -- and 0.0 makes every term bit-identical to the symmetric step.
    let leverage = if day_factor < 0.0 { gamma } else { 0.0 };
    (1.0 - alpha - beta - 0.5 * gamma) * target
        + (alpha + leverage) * day_factor * day_factor
        + beta * current
}

/// The slow component's `(alpha, beta)`, from its persistence and the
/// share of that persistence carried by the shock term.
///
/// Parameterised this way because the fitted quantity is the HALF-LIFE --
/// the analytic fit against real markets wants 1 day fast and 17 days slow
/// -- and a half-life is a statement about `alpha + beta`, not about
/// either one alone.
fn slow_alpha_beta(params: &crate::params::ModelParams) -> (f64, f64) {
    let rho = params.market_vol_slow_persistence;
    let share = params.market_vol_slow_gain;
    (share * rho, (1.0 - share) * rho)
}

/// The shared clamp: a variance stays inside its floor and ceiling
/// multiples of the baseline, in the bound order `garch.rs` uses.
fn clamp_variance(params: &crate::params::ModelParams, v: f64) -> f64 {
    let base = params.market_factor_sigma * params.market_factor_sigma;
    mathx::max(
        mathx::min(v, base * params.market_vol_ceiling_multiple),
        base * params.market_vol_floor_multiple,
    )
}


/// The VIX ratio raised to the response exponent. At exactly 2.0 -- every
/// preset before the dial -- this is the literal `r * r` the shipped
/// update has always computed, bit for bit; `powf` never runs there.
/// Round 100: along the real covid path the square is too convex through
/// mid-VIX, and the exponent (with the coupling re-fit to preserve the
/// held-VIX ratio) is the shape the driven window asks for.
fn vix_response(params: &crate::params::ModelParams, vix_ratio: f64) -> f64 {
    if params.market_vol_vix_exponent == 2.0 {
        vix_ratio * vix_ratio
    } else {
        mathx::pow(vix_ratio, params.market_vol_vix_exponent)
    }
}

fn update_toward_with(
    params: &crate::params::ModelParams,
    current_variance: f64,
    day_factor: f64,
    target_variance: f64,
) -> f64 {
    let base = params.market_factor_sigma * params.market_factor_sigma;
    // GJR leverage on the common factor: a down day loads `alpha + gamma`
    // on the squared shock, an up day `alpha` alone, and omega gives back
    // `gamma/2` so the unconditional level stays on target -- the dial
    // redistributes variance between down and up states rather than adding
    // any. At gamma 0.0 every term below is bit-identical to the
    // symmetric update, which is what every preset before the dial ships.
    let gamma = params.market_vol_gamma;
    let leverage = if day_factor < 0.0 { gamma } else { 0.0 };
    let omega = (1.0 - params.market_vol_alpha - params.market_vol_beta - 0.5 * gamma)
        * target_variance;
    let new_var = omega
        + (params.market_vol_alpha + leverage) * day_factor * day_factor
        + params.market_vol_beta * current_variance;
    // `max(min(x, ceiling), floor)` — the same bound order as
    // `garch.rs`, which is contractual there because it is visible when
    // the bounds cross. The bounds here are multiples of a positive
    // compile-time constant so they cannot cross, but two spellings of
    // one idiom in one codebase is how the next reader gets it wrong.
    mathx::max(
        mathx::min(new_var, base * params.market_vol_ceiling_multiple),
        base * params.market_vol_floor_multiple,
    )
}

/// The market factor's conditional-variance state, held by the engine.
///
/// Two numbers: today's variance (fixed for the whole session — the tick
/// reads sigma from the previous close's update, so the factor is
/// conditionally Gaussian within a day) and the day's accumulated factor,
/// which becomes the innovation at the close.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MarketVarianceState {
    /// The MIXTURE — what the tick draws against. Equals `fast_variance`
    /// exactly whenever the slow component is off.
    variance: f64,
    day_factor: f64,
    /// The fast component's own level.
    fast_variance: f64,
    /// The slow component's own level.
    slow_variance: f64,
    /// EMA of the VIX the variance target reads, when
    /// `market_vol_vix_smooth` is on. `None` until the first smoothed
    /// close, and never touched while the dial is 0.0.
    smoothed_vix: Option<f64>,
    /// Yesterday's accumulated day factor, recorded at close for the
    /// lagged transmission wire. Purely observational: nothing in the
    /// variance process reads it.
    prev_day_factor: f64,
}

impl Default for MarketVarianceState {
    fn default() -> Self {
        Self::new()
    }
}

impl MarketVarianceState {
    /// A fresh state at the baseline: day one of every engine draws the
    /// factor at exactly `MARKET_FACTOR_SIGMA`, so the first session of
    /// the conditional process is bit-identical to the constant-sigma
    /// model at the same baseline.
    pub fn new() -> Self {
        Self::new_with(&crate::params::PT_V1)
    }

    /// A fresh state at a given preset's baseline. See [`Self::new`]; the
    /// day-one bit-identity property holds for any baseline, because
    /// `sqrt(x*x)` returns `x`'s bits for any positive finite `x` whose
    /// square neither overflows nor underflows.
    pub fn new_with(params: &crate::params::ModelParams) -> Self {
        let base = params.market_factor_sigma * params.market_factor_sigma;
        Self {
            variance: base,
            day_factor: 0.0,
            fast_variance: base,
            slow_variance: base,
            smoothed_vix: None,
            prev_day_factor: 0.0,
        }
    }

    /// Whether yesterday's session accumulated a DOWN market factor. For
    /// the lagged transmission wire; false before the first close.
    pub fn prev_day_down(&self) -> bool {
        self.prev_day_factor < 0.0
    }

    /// Today's factor sigma at DAILY scale — what the tick multiplies by
    /// `1/√390` to draw at per-tick scale.
    pub fn sigma_daily(&self) -> f64 {
        mathx::sqrt(self.variance)
    }

    /// The variance co-jump (params `vix_selfex_vol_jump`): a fired fear
    /// event lands in the market's own variance, so the following
    /// sessions genuinely move harder and realized volatility carries
    /// the episode. Both the mixture and the fast component take the
    /// kick — the next close re-mixes and re-targets through the normal
    /// update, whose ceiling then applies. Called by the engine with a
    /// non-zero kick only; no state here changes otherwise.
    pub fn inject_variance(&mut self, kick: f64) {
        self.fast_variance += kick;
        self.variance += kick;
    }

    /// Accumulate one tick's market factor into the day's innovation.
    pub fn accumulate(&mut self, market_factor: f64) {
        self.day_factor += market_factor;
    }

    /// Market-open reset of the day accumulator, mirroring the engine's
    /// per-day attribution reset: a day abandoned without a close must
    /// not leak its partial innovation into the next day's update.
    pub fn open_day(&mut self) {
        self.day_factor = 0.0;
    }

    /// Close-of-day update. Consumes the accumulated innovation exactly
    /// once; a second close before any tick sees a zero innovation, which
    /// is the same quiet-day decay the per-name GARCH exhibits.
    pub fn close_day(&mut self, vix: f64) {
        self.close_day_with(&crate::params::PT_V1, vix);
    }

    /// [`Self::close_day`] under explicit model parameters. What the engine
    /// calls; at [`crate::params::PT_V1`] it is the shipped update bit for
    /// bit.
    pub fn close_day_with(&mut self, params: &crate::params::ModelParams, vix: f64) {
        // The fear the target reads, not necessarily today's print. Real
        // volatility follows sustained fear with inertia; a model that
        // transmits every VIX print one-for-one into the variance target
        // injects the print-to-print churn straight into daily returns,
        // which round 99 measured as the whole of the driven excess. At
        // `market_vol_vix_smooth` = 0 -- every shipped preset -- the print
        // is read raw and this state never updates, bit for bit.
        let vix = if params.market_vol_vix_smooth == 0.0 {
            vix
        } else {
            let alpha = 2.0 / (params.market_vol_vix_smooth + 1.0);
            let prev = self.smoothed_vix.unwrap_or(vix);
            let sm = prev + alpha * (vix - prev);
            self.smoothed_vix = Some(sm);
            sm
        };
        let base = params.market_factor_sigma * params.market_factor_sigma;
        let vix_ratio = vix / params.market_vol_vix_anchor;
        let target = base
            * (1.0 - params.market_vol_vix_coupling
                + params.market_vol_vix_coupling * vix_response(params, vix_ratio));

        let w = params.market_vol_slow_weight;

        // The zero-weight branch is a BRANCH and not arithmetic on purpose.
        // Every preset before pt-v4 must reproduce the single-component
        // update to the bit, and this is the only spelling that owes
        // nothing to an argument about how floats behave.
        if w == 0.0 {
            self.variance = update_market_variance_with(
                params, self.variance, self.day_factor, vix);
            self.fast_variance = self.variance;
            self.prev_day_factor = self.day_factor;
            self.day_factor = 0.0;
            return;
        }

        let fast = clamp_variance(
            params,
            component_step(self.fast_variance, self.day_factor, target,
                           params.market_vol_alpha, params.market_vol_beta,
                           params.market_vol_gamma),
        );
        // The slow component may revert to a LESS VIX-coupled target than
        // the fast one, which is the whole point of having two.
        //
        // With a shared target, adding a slow component makes the mixture
        // track a VIX spike more sluggishly than the fast component alone --
        // the opposite of what the scenario transient needs. The measured
        // defect is exactly that: pt-v3 retains 95.2% of pt-v1's STEADY-STATE
        // VIX lever and only 27.6% of its TRANSIENT, because one variance
        // timescale is doing two jobs. Within-year clustering wants long
        // memory; tracking a twenty-day spike wants short.
        //
        // Damping the slow component's coupling separates them: the fast
        // component chases VIX, the slow one carries the autonomous level and
        // the long memory. At 0.0 the two targets are the same expression and
        // the branch is skipped, so every preset before pt-v4 is untouched.
        let slow_target = if params.market_vol_slow_vix_damp == 0.0 {
            target
        } else {
            let c = params.market_vol_vix_coupling
                * (1.0 - params.market_vol_slow_vix_damp);
            base * (1.0 - c + c * vix_response(params, vix_ratio))
        };
        let (sa, sb) = slow_alpha_beta(params);
        let slow = clamp_variance(
            params,
            component_step(self.slow_variance, self.day_factor, slow_target, sa, sb, 0.0),
        );

        self.fast_variance = fast;
        self.slow_variance = slow;
        // Both components revert to the SAME target, so the mixture's
        // autocorrelation is the weighted sum of two decay rates rather
        // than one decay plus a level.
        self.variance = clamp_variance(params, (1.0 - w) * fast + w * slow);
        self.prev_day_factor = self.day_factor;
        self.day_factor = 0.0;
    }

    /// The state numbers, for checkpoints: `(variance, day_factor,
    /// fast_variance, slow_variance, prev_day_factor, smoothed_vix)`.
    /// `smoothed_vix` is a VIX-scale positive number while primed;
    /// a negative value is the None sentinel (JSON carries no NaN).
    pub fn snapshot(&self) -> (f64, f64, f64, f64, f64, f64) {
        (self.variance, self.day_factor, self.fast_variance,
         self.slow_variance, self.prev_day_factor,
         self.smoothed_vix.unwrap_or(-1.0))
    }

    /// Restore from a checkpoint. Values are adopted verbatim, like every
    /// other restored column: the snapshot is trusted to be one this
    /// engine wrote.
    pub fn restore(variance: f64, day_factor: f64) -> Self {
        // A two-value checkpoint predates the slow component. Adopting the
        // variance as the slow level is the only choice that keeps such a
        // checkpoint replaying identically under a legacy preset, where the
        // slow level is inert and never read.
        Self {
            variance,
            day_factor,
            fast_variance: variance,
            slow_variance: variance,
            // Pre-dial checkpoints carry no smoothed fear; the EMA
            // re-seeds from the first smoothed close after restore.
            smoothed_vix: None,
            prev_day_factor: 0.0,
        }
    }

    /// Restore including the slow component, for checkpoints that carry it.
    pub fn restore_with_components(
        variance: f64,
        day_factor: f64,
        fast_variance: f64,
        slow_variance: f64,
        prev_day_factor: f64,
        smoothed_vix: f64,
    ) -> Self {
        // A checkpoint from before the lagged wire carries no
        // prev_day_factor; the caller passes 0.0 and the wire simply does
        // not fire on the first restored session, which is what those
        // checkpoints' era did anyway. Same for the smoothed fear: a
        // negative sentinel means unprimed, and the EMA re-seeds from the
        // first smoothed close -- bit-identical for every checkpoint
        // written while the dial was inert.
        Self {
            variance, day_factor, fast_variance, slow_variance,
            smoothed_vix: if smoothed_vix < 0.0 { None } else { Some(smoothed_vix) },
            prev_day_factor,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const BASE: f64 = MARKET_FACTOR_SIGMA * MARKET_FACTOR_SIGMA;

    #[test]
    fn the_recursion_reverts_and_the_clamp_carries_the_fourth_moment() {
        // The module header's arithmetic, pinned as it is actually argued.
        // Mean reversion of the unclamped recursion:
        let persistence = MARKET_VOL_ALPHA + MARKET_VOL_BETA;
        assert!(
            persistence < 1.0,
            "alpha + beta = {persistence}: at or above 1 the factor variance is non-stationary"
        );
        // The unclamped fourth-moment condition is KNOWINGLY violated --
        // the ceiling bounds the tail instead. Assert the violation, so
        // that if a recalibration ever moves back inside the condition,
        // whoever did it revisits the ceiling's load-bearing role (and
        // this test) rather than inheriting a clamp it no longer needs.
        let fourth = persistence * persistence + 2.0 * MARKET_VOL_ALPHA * MARKET_VOL_ALPHA;
        assert!(
            fourth > 1.0,
            "(a+b)^2 + 2a^2 = {fourth}: the shipped shape trades the unclamped fourth-moment \
             condition for in-window tails; if this now holds, the ceiling's role changed"
        );
        // And the boundedness that replaces it: ordered bounds, and a
        // quiet-run fixed point ABOVE the floor, so the floor is a
        // worst-case guarantee rather than a regime.
        assert!(MARKET_VOL_FLOOR_MULTIPLE < MARKET_VOL_CEILING_MULTIPLE);
        let quiet_fixed_point = (1.0 - persistence) / (1.0 - MARKET_VOL_BETA);
        assert!(
            quiet_fixed_point > MARKET_VOL_FLOOR_MULTIPLE,
            "quiet fixed point {quiet_fixed_point} at or below the floor pins calm periods \
             and destroys the variance dispersion kurtosis is made of (measured; see module doc)"
        );
    }

    #[test]
    fn a_violent_market_day_raises_tomorrows_factor_variance() {
        // The defining property, exactly as garch.rs states it for the
        // per-name process.
        let calm = update_market_variance(BASE, 0.0005, 15.0);
        let shocked = update_market_variance(BASE, 0.04, 15.0);
        assert!(
            shocked > calm,
            "a 4% market day must raise factor variance above a 0.05% day: {shocked} vs {calm}"
        );
    }

    #[test]
    fn factor_variance_clusters_rather_than_resetting() {
        let mut shocked = update_market_variance(BASE, 0.04, 15.0);
        for _ in 0..3 {
            shocked = update_market_variance(shocked, 0.0, 15.0);
        }
        let mut never = BASE;
        for _ in 0..4 {
            never = update_market_variance(never, 0.0, 15.0);
        }
        assert!(
            shocked > never * 1.2,
            "three days after a 4% market day the factor variance must still be elevated: \
             {shocked} vs {never}"
        );
    }

    #[test]
    fn a_quiet_run_settles_at_the_fixed_point_above_the_floor() {
        // ω/(1−β) = 0.1·base — the floor at 0.05 is deliberately BELOW
        // it, so calm markets keep a live, dispersed factor variance
        // instead of being pinned flat (the pinning was measured to
        // destroy the factor's kurtosis; see the floor's doc).
        let mut v = update_market_variance(BASE, 0.06, 15.0);
        for _ in 0..400 {
            v = update_market_variance(v, 0.0, 15.0);
        }
        let fixed_point =
            BASE * (1.0 - MARKET_VOL_ALPHA - MARKET_VOL_BETA) / (1.0 - MARKET_VOL_BETA);
        assert!(
            (v - fixed_point).abs() < fixed_point * 1e-9,
            "400 quiet days should settle at the fixed point {fixed_point}, got {v}"
        );
        assert!(v > BASE * MARKET_VOL_FLOOR_MULTIPLE);
    }

    #[test]
    fn the_variance_stays_inside_its_bounds_however_hard_it_is_driven() {
        let mut v = BASE;
        for day in 0..1000 {
            let eps = if day % 2 == 0 { 0.5 } else { 0.0 };
            v = update_market_variance(v, eps, 15.0);
            assert!(
                v <= BASE * MARKET_VOL_CEILING_MULTIPLE * 1.000001,
                "day {day}: variance {v} escaped the ceiling"
            );
            assert!(
                v >= BASE * MARKET_VOL_FLOOR_MULTIPLE * 0.999999,
                "day {day}: variance {v} fell through the floor"
            );
        }
    }

    #[test]
    fn the_ceiling_is_reachable_in_a_burst_so_it_is_a_bound_and_not_a_prop() {
        // The ceiling carries the fourth moment, so it must actually be
        // reachable by the dynamics it bounds. Two hard days from an
        // already-elevated regime must hit it.
        let mut v = BASE * 2.0;
        v = update_market_variance(v, 2.0 * mathx::sqrt(v), 15.0); // a 2-sigma day
        v = update_market_variance(v, 2.0 * mathx::sqrt(v), 15.0); // another
        assert!(
            v >= BASE * MARKET_VOL_CEILING_MULTIPLE * 0.999999,
            "two 2-sigma days from twice-baseline variance should reach the ceiling, got \
             {} x base",
            v / BASE
        );
    }

    #[test]
    fn the_shipped_coupling_is_on_so_vix_moves_the_variance_target() {
        // The era decision, pinned from the other side: this test's
        // predecessor asserted VIX 5 and VIX 65 produce bit-identical
        // updates while the coupling shipped at zero. The flip that
        // closed finding 6's open question inverts it -- a crisis VIX
        // must now pull tomorrow's factor variance ABOVE what a calm VIX
        // leaves, at every innovation. If this fails, the coupling was
        // turned back off, which un-answers a question the era answered
        // by measurement and re-falsifies the scenario/docs claims that
        // were rewritten to match; see the constant's doc.
        assert_eq!(MARKET_VOL_VIX_COUPLING, 1.0);
        for eps in [0.0, 0.01, 0.04] {
            let calm = update_market_variance(BASE * 2.0, eps, 5.0);
            let crisis = update_market_variance(BASE * 2.0, eps, 65.0);
            assert!(
                crisis > calm,
                "eps {eps}: VIX 65 must raise the variance target above VIX 5, \
                 got {crisis} vs {calm}"
            );
        }
    }

    #[test]
    fn an_anchor_level_vix_reproduces_the_autonomous_update_to_the_bit() {
        // The coupling is a pure modulation around the anchor: at
        // VIX == MARKET_VOL_VIX_ANCHOR the ratio is exactly 1.0, the
        // blend is exactly 1.0 at ANY coupling, and the update must equal
        // the explicit-target form at the baseline bit-for-bit. This is
        // what keeps the coupling question separated from a level change
        // -- the same isolation the anchor's own doc argues.
        for (v, eps) in [(BASE, 0.0), (BASE * 2.0, 0.01), (BASE * 0.5, 0.04)] {
            let coupled = update_market_variance(v, eps, MARKET_VOL_VIX_ANCHOR);
            let autonomous = update_toward(v, eps, BASE);
            assert_eq!(coupled.to_bits(), autonomous.to_bits(), "v {v} eps {eps}");
        }
    }

    #[test]
    fn a_held_crisis_pin_saturates_at_the_ceiling_so_the_response_is_flat_above_it() {
        // The damping argument, as arithmetic. The unclamped stationary
        // variance of the recursion equals its target, so once
        // (VIX/anchor)^2 exceeds the ceiling multiple the state pins at
        // the ceiling and the response to a harder pin is FLAT: a held
        // VIX 45 and a held VIX 100 bound the factor identically, and the
        // measured 82-99% crisis volatility is a ceiling regime, not the
        // start of a quadratic ramp. The coupling ships undamped BECAUSE
        // this holds; if the anchor or the ceiling moves, this test drags
        // the saturation point back into view.
        let saturation_vix = MARKET_VOL_VIX_ANCHOR * mathx::sqrt(MARKET_VOL_CEILING_MULTIPLE);
        assert!(
            saturation_vix < 45.0,
            "saturation at VIX {saturation_vix}: a held VIX 45 no longer pins the \
             ceiling, so the held-pin volatility argument needs re-measuring"
        );
        // And the state genuinely cannot exceed the ceiling under the
        // hardest coupled pin, however hard the innovations drive it.
        let mut v = BASE;
        for _ in 0..300 {
            v = update_market_variance(v, 3.0 * mathx::sqrt(v), 100.0);
            assert!(v <= BASE * MARKET_VOL_CEILING_MULTIPLE * 1.000001);
        }
        assert!(
            v >= BASE * MARKET_VOL_CEILING_MULTIPLE * 0.999999,
            "a relentless VIX-100 pin should hold the state at the ceiling, got {} x base",
            v / BASE
        );
    }

    #[test]
    fn a_coupled_target_scales_with_vix_squared_and_stays_clamped() {
        // The seam form the sweep patches, exercised explicitly: a larger
        // target raises the update, and a crisis-sized target cannot push
        // the state past the ceiling that bounds the process everywhere.
        let anchored = update_toward(BASE, 0.0, BASE);
        let doubled = update_toward(BASE, 0.0, BASE * 4.0);
        assert!(doubled > anchored);
        let extreme = update_toward(BASE * MARKET_VOL_CEILING_MULTIPLE, 0.06, BASE * 20.0);
        assert!(extreme <= BASE * MARKET_VOL_CEILING_MULTIPLE * 1.000001);
    }

    #[test]
    fn the_state_accumulates_a_day_and_consumes_it_exactly_once() {
        let mut state = MarketVarianceState::new();
        state.accumulate(0.02);
        state.accumulate(0.02);
        state.close_day(15.0);
        let after_shock = state.snapshot().0;
        assert!(
            after_shock > BASE,
            "a 4% accumulated day must raise the variance: {after_shock}"
        );
        // The innovation was consumed: a second close decays instead of
        // double-counting.
        let mut again = state;
        again.close_day(15.0);
        assert!(again.snapshot().0 < after_shock);
    }

    #[test]
    fn an_abandoned_day_does_not_leak_into_the_next_innovation() {
        let mut state = MarketVarianceState::new();
        state.accumulate(0.05);
        state.open_day(); // day abandoned without a close
        state.close_day(15.0);
        let mut clean = MarketVarianceState::new();
        clean.close_day(15.0);
        assert_eq!(state.snapshot().0.to_bits(), clean.snapshot().0.to_bits());
    }

    #[test]
    fn a_fresh_state_draws_at_exactly_the_baseline_sigma() {
        // Day one of the conditional process is the constant-sigma model:
        // sqrt of the squared constant must return the constant's bits.
        assert_eq!(
            MarketVarianceState::new().sigma_daily().to_bits(),
            MARKET_FACTOR_SIGMA.to_bits()
        );
    }

    #[test]
    fn snapshot_and_restore_round_trip_exactly() {
        let mut state = MarketVarianceState::new();
        state.accumulate(0.007);
        state.close_day(22.0);
        state.accumulate(-0.003);
        let (v, df, fast, slow, pdf, sv) = state.snapshot();
        let restored = MarketVarianceState::restore_with_components(v, df, fast, slow, pdf, sv);
        assert_eq!(state, restored);
    }

    #[test]
    fn a_two_value_checkpoint_still_replays_under_a_legacy_preset() {
        // Checkpoints written before the slow component carry two numbers,
        // so the slow level cannot be reconstructed from them. That is
        // harmless exactly where it has to be: under any preset with the
        // slow component off the level is never read, so the RESTORED
        // state prices identically even though it is not `==` to the
        // original. This pins the property that matters -- the
        // trajectory -- rather than the struct equality that does not.
        let params = &crate::params::PT_V1;
        assert_eq!(params.market_vol_slow_weight, 0.0);

        let mut original = MarketVarianceState::new_with(params);
        original.accumulate(0.011);
        original.close_day_with(params, 31.0);
        original.accumulate(-0.004);

        let (v, df, _fast, _slow, _pdf, _sv) = original.snapshot();
        let mut restored = MarketVarianceState::restore(v, df);

        for day in 0..12 {
            let vix = 15.0 + day as f64;
            let mut a = original;
            let mut b = restored;
            a.close_day_with(params, vix);
            b.close_day_with(params, vix);
            assert_eq!(a.sigma_daily(), b.sigma_daily(), "day {day}");
            original = a;
            restored = b;
        }
    }

    #[test]
    fn the_slow_component_is_inert_at_its_legacy_values() {
        // The bit-identity contract for every preset before pt-v4: with
        // the slow component off, the composed update must equal the
        // single-component update exactly, not approximately.
        let params = &crate::params::PT_V1;
        let mut composed = MarketVarianceState::new_with(params);
        let mut variance = params.market_factor_sigma * params.market_factor_sigma;

        for day in 0..40 {
            let innovation = 0.004 * ((day % 7) as f64 - 3.0);
            let vix = 12.0 + (day % 11) as f64;
            composed.accumulate(innovation);
            composed.close_day_with(params, vix);
            variance = update_market_variance_with(params, variance, innovation, vix);
            assert_eq!(composed.sigma_daily(), mathx::sqrt(variance), "day {day}");
        }
    }
}

#[cfg(test)]
mod gjr_tests {
    use super::*;

    /// The dial off is the symmetric step to the bit, whatever the sign
    /// of the day.
    #[test]
    fn gamma_zero_is_the_symmetric_step_bit_for_bit() {
        for d in [-0.02, -0.001, 0.0, 0.001, 0.02] {
            let sym = (1.0 - 0.28 - 0.69) * 4e-4 + 0.28 * d * d + 0.69 * 3e-4;
            let gjr = component_step(3e-4, d, 4e-4, 0.28, 0.69, 0.0);
            assert_eq!(sym.to_bits(), gjr.to_bits(), "day_factor {d}");
        }
    }

    /// A down day loads more variance than an up day of the same size,
    /// and omega's gamma/2 rebate keeps the two-sided average on the
    /// symmetric step: the dial redistributes, it does not add.
    #[test]
    /// A down day loads more variance than an up day of the same size.
    /// The gamma/2 rebate in omega makes the two-sided average equal the
    /// symmetric step exactly at the stationary point, and differ by
    /// (gamma/2)(d^2 - t) elsewhere -- both pinned below.
    fn gamma_redistributes_between_down_and_up_without_adding() {
        let (a, b, g, v, t) = (0.28, 0.69, 0.10, 3e-4, 4e-4);
        // At the stationary point (shock at target) the gamma/2 rebate is
        // exact: the two-sided average equals the symmetric step, so in
        // equilibrium the dial redistributes variance between down and up
        // states rather than adding any.
        let at_target = crate::mathx::sqrt(t);
        let down = component_step(v, -at_target, t, a, b, g);
        let up = component_step(v, at_target, t, a, b, g);
        let sym = component_step(v, at_target, t, a, b, 0.0);
        assert!(down > up, "leverage must load the down side");
        let two_sided = 0.5 * (down + up);
        assert!(
            (two_sided - sym).abs() < 1e-18,
            "at the stationary point the rebate is exact: {two_sided} vs {sym}"
        );
        // Off target the gap is (gamma/2)(d^2 - t) by construction.
        let d = 0.015;
        let gap = 0.5
            * (component_step(v, -d, t, a, b, g) + component_step(v, d, t, a, b, g))
            - component_step(v, d, t, a, b, 0.0);
        assert!(
            (gap - 0.5 * g * (d * d - t)).abs() < 1e-18,
            "gap {gap} must be (gamma/2)(d^2 - t)"
        );
    }
}
