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
/// many normals have been drawn is part of the generator's state: Box-Muller
/// produces a PAIR from two uniforms and keeps the second. So two normals cost
/// two uniform draws, not four, and the second one advances the stream not at
/// all — a uniform drawn after one normal and after two normals is the same
/// value. Draw accounting is therefore not one-uniform-per-value, which
/// matters to anyone reasoning about where a stream is.
#[pyclass(name = "GameRng", module = "pretium._core")]
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
    _core,
    ValidationError,
    pyo3::exceptions::PyValueError,
    "Raised when construction input is rejected at the boundary. Subclasses \
     ValueError, so `except ValueError` keeps working while the specific type \
     is available to callers who want it. The taxonomy is deliberately tiny: \
     a simulator that invents an error type per field teaches users to catch \
     Exception, which catches the bugs too."
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

/// The compiled extension.
///
/// Named `_core` because a Python package wraps it: `pretium/__init__.py`
/// re-exports everything here and adds the parts that are better written in
/// Python than in Rust -- JSON round-trips, and multiprocessing for seed
/// sweeps. Forcing those into the extension would mean hand-rolling JSON
/// escaping and reimplementing a process pool, both badly.
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyGameRng>()?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(check_rate, m)?)?;
    m.add_function(wrap_pyfunction!(fair_value, m)?)?;
    m.add_function(wrap_pyfunction!(sectors, m)?)?;
    m.add_function(wrap_pyfunction!(step_mispricing_daily, m)?)?;
    m.add_function(wrap_pyfunction!(apply_mispricing, m)?)?;
    m.add_function(wrap_pyfunction!(characteristic_root_moduli, m)?)?;
    m.add_function(wrap_pyfunction!(crowd_adjusted_root_moduli, m)?)?;
    m.add_function(wrap_pyfunction!(impulse_response, m)?)?;
    m.add_function(wrap_pyfunction!(model_preset, m)?)?;
    m.add_class::<PyMispricingState>()?;
    m.add_class::<PyFairValue>()?;
    m.add_class::<crate::python_book::PyOrderBook>()?;
    m.add_class::<crate::python_book::PyFill>()?;
    m.add_class::<crate::python_book::PyMatchResult>()?;
    m.add_class::<crate::python_book::PyPriceLevel>()?;
    m.add_class::<crate::python_book::PySweepCost>()?;
    m.add_class::<crate::python_engine::PyEngine>()?;
    m.add_class::<crate::python_engine::PyInstrument>()?;
    m.add_class::<crate::python_engine::PyMacro>()?;
    m.add_class::<crate::python_engine::PyTickResult>()?;
    m.add_class::<crate::python_engine::PyNews>()?;
    m.add_class::<crate::python_engine::PyNewsImpact>()?;
    m.add_class::<crate::python_arrow::PyArrowStream>()?;
    m.add_function(wrap_pyfunction!(crate::python_arrow::fills_stream, m)?)?;
    m.add_function(wrap_pyfunction!(crate::python_engine::market_status, m)?)?;
    m.add_function(wrap_pyfunction!(crate::python_engine::random_instruments, m)?)?;
    m.add("ValidationError", m.py().get_type_bound::<ValidationError>())?;
    m.add(
        "OrderError",
        m.py().get_type_bound::<crate::python_book::OrderError>(),
    )?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}

/// A fair-value result and its decomposition.
///
/// The decomposition is the point, not a debugging aid: the simulator knows
/// the multiple it applied and why, which no historical-data backtest can
/// tell you.
#[pyclass(name = "FairValue", module = "pretium._core", frozen, get_all)]
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

/// The daily log-mispricing state.
///
/// `s` is the log deviation of price from fair value, and `s_prev` is
/// yesterday's, which the momentum term needs. Immutable: a step returns a new
/// state rather than mutating, so a trajectory is a list of values you can
/// keep rather than a thing you have to copy defensively.
#[pyclass(name = "MispricingState", module = "pretium._core", frozen, get_all)]
#[derive(Debug, Clone, Copy)]
pub struct PyMispricingState {
    /// Current log-mispricing.
    pub s: f64,
    /// Previous day's value, the momentum term's other operand.
    pub s_prev: f64,
}

#[pymethods]
impl PyMispricingState {
    /// Build a state.
    ///
    /// With `s_prev` omitted this is the ordinary constructor: the value is
    /// clamped to the mispricing cap and `s_prev` is set equal to it, so a
    /// fresh state has zero momentum.
    ///
    /// Passing `s_prev` builds the pair VERBATIM, without clamping. That is
    /// for resuming a trajectory you already hold — mid-trajectory `s` and
    /// `s_prev` genuinely differ, and that difference IS the momentum term.
    /// Routing it through the clamping constructor would silently zero the
    /// momentum and produce a different path from the one being resumed.
    #[new]
    #[pyo3(signature = (s = 0.0, s_prev = None))]
    fn new(s: f64, s_prev: Option<f64>) -> PyResult<Self> {
        for (name, v) in [("s", Some(s)), ("s_prev", s_prev)] {
            if let Some(v) = v {
                if !v.is_finite() {
                    return Err(ValidationError::new_err(format!(
                        "{name} must be finite, got {v}"
                    )));
                }
            }
        }
        match s_prev {
            // Verbatim: an out-of-cap value is preserved and propagates into
            // s_prev exactly once, which is the reference behaviour.
            Some(prev) => Ok(Self { s, s_prev: prev }),
            None => {
                let st = crate::mispricing::create_mispricing_state(s);
                Ok(Self { s: st.s, s_prev: st.s_prev })
            }
        }
    }

    fn __repr__(&self) -> String {
        format!("MispricingState(s={}, s_prev={})", self.s, self.s_prev)
    }
}

/// One DAILY step of the mispricing process.
///
/// # This is not the process the engine's tick loop runs
///
/// Said plainly because the two are easy to conflate and produce different
/// trajectories. This function is the clean daily AR(2) step: a 60-day
/// half-life decay, a momentum term in yesterday's change, and a shock, all
/// applied once per day. The engine applies related ideas per TICK, at 1/390
/// of a day, with crowd feedback the daily step does not have. They are
/// different processes, not rounding variants of one another, and a
/// trajectory from one will not reproduce a trajectory from the other.
///
/// The daily step is the library's public model because it is what can be
/// characterised: its stationarity is provable in closed form via
/// [`characteristic_root_moduli`], not merely observed by simulating and
/// hoping. The tick variant is deliberately not exposed as a standalone
/// function; it is reachable only by running the engine, which is where it
/// belongs.
///
/// # Arguments
///
/// `innovation` is the GARCH-scaled, zero-mean daily innovation, already
/// sized by the caller and NOT clamped. `shock` is the day's summed
/// directional pressure -- news, net order flow, squeezes -- and IS clamped to
/// the daily shock cap. That asymmetry is deliberate: noise is sized by the
/// volatility model, whereas directional shocks are bounded so a single day
/// cannot dominate.
#[pyfunction]
#[pyo3(signature = (state, *, innovation = 0.0, shock = 0.0))]
fn step_mispricing_daily(
    state: &PyMispricingState,
    innovation: f64,
    shock: f64,
) -> PyResult<PyMispricingState> {
    for (name, v) in [("innovation", innovation), ("shock", shock)] {
        if !v.is_finite() {
            return Err(ValidationError::new_err(format!(
                "{name} must be finite, got {v}"
            )));
        }
    }
    let next = crate::mispricing::step_mispricing(
        &crate::mispricing::MispricingState { s: state.s, s_prev: state.s_prev },
        &crate::mispricing::MispricingInputs { innovation, shock },
    );
    Ok(PyMispricingState { s: next.s, s_prev: next.s_prev })
}

/// Price from fair value and mispricing: `fair_value * exp(s)`, floored.
///
/// The floor is a JavaScript-semantics max, so a NaN propagates rather than
/// being silently replaced by the floor. A negative fair value survives the
/// multiply and is then floored, so this never returns a negative price.
#[pyfunction]
fn apply_mispricing(fair_value: f64, s: f64) -> f64 {
    crate::mispricing::apply_mispricing(fair_value, s)
}

/// Moduli of the AR(2) characteristic roots.
///
/// Both strictly inside the unit circle means the process is stationary --
/// proven, not sampled. This is the analytic backbone of the claim that the
/// daily model cannot wander off, and it is why the daily step is the public
/// surface.
#[pyfunction]
#[pyo3(signature = (phi = None, theta = None))]
fn characteristic_root_moduli(phi: Option<f64>, theta: Option<f64>) -> (f64, f64) {
    crate::mispricing::characteristic_root_moduli(phi, theta)
}

/// The same, with the crowd-feedback term included.
#[pyfunction]
fn crowd_adjusted_root_moduli() -> (f64, f64) {
    crate::mispricing::crowd_adjusted_root_moduli()
}

/// Impulse response of the daily process over `horizon_days`.
#[pyfunction]
#[pyo3(signature = (horizon_days, phi = None, theta = None))]
fn impulse_response(horizon_days: i64, phi: Option<f64>, theta: Option<f64>) -> PyResult<Vec<f64>> {
    if horizon_days < 0 {
        return Err(ValidationError::new_err(format!(
            "horizon_days must be >= 0, got {horizon_days}"
        )));
    }
    Ok(crate::mispricing::impulse_response(horizon_days, phi, theta))
}

/// The model coefficients, as a named immutable preset.
///
/// Coefficients ship as a versioned preset rather than as constructor
/// keywords so that two published results can be compared: "pt-v1" names an
/// exact model, whereas forty tunable keyword arguments guarantee no two users
/// ran the same one.
///
/// Only LIVE coefficients appear. A preset listing knobs wired to nothing
/// would be a documentation lie of the kind this port has already had to
/// correct once.
#[pyfunction]
fn model_preset(py: Python<'_>) -> PyResult<PyObject> {
    use crate::mispricing as m;
    let d = pyo3::types::PyDict::new_bound(py);
    d.set_item("name", "pt-v1")?;
    d.set_item("mispricing_half_life_days", m::MISPRICING_HALF_LIFE_DAYS)?;
    d.set_item("mispricing_phi", m::MISPRICING_PHI)?;
    d.set_item("momentum_theta", m::MOMENTUM_THETA)?;
    d.set_item("mispricing_cap", m::MISPRICING_CAP)?;
    d.set_item("daily_shock_cap", m::DAILY_SHOCK_CAP)?;
    d.set_item("crowd_valuation_gain", m::CROWD_VALUATION_GAIN)?;
    d.set_item("crowd_momentum_gain", m::CROWD_MOMENTUM_GAIN)?;
    d.set_item("crowd_lean_cap", m::CROWD_LEAN_CAP)?;
    Ok(d.into())
}
