//! The index futures (`futures_index_listed`) and their night session
//! (`night_session_steps`): the engine's half.
//!
//! A future reads the index, the dividends, the forecast and the market
//! factor's sigma, and writes only its own state ([`FuturesState`]): its
//! basis, its book, its marks and its settlements, and the generator on
//! `stream::DERIVATIVES` they draw from. So every stock price, close and
//! economy is the one the engine prints with these switches off.
//!
//! # The price
//!
//! `F = (S - PV(D)) * exp(r * tau) + b`, in index points. `S` is the index
//! the futures read: the live level within a session, the night's path
//! between sessions (or the close's level without a night session). `tau`
//! is the sessions from now to the expiry's open over 252, where a session
//! that has run `k` of its 390 ticks has used `k / 390` of its day and the
//! night uses none. `r` is the mean over the sessions to expiry of the
//! policy rate the forecast expects to be in force in each
//! (`forecast_horizon_sessions`), or the policy rate now without one. `D`
//! is each constituent's dividend for every ex-date after the last open and
//! no later than the expiry, in index points (shares times the amount over
//! the divisor), discounted at `r` from its ex-date: the declared amount
//! within 21 sessions of the ex-date, beyond it the Lintner rule's
//! declaration on today's state, iterated a quarter at a time. `b` is the
//! basis noise (an AR(1) in index basis points, `basis_sd` and
//! `basis_persistence`) plus each contract's mark of agents' own flow, both
//! scaled by `min(1, sessions to expiry / 6)` so the future converges on the
//! index as it expires.
//!
//! # The night
//!
//! Under `night_session_steps` the close runs the next open on a copy of
//! the engine. The copy takes the OVERNIGHT stream's draws the real open
//! will take, since nothing draws on that stream but the open and nothing
//! between a close and the next open sets an input to the draws, so the
//! copy's opening prints are the real open's to the bit. Its index and each
//! future's fair value on those prints are the night's targets, and
//! [`Engine::night_tick`] walks each future's log fair value to its target
//! on a Brownian bridge with the night's share of the index's one-session
//! variance, all on one normal per step, so the last step lands on the
//! open. The real open takes its own draws as it always did. A host's write
//! between sessions (a pin, a listing or delisting, a change of status, and
//! from Python a fundamentals write) reruns the copy and moves the night's
//! path by the change in its targets at once. A write the copy is not rerun
//! for (a column or a price written from Rust, a draw patched on the
//! OVERNIGHT stream) reaches the futures at the open.

use super::*;
use crate::agent_book::{
    remove_front, AgentFill, AgentOrder, BookState, Liquidity, RestMode, AGENT_BOOK_CAP, DEPTH_OWNER,
};
use crate::derivatives::calendar::{self, ROLL_SESSIONS, SESSIONS_PER_YEAR};
use crate::derivatives::{
    index_future_symbol, ContractKind, ContractSpec, ContractSymbol, IndexFutureSpec, Quote, Settlement,
    SymbolKind, INDEX_FUTURE,
};
use crate::market_maker::MARKET_MAKER_ID;
use crate::order_book::{OrderBook, Side, SubmitOptions};

/// The ticks in a session, the day the futures' clock divides.
const SESSION_TICKS: f64 = 390.0;

/// Indices into [`ListedFuture::taken`]: what agents took from the front of
/// the maker's ladder and of the latent depth, contracts.
const TAKEN_MAKER_BID: usize = 0;
const TAKEN_MAKER_ASK: usize = 1;
const TAKEN_DEPTH_BID: usize = 2;
const TAKEN_DEPTH_ASK: usize = 3;

/// The smallest cumulative size, as a share of daily volume, the latent
/// grid descends to, and the ratio from one level to the next: the equity
/// book's (`crate::agent_book`).
const DEPTH_GRID_FLOOR: f64 = 1e-4;
const DEPTH_LEVEL_RATIO: f64 = 1.25;

/// Where the futures stand in the day: before the first open, in a session,
/// or between a close and the next open.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum FuturesPhase {
    BeforeFirstOpen,
    Session,
    Night,
}

impl FuturesPhase {
    fn code(self) -> f64 {
        match self {
            FuturesPhase::BeforeFirstOpen => 0.0,
            FuturesPhase::Session => 1.0,
            FuturesPhase::Night => 2.0,
        }
    }

    fn from_code(v: f64) -> Option<Self> {
        if v == 0.0 {
            Some(FuturesPhase::BeforeFirstOpen)
        } else if v == 1.0 {
            Some(FuturesPhase::Session)
        } else if v == 2.0 {
            Some(FuturesPhase::Night)
        } else {
            None
        }
    }
}

/// One listed index future.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ListedFuture {
    pub(crate) expiry: i64,
    /// The last close's settlement mark, and the fair value it was set on.
    /// NaN before the contract's first close.
    pub(crate) mark: f64,
    pub(crate) fair_close: f64,
    /// Agents' flow's mark on the basis, index basis points.
    pub(crate) impact: f64,
    /// Agents' net taker contracts against the house since the last step,
    /// which the next step adds to `impact`.
    pub(crate) pending: f64,
    /// See the `TAKEN_*` indices.
    pub(crate) taken: [f64; 4],
}

impl ListedFuture {
    fn new(expiry: i64) -> Self {
        ListedFuture {
            expiry,
            mark: f64::NAN,
            fair_close: f64::NAN,
            impact: 0.0,
            pending: 0.0,
            taken: [0.0; 4],
        }
    }
}

/// Numbers per listed future in [`FuturesState::to_words`].
const CONTRACT_WIDTH: usize = 9;

/// The night's path, between a close and the next open.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct NightBridge {
    /// The night's steps, and those walked.
    pub(crate) steps: u32,
    pub(crate) done: u32,
    /// The night's variance of the log index, fraction squared.
    pub(crate) variance: f64,
    /// The contracts bridged, by expiry, in listing order.
    pub(crate) expiries: Vec<i64>,
    /// The index, then each contract's fair value: where the path is, and
    /// where the open will put it. Levels, not logs.
    pub(crate) now: Vec<f64>,
    pub(crate) target: Vec<f64>,
}

impl NightBridge {
    /// The bridge as one flat list: steps, done, variance, the count `n`,
    /// the `n` expiries, then `now` and `target`, `n + 1` each.
    pub(crate) fn to_words(&self) -> Vec<f64> {
        let n = self.expiries.len();
        let mut out = Vec::with_capacity(4 + n + 2 * (n + 1));
        out.push(f64::from(self.steps));
        out.push(f64::from(self.done));
        out.push(self.variance);
        out.push(n as f64);
        out.extend(self.expiries.iter().map(|e| *e as f64));
        out.extend_from_slice(&self.now);
        out.extend_from_slice(&self.target);
        out
    }

    /// The inverse of [`NightBridge::to_words`], refusing a list that is not
    /// one.
    pub(crate) fn from_words(words: &[f64]) -> Result<Self, String> {
        let bad = |why: &str| format!("this snapshot's night_bridge does not parse: {why}");
        let whole = |v: f64, what: &str| -> Result<u64, String> {
            if v.is_finite() && v >= 0.0 && v.fract() == 0.0 && v <= 1e9 {
                Ok(v as u64)
            } else {
                Err(bad(&format!("{what} is {v}")))
            }
        };
        if words.len() < 4 {
            return Err(bad("fewer than four numbers"));
        }
        let steps = whole(words[0], "the step count")? as u32;
        let done = whole(words[1], "the steps walked")? as u32;
        let variance = words[2];
        let n = whole(words[3], "the contract count")? as usize;
        if words.len() != 4 + n + 2 * (n + 1) {
            return Err(bad(&format!("{} numbers for {n} contracts", words.len())));
        }
        if done > steps || !(variance.is_finite() && variance >= 0.0) {
            return Err(bad(&format!("{done} of {steps} steps walked at a variance of {variance}")));
        }
        let mut expiries = Vec::with_capacity(n);
        for v in &words[4..4 + n] {
            if !(v.is_finite() && v.fract() == 0.0) {
                return Err(bad(&format!("an expiry is {v}")));
            }
            expiries.push(*v as i64);
        }
        let now = words[4 + n..4 + n + n + 1].to_vec();
        let target = words[4 + n + n + 1..].to_vec();
        if now.iter().chain(target.iter()).any(|v| !(v.is_finite() && *v > 0.0)) {
            return Err(bad("a level is not finite and positive"));
        }
        Ok(NightBridge { steps, done, variance, expiries, now, target })
    }
}

/// Everything the index futures carry.
#[derive(Debug, Clone)]
pub(crate) struct FuturesState {
    pub(crate) phase: FuturesPhase,
    /// The basis noise, index basis points.
    pub(crate) basis: f64,
    /// The listed contracts, in expiry order.
    pub(crate) contracts: Vec<ListedFuture>,
    /// Every final settlement so far, in order.
    pub(crate) settlements: Vec<Settlement>,
    /// Agents' resting orders on contracts, their undelivered fills and the
    /// arrival and fill counters: the equity book's shape, its `taken`,
    /// `flow`, `impacts` and `memory` unused.
    pub(crate) book: BookState,
    pub(crate) night: Option<NightBridge>,
    /// `stream::DERIVATIVES`.
    pub(crate) rng: GameRng,
}

impl FuturesState {
    pub(crate) fn new(seed: u64) -> Self {
        FuturesState {
            phase: FuturesPhase::BeforeFirstOpen,
            basis: 0.0,
            contracts: Vec::new(),
            settlements: Vec::new(),
            book: BookState::default(),
            night: None,
            rng: GameRng::substream(seed, stream::DERIVATIVES),
        }
    }

    /// The numbers the snapshot's `futures` key and the state hash carry:
    /// the phase, the basis, the contract count and each contract's expiry,
    /// mark, closing fair value, impact, pending flow and four taken
    /// counts, then the settlement count and each settlement's session,
    /// value and reference. NaN where a contract has no mark yet.
    pub(crate) fn to_words(&self) -> Vec<f64> {
        let mut out = Vec::with_capacity(
            4 + CONTRACT_WIDTH * self.contracts.len() + 3 * self.settlements.len());
        out.push(self.phase.code());
        out.push(self.basis);
        out.push(self.contracts.len() as f64);
        for c in &self.contracts {
            out.push(c.expiry as f64);
            out.push(c.mark);
            out.push(c.fair_close);
            out.push(c.impact);
            out.push(c.pending);
            out.extend_from_slice(&c.taken);
        }
        out.push(self.settlements.len() as f64);
        for s in &self.settlements {
            out.push(s.session as f64);
            out.push(s.value);
            out.push(s.reference);
        }
        out
    }

    /// Put back what [`FuturesState::to_words`] wrote, refusing a list that
    /// is not one. The book, the night and the generator are carried apart.
    pub(crate) fn set_words(&mut self, words: &[f64]) -> Result<(), String> {
        let bad = |why: &str| format!("this snapshot's futures do not parse: {why}");
        let whole = |v: f64, what: &str| -> Result<usize, String> {
            if v.is_finite() && v >= 0.0 && v.fract() == 0.0 && v <= 1e9 {
                Ok(v as usize)
            } else {
                Err(bad(&format!("{what} is {v}")))
            }
        };
        if words.len() < 4 {
            return Err(bad("fewer than four numbers"));
        }
        let phase = FuturesPhase::from_code(words[0])
            .ok_or_else(|| bad(&format!("the phase is {}", words[0])))?;
        let basis = words[1];
        if !basis.is_finite() {
            return Err(bad(&format!("the basis is {basis}")));
        }
        let n = whole(words[2], "the contract count")?;
        let mut at = 3;
        if words.len() < at + CONTRACT_WIDTH * n + 1 {
            return Err(bad("it is shorter than its counts say"));
        }
        let mut contracts = Vec::with_capacity(n);
        for _ in 0..n {
            let w = &words[at..at + CONTRACT_WIDTH];
            if !(w[0].is_finite() && w[0].fract() == 0.0) {
                return Err(bad(&format!("an expiry is {}", w[0])));
            }
            if w[3..].iter().any(|v| !v.is_finite()) {
                return Err(bad("a contract's impact, flow or taken depth is not finite"));
            }
            contracts.push(ListedFuture {
                expiry: w[0] as i64,
                mark: w[1],
                fair_close: w[2],
                impact: w[3],
                pending: w[4],
                taken: [w[5], w[6], w[7], w[8]],
            });
            at += CONTRACT_WIDTH;
        }
        let m = whole(words[at], "the settlement count")?;
        at += 1;
        if words.len() != at + 3 * m {
            return Err(bad(&format!("{} numbers for {n} contracts and {m} settlements", words.len())));
        }
        let mut settlements = Vec::with_capacity(m);
        for k in 0..m {
            let w = &words[at + 3 * k..at + 3 * k + 3];
            if !(w[0].is_finite() && w[0].fract() == 0.0 && w[1].is_finite() && w[2].is_finite()) {
                return Err(bad("a settlement is not a session and two finite prices"));
            }
            settlements.push(settlement_of(w[0] as i64, w[2]));
        }
        self.phase = phase;
        self.basis = basis;
        self.contracts = contracts;
        self.settlements = settlements;
        Ok(())
    }
}

/// The index future expiring at `session`, settled on `reference`.
fn settlement_of(session: i64, reference: f64) -> Settlement {
    let kind = ContractKind::IndexFuture;
    Settlement {
        symbol: index_future_symbol(session),
        kind,
        root: kind.root().to_string(),
        session,
        value: reference,
        reference,
    }
}

/// How far a future's basis has converged: `min(1, sessions to expiry /
/// 6)`, the six sessions before expiry being the roll's.
fn convergence(sessions_to_expiry: f64) -> f64 {
    crate::mathx::max(0.0, crate::mathx::min(1.0, sessions_to_expiry / ROLL_SESSIONS as f64))
}

/// A price on the contract's grid, rounded away from the touch: up for an
/// ask, down for a bid.
fn on_grid(price: f64, tick: f64, side: Side) -> f64 {
    let steps = price / tick;
    match side {
        Side::Sell => (steps - 1e-7).ceil() * tick,
        Side::Buy => (steps + 1e-7).floor() * tick,
    }
}

/// What a future is worth now, and from what.
#[derive(Debug, Clone, Copy)]
pub(crate) struct Valuation {
    pub(crate) index: f64,
    pub(crate) fair: f64,
    pub(crate) rate: f64,
    pub(crate) dividends: f64,
    pub(crate) sessions: f64,
    /// The basis now, index basis points, after the convergence.
    pub(crate) basis_bp: f64,
    pub(crate) price: f64,
}

impl Engine {
    // ── State and clock ───────────────────────────────────────────────────

    pub(super) fn futures_on(&self) -> bool {
        self.params.futures_index_listed != 0.0
    }

    /// The night's steps: 0 without a night session.
    fn night_steps(&self) -> u32 {
        if self.futures_on() {
            self.params.night_session_steps as u32
        } else {
            0
        }
    }

    /// Now on the futures' clock, in sessions: the session of the last open
    /// plus the share of its ticks run, or the next session's open between
    /// sessions.
    fn futures_now(&self) -> f64 {
        let t = self.elapsed_days as f64;
        match self.futures.phase {
            FuturesPhase::BeforeFirstOpen => t,
            FuturesPhase::Session => {
                t + crate::mathx::min(1.0, f64::from(self.ticks_today()) / SESSION_TICKS)
            }
            FuturesPhase::Night => t + 1.0,
        }
    }

    /// Expiries strictly after this session are listed: the last open's, or
    /// the one before the next open before any.
    fn futures_listing_after(&self) -> i64 {
        match self.futures.phase {
            FuturesPhase::BeforeFirstOpen => self.elapsed_days - 1,
            _ => self.elapsed_days,
        }
    }

    /// List the front two on a fresh engine. Nothing with the switch off.
    pub(super) fn futures_list_initial(&mut self) {
        if !self.futures_on() {
            return;
        }
        let after = self.futures_listing_after();
        self.futures.contracts = calendar::index_future_expiries(after, calendar::INDEX_FUTURES_LISTED)
            .into_iter()
            .map(ListedFuture::new)
            .collect();
    }

    /// The listed contract named `symbol`, by slot.
    fn futures_slot(&self, symbol: &str) -> Option<usize> {
        let parsed = ContractSymbol::parse(symbol).ok()?;
        if parsed.root != ContractKind::IndexFuture.root() || parsed.kind != SymbolKind::Future {
            return None;
        }
        self.futures.contracts.iter().position(|c| c.expiry == parsed.expiry)
    }

    /// Whether `symbol` names a listed contract: an index, VIX, rate or oil
    /// future under its family's switch.
    pub fn is_listed_contract(&self, symbol: &str) -> bool {
        (self.futures_on() && self.futures_slot(symbol).is_some())
            || (self.vix_futures_on() && self.vix_futures_slot(symbol).is_some())
            || (self.rate_futures_on() && self.rate_futures_slot(symbol).is_some())
            || (self.oil_futures_on() && self.oil_futures_slot(symbol).is_some())
    }

    /// Whether an order with the id `id` already waits in any book: the
    /// names' or a contract family's.
    pub(super) fn contract_order_id_taken(&self, id: &str) -> bool {
        self.book.orders.iter().any(|o| o.id == id)
            || self.futures.book.orders.iter().any(|o| o.id == id)
            || self.vix_futures_orders().iter().any(|o| o.id == id)
            || self.rate_futures_orders().iter().any(|o| o.id == id)
            || self.oil_futures_orders().iter().any(|o| o.id == id)
    }

    // ── Pricing ───────────────────────────────────────────────────────────

    /// The mean policy rate expected to be in force over the sessions from
    /// now to `expiry`, a fraction a year: the forecast's path where there
    /// is one, the policy rate now where there is not.
    fn futures_rate(&self, expiry: i64, now: f64) -> f64 {
        let current = self.economy.federal_funds_rate;
        let Some(f) = self.forecast() else {
            return current / 100.0;
        };
        let first = crate::mathx::max(now.floor(), f.day as f64) as i64;
        if expiry <= first || f.policy_rate.is_empty() {
            return current / 100.0;
        }
        let last = f.policy_rate.len() as i64;
        let mut total = 0.0;
        for s in first..expiry {
            // The rate in force in session `s` is the one set at the close
            // before it: the policy rate now for the forecast's own day, the
            // forecast's `h`-session expectation for `s = day + h`.
            let h = s - f.day;
            total += if h <= 0 {
                current
            } else {
                f.policy_rate[(crate::mathx::min(h as f64, last as f64) - 1.0) as usize]
            };
        }
        total / (expiry - first) as f64 / 100.0
    }

    /// The present value, in index points at `rate`, of the constituents'
    /// dividends going ex after the last open and no later than `expiry`.
    fn futures_dividends(&self, expiry: i64, rate: f64, now: f64) -> f64 {
        use crate::market::dividends as dv;
        if self.params.dividend_payout_share == 0.0 || !(self.index_divisor > 0.0) {
            return 0.0;
        }
        let after = self.futures_listing_after();
        let mut total = 0.0;
        for c in &self.companies {
            if !c.is_public || c.is_bankrupt {
                continue;
            }
            // The same lookahead the American options' pricer reads, so a
            // name's projected dividends are one number wherever they enter.
            for u in dv::lookahead(&self.params, &c.ticker, c.stock.dividend.as_ref(), after, c.stock.price, expiry) {
                let points = c.stock.shares_outstanding * u.amount / self.index_divisor;
                total += points * crate::mathx::exp(-rate * (u.ex_day as f64 - now) / SESSIONS_PER_YEAR as f64);
            }
        }
        total
    }

    /// The carry fair value of the future expiring at `expiry`, on the index
    /// `index` at `now`: `(S - PV(D)) * exp(r tau)`, with its parts.
    fn futures_carry(&self, expiry: i64, index: f64, now: f64) -> (f64, f64, f64, f64) {
        let sessions = crate::mathx::max(0.0, expiry as f64 - now);
        let rate = self.futures_rate(expiry, now);
        let dividends = self.futures_dividends(expiry, rate, now);
        let fair = (index - dividends) * crate::mathx::exp(rate * sessions / SESSIONS_PER_YEAR as f64);
        (fair, rate, dividends, sessions)
    }

    /// The index the futures read in a session: the live level.
    fn futures_live_index(&self) -> f64 {
        if self.index_divisor > 0.0 {
            self.index_level_now()
        } else {
            crate::market::index_value::INDEX_BASE
        }
    }

    /// What the contract in `slot` is worth now.
    pub(crate) fn futures_valuation(&self, slot: usize) -> Valuation {
        let c = &self.futures.contracts[slot];
        let now = self.futures_now();
        let (index, fair, rate, dividends, sessions) = match self.futures.phase {
            FuturesPhase::Night => {
                let bridged = self.futures.night.as_ref().and_then(|b| {
                    b.expiries.iter().position(|e| *e == c.expiry).map(|j| (b.now[0], b.now[1 + j]))
                });
                let index = match bridged {
                    Some((i, _)) => i,
                    None if self.index_close > 0.0 => self.index_close,
                    None => self.futures_live_index(),
                };
                let (carry, rate, dividends, sessions) = self.futures_carry(c.expiry, index, now);
                let fair = match bridged {
                    Some((_, f)) => f,
                    None if c.fair_close.is_finite() => c.fair_close,
                    None => carry,
                };
                (index, fair, rate, dividends, sessions)
            }
            _ => {
                let index = self.futures_live_index();
                let (fair, rate, dividends, sessions) = self.futures_carry(c.expiry, index, now);
                (index, fair, rate, dividends, sessions)
            }
        };
        let basis_bp = convergence(sessions) * (self.futures.basis + c.impact);
        Valuation { index, fair, rate, dividends, sessions, basis_bp, price: fair + basis_bp * index / 1e4 }
    }

    /// The daily volume the futures' depth is sized to, contracts: the
    /// constituents' daily dollar volume times the spec's share, over a
    /// contract's notional at `index`.
    fn futures_daily_volume(&self, index: f64) -> f64 {
        let spec = &INDEX_FUTURE;
        let mut dollars = 0.0;
        for c in &self.companies {
            if c.is_public && !c.is_bankrupt {
                dollars += crate::agent_book::daily_volume(c) * c.stock.price;
            }
        }
        let notional = spec.multiplier * index;
        if !(notional > 0.0) || !(dollars > 0.0) {
            return 1.0;
        }
        crate::mathx::max(1.0, spec.volume_share * dollars / notional)
    }

    // ── The book ──────────────────────────────────────────────────────────

    /// The latent depth on one side of a contract's book: the equity book's
    /// square-root pool (`agent_book::append_latent_depth`) at the spec's
    /// shape, on the contract's grid.
    fn futures_depth(book: &mut OrderBook, side: Side, touch: f64, sigma: f64, volume: f64, spec: &IndexFutureSpec) {
        let y = spec.depth_coefficient;
        let reach = spec.depth_reach * volume;
        if !(y > 0.0 && volume > 0.0 && touch > 0.0 && sigma > 0.0) {
            return;
        }
        let floor = volume * DEPTH_GRID_FLOOR;
        let mut bounds = vec![reach];
        while let Some(&last) = bounds.last() {
            let next = last / DEPTH_LEVEL_RATIO;
            if next < floor {
                break;
            }
            bounds.push(next);
        }
        bounds.reverse();
        let mut placed = 0.0;
        let mut pending: Option<(f64, f64)> = None;
        let mut levels: Vec<(f64, f64)> = Vec::new();
        for bound in bounds {
            let distance = y * sigma * crate::mathx::pow(bound / volume, spec.depth_exponent);
            let raw = match side {
                Side::Sell => touch * (1.0 + distance),
                Side::Buy => touch * (1.0 - distance),
            };
            let price = on_grid(raw, spec.tick, side);
            if !(price > 0.0) {
                break;
            }
            let shares = bound.floor() - placed;
            if !(shares >= 1.0) {
                continue;
            }
            placed += shares;
            pending = match pending {
                Some((p, q)) if p == price => Some((p, q + shares)),
                Some(level) => {
                    levels.push(level);
                    Some((price, shares))
                }
                None => Some((price, shares)),
            };
        }
        if let Some(level) = pending {
            levels.push(level);
        }
        for (price, shares) in levels {
            book.rest_limit(side, price, shares, DEPTH_OWNER);
        }
    }

    /// The agent-facing book of the contract in `slot`, leaving out one
    /// agent's orders: the maker's ladder around the futures price, the
    /// latent depth beside it, less what agents have taken from the front
    /// of each, and every agent's resting orders.
    fn futures_book(&self, slot: usize, exclude: Option<&str>) -> OrderBook {
        let spec = &INDEX_FUTURE;
        let c = &self.futures.contracts[slot];
        let v = self.futures_valuation(slot);
        let symbol = index_future_symbol(c.expiry);
        let volume = self.futures_daily_volume(v.index);
        let sigma = self.market_vol.sigma_daily();
        let mut book = OrderBook::new(symbol.clone(), Some(v.price)).with_cap(AGENT_BOOK_CAP);
        let size = crate::mathx::max(1.0, (volume * spec.maker_level_share).floor());
        let n = (v.price / spec.tick).floor();
        for k in 0..spec.maker_levels {
            let k = k as f64;
            book.rest_limit(Side::Buy, (n - k) * spec.tick, size, MARKET_MAKER_ID);
            book.rest_limit(Side::Sell, (n + 1.0 + k) * spec.tick, size, MARKET_MAKER_ID);
        }
        let bid = n * spec.tick;
        let ask = (n + 1.0) * spec.tick;
        Self::futures_depth(&mut book, Side::Buy, bid, sigma, volume, spec);
        Self::futures_depth(&mut book, Side::Sell, ask, sigma, volume, spec);
        remove_front(&mut book, Side::Buy, MARKET_MAKER_ID, c.taken[TAKEN_MAKER_BID]);
        remove_front(&mut book, Side::Sell, MARKET_MAKER_ID, c.taken[TAKEN_MAKER_ASK]);
        remove_front(&mut book, Side::Buy, DEPTH_OWNER, c.taken[TAKEN_DEPTH_BID]);
        remove_front(&mut book, Side::Sell, DEPTH_OWNER, c.taken[TAKEN_DEPTH_ASK]);
        for o in &self.futures.book.orders {
            if o.ticker != symbol || exclude == Some(o.agent.as_str()) {
                continue;
            }
            book.post_limit(o.side, o.limit, o.remaining, &o.agent, Some(o.id.clone()));
        }
        book
    }

    /// The agent-facing book of a listed contract: `None` for a symbol not
    /// listed.
    pub fn contract_book(&self, symbol: &str) -> Option<OrderBook> {
        if self.vix_futures_on() {
            if let Some(slot) = self.vix_futures_slot(symbol) {
                return self.vix_futures_book_at(slot);
            }
        }
        if self.rate_futures_on() {
            if let Some(slot) = self.rate_futures_slot(symbol) {
                return self.rate_futures_book_at(slot);
            }
        }
        if self.oil_futures_on() {
            if let Some(slot) = self.oil_futures_slot(symbol) {
                return self.oil_futures_book_at(slot);
            }
        }
        if !self.futures_on() {
            return None;
        }
        let slot = self.futures_slot(symbol)?;
        Some(self.futures_book(slot, None))
    }

    /// The listed contract at position `k` in listing order (the index
    /// futures, then the VIX, rate and oil futures), for
    /// [`Engine::book_for`]'s indices past the rate instruments.
    pub(super) fn contract_book_at(&self, k: usize) -> Option<OrderBook> {
        let index = if self.futures_on() { self.futures.contracts.len() } else { 0 };
        if k < index {
            return Some(self.futures_book(k, None));
        }
        let vix = if self.vix_futures_on() { self.vix_futures.contracts.len() } else { 0 };
        if k < index + vix {
            return self.vix_futures_book_at(k - index);
        }
        let rates = if self.rate_futures_on() { self.rate_futures.contracts.len() } else { 0 };
        if k < index + vix + rates {
            return self.rate_futures_book_at(k - index - vix);
        }
        self.oil_futures_book_at(k - index - vix - rates)
    }

    fn futures_push_fill(&mut self, mut fill: AgentFill) -> AgentFill {
        fill.sequence = self.futures.book.fill_sequence;
        self.futures.book.fill_sequence += 1;
        self.futures.book.fills.push(fill.clone());
        fill
    }

    fn futures_reduce_order(&mut self, order_id: &str, quantity: f64) {
        if let Some(o) = self.futures.book.orders.iter_mut().find(|o| o.id == order_id) {
            o.remaining -= quantity;
        }
        self.futures.book.orders.retain(|o| o.remaining > 1e-9);
    }

    /// An order meets the contract's book. Records what it took from the
    /// house, the other agents' resting orders it hit, a taker fill per
    /// level and its net flow against the house for the next step. Returns
    /// the taker fills and the mid the order met.
    #[allow(clippy::too_many_arguments)]
    fn futures_meet(
        &mut self,
        slot: usize,
        agent: &str,
        order_id: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
    ) -> (Vec<AgentFill>, f64) {
        let mut book = self.futures_book(slot, Some(agent));
        let symbol = book.company_id.clone();
        let reference = book.mid_price().unwrap_or(self.futures_valuation(slot).price);
        let r = book.submit(
            side,
            quantity,
            agent,
            SubmitOptions { limit_price: limit, post_remainder: false, order_id: None, skip_own: true, house_ids: false },
        );
        let (day, tick) = (self.current_day, self.ticks_today());
        let mut taker = Vec::with_capacity(r.fills.len());
        for f in &r.fills {
            let owner: &str = &f.maker_id;
            let house = owner == MARKET_MAKER_ID || owner == DEPTH_OWNER;
            if house {
                let k = match (side, owner == MARKET_MAKER_ID) {
                    (Side::Buy, true) => TAKEN_MAKER_ASK,
                    (Side::Sell, true) => TAKEN_MAKER_BID,
                    (Side::Buy, false) => TAKEN_DEPTH_ASK,
                    (Side::Sell, false) => TAKEN_DEPTH_BID,
                };
                let c = &mut self.futures.contracts[slot];
                c.taken[k] += f.quantity;
                c.pending += match side {
                    Side::Buy => f.quantity,
                    Side::Sell => -f.quantity,
                };
            } else {
                self.futures_reduce_order(&f.maker_order_id, f.quantity);
                self.futures_push_fill(AgentFill {
                    agent: owner.to_string(),
                    order_id: f.maker_order_id.clone(),
                    ticker: symbol.clone(),
                    side: match side {
                        Side::Buy => Side::Sell,
                        Side::Sell => Side::Buy,
                    },
                    quantity: f.quantity,
                    price: f.price,
                    liquidity: Liquidity::Maker,
                    counterparty: agent.to_string(),
                    reference,
                    day,
                    tick,
                    sequence: 0,
                });
            }
            taker.push(self.futures_push_fill(AgentFill {
                agent: agent.to_string(),
                order_id: order_id.to_string(),
                ticker: symbol.clone(),
                side,
                quantity: f.quantity,
                price: f.price,
                liquidity: Liquidity::Taker,
                counterparty: owner.to_string(),
                reference,
                day,
                tick,
                sequence: 0,
            }));
        }
        (taker, reference)
    }

    /// Match every resting order the contracts' books now cross, at the
    /// books' prices, in arrival order: the futures price moved through it.
    fn futures_cross_resting(&mut self) {
        if self.futures.book.orders.is_empty() {
            return;
        }
        let ids: Vec<String> = self.futures.book.orders.iter().map(|o| o.id.clone()).collect();
        for id in ids {
            let Some(o) = self.futures.book.orders.iter().find(|o| o.id == id).cloned() else {
                continue;
            };
            let Some(slot) = self.futures_slot(&o.ticker) else {
                continue;
            };
            let book = self.futures_book(slot, Some(&o.agent));
            let crossed = match o.side {
                Side::Buy => book.best_ask().is_some_and(|a| o.limit >= a),
                Side::Sell => book.best_bid().is_some_and(|b| o.limit <= b),
            };
            if !crossed {
                continue;
            }
            let (fills, _) = self.futures_meet(slot, &o.agent, &o.id, o.side, o.remaining, Some(o.limit));
            let filled: f64 = fills.iter().map(|f| f.quantity).sum();
            self.futures_reduce_order(&o.id, filled);
        }
    }

    /// An agent's order on a listed contract (`Engine::submit_order` with a
    /// contract symbol): a market order takes what the book holds, a limit
    /// takes what it holds at the limit or better and rests the rest in the
    /// contract's queue. Its net flow against the house moves the
    /// contract's basis from the next step.
    pub(super) fn submit_contract_order(
        &mut self,
        agent: &str,
        symbol: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
        order_id: Option<String>,
    ) -> Result<OrderReport, String> {
        let slot = self.futures_slot(symbol).ok_or_else(|| {
            format!("{symbol} is not a listed contract: Engine.contracts() lists the ones that trade")
        })?;
        let id = match order_id {
            Some(id) => {
                if id.is_empty() {
                    return Err("order_id cannot be empty".to_string());
                }
                id
            }
            None => format!("{agent}-{symbol}-{}", self.futures.book.sequence),
        };
        if self.contract_order_id_taken(&id) {
            return Err(format!("order id {id:?} is already waiting in the book"));
        }
        let sequence = self.futures.book.sequence;
        self.futures.book.sequence += 1;
        self.futures_cross_resting();
        let (fills, reference) = self.futures_meet(slot, agent, &id, side, quantity, limit);
        let mut filled = 0.0;
        let mut notional = 0.0;
        let mut worst: Option<f64> = None;
        for f in &fills {
            filled += f.quantity;
            notional += f.price * f.quantity;
            worst = Some(f.price);
        }
        let remainder = quantity - filled;
        let (resting, mode) = match limit {
            Some(p) if remainder > 1e-9 => {
                self.futures.book.orders.push(AgentOrder {
                    id: id.clone(),
                    agent: agent.to_string(),
                    ticker: symbol.to_string(),
                    side,
                    limit: p,
                    quantity,
                    remaining: remainder,
                    sequence,
                    mode: RestMode::Queue,
                });
                (remainder, Some(RestMode::Queue))
            }
            _ => (0.0, None),
        };
        Ok(OrderReport {
            order_id: id,
            agent: agent.to_string(),
            ticker: symbol.to_string(),
            side,
            requested: quantity,
            filled,
            average_price: if filled > 0.0 { Some(notional / filled) } else { None },
            worst_price: worst,
            reference,
            resting,
            unfilled: if mode.is_some() { 0.0 } else { remainder },
            mode,
            fills,
        })
    }

    /// Cancel a resting order on a contract. Returns whether one was removed.
    pub(super) fn cancel_contract_order(&mut self, order_id: &str, agent: Option<&str>) -> bool {
        let before = self.futures.book.orders.len();
        self.futures
            .book
            .orders
            .retain(|o| !(o.id == order_id && agent.is_none_or(|a| a == o.agent)));
        self.futures.book.orders.len() != before
    }

    // ── Steps ─────────────────────────────────────────────────────────────

    /// One step of the futures: the flow since the last step reaches the
    /// basis and the old flow's mark decays, the basis noise steps (one
    /// normal, always), the maker re-quotes whole and the latent depth
    /// refills, the night's path steps under `night`, and resting orders
    /// the new books cross fill.
    fn futures_step(&mut self, night: bool) {
        let spec = &INDEX_FUTURE;
        let steps_a_day = SESSION_TICKS + f64::from(self.night_steps());
        let rho = self.params.basis_persistence;
        let a = if rho > 0.0 { crate::mathx::pow(rho, 1.0 / steps_a_day) } else { 0.0 };
        let sd = self.params.basis_sd * crate::mathx::sqrt(crate::mathx::max(0.0, 1.0 - a * a));
        self.futures.rng.site(Site::FuturesBasisZ, 0);
        let z = self.futures.rng.next_normal();
        self.futures.basis = a * self.futures.basis + sd * z;
        if !self.futures.contracts.is_empty() {
            // The flow's scale, read only when some contract has flow to
            // apply: an untraded step reads nothing but its own state.
            let scale = if self.futures.contracts.iter().any(|c| c.pending != 0.0) {
                let index = match (self.futures.phase, self.futures.night.as_ref()) {
                    (FuturesPhase::Night, Some(b)) => b.now[0],
                    (FuturesPhase::Night, None) if self.index_close > 0.0 => self.index_close,
                    _ => self.futures_live_index(),
                };
                let volume = self.futures_daily_volume(index);
                spec.impact_coefficient * 1e4 * self.market_vol.sigma_daily() / volume
            } else {
                0.0
            };
            let decay = crate::mathx::pow(0.5, 1.0 / spec.impact_half_life);
            let refill = crate::mathx::pow(0.5, 1.0 / spec.refill_half_life);
            for c in self.futures.contracts.iter_mut() {
                c.impact = c.impact * decay + scale * c.pending;
                if c.impact.abs() < 1e-12 {
                    c.impact = 0.0;
                }
                c.pending = 0.0;
                c.taken[TAKEN_MAKER_BID] = 0.0;
                c.taken[TAKEN_MAKER_ASK] = 0.0;
                for k in [TAKEN_DEPTH_BID, TAKEN_DEPTH_ASK] {
                    c.taken[k] = if c.taken[k] * refill < 1e-6 { 0.0 } else { c.taken[k] * refill };
                }
            }
        }
        if night {
            self.futures.rng.site(Site::NightBridgeZ, 0);
            let z = self.futures.rng.next_normal();
            if let Some(b) = self.futures.night.as_mut() {
                let left = b.steps - b.done;
                if left <= 1 {
                    b.now.clone_from(&b.target);
                } else {
                    let left = f64::from(left);
                    let v = b.variance / f64::from(b.steps);
                    let sd = crate::mathx::sqrt(crate::mathx::max(0.0, v * (left - 1.0) / left));
                    for (now, target) in b.now.iter_mut().zip(b.target.iter()) {
                        let x = crate::mathx::log(*now);
                        let x = x + (crate::mathx::log(*target) - x) / left + sd * z;
                        *now = crate::mathx::exp(x);
                    }
                }
                b.done += 1;
            }
        }
        self.futures_cross_resting();
    }

    /// A tick's step, on every open tick. Nothing with the switch off.
    pub(super) fn futures_session_step(&mut self) {
        if !self.futures_on() {
            return;
        }
        self.futures_step(false);
    }

    /// Walk up to `steps` steps of the night (`night_session_steps`): each
    /// moves the futures' fair values along the night's path to the next
    /// open, steps the basis and fills resting orders the books cross. Only
    /// between a close and the next open, and never past the night's last
    /// step, which lands every future on the fair value the open will give
    /// it. Returns the steps walked: 0 with the switch off, in a session,
    /// or once the night is done. Stocks do not trade.
    pub fn night_tick(&mut self, steps: u32) -> u32 {
        if self.night_steps() == 0 || self.futures.phase != FuturesPhase::Night {
            return 0;
        }
        let mut walked = 0;
        while walked < steps {
            let Some(b) = self.futures.night.as_ref() else {
                break;
            };
            if b.done >= b.steps {
                break;
            }
            self.futures_step(true);
            walked += 1;
        }
        walked
    }

    // ── The day's boundaries ──────────────────────────────────────────────

    /// At an open, after the opening prints: the contract expiring at this
    /// session settles on the index of those prints, the next is listed,
    /// the night ends, the books open whole and resting orders the opening
    /// books cross fill at their prices. Nothing with the switch off.
    pub(super) fn futures_open(&mut self) {
        if !self.futures_on() {
            return;
        }
        self.futures.phase = FuturesPhase::Session;
        self.futures.night = None;
        let t = self.elapsed_days;
        let index = self.futures_live_index();
        let mut kept = Vec::with_capacity(self.futures.contracts.len());
        for c in std::mem::take(&mut self.futures.contracts) {
            if c.expiry <= t {
                self.futures.settlements.push(settlement_of(c.expiry, index));
                let symbol = index_future_symbol(c.expiry);
                self.futures.book.orders.retain(|o| o.ticker != symbol);
            } else {
                kept.push(c);
            }
        }
        for expiry in calendar::index_future_expiries(t, calendar::INDEX_FUTURES_LISTED) {
            if !kept.iter().any(|c| c.expiry == expiry) {
                kept.push(ListedFuture::new(expiry));
            }
        }
        kept.sort_by_key(|c| c.expiry);
        kept.truncate(calendar::INDEX_FUTURES_LISTED);
        for c in kept.iter_mut() {
            c.taken = [0.0; 4];
        }
        self.futures.contracts = kept;
        self.futures_cross_resting();
    }

    /// At a close, on the session's last prints: each contract's settlement
    /// mark, its price on the close's index. Nothing with the switch off.
    pub(super) fn futures_close_marks(&mut self) {
        if !self.futures_on() {
            return;
        }
        self.futures.night = None;
        self.futures.phase = FuturesPhase::Night;
        let index = if self.index_close > 0.0 { self.index_close } else { self.futures_live_index() };
        let now = self.futures_now();
        for slot in 0..self.futures.contracts.len() {
            let expiry = self.futures.contracts[slot].expiry;
            let (fair, ..) = self.futures_carry(expiry, index, now);
            let c = &mut self.futures.contracts[slot];
            let sessions = crate::mathx::max(0.0, expiry as f64 - now);
            let basis_bp = convergence(sessions) * (self.futures.basis + c.impact);
            c.fair_close = fair;
            c.mark = fair + basis_bp * index / 1e4;
        }
    }

    /// The next open on a copy of the engine: the index and each listed
    /// contract's fair value on its opening prints, in listing order. The
    /// copy takes the draws the open will take; the engine is left as it
    /// was.
    fn futures_preview(&mut self) -> (f64, Vec<f64>) {
        let t = self.elapsed_days;
        // Out of the copy, and back: what grows with the run and an open
        // neither reads nor needs to price.
        let marks = std::mem::take(&mut self.day_marks);
        let distributions = std::mem::take(&mut self.distribution_log);
        let population = self.population.take();
        let fills = std::mem::take(&mut self.futures.book.fills);
        let orders = std::mem::take(&mut self.futures.book.orders);
        let mut copy = self.clone();
        self.day_marks = marks;
        self.distribution_log = distributions;
        self.population = population;
        self.futures.book.fills = fills;
        self.futures.book.orders = orders;
        // The live marks move no price, so the copy need not project them.
        copy.params.vix_intraday_live = 0.0;
        copy.params.rate_intraday_live = 0.0;
        copy.set_day_label(self.current_day + 1);
        copy.set_elapsed_days(t + 1);
        copy.open_market();
        let index = copy.futures_live_index();
        let now = copy.futures_now();
        let fairs = self
            .futures
            .contracts
            .iter()
            .map(|c| if c.expiry <= t + 1 { index } else { copy.futures_carry(c.expiry, index, now).0 })
            .collect();
        (index, fairs)
    }

    /// After the close's macro step (`night_session_steps`): the night's
    /// targets from the next open on a copy, and its path from the close.
    /// Nothing without a night session.
    pub(super) fn futures_night_setup(&mut self) {
        let steps = self.night_steps();
        if steps == 0 || self.futures.phase != FuturesPhase::Night || self.futures.contracts.is_empty() {
            return;
        }
        let (index_open, fairs) = self.futures_preview();
        let index_close = if self.index_close > 0.0 { self.index_close } else { self.futures_live_index() };
        let mut now = vec![index_close];
        for c in &self.futures.contracts {
            now.push(c.fair_close);
        }
        let mut target = vec![index_open];
        target.extend(fairs);
        if now.iter().chain(target.iter()).any(|v| !(v.is_finite() && *v > 0.0)) {
            return;
        }
        let p = &self.params;
        let share = if p.overnight_market_share != 0.0 || p.overnight_idio_share != 0.0 {
            p.overnight_market_share
        } else if p.overnight_variance_ratio > 0.0 {
            p.overnight_variance_ratio / (1.0 + p.overnight_variance_ratio)
        } else {
            0.0
        };
        let variance = crate::mathx::max(0.0, share * self.index_conditional_variance_terms_now().total());
        self.futures.night = Some(NightBridge {
            steps,
            done: 0,
            variance: if variance.is_finite() { variance } else { 0.0 },
            expiries: self.futures.contracts.iter().map(|c| c.expiry).collect(),
            now,
            target,
        });
    }

    /// Rerun the next open on a copy and move the night's path by the
    /// change in its targets, at once: a pin or another change between
    /// sessions reaches the futures when it is made. Nothing outside a
    /// night.
    pub(crate) fn futures_retarget(&mut self) {
        if self.night_steps() == 0 || self.futures.phase != FuturesPhase::Night || self.futures.night.is_none() {
            return;
        }
        let (index_open, fairs) = self.futures_preview();
        let mut target = vec![index_open];
        target.extend(fairs);
        let Some(b) = self.futures.night.as_mut() else {
            return;
        };
        if target.len() != b.target.len() || target.iter().any(|v| !(v.is_finite() && *v > 0.0)) {
            return;
        }
        if target.iter().zip(b.target.iter()).all(|(a, b)| a.to_bits() == b.to_bits()) {
            return;
        }
        let done = b.done >= b.steps;
        for ((now, old), new) in b.now.iter_mut().zip(b.target.iter()).zip(target.iter()) {
            *now = if done { *new } else { *now * (new / old) };
        }
        b.target = target;
    }

    // ── Reads ─────────────────────────────────────────────────────────────

    /// The listed contracts: the front two index futures under
    /// `futures_index_listed`, then the six VIX futures under
    /// `futures_vix_listed`, then the thirteen policy-rate and eight
    /// term-rate futures under `futures_rates_listed`, then the twelve oil
    /// futures under `futures_oil_listed`, each family in expiry order; none
    /// with all off.
    pub fn contracts(&self) -> Vec<ContractSpec> {
        let mut out = self.index_futures_contracts();
        out.extend(self.vix_futures_contracts());
        out.extend(self.rate_futures_contracts());
        out.extend(self.oil_futures_contracts());
        out
    }

    fn index_futures_contracts(&self) -> Vec<ContractSpec> {
        if !self.futures_on() {
            return Vec::new();
        }
        let session = match self.futures.phase {
            FuturesPhase::Night => self.elapsed_days + 1,
            _ => self.elapsed_days,
        };
        let front = self.futures.contracts.iter().position(|c| calendar::roll_session(c.expiry) > session);
        self.futures
            .contracts
            .iter()
            .enumerate()
            .map(|(k, c)| {
                let kind = ContractKind::IndexFuture;
                ContractSpec {
                    symbol: index_future_symbol(c.expiry),
                    kind,
                    root: kind.root().to_string(),
                    expiry: c.expiry,
                    roll: calendar::roll_session(c.expiry),
                    multiplier: INDEX_FUTURE.multiplier,
                    tick: INDEX_FUTURE.tick,
                    settlement: kind.settlement(),
                    front: front == Some(k),
                }
            })
            .collect()
    }

    /// A listed contract's quote now: `None` for a symbol not listed, and
    /// for every symbol with its family's switch off. Its `initial_margin` is
    /// the one the last close set (`margin_scan_coverage`).
    pub fn quote(&self, symbol: &str) -> Option<Quote> {
        let mut q = self.quote_unmargined(symbol)?;
        q.initial_margin = self.initial_margin(q.kind, q.expiry, q.multiplier);
        q.maintenance_margin = q.initial_margin.map(|m| m / super::margin::MAINTENANCE_RATIO);
        Some(q)
    }

    fn quote_unmargined(&self, symbol: &str) -> Option<Quote> {
        if self.vix_futures_on() {
            if let Some(slot) = self.vix_futures_slot(symbol) {
                return self.vix_futures_quote(slot);
            }
        }
        if self.rate_futures_on() {
            if let Some(slot) = self.rate_futures_slot(symbol) {
                return self.rate_futures_quote(slot);
            }
        }
        if self.oil_futures_on() {
            if let Some(slot) = self.oil_futures_slot(symbol) {
                return self.oil_futures_quote(slot);
            }
        }
        if !self.futures_on() {
            return None;
        }
        let slot = self.futures_slot(symbol)?;
        let c = &self.futures.contracts[slot];
        let v = self.futures_valuation(slot);
        let book = self.futures_book(slot, None);
        let (bid, ask) = (book.best_bid(), book.best_ask());
        Some(Quote {
            symbol: index_future_symbol(c.expiry),
            kind: ContractKind::IndexFuture,
            expiry: c.expiry,
            bid,
            ask,
            mid: match (bid, ask) {
                (Some(b), Some(a)) => Some(0.5 * (a + b)),
                _ => None,
            },
            price: v.price,
            fair: v.fair,
            mark: if c.mark.is_finite() { Some(c.mark) } else { None },
            basis_bp: v.basis_bp,
            index: v.index,
            rate: v.rate,
            dividends: v.dividends,
            sessions_to_expiry: v.sessions,
            multiplier: INDEX_FUTURE.multiplier,
            tick: INDEX_FUTURE.tick,
            daily_volume: self.futures_daily_volume(v.index),
            initial_margin: None,
            maintenance_margin: None,
            expected: None,
            premium: None,
            loading: None,
        })
    }

    /// The final settlements made at `session`'s open, or every one so far
    /// with `None`, in session order, an index future before a VIX future at
    /// the same open. Empty with both switches off.
    pub fn settlements(&self, session: Option<i64>) -> Vec<Settlement> {
        let mut out: Vec<Settlement> = if self.futures_on() {
            self.futures
                .settlements
                .iter()
                .filter(|s| session.is_none_or(|d| s.session == d))
                .cloned()
                .collect()
        } else {
            Vec::new()
        };
        out.extend(self.vix_futures_settlements(session));
        out.extend(self.rate_futures_settlements(session));
        out.extend(self.oil_futures_settlements(session));
        // Stable, so each family keeps its own order within a session.
        out.sort_by_key(|s| s.session);
        out
    }

    // ── Snapshot ──────────────────────────────────────────────────────────

    /// The futures' numbers for the snapshot and the hash: `None` with the
    /// switch off.
    pub fn futures_words(&self) -> Option<Vec<f64>> {
        if self.futures_on() {
            Some(self.futures.to_words())
        } else {
            None
        }
    }

    /// The futures' generator, `stream::DERIVATIVES`: `None` with the
    /// switch off.
    pub fn derivatives_rng_state(&self) -> Option<RngState> {
        if self.futures_on() {
            Some(self.futures.rng.snapshot())
        } else {
            None
        }
    }

    /// Agents' resting orders on contracts and their undelivered fills:
    /// `None` with the switch off and while no agent has traded one.
    pub fn futures_book_state(&self) -> Option<&BookState> {
        if self.futures_on() && !self.futures.book.is_pristine() {
            Some(&self.futures.book)
        } else {
            None
        }
    }

    /// The night's path, while one is walked: `None` otherwise.
    pub fn night_bridge_words(&self) -> Option<Vec<f64>> {
        if self.night_steps() == 0 {
            return None;
        }
        self.futures.night.as_ref().map(NightBridge::to_words)
    }

    /// Put the futures back (a restore): their numbers, their generator,
    /// their book and the night, each refused where the switch it belongs
    /// to is off.
    pub fn set_futures_state(
        &mut self,
        words: Option<&[f64]>,
        rng: Option<RngState>,
        book: Option<BookState>,
        night: Option<&[f64]>,
    ) -> Result<(), String> {
        let off = |key: &str, dial: &str| {
            format!(
                "this snapshot carries {key}, which only an engine with {dial} on keeps, and \
                 this engine's model has it off"
            )
        };
        if !self.futures_on() {
            if words.is_some() {
                return Err(off("the index futures (futures)", "futures_index_listed"));
            }
            if rng.is_some() {
                return Err(off("the derivatives' generator (derivatives_rng)", "futures_index_listed"));
            }
            if book.is_some() {
                return Err(off("the futures' book (futures_book)", "futures_index_listed"));
            }
            if night.is_some() {
                return Err(off("the night's path (night_bridge)", "night_session_steps"));
            }
            self.futures = FuturesState::new(self.root_seed);
            return Ok(());
        }
        let (Some(words), Some(rng)) = (words, rng) else {
            return Err("an engine with futures_index_listed on carries its futures and their \
                        generator (futures, derivatives_rng); this snapshot lacks one"
                .to_string());
        };
        if night.is_some() && self.night_steps() == 0 {
            return Err(off("the night's path (night_bridge)", "night_session_steps"));
        }
        let mut state = FuturesState::new(self.root_seed);
        state.set_words(words)?;
        state.rng = GameRng::restore(rng);
        state.book = book.unwrap_or_default();
        state.night = match night {
            Some(w) => Some(NightBridge::from_words(w)?),
            None => None,
        };
        self.futures = state;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::economy::{create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions};

    fn roster() -> Vec<TickCompany> {
        crate::universe::random_universe(12, 5)
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect()
    }

    fn with(dials: &[(&str, f64)]) -> ModelParams {
        let mut p = crate::params::PT_V21;
        for (name, value) in dials {
            p = p.with_override(name, *value).unwrap();
        }
        p
    }

    const ON: &[(&str, f64)] = &[
        ("index_level_listed", 1.0),
        ("forecast_horizon_sessions", 252.0),
        ("futures_index_listed", 1.0),
        ("basis_sd", 3.7),
        ("basis_persistence", 0.42),
        ("night_session_steps", 8.0),
    ];

    fn engine(seed: u64, dials: &[(&str, f64)]) -> Engine {
        Engine::with_params(
            seed,
            roster(),
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
            with(dials),
        )
    }

    fn day(e: &mut Engine, d: i64, buffer: &mut SessionBuffer) {
        e.run_numbered_day(d, 390, buffer);
    }

    #[test]
    fn off_nothing_is_listed_and_nothing_is_kept() {
        let mut e = engine(3, &[]);
        let mut buffer = SessionBuffer::new();
        day(&mut e, 0, &mut buffer);
        assert!(e.contracts().is_empty());
        assert!(e.quote("IDX.F0056").is_none());
        assert!(e.settlements(None).is_empty());
        assert_eq!(e.night_tick(5), 0);
        assert!(e.futures_words().is_none());
        assert!(e.derivatives_rng_state().is_none());
        assert!(e.night_bridge_words().is_none());
    }

    #[test]
    fn the_front_two_are_listed_and_roll_at_expiry_onto_the_opening_prints() {
        let mut e = engine(5, ON);
        let mut buffer = SessionBuffer::new();
        let listed: Vec<String> = e.contracts().into_iter().map(|c| c.symbol).collect();
        assert_eq!(listed, vec!["IDX.F0056", "IDX.F0119"]);
        for d in 0..56 {
            day(&mut e, d, &mut buffer);
            e.night_tick(8);
        }
        // The night before expiry lands the expiring contract on the open.
        let last_night = e.quote("IDX.F0056").unwrap();
        e.set_current_day(56);
        e.open_market();
        let opening = e.index_level().unwrap().level;
        let s = e.settlements(Some(56));
        assert_eq!(s.len(), 1);
        assert_eq!(s[0].symbol, "IDX.F0056");
        assert_eq!(s[0].value.to_bits(), opening.to_bits());
        assert_eq!(s[0].reference.to_bits(), opening.to_bits());
        // IF3: the future at expiry less the opening-print index.
        assert!((last_night.price - opening).abs() / opening <= 1e-12, "{} {opening}", last_night.price);
        let listed: Vec<String> = e.contracts().into_iter().map(|c| c.symbol).collect();
        assert_eq!(listed, vec!["IDX.F0119", "IDX.F0182"]);
        assert!(e.quote("IDX.F0056").is_none());
    }

    #[test]
    fn a_whole_night_lands_every_future_on_its_opening_fair_value() {
        let mut e = engine(7, ON);
        let mut buffer = SessionBuffer::new();
        for d in 0..30 {
            day(&mut e, d, &mut buffer);
            if d + 1 < 30 {
                e.night_tick(8);
            }
        }
        let close = e.quote("IDX.F0056").unwrap();
        // NS2 by construction: after the night the quote's fair is the fair
        // value on the opening index, to the bit, and the price is that plus
        // the basis.
        assert_eq!(e.night_tick(3), 3);
        let partway = e.quote("IDX.F0056").unwrap();
        assert_ne!(partway.fair.to_bits(), close.fair.to_bits(), "the night moved nothing");
        assert_eq!(e.night_tick(100), 5, "the night has eight steps");
        assert_eq!(e.night_tick(1), 0);
        let night = e.quote("IDX.F0056").unwrap();
        e.set_current_day(30);
        e.open_market();
        let open = e.quote("IDX.F0056").unwrap();
        assert_eq!(night.fair.to_bits(), open.fair.to_bits());
        assert_eq!(night.index.to_bits(), open.index.to_bits());
        assert!((night.price - open.price).abs() <= 1e-12 * open.price);
        assert_eq!(open.mark.unwrap().to_bits(), close.mark.unwrap().to_bits());
    }

    #[test]
    fn the_carry_reads_declared_dividends_inside_21_sessions_and_projects_beyond() {
        use crate::market::dividends as dv;
        let mut e = engine(11, ON);
        let mut buffer = SessionBuffer::new();
        for d in 0..3 {
            day(&mut e, d, &mut buffer);
        }
        e.set_current_day(3);
        e.open_market();
        let t = e.elapsed_days();
        let rate = 0.03;
        let now = e.futures_now();
        // By hand: each payer's ex-dates after the open, declared amount
        // inside 21 sessions, the rule's projection beyond.
        let by_hand = |expiry: i64| -> (f64, usize, usize) {
            let mut total = 0.0;
            let (mut declared, mut projected) = (0, 0);
            for c in e.companies() {
                let Some(d) = c.stock.dividend else { continue };
                if !(d.target_yield > 0.0) || !c.is_public || c.is_bankrupt {
                    continue;
                }
                let k = dv::sessions_since_ex(&c.ticker, t);
                let mut ex = t + 63 - k;
                let mut state = d;
                let mut amount = if d.declared {
                    assert!(ex - t <= 21, "declared more than 21 sessions ahead");
                    declared += 1;
                    d.amount
                } else {
                    assert!(ex - t > 21, "undeclared inside 21 sessions");
                    projected += 1;
                    dv::declare(e.params(), &d, c.stock.price)
                };
                while ex <= expiry {
                    total += c.stock.shares_outstanding * amount / e.index_divisor
                        * (-rate * (ex as f64 - now) / 252.0).exp();
                    state.amount = amount;
                    amount = dv::declare(e.params(), &state, c.stock.price);
                    ex += 63;
                }
            }
            (total, declared, projected)
        };
        for expiry in [56, 119, 182] {
            let (want, declared, projected) = by_hand(expiry);
            let got = e.futures_dividends(expiry, rate, now);
            assert!(want > 0.0);
            assert!((got - want).abs() <= 1e-12 * want, "{expiry}: {got} {want}");
            assert!(declared > 0 && projected > 0, "{declared} {projected}");
        }
        // Further out holds more, and an expiry before any ex-date none.
        assert!(e.futures_dividends(182, rate, now) > e.futures_dividends(56, rate, now));
        assert_eq!(e.futures_dividends(t, rate, now), 0.0);
    }

    #[test]
    fn the_words_round_trip_and_are_refused_where_the_switch_is_off() {
        let mut e = engine(13, ON);
        let mut buffer = SessionBuffer::new();
        for d in 0..4 {
            day(&mut e, d, &mut buffer);
        }
        e.night_tick(3);
        e.submit_order("a", "IDX.F0056", Side::Buy, 25.0, None, None).unwrap();
        e.submit_order("a", "IDX.F0119", Side::Sell, 5.0, Some(1e6), None).unwrap();
        let words = e.futures_words().unwrap();
        let rng = e.derivatives_rng_state().unwrap();
        let night = e.night_bridge_words().unwrap();
        let book = e.futures_book_state().cloned();
        assert!(book.is_some());
        let mut other = engine(99, ON);
        other.set_futures_state(Some(&words), Some(rng), book.clone(), Some(&night)).unwrap();
        assert_eq!(other.futures_words().unwrap(), words);
        assert_eq!(other.night_bridge_words().unwrap(), night);
        assert_eq!(other.derivatives_rng_state().unwrap(), rng);
        let mut off = engine(13, &[]);
        assert!(off.set_futures_state(Some(&words), Some(rng), None, None).is_err());
        assert!(NightBridge::from_words(&night[..night.len() - 1]).is_err());
        let mut short = FuturesState::new(1);
        assert!(short.set_words(&words[..words.len() - 1]).is_err());
    }

    #[test]
    fn the_basis_reads_its_persistence_and_sd_at_the_close() {
        // One close-to-close AR(1): a = rho^(1/K) over K steps a day.
        let mut e = engine(17, &[
            ("index_level_listed", 1.0),
            ("futures_index_listed", 1.0),
            ("basis_sd", 4.0),
            ("basis_persistence", 0.5),
        ]);
        e.futures.phase = FuturesPhase::Session;
        let mut closes = Vec::new();
        for _ in 0..6000 {
            for _ in 0..390 {
                e.futures_step(false);
            }
            closes.push(e.futures.basis);
        }
        let n = closes.len() as f64;
        let mean = closes.iter().sum::<f64>() / n;
        let var = closes.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>() / n;
        let lag = closes.windows(2).map(|w| (w[0] - mean) * (w[1] - mean)).sum::<f64>() / (n - 1.0) / var;
        assert!((var.sqrt() - 4.0).abs() < 0.15, "sd {}", var.sqrt());
        assert!((lag - 0.5).abs() < 0.04, "lag-1 {lag}");
    }
}
