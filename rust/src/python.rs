//! Python bindings, behind the `python` feature.
//!
//! Feature-gated so the WASM consumer's build never compiles
//! PyO3. The same core serves both; only this file is Python-specific.

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
    crate::units::check_rate(name, fraction)
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
