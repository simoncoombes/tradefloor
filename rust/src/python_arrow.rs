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
