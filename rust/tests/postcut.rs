//! The stress hold and the priced policy path at a meeting
//! (`fed_stress_hold`, `treasury_path_pricing`).
//!
//! These hold the meeting's arithmetic on a hand-built economy: inside the
//! hold a rise the ladder chose is held, and the put gives nothing back,
//! while inflation is under target plus the stress gap, and a cut stands;
//! above the gap the rise goes through; with the path priced, a cut moves
//! the 10-year by the rate change plus the forecast's move on the day, and
//! the 2-year reads the forecast; with every new field at its shipped value
//! the meeting is the shipped one, draws included.

use tradefloor::economy::*;
use tradefloor::rng::GameRng;

/// Inflation and unemployment at the bank's targets and the policy rate at
/// the Taylor rate, so the ladder holds, and the 10-year on its target.
fn calm_economy() -> EconomyState {
    let mut e = create_initial_economy_state(&InitialEconomyOptions {
        cycle_phase: Some(CyclePhase::Expansion),
        inflation_rate: Some(2.0),
        gdp_growth: Some(2.5),
        unemployment_rate: Some(4.0),
    });
    e.federal_funds_rate = 2.0;
    e.treasury_yield_10y = 3.0;
    e.vix = 18.0;
    e
}

/// Inflation at 3.2 and the rate at 1.0: the Taylor rate is 2.9, so the
/// ladder hikes a quarter point.
fn hiking_economy() -> EconomyState {
    let mut e = calm_economy();
    e.inflation_rate = 3.2;
    e.federal_funds_rate = 1.0;
    e
}

fn meet(e: &EconomyState, options: &PolicyOptions) -> (MeetingOutcome, u64) {
    let cb = create_initial_central_bank_state(0);
    let mut rng = GameRng::new(7, 3);
    let out = update_central_bank_with(&cb, e, cb.next_meeting_date, &mut rng, options);
    (out, rng.next_f64().to_bits())
}

fn hold(gap: f64) -> PolicyOptions {
    PolicyOptions { stress_hold: true, stress_inflation_gap: gap, ..PolicyOptions::shipped() }
}

#[test]
fn the_ladder_hikes_the_hiking_economy() {
    let (out, _) = meet(&hiking_economy(), &PolicyOptions::shipped());
    assert_eq!(out.decision, Some(Decision::Hike));
    assert_eq!(out.economy.federal_funds_rate, 1.25);
}

#[test]
fn inside_the_hold_a_rise_is_held_under_the_inflation_gate() {
    let e = hiking_economy();
    let (shipped, draw) = meet(&e, &PolicyOptions::shipped());
    let (held, held_draw) = meet(&e, &hold(2.0));
    assert_eq!(held.decision, Some(Decision::Hold));
    assert_eq!(held.economy.federal_funds_rate, 1.0);
    // The dovish score does not move, and the draws are the meeting's.
    assert_eq!(held.central_bank.hawkish_dovish_score,
               create_initial_central_bank_state(0).hawkish_dovish_score);
    assert_eq!(held_draw, draw);
    assert_eq!(held.announcement_variant, shipped.announcement_variant);
    // Inflation of 3.2 is over target plus a gap of 1: the rise stands.
    let (through, _) = meet(&e, &hold(1.0));
    assert_eq!(through.decision, Some(Decision::Hike));
    assert_eq!(through.economy.federal_funds_rate, 1.25);
}

#[test]
fn inside_the_hold_a_cut_stands() {
    let mut e = calm_economy();
    e.intermeeting_return = (0.9f64).ln();
    let put = PolicyOptions { put_gain: 5.0, ..PolicyOptions::shipped() };
    let (free, _) = meet(&e, &put);
    let (held, _) = meet(&e, &PolicyOptions { stress_hold: true, stress_inflation_gap: 2.0, ..put });
    assert_eq!(free.economy.federal_funds_rate, 1.5);
    assert_eq!(held.economy.federal_funds_rate, 1.5);
    assert_eq!(held.decision, Some(Decision::Cut));
}

#[test]
fn inside_the_hold_the_put_gives_nothing_back() {
    let mut e = calm_economy();
    e.fed_put_owed = 0.5;
    e.fed_put = 0.0;
    e.federal_funds_rate = 1.5;
    let put = PolicyOptions { put_gain: 5.0, ..PolicyOptions::shipped() };
    let (free, _) = meet(&e, &put);
    assert_eq!(free.economy.federal_funds_rate, 1.75);
    assert_eq!(free.economy.fed_put_owed, 0.25);
    let (held, _) = meet(&e, &PolicyOptions { stress_hold: true, stress_inflation_gap: 2.0, ..put });
    assert_eq!(held.economy.federal_funds_rate, 1.5);
    assert_eq!(held.economy.fed_put_owed, 0.5);
}

#[test]
fn a_priced_path_moves_the_ten_year_by_the_forecast_on_the_day() {
    let e = calm_economy();
    // A quarter-point stress cut on a calm ladder.
    let stress = PolicyOptions { stress_cut: 0.25, stress_vix: 30.0, stress_level: 35.0,
                                 ..PolicyOptions::shipped() };
    let (plain, draw) = meet(&e, &stress);
    assert_eq!(plain.economy.federal_funds_rate, 1.75);
    // Unpriced: the target falls a quarter and the surprise is a quarter,
    // so the 10-year falls a quarter.
    assert!((plain.economy.treasury_yield_10y - 2.75).abs() < 1e-12);
    // Priced one for one from a flat forecast: the forecast moves -0.25 with
    // the cut, so the target and the surprise each carry -0.5.
    let (priced, priced_draw) = meet(&e, &PolicyOptions { path_gain: 1.0, path_before: 0.0, ..stress });
    assert_eq!(priced.economy.federal_funds_rate, 1.75);
    assert!((priced.economy.treasury_yield_10y - 2.5).abs() < 1e-12);
    let two = (1.75 - 0.25) * 0.85 + priced.economy.treasury_yield_10y * 0.15;
    assert!((priced.economy.treasury_yield_2y - two).abs() < 1e-12);
    // The corporate yield is re-anchored on the 10-year it priced.
    assert!(priced.economy.corporate_bond_yield < plain.economy.corporate_bond_yield);
    assert_eq!(priced_draw, draw);
    // A forecast already standing is in the target but not in the surprise.
    let (standing, _) = meet(&e, &PolicyOptions { path_gain: 1.0, path_before: -0.25, ..stress });
    let target = 1.75 + 1.0 - 0.5;
    let expected = 3.0 + (target - 3.0) * 0.5 + (-0.25 - 0.25) * 0.5;
    assert!((standing.economy.treasury_yield_10y - expected).abs() < 1e-12);
}

#[test]
fn with_the_gain_off_the_forecast_is_unread() {
    let e = calm_economy();
    let stress = PolicyOptions { stress_cut: 0.25, stress_vix: 30.0, stress_level: 35.0,
                                 ..PolicyOptions::shipped() };
    let (plain, draw) = meet(&e, &stress);
    let (unread, unread_draw) = meet(&e, &PolicyOptions { path_before: -0.7, ..stress });
    assert_eq!(unread, plain);
    assert_eq!(unread_draw, draw);
}

#[test]
fn shipped_options_carry_neither() {
    let s = PolicyOptions::shipped();
    assert!(!s.stress_hold);
    assert_eq!(s.path_gain, 0.0);
    assert_eq!(s.path_before, 0.0);
}
