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
use crate::economy::{CyclePhase, InitialEconomyOptions};
use crate::engine::{Engine, PriceField, SessionBuffer, SessionRequest, TickRequest};
use crate::engine::{TickOutcome};
use crate::market::{GameTime, NewsEvent, NewsImpactEntry, OrderVolume, TickCompany};
use crate::python::ValidationError;

/// One recorded draw as `draw_log` returns it:
/// `((stream, kind, index), value, day, site, tag)`.
type DrawLogRow = ((String, String, u64), f64, i64, String, u32);

/// One order as `submit_many` parses it:
/// `(agent, position in the call, ticker, quantity, limit, id)`.
type ParsedOrder = (String, usize, String, f64, Option<f64>, Option<String>);

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
/// It is `price x shares_outstanding` by definition, and the spread tier is
/// selected from it. If the API accepted all three, a caller could pass an
/// inconsistent triple and a name's liquidity would disagree with its priced
/// value. So price and shares are the inputs, and market cap follows them as
/// price moves.
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
                "{}, or \"{}\" for a simulated rate index ({})",
                crate::python::unknown_sector(sector),
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

/// The layout version of the dict `Engine.state_snapshot` writes and
/// `Engine.restore_state` reads: the core's [`crate::snapshot::STATE_SCHEMA`].
pub use crate::snapshot::STATE_SCHEMA;

/// A population's engine spec, read from the object `population=` was
/// given: anything with `_engine_spec()` returning `{"fingerprint": str,
/// "participants": [dict, ...]}`, which is what `tradefloor.Population`
/// writes. Each participant dict carries `name`, `kind`, `size`, `rate`,
/// `interval`, `band` and its kind's own numbers.
fn population_from(
    obj: &Bound<'_, PyAny>,
) -> PyResult<(String, Vec<crate::population::Participant>)> {
    use crate::population::{Participant, Policy};
    let spec = obj.call_method0("_engine_spec").map_err(|_| {
        ValidationError::new_err(
            "population= takes a tradefloor.Population (for example \
             tf.Population.standard()), or None for an isolated engine.",
        )
    })?;
    let spec = spec
        .downcast_into::<PyDict>()
        .map_err(|_| ValidationError::new_err("Population._engine_spec() must return a dict"))?;
    fn get<'py>(d: &Bound<'py, PyDict>, k: &str) -> PyResult<Bound<'py, PyAny>> {
        d.get_item(k)?
            .ok_or_else(|| ValidationError::new_err(format!("population spec has no {k}")))
    }
    let fingerprint: String = get(&spec, "fingerprint")?.extract()?;
    let items = get(&spec, "participants")?;
    let mut out = Vec::new();
    for item in items.iter()? {
        let d = item?
            .downcast_into::<PyDict>()
            .map_err(|_| ValidationError::new_err("each population participant must be a dict"))?;
        let num = |k: &str| -> PyResult<f64> { get(&d, k)?.extract::<f64>() };
        let whole = |k: &str| -> PyResult<u32> {
            let v = num(k)?;
            if !(v >= 0.0 && v <= u32::MAX as f64 && v.fract() == 0.0) {
                return Err(ValidationError::new_err(format!(
                    "population participant field {k} must be a whole number, got {v}"
                )));
            }
            Ok(v as u32)
        };
        let kind: String = get(&d, "kind")?.extract()?;
        let policy = match kind.as_str() {
            "trend" => Policy::Trend { lookback: whole("lookback")?, scale: num("scale")? },
            "reversion" => Policy::Reversion { lookback: whole("lookback")?, scale: num("scale")? },
            "liquidity" => Policy::Liquidity {
                half_life: num("half_life")?,
                scale: num("scale")?,
                vix_calm: num("vix_calm")?,
                vix_stress: num("vix_stress")?,
            },
            "detector" => Policy::Detector {
                memory: num("memory")?,
                bucket: whole("bucket")?,
                lead: whole("lead")?,
                hold: whole("hold")?,
                max_spread: num("max_spread")?,
            },
            "crowd" => {
                let signal: String = get(&d, "signal")?.extract()?;
                let momentum = match signal.as_str() {
                    "momentum" => true,
                    "reversal" => false,
                    other => {
                        return Err(ValidationError::new_err(format!(
                            "a crowd's signal is \"momentum\" or \"reversal\", got {other:?}"
                        )))
                    }
                };
                Policy::Crowd {
                    momentum,
                    lookback: whole("lookback")?,
                    offset: whole("offset")?,
                    top_k: whole("top_k")?,
                    buffer: whole("buffer")?,
                    stop: num("stop")?,
                    recover: num("recover")?,
                }
            }
            other => {
                return Err(ValidationError::new_err(format!(
                    "unknown population participant kind {other:?}: trend, reversion, \
                     liquidity, detector or crowd"
                )))
            }
        };
        out.push(Participant {
            name: get(&d, "name")?.extract()?,
            size: num("size")?,
            rate: num("rate")?,
            interval: whole("interval")?,
            band: num("band")?,
            policy,
        });
    }
    Ok((fingerprint, out))
}

/// A snapshot field tree as the dict `Engine.state_snapshot` returns: each
/// value as the Python type it names, maps in field order.
fn snapshot_value_to_py(py: Python<'_>, value: &crate::snapshot::SnapshotValue) -> PyResult<PyObject> {
    use crate::snapshot::SnapshotValue as V;
    Ok(match value {
        V::None => py.None(),
        V::Bool(b) => b.into_py(py),
        V::Int(i) => i.into_py(py),
        V::UInt(u) => u.into_py(py),
        V::Float(f) => f.into_py(py),
        V::Str(s) => s.into_py(py),
        V::Bytes(b) => PyBytes::new_bound(py, b).into_any().unbind(),
        V::List(items) => {
            let items: PyResult<Vec<PyObject>> =
                items.iter().map(|v| snapshot_value_to_py(py, v)).collect();
            pyo3::types::PyList::new_bound(py, items?).into_any().unbind()
        }
        V::Tuple(items) => {
            let items: PyResult<Vec<PyObject>> =
                items.iter().map(|v| snapshot_value_to_py(py, v)).collect();
            pyo3::types::PyTuple::new_bound(py, items?).into_any().unbind()
        }
        V::Map(map) => snapshot_map_to_py(py, map)?.into_any(),
    })
}

fn snapshot_map_to_py(py: Python<'_>, map: &crate::snapshot::SnapshotMap) -> PyResult<Py<PyDict>> {
    let out = PyDict::new_bound(py);
    for (key, value) in map.iter() {
        out.set_item(key, snapshot_value_to_py(py, value)?)?;
    }
    Ok(out.unbind())
}

/// A dict handed to `Engine.restore_state` as the core's field tree, for
/// `Engine::restore` to check. Every value converts by its Python type, so
/// a value of the wrong type reaches the core's check, which names the
/// field; only a key that is not a string, or an object no snapshot field
/// can hold, is refused here. `prefix` is the dict's path in the snapshot,
/// empty at the top and `economy.` inside the economy.
fn snapshot_map_from_py(d: &Bound<'_, PyDict>, prefix: &str) -> PyResult<crate::snapshot::SnapshotMap> {
    let mut out = crate::snapshot::SnapshotMap::new();
    for (key, value) in d.iter() {
        let name: String = key.extract().map_err(|_| {
            let what = match prefix.strip_suffix('.') {
                Some(path) => format!("snapshot field {path}"),
                None => "this snapshot".to_string(),
            };
            ValidationError::new_err(format!("{what} has a key that is not a string: {key}"))
        })?;
        let value = snapshot_value_from_py(&value, &format!("{prefix}{name}"))?;
        out.insert(name, value);
    }
    Ok(out)
}

fn snapshot_value_from_py(v: &Bound<'_, PyAny>, at: &str) -> PyResult<crate::snapshot::SnapshotValue> {
    use crate::snapshot::SnapshotValue as V;
    use pyo3::types::{PyBool, PyFloat, PyList, PyLong, PyString, PyTuple};
    let items = |seq: &Bound<'_, PyAny>| -> PyResult<Vec<V>> {
        let mut out = Vec::new();
        for item in seq.iter()? {
            out.push(snapshot_value_from_py(&item?, &format!("{at}[]"))?);
        }
        Ok(out)
    };
    if v.is_none() {
        return Ok(V::None);
    }
    if v.is_instance_of::<PyBool>() {
        return Ok(V::Bool(v.extract()?));
    }
    if v.is_instance_of::<PyLong>() {
        if let Ok(i) = v.extract::<i64>() {
            return Ok(V::Int(i));
        }
        // A 64-bit key above i64::MAX.
        return v.extract::<u64>().map(V::UInt).map_err(|_| {
            ValidationError::new_err(format!(
                "snapshot field {at} holds {v}, an integer outside 64 bits"
            ))
        });
    }
    if v.is_instance_of::<PyFloat>() {
        return Ok(V::Float(v.extract()?));
    }
    if v.is_instance_of::<PyString>() {
        return Ok(V::Str(v.extract()?));
    }
    if let Ok(b) = v.downcast::<PyBytes>() {
        return Ok(V::Bytes(b.as_bytes().to_vec()));
    }
    if let Ok(d) = v.downcast::<PyDict>() {
        return Ok(V::Map(snapshot_map_from_py(d, &format!("{at}."))?));
    }
    if v.is_instance_of::<PyList>() {
        return Ok(V::List(items(v)?));
    }
    if v.is_instance_of::<PyTuple>() {
        return Ok(V::Tuple(items(v)?));
    }
    // A bool, number or sequence of another type (a numpy scalar, say), read
    // as pyo3 reads one, and any other sequence as a list.
    if let Ok(b) = v.extract::<bool>() {
        return Ok(V::Bool(b));
    }
    if let Ok(i) = v.extract::<i64>() {
        return Ok(V::Int(i));
    }
    // A numpy uint64 above i64::MAX, as a Python int there would be.
    if let Ok(u) = v.extract::<u64>() {
        return Ok(V::UInt(u));
    }
    if let Ok(f) = v.extract::<f64>() {
        return Ok(V::Float(f));
    }
    // pyo3's own sequence read, which is what a list field accepted.
    if let Ok(seq) = v.extract::<Vec<Bound<'_, PyAny>>>() {
        let mut out = Vec::with_capacity(seq.len());
        for item in &seq {
            out.push(snapshot_value_from_py(item, &format!("{at}[]"))?);
        }
        return Ok(V::List(out));
    }
    Err(ValidationError::new_err(format!(
        "snapshot field {at} holds a {}, which no snapshot field is",
        v.get_type().name()?
    )))
}

#[pymethods]
impl PyMacro {
    #[new]
    #[pyo3(signature = (
        *, vix = 15.0, federal_funds_rate = 0.025, corporate_bond_yield = None,
        inflation_rate = 0.02, qe_pe_boost = 0.0, qe_assets_ratio = 1.0, fear_greed_index = 50.0,
        cycle = "expansion"
    ))]
    #[allow(clippy::too_many_arguments)]
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
        check_vix(vix)?;
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
    ///
    /// `volume_base` is each instrument's running volume total just before
    /// the session's first tick. The tape's `volumes` are running totals, so
    /// a bar's own volume is its last tick's total minus the total before its
    /// first, and for the first tick on a tape that earlier total is not on
    /// the tape. It is zero after an open, which resets the count, and not
    /// zero for a later session of the same day, or for a session run on a
    /// restored engine that never opened.
    fn accumulate_session(&mut self, volume_base: Vec<f64>) {
        self.session_volume_base = volume_base;
        let ticks = self.buffer.ticks_written;
        if ticks == 0 {
            return;
        }
        if self.day_buffer.ticks == 0 {
            self.day_buffer.volume_base = self.session_volume_base.clone();
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
        self.day_buffer
            .repriced
            .extend_from_slice(&self.buffer.repriced[..n]);
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
        // The fair-value shift: the tick's own, then on the first row the
        // close's jump's, beside the jump (below), so the columns sum to the
        // change in `s` on that row too.
        let fv = crate::market::factors::FAIR_VALUE_SLOT;
        let first = self.day_buffer.components[fv].len();
        self.day_buffer.components[fv]
            .extend_from_slice(&self.buffer.components[crate::market::factors::TICK_FAIR_VALUE][..n]);
        self.day_buffer.components[fv].resize(self.day_buffer.components[0].len(), 0.0);
        // Each pending vector is one entry per name, and the first row is
        // `width` names wide. An entry past it would land on the second
        // tick's first name, so it is dropped rather than misfiled.
        let width = self.buffer.companies;
        if !self.pending_fair_value.is_empty() {
            for (i, v) in self.pending_fair_value.iter().enumerate().take(width) {
                if let Some(slot) = self.day_buffer.components[fv].get_mut(first + i) {
                    *slot += v;
                }
            }
            self.pending_fair_value.clear();
        }
        // The eighth series is the daily jump. It happens at the close, so
        // no tick carries it, and the row where its effect is OBSERVED is the
        // first tick of the next day: `s` there already includes it. Zeroed
        // here and filled from the pending value on that first row, so the
        // columns sum to the change in `s` tick by tick (§74).
        let before = self.day_buffer.components[crate::market::factors::JUMP_SLOT].len();
        self.day_buffer.components[crate::market::factors::JUMP_SLOT].resize(self.day_buffer.components[0].len(), 0.0);
        if !self.pending_jump.is_empty() {
            for (i, v) in self.pending_jump.iter().enumerate().take(width) {
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
            for (i, v) in self.pending_overnight.iter().enumerate().take(width) {
                if let Some(slot) = self.day_buffer.components[crate::market::factors::OVERNIGHT_SLOT].get_mut(before + i) {
                    *slot += v;
                }
            }
            self.pending_overnight.clear();
        }
        // The twelfth is the ex-date's move in `s`, which happens at the
        // open like the overnight move and is booked the same way.
        let before = self.day_buffer.components[crate::market::factors::DIVIDEND_SLOT].len();
        self.day_buffer.components[crate::market::factors::DIVIDEND_SLOT].resize(self.day_buffer.components[0].len(), 0.0);
        if !self.pending_dividend.is_empty() {
            for (i, v) in self.pending_dividend.iter().enumerate().take(width) {
                if let Some(slot) = self.day_buffer.components[crate::market::factors::DIVIDEND_SLOT].get_mut(before + i) {
                    *slot += v;
                }
            }
            self.pending_dividend.clear();
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
        let shifts: Vec<f64> = self.inner.jump_fair_value_moves().to_vec();
        if shifts.iter().any(|v| *v != 0.0) {
            self.pending_fair_value = shifts;
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

    /// Today's ex-date amounts across every instrument, zero on a rate
    /// index and on every model without dividends.
    fn dividends_padded(&self) -> Vec<f64> {
        self.padded(self.inner.dividends_today(), 0.0)
    }

    /// [`Self::dividends_padded`] for the tape: EMPTY on a model without
    /// dividends, whose `prints` carry no `distribution` column.
    fn distribution_row(&self) -> Vec<f64> {
        if self.inner.carries_dividends() {
            self.dividends_padded()
        } else {
            Vec::new()
        }
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
                "{}. For a modified model, pass ModelParams.from_preset(name, ...) \
                 instead of a string.",
                crate::python::unknown_preset(&name)
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

/// Refuse a VIX below zero. The rates beside it are range-checked by
/// `units::check_rate`, and a VIX is an index level that cannot be negative.
/// There is no upper bound here: a scenario may pin a level above anything
/// recorded, and the daily step clamps the VIX into its band at the next
/// close.
fn check_vix(v: f64) -> PyResult<()> {
    if v < 0.0 {
        return Err(ValidationError::new_err(format!(
            "vix cannot be negative, got {v}. It is the index level, such as 20."
        )));
    }
    Ok(())
}

/// A universe argument as instruments, or a refusal that says what came
/// instead.
///
/// pyo3's own words for a wrong type named its internals: `None` or a
/// number read "'int' object is not iterable", and a list of ticker strings
/// "'str' object cannot be converted to 'Instrument'". The Python side
/// (`tradefloor.universe_util.as_universe`) refuses the same inputs in the
/// same words, since `evaluate` reads the roster before any engine does.
pub(crate) fn universe_from(value: &Bound<'_, PyAny>) -> PyResult<Vec<PyInstrument>> {
    if !value.is_instance_of::<pyo3::types::PyString>() {
        if let Ok(instruments) = value.extract::<Vec<PyInstrument>>() {
            return Ok(instruments);
        }
    }
    Err(ValidationError::new_err(format!(
        "universe must be a list of instruments, such as \
         tf.Universe.random(40, seed=1). {}",
        universe_got(value)
    )))
}

/// The "Got ..." half of [`universe_from`]'s refusal.
fn universe_got(value: &Bound<'_, PyAny>) -> String {
    if value.is_none() {
        return "Got None.".to_string();
    }
    if value.is_instance_of::<pyo3::types::PyBool>() {
        return format!("Got {}.", value);
    }
    if let Ok(n) = value.extract::<i64>() {
        return format!(
            "Got the number {n}. For {n} random companies use \
             tf.Universe.random({n}, seed=1)."
        );
    }
    if value.is_instance_of::<pyo3::types::PyString>() {
        return format!(
            "Got the string {}. Tickers alone are not enough: the simulator \
             needs each company's price, shares and sector.",
            value.repr().map(|r| r.to_string()).unwrap_or_default()
        );
    }
    let kind = value
        .get_type()
        .name()
        .map(|n| n.to_string())
        .unwrap_or_else(|_| "value".to_string());
    let Ok(items) = value.iter() else {
        return format!("Got a {kind}.");
    };
    let items: Vec<Bound<'_, PyAny>> = items.filter_map(Result::ok).collect();
    if !items.is_empty() && items.iter().all(|i| i.is_instance_of::<pyo3::types::PyString>()) {
        return "Got a list of ticker strings; the simulator needs each \
                company's price, shares and sector, not only its name. \
                Universe.from_edgar builds instruments for real companies."
            .to_string();
    }
    match items
        .iter()
        .position(|i| i.extract::<PyRef<'_, PyInstrument>>().is_err())
    {
        Some(at) => format!(
            "Got a {kind} holding a {} at position {at}.",
            items[at]
                .get_type()
                .name()
                .map(|n| n.to_string())
                .unwrap_or_else(|_| "value".to_string())
        ),
        None => format!("Got a {kind}."),
    }
}

/// The refusal for a ticker listed twice in one roster.
fn duplicate_ticker(ticker: &str, first: usize, second: usize) -> String {
    format!(
        "ticker {ticker:?} appears twice in the universe (positions {first} and \
         {second}). Each ticker must be unique: orders and books find a name by \
         its ticker, so the second one could never be traded."
    )
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
    for (position, inst) in universe.iter().enumerate() {
        // Orders, books and every by-ticker lookup find the first name with
        // a ticker, so a second one could be priced and never traded or
        // read. Refused here, where the engine is built, and by
        // `list_instrument` for a name listed later.
        if !inst.is_rate() {
            if let Some(first) = universe[..position].iter().position(|e| e.ticker == inst.ticker) {
                return Err(ValidationError::new_err(duplicate_ticker(
                    &inst.ticker,
                    first,
                    position,
                )));
            }
        }
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

/// The clock and volatility `tick` takes: a time of day, a day of the week
/// from 0 (Sunday) to 6, and a volatility multiplier that is finite and not
/// negative.
fn check_clock(hour: i64, minute: i64, day_of_week: i64, volatility: f64) -> PyResult<()> {
    if !(0..24).contains(&hour) || !(0..60).contains(&minute) {
        return Err(ValidationError::new_err(format!(
            "invalid time {hour:02}:{minute:02}"
        )));
    }
    check_day_and_volatility(day_of_week, volatility)
}

/// The start a session entry point (`run_session`, `run_days`, `run_until`)
/// takes. Looser than [`check_clock`] in one way: the minute may carry into
/// the hour, as the session's own clock does after each tick, so 09:60 is
/// 10:00. Callers have always passed `30 + i * 30` minutes, and they still
/// can. The start must fall inside the day, the minute may not be negative,
/// and the day and volatility are checked as `tick` checks them.
fn check_session_start(
    hour: i64,
    minute: i64,
    day_of_week: i64,
    volatility: f64,
) -> PyResult<()> {
    let within_day = hour
        .checked_mul(60)
        .and_then(|h| h.checked_add(minute))
        .is_some_and(|m| (0..24 * 60).contains(&m));
    if hour < 0 || minute < 0 || !within_day {
        return Err(ValidationError::new_err(format!(
            "invalid session start {hour:02}:{minute:02}: it must fall \
             between 00:00 and 23:59 (the minute may carry into the hour)"
        )));
    }
    check_day_and_volatility(day_of_week, volatility)
}

fn check_day_and_volatility(day_of_week: i64, volatility: f64) -> PyResult<()> {
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
    Ok(())
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
    crate::snapshot::parse_column(name).ok_or_else(|| {
        ValidationError::new_err(format!(
            "unknown field {name:?}. Valid: {}",
            COLUMN_FIELDS.join(", ")
        ))
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
    /// Running totals since the open, as the session buffer writes them.
    volumes: Vec<f64>,
    /// Each instrument's running total before the first tick on this tape.
    /// See [`PyEngine::accumulate_session`].
    volume_base: Vec<f64>,
    mispricing: Vec<f64>,
    fundamental: Vec<f64>,
    anchor: Vec<f64>,
    components: [Vec<f64>; crate::market::factors::COMPONENT_COUNT],
    shock: Vec<f64>,
    absorbed: Vec<f64>,
    clamp: Vec<f64>,
    repriced: Vec<f64>,
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
        self.volume_base.clear();
        self.mispricing.clear();
        self.fundamental.clear();
        self.anchor.clear();
        self.shock.clear();
        self.absorbed.clear();
        self.clamp.clear();
        self.repriced.clear();
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

/// How many times Python code has copied this engine or put a copy back:
/// calls to `fork`, `state_snapshot` and `restore_state`.
///
/// Read by `tradefloor.sandbox.TamperGuard` around agent code. A fork run
/// ahead is look-ahead, and it writes nothing to the engine it came from, so
/// the state-hash comparison cannot see it; this count can, however the
/// agent reached the engine. Not market state: the hash, the snapshot and
/// the log leave it out, and a copy starts its own count at zero, so no
/// digest moves. Atomic so the engine stays `Sync` for a later PyO3.
#[derive(Default)]
struct CopyCount(std::sync::atomic::AtomicU64);

impl CopyCount {
    fn bump(&self) {
        self.0.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    }

    fn get(&self) -> u64 {
        self.0.load(std::sync::atomic::Ordering::Relaxed)
    }

    fn set(&self, value: u64) {
        self.0.store(value, std::sync::atomic::Ordering::Relaxed);
    }
}

impl Clone for CopyCount {
    fn clone(&self) -> Self {
        CopyCount::default()
    }
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
    /// The close's jump's `fair_value_shift`, waiting for the row where the
    /// jump is observed, as `pending_jump` waits. Empty unless the preset
    /// carries fair-value offsets and a name's jump moved one.
    pending_fair_value: Vec<f64>,
    /// The ex-date's move in `s` at the last open, waiting for the day's
    /// first tape row; empty unless a name went ex (`dividend_payout_share`).
    pending_dividend: Vec<f64>,
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
    /// Each instrument's running volume total before the LAST session's
    /// first tick: what the un-recorded `bars()` fallback, which reads that
    /// session alone, subtracts from its first tick.
    session_volume_base: Vec<f64>,
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
    /// Where this day's last `run_session` left the clock, so a later
    /// session that starts before it can warn. Read by nothing else: the
    /// tick, the snapshot and the hash leave it out, so it cannot change a
    /// run. `None` until the day's first session, which never warns.
    session_clock: Option<SessionClock>,
    /// Copies taken of this engine; see [`CopyCount`].
    copies: CopyCount,
    /// The log length at the last `restore_state`. What came before it is
    /// another history, so [`Self::day_is_open`] reads the log from here.
    /// Bookkeeping for a refusal only: nothing reads it that the tick, the
    /// snapshot or the hash sees.
    restored_at: usize,
}

/// Minutes in a day, the modulus `harness.session_clock` wraps the clock at.
const MINUTES_PER_DAY: i64 = 24 * 60;

/// The clock one day's sessions have reached.
///
/// `end` counts minutes from the midnight before the day's first session,
/// and it keeps counting past 1,440 instead of wrapping. `harness.
/// session_clock` wraps a long day's later steps back to 00:00 and keeps the
/// day of week, so a session at 00:05 straight after one that ran to 00:05
/// continues the day. The unwrapped count is what tells that apart from a
/// session that went back to 09:30.
#[derive(Clone, Copy, Debug)]
struct SessionClock {
    day_of_week: i64,
    end: i64,
}

impl SessionClock {
    /// Where a session starting at `start` (minutes after midnight) sits on
    /// this day's unwrapped clock: in the same 24 hours as `end`.
    fn place(&self, start: i64) -> i64 {
        start + self.end.div_euclid(MINUTES_PER_DAY) * MINUTES_PER_DAY
    }
}

/// The warning `run_session` raises when a session starts before the day's
/// previous one ended.
fn repeated_clock_warning(start: i64, end: i64) -> String {
    let at = |m: i64| {
        let m = m.rem_euclid(MINUTES_PER_DAY);
        format!("{:02}:{:02}", m / 60, m % 60)
    };
    format!(
        "run_session started at {} but this day's previous session ran to {}. \
         The time of day sets the intraday activity profile, so those minutes \
         run a second time and the day becomes a different market from one \
         continuous session. Start each session where the last one ended. \
         tradefloor.harness.session_clock(start, step, ticks_per_step) returns \
         that start, or pass (9 + m // 60, m % 60) as hour and minute, where m \
         is 30 plus the ticks already run today.",
        at(start),
        at(end),
    )
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
    /// What this binding keeps beside the core and a snapshot carries: the
    /// day counter, the open flag and the tape rows in waiting.
    fn day_loop(&self) -> crate::snapshot::DayLoop {
        let mut day_loop = crate::snapshot::DayLoop::new(self.day_count, self.market_open);
        day_loop.pending_jump = self.pending_jump.clone();
        day_loop.pending_overnight = self.pending_overnight.clone();
        day_loop.pending_fair_value = self.pending_fair_value.clone();
        day_loop.pending_dividend = self.pending_dividend.clone();
        day_loop
    }

    /// `state_snapshot` for this binding's own reads, which the copy count
    /// does not see: the ledger's snapshots in `run_days` and `economy`.
    fn snapshot_uncounted(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let before = self.copies.get();
        let out = self.state_snapshot(py);
        self.copies.set(before);
        out
    }

    /// Whether a day is open and has not closed, the state in which a
    /// second open would reopen it.
    ///
    /// Not `market_open` alone. `run_session(close_at_end=True)` closes the
    /// day in the core and leaves that flag set (see `state_hash`), and
    /// `open_market()` after it is the documented way on to the next day. So
    /// the day counts as open only when the log since its `open_market`
    /// holds no session that closed it. With no open in the log since the
    /// last restore, the state came from a snapshot this engine cannot read
    /// back, and the answer is no, which is what every call did before 0.8.5.
    fn day_is_open(&self) -> bool {
        if !self.market_open {
            return false;
        }
        for entry in self.log.get(self.restored_at..).unwrap_or(&[]).iter().rev() {
            match entry {
                crate::python_log::LogEntry::OpenMarket { .. } => return true,
                crate::python_log::LogEntry::CloseMarket => return false,
                crate::python_log::LogEntry::RunSession { close_at_end, .. }
                    if *close_at_end =>
                {
                    return false
                }
                _ => {}
            }
        }
        false
    }

    /// What a call that needs a closed market says when a day is open: which
    /// day, how far it has run, and `then`, the way out for that call.
    fn open_day_refusal(&self, then: &str) -> String {
        let day = self.inner.current_day();
        let ticks = self.day_buffer.ticks;
        let run = if ticks == 0 {
            "is open".to_string()
        } else if ticks == 1 {
            "has run 1 tick".to_string()
        } else {
            format!("has run {ticks} ticks")
        };
        format!("the market is already open: day {day} {run} and has not closed. {then}")
    }

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
        // The label goes into the log only when it is not the counter's, so
        // a replay numbers the day as this run did and every log of a run
        // that never passed `first_day` is the one it was.
        self.log.push(crate::python_log::LogEntry::OpenMarket {
            day: (day != i64::from(self.day_count)).then_some(day),
        });
        // A new day's tape starts here. Without this, a run that never closed
        // would grow one unbounded "day".
        self.day_buffer.clear();
        // And a new day's clock, so its first session never warns.
        self.session_clock = None;
        self.market_open = true;
        // The day a draw carries in the draw log is the day whose open it
        // follows, so the jumps, volume and macro draws taken at a close
        // belong to the day they close rather than to the one after.
        //
        // Two numbers, set apart. `day` is a LABEL: the draw log, the day
        // mark and the book's fill stamps carry it. The valuation's clock is
        // the days this engine has run, `day_count`, whatever the label. They
        // were one field until 0.8.5, so `run_days(30, first_day=1000)`
        // priced every name as though a thousand days had passed.
        self.inner.set_day_label(day);
        self.inner.set_elapsed_days(i64::from(self.day_count));
        self.inner.open_market();
        // The overnight move the open applied to `s`, for the tape: booked
        // onto the day's first row, where its effect is observed, as the
        // close's jump is booked onto the next day's (§74).
        let nights: Vec<f64> = self.inner.overnight_moves().to_vec();
        if nights.iter().any(|v| *v != 0.0) {
            self.pending_overnight = nights;
        }
        // And what the night's permanent share took from `s` into fair
        // value, onto the same row's `fair_value_shift`, added to whatever
        // the last close's jump left pending there.
        let shifts = self.inner.overnight_fair_value_moves();
        if shifts.iter().any(|v| *v != 0.0) {
            if self.pending_fair_value.len() < shifts.len() {
                self.pending_fair_value.resize(shifts.len(), 0.0);
            }
            for (slot, v) in self.pending_fair_value.iter_mut().zip(shifts) {
                *slot += v;
            }
        }
        // The ex-date's move in `s`, booked onto the same row.
        if self.inner.carries_dividends() {
            let moves: Vec<f64> = self.inner.dividend_moves().to_vec();
            if moves.iter().any(|v| *v != 0.0) {
                self.pending_dividend = moves;
            }
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
            .rposition(|e| matches!(e, crate::python_log::LogEntry::OpenMarket { .. }))
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
        // A contract symbol goes to the core, which lists the contracts
        // (`futures_index_listed`, `futures_vix_listed`) and says when one is
        // not listed.
        let p = self.inner.params();
        let contract = (p.futures_index_listed != 0.0
            || p.futures_vix_listed != 0.0
            || p.futures_rates_listed != 0.0
            || p.futures_oil_listed != 0.0)
            && crate::derivatives::ContractSymbol::parse(ticker).is_ok();
        if !self.tickers.iter().any(|t| t == ticker) && !contract {
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

#[pymethods]
impl PyEngine {
    /// Build an engine over a universe.
    ///
    /// `seed` is required and has no default. A simulator that seeds itself
    /// from the clock when you forget produces a run nobody can reproduce,
    /// and nobody notices until they try. It is any integer from 0 to
    /// `2**64 - 1`. Every seed below `2**32` is the market it was when seeds
    /// were 32-bit, and `rust/src/rng.rs` states how a wider one is derived.
    ///
    /// `model` selects the coefficient set: a shipped preset's name
    /// (`"pt-v1"`, the default) or a `ModelParams`. Overriding coefficients
    /// takes the extra step on purpose, and the fingerprint means an
    /// overridden run can never pass as the benchmark model (API §3).
    ///
    /// Keyword arguments only. `*args` is taken so that `Engine(7, universe)`
    /// is refused in words that show the call, rather than as pyo3's
    /// "takes 0 positional arguments but 2 were given". `seed` and
    /// `universe` then need defaults, and [`crate::python::Given`] keeps
    /// "left out" (a `TypeError` that shows the call) apart from a value
    /// that is wrong, `None` included (a `ValidationError` naming it).
    #[new]
    #[pyo3(
        signature = (
            *args,
            seed = crate::python::Given::Missing,
            universe = crate::python::Given::Missing,
            macro_state = None,
            model = None,
            population = None
        ),
        text_signature = "(*, seed, universe, macro_state=None, model=None, population=None)"
    )]
    fn new(
        py: Python<'_>,
        args: &Bound<'_, pyo3::types::PyTuple>,
        seed: crate::python::Given<'_>,
        universe: crate::python::Given<'_>,
        macro_state: Option<PyMacro>,
        model: Option<&Bound<'_, PyAny>>,
        population: Option<&Bound<'_, PyAny>>,
    ) -> PyResult<Self> {
        if !args.is_empty() {
            return Err(pyo3::exceptions::PyTypeError::new_err(format!(
                "Engine takes keyword arguments: Engine(seed=7, universe=universe). \
                 It was given {} positional argument{}.",
                args.len(),
                if args.len() == 1 { "" } else { "s" }
            )));
        }
        let seed = match seed {
            crate::python::Given::Value(value) => {
                crate::python::Seed(crate::python::seed_from(&value, "seed")?)
            }
            crate::python::Given::Missing => {
                return Err(pyo3::exceptions::PyTypeError::new_err(
                    "Engine needs a seed: Engine(seed=7, universe=universe). There is \
                     no default, because a run seeded from the clock cannot be \
                     reproduced.",
                ))
            }
        };
        let universe = match universe {
            crate::python::Given::Value(value) => universe_from(&value)?,
            crate::python::Given::Missing => {
                return Err(pyo3::exceptions::PyTypeError::new_err(
                    "Engine needs a universe: Engine(seed=7, \
                     universe=tf.Universe.random(40, seed=1)).",
                ))
            }
        };
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

        // The GIL is released for the build, as it is for a session
        // (`run_session`), and for the same reason: on pt-v21 the build plays
        // a 504-session prehistory, about 1.5 s over 20 names, and holding
        // the GIL through it ran a threaded sweep's builds one at a time and
        // stopped every other thread of the host. Nothing Python is touched
        // inside: every argument is a Rust value by now.
        let sector_keys: Vec<String> =
            crate::sectors::keys().iter().map(|s| s.to_string()).collect();
        let inner = py.allow_threads(move || {
            Engine::with_params_from_opening(
                seed.0,
                companies,
                economy,
                create_initial_central_bank_state(0),
                sector_keys,
                params,
                settle_opening,
            )
        });
        let mut engine = Self {
            inner,
            buffer: SessionBuffer::new(),
            pending_jump: Vec::new(),
            pending_overnight: Vec::new(),
            pending_fair_value: Vec::new(),
            pending_dividend: Vec::new(),
            day_buffer: DayBuffer::default(),
            session_volume_base: Vec::new(),
            market_open: false,
            day_count: 0,
            tickers,
            recorded: Vec::new(),
            recorded_macro: Vec::new(),
            recorded_book: Vec::new(),
            log: Vec::new(),
            explanations: Explanations::default(),
            session_clock: None,
            copies: CopyCount::default(),
            restored_at: 0,
        };
        // After construction, so a settled opening has run its burn-in and
        // the indices are marked at the curve the run actually starts from.
        if !rates.is_empty() {
            engine
                .inner
                .set_rate_instruments(rates.iter().map(|i| i.to_rate()).collect());
        }
        // Last, so it starts from the market the run actually opens on.
        if let Some(population) = population {
            if !population.is_none() {
                let (fingerprint, participants) = population_from(population)?;
                engine
                    .inner
                    .set_population(fingerprint, participants)
                    .map_err(ValidationError::new_err)?;
            }
        }
        Ok(engine)
    }

    /// The population this engine was built with, as the data
    /// `tradefloor.Population` was built from: `{"fingerprint": ...,
    /// "participants": [dict, ...]}`, or None for an isolated engine. A
    /// manifest carries it so a replay rebuilds the same population.
    fn population_spec(&self, py: Python<'_>) -> PyResult<Option<Py<PyDict>>> {
        use crate::population::Policy;
        let Some(pop) = self.inner.population() else {
            return Ok(None);
        };
        let out = PyDict::new_bound(py);
        out.set_item("fingerprint", pop.fingerprint.clone())?;
        let items = pyo3::types::PyList::empty_bound(py);
        for p in &pop.participants {
            let d = PyDict::new_bound(py);
            d.set_item("kind", p.policy.kind())?;
            d.set_item("name", p.name.clone())?;
            d.set_item("size", p.size)?;
            d.set_item("rate", p.rate)?;
            d.set_item("interval", p.interval)?;
            d.set_item("band", p.band)?;
            match &p.policy {
                Policy::Trend { lookback, scale } | Policy::Reversion { lookback, scale } => {
                    d.set_item("lookback", *lookback)?;
                    d.set_item("scale", *scale)?;
                }
                Policy::Liquidity { half_life, scale, vix_calm, vix_stress } => {
                    d.set_item("half_life", *half_life)?;
                    d.set_item("scale", *scale)?;
                    d.set_item("vix_calm", *vix_calm)?;
                    d.set_item("vix_stress", *vix_stress)?;
                }
                Policy::Detector { memory, bucket, lead, hold, max_spread } => {
                    d.set_item("memory", *memory)?;
                    d.set_item("bucket", *bucket)?;
                    d.set_item("lead", *lead)?;
                    d.set_item("hold", *hold)?;
                    d.set_item("max_spread", *max_spread)?;
                }
                Policy::Crowd { momentum, lookback, offset, top_k, buffer, stop, recover } => {
                    d.set_item("signal", if *momentum { "momentum" } else { "reversal" })?;
                    d.set_item("lookback", *lookback)?;
                    d.set_item("offset", *offset)?;
                    d.set_item("top_k", *top_k)?;
                    d.set_item("buffer", *buffer)?;
                    d.set_item("stop", *stop)?;
                    d.set_item("recover", *recover)?;
                }
            }
            items.append(d)?;
        }
        out.set_item("participants", items)?;
        Ok(Some(out.into()))
    }

    /// The fingerprint of the population this engine was built with, or
    /// None for an isolated engine.
    #[getter]
    fn population_fingerprint(&self) -> Option<String> {
        self.inner.population().map(|p| p.fingerprint.clone())
    }

    /// The population's ledger, one dict per participant: its `name`,
    /// `kind` and `label`, and per name (keyed by ticker) its `position` in
    /// shares, `cash`, `volume` (shares traded), `notional` (dollars
    /// traded) and `pnl` (cash plus the position at the last print); then
    /// the totals `pnl`, `volume`, `notional` and `orders`. A crowd's row
    /// also carries its `signal`, its `exposure` (the share of its full book
    /// it holds: 1 until a loss limit sells it out, then rebuilding), its
    /// `stops` (how many times it has sold out) and its `price_pnl` (what
    /// its positions made between its decisions, before what it paid to
    /// trade). An empty list on an engine without a population.
    fn population_report(&self, py: Python<'_>) -> PyResult<Py<pyo3::types::PyList>> {
        let out = pyo3::types::PyList::empty_bound(py);
        let Some(pop) = self.inner.population() else {
            return Ok(out.into());
        };
        let companies = self.inner.companies();
        for (k, p) in pop.participants.iter().enumerate() {
            let s = &pop.states[k];
            let row = PyDict::new_bound(py);
            row.set_item("name", p.name.clone())?;
            row.set_item("kind", p.policy.kind())?;
            row.set_item("label", p.label())?;
            let names = PyDict::new_bound(py);
            let (mut pnl, mut volume, mut notional) = (0.0, 0.0, 0.0);
            for (i, t) in pop.tickers.iter().enumerate() {
                let price = companies
                    .iter()
                    .find(|c| &c.ticker == t)
                    .map(|c| c.stock.price)
                    .unwrap_or(f64::NAN);
                let mark = s.cash[i] + if s.position[i] == 0.0 { 0.0 } else { s.position[i] * price };
                let cell = PyDict::new_bound(py);
                cell.set_item("position", s.position[i])?;
                cell.set_item("cash", s.cash[i])?;
                cell.set_item("volume", s.volume[i])?;
                cell.set_item("notional", s.notional[i])?;
                cell.set_item("pnl", mark)?;
                names.set_item(t, cell)?;
                pnl += mark;
                volume += s.volume[i];
                notional += s.notional[i];
            }
            row.set_item("names", names)?;
            row.set_item("pnl", pnl)?;
            row.set_item("volume", volume)?;
            row.set_item("notional", notional)?;
            row.set_item("orders", s.orders)?;
            if let [exposure, _, stops, price_pnl] = s.crowd[..] {
                if let crate::population::Policy::Crowd { momentum, .. } = p.policy {
                    row.set_item("signal", if momentum { "momentum" } else { "reversal" })?;
                }
                row.set_item("exposure", exposure)?;
                row.set_item("stops", stops)?;
                row.set_item("price_pnl", price_pnl)?;
            }
            out.append(row)?;
        }
        Ok(out.into())
    }

    /// Roll the day's opening marks. Call once before the session's ticks.
    ///
    /// Numbers the day from the engine's own counter, or `day` when given,
    /// the way `run_days(first_day=...)` does. The number is a LABEL. The
    /// draw log, the day marks and the book's fill stamps carry it, and
    /// nothing that prices reads it. The valuation counts the days this
    /// engine has run, so a label cannot reprice the market. A label that
    /// is not the counter's goes into the order log, and a replay opens the
    /// day under it.
    ///
    /// Refused while a day is open. Until 0.8.5 a second call reopened the
    /// day: it cleared the day's tape, logged a second open and changed the
    /// state hash, so the run no longer matched one that closed first, with
    /// no warning. Close the day with `close_market()` first. A day
    /// closed by `run_session(close_at_end=True)` is closed, and opening the
    /// next one after it works as it always did.
    #[pyo3(signature = (*, day = None))]
    fn open_market(&mut self, day: Option<i64>) -> PyResult<()> {
        if self.day_is_open() {
            return Err(ValidationError::new_err(self.open_day_refusal(
                "Call close_market() to end it before opening the next day.",
            )));
        }
        let day = match day {
            None => i64::from(self.day_count),
            Some(d) if d < 0 => {
                return Err(ValidationError::new_err(format!(
                    "day must be 0 or more, got {d}. It numbers the day for the \
                     draw log and the fills, and days count from 0."
                )))
            }
            Some(d) => d,
        };
        self.open_market_on(day);
        Ok(())
    }

    /// Advance one game-minute.
    ///
    /// A closed market costs nothing and draws nothing, so a caller can tick
    /// straight through a weekend without special-casing it.
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
        check_clock(hour, minute, day_of_week, volatility)?;
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
    /// Running 390 ticks a day through per-call marshalling is 390 boundary
    /// crossings, and the hot loop belongs in Rust. The results are identical
    /// to calling `tick` in a loop, and a test asserts it.
    ///
    /// Returns the number of ticks written.
    ///
    /// # Two kinds of order flow, and which one an agent's trades are
    ///
    /// `fills` is what a trader filled at the step boundary just before this
    /// session, `{ticker: (bought, sold)}` in shares, which is what
    /// `Portfolio.pending_flow()` returns. It reaches the market ONCE, on the
    /// session's first tick, whatever `ticks` is. An agent loop passes its
    /// fills here.
    ///
    /// `flow_per_tick` is a standing rate: that many shares bought
    /// and sold on EVERY tick of the session, a program that trades all
    /// session long. `tf.flow_impact` uses it. Handing an agent's fills to
    /// it counts one order once a minute for the whole step.
    ///
    /// `order_flow` is refused here since 0.8.5, because it was the second
    /// kind under a name that read as the first. Every harness in the
    /// package passed an agent's fills through it, so one order was counted
    /// on each of a step's 65 ticks and landed after the fill it came from.
    /// `tick(order_flow=...)` is unchanged, since a tick is one minute.
    ///
    /// # Advance the clock between sessions of one day
    ///
    /// `hour` and `minute` are where the session starts, and each tick is
    /// one minute after the last. The time of day sets the intraday activity
    /// profile, so a day split into several sessions has to start each one
    /// where the previous one ended. Passing 9:30 to every session replays
    /// the opening minutes each time. Measured on 40 names with the day split
    /// into 78 sessions of 5 ticks, one name returned -25.0% for the day
    /// where `run_days(1)` gave it +0.6%, and the largest gap in log price
    /// was 0.29. With the clock advanced, the stepped day's prices match
    /// `run_days(1)` to the bit.
    ///
    /// `tradefloor.harness.session_clock(start, step, ticks_per_step)`
    /// returns each step's start. By hand, pass `(9 + m // 60, m % 60)` with
    /// `m = 30 + ticks already run today`.
    ///
    /// A session that starts before the day's previous session ended raises
    /// a `RuntimeWarning` and then runs as asked. The warning writes nothing
    /// to the log and changes no state. It never fires on a day's first
    /// session, on a clock that moves forward, or on a day that runs past
    /// midnight and wraps to 00:00 as `session_clock` does. A change of
    /// `day_of_week` starts the comparison again. Only `run_session` calls
    /// are compared, so `tick` and `run_until` neither warn nor move the
    /// clock it compares against.
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
        // The checks `tick` makes, except that the minute may carry. Without
        // them a session started at 99:999 on day 9 with NaN volatility ran
        // and was logged, and a replay of a log someone sent could carry any
        // of those.
        check_session_start(hour, minute, day_of_week, volatility)?;
        let session_news = self.build_news(news)?;
        let session_impacts = self.build_impacts(news_impacts)?;
        let session_flow = self.build_flow(flow_per_tick)?;
        let session_fills = self.build_flow(fills)?;
        // A session that starts before the day's previous one ended replays
        // minutes the day has already run. Warned rather than refused, since
        // a caller may mean it. After every check that can refuse the call
        // and before anything is opened or logged, so a warning filter set
        // to "error" leaves the engine exactly as it was.
        let start = hour
            .wrapping_mul(60)
            .wrapping_add(minute)
            .rem_euclid(MINUTES_PER_DAY);
        let placed = match self.session_clock {
            Some(clock) if self.market_open && clock.day_of_week == day_of_week => {
                let placed = clock.place(start);
                if placed < clock.end {
                    PyErr::warn_bound(
                        py,
                        &py.get_type_bound::<pyo3::exceptions::PyRuntimeWarning>(),
                        &repeated_clock_warning(start, clock.end),
                        1,
                    )?;
                }
                placed
            }
            _ => start,
        };
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
            self.open_market_on(i64::from(self.day_count));
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
        let volume_base = self.all_column(PriceField::Volume);
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
        self.accumulate_session(volume_base);
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
        // Where the next session of this day should start. A close ends the
        // day, and the session after it is the next day's first.
        self.session_clock = if close_at_end {
            None
        } else {
            Some(SessionClock {
                day_of_week,
                end: placed.saturating_add(i64::try_from(ticks).unwrap_or(i64::MAX)),
            })
        };
        Ok(self.buffer.ticks_written)
    }

    /// Advance whole days: open, session, close, repeat.
    ///
    /// For backtests. With decisions daily or slower, one call covers the
    /// whole span instead of a Python loop over sessions, and it records
    /// each day as it goes so the results tables stream.
    ///
    /// The boundary crossing this saves costs 0.357 microseconds against 249
    /// microseconds of engine work per tick at a hundred instruments.
    /// Chunking suits columnar output, but it is NOT a meaningful speedup. A
    /// Python loop over `run_session` loses well under one per cent. Use
    /// this because it reads better and records for you.
    ///
    /// `ledger` is an optional `tradefloor.DayLedger`, which is handed the
    /// state hash after every close and, when it keeps them, the state
    /// itself. It is a callback rather than a return value because a run of
    /// 252 days holds 252 leaves and the caller usually wants them beside a
    /// `RunManifest` rather than in a list this method built.
    ///
    /// `first_day` numbers the days for the record, the draw log and the
    /// fills, and defaults to the engine's own counter. It is a label and
    /// prices nothing, because the valuation counts the days the engine has
    /// run.
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
        days: i64,
        hour: i64,
        minute: i64,
        day_of_week: i64,
        ticks_per_day: i64,
        volatility: f64,
        record: bool,
        first_day: Option<u32>,
        ledger: Option<Py<PyAny>>,
    ) -> PyResult<usize> {
        // Signed, so a negative count is refused in these words rather than
        // as pyo3's "can't convert negative int to unsigned".
        if days < 1 {
            return Err(ValidationError::new_err(format!(
                "run_days: days must be 1 or more, got {days}"
            )));
        }
        if ticks_per_day < 1 {
            return Err(ValidationError::new_err(format!(
                "ticks_per_day must be 1 or more, got {ticks_per_day}"
            )));
        }
        let days = days as usize;
        let ticks_per_day = ticks_per_day as usize;
        // Whole days from a closed market. On an open one each day below
        // would open again, and until 0.8.5 that reopened the open day: its
        // tape was cleared, a second open was logged and the run no longer
        // matched one that closed first.
        if self.day_is_open() {
            return Err(ValidationError::new_err(self.open_day_refusal(
                "run_days runs whole days. Finish this one with run_session(...) \
                 and close_market(), or call close_market() now, then run_days().",
            )));
        }
        // Before the first day opens, so a bad clock opens nothing.
        check_session_start(hour, minute, day_of_week, volatility)?;
        // Defaults to the engine's own counter, not to zero. Numbering from
        // zero on every call gave a second run the day numbers of the first:
        // two `run_days(2)` calls on one engine put four simulated days on
        // two numbers, so `draw_log("jumps", 0, 0)` returned two days of
        // draws and `day_marks()` read 0, 1, 0, 1. The counter is what the
        // record and the day marks already advanced on, so following it is
        // what makes the second call continue the first.
        //
        // Another `first_day` renumbers the days and nothing else. It is a
        // label for the record, the draw log and the fills: the valuation
        // counts the days this engine has run, so `first_day=1000` prices
        // the market exactly as the default does, and the order log keeps
        // the label so a replay numbers the days the same way. Until 0.8.5
        // the label was the valuation's clock as well, and `first_day=1000`
        // moved prices by 0.17 in log within thirty days on pt-v20.
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
                    Some(self.snapshot_uncounted(py)?)
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
    /// For logic that must run inside the day. Every DECISION costs one
    /// boundary crossing, so this makes decision points sparser than ticks.
    /// An algorithm watching for a level crosses once, when the level is
    /// hit, instead of 390 times a day.
    ///
    /// Returns the tick the condition fired on, or None if `max_ticks` ran
    /// out first. None means the price never got there, which is often the
    /// answer you need.
    ///
    /// The close is NOT run when the condition fires, because the day is not
    /// over.
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
        check_session_start(hour, minute, day_of_week, volatility)?;
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
            self.open_market_on(i64::from(self.day_count));
        }
        let volume_base = self.all_column(PriceField::Volume);
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
        self.accumulate_session(volume_base);
        Ok(outcome.halted_at)
    }

    /// Run the close bookkeeping: GARCH update, the daily roll, and the
    /// daily macro step.
    ///
    /// The innovation handed to GARCH is the day's accumulated NOISE, not the
    /// day's total return.
    ///
    /// `DayCloseRequest` documents `None` as falling back to the total
    /// return, and this passed `None` for every company on every close, so
    /// the fallback was the behaviour. Nothing reported it. The variance
    /// process was driven by drift plus news plus flow when the model says
    /// it should be driven by the idiosyncratic shock alone.
    ///
    /// Measured before changing it, over 397 company-days, the two differ by
    /// a median factor of 0.82, a tenth percentile of 0.22 and a ninetieth of
    /// 3.20. They are different quantities.
    ///
    /// # The macro chain advances here
    ///
    /// `Engine::advance_day` (economy update, cycle transition, central
    /// bank) runs at the end of every close. Before this it was implemented
    /// and unit-tested but reachable from nowhere in Python, so every macro
    /// field sat at its initial value for the whole run and fair value never
    /// revalued, which left the fundamentals anchoring inert by default. The
    /// recorded design decision is that the full chain runs endogenously by
    /// default, and this is where it runs.
    ///
    /// The close is also the day boundary the reference implementation uses.
    /// The rates and VIX the factor model reads on the first tick of a new
    /// day are already the day's NEW values.
    ///
    /// Interaction with `pin_macro`: a pin applied at the START of a day (the
    /// `Scenario` convention) overrides whatever the previous close evolved,
    /// so a day-by-day pinned series stays exogenous exactly as before. A
    /// single pin no longer freezes its field forever. The chain keeps
    /// evolving FROM the pinned value.
    fn close_market(&mut self) {
        self.log.push(crate::python_log::LogEntry::CloseMarket);
        // The day is over, so the next session opens a new one.
        self.market_open = false;
        self.session_clock = None;
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
    /// contractual (see `tickers`).
    ///
    /// Rate instruments come after the equities, as in `tickers`. A field that
    /// does not exist for an index reads NaN there (`garch_variance`, `beta`,
    /// `last_daily_return`, `previous_tick_price`); the mispricing fields read
    /// zero, because an index level is its own fair value.
    fn column(&self, py: Python<'_>, field: &str) -> PyResult<Py<PyBytes>> {
        // The cash dividend per share each instrument went ex for at this
        // session's open: zero on every other session, on a rate index and
        // on every model without dividends.
        if field == "dividend" {
            return Ok(f64_bytes(py, &self.dividends_padded()));
        }
        let f = parse_field(field)?;
        Ok(f64_bytes(py, &self.all_column(f)))
    }

    /// The cash dividend per share each instrument went ex for at this
    /// session's open, in `tickers` order: 0.0 on any other session, on a
    /// rate index, and on every model without dividends
    /// (`dividend_payout_share`). The price already carries the drop; a
    /// holder is owed `quantity * amount` (`Portfolio.collect_dividends`).
    fn dividends_today(&self) -> Vec<f64> {
        self.dividends_padded()
    }

    /// The `distributions` table: one row per declared cash dividend, in
    /// declaration order. `day` is the ex-date, so the table joins `bars`
    /// on `(day, instrument_id)` at the session whose open carries the
    /// drop; `declared_day` is when the amount became public, 21 sessions
    /// before (or the run's first session, for a first ex-date closer than
    /// that), and a row exists only from that session on. `kind` is `"cash"`. Pass
    /// `day` for the rows going ex that session. Empty on every model
    /// without dividends. A record, like the tape: a restored engine starts
    /// it afresh.
    #[pyo3(signature = (day = None))]
    fn distributions(&self, day: Option<i64>) -> PyResult<crate::python_arrow::PyArrowStream> {
        let rows: Vec<&crate::engine::Distribution> = self
            .inner
            .distributions()
            .iter()
            .filter(|d| day.is_none_or(|x| d.ex_day == x))
            .collect();
        let batch = crate::python_arrow::distributions_batch(&rows)
            .map_err(crate::python_arrow::arrow_err)?;
        Ok(crate::python_arrow::PyArrowStream::new(
            "distributions",
            crate::python_arrow::distributions_schema(),
            vec![batch],
        ))
    }

    /// Current price per instrument, as little-endian f64 bytes.
    fn prices(&self, py: Python<'_>) -> Py<PyBytes> {
        f64_bytes(py, &self.all_column(PriceField::Price))
    }

    /// The last session's price path: `ticks_written x instruments`, row-major.
    ///
    /// Row-major means one tick's cross-section is contiguous and one
    /// instrument's path is strided. Emission is per tick, so the contiguous
    /// direction is the hot one.
    ///
    /// Sliced to `ticks_written`, not to capacity. The buffer is reused across
    /// sessions, so anything past that point is the previous session's data.
    fn session_prices(&self, py: Python<'_>) -> Py<PyBytes> {
        f64_bytes(py, self.written(&self.buffer.prices))
    }

    /// The last session's volume path, same shape as `session_prices`.
    ///
    /// Each value is the instrument's running total since the open after
    /// that tick, as the engine counts it. `bars()` reports each bar's own
    /// volume instead, the difference of these totals.
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
    /// The tick draws per instrument, so a larger roster shifts every
    /// subsequent draw and every existing instrument's path moves too. That
    /// is how the model works.
    ///
    /// Reproducibility is guaranteed. The generator carries across the
    /// change, so one seed plus the same edits at the same ticks reproduces
    /// the same market exactly, and a replay works.
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
        // Refused before it is logged, like every other refusal here.
        if let Some(first) = self.tickers.iter().position(|t| *t == instrument.ticker) {
            return Err(ValidationError::new_err(format!(
                "ticker {:?} is already listed, at position {first}. Each ticker \
                 must be unique: orders and books find a name by its ticker, so a \
                 second one could never be traded. Delist the first one, or give \
                 the new name another ticker.",
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
                // The close's jump, its fair-value shift and the open's
                // overnight move wait per name for the row that observes
                // them. The delisted name's entry goes with it, so every
                // later name's entry stays on that name's row (#154). Its
                // own value is dropped: it has no row on the next tape.
                for pending in [&mut self.pending_jump, &mut self.pending_overnight,
                                &mut self.pending_fair_value, &mut self.pending_dividend] {
                    if index < pending.len() {
                        pending.remove(index);
                    }
                }
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

    /// The number of sessions this engine has closed: the day the current
    /// (or next) session is numbered, as `open_market` numbers it.
    #[getter]
    fn day_count(&self) -> u32 {
        self.day_count
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
    /// the precondition for their prices agreeing. It is a diagnostic and
    /// enforces nothing. The per-stream split is `draws_by_stream()`, which
    /// since the 2026-08 stream split is the more useful comparison. Two
    /// runs whose `market` counts agree saw the same market noise even if
    /// their macro chains branched apart.
    #[getter]
    fn draws_consumed(&self) -> usize {
        self.inner.draws_consumed()
    }

    /// The name of the model this engine runs: a shipped preset's name when
    /// the coefficients are bit-identical to it, and `custom-XXXXXXXX`
    /// otherwise. With `seed` and the universe fingerprint it identifies a
    /// run, and a result under a non-shipped model can never present as a
    /// standard one.
    #[getter]
    fn model_fingerprint(&self) -> String {
        self.inner.model_fingerprint().to_string()
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
    /// These are THE NUMBERS THE UPDATE READ, not a recomputation. The
    /// identity's instantaneous terms (the sector draw's sigma and the jump
    /// arrival rate) are read at the VIX the close saw, `VIX_{t-1}`, and the
    /// update then moves the VIX. A getter that evaluated the identity
    /// afresh would read those two terms at `VIX_t` and disagree with the
    /// update by a day's VIX move, which is the size of the quantity a loop
    /// measurement is trying to see.
    ///
    /// `None` when the identity is off, because the read-back is not
    /// computed at all, and a dictionary of zeroes would read as a market
    /// with no variance rather than as a run with no read-back.
    ///
    /// This is a diagnostic and it is not carried in `state_snapshot`, so
    /// it moves no state hash. A fork carries it, since a fork is a copy and
    /// its last VIX update was the parent's. `restore_state` does not. A
    /// restored engine keeps its own last reading, `None` if it had advanced
    /// no day, until its own next day advances.
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
    /// (`market_vol_slow_weight == 0.0`, as on pt-v1 through pt-v3 and the
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
    /// fingerprint. A manifest embeds this.
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
    /// count varies with macro state (a chain in contraction draws a shock
    /// the expansion never rolls), so it is reported separately and stays
    /// out of the market comparison.
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
    /// and of nothing else. Both seeds are any integer from 0 to
    /// `2**64 - 1`.
    #[staticmethod]
    fn surgery_draws(
        seed: crate::python::Seed,
        stream: &str,
        surgery_seed: &Bound<'_, PyAny>,
        kinds: Vec<String>,
    ) -> PyResult<Vec<f64>> {
        let surgery_seed = crate::python::seed_from(surgery_seed, "surgery_seed")?;
        let id = stream_id(stream)?;
        let mut rng = crate::rng::GameRng::surgery(seed.0, id, surgery_seed);
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
    /// day marks, and the day the book stamps fills with. `open_market`
    /// stamps the engine's own day counter and `run_days` stamps
    /// `first_day`, each at the open it labels, so this is for a caller that
    /// drives the core between an open and a close and wants the draws
    /// numbered its own way, or an embedder taking draws through
    /// `draw_uniform` on a closed market.
    ///
    /// Stamped between an open and the close that follows it, this moves the
    /// number the rest of that day's draws carry and leaves the day mark on
    /// the number the open stamped.
    ///
    /// It is a label only. The valuation counts the days the engine has run, so
    /// this moves no price. Until 0.8.5 it was the buyback factor's elapsed
    /// time as well, and `set_day(5000)` mid-day moved the next session's
    /// prices by 0.21 in log with the state hash unchanged. The call goes
    /// into the order log, and a label off the counter's goes into the
    /// snapshot and the state hash, so a replay and a restore stamp the
    /// fills that follow as this engine does. A negative day is refused.
    fn set_day(&mut self, day: i64) -> PyResult<()> {
        if day < 0 {
            return Err(ValidationError::new_err(format!(
                "day must be 0 or more, got {day}. It numbers the draws and the \
                 fills, and days count from 0."
            )));
        }
        self.log.push(crate::python_log::LogEntry::SetDay { day });
        self.inner.set_day_label(day);
        Ok(())
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
    ///
    /// A normal must be finite and a uniform must lie in `[0, 1]`, 1.0
    /// being the value that stops an event from firing. Every patch is
    /// checked before any is installed, so a refused list installs nothing.
    /// An infinite normal made every price NaN before this was checked.
    fn patch_draws(&mut self, patches: Vec<(String, String, u64, f64)>) -> PyResult<()> {
        let mut checked = Vec::with_capacity(patches.len());
        for (stream, kind, index, value) in patches {
            let id = stream_id(&stream)?;
            let kind = draw_kind(&kind)?;
            let ok = match kind {
                crate::rng::DrawKind::Uniform => (0.0..=1.0).contains(&value),
                crate::rng::DrawKind::Normal => value.is_finite(),
            };
            if !ok {
                return Err(ValidationError::new_err(format!(
                    "the patch at {stream} {} {index} is {value}. A normal must be \
                     finite and a uniform must lie in [0, 1]; nothing was installed.",
                    kind.name()
                )));
            }
            checked.push((id, kind, index, value));
        }
        for (id, kind, index, value) in checked {
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
        -> PyResult<Vec<DrawLogRow>> {
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
    /// It draws nothing, writes nothing and is not logged, so calling it
    /// cannot change a run, and `state_hash` is the same with and without
    /// it. `price_impact` is the whole move the event adds to the price by
    /// the close, which makes it the answer key. Never hand it to an agent
    /// (a headline writer cuts it to its sign).
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
    /// It reads a counter the engine already keeps, with no draw and no
    /// write, so it cannot change a run.
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
    /// value read here can be written straight back without a conversion.
    ///
    /// `cycle` is the phase as PUBLISHED: under `cycle_publication_lag`, the
    /// phase of that many sessions before, so a turn reaches it when it is
    /// announced. `state_snapshot()["economy"]["cycle_phase"]` is the true
    /// phase.
    #[getter]
    fn macro_state(&self) -> PyMacro {
        let e = self.inner.economy();
        PyMacro {
            // The VIX as PUBLISHED (`Engine::published_vix`): the state
            // with `vix_stress_premium` at 0.0, which every preset through
            // pt-v20 carries (pt-v21 ships 3.0).
            vix: self.inner.published_vix(),
            federal_funds_rate: crate::units::percent_to_fraction(e.federal_funds_rate),
            corporate_bond_yield: Some(crate::units::percent_to_fraction(e.corporate_bond_yield)),
            inflation_rate: crate::units::percent_to_fraction(e.inflation_rate),
            qe_pe_boost: e.qe_pe_boost,
            qe_assets_ratio: e.qe_assets_ratio,
            fear_greed_index: e.fear_greed_index,
            // The phase as PUBLISHED: under `cycle_publication_lag` the
            // phase of that many sessions before, so this `Macro` written
            // back into a constructor opens where an observer believed the
            // economy was. `state_snapshot()["economy"]["cycle_phase"]` is
            // the true phase, for a restore and for an oracle.
            cycle: cycle_name(self.inner.published_cycle_phase()).to_string(),
        }
    }

    /// Write today's value of one or more macro series. The model moves each
    /// from the next close, so this is a one-day write rather than a hold:
    /// a VIX written at 45 drifts back toward the chain's own level over the
    /// sessions that follow. `tradefloor.Scenario.hold` writes a value every
    /// day, which is what holding one means.
    ///
    /// # A scenario is a path, not a feature
    ///
    /// A rate shock is `federal_funds_rate` stepping 0.025 -> 0.05 over N
    /// days, supplied day by day by whoever is running the study. There is
    /// no `rate_shock=True` flag. QE, a hiking cycle and stagflation are
    /// each a path over these fields, so the API gives you the fields and
    /// has no named scenarios for them.
    ///
    /// Only the named fields are written, and everything else keeps evolving
    /// endogenously, so pinning the policy rate does not freeze inflation.
    ///
    /// Rates are FRACTIONAL, as everywhere else, and validated before being
    /// converted.
    ///
    /// # Four fields the market cannot see directly
    ///
    /// `gdp_growth`, `unemployment_rate`, `tariff_rate` and `oil_price` sit
    /// in the same economy struct as the rest, but nothing in the market
    /// reads them. The tick reads `federal_funds_rate`, `corporate_bond_yield`,
    /// `qe_pe_boost`, `vix` and the cycle phase, and NOTHING else. These four
    /// reach a price only through the macro chain: the monthly inflation
    /// update, then the central bank's next MEETING, then the curve. That
    /// takes longer than a short study, the same horizon trap the
    /// `tradefloor.scenario` module documents for the policy rate.
    ///
    /// They are here because a supply shock or a tariff IS one of these in
    /// this model. Moving inflation by hand and calling it an oil shock
    /// would state the transmission as a fact when it is an assumption.
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
    /// (`Scenario(vix_sets_variance=True)`), and that is the intended way to
    /// set it.
    ///
    /// # The treasury curve
    ///
    /// `treasury_yield_2y` and `treasury_yield_10y` are FRACTIONAL and write
    /// the curve the rate instruments read (`UST2Y`, `UST10Y`, and `IGCORP`
    /// through the 10-year). The chain keeps running from a pinned value: the
    /// 10-year closes 5% of its gap to the policy rate plus a term premium
    /// every session. The 2-year follows `0.85 * policy + 0.15 * 10-year`:
    /// on every preset through pt-v19 it is recomputed as that at every close,
    /// so a 2-year pinned alone lasts until that close, and on pt-v20
    /// (`treasury_2y_noise` off zero) it closes 5% of its gap to it each
    /// session, so a pinned 2-year decays over weeks. A parallel curve shift
    /// therefore pins the policy rate and
    /// the 10-year with it, which is what `scenarios/curve_shock.yml` does.
    /// Equities read neither directly: they are discounted off
    /// `corporate_bond_yield`, which the next central-bank meeting recomputes
    /// from the 10-year.
    #[pyo3(signature = (
        *, vix = None, federal_funds_rate = None, corporate_bond_yield = None,
        inflation_rate = None, qe_pe_boost = None, qe_assets_ratio = None, fear_greed_index = None,
        gdp_growth = None, unemployment_rate = None, tariff_rate = None,
        oil_price = None, cycle = None, epicentre = None, vix_sets_variance = false,
        treasury_yield_2y = None, treasury_yield_10y = None, corporate_spread = None
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
        corporate_spread: Option<f64>,
    ) -> PyResult<()> {
        if let Some(v) = corporate_spread {
            if !v.is_finite() || !(0.0..=0.2).contains(&v) {
                return Err(ValidationError::new_err(format!(
                    "corporate_spread must be a fraction in [0, 0.2], got {v}"
                )));
            }
            if corporate_bond_yield.is_some() {
                return Err(ValidationError::new_err(
                    "pin the corporate yield's level or its spread over the \
                     10-year, not both in one call",
                ));
            }
        }
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
        // The VIX above zero and no higher than the highest ceiling a
        // shipped chain holds: this model's `vix_ceiling` or the default
        // preset's (181.33 on pt-v20), whichever is higher. Finite was the
        // only check, and a pinned -10 or a million made every price NaN; a
        // hold at 1000 made the index NaN on 9 of 30 seeds, and holds of 400
        // to 800 turned a fear shock into a rally. The default's ceiling
        // rather than this model's alone, because presets before pt-v19 clamp
        // at 80 and a pin of the real March 2020 close, 82.69, is a
        // reasonable thing to ask of them.
        if let Some(v) = vix {
            let ceiling = crate::mathx::max(
                self.inner.params().vix_ceiling,
                crate::params::ModelParams::preset(crate::params::DEFAULT_PRESET_NAME)
                    .map_or(0.0, |p| p.vix_ceiling),
            );
            // Worded as `Macro` words it (`check_vix`), with the bound this
            // pin also has.
            if v < 0.0 {
                return Err(ValidationError::new_err(format!(
                    "vix cannot be negative, got {v}. It is the index level, \
                     such as 20, above 0 and at most {ceiling}, the highest \
                     vix_ceiling of this model and the default preset."
                )));
            }
            if !(v > 0.0 && v <= ceiling) {
                return Err(ValidationError::new_err(format!(
                    "vix must be above 0 and at most {ceiling}, the highest \
                     vix_ceiling of this model and the default preset, got {v}. \
                     A level past it is one no shipped chain produces, and a \
                     pin there prices the market off it."
                )));
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
            ("corporate_spread", corporate_spread),
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

        // What the market stands on before the write, for the re-mark
        // below; `None` with `macro_publication_repricing` off.
        let marks = self.inner.published_macro_marks();
        let e = self.inner.economy_mut();
        let vix_before = e.vix;
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
            self.inner.pin_cycle_phase(p);
        }
        // After the 10-year, so the spread sits over the pinned curve.
        if let Some(v) = corporate_spread {
            self.inner
                .pin_corporate_spread(crate::units::fraction_to_percent(v));
        }
        // A pinned corporate yield holds through tonight's close. Marked
        // before the VIX's credit leg below, which never moves a level or a
        // spread pinned today.
        if corporate_bond_yield.is_some() {
            self.inner.pin_corporate_level();
        }
        // A pinned corporate yield is the corporate index's yield, including
        // when the pin repeats yesterday's value. Nothing without rate
        // instruments.
        if corporate_bond_yield.is_some() || corporate_spread.is_some() {
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
        // A PINNED VIX CHARGES THE CREDIT SPREAD NOTHING TONIGHT: the close's
        // VIX move is the law's reversion from the level written here. Only
        // turned on, as the mark above is. See `Engine::vix_pinned_today`.
        // A pinned corporate yield holds through tonight's close.
        if vix.is_some() {
            self.inner.mark_macro_pins_today(crate::engine::PIN_VIX);
            // The published VIX's stress memory restarts, so the quote is
            // the pin (`vix_stress_premium`; nothing with it at 0.0).
            self.inner.note_vix_pinned();
            // `pinned_vix_feedback`: the discount lands in this pin's
            // re-mark, and the credit leg, where it applies, in the corporate
            // index's.
            if self.inner.price_pinned_vix(vix_before) {
                self.inner.remark_credit_spread();
            }
        }
        // `macro_pins_hold`: every other field written here holds through
        // tonight's close. The marks are kept only under the dial.
        {
            use crate::engine::*;
            let mut bits = 0u16;
            for (bit, set) in [
                (PIN_CYCLE, phase.is_some()),
                (PIN_T10, treasury_yield_10y.is_some()),
                (PIN_T2, treasury_yield_2y.is_some()),
                (PIN_POLICY, federal_funds_rate.is_some()),
                (PIN_INFLATION, inflation_rate.is_some()),
                (PIN_GROWTH, gdp_growth.is_some()),
                (PIN_UNEMPLOYMENT, unemployment_rate.is_some()),
                (PIN_FEAR_GREED, fear_greed_index.is_some()),
                (PIN_OIL, oil_price.is_some()),
                (PIN_QE_PE, qe_pe_boost.is_some()),
                (PIN_QE_ASSETS, qe_assets_ratio.is_some()),
                (PIN_TARIFF, tariff_rate.is_some()),
            ] {
                if set {
                    bits |= bit;
                }
            }
            if bits != 0 {
                self.inner.mark_macro_pins_today(bits);
            }
        }
        // A pinned phase is news of a turn, which the anticipated earnings
        // price at once (`earnings_anticipation_half_life`); under
        // `cycle_nowcast_accuracy` the market's belief goes onto it and
        // holds there through tonight's close, since a caller's write is
        // public (`Engine::cycle_phase_pinned`, which refreshes as well).
        if phase.is_some() {
            self.inner.cycle_phase_pinned();
        } else {
            self.inner.refresh_earnings_anticipation();
        }
        // A pin is published the moment it is written, so with
        // `macro_publication_repricing` on the price takes it now rather
        // than at the next tick (`Engine::reprice_to_published_macro`).
        self.inner.reprice_to_published_macro(marks);
        // And the rate indices, to the curve as pinned
        // (`rate_close_remark`); nothing with the switch off.
        self.inner.remark_rates_after_pin();
        Ok(())
    }

    /// The anticipated earnings level's offset over the earnings cycle's
    /// current level, which the valuation reads beside it
    /// (`earnings_anticipation_half_life`); 0.0 with it off.
    #[getter]
    fn earnings_anticipation(&self) -> f64 {
        self.inner.economy().earnings_anticipation
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
    /// epicentre, or `None` when no episode is running, so a caller can
    /// tell "no crisis" from "a crisis nobody is at the centre of". The tick
    /// does not distinguish the two. Always `(False, 0, None)` while
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
    /// It exists separately from [`PyEngine::macro_state`] because of UNITS.
    /// `macro_state` returns the seven fields the `Macro` constructor takes;
    /// `state_snapshot()["economy"]` returns the whole economy in the CORE'S
    /// percent denomination. Neither is the set `pin_macro` accepts, and an
    /// intervention that multiplies a value it read by 1.4 and writes it back
    /// has to read and write in the same units, or it is out by a factor of
    /// a hundred on a trajectory that looks plausible. See `units.rs`.
    ///
    /// This returns the pinnable fields in the denomination `pin_macro`
    /// takes: fractional rates, VIX in points, `oil_price` in dollars,
    /// `cycle` as its name. Read one, change it, write it back.
    ///
    /// There are two exceptions. Under `cycle_publication_lag`,
    /// `cycle` is the phase as PUBLISHED, that many sessions late, so a
    /// phase pinned today reads back only once it is published; and under
    /// `gdp_publication_lag`, `gdp_growth` is the last quarter's mean growth
    /// as released, so a pinned growth reaches it only through its quarter.
    /// `state_snapshot()["economy"]` holds the true values (in percent).
    #[getter]
    fn macro_fields(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let e = self.inner.economy();
        let out = PyDict::new_bound(py);
        // The VIX as PUBLISHED: under `vix_stress_premium` the quote carries
        // a stress premium over the state, which
        // `state_snapshot()["economy"]["vix"]` holds and every internal
        // reader reads. The state itself with the dial at 0.0.
        out.set_item("vix", self.inner.published_vix())?;
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
        // Growth as published (`gdp_publication_lag`): the last quarter
        // released, its mean daily growth, the growth the economy runs at
        // with the dial at 0.0. A pin writes the true growth, which reaches
        // this only through the quarter it falls in.
        out.set_item(
            "gdp_growth",
            crate::units::percent_to_fraction(self.inner.published_gdp_growth()),
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
        // The phase as published (`cycle_publication_lag`): the phase of that
        // many sessions before, the phase the economy is in at 0.0. A pin
        // writes the true phase, which this reports once it is published.
        out.set_item("cycle", cycle_name(self.inner.published_cycle_phase()))?;
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

    /// Every company's three fair-value inputs, in roster order: earnings
    /// per share, book value per share and revenue growth, NaN where absent.
    /// The equities only; rate instruments carry no fundamentals.
    ///
    /// Returns a tuple of three lists, `(eps, book_value_per_share,
    /// revenue_growth)`, one value per equity in `tickers` order. It reads
    /// what the engine is valuing on, which is the published figures: under
    /// a preset with `fair_value_news_share` or an earnings cycle, the
    /// valuation scales these by the name's own level and the aggregate
    /// cycle, and neither is here. `set_fundamentals` writes them.
    fn fundamentals(&self) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
        self.inner.fundamentals()
    }

    /// Every equity's fair value as this engine computes it now, in roster
    /// order, NaN for a bankrupt or private name.
    ///
    /// This is the valuation `tradefloor.fair_value(..., model=...)` returns
    /// for the same fundamentals and macro, with what the engine adds from
    /// its own state on presets that set it: the nominal and
    /// earnings-cycle restatement, buybacks, each name's fair-value level
    /// and the VIX discount. It is the fair value the next tick starts from.
    /// That tick's `fundamental_value` in `truth()` also carries its own
    /// share of the tick's shocks (`fair_value_news_share`,
    /// `fair_value_market_share`), so the two are equal only where those
    /// shares are 0.0. On a preset whose opening draws the mispricing
    /// (`opening_market_sigma`), a name's fair-value level is written on its
    /// first tick, so before that tick this is its published valuation.
    fn fair_values(&self) -> Vec<f64> {
        self.inner.fair_values()
    }

    /// Replace every company's fair-value inputs, in roster order, NaN to
    /// clear one. The equities only, one value each.
    ///
    /// An embedder sets reported earnings through this, and so does the
    /// `market.earnings` scenario target. It consumes no
    /// draws, so it cannot move the generator, and an engine that is never
    /// told anything values on the figures it was built with, exactly as
    /// before this was exposed. A change moves every affected fair value, and
    /// so every price, from the next tick.
    ///
    /// Each value is finite, or NaN to clear it, as `Instrument` takes them;
    /// an infinite one is refused and nothing is written. Once the figures
    /// differ from the ones the engine was built with, `state_snapshot`
    /// carries them and `state_hash` covers them, so a restore values on
    /// what this wrote rather than on the figures the roster started with.
    fn set_fundamentals(
        &mut self,
        eps: Vec<f64>,
        book_value_per_share: Vec<f64>,
        revenue_growth: Vec<f64>,
    ) -> PyResult<()> {
        for (name, values) in [
            ("eps", &eps),
            ("book_value_per_share", &book_value_per_share),
            ("revenue_growth", &revenue_growth),
        ] {
            if let Some((i, v)) = values.iter().enumerate().find(|(_, v)| v.is_infinite()) {
                return Err(ValidationError::new_err(format!(
                    "{name}[{i}] = {v}. Each value must be finite, or NaN to clear \
                     it, as Instrument takes it; nothing was written."
                )));
            }
        }
        self.inner
            .set_fundamentals(&eps, &book_value_per_share, &revenue_growth)
            .map_err(ValidationError::new_err)?;
        // Between sessions the night's path moves to the open this write
        // makes (`night_session_steps`); nothing otherwise.
        self.inner.futures_retarget();
        // Logged once the engine has taken it, so a refused write leaves no
        // entry a replay would then fail on.
        self.log.push(crate::python_log::LogEntry::SetFundamentals {
            eps,
            book_value_per_share,
            revenue_growth,
        });
        Ok(())
    }

    /// Every company's share count, in roster order. The equities only;
    /// rate instruments carry no share count. `set_shares_outstanding`
    /// writes them.
    fn shares_outstanding(&self) -> Vec<f64> {
        self.inner.shares_outstanding()
    }

    /// Replace every company's share count, in roster order: one value per
    /// equity, each finite and above 0. For a host whose companies buy back
    /// stock or issue it.
    ///
    /// Each market cap follows at once, at the price standing, and every
    /// tick after reads the new count, so everything the engine weights by
    /// its own capitalisation -- the market factor's loadings, the roster
    /// beta normalisation, the cap-weighted market P/E -- weights by it. The
    /// listed index keeps its level across the write: its divisor is reset,
    /// as it is across a listing. The float is not moved; write
    /// `float_shares` with `set_column` to keep it in step. It consumes no
    /// draws, and a refused write writes nothing.
    ///
    /// Once the counts differ from the ones the engine was built with,
    /// `state_snapshot` carries them and `state_hash` covers them, so a
    /// restore weights by what this wrote.
    fn set_shares_outstanding(&mut self, shares: Vec<f64>) -> PyResult<()> {
        self.inner
            .set_shares_outstanding(&shares)
            .map_err(ValidationError::new_err)?;
        // Logged once the engine has taken it, so a refused write leaves no
        // entry a replay would then fail on.
        self.log.push(crate::python_log::LogEntry::SetSharesOutstanding { shares });
        Ok(())
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
    /// This simulator expresses a liquidity shock as less depth to trade
    /// against.
    ///
    /// # Why the engine never writes it itself
    ///
    /// The shipped close policy is [`AvgVolumePolicy::Hold`], so `avg_volume`
    /// stays whatever the universe calibrated it to be for the whole run.
    /// `market::daily` names writing `PriceField::AvgVolume` as the
    /// embedder's route, and this method is that route. It is recorded in
    /// the order log like any other input, so a replay, a checkpoint and a
    /// fork all carry it.
    ///
    /// Values must be finite and strictly positive. A zero does not mean no
    /// liquidity. `base_quote_size` treats a zero as ABSENT and falls
    /// through to realised volume, then to half a percent of shares
    /// outstanding, so a zeroed column quotes the book off a different input
    /// instead of making it thin.
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

    /// Hold at most `entries` engines in the opening cache; 0 turns it off
    /// and empties it. It holds 16 until this is called.
    ///
    /// A build whose model plays a market prehistory (`pt-v21` plays 504
    /// sessions, about 1.45 s over 20 names) is kept, and a later build from
    /// the same seed, universe and model, with no `macro_state`, is a copy
    /// of it. The copy is the same engine to the bit, so this moves no
    /// result: only how long a repeated build takes and the memory the kept
    /// engines hold, about 160 KB for a 20-name engine and 1.9 MB for a
    /// 500-name one. The kept engines hold 2,000 names between them at most,
    /// whatever the count. The cache is one per process.
    #[staticmethod]
    fn set_opening_cache_capacity(entries: i64) -> PyResult<()> {
        let entries = usize::try_from(entries).map_err(|_| {
            ValidationError::new_err(format!("entries must be 0 or more, got {entries}"))
        })?;
        Engine::set_opening_cache_capacity(entries);
        Ok(())
    }

    /// The opening cache as a dict: `capacity`, the `entries` it holds and
    /// the `names` across them, and the builds since the process started
    /// that it served (`hits`) or had to play (`misses`).
    #[staticmethod]
    fn opening_cache_info<'py>(py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let info = Engine::opening_cache_info();
        let out = PyDict::new_bound(py);
        out.set_item("capacity", info.capacity)?;
        out.set_item("entries", info.entries)?;
        out.set_item("names", info.names)?;
        out.set_item("hits", info.hits)?;
        out.set_item("misses", info.misses)?;
        Ok(out)
    }

    /// The layout version `state_snapshot` writes as `state_schema` and
    /// the newest `restore_state` reads. See [`STATE_SCHEMA`].
    #[classattr]
    #[allow(non_snake_case)]
    fn STATE_SCHEMA() -> i64 {
        STATE_SCHEMA
    }

    /// The live factor names, in the order `attribution` reports them.
    #[classattr]
    #[allow(non_snake_case)]
    fn FACTORS() -> Vec<String> {
        FACTOR_NAMES.iter().map(|s| s.to_string()).collect()
    }

    /// One attribution column across every instrument, as f64 bytes.
    ///
    /// # What it labels
    ///
    /// The simulator computed every driver of `mispricing_s`, the log gap
    /// between the model price and fair value, so it can report how much
    /// each one moved it. No historical dataset carries these labels. They
    /// decompose the change in `s` and nothing else. When fair value itself
    /// moves, on rates, earnings or the VIX, that move is not split up here,
    /// and on pt-v20 it is most of a price's daily move. For a breakdown that
    /// sums to the log price move, call `keep_explanations` before the run
    /// and `explain(ticker, day)` after it, which adds fair value, the book
    /// and the close's re-mark.
    ///
    /// Accumulated per DAY and reset at `open_market`, so a read after
    /// `close_market` still returns the day just finished.
    ///
    /// # Twelve components, the same twelve the `truth` table carries
    ///
    /// In `FACTORS` order. `reversion`, `momentum` and `crowd_lean` are the
    /// model's own dynamics. `company_news`, `order_flow_impact`,
    /// `short_squeeze_effect` and `random_noise` are the shocks.
    /// `circuit_breaker` is the correction when the session breaker clamps a
    /// price. `jump` is the daily jump and `overnight` the move applied at
    /// the open, the whole night under a night split (which `random_noise`
    /// does not count again; `garch_innovation` is the noise the GJR steps
    /// on, the night's included). `fair_value_shift` is minus the part of the shocks that
    /// moved the name's fair value for good instead of `s`. `dividend` is the
    /// change in `s` at an ex-date open, zero on every model without
    /// dividends (`dividend_payout_share`).
    ///
    /// All twelve sum to the day's change in `mispricing_s`. What that
    /// change covers depends on the preset. Measured over the first 40 days
    /// of `Universe.random(40, seed=111)` with seed 101, where the sum matched
    /// the change in `s` to within 1e-13 on both presets, and "explained" is
    /// the squared correlation with the day's log price change:
    ///
    /// - Through pt-v19, `fair_value_shift` is zero and fair value moves
    ///   little in a day. The eleven summed to a daily standard deviation of
    ///   0.0133 against 0.0143 for the log price change, and explained 69% of
    ///   it.
    /// - On pt-v20, the default from 0.8.5 to 0.9.1, most of each shock moves fair value
    ///   instead, so `random_noise` and `fair_value_shift` are both large and
    ///   mostly cancel (45.3% and 45.6% of the summed absolute
    ///   contributions). The eleven summed to a standard deviation of 0.0019
    ///   against 0.0127 and explained 28%. Without `fair_value_shift`, the
    ///   other ten count each shock at full size, as if fair value had not
    ///   moved, and they explained 75%.
    ///
    /// This is the DAY grain of exactly what `truth` reports per tick, so
    /// summing a `truth` column over a day reproduces the value here.
    ///
    /// These are the APPLIED contributions, what each driver did to `s`,
    /// and not the raw factors before scaling. Raw factors were the earlier
    /// behaviour, and they were wrong because the drift factors are divided
    /// by 390 on their way into `s` while noise is multiplied by the
    /// intraday volatility curve. Raw sums overstate news, flow and squeeze
    /// by around 390x against noise, and ranking raw magnitudes named
    /// `company_news` the dominant driver on sessions that were almost
    /// entirely noise.
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

    /// The day's noise each name's GJR variance steps on at tonight's
    /// close, before `garch_innovation_commensurate` rescales it: the
    /// `random_noise` attribution, and under a night split
    /// (`overnight_market_share` or `overnight_idio_share` set) the night's
    /// own noise ahead of it. The attribution counts the night once, in
    /// `overnight`, so its twelve factors sum to the day's change in `s`;
    /// the GJR reads the whole day's noise, and this is that sum. Zero in
    /// every rate instrument's slot. Not a decomposition of the price move.
    fn garch_innovation(&self, py: Python<'_>) -> Py<PyBytes> {
        f64_bytes(py, &self.padded(self.inner.day_noise_column(), 0.0))
    }

    /// The earnings reports ahead, as a real calendar lists them: one dict
    /// per report with `ticker`, `session` (the engine's session number of
    /// the reaction session, the first to trade the report) and
    /// `sessions_ahead` (0 is the session now open, or the next to open when
    /// the market is closed), for every reaction session in the next
    /// `horizon` sessions, ordered by session. Dates only: the surprise is
    /// realised at the reaction session's opening print and nothing here
    /// reads it. Empty unless the model runs the calendar
    /// (`earnings_surprise_sigma` non-zero).
    #[pyo3(signature = (horizon = 63))]
    fn earnings_calendar(&self, py: Python<'_>, horizon: i64) -> PyResult<Vec<Py<PyDict>>> {
        if !(0..=2520).contains(&horizon) {
            return Err(ValidationError::new_err(format!(
                "horizon is {horizon}. It is a number of sessions, in [0, 2520]."
            )));
        }
        let from = i64::from(self.day_count);
        let mut out = Vec::new();
        for (i, day) in self.inner.earnings_calendar(from, horizon) {
            let d = PyDict::new_bound(py);
            d.set_item("ticker", self.inner.companies()[i].ticker.clone())?;
            d.set_item("session", day)?;
            d.set_item("sessions_ahead", day - from)?;
            out.push(d.unbind());
        }
        Ok(out)
    }

    /// The earnings surprise each name's opening print realised at the last
    /// open, in `tickers` order, as f64 bytes (a log move of fair value);
    /// 0.0 where no report was realised, and in every rate instrument's slot.
    fn earnings_surprises(&self, py: Python<'_>) -> Py<PyBytes> {
        let mut moves = self.inner.earnings_moves().to_vec();
        moves.resize(self.inner.companies().len(), 0.0);
        f64_bytes(py, &self.padded(moves, 0.0))
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
        // Under `rate_intraday_live` a session's print is around the live
        // mark, which commits nothing: its repricing from the committed
        // yield joins the day's terms, and the flow is the premium over it.
        let live: Vec<crate::rates::Repricing> = match self.inner.rate_live_curve() {
            Some(c) => rates
                .iter()
                .map(|i| crate::rates::Repricing::new(
                    i.spec.duration, i.spec.convexity, c.dy(i.spec.point), 0.0))
                .collect(),
            None => Vec::new(),
        };
        let marks = self.inner.rate_marks();
        let values: Vec<f64> = match component {
            "carry" => rates.iter().map(|i| i.day_carry).collect(),
            "duration" => rates
                .iter()
                .enumerate()
                .map(|(j, i)| match live.get(j) {
                    Some(r) => i.day_duration + r.duration,
                    None => i.day_duration,
                })
                .collect(),
            "convexity" => rates
                .iter()
                .enumerate()
                .map(|(j, i)| match live.get(j) {
                    Some(r) => i.day_convexity + r.convexity,
                    None => i.day_convexity,
                })
                .collect(),
            "flow" => rates
                .iter()
                .zip(marks.iter())
                .map(|(i, m)| i.price / m.0 - 1.0)
                .collect(),
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
        let marks = self.inner.rate_marks();
        for (inst, mark) in self.inner.rates().instruments.iter().zip(marks.iter()) {
            let d = PyDict::new_bound(py);
            d.set_item("ticker", inst.spec.ticker)?;
            d.set_item("name", inst.spec.name)?;
            d.set_item("curve_point", inst.spec.point.as_str())?;
            d.set_item("duration", inst.spec.duration)?;
            d.set_item("convexity", inst.spec.convexity)?;
            d.set_item("spread_bps", inst.spec.spread_bps)?;
            // The live mark's during a session under `rate_intraday_live`.
            d.set_item("level", mark.0)?;
            d.set_item("yield", mark.1)?;
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

    /// The price index (`index_level_listed`), or None with the switch off:
    /// `level`, `sum(price * shares_outstanding) / divisor` over the public,
    /// solvent names on the prices as they stand (1000 before the first
    /// session opens); `close`, the level on the last close's prints (None
    /// before the first close); `divisor` (None before the first open); and
    /// `constituents`, the names in it. A listing, a delisting or a change of
    /// status resets the divisor so the level does not move.
    #[getter]
    fn index_level<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        let Some(index) = self.inner.index_level() else {
            return Ok(None);
        };
        let d = PyDict::new_bound(py);
        d.set_item("level", index.level)?;
        d.set_item("close", index.close)?;
        d.set_item("divisor", index.divisor)?;
        d.set_item("constituents", index.constituents)?;
        Ok(Some(d))
    }

    /// The live VIX (`vix_intraday_live`), or None with the switch off: within
    /// a session the projection of the VIX tonight's close will publish,
    /// given the session so far, refreshed every five session minutes;
    /// outside one, the published VIX.
    #[getter]
    fn live_vix(&self) -> Option<f64> {
        self.inner.live_vix()
    }

    /// The forecast the last close computed (`forecast_horizon_sessions`), or
    /// None with the dial at 0 and before the first close. Entry `h - 1` of
    /// each list is the expectation `h` sessions ahead: `vix`, the published
    /// VIX; `index_variance`, the index's one-session variance for session
    /// `t + h` (fraction squared); `policy_rate`, fractional; `oil`; and
    /// `name_variance`, each equity's one-session variance by ticker (an
    /// empty list for a name outside the index). `day` is the day count of
    /// the close that computed it, `horizon` the number of sessions, and
    /// `next_meeting` the session, counted from 0, whose close holds the next
    /// scheduled policy meeting (unscheduled ones are not on the calendar).
    fn forecast<'py>(&self, py: Python<'py>) -> PyResult<Option<Bound<'py, PyDict>>> {
        let Some(f) = self.inner.forecast() else {
            return Ok(None);
        };
        let d = PyDict::new_bound(py);
        d.set_item("day", f.day)?;
        d.set_item("horizon", f.horizon())?;
        d.set_item("vix", f.vix.clone())?;
        d.set_item("index_variance", f.index_variance.clone())?;
        let rates: Vec<f64> =
            f.policy_rate.iter().map(|r| crate::units::percent_to_fraction(*r)).collect();
        d.set_item("policy_rate", rates)?;
        d.set_item("oil", f.oil.clone())?;
        d.set_item("next_meeting", self.inner.next_meeting_session())?;
        let names = PyDict::new_bound(py);
        for (ticker, row) in self.tickers.iter().zip(f.name_variance.iter()) {
            names.set_item(ticker, row.clone())?;
        }
        d.set_item("name_variance", names)?;
        Ok(Some(d))
    }

    /// The listed contracts: the front two index futures
    /// (`futures_index_listed`), the next six monthly VIX futures
    /// (`futures_vix_listed`), then thirteen monthly policy-rate and eight
    /// quarterly term-rate futures (`futures_rates_listed`), then the next
    /// twelve monthly oil futures (`futures_oil_listed`), all but the index
    /// futures from the first close, each in expiry order; an empty list with
    /// all off. Each is a dict: `symbol` (`IDX.F0119` for the index
    /// future expiring at session 119, `VIX.F0035`, `FF.F0020` for the
    /// policy-rate future on sessions 0 to 20, `TR3.F0062`), `root`, `kind`
    /// (`"future"`), `expiry` (counted from 0 as `day_count` counts: an index
    /// or VIX future settles at that session's open, a rate future at its
    /// close), `roll`, `multiplier` (dollars per point), `tick`, `settlement`
    /// (`"opening_print_index"`, `"published_vix_at_open"`,
    /// `"average_policy_rate_at_close"`, `"compounded_policy_rate_at_close"`
    /// or `"oil_price_at_open"`)
    /// and `front` (the first of its family whose roll has not come; a rate
    /// future's period in progress).
    fn contracts<'py>(&self, py: Python<'py>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        self.inner
            .contracts()
            .iter()
            .map(|c| {
                let d = PyDict::new_bound(py);
                d.set_item("symbol", &c.symbol)?;
                d.set_item("root", &c.root)?;
                d.set_item("kind", c.kind.family())?;
                d.set_item("expiry", c.expiry)?;
                d.set_item("roll", c.roll)?;
                d.set_item("multiplier", c.multiplier)?;
                d.set_item("tick", c.tick)?;
                d.set_item("settlement", c.settlement.as_str())?;
                d.set_item("front", c.front)?;
                Ok(d)
            })
            .collect()
    }

    /// A listed contract's quote now, a dict: `bid`, `ask` and `mid` (the
    /// agent-facing book's touch, None on an empty side); `price`, the
    /// futures price the book is quoted around; `fair`, the carry fair
    /// value `(S - PV(D)) * exp(r * tau)`; `mark`, the last close's
    /// settlement mark (None before the contract's first close); `basis_bp`,
    /// `price - fair` in basis points of the index; `index`, the level
    /// `fair` reads (between sessions, the night's path under
    /// `night_session_steps`); `rate`, the financing rate, fractional;
    /// `dividends`, the present value of the dividends going ex before it
    /// settles, index points; `sessions_to_expiry`; `multiplier`; `tick`;
    /// `daily_volume`, contracts; `initial_margin` and `maintenance_margin`,
    /// dollars a contract, as the last close set them (`margin_scan_coverage`;
    /// None without margin and before the contract's first close); and
    /// `symbol`, `kind` and `expiry`.
    ///
    /// For a VIX future, `fair` is the expected settlement plus the premium,
    /// moved within a session by the live VIX's surprise: `expected` (the
    /// forecast's published VIX at expiry), `premium` (VIX points) and
    /// `loading` (on the live VIX's surprise over the forecast's expectation
    /// of tonight's VIX); `index` is the VIX the price reads, `basis_bp` the
    /// mark of agents' flow in basis points of the price, and `rate` and
    /// `dividends` are 0. For a rate future, `fair` is 100 less the period's
    /// expected rate and the premium: `expected` is that rate (per cent,
    /// realised closes and the forecast's expectation of the rest), `premium`
    /// the premium in rate points, `index` the policy rate now and `rate` the
    /// expected rate as a fraction; `loading` is None. For an oil future,
    /// `fair` and `expected` are the forecast's expected oil price at its
    /// settlement, `premium` 0, `index` the oil price now and `rate` the
    /// mean policy rate expected to expiry. `expected`, `premium`
    /// and `loading` are None for an index future. A read.
    fn quote<'py>(&self, py: Python<'py>, symbol: &str) -> PyResult<Bound<'py, PyDict>> {
        let q = self.inner.quote(symbol).ok_or_else(|| {
            ValidationError::new_err(format!(
                "{symbol:?} is not a listed contract: Engine.contracts() lists the ones that \
                 trade, and none does with futures_index_listed, futures_vix_listed, \
                 futures_rates_listed and futures_oil_listed off"
            ))
        })?;
        let d = PyDict::new_bound(py);
        d.set_item("symbol", &q.symbol)?;
        d.set_item("kind", q.kind.family())?;
        d.set_item("expiry", q.expiry)?;
        d.set_item("bid", q.bid)?;
        d.set_item("ask", q.ask)?;
        d.set_item("mid", q.mid)?;
        d.set_item("price", q.price)?;
        d.set_item("fair", q.fair)?;
        d.set_item("mark", q.mark)?;
        d.set_item("basis_bp", q.basis_bp)?;
        d.set_item("index", q.index)?;
        d.set_item("rate", q.rate)?;
        d.set_item("dividends", q.dividends)?;
        d.set_item("sessions_to_expiry", q.sessions_to_expiry)?;
        d.set_item("multiplier", q.multiplier)?;
        d.set_item("tick", q.tick)?;
        d.set_item("daily_volume", q.daily_volume)?;
        d.set_item("initial_margin", q.initial_margin)?;
        d.set_item("maintenance_margin", q.maintenance_margin)?;
        d.set_item("expected", q.expected)?;
        d.set_item("premium", q.premium)?;
        d.set_item("loading", q.loading)?;
        Ok(d)
    }

    /// The contracts' final settlements made at the open of session `day`,
    /// or every one so far with `day` None, in order: dicts with `symbol`,
    /// `root`, `kind`, `session`, `value` (the settlement price) and
    /// `reference` (what it settles on, from which `value` is set: the index
    /// of the expiry session's opening prints for an index future, the
    /// published VIX at the expiry open for a VIX future), in session order.
    /// Empty with `futures_index_listed` and `futures_vix_listed` off. A
    /// read.
    #[pyo3(signature = (day = None))]
    fn settlements<'py>(&self, py: Python<'py>, day: Option<i64>) -> PyResult<Vec<Bound<'py, PyDict>>> {
        self.inner
            .settlements(day)
            .iter()
            .map(|s| {
                let d = PyDict::new_bound(py);
                d.set_item("symbol", &s.symbol)?;
                d.set_item("root", &s.root)?;
                d.set_item("kind", s.kind.family())?;
                d.set_item("session", s.session)?;
                d.set_item("value", s.value)?;
                d.set_item("reference", s.reference)?;
                Ok(d)
            })
            .collect()
    }

    /// Walk the night session for index futures (`night_session_steps`),
    /// between a close and the next open: `steps` steps, or the rest of the
    /// night with None. Each step moves every future's fair value along the
    /// night's path to the fair value the next open will give it, steps the
    /// basis, and fills resting contract orders the moved books cross; the
    /// last step lands on that open. Stocks do not trade, and no stock
    /// price moves. Returns the steps walked: 0 with the dial at 0, once the
    /// night is done, or before any close. Refused while a day is open.
    /// Recorded in the order log.
    #[pyo3(signature = (steps = None))]
    fn run_night(&mut self, steps: Option<u32>) -> PyResult<u32> {
        if self.day_is_open() {
            return Err(ValidationError::new_err(self.open_day_refusal(
                "The night runs between a close and the next open: call close_market() first.",
            )));
        }
        let steps = steps.unwrap_or(u32::MAX);
        self.log.push(crate::python_log::LogEntry::RunNight { steps });
        Ok(self.inner.night_tick(steps))
    }

    /// The day's `random_noise` column split into the three draws it sums,
    /// `"market"`, `"sector"` or `"idio"`, as f64 bytes per company.
    ///
    /// The `random_noise` column is the innovation the close feeds the
    /// per-name GJR, and it is the sum of the factor's transmission, the
    /// sector's and the name's own draw. Only the split says which of the
    /// three the name's variance process is responding to. Reading it
    /// changes nothing. The close reads the same accumulator directly, and
    /// only while `garch_innovation_commensurate` is non-zero.
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
    /// Each is a deep copy of the whole engine, so the branches share no
    /// memory and driving one cannot perturb another.
    ///
    /// # Why a copy and not a rebuilt snapshot
    ///
    /// Forking used to mean building a fresh engine and writing a
    /// hand-maintained list of fields into it. The list was incomplete every
    /// time the engine grew: the per-day attribution accumulators and the
    /// market-open flag went missing first, then the market factor's variance
    /// state, then the common log-volume state, then the day counter, then the
    /// day's endogenous news. The last one made a mid-day fork price
    /// DIFFERENTLY from the parent it was supposed to be a copy of, on the
    /// shipped default preset, with nothing to indicate it.
    ///
    /// Each of those was a real divergence, found after the fact, so this
    /// keeps no list. `#[derive(Clone)]` copies whatever the struct holds,
    /// and a field added later is carried without anyone remembering to
    /// carry it.
    ///
    /// Unlike [`PyEngine::state_snapshot`] this also carries the run's ORDER
    /// LOG, so a fork can itself be checkpointed, forked again, or written to
    /// a `RunManifest`. Reconstructing a fork from a snapshot left its log
    /// empty, and a `Checkpoint` taken on one then replayed a market that
    /// began at day zero, with no error, because a checkpoint has no way to
    /// know the history it was handed is short.
    #[pyo3(signature = (count = 2))]
    fn fork(&self, count: i64) -> PyResult<Vec<PyEngine>> {
        if count < 1 {
            return Err(ValidationError::new_err(format!(
                "count must be at least 1, got {count}"
            )));
        }
        self.copies.bump();
        Ok((0..count).map(|_| self.clone()).collect())
    }

    /// How many times `fork`, `state_snapshot` or `restore_state` has been
    /// called on this engine. Not market state, and a fork starts at zero.
    /// `tradefloor.sandbox.TamperGuard` compares it around agent code,
    /// because a copy run ahead is look-ahead and changes nothing a hash
    /// can see.
    #[getter]
    fn copy_count(&self) -> u64 {
        self.copies.get()
    }

    /// The economy block of [`PyEngine::state_snapshot`], and only that,
    /// without counting as a copy. The whole snapshot carries the generator
    /// state, so it counts; this does not, which is what lets
    /// `tradefloor.sandbox.HiddenState.economy` serve an agent that declared
    /// hidden state without its scorecard reading as look-ahead.
    fn economy(&self, py: Python<'_>) -> PyResult<PyObject> {
        let snapshot = self.snapshot_uncounted(py)?;
        let economy = snapshot
            .bind(py)
            .get_item("economy")?
            .ok_or_else(|| ValidationError::new_err("the snapshot carried no economy block"))?;
        Ok(economy.unbind())
    }

    /// The `dividend` block of [`PyEngine::state_snapshot`], and only that,
    /// without counting as a copy; `None` on a model without dividends
    /// (`dividend_payout_share` 0.0). What `economy` is for the economy
    /// block: `tradefloor.sandbox.HiddenState.dividend_states` serves an
    /// agent that declared hidden state through it, so the Oracle's read of
    /// the dividend accrual on a dividend model does not count as look-ahead.
    fn dividend_states(&self, py: Python<'_>) -> PyResult<PyObject> {
        let snapshot = self.snapshot_uncounted(py)?;
        Ok(match snapshot.bind(py).get_item("dividend")? {
            Some(block) => block.unbind(),
            None => py.None(),
        })
    }

    /// This market's state as one 64-character hex digest: the ledger leaf.
    ///
    /// Covers every field [`PyEngine::state_snapshot`] carries but one, in
    /// one fixed order, on the canonical-f64 rule `manifest._f64` and
    /// `tests/known_answer.py` share. `crate::engine::Engine::state_hash`
    /// documents the encoding and why the generator states are hashed as
    /// `u64` bit patterns rather than as floats.
    ///
    /// The one is `session_tick`, the ticks the day has run. It numbers the
    /// fills the book stamps and moves no price, and every snapshot of an
    /// open or a closed day carries a count, so covering it would have moved
    /// every leaf already written. Two engines that differ only in it hash
    /// equal until one of them fills an order. The day's label and the
    /// valuation's clock are covered where they are carried, which is only
    /// where they are not the day the counter gives, and so are the
    /// fair-value inputs once `set_fundamentals` has moved them and the
    /// variance cascade on a model that runs it.
    ///
    /// Two engines whose hashes agree hold the same market state to the bit,
    /// including the macro chain and the generator positions that
    /// `market_digest` leaves out. Two engines that reached that state by
    /// different routes hash the same: this is a hash of state, and the
    /// order log and the recorded tape are outside it, exactly as they
    /// are outside the snapshot.
    ///
    /// Check one difference before comparing two runs. `run_session` with
    /// `close_at_end` leaves this binding's session flag set where
    /// `close_market` clears it, so the two spellings of one close hash
    /// apart on a market that is otherwise identical to the bit. The flag is
    /// state rather than bookkeeping, because it decides whether the next
    /// session re-opens the day and re-anchors `previous_close`. A recorded
    /// run still verifies against itself either way, because a replay runs
    /// the spelling its own log holds.
    ///
    /// Each per-slot array is hashed at the width the engine holds for it,
    /// whatever the roster does.
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
            &self.pending_jump, &self.pending_overnight, &self.pending_fair_value,
            &self.pending_dividend);
        crate::params::lower_hex(&bytes)
    }

    /// Every column plus the generator position, as one dict.
    ///
    /// It captures a market's complete state in constant time. Replaying an
    /// order log, the other way to get it, costs what the original run cost,
    /// measured at 1.04x on a sixty-day run.
    ///
    /// The columns are generated from `COLUMN_FIELDS`, not listed, so a field
    /// added to the engine appears here without anyone remembering. The Rust
    /// side works the same way. `set_column` matches exhaustively on
    /// `PriceField`, so a new variant fails to compile until it is handled,
    /// and a snapshot cannot silently omit it.
    ///
    /// The dict carries `state_schema`, its layout version
    /// ([`STATE_SCHEMA`]), and [`PyEngine::restore_state`] reads every field
    /// that version names or refuses the dict. The state hash does not cover
    /// the version, so a leaf is the same with or without it.
    ///
    /// # What it does NOT carry
    ///
    /// Everything here drives the market. Two things that do not are left
    /// out, and each of them makes a restored engine differ from the one it
    /// copied in a way no price will show:
    ///
    /// - **The order log.** A snapshot reproduces a STATE and the log
    ///   reproduces a HISTORY, which is what a published result cites. An
    ///   engine restored from a snapshot has an EMPTY log, so a `Checkpoint` or
    ///   `RunManifest` taken on it describes a run that began at day zero.
    /// - **The day's recorded tape.** `record` accumulates the day's ticks;
    ///   a restore starts that accumulation empty, so a day half-recorded
    ///   before the snapshot comes back half as long.
    ///
    /// Both are recording and history rather than market state.
    /// [`PyEngine::fork`] carries them, because it
    /// copies the engine rather than rebuilding one, and it is what
    /// `tradefloor.branch` uses.
    fn state_snapshot(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        self.copies.bump();
        // `Engine::snapshot_with` writes every field, in order, with this
        // binding's day counter, open flag and pending tape rows; this only
        // turns the tree into a dict.
        let snapshot = self.inner.snapshot_with(&self.day_loop());
        snapshot_map_to_py(py, snapshot.fields())
    }

    /// Put a market back to a captured state.
    ///
    /// # The contract
    ///
    /// The snapshot is read against its layout version, `state_schema`
    /// ([`STATE_SCHEMA`], 1), and every field that version carries is
    /// required. A missing key, a key this build does not know, a value of
    /// the wrong type or length, or a non-finite scalar is refused by name.
    /// Until this contract a field the dict lacked was skipped and the engine
    /// kept whatever it held, so a restored run could differ from the one it
    /// claimed to continue while `manifest.state_hash` refused the same dict.
    ///
    /// Some keys are carried only under a model dial (`vix_anchor_slow`,
    /// `fair_value_offset`, the economy's `cycle_history` and others). Each
    /// is required exactly when this engine's model sets its dial, and the
    /// refusal names the dial. A few are carried only while they hold
    /// something (`book`, `macro_pins_today`, `current_day`, ...), and their
    /// absence is a value: a pristine book, no pins, the day the counter
    /// gives.
    ///
    /// A snapshot with no `state_schema` was written before the version was
    /// recorded. tradefloor 0.8.5 to 0.8.8 wrote every key of version 1, so
    /// such a snapshot is read as version 1. One that lacks a key was
    /// written before 0.8.5 or edited, and is refused: those builds carried
    /// no session tick, and kept the day label and the valuation clock in
    /// one field they did not carry, so a restore would have to guess them.
    /// A newer version is refused rather than read in part.
    ///
    /// A refused restore changes nothing. The new state is built on a copy
    /// of the engine's core and swapped in only once every field has been
    /// read.
    ///
    /// # The roster
    ///
    /// Refuses a snapshot whose roster does not match this engine's, because
    /// the columns are positional: writing them onto a re-ordered or
    /// differently-sized roster would attach every price to the wrong company
    /// and look entirely plausible.
    ///
    /// # Matching tickers do NOT mean a matching universe
    ///
    /// The check is on identity and order, which is all an engine knows,
    /// because it holds no fundamentals. Tickers are generated positionally, so
    /// `Universe.random(40, seed=1)` and `Universe.random(40, seed=99)` have
    /// exactly the same names and entirely different earnings, sectors and
    /// share counts.
    ///
    /// Restoring across those two would pass this check and produce a market
    /// with the right prices and the wrong fair values. The caller must supply
    /// the universe the snapshot came from. This guard catches a re-ordered or
    /// resized roster, but not a substituted one.
    fn restore_state(&mut self, snapshot: &Bound<'_, PyDict>) -> PyResult<()> {
        self.copies.bump();
        // The dict as the core's field tree, then `Engine::restore`, which
        // checks every field and builds the new state on a copy: one
        // implementation behind this and the Rust API.
        let fields = snapshot_map_from_py(snapshot, "")?;
        let day_loop = self
            .inner
            .restore(&crate::snapshot::EngineSnapshot::from_map(fields))
            .map_err(|e| ValidationError::new_err(e.to_string()))?;
        // Every field has been read. Nothing above touched this binding.
        self.market_open = day_loop.market_open;
        self.day_count = day_loop.day_count;
        self.pending_jump = day_loop.pending_jump;
        self.pending_overnight = day_loop.pending_overnight;
        self.pending_fair_value = day_loop.pending_fair_value;
        self.pending_dividend = day_loop.pending_dividend;
        // The snapshot carries no clock, so the restored engine's next
        // session is treated as the day's first and does not warn.
        self.session_clock = None;
        self.restored_at = self.log.len();
        Ok(())
    }

    /// Capture the session just run, and the macro state, as one day.
    ///
    /// Recording is explicit. The session buffer is reused, so anything not
    /// captured before the next `run_session` is gone, but a caller who does
    /// not want a table should not pay to build one every session.
    ///
    /// The RAW buffers are kept rather than a finished batch. That costs the
    /// same memory and leaves grain a read-time decision, so one recording
    /// can answer tick, five-minute and daily questions without re-running
    /// the day.
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
            volume_base: self.day_buffer.volume_base.clone(),
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
            repriced: self.day_buffer.repriced.clone(),
            unbounded_print: self.day_buffer.unbounded_print.clone(),
            liquidity_share: self.day_buffer.liquidity_share.clone(),
            distribution: self.distribution_row(),
        });
        let e = self.inner.economy();
        self.recorded_macro.push(crate::python_arrow::MacroRow {
            day,
            // As published (`vix_stress_premium`), as `macro_fields`
            // reports it: the state with the dial at 0.0.
            vix: self.inner.published_vix(),
            // Fractional on the way out, matching the way in. A results table
            // reporting percent while the constructor takes fractions would
            // reintroduce the unit trap on the return journey.
            federal_funds_rate: crate::units::percent_to_fraction(e.federal_funds_rate),
            corporate_bond_yield: crate::units::percent_to_fraction(e.corporate_bond_yield),
            inflation_rate: crate::units::percent_to_fraction(e.inflation_rate),
            unemployment_rate: crate::units::percent_to_fraction(e.unemployment_rate),
            // As published (`gdp_publication_lag`), as `macro_fields`
            // reports it: the true daily growth with the dial at 0.0.
            gdp_growth: crate::units::percent_to_fraction(self.inner.published_gdp_growth()),
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
    /// the consumer, because bucketing ten million rows in Python to get two
    /// hundred is the cost this surface exists to avoid.
    ///
    ///   `bars()`             tick grain: day, tick, instrument_id, close, volume
    ///   `bars(minutes=5)`    five-minute OHLCV bars
    ///   `bars(grain="day")`  one OHLCV bar per instrument per day
    ///
    /// The tick schema has no open/high/low because at tick grain a bar IS the
    /// print and those columns would repeat close. Once ticks are bucketed
    /// those columns carry real information, so the coarse schema is wider.
    ///
    /// `volume` is the volume traded inside the bar, at every grain: a tick
    /// row holds that minute's volume, a five-minute bar the five minutes',
    /// and a day bar the day's. So the tick rows of a day sum to its day bar.
    /// The engine counts volume as a running total that the open resets to
    /// zero, and each bar is that total at its last tick minus the total
    /// before its first. Before 0.8.5 the tick rows held the running total
    /// itself and the coarser bars summed it, which made a day bar about
    /// two hundred times the day's volume. For the running total, take the
    /// cumulative sum of the tick rows per instrument and day.
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
                volume_base: self.session_volume_base.clone(),
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
                repriced: Vec::new(),
                unbounded_print: Vec::new(),
                liquidity_share: Vec::new(),
                distribution: Vec::new(),
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
                            &d.volume_base,
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
    /// It used to be ignored once anything had been recorded (the argument
    /// only labelled the un-recorded fallback), so `truth(day=4)` on a
    /// hundred-day run returned all hundred days. The table had the right
    /// schema and plausible values, so nothing flagged it.
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
                    } else if k == crate::market::factors::FAIR_VALUE_SLOT {
                        self.written(&self.buffer.components[crate::market::factors::TICK_FAIR_VALUE]).to_vec()
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
    /// known-answer digest is the same digest with the arm on. It costs
    /// roughly one settlement per active company per open tick, which is the
    /// largest single item in a tick.
    ///
    /// Set it before the FIRST session of a day. A day whose sessions
    /// disagree records fewer counterfactual values than it has rows, and
    /// `prints()` drops both columns for that whole day rather than serving
    /// one with a gap in it. The table's schema caveat names that case, so a
    /// caller who switched the arm mid-day can see why the columns are gone.
    /// Days that disagree with EACH OTHER raise.
    ///
    /// The run log does not carry it, because the log carries INPUTS and this
    /// is not one. No draw, no price and no company field depends on it. A
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
    /// fair value and the tape: `shock` is the log distance from the price
    /// the tick started from to the model price, `absorbed` is the log
    /// distance from the model price to the print, and `repriced` is the
    /// log distance from the last print to the price the tick started from.
    /// The three sum to the print's own log move. `repriced` is zero except
    /// where something wrote the price between two prints: the close's
    /// re-mark to the macro state it publishes and a `pin_macro`'s
    /// (`macro_publication_repricing`, pt-v20), and the overnight opening
    /// print (`overnight_variance_ratio`). It is NaN on the first print
    /// after `restore_state` on
    /// a model that can write a price between prints, because the snapshot
    /// does not carry it.
    ///
    ///   `prints()`        every recorded day, one batch each
    ///   `prints(day=N)`   that day alone
    ///
    /// `absorbed` is measured to the PRINTED price, so it carries the second
    /// circuit breaker as well as the book. `clamp` is the breaker's own
    /// part of it, and `absorbed - clamp` is the book's.
    ///
    /// Read them separately. On every clamped print measured, the book and
    /// the breaker pull opposite ways, and on roughly three fifths of them
    /// they cancel to the last bit, so `absorbed` alone reads exactly zero on
    /// a name the breaker had just moved 513 basis points. That is the same
    /// value it takes on a tick that never settled, and `clamp` tells those
    /// two rows apart.
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

        // The `distribution` column, on a model that pays dividends only.
        let distribution_now: Option<Vec<f64>> =
            if self.inner.carries_dividends() { Some(self.distribution_row()) } else { None };
        let with_distribution = if self.recorded.is_empty() {
            distribution_now.is_some()
        } else {
            self.recorded.iter().any(|d| !d.distribution.is_empty())
        };
        let (batches, depth) = if self.recorded.is_empty() {
            let ticks = self.buffer.ticks_written;
            let instruments = self.buffer.companies;
            let depth = state(
                self.written(&self.buffer.unbounded_print).len(),
                ticks * instruments,
            );
            (
                vec![crate::python_arrow::prints_batch_with(
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
                    self.written(&self.buffer.repriced),
                    self.written(&self.buffer.unbounded_print),
                    self.written(&self.buffer.liquidity_share),
                    depth,
                    distribution_now.as_deref(),
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
                let zeros = vec![0.0; d.instruments];
                let amounts: Option<&[f64]> = if !with_distribution {
                    None
                } else if d.distribution.is_empty() {
                    Some(&zeros)
                } else {
                    Some(&d.distribution)
                };
                out.push(
                    crate::python_arrow::prints_batch_with(
                        d.day,
                        d.ticks,
                        d.instruments,
                        &d.prices,
                        &d.anchor,
                        &d.shock,
                        &d.absorbed,
                        &d.clamp,
                        &d.repriced,
                        &d.unbounded_print,
                        &d.liquidity_share,
                        depth,
                        amounts,
                    )
                    .map_err(crate::python_arrow::arrow_err)?,
                );
            }
            (out, depth)
        };
        Ok(crate::python_arrow::PyArrowStream::new(
            "prints",
            crate::python_arrow::prints_schema_with(depth, with_distribution),
            batches,
        ))
    }

    /// The `macro` table: one row per recorded day.
    ///
    /// Row `d` holds the values day `d` traded under. `run_days`, and every
    /// day loop in the package, records a day before closing it, and the
    /// close is where the macro chain steps. So row `d` is what day `d - 1`'s
    /// close produced, plus any pin made before the record, and what day
    /// `d`'s own close produced is in row `d + 1`. If you call `record`
    /// yourself after `close_market`, row `d` holds day `d`'s close instead.
    ///
    /// A join on `day` with `bars` therefore pairs each day's return with the
    /// macro move that came before it. To pair a return with the move it
    /// caused, compare row `d + 1` with row `d`. Measured on 40 names over 5
    /// days (seed 101), `macro_state.vix` after each close read 17.005,
    /// 15.853, 17.849, 19.312 and 23.060, and rows 0 to 4 read 17.660,
    /// 17.005, 15.853, 17.849 and 19.312.
    ///
    /// The values after the last recorded close are not in the table. Read
    /// them from `macro_state` or `macro_fields`.
    ///
    /// Rates are fractional, as everywhere else.
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
    /// and an order submitted to it pays those prices because it consumed
    /// those levels. Market impact is emergent, with no impact coefficient.
    ///
    /// # It is a snapshot, and trading it does not move the market
    ///
    /// The book is rebuilt per call from current state, so the object
    /// returned is detached. Filling against it tells you your execution
    /// price, but the market only learns about your trading through `fills`
    /// on the next `run_session` (or `order_flow` on the next `tick`). The
    /// two channels are separate on purpose. One prices your fill and the
    /// other applies your pressure, once, so a harness that wants both must
    /// do both.
    fn book(&self, ticker: &str) -> PyResult<crate::python_book::PyOrderBook> {
        // A listed contract's book (`futures_index_listed`, `futures_vix_listed`), by its symbol.
        if !self.tickers.iter().any(|t| t == ticker) {
            if let Some(book) = self.inner.contract_book(ticker) {
                return Ok(crate::python_book::PyOrderBook::from_core(book));
            }
        }
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
    /// The book's structure sets the multiplier, and the `levels` argument
    /// does not. The maker quotes `BOOK_LEVELS = 10` a side
    /// (microstructure.rs), and `price_levels()` can only return what the
    /// book holds, so asking for `levels = 20` records the same 20 rows per
    /// name. Measured on six instruments, one snapshot gave 120 rows at
    /// levels 10 and 20 alike.
    ///
    /// So the caller decides when and how deep, and nothing samples on their
    /// behalf. A sampling rate built into the engine would be a modelling
    /// decision presented as a default, and two studies using different
    /// rates would be measuring different things without knowing it.
    ///
    /// This is NOT logged as a replayable input. It reads state without
    /// changing it and consumes no draws, so replaying a run produces the
    /// same depth whether or not anyone looked.
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
    /// `side` is 0 for bids and 1 for asks. It is an integer rather than a
    /// string because it repeats on every row, the same reason
    /// `instrument_id` is an index.
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
    /// order the list gives. The arrival order of a step depends on who sent
    /// what and never on how the caller built the list, so the same orders
    /// give the same market. Each one meets the book the
    /// ones before it left, so an agent later in the order pays for the
    /// levels an earlier one took and can hit an earlier one's resting
    /// order.
    ///
    /// Returns one report per order, in the order they were processed. An
    /// order the engine refuses raises, and the orders before it stand.
    fn submit_many(&mut self, py: Python<'_>, orders: Vec<Bound<'_, PyDict>>) -> PyResult<Vec<PyObject>> {
        let mut parsed: Vec<ParsedOrder> = Vec::new();
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
    /// during a session arrives here, and only here. A resting order the
    /// maker's re-quote crossed is ``"taker"`` against ``"mm"``, at the
    /// maker's price: it took the maker's size (docs/MODEL.md, "The
    /// agent's book"). Recorded in the order
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
    /// rata by signed shares under the imbalance law. With
    /// ``impact_memory_coefficient`` set, ``transient`` is the metaorder
    /// memory's part of the tick: what the tick's flow moved the name's
    /// displacement, shared by signed net shares; the key is absent
    /// otherwise. Recorded in the log.
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
                if let Some(v) = r.transient {
                    d.set_item("transient", v)?;
                }
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

    /// The order in which a cohort's orders reach the book on one step.
    ///
    /// ``labels`` sorted at ``book_arrival_shuffle`` 0.0, the order every
    /// preset carries. With the switch on, a seeded shuffle that is fresh
    /// every step: the labels sorted by a counter-based priority of this
    /// engine's seed, ``day``, ``step_of_day`` and the label
    /// (``rust/src/rng.rs``, ``arrival_priority``). It takes no draw and
    /// moves nothing, and a label's priority does not depend on which other
    /// labels are present, so removing one never reorders the rest.
    /// ``World.run`` reads it for a cohort.
    fn arrival_order(&self, day: u64, step_of_day: u64, labels: Vec<String>) -> Vec<String> {
        self.inner.arrival_order(day, step_of_day, &labels)
    }

    /// Every input that crossed into this engine, in order.
    ///
    /// # A seed alone does not reproduce a run
    ///
    /// It would if nothing else varied. But the market an agent trades in
    /// depends on the agent's own orders, so one seed with different flow is a
    /// different market. Reproducing a run means reproducing every input, and
    /// this is that sequence.
    ///
    /// It records INPUTS only. Prices, attribution and draw counts are
    /// consequences of replaying them, and logging those too would create a
    /// second source of truth that could disagree with the first.
    ///
    /// Embedder draws are in here too. They move the EXTERNAL stream, which
    /// the market no longer depends on since the stream split, so a replay
    /// that skipped one would hand the embedder different values from the
    /// run it claims to reproduce.
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
/// consumes nothing from an engine's. Any integer from 0 to `2**64 - 1`.
#[pyfunction]
#[pyo3(signature = (n = 108, *, seed = crate::python::Seed(0)),
       text_signature = "(n=108, *, seed=0)")]
pub fn random_instruments(n: i64, seed: crate::python::Seed) -> PyResult<Vec<PyInstrument>> {
    // Signed, so a negative count is refused in these words rather than as
    // pyo3's "can't convert negative int to unsigned".
    if n < 1 {
        return Err(ValidationError::new_err(format!(
            "Universe.random needs a number of companies of 1 or more, got {n}"
        )));
    }
    if n > 26 * 26 * 26 {
        return Err(ValidationError::new_err(format!(
            "Universe.random makes at most 17,576 companies, since tickers are \
             three letters; got {n}. For more, run several universes."
        )));
    }
    let n = n as usize;
    Ok(crate::universe::random_universe(n, seed.0)
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
/// It has only the three fields the factor model reads. The game's richer
/// event objects (headlines, bodies, storyline phases) never reach the price
/// loop, so carrying them across the boundary would be marshalling cost for
/// nothing.
///
/// Scope is decided by which fields are set, and the rules are not symmetric:
///
///   ticker set                    -> that instrument only
///   sector set, no ticker         -> every instrument in that sector
///   neither set                   -> market-wide
///
/// So an event with no ticker and no sector is market-wide, the broadest
/// possible event, and is not inert. That is the reference behaviour, and it
/// surprises people.
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
                return Err(ValidationError::new_err(crate::python::unknown_sector(s)));
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
/// A [`PyNews`] is an impulse arriving now, and this is the residue of one
/// still working through the tape. It also drives the volume
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
                return Err(ValidationError::new_err(crate::python::unknown_sector(s)));
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
    crate::market::factors::FAIR_VALUE_COMPONENT_KEY,
    crate::market::factors::DIVIDEND_COMPONENT_KEY,
];

/// The components `Engine.rate_attribution` reports.
pub const RATE_COMPONENTS: [&str; 4] = ["carry", "duration", "convexity", "flow"];

/// The column names, in snapshot and state-hash order: the core's list.
pub(crate) use crate::snapshot::COLUMN_FIELDS;

/// A sector's relative volatility multiplier.
///
/// Exposed so a loader can derive a beta with the same cross-sector structure
/// a generated universe has, without consuming an RNG draw. A draw here
/// would make building a universe perturb the market it is built for.
///
/// DIMENSIONLESS and relative (0.6 to 1.3). It is not a volatility in any
/// unit and should not be squared for a variance (see the sector table).
#[pyfunction]
pub fn sector_volatility(sector: &str) -> PyResult<f64> {
    crate::sectors::by_key(sector)
        .map(|s| s.volatility)
        .ok_or_else(|| {
            ValidationError::new_err(crate::python::unknown_sector(sector))
        })
}

/// A sector's long-run daily return standard deviation, as a fraction.
///
/// This is the dispersion measure to use. The relative `volatility`
/// multiplier is dimensionless, and squaring it for a variance is a mistake
/// the reference implementation made and had to fix.
#[pyfunction]
pub fn sector_daily_sigma(sector: &str) -> PyResult<f64> {
    crate::sectors::by_key(sector)
        .map(|s| s.daily_sigma)
        .ok_or_else(|| {
            ValidationError::new_err(crate::python::unknown_sector(sector))
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
/// It is read-only and computes nothing an engine does not compute for
/// itself. It exists so a reader (and `tests/test_crisis_epicentre.py`) can
/// check the solve against its own two equations with the engine's numbers
/// rather than a transcription of them.
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
/// until shocks accumulate, which takes on the order of one 60-day
/// half-life. This is the width of the distribution such a universe would
/// eventually reach, so a caller can start there instead of waiting.
///
/// Returns None for non-stationary parameters. A large finite number would
/// be worse, because a caller would use it.
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
