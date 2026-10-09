//! The VIX futures (`futures_vix_listed`): the engine's half.
//!
//! A VIX future reads the published VIX, the live VIX and the forecast, and
//! writes only its own state ([`VixFuturesState`]): its marks, its flow's
//! mark, its book and its settlements. It draws nothing. So every stock
//! price, close, economy and VIX is the one the engine prints with the
//! switch off.
//!
//! # The calendar
//!
//! Monthly, expiring at session 15 of each 21-session month, `21m + 14`
//! (`derivatives::calendar::monthly_expiry`), the next six listed from the
//! first close (before it there is no forecast to price them on). A contract
//! settles at its expiry session's open, in cash, on the published VIX
//! then: the previous close's value, since the VIX moves only at a close.
//!
//! # The price
//!
//! At a close, a contract `n` sessions from expiry (`n = expiry - the
//! close's session`) is worth `E_n + premium(n, P)`: `P` the published VIX
//! the close leaves, `E_n` the forecast's expected published VIX after
//! `n - 1` closes (`P` itself at `n = 1`, when the contract settles at the
//! next open on it), and the premium [`crate::derivatives::VIX_FUTURE`]
//! freezes. So the close marks are the curve the VIX-law screen priced and
//! read.
//!
//! Within the next session the contract is `m = n - 1` sessions from
//! expiry, counted from tonight's close, and moves with what the session
//! says about tonight's VIX: by `lambda(m)` times the live VIX's surprise,
//! the live VIX (`vix_intraday_live`) less the forecast's expectation of
//! tonight's published VIX, and its premium rolls from `premium(n, P)` to
//! `premium(m, V)`, `V` the live VIX, over the session's ticks:
//!
//! `F = E_n + lambda(m) (V - E_1) + (1 - k) premium(n, P) + k premium(m, V)`
//!
//! with `k` the share of the session's 390 ticks run. `lambda(1)` is 1, so
//! the contract that settles on tonight's VIX ends the session on the live
//! VIX, which the close then publishes. `lambda` is the projection of a
//! close's revision of the expected settlement on the close's surprise,
//! fitted on held-out histories (`futures_vix_live_fast_share` and its two
//! half-lives); without the live VIX the surprise is 0. Between sessions a
//! contract is worth its close's value, read on the standing forecast and
//! published VIX, so a pin between sessions reaches it at once.
//!
//! Agents' net taker flow against the house marks the price, `F (1 + i)`,
//! and the mark decays, as an index future's basis does.

use super::*;
use crate::agent_book::{remove_front, AgentFill, AgentOrder, BookState, Liquidity, RestMode, AGENT_BOOK_CAP, DEPTH_OWNER};
use crate::derivatives::calendar::{self, ROLL_SESSIONS};
use crate::derivatives::{
    vix_future_symbol, ContractKind, ContractSpec, ContractSymbol, Quote, Settlement, SymbolKind, VixFutureSpec,
    VIX_FUTURE,
};
use crate::market_maker::MARKET_MAKER_ID;
use crate::order_book::{OrderBook, Side, SubmitOptions};

/// The ticks in a session, the day the premium's roll divides.
const SESSION_TICKS: f64 = 390.0;

/// Indices into [`ListedVixFuture::taken`], as an index future's.
const TAKEN_MAKER_BID: usize = 0;
const TAKEN_MAKER_ASK: usize = 1;
const TAKEN_DEPTH_BID: usize = 2;
const TAKEN_DEPTH_ASK: usize = 3;

/// The latent grid's floor and level ratio: the equity book's.
const DEPTH_GRID_FLOOR: f64 = 1e-4;
const DEPTH_LEVEL_RATIO: f64 = 1.25;

/// Where the VIX futures stand in the day.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum VixPhase {
    /// Before the first close: nothing listed.
    BeforeFirstClose,
    Session,
    Night,
}

impl VixPhase {
    fn code(self) -> f64 {
        match self {
            VixPhase::BeforeFirstClose => 0.0,
            VixPhase::Session => 1.0,
            VixPhase::Night => 2.0,
        }
    }

    fn from_code(v: f64) -> Option<Self> {
        if v == 0.0 {
            Some(VixPhase::BeforeFirstClose)
        } else if v == 1.0 {
            Some(VixPhase::Session)
        } else if v == 2.0 {
            Some(VixPhase::Night)
        } else {
            None
        }
    }
}

/// One listed VIX future.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ListedVixFuture {
    pub(crate) expiry: i64,
    /// The last close's settlement mark. NaN before the contract's first
    /// close.
    pub(crate) mark: f64,
    /// Agents' flow's mark on the price, a share of it.
    pub(crate) impact: f64,
    /// Agents' net taker contracts against the house since the last step.
    pub(crate) pending: f64,
    pub(crate) taken: [f64; 4],
}

impl ListedVixFuture {
    fn new(expiry: i64) -> Self {
        ListedVixFuture { expiry, mark: f64::NAN, impact: 0.0, pending: 0.0, taken: [0.0; 4] }
    }
}

/// Numbers per listed contract in [`VixFuturesState::to_words`].
const CONTRACT_WIDTH: usize = 8;

/// Everything the VIX futures carry.
#[derive(Debug, Clone)]
pub(crate) struct VixFuturesState {
    pub(crate) phase: VixPhase,
    /// The listed contracts, in expiry order.
    pub(crate) contracts: Vec<ListedVixFuture>,
    /// Every final settlement so far, in order.
    pub(crate) settlements: Vec<Settlement>,
    /// Agents' resting orders on VIX futures and their undelivered fills.
    pub(crate) book: BookState,
}

impl Default for VixFuturesState {
    fn default() -> Self {
        VixFuturesState {
            phase: VixPhase::BeforeFirstClose,
            contracts: Vec::new(),
            settlements: Vec::new(),
            book: BookState::default(),
        }
    }
}

impl VixFuturesState {
    /// The numbers the snapshot's `vix_futures` key and the state hash
    /// carry: the phase, the contract count and each contract's expiry,
    /// mark, impact, pending flow and four taken counts, then the settlement
    /// count and each settlement's session and value.
    pub(crate) fn to_words(&self) -> Vec<f64> {
        let mut out = Vec::with_capacity(3 + CONTRACT_WIDTH * self.contracts.len() + 2 * self.settlements.len());
        out.push(self.phase.code());
        out.push(self.contracts.len() as f64);
        for c in &self.contracts {
            out.push(c.expiry as f64);
            out.push(c.mark);
            out.push(c.impact);
            out.push(c.pending);
            out.extend_from_slice(&c.taken);
        }
        out.push(self.settlements.len() as f64);
        for s in &self.settlements {
            out.push(s.session as f64);
            out.push(s.value);
        }
        out
    }

    /// Put back what [`VixFuturesState::to_words`] wrote, refusing a list
    /// that is not one. The book is carried apart.
    pub(crate) fn set_words(&mut self, words: &[f64]) -> Result<(), String> {
        let bad = |why: &str| format!("this snapshot's vix_futures do not parse: {why}");
        let whole = |v: f64, what: &str| -> Result<usize, String> {
            if v.is_finite() && v >= 0.0 && v.fract() == 0.0 && v <= 1e9 {
                Ok(v as usize)
            } else {
                Err(bad(&format!("{what} is {v}")))
            }
        };
        if words.len() < 3 {
            return Err(bad("fewer than three numbers"));
        }
        let phase = VixPhase::from_code(words[0]).ok_or_else(|| bad(&format!("the phase is {}", words[0])))?;
        let n = whole(words[1], "the contract count")?;
        let mut at = 2;
        if words.len() < at + CONTRACT_WIDTH * n + 1 {
            return Err(bad("it is shorter than its counts say"));
        }
        let mut contracts = Vec::with_capacity(n);
        for _ in 0..n {
            let w = &words[at..at + CONTRACT_WIDTH];
            if !(w[0].is_finite() && w[0].fract() == 0.0) {
                return Err(bad(&format!("an expiry is {}", w[0])));
            }
            if !(w[1].is_nan() || (w[1].is_finite() && w[1] > 0.0)) {
                return Err(bad(&format!("a mark is {}", w[1])));
            }
            if w[2..].iter().any(|v| !v.is_finite()) {
                return Err(bad("a contract's impact, flow or taken depth is not finite"));
            }
            contracts.push(ListedVixFuture {
                expiry: w[0] as i64,
                mark: w[1],
                impact: w[2],
                pending: w[3],
                taken: [w[4], w[5], w[6], w[7]],
            });
            at += CONTRACT_WIDTH;
        }
        let m = whole(words[at], "the settlement count")?;
        at += 1;
        if words.len() != at + 2 * m {
            return Err(bad(&format!("{} numbers for {n} contracts and {m} settlements", words.len())));
        }
        let mut settlements = Vec::with_capacity(m);
        for k in 0..m {
            let w = &words[at + 2 * k..at + 2 * k + 2];
            if !(w[0].is_finite() && w[0].fract() == 0.0 && w[1].is_finite() && w[1] > 0.0) {
                return Err(bad("a settlement is not a session and a positive VIX"));
            }
            settlements.push(vix_settlement(w[0] as i64, w[1]));
        }
        self.phase = phase;
        self.contracts = contracts;
        self.settlements = settlements;
        Ok(())
    }
}

/// The VIX future expiring at `session`, settled on the published VIX `vix`.
fn vix_settlement(session: i64, vix: f64) -> Settlement {
    let kind = ContractKind::VixFuture;
    Settlement { symbol: vix_future_symbol(session), kind, root: kind.root().to_string(), session, value: vix, reference: vix }
}

/// A price on the contract's grid, rounded away from the touch.
fn on_grid(price: f64, tick: f64, side: Side) -> f64 {
    let steps = price / tick;
    match side {
        Side::Sell => (steps - 1e-7).ceil() * tick,
        Side::Buy => (steps + 1e-7).floor() * tick,
    }
}

/// The loading's part at `m` sessions from expiry with half-life `h`: 1 at
/// `m = 1`, and with a half-life of 0.0, 0 beyond.
fn part(m: f64, h: f64) -> f64 {
    if m <= 1.0 {
        1.0
    } else if h == 0.0 {
        0.0
    } else {
        crate::mathx::pow(0.5, (m - 1.0) / h)
    }
}

/// What a VIX future is worth now, and from what.
#[derive(Debug, Clone, Copy)]
pub(crate) struct VixValuation {
    /// The VIX the price reads: the live VIX in a session (the forecast's
    /// expectation of tonight's without it), the published VIX outside.
    pub(crate) vix: f64,
    pub(crate) expected: f64,
    pub(crate) premium: f64,
    pub(crate) loading: f64,
    pub(crate) fair: f64,
    /// Sessions from now to the expiry's open.
    pub(crate) sessions: f64,
    pub(crate) price: f64,
}

impl Engine {
    // ── State ─────────────────────────────────────────────────────────────

    pub(super) fn vix_futures_on(&self) -> bool {
        self.params.futures_vix_listed != 0.0
    }

    /// The listed VIX future named `symbol`, by slot.
    pub(super) fn vix_futures_slot(&self, symbol: &str) -> Option<usize> {
        let parsed = ContractSymbol::parse(symbol).ok()?;
        if parsed.root != ContractKind::VixFuture.root() || parsed.kind != SymbolKind::Future {
            return None;
        }
        self.vix_futures.contracts.iter().position(|c| c.expiry == parsed.expiry)
    }

    /// The intraday loading at `m` sessions from expiry, counted from
    /// tonight's close (`futures_vix_live_fast_share`).
    pub(crate) fn vix_futures_loading(&self, m: f64) -> f64 {
        let p = &self.params;
        let w = p.futures_vix_live_fast_share;
        w * part(m, p.futures_vix_live_fast_half_life) + (1.0 - w) * part(m, p.futures_vix_live_slow_half_life)
    }

    // ── Pricing ───────────────────────────────────────────────────────────

    /// The forecast's expected published VIX after `closes` closes from the
    /// close it was taken at; the published VIX now at 0.
    fn vix_expected(&self, f: &crate::derivatives::Forecast, closes: i64) -> f64 {
        if closes <= 0 || f.vix.is_empty() {
            return self.published_vix();
        }
        let h = crate::mathx::min(closes as f64, f.vix.len() as f64) as usize;
        f.vix[h - 1]
    }

    /// What the contract in `slot` is worth now. `None` without a forecast.
    pub(crate) fn vix_futures_valuation(&self, slot: usize) -> Option<VixValuation> {
        let spec = &VIX_FUTURE;
        let f = self.forecast()?;
        let c = &self.vix_futures.contracts[slot];
        let published = self.published_vix();
        // The forecast is taken at a close for the next session, `f.day`, so
        // the session that close ended is `f.day - 1`; the contract is `n`
        // sessions from expiry counted from it, and settles on the published
        // VIX `n - 1` closes on.
        let closed = f.day - 1;
        let n = (c.expiry - closed) as f64;
        let expected = self.vix_expected(f, c.expiry - closed - 1);
        let (vix, premium, loading, sessions) = match self.vix_futures.phase {
            VixPhase::Session => {
                let m = n - 1.0;
                let tonight = self.vix_expected(f, 1);
                let live = self.live_vix().unwrap_or(tonight);
                let k = crate::mathx::min(1.0, f64::from(self.ticks_today()) / SESSION_TICKS);
                let premium = (1.0 - k) * spec.premium.at(n, published) + k * spec.premium.at(m, live);
                let loading = self.vix_futures_loading(m);
                (live, premium, loading, n - k)
            }
            _ => (published, spec.premium.at(n, published), self.vix_futures_loading(n - 1.0), n),
        };
        let surprise = match self.vix_futures.phase {
            VixPhase::Session => vix - self.vix_expected(f, 1),
            _ => 0.0,
        };
        let fair = expected + loading * surprise + premium;
        Some(VixValuation { vix, expected, premium, loading, fair, sessions, price: fair * (1.0 + c.impact) })
    }

    /// The daily volume the contract in `slot`'s book is sized to.
    fn vix_futures_daily_volume(slot: usize) -> f64 {
        let spec = &VIX_FUTURE;
        if slot < calendar::VIX_FUTURES_WITH_DEPTH {
            spec.front_daily_volume[slot]
        } else {
            spec.deferred_daily_volume
        }
    }

    // ── The book ──────────────────────────────────────────────────────────

    /// The latent depth on one side of a contract's book, as an index
    /// future's.
    fn vix_futures_depth(book: &mut OrderBook, side: Side, touch: f64, volume: f64, spec: &VixFutureSpec) {
        let (y, sigma) = (spec.depth_coefficient, spec.daily_sigma);
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
            let size = bound.floor() - placed;
            if !(size >= 1.0) {
                continue;
            }
            placed += size;
            pending = match pending {
                Some((p, q)) if p == price => Some((p, q + size)),
                Some(level) => {
                    levels.push(level);
                    Some((price, size))
                }
                None => Some((price, size)),
            };
        }
        if let Some(level) = pending {
            levels.push(level);
        }
        for (price, size) in levels {
            book.rest_limit(side, price, size, DEPTH_OWNER);
        }
    }

    /// The agent-facing book of the contract in `slot`, leaving out one
    /// agent's orders: the maker's ladder around the price, latent depth
    /// beside it on the front three, less what agents took from the front of
    /// each, and every agent's resting orders.
    fn vix_futures_book(&self, slot: usize, exclude: Option<&str>) -> OrderBook {
        let spec = &VIX_FUTURE;
        let c = &self.vix_futures.contracts[slot];
        let symbol = vix_future_symbol(c.expiry);
        let price = self.vix_futures_valuation(slot).map(|v| v.price).unwrap_or(f64::NAN);
        let mut book = OrderBook::new(symbol.clone(), if price.is_finite() { Some(price) } else { None })
            .with_cap(AGENT_BOOK_CAP);
        if !(price.is_finite() && price > spec.tick) {
            return book;
        }
        let volume = Self::vix_futures_daily_volume(slot);
        let size = crate::mathx::max(1.0, (volume * spec.maker_level_share).floor());
        let n = (price / spec.tick).floor();
        for k in 0..spec.maker_levels {
            let k = k as f64;
            if n - k >= 1.0 {
                book.rest_limit(Side::Buy, (n - k) * spec.tick, size, MARKET_MAKER_ID);
            }
            book.rest_limit(Side::Sell, (n + 1.0 + k) * spec.tick, size, MARKET_MAKER_ID);
        }
        if slot < calendar::VIX_FUTURES_WITH_DEPTH {
            Self::vix_futures_depth(&mut book, Side::Buy, n * spec.tick, volume, spec);
            Self::vix_futures_depth(&mut book, Side::Sell, (n + 1.0) * spec.tick, volume, spec);
        }
        remove_front(&mut book, Side::Buy, MARKET_MAKER_ID, c.taken[TAKEN_MAKER_BID]);
        remove_front(&mut book, Side::Sell, MARKET_MAKER_ID, c.taken[TAKEN_MAKER_ASK]);
        remove_front(&mut book, Side::Buy, DEPTH_OWNER, c.taken[TAKEN_DEPTH_BID]);
        remove_front(&mut book, Side::Sell, DEPTH_OWNER, c.taken[TAKEN_DEPTH_ASK]);
        for o in &self.vix_futures.book.orders {
            if o.ticker != symbol || exclude == Some(o.agent.as_str()) {
                continue;
            }
            book.post_limit(o.side, o.limit, o.remaining, &o.agent, Some(o.id.clone()));
        }
        book
    }

    /// The agent-facing book of the listed VIX future in `slot`.
    pub(super) fn vix_futures_book_at(&self, slot: usize) -> Option<OrderBook> {
        if !self.vix_futures_on() || slot >= self.vix_futures.contracts.len() {
            return None;
        }
        Some(self.vix_futures_book(slot, None))
    }

    fn vix_futures_push_fill(&mut self, mut fill: AgentFill) -> AgentFill {
        fill.sequence = self.vix_futures.book.fill_sequence;
        self.vix_futures.book.fill_sequence += 1;
        self.vix_futures.book.fills.push(fill.clone());
        fill
    }

    fn vix_futures_reduce_order(&mut self, order_id: &str, quantity: f64) {
        if let Some(o) = self.vix_futures.book.orders.iter_mut().find(|o| o.id == order_id) {
            o.remaining -= quantity;
        }
        self.vix_futures.book.orders.retain(|o| o.remaining > 1e-9);
    }

    /// An order meets a contract's book, as an index future's does.
    #[allow(clippy::too_many_arguments)]
    fn vix_futures_meet(
        &mut self,
        slot: usize,
        agent: &str,
        order_id: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
    ) -> (Vec<AgentFill>, f64) {
        let mut book = self.vix_futures_book(slot, Some(agent));
        let symbol = book.company_id.clone();
        let reference = book
            .mid_price()
            .or_else(|| self.vix_futures_valuation(slot).map(|v| v.price))
            .unwrap_or(f64::NAN);
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
            if owner == MARKET_MAKER_ID || owner == DEPTH_OWNER {
                let k = match (side, owner == MARKET_MAKER_ID) {
                    (Side::Buy, true) => TAKEN_MAKER_ASK,
                    (Side::Sell, true) => TAKEN_MAKER_BID,
                    (Side::Buy, false) => TAKEN_DEPTH_ASK,
                    (Side::Sell, false) => TAKEN_DEPTH_BID,
                };
                let c = &mut self.vix_futures.contracts[slot];
                c.taken[k] += f.quantity;
                c.pending += match side {
                    Side::Buy => f.quantity,
                    Side::Sell => -f.quantity,
                };
            } else {
                self.vix_futures_reduce_order(&f.maker_order_id, f.quantity);
                self.vix_futures_push_fill(AgentFill {
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
            taker.push(self.vix_futures_push_fill(AgentFill {
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

    /// Match every resting order the VIX futures' books now cross.
    fn vix_futures_cross_resting(&mut self) {
        if self.vix_futures.book.orders.is_empty() || self.vix_futures.phase != VixPhase::Session {
            return;
        }
        let ids: Vec<String> = self.vix_futures.book.orders.iter().map(|o| o.id.clone()).collect();
        for id in ids {
            let Some(o) = self.vix_futures.book.orders.iter().find(|o| o.id == id).cloned() else {
                continue;
            };
            let Some(slot) = self.vix_futures_slot(&o.ticker) else {
                continue;
            };
            let book = self.vix_futures_book(slot, Some(&o.agent));
            let crossed = match o.side {
                Side::Buy => book.best_ask().is_some_and(|a| o.limit >= a),
                Side::Sell => book.best_bid().is_some_and(|b| o.limit <= b),
            };
            if !crossed {
                continue;
            }
            let (fills, _) = self.vix_futures_meet(slot, &o.agent, &o.id, o.side, o.remaining, Some(o.limit));
            let filled: f64 = fills.iter().map(|f| f.quantity).sum();
            self.vix_futures_reduce_order(&o.id, filled);
        }
    }

    /// An agent's order on a listed VIX future: as on an index future, in
    /// the session only.
    pub(super) fn submit_vix_future_order(
        &mut self,
        agent: &str,
        symbol: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
        order_id: Option<String>,
    ) -> Result<OrderReport, String> {
        let slot = self.vix_futures_slot(symbol).ok_or_else(|| {
            format!("{symbol} is not a listed contract: Engine.contracts() lists the ones that trade")
        })?;
        if self.vix_futures.phase != VixPhase::Session {
            return Err(format!(
                "{symbol} is a VIX future, which trades in the session only: the VIX moves only at a \
                 close, and nothing prices it between a close and the next open"
            ));
        }
        let id = match order_id {
            Some(id) => {
                if id.is_empty() {
                    return Err("order_id cannot be empty".to_string());
                }
                id
            }
            None => format!("{agent}-{symbol}-{}", self.vix_futures.book.sequence),
        };
        if self.vix_futures.book.orders.iter().any(|o| o.id == id)
            || self.futures.book.orders.iter().any(|o| o.id == id)
            || self.book.orders.iter().any(|o| o.id == id)
        {
            return Err(format!("order id {id:?} is already waiting in the book"));
        }
        let sequence = self.vix_futures.book.sequence;
        self.vix_futures.book.sequence += 1;
        self.vix_futures_cross_resting();
        let (fills, reference) = self.vix_futures_meet(slot, agent, &id, side, quantity, limit);
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
                self.vix_futures.book.orders.push(AgentOrder {
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

    /// Cancel a resting order on a VIX future.
    pub(super) fn cancel_vix_future_order(&mut self, order_id: &str, agent: Option<&str>) -> bool {
        let before = self.vix_futures.book.orders.len();
        self.vix_futures.book.orders.retain(|o| !(o.id == order_id && agent.is_none_or(|a| a == o.agent)));
        self.vix_futures.book.orders.len() != before
    }

    // ── Steps and the day's boundaries ────────────────────────────────────

    /// A tick's step, on every open tick: the flow since the last step
    /// reaches the price's mark and the old mark decays, the maker
    /// re-quotes whole and the latent depth refills, and resting orders the
    /// moved books cross fill. Nothing with the switch off.
    pub(super) fn vix_futures_session_step(&mut self) {
        if !self.vix_futures_on() || self.vix_futures.phase != VixPhase::Session {
            return;
        }
        let spec = &VIX_FUTURE;
        let decay = crate::mathx::pow(0.5, 1.0 / spec.impact_half_life);
        let refill = crate::mathx::pow(0.5, 1.0 / spec.refill_half_life);
        for (slot, c) in self.vix_futures.contracts.iter_mut().enumerate() {
            let scale = spec.impact_coefficient * spec.daily_sigma / Self::vix_futures_daily_volume(slot);
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
        self.vix_futures_cross_resting();
    }

    /// At an open: each contract expiring at this session settles on the
    /// published VIX, the previous close's value, its resting orders go,
    /// the next is listed, and resting orders the opening books cross fill.
    /// Nothing before the first close or with the switch off.
    pub(super) fn vix_futures_open(&mut self) {
        if !self.vix_futures_on() || self.vix_futures.phase == VixPhase::BeforeFirstClose {
            return;
        }
        self.vix_futures.phase = VixPhase::Session;
        let t = self.elapsed_days;
        let vix = self.published_vix();
        let mut kept = Vec::with_capacity(self.vix_futures.contracts.len());
        for c in std::mem::take(&mut self.vix_futures.contracts) {
            if c.expiry <= t {
                self.vix_futures.settlements.push(vix_settlement(c.expiry, vix));
                let symbol = vix_future_symbol(c.expiry);
                self.vix_futures.book.orders.retain(|o| o.ticker != symbol);
            } else {
                kept.push(c);
            }
        }
        for expiry in calendar::vix_future_expiries(t, calendar::VIX_FUTURES_LISTED) {
            if !kept.iter().any(|c| c.expiry == expiry) {
                kept.push(ListedVixFuture::new(expiry));
            }
        }
        kept.sort_by_key(|c| c.expiry);
        kept.truncate(calendar::VIX_FUTURES_LISTED);
        for c in kept.iter_mut() {
            c.taken = [0.0; 4];
        }
        self.vix_futures.contracts = kept;
        self.vix_futures_cross_resting();
    }

    /// At a close, after the forecast is taken on the state it leaves: the
    /// first close lists the next six, and each contract's settlement mark
    /// is its value on the new forecast and published VIX. Nothing with the
    /// switch off.
    pub(super) fn vix_futures_close_marks(&mut self) {
        if !self.vix_futures_on() || self.forecast().is_none() {
            return;
        }
        if self.vix_futures.phase == VixPhase::BeforeFirstClose {
            self.vix_futures.contracts = calendar::vix_future_expiries(self.elapsed_days, calendar::VIX_FUTURES_LISTED)
                .into_iter()
                .map(ListedVixFuture::new)
                .collect();
        }
        self.vix_futures.phase = VixPhase::Night;
        for slot in 0..self.vix_futures.contracts.len() {
            if let Some(v) = self.vix_futures_valuation(slot) {
                self.vix_futures.contracts[slot].mark = v.price;
            }
        }
    }

    // ── Reads ─────────────────────────────────────────────────────────────

    /// The listed VIX futures, in expiry order. The front by the roll rule
    /// is the first whose roll date, six sessions before expiry, has not
    /// come.
    pub(super) fn vix_futures_contracts(&self) -> Vec<ContractSpec> {
        if !self.vix_futures_on() {
            return Vec::new();
        }
        let session = match self.vix_futures.phase {
            VixPhase::Night => self.elapsed_days + 1,
            _ => self.elapsed_days,
        };
        let front = self.vix_futures.contracts.iter().position(|c| c.expiry - ROLL_SESSIONS > session);
        let kind = ContractKind::VixFuture;
        self.vix_futures
            .contracts
            .iter()
            .enumerate()
            .map(|(k, c)| ContractSpec {
                symbol: vix_future_symbol(c.expiry),
                kind,
                root: kind.root().to_string(),
                expiry: c.expiry,
                roll: c.expiry - ROLL_SESSIONS,
                multiplier: VIX_FUTURE.multiplier,
                tick: VIX_FUTURE.tick,
                settlement: kind.settlement(),
                front: front == Some(k),
            })
            .collect()
    }

    /// A listed VIX future's quote now.
    pub(super) fn vix_futures_quote(&self, slot: usize) -> Option<Quote> {
        let c = &self.vix_futures.contracts[slot];
        let v = self.vix_futures_valuation(slot)?;
        let book = self.vix_futures_book(slot, None);
        let (bid, ask) = (book.best_bid(), book.best_ask());
        Some(Quote {
            symbol: vix_future_symbol(c.expiry),
            kind: ContractKind::VixFuture,
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
            basis_bp: 1e4 * c.impact,
            index: v.vix,
            rate: 0.0,
            dividends: 0.0,
            sessions_to_expiry: v.sessions,
            multiplier: VIX_FUTURE.multiplier,
            tick: VIX_FUTURE.tick,
            daily_volume: Self::vix_futures_daily_volume(slot),
            initial_margin: None,
            expected: Some(v.expected),
            premium: Some(v.premium),
            loading: Some(v.loading),
        })
    }

    /// The VIX futures' final settlements, at `session`'s open or all.
    pub(super) fn vix_futures_settlements(&self, session: Option<i64>) -> Vec<Settlement> {
        if !self.vix_futures_on() {
            return Vec::new();
        }
        self.vix_futures
            .settlements
            .iter()
            .filter(|s| session.is_none_or(|d| s.session == d))
            .cloned()
            .collect()
    }

    /// Agents' resting orders on VIX futures, for `Engine::open_orders`.
    pub(super) fn vix_futures_orders(&self) -> &[AgentOrder] {
        &self.vix_futures.book.orders
    }

    /// Take the VIX futures' fills, for one agent or all.
    pub(super) fn take_vix_futures_fills(&mut self, agent: Option<&str>) -> Vec<AgentFill> {
        if self.vix_futures.book.fills.is_empty() {
            return Vec::new();
        }
        let (taken, kept): (Vec<_>, Vec<_>) = std::mem::take(&mut self.vix_futures.book.fills)
            .into_iter()
            .partition(|f| agent.is_none_or(|a| a == f.agent));
        self.vix_futures.book.fills = kept;
        taken
    }

    // ── Snapshot ──────────────────────────────────────────────────────────

    /// The VIX futures' numbers for the snapshot and the hash: `None` with
    /// the switch off.
    pub fn vix_futures_words(&self) -> Option<Vec<f64>> {
        if self.vix_futures_on() {
            Some(self.vix_futures.to_words())
        } else {
            None
        }
    }

    /// Agents' resting orders on VIX futures and their undelivered fills:
    /// `None` with the switch off and while no agent has traded one.
    pub fn vix_futures_book_state(&self) -> Option<&BookState> {
        if self.vix_futures_on() && !self.vix_futures.book.is_pristine() {
            Some(&self.vix_futures.book)
        } else {
            None
        }
    }

    /// Put the VIX futures back (a restore), each part refused where the
    /// switch is off.
    pub fn set_vix_futures_state(&mut self, words: Option<&[f64]>, book: Option<BookState>) -> Result<(), String> {
        if !self.vix_futures_on() {
            if words.is_some() || book.is_some() {
                return Err("this snapshot carries the VIX futures (vix_futures, vix_futures_book), which \
                            only an engine with futures_vix_listed on keeps, and this engine's model has it \
                            off"
                    .to_string());
            }
            self.vix_futures = VixFuturesState::default();
            return Ok(());
        }
        let Some(words) = words else {
            return Err("an engine with futures_vix_listed on carries its VIX futures (vix_futures); this \
                        snapshot lacks them"
                .to_string());
        };
        let mut state = VixFuturesState::default();
        state.set_words(words)?;
        state.book = book.unwrap_or_default();
        self.vix_futures = state;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_loading_is_one_at_one_session_and_its_parts_decay_at_their_half_lives() {
        assert_eq!(part(1.0, 0.0), 1.0);
        assert_eq!(part(2.0, 0.0), 0.0);
        assert_eq!(part(1.0, 5.0), 1.0);
        assert!((part(6.0, 5.0) - 0.5).abs() < 1e-15);
    }

    #[test]
    fn the_words_round_trip_and_refuse_what_is_not_theirs() {
        let s = VixFuturesState {
            phase: VixPhase::Night,
            contracts: vec![
                ListedVixFuture::new(35),
                ListedVixFuture { expiry: 56, mark: 21.5, impact: 1e-4, pending: 2.0, taken: [1.0, 2.0, 3.0, 4.0] },
            ],
            settlements: vec![vix_settlement(14, 17.25)],
            ..Default::default()
        };
        let words = s.to_words();
        let mut back = VixFuturesState::default();
        back.set_words(&words).unwrap();
        assert_eq!(back.to_words().iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                   words.iter().map(|v| v.to_bits()).collect::<Vec<_>>());
        assert_eq!(back.settlements[0].value, 17.25);
        assert_eq!(back.settlements[0].reference, 17.25);
        let mut short = words.clone();
        short.pop();
        assert!(VixFuturesState::default().set_words(&short).is_err());
        let mut bad = words.clone();
        bad[0] = 7.0;
        assert!(VixFuturesState::default().set_words(&bad).is_err());
    }

    #[test]
    fn the_premium_is_zero_at_one_session_and_the_frozen_fit_reads_vf6s_centres() {
        let p = VIX_FUTURE.premium;
        assert_eq!(p.at(1.0, 40.0), 0.0);
        // vix_premium.json's own check: a(21) 1.1369, a(63) 1.9848, a(126) 2.8035.
        assert!((p.level(21.0) - 1.1369).abs() < 5e-5, "{}", p.level(21.0));
        assert!((p.level(63.0) - 1.9848).abs() < 5e-5, "{}", p.level(63.0));
        assert!((p.level(126.0) - 2.8035).abs() < 5e-5, "{}", p.level(126.0));
        assert!((p.at(21.0, p.centre) - p.level(21.0)).abs() < 1e-15);
    }
}
