// Quantify the weibullHazard divergence: how many, how large, and is it pow?
use pretium::economy::*;
use serde_json::Value;

fn bits(h: &str) -> f64 {
    f64::from_bits(u64::from_str_radix(h, 16).unwrap())
}

fn main() {
    let raw = std::fs::read_to_string(concat!(
        env!("CARGO_MANIFEST_DIR"),
        "/goldens/economy-tier1.json"
    ))
    .unwrap();
    let doc: Value = serde_json::from_str(&raw).unwrap();
    let base = create_initial_economy_state(&InitialEconomyOptions::default());

    let (mut n, mut bad, mut max_ulp) = (0u32, 0u32, 0i64);
    let mut worst = String::new();
    for case in doc["cycleProbability"].as_array().unwrap() {
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

        let (p, _) = get_cycle_transition_probability(&e);
        let want = bits(case["output"]["probability"].as_str().unwrap());
        n += 1;
        if p.to_bits() != want.to_bits() {
            bad += 1;
            let ulp = (p.to_bits() as i64 - want.to_bits() as i64).abs();
            if ulp > max_ulp {
                max_ulp = ulp;
                worst = format!(
                    "{} months={} rust={p:?} ts={want:?}",
                    e.cycle_phase.as_str(),
                    e.months_in_current_phase
                );
            }
        }
    }
    println!(
        "hazard probability: {bad} of {n} differ ({:.3}%), max {max_ulp} ULP",
        100.0 * bad as f64 / n as f64
    );
    println!("worst: {worst}");

    // Is it pow, and would std have matched? The crate forbids std maths, but
    // knowing the answer decides whether this is a libm defect or a genuine
    // no-single-answer case.
    println!("\n  shape  scale  months   libm vs std");
    let mut libm_only = 0;
    for (phase, (shape, scale)) in [
        ("expansion", cycle_hazard_params(CyclePhase::Expansion)),
        ("peak", cycle_hazard_params(CyclePhase::Peak)),
        ("contraction", cycle_hazard_params(CyclePhase::Contraction)),
        ("trough", cycle_hazard_params(CyclePhase::Trough)),
        ("recovery", cycle_hazard_params(CyclePhase::Recovery)),
    ] {
        for months in [1.0f64, 4.0, 12.0, 24.0, 36.0, 60.0, 120.0, 600.0] {
            let t = months / scale;
            let l = libm::pow(t, shape - 1.0);
            let s = t.powf(shape - 1.0);
            if l.to_bits() != s.to_bits() {
                libm_only += 1;
                println!("  {phase:<12} m={months:<6} libm={l:?} std={s:?}");
            }
        }
    }
    println!("  {libm_only} of 40 pow inputs where libm and std disagree");
}
