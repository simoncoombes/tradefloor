//! Python bindings, behind the `python` feature.
//!
//! Feature-gated so the WASM consumer's build never compiles
//! PyO3. The same core serves both; only this file is Python-specific.

#![allow(unexpected_cfgs, clippy::useless_conversion)]
// Both lints fire only inside pyo3 0.22 macro expansions -- create_exception!
// and #[pyfunction] -- as a "gil-refs" cfg value this crate does not define,
// and an identity Into on PyErr. Silenced at the module because the
// alternative is reshaping hand-written code to satisfy a lint aimed at code
// the macros generate.

use pyo3::prelude::*;

/// Deterministic seeded random number generator (PCG32 + Box-Muller).
///
/// Two instances constructed with the same `(seed, sequence)` produce the
/// same sequence of draws, on any platform. Each instance owns its state
/// completely: constructing two of them in one process gives two independent
/// streams, and importing the module draws nothing.
///
/// The normal-draw path caches a Box-Muller spare, so the *parity* of how
/// many normals have been drawn is part of the generator's state. Drawing a
/// uniform between two normals therefore changes the second normal. This is
/// deliberate and matches the reference implementation.
#[pyclass(name = "GameRng", module = "pretium")]
pub struct PyGameRng {
    inner: crate::GameRng,
}

#[pymethods]
impl PyGameRng {
    /// Construct a generator.
    ///
    /// `seed` and `sequence` together select the stream. `sequence` is
    /// required rather than defaulted: two generators differing only by
    /// sequence are independent, and silently sharing a default would make
    /// "two independent streams" quietly false.
    #[new]
    fn new(seed: u32, sequence: u32) -> Self {
        Self {
            inner: crate::GameRng::new(seed, sequence),
        }
    }

    /// A uniform draw in [0, 1).
    fn next_float(&mut self) -> f64 {
        self.inner.next_f64()
    }

    /// A standard normal draw (mean 0, standard deviation 1).
    fn next_normal(&mut self) -> f64 {
        self.inner.next_normal()
    }

    /// A uniform integer in `[min, max]`, inclusive at both ends.
    fn next_int(&mut self, min: i64, max: i64) -> i64 {
        self.inner.next_int(min, max)
    }

    /// A boolean that is true with probability `p`.
    fn next_bool(&mut self, p: f64) -> bool {
        self.inner.next_bool(p)
    }

    fn __repr__(&self) -> String {
        "GameRng(...)".to_string()
    }
}

pyo3::create_exception!(
    pretium,
    ValidationError,
    pyo3::exceptions::PyValueError,
    "Raised when construction input is rejected at the boundary.

     Subclasses ValueError, so `except ValueError` keeps working while the      specific type is available for callers who want it. The taxonomy is      deliberately tiny: a simulator that invents error types for every field      teaches users to catch Exception."
);

/// The package version.
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Validate a fractional rate, raising `ValueError` if it looks wrong.
///
/// Exposed now, before the valuation surface that will call it, because the
/// fractional-units decision is only real if it is enforced somewhere a user
/// can see. The error names the percent-versus-fractional confusion
/// explicitly rather than reporting a bare range, because that confusion is
/// what an out-of-range rate almost always is.
#[pyfunction]
fn check_rate(name: &str, fraction: f64) -> PyResult<f64> {
    crate::units::check_rate(name, fraction).map_err(ValidationError::new_err)
}

#[pymodule]
fn pretium(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyGameRng>()?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(check_rate, m)?)?;
    m.add_function(wrap_pyfunction!(fair_value, m)?)?;
    m.add_function(wrap_pyfunction!(sectors, m)?)?;
    m.add_class::<PyFairValue>()?;
    m.add("ValidationError", m.py().get_type_bound::<ValidationError>())?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

/// A fair-value result and its decomposition.
///
/// The decomposition is the point, not a debugging aid: the simulator knows
/// the multiple it applied and why, which no historical-data backtest can
/// tell you.
#[pyclass(name = "FairValue", module = "pretium", frozen, get_all)]
#[derive(Debug, Clone, Copy)]
pub struct PyFairValue {
    /// Fair value per share.
    pub fair_value: f64,
    /// The multiple actually applied. Zero on the book-value path.
    pub target_pe: f64,
    /// The sector's anchor multiple, before adjustment.
    pub sector_anchor_pe: f64,
    /// Rate adjustment applied to the anchor.
    pub rate_adjustment: f64,
    /// QE adjustment applied to the anchor.
    pub qe_adjustment: f64,
    /// True when valued off book because EPS was not positive.
    pub book_value_path: bool,
}

#[pymethods]
impl PyFairValue {
    fn __repr__(&self) -> String {
        format!(
            "FairValue(fair_value={:.4}, target_pe={:.4}, book_value_path={})",
            self.fair_value, self.target_pe, self.book_value_path
        )
    }
}

/// Fair value per share from fundamentals and the macro state.
///
/// # Units
///
/// **Rates are fractional**: `corporate_bond_yield=0.052` means 5.2%. Two of
/// these five numbers are rates and are converted to the core's percent
/// denomination here, at the boundary; the other three are already fractional
/// in both and must NOT be scaled:
///
/// | argument | kind | converted? |
/// |---|---|---|
/// | `corporate_bond_yield` | rate | yes, x100 |
/// | `federal_funds_rate` | rate | yes, x100 |
/// | `revenue_growth` | growth fraction | no |
/// | `qe_pe_boost` | multiplier delta | no |
/// | `eps`, `book_value_per_share` | currency | no |
///
/// Getting that split wrong is silent: scaling `revenue_growth` by 100 turns
/// 22% growth into 2200% and produces a large, finite, entirely wrong
/// multiple. Hence the table, and hence the tests that pin each column.
///
/// # Absence versus zero
///
/// `corporate_bond_yield=None` falls through to the policy rate. A yield of
/// exactly `0.0` is a real observation and is used as-is. This is deliberately
/// the opposite of the sector anchor's behaviour, where a zero anchor falls
/// back to the default multiple.
///
/// # Negative EPS
///
/// Legal and meaningful. A loss-maker is valued off book value, and
/// `book_value_path` reports that it happened rather than leaving the caller
/// to infer it from a suspiciously round multiple.
#[pyfunction]
#[pyo3(signature = (
    *,
    eps = None,
    sector,
    revenue_growth = None,
    federal_funds_rate = 0.0,
    corporate_bond_yield = None,
    qe_pe_boost = None,
    book_value_per_share = None,
))]
#[allow(clippy::too_many_arguments)]
fn fair_value(
    // Optional wherever the core is optional. Flattening `Option<f64>` to a
    // defaulted `f64` here would erase the absence-versus-zero distinction,
    // which is load-bearing in both directions: an absent bond yield falls
    // through to the policy rate, while a yield of exactly zero is used
    // as-is, and a zero sector anchor falls back to the default multiple
    // while an absent one does the same. Same type, opposite rules -- the
    // binding must not decide which the caller meant.
    eps: Option<f64>,
    sector: &str,
    revenue_growth: Option<f64>,
    federal_funds_rate: f64,
    corporate_bond_yield: Option<f64>,
    qe_pe_boost: Option<f64>,
    book_value_per_share: Option<f64>,
) -> PyResult<PyFairValue> {
    // Boundary rejections carry the specific type; PyO3's own argument-type
    // errors remain plain TypeError/ValueError, which is correct -- those are
    // "you passed a string where a float goes", not "your scenario is invalid".
    use ValidationError as Rejected;

    // Keyword-only (note the leading `*` in the signature). Seven positional
    // floats would be the roster-column hazard again: every one is a number,
    // so transposing two would typecheck, run, and quietly value the company
    // wrong.
    let s = crate::sectors::by_key(sector).ok_or_else(|| {
        Rejected::new_err(format!(
            "unknown sector {sector:?}. Valid sectors: {}",
            crate::sectors::keys().join(", ")
        ))
    })?;

    // Finite floats everywhere, per the validation policy. The core
    // reproduces the TypeScript's NaN behaviour faithfully -- that is what
    // the golden vectors pin -- but a NaN admitted HERE propagates silently
    // into prices: a NaN fair value takes early returns in settlement, books
    // stop trading, and the user gets a quietly frozen market instead of an
    // error. So the core keeps its faithfulness and the boundary makes sure
    // nobody reaches it by accident.
    //
    // Rates are checked separately below because their error message can be
    // more specific. This catches the rest.
    for (name, value) in [
        ("eps", eps),
        ("book_value_per_share", book_value_per_share),
        ("revenue_growth", revenue_growth),
        ("qe_pe_boost", qe_pe_boost),
    ] {
        if let Some(v) = value {
            if !v.is_finite() {
                return Err(Rejected::new_err(format!(
                    "{name} must be finite, got {v}"
                )));
            }
        }
    }

    // Rates. Validated in FRACTIONAL form, before conversion, so the error
    // can say "pass 0.045 for 4.5%" in the units the caller actually used.
    crate::units::check_rate("federal_funds_rate", federal_funds_rate)
        .map_err(Rejected::new_err)?;
    if let Some(y) = corporate_bond_yield {
        crate::units::check_rate("corporate_bond_yield", y).map_err(Rejected::new_err)?;
    }

    let economy = crate::fair_value::EconomyValuationInputs {
        corporate_bond_yield: corporate_bond_yield.map(crate::units::fraction_to_percent),
        federal_funds_rate: crate::units::fraction_to_percent(federal_funds_rate),
        // NOT converted: a multiplier delta, fractional in both denominations.
        qe_pe_boost,
    };
    let company = crate::fair_value::CompanyValuationInputs {
        sector_avg_pe: Some(s.avg_pe),
        eps,
        book_value_per_share,
        // NOT converted: already a fraction in both denominations.
        revenue_growth,
    };

    let b = crate::fair_value::compute_fair_value(&company, &economy);
    Ok(PyFairValue {
        fair_value: b.fair_value,
        target_pe: b.target_pe,
        sector_anchor_pe: b.sector_anchor_pe,
        rate_adjustment: b.rate_adjustment,
        qe_adjustment: b.qe_adjustment,
        book_value_path: b.book_value_path,
    })
}

/// The twelve sector keys, in contractual declaration order.
#[pyfunction]
fn sectors() -> Vec<String> {
    crate::sectors::keys().iter().map(|s| s.to_string()).collect()
}
