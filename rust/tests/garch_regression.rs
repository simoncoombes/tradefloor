//! Regression gates for the GARCH surface whose reference parity was
//! retired.
//!
//! # What this file is, and is not
//!
//! The 2026-08-21 retirement (D-P1 in the design log) took the external
//! oracle away from `update_garch_variance`: the GJR asymmetry term landed
//! with the parameters retuned (γ 0.34, α 0.09 → 0.02, β 0.90 → 0.80,
//! effective persistence held at 0.99), so no recorded reference vector can
//! gate this path any more — `market_islands_parity.rs` and the
//! garchVariance halves of `market_daily_parity.rs` keep the measured
//! divergence replayable under `--ignored`, and this file carries the live
//! coverage that replaces them.
//!
//! **These tests gate REGRESSION, not correctness.** Every pinned value
//! below was measured by running this crate against itself at the shipped
//! constants. `sync-goldens.py`'s one-way rule exists precisely because a
//! self-anchored test passes forever regardless of whether the model is
//! right; that criticism applies to this file in full, is accepted, and is
//! the cost D-P1 records. What these pins catch is the arithmetic CHANGING
//! when nobody meant it to — a re-associated sum (the guarded `+=` after
//! the reference three-term sum is contractual, `garch.rs`), a flipped
//! clamp order, an edited constant that forgot its tests, a strict `< 0`
//! guard becoming `<= 0`. What they cannot catch is the model being
//! consistently wrong. Correctness of the GJR calibration is argued by
//! `tools/calibration/sweep_gjr_gamma.py` and the realism panel, which
//! argue distributions, not bits; the model PROPERTIES (leverage
//! asymmetry, clustering, bounds, decay, stationarity) are pinned by
//! `garch.rs`'s own unit tests and are deliberately not repeated here.
//!
//! # When these pins are allowed to move
//!
//! On a deliberate retune of the constants, and then only. The constants
//! test fails first and names the procedure; every assertion prints the
//! measured bits, so re-pinning is transcription from the failure output —
//! after confirming the change was intended, never to get past a red run.
//! Unlike the retired parity suites, a failure here is a live alarm, not a
//! recorded divergence: nothing in this file is expected to fail.
//!
//! # One coverage note
//!
//! The chain pins include negative-return drives. The retired reference
//! chains never had any — all four drove non-negative returns, which is
//! exactly why the forward-note's discriminator could use them to separate
//! a retune from a pure asymmetry term. In the GJR era the leverage branch
//! is where the model lives, so the chains that gate it here exercise the
//! path the reference vectors never reached.

use pretium::market::garch::{
    ALPHA, BETA, CEILING_MULTIPLE, FLOOR_MULTIPLE, GAMMA, OMEGA,
};
use pretium::market::update_garch_variance;

/// The representative sector daily variance the pins are stated at: a 1.5%
/// daily sigma, `sectorBaseDailyVariance`'s default and the same anchor
/// `garch.rs`'s unit tests use.
const BASE: f64 = 0.015 * 0.015;

fn bits(hex: u64) -> f64 {
    f64::from_bits(hex)
}

/// The era the pins were measured at. This fails FIRST on any retune, so
/// the pin failures below read as consequences with a named cause rather
/// than a mystery: if the constant change was deliberate (a calibration
/// decision, recorded like the γ sweep), re-measure every pin in this file
/// from the failure output; if it was not, the constant is the regression.
#[test]
fn the_shipped_era_constants_are_what_the_pins_were_measured_at() {
    let eras: [(&str, f64, f64); 6] = [
        ("OMEGA", OMEGA, 0.000002),
        ("ALPHA", ALPHA, 0.02),
        ("BETA", BETA, 0.80),
        ("GAMMA", GAMMA, 0.34),
        ("CEILING_MULTIPLE", CEILING_MULTIPLE, 5.0),
        ("FLOOR_MULTIPLE", FLOOR_MULTIPLE, 0.25),
    ];
    for (name, got, want) in eras {
        assert_eq!(
            got.to_bits(),
            want.to_bits(),
            "{name} = {got:?}, but the pins in this file were measured at {want:?} — \
             if this retune is deliberate, re-measure them; if not, this is the regression"
        );
    }
}

/// Single-step known answers, bit-exact, measured on this build 2026-08-21.
///
/// The rows are chosen to hold every branch of the update: both signs of an
/// interior return, exact zero (the γ guard is STRICTLY `< 0` and must not
/// fire), a ±1e-9 pair one guard-decision apart (their outputs differ, so a
/// weakened guard flips bits here), both clamps, decay from the ceiling,
/// and the crossed-bounds case where a zero base makes `max(min(..))`
/// return the floor for every input.
#[test]
fn single_step_pins_hold_bit_for_bit() {
    #[rustfmt::skip]
    let pins: [(&str, f64, f64, f64, u64); 9] = [
        ("up_2pct_interior",         BASE,        0.02,   BASE, 0x3F28E757928E0C9E),
        ("down_2pct_interior",       BASE,       -0.02,   BASE, 0x3F355D5F56A7AC82),
        ("zero_return",              BASE,        0.0,    BASE, 0x3F27DAE81882ADC5),
        ("tiny_positive",            BASE,        1e-9,   BASE, 0x3F27DAE81882ADC6),
        ("tiny_negative",            BASE,       -1e-9,   BASE, 0x3F27DAE81882ADD3),
        ("down_8pct_ceiling",        BASE,       -0.08,   BASE, 0x3F526E978D4FDF3B),
        ("quiet_low_var_floor",      0.05 * BASE, 0.0,    BASE, 0x3F0D7DBF487FCB92),
        ("decay_from_ceiling",       5.0 * BASE,  0.0,    BASE, 0x3F4D8E8640208180),
        ("crossed_bounds_zero_base", 0.01,        0.5,    0.0,  0x0000000000000000),
    ];

    for (name, v, r, base, want) in pins {
        let got = update_garch_variance(v, r, base);
        assert_eq!(
            got.to_bits(),
            want,
            "{name}: update({v:?}, {r:?}, {base:?}) = {got:?} ({:016X}), \
             pinned {:?} ({want:016X})",
            got.to_bits(),
            bits(want)
        );
    }

    // The guard-boundary pair must actually straddle the guard: if the
    // ±1e-9 rows ever pin to the same bits, they have stopped witnessing
    // the strictness of `< 0` and need re-choosing, not re-measuring.
    assert_ne!(
        update_garch_variance(BASE, 1e-9, BASE).to_bits(),
        update_garch_variance(BASE, -1e-9, BASE).to_bits(),
        "the tiny_positive/tiny_negative pair no longer separates the sign guard"
    );
}

/// Runs a 60-day chain from `BASE` and returns (day 0, day 59, a
/// position-dependent fold of all 60 days). The fold is rotate-left-1 then
/// XOR, so a single-bit change on ANY day changes it — a plain XOR would
/// cancel on periodic chains like `alternating_extremes`, which visits
/// exactly two values thirty times each.
fn run_chain(drive: impl Fn(usize) -> f64) -> (u64, u64, u64) {
    let mut v = BASE;
    let mut d0 = 0u64;
    let mut fold = 0u64;
    for d in 0..60 {
        v = update_garch_variance(v, drive(d), BASE);
        if d == 0 {
            d0 = v.to_bits();
        }
        fold = fold.rotate_left(1) ^ v.to_bits();
    }
    (d0, v.to_bits(), fold)
}

/// Compounding chains, bit-exact, measured on this build 2026-08-21.
///
/// The first four are the retired reference chains' shapes, re-anchored to
/// this era; the last two drive NEGATIVE returns, exercising the leverage
/// branch day after day — the path the reference vectors never reached.
/// The fold makes every one of the 60 days load-bearing, not just the two
/// endpoints; single-step agreement plus 60-day agreement is what the
/// retired chains proved about compounding, minus the external oracle.
#[test]
fn sixty_day_chain_pins_hold_bit_for_bit() {
    #[rustfmt::skip]
    let pins: [(&str, fn(usize) -> f64, u64, u64, u64); 6] = [
        ("calm",
            |_| 0.0,
            0x3F27DAE81882ADC5, 0x3F0D7DBF487FCB92, 0x3EEAB0782442DBBE),
        ("single_shock_then_quiet",
            |d| if d == 0 { 0.08 } else { 0.0 },
            0x3F3450EFDC9C4DAA, 0x3F0D7DBF487FCB92, 0x876A2DC74F487927),
        ("sustained_crisis",
            |_| 0.06,
            0x3F30A569B17481B2, 0x3F383F90F1F3DDCE, 0xF047A198700B37E3),
        ("alternating_extremes",
            |d| if d % 2 == 0 { 0.9 } else { 0.0 },
            0x3F526E978D4FDF3B, 0x3F4D8E8640208180, 0x0B9B3E2F3CFBC3FD),
        ("single_down_shock_then_quiet",
            |d| if d == 0 { -0.08 } else { 0.0 },
            0x3F526E978D4FDF3B, 0x3F0D7DBF487FCB92, 0x5635F627711D9B9A),
        ("alternating_signs",
            |d| if d % 2 == 0 { 0.03 } else { -0.03 },
            0x3F2A36E2EB1C432D, 0x3F4F212B1461DEFA, 0xDAA66414288528EE),
    ];

    for (name, drive, want_d0, want_d59, want_fold) in pins {
        let (d0, d59, fold) = run_chain(drive);
        assert_eq!(
            d0, want_d0,
            "chain '{name}' day 0: got {:?} ({d0:016X}), pinned {:?} ({want_d0:016X})",
            bits(d0),
            bits(want_d0)
        );
        assert_eq!(
            d59, want_d59,
            "chain '{name}' day 59: got {:?} ({d59:016X}), pinned {:?} ({want_d59:016X})",
            bits(d59),
            bits(want_d59)
        );
        assert_eq!(
            fold, want_fold,
            "chain '{name}': the 60-day fold moved ({fold:016X} vs pinned {want_fold:016X}) \
             with both endpoints in place — some interior day changed"
        );
    }
}
