//! Bit-identical parity for the daily lifecycle — WP4, part 3.
//!
//! The close bookkeeping takes no draws, so this is a hard Tier-1 gate with no
//! exceptions of any kind.
//!
//! **Provenance caveat, carried from the generator.** `resetDailyPrices` and
//! `updateGarchVariance` are called for real, but the momentum roll,
//! innovation selection and volume EMA are transcribed from
//! `transitions.ts:105-128` rather than invoked — `tickMarketTransitions`
//! needs a full TickWork/TickState/TickContext. That means these vectors can
//! go stale silently if `transitions.ts` changes. Nothing here detects that;
//! the mitigation is the source lines recorded in the generator so a reviewer
//! can diff them.

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

#[test]
fn close_day_matches_bit_for_bit() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;

    for case in doc["cases"].as_array().expect("cases") {
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
            },
        );

        let o = &case["out"];
        let note = format!(
            "close(sector={}, price={}, prevClose={}, vol={}, innov={:?}, s={:?})",
            i["sector"],
            i["price"],
            i["previousClose"],
            i["volume"],
            i["dailyInnovation"],
            i["mispricingS"]
        );

        for (field, got, want) in [
            (
                "garchVariance",
                c.stock.garch_variance,
                bits(o["garchVariance"].as_str().unwrap()),
            ),
            (
                "avgVolume",
                c.stock.avg_volume,
                bits(o["avgVolume"].as_str().unwrap()),
            ),
        ] {
            if !agrees(got, want) {
                problems.push(format!("{note} .{field}: rust={got:?} ts={want:?}"));
            }
            checked += 1;
        }
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

#[test]
fn five_hundred_day_chains_match_without_drift() {
    // A single close cannot show the EMA's stability, and stability is the
    // property that matters: `avgVolume` feeds both the volume model and the
    // maker's quote size, so a per-day bias compounds into wider books.
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
                },
            );

            avg_volume = c.stock.avg_volume;
            garch_variance = c.stock.garch_variance;
            s_prev = c.stock.mispricing_s_prev_close.unwrap();
            s = s * 0.99 + 0.001 * ((d % 5) as f64 - 2.0);

            let mut diverged = false;
            for (field, got, w) in [
                (
                    "avgVolume",
                    avg_volume,
                    bits(want["avgVolume"].as_str().unwrap()),
                ),
                (
                    "garchVariance",
                    garch_variance,
                    bits(want["garchVariance"].as_str().unwrap()),
                ),
            ] {
                if !agrees(got, w) {
                    problems.push(format!(
                        "chain '{name}' first diverges at day {d} on {field}: rust={got:?} ts={w:?}"
                    ));
                    diverged = true;
                }
                checked += 1;
            }
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
            if diverged {
                break;
            }
        }
    }

    report("market-daily.json chains", problems, checked);
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
