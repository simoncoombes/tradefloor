//! A host's share counts (#274): `Engine::set_shares_outstanding`.
//!
//! A host whose companies buy back stock or issue it writes the counts, and
//! the engine weights by them from then on:
//!
//! - each market cap follows at once, and every tick after prices it off
//!   the new count;
//! - a bad write is refused and changes nothing;
//! - a snapshot carries the counts, and the state hash covers them, only
//!   once they have moved, so a restore continues bit for bit and an engine
//!   never told anything snapshots and hashes as it did.

use tradefloor::economy::{create_initial_central_bank_state, create_initial_economy_state};
use tradefloor::engine::{Engine, PriceField, SessionBuffer, SessionRequest};
use tradefloor::market::GameTime;
use tradefloor::params::ModelParams;
use tradefloor::snapshot::EngineSnapshot;

const TICKS: usize = 78;

fn engine_with(params: ModelParams) -> Engine {
    let companies = tradefloor::universe::random_universe(6, 5)
        .iter()
        .enumerate()
        .map(|(i, g)| g.to_init().to_tick_company(i))
        .collect();
    Engine::with_params(
        3,
        companies,
        create_initial_economy_state(&Default::default()),
        create_initial_central_bank_state(0),
        tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect(),
        params,
    )
}

fn engine() -> Engine {
    engine_with(ModelParams::preset("pt-v21").unwrap())
}

fn session(e: &mut Engine, dow: i64, hour: i64, minute: i64, ticks: usize) {
    let request = SessionRequest::new(GameTime::new(hour, minute, dow), ticks);
    e.run_session(&request, &mut SessionBuffer::new());
}

fn run(e: &mut Engine, days: std::ops::Range<i64>) {
    for day in days {
        e.open_market();
        session(e, day % 5, 9, 30, TICKS);
        e.close_day(day + 1);
    }
}

fn bits(values: &[f64]) -> Vec<u64> {
    values.iter().map(|v| v.to_bits()).collect()
}

/// Every count scaled: a buyback on the even names, an issue on the odd.
fn moved(e: &Engine) -> Vec<f64> {
    e.shares_outstanding()
        .iter()
        .enumerate()
        .map(|(i, s)| if i % 2 == 0 { s * 0.8 } else { s * 1.2 })
        .collect()
}

#[test]
fn the_market_cap_follows_the_count_at_once_and_at_every_tick_after() {
    let mut e = engine();
    run(&mut e, 0..2);
    let shares = moved(&e);
    e.set_shares_outstanding(&shares).unwrap();
    assert_eq!(bits(&e.shares_outstanding()), bits(&shares));
    let caps = e.column(PriceField::MarketCap);
    let prices = e.column(PriceField::Price);
    for i in 0..shares.len() {
        assert_eq!(caps[i], prices[i] * shares[i], "name {i}");
    }
    run(&mut e, 2..3);
    let caps = e.column(PriceField::MarketCap);
    let prices = e.column(PriceField::Price);
    for i in 0..shares.len() {
        assert_eq!(caps[i], prices[i] * shares[i], "name {i} after a day");
    }
}

#[test]
fn the_counts_move_the_market_and_consume_no_draws() {
    let mut a = engine();
    let mut b = engine();
    run(&mut a, 0..2);
    run(&mut b, 0..2);
    let shares = moved(&b);
    b.set_shares_outstanding(&shares).unwrap();
    assert_eq!(a.draws_consumed(), b.draws_consumed());
    run(&mut a, 2..6);
    run(&mut b, 2..6);
    assert_eq!(a.draws_consumed(), b.draws_consumed());
    assert_ne!(
        bits(&a.column(PriceField::Price)),
        bits(&b.column(PriceField::Price)),
        "moving every share count moved no price: the cap weights do not read it"
    );
}

#[test]
fn a_bad_write_is_refused_and_changes_nothing() {
    let mut e = engine();
    run(&mut e, 0..2);
    let n = e.len();
    let saved = e.snapshot().to_bytes();
    let good = e.shares_outstanding();
    let short = &good[..n - 1];
    assert!(e.set_shares_outstanding(short).unwrap_err().contains("values for"));
    for bad in [0.0, -1.0, f64::NAN, f64::INFINITY] {
        let mut shares = good.clone();
        shares[2] = bad;
        let err = e.set_shares_outstanding(&shares).unwrap_err();
        assert!(err.contains("shares_outstanding[2]"), "{err}");
        assert!(err.contains(&e.companies()[2].id), "{err}");
    }
    assert_eq!(e.snapshot().to_bytes(), saved);
    assert!(!e.shares_outstanding_changed());
}

#[test]
fn an_engine_never_told_carries_no_counts_and_hashes_as_before() {
    let mut e = engine();
    run(&mut e, 0..2);
    let hash = e.state_hash(2, false);
    assert!(!e.snapshot().fields().contains_key("shares_outstanding"));
    // The counts it was built with, written back, are no move.
    let same = e.shares_outstanding();
    e.set_shares_outstanding(&same).unwrap();
    assert!(!e.shares_outstanding_changed());
    assert!(!e.snapshot().fields().contains_key("shares_outstanding"));
    assert_eq!(e.state_hash(2, false), hash);
    let shares = moved(&e);
    e.set_shares_outstanding(&shares).unwrap();
    assert!(e.shares_outstanding_changed());
    assert!(e.snapshot().fields().contains_key("shares_outstanding"));
    assert_ne!(e.state_hash(2, false), hash);
}

#[test]
fn a_restore_carries_the_counts_and_continues_bit_for_bit() {
    let make = engine;
    let mut original = make();
    run(&mut original, 0..2);
    let shares = moved(&original);
    original.set_shares_outstanding(&shares).unwrap();
    run(&mut original, 2..3);
    let bytes = original.snapshot().to_bytes();
    let mut resumed = make();
    resumed.restore(&EngineSnapshot::from_bytes(&bytes).unwrap()).unwrap();
    assert_eq!(bits(&resumed.shares_outstanding()), bits(&shares));
    assert_eq!(resumed.snapshot().to_bytes(), bytes);
    run(&mut original, 3..6);
    run(&mut resumed, 3..6);
    assert_eq!(bits(&resumed.column(PriceField::Price)), bits(&original.column(PriceField::Price)));
    assert_eq!(resumed.state_hash(6, false), original.state_hash(6, false));
}

#[test]
fn a_snapshot_without_counts_puts_back_the_ones_the_engine_was_built_with() {
    let mut fresh = engine();
    run(&mut fresh, 0..2);
    let saved = fresh.snapshot();
    let built = fresh.shares_outstanding();
    let mut target = engine();
    run(&mut target, 0..2);
    let shares = moved(&target);
    target.set_shares_outstanding(&shares).unwrap();
    target.restore(&saved).unwrap();
    assert_eq!(bits(&target.shares_outstanding()), bits(&built));
    assert!(!target.shares_outstanding_changed());
}

#[test]
fn a_snapshot_whose_counts_are_the_wrong_width_is_refused() {
    let mut e = engine();
    run(&mut e, 0..2);
    let shares = moved(&e);
    e.set_shares_outstanding(&shares).unwrap();
    let mut s = e.snapshot();
    s.fields_mut().insert(
        "shares_outstanding",
        tradefloor::snapshot::SnapshotValue::from_f64s(&shares[..shares.len() - 1]),
    );
    let mut target = engine();
    run(&mut target, 0..2);
    let err = target.restore(&s).unwrap_err();
    assert!(err.message().contains("share counts"), "{err}");
}
