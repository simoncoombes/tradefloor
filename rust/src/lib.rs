#![doc = include_str!("../README.md")]
//!
//! ## Where things are
//!
//! [`engine::Engine`] owns a market and runs the day loop. It takes its
//! coefficients from a [`params::ModelParams`], which is one of the named
//! presets ([`params::ModelParams::preset`]) or a preset with overrides, and
//! its roster from [`universe`]. The rest are the parts the engine is built
//! from, and each can be called on its own:
//!
//! - [`market`]: the tick, the factor model, GARCH and the trading clock.
//! - [`economy`]: the macro chain (GDP, inflation, unemployment, the VIX,
//!   the business cycle) and the central bank.
//! - [`order_book`], [`agent_book`], [`market_maker`] and
//!   [`microstructure`]: the limit order book and the depth around it.
//! - [`fair_value`] and [`mispricing`]: what a stock is worth and how far
//!   its price has strayed from it.
//! - [`rates`]: bond indices priced off the engine's yield curve.
//! - [`rng`] and [`mathx`]: the random streams and the transcendental
//!   maths, both written here so that a seed gives the same bits on every
//!   platform.
//!
//! The `python` feature builds the Python extension module and the `wasm`
//! feature builds the WebAssembly binding. Both drive
//! [`engine::Engine::close_day`], so a day in Python and a day in a browser
//! run the same code.
//!
//! Many modules began as line-for-line ports of an earlier reference
//! implementation, and their comments still cite it. The crate has since
//! changed the model on purpose and is now its only definition.
//!
//! What it promises is determinism: the same seed, roster and preset give
//! the same market, bit for bit, on every supported platform and in every
//! patch release of an LTS line.

// `!(x > 0.0)` rather than `x <= 0.0` is a deliberate, load-bearing idiom
// throughout this crate: the negated form also rejects NaN, and it is how the
// reference-implementation guards are written. Clippy reads it as an
// accident of style.
// Rewriting to `partial_cmp` would obscure the one property the guards exist
// for, so the lint is silenced here with the reason rather than at each site.
#![allow(clippy::neg_cmp_op_on_partial_ord)]

pub mod agent_book;
pub mod earnings;
pub mod economy;
pub mod engine;
pub mod fair_value;
pub mod market;
pub mod market_maker;
pub mod mathx;
pub mod microstructure;
pub mod mispricing;
pub mod order_book;
/// The runtime parameter seam: `ModelParams` and the preset table.
pub mod params;
/// Simulated constant-maturity bond indices priced off the engine's curve.
pub mod rates;
pub mod rng;
/// The twelve sectors and their model parameters.
pub mod sectors;
#[cfg(feature = "wasm")]
pub mod wasm;
pub mod types;
/// Universe generation - plausible rosters, deterministically.
pub mod universe;
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

/// ModelParams bindings - the runtime parameter seam's boundary.
#[cfg(feature = "python")]
mod python_params;

/// Arrow record batches for the results surface.
#[cfg(feature = "python")]
mod python_arrow;

/// The run log: every input, in order, sufficient to replay.
#[cfg(feature = "python")]
mod python_log;

/// Vectorised engines for sweeps and vector envs.
#[cfg(feature = "python")]
mod python_batch;

pub use rng::{stream, to_uint32, GameRng, Pcg32};
