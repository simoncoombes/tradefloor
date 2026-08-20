//! # pretium
//!
//! A deterministic market simulator with a real limit order book.
//!
//! This is a **placeholder release** reserving the name while the library is
//! developed. It contains no functional code yet. The first working release
//! will provide a seeded market simulation whose output is bit-identical
//! across operating systems, with price-time-priority order matching and
//! fundamentals-anchored valuation.
//!
//! Do not depend on this version.

/// Returns the crate version. Placeholder; carries no simulation behaviour.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
