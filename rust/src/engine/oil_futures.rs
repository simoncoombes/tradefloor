//! The oil futures (`futures_oil_listed`): the engine's half.
//!
//! An oil future reads the oil price and the forecast, and writes only its
//! own state ([`OilFuturesState`]): its marks, its flow's mark, its book and
//! its settlements. It draws nothing. So every stock price, close and
//! economy is the one the engine prints with the switch off.
//!
//! # The contracts
//!
//! Monthly, on the VIX futures' calendar: each expires at session 15 of a
//! 21-session month, `21m + 14`, the next twelve listed from the first
//! close. A contract settles at its expiry session's open, in cash, on the
//! oil price then: the previous close's, since oil moves only at a close.
//!
//! # The price
//!
//! A contract `n` sessions from expiry, counted from the last close, is
//! worth the forecast's expected oil price after `n - 1` closes (the price
//! now at `n = 1`; past the forecast's horizon its last price; without a
//! forecast the price now), with no premium: no oil premium is registered,
//! and a price off the model's own expectation would be an edge the model's
//! dynamics do not support. So the curve carries what the forecast expects:
//! the reversion to the target, the inventory push, the OPEC decision and the
//! dollar's drift. A contract holds its price through a session, since oil
//! moves only at a close, and a pin moves it at once. Agents' net taker flow
//! against the house marks the price, `F (1 + i)`, and the mark decays.

use super::*;
use crate::agent_book::{remove_front, AgentFill, AgentOrder, BookState, Liquidity, RestMode, AGENT_BOOK_CAP};
use crate::derivatives::calendar::{self, ROLL_SESSIONS};
use crate::derivatives::{
    oil_future_symbol, ContractKind, ContractSpec, ContractSymbol, Quote, Settlement, SymbolKind, OIL_FUTURE,
};
use crate::market_maker::MARKET_MAKER_ID;
use crate::order_book::{OrderBook, Side, SubmitOptions};

const TAKEN_MAKER_BID: usize = 0;
const TAKEN_MAKER_ASK: usize = 1;

/// Where the oil futures stand in the day.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum OilPhase {
    BeforeFirstClose,
    Session,
    Night,
}

impl OilPhase {
    fn code(self) -> f64 {
        match self {
            OilPhase::BeforeFirstClose => 0.0,
            OilPhase::Session => 1.0,
            OilPhase::Night => 2.0,
        }
    }

    fn from_code(v: f64) -> Option<Self> {
        if v == 0.0 {
            Some(OilPhase::BeforeFirstClose)
        } else if v == 1.0 {
            Some(OilPhase::Session)
        } else if v == 2.0 {
            Some(OilPhase::Night)
        } else {
            None
        }
    }
}

/// One listed oil future.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ListedOilFuture {
    pub(crate) expiry: i64,
    pub(crate) mark: f64,
    /// Agents' flow's mark on the price, a share of it.
    pub(crate) impact: f64,
    pub(crate) pending: f64,
    pub(crate) taken: [f64; 2],
}

impl ListedOilFuture {
    fn new(expiry: i64) -> Self {
        ListedOilFuture { expiry, mark: f64::NAN, impact: 0.0, pending: 0.0, taken: [0.0; 2] }
    }
}

const CONTRACT_WIDTH: usize = 6;

/// Everything the oil futures carry.
#[derive(Debug, Clone)]
pub(crate) struct OilFuturesState {
    pub(crate) phase: OilPhase,
    pub(crate) contracts: Vec<ListedOilFuture>,
    pub(crate) settlements: Vec<Settlement>,
    pub(crate) book: BookState,
}

impl Default for OilFuturesState {
    fn default() -> Self {
        OilFuturesState {
            phase: OilPhase::BeforeFirstClose,
            contracts: Vec::new(),
            settlements: Vec::new(),
            book: BookState::default(),
        }
    }
}

impl OilFuturesState {
    /// The numbers the snapshot's `oil_futures` key and the state hash
    /// carry: the phase, the contract count and each contract's expiry,
    /// mark, impact, pending flow and two taken counts, then the settlement
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

    /// Put back what [`OilFuturesState::to_words`] wrote, refusing a list
    /// that is not one. The book is carried apart.
    pub(crate) fn set_words(&mut self, words: &[f64]) -> Result<(), String> {
        let bad = |why: &str| format!("this snapshot's oil_futures do not parse: {why}");
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
        let phase = OilPhase::from_code(words[0]).ok_or_else(|| bad(&format!("the phase is {}", words[0])))?;
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
            contracts.push(ListedOilFuture {
                expiry: w[0] as i64,
                mark: w[1],
                impact: w[2],
                pending: w[3],
                taken: [w[4], w[5]],
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
                return Err(bad("a settlement is not a session and a positive price"));
            }
            settlements.push(oil_settlement(w[0] as i64, w[1]));
        }
        self.phase = phase;
        self.contracts = contracts;
        self.settlements = settlements;
        Ok(())
    }
}

/// The oil future expiring at `session`, settled on the oil price `price`.
fn oil_settlement(session: i64, price: f64) -> Settlement {
    let kind = ContractKind::OilFuture;
    Settlement { symbol: oil_future_symbol(session), kind, root: kind.root().to_string(), session, value: price, reference: price }
}

/// What an oil future is worth now, and from what.
#[derive(Debug, Clone, Copy)]
pub(crate) struct OilValuation {
    pub(crate) expected: f64,
    pub(crate) rate: f64,
    pub(crate) sessions: f64,
    pub(crate) price: f64,
}

impl Engine {
    pub(super) fn oil_futures_on(&self) -> bool {
        self.params.futures_oil_listed != 0.0
    }

    /// The listed oil future named `symbol`, by slot.
    pub(super) fn oil_futures_slot(&self, symbol: &str) -> Option<usize> {
        let parsed = ContractSymbol::parse(symbol).ok()?;
        if parsed.root != ContractKind::OilFuture.root() || parsed.kind != SymbolKind::Future {
            return None;
        }
        self.oil_futures.contracts.iter().position(|c| c.expiry == parsed.expiry)
    }

    /// The session the last close ended: the forecast's day less one, or the
    /// engine's day count without a forecast.
    fn oil_futures_closed(&self) -> i64 {
        match self.forecast() {
            Some(f) => f.day - 1,
            None => match self.oil_futures.phase {
                OilPhase::Session => self.elapsed_days - 1,
                _ => self.elapsed_days,
            },
        }
    }

    /// The oil price expected after `closes` closes from the last: the price
    /// now at 0 and without a forecast, the forecast's last past its horizon.
    fn oil_expected(&self, closes: i64) -> f64 {
        let now = self.economy.oil_price;
        let Some(f) = self.forecast() else {
            return now;
        };
        if closes <= 0 || f.oil.is_empty() {
            return now;
        }
        let h = crate::mathx::min(closes as f64, f.oil.len() as f64) as usize;
        f.oil[h - 1]
    }

    /// The mean policy rate the forecast expects over the sessions to
    /// `expiry`, a fraction: what the quote reports as the carry's rate.
    fn oil_futures_rate(&self, closed: i64, expiry: i64) -> f64 {
        let now = self.economy.federal_funds_rate;
        let Some(f) = self.forecast() else {
            return now / 100.0;
        };
        if expiry <= closed + 1 || f.policy_rate.is_empty() {
            return now / 100.0;
        }
        let mut total = 0.0;
        for h in 1..(expiry - closed) {
            total += f.policy_rate[crate::mathx::min(h as f64, f.policy_rate.len() as f64) as usize - 1];
        }
        total / (expiry - closed - 1) as f64 / 100.0
    }

    /// What the contract in `slot` is worth now. `None` before the first
    /// close.
    pub(crate) fn oil_futures_valuation(&self, slot: usize) -> Option<OilValuation> {
        if self.oil_futures.phase == OilPhase::BeforeFirstClose {
            return None;
        }
        let c = &self.oil_futures.contracts[slot];
        let closed = self.oil_futures_closed();
        let n = c.expiry - closed;
        let expected = self.oil_expected(n - 1);
        Some(OilValuation {
            expected,
            rate: self.oil_futures_rate(closed, c.expiry),
            sessions: n as f64,
            price: expected * (1.0 + c.impact),
        })
    }

    fn oil_futures_daily_volume(slot: usize) -> f64 {
        let v = OIL_FUTURE.daily_volume;
        v[crate::mathx::min(slot as f64, (v.len() - 1) as f64) as usize]
    }

    /// The book of the contract in `slot`: the maker's ladder around the
    /// price, less what agents took from its front, and every agent's
    /// resting orders.
    fn oil_futures_book(&self, slot: usize, exclude: Option<&str>) -> OrderBook {
        let spec = &OIL_FUTURE;
        let c = &self.oil_futures.contracts[slot];
        let symbol = oil_future_symbol(c.expiry);
        let price = self.oil_futures_valuation(slot).map(|v| v.price).unwrap_or(f64::NAN);
        let mut book = OrderBook::new(symbol.clone(), if price.is_finite() { Some(price) } else { None })
            .with_cap(AGENT_BOOK_CAP);
        if !(price.is_finite() && price > spec.tick) {
            return book;
        }
        let size = crate::mathx::max(1.0, (Self::oil_futures_daily_volume(slot) * spec.maker_level_share).floor());
        let n = (price / spec.tick).floor();
        for k in 0..spec.maker_levels {
            let k = k as f64;
            if n - k >= 1.0 {
                book.rest_limit(Side::Buy, (n - k) * spec.tick, size, MARKET_MAKER_ID);
            }
            book.rest_limit(Side::Sell, (n + 1.0 + k) * spec.tick, size, MARKET_MAKER_ID);
        }
        remove_front(&mut book, Side::Buy, MARKET_MAKER_ID, c.taken[TAKEN_MAKER_BID]);
        remove_front(&mut book, Side::Sell, MARKET_MAKER_ID, c.taken[TAKEN_MAKER_ASK]);
        for o in &self.oil_futures.book.orders {
            if o.ticker != symbol || exclude == Some(o.agent.as_str()) {
                continue;
            }
            book.post_limit(o.side, o.limit, o.remaining, &o.agent, Some(o.id.clone()));
        }
        book
    }

    pub(super) fn oil_futures_book_at(&self, slot: usize) -> Option<OrderBook> {
        if !self.oil_futures_on() || slot >= self.oil_futures.contracts.len() {
            return None;
        }
        Some(self.oil_futures_book(slot, None))
    }

    fn oil_futures_push_fill(&mut self, mut fill: AgentFill) -> AgentFill {
        fill.sequence = self.oil_futures.book.fill_sequence;
        self.oil_futures.book.fill_sequence += 1;
        self.oil_futures.book.fills.push(fill.clone());
        fill
    }

    fn oil_futures_reduce_order(&mut self, order_id: &str, quantity: f64) {
        if let Some(o) = self.oil_futures.book.orders.iter_mut().find(|o| o.id == order_id) {
            o.remaining -= quantity;
        }
        self.oil_futures.book.orders.retain(|o| o.remaining > 1e-9);
    }

    #[allow(clippy::too_many_arguments)]
    fn oil_futures_meet(
        &mut self,
        slot: usize,
        agent: &str,
        order_id: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
    ) -> (Vec<AgentFill>, f64) {
        let mut book = self.oil_futures_book(slot, Some(agent));
        let symbol = book.company_id.clone();
        let reference = book
            .mid_price()
            .or_else(|| self.oil_futures_valuation(slot).map(|v| v.price))
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
            if owner == MARKET_MAKER_ID {
                let c = &mut self.oil_futures.contracts[slot];
                c.taken[if side == Side::Buy { TAKEN_MAKER_ASK } else { TAKEN_MAKER_BID }] += f.quantity;
                c.pending += match side {
                    Side::Buy => f.quantity,
                    Side::Sell => -f.quantity,
                };
            } else {
                self.oil_futures_reduce_order(&f.maker_order_id, f.quantity);
                self.oil_futures_push_fill(AgentFill {
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
            taker.push(self.oil_futures_push_fill(AgentFill {
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

    fn oil_futures_cross_resting(&mut self) {
        if self.oil_futures.book.orders.is_empty() || self.oil_futures.phase != OilPhase::Session {
            return;
        }
        let ids: Vec<String> = self.oil_futures.book.orders.iter().map(|o| o.id.clone()).collect();
        for id in ids {
            let Some(o) = self.oil_futures.book.orders.iter().find(|o| o.id == id).cloned() else {
                continue;
            };
            let Some(slot) = self.oil_futures_slot(&o.ticker) else {
                continue;
            };
            let book = self.oil_futures_book(slot, Some(&o.agent));
            let crossed = match o.side {
                Side::Buy => book.best_ask().is_some_and(|a| o.limit >= a),
                Side::Sell => book.best_bid().is_some_and(|b| o.limit <= b),
            };
            if !crossed {
                continue;
            }
            let (fills, _) = self.oil_futures_meet(slot, &o.agent, &o.id, o.side, o.remaining, Some(o.limit));
            let filled: f64 = fills.iter().map(|f| f.quantity).sum();
            self.oil_futures_reduce_order(&o.id, filled);
        }
    }

    /// An agent's order on a listed oil future, in the session only.
    pub(super) fn submit_oil_future_order(
        &mut self,
        agent: &str,
        symbol: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
        order_id: Option<String>,
    ) -> Result<OrderReport, String> {
        let slot = self.oil_futures_slot(symbol).ok_or_else(|| {
            format!("{symbol} is not a listed contract: Engine.contracts() lists the ones that trade")
        })?;
        if self.oil_futures.phase != OilPhase::Session {
            return Err(format!(
                "{symbol} is an oil future, which trades in the session only: oil moves only at a close"
            ));
        }
        let id = match order_id {
            Some(id) => {
                if id.is_empty() {
                    return Err("order_id cannot be empty".to_string());
                }
                id
            }
            None => format!("{agent}-{symbol}-{}", self.oil_futures.book.sequence),
        };
        if self.contract_order_id_taken(&id) {
            return Err(format!("order id {id:?} is already waiting in the book"));
        }
        let sequence = self.oil_futures.book.sequence;
        self.oil_futures.book.sequence += 1;
        self.oil_futures_cross_resting();
        let (fills, reference) = self.oil_futures_meet(slot, agent, &id, side, quantity, limit);
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
                self.oil_futures.book.orders.push(AgentOrder {
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

    pub(super) fn cancel_oil_future_order(&mut self, order_id: &str, agent: Option<&str>) -> bool {
        let before = self.oil_futures.book.orders.len();
        self.oil_futures.book.orders.retain(|o| !(o.id == order_id && agent.is_none_or(|a| a == o.agent)));
        self.oil_futures.book.orders.len() != before
    }

    /// A tick's step: the flow marks the price and the old mark decays, the
    /// maker re-quotes whole, and resting orders the books cross fill.
    pub(super) fn oil_futures_session_step(&mut self) {
        if !self.oil_futures_on() || self.oil_futures.phase != OilPhase::Session {
            return;
        }
        let spec = &OIL_FUTURE;
        let decay = crate::mathx::pow(0.5, 1.0 / spec.impact_half_life);
        for (slot, c) in self.oil_futures.contracts.iter_mut().enumerate() {
            let scale = spec.impact_coefficient * spec.daily_sigma / Self::oil_futures_daily_volume(slot);
            c.impact = c.impact * decay + scale * c.pending;
            if c.impact.abs() < 1e-12 {
                c.impact = 0.0;
            }
            c.pending = 0.0;
            c.taken = [0.0; 2];
        }
        self.oil_futures_cross_resting();
    }

    /// At an open: each contract expiring at this session settles on the oil
    /// price, the previous close's, its resting orders go, the next is
    /// listed, and resting orders the opening books cross fill.
    pub(super) fn oil_futures_open(&mut self) {
        if !self.oil_futures_on() || self.oil_futures.phase == OilPhase::BeforeFirstClose {
            return;
        }
        self.oil_futures.phase = OilPhase::Session;
        let t = self.elapsed_days;
        let price = self.economy.oil_price;
        let mut kept = Vec::with_capacity(self.oil_futures.contracts.len());
        for c in std::mem::take(&mut self.oil_futures.contracts) {
            if c.expiry <= t {
                self.oil_futures.settlements.push(oil_settlement(c.expiry, price));
                let symbol = oil_future_symbol(c.expiry);
                self.oil_futures.book.orders.retain(|o| o.ticker != symbol);
            } else {
                kept.push(c);
            }
        }
        for expiry in calendar::vix_future_expiries(t, calendar::OIL_FUTURES_LISTED) {
            if !kept.iter().any(|c| c.expiry == expiry) {
                kept.push(ListedOilFuture::new(expiry));
            }
        }
        kept.sort_by_key(|c| c.expiry);
        kept.truncate(calendar::OIL_FUTURES_LISTED);
        for c in kept.iter_mut() {
            c.taken = [0.0; 2];
        }
        self.oil_futures.contracts = kept;
        self.oil_futures_cross_resting();
    }

    /// At a close, after the forecast: the first close lists the next
    /// twelve, and each contract is marked.
    pub(super) fn oil_futures_close_marks(&mut self) {
        if !self.oil_futures_on() {
            return;
        }
        if self.oil_futures.phase == OilPhase::BeforeFirstClose {
            self.oil_futures.contracts = calendar::vix_future_expiries(self.elapsed_days, calendar::OIL_FUTURES_LISTED)
                .into_iter()
                .map(ListedOilFuture::new)
                .collect();
        }
        self.oil_futures.phase = OilPhase::Night;
        for slot in 0..self.oil_futures.contracts.len() {
            if let Some(v) = self.oil_futures_valuation(slot) {
                self.oil_futures.contracts[slot].mark = v.price;
            }
        }
    }

    // ── Reads ─────────────────────────────────────────────────────────────

    pub(super) fn oil_futures_contracts(&self) -> Vec<ContractSpec> {
        if !self.oil_futures_on() {
            return Vec::new();
        }
        let session = match self.oil_futures.phase {
            OilPhase::Night => self.elapsed_days + 1,
            _ => self.elapsed_days,
        };
        let front = self.oil_futures.contracts.iter().position(|c| c.expiry - ROLL_SESSIONS > session);
        let kind = ContractKind::OilFuture;
        self.oil_futures
            .contracts
            .iter()
            .enumerate()
            .map(|(k, c)| ContractSpec {
                symbol: oil_future_symbol(c.expiry),
                kind,
                root: kind.root().to_string(),
                expiry: c.expiry,
                roll: c.expiry - ROLL_SESSIONS,
                multiplier: OIL_FUTURE.multiplier,
                tick: OIL_FUTURE.tick,
                settlement: kind.settlement(),
                front: front == Some(k),
            })
            .collect()
    }

    pub(super) fn oil_futures_quote(&self, slot: usize) -> Option<Quote> {
        let c = &self.oil_futures.contracts[slot];
        let v = self.oil_futures_valuation(slot)?;
        let book = self.oil_futures_book(slot, None);
        let (bid, ask) = (book.best_bid(), book.best_ask());
        Some(Quote {
            symbol: oil_future_symbol(c.expiry),
            kind: ContractKind::OilFuture,
            expiry: c.expiry,
            bid,
            ask,
            mid: match (bid, ask) {
                (Some(b), Some(a)) => Some(0.5 * (a + b)),
                _ => None,
            },
            price: v.price,
            fair: v.expected,
            mark: if c.mark.is_finite() { Some(c.mark) } else { None },
            basis_bp: 1e4 * c.impact,
            index: self.economy.oil_price,
            rate: v.rate,
            dividends: 0.0,
            sessions_to_expiry: v.sessions,
            multiplier: OIL_FUTURE.multiplier,
            tick: OIL_FUTURE.tick,
            daily_volume: Self::oil_futures_daily_volume(slot),
            initial_margin: None,
            maintenance_margin: None,
            expected: Some(v.expected),
            premium: Some(0.0),
            loading: None,
        })
    }

    pub(super) fn oil_futures_settlements(&self, session: Option<i64>) -> Vec<Settlement> {
        if !self.oil_futures_on() {
            return Vec::new();
        }
        self.oil_futures.settlements.iter().filter(|s| session.is_none_or(|d| s.session == d)).cloned().collect()
    }

    pub(super) fn oil_futures_orders(&self) -> &[AgentOrder] {
        &self.oil_futures.book.orders
    }

    pub(super) fn take_oil_futures_fills(&mut self, agent: Option<&str>) -> Vec<AgentFill> {
        if self.oil_futures.book.fills.is_empty() {
            return Vec::new();
        }
        let (taken, kept): (Vec<_>, Vec<_>) = std::mem::take(&mut self.oil_futures.book.fills)
            .into_iter()
            .partition(|f| agent.is_none_or(|a| a == f.agent));
        self.oil_futures.book.fills = kept;
        taken
    }

    /// The oil futures' numbers for the snapshot and the hash: `None` with
    /// the switch off.
    pub fn oil_futures_words(&self) -> Option<Vec<f64>> {
        if self.oil_futures_on() {
            Some(self.oil_futures.to_words())
        } else {
            None
        }
    }

    /// Agents' resting orders on oil futures and their undelivered fills:
    /// `None` with the switch off and while no agent has traded one.
    pub fn oil_futures_book_state(&self) -> Option<&BookState> {
        if self.oil_futures_on() && !self.oil_futures.book.is_pristine() {
            Some(&self.oil_futures.book)
        } else {
            None
        }
    }

    /// Put the oil futures back (a restore), refused where the switch is
    /// off.
    pub fn set_oil_futures_state(&mut self, words: Option<&[f64]>, book: Option<BookState>) -> Result<(), String> {
        if !self.oil_futures_on() {
            if words.is_some() || book.is_some() {
                return Err("this snapshot carries the oil futures (oil_futures, oil_futures_book), which \
                            only an engine with futures_oil_listed on keeps, and this engine's model has it \
                            off"
                    .to_string());
            }
            self.oil_futures = OilFuturesState::default();
            return Ok(());
        }
        let Some(words) = words else {
            return Err("an engine with futures_oil_listed on carries its oil futures (oil_futures); this \
                        snapshot lacks them"
                .to_string());
        };
        let mut state = OilFuturesState::default();
        state.set_words(words)?;
        state.book = book.unwrap_or_default();
        self.oil_futures = state;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_words_round_trip_and_refuse_what_is_not_theirs() {
        let s = OilFuturesState {
            phase: OilPhase::Night,
            contracts: vec![
                ListedOilFuture::new(35),
                ListedOilFuture { expiry: 56, mark: 74.25, impact: 1e-4, pending: 2.0, taken: [1.0, 2.0] },
            ],
            settlements: vec![oil_settlement(14, 73.5)],
            ..Default::default()
        };
        let words = s.to_words();
        let mut back = OilFuturesState::default();
        back.set_words(&words).unwrap();
        assert_eq!(back.to_words().iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                   words.iter().map(|v| v.to_bits()).collect::<Vec<_>>());
        assert_eq!(back.settlements[0].symbol, "OIL.F0014");
        let mut short = words.clone();
        short.pop();
        assert!(OilFuturesState::default().set_words(&short).is_err());
    }
}
