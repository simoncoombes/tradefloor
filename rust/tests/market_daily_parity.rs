//! Bit-identical parity for the daily lifecycle — WP4, part 3.
//!
//! The close bookkeeping takes no draws, so this is a hard Tier-1 gate with no
//! exceptions of any kind.
//!
//! Everything here runs under `AvgVolumePolicy::ReferenceEma`, which is the
//! policy's whole reason to exist: these vectors are recorded reference runs,
//! and bit-fidelity to the reference -- its volume EMA included -- is the
//! property under test. The SHIPPED policy deliberately holds `avg_volume`
//! fixed instead; that divergence is argued in `market/daily.rs` and tested
//! there.
//!
//! **Provenance caveat, carried from the generator.** `resetDailyPrices` and
//! `updateGarchVariance` are called for real, but the momentum roll,
//! innovation selection and volume EMA are transcribed from
//! `transitions.ts:105-128` rather than invoked — `tickMarketTransitions`
//! needs a full TickWork/TickState/TickContext. That means these vectors can
//! go stale silently if `transitions.ts` changes. Nothing here detects that;
//! the mitigation is the source lines recorded in the generator so a reviewer
//! can diff them.
//!
//! # The garchVariance assertions retired, 2026-08-21 (D-P1)
//!
//! The forward-note this section used to carry anticipated the GJR stream
//! and said what to do when it landed: retire the garchVariance
//! ASSERTIONS, not this file. It landed — γ 0.34 with α and β retuned
//! 0.09/0.90 → 0.02/0.80 — and that is what happened, by splitting each
//! affected test in two:
//!
//! - `close_day_matches_bit_for_bit` and
//!   `five_hundred_day_chains_match_without_drift` stay LIVE on every
//!   field that shares no path with the fork: the momentum roll,
//!   `lastDailyReturn`, `sPrevClose` and the ReferenceEma avgVolume arm
//!   (`reset_daily_prices` never touched GARCH at all). `close_day`
//!   writes `garch_variance` but nothing else in it reads the value
//!   (`daily.rs`), so the surviving assertions gate exactly the reference
//!   behaviour they always did — now through the chains' full 500 days,
//!   which the combined test could only reach while GARCH still agreed.
//! - the split-off `*_garch_variance_*` tests carry the retired
//!   assertions under `#[ignore]`, replaying the same closes through the
//!   same harness: `cargo test --test market_daily_parity -- --ignored`
//!   reproduces the measured divergence, and both are EXPECTED to fail —
//!   19,980 of the 43,200 point cases (the rest pin to the sector clamp
//!   bounds, where the old and new parameterisations agree) and all
//!   three chains at day 0. A retired test that PASSES means the corpus
//!   was regenerated from a constants-matched source, which D-P2
//!   forecloses: an incident, not a fix.
//!
//! Both halves of the note's prediction resolved correctly — the
//! garchVariance assertions failed, and the parameters having been
//! retuned, the chains failed from day 0. The measured detail, including
//! the discriminator that separated a retune from a pure asymmetry term,
//! is in `market_islands_parity.rs`'s header. Replacement coverage for
//! the retired surface is `garch_regression.rs` — self-anchored, gating
//! regression and never correctness, as its header says — plus
//! `garch.rs`'s own property tests.

use std::fs;
use std::path::PathBuf;

use pretium::market::*;
use serde_json::Value as Json;

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

fn load() -> Json {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens/market-daily.json");
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}\nRun: npx tsx scripts/rust-port/market-daily-vectors.ts",
            path.display()
        )
    });
    let doc: Json = serde_json::from_str(&raw).expect("malformed market-daily.json");
    assert!(!doc["meta"]["wasmReady"].as_bool().expect("meta.wasmReady"));
    doc
}

/// A company carrying only what the close reads.
fn company(price: f64, previous_close: f64) -> TickCompany {
    TickCompany {
        id: "x".into(),
        ticker: "X".into(),
        sector: "technology".into(),
        is_bankrupt: false,
        is_public: true,
        sector_volatility: Some(1.0),
        sector_avg_pe: Some(32.0),
        eps: Some(4.0),
        book_value_per_share: Some(20.0),
        revenue_growth: Some(0.1),
        stock: TickStock {
            price,
            previous_close,
            previous_tick_price: None,
            open: 0.0,
            high: 0.0,
            low: 0.0,
            volume: 0.0,
            avg_volume: 0.0,
            shares_outstanding: 1e8,
            market_cap: 0.0,
            mispricing_s: None,
            mispricing_s_prev_close: None,
            mispricing_momentum: None,
            maker_inventory: None,
            garch_variance: 0.0,
            garch_cascade: [0.015 * 0.015; pretium::market::garch::CASCADE_MAX],
            last_daily_return: None,
            beta: Some(1.0),
            short_interest: 0.0,
            float: 1e8,
        },
    }
}

fn report(name: &str, problems: Vec<String>, checked: usize) {
    println!("{name}: {checked} values checked");
    if !problems.is_empty() {
        let shown: Vec<&str> = problems.iter().take(20).map(|s| s.as_str()).collect();
        panic!(
            "{} mismatches in {name} (first {} shown):\n\n  {}\n",
            problems.len(),
            shown.len(),
            shown.join("\n  ")
        );
    }
}

/// One recorded case replayed through `close_day`, identically for the live
/// test and the retired one: the split (module header) is in what each
/// asserts afterwards, never in what runs.
fn replay_case(case: &Json) -> (TickCompany, String) {
    let i = &case["in"];
    let mut c = company(
        bits(i["price"].as_str().unwrap()),
        bits(i["previousClose"].as_str().unwrap()),
    );
    c.sector = i["sector"].as_str().unwrap().to_string();
    c.stock.volume = bits(i["volume"].as_str().unwrap());
    c.stock.avg_volume = bits(i["avgVolume"].as_str().unwrap());
    c.stock.garch_variance = bits(i["garchVariance"].as_str().unwrap());
    c.stock.mispricing_s = maybe(&i["mispricingS"]);
    c.stock.mispricing_s_prev_close = maybe(&i["mispricingSPrevClose"]);
    c.stock.mispricing_momentum = Some(bits(i["mispricingMomentum"].as_str().unwrap()));

    close_day(
        &mut c,
        &CloseInputs {
            daily_innovation: maybe(&i["dailyInnovation"]),
            // Passed in rather than looked up, following the crate's
            // convention: the sector table lives in the TypeScript.
            sector_base_daily_variance: bits(i["sectorBaseDailyVariance"].as_str().unwrap()),
            vix: 15.0,
            avg_volume: AvgVolumePolicy::ReferenceEma,
        },
    );

    let note = format!(
        "close(sector={}, price={}, prevClose={}, vol={}, innov={:?}, s={:?})",
        i["sector"],
        i["price"],
        i["previousClose"],
        i["volume"],
        i["dailyInnovation"],
        i["mispricingS"]
    );
    (c, note)
}

/// LIVE. Everything the close writes except `garch_variance`: the momentum
/// roll, `lastDailyReturn`, `sPrevClose` and the ReferenceEma avgVolume arm
/// share no path with the GJR fork (`close_day` writes the variance and
/// nothing else in it reads the value), so bit-parity with the reference
/// remains a true claim here. The `garchVariance` assertion this test
/// carried until 2026-08-21 is retired below.
#[test]
fn close_day_matches_bit_for_bit() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;

    for case in doc["cases"].as_array().expect("cases") {
        let (c, note) = replay_case(case);
        let o = &case["out"];

        if !agrees(c.stock.avg_volume, bits(o["avgVolume"].as_str().unwrap())) {
            problems.push(format!(
                "{note} .avgVolume: rust={:?} ts={:?}",
                c.stock.avg_volume,
                bits(o["avgVolume"].as_str().unwrap())
            ));
        }
        checked += 1;
        for (field, got, want) in [
            (
                "lastDailyReturn",
                c.stock.last_daily_return,
                maybe(&o["lastDailyReturn"]),
            ),
            (
                "mispricingMomentum",
                c.stock.mispricing_momentum,
                maybe(&o["mispricingMomentum"]),
            ),
            (
                "mispricingSPrevClose",
                c.stock.mispricing_s_prev_close,
                maybe(&o["mispricingSPrevClose"]),
            ),
        ] {
            if !same_opt(got, want) {
                problems.push(format!("{note} .{field}: rust={got:?} ts={want:?}"));
            }
            checked += 1;
        }
    }

    report("market-daily.json cases", problems, checked);
}

/// The retired half of `close_day_matches_bit_for_bit`: the same replay,
/// asserting only `garchVariance`. Expected to FAIL under `-- --ignored`
/// with 19,980 mismatches of 43,200 — the cases that do not pin to the
/// sector clamp bounds, where every parameterisation agrees. A PASS means
/// the corpus changed and needs investigating, because D-P2 forecloses
/// regenerating it.
#[test]
#[ignore = "retired 2026-08-21 (D-P1): reference recorded symmetric GARCH (alpha 0.09, beta 0.90); model moved to GJR (gamma 0.34, alpha 0.02, beta 0.80); expected to fail under --ignored"]
fn close_day_garch_variance_matches_bit_for_bit() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;

    for case in doc["cases"].as_array().expect("cases") {
        let (c, note) = replay_case(case);
        let want = bits(case["out"]["garchVariance"].as_str().unwrap());
        if !agrees(c.stock.garch_variance, want) {
            problems.push(format!(
                "{note} .garchVariance: rust={:?} ts={want:?}",
                c.stock.garch_variance
            ));
        }
        checked += 1;
    }

    report("market-daily.json cases (garchVariance)", problems, checked);
}

/// Walks one recorded chain through `close_day`, feeding the evolving state
/// forward exactly as the combined test always did: `avg_volume` and
/// `garch_variance` from the close's own output, `s` on its recorded
/// deterministic path. Shared by the live chain test and the retired one,
/// so the split is in what each asserts, never in what runs. `visit`
/// returns `true` to stop the walk — once an asserted, fed-forward value
/// has diverged, the two sides are integrating different state.
///
/// The GARCH state deliberately keeps evolving under the SHIPPED (forked)
/// parameters here, in both halves: `close_day` writes `garch_variance`
/// but nothing else in it reads the value (`daily.rs`), so the forked
/// variance riding along cannot touch the live assertions — that
/// independence is exactly what let the assertions retire without the
/// file.
fn walk_chain(chain: &Json, base: f64, mut visit: impl FnMut(usize, &TickCompany, &Json) -> bool) {
    let daily_volume = bits(chain["dailyVolume"].as_str().unwrap());
    let mut avg_volume = 1_000_000.0;
    let mut garch_variance = 0.015 * 0.015;
    let mut s = 0.05f64;
    let mut s_prev = 0.0f64;

    for (d, want) in chain["path"].as_array().unwrap().iter().enumerate() {
        let mut c = company(100.0 + (d % 7) as f64, 100.0);
        c.stock.volume = daily_volume;
        c.stock.avg_volume = avg_volume;
        c.stock.garch_variance = garch_variance;
        c.stock.mispricing_s = Some(s);
        c.stock.mispricing_s_prev_close = Some(s_prev);

        close_day(
            &mut c,
            &CloseInputs {
                daily_innovation: None,
                sector_base_daily_variance: base,
                vix: 15.0,
                avg_volume: AvgVolumePolicy::ReferenceEma,
            },
        );

        avg_volume = c.stock.avg_volume;
        garch_variance = c.stock.garch_variance;
        s_prev = c.stock.mispricing_s_prev_close.unwrap();
        s = s * 0.99 + 0.001 * ((d % 5) as f64 - 2.0);

        if visit(d, &c, want) {
            break;
        }
    }
}

/// LIVE. The EMA and the momentum roll across 500 chained closes.
///
/// A single close cannot show the EMA's stability, and stability is the
/// property that matters: `avgVolume` feeds both the volume model and the
/// maker's quote size, so a per-day bias compounds into wider books. Until
/// 2026-08-21 this test also chained `garchVariance` — that assertion is
/// retired below, and shedding it is what lets this one reach all 500 days
/// again instead of stopping at the fork's day-0 divergence.
#[test]
fn five_hundred_day_chains_match_without_drift() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;

    let base = bits(
        doc["cases"][0]["in"]["sectorBaseDailyVariance"]
            .as_str()
            .unwrap(),
    );

    for chain in doc["chains"].as_array().expect("chains") {
        let name = chain["name"].as_str().unwrap();
        walk_chain(chain, base, |d, c, want| {
            let mut diverged = false;
            let got = c.stock.avg_volume;
            let w = bits(want["avgVolume"].as_str().unwrap());
            if !agrees(got, w) {
                problems.push(format!(
                    "chain '{name}' first diverges at day {d} on avgVolume: rust={got:?} ts={w:?}"
                ));
                diverged = true;
            }
            checked += 1;
            if !same_opt(
                c.stock.mispricing_momentum,
                maybe(&want["mispricingMomentum"]),
            ) {
                problems.push(format!(
                    "chain '{name}' day {d} momentum: rust={:?} ts={:?}",
                    c.stock.mispricing_momentum,
                    maybe(&want["mispricingMomentum"])
                ));
                diverged = true;
            }
            checked += 1;

            // After the first bad day the two are integrating different state.
            diverged
        });
    }

    report("market-daily.json chains", problems, checked);
}

/// The retired half of `five_hundred_day_chains_match_without_drift`: the
/// same walk, asserting only the chained `garchVariance`. Expected to FAIL
/// under `-- --ignored` at day 0 of all three chains — the retuned α/β
/// diverge on the very first close, before the leverage term could even
/// matter (the chains' driving returns are all non-negative). A PASS means
/// the corpus changed and needs investigating, because D-P2 forecloses
/// regenerating it.
#[test]
#[ignore = "retired 2026-08-21 (D-P1): reference recorded symmetric GARCH (alpha 0.09, beta 0.90); model moved to GJR (gamma 0.34, alpha 0.02, beta 0.80); expected to fail under --ignored"]
fn five_hundred_day_chains_garch_variance_matches_without_drift() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;

    let base = bits(
        doc["cases"][0]["in"]["sectorBaseDailyVariance"]
            .as_str()
            .unwrap(),
    );

    for chain in doc["chains"].as_array().expect("chains") {
        let name = chain["name"].as_str().unwrap();
        walk_chain(chain, base, |d, c, want| {
            let got = c.stock.garch_variance;
            let w = bits(want["garchVariance"].as_str().unwrap());
            if !agrees(got, w) {
                problems.push(format!(
                    "chain '{name}' first diverges at day {d} on garchVariance: \
                     rust={got:?} ts={w:?}"
                ));
                return true;
            }
            checked += 1;
            false
        });
    }

    report("market-daily.json chains (garchVariance)", problems, checked);
}

#[test]
fn reset_daily_prices_matches_bit_for_bit() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;

    for case in doc["resets"].as_array().expect("resets") {
        let price = bits(case["in"].as_str().unwrap());
        let mut roster = vec![company(price, 1.0)];
        roster[0].stock.open = 2.0;
        roster[0].stock.high = 3.0;
        roster[0].stock.low = 4.0;
        roster[0].stock.volume = 999.0;
        reset_daily_prices(&mut roster);

        let s = &roster[0].stock;
        let o = &case["out"];
        for (field, got, want) in [
            (
                "previousClose",
                s.previous_close,
                o["previousClose"].as_str().unwrap(),
            ),
            ("open", s.open, o["open"].as_str().unwrap()),
            ("high", s.high, o["high"].as_str().unwrap()),
            ("low", s.low, o["low"].as_str().unwrap()),
            ("volume", s.volume, o["volume"].as_str().unwrap()),
        ] {
            if !agrees(got, bits(want)) {
                problems.push(format!(
                    "reset(price={price}) .{field}: rust={got:?} ts={:?}",
                    bits(want)
                ));
            }
            checked += 1;
        }
    }

    report("market-daily.json resets", problems, checked);
}
