//! The engine's half of what derivatives read: the price index
//! (`index_level_listed`), the live VIX (`vix_intraday_live`) and the
//! forecast (`forecast_horizon_sessions`).
//!
//! Each reads the engine's state and writes only its own fields, so a model
//! with these switches on prints the prices the same model prints with them
//! off. None of them takes a draw.

use super::*;
use crate::derivatives::Forecast;
use crate::market::index_value::{
    calculate_market_index, rebased_divisor, IndexConstituent, IndexLevel, INDEX_BASE,
};
use crate::market::index_var::{
    index_conditional_variance_terms_with_states, intraday_variance_factor,
    name_noise_variance_scaled, name_session_variance, vix_from_variance, NameConstants,
    NameVariance,
};

/// Five-point Gauss-Hermite nodes and weights (physicists' form, the
/// weights summing to the square root of pi).
const GAUSS_HERMITE_5: [(f64, f64); 5] = [
    (-2.020_182_870_456_086, 0.019_953_242_059_045_913),
    (-0.958_572_464_613_818_5, 0.393_619_323_152_241_2),
    (0.0, 0.945_308_720_482_941_9),
    (0.958_572_464_613_818_5, 0.393_619_323_152_241_2),
    (2.020_182_870_456_086, 0.019_953_242_059_045_913),
];

/// The day's market factor in the forecast's expected close, integrated by
/// four equally likely nodes: the means of a standard normal's four
/// equal-probability bins, rescaled so the rule's variance is one. The close
/// is quadratic in the day's factor with a sign asymmetry, which any
/// symmetric unit-variance rule integrates exactly, and each sign keeps two
/// nodes for the lagged wire's faces.
const FACTOR_NODES: [f64; 4] = [-1.370_224_249_159_755, -0.349_979_295_122_705,
                                0.349_979_295_122_705, 1.370_224_249_159_755];

/// Forty-point Gauss-Hermite nodes and weights (physicists' form), the
/// positive half; the rule is symmetric. Fine enough for the stress premium,
/// which has a kink at its knee, and for a skewed spread's tail.
const GAUSS_HERMITE_40_HALF: [(f64, f64); 20] = [
    (0.17453721459758237, 0.3386432774255891),
    (0.5238747138322772, 0.2657282518773773),
    (0.8740066123570881, 0.1633787327132714),
    (1.225480109046289, 0.07847460586540442),
    (1.5788698949316138, 0.029312565536172383),
    (1.9347914722822959, 0.008460888008258134),
    (2.2939171418750837, 0.0018714968295979483),
    (2.6569959984428957, 0.00031385359454133175),
    (3.0248798839012845, 3.936933981092491e-5),
    (3.398558265859628, 3.6315761506930256e-6),
    (3.7792067534352234, 2.411144163670531e-7),
    (4.1682570668325, 1.1212360832275839e-8),
    (4.567502072844395, 3.525620791365422e-10),
    (4.9792609785452555, 7.156528052690362e-12),
    (5.406654247970128, 8.805707645216109e-14),
    (5.8540950560304, 6.008358789490818e-16),
    (6.328255351220082, 1.9891810121165008e-18),
    (6.840237305249355, 2.567593365411684e-21),
    (7.411582531485469, 8.544056963775433e-25),
    (8.09876113925085, 2.5910437138470347e-29),
];

/// The shape of the log VIX's spread about the forecast's path: a standard
/// variable, skewed to `forecast_vix_dispersion_skew`, on the forty nodes of
/// a standard normal. Each node is `(shape, weight)`, the weights summing to
/// one and the shapes to mean 0 and variance 1 under them. At a skew of 0.0
/// the shape is the normal itself; off it, a skew-normal, the normal's
/// weights times `2 Phi(a z)`, with `a` the one whose skewness under the
/// rule is the skew. Its tails stay normal, so every power of the VIX the
/// forecast takes an expectation of stays finite; its skewness reaches
/// about 0.99.
fn spread_shape(skew: f64) -> Vec<(f64, f64)> {
    let root_pi = crate::mathx::sqrt(std::f64::consts::PI);
    let mut normal = Vec::with_capacity(40);
    for &(x, w) in GAUSS_HERMITE_40_HALF.iter().rev() {
        normal.push((-std::f64::consts::SQRT_2 * x, w / root_pi));
    }
    for &(x, w) in GAUSS_HERMITE_40_HALF.iter() {
        normal.push((std::f64::consts::SQRT_2 * x, w / root_pi));
    }
    // The skew-normal of shape `a`, standardised under the rule itself, so a
    // node's shape is exactly a number of the spread's sds; and its
    // skewness under the rule.
    let skewed = |a: f64| -> (Vec<(f64, f64)>, f64) {
        let mut nodes = normal.clone();
        if a != 0.0 {
            let mut total = 0.0;
            for node in nodes.iter_mut() {
                node.1 *= crate::mathx::erfc(-a * node.0 / std::f64::consts::SQRT_2);
                total += node.1;
            }
            for node in nodes.iter_mut() {
                node.1 /= total;
            }
        }
        let mean: f64 = nodes.iter().map(|(z, w)| w * z).sum();
        let var: f64 = nodes.iter().map(|(z, w)| w * (z - mean) * (z - mean)).sum();
        let sd = crate::mathx::sqrt(var);
        let mut third = 0.0;
        for node in nodes.iter_mut() {
            node.0 = (node.0 - mean) / sd;
            third += node.1 * node.0 * node.0 * node.0;
        }
        (nodes, third)
    };
    if skew == 0.0 {
        return skewed(0.0).0;
    }
    let (mut lo, mut hi) = (0.0, 50.0);
    for _ in 0..60 {
        let mid = 0.5 * (lo + hi);
        if skewed(mid).1 < skew.abs() {
            lo = mid;
        } else {
            hi = mid;
        }
    }
    // A left skew is the right one reflected.
    skewed(skew.signum() * 0.5 * (lo + hi)).0
}

/// The log of `V / mean` at each node of a spread of log variance `var`
/// with shape `shape` ([`spread_shape`]), shifted so that `E[V]` is the mean
/// under the rule. Each entry is `(log ratio, weight)`.
fn spread_at(shape: &[(f64, f64)], var: f64) -> Vec<(f64, f64)> {
    let sd = crate::mathx::sqrt(crate::mathx::max(0.0, var));
    let total: f64 = shape.iter().map(|(s, w)| w * crate::mathx::exp(sd * s)).sum();
    let shift = -crate::mathx::log(total);
    shape.iter().map(|(s, w)| (shift + sd * s, *w)).collect()
}

/// `E[g(V)]` for `V = mean * exp(x)` over the nodes of [`spread_at`].
fn spread_expect(nodes: &[(f64, f64)], mean: f64, g: impl Fn(f64) -> f64) -> f64 {
    let mut total = 0.0;
    for &(x, w) in nodes {
        total += w * g(mean * crate::mathx::exp(x));
    }
    total
}

/// `E[g(X)]` for `X` lognormal with mean `mean` and log variance `var`, by
/// five-point Gauss-Hermite; `g(mean)` at a variance of 0.0.
fn lognormal_expect(mean: f64, var: f64, g: impl Fn(f64) -> f64) -> f64 {
    if !(var > 0.0) || !(mean > 0.0) {
        return g(mean);
    }
    let sd = crate::mathx::sqrt(var);
    let mu = crate::mathx::log(mean) - 0.5 * var;
    let mut total = 0.0;
    for &(node, weight) in GAUSS_HERMITE_5.iter() {
        total += weight * g(crate::mathx::exp(mu + sd * std::f64::consts::SQRT_2 * node));
    }
    total / crate::mathx::sqrt(std::f64::consts::PI)
}

/// `E[max(X - k, 0)]` for `X` lognormal with mean `mean` and log variance
/// `var`: `mean N(d1) - k N(d2)`.
fn lognormal_excess(mean: f64, var: f64, k: f64) -> f64 {
    if !(var > 0.0) || !(mean > 0.0) || !(k > 0.0) {
        return if mean > k { mean - k } else { 0.0 };
    }
    let sd = crate::mathx::sqrt(var);
    let d1 = (crate::mathx::log(mean / k) + 0.5 * var) / sd;
    let d2 = d1 - sd;
    let cdf = |x: f64| 0.5 * crate::mathx::erfc(-x / std::f64::consts::SQRT_2);
    mean * cdf(d1) - k * cdf(d2)
}

/// A slot the forecast follows: a public, solvent name with a
/// capitalisation, as the variance identity reads it.
struct ForecastName {
    slot: usize,
    name: NameVariance,
    constants: NameConstants,
    sector_base: f64,
    garch_beta: f64,
}

impl Engine {
    // ── The index (`index_level_listed`) ──────────────────────────────────

    fn index_on(&self) -> bool {
        self.params.index_level_listed != 0.0
    }

    /// The roster as [`calculate_market_index`] reads it: every public name
    /// at `price * shares_outstanding`, marked when bankrupt; the public,
    /// solvent names' ids as the components; and their capitalisation,
    /// summed in roster order as that function sums it.
    pub(super) fn index_constituents(&self) -> (Vec<IndexConstituent>, Vec<String>, f64) {
        let mut roster = Vec::with_capacity(self.companies.len());
        let mut ids = Vec::with_capacity(self.companies.len());
        let mut cap = 0.0;
        for c in &self.companies {
            if !c.is_public {
                continue;
            }
            let mut k = IndexConstituent::new(c.id.clone(), c.stock.price * c.stock.shares_outstanding);
            if c.is_bankrupt {
                k.is_bankrupt = true;
            } else {
                ids.push(c.id.clone());
                cap += k.market_cap;
            }
            roster.push(k);
        }
        (roster, ids, cap)
    }

    /// The level on the prices standing, the last close's level being what
    /// a degenerate roster holds.
    pub(super) fn index_level_now(&self) -> f64 {
        let (roster, ids, _) = self.index_constituents();
        let previous = if self.index_close > 0.0 { self.index_close } else { INDEX_BASE };
        calculate_market_index(&roster, &ids, previous, self.index_divisor).value
    }

    /// At an open: the divisor, once, so the session opens at the base.
    pub(super) fn index_open(&mut self) {
        if !self.index_on() || self.index_divisor > 0.0 {
            return;
        }
        let (_, _, cap) = self.index_constituents();
        if let Some(d) = rebased_divisor(cap, INDEX_BASE) {
            self.index_divisor = d;
        }
    }

    /// At a close, on the session's last prints: the close level.
    pub(super) fn index_mark_close(&mut self) {
        if !self.index_on() || !(self.index_divisor > 0.0) {
            return;
        }
        self.index_close = self.index_level_now();
    }

    /// Before a listing, a delisting or a change of status: the
    /// constituents' capitalisation and the level, at the prices standing.
    /// `None` with the switch off or before the base is set.
    pub(super) fn index_before_change(&self) -> Option<(f64, f64)> {
        if !self.index_on() || !(self.index_divisor > 0.0) {
            return None;
        }
        let (roster, ids, cap) = self.index_constituents();
        let previous = if self.index_close > 0.0 { self.index_close } else { INDEX_BASE };
        Some((cap, calculate_market_index(&roster, &ids, previous, self.index_divisor).value))
    }

    /// After the change: the divisor that holds the level where it was on
    /// the constituents now standing. Nothing when the capitalisation did
    /// not move, or when nothing is left to divide.
    pub(super) fn index_rebase(&mut self, before: Option<(f64, f64)>) {
        let Some((cap_before, level)) = before else {
            return;
        };
        let (_, _, cap) = self.index_constituents();
        if cap.to_bits() == cap_before.to_bits() {
            return;
        }
        if let Some(d) = rebased_divisor(cap, level) {
            self.index_divisor = d;
        }
    }

    /// The price index (`index_level_listed`): `None` with the switch off.
    ///
    /// The level is `sum(price * shares_outstanding) / divisor` over the
    /// public, solvent names on the prices as they stand. The divisor is set
    /// at the first open so that session opens at
    /// [`crate::market::INDEX_BASE`], and reset by [`Engine::add_company`],
    /// [`Engine::remove_company`] and [`Engine::set_status`] so the level
    /// does not move when the constituents do. A name edited through
    /// [`Engine::companies_mut`] is read as it stands and resets nothing.
    pub fn index_level(&self) -> Option<IndexLevel> {
        if !self.index_on() {
            return None;
        }
        let constituents = self.companies.iter().filter(|c| c.is_public && !c.is_bankrupt).count();
        let divisor = if self.index_divisor > 0.0 { Some(self.index_divisor) } else { None };
        let close = if self.index_close > 0.0 { Some(self.index_close) } else { None };
        let level = if divisor.is_some() { self.index_level_now() } else { INDEX_BASE };
        Some(IndexLevel { level, close, divisor, constituents })
    }

    /// The index's state for the snapshot: `[divisor, close]`, 0.0 for each
    /// not yet set. `None` with the switch off.
    pub fn index_state(&self) -> Option<[f64; 2]> {
        if self.index_on() {
            Some([self.index_divisor, self.index_close])
        } else {
            None
        }
    }

    /// Put the index's state back (a restore). Refused with the switch off,
    /// and for a divisor or level that is negative or not finite.
    pub fn set_index_state(&mut self, state: Option<[f64; 2]>) -> Result<(), String> {
        match state {
            Some(_) if !self.index_on() => Err(
                "this snapshot carries the price index (index_divisor), which only an \
                 engine with index_level_listed on keeps, and this engine's model has it off"
                    .to_string()),
            Some([d, c]) => {
                if !(d.is_finite() && d >= 0.0 && c.is_finite() && c >= 0.0) {
                    return Err(format!(
                        "this snapshot's index_divisor is [{d}, {c}]; the divisor and the close \
                         level are finite and not negative, 0.0 where not yet set"));
                }
                self.index_divisor = d;
                self.index_close = c;
                Ok(())
            }
            None => {
                self.index_divisor = 0.0;
                self.index_close = 0.0;
                Ok(())
            }
        }
    }

    // ── The live VIX (`vix_intraday_live`) ────────────────────────────────

    /// The VIX as published live (`vix_intraday_live`): within a session the
    /// projection of the VIX tonight's close will publish, given the session
    /// so far; outside one, and between a pin and the next tick, the
    /// published VIX. `None` with the switch off.
    pub fn live_vix(&self) -> Option<f64> {
        if self.params.vix_intraday_live == 0.0 {
            return None;
        }
        Some(match self.vix_live {
            Some(v) => v,
            None => self.published_vix(),
        })
    }

    /// The live VIX's projection a session holds, for the snapshot: `None`
    /// outside a session and with the switch off.
    pub fn vix_live_mark(&self) -> Option<f64> {
        if self.params.vix_intraday_live == 0.0 {
            None
        } else {
            self.vix_live
        }
    }

    /// Put the live VIX's projection back (a restore). Refused with the
    /// switch off and for a projection that is not finite and positive.
    pub fn set_vix_live_mark(&mut self, mark: Option<f64>) -> Result<(), String> {
        match mark {
            Some(_) if self.params.vix_intraday_live == 0.0 => Err(
                "this snapshot carries the live VIX (vix_live), which only an engine with \
                 vix_intraday_live on keeps, and this engine's model has it off"
                    .to_string()),
            Some(v) if !(v.is_finite() && v > 0.0) => Err(format!(
                "this snapshot's vix_live is {v}; the live VIX is finite and positive")),
            m => {
                self.vix_live = m;
                Ok(())
            }
        }
    }

    // ── The forecast (`forecast_horizon_sessions`) ────────────────────────

    /// The forecast the last close computed (`forecast_horizon_sessions`):
    /// `None` with the dial at 0.0 and before the first close.
    pub fn forecast(&self) -> Option<&Forecast> {
        if self.params.forecast_horizon_sessions == 0.0 {
            None
        } else {
            self.forecast.as_ref()
        }
    }

    /// Put a forecast back (a restore). Refused with the dial at 0.0 and for
    /// a forecast whose horizon is not the dial's.
    pub fn set_forecast(&mut self, forecast: Option<Forecast>) -> Result<(), String> {
        let h = self.params.forecast_horizon_sessions;
        match forecast {
            Some(_) if h == 0.0 => Err(
                "this snapshot carries a forecast, which only an engine with \
                 forecast_horizon_sessions set keeps, and this engine's is 0"
                    .to_string()),
            Some(f) if f.horizon() as f64 != h => Err(format!(
                "this snapshot's forecast runs {} sessions and this engine's \
                 forecast_horizon_sessions is {h}",
                f.horizon())),
            f => {
                self.forecast = f;
                Ok(())
            }
        }
    }

    /// Compute the forecast on the state standing: at a close for the close
    /// whose day count is `game_day`, or after a pin for the close the
    /// standing forecast was taken at (nothing before the first close).
    /// Nothing with the dial at 0.0.
    pub(super) fn refresh_forecast(&mut self, game_day: Option<i64>) {
        if self.params.forecast_horizon_sessions == 0.0 {
            return;
        }
        let day = match game_day.or_else(|| self.forecast.as_ref().map(|f| f.day)) {
            Some(d) => d,
            None => return,
        };
        // `oil_forecast_path_law`: a close within the closes the paths are
        // held for reads them; the first close past them, and a refresh
        // after a pin, simulates afresh.
        let held = match &self.oil_path_hold {
            Some(h) if game_day.is_some() && day > h.day && day - h.day < OIL_FORECAST_PATHS_HELD => {
                self.oil_path_hold.take()
            }
            _ => None,
        };
        let (forecast, hold) = self.compute_forecast(day, held);
        self.forecast = Some(forecast);
        self.oil_path_hold = hold;
    }

    /// The market's belief over the five phases, `phase_cycle` order: the
    /// cycle nowcast where the model runs one, and otherwise the whole
    /// weight on the published phase.
    fn forecast_belief(&self) -> [f64; 5] {
        if self.params.cycle_nowcast_accuracy != 0.0 {
            let total: f64 = self.cycle_nowcast.iter().sum();
            if total > 0.0 && total.is_finite() {
                return self.cycle_nowcast;
            }
        }
        let phases = crate::economy::cycle::phase_cycle();
        let published = self.published_cycle_phase();
        let mut b = [0.0; 5];
        let k = phases.iter().position(|p| *p == published).unwrap_or(0);
        b[k] = 1.0;
        b
    }

    /// Each phase's exit rate per session, from the cycle's own mean
    /// sojourns: the nowcast's predict step.
    fn forecast_exit_rates(&self) -> [f64; 5] {
        if self.params.cycle_nowcast_accuracy != 0.0 || self.params.corporate_spread_cycle != 0.0 {
            return self.cycle_nowcast_terms.0;
        }
        let (mean, _) = crate::economy::cycle::stationary_phase_shares_for(&self.cycle_spec());
        let mut lambda = [0.0; 5];
        for j in 0..5 {
            lambda[j] = if mean[j] > 0.0 { crate::mathx::min(1.0, 1.0 / mean[j]) } else { 0.0 };
        }
        lambda
    }

    /// The names the forecast follows, with the weights the identity reads.
    fn forecast_names(&self) -> Vec<ForecastName> {
        let mut total = 0.0;
        for c in self.companies.iter() {
            if c.is_public && !c.is_bankrupt && c.stock.market_cap > 0.0 {
                total += c.stock.market_cap;
            }
        }
        if !(total > 0.0) {
            return Vec::new();
        }
        let bases = self.sector_base_variances();
        let mut out = Vec::new();
        for (slot, c) in self.companies.iter().enumerate() {
            if !c.is_public || c.is_bankrupt || c.stock.market_cap <= 0.0 {
                continue;
            }
            let name = NameVariance {
                weight: c.stock.market_cap / total,
                beta: c.stock.beta.unwrap_or(1.0),
                sector: self.sector_keys.iter().position(|k| *k == c.sector).unwrap_or(usize::MAX),
                garch_variance: c.stock.garch_variance,
                market_cap: c.stock.market_cap,
            };
            out.push(ForecastName {
                slot,
                name,
                constants: NameConstants::of(&self.params, &name),
                sector_base: bases.get(slot).copied().unwrap_or(c.stock.garch_variance),
                garch_beta: crate::market::garch::garch_beta_for(&self.params, c.stock.market_cap),
            });
        }
        out
    }

    /// The cycle's volatility regimes the belief weighs: for each phase,
    /// its weight, the level the factor's baseline is scaled by and the VIX
    /// coupling's scale (`market_vol_cycle_ratio`). One regime of weight one
    /// with the dial at 0.0.
    fn forecast_regimes(&self, belief: &[f64; 5], phase_regimes: &[(f64, f64); 5]) -> Vec<(f64, f64, f64)> {
        if self.params.market_vol_cycle_ratio == 0.0 {
            return vec![(1.0, 1.0, 1.0)];
        }
        let mut out: Vec<(f64, f64, f64)> = Vec::new();
        for (j, &w) in belief.iter().enumerate() {
            if !(w > 0.0) {
                continue;
            }
            let (level, scale) = phase_regimes[j];
            match out.iter_mut().find(|r| r.1.to_bits() == level.to_bits() && r.2.to_bits() == scale.to_bits()) {
                Some(r) => r.0 += w,
                None => out.push((w, level, scale)),
            }
        }
        if out.is_empty() {
            out.push((1.0, 1.0, 1.0));
        }
        out
    }

    /// Each phase's level and VIX scale for [`Engine::forecast_regimes`]:
    /// the cycle multiplier's target in the phase, read once a forecast,
    /// since nothing it reads moves over the horizon.
    fn forecast_phase_regimes(&self) -> [(f64, f64); 5] {
        let phases = crate::economy::cycle::phase_cycle();
        let mut out = [(1.0, 1.0); 5];
        if self.params.market_vol_cycle_ratio == 0.0 {
            return out;
        }
        for (j, phase) in phases.iter().enumerate() {
            let l = self.market_vol_cycle_target_log_for(*phase);
            let m = crate::mathx::exp(l);
            out[j] = (m * m, self.market_vol_cycle_vix_scale_at(Some(l)));
        }
        out
    }

    /// The identity on a forecast state: the index's one-session variance
    /// and its names', at the VIX `vix`. The factor's variance is `faces`:
    /// its expectation after an up day and after a down day, which the
    /// lagged wire's two faces read (`down` is the bit where it is known, and
    /// both faces are then the one variance). The names read their average.
    #[allow(clippy::too_many_arguments)]
    fn forecast_identity(
        &self,
        names: &[ForecastName],
        garch: &[f64],
        sector_states: &[f64],
        idio: &[f64],
        faces: (f64, f64),
        vix: f64,
        universe_stress: f64,
        down: Option<bool>,
        economy: &EconomyState,
    ) -> (f64, Vec<f64>) {
        let factor_variance = 0.5 * (faces.0 + faces.1);
        let p = &self.params;
        let k = intraday_variance_factor();
        let target = crate::market::tick::sector_sigma_at(p, economy, self.vix_anchor);
        let sigmas: Vec<f64> = if self.sector_state_on() {
            sector_states.iter().map(|s| if *s > 0.0 { target * crate::mathx::sqrt(*s) } else { target }).collect()
        } else {
            vec![target; self.sector_keys.len()]
        };
        let ratio = vix / self.vix_anchor;
        let rate_scale = if p.jump_vix_coupling == 0.0 {
            1.0
        } else {
            (1.0 - p.jump_vix_coupling) + ((p.jump_vix_coupling * ratio) * ratio)
        };
        let spike = crate::market::tick::crisis_spike_for(p, vix, universe_stress);
        let list: Vec<NameVariance> = names
            .iter()
            .zip(garch.iter())
            .map(|(n, g)| NameVariance { garch_variance: *g, ..n.name })
            .collect();
        let excitations: Vec<f64> = if p.jump_idio_excitation == 0.0 {
            Vec::new()
        } else {
            names.iter().map(|n| self.jump_excitation.get(n.slot).copied().unwrap_or(0.0)).collect()
        };
        let one = |bit: bool, v: f64| {
            index_conditional_variance_terms_with_states(
                p, &list, self.sector_keys.len(), v, &sigmas, &excitations, idio,
                rate_scale, spike, bit, k)
                .total()
        };
        let index = match down {
            Some(bit) => one(bit, factor_variance),
            None if p.market_beta_down_asym_lag == 0.0 => one(false, factor_variance),
            None => 0.5 * (one(false, faces.0) + one(true, faces.1)),
        };
        let per_name = names
            .iter()
            .enumerate()
            .map(|(i, n)| {
                let sigma = if n.name.sector < sigmas.len() { sigmas[n.name.sector] } else { 0.0 };
                let r = idio.get(i).copied().unwrap_or(1.0);
                name_session_variance(
                    p, &n.constants, n.name.beta, garch[i], factor_variance, sigma, r, rate_scale, k)
            })
            .collect();
        (index, per_name)
    }

    /// The policy rate expected at each close `1..=horizon` sessions after
    /// the close whose day count is `day`, meeting by meeting. The next
    /// meeting moves the rate by the change the meeting function makes on the
    /// economy as published (the shadow `policy_anticipation` prices), less
    /// `forecast_policy_shadow_discount` of it. Each meeting after it repeats
    /// `forecast_policy_persistence` of the one before's shadow-driven
    /// change, and every meeting closes `forecast_policy_reversion` of the
    /// gap to `forecast_policy_neutral`. Meetings fall on the calendar's next
    /// date and then at the mean of the ladder's normal cadence. The ladder
    /// is discrete, so these three are a projection fitted on the model's own
    /// held-out histories rather than an iteration of its law.
    /// The day count the next scheduled meeting is held at, for a forecast
    /// taken for `day`: the first close whose timestamp reaches the date on
    /// the calendar, and no earlier than `day + 1`. Its decision is the rate
    /// the close of session `first - 1` sets.
    fn next_meeting_day(&self, day: i64) -> i64 {
        let minutes = 24 * 60;
        let next = self.central_bank.next_meeting_date;
        let first = if next <= 0 { day + 1 } else { (next + minutes - 1) / minutes };
        if first < day + 1 {
            day + 1
        } else {
            first
        }
    }

    /// The session, counted from 0, whose close holds the next scheduled
    /// meeting, as the standing forecast's policy path reads it: `None`
    /// without a forecast (`forecast_horizon_sessions`).
    pub fn next_meeting_session(&self) -> Option<i64> {
        let f = self.forecast()?;
        Some(self.next_meeting_day(f.day) - 1)
    }

    fn forecast_policy_path(&self, day: i64, horizon: usize) -> Vec<f64> {
        let p = &self.params;
        let r0 = self.economy.federal_funds_rate;
        let request = DayAdvanceRequest {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: day + 1,
            timestamp: (day + 1) * 24 * 60,
        };
        let options = self.policy_options(false);
        let shadow = self.shadow_meeting_change(&request, &options);
        let first = self.next_meeting_day(day);
        // The normal cadence is 42 to 55 calendar days, uniform.
        let cal = self.macro_calendar();
        let mut cadence = 0.0;
        for j in 0..14 {
            cadence += cal.scale_days(42 + j) as f64;
        }
        let cadence = cadence / 14.0;
        let last = day + horizon as i64;
        let mut path = vec![r0; horizon];
        let mut rate = r0;
        let mut push = (1.0 - p.forecast_policy_shadow_discount) * shadow;
        let mut j = 0;
        loop {
            let at = first + (j as f64 * cadence).round() as i64;
            if at > last {
                break;
            }
            let mut change = push;
            if p.forecast_policy_reversion != 0.0 {
                change += p.forecast_policy_reversion * (p.forecast_policy_neutral - rate);
            }
            rate += change;
            for slot in path.iter_mut().skip((at - day - 1) as usize) {
                *slot = rate;
            }
            push = if p.forecast_policy_persistence != 0.0 {
                p.forecast_policy_persistence * push
            } else {
                0.0
            };
            j += 1;
        }
        path
    }

    /// The forecast for the close whose day count is `day`, on the state
    /// standing. See `ModelParams::forecast_horizon_sessions`.
    ///
    /// Two tracks run side by side. The first iterates the laws on the
    /// expected VIX: the VIX's own path, its index variance read-back and
    /// the economy the step reads come from it. The second carries the same
    /// states for the variances the forecast reports, with each convex VIX
    /// coupling taken as its expectation over the VIX's spread about the
    /// first track (`forecast_vix_dispersion`, skewed by
    /// `forecast_vix_dispersion_skew`) and the market factor's return
    /// memory's multiplier as its expectation over the memory's own spread.
    /// The first track's VIX is the state's expectation; the published VIX
    /// reported is the published quote's expectation over the same spread
    /// ([`Engine::forecast_published_vix`]).
    fn compute_forecast(&self, day: i64, held: Option<OilPathHold>) -> (Forecast, Option<OilPathHold>) {
        let p = &self.params;
        let horizon = p.forecast_horizon_sessions as usize;
        let phases = crate::economy::cycle::phase_cycle();
        let exits = self.forecast_exit_rates();
        let mut belief = self.forecast_belief();

        // The economy as public: the believed phase, the published growth,
        // and a phase old enough that the step takes no phase-change shock
        // it cannot know of.
        let mut econ = self.economy.clone();
        let mut best = 0;
        for j in 1..5 {
            if belief[j] > belief[best] {
                best = j;
            }
        }
        econ.cycle_phase = phases[best];
        econ.gdp_growth = self.published_gdp_growth();
        if econ.months_in_current_phase < 1.0 {
            econ.months_in_current_phase = 1.0;
        }

        let names = self.forecast_names();
        let phase_regimes = self.forecast_phase_regimes();
        let mut garch: Vec<f64> = names.iter().map(|n| n.name.garch_variance).collect();
        let mut garch_e = garch.clone();
        let mut sector_states: Vec<f64> = if self.sector_state_on() {
            self.sector_variance.iter().map(|s| if *s > 0.0 { *s } else { 1.0 }).collect()
        } else {
            Vec::new()
        };
        let mut idio: Vec<f64> = if self.idio_state_on() {
            names.iter().map(|n| self.idio_ratio_at(n.slot)).collect()
        } else {
            Vec::new()
        };
        let mut mv = self.market_vol;
        let mut mv_e = self.market_vol;
        // Each track's factor variance after an up and a down day; known
        // (and one) at the close the forecast starts from.
        let mut faces = (mv.variance(), mv.variance());
        let mut faces_e = faces;
        let mut slow = self.vix_anchor_slow;
        let mut stress = self.vix_stress_memory;
        let mut fear = self.vix_fear;
        // The host's premium on the VIX target, which the next close reads
        // as it stands, fading on its half-life a close at a time; the floor
        // is held (`daily_inputs` carries it).
        let mut premium = self.vix_target_premium;
        let premium_fade = if self.vix_target_premium_half_life != 0.0 {
            crate::mathx::pow(0.5, 1.0 / self.vix_target_premium_half_life)
        } else {
            1.0
        };
        let mut universe_stress = self.universe_stress;
        let mut down = Some(self.market_vol.prev_day_down());
        let (mut var_oil, mut var_inventory) = (0.0, 0.0);
        // Oil's long factor (`oil_target_drift_sd`): the mean and variance of
        // its log level, so the step reads the log of the level's
        // expectation, `m + v / 2`, which stays where it is with no pull.
        let (mut drift_mean, mut drift_var) = (self.oil_target_drift, 0.0);
        // `oil_forecast_clamp`: the level carriers' log variance about the
        // expected path (the long factor's and the dollar's), and the
        // dollar's own variance, for the clamped expectation the forecast
        // publishes; `oil_forecast_opec_spread` reads them too. Nothing is
        // read with both switches off.
        let (mut carry_var, mut usd_var) = (0.0f64, 0.0f64);
        // Under the long factor a bound's truncation moves the factor, so the
        // expected truncation at one horizon joins the factor's expected path
        // at the next (`oil_expected_log_truncation`).
        let (mut absorb_pending, mut absorb_so_far) = (0.0f64, 0.0f64);
        // `oil_forecast_cycle_growth`: growth's expectation over the cycle's
        // phases, for oil's block, rather than the believed phase held.
        let mut cycle = if p.oil_forecast_cycle_growth != 0.0 && p.oil_forecast_path_law == 0.0 {
            Some(CycleGrowth::new(self, &belief, &econ, day))
        } else {
            None
        };
        // `oil_forecast_path_law`: growth's expectation over the law's own
        // paths, which takes the place of the cycle's above; simulated here,
        // or read from the paths a close within the week simulated.
        let mut paths = if p.oil_forecast_path_law != 0.0 && held.is_none() {
            Some(OilPaths::new(self, &belief, &econ, day))
        } else {
            None
        };
        let mut path_growth: Vec<f64> = Vec::new();
        let mut path_last: Option<(DailyInputs, f64)> = None;
        let policy = self.forecast_policy_path(day, horizon);
        let k_curve = intraday_variance_factor();
        // The spread's shape (`forecast_vix_dispersion_skew`), and the stress
        // memory's sd about its path: the memory's own average of the log
        // VIX's spread, moving with it.
        let shape = if p.forecast_vix_dispersion != 0.0 {
            spread_shape(p.forecast_vix_dispersion_skew)
        } else {
            Vec::new()
        };
        let skewed = p.forecast_vix_dispersion != 0.0 && p.forecast_vix_dispersion_skew != 0.0;
        let mut stress_sd = 0.0;

        let mut out = Forecast {
            day,
            vix: Vec::with_capacity(horizon),
            index_variance: Vec::with_capacity(horizon),
            policy_rate: policy.clone(),
            oil: Vec::with_capacity(horizon),
            name_variance: vec![Vec::new(); self.companies.len()],
        };
        for n in &names {
            out.name_variance[n.slot] = Vec::with_capacity(horizon);
        }

        for (step, &rate) in policy.iter().enumerate() {
            let vix = econ.vix;
            // The log VIX's variance about its expectation after `step`
            // closes, and the VIX whose square is the expected square.
            let spread = self.forecast_vix_log_variance(step);
            // Off zero skew, the spread's nodes: the log of V / vix at each.
            let nodes = if skewed && spread > 0.0 { spread_at(&shape, spread) } else { Vec::new() };
            let vix_sq = if !nodes.is_empty() {
                let second: f64 = nodes.iter().map(|(x, w)| w * crate::mathx::exp(2.0 * x)).sum();
                vix * crate::mathx::sqrt(second)
            } else if spread > 0.0 {
                vix * crate::mathx::exp(0.5 * spread)
            } else {
                vix
            };
            let lev = self.forecast_leverage_factor(step);
            let mut econ_sq = econ.clone();
            econ_sq.vix = vix_sq;

            // The session t + step + 1: the variances the forecast reports,
            // on the expectation track; and the index's variance on the
            // first track, which sizes the session's return the VIX reads.
            let (v_report, per_name) = self.forecast_identity(
                &names, &garch_e, &sector_states, &idio, (faces_e.0 * lev, faces_e.1 * lev), vix_sq,
                universe_stress, down, &econ_sq);
            out.index_variance.push(v_report);
            for (n, v) in names.iter().zip(per_name.iter()) {
                out.name_variance[n.slot].push(*v);
            }
            let (v_session, _) = self.forecast_identity(
                &names, &garch, &sector_states, &idio, faces, vix, universe_stress, down, &econ);

            // Its close. The factor on both tracks: the expected update over
            // the day's factor (four nodes) and the believed phase's regimes.
            let regimes = self.forecast_regimes(&belief, &phase_regimes);
            let (next_mv, next_faces) = self.forecast_factor_close(&mv, &regimes, vix, 0.0, &[]);
            let (next_mv_e, next_faces_e) = self.forecast_factor_close(&mv_e, &regimes, vix, spread, &nodes);

            // Each name's GARCH on both tracks: the expected GJR step on the
            // session's noise.
            if p.garch_cascade_components < 1.0 {
                let coupled = |x: f64| {
                    if p.garch_vix_coupling == 0.0 {
                        1.0
                    } else {
                        let cpl = p.garch_vix_coupling;
                        1.0 - cpl + crate::market::garch::vix_coupled_response(p, cpl, x / self.vix_anchor)
                    }
                };
                let coupled_e = if p.garch_vix_coupling == 0.0 {
                    1.0
                } else if !nodes.is_empty() {
                    spread_expect(&nodes, vix, coupled)
                } else {
                    lognormal_expect(vix, spread, coupled)
                };
                let target = crate::market::tick::sector_sigma_at(p, &econ, self.vix_anchor);
                let target_e = crate::market::tick::sector_sigma_at(p, &econ_sq, self.vix_anchor);
                for (i, n) in names.iter().enumerate() {
                    let state = |t: f64| {
                        if self.sector_state_on() {
                            t * crate::mathx::sqrt(sector_states.get(n.name.sector).copied().unwrap_or(1.0))
                        } else {
                            t
                        }
                    };
                    let r = idio.get(i).copied().unwrap_or(1.0);
                    let base = n.sector_base * coupled(vix);
                    let base_e = if p.garch_vix_coupling == 0.0 {
                        n.sector_base
                    } else {
                        n.sector_base * coupled_e
                    };
                    garch[i] = self.forecast_garch_step(
                        n, garch[i], mv.variance(), state(target), r, base, k_curve);
                    garch_e[i] = self.forecast_garch_step(
                        n, garch_e[i], mv_e.variance() * lev, state(target_e), r, base_e, k_curve);
                }
            }
            // The sector and own-variance ratios revert to one.
            let (ga, gc) = (p.garch_floor_multiple, p.garch_ceiling_multiple);
            if self.sector_state_on() {
                let (a, b) = (p.sector_vol_alpha, p.sector_vol_beta);
                for s in sector_states.iter_mut() {
                    *s = crate::mathx::max(crate::mathx::min((1.0 - a - b) + (a + b) * *s, gc), ga);
                }
            }
            if self.idio_state_on() {
                let (a, b) = (p.idio_vol_alpha, p.idio_vol_beta);
                for r in idio.iter_mut() {
                    *r = crate::mathx::max(crate::mathx::min((1.0 - a - b) + (a + b) * *r, gc), ga);
                }
            }
            // The universe's stress at the close's VIX.
            let from_vix = if vix > p.crisis_vix_threshold { vix - p.crisis_vix_threshold } else { 0.0 };
            let mut from_regime = 0.0;
            for (j, w) in belief.iter().enumerate() {
                from_regime += w * p.regime_stress_points * phases[j].stress_intensity();
            }
            universe_stress = crate::mathx::max(
                crate::mathx::max(from_vix, from_regime), p.universe_stress_decay * universe_stress);
            mv = next_mv;
            mv_e = next_mv_e;
            faces = next_faces;
            faces_e = next_faces_e;
            down = None;

            // The step's read-back, at the VIX before the step.
            let (v_next, _) = self.forecast_identity(
                &names, &garch, &sector_states, &idio, faces, vix, universe_stress, None, &econ);
            let mut scale_bar = 0.0;
            for &(w, _, scale) in &regimes {
                scale_bar += w * scale;
            }
            let implied = vix_from_variance(p.vix_variance_premium, v_next);
            if p.vix_anchor_memory != 0.0 && p.vix_level_identity != 0.0 {
                let anchor = self.vix_anchor * scale_bar;
                let anchor = if p.vix_anchor_centre != 0.0 {
                    anchor * crate::mathx::exp(-p.vix_anchor_centre)
                } else {
                    anchor
                };
                if implied > 0.0 && anchor > 0.0 {
                    let h = p.vix_anchor_memory;
                    let d = crate::mathx::log(implied / anchor);
                    slow = (1.0 - h) * slow + h * d;
                    if p.vix_stress_premium != 0.0 {
                        let hs = p.vix_stress_memory_rate();
                        stress = (1.0 - hs) * stress + hs * d;
                        let after = self.forecast_vix_log_variance(step + 1);
                        stress_sd = (1.0 - hs) * stress_sd + hs * crate::mathx::sqrt(after);
                    }
                }
            }

            // The VIX: the step on the session's return, integrated by the
            // eight nodes, with its draws at their means and the jump's mean.
            let next_day = day + step as i64 + 1;
            let request = DayAdvanceRequest {
                volatility: 1.0,
                active_shocks: &[],
                market_return_pct: 0.0,
                game_day: next_day,
                timestamp: next_day * 24 * 60,
            };
            let mut inputs = self.daily_inputs(&request, 0.0, v_next, slow, scale_bar);
            inputs.vix_anchor_level = self.vix_anchor * scale_bar;
            inputs.vix_target_premium = premium;
            if premium != 0.0 {
                premium *= premium_fade;
                if premium.abs() < crate::engine::VIX_TARGET_PREMIUM_CLEARED_UNDER {
                    premium = 0.0;
                }
            }
            inputs.vix_implied_from_market = if p.vix_level_identity != 0.0 {
                implied
            } else if p.vix_realised_vol_weight == 0.0 {
                0.0
            } else {
                p.market_vol_vix_anchor * mv.sigma_daily() / p.market_factor_sigma
            };
            // The fear memory, advanced on the step's inputs at the expected
            // VIX, as the close advances it (`vix_fear_uptake`).
            if p.vix_fear_uptake != 0.0 {
                fear = crate::economy::daily::advance_vix_fear(fear, &inputs, vix, p.vix_fear_half_life);
                inputs.vix_fear = fear;
            }
            let sd_ret = 100.0 * crate::mathx::sqrt(crate::mathx::max(0.0, v_session));
            let mut vix_next = 0.0;
            for &z in live_mark_nodes().iter() {
                let mut node = inputs;
                node.market_day_return_pct = z * sd_ret;
                let stepped = crate::economy::daily::vix_close(
                    &econ, &node, 0.0, &mut crate::economy::daily::MeanDraws);
                vix_next += stepped / 8.0 + self.forecast_vix_jump_mean(&econ, &node) / 8.0;
            }

            // The rest of the economy: the step itself with its draws at
            // their means, then the VIX above, the policy path, and oil's
            // expected inventory push, OPEC decision and the dollar's
            // expected safe-haven drift over the VIX's spread.
            let opec_day = econ.oil_last_opec_day;
            let oil_before = econ.oil_price;
            let kappa = p.oil_inventory_reversion;
            let sd_before = crate::mathx::sqrt(var_inventory);
            // Inventory's noise variance a session: the standing 0.25, or
            // `oil_inventory_noise_sd` squared. A branch, so the shipped
            // arithmetic is kept to the bit.
            let inventory_var = if p.oil_inventory_noise_sd == 0.0 {
                0.25
            } else {
                p.oil_inventory_noise_sd * p.oil_inventory_noise_sd
            };
            let var_after = (1.0 - kappa) * (1.0 - kappa) * var_inventory + inventory_var;
            // The convenience premium (`oil_convenience_yield`) is convex in
            // inventory, so the step reads its expectation over the
            // inventory's spread, before the step and after it, rather than
            // its value on the expected path: the expected price is then the
            // premium-free path times the premium's expectation.
            if p.oil_convenience_yield != 0.0 {
                inputs.oil_convenience_inventory_var_before = var_inventory;
                inputs.oil_convenience_inventory_var = var_after;
            }
            if p.oil_target_drift_sd != 0.0 {
                let sigma = p.oil_target_drift_sd;
                let pull = 1.0 - p.oil_target_drift_reversion;
                let before = drift_mean + 0.5 * drift_var;
                drift_mean = pull * drift_mean - 0.5 * sigma * sigma + absorb_pending;
                absorb_pending = 0.0;
                drift_var = pull * pull * drift_var + sigma * sigma;
                inputs.oil_target_drift = Some((before, drift_mean + 0.5 * drift_var));
            }
            if let Some(c) = &cycle {
                inputs.oil_growth = Some(c.growth());
            }
            if let Some(h) = &held {
                inputs.oil_growth = h.growth.get((day - h.day) as usize + step).copied();
            }
            if let Some(s) = &paths {
                let g = s.growth();
                path_growth.push(g);
                inputs.oil_growth = Some(g);
            }
            let mut next = update_economy_daily(&econ, &inputs, &mut crate::economy::daily::MeanDraws);
            if let Some(c) = cycle.as_mut() {
                c.step(&inputs, request.volatility, v_session, request.timestamp);
            }
            if let Some(s) = paths.as_mut() {
                s.step(self, &inputs, v_session, request.timestamp);
                path_last = Some((inputs, v_session));
            }
            var_inventory = var_after;
            let sd_inv = crate::mathx::sqrt(var_inventory);
            let gap = |level: f64, sd: f64| {
                crate::economy::daily::inventory_pressure_expected(level, sd)
                    - crate::economy::daily::inventory_pressure(level)
            };
            // The step read the pressure at the inventory's expected path;
            // its expectation over inventory's spread is what the price
            // expects. Under `oil_inventory_level_gain` the step read the
            // change in the pressure, so the gap is the change in the gaps.
            let mut pushed = if p.oil_inventory_level_gain == 0.0 {
                gap(next.oil_inventory_level, sd_inv)
            } else {
                p.oil_inventory_level_gain
                    * (gap(next.oil_inventory_level, sd_inv) - gap(econ.oil_inventory_level, sd_before))
            };
            next.oil_price += pushed;
            if next.oil_last_opec_day != opec_day {
                // `oil_forecast_opec_spread`: the decision over the price's
                // whole spread at the step's start (the clamp's log variance,
                // every part as it stood before this step), read lognormal.
                // A branch, so 0.0 keeps the short factor's normal to the bit.
                let opec = if p.oil_forecast_opec_spread != 0.0 {
                    let log_var = if oil_before > 0.0 { var_oil / (oil_before * oil_before) } else { 0.0 }
                        + carry_var
                        + p.oil_convenience_yield * p.oil_convenience_yield * (sd_before * sd_before) / (50.0 * 50.0)
                        + p.oil_dollar_elasticity * p.oil_dollar_elasticity * usd_var / (100.0 * 100.0);
                    crate::economy::daily::opec_expected_impact_lognormal(oil_before, log_var, p.oil_opec_symmetry)
                } else {
                    crate::economy::daily::opec_expected_impact(
                        oil_before, crate::mathx::sqrt(var_oil), p.oil_opec_symmetry)
                };
                next.oil_price += opec;
                pushed += opec;
            }
            // Under `oil_pushes_in_target` the expected pushes join the part
            // of the price that decays at 0.03, as the step's own do.
            if p.oil_pushes_in_target != 0.0 {
                next.oil_push_level += pushed;
            }
            // Oil's own spread about its expectation, for the OPEC rule's
            // expected decision: the law's reversion and noise.
            // A branch, so the standing law keeps its arithmetic to the bit.
            var_oil = if p.oil_mean_reversion == 0.0 && p.oil_noise_sd == 0.0 && p.oil_noise_log_sd == 0.0 {
                0.97 * 0.97 * var_oil + 4.0
            } else {
                let keep = if p.oil_mean_reversion == 0.0 { 0.97 } else { 1.0 - p.oil_mean_reversion };
                // Under `oil_noise_log_sd` the noise is a share of the
                // price, read at the price the step started from.
                let noise = if p.oil_noise_log_sd != 0.0 {
                    p.oil_noise_log_sd * oil_before
                } else if p.oil_noise_sd == 0.0 {
                    2.0
                } else {
                    p.oil_noise_sd
                };
                keep * keep * var_oil + noise * noise
            };
            if spread > 0.0 {
                let k = p.usd_crisis_vix_threshold;
                let at_mean = if vix > k { vix - k } else { 0.0 };
                let excess = if !nodes.is_empty() {
                    spread_expect(&nodes, vix, |x| if x > k { x - k } else { 0.0 })
                } else {
                    lognormal_excess(vix, spread, k)
                };
                let haven = crate::economy::daily::usd_safe_haven_gain(p.usd_safe_haven_gain) * (excess - at_mean);
                // Under `oil_dollar_elasticity` the law rescales the oil
                // price's reverting part by the day's whole dollar move, the
                // bid included, so the expected bid's excess over the bid at
                // the expected VIX moves oil by the same ratio.
                if p.oil_dollar_elasticity != 0.0 && next.usd_index > 0.0 {
                    let push = if p.oil_pushes_in_target != 0.0 { next.oil_push_level } else { 0.0 };
                    let ratio = crate::mathx::pow((next.usd_index + haven) / next.usd_index, -p.oil_dollar_elasticity);
                    next.oil_price = (next.oil_price - push) * ratio + push;
                }
                next.usd_index += haven;
                // Under `usd_mean_reversion` the expected bid joins the part of
                // the dollar that decays at 0.02, as the step's own does.
                if p.usd_mean_reversion != 0.0 {
                    next.usd_haven_level += haven;
                }
            }
            next.vix = vix_next;
            next.federal_funds_rate = rate;
            econ = next;

            out.vix.push(self.forecast_published_vix(&shape, econ.vix, stress, stress_sd, step + 1));
            // `oil_forecast_clamp`: publish the expectation truncated at the
            // clamps over the price's spread; the recursion keeps stepping
            // the unclamped expectation, so the clamp is read once.
            // The carriers' variances step under the clamp and under
            // `oil_forecast_opec_spread`, which reads them at the next step.
            if p.oil_forecast_clamp != 0.0 || p.oil_forecast_opec_spread != 0.0 {
                let sigma = p.oil_target_drift_sd;
                let pull = 1.0 - p.oil_target_drift_reversion;
                carry_var = pull * pull * carry_var + sigma * sigma;
                // The dollar's own spread, on its law: the standing 0.02 and
                // 0.3, or `usd_mean_reversion` and `usd_noise_sd`.
                let usd_keep = 1.0 - if p.usd_mean_reversion == 0.0 { 0.02 } else { p.usd_mean_reversion };
                let usd_noise = if p.usd_noise_sd == 0.0 { 0.3 } else { p.usd_noise_sd };
                usd_var = usd_keep * usd_keep * usd_var + usd_noise * usd_noise;
            }
            if p.oil_forecast_clamp != 0.0 {
                let s = econ.oil_price;
                let kappa_c = p.oil_convenience_yield;
                let inventory_log_var = var_inventory / (50.0 * 50.0);
                let log_var = carry_var
                    + kappa_c * kappa_c * inventory_log_var
                    + p.oil_dollar_elasticity * p.oil_dollar_elasticity * usd_var / (100.0 * 100.0);
                let bounds = crate::economy::daily::oil_bounds(p.oil_price_floor, p.oil_price_ceiling);
                // The price at this horizon rests on a bound it reaches; under
                // the long factor that truncation then moves the factor, which
                // the next step's expected path carries.
                // Read as lognormal: the price's spread is right-skewed. Its log
                // variance is the short factor's (in dollars, over the price)
                // and the carriers'. The marginal's expected truncation at a
                // horizon is the whole of it to that horizon, so the factor's
                // path takes its change.
                let log_total = if s > 0.0 { var_oil / (s * s) + log_var } else { 0.0 };
                if p.oil_target_drift_sd != 0.0 {
                    let to_here = crate::economy::daily::oil_lognormal_log_truncation(s, log_total, bounds);
                    absorb_pending = to_here - absorb_so_far;
                    absorb_so_far = to_here;
                }
                out.oil.push(crate::economy::daily::oil_lognormal_censored(s, log_total, bounds));
            } else {
                out.oil.push(econ.oil_price);
            }

            // The belief, one session on.
            let mut pred = [0.0; 5];
            for (j, (&lam, &pj)) in exits.iter().zip(belief.iter()).enumerate() {
                pred[j] += pj * (1.0 - lam);
                pred[(j + 1) % 5] += pj * lam;
            }
            belief = pred;
        }
        // The paths simulated here run on past the horizon for the closes
        // that read them later in the week, on the last session's inputs
        // with the days moved on.
        let hold = match (paths.as_mut(), path_last) {
            (Some(s), Some((last, variance))) => {
                for k in 1..OIL_FORECAST_PATHS_HELD {
                    path_growth.push(s.growth());
                    let mut inputs = last;
                    inputs.game_day += k;
                    s.step(self, &inputs, variance, inputs.game_day * 24 * 60);
                }
                Some(OilPathHold { day, growth: path_growth })
            }
            _ => held,
        };
        (out, hold)
    }

    /// The published VIX expected `closes` closes ahead, from the first
    /// track's VIX `vix` and stress memory `stress`. The quote is the state
    /// times `exp(premium)`, the premium a capped hinge in the stress memory
    /// (`vix_stress_premium`), so its expectation is not the quote at the
    /// track's state. With `forecast_vix_dispersion` set it is taken over the
    /// VIX's spread after these closes ([`spread_shape`], [`spread_at`]),
    /// the first track's VIX being the state's mean, and the stress memory
    /// at each node `stress_sd` of its own sds from `stress`, the same number
    /// as the VIX's: the memory is an average of the VIX's log excursions,
    /// so it moves with them, and `stress_sd` is that average of the VIX's
    /// spread. With the dial or the premium at 0.0 it is the quote at the
    /// track's state.
    fn forecast_published_vix(&self, shape: &[(f64, f64)], vix: f64, stress: f64, stress_sd: f64,
                              closes: usize) -> f64 {
        let var = self.forecast_vix_log_variance(closes);
        if self.params.vix_stress_premium == 0.0 || shape.is_empty() || !(var > 0.0) {
            return self.published_vix_at(vix, stress);
        }
        let nodes = spread_at(shape, var);
        let mut total = 0.0;
        for (&(x, w), &(z, _)) in nodes.iter().zip(shape.iter()) {
            total += w * self.published_vix_at(vix * crate::mathx::exp(x), stress + stress_sd * z);
        }
        total
    }

    /// The log VIX's variance about the forecast after `closes` closes:
    /// `sd^2 * (1 - 0.5^(2 closes / half-life))` under
    /// `forecast_vix_dispersion`, its long-horizon level from the first close
    /// at a half-life of 0.0, and 0.0 with the dial off.
    fn forecast_vix_log_variance(&self, closes: usize) -> f64 {
        let p = &self.params;
        let sd = p.forecast_vix_dispersion;
        if sd == 0.0 || closes == 0 {
            return 0.0;
        }
        let hl = p.forecast_vix_dispersion_half_life;
        let share = if hl == 0.0 {
            1.0
        } else {
            1.0 - crate::mathx::pow(0.5, 2.0 * closes as f64 / hl)
        };
        sd * sd * share
    }

    /// The expected over the typical multiplier the market factor's return
    /// memory (`market_vol_leverage`) applies `closes` closes on: the memory
    /// is a sum of the days' standardised factors, so its spread about its
    /// expectation grows to its stationary spread `s` as `1 - phi^(2 closes)`,
    /// and `E[exp(k l)]` is `exp(k E[l] + k^2 var / 2)`. 1.0 with the memory
    /// off.
    fn forecast_leverage_factor(&self, closes: usize) -> f64 {
        let p = &self.params;
        if p.market_vol_leverage == 0.0 || closes == 0 {
            return 1.0;
        }
        let k = p.market_vol_leverage;
        let phi = crate::mathx::pow(0.5, 1.0 / p.market_vol_leverage_half_life);
        let a = p.market_vol_leverage_down;
        let var_u = 0.5 * (1.0 + (1.0 - a) * (1.0 - a)) - a * a / (2.0 * std::f64::consts::PI);
        let s2 = (1.0 - phi) / (1.0 + phi) * var_u;
        let grown = 1.0 - crate::mathx::pow(phi, 2.0 * closes as f64);
        crate::mathx::exp(0.5 * k * k * s2 * grown)
    }

    /// One close of the market factor's variance state in expectation: the
    /// update over the four nodes of the day's factor and each believed
    /// regime, at the VIX `vix`. With `spread` off zero the target's VIX
    /// coupling is taken as its expectation over the VIX's spread of that log
    /// variance (a lognormal, or `nodes` when the spread is skewed), by
    /// scaling the regime's level by the ratio of the expected coupling to
    /// the coupling at the expected VIX.
    fn forecast_factor_close(
        &self,
        mv: &MarketVarianceState,
        regimes: &[(f64, f64, f64)],
        vix: f64,
        spread: f64,
        nodes: &[(f64, f64)],
    ) -> (MarketVarianceState, (f64, f64)) {
        let p = &self.params;
        let sd_f = crate::mathx::sqrt(crate::mathx::max(0.0, mv.variance()));
        // The VIX the close's target reads: the smoothed one, as
        // `close_day_scaled` steps it.
        let smoothed = if p.market_vol_vix_smooth == 0.0 {
            vix
        } else {
            let alpha = 2.0 / (p.market_vol_vix_smooth + 1.0);
            let prev = mv.snapshot().5;
            let prev = if prev < 0.0 { vix } else { prev };
            prev + alpha * (vix - prev)
        };
        let c = p.market_vol_vix_coupling;
        let c_slow = if p.market_vol_slow_vix_damp == 0.0 { c } else { c * (1.0 - p.market_vol_slow_vix_damp) };
        let w_slow = p.market_vol_slow_weight;
        let (mut variance, mut fast, mut slow_part, mut lev) = (0.0, 0.0, 0.0, 0.0);
        let (mut up, mut down) = (0.0, 0.0);
        let mut smoothed_out = -1.0;
        for &(w, level, scale) in regimes {
            let denominator = self.vix_anchor * scale;
            let level = if spread > 0.0 && c != 0.0 {
                let at = crate::market::factor_vol::vix_response(p, smoothed / denominator);
                let response = |x: f64| crate::market::factor_vol::vix_response(p, x / denominator);
                let expected = if nodes.is_empty() {
                    lognormal_expect(smoothed, spread, response)
                } else {
                    spread_expect(nodes, smoothed, response)
                };
                let ratio_fast = (1.0 - c + c * expected) / (1.0 - c + c * at);
                let ratio_slow = (1.0 - c_slow + c_slow * expected) / (1.0 - c_slow + c_slow * at);
                level * ((1.0 - w_slow) * ratio_fast + w_slow * ratio_slow)
            } else {
                level
            };
            for &z in FACTOR_NODES.iter() {
                let mut s = mv.with_day_factor(z * sd_f);
                let _ = s.close_day_scaled(p, denominator, vix, level);
                let snap = s.snapshot();
                variance += w / 4.0 * snap.0;
                // Each face is the two nodes of its sign.
                if z < 0.0 {
                    down += w / 2.0 * snap.0;
                } else {
                    up += w / 2.0 * snap.0;
                }
                fast += w / 4.0 * snap.2;
                slow_part += w / 4.0 * snap.3;
                // The smoothed VIX reads the VIX alone, the same on every node.
                smoothed_out = snap.5;
                lev += w / 4.0 * s.leverage_memory();
            }
        }
        let mut next = MarketVarianceState::restore_with_components(
            variance, 0.0, fast, slow_part, 0.0, smoothed_out);
        next.set_leverage_memory(lev);
        (next, (up, down))
    }

    /// One name's GARCH variance one close on, in expectation: the GJR
    /// update averaged over the session's noise at plus and minus its sd,
    /// which is exact for the update's quadratic and its sign asymmetry.
    #[allow(clippy::too_many_arguments)]
    fn forecast_garch_step(
        &self,
        n: &ForecastName,
        h: f64,
        factor_variance: f64,
        sector_sigma: f64,
        idio_ratio: f64,
        base: f64,
        k: f64,
    ) -> f64 {
        let p = &self.params;
        let noise = name_noise_variance_scaled(
            p, &n.constants, n.name.beta, h, factor_variance, sector_sigma, idio_ratio, k);
        let eps = crate::mathx::sqrt(crate::mathx::max(0.0, noise));
        let up = crate::market::garch::update_garch_variance_for(p, n.garch_beta, h, eps, base);
        let dn = crate::market::garch::update_garch_variance_for(p, n.garch_beta, h, -eps, base);
        0.5 * (up + dn)
    }

    /// The mean of the VIX's own jump on the step `inputs` describes:
    /// its arrival rate times its mean size, as `vix_and_yields` draws it.
    fn forecast_vix_jump_mean(&self, economy: &EconomyState, inputs: &DailyInputs) -> f64 {
        if inputs.vix_jump_intensity == 0.0 && inputs.vix_jump_return_intensity == 0.0 {
            return 0.0;
        }
        let clamp = inputs.vix_return_clamp;
        let ret = crate::mathx::max(-clamp, crate::mathx::min(clamp, inputs.market_day_return_pct));
        let rate = if inputs.vix_jump_return_intensity != 0.0 {
            inputs.vix_jump_intensity + inputs.vix_jump_return_intensity * crate::mathx::max(0.0, -ret)
        } else {
            inputs.vix_jump_intensity
        };
        let innovation_on = inputs.vix_innovation_sigma != 0.0 || inputs.vix_innovation_return_sigma != 0.0;
        let size = if inputs.vix_jump_level_scale != 0.0 {
            let unit = if innovation_on {
                let s0 = inputs.vix_innovation_sigma;
                let sr = inputs.vix_innovation_return_sigma * ret;
                economy.vix * crate::mathx::sqrt(s0 * s0 + sr * sr)
            } else {
                economy.vix
            };
            inputs.vix_jump_level_scale * unit
        } else {
            inputs.vix_jump_scale
        };
        crate::mathx::min(1.0, rate / 252.0) * size
    }
}


/// The first day of the economy's long run for the forecast's cycle
/// (`oil_forecast_cycle_growth`), on the run's own day numbering: far past
/// any session a run reaches, so the keyed draws the run takes by day are
/// not the long run's, beside the keys and generators of its own.
const CYCLE_AGE_LAW_ORIGIN: i64 = 1 << 34;

/// The long run's warm-up and its length, in days. Precision, not a value
/// of the model: the run starts from the close's state, which the warm-up
/// forgets, and the age law is its occupancy over the rest.
const CYCLE_AGE_LAW_WARM_DAYS: i64 = 252 * 50;
const CYCLE_AGE_LAW_DAYS: i64 = 252 * 10_000;
/// The long run the oil forecast's paths draw their start from
/// (`oil_forecast_path_law`, [`Engine::oil_paths_start_law`]): a tenth of
/// the cycle's, for the paths' cost. Precision, as above.
const OIL_PATHS_AGE_LAW_DAYS: i64 = 252 * 1_000;

/// Draws at the middle of their range for a projected meeting: a uniform
/// of one half and a normal of zero, as the market's shadow meeting reads.
struct MidDraws;

impl crate::rng::Rng for MidDraws {
    fn next_f64(&mut self) -> f64 {
        0.5
    }
    fn next_normal(&mut self) -> f64 {
        0.0
    }
}

impl Engine {
    /// The engine the cycle's long run is played on ([`Engine::cycle_age_law`],
    /// [`Engine::oil_paths_start_law`]): a copy with every generator moved
    /// to the age law's own, the economy alone, and the absolute clocks
    /// moved to the run's origin.
    fn age_law_engine(&self, day: i64) -> Engine {
        let tag = crate::rng::AGE_LAW_TAG;
        let root = self.root_seed;
        let mut e = self.clone();
        e.market_rng = GameRng::surgery(root, stream::MARKET, tag);
        e.economy_rng = GameRng::surgery(root, stream::ECONOMY, tag);
        e.external_rng = GameRng::surgery(root, stream::EXTERNAL, tag);
        e.jump_rng = GameRng::surgery(root, stream::JUMPS, tag);
        e.volume_rng = GameRng::surgery(root, stream::VOLUME, tag);
        e.volume_idio_rng = GameRng::surgery(root, stream::VOLUME_IDIO, tag);
        e.news_rng = GameRng::surgery(root, stream::NEWS, tag);
        e.overnight_rng = GameRng::surgery(root, stream::OVERNIGHT, tag);
        e.market_vol_level_rng = GameRng::surgery(root, stream::MARKET_VOL_LEVEL, tag);
        e.crisis_epicentre_rng = GameRng::surgery(root, stream::CRISIS_EPICENTRE, tag);
        e.cycle_nowcast_rng = GameRng::surgery(root, stream::CYCLE_NOWCAST, tag);
        e.earnings_key = crate::rng::age_law_key(self.earnings_key);
        e.skew_noise_key = crate::rng::age_law_key(self.skew_noise_key);
        e.oil_drift_key = crate::rng::age_law_key(self.oil_drift_key);
        e.core_trend_key = crate::rng::age_law_key(self.core_trend_key);
        e.spread_noise_key = crate::rng::age_law_key(self.spread_noise_key);
        e.term_premium_key = crate::rng::age_law_key(self.term_premium_key);
        e.cycle_publication.key = crate::rng::age_law_key(self.cycle_publication.key);
        e.economy_alone = true;
        // The absolute clocks move with the days, as the burn-in's do.
        let shift = CYCLE_AGE_LAW_ORIGIN - day;
        e.central_bank.next_meeting_date += shift * 24 * 60;
        e.central_bank.last_meeting_date += shift * 24 * 60;
        e.economy.oil_last_opec_day += shift;
        e
    }

    /// Each phase's stationary age law under the FULL law, for the start of
    /// the forecast's cycle (`oil_forecast_cycle_growth`): the hazard and the
    /// ladder on the economy's own state, as the law transitions. There is no
    /// closed form ([`crate::economy::cycle`], the stationary law), so it is
    /// read as the occupancy of the economy's own long run: a copy of this
    /// engine run alone, as before day zero (the cycle reads
    /// `cycle_equity_hazard_opening` for the index it does not have), with
    /// every generator and counter key its own (`rng::AGE_LAW_TAG`) and its
    /// days numbered from [`CYCLE_AGE_LAW_ORIGIN`]. Per phase, `phase_cycle`
    /// order, in bins of a month: each bin's mass-weighted age in completed
    /// days and its mass. Bins under a billionth are left out. Worked out
    /// at the first forecast, from the state it stands on, once per seed and
    /// model, and kept for the process; nothing the run reads is touched.
    /// An engine restored from a snapshot works it out from the state it
    /// restores, so its forecast can differ from the original's in the long
    /// run's sampling.
    fn cycle_age_law(&self, day: i64) -> Vec<Vec<(f64, f64)>> {
        type Key = (u64, String);
        static MEMO: std::sync::Mutex<Vec<(Key, Vec<Vec<(f64, f64)>>)>> = std::sync::Mutex::new(Vec::new());
        let key: Key = (self.root_seed, self.model_fingerprint().to_string());
        if let Some(v) = MEMO.lock().ok().and_then(|m| m.iter().find(|(k, _)| *k == key).map(|(_, v)| v.clone())) {
            return v;
        }
        let mut e = self.age_law_engine(day);
        let spec = self.cycle_spec();
        let phases = crate::economy::cycle::phase_cycle();
        let mut counts: Vec<Vec<f64>> = vec![Vec::new(); 5];
        for j in 1..=CYCLE_AGE_LAW_WARM_DAYS + CYCLE_AGE_LAW_DAYS {
            let d = CYCLE_AGE_LAW_ORIGIN + j;
            e.advance_day(&DayAdvanceRequest {
                volatility: 1.0,
                active_shocks: &[],
                market_return_pct: 0.0,
                game_day: d,
                timestamp: d * 24 * 60,
            });
            if j > CYCLE_AGE_LAW_WARM_DAYS {
                let k = phases.iter().position(|ph| *ph == e.economy.cycle_phase).unwrap_or(0);
                let a = crate::mathx::max(0.0, (e.economy.months_in_current_phase * spec.month_days).round()) as usize;
                if counts[k].len() <= a {
                    counts[k].resize(a + 1, 0.0);
                }
                counts[k][a] += 1.0;
            }
        }
        let month = crate::mathx::max(1.0, spec.month_days.round()) as usize;
        let mut out = Vec::with_capacity(5);
        for c in &counts {
            let total: f64 = c.iter().sum();
            let mut bins: Vec<(f64, f64)> = Vec::new();
            for chunk in c.chunks(month).enumerate() {
                let (b, ws) = chunk;
                let (mut mass, mut age) = (0.0, 0.0);
                for (i, w) in ws.iter().enumerate() {
                    mass += w;
                    age += w * (b * month + i) as f64;
                }
                if total > 0.0 && mass / total > 1e-9 {
                    bins.push((age / mass, mass / total));
                }
            }
            out.push(bins);
        }
        if let Ok(mut m) = MEMO.lock() {
            if m.len() > 8 {
                m.clear();
            }
            m.push((key, out.clone()));
        }
        out
    }

    /// The start's law for the oil forecast's paths
    /// (`oil_forecast_path_law`): [`Engine::cycle_age_law`]'s occupancy, on
    /// a long run of [`OIL_PATHS_AGE_LAW_DAYS`], with each bin's spread of
    /// growth about the published figure, `gdp_growth - published`, at
    /// every session of the bin. A path drawn into a bin starts at the
    /// close's published growth plus one of its bin's differences: growth
    /// as the law spreads it about what is public, in that phase at that
    /// age. Per phase, `phase_cycle` order: each bin's mass-weighted age in
    /// completed days, its mass, and its differences. Kept for the process
    /// as the age law is.
    fn oil_paths_start_law(&self, day: i64) -> std::sync::Arc<OilStartLaw> {
        type Key = (u64, String);
        static MEMO: std::sync::Mutex<Vec<(Key, std::sync::Arc<OilStartLaw>)>> = std::sync::Mutex::new(Vec::new());
        let key: Key = (self.root_seed, self.model_fingerprint().to_string());
        if let Some(v) = MEMO.lock().ok().and_then(|m| m.iter().find(|(k, _)| *k == key).map(|(_, v)| v.clone())) {
            return v;
        }
        let mut e = self.age_law_engine(day);
        let spec = self.cycle_spec();
        let phases = crate::economy::cycle::phase_cycle();
        let month = crate::mathx::max(1.0, spec.month_days.round()) as usize;
        // Per phase and bin: the mass, the summed age, the differences.
        let mut bins: Vec<Vec<(f64, f64, Vec<f64>)>> = vec![Vec::new(); 5];
        for j in 1..=CYCLE_AGE_LAW_WARM_DAYS + OIL_PATHS_AGE_LAW_DAYS {
            let d = CYCLE_AGE_LAW_ORIGIN + j;
            e.advance_day(&DayAdvanceRequest {
                volatility: 1.0,
                active_shocks: &[],
                market_return_pct: 0.0,
                game_day: d,
                timestamp: d * 24 * 60,
            });
            if j > CYCLE_AGE_LAW_WARM_DAYS {
                let k = phases.iter().position(|ph| *ph == e.economy.cycle_phase).unwrap_or(0);
                let a = crate::mathx::max(0.0, (e.economy.months_in_current_phase * spec.month_days).round()) as usize;
                let b = a / month;
                if bins[k].len() <= b {
                    bins[k].resize(b + 1, (0.0, 0.0, Vec::new()));
                }
                let bin = &mut bins[k][b];
                bin.0 += 1.0;
                bin.1 += a as f64;
                bin.2.push(e.economy.gdp_growth - e.published_gdp_growth());
            }
        }
        let mut out: OilStartLaw = Vec::with_capacity(5);
        for c in bins {
            let total: f64 = c.iter().map(|b| b.0).sum();
            let mut kept = Vec::new();
            for (mass, age, diffs) in c {
                if total > 0.0 && mass / total > 1e-9 {
                    kept.push((age / mass, mass / total, diffs));
                }
            }
            out.push(kept);
        }
        let out = std::sync::Arc::new(out);
        if let Ok(mut m) = MEMO.lock() {
            if m.len() > 8 {
                m.clear();
            }
            m.push((key, out.clone()));
        }
        out
    }
}

/// [`Engine::oil_paths_start_law`]'s law: per phase, each bin's age, mass
/// and differences of growth from the published figure.
type OilStartLaw = Vec<Vec<(f64, f64, Vec<f64>)>>;

/// One phase's own economy in the forecast's cycle
/// (`oil_forecast_cycle_growth`): the state the law steps in that phase,
/// the central bank that meets on it, and the variance of the market's gap
/// about the state's (`spread_equity_gap`).
#[derive(Clone)]
struct CycleTrack {
    economy: EconomyState,
    bank: crate::economy::CentralBankState,
    gap_var: f64,
}

impl CycleTrack {
    /// Mass entering the phase joins its state at the mass-weighted mean:
    /// every continuous field, and the gap's variance as a mixture's. The
    /// phase, its clock, the OPEC day and the central bank are the
    /// receiving phase's.
    fn join(&mut self, w: f64, from: &CycleTrack, wf: f64) {
        let t = w + wf;
        if !(t > 0.0) {
            return;
        }
        let (a, b) = (w / t, wf / t);
        let (g0, g1) = (self.economy.spread_equity_gap, from.economy.spread_equity_gap);
        let g = a * g0 + b * g1;
        self.gap_var = a * (self.gap_var + g0 * g0) + b * (from.gap_var + g1 * g1) - g * g;
        if self.gap_var < 0.0 {
            self.gap_var = 0.0;
        }
        let x = &mut self.economy;
        let y = &from.economy;
        macro_rules! mix {
            ($($f:ident),* $(,)?) => { $( x.$f = a * x.$f + b * y.$f; )* };
        }
        mix!(federal_funds_rate, prime_rate, corporate_bond_yield, treasury_yield_10y, treasury_yield_2y,
             mortgage_rate_30y, cpi, inflation_rate, core_inflation, gdp_growth, gdp, unemployment_rate,
             jobs_created, labor_force_participation, usd_index, oil_price, gold_price, copper_price,
             housing_index, home_starts_monthly, housing_transaction_volume, long_term_unemployment_rate,
             structural_unemployment, consumer_confidence, business_confidence, fear_greed_index, vix,
             tariff_rate, trade_balance, oil_inventory_level, wage_growth, previous_day_market_return,
             rolling_market_return_30d, earnings_cycle, vix_feedback, intermeeting_return, fed_put,
             fed_put_owed, fed_put_mcap_prev, spread_equity_gap, spread_priced_gap, earnings_anticipation,
             earnings_lookthrough, unemployment_impulse, oil_push_level, usd_haven_level,
             oil_inflation_level, zlb_unmet, oil_bound_log_ratio, qe_pe_boost, qe_assets_ratio,
             fiscal_stimulus, government_debt_to_gdp, phase_gdp_target, recession_probability);
        for (u, v) in x.gdp_trend.iter_mut().zip(y.gdp_trend.iter()) {
            *u = a * *u + b * *v;
        }
        if let (Some(u), Some(v)) = (x.market_pe, y.market_pe) {
            x.market_pe = Some(a * u + b * v);
        }
    }
}

/// Growth's expectation over the cycle's phases for oil's forecast
/// (`oil_forecast_cycle_growth`), on the law's own transitions. Each phase
/// holds cohorts of the belief's mass, each with its own growth and age:
/// the mass on a phase at the start, spread over the phase's stationary age
/// law under the full law (the age is not public;
/// [`Engine::cycle_age_law`], in months), and the mass that enters a phase
/// over the horizon, a fresh sojourn. Each phase also carries its own
/// economy ([`CycleTrack`]), from the close's: at each close the law steps
/// it in that phase, its draws at their means, at its cohorts' mean growth;
/// its central bank meets on it by the law's rule, the draws at the middle
/// of their range; and the market's gap steps on it with its variance about
/// that path, the forecast's own session variance of the index. Each
/// cohort's growth moves with the step, and each cohort leaves with the
/// probability the law's transition check compares against on that
/// phase's stepped economy at the cohort's age: the hazard, the ladder from
/// the phase's own state (the market's fall over the gap's spread,
/// inflation, rates, the curve, valuation, unemployment, growth), the clamp
/// and the per-day conversion
/// ([`crate::economy::cycle::transition_probability_for`]). Mass entering a
/// phase takes the step's mean shock on a phase's first session, and its
/// state joins the phase's at the mass-weighted mean. No value is set
/// here: every part is the law's.
struct CycleGrowth {
    phases: [crate::economy::CyclePhase; 5],
    spec: crate::economy::CycleSpec,
    /// Each phase's cohorts: age in completed sessions, mass and growth.
    cohorts: Vec<Vec<(f64, f64, f64)>>,
    tracks: Vec<Option<CycleTrack>>,
    /// The meeting's options as the close stands; the bank's and the
    /// curve's inflation are each track's own.
    options: crate::economy::PolicyOptions,
    core: (bool, bool),
}

impl CycleGrowth {
    fn new(engine: &Engine, belief: &[f64; 5], econ: &EconomyState, day: i64) -> Self {
        let phases = crate::economy::cycle::phase_cycle();
        let spec = engine.cycle_spec();
        let ages = engine.cycle_age_law(day);
        let total: f64 = belief.iter().sum();
        let mut cohorts = vec![Vec::new(); 5];
        let mut tracks = vec![None; 5];
        for k in 0..5 {
            let b = if total > 0.0 { belief[k] / total } else { 0.0 };
            if b > 0.0 {
                for &(age, w) in &ages[k] {
                    cohorts[k].push((age, b * w, econ.gdp_growth));
                }
                let mut economy = econ.clone();
                economy.cycle_phase = phases[k];
                tracks[k] = Some(CycleTrack { economy, bank: engine.central_bank, gap_var: 0.0 });
            }
        }
        let options = crate::economy::PolicyOptions {
            path_before: engine.priced_policy_path(),
            eve_change: None,
            ..engine.policy_options(false)
        };
        let core = (engine.params.fed_core_inflation != 0.0, engine.params.treasury_core_inflation != 0.0);
        CycleGrowth { phases, spec, cohorts, tracks, options, core }
    }

    /// The growth oil's block reads at the next step: the cohorts' mean.
    fn growth(&self) -> f64 {
        let (mut m, mut g) = (0.0, 0.0);
        for c in self.cohorts.iter().flatten() {
            m += c.1;
            g += c.1 * c.2;
        }
        if m > 0.0 {
            g / m
        } else {
            0.0
        }
    }

    /// One close: each phase's economy stepped by the law at its cohorts'
    /// mean growth and one point above it (the step is affine in growth but
    /// for its clamp and the pull's doubling against the phase, so each
    /// cohort moves on the line through the two); each cohort's leavers, at
    /// the law's transition probability on that stepped economy, enter the
    /// next phase; then the phase's bank meets. `variance` is the forecast's
    /// variance of the session's return, the return the gap steps on.
    fn step(&mut self, inputs: &DailyInputs, volatility: f64, variance: f64, timestamp: i64) {
        let decay = inputs.yields.spread_equity_decay;
        let mut own = *inputs;
        own.oil_growth = None;
        let mut leaving = [(0.0, 0.0); 5];
        let mut stepped_tracks: Vec<Option<CycleTrack>> = vec![None; 5];
        for k in 0..5 {
            let (mut m, mut g) = (0.0, 0.0);
            for c in &self.cohorts[k] {
                m += c.1;
                g += c.1 * c.2;
            }
            let track = match &self.tracks[k] {
                Some(t) if m > 0.0 => t,
                _ => continue,
            };
            let bar = g / m;
            let mut at = track.economy.clone();
            at.cycle_phase = self.phases[k];
            at.gdp_growth = bar + 1.0;
            let s1 = update_economy_daily(&at, &own, &mut crate::economy::daily::MeanDraws).gdp_growth;
            at.gdp_growth = bar;
            let stepped = update_economy_daily(&at, &own, &mut crate::economy::daily::MeanDraws);
            let s0 = stepped.gdp_growth;
            let slope = s1 - s0;
            let gap_var = if decay != 0.0 { decay * decay * (track.gap_var + variance) } else { 0.0 };
            for c in self.cohorts[k].iter_mut() {
                c.0 += 1.0;
                c.2 = s0 + slope * (c.2 - bar);
                let q = crate::economy::cycle::transition_probability_for(
                    &stepped, self.phases[k], c.0, &self.spec, gap_var);
                let out = c.1 * q;
                c.1 -= out;
                leaving[k].0 += out;
                leaving[k].1 += out * c.2;
            }
            self.cohorts[k].retain(|c| c.1 > 1e-12);
            let mut options = self.options;
            let core = stepped.inflation_rate - stepped.oil_inflation_level;
            if self.core.0 {
                options.policy_inflation = Some(core);
            }
            if self.core.1 {
                options.curve_inflation = Some(core);
            }
            let met = crate::economy::update_central_bank_with(&track.bank, &stepped, timestamp, &mut MidDraws, &options);
            stepped_tracks[k] = Some(CycleTrack { economy: met.economy, bank: met.central_bank, gap_var });
        }
        let mut staying = [0.0; 5];
        for k in 0..5 {
            staying[k] = self.cohorts[k].iter().map(|c| c.1).sum();
            if stepped_tracks[k].is_some() {
                self.tracks[k] = stepped_tracks[k].clone();
            }
        }
        for k in 0..5 {
            let (m, g) = leaving[k];
            if m > 0.0 {
                let next = (k + 1) % 5;
                let shock = crate::economy::daily::phase_entry_growth_shock(self.phases[next], 0.5, volatility);
                self.cohorts[next].push((0.0, m, g / m + shock));
                let from = stepped_tracks[k].as_ref().expect("a phase with leavers was stepped");
                match self.tracks[next].as_mut() {
                    Some(t) if staying[next] > 0.0 => t.join(staying[next], from, m),
                    _ => {
                        let mut t = from.clone();
                        t.economy.cycle_phase = self.phases[next];
                        self.tracks[next] = Some(t);
                    }
                }
            }
        }
    }
}

/// The closes the oil forecast's paths are held for
/// (`oil_forecast_path_law`): the paths are simulated at one close and read
/// by it and the four after it, each from its own session on. Cost, not a
/// value of the model.
pub(crate) const OIL_FORECAST_PATHS_HELD: i64 = 5;

/// The oil forecast's paths held between the closes that simulate them
/// (`oil_forecast_path_law`): the close that simulated them and their mean
/// growth at the start of each session after it. Not state: nothing
/// snapshots, restores or hashes it, so a restored engine simulates afresh
/// at its first close and its forecast can differ from the original's by
/// the paths' Monte Carlo error until the original's next simulation.
#[derive(Clone)]
pub(crate) struct OilPathHold {
    day: i64,
    growth: Vec<f64>,
}

/// The paths the oil forecast simulates at a close
/// (`oil_forecast_path_law`). Precision, not a value of the model: the
/// cost of a traded history, and the Monte Carlo error that leaves.
pub(crate) const OIL_FORECAST_PATHS: usize = 20;

/// Growth's expectation over the law's own paths for oil's forecast
/// (`oil_forecast_path_law`). Each path starts from the close's public
/// state: a phase drawn from the belief, an age drawn from that phase's
/// stationary law under the full law, and growth at the published figure
/// plus a difference drawn from the law's own spread of growth about it in
/// that phase at that age ([`Engine::oil_paths_start_law`]); the close's
/// central bank, the market's belief and the close's index. At each
/// forecast close every path takes the law's own close, with draws from the
/// paths' own generator ([`crate::rng::oil_paths_rng`]), in the law's
/// order:
/// - the nowcast's report on the phase the session ran in, and the
///   belief's predict and update (`cycle_nowcast_accuracy`), with the spread
///   multiplier the market prices on it;
/// - the session's index: its return is the move in the index's fair value
///   on the path's own economy (each name's fair value as the tick reads it,
///   at its close's price and earnings, weighted by its close's
///   capitalisation), plus a normal at the forecast's variance of the
///   index; the market's P/E is the close's moved by the index over the
///   earnings' restatement;
/// - the economy's step, which the market's gap steps on that return; the
///   cycle's transition at the law's probability on the path's stepped
///   state (the hazard and the ladder, the market's fall and its P/E
///   among them); the earnings cycle's pull to its phase's level and the
///   volatility feedback's exposure; its central bank's meeting; and the
///   earnings anticipation on the path's belief and level.
/// Oil's block reads the paths' mean growth, which is oil's target's mean
/// over the paths (the target is affine in growth). No value is set here:
/// every part is the law's.
///
/// The transition is read on an exponential clock, the law's daily roll in
/// distribution: a path leaves its phase at the first close whose summed
/// `-log(1 - q)` passes its clock, a unit exponential, so it survives a
/// close with probability `1 - q`. The start's (phase, age) and the first
/// clock are stratified across the paths (each path's draw from its own
/// N-th of the unit interval, the two strata paired at random), which
/// leaves every path's law as it is and lowers the mean's Monte Carlo
/// error; every clock after a transition is a fresh draw.
struct OilPaths {
    paths: Vec<OilPath>,
    rng: GameRng,
    spec: crate::economy::CycleSpec,
    /// The meeting's options as the close stands; the bank's and the
    /// curve's inflation are each path's own.
    options: crate::economy::PolicyOptions,
    core: (bool, bool),
    /// The index's names: each one's share of the close's capitalisation,
    /// its valuation inputs, its beta, and its target P/E and volatility
    /// discount on the start's economy.
    names: Vec<OilPathName>,
    /// The market's P/E at the close and the earnings' restatement there.
    pe: Option<f64>,
    nominal: f64,
    /// The earnings anticipation's terms (`g`, `c`), where it runs.
    anticipation: Option<([f64; 5], f64)>,
}

struct OilPath {
    economy: EconomyState,
    bank: crate::economy::CentralBankState,
    /// What is left of the phase's clock.
    clock: f64,
    /// The market's belief over the phases on the path.
    belief: [f64; 5],
    /// The index's fair value and its level, each over the close's.
    fair: f64,
    level: f64,
    /// The anticipation's drift and its last `A - e`
    /// (`earnings_anticipation_drift_share`).
    drift: f64,
    raw: f64,
}

impl OilPaths {
    fn new(engine: &Engine, belief: &[f64; 5], econ: &EconomyState, day: i64) -> Self {
        let p = &engine.params;
        let phases = crate::economy::cycle::phase_cycle();
        let spec = engine.cycle_spec();
        let law = engine.oil_paths_start_law(day);
        let mut rng = crate::rng::oil_paths_rng(engine.root_seed, day);
        let n = OIL_FORECAST_PATHS;
        // The start's law as one list: each phase's share of the belief
        // times each of its age bins' share of the phase.
        let total: f64 = belief.iter().sum();
        let mut start: Vec<(usize, usize, f64)> = Vec::new();
        for k in 0..5 {
            let mass: f64 = law[k].iter().map(|b| b.1).sum();
            if belief[k] > 0.0 && mass > 0.0 {
                for (b, bin) in law[k].iter().enumerate() {
                    start.push((k, b, belief[k] / total * bin.1 / mass));
                }
            }
        }
        let mut order: Vec<usize> = (0..n).collect();
        for i in (1..n).rev() {
            let j = (rng.next_f64() * (i + 1) as f64) as usize;
            order.swap(i, if j > i { i } else { j });
        }
        let (drift, raw) = engine.anticipation_drift();
        let mut paths = Vec::with_capacity(n);
        for (i, &o) in order.iter().enumerate() {
            let u = (i as f64 + rng.next_f64()) / n as f64;
            let (mut k, mut b) = start.last().map(|s| (s.0, s.1)).unwrap_or((0, 0));
            let mut acc = 0.0;
            for &(kk, bb, w) in &start {
                acc += w;
                if u < acc {
                    k = kk;
                    b = bb;
                    break;
                }
            }
            let v = (o as f64 + rng.next_f64()) / n as f64;
            let mut economy = econ.clone();
            economy.cycle_phase = phases[k];
            if let Some(bin) = law.get(k).and_then(|l| l.get(b)) {
                economy.months_in_current_phase = bin.0 / spec.month_days;
                let m = bin.2.len();
                if m > 0 {
                    let j = (rng.next_f64() * m as f64) as usize;
                    economy.gdp_growth += bin.2[if j < m { j } else { m - 1 }];
                }
            }
            paths.push(OilPath {
                economy,
                bank: engine.central_bank,
                clock: -crate::mathx::log(1.0 - v),
                belief: *belief,
                fair: 1.0,
                level: 1.0,
                drift,
                raw,
            });
        }
        let options = crate::economy::PolicyOptions {
            path_before: engine.priced_policy_path(),
            eve_change: None,
            ..engine.policy_options(false)
        };
        let core = (p.fed_core_inflation != 0.0, p.treasury_core_inflation != 0.0);
        let mut cap = 0.0;
        for c in engine.companies.iter() {
            if c.is_public && !c.is_bankrupt && c.stock.market_cap > 0.0 {
                cap += c.stock.market_cap;
            }
        }
        let mut names = Vec::new();
        for c in engine.companies.iter() {
            if cap > 0.0 && c.is_public && !c.is_bankrupt && c.stock.market_cap > 0.0 {
                let mut name = OilPathName {
                    weight: c.stock.market_cap / cap,
                    valuation: c.valuation(),
                    beta: c.stock.beta,
                    pe: 1.0,
                    discount: 1.0,
                };
                let (pe, discount) = name.terms(p, econ);
                if pe > 0.0 && discount > 0.0 {
                    name.pe = pe;
                    name.discount = discount;
                    names.push(name);
                }
            }
        }
        OilPaths {
            paths,
            rng,
            spec,
            options,
            core,
            names,
            pe: econ.market_pe,
            nominal: crate::market::tick::nominal_scale(p, econ, engine.nominal_output_base),
            anticipation: engine.earnings_anticipation_terms(),
        }
    }

    /// The growth oil's block reads at the next step: the paths' mean.
    fn growth(&self) -> f64 {
        let g: f64 = self.paths.iter().map(|p| p.economy.gdp_growth).sum();
        g / self.paths.len() as f64
    }

    /// One close on every path. `inputs` are the forecast's for the
    /// session; `variance` is its variance of the session's return.
    fn step(&mut self, engine: &Engine, inputs: &DailyInputs, variance: f64, timestamp: i64) {
        let p = &engine.params;
        let phases = crate::economy::cycle::phase_cycle();
        let mut own = *inputs;
        own.oil_growth = None;
        let sd = 100.0 * crate::mathx::sqrt(crate::mathx::max(0.0, variance));
        let q = p.cycle_nowcast_accuracy;
        let lambda = engine.cycle_nowcast_terms.0;
        let OilPaths { paths, rng, spec, options, core, names, pe, nominal, anticipation } = self;
        for path in paths.iter_mut() {
            // The nowcast's report on the phase the session ran in, and the
            // spread the market prices before and after it.
            let k = phases.iter().position(|ph| *ph == path.economy.cycle_phase).unwrap_or(0);
            let before = engine.oil_path_spread_multiplier(&path.belief, path.economy.cycle_phase);
            if q != 0.0 {
                let u = rng.next_f64();
                let r = if u < q {
                    k
                } else {
                    let j = ((u - q) / (1.0 - q) * 4.0) as usize;
                    (k + 1 + if j > 3 { 3 } else { j }) % 5
                };
                let mut pred = [0.0; 5];
                for (j, (&lam, &pj)) in lambda.iter().zip(path.belief.iter()).enumerate() {
                    pred[j] += pj * (1.0 - lam);
                    pred[(j + 1) % 5] += pj * lam;
                }
                let other = (1.0 - q) / 4.0;
                let mut total = 0.0;
                for (j, pj) in pred.iter_mut().enumerate() {
                    *pj *= if j == r { q } else { other };
                    total += *pj;
                }
                if total > 0.0 && total.is_finite() {
                    for pj in pred.iter_mut() {
                        *pj /= total;
                    }
                    path.belief = pred;
                } else {
                    path.belief = [0.0; 5];
                    path.belief[k] = 1.0;
                }
            }
            if own.yields.spread_multiplier.is_some() {
                let after = engine.oil_path_spread_multiplier(&path.belief, path.economy.cycle_phase);
                own.yields.spread_multiplier = Some((before, after));
            }
            // The session's index on the path's economy, and its P/E.
            let now = crate::market::tick::nominal_scale(p, &path.economy, engine.nominal_output_base);
            let mut fair = 0.0;
            for name in names.iter() {
                let (pe, discount) = name.terms(p, &path.economy);
                fair += name.weight * pe / name.pe * discount / name.discount;
            }
            let fair = if *nominal > 0.0 { fair * now / *nominal } else { fair };
            let ret = if path.fair > 0.0 { 100.0 * (fair / path.fair - 1.0) } else { 0.0 } + sd * rng.next_normal();
            path.fair = fair;
            path.level *= 1.0 + ret / 100.0;
            if let Some(pe0) = *pe {
                if now > 0.0 {
                    path.economy.market_pe = Some(pe0 * path.level * *nominal / now);
                }
            }
            own.market_day_return_pct = ret;
            let mut stepped = update_economy_daily(&path.economy, &own, rng);
            let (h, next) = crate::economy::cycle::get_cycle_transition_probability_for(&stepped, spec);
            if h > 0.0 {
                path.clock += crate::mathx::log(1.0 - h);
                if path.clock <= 0.0 {
                    stepped.cycle_phase = next;
                    stepped.months_in_current_phase = 0.0;
                    path.clock = -crate::mathx::log(1.0 - rng.next_f64());
                }
            }
            // The earnings cycle's pull to its phase's level, and the
            // volatility feedback's exposure, as the close steps them.
            if p.earnings_cycle_depth != 0.0 {
                let target = match stepped.cycle_phase {
                    crate::economy::CyclePhase::Contraction | crate::economy::CyclePhase::Trough => {
                        -p.earnings_cycle_depth
                    }
                    _ => p.earnings_cycle_depth * p.earnings_cycle_upside,
                };
                let pull = 1.0 - crate::mathx::pow(0.5, 1.0 / p.earnings_cycle_half_life);
                let mut level = stepped.earnings_cycle + pull * (target - stepped.earnings_cycle);
                if p.earnings_cycle_sigma != 0.0 {
                    level += p.earnings_cycle_sigma * rng.next_normal();
                }
                stepped.earnings_cycle = level;
            }
            if p.fair_value_vix_discount != 0.0 && p.fair_value_vix_half_life != 0.0 {
                let target = crate::market::tick::vix_excess(p, stepped.vix);
                let half_life = if p.fair_value_vix_release_half_life != 0.0 && target < stepped.vix_feedback {
                    p.fair_value_vix_release_half_life
                } else {
                    p.fair_value_vix_half_life
                };
                let pull = 1.0 - crate::mathx::pow(0.5, 1.0 / half_life);
                stepped.vix_feedback += pull * (target - stepped.vix_feedback);
            }
            let mut options = *options;
            let core_inflation = stepped.inflation_rate - stepped.oil_inflation_level;
            if core.0 {
                options.policy_inflation = Some(core_inflation);
            }
            if core.1 {
                options.curve_inflation = Some(core_inflation);
            }
            let met = crate::economy::update_central_bank_with(&path.bank, &stepped, timestamp, rng, &options);
            path.economy = met.economy;
            path.bank = met.central_bank;
            // The anticipation on the path's belief and level.
            if let Some((g, c)) = anticipation {
                let share = p.earnings_anticipation_drift_share;
                if share != 0.0 && p.earnings_anticipation_half_life > 0.0 {
                    let rho = std::f64::consts::LN_2 / p.earnings_anticipation_half_life;
                    let hd = if p.earnings_anticipation_drift_half_life == 0.0 {
                        1260.0
                    } else {
                        p.earnings_anticipation_drift_half_life
                    };
                    path.drift = path.drift * crate::mathx::exp(-std::f64::consts::LN_2 / hd) + share * rho * path.raw;
                }
                let gk = if q != 0.0 {
                    let mut acc = 0.0;
                    for (pj, gj) in path.belief.iter().zip(g.iter()) {
                        acc += pj * gj;
                    }
                    acc
                } else {
                    let j = phases.iter().position(|ph| *ph == path.economy.cycle_phase).unwrap_or(0);
                    g[j]
                };
                let e = path.economy.earnings_cycle;
                path.raw = gk + *c * e - e;
                path.economy.earnings_anticipation = if share == 0.0 { path.raw } else { path.raw - path.drift };
            }
        }
    }
}

/// A name of the index the oil forecast's paths carry
/// (`oil_forecast_path_law`).
struct OilPathName {
    weight: f64,
    valuation: crate::fair_value::CompanyValuationInputs,
    beta: Option<f64>,
    /// [`OilPathName::terms`] on the start's economy.
    pe: f64,
    discount: f64,
}

impl OilPathName {
    /// The two terms of the name's fair value that read the economy beyond
    /// the earnings' restatement, as the tick reads them: its target P/E
    /// (the rates and QE; one on the book's path, which reads neither) and
    /// the volatility feedback's discount. Its earnings and price, and with
    /// them its buyback term and accrual, are the close's.
    fn terms(&self, p: &ModelParams, economy: &EconomyState) -> (f64, f64) {
        let pe = if self.valuation.eps.unwrap_or(0.0) > 0.0 {
            let view = crate::fair_value::EconomyValuationInputs {
                corporate_bond_yield: Some(economy.corporate_bond_yield),
                federal_funds_rate: economy.federal_funds_rate,
                qe_pe_boost: Some(economy.qe_pe_boost),
                qe_assets_ratio: Some(economy.qe_assets_ratio),
            };
            crate::fair_value::compute_target_pe_at(
                &self.valuation, &view, p.qe_pe_gain, p.qe_pe_stock_gain, p.neutral_discount_rate,
                p.rate_pe_sensitivity)
            .target_pe
        } else {
            1.0
        };
        let exposure = crate::market::tick::vix_feedback_exposure(p, economy);
        (pe, crate::market::tick::with_vix_discount(p, 1.0, exposure, self.beta))
    }
}

impl Engine {
    /// The spread multiplier the market prices on a path's belief
    /// ([`Engine::priced_spread_multiplier`] on the path's state).
    fn oil_path_spread_multiplier(&self, belief: &[f64; 5], phase: crate::economy::CyclePhase) -> f64 {
        let phases = crate::economy::cycle::phase_cycle();
        let m = if self.params.cycle_nowcast_accuracy != 0.0 {
            let mut acc = 0.0;
            for (p, &ph) in belief.iter().zip(phases.iter()) {
                acc += p * crate::economy::central_bank::spread_multiplier_of(ph);
            }
            acc
        } else {
            crate::economy::central_bank::spread_multiplier_of(phase)
        };
        let s = self.params.corporate_spread_cycle;
        if s == 0.0 {
            return m;
        }
        (1.0 - s) * m + s * self.cycle_nowcast_terms.1
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::economy::{
        create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions,
    };
    use crate::market::TickStock;

    fn company(id: &str, price: f64, shares: f64) -> TickCompany {
        TickCompany {
            id: id.to_string(),
            ticker: id.to_string(),
            sector: "technology".to_string(),
            is_bankrupt: false,
            is_public: true,
            sector_volatility: Some(1.0),
            sector_avg_pe: Some(32.0),
            eps: Some(4.0),
            book_value_per_share: Some(20.0),
            revenue_growth: Some(0.1),
            stock: TickStock {
                price,
                previous_close: price,
                previous_tick_price: None,
                open: price,
                high: price,
                low: price,
                volume: 0.0,
                avg_volume: 1e6,
                shares_outstanding: shares,
                market_cap: price * shares,
                mispricing_s: None,
                mispricing_s_prev_close: None,
                mispricing_momentum: None,
                fair_value_offset: None,
                buyback_log_shares: None,
                dividend: None,
                maker_inventory: None,
                garch_variance: 0.015 * 0.015,
                garch_cascade: [0.015 * 0.015; crate::market::garch::CASCADE_MAX],
                last_daily_return: None,
                beta: Some(1.0),
                short_interest: 0.0,
                float: shares,
            },
        }
    }

    fn engine(params: ModelParams) -> Engine {
        Engine::with_params(
            11,
            vec![company("A", 100.0, 1e8), company("B", 50.0, 3e8), company("C", 220.0, 5e7)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            vec!["technology".to_string(), "energy".to_string(), "healthcare".to_string()],
            params,
        )
    }

    fn with(dials: &[(&str, f64)]) -> ModelParams {
        let mut p = crate::params::PT_V21;
        for (name, value) in dials {
            p = p.with_override(name, *value).unwrap();
        }
        p
    }

    /// `sum(price * shares) / divisor` over the public, solvent names, by hand.
    fn by_hand(e: &Engine, divisor: f64) -> f64 {
        let cap: f64 = e
            .companies()
            .iter()
            .filter(|c| c.is_public && !c.is_bankrupt)
            .map(|c| c.stock.price * c.stock.shares_outstanding)
            .sum();
        cap / divisor
    }

    fn close_enough(a: f64, b: f64) -> bool {
        (a - b).abs() <= 1e-12 * b.abs()
    }

    #[test]
    fn off_there_is_no_index_no_live_vix_and_no_forecast() {
        let mut e = engine(crate::params::PT_V21);
        let mut buffer = SessionBuffer::new();
        e.run_numbered_day(0, 390, &mut buffer);
        assert!(e.index_level().is_none());
        assert!(e.index_state().is_none());
        assert!(e.live_vix().is_none());
        assert!(e.forecast().is_none());
    }

    #[test]
    fn the_index_keeps_its_level_through_a_listing_a_delisting_and_a_bankruptcy() {
        let mut e = engine(with(&[("index_level_listed", 1.0)]));
        let before = e.index_level().unwrap();
        assert_eq!(before.level, INDEX_BASE);
        assert_eq!(before.divisor, None);
        assert_eq!(before.close, None);
        // The base: the first open, on the prices before any tick.
        e.set_current_day(0);
        e.open_market();
        let d0 = e.index_level().unwrap().divisor.unwrap();
        let cap0: f64 = e.companies().iter().map(|c| c.stock.price * c.stock.shares_outstanding).sum();
        assert_eq!(d0.to_bits(), (cap0 / INDEX_BASE).to_bits());
        assert!(close_enough(e.index_level().unwrap().level, INDEX_BASE));
        let mut buffer = SessionBuffer::new();
        e.run_session(&SessionRequest::new(GameTime::new(9, 30, 3), 390), &mut buffer);
        let live = e.index_level().unwrap();
        assert!(close_enough(live.level, by_hand(&e, d0)));
        assert_ne!(live.level, INDEX_BASE, "the session moved no price");
        e.close_day(1);
        let closed = e.index_level().unwrap();
        assert!(close_enough(closed.close.unwrap(), live.level), "the close is the last prints'");

        // A delisting between sessions, at the prices then standing.
        let level = e.index_level().unwrap().level;
        e.remove_company(1).unwrap();
        let after = e.index_level().unwrap();
        assert!(close_enough(after.level, level));
        assert_eq!(after.constituents, 2);
        assert!(close_enough(after.divisor.unwrap(), by_hand(&e, 1.0) / level));
        // A listing.
        e.add_company(company("D", 80.0, 2e8));
        let after = e.index_level().unwrap();
        assert!(close_enough(after.level, level));
        assert_eq!(after.constituents, 3);
        // A bankruptcy, through the status setter.
        e.set_status(&[false, true, false], &[true, true, true]).unwrap();
        let after = e.index_level().unwrap();
        assert!(close_enough(after.level, level));
        assert_eq!(after.constituents, 2);
        // And the next session prices the survivors on the new divisor.
        let d1 = after.divisor.unwrap();
        e.set_current_day(1);
        e.open_market();
        e.run_session(&SessionRequest::new(GameTime::new(9, 30, 3), 390), &mut buffer);
        assert!(close_enough(e.index_level().unwrap().level, by_hand(&e, d1)));
    }

    #[test]
    fn the_index_state_round_trips_and_is_refused_where_the_switch_is_off() {
        let mut e = engine(with(&[("index_level_listed", 1.0)]));
        let mut buffer = SessionBuffer::new();
        e.run_numbered_day(0, 390, &mut buffer);
        let state = e.index_state().unwrap();
        let mut other = engine(with(&[("index_level_listed", 1.0)]));
        other.set_index_state(Some(state)).unwrap();
        assert_eq!(other.index_state(), Some(state));
        let mut off = engine(crate::params::PT_V21);
        assert!(off.set_index_state(Some(state)).is_err());
        assert!(off.set_vix_live_mark(Some(20.0)).is_err());
    }

    /// The live VIX at the session's last minute is the VIX the close
    /// publishes with its draws at their means, bit for bit, on a model
    /// whose close steps nothing the projection does not (the sector and
    /// own-variance states off). After the close it is the published VIX.
    #[test]
    fn the_last_minute_is_the_close_at_its_means() {
        let p = with(&[
            ("vix_intraday_live", 1.0),
            ("sector_vol_alpha", 0.0),
            ("sector_vol_beta", 0.0),
            ("idio_vol_alpha", 0.0),
            ("idio_vol_beta", 0.0),
            ("idio_vol_jump_bump", 0.0),
            ("vix_level_sigma", 0.0),
        ]);
        let mut e = engine(p);
        let mut buffer = SessionBuffer::new();
        for day in 0..3 {
            e.run_numbered_day(day, 390, &mut buffer);
        }
        e.set_current_day(3);
        e.open_market();
        let opening = e.live_vix().unwrap();
        e.run_session(&SessionRequest::new(GameTime::new(9, 30, 3), 390), &mut buffer);
        let last = e.live_vix().unwrap();
        assert_ne!(last.to_bits(), opening.to_bits(), "the session moved the live VIX");
        let noise = e.daily_innovation_column();
        let innovations: Vec<Option<f64>> = noise.into_iter().map(Some).collect();
        let variances = e.sector_base_variances();
        e.close_market(&DayCloseRequest {
            daily_innovations: &innovations,
            sector_base_variances: &variances,
            avg_volume: crate::market::AvgVolumePolicy::Hold,
        });
        let request = DayAdvanceRequest {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: 4,
            timestamp: 4 * 24 * 60,
        };
        e.advance_day_with(&request, &mut crate::economy::daily::MeanDraws);
        assert_eq!(last.to_bits(), e.published_vix().to_bits());
        assert_eq!(e.live_vix().unwrap().to_bits(), e.published_vix().to_bits());
    }

    #[test]
    fn the_forecast_runs_its_horizon_and_its_first_variance_is_the_identitys() {
        let mut e = engine(with(&[("forecast_horizon_sessions", 63.0), ("index_level_listed", 1.0)]));
        let mut buffer = SessionBuffer::new();
        for day in 0..3 {
            e.run_numbered_day(day, 390, &mut buffer);
        }
        let f = e.forecast().unwrap().clone();
        assert_eq!(f.horizon(), 63);
        assert_eq!(f.day, 3);
        for series in [&f.vix, &f.index_variance, &f.policy_rate, &f.oil] {
            assert_eq!(series.len(), 63);
            assert!(series.iter().all(|v| v.is_finite() && *v >= 0.0));
        }
        assert!(f.name_variance.iter().all(|row| row.len() == 63));
        // Entry 0 is the next session's variance as the identity reads it.
        let now = e.index_conditional_variance_terms_now().total();
        assert!(close_enough(f.index_variance[0], now), "{} {now}", f.index_variance[0]);
        // A pure function of the state: a copy computes the same one, and
        // so does a restore of it.
        let mut copy = e.clone();
        copy.refresh_forecast(None);
        assert_eq!(copy.forecast().unwrap(), &f);
        let words = f.to_words();
        let back = crate::derivatives::Forecast::from_words(&words).unwrap();
        assert_eq!(back, f);
    }

    /// The rule's mean, variance and skewness of a list of `(value, weight)`.
    fn moments(nodes: &[(f64, f64)]) -> (f64, f64, f64) {
        let mean: f64 = nodes.iter().map(|(x, w)| w * x).sum();
        let var: f64 = nodes.iter().map(|(x, w)| w * (x - mean) * (x - mean)).sum();
        let third: f64 = nodes.iter().map(|(x, w)| w * (x - mean).powi(3)).sum();
        (mean, var, third / var.powf(1.5))
    }

    #[test]
    fn the_spread_is_standard_skewed_as_asked_and_keeps_the_mean() {
        let weights: f64 = spread_shape(0.0).iter().map(|(_, w)| w).sum();
        assert!((weights - 1.0).abs() < 1e-12);
        for skew in [0.0, 0.81, 0.95, -0.5] {
            let shape = spread_shape(skew);
            let (mean, var, sk) = moments(&shape);
            assert!(mean.abs() < 1e-12 && (var - 1.0).abs() < 1e-12, "{skew}: {mean} {var}");
            assert!((sk - skew).abs() < 1e-9, "{skew}: skewness {sk}");
            // The nodes are a spread of the asked log variance whose
            // exponential has mean one, so `vix` stays the mean.
            let nodes = spread_at(&shape, 0.09);
            let (_, var_x, _) = moments(&nodes);
            assert!((var_x - 0.09).abs() < 1e-12);
            assert!((spread_expect(&nodes, 18.0, |v| v) - 18.0).abs() < 1e-12);
        }
        // At no skew the second moment is the lognormal's.
        let nodes = spread_at(&spread_shape(0.0), 0.09);
        let second = spread_expect(&nodes, 1.0, |v| v * v);
        assert!((second - crate::mathx::exp(0.09)).abs() < 1e-12);
        // Skewed, a steep power's expectation is above the lognormal's and
        // finite: the tails stay normal (the variance target reads the VIX
        // to the fourth power).
        let lognormal = spread_expect(&nodes, 1.0, |v| v.powi(4));
        let skewed = spread_expect(&spread_at(&spread_shape(0.95), 0.09), 1.0, |v| v.powi(4));
        assert!(skewed > lognormal && skewed < 1.5 * lognormal, "{skewed} {lognormal}");
    }

    #[test]
    fn the_published_vix_is_the_quotes_expectation_over_the_spread() {
        let dials = [
            ("forecast_horizon_sessions", 63.0),
            ("forecast_vix_dispersion", 0.3),
            ("forecast_vix_dispersion_half_life", 31.5),
            ("forecast_vix_dispersion_skew", 0.81),
        ];
        let e = engine(with(&dials));
        let shape = spread_shape(0.81);
        // Calm: the stress memory under the knee at the state, so the quote
        // is the state, and its expectation adds the premium's right tail.
        let (vix, calm) = (16.0, 0.1);
        assert_eq!(e.published_vix_at(vix, calm), vix);
        let near = e.forecast_published_vix(&shape, vix, calm, 0.0, 1);
        let far = e.forecast_published_vix(&shape, vix, calm, 0.3, 126);
        assert!((near - vix).abs() < 1e-9, "{near}");
        assert!(far > vix * 1.005, "{far}");
        // More of the memory's spread, more of the premium.
        assert!(e.forecast_published_vix(&shape, vix, calm, 0.2, 126) < far);
        // Stressed: the premium is near its cap at the state, and the
        // expectation reads its concave top, so it is below the quote.
        let hot = 2.0;
        let quote = e.published_vix_at(40.0, hot);
        assert!(e.forecast_published_vix(&shape, 40.0, hot, 0.05, 1) <= quote * 1.0001);
        // With the premium off it is the state, whatever the spread.
        let off = engine(with(&[dials[0], dials[1], dials[2], dials[3], ("vix_stress_premium", 0.0)]));
        assert_eq!(off.forecast_published_vix(&shape, vix, calm, 0.3, 126), vix);
        // With the dispersion off, the quote at the state.
        assert_eq!(e.forecast_published_vix(&[], vix, 2.0, 0.3, 126), e.published_vix_at(vix, 2.0));
    }
}
