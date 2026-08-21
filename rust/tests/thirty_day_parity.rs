//! Thirty simulated days, interleaved — WP5's acceptance criterion.
//!
//! # What this catches that nothing else does
//!
//! Every other gate verifies one function or one session. This verifies the
//! INTERLEAVING, in `runTick`'s order — daily → transitions → market hours —
//! thirty times over.
//!
//! That order is where a port goes wrong invisibly. The economy steps before
//! the market, so the first tick of a new day reads the day's NEW rates. The
//! open reset anchors the breaker band. The close rolls momentum and updates
//! GARCH, and both feed tomorrow. Get any of it out of order and every
//! individual function still passes its own gate while the simulation drifts.
//!
//! # Full tape, not just counts
//!
//! Recording only the NUMBER of draws per step does not work, and it is worth
//! saying why because it is the obvious cheap version. Draw counts are
//! state-dependent — settlement takes four draws or zero, OPEC fires on a
//! price condition, the bank reschedules itself randomly — so once values
//! diverge the counts legitimately diverge too and the comparison stops
//! meaning anything. Replaying the full tape keeps both aligned for thirty
//! days, and any schedule error surfaces at once.
//!
//! # Coverage is asserted, not hoped for
//!
//! The `calm` scenario reaches neither a cycle transition nor a bank meeting
//! in thirty days, and cannot: the first meeting is scheduled 45 days out and
//! expansion needs six months in phase. Left at that, this file would look
//! thorough while never exercising the two most interesting interleavings. The
//! `eventful` scenario ages the state so both fire, and the generator refuses
//! to write it if they do not.

use std::fs;
use std::path::PathBuf;

use pretium::economy::{
    create_initial_central_bank_state, create_initial_economy_state, CyclePhase,
    InitialEconomyOptions,
};
use pretium::engine::{DayAdvanceRequest, DayCloseRequest, Engine, PriceField, TickRequest};
use pretium::market::{GameTime, TickCompany, TickStock};
use pretium::rng::Rng;
use serde_json::Value as Json;

// ── Replay ────────────────────────────────────────────────────────────────

/// Feeds a recorded tape, where each entry is a kind prefix plus raw bits.
struct Tape {
    draws: Vec<String>,
    at: usize,
    problems: Vec<String>,
    label: String,
}

impl Tape {
    fn new(draws: Vec<String>, label: impl Into<String>) -> Self {
        Self {
            draws,
            at: 0,
            problems: Vec::new(),
            label: label.into(),
        }
    }

    fn take(&mut self, want: char) -> f64 {
        if self.at >= self.draws.len() {
            self.problems.push(format!(
                "{}: asked for draw #{} ('{want}') but the tape holds only {} — \
                 the port takes MORE draws than the TypeScript",
                self.label,
                self.at + 1,
                self.draws.len()
            ));
            self.at += 1;
            return 0.0;
        }
        let entry = &self.draws[self.at];
        let kind = entry.as_bytes()[0] as char;
        if kind != want {
            self.problems.push(format!(
                "{}: draw #{} is '{kind}' in the TypeScript but the port asked for '{want}' — \
                 same count, different schedule",
                self.label,
                self.at + 1
            ));
        }
        self.at += 1;
        f64::from_bits(u64::from_str_radix(&entry[1..], 16).expect("bad draw bits"))
    }

    fn finish(mut self) -> Vec<String> {
        if self.at < self.draws.len() {
            self.problems.push(format!(
                "{}: {} draws left unconsumed ({} of {} taken) — the port takes FEWER draws",
                self.label,
                self.draws.len() - self.at,
                self.at,
                self.draws.len()
            ));
        }
        self.problems
    }
}

impl Rng for Tape {
    fn next_f64(&mut self) -> f64 {
        self.take('u')
    }
    fn next_normal(&mut self) -> f64 {
        self.take('n')
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
fn same_opt(got: Option<f64>, want: Option<f64>) -> bool {
    match (got, want) {
        (None, None) => true,
        (Some(a), Some(b)) => agrees(a, b),
        _ => false,
    }
}

fn load(name: &str) -> Json {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens")
        .join(name);
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}\nRun: npx tsx scripts/rust-port/thirty-day-vectors.ts",
            path.display()
        )
    });
    let doc: Json = serde_json::from_str(&raw).unwrap_or_else(|e| panic!("malformed {name}: {e}"));
    assert!(
        !doc["meta"]["wasmReady"].as_bool().expect("meta.wasmReady"),
        "{name} was generated with WASM ready"
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

fn tape_of(v: &Json) -> Vec<String> {
    serde_json::from_value(v.clone()).expect("tape")
}

fn check(file: &str) {
    let doc = load(file);
    let spec = &doc["spec"];
    let days = spec["days"].as_i64().unwrap();
    let ticks_per_day = spec["ticksPerDay"].as_i64().unwrap();
    let volatility = bits(spec["volatility"].as_str().unwrap());
    let sector_keys: Vec<String> =
        serde_json::from_value(doc["sectorKeys"].clone()).expect("sectorKeys");

    // Economy rebuilt from defaults, then the fields the run arranged.
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
    let sector_variances: Vec<f64> = doc["initial"]
        .as_array()
        .unwrap()
        .iter()
        .map(|c| bits(c["sectorBaseDailyVariance"].as_str().unwrap()))
        .collect();
    let roster = companies.len();

    // The seed is irrelevant — every draw comes from the tape — but the
    // Engine owns a generator, so it gets one.
    let mut engine = Engine::new(0, companies, economy, central_bank, sector_keys);

    let mut problems: Vec<String> = Vec::new();
    let mut first_divergence: Option<i64> = None;
    let (mut transitions, mut meetings) = (0, 0);

    for day_doc in doc["days"].as_array().expect("days") {
        let day = day_doc["day"].as_i64().unwrap();
        let before = problems.len();
        let draws = &day_doc["draws"];

        // ── 1. DAILY ──────────────────────────────────────────────────────
        let mut tape = Tape::new(tape_of(&draws["daily"]), format!("day {day} daily"));
        let advance = engine.advance_day_with(
            &DayAdvanceRequest {
                volatility,
                active_shocks: &[],
                market_return_pct: 0.0,
                game_day: day,
                timestamp: day * 24 * 60,
            },
            &mut tape,
        );
        problems.extend(tape.finish());
        if advance.phase_changed {
            transitions += 1;
        }
        if advance.meeting_held {
            meetings += 1;
        }

        for (label, got, want) in [
            (
                "phaseChanged",
                advance.phase_changed,
                day_doc["phaseChanged"].as_bool().unwrap(),
            ),
            (
                "meetingHeld",
                advance.meeting_held,
                day_doc["meetingHeld"].as_bool().unwrap(),
            ),
        ] {
            if got != want {
                problems.push(format!("day {day} {label}: rust={got} ts={want}"));
            }
        }

        // ── 2. OPEN ───────────────────────────────────────────────────────
        // Zero draws, asserted: a draw hidden here would shift the stream
        // once per simulated day, slowly enough to look like a modelling
        // difference rather than a bug.
        assert!(
            tape_of(&draws["open"]).is_empty(),
            "day {day}: the TypeScript open reset drew — the schedule has changed"
        );
        engine.open_market();

        // ── 3. MARKET HOURS ───────────────────────────────────────────────
        let tick_tapes = draws["ticks"].as_array().expect("ticks");
        assert_eq!(
            tick_tapes.len() as i64,
            ticks_per_day,
            "day {day} tick count"
        );
        let (mut hour, mut minute) = (9i64, 30i64);
        for (t, tick_tape) in tick_tapes.iter().enumerate() {
            let mut tape = Tape::new(tape_of(tick_tape), format!("day {day} tick {t}"));
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
                &mut tape,
            );
            problems.extend(tape.finish());
            minute += 1;
            if minute >= 60 {
                minute = 0;
                hour += 1;
            }
            // Stop at the first bad tick rather than reporting 390 of them.
            if problems.len() > before {
                break;
            }
        }

        // ── 4. CLOSE ──────────────────────────────────────────────────────
        assert!(
            tape_of(&draws["close"]).is_empty(),
            "day {day}: the TypeScript close drew — the schedule has changed"
        );
        engine.close_market(&DayCloseRequest {
            daily_innovations: &vec![None; roster],
            sector_base_variances: &sector_variances,
        });

        // ── Compare ───────────────────────────────────────────────────────
        let we = &day_doc["economy"];
        let e = engine.economy();
        for (field, got, want) in [
            ("vix", e.vix, we["vix"].as_str().unwrap()),
            (
                "federalFundsRate",
                e.federal_funds_rate,
                we["federalFundsRate"].as_str().unwrap(),
            ),
            (
                "corporateBondYield",
                e.corporate_bond_yield,
                we["corporateBondYield"].as_str().unwrap(),
            ),
            (
                "qePeBoost",
                e.qe_pe_boost,
                we["qePeBoost"].as_str().unwrap(),
            ),
            (
                "inflationRate",
                e.inflation_rate,
                we["inflationRate"].as_str().unwrap(),
            ),
        ] {
            if !agrees(got, bits(want)) {
                problems.push(format!(
                    "day {day} economy.{field}: rust={got:?} ts={:?}",
                    bits(want)
                ));
            }
        }
        if e.cycle_phase.as_str() != we["cyclePhase"].as_str().unwrap() {
            problems.push(format!(
                "day {day} economy.cyclePhase: rust={} ts={}",
                e.cycle_phase.as_str(),
                we["cyclePhase"].as_str().unwrap()
            ));
        }

        let prices = engine.column(PriceField::Price);
        let garch = engine.column(PriceField::GarchVariance);
        for (i, want) in day_doc["companies"].as_array().unwrap().iter().enumerate() {
            let c = &engine.companies()[i];
            let id = want["id"].as_str().unwrap();
            for (field, got, w) in [
                ("price", prices[i], want["price"].as_str().unwrap()),
                (
                    "avgVolume",
                    c.stock.avg_volume,
                    want["avgVolume"].as_str().unwrap(),
                ),
                (
                    "garchVariance",
                    garch[i],
                    want["garchVariance"].as_str().unwrap(),
                ),
            ] {
                if !agrees(got, bits(w)) {
                    problems.push(format!(
                        "day {day} {id}.{field}: rust={got:?} ts={:?}",
                        bits(w)
                    ));
                }
            }
            for (field, got, w) in [
                (
                    "mispricingS",
                    c.stock.mispricing_s,
                    maybe(&want["mispricingS"]),
                ),
                (
                    "mispricingMomentum",
                    c.stock.mispricing_momentum,
                    maybe(&want["mispricingMomentum"]),
                ),
                (
                    "lastDailyReturn",
                    c.stock.last_daily_return,
                    maybe(&want["lastDailyReturn"]),
                ),
            ] {
                if !same_opt(got, w) {
                    problems.push(format!("day {day} {id}.{field}: rust={got:?} ts={w:?}"));
                }
            }
        }

        if problems.len() > before && first_divergence.is_none() {
            first_divergence = Some(day);
            break;
        }
    }

    if let Some(day) = first_divergence {
        problems.insert(0, format!("FIRST DIVERGENCE on day {day} of {days}"));
    }

    println!(
        "{file}: {days} days x {ticks_per_day} ticks x {roster} names, \
         {transitions} transitions, {meetings} meetings, {} draws",
        engine.draws_consumed()
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
fn thirty_days_calm() {
    check("thirty-day-calm.json");
}

#[test]
fn thirty_days_eventful() {
    check("thirty-day-eventful.json");
}

/// The eventful scenario must actually be eventful.
///
/// Without this the file would pass while exercising neither a cycle
/// transition nor a bank meeting — the two interleavings it exists for.
#[test]
fn the_eventful_scenario_exercises_what_it_claims() {
    let doc = load("thirty-day-eventful.json");
    let days = doc["days"].as_array().unwrap();
    let transitions = days
        .iter()
        .filter(|d| d["phaseChanged"].as_bool().unwrap())
        .count();
    let meetings = days
        .iter()
        .filter(|d| d["meetingHeld"].as_bool().unwrap())
        .count();
    println!("eventful: {transitions} transitions, {meetings} meetings across 30 days");
    assert!(
        transitions > 0,
        "no cycle transition — the corpus is weaker than it looks"
    );
    assert!(
        meetings > 0,
        "no bank meeting — the corpus is weaker than it looks"
    );
}
