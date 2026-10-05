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

/// R20M's vector on pt-v20 (the arm the pt-v21 screen graded) with the
/// prehistory cut to five sessions, plus the dials R20M leaves off whose
/// state a snapshot carries (the day's t scale, the withheld earnings
/// cycle): every dial-gated key of the later mechanisms is in play.
fn every_r21_dial() -> ModelParams {
    let mut p = ModelParams::preset("pt-v20").unwrap();
    for (name, value) in [
        ("cycle_equity_hazard_opening", 0.007), ("cycle_equity_hazard", 5.0),
        ("cycle_equity_hazard_knee", 0.1), ("cycle_nowcast_accuracy", 0.4),
        ("corporate_spread_cycle", 0.75), ("fed_growth_cut", 2.0),
        ("cycle_publication_lag_draw", 1.0), ("earnings_anticipation_drift_half_life", 252.0),
        ("earnings_anticipation_drift_share", 0.9), ("buyback_accrual", 1.0),
        ("rate_close_remark", 1.0), ("rate_intraday_live", 1.0), ("fed_stress_cut", 0.1),
        ("fed_stress_inflation_gap", 2.0), ("macro_pins_hold", 1.0),
        ("market_vol_vix_coupling", 0.75), ("flight_to_quality_gain", 0.013),
        ("buyback_payout_share", 0.9), ("dividend_payout_share", 1.2),
        ("dividend_buyback_substitution", 1.0), ("impact_memory_coefficient", 0.65),
        ("impact_memory_half_life", 12.0), ("impact_memory_slow_half_life", 780.0),
        ("impact_memory_slow_weight", 0.1), ("impact_memory_crossover", 0.001),
        ("fill_impact_coefficient", 0.15), ("book_arrival_shuffle", 1.0),
        ("overnight_market_share", 0.6), ("overnight_idio_share", 0.25),
        ("overnight_idio_df", 4.0), ("earnings_surprise_sigma", 3.5),
        ("earnings_session_sigma", 1.9), ("earnings_followthrough_sigma", 1.1),
        ("earnings_volume_multiple", 1.2), ("jump_intensity_idio", 0.009),
        ("jump_sigma_idio", 0.0318), ("idio_sigma_scale", 0.52), ("idio_vol_alpha", 0.25),
        ("idio_vol_beta", 0.5), ("idio_vol_jump_bump", 1.0), ("market_vol_slow_gamma", 0.05),
        ("vix_stress_premium", 3.0), ("vix_stress_premium_knee", 0.6),
        ("vix_stress_premium_cap", 0.35), ("fed_put_gain", 3.0), ("fed_put_half_life", 126.0),
        ("treasury_put_pricing", 1.0), ("treasury_haven_gain", 0.014),
        ("fed_stress_hold", 42.0), ("treasury_path_pricing", 1.0),
        ("treasury_path_half_life", 63.0), ("treasury_policy_damping", 0.5),
        ("corporate_spread_vix_cut", 1.0), ("corporate_spread_equity_gain", 1.8),
        ("corporate_spread_equity_half_life", 126.0), ("pinned_vix_feedback", 0.8),
        ("pinned_vix_variance_share", 0.7), ("market_vol_cycle_ratio", 2.4705882352941178),
        ("market_vol_cycle_expansion", 0.82), ("market_vol_cycle_half_life", 10.0),
        ("market_vol_cycle_relative", 0.75), ("market_vol_cycle_cap_relative", 1.0),
        ("market_vol_cycle_pin_neutral", 1.0), ("market_vol_cycle_pin_phase", 1.0),
        ("market_vol_leverage", 2.5), ("market_vol_leverage_half_life", 15.0),
        ("market_vol_leverage_standardise", 1.0), ("vix_level_sigma", 0.009),
        ("market_vol_vix_smooth", 3.0), ("market_vol_gamma", 0.06),
        ("market_vol_beta", 0.9446), ("price_hard_cap", 1000000000.0),
        ("book_depth_nesting", 1.0), ("fair_value_market_excess_share", 0.5),
        ("fair_value_vix_release_half_life", 504.0), ("book_cross_at_limit", 1.0),
        ("impact_memory_refill", 1.0), ("pinned_vix_calm_knee", 17.6),
        ("pinned_vix_calm_share", 0.2), ("pinned_vix_priced_cap", 1.0),
        ("policy_anticipation", 2.0), ("fair_value_relative_knee", 4.0),
        ("fair_value_relative_half_life", 63.0), ("market_beta_normalise", 1.0),
        ("market_factor_sigma", 0.007099478), ("market_prehistory_sessions", 5.0),
        ("market_prehistory_valuation", 1.0), ("fed_put_carry", 1.0),
        ("fed_put_emergency_vix", 50.0), ("fed_drawdown_hold", 0.12),
        ("market_vol_cycle_recovery_release", 0.45), ("market_vol_cycle_recovery_scale", 0.1),
        ("market_day_tail_df", 7.0), ("market_day_tail_state_share", 1.0),
        ("earnings_cycle_depth", 0.05), ("earnings_cycle_report_share", 0.5),
    ] {
        p = p.with_override(name, value).unwrap();
    }
    p.invariants().unwrap();
    p
}

/// The r21 mechanisms' state, through the byte form, at a close and in the
/// middle of a session: on an engine that holds rate instruments and a
/// used book, the resumed run is the uninterrupted one.
#[test]
fn a_model_with_every_r21_dial_resumes_exactly_through_bytes() {
    let make = || engine_with(every_r21_dial(), 3, 6, true);
    for mid in [false, true] {
        resume_matches(make, mid, if mid { "r21 mid-session" } else { "r21 closed" });
    }
    let saved = {
        let mut e = make();
        mid_day(&mut e);
        e.snapshot()
    };
    for key in ["market_vol_leverage_memory", "vix_stress_memory", "cycle_nowcast_rng",
                "fed_stress_vix_max", "fed_stress_hold_age", "treasury_policy_path",
                "fed_drawdown_returns", "fed_drawdown_mcap_prev", "policy_anticipation_priced",
                "buyback_log_shares", "opening_carry", "dividend", "earnings_key",
                "earnings_withheld", "idio_variance", "idio_jump_pending",
                "idio_jump_var_pending", "night_market_factor", "rate_live_marks",
                "market_day_scale", "market_vol_cycle_log"] {
        assert!(saved.get(key).is_some(), "{key} not carried");
    }
    let Some(SnapshotValue::Map(economy)) = saved.get("economy") else { panic!() };
    for key in ["cycle_nowcast", "cycle_publication", "anticipation_drift", "anticipation_raw",
                "intermeeting_return", "fed_put", "fed_put_owed", "fed_put_mcap_prev",
                "spread_equity_gap", "earnings_cycle"] {
        assert!(economy.contains_key(key), "economy.{key} not carried");
    }
    // With the agents' book in use under the meta-order memory: fills
    // between the cut and the close read what the memory held at the cut.
    let make_busy = || {
        let mut e = make();
        run(&mut e, 0..2);
        e.open_market();
        let first = e.companies()[0].ticker.clone();
        let second = e.companies()[1].ticker.clone();
        e.submit_order("fund", &first, Side::Buy, 2_000.0, None, None).unwrap();
        e.submit_order("fund", &second, Side::Sell, 1_500.0, None, None).unwrap();
        e.submit_order("fund", &second, Side::Buy, 100.0, Some(1.0), None).unwrap();
        session(&mut e, 2, 9, 30, 40);
        e
    };
    let saved = make_busy().snapshot();
    assert!(saved.get("book").is_some());
    let mut original = make_busy();
    let mut resumed = make();
    resumed.restore(&EngineSnapshot::from_bytes(&saved.to_bytes()).unwrap()).unwrap();
    for e in [&mut original, &mut resumed] {
        let first = e.companies()[0].ticker.clone();
        e.submit_order("fund", &first, Side::Buy, 2_000.0, None, None).unwrap();
        session(e, 2, 10, 10, TICKS - 40);
        e.close_day(3);
        run(e, 3..6);
    }
    assert_eq!(bits(&resumed.column(PriceField::Price)), bits(&original.column(PriceField::Price)));
    assert_eq!(resumed.snapshot().to_bytes(), original.snapshot().to_bytes());
}

/// A 64-bit key above `i64::MAX` travels as `UInt` (tag 0x0a) and comes
/// back the same; the same key at or below it is an `Int`.
#[test]
fn a_u64_key_survives_the_byte_form_and_the_restore() {
    // Some seed's earnings key is above i64::MAX: half of them are.
    let (seed, saved) = (3..40)
        .map(|seed| {
            let mut e = engine_with(every_r21_dial(), seed, 6, true);
            run(&mut e, 0..1);
            (seed, e.snapshot())
        })
        .find(|(_, s)| matches!(s.get("earnings_key"), Some(SnapshotValue::UInt(_))))
        .expect("no seed in 3..40 draws an earnings key above i64::MAX");
    let bytes = saved.to_bytes();
    let decoded = EngineSnapshot::from_bytes(&bytes).unwrap();
    assert_eq!(decoded.get("earnings_key"), saved.get("earnings_key"));
    let mut resumed = engine_with(every_r21_dial(), seed + 100, 6, true);
    resumed.restore(&decoded).unwrap();
    assert_eq!(resumed.snapshot().to_bytes(), bytes);
    assert_eq!(SnapshotValue::from_u64(u64::MAX), SnapshotValue::UInt(u64::MAX));
    assert_eq!(SnapshotValue::from_u64(i64::MAX as u64), SnapshotValue::Int(i64::MAX));
    // A negative key is no key: refused by name, not wrapped.
    let mut bad = decoded.clone();
    bad.fields_mut().insert("earnings_key", SnapshotValue::Int(-1));
    let err = engine_with(every_r21_dial(), seed, 6, true).restore(&bad).unwrap_err();
    assert!(err.message().contains("earnings_key"), "{err}");
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
