//! The engine surface, Layer 2, behind the `python` feature.
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
//! the same: a silent parity-breaking switch in the public API. Downcast
//! your own copy after the bits leave the library.

#![allow(unexpected_cfgs, clippy::useless_conversion)]

use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3::types::PyBytes;

use crate::economy::{create_initial_central_bank_state, create_initial_economy_state};
use crate::economy::{CyclePhase, ForwardGuidance, InitialEconomyOptions};
use crate::engine::{Engine, PriceField, SessionBuffer, SessionRequest, TickRequest};
use crate::engine::{TickOutcome};
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
#[pyclass(name = "Instrument", module = "tradefloor._core", get_all)]
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
        if sector == crate::rates::RATE_SECTOR {
            // A simulated rate index rather than a company. Its duration,
            // convexity, spread and curve point come from the ticker, so only
            // the tickers this build prices are accepted, and the company
            // fields must be absent: a bond index with earnings would be a
            // universe nobody could have meant.
            if crate::rates::spec_for(ticker).is_none() {
                return Err(ValidationError::new_err(format!(
                    "{ticker:?} is not a rate instrument this build prices. \
                     Sector \"rates\" takes one of: {}. They are simulated \
                     constant-maturity indices, not real securities; \
                     tradefloor.bonds() builds them with their defaults.",
                    crate::rates::tickers().join(", ")
                )));
            }
            for (name, v) in [
                ("eps", eps),
                ("book_value_per_share", book_value_per_share),
                ("revenue_growth", revenue_growth),
            ] {
                if v.is_some() {
                    return Err(ValidationError::new_err(format!(
                        "{name} is a company field and {ticker} is a rate index; \
                         leave it unset."
                    )));
                }
            }
            if short_interest != 0.0 {
                return Err(ValidationError::new_err(format!(
                    "short_interest is a company field and {ticker} is a rate \
                     index; leave it at 0."
                )));
            }
        } else if crate::sectors::by_key(sector).is_none() {
            return Err(ValidationError::new_err(format!(
                "unknown sector {sector:?}. Valid sectors: {}, or \"{}\" for a \
                 simulated rate index ({})",
                crate::sectors::keys().join(", "),
                crate::rates::RATE_SECTOR,
                crate::rates::tickers().join(", ")
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
        // short_interest is a SHARE COUNT, and the shape of the mistake is
        // predictable enough to refuse. Someone writing 0.03 means three per
        // cent; what they get is three hundredths of one share, a ratio of
        // 3e-11 against the float, and a squeeze that can never fire. The
        // failure is silent -- the value is legal, the market runs, and one of
        // the four shock factors is simply dead.
        //
        // A fractional short position in a company with a meaningful share
        // count is not a scenario anyone is modelling, so the ambiguous range
        // is rejected rather than guessed at. This is the same treatment rates
        // get: refuse the plausible-looking mistake at the boundary instead of
        // producing a market nobody specified.
        if short_interest > 0.0 && short_interest < 1.0 && shares_outstanding >= 1000.0 {
            let as_percent = short_interest * 100.0;
            let as_shares = shares_outstanding * short_interest;
            return Err(ValidationError::new_err(format!(
                "short_interest = {short_interest} looks like a fraction, but it is a SHARE COUNT: the squeeze rule divides it by the float. If you meant {as_percent}% of {shares_outstanding} shares, pass {as_shares}. Values between 0 and 1 are refused because a fractional short position is never what anyone means."
            )));
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

    /// Whether this is a simulated rate index rather than a company.
    pub fn is_rate(&self) -> bool {
        self.sector == crate::rates::RATE_SECTOR
    }

    /// The rate instrument this describes. Only for `is_rate()`, which the
    /// constructor has already checked names a priced ticker.
    fn to_rate(&self) -> crate::rates::RateInstrument {
        let spec = *crate::rates::spec_for(&self.ticker).expect("validated at construction");
        crate::rates::RateInstrument::new(
            spec,
            self.initial_price,
            self.avg_volume,
            self.shares_outstanding,
        )
    }

    fn to_core(&self, index: usize) -> TickCompany {
        // The mapping is `InstrumentInit::to_tick_company` in the core, so
        // the browser surface starts a market from the same initial state
        // rather than a second copy of these decisions.
        crate::universe::InstrumentInit {
            ticker: self.ticker.clone(),
            sector: self.sector.clone(),
            initial_price: self.initial_price,
            shares_outstanding: self.shares_outstanding,
            eps: self.eps,
            book_value_per_share: self.book_value_per_share,
            revenue_growth: self.revenue_growth,
            avg_volume: self.avg_volume,
            beta: self.beta,
            short_interest: self.short_interest,
        }
        .to_tick_company(index)
    }
}

/// The macro state the price loop reads.
///
/// Rates are FRACTIONAL here and converted once, in `to_core`.
#[pyclass(name = "Macro", module = "tradefloor._core", get_all)]
#[derive(Debug, Clone)]
pub struct PyMacro {
    pub vix: f64,
    pub federal_funds_rate: f64,
    pub corporate_bond_yield: Option<f64>,
    pub inflation_rate: f64,
    pub qe_pe_boost: f64,
    pub qe_assets_ratio: f64,
    pub fear_greed_index: f64,
    pub cycle: String,
}

#[pymethods]
impl PyMacro {
    #[new]
    #[pyo3(signature = (
        *, vix = 15.0, federal_funds_rate = 0.025, corporate_bond_yield = None,
        inflation_rate = 0.02, qe_pe_boost = 0.0, qe_assets_ratio = 1.0, fear_greed_index = 50.0,
        cycle = "expansion"
    ))]
    fn new(
        vix: f64,
        federal_funds_rate: f64,
        corporate_bond_yield: Option<f64>,
        inflation_rate: f64,
        qe_pe_boost: f64,
        qe_assets_ratio: f64,
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
            ("qe_assets_ratio", qe_assets_ratio),
            ("qe_assets_ratio", qe_assets_ratio),
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
            qe_assets_ratio,
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
    /// Which recorded days a table call covers.
    ///
    /// `None` is all of them, which is what a streaming consumer wants and
    /// what these tables have always returned. `Some(d)` is that day alone.
    ///
    /// A day that was never recorded is an ERROR rather than an empty table.
    /// The whole reason `day` needed fixing is that it looked like a filter
    /// and silently was not, and answering a question about day 7 with a
    /// well-formed table of nothing would be the same failure wearing a
    /// different shape.
    fn select_recorded(
        &self,
        day: Option<u32>,
    ) -> PyResult<Vec<crate::python_arrow::RecordedDay>> {
        let Some(wanted) = day else {
            return Ok(self.recorded.clone());
        };
        let hit: Vec<_> = self
            .recorded
            .iter()
            .filter(|r| r.day == wanted)
            .cloned()
            .collect();
        if hit.is_empty() {
            let mut days: Vec<u32> = self.recorded.iter().map(|r| r.day).collect();
            days.sort_unstable();
            days.dedup();
            let recorded = match (days.first(), days.last()) {
                (Some(lo), Some(hi)) if days.len() as u32 == hi - lo + 1 => {
                    format!("{lo} to {hi}")
                }
                (Some(_), Some(_)) => days
                    .iter()
                    .map(|d| d.to_string())
                    .collect::<Vec<_>>()
                    .join(", "),
                _ => "none".to_string(),
            };
            return Err(ValidationError::new_err(format!(
                // Positional rather than captured: `concat!` expands
                // after `format!` has read its inline captures, so
                // {wanted} inside it resolves to nothing.
                concat!(
                    "day {} was not recorded; recorded days are {}. ",
                    "A day reaches these tables through `record(day)`, ",
                    "and the label it was given there is what this ",
                    "selects on."
                ),
                wanted,
                recorded
            )));
        }
        Ok(hit)
    }

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

    /// Why a news item cannot name `ticker`: it is not on the roster, or it
    /// is a rate index, which the news channels never reach.
    fn no_news_for(&self, ticker: &str) -> PyErr {
        if self.inner.rates().index_of(ticker).is_some() {
            ValidationError::new_err(format!(
                "{ticker} is a rate index. News moves equities through the \
                 factor model, which never runs on an index; move a rate index \
                 by writing the yield it reads with pin_macro."
            ))
        } else {
            ValidationError::new_err(format!(
                "no instrument with ticker {ticker:?} in this universe"
            ))
        }
    }

    fn id_for(&self, ticker: &str) -> Option<String> {
        let pos = self.tickers.iter().position(|t| t == ticker)?;
        self.inner.ids().get(pos).cloned()
    }

    /// The sector of the instrument a ticker names.
    ///
    /// Used to resolve the ANNOUNCER's sector for company-tagged news, so
    /// the information-transfer channel can find that company's peers
    /// without the caller restating what the roster already knows.
    fn sector_for(&self, ticker: &str) -> Option<String> {
        let pos = self.tickers.iter().position(|t| t == ticker)?;
        self.inner.sectors().get(pos).cloned()
    }

    fn build_news(&self, news: Option<Vec<PyNews>>) -> PyResult<Vec<NewsEvent>> {
        let Some(items) = news else { return Ok(Vec::new()) };
        let mut out = Vec::with_capacity(items.len());
        for n in items {
            let company_id = match n.ticker.as_deref() {
                Some(t) => Some(self.id_for(t).ok_or_else(|| self.no_news_for(t))?),
                None => None,
            };
            // A company-tagged event with no sector is resolved to the
            // announcer's own sector, so the information-transfer channel can
            // find its peers. Bit-inert while `news_peer_weight` is zero:
            // the only branch that reads a sector alongside a company id is
            // the peer branch, and that one is skipped entirely at zero
            // weight. The named company itself is matched by id, before
            // sector is consulted at all.
            let sector = match (&n.sector, n.ticker.as_deref()) {
                (Some(s), _) => Some(s.clone()),
                (None, Some(t)) => self.sector_for(t),
                (None, None) => None,
            };
            out.push(NewsEvent {
                company_id,
                sector,
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
                Some(t) => Some(self.id_for(t).ok_or_else(|| self.no_news_for(t))?),
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
    /// Append the session just run onto the day's accumulator.
    ///
    /// Called after EVERY inner `run_session`, so a day made of many sessions
    /// records as one continuous tape. Copies only what was written, which is
    /// less than capacity whenever a stop condition fired.
    fn accumulate_session(&mut self) {
        let ticks = self.buffer.ticks_written;
        if ticks == 0 {
            return;
        }
        let n = ticks * self.buffer.companies;
        self.day_buffer.companies = self.buffer.companies;
        self.day_buffer.ticks += ticks;
        self.day_buffer.prices.extend_from_slice(&self.buffer.prices[..n]);
        self.day_buffer.volumes.extend_from_slice(&self.buffer.volumes[..n]);
        self.day_buffer
            .mispricing
            .extend_from_slice(&self.buffer.mispricing_s[..n]);
        self.day_buffer
            .fundamental
            .extend_from_slice(&self.buffer.fundamental[..n]);
        self.day_buffer
            .anchor
            .extend_from_slice(&self.buffer.anchor[..n]);
        self.day_buffer
            .shock
            .extend_from_slice(&self.buffer.shock[..n]);
        self.day_buffer
            .absorbed
            .extend_from_slice(&self.buffer.absorbed[..n]);
        self.day_buffer
            .clamp
            .extend_from_slice(&self.buffer.clamp[..n]);
        // Appended only when the session actually carried the arm, so a day
        // that ran without it holds an EMPTY pair rather than a padded one.
        if !self.buffer.unbounded_print.is_empty() {
            self.day_buffer
                .unbounded_print
                .extend_from_slice(&self.buffer.unbounded_print[..n]);
            self.day_buffer
                .liquidity_share
                .extend_from_slice(&self.buffer.liquidity_share[..n]);
        }
        for k in 0..crate::market::factors::S_COMPONENT_KEYS.len() {
            self.day_buffer.components[k]
                .extend_from_slice(&self.buffer.components[k][..n]);
        }
        // The eighth series is the daily jump. It happens at the close, so
        // no tick carries it, and the row where its effect is OBSERVED is the
        // first tick of the next day: `s` there already includes it. Zeroed
        // here and filled from the pending value on that first row, so the
        // columns sum to the change in `s` tick by tick (§74).
        let before = self.day_buffer.components[crate::market::factors::JUMP_SLOT].len();
        self.day_buffer.components[crate::market::factors::JUMP_SLOT].resize(self.day_buffer.components[0].len(), 0.0);
        if !self.pending_jump.is_empty() {
            for (i, v) in self.pending_jump.iter().enumerate() {
                if let Some(slot) = self.day_buffer.components[crate::market::factors::JUMP_SLOT].get_mut(before + i) {
                    *slot += v;
                }
            }
            self.pending_jump.clear();
        }
        // The tenth series is the overnight move. It happens at the open,
        // before any tick, and the row where its effect is observed is the
        // first tick of the same day: `s` there already includes it. Zeroed
        // here and filled from the pending value on that first row, as the
        // jump is, so the columns sum to the change in `s` tick by tick.
        let before = self.day_buffer.components[crate::market::factors::OVERNIGHT_SLOT].len();
        self.day_buffer.components[crate::market::factors::OVERNIGHT_SLOT].resize(self.day_buffer.components[0].len(), 0.0);
        if !self.pending_overnight.is_empty() {
            for (i, v) in self.pending_overnight.iter().enumerate() {
                if let Some(slot) = self.day_buffer.components[crate::market::factors::OVERNIGHT_SLOT].get_mut(before + i) {
                    *slot += v;
                }
            }
            self.pending_overnight.clear();
        }
    }

    /// Write the day's jump into the eighth component series, on the last
    /// recorded row of each instrument.
    ///
    /// `apply_jumps` moves `mispricing_s` after the tick loop, so no tick can
    /// carry it. The engine accumulates it in attribution slot 7; this puts it
    /// on the tape where a reader reconstructing the day will find it.
    fn record_day_jump(&mut self) {
        let jumps: Vec<f64> = self.inner.attribution().iter().map(|row| row[crate::market::factors::JUMP_SLOT]).collect();
        if jumps.iter().any(|v| *v != 0.0) {
            self.pending_jump = jumps;
        }
    }

    /// The daily macro step, run at every day boundary -- the explicit
    /// `close_market` and the `close_at_end` session path alike, so the two
    /// spellings of one close roll one world.
    ///
    fn written<'a>(&self, buf: &'a [f64]) -> &'a [f64] {
        let n = self.buffer.ticks_written * self.buffer.companies;
        let end = if n > buf.len() { buf.len() } else { n };
        &buf[..end]
    }

    /// One field across every instrument: the equities, then the rate
    /// instruments. Every per-instrument surface on the Python side is this
    /// width and in this order, which is the order of `tickers`.
    fn all_column(&self, field: PriceField) -> Vec<f64> {
        let mut values = self.inner.column(field);
        if !self.inner.rates().is_empty() {
            values.extend(self.inner.rate_column(field));
        }
        values
    }

    /// A per-equity column widened to every instrument, with `fill` in the
    /// rate instruments' slots.
    fn padded(&self, mut values: Vec<f64>, fill: f64) -> Vec<f64> {
        values.resize(values.len() + self.inner.rates().len(), fill);
        values
    }
}

/// Resolve the `model=` argument: `None` is the shipped preset, a string
/// names a shipped preset, a `ModelParams` is taken as built. Anything else
/// is refused by type, so `model=0.12` cannot silently run the default.
pub fn model_params_from(
    model: Option<&Bound<'_, PyAny>>,
) -> PyResult<crate::params::ModelParams> {
    let Some(value) = model else {
        return Ok(crate::engine::Engine::default_model());
    };
    if let Ok(name) = value.extract::<String>() {
        return crate::params::ModelParams::preset(&name).ok_or_else(|| {
            ValidationError::new_err(format!(
                "unknown model preset {name:?}. Shipped presets: {}. For a \
                 modified model, pass ModelParams.from_preset(name, ...) \
                 instead of a string.",
                crate::params::ModelParams::preset_names().join(", ")
            ))
        });
    }
    if let Ok(params) = value.extract::<PyRef<'_, crate::python_params::PyModelParams>>() {
        return Ok(params.inner.clone());
    }
    Err(ValidationError::new_err(format!(
        "model must be a preset name or a ModelParams, got {}",
        value.get_type().name().map(|n| n.to_string()).unwrap_or_default()
    )))
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
        None => PyMacro::new(15.0, 0.025, None, 0.02, 0.0, 1.0, 50.0, "expansion")?.to_core(),
    })
}

/// Split a universe into its equities and its rate instruments.
///
/// Rate instruments must come after every equity. The engine keeps the two in
/// separate blocks, equities first, and every per-instrument surface (tickers,
/// prices, columns, the tape) lists them in that order; accepting a rate index
/// in the middle would either reorder the caller's roster, which is
/// contractual, or put two orders on one market. So the order the caller
/// wrote must already be that order. `Universe.random(..., bonds=True)` and
/// `Universe.with_bonds()` append them.
///
/// A universe of rate instruments alone is refused: the curve they read is
/// the economy's, and the economy steps with an equity market.
pub fn split_roster(
    universe: &[PyInstrument],
) -> PyResult<(Vec<PyInstrument>, Vec<PyInstrument>)> {
    let mut equities = Vec::new();
    let mut rates: Vec<PyInstrument> = Vec::new();
    for inst in universe {
        if inst.is_rate() {
            if rates.iter().any(|r| r.ticker == inst.ticker) {
                return Err(ValidationError::new_err(format!(
                    "{} is listed twice. Each rate index is one instrument; list it once.",
                    inst.ticker
                )));
            }
            rates.push(inst.clone());
        } else {
            if !rates.is_empty() {
                return Err(ValidationError::new_err(format!(
                    "{} is an equity listed after the rate instrument {}. Rate \
                     instruments must come after every equity in the roster, \
                     because the engine lists them in that order and roster order \
                     is contractual. Append them: Universe.with_bonds() does.",
                    inst.ticker, rates[0].ticker
                )));
            }
            equities.push(inst.clone());
        }
    }
    if let Some(clash) = equities
        .iter()
        .find(|e| rates.iter().any(|r| r.ticker == e.ticker))
    {
        return Err(ValidationError::new_err(format!(
            "the equity {0} carries the ticker of the rate index {0}. Order flow \
             and books are found by ticker, so the two would trade as one; rename \
             the equity.",
            clash.ticker
        )));
    }
    if equities.is_empty() && !rates.is_empty() {
        return Err(ValidationError::new_err(
            "this universe holds rate instruments and no equities. The curve they \
             are priced off is the economy's, which steps with an equity market; \
             add equities, for example Universe.random(20, seed=..., bonds=True).",
        ));
    }
    Ok((equities, rates))
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
        e.qe_assets_ratio = self.qe_assets_ratio;
        e.fear_greed_index = self.fear_greed_index;
        e.cycle_phase = CyclePhase::from_name(&self.cycle).expect("validated at construction");
        e
    }
}

/// What one tick produced.
#[pyclass(name = "TickResult", module = "tradefloor._core", frozen, get_all)]
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
        "previous_tick_price" => PriceField::PreviousTickPrice,
        "mispricing_s_prev_close" => PriceField::MispricingSPrevClose,
        "mispricing_momentum" => PriceField::MispricingMomentum,
        "last_daily_return" => PriceField::LastDailyReturn,
        "avg_volume" => PriceField::AvgVolume,
        "beta" => PriceField::Beta,
        "short_interest" => PriceField::ShortInterest,
        "float_shares" => PriceField::FloatShares,
        other => {
            return Err(ValidationError::new_err(format!(
                "unknown field {other:?}. Valid: {}",
                COLUMN_FIELDS.join(", ")
            )))
        }
    })
}

/// One day's sessions, concatenated in the order they ran.
///
/// Holds the same columns as [`SessionBuffer`] and nothing else; the only
/// difference is that it appends where the session buffer overwrites.
#[derive(Debug, Default, Clone)]
struct DayBuffer {
    ticks: usize,
    companies: usize,
    prices: Vec<f64>,
    volumes: Vec<f64>,
    mispricing: Vec<f64>,
    fundamental: Vec<f64>,
    anchor: Vec<f64>,
    components: [Vec<f64>; crate::market::factors::COMPONENT_COUNT],
    shock: Vec<f64>,
    absorbed: Vec<f64>,
    clamp: Vec<f64>,
    /// Empty on a day whose sessions ran without the depth counterfactual.
    ///
    /// A session that ran with it appends; one that did not appends nothing.
    /// A day whose sessions disagree therefore ends SHORT, and `prints`
    /// reports that day without the two columns rather than serving a column
    /// that stops part way through it. `settle_depth_counterfactual` is
    /// documented as a setting to choose before a run for this reason.
    unbounded_print: Vec<f64>,
    liquidity_share: Vec<f64>,
}

impl DayBuffer {
    fn clear(&mut self) {
        self.ticks = 0;
        self.prices.clear();
        self.volumes.clear();
        self.mispricing.clear();
        self.fundamental.clear();
        self.anchor.clear();
        self.shock.clear();
        self.absorbed.clear();
        self.clamp.clear();
        self.unbounded_print.clear();
        self.liquidity_share.clear();
        for column in self.components.iter_mut() {
            column.clear();
        }
    }
}

/// A whole market, stepped through time.
///
/// `Clone` is what [`PyEngine::fork`] is made of, and it is derived rather
/// than written so that a field added here is carried into a fork without
/// anyone remembering to carry it. See the note on [`Engine`].
fn stream_name(id: u32) -> &'static str {
    match id {
        crate::rng::stream::MARKET => "market",
        crate::rng::stream::ECONOMY => "economy",
        crate::rng::stream::EXTERNAL => "external",
        crate::rng::stream::JUMPS => "jumps",
        crate::rng::stream::VOLUME => "volume",
        crate::rng::stream::NEWS => "news",
        crate::rng::stream::VOLUME_IDIO => "volume_idio",
        crate::rng::stream::OVERNIGHT => "overnight",
        crate::rng::stream::MARKET_VOL_LEVEL => "market_vol_level",
        crate::rng::stream::CRISIS_EPICENTRE => "crisis_epicentre",
        _ => "unknown",
    }
}

fn stream_id(name: &str) -> PyResult<u32> {
    Ok(match name {
        "market" => crate::rng::stream::MARKET,
        "economy" => crate::rng::stream::ECONOMY,
        "external" => crate::rng::stream::EXTERNAL,
        "jumps" => crate::rng::stream::JUMPS,
        "volume" => crate::rng::stream::VOLUME,
        "news" => crate::rng::stream::NEWS,
        "volume_idio" => crate::rng::stream::VOLUME_IDIO,
        "overnight" => crate::rng::stream::OVERNIGHT,
        "market_vol_level" => crate::rng::stream::MARKET_VOL_LEVEL,
        "crisis_epicentre" => crate::rng::stream::CRISIS_EPICENTRE,
        other => {
            return Err(ValidationError::new_err(format!(
                "unknown stream {other:?}; one of market, economy, external, jumps, volume, news, volume_idio, overnight, market_vol_level, crisis_epicentre"
            )))
        }
    })
}

/// Which sector a name belongs to, on the roster `engine` holds.
///
/// The KEY rather than its position, so the Python side derives the
/// sector factor's tag from `tradefloor.sectors()` instead of taking a
/// number this function worked out. Not two sources: that wrapper reads
/// the same array, and an engine refuses an unknown sector at
/// construction, so the two cannot disagree. What it buys is that the
/// tag becomes recomputable from published values, so a check can derive
/// it independently of the code under it rather than reading back an
/// integer this function produced. Resolved against whichever engine is
/// asked, because a kept copy's roster and the live one differ after a
/// delisting.
///
/// Empty where the name is not on that engine's roster, which the caller
/// refuses before it reaches the tag.
fn sector_of(engine: &PyEngine, ticker: &str) -> String {
    engine
        .tickers
        .iter()
        .position(|t| t == ticker)
        .and_then(|i| engine.inner.companies().get(i).map(|c| c.sector.clone()))
        .unwrap_or_default()
}

fn draw_kind(name: &str) -> PyResult<crate::rng::DrawKind> {
    Ok(match name {
        "uniform" => crate::rng::DrawKind::Uniform,
        "normal" => crate::rng::DrawKind::Normal,
        other => {
            return Err(ValidationError::new_err(format!(
                "unknown draw kind {other:?}; uniform or normal"
            )))
        }
    })
}

/// One kept day: the engine as it stood before that day opened, and where
/// that day's inputs begin in the run log.
///
/// `Box` because the copy is a `PyEngine` and a `PyEngine` holds the store,
/// so the type is recursive. The copy's own store is empty, which is what
/// makes the recursion one level deep however many days are kept.
#[derive(Clone)]
struct KeptOpen {
    engine: Box<PyEngine>,
    log_start: usize,
}

/// The days `explain` can reach, and the copies it reaches them through.
///
/// Bookkeeping, off until [`PyEngine::keep_explanations`] asks for it, and
/// outside [`PyEngine::state_snapshot`] and [`PyEngine::state_hash`]: a copy
/// taken here is read and never run, so an engine with a window open produces
/// the same market as one without. The known-answer digest is the same digest
/// with the window open, which `tests/test_explain.py` states.
///
/// The cost is one engine copy per kept day, without the recorded tape, so a
/// window is asked for over the days a caller means to explain rather than
/// over a whole run.
#[derive(Clone, Default)]
struct Explanations {
    window: Option<(i64, i64)>,
    opens: std::collections::BTreeMap<i64, KeptOpen>,
}

impl Explanations {
    fn wants(&self, day: i64) -> bool {
        match self.window {
            Some((from, to)) => from <= day && day <= to,
            None => false,
        }
    }
}

#[pyclass(name = "Engine", module = "tradefloor._core")]
#[derive(Clone)]
pub struct PyEngine {
    inner: Engine,
    buffer: SessionBuffer,
    /// The jump the last close applied to `s`, waiting for the row where its
    /// effect is observed: the FIRST tick of the next day (§74).
    pending_jump: Vec<f64>,
    /// The overnight move the last open applied to `s`, waiting for the
    /// row where its effect is observed: the first tick of the same day.
    pending_overnight: Vec<f64>,
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
    /// Every session since the last `open_market`, concatenated.
    ///
    /// `SessionBuffer` is, as its name and docs say, the LAST session's path:
    /// `resize` rewrites it from tick zero each time. That is right for
    /// `prices()`, and it was silently wrong for `record`, which is named for
    /// a DAY. An agent-shaped run calls `run_session` once per step -- the
    /// harness does exactly this -- so `record(day)` kept the last step and
    /// discarded the rest of the day. Four steps a day meant a tape with 75%
    /// of its ticks missing, with nothing to indicate it: the table was
    /// well-formed, self-consistent and short.
    ///
    /// Accumulating here rather than changing `SessionBuffer` keeps
    /// `prices()` meaning what it has always meant, and costs a copy per
    /// session that only a recording caller pays.
    day_buffer: DayBuffer,
    /// Whether the market has been opened and not yet closed.
    ///
    /// Exists so a day is opened exactly ONCE however many sessions it is made
    /// of. `Engine::run_session` used to open unconditionally, which made the
    /// attribution accumulator and the daily open per-session; see
    /// `SessionRequest::reopen`.
    market_open: bool,
    /// Completed days, counted at `close_market`.
    ///
    /// This is the macro chain's clock: it becomes `game_day` and (times
    /// 1,440 minutes) the timestamp the central bank's meeting calendar runs
    /// on. It rides in `state()` snapshots because a fork that restarted the
    /// clock would re-run day-dependent macro branches -- the OPEC cycle, the
    /// meeting schedule -- differently from the engine it forked from.
    day_count: u32,
    recorded_macro: Vec<crate::python_arrow::MacroRow>,
    /// Recorded order-book depth. Empty unless a caller asks for it.
    recorded_book: Vec<crate::python_arrow::BookRow>,
    /// Every input that crossed into this engine, in order.
    ///
    /// Inputs only. Prices, attribution and draw counts are consequences,
    /// and recording them would create a second source of truth that could
    /// disagree with the first.
    log: Vec<crate::python_log::LogEntry>,
    /// What `explain` reaches a day through. Recording only: nothing here is
    /// read by the tick, the snapshot or the hash.
    explanations: Explanations,
}

/// The day stamp, kept off the Python surface.
///
/// `open_market_on` is how `open_market` and `run_days` agree on the day
/// a mark and the day's news draws carry, and it is not something a caller
/// reaches for: a caller that wants its own numbering has `set_day`. Held
/// in a plain `impl` rather than `#[pymethods]` for that reason, since
/// every method of a `#[pymethods]` block becomes a binding and every
/// binding has to be declared in the stub.
impl PyEngine {
    /// Roll the day's opening marks, numbering the day `day`.
    ///
    /// The day is stamped BEFORE `inner.open_market()`, and the order is the
    /// whole of it. That call pushes the day mark and takes the day's
    /// endogenous news draws, so both carry whatever number was stamped when
    /// it ran. `run_days` used to open first and re-stamp afterwards, which
    /// left one run carrying two numbers: `run_days(3, first_day=100)` logged
    /// the market, economy, jumps, volume and per-name volume streams on days
    /// 100, 101 and 102, the news stream on 0, 1 and 2, and `day_marks()` on
    /// 0, 1 and 2, so `market_day_layout(100)` found nothing while
    /// `market_day_layout(0)` returned a mark for a day the log called 100.
    /// Two `run_days(2)` calls in a row reached the same split without any
    /// `first_day` at all.
    fn open_market_on(&mut self, day: i64) {
        // The explanation copy is taken before anything about the day has
        // happened, so a fork of it runs the day from its own open under the
        // inputs the log records from here on. Taken with the store moved
        // out, because cloning `self` with the store in place would copy
        // every day already kept, once per day kept.
        if self.explanations.wants(day) {
            let held = std::mem::take(&mut self.explanations);
            let mut copy = self.clone();
            self.explanations = held;
            // The tape is a consequence, and a copy of a year of it per kept
            // day is what makes this unaffordable. A replay records the one
            // day it runs, which is the day being explained.
            copy.recorded.clear();
            copy.recorded_macro.clear();
            copy.recorded_book.clear();
            copy.day_buffer.clear();
            // And the draw log, which is the size of the tape. Without
            // this a copy carried every entry logged before its day, so
            // N kept days held N squared over two days of log and a
            // thirty-day window at forty names cost 1.6 GB. The copy is
            // never asked what it recorded: the tree reads the source
            // engine's log, and the copy is only forked and replayed.
            copy.inner.clear_draw_log_records();
            let log_start = self.log.len();
            self.explanations.opens.insert(
                day,
                KeptOpen {
                    engine: Box::new(copy),
                    log_start,
                },
            );
        }
        self.log.push(crate::python_log::LogEntry::OpenMarket);
        // A new day's tape starts here. Without this, a run that never closed
        // would grow one unbounded "day".
        self.day_buffer.clear();
        self.market_open = true;
        // The day a draw carries in the draw log is the day whose open it
        // follows, so the jumps, volume and macro draws taken at a close
        // belong to the day they close rather than to the one after.
        self.inner.set_current_day(day);
        self.inner.open_market();
        // The overnight move the open applied to `s`, for the tape: booked
        // onto the day's first row, where its effect is observed, as the
        // close's jump is booked onto the next day's (§74).
        let nights: Vec<f64> = self.inner.overnight_moves().to_vec();
        if nights.iter().any(|v| *v != 0.0) {
            self.pending_overnight = nights;
        }
    }

    /// The roster operations between the previous day's open and this one.
    ///
    /// Walks back from `log_start` to the open before it and reports every
    /// `list_instrument` and `delist` in that gap, as `(operation, what)`.
    /// A roster operation there moves the slots the previous day's tape is
    /// keyed by, so a level read from it at today's slot is another
    /// company's. The log is what KNOWS: comparing the two days' tape
    /// widths misses a listing paired with a delisting, and comparing the
    /// two days' rosters needs the day before to have been kept.
    fn roster_ops_before(&self, log_start: usize) -> Vec<(String, String)> {
        let mut out = Vec::new();
        // Spelled as a comparison rather than `.min`, because the parity
        // scan keeps `.min` and `.max` inside `mathx` even on integers.
        let end = if log_start > self.log.len() {
            self.log.len()
        } else {
            log_start
        };
        let start = self.log[..end]
            .iter()
            .rposition(|e| matches!(e, crate::python_log::LogEntry::OpenMarket))
            .unwrap_or(0);
        for entry in &self.log[start..end] {
            match entry {
                crate::python_log::LogEntry::ListInstrument { ticker, .. } => {
                    out.push(("list_instrument".to_string(), ticker.clone()))
                }
                crate::python_log::LogEntry::Delist { index } => {
                    out.push(("delist".to_string(), index.to_string()))
                }
                _ => {}
            }
        }
        out
    }

    /// The log entries of the day whose open sits at `log_start`.
    ///
    /// From that open to the close that ends the day, inclusive, plus any
    /// `record` that follows the close. A day ends at an explicit
    /// `close_market` or at a session that carried `close_at_end`, which
    /// are the two spellings of one close, and a day that never closed
    /// runs to the end of the log.
    ///
    /// The trailing records are here because a caller who closes through
    /// the session records AFTER the close, and stopping at the close
    /// dropped that entry out of the day. What the entry carries is the
    /// label the day's rows have on the tape, so losing it left the
    /// explanation reading the tape at the day the store keyed the open
    /// by, which is a different day's rows whenever the two disagree.
    fn day_inputs(&self, py: Python<'_>, log_start: usize) -> PyResult<Vec<PyObject>> {
        let mut out = Vec::new();
        let mut closed = false;
        for entry in self.log.iter().skip(log_start) {
            if closed {
                match entry {
                    crate::python_log::LogEntry::Record { .. } => {
                        out.push(entry.to_py(py)?);
                        continue;
                    }
                    _ => break,
                }
            }
            out.push(entry.to_py(py)?);
            match entry {
                crate::python_log::LogEntry::CloseMarket => closed = true,
                crate::python_log::LogEntry::RunSession { close_at_end, .. }
                    if *close_at_end =>
                {
                    closed = true
                }
                _ => {}
            }
        }
        Ok(out)
    }
}

/// The agents' half of the binding, kept off the Python surface.
impl PyEngine {
    fn submit_one(
        &mut self,
        agent: &str,
        ticker: &str,
        quantity: f64,
        limit_price: Option<f64>,
        order_id: Option<String>,
    ) -> PyResult<crate::engine::OrderReport> {
        if !quantity.is_finite() || quantity == 0.0 {
            return Err(ValidationError::new_err(format!(
                "quantity must be non-zero and finite, got {quantity}"
            )));
        }
        if !self.tickers.iter().any(|t| t == ticker) {
            return Err(ValidationError::new_err(format!(
                "no instrument with ticker {ticker:?} in this universe"
            )));
        }
        let side = if quantity > 0.0 {
            crate::order_book::Side::Buy
        } else {
            crate::order_book::Side::Sell
        };
        // Validated by the core before it changes anything, so a refused
        // order is logged by nobody: it did not happen.
        let report = self
            .inner
            .submit_order(agent, ticker, side, quantity.abs(), limit_price, order_id.clone())
            .map_err(crate::python_book::OrderError::new_err)?;
        self.log.push(crate::python_log::LogEntry::Submit {
            agent: agent.to_string(),
            ticker: ticker.to_string(),
            quantity,
            limit_price,
            order_id,
        });
        Ok(report)
    }
}

fn side_str(side: crate::order_book::Side) -> &'static str {
    match side {
        crate::order_book::Side::Buy => "buy",
        crate::order_book::Side::Sell => "sell",
    }
}

fn parse_side_str(s: &str) -> PyResult<crate::order_book::Side> {
    match s {
        "buy" => Ok(crate::order_book::Side::Buy),
        "sell" => Ok(crate::order_book::Side::Sell),
        other => Err(ValidationError::new_err(format!("unknown side {other:?}"))),
    }
}

fn fill_to_py(py: Python<'_>, f: &crate::agent_book::AgentFill) -> PyResult<PyObject> {
    let d = PyDict::new_bound(py);
    d.set_item("agent", &f.agent)?;
    d.set_item("order_id", &f.order_id)?;
    d.set_item("ticker", &f.ticker)?;
    d.set_item("side", side_str(f.side))?;
    d.set_item("quantity", f.quantity)?;
    d.set_item("price", f.price)?;
    d.set_item("liquidity", f.liquidity.as_str())?;
    d.set_item("counterparty", &f.counterparty)?;
    d.set_item("reference", f.reference)?;
    d.set_item("day", f.day)?;
    d.set_item("tick", f.tick)?;
    d.set_item("sequence", f.sequence)?;
    Ok(d.into())
}

fn order_to_py(py: Python<'_>, o: &crate::agent_book::AgentOrder) -> PyResult<PyObject> {
    let d = PyDict::new_bound(py);
    d.set_item("order_id", &o.id)?;
    d.set_item("agent", &o.agent)?;
    d.set_item("ticker", &o.ticker)?;
    d.set_item("side", side_str(o.side))?;
    d.set_item("limit_price", o.limit)?;
    d.set_item("quantity", o.quantity)?;
    d.set_item("remaining", o.remaining)?;
    d.set_item("sequence", o.sequence)?;
    d.set_item("mode", o.mode.as_str())?;
    Ok(d.into())
}

fn report_to_py(py: Python<'_>, r: &crate::engine::OrderReport) -> PyResult<PyObject> {
    let d = PyDict::new_bound(py);
    d.set_item("order_id", &r.order_id)?;
    d.set_item("agent", &r.agent)?;
    d.set_item("ticker", &r.ticker)?;
    d.set_item("side", side_str(r.side))?;
    d.set_item("requested", r.requested)?;
    d.set_item("filled", r.filled)?;
    d.set_item("average_price", r.average_price)?;
    d.set_item("worst_price", r.worst_price)?;
    d.set_item("reference", r.reference)?;
    d.set_item("resting", r.resting)?;
    d.set_item("unfilled", r.unfilled)?;
    d.set_item("mode", r.mode.map(|m| m.as_str()))?;
    let fills: PyResult<Vec<PyObject>> = r.fills.iter().map(|f| fill_to_py(py, f)).collect();
    d.set_item("fills", fills?)?;
    Ok(d.into())
}

/// The snapshot's `book` entry. See `Engine::state_hash`, which hashes the
/// same fields in the same order.
fn book_to_py(py: Python<'_>, book: &crate::agent_book::BookState) -> PyResult<Py<PyDict>> {
    let d = PyDict::new_bound(py);
    d.set_item("sequence", book.sequence)?;
    d.set_item("fill_sequence", book.fill_sequence)?;
    let flat: Vec<f64> = book.taken.iter().flat_map(|row| row.iter().copied()).collect();
    d.set_item("taken", f64_bytes(py, &flat))?;
    let orders: PyResult<Vec<PyObject>> = book.orders.iter().map(|o| order_to_py(py, o)).collect();
    d.set_item("orders", orders?)?;
    let flow = pyo3::types::PyList::empty_bound(py);
    for (agent, ticker, bought, sold) in &book.flow {
        flow.append((agent.as_str(), ticker.as_str(), *bought, *sold))?;
    }
    d.set_item("flow", flow)?;
    let fills: PyResult<Vec<PyObject>> = book.fills.iter().map(|f| fill_to_py(py, f)).collect();
    d.set_item("fills", fills?)?;
    let impacts = pyo3::types::PyList::empty_bound(py);
    for r in &book.impacts {
        let x = PyDict::new_bound(py);
        x.set_item("agent", &r.agent)?;
        x.set_item("ticker", &r.ticker)?;
        x.set_item("bought", r.bought)?;
        x.set_item("sold", r.sold)?;
        x.set_item("permanent", r.permanent)?;
        x.set_item("day", r.day)?;
        x.set_item("tick", r.tick)?;
        impacts.append(x)?;
    }
    d.set_item("impacts", impacts)?;
    Ok(d.into())
}

fn book_from_py(d: &Bound<'_, PyDict>) -> PyResult<crate::agent_book::BookState> {
    use crate::agent_book::*;
    let get = |k: &str| -> PyResult<Bound<'_, pyo3::PyAny>> {
        d.get_item(k)?
            .ok_or_else(|| ValidationError::new_err(format!("the snapshot's book has no {k:?}")))
    };
    let mut state = BookState {
        sequence: get("sequence")?.extract()?,
        fill_sequence: get("fill_sequence")?.extract()?,
        ..Default::default()
    };
    let raw: Vec<u8> = get("taken")?.extract()?;
    if raw.len() % (8 * TAKEN_WIDTH) != 0 {
        return Err(ValidationError::new_err("the snapshot's book `taken` is not whole rows"));
    }
    for row in raw.chunks(8 * TAKEN_WIDTH) {
        let mut r = [0.0; TAKEN_WIDTH];
        for (k, bytes) in row.chunks(8).enumerate() {
            let mut b = [0u8; 8];
            b.copy_from_slice(bytes);
            r[k] = f64::from_le_bytes(b);
        }
        state.taken.push(r);
    }
    for item in get("orders")?.iter()? {
        let o = item?;
        let o = o.downcast::<PyDict>()?;
        let g = |k: &str| -> PyResult<Bound<'_, pyo3::PyAny>> {
            o.get_item(k)?
                .ok_or_else(|| ValidationError::new_err(format!("a snapshot order has no {k:?}")))
        };
        let side: String = g("side")?.extract()?;
        let mode: String = g("mode")?.extract()?;
        state.orders.push(AgentOrder {
            id: g("order_id")?.extract()?,
            agent: g("agent")?.extract()?,
            ticker: g("ticker")?.extract()?,
            side: parse_side_str(&side)?,
            limit: g("limit_price")?.extract()?,
            quantity: g("quantity")?.extract()?,
            remaining: g("remaining")?.extract()?,
            sequence: g("sequence")?.extract()?,
            mode: RestMode::parse(&mode)
                .ok_or_else(|| ValidationError::new_err(format!("unknown order mode {mode:?}")))?,
        });
    }
    for item in get("flow")?.iter()? {
        let (agent, ticker, bought, sold): (String, String, f64, f64) = item?.extract()?;
        state.flow.push((agent, ticker, bought, sold));
    }
    for item in get("fills")?.iter()? {
        let f = item?;
        let f = f.downcast::<PyDict>()?;
        let g = |k: &str| -> PyResult<Bound<'_, pyo3::PyAny>> {
            f.get_item(k)?
                .ok_or_else(|| ValidationError::new_err(format!("a snapshot fill has no {k:?}")))
        };
        let side: String = g("side")?.extract()?;
        let liquidity: String = g("liquidity")?.extract()?;
        state.fills.push(AgentFill {
            agent: g("agent")?.extract()?,
            order_id: g("order_id")?.extract()?,
            ticker: g("ticker")?.extract()?,
            side: parse_side_str(&side)?,
            quantity: g("quantity")?.extract()?,
            price: g("price")?.extract()?,
            liquidity: Liquidity::parse(&liquidity).ok_or_else(|| {
                ValidationError::new_err(format!("unknown liquidity {liquidity:?}"))
            })?,
            counterparty: g("counterparty")?.extract()?,
            reference: g("reference")?.extract()?,
            day: g("day")?.extract()?,
            tick: g("tick")?.extract()?,
            sequence: g("sequence")?.extract()?,
        });
    }
    for item in get("impacts")?.iter()? {
        let r = item?;
        let r = r.downcast::<PyDict>()?;
        let g = |k: &str| -> PyResult<Bound<'_, pyo3::PyAny>> {
            r.get_item(k)?
                .ok_or_else(|| ValidationError::new_err(format!("a snapshot impact has no {k:?}")))
        };
        state.impacts.push(AgentImpact {
            agent: g("agent")?.extract()?,
            ticker: g("ticker")?.extract()?,
            bought: g("bought")?.extract()?,
            sold: g("sold")?.extract()?,
            permanent: g("permanent")?.extract()?,
            day: g("day")?.extract()?,
            tick: g("tick")?.extract()?,
        });
    }
    Ok(state)
}

#[pymethods]
impl PyEngine {
    /// Build an engine over a universe.
    ///
    /// `seed` is required, never defaulted. A simulator that seeds itself from
    /// the clock when you forget produces a run nobody can reproduce, and the
    /// failure is invisible until someone tries.
    ///
    /// `model` selects the coefficient set: a shipped preset's name
    /// (`"pt-v1"`, the default) or a `ModelParams`. The escape hatch is
    /// deliberately ceremonial, because the fingerprint means an overridden run
    /// can never silently masquerade as the benchmark model (API §3).
    #[new]
    #[pyo3(signature = (*, seed, universe, macro_state = None, model = None))]
    fn new(
        seed: u32,
        universe: Vec<PyInstrument>,
        macro_state: Option<PyMacro>,
        model: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        if universe.is_empty() {
            return Err(ValidationError::new_err(
                "universe is empty - an engine with no instruments has nothing to simulate",
            ));
        }
        let params = model_params_from(model)?;
        // WHETHER THE OPENING IS THE MODEL'S TO SETTLE. `macro_burn_in_days`
        // exists to relax the CONSTRUCTOR'S default macro, which otherwise
        // opens every run in expansion at phase age zero. A caller who
        // passes `macro_state` has named an opening instead, and settling it
        // for 755 days discards what they asked for -- measured at 0.7.0, an
        // engine asked for a VIX of 45.0 and a policy rate of 5 per cent
        // opened at 21.55 and 0.00. This is the only place that knows the
        // difference: by the time the core has an `EconomyState`, a supplied
        // macro and the default one look the same.
        let settle_opening = macro_state.is_none();
        let economy = economy_from(macro_state)?;
        let (equities, rates) = split_roster(&universe)?;
        let companies: Vec<TickCompany> = equities
            .iter()
            .enumerate()
            .map(|(i, inst)| inst.to_core(i))
            .collect();
        let tickers = universe.iter().map(|i| i.ticker.clone()).collect();

        let mut engine = Self {
            inner: Engine::with_params_from_opening(
                seed,
                companies,
                economy,
                create_initial_central_bank_state(0),
                crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
                params,
                settle_opening,
            ),
            buffer: SessionBuffer::new(),
            pending_jump: Vec::new(),
            pending_overnight: Vec::new(),
            day_buffer: DayBuffer::default(),
            market_open: false,
            day_count: 0,
            tickers,
            recorded: Vec::new(),
            recorded_macro: Vec::new(),
            recorded_book: Vec::new(),
            log: Vec::new(),
            explanations: Explanations::default(),
        };
        // After construction, so a settled opening has run its burn-in and
        // the indices are marked at the curve the run actually starts from.
        if !rates.is_empty() {
            engine
                .inner
                .set_rate_instruments(rates.iter().map(|i| i.to_rate()).collect());
        }
        Ok(engine)
    }

    /// Roll the day's opening marks. Call once before the session's ticks.
    ///
    /// Numbers the day from the engine's own counter. `run_days` numbers it
    /// from `first_day` instead, through [`Self::open_market_on`].
    fn open_market(&mut self) {
        self.open_market_on(i64::from(self.day_count));
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
    ///
    /// # Two kinds of order flow, and which one an agent's trades are
    ///
    /// `fills` is what a trader filled at the step boundary just before this
    /// session, `{ticker: (bought, sold)}` in shares, which is what
    /// `Portfolio.pending_flow()` returns. It reaches the market ONCE, on the
    /// session's first tick, whatever `ticks` is. That is the argument an
    /// agent loop wants.
    ///
    /// `flow_per_tick` is a standing rate instead: that many shares bought
    /// and sold on EVERY tick of the session, a program that trades all
    /// session long. `tf.flow_impact` uses it. Handing an agent's fills to
    /// it counts one order once a minute for the whole step.
    ///
    /// `order_flow` is refused here since 0.8.5, because it was the second
    /// kind under a name that read as the first: every harness in the
    /// package passed an agent's fills through it, so one order was counted
    /// on each of a step's 65 ticks and landed after the fill it came from.
    /// `tick(order_flow=...)` is unchanged, since a tick is one minute.
    #[pyo3(signature = (
        hour, minute, day_of_week, ticks, *, volatility = 1.0,
        close_at_end = false, news = None, news_impacts = None, fills = None,
        flow_per_tick = None, order_flow = None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn run_session(
        &mut self,
        py: Python<'_>,
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
        fills: Option<std::collections::HashMap<String, (f64, f64)>>,
        flow_per_tick: Option<std::collections::HashMap<String, (f64, f64)>>,
        order_flow: Option<std::collections::HashMap<String, (f64, f64)>>,
    ) -> PyResult<usize> {
        if order_flow.is_some() {
            // Refused rather than kept under its old meaning, so a caller
            // still passing `order_flow=portfolio.pending_flow()` is told
            // rather than left counting one order on every tick. Before any
            // other check and before anything is logged, so a refused call
            // leaves no trace.
            return Err(ValidationError::new_err(
                "run_session no longer takes order_flow (0.8.5). It held the \
                 flow on every tick of the session, so an agent's fills passed \
                 through it were counted once a minute for the whole step. Pass \
                 an agent's trades as fills=portfolio.pending_flow(), which \
                 reaches the market once, on the session's first tick. Pass \
                 flow_per_tick= for a standing rate of shares a minute held for \
                 the whole session, which is what order_flow= did.",
            ));
        }
        if ticks == 0 {
            return Err(ValidationError::new_err("ticks must be greater than zero"));
        }
        let session_news = self.build_news(news)?;
        let session_impacts = self.build_impacts(news_impacts)?;
        let session_flow = self.build_flow(flow_per_tick)?;
        let session_fills = self.build_flow(fills)?;
        // Open the day here if the caller has not, and exactly once however
        // many sessions the day is made of. Letting `run_session` re-open made
        // attribution and the daily anchor per-STEP; see
        // `SessionRequest::reopen`.
        //
        // BEFORE the session is logged, and that ordering is the whole point.
        // `open_market` writes its own log entry, so auto-opening after the
        // push recorded "run a session, then open the market" -- a log that
        // replayed to different prices, because replaying it opened the market
        // in the middle of the day instead of at the start.
        if !self.market_open {
            self.open_market();
        }
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
            fills: session_fills
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

        // The GIL is released for the compute, and that is what makes a
        // parallel sweep possible at all.
        //
        // `run_many` used processes before this, because a thread pool holding
        // the GIL would have run the sweep serially with extra bookkeeping.
        // Processes cost a universe serialised per worker -- and on Windows
        // they HANG: the spawn start method re-imports `__main__`, which does
        // not exist for a REPL, a notebook or a piped script, so the children
        // die and the parent waits forever. A ten-minute wait for a twenty-seed
        // sweep, with no error.
        //
        // Nothing Python is touched inside: news, impacts and flow are already
        // converted to Rust types above, and the engine and buffer are plain
        // data. That is the precondition for releasing it, not an optimisation
        // note.
        let inner = &mut self.inner;
        let buffer = &mut self.buffer;
        py.allow_threads(move || {
            inner.run_session(
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
                    fills: &session_fills,
                    close_at_end,
                    reopen: false,
                    daily_innovations: &innovations,
                    sector_base_variances: &variances,
                    stop: None,
                },
                buffer,
            )
        });
        self.accumulate_session();
        if close_at_end {
            // The core ran the close bookkeeping; the daily macro step
            // belongs to the same boundary. Without this the two spellings
            // of one close -- `run_session(close_at_end=True)` and
            // `run_session(); close_market()` -- would roll different
            // worlds, which is exactly the divergence the equivalence tests
            // exist to forbid.
            self.day_count += 1;
            self.inner.advance_macro_day(i64::from(self.day_count));
            // AND THE DAY'S JUMP ONTO THE TAPE, which `close_market` does
            // and this path did not. Same argument as the line above, one
            // field further on: a day closed this way applied its jump to
            // the market and never wrote it to the record, so the truth
            // table for a `close_at_end` run was missing a column the
            // explicit close carried.
            //
            // Invisible until 0.7.0. The jump slot is zero unless a jump
            // fired, and pt-v18 switches on `jump_mean_compensated`, whose
            // compensator lands every day; the state hash learned the
            // pending fields in the same release and the two spellings then
            // hashed apart, which is how this surfaced.
            self.record_day_jump();
        }
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
    /// `ledger` is an optional `tradefloor.DayLedger`, which is handed the
    /// state hash after every close and, when it keeps them, the state
    /// itself. It is a callback rather than a return value because a run of
    /// 252 days holds 252 leaves and the caller usually wants them beside a
    /// `RunManifest` rather than in a list this method built.
    ///
    /// Returns the number of days run.
    #[pyo3(signature = (
        days, *, hour = 9, minute = 30, day_of_week = 3,
        ticks_per_day = 390, volatility = 1.0, record = true,
        first_day = None, ledger = None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn run_days(
        &mut self,
        py: Python<'_>,
        days: usize,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        ticks_per_day: usize,
        volatility: f64,
        record: bool,
        first_day: Option<u32>,
        ledger: Option<Py<PyAny>>,
    ) -> PyResult<usize> {
        if days == 0 {
            return Err(ValidationError::new_err("days must be greater than zero"));
        }
        if ticks_per_day == 0 {
            return Err(ValidationError::new_err("ticks_per_day must be greater than zero"));
        }
        // Defaults to the engine's own counter, not to zero. Numbering from
        // zero on every call gave a second run the day numbers of the first:
        // two `run_days(2)` calls on one engine put four simulated days on
        // two numbers, so `draw_log("jumps", 0, 0)` returned two days of
        // draws and `day_marks()` read 0, 1, 0, 1. The counter is what the
        // record and the day marks already advanced on, so following it is
        // what makes the second call continue the first. A caller that
        // wants to restart the numbering passes `first_day=0`.
        let first_day = first_day.unwrap_or(self.day_count);
        // Asked once rather than per day: whether the ledger wants the
        // predecessor states decides how much a later verification costs, and
        // it cannot change halfway through a run.
        let keeps_snapshots: bool = match &ledger {
            Some(l) => l.bind(py).getattr("keeps_snapshots")?.extract()?,
            None => false,
        };
        for offset in 0..days {
            // `first_day` is the day number the record and the truth table
            // carry, so the day mark, the news draws and every stream's log
            // carry it too. Passed INTO the open rather than stamped after
            // it, for the reason `open_market_on` gives.
            self.open_market_on((first_day + offset as u32) as i64);
            self.run_session(py, hour, minute, day_of_week, ticks_per_day, volatility,
                             false, None, None, None, None, None)?;
            // Record BEFORE the close: the close advances the macro chain
            // into the next day, and the macro row for day N must carry the
            // values day N actually traded under, not the ones day N+1 will.
            if record {
                self.record(first_day + offset as u32)?;
            }
            self.close_market();
            // The leaf is taken AFTER the close, so a run that never called
            // `record` still ledgers, and the state a leaf commits to is the
            // one the next day starts from.
            if let Some(l) = &ledger {
                let leaf = self.state_hash();
                let snapshot = if keeps_snapshots {
                    Some(self.state_snapshot(py)?)
                } else {
                    None
                };
                l.bind(py).call_method1("_close", (leaf, snapshot))?;
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
        // The stop reads an equity's print inside the tick loop. A rate
        // index moves only when a yield is written, which no session does,
        // so a band on one could never fire; refused rather than run to
        // `max_ticks` in silence.
        if company >= self.inner.len() {
            return Err(ValidationError::new_err(format!(
                "{ticker} is a rate index, and run_until stops on an equity's \
                 print. An index level moves only when a yield is written, \
                 between sessions or by pin_macro, so a band on it cannot fire \
                 inside one."
            )));
        }

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

        if !self.market_open {
            self.open_market();
        }
        let outcome = self.inner.run_session(
            &SessionRequest {
                start: GameTime { hour, minute, day_of_week },
                ticks: max_ticks,
                volatility_multiplier: volatility,
                news: &[],
                news_impact_queue: &[],
                order_volumes: &[],
                fills: &[],
                close_at_end: false,
                reopen: false,
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
        self.accumulate_session();
        Ok(outcome.halted_at)
    }

    /// Run the close bookkeeping: GARCH update, the daily roll, and the
    /// daily macro step.
    ///
    /// The innovation handed to GARCH is the day's accumulated NOISE, not the
    /// day's total return.
    ///
    /// `DayCloseRequest` documents `None` as falling back to the total
    /// return, and this passed `None` for every company on every close -- so
    /// the fallback was not a fallback, it was the behaviour. Silently: no
    /// error, no implausible number, just a variance process driven by drift
    /// plus news plus flow when the model says it should be driven by the
    /// idiosyncratic shock alone.
    ///
    /// Measured before changing it, over 397 company-days: the two differ by
    /// a median factor of 0.82, a tenth percentile of 0.22 and a ninetieth of
    /// 3.20. Not a rounding difference -- a different quantity.
    ///
    /// # The macro chain advances here
    ///
    /// `Engine::advance_day` -- economy update, cycle transition, central
    /// bank -- runs at the end of every close. Before this it was implemented,
    /// unit-tested, and reachable from nowhere in Python: every macro field
    /// sat at its initial value for the whole run and fair value never
    /// revalued, so the fundamentals anchoring was inert by default. The
    /// recorded design decision (PYTHON-API-DESIGN.md section 6.3) is that the
    /// full chain runs endogenously by default; this is that default, wired.
    ///
    /// The close is the day boundary the reference implementation uses too:
    /// the rates and VIX the factor model reads on the first tick of a new
    /// day are already the day's NEW values.
    ///
    /// Interaction with `pin_macro`: a pin applied at the START of a day (the
    /// `Scenario` convention) overrides whatever the previous close evolved,
    /// so a day-by-day pinned series stays exogenous exactly as before. A
    /// single pin no longer freezes its field forever -- the chain keeps
    /// evolving FROM the pinned value, which is what "everything else keeps
    /// responding" was always meant to say.
    fn close_market(&mut self) {
        self.log.push(crate::python_log::LogEntry::CloseMarket);
        // The day is over, so the next session opens a new one.
        self.market_open = false;
        // The settle-and-advance is `Engine::close_day` in the core, so the
        // WebAssembly binding runs the same day loop rather than a second
        // implementation of it. This surface keeps only the day COUNTER,
        // which is bookkeeping rather than a modelling decision.
        self.day_count += 1;
        self.inner.close_day(i64::from(self.day_count));
        self.record_day_jump();
    }

    /// One column across every instrument, as little-endian f64 bytes.
    ///
    /// Read it with `numpy.frombuffer(buf, dtype="<f8")`, which adopts the
    /// bytes without copying. Values are in roster order, which is
    /// contractual -- see `tickers`.
    ///
    /// Rate instruments come after the equities, as in `tickers`. A field that
    /// does not exist for an index reads NaN there (`garch_variance`, `beta`,
    /// `last_daily_return`, `previous_tick_price`); the mispricing fields read
    /// zero, because an index level is its own fair value.
    fn column(&self, py: Python<'_>, field: &str) -> PyResult<Py<PyBytes>> {
        let f = parse_field(field)?;
        Ok(f64_bytes(py, &self.all_column(f)))
    }

    /// Current price per instrument, as little-endian f64 bytes.
    fn prices(&self, py: Python<'_>) -> Py<PyBytes> {
        f64_bytes(py, &self.all_column(PriceField::Price))
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
    ///
    /// Equities only. A rate index is part of the universe an engine is built
    /// with, and listing one mid-run is refused.
    fn list_instrument(&mut self, instrument: PyInstrument) -> PyResult<usize> {
        if instrument.is_rate() {
            return Err(ValidationError::new_err(format!(
                "{} is a rate index; rate instruments are fixed when the engine \
                 is built. Include it in the universe instead.",
                instrument.ticker
            )));
        }
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
        // Inserted after the last equity, ahead of any rate instruments,
        // which is where the engine puts it. Without rate instruments that is
        // the end of the list, as it always was.
        self.tickers.insert(index, instrument.ticker.clone());
        Ok(self.inner.add_company(core))
    }

    /// Delist the instrument at `index`, returning its ticker.
    ///
    /// The tail keeps its relative order and shifts down by one, so any index
    /// a caller is holding past this point is stale. Re-read `tickers`.
    fn delist(&mut self, index: usize) -> PyResult<String> {
        if index >= self.inner.len() && index < self.inner.instrument_count() {
            return Err(ValidationError::new_err(format!(
                "index {index} is the rate index {}; rate instruments are fixed \
                 when the engine is built and cannot be delisted.",
                self.tickers[index]
            )));
        }
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

    /// Cumulative draws across all three engine streams.
    ///
    /// Two runs that agree here consumed the generators identically, which is
    /// the precondition for their prices agreeing. Diagnostic: it reports
    /// alignment, it does not enforce it. The per-stream split is
    /// `draws_by_stream()`, and since the 2026-08 stream split THAT is the
    /// sharper question: two runs whose `market` counts agree saw the same
    /// market noise even if their macro chains branched apart.
    #[getter]
    fn draws_consumed(&self) -> usize {
        self.inner.draws_consumed()
    }

    /// The honest name of the model this engine runs: a shipped preset's
    /// name when the coefficients are bit-identical to it, and
    /// `custom-XXXXXXXX` otherwise. Joins `seed` and the universe
    /// fingerprint in identifying a run, since a result under a non-shipped
    /// model can never present as a standard one.
    #[getter]
    fn model_fingerprint(&self) -> String {
        self.inner.params().fingerprint()
    }

    /// The VIX at which this engine's variance couplings read ONE.
    ///
    /// `model_params["market_vol_vix_anchor"]` under every preset before
    /// pt-v19, and the value DERIVED from the index's own unconditional
    /// variance under `vix_level_identity`. Exposed because under the
    /// identity it is a property of the run rather than of the model: it
    /// depends on the roster, so no coefficient dictionary can state it and
    /// a preset record that quotes it has to say which universe it was
    /// derived on.
    #[getter]
    fn vix_anchor(&self) -> f64 {
        self.inner.vix_anchor()
    }

    /// The index variance the LAST VIX update read, term by term, or
    /// `None` if no day has advanced under `vix_level_identity`.
    ///
    /// Keys: `factor`, `sector`, `idio`, `crash`, `crisis`, `tilt` (the six
    /// noise blocks BEFORE the
    /// intraday curve), `market_jump`, `idio_jump`, `news`, `k` (the
    /// curve's second moment, which multiplies the first three and not the
    /// last three), `total` (the variance itself, in fraction squared per
    /// session) and `implied` (that variance as a VIX, through
    /// `(1 + premium) * 100 * sqrt(252 * total)`).
    ///
    /// THE NUMBER THE UPDATE READ, not a recomputation. The identity's
    /// instantaneous terms — the sector draw's sigma and the jump arrival
    /// rate — are read at the VIX the close saw, `VIX_{t-1}`, and the
    /// update then moves the VIX. A getter that evaluated the identity
    /// afresh would read those two terms at `VIX_t` and disagree with the
    /// update by a day's VIX move, which is exactly the size of the
    /// quantity a loop measurement is trying to see.
    ///
    /// `None` rather than zeroes when the identity is off: there the
    /// read-back is not computed at all, and a dictionary of zeroes would
    /// read as a market with no variance rather than as a run with no
    /// read-back.
    ///
    /// This is a diagnostic and it is not carried in `state_snapshot`, so
    /// it moves no state hash. A fork carries it — a fork is a copy, and
    /// its last VIX update really was the parent's — but `restore_state`
    /// does not: a restored engine keeps its own last reading, `None` if
    /// it had advanced no day, until its own next day advances.
    fn index_variance_terms<'py>(
        &self,
        py: Python<'py>,
    ) -> PyResult<Option<Bound<'py, PyDict>>> {
        let terms = match self.inner.last_index_variance() {
            Some(t) => t,
            None => return Ok(None),
        };
        let out = PyDict::new_bound(py);
        out.set_item("factor", terms.factor_raw)?;
        out.set_item("sector", terms.sector_raw)?;
        out.set_item("idio", terms.idio_raw)?;
        // The two regime terms (charter B4). Both are pre-`K` like the three
        // above and both are exactly 0.0 outside their regime, so a reader
        // can tell a crisis session from a calm one by the key alone.
        out.set_item("crash", terms.crash_raw)?;
        out.set_item("crisis", terms.crisis_raw)?;
        // The transmission tilt, pre-`K` like the rest of the noise block
        // and exactly 0.0 on a session with neither wire live. A reader can
        // tell a lagged session from an unlagged one by this key alone,
        // which is the one bit of the read-back's state that is not in the
        // VIX it was read at.
        out.set_item("tilt", terms.tilt_raw)?;
        out.set_item("market_jump", terms.market_jump)?;
        out.set_item("idio_jump", terms.idio_jump)?;
        out.set_item("news", terms.news)?;
        out.set_item("k", terms.k)?;
        let total = terms.total();
        out.set_item("total", total)?;
        out.set_item(
            "implied",
            crate::market::index_var::vix_from_variance(
                self.inner.params().vix_variance_premium,
                total,
            ),
        )?;
        Ok(Some(out))
    }

    /// The variance targets the last close reverted toward, as
    /// `(fast, slow)`, or `None` before any close.
    ///
    /// `slow` is `None` when the preset has no slow component
    /// (`market_vol_slow_weight == 0.0` — pt-v1 through pt-v3 and the
    /// default `PT_V1`), because `factor_vol.rs::close_day_at` returns
    /// before a slow target is computed on that branch. On such a preset
    /// the first element is THE target, not a "fast" one.
    ///
    /// The two `None`s mean different things: the outer is "no close
    /// yet", the inner is "no slow component". A fork carries the
    /// reading, a restore does not.
    fn market_variance_target(&self) -> Option<(f64, Option<f64>)> {
        self.inner.market_variance_target()
    }

    /// The model this engine runs, as a `ModelParams`.
    #[getter]
    fn model(&self) -> crate::python_params::PyModelParams {
        crate::python_params::PyModelParams {
            inner: self.inner.params().clone(),
        }
    }

    /// The full coefficient dictionary of the model this engine runs,
    /// `ModelParams.to_dict()` of `model`, with `"name"` set to the
    /// fingerprint. What a manifest embeds.
    #[getter]
    fn model_params(&self, py: Python<'_>) -> PyResult<PyObject> {
        crate::python_params::PyModelParams {
            inner: self.inner.params().clone(),
        }
        .to_dict(py)
    }

    /// Cumulative draws per stream: `{"market": n, "economy": n, "external": n}`.
    ///
    /// The market stream's schedule is a pure function of (market status,
    /// active roster, sector count), so equal `market` counts between two
    /// runs of the same tick schedule mean the two markets consumed, and
    /// therefore saw, an identical noise sequence. The economy stream's
    /// count genuinely varies with macro state (a chain in contraction
    /// draws a shock the expansion never rolls), which is why it is
    /// reported separately instead of polluting the market comparison.
    fn draws_by_stream<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let draws = self.inner.draws_by_stream();
        let out = PyDict::new_bound(py);
        out.set_item("market", draws.market)?;
        out.set_item("economy", draws.economy)?;
        out.set_item("external", draws.external)?;
        Ok(out)
    }

    // ── Draw surgery (phase 2) ────────────────────────────────────────────

    /// The draws a surgery generator delivers, in the order of `kinds`.
    ///
    /// A fresh generator per the surgery derivation contract in `rng.rs`
    /// (`GameRng::surgery`), read once. `kinds` is "uniform" or "normal"
    /// per draw, in the order of the addresses the caller is about to
    /// replace, and the result is the value for each. Nothing on any
    /// engine is read or moved; the root seed is an argument because the
    /// derivation is a function of it, the stream and the surgery seed,
    /// and of nothing else.
    #[staticmethod]
    fn surgery_draws(
        seed: u32,
        stream: &str,
        surgery_seed: u32,
        kinds: Vec<String>,
    ) -> PyResult<Vec<f64>> {
        let id = stream_id(stream)?;
        let mut rng = crate::rng::GameRng::surgery(seed, id, surgery_seed);
        let mut out = Vec::with_capacity(kinds.len());
        for kind in &kinds {
            out.push(match draw_kind(kind)? {
                crate::rng::DrawKind::Uniform => rng.next_f64(),
                crate::rng::DrawKind::Normal => rng.next_normal(),
            });
        }
        Ok(out)
    }

    // ── Draw addressing (phase 1) ─────────────────────────────────────────

    /// The market jump's effective daily intensity at this engine's dials
    /// and its current VIX: the threshold `apply_jumps` compares its
    /// market uniform against. Takes no draw and changes nothing.
    ///
    /// A surgery that stops the jump installs 1.0, so it can only stop one
    /// where this is at most 1.0; above that every uniform the stream can
    /// draw is already under the threshold.
    fn market_jump_intensity(&self) -> f64 {
        self.inner.market_jump_intensity()
    }

    /// The day the draws taken from now on carry in the draw log and the
    /// day marks. `open_market` stamps the engine's own day counter and
    /// `run_days` stamps `first_day`, each at the open it labels, so this is
    /// for a caller that drives the core between an open and a close and
    /// wants the draws numbered its own way, or an embedder taking draws
    /// through `draw_uniform` on a closed market.
    ///
    /// Stamped between an open and the close that follows it, this moves the
    /// number the rest of that day's draws carry and leaves the day mark on
    /// the number the open stamped.
    ///
    /// It moves the VALUATION too, under a preset that sets
    /// `buyback_payout_share`, because the buyback factor reads this number
    /// as its elapsed time. So a stamp taken to label draws reprices every
    /// name from the next tick. Nothing in this repository calls it.
    fn set_day(&mut self, day: i64) {
        self.inner.set_current_day(day);
    }

    /// `(uniforms, normals)` taken so far on each stream, keyed by stream
    /// name: the address the next draw of each kind would take.
    fn stream_positions<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let out = PyDict::new_bound(py);
        for (id, (u, n)) in self.inner.stream_positions().iter().enumerate() {
            out.set_item(stream_name(id as u32), (*u, *n))?;
        }
        Ok(out)
    }

    /// Install substitutions: `(stream, kind, index, value)` tuples, the
    /// stream and kind by name. The generators still advance at every
    /// address; only the value the consumer receives changes.
    fn patch_draws(&mut self, patches: Vec<(String, String, u64, f64)>) -> PyResult<()> {
        for (stream, kind, index, value) in patches {
            let id = stream_id(&stream)?;
            let kind = draw_kind(&kind)?;
            self.inner.patch_draw(id, kind, index, value);
        }
        Ok(())
    }

    /// The installed overlay, as `(stream, kind, index, value)` tuples.
    fn draw_patches(&self) -> Vec<(String, String, u64, f64)> {
        let mut out = Vec::new();
        for id in 0..crate::rng::stream::COUNT as u32 {
            if let Some(o) = self.inner.draw_overlay(id) {
                for ((kind, index), value) in &o.table {
                    out.push((stream_name(id).to_string(), kind.name().to_string(), *index, *value));
                }
            }
        }
        out
    }

    /// Record every draw `stream` takes on days `from_day..=to_day`.
    fn trace_draws(&mut self, stream: String, from_day: i64, to_day: i64) -> PyResult<()> {
        self.inner.enable_draw_log(stream_id(&stream)?, from_day, to_day);
        Ok(())
    }

    /// The recorded draws of `stream` on days `from_day..=to_day`, as
    /// `((stream, kind, index), value, day, site, tag)` tuples in the order
    /// they were taken.
    fn draw_log(&self, stream: String, from_day: i64, to_day: i64)
        -> PyResult<Vec<((String, String, u64), f64, i64, String, u32)>> {
        let id = stream_id(&stream)?;
        Ok(self
            .inner
            .draw_log(id)
            .iter()
            .filter(|r| from_day <= r.day && r.day <= to_day)
            .map(|r| ((stream.clone(), r.kind.name().to_string(), r.index), r.value, r.day, r.site.name().to_string(), r.tag))
            .collect())
    }

    /// One dict per opened day: `day`, `positions` (stream name to
    /// `(uniforms, normals)` at the open), `active`, `sectors`, `ticks`.
    fn day_marks<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let mut out = Vec::new();
        for m in self.inner.day_marks() {
            let d = PyDict::new_bound(py);
            d.set_item("day", m.day)?;
            let pos = PyDict::new_bound(py);
            for (id, (u, n)) in m.positions.iter().enumerate() {
                pos.set_item(stream_name(id as u32), (*u, *n))?;
            }
            d.set_item("positions", pos)?;
            d.set_item("active", m.active.clone())?;
            d.set_item("sectors", m.sectors)?;
            d.set_item("ticks", m.ticks)?;
            out.push(d);
        }
        Ok(out)
    }

    /// The current news day's endogenous events, one dict each:
    /// `ticker` (the trading ticker, `None` if the event names no company
    /// on the roster), `sector`, `price_impact` and `day`.
    ///
    /// The engine draws the day's events at `open_market` and keeps them
    /// through `close_market`, so `day` is `day_count` while the market is
    /// open and `day_count - 1` after the close, the frame a headline
    /// writer uses. Empty before the first open and on any
    /// preset with `endogenous_news_intensity` at zero.
    ///
    /// A read. It draws nothing, writes nothing and is not logged, so
    /// calling it cannot change a run: `state_hash` is the same with and
    /// without it. `price_impact` is the whole move the event adds to the
    /// price by the close, which makes it the answer key; never hand it to
    /// an agent (a headline writer cuts it to its sign).
    fn session_news<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let day: Option<i64> = if self.market_open {
            Some(i64::from(self.day_count))
        } else if self.day_count > 0 {
            Some(i64::from(self.day_count) - 1)
        } else {
            None
        };
        let mut out = Vec::new();
        for event in self.inner.session_news() {
            let d = PyDict::new_bound(py);
            d.set_item(
                "ticker",
                event.company_id.as_deref().and_then(|id| self.ticker_for_id(id)),
            )?;
            d.set_item("sector", event.sector.clone())?;
            d.set_item("price_impact", event.price_impact)?;
            d.set_item("day", day)?;
            out.push(d);
        }
        Ok(out)
    }

    /// Ticks run since the current day's `open_market`: 0 at the open, 390
    /// after a full session, and still 390 after the close until the next
    /// open. Every tick call counts, as it does in `day_marks()[-1]
    /// ["ticks"]`, which is the counter this reads.
    ///
    /// `None` when this engine has not opened a day since it was built or
    /// restored. The count is recording state, like the day's tape, so a
    /// snapshot does not carry it and a restored engine learns it again at
    /// its next open.
    ///
    /// A read of a counter the engine already keeps: no draw, no write, so
    /// it cannot change a run.
    #[getter]
    fn session_tick(&self) -> Option<u32> {
        self.inner.day_marks().last().map(|m| m.ticks)
    }

    /// Where each active company's market-stream normals sit on `day`:
    /// `(company, first, stride, ticks)`, the normal at tick `t` being
    /// `first + t * stride`. `None` if the day was never opened.
    fn market_day_layout(&self, day: i64) -> Option<Vec<(u32, u64, u64, u32)>> {
        self.inner
            .market_day_layout(day)
            .map(|v| v.iter().map(|l| (l.company, l.first, l.stride, l.ticks)).collect())
    }

    /// Keep what [`Self::explain`] needs for days `from_day` to `to_day`.
    ///
    /// Two records, both read-only. Every stream's draw log is turned on over
    /// the window, starting one day early because the jump a day's `truth`
    /// table carries was drawn at the close before it. And the engine is
    /// copied at each of those days' opens, so a day can be run again from
    /// the state it ran from the first time, under the inputs the run log
    /// holds for it.
    ///
    /// Neither moves a trajectory. The log records what a consumer received
    /// and the copy is never run by the engine that holds it, so a market
    /// with a window open is the market it would have been without one and
    /// `tests/known_answer.py` reports the shipped digest either way.
    ///
    /// Both `run_days` and `World.run` open through one path, so both fill
    /// this. The cost is one engine copy per kept day, taken without the
    /// recorded tape and without the draw log, so ask for the days you
    /// mean to explain rather than for a whole run.
    ///
    /// What that costs, on `Universe.random(40, seed=111)` at `pt-v16`
    /// over thirty days with `record=True`, as the PROCESS peak working
    /// set rather than a Python-side allocation figure, since the store
    /// is Rust memory: 436 to 457 MB with a thirty-day window against 89
    /// to 92 MB with none, two readers on one machine. Recorded, because
    /// `Engine.explain` reads the day off `truth()` and a window is only
    /// useful on a run that recorded. A fork carries what the parent
    /// kept, so `World.fork` and every counterfactual arm pay it again
    /// per arm: `fork(1)` takes 0.011 s at a five-day window, 0.046 s at
    /// twenty and 0.136 s at sixty. A fork collects no new opens of its
    /// own past the window it inherited.
    ///
    /// The draw log is cleared on the copy because the copy is never
    /// asked what it recorded: the tree reads the SOURCE engine's log,
    /// and the copy is only forked and replayed. Carried, it made the
    /// store quadratic in the window, since copy k held k days of a log
    /// that is the size of the tape; the same thirty-day window cost
    /// 1.6 GB and a single fork at sixty days took 24.8 seconds. Found
    /// by the P1 reviewer on 2026-09-02.
    fn keep_explanations(&mut self, from_day: i64, to_day: i64) -> PyResult<()> {
        if to_day < from_day {
            return Err(ValidationError::new_err(format!(
                "keep_explanations takes a window, and day {to_day} is \
                 before day {from_day}"
            )));
        }
        self.explanations.window = Some((from_day, to_day));
        for id in 0..crate::rng::stream::COUNT as u32 {
            self.inner.enable_draw_log(id, from_day - 1, to_day);
        }
        Ok(())
    }

    /// One name's day, from its move down to the draws that seeded it.
    ///
    /// Returns a `tradefloor.explain.Explanation`. The tree, the replays and
    /// the caveats are built in `python/tradefloor/explain.py`; this hands
    /// that module the engine, the window and the day's own inputs, which is
    /// the bookkeeping a binding owns.
    ///
    /// Raises when `day` was not kept, naming the days that were. The store
    /// is opt-in, so a caller who has not asked for it is told what to ask
    /// for rather than handed a tree with no leaves.
    fn explain(slf: &Bound<'_, Self>, ticker: &str, day: i64) -> PyResult<PyObject> {
        let py = slf.py();
        if slf.borrow().inner.rates().index_of(ticker).is_some() {
            return Err(ValidationError::new_err(format!(
                "{ticker} is a rate index, and explain walks the equity factor \
                 model, which never runs on one. Its move is the repricing formula: \
                 read rate_attribution(\"carry\" | \"duration\" | \"convexity\" | \
                 \"flow\")."
            )));
        }
        let (sector, window, kept, opened, inputs, before, ops) = {
            let me = slf.borrow();
            let window = me.explanations.window;
            let kept: Vec<i64> = me.explanations.opens.keys().copied().collect();
            // The roster the day BEFORE opened on, where that day was
            // kept too. Comparing the two rosters is exact where the
            // widths are not: a listing and a delisting in one day leave
            // the width alone and move every slot between them.
            let before: Option<Vec<String>> = me
                .explanations
                .opens
                .get(&(day - 1))
                .map(|k| k.engine.tickers.clone());
            match me.explanations.opens.get(&day) {
                Some(k) => {
                    let entries = me.day_inputs(py, k.log_start)?;
                    let ops = me.roster_ops_before(k.log_start);
                    // Against the COPY's roster, not this engine's. A
                    // delisting shifts every slot below it, so a name
                    // resolved against the roster as it is now addresses
                    // a different company in a day kept before the
                    // change, and the sector tag with it.
                    let sector = sector_of(&k.engine, ticker);
                    (
                        sector,
                        window,
                        kept,
                        Some((*k.engine).clone()),
                        Some(entries),
                        before,
                        ops,
                    )
                }
                None => (
                    sector_of(&me, ticker),
                    window,
                    kept,
                    None,
                    None,
                    before,
                    Vec::new(),
                ),
            }
        };
        let module = py.import_bound("tradefloor.explain")?;
        Ok(module
            .call_method1(
                "_explain",
                (
                    slf, ticker, day, sector, window, kept, opened, inputs,
                    before, ops,
                ),
            )?
            .unbind())
    }

    #[getter]
    fn len(&self) -> usize {
        self.inner.instrument_count()
    }

    fn __len__(&self) -> usize {
        self.inner.instrument_count()
    }

    /// Take one uniform from the engine's EXTERNAL stream.
    ///
    /// For a caller's own subsystems. The draws are derived from the same
    /// root seed, so they are reproducible run to run, but live on their own
    /// substream, so taking one (or a thousand) leaves the market's noise
    /// sequence bit-identical. A caller that varies how much it draws no
    /// longer invalidates every seeded trajectory it computed before.
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
            qe_assets_ratio: e.qe_assets_ratio,
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
    ///
    /// # Four fields the market cannot see directly
    ///
    /// `gdp_growth`, `unemployment_rate`, `tariff_rate` and `oil_price` sit
    /// in the same economy struct as the rest, but nothing in the market
    /// reads them: the tick reads `federal_funds_rate`, `corporate_bond_yield`,
    /// `qe_pe_boost`, `vix` and the cycle phase, and NOTHING else. These four
    /// reach a price only through the macro chain -- the monthly inflation
    /// update, then the central bank's next MEETING, then the curve. That is
    /// slower than a short study, and it is the same horizon trap the
    /// `tradefloor.scenario` module documents for the policy rate.
    ///
    /// They are here because they are what a supply shock or a tariff
    /// actually IS in this model, and because the alternative -- moving
    /// inflation by hand and calling it an oil shock -- states the
    /// transmission as a fact rather than as an assumption.
    ///
    /// `gdp_growth`, `unemployment_rate` and `tariff_rate` are FRACTIONAL
    /// like every other rate here (0.025 is 2.5%). `oil_price` is a price in
    /// dollars, and the daily chain clamps it into [35, 150] on its next
    /// step, so a pin outside that band survives only the day it is written.
    ///
    /// # `vix_sets_variance`: a forced VIX that sets the market's volatility
    ///
    /// Off (the default), a pinned VIX reaches volatility the way the
    /// model's own VIX does: each close moves the market factor's variance
    /// one step toward the level the VIX implies, so a VIX that jumps is
    /// felt over weeks. `vix_sets_variance=True` marks tonight's close to SET
    /// the factor's variance (both components) to that level instead, so the
    /// next session trades at it. The close consumes the mark; a session that
    /// is not marked closes free, from the level the mark left, which is the
    /// free law's own fixed point at that VIX. `tradefloor.Scenario` sets it
    /// on every session it forces the VIX when the scenario asks for it
    /// (`Scenario(vix_sets_variance=True)`), which is the intended way in.
    ///
    /// # The treasury curve
    ///
    /// `treasury_yield_2y` and `treasury_yield_10y` are FRACTIONAL and write
    /// the curve the rate instruments read (`UST2Y`, `UST10Y`, and `IGCORP`
    /// through the 10-year). The chain keeps running from a pinned value: the
    /// 10-year closes 5% of its gap to the policy rate plus a term premium
    /// every session, and the 2-year is recomputed at every close as
    /// `0.85 * policy + 0.15 * 10-year`, so a 2-year pinned alone lasts until
    /// that close. A parallel curve shift therefore pins the policy rate and
    /// the 10-year with it, which is what `scenarios/curve_shock.yml` does.
    /// Equities read neither directly: they are discounted off
    /// `corporate_bond_yield`, which the next central-bank meeting recomputes
    /// from the 10-year.
    #[pyo3(signature = (
        *, vix = None, federal_funds_rate = None, corporate_bond_yield = None,
        inflation_rate = None, qe_pe_boost = None, qe_assets_ratio = None, fear_greed_index = None,
        gdp_growth = None, unemployment_rate = None, tariff_rate = None,
        oil_price = None, cycle = None, epicentre = None, vix_sets_variance = false,
        treasury_yield_2y = None, treasury_yield_10y = None
    ))]
    #[allow(clippy::too_many_arguments)]
    fn pin_macro(
        &mut self,
        vix: Option<f64>,
        federal_funds_rate: Option<f64>,
        corporate_bond_yield: Option<f64>,
        inflation_rate: Option<f64>,
        qe_pe_boost: Option<f64>,
        qe_assets_ratio: Option<f64>,
        fear_greed_index: Option<f64>,
        gdp_growth: Option<f64>,
        unemployment_rate: Option<f64>,
        tariff_rate: Option<f64>,
        oil_price: Option<f64>,
        cycle: Option<String>,
        epicentre: Option<String>,
        vix_sets_variance: bool,
        treasury_yield_2y: Option<f64>,
        treasury_yield_10y: Option<f64>,
    ) -> PyResult<()> {
        // Validate EVERYTHING before writing ANYTHING. A pin that applied the
        // first three fields and then rejected the fourth would leave the
        // scenario half-applied, and the run would continue on a macro state
        // nobody asked for.
        for (name, v) in [
            ("federal_funds_rate", federal_funds_rate),
            ("corporate_bond_yield", corporate_bond_yield),
            ("inflation_rate", inflation_rate),
            ("gdp_growth", gdp_growth),
            ("unemployment_rate", unemployment_rate),
            ("tariff_rate", tariff_rate),
            ("treasury_yield_2y", treasury_yield_2y),
            ("treasury_yield_10y", treasury_yield_10y),
        ] {
            if let Some(v) = v {
                crate::units::check_rate(name, v).map_err(ValidationError::new_err)?;
            }
        }
        for (name, v) in [
            ("vix", vix),
            ("qe_pe_boost", qe_pe_boost),
            ("qe_assets_ratio", qe_assets_ratio),
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
        // A price, not a rate, so the fractional band does not apply -- but a
        // non-positive one is not a cheaper barrel, it is a barrel the
        // seasonality multiply and the inventory ratio both divide by, and
        // the chain carries the result forward for the rest of the run.
        if let Some(v) = oil_price {
            if !v.is_finite() || v <= 0.0 {
                return Err(ValidationError::new_err(format!(
                    "oil_price must be finite and positive, got {v}. It is a price \
                     in dollars (the engine opens at 75.0), not a fraction or a \
                     multiplier."
                )));
            }
        }
        // THE CRISIS EPICENTRE, a pin rather than a macro field: it names
        // which sector carries the next crisis episode instead of setting a
        // level the chain then evolves. `"none"` is a value in its own
        // right -- a crisis with no epicentre, which is two of the tape's
        // five episodes -- and is why this is a string and not a sector key
        // or nothing. Validated here, where it is written, against the
        // engine's own table, so a misspelt sector is refused rather than
        // silently pinning nothing.
        //
        // It PERSISTS once written, like the macro pins beside it: a
        // scenario's `hold` writes it every day it covers, and an episode
        // that starts while it is set takes no draw.
        let epicentre_pin = match epicentre.as_deref() {
            None => None,
            Some("none") => Some(-1_i32),
            Some(key) => match crate::sectors::SECTORS.iter().position(|s| s.key == key) {
                Some(i) => Some(i as i32),
                None => {
                    return Err(ValidationError::new_err(format!(
                        "unknown epicentre {key:?}. Valid: none, {}",
                        crate::sectors::keys().join(", ")
                    )))
                }
            },
        };
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
            ("qe_assets_ratio", qe_assets_ratio),
            ("fear_greed_index", fear_greed_index),
            ("gdp_growth", gdp_growth),
            ("unemployment_rate", unemployment_rate),
            ("tariff_rate", tariff_rate),
            ("oil_price", oil_price),
            ("treasury_yield_2y", treasury_yield_2y),
            ("treasury_yield_10y", treasury_yield_10y),
        ] {
            if let Some(v) = value {
                logged.push((name.to_string(), v));
            }
        }
        self.log.push(crate::python_log::LogEntry::PinMacro {
            fields: logged,
            cycle: cycle.clone(),
            epicentre: epicentre.clone(),
            vix_sets_variance,
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
        if let Some(v) = qe_assets_ratio {
            e.qe_assets_ratio = v;
        }
        if let Some(v) = fear_greed_index {
            e.fear_greed_index = v;
        }
        if let Some(v) = gdp_growth {
            e.gdp_growth = crate::units::fraction_to_percent(v);
        }
        if let Some(v) = unemployment_rate {
            e.unemployment_rate = crate::units::fraction_to_percent(v);
        }
        if let Some(v) = tariff_rate {
            e.tariff_rate = crate::units::fraction_to_percent(v);
        }
        if let Some(v) = oil_price {
            e.oil_price = v;
        }
        if let Some(v) = treasury_yield_2y {
            e.treasury_yield_2y = crate::units::fraction_to_percent(v);
        }
        if let Some(v) = treasury_yield_10y {
            e.treasury_yield_10y = crate::units::fraction_to_percent(v);
        }
        if let Some(p) = phase {
            e.cycle_phase = p;
        }
        // A pinned corporate yield is the corporate index's yield, including
        // when the pin repeats yesterday's value. Nothing without rate
        // instruments.
        if corporate_bond_yield.is_some() {
            self.inner.remark_credit_spread();
        }
        if let Some(pin) = epicentre_pin {
            self.inner.set_crisis_epicentre_pin(Some(pin));
        }
        // A FORCED VIX THAT SETS THE MARKET'S VOLATILITY. Marks tonight's
        // close to set the market factor's variance to the level the
        // variance law implies at the VIX then standing, instead of stepping
        // one session toward it; the close consumes the mark. Only ever
        // turned ON here: `False`, the default, leaves a mark an earlier
        // call made today in place, so a later pin of another field cannot
        // cancel it. See `MarketVarianceState::close_day_forced`.
        if vix_sets_variance {
            self.inner.set_vix_sets_variance_pending(true);
        }
        Ok(())
    }

    /// Whether tonight's close will SET the market factor's variance from
    /// the VIX, because a scenario forced the VIX today with
    /// `vix_sets_variance` on. Cleared by the close.
    #[getter]
    fn vix_sets_variance_pending(&self) -> bool {
        self.inner.vix_sets_variance_pending()
    }

    /// The crisis episode: `(in_episode, sessions_under, epicentre)`.
    ///
    /// `epicentre` is the sector key, or `"none"` for a crisis with no
    /// epicentre, or `None` when no episode is running -- so a caller can
    /// tell "no crisis" from "a crisis nobody is at the centre of", which
    /// the tick deliberately cannot. Always `(False, 0, None)` while
    /// `crisis_epicentre_extra` is 0.0, which is every preset before pt-v19.
    /// pt-v19 ships `crisis_epicentre_extra` at 1.93, so on the default an
    /// episode starts at the first session above the crisis threshold. This
    /// read "every shipped preset" until 2026-09-24.
    #[getter]
    fn crisis_episode(&self) -> (bool, i64, Option<&'static str>) {
        self.inner.crisis_episode()
    }

    /// Every field [`PyEngine::pin_macro`] can write, as it can write it.
    ///
    /// The read side of the narrow write surface, and the reason it exists
    /// separately from [`PyEngine::macro_state`] is UNITS. `macro_state`
    /// returns the seven fields the `Macro` constructor takes;
    /// `state_snapshot()["economy"]` returns the whole economy in the CORE'S
    /// percent denomination. Neither is the set `pin_macro` accepts, and an
    /// intervention that multiplies a value it read by 1.4 and writes it back
    /// has to read and write in the same units or it is a factor of a hundred
    /// out, silently, on a plausible-looking trajectory. See `units.rs`.
    ///
    /// So this returns exactly the pinnable fields, in exactly the
    /// denomination `pin_macro` takes: fractional rates, VIX in points,
    /// `oil_price` in dollars, `cycle` as its name. Read one, change it,
    /// write it back.
    #[getter]
    fn macro_fields(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let e = self.inner.economy();
        let out = PyDict::new_bound(py);
        out.set_item("vix", e.vix)?;
        out.set_item(
            "federal_funds_rate",
            crate::units::percent_to_fraction(e.federal_funds_rate),
        )?;
        out.set_item(
            "corporate_bond_yield",
            crate::units::percent_to_fraction(e.corporate_bond_yield),
        )?;
        out.set_item(
            "inflation_rate",
            crate::units::percent_to_fraction(e.inflation_rate),
        )?;
        out.set_item("qe_pe_boost", e.qe_pe_boost)?;
        out.set_item("fear_greed_index", e.fear_greed_index)?;
        out.set_item(
            "gdp_growth",
            crate::units::percent_to_fraction(e.gdp_growth),
        )?;
        out.set_item(
            "unemployment_rate",
            crate::units::percent_to_fraction(e.unemployment_rate),
        )?;
        out.set_item(
            "tariff_rate",
            crate::units::percent_to_fraction(e.tariff_rate),
        )?;
        out.set_item("oil_price", e.oil_price)?;
        out.set_item("cycle", cycle_name(e.cycle_phase))?;
        out.set_item(
            "treasury_yield_2y",
            crate::units::percent_to_fraction(e.treasury_yield_2y),
        )?;
        out.set_item(
            "treasury_yield_10y",
            crate::units::percent_to_fraction(e.treasury_yield_10y),
        )?;
        Ok(out.into())
    }

    /// Write the `avg_volume` column: one value per instrument, in shares.
    ///
    /// # Why this is the liquidity lever
    ///
    /// `avg_volume` is what the market maker quotes off. `base_quote_size`
    /// reads it, every ladder level is a fraction of that size, and the
    /// printed volume of a tick is bounded by `avg_volume / 390`. Halve it
    /// and the book is half as deep at every level, a marketable order walks
    /// further up it, and the impact an agent pays for the same trade rises.
    /// That is a liquidity shock as this simulator can actually express one:
    /// not a number called "liquidity" multiplied by 0.4, but less depth to
    /// trade against.
    ///
    /// # Why the engine never writes it itself
    ///
    /// The shipped close policy is [`AvgVolumePolicy::Hold`], so `avg_volume`
    /// stays whatever the universe calibrated it to be for the whole run --
    /// see `market::daily`, which names writing `PriceField::AvgVolume` as
    /// the embedder's route. This is that route, and it is recorded in the
    /// order log like any other input, so a replay, a checkpoint and a fork
    /// all carry it.
    ///
    /// Values must be finite and strictly positive. Zero is not "no
    /// liquidity": `base_quote_size` treats a zero as ABSENT and falls
    /// through to realised volume, then to half a percent of shares
    /// outstanding, so a zeroed column quietly quotes a book off a different
    /// input rather than a thin one.
    ///
    /// One value per instrument in `tickers` order, rate instruments included,
    /// so a column read with `column("avg_volume")` can be scaled and written
    /// back whole. A rate index quotes its depth off this column exactly as an
    /// equity does.
    fn set_avg_volume(&mut self, values: Vec<f64>) -> PyResult<()> {
        let n = self.inner.instrument_count();
        if values.len() != n {
            return Err(ValidationError::new_err(format!(
                "set_avg_volume needs one value per instrument: this engine \
                 holds {n}, got {}. The column is positional, so a short or long \
                 list would attach volumes to the wrong names.",
                values.len()
            )));
        }
        for (i, v) in values.iter().enumerate() {
            if !v.is_finite() || *v <= 0.0 {
                return Err(ValidationError::new_err(format!(
                    "avg_volume[{i}] = {v} is not a positive, finite share count. \
                     Zero is not an empty book here -- the maker reads a zero as \
                     ABSENT and quotes off realised volume instead, so it thins \
                     nothing. Scale the column down to thin it."
                )));
            }
        }
        self.log
            .push(crate::python_log::LogEntry::SetAvgVolume { values: values.clone() });
        let equities = self.inner.len();
        for (inst, v) in self
            .inner
            .rates_mut()
            .instruments
            .iter_mut()
            .zip(&values[equities..])
        {
            inst.avg_volume = *v;
        }
        self.inner
            .set_column(PriceField::AvgVolume, &values[..equities])
            .map_err(ValidationError::new_err)
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
    /// # Nine components, and the same nine the `truth` table carries
    ///
    /// Four shocks -- `company_news`, `order_flow_impact`,
    /// `short_squeeze_effect`, `random_noise` -- and the three pieces of the
    /// model's own dynamics: `reversion`, `momentum`, `crowd_lean`. Together
    /// they account for the day's change in `s`.
    ///
    /// This is the DAY grain of exactly what `truth` reports per tick, so
    /// summing a `truth` column over a day reproduces the value here. Two
    /// surfaces that disagreed about what drove a price would be worse than
    /// either alone.
    ///
    /// These are the APPLIED contributions -- what each driver did to `s` --
    /// not the raw factors before scaling. Raw was the earlier behaviour and
    /// it was wrong: the drift factors are divided by 390 on their way into
    /// `s` while noise is multiplied by the intraday volatility curve, so raw
    /// sums overstate news, flow and squeeze by around 390x against noise.
    /// Ranking raw magnitudes named `company_news` the dominant driver on
    /// sessions that were almost entirely noise.
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
        // Zero in every rate instrument's slot: none of these drivers moves
        // an index. Its move is in `rate_attribution`.
        Ok(f64_bytes(py, &self.padded(self.inner.attribution_column(index), 0.0)))
    }

    /// The rate components of today's move, one value per instrument in
    /// `tickers` order, as f64 bytes. Zero for every equity.
    ///
    /// `"carry"`, `"duration"` and `"convexity"` are the terms of the
    /// repricing formula summed over the day (fractions of the level: see
    /// `RATE_COMPONENTS`), so `1 + carry + duration + convexity` is the
    /// day's index return whenever the curve moved once, which is how the
    /// engine moves it. `"flow"` is the tape's premium over the index level
    /// right now, `price / level - 1`: the part of a print that is the book's
    /// response to trading rather than the curve.
    ///
    /// Reset at each open, before the open reprices, so the night's move is
    /// in the day it lands on.
    fn rate_attribution(&self, py: Python<'_>, component: &str) -> PyResult<Py<PyBytes>> {
        let rates = &self.inner.rates().instruments;
        let values: Vec<f64> = match component {
            "carry" => rates.iter().map(|i| i.day_carry).collect(),
            "duration" => rates.iter().map(|i| i.day_duration).collect(),
            "convexity" => rates.iter().map(|i| i.day_convexity).collect(),
            "flow" => rates.iter().map(|i| i.price / i.level - 1.0).collect(),
            other => {
                return Err(ValidationError::new_err(format!(
                    "unknown rate component {other:?}. Valid: {}",
                    RATE_COMPONENTS.join(", ")
                )))
            }
        };
        let mut out = vec![0.0; self.inner.len()];
        out.extend(values);
        Ok(f64_bytes(py, &out))
    }

    /// The rate components `rate_attribution` reports, in order.
    #[classattr]
    #[allow(non_snake_case)]
    fn RATE_COMPONENTS() -> Vec<String> {
        RATE_COMPONENTS.iter().map(|s| s.to_string()).collect()
    }

    /// The rate instruments this engine holds, one dict each, in `tickers`
    /// order: the spec (name, curve point, duration, convexity, spread) and
    /// the state (level, the yield it is marked at, price). Empty without
    /// them.
    #[getter]
    fn rate_instruments<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        let mut out = Vec::new();
        for inst in &self.inner.rates().instruments {
            let d = PyDict::new_bound(py);
            d.set_item("ticker", inst.spec.ticker)?;
            d.set_item("name", inst.spec.name)?;
            d.set_item("curve_point", inst.spec.point.as_str())?;
            d.set_item("duration", inst.spec.duration)?;
            d.set_item("convexity", inst.spec.convexity)?;
            d.set_item("spread_bps", inst.spec.spread_bps)?;
            d.set_item("level", inst.level)?;
            d.set_item("yield", inst.marked_yield)?;
            d.set_item("price", inst.price)?;
            d.set_item("avg_volume", inst.avg_volume)?;
            out.push(d);
        }
        Ok(out)
    }

    /// The curve as the engine holds it now, fractional: the policy rate, the
    /// 2-year and 10-year treasury yields, the corporate yield equities are
    /// discounted off, and the yield the corporate index reads (the 10-year
    /// plus the credit spread last marked, which equals the corporate yield
    /// whenever the engine has just set it). The last is present only on an
    /// engine holding rate instruments.
    #[getter]
    fn curve<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let e = self.inner.economy();
        let d = PyDict::new_bound(py);
        d.set_item("policy_rate", crate::units::percent_to_fraction(e.federal_funds_rate))?;
        d.set_item("treasury_2y", crate::units::percent_to_fraction(e.treasury_yield_2y))?;
        d.set_item("treasury_10y", crate::units::percent_to_fraction(e.treasury_yield_10y))?;
        d.set_item("corporate", crate::units::percent_to_fraction(e.corporate_bond_yield))?;
        let rates = self.inner.rates();
        if !rates.is_empty() {
            let spread = if e.corporate_bond_yield != rates.last_corporate {
                crate::rates::credit_spread(e)
            } else {
                rates.ig_spread
            };
            d.set_item(
                "investment_grade",
                crate::rates::curve_yield(crate::rates::CurvePoint::InvestmentGrade, e, spread),
            )?;
        }
        Ok(d)
    }

    /// The day's `random_noise` column split into the three draws it sums,
    /// `"market"`, `"sector"` or `"idio"`, as f64 bytes per company.
    ///
    /// A window on the innovation the close feeds the per-name GJR. The
    /// `random_noise` column is that innovation, and it is the sum of the
    /// factor's transmission, the sector's and the name's own draw; only
    /// the split says which of the three the name's variance process is
    /// responding to. Reading it changes nothing: the close reads the same
    /// accumulator directly, and only while
    /// `garch_innovation_commensurate` is non-zero.
    fn noise_split(&self, py: Python<'_>, part: &str) -> PyResult<Py<PyBytes>> {
        let index = match part {
            "market" => 0,
            "sector" => 1,
            "idio" => 2,
            other => {
                return Err(ValidationError::new_err(format!(
                    "unknown noise part {other:?}. Valid: market, sector, idio"
                )))
            }
        };
        Ok(f64_bytes(py, &self.padded(self.inner.noise_part_column(index), 0.0)))
    }

    /// `count` independent engines at exactly this state.
    ///
    /// A deep copy of the whole engine, so the branches share no memory and
    /// driving one cannot perturb another. That is what makes a fork a
    /// controlled experiment rather than two runs that started similarly.
    ///
    /// # Why a copy and not a rebuilt snapshot
    ///
    /// Forking used to mean building a fresh engine and writing a
    /// hand-maintained list of fields into it. The list was incomplete every
    /// time the engine grew: the per-day attribution accumulators and the
    /// market-open flag went missing first, then the market factor's variance
    /// state, then the common log-volume state, then the day counter, then the
    /// day's endogenous news -- and that last one made a mid-day fork price
    /// DIFFERENTLY from the parent it was supposed to be a copy of, on the
    /// shipped default preset, with nothing to indicate it.
    ///
    /// Each of those was a real divergence found after the fact. The list
    /// cannot be trusted, so this does not keep one: `#[derive(Clone)]` copies
    /// whatever the struct holds, and a field added tomorrow is carried
    /// without anyone remembering to carry it.
    ///
    /// Unlike [`PyEngine::state_snapshot`] this also carries the run's ORDER
    /// LOG, so a fork can itself be checkpointed, forked again, or written to
    /// a `RunManifest`. Reconstructing a fork from a snapshot left its log
    /// empty, and a `Checkpoint` taken on one then replayed a market that
    /// began at day zero -- silently, because a checkpoint has no way to know
    /// the history it was handed is short.
    #[pyo3(signature = (count = 2))]
    fn fork(&self, count: i64) -> PyResult<Vec<PyEngine>> {
        if count < 1 {
            return Err(ValidationError::new_err(format!(
                "count must be at least 1, got {count}"
            )));
        }
        Ok((0..count).map(|_| self.clone()).collect())
    }

    /// This market's state as one 64-character hex digest: the ledger leaf.
    ///
    /// Covers every field [`PyEngine::state_snapshot`] carries, in one fixed
    /// order, on the canonical-f64 rule `manifest._f64` and
    /// `tests/known_answer.py` share. `crate::engine::Engine::state_hash`
    /// documents the encoding and why the generator states are hashed as
    /// `u64` bit patterns rather than as floats.
    ///
    /// Two engines whose hashes agree hold the same market state to the bit,
    /// including the macro chain and the generator positions that
    /// `market_digest` leaves out. Two engines that reached that state by
    /// different routes hash the same: this is a hash of state, and the
    /// order log and the recorded tape are outside it, exactly as they
    /// are outside the snapshot.
    ///
    /// One difference is worth knowing before two runs are compared.
    /// `run_session` with `close_at_end` leaves this binding's session flag
    /// set where `close_market` clears it, so the two spellings of one close
    /// hash apart on a market that is otherwise identical to the bit. The
    /// flag is state rather than bookkeeping: it decides whether the next
    /// session re-opens the day and re-anchors `previous_close`. A recorded
    /// run still verifies against itself either way, because a replay runs
    /// the spelling its own log holds.
    ///
    /// Each per-slot array is hashed at the width the engine holds for it.
    /// That is the invariant, whatever the roster does.
    ///
    /// Every per-slot array follows the roster, `volume_idio` included
    /// since the resize that landed with this one, so the width this walks
    /// after a listing or a delisting is the roster's and the Python twin
    /// accepts the same snapshot.
    ///
    /// `tradefloor.manifest.state_hash(engine.state_snapshot())` computes
    /// the same digest in Python, and a test holds the two equal.
    fn state_hash(&self) -> String {
        let bytes = self.inner.state_hash_with_pending(
            self.day_count, self.market_open,
            &self.pending_jump, &self.pending_overnight);
        let mut hex = String::with_capacity(64);
        for byte in bytes {
            hex.push_str(&format!("{byte:02x}"));
        }
        hex
    }

    /// Every column plus the generator position, as one dict.
    ///
    /// A market's complete state, in constant time. The alternative already
    /// here -- replaying an order log -- costs what the original run cost,
    /// measured at 1.04x on a sixty-day run.
    ///
    /// The columns are generated from `COLUMN_FIELDS`, not listed, so a field
    /// added to the engine appears here without anyone remembering. That is
    /// the same discipline the Rust side uses: `set_column` matches
    /// exhaustively on `PriceField`, so a new variant fails to compile until
    /// it is handled, and a snapshot cannot silently omit it.
    ///
    /// # What it does NOT carry
    ///
    /// Everything here drives the market. Two things that do not are left
    /// out deliberately, and each of them makes a restored engine differ from
    /// the one it copied in a way no price will show:
    ///
    /// - **The order log.** A snapshot reproduces a STATE; the log reproduces
    ///   a HISTORY, and a published result cites the second. An engine
    ///   restored from a snapshot has an EMPTY log, so a `Checkpoint` or
    ///   `RunManifest` taken on it describes a run that began at day zero.
    /// - **The day's recorded tape.** `record` accumulates the day's ticks;
    ///   a restore starts that accumulation empty, so a day half-recorded
    ///   before the snapshot comes back half as long.
    ///
    /// Both are recording and history rather than market state, which is
    /// the line this method draws. [`PyEngine::fork`] carries them, because it
    /// copies the engine rather than rebuilding one, and it is what
    /// `tradefloor.branch` uses.
    fn state_snapshot(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let out = PyDict::new_bound(py);
        let columns = PyDict::new_bound(py);
        for name in COLUMN_FIELDS {
            let field = parse_field(name)?;
            columns.set_item(name, f64_bytes(py, &self.inner.column(field)))?;
        }
        out.set_item("columns", columns)?;
        let rng = self.inner.rng_state();
        // Five streams, three numbers each: (state, increment, spare) for
        // market, economy, external, jumps, volume, in that order. Snapshots
        // written before those last two carry nine or twelve, and restore
        // detects that by LENGTH rather than by a version field. The u64s ride as f64 bit
        // patterns: a u64 does not survive a Python float, and this has to
        // round-trip exactly rather than closely. Nine numbers rather than a
        // nested structure so a pre-split snapshot (three numbers) is
        // unmistakable at a glance and on restore.
        let mut rng_out = Vec::with_capacity(3 * crate::rng::stream::COUNT);
        for s in [rng.market, rng.economy, rng.external, rng.jumps, rng.volume,
                  rng.news, rng.volume_idio, rng.overnight, rng.market_vol_level,
                  rng.crisis_epicentre] {
            rng_out.push(f64::from_bits(s.state));
            rng_out.push(f64::from_bits(s.increment));
            rng_out.push(s.spare.unwrap_or(f64::NAN));
        }
        out.set_item("rng", rng_out)?;
        // Draw addressing (phase 1): the counts that give every draw its
        // address, and the overlay, both additive. A snapshot from before
        // this key restores with counts of zero and no overlay.
        let positions = self.inner.stream_positions();
        let counts: Vec<f64> = positions
            .iter()
            .flat_map(|(u, n)| [*u as f64, *n as f64])
            .collect();
        out.set_item("draw_counts", counts)?;
        let mut overlay: Vec<(u32, u8, u64, f64)> = Vec::new();
        for id in 0..crate::rng::stream::COUNT as u32 {
            if let Some(o) = self.inner.draw_overlay(id) {
                for ((kind, index), value) in &o.table {
                    overlay.push((id, *kind as u8, *index, *value));
                }
            }
        }
        out.set_item("draw_overlay", overlay)?;
        out.set_item("tickers", self.inner.ids().to_vec())?;
        // The model the frozen market was priced under. `restore_state`
        // refuses a mismatch: a snapshot restored onto an engine running
        // other coefficients would continue plausibly and wrongly -- the
        // same failure class as the roster check above, for the model.
        out.set_item("model_fingerprint", self.inner.params().fingerprint())?;
        // The per-DAY accumulators. The columns above are per-company state;
        // these live beside them and were missing, which made a mid-day fork
        // diverge in PRICE -- `attribution` is the day's GARCH innovation at
        // the close, and `market_open` decides whether the next session
        // re-opens the day and re-anchors `previous_close`.
        // Two widths now: the engine's attribution carries the daily jump in
        // an eighth slot, the tick's own decomposition does not (§74).
        let flat10 = |rows: &[[f64; crate::market::factors::COMPONENT_COUNT]]| -> Vec<f64> {
            rows.iter().flat_map(|r| r.iter().copied()).collect()
        };
        let flat = |rows: &[[f64; 8]]| -> Vec<f64> {
            rows.iter().flat_map(|r| r.iter().copied()).collect()
        };
        out.set_item("attribution", f64_bytes(py, &flat10(self.inner.attribution())))?;
        out.set_item(
            "tick_components",
            f64_bytes(py, &flat(self.inner.tick_components())),
        )?;
        out.set_item(
            "tick_fundamental",
            f64_bytes(py, self.inner.tick_fundamental()),
        )?;
        out.set_item("tick_anchor", f64_bytes(py, self.inner.tick_anchor()))?;
        // The day's `random_noise` split and the scale its idiosyncratic
        // part was drawn at, beside the accumulators above because they are
        // the same per-DAY state and lost the same way: off zero on
        // `garch_innovation_commensurate` the close builds the per-name GJR
        // innovation out of them, so a mid-day fork that dropped them closed
        // on a different innovation and priced differently from its parent.
        // Their own keys, so a snapshot written before they were carried
        // restores to the zeros a day that has not started holds -- which is
        // every run recorded while the dial shipped 0.0.
        let flat3 = |rows: &[[f64; 3]]| -> Vec<f64> {
            rows.iter().flat_map(|r| r.iter().copied()).collect()
        };
        out.set_item("noise_parts", f64_bytes(py, &flat3(self.inner.noise_parts())))?;
        out.set_item(
            "noise_own_scale2",
            f64_bytes(py, self.inner.noise_own_scale2()),
        )?;
        // The jump each name booked at the last close, kept across the open
        // for the session that trades the gap in. Written and read only off
        // the shipped `volume_move_jump_share` of 1.0, and carried here so a
        // restored engine's first session reads the gap a copy's reads.
        out.set_item("jump_move", f64_bytes(py, self.inner.jump_move()))?;
        out.set_item("market_open", self.market_open)?;
        // The market factor's variance state: (variance, day_factor).
        // Engine-level rather than per-company, so it has no column; a
        // fork that lost it would re-open at the baseline factor sigma
        // mid-regime and diverge from its parent at the next close.
        let (market_variance, market_day_factor, market_fast_variance,
             market_slow_variance, market_prev_day_factor, market_smoothed_vix) =
            self.inner.market_variance_state();
        out.set_item(
            "market_variance",
            vec![market_variance, market_day_factor, market_fast_variance,
                 market_slow_variance, market_prev_day_factor,
                 market_smoothed_vix],
        )?;
        // The forced-flow segment's spent budget (round 143). Its own key:
        // a snapshot without it restores to 0.0, which is bit-exact for
        // every run recorded while the reservoir dial shipped 0.0.
        out.set_item("forced_flow_spent", self.inner.forced_flow_spent())?;
        // The market factor's slow variance level, in logs. Its own key
        // for the reason the line above has one: a snapshot without it
        // restores to 0.0, a multiplier of exactly 1.0, which is what
        // every run recorded while `market_vol_level_sigma` shipped 0.0
        // actually carried.
        out.set_item("market_vol_log_level", self.inner.market_vol_log_level())?;
        out.set_item("vix_log_level", self.inner.vix_log_level())?;
        // Only where the hash covers it: the memory moves, and is hashed,
        // only with `vix_anchor_memory` nonzero.
        if self.inner.params().vix_anchor_memory != 0.0 {
            out.set_item("vix_anchor_slow", self.inner.vix_anchor_slow())?;
        }
        // THE CRISIS EPISODE: whether one is running, how many consecutive
        // sessions it has spent under the threshold, the sector index its
        // epicentre was drawn at (`-1` for `none`, a crisis with no
        // epicentre) and the pin a scenario has set (`-2` for no pin, which
        // is no sector index and not the `-1` that means `none`).
        //
        // Four keys of their own, for the reason the two levels above have
        // theirs: a snapshot written before they existed restores to the
        // state a run with `crisis_epicentre_extra` at 0.0 carries, which is
        // every run ever recorded. A fork that dropped them would resume
        // outside the episode its parent is three sessions into, price the
        // epicentre's names without the multiple, and redraw a fresh
        // epicentre on the next crossing.
        let (in_episode, sessions_under, epicentre, pin) = self.inner.crisis_episode_raw();
        out.set_item("crisis_in_episode", in_episode)?;
        out.set_item("crisis_sessions_under", sessions_under)?;
        out.set_item("crisis_epicentre", epicentre)?;
        out.set_item("crisis_epicentre_pin", pin.unwrap_or(-2))?;
        // A forced close pending tonight. A key only while true, so every
        // snapshot of an engine that was never forced is the one it was.
        if self.inner.vix_sets_variance_pending() {
            out.set_item("vix_sets_variance_pending", true)?;
        }
        // Nominal output when the run opened, the base of the growth
        // term's ratio. A constant of the run rather than advancing state,
        // and carried for the reason the two above are: an engine restored
        // without it would rebase its valuation on the restore day and
        // grow from there, which is a plausible market and not the one the
        // snapshot describes. A snapshot written before this key
        // leaves the engine on the base it was built with, and such a
        // snapshot comes from a build where the growth term did not exist,
        // so it names a preset carrying the dial at 0.0.
        out.set_item("nominal_output_base", self.inner.nominal_output_base())?;
        // The common log-volume state. Same reason as the variance above, and
        // the same failure: omitted, a fork re-opens at volume 1.0 mid-regime
        // and diverges through the book (§74).
        out.set_item("volume_state", self.inner.volume_state())?;
        // The universe's remembered stress and the per-name volume states.
        // Both are engine-level dials that are INERT under every preset
        // through pt-v15, which is exactly the position `volume_state` above
        // was in before pt-v10 turned it on and a restored engine started
        // trading different volume. `set_universe_stress` was written for
        // this and nothing called it. Carried now, while it is free.
        out.set_item("universe_stress", self.inner.universe_stress())?;
        out.set_item("volume_idio", f64_bytes(py, self.inner.volume_idio()))?;
        // The two states pt-v19 turned on. Their own keys, so a snapshot
        // written before they were carried restores to the zeros every
        // preset through pt-v18 actually held.
        out.set_item("sector_variance", f64_bytes(py, self.inner.sector_variance()))?;
        out.set_item("jump_excitation", f64_bytes(py, self.inner.jump_excitation()))?;
        // The sector state's two per-DAY companions, carried for the
        // reason `attribution` and `tick_components` are: a fork taken
        // mid-day needs the day's accumulated sector factor and the
        // scale it was drawn at.
        out.set_item("sector_day_factor", f64_bytes(py, self.inner.sector_day_factor()))?;
        out.set_item("sector_target_day", self.inner.sector_target_day())?;
        // THE DAY'S JUMP AND OVERNIGHT MOVE, WAITING FOR A TAPE ROW.
        //
        // Both are applied at a day boundary, so no tick of that day can
        // carry them; they are written onto the FIRST TICK OF THE NEXT DAY,
        // which is where a reader reconstructing the day finds them. Between
        // the close that produced them and that row they are pending, and a
        // snapshot taken in that window used to drop them -- so a resumed
        // run's tape was missing the jump on its first recorded row while the
        // continuous run's carried it.
        //
        // Invisible until 0.7.0: the jump slot is zero unless a jump fired,
        // and pt-v18 switches on `jump_mean_compensated`, whose compensator
        // is deterministic and lands EVERY day. `test_a_resumed_run_carries_
        // its_whole_record` found it at day 2, tick 0.
        //
        // RECORDING state, not market state. Restoring them changes what the
        // tape says and no price, which is why the drift guard in
        // `test_forking.py` names them rather than seeing them move a market.
        out.set_item("pending_jump", f64_bytes(py, &self.pending_jump))?;
        out.set_item("pending_overnight", f64_bytes(py, &self.pending_overnight))?;
        // The day's endogenous news, generated once in `open_market` and read
        // by every tick of that day. Per-DAY state, not a per-tick input, and
        // omitting it made a mid-day restore run the rest of the day with the
        // news missing -- a divergence in PRICE, on the shipped default
        // preset, with nothing to indicate it.
        let news = pyo3::types::PyList::empty_bound(py);
        for event in self.inner.session_news() {
            let item = PyDict::new_bound(py);
            item.set_item("ticker", event.company_id.clone())?;
            item.set_item("sector", event.sector.clone())?;
            item.set_item("price_impact", event.price_impact)?;
            news.append(item)?;
        }
        out.set_item("session_news", news)?;

        // The macro chain's state. The chain advances at every close now, so
        // a fork that did not carry these would snap back to the initial
        // economy and diverge from its parent on the first day boundary --
        // the same failure class the per-day accumulators above fix, one
        // level up. Field-by-field rather than opaque bytes, for the same
        // reason the columns are named: a snapshot someone archived should
        // be inspectable data, not a blob only this build can read.
        let economy = self.inner.economy();
        let econ = PyDict::new_bound(py);
        macro_rules! econ_put {
            ($($field:ident),* $(,)?) => {
                $(econ.set_item(stringify!($field), economy.$field)?;)*
            };
        }
        econ_put!(
            federal_funds_rate, prime_rate, corporate_bond_yield,
            treasury_yield_10y, treasury_yield_2y, mortgage_rate_30y,
            cpi, inflation_rate, core_inflation,
            gdp_growth, gdp,
            unemployment_rate, jobs_created, labor_force_participation,
            usd_index, oil_price, gold_price, copper_price,
            housing_index, home_starts_monthly, housing_transaction_volume,
            long_term_unemployment_rate, structural_unemployment,
            consumer_confidence, business_confidence, fear_greed_index, vix,
            tariff_rate, trade_balance,
            oil_inventory_level, oil_last_opec_day,
            wage_growth,
            previous_day_market_return, rolling_market_return_30d,
            market_pe, qe_pe_boost,
            fiscal_stimulus, government_debt_to_gdp,
            months_in_current_phase, phase_gdp_target, recession_probability,
        );
        econ.set_item("gdp_trend", economy.gdp_trend.to_vec())?;
        econ.set_item("cycle_phase", economy.cycle_phase.as_str())?;
        out.set_item("economy", econ)?;

        let bank = self.inner.central_bank();
        let cb = PyDict::new_bound(py);
        cb.set_item("last_meeting_date", bank.last_meeting_date)?;
        cb.set_item("next_meeting_date", bank.next_meeting_date)?;
        cb.set_item("target_inflation", bank.target_inflation)?;
        cb.set_item("target_unemployment", bank.target_unemployment)?;
        cb.set_item("qe_active", bank.qe_active)?;
        cb.set_item("qe_monthly_purchases", bank.qe_monthly_purchases)?;
        cb.set_item("hawkish_dovish_score", bank.hawkish_dovish_score)?;
        cb.set_item("forward_guidance", bank.forward_guidance.as_str())?;
        out.set_item("central_bank", cb)?;

        out.set_item("day_count", self.day_count)?;
        // The rate instruments, only when the engine holds any, so every
        // snapshot of an engine without them is the one it always was. Each
        // instrument's state by name, in `RATE_STATE_FIELDS` order, which is
        // the order the state hash walks.
        let rates = self.inner.rates();
        if !rates.is_empty() {
            let block = PyDict::new_bound(py);
            let items = pyo3::types::PyList::empty_bound(py);
            for inst in &rates.instruments {
                let item = PyDict::new_bound(py);
                item.set_item("ticker", inst.spec.ticker)?;
                for (name, value) in RATE_STATE_FIELDS.iter().zip(rate_state(inst)) {
                    item.set_item(*name, value)?;
                }
                items.append(item)?;
            }
            block.set_item("instruments", items)?;
            block.set_item("ig_spread", rates.ig_spread)?;
            block.set_item("last_corporate", rates.last_corporate)?;
            block.set_item("closed_since_open", rates.closed_since_open)?;
            out.set_item("rates", block)?;
        }
        // The agent-facing book, only once it has been used, so every
        // snapshot of a run that never saw an agent's order is the dict it
        // was before the book existed. `manifest.state_hash` accepts it
        // exactly when it is here.
        if !self.inner.book_state().is_pristine() {
            out.set_item("book", book_to_py(py, self.inner.book_state())?)?;
        }
        Ok(out.into())
    }

    /// Put a market back to a captured state.
    ///
    /// Refuses a snapshot whose roster does not match this engine's, because
    /// the columns are positional: writing them onto a re-ordered or
    /// differently-sized roster would attach every price to the wrong company
    /// and look entirely plausible.
    ///
    /// # Matching tickers do NOT mean a matching universe
    ///
    /// The check is on identity and order, which is all an engine knows -- it
    /// holds no fundamentals. And tickers are generated positionally, so
    /// `Universe.random(40, seed=1)` and `Universe.random(40, seed=99)` have
    /// exactly the same names and entirely different earnings, sectors and
    /// share counts.
    ///
    /// Restoring across those two would pass this check and produce a market
    /// with the right prices and the wrong fair values. The caller must supply
    /// the universe the snapshot came from; this guard catches a re-ordered or
    /// resized roster, not a substituted one.
    fn restore_state(&mut self, snapshot: &Bound<'_, PyDict>) -> PyResult<()> {
        let tickers: Vec<String> = snapshot
            .get_item("tickers")?
            .ok_or_else(|| ValidationError::new_err("snapshot has no 'tickers'"))?
            .extract()?;
        if tickers != self.inner.ids() {
            return Err(ValidationError::new_err(
                "snapshot roster does not match this engine. Columns are                  positional, so restoring across rosters would attach every                  value to the wrong instrument.",
            ));
        }

        // The model check mirrors the roster check: state restored under
        // different coefficients continues a market the snapshot does not
        // describe, with no visible symptom. A snapshot written before the
        // fingerprint was recorded has no key and is accepted as before --
        // the caller vouches for the context, as with the universe.
        if let Some(recorded) = snapshot.get_item("model_fingerprint")? {
            let recorded: String = recorded.extract()?;
            let ours = self.inner.params().fingerprint();
            if recorded != ours {
                return Err(ValidationError::new_err(format!(
                    "this snapshot was taken under model {recorded:?} and \
                     this engine runs {ours:?}. Restoring across models \
                     would continue the frozen market under coefficients \
                     it was never priced with; build the engine with the \
                     snapshot's model instead."
                )));
            }
        }

        let columns = snapshot
            .get_item("columns")?
            .ok_or_else(|| ValidationError::new_err("snapshot has no 'columns'"))?;
        let columns = columns.downcast::<PyDict>()?;
        for name in COLUMN_FIELDS {
            let raw = columns.get_item(name)?.ok_or_else(|| {
                ValidationError::new_err(format!("snapshot is missing column {name:?}"))
            })?;
            let bytes: &[u8] = raw.extract()?;
            let values: Vec<f64> = bytes
                .chunks_exact(8)
                .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                .collect();
            self.inner
                .set_column(parse_field(name)?, &values)
                .map_err(ValidationError::new_err)?;
        }

        let rng: Vec<f64> = snapshot
            .get_item("rng")?
            .ok_or_else(|| ValidationError::new_err("snapshot has no 'rng'"))?
            .extract()?;
        if rng.len() == 3 {
            // A pre-split snapshot carries ONE stream where this engine has
            // three. Guessing at the other two would restore a market that
            // looks right and silently draws different macro and embedder
            // sequences, and the trajectory it froze belongs to the old
            // era anyway, so it cannot be continued bit-exactly here.
            return Err(ValidationError::new_err(
                "this snapshot predates the RNG stream split (3 rng numbers, \
                 expected 9). It froze a single-stream market that this \
                 version cannot continue bit-exactly; re-run it under the \
                 version that wrote it, or re-simulate from the seed.",
            ));
        }
        // Three words per stream, and the count has grown three times:
        // 9 predates the jump stream, 12 carries it, 15 adds volume, 18 adds
        // endogenous news. Every length still restores, and a short snapshot
        // keeps this engine's own seed-derived position for the streams it
        // does not carry -- see the bindings below. That is what lets a
        // checkpoint written before a mechanism existed replay exactly as it
        // did then, rather than against a zeroed generator wearing its seed.
        if rng.len() < 9 || rng.len() % 3 != 0 || rng.len() > 3 * crate::rng::stream::COUNT {
            return Err(ValidationError::new_err(format!(
                "rng must be 9 numbers (market, economy, external), 12 \
                 (plus jumps), 15 (plus volume), 18 (plus news), 21 \
                 (plus per-name volume), 24 (plus overnight), 27 (plus the \
                 slow level) or 30 (plus the crisis epicentre), as \
                 (state, increment, spare) triples, got {}",
                rng.len()
            )));
        }
        let mut counts: Vec<f64> = match snapshot.get_item("draw_counts")? {
            Some(v) => v.extract()?,
            None => vec![0.0; 2 * crate::rng::stream::COUNT],
        };
        // Fourteen numbers predate the overnight stream. That stream keeps
        // the position this engine holds, as its generator does below, so a
        // snapshot from before the stream restores exactly as it did then.
        if counts.len() == 2 * (crate::rng::stream::COUNT - 1) {
            let (uniforms, normals) = self.inner.stream_positions()[crate::rng::stream::COUNT - 1];
            counts.push(uniforms as f64);
            counts.push(normals as f64);
        }
        if counts.len() != 2 * crate::rng::stream::COUNT {
            return Err(ValidationError::new_err(format!(
                "draw_counts must be {} numbers, two per stream, got {}",
                2 * crate::rng::stream::COUNT,
                counts.len()
            )));
        }
        let stream = |at: usize| crate::rng::RngState {
            state: rng[at].to_bits(),
            increment: rng[at + 1].to_bits(),
            spare: if rng[at + 2].is_nan() {
                None
            } else {
                Some(rng[at + 2])
            },
            uniforms: counts[(at / 3) * 2] as u64,
            normals: counts[(at / 3) * 2 + 1] as u64,
        };
        // A nine-number snapshot predates the jump stream. Its jump position
        // is whatever this engine derived from its seed, and keeping that is
        // the only choice that leaves such a snapshot restoring exactly as it
        // did before jumps existed -- the alternative, a zeroed generator,
        // would be a different sequence wearing the same seed.
        // A short snapshot predates a stream; its position is whatever this
        // engine derived from its seed, and keeping that is the only choice
        // that leaves such a snapshot restoring exactly as it did before the
        // stream existed. A zeroed generator would be a different sequence
        // wearing the same seed.
        let current = self.inner.rng_state();
        let jumps = if rng.len() >= 12 { stream(9) } else { current.jumps };
        let volume = if rng.len() >= 15 { stream(12) } else { current.volume };
        let news = if rng.len() >= 18 { stream(15) } else { current.news };
        let volume_idio = if rng.len() >= 21 { stream(18) } else { current.volume_idio };
        let overnight = if rng.len() >= 24 { stream(21) } else { current.overnight };
        // LITERAL OFFSETS, not `3 * COUNT`. This read `>= 3 * COUNT` and
        // `stream(3 * (COUNT - 1))` while `market_vol_level` happened to be
        // the last stream, and the day a tenth stream was added those two
        // expressions moved together: a 27-number snapshot stopped restoring
        // the slow level at all and kept the fresh engine's seed-derived
        // position, which diverged the market a day later and nothing here
        // said so. Each stream's offset is its own position in the flat
        // array and is fixed forever once written.
        let market_vol_level = if rng.len() >= 27 { stream(24) } else { current.market_vol_level };
        let crisis_epicentre = if rng.len() >= 30 { stream(27) } else { current.crisis_epicentre };
        self.inner.set_rng_state(crate::engine::EngineRngState {
            market: stream(0),
            economy: stream(3),
            external: stream(6),
            jumps,
            volume,
            news,
            volume_idio,
            overnight,
            market_vol_level,
            crisis_epicentre,
        });
        if let Some(raw) = snapshot.get_item("draw_overlay")? {
            let entries: Vec<(u32, u8, u64, f64)> = raw.extract()?;
            for id in 0..crate::rng::stream::COUNT as u32 {
                self.inner.set_draw_overlay(id, None);
            }
            for (id, kind, index, value) in entries {
                let kind = match kind {
                    0 => crate::rng::DrawKind::Uniform,
                    1 => crate::rng::DrawKind::Normal,
                    other => {
                        return Err(ValidationError::new_err(format!(
                            "draw_overlay kind must be 0 (uniform) or 1 (normal), got {other}"
                        )))
                    }
                };
                self.inner.patch_draw(id, kind, index, value);
            }
        }

        // The per-day accumulators. Absent from a snapshot written before
        // these were carried, so they are optional and default to "a day that
        // has not started" -- which is what such a snapshot described.
        let buffer = |key: &str| -> PyResult<Option<Vec<f64>>> {
            match snapshot.get_item(key)? {
                None => Ok(None),
                Some(raw) => {
                    let bytes: &[u8] = raw.extract()?;
                    Ok(Some(
                        bytes
                            .chunks_exact(8)
                            .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                            .collect(),
                    ))
                }
            }
        };
        let n = self.inner.len();
        if let Some(attribution) = buffer("attribution")? {
            let components = buffer("tick_components")?.unwrap_or_else(|| vec![0.0; n * 7]);
            let fundamental =
                buffer("tick_fundamental")?.unwrap_or_else(|| vec![f64::NAN; n]);
            let anchor = buffer("tick_anchor")?.unwrap_or_else(|| vec![f64::NAN; n]);
            self.inner
                .restore_day_state(&attribution, &components, &fundamental, &anchor)
                .map_err(ValidationError::new_err)?;
        }
        // The day's noise split. Absent means a snapshot from a build that
        // did not carry it, whose day the close read at the whole
        // `random_noise` column, which is what zeros here reproduce.
        if let Some(parts) = buffer("noise_parts")? {
            let scale2 = buffer("noise_own_scale2")?.unwrap_or_else(|| vec![0.0; n]);
            self.inner
                .restore_noise_split(&parts, &scale2)
                .map_err(ValidationError::new_err)?;
        }
        // The jump waiting for the session that trades it in. Absent means a
        // snapshot from a build without it, and every such run shipped
        // `volume_move_jump_share` at 1.0, where the vector is never written.
        if let Some(moves) = buffer("jump_move")? {
            self.inner
                .set_jump_move(&moves)
                .map_err(ValidationError::new_err)?;
        }
        if let Some(flag) = snapshot.get_item("market_open")? {
            // Without this the fork believes the day has not started, re-opens
            // on its next session, and re-anchors `previous_close` mid-day --
            // so it prices differently from the parent it forked from.
            self.market_open = flag.extract()?;
        }
        if let Some(raw) = snapshot.get_item("volume_state")? {
            self.inner.set_volume_state(raw.extract::<f64>()?);
        }
        if let Some(raw) = snapshot.get_item("universe_stress")? {
            self.inner.set_universe_stress(raw.extract::<f64>()?);
        }
        for (key, slot) in [("pending_jump", 0usize), ("pending_overnight", 1usize)] {
            if let Some(raw) = snapshot.get_item(key)? {
                let bytes: &[u8] = raw.extract()?;
                let values: Vec<f64> = bytes
                    .chunks_exact(8)
                    .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                    .collect();
                if slot == 0 {
                    self.pending_jump = values;
                } else {
                    self.pending_overnight = values;
                }
            }
        }
        if let Some(raw) = snapshot.get_item("volume_idio")? {
            let bytes: &[u8] = raw.extract()?;
            let values: Vec<f64> = bytes
                .chunks_exact(8)
                .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                .collect();
            self.inner
                .set_volume_idio(&values)
                .map_err(ValidationError::new_err)?;
        }
        // AFTER the volume states on purpose: `set_volume_idio`'s docstring
        // describes a positional boundary -- what holds the snapshot's value
        // when a width mismatch refuses, and what holds the engine's -- and
        // adding these on that side leaves every sentence of it true.
        //
        // Absent means a snapshot from before these were carried, whose
        // preset shipped both mechanisms at 0.0 and whose arrays were
        // therefore all zeros, which is what a fresh engine holds.
        // The day accumulators, restored together because the factor is
        // only meaningful beside the scale it was drawn at. Absent means a
        // snapshot from before they were carried, whose preset ran no
        // sector state, and a fresh engine's zeros are what it described.
        if let Some(raw) = snapshot.get_item("sector_day_factor")? {
            let bytes: &[u8] = raw.extract()?;
            let values: Vec<f64> = bytes
                .chunks_exact(8)
                .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                .collect();
            let target = match snapshot.get_item("sector_target_day")? {
                Some(v) => v.extract()?,
                None => 0.0,
            };
            self.inner
                .set_sector_day(&values, target)
                .map_err(ValidationError::new_err)?;
        }
        for (key, sector) in [("sector_variance", true), ("jump_excitation", false)] {
            let Some(raw) = snapshot.get_item(key)? else { continue };
            let bytes: &[u8] = raw.extract()?;
            let values: Vec<f64> = bytes
                .chunks_exact(8)
                .map(|c| f64::from_le_bytes(c.try_into().unwrap()))
                .collect();
            if sector {
                self.inner.set_sector_variance(&values)
            } else {
                self.inner.set_jump_excitation(&values)
            }
            .map_err(ValidationError::new_err)?;
        }
        // Absent in a snapshot written before this was carried. Such a
        // snapshot described a day whose news this engine cannot know, so the
        // honest restore is the empty day it recorded -- which is what those
        // archives already replay to.
        if let Some(raw) = snapshot.get_item("session_news")? {
            let items = raw.downcast::<pyo3::types::PyList>()?;
            let mut events = Vec::with_capacity(items.len());
            for item in items.iter() {
                let d = item.downcast::<PyDict>()?;
                let get = |key: &str| -> PyResult<Option<Bound<'_, PyAny>>> {
                    Ok(d.get_item(key)?)
                };
                events.push(NewsEvent {
                    company_id: match get("ticker")? {
                        Some(v) => v.extract()?,
                        None => None,
                    },
                    sector: match get("sector")? {
                        Some(v) => v.extract()?,
                        None => None,
                    },
                    price_impact: match get("price_impact")? {
                        Some(v) => v.extract()?,
                        None => None,
                    },
                });
            }
            self.inner.set_session_news(events);
        }
        // Restore the forced-flow budget; absent means a pre-reservoir
        // snapshot, whose runs all carried 0.0.
        if let Some(raw) = snapshot.get_item("forced_flow_spent")? {
            self.inner.set_forced_flow_spent(raw.extract()?);
        }
        // Absent means a snapshot from a build without the slow level,
        // whose runs all carried a multiplier of exactly 1.0.
        if let Some(raw) = snapshot.get_item("market_vol_log_level")? {
            self.inner.set_market_vol_log_level(raw.extract()?);
        }
        // Absent means a snapshot from a build without the VIX level, whose
        // runs all carried a multiplier of exactly 1.0.
        if let Some(raw) = snapshot.get_item("vix_log_level")? {
            self.inner.set_vix_log_level(raw.extract()?);
        }
        // Absent means a build without the anchor's memory, where it was 0.0.
        if let Some(raw) = snapshot.get_item("vix_anchor_slow")? {
            self.inner.set_vix_anchor_slow(raw.extract()?);
        }
        // The crisis episode. Absent means a snapshot from a build without
        // it, and every such run shipped `crisis_epicentre_extra` at 0.0,
        // where no episode is ever entered -- which is what the defaults
        // here reproduce. Read as one group so a half-written snapshot
        // cannot restore an episode with no epicentre index behind it.
        if let Some(raw) = snapshot.get_item("crisis_in_episode")? {
            let in_episode: bool = raw.extract()?;
            let sessions_under: i64 = match snapshot.get_item("crisis_sessions_under")? {
                Some(v) => v.extract()?,
                None => 0,
            };
            let epicentre: i32 = match snapshot.get_item("crisis_epicentre")? {
                Some(v) => v.extract()?,
                None => -1,
            };
            let pin: i32 = match snapshot.get_item("crisis_epicentre_pin")? {
                Some(v) => v.extract()?,
                None => -2,
            };
            self.inner.set_crisis_episode_raw(
                in_episode,
                sessions_under,
                epicentre,
                if pin <= -2 { None } else { Some(pin) },
            );
        }
        // Absent means no forced close was pending when it was taken.
        let pending: bool = match snapshot.get_item("vix_sets_variance_pending")? {
            Some(v) => v.extract()?,
            None => false,
        };
        self.inner.set_vix_sets_variance_pending(pending);
        // Restore the growth term's base. Absent means a snapshot from a
        // build without the term, whose preset carries the dial at 0.0.
        if let Some(raw) = snapshot.get_item("nominal_output_base")? {
            self.inner.set_nominal_output_base(raw.extract()?);
        }
        if let Some(raw) = snapshot.get_item("market_variance")? {
            let vals: Vec<f64> = raw.extract()?;
            // Two values is a checkpoint written before the slow component
            // existed; three is one written after; four adds the mixture
            // components; five carries the lagged-wire memory. All replay.
            if vals.len() < 2 || vals.len() > 6 {
                return Err(ValidationError::new_err(format!(
                    "market_variance must be [variance, day_factor], optionally \
                     plus the component levels and the lagged-wire memory, \
                     got {} values",
                    vals.len()
                )));
            }
            match vals.len() {
                // Five carries the lagged-wire memory. Four is a pt-v4
                // checkpoint (no lag memory; the wire skips one session,
                // which is what that era did anyway). Three predates the
                // mixture and carried an additive slow level; adopting it
                // as both components is the only reading that leaves a
                // legacy preset replaying identically, where neither is
                // read.
                6 => self.inner.set_market_variance_state_with_components(
                    vals[0], vals[1], vals[2], vals[3], vals[4], vals[5]),
                5 => self.inner.set_market_variance_state_with_components(
                    vals[0], vals[1], vals[2], vals[3], vals[4], -1.0),
                4 => self.inner.set_market_variance_state_with_components(
                    vals[0], vals[1], vals[2], vals[3], 0.0, -1.0),
                3 => self.inner.set_market_variance_state_with_components(
                    vals[0], vals[1], vals[0], vals[2], 0.0, -1.0),
                _ => self.inner.set_market_variance_state(vals[0], vals[1]),
            }
        }

        // The macro chain's state. Optional for the same reason as the
        // per-day accumulators above: a snapshot written before the chain
        // was carried described a world where the macro never moved, and
        // restoring one leaves this engine's constructed economy in place --
        // which is exactly what that snapshot meant.
        if let Some(raw) = snapshot.get_item("economy")? {
            let d = raw.downcast::<PyDict>()?;
            let economy = self.inner.economy_mut();
            macro_rules! econ_get {
                ($($field:ident),* $(,)?) => {
                    $(if let Some(v) = d.get_item(stringify!($field))? {
                        economy.$field = v.extract()?;
                    })*
                };
            }
            econ_get!(
                federal_funds_rate, prime_rate, corporate_bond_yield,
                treasury_yield_10y, treasury_yield_2y, mortgage_rate_30y,
                cpi, inflation_rate, core_inflation,
                gdp_growth, gdp,
                unemployment_rate, jobs_created, labor_force_participation,
                usd_index, oil_price, gold_price, copper_price,
                housing_index, home_starts_monthly, housing_transaction_volume,
                long_term_unemployment_rate, structural_unemployment,
                consumer_confidence, business_confidence, fear_greed_index, vix,
                tariff_rate, trade_balance,
                oil_inventory_level, oil_last_opec_day,
                wage_growth,
                previous_day_market_return, rolling_market_return_30d,
                market_pe, qe_pe_boost,
                fiscal_stimulus, government_debt_to_gdp,
                months_in_current_phase, phase_gdp_target, recession_probability,
            );
            if let Some(v) = d.get_item("gdp_trend")? {
                let trend: Vec<f64> = v.extract()?;
                if trend.len() != 4 {
                    return Err(ValidationError::new_err(format!(
                        "gdp_trend must be 4 numbers, got {}",
                        trend.len()
                    )));
                }
                economy.gdp_trend = [trend[0], trend[1], trend[2], trend[3]];
            }
            if let Some(v) = d.get_item("cycle_phase")? {
                let name: String = v.extract()?;
                economy.cycle_phase = CyclePhase::from_name(&name).ok_or_else(|| {
                    ValidationError::new_err(format!("unknown cycle phase {name:?}"))
                })?;
            }
        }
        if let Some(raw) = snapshot.get_item("central_bank")? {
            let d = raw.downcast::<PyDict>()?;
            let bank = self.inner.central_bank_mut();
            if let Some(v) = d.get_item("last_meeting_date")? {
                bank.last_meeting_date = v.extract()?;
            }
            if let Some(v) = d.get_item("next_meeting_date")? {
                bank.next_meeting_date = v.extract()?;
            }
            if let Some(v) = d.get_item("target_inflation")? {
                bank.target_inflation = v.extract()?;
            }
            if let Some(v) = d.get_item("target_unemployment")? {
                bank.target_unemployment = v.extract()?;
            }
            if let Some(v) = d.get_item("qe_active")? {
                bank.qe_active = v.extract()?;
            }
            if let Some(v) = d.get_item("qe_monthly_purchases")? {
                bank.qe_monthly_purchases = v.extract()?;
            }
            if let Some(v) = d.get_item("hawkish_dovish_score")? {
                bank.hawkish_dovish_score = v.extract()?;
            }
            if let Some(v) = d.get_item("forward_guidance")? {
                let name: String = v.extract()?;
                bank.forward_guidance =
                    ForwardGuidance::from_name(&name).ok_or_else(|| {
                        ValidationError::new_err(format!(
                            "unknown forward guidance {name:?}"
                        ))
                    })?;
            }
        }
        // The marks name the days THIS engine opened, and a restore replaces
        // the run. Kept across one, they named days the restored engine had
        // not run: two days, then a three-day snapshot, then two more
        // reported marks for 0, 1, 3 and 4.
        self.inner.clear_day_marks();
        if let Some(v) = snapshot.get_item("day_count")? {
            self.day_count = v.extract()?;
        }
        // The core's own day, pushed rather than left where construction put
        // it. `open_market` sets it from `day_count` at every open, so the
        // two agree at every point a snapshot can be taken; a restore that
        // stops before the next open is the one path where they part.
        //
        // That path used to reach nothing. It reaches the valuation now,
        // because the buyback factor is `exp(yield * elapsed / 252)` and
        // elapsed is this number, so a market restored MID-DAY priced its
        // next tick as though the run had just begun. The forking guard
        // caught it as a divergence in every column, which is what it is
        // for: something the engine carries drove the market and was not
        // restored with it.
        self.inner.set_current_day(i64::from(self.day_count));

        // The rate instruments. Required exactly when this engine holds them,
        // and for the same tickers in the same order: a snapshot restored
        // without them would leave the indices at this engine's own levels
        // under a restored curve, and they would reprice by the difference at
        // the next open.
        let held: Vec<&str> = self
            .inner
            .rates()
            .instruments
            .iter()
            .map(|i| i.spec.ticker)
            .collect();
        match snapshot.get_item("rates")? {
            None if held.is_empty() => {}
            None => {
                return Err(ValidationError::new_err(format!(
                    "this engine holds the rate instruments {} and the snapshot \
                     carries none. It was taken on a roster without them.",
                    held.join(", ")
                )))
            }
            Some(block) => {
                let block = block.downcast::<PyDict>()?;
                let items: Vec<Bound<'_, PyDict>> = block
                    .get_item("instruments")?
                    .ok_or_else(|| ValidationError::new_err("snapshot rates has no 'instruments'"))?
                    .extract()?;
                let mut tickers = Vec::with_capacity(items.len());
                for item in &items {
                    let t: String = item
                        .get_item("ticker")?
                        .ok_or_else(|| ValidationError::new_err("a rate instrument has no 'ticker'"))?
                        .extract()?;
                    tickers.push(t);
                }
                if tickers != held {
                    return Err(ValidationError::new_err(format!(
                        "the snapshot's rate instruments are [{}] and this engine \
                         holds [{}]. Columns are positional, so they must match.",
                        tickers.join(", "),
                        held.join(", ")
                    )));
                }
                fn get<'py>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<Bound<'py, PyAny>> {
                    d.get_item(key)?.ok_or_else(|| {
                        ValidationError::new_err(format!("snapshot rates is missing {key:?}"))
                    })
                }
                let mut values: Vec<[f64; RATE_STATE_FIELDS.len()]> = Vec::new();
                for item in &items {
                    let mut row = [0.0; RATE_STATE_FIELDS.len()];
                    for (k, name) in RATE_STATE_FIELDS.iter().enumerate() {
                        row[k] = get(item, name)?.extract()?;
                    }
                    values.push(row);
                }
                let ig_spread: f64 = get(block, "ig_spread")?.extract()?;
                let last_corporate: f64 = get(block, "last_corporate")?.extract()?;
                let closed: bool = get(block, "closed_since_open")?.extract()?;
                let book = self.inner.rates_mut();
                for (inst, row) in book.instruments.iter_mut().zip(values) {
                    set_rate_state(inst, &row);
                }
                book.ig_spread = ig_spread;
                book.last_corporate = last_corporate;
                book.closed_since_open = closed;
            }
        }
        // The book is part of the state: restored when the snapshot carries
        // it, and pristine when it does not, so a restore never keeps the
        // orders or the consumed depth of the market it replaced.
        let book = match snapshot.get_item("book")? {
            Some(raw) => book_from_py(raw.downcast::<PyDict>()?)?,
            None => crate::agent_book::BookState::default(),
        };
        self.inner.set_book_state(book).map_err(ValidationError::new_err)?;
        Ok(())
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
        // The DAY's tape, not the last session's. See `DayBuffer`.
        self.recorded.push(crate::python_arrow::RecordedDay {
            day,
            ticks: self.day_buffer.ticks,
            instruments: self.day_buffer.companies,
            prices: self.day_buffer.prices.clone(),
            volumes: self.day_buffer.volumes.clone(),
            // The session's open, the engine's mark from `open_market`,
            // which the close leaves alone, so a record taken on either
            // side of it reads the same value.
            opens: self.all_column(PriceField::Open),
            mispricing: self.day_buffer.mispricing.clone(),
            fundamental: self.day_buffer.fundamental.clone(),
            anchor: self.day_buffer.anchor.clone(),
            components: std::array::from_fn(|k| self.day_buffer.components[k].clone()),
            shock: self.day_buffer.shock.clone(),
            absorbed: self.day_buffer.absorbed.clone(),
            clamp: self.day_buffer.clamp.clone(),
            unbounded_print: self.day_buffer.unbounded_print.clone(),
            liquidity_share: self.day_buffer.liquidity_share.clone(),
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
            universe_stress: self.inner.universe_stress(),
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
    ///
    /// `day = None`, the default, is every recorded day; `day = N` is that day
    /// alone. Like `truth`, it used to be discarded once anything had been
    /// recorded and only labelled the un-recorded fallback.
    #[pyo3(signature = (*, day = None, minutes = None, grain = None))]
    fn bars(
        &self,
        day: Option<u32>,
        minutes: Option<usize>,
        grain: Option<&str>,
    ) -> PyResult<crate::python_arrow::PyArrowStream> {
        let days: Vec<crate::python_arrow::RecordedDay> = if self.recorded.is_empty() {
            vec![crate::python_arrow::RecordedDay {
                // The label, as it has always been on this path.
                day: day.unwrap_or(0),
                ticks: self.buffer.ticks_written,
                instruments: self.buffer.companies,
                prices: self.written(&self.buffer.prices).to_vec(),
                volumes: self.written(&self.buffer.volumes).to_vec(),
                opens: self.all_column(PriceField::Open),
                // bars() reads neither, and cloning the ground-truth
                // buffers to build a table that discards them would be pure
                // copying. truth() has its own path below.
                mispricing: Vec::new(),
                fundamental: Vec::new(),
                anchor: Vec::new(),
                components: std::array::from_fn(|_| Vec::new()),
                shock: Vec::new(),
                absorbed: Vec::new(),
                clamp: Vec::new(),
                unbounded_print: Vec::new(),
                liquidity_share: Vec::new(),
            }]
        } else {
            self.select_recorded(day)?
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
    ///
    /// # `day` selects, and used not to
    ///
    /// `day = None`, the default, is every recorded day: one batch each, so a
    /// year streams. `day = N` is that day alone.
    ///
    /// It was ignored entirely once anything had been recorded -- the argument
    /// only ever labelled the un-recorded fallback -- so `truth(day=4)` on a
    /// hundred-day run returned all hundred days and looked like it had
    /// answered. The table had the right schema and plausible values, which is
    /// why nothing noticed for as long as it did.
    #[pyo3(signature = (*, day = None))]
    fn truth(&self, day: Option<u32>) -> PyResult<crate::python_arrow::PyArrowStream> {
        let batches = if self.recorded.is_empty() {
            vec![crate::python_arrow::truth_batch(
                // Nothing is recorded, so there is no day to select and the
                // argument is what it always was here: the label on the rows.
                day.unwrap_or(0),
                self.buffer.ticks_written,
                self.buffer.companies,
                self.written(&self.buffer.mispricing_s),
                self.written(&self.buffer.fundamental),
                self.written(&self.buffer.anchor),
                // Eight series from a seven-wide session buffer: the tick's
                // own components, then the jump, which the tick loop never
                // writes. On this un-recorded path the day has not closed, so
                // there is no jump yet and the column is zeros (§74).
                &std::array::from_fn(|k| {
                    if k < crate::market::factors::S_COMPONENT_KEYS.len() {
                        self.written(&self.buffer.components[k]).to_vec()
                    } else {
                        vec![0.0; self.written(&self.buffer.components[0]).len()]
                    }
                }),
            )
            .map_err(crate::python_arrow::arrow_err)?]
        } else {
            let selected = self.select_recorded(day)?;
            let mut out = Vec::with_capacity(selected.len());
            for d in &selected {
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

    /// Settle every open tick a second time against unbounded depth.
    ///
    /// Off by default. With it on, `prints()` carries `unbounded_print` and
    /// `liquidity_share`: what the same tick would have printed against every
    /// resting level, under the same four uniforms and from the same book
    /// state, and how much of the print's move that difference accounts for.
    ///
    /// The market does not change. The second settlement runs on its own
    /// book, takes no draw, and its fills reach no company field, so the
    /// known-answer digest is the same digest with the arm on. What it costs
    /// is roughly one settlement per active company per open tick, which is
    /// the largest single item in a tick.
    ///
    /// Set it before the FIRST session of a day. A day whose sessions
    /// disagree records fewer counterfactual values than it has rows, and
    /// `prints()` drops both columns for that whole day rather than serving
    /// one with a gap in it. The table's schema caveat names that case, so a
    /// caller who switched the arm mid-day is told why the columns are gone
    /// rather than being told to do what they just did. Days that disagree
    /// with EACH OTHER are a different matter and raise.
    ///
    /// The run log does not carry it, because the log carries INPUTS and this
    /// is not one: no draw, no price and no company field depends on it. A
    /// replay therefore rebuilds the same market and the same `shock` and
    /// `absorbed` columns, and rebuilds the arm only if it is asked for
    /// again.
    #[pyo3(signature = (on = true))]
    fn settle_depth_counterfactual(&mut self, on: bool) {
        self.inner.set_settle_depth_counterfactual(on);
    }

    /// The `prints` table: how each print was arrived at.
    ///
    /// `truth` says what moved fair value. This says what happened between
    /// fair value and the tape: `shock` is the log distance from the last
    /// print to the model price, `absorbed` is the log distance from the
    /// model price to the print, and the two sum to the print's own log move.
    ///
    ///   `prints()`        every recorded day, one batch each
    ///   `prints(day=N)`   that day alone
    ///
    /// `absorbed` is measured to the PRINTED price, so it carries the second
    /// circuit breaker as well as the book. `clamp` is the breaker's own
    /// part of it, and `absorbed - clamp` is the book's.
    ///
    /// Read them apart. On every clamped print measured, the book and the
    /// breaker pull opposite ways, and on roughly three fifths of them they
    /// cancel to the last bit, so `absorbed` alone reads exactly zero on a
    /// name the breaker had just moved 513 basis points -- the same value it
    /// takes on a tick that never settled. `clamp` is what tells those two
    /// rows apart.
    ///
    /// `unbounded_print` and `liquidity_share` are present only when
    /// `settle_depth_counterfactual(True)` was set before the run, and the
    /// schema metadata says which case the table is. Asking for several days
    /// that disagree raises rather than dropping the columns, because a table
    /// whose meaning changes half way down is worse than an error.
    ///
    /// `liquidity_share` is NEGATIVE on most rows that carry one, because
    /// the depth bound truncates a walk rather than adding to it: an order
    /// that exhausts a shallow book stops there, while against every resting
    /// level it keeps filling and prints further out. The unbounded move is
    /// `1 - liquidity_share` times the printed move.
    #[pyo3(signature = (*, day = None))]
    fn prints(&self, day: Option<u32>) -> PyResult<crate::python_arrow::PyArrowStream> {
        use crate::python_arrow::DepthColumns;
        // Which of the three shapes a set of buffers is in. A column the
        // length of the table is the arm; an empty one is no arm; anything
        // between is a day whose sessions disagreed, and that day is served
        // WITHOUT the columns rather than with a gap in them.
        let state = |len: usize, rows: usize| {
            if len == 0 || rows == 0 {
                DepthColumns::Absent
            } else if len >= rows {
                DepthColumns::Present
            } else {
                DepthColumns::PartialDay
            }
        };
        let ran = |s: DepthColumns| match s {
            DepthColumns::Present => "with",
            DepthColumns::Absent => "without",
            DepthColumns::PartialDay => "part way through",
        };

        let (batches, depth) = if self.recorded.is_empty() {
            let ticks = self.buffer.ticks_written;
            let instruments = self.buffer.companies;
            let depth = state(
                self.written(&self.buffer.unbounded_print).len(),
                ticks * instruments,
            );
            (
                vec![crate::python_arrow::prints_batch(
                    // Nothing is recorded, so there is no day to select and
                    // the argument is the label on the rows, as it is on the
                    // same path in `bars` and `truth`.
                    day.unwrap_or(0),
                    ticks,
                    instruments,
                    self.written(&self.buffer.prices),
                    self.written(&self.buffer.anchor),
                    self.written(&self.buffer.shock),
                    self.written(&self.buffer.absorbed),
                    self.written(&self.buffer.clamp),
                    self.written(&self.buffer.unbounded_print),
                    self.written(&self.buffer.liquidity_share),
                    depth,
                )
                .map_err(crate::python_arrow::arrow_err)?],
                depth,
            )
        } else {
            let selected = self.select_recorded(day)?;
            // Decided across the whole selection, and a disagreement between
            // DAYS is reported rather than resolved. A run that switched the
            // arm on half way through has two kinds of day in it, and picking
            // either one for the schema mislabels the other. A disagreement
            // WITHIN one day is a different thing: `PartialDay` is a shape,
            // and the caveat on it names the case.
            let rows = |d: &crate::python_arrow::RecordedDay| d.ticks * d.instruments;
            let depth = selected
                .first()
                .map(|d| state(d.unbounded_print.len(), rows(d)))
                .unwrap_or(DepthColumns::Absent);
            for d in &selected {
                let has = state(d.unbounded_print.len(), rows(d));
                if has != depth {
                    return Err(ValidationError::new_err(format!(
                        "day {} ran {} the depth counterfactual and the rest ran {} it. \
                         Ask for one day at a time, or re-run with one setting.",
                        d.day,
                        ran(has),
                        ran(depth),
                    )));
                }
            }
            let mut out = Vec::with_capacity(selected.len());
            for d in &selected {
                out.push(
                    crate::python_arrow::prints_batch(
                        d.day,
                        d.ticks,
                        d.instruments,
                        &d.prices,
                        &d.anchor,
                        &d.shock,
                        &d.absorbed,
                        &d.clamp,
                        &d.unbounded_print,
                        &d.liquidity_share,
                        depth,
                    )
                    .map_err(crate::python_arrow::arrow_err)?,
                );
            }
            (out, depth)
        };
        Ok(crate::python_arrow::PyArrowStream::new(
            "prints",
            crate::python_arrow::prints_schema(depth),
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
    /// market only learns about your trading through `fills` on the next
    /// `run_session` (or `order_flow` on the next `tick`). Those are two
    /// separate channels on purpose -- one prices your fill, the other
    /// applies your pressure, once -- and a harness that wants both must do
    /// both.
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
    /// One snapshot is ten levels a side, both sides: 20 rows per (tick,
    /// instrument) against one `bars` row. A hundred names at 390 ticks is
    /// 780,000 rows a day, against 39,000 for `bars`. Recording depth by
    /// default would make every run twenty times more expensive to answer a
    /// question most runs never ask.
    ///
    /// The multiplier is the book's structure, not the `levels` argument:
    /// the maker quotes `BOOK_LEVELS = 10` a side (microstructure.rs), and
    /// `price_levels()` can only return what the book holds, so asking for
    /// `levels = 20` records the same 20 rows per name. Measured: six
    /// instruments, one snapshot, 120 rows at levels 10 and 20 alike.
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
        for index in 0..self.inner.instrument_count() {
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

    // ── Agents' orders ────────────────────────────────────────────────────

    /// Send one agent's order to this market's book.
    ///
    /// ``quantity`` is signed, positive to buy, as ``Portfolio.execute``
    /// takes it. ``limit_price=None`` is a market order: it takes what the
    /// book holds and the rest is reported ``unfilled``. A limit order takes
    /// what the book holds at its limit or better, and its remainder waits,
    /// in the book's queue with ``book_resting`` on, or for the traded range
    /// with it off; ``resting`` says how much. ``order_id`` is optional and
    /// must be unique among waiting orders; the engine assigns
    /// ``"{agent}-{n}"`` otherwise.
    ///
    /// Every share taken is taker flow: it reaches the market once, on the
    /// next tick that is not closed, with every other agent's, and the fills
    /// are reported both here and in ``take_fills``. Orders are processed in
    /// the order they arrive; ``submit_many`` fixes that order for a step.
    ///
    /// Returns a dict: ``order_id``, ``agent``, ``ticker``, ``side``,
    /// ``requested``, ``filled``, ``average_price``, ``worst_price``,
    /// ``reference`` (the mid the order met), ``resting``, ``unfilled``,
    /// ``mode`` (``"queue"``, ``"range"`` or None) and ``fills``.
    ///
    /// Recorded in the order log, so a replay and a fork carry it.
    #[pyo3(signature = (agent, ticker, quantity, *, limit_price = None, order_id = None))]
    fn submit(
        &mut self,
        py: Python<'_>,
        agent: &str,
        ticker: &str,
        quantity: f64,
        limit_price: Option<f64>,
        order_id: Option<String>,
    ) -> PyResult<PyObject> {
        let report = self.submit_one(agent, ticker, quantity, limit_price, order_id)?;
        report_to_py(py, &report)
    }

    /// Send several agents' orders for one step, in a fixed order.
    ///
    /// ``orders`` is a list of dicts with ``agent``, ``ticker``,
    /// ``quantity`` and optionally ``limit_price`` and ``order_id``. They
    /// are processed sorted by agent label, and within one agent in the
    /// order the list gives: the arrival order of a step is a property of
    /// who sent what, never of how the caller happened to build the list,
    /// so the same orders give the same market. Each one meets the book the
    /// ones before it left, so an agent later in the order pays for the
    /// levels an earlier one took and can hit an earlier one's resting
    /// order.
    ///
    /// Returns one report per order, in the order they were processed. An
    /// order the engine refuses raises, and the orders before it stand.
    fn submit_many(&mut self, py: Python<'_>, orders: Vec<Bound<'_, PyDict>>) -> PyResult<Vec<PyObject>> {
        let mut parsed: Vec<(String, usize, String, f64, Option<f64>, Option<String>)> = Vec::new();
        for (i, d) in orders.iter().enumerate() {
            let get = |k: &str| -> PyResult<Bound<'_, pyo3::PyAny>> {
                d.get_item(k)?.ok_or_else(|| {
                    ValidationError::new_err(format!("order {i} has no {k:?}"))
                })
            };
            let agent: String = get("agent")?.extract()?;
            let ticker: String = get("ticker")?.extract()?;
            let quantity: f64 = get("quantity")?.extract()?;
            let limit: Option<f64> = match d.get_item("limit_price")? {
                Some(v) if !v.is_none() => Some(v.extract()?),
                _ => None,
            };
            let id: Option<String> = match d.get_item("order_id")? {
                Some(v) if !v.is_none() => Some(v.extract()?),
                _ => None,
            };
            parsed.push((agent, i, ticker, quantity, limit, id));
        }
        // Stable on the list position, so one agent's orders keep theirs.
        parsed.sort_by(|a, b| a.0.cmp(&b.0).then(a.1.cmp(&b.1)));
        let mut out = Vec::with_capacity(parsed.len());
        for (agent, _, ticker, quantity, limit, id) in parsed {
            let report = self.submit_one(&agent, &ticker, quantity, limit, id)?;
            out.push(report_to_py(py, &report)?);
        }
        Ok(out)
    }

    /// Cancel a waiting order. With ``agent``, only that agent's. Returns
    /// whether one was removed. Recorded in the order log.
    #[pyo3(signature = (order_id, *, agent = None))]
    fn cancel(&mut self, order_id: &str, agent: Option<String>) -> bool {
        self.log.push(crate::python_log::LogEntry::Cancel {
            order_id: order_id.to_string(),
            agent: agent.clone(),
        });
        self.inner.cancel_order(order_id, agent.as_deref())
    }

    /// Waiting orders, in arrival order, for one agent or all: dicts with
    /// ``order_id``, ``agent``, ``ticker``, ``side``, ``limit_price``,
    /// ``quantity``, ``remaining``, ``sequence`` and ``mode``. A read.
    #[pyo3(signature = (agent = None))]
    fn open_orders(&self, py: Python<'_>, agent: Option<String>) -> PyResult<Vec<PyObject>> {
        self.inner
            .open_orders(agent.as_deref())
            .iter()
            .map(|o| order_to_py(py, o))
            .collect()
    }

    /// Collect the fills the book holds for one agent, or for all, in the
    /// order they happened, and forget them.
    ///
    /// Each is a dict: ``agent``, ``order_id``, ``ticker``, ``side``,
    /// ``quantity``, ``price``, ``liquidity`` (``"taker"``, ``"maker"`` or
    /// ``"range"``), ``counterparty`` (``"mm"``, ``"depth"``, ``"flow"``,
    /// ``"range"`` or another agent's label), ``reference``, ``day``,
    /// ``tick`` and ``sequence``. A resting order the model's flow filled
    /// during a session arrives here, and only here. Recorded in the order
    /// log, because the book no longer owes what was collected.
    #[pyo3(signature = (agent = None))]
    fn take_fills(&mut self, py: Python<'_>, agent: Option<String>) -> PyResult<Vec<PyObject>> {
        self.log.push(crate::python_log::LogEntry::TakeFills { agent: agent.clone() });
        self.inner
            .take_fills(agent.as_deref())
            .iter()
            .map(|f| fill_to_py(py, f))
            .collect()
    }

    /// Collect each agent's permanent impact, one row per agent, name and
    /// tick its flow reached the market, and forget them.
    ///
    /// ``permanent`` is the change to the name's ``s`` the agent's fills
    /// made, in log units: exact under ``fill_impact_coefficient``, whose
    /// law is linear and additive, and the tick's flow impact shared pro
    /// rata by signed shares under the imbalance law. Recorded in the log.
    #[pyo3(signature = (agent = None))]
    fn take_impacts(&mut self, py: Python<'_>, agent: Option<String>) -> PyResult<Vec<PyObject>> {
        self.log.push(crate::python_log::LogEntry::TakeImpacts { agent: agent.clone() });
        self.inner
            .take_impacts(agent.as_deref())
            .iter()
            .map(|r| {
                let d = PyDict::new_bound(py);
                d.set_item("agent", &r.agent)?;
                d.set_item("ticker", &r.ticker)?;
                d.set_item("bought", r.bought)?;
                d.set_item("sold", r.sold)?;
                d.set_item("permanent", r.permanent)?;
                d.set_item("day", r.day)?;
                d.set_item("tick", r.tick)?;
                Ok(d.into())
            })
            .collect()
    }

    /// Whether an agent's order executes in this engine rather than being
    /// priced off a snapshot of the book: ``book_shared`` or
    /// ``book_resting`` is on. ``Portfolio.execute`` reads this.
    #[getter]
    fn book_live(&self) -> bool {
        self.inner.book_live()
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
    /// Embedder draws are in here, which is easy to overlook: they move the
    /// EXTERNAL stream, so a replay that skipped one would hand the embedder
    /// different values than the run it claims to reproduce, because the market
    /// itself no longer depends on them since the stream split.
    #[getter]
    fn order_log(&self, py: Python<'_>) -> PyResult<Vec<PyObject>> {
        self.log.iter().map(|e| e.to_py(py)).collect()
    }

    fn __repr__(&self) -> String {
        format!(
            "Engine({} instruments, draws={})",
            self.inner.instrument_count(),
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
/// "same universe, different market draws", the standard design for variance
/// estimation, is expressible. Generation draws from its own stream and
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
            short_interest: g.short_interest,
        })
        .collect())
}

/// The rate instruments this build prices, as instruments with their
/// default level, depth and size, in the order given.
///
/// `tickers` defaults to all of them. `tradefloor.bonds()` wraps this, and
/// `Universe.random(..., bonds=True)` appends the result. Every number comes
/// from `crate::rates::RATE_SPECS`, so the defaults have one home.
#[pyfunction]
#[pyo3(signature = (tickers = None))]
pub fn rate_instruments(tickers: Option<Vec<String>>) -> PyResult<Vec<PyInstrument>> {
    let wanted: Vec<String> = match tickers {
        Some(t) => t,
        None => crate::rates::tickers().iter().map(|t| t.to_string()).collect(),
    };
    let mut out = Vec::with_capacity(wanted.len());
    for ticker in &wanted {
        let spec = crate::rates::spec_for(ticker).ok_or_else(|| {
            ValidationError::new_err(format!(
                "{ticker:?} is not a rate instrument this build prices. Valid: {}",
                crate::rates::tickers().join(", ")
            ))
        })?;
        out.push(PyInstrument {
            ticker: spec.ticker.to_string(),
            sector: crate::rates::RATE_SECTOR.to_string(),
            initial_price: spec.initial_price,
            shares_outstanding: spec.units_outstanding,
            eps: None,
            book_value_per_share: None,
            revenue_growth: None,
            avg_volume: spec.avg_volume,
            beta: 0.0,
            short_interest: 0.0,
        });
    }
    Ok(out)
}

/// Each rate instrument's fixed terms, one dict per ticker: name, curve
/// point, duration, convexity, spread and the default depth and level.
#[pyfunction]
pub fn rate_specs(py: Python<'_>) -> PyResult<Vec<Py<PyDict>>> {
    let mut out = Vec::new();
    for spec in crate::rates::RATE_SPECS.iter() {
        let d = PyDict::new_bound(py);
        d.set_item("ticker", spec.ticker)?;
        d.set_item("name", spec.name)?;
        d.set_item("curve_point", spec.point.as_str())?;
        d.set_item("duration", spec.duration)?;
        d.set_item("convexity", spec.convexity)?;
        d.set_item("spread_bps", spec.spread_bps)?;
        d.set_item("avg_volume", spec.avg_volume)?;
        d.set_item("units_outstanding", spec.units_outstanding)?;
        d.set_item("initial_price", spec.initial_price)?;
        out.push(d.into());
    }
    Ok(out)
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
#[pyclass(name = "News", module = "tradefloor._core", frozen, get_all)]
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
#[pyclass(name = "NewsImpact", module = "tradefloor._core", frozen, get_all)]
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


/// The decomposition names, in reporting order.
///
/// An alias rather than a second list. Declared twice, the two orderings would
/// eventually disagree and every column would still look plausible -- the
/// `truth` schema is generated from the same constant for the same reason.
pub const FACTOR_NAMES: [&str; crate::market::factors::COMPONENT_COUNT] = [
    crate::market::factors::S_COMPONENT_KEYS[0],
    crate::market::factors::S_COMPONENT_KEYS[1],
    crate::market::factors::S_COMPONENT_KEYS[2],
    crate::market::factors::S_COMPONENT_KEYS[3],
    crate::market::factors::S_COMPONENT_KEYS[4],
    crate::market::factors::S_COMPONENT_KEYS[5],
    crate::market::factors::S_COMPONENT_KEYS[6],
    crate::market::factors::S_COMPONENT_KEYS[7],
    crate::market::factors::JUMP_COMPONENT_KEY,
    crate::market::factors::OVERNIGHT_COMPONENT_KEY,
];

/// Every field `column()` accepts, in one place.
///
/// Declared once so the error message cannot drift from the match arms above
/// it -- a list of valid names that omits a name it accepts is worse than no
/// list, because it sends the reader looking for a different mistake.
/// Index of `random_noise` in the engine's component order.
///
/// Found in the engine's own key list rather than written as a literal. Every
/// component is an f64, so a hard-coded index would keep compiling and start
/// feeding GARCH the crowd lean the day a component is inserted.
pub fn random_noise_index() -> usize {
    FACTOR_NAMES
        .iter()
        .position(|name| *name == "random_noise")
        .expect("random_noise is one of the components")
}

/// A rate instrument's state in a snapshot, in the order the state hash
/// walks it (`Engine::state_hash_with_pending`, `manifest.state_hash`).
pub const RATE_STATE_FIELDS: [&str; 14] = [
    "level", "marked_yield", "price", "previous_close", "open", "high", "low",
    "volume", "avg_volume", "units_outstanding", "maker_inventory", "day_carry",
    "day_duration", "day_convexity",
];

fn rate_state(inst: &crate::rates::RateInstrument) -> [f64; 14] {
    [
        inst.level, inst.marked_yield, inst.price, inst.previous_close, inst.open,
        inst.high, inst.low, inst.volume, inst.avg_volume, inst.units_outstanding,
        inst.maker_inventory, inst.day_carry, inst.day_duration, inst.day_convexity,
    ]
}

fn set_rate_state(inst: &mut crate::rates::RateInstrument, row: &[f64; 14]) {
    let [level, marked_yield, price, previous_close, open, high, low, volume, avg_volume,
         units_outstanding, maker_inventory, day_carry, day_duration, day_convexity] = *row;
    inst.level = level;
    inst.marked_yield = marked_yield;
    inst.price = price;
    inst.previous_close = previous_close;
    inst.open = open;
    inst.high = high;
    inst.low = low;
    inst.volume = volume;
    inst.avg_volume = avg_volume;
    inst.units_outstanding = units_outstanding;
    inst.maker_inventory = maker_inventory;
    inst.day_carry = day_carry;
    inst.day_duration = day_duration;
    inst.day_convexity = day_convexity;
}

/// The components `Engine.rate_attribution` reports.
pub const RATE_COMPONENTS: [&str; 4] = ["carry", "duration", "convexity", "flow"];

pub const COLUMN_FIELDS: [&str; 18] = [
    "price",
    "previous_close",
    "previous_tick_price",
    "open",
    "high",
    "low",
    "volume",
    "avg_volume",
    "market_cap",
    "mispricing_s",
    "mispricing_s_prev_close",
    "mispricing_momentum",
    "last_daily_return",
    "maker_inventory",
    "garch_variance",
    "beta",
    "short_interest",
    "float_shares",
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

/// The crisis epicentre's solve at an extra, as the numbers the tick uses.
///
/// `market_share` and `sector_share` are the two MEASURED constants
/// `market::factors::CRISIS_EPICENTRE_MARKET_SHARE` and
/// `CRISIS_EPICENTRE_SECTOR_SHARE`; `gain_up` and `gain_down` are the
/// multiples the epicentre sector's names and every other name carry on
/// their non-market parts while an episode runs; `extra_min` and `extra_max`
/// are the open interval `ModelParams::invariants` admits.
///
/// A window, not a dial: this computes nothing an engine does not compute
/// for itself, and it exists so a reader (and
/// `tests/test_crisis_epicentre.py`) can check the solve against its own two
/// equations with the engine's numbers rather than a transcription of them.
/// It takes the extra rather than a `ModelParams` because the solve reads
/// exactly one field and a params argument would suggest otherwise.
#[pyfunction]
pub fn crisis_epicentre_solve(py: Python<'_>, extra: f64) -> PyResult<Bound<'_, PyDict>> {
    let (up, down) = crate::market::factors::crisis_epicentre_gains(extra);
    let (lo, hi) = crate::market::factors::crisis_epicentre_extra_bounds();
    let out = PyDict::new_bound(py);
    out.set_item("extra", extra)?;
    out.set_item("market_share", crate::market::factors::CRISIS_EPICENTRE_MARKET_SHARE)?;
    out.set_item("sector_share", crate::market::factors::CRISIS_EPICENTRE_SECTOR_SHARE)?;
    out.set_item("gain_up", up)?;
    out.set_item("gain_down", down)?;
    out.set_item("extra_min", lo)?;
    out.set_item("extra_max", hi)?;
    Ok(out)
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
