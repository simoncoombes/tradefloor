//! What the model's derivatives read.
//!
//! Futures and options on the simulated market are priced from the model's
//! own state and its own expectations of that state. This module holds the
//! pieces that do not depend on any one contract: the forecast the engine
//! computes at each close ([`Forecast`], under
//! `ModelParams::forecast_horizon_sessions`). The price index the index
//! contracts settle on is [`crate::market::IndexLevel`], read through
//! [`crate::engine::Engine::index_level`], and the live VIX through
//! [`crate::engine::Engine::live_vix`].
//!
//! Everything here reads state and writes nothing back, so a model that
//! turns these switches on prints the same prices as one that does not.

pub mod forecast;

pub use forecast::Forecast;
