//! `updateEconomyDaily`, ported from `src/lib/engine/economy.ts:545`.
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
use crate::rng::Rng;

/// `randomNormal(mean, stdDev)` — the local wrapper at `economy.ts:364`.
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
    /// TypeScript default: 1.0.
    pub volatility: f64,
    pub active_shocks: &'a [EconomicShock],
    /// TypeScript default: 0.
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
    /// Monthly fraction of the inflation gap closed toward the 2% target.
    /// Threaded like `vix_mean_reversion`: the shipped 0.55 was a literal
    /// inside the inflation update, which made inflation's persistence and
    /// dispersion a fact of the code rather than a calibration.
    pub inflation_reversion: f64,
    /// Hard ceiling on endogenous inflation, percent (shipped 6.0).
    pub inflation_ceiling: f64,
    /// VIX level at which crisis behaviour begins.
    ///
    /// Gates the sector-to-market correlation blend, the universe stress
    /// memory and the economy's crisis premium. Threaded for the same
    /// reason: where a crisis STARTS is a calibration question, and a const
    /// answers it before anyone asks.
    pub crisis_vix_threshold: f64,
}

impl<'a> Default for DailyInputs<'a> {
    fn default() -> Self {
        Self {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: 0,
            vix_mean_reversion: VIX_MEAN_REVERSION,
            inflation_reversion: INFLATION_MEAN_REVERSION,
            inflation_ceiling: INFLATION_CEILING,
            crisis_vix_threshold: CRISIS_VIX_THRESHOLD,
        }
    }
}

/// One simulated day of the macro chain.
///
/// The TypeScript spread-copies (`{ ...economy }`) and returns new state.
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
            -1.0,
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
    let oil_supply_factor = 0.0;
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
    let safe_haven_drift = if economy.vix > CRISIS_VIX_THRESHOLD {
        (economy.vix - CRISIS_VIX_THRESHOLD) * 0.05
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
    // Pure JS since "Cycle 71". `wasmCalculateVixTarget` is imported at
    // `economy.ts:16` and never called — an import-driven worklist would
    // invent work here.
    let mut target_vix = match economy.cycle_phase {
        CyclePhase::Expansion => 14.0,
        CyclePhase::Peak => 18.0,
        CyclePhase::Contraction => 25.0,
        CyclePhase::Trough => 22.0,
        CyclePhase::Recovery => 16.0,
    };
    // The CURRENT day's return, not the previous one, so VIX reacts same-day
    // and the negative correlation is real.
    let current_mkt_ret_vix = mathx::max(-0.03, mathx::min(0.03, inputs.market_return_pct));
    let return_spike = if current_mkt_ret_vix < 0.0 {
        -current_mkt_ret_vix * 25.0
    } else {
        -current_mkt_ret_vix * 10.0
    };
    let inflation_adj = mathx::max(0.0, (economy.inflation_rate - 3.0) * 0.2);
    let shock_adj = shock_gdp_impact.abs() * 2.0;
    target_vix += mathx::min(12.0, return_spike + inflation_adj + shock_adj);

    // Earnings-season bump. The 30.44 is a mean month length, so this is not
    // the same day-of-month the month-start blocks above use.
    let earnings_month_vix = (((day_of_year - 1) % 365) as f64 / 30.44).floor() + 1.0;
    let day_of_month_vix = day_of_year as f64 - ((earnings_month_vix - 1.0) * 30.44).floor();
    if day_of_month_vix <= 15.0 {
        target_vix += 0.5;
    }

    new_state.vix = clamp(
        economy.vix
            + (target_vix - economy.vix) * inputs.vix_mean_reversion
            + random_normal(rng, 0.0, 0.15 * volatility),
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

    new_state
}
