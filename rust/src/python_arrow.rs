//! Arrow record batches, behind the `python` feature.
//!
//! # Why Arrow rather than returning arrays
//!
//! One seed at tick grain for 100 names over a trading year is roughly 9.8
//! million rows per table. Returning that as Python objects is not slow, it is
//! unusable. Returning it as raw bytes — which is what this package did before
//! — is usable but leaves the consumer to reconstruct types, names and null
//! masks by hand, and gets no further than NumPy.
//!
//! Arrow record batches handed over through the **C Data Interface** reach
//! polars, pandas, pyarrow and duckdb zero-copy, without this crate taking a
//! dependency on any of them. The consumer picks the tool; the library ships
//! buffers.
//!
//! # The dtype rule
//!
//! Every numeric column is `Float64`. There is no f32 option and there will
//! not be one. Bit-exactness is the product: the known-answer gate hashes
//! these buffers, and a half-precision "memory-saving" variant would be a
//! different market that happens to plot the same — a silent parity-breaking
//! switch sitting in the public API.
//!
//! Identifiers are the exception, and are integers rather than strings:
//! `instrument_id` is a `UInt32` index into the roster, not a repeated ticker.
//! At 9.8 million rows the difference between an index and a string per row is
//! the difference between a column and a memory problem.

#![allow(unexpected_cfgs, clippy::useless_conversion)]

use std::sync::Arc;

use arrow::array::{ArrayRef, Float64Array, RecordBatch, UInt32Array};
use arrow::datatypes::{DataType, Field, Schema, SchemaRef};
use arrow::ffi_stream::FFI_ArrowArrayStream;
use arrow::record_batch::RecordBatchReader;
use pyo3::prelude::*;
use pyo3::types::PyCapsule;

use crate::python::ValidationError;

/// `bars`: one row per (tick, instrument).
///
/// At tick grain a bar IS the print, so `close` and `volume` are the whole
/// story and there is no open/high/low to report that would not simply repeat
/// `close`. Emitting four identical columns to look like a candle would be
/// padding, and padding at ten million rows is expensive padding.
pub fn bars_schema() -> SchemaRef {
    Arc::new(Schema::new(vec![
        Field::new("day", DataType::UInt32, false),
        Field::new("tick", DataType::UInt32, false),
        Field::new("instrument_id", DataType::UInt32, false),
        Field::new("close", DataType::Float64, false),
        Field::new("volume", DataType::Float64, false),
    ]))
}

/// `truth`: the labelled-dataset table.
///
/// The distinguishing output. A historical dataset can tell you a price; it
/// cannot tell you the fair value the price was deviating from, or how much of
/// the move was order flow rather than noise. This can, because it computed
/// them.
pub fn truth_schema() -> SchemaRef {
    Arc::new(Schema::new(vec![
        Field::new("day", DataType::UInt32, false),
        Field::new("tick", DataType::UInt32, false),
        Field::new("instrument_id", DataType::UInt32, false),
        Field::new("mispricing_s", DataType::Float64, false),
    ]))
}

/// Build the `bars` batch from a session's row-major buffers.
///
/// Row-major means one tick's cross-section is contiguous, so this walks the
/// buffers in their natural order and the emitted row order is
/// tick-major — which is also the order a consumer wants for a time series.
pub fn bars_batch(
    day: u32,
    ticks: usize,
    instruments: usize,
    prices: &[f64],
    volumes: &[f64],
) -> Result<RecordBatch, String> {
    let rows = ticks * instruments;
    if prices.len() < rows || volumes.len() < rows {
        return Err(format!(
            "buffer shorter than {ticks} ticks x {instruments} instruments"
        ));
    }

    let mut day_col = Vec::with_capacity(rows);
    let mut tick_col = Vec::with_capacity(rows);
    let mut id_col = Vec::with_capacity(rows);
    for t in 0..ticks {
        for i in 0..instruments {
            day_col.push(day);
            tick_col.push(t as u32);
            id_col.push(i as u32);
        }
    }

    let columns: Vec<ArrayRef> = vec![
        Arc::new(UInt32Array::from(day_col)),
        Arc::new(UInt32Array::from(tick_col)),
        Arc::new(UInt32Array::from(id_col)),
        Arc::new(Float64Array::from(prices[..rows].to_vec())),
        Arc::new(Float64Array::from(volumes[..rows].to_vec())),
    ];
    RecordBatch::try_new(bars_schema(), columns).map_err(|e| e.to_string())
}

/// Build the `truth` batch from a session's mispricing buffer.
pub fn truth_batch(
    day: u32,
    ticks: usize,
    instruments: usize,
    mispricing: &[f64],
) -> Result<RecordBatch, String> {
    let rows = ticks * instruments;
    if mispricing.len() < rows {
        return Err(format!(
            "buffer shorter than {ticks} ticks x {instruments} instruments"
        ));
    }

    let mut day_col = Vec::with_capacity(rows);
    let mut tick_col = Vec::with_capacity(rows);
    let mut id_col = Vec::with_capacity(rows);
    for t in 0..ticks {
        for i in 0..instruments {
            day_col.push(day);
            tick_col.push(t as u32);
            id_col.push(i as u32);
        }
    }

    let columns: Vec<ArrayRef> = vec![
        Arc::new(UInt32Array::from(day_col)),
        Arc::new(UInt32Array::from(tick_col)),
        Arc::new(UInt32Array::from(id_col)),
        Arc::new(Float64Array::from(mispricing[..rows].to_vec())),
    ];
    RecordBatch::try_new(truth_schema(), columns).map_err(|e| e.to_string())
}

/// A reader over a fixed set of batches.
///
/// Deliberately a READER rather than a single batch, even when there is only
/// one batch to hand over. The C stream interface is a pull protocol, so a
/// consumer that wants to process a sweep day by day gets that for free; a
/// design that handed over one materialised table would have to be replaced
/// rather than extended when the first user runs out of memory.
struct BatchReader {
    schema: SchemaRef,
    batches: std::vec::IntoIter<RecordBatch>,
}

impl Iterator for BatchReader {
    type Item = Result<RecordBatch, arrow::error::ArrowError>;
    fn next(&mut self) -> Option<Self::Item> {
        self.batches.next().map(Ok)
    }
}

impl RecordBatchReader for BatchReader {
    fn schema(&self) -> SchemaRef {
        self.schema.clone()
    }
}

/// A stream of record batches, consumable by anything that speaks Arrow.
///
/// Exposes `__arrow_c_stream__`, so `polars.from_arrow`, `pyarrow.table`,
/// `pandas` and duckdb all read it directly and zero-copy. The package depends
/// on none of them.
#[pyclass(name = "ArrowStream", module = "pretium._core")]
pub struct PyArrowStream {
    schema: SchemaRef,
    batches: Vec<RecordBatch>,
    name: String,
}

impl PyArrowStream {
    pub fn new(name: &str, schema: SchemaRef, batches: Vec<RecordBatch>) -> Self {
        Self { schema, batches, name: name.to_string() }
    }
}

#[pymethods]
impl PyArrowStream {
    /// The Arrow PyCapsule stream protocol.
    ///
    /// `requested_schema` is accepted and ignored, which the protocol allows:
    /// it is a hint for casting, and this stream has exactly one schema it can
    /// produce. Silently casting f64 columns to something narrower on request
    /// is precisely the parity-breaking switch the dtype rule exists to
    /// prevent, so a caller asking for one gets the honest schema instead.
    #[pyo3(signature = (requested_schema = None))]
    fn __arrow_c_stream__<'py>(
        &self,
        py: Python<'py>,
        requested_schema: Option<PyObject>,
    ) -> PyResult<Bound<'py, PyCapsule>> {
        let _ = requested_schema;
        let reader = BatchReader {
            schema: self.schema.clone(),
            batches: self.batches.clone().into_iter(),
        };
        let stream = FFI_ArrowArrayStream::new(Box::new(reader));
        // The capsule name is fixed by the protocol. A different name is not a
        // variant, it is invisible to every consumer.
        let name = std::ffi::CString::new("arrow_array_stream").unwrap();
        PyCapsule::new_bound(py, stream, Some(name))
    }

    /// Number of rows across every batch.
    #[getter]
    fn num_rows(&self) -> usize {
        self.batches.iter().map(|b| b.num_rows()).sum()
    }

    #[getter]
    fn num_batches(&self) -> usize {
        self.batches.len()
    }

    /// Column names, in schema order.
    #[getter]
    fn columns(&self) -> Vec<String> {
        self.schema.fields().iter().map(|f| f.name().clone()).collect()
    }

    fn __repr__(&self) -> String {
        format!(
            "ArrowStream({:?}, {} rows in {} batch(es))",
            self.name,
            self.num_rows(),
            self.batches.len()
        )
    }
}

/// Map an arrow error onto the package's own taxonomy.
pub fn arrow_err(e: String) -> PyErr {
    ValidationError::new_err(e)
}

/// `macro`: one row per day, the evolved macro state.
///
/// Keyed by the same `day` as `bars` and `truth`, so aligning a macro signal
/// with prices is a join rather than a hand-rolled accumulation loop. That is
/// the whole reason it is a table at all instead of an accessor.
///
/// Rates are FRACTIONAL here, matching every other boundary in the package. A
/// results table that reported percent while the constructor took fractions
/// would be the unit trap reintroduced on the way out.
pub fn macro_schema() -> SchemaRef {
    Arc::new(Schema::new(vec![
        Field::new("day", DataType::UInt32, false),
        Field::new("vix", DataType::Float64, false),
        Field::new("federal_funds_rate", DataType::Float64, false),
        Field::new("corporate_bond_yield", DataType::Float64, false),
        Field::new("inflation_rate", DataType::Float64, false),
        Field::new("unemployment_rate", DataType::Float64, false),
        Field::new("gdp_growth", DataType::Float64, false),
        Field::new("qe_pe_boost", DataType::Float64, false),
        Field::new("fear_greed_index", DataType::Float64, false),
    ]))
}

/// One day of macro state, already converted to the fractional boundary.
#[derive(Debug, Clone, Copy)]
pub struct MacroRow {
    pub day: u32,
    pub vix: f64,
    pub federal_funds_rate: f64,
    pub corporate_bond_yield: f64,
    pub inflation_rate: f64,
    pub unemployment_rate: f64,
    pub gdp_growth: f64,
    pub qe_pe_boost: f64,
    pub fear_greed_index: f64,
}

pub fn macro_batch(rows: &[MacroRow]) -> Result<RecordBatch, String> {
    let columns: Vec<ArrayRef> = vec![
        Arc::new(UInt32Array::from(rows.iter().map(|r| r.day).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.vix).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.federal_funds_rate).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.corporate_bond_yield).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.inflation_rate).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.unemployment_rate).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.gdp_growth).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.qe_pe_boost).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.fear_greed_index).collect::<Vec<_>>())),
    ];
    RecordBatch::try_new(macro_schema(), columns).map_err(|e| e.to_string())
}

/// `fills`: one row per execution.
///
/// The trader's own record, not the market's. It exists so a study can join
/// what it DID against what the market did -- `bars` says where the price was,
/// this says where you got filled, and the gap between them is your execution
/// quality.
///
/// `worst_price` is carried alongside the average deliberately. An average
/// alone hides how far up the book an order reached, and that tail is what
/// separates a large order that was worked from one that was dumped.
pub fn fills_schema() -> SchemaRef {
    Arc::new(Schema::new(vec![
        Field::new("day", DataType::UInt32, false),
        Field::new("step", DataType::UInt32, false),
        Field::new("instrument_id", DataType::UInt32, false),
        Field::new("quantity", DataType::Float64, false),
        Field::new("price", DataType::Float64, false),
        Field::new("worst_price", DataType::Float64, false),
        Field::new("notional", DataType::Float64, false),
    ]))
}

#[allow(clippy::too_many_arguments)]
pub fn fills_batch(
    day: Vec<u32>,
    step: Vec<u32>,
    instrument_id: Vec<u32>,
    quantity: Vec<f64>,
    price: Vec<f64>,
    worst_price: Vec<f64>,
    notional: Vec<f64>,
) -> Result<RecordBatch, String> {
    let n = day.len();
    for (name, len) in [
        ("step", step.len()),
        ("instrument_id", instrument_id.len()),
        ("quantity", quantity.len()),
        ("price", price.len()),
        ("worst_price", worst_price.len()),
        ("notional", notional.len()),
    ] {
        if len != n {
            return Err(format!("{name} has {len} rows, expected {n}"));
        }
    }
    let columns: Vec<ArrayRef> = vec![
        Arc::new(UInt32Array::from(day)),
        Arc::new(UInt32Array::from(step)),
        Arc::new(UInt32Array::from(instrument_id)),
        Arc::new(Float64Array::from(quantity)),
        Arc::new(Float64Array::from(price)),
        Arc::new(Float64Array::from(worst_price)),
        Arc::new(Float64Array::from(notional)),
    ];
    RecordBatch::try_new(fills_schema(), columns).map_err(|e| e.to_string())
}

/// Build a `fills` stream from parallel columns.
///
/// Takes columns rather than a list of row objects because that is the shape
/// the data is already in on both sides, and marshalling a million small
/// objects across the boundary to immediately transpose them would be the cost
/// this whole surface exists to avoid.
#[pyfunction]
#[allow(clippy::too_many_arguments)]
pub fn fills_stream(
    day: Vec<u32>,
    step: Vec<u32>,
    instrument_id: Vec<u32>,
    quantity: Vec<f64>,
    price: Vec<f64>,
    worst_price: Vec<f64>,
    notional: Vec<f64>,
) -> PyResult<PyArrowStream> {
    let batch = fills_batch(day, step, instrument_id, quantity, price, worst_price, notional)
        .map_err(arrow_err)?;
    Ok(PyArrowStream::new("fills", fills_schema(), vec![batch]))
}
