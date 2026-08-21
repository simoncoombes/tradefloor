//! Parity for `economy`, against the TypeScript.
//!
//! # Why the draws are replayed rather than generated
//!
//! `next_normal` is Box-Muller and routes through `cos`, where Rust and V8
//! disagree on **1.545%** of draws — measured in `prng_normals_full.rs`, first
//! divergence at draw 12. The daily macro step takes nine normals, so a
//! trajectory driven by Rust's own generator would diverge inside the first
//! week, every time, for a reason that belongs entirely to `cos`.
//!
//! Running such a test would prove nothing about the economy and would tempt
//! whoever inherits it to widen the comparator until it passes. So the
//! recorded draws are fed in instead, which splits the two failures:
//!
//! - the economy **arithmetic** is held to bit-parity, with no tolerance;
//! - the **draw schedule** is held exactly, because [`ScriptedRng`] runs dry
//!   if the port asks for one draw too many and leaves a remainder if it asks
//!   for one too few — and it checks the KIND of each draw, so a uniform
//!   taken where a normal was expected fails even if the counts agree.
//!
//! The generator's divergence stays measured in one place — `rng.rs` — rather
//! than being rediscovered at every layer above it.
//!
//! Vectors: `docs/rust-port/goldens/economy-*.json`, from
//! `scripts/rust-port/economy-vectors.ts`, which **asserts** WASM was absent
//! (decisions D1–D3 and D5 all select the JS formulas) rather than assuming it.

use std::fs;
use std::path::PathBuf;

use pretium::economy::*;
use pretium::rng::Rng;
use serde::Deserialize;
use serde_json::Value as Json;

// ── The replay generator ──────────────────────────────────────────────────

#[derive(Deserialize, Clone, Debug)]
struct Draw {
    kind: String,
    bits: String,
}

/// Feeds recorded draws, and fails loudly on any deviation.
struct ScriptedRng {
    tape: Vec<Draw>,
    at: usize,
    /// Set when the port asked for a draw the tape does not have, or of the
    /// wrong kind. Collected rather than panicking so one run can report the
    /// whole day rather than the first surprise.
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
                "{}: draw #{} is a '{}' in the TypeScript but the port asked for a '{want_kind}' — \
                 same count, different schedule",
                self.label,
                self.at + 1,
                d.kind
            ));
        }
        self.at += 1;
        f64::from_bits(u64::from_str_radix(&d.bits, 16).expect("bad draw bits"))
    }

    /// Check the tape was fully consumed. Leftovers mean the port took FEWER
    /// draws, which is just as much a divergence as taking too many.
    fn finish(mut self) -> Vec<String> {
        if self.at < self.tape.len() {
            self.problems.push(format!(
                "{}: {} draws left unconsumed ({} of {} taken) — \
                 the port takes FEWER draws than the TypeScript",
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

fn agrees(got: f64, want: f64) -> bool {
    got.to_bits() == want.to_bits() || (got.is_nan() && want.is_nan())
}

fn load(name: &str) -> Json {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens")
        .join(name);
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}\nRun: npx tsx scripts/rust-port/economy-vectors.ts",
            path.display()
        )
    });
    serde_json::from_str(&raw).unwrap_or_else(|e| panic!("malformed {name}: {e}"))
}

/// The vectors must have been generated with WASM absent — otherwise they
/// encode production's formulas, not the decided ones.
fn assert_js_oracle(doc: &Json, name: &str) {
    let ready = doc["meta"]["wasmEconomyReady"].as_bool().unwrap_or_else(|| {
        panic!("{name}: meta.wasmEconomyReady missing — the generator must record which oracle ran")
    });
    assert!(
        !ready,
        "{name} was generated with WASM READY. Decisions D1-D3/D5 all select the JS \
         formulas, so these vectors describe the wrong era."
    );
}

/// Field-by-field comparison of an economy state against its recorded form.
fn check_economy(problems: &mut Vec<String>, note: &str, got: &EconomyState, want: &Json) -> usize {
    let mut checked = 0;
    let mut cmp = |field: &str, g: f64| {
        let w = want[field]
            .as_str()
            .unwrap_or_else(|| panic!("{note}: field {field} missing from the vector"));
        if !agrees(g, bits(w)) {
            problems.push(format!(
                "{note} .{field}: rust={g:?} ({:016X})  ts={:?} ({w})",
                g.to_bits(),
                bits(w)
            ));
        }
        checked += 1;
    };

    cmp("federalFundsRate", got.federal_funds_rate);
    cmp("primeRate", got.prime_rate);
    cmp("corporateBondYield", got.corporate_bond_yield);
    cmp("treasuryYield10Y", got.treasury_yield_10y);
    cmp("treasuryYield2Y", got.treasury_yield_2y);
    cmp("mortgageRate30Y", got.mortgage_rate_30y);
    cmp("cpi", got.cpi);
    cmp("inflationRate", got.inflation_rate);
    cmp("coreInflation", got.core_inflation);
    cmp("gdpGrowth", got.gdp_growth);
    cmp("gdp", got.gdp);
    cmp("unemploymentRate", got.unemployment_rate);
    cmp("jobsCreated", got.jobs_created);
    cmp("laborForceParticipation", got.labor_force_participation);
    cmp("usdIndex", got.usd_index);
    cmp("oilPrice", got.oil_price);
    cmp("goldPrice", got.gold_price);
    cmp("copperPrice", got.copper_price);
    cmp("housingIndex", got.housing_index);
    cmp("homeStartsMonthly", got.home_starts_monthly);
    cmp("housingTransactionVolume", got.housing_transaction_volume);
    cmp("longTermUnemploymentRate", got.long_term_unemployment_rate);
    cmp("structuralUnemployment", got.structural_unemployment);
    cmp("consumerConfidence", got.consumer_confidence);
    cmp("businessConfidence", got.business_confidence);
    cmp("fearGreedIndex", got.fear_greed_index);
    cmp("vix", got.vix);
    cmp("tariffRate", got.tariff_rate);
    cmp("tradeBalance", got.trade_balance);
    cmp("oilInventoryLevel", got.oil_inventory_level);
    cmp("wageGrowth", got.wage_growth);
    cmp("previousDayMarketReturn", got.previous_day_market_return);
    cmp("rollingMarketReturn30D", got.rolling_market_return_30d);
    cmp("qePeBoost", got.qe_pe_boost);
    cmp("fiscalStimulus", got.fiscal_stimulus);
    cmp("governmentDebtToGDP", got.government_debt_to_gdp);
    cmp("monthsInCurrentPhase", got.months_in_current_phase);
    cmp("recessionProbability", got.recession_probability);

    // Non-float fields, which a float loop would silently skip.
    let want_phase = want["cyclePhase"].as_str().expect("cyclePhase");
    if got.cycle_phase.as_str() != want_phase {
        problems.push(format!(
            "{note} .cyclePhase: rust={} ts={want_phase}",
            got.cycle_phase.as_str()
        ));
    }
    let want_opec = want["oilLastOpecDay"].as_i64().expect("oilLastOpecDay");
    if got.oil_last_opec_day != want_opec {
        problems.push(format!(
            "{note} .oilLastOpecDay: rust={} ts={want_opec}",
            got.oil_last_opec_day
        ));
    }
    checked += 2;
    checked
}

fn report(name: &str, problems: Vec<String>, checked: usize) {
    println!("{name}: {checked} values checked");
    if !problems.is_empty() {
        let shown: Vec<&str> = problems.iter().take(25).map(|s| s.as_str()).collect();
        panic!(
            "{} mismatches in {name} (first {} shown):\n\n  {}\n",
            problems.len(),
            shown.len(),
            shown.join("\n  ")
        );
    }
}

// ── Tier-1 islands ────────────────────────────────────────────────────────

#[test]
fn initial_state_matches_bit_for_bit() {
    let doc = load("economy-tier1.json");
    assert_js_oracle(&doc, "economy-tier1.json");

    let mut problems = Vec::new();
    let mut checked = 0;

    for case in doc["initialState"].as_array().expect("initialState") {
        let options = if case["input"].is_string() {
            // The no-argument call.
            InitialEconomyOptions::default()
        } else {
            let i = &case["input"];
            InitialEconomyOptions {
                cycle_phase: CyclePhase::from_name(i["cyclePhase"].as_str().expect("cyclePhase")),
                inflation_rate: Some(bits(i["inflationRate"].as_str().unwrap())),
                gdp_growth: Some(bits(i["gdpGrowth"].as_str().unwrap())),
                unemployment_rate: Some(bits(i["unemploymentRate"].as_str().unwrap())),
            }
        };
        let note = format!("createInitialEconomyState({})", case["input"]);
        let got = create_initial_economy_state(&options);
        checked += check_economy(&mut problems, &note, &got, &case["output"]);
    }

    report("economy-tier1.json initialState", problems, checked);
}

#[test]
fn cycle_transition_probability_matches_bit_for_bit() {
    // No draws anywhere in this function, so it is a hard Tier-1 gate — and
    // it is the only place `weibull_hazard`'s `pow` is exercised, across
    // shapes either side of 1.0 where the exponent changes sign.
    let doc = load("economy-tier1.json");
    assert_js_oracle(&doc, "economy-tier1.json");

    let base = create_initial_economy_state(&InitialEconomyOptions::default());
    let mut problems = Vec::new();
    let mut pow_exceptions: Vec<String> = Vec::new();
    let mut checked = 0;
    let mut nonzero = 0;

    for case in doc["cycleProbability"]
        .as_array()
        .expect("cycleProbability")
    {
        let i = &case["input"];
        let mut e = base.clone();
        e.cycle_phase = CyclePhase::from_name(i["cyclePhase"].as_str().unwrap()).unwrap();
        e.months_in_current_phase = bits(i["monthsInCurrentPhase"].as_str().unwrap());
        e.inflation_rate = bits(i["inflationRate"].as_str().unwrap());
        e.federal_funds_rate = bits(i["federalFundsRate"].as_str().unwrap());
        e.unemployment_rate = bits(i["unemploymentRate"].as_str().unwrap());
        e.gdp_growth = bits(i["gdpGrowth"].as_str().unwrap());
        e.treasury_yield_2y = bits(i["treasuryYield2Y"].as_str().unwrap());
        e.treasury_yield_10y = bits(i["treasuryYield10Y"].as_str().unwrap());
        e.market_pe = i["marketPE"].as_str().map(bits);

        let (p, next) = get_cycle_transition_probability(&e);
        let want_p = bits(case["output"]["probability"].as_str().unwrap());
        let want_next = case["output"]["nextPhase"].as_str().unwrap();

        if !agrees(p, want_p) {
            // The ONE documented Tier-2 exception in this file. `weibull_hazard`
            // calls `pow(t, shape - 1)` with a non-integer exponent, and
            // `libm::pow` differs from V8 by one ULP there — measured, and
            // confined to `expansion` (shape 1.8, so exponent 0.8).
            //
            // `std::pow` matches V8 on these inputs, but the crate cannot use
            // it: `std` delegates to the platform libm, which would make the
            // same Python wheel produce different numbers on Linux and
            // Windows. For a library selling reproducible markets that is
            // disqualifying, so one ULP against one browser is the better
            // trade.
            //
            // Handled per §0.1's policy — recorded as an exception with a
            // bounded ULP distance AND a bounded count, never by loosening the
            // comparator. A real defect landing here will either exceed one
            // ULP or push the count past the recorded ceiling.
            let ulp = (p.to_bits() as i64 - want_p.to_bits() as i64).abs();
            if ulp <= 1 {
                pow_exceptions.push(format!(
                    "{} months={:?}: rust={p:?} ts={want_p:?} ({ulp} ULP)",
                    e.cycle_phase.as_str(),
                    e.months_in_current_phase
                ));
            } else {
                problems.push(format!(
                    "probability(phase={}, months={:?}): rust={p:?} ts={want_p:?} — {ulp} ULP, beyond the documented 1-ULP `pow` exception",
                    e.cycle_phase.as_str(),
                    e.months_in_current_phase
                ));
            }
        }
        if next.as_str() != want_next {
            problems.push(format!(
                "nextPhase(phase={}): rust={} ts={want_next}",
                e.cycle_phase.as_str(),
                next.as_str()
            ));
        }
        if p > 0.0 {
            nonzero += 1;
        }
        checked += 2;
    }

    // A sweep that only produced zeros would pass while testing nothing.
    assert!(
        nonzero > 50,
        "only {nonzero} cases produced a non-zero probability — the sweep is not \
         reaching the hazard function"
    );
    println!("  {nonzero} cases with a non-zero transition probability");

    // The ceiling is the MEASURED count, not a round number with slack. Slack
    // is what absorbs the next real defect silently.
    const RECORDED_POW_EXCEPTIONS: usize = 9;
    println!(
        "  {} one-ULP `pow` exceptions (recorded ceiling {RECORDED_POW_EXCEPTIONS})",
        pow_exceptions.len()
    );
    if pow_exceptions.len() > RECORDED_POW_EXCEPTIONS {
        problems.push(format!(
            "{} one-ULP `pow` exceptions, up from the recorded {RECORDED_POW_EXCEPTIONS}. Re-measure before raising this:\n    {}",
            pow_exceptions.len(),
            pow_exceptions.join("\n    ")
        ));
    }

    report("economy-tier1.json cycleProbability", problems, checked);
}

// ── Trajectories ──────────────────────────────────────────────────────────

fn shock_kind(name: &str) -> ShockKind {
    match name {
        "oil_shock" => ShockKind::OilShock,
        "pandemic" => ShockKind::Pandemic,
        "war" => ShockKind::War,
        _ => ShockKind::Other,
    }
}

fn check_trajectory(file: &str) {
    let doc = load(file);
    assert_js_oracle(&doc, file);

    let spec = &doc["spec"];
    let volatility = bits(spec["volatility"].as_str().unwrap());
    let run_central_bank = spec["centralBank"].as_bool().unwrap();
    let start_phase = CyclePhase::from_name(spec["startPhase"].as_str().unwrap()).unwrap();

    let mut problems: Vec<String> = Vec::new();
    let mut checked = 0;

    // The opening state is rebuilt from the SPEC, not read from the vector,
    // so `create_initial_economy_state` is on the hook for it too.
    let mut economy = create_initial_economy_state(&InitialEconomyOptions {
        cycle_phase: Some(start_phase),
        inflation_rate: doc["initialEconomy"]["inflationRate"].as_str().map(bits),
        gdp_growth: None,
        unemployment_rate: None,
    });
    // Re-seed the fields the spec overrode, from the recorded opening state.
    // Cleaner than duplicating the spec's option plumbing, and it still leaves
    // every DERIVED opening field (spreads, confidence, VIX) to be checked.
    let init = &doc["initialEconomy"];
    economy.inflation_rate = bits(init["inflationRate"].as_str().unwrap());
    economy.core_inflation = bits(init["coreInflation"].as_str().unwrap());
    economy.gdp_growth = bits(init["gdpGrowth"].as_str().unwrap());
    economy.unemployment_rate = bits(init["unemploymentRate"].as_str().unwrap());
    economy.consumer_confidence = bits(init["consumerConfidence"].as_str().unwrap());
    economy.business_confidence = bits(init["businessConfidence"].as_str().unwrap());
    checked += check_economy(&mut problems, "initialEconomy", &economy, init);

    let mut central_bank = create_initial_central_bank_state(0);

    let mut first_divergence: Option<i64> = None;

    for day_doc in doc["days"].as_array().expect("days") {
        let day = day_doc["day"].as_i64().unwrap();
        let market_return = bits(day_doc["marketReturn"].as_str().unwrap());

        let shocks: Vec<EconomicShock> = day_doc["shocks"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| EconomicShock {
                kind: shock_kind(s["type"].as_str().unwrap()),
                severity: bits(s["severity"].as_str().unwrap()),
                gdp_impact: bits(s["gdpImpact"].as_str().unwrap()),
            })
            .collect();

        let before = problems.len();

        // ── daily ─────────────────────────────────────────────────────────
        let tape: Vec<Draw> =
            serde_json::from_value(day_doc["draws"]["daily"].clone()).expect("daily tape");
        let mut rng = ScriptedRng::new(tape, format!("day {day} daily"));
        economy = update_economy_daily(
            &economy,
            &DailyInputs {
                volatility,
                active_shocks: &shocks,
                market_return_pct: market_return,
                game_day: day,
            },
            &mut rng,
        );
        problems.extend(rng.finish());

        // ── cycle ─────────────────────────────────────────────────────────
        let tape: Vec<Draw> =
            serde_json::from_value(day_doc["draws"]["cycle"].clone()).expect("cycle tape");
        let mut rng = ScriptedRng::new(tape, format!("day {day} cycle"));
        let phase_before = economy.cycle_phase;
        economy = check_cycle_transition(&economy, &mut rng);
        problems.extend(rng.finish());

        let transitioned = economy.cycle_phase != phase_before;
        let want_transitioned = day_doc["transitioned"].as_bool().unwrap();
        if transitioned != want_transitioned {
            problems.push(format!(
                "day {day}: transitioned rust={transitioned} ts={want_transitioned}"
            ));
        }
        checked += 1;

        // ── central bank ──────────────────────────────────────────────────
        if run_central_bank {
            let tape: Vec<Draw> = serde_json::from_value(day_doc["draws"]["centralBank"].clone())
                .expect("central bank tape");
            let mut rng = ScriptedRng::new(tape, format!("day {day} centralBank"));
            let outcome = update_central_bank(&central_bank, &economy, day * 24 * 60, &mut rng);
            problems.extend(rng.finish());
            central_bank = outcome.central_bank;
            economy = outcome.economy;

            let met = outcome.decision.is_some();
            let want_met = day_doc["met"].as_bool().unwrap();
            if met != want_met {
                problems.push(format!("day {day}: met rust={met} ts={want_met}"));
            }
            checked += 1;

            let cb = &day_doc["centralBankState"];
            for (field, g, w) in [
                (
                    "targetInflation",
                    central_bank.target_inflation,
                    cb["targetInflation"].as_str().unwrap(),
                ),
                (
                    "targetUnemployment",
                    central_bank.target_unemployment,
                    cb["targetUnemployment"].as_str().unwrap(),
                ),
                (
                    "qeMonthlyPurchases",
                    central_bank.qe_monthly_purchases,
                    cb["qeMonthlyPurchases"].as_str().unwrap(),
                ),
                (
                    "hawkishDovishScore",
                    central_bank.hawkish_dovish_score,
                    cb["hawkishDovishScore"].as_str().unwrap(),
                ),
            ] {
                if !agrees(g, bits(w)) {
                    problems.push(format!(
                        "day {day} centralBank.{field}: rust={g:?} ts={:?}",
                        bits(w)
                    ));
                }
                checked += 1;
            }
            for (field, g, w) in [
                (
                    "nextMeetingDate",
                    central_bank.next_meeting_date,
                    cb["nextMeetingDate"].as_i64().unwrap(),
                ),
                (
                    "lastMeetingDate",
                    central_bank.last_meeting_date,
                    cb["lastMeetingDate"].as_i64().unwrap(),
                ),
            ] {
                if g != w {
                    problems.push(format!("day {day} centralBank.{field}: rust={g} ts={w}"));
                }
                checked += 1;
            }
            if central_bank.qe_active != cb["qeActive"].as_bool().unwrap() {
                problems.push(format!(
                    "day {day} centralBank.qeActive: rust={} ts={}",
                    central_bank.qe_active,
                    cb["qeActive"].as_bool().unwrap()
                ));
            }
            checked += 1;
        }

        checked += check_economy(
            &mut problems,
            &format!("day {day}"),
            &economy,
            &day_doc["economy"],
        );

        if problems.len() > before && first_divergence.is_none() {
            first_divergence = Some(day);
        }
        // Once the states differ the two are simulating different economies
        // and every later day is noise. Report where it started.
        if first_divergence.is_some() {
            break;
        }
    }

    if let Some(day) = first_divergence {
        problems.insert(
            0,
            format!(
                "FIRST DIVERGENCE on day {day} of {}",
                doc["days"].as_array().unwrap().len()
            ),
        );
    }

    report(file, problems, checked);
}

#[test]
fn trajectory_calm_expansion() {
    check_trajectory("economy-trajectory-calm-expansion.json");
}

#[test]
fn trajectory_volatile_contraction() {
    check_trajectory("economy-trajectory-volatile-contraction.json");
}

#[test]
fn trajectory_stagflation() {
    check_trajectory("economy-trajectory-stagflation.json");
}

#[test]
fn trajectory_active_shocks() {
    check_trajectory("economy-trajectory-active-shocks.json");
}

#[test]
fn trajectory_no_central_bank() {
    check_trajectory("economy-trajectory-no-central-bank.json");
}

// ── The central bank, swept directly ──────────────────────────────────────

#[test]
fn central_bank_sweep_matches_bit_for_bit() {
    // The trajectories reach the central bank only along the paths their own
    // dynamics happen to take, which left D1's distinguishing feature
    // ungated: across 32 trajectory meetings the `hawkishDovishScore` term
    // never moved a decision across a threshold, so deleting it from the
    // Taylor rule still passed. Found by mutation, not by reading.
    //
    // This grid drives the score to values that DO move the decision, which
    // is the only way to observe a term that is otherwise swallowed inside an
    // unexported intermediate.
    let doc = load("economy-tier1.json");
    assert_js_oracle(&doc, "economy-tier1.json");

    let base = create_initial_economy_state(&InitialEconomyOptions::default());
    let mut problems = Vec::new();
    let mut checked = 0;
    let mut decisions_seen = std::collections::BTreeSet::new();

    for case in doc["centralBank"].as_array().expect("centralBank") {
        let i = &case["input"];
        let mut e = base.clone();
        e.cycle_phase = CyclePhase::from_name(i["cyclePhase"].as_str().unwrap()).unwrap();
        e.inflation_rate = bits(i["inflationRate"].as_str().unwrap());
        e.unemployment_rate = bits(i["unemploymentRate"].as_str().unwrap());
        e.gdp_growth = bits(i["gdpGrowth"].as_str().unwrap());
        e.federal_funds_rate = bits(i["federalFundsRate"].as_str().unwrap());
        e.vix = bits(i["vix"].as_str().unwrap());

        let mut cb = create_initial_central_bank_state(0);
        cb.hawkish_dovish_score = bits(i["hawkishDovishScore"].as_str().unwrap());
        cb.next_meeting_date = -1;

        let tape: Vec<Draw> = serde_json::from_value(case["draws"].clone()).expect("tape");
        let note = format!(
            "cb(phase={}, infl={:?}, unemp={:?}, gdp={:?}, fed={:?}, hd={:?})",
            e.cycle_phase.as_str(),
            e.inflation_rate,
            e.unemployment_rate,
            e.gdp_growth,
            e.federal_funds_rate,
            cb.hawkish_dovish_score
        );
        let mut rng = ScriptedRng::new(tape, note.clone());
        let outcome = update_central_bank(&cb, &e, 1000, &mut rng);
        problems.extend(rng.finish());

        if let Some(d) = outcome.decision {
            decisions_seen.insert(format!("{d:?}"));
        }

        checked += check_economy(
            &mut problems,
            &note,
            &outcome.economy,
            &case["output"]["economy"],
        );

        let want_cb = &case["output"]["centralBank"];
        for (field, g, w) in [
            (
                "hawkishDovishScore",
                outcome.central_bank.hawkish_dovish_score,
                want_cb["hawkishDovishScore"].as_str().unwrap(),
            ),
            (
                "qeMonthlyPurchases",
                outcome.central_bank.qe_monthly_purchases,
                want_cb["qeMonthlyPurchases"].as_str().unwrap(),
            ),
        ] {
            if !agrees(g, bits(w)) {
                problems.push(format!("{note} .{field}: rust={g:?} ts={:?}", bits(w)));
            }
            checked += 1;
        }
        if outcome.central_bank.next_meeting_date != want_cb["nextMeetingDate"].as_i64().unwrap() {
            problems.push(format!(
                "{note} .nextMeetingDate: rust={} ts={}",
                outcome.central_bank.next_meeting_date,
                want_cb["nextMeetingDate"].as_i64().unwrap()
            ));
        }
        if outcome.central_bank.qe_active != want_cb["qeActive"].as_bool().unwrap() {
            problems.push(format!(
                "{note} .qeActive: rust={} ts={}",
                outcome.central_bank.qe_active,
                want_cb["qeActive"].as_bool().unwrap()
            ));
        }
        checked += 2;
    }

    // Coverage of the LADDER, not just of the cases. A sweep that only ever
    // reached `Hold` would pass while testing one branch out of seven.
    println!("  decisions exercised: {decisions_seen:?}");
    assert!(
        decisions_seen.len() >= 5,
        "only {} distinct decisions reached ({decisions_seen:?}) — the sweep is not \
         exercising the policy ladder",
        decisions_seen.len()
    );

    report("economy-tier1.json centralBank", problems, checked);
}
