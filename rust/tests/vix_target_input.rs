//! Issue #275: a host's input to the VIX target, a premium that fades on its
//! own half-life and a floor held until cleared.
//!
//! - With nothing written the engine is the one it was, bit for bit, and its
//!   snapshot and state hash carry nothing new.
//! - A premium lifts the VIX for as long as it stands, where a one-off write
//!   of the same size is reverted within days; a floor holds the VIX up and
//!   lets it go when cleared.
//! - A snapshot taken mid-fade, with a floor set, restores bit for bit, and
//!   a malformed one is refused.
//! - The forecast reads the premium fading and the floor held.

use tradefloor::economy::{create_initial_central_bank_state, create_initial_economy_state};
use tradefloor::engine::{Engine, PriceField, SessionBuffer, SessionRequest};
use tradefloor::market::GameTime;
use tradefloor::params::ModelParams;
use tradefloor::snapshot::{EngineSnapshot, SnapshotErrorKind, SnapshotValue};

const TICKS: usize = 78;

fn engine_with(params: ModelParams) -> Engine {
    let companies = tradefloor::universe::random_universe(8, 5)
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

/// Days `days`, each opened, run and closed; the VIX after each close.
fn run(e: &mut Engine, days: std::ops::Range<i64>) -> Vec<f64> {
    let mut vix = Vec::new();
    for day in days {
        e.open_market();
        session(e, day % 5, 9, 30, TICKS);
        e.close_day(day + 1);
        vix.push(e.economy().vix);
    }
    vix
}

fn mean(xs: &[f64]) -> f64 {
    xs.iter().sum::<f64>() / xs.len() as f64
}

fn bits(xs: &[f64]) -> Vec<u64> {
    xs.iter().map(|x| x.to_bits()).collect()
}

#[test]
fn with_nothing_written_the_engine_is_the_one_it_was() {
    let mut plain = engine();
    let mut touched = engine();
    // Writing the defaults is writing nothing.
    touched.set_vix_target_premium(0.0, 0.0).unwrap();
    touched.set_vix_target_floor(None).unwrap();
    let a = run(&mut plain, 0..12);
    let b = run(&mut touched, 0..12);
    assert_eq!(bits(&a), bits(&b));
    assert_eq!(bits(&plain.column(PriceField::Price)), bits(&touched.column(PriceField::Price)));
    assert_eq!(plain.state_hash(12, false), touched.state_hash(12, false));
    let s = touched.snapshot();
    assert!(!s.fields().contains_key("vix_target_premium"));
    assert!(!s.fields().contains_key("vix_target_floor"));
    assert_eq!(plain.snapshot().to_bytes(), s.to_bytes());
}

#[test]
fn a_premium_lifts_the_vix_while_it_stands_and_a_write_does_not() {
    let mut base = engine();
    let mut premium = engine();
    let mut write = engine();
    for e in [&mut base, &mut premium, &mut write] {
        run(e, 0..3);
    }
    premium.set_vix_target_premium(6.0, 13.0).unwrap();
    write.economy_mut().vix += 6.0;
    let b = run(&mut base, 3..43);
    let p = run(&mut premium, 3..43);
    let w = run(&mut write, 3..43);
    let lift_premium = mean(&p) - mean(&b);
    let lift_write = mean(&w) - mean(&b);
    assert!(lift_premium > 2.0, "premium lifts the VIX {lift_premium} points on average");
    assert!(lift_premium > 2.0 * lift_write, "premium {lift_premium} against write {lift_write}");
    // Ten sessions on, the write is mostly gone and the premium is not.
    assert!(p[10] - b[10] > 2.0 * (w[10] - b[10]), "{} {} {}", p[10], w[10], b[10]);
    // Faded on its half-life: forty closes at thirteen sessions.
    let (points, half_life) = premium.vix_target_premium();
    assert_eq!(half_life, 13.0);
    assert!((points - 6.0 * 0.5f64.powf(40.0 / 13.0)).abs() < 1e-9, "{points}");
}

#[test]
fn a_held_premium_does_not_fade_and_a_faded_one_clears() {
    let mut e = engine();
    e.set_vix_target_premium(2.5, 0.0).unwrap();
    run(&mut e, 0..5);
    assert_eq!(e.vix_target_premium(), (2.5, 0.0));
    e.set_vix_target_premium(1e-3, 1.0).unwrap();
    run(&mut e, 5..25);
    assert_eq!(e.vix_target_premium(), (0.0, 0.0));
    assert!(!e.snapshot().fields().contains_key("vix_target_premium"));
}

#[test]
fn a_floor_holds_the_vix_up_and_lets_it_go_when_cleared() {
    let mut base = engine();
    let mut floored = engine();
    for e in [&mut base, &mut floored] {
        run(e, 0..3);
    }
    floored.set_vix_target_floor(Some(30.0)).unwrap();
    let b = run(&mut base, 3..33);
    let f = run(&mut floored, 3..33);
    assert!(mean(&f[15..]) > 27.0, "floored VIX {}", mean(&f[15..]));
    assert!(mean(&b[15..]) < 25.0, "unfloored VIX {}", mean(&b[15..]));
    floored.set_vix_target_floor(None).unwrap();
    assert_eq!(floored.vix_target_floor(), None);
    let after = run(&mut floored, 33..73);
    assert!(mean(&after[25..]) < 25.0, "released VIX {}", mean(&after[25..]));
}

#[test]
fn the_setters_refuse_what_is_not_a_premium_or_a_floor() {
    let mut e = engine();
    for (points, half_life) in [(f64::NAN, 10.0), (f64::INFINITY, 10.0), (3.0, -1.0),
                                (3.0, f64::NAN), (3.0, f64::INFINITY)] {
        assert!(e.set_vix_target_premium(points, half_life).is_err(), "{points} {half_life}");
    }
    for floor in [0.0, -5.0, f64::NAN, f64::INFINITY] {
        assert!(e.set_vix_target_floor(Some(floor)).is_err(), "{floor}");
    }
    // A refused write changes nothing.
    assert_eq!(e.vix_target_premium(), (0.0, 0.0));
    assert_eq!(e.vix_target_floor(), None);
}

fn primed() -> Engine {
    let mut e = engine();
    run(&mut e, 0..3);
    e.set_vix_target_premium(4.0, 13.0).unwrap();
    e.set_vix_target_floor(Some(22.0)).unwrap();
    run(&mut e, 3..6);
    e.open_market();
    session(&mut e, 1, 9, 30, 40);
    e
}

#[test]
fn a_snapshot_mid_fade_with_a_floor_restores_bit_for_bit() {
    let mut original = primed();
    let saved = original.snapshot();
    assert!(saved.fields().contains_key("vix_target_premium"));
    assert!(saved.fields().contains_key("vix_target_floor"));
    let bytes = saved.to_bytes();
    let mut resumed = engine();
    resumed.restore(&EngineSnapshot::from_bytes(&bytes).unwrap()).unwrap();
    assert_eq!(resumed.snapshot().to_bytes(), bytes);
    assert_eq!(resumed.vix_target_premium(), original.vix_target_premium());
    assert_eq!(resumed.vix_target_floor(), original.vix_target_floor());
    for e in [&mut original, &mut resumed] {
        session(e, 1, 10, 10, TICKS - 40);
        e.close_day(7);
    }
    let a = run(&mut original, 7..20);
    let b = run(&mut resumed, 7..20);
    assert_eq!(bits(&a), bits(&b));
    assert_eq!(bits(&original.column(PriceField::Price)), bits(&resumed.column(PriceField::Price)));
    assert_eq!(original.snapshot().to_bytes(), resumed.snapshot().to_bytes());
}

#[test]
fn the_state_hash_covers_the_input_only_while_it_stands() {
    let mut a = engine();
    let mut b = engine();
    run(&mut a, 0..2);
    run(&mut b, 0..2);
    b.set_vix_target_premium(3.0, 10.0).unwrap();
    assert_ne!(a.state_hash(2, false), b.state_hash(2, false));
    b.set_vix_target_premium(0.0, 0.0).unwrap();
    assert_eq!(a.state_hash(2, false), b.state_hash(2, false));
    b.set_vix_target_floor(Some(20.0)).unwrap();
    assert_ne!(a.state_hash(2, false), b.state_hash(2, false));
    b.set_vix_target_floor(None).unwrap();
    assert_eq!(a.state_hash(2, false), b.state_hash(2, false));
}

#[test]
fn a_restore_without_the_input_clears_it_and_a_malformed_one_is_refused() {
    let plain = {
        let mut e = engine();
        run(&mut e, 0..3);
        e.snapshot()
    };
    let mut target = engine();
    target.set_vix_target_premium(5.0, 10.0).unwrap();
    target.set_vix_target_floor(Some(25.0)).unwrap();
    target.restore(&plain).unwrap();
    assert_eq!(target.vix_target_premium(), (0.0, 0.0));
    assert_eq!(target.vix_target_floor(), None);

    let good = primed().snapshot();
    let refused = |s: &EngineSnapshot, name: &str, kind: SnapshotErrorKind| {
        let mut t = engine();
        run(&mut t, 0..2);
        let before = t.snapshot().to_bytes();
        let err = t.restore(s).expect_err("accepted");
        assert_eq!(err.kind(), kind, "{err}");
        assert!(err.message().contains(name), "{err}");
        assert_eq!(t.snapshot().to_bytes(), before, "a refused restore changed the engine");
    };
    let mut s = good.clone();
    s.fields_mut().insert("vix_target_floor", SnapshotValue::Float(-1.0));
    refused(&s, "vix_target_floor", SnapshotErrorKind::Value);
    let mut s = good.clone();
    if let Some(SnapshotValue::Map(block)) = s.fields_mut().get_mut("vix_target_premium") {
        block.insert("points", SnapshotValue::Float(0.0));
    }
    refused(&s, "vix_target_premium", SnapshotErrorKind::Value);
    let mut s = good.clone();
    if let Some(SnapshotValue::Map(block)) = s.fields_mut().get_mut("vix_target_premium") {
        block.remove("half_life");
    }
    refused(&s, "half_life", SnapshotErrorKind::Fields);
}

#[test]
fn the_forecast_reads_the_premium_fading_and_the_floor_held() {
    let p = ModelParams::preset("pt-v21")
        .unwrap()
        .with_override("index_level_listed", 1.0)
        .unwrap()
        .with_override("forecast_horizon_sessions", 63.0)
        .unwrap();
    let mut base = engine_with(p.clone());
    let mut premium = engine_with(p.clone());
    let mut floored = engine_with(p);
    for e in [&mut base, &mut premium, &mut floored] {
        run(e, 0..3);
    }
    let b = base.forecast().unwrap().vix.clone();
    premium.set_vix_target_premium(6.0, 5.0).unwrap();
    floored.set_vix_target_floor(Some(30.0)).unwrap();
    // A write re-reads the forecast at once, as a pin does.
    let p = premium.forecast().unwrap().vix.clone();
    let f = floored.forecast().unwrap().vix.clone();
    let lift: Vec<f64> = p.iter().zip(&b).map(|(x, y)| x - y).collect();
    assert!(lift[3] > 0.5, "{lift:?}");
    // Fading at five sessions, the lift is all but gone by the horizon.
    assert!(lift[62].abs() < 0.25 * lift[3], "{} {}", lift[62], lift[3]);
    assert!(f[62] > 27.0 && f[62] > b[62] + 5.0, "{} {}", f[62], b[62]);
    // And it is the forecast the next close would compute: a copy that
    // refreshes reads the same.
    let mut copy = premium.clone();
    run(&mut copy, 3..4);
    run(&mut premium, 3..4);
    assert_eq!(bits(&copy.forecast().unwrap().vix), bits(&premium.forecast().unwrap().vix));
}
