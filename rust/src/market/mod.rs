//! Market — the stationary price path, ported from `src/lib/engine/market.ts`
//! plus the daily-lifecycle pieces in `src/lib/stores/tick/transitions.ts`.
//!
//! # Scope
//!
//! The LIVE branch only. `STATIONARY_PRICE_MODEL` and `BOOK_PRICE_DISCOVERY`
//! are both hardcoded `true` (`market.ts:1225`, `:1208`), so the legacy
//! 14-factor path and the pre-book settlement path are dead code.
//!
//! **D7, decided:** the legacy branch is NOT ported as a Rust revert path.
//! The kill switch falls back to TypeScript, not to a Rust legacy mode, so a
//! Rust copy would be a second dead model — and two models that can disagree
//! is the outcome this project exists to avoid.
//!
//! # The draw schedule
//!
//! `simulate_market_tick` consumes, in this order:
//!
//! | Position | Draws |
//! |---|---|
//! | Once per tick | 1 normal (market factor), then 1 normal per sector |
//! | Per active company, phase 1 | 1 normal (GARCH idiosyncratic), then 1 uniform |
//! | Per active company, settlement | 4 uniforms, market-open only — unconditional under `SettleDrawPolicy::FourAlways` (the engine's generated schedule), or four-or-zero as `microstructure::settle_price_through_book`'s guards decide under `FourOrZero` (recorded-stream replay) |
//!
//! "Active" means not bankrupt and public, in `companies` array order. The
//! order is contractual: the uniform drawn at the end of each phase-1 body is
//! consumed much later, so a port that batched the normals separately from
//! the uniforms would produce the same counts and a different stream.
//!
//! Under `FourAlways` the whole schedule is a pure function of (market
//! status, active set, sector count) — the 2026-08 era's alignment
//! guarantee: no price, macro value or order flow can move the market
//! stream's position.
//!
//! # D6 — the circuit breaker is a SESSION band
//!
//! The ±25% breaker clamps against `previousClose` during 09:30–16:00 and is
//! applied TWICE, deliberately: once to the model price (`fairValue × exp(s)`)
//! and again to the settled print. The second clamp is the one that matters —
//! a breaker that bounds an unobservable reference and not the tape is not a
//! breaker, and the gap between them is where the measured 40.30% violations
//! lived.
//!
//! The overnight gap is left unclamped BY DESIGN. Real LULD bands apply
//! during continuous trading, and earnings gaps arrive after the close and
//! routinely exceed 25%. The residual outliers (4 of 2,160 runs, max 29.38%)
//! are that gap and are not a defect. See the port notes §D6.

pub mod daily;
pub mod factors;
pub mod garch;
pub mod hours;
pub mod index_value;
pub mod tick;

pub use daily::{close_day, close_day_all, reset_daily_prices, AvgVolumePolicy, CloseInputs};
pub use factors::{
    calculate_live_factors, order_imbalance, FactorCompany, LiveFactors, NewsEvent, SharedFactors,
};
pub use garch::update_garch_variance;
pub use hours::{
    get_market_status, intraday_fraction, intraday_vol, intraday_volume, is_market_open, GameTime,
    MarketStatus, MARKET_MINUTES,
};
pub use index_value::{calculate_market_index, IndexConstituent, IndexValue};
pub use tick::{
    simulate_market_tick, NewsImpactEntry, OrderVolume, SettleDrawPolicy, TickCompany, TickInputs,
    TickOutcome, TickStock, S_PHI_TICK,
};
