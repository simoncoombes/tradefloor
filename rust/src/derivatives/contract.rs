//! Contracts: what a listed derivative is, how it settles, and what a quote
//! of one says.
//!
//! The engine lists contracts from the price index, the economy and the
//! roster; they are not roster instruments and hold no slot in a column.
//! Each is named by its [`crate::derivatives::ContractSymbol`], which does not
//! depend on the order contracts were listed in.

use super::symbol::{ContractSymbol, SymbolKind};

/// What kind of contract this is: index futures (`futures_index_listed`)
/// and VIX futures (`futures_vix_listed`) so far; the enum is non-exhaustive
/// so the rate and oil futures and the options of later releases join it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum ContractKind {
    /// A future on the engine's price index (`Engine::index_level`),
    /// cash-settled on the index of its expiry session's opening prints.
    IndexFuture,
    /// A future on the published VIX, cash-settled on the published VIX at
    /// its expiry session's open: the previous close's value.
    VixFuture,
}

impl ContractKind {
    /// The root its symbols carry.
    pub fn root(self) -> &'static str {
        match self {
            ContractKind::IndexFuture => "IDX",
            ContractKind::VixFuture => "VIX",
        }
    }

    /// Whether this is a future (rather than an option).
    pub fn is_future(self) -> bool {
        match self {
            ContractKind::IndexFuture | ContractKind::VixFuture => true,
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
            ContractKind::VixFuture => SettlementRule::PublishedVixAtOpen,
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
    /// In cash, on the published VIX at the expiry session's open, which is
    /// the previous close's value: the VIX moves only at a close.
    PublishedVixAtOpen,
}

impl SettlementRule {
    /// The rule's name, as the Python surface spells it.
    pub fn as_str(self) -> &'static str {
        match self {
            SettlementRule::OpeningPrintIndex => "opening_print_index",
            SettlementRule::PublishedVixAtOpen => "published_vix_at_open",
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
///
/// For a VIX future: `fair` is the expected settlement plus the premium,
/// moved within a session by the live VIX's surprise (`expected`,
/// `premium`, `loading`), `price` is `fair` plus the mark of agents' own
/// flow, `index` is the VIX the price reads (the live VIX in a session, the
/// published VIX outside one), and `rate` and `dividends` are 0.
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
    /// A VIX future's expected settlement, the forecast's published VIX at
    /// its expiry as the last close (or pin) expected it; `None` for other
    /// kinds.
    pub expected: Option<f64>,
    /// A VIX future's premium over its expected settlement now, VIX points;
    /// `None` for other kinds.
    pub premium: Option<f64>,
    /// A VIX future's intraday loading on the live VIX's surprise
    /// (`futures_vix_live_fast_share`); `None` for other kinds.
    pub loading: Option<f64>,
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
    /// session's opening prints; for a VIX future, the published VIX at the
    /// expiry session's open. `value` is set from it, so the two agree.
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

/// The VIX futures' premium over the expected settlement, frozen: fitted
/// once to the CFE VX monthly settlements, 266 contracts from May 2004,
/// and not to any row of the model's. For a contract `n` sessions from
/// expiry, on the VIX `vix`, the premium is `a(n) + b(n) (vix - centre)`,
/// with `a(n) = k (n - 1)^p` the settlement `n` sessions before expiry less
/// the final settlement, mean over contracts, and `b(n) = beta (1 -
/// exp(-(n - 1) / tau))` its slope on the VIX's close that day; both are 0
/// at `n = 1`.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct VixPremium {
    pub centre: f64,
    pub k: f64,
    pub p: f64,
    pub beta: f64,
    pub tau: f64,
}

impl VixPremium {
    /// The premium's level part, `a(n)`.
    pub fn level(&self, n: f64) -> f64 {
        if n <= 1.0 {
            0.0
        } else {
            self.k * crate::mathx::pow(n - 1.0, self.p)
        }
    }

    /// The premium's slope on the VIX, `b(n)`.
    pub fn slope(&self, n: f64) -> f64 {
        if n <= 1.0 {
            0.0
        } else {
            self.beta * (1.0 - crate::mathx::exp(-(n - 1.0) / self.tau))
        }
    }

    /// The premium of a contract `n` sessions from expiry on the VIX `vix`.
    pub fn at(&self, n: f64, vix: f64) -> f64 {
        self.level(n) + self.slope(n) * (vix - self.centre)
    }
}

/// The VIX future's specification: constants of the contract, not dials.
///
/// The book takes the index future's shape (`INDEX_FUTURE`): a maker's
/// ladder one tick apart, latent square-root depth at 0.75 and 0.5 out to
/// a day's volume, and agents' flow marking the price and decaying. The
/// stated daily volumes are about CFE VX's by position, 2023-2025.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct VixFutureSpec {
    /// Dollars per VIX point: 1,000, as CFE VX.
    pub multiplier: f64,
    /// The price grid: 0.05 VIX points, as CFE VX's outright tick.
    pub tick: f64,
    /// The daily volume of the front three contracts, in listing order,
    /// contracts; their books carry latent depth sized to it.
    pub front_daily_volume: [f64; 3],
    /// The daily volume each deferred contract's maker ladder is sized to.
    pub deferred_daily_volume: f64,
    /// The maker's levels a side, one tick apart.
    pub maker_levels: usize,
    /// Each maker level, as a share of the daily volume.
    pub maker_level_share: f64,
    /// The latent depth's coefficient, exponent and reach (in days' volume).
    pub depth_coefficient: f64,
    pub depth_exponent: f64,
    pub depth_reach: f64,
    /// Consumed latent depth refills at this half-life, in steps.
    pub refill_half_life: f64,
    /// A contract's daily sd of its log price, the sigma its latent depth
    /// and its flow's mark are scaled by: about the front CFE VX future's.
    pub daily_sigma: f64,
    /// Agents' net taker flow against the house moves the price by this
    /// times `daily_sigma` times the flow over the daily volume, a share of
    /// the price.
    pub impact_coefficient: f64,
    /// The flow's mark decays at this half-life, in steps.
    pub impact_half_life: f64,
    /// The premium over the expected settlement.
    pub premium: VixPremium,
}

/// The VIX future's specification.
pub const VIX_FUTURE: VixFutureSpec = VixFutureSpec {
    multiplier: 1000.0,
    tick: 0.05,
    front_daily_volume: [110_000.0, 55_000.0, 25_000.0],
    deferred_daily_volume: 8_000.0,
    maker_levels: 10,
    maker_level_share: 0.003,
    depth_coefficient: 0.75,
    depth_exponent: 0.5,
    depth_reach: 1.0,
    refill_half_life: 27.0,
    daily_sigma: 0.05,
    impact_coefficient: 0.15,
    impact_half_life: 30.0,
    premium: VixPremium { centre: 19.3197, k: 0.26, p: 0.4925, beta: 0.36, tau: 148.0 },
};

/// The symbol of the VIX future expiring at session `expiry`.
pub fn vix_future_symbol(expiry: i64) -> String {
    ContractSymbol { root: ContractKind::VixFuture.root().to_string(), expiry, kind: SymbolKind::Future }
        .to_string()
}

/// The symbol of the index future expiring at session `expiry`.
pub fn index_future_symbol(expiry: i64) -> String {
    ContractSymbol { root: ContractKind::IndexFuture.root().to_string(), expiry, kind: SymbolKind::Future }
        .to_string()
}
