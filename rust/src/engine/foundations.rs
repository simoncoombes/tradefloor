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
    fn index_constituents(&self) -> (Vec<IndexConstituent>, Vec<String>, f64) {
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
    fn index_level_now(&self) -> f64 {
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
        self.forecast = Some(self.compute_forecast(day));
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
        // The close a meeting is held at is the first whose timestamp
        // reaches the date on the calendar.
        let minutes = 24 * 60;
        let next = self.central_bank.next_meeting_date;
        let first = if next <= 0 { day + 1 } else { (next + minutes - 1) / minutes };
        let first = if first < day + 1 { day + 1 } else { first };
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
    /// coupling taken as its expectation over a lognormal VIX
    /// (`forecast_vix_dispersion`) and the market factor's return memory's
    /// multiplier as its expectation over the memory's own spread.
    fn compute_forecast(&self, day: i64) -> Forecast {
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
        let mut universe_stress = self.universe_stress;
        let mut down = Some(self.market_vol.prev_day_down());
        let (mut var_oil, mut var_inventory) = (0.0, 0.0);
        let policy = self.forecast_policy_path(day, horizon);
        let k_curve = intraday_variance_factor();

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
            let vix_sq = if spread > 0.0 { vix * crate::mathx::exp(0.5 * spread) } else { vix };
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
            let (next_mv, next_faces) = self.forecast_factor_close(&mv, &regimes, vix, 0.0);
            let (next_mv_e, next_faces_e) = self.forecast_factor_close(&mv_e, &regimes, vix, spread);

            // Each name's GARCH on both tracks: the expected GJR step on the
            // session's noise.
            if p.garch_cascade_components < 1.0 {
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
                    let coupled = |x: f64| {
                        if p.garch_vix_coupling == 0.0 {
                            1.0
                        } else {
                            let cpl = p.garch_vix_coupling;
                            1.0 - cpl + crate::market::garch::vix_coupled_response(p, cpl, x / self.vix_anchor)
                        }
                    };
                    let base = n.sector_base * coupled(vix);
                    let base_e = if p.garch_vix_coupling == 0.0 {
                        n.sector_base
                    } else {
                        n.sector_base * lognormal_expect(vix, spread, coupled)
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
                        stress = (1.0 - h) * stress + h * d;
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
            inputs.vix_implied_from_market = if p.vix_level_identity != 0.0 {
                implied
            } else if p.vix_realised_vol_weight == 0.0 {
                0.0
            } else {
                p.market_vol_vix_anchor * mv.sigma_daily() / p.market_factor_sigma
            };
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
            let mut next = update_economy_daily(&econ, &inputs, &mut crate::economy::daily::MeanDraws);
            let kappa = p.oil_inventory_reversion;
            var_inventory = (1.0 - kappa) * (1.0 - kappa) * var_inventory + 0.25;
            let sd_inv = crate::mathx::sqrt(var_inventory);
            next.oil_price += crate::economy::daily::inventory_pressure_expected(next.oil_inventory_level, sd_inv)
                - crate::economy::daily::inventory_pressure(next.oil_inventory_level);
            if next.oil_last_opec_day != opec_day {
                next.oil_price += crate::economy::daily::opec_expected_impact(
                    oil_before, crate::mathx::sqrt(var_oil), p.oil_opec_symmetry);
            }
            var_oil = 0.97 * 0.97 * var_oil + 4.0;
            if spread > 0.0 {
                let k = p.usd_crisis_vix_threshold;
                let at_mean = if vix > k { vix - k } else { 0.0 };
                next.usd_index += crate::economy::daily::USD_SAFE_HAVEN_GAIN
                    * (lognormal_excess(vix, spread, k) - at_mean);
            }
            next.vix = vix_next;
            next.federal_funds_rate = rate;
            econ = next;

            out.vix.push(self.published_vix_at(econ.vix, stress));
            out.oil.push(econ.oil_price);

            // The belief, one session on.
            let mut pred = [0.0; 5];
            for (j, (&lam, &pj)) in exits.iter().zip(belief.iter()).enumerate() {
                pred[j] += pj * (1.0 - lam);
                pred[(j + 1) % 5] += pj * lam;
            }
            belief = pred;
        }
        out
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
    /// coupling is taken as its expectation over a lognormal VIX of that log
    /// variance, by scaling the regime's level by the ratio of the expected
    /// coupling to the coupling at the expected VIX.
    fn forecast_factor_close(
        &self,
        mv: &MarketVarianceState,
        regimes: &[(f64, f64, f64)],
        vix: f64,
        spread: f64,
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
                let expected = lognormal_expect(smoothed, spread, |x| {
                    crate::market::factor_vol::vix_response(p, x / denominator)
                });
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
}
