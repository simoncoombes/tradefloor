//! INVARIANTS 6.1–6.8, as property tests.
//!
//! These are deliberately NOT parity tests. The vectors already hold the port
//! to the TypeScript bit for bit; what these assert is that the MODEL is
//! right — that Okun's law has the sign economics gives it, that the Fed cuts
//! into a collapse. A port can be perfectly faithful to a broken model, and
//! these are the tests that would notice.

#![cfg(test)]

use super::*;
use crate::rng::Rng;

/// A generator with no noise. Every normal here has mean zero, so silencing
/// them leaves the deterministic channel — which is what a property test
/// about economics should be looking at.
struct Silent(f64);
impl Rng for Silent {
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

/// A month-start day, where the monthly channels fire.
fn month_step(e: &EconomyState, volatility: f64) -> EconomyState {
    update_economy_daily(
        e,
        &DailyInputs {
            volatility,
            game_day: DAYS_PER_MONTH,
            ..Default::default()
        },
        &mut Silent(0.5),
    )
}

fn meeting(e: &EconomyState, cb: &CentralBankState) -> MeetingOutcome {
    let mut cb = *cb;
    cb.next_meeting_date = -1;
    update_central_bank(&cb, e, 1000, &mut Silent(0.5))
}

// ── 6.1 Taylor rule ───────────────────────────────────────────────────────

#[test]
fn the_taylor_rule_reads_every_one_of_its_inputs() {
    // The point of decision D1. Each input is moved on its own and must change
    // the outcome; a rule blind to one of them passes a test that only ever
    // moves inflation.
    let base = economy();
    let cb = create_initial_central_bank_state(0);
    let rate = |e: &EconomyState, cb: &CentralBankState| meeting(e, cb).economy.federal_funds_rate;

    let baseline = rate(&base, &cb);

    let mut hot = base.clone();
    hot.inflation_rate = 6.0;
    assert!(
        rate(&hot, &cb) > baseline,
        "high inflation must raise the rate"
    );

    let mut slack = base.clone();
    slack.unemployment_rate = 9.0;
    slack.inflation_rate = 2.5;
    assert!(
        rate(&slack, &cb) < baseline,
        "a slack labour market must lower the rate — a Taylor rule blind to \
         unemployment is exactly what D1 rejected"
    );

    // `hawkish_dovish_score` is real state that the deployed WASM formula
    // never read. The term shifts the rate by at most 0.1, so the test has to
    // straddle a decision threshold for it to be observable at all — the same
    // reason the parity sweep needed a fine `federalFundsRate` grid.
    let mut near = base.clone();
    near.inflation_rate = 3.2;
    near.unemployment_rate = 4.5;
    near.federal_funds_rate = 2.15;
    let mut dovish = cb;
    dovish.hawkish_dovish_score = -1.0;
    let mut hawkish = cb;
    hawkish.hawkish_dovish_score = 1.0;
    assert!(
        rate(&near, &hawkish) > rate(&near, &dovish),
        "policy momentum must move the decision when rateDiff sits on a threshold"
    );
}

#[test]
fn the_fed_cuts_into_a_collapse_whatever_inflation_is_doing() {
    // The dual mandate: employment wins when the economy is collapsing. A
    // pure inflation targeter would hike here.
    let mut e = economy();
    e.gdp_growth = -3.0;
    e.unemployment_rate = 11.0;
    e.inflation_rate = 5.0;
    e.federal_funds_rate = 4.0;
    let cb = create_initial_central_bank_state(0);

    let out = meeting(&e, &cb);
    assert!(
        out.economy.federal_funds_rate < e.federal_funds_rate,
        "deep recession must cut, got {} from {}",
        out.economy.federal_funds_rate,
        e.federal_funds_rate
    );
    assert_eq!(out.decision, Some(Decision::EmergencyCut));
}

// ── 6.2 Phillips curve ────────────────────────────────────────────────────

#[test]
fn slack_above_nairu_lowers_inflation_and_tightness_raises_it() {
    let mut slack = economy();
    slack.unemployment_rate = 9.0;
    slack.structural_unemployment = 4.5;
    let mut tight = economy();
    tight.unemployment_rate = 2.8;
    tight.structural_unemployment = 4.5;

    let slack_out = month_step(&slack, 0.0);
    let tight_out = month_step(&tight, 0.0);
    assert!(
        slack_out.inflation_rate < tight_out.inflation_rate,
        "a slack labour market must be less inflationary: {} vs {}",
        slack_out.inflation_rate,
        tight_out.inflation_rate
    );
}

// ── 6.3 Okun's law ────────────────────────────────────────────────────────

#[test]
fn gdp_below_trend_raises_unemployment() {
    let mut weak = economy();
    weak.gdp_growth = -2.0;
    let mut strong = economy();
    strong.gdp_growth = 4.0;

    assert!(
        month_step(&weak, 0.0).unemployment_rate > month_step(&strong, 0.0).unemployment_rate,
        "weak growth must raise unemployment relative to strong growth"
    );
}

// ── 6.4 Prime rate ────────────────────────────────────────────────────────

#[test]
fn prime_is_always_exactly_three_points_above_the_policy_rate() {
    for fed in [0.0, 0.1, 2.5, 5.5, 8.0] {
        let mut e = economy();
        e.federal_funds_rate = fed;
        e.inflation_rate = 2.0;
        let out = meeting(&e, &create_initial_central_bank_state(0)).economy;
        assert_eq!(out.prime_rate, out.federal_funds_rate + 3.0);
    }
    let e = economy();
    assert_eq!(e.prime_rate, e.federal_funds_rate + 3.0);
}

// ── 6.5 Spreads ───────────────────────────────────────────────────────────

#[test]
fn mortgage_and_corporate_spreads_stay_positive_and_bounded() {
    for vix in [10.0, 18.0, 30.0, 55.0, 80.0] {
        for phase in [
            CyclePhase::Expansion,
            CyclePhase::Peak,
            CyclePhase::Contraction,
            CyclePhase::Trough,
            CyclePhase::Recovery,
        ] {
            let mut e = economy();
            e.vix = vix;
            e.cycle_phase = phase;
            let out = meeting(&e, &create_initial_central_bank_state(0)).economy;

            let m_spread = out.mortgage_rate_30y - out.treasury_yield_10y;
            assert!(
                (0.5 - 1e-12..=3.5 + 1e-12).contains(&m_spread),
                "mortgage spread {m_spread} outside [0.5, 3.5] at vix {vix} in {phase:?}"
            );

            let c_spread = out.corporate_bond_yield - out.treasury_yield_10y;
            assert!(
                c_spread >= 0.8 - 1e-12,
                "corporate spread {c_spread} below its floor at vix {vix} in {phase:?}"
            );
        }
    }
}

#[test]
fn credit_spreads_widen_in_a_downturn() {
    // The cycle multipliers exist so credit stress is a phase property, not
    // only a VIX property.
    let spread_in = |phase| {
        let mut e = economy();
        e.cycle_phase = phase;
        e.vix = 25.0;
        let out = meeting(&e, &create_initial_central_bank_state(0)).economy;
        out.corporate_bond_yield - out.treasury_yield_10y
    };
    assert!(spread_in(CyclePhase::Trough) > spread_in(CyclePhase::Contraction));
    assert!(spread_in(CyclePhase::Contraction) > spread_in(CyclePhase::Expansion));
}

// ── 6.7 / 6.8 Clamps ──────────────────────────────────────────────────────

#[test]
fn the_bounded_series_stay_bounded_under_sustained_stress() {
    // Driven with an extreme generator rather than a silent one, so the noise
    // terms push hard against every clamp for 400 consecutive days.
    struct Extreme(f64);
    impl Rng for Extreme {
        fn next_f64(&mut self) -> f64 {
            self.0
        }
        fn next_normal(&mut self) -> f64 {
            self.0 * 40.0
        }
    }

    for sign in [1.0f64, -1.0] {
        let mut e = economy();
        let mut cb = create_initial_central_bank_state(0);
        for day in 0..400i64 {
            e = update_economy_daily(
                &e,
                &DailyInputs {
                    volatility: 0.9,
                    market_return_pct: sign * 5.0,
                    game_day: day,
                    ..Default::default()
                },
                &mut Extreme(sign),
            );
            e = check_cycle_transition(&e, &mut Extreme(if sign > 0.0 { 0.0 } else { 0.99 }));
            let out = update_central_bank(&cb, &e, day * 24 * 60, &mut Extreme(sign));
            cb = out.central_bank;
            e = out.economy;

            for (label, value, lo, hi) in [
                ("unemployment", e.unemployment_rate, 2.5, 15.0),
                ("fed rate", e.federal_funds_rate, 0.0, 8.0),
                ("vix", e.vix, 10.0, 80.0),
                ("10Y", e.treasury_yield_10y, 0.5, 12.0),
                ("inflation", e.inflation_rate, -1.0, 6.0),
                ("fear/greed", e.fear_greed_index, 0.0, 100.0),
                ("recession prob", e.recession_probability, 0.05, 0.95),
            ] {
                assert!(
                    (lo..=hi).contains(&value),
                    "day {day} (sign {sign}): {label} {value} escaped [{lo}, {hi}]"
                );
            }
        }
    }
}

#[test]
fn qe_engages_only_at_the_zero_bound_in_a_contraction() {
    let mut e = economy();
    e.federal_funds_rate = 0.1;
    e.cycle_phase = CyclePhase::Contraction;
    e.inflation_rate = 0.5;
    e.unemployment_rate = 9.0;
    let cb = create_initial_central_bank_state(0);

    let out = meeting(&e, &cb);
    assert!(
        out.central_bank.qe_active,
        "QE must engage at the zero bound in contraction"
    );
    assert!(
        out.economy.qe_pe_boost > 0.0,
        "QE must feed the PE boost market.ts reads"
    );

    // Not in an expansion, however low the rate.
    let mut calm = e.clone();
    calm.cycle_phase = CyclePhase::Expansion;
    let out = meeting(&calm, &cb);
    assert!(!out.central_bank.qe_active);
    assert_eq!(out.economy.qe_pe_boost, 0.0);
}

// ── Cycle ─────────────────────────────────────────────────────────────────

#[test]
fn a_phase_cannot_end_before_its_minimum_duration() {
    for phase in [
        CyclePhase::Expansion,
        CyclePhase::Peak,
        CyclePhase::Contraction,
        CyclePhase::Trough,
        CyclePhase::Recovery,
    ] {
        let mut e = economy();
        e.cycle_phase = phase;
        e.months_in_current_phase = phase_characteristics(phase).min_months - 0.001;
        let (p, _) = get_cycle_transition_probability(&e);
        assert_eq!(p, 0.0, "{phase:?} offered a transition before its minimum");
        // A generator that always fires must still not move it.
        assert_eq!(
            check_cycle_transition(&e, &mut Silent(0.0)).cycle_phase,
            phase
        );
    }
}

#[test]
fn the_daily_transition_hazard_is_capped_at_thirty_percent() {
    for phase in [
        CyclePhase::Expansion,
        CyclePhase::Peak,
        CyclePhase::Contraction,
        CyclePhase::Trough,
        CyclePhase::Recovery,
    ] {
        let mut e = economy();
        e.cycle_phase = phase;
        e.months_in_current_phase = 600.0;
        e.inflation_rate = 9.0;
        e.federal_funds_rate = 8.0;
        e.treasury_yield_2y = 9.0;
        e.treasury_yield_10y = 1.0;
        e.market_pe = Some(60.0);
        let (p, _) = get_cycle_transition_probability(&e);
        assert!(
            (0.0..=0.3).contains(&p),
            "{phase:?} hazard {p} escaped [0, 0.3]"
        );
    }
}

#[test]
fn phases_advance_in_the_documented_order() {
    for (from, to) in [
        (CyclePhase::Expansion, CyclePhase::Peak),
        (CyclePhase::Peak, CyclePhase::Contraction),
        (CyclePhase::Contraction, CyclePhase::Trough),
        (CyclePhase::Trough, CyclePhase::Recovery),
        (CyclePhase::Recovery, CyclePhase::Expansion),
    ] {
        assert_eq!(phase_characteristics(from).next_phase, to);
    }
}

#[test]
fn the_weibull_hazard_changes_character_across_shape_one() {
    // shape > 1: an ageing phase grows fragile. shape < 1: early exits
    // dominate and late ones linger. Both regimes are configured, so both are
    // asserted — this is the property the shape parameter exists for.
    let rising = |m| weibull_hazard(m, 1.8, 36.0);
    assert!(
        rising(60.0) > rising(12.0),
        "shape 1.8 must rise with duration"
    );

    let falling = |m| weibull_hazard(m, 0.7, 12.0);
    assert!(
        falling(24.0) < falling(2.0),
        "shape 0.7 must fall with duration"
    );

    // And the cap holds however long the phase runs.
    for (shape, scale) in [
        (1.8, 36.0),
        (2.0, 6.0),
        (0.7, 12.0),
        (1.5, 4.0),
        (1.3, 12.0),
    ] {
        assert!(weibull_hazard(1e6, shape, scale) <= 0.8);
        assert_eq!(weibull_hazard(0.0, shape, scale), 0.0);
        assert_eq!(weibull_hazard(-5.0, shape, scale), 0.0);
    }
}
