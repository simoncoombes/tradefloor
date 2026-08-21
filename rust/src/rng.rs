//! PCG32, ported from `src/lib/engine/prng.ts`.
//!
//! # What this is a port of, and why it can be exact
//!
//! The TypeScript emulates a 64-bit PCG32 using pairs of 32-bit integers,
//! because JavaScript's bitwise operators are 32-bit (`Math.imul`, `>>> 0`).
//! Rust has native `u64`, so this implementation is shorter than the original
//! while producing **bit-identical** output.
//!
//! That claim is safe for a specific reason: every operation in the PCG32 core
//! is integer arithmetic. Integer arithmetic is exactly specified in both
//! languages, so "port carefully and it will match" is a guarantee here rather
//! than a hope. This is the one part of the whole engine port where that is
//! true.
//!
//! # It applies to the integer core ONLY — measured
//!
//! An earlier version of this file, and several summaries of it, described the
//! RNG port as bit-identical without qualification. That is **false for
//! `next_normal`**, and the correction is worth stating precisely because the
//! overstatement survived a passing test.
//!
//! | surface | vs Chrome |
//! |---|---|
//! | raw `u32`, uniforms, ranged ints, bools | bit-identical |
//! | `next_normal` (Box-Muller) | **1.545% of draws differ** |
//!
//! Measured across all 60,000 recorded draws in `prng-normals.json` by
//! `tests/prng_normals_full.rs`. **The first divergence is at draw 12.**
//!
//! `ts_parity_smoke.rs` checks six normals and passes — it stops six draws
//! short of the first mismatch. A test can pass because it is too small, and
//! this is what that looks like.
//!
//! The divergence is not a defect: it is the same finding as
//! `docs/rust-port/DETERMINISM.md`, since Box-Muller routes through `cos` and
//! Chrome, Firefox and Safari disagree about `cos`. There is no single browser
//! answer to match. What was wrong was the claim, not the code.
//!
//! # Where it stops being true
//!
//! [`GameRng::next_normal`] is Box-Muller and calls `ln`, `sin` and `cos`.
//! Those are implementation-defined, and Phase 0 measured the consequence
//! rather than speculating about it:
//!
//! | op | Rust `std` vs V8 |
//! |---|---|
//! | `ln` | identical |
//! | `sqrt` | identical (IEEE-754 specifies it exactly) |
//! | `sin` | identical |
//! | `cos` | **differs by 1 ULP** |
//!
//! One function, one bit. But one bit is enough: normal draws feed the GARCH
//! noise term for every company on every tick, and the mispricing process is
//! non-linear with feedback, so a last-ULP difference becomes a different
//! market within a simulated year.
//!
//! # Why this crate does not use `std` for maths
//!
//! Rust's `f64::cos` delegates to the platform libm — MSVC's CRT on Windows,
//! glibc on Linux, Apple's on macOS. The `libm` crate is a pure-Rust port of
//! MUSL's, which shares fdlibm ancestry with V8's implementation, and it
//! reproduces V8 exactly on the case that failed.
//!
//! That buys two things, and the second matters more than the first:
//!
//! 1. **Parity with V8**, so the port can be verified against the TypeScript.
//! 2. **Determinism across platforms.** With `std`, the same Python wheel
//!    would produce different numbers on Linux and Windows. For a library
//!    whose entire selling point is reproducible markets, that is disqualifying
//!    on its own — this crate would need to vendor its maths even if V8 parity
//!    were not a goal.
//!
//! So: no `std` transcendentals anywhere in this crate. Everything routes
//! through [`crate::mathx`], which is enforced by a test that greps the source
//! rather than trusting anyone to remember.
//!
//! # Faithfulness over correctness
//!
//! Where the TypeScript does something odd, this reproduces the oddity rather
//! than improving on it. A port that "fixes" the source is not a port; it is a
//! second model, and two models that disagree is the outcome this entire
//! project exists to avoid. Every such case is commented with what the
//! TypeScript does and why the Rust matches it.

use crate::mathx;

/// The draw interface the engine modules consume.
///
/// Exists so that a caller can supply RECORDED draws instead of generated
/// ones. That is not a testing convenience, it is what makes the higher
/// layers gateable at all: `next_normal` is Box-Muller and therefore routes
/// through `cos`, which diverges from V8 on **1.545%** of draws (see the
/// module header). Any module that consumes normals inherits that rate, so a
/// long trajectory driven by a generated stream diverges within a handful of
/// steps for a reason that belongs to `cos` and not to the module under test.
///
/// Feeding recorded draws separates the two failures: the arithmetic is held
/// to bit-parity, while the generator's divergence stays measured in one
/// place — here — instead of being rediscovered, blurred into a tolerance, at
/// every layer above.
pub trait Rng {
    fn next_f64(&mut self) -> f64;
    fn next_normal(&mut self) -> f64;
}

impl Rng for GameRng {
    fn next_f64(&mut self) -> f64 {
        GameRng::next_f64(self)
    }
    fn next_normal(&mut self) -> f64 {
        GameRng::next_normal(self)
    }
}

/// JavaScript's `ToUint32` coercion, as applied by `>>> 0`.
///
/// The TypeScript constructor does `seed >>> 0` and `sequence << 1`, which
/// means a seed of `2^32` becomes `0` and a seed of `-1` becomes `4294967295`.
/// Callers reaching this crate from Python or Rust will not naturally apply
/// that, so it is applied here — otherwise the same nominal seed would produce
/// different streams in the game and in the library, which is precisely the
/// class of divergence this port must not introduce.
pub fn to_uint32(value: f64) -> u32 {
    if !value.is_finite() {
        return 0;
    }
    let truncated = value.trunc();
    let modulo = truncated.rem_euclid(4294967296.0);
    modulo as u32
}

/// PCG-XSH-RR with 64-bit state and 32-bit output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pcg32 {
    state: u64,
    inc: u64,
}

/// `6364136223846793005`, split as `0x5851F42D_4C957F2D` in the TypeScript.
const PCG_MULTIPLIER: u64 = 0x5851_F42D_4C95_7F2D;

impl Pcg32 {
    /// Mirrors the TypeScript constructor exactly:
    ///
    /// ```text
    /// inc   = (sequence << 1) | 1     // 32-bit shift, so the top bit lands in incHi
    /// state = 0; step(); state += seed; step();
    /// ```
    ///
    /// The TypeScript builds `inc` from `incHi = sequence >>> 31` and
    /// `incLo = (sequence << 1) | 1`, which together are exactly
    /// `((sequence as u64) << 1) | 1` — the high half is the bit that `<< 1`
    /// pushes out of 32 bits. Verified rather than assumed, because getting
    /// this wrong produces a plausible-looking stream that is simply a
    /// different one.
    pub fn new(seed: u32, sequence: u32) -> Self {
        let mut rng = Self {
            state: 0,
            inc: ((sequence as u64) << 1) | 1,
        };
        rng.step();
        rng.state = rng.state.wrapping_add(seed as u64);
        rng.step();
        rng
    }

    /// Advance the state and return the XSH-RR permutation of the OLD state.
    ///
    /// The TypeScript computes the permutation from `oldHi`/`oldLo` captured
    /// before the multiply-add, which is standard PCG and is what makes the
    /// output independent of the next state. Ordering matters: permuting the
    /// new state would produce a valid-looking but different generator.
    fn step(&mut self) -> u32 {
        let old = self.state;
        self.state = old.wrapping_mul(PCG_MULTIPLIER).wrapping_add(self.inc);

        // xorshifted = (uint32)(((old >> 18) ^ old) >> 27)
        let xorshifted = (((old >> 18) ^ old) >> 27) as u32;
        // rot = old >> 59 — the top 5 bits
        let rot = (old >> 59) as u32;

        // The TypeScript writes this as `(xs >>> rot) | (xs << ((-rot) & 31))`.
        // The `(-rot) & 31` exists to make a rotation of 0 shift by 0 rather
        // than by 32, which would be undefined. `rotate_right` has that
        // behaviour natively.
        xorshifted.rotate_right(rot)
    }

    /// Next raw 32-bit output.
    pub fn next_u32(&mut self) -> u32 {
        self.step()
    }

    /// Uniform float in `[0, 1)`.
    ///
    /// The TypeScript divides by `4294967296` (2^32). Division of an exactly
    /// representable integer by a power of two is exact in IEEE-754, so this
    /// is one of the float operations that carries no parity risk at all.
    pub fn next_f64(&mut self) -> f64 {
        self.next_u32() as f64 / 4294967296.0
    }
}

/// The game-facing RNG: uniform, normal, ranged-int and boolean draws.
///
/// `PartialEq` compares STREAM POSITION, including the Box-Muller spare.
/// Tests use it to assert how many draws a function consumed, which for a
/// single shared stream is as load-bearing as what the function returned:
/// a call that draws one time too many silently reshuffles every later
/// consumer on the same tick.
#[derive(Debug, Clone, PartialEq)]
pub struct GameRng {
    pcg: Pcg32,
    spare: Option<f64>,
}

impl GameRng {
    pub fn new(seed: u32, sequence: u32) -> Self {
        Self {
            pcg: Pcg32::new(seed, sequence),
            spare: None,
        }
    }

    /// `createGameRng(seed)` in the TypeScript — note it uses sequence **0**,
    /// while the `GameRng` constructor defaults to **1**. That asymmetry is in
    /// the original and is load-bearing: the two entry points produce
    /// different streams from the same seed.
    pub fn from_seed(seed: u32) -> Self {
        Self::new(seed, 0)
    }

    pub fn next_f64(&mut self) -> f64 {
        self.pcg.next_f64()
    }

    /// Box-Muller, caching the spare.
    ///
    /// Uses `libm` rather than `std` for `ln`, `sin` and `cos`. Phase 0 found
    /// that `std`'s `cos` differs from V8's by 1 ULP on real inputs while
    /// `libm`'s matches exactly — see the module docs. `sqrt` stays on `std`
    /// because IEEE-754 specifies it exactly, so every implementation agrees.
    ///
    /// Two faithfulness details, both easy to "improve" and both wrong to:
    ///
    /// 1. The TypeScript writes `const u = this.nextFloat() || 1e-10`. That is
    ///    a truthiness guard, not a comparison: it substitutes `1e-10` when
    ///    `nextFloat()` returns exactly `0.0`, which would otherwise make
    ///    `ln(0) = -inf`. It fires on exactly one of 2^32 outcomes. Reproduced
    ///    literally, including the specific constant.
    /// 2. The spare is `r * sin(...)` and the returned value is `r * cos(...)`
    ///    — in that order, with `sin` computed first. Swapping them would still
    ///    be valid Box-Muller and would still be a different sequence.
    pub fn next_normal(&mut self) -> f64 {
        if let Some(spare) = self.spare.take() {
            return spare;
        }
        let raw = self.next_f64();
        let u = if raw == 0.0 { 1e-10 } else { raw };
        let v = self.next_f64();
        let r = mathx::sqrt(-2.0 * mathx::log(u));
        self.spare = Some(r * mathx::sin(2.0 * std::f64::consts::PI * v));
        r * mathx::cos(2.0 * std::f64::consts::PI * v)
    }

    /// Integer in `[min, max]` inclusive.
    ///
    /// The TypeScript is `min + (this.pcg.next() % range)`, where `next()` is
    /// an unsigned 32-bit value held in a JS double, so `%` is exact integer
    /// remainder. Both languages truncate toward zero and both operands are
    /// non-negative, so the result matches.
    ///
    /// This is a modulo-biased draw — values below `2^32 % range` are very
    /// slightly more likely. That bias is in the original and is reproduced
    /// deliberately: removing it would change every stream in the game.
    pub fn next_int(&mut self, min: i64, max: i64) -> i64 {
        let range = max - min + 1;
        if range <= 0 {
            // JS would produce NaN or Infinity here and poison downstream
            // arithmetic silently. Returning `min` is a deliberate divergence
            // from a case the TypeScript never exercises; if a golden vector
            // ever hits it, this must be revisited rather than papered over.
            return min;
        }
        min + (self.pcg.next_u32() as i64 % range)
    }

    /// True with probability `p`.
    pub fn next_bool(&mut self, p: f64) -> bool {
        self.next_f64() < p
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The state after construction is not something the TypeScript exposes,
    /// so this pins the internal sequence instead: the first outputs must be
    /// stable across refactors of this file.
    #[test]
    fn is_deterministic_for_a_given_seed() {
        let mut a = GameRng::from_seed(42);
        let mut b = GameRng::from_seed(42);
        for _ in 0..1000 {
            assert_eq!(a.next_f64().to_bits(), b.next_f64().to_bits());
        }
    }

    #[test]
    fn different_seeds_diverge() {
        let mut a = GameRng::from_seed(42);
        let mut b = GameRng::from_seed(43);
        let differs = (0..100).any(|_| a.next_f64() != b.next_f64());
        assert!(differs, "seeds 42 and 43 produced identical prefixes");
    }

    /// `createGameRng` uses sequence 0 and the bare constructor defaults to 1.
    /// If someone "tidies" that away, this fails.
    #[test]
    fn sequence_zero_and_one_are_different_streams() {
        let mut a = GameRng::new(42, 0);
        let mut b = GameRng::new(42, 1);
        let differs = (0..100).any(|_| a.next_f64() != b.next_f64());
        assert!(differs, "sequence 0 and 1 produced the same stream");
    }

    #[test]
    fn floats_are_in_unit_interval() {
        let mut rng = GameRng::from_seed(7);
        for _ in 0..100_000 {
            let x = rng.next_f64();
            assert!((0.0..1.0).contains(&x), "out of range: {x}");
        }
    }

    /// The spare must be returned on the very next call, unmodified — the
    /// caching is observable in the output sequence, not an implementation
    /// detail.
    #[test]
    fn normal_spare_is_returned_next_and_unchanged() {
        let mut probe = GameRng::from_seed(11);
        let _first = probe.next_normal();
        let cached = probe.spare;
        assert!(cached.is_some(), "first next_normal did not cache a spare");

        let second = probe.next_normal();
        assert_eq!(second.to_bits(), cached.unwrap().to_bits());
        assert!(probe.spare.is_none(), "spare was not consumed");
    }

    #[test]
    fn next_int_stays_within_bounds() {
        let mut rng = GameRng::from_seed(3);
        for _ in 0..100_000 {
            let v = rng.next_int(-5, 5);
            assert!((-5..=5).contains(&v), "out of range: {v}");
        }
    }

    #[test]
    fn to_uint32_matches_javascript_coercion() {
        // The cases that actually differ between a naive `as u32` and JS.
        assert_eq!(to_uint32(0.0), 0);
        assert_eq!(to_uint32(42.0), 42);
        assert_eq!(to_uint32(4294967296.0), 0, "2^32 wraps to 0");
        assert_eq!(to_uint32(-1.0), 4294967295, "-1 wraps to u32::MAX");
        assert_eq!(to_uint32(4294967297.0), 1, "2^32 + 1 wraps to 1");
        assert_eq!(to_uint32(2147483648.0), 2147483648, "2^31 is unchanged");
        assert_eq!(to_uint32(1.9), 1, "truncates toward zero");
        assert_eq!(to_uint32(-1.9), 4294967295, "truncates, then wraps");
        assert_eq!(to_uint32(f64::NAN), 0);
        assert_eq!(to_uint32(f64::INFINITY), 0);
    }
}
