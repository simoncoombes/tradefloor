//! `updateEconomyDaily`, ported from the reference implementation.
//!
//! # The draw schedule is the contract
//!
//! This function is saturated with draws and the count is **state
//! dependent**. Getting the values right while taking one draw too many or
//! too few is not a partial success — the stream is shared with the whole
//! engine, so every later consumer on that day receives different numbers and
//! the simulation diverges for a reason that has nothing to do with the
//! economy.
//!
//! Worse than a count: [`GameRng::next_normal`](crate::rng::GameRng::next_normal) caches a **spare**. Box-Muller
//! produces two normals per pair of uniforms, so an extra or missing normal
//! call flips the parity of the cache and changes which uniforms every
//! subsequent normal anywhere in the engine is built from. The count and the
//! cache state are both part of the contract.
//!
//! The schedule, verified against the source:
//!
//! | When | Draws |
//! |---|---|
//! | Every day | **9 normals** — oil inventory, oil price, gold, copper, USD, trade balance, VIX, 10Y, fear/greed |
//! | Phase just changed (ANY phase) | +1 **uniform** — the GDP shock, drawn even when discarded |
//! | Quarter start | +1 normal (GDP growth) |
//! | Month start | +8 normals — GDP drift, unemployment, jobs, inflation, consumer confidence, business confidence, housing, home starts |
//! | OPEC decision day | +1 to +3 uniforms, branch-dependent |
//!
//! The 10Y normal is **D5, decided**: keep it. Production takes zero draws
//! there, but only because `??` short-circuits when WASM returns a value —
//! an artefact of evaluation order, not a modelling choice. See
//! the port notes.
//!
//! # WASM is absent by construction
//!
//! Every `isWasmEconomyReady()` branch in the original resolves to the JS
//! side here, per decisions D1–D3. Those branches are not ported as runtime
//! conditionals — there is no WASM in this crate to be ready — so the JS
//! formula is inlined directly and the decision is recorded at each site.

use super::state::*;
use crate::mathx::{self, clamp_via_min_max as clamp};
use super::central_bank::{CORPORATE_SPREAD_FLOOR, MORTGAGE_SPREAD_FLOOR};
use crate::rng::Rng;

/// `randomNormal(mean, stdDev)` — the reference implementation's local
/// wrapper.
///
/// One normal draw per call, with mean and scale applied **outside** the
/// draw. Ported as a wrapper rather than inlined so the call sites read the
/// same as the original and the draw count stays countable by eye.
#[inline]
fn random_normal(rng: &mut impl Rng, mean: f64, std_dev: f64) -> f64 {
    mean + rng.next_normal() * std_dev
}

/// The phase's growth range as the dials leave it.
///
/// Only the trough declares a negative floor under a positive top, and
/// only its floor moves. At 0.0 the table's own tuple is returned by a
/// BRANCH rather than by arithmetic, so every preset before pt-v18 reads
/// the identical pair of doubles.
fn effective_gdp_range(
    phase: &PhaseCharacteristics,
    cycle: CyclePhase,
    trough_floor: f64,
) -> (f64, f64) {
    if trough_floor == 0.0 || cycle != CyclePhase::Trough {
        phase.gdp_growth_range
    } else {
        let (lo, hi) = phase.gdp_growth_range;
        (lo * (1.0 - trough_floor), hi)
    }
}

/// The growth rate a phase pulls toward.
///
/// At `range_draw` 0.0 this is the declared range's midpoint, which is
/// what both read sites computed before the dial existed, and the stored
/// per-phase value is not read at all.
fn phase_growth_target(stored: f64, range: (f64, f64), range_draw: f64) -> f64 {
    if range_draw == 0.0 {
        (range.0 + range.1) / 2.0
    } else {
        stored
    }
}

/// Inputs that the caller supplies per day.
#[derive(Debug, Clone, Copy)]
#[non_exhaustive]
pub struct DailyInputs<'a> {
    /// Reference default: 1.0.
    pub volatility: f64,
    pub active_shocks: &'a [EconomicShock],
    /// Reference default: 0.
    pub market_return_pct: f64,
    /// `gameDay ?? 0`.
    pub game_day: i64,
    /// How fast VIX reverts toward its target each day.
    ///
    /// Threaded so it can be CALIBRATED. It was a const, and that made it
    /// unreachable by any search -- which matters because it is the other
    /// side of the scenario transient. The defect is that a 63-day variance
    /// half-life cannot track a twenty-day VIX spike; one answer is a faster
    /// variance process, which was measured and costs long-horizon realism,
    /// and the other is a LONGER SPIKE, which touches variance persistence
    /// not at all.
    pub vix_mean_reversion: f64,
    /// See [`crate::params::ModelParams::vix_decay_ratio`]. 1.0 is the
    /// shipped symmetric reversion exactly.
    pub vix_decay_ratio: f64,
    /// The VIX's own slow reversion toward the identity's anchor, and the
    /// anchor it reverts to (`Engine::vix_anchor` times
    /// `Engine::vix_level_multiplier`, the latter exactly 1.0 with the
    /// regime level off). At a rate of 0.0 -- every preset through pt-v19 --
    /// the term is not added and the step is the sum it always was, bit for
    /// bit. See [`crate::params::ModelParams::vix_anchor_reversion`].
    pub vix_anchor_reversion: f64,
    pub vix_anchor_level: f64,
    /// See [`crate::params::ModelParams::vix_anchor_weight`]. 0.0 leaves the
    /// target the read-back exactly.
    pub vix_anchor_weight: f64,
    /// See [`crate::params::ModelParams::vix_anchor_memory`]. 0.0 is the
    /// instantaneous form and `vix_anchor_slow` is then not read.
    pub vix_anchor_memory: f64,
    /// See [`crate::params::ModelParams::macro_compound_days_per_year`].
    /// 365.0 is the shipped division exactly.
    pub macro_compound_days_per_year: f64,
    /// See [`crate::params::ModelParams::macro_calendar_days_per_year`].
    /// [`MacroCalendar::shipped`] is every literal that stood exactly.
    pub macro_calendar: MacroCalendar,
    /// See [`crate::params::ModelParams::cycle_us_calibration`]. 0.0 reads
    /// the shipped phase table.
    pub cycle_us_calibration: f64,
    /// See [`crate::params::ModelParams::vix_anchor_centre`]. 0.0 leaves the
    /// reference at `vix_anchor_level` exactly.
    pub vix_anchor_centre: f64,
    /// See [`crate::params::ModelParams::vix_anchor_weight_level`]. 0.0 is
    /// the constant weight.
    pub vix_anchor_weight_level: f64,
    /// See [`crate::params::ModelParams::vix_anchor_weight_level_cap`].
    pub vix_anchor_weight_level_cap: f64,
    /// See [`crate::params::ModelParams::vix_anchor_weight_level_knee`].
    pub vix_anchor_weight_level_knee: f64,
    /// See [`crate::params::ModelParams::vix_anchor_weight_level_below`].
    pub vix_anchor_weight_level_below: f64,
    /// See [`crate::params::ModelParams::vix_anchor_weight_level_knee_fixed`].
    /// 0.0 puts the knee on `vix_anchor_level` as it always was.
    pub vix_anchor_weight_level_knee_fixed: f64,
    /// The identity's derived anchor WITHOUT the slow regime level's
    /// multiplier (`Engine::vix_anchor`). Read only by the knee, and only
    /// with `vix_anchor_weight_level_knee_fixed` nonzero.
    pub vix_anchor_level_fixed: f64,
    /// The anchor's slow memory of the read-back's log deviation, already
    /// advanced to today by the engine.
    pub vix_anchor_slow: f64,
    /// See [`crate::params::ModelParams::vix_fear_uptake`]. 0.0 leaves the
    /// identity's target as the anchor makes it, and `vix_fear` is then not
    /// read.
    pub vix_fear_uptake: f64,
    /// The VIX's fear memory in log units, already advanced to today by the
    /// engine ([`advance_vix_fear`]).
    pub vix_fear: f64,
    /// See [`crate::params::ModelParams::vix_jump_intensity`]. 0.0 takes
    /// no draws and reproduces the shipped schedule exactly.
    pub vix_jump_intensity: f64,
    pub vix_jump_scale: f64,
    /// VIX points per unit of a down day's index return (shipped 25.0), of
    /// an up day's (10.0), the clamp on that return (0.03) and the ceiling
    /// on the whole target excursion (12.0). Threaded for the reason
    /// `vix_mean_reversion` is: a literal here decides how violent a crisis
    /// can be, which is a calibration question. See §68.
    pub vix_return_gain: f64,
    /// How much of the VIX target is the market's own volatility, and what
    /// that volatility implies in VIX points. The engine computes the second
    /// from the market factor's current sigma through the forward coupling's
    /// own anchor; at weight 0.0 neither is read. See §68.
    pub vix_realised_vol_weight: f64,
    /// Which return the VIX reacts to (0.0 the last tick, 1.0 the day), and
    /// the day's cap-weighted open-to-close index return in percent. See §70.
    pub vix_return_source: f64,
    /// How much of the VIX level comes from the cycle phase (1.0 shipped).
    pub vix_cycle_amplitude: f64,
    pub market_day_return_pct: f64,
    pub vix_implied_from_market: f64,
    pub vix_return_gain_up: f64,
    /// The VIX's level comes from the index's own conditional variance
    /// rather than from the phase table. See
    /// `ModelParams::vix_level_identity`; 0.0 is every preset before
    /// pt-v19 and is bit-identical.
    pub vix_level_identity: f64,
    /// The index's conditional daily sigma in PER CENT, for the fear
    /// excursion's zero-mean correction. Read only under the identity.
    pub vix_index_sigma_pct: f64,
    /// The exponent of the return-to-fear response; 1.0 is the linear
    /// form and is bit-identical. See `ModelParams::vix_return_exponent`.
    pub vix_return_exponent: f64,
    pub vix_return_clamp: f64,
    pub vix_target_shock_cap: f64,
    /// Upper bound on the VIX state. See `ModelParams::vix_ceiling`, which
    /// carries the reasoning; 80.0 reproduces the literal this replaces.
    pub vix_ceiling: f64,
    /// A constant added to the VIX target BEFORE the realised-volatility
    /// blend, so it is scaled by `1 - vix_realised_vol_weight` exactly as the
    /// anchor is. See `ModelParams::vix_target_offset`, which carries the
    /// reasoning and the limitation.
    pub vix_target_offset: f64,
    /// The level dependence of the fear response and the up side's own
    /// exponent. See `ModelParams::vix_return_level_exponent`,
    /// `::vix_return_exponent_up` and `::vix_return_level_exponent_up`;
    /// at (0.0, 1.0, 0.0) `return_spike_at_level` is a BRANCH to
    /// `return_spike_for` and every preset up to pt-v19 is bit-identical.
    pub vix_return_level_exponent: f64,
    pub vix_return_exponent_up: f64,
    pub vix_return_level_exponent_up: f64,
    /// The VIX's own innovation, as a fraction of its level per session,
    /// and the part of it that scales with the session return. See
    /// `ModelParams::vix_innovation_sigma`; at (0.0, 0.0) the noise is the
    /// shipped `0.15 * volatility` points, computed by the same expression.
    pub vix_innovation_sigma: f64,
    pub vix_innovation_return_sigma: f64,
    /// A fear event's size in units of the day's innovation scale, and the
    /// part of its arrival rate that rises with a down session. See
    /// `ModelParams::vix_jump_level_scale`; at 0.0 the event is
    /// `vix_jump_scale` points, and with both intensities at 0.0 no draw
    /// is taken.
    pub vix_jump_level_scale: f64,
    pub vix_jump_return_intensity: f64,
    /// Monthly fraction of the inflation gap closed toward the 2% target.
    /// Threaded like `vix_mean_reversion`: the shipped 0.55 was a literal
    /// inside the inflation update, which made inflation's persistence and
    /// dispersion a fact of the code rather than a calibration.
    pub inflation_reversion: f64,
    /// Hard ceiling on endogenous inflation, percent (shipped 6.0).
    pub inflation_ceiling: f64,
    /// Hard floor on endogenous inflation, percent (shipped -1.0).
    pub inflation_floor: f64,
    /// VIX level at which crisis behaviour begins.
    ///
    /// Gates the sector-to-market correlation blend, the universe stress
    /// memory and the economy's crisis premium. Threaded for the same
    /// reason: where a crisis STARTS is a calibration question, and a const
    /// answers it before anyone asks.
    pub crisis_vix_threshold: f64,
    /// The VIX above which the dollar catches a safe-haven bid.
    ///
    /// SEPARATE from `crisis_vix_threshold`, and defaulted to the same
    /// constant, because 0.4.2 learned what happens when they are the same
    /// dial. Issue #50 correctly reported that this gate read the constant
    /// while the gold crisis premium read the parameter, and the one-line fix
    /// pointed both at the parameter. But `pt-v13` and `pt-v14` OVERRIDE
    /// `crisis_vix_threshold` to 30.88, so their dollar gate moved from 25.5
    /// and their trajectories moved with it, in a patch release. Two gates
    /// that happen to share a default are not one gate.
    pub usd_crisis_vix_threshold: f64,
    /// Re-assert the credit spread floors on every daily step, scaled.
    ///
    /// 0.0 disables it, which is what every shipped preset sets and what the
    /// reference implementation does. 1.0 enforces both floors in full. See
    /// the block at the end of `update_economy_daily`.
    pub daily_credit_floor_gain: f64,
    /// How much of oil demand supply answers on the daily step. 0.0
    /// disables it, which is what every shipped preset sets and what the
    /// reference implementation does. See `ModelParams::oil_supply_response`
    /// for why the zero is a defect rather than a modelling choice, and why
    /// 1.0 is the derived value.
    pub oil_supply_response: f64,
    /// Removes the direction from the OPEC rule while keeping its size.
    /// 0.0 disables it, which is what every shipped preset sets and what
    /// the reference implementation does. See `ModelParams::oil_opec_symmetry`.
    pub oil_opec_symmetry: f64,
    /// The share of oil's seasonal amplitude carried by the reversion
    /// target rather than by the price level. 0.0 puts all of it on the
    /// level, which is what every shipped preset sets and what the
    /// reference implementation does. See
    /// `ModelParams::oil_seasonality_target` for why a shape applied to a
    /// level compounds over a window shorter than its own period.
    pub oil_seasonality_target: f64,
    /// Where the trough phase's growth range begins. 0.0 is the shipped
    /// `(-1.0, 0.5)` and is what every preset before pt-v18 sets. See
    /// [`crate::params::ModelParams::trough_growth_floor`].
    pub trough_growth_floor: f64,
    /// Whether a phase's growth target is drawn from its declared range
    /// or fixed at the midpoint. 0.0 takes NO draw and reproduces the
    /// shipped behaviour exactly. See
    /// [`crate::params::ModelParams::phase_target_range_draw`].
    pub phase_target_range_draw: f64,
    /// The share of the gap between the cyclical drive and the unemployment
    /// impulse closed at each monthly release: `1 - 0.5^(month / half_life)`
    /// from `ModelParams::unemployment_adjustment_half_life`. 0.0 is off,
    /// and the release adds the drive whole, as it always has.
    pub unemployment_adjustment: f64,
    /// The monthly share of unemployment's gap to the natural rate closed
    /// at a release. 0.0 is the shipped 0.06. See
    /// `ModelParams::unemployment_natural_pull`.
    pub unemployment_natural_pull: f64,
    /// Okun's law as its annual coefficient. 0.0 is the shipped monthly
    /// 0.20 and the recovery term. See
    /// `ModelParams::unemployment_okun_coefficient`.
    pub unemployment_okun_coefficient: f64,
    /// The natural rate of unemployment with no long-term unemployment,
    /// percent. 0.0 is the shipped 4.0. See
    /// `ModelParams::unemployment_natural_rate`.
    pub unemployment_natural_rate: f64,
    /// The daily share of oil inventory's gap to [`OIL_INVENTORY_NORMAL`]
    /// closed. 0.0 is off. See `ModelParams::oil_inventory_reversion`.
    pub oil_inventory_reversion: f64,
    /// Inflation's monthly response to oil off [`OIL_PASSTHROUGH_ANCHOR`],
    /// both sides, as a multiple of the shipped 0.01 a dollar. 0.0 is the
    /// shipped three-way branch. See `ModelParams::oil_inflation_passthrough`.
    pub oil_inflation_passthrough: f64,
    /// The share of oil's gap to its target closed each session. 0.0 is the
    /// shipped 0.03. See `ModelParams::oil_mean_reversion`.
    pub oil_mean_reversion: f64,
    /// The sd of oil's daily noise, dollars at a volatility of 1.0. 0.0 is
    /// the shipped 2.0. See `ModelParams::oil_noise_sd`.
    pub oil_noise_sd: f64,
    /// Inventory's pressure on oil's level, sessions. 0.0 is the shipped
    /// daily push. See `ModelParams::oil_inventory_level_gain`.
    pub oil_inventory_level_gain: f64,
    /// The convenience yield's elasticity of oil's target to inventory. 0.0
    /// is no premium. See `ModelParams::oil_convenience_yield`.
    pub oil_convenience_yield: f64,
    /// The variance of the inventory the premium reads after the step, about
    /// the level the step computes: 0.0 at a close, which reads the
    /// inventory it has; the forecast's spread on its expected path, so the
    /// step reads the premium's expectation. See [`oil_convenience_factor`].
    pub oil_convenience_inventory_var: f64,
    /// The same before the step, about the inventory the step starts from.
    pub oil_convenience_inventory_var_before: f64,
    /// The oil price's long factor (`ModelParams::oil_target_drift_sd`): the
    /// log of its level before the step and after it. `None` is no long
    /// factor. The forecast passes the log of the level's expectation.
    pub oil_target_drift: Option<(f64, f64)>,
    /// Whether the oil pushes decay at the shipped 0.03 apart from the rest
    /// of the price. See `ModelParams::oil_pushes_in_target`.
    pub oil_pushes_in_target: bool,
    /// The oil noise as a share of the price; 0.0 is the noise in dollars.
    /// See `ModelParams::oil_noise_log_sd`.
    pub oil_noise_log_sd: f64,
    /// The oil price's elasticity to the dollar as a level; 0.0 is the
    /// dollar's daily drag. See `ModelParams::oil_dollar_elasticity`.
    pub oil_dollar_elasticity: f64,
    /// The sd of oil inventory's daily noise; 0.0 is the standing 0.5. See
    /// `ModelParams::oil_inventory_noise_sd`.
    pub oil_inventory_noise_sd: f64,
    /// The dollar index's daily reversion share; 0.0 is the standing 0.02.
    /// See `ModelParams::usd_mean_reversion`.
    pub usd_mean_reversion: f64,
    /// The dollar index's daily noise sd; 0.0 is the standing 0.3. See
    /// `ModelParams::usd_noise_sd`.
    pub usd_noise_sd: f64,
    /// The oil price's bounds, dollars; 0.0 is the standing 35 and 150. See
    /// `ModelParams::oil_price_floor`.
    pub oil_price_floor: f64,
    pub oil_price_ceiling: f64,
    /// The business-cycle phase and the GDP growth (percent) the fear/greed
    /// index reads, as PUBLISHED (`ModelParams::fear_greed_published_inputs`),
    /// or `None` for the economy's own, as it always read them.
    pub fear_greed_published: Option<(CyclePhase, f64)>,
    /// The yield curve's daily dials (pt-v20). See [`YieldDials`].
    pub yields: YieldDials,
}

/// The yield curve's daily step, as dials. [`YieldDials::default`] is the
/// arithmetic that always stood, bit for bit: the 10-year's 0.03 noise, the
/// 2-year as the formula of the policy rate and the 10-year, the flight to
/// quality at 0.02 read off the PREVIOUS day's closing-minute return behind a
/// 0.5 per cent gate (which that return never crosses, so it never fires),
/// and the corporate yield moved only at a central-bank meeting.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct YieldDials {
    /// The 10-year's daily noise, percentage points. See
    /// [`crate::params::ModelParams::treasury_10y_noise`].
    pub treasury_10y_noise: f64,
    /// The 2-year's own daily noise; 0.0 is the formula. See
    /// [`crate::params::ModelParams::treasury_2y_noise`].
    pub treasury_2y_noise: f64,
    /// Percentage points of yield per per cent of index return. See
    /// [`crate::params::ModelParams::flight_to_quality_gain`].
    pub flight_to_quality_gain: f64,
    /// A switch: 1.0 reads the session's own index return. See
    /// [`crate::params::ModelParams::flight_to_quality_day`].
    pub flight_to_quality_day: f64,
    /// A switch: 1.0 moves the corporate yield every session. See
    /// [`crate::params::ModelParams::corporate_yield_daily`].
    pub corporate_yield_daily: f64,
    /// A caller pinned the VIX before this session (`Engine::vix_pinned_today`).
    /// The close's VIX move is then the VIX law's reversion from a level the
    /// caller wrote, which the next pin discards, so the corporate yield
    /// takes no VIX term from it. Read only with `corporate_yield_daily` on.
    pub vix_pinned: bool,
    /// A caller pinned the corporate yield before this session. The pinned
    /// level then holds through the close, as it does on every preset
    /// without `corporate_yield_daily`: the daily move is not applied.
    pub corporate_pinned: bool,
    /// The spread's cycle multiplier at the session's start and at its close,
    /// under `cycle_nowcast_accuracy` or `corporate_spread_cycle`: the daily
    /// move then carries the meeting formula's whole change,
    /// `S(VIX', m') - S(VIX, m)`, so the level stays on the formula and a
    /// meeting has nothing to re-anchor. `None` is the move that stood.
    pub spread_multiplier: Option<(f64, f64)>,
    /// A caller pinned the 10-year and it holds through the close
    /// (`macro_pins_hold`): its step is taken, draw included, and
    /// discarded, so the 2-year's formula, the flight to quality and the
    /// corporate yield's daily move read the pinned level.
    pub treasury_10y_pinned: bool,
    /// The same for the 2-year.
    pub treasury_2y_pinned: bool,
    /// The Fed put's expected cut the curve prices tonight, percentage
    /// points: `treasury_put_pricing` times the cut the put would ask for at
    /// a meeting now, no more than the policy rate. 0.0 unless
    /// `fed_put_gain` and `treasury_put_pricing` are both set. See
    /// [`crate::params::ModelParams::treasury_put_pricing`].
    pub priced_put: f64,
    /// The policy path the curve prices tonight, percentage points, signed:
    /// `treasury_path_pricing` times the market's forecast of the rate's
    /// further change. 0.0 unless the dial is set. See
    /// [`crate::params::ModelParams::treasury_path_pricing`].
    pub priced_path: f64,
    /// The next meeting's expected change the curve prices tonight,
    /// percentage points, signed (`policy_anticipation`): read beside the
    /// priced path, damped with it. 0.0 unless the dial is set. See
    /// [`crate::params::ModelParams::policy_anticipation`].
    pub priced_anticipation: f64,
    /// `treasury_policy_damping`: the share of the priced policy rate's
    /// distance from [`TREASURY_NEUTRAL_RATE`] the 10-year's anchor leaves
    /// out. 0.0 unless the dial is set.
    pub rate_damping: f64,
    /// Percentage points off the 10-year's term premium per VIX point above
    /// 20 while inflation is under 4. See
    /// [`crate::params::ModelParams::treasury_haven_gain`].
    pub haven_gain: f64,
    /// The share of the VIX slope taken out of the corporate spread's
    /// formula. See [`crate::params::ModelParams::corporate_spread_vix_cut`].
    pub spread_vix_cut: f64,
    /// Percentage points of spread (times the multiplier) per unit of the
    /// index's log fall below its slow average. See
    /// [`crate::params::ModelParams::corporate_spread_equity_gain`].
    pub spread_equity_gain: f64,
    /// The slow average's one-session decay, `0.5^(1/H)` with H
    /// [`crate::params::ModelParams::corporate_spread_equity_half_life`].
    /// Read only with `spread_equity_gain` set.
    pub spread_equity_decay: f64,
}

/// The largest move the corporate yield takes in one session under
/// `corporate_yield_daily`, in percentage points either way. The VIX term
/// is 2 bp a point times the cycle's multiplier (2.8 in a contraction), so a
/// 20-point VIX session would move it 1.1 points unbounded. Moody's Baa
/// yield (FRED DBAA, 10,624 sessions 1986-2026) never moved more than 0.48
/// in a session (18 March 2020; 0.43 on 10 October 2008), its 99.99th
/// percentile is 0.43 and its 99.9th 0.25. 0.50 sits just above the largest
/// recorded move. Read only with `corporate_yield_daily` on, so no preset
/// through pt-v19 reads it.
pub const CORPORATE_DAILY_MOVE_CAP: f64 = 0.50;

/// The VIX level above which the Treasury haven lowers the 10-year's term
/// premium (`treasury_haven_gain`). 20 is about the tape's long-run mean
/// VIX (19.5 on ^VIX 1990-2025, S&P 500 and VIX tape in the design
/// repository; the median is 17.6), so the haven acts on the 38 per cent of
/// sessions above it, and most on the stressed ones.
pub const TREASURY_HAVEN_VIX: f64 = 20.0;

/// The policy rate the 10-year's damped pass-through pulls toward
/// (`treasury_policy_damping`), in per cent: about the long-run mean policy
/// rate, 2.9 on FRED DFF 1990-2025 and 2.6 on pt-v19's long run. Read only
/// with the dial set.
pub const TREASURY_NEUTRAL_RATE: f64 = 2.5;

/// The Treasury haven's cut to the 10-year's term premium: `gain` points per
/// VIX point above [`TREASURY_HAVEN_VIX`]. Read by the daily anchor and by
/// the meeting's 10-year target, only with `treasury_haven_gain` set.
pub fn haven_term_cut(gain: f64, vix: f64) -> f64 {
    gain * mathx::max(0.0, vix - TREASURY_HAVEN_VIX)
}

impl Default for YieldDials {
    fn default() -> Self {
        Self {
            treasury_10y_noise: 0.03,
            treasury_2y_noise: 0.0,
            flight_to_quality_gain: 0.02,
            flight_to_quality_day: 0.0,
            corporate_yield_daily: 0.0,
            vix_pinned: false,
            corporate_pinned: false,
            spread_multiplier: None,
            treasury_10y_pinned: false,
            treasury_2y_pinned: false,
            priced_put: 0.0,
            priced_path: 0.0,
            priced_anticipation: 0.0,
            rate_damping: 0.0,
            haven_gain: 0.0,
            spread_vix_cut: 0.0,
            spread_equity_gain: 0.0,
            spread_equity_decay: 0.0,
        }
    }
}

impl<'a> Default for DailyInputs<'a> {
    fn default() -> Self {
        Self {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: 0,
            trough_growth_floor: 0.0,
            phase_target_range_draw: 0.0,
            unemployment_adjustment: 0.0,
            unemployment_natural_pull: 0.0,
            unemployment_okun_coefficient: 0.0,
            unemployment_natural_rate: 0.0,
            oil_inventory_reversion: 0.0,
            oil_mean_reversion: 0.0,
            oil_noise_sd: 0.0,
            oil_inventory_level_gain: 0.0,
            oil_convenience_yield: 0.0,
            oil_convenience_inventory_var: 0.0,
            oil_convenience_inventory_var_before: 0.0,
            oil_target_drift: None,
            oil_pushes_in_target: false,
            oil_noise_log_sd: 0.0,
            oil_dollar_elasticity: 0.0,
            oil_inventory_noise_sd: 0.0,
            usd_mean_reversion: 0.0,
            usd_noise_sd: 0.0,
            oil_price_floor: 0.0,
            oil_price_ceiling: 0.0,
            oil_inflation_passthrough: 0.0,
            fear_greed_published: None,
            yields: YieldDials::default(),
            vix_mean_reversion: VIX_MEAN_REVERSION,
            vix_decay_ratio: 1.0,
            vix_anchor_reversion: 0.0,
            vix_anchor_level: 0.0,
            vix_anchor_weight: 0.0,
            vix_anchor_memory: 0.0,
            macro_compound_days_per_year: 365.0,
            macro_calendar: MacroCalendar::shipped(),
            cycle_us_calibration: 0.0,
            vix_anchor_centre: 0.0,
            vix_anchor_weight_level: 0.0,
            vix_anchor_weight_level_cap: 0.0,
            vix_anchor_weight_level_knee: 0.0,
            vix_anchor_weight_level_below: 0.0,
            vix_anchor_weight_level_knee_fixed: 0.0,
            vix_anchor_level_fixed: 0.0,
            vix_anchor_slow: 0.0,
            vix_fear_uptake: 0.0,
            vix_fear: 0.0,
            vix_jump_intensity: 0.0,
            vix_jump_scale: 0.0,
            vix_return_gain: VIX_RETURN_GAIN,
            vix_realised_vol_weight: 0.0,
            vix_return_source: 0.0,
            vix_cycle_amplitude: 1.0,
            market_day_return_pct: 0.0,
            vix_implied_from_market: 0.0,
            vix_return_gain_up: VIX_RETURN_GAIN_UP,
            vix_level_identity: 0.0,
            vix_index_sigma_pct: 0.0,
            vix_return_exponent: 1.0,
            vix_return_clamp: VIX_RETURN_CLAMP,
            vix_target_shock_cap: VIX_TARGET_SHOCK_CAP,
            inflation_reversion: INFLATION_MEAN_REVERSION,
            inflation_ceiling: INFLATION_CEILING,
            inflation_floor: INFLATION_FLOOR,
            crisis_vix_threshold: CRISIS_VIX_THRESHOLD,
            usd_crisis_vix_threshold: CRISIS_VIX_THRESHOLD,
            vix_ceiling: 80.0,
            vix_target_offset: 0.0,
            vix_return_level_exponent: 0.0,
            vix_return_exponent_up: 1.0,
            vix_return_level_exponent_up: 0.0,
            vix_innovation_sigma: 0.0,
            vix_innovation_return_sigma: 0.0,
            vix_jump_level_scale: 0.0,
            vix_jump_return_intensity: 0.0,
            daily_credit_floor_gain: 0.0,
            oil_supply_response: 0.0,
            oil_opec_symmetry: 0.0,
            oil_seasonality_target: 0.0,
        }
    }
}

/// The MEAN of the VIX target's return spike over a session, given the
/// index's own conditional sigma — the quantity `vix_target_offset` was
/// fitted to cancel.
///
/// # Why an offset existed at all
///
/// The spike is `gain * |r|` on a down session and `-gain_up * r` on an up
/// one. Over a zero-mean return those two do not cancel unless the gains
/// are equal, so an ASYMMETRIC gain injects a standing positive excursion
/// into the level — and `vix_target_offset` was a constant fitted to
/// subtract it. That is a level constant standing in for a first moment
/// somebody could have written down, and it is why the same dial had to be
/// refitted every time the gain moved.
///
/// # The closed form
///
/// For `r ~ N(0, sigma^2)` and gains `g_down`, `g_up`:
///
/// ```text
/// E[spike] = P(r < 0) g_down E[|r| | r < 0] - P(r >= 0) g_up E[r | r >= 0]
///          = 0.5 (g_down - g_up) E|r|
///          = 0.5 (g_down - g_up) sigma sqrt(2 / pi)
/// ```
///
/// so it moves with the market's own volatility instead of standing still,
/// which is the second thing a fitted constant could not do: an offset
/// derived in a calm year is the wrong offset in a violent one.
///
/// # The approximations, stated rather than absorbed
///
/// - **Gaussian.** The model's index carries excess kurtosis about 2.6, so
///   `E|r|` is a per cent or two under the true one and the correction is
///   fractionally small. It is a correction to a term that is itself a few
///   points, so the residual is tenths of a point.
/// - **Unclamped.** `vix_return_clamp` truncates `r` first. At the shipped
///   15 per cent and an index sigma near 1 per cent that is a fifteen-sigma
///   truncation and contributes nothing; a preset that clamps near the
///   index's own sigma would need the truncated moment instead.
/// - **The moment's order.** The exponent below is carried in closed form
///   rather than approximated, but it is the GAUSSIAN absolute moment, so
///   the first bullet's caveat applies to it with more force: a moment of
///   order `p > 1` weights the tail harder than the mean does, and the
///   index's excess kurtosis is therefore understated by more here than at
///   `p = 1`. The residual is still a fraction of a term worth a few
///   points, and it is one-signed: the correction is too SMALL, so the
///   level it leaves is fractionally too high.
///
/// # The exponent, carried rather than assumed away
///
/// The two halves of the excursion no longer share a moment. With the
/// response `scale |r|^p` on the down side and `g_up |r|` on the up side —
/// which is what [`return_spike_for`] does, the exponent being the down
/// side's alone — the identity is
///
/// ```text
/// E[spike] = 0.5 scale E|r|^p - 0.5 g_up E|r|
///
/// E|r|^p   = sigma^p 2^(p/2) Gamma((p + 1) / 2) / sqrt(pi)
/// ```
///
/// the Gaussian absolute moment of order `p`. At `p = 1` that is
/// `sigma sqrt(2/pi)` exactly, because `Gamma(1) = 1`, and the two halves
/// collapse back to `0.5 (g_down - g_up) E|r|`.
///
/// **This is the term that makes the two fixes compose.** The identity's
/// whole claim is that the excursion is cancelled by its own closed form
/// and nothing is left over. Under a power form a correction computed as
/// if the response were linear cancels the wrong quantity, and the error
/// is not constant: it GROWS with the index's own sigma, because
/// `E|r|^p / E|r|` scales as `sigma^(p-1)`. A level correction that is
/// right in a calm year and wrong in a violent one is the defect the
/// fitted offset had, reintroduced by the back door.
pub fn expected_return_spike(sigma_pct: f64, gain: f64, gain_up: f64, exponent: f64) -> f64 {
    if exponent == 1.0 {
        // `E|r| = sigma sqrt(2/pi)` for a zero-mean Gaussian. Written as
        // the reciprocal of `SQRT_TWO_PI` times two so it is visibly the
        // same constant `factors.rs` uses for the tilt's first moment,
        // which is the same integral.
        //
        // The branch is explicit rather than evaluated, for the reason
        // `return_spike_for` gives: neither `pow(x, 1.0)` nor `Gamma(1.0)`
        // is guaranteed to return its exact value on every platform, and a
        // preset that predates the exponent must reproduce to the bit.
        // This is the expression that stood here, in the same operand
        // order.
        return 0.5 * (gain - gain_up) * sigma_pct * (2.0 / SQRT_TWO_PI);
    }
    let e_abs = sigma_pct * (2.0 / SQRT_TWO_PI);
    let e_pow = mathx::pow(sigma_pct, exponent)
        * mathx::pow(2.0, 0.5 * exponent)
        * mathx::tgamma(0.5 * (exponent + 1.0))
        / mathx::sqrt(core::f64::consts::PI);
    0.5 * (gain * e_pow - gain_up * e_abs)
}

/// `sqrt(2 pi)`, as `market::factors` spells it. A literal because `sqrt`
/// is not `const`, and the test below pins it against `mathx::sqrt`.
const SQRT_TWO_PI: f64 = 2.5066282746310002;

/// The VIX target's response to the session return, in points.
///
/// Extracted from the daily update so it can be pinned directly: the
/// claim that a preset predating `vix_return_exponent` reproduces bit for
/// bit is a property of this arithmetic and nothing else, and a test that
/// has to drive a whole economy to reach it is testing the economy.
///
/// The sign convention is the one that stood: a DOWN session (a negative
/// `current`) produces a POSITIVE spike, and an up session a negative one.
///
/// At `exponent == 1.0` this is the shipped expression to the bit -- the
/// same multiply in the same operand order -- and the branch exists for
/// that reason rather than for speed. `pow(x, 1.0)` is not guaranteed to
/// return `x` exactly on every platform, and a preset that predates a dial
/// must not depend on it.
///
/// Above 1.0 the response is CONVEX and `gain` becomes the SCALE of a
/// power law: see [`crate::params::ModelParams::vix_return_exponent`] for
/// the measurement it comes from, the exponent's error bar, and what the
/// up side assumes.
pub fn return_spike_for(current: f64, gain: f64, gain_up: f64, exponent: f64) -> f64 {
    // THE EXPONENT IS THE DOWN SIDE'S ALONE, and that is a measurement
    // rather than a simplification. Fitting the two sides separately on
    // the same 8,960 sessions, same alignment, same estimator:
    //
    //   down   scale 1.0033   exponent 1.1996   R^2 0.9947
    //   up     scale 0.8965   exponent 1.0410   R^2 0.9655
    //
    // The down side is convex; the up side is very nearly LINEAR, and at
    // an R^2 of 0.9655 with a worst bucket missing by 35 per cent on 23
    // sessions, 1.0410 is not distinguishable from 1.0 by this data. So
    // the up side keeps the linear form rather than carrying a fourth
    // decimal the fit does not support, and applying one exponent to
    // both sides -- which is what the first version of this did -- would
    // have made the up side wrong to buy nothing.
    if current >= 0.0 || exponent == 1.0 {
        // An UP session, or the linear special case, in the arithmetic
        // that stood here: same multiply, same operand order, so every
        // preset predating this dial reproduces to the bit.
        if current < 0.0 {
            -current * gain
        } else {
            -current * gain_up
        }
    } else {
        gain * mathx::pow(-current, exponent)
    }
}

/// [`return_spike_for`] with the LEVEL in it: the target's response to the
/// session return can fall, or rise, with the VIX the session opened from.
///
/// ```text
/// down    gain    * |r|^exponent     * vix^(-level_exponent)
/// up     -gain_up * |r|^exponent_up  * vix^(-level_exponent_up)
/// ```
///
/// At `level_exponent` 0.0, `exponent_up` 1.0 and `level_exponent_up` 0.0
/// -- every preset up to pt-v19 -- this is a BRANCH to `return_spike_for`
/// and the general arithmetic is never evaluated, so those presets
/// reproduce to the bit. The measurement behind the form, its error bars
/// and the value each dial derives to are on
/// `ModelParams::vix_return_level_exponent`.
/// The anchor weight at the VIX's level: `1 - a(x) = (1 - a) (K / x')^eta`
/// with `x' = min(x, cap K)` (no cap at 0.0) and, unless `below` is
/// nonzero, `x' >= K` so the weight is the dial at and below the knee `K`.
/// Floored at zero. See [`crate::params::ModelParams::vix_anchor_weight_level`].
pub fn anchor_weight_at_level(a: f64, eta: f64, cap: f64, below: f64, vix: f64, knee: f64) -> f64 {
    if !(vix > 0.0) || !(knee > 0.0) {
        return a;
    }
    let x = if cap != 0.0 { mathx::min(vix, cap * knee) } else { vix };
    let x = if below == 0.0 { mathx::max(x, knee) } else { x };
    let one_minus = (1.0 - a) * mathx::pow(knee / x, eta);
    mathx::max(0.0, 1.0 - one_minus)
}

#[allow(clippy::too_many_arguments)]
pub fn return_spike_at_level(
    current: f64,
    gain: f64,
    gain_up: f64,
    exponent: f64,
    exponent_up: f64,
    level_exponent: f64,
    level_exponent_up: f64,
    vix: f64,
) -> f64 {
    if level_exponent == 0.0 && exponent_up == 1.0 && level_exponent_up == 0.0 {
        return return_spike_for(current, gain, gain_up, exponent);
    }
    if current < 0.0 {
        let level = if level_exponent == 0.0 { 1.0 } else { mathx::pow(vix, -level_exponent) };
        gain * mathx::pow(-current, exponent) * level
    } else if current > 0.0 {
        let level = if level_exponent_up == 0.0 { 1.0 } else { mathx::pow(vix, -level_exponent_up) };
        -gain_up * mathx::pow(current, exponent_up) * level
    } else {
        0.0
    }
}

/// [`expected_return_spike`] for the level-dependent form: the mean of
/// [`return_spike_at_level`] over `r ~ N(0, sigma^2)`, which is what the
/// identity subtracts so the fear excursion is zero-mean.
///
/// ```text
/// E[spike] = 0.5 gain    vix^(-level_exponent)    E|r|^exponent
///          - 0.5 gain_up vix^(-level_exponent_up) E|r|^exponent_up
/// E|r|^q   = sigma^q 2^(q/2) Gamma((q + 1) / 2) / sqrt(pi)
/// ```
///
/// The level factors are constants given the VIX the session opened from,
/// so they pass straight through the expectation; the two absolute moments
/// are the Gaussian ones `expected_return_spike` already carries, with the
/// up side's own order. Same branch as the spike: at the three defaults it
/// IS `expected_return_spike`, evaluated by that function.
#[allow(clippy::too_many_arguments)]
pub fn expected_return_spike_at_level(
    sigma_pct: f64,
    gain: f64,
    gain_up: f64,
    exponent: f64,
    exponent_up: f64,
    level_exponent: f64,
    level_exponent_up: f64,
    vix: f64,
) -> f64 {
    if level_exponent == 0.0 && exponent_up == 1.0 && level_exponent_up == 0.0 {
        return expected_return_spike(sigma_pct, gain, gain_up, exponent);
    }
    let moment = |q: f64| -> f64 {
        mathx::pow(sigma_pct, q)
            * mathx::pow(2.0, 0.5 * q)
            * mathx::tgamma(0.5 * (q + 1.0))
            / mathx::sqrt(core::f64::consts::PI)
    };
    let down = if level_exponent == 0.0 { 1.0 } else { mathx::pow(vix, -level_exponent) };
    let up = if level_exponent_up == 0.0 { 1.0 } else { mathx::pow(vix, -level_exponent_up) };
    0.5 * (gain * down * moment(exponent) - gain_up * up * moment(exponent_up))
}

/// The monthly change in unemployment its cyclical drivers ask for: the
/// phase's trend, Okun's law on `growth` (1 pp of growth below 2% is 0.2 pp
/// a month) and the recovery's hiring above 1% growth. The three terms of
/// the monthly release beside the NAIRU pull and the noise, with the same
/// arithmetic; read by the release under `unemployment_adjustment_half_life`
/// and by the engine to seed the impulse.
pub fn unemployment_drive(unemployment_trend: f64, phase: CyclePhase, growth: f64) -> f64 {
    unemployment_drive_with(unemployment_trend, phase, growth, 0.0)
}

/// Oil inventory's normal level: the middle of the 40 to 60 dead zone in
/// which inventory puts no pressure on the oil price, and the level it
/// opens at. Read only under `ModelParams::oil_inventory_reversion`.
pub const OIL_INVENTORY_NORMAL: f64 = 50.0;

/// The lowest inventory the convenience premium reads
/// (`ModelParams::oil_convenience_yield`): half the normal level, where the
/// premium is `2^k`. Below it the target is pinned at its steepest, so a
/// premium that grows without bound as stocks run out cannot carry the price
/// past what its clamps hold.
pub const OIL_CONVENIENCE_FLOOR: f64 = 25.0;

/// The convenience factor's floor: a quarter of the premium-free price, so
/// a large k on stocks far above normal cannot take the factor to zero or
/// below. At EIA's inventory scale (`oil_inventory_noise_sd` 0.35) it binds
/// only past 1 / (0.75 k) of log deviation, beyond any session measured.
pub const OIL_CONVENIENCE_MIN: f64 = 0.25;

/// The convenience premium's factor on oil's level, `1 - k ln(I / 50)`
/// (at least [`OIL_CONVENIENCE_MIN`]) with `I` read at no lower than
/// [`OIL_CONVENIENCE_FLOOR`] and no higher than 100. With `var` above 0 it is the factor's expectation over an
/// inventory normal about `inventory` with that variance, as the forecast
/// needs it: the factor is convex, so its value at the expected inventory is
/// below its expected value. The expectation is a trapezoid sum over six
/// standard deviations either side at a tenth of one, which the floor's kink
/// costs nothing to (a Gauss-Hermite rule, exact for smooth polynomials,
/// was 0.6 per cent off at the floor).
pub fn oil_convenience_factor(kappa: f64, inventory: f64, var: f64) -> f64 {
    // LINEAR in log inventory, the convenience yield of Working (1949) and
    // Gibson and Schwartz (Journal of Finance 45(3), 1990), in which log
    // futures are linear in it: its expectation over inventory's spread is
    // its value at the expected inventory, so it puts no standing tilt on
    // the curve. The exponential tried first, exp(-k ln(I / 50)), is convex,
    // and its expectation rose with the horizon as inventory's spread
    // widened: a standing contango of k^2 dv / 2, about 1.7 per cent between
    // contracts 1 and 4 at k 3.6, which held OF1 under its band. Floored at
    // a quarter of the price, where it is never read at EIA's inventory
    // scale.
    let at = |i: f64| {
        let i = clamp(i, OIL_CONVENIENCE_FLOOR, 100.0);
        mathx::max(OIL_CONVENIENCE_MIN, 1.0 - kappa * mathx::log(i / OIL_INVENTORY_NORMAL))
    };
    if !(var > 0.0) {
        return at(inventory);
    }
    let sd = mathx::sqrt(var);
    let (mut total, mut mass) = (0.0, 0.0);
    for i in -60..=60 {
        let z = i as f64 / 10.0;
        let w = mathx::exp(-0.5 * z * z);
        total += w * at(inventory + z * sd);
        mass += w;
    }
    total / mass
}

/// The oil price at which inflation takes no oil term under
/// `ModelParams::oil_inflation_passthrough`: the level oil's reversion
/// target takes at the 2 per cent trend growth the Okun term pivots on,
/// `OIL_BASELINE + 3 * 2`.
pub const OIL_PASSTHROUGH_ANCHOR: f64 = OIL_BASELINE + 3.0 * 2.0;

/// [`unemployment_drive`] under `ModelParams::unemployment_okun_coefficient`.
/// At `okun` 0.0 it is the shipped arithmetic, operation for operation. Off
/// zero it is the phase's trend and `(2 - growth) * okun / 12`, Okun's law
/// read as the annual relation it states, with no recovery term.
pub fn unemployment_drive_with(
    unemployment_trend: f64,
    phase: CyclePhase,
    growth: f64,
    okun: f64,
) -> f64 {
    if okun != 0.0 {
        return unemployment_trend * 0.3 + (2.0 - growth) * (okun / 12.0);
    }
    let gdp_effect = (2.0 - growth) * 0.20;
    let recovery_effect = if (phase == CyclePhase::Expansion || phase == CyclePhase::Recovery)
        && growth > 1.0
    {
        -growth * 0.08
    } else {
        0.0
    };
    unemployment_trend * 0.3 + gdp_effect + recovery_effect
}

/// The close's VIX and its three yields (the 10-year, the 2-year and the
/// corporate yield between meetings), written into `new_state` from
/// `economy`: the block of [`update_economy_daily`] from `// ── VIX` to the
/// corporate yield, factored out so [`project_close_yields`] runs the same
/// arithmetic. `shock_gdp_impact` is the step's shock aggregate. Draws
/// exactly what the block drew, in the same order.
pub fn vix_and_yields(
    economy: &EconomyState,
    inputs: &DailyInputs,
    new_state: &mut EconomyState,
    shock_gdp_impact: f64,
    rng: &mut impl Rng,
) {
    new_state.vix = vix_close(economy, inputs, shock_gdp_impact, rng);
    let volatility = inputs.volatility;
    close_yields(economy, inputs, new_state, volatility, rng);
}

/// The identity's target level, before the session's return excursion and
/// the fear memory: the variance read-back held against the anchor
/// (`vix_anchor_weight`, `vix_anchor_memory`, the weight's level law read at
/// the VIX `vix`). The expressions [`vix_close`] steps to, in the same order.
pub fn identity_target_level(inputs: &DailyInputs, vix: f64) -> f64 {
    // THE ANCHOR IN THE TARGET, not in the rate: a geometric blend of the
    // read-back and `L * anchor`. The VIX still reverts at `mr`, so its
    // lag-one persistence is the loop's and not `mr + kappa`'s. Guarded,
    // so at 0.0 the target is the read-back bit for bit. See
    // `ModelParams::vix_anchor_weight`.
    // The centre and the level law are guarded, so with both at 0.0 the
    // weight and the reference are the dial and `L * anchor` exactly.
    let centre_level = if inputs.vix_anchor_centre != 0.0 {
        inputs.vix_anchor_level * mathx::exp(-inputs.vix_anchor_centre)
    } else {
        inputs.vix_anchor_level
    };
    let weight = if inputs.vix_anchor_weight != 0.0 && inputs.vix_anchor_weight_level != 0.0 {
        // The knee is where the held read-back's elasticity reaches the
        // level the law holds, a property of the VIX's ABSOLUTE level; the
        // switch takes the slow regime level out of it. Guarded, so at
        // 0.0 the knee reads `vix_anchor_level` exactly as it did.
        let knee_base = if inputs.vix_anchor_weight_level_knee_fixed != 0.0 {
            inputs.vix_anchor_level_fixed
        } else {
            inputs.vix_anchor_level
        };
        let knee = if inputs.vix_anchor_weight_level_knee != 0.0 {
            knee_base * mathx::exp(-inputs.vix_anchor_weight_level_knee)
        } else {
            knee_base
        };
        anchor_weight_at_level(
            inputs.vix_anchor_weight,
            inputs.vix_anchor_weight_level,
            inputs.vix_anchor_weight_level_cap,
            inputs.vix_anchor_weight_level_below,
            vix,
            knee,
        )
    } else {
        inputs.vix_anchor_weight
    };
    if inputs.vix_anchor_weight != 0.0 && inputs.vix_anchor_memory != 0.0 {
        // Against the slow memory: today's move passes in full. The
        // memory is kept against the centre by the engine.
        inputs.vix_implied_from_market * mathx::exp(-weight * inputs.vix_anchor_slow)
    } else if inputs.vix_anchor_weight != 0.0 {
        let a = weight;
        mathx::exp((1.0 - a) * mathx::log(inputs.vix_implied_from_market)
            + a * mathx::log(centre_level))
    } else {
        inputs.vix_implied_from_market
    }
}

/// The fear memory one close on: `f' = 0.5^(1 / H) f + k ln(vix / (T exp(f)))`,
/// with `T` the identity's target level ([`identity_target_level`]) on the
/// close's inputs and `vix` the VIX before the step. See
/// `ModelParams::vix_fear_uptake`. Called only with the uptake non-zero.
pub fn advance_vix_fear(fear: f64, inputs: &DailyInputs, vix: f64, half_life: f64) -> f64 {
    let level = identity_target_level(inputs, vix);
    if !(level > 0.0) || !(vix > 0.0) {
        return fear;
    }
    let excursion = mathx::log(vix / level) - fear;
    mathx::pow(0.5, 1.0 / half_life) * fear + inputs.vix_fear_uptake * excursion
}

/// The close's VIX from `economy` on `inputs`: the VIX half of
/// [`vix_and_yields`], which writes it before the yields read it. Draws
/// exactly what that half drew, in the same order. The forecast
/// (`forecast_horizon_sessions`) runs it alone.
pub fn vix_close(
    economy: &EconomyState,
    inputs: &DailyInputs,
    shock_gdp_impact: f64,
    rng: &mut impl Rng,
) -> f64 {
    let volatility = inputs.volatility;
    let day = inputs.game_day;
    let cal = inputs.macro_calendar;
    let year = cal.days_per_year;
    let day_of_year = ((day - 1) % year) + 1;
    // ── VIX ───────────────────────────────────────────────────────────────
    // Pure JS since "Cycle 71". `wasmCalculateVixTarget` is imported by
    // the economy module and never called — an import-driven worklist would
    // invent work here.
    let phase_vix = match economy.cycle_phase {
        CyclePhase::Expansion => 14.0,
        CyclePhase::Peak => 18.0,
        CyclePhase::Contraction => 25.0,
        CyclePhase::Trough => 22.0,
        CyclePhase::Recovery => 16.0,
    };
    // THE LEVEL. Under the identity it is the index's own conditional
    // variance in VIX points and the table above is not read at all: the
    // business cycle reaches the VIX through the variance processes or not
    // at all. At 0.0 the branch is not taken and every preset before pt-v19
    // reproduces bit for bit. See `ModelParams::vix_level_identity`.
    //
    // THE CLOCK THE CLUSTERING RUNS ON. Those five constants move on a
    // multi-year cycle, so volatility regimes here are years long and a
    // one-year window contains no regime change at all. At amplitude 1.0
    // this is the shipped arithmetic exactly. See §71.
    let identity_level = inputs.vix_level_identity != 0.0;
    let mut target_vix = if identity_level {
        let level = identity_target_level(inputs, economy.vix);
        // THE FEAR MEMORY: the target carries the share of the VIX's own
        // excursion the memory has taken up (`advance_vix_fear`). Guarded,
        // so at 0.0 the target is the anchor's bit for bit. See
        // `ModelParams::vix_fear_uptake`.
        if inputs.vix_fear_uptake != 0.0 {
            level * mathx::exp(inputs.vix_fear)
        } else {
            level
        }
    } else if inputs.vix_cycle_amplitude == 1.0 {
        phase_vix
    } else {
        VIX_PHASE_MEAN + inputs.vix_cycle_amplitude * (phase_vix - VIX_PHASE_MEAN)
    };
    // The CURRENT day's return, not the previous one, so VIX reacts same-day
    // and the negative correlation is real.
    let clamp_vix = inputs.vix_return_clamp;
    // WHICH RETURN THE VIX IS AFRAID OF. `market_return_pct` is the final
    // tick's cap-weighted move, so the fear channel has been reading the
    // closing minute rather than the session: a -7.87% day moved the VIX
    // +0.15 points and the correlation between the day's return and the
    // next day's VIX change is -0.065 even with the gain at 5000 (§70). At
    // source 0.0 this branch is not taken and every preset reproduces.
    let driving_return = if identity_level {
        // THE SESSION, always. The excursion below is made zero-mean
        // against the session's own conditional sigma, and the same
        // correction applied to a closing MINUTE would be twenty times the
        // quantity it is correcting. So the identity does not read
        // `vix_return_source`; a preset that turns it on gets the day.
        inputs.market_day_return_pct
    } else if inputs.vix_return_source == 0.0 {
        inputs.market_return_pct
    } else {
        let s = inputs.vix_return_source;
        (1.0 - s) * inputs.market_return_pct + s * inputs.market_day_return_pct
    };
    let current_mkt_ret_vix = mathx::max(-clamp_vix, mathx::min(clamp_vix, driving_return));
    let return_spike = return_spike_at_level(
        current_mkt_ret_vix, inputs.vix_return_gain,
        inputs.vix_return_gain_up, inputs.vix_return_exponent,
        inputs.vix_return_exponent_up, inputs.vix_return_level_exponent,
        inputs.vix_return_level_exponent_up, economy.vix);
    let inflation_adj = mathx::max(0.0, (economy.inflation_rate - 3.0) * 0.2);
    let shock_adj = shock_gdp_impact.abs() * 2.0;
    target_vix += mathx::min(
        inputs.vix_target_shock_cap,
        return_spike + inflation_adj + shock_adj,
    );

    // THE EXCURSION'S OWN MEAN, which is what the offset was for.
    //
    // An asymmetric gain over a zero-mean return injects a standing
    // positive excursion, and `vix_target_offset` was a number fitted to
    // cancel it. Under the identity it is cancelled by its own closed form
    // instead, computed each day from the index's conditional sigma: see
    // `expected_return_spike`. Nothing is fitted and nothing is left over.
    if identity_level {
        target_vix -= expected_return_spike_at_level(
            inputs.vix_index_sigma_pct,
            inputs.vix_return_gain,
            inputs.vix_return_gain_up,
            inputs.vix_return_exponent,
            inputs.vix_return_exponent_up,
            inputs.vix_return_level_exponent,
            inputs.vix_return_level_exponent_up,
            economy.vix,
        );
    }

    // Earnings-season bump. The 30.44 is a mean month length, so this is not
    // the same day-of-month the month-start blocks above use.
    //
    // Not read under the identity: half a point on the first half of a
    // month is a level constant, and the level is the variance now.
    let month_len = cal.mean_month_len();
    let earnings_month_vix = (((day_of_year - 1) % year) as f64 / month_len).floor() + 1.0;
    let day_of_month_vix = day_of_year as f64 - ((earnings_month_vix - 1.0) * month_len).floor();
    if !identity_level && day_of_month_vix <= cal.scale_days(15) as f64 {
        target_vix += 0.5;
    }

    // THE FEEDBACK LOOP, WHICH RAN ONE WAY. The VIX sets the market
    // factor's variance target and the market's own volatility never came
    // back, so the VIX was a function of the business cycle and not of the
    // market: it tracked trailing realised volatility at +0.28 against a
    // real +0.82, and never once crossed its own crisis threshold in a year
    // A constant level on the target, before the blend, so it scales by
    // `1 - weight` exactly as the phase anchor does. It exists because an
    // ASYMMETRIC return gain injects a standing positive excursion, and the
    // offset cancels it. Guarded rather than added: `x + 0.0` is not a no-op
    // on a negative zero, which is the same reason `apply_jumps` guards its
    // own total, and a dial that ships inert must leave the state it does not
    // touch bit-identical.
    if !identity_level && inputs.vix_target_offset != 0.0 {
        target_vix += inputs.vix_target_offset;
    }

    // (§68). At weight zero this branch is not taken and every preset
    // reproduces bit for bit.
    // Not read under the identity: the WHOLE target is the read-back, so
    // there is nothing to blend it with.
    if !identity_level && inputs.vix_realised_vol_weight != 0.0 {
        let w = inputs.vix_realised_vol_weight;
        target_vix = (1.0 - w) * target_vix + w * inputs.vix_implied_from_market;
    }

    // Asymmetric reversion: fear arrives at the full rate and decays at
    // `vix_decay_ratio` of it. At 1.0 the branch collapses to the shipped
    // arithmetic exactly (same multiply, same operand order).
    let vix_mr = if target_vix < economy.vix {
        inputs.vix_mean_reversion * inputs.vix_decay_ratio
    } else {
        inputs.vix_mean_reversion
    };
    // Exogenous fear events (round 134). STRICTLY no draws at zero: any
    // draw here would shift every later draw in the economy schedule and
    // break bit-reproduction of recorded runs.
    //
    // THE VIX'S OWN INNOVATION. The shipped noise is 0.15 points a day,
    // under one per cent of the level; the tape's VIX, once its response
    // to the index return is removed, moves by 3.3 per cent of its level
    // a session plus a part that scales with the session's own size. See
    // `ModelParams::vix_innovation_sigma`. With both innovation dials at
    // 0.0 the scale is the expression that stood here, `0.15 * volatility`,
    // and the draw is the same draw.
    let innovation_on = inputs.vix_innovation_sigma != 0.0
        || inputs.vix_innovation_return_sigma != 0.0;
    let vix_noise_sd = if innovation_on {
        let s0 = inputs.vix_innovation_sigma;
        let sr = inputs.vix_innovation_return_sigma * current_mkt_ret_vix;
        economy.vix * mathx::sqrt(s0 * s0 + sr * sr)
    } else {
        0.15 * volatility
    };
    let fear_jump = if inputs.vix_jump_intensity != 0.0
        || inputs.vix_jump_return_intensity != 0.0
    {
        // The arrival rate per year, with the part that rises on a down
        // session added only when that dial is set, so a preset carrying
        // the constant rate alone computes exactly what it did.
        let rate = if inputs.vix_jump_return_intensity != 0.0 {
            inputs.vix_jump_intensity
                + inputs.vix_jump_return_intensity * mathx::max(0.0, -current_mkt_ret_vix)
        } else {
            inputs.vix_jump_intensity
        };
        let p_daily = rate / 252.0;
        if rng.next_f64() < p_daily {
            // Exponential magnitude: mean `vix_jump_scale` points, or mean
            // `vix_jump_level_scale` innovation scales.
            let draw = -mathx::log(mathx::max(rng.next_f64(), 1e-12));
            if inputs.vix_jump_level_scale != 0.0 {
                let unit = if innovation_on { vix_noise_sd } else { economy.vix };
                inputs.vix_jump_level_scale * unit * draw
            } else {
                inputs.vix_jump_scale * draw
            }
        } else {
            0.0
        }
    } else {
        0.0
    };
    let stepped_vix = economy.vix
        + (target_vix - economy.vix) * vix_mr
        + random_normal(rng, 0.0, vix_noise_sd)
        + fear_jump;
    // THE ANCHOR THE LOOP LACKS. Under the identity the VIX reverts to the
    // read-back and the read-back reverts to the VIX; neither reverts to a
    // level, so the pair's only anchor is that its static gain is under one.
    // This term is the third thing: a slow pull toward the identity's own
    // derived anchor, times the regime level's multiplier. Guarded rather
    // than added, for the reason `vix_target_offset` is guarded -- `x + 0.0`
    // is not a no-op on a negative zero -- so at 0.0 the sum above is the
    // literal expression that stood here and every preset reproduces.
    // See `ModelParams::vix_anchor_reversion`.
    let stepped_vix = if inputs.vix_anchor_reversion != 0.0 {
        stepped_vix + inputs.vix_anchor_reversion * (inputs.vix_anchor_level - economy.vix)
    } else {
        stepped_vix
    };
    clamp(stepped_vix, 10.0, inputs.vix_ceiling)
}

/// The yields half of [`vix_and_yields`], after the VIX is written.
fn close_yields(
    economy: &EconomyState,
    inputs: &DailyInputs,
    new_state: &mut EconomyState,
    volatility: f64,
    rng: &mut impl Rng,
) {
    // ── Treasury yields ───────────────────────────────────────────────────
    let debt_premium = mathx::max(0.0, (economy.government_debt_to_gdp - 100.0) * 0.002);
    let term_premium_10y =
        1.0 + mathx::max(0.0, (economy.inflation_rate - 2.0) * 0.3) + debt_premium;
    // THE TREASURY HAVEN (`treasury_haven_gain`): the term premium falls
    // with the VIX above 20 while inflation is under 4, so the 10-year
    // rallies through a stressed month in a low-inflation regime. Guarded,
    // so at 0.0 the premium is the expression that stood.
    //
    // WITH THE FLIGHT TO QUALITY BELOW. `flight_to_quality_gain` moves the
    // yield by an increment, which this anchor's 5 per cent pull erases in
    // about 60 sessions; the haven moves the anchor, so it lasts while the
    // VIX does. Both push the stock-bond correlation negative at low
    // inflation (the flight to quality below 3, the haven below 4), so the
    // two are fitted together: see `ModelParams::treasury_haven_gain`.
    // Here in the step the live rate mark projects too
    // (`rate_intraday_live`), so the projection carries the haven and the
    // priced put below.
    let term_premium_10y = if inputs.yields.haven_gain != 0.0
        && economy.inflation_rate < crate::economy::central_bank::FED_PUT_INFLATION_CEILING
    {
        term_premium_10y - haven_term_cut(inputs.yields.haven_gain, new_state.vix)
    } else {
        term_premium_10y
    };
    // THE PRICED FED PUT (`treasury_put_pricing`): the 10-year's anchor, and
    // the 2-year's formula, read the policy rate the market expects after
    // the next meeting rather than the one standing. Guarded, as above.
    let fed_rate_for_10y = if inputs.yields.priced_put != 0.0 {
        new_state.federal_funds_rate - inputs.yields.priced_put
    } else {
        new_state.federal_funds_rate
    };
    // THE PRICED PATH (`treasury_path_pricing`): the anchor, and the 2-year's
    // formula, read the rate the market expects the cycle to reach. Guarded,
    // as above.
    let fed_rate_for_10y = if inputs.yields.priced_path != 0.0 {
        fed_rate_for_10y + inputs.yields.priced_path
    } else {
        fed_rate_for_10y
    };
    // THE ANTICIPATED MEETING (`policy_anticipation`): the anchor and the
    // 2-year's formula read the next decision the market expects, as far as
    // it is priced tonight. Guarded, as above.
    let fed_rate_for_10y = if inputs.yields.priced_anticipation != 0.0 {
        fed_rate_for_10y + inputs.yields.priced_anticipation
    } else {
        fed_rate_for_10y
    };
    let current_10y = new_state.treasury_yield_10y;
    // THE DAMPED PASS-THROUGH (`treasury_policy_damping`): the 10-year's
    // anchor reads the priced rate pulled toward the neutral rate; the
    // 2-year's formula below reads it undamped. Guarded, as above.
    // The damped rate is the ladder's (the policy rate plus what the Fed put
    // owes) and the priced path: the put's overlay, owed and priced, passes
    // through whole.
    let rate_for_10y_anchor = if inputs.yields.rate_damping != 0.0 {
        let ladder = new_state.federal_funds_rate + new_state.fed_put_owed + inputs.yields.priced_path;
        let ladder = if inputs.yields.priced_anticipation != 0.0 {
            ladder + inputs.yields.priced_anticipation
        } else {
            ladder
        };
        fed_rate_for_10y - inputs.yields.rate_damping * (ladder - TREASURY_NEUTRAL_RATE)
    } else {
        fed_rate_for_10y
    };

    // D5, decided: KEEP the draw. In production `wasm10Y ?? (…)` short-
    // circuits and this normal is never taken — a consequence of `??`, not a
    // modelling choice. Dropping it would also flip the Box-Muller spare
    // parity for every later normal in the engine.
    new_state.treasury_yield_10y = clamp(
        current_10y
            + (rate_for_10y_anchor + term_premium_10y - current_10y) * 0.05
            + random_normal(rng, 0.0, inputs.yields.treasury_10y_noise * volatility),
        0.5,
        12.0,
    );
    if inputs.yields.treasury_10y_pinned {
        new_state.treasury_yield_10y = current_10y;
    }
    // THE 2-YEAR. The formula has no noise of its own: between meetings the
    // policy rate is flat, so the 2-year moved by 0.15 of the 10-year's
    // noise, 0.46 bp a session against the tape's 5.2. Off zero it is its
    // own process, pulled at the 10-year's rate toward the formula, with
    // its own noise: one more normal on the economy stream, taken only
    // under the dial.
    let target_2y = fed_rate_for_10y * 0.85 + new_state.treasury_yield_10y * 0.15;
    let own_2y = inputs.yields.treasury_2y_noise != 0.0;
    new_state.treasury_yield_2y = if own_2y {
        clamp(
            economy.treasury_yield_2y
                + (target_2y - economy.treasury_yield_2y) * 0.05
                + random_normal(rng, 0.0, inputs.yields.treasury_2y_noise * volatility),
            0.0,
            12.0,
        )
    } else {
        target_2y
    };
    if inputs.yields.treasury_2y_pinned {
        new_state.treasury_yield_2y = economy.treasury_yield_2y;
    }

    // Bond-stock correlation regime: inflation sets the sign.
    //
    // WHICH RETURN, AND WHEN. The shipped rule reads the PREVIOUS session's
    // closing-minute return behind a 0.5 per cent gate, which that return
    // never crosses, so the rule has never fired and the curve has no
    // stock-bond correlation at all. `flight_to_quality_day` 1.0 reads THIS
    // session's index return (the step runs after the close, so the yield
    // it writes is the session's own close) with no gate: the relation is
    // linear in the move.
    let (prev_mkt_ret, ftq_gate) = if inputs.yields.flight_to_quality_day != 0.0 {
        (inputs.market_day_return_pct, 0.0)
    } else {
        (economy.previous_day_market_return, 0.5)
    };
    let ftq_gain = inputs.yields.flight_to_quality_gain;
    if prev_mkt_ret.abs() > ftq_gate {
        let bond_stock_yield_shift = if economy.inflation_rate > 4.0 {
            // Positive correlation: stocks down, yields up.
            -prev_mkt_ret * ftq_gain
        } else if economy.inflation_rate < 3.0 {
            // Flight to quality.
            prev_mkt_ret * ftq_gain
        } else {
            0.0
        };
        if !inputs.yields.treasury_10y_pinned {
            new_state.treasury_yield_10y = clamp(
                new_state.treasury_yield_10y + bond_stock_yield_shift,
                0.5,
                12.0,
            );
        }
        if !inputs.yields.treasury_2y_pinned {
            new_state.treasury_yield_2y = if own_2y {
                clamp(new_state.treasury_yield_2y + bond_stock_yield_shift, 0.0, 12.0)
            } else {
                new_state.federal_funds_rate * 0.85 + new_state.treasury_yield_10y * 0.15
            };
        }
    }

    // CREDIT'S LEVERAGE TERM (`corporate_spread_equity_gain`): the index's
    // log fall below its own slow average, stepped on the session's return
    // from the last close. Written whatever the pins, so the meeting's
    // re-anchor reads it too; nothing runs with the gain at 0.0 unless the
    // cycle's hazard (`cycle_equity_hazard`) reads the gap, which the engine
    // signals with a decay set and the gain at 0.0.
    let equity_gain = inputs.yields.spread_equity_gain;
    let (gap_before, gap_after) = if equity_gain != 0.0 || inputs.yields.spread_equity_decay != 0.0 {
        let r = mathx::log(mathx::max(1.0 + inputs.market_day_return_pct / 100.0, 1e-6));
        let g = inputs.yields.spread_equity_decay * (economy.spread_equity_gap - r);
        new_state.spread_equity_gap = g;
        (economy.spread_equity_gap, g)
    } else {
        (0.0, 0.0)
    };

    // THE CORPORATE YIELD BETWEEN MEETINGS. It was written only at a
    // central-bank meeting, so fair value's discount rate, and an IG bond
    // priced off it, sat still for six weeks at a time. Off zero it moves
    // every session by the 10-year's move and by the meeting formula's own
    // VIX slope (2 bp a point, times the cycle phase's multiplier) on the
    // session's VIX change: increments, so a scenario's write to the level
    // survives, and the next meeting re-anchors the level to the formula.
    //
    // A PINNED VIX TAKES NO VIX TERM. When a caller wrote the VIX before the
    // session (`Scenario().hold(vix=...)`, `pin_macro(vix=...)`), the close
    // moves it by the VIX law's reversion from the written level, and the
    // next morning's pin writes the level back without passing through here.
    // Charging the close's move to the credit spread then ratchets it: under
    // hold(vix=45) the corporate yield fell 2.81 -> 2.42 per cent in five
    // sessions with the 10-year flat. It also carried the market return,
    // which moves the close's VIX, into every name's discount rate, so two
    // worlds that differ only by one agent's trades no longer agreed on the
    // names it never touched, against the advice to pin the VIX for exactly
    // that. The 10-year's own move still passes through, and with it the
    // flight to quality (`flight_to_quality_day`), which reads the session's
    // index return: that is a second path from one agent's flow to names it
    // never touched, which a VIX pin does not hold (`tca.Execution.moved`).
    //
    // A PINNED CORPORATE YIELD HOLDS THROUGH THE CLOSE. A caller that wrote
    // the level wants that level for the session and the night after it,
    // which is what every preset without the daily move gives: without this
    // the close moved it by the 10-year's change and the next morning's pin
    // put it back, so it was never the pinned value overnight.
    if inputs.yields.corporate_yield_daily != 0.0 && !inputs.yields.corporate_pinned {
        let cycle_spread_multiplier =
            crate::economy::central_bank::spread_multiplier_of(economy.cycle_phase);
        // THE PRICED MULTIPLIER (`cycle_nowcast_accuracy`,
        // `corporate_spread_cycle`): the move is the meeting formula's whole
        // change over the session, the VIX's and the multiplier's, so the
        // level stays on the formula and the next meeting finds it there.
        // A pinned VIX still takes no VIX term; the multiplier's change
        // passes through at the VIX written.
        //
        // THE VIX SLOPE'S CUT AND CREDIT'S LEVERAGE TERM
        // (`corporate_spread_vix_cut`, `corporate_spread_equity_gain`): the
        // formula's slope scaled and its base carrying `gain * gap`, on both
        // sides of the change. A VIX pin takes the VIX out, not the index.
        // Guarded, so with both at 0.0 the expressions are the ones that
        // stood.
        let spread_dials = inputs.yields.spread_vix_cut != 0.0 || equity_gain != 0.0;
        let vix_term = if let Some((m0, m1)) = inputs.yields.spread_multiplier {
            let vix_close = if inputs.yields.vix_pinned { economy.vix } else { new_state.vix };
            if spread_dials {
                let cut = inputs.yields.spread_vix_cut;
                crate::economy::central_bank::spread_formula_with(
                    vix_close, m1, cut, equity_gain * gap_after)
                    - crate::economy::central_bank::spread_formula_with(
                        economy.vix, m0, cut, equity_gain * gap_before)
            } else {
                crate::economy::central_bank::spread_formula(vix_close, m1)
                    - crate::economy::central_bank::spread_formula(economy.vix, m0)
            }
        } else if spread_dials {
            let vix_part = if inputs.yields.vix_pinned {
                0.0
            } else {
                0.02 * (1.0 - inputs.yields.spread_vix_cut)
                    * cycle_spread_multiplier * (new_state.vix - economy.vix)
            };
            vix_part + equity_gain * cycle_spread_multiplier * (gap_after - gap_before)
        } else if inputs.yields.vix_pinned {
            0.0
        } else {
            0.02 * cycle_spread_multiplier * (new_state.vix - economy.vix)
        };
        let moved = clamp(
            (new_state.treasury_yield_10y - economy.treasury_yield_10y) + vix_term,
            -CORPORATE_DAILY_MOVE_CAP,
            CORPORATE_DAILY_MOVE_CAP,
        );
        new_state.corporate_bond_yield = mathx::max(
            economy.corporate_bond_yield + moved,
            new_state.treasury_yield_10y + crate::economy::central_bank::CORPORATE_SPREAD_FLOOR,
        );
    }
}

/// A draw source at the means: every normal is 0.0 and every uniform sits
/// just under one, so no event fires (a VIX jump, an OPEC move). For a
/// projection of the close; not an engine stream, and nothing it returns is
/// kept.
pub struct MeanDraws;

impl Rng for MeanDraws {
    fn next_f64(&mut self) -> f64 {
        1.0 - f64::EPSILON
    }
    fn next_normal(&mut self) -> f64 {
        0.0
    }
}

/// The push inventory gives the oil price a day: 0.08 a unit below 40 and
/// above 60, the dead zone between them.
pub fn inventory_pressure(inventory: f64) -> f64 {
    let mut pressure = 0.0;
    if inventory < 40.0 {
        pressure = (40.0 - inventory) * 0.08;
    } else if inventory > 60.0 {
        pressure = (60.0 - inventory) * 0.08;
    }
    pressure
}

/// [`inventory_pressure`]'s expectation when inventory is normal with mean
/// `mean` and sd `sd`; at `sd` 0.0 the push at `mean`. For the forecast
/// (`forecast_horizon_sessions`).
/// The oil price's bounds: the standing 35 and 150, or `oil_price_floor`
/// and `oil_price_ceiling` where set. Branches, so 0.0 is the literal.
pub fn oil_bounds(floor: f64, ceiling: f64) -> (f64, f64) {
    (if floor == 0.0 { 35.0 } else { floor }, if ceiling == 0.0 { 150.0 } else { ceiling })
}

/// The expectation of a normal price `N(mean, sd^2)` truncated at the oil
/// price's bounds `(lo, hi)`: the clamped variable's mean, in closed form,
/// `lo P(below) + hi P(above) + mean P(between) + sd (phi(a) - phi(b))`.
/// `sd` at or under 0 is the clamped mean itself.
pub fn oil_clamped_expectation(mean: f64, sd: f64, (lo, hi): (f64, f64)) -> f64 {
    if !(sd > 0.0) {
        return clamp(mean, lo, hi);
    }
    let (a, b) = ((lo - mean) / sd, (hi - mean) / sd);
    let pdf = |x: f64| mathx::exp(-0.5 * x * x) / mathx::sqrt(2.0 * std::f64::consts::PI);
    let cdf = |x: f64| 0.5 * mathx::erfc(-x / std::f64::consts::SQRT_2);
    let (fa, fb) = (cdf(a), cdf(b));
    lo * fa + hi * (1.0 - fb) + mean * (fb - fa) + sd * (pdf(a) - pdf(b))
}

/// The oil price's spread is right-skewed (the long factor, the premium and
/// the dollar each multiply its level), so the forecast reads it as
/// LOGNORMAL with mean `mean` and log variance `log_var`: ln S ~ N(m, v),
/// m = ln mean - v/2. Its expectation censored at the bounds, in closed form:
/// `lo P(S < lo) + hi P(S > hi) + e^{m + v/2} [Phi(c_hi) - Phi(c_lo)]`,
/// `c = (ln b - m - v) / sqrt v`.
pub fn oil_lognormal_censored(mean: f64, log_var: f64, (lo, hi): (f64, f64)) -> f64 {
    if !(log_var > 0.0) || !(mean > 0.0) {
        return clamp(mean, lo, hi);
    }
    let cdf = |x: f64| 0.5 * mathx::erfc(-x / std::f64::consts::SQRT_2);
    let sd = mathx::sqrt(log_var);
    let m = mathx::log(mean) - 0.5 * log_var;
    let (l_lo, l_hi) = (mathx::log(lo), mathx::log(hi));
    let p_lo = cdf((l_lo - m) / sd);
    let p_hi = 1.0 - cdf((l_hi - m) / sd);
    let inside = mean * (cdf((l_hi - m - log_var) / sd) - cdf((l_lo - m - log_var) / sd));
    lo * p_lo + hi * p_hi + inside
}

/// The same lognormal price's expected log truncation at the bounds,
/// `E[ln(clamp(S) / S)] = -E[(Y - ln hi)^+] + E[(ln lo - Y)^+]`, Y = ln S, in
/// closed form: the normal's partial expectations in log space.
pub fn oil_lognormal_log_truncation(mean: f64, log_var: f64, (lo, hi): (f64, f64)) -> f64 {
    if !(log_var > 0.0) || !(mean > 0.0) {
        return 0.0;
    }
    let pdf = |x: f64| mathx::exp(-0.5 * x * x) / mathx::sqrt(2.0 * std::f64::consts::PI);
    let cdf = |x: f64| 0.5 * mathx::erfc(-x / std::f64::consts::SQRT_2);
    let sd = mathx::sqrt(log_var);
    let m = mathx::log(mean) - 0.5 * log_var;
    let (a, b) = (mathx::log(hi), mathx::log(lo));
    let za = (a - m) / sd;
    let zb = (b - m) / sd;
    let over = sd * pdf(za) + (m - a) * (1.0 - cdf(za));
    let under = sd * pdf(zb) + (b - m) * cdf(zb);
    under - over
}

/// The expected log truncation of a normal price `N(mean, sd^2)` at the
/// oil price's bounds, `E[ln(clamp(S) / S)]`: negative where the ceiling is in
/// reach, positive where the floor is. Under the long factor the close adds
/// each session's truncation to the factor (`EconomyState::oil_bound_log_ratio`),
/// so the forecast adds this expectation to the factor's expected path. A
/// trapezoid sum over six standard deviations at a tenth of one, the price
/// read at no lower than a hundredth of its mean.
pub fn oil_expected_log_truncation(mean: f64, sd: f64, (lo, hi): (f64, f64)) -> f64 {
    if !(sd > 0.0) || !(mean > 0.0) {
        return 0.0;
    }
    let (mut total, mut mass) = (0.0, 0.0);
    for i in -60..=60 {
        let z = i as f64 / 10.0;
        let w = mathx::exp(-0.5 * z * z);
        let x = mathx::max(mean + sd * z, 0.01 * mean);
        total += w * mathx::log(clamp(x, lo, hi) / x);
        mass += w;
    }
    total / mass
}

/// Oil inventory's daily noise sd at a volatility of 1.0: the standing 0.5,
/// or `oil_inventory_noise_sd` where set. A branch, so 0.0 is the literal.
pub fn inventory_noise(inputs: &DailyInputs) -> f64 {
    if inputs.oil_inventory_noise_sd == 0.0 {
        0.5
    } else {
        inputs.oil_inventory_noise_sd
    }
}

pub fn inventory_pressure_expected(mean: f64, sd: f64) -> f64 {
    if !(sd > 0.0) {
        return inventory_pressure(mean);
    }
    // E[(a - X)+] for X ~ N(mean, sd^2), and E[(X - b)+] by symmetry.
    let shortfall = |a: f64, x_mean: f64| {
        let d = (a - x_mean) / sd;
        let cdf = 0.5 * mathx::erfc(-d / std::f64::consts::SQRT_2);
        let pdf = mathx::exp(-0.5 * d * d) / mathx::sqrt(2.0 * std::f64::consts::PI);
        (a - x_mean) * cdf + sd * pdf
    };
    0.08 * (shortfall(40.0, mean) - shortfall(-60.0, -mean))
}

/// The dollar's daily drift per VIX point above `usd_crisis_vix_threshold`.
pub const USD_SAFE_HAVEN_GAIN: f64 = 0.05;

/// The oil price OPEC's rule defends, and the distance from it past which
/// a decision is a cut or a raise rather than a small adjustment.
const OPEC_TARGET: f64 = 80.0;
const OPEC_BAND: f64 = 10.0;

/// OPEC's rule at `oil_opec_symmetry`: the probability and the lower end of
/// the uniform three-dollar range of a cut below the band, and the same for
/// a raise above it. At 0.0 the reference's asymmetric pair.
pub fn opec_rule(symmetry: f64) -> (f64, f64, f64, f64) {
    if symmetry == 0.0 {
        (0.6, 3.0, 0.5, 2.0)
    } else {
        let g = symmetry;
        (0.6 - 0.05 * g, 3.0 - 0.5 * g, 0.5 + 0.05 * g, 2.0 + 0.5 * g)
    }
}

/// The expected move an OPEC decision makes to the oil price when the price
/// it reads is normal with mean `mean` and sd `sd`: a cut's mean size times
/// its probability times the chance the price is under the band, less a
/// raise's above it. The adjustment inside the band has mean zero. For the
/// forecast (`forecast_horizon_sessions`); at `sd` 0.0 it is the rule's
/// expectation at `mean` itself.
pub fn opec_expected_impact(mean: f64, sd: f64, symmetry: f64) -> f64 {
    let (cut_p, cut_lo, raise_p, raise_lo) = opec_rule(symmetry);
    let below = OPEC_TARGET - OPEC_BAND;
    let above = OPEC_TARGET + OPEC_BAND;
    let (p_below, p_above) = if sd > 0.0 {
        (
            0.5 * mathx::erfc((mean - below) / (sd * std::f64::consts::SQRT_2)),
            0.5 * mathx::erfc((above - mean) / (sd * std::f64::consts::SQRT_2)),
        )
    } else {
        (
            if mean < below { 1.0 } else { 0.0 },
            if mean > above { 1.0 } else { 0.0 },
        )
    };
    cut_p * (cut_lo + 1.5) * p_below - raise_p * (raise_lo + 1.5) * p_above
}

/// The (2-year, 10-year, corporate) yields, in per cent, that
/// [`update_economy_daily`] would publish from `economy` on `inputs` with its
/// draws at their means ([`MeanDraws`]) and no active shock: the VIX and
/// yields block and the credit floor, and nothing else, since no other part
/// of the step writes or reads what they produce. For the rate indices' live
/// mark (`rate_intraday_live`). Takes no draw from any engine stream.
pub fn project_close_yields(economy: &EconomyState, inputs: &DailyInputs) -> (f64, f64, f64) {
    let next = project_close_state(economy, inputs);
    (next.treasury_yield_2y, next.treasury_yield_10y, next.corporate_bond_yield)
}

/// The economy [`project_close_yields`] reads its yields from: `economy` with
/// the VIX and its three yields as [`update_economy_daily`] would publish
/// them on `inputs` with its draws at their means and no active shock, and
/// the credit floor applied. Every other field is `economy`'s. The VIX here
/// is the state the close writes, before any published premium; the live
/// VIX (`vix_intraday_live`) reads it. Takes no draw from any engine stream.
pub fn project_close_state(economy: &EconomyState, inputs: &DailyInputs) -> EconomyState {
    let mut next = economy.clone();
    let mut shock_gdp_impact = 0.0;
    for shock in inputs.active_shocks {
        shock_gdp_impact += shock.gdp_impact * shock.severity;
    }
    vix_and_yields(economy, inputs, &mut next, shock_gdp_impact, &mut MeanDraws);
    if inputs.daily_credit_floor_gain > 0.0 {
        next.corporate_bond_yield = mathx::max(
            next.corporate_bond_yield,
            next.treasury_yield_10y + inputs.daily_credit_floor_gain * CORPORATE_SPREAD_FLOOR,
        );
    }
    next
}

/// One simulated day of the macro chain.
///
/// The reference implementation spread-copies (`{ ...economy }`) and returns new state.
/// This takes `&EconomyState` and returns a new one for the same reason: the
/// observable contract is the returned value, and a `&mut` version would make
/// the "reads `economy.x`, writes `newState.x`" distinction — which is
/// load-bearing throughout, since many lines read the OLD value after a new
/// one has been written — impossible to express faithfully.
pub fn update_economy_daily(
    economy: &EconomyState,
    inputs: &DailyInputs,
    rng: &mut impl Rng,
) -> EconomyState {
    let volatility = inputs.volatility;
    let mut new_state = economy.clone();
    let phase = phase_characteristics_for(economy.cycle_phase, inputs.cycle_us_calibration != 0.0);
    let day = inputs.game_day;

    let cal = inputs.macro_calendar;
    let is_month_start = day % cal.days_per_month == 0;
    let is_quarter_start = day % cal.days_per_quarter() == 0;

    // ── Shock aggregation ─────────────────────────────────────────────────
    let mut shock_gdp_impact = 0.0;
    let mut shock_inflation_impact = 0.0;
    let mut shock_oil_impact = 0.0;
    for shock in inputs.active_shocks {
        shock_gdp_impact += shock.gdp_impact * shock.severity;
        if shock.kind == ShockKind::OilShock {
            shock_oil_impact += shock.severity * 50.0;
        }
        if shock.kind == ShockKind::Pandemic || shock.kind == ShockKind::War {
            shock_inflation_impact += shock.severity * 2.0;
        }
    }

    // GDP floor scales with volatility, so harder difficulties allow deeper
    // recessions.
    let gdp_floor = -5.0 - (volatility - 0.5) * 10.0;

    // ── Phase-change GDP shock ────────────────────────────────────────────
    // The `+ 0.001` is a tolerance on a float accumulated by repeated
    // `+= 1/30`, not a spare margin: `months_in_current_phase` is never
    // exactly 1/30 after the first increment.
    if economy.months_in_current_phase < 1.0 / cal.month_f64() + 0.001 {
        // DRAW SITE (uniform) — on EVERY phase-change day, in every phase.
        //
        // The original builds a `Record<EconomicCyclePhase, number>` object
        // literal here and then indexes it. A JavaScript object literal
        // evaluates all of its values, so `contraction`'s `random()` runs
        // even when the phase is `trough` and the drawn number is discarded.
        // A `match` is the natural Rust shape and takes the draw only on the
        // contraction arm — which is the same output and a DIFFERENT stream
        // position, shifting every later consumer that day.
        //
        // So the draw is hoisted out, exactly as the literal does.
        let contraction_shock = -(2.0 + rng.next_f64() * (1.0 + volatility));
        let shock = match economy.cycle_phase {
            CyclePhase::Contraction => contraction_shock,
            CyclePhase::Trough => -0.5,
            CyclePhase::Recovery => 1.0,
            CyclePhase::Expansion => 0.5,
            CyclePhase::Peak => 0.0,
        };
        if shock != 0.0 {
            new_state.gdp_growth = clamp(economy.gdp_growth + shock, gdp_floor, 6.0);
        }

        // DRAW SITE (uniform) -- only when `phase_target_range_draw` is on.
        //
        // Placed after the shock draw above so the stream position of
        // every existing consumer is unchanged while the dial is 0.0, and
        // guarded by a BRANCH rather than by a zero coefficient, because a
        // draw taken and multiplied by zero still moves every later
        // consumer that day.
        if inputs.phase_target_range_draw != 0.0 {
            let r = effective_gdp_range(
                &phase, economy.cycle_phase, inputs.trough_growth_floor);
            let mid = (r.0 + r.1) / 2.0;
            let drawn = r.0 + rng.next_f64() * (r.1 - r.0);
            new_state.phase_gdp_target =
                mid + inputs.phase_target_range_draw * (drawn - mid);
        }
    }

    // ── Quarterly GDP ─────────────────────────────────────────────────────
    if is_quarter_start {
        let range = effective_gdp_range(
            &phase, economy.cycle_phase, inputs.trough_growth_floor);
        let target_gdp = phase_growth_target(
            new_state.phase_gdp_target, range, inputs.phase_target_range_draw)
            + shock_gdp_impact;
        new_state.gdp_growth = clamp(
            new_state.gdp_growth
                + (target_gdp - new_state.gdp_growth) * 0.25
                + random_normal(rng, 0.0, 0.3 * volatility),
            gdp_floor,
            6.0,
        );
    }

    // GDP level compounds daily from the CURRENT growth rate.
    new_state.gdp = economy.gdp * (1.0 + new_state.gdp_growth / 100.0 / inputs.macro_compound_days_per_year);

    // ── Monthly releases ──────────────────────────────────────────────────
    if is_month_start {
        let range = effective_gdp_range(
            &phase, economy.cycle_phase, inputs.trough_growth_floor);
        let phase_gdp_target = phase_growth_target(
            new_state.phase_gdp_target, range, inputs.phase_target_range_draw);
        let gdp_gap = phase_gdp_target - new_state.gdp_growth;
        // Asymmetry correction: pull twice as hard when GDP is moving against
        // the phase, so it does not fall fast and recover slowly.
        //
        // The two arms return the same 2.0 and clippy would merge them. They
        // are kept apart because they are two DIFFERENT economic conditions —
        // a downturn with growth still positive, and an upswing with growth
        // still negative — that happen to share a coefficient today. Merging
        // them would make a future change to one silently change both.
        #[allow(clippy::if_same_then_else)]
        let gdp_correction_multiplier = if (economy.cycle_phase == CyclePhase::Contraction
            || economy.cycle_phase == CyclePhase::Trough)
            && new_state.gdp_growth > 0.0
        {
            2.0
        } else if (economy.cycle_phase == CyclePhase::Recovery
            || economy.cycle_phase == CyclePhase::Expansion)
            && new_state.gdp_growth < 0.0
        {
            2.0
        } else {
            1.0
        };
        new_state.gdp_growth = clamp(
            new_state.gdp_growth
                + gdp_gap * 0.12 * gdp_correction_multiplier
                + random_normal(rng, 0.0, 0.1 * volatility),
            gdp_floor,
            6.0,
        );

        // Okun's law: 1pp of GDP below trend (~2%) is ~0.5pp of unemployment.
        //
        // That is an annual relation, and at 0.0 `unemployment_okun_coefficient`
        // applies 0.20 of it at every monthly release, 2.4 points a year,
        // beside a recovery term that counts the same growth again. Off
        // zero the coefficient is annual and divided over the year's twelve
        // releases, and there is no recovery term. A branch, so 0.0 is the
        // arithmetic that stood; adding the `+ 0.0` recovery term off zero
        // moves nothing.
        let okun = inputs.unemployment_okun_coefficient;
        let gdp_effect = if okun == 0.0 {
            (2.0 - new_state.gdp_growth) * 0.20
        } else {
            (2.0 - new_state.gdp_growth) * (okun / 12.0)
        };
        let nairu = economy.structural_unemployment;
        // `unemployment_natural_pull`: the share of the gap to the natural
        // rate closed this release. A branch, so 0.0 multiplies by the
        // literal 0.06 that stood.
        let nairu_pull = if inputs.unemployment_natural_pull == 0.0 {
            (nairu - economy.unemployment_rate) * 0.06
        } else {
            (nairu - economy.unemployment_rate) * inputs.unemployment_natural_pull
        };
        let mut recovery_effect = 0.0;
        if okun == 0.0
            && (economy.cycle_phase == CyclePhase::Expansion
                || economy.cycle_phase == CyclePhase::Recovery)
            && new_state.gdp_growth > 1.0
        {
            recovery_effect = -new_state.gdp_growth * 0.08;
        }
        // `unemployment_adjustment_half_life`: the cyclical drive reaches
        // the rate through a partial adjustment, so a turn's step in growth
        // and in the phase's trend builds into the monthly change over
        // months, as unemployment rises through a real recession. A branch,
        // so 0.0 is the expression that stood, operation for operation; the
        // one noise draw is taken in the same place either way.
        new_state.unemployment_rate = if inputs.unemployment_adjustment == 0.0 {
            clamp(
                economy.unemployment_rate
                    + phase.unemployment_trend * 0.3
                    + gdp_effect
                    + nairu_pull
                    + recovery_effect
                    + random_normal(rng, 0.0, 0.06 * volatility),
                2.5,
                15.0,
            )
        } else {
            let drive = unemployment_drive_with(
                phase.unemployment_trend, economy.cycle_phase, new_state.gdp_growth, okun);
            let impulse = economy.unemployment_impulse
                + inputs.unemployment_adjustment * (drive - economy.unemployment_impulse);
            new_state.unemployment_impulse = impulse;
            clamp(
                economy.unemployment_rate
                    + impulse
                    + nairu_pull
                    + random_normal(rng, 0.0, 0.06 * volatility),
                2.5,
                15.0,
            )
        };

        let unemployment_change = new_state.unemployment_rate - economy.unemployment_rate;
        new_state.jobs_created = clamp(
            200000.0 - unemployment_change * 500000.0
                + random_normal(rng, 0.0, 30000.0 * volatility),
            -500000.0,
            500000.0,
        );

        // ── Inflation ─────────────────────────────────────────────────────
        let inflation_target = INFLATION_TARGET;
        let inflation_mean_rev_coeff = inputs.inflation_reversion;

        // Positive real rates are contractionary.
        let real_rate_suppression = if economy.federal_funds_rate > economy.inflation_rate {
            -(economy.federal_funds_rate - economy.inflation_rate) * 0.04
        } else if economy.federal_funds_rate > 3.0 {
            -(economy.federal_funds_rate - 3.0) * 0.015
        } else {
            0.0
        };

        // `oil_inflation_passthrough`: 0.0 is the shipped three-way branch,
        // which pays twice as much above 80 as below 50 and nothing between,
        // so it has a positive mean about oil's own anchor. Off zero one
        // coefficient acts either side of `OIL_PASSTHROUGH_ANCHOR`.
        let oil_inflation_effect = if inputs.oil_inflation_passthrough != 0.0 {
            (economy.oil_price - OIL_PASSTHROUGH_ANCHOR) * (0.01 * inputs.oil_inflation_passthrough)
        } else if economy.oil_price > 80.0 {
            (economy.oil_price - 80.0) * 0.01
        } else if economy.oil_price < 50.0 {
            (economy.oil_price - 50.0) * 0.005
        } else {
            0.0
        };

        let tariff_inflation_effect = (economy.tariff_rate - 5.0) * 0.01;

        let nairu_for_phillips = economy.structural_unemployment;
        let unemployment_gap = new_state.unemployment_rate - nairu_for_phillips;
        // D3: the JS coefficient, which `wasm/src` also states. Only the
        // stale compiled binary said -0.18.
        let phillips_curve_effect = -unemployment_gap * PHILLIPS_CURVE_COEFF;

        let usd_inflation_effect = -(economy.usd_index - 100.0) * 0.01;

        // Wage growth: tight labour markets drive wages.
        let mut wage_growth_target =
            economy.inflation_rate * 0.7 + (nairu_for_phillips - economy.unemployment_rate) * 0.5;
        // Cost-of-living adjustment: workers demand 80% of inflation as a
        // floor once inflation is above 3%.
        if economy.inflation_rate > 3.0 {
            let cola_floor = economy.inflation_rate * 0.8;
            wage_growth_target = mathx::max(wage_growth_target, cola_floor);
        }
        new_state.wage_growth =
            economy.wage_growth + (wage_growth_target - economy.wage_growth) * 0.15;

        let mut wage_pressure = mathx::max(0.0, new_state.wage_growth - 2.0) * 0.08;
        // Wage-price spiral: fast wages AND high inflation reinforce.
        let spiral_condition = economy.wage_growth > 4.0 && economy.inflation_rate > 3.0;
        let spiral_boost = if spiral_condition {
            (economy.wage_growth - 4.0) * (economy.inflation_rate - 3.0) * 0.02
        } else {
            0.0
        };
        wage_pressure += spiral_boost;

        new_state.inflation_rate = clamp(
            economy.inflation_rate
                + (inflation_target - economy.inflation_rate) * inflation_mean_rev_coeff
                + phase.inflation_trend * 0.04
                + shock_inflation_impact * 0.02
                + real_rate_suppression
                + oil_inflation_effect
                + tariff_inflation_effect * 0.03
                + usd_inflation_effect * 0.03
                + phillips_curve_effect
                + wage_pressure
                + random_normal(rng, 0.0, 0.04 * volatility),
            inputs.inflation_floor,
            inputs.inflation_ceiling,
        );
        new_state.core_inflation = clamp(
            economy.core_inflation + (new_state.inflation_rate - economy.core_inflation) * 0.3,
            -1.0,
            12.0,
        );

        // ── Confidence ────────────────────────────────────────────────────
        let confidence_phase_adj = match economy.cycle_phase {
            CyclePhase::Contraction => -20.0,
            CyclePhase::Trough => -15.0,
            CyclePhase::Peak => 5.0,
            CyclePhase::Recovery => -5.0,
            CyclePhase::Expansion => 0.0,
        };
        let confidence_base = 100.0 + economy.gdp_growth * 5.0 - economy.unemployment_rate * 3.0
            + confidence_phase_adj;
        let fear_greed_nudge = (economy.fear_greed_index - 50.0) / 50.0 * 2.0;
        let market_return_nudge = economy.rolling_market_return_30d * 3.0;

        new_state.consumer_confidence = clamp(
            economy.consumer_confidence
                + (confidence_base - economy.consumer_confidence) * 0.25
                + fear_greed_nudge
                + market_return_nudge
                + random_normal(rng, 0.0, 3.0 * volatility),
            40.0,
            130.0,
        );
        new_state.business_confidence = clamp(
            economy.business_confidence
                + (confidence_base - economy.business_confidence) * 0.20
                + market_return_nudge * 1.5
                + random_normal(rng, 0.0, 4.5 * volatility),
            40.0,
            130.0,
        );

        // ── Housing ───────────────────────────────────────────────────────
        let confidence_housing_adj = (new_state.consumer_confidence - 100.0) * 0.1;
        let housing_target = 100.0 - (economy.mortgage_rate_30y - 5.0) * 4.0
            + economy.gdp_growth * 2.0
            + confidence_housing_adj;
        // Thin trading slows price discovery.
        let volume_speed_factor = mathx::max(0.3, economy.housing_transaction_volume / 100.0);
        let housing_mean_rev = (clamp(housing_target, 75.0, 130.0) - economy.housing_index)
            * 0.08
            * volume_speed_factor;
        let recession_effect = if economy.recession_probability > 0.3 {
            -(economy.recession_probability - 0.3) * 2.0
        } else {
            0.0
        };
        let housing_change =
            housing_mean_rev + recession_effect + random_normal(rng, 0.0, 0.3 * volatility);
        new_state.housing_index = clamp(economy.housing_index + housing_change, 75.0, 180.0);

        let mortgage_effect = mathx::max(-0.3, -(economy.mortgage_rate_30y - 5.0) * 0.05);
        let housing_gdp_effect = if economy.gdp_growth > 2.0 {
            0.02
        } else if economy.gdp_growth < 0.0 {
            -0.05
        } else {
            0.0
        };
        // NOTE `randomNormal(0, 1) * 0.02` — the scale is applied OUTSIDE the
        // wrapper here, unlike every other call site. Same draw either way,
        // but the arithmetic differs in the last bit, so it is preserved.
        new_state.home_starts_monthly = mathx::max(
            500000.0,
            mathx::min(
                2000000.0,
                economy.home_starts_monthly
                    * (1.0
                        + mortgage_effect
                        + housing_gdp_effect
                        + random_normal(rng, 0.0, 1.0) * 0.02),
            ),
        );

        // Volume responds immediately to rate shocks — it freezes long before
        // prices move.
        let rate_shock = mathx::max(0.0, economy.mortgage_rate_30y - 5.0);
        let volume_target = mathx::max(
            30.0,
            100.0 - rate_shock * 15.0 - mathx::max(0.0, (economy.unemployment_rate - 5.0) * 5.0),
        );
        new_state.housing_transaction_volume = economy.housing_transaction_volume
            + (volume_target - economy.housing_transaction_volume) * 0.30;

        // ── Labour-market hysteresis ──────────────────────────────────────
        if economy.cycle_phase == CyclePhase::Contraction
            || economy.cycle_phase == CyclePhase::Trough
        {
            let ltu_target = economy.unemployment_rate * 0.4;
            new_state.long_term_unemployment_rate = economy.long_term_unemployment_rate
                + (ltu_target - economy.long_term_unemployment_rate) * 0.05;
        } else {
            // Hysteresis: the long-term unemployed are harder to re-employ,
            // so this falls at only 3%/month.
            new_state.long_term_unemployment_rate =
                mathx::max(0.5, economy.long_term_unemployment_rate * 0.97);
        }
        // `unemployment_natural_rate`: the natural rate with no long-term
        // unemployment. A branch, so 0.0 adds to the literal 4.0 that stood.
        let natural_base = if inputs.unemployment_natural_rate == 0.0 {
            4.0
        } else {
            inputs.unemployment_natural_rate
        };
        new_state.structural_unemployment =
            natural_base + new_state.long_term_unemployment_rate * 0.3;

        let lfp_target = if economy.cycle_phase == CyclePhase::Contraction
            || economy.cycle_phase == CyclePhase::Trough
        {
            60.0 - (economy.unemployment_rate - 5.0) * 0.5
        } else {
            63.0 + (economy.gdp_growth - 1.0) * 0.3
        };
        let lfp_drift = (lfp_target - economy.labor_force_participation) * 0.01;
        new_state.labor_force_participation = mathx::max(
            55.0,
            mathx::min(68.0, economy.labor_force_participation + lfp_drift),
        );

        // ── Fiscal ────────────────────────────────────────────────────────
        if economy.cycle_phase == CyclePhase::Contraction
            || economy.cycle_phase == CyclePhase::Trough
        {
            let auto_stabilizer = 1.0;
            let discretionary = if economy.unemployment_rate > 7.0 {
                mathx::min(4.0, (economy.unemployment_rate - 5.0) * 0.8)
            } else {
                0.0
            };
            new_state.fiscal_stimulus = mathx::min(6.0, auto_stabilizer + discretionary);
            // `+=` on the already-updated growth, and NOT re-clamped — so a
            // stimulus can push gdp_growth above the 6.0 ceiling the lines
            // above enforce. Faithful to the original.
            new_state.gdp_growth += new_state.fiscal_stimulus * FISCAL_MULTIPLIER / 12.0;
            new_state.government_debt_to_gdp =
                economy.government_debt_to_gdp + new_state.fiscal_stimulus / 12.0;
        } else {
            new_state.fiscal_stimulus = mathx::max(0.0, economy.fiscal_stimulus - 0.2);
            if new_state.gdp_growth > 2.0 {
                new_state.government_debt_to_gdp =
                    mathx::max(60.0, economy.government_debt_to_gdp - 0.05);
            } else {
                new_state.government_debt_to_gdp = economy.government_debt_to_gdp;
            }
        }
    }

    // CPI compounds daily whether or not a release happened.
    new_state.cpi = economy.cpi * (1.0 + new_state.inflation_rate / 100.0 / inputs.macro_compound_days_per_year);

    // ── Oil ───────────────────────────────────────────────────────────────
    let oil_inventory = economy.oil_inventory_level;
    let oil_last_opec = economy.oil_last_opec_day;

    let oil_demand_factor = economy.gdp_growth * 0.15;
    // Supply answers demand, or does not. At 0.0 this is the literal zero
    // the reference implementation writes, and a BRANCH rather than a
    // multiply so a negative `gdp_growth` cannot turn a `-0.0` into a
    // `+0.0` and move a trajectory by a signed zero.
    //
    // The zero is why inventory only ever falls: demand draws it down every
    // day and nothing puts it back, so it reaches its floor and the
    // pressure term saturates into a standing push on the oil price. At 1.0
    // supply matches demand in expectation and inventory is driftless,
    // which is the stationarity condition of this process and not a level
    // chosen to hit a number.
    let oil_supply_factor = if inputs.oil_supply_response == 0.0 {
        0.0
    } else {
        oil_demand_factor * inputs.oil_supply_response
    };
    let inventory_change =
        oil_demand_factor - oil_supply_factor + random_normal(rng, 0.0, inventory_noise(inputs) * volatility);
    // `oil_inventory_reversion`: production and storage close this share of
    // inventory's gap to its normal level each day, so inventory has a
    // stationary distribution and the pressure below cannot saturate for
    // good. A branch, so 0.0 is the integrator that stood; the one draw
    // above is taken either way.
    let new_oil_inventory = if inputs.oil_inventory_reversion == 0.0 {
        clamp(oil_inventory - inventory_change, 0.0, 100.0)
    } else {
        clamp(
            oil_inventory - inventory_change
                + inputs.oil_inventory_reversion * (OIL_INVENTORY_NORMAL - oil_inventory),
            0.0,
            100.0,
        )
    };
    new_state.oil_inventory_level = new_oil_inventory;

    // `oil_inventory_level_gain`: a branch, so 0.0 is the push that stood;
    // off it the pressure moves the level by its change.
    let oil_inventory_pressure = if inputs.oil_inventory_level_gain == 0.0 {
        inventory_pressure(new_oil_inventory)
    } else {
        inputs.oil_inventory_level_gain * (inventory_pressure(new_oil_inventory) - inventory_pressure(oil_inventory))
    };

    // Summer driving season peaks ~day 180, winter valley ~day 90.
    //
    // `(day - 1) % 365 + 1` — JavaScript's `%` keeps the sign of the
    // dividend, and so does Rust's, so a day of 0 gives -1 % 365 = -1 and a
    // dayOfYear of 0 in both. Reproduced rather than corrected.
    //
    // The AMPLITUDE, not the factor. WHERE it acts is decided at the
    // reversion target below, because a shape multiplied into a level every
    // day compounds: the product of these factors is 5.119 over the 252
    // game-days a certified year passes and 0.921 over a full 365, so a
    // window shorter than the period reads a near-neutral shape as a trend.
    //
    // On the macro calendar: the period is the calendar's year and the
    // valley its day 90, which are 365 and 90 as shipped.
    let year = cal.days_per_year;
    let day_of_year = ((day - 1) % year) + 1;
    let oil_seasonal_amplitude = 0.03
        * mathx::sin(2.0 * std::f64::consts::PI
            * (day_of_year as f64 - cal.scale_days(90) as f64) / year as f64);

    // `oil_dollar_elasticity`: the dollar is a level the price carries
    // (below) rather than a daily drag, so the drag is dropped. A branch, so
    // 0.0 is the drag as it stood.
    let oil_usd_drag = if inputs.oil_dollar_elasticity == 0.0 {
        -(economy.usd_index - 100.0) * 0.08
    } else {
        0.0
    };

    // ── OPEC ──────────────────────────────────────────────────────────────
    // A state-dependent draw site: 0 draws on an ordinary day, 1 to 3 on a
    // decision day depending on which branch the price difference selects.
    let mut opec_impact = 0.0;
    if day - oil_last_opec >= cal.opec_interval() {
        new_state.oil_last_opec_day = day;
        let oil_price = economy.oil_price;
        let price_diff = oil_price - OPEC_TARGET;

        // The two outer branches are a pair that should mirror and do not:
        // 0.6 at 3-to-6 against 0.5 at 2-to-5 is an expected +2.700 against
        // -1.750, so the rule pushes oil UP on net. At symmetry 1.0 both
        // sides use the mean of the rule's own two probabilities and the
        // mean of its own two magnitude ranges, which is the one symmetric
        // rule that keeps the total intervention it performs.
        //
        // A branch, so 0.0 takes the original arithmetic in the original
        // order and consumes the same draws in the same places either way.
        let (cut_p, cut_lo, raise_p, raise_lo) = opec_rule(inputs.oil_opec_symmetry);
        if price_diff < -OPEC_BAND {
            // Well below target: likely a production cut.
            if rng.next_f64() < cut_p {
                opec_impact = cut_lo + rng.next_f64() * 3.0;
            }
        } else if price_diff > OPEC_BAND {
            // Well above target: likely a production increase.
            if rng.next_f64() < raise_p {
                opec_impact = -(raise_lo + rng.next_f64() * 3.0);
            }
        } else {
            // In the comfort zone: a small adjustment.
            if rng.next_f64() < 0.2 {
                opec_impact = (rng.next_f64() - 0.5) * 3.0;
            }
        }
    }

    let oil_target_level = OIL_BASELINE + economy.gdp_growth * 3.0 + shock_oil_impact * 10.0;
    // Where the seasonal shape acts, as a share of its own amplitude. A
    // BRANCH at 0.0, so every preset before pt-v18 runs the reference
    // implementation's arithmetic in its own order: the whole amplitude
    // multiplies the new price level and the target carries none of it.
    //
    // Above 0.0 the amplitude is SPLIT, so its total is conserved and only
    // the point of application moves. At 1.0 the target carries all of it
    // and the level's factor is exactly 1.0, which makes the shape modulate
    // where the price is pulled toward by plus or minus 3 per cent instead
    // of compounding on the price itself.
    let (oil_target, oil_level_seasonality) = if inputs.oil_seasonality_target == 0.0 {
        (oil_target_level, 1.0 + oil_seasonal_amplitude)
    } else {
        let g = inputs.oil_seasonality_target;
        (
            oil_target_level * (1.0 + g * oil_seasonal_amplitude),
            1.0 + (1.0 - g) * oil_seasonal_amplitude,
        )
    };
    // `oil_convenience_yield`: the premium low stocks put on the price's
    // LEVEL, `S = X f(I)` with `X` the premium-free price that reverts to
    // the target. The step reverts toward the target carrying the premium
    // the session opened with, `f(I)`, and then moves the level by the
    // premium's change to the inventory the step leaves, `f(I') / f(I)`. So
    // a shortage raises the price at once and the premium unwinds as stocks
    // refill: the expected path falls, backwardation. A premium on the
    // target alone, which the price reaches only at its own slow reversion,
    // made the expected path RISE while stocks were low: contango, the
    // wrong sign. A branch, so 0.0 is the step that stood.
    //
    // `oil_target_drift_sd`: the long factor rides on the level the same
    // way, `S = X e^D f(I)`, so its change moves spot and the whole curve
    // alike while `X` reverts to the target.
    let (oil_reversion_target, oil_premium_ratio) = if inputs.oil_convenience_yield == 0.0
        && inputs.oil_target_drift.is_none()
        && inputs.oil_dollar_elasticity == 0.0
    {
            (oil_target, None)
        } else {
            let (mut opened, mut left) = (1.0, 1.0);
            if inputs.oil_convenience_yield != 0.0 {
                let k = inputs.oil_convenience_yield;
                opened = oil_convenience_factor(k, oil_inventory, inputs.oil_convenience_inventory_var_before);
                left = oil_convenience_factor(k, new_oil_inventory, inputs.oil_convenience_inventory_var);
            }
            if let Some((d, d_next)) = inputs.oil_target_drift {
                opened *= mathx::exp(d);
                left *= mathx::exp(d_next);
            }
            // The dollar as it stood at the session's start, in both: its
            // change tonight is applied once the dollar's step has run.
            if inputs.oil_dollar_elasticity != 0.0 {
                let g = mathx::pow(economy.usd_index / 100.0, -inputs.oil_dollar_elasticity);
                opened *= g;
                left *= g;
            }
            (oil_target * opened, Some(left / opened))
        };
    // `oil_mean_reversion` and `oil_noise_sd`: branches, so 0.0 multiplies
    // by the standing 0.03 and 2.0 as the reference always did.
    // `oil_pushes_in_target`: the part of the price the pushes made decays
    // at the shipped 0.03 on its own, and the rest reverts. A branch, so off
    // it the whole price reverts as it stood.
    let oil_reverting = if inputs.oil_pushes_in_target {
        economy.oil_price - economy.oil_push_level
    } else {
        economy.oil_price
    };
    let oil_mean_rev = if inputs.oil_mean_reversion == 0.0 {
        (oil_reversion_target - oil_reverting) * 0.03
    } else {
        (oil_reversion_target - oil_reverting) * inputs.oil_mean_reversion
    };
    let oil_volatility = if inputs.oil_noise_sd == 0.0 { 2.0 * volatility } else { inputs.oil_noise_sd * volatility };
    // `oil_noise_log_sd`: a share of the price, a branch so 0.0 is the
    // dollar noise above.
    let oil_volatility = if inputs.oil_noise_log_sd == 0.0 {
        oil_volatility
    } else {
        inputs.oil_noise_log_sd * volatility * economy.oil_price
    };
    // The oil price's reverting part and the pushes' part before the clamp,
    // which `oil_dollar_elasticity` rescales once the dollar has stepped.
    let oil_dollar_parts: (f64, f64);
    let oil_level = if inputs.oil_pushes_in_target {
        // The reverting part carries the noise and the level's carriers;
        // the pushes' part decays at 0.03 and takes this session's pushes.
        // At a reversion of 0.03 their sum is the step below exactly.
        let noise = random_normal(rng, 0.0, oil_volatility);
        let pushes = oil_inventory_pressure + oil_usd_drag + opec_impact + shock_oil_impact * 0.1;
        let reverting = (oil_reverting + oil_mean_rev + noise) * oil_level_seasonality;
        let reverting = match oil_premium_ratio {
            None => reverting,
            Some(ratio) => reverting * ratio,
        };
        let pushed = (0.97 * economy.oil_push_level + pushes) * oil_level_seasonality;
        new_state.oil_push_level = pushed;
        oil_dollar_parts = (reverting, pushed);
        reverting + pushed
    } else {
        let oil_level = (economy.oil_price
            + oil_mean_rev
            + oil_inventory_pressure
            + oil_usd_drag
            + opec_impact
            + random_normal(rng, 0.0, oil_volatility)
            + shock_oil_impact * 0.1)
            * oil_level_seasonality;
        let oil_level = match oil_premium_ratio {
            None => oil_level,
            Some(ratio) => oil_level * ratio,
        };
        oil_dollar_parts = (oil_level, 0.0);
        oil_level
    };
    let (oil_lo, oil_hi) = oil_bounds(inputs.oil_price_floor, inputs.oil_price_ceiling);
    new_state.oil_price = clamp(oil_level, oil_lo, oil_hi);
    // Under the long factor, the bound's truncation goes to the long-run
    // level (the close adds it to the factor), not to the short gap.
    if inputs.oil_target_drift.is_some() {
        new_state.oil_bound_log_ratio = if oil_level > 0.0 {
            mathx::log(new_state.oil_price / oil_level)
        } else {
            0.0
        };
    }

    // ── Gold ──────────────────────────────────────────────────────────────
    let real_rate = economy.federal_funds_rate - economy.inflation_rate;
    let real_rate_drift = -real_rate * 0.8;
    let inflation_hedge = mathx::max(0.0, (economy.inflation_rate - 2.0) * 1.5);
    // Reference: `vix > 30`, a level endogenous VIX never reaches (measured
    // ceiling 26.57). Re-sited at the endogenous P94 so the gate is live;
    // see `CRISIS_VIX_THRESHOLD`. The hinge origin moves with the gate, so
    // the premium stays continuous at the threshold.
    let crisis_premium = if economy.vix > inputs.crisis_vix_threshold && economy.gdp_growth < -1.0 {
        mathx::min(
            5.0,
            economy.gdp_growth.abs() * 1.0 + (economy.vix - inputs.crisis_vix_threshold) * 0.15,
        )
    } else {
        0.0
    };
    let prev_market_return = economy.previous_day_market_return;
    let gold_safe_haven = if prev_market_return < -1.0 {
        prev_market_return.abs() * 2.0
    } else {
        0.0
    };

    // D2, decided: the ADDITIVE equilibrium (~$200 per inflation point). The
    // deployed WASM multiplicative version moves gold ~0.3% per point — about
    // $6 on $2,000 gold — which is economically inert and fails REALISM
    // MANDATE #7 as written. Whether $200/point is too strong is a
    // calibration question, to be answered with measurements, not by keeping
    // the wrong shape.
    let inflation_premium_target = mathx::max(0.0, (economy.inflation_rate - 2.0) * 150.0)
        + (economy.inflation_rate - 2.5) * 50.0;
    let real_rate_penalty = -real_rate * 100.0;
    let gold_equilibrium = GOLD_EQUILIBRIUM_BASE + inflation_premium_target + real_rate_penalty;

    let gold_mean_reversion = (gold_equilibrium - economy.gold_price) * GOLD_MEAN_REVERSION;
    let sentiment_drift = (50.0 - economy.fear_greed_index) * 0.03;

    let gold_change = real_rate_drift
        + inflation_hedge
        + crisis_premium
        + gold_safe_haven
        + gold_mean_reversion
        + sentiment_drift
        + random_normal(rng, 0.0, 3.0 * volatility);
    new_state.gold_price = clamp(economy.gold_price + gold_change, 800.0, 5000.0);

    // ── Copper ────────────────────────────────────────────────────────────
    let copper_demand = economy.gdp_growth * 0.02 + (economy.housing_index - 100.0) * 0.001;
    let copper_usd_drag = -(economy.usd_index - 100.0) * 0.003;
    let copper_change =
        copper_demand + copper_usd_drag + random_normal(rng, 0.0, 0.04 * volatility);
    new_state.copper_price = clamp(economy.copper_price + copper_change, 2.0, 8.0);

    // ── USD ───────────────────────────────────────────────────────────────
    let usd_target = 100.0 + (economy.federal_funds_rate - 2.5) * 3.0;
    // `usd_mean_reversion` and `usd_noise_sd`: branches, so 0.0 is the
    // standing 0.02 and 0.3.
    let usd_mean_reversion = (usd_target - economy.usd_index) * 0.02;
    let usd_noise = if inputs.usd_noise_sd == 0.0 { 0.3 * volatility } else { inputs.usd_noise_sd * volatility };
    // Reference: `vix > 30` — dead for the same reason as the gold crisis
    // premium above; re-sited with it. See `CRISIS_VIX_THRESHOLD`.
    //
    // Reads the PARAMETER, not the constant. It read the constant until
    // 0.4.2, so an embedder who moved `crisis_vix_threshold` got a gold gate
    // at their level and a dollar gate still at 25.5. The two describe one
    // regime: a crisis is the same crisis whether you watch gold or the
    // dollar. No behaviour changes at the default, where the two agree.
    let safe_haven_drift = if economy.vix > inputs.usd_crisis_vix_threshold {
        (economy.vix - inputs.usd_crisis_vix_threshold) * USD_SAFE_HAVEN_GAIN
    } else {
        0.0
    };
    if inputs.usd_mean_reversion == 0.0 {
        let usd_change =
            usd_mean_reversion + safe_haven_drift + random_normal(rng, 0.0, usd_noise);
        new_state.usd_index = clamp(economy.usd_index + usd_change, 80.0, 130.0);
    } else {
        // The base reverts at the measured rate; the safe-haven bid takes the
        // drift and decays at the shipped 0.02. The same one draw.
        let base = economy.usd_index - economy.usd_haven_level;
        let base_next = base + (usd_target - base) * inputs.usd_mean_reversion
            + random_normal(rng, 0.0, usd_noise);
        let haven_next = 0.98 * economy.usd_haven_level + safe_haven_drift;
        new_state.usd_haven_level = haven_next;
        new_state.usd_index = clamp(base_next + haven_next, 80.0, 130.0);
    }
    // `oil_dollar_elasticity`: tonight's dollar move carries the oil price's
    // reverting part, before its clamp, and nothing between the oil step
    // and here reads the new oil price.
    if inputs.oil_dollar_elasticity != 0.0 {
        let (reverting, pushed) = oil_dollar_parts;
        let ratio = mathx::pow(new_state.usd_index / economy.usd_index, -inputs.oil_dollar_elasticity);
        let unclamped = reverting * ratio + pushed;
        new_state.oil_price = clamp(unclamped, oil_lo, oil_hi);
        if inputs.oil_target_drift.is_some() {
            new_state.oil_bound_log_ratio = if unclamped > 0.0 {
                mathx::log(new_state.oil_price / unclamped)
            } else {
                0.0
            };
        }
    }

    // ── Trade balance ─────────────────────────────────────────────────────
    let tariff_effect = economy.tariff_rate * 0.5;
    let dollar_effect = -(economy.usd_index - 100.0) * 0.3;
    new_state.trade_balance = clamp(
        economy.trade_balance
            + (tariff_effect + dollar_effect) * 0.01
            + random_normal(rng, 0.0, 0.5 * volatility),
        -200.0,
        50.0,
    );

    // The VIX and the three yields, factored out so the rate indices'
    // live mark (`rate_intraday_live`) projects tonight's curve with the
    // very arithmetic this step runs. Same expressions, same draws, same
    // order.
    vix_and_yields(economy, inputs, &mut new_state, shock_gdp_impact, rng);

    // ── Fear/greed ────────────────────────────────────────────────────────
    // `fear_greed_published_inputs`: the phase and growth the index reads
    // are the published ones the engine passes in, the economy's own with
    // the switch off (`None`), which is the expression that stood.
    let (fear_greed_phase, fear_greed_growth) = match inputs.fear_greed_published {
        Some((phase, growth)) => (phase, growth),
        None => (economy.cycle_phase, economy.gdp_growth),
    };
    let fear_greed_phase_bonus = match fear_greed_phase {
        CyclePhase::Expansion => 15.0,
        CyclePhase::Peak => 5.0,
        CyclePhase::Contraction => -25.0,
        CyclePhase::Trough => -20.0,
        CyclePhase::Recovery => 10.0,
    };
    let market_sentiment = inputs.market_return_pct * 5.0;
    let fear_greed_base = 50.0 + fear_greed_growth * 3.0 - (economy.vix - 15.0) * 0.8
        + fear_greed_phase_bonus
        + market_sentiment;
    new_state.fear_greed_index = clamp(
        economy.fear_greed_index
            + (fear_greed_base - economy.fear_greed_index) * 0.25
            + random_normal(rng, 0.0, 2.0 * volatility),
        0.0,
        100.0,
    );

    // ── Recession probability ─────────────────────────────────────────────
    let target_recession_prob = match economy.cycle_phase {
        CyclePhase::Expansion => 0.10,
        CyclePhase::Peak => 0.30,
        CyclePhase::Contraction => 0.70,
        CyclePhase::Trough => 0.50,
        CyclePhase::Recovery => 0.20,
    };
    // Curve inversion is the most reliable recession predictor there is.
    let yield_spread = economy.treasury_yield_10y - economy.treasury_yield_2y;
    let inversion_premium = if yield_spread < 0.0 {
        mathx::min(0.25, yield_spread.abs() * 0.15)
    } else {
        0.0
    };
    let adjusted_target = mathx::min(0.95, target_recession_prob + inversion_premium);
    new_state.recession_probability = clamp(
        economy.recession_probability + (adjusted_target - economy.recession_probability) * 0.1,
        0.05,
        0.95,
    );

    new_state.months_in_current_phase = economy.months_in_current_phase + 1.0 / cal.month_f64();
    new_state.previous_day_market_return = inputs.market_return_pct;

    let prev_30d = economy.rolling_market_return_30d;
    // A month's memory: 30 steps as shipped.
    new_state.rolling_market_return_30d =
        prev_30d + (inputs.market_return_pct - prev_30d) / cal.month_f64();

    // `newState.derived = computeDerivedIndicators(newState)` sits here in
    // the original. Deliberately not ported — out of scope per the surface audit §0,
    // and verified to consume zero draws, which is what makes the omission
    // invisible to the stream.

    // Re-assert the credit spread floors against the benchmark as it now
    // stands -- but only when `daily_credit_floor_gain` is non-zero, which no
    // shipped preset yet sets.
    //
    // The defect it exists to close. `update_central_bank` floors both credit
    // yields when it computes them, and this function moves
    // `treasury_yield_10y` on EVERY day -- mean reversion toward fed funds
    // plus term premium, and the bond-stock correlation shift -- while never
    // writing either credit yield. Meetings are periodic, so between them the
    // yields go stale against a benchmark that has moved underneath them and
    // the spread drifts wherever the treasury takes it. Measured on the
    // deterministic channel, corporate reaches 0.4216 against a floor of 0.8
    // and first breaches on day 121. An investment-grade yield below the
    // risk-free curve is not a rare edge, it is an impossible quote.
    //
    // Why it ships INERT rather than simply fixed. This function is
    // preset-independent, so flooring unconditionally would move the economy
    // trajectory of every preset including `pt-v1`. The version policy is
    // explicit that a change to the simulated trajectory is breaking however
    // small it looks, and that such changes arrive as a NEW PRESET rather
    // than an edit to an existing one. So the correction is a dial, off
    // everywhere, for a future preset to turn on. At 1.0 both floors are
    // enforced in full; the gain scales them together because they are one
    // decision about whether stale credit is allowed to invert.
    //
    // A floor rather than a recomputation, so the semantics stay put: the
    // yields only ever get pushed UP to stay honest, and the next meeting
    // recomputes them properly, so there is no ratchet. Consumes no draws.
    if inputs.daily_credit_floor_gain > 0.0 {
        let g = inputs.daily_credit_floor_gain;
        new_state.corporate_bond_yield = mathx::max(
            new_state.corporate_bond_yield,
            new_state.treasury_yield_10y + g * CORPORATE_SPREAD_FLOOR,
        );
        new_state.mortgage_rate_30y = mathx::max(
            new_state.mortgage_rate_30y,
            new_state.treasury_yield_10y + g * MORTGAGE_SPREAD_FLOOR,
        );
    }

    new_state
}

#[cfg(test)]
mod phase_table_dials {
    use super::*;

    const PHASES: [CyclePhase; 5] = [
        CyclePhase::Expansion, CyclePhase::Peak, CyclePhase::Contraction,
        CyclePhase::Trough, CyclePhase::Recovery,
    ];

    /// At the dial's zero every phase reports the table's own pair, and the
    /// comparison is on bits rather than on a tolerance, because the claim
    /// is that no preset before pt-v18 can see this dial exist.
    #[test]
    fn a_zero_floor_returns_the_declared_range_unchanged() {
        for phase in PHASES {
            let c = phase_characteristics(phase);
            assert_eq!(effective_gdp_range(&c, phase, 0.0), c.gdp_growth_range,
                       "{phase:?} moved at a zero floor");
        }
    }

    /// The floor is the trough's alone. Asserted against every other phase
    /// rather than against the two that happen to declare a negative end,
    /// so a table that gains one is caught here rather than in a sweep.
    #[test]
    fn the_floor_moves_the_trough_and_nothing_else() {
        for phase in PHASES {
            let c = phase_characteristics(phase);
            let moved = effective_gdp_range(&c, phase, 1.0);
            if phase == CyclePhase::Trough {
                assert_eq!(moved.0, 0.0, "the trough floor did not reach zero");
                assert_eq!(moved.1, c.gdp_growth_range.1,
                           "the trough's upper end moved");
            } else {
                assert_eq!(moved, c.gdp_growth_range, "{phase:?} moved");
            }
        }
    }

    /// The dial is a share of the distance to zero, so a half dial is a
    /// half floor. Derived from the declared endpoint rather than written
    /// out, so a table edit moves the expectation with it.
    #[test]
    fn a_partial_floor_is_a_share_of_the_distance() {
        let c = phase_characteristics(CyclePhase::Trough);
        let half = effective_gdp_range(&c, CyclePhase::Trough, 0.5);
        assert_eq!(half.0, c.gdp_growth_range.0 / 2.0);
    }

    /// At zero the stored per-phase target is not consulted at all, which
    /// is what lets the field exist without any preset reading it. The
    /// stored value passed here is one no range contains.
    #[test]
    fn a_zero_draw_ignores_the_stored_target() {
        for phase in PHASES {
            let c = phase_characteristics(phase);
            let mid = (c.gdp_growth_range.0 + c.gdp_growth_range.1) / 2.0;
            assert_eq!(phase_growth_target(999.0, c.gdp_growth_range, 0.0), mid,
                       "{phase:?} read the stored target at a zero draw");
        }
    }

    /// And at one it is the only thing consulted.
    #[test]
    fn a_full_draw_reads_the_stored_target() {
        let c = phase_characteristics(CyclePhase::Contraction);
        assert_eq!(phase_growth_target(-2.75, c.gdp_growth_range, 1.0), -2.75);
    }
}

#[cfg(test)]
mod vix_level_identity {
    use super::*;
    use crate::economy::state::{create_initial_economy_state, InitialEconomyOptions};

    /// A draw-free RNG, so two calls that differ only in a dial can be
    /// compared BIT FOR BIT rather than to a tolerance. Copied from
    /// `economy::invariants`, which uses it for the same reason.
    struct Silent(f64);
    impl crate::rng::Rng for Silent {
        fn next_f64(&mut self) -> f64 {
            self.0
        }
        fn next_normal(&mut self) -> f64 {
            0.0
        }
    }

    fn economy() -> EconomyState {
        create_initial_economy_state(&InitialEconomyOptions::default())
    }

    /// The arm the design note registers on, as `DailyInputs`. The gains are
    /// ASYMMETRIC, which is the whole reason an offset existed.
    fn arm(identity: f64) -> DailyInputs<'static> {
        DailyInputs {
            vix_level_identity: identity,
            vix_index_sigma_pct: 1.04,
            vix_implied_from_market: 20.7,
            vix_realised_vol_weight: 0.3,
            vix_cycle_amplitude: 0.85,
            vix_mean_reversion: 0.06,
            vix_decay_ratio: 0.6,
            vix_return_gain: 30.0,
            vix_return_gain_up: 14.0,
            vix_return_source: 1.0,
            vix_target_offset: -9.16,
            vix_target_shock_cap: 150.0,
            // pt-v18's own, and it matters here: the DEFAULT is 0.03, at
            // which every return in this module clamps to the same value
            // and two arms that should differ do not. A test that had left
            // it at the default reported the return source as inert with
            // the identity off, which is the harness clamping, not the
            // model.
            vix_return_clamp: 15.0,
            market_day_return_pct: -1.7,
            market_return_pct: -0.02,
            game_day: 40,
            ..Default::default()
        }
    }

    fn vix_after(inputs: &DailyInputs) -> f64 {
        update_economy_daily(&economy(), inputs, &mut Silent(0.5)).vix
    }

    /// THE NEW INPUT IS UNREAD AT THE DEFAULT, asserted on bits over a
    /// spread rather than at one point.
    ///
    /// `vix_index_sigma_pct` is the index's conditional sigma, and the only
    /// thing that reads it is the excursion's zero-mean correction. If the
    /// branch guarding that correction were ever wrong -- an `||` for an
    /// `&&`, a dial compared against the wrong constant -- every preset
    /// before pt-v19 would move, and it would move by a fraction of a point
    /// a day, which is exactly the size nothing notices.
    #[test]
    fn the_index_sigma_is_not_read_while_the_identity_is_off() {
        let want = vix_after(&arm(0.0));
        for i in 0..=800 {
            let mut inputs = arm(0.0);
            inputs.vix_index_sigma_pct = i as f64 * 0.05;
            assert_eq!(vix_after(&inputs), want, "moved at sigma = {}",
                       i as f64 * 0.05);
        }
    }

    /// `unemployment_adjustment`: off, a monthly release moves the rate by
    /// the whole drive and leaves the impulse alone; on, it moves it by the
    /// impulse, which closes that share of its gap to the drive, and every
    /// other term (the NAIRU pull, the noise) is the one that stood.
    #[test]
    fn the_unemployment_release_adds_the_partially_adjusted_impulse() {
        let mut e = economy();
        e.cycle_phase = CyclePhase::Contraction;
        e.gdp_growth = -1.5;
        e.unemployment_impulse = 0.05;
        // A month start.
        let off_inputs = DailyInputs { game_day: DAYS_PER_MONTH, ..Default::default() };
        let off = update_economy_daily(&e, &off_inputs, &mut Silent(0.5));
        assert_eq!(off.unemployment_impulse, 0.05);
        let a = 0.2;
        let on_inputs = DailyInputs {
            game_day: DAYS_PER_MONTH,
            unemployment_adjustment: a,
            ..Default::default()
        };
        let on = update_economy_daily(&e, &on_inputs, &mut Silent(0.5));
        // Growth is set before unemployment reads it, the same in both.
        assert_eq!(on.gdp_growth, off.gdp_growth);
        // The drive the release read, from the two rises: off moves the rate
        // by drive + pull + noise and on by impulse + pull + noise, with
        // impulse = 0.05 + a (drive - 0.05), so the gap is (1 - a)(drive - 0.05).
        let gap = (off.unemployment_rate - e.unemployment_rate)
            - (on.unemployment_rate - e.unemployment_rate);
        let drive = 0.05 + gap / (1.0 - a);
        assert!((on.unemployment_impulse - (0.05 + a * (drive - 0.05))).abs() < 1e-12);
        // It is the drive `unemployment_drive` gives on the release's growth
        // (read before the day's later adjustments to growth, hence the
        // tolerance): the contraction's trend and Okun's law on a growth
        // near -4 per cent, about 1.3 pp a month, of which a fifth arrives.
        let phase = phase_characteristics_for(e.cycle_phase, false);
        let approx = unemployment_drive(phase.unemployment_trend, e.cycle_phase, on.gdp_growth);
        assert!((drive - approx).abs() < 0.01, "{drive} {approx}");
        assert!(drive > 1.0 && on.unemployment_rate - off.unemployment_rate < -0.9);
        // Off a month start nothing moves either way.
        let mid = DailyInputs { game_day: DAYS_PER_MONTH + 1, unemployment_adjustment: a,
                                ..Default::default() };
        let quiet = update_economy_daily(&e, &mid, &mut Silent(0.5));
        assert_eq!(quiet.unemployment_impulse, 0.05);
        assert_eq!(quiet.unemployment_rate, e.unemployment_rate);
    }

    /// And the mirror: at the identity it is read, monotonically, because
    /// the correction it sizes is subtracted from the target.
    #[test]
    fn the_index_sigma_is_read_once_the_identity_is_on() {
        let mut last = f64::INFINITY;
        for i in 1..=20 {
            let mut inputs = arm(1.0);
            inputs.vix_index_sigma_pct = i as f64 * 0.25;
            let v = vix_after(&inputs);
            assert!(v < last, "not falling at sigma {}: {v} after {last}",
                    i as f64 * 0.25);
            last = v;
        }
    }

    /// THE EARNINGS BUMP, which is a literal in the code and not a dial, so
    /// it cannot be shown retired by moving a value.
    ///
    /// Shown by its consequence instead: half a point on the first half of
    /// a month is a level constant, and under the identity the level is the
    /// variance. Two days that differ only in where they fall in the month
    /// must give the same VIX, and with the identity off they must not.
    #[test]
    fn the_earnings_bump_is_not_read_under_the_identity() {
        let (early, late) = (5, 20);
        let mut a = arm(1.0);
        a.game_day = early;
        let mut b = arm(1.0);
        b.game_day = late;
        assert_eq!(vix_after(&a), vix_after(&b),
                   "the month's half still moves the VIX under the identity");

        let mut a = arm(0.0);
        a.game_day = early;
        let mut b = arm(0.0);
        b.game_day = late;
        assert_ne!(vix_after(&a), vix_after(&b),
                   "the bump moves nothing even with the identity off; the harness is not reaching the code");
    }

    /// THE PHASE TABLE, the same way: five constants in the code, not a
    /// dial. Under the identity the business cycle reaches the VIX through
    /// the variance processes or not at all, so every phase must give the
    /// same VIX from the same variance.
    #[test]
    fn the_phase_table_is_not_read_under_the_identity() {
        let phases = [CyclePhase::Expansion, CyclePhase::Peak,
                      CyclePhase::Contraction, CyclePhase::Trough,
                      CyclePhase::Recovery];
        let of = |identity: f64, phase: CyclePhase| {
            let mut e = economy();
            e.cycle_phase = phase;
            update_economy_daily(&e, &arm(identity), &mut Silent(0.5)).vix
        };
        let want = of(1.0, CyclePhase::Expansion);
        for p in phases {
            assert_eq!(of(1.0, p), want, "{p:?} moved the VIX under the identity");
        }
        // The mirror: with it off the table is the level, so every phase
        // gives a different one.
        let mut seen: Vec<f64> = phases.iter().map(|p| of(0.0, *p)).collect();
        seen.sort_by(|a, b| a.partial_cmp(b).unwrap());
        seen.dedup();
        assert_eq!(seen.len(), phases.len(),
                   "the phase table moves nothing even with the identity off");
    }

    /// THE ZERO-MEAN CORRECTION IS ZERO WHEN THERE IS NOTHING TO CORRECT.
    ///
    /// The excursion exists because an ASYMMETRIC gain over a zero-mean
    /// return does not cancel. At equal gains it does, and the correction
    /// must be exactly 0.0 -- not nearly, because a preset with symmetric
    /// gains must not acquire a standing bias from a term that is supposed
    /// to be about the asymmetry.
    #[test]
    fn a_symmetric_response_needs_no_correction() {
        for i in 0..=200 {
            let sigma = i as f64 * 0.05;
            assert_eq!(expected_return_spike(sigma, 17.0, 17.0, 1.0), 0.0,
                       "a symmetric response was corrected at sigma {sigma}");
        }
    }

    /// And it is the Gaussian absolute moment, which is the identity it
    /// claims to be. Asserted against `sigma sqrt(2/pi)` computed the other
    /// way, so the constant in the code cannot drift from the integral it
    /// stands for.
    #[test]
    fn the_correction_is_the_gaussian_absolute_moment() {
        let two_over_pi = 2.0 / core::f64::consts::PI;
        for &sigma in &[0.25, 0.8, 1.04, 2.5, 9.0] {
            for &(g, gu) in &[(30.0, 14.0), (17.0, 5.0), (1.0, 0.0)] {
                let want = 0.5 * (g - gu) * sigma * mathx::sqrt(two_over_pi);
                let have = expected_return_spike(sigma, g, gu, 1.0);
                assert!((have - want).abs() < 1e-12,
                        "sigma {sigma}, gains {g}/{gu}: {have} vs {want}");
            }
        }
    }

    /// The correction MOVES WITH THE MARKET's volatility, which is the
    /// second thing a fitted offset could not do: an offset derived in a
    /// calm year is the wrong offset in a violent one.
    #[test]
    fn the_correction_scales_with_the_index_sigma() {
        let a = expected_return_spike(1.0, 30.0, 14.0, 1.0);
        let b = expected_return_spike(2.0, 30.0, 14.0, 1.0);
        assert!((b - 2.0 * a).abs() < 1e-12, "{b} is not twice {a}");
        assert!(a > 0.0, "an asymmetric down-heavy response has a positive mean");
    }

    /// A UNIT EXPONENT IS THE LINEAR CORRECTION TO THE BIT.
    ///
    /// The generalised branch would reproduce it to about an ULP, and an
    /// ULP is exactly what this crate does not accept: the VIX feeds the
    /// factor's variance, which feeds prices, so a last-place difference
    /// becomes a different market inside a year. The equality is asserted
    /// on bits over a spread of sigmas and gain pairs, against the
    /// expression rather than against a number.
    #[test]
    fn a_unit_exponent_is_the_linear_correction_to_the_bit() {
        for i in 0..=200 {
            let sigma = i as f64 * 0.05;
            for &(g, gu) in &[(30.0, 14.0), (17.0, 5.0), (22.3, 11.15), (1.0, 0.0)] {
                let want = 0.5 * (g - gu) * sigma * (2.0 / SQRT_TWO_PI);
                assert_eq!(expected_return_spike(sigma, g, gu, 1.0), want,
                           "sigma {sigma}, gains {g}/{gu}");
            }
        }
    }

    /// THE MOMENT IS THE GAUSSIAN ABSOLUTE MOMENT, checked where the
    /// answer needs no gamma function.
    ///
    /// `E|r|^p = sigma^p 2^(p/2) Gamma((p+1)/2) / sqrt(pi)` is what the
    /// code evaluates, so asserting it against itself would prove nothing.
    /// At `p = 2` and `p = 3` the moment has an elementary closed form —
    /// `sigma^2` and `2 sigma^3 sqrt(2/pi)` — reached without `tgamma` at
    /// all, so a wrong gamma, a wrong power of two or a swapped half is
    /// caught rather than reproduced.
    #[test]
    fn the_power_moment_matches_its_elementary_cases() {
        let root_two_over_pi = 2.0 / SQRT_TWO_PI;
        for &sigma in &[0.25, 0.8, 1.04, 2.5, 9.0] {
            for &(g, gu) in &[(30.0, 14.0), (22.3, 11.15)] {
                let e_abs = sigma * root_two_over_pi;
                for &(p, e_pow) in &[
                    (2.0, sigma * sigma),
                    (3.0, 2.0 * sigma * sigma * sigma * root_two_over_pi),
                ] {
                    let want = 0.5 * (g * e_pow - gu * e_abs);
                    let have = expected_return_spike(sigma, g, gu, p);
                    assert!((have - want).abs() <= 1e-9 * want.abs().max(1.0),
                            "sigma {sigma}, p {p}, gains {g}/{gu}: {have} vs {want}");
                }
            }
        }
    }

    /// AND IT IS THE MEAN OF THE THING IT CORRECTS, at the shipped
    /// exponent, where no elementary form exists.
    ///
    /// The two cases above pin the moment; this pins the WHOLE identity —
    /// the half-split, the sign convention and the exponent living on the
    /// down side alone — by integrating `return_spike_for` itself against
    /// a Gaussian density. Simpson's rule on +/- 12 sigma with 24,001
    /// points, which is a different computation from the closed form in
    /// every respect except the answer.
    ///
    /// A residual of 1e-6 relative is far inside the identity's own
    /// approximations, and far outside what a missing factor of two or a
    /// moment taken on the wrong side would give.
    #[test]
    fn the_closed_form_is_the_integral_of_the_response() {
        let n = 24_000usize;
        for &sigma in &[0.6, 1.04, 2.5] {
            for &(p, g, gu) in &[(1.2, 22.3, 11.15), (1.132, 30.0, 14.0), (1.0, 30.0, 14.0)] {
                let (lo, hi) = (-12.0 * sigma, 12.0 * sigma);
                let h = (hi - lo) / n as f64;
                let f = |r: f64| {
                    let d = mathx::exp(-0.5 * (r / sigma) * (r / sigma))
                        / (sigma * SQRT_TWO_PI);
                    return_spike_for(r, g, gu, p) * d
                };
                let mut acc = f(lo) + f(hi);
                for i in 1..n {
                    let r = lo + i as f64 * h;
                    acc += f(r) * if i % 2 == 1 { 4.0 } else { 2.0 };
                }
                let quad = acc * h / 3.0;
                let closed = expected_return_spike(sigma, g, gu, p);
                assert!((quad - closed).abs() <= 1e-6 * closed.abs().max(1.0),
                        "sigma {sigma}, p {p}: quadrature {quad} vs closed {closed}");
            }
        }
    }

    /// A LINEAR CORRECTION UNDER A POWER FORM IS WRONG BY A FACTOR THAT
    /// MOVES WITH THE MARKET, and it is wrong in both directions.
    ///
    /// This is why the exponent had to reach this function rather than
    /// only [`return_spike_for`]. The down half carries `E|r|^p` and the
    /// up half `E|r|`, so the ratio of the right correction to the linear
    /// one grows as `sigma^(p - 1)` and crosses 1.0 at a single
    /// volatility — near sigma 0.91 at `p = 1.2`. A correction computed as
    /// if the response were linear therefore over-corrects a calm market
    /// and under-corrects a violent one, which is precisely the defect the
    /// fitted `vix_target_offset` had. The identity exists to remove that
    /// defect, not to relocate it behind a closed form.
    ///
    /// Asserted as monotonicity plus a sign change rather than as
    /// "the error grows with sigma", which is false: the error is smallest
    /// AT the crossing and grows away from it in both directions.
    #[test]
    fn a_linear_correction_under_a_power_form_is_wrong_by_a_moving_factor() {
        let (p, g, gu) = (1.2, 22.3, 11.15);
        let ratio = |sigma: f64| {
            expected_return_spike(sigma, g, gu, p) / expected_return_spike(sigma, g, gu, 1.0)
        };
        let mut last = f64::NEG_INFINITY;
        for &sigma in &[0.25, 0.5, 0.91, 2.0, 4.0, 9.0] {
            let r = ratio(sigma);
            assert!(r > last, "the ratio did not rise at sigma {sigma}: {r} vs {last}");
            last = r;
        }
        assert!(ratio(0.25) < 1.0,
                "the linear correction did not over-correct a calm market: {}",
                ratio(0.25));
        assert!(ratio(9.0) > 1.0,
                "the linear correction did not under-correct a violent one: {}",
                ratio(9.0));
        assert!(ratio(9.0) / ratio(0.25) > 1.5,
                "a 36-fold volatility moved the correction by less than half: {}",
                ratio(9.0) / ratio(0.25));
    }

    /// THE SPIKE READS THE SESSION UNDER THE IDENTITY, whatever
    /// `vix_return_source` says, because the correction is sized for a
    /// session and applying it to a closing MINUTE would be twenty times
    /// the quantity it corrects.
    #[test]
    fn the_identity_reads_the_session_return_at_every_source() {
        let want = {
            let mut i = arm(1.0);
            i.vix_return_source = 1.0;
            vix_after(&i)
        };
        for &s in &[0.0, 0.25, 0.5, 0.75, 1.0] {
            let mut i = arm(1.0);
            i.vix_return_source = s;
            assert_eq!(vix_after(&i), want, "the source moved the VIX at {s}");
        }
        // The mirror: with the identity off the source blends the two
        // returns and they differ here, so every value gives a different
        // answer.
        let off: Vec<f64> = [0.0f64, 0.5, 1.0].iter().map(|&s| {
            let mut i = arm(0.0);
            i.vix_return_source = s;
            vix_after(&i)
        }).collect();
        assert!(off[0] != off[1] && off[1] != off[2],
                "the source moves nothing even with the identity off: {off:?}");
    }
}

#[cfg(test)]
mod vix_return_shape {
    use super::*;

    /// Every preset that predates the dial must reproduce bit for bit, and
    /// the claim is about THIS arithmetic, so it is asserted on bits over
    /// a spread of returns rather than on a tolerance at one point. The
    /// expectation is written out as the expression that stood here, not
    /// as a number, so a change to either side is caught.
    #[test]
    fn a_unit_exponent_is_the_shipped_arithmetic_to_the_bit() {
        let (gain, gain_up) = (30.0, 14.0);
        for i in -400..=400 {
            let r = i as f64 * 0.05;
            let want = if r < 0.0 { -r * gain } else { -r * gain_up };
            assert_eq!(return_spike_for(r, gain, gain_up, 1.0), want,
                       "moved at r = {r}");
        }
    }

    /// The sign convention the linear form set: a DOWN session frightens
    /// the market and an UP session calms it, at every exponent.
    #[test]
    fn a_down_session_raises_fear_and_an_up_session_lowers_it() {
        for &p in &[1.0, 1.132, 1.200, 1.287] {
            assert!(return_spike_for(-2.0, 30.0, 15.0, p) > 0.0, "down at p={p}");
            assert!(return_spike_for(2.0, 30.0, 15.0, p) < 0.0, "up at p={p}");
            assert_eq!(return_spike_for(0.0, 30.0, 15.0, p), 0.0, "zero at p={p}");
        }
    }

    /// THE UP SIDE IS LINEAR AT EVERY EXPONENT, because the tape says it
    /// is: fitted separately the up side reads 1.0410 at an R squared of
    /// 0.9655, which this data cannot distinguish from 1.0. So the
    /// exponent must not reach it, and an up session gives the same
    /// answer whatever the dial says.
    #[test]
    fn the_exponent_does_not_reach_the_up_side() {
        for i in 0..=200 {
            let r = i as f64 * 0.05;
            let want = -r * 15.0;
            for &p in &[1.0, 1.132, 1.200, 1.287, 1.8] {
                assert_eq!(return_spike_for(r, 30.0, 15.0, p), want,
                           "the up side moved at r={r}, p={p}");
            }
        }
    }

    /// CONVEXITY, which is the whole point of the dial: points of fear per
    /// per-cent of return must RISE with the size of the move, where the
    /// linear form holds them flat. The real curve rises from 1.00 to 1.54
    /// across this span and a constant gain cannot.
    #[test]
    fn the_response_per_per_cent_rises_only_above_a_unit_exponent() {
        let per_pct = |r: f64, p: f64| return_spike_for(-r, 30.0, 15.0, p) / r;
        for &r in &[0.71, 1.70, 2.75, 3.36, 6.39] {
            assert!((per_pct(r, 1.0) - 30.0).abs() < 1e-12,
                    "the linear form is not flat at r={r}");
        }
        let mut last = f64::NEG_INFINITY;
        for &r in &[0.71, 1.70, 2.75, 3.36, 6.39] {
            let v = per_pct(r, 1.200);
            assert!(v > last, "not rising at r={r}: {v} after {last}");
            last = v;
        }
    }

    /// The scale reproduces the FITTED curve, which is the only claim
    /// this arithmetic can make.
    ///
    /// `dVIX = 1.003 |r|^1.200` reaches the VIX through a transmission
    /// `c`, so a scale of `1.003 / c` puts the target's spike at
    /// `1.003/c * |r|^1.200` and the realised response back on the fit.
    /// Asserted to floating-point tolerance, because this is an identity
    /// about the code and not a measurement.
    ///
    /// **`c` CANCELS, and the test says so by varying it.** The first
    /// version pinned one value, 0.03138, which was the transmission
    /// measured on a VIX level since found to be 1.232x too low. Nothing
    /// went wrong -- the constant cancels between the scale and the
    /// response -- but a stale measured figure standing alone in a test
    /// reads as though the test depended on it, and the next reader has
    /// to derive the cancellation to find out that it does not. Three
    /// values spanning the compressed level, the corrected one and a
    /// number belonging to neither make the independence the assertion.
    #[test]
    fn the_derived_scale_reproduces_the_fitted_curve() {
        for &c in &[0.03138, 0.0600, 0.25] {
            let scale = 1.003 / c;
            for &r in &[0.710, 1.211, 1.704, 2.234, 2.746, 3.360, 4.415, 6.390] {
                let realised = c * return_spike_for(-r, scale, scale / 2.0, 1.200);
                let want = 1.003 * mathx::pow(r, 1.200);
                assert!((realised - want).abs() < 1e-9,
                        "c {c}, bucket {r}: realised {realised}, fit {want}");
            }
        }
    }

    /// And the fit's distance from the buckets it was fitted to is a
    /// property of the FIT, not of this code, so it is derived here rather
    /// than pinned: the tolerance is the worst residual the eight medians
    /// themselves produce. Written this way so that the day someone
    /// refits the curve, this test moves with it instead of failing for a
    /// reason that has nothing to do with the arithmetic under test.
    #[test]
    fn the_fit_sits_within_its_own_worst_residual_of_every_bucket() {
        const BUCKETS: [(f64, f64); 8] = [
            (0.710, 0.710), (1.211, 1.255), (1.704, 1.890), (2.234, 2.440),
            (2.746, 3.040), (3.360, 4.530), (4.415, 6.040), (6.390, 9.830)];
        let worst = BUCKETS.iter()
            .map(|&(r, d)| (1.003 * mathx::pow(r, 1.200) / d - 1.0).abs())
            .fold(0.0_f64, f64::max);
        // The fit is a least-squares line through eight points and does
        // not pass through any of them; a worst residual far above a
        // tenth would mean the power law is the wrong family, which is
        // the thing worth catching here.
        assert!(worst < 0.15,
                "the fit misses its worst bucket by {:.1} per cent, which is                  too far for a power law to be the right family",
                100.0 * worst);
        const C: f64 = 0.03138;
        let scale = 1.003 / C;
        for &(r, dvix) in BUCKETS.iter() {
            let realised = C * return_spike_for(-r, scale, scale / 2.0, 1.200);
            let err = (realised / dvix - 1.0).abs();
            assert!(err <= worst + 1e-9,
                    "bucket {r} is off by {:.1} per cent, worse than the                      fit's own worst residual of {:.1}",
                    100.0 * err, 100.0 * worst);
        }
    }
}

/// THE FEAR RESPONSE MUST KEEP RISING, and for nine shipped presets it
/// does not.
///
/// The defect this module makes permanent was measured on 2026-09-06 and
/// is recorded in the project's unpublished design notes.
/// `vix_target_shock_cap` is documented as a boundary condition, but at
/// pt-v16 — [`crate::params::DEFAULT_PRESET_NAME`] — it is 45.0 against a
/// `vix_return_gain` of 17.0, so it BINDS at 2.647 per cent of session
/// return. Above that the target cannot rise any further, and the model's
/// measured response per one per cent peaks at 0.989 and falls away to
/// 0.470 at a -7.1 per cent session, against a tape that rises
/// monotonically from 1.000 to 1.538. The certified default's crash
/// response is 0.32x of the real one, and a constant does it.
///
/// **A cap that binds inside the graded range is not a boundary
/// condition. It is a SHAPE parameter**, and it was an undeclared one:
/// the era's standing finding that the model is flat where the tape rises
/// was measured on pt-v18 arms carrying a fear vector whose cap of 150 or
/// 450 does not bind, while every shipped preset had it bound at 2.647.
///
/// So this asserts no value. It asserts the PROPERTY — the response rises
/// — and requires any preset that breaks it to say where. That turns a
/// silent shape change into a declared one, and it is the only form of
/// the test that can pass today and still fail the day somebody
/// reintroduces the defect.
///
/// # pt-v19 is the first preset that does not need a row here
///
/// Charter bar B4. The table below is unchanged for the eighteen presets
/// that shipped with the defect and pt-v19 has left it: its cap is the
/// image of its own clamp, so the cap cannot bind before the clamp, and
/// the clamp is at 15 per cent — outside [`GRADED_ABS_R`] and outside
/// anything this market produces.
///
/// **What made that affordable is one level down.** The cap was not an
/// arbitrary brake: the loop-gain run measured the index realising
/// four to five times the variance `V_t` priced above
/// `crisis_vix_threshold`, so the fear arm had nothing balancing it there
/// and the cap was holding the divergence. `market::index_var` now prices
/// the crash amplifier and the crisis blend — see
/// [`crate::market::index_var::amplifier_moments`] — so the read-back
/// carries the regime and the brake has a mechanism behind it instead of a
/// constant.
#[cfg(test)]
mod fear_response_shape {
    use super::*;
    use crate::params::ModelParams;

    /// The deepest `|r|` at which the tape supplies a conditional median
    /// to be wrong against: the median of its own `past -5 per cent`
    /// bucket, 22 sessions of ^GSPC 1990-2025. Measured rather than
    /// chosen, and past it the property is not asserted, because past it
    /// there is no real number to compare with.
    ///
    /// That bucket is the one the 2026-09-06 measurement records as MISMATCHED: on
    /// a pt-v16 arm the model put 336 of 7,560 sessions past -5 per cent
    /// against the tape's 22 of 8,959, at an index sd of 3.135 against
    /// about 1.1. The mismatch makes the bucket useless for comparing a
    /// conditional MEDIAN across the two. It does not touch the use here,
    /// which asks only how deep the tape grades at all: what is wanted is
    /// the far end of a domain, not a statistic conditional on landing in
    /// it.
    const GRADED_ABS_R: f64 = 6.390;

    /// The shallow end of the same table, the median of the tape's
    /// `-1.0 to -0.5 per cent` bucket. The pair bounds the range over
    /// which the response's shape is asserted.
    const SHALLOW_ABS_R: f64 = 0.710;

    /// The floor `update_economy_daily` clamps the VIX state to, so no
    /// session after the first is driven from a level under it. Pinned
    /// against the update itself in
    /// [`the_default_cap_is_the_clamps_own_image`] rather than copied off
    /// the line, because the cap's whole derivation rests on it.
    const VIX_STATE_FLOOR: f64 = 10.0;

    /// The index sigmas the zero-mean correction is asserted at, in per
    /// cent a session. 0.6 is a calm year, 1.0 is about the tape's
    /// unconditional level and 2.5 is a crisis. A correction computed with
    /// the wrong moment order is right at one sigma and wrong at the other
    /// two, which is the failure `expected_return_spike` documents and no
    /// single-sigma test can see.
    const SIGMAS: &[f64] = &[0.6, 1.0, 2.5];

    /// Levels to assert the response at, spanning the state floor to the
    /// shipped ceiling. It has to be a spread rather than one value:
    /// under pt-v19 the response is a function of two arguments, and a
    /// test that fixes one of them cannot see half the law.
    const LEVELS: &[f64] = &[10.0, 15.0, 21.0, 30.0, 60.0, 120.0, 181.3295];

    /// EVERY SHIPPED PRESET, and the return at which its fear response
    /// stops rising. There is no third column of presets that behave.
    ///
    /// Two dials do it, in two eras. The first eight clamp the driving
    /// return to 0.03 per cent, so their fear channel is spent before a
    /// session is a tenth of a typical one. The last nine cap the spike at
    /// 45 against a gain of 17 and stop at 2.647 per cent. Both are deep
    /// inside the range the tape grades, and the tape's own response rises
    /// across all of it, 1.000 points per per cent at -0.7 and 1.538 at
    /// -6.4.
    ///
    /// THE DEFECT WAS MOVED, NOT FIXED, and the tree says so in its own
    /// words without anyone having joined them up.
    /// [`crate::params::ModelParams::vix_return_clamp`] documents the first
    /// era exactly: "Shipped 0.03, so a -10% day and a -3% day produce
    /// identical fear. A crash is exactly where that assumption is worst."
    /// pt-v9 raised that clamp from 0.03 to 15 and set a cap of 45 against
    /// a gain of 17, which reinstates identical fear from 2.647 per cent
    /// up. The binding constraint moved by a factor of 88 and stopped
    /// being documented; it never went away.
    ///
    /// Each entry is a declared DEFECT, not a permission. The list is
    /// exhaustive in both directions and the binding dial is recomputed
    /// rather than trusted, so the table cannot rot either way.
    ///
    /// Every preset in this table is LEVEL-BLIND on the down side, which
    /// is what lets one number describe it. That is asserted rather than
    /// assumed: a preset whose response depends on the VIX it opened from
    /// has a different flattening point at every level, so a row here
    /// would be a reading of one level pretending to be a property.
    const FLATTENS_AT: &[(&str, f64, Binder)] = &[
        ("pt-v1", 0.030, Binder::Clamp),
        ("pt-v2", 0.030, Binder::Clamp),
        ("pt-v3", 0.030, Binder::Clamp),
        ("pt-v4", 0.030, Binder::Clamp),
        ("pt-v5", 0.030, Binder::Clamp),
        ("pt-v6", 0.030, Binder::Clamp),
        ("pt-v7", 0.030, Binder::Clamp),
        ("pt-v8", 0.030, Binder::Clamp),
        ("pt-v9", 2.647, Binder::Cap),
        ("pt-v10", 2.647, Binder::Cap),
        ("pt-v11", 2.647, Binder::Cap),
        ("pt-v12", 2.647, Binder::Cap),
        ("pt-v13", 2.647, Binder::Cap),
        ("pt-v14", 2.647, Binder::Cap),
        ("pt-v15", 2.647, Binder::Cap),
        ("pt-v16", 2.647, Binder::Cap),
        ("pt-v18", 2.647, Binder::Cap),
        // pt-v19 IS NOT HERE, and that is charter bar B4 met. Its cap is
        // the image of its own clamp under the shipped law, evaluated at
        // the VIX floor: `gain * clamp^p * floor^(-g)` =
        // `8.83 * 15^1.4483 * 10^-0.4483` = 158.8524. That is the spike's
        // supremum over the whole domain the update admits, so the cap
        // cannot bind anywhere the clamp does not, and the clamp sits at
        // 15 per cent, outside `GRADED_ABS_R` and outside anything this
        // market produces. Its response rises across the whole graded
        // range at every level, `flattens_at` returns `None` for it, and
        // that is the `(None, None)` arm below.
        //
        // The dial was not raised on its own. `market::index_var` prices
        // the crash amplifier and the crisis blend, so the read-back
        // carries the regime and the fear arm has a mechanism balancing it
        // above `crisis_vix_threshold`; the cap had been the brake standing
        // in for that. See `ModelParams::vix_target_shock_cap`.
    ];

    /// Which dial ends the rise. Named rather than inferred at the call
    /// site, because "the response goes flat" is one symptom of two quite
    /// different defects, and a fix for one does nothing for the other.
    #[derive(Debug, PartialEq, Eq, Clone, Copy)]
    enum Binder {
        /// `vix_return_clamp` truncates the driving return itself.
        Clamp,
        /// `vix_target_shock_cap` truncates the spike it produces.
        Cap,
    }

    /// THE LAW THIS MODULE ASSERTS, in one place.
    ///
    /// `update_economy_daily` builds the VIX target from a SIGNED session
    /// return `r` and the VIX `v` the session opened from. With the
    /// inflation and shock adders at zero, so this is the fear channel
    /// alone:
    ///
    /// ```text
    /// x      = clip(r, -clamp, +clamp)
    /// S(x,v) =  gain    |x|^exponent     v^(-level_exponent)      x < 0
    ///        = -gain_up  x ^exponent_up  v^(-level_exponent_up)   x > 0
    ///        =  0                                                 x = 0
    /// target = implied + min(cap, S) - E[S | v, sigma]
    /// v'     = clip(v + mr (target - v), 10, ceiling)
    /// ```
    ///
    /// At pt-v19's dials: gain 8.83, exponent 1.4483, level_exponent
    /// 0.4483, gain_up 0.049, exponent_up 0.5433, level_exponent_up -1.0,
    /// clamp 15, cap 158.8524, mr 0.27, decay ratio 1.0, ceiling 181.3295.
    /// So the down side is CONVEX in the move and FALLS with the level,
    /// and the up side is CONCAVE in the move and is PROPORTIONAL to the
    /// level, `level_exponent_up` being exactly -1.
    ///
    /// The measurement is a fit of the VIX's session change to the index
    /// return, on 8,959 sessions of ^GSPC: `g_dn` = +0.49 +/- 0.12 with
    /// P(g > 0) = 1.000, `p_dn` = 1.44 +/- 0.08, `g_up` = -0.85 +/- 0.12,
    /// `p_up` = 0.60 +/- 0.04. The level-blind power form is refused
    /// against the free form at F = 118 on 2 dof and the linear
    /// level-blind form, which is what this module used to assert, at
    /// F = 105 on 4.
    ///
    /// The zero-mean subtraction is not part of the shape: it is a
    /// constant of the day rather than a function of the return, so it
    /// lives in [`zero_mean_correction`] and enters only where the STATE
    /// is asserted.
    fn fear_term(p: &ModelParams, r: f64, vix: f64) -> f64 {
        let clamped = mathx::max(-p.vix_return_clamp, mathx::min(p.vix_return_clamp, r));
        let spike = return_spike_at_level(
            clamped,
            p.vix_return_gain,
            p.vix_return_gain_up,
            p.vix_return_exponent,
            p.vix_return_exponent_up,
            p.vix_return_level_exponent,
            p.vix_return_level_exponent_up,
            vix,
        );
        mathx::min(p.vix_target_shock_cap, spike)
    }

    /// [`fear_term`] on a DOWN session of size `r`, `r` a positive
    /// magnitude. The sign flip is here and nowhere else.
    fn response(p: &ModelParams, r: f64, vix: f64) -> f64 {
        fear_term(p, -r, vix)
    }

    /// [`fear_term`] on an UP session of size `r`, `r` a positive
    /// magnitude. Negative, because an up session gives VIX back.
    fn give_back(p: &ModelParams, r: f64, vix: f64) -> f64 {
        fear_term(p, r, vix)
    }

    /// The smallest down session in the graded range at which the response
    /// stops rising, FROM A STATE AT `vix`, or `None` if it never does.
    /// Swept at 0.0001 per cent, so a binding point anywhere in the range
    /// is located to three decimals.
    ///
    /// The level argument is the whole point. Under pt-v19 the cap's
    /// binding session is `(cap * v^g / gain)^(1/p)`, which moves from
    /// 15.00 at the floor to 36.78 at the ceiling, so a sweep at one level
    /// answers a question about that level and nothing else.
    fn flattens_at(p: &ModelParams, vix: f64) -> Option<f64> {
        let steps = 63_900usize;
        let mut prev = response(p, 0.0, vix);
        for i in 1..=steps {
            let r = GRADED_ABS_R * i as f64 / steps as f64;
            let now = response(p, r, vix);
            if now <= prev {
                return Some(r);
            }
            prev = now;
        }
        None
    }

    /// The down session at which `vix_target_shock_cap` starts truncating,
    /// from the dials alone and with the level in it:
    ///
    /// ```text
    /// cap = gain |r|^p v^(-g)   =>   r = (cap v^g / gain)^(1/p)
    /// ```
    ///
    /// At `g` = 0 this is the `(cap / gain)^(1/p)` the level-blind module
    /// computed, so the eighteen presets that predate the level classify
    /// exactly as they did.
    fn cap_binds_at(p: &ModelParams, vix: f64) -> f64 {
        if p.vix_return_gain <= 0.0 {
            return f64::INFINITY;
        }
        let level = mathx::pow(vix, p.vix_return_level_exponent);
        mathx::pow(
            p.vix_target_shock_cap * level / p.vix_return_gain,
            1.0 / p.vix_return_exponent,
        )
    }

    /// The level at which the cap comes closest to binding, which is where
    /// the spike is largest. The down spike carries `v^(-g)`, so at a
    /// positive `g` that is the state floor and at a negative one the
    /// ceiling. A comparison made at any other level would pass while the
    /// cap bound somewhere the model can actually reach.
    fn most_binding_level(p: &ModelParams) -> f64 {
        if p.vix_return_level_exponent >= 0.0 {
            VIX_STATE_FLOOR
        } else {
            p.vix_ceiling
        }
    }

    /// Which dial binds first, from the dials alone. Derived here and
    /// compared against the table, so the table records a reading of the
    /// preset rather than a memory of one.
    fn binder_of(p: &ModelParams) -> Binder {
        if p.vix_return_clamp <= cap_binds_at(p, most_binding_level(p)) {
            Binder::Clamp
        } else {
            Binder::Cap
        }
    }

    /// Whether the down response depends on the level the session opened
    /// from. `false` for pt-v1 through pt-v18 and `true` for pt-v19.
    fn down_is_level_dependent(p: &ModelParams) -> bool {
        p.vix_return_level_exponent != 0.0
    }

    /// `E|r|^k` for `r ~ N(0, sigma^2)`, the Gaussian absolute moment of
    /// order `k`. Spelled out from the identity rather than called out of
    /// `expected_return_spike_at_level`, so that a dropped level factor or
    /// a wrong moment order in the implementation is exactly what the
    /// state tests below catch.
    fn abs_moment(sigma: f64, k: f64) -> f64 {
        mathx::pow(sigma, k) * mathx::pow(2.0, 0.5 * k) * mathx::tgamma(0.5 * (k + 1.0))
            / mathx::sqrt(core::f64::consts::PI)
    }

    /// `E[S]`, the standing excursion an asymmetric gain injects and the
    /// identity subtracts each day:
    ///
    /// ```text
    /// E[S] = 0.5 gain    v^(-level_exponent)    E|r|^exponent
    ///      - 0.5 gain_up v^(-level_exponent_up) E|r|^exponent_up
    /// ```
    ///
    /// It is a function of the level as well as the sigma, and it changes
    /// SIGN: at pt-v19's dials and sigma 1 it is +1.137 at a VIX of 10 and
    /// -3.259 at the ceiling, because the up side's give-back is
    /// proportional to the level while the down side's response falls with
    /// it. The pair crosses near a VIX of 41.
    fn zero_mean_correction(p: &ModelParams, sigma: f64, vix: f64) -> f64 {
        let down = mathx::pow(vix, -p.vix_return_level_exponent);
        let up = mathx::pow(vix, -p.vix_return_level_exponent_up);
        0.5 * (p.vix_return_gain * down * abs_moment(sigma, p.vix_return_exponent)
            - p.vix_return_gain_up * up * abs_moment(sigma, p.vix_return_exponent_up))
    }

    #[test]
    fn every_preset_declares_where_its_fear_response_stops_rising() {
        let mut wrong: Vec<String> = Vec::new();
        for name in ModelParams::preset_names() {
            let p = ModelParams::preset(name).expect("a name from preset_names resolves");
            let declared = FLATTENS_AT.iter().find(|(n, _, _)| n == name);

            // A declared row is one number, so it may only describe a
            // preset whose response is the same at every level. Checked
            // before the sweep, because if it fails the sweep's answer is
            // not the kind of thing the row claims to be.
            if declared.is_some() && down_is_level_dependent(&p) {
                wrong.push(format!(
                    "{name} has a row in FLATTENS_AT and a level-dependent response \
                     (vix_return_level_exponent {}). Its flattening point is a different \
                     number at every VIX, so one value cannot state it.",
                    p.vix_return_level_exponent
                ));
            }

            // The earliest flattening point over the levels the state can
            // actually occupy. For a level-blind preset every level gives
            // the same answer and the first one settles it.
            let mut earliest: Option<(f64, f64)> = None;
            for &vix in LEVELS {
                if let Some(at) = flattens_at(&p, vix) {
                    if earliest.is_none_or(|(best, _)| at < best) {
                        earliest = Some((at, vix));
                    }
                }
                if !down_is_level_dependent(&p) {
                    break;
                }
            }

            match (earliest, declared) {
                (Some((at, vix)), Some((_, want, want_binder))) => {
                    if (at - want).abs() >= 0.002 {
                        wrong.push(format!(
                            "{name} stops rising at {at:.3} per cent from a VIX of {vix}, not \
                             the declared {want:.3}. Read why the number moved before editing \
                             the table."
                        ));
                    }
                    let binder = binder_of(&p);
                    if binder != *want_binder {
                        wrong.push(format!(
                            "{name} is declared bound by {want_binder:?} but its dials say \
                             {binder:?}. Those are different defects."
                        ));
                    }
                }
                (Some((at, vix)), None) => wrong.push(format!(
                    "{name} stops rising at {at:.3} per cent from a VIX of {vix} and declares \
                     nothing. A response that goes flat inside the range the tape grades is a \
                     SHAPE change, not a boundary condition, and it must be declared."
                )),
                (None, Some(_)) => wrong.push(format!(
                    "{name} is declared in FLATTENS_AT but its response rises the whole \
                     way at every level. Remove it: a table that grants permission nobody \
                     needs will one day grant it to a preset that does."
                )),
                // Nothing to say: it rises across the graded range at every
                // level the state can occupy, which is what a fear channel
                // is for. pt-v19 is the only preset on this arm.
                (None, None) => {}
            }
        }
        assert!(wrong.is_empty(), "{}", wrong.join("\n"));
    }

    /// **THE SHIPPED DEFAULT'S FEAR RESPONSE RISES ACROSS THE GRADED
    /// RANGE, FROM EVERY LEVEL THE STATE CAN OCCUPY.** That is charter bar
    /// B4, and it was unmet by every preset this table has a row for.
    ///
    /// This test used to assert the opposite, then it asserted the
    /// positive form at one level. Neither could see the shipped law: the
    /// response has two arguments now, and the version that swept only
    /// `|r|` would have passed unchanged if `vix_return_level_exponent`
    /// were silently set back to zero.
    ///
    /// WHAT THIS CATCHES. A cap or clamp moved back inside the graded
    /// range at any level, which is the pt-v9 defect returning. A response
    /// that rises but by a millionth of a point, which the sweep alone
    /// would accept. And a level factor whose sign flipped, because a
    /// response that ROSE with the level would put its flattening point at
    /// the ceiling rather than the floor and `most_binding_level` would
    /// follow it there.
    #[test]
    fn the_shipped_default_carries_its_fear_response_across_the_graded_range() {
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        for &vix in LEVELS {
            assert!(
                flattens_at(&p, vix).is_none(),
                "from a VIX of {vix} the default's fear response flattens at {:?} per cent, \
                 inside the {GRADED_ABS_R} per cent the tape grades. That is charter bar B4 \
                 lost again: read `ModelParams::vix_target_shock_cap` before adding a row to \
                 FLATTENS_AT.",
                flattens_at(&p, vix)
            );
            // RISING, not merely non-flattening, and by the exponent the
            // tape measured. `flattens_at` sweeps at 0.0001 per cent and
            // returns on the first non-increase, so it would accept a
            // response that rose by a millionth of a point.
            let shallow = response(&p, SHALLOW_ABS_R, vix);
            let deep = response(&p, GRADED_ABS_R, vix);
            assert!(
                deep > 8.0 * shallow,
                "from a VIX of {vix} the response at -{GRADED_ABS_R} per cent is {deep:.2} \
                 against {shallow:.2} at -{SHALLOW_ABS_R}, a ratio of {:.2}. The tape's own \
                 conditional medians rise from 0.710 to 9.830 over that range, a ratio of 13.8.",
                deep / shallow
            );
            // AND THE RATIO IS THE SAME AT EVERY LEVEL, which is the
            // separability the law claims: `S = gain |r|^p v^(-g)` factors,
            // so the level moves the SCALE of the response and never its
            // shape. 24.101 here, `(6.390 / 0.710)^1.4483`.
            let want = mathx::pow(GRADED_ABS_R / SHALLOW_ABS_R, p.vix_return_exponent);
            assert!(
                (deep / shallow - want).abs() < 1e-9,
                "from a VIX of {vix} the deep-to-shallow ratio is {} where the exponent alone \
                 says {want}. The response has stopped factoring into a level and a shape.",
                deep / shallow
            );
        }
        // AND THE CAP IS WHERE THE DERIVATION PUTS IT, which is the other
        // half: a cap raised to a round number nobody can defend would pass
        // every assertion above.
        assert_eq!(binder_of(&p), Binder::Clamp, "the clamp is the binding dial now");
        the_default_cap_is_the_clamps_own_image();
    }

    /// **THE DOWN RESPONSE FALLS WITH THE LEVEL AND THE UP RESPONSE IS
    /// PROPORTIONAL TO IT.** Nothing in this module asserted that until
    /// 2026-09-14, which is how the level-blind spellings survived the
    /// preset that replaced them.
    ///
    /// A second reading of the same tape measures it without
    /// a fit, as conditional medians of `dV` by level tertile within an
    /// `|r|` bin: in the 2 to 3 per cent bin the tape's down response is
    /// 3.32 at a VIX of 18.8, 2.52 at 24.5 and 2.17 at 32.1, and its up
    /// response over the same bin is -1.76, -2.17 and -2.40. Down falls,
    /// up grows, before any form is imposed. Section 2.2 puts
    /// `g_dn` at +0.49 +/- 0.12 with P(g > 0) = 1.000 and `g_up` at
    /// -0.85 +/- 0.12 against the shipped -1.
    ///
    /// WHAT THIS CATCHES. `vix_return_level_exponent` set back to 0, which
    /// is the change that turns the shipped law back into the one this
    /// module used to assert and which every other test here would accept.
    /// A level factor applied to the wrong side. And the up side losing
    /// its concavity, which is the half of the measurement that has no
    /// error bar overlapping 1.0 at all.
    #[test]
    fn the_down_response_falls_with_the_level_and_the_up_response_rises_with_it() {
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        assert!(
            p.vix_return_level_exponent > 0.0,
            "the down response no longer falls with the level. The tape puts g_dn at \
             +0.49 +/- 0.12 with P(g > 0) = 1.000 and refuses the level-blind power form \
             at F = 118 on 2 dof."
        );
        assert!(
            p.vix_return_level_exponent_up < 0.0,
            "the up response no longer rises with the level. The tape puts g_up at \
             -0.85 +/- 0.12, P(g_up > 0) = 0.000."
        );
        assert!(
            p.vix_return_exponent > 1.0 && p.vix_return_exponent_up < 1.0,
            "the down side is convex in the move and the up side concave: p_dn \
             1.44 +/- 0.08, p_up 0.60 +/- 0.04 with P(p_up > 1) = 0.000. Read \
             {} and {}.",
            p.vix_return_exponent,
            p.vix_return_exponent_up
        );
        for &r in &[0.710, 2.5, GRADED_ABS_R] {
            let base_dn = response(&p, r, LEVELS[0]);
            let base_up = give_back(&p, r, LEVELS[0]);
            assert!(base_dn > 0.0 && base_up < 0.0, "a down session raises the VIX and an up \
                     session gives it back: {base_dn} and {base_up}");
            for &vix in LEVELS {
                let want_dn = mathx::pow(vix / LEVELS[0], -p.vix_return_level_exponent);
                let got_dn = response(&p, r, vix) / base_dn;
                assert!(
                    (got_dn - want_dn).abs() < 1e-9,
                    "the down response at -{r} scales by {got_dn} from a VIX of {} to {vix}, \
                     where `v^(-g)` says {want_dn}",
                    LEVELS[0]
                );
                let want_up = mathx::pow(vix / LEVELS[0], -p.vix_return_level_exponent_up);
                let got_up = give_back(&p, r, vix) / base_up;
                assert!(
                    (got_up - want_up).abs() < 1e-9,
                    "the up give-back at +{r} scales by {got_up} from a VIX of {} to {vix}, \
                     where `v^(-g_up)` says {want_up}",
                    LEVELS[0]
                );
            }
        }
        // The size of it, stated so a reader has the number: over the floor
        // to the ceiling the same -6.39 per cent session buys 46.16 points
        // of target at a VIX of 10 and 12.59 at 181.33, a factor of 0.273.
        // A level-blind law would put both at 129.59 and this ratio at 1.
        let at_floor = response(&p, GRADED_ABS_R, VIX_STATE_FLOOR);
        let at_ceiling = response(&p, GRADED_ABS_R, p.vix_ceiling);
        assert!(
            (at_floor - 46.1602).abs() < 1e-3 && (at_ceiling - 12.5920).abs() < 1e-3,
            "the graded-top response reads {at_floor} at the floor and {at_ceiling} at the \
             ceiling, where the dials say 46.1602 and 12.5920"
        );
        // And the up side is CONCAVE in the move: doubling an up session
        // buys 2^0.5433 = 1.457 times the give-back, not twice.
        let one = give_back(&p, 1.0, 21.0).abs();
        let two = give_back(&p, 2.0, 21.0).abs();
        assert!(
            (two / one - mathx::pow(2.0, p.vix_return_exponent_up)).abs() < 1e-9 && two < 2.0 * one,
            "doubling an up session multiplied the give-back by {}, not by 2^p_up",
            two / one
        );
    }

    /// A draw-free RNG, as `vix_level_identity` above defines one: the
    /// economy's own noise on the VIX is `N(0, 0.15)` and with it silent the
    /// update below is arithmetic on the dials, so it can be asserted to
    /// 1e-9 rather than to a tolerance that would hide a wrong term.
    struct Silent;
    impl crate::rng::Rng for Silent {
        fn next_f64(&mut self) -> f64 {
            0.5
        }
        fn next_normal(&mut self) -> f64 {
            0.0
        }
    }

    /// The VIX the default preset's update leaves after ONE session of
    /// `session_pct` (negative is a down day), from a state `vix` whose
    /// read-back is `implied`, with the index's conditional sigma at
    /// `sigma_pct`.
    ///
    /// Every other term of the target is zero here and the reason is
    /// stated for each: inflation sits at 2 so its adder is zero, there is
    /// no active shock, the jump dials are left at the inputs' zero so the
    /// branch is not taken, and the RNG is silent so the innovation is
    /// zero whatever scale it would have had.
    ///
    /// The gains are NOT equal any more, which is the change that made the
    /// version of this helper standing here permanently red. Under pt-v19
    /// `vix_return_gain` is 8.83 and `vix_return_gain_up` is 0.049, the
    /// excursion they inject is standing and positive at low levels and
    /// negative at high ones, and the identity cancels it with
    /// `expected_return_spike_at_level` rather than by keeping the two
    /// dials equal. So the term is carried in the arithmetic below instead
    /// of being assumed away, and the level exponents are passed through,
    /// which the old helper did not do: it left them at `Default::default`
    /// and so drove a LEVEL-BLIND update while claiming to test the
    /// shipped preset.
    fn vix_after_one_session(vix: f64, implied: f64, sigma_pct: f64, session_pct: f64) -> f64 {
        use crate::economy::state::{create_initial_economy_state, InitialEconomyOptions};
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        assert_eq!(p.vix_level_identity, 1.0, "the arithmetic below is the identity's");
        let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
        economy.vix = vix;
        economy.inflation_rate = 2.0;
        let inputs = DailyInputs {
            vix_level_identity: p.vix_level_identity,
            vix_implied_from_market: implied,
            vix_index_sigma_pct: sigma_pct,
            market_day_return_pct: session_pct,
            vix_mean_reversion: p.vix_mean_reversion,
            vix_decay_ratio: p.vix_decay_ratio,
            vix_return_gain: p.vix_return_gain,
            vix_return_gain_up: p.vix_return_gain_up,
            vix_return_exponent: p.vix_return_exponent,
            vix_return_exponent_up: p.vix_return_exponent_up,
            vix_return_level_exponent: p.vix_return_level_exponent,
            vix_return_level_exponent_up: p.vix_return_level_exponent_up,
            vix_return_clamp: p.vix_return_clamp,
            vix_target_shock_cap: p.vix_target_shock_cap,
            vix_ceiling: p.vix_ceiling,
            vix_return_source: p.vix_return_source,
            game_day: 40,
            ..Default::default()
        };
        update_economy_daily(&economy, &inputs, &mut Silent).vix
    }

    /// The same step written from the identity rather than from the code:
    /// the target is the read-back plus the capped fear term minus that
    /// term's own mean, the state moves `vix_mean_reversion` of the way to
    /// it (`vix_decay_ratio` of that when the target is BELOW the state),
    /// and the result is clamped to `[10, vix_ceiling]`.
    fn vix_step_by_hand(
        p: &ModelParams,
        vix: f64,
        implied: f64,
        sigma_pct: f64,
        session_pct: f64,
    ) -> f64 {
        let target =
            implied + fear_term(p, session_pct, vix) - zero_mean_correction(p, sigma_pct, vix);
        let mr = if target < vix {
            p.vix_mean_reversion * p.vix_decay_ratio
        } else {
            p.vix_mean_reversion
        };
        mathx::max(
            VIX_STATE_FLOOR,
            mathx::min(p.vix_ceiling, vix + (target - vix) * mr),
        )
    }

    /// **THE CEILING CLAMPS THE STATE, AND THE STATE MOVES
    /// `vix_mean_reversion` OF THE WAY TO THE TARGET IN A DAY.**
    ///
    /// The test that stood here asserted `vix_ceiling == vix_return_gain *
    /// GRADED_ABS_R` and called the quotient "the session at which fear
    /// alone reaches the ceiling". Both halves were arithmetic on the dials
    /// and neither was about the quantity the ceiling clamps. The clamp at
    /// `:1291` is applied to `vix + (target - vix) * vix_mean_reversion`,
    /// the STATE after the reversion step, so a session of `r` from a state
    /// `x` with a read-back `I` leaves
    ///
    ///     x' = clamp(x + mr (I + min(cap, S(r, x)) - E[S | x] - x), 10, C)
    ///
    /// and "fear alone", from rest at a read-back of 21, moves the VIX by
    /// `0.27 * (33.099 - 0.539)` = 8.791 points rather than the 108.63 the
    /// old derivation named. Three terms of that were missing from the test
    /// this replaces: the exponent, the level factor, and the zero-mean
    /// subtraction, which is only zero when the two gains are equal and
    /// they are not.
    ///
    /// WHAT THIS CATCHES. Any change to the order the update composes the
    /// clamp, the spike, the cap and the reversion in. A zero-mean
    /// correction computed at the wrong moment order, which is right at one
    /// sigma and wrong at the others, so three sigmas are swept. A level
    /// factor dropped from either the spike or the correction, which the
    /// spread of read-backs makes visible. And the asymmetric decay ratio
    /// being applied on the wrong side of the comparison, which the up
    /// sessions here reach.
    #[test]
    fn a_graded_session_moves_the_state_its_reversion_share_of_the_way_to_its_target() {
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        let m = p.vix_mean_reversion;
        for &sigma in SIGMAS {
            for i in 0..=45 {
                let implied = 15.0 + i as f64;
                // From REST: the state is the read-back, which is what
                // "rest" means under the identity.
                for &session in &[-GRADED_ABS_R, -1.0, 0.0, 1.0, GRADED_ABS_R] {
                    let want = vix_step_by_hand(&p, implied, implied, sigma, session);
                    let got = vix_after_one_session(implied, implied, sigma, session);
                    assert!(
                        (got - want).abs() < 1e-9,
                        "from rest at {implied} with sigma {sigma} and a session of {session}: \
                         the update left {got}, the arithmetic says {want}"
                    );
                }
                // And away from rest, so the reversion term is not
                // multiplying a quantity that happens to be the fear term
                // alone.
                let away = implied + 12.0;
                let want = vix_step_by_hand(&p, away, implied, sigma, -GRADED_ABS_R);
                let got = vix_after_one_session(away, implied, sigma, -GRADED_ABS_R);
                assert!(
                    (got - want).abs() < 1e-9,
                    "from {away} against a read-back of {implied}: the update left {got}, \
                     the arithmetic says {want}"
                );
                assert!(
                    got < p.vix_ceiling - 100.0,
                    "a graded session from {away} reached {got}; the ceiling is {} and the \
                     old test's 'fear alone reaches it at 6.39 per cent' was never true of \
                     the state",
                    p.vix_ceiling
                );
            }
        }
        // The from-rest image of the graded range on the state, stated as a
        // number so the next reader has it: at a read-back of 21 it is
        // 21 + 8.791, where the level-blind law with the gain that shipped
        // at pt-v16 put it at 21 + 10.863 and the derivation this replaces
        // called it 108.63.
        let from_rest = vix_after_one_session(21.0, 21.0, 1.0, -GRADED_ABS_R);
        let fear = response(&p, GRADED_ABS_R, 21.0) - zero_mean_correction(&p, 1.0, 21.0);
        assert!((from_rest - (21.0 + m * fear)).abs() < 1e-9, "{from_rest}");
        assert!(
            (from_rest - 29.791253).abs() < 1e-4,
            "the graded session from rest at 21 leaves {from_rest}, where the dials say \
             29.791253. If the dials moved, re-derive this rather than editing it."
        );
    }

    /// **A VIX AT THE CEILING IS HELD THERE BY A SESSION EXACTLY WHEN THE
    /// TARGET IS AT OR ABOVE THE CEILING**, `I + min(cap, S(r, C)) - E[S|C]
    /// >= C`, and under the shipped law that needs a read-back of 134.74.
    ///
    /// The version of this test that stood here inverted the threshold as
    /// `(C - I) / gain`, which is the level-blind law's spelling. At a
    /// read-back of 40 it reads a session of 16.00 per cent on pt-v19's own
    /// gain (8.31 on the gain of 17 it was written against) where the
    /// shipped law needs 33.39, which the clamp does not admit at all. Two
    /// things move it. The response at the ceiling carries `C^(-0.4483)`,
    /// so the same session buys 12.59 points there against 46.16 at the
    /// floor. And the target also subtracts `E[S|C]`, which is NEGATIVE at
    /// the ceiling (-3.259 at sigma 1) because the up side's give-back is
    /// proportional to the level, so it pushes the threshold the other way.
    ///
    /// The finding, and it is new. The engine admits sessions up to
    /// `vix_return_clamp` at 15 per cent, and the largest spike that can
    /// reach a state at the ceiling is `8.83 * 15^1.4483 * 181.3295^-0.4483`
    /// = 43.333 points. So the ceiling is reachable at all only from a
    /// settled read-back above `C - 43.333 + E[S|C]` = 134.74 at sigma 1
    /// and 133.20 at sigma 3. The ceiling's derivation records the
    /// headroom as "any settled read-back below 168.74"; that number is the
    /// condition evaluated at the top of the GRADED range, `r` = 6.39,
    /// where the fear term is 12.59, and the domain the update admits runs
    /// to the clamp instead. The condition still holds on the map, whose
    /// closed form puts `implied(181.3295)` at 70.2 to 75.9 across three
    /// rosters, and it holds against the map's absolute upper bound of
    /// 121.8 to 134.5 by 0.24 points at sigma 1 on the worst roster and
    /// fails it by 1.30 at sigma 3. Nothing on the record goes near either:
    /// b4fix6's census reads 0 of 30,240 seed-days at the clamp and the
    /// measured maximum VIX on this vector is 60.59.
    ///
    /// WHAT THIS CATCHES. The threshold inverted without the level factor,
    /// which is the bug this replaces. The cap reintroduced somewhere it
    /// truncates a reachable session. And the non-stickiness the ceiling's
    /// `derived` kind rests on: the last assertion drives the worst
    /// admissible session from the highest read-back the map produces and
    /// requires the state to come off the clamp.
    /// The step with a pinned read-back, so the anchor reversion can be read
    /// on its own. Everything else is the shipped identity vector.
    fn vix_step_with_anchor(
        kappa: f64,
        anchor_level: f64,
        vix: f64,
        implied: f64,
        sigma_pct: f64,
        session_pct: f64,
    ) -> f64 {
        use crate::economy::state::{create_initial_economy_state, InitialEconomyOptions};
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
        economy.vix = vix;
        economy.inflation_rate = 2.0;
        let inputs = DailyInputs {
            vix_level_identity: p.vix_level_identity,
            vix_implied_from_market: implied,
            vix_index_sigma_pct: sigma_pct,
            market_day_return_pct: session_pct,
            vix_mean_reversion: p.vix_mean_reversion,
            vix_decay_ratio: p.vix_decay_ratio,
            vix_return_gain: p.vix_return_gain,
            vix_return_gain_up: p.vix_return_gain_up,
            vix_return_exponent: p.vix_return_exponent,
            vix_return_exponent_up: p.vix_return_exponent_up,
            vix_return_level_exponent: p.vix_return_level_exponent,
            vix_return_level_exponent_up: p.vix_return_level_exponent_up,
            vix_return_clamp: p.vix_return_clamp,
            vix_target_shock_cap: p.vix_target_shock_cap,
            vix_ceiling: p.vix_ceiling,
            vix_return_source: p.vix_return_source,
            vix_anchor_reversion: kappa,
            vix_anchor_level: anchor_level,
            game_day: 40,
            ..Default::default()
        };
        update_economy_daily(&economy, &inputs, &mut Silent).vix
    }

    /// **AT 0.0 THE TERM IS NOT ADDED**, whatever anchor is threaded beside
    /// it, and the step is the sum it was before the dial existed.
    ///
    /// Bit-identity, not closeness: `x + 0.0` is not a no-op on a negative
    /// zero, so the zero arm is a BRANCH in `daily.rs` and this reads the
    /// raw bits of both sides. The anchor passed on the left is the derived
    /// one the engine threads on the certified roster, which is nowhere near
    /// any of these states, so a term that ran would be visible in the top
    /// bits and not only the last.
    #[test]
    fn the_anchor_reversion_at_zero_is_the_step_that_stood_before_it() {
        for &vix in &[10.5, 15.0, 23.25, 40.0, 90.0] {
            for &implied in &[12.0, 21.0, 60.0] {
                for &session in &[-3.0, 0.0, 1.5] {
                    let with = vix_step_with_anchor(0.0, 23.249857144842903, vix, implied,
                                                    1.0, session);
                    let without = vix_step_with_anchor(0.0, 0.0, vix, implied, 1.0, session);
                    let plain = vix_after_one_session(vix, implied, 1.0, session);
                    assert_eq!(
                        with.to_bits(), plain.to_bits(),
                        "the anchor reversion at 0.0 moved the step at VIX {vix}, \
                         read-back {implied}, session {session}: {with} against {plain}"
                    );
                    assert_eq!(without.to_bits(), plain.to_bits());
                }
            }
        }
    }

    /// **THE REVERSION MOVES THE VIX TOWARD `L * anchor`**, by exactly
    /// `kappa` of the distance, on a day whose variance is pinned.
    ///
    /// The read-back is held at the state's own value and the session return
    /// is zero, so `vix_mean_reversion` has almost nothing to carry and what
    /// the state does is the new term. Three things are asserted: the size is
    /// the closed form, the direction is toward the anchor from both sides,
    /// and the anchor is a FIXED POINT of the added term rather than of the
    /// whole step.
    #[test]
    fn the_anchor_reversion_moves_the_vix_toward_the_level_times_the_anchor() {
        let kappa = 0.046081;
        let anchor = 23.249857144842903;
        for &level in &[1.0, 0.8, 1.25] {
            let target = level * anchor;
            for &vix in &[12.0, 18.0, 23.249857144842903, 30.0, 55.0] {
                let base = vix_step_with_anchor(0.0, target, vix, vix, 1.0, 0.0);
                let with = vix_step_with_anchor(kappa, target, vix, vix, 1.0, 0.0);
                let pull = kappa * (target - vix);
                assert!(
                    (with - base - pull).abs() < 1e-12,
                    "at level {level}, VIX {vix}: the term should be {pull}, read \
                     {} ({with} against {base})", with - base
                );
                if vix < target {
                    assert!(with > base, "below the anchor the reversion must lift");
                } else if vix > target {
                    assert!(with < base, "above the anchor the reversion must pull down");
                } else {
                    assert_eq!(with.to_bits(), base.to_bits(),
                               "at the anchor the term is exactly zero");
                }
            }
        }
    }

    /// The step with the anchor in the TARGET rather than in the rate.
    fn vix_step_with_weight(weight: f64, anchor_level: f64, vix: f64, implied: f64) -> f64 {
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        use crate::economy::state::{create_initial_economy_state, InitialEconomyOptions};
        let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
        economy.vix = vix;
        economy.inflation_rate = 2.0;
        let inputs = DailyInputs {
            vix_level_identity: p.vix_level_identity,
            vix_implied_from_market: implied,
            vix_index_sigma_pct: 1.0,
            market_day_return_pct: 0.0,
            vix_mean_reversion: p.vix_mean_reversion,
            vix_decay_ratio: 1.0,
            vix_return_gain: p.vix_return_gain,
            vix_return_gain_up: p.vix_return_gain_up,
            vix_return_exponent: p.vix_return_exponent,
            vix_return_exponent_up: p.vix_return_exponent_up,
            vix_return_level_exponent: p.vix_return_level_exponent,
            vix_return_level_exponent_up: p.vix_return_level_exponent_up,
            vix_return_clamp: p.vix_return_clamp,
            vix_target_shock_cap: p.vix_target_shock_cap,
            vix_ceiling: p.vix_ceiling,
            vix_return_source: p.vix_return_source,
            vix_anchor_weight: weight,
            vix_anchor_level: anchor_level,
            game_day: 40,
            ..Default::default()
        };
        update_economy_daily(&economy, &inputs, &mut Silent).vix
    }

    /// **AT 0.0 THE TARGET IS THE READ-BACK**, bit for bit, whatever anchor
    /// is threaded beside it.
    #[test]
    fn the_anchor_weight_at_zero_is_the_step_that_stood_before_it() {
        for &vix in &[10.5, 15.0, 23.25, 40.0, 90.0] {
            for &implied in &[12.0, 21.0, 60.0] {
                let with = vix_step_with_weight(0.0, 23.249857144842903, vix, implied);
                let without = vix_step_with_weight(0.0, 0.0, vix, implied);
                assert_eq!(with.to_bits(), without.to_bits(),
                           "the weight at 0.0 moved the step at VIX {vix}, read-back {implied}");
            }
        }
    }

    /// **THE WEIGHT MOVES THE TARGET, NOT THE RATE.** The step's change is
    /// `mr` times the move in the target, where the target becomes
    /// `implied^(1 - a) * anchor^a`, and at the anchor itself nothing moves.
    #[test]
    fn the_anchor_weight_blends_the_target_geometrically_at_the_shipped_rate() {
        let mr = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves")
            .vix_mean_reversion;
        let anchor = 23.249857144842903;
        let a = 0.609944;
        for &vix in &[12.0, 23.249857144842903, 55.0] {
            for &implied in &[12.0, 23.249857144842903, 60.0] {
                let base = vix_step_with_weight(0.0, anchor, vix, implied);
                let with = vix_step_with_weight(a, anchor, vix, implied);
                let blended = implied.powf(1.0 - a) * anchor.powf(a);
                let want = mr * (blended - implied);
                assert!((with - base - want).abs() < 1e-9,
                        "VIX {vix}, read-back {implied}: moved {} against {want}", with - base);
            }
        }
    }

    #[test]
    fn a_vix_at_the_ceiling_is_held_there_iff_the_target_is_at_or_above_it() {
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        let c = p.vix_ceiling;
        for &sigma in SIGMAS {
            let correction = zero_mean_correction(&p, sigma, c);
            // The largest spike an admissible session can put on a state at
            // the ceiling.
            let reachable = response(&p, p.vix_return_clamp, c);
            for implied in [40.0, 100.0, 134.5, 150.0, 160.0, 175.0] {
                let need = c - implied + correction;
                if need > reachable {
                    // Not reachable at all: even the clamp's own session
                    // leaves the target under the ceiling, so the state
                    // must come off it.
                    let held = vix_after_one_session(c, implied, sigma, -p.vix_return_clamp);
                    assert!(
                        held < c,
                        "a read-back of {implied} at sigma {sigma} needs {need:.4} points of \
                         spike and the clamp can only deliver {reachable:.4}, yet the state \
                         stayed at {held}"
                    );
                    continue;
                }
                // Invert the response WITH the level in it.
                let threshold = mathx::pow(
                    need * mathx::pow(c, p.vix_return_level_exponent) / p.vix_return_gain,
                    1.0 / p.vix_return_exponent,
                );
                assert!(
                    threshold > 0.0 && threshold <= p.vix_return_clamp,
                    "the holding session at a read-back of {implied} is {threshold}, outside \
                     the admissible range"
                );
                let above = vix_after_one_session(c, implied, sigma, -(threshold + 0.01));
                assert_eq!(
                    above, c,
                    "target above the ceiling and the state came off it: {above}"
                );
                let below_r = threshold - 0.01;
                let below = vix_after_one_session(c, implied, sigma, -below_r);
                let want = vix_step_by_hand(&p, c, implied, sigma, -below_r);
                assert!(
                    below < c && (below - want).abs() < 1e-9,
                    "target below the ceiling: the update left {below}, the arithmetic says \
                     {want}"
                );
            }
        }
        // NON-STICKINESS ON THE MAP, which is what the ceiling's `derived`
        // kind rests on. 75.9 is the highest settled `implied(181.3295)` of
        // the three rosters the ceiling's derivation measured, and this
        // drives the largest session the clamp admits from it.
        let worst = vix_after_one_session(c, 75.9, 2.5, -p.vix_return_clamp);
        assert!(
            worst < c - 10.0,
            "from the map's highest settled read-back the worst admissible session left the \
             state at {worst}, within 10 points of the ceiling at {c}. The census that makes \
             the ceiling inert needs re-measuring."
        );
    }

    /// **THE SHIPPED CEILING, PINNED WITH ITS PROVENANCE. THE ORDERING
    /// AGAINST THE CAP IS WITHDRAWN AND IS NOT ASSERTED HERE.**
    ///
    /// 181.3295 is the solution of `C - implied(C) >= 17 * 6.39` on b4fix7's
    /// settled pin ladder of the map this preset runs (crisis blend at 0,
    /// ten rosters, burn 250, pins 14 to 260, residual 8.64 from the
    /// ladder's spread across rosters). `implied(C)` there is the SETTLED
    /// read-back at a pin, the level the map sustains when the VIX is held
    /// at `C`; it is not the read-back the state carries on a variance
    /// excursion, and the condition is sufficient for the settled map only
    /// (an independent re-derivation of the ceiling shows this). What the
    /// record shows at gain 0 is a clip rate of zero over 30,240 seed-days
    /// with a highest VIX of 73.9 and a highest read-back of 93.2.
    ///
    /// THE ORDERING, WITHDRAWN 2026-09-14. The assertion that stood last
    /// here was `vix_target_shock_cap > vix_ceiling`, on a recorded
    /// invariant reading "must stay above the ceiling or the cap binds
    /// first". The cap is 158.8524 and sits 22.4771 points UNDER the
    /// ceiling on the shipped preset, and nothing binds first: the cap
    /// truncates an additive term of the TARGET at `:1167` and the ceiling
    /// truncates the STATE at `:1291`, so no expression compares them and
    /// neither preempts the other at any pair of values. A cap under the
    /// ceiling in fact makes the ceiling LESS sticky, which is the
    /// direction the ceiling's own derivation wants.
    /// The ceiling's derivation establishes that, and `provenance.py`
    /// carries the withdrawal.
    ///
    /// AND THE CONDITION STOPPED CHOOSING A VALUE, which is the honest
    /// state of the derivation. Re-solved on the shipping law the smallest
    /// admissible `C` is 55.98 to 58.43 across three rosters, inside the
    /// measured maximum VIX of 60.59, so the value that restores the
    /// ordering is the value that breaks the census the `derived` kind
    /// rests on. 181.3295 stays because it satisfies the condition and the
    /// census, not because the condition solves to it.
    ///
    /// WHAT THIS CATCHES. A ceiling moved without re-solving the condition
    /// and re-measuring the census. And a preset that puts the ceiling
    /// under the fear term at the top of the graded range, which is
    /// necessary for the condition and nothing like sufficient.
    #[test]
    fn the_default_ceiling_is_pinned_to_its_solve_and_the_cap_ordering_is_withdrawn() {
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        const CEILING_SOLVED: f64 = 181.3295;
        assert_eq!(
            p.vix_ceiling, CEILING_SOLVED,
            "the ceiling is {} where b4fix7's settled ladder solved {CEILING_SOLVED}. Re-solve \
             `C - implied(C) >= gain * GRADED_ABS_R` on a ladder and re-measure the \
             census before moving it.",
            p.vix_ceiling
        );
        // The fear term at the top of the graded range, evaluated AT THE
        // CEILING because that is the level the condition asks about. Under
        // the level-blind law it was the constant 108.63; under the shipped
        // law it is `8.83 * 6.39^1.4483 * C^-0.4483` = 12.5920, and the
        // condition it feeds is met for any settled read-back below 168.74.
        let term = response(&p, GRADED_ABS_R, p.vix_ceiling);
        assert!(
            (term - 12.5920).abs() < 1e-3,
            "the fear term at the ceiling reads {term}, where the dials say 12.5920"
        );
        assert!(
            p.vix_ceiling > term,
            "{} is under the target's fear term {term}",
            p.vix_ceiling
        );
        // NO ORDERING AGAINST THE CAP IS ASSERTED. What is asserted is the
        // property the cap actually has, which does not mention the
        // ceiling.
        assert_eq!(
            binder_of(&p),
            Binder::Clamp,
            "the cap binds before the clamp, which is the ordering that does have a \
             consequence"
        );
    }

    /// The cap is the image of the clamp under the spike, evaluated at the
    /// VIX floor, so it is the spike's supremum over the whole domain the
    /// update admits and it cannot bind anywhere the clamp does not.
    ///
    /// ```text
    /// cap = gain * clamp^exponent * floor^(-level_exponent)
    ///     = 8.83 * 15^1.4483 * 10^-0.4483
    ///     = 158.85236
    /// ```
    ///
    /// The shipped value is 158.8524, the four-decimal rounding of that,
    /// and it is a LITERAL in `pt_v19` because `powf` is not available in a
    /// `const fn`. Rounding UP is what makes the cap strictly inert: at
    /// 158.8524 the session that would truncate is 15.0000025 per cent
    /// against a clamp of 15, so the cap never binds rather than binding on
    /// a measure-zero corner.
    ///
    /// This test previously computed `gain * clamp^exponent` with no level
    /// factor, which reads 445.9577, and asserted `vix_return_exponent ==
    /// 1.0` beside it. Both were the level-blind law's spellings and the
    /// second is false on the shipped preset by 0.4483.
    ///
    /// Called from `the_shipped_default_carries_its_fear_response_across_
    /// the_graded_range` as well as standing on its own: it is the
    /// derivation `ModelParams::vix_target_shock_cap` claims, and a claim
    /// in a doc comment that no test reads is a claim that rots.
    ///
    /// WHAT THIS CATCHES. A cap set to a round number rather than to the
    /// image, which is what pt-v9 through pt-v18 did at 45. A clamp moved
    /// without the cap following it. The level exponent changed without the
    /// cap being re-derived, which would leave an inert cap binding. And
    /// the state floor moving off 10, which is pinned against the update
    /// itself rather than read off the source line.
    #[test]
    fn the_default_cap_is_the_clamps_own_image() {
        let p = ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
            .expect("the default preset resolves");
        // The floor, from `update_economy_daily` and not from a comment: a
        // read-back of zero pulls the target below the floor and the state
        // must stop there.
        let floored = vix_after_one_session(VIX_STATE_FLOOR + 0.5, 0.0, 1.0, 0.0);
        assert_eq!(
            floored, VIX_STATE_FLOOR,
            "the state floor is {floored}, not the {VIX_STATE_FLOOR} the cap's derivation \
             evaluates the clamp's image at"
        );
        let image = p.vix_return_gain
            * mathx::pow(p.vix_return_clamp, p.vix_return_exponent)
            * mathx::pow(VIX_STATE_FLOOR, -p.vix_return_level_exponent);
        assert!(
            (p.vix_target_shock_cap - image).abs() < 1e-3,
            "the cap is {} where the clamp's image at the floor is {image}. A cap above the \
             image is inert and a cap below it is a second binding constraint.",
            p.vix_target_shock_cap
        );
        assert!(
            p.vix_target_shock_cap >= image,
            "the cap {} is UNDER the spike's supremum {image}, so it truncates sessions the \
             clamp admits and is a shape parameter again",
            p.vix_target_shock_cap
        );
        // And it binds nowhere the state can be. The binding session runs
        // 15.0000 at the floor to 36.7817 at the ceiling, against a clamp
        // of 15.
        for &vix in LEVELS {
            let binds = cap_binds_at(&p, vix);
            assert!(
                binds >= p.vix_return_clamp,
                "from a VIX of {vix} the cap truncates at {binds} per cent, inside the clamp \
                 at {}. It is a second binding constraint again.",
                p.vix_return_clamp
            );
        }
        // And the clamp itself is outside the graded range, so neither dial
        // shapes the response where the tape can see it.
        assert!(
            p.vix_return_clamp > GRADED_ABS_R,
            "the clamp at {} is inside the graded {GRADED_ABS_R}, so it is now the \
             shape parameter the cap used to be",
            p.vix_return_clamp
        );
    }
}

#[cfg(test)]
mod anchor_weight_level {
    use super::anchor_weight_at_level;

    #[test]
    fn at_and_below_the_knee_it_is_the_dial() {
        for x in [9.0, 14.0, 18.5] {
            assert!((anchor_weight_at_level(0.45, 1.0, 0.0, 0.0, x, 18.5) - 0.45).abs() < 1e-15);
        }
    }

    #[test]
    fn it_holds_one_minus_a_times_level_constant_below_the_cap() {
        for x in [20.0, 25.0, 30.0, 36.0] {
            let a = anchor_weight_at_level(0.45, 1.0, 1.95, 0.0, x, 18.5);
            assert!(((1.0 - a) * x - 0.55 * 18.5).abs() < 1e-12, "x {x} a {a}");
        }
    }

    #[test]
    fn it_stops_rising_at_the_cap_and_below_the_knee_only_if_asked_it_falls() {
        let at_cap = anchor_weight_at_level(0.45, 1.0, 1.95, 0.0, 1.95 * 18.5, 18.5);
        assert_eq!(anchor_weight_at_level(0.45, 1.0, 1.95, 0.0, 80.0, 18.5), at_cap);
        assert_eq!(anchor_weight_at_level(0.45, 1.0, 1.95, 1.0, 9.0, 18.5), 0.0);
    }
}

#[cfg(test)]
mod live_projection {
    use super::*;
    use crate::economy::state::{create_initial_economy_state, InitialEconomyOptions};

    /// The live mark's projection is the step's own yields with its draws at
    /// their means, BIT FOR BIT, whatever else the step does that day: a
    /// month start (releases), a quarter start, each phase, both signs of
    /// the session, the corporate yield's daily move and the credit floor.
    #[test]
    fn the_projection_is_the_steps_yields_at_the_means() {
        let phases = [CyclePhase::Expansion, CyclePhase::Peak, CyclePhase::Contraction,
                      CyclePhase::Trough, CyclePhase::Recovery];
        let mut checked = 0;
        for (k, phase) in phases.iter().enumerate() {
            for (day, ret) in [(17_i64, -2.3), (30, 1.1), (90, 0.0), (91, -0.4)] {
                for (inflation, floor) in [(2.1, 0.0), (5.0, 1.0), (3.5, 1.0)] {
                    let mut e = create_initial_economy_state(&InitialEconomyOptions::default());
                    e.cycle_phase = *phase;
                    e.inflation_rate = inflation;
                    e.vix = 14.0 + 6.0 * k as f64;
                    let inputs = DailyInputs {
                        game_day: day,
                        market_day_return_pct: ret,
                        vix_return_source: 1.0,
                        daily_credit_floor_gain: floor,
                        yields: YieldDials {
                            treasury_10y_noise: 0.038,
                            treasury_2y_noise: 0.022,
                            flight_to_quality_gain: 0.016,
                            flight_to_quality_day: 1.0,
                            corporate_yield_daily: 1.0,
                            ..YieldDials::default()
                        },
                        ..Default::default()
                    };
                    let full = update_economy_daily(&e, &inputs, &mut MeanDraws);
                    let (y2, y10, corp) = project_close_yields(&e, &inputs);
                    assert_eq!(y2.to_bits(), full.treasury_yield_2y.to_bits());
                    assert_eq!(y10.to_bits(), full.treasury_yield_10y.to_bits());
                    assert_eq!(corp.to_bits(), full.corporate_bond_yield.to_bits());
                    // And the VIX, which the live VIX reads (`vix_intraday_live`).
                    let state = project_close_state(&e, &inputs);
                    assert_eq!(state.vix.to_bits(), full.vix.to_bits());
                    checked += 1;
                }
            }
        }
        assert_eq!(checked, 60);
    }

    /// The session's move reaches the projection through the flight to
    /// quality and the corporate yield's VIX term, which is what the live
    /// mark prices.
    #[test]
    fn the_projection_moves_with_the_session() {
        let e = create_initial_economy_state(&InitialEconomyOptions::default());
        let at = |ret: f64| {
            project_close_yields(&e, &DailyInputs {
                market_day_return_pct: ret,
                vix_return_source: 1.0,
                yields: YieldDials {
                    flight_to_quality_gain: 0.016,
                    flight_to_quality_day: 1.0,
                    corporate_yield_daily: 1.0,
                    ..YieldDials::default()
                },
                ..Default::default()
            })
        };
        let (down, flat, up) = (at(-2.0), at(0.0), at(2.0));
        assert!(down.1 < flat.1 && flat.1 < up.1, "{down:?} {flat:?} {up:?}");
    }
}

/// Unemployment's anchor and oil's interior (issues #170 to #172): each
/// switch at 0.0 is the arithmetic that stood, and off zero it does what
/// its docstring says, read off one release with a draw-free generator.
#[cfg(test)]
mod macro_anchors {
    use super::*;
    use crate::economy::state::{create_initial_economy_state, InitialEconomyOptions};

    struct Silent(f64);
    impl crate::rng::Rng for Silent {
        fn next_f64(&mut self) -> f64 {
            self.0
        }
        fn next_normal(&mut self) -> f64 {
            0.0
        }
    }

    const MONTH: i64 = DAYS_PER_MONTH;

    fn economy() -> EconomyState {
        create_initial_economy_state(&InitialEconomyOptions::default())
    }

    fn step(e: &EconomyState, inputs: DailyInputs) -> EconomyState {
        update_economy_daily(e, &inputs, &mut Silent(0.5))
    }

    fn release() -> DailyInputs<'static> {
        DailyInputs { game_day: MONTH, ..Default::default() }
    }

    /// The pull is `k * (natural - u)` in place of `0.06 * (natural - u)`:
    /// the rise moves by exactly the difference, and 0.06 set by hand is the
    /// shipped release bit for bit.
    #[test]
    fn the_natural_pull_replaces_the_shipped_share() {
        let mut e = economy();
        // Clear of both clamps, so the difference is the pull's alone.
        e.unemployment_rate = 6.0;
        e.structural_unemployment = 4.5;
        let off = step(&e, release());
        let same = step(&e, DailyInputs { unemployment_natural_pull: 0.06, ..release() });
        assert_eq!(same.unemployment_rate.to_bits(), off.unemployment_rate.to_bits());
        let on = step(&e, DailyInputs { unemployment_natural_pull: 0.3, ..release() });
        let want = (0.3 - 0.06) * (4.5 - 6.0);
        assert!((on.unemployment_rate - off.unemployment_rate - want).abs() < 1e-12);
        // Nothing else in the release reads the dial.
        // The Phillips term reads this month's rate, so inflation moves.
        assert_ne!(on.inflation_rate.to_bits(), off.inflation_rate.to_bits());
        assert_eq!(on.gdp_growth.to_bits(), off.gdp_growth.to_bits());
    }

    /// Okun's law read as annual: in an expansion at 3 per cent growth the
    /// shipped drive is -0.2 for Okun and -0.24 for the recovery term every
    /// month; off zero it is `(2 - 3) * beta / 12` and no recovery term.
    #[test]
    fn the_okun_coefficient_is_annual_and_drops_the_recovery_term() {
        let phase = phase_characteristics_for(CyclePhase::Expansion, false);
        let shipped = unemployment_drive(phase.unemployment_trend, CyclePhase::Expansion, 3.0);
        assert_eq!(
            unemployment_drive_with(phase.unemployment_trend, CyclePhase::Expansion, 3.0, 0.0)
                .to_bits(),
            shipped.to_bits());
        assert!((shipped - (-0.05 * 0.3 - 0.2 - 0.24)).abs() < 1e-12);
        let annual =
            unemployment_drive_with(phase.unemployment_trend, CyclePhase::Expansion, 3.0, 0.5);
        assert!((annual - (-0.05 * 0.3 - 0.5 / 12.0)).abs() < 1e-12);

        // And the release adds exactly that drive in place of the shipped
        // one. The economy opens in an expansion; growth after its monthly
        // step is the same in both, and no fiscal term touches it there.
        let e = economy();
        assert_eq!(e.cycle_phase, CyclePhase::Expansion);
        let off = step(&e, release());
        let on = step(&e, DailyInputs { unemployment_okun_coefficient: 0.5, ..release() });
        assert_eq!(on.gdp_growth.to_bits(), off.gdp_growth.to_bits());
        let g = off.gdp_growth;
        let d_off = unemployment_drive(phase.unemployment_trend, CyclePhase::Expansion, g);
        let d_on = unemployment_drive_with(phase.unemployment_trend, CyclePhase::Expansion, g, 0.5);
        let moved = on.unemployment_rate - off.unemployment_rate;
        assert!((moved - (d_on - d_off)).abs() < 1e-12, "{moved} {}", d_on - d_off);
    }

    /// The release resets the NAIRU to the natural rate plus 0.3 times
    /// long-term unemployment, and 4.0 set by hand is the shipped release.
    #[test]
    fn the_natural_rate_sets_the_nairu() {
        let e = economy();
        let off = step(&e, release());
        let same = step(&e, DailyInputs { unemployment_natural_rate: 4.0, ..release() });
        assert_eq!(same.structural_unemployment.to_bits(), off.structural_unemployment.to_bits());
        let on = step(&e, DailyInputs { unemployment_natural_rate: 5.0, ..release() });
        assert!((on.structural_unemployment - off.structural_unemployment - 1.0).abs() < 1e-12);
        assert!((on.structural_unemployment
            - (5.0 + 0.3 * on.long_term_unemployment_rate)).abs() < 1e-12);
    }

    /// Inventory closes `k` of its gap to 50 each day beside the shipped
    /// step, on any day, release or not.
    #[test]
    fn inventory_reverts_toward_its_normal_level() {
        let mut e = economy();
        e.oil_inventory_level = 90.0;
        let day = DailyInputs { game_day: MONTH + 3, ..Default::default() };
        let off = step(&e, day);
        let on = step(&e, DailyInputs { oil_inventory_reversion: 0.01, ..day });
        let want = 0.01 * (OIL_INVENTORY_NORMAL - 90.0);
        assert!((on.oil_inventory_level - off.oil_inventory_level - want).abs() < 1e-12);
        // Below the normal level it pushes the other way.
        e.oil_inventory_level = 10.0;
        let off = step(&e, day);
        let on = step(&e, DailyInputs { oil_inventory_reversion: 0.01, ..day });
        assert!((on.oil_inventory_level - off.oil_inventory_level - 0.4).abs() < 1e-12);
    }

    /// The oil law's reversion and noise at their standing values are the
    /// branch to the bit, and off them the reversion closes its share of the
    /// gap; the inventory level effect moves the price by the change in the
    /// pressure.
    #[test]
    fn the_oil_laws_dials_branch_at_their_standing_values_and_act_as_stated() {
        let mut e = economy();
        e.oil_price = 110.0;
        let day = DailyInputs { game_day: MONTH + 3, ..Default::default() };
        let off = step(&e, day);
        let same = step(&e, DailyInputs { oil_mean_reversion: 0.03, oil_noise_sd: 2.0, ..day });
        assert_eq!(same.oil_price.to_bits(), off.oil_price.to_bits());
        // The same draws, so slowing the reversion moves the price by the
        // reversion's change alone: 0.03 - 0.0035 of the gap to the target,
        // times the seasonal factor the level carries.
        let slow = step(&e, DailyInputs { oil_mean_reversion: 0.0035, ..day });
        assert!(slow.oil_price > off.oil_price);
        // Inventory inside its dead zone: no pressure either way.
        e.oil_inventory_level = 50.0;
        let flat_off = step(&e, day);
        let flat_on = step(&e, DailyInputs { oil_inventory_level_gain: 30.0, ..day });
        assert_eq!(flat_on.oil_price.to_bits(), flat_off.oil_price.to_bits());
        // Below it, the standing step adds the pressure; the level form adds
        // the gain times its change, which is 0 for an inventory that does
        // not move into a new pressure.
        e.oil_inventory_level = 30.0;
        let push = step(&e, day);
        let level = step(&e, DailyInputs { oil_inventory_level_gain: 30.0, ..day });
        let p_new = inventory_pressure(push.oil_inventory_level);
        let moved = 30.0 * (p_new - inventory_pressure(30.0)) - p_new;
        // Up to the seasonal factor on the level, within 3 per cent of 1.
        let ratio = (level.oil_price - push.oil_price) / moved;
        assert!((ratio - 1.0).abs() <= 0.031, "{ratio}");
    }

    /// The convenience premium raises the price when stocks are low and
    /// lowers it when they are high, by `1 - k ln(I / 50)` on its level;
    /// inventory at its normal level moves nothing, and an expectation over
    /// a spread stays near the value at its centre.
    #[test]
    fn the_convenience_premium_moves_the_target_with_inventory() {
        let mut e = economy();
        e.oil_price = 80.0;
        let day = DailyInputs { game_day: MONTH + 3, oil_inventory_reversion: 0.002, ..Default::default() };
        // At 50 after the step's own move the factor is near 1 and the price
        // near the premium-free one.
        e.oil_inventory_level = 50.0;
        let off = step(&e, day);
        let on = step(&e, DailyInputs { oil_convenience_yield: 2.0, ..day });
        let f = oil_convenience_factor(2.0, off.oil_inventory_level, 0.0);
        assert!((f - 1.0).abs() < 0.05, "{f}");
        // Low stocks: a premium, so the price rises against the premium-free
        // step; high stocks: a discount. Two standard deviations either side
        // at EIA's inventory scale (oil_inventory_noise_sd 0.35).
        e.oil_inventory_level = 42.0;
        let low_off = step(&e, day);
        let low_on = step(&e, DailyInputs { oil_convenience_yield: 2.0, ..day });
        e.oil_inventory_level = 58.0;
        let high_off = step(&e, day);
        let high_on = step(&e, DailyInputs { oil_convenience_yield: 2.0, ..day });
        // The same draws and the same inventory move at both levels, so the
        // premium's effect on the step is larger with stocks low than high:
        // the reversion pulls toward a higher target, `T (1 - k ln(I / 50))`.
        assert!(low_on.oil_price - low_off.oil_price > high_on.oil_price - high_off.oil_price,
                "{} {} {} {}", low_on.oil_price, low_off.oil_price, high_on.oil_price, high_off.oil_price);
        // The same draws either way: the premium moves no draw count.
        assert_eq!(on.oil_inventory_level.to_bits(), off.oil_inventory_level.to_bits());
        // The factor itself, its inventory floor at 25 and its own floor.
        assert!((oil_convenience_factor(2.0, 25.0, 0.0) - (1.0 + 2.0 * 2f64.ln())).abs() < 1e-12);
        assert_eq!(oil_convenience_factor(2.0, 5.0, 0.0), oil_convenience_factor(2.0, 25.0, 0.0));
        assert!((oil_convenience_factor(1.0, 100.0, 0.0) - (1.0 - 2f64.ln())).abs() < 1e-12);
        assert_eq!(oil_convenience_factor(4.0, 100.0, 0.0), OIL_CONVENIENCE_MIN);
        // The expectation over a spread, against a fine sum over the normal.
        // At EIA's inventory scale (oil_inventory_noise_sd 0.35: sd about 4).
        let (k, m, sd) = (3.6, 45.0, 4.0);
        let mut fine = 0.0;
        let mut mass = 0.0;
        for i in -4000..=4000 {
            let z = i as f64 / 500.0;
            let w = mathx::exp(-0.5 * z * z);
            fine += w * oil_convenience_factor(k, m + z * sd, 0.0);
            mass += w;
        }
        let quad = oil_convenience_factor(k, m, sd * sd);
        assert!((quad / (fine / mass) - 1.0).abs() < 1e-4, "{quad} {}", fine / mass);
        // Linear in log inventory, so the spread moves it only through the
        // log's own small curvature, not by a convex premium's tilt.
        assert!((quad / oil_convenience_factor(k, m, 0.0) - 1.0).abs() < 0.02);
    }

    /// The clamped expectation the forecast publishes under
    /// `oil_forecast_clamp`: a fine sum of `clamp(x, 35, 150)` over the
    /// normal agrees with the closed form, and far from the clamps it is the
    /// mean.
    #[test]
    fn the_clamped_oil_expectation_is_the_truncated_mean() {
        for &(mean, sd) in &[(90.0, 5.0), (140.0, 20.0), (45.0, 15.0), (150.0, 30.0)] {
            let (mut total, mut mass) = (0.0, 0.0);
            for i in -8000..=8000 {
                let z = i as f64 / 1000.0;
                let w = mathx::exp(-0.5 * z * z);
                total += w * clamp(mean + sd * z, 35.0, 150.0);
                mass += w;
            }
            let want = total / mass;
            let got = oil_clamped_expectation(mean, sd, (35.0, 150.0));
            assert!((got - want).abs() < 1e-6, "{mean} {sd}: {got} {want}");
        }
        assert!((oil_clamped_expectation(90.0, 1.0, (35.0, 150.0)) - 90.0).abs() < 1e-12);
        assert_eq!(oil_clamped_expectation(160.0, 0.0, (35.0, 150.0)), 150.0);
        assert_eq!(oil_bounds(0.0, 0.0), (35.0, 150.0));
        // The expected log truncation: nothing far from the bounds, negative
        // near the ceiling, positive near the floor.
        assert!(oil_expected_log_truncation(90.0, 5.0, (35.0, 150.0)).abs() < 1e-12);
        assert!(oil_expected_log_truncation(145.0, 15.0, (35.0, 150.0)) < 0.0);
        assert!(oil_expected_log_truncation(38.0, 8.0, (35.0, 150.0)) > 0.0);
        // The lognormal forms against fine sums in log space.
        for &(mean, v) in &[(140.0, 0.04), (45.0, 0.09), (90.0, 0.003)] {
            let (m, sd) = (f64::ln(mean) - 0.5 * v, f64::sqrt(v));
            let (mut cens, mut trunc, mut mass) = (0.0, 0.0, 0.0);
            for i in -8000..=8000 {
                let z = i as f64 / 1000.0;
                let w = mathx::exp(-0.5 * z * z);
                let x = mathx::exp(m + sd * z);
                cens += w * clamp(x, 35.0, 150.0);
                trunc += w * mathx::log(clamp(x, 35.0, 150.0) / x);
                mass += w;
            }
            assert!((oil_lognormal_censored(mean, v, (35.0, 150.0)) - cens / mass).abs() < 1e-5);
            assert!((oil_lognormal_log_truncation(mean, v, (35.0, 150.0)) - trunc / mass).abs() < 1e-6);
        }
        assert_eq!(oil_bounds(16.4, 279.0), (16.4, 279.0));
    }

    /// Under `oil_dollar_elasticity` a stronger dollar lowers the oil price
    /// by `(usd' / usd)^-e` on the step that moves it: two economies that
    /// differ only in the policy rate, which the oil step does not read and
    /// the dollar's target does, take the same draws and the same oil step,
    /// and their oil prices differ by the ratio of their dollars' carriers.
    #[test]
    fn a_stronger_dollar_lowers_oil_under_the_elasticity() {
        let mut a = economy();
        a.oil_price = 80.0;
        a.usd_index = 100.0;
        let mut b = a.clone();
        b.federal_funds_rate = a.federal_funds_rate + 4.0;
        let day = DailyInputs { game_day: MONTH + 3, oil_dollar_elasticity: 1.2, ..Default::default() };
        let (sa, sb) = (step(&a, day), step(&b, day));
        assert!(sb.usd_index > sa.usd_index, "{} {}", sb.usd_index, sa.usd_index);
        assert!(sb.oil_price < sa.oil_price, "{} {}", sb.oil_price, sa.oil_price);
        let want = mathx::pow(sb.usd_index / sa.usd_index, -1.2);
        assert!((sb.oil_price / sa.oil_price - want).abs() < 1e-12, "{} {want}", sb.oil_price / sa.oil_price);
        // At 0.0 the same pair moves oil by the drag alone, which reads the
        // dollar at the session's start: equal here, so the prices match.
        let off = DailyInputs { game_day: MONTH + 3, ..Default::default() };
        assert_eq!(step(&a, off).oil_price.to_bits(), step(&b, off).oil_price.to_bits());
    }

    /// The shipped pass-through pays a rise from 75 and not the matching
    /// fall; off zero a rise and a fall about the anchor pay equal and
    /// opposite amounts.
    #[test]
    fn the_oil_pass_through_is_symmetric_about_its_anchor() {
        let at = |oil: f64, c: f64| {
            let mut e = economy();
            e.oil_price = oil;
            step(&e, DailyInputs { oil_inflation_passthrough: c, ..release() }).inflation_rate
        };
        // Shipped: +10 from 75 crosses 80 and pays; -10 pays nothing.
        assert!(at(85.0, 0.0) - at(75.0, 0.0) > 0.04);
        assert_eq!(at(65.0, 0.0).to_bits(), at(75.0, 0.0).to_bits());
        // Symmetric about 81.
        assert_eq!(OIL_PASSTHROUGH_ANCHOR, 81.0);
        let up = at(96.0, 1.0) - at(81.0, 1.0);
        let down = at(66.0, 1.0) - at(81.0, 1.0);
        assert!((up - 0.15).abs() < 1e-9, "{up}");
        assert!((up + down).abs() < 1e-9, "{up} {down}");
    }
}
