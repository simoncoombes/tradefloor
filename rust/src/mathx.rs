//! The deterministic maths layer — Phase 1.
//!
//! **Every transcendental in this crate goes through this module.** Nothing
//! else may call `f64::exp`, `f64::ln`, `f64::powf`, `f64::sin` or `f64::cos`
//! directly. That is enforced by `no_std_transcendentals` in
//! `tests/mathx_parity.rs`, which greps the source rather than trusting anyone
//! to remember.
//!
//! # Why the rule exists
//!
//! Two separate reasons, and the second is the one that would still apply if
//! the reference implementation did not exist.
//!
//! **1. Parity with V8.** Phase 0 found that Rust's `std` `cos` differs from
//! V8's by 1 ULP on a real Box-Muller input. Since `price = fairValue x exp(s)`
//! is a non-linear feedback loop, a single last-ULP difference becomes a
//! visibly different market within a simulated year. Verifying the port against
//! the reference implementation requires the maths to agree exactly.
//!
//! **2. Determinism across platforms.** `std`'s float maths delegates to the
//! platform libm — MSVC's CRT on Windows, glibc on Linux, Apple's on macOS.
//! They do not agree with each other. A Python wheel built on `std` would
//! therefore produce *different markets on different operating systems*, which
//! for a library whose entire premise is reproducible backtests is
//! disqualifying on its own.
//!
//! The `libm` crate is a pure-Rust port of MUSL's implementation, which shares
//! fdlibm ancestry with V8's. It is the same code everywhere it compiles, so it
//! fixes both problems at once.
//!
//! # `sqrt` is the one exception
//!
//! IEEE-754 specifies square root exactly: the result must be the correctly
//! rounded value, so every conforming implementation returns identical bits.
//! `std`'s `sqrt` also lowers to a single hardware instruction, so routing it
//! through `libm` would cost performance for no correctness gain. It is
//! re-exported here anyway so that call sites read consistently and nobody has
//! to remember which functions are safe.

/// `e^x`. See module docs for why this is not `f64::exp`.
#[inline]
pub fn exp(x: f64) -> f64 {
    libm::exp(x)
}

/// Natural logarithm. Named `log` to match the `libm`/C convention rather than
/// Rust's `ln`, because the reference implementation being ported says `Math.log` and the
/// port reads more obviously against its source this way.
#[inline]
pub fn log(x: f64) -> f64 {
    libm::log(x)
}

/// `base^exponent`.
///
/// The risky call sites are the intraday U-shape and volume curves in
/// the reference implementation, which evaluate `pow` fresh every tick with
/// a time-varying base
/// and a non-integer exponent (2.5). Non-integer exponents are exactly where
/// `pow` implementations diverge, because they route through `exp(y * log(x))`
/// with implementation-specific extra precision.
#[inline]
pub fn pow(base: f64, exponent: f64) -> f64 {
    libm::pow(base, exponent)
}

/// Sine. Hit on every normal draw via Box-Muller, and once a day for oil
/// seasonality.
#[inline]
pub fn sin(x: f64) -> f64 {
    libm::sin(x)
}

/// Cosine. **The function Phase 0 caught diverging** — see module docs.
#[inline]
pub fn cos(x: f64) -> f64 {
    libm::cos(x)
}

/// The gamma function.
///
/// Reached from one place: the Gaussian absolute moment of order `p` in
/// [`crate::economy::daily::expected_return_spike`], which needs
/// `Gamma((p + 1) / 2)` to cancel a fear excursion whose down side carries
/// an exponent. Its call site branches at `p = 1` rather than evaluating
/// `Gamma(1)`, so nothing that predates that exponent reaches this
/// function at all.
///
/// Routed through `libm` for the module's own reason and not by habit:
/// `tgamma` is a series evaluation, so the platform libm answers differ in
/// the last places, and a VIX level that differed by an ULP between Linux
/// and Windows would compound through the variance loop exactly as the
/// module docs describe for `cos`.
#[inline]
pub fn tgamma(x: f64) -> f64 {
    libm::tgamma(x)
}

/// The complementary error function, `1 - erf(x)`.
///
/// Reached from one place: the standard normal's upper tail in
/// [`crate::market::index_var`], where `Q(c) = erfc(c / sqrt(2)) / 2` is the
/// factor the truncated second and fourth moments carry. Those moments are
/// what price the crash amplifier's conditional tail, so this value is
/// multiplied into the VIX's own variance read-back and compounds through the
/// loop exactly as `cos` does.
///
/// Routed through `libm` for the module's own reason. `erfc` is a piecewise
/// rational approximation and the platform implementations differ in the last
/// places — and this one is worse than most, because the tail is EVALUATED
/// where it is small: `erfc(3)` is 2.2e-05, so a relative difference in the
/// last places of the polynomial is a relative difference in a term the
/// amplifier multiplies by `a^2`. There is no `std` `erfc` to fall back to in
/// any case, which is why this is the one function here that is not also an
/// argument about V8: the reference implementation never computed a normal
/// tail, so there are no recorded vectors to be in parity with. What is
/// asserted instead, beside [`crate::market::index_var`], is the identity the
/// value is used through — a moment check against the Monte Carlo the moments
/// are moments of.
#[inline]
pub fn erfc(x: f64) -> f64 {
    libm::erfc(x)
}

/// Square root.
///
/// Delegates to `std` deliberately: IEEE-754 pins the result exactly, so all
/// implementations agree, and `std` compiles to a single instruction. This is
/// the only transcendental-adjacent function in the crate that is allowed to
/// use `std`, and the reason is a specification guarantee rather than a
/// measurement.
#[inline]
pub fn sqrt(x: f64) -> f64 {
    x.sqrt()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Anchors that must hold in any correct implementation. These do not test
    /// V8 parity — `tests/mathx_parity.rs` does that against 12,625 recorded
    /// V8 values. These catch a catastrophically wrong wiring, such as `exp`
    /// accidentally calling `log`.
    #[test]
    fn known_values_are_sane() {
        assert_eq!(exp(0.0).to_bits(), 1.0f64.to_bits());
        assert_eq!(log(1.0).to_bits(), 0.0f64.to_bits());
        assert_eq!(sqrt(4.0).to_bits(), 2.0f64.to_bits());
        assert_eq!(pow(2.0, 10.0).to_bits(), 1024.0f64.to_bits());
        assert_eq!(sin(0.0).to_bits(), 0.0f64.to_bits());
        assert_eq!(cos(0.0).to_bits(), 1.0f64.to_bits());
        assert_eq!(erfc(0.0).to_bits(), 1.0f64.to_bits());
    }

    /// `erfc` HAS NO RECORDED V8 VECTORS, so the anchors here carry more
    /// weight than the one line above does for the others: there is no
    /// 12,625-value parity file behind it, because the reference
    /// implementation never computed a normal tail.
    ///
    /// What is asserted instead is the identity the function is USED
    /// through. `market::index_var` reads it only as the standard normal's
    /// upper tail `Q(c) = erfc(c / sqrt 2) / 2`, and that has properties a
    /// wrong wiring cannot fake: it is 0.5 at zero, it is monotone, it
    /// halves the symmetric two-sided tail, and at the exceedances the
    /// amplifier is evaluated at it must still carry significant digits
    /// where `1 - Phi(c)` would have cancelled to nothing.
    #[test]
    fn the_complementary_error_function_is_the_normals_upper_tail() {
        let sqrt_two = sqrt(2.0);
        let q = |c: f64| 0.5 * erfc(c / sqrt_two);
        assert_eq!(q(0.0).to_bits(), 0.5f64.to_bits());
        // The three-sigma tail to five places, a number anyone can check.
        assert!((q(3.0) - 0.001349898).abs() < 5e-10, "Q(3) = {}", q(3.0));
        assert!((q(1.0) - 0.158655254).abs() < 5e-10, "Q(1) = {}", q(1.0));
        // Symmetry: Q(-c) = 1 - Q(c), to the last places.
        for i in 0..=80 {
            let c = i as f64 * 0.1;
            assert!((q(-c) - (1.0 - q(c))).abs() < 1e-15, "asymmetric at {c}");
        }
        // Monotone, and STILL POSITIVE where the complement would have
        // cancelled. At nine sigmas the tail is about 1.1e-19 and
        // `1.0 - Phi(9.0)` is exactly zero in a double; the amplifier's
        // floored-regime branch evaluates out here.
        let mut prev = q(0.0);
        for i in 1..=200 {
            let c = i as f64 * 0.05;
            let now = q(c);
            assert!(now < prev, "the tail rose at {c}");
            assert!(now > 0.0, "the tail underflowed at {c}");
            prev = now;
        }
        assert!(q(9.0) > 0.0 && q(9.0) < 1e-18, "Q(9) = {}", q(9.0));
    }

    /// `exp` and `log` must round-trip on the band the price model actually
    /// uses — `s` is clamped to +/-0.9.
    ///
    /// Measured as ABSOLUTE error, not ULPs, and the reason is worth recording
    /// because the first version of this test asserted ULPs and failed at 198.
    /// That was the test being wrong, not the maths. For small `s`, `exp(s)` is
    /// near 1, and `log` of a value near 1 is the classic cancellation case:
    /// a 1-ULP error in `exp(s)` is ~2.2e-16 absolute, which against `s =
    /// 0.001` is ~1000 ULPs of the RESULT. The ULP count explodes as `s`
    /// approaches zero for reasons intrinsic to the round trip, so it measures
    /// the arithmetic of the test rather than the quality of the functions.
    #[test]
    fn exp_log_round_trip_over_the_mispricing_band() {
        let mut worst_abs = 0.0f64;
        for i in -900..=900 {
            let s = i as f64 / 1000.0;
            let back = log(exp(s));
            worst_abs = worst_abs.max((back - s).abs());
        }
        assert!(
            worst_abs <= 1e-15,
            "exp/log round trip drifted {worst_abs:e} in absolute terms"
        );
    }
}

/// JavaScript's `Math.max` for two arguments.
///
/// `f64::max` is NOT this. Rust's `max` treats NaN as "missing" and returns
/// the other operand; ECMAScript propagates it. `Math.max(1, NaN)` is `NaN`
/// while `1.0f64.max(f64::NAN)` is `1.0` — a divergence that turns a poisoned
/// input into a plausible-looking number, which is the worst way to lose a
/// NaN.
///
/// The two also disagree on signed zero: `Math.max` prefers `+0`, and parity
/// here is asserted on raw bits.
pub fn max(a: f64, b: f64) -> f64 {
    // Propagate the OPERAND's NaN rather than minting `f64::NAN`. The two
    // are different bit patterns — `f64::NAN` is `7ff8…`, while the NaN x86
    // hardware produces for an invalid operation is `fff8…` — and there is
    // no reason to discard the one that arrived.
    if a.is_nan() {
        return a;
    }
    if b.is_nan() {
        return b;
    }
    if a > b {
        a
    } else if b > a {
        b
    } else {
        // Equal — which only matters for ±0. Math.max yields +0.
        if a.is_sign_positive() {
            a
        } else {
            b
        }
    }
}

/// JavaScript's `Math.min` for two arguments. See [`max`]; `Math.min` prefers
/// `-0` where `Math.max` prefers `+0`.
pub fn min(a: f64, b: f64) -> f64 {
    if a.is_nan() {
        return a;
    }
    if b.is_nan() {
        return b;
    }
    if a < b {
        a
    } else if b < a {
        b
    } else {
        if a.is_sign_negative() {
            a
        } else {
            b
        }
    }
}

/// JavaScript's `Math.round`, exactly.
///
/// Neither obvious Rust spelling is correct:
///
/// - `f64::round` rounds half **away from zero**; ECMAScript rounds half
///   **up** (toward +infinity). They disagree on every negative half —
///   JS `Math.round(-3.5)` is `-3`, Rust `(-3.5).round()` is `-4`.
/// - `(x + 0.5).floor()` is the usual half-up idiom and is ALSO wrong, in
///   two ways the spec calls out. For `-0.5 <= x < 0` it yields `+0` where
///   `Math.round` yields `-0`; and when `x + 0.5` itself rounds up in
///   floating point it overshoots by a whole unit — `Math.round` of
///   `0.49999999999999994` is `0`, but `(x + 0.5).floor()` is `1`.
///
/// So the frac is taken against `floor(x)` instead, which is exact, and the
/// negative-zero case is restored explicitly.
pub fn js_round(x: f64) -> f64 {
    let floor = x.floor();
    // Exact: `x - floor(x)` is representable, so no second rounding error.
    let rounded = if x - floor >= 0.5 { floor + 1.0 } else { floor };
    // ECMAScript yields -0 for -0.5 <= x < 0. The sign of zero is not
    // cosmetic here: parity is asserted on raw bits.
    if rounded == 0.0 && x < 0.0 {
        -0.0
    } else {
        rounded
    }
}

/// The engine's `clamp`, which is a ternary and NOT `f64::clamp`.
///
/// Both the market-maker and mispricing modules define it identically as
/// `x < lo ? lo : x > hi ? hi : x`, and the behaviour that matters is what
/// that does to values the comparisons cannot order:
///
/// - **NaN passes through unchanged.** It fails `<` and `>` alike, so it
///   falls to the final branch. `f64::clamp` PANICS on a NaN input, and a
///   `min`/`max` pair would return one of the bounds. Neither is this.
/// - **`-0` passes through as `-0`** rather than being normalised, which the
///   `crowd_lean` and `create_mispricing_state` vectors both pin.
///
/// It also does not require `lo <= hi`; with the bounds inverted every input
/// lands on `hi`, and nothing checks.
pub fn clamp(x: f64, lo: f64, hi: f64) -> f64 {
    if x < lo {
        lo
    } else if x > hi {
        hi
    } else {
        x
    }
}

/// The OTHER clamp — `Math.max(lo, Math.min(hi, x))`, from
/// the reference implementation's math utilities.
///
/// The engine has two clamps and they are **not interchangeable**.
/// The market-maker and mispricing modules define a ternary locally
/// ([`clamp`]); the economy module imports this one. They agree on NaN (both propagate) and on
/// ordinary values, and differ on signed zero:
///
/// ```text
/// clamp(-0, 0, 1)  ternary -> -0     min/max -> +0
/// ```
///
/// because `Math.max` prefers `+0`. That is reachable — `fearGreedIndex`
/// clamps with a lower bound of exactly `0` — so which spelling a call site
/// uses has to be carried across, not normalised to one "clamp".
pub fn clamp_via_min_max(x: f64, lo: f64, hi: f64) -> f64 {
    max(lo, min(hi, x))
}

#[cfg(test)]
mod js_minmax_clamp_tests {
    use super::{clamp, clamp_via_min_max};

    #[test]
    fn the_two_clamps_disagree_on_negative_zero() {
        // Verified against V8 with Object.is. If these ever agree, one of
        // them has been "tidied up" into the other.
        assert_eq!(clamp(-0.0, 0.0, 1.0).to_bits(), (-0.0f64).to_bits());
        assert_eq!(
            clamp_via_min_max(-0.0, 0.0, 1.0).to_bits(),
            0.0f64.to_bits()
        );
    }

    #[test]
    fn they_agree_everywhere_else_that_matters() {
        for (x, lo, hi) in [
            (-0.0, -1.0, 1.0),
            (0.0, -0.0, 1.0),
            (5.0, -1.0, 1.0),
            (-5.0, -1.0, 1.0),
            (1.0, -1.0, 1.0),
            (0.5, -1.0, 1.0),
        ] {
            assert_eq!(
                clamp(x, lo, hi).to_bits(),
                clamp_via_min_max(x, lo, hi).to_bits(),
                "clamp({x}, {lo}, {hi})"
            );
        }
    }

    #[test]
    fn nan_propagates_through_both() {
        assert!(clamp(f64::NAN, -1.0, 1.0).is_nan());
        assert!(clamp_via_min_max(f64::NAN, -1.0, 1.0).is_nan());
    }
}

#[cfg(test)]
mod js_clamp_tests {
    use super::clamp;

    #[test]
    fn nan_passes_through_where_f64_clamp_would_panic() {
        assert!(clamp(f64::NAN, -1.0, 1.0).is_nan());
    }

    #[test]
    fn negative_zero_passes_through_unnormalised() {
        assert_eq!(clamp(-0.0, -1.0, 1.0).to_bits(), (-0.0f64).to_bits());
        assert_eq!(clamp(0.0, -1.0, 1.0).to_bits(), 0.0f64.to_bits());
    }

    #[test]
    fn ordinary_bounds() {
        assert_eq!(clamp(5.0, -1.0, 1.0), 1.0);
        assert_eq!(clamp(-5.0, -1.0, 1.0), -1.0);
        assert_eq!(clamp(0.5, -1.0, 1.0), 0.5);
        // Exactly on a bound is not clamped: the comparisons are strict.
        assert_eq!(clamp(1.0, -1.0, 1.0), 1.0);
        assert_eq!(clamp(f64::INFINITY, -1.0, 1.0), 1.0);
        assert_eq!(clamp(f64::NEG_INFINITY, -1.0, 1.0), -1.0);
    }
}

#[cfg(test)]
mod js_minmax_tests {
    use super::{max, min};

    /// Expectations transcribed from V8, compared with `Object.is` so the
    /// zero cases are distinguished rather than both printing as "0".
    #[test]
    fn nan_propagates_where_rusts_own_max_would_swallow_it() {
        assert!(max(1.0, f64::NAN).is_nan());
        assert!(max(f64::NAN, 1.0).is_nan());
        assert!(min(1.0, f64::NAN).is_nan());
        assert!(min(f64::NAN, 1.0).is_nan());
        // The divergence this exists to avoid.
        assert_eq!(1.0f64.max(f64::NAN), 1.0);
    }

    #[test]
    fn signed_zero_matches_v8() {
        assert_eq!(max(-0.0, 0.0).to_bits(), 0.0f64.to_bits());
        assert_eq!(max(0.0, -0.0).to_bits(), 0.0f64.to_bits());
        assert_eq!(min(-0.0, 0.0).to_bits(), (-0.0f64).to_bits());
        assert_eq!(min(0.0, -0.0).to_bits(), (-0.0f64).to_bits());
    }

    #[test]
    fn ordinary_values() {
        assert_eq!(max(3.0, 7.0), 7.0);
        assert_eq!(max(-3.0, -7.0), -3.0);
        assert_eq!(min(3.0, 7.0), 3.0);
        assert_eq!(min(-3.0, -7.0), -7.0);
        assert_eq!(max(f64::INFINITY, 1e308), f64::INFINITY);
        assert_eq!(min(f64::NEG_INFINITY, -1e308), f64::NEG_INFINITY);
    }
}
