//! # pretium - the engine core
//!
//! The price model, in Rust, compiled once and consumed twice: as WebAssembly
//! inside a browser, and as a Python extension module for backtesting.
//!
//! "Once" is the load-bearing word. A Rust implementation that runs only in
//! Python while the browser keeps a TypeScript one is not a port, it is a fork,
//! and two models that quietly disagree about the same prices is a worse
//! outcome than having no Python bindings at all. See the port plan.
//!
//! ## The rule this crate is written under
//!
//! Every module here is a port of a specific TypeScript file, and the target
//! is **bit-identical output**, not equivalent behaviour. Where the original
//! does something surprising, the port reproduces the surprise and comments
//! why. Improvements, tidy-ups and bug fixes are all out of scope: each one
//! would be a silent divergence, and the divergences are invisible until a
//! whole simulated market has drifted apart.
//!
//! Fixes belong upstream in the TypeScript first, where the existing test
//! suite can catch them — then they arrive here as an ordinary re-port.

// `!(x > 0.0)` rather than `x <= 0.0` is a deliberate, load-bearing idiom
// throughout this crate: the negated form also rejects NaN, and it is how the
// TypeScript guards are written. Clippy reads it as an accident of style.
// Rewriting to `partial_cmp` would obscure the one property the guards exist
// for, so the lint is silenced here with the reason rather than at each site.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

pub mod economy;
pub mod engine;
pub mod fair_value;
pub mod market;
pub mod market_maker;
pub mod mathx;
pub mod microstructure;
pub mod mispricing;
pub mod order_book;
pub mod rng;
/// The twelve sectors and their model parameters.
pub mod sectors;
pub mod types;
/// The single place a factor of 100 exists - see the module docs.
pub mod units;

/// Python bindings. Feature-gated: the WASM consumer never compiles PyO3.
#[cfg(feature = "python")]
mod python;

/// Order-book bindings, split out because `python` is already long.
#[cfg(feature = "python")]
mod python_book;

/// Engine bindings - Layer 2.
#[cfg(feature = "python")]
mod python_engine;

pub use rng::{to_uint32, GameRng, Pcg32};
