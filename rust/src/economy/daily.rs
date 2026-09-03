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
//! Worse than a count: [`GameRng::next_normal`] caches a **spare**. Box-Muller
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

/// Inputs that the caller supplies per day.
#[derive(Debug, Clone, Copy)]
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
    pub vix_return_clamp: f64,
    pub vix_target_shock_cap: f64,
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
}

impl<'a> Default for DailyInputs<'a> {
    fn default() -> Self {
        Self {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: 0,
            vix_mean_reversion: VIX_MEAN_REVERSION,
            vix_decay_ratio: 1.0,
            vix_jump_intensity: 0.0,
            vix_jump_scale: 0.0,
            vix_return_gain: VIX_RETURN_GAIN,
            vix_realised_vol_weight: 0.0,
            vix_return_source: 0.0,
            vix_cycle_amplitude: 1.0,
            market_day_return_pct: 0.0,
            vix_implied_from_market: 0.0,
            vix_return_gain_up: VIX_RETURN_GAIN_UP,
            vix_return_clamp: VIX_RETURN_CLAMP,
            vix_target_shock_cap: VIX_TARGET_SHOCK_CAP,
            inflation_reversion: INFLATION_MEAN_REVERSION,
            inflation_ceiling: INFLATION_CEILING,
            inflation_floor: INFLATION_FLOOR,
            crisis_vix_threshold: CRISIS_VIX_THRESHOLD,
            usd_crisis_vix_threshold: CRISIS_VIX_THRESHOLD,
            daily_credit_floor_gain: 0.0,
            oil_supply_response: 0.0,
        }
    }
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
    let phase = phase_characteristics(economy.cycle_phase);
    let day = inputs.game_day;

    let is_month_start = day % DAYS_PER_MONTH == 0;
    let is_quarter_start = day % DAYS_PER_QUARTER == 0;

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
    if economy.months_in_current_phase < 1.0 / 30.0 + 0.001 {
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
    }

    // ── Quarterly GDP ─────────────────────────────────────────────────────
    if is_quarter_start {
        let target_gdp =
            (phase.gdp_growth_range.0 + phase.gdp_growth_range.1) / 2.0 + shock_gdp_impact;
        new_state.gdp_growth = clamp(
            new_state.gdp_growth
                + (target_gdp - new_state.gdp_growth) * 0.25
                + random_normal(rng, 0.0, 0.3 * volatility),
            gdp_floor,
            6.0,
        );
    }

    // GDP level compounds daily from the CURRENT growth rate.
    new_state.gdp = economy.gdp * (1.0 + new_state.gdp_growth / 100.0 / 365.0);

    // ── Monthly releases ──────────────────────────────────────────────────
    if is_month_start {
        let phase_gdp_target = (phase.gdp_growth_range.0 + phase.gdp_growth_range.1) / 2.0;
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
        let gdp_effect = (2.0 - new_state.gdp_growth) * 0.20;
        let nairu = economy.structural_unemployment;
        let nairu_pull = (nairu - economy.unemployment_rate) * 0.06;
        let mut recovery_effect = 0.0;
        if (economy.cycle_phase == CyclePhase::Expansion
            || economy.cycle_phase == CyclePhase::Recovery)
            && new_state.gdp_growth > 1.0
        {
            recovery_effect = -new_state.gdp_growth * 0.08;
        }
        new_state.unemployment_rate = clamp(
            economy.unemployment_rate
                + phase.unemployment_trend * 0.3
                + gdp_effect
                + nairu_pull
                + recovery_effect
                + random_normal(rng, 0.0, 0.06 * volatility),
            2.5,
            15.0,
        );

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

        let oil_inflation_effect = if economy.oil_price > 80.0 {
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
        new_state.structural_unemployment = 4.0 + new_state.long_term_unemployment_rate * 0.3;

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
    new_state.cpi = economy.cpi * (1.0 + new_state.inflation_rate / 100.0 / 365.0);

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
        oil_demand_factor - oil_supply_factor + random_normal(rng, 0.0, 0.5 * volatility);
    let new_oil_inventory = clamp(oil_inventory - inventory_change, 0.0, 100.0);
    new_state.oil_inventory_level = new_oil_inventory;

    let mut oil_inventory_pressure = 0.0;
    if new_oil_inventory < 40.0 {
        oil_inventory_pressure = (40.0 - new_oil_inventory) * 0.08;
    } else if new_oil_inventory > 60.0 {
        oil_inventory_pressure = (60.0 - new_oil_inventory) * 0.08;
    }

    // Summer driving season peaks ~day 180, winter valley ~day 90.
    //
    // `(day - 1) % 365 + 1` — JavaScript's `%` keeps the sign of the
    // dividend, and so does Rust's, so a day of 0 gives -1 % 365 = -1 and a
    // dayOfYear of 0 in both. Reproduced rather than corrected.
    let day_of_year = ((day - 1) % 365) + 1;
    let oil_seasonality =
        1.0 + 0.03 * mathx::sin(2.0 * std::f64::consts::PI * (day_of_year as f64 - 90.0) / 365.0);

    let oil_usd_drag = -(economy.usd_index - 100.0) * 0.08;

    // ── OPEC ──────────────────────────────────────────────────────────────
    // A state-dependent draw site: 0 draws on an ordinary day, 1 to 3 on a
    // decision day depending on which branch the price difference selects.
    let mut opec_impact = 0.0;
    if day - oil_last_opec >= OIL_OPEC_INTERVAL {
        new_state.oil_last_opec_day = day;
        let oil_price = economy.oil_price;
        let opec_target = 80.0;
        let price_diff = oil_price - opec_target;

        if price_diff < -10.0 {
            // Well below target: likely a production cut.
            if rng.next_f64() < 0.6 {
                opec_impact = 3.0 + rng.next_f64() * 3.0;
            }
        } else if price_diff > 10.0 {
            // Well above target: likely a production increase.
            if rng.next_f64() < 0.5 {
                opec_impact = -(2.0 + rng.next_f64() * 3.0);
            }
        } else {
            // In the comfort zone: a small adjustment.
            if rng.next_f64() < 0.2 {
                opec_impact = (rng.next_f64() - 0.5) * 3.0;
            }
        }
    }

    let oil_target = OIL_BASELINE + economy.gdp_growth * 3.0 + shock_oil_impact * 10.0;
    let oil_mean_rev = (oil_target - economy.oil_price) * 0.03;
    let oil_volatility = 2.0 * volatility;
    new_state.oil_price = clamp(
        (economy.oil_price
            + oil_mean_rev
            + oil_inventory_pressure
            + oil_usd_drag
            + opec_impact
            + random_normal(rng, 0.0, oil_volatility)
            + shock_oil_impact * 0.1)
            * oil_seasonality,
        35.0,
        150.0,
    );

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
    let usd_mean_reversion = (usd_target - economy.usd_index) * 0.02;
    // Reference: `vix > 30` — dead for the same reason as the gold crisis
    // premium above; re-sited with it. See `CRISIS_VIX_THRESHOLD`.
    //
    // Reads the PARAMETER, not the constant. It read the constant until
    // 0.4.2, so an embedder who moved `crisis_vix_threshold` got a gold gate
    // at their level and a dollar gate still at 25.5. The two describe one
    // regime: a crisis is the same crisis whether you watch gold or the
    // dollar. No behaviour changes at the default, where the two agree.
    let safe_haven_drift = if economy.vix > inputs.usd_crisis_vix_threshold {
        (economy.vix - inputs.usd_crisis_vix_threshold) * 0.05
    } else {
        0.0
    };
    let usd_change =
        usd_mean_reversion + safe_haven_drift + random_normal(rng, 0.0, 0.3 * volatility);
    new_state.usd_index = clamp(economy.usd_index + usd_change, 80.0, 130.0);

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
    // THE CLOCK THE CLUSTERING RUNS ON. Those five constants move on a
    // multi-year cycle, so volatility regimes here are years long and a
    // one-year window contains no regime change at all. At amplitude 1.0
    // this is the shipped arithmetic exactly. See §71.
    let mut target_vix = if inputs.vix_cycle_amplitude == 1.0 {
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
    let driving_return = if inputs.vix_return_source == 0.0 {
        inputs.market_return_pct
    } else {
        let s = inputs.vix_return_source;
        (1.0 - s) * inputs.market_return_pct + s * inputs.market_day_return_pct
    };
    let current_mkt_ret_vix = mathx::max(-clamp_vix, mathx::min(clamp_vix, driving_return));
    let return_spike = if current_mkt_ret_vix < 0.0 {
        -current_mkt_ret_vix * inputs.vix_return_gain
    } else {
        -current_mkt_ret_vix * inputs.vix_return_gain_up
    };
    let inflation_adj = mathx::max(0.0, (economy.inflation_rate - 3.0) * 0.2);
    let shock_adj = shock_gdp_impact.abs() * 2.0;
    target_vix += mathx::min(
        inputs.vix_target_shock_cap,
        return_spike + inflation_adj + shock_adj,
    );

    // Earnings-season bump. The 30.44 is a mean month length, so this is not
    // the same day-of-month the month-start blocks above use.
    let earnings_month_vix = (((day_of_year - 1) % 365) as f64 / 30.44).floor() + 1.0;
    let day_of_month_vix = day_of_year as f64 - ((earnings_month_vix - 1.0) * 30.44).floor();
    if day_of_month_vix <= 15.0 {
        target_vix += 0.5;
    }

    // THE FEEDBACK LOOP, WHICH RAN ONE WAY. The VIX sets the market
    // factor's variance target and the market's own volatility never came
    // back, so the VIX was a function of the business cycle and not of the
    // market: it tracked trailing realised volatility at +0.28 against a
    // real +0.82, and never once crossed its own crisis threshold in a year
    // (§68). At weight zero this branch is not taken and every preset
    // reproduces bit for bit.
    if inputs.vix_realised_vol_weight != 0.0 {
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
    let fear_jump = if inputs.vix_jump_intensity != 0.0 {
        let p_daily = inputs.vix_jump_intensity / 252.0;
        if rng.next_f64() < p_daily {
            // Exponential magnitude: mean `vix_jump_scale` points.
            inputs.vix_jump_scale * -mathx::log(mathx::max(rng.next_f64(), 1e-12))
        } else {
            0.0
        }
    } else {
        0.0
    };
    new_state.vix = clamp(
        economy.vix
            + (target_vix - economy.vix) * vix_mr
            + random_normal(rng, 0.0, 0.15 * volatility)
            + fear_jump,
        10.0,
        80.0,
    );

    // ── Treasury yields ───────────────────────────────────────────────────
    let debt_premium = mathx::max(0.0, (economy.government_debt_to_gdp - 100.0) * 0.002);
    let term_premium_10y =
        1.0 + mathx::max(0.0, (economy.inflation_rate - 2.0) * 0.3) + debt_premium;
    let fed_rate_for_10y = new_state.federal_funds_rate;
    let current_10y = new_state.treasury_yield_10y;

    // D5, decided: KEEP the draw. In production `wasm10Y ?? (…)` short-
    // circuits and this normal is never taken — a consequence of `??`, not a
    // modelling choice. Dropping it would also flip the Box-Muller spare
    // parity for every later normal in the engine.
    new_state.treasury_yield_10y = clamp(
        current_10y
            + (fed_rate_for_10y + term_premium_10y - current_10y) * 0.05
            + random_normal(rng, 0.0, 0.03 * volatility),
        0.5,
        12.0,
    );
    new_state.treasury_yield_2y = fed_rate_for_10y * 0.85 + new_state.treasury_yield_10y * 0.15;

    // Bond-stock correlation regime: inflation sets the sign.
    let prev_mkt_ret = economy.previous_day_market_return;
    if prev_mkt_ret.abs() > 0.5 {
        let bond_stock_yield_shift = if economy.inflation_rate > 4.0 {
            // Positive correlation: stocks down, yields up.
            -prev_mkt_ret * 0.02
        } else if economy.inflation_rate < 3.0 {
            // Flight to quality.
            prev_mkt_ret * 0.02
        } else {
            0.0
        };
        new_state.treasury_yield_10y = clamp(
            new_state.treasury_yield_10y + bond_stock_yield_shift,
            0.5,
            12.0,
        );
        new_state.treasury_yield_2y =
            new_state.federal_funds_rate * 0.85 + new_state.treasury_yield_10y * 0.15;
    }

    // ── Fear/greed ────────────────────────────────────────────────────────
    let fear_greed_phase_bonus = match economy.cycle_phase {
        CyclePhase::Expansion => 15.0,
        CyclePhase::Peak => 5.0,
        CyclePhase::Contraction => -25.0,
        CyclePhase::Trough => -20.0,
        CyclePhase::Recovery => 10.0,
    };
    let market_sentiment = inputs.market_return_pct * 5.0;
    let fear_greed_base = 50.0 + economy.gdp_growth * 3.0 - (economy.vix - 15.0) * 0.8
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

    new_state.months_in_current_phase = economy.months_in_current_phase + 1.0 / 30.0;
    new_state.previous_day_market_return = inputs.market_return_pct;

    let prev_30d = economy.rolling_market_return_30d;
    new_state.rolling_market_return_30d = prev_30d + (inputs.market_return_pct - prev_30d) / 30.0;

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
