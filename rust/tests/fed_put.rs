//! The Fed put and the Treasury haven at a meeting (`fed_put_gain`,
//! `fed_put_threshold`, `treasury_put_pricing`, `treasury_haven_gain`).
//!
//! These hold the meeting's arithmetic on a hand-built economy, one rule at
//! a time: a 10 per cent intermeeting fall asks for a half-point cut with
//! inflation under 4 and for nothing at 4 or more; a stressed VIX holds a
//! hike the ladder would take; what the put owes is given back a quarter
//! point a calm meeting until nothing is owed; a fully priced put moves the
//! 10-year by no surprise on the day; the haven lowers the meeting's
//! 10-year target by its gain per VIX point above 20; and with every dial
//! at 0.0 the meeting is the shipped one, draws included.

use tradefloor::economy::*;
use tradefloor::rng::{GameRng, Rng};

/// Inflation and unemployment at the bank's targets and the policy rate at
/// the Taylor rate, so the ladder holds: every rate change below is the
/// put's.
fn calm_economy() -> EconomyState {
    let mut opening = InitialEconomyOptions::default();
    opening.cycle_phase = Some(CyclePhase::Expansion);
    opening.inflation_rate = Some(2.0);
    opening.gdp_growth = Some(2.5);
    opening.unemployment_rate = Some(4.0);
    let mut e = create_initial_economy_state(&opening);
    e.federal_funds_rate = 2.0;
    e.treasury_yield_10y = 3.0;
    e.vix = 18.0;
    e
}

fn put(gain: f64) -> PolicyOptions {
    let mut o = PolicyOptions::shipped();
    o.put_gain = gain;
    o
}

fn meet(cb: &CentralBankState, e: &EconomyState, options: &PolicyOptions) -> MeetingOutcome {
    let mut rng = GameRng::new(7, 3);
    update_central_bank_with(cb, e, cb.next_meeting_date, &mut rng, options)
}

fn bank() -> CentralBankState {
    create_initial_central_bank_state(0)
}

#[test]
fn the_ladder_holds_on_the_calm_economy() {
    let out = meet(&bank(), &calm_economy(), &PolicyOptions::shipped());
    assert_eq!(out.decision, Some(Decision::Hold));
    assert_eq!(out.economy.federal_funds_rate, 2.0);
}

#[test]
fn a_ten_per_cent_fall_asks_for_half_a_point_under_inflation_of_four() {
    let mut e = calm_economy();
    e.intermeeting_return = (0.9f64).ln();
    let out = meet(&bank(), &e, &put(5.0));
    assert_eq!(out.decision, Some(Decision::Cut));
    assert_eq!(out.economy.federal_funds_rate, 1.5);
    // Owed and in the stock; the intermeeting return restarts.
    assert_eq!(out.economy.fed_put_owed, 0.5);
    assert_eq!(out.economy.fed_put, 0.5);
    assert_eq!(out.economy.intermeeting_return, 0.0);
    // A threshold of 5 per cent leaves 0.0554 of log fall: 0.28 rounds to
    // a quarter.
    let with_threshold = meet(&bank(), &e, &{ let mut o = put(5.0); o.put_threshold = 0.05; o });
    assert_eq!(with_threshold.economy.federal_funds_rate, 1.75);
}

#[test]
fn no_put_at_inflation_of_four_or_more() {
    let mut e = calm_economy();
    e.intermeeting_return = (0.9f64).ln();
    e.inflation_rate = 4.0;
    let off = meet(&bank(), &e, &PolicyOptions::shipped());
    let on = meet(&bank(), &e, &put(5.0));
    assert_eq!(on.economy.federal_funds_rate, off.economy.federal_funds_rate);
    assert_eq!(on.decision, off.decision);
    assert_eq!(on.economy.fed_put_owed, 0.0);
}

#[test]
fn the_cut_is_no_more_than_the_rate() {
    let mut e = calm_economy();
    e.federal_funds_rate = 0.3;
    e.intermeeting_return = -0.4;
    let out = meet(&bank(), &e, &put(5.0));
    assert_eq!(out.economy.federal_funds_rate, 0.0);
}

#[test]
fn a_stressed_vix_holds_a_hike_the_ladder_would_take() {
    // Inflation a point and a half over target and the rate over a point
    // under the rule: the ladder hikes a quarter.
    let mut e = calm_economy();
    e.inflation_rate = 3.5;
    let calm = meet(&bank(), &e, &put(5.0));
    assert_eq!(calm.decision, Some(Decision::Hike));
    assert_eq!(calm.economy.federal_funds_rate, 2.25);
    e.vix = 30.0;
    let off = meet(&bank(), &e, &PolicyOptions::shipped());
    assert_eq!(off.economy.federal_funds_rate, 2.25);
    let on = meet(&bank(), &e, &put(5.0));
    assert_eq!(on.decision, Some(Decision::Hold));
    assert_eq!(on.economy.federal_funds_rate, 2.0);
    // The held hike is owed, so the calm meetings take it later.
    assert_eq!(on.economy.fed_put_owed, 0.25);
}

#[test]
fn the_calm_meetings_give_back_what_is_owed() {
    let mut e = calm_economy();
    e.fed_put_owed = 1.0;
    e.fed_put = 0.0;
    let mut cb = bank();
    let options = put(5.0);
    let mut path = Vec::new();
    for _ in 0..6 {
        let out = meet(&cb, &e, &options);
        cb = out.central_bank;
        e = out.economy;
        path.push((e.federal_funds_rate, e.fed_put_owed));
    }
    assert_eq!(path[0], (2.25, 0.75));
    assert_eq!(path[3], (3.0, 0.0));
    // Nothing owed, nothing more given back.
    assert_eq!(path[5], (3.0, 0.0));
}

#[test]
fn nothing_is_given_back_while_the_stock_is_high_or_the_vix_stressed() {
    let mut e = calm_economy();
    e.fed_put_owed = 1.0;
    e.fed_put = 0.95;
    let out = meet(&bank(), &e, &put(5.0));
    assert_eq!(out.economy.federal_funds_rate, 2.0);
    assert_eq!(out.economy.fed_put_owed, 1.0);
    e.fed_put = 0.0;
    e.vix = 31.0;
    let out = meet(&bank(), &e, &put(5.0));
    assert_eq!(out.economy.federal_funds_rate, 2.0);
}

#[test]
fn a_priced_put_is_no_surprise_to_the_ten_year() {
    // Log fall of exactly 0.1: the put asks for 0.5, which it delivers.
    let mut e = calm_economy();
    e.intermeeting_return = -0.1;
    let target = 1.5 + 1.0;
    let priced = meet(&bank(), &e, &{ let mut o = put(5.0); o.put_pricing = 1.0; o });
    assert_eq!(priced.economy.federal_funds_rate, 1.5);
    let want = 3.0 + (target - 3.0) * 0.5;
    assert!((priced.economy.treasury_yield_10y - want).abs() < 1e-12);
    // Unpriced, the cut is news: half of it on the day.
    let unpriced = meet(&bank(), &e, &put(5.0));
    let want = 3.0 + (target - 3.0) * 0.5 - 0.25;
    assert!((unpriced.economy.treasury_yield_10y - want).abs() < 1e-12);
}

#[test]
fn the_haven_lowers_the_meetings_ten_year_target() {
    let mut e = calm_economy();
    e.vix = 50.0;
    let off = meet(&bank(), &e, &PolicyOptions::shipped());
    let on = meet(&bank(), &e, &{ let mut o = PolicyOptions::shipped(); o.haven_gain = 0.02; o });
    // Half of the target's 0.6 lower on the day.
    let gap = off.economy.treasury_yield_10y - on.economy.treasury_yield_10y;
    assert!((gap - 0.3).abs() < 1e-12, "gap {gap}");
    // Not with inflation at 4 or more.
    e.inflation_rate = 4.5;
    let off = meet(&bank(), &e, &PolicyOptions::shipped());
    let on = meet(&bank(), &e, &{ let mut o = PolicyOptions::shipped(); o.haven_gain = 0.02; o });
    assert_eq!(off.economy.treasury_yield_10y, on.economy.treasury_yield_10y);
}

/// Counts the draws a meeting takes, to hold the module's draw table.
struct Counting(GameRng, usize);

impl Rng for Counting {
    fn next_f64(&mut self) -> f64 {
        self.1 += 1;
        self.0.next_f64()
    }
    fn next_normal(&mut self) -> f64 {
        self.1 += 1;
        self.0.next_normal()
    }
}

#[test]
fn the_put_takes_no_draw() {
    let mut e = calm_economy();
    e.intermeeting_return = -0.2;
    e.vix = 45.0;
    let cb = bank();
    let mut counts = Vec::new();
    for options in [PolicyOptions::shipped(),
                    { let mut o = put(5.0); o.put_pricing = 1.0; o.haven_gain = 0.02; o }] {
        let mut rng = Counting(GameRng::new(7, 3), 0);
        update_central_bank_with(&cb, &e, cb.next_meeting_date, &mut rng, &options);
        counts.push(rng.1);
    }
    assert_eq!(counts[0], counts[1]);
}
