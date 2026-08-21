//! # pretium
//!
//! A deterministic market simulator with a real limit order book.
//!
//! ## Determinism
//!
//! The library ships its own transcendental maths rather than calling the
//! platform's libm, so the same seed and the same inputs are *designed* to
//! produce bit-identical output on Linux, macOS and Windows. That property is
//! argued from construction and enforced by a source-level ban on `std`
//! transcendentals; it is not yet verified by a cross-platform test, and the
//! release gate that will verify it is not built. Until then the honest
//! phrasing is "designed to be", not "verified to be".
//!
//! ## Units
//!
//! Every rate crossing this boundary is **fractional**: `0.045` means 4.5%.
//! The simulation core denominates rates in percent internally, and the
//! conversion happens exactly once, at this boundary, in [`units`] -- so
//! there is a single auditable place where a factor of 100 can be wrong.

use pyo3::prelude::*;

mod units;

pub use units::{fraction_to_percent, percent_to_fraction};

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
    inner: mc_engine_core::GameRng,
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
            inner: mc_engine_core::GameRng::new(seed, sequence),
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
    units::check_rate(name, fraction)
        .map_err(pyo3::exceptions::PyValueError::new_err)
}

#[pymodule]
fn pretium(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyGameRng>()?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(check_rate, m)?)?;
    m.add("__version__", env!("CARGO_PKG_VERSION"))?;
    Ok(())
}
