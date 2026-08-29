//! Parity for `mispricing`, against ~632k recorded assertions.
//!
//! **Tier 1 throughout except `apply_mispricing`**, whose single `exp` makes
//! it Tier 2. Everything else is arithmetic and `sqrt`, both exactly specified
//! by IEEE-754, so a mismatch is a defect rather than a last-ULP disagreement
//! between libm implementations.
//!
//! # Why the vectors are read rather than regenerated
//!
//! The trajectory inputs were produced by a seeded generator, but they are
//! stored explicitly and replayed from the file. If the harness regenerated
//! them, a PRNG bug would masquerade as a mispricing bug — the two failures
//! look identical from here, and only one of them is this module's.
//!
//! For the same reason [`manifest_matches_the_recorded_hashes`] checks every
//! file against `index.json`'s SHA-256. A vector set regenerated from Rust
//! would make the port its own oracle, and would otherwise pass silently.
//!
//! # Encodings
//!
//! `goldens/README.md` uses four forms, all handled here: `{dec, bits}`
//! objects, bare bit-string arrays, the collapsed `{constantBits, length}`
//! form for runs of identical values, and a row table (`columns` + `rows`)
//! used by the step cases to keep that file under 22 MB.

use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;

use tradefloor::mispricing::{
    apply_mispricing, characteristic_root_moduli, create_mispricing_state,
    crowd_adjusted_root_moduli, crowd_lean, impulse_response, step_mispricing, MispricingInputs,
    MispricingState, CROWD_LEAN_CAP, CROWD_MOMENTUM_GAIN, CROWD_VALUATION_GAIN, DAILY_SHOCK_CAP,
    MISPRICING_CAP, MISPRICING_HALF_LIFE_DAYS, MISPRICING_PHI, MOMENTUM_THETA,
};
use serde::Deserialize;
use serde_json::Value as Json;

// ── Encodings ─────────────────────────────────────────────────────────────

/// The `{dec, bits}` pair. `dec` is carried for legibility in failures; only
/// `bits` is authoritative, since a decimal rendering can agree while the
/// doubles differ.
#[derive(Deserialize, Clone)]
struct Val {
    dec: String,
    bits: String,
}

impl Val {
    fn f(&self) -> f64 {
        bits(&self.bits)
    }
}

/// An argument that may be the literal string `"default"`, meaning the
/// reference-implementation default parameter applied.
#[derive(Deserialize)]
#[serde(untagged)]
enum Arg {
    Value(Val),
    // The payload is the file's own label ("default", "default (0)"). It is
    // never read, but the variant must accept it for the untagged
    // deserialiser to recognise a string where an object was also possible.
    Default(#[allow(dead_code)] String),
}

impl Arg {
    fn opt(&self) -> Option<f64> {
        match self {
            Arg::Value(v) => Some(v.f()),
            Arg::Default(_) => None,
        }
    }
}

/// A long float array: either explicit bit strings, or collapsed to a single
/// repeated value.
#[derive(Deserialize)]
#[serde(untagged)]
enum Bulk {
    Constant {
        #[serde(rename = "constantBits")]
        constant_bits: String,
        length: usize,
    },
    Explicit(Vec<String>),
}

impl Bulk {
    fn len(&self) -> usize {
        match self {
            Bulk::Constant { length, .. } => *length,
            Bulk::Explicit(v) => v.len(),
        }
    }
    fn at(&self, i: usize) -> f64 {
        match self {
            Bulk::Constant { constant_bits, .. } => bits(constant_bits),
            Bulk::Explicit(v) => bits(&v[i]),
        }
    }
}

fn bits(hex: &str) -> f64 {
    f64::from_bits(u64::from_str_radix(hex, 16).unwrap_or_else(|e| panic!("bad bits {hex}: {e}")))
}

/// Bit equality, with NaN equal to NaN.
///
/// NaN sign and payload are unspecified by IEEE-754 and ECMAScript and differ
/// by architecture, and neither is observable from JavaScript. The vectors
/// deliberately include NaN inputs — the `nonFinite` step rows are 396 of
/// them — so this distinction is load-bearing rather than theoretical.
fn agrees(got: f64, want: f64) -> bool {
    got.to_bits() == want.to_bits() || (got.is_nan() && want.is_nan())
}

// ── Loading ───────────────────────────────────────────────────────────────

fn goldens_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("goldens")
}

fn raw(name: &str) -> String {
    let path = goldens_dir().join(name);
    fs::read_to_string(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()))
}

fn load(name: &str) -> Json {
    serde_json::from_str(&raw(name)).unwrap_or_else(|e| panic!("malformed {name}: {e}"))
}

fn de<T: for<'a> Deserialize<'a>>(v: &Json) -> T {
    T::deserialize(v.clone()).expect("golden shape did not match")
}

/// Declared assertion count for one file, from `index.json`.
fn declared(name: &str) -> usize {
    let index = load("index.json");
    index["files"]
        .as_array()
        .expect("index.files")
        .iter()
        .find(|f| f["file"] == name)
        .unwrap_or_else(|| panic!("{name} is not in index.json"))["assertions"]
        .as_u64()
        .expect("assertions") as usize
}

/// Report, then assert. Coverage is asserted as well as correctness: a
/// harness that silently checked half the file would otherwise pass.
fn finish(name: &str, problems: Vec<String>, checked: usize) {
    let want = declared(name);
    println!("{name}: {checked} values checked ({want} declared in index.json)");
    if !problems.is_empty() {
        let shown: Vec<&String> = problems.iter().take(20).collect();
        panic!(
            "{} mismatches in {name} (first {} shown):\n\n  {}\n",
            problems.len(),
            shown.len(),
            shown
                .iter()
                .map(|s| s.as_str())
                .collect::<Vec<_>>()
                .join("\n  ")
        );
    }
    // The decimal previews are redundant with the bulk data by construction,
    // so a correct harness checks slightly MORE than index.json declares.
    assert!(
        checked >= want,
        "{name}: only checked {checked} values but index.json declares {want} — \
         the harness is skipping part of the file"
    );
}

// ── Constants ─────────────────────────────────────────────────────────────

#[test]
fn constants_match_the_recorded_bits() {
    let doc = load("mispricing-constants.json");
    let mut problems = Vec::new();
    let mut checked = 0;

    let expected: Vec<(&str, f64)> = vec![
        ("MISPRICING_HALF_LIFE_DAYS", MISPRICING_HALF_LIFE_DAYS),
        ("MISPRICING_PHI", MISPRICING_PHI),
        ("MOMENTUM_THETA", MOMENTUM_THETA),
        ("MISPRICING_CAP", MISPRICING_CAP),
        ("DAILY_SHOCK_CAP", DAILY_SHOCK_CAP),
        ("CROWD_VALUATION_GAIN", CROWD_VALUATION_GAIN),
        ("CROWD_MOMENTUM_GAIN", CROWD_MOMENTUM_GAIN),
        ("CROWD_LEAN_CAP", CROWD_LEAN_CAP),
    ];

    for (name, got) in expected {
        let want: Val = de(&doc["constants"][name]);
        if !agrees(got, want.f()) {
            problems.push(format!(
                "{name}: rust={got:?} ({:016X})  ts={} ({})",
                got.to_bits(),
                want.dec,
                want.bits
            ));
        }
        checked += 1;
    }

    // The derivation intermediates. These matter because `1/60` is itself
    // inexact: the recorded `exponent` is the exact double `Math.pow`
    // received, so if a future maintainer ever recomputes phi they can check
    // they are feeding it the same input rather than a differently-rounded one.
    let d = &doc["phiDerivation"];
    let derivation: Vec<(&str, f64)> = vec![
        ("base", 0.5),
        ("exponentNumerator", 1.0),
        ("exponentDenominator", MISPRICING_HALF_LIFE_DAYS),
        ("exponent", 1.0 / MISPRICING_HALF_LIFE_DAYS),
        ("result", MISPRICING_PHI),
    ];
    for (name, got) in derivation {
        let want: Val = de(&d[name]);
        if !agrees(got, want.f()) {
            problems.push(format!(
                "phiDerivation.{name}: rust={got:?} ts={}",
                want.dec
            ));
        }
        checked += 1;
    }

    finish("mispricing-constants.json", problems, checked);
}

// ── createMispricingState ─────────────────────────────────────────────────

#[derive(Deserialize)]
struct CreateStateDoc {
    cases: Vec<CreateCase>,
    #[serde(rename = "defaultCase")]
    default_case: DefaultCreateCase,
}
#[derive(Deserialize)]
struct CreateCase {
    input: CreateInput,
    output: StateOut,
}
#[derive(Deserialize)]
struct CreateInput {
    initial: Val,
}
#[derive(Deserialize)]
struct DefaultCreateCase {
    output: StateOut,
}
#[derive(Deserialize)]
struct StateOut {
    s: Val,
    #[serde(rename = "sPrev")]
    s_prev: Val,
}

#[test]
fn create_state_matches() {
    let doc: CreateStateDoc = de(&load("mispricing-create-state.json"));
    let mut problems = Vec::new();
    let mut checked = 0;

    let mut check_state = |note: String, got: MispricingState, want: &StateOut| {
        for (label, g, w) in [("s", got.s, &want.s), ("sPrev", got.s_prev, &want.s_prev)] {
            if !agrees(g, w.f()) {
                problems.push(format!(
                    "{note} .{label}: rust={g:?} ts={} ({})",
                    w.dec, w.bits
                ));
            }
        }
        checked += 2;
    };

    for case in &doc.cases {
        let initial = case.input.initial.f();
        check_state(
            format!("createMispricingState({})", case.input.initial.dec),
            create_mispricing_state(initial),
            &case.output,
        );
    }

    // The no-argument call. `MispricingState::default()` is the port's
    // spelling of the reference-implementation default parameter.
    check_state(
        "createMispricingState() [default]".to_string(),
        MispricingState::default(),
        &doc.default_case.output,
    );

    finish("mispricing-create-state.json", problems, checked);
}

// ── stepMispricing ────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct StepDoc {
    columns: Vec<String>,
    rows: Vec<Vec<String>>,
    #[serde(rename = "nonFinite")]
    non_finite: Vec<Vec<String>>,
}

#[test]
fn step_matches_across_the_row_table() {
    let doc: StepDoc = de(&load("mispricing-step-cases.json"));

    // The column order is the contract; a reordered table would otherwise be
    // read as a mass of mismatches rather than as a schema change.
    assert_eq!(
        doc.columns,
        vec!["inS", "inSPrev", "innovation", "shock", "outS", "outSPrev"],
        "step-cases column order changed — the harness is decoding the wrong fields"
    );

    let mut problems = Vec::new();
    let mut checked = 0;

    for (label, table) in [("rows", &doc.rows), ("nonFinite", &doc.non_finite)] {
        for (i, row) in table.iter().enumerate() {
            assert_eq!(row.len(), 6, "{label}[{i}] has {} columns", row.len());
            let state = MispricingState {
                s: bits(&row[0]),
                s_prev: bits(&row[1]),
            };
            let inputs = MispricingInputs {
                innovation: bits(&row[2]),
                shock: bits(&row[3]),
            };
            let got = step_mispricing(&state, &inputs);

            for (field, g, want_hex) in [("s", got.s, &row[4]), ("sPrev", got.s_prev, &row[5])] {
                if !agrees(g, bits(want_hex)) {
                    problems.push(format!(
                        "{label}[{i}] .{field}: in s={:?} sPrev={:?} innov={:?} shock={:?} → rust={g:?} ({:016X}) ts={:?} ({want_hex})",
                        state.s, state.s_prev, inputs.innovation, inputs.shock,
                        g.to_bits(), bits(want_hex)
                    ));
                }
                checked += 1;
            }
        }
    }

    finish("mispricing-step-cases.json", problems, checked);
}

// ── applyMispricing ───────────────────────────────────────────────────────

#[derive(Deserialize)]
struct ApplyDoc {
    cases: Vec<ApplyCase>,
}
#[derive(Deserialize)]
struct ApplyCase {
    input: ApplyInput,
    output: Val,
}
#[derive(Deserialize)]
struct ApplyInput {
    #[serde(rename = "fairValue")]
    fair_value: Val,
    s: Val,
}

#[test]
fn apply_matches_including_its_exponential() {
    // The one Tier 2 function here. Its `exp` argument is clamped to
    // [-0.9, 0.9], a narrow domain where the determinism notes measured libm and
    // V8 to agree — but "expected to agree" is not "verified to agree", which
    // is what this is. Any exception would be handled per §0.1's policy, by
    // documenting the specific input, NOT by loosening the comparator.
    let doc: ApplyDoc = de(&load("mispricing-apply.json"));
    let mut problems = Vec::new();
    let mut checked = 0;

    for case in &doc.cases {
        let got = apply_mispricing(case.input.fair_value.f(), case.input.s.f());
        let want = case.output.f();
        if !agrees(got, want) {
            let ulps = (got.to_bits() as i64 - want.to_bits() as i64).abs();
            problems.push(format!(
                "applyMispricing({}, {}): rust={got:?} ts={} — {ulps} ULP",
                case.input.fair_value.dec, case.input.s.dec, case.output.dec
            ));
        }
        checked += 1;
    }

    finish("mispricing-apply.json", problems, checked);
}

// ── crowdLean ─────────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct CrowdDoc {
    cases: Vec<CrowdCase>,
}
#[derive(Deserialize)]
struct CrowdCase {
    input: CrowdInput,
    output: Val,
}
#[derive(Deserialize)]
struct CrowdInput {
    s: Val,
    momentum: Val,
}

#[test]
fn crowd_lean_matches() {
    // The best structural canary in the set: two multiplies, one add, one
    // clamp, and no libm at all. If this fails the problem is structural, not
    // a last-ULP difference.
    let doc: CrowdDoc = de(&load("mispricing-crowd-lean.json"));
    let mut problems = Vec::new();
    let mut checked = 0;

    for case in &doc.cases {
        let got = crowd_lean(case.input.s.f(), case.input.momentum.f());
        if !agrees(got, case.output.f()) {
            problems.push(format!(
                "crowdLean({}, {}): rust={got:?} ({:016X}) ts={} ({})",
                case.input.s.dec,
                case.input.momentum.dec,
                got.to_bits(),
                case.output.dec,
                case.output.bits
            ));
        }
        checked += 1;
    }

    finish("mispricing-crowd-lean.json", problems, checked);
}

// ── characteristicRootModuli ──────────────────────────────────────────────

#[derive(Deserialize)]
struct RootsDoc {
    cases: Vec<RootCase>,
    defaults: DefaultRoots,
    #[serde(rename = "crowdAdjusted")]
    crowd_adjusted: CrowdAdjusted,
}
#[derive(Deserialize)]
struct RootCase {
    input: RootInput,
    discriminant: Val,
    branch: String,
    output: Vec<Val>,
}
#[derive(Deserialize)]
struct RootInput {
    phi: Val,
    theta: Val,
}
#[derive(Deserialize)]
struct DefaultRoots {
    output: Vec<Val>,
}
#[derive(Deserialize)]
struct CrowdAdjusted {
    input: RootInput,
    output: Vec<Val>,
}

#[test]
fn characteristic_roots_match_on_both_branches() {
    let doc: RootsDoc = de(&load("mispricing-roots.json"));
    let mut problems = Vec::new();
    let mut checked = 0;
    let (mut real, mut complex) = (0, 0);

    for case in &doc.cases {
        let phi = case.input.phi.f();
        let theta = case.input.theta.f();
        let (r1, r2) = characteristic_root_moduli(Some(phi), Some(theta));

        // Recompute the discriminant the way the source does and compare it
        // to the recorded value — that part gates the port.
        //
        // The `branch` comparison below does NOT: it checks this harness's
        // own `disc >= 0.0` against the corpus label, which is a consistency
        // check on the vectors rather than a test of the module. That is not
        // a gap being tolerated — the module's branch choice is genuinely
        // unobservable, because at `disc == 0` both formulas yield the same
        // moduli (see `characteristic_root_moduli`). There is nothing to gate
        // through the public API, and pretending otherwise would be worse
        // than saying so.
        let a1 = phi + theta;
        let disc = a1 * a1 + 4.0 * -theta;
        if !agrees(disc, case.discriminant.f()) {
            problems.push(format!(
                "disc(phi={}, theta={}): rust={disc:?} ts={}",
                case.input.phi.dec, case.input.theta.dec, case.discriminant.dec
            ));
        }
        checked += 1;

        let took_real = disc >= 0.0;
        let expected_real = match case.branch.as_str() {
            "real" => true,
            "complex" => false,
            other => panic!("unknown branch {other}"),
        };
        if took_real != expected_real {
            problems.push(format!(
                "branch(phi={}, theta={}): rust took {}, ts took {}",
                case.input.phi.dec,
                case.input.theta.dec,
                if took_real { "real" } else { "complex" },
                case.branch
            ));
        }
        if expected_real {
            real += 1;
        } else {
            complex += 1;
        }

        for (i, (g, w)) in [r1, r2].iter().zip(&case.output).enumerate() {
            if !agrees(*g, w.f()) {
                problems.push(format!(
                    "root[{i}](phi={}, theta={}): rust={g:?} ts={}",
                    case.input.phi.dec, case.input.theta.dec, w.dec
                ));
            }
            checked += 1;
        }
    }

    // Coverage of the branch itself, not just of the cases: a corpus that
    // only exercised real roots would leave half the function unverified.
    assert!(
        real > 0 && complex > 0,
        "one branch is unexercised: {real} real, {complex} complex"
    );
    println!("roots: {real} real-branch cases, {complex} complex-branch");

    // The no-argument call, which depends on MISPRICING_PHI.
    let (d1, d2) = characteristic_root_moduli(None, None);
    for (i, (g, w)) in [d1, d2].iter().zip(&doc.defaults.output).enumerate() {
        if !agrees(*g, w.f()) {
            problems.push(format!("defaults root[{i}]: rust={g:?} ts={}", w.dec));
        }
        checked += 1;
    }

    // crowdAdjustedRootModuli, plus the inputs it derives.
    let (c1, c2) = crowd_adjusted_root_moduli();
    for (label, g, w) in [
        (
            "phi",
            MISPRICING_PHI - CROWD_VALUATION_GAIN,
            &doc.crowd_adjusted.input.phi,
        ),
        (
            "theta",
            MOMENTUM_THETA + CROWD_MOMENTUM_GAIN,
            &doc.crowd_adjusted.input.theta,
        ),
    ] {
        if !agrees(g, w.f()) {
            problems.push(format!(
                "crowdAdjusted input {label}: rust={g:?} ts={}",
                w.dec
            ));
        }
    }
    for (i, (g, w)) in [c1, c2].iter().zip(&doc.crowd_adjusted.output).enumerate() {
        if !agrees(*g, w.f()) {
            problems.push(format!("crowdAdjusted root[{i}]: rust={g:?} ts={}", w.dec));
        }
        checked += 1;
    }

    finish("mispricing-roots.json", problems, checked);
}

// ── impulseResponse ───────────────────────────────────────────────────────

#[derive(Deserialize)]
struct ImpulseDoc {
    cases: Vec<ImpulseCase>,
    degenerate: Vec<ImpulseCase>,
    defaults: ImpulseCase,
}
#[derive(Deserialize)]
struct ImpulseCase {
    input: ImpulseInput,
    /// Absent on the `defaults` entry, which records only input and output.
    length: Option<usize>,
    output: Bulk,
}
#[derive(Deserialize)]
struct ImpulseInput {
    #[serde(rename = "horizonDays")]
    horizon_days: i64,
    phi: Arg,
    theta: Arg,
}

#[test]
fn impulse_response_matches_over_five_thousand_compounding_steps() {
    let doc: ImpulseDoc = de(&load("mispricing-impulse.json"));
    let mut problems = Vec::new();
    let mut checked = 0;

    let run = |label: &str, case: &ImpulseCase, problems: &mut Vec<String>, checked: &mut usize| {
        let got = impulse_response(
            case.input.horizon_days,
            case.input.phi.opt(),
            case.input.theta.opt(),
        );
        let declared_len = case.length.unwrap_or_else(|| case.output.len());
        // The recorded `length` is itself one of the file's assertions, so it
        // is counted where it is present.
        if case.length.is_some() {
            *checked += 1;
        }
        if got.len() != declared_len || got.len() != case.output.len() {
            problems.push(format!(
                "{label} horizon={}: rust produced {} entries, ts {} (declared {})",
                case.input.horizon_days,
                got.len(),
                case.output.len(),
                declared_len
            ));
            return;
        }
        // Report the FIRST divergence, not all of them: after step k the two
        // are integrating different states and everything downstream is noise.
        for (i, g) in got.iter().enumerate() {
            let w = case.output.at(i);
            if !agrees(*g, w) {
                problems.push(format!(
                    "{label} horizon={} phi={:?} theta={:?}: first divergence at step {i} — rust={g:?} ({:016X}) ts={w:?}",
                    case.input.horizon_days,
                    case.input.phi.opt(),
                    case.input.theta.opt(),
                    g.to_bits()
                ));
                *checked += i + 1;
                return;
            }
        }
        *checked += got.len();
    };

    for (i, case) in doc.cases.iter().enumerate() {
        run(&format!("cases[{i}]"), case, &mut problems, &mut checked);
    }
    for (i, case) in doc.degenerate.iter().enumerate() {
        run(
            &format!("degenerate[{i}]"),
            case,
            &mut problems,
            &mut checked,
        );
    }
    run("defaults", &doc.defaults, &mut problems, &mut checked);

    finish("mispricing-impulse.json", problems, checked);
}

// ── Trajectories ──────────────────────────────────────────────────────────

#[derive(Deserialize)]
struct TrajectoryDoc {
    trajectory: TrajectoryMeta,
    innovations: Bulk,
    shocks: Bulk,
    #[serde(rename = "outputSBits")]
    output_s: Bulk,
    checkpoints: Vec<Checkpoint>,
}
#[derive(Deserialize)]
struct TrajectoryMeta {
    name: String,
    steps: usize,
    #[serde(rename = "initialState")]
    initial_state: StateOut,
    #[serde(rename = "clampedStepCount")]
    clamped_step_count: usize,
}
#[derive(Deserialize)]
struct Checkpoint {
    step: usize,
    output: StateOut,
}

fn check_trajectory(file: &str) {
    let doc: TrajectoryDoc = de(&load(file));
    let meta = &doc.trajectory;
    let mut problems = Vec::new();
    let mut checked = 0;

    assert_eq!(
        doc.innovations.len(),
        meta.steps,
        "{file}: innovation count"
    );
    assert_eq!(doc.shocks.len(), meta.steps, "{file}: shock count");
    assert_eq!(doc.output_s.len(), meta.steps, "{file}: output count");

    let mut state = MispricingState {
        s: meta.initial_state.s.f(),
        s_prev: meta.initial_state.s_prev.f(),
    };
    for (label, g, w) in [
        ("initialState.s", state.s, &meta.initial_state.s),
        (
            "initialState.sPrev",
            state.s_prev,
            &meta.initial_state.s_prev,
        ),
    ] {
        if !agrees(g, w.f()) {
            problems.push(format!("{label}: rust={g:?} ts={}", w.dec));
        }
        checked += 1;
    }

    let checkpoints: HashMap<usize, &Checkpoint> =
        doc.checkpoints.iter().map(|c| (c.step, c)).collect();

    let mut first_divergence: Option<usize> = None;
    let mut decade_report: Vec<String> = Vec::new();
    let mut clamped = 0usize;

    for i in 0..meta.steps {
        state = step_mispricing(
            &state,
            &MispricingInputs {
                innovation: doc.innovations.at(i),
                shock: doc.shocks.at(i),
            },
        );

        // sPrev is not recorded per step because it is s[i-1] by
        // construction — `initialState.s` at step 0. Asserting that identity
        // is what makes omitting it safe rather than merely convenient.
        //
        // The check belongs AFTER the step: `step_mispricing` sets the new
        // sPrev from the s it was given, so before the call `state.s_prev` is
        // still s[i-2] and comparing it here reports every step as broken.
        let expected_prev = if i == 0 {
            meta.initial_state.s.f()
        } else {
            doc.output_s.at(i - 1)
        };
        if !agrees(state.s_prev, expected_prev) {
            problems.push(format!(
                "step {i}: sPrev identity broken — rust={:?} expected s[{}]={expected_prev:?}",
                state.s_prev,
                i as i64 - 1
            ));
        }

        let want = doc.output_s.at(i);
        if !agrees(state.s, want) {
            if first_divergence.is_none() {
                first_divergence = Some(i);
            }
            // A divergence INDEX plus magnitudes by decade, not a boolean:
            // once these differ, the two are integrating different states, so
            // the useful information is where it started and how it grew.
            if i == 0 || i.is_power_of_two() || i % 10_000 == 0 {
                decade_report.push(format!(
                    "    step {i}: rust={:?} ts={want:?} (|Δ| = {:e})",
                    state.s,
                    (state.s - want).abs()
                ));
            }
            // Re-sync so later steps report their own divergence rather than
            // the accumulated consequence of this one.
            state.s = want;
        }
        checked += 1;

        if state.s.abs() >= MISPRICING_CAP {
            clamped += 1;
        }

        if let Some(cp) = checkpoints.get(&i) {
            for (label, g, w) in [
                ("s", state.s, &cp.output.s),
                ("sPrev", state.s_prev, &cp.output.s_prev),
            ] {
                if !agrees(g, w.f()) {
                    problems.push(format!("checkpoint {i} .{label}: rust={g:?} ts={}", w.dec));
                }
                checked += 1;
            }
        }
    }

    if let Some(i) = first_divergence {
        problems.insert(
            0,
            format!(
                "FIRST DIVERGENCE at step {i} of {}\n{}",
                meta.steps,
                decade_report.join("\n")
            ),
        );
    }

    println!(
        "  [{}] {} steps, {clamped} at/beyond the cap (file records {})",
        meta.name, meta.steps, meta.clamped_step_count
    );

    finish(file, problems, checked);
}

#[test]
fn trajectory_calm() {
    check_trajectory("mispricing-trajectory-calm.json");
}

#[test]
fn trajectory_garch_clustered() {
    check_trajectory("mispricing-trajectory-garch-clustered.json");
}

#[test]
fn trajectory_news_shocks() {
    check_trajectory("mispricing-trajectory-news-shocks.json");
}

#[test]
fn trajectory_extreme_clamped() {
    check_trajectory("mispricing-trajectory-extreme-clamped.json");
}

#[test]
fn trajectory_denormal_drift() {
    check_trajectory("mispricing-trajectory-denormal-drift.json");
}

// ── Manifest ──────────────────────────────────────────────────────────────

#[test]
fn manifest_matches_the_recorded_hashes() {
    use sha2::{Digest, Sha256};

    let index = load("index.json");
    let mut problems = Vec::new();
    let mut seen = 0;

    for entry in index["files"].as_array().expect("index.files") {
        let name = entry["file"].as_str().expect("file");
        if !name.starts_with("mispricing-") {
            continue;
        }
        seen += 1;

        let path = goldens_dir().join(name);
        let bytes = fs::read(&path).unwrap_or_else(|e| panic!("{}: {e}", path.display()));

        let want_bytes = entry["bytes"].as_u64().expect("bytes") as usize;
        if bytes.len() != want_bytes {
            problems.push(format!(
                "{name}: {} bytes on disk, {want_bytes} in the manifest",
                bytes.len()
            ));
        }

        let digest = format!("{:x}", Sha256::digest(&bytes));
        let want = entry["sha256"].as_str().expect("sha256");
        if digest != want {
            problems.push(format!("{name}: sha256 {digest} != manifest {want}"));
        }
    }

    assert_eq!(
        seen, 12,
        "expected 12 mispricing vector files, found {seen}"
    );
    assert!(
        problems.is_empty(),
        "the golden vectors do not match index.json — they have been regenerated or \
         truncated, and regenerating them from Rust would make the port its own oracle:\n  {}",
        problems.join("\n  ")
    );
    println!("{seen} mispricing vector files match their recorded size and SHA-256");
}
