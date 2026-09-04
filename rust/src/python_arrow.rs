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
/// The distinguishing output, and the reason it is worth building a simulator
/// rather than downloading history. A historical dataset gives you a price. It
/// cannot give you the fair value that price was deviating from, and it cannot
/// give you how much of a move was company news rather than order-flow
/// pressure rather than noise -- because nobody knows. Here the simulator
/// computed each of those to produce the print, so it can report them.
///
/// One row per instrument per tick, joining to `bars` on
/// `(day, tick, instrument_id)`, and carrying three kinds of column.
///
/// **Levels.** `fundamental_value` is the valuation -- earnings, sector
/// anchor, rates -- with no mispricing in it. `anchor_price` is
/// `fundamental_value * exp(mispricing_s)`: the price the model wanted before
/// the order book touched it. The printed price in `bars` is what the book
/// actually settled, so `close - anchor_price` isolates the microstructure and
/// `mispricing_s` isolates the valuation gap. Three quantities that are easy
/// to conflate, kept apart on purpose.
///
/// **The decomposition.** Ten columns, in `FACTOR_NAMES` order, giving
/// every contribution to this tick's change in `s`: the eight
/// `S_COMPONENT_KEYS`, then the daily jump and the overnight move, which
/// land outside the tick loop and are booked onto the row where each is
/// observed. `reversion`, `momentum` and `crowd_lean` are the model's own
/// dynamics; `company_news`, `order_flow_impact`, `short_squeeze_effect`
/// and `random_noise` are the shocks. They sum to `Δs`.
///
/// That they SUM is what makes this a dataset rather than a commentary. A
/// consumer can difference `mispricing_s` across ticks, add the nine columns,
/// and check the label against the outcome instead of trusting it. Where the
/// residual is not zero, the `s` clamp or a circuit breaker bound -- which is
/// itself worth seeing, and would be invisible in a summary.
///
/// Per TICK rather than per day, which is the whole point. The day-level
/// accumulator behind `attribution()` can say order flow moved a price today;
/// it cannot say when, and a label that cannot be aligned to a bar is not a
/// label.
///
/// A company that did not tick has zeros across all seven: it did not move, so
/// nothing contributed to it, and `Δs` was zero. The two level columns instead
/// PERSIST from its last tick, because a valuation does not disappear when
/// trading is quiet.
pub fn truth_schema() -> SchemaRef {
    let mut fields = vec![
        Field::new("day", DataType::UInt32, false),
        Field::new("tick", DataType::UInt32, false),
        Field::new("instrument_id", DataType::UInt32, false),
        Field::new("mispricing_s", DataType::Float64, false),
        Field::new("fundamental_value", DataType::Float64, false),
        Field::new("anchor_price", DataType::Float64, false),
    ];
    // Generated from the same constant the tick fills, so the schema cannot
    // drift out of step with the data. Written out by hand in two places, the
    // columns would eventually swap and every value would still look
    // plausible.
    for key in crate::market::factors::S_COMPONENT_KEYS {
        fields.push(Field::new(key, DataType::Float64, false));
    }
    // The daily jump, which `apply_jumps` writes to `s` after the tick loop.
    // Without it the other eight columns do not reconstruct the move on any preset
    // that carries jumps, which is every preset from pt-v4 (§74).
    fields.push(Field::new(
        crate::market::factors::JUMP_COMPONENT_KEY,
        DataType::Float64,
        false,
    ));
    Arc::new(Schema::new(fields))
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

/// Build the `truth` batch from a session's ground-truth buffers.
pub fn truth_batch(
    day: u32,
    ticks: usize,
    instruments: usize,
    mispricing: &[f64],
    fundamental: &[f64],
    anchor: &[f64],
    components: &[Vec<f64>; 10],
) -> Result<RecordBatch, String> {
    let rows = ticks * instruments;
    if mispricing.len() < rows || fundamental.len() < rows || anchor.len() < rows {
        return Err(format!(
            "buffer shorter than {ticks} ticks x {instruments} instruments"
        ));
    }
    for column in components.iter() {
        if column.len() < rows {
            return Err(format!(
                "component buffer shorter than {ticks} ticks x {instruments} instruments"
            ));
        }
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

    let mut columns: Vec<ArrayRef> = vec![
        Arc::new(UInt32Array::from(day_col)),
        Arc::new(UInt32Array::from(tick_col)),
        Arc::new(UInt32Array::from(id_col)),
        Arc::new(Float64Array::from(mispricing[..rows].to_vec())),
        Arc::new(Float64Array::from(fundamental[..rows].to_vec())),
        Arc::new(Float64Array::from(anchor[..rows].to_vec())),
    ];
    for column in components.iter() {
        columns.push(Arc::new(Float64Array::from(column[..rows].to_vec())));
    }
    RecordBatch::try_new(truth_schema(), columns).map_err(|e| e.to_string())
}

/// `prints`: how each print was arrived at.
///
/// One row per instrument per tick, joining to `bars` and `truth` on
/// `(day, tick, instrument_id)`. `truth` says what moved fair value;
/// this says what happened between fair value and the tape.
///
/// **The decomposition.** `shock` is `log(model_price / the last print)`:
/// the distance the price model put between the tape and this tick's anchor.
/// `absorbed` is `log(print / model_price)`: what stood in the way. They sum
/// to the print's own log move, so a consumer can difference `print` across
/// ticks and check the split rather than trust it.
///
/// `absorbed` carries the circuit breaker as well as the book, because the
/// breaker lands between the book's last trade and what is published. On a
/// halted name the breaker is what absorbed the shock, and booking it to the
/// book would be the more flattering of two wrong answers.
///
/// **The counterfactual.** `unbounded_print` is what the same tick printed
/// when it was settled a second time against every resting level, under the
/// same four uniforms and from the same book state. `liquidity_share` is
/// `log(print / unbounded_print) / log(print / the last print)`: how far the
/// depth bound moved the print, as a multiple of the move the print made.
/// Both columns are absent unless `Engine.settle_depth_counterfactual(True)`
/// was set before the run, and the schema metadata says which case a reader
/// is holding.
///
/// **The share is normally negative.** The bound TRUNCATES a walk: an order
/// that exhausts a shallow book stops there, while against every resting
/// level it keeps filling and prints further from where it started. So the
/// real print sits between the last print and the unbounded print and the
/// ratio comes out below zero. On the roster and seeds this table's tests
/// name, 1,409 of the 1,516 rows where the bound moved a print are negative.
///
/// Read it through the identity it satisfies: the unbounded book's move away
/// from the last print is `1 - share` times the printed move, so a share of
/// -1 means the deeper book would have moved the price twice as far. Nothing
/// bounds it by one. It is exactly zero where the two prints coincide, which
/// is most rows, and NaN where the print did not move and the deeper book
/// would have moved it, so filter the NaN before taking a mean. A mean over
/// the signed column is near zero because up moves and down moves cancel;
/// the useful statistic is taken over the rows where the bound moved a print.
///
/// # What it cannot say
///
/// The counterfactual prints one tick from the real state. It does not say
/// what a deeper book would have done to the NEXT tick, because the maker
/// inventory it would have left is discarded and the tick after this one is
/// the one that actually ran. A study wanting the compounded answer runs two
/// markets, not one market and an arm.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DepthColumns {
    /// The depth counterfactual ran for every session of every day served,
    /// so `unbounded_print` and `liquidity_share` are here.
    Present,
    /// It did not run, so the two columns are absent.
    Absent,
    /// It ran for some of a day's sessions and not others. The day's columns
    /// are shorter than the day, so they are dropped rather than served with
    /// a gap in them, and the caveat says which case a reader is holding.
    PartialDay,
}

/// The schema, and the one metadata key it carries.
///
/// # One key, because the metadata order is not ours to fix
///
/// `arrow::Schema::metadata` is a `std::collections::HashMap`, and the IPC
/// writer serialises it in that map's iteration order. `RandomState` reseeds
/// per map, so two writes of one table in ONE process produced different
/// footer bytes and two different digests -- measured, not reasoned about.
/// Nothing on this side can supply a hasher, because the field's type belongs
/// to `arrow`.
///
/// So the map holds exactly one entry, where iteration order cannot vary.
/// That costs nothing, because the two keys it used to carry beside the
/// caveat were both derivable from the schema itself: a consumer asking
/// whether the arm ran reads the column list, which is the more reliable
/// answer anyway.
pub fn prints_schema(depth: DepthColumns) -> SchemaRef {
    let mut fields = vec![
        Field::new("day", DataType::UInt32, false),
        Field::new("tick", DataType::UInt32, false),
        Field::new("instrument_id", DataType::UInt32, false),
        Field::new("print", DataType::Float64, false),
        Field::new("model_price", DataType::Float64, false),
        Field::new("shock", DataType::Float64, false),
        Field::new("absorbed", DataType::Float64, false),
        Field::new("clamp", DataType::Float64, false),
    ];
    if depth == DepthColumns::Present {
        fields.push(Field::new("unbounded_print", DataType::Float64, false));
        fields.push(Field::new("liquidity_share", DataType::Float64, false));
    }
    // Computed from the state the caller is actually in, so the caveat
    // cannot describe a table this is not. A retyped sentence per branch is
    // how a caveat becomes false.
    let absorbed = "absorbed is measured to the printed price, so it carries \
                    the circuit breaker as well as the book. clamp is the \
                    breaker's own part and book = absorbed - clamp is the \
                    book's. Read them apart: on a halted print the two pull \
                    opposite ways and often cancel exactly, so absorbed alone \
                    reads zero on a name the breaker had just moved. ";
    let caveat = match depth {
        DepthColumns::Present => format!(
            "{absorbed}unbounded_print is the same tick settled against every \
             resting level, under the same draws and from the same book \
             state; it is one tick from the real state and says nothing \
             about the next one. liquidity_share is NEGATIVE where the depth \
             bound truncated a walk, which is most rows that carry one, it \
             is not bounded by one, and it is NaN where the print did not \
             move. The unbounded move is (1 - liquidity_share) times the \
             printed move."
        ),
        DepthColumns::Absent => format!(
            "{absorbed}The depth counterfactual did not run, so \
             unbounded_print and liquidity_share are absent. Set \
             Engine.settle_depth_counterfactual(True) before the run to get \
             them."
        ),
        DepthColumns::PartialDay => format!(
            "{absorbed}The depth counterfactual was switched part way \
             through a day, so that day recorded fewer counterfactual values \
             than it has rows. unbounded_print and liquidity_share are \
             dropped for the whole day rather than served with a gap in \
             them. Set Engine.settle_depth_counterfactual before the first \
             session of a day."
        ),
    };
    let metadata = std::collections::HashMap::from([("caveat".to_string(), caveat)]);
    Arc::new(Schema::new_with_metadata(fields, metadata))
}

/// Build the `prints` batch from a session's buffers.
///
/// `depth` says which of the three shapes to build, and the caller decides
/// it, because only the caller can see whether every day it is serving
/// agrees. A `Present` state with a buffer shorter than the table is an
/// error rather than a pad.
#[allow(clippy::too_many_arguments)]
pub fn prints_batch(
    day: u32,
    ticks: usize,
    instruments: usize,
    prints: &[f64],
    model_price: &[f64],
    shock: &[f64],
    absorbed: &[f64],
    clamp: &[f64],
    unbounded_print: &[f64],
    liquidity_share: &[f64],
    depth: DepthColumns,
) -> Result<RecordBatch, String> {
    let rows = ticks * instruments;
    for (name, len) in [
        ("print", prints.len()),
        ("model_price", model_price.len()),
        ("shock", shock.len()),
        ("absorbed", absorbed.len()),
        ("clamp", clamp.len()),
    ] {
        if len < rows {
            return Err(format!(
                "{name} buffer shorter than {ticks} ticks x {instruments} instruments"
            ));
        }
    }
    // The caller decides the state; this only refuses a state the buffers
    // cannot support, because a short column served as a full one would put
    // a real value on the wrong row for the rest of the day.
    if depth == DepthColumns::Present
        && (unbounded_print.len() < rows || liquidity_share.len() < rows)
    {
        return Err(format!(
            "counterfactual buffer shorter than {ticks} ticks x {instruments} instruments"
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

    let mut columns: Vec<ArrayRef> = vec![
        Arc::new(UInt32Array::from(day_col)),
        Arc::new(UInt32Array::from(tick_col)),
        Arc::new(UInt32Array::from(id_col)),
        Arc::new(Float64Array::from(prints[..rows].to_vec())),
        Arc::new(Float64Array::from(model_price[..rows].to_vec())),
        Arc::new(Float64Array::from(shock[..rows].to_vec())),
        Arc::new(Float64Array::from(absorbed[..rows].to_vec())),
        Arc::new(Float64Array::from(clamp[..rows].to_vec())),
    ];
    if depth == DepthColumns::Present {
        columns.push(Arc::new(Float64Array::from(unbounded_print[..rows].to_vec())));
        columns.push(Arc::new(Float64Array::from(liquidity_share[..rows].to_vec())));
    }
    RecordBatch::try_new(prints_schema(depth), columns).map_err(|e| e.to_string())
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
#[pyclass(name = "ArrowStream", module = "tradefloor._core")]
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
        // The universe's remembered stress, in VIX points above the crisis
        // threshold. Exposed because it is the market's REGIME as the model
        // actually holds it -- not an estimate of the regime, the thing
        // itself. A regime detector can be scored against this the way an
        // attribution model is scored against `truth`, which is the check
        // no real market can offer. Zero under any preset with the memory
        // disabled, which is every preset before pt-v4.
        Field::new("universe_stress", DataType::Float64, false),
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
    pub universe_stress: f64,
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
        Arc::new(Float64Array::from(rows.iter().map(|r| r.universe_stress).collect::<Vec<_>>())),
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
        // The join key. `bars`, `truth` and `book` are keyed on a WITHIN-DAY
        // tick; `step` is a global counter, so without this column the fills
        // table cannot be joined to the tape from the data alone -- it would
        // need `steps_per_day` and `ticks_per_step`, which appear in no table.
        // A columnar contract whose tables do not join is not a contract.
        Field::new("tick", DataType::UInt32, false),
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
    tick: Vec<u32>,
    instrument_id: Vec<u32>,
    quantity: Vec<f64>,
    price: Vec<f64>,
    worst_price: Vec<f64>,
    notional: Vec<f64>,
) -> Result<RecordBatch, String> {
    let n = day.len();
    for (name, len) in [
        ("step", step.len()),
        ("tick", tick.len()),
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
        Arc::new(UInt32Array::from(tick)),
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
    tick: Vec<u32>,
    instrument_id: Vec<u32>,
    quantity: Vec<f64>,
    price: Vec<f64>,
    worst_price: Vec<f64>,
    notional: Vec<f64>,
) -> PyResult<PyArrowStream> {
    let batch = fills_batch(
        day, step, tick, instrument_id, quantity, price, worst_price, notional,
    )
    .map_err(arrow_err)?;
    Ok(PyArrowStream::new("fills", fills_schema(), vec![batch]))
}

/// One recorded day, kept raw so grain stays a read-time decision.
///
/// Storing the buffers rather than a pre-built batch costs the same memory and
/// buys the ability to ask for tick, N-minute or day bars from one recording.
/// Building the batch is cheap; re-running the day to change grain is not.
#[derive(Debug, Clone)]
pub struct RecordedDay {
    pub day: u32,
    pub ticks: usize,
    pub instruments: usize,
    pub prices: Vec<f64>,
    pub volumes: Vec<f64>,
    /// The price each instrument's SESSION opened at, before its first
    /// tick: the engine's `open` mark, taken at `open_market`. One per
    /// instrument, or EMPTY when the recording predates the field, in which
    /// case the day bar falls back to the first print. `prices` holds the
    /// print AFTER each tick, so without this the day bar's open was the
    /// first tick's close, one tick of movement presented as an opening
    /// price (issue #179). Stored rather than derived from the previous
    /// close, because an embedder may write prices between sessions and a
    /// derived open would miss that gap.
    pub opens: Vec<f64>,
    pub mispricing: Vec<f64>,
    pub fundamental: Vec<f64>,
    pub anchor: Vec<f64>,
    pub components: [Vec<f64>; 10],
    /// The print decomposition. See [`prints_schema`].
    pub shock: Vec<f64>,
    pub absorbed: Vec<f64>,
    pub clamp: Vec<f64>,
    /// The depth counterfactual, EMPTY on a day that ran without it. A day
    /// carries its own answer because the arm can be switched between days,
    /// and a table that reported one day's setting for all of them would be
    /// wrong for the rest.
    pub unbounded_print: Vec<f64>,
    pub liquidity_share: Vec<f64>,
}

/// `bars` at a coarser grain: real OHLCV.
///
/// At tick grain a bar is the print and open/high/low would simply repeat
/// close, which is why the tick schema omits them. Once ticks are bucketed
/// they carry real information, so the coarse schema is wider rather than the
/// same columns downsampled.
pub fn ohlc_schema() -> SchemaRef {
    Arc::new(Schema::new(vec![
        Field::new("day", DataType::UInt32, false),
        Field::new("bar", DataType::UInt32, false),
        Field::new("instrument_id", DataType::UInt32, false),
        Field::new("open", DataType::Float64, false),
        Field::new("high", DataType::Float64, false),
        Field::new("low", DataType::Float64, false),
        Field::new("close", DataType::Float64, false),
        Field::new("volume", DataType::Float64, false),
    ]))
}

/// Bucket one day's ticks into OHLCV bars.
///
/// `bucket` is ticks per bar; a bucket at or beyond the day's length produces
/// exactly one bar, which is the day-grain case.
///
/// The final bucket is kept even when short. Dropping a partial bar would
/// silently discard the end of every session whose length is not a multiple of
/// the bucket — including the close, which is the single most-used price in
/// the table.
pub fn ohlc_batch(day: &RecordedDay, bucket: usize) -> Result<RecordBatch, String> {
    if bucket == 0 {
        return Err("bucket must be at least one tick".to_string());
    }
    let n = day.instruments;
    if n == 0 || day.ticks == 0 {
        return RecordBatch::try_new(
            ohlc_schema(),
            vec![
                Arc::new(UInt32Array::from(Vec::<u32>::new())),
                Arc::new(UInt32Array::from(Vec::<u32>::new())),
                Arc::new(UInt32Array::from(Vec::<u32>::new())),
                Arc::new(Float64Array::from(Vec::<f64>::new())),
                Arc::new(Float64Array::from(Vec::<f64>::new())),
                Arc::new(Float64Array::from(Vec::<f64>::new())),
                Arc::new(Float64Array::from(Vec::<f64>::new())),
                Arc::new(Float64Array::from(Vec::<f64>::new())),
            ],
        )
        .map_err(|e| e.to_string());
    }

    let bars = day.ticks.div_ceil(bucket);
    let rows = bars * n;
    let (mut day_c, mut bar_c, mut id_c) = (
        Vec::with_capacity(rows),
        Vec::with_capacity(rows),
        Vec::with_capacity(rows),
    );
    let (mut o, mut h, mut l, mut c, mut v) = (
        Vec::with_capacity(rows),
        Vec::with_capacity(rows),
        Vec::with_capacity(rows),
        Vec::with_capacity(rows),
        Vec::with_capacity(rows),
    );

    for bar in 0..bars {
        let first = bar * bucket;
        let last = core::cmp::min(first + bucket, day.ticks);
        for i in 0..n {
            // The first bar of a day opens at the SESSION's open, the price
            // the day started at, which the recording carries in `opens`; a
            // later bar opens at its first print, the exchange convention
            // for an intraday bar. The session open is a print too, the one
            // the day opened on, so it belongs in the first bar's range.
            let session_open = if first == 0 && day.opens.len() == n {
                day.opens[i]
            } else {
                f64::NAN
            };
            let mut open = session_open;
            let mut high = if session_open.is_nan() { f64::NEG_INFINITY } else { session_open };
            let mut low = if session_open.is_nan() { f64::INFINITY } else { session_open };
            let mut close = f64::NAN;
            let mut volume = 0.0;
            for t in first..last {
                let price = day.prices[t * n + i];
                if t == first && open.is_nan() {
                    open = price;
                }
                // `>` and `<` rather than a max/min helper: NaN must not
                // silently win a comparison and become the high of a bar.
                if price > high {
                    high = price;
                }
                if price < low {
                    low = price;
                }
                close = price;
                volume += day.volumes[t * n + i];
            }
            day_c.push(day.day);
            bar_c.push(bar as u32);
            id_c.push(i as u32);
            o.push(open);
            h.push(high);
            l.push(low);
            c.push(close);
            v.push(volume);
        }
    }

    RecordBatch::try_new(
        ohlc_schema(),
        vec![
            Arc::new(UInt32Array::from(day_c)),
            Arc::new(UInt32Array::from(bar_c)),
            Arc::new(UInt32Array::from(id_c)),
            Arc::new(Float64Array::from(o)),
            Arc::new(Float64Array::from(h)),
            Arc::new(Float64Array::from(l)),
            Arc::new(Float64Array::from(c)),
            Arc::new(Float64Array::from(v)),
        ],
    )
    .map_err(|e| e.to_string())
}

/// `book`: one row per (tick, instrument, side, level).
///
/// # Why this one is opt-in and the others are not
///
/// Arithmetic, not preference. A hundred names at 390 ticks with ten levels a
/// side is 780,000 rows per day per side -- roughly 1.5 million rows daily
/// against 39,000 for `bars`. Recording depth by default would make every run
/// forty times more expensive to serve a question most runs never ask.
///
/// So the caller chooses when to snapshot and how deep. Nothing samples on
/// their behalf, because a sampling rate baked into the engine is a modelling
/// decision disguised as a default.
///
/// `side` is 0 for bids and 1 for asks. An integer rather than a string
/// because it repeats on every row, and at these counts the difference is the
/// difference between a column and a memory problem -- the same reason
/// `instrument_id` is an index.
pub fn book_schema() -> SchemaRef {
    Arc::new(Schema::new(vec![
        Field::new("day", DataType::UInt32, false),
        Field::new("tick", DataType::UInt32, false),
        Field::new("instrument_id", DataType::UInt32, false),
        Field::new("side", DataType::UInt32, false),
        Field::new("level", DataType::UInt32, false),
        Field::new("price", DataType::Float64, false),
        Field::new("size", DataType::Float64, false),
    ]))
}

/// One row of recorded depth.
#[derive(Debug, Clone, Copy)]
pub struct BookRow {
    pub day: u32,
    pub tick: u32,
    pub instrument_id: u32,
    pub side: u32,
    pub level: u32,
    pub price: f64,
    pub size: f64,
}

pub fn book_batch(rows: &[BookRow]) -> Result<RecordBatch, String> {
    let columns: Vec<ArrayRef> = vec![
        Arc::new(UInt32Array::from(rows.iter().map(|r| r.day).collect::<Vec<_>>())),
        Arc::new(UInt32Array::from(rows.iter().map(|r| r.tick).collect::<Vec<_>>())),
        Arc::new(UInt32Array::from(rows.iter().map(|r| r.instrument_id).collect::<Vec<_>>())),
        Arc::new(UInt32Array::from(rows.iter().map(|r| r.side).collect::<Vec<_>>())),
        Arc::new(UInt32Array::from(rows.iter().map(|r| r.level).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.price).collect::<Vec<_>>())),
        Arc::new(Float64Array::from(rows.iter().map(|r| r.size).collect::<Vec<_>>())),
    ];
    RecordBatch::try_new(book_schema(), columns).map_err(|e| e.to_string())
}
