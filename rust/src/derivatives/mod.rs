//! Derivatives on the simulated market, and what they read.
//!
//! Futures and options are priced from the model's own state and its own
//! expectations of that state. This module holds the pieces that do not
//! depend on one engine: the forecast the engine computes at each close
//! ([`Forecast`], under `ModelParams::forecast_horizon_sessions`), the
//! session-numbered expiry calendar ([`calendar`]), contract symbols
//! ([`ContractSymbol`]), and what a contract is, how it settles and what a
//! quote of one says ([`ContractSpec`], [`ContractKind`], [`Settlement`],
//! [`Quote`]). The price index the index contracts settle on is
//! [`crate::market::IndexLevel`], read through
//! [`crate::engine::Engine::index_level`], and the live VIX through
//! [`crate::engine::Engine::live_vix`].
//!
//! The engine lists index futures under `ModelParams::futures_index_listed`
//! VIX futures under `ModelParams::futures_vix_listed` and policy-rate and
//! term-rate futures under `ModelParams::futures_rates_listed` and oil futures
//! under `ModelParams::futures_oil_listed`, and walks the
//! index futures through the night under `ModelParams::night_session_steps`
//! ([`crate::engine::Engine::contracts`], [`crate::engine::Engine::quote`],
//! [`crate::engine::Engine::settlements`],
//! [`crate::engine::Engine::night_tick`]).
//!
//! Everything here reads state and writes nothing back, so a model that
//! turns these switches on prints the same stock prices as one that does
//! not.

pub mod calendar;
pub mod contract;
pub mod dealer;
pub mod forecast;
pub mod pricing;
pub mod surface;
pub mod symbol;

pub use contract::{
    index_future_symbol, index_option_symbol, oil_future_symbol, rate_future_symbol, vix_future_symbol, ContractKind,
    ContractSpec, IndexFutureSpec, IndexOptionSpec, OilFutureSpec, Quote, RateFutureSpec, RatePremium, Settlement,
    SettlementRule, VixFutureSpec, VixPremium, INDEX_FUTURE, INDEX_OPTION, OIL_FUTURE, POLICY_RATE_FUTURE,
    RATE_PREMIUM, TERM_RATE_FUTURE, VIX_FUTURE,
};
pub use dealer::{OptionDealerSpec, OptionQuote, INDEX_OPTION_DEALER};
pub use forecast::Forecast;
pub use pricing::Greeks;
pub use surface::{Ssvi, StripMoments, Surface};
pub use symbol::{ContractSymbol, Right, SymbolKind};
