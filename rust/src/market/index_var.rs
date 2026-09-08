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
//! - **The downside transmission tilt.** `market_beta_down_asym` scales one
//!   side of a zero-mean draw, which raises its variance by
//!   `a + a^2/2` — 2.5 per cent of the factor term at the shipped 0.025,
//!   about 1.5 per cent of `V_t`.
//! - **The crash amplifier and the crisis blend.** Both are conditional on
//!   a tail the closed form has no moment for: the amplifier fires above
//!   `crash_amplifier_threshold` conditional sigmas and the blend above
//!   `crisis_vix_threshold`. Silent through the calm range this variance is
//!   read over, and understated in a crisis — which is the direction that
//!   makes the VIX conservative rather than the direction that flatters it.
//! - **Reversion, momentum, crowd lean and the squeeze**, measured together
//!   at 0.6 per cent of the index's variance, and the cross-covariances
//!   between components, measured at 2.6 per cent.
//!
//! Together those are worth about five per cent of `V_t`, so 2.5 per cent
//! of the VIX — half a point at VIX 20, which is inside the measured
//! spread of the variance premium the level is multiplied by. They are not
//! quietly folded into a coefficient: the residual is the residual.
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
    pub fn total(&self) -> f64 {
        self.k * (self.factor_raw + self.sector_raw + self.idio_raw)
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
    k: f64,
) -> f64 {
    index_conditional_variance_terms(
        p, names, sector_count, factor_variance, sector_sigma, jump_rate_scale, k)
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
    k: f64,
) -> IndexVarianceTerms {
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

    let factor_raw = beta_w * beta_w * factor_variance;

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
    index_conditional_variance(
        p, &at_rest, sector_count, factor_variance, sector_sigma, 1.0, k)
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
        let v_f = p.market_factor_sigma * p.market_factor_sigma;
        let only_factor =
            index_conditional_variance(&p, &names, 3, v_f, 0.0, 1.0, k);
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
        let want = k * (beta_w * beta_w * v_f + idio);
        assert!((only_factor - want).abs() < 1e-18, "{only_factor} vs {want}");

        // News alone, on top: an exactly computable increment.
        let mut with_news = p.clone();
        with_news.endogenous_news_intensity = 0.05;
        with_news.endogenous_news_sigma = 0.02;
        let news = index_conditional_variance(&with_news, &names, 3, v_f, 0.0, 1.0, k)
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
        let at_k = index_conditional_variance(&p, &names, 3, v_f, 0.0, 1.0, k);
        let at_one = index_conditional_variance(&p, &names, 3, v_f, 0.0, 1.0, 1.0);
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

    /// KEEPING THE TERMS DOES NOT MOVE THE SUM, ON BITS.
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
    #[test]
    fn the_terms_sum_to_the_variance_they_were_split_from() {
        let k_curve = intraday_variance_factor();
        for (label, p) in parameter_spread() {
            for names in [roster(), roster_with_a_stray_sector()] {
                for &sector_count in &[3usize, 5] {
                    for i in 0..=12 {
                        let factor_variance = 1.0e-6 + i as f64 * 8.3e-5;
                        for j in 0..=6 {
                            let sector_sigma = j as f64 * 0.0061;
                            for &scale in &[0.0, 0.25, 1.0, 1.7, 4.9, 27.5] {
                                for &k in &[1.0, k_curve, 2.25] {
                                    let want = the_sum_as_it_was_written(
                                        &p, &names, sector_count, factor_variance,
                                        sector_sigma, scale, k);
                                    let terms = index_conditional_variance_terms(
                                        &p, &names, sector_count, factor_variance,
                                        sector_sigma, scale, k);
                                    assert_eq!(
                                        terms.total(), want,
                                        "{label}: sectors {sector_count}, v_f \
                                         {factor_variance}, sigma_s {sector_sigma}, \
                                         scale {scale}, k {k}");
                                    assert_eq!(
                                        index_conditional_variance(
                                            &p, &names, sector_count, factor_variance,
                                            sector_sigma, scale, k),
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
        let (v_f, sigma_s, scale) = (0.00021, 0.0093, 1.6);
        let terms = index_conditional_variance_terms(
            &p, &names, 3, v_f, sigma_s, scale, k);

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
            &p, &names, 3, v_f, 0.0, scale, k);
        assert_eq!(no_sector.sector_raw, 0.0,
                   "the sector block did not follow its sigma");
        assert_eq!(no_sector.idio_raw, terms.idio_raw,
                   "the sector sigma reached idio_raw");
        assert_eq!(no_sector.factor_raw, terms.factor_raw,
                   "the sector sigma reached factor_raw");

        let mut quiet = p.clone();
        quiet.endogenous_news_intensity = 0.0;
        let no_news = index_conditional_variance_terms(
            &quiet, &names, 3, v_f, sigma_s, scale, k);
        assert_eq!(no_news.news, 0.0,
                   "the news block did not follow its intensity");
        assert_eq!(no_news.idio_jump, terms.idio_jump,
                   "the news intensity reached idio_jump");

        let no_factor = index_conditional_variance_terms(
            &p, &names, 3, 0.0, sigma_s, scale, k);
        assert_eq!(no_factor.factor_raw, 0.0,
                   "the factor block did not follow its variance");
        assert_eq!(no_factor.sector_raw, terms.sector_raw,
                   "the factor variance reached sector_raw");
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
