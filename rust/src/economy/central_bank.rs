//! The central bank, ported from the reference implementation's economy
//! module.
//!
//! # Draw schedule
//!
//! A meeting that does not happen costs **zero** draws — the early return
//! precedes everything. A meeting that does happen costs:
//!
//! | Site | Draws |
//! |---|---|
//! | The rate decision | **1 uniform**, but only on the two deep-recession cut branches |
//! | The announcement variant | **1 uniform, always** — all seven cases pick from six strings, including `hold`/`default` |
//! | Scheduling the next meeting | **1 uniform, always** |
//!
//! so 2 or 3 uniforms per meeting, never any other count.
//!
//! The announcement TEXT is out of scope — it is narrative, and this crate
//! does not build strings. **The draw that selects it is not out of scope.**
//! Skipping it would shift the stream for every later consumer, so the draw
//! is taken and the chosen index returned, which is also what lets the
//! parity harness prove the draw happened at the right point with the right
//! value.

/// Neutral Fed portfolio for the QE stock channel, in $B. `qe_assets_ratio`
/// is holdings over this. See `qe_pe_stock_gain`.
const QE_ASSETS_BASELINE_B: f64 = 5000.0;


use super::state::*;
use crate::mathx::{self, clamp_via_min_max as clamp};
use crate::rng::Rng;

/// Every announcement case picks from exactly six strings.
const ANNOUNCEMENT_VARIANTS: f64 = 6.0;

/// The policy action taken at a meeting.
/// The corporate spread never sits under this, in basis points over the 10y.
///
/// An investment-grade yield below the risk-free curve is not a rare edge, it
/// is an impossible quote. Stated here rather than as a literal because the
/// floor is applied in two places -- once where the meeting computes the
/// yield, and once at the end of the daily update, where the benchmark has
/// moved underneath it since.
pub const CORPORATE_SPREAD_FLOOR: f64 = 0.8;

/// The cycle phase's multiplier on the corporate spread, the true phase's:
/// [`spread_multiplier_of`], the one table the meeting formula and the daily
/// step read, under the name `Engine::price_pinned_vix` reads it by.
pub fn cycle_spread_multiplier(phase: CyclePhase) -> f64 {
    spread_multiplier_of(phase)
}

/// The mortgage spread's floor, on the same footing.
///
/// Structurally identical to the corporate one, and it survived on margin
/// alone: this spread runs 1.5 to 2.8, so daily drift never reached 0.5. That
/// is luck rather than a guarantee, so it is floored too.
pub const MORTGAGE_SPREAD_FLOOR: f64 = 0.5;

/// The inflation rate at or above which the Fed put stands aside
/// (`fed_put_gain`), and at or above which the Treasury haven does
/// (`treasury_haven_gain`). Real target changes at a VIX close of 30 or
/// more, 1990-2025, were all cuts (14 of 14, FRED DFEDTAR and DFEDTARU),
/// but only 9 of the 14 came with published headline CPI inflation under 4:
/// five came at 4.1 to 6.2 (1990-10-29, 1991-01-09, 2008-03-18, 2008-10-08
/// and 2008-10-29), so this ceiling would have switched the put off in
/// 1990-91 and October 2008. What the data separate is about 6.2 (1990,
/// still cutting) from 7.6 and more (2022, hiking through a 25 per cent
/// fall). 4 is chosen instead, for the stock-bond correlation, which turns
/// positive above about 3 to 4 (Campbell, Sunderam and Viceira 2017), so
/// the put and the haven share one gate. It costs little on pt-v20, whose
/// CPI is at or above 4 on about 1.7 per cent of sessions of its held-out
/// histories (0.3 per cent at a VIX of 30 or more), against 14 per cent of
/// sessions on the tape (20 per cent at a VIX of 30 or more).
pub const FED_PUT_INFLATION_CEILING: f64 = 4.0;

/// The VIX at or above which the Fed put holds any hike and gives nothing
/// back (`fed_put_gain`): no FOMC target change 1990-2025 was a hike at a
/// VIX close of 30 or more.
pub const FED_PUT_HOLD_VIX: f64 = 30.0;

/// The cut the Fed put asks for, percentage points, before rounding:
/// `gain * max(0, -intermeeting_return - threshold)`. Read by the meeting
/// and, for the priced put, by the daily anchor.
pub fn fed_put_ask(gain: f64, threshold: f64, intermeeting_return: f64) -> f64 {
    gain * mathx::max(0.0, -intermeeting_return - threshold)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum Decision {
    AggressiveHike,
    Hike,
    Cut,
    EmergencyCut,
    StagflationHike,
    LaborEmergencyCut,
    Hold,
}

impl Decision {
    /// The decision's name, following `CyclePhase::as_str`.
    ///
    /// Anything aggregating decisions across a run otherwise has to match on
    /// the enum to get a label, and a `match` in a caller goes stale silently
    /// when a variant is added here.
    pub fn as_str(self) -> &'static str {
        match self {
            Decision::AggressiveHike => "aggressive_hike",
            Decision::Hike => "hike",
            Decision::Cut => "cut",
            Decision::EmergencyCut => "emergency_cut",
            Decision::StagflationHike => "stagflation_hike",
            Decision::LaborEmergencyCut => "labor_emergency_cut",
            Decision::Hold => "hold",
        }
    }
}

/// What a meeting produced.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct MeetingOutcome {
    pub central_bank: CentralBankState,
    pub economy: EconomyState,
    /// `None` when no meeting took place, so the caller can distinguish
    /// "held rates" from "did not meet" — the original signals that by
    /// omitting `announcement`.
    pub decision: Option<Decision>,
    /// Index into the six announcement variants. Carried instead of the
    /// string: the draw is contractual, the prose is not.
    pub announcement_variant: Option<usize>,
}

/// What the engine's dials change about a meeting. [`PolicyOptions::shipped`]
/// is the reference bank exactly.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct PolicyOptions {
    /// The calendar meeting intervals are counted on; see `MacroCalendar`.
    pub calendar: MacroCalendar,
    /// `fed_liftoff_rule`: 0.0 is the shipped ladder. See
    /// [`crate::params::ModelParams::fed_liftoff_rule`].
    pub liftoff: f64,
    /// The corporate spread's cycle multiplier the meeting re-anchors with;
    /// `None` is the true phase's, the table that stood. `Some` only while
    /// `cycle_nowcast_accuracy` or `corporate_spread_cycle` is set, and then
    /// it is the multiplier the market prices. See
    /// [`crate::params::ModelParams::cycle_nowcast_accuracy`].
    pub spread_multiplier: Option<f64>,
    /// `fed_growth_cut`: the growth rate, in per cent a year, below which
    /// the bank takes a risk-management cut; 0.0 is off. See
    /// [`crate::params::ModelParams::fed_growth_cut`].
    pub growth_cut: f64,
    /// `fed_stress_cut`, points per step: 0.0 is the shipped ladder, and
    /// nothing below reads the three fields after it. See
    /// [`crate::params::ModelParams::fed_stress_cut`].
    pub stress_cut: f64,
    /// `fed_stress_vix`: the stress level the cut starts at.
    pub stress_vix: f64,
    /// `fed_stress_inflation_gap`: inflation must be under target plus this.
    pub stress_inflation_gap: f64,
    /// The stress the meeting reads: the highest published VIX since the
    /// last meeting, which the engine keeps only with the cut on.
    pub stress_level: f64,
    /// A caller pinned the policy rate for this session and it holds
    /// through the close (`macro_pins_hold`): the meeting still reads the
    /// economy and takes every draw its ladder takes, but the decision is a
    /// hold, the rate stays where the pin put it, and the curve is
    /// re-anchored to that rate. False on every preset.
    pub hold_rate: bool,
    /// `fed_put_gain`: 0.0 is no put. See
    /// [`crate::params::ModelParams::fed_put_gain`].
    pub put_gain: f64,
    /// `fed_put_threshold`, read only with `put_gain` non-zero.
    pub put_threshold: f64,
    /// `treasury_put_pricing`, read only with `put_gain` non-zero.
    pub put_pricing: f64,
    /// `treasury_haven_gain`: 0.0 is no haven in the meeting's 10-year
    /// target.
    pub haven_gain: f64,
    /// `fed_put_carry`: the share of the put's unanswered intermeeting fall
    /// the clock restarts from. 0.0 restarts it at zero, as it stood. See
    /// [`crate::params::ModelParams::fed_put_carry`].
    pub put_carry: f64,
    /// `fed_stress_hold`: the meeting falls within the dial's sessions of a
    /// stressed close, so a rise is held while inflation is under target
    /// plus `stress_inflation_gap`. False with the dial off. See
    /// [`crate::params::ModelParams::fed_stress_hold`].
    pub stress_hold: bool,
    /// `treasury_path_pricing`: 0.0 is off, and nothing below reads the
    /// field after it. See
    /// [`crate::params::ModelParams::treasury_path_pricing`].
    pub path_gain: f64,
    /// The expected path the curve priced before the meeting, `gain * M`,
    /// percentage points. Read only with `path_gain` non-zero.
    pub path_before: f64,
    /// `treasury_policy_damping`: 0.0 is the 10-year reading the policy
    /// rate one for one. See
    /// [`crate::params::ModelParams::treasury_policy_damping`].
    pub rate_damping: f64,
    /// `corporate_spread_vix_cut`: the share of the VIX slope taken out of
    /// the meeting's corporate spread formula. 0.0 is the slope that stood.
    pub spread_vix_cut: f64,
    /// `corporate_spread_equity_gain`: percentage points added to the
    /// formula's base per unit of `EconomyState::spread_equity_gap`. 0.0 is
    /// none.
    pub spread_equity_gain: f64,
    /// `fed_core_inflation`: the inflation the meeting reads in place of
    /// headline, core (headline less `EconomyState::oil_inflation_level`).
    /// `None`, on every preset, is headline.
    pub policy_inflation: Option<f64>,
    /// `treasury_core_inflation`: the inflation the meeting's 10-year target
    /// reads (its term premium and the haven's gate). `None`, on every
    /// preset, is the inflation the meeting reads.
    pub curve_inflation: Option<f64>,
}

/// [`PolicyOptions::shipped`].
impl Default for PolicyOptions {
    fn default() -> Self {
        PolicyOptions::shipped()
    }
}

impl PolicyOptions {
    pub const fn shipped() -> Self {
        PolicyOptions {
            calendar: MacroCalendar::shipped(),
            liftoff: 0.0,
            spread_multiplier: None,
            growth_cut: 0.0,
            stress_cut: 0.0,
            stress_vix: 30.0,
            stress_inflation_gap: 1.0,
            stress_level: 0.0,
            hold_rate: false,
            put_gain: 0.0,
            put_threshold: 0.0,
            put_pricing: 0.0,
            haven_gain: 0.0,
            put_carry: 0.0,
            stress_hold: false,
            path_gain: 0.0,
            path_before: 0.0,
            rate_damping: 0.0,
            spread_vix_cut: 0.0,
            spread_equity_gain: 0.0,
            policy_inflation: None,
            curve_inflation: None,
        }
    }
}

/// The spread's cycle multiplier of one phase, the table the meeting and the
/// daily VIX term read.
pub fn spread_multiplier_of(phase: CyclePhase) -> f64 {
    match phase {
        CyclePhase::Contraction => 2.8,
        CyclePhase::Trough => 3.5,
        CyclePhase::Recovery => 1.4,
        CyclePhase::Peak => 1.1,
        CyclePhase::Expansion => 1.0,
    }
}

/// The meeting formula's spread at a VIX and a multiplier, clamped as the
/// meeting clamps it.
pub fn spread_formula(vix: f64, multiplier: f64) -> f64 {
    clamp((1.0 + (vix - 12.0) * 0.02) * multiplier, CORPORATE_SPREAD_FLOOR, 6.0)
}

/// [`spread_formula`] with the VIX slope cut by `vix_cut`
/// (`corporate_spread_vix_cut`) and `equity` percentage points added to the
/// base before the multiplier (`corporate_spread_equity_gain` times the
/// index's gap, `EconomyState::spread_equity_gap`). Read only with one of
/// the two dials set; [`spread_formula`] is what stood.
pub fn spread_formula_with(vix: f64, multiplier: f64, vix_cut: f64, equity: f64) -> f64 {
    clamp(
        (1.0 + (vix - 12.0) * 0.02 * (1.0 - vix_cut) + equity) * multiplier,
        CORPORATE_SPREAD_FLOOR,
        6.0,
    )
}

/// Run a scheduled (or emergency) FOMC-style meeting.
pub fn update_central_bank(
    central_bank: &CentralBankState,
    economy: &EconomyState,
    current_timestamp: i64,
    rng: &mut impl Rng,
) -> MeetingOutcome {
    update_central_bank_with(central_bank, economy, current_timestamp, rng, &PolicyOptions::shipped())
}

/// [`update_central_bank`] under the engine's dials.
pub fn update_central_bank_with(
    central_bank: &CentralBankState,
    economy: &EconomyState,
    current_timestamp: i64,
    rng: &mut impl Rng,
    options: &PolicyOptions,
) -> MeetingOutcome {
    // THE BANK'S INFLATION (`fed_core_inflation`): the meeting runs on the
    // economy with core in headline's place, every read below included, and
    // headline is put back on what it returns (the meeting writes no
    // inflation). The curve's 10-year target reads the curve's own
    // inflation, headline unless `treasury_core_inflation` gave core. A
    // branch: with `None` the meeting is the one that stood.
    if let Some(pi) = options.policy_inflation {
        let mut view = economy.clone();
        view.inflation_rate = pi;
        let inner = PolicyOptions {
            policy_inflation: None,
            curve_inflation: Some(options.curve_inflation.unwrap_or(economy.inflation_rate)),
            ..*options
        };
        let mut outcome = update_central_bank_with(central_bank, &view, current_timestamp, rng, &inner);
        outcome.economy.inflation_rate = economy.inflation_rate;
        return outcome;
    }
    let curve_inflation = options.curve_inflation.unwrap_or(economy.inflation_rate);
    // An inflation rate running 4pp above the policy rate forces a meeting
    // regardless of the calendar.
    let inflation_rate_gap = economy.inflation_rate - economy.federal_funds_rate;
    let is_emergency_meeting = inflation_rate_gap > 4.0 && economy.inflation_rate > 4.0;

    if !is_emergency_meeting && current_timestamp < central_bank.next_meeting_date {
        return MeetingOutcome {
            central_bank: *central_bank,
            economy: economy.clone(),
            decision: None,
            announcement_variant: None,
        };
    }

    let mut new_cb = *central_bank;
    let mut new_economy = economy.clone();

    // ── Taylor rule ───────────────────────────────────────────────────────
    // D1, decided: the JS formula. The deployed WASM version is blind to
    // unemployment and to `hawkish_dovish_score`, which severs the policy
    // rate from the Okun and Phillips chain the engine computes every day and
    // leaves `hawkish_dovish_score` as state that is maintained and never
    // read. That is a truncation of a Taylor rule, not a variant of one.
    let neutral_rate = 2.0 + 0.5 * (economy.inflation_rate / central_bank.target_inflation - 1.0);
    let inflation_gap_taylor = economy.inflation_rate - central_bank.target_inflation;
    let output_gap_taylor = central_bank.target_unemployment - economy.unemployment_rate;
    let taylor_rate = neutral_rate
        + 0.5 * inflation_gap_taylor
        + 0.5 * output_gap_taylor
        + 0.1 * central_bank.hawkish_dovish_score;
    // THE FED PUT'S OVERLAY (`fed_put_gain`): the ladder reads its rule less
    // what the put still owes, so the lift-off branch does not undo a put
    // cut at the next calm meeting; the calm meetings give it back instead,
    // below. Guarded, so with the dial at 0.0 the rule is the one that stood.
    let put_on = options.put_gain != 0.0;
    let taylor_rate = if put_on { taylor_rate - economy.fed_put_owed } else { taylor_rate };

    let current_rate = economy.federal_funds_rate;
    let rate_diff = taylor_rate - current_rate;

    // Urgency: with inflation far above target the Fed historically hikes
    // 50-75bp per meeting until the rate exceeds inflation.
    let inflation_gap = (economy.inflation_rate - central_bank.target_inflation).abs();
    let urgency = if inflation_gap > 4.0 {
        mathx::max(2.0, inflation_gap / 1.5)
    } else {
        mathx::max(1.0, inflation_gap / 2.0)
    };

    let mut rate_change = 0.0;
    let mut decision = Decision::Hold;

    // The ladder is ordered, and the order is the policy. Recession cuts are
    // tested FIRST so that a collapsing economy overrides inflation
    // targeting — the dual mandate putting employment first.
    if economy.gdp_growth < -2.0 && economy.unemployment_rate > 10.0 {
        // DRAW SITE. Deep recession: -100 to -150bps.
        rate_change = -1.0 - rng.next_f64() * 0.5;
        decision = Decision::EmergencyCut;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.5, -1.0, 1.0);
    } else if economy.gdp_growth < 0.0 && economy.unemployment_rate > 8.0 {
        // DRAW SITE. Recession: -50 to -100bps.
        rate_change = -0.5 - rng.next_f64() * 0.5;
        decision = Decision::EmergencyCut;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.4, -1.0, 1.0);
    } else if economy.unemployment_rate > 8.0 && economy.inflation_rate < 3.0 {
        rate_change = -0.75;
        decision = Decision::LaborEmergencyCut;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.4, -1.0, 1.0);
    } else if (economy.cycle_phase == CyclePhase::Contraction
        || economy.cycle_phase == CyclePhase::Trough)
        && economy.gdp_growth < 0.0
        && economy.unemployment_rate > 7.0
    {
        // Contraction with a weakening labour market: cut if inflation is
        // moderating, otherwise wait.
        if economy.inflation_rate < economy.federal_funds_rate {
            rate_change = -0.25;
            decision = Decision::Cut;
            new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.2, -1.0, 1.0);
        } else {
            rate_change = 0.0;
            decision = Decision::Hold;
        }
    } else if (economy.cycle_phase == CyclePhase::Contraction
        || economy.cycle_phase == CyclePhase::Trough)
        && current_rate > 5.0
    {
        // Stops the Fed pinning rates at the ceiling through a whole
        // contraction, which causes mass bankruptcies.
        rate_change = -0.25;
        decision = Decision::Cut;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.15, -1.0, 1.0);
    } else if rate_diff > 2.0
        && economy.inflation_rate > central_bank.target_inflation + 4.0
        && current_rate < economy.inflation_rate
    {
        // The Volcker response.
        rate_change = if inflation_rate_gap > 4.0 { 1.0 } else { 0.75 };
        decision = Decision::AggressiveHike;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score + 0.4, -1.0, 1.0);
    } else if inflation_rate_gap > 2.0 && economy.inflation_rate > 4.0 {
        rate_change = 0.75;
        decision = Decision::AggressiveHike;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score + 0.35, -1.0, 1.0);
    } else if rate_diff > 1.0 && economy.inflation_rate > central_bank.target_inflation + 2.0 {
        rate_change = 0.5;
        decision = Decision::AggressiveHike;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score + 0.3, -1.0, 1.0);
    } else if rate_diff > 0.5 && economy.inflation_rate > central_bank.target_inflation + 1.0 {
        rate_change = 0.25;
        decision = Decision::Hike;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score + 0.2, -1.0, 1.0);
    } else if rate_diff < -0.5
        && economy.unemployment_rate > central_bank.target_unemployment + 1.0
        && economy.inflation_rate < central_bank.target_inflation + 2.0
    {
        rate_change = -0.25;
        decision = Decision::Cut;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.2, -1.0, 1.0);
    } else if rate_diff < -1.0
        && (economy.cycle_phase == CyclePhase::Contraction
            || economy.cycle_phase == CyclePhase::Trough)
        && economy.inflation_rate < central_bank.target_inflation + 1.5
    {
        rate_change = -0.5;
        decision = Decision::EmergencyCut;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.3, -1.0, 1.0);
    } else if economy.inflation_rate > central_bank.target_inflation + 1.5
        && current_rate < economy.inflation_rate
        && economy.unemployment_rate < 8.0
    {
        // Stagflation guard: track inflation only while unemployment is out
        // of crisis territory. Above 8% the dual mandate prioritises jobs.
        let rate_deficit = economy.inflation_rate - current_rate;
        rate_change = if rate_deficit > 3.0 { 0.50 } else { 0.25 };
        decision = Decision::StagflationHike;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score + 0.15, -1.0, 1.0);
    } else if options.growth_cut != 0.0
        && economy.gdp_growth < options.growth_cut
        && economy.inflation_rate < central_bank.target_inflation + 1.5
        && current_rate > 0.0
    {
        // RISK MANAGEMENT (`fed_growth_cut`). The cut branches above read
        // unemployment, which turns over months, so without this the first
        // cut of an easing comes at the trough. This cuts a quarter point
        // when growth has slowed under the dial and inflation allows it,
        // whatever unemployment has done yet, as the Fed's first cuts came
        // at or before the NBER peak. After the stagflation guard, so high
        // inflation still wins; before lift-off, so the two never meet.
        rate_change = -0.25;
        decision = Decision::Cut;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.2, -1.0, 1.0);
    } else if options.liftoff != 0.0
        && rate_diff > 0.5
        && economy.unemployment_rate <= central_bank.target_unemployment + 1.0
        && economy.cycle_phase != CyclePhase::Contraction
        && economy.cycle_phase != CyclePhase::Trough
    {
        // LIFT-OFF (`fed_liftoff_rule`). Every hike branch above needs
        // inflation at least a point over target, so once a recession has
        // cut the rate to zero and inflation settles near target, nothing
        // can raise it again: the Taylor rate sits two or three points
        // above the policy rate for the rest of the run. This is the cut
        // branch below mirrored -- that one cuts a quarter point when the
        // rule is 50bp under the rate and the labour market is slack (more
        // than a point over target); this one hikes a quarter point when the
        // rule is 50bp over the rate and it is not slack, whatever inflation
        // is, outside a recession. Real lift-offs (1994, 2004, 2015) came
        // with inflation at or under target plus one.
        // The step and the score move are the mirrored cut's, signs flipped.
        rate_change = 0.25;
        decision = Decision::Hike;
        new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score + 0.2, -1.0, 1.0);
    }

    // THE STRESS CUT (`fed_stress_cut`). The ladder has no market-stress
    // term, so on pt-v20 a meeting after a VIX of 30 to 40 cut within 42
    // sessions 0.29 of the time and hiked 0.25, against 0.53 and 0.01 on the
    // target rate over 1990-2025. A branch: at 0.0 nothing here runs. At a
    // stress level (the highest published VIX since the last meeting) at or
    // over `stress_vix`, with inflation under target plus the gap and the
    // rate over zero, the bank cuts one step, one more per further ten VIX
    // points, at most four, never below zero; the cut replaces a smaller cut
    // or any hike the ladder chose, and a deeper cut the ladder chose
    // stands. No draw.
    if options.stress_cut != 0.0
        && options.stress_level >= options.stress_vix
        && economy.inflation_rate < central_bank.target_inflation + options.stress_inflation_gap
        && current_rate > 0.0
    {
        let steps = mathx::min(
            4.0, 1.0 + ((options.stress_level - options.stress_vix) / 10.0).floor());
        let cut = mathx::min(current_rate, options.stress_cut * steps);
        if rate_change > -cut {
            rate_change = -cut;
            decision = if cut > 0.25 { Decision::EmergencyCut } else { Decision::Cut };
            new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.2, -1.0, 1.0);
        }
    }

    // THE STRESS HOLD (`fed_stress_hold`). Within the dial's sessions of a
    // close whose published VIX was at or over `fed_stress_vix`, with
    // inflation under target plus the stress gap, a rise the ladder chose
    // is held and the dovish score stays where it was; below, the put gives
    // nothing back. A cut stands. A branch: false with the dial off.
    if options.stress_hold
        && rate_change > 0.0
        && economy.inflation_rate < central_bank.target_inflation + options.stress_inflation_gap
    {
        rate_change = 0.0;
        decision = Decision::Hold;
        new_cb.hawkish_dovish_score = central_bank.hawkish_dovish_score;
    }

    // A PINNED RATE HOLDS (`PolicyOptions::hold_rate`). After the ladder,
    // so its draws are taken as they would be, and after the stress cut
    // (`fed_stress_cut`), so a pinned rate holds whatever either chose.
    if options.hold_rate {
        rate_change = 0.0;
        decision = Decision::Hold;
        new_cb.hawkish_dovish_score = central_bank.hawkish_dovish_score;
    }

    // Urgency amplifies HIKES only. Amplifying cuts by an inflation gap would
    // be backwards: during a recession the Fed cuts on employment.
    if rate_change > 0.0 {
        rate_change *= urgency;
    }

    // THE FED PUT (`fed_put_gain`), after the ladder and its urgency. The
    // index's log fall since the last meeting asks for a cut, in quarter
    // points and no more than the rate, while inflation is under
    // `FED_PUT_INFLATION_CEILING`; it replaces the ladder's decision when
    // the ladder would cut less, hold or hike, and a VIX at or above
    // `FED_PUT_HOLD_VIX` holds any hike. What the overlay takes off the
    // ladder's path is owed (`fed_put_owed`) and given back a quarter point
    // at a calm meeting once the put's decaying stock (`fed_put`) sits an
    // eighth under it. No draw, so the meeting's draw count is the one the
    // module's table states.
    let ladder_change = rate_change;
    let mut asked = 0.0;
    let mut put_cut = 0.0;
    let mut unwind = 0.0;
    // A policy rate a caller pinned holds (`macro_pins_hold`, above): the put
    // neither cuts nor unwinds at that meeting, so the pin is the rate.
    if put_on && !options.hold_rate && economy.inflation_rate < FED_PUT_INFLATION_CEILING {
        asked = fed_put_ask(options.put_gain, options.put_threshold, economy.intermeeting_return);
        put_cut = mathx::min((asked / 0.25).round() * 0.25, mathx::max(0.0, current_rate));
        if put_cut > 0.0 && rate_change > -put_cut {
            rate_change = -put_cut;
            decision = Decision::Cut;
            new_cb.hawkish_dovish_score = clamp(central_bank.hawkish_dovish_score - 0.2, -1.0, 1.0);
        }
        if economy.vix >= FED_PUT_HOLD_VIX && rate_change > 0.0 {
            rate_change = 0.0;
            decision = Decision::Hold;
            new_cb.hawkish_dovish_score = central_bank.hawkish_dovish_score;
        }
    }
    if put_on
        && !options.hold_rate
        && put_cut == 0.0
        && rate_change >= 0.0
        && ladder_change >= 0.0
        && economy.vix < FED_PUT_HOLD_VIX
        && economy.fed_put_owed - economy.fed_put > 0.125
        && !(options.stress_hold
            && economy.inflation_rate
                < central_bank.target_inflation + options.stress_inflation_gap)
    {
        unwind = mathx::min(0.25, economy.fed_put_owed);
        rate_change += unwind;
        if decision == Decision::Hold {
            decision = Decision::Hike;
        }
    }

    // DRAW SITE — always. The announcement text is not built here, but the
    // draw that chooses it is taken at exactly this point.
    let announcement_variant = (rng.next_f64() * ANNOUNCEMENT_VARIANTS).floor() as usize;

    // ── Apply the rate change ─────────────────────────────────────────────
    // 8% ceiling: fed funds peaked at 5.5% (2023) and 6.5% (2006); only
    // Volcker went past 20%.
    new_economy.federal_funds_rate = clamp(current_rate + rate_change, 0.0, 8.0);
    new_economy.prime_rate = new_economy.federal_funds_rate + 3.0;
    // The put's books: a quarter given back pays the debt down; otherwise
    // whatever the overlay took off the ladder's path (a cut deepened, a
    // hike held) is added to the debt and to the decaying stock. The
    // intermeeting return restarts.
    if put_on {
        let ladder_rate = clamp(current_rate + ladder_change, 0.0, 8.0);
        let taken = mathx::max(0.0, ladder_rate - new_economy.federal_funds_rate);
        if unwind > 0.0 {
            new_economy.fed_put_owed = mathx::max(0.0, economy.fed_put_owed - unwind);
        } else if taken > 0.0 {
            new_economy.fed_put_owed = economy.fed_put_owed + taken;
            new_economy.fed_put = economy.fed_put + taken;
        }
        // THE UNANSWERED FALL (`fed_put_carry`): the clock restarts at the
        // share of the fall this meeting's cut did not answer, `min(0, I +
        // c / gain)` with `c` the cut the meeting took (whichever of the
        // ladder, the stress cut and the put chose it), while the put was
        // live at this meeting and the rate is still above zero. A branch:
        // at 0.0 it restarts at zero as it stood.
        new_economy.intermeeting_return = if options.put_carry != 0.0
            && !options.hold_rate
            && economy.inflation_rate < FED_PUT_INFLATION_CEILING
            && new_economy.federal_funds_rate > 0.0
        {
            let cut = mathx::max(0.0, current_rate - new_economy.federal_funds_rate);
            options.put_carry
                * mathx::min(0.0, economy.intermeeting_return + cut / options.put_gain)
        } else {
            0.0
        };
    }
    // What the 10-year hears on the day: the rate change, less the share of
    // the put the curve had priced (`treasury_put_pricing`), so a priced cut
    // is not news. The rate change itself with the put off.
    let surprise = if put_on && options.put_pricing != 0.0 {
        rate_change + options.put_pricing * mathx::min(asked, mathx::max(0.0, current_rate))
    } else {
        rate_change
    };
    // THE PRICED PATH (`treasury_path_pricing`): the market's forecast of the
    // rate's further change moves with the change it has just seen, so the
    // 10-year's target reads the rate plus the forecast after the meeting,
    // and the day's surprise carries the forecast's move as well as the
    // rate's. A branch: with the dial off both are as they stood.
    // The put's own cut and give-back are left out of the forecast: what the
    // put takes is owed and given back, so the change the forecast reads is
    // the rate's change plus the change in what is owed.
    let path_on = options.path_gain != 0.0;
    let owed_moved = if put_on { new_economy.fed_put_owed - economy.fed_put_owed } else { 0.0 };
    let path_after = if path_on {
        options.path_before
            + options.path_gain * (new_economy.federal_funds_rate - current_rate + owed_moved)
    } else {
        0.0
    };
    let surprise = if path_on { surprise + (path_after - options.path_before) } else { surprise };
    // THE DAMPED PASS-THROUGH (`treasury_policy_damping`): the 10-year reads
    // the rate the ladder sets (the policy rate plus what the put owes) and
    // the priced path, pulled toward the neutral rate by the dial; the put's
    // own overlay passes through whole. So the target and the day's surprise
    // carry `1 - d` of the ladder's move and the path's, and all of the
    // put's. A branch: with the dial off both are as they stood.
    let damp = options.rate_damping;
    let surprise = if damp != 0.0 {
        let ladder = (new_economy.federal_funds_rate - current_rate) + owed_moved
            + (path_after - options.path_before);
        surprise - damp * ladder
    } else {
        surprise
    };

    let treasury_target_10y = new_economy.federal_funds_rate
        + 1.0
        + mathx::max(0.0, (curve_inflation - 2.0) * 0.3);
    let treasury_target_10y = if path_on { treasury_target_10y + path_after } else { treasury_target_10y };
    let treasury_target_10y = if damp != 0.0 {
        let owed = if put_on { new_economy.fed_put_owed } else { 0.0 };
        treasury_target_10y
            - damp * (new_economy.federal_funds_rate + owed + path_after
                - super::daily::TREASURY_NEUTRAL_RATE)
    } else {
        treasury_target_10y
    };
    // The Treasury haven (`treasury_haven_gain`) in the target too, so the
    // meeting does not undo what the daily anchor has priced.
    let treasury_target_10y = if options.haven_gain != 0.0
        && curve_inflation < FED_PUT_INFLATION_CEILING
    {
        treasury_target_10y - super::daily::haven_term_cut(options.haven_gain, economy.vix)
    } else {
        treasury_target_10y
    };
    // Half the gap closes on announcement day; daily mean-reversion does the
    // rest.
    new_economy.treasury_yield_10y = clamp(
        economy.treasury_yield_10y
            + (treasury_target_10y - economy.treasury_yield_10y) * 0.50
            + surprise * 0.5,
        0.5,
        12.0,
    );
    new_economy.treasury_yield_2y = if path_on {
        (new_economy.federal_funds_rate + path_after) * 0.85 + new_economy.treasury_yield_10y * 0.15
    } else {
        new_economy.federal_funds_rate * 0.85 + new_economy.treasury_yield_10y * 0.15
    };

    // ── Mortgage rate: three floors and a cap, applied in order ───────────
    // The order matters — each step reads the result of the last.
    let mortgage_spread = 1.5 + (economy.vix - 12.0) * 0.015;
    let calculated_mortgage = new_economy.treasury_yield_10y + clamp(mortgage_spread, 1.5, 2.8);
    // Must exceed prime.
    new_economy.mortgage_rate_30y = mathx::max(new_economy.prime_rate + 0.5, calculated_mortgage);
    // Must sit at least 50bp above the 10Y.
    new_economy.mortgage_rate_30y = mathx::max(
        new_economy.treasury_yield_10y + 0.5,
        new_economy.mortgage_rate_30y,
    );
    // And no more than 350bp above it.
    let final_mortgage_spread = new_economy.mortgage_rate_30y - new_economy.treasury_yield_10y;
    if final_mortgage_spread > 3.5 {
        new_economy.mortgage_rate_30y = new_economy.treasury_yield_10y + 3.5;
    }

    // ── Corporate spread ──────────────────────────────────────────────────
    // The VIX slope's cut and credit's leverage term
    // (`corporate_spread_vix_cut`, `corporate_spread_equity_gain`), as the
    // close's daily move carries them, so the meeting re-anchors the level
    // the daily move keeps. Guarded: with both at 0.0 the base is the
    // expression that stood.
    let base_corporate_spread = if options.spread_vix_cut != 0.0 || options.spread_equity_gain != 0.0 {
        1.0 + (economy.vix - 12.0) * 0.02 * (1.0 - options.spread_vix_cut)
            + options.spread_equity_gain * economy.spread_equity_gap
    } else {
        1.0 + (economy.vix - 12.0) * 0.02
    };
    let cycle_spread_multiplier = match options.spread_multiplier {
        Some(m) => m,
        None => spread_multiplier_of(economy.cycle_phase),
    };
    let corporate_spread = base_corporate_spread * cycle_spread_multiplier;
    let calculated_corp_yield = new_economy.treasury_yield_10y + clamp(corporate_spread, CORPORATE_SPREAD_FLOOR, 6.0);
    // The nested max is redundant — `+0.8` dominates `+0.3` — but it is what
    // the original writes, and collapsing it would be a silent edit rather
    // than a port.
    new_economy.corporate_bond_yield = mathx::max(
        new_economy.treasury_yield_10y + 0.3,
        mathx::max(
            new_economy.treasury_yield_10y + CORPORATE_SPREAD_FLOOR,
            calculated_corp_yield,
        ),
    );

    // ── QE ────────────────────────────────────────────────────────────────
    if new_economy.federal_funds_rate <= 0.25 && economy.cycle_phase == CyclePhase::Contraction {
        new_cb.qe_active = true;
        new_cb.qe_monthly_purchases = 120.0;
    } else if central_bank.qe_active && economy.cycle_phase == CyclePhase::Expansion {
        new_cb.qe_monthly_purchases = mathx::max(0.0, central_bank.qe_monthly_purchases - 15.0);
        if new_cb.qe_monthly_purchases <= 0.0 {
            new_cb.qe_active = false;
        }
    }

    // Asset purchases compress long yields: $120B/month ≈ -50bp annualised,
    // applied per trading day.
    if new_cb.qe_active && new_cb.qe_monthly_purchases > 0.0 {
        let qe_suppression = (new_cb.qe_monthly_purchases / 120.0) * 0.50;
        new_economy.treasury_yield_10y =
            mathx::max(0.1, new_economy.treasury_yield_10y - qe_suppression / 252.0);
    }

    // Cheap money inflates multiples. Stored on the economy so the market
    // module can read it without a central-bank reference.
    if new_cb.qe_active && new_cb.qe_monthly_purchases > 0.0 {
        new_economy.qe_pe_boost = 0.10 * (new_cb.qe_monthly_purchases / 120.0);
        // The STOCK the flow accumulates into, per trading day, over a
        // neutral baseline of $5,000B -- roughly the pre-2020 portfolio.
        // Consumed only by `qe_pe_stock_gain`, which ships at 0.0, so this
        // integration is invisible until a preset turns that dial. No
        // runoff is modelled: the engine's cycle has QE start and taper,
        // and balance-sheet reduction is future work, stated rather than
        // faked.
        new_economy.qe_assets_ratio +=
            (new_cb.qe_monthly_purchases / 21.0) / QE_ASSETS_BASELINE_B;
    } else {
        new_economy.qe_pe_boost = 0.0;
    }

    new_cb.forward_guidance = if new_cb.hawkish_dovish_score > 0.3 {
        ForwardGuidance::OngoingIncreases
    } else if new_cb.hawkish_dovish_score < -0.3 {
        ForwardGuidance::Accommodative
    } else {
        ForwardGuidance::AsAppropriate
    };

    // ── Schedule the next meeting ─────────────────────────────────────────
    new_cb.last_meeting_date = current_timestamp;
    // Reads the rate this meeting has JUST SET, deliberately. The question is
    // not "was the bank behind the curve when it walked in", it is "did the
    // decision it just took leave it behind", and an early follow-up is
    // warranted only in the second case.
    //
    // RETRACTED, 2026-08-25: changed to the pre-decision rate on the belief
    // that the path was unreachable, having measured zero firings across five
    // seeds and five years and again under pinned inflation up to 9%. The JS
    // oracle rejected it, 1618 mismatches, and it was right. Those scenarios
    // all had low unemployment, so the Taylor rule hiked hard and genuinely
    // did catch up, and not firing was the correct answer.
    //
    // Where it fires is stagflation, which none of those scenarios produced:
    // at inflation 4.5 with unemployment 9.0 the bank CUTS for the output gap
    // and leaves itself further behind on inflation, so the post-decision gap
    // widens past 2pp and the next meeting is pulled in to 21-30 days. The
    // pre-decision reading gets that case backwards, scheduling a normal
    // cadence there and a crisis cadence at inflation 7.0 with unemployment
    // 3.0, where the bank has already caught up. See `economy_parity`.
    let inflation_crisis = new_economy.inflation_rate > new_economy.federal_funds_rate + 2.0
        && new_economy.inflation_rate > 4.0;
    // DRAW SITE — always, on both branches.
    // Intervals are calendar days; on another macro calendar they are scaled
    // to its steps (`MacroCalendar::scale_days`, the literal as shipped).
    let cal = options.calendar;
    new_cb.next_meeting_date = if inflation_crisis {
        // Crisis cadence: 21-30 days.
        current_timestamp + cal.scale_days(21 + (rng.next_f64() * 10.0).floor() as i64) * 24 * 60
    } else {
        // Normal cadence: 6-8 weeks.
        current_timestamp + cal.scale_days(42 + (rng.next_f64() * 14.0).floor() as i64) * 24 * 60
    };

    MeetingOutcome {
        central_bank: new_cb,
        economy: new_economy,
        decision: Some(decision),
        announcement_variant: Some(announcement_variant),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rng::GameRng;

    /// A meeting day on a calm economy: inflation and unemployment at
    /// target, so the Taylor rate is 2.0 and at a policy rate of 2.0 the
    /// ladder holds.
    fn meeting(growth: f64, rate: f64, inflation: f64, options: &PolicyOptions) -> Option<Decision> {
        let bank = create_initial_central_bank_state(0);
        let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
        economy.cycle_phase = CyclePhase::Expansion;
        economy.gdp_growth = growth;
        economy.federal_funds_rate = rate;
        economy.inflation_rate = inflation;
        economy.unemployment_rate = bank.target_unemployment;
        let mut rng = GameRng::new(1, 1);
        update_central_bank_with(&bank, &economy, bank.next_meeting_date, &mut rng, options).decision
    }

    /// `fed_core_inflation`: the meeting on core is the meeting on an
    /// economy whose headline is core, decision and rates alike, and it
    /// hands back the headline it was given.
    #[test]
    fn the_core_reading_meeting_is_the_meeting_at_core() {
        let bank = create_initial_central_bank_state(0);
        let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
        economy.cycle_phase = CyclePhase::Expansion;
        economy.gdp_growth = 1.0;
        economy.federal_funds_rate = 2.0;
        economy.unemployment_rate = bank.target_unemployment;
        economy.inflation_rate = 3.6;
        let on = PolicyOptions { growth_cut: 2.0, policy_inflation: Some(2.0), ..PolicyOptions::shipped() };
        let mut at_core = economy.clone();
        at_core.inflation_rate = 2.0;
        let plain = PolicyOptions { growth_cut: 2.0, curve_inflation: Some(3.6), ..PolicyOptions::shipped() };
        let a = update_central_bank_with(&bank, &economy, bank.next_meeting_date, &mut GameRng::new(1, 1), &on);
        let b = update_central_bank_with(&bank, &at_core, bank.next_meeting_date, &mut GameRng::new(1, 1), &plain);
        assert_eq!(a.decision, Some(Decision::Cut));
        assert_eq!(a.decision, b.decision);
        assert_eq!(a.economy.federal_funds_rate.to_bits(), b.economy.federal_funds_rate.to_bits());
        assert_eq!(a.economy.treasury_yield_10y.to_bits(), b.economy.treasury_yield_10y.to_bits());
        assert_eq!(a.economy.inflation_rate.to_bits(), 3.6f64.to_bits());
        // Headline read, the growth cut is closed at 3.6.
        let off = PolicyOptions { growth_cut: 2.0, ..PolicyOptions::shipped() };
        let c = update_central_bank_with(&bank, &economy, bank.next_meeting_date, &mut GameRng::new(1, 1), &off);
        assert_ne!(c.decision, Some(Decision::Cut));
    }

    /// `fed_growth_cut`: off, the calm meeting holds whatever growth does;
    /// on, it cuts a quarter point when growth is under the dial, inflation
    /// under target plus 1.5 and the rate above zero, and not otherwise;
    /// and it sits before the lift-off branch, which a rate 60 bp under the
    /// Taylor rate would otherwise take.
    #[test]
    fn the_growth_cut_fires_under_its_dial_and_before_lift_off() {
        let off = PolicyOptions::shipped();
        let on = PolicyOptions { growth_cut: 2.0, ..PolicyOptions::shipped() };
        assert_eq!(meeting(1.0, 2.0, 2.0, &off), Some(Decision::Hold));
        assert_eq!(meeting(1.0, 2.0, 2.0, &on), Some(Decision::Cut));
        // The cut is a quarter point and moves the score by -0.2.
        {
            let bank = create_initial_central_bank_state(0);
            let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
            economy.cycle_phase = CyclePhase::Expansion;
            economy.gdp_growth = 1.0;
            economy.federal_funds_rate = 2.0;
            economy.inflation_rate = 2.0;
            economy.unemployment_rate = bank.target_unemployment;
            let mut rng = GameRng::new(1, 1);
            let out = update_central_bank_with(&bank, &economy, bank.next_meeting_date, &mut rng, &on);
            assert!((out.economy.federal_funds_rate - 1.75).abs() < 1e-12);
            assert!((out.central_bank.hawkish_dovish_score + 0.2).abs() < 1e-12);
        }
        // Growth at or above the dial, inflation too high, or no room: no cut.
        assert_eq!(meeting(2.0, 2.0, 2.0, &on), Some(Decision::Hold));
        assert_eq!(meeting(2.5, 2.0, 2.0, &on), Some(Decision::Hold));
        assert_eq!(meeting(1.0, 0.0, 2.0, &on), Some(Decision::Hold));
        assert_ne!(meeting(1.0, 2.0, 3.6, &on), Some(Decision::Cut));
        // A negative dial waits for output to fall.
        let deep = PolicyOptions { growth_cut: -0.5, ..PolicyOptions::shipped() };
        assert_eq!(meeting(0.0, 2.0, 2.0, &deep), Some(Decision::Hold));
        assert_eq!(meeting(-1.0, 2.0, 2.0, &deep), Some(Decision::Cut));
        // Before lift-off: at a rate 60 bp under the Taylor rate the lift-off
        // branch hikes, unless growth is under the dial.
        let liftoff = PolicyOptions { liftoff: 1.0, ..PolicyOptions::shipped() };
        let both = PolicyOptions { liftoff: 1.0, growth_cut: 2.0, ..PolicyOptions::shipped() };
        assert_eq!(meeting(1.0, 1.4, 2.0, &liftoff), Some(Decision::Hike));
        assert_eq!(meeting(1.0, 1.4, 2.0, &both), Some(Decision::Cut));
        assert_eq!(meeting(3.0, 1.4, 2.0, &both), Some(Decision::Hike));
    }
}

#[cfg(test)]
mod stress_cut {
    use super::*;
    use crate::economy::state::{
        create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions,
    };

    struct Silent;
    impl Rng for Silent {
        fn next_f64(&mut self) -> f64 {
            0.5
        }
        fn next_normal(&mut self) -> f64 {
            0.0
        }
    }

    /// A meeting on a calm economy the ladder holds at: inflation on target,
    /// unemployment at its target, the rate at the rule's.
    fn meet(economy: &EconomyState, options: &PolicyOptions) -> MeetingOutcome {
        let mut cb = create_initial_central_bank_state(0);
        cb.next_meeting_date = -1;
        update_central_bank_with(&cb, economy, 1000, &mut Silent, options)
    }

    fn calm() -> EconomyState {
        let mut e = create_initial_economy_state(&InitialEconomyOptions::default());
        e.inflation_rate = 2.0;
        e.unemployment_rate = 4.0;
        e.federal_funds_rate = 2.0;
        e.gdp_growth = 2.0;
        e
    }

    fn with(level: f64) -> PolicyOptions {
        PolicyOptions { stress_cut: 0.25, stress_vix: 30.0, stress_level: level, ..PolicyOptions::shipped() }
    }

    #[test]
    fn off_the_cut_reads_nothing_it_is_given() {
        let e = calm();
        let shipped = meet(&e, &PolicyOptions::shipped());
        let off = PolicyOptions { stress_level: 80.0, stress_vix: 10.0, stress_inflation_gap: 9.0,
                                  ..PolicyOptions::shipped() };
        assert_eq!(meet(&e, &off), shipped);
    }

    #[test]
    fn the_cut_fires_at_the_stress_level_and_steps_every_ten_points() {
        let e = calm();
        let hold = meet(&e, &PolicyOptions::shipped());
        assert_eq!(hold.decision, Some(Decision::Hold), "the calm economy holds");
        // Below the start: the ladder's decision, bit for bit.
        assert_eq!(meet(&e, &with(29.99)), hold);
        let cases = [(30.0, 0.25, Decision::Cut), (39.9, 0.25, Decision::Cut),
                     (40.0, 0.5, Decision::EmergencyCut), (49.9, 0.5, Decision::EmergencyCut),
                     (50.0, 0.75, Decision::EmergencyCut), (60.0, 1.0, Decision::EmergencyCut),
                     (200.0, 1.0, Decision::EmergencyCut)];
        for (level, cut, decision) in cases {
            let out = meet(&e, &with(level));
            assert_eq!(out.decision, Some(decision), "level {level}");
            assert!((out.economy.federal_funds_rate - (2.0 - cut)).abs() < 1e-12,
                    "level {level}: rate {}", out.economy.federal_funds_rate);
            assert!((out.central_bank.hawkish_dovish_score - (-0.2)).abs() < 1e-12);
        }
    }

    #[test]
    fn the_cut_never_takes_the_rate_below_zero_and_waits_on_inflation() {
        let mut e = calm();
        e.federal_funds_rate = 0.3;
        let out = meet(&e, &PolicyOptions { stress_cut: 0.5, ..with(60.0) });
        assert_eq!(out.economy.federal_funds_rate, 0.0);
        e.federal_funds_rate = 0.0;
        assert_ne!(meet(&e, &with(60.0)).decision, Some(Decision::EmergencyCut));
        // Inflation at target plus the gap closes the gate.
        let mut hot = calm();
        hot.inflation_rate = 3.0;
        let gated = meet(&hot, &with(45.0));
        assert_eq!(gated, meet(&hot, &PolicyOptions::shipped()));
        let wide = meet(&hot, &PolicyOptions { stress_inflation_gap: 2.0, ..with(45.0) });
        assert!(wide.economy.federal_funds_rate < hot.federal_funds_rate);
    }

    #[test]
    fn the_cut_replaces_a_hike_and_a_smaller_cut_but_not_a_deeper_one() {
        // A hike: inflation over target plus one with the rate under the rule.
        let mut e = calm();
        e.inflation_rate = 3.2;
        e.federal_funds_rate = 1.0;
        let ladder = meet(&e, &PolicyOptions::shipped());
        assert!(ladder.economy.federal_funds_rate > 1.0, "the ladder hikes here");
        let stressed = meet(&e, &PolicyOptions { stress_inflation_gap: 1.5, ..with(35.0) });
        assert_eq!(stressed.decision, Some(Decision::Cut));
        assert!((stressed.economy.federal_funds_rate - 0.75).abs() < 1e-12);
        // A deeper cut the ladder chose (a deep recession) stands.
        let mut slump = calm();
        slump.gdp_growth = -3.0;
        slump.unemployment_rate = 11.0;
        slump.federal_funds_rate = 4.0;
        let deep = meet(&slump, &PolicyOptions::shipped());
        assert_eq!(meet(&slump, &with(35.0)), deep);
    }
}

#[cfg(test)]
mod spread_dials {
    use super::*;
    use crate::economy::state::{
        create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions,
    };

    struct Silent;
    impl Rng for Silent {
        fn next_f64(&mut self) -> f64 {
            0.5
        }
        fn next_normal(&mut self) -> f64 {
            0.0
        }
    }

    fn meet(economy: &EconomyState, options: &PolicyOptions) -> MeetingOutcome {
        let mut cb = create_initial_central_bank_state(0);
        cb.next_meeting_date = -1;
        update_central_bank_with(&cb, economy, 1000, &mut Silent, options)
    }

    #[test]
    fn the_formula_with_nothing_set_is_the_formula_bit_for_bit() {
        for vix in [9.0, 12.0, 17.3, 31.0, 80.0] {
            for m in [1.0, 1.1, 1.4, 2.8, 3.5] {
                assert_eq!(spread_formula_with(vix, m, 0.0, 0.0).to_bits(),
                           spread_formula(vix, m).to_bits());
            }
        }
    }

    #[test]
    fn the_cut_scales_the_slope_and_the_equity_term_joins_the_base() {
        // Whole cut: the VIX reads nothing.
        assert_eq!(spread_formula_with(45.0, 2.8, 1.0, 0.25), (1.25f64 * 2.8).min(6.0));
        // Half cut at a VIX of 32: 1 + 0.01 * 20 = 1.2, plus 0.1, times 1.4.
        assert!((spread_formula_with(32.0, 1.4, 0.5, 0.1) - 1.3 * 1.4).abs() < 1e-12);
        // The clamp holds either way.
        assert_eq!(spread_formula_with(12.0, 1.0, 0.0, -0.9), CORPORATE_SPREAD_FLOOR);
        assert_eq!(spread_formula_with(80.0, 3.5, 0.0, 2.0), 6.0);
    }

    #[test]
    fn the_meeting_re_anchors_to_the_gap_and_off_it_reads_nothing() {
        let mut e = create_initial_economy_state(&InitialEconomyOptions::default());
        e.inflation_rate = 2.0;
        e.unemployment_rate = 4.0;
        e.federal_funds_rate = 2.0;
        e.vix = 30.0;
        e.spread_equity_gap = 0.2;
        let shipped = meet(&e, &PolicyOptions::shipped());
        // A gap in the state with both dials off moves nothing it writes.
        let mut no_gap = e.clone();
        no_gap.spread_equity_gap = 0.0;
        let mut off = meet(&no_gap, &PolicyOptions::shipped());
        off.economy.spread_equity_gap = 0.2;
        assert_eq!(off, shipped);
        let on = PolicyOptions { spread_vix_cut: 1.0, spread_equity_gain: 2.0, ..PolicyOptions::shipped() };
        let out = meet(&e, &on);
        let m = spread_multiplier_of(e.cycle_phase);
        let want = clamp((1.0 + 2.0 * 0.2) * m, CORPORATE_SPREAD_FLOOR, 6.0);
        let got = out.economy.corporate_bond_yield - out.economy.treasury_yield_10y;
        assert!((got - want).abs() < 1e-12, "spread {got} against {want}");
        // Everything but the corporate yield is the shipped meeting's.
        assert_eq!(out.economy.treasury_yield_10y, shipped.economy.treasury_yield_10y);
        assert_eq!(out.economy.federal_funds_rate, shipped.economy.federal_funds_rate);
    }
}

#[cfg(test)]
mod put_carry {
    use super::*;
    use crate::economy::state::{
        create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions,
    };

    struct Silent;
    impl Rng for Silent {
        fn next_f64(&mut self) -> f64 {
            0.5
        }
        fn next_normal(&mut self) -> f64 {
            0.0
        }
    }

    fn meet(economy: &EconomyState, options: &PolicyOptions) -> MeetingOutcome {
        let mut cb = create_initial_central_bank_state(0);
        cb.next_meeting_date = -1;
        update_central_bank_with(&cb, economy, 1000, &mut Silent, options)
    }

    fn calm(intermeeting: f64) -> EconomyState {
        let mut e = create_initial_economy_state(&InitialEconomyOptions::default());
        e.inflation_rate = 2.0;
        e.unemployment_rate = 4.0;
        e.federal_funds_rate = 2.0;
        e.gdp_growth = 2.0;
        e.intermeeting_return = intermeeting;
        e
    }

    fn put(carry: f64) -> PolicyOptions {
        PolicyOptions { put_gain: 3.0, put_carry: carry, ..PolicyOptions::shipped() }
    }

    /// At 0.0 the clock restarts at zero whatever the meeting read.
    #[test]
    fn off_the_clock_restarts_at_zero() {
        for i in [-0.03, -0.06, -0.2, 0.04] {
            let out = meet(&calm(i), &put(0.0));
            assert_eq!(out.economy.intermeeting_return, 0.0, "I {i}");
        }
    }

    /// A 3 per cent fall asks 0.09, which rounds to no cut, so the whole
    /// fall is carried; the next 3 per cent then takes a quarter point and
    /// the carried part is what that quarter did not answer.
    #[test]
    fn an_unanswered_fall_is_carried_and_a_cut_answers_its_share() {
        let first = meet(&calm(-0.03), &put(1.0));
        assert_eq!(first.economy.federal_funds_rate, 2.0);
        assert!((first.economy.intermeeting_return + 0.03).abs() < 1e-12);
        let second = meet(&calm(first.economy.intermeeting_return - 0.03), &put(1.0));
        assert!((second.economy.federal_funds_rate - 1.75).abs() < 1e-12);
        // 0.06 less the 0.083 a quarter answers is a rise: nothing carried.
        assert_eq!(second.economy.intermeeting_return, 0.0);
        // A 10 per cent fall: 0.3 rounds to a quarter, and 0.1 - 0.25/3 is carried.
        let big = meet(&calm(-0.10), &put(1.0));
        assert!((big.economy.federal_funds_rate - 1.75).abs() < 1e-12);
        assert!((big.economy.intermeeting_return - (-0.10 + 0.25 / 3.0)).abs() < 1e-12);
        // The share scales it; a rise carries nothing.
        let half = meet(&calm(-0.03), &put(0.5));
        assert!((half.economy.intermeeting_return + 0.015).abs() < 1e-12);
        assert_eq!(meet(&calm(0.04), &put(1.0)).economy.intermeeting_return, 0.0);
    }

    /// Nothing is carried at the floor, with inflation at the put's ceiling
    /// or on a pinned rate; the carry never moves the meeting's decision.
    #[test]
    fn the_carry_stops_at_the_floor_the_ceiling_and_a_pin() {
        let mut floor = calm(-0.5);
        floor.federal_funds_rate = 0.25;
        let out = meet(&floor, &put(1.0));
        assert_eq!(out.economy.federal_funds_rate, 0.0);
        assert_eq!(out.economy.intermeeting_return, 0.0);
        let mut hot = calm(-0.03);
        hot.inflation_rate = FED_PUT_INFLATION_CEILING;
        assert_eq!(meet(&hot, &put(1.0)).economy.intermeeting_return, 0.0);
        let pinned = PolicyOptions { hold_rate: true, ..put(1.0) };
        assert_eq!(meet(&calm(-0.03), &pinned).economy.intermeeting_return, 0.0);
        for i in [-0.03, -0.1, 0.02] {
            let mut on = meet(&calm(i), &put(1.0));
            let off = meet(&calm(i), &put(0.0));
            on.economy.intermeeting_return = 0.0;
            assert_eq!(on, off, "I {i}");
        }
    }
}
