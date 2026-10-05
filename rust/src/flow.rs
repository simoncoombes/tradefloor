//! The shock flow a preset was calibrated at, and a check on the flow a
//! caller adds to it.
//!
//! Every preset is fitted with the engine supplying its own shocks and
//! nothing else: company news drawn at each open, idiosyncratic and market
//! jumps, and one macro step per trading session with no active economic
//! shocks. A caller that hands the engine news through
//! [`crate::engine::TickRequest::news`] or [`crate::engine::SessionRequest`],
//! or macro shocks through [`crate::engine::DayAdvanceRequest::active_shocks`],
//! adds to that flow, and the model's realism statements describe the flow it
//! was fitted at, not the sum.
//!
//! [`CalibratedFlow`] states the fitted flow for any [`ModelParams`].
//! [`ExternalFlow`] is a tally a host keeps of what it supplied, and
//! [`ExternalFlow::assess`] compares the two. Neither touches an engine: the
//! tally is the caller's object, holds no model state and takes no draw, so
//! keeping one changes no trajectory.
//!
//! The comparison is per channel, because the channels reach prices
//! differently.
//!
//! - Company news lands on the one name it names. The fitted flow has it,
//!   at `endogenous_news_intensity` events a name a session with log size
//!   `endogenous_news_sigma`, so external company news is compared with that
//!   variance, and it is outside once it adds more than the fitted channel
//!   carries, which doubles the channel.
//! - Sector and market-wide news move many names at once, at weights
//!   `news_sector_weight` and `news_market_weight`. The fitted flow has none
//!   of either, so they are compared with the variance of the factor they
//!   move with, the market factor's base daily variance
//!   (`market_factor_sigma` squared), and are outside above
//!   [`COMMON_NEWS_TOLERANCE`] of it. The sector share assumes a roster
//!   spread evenly over the twelve sectors.
//! - Macro shocks have no fitted counterpart. A step's active shocks add
//!   twice the absolute sum of `gdp_impact x severity` to the VIX's daily
//!   target, and on a preset with `market_vol_vix_coupling` the market
//!   factor's variance target follows the VIX, so a sustained shock raises
//!   every name's volatility and the correlation between names. Any macro
//!   step with an active shock is outside.
//! - Fundamental revisions written with
//!   [`crate::engine::Engine::set_fundamentals`] move fair value one for one,
//!   since fair value is earnings times a target multiple. The fitted flow
//!   moves earnings only through the engine's own nominal and cycle terms,
//!   so a host's revisions are another per-name repricing and are compared
//!   with the fitted company news variance, outside once they exceed it.
//! - VIX levels a host writes into the economy (through
//!   [`crate::engine::Engine::economy_mut`]) move the market factor's
//!   variance target on a coupled preset as the VIX's own moves do. The
//!   fitted flow writes none, and the tally is outside once the written
//!   changes average more than [`VIX_WRITE_TOLERANCE`] points a session.
//! - From pt-v19 the macro calendar counts trading sessions
//!   (`macro_calendar_days_per_year` 252). A host that steps the economy on
//!   every calendar day, weekends included, runs the macro clock about 1.45
//!   times fast and applies its shocks that much more often, and is outside
//!   above [`MACRO_STEP_TOLERANCE`] steps a session.

use crate::economy::EconomicShock;
use crate::market::NewsEvent;
use crate::params::ModelParams;

/// Ticks in a regular session: what [`crate::engine::Engine::tick`] divides a
/// news event's `price_impact` by.
const SESSION_TICKS: f64 = 390.0;

/// Common (sector and market-wide) news is outside the fitted flow above
/// this share of the market factor's base daily variance. Chosen, not
/// fitted: a tenth of the factor's variance is about a five per cent rise in
/// its volatility.
pub const COMMON_NEWS_TOLERANCE: f64 = 0.1;

/// Mean absolute VIX points a host writes a session above which the tally is
/// outside. Chosen, not fitted: the VIX's own mean absolute daily change on
/// pt-v20 is about 0.64 points (20 seeds, 504 sessions, a 108-name roster),
/// so this is about a sixth of it.
pub const VIX_WRITE_TOLERANCE: f64 = 0.1;

/// Macro steps per trading session above which the macro clock is outside
/// the fitted flow. One step a session is the fitted rate; the tolerance
/// leaves room for a host that steps once on a holiday it does not trade.
pub const MACRO_STEP_TOLERANCE: f64 = 1.05;

/// The shocks a preset generates for itself, which is the flow its fitted
/// statistics were measured under.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct CalibratedFlow {
    /// Company news events a name a session (`endogenous_news_intensity`).
    pub company_news_rate: f64,
    /// Standard deviation of a company news event's log size
    /// (`endogenous_news_sigma`).
    pub company_news_sigma: f64,
    /// Sector-scoped news events a session. Zero on every preset: the engine
    /// draws none.
    pub sector_news_rate: f64,
    /// Market-wide news events a session. Zero on every preset.
    pub market_news_rate: f64,
    /// Weight a sector-scoped event lands on each name in its sector.
    pub news_sector_weight: f64,
    /// Weight a market-wide event lands on every name.
    pub news_market_weight: f64,
    /// Idiosyncratic jumps a name a session, and their log-size sd.
    pub idio_jump_rate: f64,
    pub idio_jump_sigma: f64,
    /// Market jumps a session, their mean and their sd.
    pub market_jump_rate: f64,
    pub market_jump_mean: f64,
    pub market_jump_sigma: f64,
    /// The market factor's base daily sigma (`market_factor_sigma`).
    pub market_factor_sigma: f64,
    /// Share of macro steps that carry an active economic shock. Zero: the
    /// library's own day loop passes none.
    pub macro_shock_share: f64,
    /// Macro steps a trading session. One: the library steps the economy at
    /// each close.
    pub macro_steps_per_session: f64,
}

impl CalibratedFlow {
    /// The flow `params` generates for itself.
    pub fn of(params: &ModelParams) -> Self {
        CalibratedFlow {
            company_news_rate: params.endogenous_news_intensity,
            company_news_sigma: params.endogenous_news_sigma,
            sector_news_rate: 0.0,
            market_news_rate: 0.0,
            news_sector_weight: params.news_sector_weight,
            news_market_weight: params.news_market_weight,
            idio_jump_rate: params.jump_intensity_idio,
            idio_jump_sigma: params.jump_sigma_idio,
            market_jump_rate: params.jump_intensity_market,
            market_jump_mean: params.jump_mean_market,
            market_jump_sigma: params.jump_sigma_market,
            market_factor_sigma: params.market_factor_sigma,
            macro_shock_share: 0.0,
            macro_steps_per_session: 1.0,
        }
    }

    /// Log-price variance the fitted company news adds to one name each
    /// session: rate times size squared.
    pub fn company_news_variance(&self) -> f64 {
        self.company_news_rate * self.company_news_sigma * self.company_news_sigma
    }

    /// The market factor's base daily variance, the yardstick for news that
    /// moves many names at once.
    pub fn market_factor_variance(&self) -> f64 {
        self.market_factor_sigma * self.market_factor_sigma
    }
}

/// A tally of the shocks a caller supplied, kept by the caller.
///
/// Feed it what the engine is handed, in the units the engine is handed it:
/// [`ExternalFlow::record_tick_news`] with the slice given to one
/// [`crate::engine::Engine::tick`], [`ExternalFlow::record_session_news`]
/// with the slice given to a whole [`crate::engine::Engine::run_session`],
/// [`ExternalFlow::record_macro_step`] with the shocks given to each
/// [`crate::engine::Engine::advance_day`], and
/// [`ExternalFlow::record_session`] once per trading session. The tally then
/// holds rates and sizes [`ExternalFlow::assess`] can compare with the
/// fitted flow.
#[derive(Debug, Clone, Default, PartialEq)]
#[non_exhaustive]
pub struct ExternalFlow {
    /// Names in the roster the news is spread over.
    pub names: usize,
    /// Trading sessions recorded.
    pub sessions: u64,
    /// Company-scoped events and the sum of their squared log moves.
    pub company_events: u64,
    pub company_sum_sq: f64,
    /// Sector-scoped events (a sector, no company) and the same sum.
    pub sector_events: u64,
    pub sector_sum_sq: f64,
    /// Market-wide events (no company, no sector) and the same sum.
    pub market_events: u64,
    pub market_sum_sq: f64,
    /// Fundamental revisions (log changes in earnings a host wrote) and the
    /// sum of their squares.
    pub fundamental_events: u64,
    pub fundamental_sum_sq: f64,
    /// VIX levels the host wrote: how many, the sum of their absolute
    /// changes in points, and the largest.
    pub vix_writes: u64,
    pub vix_write_abs: f64,
    pub vix_write_max: f64,
    /// Macro steps recorded, and how many carried an active shock.
    pub macro_steps: u64,
    pub macro_shock_steps: u64,
    /// Sum over macro steps of `|sum of gdp_impact x severity|`, the
    /// quantity the VIX target reads.
    pub macro_shock_load: f64,
}

impl ExternalFlow {
    /// An empty tally for a roster of `names`.
    pub fn new(names: usize) -> Self {
        ExternalFlow {
            names,
            ..ExternalFlow::default()
        }
    }

    /// One event landing a log move of `size` (before the scope weight).
    pub fn record_news_move(&mut self, company_id: Option<&str>, sector: Option<&str>, size: f64) {
        if !size.is_finite() || size == 0.0 {
            return;
        }
        let sq = size * size;
        if company_id.is_some() {
            self.company_events += 1;
            self.company_sum_sq += sq;
        } else if sector.is_some() {
            self.sector_events += 1;
            self.sector_sum_sq += sq;
        } else {
            self.market_events += 1;
            self.market_sum_sq += sq;
        }
    }

    /// The news handed to one [`crate::engine::Engine::tick`]. The tick
    /// divides each event's `price_impact` by 390, so that is the move the
    /// event lands.
    pub fn record_tick_news(&mut self, news: &[NewsEvent]) {
        for e in news {
            let x = e.price_impact.unwrap_or(0.0) / SESSION_TICKS;
            self.record_news_move(e.company_id.as_deref(), e.sector.as_deref(), x);
        }
    }

    /// The news handed to one whole [`crate::engine::Engine::run_session`],
    /// which applies it on every tick, so an event lands its full
    /// `price_impact` over the session.
    pub fn record_session_news(&mut self, news: &[NewsEvent]) {
        for e in news {
            let x = e.price_impact.unwrap_or(0.0);
            self.record_news_move(e.company_id.as_deref(), e.sector.as_deref(), x);
        }
    }

    /// One name's earnings revised by a log change of `size`, as written
    /// with [`crate::engine::Engine::set_fundamentals`]. A write that leaves
    /// the figure where it was is not a revision; pass only the ones that
    /// moved it.
    pub fn record_fundamental_move(&mut self, size: f64) {
        if !size.is_finite() || size == 0.0 {
            return;
        }
        self.fundamental_events += 1;
        self.fundamental_sum_sq += size * size;
    }

    /// The host wrote the economy's VIX, changing it by `delta` points.
    pub fn record_vix_write(&mut self, delta: f64) {
        if !delta.is_finite() || delta == 0.0 {
            return;
        }
        self.vix_writes += 1;
        self.vix_write_abs += delta.abs();
        if delta.abs() > self.vix_write_max {
            self.vix_write_max = delta.abs();
        }
    }

    /// One call of [`crate::engine::Engine::advance_day`] with these shocks.
    pub fn record_macro_step(&mut self, shocks: &[EconomicShock]) {
        self.macro_steps += 1;
        if !shocks.is_empty() {
            self.macro_shock_steps += 1;
        }
        let load: f64 = shocks.iter().map(|s| s.gdp_impact * s.severity).sum();
        self.macro_shock_load += load.abs();
    }

    /// One trading session traded.
    pub fn record_session(&mut self) {
        self.sessions += 1;
    }

    /// Compare the tally with the flow `params` was fitted at.
    pub fn assess(&self, params: &ModelParams) -> FlowAssessment {
        assess(&CalibratedFlow::of(params), self)
    }
}

/// What [`ExternalFlow::assess`] found. `inside` is false when any channel is
/// outside the fitted flow, and `findings` says which and by how much.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct FlowAssessment {
    pub inside: bool,
    /// External company news variance a name a session over the fitted
    /// company news variance.
    pub company_news_ratio: f64,
    /// External sector and market-wide news variance a name a session over
    /// the market factor's base daily variance.
    pub common_news_ratio: f64,
    /// Fundamental revision variance a name a session over the fitted
    /// company news variance.
    pub fundamental_ratio: f64,
    /// Mean absolute VIX points the host wrote a session.
    pub vix_write_per_session: f64,
    /// Share of macro steps that carried an active shock.
    pub macro_shock_share: f64,
    /// Mean `|sum of gdp_impact x severity|` a macro step: half the VIX
    /// target points the shocks add.
    pub macro_shock_load: f64,
    /// Macro steps a session.
    pub macro_steps_per_session: f64,
    /// One sentence per channel outside the fitted flow.
    pub findings: Vec<String>,
}

fn assess(fit: &CalibratedFlow, obs: &ExternalFlow) -> FlowAssessment {
    let sessions = if obs.sessions > 0 {
        obs.sessions as f64
    } else {
        1.0
    };
    let names = if obs.names > 0 { obs.names as f64 } else { 1.0 };
    // Company news: each event lands on one name.
    let company_var = obs.company_sum_sq / (names * sessions);
    let fitted_company = fit.company_news_variance();
    let company_news_ratio = if fitted_company > 0.0 {
        company_var / fitted_company
    } else if company_var > 0.0 {
        f64::INFINITY
    } else {
        0.0
    };
    // Common news: a sector event lands on each member at the sector weight,
    // a market event on every name at the market weight. Per name a session.
    let sector_var = obs.sector_sum_sq * fit.news_sector_weight * fit.news_sector_weight
        / (crate::sectors::keys().len() as f64 * sessions);
    let market_var = obs.market_sum_sq * fit.news_market_weight * fit.news_market_weight / sessions;
    let factor_var = fit.market_factor_variance();
    let common_news_ratio = if factor_var > 0.0 {
        (sector_var + market_var) / factor_var
    } else {
        0.0
    };
    let fundamental_var = obs.fundamental_sum_sq / (names * sessions);
    let fundamental_ratio = if fitted_company > 0.0 {
        fundamental_var / fitted_company
    } else if fundamental_var > 0.0 {
        f64::INFINITY
    } else {
        0.0
    };
    let vix_write_per_session = obs.vix_write_abs / sessions;
    let steps = obs.macro_steps as f64;
    let macro_shock_share = if steps > 0.0 {
        obs.macro_shock_steps as f64 / steps
    } else {
        0.0
    };
    let macro_shock_load = if steps > 0.0 {
        obs.macro_shock_load / steps
    } else {
        0.0
    };
    let macro_steps_per_session = if obs.sessions > 0 {
        steps / sessions
    } else {
        0.0
    };

    let mut findings = Vec::new();
    if company_news_ratio > 1.0 {
        findings.push(format!(
            "company news adds {:.2}x the variance the preset's own company news carries \
             ({:.3} events a name a session, rms log move {:.4}, against {:.3} at sd {:.4}), \
             so the news channel runs at {:.2}x its fitted size",
            company_news_ratio,
            obs.company_events as f64 / (names * sessions),
            if obs.company_events > 0 {
                crate::mathx::sqrt(obs.company_sum_sq / obs.company_events as f64)
            } else {
                0.0
            },
            fit.company_news_rate,
            fit.company_news_sigma,
            1.0 + company_news_ratio,
        ));
    }
    if common_news_ratio > COMMON_NEWS_TOLERANCE {
        findings.push(format!(
            "sector and market-wide news add {:.2} of the market factor's base daily variance \
             ({:.3} sector and {:.3} market-wide events a session); the preset draws no news \
             of either scope, so every name moves together by that much more than it was fitted at",
            common_news_ratio,
            obs.sector_events as f64 / sessions,
            obs.market_events as f64 / sessions,
        ));
    }
    if fundamental_ratio > 1.0 {
        findings.push(format!(
            "fundamental revisions add {:.2}x the fitted company news variance ({:.4} \
             revisions a name a session, rms log change {:.4}); fair value moves one for one \
             with earnings, and the preset was fitted with earnings moved only by its own \
             nominal and cycle terms",
            fundamental_ratio,
            obs.fundamental_events as f64 / (names * sessions),
            if obs.fundamental_events > 0 {
                crate::mathx::sqrt(obs.fundamental_sum_sq / obs.fundamental_events as f64)
            } else {
                0.0
            },
        ));
    }
    if vix_write_per_session > VIX_WRITE_TOLERANCE {
        findings.push(format!(
            "the host wrote the VIX {} times, {:.2} points a session in absolute terms and up to \
             {:.1} points at once; the preset writes none, and the market factor's variance \
             target follows the VIX on a preset with `market_vol_vix_coupling`",
            obs.vix_writes, vix_write_per_session, obs.vix_write_max,
        ));
    }
    if obs.macro_shock_steps > 0 {
        findings.push(format!(
            "{:.0}% of macro steps carried an active economic shock, at a mean load of {:.2}; \
             the preset was fitted with none, and each step's load adds twice itself to the \
             VIX target, and on a preset with `market_vol_vix_coupling` the market factor's \
             variance target follows the VIX",
            100.0 * macro_shock_share,
            macro_shock_load,
        ));
    }
    if obs.sessions > 0 && macro_steps_per_session > MACRO_STEP_TOLERANCE {
        findings.push(format!(
            "the economy stepped {:.2} times a trading session; the preset's macro calendar \
             counts sessions, so its months, quarters and phases run {:.2}x fast",
            macro_steps_per_session, macro_steps_per_session,
        ));
    }
    FlowAssessment {
        inside: findings.is_empty(),
        company_news_ratio,
        common_news_ratio,
        fundamental_ratio,
        vix_write_per_session,
        macro_shock_share,
        macro_shock_load,
        macro_steps_per_session,
        findings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::economy::ShockKind;

    fn v20() -> ModelParams {
        ModelParams::preset("pt-v20").unwrap()
    }

    fn company(x: f64) -> NewsEvent {
        NewsEvent {
            company_id: Some("A-0".into()),
            sector: Some("technology".into()),
            price_impact: Some(x),
        }
    }

    #[test]
    fn the_calibrated_flow_reads_the_preset() {
        let p = v20();
        let f = CalibratedFlow::of(&p);
        assert_eq!(f.company_news_rate, p.endogenous_news_intensity);
        assert_eq!(f.company_news_sigma, p.endogenous_news_sigma);
        assert_eq!(f.market_jump_rate, p.jump_intensity_market);
        assert_eq!(f.sector_news_rate, 0.0);
        assert_eq!(f.market_news_rate, 0.0);
        assert_eq!(f.macro_shock_share, 0.0);
        assert_eq!(f.macro_steps_per_session, 1.0);
    }

    #[test]
    fn no_external_flow_is_inside() {
        let mut t = ExternalFlow::new(40);
        for _ in 0..252 {
            t.record_macro_step(&[]);
            t.record_session();
        }
        let a = t.assess(&v20());
        assert!(a.inside, "{:?}", a.findings);
        assert_eq!(a.company_news_ratio, 0.0);
    }

    #[test]
    fn a_tick_lands_one_390th_and_a_session_lands_the_whole_impact() {
        let mut tick = ExternalFlow::new(1);
        tick.record_tick_news(&[company(0.05 * 390.0)]);
        let mut session = ExternalFlow::new(1);
        session.record_session_news(&[company(0.05)]);
        assert!((tick.company_sum_sq - 0.0025).abs() < 1e-15);
        assert!((session.company_sum_sq - 0.0025).abs() < 1e-15);
    }

    #[test]
    fn company_news_is_outside_past_the_fitted_variance() {
        let p = v20();
        let fit = CalibratedFlow::of(&p);
        let names = 100usize;
        let sessions = 1000u64;
        // Exactly the fitted variance: rate x sigma^2 a name a session.
        let at = |scale: f64| {
            let mut t = ExternalFlow::new(names);
            t.sessions = sessions;
            t.company_events = (fit.company_news_rate * names as f64 * sessions as f64) as u64;
            t.company_sum_sq = scale * fit.company_news_variance() * names as f64 * sessions as f64;
            t.assess(&p)
        };
        assert!(at(1.0).inside);
        let over = at(2.0);
        assert!(!over.inside);
        assert!((over.company_news_ratio - 2.0).abs() < 1e-9);
    }

    #[test]
    fn common_news_macro_shocks_and_a_fast_macro_clock_are_each_outside() {
        let p = v20();
        let mut market = ExternalFlow::new(100);
        market.sessions = 252;
        // One market-wide 3% event a session: 0.3^2 x 0.03^2 = 8.1e-5 a
        // session against a base factor variance of about 4.2e-5.
        market.market_events = 252;
        market.market_sum_sq = 252.0 * 0.03 * 0.03;
        assert!(!market.assess(&p).inside);

        let mut shocks = ExternalFlow::new(100);
        shocks.record_session();
        shocks.record_macro_step(&[EconomicShock {
            kind: ShockKind::Pandemic,
            severity: 0.5,
            gdp_impact: -2.0,
        }]);
        let a = shocks.assess(&p);
        assert!(!a.inside);
        assert_eq!(a.macro_shock_share, 1.0);
        assert!((a.macro_shock_load - 1.0).abs() < 1e-12);

        let mut clock = ExternalFlow::new(100);
        for d in 0..252 {
            clock.record_session();
            clock.record_macro_step(&[]);
            if d % 5 < 2 {
                clock.record_macro_step(&[]);
            }
        }
        let a = clock.assess(&p);
        assert!(!a.inside);
        assert!(a.macro_steps_per_session > 1.3);
    }

    #[test]
    fn written_vix_levels_are_outside_past_the_tolerance() {
        let p = v20();
        let mut t = ExternalFlow::new(10);
        for _ in 0..100 {
            t.record_session();
            t.record_macro_step(&[]);
        }
        t.record_vix_write(5.0);
        assert!(
            t.assess(&p).inside,
            "5 points over 100 sessions is 0.05 a session"
        );
        t.record_vix_write(-10.0);
        let a = t.assess(&p);
        assert!(!a.inside);
        assert_eq!(t.vix_write_max, 10.0);
        assert!((a.vix_write_per_session - 0.15).abs() < 1e-12);
    }

    #[test]
    fn quarterly_earnings_revisions_of_a_quarter_are_outside() {
        // Four revisions a name a year of rms log size 0.25: about 0.25 of
        // log variance a year, against the fitted news channel's 0.004.
        let p = v20();
        let mut t = ExternalFlow::new(10);
        for d in 0..252 {
            t.record_session();
            t.record_macro_step(&[]);
            if d % 63 == 0 {
                for _ in 0..10 {
                    t.record_fundamental_move(0.25);
                }
            }
        }
        t.record_fundamental_move(0.0);
        assert_eq!(t.fundamental_events, 40);
        let a = t.assess(&p);
        assert!(!a.inside);
        assert!(a.fundamental_ratio > 50.0, "{}", a.fundamental_ratio);
    }
}
