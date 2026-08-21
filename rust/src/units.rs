//! The one place a factor of 100 lives.
//!
//! Decision (plan owner, settled): **units are fractional at the Python
//! boundary.** `0.045` means 4.5%. The simulation core denominates rates in
//! percent (`federal_funds_rate = 2.5` meaning 2.5%, divided by 100 again at
//! the point of use in fair value). Conversion happens exactly once, here.
//!
//! Why one place rather than at each call site: a unit bug is invisible. A
//! market run at a 0.045% policy rate instead of 4.5% does not crash, does
//! not warn, and produces a plausible-looking trajectory that is wrong. The
//! only defence is that there is a single function to audit, and that
//! implausible inputs are rejected rather than simulated.
//!
//! Fractional is the right boundary convention because every other Python
//! finance library uses it; exporting the engine's percent convention would
//! make this the only percent-denominated library in a user's stack, and the
//! resulting mistakes would be the user's to debug and ours to have caused.

/// Convert a fractional rate from the public API into the core's percent.
///
/// `0.045` (4.5%) becomes `4.5`.
pub fn fraction_to_percent(fraction: f64) -> f64 {
    fraction * 100.0
}

/// Convert a core percent rate back to the public API's fractional form.
///
/// `4.5` becomes `0.045`.
pub fn percent_to_fraction(percent: f64) -> f64 {
    percent / 100.0
}

/// The plausibility band for an interest-rate-like input, in FRACTIONAL form.
///
/// Settled by the plan owner. The band exists to catch ONE error -- passing
/// `4.5` meaning 4.5%, which under a fractional API is 450% -- and not to
/// encode a view about monetary policy.
///
/// The lower bound is negative on purpose. Negative policy rates are not
/// hypothetical: the SNB held -0.75%, the ECB's deposit rate was negative for
/// eight years, and Japan ran -0.1%. A band starting at zero would make the
/// eurozone of 2014-2022 unmodellable, which for a rate-sensitivity research
/// library is a defect rather than a conservative choice.
///
/// The upper bound covers every observed policy rate -- Volcker peaked near
/// 20%, and emerging markets exceed 25% regularly -- while staying a real
/// guard. Note that tightening it further would not catch more UNIT errors:
/// the slip passes percent values of roughly 2 to 10, so any ceiling at or
/// below 1.0 catches all of them. A lower ceiling only rejects more real
/// scenarios. Neither bound catches a sub-1.0 percent value such as `0.5`
/// meaning 0.5%, and nothing can.
const RATE_MIN: f64 = -0.05;
const RATE_MAX: f64 = 0.50;

/// Validate a fractional rate, returning a message describing the likely
/// mistake rather than merely reporting a range.
///
/// The error text names the percent-versus-fractional confusion explicitly,
/// because that is what the input almost always is, and a bare "out of range"
/// leaves the user to rediscover the convention.
pub fn check_rate(name: &str, fraction: f64) -> Result<f64, String> {
    if fraction.is_nan() {
        return Err(format!("{name} is NaN"));
    }
    if !(RATE_MIN..=RATE_MAX).contains(&fraction) {
        let hint = if fraction > 1.0 && fraction <= 100.0 {
            format!(
                " - this looks like a percent value. Rates are FRACTIONAL \
                 here: pass {} for {}%.",
                fraction / 100.0,
                fraction
            )
        } else {
            String::new()
        };
        return Err(format!(
            "{name} = {fraction} is outside the plausible range \
             [{RATE_MIN}, {RATE_MAX}]{hint}"
        ));
    }
    Ok(fraction)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn round_trips_exactly_for_representable_values() {
        // Not all values round-trip: /100 then *100 is two roundings. These
        // are chosen because they do, and the test says so rather than
        // implying the property is general.
        for percent in [0.0, 2.5, 4.0, 25.0, 100.0] {
            assert_eq!(fraction_to_percent(percent_to_fraction(percent)), percent);
        }
    }

    #[test]
    fn converts_in_the_direction_the_docs_claim() {
        // Guards against the one mistake that matters: an inverted conversion
        // is still a number, and every downstream value stays finite.
        assert_eq!(fraction_to_percent(0.045), 4.5);
        assert_eq!(percent_to_fraction(4.5), 0.045);
    }

    #[test]
    fn catches_a_percent_value_passed_to_a_fractional_api() {
        let err = check_rate("corporate_bond_yield", 4.5).unwrap_err();
        assert!(err.contains("looks like a percent value"), "{err}");
        assert!(err.contains("0.045"), "{err}");
    }

    #[test]
    fn admits_every_policy_rate_that_has_actually_happened() {
        // Named cases, so a future tightening of the band has to explicitly
        // decide to make one of these unmodellable.
        for (name, rate) in [
            ("ECB deposit rate, 2019", -0.005),
            ("Bank of Japan, 2016", -0.001),
            ("ZIRP", 0.0),
            ("US, 2023", 0.0533),
            ("Volcker peak, 1981", 0.20),
            ("emerging-market tightening", 0.45),
        ] {
            assert!(check_rate("fed_funds", rate).is_ok(), "{name} ({rate}) rejected");
        }
    }

    #[test]
    fn rejects_beyond_the_band_at_both_ends() {
        assert!(check_rate("fed_funds", -0.20).is_err());
        assert!(check_rate("fed_funds", 0.75).is_err());
    }

    #[test]
    fn rejects_nan_rather_than_simulating_it() {
        assert!(check_rate("fed_funds", f64::NAN).is_err());
    }

    #[test]
    fn a_bare_zero_is_legal() {
        // Zero is a real rate, not a missing value. Rejecting it would be the
        // truthy-versus-nullish bug in a new place.
        assert!(check_rate("fed_funds", 0.0).is_ok());
        assert_eq!(fraction_to_percent(0.0), 0.0);
    }
}
