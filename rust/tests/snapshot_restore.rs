//! Saving an engine and putting it back, through the Rust API alone.
//!
//! A host that rebuilds an engine from its own saved columns, generator
//! position and economy loses state the engine holds and the host never
//! sees: the nominal-output base, the opening draws, the VIX's slow levels,
//! the crisis episode, the published-GDP lag and more. The resumed market
//! then drifts off the one it claims to continue. `Engine::snapshot` and
//! `Engine::restore` carry all of it, and these tests hold the contract:
//!
//! - run, snapshot, encode to bytes, decode into a fresh engine, restore, and
//!   continue: the result is the uninterrupted run, bit for bit, on the
//!   default preset and on older ones, at a close and in the middle of a
//!   session;
//! - a missing, unknown or malformed field is refused by name, and a refused
//!   restore changes nothing.

use tradefloor::economy::{create_initial_central_bank_state, create_initial_economy_state};
use tradefloor::engine::{Engine, PriceField, SessionBuffer, SessionRequest};
use tradefloor::market::GameTime;
use tradefloor::order_book::Side;
use tradefloor::params::ModelParams;
use tradefloor::snapshot::{EngineSnapshot, SnapshotErrorKind, SnapshotValue, STATE_SCHEMA};

const TICKS: usize = 78;

fn engine_with(params: ModelParams, seed: u64, n: usize, bonds: bool) -> Engine {
    let companies = tradefloor::universe::random_universe(n, 5)
        .iter()
        .enumerate()
        .map(|(i, g)| g.to_init().to_tick_company(i))
        .collect();
    let mut engine = Engine::with_params(
        seed,
        companies,
        create_initial_economy_state(&Default::default()),
        create_initial_central_bank_state(0),
        tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect(),
        params,
    );
    if bonds {
        engine.set_rate_instruments(
            tradefloor::rates::RATE_SPECS
                .iter()
                .map(|spec| tradefloor::rates::RateInstrument::new(*spec, 100.0, 1e6, 1e8))
                .collect(),
        );
    }
    engine
}

fn engine(preset: &str) -> Engine {
    engine_with(ModelParams::preset(preset).unwrap(), 3, 6, false)
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

/// Three closed days, then forty ticks into the fourth.
fn mid_day(e: &mut Engine) {
    run(e, 0..3);
    e.open_market();
    session(e, 3, 9, 30, 40);
}

/// The rest of the fourth day, then `more` days.
fn finish(e: &mut Engine, mid: bool, more: i64) {
    if mid {
        session(e, 3, 10, 10, TICKS - 40);
        e.close_day(4);
    }
    run(e, 4..4 + more);
}

fn bits(values: &[f64]) -> Vec<u64> {
    values.iter().map(|v| v.to_bits()).collect()
}

/// Snapshot, through bytes, into a fresh engine; then continue both.
fn resume_matches(make: impl Fn() -> Engine, mid: bool, label: &str) {
    let mut original = make();
    if mid {
        mid_day(&mut original);
    } else {
        run(&mut original, 0..4);
    }
    let saved = original.snapshot();
    assert_eq!(saved.schema(), Some(STATE_SCHEMA));
    let bytes = saved.to_bytes();

    let mut resumed = make();
    let decoded = EngineSnapshot::from_bytes(&bytes).unwrap();
    assert_eq!(decoded, saved, "{label}: the byte form lost something");
    resumed.restore(&decoded).unwrap_or_else(|e| panic!("{label}: {e}"));
    assert_eq!(resumed.snapshot().to_bytes(), bytes, "{label}: the restore is not the snapshot");

    finish(&mut original, mid, 3);
    finish(&mut resumed, mid, 3);
    assert_eq!(
        bits(&resumed.column(PriceField::Price)),
        bits(&original.column(PriceField::Price)),
        "{label}: the resumed run priced differently",
    );
    assert_eq!(
        resumed.snapshot().to_bytes(),
        original.snapshot().to_bytes(),
        "{label}: the resumed run holds different state",
    );
    assert_eq!(resumed.stream_positions(), original.stream_positions());
}

#[test]
fn a_resumed_run_is_the_uninterrupted_one_on_the_default_and_older_presets() {
    for preset in ["pt-v20", "pt-v19", "pt-v16", "pt-v3"] {
        for mid in [false, true] {
            let label = format!("{preset} {}", if mid { "mid-session" } else { "closed" });
            resume_matches(|| engine(preset), mid, &label);
        }
    }
}

/// A model that sets every dial a snapshot field is gated on, with rate
/// instruments, changed fundamentals and an agent's resting order.
fn every_dial() -> ModelParams {
    let mut p = ModelParams::preset("pt-v20").unwrap();
    for (name, value) in [
        ("garch_cascade_components", 3.0),
        ("cycle_publication_lag", 5.0),
        ("gdp_publication_lag", 21.0),
        ("unemployment_adjustment_half_life", 84.0),
        ("earnings_cycle_depth", 0.05),
        ("fair_value_vix_discount", 0.01),
        ("fair_value_vix_half_life", 10.0),
        ("qe_pe_stock_gain", 2.0),
    ] {
        p = p.with_override(name, value).unwrap();
    }
    p
}

fn busy() -> Engine {
    let mut e = engine_with(every_dial(), 3, 6, true);
    let (eps, book, growth) = e.fundamentals();
    let eps: Vec<f64> = eps.iter().map(|v| v * 1.1).collect();
    e.set_fundamentals(&eps, &book, &growth).unwrap();
    e
}

#[test]
fn a_model_with_every_gated_field_resumes_exactly() {
    for mid in [false, true] {
        resume_matches(
            busy,
            mid,
            if mid { "every dial mid-session" } else { "every dial closed" },
        );
    }
    // And with the agents' book in use: a resting order and a fill.
    let make = || {
        let mut e = busy();
        run(&mut e, 0..2);
        e.open_market();
        let first = e.companies()[0].ticker.clone();
        let second = e.companies()[1].ticker.clone();
        e.submit_order("fund", &first, Side::Buy, 100.0, Some(1.0), None).unwrap();
        e.submit_order("fund", &second, Side::Sell, 50.0, None, None).unwrap();
        session(&mut e, 2, 9, 30, 40);
        e
    };
    let saved = make().snapshot();
    for key in ["vix_anchor_slow", "fair_value_offset", "opening_z", "garch_cascade", "rates",
                "book", "fundamentals"] {
        assert!(saved.get(key).is_some(), "{key} not carried");
    }
    let Some(SnapshotValue::Map(economy)) = saved.get("economy") else { panic!() };
    for key in ["earnings_cycle", "vix_feedback", "qe_assets_ratio", "cycle_history",
                "unemployment_impulse", "gdp_publication"] {
        assert!(economy.contains_key(key), "economy.{key} not carried");
    }
    let mut original = make();
    let mut resumed = busy();
    resumed.restore(&EngineSnapshot::from_bytes(&saved.to_bytes()).unwrap()).unwrap();
    for e in [&mut original, &mut resumed] {
        session(e, 2, 10, 10, TICKS - 40);
        e.close_day(3);
        run(e, 3..5);
    }
    assert_eq!(resumed.snapshot().to_bytes(), original.snapshot().to_bytes());
}

#[test]
fn a_host_rebuild_without_the_snapshot_drifts_and_with_it_does_not() {
    // What a host could save before this existed: the columns, the
    // generators and the economy, onto an engine rebuilt from scratch.
    let mut original = engine("pt-v20");
    run(&mut original, 0..5);
    let mut rebuilt = engine("pt-v20");
    for field in tradefloor::engine::STATE_HASH_COLUMNS {
        rebuilt.set_column(field, &original.column(field)).unwrap();
    }
    rebuilt.set_rng_state(original.rng_state());
    *rebuilt.economy_mut() = original.economy().clone();
    let mut restored = engine("pt-v20");
    restored.restore(&original.snapshot()).unwrap();
    for e in [&mut original, &mut rebuilt, &mut restored] {
        run(e, 5..7);
    }
    let price = |e: &Engine| bits(&e.column(PriceField::Price));
    assert_ne!(price(&rebuilt), price(&original), "the partial rebuild no longer drifts");
    assert_eq!(price(&restored), price(&original));
}

#[test]
fn a_restore_hands_back_the_day_loop_it_was_given() {
    let mut original = engine("pt-v20");
    run(&mut original, 0..2);
    let mut day_loop = tradefloor::snapshot::DayLoop::new(2, false);
    day_loop.pending_jump = vec![0.25; 6];
    let saved = original.snapshot_with(&day_loop);
    assert_eq!(saved.day_count(), Some(2));
    assert_eq!(saved.market_open(), Some(false));
    let mut resumed = engine("pt-v20");
    assert_eq!(resumed.restore(&saved).unwrap(), day_loop);
    assert_eq!(resumed.snapshot_with(&day_loop), saved);
}

// ---------------------------------------------------------------------------
// Refusals
// ---------------------------------------------------------------------------

/// Restore `snapshot` onto a closed engine and return the refusal, checking
/// it changed nothing.
fn refused(preset: &str, snapshot: &EngineSnapshot) -> tradefloor::snapshot::SnapshotError {
    let mut target = engine(preset);
    run(&mut target, 0..2);
    let before = target.snapshot().to_bytes();
    let err = target.restore(snapshot).expect_err("the restore was accepted");
    assert_eq!(target.snapshot().to_bytes(), before, "a refused restore changed the engine");
    err
}

fn taken(preset: &str) -> EngineSnapshot {
    let mut e = engine(preset);
    mid_day(&mut e);
    e.snapshot()
}

fn edit(preset: &str, path: &[&str], value: Option<SnapshotValue>) -> EngineSnapshot {
    let mut s = taken(preset);
    let mut map = s.fields_mut();
    for key in &path[..path.len() - 1] {
        let Some(SnapshotValue::Map(inner)) = map.get_mut(key) else { panic!("{key}") };
        map = inner;
    }
    let last = path[path.len() - 1];
    match value {
        Some(v) => {
            map.insert(last, v);
        }
        None => {
            map.remove(last).expect("the field to remove");
        }
    }
    s
}

#[test]
fn a_missing_field_is_refused_by_name_at_every_level() {
    let keys: Vec<String> = taken("pt-v20").fields().keys().map(str::to_string).collect();
    // Less the fields whose absence is a value.
    let optional = ["state_schema", "book", "vix_sets_variance_pending", "macro_pins_today",
                    "pending_fair_value", "current_day", "elapsed_days", "fundamentals"];
    for key in keys.iter().filter(|k| !optional.contains(&k.as_str())) {
        let err = refused("pt-v20", &edit("pt-v20", &[key], None));
        assert_eq!(err.kind(), SnapshotErrorKind::Fields, "{key}: {err}");
        assert!(err.message().contains(key.as_str()), "{key}: {err}");
    }
    for path in [&["economy", "vix"][..], &["economy", "cycle_phase"], &["economy", "gdp_trend"],
                 &["central_bank", "hawkish_dovish_score"], &["columns", "price"]] {
        let err = refused("pt-v20", &edit("pt-v20", path, None));
        assert!(err.message().contains(path[1]), "{path:?}: {err}");
    }
}

#[test]
fn an_unknown_field_is_refused_by_name_at_every_level() {
    for path in [&["something_new"][..], &["economy", "something_new"],
                 &["central_bank", "something_new"], &["columns", "something_new"]] {
        let err = refused("pt-v20", &edit("pt-v20", path, Some(SnapshotValue::Float(1.0))));
        assert_eq!(err.kind(), SnapshotErrorKind::Fields, "{path:?}: {err}");
        assert!(err.message().contains("something_new"), "{path:?}: {err}");
    }
}

#[test]
fn a_dial_gated_field_follows_the_model() {
    let mut s = taken("pt-v20");
    s.fields_mut().remove("fair_value_offset");
    s.fields_mut().remove("opening_z");
    let err = refused("pt-v20", &s);
    assert!(err.message().contains("fair_value_offset") && err.message().contains("fair_value_news_share"),
            "{err}");
    let err = refused("pt-v3", &edit("pt-v3", &["vix_anchor_slow"], Some(SnapshotValue::Float(0.0))));
    assert!(err.message().contains("vix_anchor_memory"), "{err}");
}

#[test]
fn a_malformed_value_is_refused_by_name() {
    use SnapshotValue as V;
    let s = taken("pt-v20");
    let short_rng = match s.get("rng") {
        Some(V::List(v)) => V::List(v[..27].to_vec()),
        _ => panic!(),
    };
    let short_price = V::from_f64s(&s.column(PriceField::Price).unwrap()[..5]);
    let cases: Vec<(&[&str], V, &str)> = vec![
        (&["economy", "gdp"], V::Str("high".into()), "economy.gdp"),
        (&["economy", "vix"], V::Float(f64::NAN), "economy.vix"),
        (&["economy", "oil_last_opec_day"], V::Float(1.5), "economy.oil_last_opec_day"),
        (&["economy", "gdp_trend"], V::List(vec![V::Float(1.0); 3]), "gdp_trend"),
        (&["economy", "cycle_phase"], V::Str("boom".into()), "cycle phase"),
        (&["central_bank", "forward_guidance"], V::Str("loud".into()), "forward guidance"),
        (&["volume_state"], V::Float(f64::INFINITY), "volume_state"),
        (&["market_open"], V::Str("yes".into()), "market_open"),
        (&["day_count"], V::Int(-1), "day_count"),
        (&["rng"], short_rng, "rng"),
        (&["draw_overlay"], V::List(vec![V::Tuple(vec![V::Int(99), V::Int(0), V::Int(1), V::Float(0.5)])]),
         "draw_overlay"),
        (&["attribution"], V::Bytes(vec![0; 8]), "attribution"),
        (&["noise_parts"], V::Bytes(vec![0; 9]), "noise_parts"),
        (&["volume_idio"], V::List(vec![V::Float(1.0)]), "volume_idio"),
        (&["columns", "price"], short_price, "price"),
        (&["session_news"], V::List(vec![V::Int(1)]), "session_news"),
        (&["crisis_epicentre_pin"], V::Str("none".into()), "crisis_epicentre_pin"),
        (&["session_tick"], V::Int(-1), "session_tick"),
    ];
    for (path, value, words) in cases {
        let err = refused("pt-v20", &edit("pt-v20", path, Some(value)));
        assert!(err.message().contains(words), "{path:?}: {err}");
    }
}

#[test]
fn a_version_this_build_does_not_read_is_refused_and_none_is_version_one() {
    for (value, words) in [(SnapshotValue::Int(2), "newer"), (SnapshotValue::Int(0), "versions run"),
                           (SnapshotValue::Str("1".into()), "integer"),
                           (SnapshotValue::Bool(true), "integer")] {
        let err = refused("pt-v20", &edit("pt-v20", &["state_schema"], Some(value)));
        assert_eq!(err.kind(), SnapshotErrorKind::Version);
        assert!(err.message().contains(words), "{err}");
    }
    // Unversioned, as 0.8.5 to 0.8.8 wrote it: read as version 1.
    let unversioned = edit("pt-v20", &["state_schema"], None);
    let mut e = engine("pt-v20");
    e.restore(&unversioned).unwrap();
    // And one also missing a field carries the reason.
    let mut older = unversioned.clone();
    older.fields_mut().remove("session_tick");
    let err = refused("pt-v20", &older);
    assert!(err.message().contains("0.8.5") && err.message().contains("session_tick"), "{err}");
}

#[test]
fn another_roster_or_model_is_refused() {
    let s = taken("pt-v20");
    let mut other = engine_with(ModelParams::preset("pt-v20").unwrap(), 3, 5, false);
    let err = other.restore(&s).unwrap_err();
    assert_eq!(err.kind(), SnapshotErrorKind::Roster, "{err}");
    let custom = ModelParams::preset("pt-v20").unwrap().with_override("market_factor_sigma", 0.007).unwrap();
    let mut other = engine_with(custom, 3, 6, false);
    let err = other.restore(&s).unwrap_err();
    assert_eq!(err.kind(), SnapshotErrorKind::Model, "{err}");
}
