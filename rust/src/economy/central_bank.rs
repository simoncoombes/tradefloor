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

/// The mortgage spread's floor, on the same footing.
///
/// Structurally identical to the corporate one, and it survived on margin
/// alone: this spread runs 1.5 to 2.8, so daily drift never reached 0.5. That
/// is luck rather than a guarantee, so it is floored too.
pub const MORTGAGE_SPREAD_FLOOR: f64 = 0.5;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
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

/// Run a scheduled (or emergency) FOMC-style meeting.
pub fn update_central_bank(
    central_bank: &CentralBankState,
    economy: &EconomyState,
    current_timestamp: i64,
    rng: &mut impl Rng,
) -> MeetingOutcome {
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
    }

    // Urgency amplifies HIKES only. Amplifying cuts by an inflation gap would
    // be backwards: during a recession the Fed cuts on employment.
    if rate_change > 0.0 {
        rate_change *= urgency;
    }

    // DRAW SITE — always. The announcement text is not built here, but the
    // draw that chooses it is taken at exactly this point.
    let announcement_variant = (rng.next_f64() * ANNOUNCEMENT_VARIANTS).floor() as usize;

    // ── Apply the rate change ─────────────────────────────────────────────
    // 8% ceiling: fed funds peaked at 5.5% (2023) and 6.5% (2006); only
    // Volcker went past 20%.
    new_economy.federal_funds_rate = clamp(current_rate + rate_change, 0.0, 8.0);
    new_economy.prime_rate = new_economy.federal_funds_rate + 3.0;

    let treasury_target_10y = new_economy.federal_funds_rate
        + 1.0
        + mathx::max(0.0, (economy.inflation_rate - 2.0) * 0.3);
    // Half the gap closes on announcement day; daily mean-reversion does the
    // rest.
    new_economy.treasury_yield_10y = clamp(
        economy.treasury_yield_10y
            + (treasury_target_10y - economy.treasury_yield_10y) * 0.50
            + rate_change * 0.5,
        0.5,
        12.0,
    );
    new_economy.treasury_yield_2y =
        new_economy.federal_funds_rate * 0.85 + new_economy.treasury_yield_10y * 0.15;

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
    let base_corporate_spread = 1.0 + (economy.vix - 12.0) * 0.02;
    let cycle_spread_multiplier = match economy.cycle_phase {
        CyclePhase::Contraction => 2.8,
        CyclePhase::Trough => 3.5,
        CyclePhase::Recovery => 1.4,
        CyclePhase::Peak => 1.1,
        CyclePhase::Expansion => 1.0,
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
    new_cb.next_meeting_date = if inflation_crisis {
        // Crisis cadence: 21-30 days.
        current_timestamp + (21 + (rng.next_f64() * 10.0).floor() as i64) * 24 * 60
    } else {
        // Normal cadence: 6-8 weeks.
        current_timestamp + (42 + (rng.next_f64() * 14.0).floor() as i64) * 24 * 60
    };

    MeetingOutcome {
        central_bank: new_cb,
        economy: new_economy,
        decision: Some(decision),
        announcement_variant: Some(announcement_variant),
    }
}
