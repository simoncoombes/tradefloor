//! `ModelParams` — the settable half of the model preset, on the Python
//! surface (PYTHON-API-DESIGN.md §3, CALIBRATION.md §5.1).
//!
//! ```python
//! eng = pt.Engine(seed=42, universe=u, model="pt-v1")          # default
//! custom = pt.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
//! eng = pt.Engine(seed=42, universe=u, model=custom)
//! eng.model_fingerprint    # "custom-3fb2c91a", never "pt-v1"
//! ```
//!
//! Immutable after construction (`frozen`), constructed only via
//! `from_preset` / `from_dict` — the fingerprint must not be able to lie.
//! Everything of substance lives in `crate::params`; this file is the
//! boundary.

#![allow(unexpected_cfgs)]

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::params::{settable_names, ModelParams};
use crate::python::ValidationError;

/// An immutable model coefficient set: a shipped preset, or a named
/// deviation from one.
#[pyclass(name = "ModelParams", module = "pretium._core", frozen)]
#[derive(Debug, Clone)]
pub struct PyModelParams {
    pub inner: ModelParams,
}

#[pymethods]
impl PyModelParams {
    /// Build from a shipped preset, with keyword overrides.
    ///
    /// `ModelParams.from_preset("pt-v1")` is the shipped model and
    /// fingerprints as `"pt-v1"`; any override that changes a bit
    /// fingerprints as `"custom-XXXXXXXX"`. Unknown names, non-finite
    /// values, the derived-bits coefficients (`mispricing_phi`,
    /// `s_phi_tick` — override `mispricing_half_life_days` instead) and
    /// the carried read-only surface are refused by name.
    #[staticmethod]
    #[pyo3(signature = (name = "pt-v1", **overrides))]
    fn from_preset(name: &str, overrides: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let mut params = ModelParams::preset(name).ok_or_else(|| {
            ValidationError::new_err(format!(
                "unknown model preset {name:?}. Shipped presets: {}",
                ModelParams::preset_names().join(", ")
            ))
        })?;
        if let Some(kwargs) = overrides {
            // Sorted for a deterministic application order. The overrides
            // commute — each writes one independent field, and the derived
            // recomputation depends only on the final half-life — but a
            // deterministic order keeps error messages stable too.
            let mut keys: Vec<String> = Vec::new();
            for key in kwargs.keys() {
                keys.push(key.extract::<String>().map_err(|_| {
                    ValidationError::new_err(
                        "model parameter names must be strings".to_string(),
                    )
                })?);
            }
            keys.sort();
            for key in keys {
                let value: f64 = kwargs
                    .get_item(&key)?
                    .expect("key came from the dict")
                    .extract()
                    .map_err(|_| {
                        ValidationError::new_err(format!(
                            "{key} must be a number"
                        ))
                    })?;
                params = params
                    .with_override(&key, value)
                    .map_err(ValidationError::new_err)?;
            }
        }
        Ok(Self { inner: params })
    }

    /// Rebuild from a full parameter dictionary — the manifest's embedded
    /// form. The inverse of `to_dict`.
    ///
    /// Settable keys are applied as overrides of the shipped preset; a
    /// `"name"` key is ignored (the fingerprint is recomputed, never
    /// trusted); a read-only or derived key whose value does not match
    /// this build is REFUSED, because the dictionary then describes a
    /// model this build cannot run.
    #[staticmethod]
    fn from_dict(values: &Bound<'_, PyDict>) -> PyResult<Self> {
        let mut params = ModelParams::preset("pt-v1").expect("shipped");
        let settable: Vec<&str> = settable_names();
        // Two passes so the half-life override (which rewrites the derived
        // bits) is applied before the derived keys are verified.
        let mut items: Vec<(String, f64)> = Vec::new();
        for (key, value) in values.iter() {
            let key: String = key.extract().map_err(|_| {
                ValidationError::new_err("model parameter names must be strings".to_string())
            })?;
            if key == "name" {
                continue;
            }
            let value: f64 = value.extract().map_err(|_| {
                ValidationError::new_err(format!("{key} must be a number"))
            })?;
            items.push((key, value));
        }
        items.sort_by(|a, b| a.0.cmp(&b.0));
        for (key, value) in &items {
            if settable.contains(&key.as_str()) {
                params = params
                    .with_override(key, *value)
                    .map_err(ValidationError::new_err)?;
            }
        }
        for (key, value) in &items {
            if settable.contains(&key.as_str()) {
                continue;
            }
            match params.get(key) {
                Some(ours) if ours.to_bits() == value.to_bits() => {}
                Some(ours) => {
                    return Err(ValidationError::new_err(format!(
                        "{key} is {value:?} in this dictionary but {ours:?} \
                         on this build, and it is not runtime-settable here. \
                         The dictionary describes a model this build cannot \
                         run; use the build that wrote it."
                    )));
                }
                None => {
                    return Err(ValidationError::new_err(format!(
                        "unknown model parameter {key:?} in the dictionary. \
                         A newer build may have written it; upgrade pretium \
                         rather than dropping it silently."
                    )));
                }
            }
        }
        Ok(Self { inner: params })
    }

    /// The full preset surface as a dict — every settable coefficient, the
    /// derived-bits pair, and the carried read-only constants — plus
    /// `"name"`, which is the fingerprint. This is what a manifest embeds.
    pub fn to_dict(&self, py: Python<'_>) -> PyResult<PyObject> {
        let out = PyDict::new_bound(py);
        out.set_item("name", self.inner.fingerprint())?;
        for (name, value) in self.inner.to_pairs() {
            out.set_item(name, value)?;
        }
        Ok(out.into())
    }

    /// The honest name: a shipped preset's name when bit-identical to it,
    /// `custom-XXXXXXXX` otherwise — first 8 hex chars of sha256 over the
    /// canonical serialisation (names sorted, values as IEEE-754 bit
    /// patterns). A non-shipped preset can never present as a shipped one.
    #[getter]
    fn fingerprint(&self) -> String {
        self.inner.fingerprint()
    }

    /// The runtime-settable parameter names, sorted — what `from_preset`
    /// accepts as keywords.
    #[staticmethod]
    fn settable() -> Vec<String> {
        settable_names().iter().map(|s| s.to_string()).collect()
    }

    /// Read any parameter as an attribute: `params.garch_alpha`.
    fn __getattr__(&self, name: &str) -> PyResult<f64> {
        self.inner.get(name).ok_or_else(|| {
            pyo3::exceptions::PyAttributeError::new_err(format!(
                "ModelParams has no parameter {name:?}"
            ))
        })
    }

    fn __richcmp__(
        &self,
        other: &Bound<'_, PyAny>,
        op: pyo3::basic::CompareOp,
        py: Python<'_>,
    ) -> PyResult<PyObject> {
        let Ok(other) = other.extract::<PyRef<'_, PyModelParams>>() else {
            return Ok(py.NotImplemented());
        };
        // Bit-equality via the digest — the same rule the fingerprint uses.
        let equal = self.inner.digest() == other.inner.digest();
        match op {
            pyo3::basic::CompareOp::Eq => Ok(equal.into_py(py)),
            pyo3::basic::CompareOp::Ne => Ok((!equal).into_py(py)),
            _ => Ok(py.NotImplemented()),
        }
    }

    fn __repr__(&self) -> String {
        let fp = self.inner.fingerprint();
        if fp == "pt-v1" {
            return "ModelParams(\"pt-v1\")".to_string();
        }
        // Name the deviations, so a repr in a log says WHAT the custom
        // model is rather than only that it is one.
        let base = ModelParams::preset("pt-v1").expect("shipped");
        let mut diffs: Vec<String> = Vec::new();
        for name in settable_names() {
            let ours = self.inner.get(name).expect("settable");
            let theirs = base.get(name).expect("settable");
            if ours.to_bits() != theirs.to_bits() {
                diffs.push(format!("{name}={ours}"));
            }
        }
        format!("ModelParams(\"{fp}\", from pt-v1 with {})", diffs.join(", "))
    }
}
