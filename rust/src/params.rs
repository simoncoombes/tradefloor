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
    /// How hard a name loads onto its OWN sector's factor. 0.5 on every
    /// preset before this dial and bit-identical (§108).
    ///
    /// It was the literal 0.5 for every member of every sector. A name's
    /// exposure to the MARKET varies by its beta; its exposure to its own
    /// industry did not vary at all, which is the last homogeneous loading
    /// in the factor structure.
    pub sector_loading: f64,
    /// How much a name's sector loading follows its market beta. Zero on
    /// every preset before this dial and bit-identical.
    ///
    /// At `s` the loading is `sector_loading * (1 + s * (beta - 1))`, so a
    /// high-beta name loads harder on its industry and a defensive one
    /// loads less. Tied to beta rather than to a fresh draw on purpose: it
    /// reuses a per-name attribute the universe already carries, so it needs
    /// no RNG stream and cannot move the draw schedule.
    ///
    /// This is cross-sectional DISPERSION in sector exposure, which the
    /// model has never had. `sector_excess_corr` is a mean over pairs, so a
    /// fixed loading makes every same-sector pair identically exposed; real
    /// industries contain pure plays and conglomerates.
    pub sector_loading_beta_slope: f64,
    /// How far the crisis blend's market injection is decoupled from the
    /// market factor's own magnitude. Zero on every preset before this dial
    /// and bit-identical.
    ///
    /// The injection is `source * gain * crisis_spike * market_factor`, and
    /// at a held VIX the spike is pinned at [`crisis_blend_cap`] (§63), so
    /// the ONLY thing that varies between seed blocks is `market_factor`'s
    /// magnitude — which is the market variance level, which is what GARCH
    /// persistence governs.
    ///
    /// That is the mechanical reason crisis co-movement's across-block RANGE
    /// tracks persistence at rho +0.85, and why every attempt to tighten the
    /// range has had to lower persistence and pay for it in the 504-day
    /// panel. Round 79 measured that trade across a 4x4 grid and found no
    /// cell escaping it: the range is bought with panel blocks.
    ///
    /// At `d` the injection is scaled by `|market_factor / baseline|^-d`, so
    /// at 1.0 its magnitude no longer depends on how large the market factor
    /// happens to be and the crisis correlation it produces stops inheriting
    /// the variance level. The baseline is [`market_factor_sigma`] at tick
    /// scale, the same normaliser `crash_amplifier` already uses, so
    /// "ordinary" means the same thing in both places.
    ///
    /// This is the mechanism round 79 said the next gain on this axis would
    /// need: one that decouples the co-movement spread from GARCH
    /// persistence rather than another search over the dials that exist.
    /// Whether it does so at a price worth paying is a measurement, and it
    /// ships inert until that measurement exists.
    pub crisis_blend_variance_damp: f64,
    /// Gain on the QE valuation channel. 1.0 on every preset before this
    /// dial and bit-identical.
    ///
    /// `qe_pe_boost` reaches the target P/E as `1 + qe_pe_boost`, and alone
    /// among the model's macro channels it has no gain between the input and
    /// the response — `garch_vix_coupling`, `jump_vix_coupling`,
    /// `sector_vix_coupling` and `market_vol_vix_coupling` all exist.
    ///
    /// Round 76 measured why that matters. Freezing each macro channel of
    /// the driven test in turn, the VIX channel alone produces a ratio of
    /// 1.136 against real AAPL and the full four produce 1.394; `qe_pe_boost`
    /// carries about 0.25 of that 0.39 excess, more than VIX's own 0.14, and
    /// the policy channel contributes nothing. The response is convex, so
    /// halving the amplitude removes 68% of the contribution: a gain near
    /// 0.5 would move the driven ratio from 1.394 to 1.222.
    ///
    /// **A caveat that belongs with the dial, not only in the record.** The
    /// `qe_pe_boost` series the driven test supplies is not measured
    /// quantitative easing. `gate_pick._covid_inputs` derives it as the S&P
    /// against its own 200-day EMA, clamped to +/-0.35. A gain calibrated
    /// against that proxy encodes the proxy's amplitude as if it were the
    /// model's physics. The dial is a real gap in the model — every other
    /// channel has one — but any value fitted to the current driven test
    /// carries that qualification with it.
    pub qe_pe_gain: f64,
    /// Gain on the QE STOCK channel: the target P/E takes
    /// `+ qe_pe_stock_gain * ln(qe_assets_ratio)`, concave in the level of
    /// holdings, zero at the neutral baseline. 0.0 -- every preset when the
    /// dial shipped -- is bit-inert. The flow channel above it is linear in
    /// monthly purchases and overshoots when fed the measured series; this
    /// is the formulation that can take real data. See `corpus/qe` in the
    /// design record for the measured Fed series and the -0.485 proxy
    /// anticorrelation that motivated it.
    pub qe_pe_stock_gain: f64,
    /// Scale on the per-name idiosyncratic GARCH sigma — the funding side
    /// of the factor-variance reallocation. Bit-inert at 1.0.
    pub idio_sigma_scale: f64,
    /// How strongly a name's idiosyncratic volatility follows its market
    /// beta, as an EXPONENT. Zero on every preset before this dial and
    /// bit-identical.
    ///
    /// At `k` the scale is `idio_sigma_scale * beta^k`, bounded by
    /// [`IDIO_BETA_BOUNDS`]. Like [`sector_loading_beta_slope`] it reuses a
    /// per-name attribute the universe already carries rather than drawing a
    /// fresh one, so it costs no RNG stream and cannot move the draw
    /// schedule.
    ///
    /// It exists because `idio_sigma_scale` is the last homogeneous term in
    /// a name's volatility. Cap size varies it through
    /// `cap_size_multiplier` and GARCH varies it through each name's own
    /// conditional variance, but the SCALE itself is one number for every
    /// name in the roster. Real rosters disperse considerably more: over ten
    /// non-overlapping 252-day windows of the forty-name reference panel the
    /// interquartile ratio of annualised name volatility runs 1.273 to
    /// 1.486, where pt-v12 averages 1.205 across thirty seeds. That is a
    /// dispersion gap, not a level gap, and no dial that moves every name
    /// together can close it.
    ///
    /// An exponent rather than the linear `1 + s(beta - 1)` this dial was
    /// first built as. The linear form has a wall: it drives any name with
    /// beta below `1 - 1/s` to exactly zero volatility, and on the certified
    /// roster that starts at `s` near 2 -- before the form reaches the
    /// bottom of the real range, which it never did. `beta^k` is positive
    /// for every positive beta, so it disperses without deleting names.
    pub idio_sigma_beta_exponent: f64,
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
    /// Daily probability that a company generates its OWN news, and the
    /// standard deviation of that news's price impact. Both zero on every
    /// preset before this dial, bit-identically (§101).
    ///
    /// The news machinery has always existed and has never fired. News is
    /// caller-supplied: `SessionRequest.news` is a slice the engine never
    /// filled, and the only populated path is `tradefloor.replay`, which feeds
    /// a recorded log's news back in. So `company_news` contributed exactly
    /// zero in every simulation the panel measures, 0 nonzero day-cells out
    /// of 30240 at every pinned VIX (§85), and `news_sector_weight`,
    /// `news_market_weight` and the two peer weights could not move any
    /// certified statistic.
    ///
    /// That left the jump process as this market's only idiosyncratic
    /// shock, which this file already calls the earnings-surprise channel.
    /// A jump lands on one name and reaches no other. Real earnings
    /// surprises transfer: one cloud company's miss moves its peers.
    /// Switching this on gives sector co-movement an ENDOGENOUS contagion
    /// route, where today it is entirely exogenous, a per-tick sector draw
    /// plus market beta and nothing that travels between members.
    pub endogenous_news_intensity: f64,
    /// Standard deviation of an endogenous news event's price impact, in the
    /// units `NewsEvent::price_impact` carries.
    pub endogenous_news_sigma: f64,
    /// Weight on SECTOR-WIDE news, an event tagged with a sector and
    /// no company. Distinct from peer transfer, which is one named
    /// company's news reaching another; this is news about the
    /// industry itself.
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
    /// How much harder news transfers to a PEER in a crisis. Zero on every
    /// preset before this dial and bit-identical (§104, §105).
    ///
    /// The peer weights are constants, so contagion ran as hard in a quiet
    /// July as in March 2020. Measured, that is what made endogenous news
    /// unusable: calm-market sector excess is already in band at +0.166
    /// against a 0.11-to-0.22 ceiling, and constant transfer pushed it to
    /// +0.256 at both horizons in exchange for the crisis figure that was
    /// wanted. A mechanism that cannot tell a crisis from a Tuesday cannot
    /// be aimed at one.
    ///
    /// At `c` the peer weight becomes `base * (1 + c * crisis_spike)`. The
    /// spike is ZERO below `crisis_vix_threshold`, so a calm market is
    /// untouched at ANY coupling and only the crisis moves. Real
    /// information contagion works this way: when one bank misses, the
    /// market re-reads every other bank, and it does that harder in a panic.
    pub news_peer_vix_coupling: f64,
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
    /// How hard a crisis loads every name onto the market factor, as a
    /// multiplier on the crisis spike. 0.5 is every preset before this dial
    /// and is bit-identical (§96, §97).
    ///
    /// The crisis blend adds `crisis_blend_source * gain * crisis_spike *
    /// market_factor` to a name's market component, and `gain` was the
    /// literal 0.5. The spike itself is capped at `crisis_blend_cap`, 0.98,
    /// so the extra market loading a crisis could ever produce was
    /// 0.5 x 0.98 = 0.49 of beta, fixed in the source and reachable by no
    /// parameter.
    ///
    /// That ceiling is why crisis co-movement could not be raised. Measured
    /// at thirty seeds, every route to a real-sized crisis lever adds
    /// variance that is NOT the market factor (jumps are per-name, the
    /// sector draw is per-sector), and crisis-state cross-sectional
    /// correlation is precisely the market factor's SHARE of total variance.
    /// So the lever and co-movement traded against each other, and the one
    /// channel that should have raised both was already pinned: the spike
    /// saturates its cap for any crisis, because `effective_stress` is the
    /// raw point excess over the 25.5 threshold and is about 19.5 at a held
    /// VIX 45.
    ///
    /// Raising this gain is the headroom. It multiplies the market factor
    /// and nothing else, so it buys co-movement in the one currency that
    /// does not dilute it.
    pub crisis_blend_gain: f64,
    /// Where the crisis correlation injection is taken FROM. At 0.0 it
    /// comes out of the sector slot, which consumes the sector draw
    /// exactly when sector structure matters most; at 1.0 it is added
    /// to the market component instead, leaving the sector draw
    /// whole. pt-v7 onward run 1.0.
    pub crisis_blend_source: f64,

    // ── Per-name GJR-GARCH (market/garch.rs) ────────────────────────────
    /// The GJR-GARCH constant: the variance a name reverts toward
    /// when neither yesterday's shock nor yesterday's variance pulls
    /// it. Small by construction, because the long-run level is set
    /// by the sector's base variance rather than by this term.
    pub garch_omega: f64,
    /// Weight on yesterday's squared shock: how sharply a name's
    /// variance reacts to its own last move. Higher alpha is a
    /// twitchier name; the persistence that carries the reaction
    /// forward is `garch_beta`.
    pub garch_alpha: f64,
    /// Weight on yesterday's variance: how long a name's volatility
    /// remembers. `alpha + beta + gamma/2` is the persistence, and it
    /// must stay under one or the variance process has no stationary
    /// level and a long run drifts without bound. The calibration
    /// searches (persistence, alpha share) rather than these two
    /// directly, because independent boxes around them put half
    /// their mass across that line (atlas_survey TRANSFORMED_AXES).
    pub garch_beta: f64,
    /// GJR leverage-effect asymmetry. Zero recovers symmetric GARCH(1,1)
    /// bit for bit.
    pub garch_gamma: f64,
    /// Ceiling as a multiple of the sector's long-run variance. A guard,
    /// but searched under bounds — measured as binding on clustering.
    pub garch_ceiling_multiple: f64,
    /// How much a NAME's own variance follows the VIX, on the market
    /// factor's own target shape.
    ///
    /// Shipped 0.0, bit-identical there by branch, and the last piece of the
    /// variance model that does not know what regime it is in. The per-name
    /// GJR-GARCH reads no macro state at all: its clamps are multiples of a
    /// static per-sector variance, and its own unconditional level sits far
    /// below the floor those clamps impose (5.6% annualised against a floor
    /// of 19.8% for technology), so a name's variance hovers near that floor
    /// whatever the market is doing. That is why `garch_ceiling_multiple`
    /// was measured not to bind at any value on this preset (§75): the
    /// variance never gets within twenty times of it.
    ///
    /// The consequence is the crisis lever. Total volatility is the market
    /// factor plus the name's own, the factor scales with the VIX squared
    /// and the name's does not, so a held VIX 65 raises one term and leaves
    /// the other where it was: the lever reads 4.75x against a real 6.16x
    /// with the factor's own clamp already past what a record VIX implies
    /// (§77). Real single-stock volatility rises with the market's in a
    /// crisis; here it cannot.
    ///
    /// At `c` the clamp reference becomes `base * (1 - c + c * (vix /
    /// market_vol_vix_anchor)^2)`, the same map the market factor's target
    /// uses, so at the anchor the reference is exactly the base at any
    /// coupling and the two variance processes read the regime the same way.
    pub garch_vix_coupling: f64,
    /// Floor as a multiple of the sector's long-run variance.
    pub garch_floor_multiple: f64,

    // ── Market-factor variance process (market/factor_vol.rs) ───────────
    /// The market factor's own GARCH reaction term: how sharply
    /// market-wide variance responds to the last market-wide shock.
    /// This is the process that gives every name a common volatility
    /// regime; a name's own GJR-GARCH is idiosyncratic on top of it.
    pub market_vol_alpha: f64,
    /// The market factor's variance persistence. `alpha + beta` is
    /// this process's persistence and is subject to the same
    /// stationarity limit as the per-name one, and to the same
    /// reparameterisation in the survey.
    pub market_vol_beta: f64,
    /// GJR leverage on the MARKET factor's variance update. Zero recovers
    /// the symmetric update bit for bit; every preset before this dial
    /// sets it.
    ///
    /// A down day loads `market_vol_alpha + market_vol_gamma` on the
    /// squared shock where an up day loads `market_vol_alpha` alone, and
    /// omega compensates by `gamma/2` so the unconditional level stays on
    /// target: the dial redistributes variance between down and up states
    /// rather than adding any. The per-name asymmetry (`garch_gamma`) has
    /// existed since pt-v2; the COMMON factor has run symmetric forever,
    /// which is why the model cannot produce correlation asymmetry --
    /// correlations that rise in falling markets are the common factor's
    /// leverage effect, and this model's corr_asymmetry sits at -0.016
    /// against a real +0.015 with nothing measured able to move it
    /// (rounds 93/95). This is the wire for exactly that statistic.
    ///
    /// Applies to the FAST component only: the leverage effect is a
    /// same-week phenomenon, and the slow component carries long-horizon
    /// clustering, not asymmetry.
    pub market_vol_gamma: f64,
    /// Cap on the market factor's variance, as a multiple of its calm
    /// level. A CAP, not a lever: it does nothing until the variance
    /// reaches it, so raising it above where it already binds changes
    /// almost nothing. Measured from 32 to 50 it moves the crisis
    /// lever by under 3% and crisis co-movement not at all (§93).
    /// The physical anchor is that a real record VIX of 82.7 against
    /// this model's anchor of 15 is a variance ratio of about 30.
    pub market_vol_ceiling_multiple: f64,
    /// Floor on the market factor's variance, as a multiple of its
    /// calm level. Stops a quiet stretch from compounding into a
    /// market with no shared movement at all.
    pub market_vol_floor_multiple: f64,
    /// 0 = autonomous target, 1 = target fully proportional to VIX².
    pub market_vol_vix_coupling: f64,
    /// VIX level at which a coupled target equals the baseline variance.
    pub market_vol_vix_anchor: f64,
    /// Days of EMA smoothing on the VIX the MARKET variance target reads.
    /// 0 -- every shipped preset -- reads each day's print raw, bit for
    /// bit. Round 99: with QE silenced, the entire remaining driven-window
    /// excess is the fear response transmitting print-to-print VIX churn
    /// into the variance target; real volatility follows sustained fear.
    /// Affects the market factor only; the per-name GARCH VIX coupling
    /// still reads the print.
    pub market_vol_vix_smooth: f64,
    /// Exponent on the market variance target's VIX ratio. 2.0 -- every
    /// shipped preset -- is the literal square, bit for bit. Round 100
    /// measured the square too convex through mid-VIX along real paths;
    /// a lower exponent with the coupling re-fit to hold T(45)/T(5)
    /// flattens the middle while preserving the certified crisis lever's
    /// backbone. Below one the target at very low VIX can go negative and
    /// the variance floor clamps it; the vix5 instrument must be checked,
    /// not assumed.
    pub market_vol_vix_exponent: f64,
    /// Downside transmission asymmetry: on a down tick of the market
    /// factor, every name receives `beta * factor * (1 + this)`. 0.0 --
    /// every shipped preset -- is bit-identical. The direct wire for
    /// correlation asymmetry: names co-moving harder on the way down IS
    /// the exceedance correlation real markets show and the panel's
    /// corr_asymmetry statistic measures. Raises down-day co-movement and
    /// some volatility asymmetry with it; the leverage_effect row is the
    /// stated side-channel to watch.
    pub market_beta_down_asym: f64,
    /// The LAGGED downside transmission: on the session after a down day,
    /// every name receives `beta * factor * (1 + this)` whatever the
    /// tick's own sign. 0.0 -- every shipped preset -- is bit-identical.
    /// Block 1201's deep-trim signature (the wire landing a day late on
    /// its structure) is the measured motivation: real down-moves
    /// continue, and the contemporaneous wire alone cannot express that.
    pub market_beta_down_asym_lag: f64,
    /// The compensation side of the downside wire: on UP ticks of the
    /// market factor, transmission is scaled by (1 - this). The bare
    /// [`ModelParams::market_beta_down_asym`] raises MEAN transmission
    /// with its dose, which is measured as its co-movement tax (round
    /// 102: "per unit of 1201 gain the dial pays roughly its own weight
    /// in 401 co-movement"). Paired, the two move correlation from
    /// unconditional to down-day-conditional -- which is the shape the
    /// 2026-09 re-derived rulers say real markets have (fresh
    /// cross-sectional ceilings BELOW the model, fresh corr_asymmetry
    /// floors ABOVE it). 0.0 -- every shipped preset -- takes the
    /// untouched branch, bit for bit.
    pub market_beta_up_comp: f64,
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
    /// At 0.0 this is off and the valuation is bit-identical to the reference implementation
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
    /// Weight of the SLOW variance component in the market factor's
    /// two-component mixture. The mixture exists because real
    /// volatility memory decays hyperbolically and a single
    /// exponential cannot imitate that past about a year; two
    /// exponentials fake it better and still come apart, which is
    /// gap 3.
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
    /// Per-NAME volume persistence and its innovation. Both zero on every
    /// preset before this dial and bit-identical (§107).
    ///
    /// `volume_persistence` carries a COMMON multiplier: every name shares
    /// it, so the whole market is busy or quiet together. That function's
    /// own docstring has said since it was written that "real volume
    /// persistence is partly idiosyncratic, and that half is not modelled".
    ///
    /// It is the half the last panel miss needs. `volume_change_acf1` at 504
    /// days reads about -0.316 against a band of -0.29 to -0.21 on every
    /// preset, and the model is too NEGATIVE, which is what independent
    /// per-tick noise does to the change in a series. Reaching the band
    /// through the common component needs a bigger innovation, and that
    /// takes `volume_abs_return_corr` out with it (§21 to §23, §73): a
    /// market-wide volume multiplier adds volume variance unrelated to any
    /// name's own moves. A PER-NAME state raises each name's own volume
    /// autocorrelation without touching the common component the
    /// volume-and-volatility row depends on, which is the trade those
    /// searches could not find a way around.
    pub volume_idio_persistence: f64,
    /// Volume that follows the NAME's own conditional variance. Zero on
    /// every preset before this dial and bit-identical (§112).
    ///
    /// `volume_variance_gain` couples volume to the MARKET factor's
    /// variance and nothing else, so a name whose OWN volatility is
    /// elevated trades no more than a quiet one in the same market.
    ///
    /// This exists because §111 refuted §107. Adding per-name volume
    /// PERSISTENCE fixes `volume_change_acf1` and takes
    /// `volume_abs_return_corr` down with it, exactly as the common
    /// component does, and the reason is not that the old state was common.
    /// `volume_abs_return_corr` measures how well volume tracks the size of
    /// a name's own move, so any volume variance UNRELATED to that name's
    /// returns dilutes it, and per-name noise is as unrelated as
    /// market-wide noise. This is the return-related version: at `g` the
    /// multiplier is `1 + g * (garch_variance / sector base - 1)`, clamped,
    /// so a name trades more precisely when its own volatility is high.
    pub volume_idio_variance_gain: f64,
    /// Innovation of the per-name volume state. See
    /// [`ModelParams::volume_idio_persistence`].
    pub volume_idio_sigma: f64,

    /// How many components a name's variance cascade carries. 0 is OFF and
    /// is what every SHIPPED preset uses, so the single-component
    /// GJR recursion runs bit for bit.
    ///
    /// See [`crate::market::garch::update_garch_cascade`] for why more than
    /// two matters: a superposition of exponentials with geometrically
    /// spaced timescales approximates a power law, and the count you need is
    /// about `log(range)/log(ratio)`. Six at ratio 3 covers lags 1 to 60.
    /// Capped at [`crate::market::garch::CASCADE_MAX`].
    pub garch_cascade_components: f64,
    /// Half-life spacing between cascade components. Component `k` has a
    /// half-life `ratio^k` times component 0's, and component 0 keeps the
    /// name's own `garch_beta`, so per-name persistence dispersion survives.
    ///
    /// Measured (§122): at ratio 3 with six components the latent decay slope
    /// reads -0.536 against a one-component -1.273 and a real -0.436.
    pub garch_cascade_ratio: f64,
    /// How much of the variance comes from the cascade rather than from the
    /// single-component process. 0.0 is the legacy process exactly; 1.0 is
    /// the cascade alone.
    ///
    /// A dial rather than a switch so a preset can take part of the
    /// cascade's shape without paying all of its cost, and so the two can be
    /// separated in a search: `components` sets the SHAPE, this sets how much
    /// of it reaches the price.
    pub garch_cascade_weight: f64,

    /// Base volume a name trades on a day it does not move at all.
    ///
    /// Volume per tick is `base * (floor + response * min(move, cap) +
    /// noise * u)`, where `move` is the day's move from the open in units
    /// of one percent and `u` is a uniform draw. These four numbers were
    /// literals `0.6`, `0.6`, `4.0` and `0.2` in the tick engine from the
    /// first version until 0.3.0, and they ship at exactly those values,
    /// so every preset before pt-v12 prints the volume it always did.
    ///
    /// They became dials because §113 measured what the cap costs. See
    /// [`ModelParams::volume_move_cap`], which is the interesting one.
    pub volume_move_floor: f64,
    /// How much more a name trades per one percent it has moved today.
    ///
    /// The contemporaneous channel `volume_abs_return_corr` measures: that
    /// statistic asks how well a name's volume tracks the size of its own
    /// move, and this is the only term in the volume expression that ties
    /// the two together on the SAME day. `volume_variance_gain` and
    /// [`ModelParams::volume_idio_variance_gain`] both couple volume to a
    /// conditional variance, which is a forecast made from yesterday's
    /// information, and §113 measured that a forecast tracks today's
    /// realised move far more loosely than today's move does.
    pub volume_move_response: f64,
    /// Where the volume response to a move SATURATES, in units of one
    /// percent.
    ///
    /// At the shipped 4.0 a name that falls twelve percent trades exactly
    /// as much as one that falls four. Real markets do not do that: volume
    /// on a limit-down day is a multiple of a bad-Tuesday day, and the
    /// relationship keeps rising well past four percent. The cap is the
    /// reason `volume_abs_return_corr` sits where it does and the reason it
    /// is so easily diluted, because every crisis day is pinned to one
    /// value and contributes no covariance at all.
    ///
    /// Raising it also raises volume in crises, which is a returns change
    /// and not only a volume one: volume feeds the book depth that prices
    /// settle through, so a volume dial is a price dial (§113).
    pub volume_move_cap: f64,
    /// Amplitude of the return-UNRELATED noise in a name's daily volume.
    ///
    /// Multiplies a uniform draw, so it widens volume without any relation
    /// to what the name did, which is exactly the dilution §111 identified.
    /// Lowering it should raise `volume_abs_return_corr` and cost realism
    /// in whatever a real market's unexplained volume variation represents,
    /// so it is a dial rather than a thing to minimise.
    pub volume_move_noise: f64,

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
    /// How much a jump's ARRIVAL RATE follows the VIX. Zero is every preset
    /// before this dial and is bit-identical (§84).
    ///
    /// Both intensities are per-day probabilities that ignore the regime,
    /// so the number of jump days in a dead-calm market and in a panic is
    /// the same. Decomposing the nine attribution components under a pinned
    /// VIX measured what that costs: jumps carry 40.5% of the variance of a
    /// market pinned at VIX 5 and 1.1% of one pinned at VIX 65, on 3003 and
    /// 2998 jump day-cells respectively. Real markets are the other way
    /// round, and jump clustering in crises is the documented fact this
    /// misses. It is also the floor under the calm end of the crisis lever:
    /// a market that cannot stop jumping cannot get quiet.
    ///
    /// At `c` both intensities are scaled by `1 - c + c * (vix /
    /// market_vol_vix_anchor)^2`, the same map `garch_vix_coupling` and the
    /// market factor's target use, so at the anchor the rate is exactly the
    /// shipped rate at any coupling and the mechanisms read the regime
    /// alike. `apply_jumps` draws two uniforms and two normals
    /// unconditionally whatever the rate is, so this moves a THRESHOLD and
    /// never a stream position.
    ///
    /// # It is not variance-neutral, and must be funded
    ///
    /// The scale averages ABOVE one over this model's own VIX distribution,
    /// so raising the coupling adds jump variance rather than only moving it
    /// between regimes. Measured on an undriven 504-day run (§88): VIX mean
    /// 20.24, `E[(vix / 15)^2]` = 2.035, so the mean scale is
    /// `1 - c + 2.035c` — 1.725 at `c` = 0.7, meaning jumps fire 72% more
    /// often on average.
    ///
    /// Left unfunded that shows up as total volatility. At `c` = 0.7 on the
    /// pt-v10 base it takes the 252-day panel to 14 of 14 and the 504-day
    /// panel from 13 of 14 to 11, losing `annualised_vol_pct` at 34.9
    /// against a ceiling of 34.0 and `sector_excess_corr` at 0.1058 against
    /// a floor of 0.11 (§87).
    ///
    /// Fund it by scaling `jump_intensity_market` and `jump_intensity_idio`
    /// by the reciprocal of the mean scale, which is derivable rather than
    /// searched: 0.580 at `c` = 0.7, 0.491 at `c` = 1.0. That is the same
    /// bookkeeping `idio_sigma_scale` does for the market factor's variance.
    pub jump_vix_coupling: f64,
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
    /// Fear decays slower than it arrives. Multiplies the mean reversion
    /// on days the target sits BELOW the current VIX (fear decaying);
    /// rising days keep the full rate. 1.0 -- every preset before the
    /// fear-gap era -- is the symmetric shipped arithmetic, bit for bit.
    /// Real markets: up-moves average 1.20x the size of down-moves
    /// (fear-gap-targets.json, 2004-2025).
    pub vix_decay_ratio: f64,
    /// Exogenous fear events, per YEAR. Real VIX spikes often arrive from
    /// news rather than accumulated market moves, and the target's small
    /// Gaussian noise cannot make that tail: P(VIX>30) reads ~0.007
    /// endogenous against a real 0.082 (round 133). At intensity != 0 a
    /// rare jump lands directly on the VIX LEVEL (a target jump would be
    /// eaten by the mean reversion, which is the measured death of the
    /// return wire) and decays through the slow side of
    /// [`ModelParams::vix_decay_ratio`] -- up fast, down slow, like fear.
    /// 0.0 -- every preset before the fear era -- takes NO random draws,
    /// so the schedule and every recorded run reproduce bit for bit.
    pub vix_jump_intensity: f64,
    /// Mean size of a fear event, in VIX points (exponential draw).
    pub vix_jump_scale: f64,
    /// Flow composition (the design record's FLOW-COMPOSITION campaign):
    /// a stress-activated COMMON flow lean in the price path -- forced,
    /// correlated selling above a fear threshold, which is the real-market
    /// mechanism hypothesized to PIN a crash's cohesion (real 2020 crash
    /// co-movement 0.781 tightly, this model 0.22-0.73 across seeds).
    /// Log-shock per VIX point above the threshold, per day, applied
    /// identically to every name alongside the crowd lean. The term varies
    /// only as the VIX varies, so a REPLAYED crash (VIX moving 30->80)
    /// receives strong common motion while the held-VIX crisis instrument
    /// (constant 45) receives a constant drift and near-zero added
    /// correlation. 0.0 -- a branch, not arithmetic -- is bit-inert.
    pub forced_flow_gain: f64,
    /// Where forced flow wakes, in VIX points. Below it the segment does
    /// not exist, which is what makes composition invisible in calm
    /// markets by construction.
    pub forced_flow_threshold: f64,
    /// How unevenly forced flow lands, as beta^k. Screen one measured the
    /// UNIFORM lean pinning crash cohesion (0.52 -> 0.69, IQR halved) at
    /// the cost of crash dispersion (0.48 -> 0.34 of real) -- identical
    /// pressure crowds out cross-sectional spread. Real forced selling is
    /// heterogeneous: leveraged and high-beta names get sold hardest. At
    /// k the per-name lean is the common term times beta^k; 0.0 is the
    /// uniform screen-one behaviour bit for bit (beta^0 multiplies by
    /// 1.0 through a branch, not a pow call).
    pub forced_flow_beta_exponent: f64,
    /// Forced sellers are FINITE (round 143: sixty held days of constant
    /// sell drift ground prices into their clamps and the crisis lever
    /// broke DOWNWARD -- only a sustained-stress instrument could catch
    /// infinite sellers). The reservoir is the segment's total budget in
    /// VIX-point-days: each day above the threshold spends its excess,
    /// and the effective lean scales by the fraction remaining. 0.0 is
    /// the infinite-sellers screen behaviour bit for bit.
    pub forced_flow_reservoir: f64,
    /// Fraction of spent budget recovered per below-threshold day
    /// (deleveraging capacity rebuilds in calm). 0.0: never.
    pub forced_flow_replenish: f64,
    /// Crash-gated fear events (the pt-v17 era's co-jump family). The
    /// Poisson fear jump above was measured dead at every dose (round
    /// 135): a jump day with no return behind it dilutes the same-day
    /// coupling and decouples the VIX from realized vol. This family
    /// inverts that by construction -- a fear event can fire ONLY
    /// because the market fell. The day's index return, standardized by
    /// the prior VIX's own implied daily sigma, gates the intensity:
    /// nothing fires on mild or up days; the SVCJ shared-arrival
    /// structure (Duffie-Pan-Singleton 2000) at daily resolution.
    /// This is the master dial (per-unit-of-gate intensity). At 0.0 the
    /// branch takes NO draws and every recorded run reproduces bit for
    /// bit; when on, exactly two uniforms per day, fire or not.
    pub vix_selfex_gain: f64,
    /// The gate's threshold, in sigmas of down-move (the day's index
    /// return over the prior VIX's implied daily sigma). Below it the
    /// fire probability is exactly zero.
    pub vix_selfex_threshold: f64,
    /// Minimum size of a fear event, VIX points. Small pops belong to
    /// the diffusive channel; events start here.
    pub vix_selfex_min: f64,
    /// Mean event size above the minimum, VIX points (exponential).
    pub vix_selfex_scale: f64,
    /// Per-day retention of the fear component -- fear rides on the VIX
    /// as its own additive state with its own clock, so the mean
    /// reversion cannot eat it (the measured death of the return wire:
    /// at reversion 0.06 the level takes 6% of a one-day target move).
    /// 0.94 is a half-life near eleven trading days.
    pub vix_selfex_decay: f64,
    /// Retention on strong rally days (day return above +1 sigma):
    /// fear resolves faster when the market answers. The counterweight
    /// that keeps spike asymmetry near the real 1.20 rather than above
    /// it (Amengual-Xiu: implied vol also jumps DOWN, on resolution).
    pub vix_selfex_relax: f64,
    /// Hawkes self-excitation: each fired event kicks the intensity by
    /// this much, so aftershocks cluster (Ait-Sahalia et al 2015;
    /// Fulop-Li-Yu 2015 find exactly this clustering since 1987).
    pub vix_selfex_excite: f64,
    /// Per-day retention of the excitation memory. The branching ratio
    /// excite/(1-this) stays below one: clustering, not criticality.
    pub vix_selfex_excite_decay: f64,
    /// Extra intensity in Contraction and Trough phases, as a fraction
    /// (0.0: no phase gating). Real crisis frequency is phase-skewed:
    /// the sub-period spread of P(VIX>30) runs 0.004 to 0.263.
    pub vix_selfex_phase: f64,
    /// Couples an event's SIZE to the down-move that fired it: the
    /// magnitude is scaled by (1 + this x the gate excess). Round 153
    /// measured the missing piece exactly — the gate protects the
    /// TIMING (jump days are crash days) but an independent magnitude
    /// still thins the dVIX-on-return regression (same-day corr -0.86
    /// -> -0.64..-0.71 across the first ladder). Real co-jump sizes
    /// are strongly anti-correlated (Todorov-Tauchen 2011: SPX -6%,
    /// VIX +16). 0.0 multiplies by exactly one, bit for bit.
    pub vix_selfex_size_coupling: f64,
    /// The HAR realized-vol anchor for the VIX target (the pt-v17
    /// era's persistent-target channel). A one-day target spike
    /// transmits ~6% at reversion 0.06 -- the measured death of the
    /// return wire -- but a target move that PERSISTS transmits 74%
    /// over a month. HAR components (Corsi 2009) are persistent by
    /// construction, and VIX = expected realized vol + premium is the
    /// Bekaert-Hoerova decomposition. Weight of the anchor in the
    /// target after the realised-vol branch; 0.0 -- every shipped
    /// preset -- skips the branch AND freezes the anchor's state, so
    /// nothing dormant moves.
    pub vix_har_weight: f64,
    /// The HAR anchor's weekly-component weight (weight on the 5-day
    /// EMA of squared daily index returns). The daily component gets
    /// one minus this minus the monthly weight.
    pub vix_har_mid: f64,
    /// The HAR anchor's monthly-component weight (22-day EMA). The
    /// persistence carrier -- and therefore the AR(1) risk, watched
    /// against the real 0.976 ceiling.
    pub vix_har_slow: f64,
    /// Variance risk premium multiplier on the HAR anchor: the VIX
    /// trades above expected realized vol (Bollerslev-Tauchen-Zhou:
    /// ~4-6 points on average, countercyclical). 1.25 is the neutral
    /// anchor of the searched range.
    pub vix_har_vrp: f64,
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
    /// The VIX above which the dollar catches a safe-haven bid.
    ///
    /// Defaults to the same constant as `crisis_vix_threshold` and is a
    /// SEPARATE dial. 0.4.2 pointed the dollar gate at `crisis_vix_threshold`
    /// to close issue #50, which moved it from 25.5 to 30.88 for `pt-v13` and
    /// `pt-v14`, the two presets that override that parameter, and changed
    /// their trajectories in a patch release. A preset that wants the two
    /// gates to move together sets them together.
    pub usd_crisis_vix_threshold: f64,
    /// Re-assert the credit spread floors on every daily step, scaled.
    ///
    /// INERT at 0.0, which every shipped preset sets. `update_economy_daily`
    /// moves the 10y treasury daily and never writes the credit yields, so
    /// between periodic meetings the corporate spread drifts below its 0.8
    /// floor -- measured to 0.4216, first breaching on day 121, which is an
    /// investment-grade yield under the risk-free curve.
    ///
    /// A dial rather than a straight fix because that function is
    /// preset-independent: flooring unconditionally would move the economy
    /// trajectory of every preset including `pt-v1`, and the version policy
    /// requires a trajectory change to arrive as a new preset. 1.0 enforces
    /// both floors in full.
    pub daily_credit_floor_gain: f64,

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
/// report is `tradefloor-design/CALIBRATION-PTV2.md`. Built as `pt_v1()` with
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
/// `pt-v3`, the shipped default from 2026-08-22 until pt-v10 took it at
/// 0.2.0. Emitted beside the preset body by
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

/// pt-v10 with a crisis that behaves like one -- see
/// [`ModelParams::pt_v11`].
pub const PT_V11: ModelParams = ModelParams::pt_v11();
/// [`ModelParams::pt_v12`].
pub const PT_V12: ModelParams = ModelParams::pt_v12();
pub const PT_V13: ModelParams = ModelParams::pt_v13();
pub const PT_V14: ModelParams = ModelParams::pt_v14();
/// pt-v14 with the slow variance component switched on and the credit
/// floor enforced -- see [`ModelParams::pt_v15`].
pub const PT_V15: ModelParams = ModelParams::pt_v15();
/// pt-v15 with the QE valuation channel silenced -- see
/// [`ModelParams::pt_v16`].
pub const PT_V16: ModelParams = ModelParams::pt_v16();

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
pub const DEFAULT_PRESET_NAME: &str = "pt-v16";

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
            sector_loading: 0.5,
            sector_loading_beta_slope: 0.0,
            crisis_blend_variance_damp: 0.0,
            qe_pe_gain: 1.0,
            qe_pe_stock_gain: 0.0,
            idio_sigma_scale: factor_vol::IDIO_SIGMA_SCALE,
            idio_sigma_beta_exponent: 0.0,
            order_flow_coefficient: factors::ORDER_FLOW_COEFFICIENT,
            informed_flow_fraction: factors::INFORMED_FLOW_FRACTION,
            inflation_reversion: crate::economy::INFLATION_MEAN_REVERSION,
            inflation_ceiling: crate::economy::INFLATION_CEILING,
            inflation_floor: crate::economy::INFLATION_FLOOR,
            endogenous_news_intensity: 0.0,
            endogenous_news_sigma: 0.0,
            news_sector_weight: factors::NEWS_SECTOR_WEIGHT,
            news_market_weight: factors::NEWS_MARKET_WEIGHT,
            crash_amplifier_threshold: factors::CRASH_AMPLIFIER_THRESHOLD,
            crash_amplifier_slope: factors::CRASH_AMPLIFIER_SLOPE,
            crisis_blend_ramp: tick::CRISIS_BLEND_RAMP,
            crisis_blend_cap: tick::CRISIS_BLEND_CAP,
            crisis_blend_gain: 0.5,
            crisis_blend_source: 0.0,
            sector_vix_coupling: 0.0,
            garch_omega: garch::OMEGA,
            garch_alpha: garch::ALPHA,
            garch_beta: garch::BETA,
            garch_gamma: garch::GAMMA,
            garch_ceiling_multiple: garch::CEILING_MULTIPLE,
            garch_vix_coupling: 0.0,
            garch_floor_multiple: garch::FLOOR_MULTIPLE,
            market_vol_alpha: factor_vol::MARKET_VOL_ALPHA,
            market_vol_beta: factor_vol::MARKET_VOL_BETA,
            market_vol_gamma: 0.0,
            market_vol_ceiling_multiple: factor_vol::MARKET_VOL_CEILING_MULTIPLE,
            market_vol_floor_multiple: factor_vol::MARKET_VOL_FLOOR_MULTIPLE,
            market_vol_vix_coupling: factor_vol::MARKET_VOL_VIX_COUPLING,
            market_vol_vix_anchor: factor_vol::MARKET_VOL_VIX_ANCHOR,
            market_vol_vix_smooth: 0.0,
            market_vol_vix_exponent: 2.0,
            market_beta_down_asym: 0.0,
            market_beta_down_asym_lag: 0.0,
            market_beta_up_comp: 0.0,
            // Legacy values: the slow component is OFF, and the update
            // reduces to the single-component form bit for bit.
            market_vol_slow_persistence: 0.0,
            market_vol_slow_gain: 0.0,
            fair_value_book_floor: 0.0,
            market_vol_slow_weight: 0.0,
            volume_idio_variance_gain: 0.0,
            volume_idio_persistence: 0.0,
            volume_idio_sigma: 0.0,
            garch_cascade_components: 0.0,
            garch_cascade_ratio: 3.0,
            garch_cascade_weight: 1.0,
            volume_move_floor: 0.6,
            volume_move_response: 0.6,
            volume_move_cap: 4.0,
            volume_move_noise: 0.2,
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
            jump_vix_coupling: 0.0,
            garch_beta_dispersion: 0.0,
            jump_momentum_share: 1.0,
            volume_persistence: 0.0,
            volume_innovation_sigma: 0.0,
            size_effect_smoothness: 0.0,
            size_effect_exponent: 0.15,
            spread_size_smoothness: 0.0,
            spread_size_exponent: crate::microstructure::SPREAD_SIZE_EXPONENT,
            vix_mean_reversion: crate::economy::VIX_MEAN_REVERSION,
            vix_decay_ratio: 1.0,
            vix_jump_intensity: 0.0,
            vix_jump_scale: 0.0,
            forced_flow_gain: 0.0,
            forced_flow_threshold: 40.0,
            forced_flow_beta_exponent: 0.0,
            forced_flow_reservoir: 0.0,
            forced_flow_replenish: 0.0,
            // The co-jump family ships OFF with its shape dials at the
            // neutral anchors of their searched ranges, the
            // forced_flow_threshold convention: the master at zero is
            // what inertness means; the shapes carry sensible values so
            // turning one dial on means something.
            vix_selfex_gain: 0.0,
            vix_selfex_threshold: 1.75,
            vix_selfex_min: 3.0,
            vix_selfex_scale: 6.0,
            vix_selfex_decay: 0.94,
            vix_selfex_relax: 0.85,
            vix_selfex_excite: 0.35,
            vix_selfex_excite_decay: 0.87,
            vix_selfex_phase: 0.0,
            vix_selfex_size_coupling: 0.0,
            vix_har_weight: 0.0,
            vix_har_mid: 0.4,
            vix_har_slow: 0.25,
            vix_har_vrp: 1.25,
            vix_realised_vol_weight: 0.0,
            vix_cycle_amplitude: 1.0,
            vix_return_source: 0.0,
            vix_return_gain: crate::economy::VIX_RETURN_GAIN,
            vix_return_gain_up: crate::economy::VIX_RETURN_GAIN_UP,
            vix_return_clamp: crate::economy::VIX_RETURN_CLAMP,
            vix_target_shock_cap: crate::economy::VIX_TARGET_SHOCK_CAP,
            crisis_vix_threshold: crate::economy::CRISIS_VIX_THRESHOLD,
            usd_crisis_vix_threshold: crate::economy::CRISIS_VIX_THRESHOLD,
            daily_credit_floor_gain: 0.0,
            news_peer_weight: 0.0,
            news_peer_weight_down: 0.0,
            news_peer_vix_coupling: 0.0,
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

    /// `pt-v3`, the converged margined optimum, and the shipped default
    /// from 2026-08-22 until pt-v10 took it at 0.2.0.
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
    /// certifies pt-v10 at 252 days since the 2026-08-26 era boundary, and
    /// that claim is not weakened by this preset existing beside it.
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
    /// NOT the default. The envelope certifies whatever
    /// `DEFAULT_PRESET_NAME` names at 252 days, and certification is a
    /// separate act from passing the controls.
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
    /// NOT the default. [`PT_V16`] holds that since 0.6.0, and the
    /// envelope certifies whatever `DEFAULT_PRESET_NAME` names. Passing the
    /// controls is a separate act from certification.
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
    /// NOT the default. [`PT_V16`] holds that since 0.6.0, and the
    /// envelope certifies whatever `DEFAULT_PRESET_NAME` names.
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
    /// NOT the default. [`PT_V16`] holds that since 0.6.0, and the
    /// envelope certifies whatever `DEFAULT_PRESET_NAME` names.
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
    /// NOT the default. [`PT_V16`] holds that since 0.6.0, and the
    /// envelope certifies whatever `DEFAULT_PRESET_NAME` names.
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
    /// Five coefficients move from pt-v9 (CALIBRATION-FOLLOWUPS.md §73, §76
    /// to §78). `garch_vix_coupling` 0.0 to 0.3 lets a NAME's own variance
    /// follow the VIX, which no earlier preset did: the per-name GJR-GARCH
    /// read no macro state at all, so the market factor's variance tracked
    /// the regime and every name's own did not. Total volatility is the sum
    /// of the two, so a held crisis raised one term and left the other,
    /// which is what compressed the crisis lever. At 0.3 the lever reads
    /// 5.05x against 4.75x and a real 6.16x, the shock ratio 1.094 against
    /// 1.078, and crisis-state cross-sectional correlation moves from 0.740,
    /// just above the real 0.664 to 0.727, to 0.669 inside it. It costs
    /// crisis-state sector excess, +0.040 against +0.046 with real at
    /// +0.103. `market_vol_ceiling_multiple` 16 to 32 is the physical number
    /// rather than a fitted one: the clamp caps the market factor's variance
    /// at N times its calm level, and a real VIX of 82.7 against this model's
    /// anchor of 15 is a variance ratio of 30. At 16 the market could not
    /// reach the variance a real record VIX implies. It binds only in a
    /// crisis, so calm volatility at a held VIX of 5, 15 and 25 is unchanged
    /// to a tenth of a point, and the crisis lever rises from 4.30x to 4.75x
    /// against a real 6.16x. `vix_cycle_amplitude` 0.6 to 0.0 takes the business cycle out of
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
    /// NOT the default. [`PT_V16`] holds it. This line has been wrong in
    /// both directions before -- it read "NOT the default" through 0.2.0
    /// while pt-v10 held it, "THE DEFAULT" after pt-v12 took it away, and
    /// pt-v12's own block still claimed it two eras later.
    /// The envelope certifies whatever `DEFAULT_PRESET_NAME` names, which
    /// is the only place worth reading it from.
    pub const fn pt_v10() -> ModelParams {
        let mut p = ModelParams::pt_v9();
        p.vix_cycle_amplitude = 0.0;
        p.market_vol_ceiling_multiple = 32.0;
        p.garch_vix_coupling = 0.3;
        p.volume_innovation_sigma = 0.21;
        p.volume_persistence = 0.7;
        p
    }

    /// pt-v10 with a crisis that behaves like one: the first preset to hold
    /// the crisis lever, crisis co-movement and crisis sector structure at
    /// the same time.
    ///
    /// Three coefficients move from pt-v10 (CALIBRATION-FOLLOWUPS.md §92 to
    /// §100). `crisis_blend_gain` 0.5 to 0.8 loads names onto the market
    /// factor harder in a crisis; `sector_vix_coupling` 0.25 to 1.0 lets the
    /// sector draw's variance follow the regime; `idio_sigma_scale` 0.6526
    /// to 0.58 funds the extra variance the first two add, which is the same
    /// bookkeeping the market factor's variance has always used.
    ///
    /// # Measured, thirty seeds
    ///
    /// | | pt-v11 | pt-v10 | real |
    /// |---|---|---|---|
    /// | panel at 252 | 14/14 | 14/14 | |
    /// | panel at 504 | 13/14 | 13/14 | |
    /// | crisis lever | **6.01** | 4.97 | 6.16 |
    /// | crisis co-movement | **0.697** | 0.669 | 0.664 to 0.727 |
    /// | crisis sector excess | **+0.110** | +0.040 | +0.103 |
    ///
    /// It regresses NOTHING against pt-v10: both panels equal, and all three
    /// crisis numbers better. §8 passes on every axis with no flips, with
    /// pt-v10 run as the control beside it. The single 504-day miss is the
    /// volume row pt-v10 misses too.
    ///
    /// # Why this route and not jumps
    ///
    /// An earlier pt-v11 bought the lever with `jump_vix_coupling` and
    /// reached 6.22, and it cost crisis co-movement: 0.604 against a real
    /// floor of 0.664. Jumps are per-name, and crisis co-movement IS the
    /// market factor's share of total variance, so buying a violent crisis
    /// with idiosyncratic variance necessarily dilutes it. Four gate batches
    /// confirmed the trade on every dial that was not the market factor.
    ///
    /// The market factor's own crisis channel had been unusable because its
    /// gain was the literal 0.5 and its spike saturates a 0.98 cap in any
    /// crisis, so the most a crisis could load a name onto the market was
    /// 0.49 of beta (§97). Making that a dial removed the ceiling, and the
    /// lever bought through it raises co-movement instead of lowering it.
    /// `jump_vix_coupling` remains a shipped dial and §84 stands on its own
    /// terms, but it is not how this preset reaches a real crisis.
    ///
    /// # What it costs
    ///
    /// The driven-window axis (§81, §100): replayed through the real 2020-21
    /// macro path, daily return sd is 1.55x real AAPL's against pt-v10's
    /// 1.47x. The model already carried excess noise around a correct
    /// scenario gain and this adds about five percent more of it. Sector
    /// excess also lands at +0.091 against a real +0.103, closer than any
    /// preset before it and still short.
    ///
    /// NOT the default. [`PT_V16`] holds that. [`PT_V12`] is this preset plus one
    /// number: `volume_move_cap`. Selecting pt-v11 by name gives the crisis
    /// work without the volume-cap fix, which is the comparison §114 is
    /// written against.
    pub const fn pt_v11() -> ModelParams {
        let mut p = ModelParams::pt_v10();
        // The crisis, through the market factor (§97 to §99).
        p.crisis_blend_gain = 0.8;
        p.sector_vix_coupling = 1.0;
        // Company news that transfers to sector peers, harder in a crisis
        // (§101 to §106). The peer weight is small and the coupling large,
        // so a calm market barely sees transfer and a crisis does.
        p.endogenous_news_intensity = 0.05;
        p.endogenous_news_sigma = 0.03;
        p.news_peer_weight = 0.05;
        p.news_peer_weight_down = 0.05;
        p.news_peer_vix_coupling = 8.0;
        // News is the earnings-surprise channel the idio jump stood in for,
        // so the jump gives way rather than stacking; the rest is funded
        // from the idio scale, as the market factor's variance always has
        // been. Cutting the jump further than this costs the tails: at 0.6
        // of pt-v10's rate, 504-day kurtosis fell to 7.04 against a floor
        // of 7.1 (§106).
        p.jump_intensity_idio = 0.018175898318813576;
        p.idio_sigma_scale = 0.53;
        p
    }

    /// pt-v11 plus one number that was never chosen: where volume stops
    /// responding to the size of a move.
    ///
    /// The volume a name trades was `0.6 + 0.6 * min(move, 4.0) + 0.2 * u`
    /// from the first version of this model, and the `4.0` saturates the
    /// response at a four percent day. Every crisis session traded exactly
    /// as much as a bad Tuesday. Raising it to twelve percent, which is
    /// roughly where a real exchange starts halting, is the whole of this
    /// preset.
    ///
    /// It is the largest single measured gain in the project's record and
    /// it cost nothing (§114, thirty seeds):
    ///
    /// | | pt-v11 | pt-v12 | band |
    /// |---|---|---|---|
    /// | in band @252 | 14/14 | 14/14 | |
    /// | in band @504 | 13/14 | **14/14** | |
    /// | held-out universe | 13/14 | **14/14** | |
    /// | `volume_change_acf1` @504 | -0.3156 | -0.2656 | -0.29..-0.21 |
    /// | `volume_abs_return_corr` @252 | 0.4822 | 0.5599 | 0.46..0.66 |
    /// | `annualised_vol_pct` @252 | 32.81 | 32.76 | 15..36 |
    /// | `excess_kurtosis` @252 | 6.66 | 6.70 | 1.6..41 |
    /// | crisis lever | 6.01 | 6.04 | real 6.16 |
    /// | crisis co-movement @VIX45 | 0.697 | 0.696 | 0.664..0.727 |
    /// | crisis sector excess @VIX45 | +0.110 | +0.109 | real +0.103 |
    ///
    /// **14 of 14 at two years is the first time this project has measured
    /// it.** pt-v3 held 7, pt-v10 and pt-v11 hold 13. The row that closed,
    /// `volume_change_acf1`, is the one the `volume-change` gap was written
    /// about, and the one §21 through §23 called structurally unreachable.
    ///
    /// A region rather than a point: caps of 8, 12 and 20 all read 14/14 at
    /// both horizons with a held-out universe at 14/14, and the statistics
    /// differ in the third decimal. Twelve is the middle of that plateau.
    ///
    /// One axis moves the wrong way, stated because it is a regression: the
    /// driven-window noise ratio reads 1.565 against pt-v11's 1.555, both
    /// against a real 1.00. That axis was already the worst thing about this
    /// model and this makes it 0.6% worse.
    ///
    /// The default from the second 2026-08-26 era boundary until pt-v14
    /// took it on 2026-08-28. Selectable and bit-reproducing, so anything
    /// recorded under it replays exactly by naming it.
    ///
    /// What is measured rather than asserted: over thirteen
    /// thirty-seed blocks this preset holds all fourteen statistics at 504
    /// days on three of them, because its mean annualised volatility is
    /// 34.157 against a ceiling of 34.0 and its mean excess kurtosis 7.267
    /// against a floor of 7.1. It sits on two band rims. Selectable and
    /// bit-reproducing forever, so anything recorded under it replays by
    /// naming it.
    pub const fn pt_v12() -> ModelParams {
        let mut p = ModelParams::pt_v11();
        p.volume_move_cap = 12.0;
        p
    }

    /// REGISTERED AND SELECTABLE, NOT THE DEFAULT. [`PT_V16`] holds that.
    ///
    /// Registered because it is measured and because a preset nobody can
    /// select is a preset nobody can check. It is not the default because
    /// it does not clear the era-boundary bar: it regresses crisis
    /// co-movement, which sits outside its real range on five seed blocks
    /// of thirteen against pt-v12's four. Its sibling at
    /// `endogenous_news_sigma` 0.0258 regresses the crisis lever instead.
    /// Neither is free, and the successor that replaces pt-v12 should be.
    ///
    /// Fifteen coefficients, found by
    /// SURVEYING the settable surface rather than by moving one dial at a
    /// time, then reduced from thirty-three to fifteen by removing every
    /// coefficient that carried no information.
    ///
    /// What it fixes is a defect in [`PT_V12`] that nobody had measured
    /// because nobody had measured the mean over enough seeds. Over
    /// thirteen thirty-seed blocks -- 390 seeds -- `pt-v12` holds all
    /// fourteen statistics at 504 days on THREE of them. Its mean 504-day
    /// annualised volatility is 34.157 against a band ceiling of 34.0, and
    /// its mean excess kurtosis is 7.267 against a floor of 7.1: the preset
    /// sits on two band rims and passes only when seed noise pulls it back.
    /// This preset sits 77% and 31% into those bands and holds them on all
    /// thirteen blocks.
    ///
    /// | | pt-v12 | pt-v13 |
    /// |---|---|---|
    /// | 504-day panel at 14 of 14 | 3 of 13 blocks | 9 of 13 |
    /// | 252-day panel at 14 of 14 | 12 of 13 | 13 of 13 |
    /// | concentrated rosters, 504 | 62 of 69 | 68 of 69 |
    /// | crisis lever, mean | 5.96 | 6.11 (real 6.16) |
    /// | driven-window ratio | 1.651 | 1.453 |
    ///
    /// The four groups, and why each is here:
    ///
    /// **Persistence and funded jump coupling.** Market volatility
    /// persistence rises, which improves every VIX bucket and costs the
    /// crisis lever; a funded `jump_vix_coupling` buys the lever back. The
    /// pair was invisible to a one-dial search because each half fails
    /// alone. `jump_vix_coupling` starts here: it has shipped inert at 0.0
    /// in every preset, and §84 designed it to let idiosyncratic news flow
    /// cluster with the regime.
    ///
    /// **The crisis threshold group.** `crisis_vix_threshold` 25.5 to 30.9
    /// moves the VIX 25-30 bucket of the driven window from 1.62 to 1.34,
    /// which is the band crisis blending starts in -- the mechanism acting
    /// exactly where it should. `garch_vix_coupling` falls to near zero to
    /// pay for it, and that dial is the crisis co-movement lever (§107).
    ///
    /// **The VIX anchor.** The market variance target scales with
    /// `(VIX/anchor)^2`, so moving the anchor rescales the whole
    /// volatility-versus-VIX curve rather than a point on it. §17 split the
    /// driven-window defect into a flat gain error and a VIX 30-45 spike;
    /// this addresses the flat half and nothing else found does.
    ///
    /// **`endogenous_news_sigma`.** Pays the anchor's lever cost and lowers
    /// the driven ratio at the same time -- one of two dials found in
    /// forty-six rounds that improve two things at once.
    ///
    /// Measured beside `pt-v12` throughout: thirty-seed gate on every axis,
    /// §8 with `pt-v12` as its control, thirteen seed blocks, the
    /// concentrated rosters, and the bucketed driven window. It regresses
    /// nothing.
    pub const fn pt_v13() -> ModelParams {
        let mut p = ModelParams::pt_v12();
        // Persistence, and the coupling that pays for it.
        p.market_vol_alpha = 0.300730582;
        p.market_vol_beta = 0.66999041;
        p.jump_vix_coupling = 0.2626;
        p.jump_intensity_idio = 0.0068895346;
        p.jump_sigma_idio = 0.08369236;
        p.jump_intensity_market = 0.0565753337;
        p.idio_sigma_scale = 0.5688;
        // The crisis threshold group. garch_vix_coupling near zero is what
        // holds crisis co-movement up while the rest spends it.
        p.crisis_vix_threshold = 30.88325108;
        p.crisis_blend_gain = 0.8275881;
        p.garch_vix_coupling = 0.0269;
        p.sector_factor_sigma = 0.01006215;
        p.sector_loading = 0.57351027;
        // NOT mispricing_half_life_days. The search chose 68.25733542, but
        // this is a `const fn` and the half-life is an INPUT: setting it has
        // to recompute `mispricing_phi` and `s_phi_tick` through `ln`/`exp`,
        // which const evaluation cannot do. Assigning the field alone left the
        // preset reporting a 68.26-day half-life while the engine decayed at
        // the inherited 60, because the engine reads phi. A preset that
        // misreports its own coefficient is worse than one that does not carry
        // the search's value, so the field is left inherited and the intended
        // half-life is recorded in the changelog. To ship it for real, write
        // the recomputed phi and s_phi_tick bits literally, under a NEW name:
        // changing them here would move a published preset.
        // The curve's anchor, and the news sigma that pays for it.
        p.market_vol_vix_anchor = 15.98426471;
        p.endogenous_news_sigma = 0.021;
        p
    }

    /// The fourteenth preset, and the shipped default from 2026-08-28
    /// until 0.6.0, when [`PT_V16`] took it.
    ///
    /// Measured against [`PT_V12`] on thirteen seed blocks of thirty seeds
    /// each, plus six independent roster draws:
    ///
    /// | axis | pt-v12 | pt-v14 |
    /// |---|---|---|
    /// | 504-day panel, blocks fully in band | 3/13 | **10/12** |
    /// | crisis co-movement outside its real range | 4/13 | **2/12** |
    /// | crisis lever error against the real 6.16 | 0.0360 | **0.0176** |
    /// | roster shapes, cells in band | 131/138 | **137/138** |
    /// | driven window, day-weighted ratio to real | 1.527 | **1.336** |
    /// | volatility dispersion q3/q1 (real 1.273-1.486) | 1.205 | **1.242** |
    /// | section 8 | passes | passes, every held-out loss 0.0000 |
    ///
    /// It is the first vector in this programme's history to improve the
    /// realism panel while regressing nothing. Two predecessors reached the
    /// same panel numbers and neither could: `r15-70` (registered as
    /// [`PT_V13`]) bought them with crisis co-movement, and its sibling
    /// `r15-86` bought them with the crisis lever.
    ///
    /// **The mechanism is the sector block.** `sector_factor_sigma` carries
    /// more of the systematic variance while the market factor's persistence
    /// shifts to compensate, so names decorrelate ACROSS sectors rather than
    /// uniformly. That is what pulls crisis co-movement off the ceiling pt-v12
    /// sits against, and it is also what the cross-sectional dispersion gap
    /// wanted: two gaps, one mechanism.
    ///
    /// # What it cost, and what it does not fix
    ///
    /// **Its crisis lever is a sharp optimum rather than a basin.** Six
    /// neighbours at plus or minus three percent on all fifteen dials hold
    /// the 504 panel and crisis co-movement; only two keep the lever inside
    /// its five percent tolerance.
    ///
    /// That was the reason this preset was registered inert for most of a
    /// day, and it was withdrawn as a reason when the same probe was finally
    /// run on the INCUMBENT. [`PT_V12`] breaks the lever tolerance on the
    /// same three dials -- `market_vol_alpha`, `market_vol_beta` and
    /// `market_vol_vix_anchor` -- with a worst of 0.173 against a bar of
    /// 0.05. pt-v14 is more sensitive by a factor between 1.1 and 1.6, and
    /// both are three to six times past the bar. Those three dials ARE the
    /// volatility-versus-VIX curve and the crisis lever is a ratio of two
    /// points on it, so every vector is sensitive there. It is a property of
    /// the mechanism, not of this preset, and the era-boundary checklist's
    /// "region rather than a lucky point" test disqualifies the incumbent
    /// too. Stated and accepted on purpose.
    ///
    /// **Crisis co-movement is improved, not solved.** 2 of 13 blocks
    /// outside against pt-v12's 4. No candidate's across-block RANGE fits
    /// the 0.0630 band -- pt-v12's 0.0551 does and pt-v14's 0.0769 does not
    /// -- and eight search directions closed against that before one broke
    /// it. The two-component variance mixture at a slow timescale near 0.98
    /// reaches 0.0476 while holding this preset's panel, and is the lead for
    /// the successor rather than part of this one: it has four blocks where
    /// this has thirteen.
    ///
    /// **The one regression, stated and accepted.**
    /// `volume_abs_return_corr` loses margin. At the certified horizon and
    /// resolution -- 252 days, thirty seeds -- neither preset ever leaves
    /// the 0.46-to-0.66 band: 0 of 13 blocks for pt-v12 and 0 of 12 for
    /// pt-v14. But the median falls from 0.5632 to 0.5198, so pt-v14 sits
    /// nearer the floor, and at a shorter 180-day horizon on single seeds
    /// the failure rate doubles: 2 of 12 seeds against pt-v12's, 4 of 12
    /// against this preset's.
    ///
    /// Accepted on purpose. It is a narrowed margin on one statistic that
    /// never actually fails where the envelope certifies, against a 504-day
    /// panel rate of 11 blocks in 13 where pt-v12 holds 3, a halved crisis
    /// lever, halved crisis co-movement failures, and a 504-day volatility
    /// row that goes from 0.11 of headroom to 3.76. The trade is worth
    /// making and the cost is not hidden.
    ///
    /// **The driven window is improved, not closed.** 1.336 against 1.527,
    /// and still a third too volatile. Most of that excess is not the VIX
    /// channel but the QE valuation channel, whose gain
    /// ([`qe_pe_gain`]) ships inert because the driven test feeds it a
    /// harness-derived proxy rather than measured data.
    ///
    pub const fn pt_v14() -> ModelParams {
        let mut p = ModelParams::pt_v12();
        p.market_vol_alpha = 0.28035004;
        p.market_vol_beta = 0.69244622;
        p.jump_intensity_idio = 0.0068895346;
        p.jump_sigma_idio = 0.08745117;
        p.jump_intensity_market = 0.0565753337;
        p.jump_vix_coupling = 0.2626;
        p.idio_sigma_scale = 0.59604441;
        p.garch_vix_coupling = 0.14219611;
        p.crisis_vix_threshold = 30.88325108;
        p.crisis_blend_gain = 0.8275881;
        p.sector_factor_sigma = 0.0099802949;
        p.sector_loading = 0.58821442;
        // NOT mispricing_half_life_days. The search chose 68.25733542, but
        // this is a `const fn` and the half-life is an INPUT: setting it has
        // to recompute `mispricing_phi` and `s_phi_tick` through `ln`/`exp`,
        // which const evaluation cannot do. Assigning the field alone left the
        // preset reporting a 68.26-day half-life while the engine decayed at
        // the inherited 60, because the engine reads phi. A preset that
        // misreports its own coefficient is worse than one that does not carry
        // the search's value, so the field is left inherited and the intended
        // half-life is recorded in the changelog. To ship it for real, write
        // the recomputed phi and s_phi_tick bits literally, under a NEW name:
        // changing them here would move a published preset.
        p.market_vol_vix_anchor = 15.98426471;
        p.endogenous_news_sigma = 0.020360516;
        p
    }

    /// pt-v14 with the slow variance component switched on, its VIX
    /// coupling damped, and the daily credit floor enforced.
    ///
    /// Six numbers. Four are the two-timescale mixture the model has
    /// carried inert since the pt-v4 era: the slow component takes weight
    /// 0.35 of the market variance target (persistence 0.98, gain 0.05)
    /// and its VIX coupling is damped to 0.374. The fifth,
    /// [`daily_credit_floor_gain`](#structfield.daily_credit_floor_gain)
    /// at 1.0, activates the #48 fix in full, arriving the way the
    /// version policy requires a trajectory change to arrive: as a new
    /// preset. The sixth,
    /// [`sector_loading_beta_slope`](#structfield.sector_loading_beta_slope)
    /// at 0.5, gives sector exposure cross-sectional dispersion -- a
    /// high-beta name loads harder on its industry -- and is what closes
    /// the one crisis block the mixture alone cannot reach.
    ///
    /// The slow component carries part of the variance target at a
    /// persistence the fast component cannot, and its damped VIX coupling
    /// means a held crisis drives it less. Measured, that cuts how far
    /// crisis co-movement wanders across seed blocks while leaving the
    /// panel untouched. Against pt-v14 over thirteen thirty-seed blocks,
    /// on the build 0.4.3 restores:
    ///
    /// | | pt-v14 | pt-v15 | |
    /// |---|---|---|---|
    /// | 504 panel, paired per block | -- | 0W 13T 0L | never worse |
    /// | full-house panel blocks | 11/13 | 11/13 | |
    /// | crisis co-movement, range over blocks | 0.0774 | **0.0502** | band width 0.0630 |
    /// | co-movement blocks in range | 11/13 | 12/13 | |
    /// | crisis lever, median | 6.127 | **6.159** | real 6.16 |
    /// | lever blocks in tolerance | 13/13 | 13/13 | +/-5% |
    ///
    /// **The headline is the range row.** pt-v14's crisis co-movement
    /// varies more across seed blocks than the whole width of the real
    /// band, so no placement of its centre can hold every block. 0.0502
    /// fits, and beats the 0.0551 of pt-v12, the only other preset whose
    /// range ever has. The lever error falls 0.54% to 0.01%.
    ///
    /// The damp is 0.374 and not lower because the two crisis instruments
    /// trade against each other block by block: every measured damp from
    /// 0.26 to 0.374 moves both monotonically, one binding block caps
    /// co-movement from above while another floors the lever from below,
    /// and no value satisfies both on all thirteen. 0.374 is the end of
    /// that frontier that holds the lever everywhere and cedes a single
    /// co-movement block -- one pt-v14 also fails.
    ///
    /// The sector dispersion takes that ceded block back, which no dial
    /// inside the variance mixture can: it raises pairwise sector
    /// co-movement without touching market variance. Confirmed over
    /// thirteen thirty-seed blocks against the five-override base, paired
    /// within one run: co-movement in range 13/13 (the base 12/13), the
    /// range over blocks 0.0502 to 0.0464, the ceiling keeping 0.0148 of
    /// headroom, the lever 13/13 at median 6.152, the panel a tie on
    /// every block. **The first measured cell to hold both crisis
    /// instruments on all thirteen blocks.**
    ///
    /// The credit floor is measured free: against the same base with only
    /// this dial moved, the panel reads 1W 12T 0L and both crisis
    /// instruments are identical on every block. What it buys is an
    /// invariant rather than a statistic: the corporate spread can no
    /// longer drift below its floor between meetings.
    ///
    /// NOT the default. [`PT_V16`] holds that, and the envelope certifies
    /// whatever `DEFAULT_PRESET_NAME` names.
    pub const fn pt_v15() -> ModelParams {
        let mut p = ModelParams::pt_v14();
        p.market_vol_slow_weight = 0.35;
        p.market_vol_slow_persistence = 0.98;
        p.market_vol_slow_gain = 0.05;
        p.market_vol_slow_vix_damp = 0.374;
        p.daily_credit_floor_gain = 1.0;
        p.sector_loading_beta_slope = 0.5;
        p
    }

    /// pt-v15 re-levelled: the QE channel silenced, the asymmetry
    /// composition, and the 0.86x joint volatility trim.
    ///
    /// **The first preset to hold the complete card at the deepest
    /// standard this programme runs** -- twenty-six blocks spanning both
    /// the qualification corpus and thirteen blocks no search ever
    /// touched, one hundred seeds per block:
    ///
    /// | | pt-v16 | the pre-trim candidate |
    /// |---|---|---|
    /// | 504 full-house | **26/26** | 24/26 |
    /// | crisis co-movement in range | 26/26 (spread 0.0406) | 26/26 |
    /// | crisis lever in tolerance | 26/26 (median 6.241) | 26/26 |
    /// | driven noise ratio | **1.1246** | 1.2995 |
    /// | out-of-band rows, anywhere | **none** | corr_asymmetry x2 |
    ///
    /// Three ideas compose. `qe_pe_gain` 0.0 silences a channel whose
    /// driven input is a proxy anticorrelated with measured Fed purchases
    /// (-0.485) and which subtracts realism with either input.
    /// `vix_cycle_amplitude` 0.85, `sector_loading_beta_slope` 0.7 and
    /// `market_beta_down_asym` 0.025 are the correlation-asymmetry
    /// composition: down ticks of the factor transmit harder (exceedance
    /// correlation, the mechanism the statistic is about), funded by
    /// sector-loading dispersion, seasoned by pulling the business-cycle
    /// share of the VIX in. And the six noise sources scale together by
    /// 0.86, which round 101 measured as the model running 20-25% hot at
    /// both held-VIX ends with the ratio immaculate, and round 107 proved
    /// must be trimmed JOINTLY -- any single source alone re-balances the
    /// market/idio split and collapses correlations instead of
    /// re-levelling.
    ///
    /// Scope, stated: corr_asymmetry's median (-0.022) is band-complete
    /// and still below every real reference window; the driven window at
    /// 1.12 is the closest this model has been to real (1.00) and is not
    /// there. The gaps that remain are real, smaller than they have ever
    /// been, and named in the record.
    ///
    /// THE DEFAULT since 0.6.0, and what the envelope certifies.
    /// [`PT_V14`] and every earlier preset stay selectable and
    /// bit-reproducing, so anything recorded under one replays by naming it.
    pub const fn pt_v16() -> ModelParams {
        let mut p = ModelParams::pt_v15();
        p.qe_pe_gain = 0.0;
        p.vix_cycle_amplitude = 0.85;
        p.sector_loading_beta_slope = 0.7;
        p.market_beta_down_asym = 0.025;
        // The 0.86x joint level trim: every noise source scaled together,
        // which preserves correlations and ratios while bringing the
        // volatility LEVEL to real scale. Trimming any one source alone
        // re-balances instead of re-levelling (round 107).
        p.market_factor_sigma = 0.007593024924589399;
        p.idio_sigma_scale = 0.5125981926;
        p.jump_sigma_idio = 0.0752080062;
        p.jump_sigma_market = 0.0024597567320385947;
        p.endogenous_news_sigma = 0.01751004376;
        p.sector_factor_sigma = 0.008583053614;
        // The same-day volume coupling, raised off the 252-day floor. The
        // response term is the only one tying a name's volume to the size
        // of TODAY'S move (see the field's docstring); at the shipped 0.6
        // the 252-day volume-|return| correlation sat below the weakest
        // real reference window on every block measured. At 1.0 all 26
        // qualification blocks clear the floor and the union card is
        // clean on both panels (volqual, 100 seeds).
        p.volume_move_response = 1.0;
        // The VIX learns fear (the fear-gap campaign, rounds 124-135).
        // The realized-vol feedback closes the loop the code left open
        // since the implied read was built: a third of the VIX target is
        // the variance process's own inverse. Fear decays at six tenths
        // of the rate it arrives, and the reversion slows to match.
        // Measured against ^VIX/^GSPC 2004-2025: realized-vol tracking
        // 0.16 -> 0.57, spike asymmetry 0.95 -> 1.28 (real 1.20), day
        // persistence 0.90 -> 0.985. Two numbers are stated rather than
        // hidden: crisis frequency P(VIX>30) stays below real (every
        // mechanism that raised it broke the certified statistics --
        // three families measured dead), and one corr_asymmetry row on
        // one of twenty-six blocks sits 0.0025 past its floor.
        p.vix_realised_vol_weight = 0.3;
        p.vix_decay_ratio = 0.6;
        p.vix_mean_reversion = 0.06;
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
    /// `python_engine.rs`'s default, and as of the second 2026-08-26 era
    /// boundary it is [`PT_V12`]. `pt-v1` and `pt-v2` remain selectable and
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
            "pt-v11" => Some(PT_V11),
            "pt-v12" => Some(PT_V12),
            "pt-v13" => Some(PT_V13),
            "pt-v14" => Some(PT_V14),
            "pt-v15" => Some(PT_V15),
            "pt-v16" => Some(PT_V16),
            _ => None,
        }
    }

    /// Names of the shipped presets, for error messages.
    pub fn preset_names() -> &'static [&'static str] {
        &["pt-v1", "pt-v2", "pt-v3", "pt-v4", "pt-v5", "pt-v6", "pt-v7", "pt-v8", "pt-v9", "pt-v10",
          "pt-v11", "pt-v12", "pt-v13", "pt-v14", "pt-v15",
          "pt-v16"]
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
            "sector_loading" => self.sector_loading,
            "sector_loading_beta_slope" => self.sector_loading_beta_slope,
            "crisis_blend_variance_damp" => self.crisis_blend_variance_damp,
            "qe_pe_gain" => self.qe_pe_gain,
            "qe_pe_stock_gain" => self.qe_pe_stock_gain,
            "idio_sigma_scale" => self.idio_sigma_scale,
            "idio_sigma_beta_exponent" => self.idio_sigma_beta_exponent,
            "order_flow_coefficient" => self.order_flow_coefficient,
            "inflation_ceiling" => self.inflation_ceiling,
            "inflation_floor" => self.inflation_floor,
            "inflation_reversion" => self.inflation_reversion,
            "informed_flow_fraction" => self.informed_flow_fraction,
            "endogenous_news_intensity" => self.endogenous_news_intensity,
            "endogenous_news_sigma" => self.endogenous_news_sigma,
            "news_sector_weight" => self.news_sector_weight,
            "news_market_weight" => self.news_market_weight,
            "crash_amplifier_threshold" => self.crash_amplifier_threshold,
            "crash_amplifier_slope" => self.crash_amplifier_slope,
            "crisis_blend_ramp" => self.crisis_blend_ramp,
            "crisis_blend_cap" => self.crisis_blend_cap,
            "crisis_blend_gain" => self.crisis_blend_gain,
            "crisis_blend_source" => self.crisis_blend_source,
            "sector_vix_coupling" => self.sector_vix_coupling,
            "garch_omega" => self.garch_omega,
            "garch_alpha" => self.garch_alpha,
            "garch_beta" => self.garch_beta,
            "garch_gamma" => self.garch_gamma,
            "garch_ceiling_multiple" => self.garch_ceiling_multiple,
            "garch_vix_coupling" => self.garch_vix_coupling,
            "garch_floor_multiple" => self.garch_floor_multiple,
            "market_vol_alpha" => self.market_vol_alpha,
            "market_vol_beta" => self.market_vol_beta,
            "market_vol_gamma" => self.market_vol_gamma,
            "market_vol_ceiling_multiple" => self.market_vol_ceiling_multiple,
            "market_vol_floor_multiple" => self.market_vol_floor_multiple,
            "market_vol_vix_coupling" => self.market_vol_vix_coupling,
            "market_vol_vix_anchor" => self.market_vol_vix_anchor,
            "market_vol_vix_smooth" => self.market_vol_vix_smooth,
            "market_vol_vix_exponent" => self.market_vol_vix_exponent,
            "market_beta_down_asym" => self.market_beta_down_asym,
            "market_beta_down_asym_lag" => self.market_beta_down_asym_lag,
            "market_beta_up_comp" => self.market_beta_up_comp,
            "market_vol_slow_persistence" => self.market_vol_slow_persistence,
            "market_vol_slow_gain" => self.market_vol_slow_gain,
            "fair_value_book_floor" => self.fair_value_book_floor,
            "market_vol_slow_weight" => self.market_vol_slow_weight,
            "volume_idio_variance_gain" => self.volume_idio_variance_gain,
            "volume_idio_persistence" => self.volume_idio_persistence,
            "volume_idio_sigma" => self.volume_idio_sigma,
            "garch_cascade_components" => self.garch_cascade_components,
            "garch_cascade_ratio" => self.garch_cascade_ratio,
            "garch_cascade_weight" => self.garch_cascade_weight,
            "volume_move_floor" => self.volume_move_floor,
            "volume_move_response" => self.volume_move_response,
            "volume_move_cap" => self.volume_move_cap,
            "volume_move_noise" => self.volume_move_noise,
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
            "jump_vix_coupling" => self.jump_vix_coupling,
            "garch_beta_dispersion" => self.garch_beta_dispersion,
            "jump_momentum_share" => self.jump_momentum_share,
            "volume_persistence" => self.volume_persistence,
            "volume_innovation_sigma" => self.volume_innovation_sigma,
            "size_effect_smoothness" => self.size_effect_smoothness,
            "size_effect_exponent" => self.size_effect_exponent,
            "spread_size_smoothness" => self.spread_size_smoothness,
            "spread_size_exponent" => self.spread_size_exponent,
            "vix_mean_reversion" => self.vix_mean_reversion,
            "vix_decay_ratio" => self.vix_decay_ratio,
            "vix_jump_intensity" => self.vix_jump_intensity,
            "vix_jump_scale" => self.vix_jump_scale,
            "forced_flow_gain" => self.forced_flow_gain,
            "forced_flow_threshold" => self.forced_flow_threshold,
            "forced_flow_beta_exponent" => self.forced_flow_beta_exponent,
            "forced_flow_reservoir" => self.forced_flow_reservoir,
            "forced_flow_replenish" => self.forced_flow_replenish,
            "vix_selfex_gain" => self.vix_selfex_gain,
            "vix_selfex_threshold" => self.vix_selfex_threshold,
            "vix_selfex_min" => self.vix_selfex_min,
            "vix_selfex_scale" => self.vix_selfex_scale,
            "vix_selfex_decay" => self.vix_selfex_decay,
            "vix_selfex_relax" => self.vix_selfex_relax,
            "vix_selfex_excite" => self.vix_selfex_excite,
            "vix_selfex_excite_decay" => self.vix_selfex_excite_decay,
            "vix_selfex_phase" => self.vix_selfex_phase,
            "vix_selfex_size_coupling" => self.vix_selfex_size_coupling,
            "vix_har_weight" => self.vix_har_weight,
            "vix_har_mid" => self.vix_har_mid,
            "vix_har_slow" => self.vix_har_slow,
            "vix_har_vrp" => self.vix_har_vrp,
            "vix_cycle_amplitude" => self.vix_cycle_amplitude,
            "vix_realised_vol_weight" => self.vix_realised_vol_weight,
            "vix_return_clamp" => self.vix_return_clamp,
            "vix_return_gain" => self.vix_return_gain,
            "vix_return_gain_up" => self.vix_return_gain_up,
            "vix_return_source" => self.vix_return_source,
            "vix_target_shock_cap" => self.vix_target_shock_cap,
            "crisis_vix_threshold" => self.crisis_vix_threshold,
            "usd_crisis_vix_threshold" => self.usd_crisis_vix_threshold,
            "daily_credit_floor_gain" => self.daily_credit_floor_gain,
            "news_peer_weight" => self.news_peer_weight,
            "news_peer_weight_down" => self.news_peer_weight_down,
            "news_peer_vix_coupling" => self.news_peer_vix_coupling,
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
            "sector_loading" => out.sector_loading = value,
            "sector_loading_beta_slope" => out.sector_loading_beta_slope = value,
            "crisis_blend_variance_damp" => out.crisis_blend_variance_damp = value,
            "qe_pe_gain" => out.qe_pe_gain = value,
            "qe_pe_stock_gain" => out.qe_pe_stock_gain = value,
            "idio_sigma_scale" => out.idio_sigma_scale = value,
            "idio_sigma_beta_exponent" => out.idio_sigma_beta_exponent = value,
            "order_flow_coefficient" => out.order_flow_coefficient = value,
            "inflation_ceiling" => out.inflation_ceiling = value,
            "inflation_floor" => out.inflation_floor = value,
            "inflation_reversion" => out.inflation_reversion = value,
            "informed_flow_fraction" => out.informed_flow_fraction = value,
            "endogenous_news_intensity" => out.endogenous_news_intensity = value,
            "endogenous_news_sigma" => out.endogenous_news_sigma = value,
            "news_sector_weight" => out.news_sector_weight = value,
            "news_market_weight" => out.news_market_weight = value,
            "crash_amplifier_threshold" => out.crash_amplifier_threshold = value,
            "crash_amplifier_slope" => out.crash_amplifier_slope = value,
            "crisis_blend_ramp" => out.crisis_blend_ramp = value,
            "crisis_blend_cap" => out.crisis_blend_cap = value,
            "crisis_blend_gain" => out.crisis_blend_gain = value,
            "crisis_blend_source" => out.crisis_blend_source = value,
            "sector_vix_coupling" => out.sector_vix_coupling = value,
            "garch_omega" => out.garch_omega = value,
            "garch_alpha" => out.garch_alpha = value,
            "garch_beta" => out.garch_beta = value,
            "garch_gamma" => out.garch_gamma = value,
            "garch_ceiling_multiple" => out.garch_ceiling_multiple = value,
            "garch_vix_coupling" => out.garch_vix_coupling = value,
            "garch_floor_multiple" => out.garch_floor_multiple = value,
            "market_vol_alpha" => out.market_vol_alpha = value,
            "market_vol_beta" => out.market_vol_beta = value,
            "market_vol_gamma" => out.market_vol_gamma = value,
            "market_vol_ceiling_multiple" => out.market_vol_ceiling_multiple = value,
            "market_vol_floor_multiple" => out.market_vol_floor_multiple = value,
            "market_vol_vix_coupling" => out.market_vol_vix_coupling = value,
            "market_vol_vix_anchor" => out.market_vol_vix_anchor = value,
            "market_vol_vix_smooth" => out.market_vol_vix_smooth = value,
            "market_vol_vix_exponent" => out.market_vol_vix_exponent = value,
            "market_beta_down_asym" => out.market_beta_down_asym = value,
            "market_beta_down_asym_lag" => out.market_beta_down_asym_lag = value,
            "market_beta_up_comp" => out.market_beta_up_comp = value,
            "market_vol_slow_persistence" => out.market_vol_slow_persistence = value,
            "market_vol_slow_gain" => out.market_vol_slow_gain = value,
            "fair_value_book_floor" => out.fair_value_book_floor = value,
            "market_vol_slow_weight" => out.market_vol_slow_weight = value,
            "volume_idio_variance_gain" => out.volume_idio_variance_gain = value,
            "volume_idio_persistence" => out.volume_idio_persistence = value,
            "volume_idio_sigma" => out.volume_idio_sigma = value,
            "garch_cascade_components" => out.garch_cascade_components = value,
            "garch_cascade_ratio" => out.garch_cascade_ratio = value,
            "garch_cascade_weight" => out.garch_cascade_weight = value,
            "volume_move_floor" => out.volume_move_floor = value,
            "volume_move_response" => out.volume_move_response = value,
            "volume_move_cap" => out.volume_move_cap = value,
            "volume_move_noise" => out.volume_move_noise = value,
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
            "jump_vix_coupling" => out.jump_vix_coupling = value,
            "jump_sigma_market" => out.jump_sigma_market = value,
            "volume_innovation_sigma" => out.volume_innovation_sigma = value,
            "volume_persistence" => out.volume_persistence = value,
            "size_effect_exponent" => out.size_effect_exponent = value,
            "size_effect_smoothness" => out.size_effect_smoothness = value,
            "spread_size_exponent" => out.spread_size_exponent = value,
            "spread_size_smoothness" => out.spread_size_smoothness = value,
            "vix_mean_reversion" => out.vix_mean_reversion = value,
            "vix_decay_ratio" => out.vix_decay_ratio = value,
            "vix_jump_intensity" => out.vix_jump_intensity = value,
            "vix_jump_scale" => out.vix_jump_scale = value,
            "forced_flow_gain" => out.forced_flow_gain = value,
            "forced_flow_threshold" => out.forced_flow_threshold = value,
            "forced_flow_beta_exponent" => out.forced_flow_beta_exponent = value,
            "forced_flow_reservoir" => out.forced_flow_reservoir = value,
            "forced_flow_replenish" => out.forced_flow_replenish = value,
            "vix_selfex_gain" => out.vix_selfex_gain = value,
            "vix_selfex_threshold" => out.vix_selfex_threshold = value,
            "vix_selfex_min" => out.vix_selfex_min = value,
            "vix_selfex_scale" => out.vix_selfex_scale = value,
            "vix_selfex_decay" => out.vix_selfex_decay = value,
            "vix_selfex_relax" => out.vix_selfex_relax = value,
            "vix_selfex_excite" => out.vix_selfex_excite = value,
            "vix_selfex_excite_decay" => out.vix_selfex_excite_decay = value,
            "vix_selfex_phase" => out.vix_selfex_phase = value,
            "vix_selfex_size_coupling" => out.vix_selfex_size_coupling = value,
            "vix_har_weight" => out.vix_har_weight = value,
            "vix_har_mid" => out.vix_har_mid = value,
            "vix_har_slow" => out.vix_har_slow = value,
            "vix_har_vrp" => out.vix_har_vrp = value,
            "vix_cycle_amplitude" => out.vix_cycle_amplitude = value,
            "vix_realised_vol_weight" => out.vix_realised_vol_weight = value,
            "vix_return_clamp" => out.vix_return_clamp = value,
            "vix_return_gain" => out.vix_return_gain = value,
            "vix_return_gain_up" => out.vix_return_gain_up = value,
            "vix_return_source" => out.vix_return_source = value,
            "vix_target_shock_cap" => out.vix_target_shock_cap = value,
            "crisis_vix_threshold" => out.crisis_vix_threshold = value,
            "usd_crisis_vix_threshold" => out.usd_crisis_vix_threshold = value,
            "daily_credit_floor_gain" => out.daily_credit_floor_gain = value,
            "news_peer_weight" => out.news_peer_weight = value,
            "news_peer_weight_down" => out.news_peer_weight_down = value,
            "news_peer_vix_coupling" => out.news_peer_vix_coupling = value,
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
        "crisis_blend_gain",
        "crisis_blend_ramp",
        "crisis_blend_source",
        "crisis_blend_variance_damp",
        "crisis_vix_threshold",
        "crowd_lean_cap",
        "crowd_momentum_gain",
        "crowd_valuation_gain",
        "daily_credit_floor_gain",
        "endogenous_news_intensity",
        "endogenous_news_sigma",
        "fair_value_book_floor",
        "garch_alpha",
        "garch_beta",
        "garch_beta_dispersion",
        "garch_cascade_components",
        "garch_cascade_ratio",
        "garch_cascade_weight",
        "garch_ceiling_multiple",
        "garch_floor_multiple",
        "garch_gamma",
        "garch_omega",
        "garch_vix_coupling",
        "idio_sigma_beta_exponent",
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
        "jump_vix_coupling",
        "market_factor_sigma",
        "market_vol_alpha",
        "market_vol_beta",
        "market_vol_gamma",
        "market_vol_ceiling_multiple",
        "market_vol_floor_multiple",
        "market_vol_slow_gain",
        "market_vol_slow_persistence",
        "market_vol_slow_vix_damp",
        "market_vol_slow_weight",
        "market_vol_vix_anchor",
        "market_vol_vix_smooth",
        "market_vol_vix_exponent",
        "market_beta_down_asym",
        "market_beta_down_asym_lag",
        "market_vol_vix_coupling",
        "mispricing_cap",
        "mispricing_half_life_days",
        "momentum_theta",
        "news_market_weight",
        "news_peer_vix_coupling",
        "news_peer_weight",
        "news_peer_weight_down",
        "news_sector_weight",
        "order_flow_coefficient",
        "price_breaker_fraction",
        "price_hard_cap",
        "qe_pe_gain",
        "qe_pe_stock_gain",
        "regime_stress_points",
        "sector_factor_sigma",
        "sector_loading",
        "sector_loading_beta_slope",
        "sector_vix_coupling",
        "size_effect_exponent",
        "size_effect_smoothness",
        "spread_size_exponent",
        "spread_size_smoothness",
        "universe_stress_decay",
        "universe_stress_weight",
        "usd_crisis_vix_threshold",
        "vix_cycle_amplitude",
        "vix_mean_reversion",
        "vix_decay_ratio",
        "vix_jump_intensity",
        "vix_jump_scale",
        "forced_flow_gain",
        "forced_flow_threshold",
        "forced_flow_beta_exponent",
        "forced_flow_reservoir",
        "forced_flow_replenish",
        "market_beta_up_comp",
        "vix_selfex_gain",
        "vix_selfex_threshold",
        "vix_selfex_min",
        "vix_selfex_scale",
        "vix_selfex_decay",
        "vix_selfex_relax",
        "vix_selfex_excite",
        "vix_selfex_excite_decay",
        "vix_selfex_phase",
        "vix_selfex_size_coupling",
        "vix_har_weight",
        "vix_har_mid",
        "vix_har_slow",
        "vix_har_vrp",
        "vix_realised_vol_weight",
        "vix_return_clamp",
        "vix_return_gain",
        "vix_return_gain_up",
        "vix_return_source",
        "vix_target_shock_cap",
        "volume_idio_persistence",
        "volume_idio_sigma",
        "volume_idio_variance_gain",
        "volume_innovation_sigma",
        "volume_move_cap",
        "volume_move_floor",
        "volume_move_noise",
        "volume_move_response",
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
        assert_eq!(ModelParams::preset("pt-v10").unwrap().fingerprint(), "pt-v10");
        assert_eq!(PT_V11.fingerprint(), "pt-v11");
        assert_eq!(PT_V12.fingerprint(), "pt-v12");
        assert_eq!(ModelParams::preset("pt-v12").unwrap().fingerprint(), "pt-v12");
        assert_eq!(ModelParams::preset("pt-v11").unwrap().fingerprint(), "pt-v11");
        // The sentinel is a name no preset will ever take, not the NEXT one.
        // It used to be the next unreleased name, which meant this guard
        // sprang the day that preset shipped rather than the day something
        // went wrong: pt-v11 tripped it on registration, twice (here and in
        // tests/test_model_params.py).
        assert!(ModelParams::preset("pt-v999").is_none());

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
    fn the_default_is_one_preset_named_in_one_place() {
        // This test was called `the_default_preset_is_pt_v10` and asserted
        // "pt-v12" -- edited at the 2026-08-26 boundary and its NAME left
        // behind, which is the drift the era-boundary checklist exists to
        // stop. So it no longer names a preset at all.
        //
        // What matters is not WHICH preset is the default but that exactly
        // one thing decides it. An engine built without a model must agree
        // with `DEFAULT_PRESET_NAME`, and that name must resolve. If those
        // two ever disagree, every published figure silently describes a
        // different market from the one the docs name.
        let named = crate::params::ModelParams::preset(DEFAULT_PRESET_NAME)
            .expect("DEFAULT_PRESET_NAME must resolve to a shipped preset");
        assert_eq!(named.fingerprint(), DEFAULT_PRESET_NAME);
        assert_eq!(
            crate::engine::Engine::default_model().fingerprint(),
            DEFAULT_PRESET_NAME
        );
        // Every earlier default still exists and still answers to its name,
        // which is what makes a recorded result replayable across a
        // boundary.
        assert_eq!(crate::params::PT_V3.fingerprint(), "pt-v3");
        assert_eq!(crate::params::PT_V10.fingerprint(), "pt-v10");
        assert_eq!(crate::params::PT_V12.fingerprint(), "pt-v12");
        assert_eq!(crate::params::PT_V13.fingerprint(), "pt-v13");
        // pt-v14 held the default from the 2026-08-28 boundary until pt-v16
        // took it at 0.6.0. The guard that used to assert a preset was NOT
        // the default lived here and is gone on purpose: it existed while
        // the preset was registered inert, and a stale assertion about which
        // preset is default is exactly the drift this test exists to catch.
        assert_eq!(crate::params::PT_V14.fingerprint(), "pt-v14");
        assert_eq!(crate::params::PT_V15.fingerprint(), "pt-v15");
        assert_eq!(crate::params::PT_V16.fingerprint(), "pt-v16");
        assert_eq!(DEFAULT_PRESET_NAME, "pt-v16");
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

    /// pt-v16's qualification is asymqual's `cand` cell: thirteen
    /// certified blocks measured on pt-v15 plus these four overrides. The
    /// frozen preset inherits those measurements only if it is that vector
    /// to the bit, which this asserts. If it ever fails, the preset has
    /// drifted from the evidence that qualified it.
    #[test]
    fn pt_v16_is_the_measured_cand_cell_to_the_bit() {
        let measured = ModelParams::preset("pt-v15")
            .unwrap()
            .with_override("qe_pe_gain", 0.0)
            .and_then(|m| m.with_override("vix_cycle_amplitude", 0.85))
            .and_then(|m| m.with_override("sector_loading_beta_slope", 0.7))
            .and_then(|m| m.with_override("market_beta_down_asym", 0.025))
            .and_then(|m| m.with_override("market_factor_sigma", 0.007593024924589399))
            .and_then(|m| m.with_override("idio_sigma_scale", 0.5125981926))
            .and_then(|m| m.with_override("jump_sigma_idio", 0.0752080062))
            .and_then(|m| m.with_override("jump_sigma_market", 0.0024597567320385947))
            .and_then(|m| m.with_override("endogenous_news_sigma", 0.01751004376))
            .and_then(|m| m.with_override("sector_factor_sigma", 0.008583053614))
            .and_then(|m| m.with_override("volume_move_response", 1.0))
            .and_then(|m| m.with_override("vix_realised_vol_weight", 0.3))
            .and_then(|m| m.with_override("vix_decay_ratio", 0.6))
            .and_then(|m| m.with_override("vix_mean_reversion", 0.06))
            .expect("every folded dial is settable");
        assert_eq!(crate::params::PT_V16.digest(), measured.digest());
        assert_eq!(crate::params::PT_V16.fingerprint(), "pt-v16");
    }

    #[test]
    fn every_preset_runs_the_half_life_it_reports() {
        // The gap this closes. `with_override` recomputes `mispricing_phi`
        // from the half-life correctly, and the test below proves it. A
        // preset CONSTRUCTOR cannot: it is a `const fn`, `ln` and `exp` are
        // not available there, and assigning the field alone leaves phi at
        // whatever the base preset had. pt-v13 and pt-v14 shipped in 0.4.0
        // reporting a 68.26-day half-life while decaying at the inherited
        // 60, because the engine reads phi and nothing compared the two.
        for name in ModelParams::preset_names() {
            let p = ModelParams::preset(name).expect("a listed preset must resolve");
            let implied = (0.5f64).ln() / p.mispricing_phi.ln();
            assert!(
                (implied - p.mispricing_half_life_days).abs() < 1e-6,
                "{name} reports a half-life of {} days but its mispricing_phi \
                 decays at {implied}. A const-fn constructor cannot recompute \
                 phi, so assigning the field alone makes the preset misreport \
                 its own coefficient.",
                p.mispricing_half_life_days,
            );
        }
    }

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
