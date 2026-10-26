//! The single-name options (`options_names_listed`): the engine's half.
//!
//! American calls and puts on every public roster name, 100 shares a
//! contract, physically settled. Each name's surface is built when asked
//! from public state: the forecast's expected variance of the name to each
//! tenor (`forecast_horizon_sessions`), scaled by the single-name premium
//! (`surface_name_premium`), with the index surface's term premia and the
//! variance of the name's own reports before the tenor
//! (`surface_earnings_weight`, the calendar the engine publishes); its
//! smile has the index surface's curvature and a share of its correlation
//! (`surface_name_skew`). It never reads the cycle's true phase, a crisis,
//! a pending surprise or a draw.
//!
//! # The chain
//!
//! From the first close, every public name lists calls and puts at the next
//! three monthly expiries and the three quarterlies after them
//! (`derivatives::calendar::name_option_expiries`), the index options'
//! sessions. A name option settles at its expiry session's close, so it
//! trades through its last session. An expiry's strikes sit on the name's
//! price tier when it lists ([`crate::derivatives::NameOptionSpec`]), ten
//! either side of the price, widened at each open and close, so a symbol
//! listed once stays listed to its expiry. A name whose ticker is a
//! contract family's root (`IDX`, `VIX`, `FF`, `TR3`, `OIL`) lists none.
//!
//! # The price
//!
//! An option expiring at the close of session `E` has `E + 1 - now`
//! sessions left, `now` counting a session that has run `k` of its 390
//! ticks as `k / 390` used and a close as the next session's start. The
//! dealer quotes the American value a Leisen-Reimer tree gives
//! (`derivatives::american`) on the escrowed price: the name's price less
//! the present value of the dividends going ex before expiry, the declared
//! amount inside the declaration lead and the rule's projection beyond
//! (`market::dividends::lookahead`), each at its ex-date's eve. The tree's
//! volatility is the surface's at the strike's log-moneyness on the
//! escrowed forward, moved by the dealer's pressure on the expiry, and its
//! rate the policy rate the forecast expects to expiry. A tree is priced
//! only when a quote is asked for, and kept for the session: a chain is
//! about 250 trees a name, and an untraded run prices none.
//!
//! # Exercise and delivery
//!
//! The dealer is the other side of every trade. At each close:
//!
//! - an agent's instructions ([`Engine::exercise`]) are exercised, up to
//!   the long position it still holds;
//! - the dealer exercises its net long in a contract where its own tree
//!   says exercising beats holding (`american::early_exercise`): a call only
//!   on the eve of an ex-date whose dividend is worth more than the call's
//!   time value, a put when it is deep enough that the interest on the
//!   strike outweighs the insurance left;
//! - at an expiry session's close every option in the money by a cent or
//!   more on the closing print is exercised, the OCC's exercise by
//!   exception;
//! - the contracts exercised are assigned to every short position, agents'
//!   and the dealer's, pro rata: in whole contracts by largest remainder
//!   when every position is whole, in exact shares otherwise.
//!
//! Each exercise and assignment is a row of [`Engine::settlements`]: the
//! party, the contracts, the shares it receives or delivers (100 a
//! contract) and the cash, the strike times the shares. The dealer's rows
//! are among them, so a close's rows sum to no shares and no cash. Nothing
//! trades in the stock's book: a delivery moves no price, sends no flow and
//! takes no draw.

use super::options::{strike_at, Clock, ListedExpiry, OptionsPhase, SURFACE_MIN_KNOTS};
use super::*;
use crate::agent_book::{AgentFill, AgentOrder, BookState, Liquidity, RestMode, AGENT_BOOK_CAP};
use crate::derivatives::american::{self, CashDividend, Lattice, OptionSpec, Style};
use crate::derivatives::calendar;
use crate::derivatives::dealer::{
    american_ladder, dealer_thetas, moneyness_factor, pressure_after, price_ladder, read_smile, walk, ExpirySummary, Ladder,
    OptionQuote,
};
use crate::derivatives::pricing::{black_greeks, intrinsic, Greeks};
use crate::derivatives::surface::{term_shape, Ssvi, Surface, TermInputs, ThetaCurve};
use crate::derivatives::{
    name_option_symbol, ContractKind, ContractSpec, ContractSymbol, Right, Settlement, SettlementEvent, SymbolKind,
    NAME_OPTION, NAME_OPTION_DEALER,
};
use crate::market_maker::MARKET_MAKER_ID;
use crate::order_book::{OrderBook, Side};

/// Open ticks a session (`option_name_inventory_half_life` counts sessions).
const SESSION_TICKS: f64 = 390.0;

/// The contract families' roots: a name with one of these tickers lists no
/// options, so a symbol names one contract.
const RESERVED_ROOTS: [&str; 5] = ["IDX", "VIX", "FF", "TR3", "OIL"];

/// An exercise instruction as the snapshot and the reads carry it:
/// `(agent, symbol, contracts)`.
pub type InstructionRow = (String, String, f64);

/// A held option the margin scans: its expiry's slot in its chain, right,
/// strike and signed contracts.
type HeldOption = (usize, Right, f64, f64);

/// A tree's cache is emptied when it holds this many entries.
const TREE_CACHE_CAP: usize = 50_000;

/// One name's listed expiries.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct NameChain {
    /// The name's roster slot.
    pub(crate) slot: usize,
    /// In expiry order.
    pub(crate) expiries: Vec<ListedExpiry>,
}

/// One name's settled expiry: the closing print it settled on, and its
/// strikes.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct SettledName {
    pub(crate) slot: usize,
    pub(crate) expiry: i64,
    pub(crate) reference: f64,
    pub(crate) step: f64,
    pub(crate) lo: i64,
    pub(crate) hi: i64,
}

/// An agent's instruction to exercise, waiting for the close.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct Instruction {
    pub(crate) agent: String,
    pub(crate) symbol: String,
    pub(crate) quantity: f64,
}

/// What a tree said.
#[derive(Debug, Clone, Copy, PartialEq)]
struct TreeRead {
    value: f64,
    delta: f64,
    gamma: f64,
}

/// Trees priced this session, by their inputs' bits: a quote, the order
/// that follows it and the margin on it read one tree. Emptied at each open
/// and close; a copy of the engine starts it empty, since it holds nothing
/// a tree would not give again.
#[derive(Debug, Default)]
pub(crate) struct TreeCache(std::sync::Mutex<std::collections::HashMap<Vec<u64>, TreeRead>>);

impl Clone for TreeCache {
    fn clone(&self) -> Self {
        TreeCache::default()
    }
}

impl TreeCache {
    fn clear(&self) {
        if let Ok(mut guard) = self.0.lock() {
            guard.clear();
        }
    }
}

/// Everything the single-name options carry.
#[derive(Debug, Clone, Default)]
pub(crate) struct NameOptionsState {
    /// The listed names, in roster order.
    pub(crate) chains: Vec<NameChain>,
    /// Every settled expiry, in order.
    pub(crate) settled: Vec<SettledName>,
    /// Agents' waiting orders and undelivered fills; its `flow` holds each
    /// agent's contracts bought and sold of each listed option, an exercise
    /// counting as a sale and an assignment as a purchase, so `bought -
    /// sold` is the position.
    pub(crate) book: BookState,
    /// Exercise instructions waiting for the close, in arrival order.
    pub(crate) instructions: Vec<Instruction>,
    /// Every exercise and assignment so far, and the cash settlement of a
    /// delisted name's options, in order.
    pub(crate) events: Vec<Settlement>,
    pub(crate) cache: TreeCache,
}

const EXPIRY_WIDTH: usize = 5;
const SETTLED_WIDTH: usize = 6;

impl NameOptionsState {
    /// The numbers the snapshot's `name_options` key and the state hash
    /// carry: the chain count, each chain's slot and expiry count and each
    /// expiry's session, strike step, strike range and pressure, then the
    /// settled count and each settled expiry's slot, session, reference,
    /// step and range.
    pub(crate) fn to_words(&self) -> Vec<f64> {
        let mut out = vec![self.chains.len() as f64];
        for c in &self.chains {
            out.push(c.slot as f64);
            out.push(c.expiries.len() as f64);
            for e in &c.expiries {
                out.extend_from_slice(&[e.expiry as f64, e.step, e.lo as f64, e.hi as f64, e.pressure]);
            }
        }
        out.push(self.settled.len() as f64);
        for s in &self.settled {
            out.extend_from_slice(&[s.slot as f64, s.expiry as f64, s.reference, s.step, s.lo as f64, s.hi as f64]);
        }
        out
    }

    /// Put back what [`NameOptionsState::to_words`] wrote, refusing a list
    /// that is not one. The book and the exercises are carried apart.
    pub(crate) fn set_words(&mut self, words: &[f64]) -> Result<(), String> {
        let bad = |why: &str| format!("this snapshot's name_options do not parse: {why}");
        let whole = |v: f64, what: &str| -> Result<i64, String> {
            if v.is_finite() && v.fract() == 0.0 && v.abs() <= 1e15 {
                Ok(v as i64)
            } else {
                Err(bad(&format!("{what} is {v}")))
            }
        };
        let count = |v: f64, what: &str| -> Result<usize, String> {
            let n = whole(v, what)?;
            if n < 0 {
                Err(bad(&format!("{what} is negative")))
            } else {
                Ok(n as usize)
            }
        };
        let mut at = 0;
        let mut take = |n: usize| -> Result<&[f64], String> {
            if at + n > words.len() {
                return Err(bad("it is shorter than its counts say"));
            }
            let s = &words[at..at + n];
            at += n;
            Ok(s)
        };
        let chains_n = count(take(1)?[0], "the chain count")?;
        let mut chains = Vec::with_capacity(chains_n);
        for _ in 0..chains_n {
            let head = take(2)?;
            let slot = count(head[0], "a slot")?;
            let n = count(head[1], "an expiry count")?;
            let mut expiries = Vec::with_capacity(n);
            for _ in 0..n {
                let w = take(EXPIRY_WIDTH)?;
                let (expiry, lo, hi) = (whole(w[0], "an expiry")?, whole(w[2], "a strike index")?, whole(w[3], "a strike index")?);
                if !(w[1].is_finite() && w[1] > 0.0 && w[4].is_finite() && lo >= 1 && hi >= lo) {
                    return Err(bad("an expiry's step, range or pressure is not one"));
                }
                expiries.push(ListedExpiry { expiry, step: w[1], lo, hi, pressure: w[4] });
            }
            chains.push(NameChain { slot, expiries });
        }
        let settled_n = count(take(1)?[0], "the settled count")?;
        let mut settled = Vec::with_capacity(settled_n);
        for _ in 0..settled_n {
            let w = take(SETTLED_WIDTH)?;
            let (slot, expiry) = (count(w[0], "a slot")?, whole(w[1], "an expiry")?);
            let (lo, hi) = (whole(w[4], "a strike index")?, whole(w[5], "a strike index")?);
            if !(w[2].is_finite() && w[2] >= 0.0 && w[3].is_finite() && w[3] > 0.0 && lo >= 1 && hi >= lo) {
                return Err(bad("a settled expiry's reference, step or range is not one"));
            }
            settled.push(SettledName { slot, expiry, reference: w[2], step: w[3], lo, hi });
        }
        if at != words.len() {
            return Err(bad(&format!("{} numbers, more than its counts say", words.len())));
        }
        self.chains = chains;
        self.settled = settled;
        Ok(())
    }
}

/// What one name's options read now.
struct NameContext {
    chain: usize,
    slot: usize,
    ticker: String,
    spot: f64,
    surface: Surface,
    /// Each listed expiry's surface theta and the dealer's, in listing order.
    thetas: Vec<(f64, f64)>,
}

/// What one expiry of a name's options reads.
struct NameExpiry {
    expiry: i64,
    /// Sessions to the expiry session's close.
    sessions: f64,
    years: f64,
    rate: f64,
    dividends: Vec<CashDividend>,
    /// The dividends' present value, per share.
    pv: f64,
    carry_spot: f64,
    forward: f64,
    discount: f64,
    theta: f64,
    dealer_theta: f64,
}

/// The whole contracts each short position is assigned of `exercised`
/// contracts, pro rata to `shorts`: whole contracts by largest remainder
/// when `exercised` and every short are whole numbers (ties to the larger
/// short, then the earlier), exact shares otherwise. Sums to `exercised`,
/// and no short is assigned more than it holds.
fn allocate(exercised: f64, shorts: &[f64]) -> Vec<f64> {
    let total: f64 = shorts.iter().sum();
    if !(total > 0.0) || !(exercised > 0.0) {
        return vec![0.0; shorts.len()];
    }
    let exercised = crate::mathx::min(exercised, total);
    let integral = exercised.fract() == 0.0 && shorts.iter().all(|s| s.fract() == 0.0);
    if !integral {
        let mut out: Vec<f64> = shorts.iter().map(|s| exercised * s / total).collect();
        // The last takes the rounding, so the sum is exact.
        let n = out.len();
        let rest: f64 = out[..n - 1].iter().sum();
        out[n - 1] = crate::mathx::max(0.0, crate::mathx::min(shorts[n - 1], exercised - rest));
        return out;
    }
    let mut out = Vec::with_capacity(shorts.len());
    let mut fractions = Vec::with_capacity(shorts.len());
    let mut given = 0.0;
    for (i, &s) in shorts.iter().enumerate() {
        let share = exercised * s / total;
        let whole = share.floor();
        out.push(whole);
        given += whole;
        fractions.push((share - whole, s, i));
    }
    // Largest remainder first; ties to the larger short, then the earlier.
    fractions.sort_by(|a, b| b.0.total_cmp(&a.0).then(b.1.total_cmp(&a.1)).then(a.2.cmp(&b.2)));
    let mut left = (exercised - given).round() as i64;
    for &(_, s, i) in &fractions {
        if left <= 0 {
            break;
        }
        if out[i] + 1.0 <= s {
            out[i] += 1.0;
            left -= 1;
        }
    }
    out
}

impl Engine {
    // ── Switch ────────────────────────────────────────────────────────────

    pub(super) fn names_on(&self) -> bool {
        self.params.options_names_listed != 0.0
    }

    /// Whether this model lists single-name options (`options_names_listed`):
    /// a property of the model, as [`Engine::lists_options`] is. False on
    /// every shipped preset.
    pub fn lists_name_options(&self) -> bool {
        self.names_on()
    }

    /// The tickers whose options are listed now, in roster order: empty with
    /// the switch off and before the first close.
    pub fn name_option_roots(&self) -> Vec<String> {
        if !self.names_on() {
            return Vec::new();
        }
        self.name_options.chains.iter().map(|c| self.companies[c.slot].ticker.clone()).collect()
    }

    fn name_chain_of(&self, ticker: &str) -> Option<usize> {
        self.name_options.chains.iter().position(|c| self.companies.get(c.slot).is_some_and(|x| x.ticker == ticker))
    }

    /// The listed name option `symbol` names: its chain, its expiry's slot
    /// in the chain, its right and its strike.
    fn name_option_slot(&self, symbol: &str) -> Option<(usize, usize, Right, f64)> {
        if !self.names_on() {
            return None;
        }
        let parsed = ContractSymbol::parse(symbol).ok()?;
        let SymbolKind::Option { right, .. } = parsed.kind else {
            return None;
        };
        let strike = parsed.strike()?;
        let chain = self.name_chain_of(&parsed.root)?;
        let e = self.name_options.chains[chain].expiries.iter().position(|e| e.expiry == parsed.expiry)?;
        if !self.name_options.chains[chain].expiries[e].lists(strike) {
            return None;
        }
        Some((chain, e, right, strike))
    }

    /// Whether `symbol` names a listed single-name option.
    pub(super) fn is_listed_name_option(&self, symbol: &str) -> bool {
        self.name_option_slot(symbol).is_some()
    }

    // ── The surface ───────────────────────────────────────────────────────

    /// The name in `slot`'s reports the surface reads: `(knot, variance)` for
    /// each reaction session `d` from the next unprinted one on, at knot `d -
    /// base + 1`, so a report at an open counts for an option settling at
    /// that session's close and not the one before, its variance
    /// `earnings_surprise_sigma` squared times the forecast's expected
    /// one-session variance of the name at the report.
    fn name_events(&self, clock: &Clock, slot: usize, series: &[f64], day: i64, n_max: usize) -> Vec<(usize, f64)> {
        let p = &self.params;
        if p.surface_earnings_weight == 0.0 || !self.carries_earnings() || series.is_empty() {
            return Vec::new();
        }
        let c = &self.companies[slot];
        if !c.is_public || c.is_bankrupt {
            return Vec::new();
        }
        let h = crate::earnings::id_hash(&c.id);
        let q = crate::earnings::QUARTER_SESSIONS;
        let from = clock.unprinted;
        let last = clock.base + n_max as i64 - 1;
        let sigma = p.earnings_surprise_sigma;
        let mut out = Vec::new();
        if last < from {
            return out;
        }
        for quarter in std::cmp::max(from, 0).div_euclid(q)..=std::cmp::max(last, 0).div_euclid(q) {
            let d = crate::earnings::reaction_session(self.earnings_key, h, quarter);
            if d < from || d > last {
                continue;
            }
            let n = d - clock.base + 1;
            if n < 1 || n as usize > n_max {
                continue;
            }
            let at = std::cmp::max(1, d - day + 1) as usize;
            let v = series[std::cmp::min(at, series.len()) - 1];
            out.push((n as usize, sigma * sigma * v));
        }
        out
    }

    /// The surface of the name in `slot` now, on the index surface `index`:
    /// `None` without a forecast of the name.
    fn name_surface_at(&self, clock: &Clock, slot: usize, index: &Surface) -> Option<Surface> {
        let p = &self.params;
        let f = self.forecast()?;
        let series = f.name_variance.get(slot).filter(|s| !s.is_empty())?;
        let scaled: Vec<f64>;
        let variance: &[f64] = if p.surface_name_premium == 0.0 {
            series
        } else {
            let k = (1.0 + p.surface_name_premium) * (1.0 + p.surface_name_premium);
            scaled = series.iter().map(|v| v * k).collect();
            &scaled
        };
        let last = self
            .name_chain_of(&self.companies[slot].ticker)
            .and_then(|c| self.name_options.chains[c].expiries.last())
            .map_or(0, |e| e.expiry + 1 - clock.base);
        let n_max = std::cmp::max(SURFACE_MIN_KNOTS, std::cmp::max(last, 0) as usize + 2);
        let events = self.name_events(clock, slot, series, f.day, n_max);
        let inputs = TermInputs::new(
            variance,
            clock.elapsed,
            p.surface_term_premium_short,
            p.surface_term_premium_long,
            &events,
            p.surface_earnings_weight,
        );
        let shape = term_shape(&inputs, n_max);
        let curve = ThetaCurve::from_shape(&shape, clock.elapsed, 1.0)?;
        let rho = if p.surface_name_skew == 0.0 { 0.0 } else { p.surface_name_skew * index.ssvi.rho };
        Some(Surface {
            root: self.companies[slot].ticker.clone(),
            ssvi: Ssvi::new(rho, index.ssvi.eta, index.ssvi.gamma),
            curve,
            vix: index.vix,
            skewness_target: None,
            fit: index.fit,
        })
    }

    /// The surface of `ticker`'s options now (`options_names_listed`): its
    /// smile's correlation, the index's curvature and its term structure
    /// (`curve`). `vix` and `fit` are the index surface's, which its
    /// correlation is a share of. `None` with the switch off, for a name
    /// without listed options and before the first close's forecast.
    pub fn name_surface(&self, ticker: &str) -> Option<Surface> {
        let chain = self.name_chain_of(ticker)?;
        let clock = self.options_clock();
        let index = self.surface_at(&clock)?;
        self.name_surface_at(&clock, self.name_options.chains[chain].slot, &index)
    }

    /// `ticker`'s carry `sessions` from now: `(forward, rate, dividends)`, the
    /// forward of its escrowed price, the policy rate the forecast expects to
    /// the session nearest `now + sessions` (a fraction a year) and the
    /// present value of the dividends going ex before it, per share. `None`
    /// where [`Engine::name_surface`] is.
    pub fn name_carry(&self, ticker: &str, sessions: f64) -> Option<(f64, f64, f64)> {
        let chain = self.name_chain_of(ticker)?;
        if !sessions.is_finite() {
            return None;
        }
        let clock = self.options_clock();
        let slot = self.name_options.chains[chain].slot;
        let s = crate::mathx::max(0.0, sessions);
        let end = (clock.now + s).round() as i64;
        let x = self.name_carry_to(&clock, slot, end, s);
        Some((x.forward, x.rate, x.pv))
    }

    /// The carry of the name in `slot` to `end`, the session-start the
    /// option's life ends at, `sessions` away: the dividends going ex at or
    /// before `end - 1`'s open.
    fn name_carry_to(&self, clock: &Clock, slot: usize, end: i64, sessions: f64) -> NameExpiry {
        let c = &self.companies[slot];
        let spot = c.stock.price;
        let years = sessions / 252.0;
        let rate = self.futures_rate(end, clock.now);
        let mut dividends = Vec::new();
        let mut pv = 0.0;
        for u in crate::market::dividends::lookahead(&self.params, &c.ticker, c.stock.dividend.as_ref(), clock.after, spot, end - 1) {
            let t = crate::mathx::max(0.0, u.ex_day as f64 - clock.now) / 252.0;
            if t < years {
                dividends.push(CashDividend::new(t, u.amount));
                pv += u.amount * crate::mathx::exp(-rate * t);
            }
        }
        let carry_spot = spot - pv;
        NameExpiry {
            expiry: end - 1,
            sessions,
            years,
            rate,
            dividends,
            pv,
            carry_spot,
            forward: carry_spot * crate::mathx::exp(rate * years),
            discount: crate::mathx::exp(-rate * years),
            theta: 0.0,
            dealer_theta: 0.0,
        }
    }

    // ── Pricing ───────────────────────────────────────────────────────────

    /// What the name in chain `chain` reads now, on the index surface `index`.
    fn name_context(&self, clock: &Clock, chain: usize, index: &Surface) -> Option<NameContext> {
        let c = &self.name_options.chains[chain];
        let company = &self.companies[c.slot];
        let spot = company.stock.price;
        if !(spot > 0.0 && spot.is_finite()) {
            return None;
        }
        let surface = self.name_surface_at(clock, c.slot, index)?;
        let spec = &NAME_OPTION_DEALER;
        let base = self.params.option_dealer_spread;
        let mut plain = Vec::with_capacity(c.expiries.len());
        let mut shifts = Vec::with_capacity(c.expiries.len());
        for e in &c.expiries {
            let sessions = crate::mathx::max(0.0, (e.expiry + 1) as f64 - clock.now);
            let theta = surface.theta(sessions);
            plain.push((sessions, theta));
            let half_atm = spec.half_spread(base, surface.vix, 0.0);
            let slope = self.params.option_name_inventory_slope;
            shifts.push(if slope != 0.0 {
                // `option_name_inventory_slope`: a share of the expiry's
                // at-the-money volatility per contract, under the same cap
                let vol = if sessions > 0.0 && theta > 0.0 { crate::mathx::sqrt(theta * 252.0 / sessions) } else { 0.0 };
                if e.pressure == 0.0 || !(half_atm > 0.0) {
                    0.0
                } else {
                    let cap = spec.inventory_cap * half_atm;
                    crate::mathx::clamp(slope * vol * e.pressure, -cap, cap)
                }
            } else {
                spec.inventory_shift(e.pressure, half_atm)
            });
        }
        let dealer = dealer_thetas(&plain, &shifts);
        let thetas = plain.iter().zip(dealer).map(|(&(_, theta), d)| (theta, d)).collect();
        Some(NameContext { chain, slot: c.slot, ticker: company.ticker.clone(), spot, surface, thetas })
    }

    fn name_expiry(&self, clock: &Clock, ctx: &NameContext, slot: usize) -> NameExpiry {
        let e = &self.name_options.chains[ctx.chain].expiries[slot];
        let sessions = crate::mathx::max(0.0, (e.expiry + 1) as f64 - clock.now);
        let mut x = self.name_carry_to(clock, ctx.slot, e.expiry + 1, sessions);
        let (theta, dealer_theta) = ctx.thetas[slot];
        x.theta = theta;
        x.dealer_theta = dealer_theta;
        x
    }

    /// The American value, delta and gamma of an option on the name at
    /// `spot`, at the volatility `vol`, from the session's cache.
    #[allow(clippy::too_many_arguments)]
    fn name_tree(&self, right: Right, spot: f64, strike: f64, x: &NameExpiry, vol: f64, style: Style) -> Option<TreeRead> {
        if !(x.years > 0.0 && vol > 0.0) {
            let value = match right {
                Right::Call => crate::mathx::max(0.0, spot - strike),
                Right::Put => crate::mathx::max(0.0, strike - spot),
            };
            let delta = match (right, value > 0.0) {
                (Right::Call, true) => 1.0,
                (Right::Put, true) => -1.0,
                _ => 0.0,
            };
            return Some(TreeRead { value, delta, gamma: 0.0 });
        }
        let steps = american::steps_for(x.sessions.ceil() as i64);
        let mut key = Vec::with_capacity(8 + 2 * x.dividends.len());
        key.push(match (right, style) {
            (Right::Call, Style::American) => 0,
            (Right::Put, Style::American) => 1,
            (Right::Call, _) => 2,
            (Right::Put, _) => 3,
        });
        for v in [spot, strike, x.rate, vol, x.years] {
            key.push(v.to_bits());
        }
        key.push(steps as u64);
        for d in &x.dividends {
            key.push(d.time.to_bits());
            key.push(d.amount.to_bits());
        }
        if let Ok(guard) = self.name_options.cache.0.lock() {
            if let Some(read) = guard.get(&key) {
                return Some(*read);
            }
        }
        let spec = OptionSpec::new(right, spot, strike, x.rate, vol, x.years);
        let tree = american::price(&spec, &x.dividends, steps, style, Lattice::LeisenReimer).ok()?;
        let read = TreeRead { value: tree.value, delta: tree.delta, gamma: tree.gamma };
        if let Ok(mut guard) = self.name_options.cache.0.lock() {
            if guard.len() >= TREE_CACHE_CAP {
                guard.clear();
            }
            guard.insert(key, read);
        }
        Some(read)
    }

    /// The volatility `theta` gives a strike at log-moneyness `k`.
    fn name_vol(surface: &Surface, x: &NameExpiry, k: f64, theta: f64) -> f64 {
        if x.years > 0.0 && theta > 0.0 {
            crate::mathx::sqrt(surface.ssvi.total_variance(k, theta) / x.years)
        } else {
            0.0
        }
    }

    /// The dealer's ladder and quote for one name option.
    fn name_priced(&self, ctx: &NameContext, x: &NameExpiry, right: Right, strike: f64) -> Option<(Ladder, OptionQuote)> {
        let spec = &NAME_OPTION_DEALER;
        let k = crate::mathx::log(strike / x.forward);
        let iv = Self::name_vol(&ctx.surface, x, k, x.dealer_theta);
        let surface_iv = Self::name_vol(&ctx.surface, x, k, x.theta);
        let tree = self.name_tree(right, ctx.spot, strike, x, iv, Style::American)?;
        let fair = if surface_iv.to_bits() == iv.to_bits() {
            tree.value
        } else {
            self.name_tree(right, ctx.spot, strike, x, surface_iv, Style::American)?.value
        };
        let z = if x.theta > 0.0 { k / crate::mathx::sqrt(x.theta) } else { 0.0 };
        let mut half = spec.half_spread(self.params.option_dealer_spread, ctx.surface.vix, z);
        let exponent = self.params.option_name_spread_tenor_exponent;
        let tenor = if exponent != 0.0 {
            crate::mathx::pow(crate::mathx::max(x.sessions, 1.0) / 21.0, exponent)
        } else {
            1.0
        };
        let (otm, itm) = (self.params.option_name_spread_otm, self.params.option_name_spread_itm);
        let atm_vol = if x.years > 0.0 && x.dealer_theta > 0.0 {
            crate::mathx::sqrt(x.dealer_theta / x.years)
        } else {
            0.0
        };
        let l = if (otm != 0.0 || itm != 0.0) && atm_vol > 0.0 {
            // `option_name_spread_otm` and `_itm`: the half-spread a share of
            // the price, the at-the-money share times the moneyness factor
            let side = |v: f64| if v != 0.0 { v } else { 1.0 };
            let share = spec.half_spread(self.params.option_dealer_spread, ctx.surface.vix, 0.0) / atm_vol
                * moneyness_factor(tree.delta.abs(), side(otm), side(itm))
                * tenor;
            price_ladder(spec, tree.value, share * tree.value, iv)
        } else {
            if exponent != 0.0 {
                half *= tenor;
            }
            american_ladder(spec, right, x.forward, strike, x.years, x.discount, iv, half, tree.value)
        };
        let (_, euro) = black_greeks(right, x.carry_spot, strike, x.years, iv, x.rate);
        let greeks = Greeks { delta: tree.delta, gamma: tree.gamma, vega: euro.vega, theta: euro.theta };
        let symbol = name_option_symbol(&ctx.ticker, x.expiry, right, strike);
        let open_interest = self.name_open_interest(&symbol);
        let q = OptionQuote {
            root: ctx.ticker.clone(),
            expiry: x.expiry,
            right,
            strike,
            bid: l.bids.first().map(|b| b.price),
            ask: l.asks.first().map(|a| a.price),
            bid_size: l.bids.first().map_or(0.0, |b| b.size),
            ask_size: l.asks.first().map_or(0.0, |a| a.size),
            mid: tree.value,
            fair,
            iv,
            surface_iv,
            greeks,
            forward: x.forward,
            index: ctx.spot,
            rate: x.rate,
            dividends: x.pv,
            sessions_to_expiry: x.sessions,
            multiplier: NAME_OPTION.multiplier,
            tick: spec.tick_at(tree.value),
            open_interest,
            initial_margin: None,
            maintenance_margin: None,
            symbol,
        };
        Some((l, q))
    }

    /// A row of an expiry's summary: the dealer's volatility and the
    /// European delta on the escrowed price, which need no tree.
    fn name_light(&self, ctx: &NameContext, x: &NameExpiry, right: Right, strike: f64) -> OptionQuote {
        let k = crate::mathx::log(strike / x.forward);
        let iv = Self::name_vol(&ctx.surface, x, k, x.dealer_theta);
        let (mid, greeks) = black_greeks(right, x.carry_spot, strike, x.years, iv, x.rate);
        OptionQuote {
            symbol: String::new(),
            root: ctx.ticker.clone(),
            expiry: x.expiry,
            right,
            strike,
            bid: None,
            ask: None,
            bid_size: 0.0,
            ask_size: 0.0,
            mid,
            fair: mid,
            iv,
            surface_iv: iv,
            greeks,
            forward: x.forward,
            index: ctx.spot,
            rate: x.rate,
            dividends: x.pv,
            sessions_to_expiry: x.sessions,
            multiplier: NAME_OPTION.multiplier,
            tick: NAME_OPTION_DEALER.tick,
            open_interest: 0.0,
            initial_margin: None,
            maintenance_margin: None,
        }
    }

    /// Contracts open in `symbol`: every agent's long position plus the
    /// dealer's, which is the other side of all of theirs.
    fn name_open_interest(&self, symbol: &str) -> f64 {
        let mut longs = 0.0;
        let mut net = 0.0;
        for (_, ticker, bought, sold) in &self.name_options.book.flow {
            if ticker == symbol {
                let pos = bought - sold;
                longs += crate::mathx::max(0.0, pos);
                net += pos;
            }
        }
        longs + crate::mathx::max(0.0, -net)
    }

    /// A listed name option's quote now: `None` for a symbol not listed,
    /// with the switch off and where the surface does not price.
    pub(super) fn name_option_quote(&self, symbol: &str) -> Option<OptionQuote> {
        let (chain, slot, right, strike) = self.name_option_slot(symbol)?;
        let clock = self.options_clock();
        let index = self.surface_at(&clock)?;
        let ctx = self.name_context(&clock, chain, &index)?;
        let x = self.name_expiry(&clock, &ctx, slot);
        let (_, mut q) = self.name_priced(&ctx, &x, right, strike)?;
        q.initial_margin = self.option_margin(&[(symbol.to_string(), -1.0)]);
        q.maintenance_margin = q.initial_margin.map(|m| m / super::margin::MAINTENANCE_RATIO);
        Some(q)
    }

    /// `ticker`'s options at `expiry`, by strike, the call before the put.
    pub(super) fn name_chain(&self, ticker: &str, expiry: i64) -> Option<Vec<OptionQuote>> {
        let chain = self.name_chain_of(ticker)?;
        let slot = self.name_options.chains[chain].expiries.iter().position(|e| e.expiry == expiry)?;
        let clock = self.options_clock();
        let index = self.surface_at(&clock)?;
        let ctx = self.name_context(&clock, chain, &index)?;
        let x = self.name_expiry(&clock, &ctx, slot);
        let e = &self.name_options.chains[chain].expiries[slot];
        let mut out = Vec::with_capacity(2 * (e.hi - e.lo + 1) as usize);
        for strike in e.strikes() {
            for right in [Right::Call, Right::Put] {
                out.push(self.name_priced(&ctx, &x, right, strike)?.1);
            }
        }
        Some(out)
    }

    /// `ticker`'s listed expiries as a trader reads them now (see
    /// [`Engine::option_expiries`]): each read on the dealer's volatilities
    /// and the European deltas on the escrowed price, so it prices no tree.
    pub(super) fn name_option_expiries(&self, ticker: &str) -> Vec<ExpirySummary> {
        let Some(chain) = self.name_chain_of(ticker) else {
            return Vec::new();
        };
        let clock = self.options_clock();
        let Some(index) = self.surface_at(&clock) else {
            return Vec::new();
        };
        let Some(ctx) = self.name_context(&clock, chain, &index) else {
            return Vec::new();
        };
        let expiries = &self.name_options.chains[chain].expiries;
        (0..expiries.len())
            .map(|slot| {
                let e = &expiries[slot];
                let x = self.name_expiry(&clock, &ctx, slot);
                let mut rows = Vec::new();
                for strike in e.strikes() {
                    for right in [Right::Call, Right::Put] {
                        rows.push(self.name_light(&ctx, &x, right, strike));
                    }
                }
                let (atm_iv, put_25d_iv, call_25d_iv) = read_smile(&rows);
                ExpirySummary {
                    root: ticker.to_string(),
                    expiry: e.expiry,
                    sessions_to_expiry: x.sessions,
                    forward: x.forward,
                    strike_low: strike_at(e.lo, e.step),
                    strike_high: strike_at(e.hi, e.step),
                    strike_step: e.step,
                    atm_iv,
                    put_25d_iv,
                    call_25d_iv,
                    skew_25d: match (put_25d_iv, call_25d_iv) {
                        (Some(p), Some(c)) => Some(p - c),
                        _ => None,
                    },
                }
            })
            .collect()
    }

    /// The scan's initial margin on the name options and stock in
    /// `positions` (`(symbol or ticker, signed contracts or shares)`), name
    /// by name: the worst loss over the scan's moves of the name, sized to
    /// the margin's quantile of its one-session sd, at each volatility
    /// shift, the stock's gain in each move netted against the options' loss
    /// there (`option_scan_loss_with`), so stock that covers an option's risk
    /// nets it and stock alone asks nothing. Each
    /// option is revalued as a European on its escrowed price. 0 where no
    /// name option is held; `None` where a held name does not price.
    pub(super) fn name_option_margin(&self, positions: &[(String, f64)]) -> Option<f64> {
        let mut by_chain: Vec<(usize, Vec<HeldOption>)> = Vec::new();
        for (symbol, quantity) in positions {
            if *quantity == 0.0 {
                continue;
            }
            let Some((chain, slot, right, strike)) = self.name_option_slot(symbol) else {
                continue;
            };
            match by_chain.iter_mut().find(|(c, _)| *c == chain) {
                Some((_, v)) => v.push((slot, right, strike, *quantity)),
                None => by_chain.push((chain, vec![(slot, right, strike, *quantity)])),
            }
        }
        if by_chain.is_empty() {
            return Some(0.0);
        }
        let clock = self.options_clock();
        let index = self.surface_at(&clock)?;
        let p = &self.params;
        let z = super::margin::normal_quantile(0.5 + 0.5 * p.margin_scan_coverage)
            * super::margin::option_scan_tail(p);
        let mut total = 0.0;
        for (chain, held) in by_chain {
            let ctx = self.name_context(&clock, chain, &index)?;
            let mut scan = Vec::with_capacity(held.len());
            for (slot, right, strike, quantity) in held {
                let x = self.name_expiry(&clock, &ctx, slot);
                let k = crate::mathx::log(strike / x.forward);
                scan.push(super::margin::ScanOption {
                    right,
                    carry_spot: x.carry_spot,
                    strike,
                    years: x.years,
                    vol: Self::name_vol(&ctx.surface, &x, k, x.theta),
                    rate: x.rate,
                    units: quantity * NAME_OPTION.multiplier,
                });
            }
            let shares: f64 = positions.iter().filter(|(s, _)| *s == ctx.ticker).map(|(_, q)| *q).sum();
            // The name's one-session sd: the forecast's for the next session.
            let variance = self
                .forecast()
                .and_then(|f| f.name_variance.get(ctx.slot))
                .and_then(|s| s.first().copied())
                .unwrap_or(0.0);
            let spot_move = z * crate::mathx::sqrt(crate::mathx::max(0.0, variance)) * ctx.spot;
            let loss = super::margin::option_scan_loss_with(&scan, shares, spot_move, z * super::margin::SCAN_VOL_SD);
            // The portfolio-margin rule's per-contract minimum
            // (`margin_option_minimum`).
            total += if p.margin_option_minimum != 0.0 {
                crate::mathx::max(loss, super::margin::option_contract_minimum(&scan, p.margin_option_minimum))
            } else {
                loss
            };
        }
        Some(total)
    }

    // ── Listing ───────────────────────────────────────────────────────────

    /// List, for every public name, the expiries at or after session `from`
    /// it does not list, on its price tier, and widen every range around
    /// each name's price.
    fn name_options_list(&mut self, from: i64) {
        let expiries = calendar::name_option_expiries(from);
        let mut chains = std::mem::take(&mut self.name_options.chains);
        for (slot, c) in self.companies.iter().enumerate() {
            let price = c.stock.price;
            if !c.is_public || c.is_bankrupt || RESERVED_ROOTS.contains(&c.ticker.as_str()) || !(price > 0.0) {
                continue;
            }
            let at = match chains.iter().position(|x| x.slot == slot) {
                Some(at) => at,
                None => {
                    chains.push(NameChain { slot, expiries: Vec::new() });
                    chains.len() - 1
                }
            };
            let chain = &mut chains[at];
            for &expiry in &expiries {
                if !chain.expiries.iter().any(|e| e.expiry == expiry) {
                    let step = NAME_OPTION.strike_step(price);
                    chain.expiries.push(ListedExpiry { expiry, step, lo: 1, hi: 0, pressure: 0.0 });
                }
            }
            chain.expiries.sort_by_key(|e| e.expiry);
        }
        chains.sort_by_key(|c| c.slot);
        self.name_options.chains = chains;
        self.name_options_widen();
    }

    /// Widen every listed range to keep ten strikes either side of its
    /// name's price.
    fn name_options_widen(&mut self) {
        for chain in self.name_options.chains.iter_mut() {
            let price = self.companies[chain.slot].stock.price;
            if !(price > 0.0 && price.is_finite()) {
                continue;
            }
            for e in chain.expiries.iter_mut() {
                e.widen(price, NAME_OPTION.strikes_each_side);
            }
        }
    }

    // ── Orders ────────────────────────────────────────────────────────────

    fn name_options_record_flow(&mut self, agent: &str, symbol: &str, bought: f64, sold: f64) {
        match self.name_options.book.flow.iter_mut().find(|(a, t, _, _)| a == agent && t == symbol) {
            Some(r) => {
                r.2 += bought;
                r.3 += sold;
            }
            None => self.name_options.book.flow.push((agent.to_string(), symbol.to_string(), bought, sold)),
        }
    }

    /// An order meets the dealer, as an index option's does
    /// (`Engine::submit_order`): it walks the ladder, the position is
    /// recorded, and the expiry's pressure moves. The fills and the mid.
    fn name_options_meet(
        &mut self,
        symbol: &str,
        agent: &str,
        order_id: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
    ) -> (Vec<AgentFill>, f64) {
        let Some((chain, slot, right, strike)) = self.name_option_slot(symbol) else {
            return (Vec::new(), f64::NAN);
        };
        let clock = self.options_clock();
        let Some(index) = self.surface_at(&clock) else {
            return (Vec::new(), f64::NAN);
        };
        let Some(ctx) = self.name_context(&clock, chain, &index) else {
            return (Vec::new(), f64::NAN);
        };
        let x = self.name_expiry(&clock, &ctx, slot);
        let Some((l, q)) = self.name_priced(&ctx, &x, right, strike) else {
            return (Vec::new(), f64::NAN);
        };
        let levels = match side {
            Side::Buy => &l.asks,
            Side::Sell => &l.bids,
        };
        let taken = walk(levels, quantity, limit, side == Side::Buy);
        let (day, tick) = (self.current_day, self.ticks_today());
        let mut fills = Vec::with_capacity(taken.len());
        let mut total = 0.0;
        for (price, contracts) in taken {
            total += contracts;
            let mut fill = AgentFill {
                agent: agent.to_string(),
                order_id: order_id.to_string(),
                ticker: symbol.to_string(),
                side,
                quantity: contracts,
                price,
                liquidity: Liquidity::Taker,
                counterparty: MARKET_MAKER_ID.to_string(),
                reference: q.mid,
                day,
                tick,
                sequence: self.name_options.book.fill_sequence,
            };
            self.name_options.book.fill_sequence += 1;
            self.name_options.book.fills.push(fill.clone());
            fill.sequence = self.name_options.book.fill_sequence - 1;
            fills.push(fill);
        }
        if total > 0.0 {
            match side {
                Side::Buy => self.name_options_record_flow(agent, symbol, total, 0.0),
                Side::Sell => self.name_options_record_flow(agent, symbol, 0.0, total),
            }
            // The expiry's at-the-money vega, a share.
            let atm_vol = crate::mathx::sqrt(x.theta / crate::mathx::max(x.years, 1e-12));
            let atm = black_greeks(Right::Call, x.carry_spot, x.forward, x.years, atm_vol, x.rate).1.vega;
            let sold = match side {
                Side::Buy => total,
                Side::Sell => -total,
            };
            let e = &mut self.name_options.chains[chain].expiries[slot];
            e.pressure = pressure_after(e.pressure, sold, q.greeks.vega, atm);
        }
        (fills, q.mid)
    }

    fn name_options_reduce_order(&mut self, order_id: &str, quantity: f64) {
        if let Some(o) = self.name_options.book.orders.iter_mut().find(|o| o.id == order_id) {
            o.remaining -= quantity;
        }
        self.name_options.book.orders.retain(|o| o.remaining > 1e-9);
    }

    /// Fill every waiting order the dealer's quote now crosses, in arrival
    /// order.
    fn name_options_cross_resting(&mut self) {
        if self.name_options.book.orders.is_empty() || self.options.phase != OptionsPhase::Session {
            return;
        }
        let ids: Vec<String> = self.name_options.book.orders.iter().map(|o| o.id.clone()).collect();
        for id in ids {
            let Some(o) = self.name_options.book.orders.iter().find(|o| o.id == id).cloned() else {
                continue;
            };
            let (fills, _) = self.name_options_meet(&o.ticker, &o.agent, &o.id, o.side, o.remaining, Some(o.limit));
            let filled: f64 = fills.iter().map(|f| f.quantity).sum();
            if filled > 0.0 {
                self.name_options_reduce_order(&o.id, filled);
            }
        }
    }

    /// An agent's order on a listed name option, in the session only, as
    /// an index option's is.
    pub(super) fn submit_name_option_order(
        &mut self,
        agent: &str,
        symbol: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
        order_id: Option<String>,
    ) -> Result<OrderReport, String> {
        if self.name_option_slot(symbol).is_none() {
            return Err(format!("{symbol} is not a listed contract: Engine.contracts() lists the ones that trade"));
        }
        if self.options.phase != OptionsPhase::Session {
            return Err(format!("{symbol} is an option, which trades in the session only"));
        }
        let depth = NAME_OPTION_DEALER.levels as f64 * NAME_OPTION_DEALER.displayed_size;
        self.option_limit_check(&self.name_options.book, depth, agent, symbol, side, quantity)?;
        let id = match order_id {
            Some(id) => {
                if id.is_empty() {
                    return Err("order_id cannot be empty".to_string());
                }
                id
            }
            None => format!("{agent}-{symbol}-{}", self.name_options.book.sequence),
        };
        if self.contract_order_id_taken(&id) {
            return Err(format!("order id {id:?} is already waiting in the book"));
        }
        let sequence = self.name_options.book.sequence;
        self.name_options.book.sequence += 1;
        self.name_options_cross_resting();
        let (fills, reference) = self.name_options_meet(symbol, agent, &id, side, quantity, limit);
        if !reference.is_finite() {
            return Err(format!("{symbol} has no quote: its name's surface does not price"));
        }
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
                self.name_options.book.orders.push(AgentOrder {
                    id: id.clone(),
                    agent: agent.to_string(),
                    ticker: symbol.to_string(),
                    side,
                    limit: p,
                    quantity,
                    remaining: remainder,
                    sequence,
                    mode: RestMode::Range,
                });
                (remainder, Some(RestMode::Range))
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

    pub(super) fn cancel_name_option_order(&mut self, order_id: &str, agent: Option<&str>) -> bool {
        let before = self.name_options.book.orders.len();
        self.name_options.book.orders.retain(|o| !(o.id == order_id && agent.is_none_or(|a| a == o.agent)));
        self.name_options.book.orders.len() != before
    }

    pub(super) fn name_option_orders(&self) -> &[AgentOrder] {
        &self.name_options.book.orders
    }

    pub(super) fn take_name_option_fills(&mut self, agent: Option<&str>) -> Vec<AgentFill> {
        if self.name_options.book.fills.is_empty() {
            return Vec::new();
        }
        let (taken, kept): (Vec<_>, Vec<_>) =
            std::mem::take(&mut self.name_options.book.fills).into_iter().partition(|f| agent.is_none_or(|a| a == f.agent));
        self.name_options.book.fills = kept;
        taken
    }

    /// The dealer's ladder for a listed name option as a book, with every
    /// agent's waiting order on it.
    pub(super) fn name_option_book(&self, symbol: &str) -> Option<OrderBook> {
        let (chain, slot, right, strike) = self.name_option_slot(symbol)?;
        let clock = self.options_clock();
        let index = self.surface_at(&clock)?;
        let ctx = self.name_context(&clock, chain, &index)?;
        let x = self.name_expiry(&clock, &ctx, slot);
        let (l, q) = self.name_priced(&ctx, &x, right, strike)?;
        let mut book = OrderBook::new(symbol.to_string(), Some(q.mid)).with_cap(AGENT_BOOK_CAP);
        if self.options.phase == OptionsPhase::Session {
            for b in &l.bids {
                book.rest_limit(Side::Buy, b.price, b.size, MARKET_MAKER_ID);
            }
            for a in &l.asks {
                book.rest_limit(Side::Sell, a.price, a.size, MARKET_MAKER_ID);
            }
        }
        for o in &self.name_options.book.orders {
            if o.ticker == symbol {
                book.post_limit(o.side, o.limit, o.remaining, &o.agent, Some(o.id.clone()));
            }
        }
        Some(book)
    }

    // ── Exercise ──────────────────────────────────────────────────────────

    /// `agent`'s position in `symbol`, contracts, long positive.
    fn name_position(&self, agent: &str, symbol: &str) -> f64 {
        self.name_options
            .book
            .flow
            .iter()
            .filter(|(a, t, _, _)| a == agent && t == symbol)
            .fold(0.0, |acc, (_, _, b, s)| acc + (b - s))
    }

    /// Instruct the exercise of `quantity` contracts of `agent`'s long
    /// position in the listed name option `symbol` at the next close
    /// (`options_names_listed`). The holder pays the strike for 100 shares a
    /// call, or delivers 100 shares for the strike a put, and the contracts
    /// are assigned to the short positions pro rata (see `engine::name_options`).
    /// Instructions add up; at the close each is exercised up to the long
    /// position held then. Returns the contracts now instructed for the
    /// close. Refused for an index option, which is European, for a
    /// quantity that is not positive and finite, and for more than the
    /// position held less what is already instructed.
    pub fn exercise(&mut self, agent: &str, symbol: &str, quantity: f64) -> Result<f64, String> {
        if agent.is_empty() || crate::agent_book::is_house(agent) {
            return Err(format!(
                "{agent:?} cannot exercise: agent labels are non-empty and not one of the book's own owners"
            ));
        }
        if !(quantity > 0.0 && quantity.is_finite()) {
            return Err(format!("quantity must be finite and greater than zero, got {quantity}"));
        }
        if self.is_listed_option(symbol) {
            return Err(format!(
                "{symbol} is an index option, which is European: it settles in cash at expiry and cannot be exercised"
            ));
        }
        if self.name_option_slot(symbol).is_none() {
            return Err(if self.names_on() {
                format!("{symbol} is not a listed single-name option: Engine.contracts() lists the ones that trade")
            } else {
                format!("{symbol} cannot be exercised: no single-name option is listed with options_names_listed off")
            });
        }
        let held = self.name_position(agent, symbol);
        let pending = self
            .name_options
            .instructions
            .iter()
            .filter(|i| i.agent == agent && i.symbol == symbol)
            .fold(0.0, |acc, i| acc + i.quantity);
        if quantity > held - pending + 1e-9 {
            return Err(format!(
                "{agent} holds {held} contracts of {symbol} long and has instructed {pending} already: \
                 it can exercise at most {} more",
                crate::mathx::max(0.0, held - pending)
            ));
        }
        self.name_options.instructions.push(Instruction {
            agent: agent.to_string(),
            symbol: symbol.to_string(),
            quantity,
        });
        Ok(pending + quantity)
    }

    /// Withdraw `agent`'s exercise instructions on `symbol` waiting for the
    /// close. Returns the contracts withdrawn.
    pub fn cancel_exercise(&mut self, agent: &str, symbol: &str) -> f64 {
        let mut gone = 0.0;
        self.name_options.instructions.retain(|i| {
            let hit = i.agent == agent && i.symbol == symbol;
            if hit {
                gone += i.quantity;
            }
            !hit
        });
        gone
    }

    /// The exercise instructions waiting for the next close, for one agent
    /// or all: `(agent, symbol, contracts)`, in arrival order.
    pub fn exercise_instructions(&self, agent: Option<&str>) -> Vec<InstructionRow> {
        self.name_options
            .instructions
            .iter()
            .filter(|i| agent.is_none_or(|a| a == i.agent))
            .map(|i| (i.agent.clone(), i.symbol.clone(), i.quantity))
            .collect()
    }

    /// The dealer's decision on a long it holds, at the close, from its tree
    /// at the surface's volatility.
    fn name_dealer_decision(
        &self,
        ctx: &NameContext,
        x: &NameExpiry,
        right: Right,
        strike: f64,
    ) -> Option<american::ExerciseDecision> {
        let k = crate::mathx::log(strike / x.forward);
        let vol = Self::name_vol(&ctx.surface, x, k, x.theta);
        if !(vol > 0.0 && x.years > 0.0) {
            return None;
        }
        let spec = OptionSpec::new(right, ctx.spot, strike, x.rate, vol, x.years);
        american::early_exercise(&spec, &x.dividends, american::steps_for(x.sessions.ceil() as i64)).ok()
    }

    /// The close's exercises: agents' instructions, the dealer's own and, at
    /// an expiry, every option in the money by a cent on the closing print;
    /// each contract exercised assigned pro rata to the short positions. The
    /// rows join `events` and the positions move.
    fn name_options_exercise_at_close(&mut self) {
        let t = self.elapsed_days;
        let clock = self.options_clock();
        // Every symbol with a position or an instruction, in chain, expiry,
        // strike and right order.
        let mut symbols: Vec<(usize, usize, u64, u8, String)> = Vec::new();
        let mut seen = std::collections::HashSet::new();
        let candidates = self
            .name_options
            .book
            .flow
            .iter()
            .filter(|(_, _, b, s)| b - s != 0.0)
            .map(|(_, t, _, _)| t.clone())
            .chain(self.name_options.instructions.iter().map(|i| i.symbol.clone()))
            .collect::<Vec<_>>();
        for sym in candidates {
            if !seen.insert(sym.clone()) {
                continue;
            }
            let Some((chain, slot, right, strike)) = self.name_option_slot(&sym) else {
                continue;
            };
            let cents = (strike * 100.0).round() as u64;
            symbols.push((chain, slot, cents, if right == Right::Call { 0 } else { 1 }, sym));
        }
        symbols.sort();
        let instructions = std::mem::take(&mut self.name_options.instructions);
        if symbols.is_empty() {
            return;
        }
        let index = self.surface_at(&clock);
        let mut contexts: Vec<(usize, Option<NameContext>)> = Vec::new();
        for (chain, slot, cents, rcode, sym) in symbols {
            let right = if rcode == 0 { Right::Call } else { Right::Put };
            let strike = cents as f64 / 100.0;
            let company_slot = self.name_options.chains[chain].slot;
            let expiry = self.name_options.chains[chain].expiries[slot].expiry;
            let expiring = expiry <= t;
            let spot = self.companies[company_slot].stock.price;
            let reference = if expiring {
                self.last_closes.get(company_slot).copied().unwrap_or(spot)
            } else {
                spot
            };
            let positions: Vec<(String, f64)> = self
                .name_options
                .book
                .flow
                .iter()
                .filter(|(_, s, _, _)| *s == sym)
                .map(|(a, _, b, s)| (a.clone(), b - s))
                .collect();
            let dealer = -positions.iter().map(|(_, p)| p).sum::<f64>();
            let itm = american::exercised_at_expiry(right, strike, reference);
            let mut exercises: Vec<(String, f64)> = Vec::new();
            for (agent, p) in &positions {
                if *p <= 0.0 {
                    continue;
                }
                let e = if expiring && itm {
                    *p
                } else {
                    let asked: f64 = instructions.iter().filter(|i| i.agent == *agent && i.symbol == sym).map(|i| i.quantity).sum();
                    crate::mathx::min(asked, *p)
                };
                if e > 0.0 {
                    exercises.push((agent.clone(), e));
                }
            }
            if dealer > 0.0 {
                let dealer_exercises = if expiring {
                    itm
                } else {
                    if !contexts.iter().any(|(c, _)| *c == chain) {
                        let ctx = index.as_ref().and_then(|ix| self.name_context(&clock, chain, ix));
                        contexts.push((chain, ctx));
                    }
                    let ctx = contexts.iter().find(|(c, _)| *c == chain).and_then(|(_, x)| x.as_ref());
                    match ctx {
                        Some(ctx) => {
                            let x = self.name_expiry(&clock, ctx, slot);
                            self.name_dealer_decision(ctx, &x, right, strike).is_some_and(|d| d.exercise)
                        }
                        None => false,
                    }
                };
                if dealer_exercises {
                    exercises.push((MARKET_MAKER_ID.to_string(), dealer));
                }
            }
            let exercised: f64 = exercises.iter().map(|(_, e)| e).sum();
            if !(exercised > 0.0) {
                continue;
            }
            let mut shorts: Vec<(String, f64)> =
                positions.iter().filter(|(_, p)| *p < 0.0).map(|(a, p)| (a.clone(), -p)).collect();
            if dealer < 0.0 {
                shorts.push((MARKET_MAKER_ID.to_string(), -dealer));
            }
            let sizes: Vec<f64> = shorts.iter().map(|(_, s)| *s).collect();
            let assigned = allocate(exercised, &sizes);
            let ticker = self.companies[company_slot].ticker.clone();
            let value = intrinsic(right, reference, strike);
            let row = |agent: &str, event: SettlementEvent, contracts: f64| {
                // A call's holder receives the shares and pays the strike, a
                // put's delivers them and receives it; the writer the reverse.
                let sign = match (right, event) {
                    (Right::Call, SettlementEvent::Exercise) | (Right::Put, SettlementEvent::Assignment) => 1.0,
                    _ => -1.0,
                };
                let shares = sign * NAME_OPTION.multiplier * contracts;
                let cash = -sign * cents as f64 * contracts;
                let mut s = Settlement::new(sym.clone(), ContractKind::NameOption, ticker.clone(), t, value, reference);
                s.event = event;
                s.agent = Some(agent.to_string());
                s.quantity = contracts;
                s.shares = shares;
                s.cash = cash;
                s
            };
            for (agent, e) in &exercises {
                self.name_options.events.push(row(agent, SettlementEvent::Exercise, *e));
                if agent != MARKET_MAKER_ID {
                    self.name_options_record_flow(agent, &sym, 0.0, *e);
                }
            }
            for ((agent, _), a) in shorts.iter().zip(assigned) {
                if a > 0.0 {
                    self.name_options.events.push(row(agent, SettlementEvent::Assignment, a));
                    if agent != MARKET_MAKER_ID {
                        self.name_options_record_flow(agent, &sym, a, 0.0);
                    }
                }
            }
        }
    }

    /// The close's expiries settle: each is recorded on its name's closing
    /// print, and its orders and positions go.
    fn name_options_expire(&mut self) {
        let t = self.elapsed_days;
        let mut gone: Vec<String> = Vec::new();
        let mut settled = Vec::new();
        for chain in self.name_options.chains.iter_mut() {
            let slot = chain.slot;
            let reference = self.last_closes.get(slot).copied().unwrap_or(self.companies[slot].stock.price);
            let ticker = &self.companies[slot].ticker;
            chain.expiries.retain(|e| {
                if e.expiry <= t {
                    gone.push(format!("{ticker}.O{:04}.", e.expiry));
                    settled.push(SettledName { slot, expiry: e.expiry, reference, step: e.step, lo: e.lo, hi: e.hi });
                    false
                } else {
                    true
                }
            });
        }
        if gone.is_empty() {
            return;
        }
        let hit = |s: &str| gone.iter().any(|p| s.starts_with(p.as_str()));
        self.name_options.book.orders.retain(|o| !hit(&o.ticker));
        self.name_options.book.flow.retain(|(_, s, _, _)| !hit(s));
        self.name_options.settled.extend(settled);
    }

    // ── Steps and the day's boundaries ────────────────────────────────────

    /// A tick's step: the dealer's pressures decay, and waiting orders its
    /// quotes now cross fill. Nothing with the switch off.
    pub(super) fn name_options_session_step(&mut self) {
        if !self.names_on() || self.options.phase != OptionsPhase::Session {
            return;
        }
        let life = self.params.option_name_inventory_half_life;
        let d = if life != 0.0 {
            // `option_name_inventory_half_life`, sessions of open ticks
            crate::mathx::pow(0.5, 1.0 / (life * SESSION_TICKS))
        } else {
            NAME_OPTION_DEALER.decay()
        };
        for chain in self.name_options.chains.iter_mut() {
            for e in chain.expiries.iter_mut() {
                e.pressure *= d;
                if e.pressure.abs() < 1e-9 {
                    e.pressure = 0.0;
                }
            }
        }
        self.name_options_cross_resting();
    }

    /// At an open, after the opening prints: every range widens around its
    /// name's price, and waiting orders the opening quotes cross fill.
    /// Nothing with the switch off.
    pub(super) fn name_options_open(&mut self) {
        if !self.names_on() {
            return;
        }
        self.name_options.cache.clear();
        self.name_options_widen();
        self.name_options_cross_resting();
    }

    /// At a close, on the forecast the close computed: the exercises and
    /// assignments, the expiries due settle, the next are listed and every
    /// range widened. Nothing with the switch off.
    pub(super) fn name_options_close(&mut self) {
        if !self.names_on() {
            return;
        }
        self.name_options.cache.clear();
        self.name_options_exercise_at_close();
        self.name_options_expire();
        let t = self.elapsed_days;
        self.name_options_list(t + 1);
        self.name_options.cache.clear();
    }

    /// A name leaves the roster: its options are withdrawn, each position
    /// paid in cash at its intrinsic value on the name's last price (a
    /// settlement row with the agent named, the dealer's beside them), and
    /// its chain, orders and instructions go.
    pub(super) fn name_options_remove(&mut self, index: usize) {
        if !self.names_on() {
            return;
        }
        let c = &self.companies[index];
        let (ticker, price) = (c.ticker.clone(), c.stock.price);
        let prefix = format!("{ticker}.O");
        let ours = |s: &str| {
            s.starts_with(&prefix) && ContractSymbol::parse(s).is_ok_and(|p| p.root == ticker)
        };
        let t = self.elapsed_days;
        let mut net: Vec<(String, f64)> = Vec::new();
        let rows: Vec<(String, String, f64)> = self
            .name_options
            .book
            .flow
            .iter()
            .filter(|(_, s, b, sold)| ours(s) && b - sold != 0.0)
            .map(|(a, s, b, sold)| (a.clone(), s.clone(), b - sold))
            .collect();
        for (agent, symbol, position) in rows {
            let Ok(parsed) = ContractSymbol::parse(&symbol) else {
                continue;
            };
            let (SymbolKind::Option { right, .. }, Some(strike)) = (parsed.kind, parsed.strike()) else {
                continue;
            };
            let value = intrinsic(right, price, strike);
            let mut s = Settlement::new(symbol.clone(), ContractKind::NameOption, ticker.clone(), t, value, price);
            s.agent = Some(agent);
            s.quantity = position;
            s.cash = position * NAME_OPTION.multiplier * value;
            self.name_options.events.push(s);
            match net.iter_mut().find(|(x, _)| *x == symbol) {
                Some((_, n)) => *n += position,
                None => net.push((symbol, position)),
            }
        }
        for (symbol, position) in net {
            if position == 0.0 {
                continue;
            }
            let Ok(parsed) = ContractSymbol::parse(&symbol) else {
                continue;
            };
            let (SymbolKind::Option { right, .. }, Some(strike)) = (parsed.kind, parsed.strike()) else {
                continue;
            };
            let value = intrinsic(right, price, strike);
            let mut s = Settlement::new(symbol, ContractKind::NameOption, ticker.clone(), t, value, price);
            s.agent = Some(MARKET_MAKER_ID.to_string());
            s.quantity = -position;
            s.cash = -position * NAME_OPTION.multiplier * value;
            self.name_options.events.push(s);
        }
        self.name_options.book.orders.retain(|o| !ours(&o.ticker));
        self.name_options.book.flow.retain(|(_, s, _, _)| !ours(s));
        self.name_options.instructions.retain(|i| !ours(&i.symbol));
        self.name_options.chains.retain(|c| c.slot != index);
        self.name_options.settled.retain(|s| s.slot != index);
        for c in self.name_options.chains.iter_mut() {
            if c.slot > index {
                c.slot -= 1;
            }
        }
        for s in self.name_options.settled.iter_mut() {
            if s.slot > index {
                s.slot -= 1;
            }
        }
        self.name_options.cache.clear();
    }

    // ── Reads ─────────────────────────────────────────────────────────────

    /// The listed name options, by name, expiry, strike, the call before the
    /// put: empty with the switch off.
    pub(super) fn name_option_contracts(&self) -> Vec<ContractSpec> {
        if !self.names_on() {
            return Vec::new();
        }
        let kind = ContractKind::NameOption;
        let mut out = Vec::new();
        for c in &self.name_options.chains {
            let ticker = &self.companies[c.slot].ticker;
            for e in &c.expiries {
                for strike in e.strikes() {
                    for right in [Right::Call, Right::Put] {
                        out.push(ContractSpec {
                            symbol: name_option_symbol(ticker, e.expiry, right, strike),
                            kind,
                            root: ticker.clone(),
                            expiry: e.expiry,
                            roll: e.expiry,
                            multiplier: NAME_OPTION.multiplier,
                            tick: NAME_OPTION_DEALER.tick,
                            settlement: kind.settlement(),
                            front: false,
                            right: Some(right),
                            strike: Some(strike),
                        });
                    }
                }
            }
        }
        out
    }

    /// The name options settled at `session`'s close, or every one with
    /// `None`: each listed contract at its intrinsic value on its name's
    /// closing print, then the session's exercises and assignments.
    pub(super) fn name_option_settlements(&self, session: Option<i64>) -> Vec<Settlement> {
        if !self.names_on() {
            return Vec::new();
        }
        let kind = ContractKind::NameOption;
        let mut out = Vec::new();
        for s in &self.name_options.settled {
            if session.is_some_and(|d| d != s.expiry) {
                continue;
            }
            let Some(c) = self.companies.get(s.slot) else {
                continue;
            };
            for n in s.lo..=s.hi {
                let strike = strike_at(n, s.step);
                for right in [Right::Call, Right::Put] {
                    out.push(Settlement::new(
                        name_option_symbol(&c.ticker, s.expiry, right, strike),
                        kind,
                        c.ticker.clone(),
                        s.expiry,
                        intrinsic(right, s.reference, strike),
                        s.reference,
                    ));
                }
            }
        }
        out.extend(self.name_options.events.iter().filter(|e| session.is_none_or(|d| d == e.session)).cloned());
        out.sort_by_key(|s| s.session);
        out
    }

    /// Construction rows AX1 and AX2 at the last close, as sentences: empty
    /// when every check holds. `None` with the switch off.
    ///
    /// - AX2: each exercise and assignment row moves 100 shares a contract
    ///   and the strike times the shares in cash, to the cent, and the
    ///   close's rows, the dealer's among them, sum to no shares and no cash
    ///   in every contract.
    /// - AX1, read while the close's state stands (between the close and the
    ///   next open): every early exercise of the dealer's is one its tree,
    ///   priced again now, still makes, a call's only on the eve of an
    ///   ex-date; every long the dealer still holds is one its tree holds;
    ///   and every option held is worth at least the European on the same
    ///   tree.
    pub fn exercise_violations(&self) -> Option<Vec<String>> {
        if !self.names_on() {
            return None;
        }
        let mut out = Vec::new();
        let Some(last) = self.name_options.events.iter().filter(|e| e.event != SettlementEvent::Settlement).map(|e| e.session).max() else {
            return Some(self.name_hold_violations());
        };
        let rows: Vec<&Settlement> = self.name_options.events.iter().filter(|e| e.session == last).collect();
        let mut symbols: Vec<&str> = rows.iter().map(|r| r.symbol.as_str()).collect();
        symbols.sort_unstable();
        symbols.dedup();
        for sym in &symbols {
            let (mut shares, mut cash) = (0.0, 0.0);
            for r in rows.iter().filter(|r| r.symbol == *sym) {
                if r.event == SettlementEvent::Settlement {
                    continue;
                }
                shares += r.shares;
                cash += r.cash;
                let cents = ContractSymbol::parse(sym).ok().and_then(|p| p.strike()).map_or(f64::NAN, |k| (k * 100.0).round());
                if (r.shares.abs() - NAME_OPTION.multiplier * r.quantity).abs() > 1e-9
                    || (r.cash.abs() - cents * r.quantity).abs() > 0.005
                    || r.shares * r.cash > 0.0
                {
                    out.push(format!(
                        "AX2: session {last}, {sym}: {} of {} contracts moves {} shares and {} cash",
                        r.event.as_str(), r.quantity, r.shares, r.cash
                    ));
                }
            }
            if shares.abs() > 1e-9 || cash.abs() > 0.005 {
                out.push(format!("AX2: session {last}, {sym}: the rows sum to {shares} shares and {cash} cash"));
            }
        }
        // AX1 needs the close's state: between that close and the next open.
        if self.options.phase == OptionsPhase::Night && self.elapsed_days == last {
            let clock = self.options_clock();
            if let Some(index) = self.surface_at(&clock) {
                for r in rows.iter().filter(|r| {
                    r.event == SettlementEvent::Exercise && r.agent.as_deref() == Some(MARKET_MAKER_ID)
                }) {
                    let Ok(parsed) = ContractSymbol::parse(&r.symbol) else {
                        continue;
                    };
                    if parsed.expiry <= last {
                        continue;
                    }
                    let (SymbolKind::Option { right, .. }, Some(strike)) = (parsed.kind, parsed.strike()) else {
                        continue;
                    };
                    // The contract is still listed: it expires later.
                    let Some((chain, slot, _, _)) = self.name_option_slot(&r.symbol) else {
                        out.push(format!("AX1: {} exercised early is no longer listed", r.symbol));
                        continue;
                    };
                    let Some(ctx) = self.name_context(&clock, chain, &index) else {
                        continue;
                    };
                    let x = self.name_expiry(&clock, &ctx, slot);
                    match self.name_dealer_decision(&ctx, &x, right, strike) {
                        Some(d) if d.exercise => {}
                        d => out.push(format!("AX1: the dealer exercised {} where its tree holds ({d:?})", r.symbol)),
                    }
                    if right == Right::Call && !x.dividends.iter().any(|d| d.time == 0.0) {
                        out.push(format!("AX1: the dealer exercised the call {} with no ex-date at the next open", r.symbol));
                    }
                }
            }
            out.extend(self.name_hold_violations());
        }
        Some(out)
    }

    /// AX1 on the positions standing at a close: each long the dealer holds
    /// is one its tree holds, and each option held is worth at least the
    /// European on the same tree.
    fn name_hold_violations(&self) -> Vec<String> {
        let mut out = Vec::new();
        if self.options.phase != OptionsPhase::Night {
            return out;
        }
        let clock = self.options_clock();
        let Some(index) = self.surface_at(&clock) else {
            return out;
        };
        let mut symbols: Vec<String> = self.name_options.book.flow.iter().map(|(_, s, _, _)| s.clone()).collect();
        symbols.sort();
        symbols.dedup();
        for sym in symbols {
            let net: f64 = self.name_options.book.flow.iter().filter(|(_, s, _, _)| *s == sym).map(|(_, _, b, s)| b - s).sum();
            let Some((chain, slot, right, strike)) = self.name_option_slot(&sym) else {
                continue;
            };
            let Some(ctx) = self.name_context(&clock, chain, &index) else {
                continue;
            };
            let x = self.name_expiry(&clock, &ctx, slot);
            if -net > 0.0 {
                if let Some(d) = self.name_dealer_decision(&ctx, &x, right, strike) {
                    if d.exercise {
                        out.push(format!("AX1: the dealer holds {} long where its tree exercises ({d:?})", sym));
                    }
                }
            }
            let k = crate::mathx::log(strike / x.forward);
            let vol = Self::name_vol(&ctx.surface, &x, k, x.theta);
            let a = self.name_tree(right, ctx.spot, strike, &x, vol, Style::American);
            let e = self.name_tree(right, ctx.spot, strike, &x, vol, Style::European);
            if let (Some(a), Some(e)) = (a, e) {
                if a.value < e.value - 1e-12 * strike {
                    out.push(format!("AX1: {sym}: American {} below European {}", a.value, e.value));
                }
            }
        }
        out
    }

    // ── Snapshot ──────────────────────────────────────────────────────────

    /// The name options' numbers for the snapshot and the hash: `None` with
    /// the switch off.
    pub fn name_options_words(&self) -> Option<Vec<f64>> {
        if self.names_on() {
            Some(self.name_options.to_words())
        } else {
            None
        }
    }

    /// Agents' waiting name option orders, fills and positions: `None` with
    /// the switch off and while no agent has traded one.
    pub fn name_options_book_state(&self) -> Option<&BookState> {
        if self.names_on() && !self.name_options.book.is_pristine() {
            Some(&self.name_options.book)
        } else {
            None
        }
    }

    /// The exercise instructions waiting for the close, `(agent, symbol,
    /// contracts)`, and every exercise, assignment and delisting settlement
    /// so far: `None` with the switch off and while both are empty.
    pub fn name_options_exercise_state(&self) -> Option<(Vec<InstructionRow>, &[Settlement])> {
        if self.names_on() && !(self.name_options.instructions.is_empty() && self.name_options.events.is_empty()) {
            Some((self.exercise_instructions(None), &self.name_options.events))
        } else {
            None
        }
    }

    /// Put the name options back (a restore), each part refused where the
    /// switch is off.
    pub fn set_name_options_state(
        &mut self,
        words: Option<&[f64]>,
        book: Option<BookState>,
        exercise: Option<(Vec<InstructionRow>, Vec<Settlement>)>,
    ) -> Result<(), String> {
        if !self.names_on() {
            if words.is_some() || book.is_some() || exercise.is_some() {
                return Err("this snapshot carries single-name options (name_options), which only an engine \
                            with options_names_listed on keeps, and this engine's model has it off"
                    .to_string());
            }
            self.name_options = NameOptionsState::default();
            return Ok(());
        }
        let Some(words) = words else {
            return Err("an engine with options_names_listed on carries its listing (name_options); this \
                        snapshot lacks it"
                .to_string());
        };
        let mut state = NameOptionsState::default();
        state.set_words(words)?;
        if let Some(bad) = state.chains.iter().map(|c| c.slot).chain(state.settled.iter().map(|s| s.slot)).find(|s| *s >= self.companies.len()) {
            return Err(format!("this snapshot's name_options list slot {bad}, past the roster's {}", self.companies.len()));
        }
        state.book = book.unwrap_or_default();
        if let Some((instructions, events)) = exercise {
            state.instructions = instructions
                .into_iter()
                .map(|(agent, symbol, quantity)| Instruction { agent, symbol, quantity })
                .collect();
            state.events = events;
        }
        self.name_options = state;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::derivatives::pricing::black_price;
    use crate::economy::{create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions};

    fn roster() -> Vec<TickCompany> {
        crate::universe::random_universe(12, 5)
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect()
    }

    const NAMES: &[(&str, f64)] = &[
        ("options_names_listed", 1.0),
        ("surface_name_premium", 0.1),
        ("surface_name_skew", 0.5),
    ];

    fn engine(seed: u64, names: bool) -> Engine {
        let mut p = crate::params::PT_V21;
        for (name, value) in super::super::options::tests::ON {
            p = p.with_override(name, *value).unwrap();
        }
        if names {
            for (name, value) in NAMES {
                p = p.with_override(name, *value).unwrap();
            }
        }
        Engine::with_params(
            seed,
            roster(),
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
            p,
        )
    }

    fn day(e: &mut Engine, d: i64, buffer: &mut SessionBuffer) {
        e.run_numbered_day(d, 390, buffer);
    }

    /// Open session `d`, run `f` in it before any tick, and close it.
    fn trading_day(e: &mut Engine, d: i64, buffer: &mut SessionBuffer, f: impl FnOnce(&mut Engine)) {
        e.set_current_day(d);
        e.open_market();
        f(e);
        e.run_session(&SessionRequest::new(crate::market::GameTime::new(9, 30, 3), 390), buffer);
        e.close_day(d + 1);
    }

    fn sym(e: &Engine, chain: usize, slot: usize, right: Right, n: i64) -> String {
        let c = &e.name_options.chains[chain];
        let x = &c.expiries[slot];
        name_option_symbol(&e.companies[c.slot].ticker, x.expiry, right, strike_at(n, x.step))
    }

    /// The listed strike index nearest the name's price.
    fn atm(e: &Engine, chain: usize, slot: usize) -> i64 {
        let c = &e.name_options.chains[chain];
        (e.companies[c.slot].stock.price / c.expiries[slot].step).round() as i64
    }

    #[test]
    fn off_nothing_is_listed_and_nothing_is_kept() {
        let mut e = engine(3, false);
        let mut buffer = SessionBuffer::new();
        day(&mut e, 0, &mut buffer);
        assert!(!e.lists_name_options());
        assert!(e.name_option_roots().is_empty());
        assert!(e.name_options_words().is_none());
        assert!(e.name_options_exercise_state().is_none());
        let ticker = e.companies[0].ticker.clone();
        assert!(e.name_surface(&ticker).is_none());
        assert!(e.exercise("a", &format!("{ticker}.O0014.C10.00"), 1.0).is_err());
        assert!(e.exercise_violations().is_none());
        assert!(e.contracts().iter().all(|c| c.kind != ContractKind::NameOption));
    }

    #[test]
    fn every_name_lists_six_expiries_on_its_tier_from_the_first_close() {
        let mut e = engine(5, true);
        let mut buffer = SessionBuffer::new();
        assert!(e.name_option_roots().is_empty(), "nothing before the first close");
        day(&mut e, 0, &mut buffer);
        let public = e.companies.iter().filter(|c| c.is_public && !c.is_bankrupt).count();
        assert_eq!(e.name_options.chains.len(), public);
        for c in &e.name_options.chains {
            let price = e.companies[c.slot].stock.price;
            assert_eq!(c.expiries.iter().map(|x| x.expiry).collect::<Vec<_>>(), calendar::name_option_expiries(1));
            for x in &c.expiries {
                assert_eq!(x.step, NAME_OPTION.strike_step(price));
                let centre = (price / x.step).round() as i64;
                assert!(x.lo <= std::cmp::max(1, centre - 10) && x.hi >= centre + 10, "{x:?} at {price}");
            }
        }
        let listed: i64 = e.name_options.chains.iter().flat_map(|c| &c.expiries).map(|x| 2 * (x.hi - x.lo + 1)).sum();
        let names = e.contracts().into_iter().filter(|c| c.kind == ContractKind::NameOption).count();
        assert_eq!(names as i64, listed);
        assert_eq!(NAME_OPTION.strike_step(4.0), 0.5);
        assert_eq!(NAME_OPTION.strike_step(49.99), 1.0);
        assert_eq!(NAME_OPTION.strike_step(50.0), 2.5);
        assert_eq!(NAME_OPTION.strike_step(150.0), 5.0);
        assert_eq!(NAME_OPTION.strike_step(250.0), 10.0);
    }

    #[test]
    fn a_name_surface_reads_its_forecast_variance_times_the_premium() {
        let mut e = engine(7, true);
        let mut buffer = SessionBuffer::new();
        for d in 0..3 {
            day(&mut e, d, &mut buffer);
        }
        let f = e.forecast().unwrap().clone();
        for c in &e.name_options.chains {
            let ticker = e.companies[c.slot].ticker.clone();
            let s = e.name_surface(&ticker).unwrap();
            let series = &f.name_variance[c.slot];
            // Ten sessions out, with no report among them where the curve is
            // the premium on the expected variance summed.
            let reports = e.earnings_calendar(4, 11).iter().filter(|(slot, _)| *slot == c.slot).count();
            if reports == 0 {
                let premium = crate::derivatives::surface::term_premium(10.0, -0.03, 0.1);
                let expected: f64 = series[..10].iter().sum::<f64>() * 1.1 * 1.1 * crate::mathx::exp(premium);
                assert!((s.theta(10.0) - expected).abs() < 1e-12 * expected, "{ticker}: {} {expected}", s.theta(10.0));
            }
            assert_eq!(s.ssvi.rho, 0.5 * e.surface("IDX").unwrap().ssvi.rho);
            s.check().unwrap();
        }
    }

    #[test]
    fn quotes_are_american_monotone_and_inside_the_dealers_spread() {
        let mut e = engine(11, true);
        let mut buffer = SessionBuffer::new();
        for d in 0..3 {
            day(&mut e, d, &mut buffer);
        }
        e.set_current_day(3);
        e.open_market();
        for chain in 0..3 {
            let c = &e.name_options.chains[chain];
            let ticker = e.companies[c.slot].ticker.clone();
            let spot = e.companies[c.slot].stock.price;
            for x in c.expiries.clone().iter().take(3) {
                let quotes = e.chain(&ticker, x.expiry).unwrap();
                assert_eq!(quotes.len() as i64, 2 * (x.hi - x.lo + 1));
                for q in &quotes {
                    let intrinsic = match q.right {
                        Right::Call => (spot - q.strike).max(0.0),
                        Right::Put => (q.strike - spot).max(0.0),
                    };
                    assert!(q.mid >= intrinsic - 1e-12, "{}: {} under {intrinsic}", q.symbol, q.mid);
                    assert!(q.ask.unwrap() > q.mid, "{q:?}");
                    if let Some(b) = q.bid {
                        assert!(b < q.mid);
                    }
                    // At least the European on the escrowed forward, less the
                    // tree's discretisation.
                    let years = q.sessions_to_expiry / 252.0;
                    let euro = black_price(q.right, q.forward, q.strike, years, q.iv, crate::mathx::exp(-q.rate * years));
                    assert!(q.mid >= euro - 2e-3 * spot / 100.0, "{}: {} below {euro}", q.symbol, q.mid);
                    assert_eq!(q.multiplier, 100.0);
                    assert!(q.greeks.delta.abs() <= 1.0 + 1e-9);
                }
                let side = |r: Right| quotes.iter().filter(|q| q.right == r).collect::<Vec<_>>();
                for w in side(Right::Call).windows(2) {
                    assert!(w[1].mid <= w[0].mid + 1e-12, "calls fall in the strike");
                }
                for w in side(Right::Put).windows(2) {
                    assert!(w[1].mid >= w[0].mid - 1e-12, "puts rise in the strike");
                }
                for w in side(Right::Call).windows(3) {
                    assert!(w[0].mid + w[2].mid >= 2.0 * w[1].mid - 1e-9, "convex");
                }
            }
            // The summary reads the chain without a tree.
            let summary = e.option_expiries(&ticker);
            assert_eq!(summary.len(), e.name_options.chains[chain].expiries.len());
            assert!(summary[0].atm_iv.unwrap() > 0.0);
        }
    }

    #[test]
    fn an_agents_exercise_delivers_at_the_strike_and_conserves() {
        let mut e = engine(13, true);
        let mut buffer = SessionBuffer::new();
        for d in 0..3 {
            day(&mut e, d, &mut buffer);
        }
        let call = sym(&e, 0, 1, Right::Call, atm(&e, 0, 1) - 3);
        let put = sym(&e, 1, 1, Right::Put, atm(&e, 1, 1) + 3);
        trading_day(&mut e, 3, &mut buffer, |e| {
            assert_eq!(e.submit_order("a", &call, Side::Buy, 7.0, None, None).unwrap().filled, 7.0);
            assert_eq!(e.submit_order("b", &call, Side::Sell, 3.0, None, None).unwrap().filled, 3.0);
            assert_eq!(e.submit_order("a", &put, Side::Buy, 2.0, None, None).unwrap().filled, 2.0);
            assert!(e.exercise("a", &call, 8.0).is_err(), "more than held");
            assert_eq!(e.exercise("a", &call, 4.0).unwrap(), 4.0);
            assert_eq!(e.exercise("a", &call, 1.0).unwrap(), 5.0);
            assert!(e.exercise("a", &call, 3.0).is_err(), "more than held less instructed");
            assert!(e.exercise("b", &call, 1.0).is_err(), "a writer has nothing to exercise");
            assert_eq!(e.exercise("a", &put, 2.0).unwrap(), 2.0);
        });
        let rows: Vec<Settlement> = e.settlements(Some(3)).into_iter().filter(|s| s.event != SettlementEvent::Settlement).collect();
        let strike = ContractSymbol::parse(&call).unwrap().strike().unwrap();
        let mine: Vec<&Settlement> = rows.iter().filter(|r| r.symbol == call).collect();
        // a exercises 5; the shorts are b's 3 and the dealer's 4 (it sold 7
        // and bought 3), assigned 5 pro rata: 2 and 3, or 3 and 2.
        let ex = mine.iter().find(|r| r.event == SettlementEvent::Exercise).unwrap();
        assert_eq!((ex.agent.as_deref(), ex.quantity, ex.shares), (Some("a"), 5.0, 500.0));
        assert_eq!(ex.cash, -500.0 * strike);
        let assigned: f64 = mine.iter().filter(|r| r.event == SettlementEvent::Assignment).map(|r| r.quantity).sum();
        assert_eq!(assigned, 5.0);
        assert_eq!(mine.iter().map(|r| r.shares).sum::<f64>(), 0.0);
        assert_eq!(mine.iter().map(|r| r.cash).sum::<f64>(), 0.0);
        assert_eq!(e.name_position("a", &call), 2.0);
        let b_assigned = mine.iter().find(|r| r.agent.as_deref() == Some("b")).map_or(0.0, |r| r.quantity);
        assert_eq!(e.name_position("b", &call), -3.0 + b_assigned);
        // The put delivers shares for the strike.
        let p = rows.iter().find(|r| r.symbol == put && r.event == SettlementEvent::Exercise).unwrap();
        assert_eq!(p.shares, -200.0);
        assert!(p.cash > 0.0);
        assert!(e.exercise_instructions(None).is_empty(), "instructions are spent at the close");
        assert_eq!(e.exercise_violations().unwrap(), Vec::<String>::new());
    }

    #[test]
    fn the_dealer_exercises_a_deep_call_on_the_eve_of_a_dividend_and_assigns_its_writer() {
        let mut e = engine(17, true);
        let mut buffer = SessionBuffer::new();
        day(&mut e, 0, &mut buffer);
        // A payer whose next ex-date leaves a few sessions to trade in, and
        // whose front expiry runs past it.
        let mut found = None;
        for (i, c) in e.name_options.chains.iter().enumerate() {
            let company = &e.companies[c.slot];
            let Some(d) = company.stock.dividend else {
                continue;
            };
            if !(d.target_yield > 0.0) {
                continue;
            }
            let k = crate::market::dividends::sessions_since_ex(&company.ticker, 1);
            let ex = 1 + crate::market::dividends::DIVIDEND_PERIOD - k;
            if let Some(slot) = c.expiries.iter().position(|x| x.expiry >= ex + 3) {
                if ex >= 3 {
                    found = Some((i, slot, ex));
                    break;
                }
            }
        }
        let (chain, slot, ex) = found.expect("a payer on this roster");
        for d in 1..ex - 1 {
            day(&mut e, d, &mut buffer);
        }
        let deep = sym(&e, chain, slot, Right::Call, e.name_options.chains[chain].expiries[slot].lo);
        let near = sym(&e, chain, slot, Right::Call, atm(&e, chain, slot) + 2);
        trading_day(&mut e, ex - 1, &mut buffer, |e| {
            assert!(e.submit_order("w", &deep, Side::Sell, 10.0, None, None).unwrap().filled > 0.0);
            let _ = e.submit_order("w", &near, Side::Sell, 10.0, None, None);
        });
        let rows: Vec<Settlement> = e.settlements(Some(ex - 1)).into_iter().filter(|s| s.event != SettlementEvent::Settlement).collect();
        let on = |s: &str| rows.iter().filter(|r| r.symbol == s).collect::<Vec<_>>();
        let deep_rows = on(&deep);
        assert!(
            deep_rows.iter().any(|r| r.event == SettlementEvent::Exercise && r.agent.as_deref() == Some(MARKET_MAKER_ID)),
            "the dealer exercises the deep call on the eve: {deep_rows:?}"
        );
        let w = deep_rows.iter().find(|r| r.agent.as_deref() == Some("w")).unwrap();
        assert_eq!((w.event, w.quantity, w.shares), (SettlementEvent::Assignment, 10.0, -1000.0));
        assert!(on(&near).is_empty(), "an out-of-the-money call is held");
        assert_eq!(e.exercise_violations().unwrap(), Vec::<String>::new());
        // At the ex-date's open nothing more is exercised for it.
        day(&mut e, ex, &mut buffer);
        assert!(e.settlements(Some(ex)).iter().all(|s| s.symbol != deep));
    }

    #[test]
    fn at_expiry_the_money_is_exercised_by_exception_and_the_rest_expires() {
        let mut e = engine(19, true);
        let mut buffer = SessionBuffer::new();
        day(&mut e, 0, &mut buffer);
        let front = e.name_options.chains[0].expiries[0].expiry;
        assert_eq!(front, 14);
        for d in 1..front {
            day(&mut e, d, &mut buffer);
        }
        let n = atm(&e, 0, 0);
        let lo = sym(&e, 0, 0, Right::Call, n - 5);
        let hi = sym(&e, 0, 0, Right::Call, n + 5);
        let put = sym(&e, 0, 0, Right::Put, n - 5);
        trading_day(&mut e, front, &mut buffer, |e| {
            e.submit_order("a", &lo, Side::Buy, 4.0, None, None).unwrap();
            e.submit_order("a", &hi, Side::Buy, 4.0, None, None).unwrap();
            e.submit_order("b", &put, Side::Sell, 4.0, None, None).unwrap();
        });
        let close = e.last_closes[e.name_options.chains[0].slot];
        let rows = e.settlements(Some(front));
        let strike = |s: &str| ContractSymbol::parse(s).unwrap().strike().unwrap();
        // Every listed contract of the expiry has its settlement row.
        let ticker = e.companies[e.name_options.chains[0].slot].ticker.clone();
        let settled = e.name_options.settled.iter().find(|x| x.slot == e.name_options.chains[0].slot).unwrap().clone();
        assert_eq!(settled.reference.to_bits(), close.to_bits());
        let listed = rows.iter().filter(|r| r.event == SettlementEvent::Settlement && r.root == ticker).count() as i64;
        assert_eq!(listed, 2 * (settled.hi - settled.lo + 1));
        assert!(settled.hi - settled.lo + 1 >= 11);
        let ex = |s: &str, agent: &str| {
            rows.iter().find(|r| r.symbol == s && r.agent.as_deref() == Some(agent) && r.event != SettlementEvent::Settlement).cloned()
        };
        if close >= strike(&lo) + 0.01 {
            assert_eq!(ex(&lo, "a").unwrap().quantity, 4.0);
        }
        if close <= strike(&hi) - 0.01 {
            assert!(ex(&hi, "a").is_none());
        }
        if close <= strike(&put) - 0.01 {
            assert_eq!(ex(&put, "b").unwrap().event, SettlementEvent::Assignment);
        } else {
            assert!(ex(&put, "b").is_none());
        }
        // Positions and the expiry are gone; the next is listed.
        assert!(e.name_options.book.flow.iter().all(|(_, s, _, _)| !s.contains(".O0014.")));
        assert!(e.name_options.chains[0].expiries.iter().all(|x| x.expiry > front));
        assert_eq!(e.name_options.chains[0].expiries.len(), 6);
        assert!(e.option_quote(&lo).is_none());
        assert_eq!(e.exercise_violations().unwrap(), Vec::<String>::new());
    }

    #[test]
    fn the_construction_rows_hold_at_every_close_and_prices_do_not_move() {
        let mut on = engine(23, true);
        let mut off = engine(23, false);
        let mut b1 = SessionBuffer::new();
        let mut b2 = SessionBuffer::new();
        day(&mut on, 0, &mut b1);
        day(&mut off, 0, &mut b2);
        for d in 1..30 {
            trading_day(&mut on, d, &mut b1, |e| {
                // Write and buy across names and expiries, and exercise.
                for chain in 0..4 {
                    let slot = (d as usize + chain) % 3;
                    let n = atm(e, chain, slot);
                    let k = (d % 5) - 2;
                    let c = sym(e, chain, slot, Right::Call, n + k);
                    let p = sym(e, chain, slot, Right::Put, n - k);
                    let _ = e.submit_order("w", &c, Side::Sell, 3.0, None, None);
                    let _ = e.submit_order("w", &p, Side::Sell, 3.0, None, None);
                    let _ = e.submit_order("h", &c, Side::Buy, 2.0, None, None);
                    if d % 4 == 0 {
                        let held = e.name_position("h", &c);
                        if held > 0.0 {
                            e.exercise("h", &c, held).unwrap();
                        }
                    }
                }
            });
            day(&mut off, d, &mut b2);
            assert_eq!(on.exercise_violations().unwrap(), Vec::<String>::new(), "{d}");
            for (a, b) in on.companies.iter().zip(off.companies.iter()) {
                assert_eq!(a.stock.price.to_bits(), b.stock.price.to_bits(), "{d}: {}", a.ticker);
            }
        }
        // Words round-trip, and an engine with the switch off refuses them.
        let words = on.name_options_words().unwrap();
        let book = on.name_options_book_state().cloned();
        let exercise = on.name_options_exercise_state().map(|(i, e)| (i, e.to_vec()));
        let mut other = engine(99, true);
        other.set_name_options_state(Some(&words), book, exercise.clone()).unwrap();
        assert_eq!(other.name_options_words().unwrap(), words);
        assert_eq!(other.name_options_exercise_state().map(|(i, e)| (i, e.to_vec())), exercise);
        assert!(off.set_name_options_state(Some(&words), None, None).is_err());
        let mut short = NameOptionsState::default();
        assert!(short.set_words(&words[..words.len() - 1]).is_err());
    }

    #[test]
    fn the_position_limit_caps_a_position_and_its_waiting_orders() {
        let mut p = crate::params::PT_V21;
        for (name, value) in super::super::options::tests::ON.iter().chain(NAMES) {
            p = p.with_override(name, *value).unwrap();
        }
        p = p.with_override("option_position_limit", 0.1).unwrap();
        let mut e = Engine::with_params(
            31,
            roster(),
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
            p,
        );
        let mut buffer = SessionBuffer::new();
        day(&mut e, 0, &mut buffer);
        let call = sym(&e, 0, 1, Right::Call, atm(&e, 0, 1));
        assert_eq!(e.option_position_limit(&call), Some(20.0));
        let index = e.option_contracts()[0].symbol.clone();
        assert_eq!(e.option_position_limit(&index), Some(50.0));
        assert_eq!(e.option_position_limit("IDX.O0014.C1.00"), None);
        trading_day(&mut e, 1, &mut buffer, |e| {
            assert!(e.submit_order("a", &call, Side::Buy, 15.0, None, None).is_ok());
            let refused = e.submit_order("a", &call, Side::Buy, 6.0, None, None).unwrap_err();
            assert!(refused.contains("position limit"), "{refused}");
            // A waiting order counts as filled.
            let q = e.option_quote(&call).unwrap();
            assert!(e.submit_order("a", &call, Side::Buy, 5.0, Some(q.bid.unwrap_or(0.01)), None).is_ok());
            assert!(e.submit_order("a", &call, Side::Buy, 1.0, None, None).is_err());
            // Selling back toward zero always passes; another agent has its own limit.
            assert!(e.submit_order("a", &call, Side::Sell, 30.0, None, None).is_ok());
            assert!(e.submit_order("b", &call, Side::Sell, 20.0, None, None).is_ok());
            assert!(e.submit_order("b", &call, Side::Sell, 1.0, None, None).is_err());
        });
    }

    #[test]
    fn pro_rata_assignment_is_whole_and_sums_to_the_exercise() {
        assert_eq!(allocate(20.0, &[30.0]), vec![20.0]);
        assert_eq!(allocate(30.0, &[30.0, 10.0]), vec![23.0, 7.0]);
        assert_eq!(allocate(40.0, &[30.0, 10.0]), vec![30.0, 10.0]);
        // 10 over three shorts of 1: 3.33 each; the remainder goes to the
        // first by order.
        assert_eq!(allocate(10.0, &[10.0, 10.0, 10.0]), vec![4.0, 3.0, 3.0]);
        for x in 1..60 {
            let shorts = [7.0, 19.0, 3.0, 31.0];
            let a = allocate(x as f64, &shorts);
            assert_eq!(a.iter().sum::<f64>(), x as f64);
            for (got, s) in a.iter().zip(shorts) {
                assert!(*got <= s && got.fract() == 0.0);
                // Within one contract of the exact share.
                assert!((got - x as f64 * s / 60.0).abs() < 1.0);
            }
        }
        // Fractional positions share exactly.
        let a = allocate(1.5, &[0.5, 2.5]);
        assert!((a.iter().sum::<f64>() - 1.5).abs() < 1e-15);
        assert!((a[0] - 0.25).abs() < 1e-15);
        assert_eq!(allocate(5.0, &[]), Vec::<f64>::new());
    }
}
