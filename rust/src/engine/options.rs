//! The index options (`options_index_listed`), the surface they are quoted
//! from (`surface_ssvi`) and their dealer (`option_dealer_spread`): the
//! engine's half.
//!
//! The surface reads public state only: the published VIX (the live VIX
//! within a session), the forecast's expected index and name variances, the
//! earnings calendar and the index's capitalisation weights. It never reads
//! the cycle's true phase, a crisis, the VIX's slow memory or a pending
//! surprise. It is built when asked and keeps no state but the clock and a
//! cache of its 21-session fit. An option reads the surface, the index, the
//! dividends and the forecast's rate path, and writes only its own state
//! ([`OptionsState`]): its listing, the dealer's pressure, agents' orders,
//! fills and positions, and the settlements. It draws nothing. So every
//! stock price, close and economy is the one the engine prints with these
//! switches off, and an untraded run computes no option price at all.
//!
//! # The chain
//!
//! European, cash-settled, AM-settled on the index of the expiry session's
//! opening prints, $100 a point: the next six monthly expiries and the
//! quarterlies after them (`derivatives::calendar::index_option_expiries`).
//! An expiry's strike grid is fixed at its listing
//! ([`crate::derivatives::IndexOptionSpec::strike_step`]) and its range
//! widened at each open to keep twenty strikes either side of the index, so
//! a symbol listed once stays listed to its expiry.
//!
//! # The price
//!
//! An option `tau` sessions from expiry is priced by Black on the forward
//! the index futures' carry gives, `F = (S - PV(D)) e^{r tau}`, at the
//! surface's implied volatility at `ln(K / F)`, moved by the dealer's
//! pressure on its expiry (`derivatives::dealer`). `tau` counts a session
//! that has run `k` of its 390 ticks as `k / 390` used.

use super::*;
use crate::agent_book::{AgentFill, AgentOrder, BookState, Liquidity, RestMode, AGENT_BOOK_CAP};
use crate::derivatives::calendar;
use crate::derivatives::dealer::{chain_violations, dealer_thetas, ladder, pressure_after, walk, Ladder, OptionQuote};
use crate::derivatives::pricing::{black_greeks, black_price, intrinsic};
use crate::derivatives::surface::{fit_smile, term_shape, SmileFit, Ssvi, Surface, TermInputs, ThetaCurve, VIX_SESSIONS};
use crate::derivatives::{
    index_option_symbol, ContractKind, ContractSpec, ContractSymbol, Right, Settlement, SymbolKind, INDEX_OPTION,
    INDEX_OPTION_DEALER,
};
use crate::market_maker::MARKET_MAKER_ID;
use crate::order_book::{OrderBook, Side};

/// The ticks in a session.
const SESSION_TICKS: f64 = 390.0;

/// The VIX the physical skewness's slope is measured from.
pub const SKEW_VIX_CENTRE: f64 = 20.0;

/// The fewest whole sessions the surface's term structure runs: past a
/// year, so the 252-session strip (the VIX1Y analogue) reads knots.
const SURFACE_MIN_KNOTS: usize = 270;

/// Where the options stand in the day.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum OptionsPhase {
    BeforeFirstOpen,
    Session,
    Night,
}

impl OptionsPhase {
    fn code(self) -> f64 {
        match self {
            OptionsPhase::BeforeFirstOpen => 0.0,
            OptionsPhase::Session => 1.0,
            OptionsPhase::Night => 2.0,
        }
    }

    fn from_code(v: f64) -> Option<Self> {
        if v == 0.0 {
            Some(OptionsPhase::BeforeFirstOpen)
        } else if v == 1.0 {
            Some(OptionsPhase::Session)
        } else if v == 2.0 {
            Some(OptionsPhase::Night)
        } else {
            None
        }
    }
}

/// One listed expiry: its strike grid and the dealer's pressure on it.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct ListedExpiry {
    pub(crate) expiry: i64,
    /// The strike grid, index points; strikes are `n * step` for `n` in
    /// `lo..=hi`.
    pub(crate) step: f64,
    pub(crate) lo: i64,
    pub(crate) hi: i64,
    /// The dealer's pressure, at-the-money-equivalent contracts it is short.
    pub(crate) pressure: f64,
}

impl ListedExpiry {
    fn strikes(&self) -> impl Iterator<Item = f64> + '_ {
        (self.lo..=self.hi).map(move |n| strike_at(n, self.step))
    }

    /// Whether `strike` is on this expiry's grid and listed.
    fn lists(&self, strike: f64) -> bool {
        let n = (strike / self.step).round();
        n >= self.lo as f64 && n <= self.hi as f64 && (strike_at(n as i64, self.step) - strike).abs() < 0.005
    }

    /// Widen the range to `each` strikes either side of `index`.
    fn widen(&mut self, index: f64, each: i64) {
        let centre = (index / self.step).round() as i64;
        let lo = std::cmp::max(1, centre - each);
        if self.hi < self.lo {
            self.lo = lo;
            self.hi = centre + each;
            return;
        }
        self.lo = std::cmp::min(self.lo, lo);
        self.hi = std::cmp::max(self.hi, centre + each);
    }
}

/// A strike on a grid, in whole cents.
fn strike_at(n: i64, step: f64) -> f64 {
    (n as f64 * step * 100.0).round() / 100.0
}

/// One settled expiry: what its options settled on, and its strikes.
#[derive(Debug, Clone, PartialEq)]
pub(crate) struct SettledExpiry {
    pub(crate) expiry: i64,
    pub(crate) reference: f64,
    pub(crate) step: f64,
    pub(crate) lo: i64,
    pub(crate) hi: i64,
}

const EXPIRY_WIDTH: usize = 5;
const SETTLED_WIDTH: usize = 5;

/// The 21-session fit last made, by its inputs' bits: the fit solves two
/// strips' worth of prices per step, and a session's quotes read one VIX.
#[derive(Debug, Default)]
pub(crate) struct FitCache(std::sync::Mutex<Option<([u64; 4], SmileFit)>>);

impl Clone for FitCache {
    fn clone(&self) -> Self {
        let held = self.0.lock().map(|g| *g).unwrap_or(None);
        FitCache(std::sync::Mutex::new(held))
    }
}

/// Everything the options carry.
#[derive(Debug, Clone)]
pub(crate) struct OptionsState {
    pub(crate) phase: OptionsPhase,
    /// The listed expiries, in expiry order.
    pub(crate) expiries: Vec<ListedExpiry>,
    /// Every settled expiry, in order.
    pub(crate) settled: Vec<SettledExpiry>,
    /// Agents' waiting orders on options and their undelivered fills; its
    /// `flow` holds each agent's contracts bought and sold of each listed
    /// option, which open interest is read from.
    pub(crate) book: BookState,
    pub(crate) cache: FitCache,
}

impl Default for OptionsState {
    fn default() -> Self {
        OptionsState {
            phase: OptionsPhase::BeforeFirstOpen,
            expiries: Vec::new(),
            settled: Vec::new(),
            book: BookState::default(),
            cache: FitCache::default(),
        }
    }
}

impl OptionsState {
    /// The numbers the snapshot's `options` key and the state hash carry:
    /// the phase, the expiry count and each expiry's session, strike step,
    /// strike range and pressure, then the settled count and each settled
    /// expiry's session, reference, step and range.
    pub(crate) fn to_words(&self) -> Vec<f64> {
        let mut out = Vec::with_capacity(3 + EXPIRY_WIDTH * self.expiries.len() + SETTLED_WIDTH * self.settled.len());
        out.push(self.phase.code());
        out.push(self.expiries.len() as f64);
        for e in &self.expiries {
            out.extend_from_slice(&[e.expiry as f64, e.step, e.lo as f64, e.hi as f64, e.pressure]);
        }
        out.push(self.settled.len() as f64);
        for s in &self.settled {
            out.extend_from_slice(&[s.expiry as f64, s.reference, s.step, s.lo as f64, s.hi as f64]);
        }
        out
    }

    /// Put back what [`OptionsState::to_words`] wrote, refusing a list that
    /// is not one. The book is carried apart.
    pub(crate) fn set_words(&mut self, words: &[f64]) -> Result<(), String> {
        let bad = |why: &str| format!("this snapshot's options do not parse: {why}");
        let whole = |v: f64, what: &str| -> Result<i64, String> {
            if v.is_finite() && v.fract() == 0.0 && v.abs() <= 1e15 {
                Ok(v as i64)
            } else {
                Err(bad(&format!("{what} is {v}")))
            }
        };
        if words.len() < 3 {
            return Err(bad("fewer than three numbers"));
        }
        let phase = OptionsPhase::from_code(words[0]).ok_or_else(|| bad(&format!("the phase is {}", words[0])))?;
        let n = whole(words[1], "the expiry count")?;
        if n < 0 {
            return Err(bad("the expiry count is negative"));
        }
        let n = n as usize;
        let mut at = 2;
        if words.len() < at + EXPIRY_WIDTH * n + 1 {
            return Err(bad("it is shorter than its counts say"));
        }
        let mut expiries = Vec::with_capacity(n);
        for _ in 0..n {
            let w = &words[at..at + EXPIRY_WIDTH];
            let (expiry, lo, hi) = (whole(w[0], "an expiry")?, whole(w[2], "a strike index")?, whole(w[3], "a strike index")?);
            if !(w[1].is_finite() && w[1] > 0.0 && w[4].is_finite() && lo >= 1 && hi >= lo) {
                return Err(bad("an expiry's step, range or pressure is not one"));
            }
            expiries.push(ListedExpiry { expiry, step: w[1], lo, hi, pressure: w[4] });
            at += EXPIRY_WIDTH;
        }
        let m = whole(words[at], "the settled count")?;
        if m < 0 {
            return Err(bad("the settled count is negative"));
        }
        let m = m as usize;
        at += 1;
        if words.len() != at + SETTLED_WIDTH * m {
            return Err(bad(&format!("{} numbers for {n} expiries and {m} settled", words.len())));
        }
        let mut settled = Vec::with_capacity(m);
        for k in 0..m {
            let w = &words[at + SETTLED_WIDTH * k..at + SETTLED_WIDTH * (k + 1)];
            let (expiry, lo, hi) = (whole(w[0], "an expiry")?, whole(w[3], "a strike index")?, whole(w[4], "a strike index")?);
            if !(w[1].is_finite() && w[1] > 0.0 && w[2].is_finite() && w[2] > 0.0 && lo >= 1 && hi >= lo) {
                return Err(bad("a settled expiry's reference, step or range is not one"));
            }
            settled.push(SettledExpiry { expiry, reference: w[1], step: w[2], lo, hi });
        }
        self.phase = phase;
        self.expiries = expiries;
        self.settled = settled;
        Ok(())
    }
}

/// Where the options' clock stands: now in sessions, the last open's
/// session (or the next open's between sessions) the knots count from, the
/// share of the session run, the expiries listed after `after`, and the
/// first session whose opening prints are still to come.
#[derive(Debug, Clone, Copy)]
struct Clock {
    now: f64,
    base: i64,
    elapsed: f64,
    after: i64,
    unprinted: i64,
}

/// What an expiry's options read: its time, carry and the thetas the
/// surface and the dealer give it.
#[derive(Debug, Clone, Copy)]
struct ExpiryContext {
    sessions: f64,
    years: f64,
    rate: f64,
    dividends: f64,
    index: f64,
    carry_spot: f64,
    forward: f64,
    discount: f64,
    theta: f64,
    dealer_theta: f64,
}

impl Engine {
    // ── Switches and clock ────────────────────────────────────────────────

    pub(super) fn options_on(&self) -> bool {
        self.params.options_index_listed != 0.0
    }

    pub(super) fn surface_on(&self) -> bool {
        self.params.surface_ssvi != 0.0
    }

    fn options_clock(&self) -> Clock {
        let t = self.elapsed_days;
        match self.options.phase {
            OptionsPhase::BeforeFirstOpen => Clock { now: t as f64, base: t, elapsed: 0.0, after: t - 1, unprinted: t },
            OptionsPhase::Session => {
                let k = crate::mathx::min(1.0, f64::from(self.ticks_today()) / SESSION_TICKS);
                if k >= 1.0 {
                    Clock { now: (t + 1) as f64, base: t + 1, elapsed: 0.0, after: t, unprinted: t + 1 }
                } else {
                    Clock { now: t as f64 + k, base: t, elapsed: k, after: t, unprinted: t + 1 }
                }
            }
            OptionsPhase::Night => Clock { now: (t + 1) as f64, base: t + 1, elapsed: 0.0, after: t, unprinted: t + 1 },
        }
    }

    /// The index the options read: the level on the prices standing.
    fn options_index(&self) -> f64 {
        if self.index_divisor > 0.0 {
            self.index_level_now()
        } else {
            crate::market::index_value::INDEX_BASE
        }
    }

    /// The VIX the surface is fitted to: the live VIX within a session under
    /// `vix_intraday_live`, the published VIX otherwise.
    fn surface_vix(&self) -> f64 {
        self.live_vix().unwrap_or_else(|| self.published_vix())
    }

    // ── The surface ───────────────────────────────────────────────────────

    /// The 21-session fit at `vix` and the skewness `target`, from the cache
    /// when its inputs are the last fit's.
    fn surface_fit(&self, vix: f64, target: Option<f64>) -> Option<SmileFit> {
        let p = &self.params;
        let key = [
            vix.to_bits(),
            target.map_or(u64::MAX, f64::to_bits),
            p.surface_curvature.to_bits(),
            p.surface_curvature_exponent.to_bits(),
        ];
        if let Ok(guard) = self.options.cache.0.lock() {
            if let Some((k, fit)) = *guard {
                if k == key {
                    return Some(fit);
                }
            }
        }
        let fit = fit_smile(vix, target, VIX_SESSIONS / 252.0, p.surface_curvature, p.surface_curvature_exponent)?;
        if let Ok(mut guard) = self.options.cache.0.lock() {
            *guard = Some((key, fit));
        }
        Some(fit)
    }

    /// The earnings reports the index surface reads (`surface_earnings_weight`):
    /// `(knot, variance)` for each report printing at an open from the next
    /// unprinted one to knot `n_max`, its variance the name's index weight
    /// squared times `earnings_surprise_sigma` squared times the forecast's
    /// expected one-session variance of the name at the report.
    fn surface_events(&self, clock: &Clock, n_max: usize) -> Vec<(usize, f64)> {
        let p = &self.params;
        if p.surface_earnings_weight == 0.0 || !self.carries_earnings() {
            return Vec::new();
        }
        let Some(f) = self.forecast() else {
            return Vec::new();
        };
        let (_, _, cap) = self.index_constituents();
        if !(cap > 0.0) {
            return Vec::new();
        }
        let horizon = clock.base + n_max as i64 - clock.unprinted + 1;
        let sigma = p.earnings_surprise_sigma;
        let mut out = Vec::new();
        for (slot, d) in self.earnings_calendar(clock.unprinted, horizon) {
            let n = d - clock.base;
            if n < 1 || n as usize > n_max {
                continue;
            }
            let c = &self.companies[slot];
            if !c.is_public || c.is_bankrupt {
                continue;
            }
            let Some(series) = f.name_variance.get(slot).filter(|s| !s.is_empty()) else {
                continue;
            };
            let h = std::cmp::max(1, d - f.day + 1) as usize;
            let v = series[std::cmp::min(h, series.len()) - 1];
            let w = c.stock.price * c.stock.shares_outstanding / cap;
            out.push((n as usize, w * w * sigma * sigma * v));
        }
        out
    }

    /// The index's surface now (`surface_ssvi`): `None` with the switch off
    /// or for a root other than `IDX`, and where no strip prices.
    pub fn surface(&self, root: &str) -> Option<Surface> {
        if !self.surface_on() || root != ContractKind::IndexOption.root() {
            return None;
        }
        let clock = self.options_clock();
        self.surface_at(&clock)
    }

    fn surface_at(&self, clock: &Clock) -> Option<Surface> {
        let p = &self.params;
        let vix = self.surface_vix();
        let (eta, gamma) = (p.surface_curvature, p.surface_curvature_exponent);
        let target = if eta > 0.0 {
            let slope = if p.surface_skew_physical_slope == 0.0 {
                0.0
            } else {
                p.surface_skew_physical_slope * crate::mathx::log(vix / SKEW_VIX_CENTRE)
            };
            Some(p.surface_skew_physical + slope + p.surface_skew_premium)
        } else {
            None
        };
        let fit = self.surface_fit(vix, target)?;
        let last = self.options.expiries.last().map_or(0, |e| e.expiry - clock.base);
        let n_max = std::cmp::max(SURFACE_MIN_KNOTS, last.max(0) as usize + 2);
        let events = self.surface_events(clock, n_max);
        let variance: &[f64] = match self.forecast() {
            Some(f) => &f.index_variance,
            None => &[],
        };
        let inputs = TermInputs {
            variance,
            elapsed: clock.elapsed,
            premium_short: p.surface_term_premium_short,
            premium_long: p.surface_term_premium_long,
            events: &events,
            event_weight: p.surface_earnings_weight,
        };
        let shape = term_shape(&inputs, n_max);
        let curve = ThetaCurve::new(&shape, clock.elapsed, fit.theta)?;
        Some(Surface {
            root: ContractKind::IndexOption.root().to_string(),
            ssvi: Ssvi::new(fit.rho, eta, gamma),
            curve,
            vix,
            skewness_target: target,
            fit,
        })
    }

    /// The carry `sessions` from now on the index: `(forward, rate,
    /// dividends)`, the forward to the session nearest `now + sessions`, the
    /// mean policy rate the forecast expects to it (a fraction a year), and
    /// the dividends' present value before it, index points. `None` with
    /// `surface_ssvi` off.
    pub fn surface_carry(&self, sessions: f64) -> Option<(f64, f64, f64)> {
        if !self.surface_on() || !sessions.is_finite() {
            return None;
        }
        let clock = self.options_clock();
        let expiry = (clock.now + crate::mathx::max(0.0, sessions)).round() as i64;
        let c = self.carry_at(&clock, expiry, crate::mathx::max(0.0, sessions));
        Some((c.0, c.1, c.2))
    }

    /// `(forward, rate, dividends, index)` to `expiry`, `sessions` away.
    fn carry_at(&self, clock: &Clock, expiry: i64, sessions: f64) -> (f64, f64, f64, f64) {
        let index = self.options_index();
        let rate = self.futures_rate(expiry, clock.now);
        let dividends = self.carry_dividends(expiry, rate, clock.now, clock.after);
        let forward = (index - dividends) * crate::mathx::exp(rate * sessions / 252.0);
        (forward, rate, dividends, index)
    }

    // ── Pricing ───────────────────────────────────────────────────────────

    /// The thetas the dealer quotes each listed expiry from, in listing
    /// order, with the surface's own.
    fn option_thetas(&self, surface: &Surface, clock: &Clock) -> Vec<(f64, f64)> {
        let spec = &INDEX_OPTION_DEALER;
        let vix = surface.vix;
        let base = self.params.option_dealer_spread;
        let mut plain = Vec::with_capacity(self.options.expiries.len());
        let mut shifts = Vec::with_capacity(self.options.expiries.len());
        for e in &self.options.expiries {
            let sessions = e.expiry as f64 - clock.now;
            plain.push((sessions, surface.theta(sessions)));
            shifts.push(spec.inventory_shift(e.pressure, spec.half_spread(base, vix, 0.0)));
        }
        let dealer = dealer_thetas(&plain, &shifts);
        plain.iter().zip(dealer).map(|(&(_, theta), d)| (theta, d)).collect()
    }

    fn expiry_context(&self, clock: &Clock, slot: usize, thetas: &[(f64, f64)]) -> ExpiryContext {
        let e = &self.options.expiries[slot];
        let sessions = crate::mathx::max(0.0, e.expiry as f64 - clock.now);
        let years = sessions / 252.0;
        let (forward, rate, dividends, index) = self.carry_at(clock, e.expiry, sessions);
        let (theta, dealer_theta) = thetas[slot];
        ExpiryContext {
            sessions,
            years,
            rate,
            dividends,
            index,
            carry_spot: index - dividends,
            forward,
            discount: crate::mathx::exp(-rate * years),
            theta,
            dealer_theta,
        }
    }

    /// The dealer's ladder and quote for one option.
    fn option_priced(
        &self,
        surface: &Surface,
        ctx: &ExpiryContext,
        expiry: i64,
        right: Right,
        strike: f64,
    ) -> (Ladder, OptionQuote) {
        let spec = &INDEX_OPTION_DEALER;
        let k = crate::mathx::log(strike / ctx.forward);
        let vol_at = |theta: f64| {
            if ctx.years > 0.0 && theta > 0.0 {
                crate::mathx::sqrt(surface.ssvi.total_variance(k, theta) / ctx.years)
            } else {
                0.0
            }
        };
        let iv = vol_at(ctx.dealer_theta);
        let surface_iv = vol_at(ctx.theta);
        let z = if ctx.theta > 0.0 { k / crate::mathx::sqrt(ctx.theta) } else { 0.0 };
        let half = spec.half_spread(self.params.option_dealer_spread, surface.vix, z);
        let l = ladder(spec, right, ctx.forward, strike, ctx.years, ctx.discount, iv, half);
        let (mid, greeks) = black_greeks(right, ctx.carry_spot, strike, ctx.years, iv, ctx.rate);
        let fair = black_price(right, ctx.forward, strike, ctx.years, surface_iv, ctx.discount);
        let symbol = index_option_symbol(expiry, right, strike);
        let open_interest = self.open_interest(&symbol);
        let q = OptionQuote {
            root: ContractKind::IndexOption.root().to_string(),
            expiry,
            right,
            strike,
            bid: l.bids.first().map(|b| b.price),
            ask: l.asks.first().map(|a| a.price),
            bid_size: l.bids.first().map_or(0.0, |b| b.size),
            ask_size: l.asks.first().map_or(0.0, |a| a.size),
            mid,
            fair,
            iv,
            surface_iv,
            greeks,
            forward: ctx.forward,
            index: ctx.index,
            rate: ctx.rate,
            dividends: ctx.dividends,
            sessions_to_expiry: ctx.sessions,
            multiplier: INDEX_OPTION.multiplier,
            tick: spec.tick_at(mid),
            open_interest,
            initial_margin: None,
            maintenance_margin: None,
            symbol,
        };
        (l, q)
    }

    /// Contracts open in `symbol`: every agent's long position plus the
    /// dealer's, which is the other side of all of theirs.
    fn open_interest(&self, symbol: &str) -> f64 {
        let mut longs = 0.0;
        let mut net = 0.0;
        for (_, ticker, bought, sold) in &self.options.book.flow {
            if ticker == symbol {
                let pos = bought - sold;
                longs += crate::mathx::max(0.0, pos);
                net += pos;
            }
        }
        longs + crate::mathx::max(0.0, -net)
    }

    /// The listed option `symbol` names: its expiry's slot, right and strike.
    fn option_slot(&self, symbol: &str) -> Option<(usize, Right, f64)> {
        let parsed = ContractSymbol::parse(symbol).ok()?;
        if parsed.root != ContractKind::IndexOption.root() {
            return None;
        }
        let SymbolKind::Option { right, .. } = parsed.kind else {
            return None;
        };
        let strike = parsed.strike()?;
        let slot = self.options.expiries.iter().position(|e| e.expiry == parsed.expiry)?;
        if !self.options.expiries[slot].lists(strike) {
            return None;
        }
        Some((slot, right, strike))
    }

    /// A listed option's quote now (`options_index_listed`): `None` for a
    /// symbol not listed and with the switch off, and where the surface does
    /// not price. Its `initial_margin` is the scan's on one short contract
    /// (`margin_scan_coverage`).
    pub fn option_quote(&self, symbol: &str) -> Option<OptionQuote> {
        if !self.options_on() {
            return None;
        }
        let (slot, right, strike) = self.option_slot(symbol)?;
        let clock = self.options_clock();
        let surface = self.surface_at(&clock)?;
        let thetas = self.option_thetas(&surface, &clock);
        let ctx = self.expiry_context(&clock, slot, &thetas);
        let (_, mut q) = self.option_priced(&surface, &ctx, self.options.expiries[slot].expiry, right, strike);
        q.initial_margin = self.option_margin(&[(symbol.to_string(), -1.0)]);
        q.maintenance_margin = q.initial_margin.map(|m| m / super::margin::MAINTENANCE_RATIO);
        Some(q)
    }

    /// Every listed option at `expiry` on `root`, by strike, the call before
    /// the put at each: `None` with the switch off, for an expiry not listed
    /// and where the surface does not price. Margins are left `None`: ask
    /// [`Engine::option_quote`] or [`Engine::option_margin`] for them.
    pub fn chain(&self, root: &str, expiry: i64) -> Option<Vec<OptionQuote>> {
        if !self.options_on() || root != ContractKind::IndexOption.root() {
            return None;
        }
        let slot = self.options.expiries.iter().position(|e| e.expiry == expiry)?;
        let clock = self.options_clock();
        let surface = self.surface_at(&clock)?;
        let thetas = self.option_thetas(&surface, &clock);
        let ctx = self.expiry_context(&clock, slot, &thetas);
        let e = &self.options.expiries[slot];
        let mut out = Vec::with_capacity(2 * (e.hi - e.lo + 1) as usize);
        for strike in e.strikes() {
            for right in [Right::Call, Right::Put] {
                out.push(self.option_priced(&surface, &ctx, expiry, right, strike).1);
            }
        }
        Some(out)
    }

    /// Static-arbitrage violations on every listed chain now (row SV2), and
    /// the surface's own conditions: `None` with the switch off, an empty
    /// list when there are none.
    pub fn option_arbitrage(&self, root: &str) -> Option<Vec<String>> {
        if !self.options_on() || root != ContractKind::IndexOption.root() {
            return None;
        }
        let surface = self.surface(root)?;
        let mut out = Vec::new();
        if let Err(e) = surface.check() {
            out.push(format!("surface: {e}"));
        }
        let mut quotes = Vec::new();
        for e in &self.options.expiries {
            quotes.extend(self.chain(root, e.expiry)?);
        }
        out.extend(chain_violations(&quotes, 0.5 * INDEX_OPTION_DEALER.tick));
        Some(out)
    }

    /// The scan's initial margin on a portfolio of listed options, dollars
    /// (`margin_scan_coverage`): `(symbol, signed contracts)`, symbols not
    /// listed left out. `None` with margin or options off.
    pub fn option_margin(&self, positions: &[(String, f64)]) -> Option<f64> {
        if !self.margin_on() || !self.options_on() {
            return None;
        }
        let clock = self.options_clock();
        let surface = self.surface_at(&clock)?;
        let thetas = self.option_thetas(&surface, &clock);
        let mut scan = Vec::new();
        for (symbol, quantity) in positions {
            if *quantity == 0.0 {
                continue;
            }
            let Some((slot, right, strike)) = self.option_slot(symbol) else {
                continue;
            };
            let ctx = self.expiry_context(&clock, slot, &thetas);
            let k = crate::mathx::log(strike / ctx.forward);
            let vol = if ctx.years > 0.0 && ctx.theta > 0.0 {
                crate::mathx::sqrt(surface.ssvi.total_variance(k, ctx.theta) / ctx.years)
            } else {
                0.0
            };
            scan.push(super::margin::ScanOption {
                right,
                carry_spot: ctx.carry_spot,
                strike,
                years: ctx.years,
                vol,
                rate: ctx.rate,
                units: quantity * INDEX_OPTION.multiplier,
            });
        }
        let index = self.options_index();
        let (index_move, vol_shift) = self.option_scan_size(surface.vix, index);
        Some(super::margin::option_scan_loss(&scan, index_move, vol_shift))
    }

    // ── Listing ───────────────────────────────────────────────────────────

    /// List the expiries due after `after` that are not listed, on the
    /// published VIX and the index, and widen every range around the index.
    fn options_list(&mut self, after: i64) {
        let index = self.options_index();
        let vol = self.published_vix() / 100.0;
        let mut expiries = std::mem::take(&mut self.options.expiries);
        for expiry in calendar::index_option_expiries(after) {
            if !expiries.iter().any(|e| e.expiry == expiry) {
                let step = INDEX_OPTION.strike_step(index, vol, (expiry - after) as f64);
                expiries.push(ListedExpiry { expiry, step, lo: 1, hi: 0, pressure: 0.0 });
            }
        }
        expiries.sort_by_key(|e| e.expiry);
        for e in expiries.iter_mut() {
            e.widen(index, INDEX_OPTION.strikes_each_side);
        }
        self.options.expiries = expiries;
    }

    /// List the first expiries on a fresh engine. Nothing with the switch
    /// off.
    pub(super) fn options_list_initial(&mut self) {
        if !self.options_on() {
            return;
        }
        let after = self.elapsed_days - 1;
        self.options_list(after);
    }

    // ── Orders ────────────────────────────────────────────────────────────

    fn options_push_fill(&mut self, mut fill: AgentFill) -> AgentFill {
        fill.sequence = self.options.book.fill_sequence;
        self.options.book.fill_sequence += 1;
        self.options.book.fills.push(fill.clone());
        fill
    }

    fn options_record_flow(&mut self, agent: &str, symbol: &str, side: Side, quantity: f64) {
        let row = self.options.book.flow.iter_mut().find(|(a, t, _, _)| a == agent && t == symbol);
        let (b, s) = match side {
            Side::Buy => (quantity, 0.0),
            Side::Sell => (0.0, quantity),
        };
        match row {
            Some(r) => {
                r.2 += b;
                r.3 += s;
            }
            None => self.options.book.flow.push((agent.to_string(), symbol.to_string(), b, s)),
        }
    }

    /// An order meets the dealer: it walks the dealer's ladder up to its
    /// limit, each level a taker fill with the dealer, the agent's position
    /// is recorded, and the dealer's pressure on the expiry moves by the
    /// contracts' vega over the at-the-money vega. Returns the fills and the
    /// dealer's mid.
    #[allow(clippy::too_many_arguments)]
    fn options_meet(
        &mut self,
        symbol: &str,
        agent: &str,
        order_id: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
    ) -> (Vec<AgentFill>, f64) {
        let Some((slot, right, strike)) = self.option_slot(symbol) else {
            return (Vec::new(), f64::NAN);
        };
        let clock = self.options_clock();
        let Some(surface) = self.surface_at(&clock) else {
            return (Vec::new(), f64::NAN);
        };
        let thetas = self.option_thetas(&surface, &clock);
        let ctx = self.expiry_context(&clock, slot, &thetas);
        let expiry = self.options.expiries[slot].expiry;
        let (l, q) = self.option_priced(&surface, &ctx, expiry, right, strike);
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
            fills.push(self.options_push_fill(AgentFill {
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
                sequence: 0,
            }));
        }
        if total > 0.0 {
            self.options_record_flow(agent, symbol, side, total);
            // The at-the-money vega of the expiry, per contract.
            let atm = black_greeks(Right::Call, ctx.carry_spot, ctx.forward, ctx.years,
                                   crate::mathx::sqrt(ctx.theta / crate::mathx::max(ctx.years, 1e-12)), ctx.rate).1.vega;
            let sold = match side {
                Side::Buy => total,
                Side::Sell => -total,
            };
            let e = &mut self.options.expiries[slot];
            e.pressure = pressure_after(e.pressure, sold, q.greeks.vega, atm);
        }
        (fills, q.mid)
    }

    fn options_reduce_order(&mut self, order_id: &str, quantity: f64) {
        if let Some(o) = self.options.book.orders.iter_mut().find(|o| o.id == order_id) {
            o.remaining -= quantity;
        }
        self.options.book.orders.retain(|o| o.remaining > 1e-9);
    }

    /// Fill every waiting order the dealer's quote now crosses, in arrival
    /// order, against its ladder up to the order's limit.
    fn options_cross_resting(&mut self) {
        if self.options.book.orders.is_empty() || self.options.phase != OptionsPhase::Session {
            return;
        }
        let ids: Vec<String> = self.options.book.orders.iter().map(|o| o.id.clone()).collect();
        for id in ids {
            let Some(o) = self.options.book.orders.iter().find(|o| o.id == id).cloned() else {
                continue;
            };
            let (fills, _) = self.options_meet(&o.ticker, &o.agent, &o.id, o.side, o.remaining, Some(o.limit));
            let filled: f64 = fills.iter().map(|f| f.quantity).sum();
            if filled > 0.0 {
                self.options_reduce_order(&o.id, filled);
            }
        }
    }

    /// Whether `symbol` names a listed option.
    pub(super) fn is_listed_option(&self, symbol: &str) -> bool {
        self.options_on() && self.option_slot(symbol).is_some()
    }

    /// An agent's order on a listed option, in the session only: a market
    /// order takes what the dealer's ladder shows, a limit order what it
    /// shows at the limit or better, and the rest waits outside any book
    /// until the dealer's quote crosses it.
    pub(super) fn submit_option_order(
        &mut self,
        agent: &str,
        symbol: &str,
        side: Side,
        quantity: f64,
        limit: Option<f64>,
        order_id: Option<String>,
    ) -> Result<OrderReport, String> {
        if self.option_slot(symbol).is_none() {
            return Err(format!("{symbol} is not a listed contract: Engine.contracts() lists the ones that trade"));
        }
        if self.options.phase != OptionsPhase::Session {
            return Err(format!("{symbol} is an index option, which trades in the session only"));
        }
        let id = match order_id {
            Some(id) => {
                if id.is_empty() {
                    return Err("order_id cannot be empty".to_string());
                }
                id
            }
            None => format!("{agent}-{symbol}-{}", self.options.book.sequence),
        };
        if self.contract_order_id_taken(&id) {
            return Err(format!("order id {id:?} is already waiting in the book"));
        }
        let sequence = self.options.book.sequence;
        self.options.book.sequence += 1;
        self.options_cross_resting();
        let (fills, reference) = self.options_meet(symbol, agent, &id, side, quantity, limit);
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
                self.options.book.orders.push(AgentOrder {
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

    pub(super) fn cancel_option_order(&mut self, order_id: &str, agent: Option<&str>) -> bool {
        let before = self.options.book.orders.len();
        self.options.book.orders.retain(|o| !(o.id == order_id && agent.is_none_or(|a| a == o.agent)));
        self.options.book.orders.len() != before
    }

    pub(super) fn option_orders(&self) -> &[AgentOrder] {
        &self.options.book.orders
    }

    pub(super) fn take_option_fills(&mut self, agent: Option<&str>) -> Vec<AgentFill> {
        if self.options.book.fills.is_empty() {
            return Vec::new();
        }
        let (taken, kept): (Vec<_>, Vec<_>) =
            std::mem::take(&mut self.options.book.fills).into_iter().partition(|f| agent.is_none_or(|a| a == f.agent));
        self.options.book.fills = kept;
        taken
    }

    /// The dealer's ladder for a listed option as a book, with every agent's
    /// waiting order on it: what `Engine::contract_book` reads for an
    /// option. `None` for a symbol not listed.
    pub(super) fn option_book(&self, symbol: &str) -> Option<OrderBook> {
        if !self.options_on() {
            return None;
        }
        let (slot, right, strike) = self.option_slot(symbol)?;
        let clock = self.options_clock();
        let surface = self.surface_at(&clock)?;
        let thetas = self.option_thetas(&surface, &clock);
        let ctx = self.expiry_context(&clock, slot, &thetas);
        let (l, q) = self.option_priced(&surface, &ctx, self.options.expiries[slot].expiry, right, strike);
        let mut book = OrderBook::new(symbol.to_string(), Some(q.mid)).with_cap(AGENT_BOOK_CAP);
        if self.options.phase == OptionsPhase::Session {
            for b in &l.bids {
                book.rest_limit(Side::Buy, b.price, b.size, MARKET_MAKER_ID);
            }
            for a in &l.asks {
                book.rest_limit(Side::Sell, a.price, a.size, MARKET_MAKER_ID);
            }
        }
        for o in &self.options.book.orders {
            if o.ticker == symbol {
                book.post_limit(o.side, o.limit, o.remaining, &o.agent, Some(o.id.clone()));
            }
        }
        Some(book)
    }

    // ── Steps and the day's boundaries ────────────────────────────────────

    /// A tick's step: the dealer's pressure decays, and waiting orders its
    /// quote now crosses fill. Nothing with the switch off.
    pub(super) fn options_session_step(&mut self) {
        if !self.options_on() || self.options.phase != OptionsPhase::Session {
            return;
        }
        let d = INDEX_OPTION_DEALER.decay();
        for e in self.options.expiries.iter_mut() {
            e.pressure *= d;
            if e.pressure.abs() < 1e-9 {
                e.pressure = 0.0;
            }
        }
        self.options_cross_resting();
    }

    /// At an open, after the opening prints: each expiry due settles on the
    /// index of those prints, its waiting orders and positions go, the next
    /// expiries are listed and every range widened around the index, and
    /// waiting orders the opening quotes cross fill. The surface's clock
    /// starts the session. Nothing with both switches off.
    pub(super) fn options_open(&mut self) {
        if !self.surface_on() {
            return;
        }
        self.options.phase = OptionsPhase::Session;
        if !self.options_on() {
            return;
        }
        let t = self.elapsed_days;
        let index = self.options_index();
        let mut kept = Vec::with_capacity(self.options.expiries.len());
        for e in std::mem::take(&mut self.options.expiries) {
            if e.expiry <= t {
                let prefix = format!("{}.O{:04}.", ContractKind::IndexOption.root(), e.expiry);
                self.options.book.orders.retain(|o| !o.ticker.starts_with(&prefix));
                self.options.book.flow.retain(|(_, ticker, _, _)| !ticker.starts_with(&prefix));
                self.options.settled.push(SettledExpiry { expiry: e.expiry, reference: index, step: e.step, lo: e.lo, hi: e.hi });
            } else {
                kept.push(e);
            }
        }
        self.options.expiries = kept;
        self.options_list(t);
        self.options_cross_resting();
    }

    /// At a close: the session ends. Nothing with both switches off.
    pub(super) fn options_close(&mut self) {
        if self.surface_on() {
            self.options.phase = OptionsPhase::Night;
        }
    }

    // ── Reads ─────────────────────────────────────────────────────────────

    /// The listed options, by expiry, then strike, the call before the put:
    /// empty with the switch off.
    pub(super) fn option_contracts(&self) -> Vec<ContractSpec> {
        if !self.options_on() {
            return Vec::new();
        }
        let kind = ContractKind::IndexOption;
        let mut out = Vec::new();
        for e in &self.options.expiries {
            for strike in e.strikes() {
                for right in [Right::Call, Right::Put] {
                    out.push(ContractSpec {
                        symbol: index_option_symbol(e.expiry, right, strike),
                        kind,
                        root: kind.root().to_string(),
                        expiry: e.expiry,
                        roll: e.expiry,
                        multiplier: INDEX_OPTION.multiplier,
                        tick: INDEX_OPTION_DEALER.tick,
                        settlement: kind.settlement(),
                        front: false,
                        right: Some(right),
                        strike: Some(strike),
                    });
                }
            }
        }
        out
    }

    /// The options settled at `session`'s open, or every one with `None`:
    /// each listed strike and right, at its intrinsic value on the index of
    /// the opening prints.
    pub(super) fn option_settlements(&self, session: Option<i64>) -> Vec<Settlement> {
        if !self.options_on() {
            return Vec::new();
        }
        let kind = ContractKind::IndexOption;
        let mut out = Vec::new();
        for s in &self.options.settled {
            if session.is_some_and(|d| d != s.expiry) {
                continue;
            }
            for n in s.lo..=s.hi {
                let strike = strike_at(n, s.step);
                for right in [Right::Call, Right::Put] {
                    out.push(Settlement {
                        symbol: index_option_symbol(s.expiry, right, strike),
                        kind,
                        root: kind.root().to_string(),
                        session: s.expiry,
                        value: intrinsic(right, s.reference, strike),
                        reference: s.reference,
                    });
                }
            }
        }
        out
    }

    // ── Snapshot ──────────────────────────────────────────────────────────

    /// The options' numbers for the snapshot and the hash: `None` with
    /// `surface_ssvi` off.
    pub fn options_words(&self) -> Option<Vec<f64>> {
        if self.surface_on() {
            Some(self.options.to_words())
        } else {
            None
        }
    }

    /// Agents' waiting option orders, fills and positions: `None` with the
    /// switch off and while no agent has traded one.
    pub fn options_book_state(&self) -> Option<&BookState> {
        if self.options_on() && !self.options.book.is_pristine() {
            Some(&self.options.book)
        } else {
            None
        }
    }

    /// Put the options back (a restore), each part refused where its switch
    /// is off.
    pub fn set_options_state(&mut self, words: Option<&[f64]>, book: Option<BookState>) -> Result<(), String> {
        if !self.surface_on() {
            if words.is_some() {
                return Err("this snapshot carries the options (options), which only an engine with \
                            surface_ssvi on keeps, and this engine's model has it off"
                    .to_string());
            }
            if book.is_some() {
                return Err("this snapshot carries the options' book (options_book), which only an \
                            engine with options_index_listed on keeps, and this engine's model has it off"
                    .to_string());
            }
            self.options = OptionsState::default();
            return Ok(());
        }
        let Some(words) = words else {
            return Err("an engine with surface_ssvi on carries its options' clock and listing \
                        (options); this snapshot lacks it"
                .to_string());
        };
        if book.is_some() && !self.options_on() {
            return Err("this snapshot carries the options' book (options_book), which only an engine \
                        with options_index_listed on keeps, and this engine's model has it off"
                .to_string());
        }
        let mut state = OptionsState::default();
        state.set_words(words)?;
        if !self.options_on() && !(state.expiries.is_empty() && state.settled.is_empty()) {
            return Err("this snapshot lists index options, which only an engine with \
                        options_index_listed on keeps, and this engine's model has it off"
                .to_string());
        }
        state.book = book.unwrap_or_default();
        self.options = state;
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

    pub(crate) const ON: &[(&str, f64)] = &[
        ("index_level_listed", 1.0),
        ("vix_intraday_live", 1.0),
        ("forecast_horizon_sessions", 252.0),
        ("surface_ssvi", 1.0),
        ("surface_skew_physical", -0.8),
        ("surface_skew_physical_slope", 0.3),
        ("surface_skew_premium", -1.0),
        ("surface_curvature", 1.0),
        ("surface_curvature_exponent", 0.5),
        ("surface_term_premium_short", -0.03),
        ("surface_term_premium_long", 0.1),
        ("surface_earnings_weight", 1.0),
        ("options_index_listed", 1.0),
        ("option_dealer_spread", 0.004),
        ("margin_scan_coverage", 0.99),
    ];

    fn engine(seed: u64, dials: &[(&str, f64)]) -> Engine {
        let mut p = crate::params::PT_V21;
        for (name, value) in dials {
            p = p.with_override(name, *value).unwrap();
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

    /// `n` minutes of the session in progress.
    fn minutes(e: &mut Engine, n: usize, buffer: &mut SessionBuffer) {
        e.run_session(&SessionRequest::new(crate::market::GameTime::new(9, 30, 3), n), buffer);
    }

    #[test]
    fn off_nothing_is_listed_and_nothing_is_kept() {
        let mut e = engine(3, &[]);
        let mut buffer = SessionBuffer::new();
        day(&mut e, 0, &mut buffer);
        assert!(e.surface("IDX").is_none());
        assert!(e.chain("IDX", 14).is_none());
        assert!(e.option_quote("IDX.O0014.C1000.00").is_none());
        assert!(e.options_words().is_none());
        assert!(e.options_book_state().is_none());
        assert!(e.contracts().is_empty());
    }

    #[test]
    fn sv1_the_strip_reads_the_published_vix_at_every_close() {
        let mut e = engine(5, ON);
        let mut buffer = SessionBuffer::new();
        for d in 0..30 {
            day(&mut e, d, &mut buffer);
            let s = e.surface("IDX").unwrap();
            let m = s.strip(21.0).unwrap();
            assert!((m.vix - e.published_vix()).abs() <= 1e-6, "{d}: {} {}", m.vix, e.published_vix());
            s.check().unwrap();
            // The skewness asked is met (SKEW = 100 - 10 S).
            assert!(s.fit.reached);
            assert!((m.skewness - s.skewness_target.unwrap()).abs() < 1e-6);
        }
    }

    #[test]
    fn the_chain_is_listed_priced_and_free_of_static_arbitrage() {
        let mut e = engine(7, ON);
        let mut buffer = SessionBuffer::new();
        let listed = e.contracts();
        let want: i64 = e.options.expiries.iter().map(|x| 2 * (x.hi - x.lo + 1)).sum();
        assert_eq!(listed.len() as i64, want);
        // Twenty strikes either side of the index, short of the grid's floor.
        assert!(e.options.expiries.iter().all(|x| x.hi - x.lo + 1 >= 40), "{:?}", e.options.expiries);
        assert_eq!(e.options.expiries.len(), 8);
        for d in 0..25 {
            day(&mut e, d, &mut buffer);
            assert_eq!(e.option_arbitrage("IDX").unwrap(), Vec::<String>::new(), "{d}");
        }
        // Mid-session too, on the live VIX.
        e.set_current_day(25);
        e.open_market();
        minutes(&mut e, 100, &mut buffer);
        assert_eq!(e.option_arbitrage("IDX").unwrap(), Vec::<String>::new());
        let expiry = e.options.expiries[0].expiry;
        let chain = e.chain("IDX", expiry).unwrap();
        for q in &chain {
            assert!(q.ask.unwrap() > q.mid);
            if let Some(b) = q.bid {
                assert!(b < q.mid);
            }
            assert!(q.iv > 0.0 && (q.iv - q.surface_iv).abs() < 1e-15, "no inventory, no shift");
            assert_eq!(q.open_interest, 0.0);
        }
    }

    #[test]
    fn options_settle_on_the_opening_prints_and_the_next_expiry_lists() {
        let mut e = engine(9, ON);
        let mut buffer = SessionBuffer::new();
        for d in 0..14 {
            day(&mut e, d, &mut buffer);
        }
        assert_eq!(e.options.expiries[0].expiry, 14);
        e.set_current_day(14);
        e.open_market();
        let opening = e.index_level().unwrap().level;
        let s: Vec<Settlement> = e.settlements(Some(14)).into_iter().filter(|s| s.kind == ContractKind::IndexOption).collect();
        let settled = e.options.settled.last().unwrap();
        assert_eq!(settled.expiry, 14);
        assert_eq!(s.len() as i64, 2 * (settled.hi - settled.lo + 1));
        for x in &s {
            assert_eq!(x.reference.to_bits(), opening.to_bits());
            let c = ContractSymbol::parse(&x.symbol).unwrap();
            let right = match c.kind {
                SymbolKind::Option { right, .. } => right,
                _ => unreachable!(),
            };
            assert_eq!(x.value, intrinsic(right, opening, c.strike().unwrap()));
        }
        assert_eq!(e.options.expiries.len(), 8);
        assert!(e.options.expiries.iter().all(|x| x.expiry > 14));
        assert!(e.option_quote("IDX.O0014.C1000.00").is_none());
    }

    #[test]
    fn a_trade_fills_at_the_dealers_quote_moves_its_volatility_and_opens_interest() {
        let mut e = engine(11, ON);
        let mut buffer = SessionBuffer::new();
        for d in 0..3 {
            day(&mut e, d, &mut buffer);
        }
        e.set_current_day(3);
        e.open_market();
        let expiry = e.options.expiries[1].expiry;
        let atm = e.chain("IDX", expiry).unwrap().into_iter()
            .min_by(|a, b| (a.strike - a.forward).abs().total_cmp(&(b.strike - b.forward).abs()))
            .unwrap();
        let sym = index_option_symbol(expiry, Right::Call, atm.strike);
        let before = e.option_quote(&sym).unwrap();
        let r = e.submit_order("a", &sym, Side::Buy, 120.0, None, None).unwrap();
        assert_eq!(r.filled, 120.0);
        assert_eq!(r.fills[0].price, before.ask.unwrap());
        assert!(r.fills.iter().all(|f| f.counterparty == MARKET_MAKER_ID));
        let after = e.option_quote(&sym).unwrap();
        assert!(after.iv > before.iv, "the dealer is short and raises its volatility");
        assert_eq!(after.open_interest, 120.0);
        // Parity holds through the pressure: the put at the strike moved too.
        let put = e.option_quote(&index_option_symbol(expiry, Right::Put, atm.strike)).unwrap();
        assert_eq!(put.iv.to_bits(), after.iv.to_bits());
        // Another agent sells 50 back: open interest counts both holders' longs.
        e.submit_order("b", &sym, Side::Sell, 50.0, None, None).unwrap();
        assert_eq!(e.option_quote(&sym).unwrap().open_interest, 120.0);
        assert_eq!(e.option_arbitrage("IDX").unwrap(), Vec::<String>::new());
        // The pressure decays over the session.
        minutes(&mut e, 200, &mut buffer);
        let late = e.option_quote(&sym).unwrap();
        assert!((late.iv - late.surface_iv).abs() < (after.iv - after.surface_iv).abs() / 100.0);
        // A limit below the ask waits, and fills once the dealer's ask reaches it.
        let q = e.option_quote(&sym).unwrap();
        let r = e.submit_order("c", &sym, Side::Buy, 5.0, Some(q.bid.unwrap()), None).unwrap();
        assert_eq!(r.filled, 0.0);
        assert_eq!(r.resting, 5.0);
        assert_eq!(e.open_orders(Some("c")).len(), 1);
        assert!(e.cancel_order(&r.order_id, Some("c")));
        let taken = e.take_fills(Some("a"));
        assert_eq!(taken.iter().map(|f| f.quantity).sum::<f64>(), 120.0);
    }

    #[test]
    fn the_margin_scans_shorts_and_nets_spreads() {
        let mut e = engine(13, ON);
        let mut buffer = SessionBuffer::new();
        for d in 0..3 {
            day(&mut e, d, &mut buffer);
        }
        let expiry = e.options.expiries[0].expiry;
        let chain = e.chain("IDX", expiry).unwrap();
        let atm = chain.iter().min_by(|a, b| (a.strike - a.forward).abs().total_cmp(&(b.strike - b.forward).abs())).unwrap();
        let short = index_option_symbol(expiry, Right::Put, atm.strike);
        let q = e.option_quote(&short).unwrap();
        let m = q.initial_margin.unwrap();
        assert!(m > 0.0);
        assert!((q.maintenance_margin.unwrap() - m / 1.1).abs() < 1e-9 * m);
        // Long the same: no more than its premium; hedged: nothing.
        let long = e.option_margin(&[(short.clone(), 1.0)]).unwrap();
        assert!(long <= q.fair * 100.0);
        assert_eq!(e.option_margin(&[(short.clone(), -1.0), (short.clone(), 1.0)]).unwrap(), 0.0);
        // Ten shorts ask ten times one.
        let ten = e.option_margin(&[(short, -10.0)]).unwrap();
        assert!((ten - 10.0 * m).abs() < 1e-9 * ten);
    }

    #[test]
    fn the_words_round_trip_and_are_refused_where_the_switch_is_off() {
        let mut e = engine(17, ON);
        let mut buffer = SessionBuffer::new();
        for d in 0..3 {
            day(&mut e, d, &mut buffer);
        }
        e.set_current_day(3);
        e.open_market();
        let expiry = e.options.expiries[0].expiry;
        let sym = e.chain("IDX", expiry).unwrap()[40].symbol.clone();
        e.submit_order("a", &sym, Side::Buy, 10.0, None, None).unwrap();
        let words = e.options_words().unwrap();
        let book = e.options_book_state().cloned();
        assert!(book.is_some());
        let mut other = engine(99, ON);
        other.set_options_state(Some(&words), book.clone()).unwrap();
        assert_eq!(other.options_words().unwrap(), words);
        let mut off = engine(17, &[]);
        assert!(off.set_options_state(Some(&words), None).is_err());
        let mut short = OptionsState::default();
        assert!(short.set_words(&words[..words.len() - 1]).is_err());
    }
}
