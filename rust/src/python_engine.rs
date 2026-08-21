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
#[pyclass(name = "Instrument", module = "pretium", get_all)]
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
#[pyclass(name = "Macro", module = "pretium", get_all)]
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
#[pyclass(name = "TickResult", module = "pretium", frozen, get_all)]
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
#[pyclass(name = "Engine", module = "pretium")]
pub struct PyEngine {
    inner: Engine,
    buffer: SessionBuffer,
    tickers: Vec<String>,
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
        let economy = match macro_state {
            Some(m) => m.to_core(),
            None => PyMacro::new(15.0, 0.025, None, 0.02, 0.0, 50.0, "expansion")?.to_core(),
        };
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
        })
    }

    /// Roll the day's opening marks. Call once before the session's ticks.
    fn open_market(&mut self) {
        self.inner.open_market();
    }

    /// Advance one game-minute.
    ///
    /// A closed market costs nothing and draws nothing, which is why a caller
    /// may tick straight through a weekend without special-casing it.
    #[pyo3(signature = (hour, minute, day_of_week, *, volatility = 1.0))]
    fn tick(
        &mut self,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        volatility: f64,
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
        let outcome: TickOutcome = self.inner.tick(&TickRequest {
            time: GameTime {
                hour,
                minute,
                day_of_week,
            },
            volatility_multiplier: volatility,
            news: &[] as &[NewsEvent],
            news_impact_queue: &[] as &[NewsImpactEntry],
            order_volumes: &[] as &[(String, OrderVolume)],
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
    #[pyo3(signature = (hour, minute, day_of_week, ticks, *, volatility = 1.0, close_at_end = false))]
    fn run_session(
        &mut self,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        ticks: usize,
        volatility: f64,
        close_at_end: bool,
    ) -> PyResult<usize> {
        if ticks == 0 {
            return Err(ValidationError::new_err("ticks must be greater than zero"));
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

        let outcome = self.inner.run_session(
            &SessionRequest {
                start: GameTime {
                    hour,
                    minute,
                    day_of_week,
                },
                ticks,
                volatility_multiplier: volatility,
                news: &[],
                news_impact_queue: &[],
                order_volumes: &[],
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

    /// Run the close bookkeeping: GARCH update and the daily roll.
    fn close_market(&mut self) {
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
        self.inner.draw_uniform()
    }

    fn draw_normal(&mut self) -> f64 {
        self.inner.draw_normal()
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
