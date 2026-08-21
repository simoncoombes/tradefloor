//! The engine surface — Layer 2 — behind the `python` feature.
//!
//! Layer 1 gives you the pieces: a valuation, a mispricing process, a book.
//! This steps a whole market: many instruments, a shared factor structure, an
//! order book per name, and macro state that evolves day to day.
//!
//! # Units
//!
//! Rates crossing this boundary are FRACTIONAL, as everywhere else in the
//! package. The conversion to the core's percent denomination happens here,
//! once, in [`PyMacro::to_core`].
//!
//! # Output is columnar and f64, always
//!
//! Per-tick Python objects are unusable at this library's scale: one seed at
//! tick grain for 100 names over a trading year is roughly 9.8 million rows.
//! So results come back as columns of raw little-endian f64 bytes, which
//! `numpy.frombuffer` adopts without copying.
//!
//! There is no f32 option and there will not be one. Bit-exactness is the
//! product: the known-answer gate hashes these buffers, and a half-precision
//! "memory-saving" variant would be a different market that happens to plot
//! the same — a silent parity-breaking switch in the public API. Downcast
//! your own copy after the bits leave the library.

#![allow(unexpected_cfgs, clippy::useless_conversion)]

use pyo3::prelude::*;
use pyo3::types::PyBytes;

use crate::economy::{create_initial_central_bank_state, create_initial_economy_state};
use crate::economy::{CyclePhase, InitialEconomyOptions};
use crate::engine::{Engine, PriceField, SessionBuffer, SessionRequest, TickRequest};
use crate::engine::{DayCloseRequest, TickOutcome};
use crate::market::{GameTime, NewsEvent, NewsImpactEntry, OrderVolume, TickCompany, TickStock};
use crate::python::ValidationError;

/// Serialise a column as little-endian f64 bytes.
///
/// Little-endian because that is what `numpy.frombuffer(buf, dtype="<f8")`
/// reads with no conversion on every platform the wheels target. The known
/// answer test uses big-endian for hashing, deliberately a separate choice:
/// there the byte order is part of a canonical form, here it is chosen to be
/// free for the consumer.
fn f64_bytes(py: Python<'_>, values: &[f64]) -> Py<PyBytes> {
    let mut out = Vec::with_capacity(values.len() * 8);
    for v in values {
        out.extend_from_slice(&v.to_le_bytes());
    }
    PyBytes::new_bound(py, &out).into()
}

/// One listed instrument.
///
/// # `market_cap` is derived, not given
///
/// It is definitionally `price x shares_outstanding`, and the spread tier is
/// selected from it. If the API accepted all three, a caller could pass an
/// inconsistent triple and the liquidity of a name would quietly disagree with
/// its priced value. So price and shares are the inputs; market cap follows,
/// and keeps following as price moves.
#[pyclass(name = "Instrument", module = "pretium._core", get_all)]
#[derive(Debug, Clone)]
pub struct PyInstrument {
    pub ticker: String,
    pub sector: String,
    pub initial_price: f64,
    pub shares_outstanding: f64,
    pub eps: Option<f64>,
    pub book_value_per_share: Option<f64>,
    pub revenue_growth: Option<f64>,
    pub avg_volume: f64,
    pub beta: f64,
    pub short_interest: f64,
}

#[pymethods]
impl PyInstrument {
    #[new]
    #[pyo3(signature = (
        ticker, sector, *, initial_price, shares_outstanding,
        eps = None, book_value_per_share = None, revenue_growth = None,
        avg_volume = 1_000_000.0, beta = 1.0, short_interest = 0.0
    ))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        ticker: &str,
        sector: &str,
        initial_price: f64,
        shares_outstanding: f64,
        eps: Option<f64>,
        book_value_per_share: Option<f64>,
        revenue_growth: Option<f64>,
        avg_volume: f64,
        beta: f64,
        short_interest: f64,
    ) -> PyResult<Self> {
        if crate::sectors::by_key(sector).is_none() {
            return Err(ValidationError::new_err(format!(
                "unknown sector {sector:?}. Valid sectors: {}",
                crate::sectors::keys().join(", ")
            )));
        }
        for (name, v) in [
            ("initial_price", initial_price),
            ("shares_outstanding", shares_outstanding),
        ] {
            if !(v > 0.0) || !v.is_finite() {
                return Err(ValidationError::new_err(format!(
                    "{name} must be finite and greater than zero, got {v}"
                )));
            }
        }
        for (name, v) in [
            ("avg_volume", avg_volume),
            ("beta", beta),
            ("short_interest", short_interest),
        ] {
            if !v.is_finite() || v < 0.0 {
                return Err(ValidationError::new_err(format!(
                    "{name} must be finite and not negative, got {v}"
                )));
            }
        }
        for (name, v) in [
            ("eps", eps),
            ("book_value_per_share", book_value_per_share),
            ("revenue_growth", revenue_growth),
        ] {
            if let Some(v) = v {
                if !v.is_finite() {
                    return Err(ValidationError::new_err(format!(
                        "{name} must be finite, got {v}"
                    )));
                }
            }
        }
        Ok(Self {
            ticker: ticker.to_string(),
            sector: sector.to_string(),
            initial_price,
            shares_outstanding,
            eps,
            book_value_per_share,
            revenue_growth,
            avg_volume,
            beta,
            short_interest,
        })
    }

    /// Derived: `initial_price * shares_outstanding`.
    #[getter]
    fn market_cap(&self) -> f64 {
        self.initial_price * self.shares_outstanding
    }

    fn __repr__(&self) -> String {
        format!(
            "Instrument({:?}, sector={:?}, price={})",
            self.ticker, self.sector, self.initial_price
        )
    }
}

impl PyInstrument {
    /// Shared with the batch surface.
    pub fn to_core_public(&self, index: usize) -> TickCompany {
        self.to_core(index)
    }

    fn to_core(&self, index: usize) -> TickCompany {
        let sector = crate::sectors::by_key(&self.sector).expect("validated at construction");
        TickCompany {
            id: format!("{}-{index}", self.ticker),
            ticker: self.ticker.clone(),
            sector: self.sector.clone(),
            is_bankrupt: false,
            is_public: true,
            stock: TickStock {
                price: self.initial_price,
                // A fresh instrument has not traded, so yesterday's close is
                // today's opening mark rather than a fabricated gap.
                previous_close: self.initial_price,
                // None, NOT the price. The reference field is optional and
                // absent until the first tick prints; substituting a value
                // here diverges on tick zero of every freshly built roster.
                previous_tick_price: None,
                open: self.initial_price,
                high: self.initial_price,
                low: self.initial_price,
                volume: 0.0,
                avg_volume: self.avg_volume,
                shares_outstanding: self.shares_outstanding,
                market_cap: self.market_cap(),
                mispricing_s: None,
                mispricing_s_prev_close: None,
                mispricing_momentum: None,
                maker_inventory: None,
                // From daily_sigma squared, never from the relative
                // `volatility` multiplier -- see the sector table.
                garch_variance: sector.base_daily_variance(),
                last_daily_return: None,
                beta: Some(self.beta),
                short_interest: self.short_interest,
                // Full float unless told otherwise. Only the squeeze path
                // reads it, via short_interest / float.
                float: self.shares_outstanding,
            },
            sector_volatility: Some(sector.volatility),
            sector_avg_pe: Some(sector.avg_pe),
            eps: self.eps,
            book_value_per_share: self.book_value_per_share,
            revenue_growth: self.revenue_growth,
        }
    }
}

/// The macro state the price loop reads.
///
/// Rates are FRACTIONAL here and converted once, in `to_core`.
#[pyclass(name = "Macro", module = "pretium._core", get_all)]
#[derive(Debug, Clone)]
pub struct PyMacro {
    pub vix: f64,
    pub federal_funds_rate: f64,
    pub corporate_bond_yield: Option<f64>,
    pub inflation_rate: f64,
    pub qe_pe_boost: f64,
    pub fear_greed_index: f64,
    pub cycle: String,
}

#[pymethods]
impl PyMacro {
    #[new]
    #[pyo3(signature = (
        *, vix = 15.0, federal_funds_rate = 0.025, corporate_bond_yield = None,
        inflation_rate = 0.02, qe_pe_boost = 0.0, fear_greed_index = 50.0,
        cycle = "expansion"
    ))]
    fn new(
        vix: f64,
        federal_funds_rate: f64,
        corporate_bond_yield: Option<f64>,
        inflation_rate: f64,
        qe_pe_boost: f64,
        fear_greed_index: f64,
        cycle: &str,
    ) -> PyResult<Self> {
        if CyclePhase::from_name(cycle).is_none() {
            return Err(ValidationError::new_err(format!(
                "unknown cycle {cycle:?}. Valid: expansion, peak, contraction, trough, recovery"
            )));
        }
        crate::units::check_rate("federal_funds_rate", federal_funds_rate)
            .map_err(ValidationError::new_err)?;
        crate::units::check_rate("inflation_rate", inflation_rate)
            .map_err(ValidationError::new_err)?;
        if let Some(y) = corporate_bond_yield {
            crate::units::check_rate("corporate_bond_yield", y).map_err(ValidationError::new_err)?;
        }
        for (name, v) in [
            ("vix", vix),
            ("qe_pe_boost", qe_pe_boost),
            ("fear_greed_index", fear_greed_index),
        ] {
            if !v.is_finite() {
                return Err(ValidationError::new_err(format!(
                    "{name} must be finite, got {v}"
                )));
            }
        }
        Ok(Self {
            vix,
            federal_funds_rate,
            corporate_bond_yield,
            inflation_rate,
            qe_pe_boost,
            fear_greed_index,
            cycle: cycle.to_string(),
        })
    }

    fn __repr__(&self) -> String {
        format!(
            "Macro(vix={}, federal_funds_rate={}, cycle={:?})",
            self.vix, self.federal_funds_rate, self.cycle
        )
    }
}

impl PyEngine {
    /// Resolve a ticker to the engine's internal company id.
    ///
    /// Needed because the two tick inputs key differently: order volumes match
    /// on TICKER, while news matches on the internal ID. That asymmetry is the
    /// reference behaviour and is not worth exporting, so the Python API takes
    /// a ticker for both and this does the translation.
    ///
    /// Resolved through the LIVE roster rather than by rebuilding the id from
    /// a ticker and an index: ids carry the index a company had when it was
    /// created, which stops matching its position as soon as anything is
    /// delisted.
    /// Reverse of `id_for`: the ticker an internal company id belongs to.
    ///
    /// The log carries tickers rather than ids on purpose. An id like `AAA-0`
    /// is an implementation detail, and its embedded index stops matching the
    /// company's position after any delisting -- so a log full of them would
    /// be both opaque and, after a roster edit, misleading.
    fn ticker_for_id(&self, id: &str) -> Option<String> {
        let pos = self.inner.ids().iter().position(|i| i == id)?;
        self.tickers.get(pos).cloned()
    }

    fn id_for(&self, ticker: &str) -> Option<String> {
        let pos = self.tickers.iter().position(|t| t == ticker)?;
        self.inner.ids().get(pos).cloned()
    }

    fn build_news(&self, news: Option<Vec<PyNews>>) -> PyResult<Vec<NewsEvent>> {
        let Some(items) = news else { return Ok(Vec::new()) };
        let mut out = Vec::with_capacity(items.len());
        for n in items {
            let company_id = match n.ticker.as_deref() {
                Some(t) => Some(self.id_for(t).ok_or_else(|| {
                    ValidationError::new_err(format!(
                        "no instrument with ticker {t:?} in this universe"
                    ))
                })?),
                None => None,
            };
            out.push(NewsEvent {
                company_id,
                sector: n.sector.clone(),
                // Some(), so a genuine zero reaches the truthy-or in the
                // factor model and contributes nothing -- which is what the
                // reference does with a zero impact.
                price_impact: Some(n.price_impact),
            });
        }
        Ok(out)
    }

    fn build_impacts(
        &self,
        impacts: Option<Vec<PyNewsImpact>>,
    ) -> PyResult<Vec<NewsImpactEntry>> {
        let Some(items) = impacts else { return Ok(Vec::new()) };
        let mut out = Vec::with_capacity(items.len());
        for i in items {
            let company_id = match i.ticker.as_deref() {
                Some(t) => Some(self.id_for(t).ok_or_else(|| {
                    ValidationError::new_err(format!(
                        "no instrument with ticker {t:?} in this universe"
                    ))
                })?),
                None => None,
            };
            out.push(NewsImpactEntry {
                company_id,
                sector: i.sector.clone(),
                sectors: i.sectors.clone(),
                remaining_impact: i.remaining_impact,
                reversal_phase: i.reversal_phase,
            });
        }
        Ok(out)
    }

    /// Order flow, keyed by ticker.
    ///
    /// An unknown ticker is an error rather than being ignored. Silently
    /// dropping flow would mean a study believing it had applied pressure
    /// that never reached the book, and nothing would say so.
    fn build_flow(
        &self,
        flow: Option<std::collections::HashMap<String, (f64, f64)>>,
    ) -> PyResult<Vec<(String, OrderVolume)>> {
        let Some(map) = flow else { return Ok(Vec::new()) };
        let mut out = Vec::with_capacity(map.len());
        // Sorted, so the vector this builds does not depend on HashMap
        // iteration order. The engine looks flow up by ticker rather than
        // walking it, so order does not currently reach the market -- but a
        // structure whose contents depend on hash ordering is one refactor
        // away from doing so, and that would be a platform-dependent market.
        let mut keys: Vec<&String> = map.keys().collect();
        keys.sort();
        for ticker in keys {
            let (buy, sell) = map[ticker];
            if !buy.is_finite() || !sell.is_finite() || buy < 0.0 || sell < 0.0 {
                return Err(ValidationError::new_err(format!(
                    "order flow for {ticker:?} must be finite and not negative, got ({buy}, {sell})"
                )));
            }
            if !self.tickers.iter().any(|t| t == ticker) {
                return Err(ValidationError::new_err(format!(
                    "no instrument with ticker {ticker:?} in this universe"
                )));
            }
            out.push((ticker.clone(), OrderVolume { buy, sell }));
        }
        Ok(out)
    }

    /// The portion of a session buffer this session actually wrote.
    ///
    /// Written with an `if` rather than `usize::min`, which would be perfectly
    /// safe here: the crate-wide guard bans `.min(` textually because
    /// `f64::min` swallows NaN where JavaScript's propagates it, and it cannot
    /// tell an integer min from a float one. Weakening the guard to allow this
    /// one call would be a bad trade -- it exists so nobody has to remember
    /// the distinction, and an exemption is exactly how the four `f64::max`
    /// calls got into a supposedly-guarded crate the first time.
    fn written<'a>(&self, buf: &'a [f64]) -> &'a [f64] {
        let n = self.buffer.ticks_written * self.buffer.companies;
        let end = if n > buf.len() { buf.len() } else { n };
        &buf[..end]
    }
}

/// Build the core economy from an optional macro state.
///
/// Shared between the single engine and the batch so the two cannot drift:
/// a batch whose default macro differed from a single engine's would make
/// `EngineBatch([s])` and `Engine(s)` different markets, silently.
pub fn economy_from(
    macro_state: Option<PyMacro>,
) -> PyResult<crate::economy::EconomyState> {
    Ok(match macro_state {
        Some(m) => m.to_core(),
        None => PyMacro::new(15.0, 0.025, None, 0.02, 0.0, 50.0, "expansion")?.to_core(),
    })
}

impl PyMacro {
    fn to_core(&self) -> crate::economy::EconomyState {
        let mut e = create_initial_economy_state(&InitialEconomyOptions::default());
        e.vix = self.vix;
        // x100: the boundary is fractional, the core is percent. This is the
        // only place in this file that conversion happens.
        e.federal_funds_rate = crate::units::fraction_to_percent(self.federal_funds_rate);
        e.inflation_rate = crate::units::fraction_to_percent(self.inflation_rate);
        e.corporate_bond_yield = self
            .corporate_bond_yield
            .map(crate::units::fraction_to_percent)
            // Absent means "use the policy rate", which the valuation already
            // does via its own nullish fallback. Setting it to the converted
            // policy rate here would be the same number by a different route,
            // so it is left as the core's own initial value and the fallback
            // does its job.
            .unwrap_or(e.corporate_bond_yield);
        // NOT converted: a multiplier delta, fractional in both.
        e.qe_pe_boost = self.qe_pe_boost;
        e.fear_greed_index = self.fear_greed_index;
        e.cycle_phase = CyclePhase::from_name(&self.cycle).expect("validated at construction");
        e
    }
}

/// What one tick produced.
#[pyclass(name = "TickResult", module = "pretium._core", frozen, get_all)]
#[derive(Debug, Clone)]
pub struct PyTickResult {
    /// "open", "pre_market", "after_hours" or "closed".
    pub market_status: String,
    /// Draws consumed. Zero when the market was closed.
    pub draws_consumed: usize,
    /// How many instruments were active this tick.
    pub active: usize,
}

#[pymethods]
impl PyTickResult {
    fn __repr__(&self) -> String {
        format!(
            "TickResult(market_status={:?}, draws_consumed={}, active={})",
            self.market_status, self.draws_consumed, self.active
        )
    }
}

fn status_name(s: crate::market::MarketStatus) -> &'static str {
    use crate::market::MarketStatus::*;
    match s {
        Open => "open",
        PreMarket => "pre_market",
        AfterHours => "after_hours",
        Closed => "closed",
    }
}

/// Shared with the batch surface, which parses the same field names.
pub fn parse_field_public(name: &str) -> PyResult<PriceField> {
    parse_field(name)
}

fn parse_field(name: &str) -> PyResult<PriceField> {
    Ok(match name {
        "price" => PriceField::Price,
        "previous_close" => PriceField::PreviousClose,
        "open" => PriceField::Open,
        "high" => PriceField::High,
        "low" => PriceField::Low,
        "volume" => PriceField::Volume,
        "market_cap" => PriceField::MarketCap,
        "mispricing_s" => PriceField::MispricingS,
        "maker_inventory" => PriceField::MakerInventory,
        "garch_variance" => PriceField::GarchVariance,
        other => {
            return Err(ValidationError::new_err(format!(
                "unknown field {other:?}. Valid: price, previous_close, open, high, \
                 low, volume, market_cap, mispricing_s, maker_inventory, garch_variance"
            )))
        }
    })
}

/// A whole market, stepped through time.
#[pyclass(name = "Engine", module = "pretium._core")]
pub struct PyEngine {
    inner: Engine,
    buffer: SessionBuffer,
    tickers: Vec<String>,
    /// Recorded per-day batches.
    ///
    /// The session buffer is REUSED every session, so anything not captured
    /// before the next `run_session` is gone. Recording is therefore explicit
    /// rather than automatic: a caller who wants a multi-day table asks for
    /// it, and a caller who does not pays neither the memory nor the copy.
    ///
    /// This is also what makes the results surface stream. One seed at tick
    /// grain for 100 names over a trading year is ~9.8 million rows per table;
    /// as one batch that is a memory problem, as 252 daily batches it is a
    /// pull protocol the consumer drives.
    recorded: Vec<crate::python_arrow::RecordedDay>,
    recorded_macro: Vec<crate::python_arrow::MacroRow>,
    /// Recorded order-book depth. Empty unless a caller asks for it.
    recorded_book: Vec<crate::python_arrow::BookRow>,
    /// Every input that crossed into this engine, in order.
    ///
    /// Inputs only. Prices, attribution and draw counts are consequences,
    /// and recording them would create a second source of truth that could
    /// disagree with the first.
    log: Vec<crate::python_log::LogEntry>,
}

#[pymethods]
impl PyEngine {
    /// Build an engine over a universe.
    ///
    /// `seed` is required, never defaulted. A simulator that seeds itself from
    /// the clock when you forget produces a run nobody can reproduce, and the
    /// failure is invisible until someone tries.
    #[new]
    #[pyo3(signature = (*, seed, universe, macro_state = None))]
    fn new(seed: u32, universe: Vec<PyInstrument>, macro_state: Option<PyMacro>) -> PyResult<Self> {
        if universe.is_empty() {
            return Err(ValidationError::new_err(
                "universe is empty - an engine with no instruments has nothing to simulate",
            ));
        }
        let economy = economy_from(macro_state)?;
        let companies: Vec<TickCompany> = universe
            .iter()
            .enumerate()
            .map(|(i, inst)| inst.to_core(i))
            .collect();
        let tickers = universe.iter().map(|i| i.ticker.clone()).collect();

        Ok(Self {
            inner: Engine::new(
                seed,
                companies,
                economy,
                create_initial_central_bank_state(0),
                crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
            ),
            buffer: SessionBuffer::new(),
            tickers,
            recorded: Vec::new(),
            recorded_macro: Vec::new(),
            recorded_book: Vec::new(),
            log: Vec::new(),
        })
    }

    /// Roll the day's opening marks. Call once before the session's ticks.
    fn open_market(&mut self) {
        self.log.push(crate::python_log::LogEntry::OpenMarket);
        self.inner.open_market();
    }

    /// Advance one game-minute.
    ///
    /// A closed market costs nothing and draws nothing, which is why a caller
    /// may tick straight through a weekend without special-casing it.
    #[pyo3(signature = (
        hour, minute, day_of_week, *, volatility = 1.0,
        news = None, news_impacts = None, order_flow = None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn tick(
        &mut self,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        volatility: f64,
        news: Option<Vec<PyNews>>,
        news_impacts: Option<Vec<PyNewsImpact>>,
        order_flow: Option<std::collections::HashMap<String, (f64, f64)>>,
    ) -> PyResult<PyTickResult> {
        if !(0..24).contains(&hour) || !(0..60).contains(&minute) {
            return Err(ValidationError::new_err(format!(
                "invalid time {hour:02}:{minute:02}"
            )));
        }
        if !(0..7).contains(&day_of_week) {
            return Err(ValidationError::new_err(format!(
                "day_of_week must be 0 (Sunday) to 6 (Saturday), got {day_of_week}"
            )));
        }
        if !volatility.is_finite() || volatility < 0.0 {
            return Err(ValidationError::new_err(format!(
                "volatility must be finite and not negative, got {volatility}"
            )));
        }
        let news = self.build_news(news)?;
        let impacts = self.build_impacts(news_impacts)?;
        let flow = self.build_flow(order_flow)?;
        // Logged AFTER validation, so a rejected call is not in the log.
        // A log containing a call that never happened would replay into a
        // different market than the one it claims to describe.
        self.log.push(crate::python_log::LogEntry::Tick {
            hour,
            minute,
            day_of_week,
            volatility,
            news: news
                .iter()
                .map(|n| {
                    (
                        n.company_id.as_deref().and_then(|i| self.ticker_for_id(i)),
                        n.sector.clone(),
                        n.price_impact.unwrap_or(0.0),
                    )
                })
                .collect(),
            flow: flow.iter().map(|(t, v)| (t.clone(), v.buy, v.sell)).collect(),
        });
        let outcome: TickOutcome = self.inner.tick(&TickRequest {
            time: GameTime {
                hour,
                minute,
                day_of_week,
            },
            volatility_multiplier: volatility,
            news: &news,
            news_impact_queue: &impacts,
            order_volumes: &flow,
        });
        Ok(PyTickResult {
            market_status: status_name(outcome.market_status).to_string(),
            draws_consumed: outcome.draws_consumed,
            active: outcome.active_indices.len(),
        })
    }

    /// Run many ticks in one crossing of the boundary.
    ///
    /// The reason this exists: 390 ticks a day through per-call marshalling is
    /// 390 boundary crossings, and the hot loop belongs in Rust. Identical
    /// results to calling `tick` in a loop -- asserted by a test, because a
    /// faster path that is not the same simulation is a second engine wearing
    /// the same name.
    ///
    /// Returns the number of ticks written.
    #[pyo3(signature = (
        hour, minute, day_of_week, ticks, *, volatility = 1.0,
        close_at_end = false, news = None, news_impacts = None, order_flow = None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn run_session(
        &mut self,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        ticks: usize,
        volatility: f64,
        close_at_end: bool,
        // Held for the WHOLE session rather than being applied on the first
        // tick and then dropped. That matches how the engine reads them -- the
        // impact queue is a standing residue, not an impulse -- but it does
        // mean a one-off news item belongs in `tick`, not here.
        news: Option<Vec<PyNews>>,
        news_impacts: Option<Vec<PyNewsImpact>>,
        order_flow: Option<std::collections::HashMap<String, (f64, f64)>>,
    ) -> PyResult<usize> {
        if ticks == 0 {
            return Err(ValidationError::new_err("ticks must be greater than zero"));
        }
        let session_news = self.build_news(news)?;
        let session_impacts = self.build_impacts(news_impacts)?;
        let session_flow = self.build_flow(order_flow)?;
        self.log.push(crate::python_log::LogEntry::RunSession {
            hour,
            minute,
            day_of_week,
            ticks,
            volatility,
            close_at_end,
            news: session_news
                .iter()
                .map(|n| {
                    (
                        n.company_id.as_deref().and_then(|i| self.ticker_for_id(i)),
                        n.sector.clone(),
                        n.price_impact.unwrap_or(0.0),
                    )
                })
                .collect(),
            flow: session_flow
                .iter()
                .map(|(t, v)| (t.clone(), v.buy, v.sell))
                .collect(),
        });

        let n = self.inner.len();
        let innovations: Vec<Option<f64>> = vec![None; n];
        let variances: Vec<f64> = self
            .inner
            .companies()
            .iter()
            .map(|c| {
                crate::sectors::by_key(&c.sector)
                    .map(|s| s.base_daily_variance())
                    .unwrap_or(0.000225)
            })
            .collect();

        let outcome = self.inner.run_session(
            &SessionRequest {
                start: GameTime {
                    hour,
                    minute,
                    day_of_week,
                },
                ticks,
                volatility_multiplier: volatility,
                news: &session_news,
                news_impact_queue: &session_impacts,
                order_volumes: &session_flow,
                close_at_end,
                daily_innovations: &innovations,
                sector_base_variances: &variances,
                stop: None,
            },
            &mut self.buffer,
        );
        let _ = outcome;
        Ok(self.buffer.ticks_written)
    }

    /// Advance whole days: open, session, close, repeat.
    ///
    /// The backtest shape. Decisions daily or slower means one call for the
    /// whole span rather than a Python loop over sessions, and it records each
    /// day as it goes so the results tables stream.
    ///
    /// Measured, so the claim is honest: the boundary crossing this saves
    /// costs 0.357 microseconds against 249 microseconds of engine work per
    /// tick at a hundred instruments. Chunking is the natural shape for
    /// columnar output, and it is NOT a meaningful speedup -- a Python loop
    /// over `run_session` loses well under one per cent. Use this because it
    /// reads better and records for you, not because a loop would be slow.
    ///
    /// Returns the number of days run.
    #[pyo3(signature = (
        days, *, hour = 9, minute = 30, day_of_week = 3,
        ticks_per_day = 390, volatility = 1.0, record = true, first_day = 0
    ))]
    #[allow(clippy::too_many_arguments)]
    fn run_days(
        &mut self,
        days: usize,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        ticks_per_day: usize,
        volatility: f64,
        record: bool,
        first_day: u32,
    ) -> PyResult<usize> {
        if days == 0 {
            return Err(ValidationError::new_err("days must be greater than zero"));
        }
        if ticks_per_day == 0 {
            return Err(ValidationError::new_err("ticks_per_day must be greater than zero"));
        }
        for offset in 0..days {
            self.open_market();
            self.run_session(hour, minute, day_of_week, ticks_per_day, volatility,
                             false, None, None, None)?;
            self.close_market();
            if record {
                self.record(first_day + offset as u32)?;
            }
        }
        Ok(days)
    }

    /// Advance until a price leaves a band, or until `max_ticks` elapses.
    ///
    /// The interactive shape, for logic that must run inside the day. A
    /// crossing per DECISION is irreducible, so the goal is to make decision
    /// points sparser than ticks rather than pretend the crossing away: an
    /// algorithm watching for a level crosses when the level is hit, not 390
    /// times a day hoping.
    ///
    /// Returns the tick the condition fired on, or None if `max_ticks` ran
    /// out first. None is a real outcome, not a failure -- "it never got
    /// there" is usually the answer you needed.
    ///
    /// The close is NOT run when the condition fires. The day is not over;
    /// the caller interrupted it.
    #[pyo3(signature = (
        *, ticker, above = None, below = None, max_ticks = 390,
        hour = 9, minute = 30, day_of_week = 3, volatility = 1.0
    ))]
    #[allow(clippy::too_many_arguments)]
    fn run_until(
        &mut self,
        ticker: &str,
        above: Option<f64>,
        below: Option<f64>,
        max_ticks: usize,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        volatility: f64,
    ) -> PyResult<Option<usize>> {
        if above.is_none() && below.is_none() {
            return Err(ValidationError::new_err(
                "give at least one of above= or below= - a run_until with no \
                 condition is just run_session",
            ));
        }
        for (name, bound) in [("above", above), ("below", below)] {
            if let Some(v) = bound {
                if !v.is_finite() || v <= 0.0 {
                    return Err(ValidationError::new_err(format!(
                        "{name} must be finite and positive, got {v}"
                    )));
                }
            }
        }
        if let (Some(a), Some(b)) = (above, below) {
            if b >= a {
                return Err(ValidationError::new_err(format!(
                    "below ({b}) must be under above ({a}) - an inverted band \
                     fires immediately and always"
                )));
            }
        }
        if max_ticks == 0 {
            return Err(ValidationError::new_err("max_ticks must be greater than zero"));
        }
        let company = self
            .tickers
            .iter()
            .position(|t| t == ticker)
            .ok_or_else(|| {
                ValidationError::new_err(format!(
                    "no instrument with ticker {ticker:?} in this universe"
                ))
            })?;

        let n = self.inner.len();
        let innovations: Vec<Option<f64>> = vec![None; n];
        let variances: Vec<f64> = self
            .inner
            .companies()
            .iter()
            .map(|c| {
                crate::sectors::by_key(&c.sector)
                    .map(|s| s.base_daily_variance())
                    .unwrap_or(0.000225)
            })
            .collect();

        let outcome = self.inner.run_session(
            &SessionRequest {
                start: GameTime { hour, minute, day_of_week },
                ticks: max_ticks,
                volatility_multiplier: volatility,
                news: &[],
                news_impact_queue: &[],
                order_volumes: &[],
                close_at_end: false,
                daily_innovations: &innovations,
                sector_base_variances: &variances,
                stop: Some(crate::engine::StopCondition::PriceOutside {
                    company,
                    below,
                    above,
                }),
            },
            &mut self.buffer,
        );
        Ok(outcome.halted_at)
    }

    /// Run the close bookkeeping: GARCH update and the daily roll.
    fn close_market(&mut self) {
        self.log.push(crate::python_log::LogEntry::CloseMarket);
        let n = self.inner.len();
        let innovations: Vec<Option<f64>> = vec![None; n];
        let variances: Vec<f64> = self
            .inner
            .companies()
            .iter()
            .map(|c| {
                crate::sectors::by_key(&c.sector)
                    .map(|s| s.base_daily_variance())
                    .unwrap_or(0.000225)
            })
            .collect();
        self.inner.close_market(&DayCloseRequest {
            daily_innovations: &innovations,
            sector_base_variances: &variances,
        });
    }

    /// One column across every instrument, as little-endian f64 bytes.
    ///
    /// Read it with `numpy.frombuffer(buf, dtype="<f8")`, which adopts the
    /// bytes without copying. Values are in roster order, which is
    /// contractual -- see `tickers`.
    fn column(&self, py: Python<'_>, field: &str) -> PyResult<Py<PyBytes>> {
        let f = parse_field(field)?;
        Ok(f64_bytes(py, &self.inner.column(f)))
    }

    /// Current price per instrument, as little-endian f64 bytes.
    fn prices(&self, py: Python<'_>) -> Py<PyBytes> {
        f64_bytes(py, &self.inner.prices())
    }

    /// The last session's price path: `ticks_written x instruments`, row-major.
    ///
    /// Row-major means one tick's cross-section is contiguous and one
    /// instrument's path is strided. That is the right way round: emission is
    /// per tick, so the contiguous direction is the hot one.
    ///
    /// Sliced to `ticks_written`, not to capacity. The buffer is reused across
    /// sessions, so anything past that point is the previous session's data --
    /// returning it would hand back a market that did not happen.
    fn session_prices(&self, py: Python<'_>) -> Py<PyBytes> {
        f64_bytes(py, self.written(&self.buffer.prices))
    }

    /// The last session's volume path, same shape as `session_prices`.
    fn session_volumes(&self, py: Python<'_>) -> Py<PyBytes> {
        f64_bytes(py, self.written(&self.buffer.volumes))
    }

    /// The last session's mispricing path, same shape as `session_prices`.
    ///
    /// This is the ground-truth column: the log deviation from fair value that
    /// produced each print. No historical dataset has it.
    fn session_mispricing_s(&self, py: Python<'_>) -> Py<PyBytes> {
        f64_bytes(py, self.written(&self.buffer.mispricing_s))
    }

    #[getter]
    fn session_ticks_written(&self) -> usize {
        self.buffer.ticks_written
    }

    /// List a new instrument. Returns its index.
    ///
    /// # This changes the whole market from here, and that is correct
    ///
    /// It does not append a name to an otherwise-unchanged simulation. The
    /// tick draws per instrument, so a larger roster shifts every subsequent
    /// draw and every existing instrument's path moves too. That is the model,
    /// not a limitation.
    ///
    /// What IS guaranteed is reproducibility: the generator carries across the
    /// change, so one seed plus the same edits at the same ticks reproduces
    /// the same market exactly. Replay works; invariance was never available.
    fn list_instrument(&mut self, instrument: PyInstrument) -> usize {
        self.log.push(crate::python_log::LogEntry::ListInstrument {
            ticker: instrument.ticker.clone(),
            sector: instrument.sector.clone(),
            initial_price: instrument.initial_price,
            shares_outstanding: instrument.shares_outstanding,
            eps: instrument.eps,
            book_value_per_share: instrument.book_value_per_share,
            revenue_growth: instrument.revenue_growth,
            avg_volume: instrument.avg_volume,
            beta: instrument.beta,
            short_interest: instrument.short_interest,
        });
        let index = self.inner.len();
        let core = instrument.to_core(index);
        self.tickers.push(instrument.ticker.clone());
        self.inner.add_company(core)
    }

    /// Delist the instrument at `index`, returning its ticker.
    ///
    /// The tail keeps its relative order and shifts down by one, so any index
    /// a caller is holding past this point is stale. Re-read `tickers`.
    fn delist(&mut self, index: usize) -> PyResult<String> {
        match self.inner.remove_company(index) {
            Some(c) => {
                self.tickers.remove(index);
                self.log.push(crate::python_log::LogEntry::Delist { index });
                Ok(c.ticker)
            }
            None => Err(ValidationError::new_err(format!(
                "no instrument at index {index}; the roster holds {}",
                self.inner.len()
            ))),
        }
    }

    /// Index of a ticker, or None. Indices shift after a delisting.
    fn index_of(&self, ticker: &str) -> Option<usize> {
        self.tickers.iter().position(|t| t == ticker)
    }

    /// Instrument tickers, in roster order.
    ///
    /// Order is CONTRACTUAL: every column comes back positionally against
    /// this sequence, so it is an ordered list and never a mapping.
    #[getter]
    fn tickers(&self) -> Vec<String> {
        self.tickers.clone()
    }

    /// Cumulative draws taken from the engine's stream.
    ///
    /// Two runs that agree here consumed the generator identically, which is
    /// the precondition for their prices agreeing. Diagnostic: it reports
    /// alignment, it does not enforce it.
    #[getter]
    fn draws_consumed(&self) -> usize {
        self.inner.draws_consumed()
    }

    #[getter]
    fn len(&self) -> usize {
        self.inner.len()
    }

    fn __len__(&self) -> usize {
        self.inner.len()
    }

    /// Take one uniform from the engine's own stream.
    ///
    /// For a caller's own subsystems. Drawing from a SEPARATE generator would
    /// interleave differently and change every price, so anything that needs
    /// randomness alongside the simulation must draw from here.
    fn draw_uniform(&mut self) -> f64 {
        self.log.push(crate::python_log::LogEntry::Draw { normal: false });
        self.inner.draw_uniform()
    }

    fn draw_normal(&mut self) -> f64 {
        self.log.push(crate::python_log::LogEntry::Draw { normal: true });
        self.inner.draw_normal()
    }


    /// The current macro state.
    ///
    /// Rates come back FRACTIONAL, matching what the constructor takes, so a
    /// value read here can be written straight back without a conversion --
    /// which is the whole point of having one denomination at the boundary.
    #[getter]
    fn macro_state(&self) -> PyMacro {
        let e = self.inner.economy();
        PyMacro {
            vix: e.vix,
            federal_funds_rate: crate::units::percent_to_fraction(e.federal_funds_rate),
            corporate_bond_yield: Some(crate::units::percent_to_fraction(e.corporate_bond_yield)),
            inflation_rate: crate::units::percent_to_fraction(e.inflation_rate),
            qe_pe_boost: e.qe_pe_boost,
            fear_greed_index: e.fear_greed_index,
            cycle: cycle_name(e.cycle_phase).to_string(),
        }
    }

    /// Pin one or more macro series to given values.
    ///
    /// # A scenario is a path, not a feature
    ///
    /// A rate shock is `federal_funds_rate` stepping 0.025 -> 0.05 over N
    /// days, supplied day by day by whoever is running the study. It is NOT a
    /// `rate_shock=True` flag. Every macro narrative worth expressing -- QE, a
    /// hiking cycle, stagflation -- is a path over these fields, so the API
    /// gives you the fields and refuses to grow named scenarios that are
    /// paths in disguise.
    ///
    /// Only the named fields are written; everything else keeps evolving
    /// endogenously. That is the "narrow write surface, generous read
    /// surface" the design asks for: pinning the policy rate should not also
    /// freeze inflation.
    ///
    /// Rates are FRACTIONAL, as everywhere else, and validated before being
    /// converted.
    #[pyo3(signature = (
        *, vix = None, federal_funds_rate = None, corporate_bond_yield = None,
        inflation_rate = None, qe_pe_boost = None, fear_greed_index = None,
        cycle = None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn pin_macro(
        &mut self,
        vix: Option<f64>,
        federal_funds_rate: Option<f64>,
        corporate_bond_yield: Option<f64>,
        inflation_rate: Option<f64>,
        qe_pe_boost: Option<f64>,
        fear_greed_index: Option<f64>,
        cycle: Option<String>,
    ) -> PyResult<()> {
        // Validate EVERYTHING before writing ANYTHING. A pin that applied the
        // first three fields and then rejected the fourth would leave the
        // scenario half-applied, and the run would continue on a macro state
        // nobody asked for.
        for (name, v) in [
            ("federal_funds_rate", federal_funds_rate),
            ("corporate_bond_yield", corporate_bond_yield),
            ("inflation_rate", inflation_rate),
        ] {
            if let Some(v) = v {
                crate::units::check_rate(name, v).map_err(ValidationError::new_err)?;
            }
        }
        for (name, v) in [
            ("vix", vix),
            ("qe_pe_boost", qe_pe_boost),
            ("fear_greed_index", fear_greed_index),
        ] {
            if let Some(v) = v {
                if !v.is_finite() {
                    return Err(ValidationError::new_err(format!(
                        "{name} must be finite, got {v}"
                    )));
                }
            }
        }
        let phase = match cycle.as_deref() {
            Some(name) => Some(CyclePhase::from_name(name).ok_or_else(|| {
                ValidationError::new_err(format!(
                    "unknown cycle {name:?}. Valid: expansion, peak, contraction, trough, recovery"
                ))
            })?),
            None => None,
        };

        let mut logged: Vec<(String, f64)> = Vec::new();
        for (name, value) in [
            ("vix", vix),
            ("federal_funds_rate", federal_funds_rate),
            ("corporate_bond_yield", corporate_bond_yield),
            ("inflation_rate", inflation_rate),
            ("qe_pe_boost", qe_pe_boost),
            ("fear_greed_index", fear_greed_index),
        ] {
            if let Some(v) = value {
                logged.push((name.to_string(), v));
            }
        }
        self.log.push(crate::python_log::LogEntry::PinMacro {
            fields: logged,
            cycle: cycle.clone(),
        });

        let e = self.inner.economy_mut();
        if let Some(v) = vix {
            e.vix = v;
        }
        if let Some(v) = federal_funds_rate {
            e.federal_funds_rate = crate::units::fraction_to_percent(v);
        }
        if let Some(v) = corporate_bond_yield {
            e.corporate_bond_yield = crate::units::fraction_to_percent(v);
        }
        if let Some(v) = inflation_rate {
            e.inflation_rate = crate::units::fraction_to_percent(v);
        }
        if let Some(v) = qe_pe_boost {
            e.qe_pe_boost = v;
        }
        if let Some(v) = fear_greed_index {
            e.fear_greed_index = v;
        }
        if let Some(p) = phase {
            e.cycle_phase = p;
        }
        Ok(())
    }

    /// The live factor names, in the order `attribution` reports them.
    #[classattr]
    #[allow(non_snake_case)]
    fn FACTORS() -> Vec<String> {
        FACTOR_NAMES.iter().map(|s| s.to_string()).collect()
    }

    /// One attribution column across every instrument, as f64 bytes.
    ///
    /// # Why the simulator can tell you this at all
    ///
    /// It knows WHY each price moved, because it computed the reasons. No
    /// historical dataset carries those labels -- you can observe that a stock
    /// fell, never that 60% of the fall was order-flow pressure and the rest
    /// was noise. This is the labelled-dataset output.
    ///
    /// Accumulated per DAY and reset at `open_market`, so a read after
    /// `close_market` still returns the day just finished.
    ///
    /// # Four columns, not the reference's six
    ///
    /// The reference declares six attribution keys. Three of them --
    /// `earningsRevision`, `multipleChange`, `sentiment` -- belong to factors
    /// the live flags discard, and its `shortSqueezeEffect` is folded into
    /// `orderFlowImpact` for display rather than reported separately.
    ///
    /// Reporting six here would ship three columns of structural zeros, which
    /// is a documentation lie of exactly the kind this model has already had
    /// to correct once. So the four live components are reported, with the
    /// squeeze kept separate because it is a genuinely distinct mechanism.
    ///
    /// These are the RAW factors, not the reference's scaled display
    /// accumulator: the decomposition is the ground truth, the scaling is a
    /// presentation choice, and baking it in would put a display decision
    /// inside the dataset.
    fn attribution(&self, py: Python<'_>, factor: &str) -> PyResult<Py<PyBytes>> {
        let index = FACTOR_NAMES
            .iter()
            .position(|f| *f == factor)
            .ok_or_else(|| {
                ValidationError::new_err(format!(
                    "unknown factor {factor:?}. Valid: {}",
                    FACTOR_NAMES.join(", ")
                ))
            })?;
        Ok(f64_bytes(py, &self.inner.attribution_column(index)))
    }

    /// Capture the session just run, and the macro state, as one day.
    ///
    /// Explicit rather than automatic. The session buffer is reused, so
    /// anything not captured before the next `run_session` is gone -- but a
    /// caller who does not want a table should not pay to build one every
    /// session.
    ///
    /// The RAW buffers are kept rather than a finished batch. It costs the
    /// same memory and it keeps grain a read-time decision: one recording can
    /// answer tick, five-minute and daily questions. Re-running a day to
    /// change its grain would be the alternative, and it is a much worse one.
    #[pyo3(signature = (day))]
    fn record(&mut self, day: u32) -> PyResult<()> {
        self.log.push(crate::python_log::LogEntry::Record { day });
        self.recorded.push(crate::python_arrow::RecordedDay {
            day,
            ticks: self.buffer.ticks_written,
            instruments: self.buffer.companies,
            prices: self.written(&self.buffer.prices).to_vec(),
            volumes: self.written(&self.buffer.volumes).to_vec(),
            mispricing: self.written(&self.buffer.mispricing_s).to_vec(),
            fundamental: self.written(&self.buffer.fundamental).to_vec(),
            anchor: self.written(&self.buffer.anchor).to_vec(),
            components: std::array::from_fn(|k| {
                self.written(&self.buffer.components[k]).to_vec()
            }),
        });
        let e = self.inner.economy();
        self.recorded_macro.push(crate::python_arrow::MacroRow {
            day,
            vix: e.vix,
            // Fractional on the way out, matching the way in. A results table
            // reporting percent while the constructor takes fractions would
            // reintroduce the unit trap on the return journey.
            federal_funds_rate: crate::units::percent_to_fraction(e.federal_funds_rate),
            corporate_bond_yield: crate::units::percent_to_fraction(e.corporate_bond_yield),
            inflation_rate: crate::units::percent_to_fraction(e.inflation_rate),
            unemployment_rate: crate::units::percent_to_fraction(e.unemployment_rate),
            gdp_growth: crate::units::percent_to_fraction(e.gdp_growth),
            qe_pe_boost: e.qe_pe_boost,
            fear_greed_index: e.fear_greed_index,
        });
        Ok(())
    }

    /// Discard everything recorded so far.
    fn clear_recording(&mut self) {
        self.recorded.clear();
        self.recorded_macro.clear();
        self.recorded_book.clear();
    }

    #[getter]
    fn recorded_days(&self) -> usize {
        self.recorded.len()
    }

    /// The `bars` table.
    ///
    /// Grain is chosen here, and downsampling happens in RUST rather than in
    /// the consumer: bucketing ten million rows in Python to get two hundred
    /// is the cost this surface exists to avoid.
    ///
    ///   `bars()`             tick grain: day, tick, instrument_id, close, volume
    ///   `bars(minutes=5)`    five-minute OHLCV bars
    ///   `bars(grain="day")`  one OHLCV bar per instrument per day
    ///
    /// The tick schema has no open/high/low because at tick grain a bar IS the
    /// print and those columns would repeat close. Once ticks are bucketed
    /// they carry real information, so the coarse schema is genuinely wider
    /// rather than the same columns rearranged.
    ///
    /// Every recorded day is a separate batch, so a year streams rather than
    /// materialising. With nothing recorded it falls back to the last session.
    #[pyo3(signature = (*, day = 0, minutes = None, grain = None))]
    fn bars(
        &self,
        day: u32,
        minutes: Option<usize>,
        grain: Option<&str>,
    ) -> PyResult<crate::python_arrow::PyArrowStream> {
        let days: Vec<crate::python_arrow::RecordedDay> = if self.recorded.is_empty() {
            vec![crate::python_arrow::RecordedDay {
                day,
                ticks: self.buffer.ticks_written,
                instruments: self.buffer.companies,
                prices: self.written(&self.buffer.prices).to_vec(),
                volumes: self.written(&self.buffer.volumes).to_vec(),
                // bars() reads neither, and cloning the ground-truth
                // buffers to build a table that discards them would be pure
                // copying. truth() has its own path below.
                mispricing: Vec::new(),
                fundamental: Vec::new(),
                anchor: Vec::new(),
                components: std::array::from_fn(|_| Vec::new()),
            }]
        } else {
            self.recorded.clone()
        };

        // `None` means tick grain, which uses the narrow schema.
        let bucket = match (minutes, grain) {
            (Some(_), Some(_)) => {
                return Err(ValidationError::new_err(
                    "pass either minutes or grain, not both",
                ))
            }
            (Some(m), None) => {
                if m == 0 {
                    return Err(ValidationError::new_err("minutes must be at least 1"));
                }
                // One tick is one simulated minute, so the bucket is the
                // minute count directly.
                Some(m)
            }
            (None, Some("day")) => Some(usize::MAX),
            (None, Some("tick")) | (None, None) => None,
            (None, Some(other)) => {
                return Err(ValidationError::new_err(format!(
                    "unknown grain {other:?}. Valid: \"tick\", \"day\", or minutes=N"
                )))
            }
        };

        match bucket {
            None => {
                let mut batches = Vec::with_capacity(days.len());
                for d in &days {
                    batches.push(
                        crate::python_arrow::bars_batch(
                            d.day, d.ticks, d.instruments, &d.prices, &d.volumes,
                        )
                        .map_err(crate::python_arrow::arrow_err)?,
                    );
                }
                Ok(crate::python_arrow::PyArrowStream::new(
                    "bars",
                    crate::python_arrow::bars_schema(),
                    batches,
                ))
            }
            Some(b) => {
                let mut batches = Vec::with_capacity(days.len());
                for d in &days {
                    let size = if b == usize::MAX { core::cmp::max(d.ticks, 1) } else { b };
                    batches.push(
                        crate::python_arrow::ohlc_batch(d, size)
                            .map_err(crate::python_arrow::arrow_err)?,
                    );
                }
                Ok(crate::python_arrow::PyArrowStream::new(
                    "bars",
                    crate::python_arrow::ohlc_schema(),
                    batches,
                ))
            }
        }
    }

    /// The `truth` table: the labelled-dataset output.
    ///
    /// The log deviation from fair value that produced each print. No
    /// historical dataset carries this column, because no historical dataset
    /// knows what fair value was.
    #[pyo3(signature = (*, day = 0))]
    fn truth(&self, day: u32) -> PyResult<crate::python_arrow::PyArrowStream> {
        let batches = if self.recorded.is_empty() {
            vec![crate::python_arrow::truth_batch(
                day,
                self.buffer.ticks_written,
                self.buffer.companies,
                self.written(&self.buffer.mispricing_s),
                self.written(&self.buffer.fundamental),
                self.written(&self.buffer.anchor),
                &std::array::from_fn(|k| self.written(&self.buffer.components[k]).to_vec()),
            )
            .map_err(crate::python_arrow::arrow_err)?]
        } else {
            let mut out = Vec::with_capacity(self.recorded.len());
            for d in &self.recorded {
                out.push(
                    crate::python_arrow::truth_batch(
                        d.day,
                        d.ticks,
                        d.instruments,
                        &d.mispricing,
                        &d.fundamental,
                        &d.anchor,
                        &d.components,
                    )
                    .map_err(crate::python_arrow::arrow_err)?,
                );
            }
            out
        };
        Ok(crate::python_arrow::PyArrowStream::new(
            "truth",
            crate::python_arrow::truth_schema(),
            batches,
        ))
    }

    /// The `macro` table: one row per recorded day.
    ///
    /// Keyed by the same `day` as `bars` and `truth`, so aligning a macro
    /// signal with prices is a join rather than a hand-rolled accumulation
    /// loop. Rates are fractional, as everywhere else.
    fn macro_table(&self) -> PyResult<crate::python_arrow::PyArrowStream> {
        let batch = crate::python_arrow::macro_batch(&self.recorded_macro)
            .map_err(crate::python_arrow::arrow_err)?;
        Ok(crate::python_arrow::PyArrowStream::new(
            "macro",
            crate::python_arrow::macro_schema(),
            vec![batch],
        ))
    }

    /// The executable order book for one instrument, right now.
    ///
    /// # The depth you read is the depth you trade against
    ///
    /// This is the same book the tick settles prices through, not a display
    /// copy of it. So `sweep_cost` tells you what size would ACTUALLY cost,
    /// and submitting an order pays those prices because it consumed those
    /// levels -- market impact is emergent rather than a coefficient.
    ///
    /// # It is a snapshot, and trading it does not move the market
    ///
    /// Worth being explicit, because the opposite is easy to assume. The book
    /// is rebuilt per call from current state, so the object returned is
    /// detached: filling against it tells you your execution price, but the
    /// market only learns about your trading through `order_flow` on the next
    /// tick. Those are two separate channels on purpose -- one prices your
    /// fill, the other applies your pressure -- and a harness that wants both
    /// must do both.
    fn book(&self, ticker: &str) -> PyResult<crate::python_book::PyOrderBook> {
        let index = self.tickers.iter().position(|t| t == ticker).ok_or_else(|| {
            ValidationError::new_err(format!(
                "no instrument with ticker {ticker:?} in this universe"
            ))
        })?;
        let inner = self.inner.book_for(index).ok_or_else(|| {
            ValidationError::new_err(format!("no instrument at index {index}"))
        })?;
        Ok(crate::python_book::PyOrderBook::from_core(inner))
    }

    /// Capture current book depth for every instrument.
    ///
    /// # Opt-in, because the arithmetic demands it
    ///
    /// A hundred names at 390 ticks with ten levels a side is about 1.5
    /// million rows a day, against 39,000 for `bars`. Recording depth by
    /// default would make every run forty times more expensive to answer a
    /// question most runs never ask.
    ///
    /// So the caller decides when and how deep. Nothing samples on their
    /// behalf: a sampling rate baked into the engine would be a modelling
    /// decision wearing the costume of a default, and two studies using
    /// different rates would silently be measuring different things.
    ///
    /// This is NOT logged as a replayable input, and correctly so -- it reads
    /// state without changing it, consumes no draws, and replaying a run
    /// produces the same depth whether or not anyone looked.
    #[pyo3(signature = (*, day = 0, tick = 0, levels = 10))]
    fn snapshot_book(&mut self, day: u32, tick: u32, levels: usize) -> PyResult<usize> {
        if levels == 0 {
            return Err(ValidationError::new_err("levels must be at least 1"));
        }
        let before = self.recorded_book.len();
        for index in 0..self.inner.len() {
            let Some(book) = self.inner.book_for(index) else {
                continue;
            };
            for (side_id, side) in [
                (0u32, crate::order_book::Side::Buy),
                (1u32, crate::order_book::Side::Sell),
            ] {
                for (level, entry) in book.price_levels(side, levels).iter().enumerate() {
                    self.recorded_book.push(crate::python_arrow::BookRow {
                        day,
                        tick,
                        instrument_id: index as u32,
                        side: side_id,
                        level: level as u32,
                        price: entry.price,
                        size: entry.quantity,
                    });
                }
            }
        }
        Ok(self.recorded_book.len() - before)
    }

    /// The `book` table: recorded depth, one row per (tick, instrument, side,
    /// level).
    ///
    /// `side` is 0 for bids and 1 for asks -- an integer rather than a string
    /// because it repeats on every row, the same reason `instrument_id` is an
    /// index.
    ///
    /// Empty unless `snapshot_book` was called.
    fn book_table(&self) -> PyResult<crate::python_arrow::PyArrowStream> {
        let batch = crate::python_arrow::book_batch(&self.recorded_book)
            .map_err(crate::python_arrow::arrow_err)?;
        Ok(crate::python_arrow::PyArrowStream::new(
            "book",
            crate::python_arrow::book_schema(),
            vec![batch],
        ))
    }

    #[getter]
    fn recorded_book_rows(&self) -> usize {
        self.recorded_book.len()
    }

    /// Every input that crossed into this engine, in order.
    ///
    /// # A seed alone does not reproduce a run
    ///
    /// It would, if nothing else varied. But the market an agent trades in
    /// depends on the agent's own orders, so one seed with different flow is a
    /// different market -- correctly. Reproducing a run means reproducing
    /// every input, and this is that sequence.
    ///
    /// It records INPUTS only. Prices, attribution and draw counts are
    /// consequences of replaying them, and logging those too would create a
    /// second source of truth that could disagree with the first.
    ///
    /// Embedder draws are in here, which is easy to overlook: taking a uniform
    /// between two ticks moves the shared stream, so a replay that skipped it
    /// would produce a different market from the same log.
    #[getter]
    fn order_log(&self, py: Python<'_>) -> PyResult<Vec<PyObject>> {
        self.log.iter().map(|e| e.to_py(py)).collect()
    }

    fn __repr__(&self) -> String {
        format!(
            "Engine({} instruments, draws={})",
            self.inner.len(),
            self.inner.draws_consumed()
        )
    }
}

/// Which session a moment falls in.
///
/// Exposed so a caller does not keep a second copy of the session boundaries,
/// which would drift from these.
#[pyfunction]
pub fn market_status(hour: i64, minute: i64, day_of_week: i64) -> PyResult<String> {
    if !(0..24).contains(&hour) || !(0..60).contains(&minute) || !(0..7).contains(&day_of_week) {
        return Err(ValidationError::new_err("invalid time"));
    }
    Ok(status_name(crate::market::get_market_status(GameTime {
        hour,
        minute,
        day_of_week,
    }))
    .to_string())
}

/// Generate `n` plausible instruments deterministically.
///
/// `seed` is the UNIVERSE seed and is independent of any simulation seed, so
/// "same universe, different market draws" — the standard design for variance
/// estimation — is expressible. Generation draws from its own stream and
/// consumes nothing from an engine's.
#[pyfunction]
#[pyo3(signature = (n = 108, *, seed = 0))]
pub fn random_instruments(n: usize, seed: u32) -> PyResult<Vec<PyInstrument>> {
    if n == 0 {
        return Err(ValidationError::new_err("n must be greater than zero"));
    }
    if n > 26 * 26 * 26 {
        return Err(ValidationError::new_err(format!(
            "n must be at most {} - tickers are three letters", 26 * 26 * 26
        )));
    }
    Ok(crate::universe::random_universe(n, seed)
        .into_iter()
        .map(|g| PyInstrument {
            ticker: g.ticker,
            sector: g.sector.to_string(),
            initial_price: g.initial_price,
            shares_outstanding: g.shares_outstanding,
            eps: Some(g.eps),
            book_value_per_share: Some(g.book_value_per_share),
            revenue_growth: Some(g.revenue_growth),
            avg_volume: g.avg_volume,
            beta: g.beta,
            short_interest: 0.0,
        })
        .collect())
}

fn cycle_name(p: CyclePhase) -> &'static str {
    match p {
        CyclePhase::Expansion => "expansion",
        CyclePhase::Peak => "peak",
        CyclePhase::Contraction => "contraction",
        CyclePhase::Trough => "trough",
        CyclePhase::Recovery => "recovery",
    }
}

/// A news event, as the price model sees it.
///
/// Reduced to the three fields the factor model actually reads. The game's
/// richer event objects -- headlines, bodies, storyline phases -- never reach
/// the price loop, so carrying them across the boundary would be marshalling
/// cost for nothing.
///
/// Scope is decided by which fields are set, and the rules are not symmetric:
///
///   ticker set                    -> that instrument only
///   sector set, no ticker         -> every instrument in that sector
///   neither set                   -> market-wide
///
/// So an event with no ticker and no sector is not "unscoped and inert", it is
/// the broadest possible event. That is the reference behaviour and it
/// surprises people, which is why it is written down here.
#[pyclass(name = "News", module = "pretium._core", frozen, get_all)]
#[derive(Debug, Clone)]
pub struct PyNews {
    pub ticker: Option<String>,
    pub sector: Option<String>,
    pub price_impact: f64,
}

#[pymethods]
impl PyNews {
    #[new]
    #[pyo3(signature = (*, ticker = None, sector = None, price_impact = 0.0))]
    fn new(ticker: Option<String>, sector: Option<String>, price_impact: f64) -> PyResult<Self> {
        if !price_impact.is_finite() {
            return Err(ValidationError::new_err(format!(
                "price_impact must be finite, got {price_impact}"
            )));
        }
        if let Some(s) = sector.as_deref() {
            if crate::sectors::by_key(s).is_none() {
                return Err(ValidationError::new_err(format!(
                    "unknown sector {s:?}. Valid sectors: {}",
                    crate::sectors::keys().join(", ")
                )));
            }
        }
        Ok(Self { ticker, sector, price_impact })
    }

    fn __repr__(&self) -> String {
        format!(
            "News(ticker={:?}, sector={:?}, price_impact={})",
            self.ticker, self.sector, self.price_impact
        )
    }
}

/// A decaying news impact, carried across ticks.
///
/// Distinct from [`PyNews`]: news is an impulse arriving now, this is the
/// residue of one still working through the tape. It also drives the volume
/// amplifier, which is why a name in the middle of a story trades heavier.
#[pyclass(name = "NewsImpact", module = "pretium._core", frozen, get_all)]
#[derive(Debug, Clone)]
pub struct PyNewsImpact {
    pub ticker: Option<String>,
    pub sector: Option<String>,
    pub sectors: Vec<String>,
    pub remaining_impact: f64,
    pub reversal_phase: bool,
}

#[pymethods]
impl PyNewsImpact {
    #[new]
    #[pyo3(signature = (
        *, ticker = None, sector = None, sectors = None,
        remaining_impact = 0.0, reversal_phase = false
    ))]
    fn new(
        ticker: Option<String>,
        sector: Option<String>,
        sectors: Option<Vec<String>>,
        remaining_impact: f64,
        reversal_phase: bool,
    ) -> PyResult<Self> {
        if !remaining_impact.is_finite() {
            return Err(ValidationError::new_err(format!(
                "remaining_impact must be finite, got {remaining_impact}"
            )));
        }
        let sectors = sectors.unwrap_or_default();
        for s in sector.iter().chain(sectors.iter()) {
            if crate::sectors::by_key(s).is_none() {
                return Err(ValidationError::new_err(format!(
                    "unknown sector {s:?}. Valid sectors: {}",
                    crate::sectors::keys().join(", ")
                )));
            }
        }
        Ok(Self { ticker, sector, sectors, remaining_impact, reversal_phase })
    }

    fn __repr__(&self) -> String {
        format!(
            "NewsImpact(ticker={:?}, remaining_impact={}, reversal_phase={})",
            self.ticker, self.remaining_impact, self.reversal_phase
        )
    }
}


/// The live factor decomposition, in reporting order.
pub const FACTOR_NAMES: [&str; 4] = [
    "company_news",
    "order_flow_impact",
    "short_squeeze_effect",
    "random_noise",
];

/// A sector's relative volatility multiplier.
///
/// Exposed so a loader can derive a beta with the same cross-sector structure
/// a generated universe has, without consuming an RNG draw -- drawing here
/// would make building a universe perturb the market it is built for.
///
/// DIMENSIONLESS and relative (0.6 to 1.3). Not a volatility in any unit, and
/// not the thing to square for a variance -- see the sector table.
#[pyfunction]
pub fn sector_volatility(sector: &str) -> PyResult<f64> {
    crate::sectors::by_key(sector)
        .map(|s| s.volatility)
        .ok_or_else(|| {
            ValidationError::new_err(format!(
                "unknown sector {sector:?}. Valid sectors: {}",
                crate::sectors::keys().join(", ")
            ))
        })
}

/// A sector's long-run daily return standard deviation, as a fraction.
///
/// The real dispersion measure -- NOT the relative `volatility` multiplier,
/// which is dimensionless and squaring it for a variance is a mistake the
/// reference implementation made and had to fix.
#[pyfunction]
pub fn sector_daily_sigma(sector: &str) -> PyResult<f64> {
    crate::sectors::by_key(sector)
        .map(|s| s.daily_sigma)
        .ok_or_else(|| {
            ValidationError::new_err(format!(
                "unknown sector {sector:?}. Valid sectors: {}",
                crate::sectors::keys().join(", ")
            ))
        })
}

/// Standard deviation of the daily mispricing process at rest.
///
/// A universe priced exactly at fair value starts with zero cross-sectional
/// mispricing dispersion, so a strategy that harvests mispricing sees nothing
/// until shocks accumulate -- on the order of one 60-day half-life. This is
/// the width of the distribution such a universe would eventually reach, so a
/// caller can start there instead of waiting.
///
/// Returns None for non-stationary parameters rather than a large finite
/// number, which would be worse: it would be used.
#[pyfunction]
#[pyo3(signature = (innovation_sigma, *, phi = None, theta = None))]
pub fn stationary_sigma(
    innovation_sigma: f64,
    phi: Option<f64>,
    theta: Option<f64>,
) -> PyResult<Option<f64>> {
    if !innovation_sigma.is_finite() || innovation_sigma < 0.0 {
        return Err(ValidationError::new_err(format!(
            "innovation_sigma must be finite and not negative, got {innovation_sigma}"
        )));
    }
    Ok(crate::mispricing::stationary_sigma(phi, theta, innovation_sigma))
}
