//! The cross-language divergence curve — WP5's headline measurement.
//!
//! Run: `cargo run --release --example divergence`
//!
//! # What this measures
//!
//! Every parity gate in this crate replays RECORDED draws, isolating the
//! arithmetic from the generator. This does the opposite: Rust runs its own
//! generator from the same seed as the reference implementation.
//!
//! The two DO disagree at the generator. Measured against this Node build,
//! **1.586% of normals differ, first at draw 31, by 1 to 2 ULP** -- because
//! `next_normal` is Box-Muller and routes through `cos`.
//!
//! # How the engine is driven, since the 2026-08 stream split
//!
//! The reference recording is a PRE-SPLIT artefact: one shared
//! `GameRng(seed, 99)` feeding every consumer in program order, settlement
//! drawing four uniforms or zero as its guards decided. `Engine::tick()` no
//! longer produces that schedule -- it draws from a private market substream
//! and settles on a fixed four-draw schedule (`docs/rng-streams.md`), a
//! different era from the tape, and running it against the reference would
//! compare two unrelated noise sequences.
//!
//! So the harness drives the reference's era explicitly: one
//! `GameRng(seed, MAIN_STREAM)` fed through [`Engine::tick_with`], which
//! consumes it in the shared-stream order, four-or-zero settlement included.
//! That is bit-exactly what `engine.tick()` itself did before the split --
//! same arithmetic, same draws, same order -- so the comparison still
//! measures Rust-versus-V8 and nothing else.
//!
//! # The reference must be cut at THIS crate's model constants
//!
//! The same era boundary recalibrated `MARKET_FACTOR_SIGMA` away from the
//! reference's inherited 0.003, to 0.0075 (`58837b3`). Vectors generated
//! from an unpatched reference now diverge from this crate at the first
//! tick, BY MODEL, and the curve would measure the recalibration rather
//! than the port. Regenerating `goldens/divergence-reference.json` for this
//! era means running the reference generator with its sigma patched to this
//! crate's value AND its crash amplifier's inlined `0.003` normaliser moved
//! with it -- this crate denominates that threshold in the sigma by name
//! (`factors.rs`), and a reference left normalising by the old value would
//! fire its amplifier on half of all ticks. That difference would wear this
//! curve as a costume: exactly the class of harness artefact the retraction
//! below exists to warn about.
//!
//! # The measured result: prices are bit-identical anyway
//!
//! Measured 2026-08-20, v4-era crate against its matching v4 reference. The
//! generator divergence does not reach the tape. Over a full session and
//! over twenty days with the daily lifecycle running:
//!
//! | | |
//! |---|---|
//! | printed prices | **bit-identical**, every company, every tick |
//! | unrounded `s` | differs by ~1e-17 absolute, and does NOT grow |
//! | GARCH variance | bit-identical |
//!
//! Two mechanisms produce that, both properties of the model rather than luck:
//!
//! 1. **The print is quantised.** The book quotes on a cent grid, so a print
//!    is rounded to two decimals. A 1-ULP difference in a normal perturbs the
//!    noise term by ~1e-19 and `s` by ~1e-17, moving a $200 stock by ~1e-15
//!    dollars -- thirteen orders of magnitude below a cent.
//! 2. **`s` mean-reverts.** The perturbation decays at `S_PHI_TICK` every tick
//!    instead of accumulating, so twenty days of feedback through the close
//!    leave it where it started.
//!
//! The 2026-08 era changed every trajectory, so the numbers above are
//! pending re-measurement against a v5-matched reference. Both mechanisms
//! are untouched by the era -- the grid and the mean reversion are what they
//! were -- so the expected result is the same; the point of re-running this
//! example is to confirm that rather than assume it.
//!
//! # An earlier version of this file reported a divergence curve. It was wrong.
//!
//! The reference generator called `resetDailyPrices(companies)` and DISCARDED
//! the result -- that function returns a new array rather than mutating. The
//! The reference implementation therefore ran WITHOUT the open reset while Rust called
//! `open_market()`, and the resulting starting-state mismatch was reported as
//! a `cos` divergence curve reaching 0.07%. Fixed in the divergence vector generator.
//! The curve was an artefact of the harness, not a property of the port.
//!
//! # The second table: what the split prevents
//!
//! The ULP story above is the divergence that REMAINS. The kind that used to
//! dominate -- any draw-count change anywhere re-dealing every draw after
//! it -- is structurally gone from the engine's own schedule, and this
//! example now shows both sides of that line with one extra draw taken
//! mid-session:
//!
//! - under the pre-split shared stream, that draw re-deals the market from
//!   the tick it lands on: a categorical divergence, not a ULP one;
//! - under the engine's own per-domain streams, the same draw comes from the
//!   external stream and every price is bit-identical.
//!
use std::fs;
use std::path::PathBuf;

use pretium::economy::{
    create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions,
};
use pretium::engine::{Engine, TickRequest, MAIN_STREAM};
use pretium::market::{GameTime, TickCompany, TickStock};
use pretium::rng::GameRng;
use serde_json::Value as Json;

fn bits(hex: &str) -> f64 {
    f64::from_bits(u64::from_str_radix(hex, 16).expect("bad bits"))
}
fn maybe(v: &Json) -> Option<f64> {
    v.as_str().map(bits)
}

pub fn load() -> Json {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens/divergence-reference.json");
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}\nRegenerate from the reference implementation's divergence generator",
            path.display()
        )
    });
    serde_json::from_str(&raw).expect("malformed divergence-reference.json")
}

pub fn build_company(c: &Json) -> TickCompany {
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
            garch_cascade: [0.015 * 0.015; pretium::market::garch::CASCADE_MAX],
            last_daily_return: maybe(&s["lastDailyReturn"]),
            beta: maybe(&s["beta"]),
            short_interest: bits(s["shortInterest"].as_str().unwrap()),
            float: bits(s["float"].as_str().unwrap()),
        },
    }
}

/// The engine plus the run parameters, built from the reference document's
/// initial state. Shared by every driving mode below so the modes can only
/// differ in how they draw, never in where they start.
struct Setup {
    engine: Engine,
    seed: u32,
    ticks: usize,
    volatility: f64,
}

fn setup(doc: &Json) -> Setup {
    let spec = &doc["spec"];
    let seed = spec["tickSeed"].as_u64().unwrap() as u32;
    let ticks = spec["ticks"].as_i64().unwrap() as usize;
    let volatility = bits(spec["volatility"].as_str().unwrap());

    let sector_keys: Vec<String> =
        serde_json::from_value(doc["sectorKeys"].clone()).expect("sectorKeys");

    let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
    let e = &doc["economy"];
    economy.vix = bits(e["vix"].as_str().unwrap());
    economy.federal_funds_rate = bits(e["federalFundsRate"].as_str().unwrap());
    economy.corporate_bond_yield = bits(e["corporateBondYield"].as_str().unwrap());
    economy.qe_pe_boost = bits(e["qePeBoost"].as_str().unwrap());
    economy.fear_greed_index = bits(e["fearGreedIndex"].as_str().unwrap());

    let companies: Vec<TickCompany> = doc["initial"]
        .as_array()
        .unwrap()
        .iter()
        .map(build_company)
        .collect();

    Setup {
        engine: Engine::new(
            seed,
            companies,
            economy,
            create_initial_central_bank_state(0),
            sector_keys,
        ),
        seed,
        ticks,
        volatility,
    }
}

fn tick_request(hour: i64, minute: i64, volatility: f64) -> TickRequest<'static> {
    TickRequest {
        time: GameTime {
            hour,
            minute,
            day_of_week: 3,
        },
        volatility_multiplier: volatility,
        news: &[],
        news_impact_queue: &[],
        order_volumes: &[],
    }
}

/// Drive the REFERENCE's era: one shared `GameRng(seed, MAIN_STREAM)` fed
/// through `tick_with`, which consumes it in the pre-split shared-stream
/// order, settlement's four-or-zero included. Bit-exactly what the pre-split
/// engine's own `tick()` did, and the only schedule the reference implementation recording
/// can be compared against.
///
/// `extra_draw_at`: before that tick, ONE uniform is taken from the shared
/// stream -- reproducing what any embedder event roll, macro branch or other
/// draw-count change did to the market before the split. `None` is the
/// faithful replay.
pub fn run_shared_stream(doc: &Json, extra_draw_at: Option<usize>) -> Vec<Vec<f64>> {
    let Setup {
        mut engine,
        seed,
        ticks,
        volatility,
    } = setup(doc);
    let mut main = GameRng::new(seed, MAIN_STREAM);

    // The reference calls `resetDailyPrices` after re-seeding, before the loop.
    engine.open_market();

    let mut out = Vec::with_capacity(ticks);
    let (mut hour, mut minute) = (9i64, 30i64);
    for t in 0..ticks {
        if extra_draw_at == Some(t) {
            main.next_f64();
        }
        engine.tick_with(&tick_request(hour, minute, volatility), &mut main);
        out.push(engine.prices());
        minute += 1;
        if minute >= 60 {
            minute = 0;
            hour += 1;
        }
    }
    out
}

/// Drive the engine's OWN era: `tick()` on its private market substream,
/// with `extra_draw_at` taking the same extra uniform through the EXTERNAL
/// stream instead. Not comparable to the reference -- a different era -- but
/// comparable to ITSELF, which is the point: the perturbation that re-deals
/// the shared-stream world must leave this one bit-identical.
pub fn run_own_streams(doc: &Json, extra_draw_at: Option<usize>) -> Vec<Vec<f64>> {
    let Setup {
        mut engine,
        seed: _,
        ticks,
        volatility,
    } = setup(doc);

    engine.open_market();

    let mut out = Vec::with_capacity(ticks);
    let (mut hour, mut minute) = (9i64, 30i64);
    for t in 0..ticks {
        if extra_draw_at == Some(t) {
            engine.draw_uniform();
        }
        engine.tick(&tick_request(hour, minute, volatility));
        out.push(engine.prices());
        minute += 1;
        if minute >= 60 {
            minute = 0;
            hour += 1;
        }
    }
    out
}

/// Run the Rust side against the reference: the shared-stream replay, no
/// perturbation. This is the harness `divergence_statistics.rs` imports.
pub fn run_rust(doc: &Json) -> Vec<Vec<f64>> {
    run_shared_stream(doc, None)
}

pub fn reference_prices(doc: &Json) -> Vec<Vec<f64>> {
    doc["prices"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| {
            row.as_array()
                .unwrap()
                .iter()
                .map(|p| bits(p.as_str().unwrap()))
                .collect()
        })
        .collect()
}

/// Relative error per tick, summarised across the roster.
pub struct TickError {
    pub max: f64,
    pub mean: f64,
    pub exact: usize,
    pub total: usize,
}

pub fn tick_errors(rust: &[Vec<f64>], ts: &[Vec<f64>]) -> Vec<TickError> {
    rust.iter()
        .zip(ts)
        .map(|(r, t)| {
            let mut max: f64 = 0.0;
            let mut sum = 0.0;
            let mut exact = 0;
            for (a, b) in r.iter().zip(t) {
                if a.to_bits() == b.to_bits() {
                    exact += 1;
                }
                // Relative, because a $500 name and a $3 name should not be
                // weighted differently by an absolute measure.
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
            TickError {
                max,
                mean: sum / r.len() as f64,
                exact,
                total: r.len(),
            }
        })
        .collect()
}

fn main() {
    let doc = load();
    let ts = reference_prices(&doc);
    let rust = run_rust(&doc);

    assert_eq!(rust.len(), ts.len(), "tick count");
    let errors = tick_errors(&rust, &ts);

    let first_divergence = errors.iter().position(|e| e.exact < e.total);
    let roster = errors[0].total;

    println!();
    println!("  Cross-language divergence — Rust vs V8, same seed, own generators");
    println!("  (shared-stream replay: tick_with from one GameRng(seed, MAIN_STREAM))");
    println!("  {} ticks x {roster} companies", errors.len());
    println!();
    match first_divergence {
        Some(t) => println!("  First divergence: tick {t} of {}", errors.len()),
        None => println!(
            "  Prices are BIT-IDENTICAL across all {} ticks and {roster} companies.",
            errors.len()
        ),
    }
    println!();
    println!(
        "  {:>6}  {:>9}  {:>12}  {:>12}",
        "tick", "identical", "mean rel err", "max rel err"
    );
    println!("  {:->6}  {:->9}  {:->12}  {:->12}", "", "", "", "");

    // Log-spaced, because the interesting behaviour is the growth RATE and a
    // linear sample would show a flat line then a cliff.
    let marks: Vec<usize> = [0usize, 1, 2, 5, 10, 20, 50, 100, 200, 389]
        .into_iter()
        .filter(|&m| m < errors.len())
        .collect();
    for m in marks {
        let e = &errors[m];
        println!(
            "  {:>6}  {:>4}/{:<4}  {:>12.3e}  {:>12.3e}",
            m, e.exact, e.total, e.mean, e.max
        );
    }

    let last = errors.last().unwrap();
    println!();
    println!(
        "  After a full session: mean {:.4}%, max {:.4}%",
        last.mean * 100.0,
        last.max * 100.0
    );
    println!();
    if first_divergence.is_none() {
        println!("  The generators DO disagree - 1.586% of normals differ by 1-2 ULP");
        println!("  against this Node build, first at draw 31. It does not reach the");
        println!("  tape, because the book prints on a cent grid and `s` mean-reverts");
        println!("  rather than accumulating. See the module docs for the measurement.");
    } else {
        println!("  Read this as a budget, not a defect. There is no single browser");
        println!("  answer to match - Chrome, Firefox and Safari disagree about `cos`");
        println!("  with each other. A LARGE divergence here is a different story:");
        println!("  check the reference was cut at this crate's model constants");
        println!("  (MARKET_FACTOR_SIGMA and the amplifier normaliser - module docs)");
        println!("  before reading it as a property of the port.");
    }

    // ── What the split prevents ───────────────────────────────────────────
    //
    // One extra draw, taken a third of the way into the session. Before the
    // split that is what ANY draw-count change anywhere -- an embedder event
    // roll, a macro branch, a settlement guard flipping -- did to the market:
    // every subsequent draw shifted. Since the split the same draw comes from
    // the external stream and the market's sequence cannot move.
    let perturb = errors.len() / 3;
    let shared_bumped = run_shared_stream(&doc, Some(perturb));
    let own_base = run_own_streams(&doc, None);
    let own_bumped = run_own_streams(&doc, Some(perturb));

    let shared_vs = tick_errors(&shared_bumped, &rust);
    let own_vs = tick_errors(&own_bumped, &own_base);

    println!();
    println!("  What the 2026-08 stream split prevents");
    println!("  One extra draw taken before tick {perturb} — an embedder event roll, say:");
    println!();
    match shared_vs.iter().position(|e| e.exact < e.total) {
        Some(t) => {
            let end = shared_vs.last().unwrap();
            println!("    pre-split shared stream:  every draw after it shifts — first");
            println!("                              divergence at tick {t}; at the close");
            println!(
                "                              {}/{} prices differ, max rel err {:.3e}",
                end.total - end.exact,
                end.total,
                end.max
            );
        }
        None => {
            // The extra draw re-deals every subsequent normal, so identical
            // prices here would mean the shared stream is not actually being
            // consumed — a harness bug, and this example measures its harness.
            println!("    pre-split shared stream:  NO divergence — the perturbation did");
            println!("                              not reach the draws. Harness bug; do");
            println!("                              not read the table above until fixed.");
        }
    }
    match own_vs.iter().position(|e| e.exact < e.total) {
        None => {
            println!(
                "    per-domain streams:       prices BIT-IDENTICAL, all {} ticks x {roster}",
                own_vs.len()
            );
            println!("                              names — the external stream cannot");
            println!("                              move the market's sequence.");
        }
        Some(t) => {
            println!("    per-domain streams:       DIVERGED at tick {t}. That breaks the");
            println!("                              stream-isolation contract in");
            println!("                              docs/rng-streams.md — a finding, not");
            println!("                              a harness artefact. Report it.");
        }
    }
    println!();
    println!("  The table above is the divergence that remains — 1-2 ULP at the");
    println!("  generator, absorbed by the cent grid. This one is the divergence");
    println!("  that used to dominate, and the engine now prevents it by");
    println!("  construction rather than measuring it after the fact.");
    println!();
}
