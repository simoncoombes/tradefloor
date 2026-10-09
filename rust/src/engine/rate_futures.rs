//! The policy-rate and term-rate futures (`futures_rates_listed`): the
//! engine's half.
//!
//! A rate future reads the policy rate each close sets and the forecast, and
//! writes only its own state ([`RateFuturesState`]): the closes' rates its
//! periods need, its marks, its flow's mark, its book and its settlements.
//! It draws nothing. So every stock price, close and economy is the one the
//! engine prints with the switch off.
//!
//! # The contracts
//!
//! A policy-rate future (`FF`, the 30-day fed funds analogue) is on a model
//! month, the 21 sessions `21j` to `21j + 20`; a term-rate future (`TR3`,
//! the three-month SOFR analogue) on a model quarter, `63q` to `63q + 62`.
//! Each is named by its period's last session (`FF.F0020`), trades through
//! it, and settles at its close, in cash, at 100 less the period's rate: for
//! `FF` the mean of the policy rate (per cent) each of the period's closes
//! sets, for `TR3` those rates compounded a close at a time, `prod(1 + r /
//! 25200) - 1`, quoted as a simple rate over the period's sessions. The
//! month in progress and the next twelve are listed, and the quarter in
//! progress and the next seven, from the first close.
//!
//! # The price
//!
//! `F = 100 - (R + premium(h))`. `R` is the period's rate with each close
//! already made at the rate it set, and each close to come at the rate the
//! forecast expects it to set (`forecast_horizon_sessions`; past the
//! forecast's horizon its last rate; without a forecast the policy rate
//! now). `h` is the sessions from now to the period's last close, a session
//! having used `k / 390` of itself after `k` ticks, and the premium
//! [`crate::derivatives::RATE_PREMIUM`] freezes. Agents' net taker flow
//! against the house marks the price and decays. A contract holds its price
//! through a session but for the premium's roll, since the policy rate moves
//! only at a close, and trades in the session only.

use super::*;
use crate::agent_book::{remove_front, AgentFill, AgentOrder, BookState, Liquidity, RestMode, AGENT_BOOK_CAP};
use crate::derivatives::calendar::{self, SESSIONS_PER_MONTH, SESSIONS_PER_QUARTER};
use crate::derivatives::{
    rate_future_symbol, ContractKind, ContractSpec, ContractSymbol, Quote, RateFutureSpec, Settlement, SymbolKind,
    POLICY_RATE_FUTURE, RATE_PREMIUM, TERM_RATE_FUTURE,
};
use crate::market_maker::MARKET_MAKER_ID;
use crate::order_book::{OrderBook, Side, SubmitOptions};

/// The ticks in a session, the day the premium's roll divides.
const SESSION_TICKS: f64 = 390.0;

const TAKEN_MAKER_BID: usize = 0;
const TAKEN_MAKER_ASK: usize = 1;

/// Where the rate futures stand in the day.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum RatePhase {
    BeforeFirstClose,
    Session,
    Night,
}

impl RatePhase {
    fn code(self) -> f64 {
        match self {
            RatePhase::BeforeFirstClose => 0.0,
            RatePhase::Session => 1.0,
            RatePhase::Night => 2.0,
        }
    }

    fn from_code(v: f64) -> Option<Self> {
        if v == 0.0 {
            Some(RatePhase::BeforeFirstClose)
        } else if v == 1.0 {
            Some(RatePhase::Session)
        } else if v == 2.0 {
            Some(RatePhase::Night)
        } else {
            None
        }
    }
}

/// The two families, and what each needs.
fn kind_code(kind: ContractKind) -> f64 {
    match kind {
        ContractKind::TermRateFuture => 1.0,
        _ => 0.0,
    }
}

fn kind_of(code: f64) -> Option<ContractKind> {
    if code == 0.0 {
        Some(ContractKind::PolicyRateFuture)
    } else if code == 1.0 {
        Some(ContractKind::TermRateFuture)
    } else {
        None
    }
}

/// A family's period, in sessions.
fn period(kind: ContractKind) -> i64 {
    match kind {
        ContractKind::TermRateFuture => SESSIONS_PER_QUARTER,
        _ => SESSIONS_PER_MONTH,
    }
}

fn spec_of(kind: ContractKind) -> &'static RateFutureSpec {
    match kind {
        ContractKind::TermRateFuture => &TERM_RATE_FUTURE,
        _ => &POLICY_RATE_FUTURE,
    }
}

/// The rate of a period whose closes set `rates`, per cent: their mean for a
/// policy-rate future, compounded and quoted simply for a term-rate future.
fn period_rate(kind: ContractKind, rates: &[f64]) -> f64 {
    let n = rates.len() as f64;
    match kind {
        ContractKind::TermRateFuture => {
            let mut growth = 1.0;
            for r in rates {
                growth *= 1.0 + r / 25_200.0;
            }
            (growth - 1.0) * 25_200.0 / n
        }
        _ => rates.iter().sum::<f64>() / n,
    }
}

/// One listed rate future.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ListedRateFuture {
    pub(crate) kind: ContractKind,
    /// The period's last session.
    pub(crate) expiry: i64,
    pub(crate) mark: f64,
    /// Agents' flow's mark on the price, price points.
    pub(crate) impact: f64,
    pub(crate) pending: f64,
    pub(crate) taken: [f64; 2],
}

impl ListedRateFuture {
    fn new(kind: ContractKind, expiry: i64) -> Self {
        ListedRateFuture { kind, expiry, mark: f64::NAN, impact: 0.0, pending: 0.0, taken: [0.0; 2] }
    }
}

const CONTRACT_WIDTH: usize = 7;

/// Everything the rate futures carry.
#[derive(Debug, Clone)]
pub(crate) struct RateFuturesState {
    pub(crate) phase: RatePhase,
    /// The policy rate each of the last quarter's closes set, per cent, as
    /// (session, rate), oldest first: what a period in progress has realised.
    pub(crate) closes: Vec<(i64, f64)>,
    pub(crate) contracts: Vec<ListedRateFuture>,
    pub(crate) settlements: Vec<Settlement>,
    pub(crate) book: BookState,
}

impl Default for RateFuturesState {
    fn default() -> Self {
        RateFuturesState {
            phase: RatePhase::BeforeFirstClose,
            closes: Vec::new(),
            contracts: Vec::new(),
            settlements: Vec::new(),
            book: BookState::default(),
        }
    }
}

impl RateFuturesState {
    /// The numbers the snapshot's `rate_futures` key and the state hash
    /// carry: the phase; the close count and each close's session and rate;
    /// the contract count and each contract's kind (0 `FF`, 1 `TR3`),
    /// expiry, mark, impact, pending flow and two taken counts; then the
    /// settlement count and each settlement's kind, session and value.
    pub(crate) fn to_words(&self) -> Vec<f64> {
        let mut out = Vec::with_capacity(
            4 + 2 * self.closes.len() + CONTRACT_WIDTH * self.contracts.len() + 3 * self.settlements.len());
        out.push(self.phase.code());
        out.push(self.closes.len() as f64);
        for (s, r) in &self.closes {
            out.push(*s as f64);
            out.push(*r);
        }
        out.push(self.contracts.len() as f64);
        for c in &self.contracts {
            out.push(kind_code(c.kind));
            out.push(c.expiry as f64);
            out.push(c.mark);
            out.push(c.impact);
            out.push(c.pending);
            out.extend_from_slice(&c.taken);
        }
        out.push(self.settlements.len() as f64);
        for s in &self.settlements {
            out.push(kind_code(s.kind));
            out.push(s.session as f64);
            out.push(s.value);
        }
        out
    }

    /// Put back what [`RateFuturesState::to_words`] wrote, refusing a list
    /// that is not one. The book is carried apart.
    pub(crate) fn set_words(&mut self, words: &[f64]) -> Result<(), String> {
        let bad = |why: &str| format!("this snapshot's rate_futures do not parse: {why}");
        let whole = |v: f64, what: &str| -> Result<usize, String> {
            if v.is_finite() && v >= 0.0 && v.fract() == 0.0 && v <= 1e9 {
                Ok(v as usize)
            } else {
                Err(bad(&format!("{what} is {v}")))
            }
        };
        let session = |v: f64| -> Result<i64, String> {
            if v.is_finite() && v.fract() == 0.0 {
                Ok(v as i64)
            } else {
                Err(bad(&format!("a session is {v}")))
            }
        };
        let at_least = |at: usize, n: usize| -> Result<(), String> {
            if words.len() < at + n {
                Err(bad("it is shorter than its counts say"))
            } else {
                Ok(())
            }
        };
        at_least(0, 2)?;
        let phase = RatePhase::from_code(words[0]).ok_or_else(|| bad(&format!("the phase is {}", words[0])))?;
        let n = whole(words[1], "the close count")?;
        let mut at = 2;
        at_least(at, 2 * n + 1)?;
        let mut closes = Vec::with_capacity(n);
        for _ in 0..n {
            if !words[at + 1].is_finite() {
                return Err(bad("a close's rate is not finite"));
            }
            closes.push((session(words[at])?, words[at + 1]));
            at += 2;
        }
        let n = whole(words[at], "the contract count")?;
        at += 1;
        at_least(at, CONTRACT_WIDTH * n + 1)?;
        let mut contracts = Vec::with_capacity(n);
        for _ in 0..n {
            let w = &words[at..at + CONTRACT_WIDTH];
            let kind = kind_of(w[0]).ok_or_else(|| bad(&format!("a contract's kind is {}", w[0])))?;
            if !(w[2].is_nan() || w[2].is_finite()) || w[3..].iter().any(|v| !v.is_finite()) {
                return Err(bad("a contract's impact, flow or taken depth is not finite"));
            }
            contracts.push(ListedRateFuture {
                kind,
                expiry: session(w[1])?,
                mark: w[2],
                impact: w[3],
                pending: w[4],
                taken: [w[5], w[6]],
            });
            at += CONTRACT_WIDTH;
        }
        let m = whole(words[at], "the settlement count")?;
        at += 1;
        if words.len() != at + 3 * m {
            return Err(bad(&format!("{} numbers do not match its counts", words.len())));
        }
        let mut settlements = Vec::with_capacity(m);
        for k in 0..m {
            let w = &words[at + 3 * k..at + 3 * k + 3];
            let kind = kind_of(w[0]).ok_or_else(|| bad(&format!("a settlement's kind is {}", w[0])))?;
            if !w[2].is_finite() {
                return Err(bad("a settlement's value is not finite"));
            }
            settlements.push(rate_settlement(kind, session(w[1])?, w[2]));
        }
        self.phase = phase;
        self.closes = closes;
        self.contracts = contracts;
        self.settlements = settlements;
        Ok(())
    }
}

/// A rate future settled at the close of `session` at `price`, 100 less the
/// period's realised rate.
fn rate_settlement(kind: ContractKind, session: i64, price: f64) -> Settlement {
    Settlement {
        symbol: rate_future_symbol(kind, session),
        kind,
        root: kind.root().to_string(),
        session,
        value: price,
        reference: price,
    }
}

/// What a rate future is worth now, and from what.
#[derive(Debug, Clone, Copy)]
pub(crate) struct RateValuation {
    /// The period's rate, realised and expected, per cent.
    pub(crate) expected: f64,
    /// The premium, rate points.
    pub(crate) premium: f64,
    pub(crate) fair: f64,
    /// Sessions from now to the period's last close.
    pub(crate) sessions: f64,
    pub(crate) price: f64,
}

impl Engine {
    // ── State ─────────────────────────────────────────────────────────────

    pub(super) fn rate_futures_on(&self) -> bool {
        self.params.futures_rates_listed != 0.0
    }

    /// The listed rate future named `symbol`, by slot.
    pub(super) fn rate_futures_slot(&self, symbol: &str) -> Option<usize> {
        let parsed = ContractSymbol::parse(symbol).ok()?;
        if parsed.kind != SymbolKind::Future {
            return None;
        }
        self.rate_futures.contracts.iter().position(|c| c.kind.root() == parsed.root && c.expiry == parsed.expiry)
    }

    /// The session of the last close the rate futures recorded.
    fn rate_futures_closed(&self) -> Option<i64> {
        self.rate_futures.closes.last().map(|(s, _)| *s)
    }

    // ── Pricing ───────────────────────────────────────────────────────────

    /// The policy rate the close of session `k` is expected to set, per
    /// cent: the forecast's path after the last close, its last rate past its
    /// horizon, the policy rate now without a forecast.
    fn rate_expected_at(&self, k: i64) -> f64 {
        let now = self.economy.federal_funds_rate;
        let Some(f) = self.forecast() else {
            return now;
        };
        // The forecast is taken at a close for the next session, `f.day`; the
        // close `k` is `k - (f.day - 1)` closes after the one it was taken at.
        let ahead = k - (f.day - 1);
        if ahead <= 0 || f.policy_rate.is_empty() {
            return now;
        }
        let h = crate::mathx::min(ahead as f64, f.policy_rate.len() as f64) as usize;
        f.policy_rate[h - 1]
    }

    /// The rate of the period ending at `expiry` for a future of `kind`, per
    /// cent, with each close made at its rate and each to come expected.
    fn rate_futures_period_rate(&self, kind: ContractKind, expiry: i64) -> f64 {
        let length = period(kind);
        let closed = self.rate_futures_closed().unwrap_or(i64::MIN);
        let rates: Vec<f64> = (expiry - length + 1..=expiry)
            .map(|k| {
                if k <= closed {
                    self.rate_futures
                        .closes
                        .iter()
                        .find(|(s, _)| *s == k)
                        .map(|(_, r)| *r)
                        .unwrap_or(self.economy.federal_funds_rate)
                } else {
                    self.rate_expected_at(k)
                }
            })
            .collect();
        period_rate(kind, &rates)
    }

    /// What the contract in `slot` is worth now.
    pub(crate) fn rate_futures_valuation(&self, slot: usize) -> Option<RateValuation> {
        let c = &self.rate_futures.contracts[slot];
        let closed = self.rate_futures_closed()?;
        let used = match self.rate_futures.phase {
            RatePhase::Session => crate::mathx::min(1.0, f64::from(self.ticks_today()) / SESSION_TICKS),
            _ => 0.0,
        };
        let sessions = crate::mathx::max(0.0, (c.expiry - closed) as f64 - used);
        let expected = self.rate_futures_period_rate(c.kind, c.expiry);
        let premium = RATE_PREMIUM.at(sessions);
        let fair = 100.0 - expected - premium;
        Some(RateValuation { expected, premium, fair, sessions, price: fair + c.impact })
    }

    fn rate_futures_daily_volume(&self, slot: usize) -> f64 {
        let c = &self.rate_futures.contracts[slot];
        let spec = spec_of(c.kind);
        let k = self.rate_futures.contracts[..slot].iter().filter(|o| o.kind == c.kind).count();
        spec.daily_volume[crate::mathx::min(k as f64, (spec.daily_volume.len() - 1) as f64) as usize]
    }

    // ── The book ──────────────────────────────────────────────────────────

    /// The book of the contract in `slot`, leaving out one agent's orders:
    /// the maker's ladder around the price, less what agents took from its
    /// front, and every agent's resting orders.
    fn rate_futures_book(&self, slot: usize, exclude: Option<&str>) -> OrderBook {
        let c = &self.rate_futures.contracts[slot];
        let spec = spec_of(c.kind);
        let symbol = rate_future_symbol(c.kind, c.expiry);
        let price = self.rate_futures_valuation(slot).map(|v| v.price).unwrap_or(f64::NAN);
        let mut book = OrderBook::new(symbol.clone(), if price.is_finite() { Some(price) } else { None })
            .with_cap(AGENT_BOOK_CAP);
        if !(price.is_finite() && price > spec.tick) {
            return book;
        }
        let volume = self.rate_futures_daily_volume(slot);
        let size = crate::mathx::max(1.0, (volume * spec.maker_level_share).floor());
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
        for o in &self.rate_futures.book.orders {
            if o.ticker != symbol || exclude == Some(o.agent.as_str()) {
                continue;
            }
            book.post_limit(o.side, o.limit, o.remaining, &o.agent, Some(o.id.clone()));
        }
        book
    }

    /// The book of the listed rate future in `slot`.
    pub(super) fn rate_futures_book_at(&self, slot: usize) -> Option<OrderBook> {
        if !self.rate_futures_on() || slot >= self.rate_futures.contracts.len() {
            return None;
        }
        Some(self.rate_futures_book(slot, None))
    }

    fn rate_futures_push_fill(&mut self, mut fill: AgentFill) -> AgentFill {
        fill.sequence = self.rate_futures.book.fill_sequence;
        self.rate_futures.book.fill_sequence += 1;
        self.rate_futures.book.fills.push(fill.clone());
        fill
    }

    fn rate_futures_reduce_order(&mut self, order_id: &str, quantity: f64) {
        if let Some(o) = self.rate_futures.book.orders.iter_mut().find(|o| o.id == order_id) {
            o.remaining -= quantity;
        }
        self.rate_futures.book.orders.retain(|o| o.remaining > 1e-9);
    }

    /// An order meets a contract's book, as an index future's does.
    #[allow(clippy::too_many_arguments)]
    fn rate_futures_meet(
        &mut self,
        slot: usize,
        agent: &str,
        order_id: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
    ) -> (Vec<AgentFill>, f64) {
        let mut book = self.rate_futures_book(slot, Some(agent));
        let symbol = book.company_id.clone();
        let reference = book
            .mid_price()
            .or_else(|| self.rate_futures_valuation(slot).map(|v| v.price))
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
                let c = &mut self.rate_futures.contracts[slot];
                c.taken[if side == Side::Buy { TAKEN_MAKER_ASK } else { TAKEN_MAKER_BID }] += f.quantity;
                c.pending += match side {
                    Side::Buy => f.quantity,
                    Side::Sell => -f.quantity,
                };
            } else {
                self.rate_futures_reduce_order(&f.maker_order_id, f.quantity);
                self.rate_futures_push_fill(AgentFill {
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
            taker.push(self.rate_futures_push_fill(AgentFill {
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

    /// Match every resting order the rate futures' books now cross.
    fn rate_futures_cross_resting(&mut self) {
        if self.rate_futures.book.orders.is_empty() || self.rate_futures.phase != RatePhase::Session {
            return;
        }
        let ids: Vec<String> = self.rate_futures.book.orders.iter().map(|o| o.id.clone()).collect();
        for id in ids {
            let Some(o) = self.rate_futures.book.orders.iter().find(|o| o.id == id).cloned() else {
                continue;
            };
            let Some(slot) = self.rate_futures_slot(&o.ticker) else {
                continue;
            };
            let book = self.rate_futures_book(slot, Some(&o.agent));
            let crossed = match o.side {
                Side::Buy => book.best_ask().is_some_and(|a| o.limit >= a),
                Side::Sell => book.best_bid().is_some_and(|b| o.limit <= b),
            };
            if !crossed {
                continue;
            }
            let (fills, _) = self.rate_futures_meet(slot, &o.agent, &o.id, o.side, o.remaining, Some(o.limit));
            let filled: f64 = fills.iter().map(|f| f.quantity).sum();
            self.rate_futures_reduce_order(&o.id, filled);
        }
    }

    /// An agent's order on a listed rate future: as on an index future, in
    /// the session only.
    pub(super) fn submit_rate_future_order(
        &mut self,
        agent: &str,
        symbol: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
        order_id: Option<String>,
    ) -> Result<OrderReport, String> {
        let slot = self.rate_futures_slot(symbol).ok_or_else(|| {
            format!("{symbol} is not a listed contract: Engine.contracts() lists the ones that trade")
        })?;
        if self.rate_futures.phase != RatePhase::Session {
            return Err(format!(
                "{symbol} is a rate future, which trades in the session only: the policy rate moves \
                 only at a close"
            ));
        }
        let id = match order_id {
            Some(id) => {
                if id.is_empty() {
                    return Err("order_id cannot be empty".to_string());
                }
                id
            }
            None => format!("{agent}-{symbol}-{}", self.rate_futures.book.sequence),
        };
        if self.contract_order_id_taken(&id) {
            return Err(format!("order id {id:?} is already waiting in the book"));
        }
        let sequence = self.rate_futures.book.sequence;
        self.rate_futures.book.sequence += 1;
        self.rate_futures_cross_resting();
        let (fills, reference) = self.rate_futures_meet(slot, agent, &id, side, quantity, limit);
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
                self.rate_futures.book.orders.push(AgentOrder {
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

    /// Cancel a resting order on a rate future.
    pub(super) fn cancel_rate_future_order(&mut self, order_id: &str, agent: Option<&str>) -> bool {
        let before = self.rate_futures.book.orders.len();
        self.rate_futures.book.orders.retain(|o| !(o.id == order_id && agent.is_none_or(|a| a == o.agent)));
        self.rate_futures.book.orders.len() != before
    }

    // ── Steps and the day's boundaries ────────────────────────────────────

    /// A tick's step: the flow since the last step marks the price and the
    /// old mark decays, the maker re-quotes whole, and resting orders the
    /// moved books cross fill. Nothing with the switch off.
    pub(super) fn rate_futures_session_step(&mut self) {
        if !self.rate_futures_on() || self.rate_futures.phase != RatePhase::Session {
            return;
        }
        let volumes: Vec<f64> = (0..self.rate_futures.contracts.len()).map(|k| self.rate_futures_daily_volume(k)).collect();
        for (c, volume) in self.rate_futures.contracts.iter_mut().zip(volumes) {
            let spec = spec_of(c.kind);
            let decay = crate::mathx::pow(0.5, 1.0 / spec.impact_half_life);
            c.impact = c.impact * decay + spec.impact_coefficient * spec.daily_sigma / volume * c.pending;
            if c.impact.abs() < 1e-12 {
                c.impact = 0.0;
            }
            c.pending = 0.0;
            c.taken = [0.0; 2];
        }
        self.rate_futures_cross_resting();
    }

    /// At an open: the session begins. Nothing before the first close or
    /// with the switch off.
    pub(super) fn rate_futures_open(&mut self) {
        if !self.rate_futures_on() || self.rate_futures.phase == RatePhase::BeforeFirstClose {
            return;
        }
        self.rate_futures.phase = RatePhase::Session;
        for c in self.rate_futures.contracts.iter_mut() {
            c.taken = [0.0; 2];
        }
        self.rate_futures_cross_resting();
    }

    /// At a close, after the macro step and the forecast: the policy rate
    /// the close set is recorded, each contract whose period ends at this
    /// session settles on its period's closes, the next are listed, and each
    /// is marked. Nothing with the switch off.
    pub(super) fn rate_futures_close_marks(&mut self) {
        if !self.rate_futures_on() {
            return;
        }
        let s = self.elapsed_days;
        let rate = self.economy.federal_funds_rate;
        self.rate_futures.closes.retain(|(k, _)| *k != s);
        self.rate_futures.closes.push((s, rate));
        let keep = SESSIONS_PER_QUARTER as usize;
        if self.rate_futures.closes.len() > keep {
            let cut = self.rate_futures.closes.len() - keep;
            self.rate_futures.closes.drain(..cut);
        }
        self.rate_futures.phase = RatePhase::Night;
        let mut kept = Vec::with_capacity(self.rate_futures.contracts.len());
        for c in std::mem::take(&mut self.rate_futures.contracts) {
            if c.expiry <= s {
                let price = 100.0 - self.rate_futures_period_rate(c.kind, c.expiry);
                self.rate_futures.settlements.push(rate_settlement(c.kind, c.expiry, price));
                let symbol = rate_future_symbol(c.kind, c.expiry);
                self.rate_futures.book.orders.retain(|o| o.ticker != symbol);
            } else {
                kept.push(c);
            }
        }
        for (kind, count) in [
            (ContractKind::PolicyRateFuture, calendar::POLICY_RATE_FUTURES_LISTED),
            (ContractKind::TermRateFuture, calendar::TERM_RATE_FUTURES_LISTED),
        ] {
            for expiry in calendar::period_ends(s + 1, period(kind), count) {
                if !kept.iter().any(|c| c.kind == kind && c.expiry == expiry) {
                    kept.push(ListedRateFuture::new(kind, expiry));
                }
            }
        }
        kept.sort_by_key(|c| (kind_code(c.kind) as i64, c.expiry));
        self.rate_futures.contracts = kept;
        for slot in 0..self.rate_futures.contracts.len() {
            if let Some(v) = self.rate_futures_valuation(slot) {
                self.rate_futures.contracts[slot].mark = v.price;
            }
        }
    }

    // ── Reads ─────────────────────────────────────────────────────────────

    /// The listed rate futures: the policy-rate futures, then the term-rate
    /// futures, each in expiry order. The front of each is its first: the
    /// period in progress.
    pub(super) fn rate_futures_contracts(&self) -> Vec<ContractSpec> {
        if !self.rate_futures_on() {
            return Vec::new();
        }
        self.rate_futures
            .contracts
            .iter()
            .enumerate()
            .map(|(k, c)| {
                let spec = spec_of(c.kind);
                ContractSpec {
                    symbol: rate_future_symbol(c.kind, c.expiry),
                    kind: c.kind,
                    root: c.kind.root().to_string(),
                    expiry: c.expiry,
                    roll: c.expiry,
                    multiplier: spec.multiplier,
                    tick: spec.tick,
                    settlement: c.kind.settlement(),
                    front: k == 0 || self.rate_futures.contracts[k - 1].kind != c.kind,
                }
            })
            .collect()
    }

    /// A listed rate future's quote now.
    pub(super) fn rate_futures_quote(&self, slot: usize) -> Option<Quote> {
        let c = &self.rate_futures.contracts[slot];
        let spec = spec_of(c.kind);
        let v = self.rate_futures_valuation(slot)?;
        let book = self.rate_futures_book(slot, None);
        let (bid, ask) = (book.best_bid(), book.best_ask());
        Some(Quote {
            symbol: rate_future_symbol(c.kind, c.expiry),
            kind: c.kind,
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
            basis_bp: -100.0 * c.impact,
            index: self.economy.federal_funds_rate,
            rate: v.expected / 100.0,
            dividends: 0.0,
            sessions_to_expiry: v.sessions,
            multiplier: spec.multiplier,
            tick: spec.tick,
            daily_volume: self.rate_futures_daily_volume(slot),
            initial_margin: None,
            expected: Some(v.expected),
            premium: Some(v.premium),
            loading: None,
        })
    }

    /// The rate futures' final settlements at `session`'s close, or all.
    pub(super) fn rate_futures_settlements(&self, session: Option<i64>) -> Vec<Settlement> {
        if !self.rate_futures_on() {
            return Vec::new();
        }
        self.rate_futures.settlements.iter().filter(|s| session.is_none_or(|d| s.session == d)).cloned().collect()
    }

    pub(super) fn rate_futures_orders(&self) -> &[AgentOrder] {
        &self.rate_futures.book.orders
    }

    pub(super) fn take_rate_futures_fills(&mut self, agent: Option<&str>) -> Vec<AgentFill> {
        if self.rate_futures.book.fills.is_empty() {
            return Vec::new();
        }
        let (taken, kept): (Vec<_>, Vec<_>) = std::mem::take(&mut self.rate_futures.book.fills)
            .into_iter()
            .partition(|f| agent.is_none_or(|a| a == f.agent));
        self.rate_futures.book.fills = kept;
        taken
    }

    // ── Snapshot ──────────────────────────────────────────────────────────

    /// The rate futures' numbers for the snapshot and the hash: `None` with
    /// the switch off.
    pub fn rate_futures_words(&self) -> Option<Vec<f64>> {
        if self.rate_futures_on() {
            Some(self.rate_futures.to_words())
        } else {
            None
        }
    }

    /// Agents' resting orders on rate futures and their undelivered fills:
    /// `None` with the switch off and while no agent has traded one.
    pub fn rate_futures_book_state(&self) -> Option<&BookState> {
        if self.rate_futures_on() && !self.rate_futures.book.is_pristine() {
            Some(&self.rate_futures.book)
        } else {
            None
        }
    }

    /// Put the rate futures back (a restore), refused where the switch is
    /// off.
    pub fn set_rate_futures_state(&mut self, words: Option<&[f64]>, book: Option<BookState>) -> Result<(), String> {
        if !self.rate_futures_on() {
            if words.is_some() || book.is_some() {
                return Err("this snapshot carries the rate futures (rate_futures, rate_futures_book), \
                            which only an engine with futures_rates_listed on keeps, and this engine's model \
                            has it off"
                    .to_string());
            }
            self.rate_futures = RateFuturesState::default();
            return Ok(());
        }
        let Some(words) = words else {
            return Err("an engine with futures_rates_listed on carries its rate futures (rate_futures); \
                        this snapshot lacks them"
                .to_string());
        };
        let mut state = RateFuturesState::default();
        state.set_words(words)?;
        state.book = book.unwrap_or_default();
        self.rate_futures = state;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_months_rate_is_the_mean_and_a_quarters_compounds() {
        assert_eq!(period_rate(ContractKind::PolicyRateFuture, &[4.0, 5.0, 6.0]), 5.0);
        let flat = period_rate(ContractKind::TermRateFuture, &[5.0; 63]);
        // Compounding a flat 5 per cent a close at a time over a quarter
        // reads a touch above it: ((1 + 5 / 25200)^63 - 1) * 25200 / 63.
        let closed = (crate::mathx::pow(1.0 + 5.0 / 25_200.0, 63.0) - 1.0) * 25_200.0 / 63.0;
        assert!((flat - closed).abs() < 1e-12, "{flat} {closed}");
        assert!(flat > 5.03 && flat < 5.032, "{flat}");
    }

    #[test]
    fn the_premium_reads_its_fit_and_holds_past_six_months() {
        let p = RATE_PREMIUM;
        assert_eq!(p.at(0.0), 0.0);
        assert!((p.at(21.0) * 100.0 - 1.5956).abs() < 1e-12);
        assert!((p.at(126.0) * 100.0 - 32.55).abs() < 0.01, "{}", p.at(126.0) * 100.0);
        assert_eq!(p.at(500.0), p.at(126.0));
    }

    #[test]
    fn the_words_round_trip_and_refuse_what_is_not_theirs() {
        let s = RateFuturesState {
            phase: RatePhase::Night,
            closes: vec![(40, 4.25), (41, 4.5)],
            contracts: vec![
                ListedRateFuture::new(ContractKind::PolicyRateFuture, 62),
                ListedRateFuture { kind: ContractKind::TermRateFuture, expiry: 125, mark: 95.5, impact: 0.001, pending: 3.0, taken: [1.0, 2.0] },
            ],
            settlements: vec![rate_settlement(ContractKind::PolicyRateFuture, 41, 95.6)],
            ..Default::default()
        };
        let words = s.to_words();
        let mut back = RateFuturesState::default();
        back.set_words(&words).unwrap();
        assert_eq!(back.to_words().iter().map(|v| v.to_bits()).collect::<Vec<_>>(),
                   words.iter().map(|v| v.to_bits()).collect::<Vec<_>>());
        assert_eq!(back.settlements[0].symbol, "FF.F0041");
        assert_eq!(back.contracts[1].kind, ContractKind::TermRateFuture);
        let mut short = words.clone();
        short.pop();
        assert!(RateFuturesState::default().set_words(&short).is_err());
    }
}
