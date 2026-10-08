//! Contract symbols: what an agent writes to name a contract.
//!
//! A symbol carries the root, the expiry session and, for an option, the
//! right and the strike, so it names one contract for the life of a run and
//! means the same thing in a fork, a restore or a replay: nothing in it
//! depends on the order contracts were listed in.
//!
//! - A future: `ROOT.Fnnnn`, the expiry session after `F`, at least four
//!   digits, zero-padded. `IDX.F0273` is the index future expiring at the
//!   opening print of session 273.
//! - An option: `ROOT.Onnnn.Rk.cc`, the expiry after `O`, the right (`C` or
//!   `P`) and the strike in dollars and cents. `ACME.O0294.C42.50` is the
//!   42.50 call on ACME expiring at session 294.
//!
//! [`ContractSymbol::parse`] reads only the canonical spelling
//! [`ContractSymbol`]'s `Display` writes, so a symbol and its parse
//! round-trip exactly in both directions. The root may itself hold dots (a
//! ticker such as `BRK.B`): a symbol is read from the right.

use std::fmt;

/// An option's right.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum Right {
    Call,
    Put,
}

impl Right {
    /// `C` or `P`.
    pub fn letter(self) -> char {
        match self {
            Right::Call => 'C',
            Right::Put => 'P',
        }
    }
}

/// What kind of contract a symbol names, with what the kind needs beyond
/// the root and the expiry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum SymbolKind {
    /// A future.
    Future,
    /// An option, with its right and its strike in cents.
    Option { right: Right, strike_cents: u64 },
}

/// A parsed contract symbol.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub struct ContractSymbol {
    /// The underlying's root: `IDX` for the price index, or a ticker.
    pub root: String,
    /// The session the contract expires at.
    pub expiry: i64,
    pub kind: SymbolKind,
}

/// Whether `root` can head a symbol: ASCII letters, digits, `_` and `-`,
/// in segments joined by single dots, none of them empty.
fn root_ok(root: &str) -> bool {
    !root.is_empty()
        && root.split('.').all(|seg| {
            !seg.is_empty() && seg.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
        })
}

/// The expiry digits after the kind letter: at least four, no leading zero
/// beyond the padding to four, so each session has one spelling.
fn expiry_from(digits: &str) -> Option<i64> {
    if digits.len() < 4 || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    if digits.len() > 4 && digits.starts_with('0') {
        return None;
    }
    if digits.len() > 15 {
        return None;
    }
    digits.parse::<i64>().ok()
}

impl ContractSymbol {
    /// The future on `root` expiring at session `expiry`.
    pub fn future(root: impl Into<String>, expiry: i64) -> Result<Self, String> {
        Self::checked(root.into(), expiry, SymbolKind::Future)
    }

    /// The option on `root` expiring at session `expiry`, with `strike` in
    /// dollars, which must be a whole number of cents.
    pub fn option(root: impl Into<String>, expiry: i64, right: Right, strike: f64) -> Result<Self, String> {
        let cents = strike * 100.0;
        if !(strike > 0.0 && cents.is_finite() && (cents - cents.round()).abs() < 1e-6 && cents < 1e15) {
            return Err(format!("an option's strike is a positive whole number of cents, got {strike}"));
        }
        Self::checked(root.into(), expiry, SymbolKind::Option { right, strike_cents: cents.round() as u64 })
    }

    fn checked(root: String, expiry: i64, kind: SymbolKind) -> Result<Self, String> {
        if !root_ok(&root) {
            return Err(format!(
                "{root:?} cannot be a contract's root: it is letters, digits, '_' and '-', \
                 in segments joined by single dots"
            ));
        }
        if !(0..=999_999_999_999_999).contains(&expiry) {
            return Err(format!("a contract's expiry is a session number from 0, got {expiry}"));
        }
        Ok(ContractSymbol { root, expiry, kind })
    }

    /// The strike in dollars, for an option.
    pub fn strike(&self) -> Option<f64> {
        match self.kind {
            SymbolKind::Option { strike_cents, .. } => Some(strike_cents as f64 / 100.0),
            SymbolKind::Future => None,
        }
    }

    /// Read a symbol in its canonical spelling, and refuse anything else.
    pub fn parse(text: &str) -> Result<Self, String> {
        let bad = |why: &str| format!("{text:?} is not a contract symbol: {why}");
        let parts: Vec<&str> = text.split('.').collect();
        let n = parts.len();
        if n >= 2 {
            let last = parts[n - 1];
            if let Some(digits) = last.strip_prefix('F') {
                let expiry = expiry_from(digits)
                    .ok_or_else(|| bad("the expiry after F is at least four digits, padded with zeros to four"))?;
                return Self::checked(parts[..n - 1].join("."), expiry, SymbolKind::Future)
                    .map_err(|e| bad(&e));
            }
        }
        if n >= 4 {
            let (o, r, cents) = (parts[n - 3], parts[n - 2], parts[n - 1]);
            if let Some(digits) = o.strip_prefix('O') {
                let expiry = expiry_from(digits)
                    .ok_or_else(|| bad("the expiry after O is at least four digits, padded with zeros to four"))?;
                let right = match r.as_bytes().first() {
                    Some(b'C') => Right::Call,
                    Some(b'P') => Right::Put,
                    _ => return Err(bad("the right is C or P")),
                };
                let dollars = &r[1..];
                let ok_dollars = !dollars.is_empty()
                    && dollars.bytes().all(|b| b.is_ascii_digit())
                    && (dollars.len() == 1 || !dollars.starts_with('0'))
                    && dollars.len() <= 12;
                let ok_cents = cents.len() == 2 && cents.bytes().all(|b| b.is_ascii_digit());
                if !(ok_dollars && ok_cents) {
                    return Err(bad("the strike is dollars, a dot and two digits of cents"));
                }
                let strike_cents = dollars.parse::<u64>().map_err(|e| bad(&e.to_string()))? * 100
                    + cents.parse::<u64>().map_err(|e| bad(&e.to_string()))?;
                if strike_cents == 0 {
                    return Err(bad("the strike is above zero"));
                }
                return Self::checked(parts[..n - 3].join("."), expiry, SymbolKind::Option { right, strike_cents })
                    .map_err(|e| bad(&e));
            }
        }
        Err(bad("a future is ROOT.Fnnnn and an option ROOT.Onnnn.Rk.cc"))
    }
}

impl fmt::Display for ContractSymbol {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self.kind {
            SymbolKind::Future => write!(f, "{}.F{:04}", self.root, self.expiry),
            SymbolKind::Option { right, strike_cents } => write!(
                f,
                "{}.O{:04}.{}{}.{:02}",
                self.root,
                self.expiry,
                right.letter(),
                strike_cents / 100,
                strike_cents % 100
            ),
        }
    }
}

impl std::str::FromStr for ContractSymbol {
    type Err = String;
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        ContractSymbol::parse(s)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_plans_examples_parse_and_format_back() {
        let f = ContractSymbol::parse("IDX.F0273").unwrap();
        assert_eq!(f.root, "IDX");
        assert_eq!(f.expiry, 273);
        assert_eq!(f.kind, SymbolKind::Future);
        assert_eq!(f.to_string(), "IDX.F0273");
        let o = ContractSymbol::parse("ACME.O0294.C42.50").unwrap();
        assert_eq!(o.root, "ACME");
        assert_eq!(o.expiry, 294);
        assert_eq!(o.kind, SymbolKind::Option { right: Right::Call, strike_cents: 4250 });
        assert_eq!(o.strike(), Some(42.5));
        assert_eq!(o.to_string(), "ACME.O0294.C42.50");
        assert_eq!(ContractSymbol::future("IDX", 56).unwrap().to_string(), "IDX.F0056");
        assert_eq!(ContractSymbol::future("IDX", 12345).unwrap().to_string(), "IDX.F12345");
        assert_eq!(
            ContractSymbol::option("BRK.B", 7, Right::Put, 0.05).unwrap().to_string(),
            "BRK.B.O0007.P0.05"
        );
    }

    #[test]
    fn every_symbol_round_trips_both_ways() {
        let roots = ["IDX", "A", "KAT11", "BRK.B", "X_Y-Z", "IDX.F0001"];
        for root in roots {
            for expiry in [0, 1, 56, 999, 1000, 9999, 10000, 123456] {
                let f = ContractSymbol::future(root, expiry).unwrap();
                let text = f.to_string();
                assert_eq!(ContractSymbol::parse(&text).unwrap(), f, "{text}");
                assert_eq!(ContractSymbol::parse(&text).unwrap().to_string(), text);
                for (right, strike) in [(Right::Call, 42.5), (Right::Put, 0.01), (Right::Call, 4150.0)] {
                    let o = ContractSymbol::option(root, expiry, right, strike).unwrap();
                    let text = o.to_string();
                    assert_eq!(ContractSymbol::parse(&text).unwrap(), o, "{text}");
                }
            }
        }
    }

    #[test]
    fn only_the_canonical_spelling_parses() {
        for bad in [
            "", "IDX", "IDX.F273", "IDX.F00273", "IDX.F-273", "IDX.F02x3", ".F0273", "IDX..F0273",
            "IDX.G0273", "ACME.O0294.C42.5", "ACME.O0294.C042.50", "ACME.O0294.X42.50",
            "ACME.O0294.C.50", "ACME.O0294.C0.00", "AC ME.F0001", "IDX.f0273",
        ] {
            assert!(ContractSymbol::parse(bad).is_err(), "{bad:?} parsed");
        }
        assert!(ContractSymbol::future("IDX", -1).is_err());
        assert!(ContractSymbol::future("", 3).is_err());
        assert!(ContractSymbol::option("ACME", 3, Right::Call, 42.505).is_err());
        assert!(ContractSymbol::option("ACME", 3, Right::Call, 0.0).is_err());
    }
}
