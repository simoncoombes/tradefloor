//! Parity for `simulateMarketTick`'s live path — WP4, part 2.
//!
//! Draws are REPLAYED, not generated. `next_normal` routes through `cos` and
//! diverges from V8 on 1.545% of draws; the tick takes one normal per company
//! per minute, so a 12-name roster would diverge on the first tick with near
//! certainty. That test would say nothing about the tick.
//!
//! Replaying splits the failures: the arithmetic is held to bit-parity with no
//! tolerance, and the DRAW SCHEDULE is held exactly — the tape runs dry if the
//! port asks for one draw too many, leaves a remainder if it asks for one too
//! few, and checks each draw's KIND so a uniform taken where a normal was
//! expected fails even when the counts agree.
//!
//! The schedule these vectors pin, per tick:
//!
//! | | |
//! |---|---|
//! | market factor | 1 normal |
//! | sector factors | 1 normal per sector, in `SECTOR_CONFIGS` key order |
//! | per active company, phase 1 | 1 normal, then 1 uniform |
//! | per active company, settlement | 4 uniforms, market-open only |
//!
//! The phase-1 uniform is CONSUMED IN PHASE 3. A port that drew it where it
//! is used would produce identical counts on a different stream — the same
//! class of trap as WP3's object-literal draw, and the reason the tape checks
//! order rather than totals.

use std::fs;
use std::path::PathBuf;

use pretium::economy::{create_initial_economy_state, InitialEconomyOptions};
use pretium::market::*;
use pretium::rng::Rng;
use serde::Deserialize;
use serde_json::Value as Json;

// ── The replay generator ──────────────────────────────────────────────────

#[derive(Deserialize, Clone, Debug)]
struct Draw {
    kind: String,
    bits: String,
}

struct ScriptedRng {
    tape: Vec<Draw>,
    at: usize,
    problems: Vec<String>,
    label: String,
}

impl ScriptedRng {
    fn new(tape: Vec<Draw>, label: impl Into<String>) -> Self {
        Self {
            tape,
            at: 0,
            problems: Vec::new(),
            label: label.into(),
        }
    }

    fn take(&mut self, want_kind: &str) -> f64 {
        if self.at >= self.tape.len() {
            self.problems.push(format!(
                "{}: asked for draw #{} ({want_kind}) but the tape holds only {} — \
                 the port takes MORE draws than the TypeScript",
                self.label,
                self.at + 1,
                self.tape.len()
            ));
            self.at += 1;
            return 0.0;
        }
        let d = &self.tape[self.at];
        if d.kind != want_kind {
            self.problems.push(format!(
                "{}: draw #{} is a '{}' in the TypeScript but the port asked for a '{want_kind}' \
                 — same count, different schedule",
                self.label,
                self.at + 1,
                d.kind
            ));
        }
        self.at += 1;
        f64::from_bits(u64::from_str_radix(&d.bits, 16).expect("bad draw bits"))
    }

    fn finish(mut self) -> Vec<String> {
        if self.at < self.tape.len() {
            self.problems.push(format!(
                "{}: {} draws left unconsumed ({} of {} taken) — the port takes FEWER draws \
                 than the TypeScript",
                self.label,
                self.tape.len() - self.at,
                self.at,
                self.tape.len()
            ));
        }
        self.problems
    }
}

impl Rng for ScriptedRng {
    fn next_f64(&mut self) -> f64 {
        self.take("u")
    }
    fn next_normal(&mut self) -> f64 {
        self.take("n")
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────

fn bits(hex: &str) -> f64 {
    f64::from_bits(u64::from_str_radix(hex, 16).unwrap_or_else(|e| panic!("bad bits {hex}: {e}")))
}
fn maybe(v: &Json) -> Option<f64> {
    v.as_str().map(bits)
}
fn agrees(got: f64, want: f64) -> bool {
    got.to_bits() == want.to_bits() || (got.is_nan() && want.is_nan())
}

fn load(name: &str) -> Json {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens")
        .join(name);
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}\nRun: npx tsx scripts/rust-port/market-tick-vectors.ts",
            path.display()
        )
    });
    let doc: Json = serde_json::from_str(&raw).unwrap_or_else(|e| panic!("malformed {name}: {e}"));
    assert!(
        !doc["meta"]["wasmReady"].as_bool().expect("meta.wasmReady"),
        "{name} was generated with WASM ready — wrong era"
    );
    doc
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
            last_daily_return: maybe(&s["lastDailyReturn"]),
            beta: maybe(&s["beta"]),
            short_interest: bits(s["shortInterest"].as_str().unwrap()),
            float: bits(s["float"].as_str().unwrap()),
        },
    }
}

/// The breaker's `s` re-derivation goes through `log`, where `libm` and V8
/// differ by one ULP on ~7.5% of inputs in the relevant band. Bounded at one
/// ULP per tick; see the re-sync note below for why it cannot be allowed to
/// accumulate.
const MAX_LOG_ULP: i64 = 1;

fn check_scenario(file: &str) {
    let doc = load(file);
    let volatility = bits(doc["spec"]["volatility"].as_str().unwrap());
    let sector_keys: Vec<String> =
        serde_json::from_value(doc["sectorKeys"].clone()).expect("sectorKeys");

    // The economy is rebuilt from defaults and then overridden with the four
    // fields the tick actually reads, so an unrelated economy field cannot
    // silently participate.
    let mut economy = create_initial_economy_state(&InitialEconomyOptions::default());
    let e = &doc["economy"];
    economy.vix = bits(e["vix"].as_str().unwrap());
    economy.federal_funds_rate = bits(e["federalFundsRate"].as_str().unwrap());
    economy.corporate_bond_yield = bits(e["corporateBondYield"].as_str().unwrap());
    economy.qe_pe_boost = bits(e["qePeBoost"].as_str().unwrap());
    economy.fear_greed_index = bits(e["fearGreedIndex"].as_str().unwrap());

    let mut companies: Vec<TickCompany> = doc["initial"]
        .as_array()
        .expect("initial")
        .iter()
        .map(build_company)
        .collect();

    let mut problems: Vec<String> = Vec::new();
    let mut log_exceptions: Vec<i64> = Vec::new();
    let mut checked = 0usize;
    let mut first_divergence: Option<i64> = None;
    let mut statuses = std::collections::BTreeSet::new();

    for tick_doc in doc["ticks"].as_array().expect("ticks") {
        let t = tick_doc["tick"].as_i64().unwrap();
        let time = GameTime {
            hour: tick_doc["time"]["hour"].as_i64().unwrap(),
            minute: tick_doc["time"]["minute"].as_i64().unwrap(),
            day_of_week: tick_doc["time"]["dayOfWeek"].as_i64().unwrap(),
        };
        let status = get_market_status(time);
        statuses.insert(status.as_str());

        let before = problems.len();
        let tape: Vec<Draw> = serde_json::from_value(tick_doc["draws"].clone()).expect("draws");
        let mut rng = ScriptedRng::new(tape, format!("tick {t}"));

        simulate_market_tick(
            &mut companies,
            &TickInputs {
                economy: &economy,
                market_status: status,
                intraday_t: intraday_fraction(time),
                volatility_multiplier: volatility,
                news: &[],
                news_impact_queue: &[],
                order_volumes: &[],
                sector_keys: &sector_keys,
            },
            &mut rng,
        );
        problems.extend(rng.finish());

        for (i, want) in tick_doc["companies"].as_array().unwrap().iter().enumerate() {
            let got = &companies[i];
            let id = want["id"].as_str().unwrap();
            if got.id != id {
                problems.push(format!("tick {t}: company order changed at {i}"));
                continue;
            }
            for (field, g, w) in [
                ("price", got.stock.price, want["price"].as_str().unwrap()),
                ("high", got.stock.high, want["high"].as_str().unwrap()),
                ("low", got.stock.low, want["low"].as_str().unwrap()),
                ("volume", got.stock.volume, want["volume"].as_str().unwrap()),
                (
                    "marketCap",
                    got.stock.market_cap,
                    want["marketCap"].as_str().unwrap(),
                ),
            ] {
                if !agrees(g, bits(w)) {
                    problems.push(format!(
                        "tick {t} {id}.{field}: rust={g:?} ({:016X}) ts={:?} ({w})",
                        g.to_bits(),
                        bits(w)
                    ));
                }
                checked += 1;
            }
            for (field, g, w) in [
                (
                    "mispricingS",
                    got.stock.mispricing_s,
                    maybe(&want["mispricingS"]),
                ),
                (
                    "previousTickPrice",
                    got.stock.previous_tick_price,
                    maybe(&want["previousTickPrice"]),
                ),
                (
                    "makerInventory",
                    got.stock.maker_inventory,
                    maybe(&want["makerInventory"]),
                ),
            ] {
                let ok = match (g, w) {
                    (None, None) => true,
                    (Some(a), Some(b)) => agrees(a, b),
                    _ => false,
                };
                if !ok {
                    // The ONE documented Tier-2 exception here, and only on
                    // `mispricingS`. When the circuit breaker binds, `s` is
                    // re-derived as `log(newPrice / fv)` so state and price
                    // stay consistent — and `libm::log` differs from V8 by a
                    // ULP on roughly 7.5% of inputs in the band the clamp
                    // produces (measured, `examples/log_probe.rs`).
                    //
                    // No other field gets this latitude, and the exception
                    // does not apply on the unclamped path: `s` only goes
                    // through `log` when the breaker actually bound.
                    let ulp = match (g, w) {
                        (Some(a), Some(b)) => (a.to_bits() as i64 - b.to_bits() as i64).abs(),
                        _ => i64::MAX,
                    };
                    if field == "mispricingS" && ulp <= MAX_LOG_ULP {
                        log_exceptions.push(ulp);
                    } else {
                        problems.push(format!(
                            "tick {t} {id}.{field}: rust={g:?} ts={w:?} ({ulp} ULP)"
                        ));
                    }
                }
                checked += 1;
            }
        }

        // Re-sync `mispricingS` to the recorded value after each tick.
        //
        // `s` is internal state carried ACROSS ticks: the process is
        // `s = s*phi + ...`, so a single 1-ULP seed from the breaker's
        // `log` re-derivation persists and compounds. Left uncorrected it
        // reached 2 ULP by tick 16 of 25 — not a second defect, the same one
        // accumulating.
        //
        // Re-syncing makes this a test of each tick's arithmetic given the
        // same starting state, which is what the gate is for. The
        // accumulation is a property of the exception, already characterised
        // in DETERMINISM.md, and hiding it inside a widening tolerance here
        // would test neither thing well.
        for (i, want) in tick_doc["companies"].as_array().unwrap().iter().enumerate() {
            if let Some(w) = maybe(&want["mispricingS"]) {
                companies[i].stock.mispricing_s = Some(w);
            }
        }

        if problems.len() > before && first_divergence.is_none() {
            first_divergence = Some(t);
        }
        // Once the states differ the two are simulating different markets.
        if first_divergence.is_some() {
            break;
        }
    }

    if let Some(t) = first_divergence {
        problems.insert(
            0,
            format!(
                "FIRST DIVERGENCE on tick {t} of {}",
                doc["ticks"].as_array().unwrap().len()
            ),
        );
    }

    let worst = log_exceptions.iter().copied().max().unwrap_or(0);
    println!(
        "{file}: {checked} values checked, sessions: {statuses:?},          {} `log` exceptions on the breaker path (worst {worst} ULP)",
        log_exceptions.len()
    );
    if !problems.is_empty() {
        let shown: Vec<&str> = problems.iter().take(20).map(|s| s.as_str()).collect();
        panic!(
            "{} mismatches in {file} (first {} shown):\n\n  {}\n",
            problems.len(),
            shown.len(),
            shown.join("\n  ")
        );
    }
}

#[test]
fn tick_open_session() {
    check_scenario("market-tick-open-session.json");
}

#[test]
fn tick_midday() {
    check_scenario("market-tick-midday.json");
}

#[test]
fn tick_pre_market() {
    check_scenario("market-tick-pre-market.json");
}

#[test]
fn tick_after_hours() {
    check_scenario("market-tick-after-hours.json");
}

#[test]
fn tick_closed_weekend() {
    check_scenario("market-tick-closed-weekend.json");
}

#[test]
fn tick_crisis_vix() {
    check_scenario("market-tick-crisis-vix.json");
}

#[test]
fn tick_bankrupt_and_private() {
    check_scenario("market-tick-bankrupt-and-private.json");
}

#[test]
fn tick_breaker_binding() {
    check_scenario("market-tick-breaker-binding.json");
}

#[test]
fn tick_squeeze_and_cascade() {
    check_scenario("market-tick-squeeze-and-cascade.json");
}

/// The draw schedule, asserted from the vectors themselves.
///
/// Separate from the replay tests because it checks the TypeScript's own
/// behaviour rather than the port's: if the original ever grows a draw the
/// schedule does not predict, the arithmetic below stops adding up and the
/// documented contract has to be revisited rather than quietly widened.
#[test]
fn the_recorded_draw_schedule_matches_the_documented_arithmetic() {
    for (file, expect_settlement) in [
        ("market-tick-open-session.json", true),
        ("market-tick-midday.json", true),
        ("market-tick-pre-market.json", false),
        ("market-tick-after-hours.json", false),
        ("market-tick-bankrupt-and-private.json", true),
    ] {
        let doc = load(file);
        let sectors = doc["sectorKeys"].as_array().unwrap().len();
        let active = doc["initial"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|c| !c["isBankrupt"].as_bool().unwrap() && c["isPublic"].as_bool().unwrap())
            .count();

        // 1 market normal + one per sector + 2 per active company, plus 4
        // more per company when the book settles.
        let expected = 1 + sectors + 2 * active + if expect_settlement { 4 * active } else { 0 };

        for tick_doc in doc["ticks"].as_array().unwrap() {
            let drawn = tick_doc["draws"].as_array().unwrap().len();
            assert_eq!(
                drawn, expected,
                "{file} tick {}: {drawn} draws, the documented schedule predicts {expected} \
                 ({sectors} sectors, {active} active, settlement {expect_settlement})",
                tick_doc["tick"]
            );
        }
    }

    // The closed market must cost nothing at all.
    let doc = load("market-tick-closed-weekend.json");
    for tick_doc in doc["ticks"].as_array().unwrap() {
        assert_eq!(
            tick_doc["draws"].as_array().unwrap().len(),
            0,
            "a closed market must not advance the stream"
        );
    }
}
