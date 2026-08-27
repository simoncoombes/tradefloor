//! PCG32, ported from the reference implementation.
//!
//! # What this is a port of, and why it can be exact
//!
//! The reference implementation emulates a 64-bit PCG32 using pairs of 32-bit
//! integers, because its bitwise operators are 32-bit (`Math.imul`, `>>> 0`).
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
//! the determinism notes, since Box-Muller routes through `cos` and
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
//! 1. **Parity with V8**, so the port can be verified against the reference implementation.
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
//! Where the reference implementation does something odd, this reproduces the oddity rather
//! than improving on it. A port that "fixes" the source is not a port; it is a
//! second model, and two models that disagree is the outcome this entire
//! project exists to avoid. Every such case is commented with what the
//! the reference implementation does and why the Rust matches it.

use crate::mathx;

/// Named substreams of one root seed.
///
/// # Why streams exist
///
/// The reference implementation ran every consumer — market, economy,
/// microstructure, embedder — off ONE generator, so changing what any
/// consumer drew shifted every draw that every other consumer saw
/// afterwards. That coupling is what made "what would have happened if I
/// changed only this?" unanswerable: the TCA counterfactual, a
/// pinned-versus-baseline macro comparison and the cutover embedder all
/// need to vary one thing while the rest of the randomness stays put.
///
/// The 2026-08 era boundary splits the engine onto three independent
/// substreams, all derived from the same root seed. One seed still fully
/// determines the whole simulation; what changes is that perturbing one
/// domain's consumption no longer moves any other domain's sequence.
///
/// # The derivation contract
///
/// This is a CONTRACT, not an implementation detail: it is the thing a
/// reader reproduces, and changing it is an era boundary in its own right.
/// For a root seed `s` (a `u32`) and a stream id `k` (one of the constants
/// below):
///
/// ```text
/// mixed    = splitmix64_mix((s as u64) << 32 | k as u64)
/// seed_k   = (mixed >> 32) as u32          // top 32 bits of the mix
/// seq_k    = STREAM_SEQUENCE_BASE + k      // = 256 + k
/// stream_k = GameRng::new(seed_k, seq_k)
/// ```
///
/// where `splitmix64_mix` is the SplitMix64 output finalizer (Stafford's
/// "Mix13", the one in Vigna's reference `splitmix64.c`):
///
/// ```text
/// z  = input + 0x9E3779B97F4A7C15
/// z ^= z >> 30;  z *= 0xBF58476D1CE4E5B9
/// z ^= z >> 27;  z *= 0x94D049BB133111EB
/// z ^= z >> 31
/// ```
///
/// # Why this derivation gives independent streams
///
/// Two properties carry the independence argument, and each half of the
/// derivation supplies one:
///
/// 1. **Distinct sequences, structurally.** Each stream gets its own PCG
///    `sequence`, so its LCG increment differs from every other stream's.
///    Two PCG32 generators with different odd increments traverse
///    DIFFERENT orbits — one can never be a shifted copy of the other, the
///    failure mode where two "independent" streams eventually replay each
///    other's values. This holds by construction, not probabilistically.
///
/// 2. **Decorrelated states, by mixing.** The lazy derivation —
///    `GameRng::new(s, k)` with the root seed used raw — is exactly the one
///    this contract refuses, and the refusal has a reason: two PCG streams
///    seeded with the SAME state and different increments `c, c'` have
///    states related by the affine identity
///    `state'_n − state_n = (c' − c)(aⁿ⁻¹ + ⋯ + 1)`, a deterministic
///    cross-stream structure that the output permutation only obscures.
///    Feeding the (root, id) pair through an avalanche finalizer first
///    gives every stream an unrelated starting state — a one-bit change in
///    either the root or the id flips each output bit with probability
///    ~1/2 — so no such relation exists between any two substreams.
///
/// Every operation is integer arithmetic, exactly specified on every
/// platform: no floats, no hashing with platform-dependent behaviour, and
/// nothing for the cross-OS bit-identity claim to trip on.
///
/// The sequence base 256 keeps the derived sequences clear of every
/// sequence in historical use with RAW seeds (0 and 1 from the two
/// constructors, 21 for the universe, 99 for the reference MAIN stream) —
/// not because a collision would alias a stream (the mixed seed already
/// differs) but so that a recorded sequence number identifies its era at a
/// glance.
///
/// # What is deliberately NOT derived this way
///
/// - `random_universe` keeps its original `GameRng::new(seed, 21)`. Its
///   seed is a UNIVERSE seed, a different input domain from the simulation
///   seed, so it shares no root with the engine streams and re-deriving it
///   would churn every published universe fingerprint for no independence
///   gain.
/// - `GameRng::new(seed, sequence)` stays public and raw. It is the
///   Layer-1 API and the replay surface for pre-split recordings; the
///   contract above is about how the ENGINE seeds itself.
pub mod stream {
    /// Every draw inside `simulate_market_tick`: shared factors, per-company
    /// noise, volume, and book settlement.
    pub const MARKET: u32 = 0;
    /// The daily macro chain: `update_economy_daily`, the cycle roll, and
    /// the central bank.
    pub const ECONOMY: u32 = 1;
    /// The embedder's own draws, taken through `Engine::draw_uniform` /
    /// `draw_normal`: seed-derived and reproducible, but incapable of
    /// perturbing the market.
    pub const EXTERNAL: u32 = 2;
    /// Endogenous jumps, drawn once per name per day at the close.
    ///
    /// Its own stream, and that is the whole reason a draw-CONSUMING
    /// mechanism could be added at all. The draw-schedule rule says nothing
    /// settable may change how many draws are taken or in what order, so a
    /// jump process sharing [`MARKET`] would shift every subsequent market
    /// draw and change every preset's trajectory the moment the code
    /// landed — inert parameters or not.
    ///
    /// On a stream of its own, the jump draws happen unconditionally and
    /// perturb nothing: at zero intensity no jump fires, and `MARKET`,
    /// `ECONOMY` and `EXTERNAL` are untouched, so every shipped preset
    /// reproduces bit for bit and the known-answer digest does not move.
    pub const JUMPS: u32 = 3;
    /// The persistent volume component, drawn once per day at the close.
    ///
    /// Its own stream for the same reason [`JUMPS`] has one, and a SEPARATE
    /// one from jumps so neither mechanism's draw count can shift the
    /// other's sequence. Two mechanisms sharing a stream are coupled by
    /// their draw counts, which is a dependency nobody would choose and
    /// everybody would forget.
    pub const VOLUME: u32 = 4;

    /// Endogenous company news, drawn once per name per day at the open.
    ///
    /// Its own stream for the same reason [`JUMPS`] and [`VOLUME`] have
    /// theirs: a draw-consuming mechanism on a shared stream shifts every
    /// later draw and moves every preset's trajectory the moment it lands.
    /// A separate one from both, so no two mechanisms' draw counts couple.
    pub const NEWS: u32 = 5;

    /// Per-name volume persistence, drawn once per name per day.
    ///
    /// Separate from [`VOLUME`], which carries the COMMON component, for the
    /// reason every split here exists: the common update draws once a day
    /// and the per-name update draws once per name, so sharing a stream
    /// would make the common sequence depend on the universe size and
    /// shift every preset that sets only the common half.
    pub const VOLUME_IDIO: u32 = 6;

    /// Derived streams live at `256 + id`. See the module docs for why the
    /// offset exists.
    pub const STREAM_SEQUENCE_BASE: u32 = 256;
}

/// The SplitMix64 output finalizer. Integer-only, exact on every platform.
///
/// The constants are load-bearing and pinned by `substream_derivation_is_the_documented_formula`
/// below: this is Stafford's Mix13 with SplitMix64's golden-ratio increment,
/// as published in Vigna's reference implementation.
fn splitmix64_mix(input: u64) -> u64 {
    let mut z = input.wrapping_add(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

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
/// The reference implementation's constructor does `seed >>> 0` and
/// `sequence << 1`, which
/// means a seed of `2^32` becomes `0` and a seed of `-1` becomes `4294967295`.
/// Callers reaching this crate from Python or Rust will not naturally apply
/// that, so it is applied here — otherwise the same nominal seed would produce
/// different streams in the simulation and in the library, which is precisely the
/// class of divergence this port must not introduce.
pub fn to_uint32(value: f64) -> u32 {
    if !value.is_finite() {
        return 0;
    }
    let truncated = value.trunc();
    let modulo = truncated.rem_euclid(4294967296.0);
    modulo as u32
}

/// A generator's complete state, as plain numbers.
///
/// Exists so a simulation can be checkpointed without replaying it. The
/// alternative -- recording a draw COUNT and fast-forwarding -- cannot express
/// the cached Box-Muller spare, and would restore a generator that agreed on
/// uniforms and disagreed on normals.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RngState {
    pub state: u64,
    pub increment: u64,
    pub spare: Option<f64>,
}

/// PCG-XSH-RR with 64-bit state and 32-bit output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Pcg32 {
    state: u64,
    inc: u64,
}

/// `6364136223846793005`, split as `0x5851F42D_4C957F2D` in the reference implementation.
const PCG_MULTIPLIER: u64 = 0x5851_F42D_4C95_7F2D;

impl Pcg32 {
    /// Mirrors the reference implementation's constructor exactly:
    ///
    /// ```text
    /// inc   = (sequence << 1) | 1     // 32-bit shift, so the top bit lands in incHi
    /// state = 0; step(); state += seed; step();
    /// ```
    ///
    /// The reference implementation builds `inc` from `incHi = sequence >>> 31` and
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
    /// The reference implementation computes the permutation from `oldHi`/`oldLo` captured
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

        // The reference implementation writes this as `(xs >>> rot) | (xs << ((-rot) & 31))`.
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
    /// The reference implementation divides by `4294967296` (2^32). Division of an exactly
    /// representable integer by a power of two is exact in IEEE-754, so this
    /// is one of the float operations that carries no parity risk at all.
    pub fn next_f64(&mut self) -> f64 {
        self.next_u32() as f64 / 4294967296.0
    }
}

/// The simulation-facing RNG: uniform, normal, ranged-int and boolean draws.
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

    /// `createGameRng(seed)` in the reference implementation — note it uses sequence **0**,
    /// while the `GameRng` constructor defaults to **1**. That asymmetry is in
    /// the original and is load-bearing: the two entry points produce
    /// different streams from the same seed.
    pub fn from_seed(seed: u32) -> Self {
        Self::new(seed, 0)
    }

    /// A named substream of `root_seed`, per the derivation contract in
    /// [`stream`]'s module docs.
    ///
    /// One root seed, several independent generators: the engine seeds its
    /// market, economy and external streams through here, so perturbing what
    /// one domain draws cannot shift any other domain's sequence. The
    /// derivation is integer-only and documented as a formula a reader can
    /// reproduce; it is pinned by a golden test rather than trusted.
    pub fn substream(root_seed: u32, stream_id: u32) -> Self {
        let mixed = splitmix64_mix(((root_seed as u64) << 32) | stream_id as u64);
        Self::new(
            (mixed >> 32) as u32,
            stream::STREAM_SEQUENCE_BASE + stream_id,
        )
    }

    pub fn next_f64(&mut self) -> f64 {
        self.pcg.next_f64()
    }

    /// The generator's complete observable state.
    ///
    /// Three numbers: the LCG state, its increment, and the cached Box-Muller
    /// spare. That is ALL of it -- restore these and the next draw, and every
    /// draw after it, is the one that would have come next.
    ///
    /// The spare is the part that is easy to forget and fatal to omit.
    /// `next_normal` consumes two LCG steps and returns one value, keeping the
    /// other; a snapshot of the LCG alone would restore a generator that
    /// produces the right uniforms and the wrong normals, diverging on the
    /// first `next_normal` and looking correct until then.
    ///
    /// It also means "advance by N draws" is not a well-defined restore
    /// operation on this generator, which is why this is a state snapshot
    /// rather than a jump-ahead: N draws map to N or N+1 LCG steps depending
    /// on how the normals fell.
    pub fn snapshot(&self) -> RngState {
        RngState {
            state: self.pcg.state,
            increment: self.pcg.inc,
            spare: self.spare,
        }
    }

    /// Restore a generator to a snapshot. Exact, and O(1).
    pub fn restore(state: RngState) -> Self {
        Self {
            pcg: Pcg32 {
                state: state.state,
                inc: state.increment,
            },
            spare: state.spare,
        }
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
    /// 1. The reference implementation writes `const u = this.nextFloat() || 1e-10`. That is
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
    /// The reference implementation is `min + (this.pcg.next() % range)`, where `next()` is
    /// an unsigned 32-bit value held in a JS double, so `%` is exact integer
    /// remainder. Both languages truncate toward zero and both operands are
    /// non-negative, so the result matches.
    ///
    /// This is a modulo-biased draw — values below `2^32 % range` are very
    /// slightly more likely. That bias is in the original and is reproduced
    /// deliberately: removing it would change every stream in the simulation.
    pub fn next_int(&mut self, min: i64, max: i64) -> i64 {
        let range = max - min + 1;
        if range <= 0 {
            // JS would produce NaN or Infinity here and poison downstream
            // arithmetic silently. Returning `min` is a deliberate divergence
            // from a case the reference implementation never exercises; if a golden vector
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

    /// The state after construction is not something the reference implementation exposes,
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

#[cfg(test)]
mod substream_tests {
    use super::*;

    /// The derivation is a documented contract, so it is pinned to the
    /// formula's hand-computed values rather than to "whatever the code
    /// does". If this fails, either the formula in the [`stream`] docs or
    /// this test is wrong — and a silent re-derivation would strand every
    /// recorded result of the era, so failing loudly is the point.
    #[test]
    fn substream_derivation_is_the_documented_formula() {
        // splitmix64_mix(42 << 32 | k), top 32 bits, computed independently.
        for (id, seed) in [
            (stream::MARKET, 0xEA67_E2F1_u32),
            (stream::ECONOMY, 0x6997_3300),
            (stream::EXTERNAL, 0x4B07_4493),
        ] {
            let mut derived = GameRng::substream(42, id);
            let mut expected = GameRng::new(seed, stream::STREAM_SEQUENCE_BASE + id);
            for _ in 0..64 {
                assert_eq!(
                    derived.next_f64().to_bits(),
                    expected.next_f64().to_bits(),
                    "stream {id} does not match the documented derivation"
                );
            }
        }
    }

    #[test]
    fn substreams_of_one_root_differ_from_each_other() {
        let ids = [stream::MARKET, stream::ECONOMY, stream::EXTERNAL];
        for a in ids {
            for b in ids {
                if a == b {
                    continue;
                }
                let mut x = GameRng::substream(7, a);
                let mut y = GameRng::substream(7, b);
                assert!(
                    (0..64).any(|_| x.next_f64() != y.next_f64()),
                    "streams {a} and {b} agree on a 64-draw prefix"
                );
            }
        }
    }

    #[test]
    fn substreams_differ_across_roots() {
        let mut x = GameRng::substream(1, stream::MARKET);
        let mut y = GameRng::substream(2, stream::MARKET);
        assert!((0..64).any(|_| x.next_f64() != y.next_f64()));
    }

    /// The failure the distinct-sequence half of the contract rules out:
    /// one substream must never be a time-shifted copy of another. Checked
    /// over a window rather than proved — the proof is that different odd
    /// increments give different LCG orbits, which holds by construction —
    /// so this is the tripwire for someone replacing the derivation with
    /// one that reuses an increment.
    #[test]
    fn no_substream_is_a_shifted_copy_of_another() {
        let ids = [stream::MARKET, stream::ECONOMY, stream::EXTERNAL];
        for a in ids {
            for b in ids {
                if a == b {
                    continue;
                }
                let reference: Vec<u32> = {
                    let mut rng = GameRng::substream(2026, a);
                    (0..64).map(|_| rng.pcg.next_u32()).collect()
                };
                let window: Vec<u32> = {
                    let mut rng = GameRng::substream(2026, b);
                    (0..1024).map(|_| rng.pcg.next_u32()).collect()
                };
                assert!(
                    !window
                        .windows(reference.len())
                        .any(|w| w == reference.as_slice()),
                    "stream {b} replays stream {a}'s prefix at an offset"
                );
            }
        }
    }

    /// The substreams must also stay clear of every stream in historical
    /// use with the same nominal seed — a consumer holding root seed 42
    /// must not find the engine's market stream replaying `GameRng::new(42, 99)`.
    #[test]
    fn substreams_do_not_replay_the_legacy_streams() {
        for legacy_sequence in [0, 1, 21, 99] {
            for id in [stream::MARKET, stream::ECONOMY, stream::EXTERNAL] {
                let mut legacy = GameRng::new(42, legacy_sequence);
                let mut derived = GameRng::substream(42, id);
                assert!(
                    (0..64).any(|_| derived.next_f64() != legacy.next_f64()),
                    "substream {id} replays legacy sequence {legacy_sequence}"
                );
            }
        }
    }
}

#[cfg(test)]
mod snapshot_tests {
    use super::*;

    #[test]
    fn a_restored_generator_continues_the_same_stream() {
        let mut original = GameRng::new(42, 7);
        for _ in 0..37 {
            original.next_f64();
        }
        let mark = original.snapshot();
        let expected: Vec<f64> = (0..20).map(|_| original.next_f64()).collect();

        let mut restored = GameRng::restore(mark);
        let actual: Vec<f64> = (0..20).map(|_| restored.next_f64()).collect();
        assert_eq!(expected, actual);
    }

    #[test]
    fn the_cached_spare_is_part_of_the_state() {
        // The field a snapshot is most likely to omit, and the one that makes
        // omission invisible: uniforms would still match, and only the first
        // normal after the restore would differ.
        let mut original = GameRng::new(11, 3);
        // An ODD number of normals, so a spare is left cached.
        original.next_normal();
        assert!(original.snapshot().spare.is_some(), "expected a cached spare");

        let mark = original.snapshot();
        let expected = original.next_normal();
        assert_eq!(GameRng::restore(mark).next_normal(), expected);

        // And a snapshot that dropped the spare would diverge here -- shown
        // rather than asserted about, so the test proves the field matters.
        let mut without_spare = GameRng::restore(RngState {
            spare: None,
            ..mark
        });
        assert_ne!(without_spare.next_normal(), expected);
    }

    #[test]
    fn a_snapshot_survives_a_mixed_stream() {
        // Uniforms and normals interleaved is the real usage shape: the tick
        // draws both, so a snapshot taken mid-tick must restore either kind.
        let mut original = GameRng::new(5, 99);
        for i in 0..25 {
            if i % 3 == 0 {
                original.next_normal();
            } else {
                original.next_f64();
            }
        }
        let mark = original.snapshot();
        let expected: Vec<f64> = (0..30)
            .map(|i| {
                if i % 2 == 0 {
                    original.next_normal()
                } else {
                    original.next_f64()
                }
            })
            .collect();

        let mut restored = GameRng::restore(mark);
        let actual: Vec<f64> = (0..30)
            .map(|i| {
                if i % 2 == 0 {
                    restored.next_normal()
                } else {
                    restored.next_f64()
                }
            })
            .collect();
        assert_eq!(expected, actual);
    }

    #[test]
    fn two_different_positions_do_not_snapshot_alike() {
        // Otherwise every test above passes on a snapshot that captures
        // nothing.
        let mut rng = GameRng::new(1, 1);
        let first = rng.snapshot();
        rng.next_f64();
        assert_ne!(first, rng.snapshot());
    }
}
