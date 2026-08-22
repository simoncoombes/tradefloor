//! Bit-identical parity for `market.ts`'s Tier-1 islands — WP4, part 1.
//!
//! GARCH, session logic, the intraday curves and the index divisor maths.
//! These carry no draws and no hidden state, so they are held to bit equality
//! with one stated exception: the volume curve's `t^2.5` is a non-integer
//! `pow`, the surface the determinism notes §2 documents as differing from V8.
//! Here it reaches **2 ULP on 25 of 391 points** — worse than the hazard
//! function's 1 ULP on 1.29%, because the expression sums two `pow` terms so
//! the error can compound. Bounded on BOTH axes, magnitude and count, from
//! measurement rather than by a round number.
//!
//! Gating them separately from the tick is deliberate. A GARCH defect
//! consumed by the tick would surface as an unexplained price divergence four
//! layers up, and the tick has enough of its own failure modes without
//! inheriting these.
//!
//! # GARCH retired, 2026-08-21 (D-P1)
//!
//! The GJR asymmetry term landed in `garch.rs` with the parameters retuned:
//! α 0.09 → 0.02, β 0.90 → 0.80, γ 0.34, effective persistence held at the
//! reference's 0.99. `garch_matches_bit_for_bit` stopped being a parity
//! surface at that moment and is retired under `#[ignore]` below, body
//! intact: `cargo test --test market_islands_parity -- --ignored` replays
//! it, and it is EXPECTED to fail — 84 mismatches on the committed corpus:
//! 80 of the 420 point cases, across every sign of return, and all four
//! chains, three at day 0 and `alternating-extremes` at day 1 (its 0.9
//! day-0 return drives every parameterisation into the same sector ceiling,
//! so the clamp masks day 0). A retired test that PASSES means the corpus
//! was regenerated from a constants-matched source — which D-P2 forecloses
//! — and is an incident, not a fix.
//!
//! What this test used to prove — that the update reproduces the
//! reference's symmetric GARCH(1,1) bit-for-bit, 420 point cases and four
//! compounding chains — it did prove, up to the era boundary; the migration
//! is verified and the corpus still records it.
//!
//! ## The forward-note's discriminator, resolved
//!
//! The note this section replaces left a prediction: the four recorded
//! `garchChains` drive only non-negative returns, so a PURE asymmetry term
//! (γ alone, parameters otherwise untouched) would leave the chains and the
//! non-negative point cases passing and fail only negative-return cases; if
//! everything failed, chains included, the parameters were retuned too.
//! Everything failed, chains included, and the parameters WERE retuned —
//! the note resolved correctly, and it identified which KIND of change had
//! arrived rather than merely detecting that one had. Re-verified case by
//! case at retirement time: the reference parameterisation reproduces all
//! 420 recorded point cases and all four chains bit-for-bit; a
//! pure-asymmetry variant (γ 0.34 on the reference's α/β) fails exactly 31
//! negative-return cases and nothing else; the shipped retune fails 80
//! cases and every chain. One refinement for the next note-writer:
//! "everything fails" is bounded by the clamp — wherever a case pins to the
//! sector floor or ceiling, every parameterisation agrees, which is what
//! spares 340 of the 420 point cases and `alternating-extremes`' day 0.
//!
//! Replacement coverage: `garch_regression.rs` pins the shipped era's
//! arithmetic against itself — REGRESSION, not correctness; its header says
//! what that does and does not claim — and adds the negative-return chain
//! the reference vectors never drove. The model properties (clustering,
//! leverage asymmetry, bounds, decay) are pinned by `garch.rs`'s own tests,
//! and correctness of the calibration is argued by
//! `tools/calibration/sweep_gjr_gamma.py`, not by any test in this
//! directory. The sessions, curves and index tests below share no path with
//! the fork and stay live.

use std::fs;
use std::path::PathBuf;

use pretium::market::*;
use serde::Deserialize;
use serde_json::Value as Json;

fn bits(hex: &str) -> f64 {
    f64::from_bits(u64::from_str_radix(hex, 16).unwrap_or_else(|e| panic!("bad bits {hex}: {e}")))
}

fn agrees(got: f64, want: f64) -> bool {
    got.to_bits() == want.to_bits() || (got.is_nan() && want.is_nan())
}

fn load() -> Json {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens/market-islands.json");
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}\nRun: npx tsx scripts/rust-port/market-islands-vectors.ts",
            path.display()
        )
    });
    let doc: Json = serde_json::from_str(&raw).expect("malformed market-islands.json");
    assert!(
        !doc["meta"]["wasmReady"].as_bool().expect("meta.wasmReady"),
        "the vectors were generated with WASM ready — they record the wrong era"
    );
    doc
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

// ── GARCH — RETIRED, see the module header ────────────────────────────────

/// Retired whole rather than split: every assertion in it — 420 point cases
/// and four compounding chains — routes through `update_garch_variance`,
/// so no live remainder exists. Expected to FAIL when run with
/// `-- --ignored`; a PASS means the corpus changed and needs investigating,
/// because D-P2 forecloses regenerating it.
#[test]
#[ignore = "retired 2026-08-21 (D-P1): reference recorded symmetric GARCH (alpha 0.09, beta 0.90); model moved to GJR (gamma 0.34, alpha 0.02, beta 0.80); expected to fail under --ignored"]
fn garch_matches_bit_for_bit() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;

    for case in doc["garch"].as_array().expect("garch") {
        let i = &case["in"];
        let cv = bits(i["currentVariance"].as_str().unwrap());
        let r = bits(i["lastDailyReturn"].as_str().unwrap());
        let base = bits(i["sectorBaseVariance"].as_str().unwrap());
        let got = update_garch_variance(cv, r, base);
        let want = bits(case["out"].as_str().unwrap());
        if !agrees(got, want) {
            problems.push(format!(
                "garch(v={cv:?}, r={r:?}, base={base:?}): rust={got:?} ts={want:?}"
            ));
        }
        checked += 1;
    }

    // Compounding chains — a single step cannot show clustering, and
    // clustering is what the model is for. Report the FIRST diverging day:
    // after that the two are integrating different variances.
    for chain in doc["garchChains"].as_array().expect("garchChains") {
        let name = chain["name"].as_str().unwrap();
        let base = bits(chain["base"].as_str().unwrap());
        let path = chain["path"].as_array().unwrap();
        let mut v = base;
        for (d, want_hex) in path.iter().enumerate() {
            let ret = match name {
                "calm" => 0.0,
                "single-shock-then-quiet" => {
                    if d == 0 {
                        0.08
                    } else {
                        0.0
                    }
                }
                "sustained-crisis" => 0.06,
                "alternating-extremes" => {
                    if d % 2 == 0 {
                        0.9
                    } else {
                        0.0
                    }
                }
                other => panic!("unknown chain {other}"),
            };
            v = update_garch_variance(v, ret, base);
            let want = bits(want_hex.as_str().unwrap());
            if !agrees(v, want) {
                problems.push(format!(
                    "chain '{name}' first diverges at day {d}: rust={v:?} ts={want:?}"
                ));
                break;
            }
            checked += 1;
        }
    }

    report("market-islands.json garch", problems, checked);
}

// ── Sessions ──────────────────────────────────────────────────────────────

#[test]
fn session_boundaries_match_exactly() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;
    let mut seen = std::collections::BTreeSet::new();

    for case in doc["sessions"].as_array().expect("sessions") {
        let i = &case["in"];
        let time = GameTime {
            hour: i["hour"].as_i64().unwrap(),
            minute: i["minute"].as_i64().unwrap(),
            day_of_week: i["dayOfWeek"].as_i64().unwrap(),
        };
        let got = get_market_status(time);
        let want = case["out"].as_str().unwrap();
        if got.as_str() != want {
            problems.push(format!(
                "status({}:{:02} dow {}): rust={} ts={want}",
                time.hour,
                time.minute,
                time.day_of_week,
                got.as_str()
            ));
        }
        seen.insert(want.to_string());
        checked += 1;
    }

    // All four sessions must appear, or the sweep is not reaching them.
    assert_eq!(seen.len(), 4, "only reached {seen:?}");
    report("market-islands.json sessions", problems, checked);
}

// ── Intraday curves ───────────────────────────────────────────────────────

#[test]
fn intraday_curves_match_within_the_documented_pow_exception() {
    let doc = load();
    let mut problems = Vec::new();
    let mut pow_exceptions: Vec<i64> = Vec::new();
    let mut checked = 0;

    for case in doc["curves"].as_array().expect("curves") {
        let t = bits(case["t"].as_str().unwrap());

        // Integer exponent — exact, no exception permitted.
        let got_vol = intraday_vol(t);
        let want_vol = bits(case["intradayVol"].as_str().unwrap());
        if !agrees(got_vol, want_vol) {
            problems.push(format!(
                "intradayVol(t={t:?}): rust={got_vol:?} ts={want_vol:?}"
            ));
        }
        checked += 1;

        // `t^2.5` — the documented non-integer `pow` surface.
        let got_volume = intraday_volume(t, MarketStatus::Open);
        let want_volume = bits(case["intradayVolumeOpen"].as_str().unwrap());
        if !agrees(got_volume, want_volume) {
            let ulp = (got_volume.to_bits() as i64 - want_volume.to_bits() as i64).abs();
            if ulp <= MAX_POW_ULP {
                pow_exceptions.push(ulp);
            } else {
                problems.push(format!(
                    "intradayVolume(t={t:?}): rust={got_volume:?} ts={want_volume:?} — {ulp} ULP, \
                     beyond the documented {MAX_POW_ULP}-ULP `pow` exception"
                ));
            }
        }
        checked += 1;
    }

    // Bounded by MEASURED values on both axes — magnitude and count — not by
    // round numbers. Same policy as `economy_parity.rs`.
    //
    // 25 of 391 points differ (6.4%), against the hazard function's 1.29%,
    // and two of them reach 2 ULP rather than 1. Both differences have the
    // same cause: `intradayVolume` evaluates TWO `pow` terms and adds them,
    // so the error can compound, and it is evaluated across the whole unit
    // interval rather than at a handful of durations.
    //
    // **The amplification path was checked rather than assumed.** The curve
    // feeds `Math.floor(volumes[i])` before settlement, and `floor` is a
    // discontinuity — a last-bit difference becomes a whole-share difference
    // whenever the true value sits within a ULP of an integer, which would
    // change `levelsNeeded`, `slice`, and therefore which fills happen.
    // `examples/volume_floor_probe.rs` measures how often that actually
    // happens: **0 of 4,692** representative volume computations floor
    // differently under a one-ULP curve shift. The floor absorbs it, because
    // the volumes are large relative to a ULP of a number near 1.
    //
    // So the exception is benign in practice AND bounded here. The tick
    // vectors should still record volume as an integer, so that if the
    // absorption ever stops holding it fails at the boundary rather than as
    // an unexplained fill difference.
    const MAX_POW_ULP: i64 = 2;
    const RECORDED_POW_EXCEPTIONS: usize = 25;
    let worst = pow_exceptions.iter().copied().max().unwrap_or(0);
    println!(
        "  {} `pow` exceptions in the volume curve, worst {worst} ULP          (recorded ceiling {RECORDED_POW_EXCEPTIONS} at {MAX_POW_ULP} ULP)",
        pow_exceptions.len()
    );
    if pow_exceptions.len() > RECORDED_POW_EXCEPTIONS {
        problems.push(format!(
            "{} `pow` exceptions, up from the recorded {RECORDED_POW_EXCEPTIONS}.              Re-measure before raising this.",
            pow_exceptions.len()
        ));
    }

    report("market-islands.json curves", problems, checked);
}

// ── Index ─────────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct CapEntry {
    id: String,
    cap: String,
    bankrupt: bool,
}

#[test]
fn index_arithmetic_matches_bit_for_bit() {
    let doc = load();
    let mut problems = Vec::new();
    let mut checked = 0;

    for case in doc["indices"].as_array().expect("indices") {
        let i = &case["in"];
        let caps: Vec<CapEntry> =
            serde_json::from_value(i["marketCaps"].clone()).expect("marketCaps");
        let companies: Vec<IndexConstituent> = caps
            .iter()
            .map(|c| IndexConstituent {
                id: c.id.clone(),
                market_cap: bits(&c.cap),
                is_bankrupt: c.bankrupt,
            })
            .collect();
        let component_ids: Vec<String> =
            serde_json::from_value(i["componentIds"].clone()).expect("componentIds");
        let previous_value = bits(i["previousValue"].as_str().unwrap());
        let divisor = bits(i["divisor"].as_str().unwrap());

        let got = calculate_market_index(&companies, &component_ids, previous_value, divisor);
        let out = &case["out"];
        let note = case["note"].as_str().unwrap();

        for (field, g, w) in [
            ("value", got.value, out["value"].as_str().unwrap()),
            ("change", got.change, out["change"].as_str().unwrap()),
            (
                "changePercent",
                got.change_percent,
                out["changePercent"].as_str().unwrap(),
            ),
        ] {
            if !agrees(g, bits(w)) {
                problems.push(format!(
                    "{note} (prev={previous_value:?}, div={divisor:?}) .{field}: \
                     rust={g:?} ts={:?}",
                    bits(w)
                ));
            }
            checked += 1;
        }
    }

    report("market-islands.json indices", problems, checked);
}
