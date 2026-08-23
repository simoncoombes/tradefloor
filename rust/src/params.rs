//! The runtime parameter seam — `ModelParams`, the settable half of the
//! model preset (CALIBRATION.md §5, PYTHON-API-DESIGN.md §3).
//!
//! # What this is
//!
//! The model's coefficients as a value instead of a rebuild. Every constant
//! in the live dynamics chain — the tick loop, the per-name GARCH close, the
//! market factor's variance process — is carried here as a plain `f64`, and
//! the engine reads the field where it used to read the `pub const`. The
//! constants themselves REMAIN, as the definition of the shipped preset:
//! [`PT_V1`] is built from them, so every existing test asserting a constant
//! still guards the preset, and a build whose constants moved fingerprints
//! differently by construction.
//!
//! # Bit-identity is the contract
//!
//! Replacing a `const` read with a field read does not change IEEE-754
//! arithmetic: same values, same operations, same order. Rust neither
//! reassociates nor contracts floating point under any default profile, and
//! this crate additionally bans `mul_add` and non-`mathx` transcendentals.
//! The one hazard §5.3 names — a `const` deriving another — is handled by
//! deriving once, in the constructor: the circuit-breaker band multipliers
//! ([`ModelParams::breaker_up`]/[`ModelParams::breaker_down`]) are computed
//! when the params are built, never per call site. The acceptance gate is
//! trajectory equality: an engine built from `PT_V1` must reproduce the
//! const build's known-answer digest bit for bit, and does — see
//! `tests/test_model_params.py`.
//!
//! # Membership (§5.2), drawn here
//!
//! Four classes, and the draw-schedule rule above all: **nothing settable
//! may change how many draws are taken or in what order.** A preset changes
//! what the draws are multiplied into, never the schedule — that is what
//! keeps every preset comparable under common random numbers and replayable
//! against order logs.
//!
//! 1. **Settable** — the live dynamics numbers ([`settable_names`]): the searched
//!    surface (both variance processes, the factor sigmas and their scale,
//!    the mispricing dynamics) plus the guards that live in the threaded
//!    chain (the mispricing cap, the crowd lean cap, the price breaker and
//!    hard cap). Guards are settable but excluded from any *search* — a
//!    loss that can widen a breaker to buy kurtosis will do so; that
//!    exclusion lives in the search configuration, not here, because "you
//!    may not change the model" was never the rule. "A changed model has a
//!    different name" is.
//! 2. **Derived bits** — `mispricing_phi` and `s_phi_tick` are carried as
//!    V8's recorded bits and are never set directly. Overriding
//!    `mispricing_half_life_days` recomputes both via `mathx::pow`,
//!    documented as deterministic-but-not-bit-identical to any recorded
//!    constant (API §3's verbatim policy). An override equal to the shipped
//!    half-life keeps the recorded bits — sameness of value must mean
//!    sameness of bits.
//! 3. **Carried, read-only** — the rest of Appendix A's preset surface:
//!    fair-value coefficients, the economy's daily-chain constants, the
//!    book geometry, the sector sigma table, `daily_shock_cap` and
//!    `crisis_vix_threshold`. Visible in the dict and covered by the
//!    fingerprint, but an override is REFUSED by name: these are not yet
//!    threaded through their call sites, and accepting an override the
//!    engine would ignore is exactly the fingerprint lie this type exists
//!    to make impossible. They become settable when their chains are
//!    threaded (the "+2–3 days" half of §5.3's estimate).
//! 4. **Excluded outright** — the draw-schedule surface: market hours, the
//!    390 tick base, the calendar constants (`DAYS_PER_MONTH`,
//!    `OIL_OPEC_INTERVAL`), the sector key set and order, the
//!    `ReferenceEma` tape-parity trio, `ANNOUNCEMENT_VARIANTS`, and the
//!    dead `MEAN_REVERSION_*` pair. Not in the dict at all: they are the
//!    schedule, the time base, or parity plumbing — a different model, not
//!    a tuning parameter.
//!
//! # The fingerprint
//!
//! First 8 hex chars of sha256 over the canonical serialisation: parameter
//! names sorted, values as big-endian IEEE-754 bit patterns — the
//! known-answer convention, because decimals differ for reasons that are
//! not the model. A `ModelParams` bit-identical to a shipped preset
//! fingerprints as that preset's NAME; anything else is `custom-XXXXXXXX`.
//! There is no way to construct a non-shipped params that presents as
//! shipped, and no mutation after construction, so the fingerprint cannot
//! lie.

use sha2::{Digest, Sha256};

use crate::market::factor_vol;
use crate::market::factors;
use crate::market::garch;
use crate::market::tick;
use crate::mispricing;

/// The complete runtime-settable model surface, plus the derived values the
/// tick loop reads. Plain `f64`s, no interior mutability: immutable once
/// built, which is what lets the fingerprint be trusted.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelParams {
    // ── Factor structure (market/tick.rs, market/factors.rs) ────────────
    /// Baseline daily sigma of the shared market factor — the anchor of the
    /// factor's variance process, and the crash amplifier's denomination.
    pub market_factor_sigma: f64,
    /// Daily sigma of each shared sector factor.
    pub sector_factor_sigma: f64,
    /// Scale on the per-name idiosyncratic GARCH sigma — the funding side
    /// of the factor-variance reallocation. Bit-inert at 1.0.
    pub idio_sigma_scale: f64,
    /// Order-flow impact coefficient, before the informed fraction.
    pub order_flow_coefficient: f64,
    /// Permanent (information) share of order-flow impact.
    pub informed_flow_fraction: f64,
    /// Weight of sector-scoped news on a member name (§5.4 promotion).
    pub news_sector_weight: f64,
    /// Weight of market-wide news on every name (§5.4 promotion).
    pub news_market_weight: f64,
    /// Market-shock magnitude, in baseline sigmas, above which the crash
    /// amplifier fires (§5.4 promotion).
    pub crash_amplifier_threshold: f64,
    /// Extra market loading per baseline sigma beyond the threshold
    /// (§5.4 promotion).
    pub crash_amplifier_slope: f64,
    /// VIX points past `CRISIS_VIX_THRESHOLD` for the sector→market blend
    /// to reach 1.0 before its cap (§5.4 promotion).
    pub crisis_blend_ramp: f64,
    /// Ceiling of the crisis correlation blend (§5.4 promotion).
    pub crisis_blend_cap: f64,

    // ── Per-name GJR-GARCH (market/garch.rs) ────────────────────────────
    pub garch_omega: f64,
    pub garch_alpha: f64,
    pub garch_beta: f64,
    /// GJR leverage-effect asymmetry. Zero recovers symmetric GARCH(1,1)
    /// bit for bit.
    pub garch_gamma: f64,
    /// Ceiling as a multiple of the sector's long-run variance. A guard,
    /// but searched under bounds — measured as binding on clustering.
    pub garch_ceiling_multiple: f64,
    /// Floor as a multiple of the sector's long-run variance.
    pub garch_floor_multiple: f64,

    // ── Market-factor variance process (market/factor_vol.rs) ───────────
    pub market_vol_alpha: f64,
    pub market_vol_beta: f64,
    pub market_vol_ceiling_multiple: f64,
    pub market_vol_floor_multiple: f64,
    /// 0 = autonomous target, 1 = target fully proportional to VIX².
    pub market_vol_vix_coupling: f64,
    /// VIX level at which a coupled target equals the baseline variance.
    pub market_vol_vix_anchor: f64,
    /// Persistence of the SLOW variance component (Engle-Lee style). The
    /// market factor's variance carries two timescales from the pt-v4 era:
    /// the fast one above tracks the VIX-scaled target, this one carries
    /// long-horizon clustering. 0.0 disables it and recovers the
    /// single-component update bit for bit.
    pub market_vol_slow_persistence: f64,
    /// How much of each day's variance surprise the slow component takes
    /// up. 0.0 disables it.
    pub market_vol_slow_gain: f64,
    /// How much of the slow component's deviation from baseline reaches
    /// the realised variance. 0.0 disables it.
    pub market_vol_slow_weight: f64,
    /// How strongly realised volume tracks the market factor's variance.
    ///
    /// Volume in this engine is a pure function of `avg_volume`, which the
    /// close holds fixed ([`crate::market::daily::AvgVolumePolicy::Hold`]),
    /// so daily volume changes are very nearly independent noise and
    /// difference to an autocorrelation near -0.5 -- measured -0.46 against
    /// a real -0.32..-0.20. Real volume is a persistent level plus large
    /// day-to-day noise, and the persistence is what this supplies.
    ///
    /// The driver is the market factor's variance, which is already
    /// persistent and is genuinely EXOGENOUS to volume -- the property the
    /// `Hold` docstring names as the precondition for reintroducing any
    /// feedback. Feeding realised volume back was tried and removed: it is
    /// a pure function of `avg_volume`, so the loop carried no information
    /// and compounded at ~1.7%/day. This carries information, because
    /// volume and volatility genuinely co-move.
    ///
    /// 0.0 disables it and volume is exactly what it was.
    pub volume_variance_gain: f64,

    // ── Mispricing dynamics (mispricing.rs, market/tick.rs) ─────────────
    /// Trading days for half of a mispricing to decay. The ONE settable
    /// knob for the decay: overriding it recomputes `mispricing_phi` and
    /// `s_phi_tick` via `mathx::pow`.
    pub mispricing_half_life_days: f64,
    /// Daily AR(1) coefficient. V8's recorded bits at the shipped
    /// half-life; recomputed, deterministically but not bit-identically to
    /// any recorded constant, when the half-life is overridden.
    pub mispricing_phi: f64,
    /// Per-tick decay, `mispricing_phi^(1/390)`. Same bits policy.
    pub s_phi_tick: f64,
    /// Herding: fraction of yesterday's re-rating that continues today.
    pub momentum_theta: f64,
    /// Hard bound on |s|. A guard: settable, never searched.
    pub mispricing_cap: f64,
    /// Crowd valuation gain per day on `s`.
    pub crowd_valuation_gain: f64,
    /// Crowd herding gain per day on yesterday's Δs.
    pub crowd_momentum_gain: f64,
    /// Bound on the crowd's daily log-price shock. A guard.
    pub crowd_lean_cap: f64,

    // ── Session guards (market/tick.rs) ─────────────────────────────────
    /// Circuit-breaker band as a fraction of the session open (±25%
    /// shipped). A guard: settable, never searched.
    pub price_breaker_fraction: f64,
    /// Absolute cap on any model price (50,000 shipped). A guard.
    pub price_hard_cap: f64,

    // ── Derived, computed once at construction (§5.3) ───────────────────
    /// `1 + price_breaker_fraction`. Not in the dict: derived, never set.
    pub breaker_up: f64,
    /// `1 - price_breaker_fraction`. Not in the dict: derived, never set.
    pub breaker_down: f64,
}

/// The shipped preset, built FROM the `pub const`s so the constants remain
/// the single definition and every test pinning one still guards the preset.
pub const PT_V1: ModelParams = ModelParams::pt_v1();

/// The calibrated preset — selectable, and NOT the default.
///
/// Produced by `tools/calibration/calibrate.py` against the re-derived
/// realism bands of 2026-08-22; the certificate that produced it is
/// `tools/calibration/results/calibrate-pt-v2-2026-08-22.json` and the
/// report is `pretium-design/CALIBRATION-PTV2.md`. Built as `pt_v1()` with
/// the calibrated coefficients substituted, for the same reason `PT_V1` is
/// built from the consts: every constant this calibration did not move
/// still has exactly one definition, so a build whose literals drift moves
/// both presets' fingerprints together and neither can quietly diverge from
/// the other.
///
/// The literals below are not typed by hand. They are emitted from the
/// certificate by `tools/calibration/emit_preset.py`, and the test at the
/// bottom of this file pins each one by its IEEE-754 bit pattern — the same
/// convention `mispricing_phi` already uses — so the vector a search found
/// and the vector a build ships are provably the same sixty-four bits.
pub const PT_V2: ModelParams = ModelParams::pt_v2();

/// Every coefficient `pt-v2` moved, with the exact bits the certificate
/// recorded. Emitted beside the preset body by `emit_preset.py` and held to
/// by the test at the bottom of this file — in both directions, so a
/// coefficient that moved without appearing here fails just as loudly as one
/// that drifted from its recorded value.
/// `pt-v3`, the shipped default. Emitted beside the preset body by
/// `emit_preset.py` from the converged certificate and held to by the test
/// at the bottom of this file, in both directions.
pub const PT_V3: ModelParams = ModelParams::pt_v3();

/// Every coefficient `pt-v3` moved, with the exact bits the converged
/// certificate recorded.
const PT_V3_BITS: &[(&str, u64)] = &[
    ("garch_alpha", 0x3FAE_77BA_B2AC_7C70u64),
    ("garch_beta", 0x3FE5_EE19_E4CB_5403u64),
    ("garch_gamma", 0x3FC7_729E_312F_9BF6u64),
    ("idio_sigma_scale", 0x3FEA_1135_9352_B54Bu64),
    ("market_vol_alpha", 0x3FDD_F05F_AB30_7BC3u64),
    ("market_vol_beta", 0x3FE0_AE2D_0FC7_85DDu64),
    ("market_vol_vix_coupling", 0x3FEE_8793_7D1E_2D96u64),
    ("momentum_theta", 0x3FB2_FF2E_48E8_A71Cu64),
];

const PT_V2_BITS: &[(&str, u64)] = &[
    ("garch_alpha", 0x3FB0_319F_E8B2_672Eu64),
    ("garch_beta", 0x3FE7_0C76_769C_A23Fu64),
    ("garch_gamma", 0x3FD5_F5EE_0557_7F56u64),
    ("idio_sigma_scale", 0x3FEA_1135_9352_B54Bu64),
    ("market_vol_alpha", 0x3FDE_2948_3B36_360Au64),
    ("market_vol_beta", 0x3FE0_CDE1_E131_8584u64),
    ("market_vol_vix_coupling", 0x3FEF_6DF9_E384_93FCu64),
    ("momentum_theta", 0x3FB0_0000_0000_0000u64),
];

impl ModelParams {
    /// The shipped preset. `const fn`, so `PT_V1` is a compile-time value
    /// and reading a field is exactly as cheap as reading the const it
    /// mirrors.
    pub const fn pt_v1() -> ModelParams {
        ModelParams {
            market_factor_sigma: tick::MARKET_FACTOR_SIGMA,
            sector_factor_sigma: tick::SECTOR_FACTOR_SIGMA,
            idio_sigma_scale: factor_vol::IDIO_SIGMA_SCALE,
            order_flow_coefficient: factors::ORDER_FLOW_COEFFICIENT,
            informed_flow_fraction: factors::INFORMED_FLOW_FRACTION,
            news_sector_weight: factors::NEWS_SECTOR_WEIGHT,
            news_market_weight: factors::NEWS_MARKET_WEIGHT,
            crash_amplifier_threshold: factors::CRASH_AMPLIFIER_THRESHOLD,
            crash_amplifier_slope: factors::CRASH_AMPLIFIER_SLOPE,
            crisis_blend_ramp: tick::CRISIS_BLEND_RAMP,
            crisis_blend_cap: tick::CRISIS_BLEND_CAP,
            garch_omega: garch::OMEGA,
            garch_alpha: garch::ALPHA,
            garch_beta: garch::BETA,
            garch_gamma: garch::GAMMA,
            garch_ceiling_multiple: garch::CEILING_MULTIPLE,
            garch_floor_multiple: garch::FLOOR_MULTIPLE,
            market_vol_alpha: factor_vol::MARKET_VOL_ALPHA,
            market_vol_beta: factor_vol::MARKET_VOL_BETA,
            market_vol_ceiling_multiple: factor_vol::MARKET_VOL_CEILING_MULTIPLE,
            market_vol_floor_multiple: factor_vol::MARKET_VOL_FLOOR_MULTIPLE,
            market_vol_vix_coupling: factor_vol::MARKET_VOL_VIX_COUPLING,
            market_vol_vix_anchor: factor_vol::MARKET_VOL_VIX_ANCHOR,
            // Legacy values: the slow component is OFF, and the update
            // reduces to the single-component form bit for bit.
            market_vol_slow_persistence: 0.0,
            market_vol_slow_gain: 0.0,
            market_vol_slow_weight: 0.0,
            volume_variance_gain: 0.0,
            mispricing_half_life_days: mispricing::MISPRICING_HALF_LIFE_DAYS,
            mispricing_phi: mispricing::MISPRICING_PHI,
            s_phi_tick: tick::S_PHI_TICK,
            momentum_theta: mispricing::MOMENTUM_THETA,
            mispricing_cap: mispricing::MISPRICING_CAP,
            crowd_valuation_gain: mispricing::CROWD_VALUATION_GAIN,
            crowd_momentum_gain: mispricing::CROWD_MOMENTUM_GAIN,
            crowd_lean_cap: mispricing::CROWD_LEAN_CAP,
            price_breaker_fraction: tick::PRICE_BREAKER_FRACTION,
            price_hard_cap: tick::PRICE_HARD_CAP,
            // Derived once, here, never per call site (§5.3). Const
            // evaluation uses the same IEEE-754 semantics as runtime, and
            // 1 ± 0.25 are exact anyway.
            breaker_up: 1.0 + tick::PRICE_BREAKER_FRACTION,
            breaker_down: 1.0 - tick::PRICE_BREAKER_FRACTION,
        }
    }

    /// The calibrated preset. See [`PT_V2`] for provenance; the body is
    /// generated by `tools/calibration/emit_preset.py` from the certificate.
    pub const fn pt_v2() -> ModelParams {
        let mut p = ModelParams::pt_v1();
        p.garch_alpha = 0.06325721198154774;
        p.garch_beta = 0.7202713314664563;
        p.garch_gamma = 0.34313536187800275;
        p.idio_sigma_scale = 0.8146007420925029;
        p.market_vol_alpha = 0.471269662689196;
        p.market_vol_beta = 0.525132121878571;
        p.market_vol_vix_coupling = 0.9821748202999738;
        p.momentum_theta = 0.0625;
        p
    }

    /// `pt-v3` — the converged margined optimum, and the shipped default
    /// from 2026-08-22.
    ///
    /// Emitted from
    /// `tools/calibration/results/calibrate-pt-v3-converged-2026-08-22.json`
    /// by `emit_preset.py`, pinned bit-for-bit by the test at the bottom of
    /// this file, and built from `pt_v1()` for the same reason `PT_V2` is.
    ///
    /// What separates it from `pt-v2`. The search that produced `pt-v2`
    /// minimised a loss that is flat inside each band, so it parked every
    /// trained-to statistic on a band EDGE — the least robust point it
    /// could occupy. `pt-v3` aims half a seed-sd inside every band while
    /// still reporting against the true bands, and it was run to
    /// convergence rather than stopping on a budget guard. The result is
    /// `L_real` 0.0000 on all three 252-day axes and 0.0058 on the 504-day
    /// one, against `pt-v2`'s 0.0002 / 0.0000 / 0.0000 / 2.2252.
    pub const fn pt_v3() -> ModelParams {
        let mut p = ModelParams::pt_v1();
        p.garch_alpha = 0.059507211981547736;
        p.garch_beta = 0.6853150814664563;
        p.garch_gamma = 0.18318536187800277;
        p.idio_sigma_scale = 0.8146007420925029;
        p.market_vol_alpha = 0.46779624669755665;
        p.market_vol_beta = 0.5212617214385166;
        p.market_vol_vix_coupling = 0.9540498202999739;
        p.momentum_theta = 0.07420624999999997;
        p
    }

    /// Look a shipped preset up by name. `"pt-v1"` remains selectable and
    /// bit-reproducing forever; `"pt-v2"` is the calibrated candidate that
    /// joined the table on 2026-08-22 (CALIBRATION-PTV2.md); `"pt-v3"` is
    /// the converged margined optimum that replaced it as the default the
    /// same day (CALIBRATION-FOLLOWUPS.md §7.5).
    ///
    /// Note what this function does NOT decide: which preset an engine
    /// gets when the caller names none. That is `engine.rs`'s and
    /// `python_engine.rs`'s default, and as of the `pt-v3` era boundary it
    /// is [`PT_V3`]. `pt-v1` and `pt-v2` remain selectable and
    /// bit-reproducing forever, so a result recorded under either replays
    /// exactly by naming it.
    pub fn preset(name: &str) -> Option<ModelParams> {
        match name {
            "pt-v1" => Some(PT_V1),
            "pt-v2" => Some(PT_V2),
            "pt-v3" => Some(PT_V3),
            _ => None,
        }
    }

    /// Names of the shipped presets, for error messages.
    pub fn preset_names() -> &'static [&'static str] {
        &["pt-v1", "pt-v2", "pt-v3"]
    }

    /// Read one parameter by name — the settable surface, the derived bits,
    /// and the carried read-only surface alike. `None` for unknown names.
    pub fn get(&self, name: &str) -> Option<f64> {
        // The carried read-only surface first: constant across every
        // instance in a build, present in the dict and the fingerprint.
        if let Some(v) = carried_read_only(name) {
            return Some(v);
        }
        Some(match name {
            "market_factor_sigma" => self.market_factor_sigma,
            "sector_factor_sigma" => self.sector_factor_sigma,
            "idio_sigma_scale" => self.idio_sigma_scale,
            "order_flow_coefficient" => self.order_flow_coefficient,
            "informed_flow_fraction" => self.informed_flow_fraction,
            "news_sector_weight" => self.news_sector_weight,
            "news_market_weight" => self.news_market_weight,
            "crash_amplifier_threshold" => self.crash_amplifier_threshold,
            "crash_amplifier_slope" => self.crash_amplifier_slope,
            "crisis_blend_ramp" => self.crisis_blend_ramp,
            "crisis_blend_cap" => self.crisis_blend_cap,
            "garch_omega" => self.garch_omega,
            "garch_alpha" => self.garch_alpha,
            "garch_beta" => self.garch_beta,
            "garch_gamma" => self.garch_gamma,
            "garch_ceiling_multiple" => self.garch_ceiling_multiple,
            "garch_floor_multiple" => self.garch_floor_multiple,
            "market_vol_alpha" => self.market_vol_alpha,
            "market_vol_beta" => self.market_vol_beta,
            "market_vol_ceiling_multiple" => self.market_vol_ceiling_multiple,
            "market_vol_floor_multiple" => self.market_vol_floor_multiple,
            "market_vol_vix_coupling" => self.market_vol_vix_coupling,
            "market_vol_vix_anchor" => self.market_vol_vix_anchor,
            "market_vol_slow_persistence" => self.market_vol_slow_persistence,
            "market_vol_slow_gain" => self.market_vol_slow_gain,
            "market_vol_slow_weight" => self.market_vol_slow_weight,
            "volume_variance_gain" => self.volume_variance_gain,
            "mispricing_half_life_days" => self.mispricing_half_life_days,
            "mispricing_phi" => self.mispricing_phi,
            "s_phi_tick" => self.s_phi_tick,
            "momentum_theta" => self.momentum_theta,
            "mispricing_cap" => self.mispricing_cap,
            "crowd_valuation_gain" => self.crowd_valuation_gain,
            "crowd_momentum_gain" => self.crowd_momentum_gain,
            "crowd_lean_cap" => self.crowd_lean_cap,
            "price_breaker_fraction" => self.price_breaker_fraction,
            "price_hard_cap" => self.price_hard_cap,
            _ => return None,
        })
    }

    /// Apply one override. Returns a NEW value — no mutation.
    ///
    /// Refuses, by name and with the reason: unknown names, non-finite
    /// values, the derived bits (`mispricing_phi`, `s_phi_tick`), and the
    /// carried read-only surface. Overriding `mispricing_half_life_days`
    /// with a value different from the current one recomputes both derived
    /// coefficients via `mathx::pow`; an override equal to the current value
    /// keeps the recorded bits.
    pub fn with_override(&self, name: &str, value: f64) -> Result<ModelParams, String> {
        if !value.is_finite() {
            return Err(format!("{name} must be finite, got {value}"));
        }
        if name == "mispricing_phi" || name == "s_phi_tick" {
            return Err(format!(
                "{name} is a derived coefficient carried as recorded bits; it \
                 cannot be set directly. Override mispricing_half_life_days \
                 and both are recomputed from it — deterministically, but not \
                 bit-identically to the recorded constants."
            ));
        }
        if carried_read_only(name).is_some() {
            return Err(format!(
                "{name} is in the preset but is not yet runtime-settable: it \
                 is compile-time in this build, and accepting an override the \
                 engine would ignore would make the fingerprint a lie. The \
                 settable surface is: {}",
                settable_names().join(", ")
            ));
        }
        let mut out = self.clone();
        match name {
            "market_factor_sigma" => out.market_factor_sigma = value,
            "sector_factor_sigma" => out.sector_factor_sigma = value,
            "idio_sigma_scale" => out.idio_sigma_scale = value,
            "order_flow_coefficient" => out.order_flow_coefficient = value,
            "informed_flow_fraction" => out.informed_flow_fraction = value,
            "news_sector_weight" => out.news_sector_weight = value,
            "news_market_weight" => out.news_market_weight = value,
            "crash_amplifier_threshold" => out.crash_amplifier_threshold = value,
            "crash_amplifier_slope" => out.crash_amplifier_slope = value,
            "crisis_blend_ramp" => out.crisis_blend_ramp = value,
            "crisis_blend_cap" => out.crisis_blend_cap = value,
            "garch_omega" => out.garch_omega = value,
            "garch_alpha" => out.garch_alpha = value,
            "garch_beta" => out.garch_beta = value,
            "garch_gamma" => out.garch_gamma = value,
            "garch_ceiling_multiple" => out.garch_ceiling_multiple = value,
            "garch_floor_multiple" => out.garch_floor_multiple = value,
            "market_vol_alpha" => out.market_vol_alpha = value,
            "market_vol_beta" => out.market_vol_beta = value,
            "market_vol_ceiling_multiple" => out.market_vol_ceiling_multiple = value,
            "market_vol_floor_multiple" => out.market_vol_floor_multiple = value,
            "market_vol_vix_coupling" => out.market_vol_vix_coupling = value,
            "market_vol_vix_anchor" => out.market_vol_vix_anchor = value,
            "market_vol_slow_persistence" => out.market_vol_slow_persistence = value,
            "market_vol_slow_gain" => out.market_vol_slow_gain = value,
            "market_vol_slow_weight" => out.market_vol_slow_weight = value,
            "volume_variance_gain" => out.volume_variance_gain = value,
            "momentum_theta" => out.momentum_theta = value,
            "mispricing_cap" => out.mispricing_cap = value,
            "crowd_valuation_gain" => out.crowd_valuation_gain = value,
            "crowd_momentum_gain" => out.crowd_momentum_gain = value,
            "crowd_lean_cap" => out.crowd_lean_cap = value,
            "price_breaker_fraction" => {
                if !(value > -1.0) || value >= 1.0 {
                    return Err(format!(
                        "price_breaker_fraction must be inside (-1, 1) for \
                         the band to exist, got {value}"
                    ));
                }
                out.price_breaker_fraction = value;
                // The §5.3 rule: derived once, here.
                out.breaker_up = 1.0 + value;
                out.breaker_down = 1.0 - value;
            }
            "price_hard_cap" => out.price_hard_cap = value,
            "mispricing_half_life_days" => {
                if !(value > 0.0) {
                    return Err(format!(
                        "mispricing_half_life_days must be greater than \
                         zero, got {value}"
                    ));
                }
                out.mispricing_half_life_days = value;
                // Bits are the contract: an override EQUAL to the current
                // half-life keeps the recorded constants, because sameness
                // of value must mean sameness of bits. A different value
                // recomputes both — deterministic on a given build, not
                // bit-identical to any recorded constant (API §3).
                if value.to_bits() != self.mispricing_half_life_days.to_bits() {
                    out.mispricing_phi = crate::mathx::pow(0.5, 1.0 / value);
                    out.s_phi_tick = crate::mathx::pow(0.5, 1.0 / (value * 390.0));
                }
            }
            other => {
                return Err(format!(
                    "unknown model parameter {other:?}. The settable surface \
                     is: {}",
                    settable_names().join(", ")
                ));
            }
        }
        Ok(out)
    }

    /// The full preset surface as sorted `(name, value)` pairs: the settable
    /// fields, the derived bits, and the carried read-only constants. This
    /// is what the fingerprint hashes and what the manifest embeds.
    pub fn to_pairs(&self) -> Vec<(String, f64)> {
        let mut out: Vec<(String, f64)> = Vec::new();
        for name in settable_names() {
            out.push((name.to_string(), self.get(name).expect("settable")));
        }
        out.push(("mispricing_phi".to_string(), self.mispricing_phi));
        out.push(("s_phi_tick".to_string(), self.s_phi_tick));
        for (name, value) in carried_read_only_pairs() {
            out.push((name, value));
        }
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out
    }

    /// sha256 over the canonical serialisation, full hex. Names sorted,
    /// values as big-endian IEEE-754 bit patterns — the known-answer
    /// convention, so decimal formatting can never differ for reasons that
    /// are not the model.
    pub fn digest(&self) -> String {
        let mut hasher = Sha256::new();
        for (name, value) in self.to_pairs() {
            hasher.update(name.as_bytes());
            hasher.update(b"=");
            hasher.update(value.to_bits().to_be_bytes());
            hasher.update(b"\n");
        }
        let out = hasher.finalize();
        let mut hex = String::with_capacity(64);
        for byte in out {
            hex.push_str(&format!("{byte:02x}"));
        }
        hex
    }

    /// The honest name: a shipped preset's name when bit-identical to it,
    /// `custom-XXXXXXXX` (first 8 hex of the digest) otherwise. A run under
    /// a non-shipped preset can never present as a standard one.
    pub fn fingerprint(&self) -> String {
        let digest = self.digest();
        for name in Self::preset_names() {
            if let Some(preset) = Self::preset(name) {
                if preset.digest() == digest {
                    return (*name).to_string();
                }
            }
        }
        format!("custom-{}", &digest[..8])
    }
}

/// The settable names, sorted. A function rather than the const above so
/// the list is derived from `to_pairs`' actual coverage in tests.
pub fn settable_names() -> Vec<&'static str> {
    vec![
        "crash_amplifier_slope",
        "crash_amplifier_threshold",
        "crisis_blend_cap",
        "crisis_blend_ramp",
        "crowd_lean_cap",
        "crowd_momentum_gain",
        "crowd_valuation_gain",
        "garch_alpha",
        "garch_beta",
        "garch_ceiling_multiple",
        "garch_floor_multiple",
        "garch_gamma",
        "garch_omega",
        "idio_sigma_scale",
        "informed_flow_fraction",
        "market_factor_sigma",
        "market_vol_alpha",
        "market_vol_beta",
        "market_vol_ceiling_multiple",
        "market_vol_floor_multiple",
        "market_vol_slow_gain",
        "market_vol_slow_persistence",
        "market_vol_slow_weight",
        "market_vol_vix_anchor",
        "market_vol_vix_coupling",
        "mispricing_cap",
        "mispricing_half_life_days",
        "momentum_theta",
        "news_market_weight",
        "news_sector_weight",
        "order_flow_coefficient",
        "price_breaker_fraction",
        "price_hard_cap",
        "sector_factor_sigma",
        "volume_variance_gain",
    ]
}

/// The carried read-only surface: in the preset (visible, versioned,
/// fingerprinted), not yet threaded, override refused. Values come straight
/// from the consts so this cannot drift from the build.
fn carried_read_only(name: &str) -> Option<f64> {
    use crate::economy::state as econ;
    use crate::fair_value as fv;
    use crate::microstructure as micro;
    if let Some(rest) = name.strip_prefix("sector_daily_sigma_") {
        return crate::sectors::by_key(rest).map(|s| s.daily_sigma);
    }
    Some(match name {
        "daily_shock_cap" => mispricing::DAILY_SHOCK_CAP,
        "crisis_vix_threshold" => econ::CRISIS_VIX_THRESHOLD,
        "neutral_discount_rate" => fv::NEUTRAL_DISCOUNT_RATE,
        "rate_pe_sensitivity" => fv::RATE_PE_SENSITIVITY,
        "rate_adjustment_floor" => fv::RATE_ADJUSTMENT_FLOOR,
        "growth_duration_scale" => fv::GROWTH_DURATION_SCALE,
        "loss_making_price_to_book" => fv::LOSS_MAKING_PRICE_TO_BOOK,
        "fair_value_floor" => fv::FAIR_VALUE_FLOOR,
        "default_sector_anchor_pe" => fv::DEFAULT_SECTOR_ANCHOR_PE,
        "book_levels" => micro::BOOK_LEVELS,
        "inventory_limit_levels" => micro::INVENTORY_LIMIT_LEVELS,
        "inflation_target" => econ::INFLATION_TARGET,
        "phillips_curve_coeff" => econ::PHILLIPS_CURVE_COEFF,
        "oil_baseline" => econ::OIL_BASELINE,
        "gold_equilibrium_base" => econ::GOLD_EQUILIBRIUM_BASE,
        "gold_mean_reversion" => econ::GOLD_MEAN_REVERSION,
        "vix_mean_reversion" => econ::VIX_MEAN_REVERSION,
        "fiscal_multiplier" => econ::FISCAL_MULTIPLIER,
        _ => return None,
    })
}

/// Every carried read-only pair, for `to_pairs`.
fn carried_read_only_pairs() -> Vec<(String, f64)> {
    let mut out: Vec<(String, f64)> = [
        "daily_shock_cap",
        "crisis_vix_threshold",
        "neutral_discount_rate",
        "rate_pe_sensitivity",
        "rate_adjustment_floor",
        "growth_duration_scale",
        "loss_making_price_to_book",
        "fair_value_floor",
        "default_sector_anchor_pe",
        "book_levels",
        "inventory_limit_levels",
        "inflation_target",
        "phillips_curve_coeff",
        "oil_baseline",
        "gold_equilibrium_base",
        "gold_mean_reversion",
        "vix_mean_reversion",
        "fiscal_multiplier",
    ]
    .iter()
    .map(|n| (n.to_string(), carried_read_only(n).expect("listed")))
    .collect();
    for sector in crate::sectors::SECTORS.iter() {
        out.push((
            format!("sector_daily_sigma_{}", sector.key),
            sector.daily_sigma,
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_shipped_preset_fingerprints_as_its_own_name() {
        assert_eq!(PT_V1.fingerprint(), "pt-v1");
        assert_eq!(ModelParams::preset("pt-v1").unwrap().fingerprint(), "pt-v1");
        assert_eq!(PT_V2.fingerprint(), "pt-v2");
        assert_eq!(ModelParams::preset("pt-v2").unwrap().fingerprint(), "pt-v2");
        assert_eq!(PT_V3.fingerprint(), "pt-v3");
        assert_eq!(ModelParams::preset("pt-v3").unwrap().fingerprint(), "pt-v3");
        assert!(ModelParams::preset("pt-v4").is_none());
    }

    #[test]
    fn the_three_presets_are_three_different_models() {
        // Same guard as `the_calibrated_preset_is_a_different_model_from_
        // the_shipped_one`, extended: a pt_v3() body holding pt-v2's values
        // would compile, pass every other test, and ship an era boundary
        // that moved nothing while the fingerprint rule reported it as
        // `pt-v2` and hid the mistake.
        assert_ne!(PT_V3.digest(), PT_V1.digest());
        assert_ne!(PT_V3.digest(), PT_V2.digest());
    }

    #[test]
    fn the_default_preset_is_pt_v3() {
        // The era boundary, asserted rather than assumed. An engine built
        // without a model gets pt-v3 from 2026-08-22; if this flips back,
        // every published figure silently describes a different market.
        assert_eq!(crate::params::PT_V3.fingerprint(), "pt-v3");
        assert_eq!(
            crate::engine::Engine::default_model().fingerprint(),
            "pt-v3"
        );
    }

    #[test]
    fn the_default_preset_holds_the_bits_the_converged_certificate_recorded() {
        // Emitted by tools/calibration/emit_preset.py from
        // results/calibrate-pt-v3-converged-2026-08-22.json.
        for (name, bits) in PT_V3_BITS {
            assert_eq!(
                PT_V3.get(name).unwrap().to_bits(),
                *bits,
                "{name} drifted from the certificate"
            );
        }
    }

    #[test]
    fn the_calibrated_preset_is_a_different_model_from_the_shipped_one() {
        // The guard against the failure this file cannot otherwise catch:
        // a `pt_v2()` body left holding pt-v1's values would compile, pass
        // every other test, and ship a "calibrated" preset that calibrates
        // nothing — while the fingerprint rule, working exactly as designed,
        // reported it as `pt-v1` and hid the mistake.
        assert_ne!(PT_V2.digest(), PT_V1.digest());
    }

    #[test]
    fn the_calibrated_preset_holds_the_bits_the_certificate_recorded() {
        // Emitted by tools/calibration/emit_preset.py from
        // results/calibrate-pt-v2-2026-08-22.json. Bit patterns rather than
        // decimals for the reason `mispricing_phi` is pinned that way: a
        // decimal that round-trips today is a decimal, and the claim being
        // made is about sixty-four bits.
        for (name, bits) in PT_V2_BITS {
            assert_eq!(
                PT_V2.get(name).unwrap().to_bits(),
                *bits,
                "{name} drifted from the certificate"
            );
        }
        // Everything the calibration did not move is pt-v1's, unchanged.
        for name in settable_names() {
            if PT_V2_BITS.iter().all(|(moved, _)| *moved != name) {
                assert_eq!(
                    PT_V2.get(name).unwrap().to_bits(),
                    PT_V1.get(name).unwrap().to_bits(),
                    "{name} moved without being in the certificate"
                );
            }
        }
    }

    #[test]
    fn any_override_fingerprints_as_custom_and_is_stable() {
        let a = PT_V1.with_override("garch_alpha", 0.12).unwrap();
        let b = PT_V1.with_override("garch_alpha", 0.12).unwrap();
        let fp = a.fingerprint();
        assert!(fp.starts_with("custom-"), "{fp}");
        assert_eq!(fp.len(), "custom-".len() + 8);
        assert_eq!(fp, b.fingerprint(), "same values must fingerprint alike");
        let c = PT_V1.with_override("garch_alpha", 0.13).unwrap();
        assert_ne!(fp, c.fingerprint(), "different values must not collide");
    }

    #[test]
    fn an_override_equal_to_the_preset_is_still_the_preset() {
        // Bit-identity is the membership rule, not construction history.
        let same = PT_V1
            .with_override("garch_alpha", crate::market::garch::ALPHA)
            .unwrap();
        assert_eq!(same.fingerprint(), "pt-v1");
    }

    #[test]
    fn unknown_and_read_only_names_are_refused_by_name() {
        assert!(PT_V1.with_override("garch_alfa", 0.1).is_err());
        let err = PT_V1.with_override("oil_baseline", 80.0).unwrap_err();
        assert!(err.contains("not yet runtime-settable"), "{err}");
        let err = PT_V1.with_override("mispricing_phi", 0.9).unwrap_err();
        assert!(err.contains("derived"), "{err}");
        assert!(PT_V1.with_override("garch_alpha", f64::NAN).is_err());
    }

    #[test]
    fn the_shipped_half_life_keeps_the_recorded_bits_and_a_new_one_recomputes() {
        let same = PT_V1.with_override("mispricing_half_life_days", 60.0).unwrap();
        assert_eq!(same.mispricing_phi.to_bits(), 0x3FEF_A1E8_27A1_B38C);
        assert_eq!(same.s_phi_tick.to_bits(), 0x3FEF_FFC1_E138_5E9E);
        assert_eq!(same.fingerprint(), "pt-v1");

        let faster = PT_V1.with_override("mispricing_half_life_days", 30.0).unwrap();
        assert_ne!(faster.mispricing_phi.to_bits(), PT_V1.mispricing_phi.to_bits());
        assert_ne!(faster.s_phi_tick.to_bits(), PT_V1.s_phi_tick.to_bits());
        // 30 steps of the recomputed phi must halve a mispricing.
        let mut decayed = 1.0f64;
        for _ in 0..30 {
            decayed *= faster.mispricing_phi;
        }
        assert!((decayed - 0.5).abs() < 1e-13, "30-day decay was {decayed}");
        // And the per-tick form compounds to the daily one.
        let mut compounded = 1.0f64;
        for _ in 0..390 {
            compounded *= faster.s_phi_tick;
        }
        assert!((compounded - faster.mispricing_phi).abs() < 1e-12);
        assert!(faster.fingerprint().starts_with("custom-"));
    }

    #[test]
    fn every_settable_name_is_readable_and_every_pair_is_covered() {
        for name in settable_names() {
            assert!(PT_V1.get(name).is_some(), "{name} not readable");
            // Round-trip: overriding with its own value must succeed.
            assert!(
                PT_V1.with_override(name, PT_V1.get(name).unwrap()).is_ok(),
                "{name} not settable"
            );
        }
        // The dict covers settable + derived bits + carried read-only, with
        // no duplicates.
        let pairs = PT_V1.to_pairs();
        let mut names: Vec<&str> = pairs.iter().map(|(n, _)| n.as_str()).collect();
        let before = names.len();
        names.dedup();
        assert_eq!(before, names.len(), "duplicate names in the preset dict");
        assert_eq!(
            before,
            settable_names().len() + 2 + 18 + 12,
            "settable + derived bits + carried read-only + sector sigmas"
        );
    }

    #[test]
    fn the_breaker_band_is_derived_once_at_construction() {
        assert_eq!(PT_V1.breaker_up, 1.25);
        assert_eq!(PT_V1.breaker_down, 0.75);
        let wider = PT_V1.with_override("price_breaker_fraction", 0.5).unwrap();
        assert_eq!(wider.breaker_up, 1.5);
        assert_eq!(wider.breaker_down, 0.5);
    }
}
