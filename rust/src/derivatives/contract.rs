//! Contracts: what a listed derivative is, how it settles, and what a quote
//! of one says.
//!
//! The engine lists contracts from the price index, the economy and the
//! roster; they are not roster instruments and hold no slot in a column.
//! Each is named by its [`crate::derivatives::ContractSymbol`], which does not
//! depend on the order contracts were listed in.

use super::symbol::{ContractSymbol, SymbolKind};

/// What kind of contract this is. Index futures are the one kind listed so
/// far (`futures_index_listed`); the enum is non-exhaustive so the VIX,
/// rate and oil futures and the options of later releases join it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum ContractKind {
    /// A future on the engine's price index (`Engine::index_level`),
    /// cash-settled on the index of its expiry session's opening prints.
    IndexFuture,
}

impl ContractKind {
    /// The root its symbols carry.
    pub fn root(self) -> &'static str {
        match self {
            ContractKind::IndexFuture => "IDX",
        }
    }

    /// Whether this is a future (rather than an option).
    pub fn is_future(self) -> bool {
        match self {
            ContractKind::IndexFuture => true,
        }
    }

    /// `"future"` or `"option"`, as the Python surface spells it.
    pub fn family(self) -> &'static str {
        if self.is_future() {
            "future"
        } else {
            "option"
        }
    }

    /// How a contract of this kind settles.
    pub fn settlement(self) -> SettlementRule {
        match self {
            ContractKind::IndexFuture => SettlementRule::OpeningPrintIndex,
        }
    }
}

/// How a contract settles at expiry.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum SettlementRule {
    /// In cash, on the price index of the expiry session's opening prints:
    /// the level after the open has applied the night and the ex-dates, and
    /// before the session's first tick (the AM settlement of an index
    /// future).
    OpeningPrintIndex,
}

impl SettlementRule {
    /// The rule's name, as the Python surface spells it.
    pub fn as_str(self) -> &'static str {
        match self {
            SettlementRule::OpeningPrintIndex => "opening_print_index",
        }
    }
}

/// What a contract's specification fixes, as [`crate::engine::Engine::contracts`]
/// lists it.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct ContractSpec {
    /// The canonical symbol, `IDX.F0119` for the index future expiring at
    /// session 119.
    pub symbol: String,
    pub kind: ContractKind,
    /// The underlying's root: `IDX` for the price index.
    pub root: String,
    /// The session it expires at, counted from 0 as the engine's elapsed
    /// sessions are; it settles at that session's open.
    pub expiry: i64,
    /// The session from which the next contract is the front by the roll
    /// rule: [`super::calendar::ROLL_SESSIONS`] before the expiry.
    pub roll: i64,
    /// Dollars per index point.
    pub multiplier: f64,
    /// The price grid, index points.
    pub tick: f64,
    pub settlement: SettlementRule,
    /// Whether it is the front contract by the roll rule: the first listed
    /// contract whose roll date has not come.
    pub front: bool,
}

/// A contract's quote, as [`crate::engine::Engine::quote`] reads it.
///
/// For an index future: `fair` is the carry fair value, `(S - PV(D)) *
/// exp(r tau)`, `price` is `fair` plus the basis, the price the maker quotes
/// around, and `bid` and `ask` are the agent-facing book's touch.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct Quote {
    pub symbol: String,
    pub kind: ContractKind,
    pub expiry: i64,
    /// The book's best bid and ask, and their mean. `None` on an empty side.
    pub bid: Option<f64>,
    pub ask: Option<f64>,
    pub mid: Option<f64>,
    /// The futures price now: `fair` plus the basis.
    pub price: f64,
    /// The carry fair value now.
    pub fair: f64,
    /// The last close's settlement mark, `price` at that close. `None` before
    /// the contract's first close.
    pub mark: Option<f64>,
    /// The basis, `price - fair`, in basis points of the index.
    pub basis_bp: f64,
    /// The index level `fair` reads: the live level within a session, the
    /// night's path between sessions under `night_session_steps`, and the
    /// close otherwise.
    pub index: f64,
    /// The financing rate `fair` compounds at: the average expected policy
    /// rate to expiry, a fraction a year.
    pub rate: f64,
    /// The present value of the dividends going ex before the contract
    /// settles, index points.
    pub dividends: f64,
    /// Sessions from now to the expiry's open, the carry's time.
    pub sessions_to_expiry: f64,
    pub multiplier: f64,
    pub tick: f64,
    /// The daily volume the book's depth is sized to, contracts.
    pub daily_volume: f64,
    /// The initial margin a contract asks. `None` until margin is listed.
    pub initial_margin: Option<f64>,
}

/// A contract's final settlement, as [`crate::engine::Engine::settlements`]
/// lists it.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct Settlement {
    pub symbol: String,
    pub kind: ContractKind,
    pub root: String,
    /// The session it settled at.
    pub session: i64,
    /// The settlement price.
    pub value: f64,
    /// What it settles on: for an index future, the index of the expiry
    /// session's opening prints. `value` is set from it, so the two agree.
    pub reference: f64,
}

/// The index future's specification: constants of the contract, not dials.
///
/// The book's latent depth and the flow's impact take the shape pt-v21
/// gives the equities' book (`book_depth_coefficient` 0.75 and exponent 0.5
/// out to a day's volume, refilling at 27 ticks; `fill_impact_coefficient`
/// 0.15), so the futures book prices size as a name's does, whatever the
/// equity dials of the model it runs under.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct IndexFutureSpec {
    /// Dollars per index point: 250, so a contract on the base level of
    /// 1,000 is $250,000, about an E-mini S&P 500 at 5,000.
    pub multiplier: f64,
    /// The price grid: 0.05 index points, half a basis point at the base.
    pub tick: f64,
    /// The stated daily volume, as a share of the index constituents' daily
    /// dollar volume: 1.0, the futures trading the cash market's notional,
    /// as E-minis roughly do.
    pub volume_share: f64,
    /// The maker's levels a side, one tick apart.
    pub maker_levels: usize,
    /// Each maker level, as a share of the daily volume: 0.3 per cent, so
    /// ten levels hold 3 per cent a side, inside the 2.6 to 5 per cent the
    /// equity maker quotes (`crate::agent_book`).
    pub maker_level_share: f64,
    /// The latent depth's coefficient, exponent and reach (in days' volume).
    pub depth_coefficient: f64,
    pub depth_exponent: f64,
    pub depth_reach: f64,
    /// Consumed latent depth refills at this half-life, in steps.
    pub refill_half_life: f64,
    /// Agents' net taker flow against the house moves the basis by this
    /// times the index's daily sigma times the flow over the daily volume.
    pub impact_coefficient: f64,
    /// The flow's mark on the basis decays at this half-life, in steps:
    /// index arbitrage closes a futures dislocation within the half hour.
    pub impact_half_life: f64,
}

/// The index future's specification.
pub const INDEX_FUTURE: IndexFutureSpec = IndexFutureSpec {
    multiplier: 250.0,
    tick: 0.05,
    volume_share: 1.0,
    maker_levels: 10,
    maker_level_share: 0.003,
    depth_coefficient: 0.75,
    depth_exponent: 0.5,
    depth_reach: 1.0,
    refill_half_life: 27.0,
    impact_coefficient: 0.15,
    impact_half_life: 30.0,
};

/// The symbol of the index future expiring at session `expiry`.
pub fn index_future_symbol(expiry: i64) -> String {
    ContractSymbol { root: ContractKind::IndexFuture.root().to_string(), expiry, kind: SymbolKind::Future }
        .to_string()
}
