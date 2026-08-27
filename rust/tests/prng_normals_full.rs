//! Consumes the 60,000 recorded normal draws in `prng-normals.json`.
//!
//! Added because the crate claimed `rng.rs` was "bit-identical" while nothing
//! read this file — the only normals ever checked were six values in
//! `reference_parity_smoke.rs`. Box-Muller routes through `cos`, and `mathx`'s own V8
//! sweep shows libm and Chrome disagreeing on roughly 0.75% of cosine inputs,
//! so a mismatch rate near 1% was the prediction. This measures it.
//!
//! Reports rather than asserts exactness, for the same reason as
//! `report_divergence_against_v8`: there is no single browser answer to match.

use std::fs;
use std::path::PathBuf;

use pretium::GameRng;
use serde::Deserialize;

#[derive(Deserialize)]
struct File_ {
    series: Vec<Series>,
}
#[derive(Deserialize)]
struct Series {
    input: Input,
    output: Vec<String>,
}
#[derive(Deserialize)]
struct Input {
    seed: u32,
    sequence: u32,
    draws: usize,
}

#[test]
fn measure_normal_draw_divergence() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens/prng-normals.json");
    let file: File_ = serde_json::from_str(&fs::read_to_string(path).expect("vectors missing"))
        .expect("malformed");

    let (mut total, mut mismatched, mut first_at) = (0usize, 0usize, usize::MAX);
    println!(
        "\n{:<22} {:>8} {:>10} {:>10}",
        "seed/seq", "draws", "mismatch", "first at"
    );
    println!("{}", "-".repeat(54));

    for s in &file.series {
        let mut rng = GameRng::new(s.input.seed, s.input.sequence);
        let (mut n, mut bad, mut first) = (0usize, 0usize, usize::MAX);
        for (i, want_hex) in s.output.iter().take(s.input.draws).enumerate() {
            let want = u64::from_str_radix(want_hex, 16).expect("bad hex");
            let got = rng.next_normal().to_bits();
            n += 1;
            if got != want {
                bad += 1;
                if first == usize::MAX {
                    first = i;
                }
            }
        }
        println!(
            "{:<22} {:>8} {:>9.3}% {:>10}",
            format!("{}/{}", s.input.seed, s.input.sequence),
            n,
            bad as f64 / n as f64 * 100.0,
            if first == usize::MAX {
                "-".to_string()
            } else {
                first.to_string()
            }
        );
        total += n;
        mismatched += bad;
        if first != usize::MAX {
            first_at = first_at.min(first);
        }
    }

    println!("{}", "-".repeat(54));
    println!(
        "{total} draws, {mismatched} mismatched ({:.3}%)",
        mismatched as f64 / total as f64 * 100.0
    );
    if mismatched > 0 {
        println!("first divergence at draw {first_at} — 'bit-identical' is FALSE for normals");
    } else {
        println!("bit-identical across every recorded normal draw");
    }
}
