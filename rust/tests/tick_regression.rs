//! Regression gates for the tick paths whose reference parity was retired.
//!
//! # What this file is, and is not
//!
//! The 2026-08-21 retirement (D-P1 in the design log) took the external
//! oracle away from the open-session tick and the thirty-day interleaving:
//! `MARKET_FACTOR_SIGMA` moved 0.003 → 0.0075 on modelling grounds, so no
//! recorded reference tape can gate those paths any more. Losing the oracle
//! is not a reason to lose the tests — but nothing here can replace what the
//! oracle proved, and this header exists to stop anyone reading it as if it
//! did.
//!
//! **These tests gate REGRESSION, not correctness.** Every expectation below
//! is derived from this crate's own documented contracts or from running the
//! crate against itself. `sync-goldens.py`'s one-way rule exists precisely
//! because a self-anchored test passes forever regardless of whether the
//! model is right; that criticism applies to this file in full, is accepted,
//! and is the cost D-P1 records. What these gates catch is the model
//! CHANGING when nobody meant it to — a reordered draw, a lost
//! `close_market` step, nondeterminism from an unseeded source. What they
//! cannot catch is the model being consistently wrong. Correctness on the
//! forked paths is now argued by the realism panel and the calibration
//! sweeps (`tools/calibration/`), not by any test in `rust/tests/`.
//!
//! Two properties are pinned, chosen because they are exactly what the
//! retired tapes were load-bearing for and they hold REGARDLESS of
//! calibration — recalibrating a sigma or adding an asymmetry term must not
//! break them, so they will survive the in-flight engine streams (GJR in
//! `garch.rs`, the market-factor variance process in `tick.rs`, the crisis
//! trigger in `economy/`) unless one of those changes the draw schedule or
//! breaks reproducibility, which is precisely when they SHOULD fail:
//!
//! 1. **The draw schedule is a pure function of (session, roster shape).**
//!    Under `SettleDrawPolicy::FourAlways` — the shipped era's schedule — no
//!    price, macro value or order flow can move a draw. The retired tapes
//!    enforced this against the reference's recording; this enforces it
//!    against the crate's own documented arithmetic.
//!    `stream_alignment.rs` covers the settlement guard specifically; this
//!    covers the whole tick's count and KIND sequence.
//!
//! 2. **One seed reproduces one market, through the full interleaving.**
//!    Daily → open → ticks → close, several times over, twice from the same
//!    seed, compared bit-for-bit. The thirty-day tapes verified this
//!    interleaving against the reference; this verifies it against itself,
//!    which still catches the failure that matters day-to-day — a step
//!    reordered or dropped on one path but not the other, or any draw from
//!    an unseeded source.

use tradefloor::economy::{
    create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions,
};
use tradefloor::engine::{DayAdvanceRequest, DayCloseRequest, Engine, PriceField, TickRequest};
use tradefloor::market::{
    simulate_market_tick, AvgVolumePolicy, GameTime, MarketStatus, SettleDrawPolicy, TickCompany,
    TickInputs, TickStock, MARKET_FACTOR_SIGMA,
};
use tradefloor::rng::Rng;

// ── Fixtures ──────────────────────────────────────────────────────────────

fn company(id: &str, price: f64) -> TickCompany {
    TickCompany {
        id: id.to_string(),
        ticker: id.to_string(),
        sector: "technology".to_string(),
        is_bankrupt: false,
        is_public: true,
        stock: TickStock {
            price,
            previous_close: price,
            previous_tick_price: None,
            open: price,
            high: price,
            low: price,
            volume: 0.0,
            avg_volume: 1e6,
            shares_outstanding: 1e8,
            market_cap: price * 1e8,
            mispricing_s: None,
            mispricing_s_prev_close: None,
            mispricing_momentum: None,
            maker_inventory: None,
            garch_variance: 0.000625,
            garch_cascade: [0.015 * 0.015; tradefloor::market::garch::CASCADE_MAX],
            last_daily_return: None,
            beta: Some(1.0),
            short_interest: 0.0,
            float: 1e8,
        },
        sector_volatility: Some(1.2),
        sector_avg_pe: Some(32.0),
        eps: Some(4.0),
        book_value_per_share: Some(20.0),
        revenue_growth: Some(0.1),
    }
}

fn sector_keys() -> Vec<String> {
    tradefloor::sectors::keys()
        .iter()
        .map(|s| s.to_string())
        .collect()
}

// ── 1. Draw schedule ──────────────────────────────────────────────────────

/// Records the KIND of every draw, so the assertion covers order as well as
/// count — the same discipline the retired tapes enforced. Values are
/// deliberately unremarkable (0.5 uniforms, 0.0 normals): this gate is about
/// the schedule, and feeding it adversarial values would only entangle it
/// with the arithmetic it deliberately does not check.
struct KindRecorder(String);

impl Rng for KindRecorder {
    fn next_f64(&mut self) -> f64 {
        self.0.push('u');
        0.5
    }
    fn next_normal(&mut self) -> f64 {
        self.0.push('n');
        0.0
    }
}

/// The documented per-tick schedule under `FourAlways`, as a kind string:
/// one market normal, one normal per sector, then per active company one
/// normal and one stashed uniform, then — open sessions only — four
/// settlement uniforms per active company, unconditionally.
fn expected_schedule(status: MarketStatus, sectors: usize, active: usize) -> String {
    if status == MarketStatus::Closed {
        return String::new();
    }
    let mut s = String::from("n");
    s.push_str(&"n".repeat(sectors));
    s.push_str(&"nu".repeat(active));
    if status == MarketStatus::Open {
        s.push_str(&"uuuu".repeat(active));
    }
    s
}

fn run_tick(companies: &mut [TickCompany], status: MarketStatus, vix: f64) -> String {
    let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
    economy.vix = vix;
    let keys = sector_keys();
    let mut rng = KindRecorder(String::new());
    simulate_market_tick(
        companies,
        &TickInputs {
            // The mechanism ships inert; 0.0 is the value that
            // preserves the behaviour these tests pin. The lagged
            // asymmetry branches on this flag and its gain defaults
            // to 0.0, so false is bit-identical here.
            prev_day_down: false,
            forced_flow_eff: 1.0,
            universe_stress: 0.0,
            volume_state: 0.0,
            volume_idio: &[],
            economy: &economy,
            market_status: status,
            intraday_t: 0.5,
            volatility_multiplier: 1.0,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
            sector_keys: &keys,
            // The constant-sigma baseline: this harness probes the draw
            // schedule, which must not depend on the factor's conditional
            // sigma at all.
            market_sigma_daily: MARKET_FACTOR_SIGMA,
            settle_draws: SettleDrawPolicy::FourAlways,
            // The depth counterfactual, off. It reaches no company field.
            settle_depth_counterfactual: false,
                // The run's opening nominal output. The growth term is
                // off on every preset these tests pin, so it is read
                // nowhere; this tick's own value is what a single-tick
                // caller opens at.
                nominal_output_base: economy.gdp * economy.cpi,
                params: &tradefloor::params::PT_V1,
        },
        &mut rng,
    );
    rng.0
}

#[test]
fn the_draw_schedule_is_a_pure_function_of_session_and_roster_shape() {
    let sectors = sector_keys().len();

    for status in [
        MarketStatus::Open,
        MarketStatus::PreMarket,
        MarketStatus::AfterHours,
        MarketStatus::Closed,
    ] {
        // A quiet market and a stressed one: prices two orders of magnitude
        // apart, VIX above every crisis threshold, a heavily shorted name
        // deep in cascade territory, a bankrupt and a private name to prove
        // the ACTIVE set (not the roster) is what the schedule counts.
        let mut calm: Vec<TickCompany> = (0..6).map(|i| company(&format!("C{i}"), 100.0)).collect();

        let mut stressed: Vec<TickCompany> =
            (0..6).map(|i| company(&format!("C{i}"), 10_000.0)).collect();
        stressed[0].stock.short_interest = 3e7;
        stressed[0].stock.last_daily_return = Some(-0.08);
        stressed[1].stock.avg_volume = 0.0;
        stressed[2].stock.garch_variance = 0.0;

        let mut with_inactive = calm.clone();
        with_inactive[4].is_bankrupt = true;
        with_inactive[5].is_public = false;

        let calm_tape = run_tick(&mut calm, status, 12.0);
        let stressed_tape = run_tick(&mut stressed, status, 80.0);
        let inactive_tape = run_tick(&mut with_inactive, status, 12.0);

        let want = expected_schedule(status, sectors, 6);
        assert_eq!(
            calm_tape, want,
            "{status:?}: the schedule left the documented arithmetic"
        );
        assert_eq!(
            stressed_tape, calm_tape,
            "{status:?}: VIX, prices, short interest or volume state moved a draw — \
             the FourAlways schedule must be blind to all of them"
        );
        assert_eq!(
            inactive_tape,
            expected_schedule(status, sectors, 4),
            "{status:?}: bankrupt and private names must drop out of the schedule \
             exactly, not approximately"
        );
    }
}

// ── 2. Interleaved reproducibility ────────────────────────────────────────

/// One full day in `runTick`'s order: daily macro step, open reset, a
/// session of ticks, close. The shipped policies throughout — this replays
/// nothing and holds nothing to the reference.
fn run_day(engine: &mut Engine, day: i64, ticks: i64) {
    let roster = engine.len();
    engine.advance_day(&DayAdvanceRequest {
        volatility: 1.0,
        active_shocks: &[],
        market_return_pct: 0.0,
        game_day: day,
        timestamp: day * 24 * 60,
    });
    engine.open_market();
    for t in 0..ticks {
        engine.tick(&TickRequest {
            time: GameTime {
                hour: 9 + (30 + t) / 60,
                minute: (30 + t) % 60,
                day_of_week: 3,
            },
            volatility_multiplier: 1.0,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
        });
    }
    engine.close_market(&DayCloseRequest {
        daily_innovations: &vec![None; roster],
        sector_base_variances: &vec![0.000625; roster],
        avg_volume: AvgVolumePolicy::Hold,
    });
}

fn engine(seed: u32) -> Engine {
    let companies = (0..8)
        .map(|i| company(&format!("C{i}"), 80.0 + 10.0 * i as f64))
        .collect();
    Engine::new(
        seed,
        companies,
        create_initial_economy_state(&InitialEconomyOptions::default()),
        create_initial_central_bank_state(0),
        sector_keys(),
    )
}

/// Bit-projected column, so the comparison is exact and NaN-safe: two NaNs
/// of the same bits compare equal here where `==` on floats would not, and
/// a NaN appearing on one path only is a hard failure rather than a puzzle.
fn bits_of(engine: &Engine, field: PriceField) -> Vec<u64> {
    engine
        .column(field)
        .into_iter()
        .map(f64::to_bits)
        .collect()
}

#[test]
fn one_seed_reproduces_one_market_through_the_full_interleaving() {
    let days = 4;
    let ticks_per_day = 40;

    let mut a = engine(42);
    let mut b = engine(42);

    for day in 0..days {
        run_day(&mut a, day, ticks_per_day);
        run_day(&mut b, day, ticks_per_day);

        // Compared at EVERY close, not once at the end, so a divergence is a
        // located day rather than a mystery. Bit equality throughout: this
        // is the reproducibility the manifest verification and every
        // counterfactual surface rest on.
        for field in [
            PriceField::Price,
            PriceField::Volume,
            PriceField::GarchVariance,
            PriceField::MispricingS,
            PriceField::MakerInventory,
            PriceField::MarketCap,
        ] {
            assert_eq!(
                bits_of(&a, field),
                bits_of(&b, field),
                "day {day}: {field:?} diverged between two runs of one seed"
            );
        }
        assert_eq!(
            a.economy(),
            b.economy(),
            "day {day}: the macro state diverged between two runs of one seed"
        );
        assert_eq!(
            a.draws_by_stream(),
            b.draws_by_stream(),
            "day {day}: the two runs consumed different draw counts — the schedule \
             depended on something outside the seed"
        );
    }

    // The runs must also have DONE something: a reproducibility gate over a
    // market that never moved would pass vacuously.
    let moved = a
        .column(PriceField::Price)
        .iter()
        .zip(a.column(PriceField::PreviousClose))
        .filter(|(p, pc)| **p != *pc)
        .count();
    assert!(
        moved > 0,
        "no price moved in {days} days — the gate is not exercising the tick"
    );
}

/// The engine-level draw budget per day, pinned through `draws_by_stream`.
///
/// The retired thirty-day tapes made the whole schedule observable at once;
/// this keeps the market stream's per-day arithmetic observable without
/// them. It is written against the DOCUMENTED schedule, so a change to
/// either the code or the documentation that forgets the other fails here.
#[test]
fn a_days_market_draws_match_the_documented_schedule() {
    let mut e = engine(7);
    let sectors = sector_keys().len();
    let active = 8;
    let ticks: usize = 25;

    let before = e.draws_by_stream();
    run_day(&mut e, 0, ticks as i64);
    let after = e.draws_by_stream();

    // Per open tick: 1 market normal + one per sector + (1 normal + 1
    // stashed uniform) per active company + 4 settlement uniforms per active
    // company, unconditionally (FourAlways). The open reset and the close
    // draw nothing — asserted by the daily step being the only other
    // consumer, on its own stream.
    let per_tick = 1 + sectors + 2 * active + 4 * active;
    assert_eq!(
        after.market - before.market,
        ticks * per_tick,
        "the market stream's per-day consumption left the documented schedule"
    );
    assert!(
        after.economy > before.economy,
        "the daily macro step consumed nothing — the interleaving lost its first phase"
    );
}
