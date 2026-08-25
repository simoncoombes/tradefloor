//! Order-book bindings, behind the `python` feature.
//!
//! Split from `python.rs` because that file is already long and this is a
//! self-contained surface.

#![allow(unexpected_cfgs, clippy::useless_conversion)]

use pyo3::prelude::*;

use crate::order_book::Side;
use crate::python::ValidationError;

pyo3::create_exception!(
    _core,
    OrderError,
    pyo3::exceptions::PyValueError,
    "Raised when an order is rejected. Distinct from ValidationError because \
     the causes differ in kind: a malformed scenario is a setup mistake, made \
     once, whereas a rejected order is a market rule hit during a run -- a \
     market order outside regular hours, or a size the book will not accept. \
     Callers reasonably handle the two differently."
);

fn parse_side(side: &str) -> PyResult<Side> {
    match side {
        "buy" => Ok(Side::Buy),
        "sell" => Ok(Side::Sell),
        other => Err(ValidationError::new_err(format!(
            "side must be \"buy\" or \"sell\", got {other:?}"
        ))),
    }
}

fn side_name(side: Side) -> &'static str {
    match side {
        Side::Buy => "buy",
        Side::Sell => "sell",
    }
}

/// Sizes and prices must be finite and strictly positive.
///
/// `!(v > 0.0)` rather than `v <= 0.0`: the negated form also rejects NaN,
/// which the positive form silently admits. The core uses the same idiom in
/// the same places, for the same reason.
fn check_size(name: &str, value: f64) -> PyResult<()> {
    if !(value > 0.0) || !value.is_finite() {
        return Err(ValidationError::new_err(format!(
            "{name} must be finite and greater than zero, got {value}"
        )));
    }
    Ok(())
}

/// One executed trade.
#[pyclass(name = "Fill", module = "pretium._core", frozen, get_all)]
#[derive(Debug, Clone)]
pub struct PyFill {
    /// Always the RESTING order's price, never the incoming one. That is what
    /// makes size costly: a large taker walks the book paying each maker's
    /// price in turn.
    pub price: f64,
    pub quantity: f64,
    pub maker_order_id: String,
    pub maker_id: String,
    pub taker_id: String,
    pub taker_side: String,
}

#[pymethods]
impl PyFill {
    fn __repr__(&self) -> String {
        format!(
            "Fill(price={}, quantity={}, taker_side={:?})",
            self.price, self.quantity, self.taker_side
        )
    }
}

/// The outcome of submitting an order.
#[pyclass(name = "MatchResult", module = "pretium._core", frozen, get_all)]
#[derive(Debug, Clone)]
pub struct PyMatchResult {
    pub fills: Vec<PyFill>,
    /// Shares that could not be filled. Zero when a remainder was posted.
    pub unfilled: f64,
    /// Volume-weighted average across the fills, or None if nothing filled.
    ///
    /// This is the number showing that slippage is emergent: compare it to the
    /// mid price before submission and the gap is levels consumed, not a
    /// coefficient applied.
    pub average_price: Option<f64>,
    /// Id of the resting remainder, when one was posted.
    pub resting_order_id: Option<String>,
}

#[pymethods]
impl PyMatchResult {
    fn __repr__(&self) -> String {
        format!(
            "MatchResult(fills={}, unfilled={}, average_price={:?})",
            self.fills.len(),
            self.unfilled,
            self.average_price
        )
    }
}

/// One aggregated price level.
#[pyclass(name = "PriceLevel", module = "pretium._core", frozen, get_all)]
#[derive(Debug, Clone, Copy)]
pub struct PyPriceLevel {
    pub price: f64,
    pub quantity: f64,
    pub orders: u32,
}

#[pymethods]
impl PyPriceLevel {
    fn __repr__(&self) -> String {
        format!(
            "PriceLevel(price={}, quantity={}, orders={})",
            self.price, self.quantity, self.orders
        )
    }
}

/// What sweeping a given size would cost, without executing it.
#[pyclass(name = "SweepCost", module = "pretium._core", frozen, get_all)]
#[derive(Debug, Clone, Copy)]
pub struct PySweepCost {
    pub average_price: f64,
    pub worst_price: f64,
    pub filled: f64,
}

#[pymethods]
impl PySweepCost {
    fn __repr__(&self) -> String {
        format!(
            "SweepCost(average_price={}, worst_price={}, filled={})",
            self.average_price, self.worst_price, self.filled
        )
    }
}

/// A central limit order book with price-time priority.
///
/// # Why this exists rather than a slippage coefficient
///
/// Real exchanges do not compute a fill price from a formula; they match two
/// orders. A large order here pays worse prices because it CONSUMED the levels
/// above it, not because a coefficient said large orders cost more. Market
/// impact is therefore emergent, and the displayed depth is the executable
/// depth -- the ladder you can read is the ladder you trade against.
///
/// # Validation differs from the core, on purpose
///
/// The underlying implementation returns an empty result for a non-positive or
/// NaN size, reproducing the reference exactly. This binding raises instead. A
/// silently ignored order is the worst failure available: the caller believes
/// they traded, the book disagrees, and nothing reports the disagreement.
#[pyclass(name = "OrderBook", module = "pretium._core")]
pub struct PyOrderBook {
    inner: crate::order_book::OrderBook,
}

impl PyOrderBook {
    /// Wrap a book the engine built. Not a `#[new]`: a caller cannot
    /// construct one of these from nothing, because its depth comes from an
    /// instrument's state rather than from arguments.
    pub fn from_core(inner: crate::order_book::OrderBook) -> Self {
        Self { inner }
    }
}

#[pymethods]
impl PyOrderBook {
    #[new]
    #[pyo3(signature = (company_id, last_price = None))]
    fn new(company_id: &str, last_price: Option<f64>) -> PyResult<Self> {
        if let Some(p) = last_price {
            check_size("last_price", p)?;
        }
        Ok(Self {
            inner: crate::order_book::OrderBook::new(company_id, last_price),
        })
    }

    /// Rest a limit order on the book. Returns its id.
    #[pyo3(signature = (side, price, quantity, *, owner, order_id = None))]
    fn post_limit(
        &mut self,
        side: &str,
        price: f64,
        quantity: f64,
        owner: &str,
        order_id: Option<String>,
    ) -> PyResult<String> {
        let s = parse_side(side)?;
        check_size("price", price)?;
        check_size("quantity", quantity)?;
        self.inner
            .post_limit(s, price, quantity, owner, order_id)
            .map(|o| o.id)
            .ok_or_else(|| OrderError::new_err("the book rejected the limit order"))
    }

    /// Submit an order against the book.
    ///
    /// `limit_price=None` is a market order. Market orders never rest: an
    /// unfilled remainder is simply unfilled, which is why `post_remainder` is
    /// ignored for them.
    #[pyo3(signature = (
        side, quantity, *, taker = "taker",
        limit_price = None, post_remainder = false, order_id = None
    ))]
    fn submit(
        &mut self,
        side: &str,
        quantity: f64,
        taker: &str,
        limit_price: Option<f64>,
        post_remainder: bool,
        order_id: Option<String>,
    ) -> PyResult<PyMatchResult> {
        let s = parse_side(side)?;
        check_size("quantity", quantity)?;
        if let Some(p) = limit_price {
            check_size("limit_price", p)?;
        }
        let r = self.inner.submit(
            s,
            quantity,
            taker,
            crate::order_book::SubmitOptions {
                limit_price,
                post_remainder,
                order_id,
            },
        );
        Ok(PyMatchResult {
            fills: r
                .fills
                .into_iter()
                .map(|f| PyFill {
                    price: f.price,
                    quantity: f.quantity,
                    maker_order_id: f.maker_order_id,
                    maker_id: f.maker_id,
                    taker_id: f.taker_id,
                    taker_side: side_name(f.taker_side).to_string(),
                })
                .collect(),
            unfilled: r.unfilled,
            average_price: r.average_price,
            resting_order_id: r.resting.map(|o| o.id),
        })
    }

    /// Append a level to the END of one side, skipping the sorted insert.
    ///
    /// # Callers MUST append in worsening price order
    ///
    /// This is a bulk-construction fast path for building a ladder that is
    /// already sorted. It does NOT sort, so appending out of order leaves the
    /// book silently mis-ordered and every later match wrong. Use
    /// [`OrderBook::post_limit`] for anything whose position is not already
    /// known — it sorts, and costs nothing until a ladder is thousands deep.
    ///
    /// It also differs from `post_limit` at the depth cap, which is easy to
    /// miss and is not merely an optimisation detail: at `MAX_DEPTH_PER_SIDE`
    /// this REFUSES and consumes no sequence number, whereas `post_limit`
    /// accepts and truncates the far end. Substituting one for the other
    /// therefore desynchronises order ids once a side is full.
    ///
    /// Returns the new order's id, or None when the side is already full.
    #[pyo3(signature = (side, price, quantity, *, owner))]
    fn append_maker_level(
        &mut self,
        side: &str,
        price: f64,
        quantity: f64,
        owner: &str,
    ) -> PyResult<Option<String>> {
        let s = parse_side(side)?;
        check_size("price", price)?;
        check_size("quantity", quantity)?;
        Ok(self
            .inner
            .append_maker_level(s, price, quantity, owner)
            .map(|o| o.id))
    }

    /// What sweeping `quantity` would cost, without executing it.
    fn sweep_cost(&self, side: &str, quantity: f64) -> PyResult<Option<PySweepCost>> {
        let s = parse_side(side)?;
        check_size("quantity", quantity)?;
        Ok(self.inner.sweep_cost(s, quantity).map(|c| PySweepCost {
            average_price: c.average_price,
            worst_price: c.worst_price,
            filled: c.filled,
        }))
    }

    /// Aggregated levels on one side, best first.
    #[pyo3(signature = (side, max_levels = 10))]
    fn price_levels(&self, side: &str, max_levels: usize) -> PyResult<Vec<PyPriceLevel>> {
        let s = parse_side(side)?;
        Ok(self
            .inner
            .price_levels(s, max_levels)
            .into_iter()
            .map(|l| PyPriceLevel {
                price: l.price,
                quantity: l.quantity,
                orders: l.orders,
            })
            .collect())
    }

    /// Total resting quantity on one side.
    fn depth(&self, side: &str) -> PyResult<f64> {
        Ok(self.inner.depth(parse_side(side)?))
    }

    fn cancel_order(&mut self, order_id: &str) -> bool {
        self.inner.cancel_order(order_id)
    }

    fn cancel_all_for(&mut self, owner_id: &str) -> u32 {
        self.inner.cancel_all_for(owner_id)
    }

    #[getter]
    fn best_bid(&self) -> Option<f64> {
        self.inner.best_bid()
    }

    #[getter]
    fn best_ask(&self) -> Option<f64> {
        self.inner.best_ask()
    }

    #[getter]
    fn mid_price(&self) -> Option<f64> {
        self.inner.mid_price()
    }

    #[getter]
    fn spread(&self) -> Option<f64> {
        self.inner.spread()
    }

    #[getter]
    fn company_id(&self) -> String {
        self.inner.company_id.clone()
    }

    fn __repr__(&self) -> String {
        format!(
            "OrderBook({:?}, bid={:?}, ask={:?})",
            self.inner.company_id,
            self.inner.best_bid(),
            self.inner.best_ask()
        )
    }
}
