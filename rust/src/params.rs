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
    /// How much the sector draw's variance follows VIX, on the same
    /// `(VIX / anchor)^2` target the market factor's variance uses
    /// (`factor_vol.rs`). At 0.0 the sector sigma is static, bit-identical by
    /// branch. At 1.0 it scales fully.
    ///
    /// MEASURED 2026-08-25 (§59, §60): a static sector sigma is the one
    /// variance term on the tick path that does not scale with stress, so any
    /// positive `sector_factor_sigma` raises calm volatility and leaves crisis
    /// volatility alone, and the crisis lever falls by a tenth on pt-v3 and
    /// pt-v6 alike (3.07x to 2.78x, 2.68x to 2.49x). This is the term that
    /// lets sector structure exist without paying that.
    pub sector_vix_coupling: f64,
    /// Daily sigma of each shared sector factor, loaded at 0.5 by every
    /// member of the sector (`market/factors.rs`).
    ///
    /// Shipped at 0.002 in every preset, which is about a quarter of one
    /// percent of a name's daily variance: in practice the model has had a
    /// market factor and nothing else, and residual correlation after it has
    /// been diagonal. Nothing measured that until 2026-08-25, when
    /// `sector_excess_corr` (same-sector minus cross-sector mean pairwise
    /// correlation) joined the panel and read 0.004 on the shipped preset
    /// against a real band of 0.11 to 0.23, fifteen seed-sd out. The pt-v1
    /// search had reported this parameter as a null direction, which is what
    /// a lever looks like when the objective cannot see what it moves.
    ///
    /// MEASURED, thirty seeds, pt-v3 base, 252 days
    /// (CALIBRATION-FOLLOWUPS.md §59 and §60): monotone and roughly
    /// quadratic in sigma; 0.012 puts `sector_excess_corr` at 0.155, inside
    /// its band, with no other panel statistic leaving its own; 0.020
    /// overshoots the band and drops kurtosis through its floor. Two costs the
    /// panel does not show: the crisis volatility lever falls from 3.07x to
    /// 2.78x because the added variance is not VIX-coupled, and kurtosis
    /// thins by about 0.3 seed-sd at 504 days. And above the crisis
    /// threshold the blend in `market/tick.rs` replaces the sector draw with
    /// the market draw, so whatever this is set to, sector structure reads
    /// zero at VIX 45 (CRISIS-BLEND-SECTOR.md). Raising it is an era boundary.
    pub sector_factor_sigma: f64,
    /// Scale on the per-name idiosyncratic GARCH sigma — the funding side
    /// of the factor-variance reallocation. Bit-inert at 1.0.
    pub idio_sigma_scale: f64,
    /// Order-flow impact coefficient, before the informed fraction.
    pub order_flow_coefficient: f64,
    /// Permanent (information) share of order-flow impact.
    pub informed_flow_fraction: f64,
    /// How fast endogenous inflation reverts toward its 2% target each
    /// month, as a fraction of the gap.
    ///
    /// Shipped 0.55, the hard-coded value every preset ran on: a half-life
    /// under a month. Promoted to a dial on 2026-08-25 (calibration record
    /// §65) because the one coefficient sets both how persistent inflation
    /// is and how far it can wander, and the endogenous economy reaches
    /// neither the persistence (monthly acf1 0.936 against real CPI's
    /// 0.978, FRED CPIAUCSL 2015-2025) nor the dispersion (sd 1.23 against
    /// 2.18; peak 4.1% against 9.0%) of the real series. Lower is more
    /// persistent and wider. Nothing else in the economy is touched; at
    /// 0.55 every preset reproduces bit for bit.
    pub inflation_reversion: f64,
    /// The hard ceiling on endogenous inflation, in percent.
    ///
    /// Shipped 6.0, the clamp every preset ran under. Promoted with
    /// `inflation_reversion` (§65): once the reversion is loosened enough
    /// to give inflation its real dispersion, the series sits on this clamp
    /// where the real one reached 9.0% in June 2022 (FRED CPIAUCSL). At 6.0
    /// every preset reproduces bit for bit. The floor stays at -1.0.
    pub inflation_ceiling: f64,
    /// The hard floor on endogenous inflation, in percent.
    ///
    /// Shipped -1.0, the clamp every preset ran under. Promoted with the
    /// ceiling (§65): with the reversion loosened to the real dispersion the
    /// series sits on this floor at every setting, where real CPI
    /// year-on-year bottomed at -0.2 in 2015-2025 and -2.0 in 2009 (FRED
    /// CPIAUCSL). At -1.0 every preset reproduces bit for bit.
    pub inflation_floor: f64,
    /// Weight of sector-scoped news on a member name (§5.4 promotion).
    pub news_sector_weight: f64,
    /// Weight of market-wide news on every name (§5.4 promotion).
    pub news_market_weight: f64,
    /// Weight of one company's GOOD news on its sector peers — the
    /// information-transfer channel.
    ///
    /// Before this, a company-tagged event moved only the company it named.
    /// The news dispatch is an if/else-if chain whose sector branch requires
    /// `company_id.is_none()`, so an earnings beat at one cloud name reached
    /// no other cloud name, in either direction. Sector co-movement existed
    /// but arrived entirely as exogenous shared shocks — a per-tick sector
    /// factor draw and market beta — never as contagion from a member.
    ///
    /// Real markets transfer: a surprise at one name moves its close
    /// competitors, typically at a fraction of the announcer's move (Foster
    /// 1981; Freeman and Tse 1992). The realism panel cannot see this at all,
    /// because `cross_sectional_corr` is unconditional and dominated by the
    /// market factor, while transfer is a conditional, event-time effect.
    ///
    /// 0.0 means no transfer, which is every preset before pt-v4, and the
    /// branch is skipped entirely at zero rather than adding `0.0 * impact`
    /// — so it is bit-identical when off, not merely numerically close.
    pub news_peer_weight: f64,
    /// Weight of one company's BAD news on its sector peers.
    ///
    /// Separate from [`ModelParams::news_peer_weight`] because the effect is
    /// asymmetric in the literature: negative surprises transfer more
    /// strongly than positive ones. One parameter with a sign flip would
    /// impose symmetry the data does not support, and a search cannot
    /// discover the asymmetry it was never given room to express.
    ///
    /// Applies when the event's price impact is negative. 0.0 is inert.
    pub news_peer_weight_down: f64,
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
    /// Where the crisis blend takes its correlation from. At 0.0 the sector
    /// draw is attenuated by the spike and the market factor is injected
    /// through the sector slot, which is the reference behaviour and is
    /// bit-identical by branch. At 1.0 the sector draw is left intact and the
    /// same market injection is added to the market component directly.
    ///
    /// MEASURED 2026-08-25 (CALIBRATION-FOLLOWUPS.md §60, CRISIS-BLEND-SECTOR.md):
    /// with the sector draw consumed, `sector_excess_corr` reads -0.007 at a
    /// held VIX 45 whatever `sector_factor_sigma` is, where the real 2020
    /// window reads +0.10; and a longer window reads lower sector excess than
    /// a shorter one on every base, because it contains more crisis days.
    /// Written when the sector draw carried nothing worth keeping.
    pub crisis_blend_source: f64,

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
    /// Applies the loss-maker book floor to PROFITABLE companies too.
    ///
    /// At 0.0 this is off and the valuation is bit-identical to the TypeScript
    /// reference, which switches hard at `eps > 0`: a company earning 0.01 is
    /// valued on earnings and one earning exactly 0 is valued at
    /// `book * LOSS_MAKING_PRICE_TO_BOOK`. Fair value therefore JUMPS UP as
    /// earnings fall through zero, and a barely profitable company is worth
    /// less than a loss-making one with the same book.
    ///
    /// At 1.0 the floor applies on both sides, `max(eps * pe, book * 1.2)`, so
    /// fair value is continuous at zero and non-decreasing in earnings.
    ///
    /// Inert by default because it is NOT a small correction: 42.8% of
    /// instruments from `Universe.random` sit below the floor, some at a fifth
    /// of it, so switching it on re-values a large part of any universe and
    /// re-bases every calibrated statistic. It exists so that a time-varying
    /// earnings path has somewhere monotonic to run; adopting it is an era
    /// boundary and a recalibration, not a bug fix.
    pub fair_value_book_floor: f64,
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

    // ── Universe memory (market/tick.rs, engine.rs) ─────────────────────
    /// How slowly the universe's remembered stress decays, per day.
    ///
    /// The crisis correlation blend is otherwise a LOOKUP ON TODAY'S VIX:
    /// `spike = min(cap, (vix - threshold) / ramp)` with no state at all,
    /// so the tick VIX falls back under the threshold and the whole
    /// cross-section decouples in the same tick. A crisis leaves the
    /// universe exactly as it found it.
    ///
    /// Real correlation does not work that way -- it spikes with the shock
    /// and decays over weeks, which is the most-observed crisis fact there
    /// is and the one this model could not produce. This carries a stress
    /// level that ratchets up instantly and decays geometrically, so an
    /// event has an effect that OUTLIVES it.
    ///
    /// 0.0 means the level never survives a day and the blend is exactly
    /// what it always was.
    pub universe_stress_decay: f64,
    /// How much of the remembered stress reaches the correlation blend.
    ///
    /// 0.0 disables the memory entirely; the blend then reads today's VIX
    /// and nothing else, bit for bit.
    pub universe_stress_weight: f64,
    /// Stress the business cycle contributes, in VIX-equivalent points at
    /// full intensity (contraction).
    ///
    /// The engine runs a five-phase cycle -- expansion, peak, contraction,
    /// trough, recovery -- that the MARKET has never read. The central
    /// bank changes its whole policy by phase; the price process behaves
    /// as though the economy were always expanding. This is the wire.
    ///
    /// It feeds the same remembered stress VIX does, so a contraction
    /// raises correlation across the whole cross-section and keeps it
    /// raised while the phase lasts and for weeks after it ends. Regime
    /// switching is also, per Diebold and Inoue, indistinguishable from
    /// long memory in the data -- so this may reproduce the decay curve a
    /// second variance timescale was added to chase.
    ///
    /// 0.0 means the market ignores the cycle, which is every preset
    /// before pt-v4.
    pub regime_stress_points: f64,
    /// How far the SLOW variance component's target is decoupled from VIX,
    /// in [0, 1].
    ///
    /// With a shared target the two components chase VIX together, so adding
    /// a slow one makes the mixture track a spike MORE sluggishly than the
    /// fast component alone -- the opposite of what a scenario transient
    /// needs. The measurement says that is the live defect: pt-v3 retains
    /// 95.2% of pt-v1's steady-state VIX lever and only 27.6% of its
    /// transient, because one timescale is doing two jobs. Within-year
    /// clustering wants long memory; tracking a twenty-day spike wants short.
    ///
    /// At 0.0 the slow component tracks VIX exactly as the fast one does and
    /// the branch is skipped, so every preset before pt-v4 is bit-identical.
    /// At 1.0 it ignores VIX entirely and reverts to the autonomous baseline,
    /// leaving the fast component to carry the whole response.
    ///
    /// # Measured, and the motivation above is REFUTED
    ///
    /// This was built on the reasoning that a VIX-coupled slow component
    /// blunts the transient, so decoupling it should sharpen the response.
    /// Swept at thirty seeds against eighteen configurations, damping makes
    /// both the shock ratio AND the steady-state lever monotonically WORSE,
    /// at every fast persistence and every weight:
    ///
    /// | damp | shock | lever |
    /// |---|---|---|
    /// | 0.0 | 1.228 | 4.446 |
    /// | 0.5 | 1.208 | 4.116 |
    /// | 1.0 | 1.194 | 3.821 |
    ///
    /// The reasoning was wrong in a nameable way: response SPEED is set by
    /// the fast component's persistence, while the slow component's VIX
    /// coupling contributes GAIN, not lag. Removing it removes gain.
    ///
    /// Kept, inert, because a measured negative is worth keeping and a
    /// search may still find a use for it in a region this grid did not
    /// cover. But nothing should set it above zero on the strength of the
    /// argument that produced it.
    ///
    /// What DID restore the transient was the fast component's persistence:
    /// 0.95 gives shock 1.228 and lever 4.446 where 0.97 gives 1.170 and
    /// 3.944. pt-v3 raised it to 0.989 to buy clustering, and that single
    /// choice is what cost the scenario response.
    ///
    /// Neither lever is near real markets regardless: measured on the 40-name
    /// reference roster, real is x6.16 (17.2% annualised below VIX 12 against
    /// 106.1% above VIX 45) against roughly x3.1 here. "Restore pt-v1's
    /// lever" was never the right target.
    pub market_vol_slow_vix_damp: f64,

    // ── Endogenous jumps (engine.rs, applied at the day close) ──────────
    /// Daily probability that a MARKET-WIDE jump fires.
    ///
    /// The model has no discontinuities without this. Prices diffuse; real
    /// markets gap. Nothing surprises this market unless a caller injects
    /// news by hand, and that is why excess kurtosis reads 5.2 over 504-day
    /// windows against real markets' 7.1 to 22 — fat tails at that scale are
    /// not reachable from a diffusion plus GARCH at any coefficients.
    ///
    /// Jumps are drawn from their OWN RNG stream ([`crate::rng::stream::JUMPS`]),
    /// which is what lets a draw-consuming mechanism ship inert: at intensity
    /// 0 the draws still happen, but they happen on a stream no earlier preset
    /// ever touched, so the market, economy and external streams are
    /// bit-identical and every shipped preset reproduces exactly.
    pub jump_intensity_market: f64,
    /// Mean of the market jump in log-return units. NEGATIVE by intent:
    /// real crash jumps are asymmetric, and a symmetric jump process
    /// produces fat tails with the wrong skew — which would read as
    /// "kurtosis fixed" on the panel while getting crises backwards.
    pub jump_mean_market: f64,
    /// Standard deviation of the market jump, in log-return units.
    pub jump_sigma_market: f64,
    /// Daily probability that a per-name idiosyncratic jump fires — the
    /// earnings-surprise channel, independent across names.
    pub jump_intensity_idio: f64,
    /// Standard deviation of the idiosyncratic jump, in log-return units.
    pub jump_sigma_idio: f64,
    /// Cross-sectional spread in volatility persistence, in raw `beta`
    /// units. Zero is every preset before pt-v7 and is bit-identical.
    ///
    /// Every instrument reads the same `garch_alpha`, `garch_beta` and
    /// `garch_gamma` off this struct, so volatility memory is homogeneous
    /// across the cross-section. The decay-shape gap is that real markets'
    /// volatility autocorrelation decays hyperbolically and this model's
    /// decays exponentially, and the envelope records a two-component
    /// variance mixture failing to close it. That mixture is two timescales
    /// WITHIN a name; this is heterogeneity ACROSS names, which is a
    /// different mechanism and the one Granger (1980) identifies as
    /// producing long memory from short-memory components.
    ///
    /// Above zero, a name's persistence moves with its size: `beta` plus
    /// `dispersion * clamp(log(cap / reference) / scale, -1, 1)`, so the reference
    /// cap gets exactly `garch_beta` and the spread is bounded by
    /// `dispersion` in both directions. Derived from the roster rather than
    /// from a draw, so the RNG stream schedule is untouched and no earlier
    /// preset's trajectory moves.
    ///
    /// Clamped so GJR persistence `alpha + beta + gamma/2` stays below
    /// [`GARCH_PERSISTENCE_CEILING`]. A name whose variance process is not
    /// stationary does not produce fat tails, it produces a number that
    /// grows until a guard catches it.
    ///
    /// # It does NOT close the decay-shape gap, which is what it was for
    ///
    /// Measured, thirty seeds at 504 days (CALIBRATION-FOLLOWUPS §54). The
    /// log-log slope of the `|r|` autocorrelation over lags 1 to 20 reads
    /// −0.436 in real markets and −0.933 on pt-v6. At dispersion 0.15 it
    /// reads −0.944, which is further from real, not closer. The spread
    /// shaves a little off the short lags and more off the long ones: lag 30
    /// goes from 0.0026 to 0.0001 against a real 0.0179.
    ///
    /// A toy AR/GARCH simulation had it roughly doubling the `acf20/acf1`
    /// ratio (§52). The engine carries a GJR term, a factor variance process
    /// and VIX coupling on top, and the result did not transfer.
    ///
    /// What it DOES do, measured on the same run, is improve room at 504 on
    /// `excess_kurtosis`, +0.58 to +0.74 seed-sd, and on
    /// `annualised_vol_pct`, +0.17 to +0.34. That is the honest claim for it.
    /// It also moves `abs_return_acf5` across its 504 band edge, but by 0.09
    /// seed-sd, which §36's flip margin exists to say is not a real crossing.
    pub garch_beta_dispersion: f64,

    /// How much of a jump the herding term is allowed to continue.
    ///
    /// A jump lands on `mispricing_s`, and the momentum term reads the
    /// change in `s` across closes. So by default a jump is a re-rating like
    /// any other and `momentum_theta` carries a share of it into the next
    /// day. That is the coupling behind the trade recorded in §34 and
    /// reinstated in §37: the only mechanism that reaches the 504-day tail
    /// also pushes 252-day return autocorrelation out of its band, because
    /// fattening the tail and adding continuation are the same act.
    ///
    /// This splits them. At `1.0` the jump feeds herding exactly as before,
    /// which is what every shipped preset does and why they reproduce bit
    /// for bit. Below `1.0` the jump moves the momentum reference point with
    /// it, so herding sees the post-jump level as the new baseline rather
    /// than as a change to continue. At `0.0` the jump is invisible to
    /// momentum: it decays on `s_phi` alone, giving a fat tail with no
    /// continuation attached to it.
    ///
    /// The jump's own mean reversion is unchanged either way -- it always
    /// decays back through the existing mispricing process. What moves is
    /// only whether the herding term amplifies it on the way.
    pub jump_momentum_share: f64,

    // ── Persistent volume (engine.rs close, market/tick.rs phase 3) ─────
    /// Day-to-day persistence of the shared volume component, in [0, 1).
    ///
    /// Volume is otherwise a LEVEL -- `avg_volume` scaled by multipliers,
    /// with an independent uniform each tick. Consecutive volumes are then
    /// near-independent draws around a fixed level, and differencing that
    /// gives a change autocorrelation near -0.5 at ANY coefficients. That is
    /// why `volume_change_acf1` sits 13.7 seed-sd outside a real band of
    /// -0.32 to -0.20 and is excluded from the calibration objective as
    /// structurally unreachable: no parameter reaches a row whose defect is
    /// the absence of a process.
    ///
    /// This supplies the process: a log-scale AR(1) multiplier, so a busy
    /// day is followed by a busy day. It models the COMMON component only --
    /// market-wide volume persistence shared by every name. Real volume
    /// persistence is partly idiosyncratic too (a name in play stays in
    /// play), and that part is NOT modelled here, because per-name state
    /// would touch the column and checkpoint surface for a second-order
    /// effect. Stated so the limitation is on the record rather than
    /// discovered later.
    ///
    /// 0.0 with a zero innovation leaves the multiplier at exactly 1.0 and
    /// the branch is skipped, so every preset before pt-v4 is bit-identical.
    pub volume_persistence: f64,
    /// Standard deviation of the daily log-volume innovation.
    pub volume_innovation_sigma: f64,

    // ── Continuous size effect (market/factors.rs) ──────────────────────
    /// Blend from the four-tier size step toward a continuous power law,
    /// in [0, 1].
    ///
    /// The step function gives a $49B company 1.0 and a $51B company 0.8 --
    /// a 25% jump in idiosyncratic volatility from a $2B difference. Every
    /// name lands on one of four levels, which puts cliffs in the
    /// cross-section that no real market has and compresses the dispersion
    /// of volatility across names into four spikes.
    ///
    /// 0.0 returns the step value by branch, so every preset before pt-v4 is
    /// bit-identical. 1.0 is the pure power law.
    pub size_effect_smoothness: f64,
    /// Exponent of the continuous size effect: `(cap / 25B) ^ -exponent`.
    ///
    /// Ships at 0.15, fitted to the step function's own tiers where those
    /// tiers are informative -- 5B reads 1.273 against a step of 1.3, 25B
    /// reads 1.000 against 1.0, 100B reads 0.812 against 0.8. It departs
    /// below $1B, where the step stops being a size effect and becomes a
    /// floor, and that departure is the mechanism's purpose rather than an
    /// error in the fit.
    pub size_effect_exponent: f64,
    /// Blend from the four-tier SPREAD step toward a continuous power law,
    /// in [0, 1].
    ///
    /// The step charges 10 bps at $1B and 30 bps at $0.9B -- a three times
    /// jump in transaction cost from a rounding error in capitalisation. Any
    /// execution study spanning that edge measures the tier rather than the
    /// size effect. 0.0 is the step and is bit-identical.
    pub spread_size_smoothness: f64,
    /// Exponent of the continuous spread curve. Ships at 0.455, least-squares
    /// fitted to the step's own four tiers: 29.65 bps against 30 at $0.5B,
    /// 10.40 against 10 at $5B, 5.00 against 5 at $25B, 2.66 against 3 at
    /// $100B.
    pub spread_size_exponent: f64,

    // ── Crisis gates (economy/daily.rs, market/tick.rs, engine.rs) ──────
    /// How fast VIX reverts toward its target each day.
    ///
    /// Promoted from carried-read-only, because a const answers a
    /// calibration question before anyone asks it. This is the OTHER side of
    /// the scenario transient: the defect is that a 63-day variance
    /// half-life cannot track a twenty-day VIX spike, and where a faster
    /// variance process was measured to cost long-horizon realism, a longer
    /// SPIKE touches variance persistence not at all.
    pub vix_mean_reversion: f64,
    /// VIX points added to its target per unit of a DOWN day's index
    /// return, before the clamp and cap below.
    ///
    /// Shipped 25.0, a literal in the VIX update. MEASURED against real
    /// markets (FRED VIXCLS and SP500, 2,511 common days to 2026-08): a
    /// session at -3% or worse moves the VIX a median of +6.03 points, and
    /// -2% to -1% moves it +1.95. The shipped gain with the shipped clamp
    /// adds at most 0.75 points to the TARGET, of which the day traverses
    /// `vix_mean_reversion`, about 0.09 points. The consequence is
    /// measurable in the panel: over 252 days the endogenous VIX has sd 1.5
    /// against a real within-year median of 4.0 and crosses its own crisis
    /// threshold on 0.0% of days against a real 12.5%, so a one-year run
    /// contains no volatility episode and lag-5 clustering sits on its band
    /// floor. Calibration record §68.
    /// How much of the VIX's target is the market's own volatility.
    ///
    /// Shipped 0.0, and bit-identical there by branch. At zero the VIX is a
    /// function of the business cycle phase, a one-day return spike that
    /// decays with a 5.4-day half-life, and noise: the market's own realised
    /// volatility is not an input to it. MEASURED consequence (§68): real
    /// implied volatility tracks trailing 21-day realised volatility at
    /// +0.818 (FRED VIXCLS against SP500, 2,491 days), the model at +0.275
    /// on pt-v3 and +0.337 on pt-v8, and the endogenous VIX crosses its own
    /// crisis threshold on 0.0% of days in a year against a real 12.5%.
    /// This market cannot frighten itself.
    ///
    /// At `w` the target becomes `(1 - w) * target + w * implied`, where
    /// `implied` is the market factor's current sigma read back through the
    /// same anchor the forward coupling uses: `market_vol_vix_anchor *
    /// sigma_today / market_factor_sigma`. Using the forward map's own
    /// inverse means the loop is consistent at equilibrium and introduces no
    /// second calibration constant. The VIX clamp of 10 to 80 and the
    /// factor's ceiling multiple bound the feedback.
    pub vix_realised_vol_weight: f64,
    /// Which return the VIX reacts to: the last TICK's (0.0, shipped) or the
    /// day's (1.0), blended in between.
    ///
    /// The VIX's return channel reads `market_return_pct`, which the engine
    /// builds from `previous_tick_price`: the cap-weighted move over the
    /// final minute of the session, not the session. MEASURED (§70): an
    /// index day of -7.87% moves the VIX +0.15 points, half the worst days
    /// move it DOWN, and with the gain raised to 5000 and the clamp opened
    /// the day's index return still correlates -0.065 with the next day's
    /// VIX change. Raising the gain amplifies the closing minute, which is
    /// noise, which is why every gain sweep in §68 and §69 did nothing.
    ///
    /// At 1.0 the channel reads the day's cap-weighted open-to-close return
    /// instead, in the same percent units, which includes the jumps that
    /// `apply_jumps` adds after the tick loop. Real markets move the VIX
    /// about 2 points per percent the index falls, so a calibrated setting
    /// is source 1.0 with `vix_return_gain` near 2.0 and
    /// `vix_return_clamp` in percentage points rather than fractions.
    pub vix_return_source: f64,
    /// How much of the VIX's level comes from the business cycle.
    ///
    /// The VIX target starts at a constant per cycle phase: 14 in expansion,
    /// 18 at a peak, 25 in contraction, 22 in a trough, 16 in recovery. Those
    /// five numbers move on a multi-YEAR clock, which is why the model's
    /// volatility clustering is a function of the measurement window: lag-5
    /// clustering reads 0.0136 over 252 days, below its 0.02 floor, and
    /// 0.0828 over 504, because only the longer window contains a phase
    /// change. Real markets read inside 0.02 to 0.09 at ONE year and 0.02 to
    /// 0.10 at two: their clustering comes from episodes lasting weeks, not
    /// from the cycle.
    ///
    /// At 1.0, shipped, the five constants are used as they are. At `a` each
    /// is pulled toward their mean of 19.0: `19.0 + a * (phase - 19.0)`, so
    /// 0.0 makes the cycle contribute nothing to the VIX and any episodes
    /// have to come from the market. Combines with `vix_return_source`, which
    /// is what supplies episodes in the first place (§70, §71).
    pub vix_cycle_amplitude: f64,
    /// VIX points added to its target per unit of a DOWN day's index
    /// return, before the clamp and cap below.
    ///
    /// Shipped 25.0, a literal in the VIX update. MEASURED against real
    /// markets (FRED VIXCLS and SP500, 2,511 common days to 2026-08): a
    /// session at -3% or worse moves the VIX a median of +6.03 points, and
    /// -2% to -1% moves it +1.95. The shipped gain with the shipped clamp
    /// adds at most 0.75 points to the TARGET, of which the day traverses
    /// `vix_mean_reversion`, about 0.09 points. Raising it to the real slope
    /// is NOT the lever, measured: it moves the within-year VIX sd from 1.54
    /// to 1.79 against a real 4.0 and leaves lag-5 clustering where it was
    /// (§68). What was missing is the feedback above, not the gain.
    pub vix_return_gain: f64,
    /// The same for an UP day. Shipped 10.0; the real response to a +2% day
    /// is about half the size of the response to -2%, which the shipped
    /// 2.5:1 ratio is already close to.
    pub vix_return_gain_up: f64,
    /// The index return is clamped to +/- this before it drives the VIX.
    ///
    /// Shipped 0.03, so a -10% day and a -3% day produce identical fear. A
    /// crash is exactly where that assumption is worst.
    pub vix_return_clamp: f64,
    /// Ceiling on the VIX target's whole excursion, in points: the return
    /// spike plus the inflation and shock adjustments. Shipped 12.0, which
    /// binds long before a real crisis does.
    pub vix_target_shock_cap: f64,
    /// VIX level at which crisis behaviour begins.
    ///
    /// Gates the sector-to-market correlation blend, the universe stress
    /// memory and the economy's crisis premium -- three mechanisms whose
    /// trigger point nobody has ever been able to search. 25.5 is the P94 of
    /// the long-run endogenous VIX distribution, chosen so the trigger is
    /// reachable at all; whether it is the RIGHT point is a different
    /// question and now an answerable one.
    pub crisis_vix_threshold: f64,

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
/// The 504-day variant. Selectable, not the default -- see [`ModelParams::pt_v4`].
pub const PT_V4: ModelParams = ModelParams::pt_v4();

/// pt-v4 with the jump decoupled from herding -- see [`ModelParams::pt_v5`].
pub const PT_V5: ModelParams = ModelParams::pt_v5();

/// pt-v5 with the herding term halved -- see [`ModelParams::pt_v6`].
pub const PT_V6: ModelParams = ModelParams::pt_v6();
/// pt-v6 with sector structure that survives a crisis -- see [`ModelParams::pt_v7`].
pub const PT_V7: ModelParams = ModelParams::pt_v7();
/// pt-v7 with the market factor's variance given a memory -- see [`ModelParams::pt_v8`].
pub const PT_V8: ModelParams = ModelParams::pt_v8();
/// pt-v8 with a market that frightens itself -- see [`ModelParams::pt_v9`].
pub const PT_V9: ModelParams = ModelParams::pt_v9();
/// pt-v9 with volume that remembers -- see [`ModelParams::pt_v10`].
pub const PT_V10: ModelParams = ModelParams::pt_v10();

/// The name of the preset an engine runs when none is named.
///
/// This exists because the name and the coefficients drifted apart once
/// already, and silently. `model_preset()`'s default argument was the
/// literal `"pt-v1"` and stayed that way when [`crate::engine::Engine`]'s
/// default moved to [`PT_V3`], so the library answered "you are running
/// pt-v1, momentum_theta 0.25" for runs that had actually executed pt-v3
/// at 0.0742 — and `manifest.py` folded those wrong coefficients into the
/// run digest whose stated job is catching exactly that substitution.
///
/// So the name is a const beside the params it names, and the test at the
/// bottom of this file asserts it resolves to the engine's default
/// bit-for-bit. A future era that moves the default and forgets this
/// constant fails the suite instead of mislabelling every manifest.
pub const DEFAULT_PRESET_NAME: &str = "pt-v10";

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
            inflation_reversion: crate::economy::INFLATION_MEAN_REVERSION,
            inflation_ceiling: crate::economy::INFLATION_CEILING,
            inflation_floor: crate::economy::INFLATION_FLOOR,
            news_sector_weight: factors::NEWS_SECTOR_WEIGHT,
            news_market_weight: factors::NEWS_MARKET_WEIGHT,
            crash_amplifier_threshold: factors::CRASH_AMPLIFIER_THRESHOLD,
            crash_amplifier_slope: factors::CRASH_AMPLIFIER_SLOPE,
            crisis_blend_ramp: tick::CRISIS_BLEND_RAMP,
            crisis_blend_cap: tick::CRISIS_BLEND_CAP,
            crisis_blend_source: 0.0,
            sector_vix_coupling: 0.0,
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
            fair_value_book_floor: 0.0,
            market_vol_slow_weight: 0.0,
            volume_variance_gain: 0.0,
            universe_stress_decay: 0.0,
            universe_stress_weight: 0.0,
            regime_stress_points: 0.0,
            market_vol_slow_vix_damp: 0.0,
            jump_intensity_market: 0.0,
            jump_mean_market: 0.0,
            jump_sigma_market: 0.0,
            jump_intensity_idio: 0.0,
            jump_sigma_idio: 0.0,
            garch_beta_dispersion: 0.0,
            jump_momentum_share: 1.0,
            volume_persistence: 0.0,
            volume_innovation_sigma: 0.0,
            size_effect_smoothness: 0.0,
            size_effect_exponent: 0.15,
            spread_size_smoothness: 0.0,
            spread_size_exponent: crate::microstructure::SPREAD_SIZE_EXPONENT,
            vix_mean_reversion: crate::economy::VIX_MEAN_REVERSION,
            vix_realised_vol_weight: 0.0,
            vix_cycle_amplitude: 1.0,
            vix_return_source: 0.0,
            vix_return_gain: crate::economy::VIX_RETURN_GAIN,
            vix_return_gain_up: crate::economy::VIX_RETURN_GAIN_UP,
            vix_return_clamp: crate::economy::VIX_RETURN_CLAMP,
            vix_target_shock_cap: crate::economy::VIX_TARGET_SHOCK_CAP,
            crisis_vix_threshold: crate::economy::CRISIS_VIX_THRESHOLD,
            news_peer_weight: 0.0,
            news_peer_weight_down: 0.0,
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

    /// The 504-day variant: pt-v3 plus endogenous jumps and a live volume
    /// process. NOT the default, deliberately.
    ///
    /// Searched on the dual-horizon objective over nine parameters, all of
    /// which ship inert on pt-v3 (CALIBRATION-FOLLOWUPS §33). It halves the
    /// combined loss -- 0.9887 to 0.4863 on the training seeds and 1.3990
    /// to 0.7493 on thirty seeds it never saw -- and closes the thin-tails
    /// gap that no calibration had moved: `excess_kurtosis` at 504 days
    /// goes 5.23 to 9.19, inside the 7.1-22 band for the first time.
    ///
    /// It is a TRADE, and that is why it does not take the default. At the
    /// CERTIFIED horizon of 252 days it is worse than pt-v3: eight of ten
    /// statistics in band against nine, losing `return_acf1` at 0.074-0.084
    /// against a ceiling of 0.06 -- out on the training seeds, on held-out
    /// seeds and on a held-out 60-name universe alike, so a regression
    /// rather than a fluctuation. At 504 days it is better, seven of ten
    /// against five.
    ///
    /// Choose it when the question is a multi-year one. The envelope
    /// certifies pt-v3 at 252 days and that claim is not weakened by this
    /// preset existing beside it.
    pub const fn pt_v4() -> ModelParams {
        let mut p = ModelParams::pt_v3();
        p.jump_intensity_market = 0.086555921159823;
        p.jump_intensity_idio = 0.02271987289851697;
        p.jump_mean_market = -0.008521833617959641;
        p.jump_sigma_market = 0.0023793153879054386;
        p.jump_sigma_idio = 0.062369653817277396;
        p.volume_persistence = 0.07231783926786545;
        p.volume_innovation_sigma = 0.1504517786623244;
        p.volume_variance_gain = 0.028403829887593345;
        p.market_factor_sigma = 0.015879388479656826;
        p
    }

    /// pt-v4 with the jump decoupled from the herding term.
    ///
    /// pt-v4 reaches the 504-day tail and pays for it at one year: eight of
    /// ten statistics in band against pt-v3's nine, losing `return_acf1`.
    /// CALIBRATION-FOLLOWUPS §34 concluded that no search over pt-v4's nine
    /// parameters escapes that trade, and §37 reinstated the conclusion
    /// after it was refuted on screening evidence and the refutation was
    /// wrong. Both stand: the trade is not a search artifact.
    ///
    /// §38 located it. A jump is applied to `mispricing_s` at the day
    /// close, and the momentum roll earlier in the same close has already
    /// set `mispricing_s_prev_close` to the pre-jump value. So at the next
    /// close the roll sees the jump as a re-rating and `momentum_theta`
    /// continues a share of it. Fattening the tail and adding return
    /// continuation were the same write to the same variable, which is why
    /// no coefficient could separate them.
    ///
    /// `jump_momentum_share` at 0.0 advances the momentum reference with
    /// the jump, so herding never sees it. The jump still decays back
    /// through the existing mispricing process; it is simply not amplified
    /// on the way.
    ///
    /// **Nine of ten in band at 252 days AND the 504-day tail held**, which
    /// no earlier preset manages: pt-v3 holds nine and misses the tail by
    /// 0.50 sd, pt-v4 reaches the tail and drops to eight. Measured on
    /// thirty training seeds, on held-out seeds and on a held-out 60-name
    /// universe (§38). Dual-horizon loss 0.087 against pt-v4's 0.486 and
    /// pt-v3's 0.989.
    ///
    /// Crisis behaviour is unchanged: vol lever retained 100.6% of pt-v4,
    /// correlation blend identical (§39). It passes §8 on every axis, both
    /// the loss thresholds and the flip test (§45).
    ///
    /// NOT the default. The envelope certifies pt-v3 at 252 days, and
    /// certification is a separate act from passing the controls.
    ///
    /// `volume_change_acf1` remains out of band here as everywhere, for the
    /// structural reason recorded when it was excluded from the objective.
    pub const fn pt_v5() -> ModelParams {
        let mut p = ModelParams::pt_v4();
        p.jump_momentum_share = 0.0;
        p
    }

    /// pt-v5 with the herding term halved.
    ///
    /// `momentum_theta` multiplies yesterday's CHANGE in mispricing into
    /// today's, so it is return continuation by construction. pt-v5 fixed
    /// one-year continuation by stopping jumps feeding it
    /// (CALIBRATION-FOLLOWUPS §38) and left the two-year reading at 0.0605
    /// against a 504-day ceiling of 0.04.
    ///
    /// §48 ruled out the macro chain and showed the estimator's
    /// finite-sample bias cancels against horizon-matched bands, so the miss
    /// was genuine. §49 ablated the two remaining candidates: lowering
    /// factor-variance persistence makes continuation WORSE and loses the
    /// tail, and shortening the mispricing half-life does nothing. Halving
    /// `momentum_theta` from 0.0742 to 0.0371 moves `return_acf1` at 504
    /// days from 0.0605 out of band to 0.0346 inside it, and no other
    /// statistic changes band at either horizon.
    ///
    /// **Nine of ten at 252 days and eight of ten at 504**, against pt-v5's
    /// nine and seven, on thirty training seeds. The two remaining misses at
    /// 504 are `abs_return_acf5`, which lives inside the decay-shape gap,
    /// and `volume_change_acf1`, which is structurally unreachable.
    ///
    /// Gates: vol lever retained 99.8% of pt-v5 with the correlation blend
    /// marginally better (§50), and §8 passes on every axis with the horizon
    /// losses falling from 0.0870 and 0.1192 to 0.0007 and 0.0000.
    ///
    /// The obvious objection was that halving the coefficient which makes
    /// returns trend would remove a momentum strategy's edge. Measured with
    /// a paired sign test over twenty-four seeds, no preset gives momentum a
    /// reliable edge over hold: pt-v3 is 8-16, pt-v5 is 13-11, this is
    /// 10-14, none significant (§51). There is no edge to remove.
    ///
    /// NOT the default. pt-v3 keeps that and the envelope certifies pt-v3;
    /// passing the controls is not certification.
    pub const fn pt_v6() -> ModelParams {
        let mut p = ModelParams::pt_v5();
        // Exactly half of pt-v3's 0.07420624999999997, asserted in tests
        // rather than trusted: a literal that drifted from the value it
        // claims to halve would be a preset nobody calibrated.
        p.momentum_theta = 0.03710312499999999;
        p
    }

    /// pt-v6 with industries: the first preset in which names in the same
    /// sector co-move more than names in different ones, in calm markets and
    /// in a crisis alike.
    ///
    /// Six coefficients move from pt-v6 (CALIBRATION-FOLLOWUPS.md §58 to
    /// §63). `sector_factor_sigma` 0.002 to 0.012 gives the sector draw real
    /// variance. `crisis_blend_source` 0.0 to 1.0 stops the crisis blend
    /// consuming that draw and injects the market factor through the market
    /// component instead. `sector_vix_coupling` 0.0 to 0.25 lets a quarter of
    /// the sector variance follow VIX, so a crisis is more violent rather than
    /// less. `idio_sigma_scale` is trimmed by ten percent to pay for the added
    /// variance, which on this base RAISES kurtosis, because the tails come
    /// from the jumps and the trimmed term was diluting them.
    /// `crisis_blend_cap` 0.8 to 0.98 raises the market factor's share in a
    /// crisis; it binds only above the crisis threshold, so the calm panels are
    /// bit-identical with and without it, and every crisis measure improves:
    /// crisis-state cross-sectional correlation 0.586 to 0.610 against a real
    /// 0.6 and above, the volatility lever 2.95x to 3.06x, the correlation
    /// blend 2.47x to 2.55x. The §62 survey found the cap to be the one axis
    /// monotone on crisis correlation and the lever together.
    /// `market_vol_ceiling_multiple` 8 to 16 lets the market factor's variance
    /// reach sixteen times its calm level before the clamp binds, which it
    /// does only in a crisis: the calm panels are identical to three places
    /// across 8, 12, 16 and 24 (§63), the lever rises 3.06x to 3.31x, and
    /// crisis kurtosis, the number that could have refused it, holds at 3.0.
    /// At 24 the 504-day panel loses `abs_return_acf5`, so 16 is the last
    /// free rung.
    ///
    /// MEASURED, thirty training seeds, thirteen statistics: twelve of
    /// thirteen in band at 252 days and at 504, the only miss being the
    /// structural `volume_change_acf1`; pt-v6 holds eleven and ten. Sector
    /// excess 0.128 at 252 and 0.118 at 504 against a band of 0.11 to 0.23,
    /// and +0.079 in a held VIX 45 against the real 2020 window's +0.103, with
    /// crisis cross-sectional correlation 0.610 against pt-v6's 0.600.
    /// Gates, in the record: the response instrument against pt-v6, held-out
    /// seeds and universe, §8 against pt-v6's passing control.
    ///
    /// NOT the default. pt-v3 keeps that and the envelope certifies pt-v3.
    pub const fn pt_v7() -> ModelParams {
        let mut p = ModelParams::pt_v6();
        p.sector_factor_sigma = 0.012;
        p.crisis_blend_source = 1.0;
        p.sector_vix_coupling = 0.25;
        // pt-v6's 0.8146007420925029 times 0.90, asserted in tests.
        p.idio_sigma_scale = 0.7331406678832526;
        p.crisis_blend_cap = 0.98;
        p.market_vol_ceiling_multiple = 16.0;
        p
    }

    /// pt-v7 with the market factor's variance given a memory: the first
    /// preset in which correlation that rose last month is still elevated
    /// this month, and the most violent crisis the model has produced.
    ///
    /// Seven coefficients move from pt-v7 (CALIBRATION-FOLLOWUPS.md §64).
    /// The factor GARCH runs alpha 0.298 / beta 0.665 (persistence 0.963,
    /// alpha share 0.31) in place of pt-v7's 0.468 / 0.521, whose fourth
    /// moment did not exist and whose variance therefore had no window-to-
    /// window memory although the VIX it targets has. pt-v8's index
    /// 3a^2 + 2ab + b^2 is 1.11 against 1.42: closer, still above one, so the
    /// gain below is measured rather than implied by the moment condition. `market_factor_sigma`
    /// falls to 0.0088 and `idio_sigma_scale` to 0.653 to re-set the levels
    /// the old alpha was holding; the market jumps and sector sigma move
    /// within noise of pt-v7's and are carried at the surveyed values.
    ///
    /// MEASURED, thirty training seeds, fourteen statistics: thirteen of
    /// fourteen at 504 days (volume_change_acf1 out) with correlation
    /// persistence +0.315 against a real band of 0.19 to 0.49 and pt-v7's
    /// +0.251; twelve of fourteen at 252 (abs_return_acf5 a quarter of a
    /// noise unit under its floor, priced by the survey on every qualifying
    /// vector). Crisis lever 4.34x against pt-v7's 3.31x (real 6.16x),
    /// correlation blend 3.16x, shock response 1.083, VIX 5 volatility 24.5
    /// against 28.4. Held-out universe 13/14, held-out seeds 11/14, §8 no
    /// flips. Cost stated: crisis-state sector excess +0.053 against pt-v7's
    /// +0.079 (real +0.10).
    ///
    /// NOT the default. pt-v3 keeps that and the envelope certifies pt-v3.
    pub const fn pt_v8() -> ModelParams {
        let mut p = ModelParams::pt_v7();
        p.market_factor_sigma = 0.008829098749522557;
        p.market_vol_alpha = 0.2983752950979363;
        p.market_vol_beta = 0.6647431226131493;
        p.idio_sigma_scale = 0.6525931444846045;
        p.jump_intensity_market = 0.07195215610657195;
        p.jump_sigma_market = 0.0028601822465565054;
        p.sector_factor_sigma = 0.011863939388471967;
        p
    }

    /// pt-v8 with a market that frightens itself: the first preset whose
    /// volatility episodes are produced by the market rather than driven
    /// through a scenario.
    ///
    /// Seven coefficients move from pt-v8 (CALIBRATION-FOLLOWUPS.md §68 to
    /// §72). `vix_return_source` 1.0 makes the VIX's fear channel read the
    /// day's cap-weighted index return instead of the closing minute, which
    /// is what it read before: with the shipped channel a -7.87% day moved
    /// the VIX +0.15 points and the correlation between the day's return and
    /// the next day's VIX change was -0.065 even with the gain at 5000.
    /// `vix_return_gain` 17.0, symmetric, with `vix_return_clamp` 15.0 and
    /// `vix_target_shock_cap` 45.0 in percentage points calibrate that
    /// channel against real markets, which move the VIX about 2 points per
    /// percent the index falls. The index gets its crash frequency from that
    /// channel rather than from bigger jumps: 1.10% of days below -3% against
    /// a real 1.27% and pt-v8's 0.73%, with the market jump left at pt-v8's
    /// size. Raising it to 0.020 was tried and REMOVED before release (§72):
    /// it overshot the crash frequency to 2.29% and cost the crisis blend a
    /// third, 3.16x to 2.29x. `vix_cycle_amplitude` 0.6 takes the volatility regimes off the
    /// multi-year business cycle clock, which is why lag-5 clustering used to
    /// depend on the measurement window. `momentum_theta` halves again to
    /// keep `return_acf1` inside its band at two years.
    ///
    /// MEASURED, thirty training seeds, fourteen statistics: **thirteen of
    /// fourteen in band at 252 days AND at 504**, which no earlier preset
    /// holds, the only miss being the structural `volume_change_acf1`.
    /// Volatility 30.3 and 33.6, kurtosis 7.65 and 8.04, cross-sectional
    /// correlation 0.320 and 0.402, sector excess +0.128 and +0.110,
    /// correlation persistence +0.156 and +0.276, lag-5 clustering 0.0375 and
    /// 0.0961, sector excess +0.144 and +0.121. Held-out universe 13/14; §8
    /// no flips on any axis. The endogenous VIX has a within-year sd of 3.98
    /// against a real 4.0 where pt-v8 reads 1.53, and reaches its own crisis
    /// threshold on 2.5% of days against a real 12.5% and pt-v8's 0.0%.
    ///
    /// It costs nothing measured: the crisis blend holds at 3.16x and the
    /// volatility lever rises to 4.31x from pt-v8's 4.34x-equivalent
    /// measurement, both under a pinned VIX.
    ///
    /// NOT the default. pt-v3 keeps that and the envelope certifies pt-v3.
    pub const fn pt_v9() -> ModelParams {
        let mut p = ModelParams::pt_v8();
        p.momentum_theta = 0.018551562499999993;
        p.vix_cycle_amplitude = 0.6;
        p.vix_return_clamp = 15.0;
        p.vix_return_gain = 17.0;
        p.vix_return_gain_up = 17.0;
        p.vix_return_source = 1.0;
        p.vix_target_shock_cap = 45.0;
        p
    }

    /// pt-v9 with volume that remembers: the first preset holding ALL
    /// FOURTEEN realism statistics in band at the certified horizon.
    ///
    /// Three coefficients move from pt-v9 (CALIBRATION-FOLLOWUPS.md §73,
    /// §76). `vix_cycle_amplitude` 0.6 to 0.0 takes the business cycle out of
    /// the VIX entirely: the five phase constants pulled the level to about
    /// 17.4 in a typical year against a real 18.3, and the market crossed its
    /// own crisis threshold on 2.7% of days against a real 12.5%. At zero the
    /// level reads 19.6, the within-year sd 4.54 against a real 4.0, and the
    /// threshold is crossed on 10.2% of days. Volatility regimes now come
    /// from the market rather than from the calendar, which is what §71
    /// measured them to need. The
    /// engine carries a common log-volume state, an AR(1) that has shipped
    /// switched off since pt-v1; `volume_persistence` 0.70 and
    /// `volume_innovation_sigma` 0.21 turn it on. `volume_change_acf1` is the
    /// statistic every earlier preset misses, and the envelope has called it
    /// unreachable without spending `volume_abs_return_corr`, because a
    /// market-wide volume multiplier adds volume variance unrelated to any
    /// name's own moves. That trade was priced on the pt-v3 era base. On this
    /// one both bands are reachable together, and the window is narrow: at
    /// innovation sigma 0.20 the change autocorrelation is still 0.005 past
    /// its edge and at 0.23 the correlation has left its floor.
    ///
    /// MEASURED, thirty training seeds: **fourteen of fourteen in band at 252
    /// days**, `volume_change_acf1` -0.3140 against a band of -0.32 to -0.20
    /// and `volume_abs_return_corr` 0.4824 against 0.46 to 0.66, with
    /// volatility 28.9, kurtosis 8.83, cross-sectional correlation 0.254,
    /// sector excess +0.146 and correlation persistence +0.181. A HELD-OUT
    /// 60-name universe also reads fourteen of fourteen. At 504 days it holds
    /// twelve: the volume statistic leaves again, and lag-5 clustering sits
    /// 0.03 seed sd past its ceiling. §8 passes on every axis with no flips.
    ///
    /// It costs nothing measured: the crisis lever reads 4.30x against
    /// pt-v9's 4.31x, the correlation blend 3.13x against 3.16x and the shock
    /// ratio 1.078 against 1.084, all inside noise.
    ///
    /// NOT the default. pt-v3 keeps that and the envelope certifies pt-v3.
    pub const fn pt_v10() -> ModelParams {
        let mut p = ModelParams::pt_v9();
        p.vix_cycle_amplitude = 0.0;
        p.volume_innovation_sigma = 0.21;
        p.volume_persistence = 0.7;
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
            "pt-v4" => Some(PT_V4),
            "pt-v5" => Some(PT_V5),
            "pt-v6" => Some(PT_V6),
            "pt-v7" => Some(PT_V7),
            "pt-v8" => Some(PT_V8),
            "pt-v9" => Some(PT_V9),
            "pt-v10" => Some(PT_V10),
            _ => None,
        }
    }

    /// Names of the shipped presets, for error messages.
    pub fn preset_names() -> &'static [&'static str] {
        &["pt-v1", "pt-v2", "pt-v3", "pt-v4", "pt-v5", "pt-v6", "pt-v7", "pt-v8", "pt-v9", "pt-v10"]
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
            "inflation_ceiling" => self.inflation_ceiling,
            "inflation_floor" => self.inflation_floor,
            "inflation_reversion" => self.inflation_reversion,
            "informed_flow_fraction" => self.informed_flow_fraction,
            "news_sector_weight" => self.news_sector_weight,
            "news_market_weight" => self.news_market_weight,
            "crash_amplifier_threshold" => self.crash_amplifier_threshold,
            "crash_amplifier_slope" => self.crash_amplifier_slope,
            "crisis_blend_ramp" => self.crisis_blend_ramp,
            "crisis_blend_cap" => self.crisis_blend_cap,
            "crisis_blend_source" => self.crisis_blend_source,
            "sector_vix_coupling" => self.sector_vix_coupling,
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
            "fair_value_book_floor" => self.fair_value_book_floor,
            "market_vol_slow_weight" => self.market_vol_slow_weight,
            "volume_variance_gain" => self.volume_variance_gain,
            "universe_stress_decay" => self.universe_stress_decay,
            "universe_stress_weight" => self.universe_stress_weight,
            "regime_stress_points" => self.regime_stress_points,
            "market_vol_slow_vix_damp" => self.market_vol_slow_vix_damp,
            "jump_intensity_market" => self.jump_intensity_market,
            "jump_mean_market" => self.jump_mean_market,
            "jump_sigma_market" => self.jump_sigma_market,
            "jump_intensity_idio" => self.jump_intensity_idio,
            "jump_sigma_idio" => self.jump_sigma_idio,
            "garch_beta_dispersion" => self.garch_beta_dispersion,
            "jump_momentum_share" => self.jump_momentum_share,
            "volume_persistence" => self.volume_persistence,
            "volume_innovation_sigma" => self.volume_innovation_sigma,
            "size_effect_smoothness" => self.size_effect_smoothness,
            "size_effect_exponent" => self.size_effect_exponent,
            "spread_size_smoothness" => self.spread_size_smoothness,
            "spread_size_exponent" => self.spread_size_exponent,
            "vix_mean_reversion" => self.vix_mean_reversion,
            "vix_cycle_amplitude" => self.vix_cycle_amplitude,
            "vix_realised_vol_weight" => self.vix_realised_vol_weight,
            "vix_return_clamp" => self.vix_return_clamp,
            "vix_return_gain" => self.vix_return_gain,
            "vix_return_gain_up" => self.vix_return_gain_up,
            "vix_return_source" => self.vix_return_source,
            "vix_target_shock_cap" => self.vix_target_shock_cap,
            "crisis_vix_threshold" => self.crisis_vix_threshold,
            "news_peer_weight" => self.news_peer_weight,
            "news_peer_weight_down" => self.news_peer_weight_down,
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
            "inflation_ceiling" => out.inflation_ceiling = value,
            "inflation_floor" => out.inflation_floor = value,
            "inflation_reversion" => out.inflation_reversion = value,
            "informed_flow_fraction" => out.informed_flow_fraction = value,
            "news_sector_weight" => out.news_sector_weight = value,
            "news_market_weight" => out.news_market_weight = value,
            "crash_amplifier_threshold" => out.crash_amplifier_threshold = value,
            "crash_amplifier_slope" => out.crash_amplifier_slope = value,
            "crisis_blend_ramp" => out.crisis_blend_ramp = value,
            "crisis_blend_cap" => out.crisis_blend_cap = value,
            "crisis_blend_source" => out.crisis_blend_source = value,
            "sector_vix_coupling" => out.sector_vix_coupling = value,
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
            "fair_value_book_floor" => out.fair_value_book_floor = value,
            "market_vol_slow_weight" => out.market_vol_slow_weight = value,
            "volume_variance_gain" => out.volume_variance_gain = value,
            "universe_stress_decay" => out.universe_stress_decay = value,
            "universe_stress_weight" => out.universe_stress_weight = value,
            "regime_stress_points" => out.regime_stress_points = value,
            "market_vol_slow_vix_damp" => out.market_vol_slow_vix_damp = value,
            "jump_intensity_idio" => out.jump_intensity_idio = value,
            "jump_intensity_market" => out.jump_intensity_market = value,
            "jump_mean_market" => out.jump_mean_market = value,
            "garch_beta_dispersion" => out.garch_beta_dispersion = value,
            "jump_momentum_share" => out.jump_momentum_share = value,
            "jump_sigma_idio" => out.jump_sigma_idio = value,
            "jump_sigma_market" => out.jump_sigma_market = value,
            "volume_innovation_sigma" => out.volume_innovation_sigma = value,
            "volume_persistence" => out.volume_persistence = value,
            "size_effect_exponent" => out.size_effect_exponent = value,
            "size_effect_smoothness" => out.size_effect_smoothness = value,
            "spread_size_exponent" => out.spread_size_exponent = value,
            "spread_size_smoothness" => out.spread_size_smoothness = value,
            "vix_mean_reversion" => out.vix_mean_reversion = value,
            "vix_cycle_amplitude" => out.vix_cycle_amplitude = value,
            "vix_realised_vol_weight" => out.vix_realised_vol_weight = value,
            "vix_return_clamp" => out.vix_return_clamp = value,
            "vix_return_gain" => out.vix_return_gain = value,
            "vix_return_gain_up" => out.vix_return_gain_up = value,
            "vix_return_source" => out.vix_return_source = value,
            "vix_target_shock_cap" => out.vix_target_shock_cap = value,
            "crisis_vix_threshold" => out.crisis_vix_threshold = value,
            "news_peer_weight" => out.news_peer_weight = value,
            "news_peer_weight_down" => out.news_peer_weight_down = value,
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
        "crisis_blend_source",
        "crisis_vix_threshold",
        "crowd_lean_cap",
        "crowd_momentum_gain",
        "crowd_valuation_gain",
        "fair_value_book_floor",
        "garch_alpha",
        "garch_beta",
        "garch_beta_dispersion",
        "garch_ceiling_multiple",
        "garch_floor_multiple",
        "garch_gamma",
        "garch_omega",
        "idio_sigma_scale",
        "inflation_ceiling",
        "inflation_floor",
        "inflation_reversion",
        "informed_flow_fraction",
        "jump_intensity_idio",
        "jump_intensity_market",
        "jump_mean_market",
        "jump_momentum_share",
        "jump_sigma_idio",
        "jump_sigma_market",
        "market_factor_sigma",
        "market_vol_alpha",
        "market_vol_beta",
        "market_vol_ceiling_multiple",
        "market_vol_floor_multiple",
        "market_vol_slow_gain",
        "market_vol_slow_persistence",
        "market_vol_slow_vix_damp",
        "market_vol_slow_weight",
        "market_vol_vix_anchor",
        "market_vol_vix_coupling",
        "mispricing_cap",
        "mispricing_half_life_days",
        "momentum_theta",
        "news_market_weight",
        "news_peer_weight",
        "news_peer_weight_down",
        "news_sector_weight",
        "order_flow_coefficient",
        "price_breaker_fraction",
        "price_hard_cap",
        "regime_stress_points",
        "sector_factor_sigma",
        "sector_vix_coupling",
        "size_effect_exponent",
        "size_effect_smoothness",
        "spread_size_exponent",
        "spread_size_smoothness",
        "universe_stress_decay",
        "universe_stress_weight",
        "vix_cycle_amplitude",
        "vix_mean_reversion",
        "vix_realised_vol_weight",
        "vix_return_clamp",
        "vix_return_gain",
        "vix_return_gain_up",
        "vix_return_source",
        "vix_target_shock_cap",
        "volume_innovation_sigma",
        "volume_persistence",
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
        "fiscal_multiplier" => econ::FISCAL_MULTIPLIER,
        _ => return None,
    })
}

/// Every carried read-only pair, for `to_pairs`.
fn carried_read_only_pairs() -> Vec<(String, f64)> {
    let mut out: Vec<(String, f64)> = [
        "daily_shock_cap",
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
        assert_eq!(PT_V4.fingerprint(), "pt-v4");
        assert_eq!(ModelParams::preset("pt-v4").unwrap().fingerprint(), "pt-v4");
        assert_eq!(PT_V5.fingerprint(), "pt-v5");
        assert_eq!(ModelParams::preset("pt-v5").unwrap().fingerprint(), "pt-v5");
        assert_eq!(PT_V6.fingerprint(), "pt-v6");
        assert_eq!(ModelParams::preset("pt-v6").unwrap().fingerprint(), "pt-v6");
        // The literal must be exactly half of what it claims to halve.
        assert_eq!(PT_V6.momentum_theta * 2.0, PT_V5.momentum_theta);
        assert_eq!(PT_V7.fingerprint(), "pt-v7");
        assert_eq!(ModelParams::preset("pt-v7").unwrap().fingerprint(), "pt-v7");
        assert_eq!(PT_V7.idio_sigma_scale, PT_V6.idio_sigma_scale * 0.9);
        // Re-armed on the next unreleased name. A preset that answers to a
        // name it does not have is how a vector nobody calibrated presents
        // as a shipped one.
        assert_eq!(PT_V8.fingerprint(), "pt-v8");
        assert_eq!(ModelParams::preset("pt-v8").unwrap().fingerprint(), "pt-v8");
        assert_eq!(PT_V9.fingerprint(), "pt-v9");
        assert_eq!(ModelParams::preset("pt-v9").unwrap().fingerprint(), "pt-v9");
        assert_eq!(PT_V10.fingerprint(), "pt-v10");
        assert!(ModelParams::preset("pt-v11").is_none());

        // Adding a preset must not disturb an existing one. The fingerprint
        // is taken over the PARAMETERS, not the table, so this holds by
        // construction -- asserted because the published manifests that cite
        // "pt-v3" depend on it and the cost of being wrong is orphaning
        // every one of them.
        assert_eq!(PT_V3.fingerprint(), "pt-v3");
        assert_ne!(PT_V3.digest(), PT_V4.digest());
    }

    #[test]
    fn the_default_preset_name_names_the_default_model() {
        // The bug this exists to prevent shipped once. `model_preset()`'s
        // default argument was the literal "pt-v1" and did not move when
        // the engine's default became pt-v3, so the library reported
        // pt-v1's name AND pt-v1's coefficients for runs that had executed
        // pt-v3 — and manifest.py folded those coefficients into the run
        // digest whose whole job is catching a coefficient substitution.
        //
        // Asserting the name resolves to the default model bit-for-bit
        // turns a future era's forgetfulness into a test failure rather
        // than a quietly mislabelled manifest.
        let named = ModelParams::preset(DEFAULT_PRESET_NAME)
            .expect("DEFAULT_PRESET_NAME must name a shipped preset");
        assert_eq!(named.digest(), crate::engine::Engine::default_model().digest());
        assert_eq!(named.fingerprint(), DEFAULT_PRESET_NAME);
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

        // DERIVED from the registries, not a hardcoded count.
        //
        // This assertion used to read `settable_names().len() + 2 + 18 + 12`.
        // Two names left `carried_read_only_pairs` and the 18 stayed, so the
        // test failed with "left: 84, right: 86" -- which says a count is
        // wrong and not WHICH NAME, and the arithmetic is equally happy if a
        // name is added and another dropped in the same change.
        //
        // Comparing the sets means the registries themselves are the
        // expectation: the test cannot go stale, and when it does fail it
        // names the parameter. That matters here more than most places --
        // three registries describe the settable surface and all of them
        // have to move together.
        let mut expected: Vec<String> =
            settable_names().iter().map(|n| n.to_string()).collect();
        expected.push("mispricing_phi".to_string());
        expected.push("s_phi_tick".to_string());
        expected.extend(carried_read_only_pairs().into_iter().map(|(n, _)| n));
        expected.sort();

        let mut got: Vec<String> = names.iter().map(|n| n.to_string()).collect();
        got.sort();

        let missing: Vec<&String> =
            expected.iter().filter(|n| !got.contains(n)).collect();
        let unexpected: Vec<&String> =
            got.iter().filter(|n| !expected.contains(n)).collect();
        assert!(
            missing.is_empty() && unexpected.is_empty(),
            "the preset dict and the registries disagree. \
             in a registry but absent from to_pairs: {missing:?}; \
             emitted by to_pairs but in no registry: {unexpected:?}"
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
