//! `ModelParams`, the settable half of the model preset, on the Python
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
//! `from_preset` / `from_dict`: the fingerprint must not be able to lie.
//! Everything of substance lives in `crate::params`; this file is the
//! boundary.

#![allow(unexpected_cfgs)]

use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::params::{claims_of, settable_names, Inconsistency, ModelParams};
use crate::python::ValidationError;

/// Refuse a vector that breaks an invariant every vector must satisfy.
///
/// Universal, and there is no hatch: `from_preset_unchecked` runs this too.
/// See `ModelParams::invariants` for what is in the set and why.
fn check_invariants(params: &ModelParams) -> PyResult<()> {
    params
        .invariants()
        .map_err(|why| ValidationError::new_err(format!("REFUSED: {why}")))
}

/// Refuse a vector that breaks an identity its own preset claims.
///
/// `base` is the preset the vector was built FROM. If the result is
/// bit-identical to some other shipped preset -- which an arm that reverts a
/// block of dials can be, and `ALL31` in the sectorbisect run is -- then it
/// IS that preset and answers to that preset's claims instead. The
/// fingerprint is only computed when the base's claims already failed, so
/// the consistent path never pays for it.
fn check_claims(params: &ModelParams, base: &str) -> PyResult<()> {
    let bad = params.claimed_inconsistencies(claims_of(base));
    if bad.is_empty() {
        return Ok(());
    }
    let landed = params.fingerprint();
    if landed != base && ModelParams::preset(&landed).is_some() {
        let bad = params.claimed_inconsistencies(claims_of(&landed));
        if bad.is_empty() {
            return Ok(());
        }
        return Err(refusal(&bad, &landed));
    }
    Err(refusal(&bad, base))
}

/// The refusal message. It names both sides of every broken identity,
/// because a refusal a reader cannot act on is a refusal they will route
/// around.
fn refusal(bad: &[Inconsistency], owner: &str) -> PyErr {
    let lines: Vec<String> = bad.iter().map(|i| format!("  - {i}")).collect();
    ValidationError::new_err(format!(
        "REFUSED: this vector breaks {} identit{} that {owner} claims about its own \
         dials:\n{}\n\nA derived dial moved off its identity is a dial that follows \
         from nothing, and a reading taken on one is a reading of the break as much as \
         of the arm. Either move the dials the identity depends on so it holds again, \
         or build the vector with ModelParams.from_preset_unchecked(...) and record the \
         waiver with the measurement.",
        bad.len(),
        if bad.len() == 1 { "y" } else { "ies" },
        lines.join("\n")
    ))
}

/// An immutable model coefficient set: a shipped preset, or a named
/// deviation from one.
#[pyclass(name = "ModelParams", module = "tradefloor._core", frozen)]
#[derive(Debug, Clone)]
pub struct PyModelParams {
    pub inner: ModelParams,
}

#[pymethods]
impl PyModelParams {
    /// Build from a shipped preset, with keyword overrides.
    ///
    /// With no name this returns the ENGINE'S DEFAULT preset, the same one
    /// `Engine(...)` runs and `model_preset()` reports, so the two cannot
    /// disagree. It read `"pt-v1"` through 0.1.4 while engines ran pt-v3,
    /// which is a live substitution bug wherever a caller uses the no-arg
    /// form as "the default model": `Checkpoint.of` did exactly that and
    /// dropped the model of every pt-v1 run, resuming it as pt-v3.
    ///
    /// `ModelParams.from_preset("pt-v1")` still returns pt-v1 and
    /// fingerprints as `"pt-v1"`; any override that changes a bit
    /// fingerprints as `"custom-XXXXXXXX"`. Unknown names, non-finite
    /// values, the derived-bits coefficients (`mispricing_phi`,
    /// `s_phi_tick`, so override `mispricing_half_life_days` instead) and
    /// the carried read-only surface are refused by name.
    #[staticmethod]
    #[pyo3(signature = (name = crate::params::DEFAULT_PRESET_NAME, **overrides))]
    fn from_preset(name: &str, overrides: Option<&Bound<'_, PyDict>>) -> PyResult<Self> {
        let params = build(name, overrides)?;
        check_invariants(&params)?;
        check_claims(&params, name)?;
        Ok(Self { inner: params })
    }

    /// `from_preset`, with the preset's own identity claims NOT checked.
    ///
    /// The escape hatch, and the reason it is a second constructor rather
    /// than a keyword: `**overrides` IS the settable surface, so a flag name
    /// would have to be reserved against every future dial forever.
    ///
    /// Probing a derived dial off its identity is a legitimate measurement --
    /// it is how the record knows the cap and the ceiling are inert on the
    /// pt-v19 vector -- and refusing it outright loses a tool. What this
    /// constructor does NOT skip is `invariants`: a universal invariant has
    /// no hatch, because no reading taken on a vector that breaks one means
    /// anything.
    ///
    /// The vector it returns is the same frozen type with the same bits. A
    /// caller that uses it owes the reader the waiver beside the number;
    /// `dialarm.py --allow-identity-break` writes it into the arm record.
    #[staticmethod]
    #[pyo3(signature = (name = crate::params::DEFAULT_PRESET_NAME, **overrides))]
    fn from_preset_unchecked(
        name: &str,
        overrides: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<Self> {
        let params = build(name, overrides)?;
        check_invariants(&params)?;
        Ok(Self { inner: params })
    }

    /// The identities `preset` claims about its own dials, evaluated on this
    /// vector: one dict per identity that does not hold, with `dial`,
    /// `identity`, `expected`, `actual`, `tolerance` and `claimed_by`, and
    /// an empty list when they all do.
    ///
    /// Read-only, and it is what a harness writes into an arm record after
    /// waiving. It takes the preset name because the claims are a property of
    /// the preset and not of the type: the cap identity holds on pt-v19 and
    /// on nothing else shipped.
    ///
    /// `preset` defaults to the engine's default preset, pt-v19, which is
    /// the default `from_preset` uses, and not to the preset the vector was
    /// built from. A vector does not record its base, and a waived one
    /// fingerprints as `custom-XXXXXXXX`, so there is nothing to infer it
    /// from. A vector built from another preset is therefore checked
    /// against pt-v19's claims unless you name its own:
    /// `identity_breaks(from_preset("pt-v18"))` returns two rows,
    /// `vix_target_shock_cap` and `garch_beta`, each with `claimed_by`
    /// `"pt-v19"`, and `identity_breaks(from_preset("pt-v18"), "pt-v18")`
    /// returns none, because pt-v18 claims no identities. Pass the name the
    /// vector was built from.
    #[staticmethod]
    #[pyo3(signature = (params, preset = crate::params::DEFAULT_PRESET_NAME))]
    fn identity_breaks(
        py: Python<'_>,
        params: PyRef<'_, PyModelParams>,
        preset: &str,
    ) -> PyResult<PyObject> {
        let out = pyo3::types::PyList::empty_bound(py);
        for bad in params.inner.claimed_inconsistencies(claims_of(preset)) {
            let row = PyDict::new_bound(py);
            row.set_item("dial", bad.dial)?;
            row.set_item("identity", bad.identity)?;
            row.set_item("expected", bad.expected)?;
            row.set_item("actual", bad.actual)?;
            row.set_item("tolerance", bad.tolerance)?;
            row.set_item("claimed_by", bad.claimed_by)?;
            out.append(row)?;
        }
        Ok(out.into())
    }

    /// Rebuild from a full parameter dictionary, the manifest's embedded
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
                         A newer build may have written it; upgrade tradefloor \
                         rather than dropping it silently."
                    )));
                }
            }
        }
        // The universal invariant and NOT the claim table. `from_dict` is the
        // manifest replay path and a manifest that embeds a waived vector must
        // replay: a waiver is recorded beside the measurement, not inside the
        // dictionary, so `to_dict` round-trips whatever was built. The
        // invariant has no hatch anywhere, including here.
        //
        // After the second pass rather than inside either, because the keys
        // apply in sorted order and "market_vol_vix_excursion" sorts before
        // "vix_level_identity": a per-key check would fire on the first of a
        // pair that is consistent once both have landed. The batch is the unit.
        check_invariants(&params)?;
        Ok(Self { inner: params })
    }

    /// The full preset surface as a dict: every settable coefficient, the
    /// derived-bits pair and the carried read-only constants, plus
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
    /// `custom-XXXXXXXX` otherwise, being the first 8 hex chars of sha256 over the
    /// canonical serialisation (names sorted, values as IEEE-754 bit
    /// patterns). A non-shipped preset can never present as a shipped one.
    #[getter]
    fn fingerprint(&self) -> String {
        self.inner.fingerprint()
    }

    /// The runtime-settable parameter names, sorted. This is what `from_preset`
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
        // Bit-equality via the digest, the same rule the fingerprint uses.
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

/// The preset plus its overrides, with nothing checked. Shared by the checked
/// and the unchecked constructor so the two cannot drift apart in how they
/// BUILD, only in what they refuse.
fn build(name: &str, overrides: Option<&Bound<'_, PyDict>>) -> PyResult<ModelParams> {
    let mut params = ModelParams::preset(name).ok_or_else(|| {
        ValidationError::new_err(format!(
            "unknown model preset {name:?}. Shipped presets: {}",
            ModelParams::preset_names().join(", ")
        ))
    })?;
    if let Some(kwargs) = overrides {
        // Sorted for a deterministic application order. The overrides
        // commute, since each writes one independent field and the derived
        // recomputation depends only on the final half-life, but a
        // deterministic order keeps error messages stable too.
        //
        // The identity checks run AFTER this loop and not inside it, and that
        // is load-bearing: "market_vol_vix_excursion" sorts before
        // "vix_level_identity", so an arm turning both on from a preset that
        // runs neither would trip a per-override check on the first key and
        // pass on the second. The batch is the unit.
        let mut keys: Vec<String> = Vec::new();
        for key in kwargs.keys() {
            keys.push(key.extract::<String>().map_err(|_| {
                ValidationError::new_err("model parameter names must be strings".to_string())
            })?);
        }
        keys.sort();
        for key in keys {
            let value: f64 = kwargs
                .get_item(&key)?
                .expect("key came from the dict")
                .extract()
                .map_err(|_| ValidationError::new_err(format!("{key} must be a number")))?;
            params = params
                .with_override(&key, value)
                .map_err(ValidationError::new_err)?;
        }
    }
    Ok(params)
}
