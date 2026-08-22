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
/// The flip landed together with what it falsified: `pretium.scenario`'s
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
            + params.market_vol_vix_coupling * (vix_ratio * vix_ratio));
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

fn update_toward_with(
    params: &crate::params::ModelParams,
    current_variance: f64,
    day_factor: f64,
    target_variance: f64,
) -> f64 {
    let base = params.market_factor_sigma * params.market_factor_sigma;
    let omega = (1.0 - params.market_vol_alpha - params.market_vol_beta) * target_variance;
    let new_var = omega
        + params.market_vol_alpha * day_factor * day_factor
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
    variance: f64,
    day_factor: f64,
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
        Self {
            variance: params.market_factor_sigma * params.market_factor_sigma,
            day_factor: 0.0,
        }
    }

    /// Today's factor sigma at DAILY scale — what the tick multiplies by
    /// `1/√390` to draw at per-tick scale.
    pub fn sigma_daily(&self) -> f64 {
        mathx::sqrt(self.variance)
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
        self.variance =
            update_market_variance_with(params, self.variance, self.day_factor, vix);
        self.day_factor = 0.0;
    }

    /// The two state numbers, for checkpoints: `(variance, day_factor)`.
    pub fn snapshot(&self) -> (f64, f64) {
        (self.variance, self.day_factor)
    }

    /// Restore from a checkpoint. Values are adopted verbatim, like every
    /// other restored column: the snapshot is trusted to be one this
    /// engine wrote.
    pub fn restore(variance: f64, day_factor: f64) -> Self {
        Self {
            variance,
            day_factor,
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
        let (v, df) = state.snapshot();
        let restored = MarketVarianceState::restore(v, df);
        assert_eq!(state, restored);
    }
}
