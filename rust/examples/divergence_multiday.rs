//! Multi-day divergence — does the single-session plateau survive?
//!
//! Run: `cargo run --release --example divergence_multiday`
//!
//! `examples/divergence.rs` measures one session and finds prices
//! bit-identical. This asks whether that survives DAYS, which add two things a
//! single session cannot show:
//!
//!  1. **Feedback through the daily lifecycle.** The close rolls momentum and
//!     updates GARCH, so a difference in `s` becomes a difference in
//!     tomorrow's herding input, and a difference in today's noise becomes a
//!     difference in tomorrow's volatility. Those are the paths that could
//!     turn a quantised-away perturbation into a visible one.
//!  2. **Discrete branch divergence.** Over enough days the two engines could
//!     take DIFFERENT branches — a cycle transitioning in one and not the
//!     other. That is not a small numeric error, it is two different worlds,
//!     and no relative-error curve would describe it honestly. So branches are
//!     compared separately and reported first.
//!
//! # Measured over twenty days
//!
//! Measured 2026-08-20, v4-era crate against its matching v4 reference:
//! prices bit-identical every day; cycle phases, transition days and meeting
//! days all identical; GARCH variance identical. The unrounded `s` differs by
//! ~1e-17 and does NOT grow — 6.9e-18 on day 0, 1.4e-17 on day 19 — because
//! `s` mean-reverts at `S_PHI_TICK` rather than accumulating.
//!
//! The feedback paths in (1) are real but carry a perturbation thirteen orders
//! of magnitude below a cent, so they never reach the tape.
//!
//! # How the engine is driven, since the 2026-08 stream split
//!
//! The reference recording is a pre-split artefact: ONE shared
//! `GameRng(seed, 99)` threading the daily macro chain, the ticks and the
//! settlement guards in program order, day after day. `Engine::advance_day()`
//! and `Engine::tick()` no longer produce that schedule — they draw from
//! private economy and market substreams, and settlement draws four
//! unconditionally (`docs/rng-streams.md`). So this harness reconstructs the
//! reference's era explicitly: one `GameRng(seed, MAIN_STREAM)` handed to
//! `advance_day_with` and `tick_with` in the reference's order, which is
//! bit-exactly the pre-split engine's own generative path — the Box-Muller
//! spare carries across the macro/market boundary exactly as the shared
//! stream carried it.
//!
//! That coupling is the point of preserving it HERE and the point of
//! removing it from live runs: pre-split, a day whose macro chain drew one
//! extra shock re-dealt every market draw after it — a categorical
//! divergence this file's branch table would report as two different worlds.
//! The engine's own schedule now makes that impossible by construction
//! (`tests/stream_alignment.rs` and the engine tests assert it; the
//! single-session `divergence` example demonstrates it). This example stays
//! focused on the question only the reference can answer: does the
//! cross-language plateau survive days of lifecycle feedback?
//!
//! As with the single-session example, the reference must be cut at THIS
//! crate's model constants — the era recalibrated `MARKET_FACTOR_SIGMA` from
//! the reference's 0.003 to 0.0075 and the crash-amplifier normaliser
//! follows it by name. Vectors from an unpatched reference measure the
//! recalibration, not the port (see `examples/divergence.rs`'s module docs
//! for the regeneration requirements), and the v4 numbers above are pending
//! re-measurement against a v5-matched reference.
//!
use std::fs;
use std::path::PathBuf;

use tradefloor::economy::{
    create_initial_central_bank_state, create_initial_economy_state, CyclePhase,
    InitialEconomyOptions,
};
use tradefloor::engine::{DayAdvanceRequest, DayCloseRequest, Engine, TickRequest, MAIN_STREAM};
use tradefloor::market::{AvgVolumePolicy, GameTime, TickCompany, TickStock};
use tradefloor::rng::GameRng;
use serde_json::Value as Json;

fn bits(hex: &str) -> f64 {
    f64::from_bits(u64::from_str_radix(hex, 16).expect("bad bits"))
}
fn maybe(v: &Json) -> Option<f64> {
    v.as_str().map(bits)
}

pub fn load() -> Json {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens/divergence-multiday.json");
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}\nRegenerate from the reference implementation's\
             divergence multi-day generator",
            path.display()
        )
    });
    serde_json::from_str(&raw).expect("malformed divergence-multiday.json")
}

fn build_company(c: &Json) -> TickCompany {
    let s = &c["stock"];
    TickCompany {
        id: c["id"].as_str().unwrap().to_string(),
        ticker: c["ticker"].as_str().unwrap().to_string(),
        sector: c["sector"].as_str().unwrap().to_string(),
        is_bankrupt: c["isBankrupt"].as_bool().unwrap(),
        is_public: c["isPublic"].as_bool().unwrap(),
        sector_volatility: maybe(&c["sectorVolatility"]),
        sector_avg_pe: maybe(&c["sectorAvgPe"]),
        eps: maybe(&c["eps"]),
        book_value_per_share: maybe(&c["bookValuePerShare"]),
        revenue_growth: maybe(&c["revenueGrowth"]),
        stock: TickStock {
            price: bits(s["price"].as_str().unwrap()),
            previous_close: bits(s["previousClose"].as_str().unwrap()),
            previous_tick_price: maybe(&s["previousTickPrice"]),
            open: bits(s["open"].as_str().unwrap()),
            high: bits(s["high"].as_str().unwrap()),
            low: bits(s["low"].as_str().unwrap()),
            volume: bits(s["volume"].as_str().unwrap()),
            avg_volume: bits(s["avgVolume"].as_str().unwrap()),
            shares_outstanding: bits(s["sharesOutstanding"].as_str().unwrap()),
            market_cap: bits(s["marketCap"].as_str().unwrap()),
            mispricing_s: maybe(&s["mispricingS"]),
            mispricing_s_prev_close: maybe(&s["mispricingSPrevClose"]),
            mispricing_momentum: maybe(&s["mispricingMomentum"]),
            maker_inventory: maybe(&s["makerInventory"]),
            garch_variance: bits(s["garchVariance"].as_str().unwrap()),
            garch_cascade: [0.015 * 0.015; tradefloor::market::garch::CASCADE_MAX],
            last_daily_return: maybe(&s["lastDailyReturn"]),
            beta: maybe(&s["beta"]),
            short_interest: bits(s["shortInterest"].as_str().unwrap()),
            float: bits(s["float"].as_str().unwrap()),
        },
    }
}

/// One day's comparison.
pub struct DayResult {
    pub day: i64,
    pub mean_rel_err: f64,
    pub max_rel_err: f64,
    pub phase_matches: bool,
    pub meeting_matches: bool,
    pub cycle_phase_matches: bool,
}

pub fn run(doc: &Json) -> Vec<DayResult> {
    let spec = &doc["spec"];
    let tick_seed = spec["tickSeed"].as_u64().unwrap() as u32;
    let ticks_per_day = spec["ticksPerDay"].as_i64().unwrap();
    let volatility = bits(spec["volatility"].as_str().unwrap());
    let sector_keys: Vec<String> =
        serde_json::from_value(doc["sectorKeys"].clone()).expect("sectorKeys");

    let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
    let ie = &doc["initialEconomy"];
    economy.vix = bits(ie["vix"].as_str().unwrap());
    economy.federal_funds_rate = bits(ie["federalFundsRate"].as_str().unwrap());
    economy.corporate_bond_yield = bits(ie["corporateBondYield"].as_str().unwrap());
    economy.qe_pe_boost = bits(ie["qePeBoost"].as_str().unwrap());
    economy.fear_greed_index = bits(ie["fearGreedIndex"].as_str().unwrap());
    economy.inflation_rate = bits(ie["inflationRate"].as_str().unwrap());
    economy.months_in_current_phase = bits(ie["monthsInCurrentPhase"].as_str().unwrap());
    economy.cycle_phase = CyclePhase::from_name(ie["cyclePhase"].as_str().unwrap()).unwrap();

    let mut central_bank = create_initial_central_bank_state(0);
    let icb = &doc["initialCentralBank"];
    central_bank.next_meeting_date = icb["nextMeetingDate"].as_i64().unwrap();
    central_bank.last_meeting_date = icb["lastMeetingDate"].as_i64().unwrap();
    central_bank.hawkish_dovish_score = bits(icb["hawkishDovishScore"].as_str().unwrap());
    central_bank.qe_monthly_purchases = bits(icb["qeMonthlyPurchases"].as_str().unwrap());
    central_bank.qe_active = icb["qeActive"].as_bool().unwrap();

    let companies: Vec<TickCompany> = doc["initial"]
        .as_array()
        .unwrap()
        .iter()
        .map(build_company)
        .collect();
    let variances: Vec<f64> = doc["initial"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| bits(c["sectorBaseDailyVariance"].as_str().unwrap()))
        .collect();
    let roster = companies.len();

    let mut engine = Engine::new(tick_seed, companies, economy, central_bank, sector_keys);
    // The reference's single shared stream, reconstructed. Every draw of the
    // run — macro chain and ticks alike — comes from this one generator, in
    // the reference's order, because that is the era the tape was cut in.
    let mut main = GameRng::new(tick_seed, MAIN_STREAM);
    let mut out = Vec::new();

    for day_doc in doc["days"].as_array().unwrap() {
        let day = day_doc["day"].as_i64().unwrap();

        let advance = engine.advance_day_with(
            &DayAdvanceRequest {
                volatility,
                active_shocks: &[],
                market_return_pct: 0.0,
                game_day: day,
                timestamp: day * 24 * 60,
            },
            &mut main,
        );

        engine.open_market();
        let (mut hour, mut minute) = (9i64, 30i64);
        for _ in 0..ticks_per_day {
            engine.tick_with(
                &TickRequest {
                    time: GameTime {
                        hour,
                        minute,
                        day_of_week: 3,
                    },
                    volatility_multiplier: volatility,
                    news: &[],
                    news_impact_queue: &[],
                    order_volumes: &[],
                },
                &mut main,
            );
            minute += 1;
            if minute >= 60 {
                minute = 0;
                hour += 1;
            }
        }
        // A reference tape is being replayed, so the reference's EMA runs.
        engine.close_market(&DayCloseRequest {
            daily_innovations: &vec![None; roster],
            sector_base_variances: &variances,
            avg_volume: AvgVolumePolicy::ReferenceEma,
        });

        let want: Vec<f64> = day_doc["closePrices"]
            .as_array()
            .unwrap()
            .iter()
            .map(|p| bits(p.as_str().unwrap()))
            .collect();
        let got = engine.prices();

        let mut max: f64 = 0.0;
        let mut sum = 0.0;
        for (a, b) in got.iter().zip(&want) {
            let rel = if *b != 0.0 {
                ((a - b) / b).abs()
            } else {
                (a - b).abs()
            };
            if rel > max {
                max = rel;
            }
            sum += rel;
        }

        out.push(DayResult {
            day,
            mean_rel_err: sum / roster as f64,
            max_rel_err: max,
            phase_matches: advance.phase_changed == day_doc["phaseChanged"].as_bool().unwrap(),
            meeting_matches: advance.meeting_held == day_doc["meetingHeld"].as_bool().unwrap(),
            cycle_phase_matches: engine.economy().cycle_phase.as_str()
                == day_doc["cyclePhase"].as_str().unwrap(),
        });
    }

    out
}

fn main() {
    let doc = load();
    let results = run(&doc);

    println!();
    println!(
        "  Multi-day divergence — Rust vs V8, own generators, {} days",
        results.len()
    );
    println!("  (shared-stream replay: one GameRng(seed, MAIN_STREAM) through");
    println!("   advance_day_with and tick_with, the reference's era)");
    println!();

    // Branches first: a divergence here is categorical, and reporting it after
    // a table of small numbers would bury it.
    let branch_breaks: Vec<&DayResult> = results
        .iter()
        .filter(|r| !r.phase_matches || !r.meeting_matches || !r.cycle_phase_matches)
        .collect();
    if branch_breaks.is_empty() {
        println!(
            "  Discrete branches: IDENTICAL on all {} days",
            results.len()
        );
        println!("  (same cycle phases, same transition days, same meeting days)");
    } else {
        println!(
            "  Discrete branches DIVERGED on {} day(s):",
            branch_breaks.len()
        );
        for r in branch_breaks.iter().take(5) {
            println!(
                "    day {}: phase {} meeting {} cyclePhase {}",
                r.day, r.phase_matches, r.meeting_matches, r.cycle_phase_matches
            );
        }
        println!("  This is two different worlds, not a rounding gap.");
    }

    println!();
    println!(
        "  {:>5}  {:>14}  {:>14}",
        "day", "mean rel err", "max rel err"
    );
    println!("  {:->5}  {:->14}  {:->14}", "", "", "");
    for r in &results {
        if r.day % 2 == 0 || r.day == results.len() as i64 - 1 {
            println!(
                "  {:>5}  {:>14.3e}  {:>14.3e}",
                r.day, r.mean_rel_err, r.max_rel_err
            );
        }
    }

    let first = &results[0];
    let last = results.last().unwrap();
    println!();
    println!(
        "  Day 0: mean {:.4}%   Day {}: mean {:.4}%   growth {:.2}x",
        first.mean_rel_err * 100.0,
        last.day,
        last.mean_rel_err * 100.0,
        last.mean_rel_err / first.mean_rel_err.max(f64::MIN_POSITIVE)
    );
    println!();
}
