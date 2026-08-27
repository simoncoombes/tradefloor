//! Smoke parity against real output from the reference implementation.
//!
//! These values were produced by running the reference implementation's PRNG
//! and printing raw IEEE-754 bit patterns, not decimals — a decimal comparison
//! can agree while the underlying doubles differ, which is exactly the failure
//! this crate must not ship.
//!
//! This is a SMOKE test, deliberately small. The exhaustive golden vectors are
//! generated separately (see `goldens/`). The purpose here is to
//! fail fast and loudly during development, before anyone waits on the full
//! harness.
//!
//! The `next_normal` case is the one that matters: it is the first point in the
//! port where implementation-defined maths (`ln`, `sin`, `cos`) is involved,
//! and therefore the first place V8 and Rust can legitimately disagree.

use pretium::GameRng;

/// Parse the `f64` bit patterns emitted by the reference implementation's probe.
fn f64s(hex_csv: &str) -> Vec<f64> {
    hex_csv
        .split(',')
        .map(|h| f64::from_bits(u64::from_str_radix(h.trim(), 16).expect("bad hex")))
        .collect()
}

fn assert_stream(label: &str, actual: Vec<f64>, expected_hex: &str) {
    let expected = f64s(expected_hex);
    assert_eq!(actual.len(), expected.len(), "{label}: length mismatch");
    for (i, (got, want)) in actual.iter().zip(expected.iter()).enumerate() {
        assert_eq!(
            got.to_bits(),
            want.to_bits(),
            "{label}[{i}]: rust={got:?} ({:016x}) ts={want:?} ({:016x})",
            got.to_bits(),
            want.to_bits()
        );
    }
}

#[test]
fn uniform_floats_match_the_reference() {
    let mut rng = GameRng::from_seed(42);
    let got: Vec<f64> = (0..6).map(|_| rng.next_f64()).collect();
    assert_stream(
        "seed42 seq0 floats",
        got,
        "3fc0dbab77000000,3fe82bdeea000000,3fe2a91537a00000,3fcaeda146800000,3fee00e2c9200000,3fe448700fe00000",
    );
}

#[test]
fn sequence_one_matches_the_reference() {
    let mut rng = GameRng::new(42, 1);
    let got: Vec<f64> = (0..4).map(|_| rng.next_f64()).collect();
    assert_stream(
        "seed42 seq1 floats",
        got,
        "3fd37c733e400000,3fecb070ea400000,3fd63b6784000000,3fee7c6f6a200000",
    );
}

#[test]
fn seed_zero_matches_the_reference() {
    let mut rng = GameRng::from_seed(0);
    let got: Vec<f64> = (0..4).map(|_| rng.next_f64()).collect();
    assert_stream(
        "seed0 seq0 floats",
        got,
        "3fec9828f1000000,3fcbce328b000000,3fd712aceec00000,3fd80748f8000000",
    );
}

/// 2^31 exercises the top bit of the seed, where a sign-extension mistake in
/// the port would show up.
#[test]
fn seed_two_to_the_31_matches_the_reference() {
    let mut rng = GameRng::from_seed(2147483648);
    let got: Vec<f64> = (0..4).map(|_| rng.next_f64()).collect();
    assert_stream(
        "seed 2^31 seq0 floats",
        got,
        "3fe4e46327200000,3fc539d917800000,3fd72644df800000,3fe43d703ca00000",
    );
}

#[test]
fn ranged_ints_match_the_reference() {
    let mut rng = GameRng::from_seed(42);
    let got: Vec<i64> = (0..8).map(|_| rng.next_int(0, 99)).collect();
    assert_eq!(got, vec![70, 84, 29, 69, 97, 99, 66, 90]);
}

/// THE ONE THAT MATTERS.
///
/// Box-Muller, so `ln`, `sin` and `cos` are all involved. If V8's libm and
/// Rust's disagree even in the last ULP, this is where it surfaces — and
/// because normal draws feed the GARCH noise term for every company on every
/// tick, a mismatch here means the whole market diverges.
#[test]
fn normal_draws_match_the_reference() {
    let mut rng = GameRng::from_seed(42);
    let got: Vec<f64> = (0..6).map(|_| rng.next_normal()).collect();
    assert_stream(
        "seed42 seq0 normals",
        got,
        "3fb1576bc104d98f,c000196ca28368f0,3fd060b43a7a1aa9,3ff01ae243f65b7c,bfcea265ccb0f3ba,bfd11f0ce42f4ec9",
    );
}
