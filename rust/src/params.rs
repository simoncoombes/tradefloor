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
    /// 0.002 -- pt-v1 through pt-v6, which this paragraph called "every
    /// preset" until 2026-09-17 -- is about a quarter of one percent of a
    /// name's daily variance: at that value the model had a market factor
    /// and nothing else, and residual correlation after it was diagonal.
    /// pt-v7 onward carry the larger sigma this entry argues for, and
    /// pt-v16 to pt-v19 ship 0.008583053614. Nothing measured that until
    /// 2026-08-25, when
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
    /// Which participation law the order-flow impact multiplier follows.
    ///
    /// At 0.0, the shipped law unchanged: the multiplier is
    /// `max(0.2, min(participation, 10) * 0.15)`. At 1.0 it is
    /// `0.15 * participation` up to the knee at ten and
    /// `1.5 * sqrt(participation / 10)` above it.
    ///
    /// A BRANCH at zero rather than a blend, so every preset that predates
    /// this dial is bit-identical instead of owing anything to an argument
    /// about arithmetic on a signed zero.
    ///
    /// THE CLOCK. Participation is a tick's total order volume over the
    /// name's average MINUTE volume, which is its average DAILY volume over
    /// 390, the minutes in a session. That is the only rate this dial
    /// touches and its clock is the trading day, not the calendar one. The
    /// 365-day economy never enters here.
    ///
    /// WHAT IS WRONG WITH THE SHIPPED LAW: it has three regimes, not two.
    /// The floor and the linear term meet at exactly 4/3, because 0.15
    /// times 4/3 is 0.2, so the shipped multiplier is the linear law
    /// clamped at BOTH ends. Its elasticity in participation is 0 below
    /// 4/3, 1 between 4/3 and 10, and 0 again above 10. The reported defect
    /// was the ceiling; the floor is the same defect at the other end.
    ///
    /// WHAT THE MEASURED LAW IS. Below the knee, linear: Cont, Kukanov and
    /// Stoikov (Journal of Financial Econometrics 12(1) 47-88, 2014) find
    /// price change over short intervals linear in order-flow imbalance
    /// with slope inversely proportional to depth, which is this function's
    /// scale exactly. Above it, one half: Toth, Lemperiere, Deremble, de
    /// Lataillade, Kockelkoren and Bouchaud (Physical Review X 1, 021006,
    /// 2011) give the square root for orders large against available
    /// volume, and Almgren, Thum, Hauptmann and Li (Risk 18(7) 58-62, 2005)
    /// measure 0.6 on the same object. The two regimes are one law rather
    /// than two findings to reconcile: Cont et al. derive the square root
    /// from their own linear model by a scaling argument, in their own
    /// abstract.
    ///
    /// NO NEW CONSTANT, WHICH IS THE POINT. 0.15 and 10 are the numbers
    /// already in the source. The knee does not move, the two branches
    /// agree at 1.5 where they meet, and the band between 4/3 and 10 is
    /// unchanged to the bit. Only the two clamped tails differ and neither
    /// carries a coefficient anyone could tune.
    ///
    /// WHAT IT DOES NOT CLAIM. It does not restate the DEPTH exponent.
    /// `calculate_live_factors` multiplies this by `liquidity_factor`,
    /// which divides by the same per-minute volume a second time, so impact
    /// still falls as depth squared where the cited law gives depth. That
    /// is a separate defect, issue #182, and this dial does not touch it.
    ///
    /// It also changes nothing in a run that injects no order flow.
    /// `TickInputs.order_volumes` is the empty slice at every construction
    /// site but the two `order_flow=` paths, and at zero volume the raw
    /// imbalance is the literal `0.0`, whose product with either multiplier
    /// is `+0.0`.
    pub order_flow_impact_law: f64,
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
    /// The news machinery existed and never fired before this dial, which
    /// is the state the rest of this paragraph describes; pt-v11 onward
    /// ship 0.05 here, with a sigma of 0.03 falling to 0.01751004376 at
    /// pt-v16. News is caller-supplied: `SessionRequest.news` is a slice
    /// the engine never filled, and the only populated path is
    /// `tradefloor.replay`, which feeds
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
    /// route, where before pt-v11 it was entirely exogenous, a per-tick
    /// sector draw
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
    ///
    /// # This is the dial the VIX loop now answers to, and it is unbounded
    /// in the regime
    ///
    /// `shock_magnitude` is normalised by the BASE sigma and not the
    /// conditional one — deliberately, and `factors.rs` states and costs the
    /// alternative — so the amplifier's conditional second moment
    /// (`market::index_var::amplifier_moments`) grows as the square of the
    /// regime ratio rather than staying flat in it. Under
    /// `vix_level_identity` that moment is in the VIX's own target, so the
    /// map from the VIX to the variance it implies is superlinear at the top
    /// of the VIX's range. Measured on a pin ladder with the crisis blend
    /// off (`tools/calibration/pin_ladder.py`), `implied(v) / v` falls to
    /// 0.732 at VIX 40 and then rises: 0.854 at 80, 0.941 at 100. On that
    /// slope it would cross one somewhere past 110 — an extrapolation, and
    /// said to be one — and it crosses one INSIDE `vix_ceiling` on a roster
    /// whose factor block is a larger share of the index's variance.
    ///
    /// That is not the amplifier being wrong. It is the amplifier having a
    /// loop gain, which nothing measured while the read-back was blind to
    /// it. Against a factor-variance process whose ORDINARY excursions reach
    /// twenty to fifty times its target — see `market_vol_alpha`, and note
    /// that the shipped mixture's fourth moment is finite — it takes the
    /// VIX to `vix_ceiling` and the
    /// loop holds it there: 81 of 7,560 seed-days on three of the
    /// certification protocol's thirty rosters, with the crisis blend
    /// switched entirely off. See `market::index_var`.
    pub crash_amplifier_slope: f64,
    /// Which sigma the crash amplifier measures a shock in: the BASELINE
    /// constant (0.0, every preset before pt-v19) or the tick's own
    /// CONDITIONAL sigma (nonzero).
    ///
    /// # A switch, and it is one on purpose
    ///
    /// There is no half-normalised shock. `factors.rs` branches on
    /// `== 0.0` and reads the conditional sigma on every other value, so
    /// the MAGNITUDE is unused and the two admissible values are the two
    /// ends. It belongs in `atlas.SWITCH_DIALS` for that reason and NOT in
    /// the survey's zero-shipped ranges: a Latin hypercube over `[0, 1]`
    /// never draws exactly zero, so an axis would survey the dial
    /// permanently ON and report the far end as the whole map.
    /// `cycle_stationary_opening` and `garch_omega_sector_scaled` are the
    /// same shape and carry the same note.
    ///
    /// At 0.0 nothing is computed that was not computed before and no draw
    /// moves, so every preset from pt-v1 to pt-v18 is BIT-IDENTICAL.
    ///
    /// # What it changes, in one line
    ///
    /// `shock_magnitude` is `|F| / sigma`. With the baseline sigma the
    /// amplifier's argument is `s|z|` for the regime ratio
    /// `s = sqrt(v_f) / market_factor_sigma`, so a high-variance regime
    /// pushes more ticks past `crash_amplifier_threshold` AND pushes them
    /// further past it. With the conditional sigma the argument is `|z|`
    /// and the regime drops out entirely.
    ///
    /// # Why pt-v19 needs it, which is a stability result and not a taste
    ///
    /// In `market::index_var`'s closed form the amplifier's second moment
    /// is `E[z^2 A^2]` at `a = m s`, `c = T / s`, which grows without
    /// bound in `s`. Under `vix_level_identity` that moment is inside the
    /// VIX's own target, so the map `v -> implied(v)` is SUPERLINEAR at the
    /// top of the VIX's range: `implied(v) / v` falls to 0.732 at VIX 40
    /// and then rises, 0.854 at 80 and 0.941 at 100, and it crosses one
    /// inside `vix_ceiling` on a roster whose factor block is a larger
    /// share of the index's variance. The ceiling is then absorbing, and
    /// measured it is absorbed: 81 of 7,560 seed-days on three of the
    /// certification protocol's thirty rosters with the crisis blend
    /// switched entirely OFF, and 11 of 120 rosters over the population
    /// b4read1 censused, where pt-v19 before charter bar B4 reached it on
    /// 0 of 7,560.
    ///
    /// At nonzero this dial sets `a = m` and `c = T`, so `E[z^2 A^2]` is
    /// CONSTANT in the regime, the amplified factor block is linear in
    /// `v_f` exactly as the unamplified one is, and the map cannot cross
    /// the diagonal however far the factor variance excurses. The runaway
    /// b4read1 decomposed onto the draw stream (14 of 120 runs with the
    /// roster held, 0 of 120 with the stream held) is removed at its
    /// mechanism rather than made rarer.
    ///
    /// # What it costs, which was priced before it was needed
    ///
    /// The alternative was built and measured on 2026-08-22, on a preset
    /// whose VIX could not see the amplifier: 0.03 of volatility
    /// clustering, 0.10 of excess kurtosis and 0.006 of correlation, for
    /// "only the constancy of the firing rate". The magnitudes are
    /// re-measured on pt-v19 in `CHANGELOG.md`; the APPRAISAL is what has
    /// changed, because constancy now buys the loop's stability, which was
    /// not on the ledger when the trade was first priced.
    ///
    /// And the realism the baseline normaliser was keeping is real and is
    /// given up knowingly: with a constant normaliser the amplifier turns
    /// a variance regime into a correlation regime, which is what crises
    /// do. What replaces it is the rest of the crisis apparatus — the
    /// blend, `sector_vix_coupling`, the jump coupling — none of which has
    /// an unbounded loop gain through the read-back.
    pub crash_amplifier_conditional_sigma: f64,
    /// WHICH VIX the market factor's variance target reads: the LEVEL
    /// against a fixed anchor (0.0, every preset before pt-v19) or the
    /// EXCURSION above the level the index's own conditional variance
    /// already implies (nonzero).
    ///
    /// # A switch, and it is one on purpose
    ///
    /// There is no half-excursion. `engine.rs` branches on `== 0.0` and
    /// reads the identity's own read-back on every other value, so the
    /// MAGNITUDE is unused and the two admissible values are the two ends.
    /// It belongs in `atlas.SWITCH_DIALS` beside
    /// [`ModelParams::crash_amplifier_conditional_sigma`],
    /// `cycle_stationary_opening` and `garch_omega_sector_scaled`, and NOT
    /// in the survey's zero-shipped ranges, for the reason all four carry:
    /// a Latin hypercube over `[0, 1]` never draws exactly zero and would
    /// survey the dial permanently ON.
    ///
    /// At 0.0 the engine passes `self.vix_anchor` exactly as it did, so
    /// nothing is computed that was not computed before, no draw moves,
    /// and every preset from pt-v1 to pt-v18 is BIT-IDENTICAL.
    ///
    /// # The double count it removes, which is a RECORDED finding
    ///
    /// `garch-derive-design.md` finding 4. Under
    /// [`ModelParams::vix_level_identity`] the VIX IS the index's own
    /// conditional variance in points, plus a fear excursion. The factor's
    /// target then reads `(VIX / anchor)^2`, which is mostly the factor's
    /// OWN variance coming back to it: §3.3 of that note shows the target
    /// reverts the factor toward `c * s_f` of its own level, so
    /// `market_vol_vix_coupling` — a fear-to-variance channel when the VIX
    /// came from a phase table — became a LOOP-GAIN dial the moment the
    /// identity shipped, doing a job its name does not say.
    ///
    /// The cost is measured, not argued. The loop's static gain is
    /// `theta = sum_k s_k c_k` (`loop-gain-design.md` §2.1) and the factor
    /// term carries about 0.45 of it against 0.11 for the instantaneous
    /// sector, jump and per-name couplings together. A standing bias `L`
    /// in the target moves the level by `1 / (1 - theta)`: **2.7x at the
    /// shipped theta of about 0.62.** Every excursion in the fear channel
    /// is amplified by that factor before it reaches the variance, and the
    /// variance's own excursions are amplified again on the way back.
    ///
    /// # What it changes, in one line
    ///
    /// The ratio's denominator. At 0.0 it is `market_vol_vix_anchor` (or
    /// the derived anchor under the identity), a CONSTANT; at nonzero it is
    /// `vix_from_variance(vix_variance_premium, V_t)` evaluated on the
    /// session's own index variance — the level the identity says this VIX
    /// ought to be. The ratio is then 1.0 whenever the VIX is exactly what
    /// the variance implies, the target is exactly `base`, and what lifts
    /// it is the fear excursion ALONE.
    ///
    /// # Why that is a stability result and not a taste
    ///
    /// At a PINNED VIX `v` the target becomes `base (1 - c + c v^2 / I(V)^2)`
    /// with `I(V)` the read-back, which is DECREASING in the variance where
    /// the old form was constant in it. The variance's fixed point solves an
    /// increasing function against a decreasing one, so it is unique and
    /// globally attracting at every pin.
    ///
    /// Free-running, the VIX is `I + E` for a fear excursion `E` that
    /// `vix_return_clamp` bounds. The ratio is `(1 + E / I)`, and `I` grows
    /// with the variance while `E` does not — so the regime ratio DECAYS as
    /// the variance excurses and the target falls back to `base`. The
    /// runaway `b4fix1` left behind (5 of 120 rosters, 25 seed-days, after
    /// the amplifier's superlinearity was removed) is a day-to-day
    /// excursion amplified 2.7x around a map that already contracts; this
    /// takes the amplification to about 1.1x, and it does so by removing
    /// the term rather than by making the excursions rarer.
    ///
    /// # What it does NOT touch, deliberately
    ///
    /// `theta_i`, the instantaneous couplings — `sector_vix_coupling`,
    /// `jump_vix_coupling`, `garch_vix_coupling`. Those read the VIX the
    /// same day it prints and close the loop through the tick rather than
    /// through the variance target; §3.4's option C names the variance
    /// arm and only the variance arm. They are about 0.11 of theta and
    /// they stay.
    ///
    /// Nor does it touch the four dials in the fear channel that
    /// `flattens_at` reads, so charter bar B4 is a property of
    /// `vix_return_clamp`, `vix_return_gain`, `vix_return_exponent` and
    /// `vix_target_shock_cap` alone and is untouched by this.
    ///
    /// # It is only meaningful under the identity
    ///
    /// Off `vix_level_identity` there is no read-back to be an excursion
    /// above, and `vix_implied_from_market` is either zero or the FACTOR's
    /// sigma on a different scale. `the_excursion_switch_requires_the_
    /// identity` asserts no shipped preset sets one without the other.
    pub market_vol_vix_excursion: f64,
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
    ///
    /// # DERIVED TO ZERO for pt-v19, 2026-09-12
    ///
    /// The tape's VIX has no crisis attractor and its correlation is a
    /// function of realised common volatility, which this model's factor
    /// share already reproduces with no lift; a lift keyed on the VIX
    /// level gives the map a second stable fixed point the tape refutes.
    /// See `pt_v19` and programme/crisis-blend-derivation.md. The account
    /// below is the value's history and stands for pt-v13 to pt-v18.
    ///
    /// # It was UN-DERIVED at 0.8275881 from pt-v13 to pt-v18
    ///
    /// The value came off pt-v13's search, against a VIX read-back that
    /// could not see the blend at all. `market::index_var` sees it now, so
    /// the dial has a loop to answer to and a stability condition to be
    /// derived from: `implied(v) < v` for every `v` above
    /// `crisis_vix_threshold`, which binds at `vix_ceiling` and is a
    /// quadratic in this gain with a closed-form root. That root is 0.1496
    /// on seeds 101 to 103.
    ///
    /// **It is not moved to that root, because the root does not settle
    /// what it was supposed to settle.** At a gain of exactly 0.0 the VIX
    /// still reaches `vix_ceiling` on 81 of 7,560 seed-days over the
    /// certification protocol's thirty rosters, where pt-v19 before B4
    /// reached it on 0 of 7,560. The runaway is the crash amplifier's
    /// second moment against the factor variance's own ordinary dispersion,
    /// and not the blend's; this dial can make that worse and cannot make
    /// it well. See `market::index_var`'s module
    /// documentation for the measurement and for the three routes that
    /// would settle it, none of them a dial.
    ///
    /// A gain near 0.05 DOES pass the certified bands -- 18 of 18 at both
    /// horizons, the down-tail row at 1.9522 of a 1.9600 ceiling at 252 and
    /// 1.7561 at 504 -- and is still not shipped. It is a grid search on one
    /// row, which is what bar B3 exists to end; that row is a `pooled_rate`
    /// with no seed scale, so the value cannot carry the error bar a
    /// `measured` dial owes; and the loop is no better at it, with the
    /// ceiling reached on 93 of 7,560 seed-days against 81 at a gain of
    /// zero. Passing a band by switching off the mechanism the dial exists
    /// for is not the same thing as being right.
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
    /// 0.0 -- pt-v1 through pt-v9 -- is bit-identical by branch. It is no
    /// longer the shipped value, where this line said "Shipped 0.0" until
    /// 2026-09-17: pt-v10 to pt-v12 carry 0.3, pt-v13 0.0269 and pt-v14
    /// onward 0.14219611. What follows is the variance model at zero, which
    /// is where it stood when this dial arrived and the last piece of it
    /// that did not know what regime it was in. The per-name
    /// GJR-GARCH reads no macro state at all: its clamps are multiples of a
    /// static per-sector variance, and its own unconditional level sits
    /// below the floor those clamps impose (5.6% annualised against a floor
    /// of 19.8% for technology), so a name's variance hovers near that floor
    /// whatever the market is doing. The 5.6% is the reading at the
    /// persistence of 0.8364 this line was written at; pt-v19's 0.9416 puts
    /// the same level at 9.3% annualised, which is still under technology's
    /// floor and over the 6.4% floor the three lowest-sigma sectors carry. That is why `garch_ceiling_multiple`
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
    /// market_vol_vix_anchor)^e)`, with `e` the exponent
    /// [`ModelParams::garch_vix_exponent`] carries, the same map the market
    /// factor's target uses, so at the anchor the reference is exactly the
    /// base at any coupling and the two variance processes read the regime
    /// the same way.
    pub garch_vix_coupling: f64,
    /// Exponent on the VIX ratio in a NAME's variance reference.
    ///
    /// 2.0 -- every preset before this dial -- is the literal square the
    /// line in `market/daily.rs` has always computed, read BY BRANCH, so
    /// `mathx::pow` never runs and every preset is bit-identical. Same
    /// spelling and same discipline as
    /// [`crate::market::factor_vol`]'s `vix_response` for the market
    /// factor's own target, which is what makes the two shapes separable:
    /// the index and the roster can be given different laws instead of
    /// sharing one because nobody wrote the second one down.
    ///
    /// # What the shipped pair reaches, and what it does not
    ///
    /// `garch_vix_coupling` is NOT inert on the shipped vector, and the
    /// reading that says it is stops one line short.
    /// [`crate::market::garch::update_garch_variance_for`] takes the
    /// constant `garch_omega` while `garch_omega_sector_scaled` is 0.0, so
    /// the coupled reference does not reach the process's LEVEL -- but both
    /// clamps are multiples of that same reference. The recursion's own
    /// constant level, `garch_omega / (1 - persistence)`, is 3.42e-5 at
    /// pt-v19's persistence of 0.9416: under the floor for eight of the
    /// twelve sectors and at most 2.2x it for the other four, so what
    /// places a name's variance is the reference and the shock rather than
    /// the constant. Measured on the held roster (`Universe.random(40, seed=111)`,
    /// the VIX pinned at 5 against 65, medians over 60 graded days after
    /// 252): the reference moves 2.28x, a name's GARCH variance 5.41x, and
    /// the variance the tick actually draws with,
    /// `max(variance, idio_sigma_floor)`, 3.57x. At coupling 0.0 the same
    /// three read 1.00x, 4.61x and 3.16x. So the coupling buys 1.17x of the
    /// 5.41x and the rest arrives by another road.
    ///
    /// That road is the SHOCK, and it is why this dial is the one the
    /// roster's law can be written on. `close_day_with` feeds the recursion
    /// the day's `random_noise` attribution, and
    /// [`crate::market::factors::calculate_live_factors`] builds it as
    /// `market_component * crash_amplifier + tilt_recentre +
    /// sector_component + idiosyncratic_noise`. A name's variance is
    /// therefore re-excited by the market factor's own VIX-coupled variance
    /// at a gain of `(alpha + gamma / 2) / (1 - beta)`, which is 0.72 on
    /// pt-v19. What a name's variance does in a crisis is what the FACTOR
    /// does, attenuated and floored; nothing in it is a statement about the
    /// roster.
    ///
    /// # The value the roster's own scaling law derives
    ///
    /// The tape's names scale like its index: realised volatility ~
    /// `VIX^0.7088` across the roster, which is the 6.16x lever the record
    /// grades against a 13x of VIX. A variance is a volatility squared, so
    /// a name's variance reference must be proportional to `VIX^(2 *
    /// 0.7088)` = `VIX^1.4176`.
    ///
    /// A blend `1 - c + c r^e` is a power law only at `c = 1`; below it the
    /// `1 - c` term is a floor the low end never leaves, and no exponent
    /// makes such a map a law. So the law is a PAIR and not a number:
    /// `garch_vix_coupling` at 1.0 and this dial at 1.4176, where the
    /// reference moves `13^1.4176` = 37.9x for the 13x of VIX the lever row
    /// reads. Neither figure is fitted to that row; both come off the
    /// tape's exponent.
    ///
    /// The pair is not adopted here, and 2.0 ships. See
    /// `provenance.py`'s entry for what would register it.
    pub garch_vix_exponent: f64,
    /// Floor as a multiple of the sector's long-run variance.
    pub garch_floor_multiple: f64,
    /// Scale `garch_omega` by the SECTOR's base variance instead of using
    /// one constant for every sector. 0.0 is the shipped constant, read by
    /// branch, and every preset before this dial is bit-identical.
    ///
    /// The identity, which is the cascade path's arithmetic verbatim
    /// ([`crate::market::garch::update_garch_cascade`]):
    ///
    /// ```text
    /// omega_i = sector_base_variance * (1 - alpha - beta_i - gamma/2)
    /// ```
    ///
    /// which is exactly the omega that makes the recursion's unconditional
    /// variance `omega / (1 - persistence)` EQUAL the sector's own base
    /// variance. No number is chosen: every term is already a parameter or
    /// the sector table's own `daily_sigma` squared.
    ///
    /// Why it is needed. At the shipped constant of 2e-6 the recursion's
    /// unconditional level is about 1.2e-5 for EVERY sector, against clamp
    /// floors running 1.6e-5 to 1.56e-4, so the level is global while the
    /// band around it is per sector. The sector table's 3.1x spread in
    /// `daily_sigma` then reaches the price as roughly 1.4x, and per-name
    /// volatility carries no sector ordering at all against a real corpus
    /// ordered from staples 17.0 per cent to technology 29.4.
    pub garch_omega_sector_scaled: f64,
    /// Feed the per-name GJR the name's OWN noise, in the units the
    /// coefficients were fitted in, instead of the whole `random_noise`
    /// column. 0.0 is the shipped column read BY BRANCH, so every preset
    /// before this dial is bit-identical; 1.0 is the whole form.
    ///
    /// # The defect
    ///
    /// `engine.rs` hands `close_day_with` the day's `random_noise`
    /// attribution as the innovation, and
    /// [`crate::market::factors::calculate_live_factors`] builds that
    /// column as `market_component * crash_amplifier + tilt_recentre +
    /// sector_component + idiosyncratic_noise`. Three of the four terms
    /// are not the name's variance at all. Measured on the held roster
    /// (`Universe.random(40, seed=111)`, 504 days, eight seeds, medians
    /// over 320 names), in multiples of the `h` the recursion carries: the
    /// whole column's mean square is 1.387 h, of which the market's and
    /// the sector's draws together are 0.810 h and the name's own is only
    /// `kappa^2` = 0.473 h.
    ///
    /// `kappa^2` is under one because the tick draws the name's own noise
    /// as `sqrt(max(h, idio_sigma_floor)) * idio_sigma_scale * cap_mult *
    /// volatility_multiplier / sqrt(390)` per tick, so the session's sum
    /// carries that factor squared and not `h`. Two consequences, both
    /// measured:
    ///
    /// - the shock coefficient acts on the name's own variance at
    ///   `(alpha + gamma P(neg)) kappa^2` = 0.0728 rather than the 0.1511
    ///   the dials say, so the process's self-persistence is 0.863 where
    ///   the dial vector reads 0.9416 and the tape's names read 0.938. The
    ///   fitted GJR persistence of the model's names is 0.894, between the
    ///   two, because the rest arrives from outside;
    /// - the name is re-excited by the MARKET's own noise at a gain of
    ///   0.72, so a name's variance memory is partly the factor's memory
    ///   wearing the name's label.
    ///
    /// # The form
    ///
    /// The close divides the name's own noise by the scale the tick drew
    /// it with. `kappa^2` is accumulated in the engine the same way the
    /// attribution is, as the day's sum of
    /// `(idio_scale / sqrt(390) * cap_mult * volatility_multiplier *
    /// suppress * tick_scale)^2`, so it is the scale that ACTUALLY ran and
    /// not a reconstruction of it, and the innovation is
    ///
    /// ```text
    /// eps = noise_idio_sum / sqrt(kappa2)
    /// ```
    ///
    /// whose mean square is `max(h, idio_sigma_floor)`: the units the
    /// coefficients were fitted in. The shock then acts at full weight and
    /// the market's noise no longer reaches the name's variance as a
    /// shock at all. Between 0.0 and 1.0 the two innovations are blended
    /// linearly; at 1.0 the commensurate one is taken exactly, because
    /// `(1 - w) a + w b` at `w = 1` is not bit-equal to `b`.
    ///
    /// # It travels with the sector-scaled omega
    ///
    /// Taking the common noise out of the innovation takes the level with
    /// it: the constant `garch_omega` gives an unconditional variance of
    /// 3.42e-5, under the clamp floor for eight of the twelve sectors, so
    /// a name that is no longer re-excited by the market simply rests on
    /// the floor. The companion is therefore
    /// [`ModelParams::garch_omega_sector_scaled`] at 1.0, which makes the
    /// recursion's own level the sector's base variance times the coupled
    /// reference, with [`ModelParams::garch_vix_coupling`] 1.0 and
    /// [`ModelParams::garch_vix_exponent`] 1.4176 -- the roster's own
    /// scaling law, `volatility ~ VIX^0.7088`. With the level following the
    /// reference, the regime reaches a name where it belongs, as a LEVEL,
    /// and the shock is the name's own.
    ///
    /// [`ModelParams::idio_sigma_floor`] is the one constant this form
    /// cannot put in the right units. It ships at 1e-4 against a median
    /// name variance of 8.9e-5, so on 59 per cent of name-days the draw
    /// the innovation is divided by was taken at the floor and not at `h`,
    /// and `eps^2` then reads the floor: on the shipped level the fed
    /// innovation would be 1.28 h rather than the 1.00 h the form is
    /// after. The companion repairs most of it by lifting the level --
    /// with the sector-scaled omega the median variance is 1.29e-4, the
    /// floor binds on 31 per cent of name-days, and the fed innovation
    /// reads 1.09 h. The floor is left at its shipped value here: moving
    /// it is a separate box, and the residual 0.09 is part of why the form
    /// lands short of the dialled 0.9416.
    ///
    /// # Measured
    ///
    /// Eight seeds of the held roster, 504 days, medians over the 320
    /// per-name GJR fits. Shipped against this dial at 1.0 with
    /// `garch_omega_sector_scaled` 1.0, `garch_vix_coupling` 1.0 and
    /// `garch_vix_exponent` 1.4176:
    ///
    /// ```text
    ///                        shipped    form    tape
    /// fitted persistence      0.8943  0.9259   0.938
    /// fitted alpha            0.0062  0.0062
    /// fitted gamma            0.0667  0.0433
    /// log h AR(1)             0.8770  0.9166
    /// log h AR(5)             0.5093  0.6435
    /// fed innovation over h    1.281   1.091
    /// ```
    ///
    /// This dial ALONE, without the sector-scaled omega, is the reading
    /// that says why the two travel together: the median variance falls to
    /// 8.19e-5, the floor binds on 64 per cent of name-days, and the log
    /// AR(1) reads 0.8673 -- below the shipped 0.8770. Taking the common
    /// noise out of the innovation takes the level with it, and the name
    /// comes to rest on the clamp floor.
    pub garch_innovation_commensurate: f64,
    /// The absolute floor under a name's daily sigma in the tick and in the
    /// overnight path, in DAILY VARIANCE units. Ships at 1e-4, which is the
    /// constant both sites carried inline, so every existing preset is
    /// bit-identical; 0.0 removes it.
    ///
    /// It was written as a dead-stock guard -- a zero variance would freeze
    /// a price -- and against a recursion whose own level is about 6.3e-5
    /// it is instead the binding constraint on 61 to 90 per cent of
    /// name-days in nine of the twelve sectors. The guard's job is already
    /// done, and done PER SECTOR rather than absolutely, by the
    /// `[0.25, 5.0] x sector_base_variance` clamp in
    /// [`crate::market::garch::update_garch_variance_for`], which cannot
    /// return zero for a positive base. A preset that sets this to 0.0
    /// therefore has one fewer constant in it, not one more.
    pub idio_sigma_floor: f64,

    // ── Market-factor variance process (market/factor_vol.rs) ───────────
    /// The market factor's own GARCH reaction term: how sharply
    /// market-wide variance responds to the last market-wide shock.
    /// This is the process that gives every name a common volatility
    /// regime; a name's own GJR-GARCH is idiosyncratic on top of it.
    ///
    /// # THE DISPERSION IS HEAVY, FINITE, AND TAPE-LIKE — AND IT IS NOW
    /// LOAD-BEARING
    ///
    /// `alpha + beta + gamma / 2 < 1` makes the process revert to its target
    /// in expectation; 0.28035 and 0.69245 -- pt-v14 through pt-v18, which
    /// this line attributed to pt-v13 until 2026-09-17 -- give 0.9728 and
    /// pass it.
    /// The fourth-moment condition `3 alpha^2 + 2 alpha beta + beta^2 < 1`
    /// reads **1.1035** for this FAST COMPONENT ALONE and fails — but the
    /// shipped process is a 0.65/0.35 mixture with `market_vol_slow_weight`,
    /// its own condition is the spectral radius of a 4x4 matrix, and that
    /// reads **0.9870**. The shipped factor variance has a finite fourth
    /// moment. Both figures are the design repository's
    /// `garch-derive-design.md`, which derived them before this note, and
    /// both are computed on the pt-v16/pt-v18 triple. Until 2026-09-17 this
    /// entry added that every dial they depend on is identical from pt-v16
    /// to pt-v19, and it is not: pt-v19 moves all three -- alpha 0.28035004
    /// to 0.0066, beta 0.69244622 to 0.8946, gamma 0.0 to 0.1556 -- and
    /// `market_vol_slow_persistence` with them, 0.98 to 0.9913. The GJR
    /// fourth-moment coefficient at pt-v19's triple reads 0.9908, recorded
    /// under [`ModelParams::market_vol_alpha_excursion`]; the mixture's 4x4
    /// spectral radius has not been recomputed at the pt-v19 vector here.
    ///
    /// What the process has is heavy, finite dispersion: variance-of-variance
    /// 3.4 times its mean squared, implied factor kurtosis 13 against the
    /// tape's own GARCH at 2.8 and 11.3. The per-seed spread is the
    /// finite-sample dispersion of any GARCH at this persistence — a bare
    /// recursion with no engine reproduces it — and the tape's own years
    /// spread as widely. Pinned at a VIX whose target is 0.45 of baseline,
    /// the variance is measured sitting at 11.7 times baseline for eighty
    /// consecutive sessions on one of thirty certification rosters, and that
    /// is ordinary rather than pathological.
    ///
    /// **Which is what makes it load-bearing now.** It was harmless while
    /// the VIX could not see the crash amplifier. Since `market::index_var`
    /// prices it, an excursion of that ordinary size implies a VIX above
    /// `vix_ceiling` and the loop holds it there — so the read-back is
    /// asking these two coefficients to carry a stability property they were
    /// never derived for, and `garch-derive-design.md` has tape-derived
    /// values (0.1059 and 0.8787, with error bars) waiting for the question.
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
    /// existed since pt-v2; the COMMON factor ran symmetric from pt-v1 to
    /// pt-v18, where this line said "forever" until 2026-09-17, and pt-v19
    /// ships 0.1556. Through pt-v18 that is why the model could not
    /// produce correlation asymmetry --
    /// correlations that rise in falling markets are the common factor's
    /// leverage effect, and this model's corr_asymmetry sits at -0.016
    /// against a real +0.015 with nothing measured able to move it
    /// (rounds 93/95). This is the wire for exactly that statistic.
    ///
    /// Applies to the FAST component only: the leverage effect is a
    /// same-week phenomenon, and the slow component carries long-horizon
    /// clustering, not asymmetry.
    pub market_vol_gamma: f64,

    /// How far the common factor's SHOCK SHARE moves with the factor's own
    /// variance excursion. 0.0 is a constant share, which is every preset
    /// before 0.8.0 and is bit-identical to the arithmetic that predates
    /// this dial.
    ///
    /// # What the tape says, and why a constant share cannot say it
    ///
    /// MEASURED on the index, 19,014 sessions 1950-2025
    /// (`programme/results/reactive/`, design repository). Regress
    /// `|r_t|` on `|r_{t-1}|`, the standardised log of trailing realised
    /// volatility over `w` sessions ending at `t-1`, and their product.
    /// The product's coefficient is how much clustering MOVES with the
    /// state, and the tape reads:
    ///
    /// ```text
    ///   w =   5 sessions   +0.0941 +/- 0.0047      (20 error bars)
    ///   w =  21 sessions   +0.0694 +/- 0.0042
    ///   w =  63 sessions   +0.0474 +/- 0.0047
    ///   w = 252 sessions   +0.0187 +/- 0.0059
    /// ```
    ///
    /// Two things follow. The response is REAL and it is FAST: strongest
    /// at a week and decaying as the window lengthens, so it is a
    /// days-to-weeks mechanism rather than a regime. And the tape's
    /// BASELINE clustering at the weekly window is NEGATIVE (-0.058):
    /// almost all of the real market's volatility clustering is
    /// conditional on the last week having been rough, and a constant
    /// shock share has no way to spell that.
    ///
    /// The shipped model has about a fifth of the response
    /// (+0.0158 +/- 0.0064 at w = 5 on the composed vector, +0.0483 on
    /// pt-v18) and the WRONG SIGN at a quarter and a year. This dial is
    /// the mechanism that gap asks for.
    ///
    /// # The form, and why it is a rotation rather than a lift
    ///
    /// `delta = excursion * ln(variance / target)`, then
    /// `alpha_t = alpha + delta` and `beta_t = beta - delta`. The PAIR
    /// moves, not the share alone, so `alpha + beta` is invariant and with
    /// it the persistence, the unconditional level and omega's
    /// mean-reversion. What changes is the split between yesterday's shock
    /// and the carried state, which is what clustering is.
    ///
    /// Lifting alpha alone was the obvious form and the fourth moment
    /// refuses it. The GJR coefficient
    /// `3a^2 + 3ag + 1.5g^2 + 2ab + bg + b^2` reads 0.9908 at the shipped
    /// triple, and raising alpha alone crosses one at 0.0120 -- half a
    /// thousandth of headroom. Rotating gives six times as much (`delta`
    /// to 0.020 at 0.9984), because what the fourth moment objects to is
    /// total persistence and the rotation does not add any. This is the
    /// same one-parameter-fewer argument that put `g = p - 1` on the VIX
    /// response and the ratio form on the sector state.
    ///
    /// `delta` is CLAMPED at the value that holds that coefficient at
    /// 0.999, computed from `beta` and `gamma` rather than written down,
    /// so a preset that moves either cannot silently lose the finite
    /// fourth moment the tape's coefficients bought.
    pub market_vol_alpha_excursion: f64,

    /// How much of last session's slow variance LEVEL carries into this
    /// one. Together with `market_vol_level_sigma` this is a lognormal
    /// AR(1) multiplier on the factor's variance TARGET -- a level that
    /// drifts over months, not a component that reverts over days.
    ///
    /// # Why a level rather than a third component
    ///
    /// DERIVED, `programme/results/cascade-fourth-moment.md` section 4.2
    /// (design repository). Every candidate that buys fat tails by adding
    /// variance-of-variance to the recursion -- a heavier `alpha`, a longer
    /// slow pole, a third component, a random `gamma` -- is a
    /// random-coefficient recursion, and those are exactly the entries of
    /// the fourth-moment operator `T` whose spectral radius has to stay
    /// under one. At the shipped triple `rho(T)` reads 0.9841 and the
    /// campaign has already spent the margin.
    ///
    /// A term that moves only `omega` does not appear in `T` at all.
    /// Scaling the target scales `omega_i = (1 - pers_i) * target` and
    /// leaves every `a_i, b_i, g_i` untouched, so the composed condition
    /// becomes `rho(T) < 1` AND `E[L^2] < infinity`, and the second holds
    /// for every stationary lognormal at every `phi < 1` and every `sigma`.
    /// The two conditions SEPARATE. That is the whole argument for this
    /// form: it is the only one whose tail does not come out of the moment
    /// condition's budget.
    ///
    /// # The value
    ///
    /// DERIVED from two MEASURED tape statistics, section 4.3: the
    /// window log-variance dispersion of ^GSPC that the model does not
    /// account for, and its window-to-window autocorrelation, solved
    /// through the closed-form autocorrelation of the session means of an
    /// AR(1). **0.9977 [0.9945, 0.9992]**, a half-life of 295 sessions
    /// [126, 866]. The two 252-session tape windows (1990-2025 and
    /// 1950-2026) agree at 0.6 of their own error.
    ///
    /// NOT derived: the estimator. The 504-window autocorrelation is the
    /// weakest of the four inputs and it is what drags the 504 solve down
    /// to 0.995. If this is adopted, `phi` should be re-derived from the
    /// log realised-variance autocorrelation over a RANGE of lags rather
    /// than from a single window lag.
    ///
    /// 0.0 -- pt-v1 through pt-v18 -- is, with `market_vol_level_sigma`
    /// 0.0, a multiplier of exactly 1.0 and bit-identical. It stopped being
    /// the shipped value at pt-v19, which carries the 0.9977 derived above
    /// beside a sigma of 0.085; this paragraph read "Ships at 0.0" until
    /// 2026-09-17.
    pub market_vol_level_persistence: f64,

    /// The per-session innovation of the slow variance level, in log
    /// units. 0.0 -- pt-v1 through pt-v18 -- pins the level at
    /// exactly 1.0 and leaves the target arithmetic that predates this
    /// dial untouched, to the bit. pt-v19 ships 0.0 again since the 2026-09-20
    /// recomposition, having carried 0.085 from 2026-09-14; this line said
    /// "every preset through pt-v19" until 2026-09-17.
    ///
    /// **0.047 [0.035, 0.064]** DERIVED, section 4.3, equivalently a
    /// stationary `sd(log L)` of **0.69 [0.56, 0.87]**: the dispersion of
    /// window log-variance the tape has and the model does not,
    /// `sqrt(tape^2 - model^2)`, carried through the AR(1) shrinkage
    /// factor at the persistence above. The bar is the propagated
    /// jackknife error on the two tape sds. That is not the shipped number
    /// and `provenance.py` records why: 4.3 set the LEVEL's window-mean
    /// dispersion equal to the INDEX's deficit, and the level drives the
    /// factor, which is about half the index, so 0.047 is low by about a
    /// factor of two. The entry records this dial `measured` at pt-v19's
    /// 0.085, read off the engine's own output rather than solved
    /// (level-phi.md section 6), and the arm at 0.085 puts sd(log var)
    /// inside the tape's band at both horizons.
    ///
    /// # Three things it costs
    ///
    /// 1. **The level would fall if `E[L]` were normalised.** At the
    ///    shipped 0.085, with `market_vol_level_persistence` 0.9977, the
    ///    stationary `sd(log L)` is `0.085 / sqrt(1 - 0.9977^2)` =
    ///    **1.254**; so with `E[L] = 1` the mean root `E[sqrt(L)]` is
    ///    `exp(-sd^2/8)` = 0.822 and median annualised volatility would
    ///    drop EIGHTEEN per cent -- and `annualised_vol_pct` is a graded
    ///    row. The derived 0.047 gives `sd(log L)` 0.69, `E[sqrt(L)]`
    ///    0.942 and six per cent instead, and this paragraph quoted only
    ///    that second triple until 2026-09-18 -- a derived cost standing
    ///    where the shipped one belongs, and the only self-refuting number
    ///    in this entry: 0.69 solves back to a sigma of 0.047, not to the
    ///    0.085 four paragraphs above it. So the normalisation here is
    ///    `E[sqrt(L)] = 1`, which subtracts `sd^2/4` from the log level. A
    ///    constant of the construction, not a free dial, and it is applied
    ///    at whatever sigma the preset carries.
    /// 2. **A draw.** One normal per session, on
    ///    [`crate::rng::stream::MARKET_VOL_LEVEL`] and not on `MARKET`, so
    ///    the schedule does not move and nothing else reshuffles. See that
    ///    constant for why the isolation is what makes the zero arm a
    ///    control rather than a different random world.
    /// 3. **The VIX loop will amplify it, by an unmeasured factor.**
    ///    `market_vol_vix_excursion` is 1.0 on pt-v19, so the target reads
    ///    the VIX's excursion from the index's own implied level and a
    ///    slow level moves that level. DERIVED as a bound: the standalone
    ///    factor's window dispersion is 0.249 and the engine's index reads
    ///    0.225, so the loop plus the 45 per cent non-factor share
    ///    currently transmits at about 0.9. If that ratio holds the value
    ///    above is right to within its own bar; if the loop amplifies, it
    ///    is high. This is the single largest reason to read 4.3 as the
    ///    starting point of a box rather than as a finished calibration.
    ///
    /// # What it is predicted to buy, registered before the box
    ///
    /// `index_tail_dn3_pct` at 252 from 0.608 to **0.86-1.03** against a
    /// tape centre of 1.213; `excess_kurtosis` UNMOVED, because a
    /// per-window level shift multiplies every name equally and the pooled
    /// standardised statistic is invariant to it by construction.
    /// FALSIFIER: if the tail row does not reach 0.80 at the derived pair,
    /// the mechanism is wrong rather than under-dialled.
    pub market_vol_level_sigma: f64,

    /// The persistence of the VIX's own slow log-level: a lognormal AR(1)
    /// multiplier on what the VIX prices under the identity, riding the
    /// normal `stream::MARKET_VOL_LEVEL` already draws every close.
    ///
    /// # Why it exists
    ///
    /// The real VIX's persistence rises from one-year to two-year windows
    /// (`facts.REAL_VIX_AR1_RISE`): two thirds of its variance is a slow
    /// regime level with a half-life near two hundred sessions, and a
    /// single-pole process cannot show that on the gate's estimator. The
    /// stochastic level on the FACTOR's variance target was the first
    /// attempt and was returned to 0.0 on 2026-09-20 because a multiplier
    /// on the variance process moves every row that reads it. This one
    /// multiplies the VIX target alone -- `vix_implied_from_market`, at the
    /// one line the engine wires it -- so the factor's variance and the
    /// fourteen shape rows it drives are untouched at first order, and the
    /// VIX loop sees the level only through the coupling every preset
    /// already carries.
    ///
    /// # Derivation (design repo, `vix-level-derivation.txt`, 2026-09-21)
    ///
    /// A two-pole fit to the ACF of log VIX over 1990-2025 at lags 1 to 504
    /// reads a fast pole of 0.942 carrying 21 per cent of the variance and
    /// a SLOW pole of 0.9965 (half-life 198 sessions) carrying 79 per cent:
    /// stationary sd 0.306 in logs, innovation 0.0256 per session. Those
    /// are the derived values; they ship at 0.0 until the registered arm
    /// has measured what they do to every row.
    ///
    /// 0.0 -- every preset -- is, with `vix_level_sigma` 0.0, a multiplier
    /// of exactly 1.0 and bit-identical. Read only while `vix_level_sigma`
    /// is non-zero.
    pub vix_level_persistence: f64,

    /// The per-session innovation of the VIX's own slow log-level. 0.0 on
    /// every preset: the log-level stays exactly 0.0, the multiplier is
    /// exactly 1.0 by a branch and not by arithmetic, and no draw is
    /// added or moved -- the normal it would consume is the one the
    /// factor level already takes unconditionally on its own stream, so a
    /// preset carrying both levels drives them with one draw and says so.
    /// Derived 0.0256 at persistence 0.9965; see `vix_level_persistence`.
    /// Read only under `vix_level_identity`, where the VIX target is what
    /// the index's own conditional variance implies; off the identity the
    /// target is the phase table and this multiplier is not applied.
    pub vix_level_sigma: f64,

    /// The loop's own transmission of the VIX's slow level into the VIX,
    /// which the level's innovation is DIVIDED by. 0.0 -- every preset --
    /// is the branch not taken: the recursion and the multiplier read
    /// `vix_level_sigma` itself, the same f64, and every preset reproduces
    /// bit for bit.
    ///
    /// # The defect
    ///
    /// `vix_level_sigma` is the spread of the tape's yearly medians of log
    /// VIX written onto the LATENT multiplier, and the two are not the same
    /// quantity. Under `vix_level_identity` the VIX's target is the
    /// read-back of the index's own conditional variance times this level,
    /// and the read-back answers the VIX back: a level a little higher
    /// raises the factor's variance target, which raises the read-back,
    /// which raises the VIX again. So the spread the model's VIX shows is
    /// the level's spread times the loop's gain, and the derivation put a
    /// spread measured on the OUTPUT onto the INPUT. It is why every gain
    /// in the variance loop lengthens the VIX's memory: at
    /// `market_vol_vix_exponent` 4.9 the same level arrives at the VIX half
    /// again as large, the slow share of the VIX's variance grows with it,
    /// and `vix_ar1_debiased` rises at both horizons.
    ///
    /// # The form, and why it is one number
    ///
    /// Hold the VIX and the excursion target's fixed point makes the
    /// read-back a power of it, `I = A * VIX^h`, which is what the held-VIX
    /// probe measures. Let the loop run and the level multiplies:
    /// `VIX = I(VIX) * L`, so `log VIX = h log VIX + log A + log L` and
    ///
    /// ```text
    /// d log VIX / d log L = 1 / (1 - h)
    /// ```
    ///
    /// That is this dial. Dividing the level's innovation by it leaves the
    /// VIX's own log-level spread at the spread the tape's yearly medians
    /// carry, whatever the loop's gain is, so the exponent can be moved for
    /// the crisis lever without the VIX's memory moving with it. It divides
    /// the STATIONARY opening too, not only the innovation, because the two
    /// are one dispersion and the opening draw is the level's own
    /// stationary distribution.
    ///
    /// # Derivation
    ///
    /// `h` is read off the held-VIX pair the lever probe already runs, VIX
    /// 5 against VIX 65: the read-back `vix_implied_from_market` rises
    /// 3.00x at the shipped exponent 2.0 and 4.60x at 4.9, over a VIX that
    /// rises 13x, so `h` is `ln 3.00 / ln 13` = 0.428 and
    /// `ln 4.60 / ln 13` = 0.595. The gain is then 1.75 at the shipped
    /// exponent and 2.47 at 4.9. Both are DERIVED in the sense that matters
    /// here: nothing was fitted to a graded row, each is a ratio of two
    /// readings the lever probe takes anyway, and the form above is the
    /// loop's own algebra.
    ///
    /// A gain must be strictly positive: it divides a dispersion, and the
    /// loop's is `1 / (1 - h)` with `h` in `[0, 1)`, so it is never below
    /// one. `ModelParams::invariants` refuses a non-positive value and
    /// refuses the dial while `vix_level_sigma` is 0.0, where there is no
    /// level for it to correct.
    ///
    /// # What it moves, MEASURED on 16 seeds of the held roster
    ///
    /// `vix_ar1_debiased` reads 0.9523 at 252 and 0.9637 at 504 on the
    /// shipped vector. At `market_vol_vix_exponent` 4.9, which is what the
    /// crisis lever asks for, it reads 0.9581 and 0.9752, and the 504
    /// reading is refused. With the gain divided out at 4.9 it reads 0.9492
    /// and 0.9592, under the shipped vector at both horizons, with a rise
    /// of +0.0100 against the shipped vector's +0.0114, and the lever is
    /// untouched: the read-back still rises 4.60x and realised index
    /// volatility 4.85x for a VIX of 5 against 65.
    ///
    /// That the LEVEL is where the exponent's whole cost sits is the claim
    /// the form makes, and it is measurable on its own. With
    /// `vix_level_sigma` at 0.0 the same exponent change reads 0.9451 and
    /// 0.9581 at 2.0 against 0.9413 and 0.9569 at 4.9: with no regime level
    /// in the loop, the loop's own gain does not lengthen the VIX's lag-one
    /// memory at all.
    pub vix_level_loop_gain: f64,

    /// The VIX's own slow reversion toward the identity's anchor, per
    /// session. 0.0 -- every preset through pt-v19 -- is the branch not
    /// taken: the step is the same three-term sum it always was, no
    /// arithmetic runs on this path and every preset is BIT-IDENTICAL.
    ///
    /// # The defect
    ///
    /// Under [`ModelParams::vix_level_identity`] the VIX reverts to the
    /// read-back of the index's own conditional variance, and the variance
    /// reverts to a target that reads the VIX. There is no third thing.
    /// The VIX reverts to the variance, the variance reverts to the VIX,
    /// and nothing in the pair reverts to a LEVEL: the loop's only anchor
    /// is the fact that its static gain is under one, so the closer that
    /// gain is to one the longer the pair wanders and the larger every
    /// standing bias becomes at the level. On the tape the VIX does have a
    /// third thing -- it sits near a regime mean over months -- and the
    /// model has been supplying that from outside, as a driven regime level
    /// (`vix_level_sigma`), because the loop itself could not.
    ///
    /// That is why the crisis lever and the VIX's memory have been traded
    /// against one another at every composition. The excursion form
    /// (`market_vol_vix_excursion` 1.0) buys stability by making the
    /// factor's target FALL as the read-back rises, which is the same knob
    /// that caps the held-VIX response: at `market_vol_vix_exponent` e the
    /// held-VIX fixed point is `v ~ VIX^(e / (1 + e/2))`, so 4.9 gives
    /// 1.42 where the tape's index variance rises as VIX^1.83, and 1.83
    /// would need e = 21.5. The valve that damps the free loop is the valve
    /// that shuts the lever.
    ///
    /// # The form
    ///
    /// The VIX step in `economy/daily.rs` is
    /// `x + vix_mean_reversion (target - x) + noise + jumps`. This dial
    /// adds one term:
    ///
    /// ```text
    /// + vix_anchor_reversion * (L * anchor - x)
    /// ```
    ///
    /// `anchor` is the derived identity anchor the engine already carries
    /// (`Engine::vix_anchor`, the VIX the identity gives at the
    /// unconditional point) and `L` is the slow regime level's multiplier
    /// (`Engine::vix_level_multiplier`, exactly 1.0 with
    /// `vix_level_sigma` at 0.0). So the VIX reverts to the read-back at
    /// `vix_mean_reversion` AND to its regime level at this rate, and the
    /// pair has an anchor that is not itself a function of the VIX. With
    /// the anchor in place the variance target can read the VIX's LEVEL
    /// with the tape's own exponent (`market_vol_vix_excursion` 0.0,
    /// `market_vol_vix_exponent` 1.83) without the loop becoming a random
    /// walk, which is what the pre-excursion form was.
    ///
    /// # Derivation
    ///
    /// Linearise the closed loop in logs about its fixed point, writing
    /// `f = log(v / base)` and `y = log(VIX / (L anchor))`. The factor
    /// mixture reverts to a target `p y` at rate `1 - rho`, and the VIX
    /// reverts to `f / 2` at `mr` and to `0` at `kappa`:
    ///
    /// ```text
    /// f' = rho f + (1 - rho) p y
    /// y' = (mr / 2) f + (1 - mr - kappa) y
    /// ```
    ///
    /// which is the two-state system whose static gain is
    /// `theta = p w / 2` at `w = mr / (mr + kappa)` and whose poles are the
    /// eigenvalues of `[[rho, (1 - rho) p], [mr / 2, 1 - mr - kappa]]`:
    ///
    /// ```text
    /// lambda = (tr +- sqrt(tr^2 - 4 det)) / 2
    /// tr  = rho + 1 - mr - kappa
    /// det = rho (1 - mr - kappa) - (1 - rho) p mr / 2
    /// ```
    ///
    /// `rho` is the factor mixture's effective persistence, the weight
    /// average of the two components' own poles: the fast component's GJR
    /// triple gives `alpha + beta + gamma / 2` = 0.979 and the slow
    /// component's `market_vol_slow_persistence` is 0.9913 at
    /// `market_vol_slow_weight` 0.35, so
    /// `0.65 * 0.979 + 0.35 * 0.9913` = 0.98331. The weight average is the
    /// right combination here because both components are fed by the SAME
    /// innovation and both revert toward the same VIX-coupled target
    /// (`factor_vol.rs`), so the mixture's response to a step in that
    /// target is the weighted sum of two exponentials and 0.98331 is its
    /// one-pole stand-in.
    ///
    /// The characteristic equation is LINEAR in `kappa`, so a target pole
    /// inverts in closed form:
    ///
    /// ```text
    /// kappa = [ -l^2 + l (rho + 1 - mr) - rho (1 - mr) + (1 - rho) p mr / 2 ]
    ///         / (l - rho)
    /// ```
    ///
    /// DERIVED 0.047 at `l` = 0.9965 -- the tape's own slow pole of log VIX
    /// (`results/ptv19recomp/vix-level-derivation.txt`, the two-pole fit:
    /// fast 0.942 with 21 per cent of the variance, slow 0.9965 with 79) --
    /// with `p` = 1.83 and `mr` = 0.27. At `kappa` 0 the same algebra reads
    /// the loop's slow pole at 0.9987, slower than the tape's; the dial is
    /// the amount of anchor that brings it back to it. The slow pole then
    /// comes from the LOOP and not from a driven regime level.
    ///
    /// # Invariants
    ///
    /// `kappa` lives in `[0, 1)`: it is a share of the distance to the
    /// anchor taken in one session, and at one or above the step
    /// overshoots the anchor every day. `ModelParams::invariants` refuses
    /// it with `vix_level_identity` at 0.0 the way `vix_level_sigma` is
    /// refused -- off the identity there is no derived anchor for the VIX
    /// to revert to, `derive_vix_anchor` returns the dial
    /// `market_vol_vix_anchor` and the VIX's target is the phase table, so
    /// the reversion would be pulling one scale toward another.
    ///
    /// # What it moves, MEASURED on 16 seeds of the held roster
    ///
    /// On the arm the design note registers -- `market_vol_vix_excursion`
    /// 0.0, `market_vol_vix_exponent` 1.83, this dial 0.046081 -- against
    /// the shipped pt-v19, with the regime level on at its shipped values
    /// (a), on at the gain the same algebra re-derives on this form, 2.3552
    /// (b), and off (c):
    ///
    /// ```text
    ///                          base      (a)      (b)      (c)    tape
    /// held VIX 65 over 5, read-back, lever, correlation
    ///   read-back            4.60x    4.38x    4.37x    4.37x       --
    ///   index vol            4.90x    4.61x    4.60x    4.60x    10.50x
    ///   a name in total      2.93x    2.88x    2.87x    2.91x     4.12x
    ///   its private part     2.19x    2.23x    2.20x    2.22x     2.34x
    ///   pairwise corr at 65  0.458    0.449    0.447    0.447    0.665
    /// the free VIX at 504 sessions
    ///   AR(1), debiased     0.9595   0.9579   0.9568   0.9590   0.93 (within-year)
    ///   log-VIX ACF at 21    0.328    0.527    0.525    0.519     0.80
    ///   log-VIX ACF at 63    0.015    0.113    0.117    0.129     0.63
    ///   seeds at vix_ceiling  0/16     0/16     0/16     0/16       --
    ///   S(252) / S(504)   42.4/31.6 51.3/37.6 48.8/39.9 47.7/36.1   --
    /// ```
    ///
    /// **THE LEVEL LAW DID NOT ARRIVE, AND THE DIAL IS NOT WHY.** The
    /// design derived p = 1.83 from the tape on the assumption that with
    /// the excursion damping removed the target's held-VIX exponent passes
    /// through to the variance's one for one. It does not: the transmission
    /// measured on this form is about 0.8 from the target's own effective
    /// exponent to the factor variance's, and about 0.9 again from there to
    /// the index's, so p = 1.83 buys an index lever of 4.61x where the
    /// design predicted 8x to 11x and where the shipped excursion form at
    /// exponent 4.9 already reads 4.90x. The exponent ladder on THIS form,
    /// with the anchor reversion on, measures the transmission directly:
    /// p = 1.83 gives 4.61x, p = 3.0 gives 7.89x, p = 4.9 gives 11.27x, so
    /// the tape's 10.5x sits near p = 4.5 and not near 1.83. The arm is
    /// registered and refused; the dial ships at 0.0.
    ///
    /// What the dial itself does is what the algebra says. The free VIX's
    /// log ACF at lags 21 and 63 rises from 0.33 / 0.01 to 0.53 / 0.11
    /// toward the tape's 0.80 / 0.63, which is the loop's own slow pole
    /// arriving, and it does so with `vix_ar1_debiased` unmoved: within
    /// 0.001 of the base at 252 and within 0.003 at 504, on the level arm
    /// at the re-derived gain and on the level-off arm alike. No seed's VIX
    /// reaches `vix_ceiling` on any arm. One graded row leaves its band on
    /// the level law and it is the same row on all three level arms:
    /// `index_tail_dn3_pct` 0.398 against 0.64 to 2.34 at both horizons,
    /// where the base reads 0.797 and 0.696 -- a three-sigma index day is
    /// half as likely under a target that reads the VIX's level, because
    /// the level moves slowly and the excursion form's target did not.
    pub vix_anchor_reversion: f64,

    /// The share of the VIX's TARGET taken by the identity's anchor, in
    /// logs. 0.0 -- every preset through pt-v19 -- is the branch not taken:
    /// the target is the read-back exactly and every preset is BIT-IDENTICAL.
    ///
    /// The same job as [`ModelParams::vix_anchor_reversion`] done in the
    /// other place. That dial adds a second reversion RATE beside
    /// `vix_mean_reversion`, so the VIX reverts `mr + kappa` of the way each
    /// session and its lag-one persistence collapses with the loop's fast
    /// pole. This one leaves the rate at `mr` and moves the TARGET instead:
    ///
    /// ```text
    /// target = implied^(1 - a) * (L * anchor)^a
    /// ```
    ///
    /// Linearised, `y' = (1 - mr) y + mr (1 - a) f / 2`. The static gain is
    /// `p (1 - a) / 2`, the same as the rate form's at `1 - a = w`, and the
    /// trace of the loop matrix is `rho + 1 - mr` whatever `a` is. So pinning
    /// the slow pole pins the fast pole too: at the tape's 0.9965 the fast
    /// pole is 0.717 at every exponent, where the rate form's runs from 0.671
    /// down to 0.146. DERIVED in closed form from the characteristic equation,
    /// `1 - a = (rho - l)(1 - mr - l) / ((1 - rho) p mr / 2)`: 0.6099 at
    /// `market_vol_vix_exponent` 4.0. Geometric rather than arithmetic
    /// because on the anchor form the held read-back rises about as
    /// `VIX^(p / 2)`, so an arithmetic blend keeps a tail that grows faster
    /// than the VIX, and the geometric one grows as `VIX^((1 - a) p / 2)`,
    /// under one at the derived weight.
    pub vix_anchor_weight: f64,

    /// How fast the anchor's view of the read-back moves, per session.
    /// 0.0 -- every preset -- is the instantaneous form: the target is
    /// `implied^(1 - a) (L anchor)^a`, today's read-back against the anchor.
    ///
    /// Nonzero, the anchor pulls against a SLOW memory of the read-back's
    /// log deviation instead, `s' = (1 - h) s + h log(implied / L anchor)`,
    /// and the target is `implied * exp(-a s)`. Today's move in the
    /// variance then reaches the VIX in full, which is the same-day fear
    /// response and the range the instantaneous form damps by `1 - a`,
    /// while a deviation that persists is pulled back at weight `a`. At
    /// h = 1 it is the instantaneous form less one session's lag.
    pub vix_anchor_memory: f64,

    /// How many economy steps the macro model compounds a year's GDP and
    /// CPI growth over. 365.0 -- every preset -- is the arithmetic that has
    /// always stood, and the step at 365.0 is the same f64 division.
    ///
    /// # The defect
    ///
    /// The economy steps once per trading SESSION, 252 to a year, and
    /// compounded `rate / 365` each step, so a trading year received
    /// 252/365 of its annual growth: nominal output, and with it every
    /// name's earnings and book (`earnings_nominal_growth`), grew about 1.1
    /// points a year too slowly. Measured on 30 seeds x 21 years of pt-v19:
    /// the index's long-run return 3.83 -> 4.92 per cent with only the two
    /// divisors changed (design repository, results/longrun-drift/). 252.0
    /// puts the compounding on the session clock. The rest of the macro
    /// calendar (30-session months) is not moved by this dial.
    pub macro_compound_days_per_year: f64,

    /// Where the anchor's weight pulls TO, as a log offset below the
    /// identity's derived anchor: the blend (and the memory's reference) use
    /// `L * anchor * exp(-c)`. 0.0 -- every preset -- is the branch not
    /// taken and the reference is `L * anchor` exactly. The forward map's
    /// denominator (`vix_ratio_denominator`) is NOT moved, so a held VIX
    /// drives the same variance it did.
    ///
    /// Why a centre and not the anchor: the anchor is the identity at the
    /// UNCONDITIONAL (mean) variance, a root-mean-square level. The
    /// geometric blend pulls log VIX toward it, so in calm it LIFTS the VIX
    /// above its own read-back by `(L anchor / implied)^a`, 1.19 to 1.26 on
    /// the route-1 cell, where the tape's VIX over realised volatility says
    /// the read-back alone is already right. A probe of
    /// `programme/results/vix-law-levels/` (design repository).
    pub vix_anchor_centre: f64,

    /// The anchor weight as a function of the VIX's level. 0.0 -- every
    /// preset -- is the constant weight `vix_anchor_weight`. Nonzero,
    ///
    /// ```text
    /// 1 - a(x) = (1 - a) * (K / clamp(x, K, r K))^eta
    /// ```
    ///
    /// with `x` the VIX entering the session, `K` the knee
    /// (`L anchor exp(-k)`, [`ModelParams::vix_anchor_weight_level_knee`])
    /// and `r` [`ModelParams::vix_anchor_weight_level_cap`]. At and below the
    /// knee the weight is the dial. The loop's local gain is
    /// `(1 - a(x)) g(x)` with `g` the held read-back's elasticity to the VIX;
    /// measured on the route-1 base `g` crosses one at a held VIX of 18.5 and
    /// rises about in proportion to the VIX from there to about 36, so
    /// `eta = 1` holds the local gain at its knee value over that range and
    /// the fear trap at 30 to 50, where a constant 0.45 leaves the gain at
    /// one, is removed.
    pub vix_anchor_weight_level: f64,

    /// The level, as a multiple of the centre, above which
    /// [`ModelParams::vix_anchor_weight_level`] stops raising the weight:
    /// where the held read-back's elasticity stops rising. 0.0 is no cap.
    pub vix_anchor_weight_level_cap: f64,

    /// Where [`ModelParams::vix_anchor_weight_level`] starts raising the
    /// weight, as a log offset below `L * anchor`. Read only with the level
    /// law on; 0.0 puts the knee at `L * anchor`.
    pub vix_anchor_weight_level_knee: f64,

    /// Nonzero, the level law also runs BELOW the knee, where it lowers the
    /// weight toward zero. 0.0 -- the default -- holds the dial there. Read
    /// only with the level law on.
    pub vix_anchor_weight_level_below: f64,

    /// Nonzero, the level law's knee does not read the slow regime level:
    /// `K = anchor exp(-k)` instead of `K = L anchor exp(-k)`. 0.0 -- every
    /// preset -- is the knee as it was. A switch, 0.0 or 1.0, read only with
    /// the level law on.
    ///
    /// # Why
    ///
    /// The knee is DERIVED from the held-VIX map: it is where the read-back's
    /// elasticity `g(x)` to a held VIX reaches `G* / (1 - a)`, and `g` is a
    /// property of the VIX's absolute level (the index variance's floor and
    /// coupling), which the slow regime level `L`
    /// ([`ModelParams::vix_level_sigma`]) does not move. With the knee on
    /// `L anchor`, a turbulent era (`L > 1`) lifts the knee, leaving the band
    /// from `anchor exp(-k)` to `L anchor exp(-k)` at the constant weight,
    /// where `(1 - a) g(x)` exceeds the gain the law holds -- toward the fear
    /// trap the law exists to remove -- and a calm era lowers it. The centre
    /// and the read-back keep `L`: that is the era.
    pub vix_anchor_weight_level_knee_fixed: f64,

    /// How many sessions of market-side warm-up the factor's variance
    /// components get before session one. 0.0 -- every preset through
    /// pt-v19 -- runs nothing, touches no state and is bit-identical.
    ///
    /// # The defect
    ///
    /// [`ModelParams::macro_burn_in_days`] runs `Engine::advance_day`, the
    /// ECONOMY path. `Engine::close_market` -- where the slow variance
    /// LEVEL is drawn and where the factor's two variance components close
    /// -- is never called during it. So the LEVEL opens from its own
    /// stationary distribution (`market_vol_level_sigma`'s third paragraph)
    /// and every variance state it acts THROUGH opens cold, at the
    /// unscaled baseline `market_factor_sigma^2`.
    ///
    /// MEASURED, `programme/results/level-sigma-horizon.md` section 2.1
    /// (design repository): cut each 504-session recording at session 252
    /// and the two halves -- both 252-session windows of a level that is
    /// stationary from session one -- do not read the same.
    /// `sd(log window variance)` across 120 rosters reads 0.6938 +/- 0.0602
    /// in the first half and 0.9570 +/- 0.0609 in the second at
    /// `market_vol_level_sigma` 0.085, and the effect is ABSENT at sigma 0
    /// (ratio 1.09 [0.90, 1.30]). The transient traces out over 98 to 145
    /// sessions and is flat from about session 350.
    ///
    /// # What it does, and why it takes NO draw
    ///
    /// The level is a lognormal AR(1) and the components are linear in
    /// their target, so the state a fully-run-in engine would hold has a
    /// CLOSED conditional mean given the level the run opens on. This
    /// steps each component's MEAN recursion --
    /// `v <- (1 - p) * target + p * v` with `p = alpha + beta + gamma/2`,
    /// which is `E[v_{t+1} | v_t]` exactly, since `E[f^2] = v` and the
    /// leverage arm fires on half the days -- along
    /// `E[log L_{1-k} | log L_1] = phi^k * log L_1`, the level's own
    /// expected backward path.
    ///
    /// So the warm-up is a DETERMINISTIC function of a draw the close
    /// already takes. It consumes nothing, from any stream, at any
    /// setting: the draw schedule cannot move, no new stream is declared,
    /// and `rng::stream`'s whole argument about draw-consuming mechanisms
    /// does not have to be spent here. See
    /// `MarketVarianceState::warm_to_level`.
    ///
    /// What that leaves behind. The component's state given the opening
    /// level still has a conditional SPREAD -- the level's own innovations
    /// over the component's memory, and the GARCH shocks -- which a
    /// conditional mean cannot carry. DERIVED for the level's half of it:
    /// its variance is 0.096 of the component's total for the fast
    /// component and 0.207 for the slow, and because it decays on the
    /// component's own memory while the conditional-mean term does not, a
    /// 252-session window average retains 99.0 per cent of the stationary
    /// window statistic's variance against the cold start's 59.6 (Monte
    /// Carlo on the linearised recursion, 40,000 replications,
    /// `tools/calibration/warmup_probe.py derive`).
    ///
    /// **MEASURED on the engine, that 99 per cent is optimistic and the
    /// recovery is partial.** With the warm-up on, the factor's level
    /// envelope over the first quarter reads 0.770 against 0.846
    /// equilibrated -- 0.91, against a cold engine's 0.50 -- and the
    /// per-name GARCH and the VIX inherit it within twenty sessions, at
    /// 0.98 of their own equilibria against 0.64 and 0.56 cold. But the
    /// window statistic the calibration is read through recovers 46 per
    /// cent of its deficit, not 99: the split-half ratio goes 1.2539 to
    /// 1.1197 +/- 0.1818 on 48 rosters. The three readings are each about
    /// one standard error apart and were not reconciled;
    /// `programme/results/warmup-registration.md` sections 4.1 and 7
    /// register the gap and the stochastic warm-up that would close it.
    ///
    /// # 504
    ///
    /// DERIVED from the measured envelope: the transient is flat from
    /// session 350 and 504 is the next round number past it. The recursion
    /// converges geometrically at the SLOW component's persistence, so
    /// 504 sessions leaves `0.9913^504` = 0.012 of the initial condition,
    /// and anything past about 700 is arithmetically indistinguishable.
    /// It is a session count rather than a switch so that the length is
    /// falsifiable rather than assumed.
    ///
    /// # Two things it deliberately does not do
    ///
    /// It does not run at `market_vol_level_sigma` 0.0, and the reason is
    /// MEASURED rather than tidy. With no level, the components' target is
    /// `base` times the VIX's excursion from the index's own implied
    /// level, and that excursion averages one -- so the constructor's
    /// `base` is close to the right LOCATION and the block means show no
    /// travel an error bar can separate from noise (32 seeds, eight
    /// 63-session blocks: block one sits +0.080 in the log above the
    /// blocks-3-to-8 mean, about one standard error, and block EIGHT sits
    /// +0.137 above it in the same direction). What is cold there is the
    /// cross-seed DISPERSION: 0.087 in the log on day one and 0.594
    /// by day 300, 0.63 of equilibrium over the first quarter. **A
    /// deterministic warm-up cannot supply a dispersion** -- every seed
    /// would get the same number -- so running this at sigma 0 would buy
    /// exactly zero. Closing that one needs a warm-up that DRAWS, which
    /// needs a stream, which is a different change;
    /// `programme/results/warmup-registration.md` section 7 item 6
    /// registers it and says what it would cost.
    ///
    /// Keeping the zero-sigma arm untouched also keeps it usable as the
    /// control for BOTH a warmed and an unwarmed ladder.
    ///
    /// The run's FIRST session still trades at the cold sigma, because the
    /// level the components must be warmed to is not drawn until that
    /// session's close. One session in 504, and the alternative is a draw
    /// at construction -- which is a draw.
    pub market_burn_in_sessions: f64,

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
    /// preset up to the 2026-09-21 composition of pt-v19, which ships 4.9
    /// (the tape's lever law at the excursion form's fixed point; see the
    /// constructor) -- is the literal square, bit for bit. Round 100
    /// measured the square too convex through mid-VIX along real paths;
    /// a lower exponent with the coupling re-fit to hold T(45)/T(5)
    /// flattens the middle while preserving the certified crisis lever's
    /// backbone. Below one the target at very low VIX can go negative and
    /// the variance floor clamps it; the vix5 instrument must be checked,
    /// not assumed.
    pub market_vol_vix_exponent: f64,
    /// Downside transmission asymmetry: on a down tick of the market
    /// factor, every name receives `beta * factor * (1 + this)`. 0.0 --
    /// pt-v1 through pt-v15 -- is bit-identical; pt-v16 onward ship
    /// 0.025. The direct wire for
    /// correlation asymmetry: names co-moving harder on the way down IS
    /// the exceedance correlation real markets show and the panel's
    /// corr_asymmetry statistic measures. Raises down-day co-movement and
    /// some volatility asymmetry with it; the leverage_effect row is the
    /// stated side-channel to watch.
    pub market_beta_down_asym: f64,
    /// The LAGGED downside transmission: on the session after a down day,
    /// every name receives `beta * factor * (1 + this)` whatever the
    /// tick's own sign. 0.0 -- pt-v1 through pt-v16 -- is bit-identical;
    /// pt-v18 and pt-v19 ship 0.375, which `provenance.py` records as an
    /// S-argmin on a seven-point grid rather than as anything derived.
    /// Block 1201's deep-trim signature (the wire landing a day late on
    /// its structure) is the measured motivation: real down-moves
    /// continue, and the contemporaneous wire alone cannot express that.
    pub market_beta_down_asym_lag: f64,
    /// WHERE the lagged wire's condition is SAMPLED. A form dial with no
    /// number to derive: it does not change what the boost is, only which
    /// state the boolean is read off. 0.0 -- every shipped preset through
    /// pt-v19 -- is bit-identical, because the branch below returns
    /// `inputs.prev_day_down` unchanged.
    ///
    /// # The three values
    ///
    /// - **0.0, as shipped.** The condition is `prev_day_factor < 0`,
    ///   sampled once at the open and held for the whole session. Exact at
    ///   the bell and a day stale by the close.
    /// - **1.0, live.** At tick `k` of 390 the condition is
    ///   `prev_day_factor * (1 - k/390) + day_factor_so_far < 0`, which is
    ///   `E[sum of the last 390 tick factors | prev_day_factor, day_factor]`
    ///   under exchangeable within-day draws: yesterday's remaining tail
    ///   decays linearly across the session while today's own running sum
    ///   takes over. At `k = 0` it IS `prev_day_down`; at `k = 390` it is
    ///   today's own sign.
    /// - **2.0, the sign control.** The same quantity with the comparison
    ///   REVERSED (`c_k > 0`). A diagnostic arm only, registered as F4 in
    ///   `corr-asymmetry-repair.md` section 8: a live sample that moves
    ///   `corr_asymmetry` the same way under both signs is adding variance,
    ///   not re-timing a signed response. Never a shipping value.
    ///
    /// # Why this injects no first moment
    ///
    /// The multiplier `m_t = 1 + lag * 1{c_t < 0}` is a function of draws
    /// strictly BEFORE tick `t` -- `day_factor_so_far` is the accumulator
    /// as it stands when the tick is called, and `engine.rs` accumulates
    /// this tick's factor only AFTER `simulate_market_tick` returns. The
    /// tick's own factor `f_t` is an independent zero-mean draw, so
    /// `E[m_t f_t] = E[m_t] E[f_t] = 0` tick by tick and the day's
    /// delivered factor has mean zero. That is the sharp contrast with
    /// `market_beta_down_asym`, which scales the draw WHOSE OWN SIGN IT
    /// BRANCHES ON and therefore needs `market_beta_down_asym_recentre`
    /// beside it. There is no recentring dial here and no `return_acf1`
    /// channel: `E[F_d F_{d+1}] = 0` by the same independence. The shipped
    /// keying has this property too, so it is PRESERVED, not gained.
    ///
    /// # No draw
    ///
    /// A branch on state the snapshot already holds
    /// (`MarketVarianceState::prev_day_factor` and `::day_factor`). The
    /// draw schedule is a pure function of market status, active set and
    /// sector count and this touches none of them, so no stream is added
    /// and no restore offset moves.
    pub market_beta_down_asym_lag_live: f64,
    /// How much of the first moment `market_beta_down_asym` injects is
    /// given back. 0.0 -- every preset before pt-v18 -- is bit-identical.
    /// 1.0 returns the whole of it.
    ///
    /// # Why the tilt injects a first moment at all
    ///
    /// It scales one side of a zero-mean draw. Scaling only the down ticks
    /// of a symmetric factor moves its mean, and a mean in the price
    /// process is a drift: `E[f 1{f<0}]` for `f ~ N(0, s^2)` is
    /// `-s / sqrt(2 pi)`, so the tilt adds `a * beta * -s / sqrt(2 pi)` to
    /// every name every tick. Nobody chose that; it is the by-product of a
    /// correlation mechanism, and it cost the equal-weight index 7.9
    /// percentage points a year at pt-v16.
    ///
    /// # What is given back, exactly
    ///
    /// `this * a * beta * s / sqrt(2 pi)`, where `s` is the CONDITIONAL
    /// per-tick sigma the factor was actually drawn with rather than
    /// `market_factor_sigma`. Every quantity in it is known exactly at the
    /// point it is applied, so the correction is arithmetic and not an
    /// estimate, and it cannot be defeated by the factor's variance
    /// process, its VIX coupling or a scenario that pins VIX: a hotter
    /// tick injects more and gives back more, in the same ratio.
    ///
    /// `beta` is per name because it is not a correction but the algebra of
    /// the line being corrected -- the injection into name `i` IS `beta_i`
    /// times the form. Using 1.0 instead would leave a residual
    /// proportional to `beta_i - 1`, which is a cross-sectional bias as
    /// well as a mean one.
    ///
    /// # What is NOT given back, deliberately
    ///
    /// The crash amplifier. It multiplies the market channel above a
    /// threshold in baseline sigmas, and it is exactly the tail the tilt
    /// scales, so the true injected mean is the form above times
    /// `E[f 1{f<0} A] / E[f 1{f<0}]`. That ratio has no closed form. It
    /// was measured at 1.00 to 1.38 across conditional sigmas and sits
    /// near 1.01 at the sigmas that occur, so correcting it would mean
    /// either a new bit-pinned transcendental or a fitted constant, and a
    /// fitted constant is the thing this project calls tuning rather than
    /// fixing. The residual is therefore known, signed and about one per
    /// cent of the term, and it is left rather than approximated.
    ///
    /// The offset is applied AFTER the amplifier for the same reason. An
    /// offset added before it would itself be amplified, delivering the
    /// form times `E[A]` rather than the form.
    pub market_beta_down_asym_recentre: f64,

    /// Suppression of a name's IDIOSYNCRATIC shock on a down tick of the
    /// market factor, with the up tick inflated to hold the unconditional
    /// variance exactly. 0.0 -- every shipped preset -- is bit-identical by
    /// branch, the way every wire in `factors.rs` is.
    ///
    /// # The tape measurement this exists for
    ///
    /// MEASURED, `programme/results/corr-asymmetry.md` sections 0 and 5
    /// (design repository). `corr_asymmetry` at 504 is the largest single
    /// row on the whole-tape nineteen -- 3.29 of 9.19 on the release
    /// candidate -- and it is NOT a level deficit. The row and its lagged
    /// partner SUM to 0.1195..0.1590 across all thirty measured
    /// arm-horizon cells against a tape of 0.1291 at 252 and 0.1377 at 504.
    /// The model has as much down-conditioned co-movement as the tape. What
    /// is wrong is where it lands: the tape puts 47 per cent of it on the
    /// SAME DAY at 252 and 60 per cent at 504, and every pt-v19 arm puts 8
    /// to 15 per cent there. It is a routing defect, and it is a routing
    /// defect no existing dial reaches -- paired over 120 rosters and 22
    /// arms, the largest move any pt-v19 dial produces at 504 is
    /// +0.0026 +/- 0.0012, three per cent of a 0.065 gap.
    ///
    /// # Why not the tilt that is already there
    ///
    /// `market_beta_down_asym` is the same-day wire, and its argmin on the
    /// nineteen -- on slopes re-measured on the pt-v19 base itself -- is
    /// the shipped 0.025 at BOTH horizons. Not near it: at it. The reason
    /// is that it MULTIPLIES. Scaling the factor leg by `(1 + a)` on a down
    /// tick raises the name's conditional variance by
    /// `beta^2 s_f^2 ((1 + a)^2 - 1)`, so the factor's SHARE -- which is
    /// what a pairwise correlation is -- rises only as
    /// `q (1+a)^2 / (1 + q ((1+a)^2 - 1))` rather than as `q (1+a)^2`,
    /// while the whole of the excess variance lands on
    /// `annualised_vol_pct`, `excess_kurtosis` and the tails. The measured
    /// bill on the pt-v19 base is +9.5 points of annualised volatility and
    /// +0.27 of `return_acf1` per unit against +0.199 of the row, and
    /// `return_acf1` has a tape error of 0.0106.
    ///
    /// # The form, and why this form
    ///
    /// A REALLOCATION rather than a multiplication. The market leg is left
    /// exactly alone and the idiosyncratic shock is scaled instead:
    ///
    /// ```text
    /// down tick (market_factor < 0):  e -> e * (1 - c)
    /// up tick   (market_factor >= 0): e -> e * sqrt(2 - (1 - c)^2)
    /// ```
    ///
    /// Nothing is added anywhere; the factor's share is raised where the
    /// statistic looks and lowered where it does not. DERIVED, with `q` the
    /// unconditional pairwise correlation (`cross_sectional_corr` reads
    /// 0.3219 on the release candidate at 504), the tick-level conditional
    /// correlations are
    ///
    /// ```text
    /// q_down = q / (q + (1 - q) (1 - c)^2)
    /// q_up   = q / (q + (1 - q) (2 - (1 - c)^2))
    /// ```
    ///
    /// -- 0.394 and 0.269 at `c` = 0.15, a tick-level difference of 0.125.
    /// At the day-level attenuation of 0.47 read off the existing tilt's
    /// own contrast, that predicts about +0.059 of day-level
    /// `corr_asymmetry` at 504 against a gap of 0.0646. FALSIFIER, and the
    /// one that matters: if `d(corr_asymmetry)/dc` measures at or below the
    /// tilt's own +0.199 the reallocating form buys nothing the
    /// multiplying form does not and the mechanism is dead whatever else it
    /// does. The 0.47 is read off ONE contrast of a DIFFERENT wire and is
    /// the weakest number in the derivation.
    ///
    /// # The neutrality, exactly
    ///
    /// Let `f` be the tick's market factor and `e` the name's
    /// idiosyncratic draw, with `E[e] = 0`, `Var[e] = s^2`, and `e` drawn
    /// independently of `f`. The transform is `e' = m(f) e` with
    /// `m = (1 - c)` on `{f < 0}` and `m = sqrt(2 - (1 - c)^2)` elsewhere.
    ///
    /// **Mean: exactly zero, and not because of the symmetry.**
    /// `E[e'] = E[m(f)] E[e] = 0` for ANY `m` and any split of the line,
    /// because `e` is an independent zero-mean draw. That is the sharpest
    /// contrast with `market_beta_down_asym_recentre`: the tilt injects a
    /// mean because it scales the draw WHOSE OWN SIGN IT BRANCHES ON, and
    /// `E[f 1{f<0}]` is not zero. This branches on a different draw, so
    /// there is no first moment to give back and no recentring dial beside
    /// it.
    ///
    /// **Variance: exactly held, and it is the symmetry that buys it.**
    /// `E[e'^2] = E[m(f)^2] s^2` with
    /// `E[m(f)^2] = p (1-c)^2 + (1-p) (2 - (1-c)^2)` at `p = P(f < 0)`.
    /// The inflation is DEFINED as the complement, so at `p = 1/2` the two
    /// squared scales average to exactly 1 for every `c` -- which is why
    /// there is no funding dial here either, and why `market::index_var`
    /// needs no new term: its `idio * idio` is the unconditional variance
    /// and this leaves it alone.
    ///
    /// # Where "exactly" stops, stated rather than asserted
    ///
    /// 1. **Exact in expectation, `O(N^{-1/2})` in a realisation.** Over
    ///    `N` ticks the realised multiplier on the idiosyncratic variance
    ///    is `M_N = (k/N)(1-c)^2 + (1 - k/N)(2 - (1-c)^2)` with
    ///    `k ~ Binomial(N, 1/2)` -- the sign of a symmetric draw is a fair
    ///    coin independent of every sigma in the recursion. So `E[M_N] = 1`
    ///    exactly and `sd(M_N) = |1 - (1-c)^2| / sqrt(N)`. MEASURED against
    ///    that closed form by `the_variance_residual_is_the_binomial_one`.
    ///    At `c` = 0.15 it is 1.4 per cent of the idiosyncratic variance
    ///    over one 390-tick session and 0.089 per cent over a 252-session
    ///    window, which on a name whose idiosyncratic leg is two thirds of
    ///    its variance is 0.03 per cent of its sigma.
    /// 2. **One ulp of arithmetic.** `(1-c)^2` and `2 - (1-c)^2` are
    ///    computed in doubles, so their mean is 1 to a relative `2^-52`
    ///    rather than to the bit. Pinned by
    ///    `the_two_scales_average_to_one_to_within_an_ulp`.
    /// 3. **THE ZERO TICK.** The branch is `< 0.0`, the convention
    ///    `market_beta_down_asym` already uses, so a factor of exactly
    ///    `0.0` takes the UP branch. On a live draw that is a
    ///    probability-zero event; on a degenerate configuration where the
    ///    factor cannot move -- `market_factor_sigma` 0.0, or a fixture
    ///    that hands the tick a zero factor -- EVERY tick takes the up
    ///    branch and the idiosyncratic variance is inflated by
    ///    `2 - (1-c)^2` rather than held. That is not a defect of the
    ///    arithmetic; it is what "half the ticks are down" means when none
    ///    of them is. It is written down because a zero-factor fixture is
    ///    exactly where somebody would go to measure neutrality and would
    ///    find it absent.
    ///
    /// # Three things it costs
    ///
    /// 1. **It is not CONDITIONALLY variance-neutral, and cannot be.** A
    ///    name's per-tick total variance is
    ///    `beta^2 s_f^2 + m(f)^2 s_i^2`, which is lower on a down tick and
    ///    higher on an up tick -- that inequality IS the raised factor
    ///    share. So a down DAY, which holds more down ticks than an up day,
    ///    carries slightly less name-level variance: at `c` = 0.15 a
    ///    55/45 day carries 0.972 of its idiosyncratic variance. The
    ///    per-name GJR GARCH innovation is the day's noise and
    ///    `garch_gamma` reads exactly that conditional moment, so the
    ///    leverage channel sees a slightly SMALLER down-day squared return.
    ///    Signed, small, and registered as a falsifier
    ///    (`asymneut-registration.md` F7) rather than argued away.
    /// 2. **The intraday leg only.** The overnight move composes a name
    ///    through `idio_scale_for` as the tick does, and it is untouched
    ///    here because there is no tick sign at the open. Nothing is lost
    ///    on the shipped preset, where `overnight_variance_ratio` is 0.0
    ///    and no price moves between sessions; on a preset that turned it
    ///    on, the reallocation would cover the intraday part of the
    ///    close-to-close return and not the gap.
    /// 3. **NO DRAW, which is the point.** The transform reshapes a shock
    ///    the tick has already taken, so the draw count and the draw order
    ///    are untouched at every value and on every branch. A mechanism
    ///    that needed a number would have needed a stream of its own.
    pub market_idio_down_suppress: f64,
    /// How much of oil DEMAND is answered by supply on the daily step.
    /// 0.0 -- every preset before pt-v18 -- is bit-identical, and it is what
    /// the reference implementation does.
    ///
    /// # The zero nobody chose
    ///
    /// `update_economy_daily` draws inventory down by
    /// `gdp_growth * 0.15` every day and replenishes it by a hardcoded
    /// `oil_supply_factor = 0.0`. Inventory therefore falls monotonically
    /// from its opening 50 whatever the world does, reaches its floor
    /// around day 120 of a 252-day run, and stays there. The inventory
    /// pressure term is `(40 - inventory) * 0.08`, so at the floor it
    /// saturates at a standing `+3.2` a day on the oil price, which oil's
    /// own mean reversion of 0.03 a day cannot hold. Oil then pins at its
    /// 150 clamp, and from there the chain is mechanical: oil raises
    /// inflation, inflation makes the central bank hike, the hike raises
    /// the ten-year and the corporate yield, the higher discount rate
    /// compresses every target multiple, and every price falls.
    ///
    /// That is the whole of the one-way rate path. It is not a rate
    /// mechanism at all; it is a supply term left at zero.
    ///
    /// # Why 1.0 is derived and not fitted
    ///
    /// At 1.0 supply equals demand in expectation, so `inventory_change`
    /// is the noise term alone and inventory is driftless. That is the
    /// stationarity condition of the inventory process, read off the
    /// process itself, and the `0.15` it uses is the coefficient already
    /// there. Nothing here is tuned to a target: the value that makes a
    /// random walk driftless is not a matter of degree.
    ///
    /// The pressure term is already two-sided, pushing up below 40 and
    /// down above 60, so a driftless inventory gives a two-sided oil price
    /// and a two-sided rate path without any of them being made two-sided
    /// by hand.
    pub oil_supply_response: f64,
    /// Removes the direction from the OPEC rule while keeping its size.
    /// 0.0 -- every preset before pt-v18 -- is bit-identical.
    ///
    /// # The asymmetry nobody chose
    ///
    /// The rule reacts to the oil price against an 80 target. Below it by
    /// more than 10 it cuts production with probability 0.6 and magnitude
    /// 3 to 6; above it by more than 10 it raises production with
    /// probability 0.5 and magnitude 2 to 5. Expected `+2.700` against
    /// `-1.750`, so the cut is 1.54 times the increase and the rule pushes
    /// the oil price up on net. Nothing in the code says that was intended
    /// and the two branches read as a pair that should mirror.
    ///
    /// # Symmetrised rather than picked
    ///
    /// At 1.0 both branches use one probability and one magnitude range,
    /// each the mean of the two the rule already carries: probability
    /// 0.55, magnitude 2.5 to 5.5. That is the unique symmetric rule which
    /// preserves the total intervention the rule performs, so it removes
    /// the direction without choosing a side and without inventing a
    /// number. Expected impact is then equal and opposite either side of
    /// the target, and zero on net.
    ///
    /// It is worth about +0.95 of oil price per firing and the rule fires
    /// every 90 days, so this is a small term. It is corrected because it
    /// is wrong rather than because it is large.
    pub oil_opec_symmetry: f64,
    /// Where oil's seasonal shape acts: on the price the process reverts
    /// toward, or on the price level itself. 0.0 -- every preset before
    /// pt-v18 -- is bit-identical.
    ///
    /// # A shape applied to a level compounds
    ///
    /// The daily step multiplies the whole new oil price by
    /// `1 + 0.03 * sin(2 pi (day_of_year - 90) / 365)`. Applied to a level
    /// every day the factors multiply, so what a window sees is their
    /// product: 5.119 over the 252 game-days a certified year passes, and
    /// 0.921 over a full 365. The shape is near neutral over its own
    /// period and the horizon slices it asymmetrically, taking 162 days of
    /// the up leg against 90 of the down.
    ///
    /// Summing the term's own contribution to each day's change over year
    /// one gives +365.80 of oil price against a net change of +72.10, so it
    /// pushes about five times harder than the price moves and the mean
    /// reversion of 0.03 a day absorbs the rest. Oil has no fixed point
    /// under it. It is a forced limit cycle with a 365-day period that sits
    /// on its 150.0 clamp from day 180 on every seed, and the inflation
    /// term, the meeting rule and the discount rate follow it there.
    ///
    /// # The target rather than the level, at the same amplitude
    ///
    /// At 1.0 the whole amplitude multiplies the reversion target and none
    /// of it multiplies the level, so the shape modulates where the price
    /// is pulled toward by plus or minus 3 per cent and integrates to
    /// +0.672 per cent of oil over a certified year. The amplitude is the
    /// 0.03 the term already carries and this dial is a share of it, so
    /// what changes is where a shape acts rather than how large it is.
    /// Nothing here is fitted: a seasonal shape has to be neutral over the
    /// WINDOW as well as over its own period, and moving it off the level
    /// is what makes that possible without touching its size.
    ///
    /// Between the ends the amplitude is split, `1 + g * a` on the target
    /// and `1 + (1 - g) * a` on the level, so the total is conserved and
    /// past 1.0 the level would carry the shape inverted.
    pub oil_seasonality_target: f64,
    /// The clock the business cycle's hazard is read on. 0.0 -- every
    /// preset before pt-v18 -- is bit-identical.
    ///
    /// # A rate per month drawn once a day
    ///
    /// `weibull_hazard` returns `(shape / scale) * pow(months / scale,
    /// shape - 1)`, and every scale in `cycle_hazard_params` is in months:
    /// 36 for an expansion, 6 for a peak, 12 for a contraction. A hazard
    /// whose scale is in months is a rate per month, and
    /// `check_cycle_transition` compares it against a uniform once a day,
    /// so the cycle runs about thirty times too fast. A full cycle takes
    /// 2.6 trading years where the same constants read per month give 9.7,
    /// and a 252-day run opening at the start of an expansion leaves it 63
    /// per cent of the time against 3.
    ///
    /// Both figures are the hazard alone. The condition ladder below adds
    /// to it and never subtracts except through the expansion guard, so
    /// each bounds a phase's length from above. The ratio of thirty is
    /// unaffected, since both readings exclude the ladder equally, and the
    /// ladder is scaled with the base, because the conversion below is
    /// applied after it.
    ///
    /// # A correction to the arithmetic this comment carried
    ///
    /// An earlier version said the ladder's four contraction conditions
    /// sum to 0.25 against a base hazard of 0.081, so a deep contraction
    /// ran 7.7 months where a mild one ran 23. Every contraction condition
    /// adds to the hazard, so the ladder can only shorten a phase, but the
    /// arithmetic assumed all four fire from the phase's fourth month.
    /// Measured on the four-year arms they fire late: growth under -2.0 on
    /// day 1 from the phase-change shock, the policy rate under 1.0 on day
    /// 190 at the median and unemployment over 10.0 on day 239.
    ///
    /// A spell's count of fired conditions therefore records how long it
    /// has already run. Completed contraction spells read 162 days at one
    /// condition, 226 at two and 335 at three, which sorts them by duration
    /// rather than by depth and carries no reading of either. A deep
    /// contraction ended early by its own ladder was looked for and
    /// measured absent.
    ///
    /// `months_in_current_phase` advances by exactly `1/30` a day, so the
    /// month this model keeps is 30 days and the divisor is read off the
    /// engine's own clock rather than chosen. At 1.0 the daily probability
    /// is the monthly rate divided by 30 to the last bit.
    ///
    /// # The whole ladder is in months
    ///
    /// The conversion is applied last, after `adjust_transition_probability`
    /// and after the clamp, because every operand before it is a rate per
    /// month: the hazard's own cap of 0.8, the ladder's additions of 0.1
    /// and 0.15 for inflation, policy and an inverted curve, and the clamp
    /// at 0.3. Converting earlier would leave the ladder as a daily
    /// probability against a base hazard near 0.0004 a day, so an inverted
    /// curve would raise the transition rate by 250 times where it now
    /// triples it. The 9.7 years above assumes this placement; dividing
    /// before the clamp instead gives 9.59, and the difference sits
    /// entirely in the two short phases.
    ///
    /// So the clamp becomes a cap on a monthly rate and the largest daily
    /// probability is 0.01. On the hazard alone it binds for a peak past
    /// month 5.40 and a trough past month 2.57 and nowhere else, since an
    /// expansion reaches it at month 338, a recovery at month 358, and a
    /// contraction's hazard falls with duration. It shapes the mean peak
    /// from 183 days to 171 and the mean trough from 159 to 138.
    ///
    /// Measured over thirty seeds at 1008 days on the era's roster, at
    /// commit 6dfe09b. Read per month the clamp binds on 527 of 2082 peak
    /// rolls and 153 of 203 trough rolls on the hazard alone, and on 0 of
    /// 18352 expansion and 0 of 1495 contraction rolls. Drawn per day it
    /// binds on none of them.
    ///
    /// WITH the ladder the trough is different, and it was different
    /// before this dial existed. A trough adds 0.1 for a policy rate under
    /// 3.0 and 0.05 for unemployment over 8.0, against a hazard of 0.265
    /// at its two-month minimum, so the clamp binds on its first eligible
    /// roll under either reading: 203 of 203 read per month and 97 of 97
    /// drawn per day, over the same runs. So the clamp was already doing
    /// work in a trough, and what changed is that phases now last long
    /// enough to reach one. A certified year reaches no trough at all and
    /// records 0 of 2121 rolls clamped under either reading.
    pub cycle_hazard_per_month: f64,
    /// Where the trough phase's growth range begins. 0.0 -- every preset
    /// before pt-v18 -- is the shipped `(-1.0, 0.5)` and bit-identical.
    ///
    /// # A second falling phase the engine does not call a recession
    ///
    /// `phase_characteristics` gives a trough `gdp_growth_range` of
    /// (-1.0, 0.5), a midpoint of -0.25, so output is still falling
    /// through a phase named for the bottom of the cycle. Measured over a
    /// hundred years on thirty seeds, 95 per cent of trough days carry
    /// falling output at pt-v18 and the share passes 100 at pt-v16.
    ///
    /// That is the whole of the engine's disagreement between its two
    /// frequency rulers. The share of days in a contraction reads 18.21
    /// and the share with output falling reads 27.31, while contraction
    /// or trough reads 27.61: the second and third agree to 0.3 points,
    /// so the nine-point gap IS this phase. The NBER's two rulers agree
    /// to 1.2 points, 10.72 per cent of months in a contraction against
    /// 11.93 per cent of quarters with real GDP falling, because a
    /// contraction there IS the falling-output period.
    ///
    /// At 1.0 the lower end moves to 0.0 and the upper end keeps the 0.5
    /// the table already states. Zero is the only non-arbitrary floor,
    /// being the boundary between falling and rising output, so nothing
    /// here is fitted. Between the ends the floor moves proportionally.
    ///
    /// # It is 0.0 on every preset, including pt-v18, and here is why
    ///
    /// This was built to close that nine-point gap and it closes 1.52 of
    /// it. Thirty seeds at a hundred years: the output ruler moves from
    /// 27.309 to 25.791 and the contraction share moves 0.10, so the
    /// mechanism is the one described and its size is a sixth of what the
    /// accounting implied.
    ///
    /// A target is only reached if the phase lasts long enough to
    /// approach it. Growth enters a trough near -3.0, having left a
    /// contraction whose realised rate is about -2.5 and then taken this
    /// phase's own -0.5 entry shock, and it reverts by `gap * 0.12` a
    /// month and `gap * 0.25` a quarter. Under the shipped range it
    /// asymptotes to -0.25 and NEVER crosses zero. At 1.0 it crosses at
    /// month 11 or 12. A trough runs 2 to 6 months, so output falls
    /// through the whole phase under either setting and this dial changes
    /// where growth is heading rather than where it is.
    ///
    /// So the binding constraint is the reversion rate against the phase
    /// length, not the declared range, and the repair the evidence points
    /// at is the -0.5 entry shock at `daily.rs:568`, which this line placed
    /// at `:247` until 2026-09-17: the only phase after
    /// a contraction that still pushes growth DOWN on entry is the one
    /// named for the turn.
    ///
    /// Kept at 0.0 rather than deleted because the measurement is the
    /// reason the next change is known, and the dial is the instrument
    /// that produced it.
    pub trough_growth_floor: f64,
    /// Whether a phase's growth target is drawn from its declared range
    /// or fixed at the range's midpoint. 0.0 -- every preset before
    /// pt-v18 -- takes NO draw and is bit-identical.
    ///
    /// # A declared range the mechanism reduces to its midpoint
    ///
    /// `gdp_growth_range` is stated for all five phases and read at
    /// exactly two sites, both of which take `(lo + hi) / 2.0`. The
    /// declared WIDTH is discarded everywhere, so the table reads as a
    /// specification and behaves as a list of five midpoints. That is the
    /// third instance of one habit in this struct rather than three
    /// accidents: `max_months` is declared for every phase and read by
    /// nothing, `gdp_growth_range` is declared and halved to a midpoint,
    /// and the trough's phase-change shock carries a sign that
    /// contradicts its own phase.
    ///
    /// What that costs is the depth distribution. An episode's fall is its
    /// mean growth times its length, and with the rate pinned to a
    /// midpoint the only varying input is the single phase-entry shock,
    /// which spans 2.0 to about 3.0. A ratio of 1.5 to 1 in the one
    /// varying input cannot produce the 9.95 to 1 span between the
    /// mildest and deepest post-1980 recession, at any sample size.
    ///
    /// At 1.0 the target is drawn once on phase entry, uniformly over the
    /// range the table already declares, and held for the phase. The
    /// contraction range is (-3.0, 0.0), whose uniform mean is its own
    /// midpoint, so this widens the depth distribution without moving its
    /// median. Measured over thirty seeds at a hundred years: the median
    /// moves 0.06 and episodes past a three per cent fall go from 20 to 34
    /// of about 450. Between the ends the draw is scaled toward the
    /// midpoint. No constant is invented: the numbers are the ones the
    /// table states.
    ///
    /// # Zero on every preset until the four items are composed
    ///
    /// This is the era's one shippable dial for the recession item and it
    /// is still 0.0 in pt-v18, deliberately. Four items are landing dials
    /// into one preset and each is measured alone, so a preset assembled
    /// by turning them on one at a time has no arm that sees the pair
    /// terms. This item supplies the evidence for that rule rather than an
    /// exception to it: its two dials each improve the depth spread alone
    /// and together they are worse than neither, because the pair
    /// eliminates shallow recessions. Two dials from ONE item already did
    /// it. Composition is one step, measured together, and it belongs to
    /// whoever runs it.
    pub phase_target_range_draw: f64,
    /// The corporate bond yield at which the target multiple sits exactly
    /// on its sector anchor. 0.04 -- every preset before pt-v18 -- is the
    /// constant this was, to the bit.
    ///
    /// # A neutral point the economy never visits
    ///
    /// `compute_target_pe` compresses the multiple by
    /// `(discount - neutral) * RATE_PE_SENSITIVITY * duration`, so a name
    /// is valued on its anchor exactly when the discount rate equals this.
    /// The economy opens at a corporate yield of 4.56 per cent and settles
    /// at 4.82, and it never visits 4.00. So every profitable name opens
    /// about one per cent below the price the generator drew for it, which
    /// is +0.0107 of day-zero mispricing at the opening and +0.014 at the
    /// corner, and the market spends the year unwinding it on a 60-day
    /// half-life. At the second sweep's measured -94.872 index points per
    /// unit of opening level that is 1.0 to 1.3 points of the first year,
    /// on every seed, and nothing in a stationary year.
    ///
    /// # Read off the economy rather than chosen
    ///
    /// The value that zeroes the day-zero term is the yield the economy
    /// opens at, which is 0.0456 as it opens today and 0.0482 at the corner
    /// the dynamics reach. Both are read off the process, the second from
    /// the burn-in table, so neither is a matter of degree.
    ///
    /// # Why the generator is untouched
    ///
    /// The multiple this anchors is the same sector anchor the generator
    /// draws its multiples around, so the two stay consistent under any
    /// neutral rate and a roster opens at fair value exactly when the
    /// engine's discount rate equals this. `NEUTRAL_DISCOUNT_RATE` is read
    /// by no line of the generator's code; the only other reference is its
    /// own test.
    ///
    /// # Promoted rather than renamed
    ///
    /// This name was on the carried read-only surface. `to_pairs` merges
    /// that surface with the settable one and sorts, so moving the name
    /// between them leaves every preset's pairs, fingerprint and
    /// coefficient digest untouched wherever the value has not moved. A new
    /// name would have added a second entry for one quantity, with this one
    /// still reading 0.04 about an engine using 0.0456.
    pub neutral_discount_rate: f64,
    /// Days the economy is advanced alone, before day zero. 0.0 -- every
    /// preset before pt-v18 -- draws nothing and leaves construction as it
    /// was.
    ///
    /// # A year spent travelling
    ///
    /// The economy opens at unemployment 4.00, inflation 2.00 and a
    /// corporate yield of 4.56, and its own dynamics reach 2.50, 2.74 and
    /// 4.82. Everything this project certifies is certified on a window in
    /// which nothing has settled, and the travel is one-way on the
    /// multiple.
    ///
    /// # The length is measured
    ///
    /// 755 is the day the last field enters one stationary standard
    /// deviation of its mean and stays there, which is the corporate yield
    /// in the burn-in table. Unemployment takes 119 days, inflation 419 and
    /// the ten-year 705. Structural unemployment is still moving after
    /// three years and is left where it is, since the fields the valuation
    /// reads have all settled by 755.
    ///
    /// # What it costs to run
    ///
    /// The draws come from the economy's own substream, so the market's
    /// day-0 draws are where they were. It consumes economy draws, which is
    /// the only settable field besides the volatility jump that moves a
    /// draw count, and it does so by running the economy rather than as a
    /// side effect.
    pub macro_burn_in_days: f64,
    /// Draw the day-zero cycle phase AND its age from the cycle's own
    /// stationary law, instead of opening every run at the same point.
    /// 0.0, which every shipped preset carries, draws nothing and leaves
    /// construction as it was, to the bit.
    ///
    /// # A cohort, not a transient
    ///
    /// Every run opens in EXPANSION at phase age ZERO. A phase has a
    /// minimum duration before a transition can roll (`economy/state.rs`,
    /// `phase_characteristics`: 6, 2, 4, 2, 4 months) and then a Weibull
    /// hazard (`economy/cycle.rs`), and at this era's clock the exit after
    /// the minimum is steep, so thirty seeds leave their first expansion
    /// at nearly the same age and move through the first cycle in step.
    /// Year two is a synchronised recession -- contraction share 0.51 on
    /// pt-v1, pt-v16 and a pinned-VIX arm alike, so it is a property of
    /// the construction and not of a preset -- and the macro fields travel
    /// with it: unemployment 2.8, 5.8, 7.0, 4.9 by year on pt-v16, the
    /// recession probability 0.13, 0.54, 0.14, 0.37.
    ///
    /// It does not damp on a horizon anyone runs. The fixed minimums make
    /// the cycle's length nearly deterministic, so the yearly mix is still
    /// 0.20 to 0.33 of a total-variation unit from stationary in years
    /// five to twelve at this clock. `macro_burn_in_days` does not fix it
    /// either, and was never meant to: it HOLDS the phase and RESETS its
    /// clock, so it restores the same point after settling the fields.
    ///
    /// # The identity it draws from
    ///
    /// The cycle is a cyclic semi-Markov chain, so for phase `i` at age
    /// `a` days ([`crate::economy::stationary_opening`]):
    ///
    /// ```text
    /// S_i(d)  = prod_{t=1}^{d-1} (1 - p_i(t))   P(the sojourn survives d - 1 checks)
    /// E[T_i]  = sum_{d>=1} S_i(d)               the mean sojourn, in days
    /// pi_i    = E[T_i] / sum_j E[T_j]           the share of days in phase i
    /// f_i(a)  = S_i(a + 1) / E[T_i]             the age within phase i
    /// P(i, a) = S_i(a + 1) / sum_j E[T_j]       the joint law
    /// ```
    ///
    /// with `p_i` the probability `check_cycle_transition` compares against
    /// its uniform, read on the hazard alone. Nothing is chosen: every term
    /// is the engine's own -- `cycle_hazard_params`, `min_months`, the
    /// hazard's cap of 0.8, the clamp at 0.3 and
    /// [`ModelParams::cycle_hazard_per_month`] -- and the walk stops where
    /// the remaining tail cannot move an f64 sum of it.
    ///
    /// **The AGE is not optional.** `f_i` is the renewal identity for the
    /// backward recurrence time, and it is the part a build would skip.
    /// Drawing the phase and setting the age to zero would start a smaller
    /// cohort at the same point: at this clock 73 per cent of stationary
    /// expansions are younger than the 180-day minimum a fresh one has to
    /// clear, with a median age of 123 days against a mean sojourn of 246.
    ///
    /// # The fields, and the ladder
    ///
    /// `adjust_transition_probability` adds to the hazard from the macro
    /// state, so the TRUE stationary law depends on the fields, which
    /// depend on the phase path. There is no closed form. The draw above
    /// is the hazard-only law; the fields are then relaxed by
    /// `macro_burn_in_days` days of the ordinary daily step with the phase
    /// NOT held and its clock NOT reset -- the chain is already in its
    /// stationary law, which a free run preserves by definition, while
    /// unemployment, inflation and the yields relax to the values
    /// consistent with the phase path they have just lived through, and the
    /// ladder acts on the mix during that run. That is how the ladder is
    /// handled: by construction rather than by algebra.
    ///
    /// So this dial and `macro_burn_in_days` are two halves of one
    /// opening, and a preset that sets this without the other opens a
    /// drawn contraction on an expansion's fields.
    ///
    /// # Why it is a SWITCH and the interior has no reading
    ///
    /// A day-zero state is either drawn from the stationary law or it is
    /// not; there is no half-drawn phase. Every non-zero value therefore
    /// gives the same opening, which is asserted rather than left for a
    /// search to find as a flat direction:
    /// `test_every_non_zero_value_gives_the_same_opening` in
    /// `tests/test_stationary_opening.py`. The two admissible values are
    /// 0.0 and 1.0.
    ///
    /// # What it costs
    ///
    /// Two uniforms from the economy substream at construction, so the
    /// market's day-zero draws sit where they sat, plus about 2,500 to
    /// 67,000 multiplies once -- the identity's walk, whose length is the
    /// clock's, not the run's. Nothing per session.
    ///
    /// # It moves the draw schedule in TWO places, not one
    ///
    /// The two construction uniforms, as `macro_burn_in_days` already
    /// moves it and declares. And then, for the WHOLE RUN:
    /// `check_cycle_transition` returns before drawing while a phase is
    /// younger than its minimum duration, so a run opening at age zero
    /// rolls no exit for its first 180 days while one opening past the
    /// minimum rolls one every day. The count there is the mechanism -- a
    /// phase past its minimum is a phase whose exit is being rolled -- and
    /// it is not a construction cost.
    ///
    /// The three-day perturbation probe cannot see either, and reads the
    /// count IDENTICAL at its own seed: the phase-change block in
    /// `economy/daily.rs` takes a uniform on both of the two days it fires
    /// and a drawn age past two days skips both, cancelling the two
    /// construction draws exactly. Measured over six seeds at 1, 2, 3, 5,
    /// 10 and 30 days the difference runs 0, +1, +2, +4 and +30, which is
    /// why `ECONOMY_STREAM_MOVERS` in `tests/test_model_params.py` carries
    /// this dial for the mechanism rather than on the probe's evidence.
    /// That set was called `DRAW_SCHEDULE_MOVERS`, as this line did until
    /// 2026-09-17, and was renamed when the sweep found no dial that moves
    /// the market stream.
    pub cycle_stationary_opening: f64,
    /// The share of earnings a company returns as net buybacks. 0.0 --
    /// every preset before pt-v18 -- is bit-identical.
    ///
    /// # A CHOSEN constant, and the only one in this era
    ///
    /// Every other dial here is derived: a stationarity condition, a
    /// symmetric mean, a unit, a measured transient, the yield the economy
    /// opens at. This one is not. It is a statement about how US large-cap
    /// companies behave, taken from company filings, where total
    /// shareholder return runs near half of earnings split between
    /// dividends and net buybacks. A third in net buybacks sits inside that
    /// record and a reader can check it against the same source.
    ///
    /// The era's record marks it CHOSEN for that reason. It is not fitted
    /// to anything this engine produces, which is the property that makes
    /// it checkable: a value fitted to our own distribution would be
    /// unfalsifiable outside it.
    ///
    /// # What it buys, and the check that is not its source
    ///
    /// The model's own median annual earnings yield is 0.0555 at pt-v18 on
    /// the era's roster, so a third of it is a buyback yield of 1.85 per
    /// cent. US large-cap net buybacks over 2000 to 2025 run near 1.5 to
    /// 2.0 per cent of market value. That agreement is a check on the
    /// share, in that order: the share comes from the earnings record and
    /// the yield it implies is then compared against the value record.
    ///
    /// # Why it is a yield and not a premium
    ///
    /// Earnings over price, so it pays more when the multiple is low and
    /// less when it is high. A flat premium would pay the same in a market
    /// priced at forty times earnings as at ten, which is the opposite of
    /// what a buyback programme does with a fixed budget. See
    /// [`crate::market::tick::buyback_scale`] for the arithmetic, its
    /// residual against the exact path integral, and the clamp on a
    /// loss-maker.
    pub buyback_payout_share: f64,
    /// How much of the drift the market jump's mean carries is given
    /// back. 0.0 -- every preset before pt-v18 -- is bit-identical. 1.0
    /// subtracts the compensator and makes the jump a martingale.
    ///
    /// # The mean is there for skew, and it also buys a drift
    ///
    /// `jump_mean_market` is negative so that crashes are larger than
    /// rallies, which is a real property of index returns and a legitimate
    /// thing to want. But a jump that arrives with probability `lambda`
    /// and mean `m` contributes `lambda * m` to the expected return every
    /// day whether it fires or not, so the skew comes with a drift nobody
    /// asked for. At pt-v16 that is -0.11769 per name per year at the
    /// day-zero intensity, and 2.6 percentage points of annual index
    /// level.
    ///
    /// The value was set once in the pt-v4 era by a search whose objective
    /// could not read a first moment, and inherited unchanged through
    /// eleven presets. So the drift was never chosen; it was never
    /// visible.
    ///
    /// # Compensated rather than re-derived
    ///
    /// The obvious repair is to solve for a smaller mean that buys skew
    /// without the drift. There is no such value: for a compound Poisson
    /// jump the drift and the skew are both linear in the mean, so trading
    /// one against the other is a matter of degree and any answer would be
    /// a fitted constant.
    ///
    /// Subtracting `lambda * m` instead is the standard compensated-Poisson
    /// construction. It makes the jump term a martingale, and because the
    /// compensator is a DETERMINISTIC offset it moves the first moment and
    /// leaves every central moment untouched. The skew and the fat tail
    /// survive exactly, at the mean the calibration chose, and the drift
    /// goes to zero. The mean therefore does not move at all: what was
    /// wrong was the missing compensator and not the value.
    ///
    /// `lambda` is the CONDITIONAL intensity, already scaled by the VIX
    /// coupling, so the compensator tracks the arrival rate. The
    /// investigation measured the realised drift at 1.084 times the
    /// day-zero closed form for exactly that reason, and a compensator on
    /// the day-zero rate would have left that 8 per cent behind.
    pub jump_mean_compensated: f64,
    /// How much of the stop-cascade ladder's direction is removed. 0.0 --
    /// every preset before pt-v18 -- is bit-identical. 1.0 makes the two
    /// ladders mirror images.
    ///
    /// # The asymmetry nobody chose
    ///
    /// Forced flow from resting stop orders runs both ways: stop-losses
    /// under longs on the way down, buy-stops over shorts on the way up.
    /// The two ladders that express it do not match. The downside fires at
    /// a 2 per cent move and the upside at 3; the downside has four tiers
    /// and the upside three; and at every matched size the downside is
    /// larger, 0.008 against 0.006, 0.005 against 0.004, 0.003 against
    /// 0.002, with a fourth downside tier of 0.001 that has no partner.
    /// Every one of those is a bare literal with no parameter, so nothing
    /// could reach them and nothing recorded a reason for the difference.
    ///
    /// Over a symmetric distribution of daily returns a ladder that
    /// subtracts more than it adds is a drift, which is this era's shape
    /// again.
    ///
    /// # What is matched, and what is deliberately left alone
    ///
    /// The GATES stay. A stop-loss sits under every long, so the downside
    /// needs no condition; a buy-stop needs shorts to exist, so the upside
    /// keeps `short_interest_ratio > 0.1`. That is defensible finance and
    /// not an asymmetry anybody left by accident.
    ///
    /// The THRESHOLD and the TIER MAGNITUDES are matched, at the mean of
    /// the two ladders: threshold 0.025, tiers 0.007, 0.0045, 0.0025 and
    /// 0.0005. That is the same construction the OPEC rule uses. It
    /// chooses neither side and it preserves the total intervention the
    /// pair performs exactly, 0.029 across both ladders before and after.
    ///
    /// # What is NOT derived here, and is worth saying
    ///
    /// Unlike the tilt, the jump and the oil supply term, this one has no
    /// stationarity condition or closed form behind it. Odd symmetry in
    /// the return is the structural claim; the mean is a rule for picking
    /// the numbers under it rather than a value read off the process.
    /// Whether these literals should become parameters at all is an open
    /// question for the era's owner rather than something settled here.
    pub cascade_symmetry: f64,
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
    /// How much of nominal output growth the valuation's earnings carry.
    /// 0.0 -- every preset before pt-v18 -- is bit-identical. 1.0 holds the
    /// earnings share of nominal output constant.
    ///
    /// # Why the model has no expected return without this
    ///
    /// Price is `fair_value * exp(s)`, `s` is a stationary AR(2) around
    /// zero, and `eps` is fixed when an instrument is built, so the only
    /// time variation in fair value is the discount rate. The expected log
    /// change of the index over any horizon is therefore zero in a
    /// stationary economy, and negative in one whose yields rise. A real
    /// large-cap price index returns 8 to 9 per cent a year nominal on an
    /// equal-weight basis, and nothing in the architecture could deliver
    /// it.
    ///
    /// A drift placed in `s` cannot deliver it either. Under a constant
    /// drift `c` per step the stationary mean solves `m = phi * m + c`, so
    /// a premium injected there is a LEVEL of `c / (1 - phi)`, reached on
    /// the 60-day half-life and followed by no growth at all. Simulated at
    /// 3, 6 and 9 per cent a year it gave levels of +0.010, +0.021 and
    /// +0.031 with third-year growth of zero. An expected return has to
    /// enter fair value.
    ///
    /// # What it scales, exactly
    ///
    /// `eps` and `book_value_per_share` are multiplied by
    /// `1 + this * (N_t / N_0 - 1)` before the valuation reads them, where
    /// `N = gdp * cpi` is nominal output and `N_0` is its value when the
    /// engine was built. The multiplier is 1.0 on day 0 by construction,
    /// so the opening valuation, and the lazy initial `s` taken from it,
    /// are unchanged.
    ///
    /// Both fields, because the valuation is then homogeneous of degree
    /// one in nominal terms on both of its paths: a profitable company
    /// through `eps * target_pe` and a loss-making one through
    /// `book * LOSS_MAKING_PRICE_TO_BOOK`. Scaling only earnings would
    /// make a loss-maker's fair value fall in real terms every year.
    ///
    /// # The clock, which is the part that is easy to get wrong
    ///
    /// `N` is integrated by the economy, which compounds `gdp` by
    /// `gdp_growth / 100 / 365` and `cpi` by `inflation_rate / 100 / 365`
    /// on every day it advances, while the market trades 252 days to a
    /// year and annualises by 252. The economy advances once per market
    /// day, so a certified year of 252 sessions takes 252 of those steps
    /// and delivers `252 / 365` of every annual rate: growth of 2.50 and
    /// inflation of 2.00 at the opening state give 4.50 per economy-year
    /// and 3.11 per trading year.
    ///
    /// This term states no rate of its own. It reads the level the economy
    /// reached, so whatever the macro chain integrated is what the
    /// valuation carries, and the rate falls with growth and inflation
    /// wherever the cycle takes them. Measured on
    /// `Universe.random(40, seed=111)` over 252 days at pt-v18, seeds 1 to
    /// 6, it delivers a median of +4.353 per cent per trading year, with
    /// mean growth of 3.353 and mean inflation of 2.931 over the run;
    /// `(3.353 + 2.931) * 252 / 365` is 4.339, which is the clock stated as
    /// a number.
    ///
    /// That 4.353 is a property of the opening expansion rather than of
    /// the model. On the same roster, seed 1, over 1008 days, the run
    /// leaves expansion and ends in a trough, and nominal output reaches a
    /// ratio of 1.0685, which is 1.67 per cent a year. The term goes below
    /// 1.0 whenever `gdp * cpi` falls under its opening value, which is
    /// growth plus inflation turning negative together. A flat premium
    /// would have paid the same rate through all of that, which is the
    /// reason this is a mechanism.
    ///
    /// # A company listed mid-run
    ///
    /// A company's stored `eps` is in the run's OPENING nominal terms,
    /// because that is what the ratio is taken against, so
    /// `Engine::list_instrument` on day 200 takes earnings stated at day
    /// zero rather than at day 200. A caller holding today's figure
    /// divides it by the ratio the snapshot gives, `gdp * cpi` over
    /// `nominal_output_base`. Every preset before pt-v18 holds that ratio
    /// at 1.0, where the two readings are the same number.
    ///
    /// # What it does NOT claim
    ///
    /// Earnings are a share of nominal output, and the share is a quantity
    /// this model does not carry. At 1.0 the share is held constant, which
    /// is the only value that is read off the process rather than chosen;
    /// below 1.0 the share falls every year and above 1.0 it rises for
    /// ever, both of which are assertions about an unmodelled quantity.
    ///
    /// A real price index also earns a return above nominal output growth,
    /// through buybacks and the drift of the earnings share, worth 3 to 4
    /// points a year. The model has nothing to derive that from, so this
    /// term does not attempt it and the gap is reported rather than
    /// closed.
    pub earnings_nominal_growth: f64,
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
    /// At 4.0 -- pt-v1 through pt-v11, and what this line called the
    /// shipped value until 2026-09-17; pt-v12 onward ship 12.0 -- a name
    /// that falls twelve percent trades exactly as much as one that falls
    /// four. Real markets do not do that: volume
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
    /// How much of a jump's share of the day's move the volume scale
    /// counts. Ships at 1.0, where the arithmetic is the shipped one.
    ///
    /// `price_magnitude` in market/tick.rs phase 3 is the day's move from
    /// the open, `|new_price - open| / open`, and a jump is in it whole. A
    /// jump lands on `mispricing_s` at the CLOSE and `reset_daily_prices`
    /// sets the next day's `open` from the price before it, so the gap
    /// trades in during the following session and the volume scale reads
    /// it as though the name had moved that far intraday. At
    /// `overnight_variance_ratio` 0.0, which every shipped preset carries,
    /// nothing reprices the open, so there is no session in which a jump
    /// is anything but an intraday move.
    ///
    /// That is the coupling §0.4 of the 2026-09-21 design note names: the
    /// model's idiosyncratic jump is five times the tape's size at a
    /// fortieth of its rate, and the rare huge private jumps are what hold
    /// `volume_change_acf1` inside its band. Take them out and the band is
    /// left; leave them in and the names' excess kurtosis is made by jumps
    /// the tape does not have. This dial separates the two: it decides
    /// whether the volume process is allowed to see a jump at all.
    ///
    /// At share `q` the day's move is measured from the open the name
    /// would have had if `(1 - q)` of the jump had gapped overnight:
    /// `open_eff = open * exp((1 - q) * j)`, with `j` the log jump the
    /// close booked into `s` -- read back from the jump slot of the
    /// attribution accumulator, which `apply_jumps` is the only writer of.
    /// At 0.0 the volume scale reads the DIFFUSION move alone, which is
    /// what a gap is: volume on a gap day is made at the open, not by the
    /// name travelling that distance through the book.
    ///
    /// UNDETERMINED. What would determine it is volume on jump days read
    /// off the tape -- the share of a gap day's volume that the gap itself
    /// explains -- and nobody has read it. 1.0 ships because it is the
    /// arithmetic that was there, not because it was chosen.
    pub volume_move_jump_share: f64,

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
    /// Kept, because a measured negative is worth keeping and a search may
    /// still find a use for it in a region this grid did not cover. But
    /// nothing should set it above zero on the strength of the argument
    /// that produced it. It did not stay inert, which this paragraph said
    /// until 2026-09-17: 0.0 is pt-v1 through pt-v14 and pt-v15 onward ship
    /// 0.374.
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
    /// How much of the market jump's log return joins the day's factor
    /// innovation, so the GJR variance update sees a crash day. Ships at
    /// 0.0, where nothing is added and the update is the shipped one.
    ///
    /// The market factor's variance steps on `day_factor`, the sum of the
    /// day's per-tick market factors (market/factor_vol.rs). A jump is not
    /// in it: `apply_jumps` writes the jump into each name's
    /// `mispricing_s` and nowhere else, so the fear channel sees it
    /// through `market_day_return_pct` and the variance never does. The
    /// index GJR the shipped coefficients come from was fitted on the
    /// tape's TOTAL index returns, jumps included, so a model whose
    /// variance update reads only the diffusion part is running that fit
    /// on a series it was not fitted to.
    ///
    /// At share `s` the day's shock gains `s * market`, `market` being the
    /// jump's log return -- the same number every name's `s` took. The
    /// compensator is deliberately NOT in it: `jump_mean_compensated`
    /// gives back a deterministic drift, the first moment, and a variance
    /// shock is a second moment. On a day no jump fires `market` is
    /// exactly 0.0 and nothing is added at all.
    ///
    /// DERIVED 1.0 by that argument, and shipped 0.0. The whole of the
    /// jump's return belongs in the shock because the whole of it was in
    /// the returns the coefficients were fitted to; a share between the
    /// two would be claiming the fit saw part of a crash day.
    ///
    /// # It has to land in the day the jump moved
    ///
    /// `apply_jumps` runs at the close, and `close_market` used to run it
    /// after the factor's own close had already consumed `day_factor` and
    /// zeroed it. Adding there would have put the jump in the NEXT day's
    /// shock, and `open_market` clears the accumulator in between, so it
    /// would have been thrown away instead. The call therefore sits
    /// immediately after the per-name closes and before the factor's, so
    /// the addition lands in the shock the same close computes. Nothing
    /// between the two reads or writes what `apply_jumps` touches, which
    /// is why the move costs no preset a bit.
    pub jump_market_variance_share: f64,
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
    /// The variance of the overnight move as a fraction of a session's,
    /// per name. Zero is every preset before this dial and is bit-identical.
    ///
    /// Nothing moved a price between sessions before it: the price after
    /// `open_market` was the price after the previous `close_market` on
    /// every name-night, so the engine's true overnight variance share was
    /// identically zero against a real 0.23 to 0.43 (the forty-name
    /// reference panel over nine non-crisis windows, median 0.33, by
    /// `tools/calibration/overnight_band.py`).
    ///
    /// Above zero `Engine::apply_overnight` does two things at each open,
    /// before the day's marks are set. It realises in the opening print
    /// the state that changed between the sessions, the close's jump on
    /// `s` and the macro step in fair value, rather than leaving them for
    /// the first ticks to settle toward; that is what gives the night its
    /// shape, since the reference panel's nights put 0.50 to 0.68 of their
    /// variance in the largest five percent against 0.36 to 0.49 for the
    /// sessions. And it adds a draw on `s` composed exactly as a session's
    /// factor structure is, beta on the market factor at its conditional
    /// daily sigma, the sector loading on the sector factor, the name's own
    /// GARCH sigma at its idiosyncratic scale, scaled by the square root of
    /// this dial; the composition is the session's because the panel's
    /// nights and sessions read the same cross-sectional correlation. The
    /// move reverts on the mispricing's own half-life and is kept out of
    /// the momentum roll, as the jump's carried share is.
    ///
    /// The session band anchors on the post-gap open, so the gap sits
    /// outside it, which is the intent market/mod.rs has stated since D6.
    /// A value is a ratio of variances, so 0.5 is a night carrying half a
    /// session's variance; with the jump realised at the open the share
    /// the row reads is more than the ratio alone would give.
    pub overnight_variance_ratio: f64,
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
    /// which is what pt-v1 through pt-v4 do and why they reproduce bit for
    /// bit; pt-v5 onward ship 0.0, where this sentence said "every shipped
    /// preset" until 2026-09-17. Below `1.0` the jump moves the momentum
    /// reference point with
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
    /// How the DOWN-side fear response falls with the VIX the session
    /// opened from: the spike is `gain * |r|^p * VIX^(-this)`.
    ///
    /// # The measurement
    ///
    /// `programme/results/vix-dynamics.md` (design repo), on ^GSPC and
    /// ^VIX 1990-01-03..2025-07-30, 8,959 aligned sessions. The VIX's
    /// session change in points regressed on the session return and the
    /// prior level, weighted by 1/VIX so the residual is in fractions of
    /// the level:
    ///
    /// ```text
    /// dVIX = a + kappa VIX + b_d |r|^p_d (VIX/20)^(-g_d)      r < 0
    ///      = a + kappa VIX - b_u |r|^p_u (VIX/20)^(-g_u)      r >= 0
    ///
    ///   down   b 0.990 +/- 0.067   p 1.442 +/- 0.076   g +0.492 +/- 0.124
    ///   up     b 0.961 +/- 0.072   p 0.596 +/- 0.037   g -0.852 +/- 0.122
    /// ```
    ///
    /// (calendar-year block bootstrap, 300 draws). The level-blind form
    /// pt-v1 through pt-v18 run -- `g = 0` on both sides, which this line
    /// called "every preset" until 2026-09-17 -- is refused at F(2,
    /// 8951) = 118, and the multiplicative "ratio" form `dVIX/VIX ~ r` is
    /// refused harder still (F = 588): on the down side the response in
    /// points FALLS with the level, as `VIX^(-0.49)`, and the level
    /// exponent is the same in every decade (0.47, 0.50, 0.47, 0.47 for the
    /// 1990s to the 2020s) while the scale `b` moves by +/- 20 per cent.
    ///
    /// # The form the tape supports, and what this dial's value is
    ///
    /// `g_d = p_d - 1` is inside the error bar (0.44 against 0.49 +/-
    /// 0.12; the restriction costs 0.15 per cent of the weighted SSR), and
    /// it is the STANDARDISED-RETURN form: `dVIX = gain VIX (|r| / VIX)^p`,
    /// the VIX responding in proportion to itself to the return measured in
    /// units of itself. It carries no reference level, so the gain has a
    /// meaning without one. The derived value is therefore
    /// `vix_return_exponent - 1` = **0.448** at the derived exponent 1.448,
    /// and the gain that reproduces the tape's same-day response is
    /// `3.754 / vix_mean_reversion` (the update transmits `mr` of the
    /// target spike on the day), less the part the identity's read-back
    /// already contributes -- both measured in the design note.
    ///
    /// 0.0 -- every preset up to pt-v19 -- is a branch, not arithmetic:
    /// `economy::daily::return_spike_at_level` returns `return_spike_for`
    /// there and the level is never read.
    pub vix_return_level_exponent: f64,
    /// The UP side's own exponent: the spike on an up session is
    /// `-gain_up * |r|^this * VIX^(-vix_return_level_exponent_up)`.
    ///
    /// Measured with the down side above: **0.596 +/- 0.037** whole span
    /// (0.49 to 0.66 by decade), and 0.543 in the restricted form this
    /// dial and its partner implement. The up side is CONCAVE -- a +2 per
    /// cent session lowers the VIX by less than twice what a +1 per cent
    /// one does -- where the earlier bucket-median fit that read 1.04 had
    /// no level in it, and big up sessions come at high levels. 1.0 is the
    /// linear form the arithmetic stood in and is bit-identical with the
    /// two level exponents at 0.0.
    pub vix_return_exponent_up: f64,
    /// How the UP-side response scales with the level. Measured
    /// **-0.852 +/- 0.122** (the response in points RISES with the level,
    /// nearly in proportion), against which the ratio form `-1.0` is
    /// within 1.2 standard errors and is what the restricted form carries:
    /// `-gain_up * VIX * |r|^p_u`, the VIX giving back a fraction of itself
    /// on an up session. The two sides are different laws -- surprise in
    /// units of priced volatility on the way up, proportional relief on the
    /// way down -- and the tape distinguishes them at F(2, 8951) = 43. 0.0
    /// is the level-blind branch.
    pub vix_return_level_exponent_up: f64,
    /// The VIX's own innovation, as a fraction of its level per session.
    ///
    /// # Why it exists
    ///
    /// The shipped noise is `N(0, 0.15)` POINTS -- under one per cent of the
    /// level -- and `vix_jump_intensity` is 0.0 on every preset, so the
    /// model's VIX is an image of its own index return: corr(dVIX, r)
    /// -0.977 against the tape's -0.804, R^2 0.954 against 0.646, the sd of
    /// the daily log change 0.105 against 0.064 and its excess kurtosis
    /// 0.16 against 2.66 (scorecard-coverage.md 3.1). Once the response
    /// form above is removed from the tape's dVIX, what is left is
    /// proportional to the level (`log|e| ~ 1.08 log VIX`, bootstrap sd
    /// 0.06) and its scale grows with the session's own size:
    ///
    /// ```text
    /// sd(e / VIX | r) = sqrt(s0^2 + (c r)^2)
    ///   s0 = 0.0331 [0.0316, 0.0346]   c = 0.0179 [0.0135, 0.0232] per per cent
    /// ```
    ///
    /// (Gaussian MLE on the within-window residual of 35 windows, window
    /// bootstrap). This dial is `s0`; the partner
    /// `vix_innovation_return_sigma` is `c`. The residual is the WITHIN-
    /// window one because a model with fixed coefficients has no
    /// between-year variation of its response to supply; the whole-span
    /// residual (0.0341, 0.0253) counts that variation as innovation and a
    /// model given it lands below the tape's median window on R^2.
    ///
    /// At (0.0, 0.0) the draw's scale is `0.15 * volatility` by the same
    /// expression, and the draw is the same draw, so every preset up to
    /// pt-v19 reproduces to the bit.
    pub vix_innovation_sigma: f64,
    /// The part of the innovation scale that rises with the session return,
    /// per per cent: see `vix_innovation_sigma`. Measured 0.0179 [0.0135,
    /// 0.0232]. What it buys, on the tape's own returns: the per-window
    /// excess kurtosis of the daily log VIX change reads 1.48 with `s0`
    /// alone and 2.09 with both, against 2.66 measured; and it is the
    /// component the whole-span kurtosis of 6.9 mostly comes from.
    pub vix_innovation_return_sigma: f64,
    /// A fear event's mean size in units of the day's innovation scale
    /// (`VIX * sqrt(s0^2 + (c r)^2)`, or `VIX` when the innovation dials
    /// are off), exponential draw. Non-zero selects that unit over
    /// `vix_jump_scale`'s points.
    ///
    /// Derived by cumulant inversion on the scale-standardised within-
    /// window residual `z`: with `kappa_3 = 6 lam mu^3` and `kappa_4 = 24
    /// lam mu^4` for a Poisson(lam) count of Exp(mu) jumps on a Gaussian,
    /// `mu = kappa_4 / (4 kappa_3)` = **1.70** and `lam = kappa_3 / (6
    /// mu^3)` = 0.0089 a day (2.24 a year), leaving the Gaussian 94.9 per
    /// cent of the variance. The closed form is checked against 2-D
    /// quadrature of the density to 2e-13 with a negative control that
    /// fails at 2e-2. The skew of `z` is 0.26 +/- 0.04 and its excess
    /// kurtosis 1.77 +/- 0.21 (window bootstrap), so the innovation is not
    /// Gaussian at seven standard errors; the rate's own error bar is wide
    /// (bootstrap 0.05 to 3.8 a year) because two decades hold most of the
    /// events.
    pub vix_jump_level_scale: f64,
    /// The part of the fear-event arrival rate, per YEAR per per cent of
    /// DOWN session, that rises with the session: the daily probability is
    /// `(vix_jump_intensity + this * max(0, -r)) / 252`. The tape's
    /// residual skew on down sessions rises with the size of the session
    /// (0.38, 0.46, 0.59, 1.76 for |r| under 0.5, 0.5-1, 1-2, 2-3 per cent
    /// within windows) and is flat on up sessions, so the events cluster
    /// with the crashes rather than arriving on their own clock. At the
    /// derived mean rate of 2.24 a year spread this way the value is
    /// **6.2** (0.0246 a day per per cent). Non-zero takes the arrival
    /// draw; with both intensities at 0.0 no draw is taken and the schedule
    /// is the shipped one.
    pub vix_jump_return_intensity: f64,
    /// The per-sector variance state's shock share: the sector factor's
    /// daily variance is `T * s`, with `T` the VIX-coupled sigma squared
    /// the stateless draw uses (`tick::sector_sigma_at`) and `s` a
    /// symmetric GARCH(1,1) of unconditional mean 1 on the factor
    /// standardised by `T`: `s' = (1 - a - b) + a d^2 / T + b s`, `d` the
    /// day's accumulated sector factor, `s` clamped to the per-name
    /// multiples. The ratio form keeps the coupling's own state whole (at
    /// `sector_vix_coupling` 1.0 the target already tracks the VIX) and
    /// adds the sector's memory net of it. Either dial non-zero switches
    /// the state on; at (0.0, 0.0) the tick draws at `sector_sigma_at`
    /// exactly as before and the read-back prices the same scalar.
    ///
    /// MEASURED (`programme/results/vix-dynamics.md` 19.7): a GARCH(1,1)
    /// QMLE on each of seven sectors' market residuals of the 40-name
    /// reference panel, 2015-2025, divided by the VIX close the session
    /// opened from over its median, reads alpha **0.067 +/- 0.043**, beta
    /// 0.837 +/- 0.111, persistence 0.904 +/- 0.069 (half-life about
    /// seven sessions). The plain fit (19.1: 0.063 / 0.908, persistence
    /// 0.971) absorbs the VIX's own level into beta; composed additively
    /// on the coupled target it smoothed that channel away and the
    /// clustering rows FELL (`vixdyn8`).
    pub sector_vol_alpha: f64,
    /// The per-sector variance state's persistence term, **0.837** by the
    /// same measurement. See [`ModelParams::sector_vol_alpha`].
    pub sector_vol_beta: f64,
    /// Self-excitation of a name's idiosyncratic jumps: after a jump the
    /// name's arrival rate is `lambda (1 + h)` with `h' = decay h + this`.
    /// 0.0 -- every preset up to pt-v19 -- is a branch: the rate is
    /// `lambda` and the state is never read or written; the arrival test
    /// takes the same uniform per name per session at every rate, so no
    /// draw moves either way.
    ///
    /// MEASURED (vix-dynamics.md 19.1): on the reference panel the rate of
    /// a 3-sd idiosyncratic move at lag k after one reads 3.29, 1.84, 2.13,
    /// 1.68, 1.58 times the base at k = 1..5 and 1.0 by k = 12, fitted as
    /// `1 + 2.0 x 0.72^(k-1)`: amplitude **2.0** [1.5, 2.8], branching ratio
    /// 0.13 (stationary by a wide margin). The model's independent arrivals
    /// read a lag-one ratio of 1.7 with no memory past a day.
    pub jump_idio_excitation: f64,
    /// The excitation's daily decay, **0.72** [0.48, 0.79] (half-life two
    /// sessions) by the same measurement. Unread while
    /// [`ModelParams::jump_idio_excitation`] is 0.0.
    pub jump_idio_excitation_decay: f64,
    /// Takes the VIX-squared scaling (`jump_vix_coupling`) off the
    /// IDIOSYNCRATIC arrival rate, leaving it on the market jump. 0.0 --
    /// pt-v1 through pt-v18, which this line called "every preset" until
    /// 2026-09-17 -- keeps the arithmetic that predates the dial; pt-v19
    /// ships 0.0 again since the 2026-09-20 recomposition, having carried
    /// 1.0 from 2026-09-14. Non-zero is a switch.
    ///
    /// MEASURED (vix-dynamics.md 19.1): the panel's idiosyncratic jump rate,
    /// in units of the name's own trailing sd, reads `var^-0.20` against the
    /// name's own variance and `var^0.05` against the market's -- flat, not
    /// squared. The coupling was an unprovenanced constant on this rate, in
    /// the family the crisis blend's gain belonged to.
    pub jump_idio_vix_decoupled: f64,
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
    /// How much of the VIX's target is the market's own volatility.
    ///
    /// 0.0 -- pt-v1 through pt-v15 -- is bit-identical by branch, and this
    /// line said "Shipped 0.0" until 2026-09-17; pt-v16 onward ship 0.3.
    /// At zero the VIX is a
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
    /// second calibration constant. The VIX clamp -- 10 to `vix_ceiling`,
    /// which is 80 through pt-v18 and 181.3295 on pt-v19, where this line
    /// said "10 to 80" until 2026-09-17 -- and the factor's ceiling
    /// multiple bound the feedback.
    pub vix_realised_vol_weight: f64,
    /// The VIX's LEVEL comes from the index's own conditional variance, not
    /// from a table of constants. 0.0 -- pt-v1 through pt-v18 -- is the
    /// table, bit for bit; pt-v19 ships 1.0. This line read "0.0 ships and
    /// is every shipped preset" until 2026-09-17.
    ///
    /// # What the level was made of
    ///
    /// At `vix_realised_vol_weight` 0.3 the target was
    ///
    /// ```text
    /// target = 0.7 * [ phase(19 + 0.85 (p - 19))     the business cycle
    ///                + spike(gain * |r|)             the day's return
    ///                + 0.5 on the first half of a month
    ///                + offset ]
    ///        + 0.3 *   anchor * sigma_f / market_factor_sigma
    /// ```
    ///
    /// so ABOUT TWO THIRDS OF THE LEVEL WAS CONSTANTS and their
    /// cancellations — a phase table shrunk toward 19, an offset cancelling
    /// the standing excursion an asymmetric gain injects, an earnings bump
    /// — and the remaining third was the market factor's sigma read through
    /// a conversion of 2105.1 VIX points per unit of daily sigma where the
    /// identity is `100 * sqrt(252)` = 1587.5. Measured, at the arm nearest
    /// the candidate: mean VIX 13.76 on an index realising 15.60 per cent
    /// annualised, a ratio of 0.882 where the tape's is 1.252.
    ///
    /// And the referent was wrong twice over. The index is not the factor:
    /// on the certified roster it carries 2.05x the factor's variance —
    /// factor 55 per cent, jumps 23, sector and idiosyncratic 11, the
    /// intraday curve 4.6, news 3 — and none of the rest reached the VIX at
    /// any value of any dial.
    ///
    /// # What it is at 1.0
    ///
    /// ```text
    /// target = (1 + pi) * 100 * sqrt(252 * V_t) + spike(r) - E[spike | sigma_t]
    /// ```
    ///
    /// with `V_t` the engine's own one-day-ahead conditional variance of the
    /// cap-weighted index ([`crate::market::index_var`]), computed from the
    /// states it already holds at the close, and the excursion made
    /// zero-mean by its own closed form rather than by a fitted offset.
    /// `pi` is [`ModelParams::vix_variance_premium`].
    ///
    /// Three things follow, and they are the point of the change rather
    /// than side effects:
    ///
    /// - `market_vol_vix_anchor` is DERIVED, from the same identity at the
    ///   unconditional point, and the dial is not read at all. The forward
    ///   map `base * (1 - c + c (VIX/anchor)^2)` and the read-back then
    ///   agree at that point by construction — the property the old
    ///   comment claimed ("so the loop is consistent") and the two
    ///   constants never delivered.
    /// - The phase table, `vix_cycle_amplitude`, `vix_target_offset`, the
    ///   earnings bump and `vix_realised_vol_weight` are not read. The
    ///   business cycle reaches the VIX through the variance processes or
    ///   not at all.
    /// - The spike reads the SESSION's return whatever `vix_return_source`
    ///   says, because the zero-mean correction is computed against the
    ///   session's conditional sigma and a correction sized for the day
    ///   applied to a closing minute would be twenty times too large.
    ///
    /// `vix_mean_reversion`, `vix_decay_ratio`, `vix_return_gain`,
    /// `vix_return_gain_up`, `vix_return_clamp` and `vix_target_shock_cap`
    /// all keep their jobs: they are the fear channel, not the level. The
    /// inflation and shock adders keep theirs too, so a scripted macro path
    /// reaches the VIX exactly as it did, and a PINNED VIX still overrides
    /// the state and drives variance through the forward map unchanged.
    ///
    /// # Stationarity, since this closes the loop
    ///
    /// Write `s_f` for the share of the unconditional index variance that
    /// follows the VIX and `c` for the coupling. The factor's target is
    /// `base (1 - c + c V_t / V_uncond)`; substituting
    /// `V_t = s_f V_uncond v_f / base + (1 - s_f) V_uncond` gives a fixed
    /// point at `v_f = base` EXACTLY, independent of `s_f`, and an
    /// effective persistence of `(alpha + beta) + (1 - alpha - beta) c s_f`
    /// — about 0.99 at the shipped coefficients. The level is held by the
    /// variance processes' own reversion, weakened but not removed, which
    /// is where a real index's long-run variance lives.
    pub vix_level_identity: f64,
    /// The variance risk premium `pi`: how far a real VIX sits ABOVE the
    /// realised volatility of its own index. Read only while
    /// [`ModelParams::vix_level_identity`] is non-zero.
    ///
    /// # Equality was the wrong identity, and this is the size of it
    ///
    /// MEASURED on ^GSPC and ^VIX adjusted closes, 1990-01-03 to
    /// 2025-07-30, 8,959 aligned sessions with a return, by
    /// `programme/scripts/vix-rv-relation.py` in the design repository.
    /// Five estimators, because the answer depends on which one the model's
    /// own statistic corresponds to:
    ///
    /// | estimator | VIX / RV |
    /// |---|---|
    /// | pooled over the whole span, one history | 1.076 |
    /// | per calendar year, 35 years | median 1.252, IQR 1.128-1.398 |
    /// | rolling 252-session windows, 415 of them | median 1.257, P10 1.049, P90 1.473 |
    /// | against the NEXT 21 sessions' realised vol | median 1.398 |
    /// | against the TRAILING 21 sessions | median 1.372, correlation 0.853 |
    ///
    /// Three of 35 calendar years read below 1.0 (2008 at 0.80, 2020 at
    /// 0.85, 2018 at 0.98); the other 32 sit above it, and 92.5 per cent of
    /// rolling windows do.
    ///
    /// **The value is 0.252 and the residual is the per-year IQR, 1.128 to
    /// 1.398, so +/- 0.13 on `pi`.** The window estimator is the
    /// like-for-like one: the certified panel's statistic is a per-window
    /// relationship on a 252-session window, which is what estimators B and
    /// C measure. The pooled 1.076 is recorded so the choice is visible; it
    /// does not change any conclusion, because the model reads 0.88.
    ///
    /// # Why the default is the measured value and not zero
    ///
    /// Every other dial in this era ships at the value that makes it inert.
    /// This one is not read at all while `vix_level_identity` is 0.0, so
    /// both defaults are equally inert and the choice is about what a
    /// preset that turns the identity on gets without saying anything.
    /// Zero would be the EQUALITY ruler, which the measurement above puts
    /// at 1.42x wrong. A default that is a refuted identity is a chosen
    /// constant; the measured one is not.
    ///
    /// # What could not be determined
    ///
    /// The premium's FORM. By VIX level the difference grows from +3.3
    /// points below VIX 12 to +8.9 above 30 while the ratio holds at 1.44,
    /// 1.42, 1.44, 1.37, 1.35, 1.35 from 0 to 40 and falls to 1.23 only
    /// above 40; by realised-vol tercile of years the ratio reads 1.40,
    /// 1.24, 1.16 from calm to violent while the difference reads +4.1,
    /// +3.0, +4.2. Neither form is exact. The ratio is the more stable one
    /// over the range this model lives in and is the one used; a
    /// state-dependent premium is not supported by this measurement and is
    /// not fitted here.
    pub vix_variance_premium: f64,
    /// Which return the VIX reacts to: the last TICK's (0.0, pt-v1 through
    /// pt-v8) or the day's (1.0, pt-v9 onward), blended in between. This
    /// line called 0.0 the shipped value until 2026-09-17.
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
    /// At 1.0 the five constants are used as they are -- pt-v1 through
    /// pt-v8, which this line called "shipped" until 2026-09-17. The shipped
    /// value since is 0.6 at pt-v9, 0.0 from pt-v10 to pt-v15 and 0.85 from
    /// pt-v16 on. At `a` each
    /// is pulled toward their mean of 19.0: `19.0 + a * (phase - 19.0)`, so
    /// 0.0 makes the cycle contribute nothing to the VIX and any episodes
    /// have to come from the market. Combines with `vix_return_source`, which
    /// is what supplies episodes in the first place (§70, §71).
    pub vix_cycle_amplitude: f64,
    /// VIX points added to its target per unit of a DOWN day's index
    /// return, before the clamp and cap below.
    ///
    /// 25.0, a literal in the VIX update, is pt-v1 through pt-v8 and what
    /// this paragraph called "shipped" until 2026-09-17; pt-v9 to pt-v18
    /// carry 17.0 and pt-v19 8.83, the scale of the power law
    /// `vix_return_exponent` makes of it. MEASURED against real
    /// markets (FRED VIXCLS and SP500, 2,511 common days to 2026-08): a
    /// session at -3% or worse moves the VIX a median of +6.03 points, and
    /// -2% to -1% moves it +1.95. The 25.0 gain with the 0.03 clamp beside
    /// it added at most 0.75 points to the TARGET, of which the day traverses
    /// `vix_mean_reversion`, about 0.09 points. Raising it to the real slope
    /// is NOT the lever, measured: it moves the within-year VIX sd from 1.54
    /// to 1.79 against a real 4.0 and leaves lag-5 clustering where it was
    /// (§68). What was missing is the feedback above, not the gain.
    pub vix_return_gain: f64,
    /// The same for an UP day. 10.0 -- pt-v1 through pt-v8, and what this
    /// line called shipped until 2026-09-17; pt-v9 to pt-v18 ship 17.0 and
    /// pt-v19 0.049 under the ratio form
    /// ([`ModelParams::vix_return_level_exponent_up`] -1.0).
    ///
    /// # The "about half" this used to claim has no provenance, and is wrong
    ///
    /// This docstring read: "the real response to a +2% day is about half
    /// the size of the response to -2%, which the shipped 2.5:1 ratio is
    /// already close to." That sentence names no series, no window, no
    /// sample size and no estimator, and it sits directly beneath
    /// `vix_return_gain`'s claim, which names all four -- so it read as
    /// though it inherited that provenance when it had none of its own.
    /// It was the only statement of a real up-to-down ratio anywhere in
    /// the tree, and a derivation was built on it.
    ///
    /// MEASURED, on ^GSPC and ^VIX over 8,960 aligned sessions, by
    /// fitting each side separately and evaluating the two curves:
    ///
    /// | move | down response | up response | up / down |
    /// |---|---|---|---|
    /// | 1% | 1.003 | 0.897 | 0.89 |
    /// | 3% | 3.73 | 2.80 | 0.75 |
    /// | 6% | 8.58 | 5.75 | 0.67 |
    ///
    /// So the real ratio is 0.67 to 0.89, not 0.5, and the model's fear
    /// asymmetry is about TWICE the real one rather than close to it.
    ///
    /// And the ratio is NOT CONSTANT, because the two sides have
    /// different exponents -- 1.1996 down against 1.0410 up. **No single
    /// value of this dial can express a ratio that varies with the size
    /// of the move.** That is the same class of defect as the linear
    /// response the exponent fixes: the parameter's form cannot represent
    /// the quantity it names. Expressing it properly needs an up-side
    /// scale set against the down-side scale at the fit level, 0.894, and
    /// the residual variation left over is the up side's own exponent,
    /// which this data cannot resolve.
    pub vix_return_gain_up: f64,
    /// The EXPONENT of the return-to-fear response. 1.0 -- pt-v1 through
    /// pt-v18 -- is the linear form, bit-identical to the arithmetic that
    /// stood here; pt-v19 ships 1.4483, and this line read "1.0 ships"
    /// until 2026-09-17.
    ///
    /// # Why a linear gain cannot be calibrated
    ///
    /// The real response of implied volatility to a session return is
    /// CONVEX, and a gain multiplies every size of move by the same
    /// factor, so no value of `vix_return_gain` can reproduce it. Measured
    /// on ^GSPC and ^VIX, 8,960 sessions aligned on dates both series
    /// report, 1990-01-03 to 2025-07-31, down sessions bucketed with each
    /// bucket represented by its OWN median rather than its label:
    ///
    /// | median \|r\| | median dVIX | points per 1% |
    /// |---|---|---|
    /// | 0.710 | 0.710 | 1.00 |
    /// | 1.211 | 1.255 | 1.04 |
    /// | 1.704 | 1.890 | 1.11 |
    /// | 2.234 | 2.440 | 1.09 |
    /// | 2.746 | 3.040 | 1.11 |
    /// | 3.360 | 4.530 | 1.35 |
    /// | 4.415 | 6.040 | 1.37 |
    /// | 6.390 | 9.830 | 1.54 |
    ///
    /// The points-per-1% column rises monotonically from 1.00 to 1.54, so
    /// the convexity is visible before any fit. Least squares in logs on
    /// the eight bucket medians gives `dVIX = 1.003 * |r|^1.200` at an R
    /// squared of 0.9947. A linear gain is the `p = 1` special case and
    /// the tape rejects it.
    ///
    /// # The exponent's error bar, which it must not ship without
    ///
    /// Refitting those medians: residual sd 0.0671 in logs, which is 6.9
    /// per cent in `dVIX`, worst bucket -9.8 per cent; standard error of
    /// the exponent 0.0357, so a 95 per cent interval of **1.112 to
    /// 1.287** on six degrees of freedom.
    ///
    /// A SECOND and larger uncertainty is the weighting. The fit above is
    /// unweighted over buckets holding 22 to 994 sessions -- the shallow
    /// buckets carry forty-five times the sessions of the deep ones -- and
    /// weighting by count gives **1.132** instead. Neither is wrong:
    /// unweighted asks what shape the curve has, count-weighted asks what
    /// shape a typical session sees. The first is the right question for a
    /// tail, so 1.200 is the value here, but the choice is a choice and
    /// the range it spans is worth 8 per cent of the response at -3% and
    /// 13 per cent at -6.4%.
    ///
    /// # What changes when this is not 1.0
    ///
    /// `vix_return_gain` stops being a gain and becomes the SCALE of a
    /// power law, and its units change from VIX points per per-cent to
    /// VIX points per per-cent to the p. The scale that reproduces the
    /// real curve is `1.003 / c`, where `c` is the measured transmission
    /// from a point of VIX TARGET to a point of same-day VIX.
    ///
    /// `vix_target_shock_cap` is a boundary condition on the spike, so it
    /// moves with the form too: the largest spike the model can produce
    /// becomes `scale * vix_return_clamp^p` rather than
    /// `gain * vix_return_clamp`.
    ///
    /// # The up side was assumed, then measured, and the assumption lost
    ///
    /// The first version of this dial applied one exponent to BOTH sides
    /// and said so as an assumption. The up side has since been fitted on
    /// the same 8,960 sessions with the same alignment, the same
    /// estimator and the same buckets, taking `-dVIX` on up sessions
    /// (`programme/scripts/vix-updown-fit.py` in the design repo, and the
    /// down fit reproduces the figures above exactly, which is what makes
    /// the instrument trustworthy before it is used on new data):
    ///
    /// | side | scale | exponent | R squared | worst bucket |
    /// |---|---|---|---|---|
    /// | down | 1.0033 | 1.1996 | 0.9947 | 9.8% |
    /// | up | 0.8965 | 1.0410 | 0.9655 | 35.0% |
    ///
    /// **The down side is convex and the up side is very nearly linear**,
    /// so ONE exponent across both would have been wrong on the up side
    /// to buy nothing. The exponent is therefore the down side's alone;
    /// `return_spike_for` never reaches an up session with it and a test
    /// pins that at every exponent.
    ///
    /// The up side keeps the LINEAR form rather than shipping 1.0410,
    /// because at an R squared of 0.9655 with a worst bucket missing by
    /// 35 per cent on 23 sessions this data cannot tell 1.0410 from 1.0.
    /// The DIRECTION is robust across every bucket; the fourth decimal is
    /// not, and shipping it would be a chosen constant wearing a
    /// measurement's clothes.
    ///
    /// **0.8965 IS NOT `vix_return_gain_up`'s TARGET**, and the table
    /// above is the easiest place in this file to think it is. It is the
    /// scale of a `p = 1.041` power fit, and no dial in this tree
    /// implements that form. The quantity the up side's dial reproduces
    /// is the scale of a fit with the exponent PINNED AT 1, which is what
    /// the code runs. Refitting the same eight up buckets that way gives
    /// **0.9278** -- residual sd 0.1425 in logs, 14.2 per cent in `dVIX`;
    /// standard error of the mean 0.0504, so a 95 per cent interval of
    /// 0.824 to 1.045 on seven degrees of freedom; worst bucket 38.5 per
    /// cent; count-weighted 0.8904. The same refit on the DOWN buckets
    /// gives 1.1872 with a worst residual of 29.6 per cent against the
    /// power form's 9.8, which is the convexity stated in the units a
    /// linear dial would have to work in.
    ///
    /// # The zero-mean correction moves with this dial
    ///
    /// Under [`ModelParams::vix_level_identity`] the standing excursion
    /// an asymmetric response injects is cancelled by its own closed form
    /// rather than by a fitted offset, and that closed form is the
    /// response's FIRST MOMENT. A power form changes it: see
    /// [`crate::economy::daily::expected_return_spike`], which carries
    /// this exponent for that reason. The two dials are independent of
    /// each other -- either ships alone -- but a tree that has one
    /// reaching the spike and not the correction cancels the wrong
    /// quantity, by a factor that scales as `sigma^(p - 1)` and is
    /// therefore right at exactly one volatility.
    pub vix_return_exponent: f64,
    /// The index return is clamped to +/- this before it drives the VIX.
    ///
    /// 0.03 -- pt-v1 through pt-v8 -- makes a -10% day and a -3% day
    /// produce identical fear, and a crash is exactly where that assumption
    /// is worst. It is not the shipped value, where this line said "Shipped
    /// 0.03" until 2026-09-17: pt-v9 onward ship 15.0, in the percentage
    /// points the day-return source (`vix_return_source` 1.0) works in.
    pub vix_return_clamp: f64,
    /// Ceiling on the VIX target's whole excursion, in points: the return
    /// spike plus the inflation and shock adjustments. 12.0 -- pt-v1
    /// through pt-v8, and what this line called shipped until 2026-09-17 --
    /// binds long before a real crisis does; pt-v9 to pt-v18 ship 45.0 and
    /// pt-v19 158.8524, both of which the section below accounts for.
    ///
    /// # It was a shape parameter, and pt-v19 retires it
    ///
    /// At 45.0 against a `vix_return_gain` of 17.0 it BOUND at 2.647 per
    /// cent of session return — deep inside the 6.39 per cent the tape
    /// supplies a conditional median for — so a -2.7 per cent session and a
    /// -6.4 per cent one produced identical fear. That is a shape, not a
    /// boundary, and `economy::daily::fear_response_shape` is the guard
    /// that made every preset declare it.
    ///
    /// **The loop-gain run says what the cap was actually doing**
    /// (`loopgain-report.md` §8.2): it was "compensating for a read-back
    /// that omits the crisis blend". The index realised 4.0 to 4.9 times the
    /// variance `V_t` priced above `crisis_vix_threshold` and 1.2 to 1.4
    /// below it, so the fear arm had nothing to balance it above the
    /// threshold and the cap was the brake on the divergence. With
    /// `market::index_var` pricing its own regime that brake has a
    /// mechanism to hold it instead, and the dial goes back to being the
    /// boundary condition it is documented as.
    ///
    /// pt-v19 therefore sets it to the SUPREMUM of the spike over the domain
    /// the update admits — `vix_return_gain * clamp^vix_return_exponent *
    /// floor^-vix_return_level_exponent`, where the floor is the 10.0 of the
    /// state clamp. **That is a derived value and not a tuned one**: the
    /// return is already bounded one step earlier and the spike FALLS with
    /// the level, so at this value the cap cannot bind anywhere the clamp
    /// does not and the pair has one binding constraint between them instead
    /// of two. Under the level-blind law (`vix_return_level_exponent` 0) the
    /// same expression is the product `gain * clamp`, which is the 255.0 at
    /// 17.0 and 15.0 the earlier text quoted; under pt-v19's composed law it
    /// is `8.83 * 15^1.4483 * 10^-0.4483` = 158.8524.
    ///
    /// **What the cap is NOT ordered against is `vix_ceiling`.** That
    /// invariant was withdrawn on 2026-09-14: the cap truncates an additive
    /// term of the TARGET and the ceiling truncates the STATE, so neither
    /// binds "first" at any pair of values, and a cap under the ceiling
    /// makes the ceiling less sticky rather than more. See
    /// `ModelParams::pt_v19` at the cap's own assignment and
    /// `programme/results/ceiling-and-omega.md` 3 in the design repository.
    ///
    /// The value is asserted against its own derivation by
    /// `the_default_cap_is_the_clamps_own_image`. That test computed the
    /// LEVEL-BLIND spelling `gain * clamp^p` = 445.9577 and was red on
    /// pt-v19 until 2026-09-14; it now computes the image WITH the level
    /// in it, `gain * clamp^p * floor^(-g)` = 158.85236 at the VIX floor
    /// of 10, which is the spike's supremum over the domain the update
    /// admits. See `programme/results/fear-response-shape.md`.
    pub vix_target_shock_cap: f64,
    /// Upper bound on the VIX state itself, in points.
    ///
    /// NOT a chosen constant on pt-v19, and this entry said otherwise until
    /// 2026-09-17. `provenance.py` records it `derived`: a LOWER BOUND solved
    /// on a simulated pin ladder at tolerance 8.64, not a closed form over the
    /// vector -- so it cannot be recomputed from the other dials the way
    /// `vix_target_shock_cap` above can. pt-v19 ships 181.3295; the 80.0 this
    /// paragraph used to call "the shipped value" is pt-v18's, and it
    /// reproduced the literal the dial replaced.
    ///
    /// Declared here so a reader can disagree with it, which is still the
    /// point -- but disagreeing now means disputing the ladder, not a taste.
    ///
    /// Two facts decide what it may be set to. The real index closed at 82.69
    /// on 2020-03-16, so any bound below that makes a peak target derived
    /// from that day unreachable by construction. And the derived response
    /// peaks at 93.0 when driven over the 2020 tape with the ceiling off, so
    /// a bound of at least 93 is INERT ON THAT TAPE and one below it
    /// truncates. That 93 is a statement about one year rather than a bound
    /// on the process, and a different tape would move it.
    pub vix_ceiling: f64,
    /// A constant added to the VIX target, in points. Zero ships and is inert.
    ///
    /// It exists because an ASYMMETRIC return gain puts a standing positive
    /// excursion on the target: the down side is worth more per unit than the
    /// up side and a session is equally likely either way, so the mean
    /// excursion is `0.5 * mean|r| * (vix_return_gain - vix_return_gain_up)`
    /// and the level it adds is that times `1 - vix_realised_vol_weight`. On
    /// the 2020 tape that is 10.5 points of excursion and 7.34 of level,
    /// against a measured overshoot of 6.38.
    ///
    /// THE LIMITATION, because whoever next changes this market's volatility
    /// will change the bias this cancels and will not otherwise know. The
    /// bias is proportional to the mean ABSOLUTE session return, which is a
    /// property of the run rather than of the model, so one constant cancels
    /// it exactly at one value of `mean|r|` and approximately elsewhere. A
    /// calm year carries a smaller bias than a crisis year and the same
    /// offset over-corrects it.
    pub vix_target_offset: f64,
    /// VIX level at which crisis behaviour begins.
    ///
    /// Gates the sector-to-market correlation blend, the universe stress
    /// memory and the economy's crisis premium -- three mechanisms whose
    /// trigger point nobody has ever been able to search. 25.5 is the P94 of
    /// the long-run endogenous VIX distribution, chosen so the trigger is
    /// reachable at all; whether it is the RIGHT point is a different
    /// question and now an answerable one.
    pub crisis_vix_threshold: f64,
    /// How much more volatile the crisis EPICENTRE's names are than the
    /// other sectors' at the same VIX. 0.0 -- every preset before pt-v19's
    /// fourth composition of 2026-09-22, which ships 1.93 -- takes the branch not
    /// taken: no episode is tracked, no epicentre is drawn, no draw is
    /// taken on any stream and every preset is bit-identical. DERIVED 1.93.
    ///
    /// # What it is
    ///
    /// A crisis EPISODE starts on the session whose VIX is above
    /// `crisis_vix_threshold` with no episode running, and ends after
    /// `crisis_epicentre_end_sessions` consecutive sessions back under it.
    /// At the start of each episode one epicentre is drawn from the sector
    /// table's [`crate::sectors::Sector::crisis_weight`], `none` being the
    /// remainder and a draw in its own right, on
    /// [`crate::rng::stream::CRISIS_EPICENTRE`]. While the episode runs,
    /// the names in that sector carry an extra multiple on the parts of
    /// their return that are NOT the market factor -- the sector leg and
    /// their own idiosyncratic noise, both in
    /// `market::factors::calculate_live_factors` -- and every OTHER name
    /// carries a multiple below one on the same two parts. The market
    /// component is untouched, so the index, the VIX and the fear rows do
    /// not move -- only who carries the crisis moves.
    ///
    /// # It redistributes rather than adds
    ///
    /// The second multiple is what makes this dial a statement about WHO
    /// carries a crisis and not a statement about how large crises are. At a
    /// given VIX the roster's total crisis variance is the VIX's to set:
    /// `crisis_blend_*`, the GARCH and the market factor set it, and this
    /// dial has no business moving it. So the pair of multiples is solved
    /// under a conservation condition -- the roster's MEAN non-market
    /// variance is unchanged -- alongside the tape's ratio.
    ///
    /// It was built the other way first, on 2026-09-22: the epicentre's
    /// names were lifted and no one was lowered. That arm moved the row it
    /// was built for (`crisis_sector_dispersion` 1.15 to 1.39 against the
    /// tape's 1.34 at two years) and moved three rows away with it --
    /// `annualised_vol_pct` 24.3 to 25.5 against 23.7,
    /// `cross_sectional_corr` 0.330 to 0.314 against 0.353,
    /// `volume_abs_return_corr` 0.535 to 0.545 -- and the three were one
    /// cause: a roster whose total crisis variance had risen. It was refused
    /// (`results/ptv19epi2`, design repository). The additive arm is kept as
    /// the `w = 0` edge of this solve rather than as a second form; see
    /// [`crate::market::factors::CRISIS_EPICENTRE_SECTOR_SHARE`].
    ///
    /// # Derivation
    ///
    /// Five crisis episodes on the tape, 39 real names of the roster mapped
    /// to the engine's sector keys, each sector's median episode volatility
    /// over each name's own calm-day volatility (VIX under 12), then each
    /// sector relative to the median sector of that episode
    /// (`results/ptv19refine/epicentre-derivation.json`, design repository).
    /// The rule was stated before the numbers were read: the epicentre is
    /// the sector furthest above the episode's median if it is 1.3x or more
    /// above it. 2008-09 gives financial services at 2.43, 2011 financial
    /// services at 1.93, 2020 financial services at 1.41; 2000-02 and 2022
    /// have none. The MEDIAN of the three ratios is 1.93, and that is this
    /// dial: nothing was fitted to a graded row.
    ///
    /// # The multiples the code applies, and why neither is this number
    ///
    /// This dial is stated on a name's TOTAL volatility, which is what the
    /// tape measures. The code can only scale the non-market parts, so the
    /// pair applied to them is solved from two measured shares: `m`, the
    /// market factor's share of a name's variance, and `w`, the epicentre
    /// sector's share of the roster's non-market variance.
    ///
    /// ```text
    /// (a)  m + (1 - m) g_up^2  =  e^2 ( m + (1 - m) g_down^2 )
    /// (b)  w g_up^2 + (1 - w) g_down^2  =  1
    ///
    /// g_down^2 = [ (1 - m) - m w A ] / [ (1 - m) (1 + w A) ]   , A = e^2 - 1
    /// g_up^2   = m A / (1 - m) + e^2 g_down^2
    /// ```
    ///
    /// `m` is [`CRISIS_EPICENTRE_MARKET_SHARE`] and `w` is
    /// [`CRISIS_EPICENTRE_SECTOR_SHARE`], both MEASURED on the composed
    /// pt-v19 and recorded there with their recipes. At `m = 0.3916`,
    /// `w = 0.1019` and `e = 1.93` that is `g_down^2 = 0.4996655 /
    /// 0.7773328 = 0.642795`, `g_down = 0.801745`, and `g_up^2 = 1.753897 +
    /// 2.394346 = 4.148243`, `g_up = 2.036724`. See
    /// [`crate::market::factors::crisis_epicentre_gains`], which is where
    /// the solve lives, and
    /// [`crate::market::factors::crisis_epicentre_extra_bounds`], which is
    /// where the two extras that would ask a name for a negative variance
    /// sit -- 0.6052 below and 4.0307 above.
    ///
    /// # What it is NOT
    ///
    /// It is not a correlation dial and not a second crisis blend. The
    /// sector leg it multiplies is the one every member of the sector
    /// shares, so lifting it lifts both the epicentre's volatility and its
    /// internal correlation -- which is what an epicentre is -- and the
    /// idiosyncratic leg is lifted beside it so the split between the two
    /// is the one the name already had.
    ///
    /// It is not meant to be a volatility dial either, and that is what the
    /// conservation condition buys: the roster's mean non-market INNOVATION
    /// variance is the same at any extra, to the bit. What a long run
    /// realises is not, because the per-name GARCH is convex in what it is
    /// fed and gives some of it back;
    /// [`crate::market::factors::crisis_epicentre_gains`] measures how much
    /// and says why moving `w` to chase it would be fitting.
    ///
    /// An epicentre no name in the roster is in does nothing at all, and
    /// `Engine::crisis_epicentre_key` is where that is decided: the draw
    /// still happened and the episode still reports it, but with nobody to
    /// move the variance TO there is nothing for the tick to do with it.
    pub crisis_epicentre_extra: f64,
    /// How many consecutive sessions under `crisis_vix_threshold` end a
    /// crisis episode. 21 ships, which is a month of sessions.
    ///
    /// The hysteresis, and it is what makes an episode an episode rather
    /// than a run of scattered days: the tape's five episodes are months
    /// long (the shortest, 2011, is four months) and the VIX crosses back
    /// under the threshold repeatedly inside each of them. Without the
    /// counter an epicentre would be redrawn on every re-crossing, which
    /// would average three sectors across one crisis and show none of them.
    ///
    /// Read only while `crisis_epicentre_extra` is non-zero, so it is inert
    /// on every shipped preset whatever it reads. A value at or below zero
    /// ends an episode on the first session back under the threshold, which
    /// `ModelParams::invariants` allows: it is a degenerate hysteresis, not
    /// an incoherent one.
    pub crisis_epicentre_end_sessions: f64,
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
    /// INERT at 0.0, which pt-v1 through pt-v14 set; pt-v15 onward ship
    /// 1.0, and this line said "every shipped preset" until 2026-09-17.
    /// `update_economy_daily`
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
/// pt-v16 with the first moments its mechanisms inject given back -- see
/// [`ModelParams::pt_v18`], including why the number skips pt-v17.
pub const PT_V18: ModelParams = ModelParams::pt_v18();
/// pt-v18 with the VIX level identity on, the VIX's fall-rate symmetric,
/// the sector loading raised and the per-name volume-variance channel
/// switched on -- see [`ModelParams::pt_v19`]. THE DEFAULT since 0.8.0:
/// `DEFAULT_PRESET_NAME` names it and `Engine::default_model` returns it,
/// and the test at the bottom of this file asserts the two agree.
pub const PT_V19: ModelParams = ModelParams::pt_v19();

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
pub const DEFAULT_PRESET_NAME: &str = "pt-v19";

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
            order_flow_impact_law: 0.0,
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
            crash_amplifier_conditional_sigma: 0.0,
            market_vol_vix_excursion: 0.0,
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
            garch_vix_exponent: 2.0,
            garch_floor_multiple: garch::FLOOR_MULTIPLE,
            garch_omega_sector_scaled: 0.0,
            garch_innovation_commensurate: 0.0,
            idio_sigma_floor: 0.0001,
            market_vol_alpha: factor_vol::MARKET_VOL_ALPHA,
            market_vol_beta: factor_vol::MARKET_VOL_BETA,
            market_vol_gamma: 0.0,
            market_vol_alpha_excursion: 0.0,
            market_vol_level_persistence: 0.0,
            market_vol_level_sigma: 0.0,
            vix_level_persistence: 0.0,
            vix_level_sigma: 0.0,
            vix_level_loop_gain: 0.0,
            vix_anchor_reversion: 0.0,
            vix_anchor_weight: 0.0,
            vix_anchor_memory: 0.0,
            macro_compound_days_per_year: 365.0,
            vix_anchor_centre: 0.0,
            vix_anchor_weight_level: 0.0,
            vix_anchor_weight_level_cap: 0.0,
            vix_anchor_weight_level_knee: 0.0,
            vix_anchor_weight_level_below: 0.0,
            vix_anchor_weight_level_knee_fixed: 0.0,
            market_burn_in_sessions: 0.0,
            market_vol_ceiling_multiple: factor_vol::MARKET_VOL_CEILING_MULTIPLE,
            market_vol_floor_multiple: factor_vol::MARKET_VOL_FLOOR_MULTIPLE,
            market_vol_vix_coupling: factor_vol::MARKET_VOL_VIX_COUPLING,
            market_vol_vix_anchor: factor_vol::MARKET_VOL_VIX_ANCHOR,
            market_vol_vix_smooth: 0.0,
            market_vol_vix_exponent: 2.0,
            market_beta_down_asym: 0.0,
            market_beta_down_asym_lag: 0.0,
            market_beta_down_asym_lag_live: 0.0,
            market_beta_down_asym_recentre: 0.0,
            market_idio_down_suppress: 0.0,
            oil_supply_response: 0.0,
            oil_opec_symmetry: 0.0,
            oil_seasonality_target: 0.0,
            cycle_hazard_per_month: 0.0,
            trough_growth_floor: 0.0,
            phase_target_range_draw: 0.0,
            neutral_discount_rate: crate::fair_value::NEUTRAL_DISCOUNT_RATE,
            macro_burn_in_days: 0.0,
            cycle_stationary_opening: 0.0,
            buyback_payout_share: 0.0,
            jump_mean_compensated: 0.0,
            cascade_symmetry: 0.0,
            // Legacy values: the slow component is OFF, and the update
            // reduces to the single-component form bit for bit.
            market_vol_slow_persistence: 0.0,
            market_vol_slow_gain: 0.0,
            fair_value_book_floor: 0.0,
            earnings_nominal_growth: 0.0,
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
            volume_move_jump_share: 1.0,
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
            jump_market_variance_share: 0.0,
            jump_vix_coupling: 0.0,
            overnight_variance_ratio: 0.0,
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
            // The seven below are the VIX-dynamics dials of
            // programme/results/vix-dynamics.md. Each default is the
            // value at which its branch is not taken, so every preset
            // written before them reproduces to the bit; `1.0` for the up
            // exponent is the linear form the arithmetic stood in.
            vix_return_level_exponent: 0.0,
            vix_return_exponent_up: 1.0,
            vix_return_level_exponent_up: 0.0,
            vix_innovation_sigma: 0.0,
            vix_innovation_return_sigma: 0.0,
            vix_jump_level_scale: 0.0,
            vix_jump_return_intensity: 0.0,
            // The two per-component states of vix-dynamics.md section 19
            // and the idiosyncratic-rate switch, all branches at 0.0.
            sector_vol_alpha: 0.0,
            sector_vol_beta: 0.0,
            jump_idio_excitation: 0.0,
            jump_idio_excitation_decay: 0.0,
            jump_idio_vix_decoupled: 0.0,
            forced_flow_gain: 0.0,
            forced_flow_threshold: 40.0,
            forced_flow_beta_exponent: 0.0,
            forced_flow_reservoir: 0.0,
            forced_flow_replenish: 0.0,
            vix_realised_vol_weight: 0.0,
            // 0.0 is the level the constants built, bit-identical to the
            // arithmetic that predates this dial.
            vix_level_identity: 0.0,
            // MEASURED, and unread while the identity above is 0.0. See
            // the field's own note for the five estimators and the IQR.
            vix_variance_premium: 0.252,
            vix_cycle_amplitude: 1.0,
            vix_return_source: 0.0,
            vix_return_gain: crate::economy::VIX_RETURN_GAIN,
            vix_return_gain_up: crate::economy::VIX_RETURN_GAIN_UP,
            // 1.0 is the linear form and is bit-identical to the
            // arithmetic that stood before this dial existed.
            vix_return_exponent: 1.0,
            vix_return_clamp: crate::economy::VIX_RETURN_CLAMP,
            vix_target_shock_cap: crate::economy::VIX_TARGET_SHOCK_CAP,
            // Both reproduce the shipped arithmetic exactly: 80.0 is the
            // literal the state clamp carried, and a zero offset adds
            // nothing. Every preset before this dial is bit-identical.
            vix_ceiling: 80.0,
            vix_target_offset: 0.0,
            crisis_vix_threshold: crate::economy::CRISIS_VIX_THRESHOLD,
            // Inert: the branch is not taken, no episode is tracked and no
            // draw is taken on any stream, so every preset is bit-identical.
            crisis_epicentre_extra: 0.0,
            crisis_epicentre_end_sessions: 21.0,
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
    /// The row that closed at two years is `volume_change_acf1`, the one
    /// the `volume-change` gap was written about and the one §21 through
    /// §23 called structurally unreachable; at pt-v11 it read -0.3156
    /// against a band of -0.29 to -0.21 and at pt-v12 it reads -0.2656,
    /// inside. That is the finding. This paragraph led with "**14 of 14 at
    /// two years is the first time this project has measured it**" until
    /// 2026-09-14; see `pt_v19` for why a band count is not a preset's
    /// quality figure.
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

    /// The index-drift era: give back the first moments the model injects
    /// without anyone having chosen them.
    ///
    /// # Why pt-v18 and not pt-v17
    ///
    /// pt-v17 is reserved by the open recomposition era on the
    /// `preset/pt-v17` branch, whose doc comments already name it. Two
    /// eras cannot share a name, and a preset name is the identity every
    /// published result cites, so this one takes the next number rather
    /// than the next slot. The gap is deliberate and this note is the
    /// record of why.
    ///
    /// # What it corrects
    ///
    /// The equal-weight index drifted -22.155 per cent a year at pt-v16
    /// over thirty seeds on `Universe.random(40, seed=111)` at 252 days,
    /// against a real large-cap index's +8 to +10, and it was negative on
    /// every seed. None of that was a modelling choice: it is the sum of
    /// mechanisms that each moved the mean as a by-product of shaping
    /// something else, and of a panel of fourteen shape statistics that
    /// could not see a first moment and read fourteen for fourteen anyway.
    ///
    /// This era gives each of those means back at its source rather than
    /// cancelling them with an offset at the end, so the shapes the
    /// mechanisms were built for survive.
    ///
    /// Returning every unchosen mean leaves the index near zero rather
    /// than near a real index's 8 to 9 per cent, because the architecture
    /// carries no expected return at all: `s` is stationary and `eps` is
    /// fixed, so fair value moves only with the discount rate. The last
    /// dial supplies one from the nominal output the economy already
    /// integrates. See [`ModelParams::earnings_nominal_growth`] for what
    /// that leaves unclaimed.
    pub const fn pt_v18() -> ModelParams {
        let mut p = ModelParams::pt_v16();
        // The downside transmission tilt scales one side of a zero-mean
        // draw, so it injects `a * beta * -s / sqrt(2 pi)` per name per
        // tick. Worth -7.940 points of annual index drift at pt-v16, and
        // the volatility path's -1.670 acts THROUGH it rather than beside
        // it, because a hotter conditional sigma injects proportionally
        // more. Recentred against the conditional sigma, so both go.
        p.market_beta_down_asym_recentre = 1.0;
        // Oil demand drew inventory down every day against a supply term
        // hardcoded to zero, so inventory hit its floor around day 120 and
        // the inventory pressure term saturated at a standing push on the
        // oil price. That is what made the rate path one-way: oil raised
        // inflation, inflation made the bank hike, and the hike compressed
        // every multiple. At 1.0 supply answers demand and inventory is
        // driftless, which is the stationarity condition of the process
        // rather than a level chosen to hit a number.
        p.oil_supply_response = 1.0;
        // The OPEC rule cuts harder than it raises, 2.700 against 1.750, so
        // it pushes oil up on net. Symmetrised at the mean of its own two
        // branches, which removes the direction without choosing a side.
        p.oil_opec_symmetry = 1.0;
        // Oil's seasonal shape multiplied the price level every day, so it
        // compounded: the product of its factors over a certified year is
        // 5.119, against 0.921 over a full 365 days. The shape was near
        // neutral over its own period and the horizon sliced it. On the
        // reversion target the same amplitude modulates where the price is
        // pulled toward and integrates to +0.672 per cent over the year.
        p.oil_seasonality_target = 1.0;
        // The cycle's Weibull scale is in months and the transition was
        // drawn against it once a day, so the cycle ran about thirty
        // times too fast: 2.6 trading years against 9.7, with a phase
        // change inside 63 per cent of certified years against 3. Read
        // per month on the 30-day month the phase clock already keeps.
        p.cycle_hazard_per_month = 1.0;
        // The valuation was neutral at a 4.00 per cent discount rate and
        // the economy opens at a corporate yield of 4.56, so every
        // profitable name opened about one per cent below the price the
        // generator drew for it and the year was spent unwinding it. The
        // value that zeroes that term is the yield the economy opens at,
        // read off the process rather than chosen. It moves to the corner's
        // 0.0482 when the burn-in lands.
        // The corner the dynamics reach rather than the point they open
        // at, because the burn-in below now opens the year there. Both
        // values are the yield the economy rests at under their own
        // arm, read off the burn-in table.
        p.neutral_discount_rate = 0.0482;
        // The economy opens at unemployment 4.00, inflation 2.00 and a
        // corporate yield of 4.56 and its own dynamics reach 2.50, 2.74
        // and 4.82, so a certified year was spent travelling. 755 is the
        // day the last of those fields enters its stationary band.
        p.macro_burn_in_days = 755.0;
        // The one CHOSEN constant in this era. A third of earnings
        // returned as net buybacks, taken from the US large-cap filing
        // record rather than from anything this engine produces. It
        // retires stock, so earnings and book per share grow by the
        // buyback yield, which is earnings over price and therefore
        // pays more when the multiple is low.
        p.buyback_payout_share = 1.0 / 3.0;
        // The market jump's negative mean buys skew and a drift together.
        // Compensating it keeps the mean, and so the skew, and returns the
        // drift: a deterministic offset moves no central moment.
        p.jump_mean_compensated = 1.0;
        // The two stop ladders do not match: the downside fires earlier, has
        // an extra tier and is larger at every matched size. Over symmetric
        // returns that is a drift. Matched at the mean of the pair, which
        // chooses no side and preserves their total intervention.
        p.cascade_symmetry = 1.0;
        // The four dials above give back means the model injected without
        // anyone choosing them, and a model with every unchosen mean
        // returned still has no expected return: `s` is stationary and
        // `eps` is fixed, so fair value moves only with the discount rate.
        // This one holds the earnings share of nominal output constant, so
        // the valuation grows with the output the economy already
        // integrates. It delivers 252/365 of growth plus inflation per
        // trading year, measured at +4.342 by median over thirty seeds on
        // the certified year at `measured/ede43c5`, which is this preset
        // before the oil seasonality and cycle clock dials joined it.
        //
        // The rate falls with growth and inflation wherever the cycle takes
        // them. An earlier form of this comment said it goes negative in a
        // contraction, and that is true of the DAILY rate and not of a
        // year. Measured: negative on 83.5 per cent of contraction days and
        // 99.9 per cent of trough days.
        //
        // Whether a contraction YEAR is negative depends on how long the
        // phase lasts, and that clock has just changed under this preset.
        // Read once a day, which is every preset before pt-v18, a
        // contraction lasts 4.4 months, ends well inside the window it
        // would have to fill, and a calendar year holding one nets about
        // +0.2 and is negative on 39 of 157 such seed-years. pt-v18 reads
        // the hazard per month, where the phase outlasts a certified year.
        // The year-level outcome under that clock is registered and being
        // measured rather than assumed here.
        p.earnings_nominal_growth = 1.0;
        // THE LAGGED DOWNSIDE WIRE, at the best of a seven-point sweep.
        //
        // The dial's own docstring gives the motivation -- real down-moves
        // continue and the contemporaneous wire alone cannot express it --
        // and this is the first preset to switch it on. 0.375 is the
        // argmin of `S` over 0, 0.25, 0.375, 0.5, 0.625, 0.75, 1.0 at
        // THIRTY seeds, and BOTH horizons agree on it: 57.55 to 46.25 at
        // 252 and 49.72 to 36.05 at 504. Nothing was ruled; the two
        // horizons picked the same point.
        //
        // It is not a fear-channel dial and the measurement says so. Across
        // that whole sweep the fear gauge moves 0.936 to 1.115 and the
        // VIX's own persistence moves 0.0333 to 0.0274 -- a nineteenth of
        // what `vix_mean_reversion` does to the same row -- so it earns its
        // score on other rows entirely, which is why the two compose.
        //
        // Full on is WORSE THAN OFF at 504 (82.52 against 49.72). An
        // eight-seed screen read the optimum as 0.5 and a survey marginal
        // over ten mechanisms read it as monotone toward 1.0; both were
        // wrong, in opposite directions, and thirty seeds on one dial is
        // what settled it.
        p.market_beta_down_asym_lag = 0.375;
        // VIX MEAN REVERSION: A RULING ON A PARTIAL ORDER, NOT A DERIVATION.
        //
        // Said plainly because the distinction is this era's: 0.375 above
        // is where two horizons agreed, and 0.10 here is where they did
        // not. On the same thirty-seed sweep, at the lag above, three
        // values are non-dominated on (`S_252`, `S_504`):
        //
        //     0.10 -> 28.69 / 29.78     0.12 -> 25.65 / 32.84
        //     0.15 -> 23.87 / 42.79
        //
        // R6 does both jobs. It forbids a combined number that would pick
        // one of these and hide the rest, and its third bullet is what
        // makes the choice among them Simon's: a frontier shows every
        // trade at once and lets a human choose. Text here used to cite
        // R10 for that second job. R10 asks only whether `cmaes.py` may
        // collapse the two horizons to rank a generation, and it has
        // never been answered.
        //
        // RULED 0.10 on 2026-09-07, filed as R13 on 2026-09-08, and R13
        // was WITHDRAWN on 2026-09-15. Box `sigmamr1` measured 0.10
        // against 0.27 on the whole objective and 0.27 is ahead at both
        // horizons and nearer the tape on `vix_ar1_debiased`, which is
        // why pt-v19 below reads 0.27. The 0.10 stays here because a
        // shipped preset's vector is frozen. It records what pt-v18 was
        // released with. The ruling behind it no longer stands.
        //
        // What the ruling was made on. The scoring rule gained the VIX's
        // own persistence row this day (`facts.PERSISTENCE`), and 0.10 is
        // the only one of the three holding that row inside two standard
        // errors of its ruler at BOTH horizons: +0.7 and -2.0, against
        // -2.7 and -6.8 at 0.15. It is also the only point where the two
        // horizons score alike rather than one being bought at the other's
        // expense, which nothing in the objective asked for.
        //
        // The shipped 0.06 is DOMINATED -- 0.12 beats it at both horizons
        // -- so whatever the ruling, it was not going to stay.
        //
        // Registered before the run in the design repository at `053f3bf`
        // and measured at `3175a4c`; the eighteen-row objective that
        // preceded the persistence row wanted 0.15 at 252 and 0.20 at 504,
        // and adding the row did not close that disagreement, it reversed
        // which end was which.
        p.vix_mean_reversion = 0.10;
        p
    }

    /// The four-dial candidate: pt-v18 with the VIX level identity on, the
    /// VIX's fall-rate symmetric, the sector loading raised and the
    /// per-name volume-variance channel switched on. Nothing else moves.
    ///
    /// THE DEFAULT since 0.8.0, and the envelope certifies whatever
    /// `DEFAULT_PRESET_NAME` names. It was composed, registered and
    /// selectable one release step before it took the default, because
    /// moving the default changes every seeded trajectory and re-baselines
    /// the known-answer test: composing it moved no digest, and moving the
    /// default moved the simulation digest and nothing else.
    ///
    /// # Where the four values come from
    ///
    /// Every one is MEASURED, on the design repository's record, and the
    /// entry for each in `python/tradefloor/provenance.py` carries the
    /// script, the date, the seed count, the estimator and the residual.
    /// The short form, so the constructor does not have to be trusted:
    ///
    /// Three of the four -- the identity, the decay ratio and the loading
    /// -- were composed as one cell on the `sectorcomp` factorial (thirty
    /// seeds, held roster 40 @ 111) and confirmed at ONE HUNDRED AND
    /// TWENTY seeds against pt-v18 as the paired control (`resolve120`,
    /// pin `3d6462a`): `S_252` 54.90 -> 32.90 and `S_504` 47.37 -> 30.05
    /// on the nineteen-row scoring rule. The fourth,
    /// `volume_idio_variance_gain` 0.20, was found by tracing
    /// `volume_change_acf1` to a per-name channel every preset ships at
    /// 0.0 (`volume-acf-result.md`) and measured on the same 120 seeds
    /// against that three-dial cell (`iterate5`): 32.90 -> 22.69 and
    /// 30.05 -> 26.11. The whole vector reproduces on two later boxes at
    /// `max|delta| = 0` over every numeric field (`gainsweep`,
    /// `crosscorr-confirm`).
    ///
    /// On the varying-roster certification protocol, which is the only
    /// one that carries a band verdict, the four-dial cell read 18 of 18
    /// rows in band at BOTH horizons with pt-v18 reproducing its published
    /// certification to four places in the same run (`cert4b`, 2026-09-10).
    ///
    /// **THAT COUNT IS NOT A QUALITY FIGURE, recorded 2026-09-14.** It is
    /// quoted above because it is what the run reported and because the
    /// paragraph below turns on it having changed. `facts.REAL_MARKETS`
    /// and `envelope.BANDS_504` are derived from 2015-2025 windows; the
    /// 0.8.0 scoring rule was re-centred on 1987-2025 and these band
    /// tables were not, so a band count grades a preset against a ruler
    /// the project stopped scoring with. A band result is stated as the
    /// rows that are out and how far, the way the `17 of 18` reading below
    /// and the `sector_excess_corr` reading in
    /// `python/tradefloor/presets/pt-v19.json` are stated. Two presets are
    /// compared on `loss.scoring_rule`, never on their counts.
    ///
    /// **THAT CERTIFICATION NO LONGER DESCRIBES THIS PRESET, and nothing in
    /// this constructor is the reason.** `cert4b` measured a build whose
    /// index-variance read-back carried neither the crash amplifier, nor the
    /// crisis blend, nor the downside transmission tilt. All three are in it
    /// now (`market::index_var`), so every pt-v19 trajectory has moved twice
    /// since, and the panel has to be re-measured before this paragraph can
    /// be restated. Re-measured on the same protocol at the read-back as it
    /// now stands, the preset reads **17 of 18 at 252 days**: the miss is
    /// `index_tail_dn3_pct` at 5.26 against a band of 0.47 to 1.96.
    /// `python/tradefloor/presets/pt-v19.json` is deliberately NOT
    /// regenerated while that is true, so
    /// `test_a_record_describes_the_preset_it_names[pt-v19]` says so.
    ///
    /// # What was measured and left alone
    ///
    /// `vix_return_gain` stays at 17. The sweep on this base measured 8,
    /// 11, 14, 17 and 20 at 120 seeds (`gainsweep`): 8 is +21.68 +/- 2.00
    /// worse at 252, 20 is +4.04 +/- 1.38 worse at 504, 14 ties at 504 and
    /// is +5.10 +/- 1.32 worse at 252. The frontier is 14/17/20 and 17 is
    /// the only point on it that needs no change. A dial not moved needs
    /// no provenance.
    ///
    /// # What this preset does NOT fix, and knows it
    ///
    /// `cross_sectional_corr` reads LOW on this base -- 2.57 + 7.79 points
    /// of `S` across the two horizons against 0.06 + 0.42 on pt-v18 -- and
    /// it is a floor: three dials move it 3-4 tape se with the sector row
    /// held, and every one pays `vix_ar1_debiased`, `corr_persistence_acf1`
    /// and `excess_kurtosis` back by as much at 120 seeds
    /// (`crosscorr-result.md`). Two thirds of that damage is the decay
    /// ratio's, and turning it back costs the fear rows twelve points, so
    /// the row is a price of the regime the fear fix needs and not a
    /// mistake in it. Bar B4 -- a fear response that RISES across the
    /// graded range -- is unmet by this and by every preset; that is a
    /// mechanism change (`programme/code-work-required.md` section 1), not
    /// a dial.
    pub const fn pt_v19() -> ModelParams {
        let mut p = ModelParams::pt_v18();
        // The VIX's target becomes the level the index's own conditional
        // variance implies, so the anchor is derived (19.53 on the certified
        // roster against the dial's 15.98) rather than chosen, and six free
        // numbers in the fear channel stop being free. Alone on pt-v18 it
        // makes the VIX too persistent; with the decay ratio below it is
        // the regime in which `fear_gauge_dn3` centres (5.82 against 5.73,
        // z +0.13 on the varying roster).
        p.vix_level_identity = 1.0;
        // The VIX falls at the full reversion rate, not 0.6 of it. Under
        // the identity the 0.6 asymmetry held the mean VIX above the
        // derived anchor and fired the crisis blend on six per cent of
        // days; at 1.0 the mean falls under it. This is the dial that
        // carries the fear fix -- turning it back costs `fear_gauge_dn1`
        // nine points and `fear_gauge_dn3` twelve -- and two thirds of the
        // `cross_sectional_corr` cost above. pt-v1 shipped 1.0; pt-v16
        // moved it to 0.6, and this returns it on measurement.
        p.vix_decay_ratio = 1.0;
        // The identity and the decay ratio take `sector_excess_corr` from
        // -3.5 to -6.7 tape se; the loading puts it back on centre
        // (0.1641 against 0.1640 at 252). Measured 0.7-0.9 on this base:
        // the row moves -0.0105 of cross-sectional per +0.027 of sector per
        // 0.1 of loading, `S` is flat between 0.75 and 0.85, and 0.7 and
        // 0.9 are 4-7 points worse.
        p.sector_loading = 0.8;
        // The per-name volume-variance channel, which ships at 0.0 in every
        // earlier preset: a name's volume follows its OWN GARCH variance
        // relative to its sector's base, not only the market factor's
        // (`market/tick.rs`, `volume_multiplier`). It centres
        // `volume_change_acf1` (term 8.82 -> 1.99 at 120 seeds) and, alone
        // among that row's movers, does not pay on `volume_abs_return_corr`.
        // 0.25 against 0.20 reads -0.12 +/- 1.28 at 252 and +1.97 +/- 1.14
        // at 504, paired over the same 120 seeds: a plateau, and 0.20 is
        // the lower dose on it. Its partners `volume_idio_persistence` and
        // `volume_idio_sigma` stay at 0.0, so the per-name volume STATE is
        // still memoryless; this is a stateless channel.
        p.volume_idio_variance_gain = 0.20;
        // CHARTER BAR B4. `market::index_var` now prices the crash
        // amplifier and the crisis blend, so the read-back carries the
        // regime it runs in and the fear arm has something to balance it
        // above `crisis_vix_threshold`. `vix_target_shock_cap` was the
        // brake standing in for that (`loopgain-report.md` §8.2) and at
        // 45.0 against a gain of 17.0 it bound at 2.647 per cent of session
        // return -- inside the 6.39 per cent the tape grades, which is what
        // B4 asks the response to rise across.
        //
        // The value is DERIVED, not searched: the image of
        // `vix_return_clamp` under the spike, so the cap cannot bind
        // anywhere the clamp does not and the pair has one binding
        // constraint between them. `vix_return_exponent` is 1.0 here, so
        // the image is the product; `the_default_cap_is_the_clamps_own_image`
        // asserts both halves of that rather than trusting the comment.
        p.vix_target_shock_cap = p.vix_return_gain * p.vix_return_clamp;
        // THE STABILITY CONDITION, and this is the dial that meets it. B4
        // put the crash amplifier inside the VIX's own target, and the
        // amplifier's second moment grows without bound in the regime ratio
        // because its shock is denominated in the BASELINE sigma -- so the
        // map `v -> implied(v)` is superlinear at the top of the VIX's
        // range and `vix_ceiling` is absorbing on rosters the certification
        // protocol actually draws. Normalising by the tick's own
        // conditional sigma sets `a = m` and `c = T` in
        // `market::index_var::amplifier_moments`, which makes `E[z^2 A^2]`
        // constant in the regime, the amplified factor block linear in
        // `v_f`, and the map incapable of crossing the diagonal however far
        // the factor variance excurses.
        //
        // DERIVED and not searched: it is the only value other than the
        // baseline reading, the dial has no interior, and what chooses it
        // is the condition `implied(v) < v` and not a panel row.
        p.crash_amplifier_conditional_sigma = 1.0;
        // THE LOOP'S VARIANCE ARM, CUT. `garch-derive-design.md` finding 4:
        // under `vix_level_identity` the VIX IS the index's conditional
        // variance in points plus a fear excursion, so a target reading
        // `(VIX / anchor)^2` reads the factor's own variance back to
        // itself. §3.3 of that note shows the target then reverts the
        // factor toward `c * s_f` of its own level, which makes
        // `market_vol_vix_coupling` a loop-gain dial wearing a fear
        // channel's name. The loop's static gain theta is about 0.62, of
        // which the factor arm carries 0.45, and a standing bias is
        // amplified by `1 / (1 - theta)` = 2.7x before it reaches
        // anything. Reading the EXCURSION above the identity's own
        // read-back takes that to about 1.1x.
        //
        // DERIVED, and it is `garch-derive-design` §3.4's option C, which
        // that note recorded as the root cause and flagged rather than
        // proposed. It is not preferred over option A because it is
        // upstream: it makes option B's REJECTED correction identically
        // zero rather than estimating it. Option B would correct the
        // tape's beta by `[beta_tape - (1 - alpha_tape) c s_f] / (1 - c
        // s_f)` = 0.844 and was rejected because `c` is unprovenanced and
        // `s_f` is a roster property; at `c s_f` = 0 that expression is
        // `beta_tape` exactly. The dependency is removed, not approximated.
        //
        // Measured, b4fix2: the static map's pin-80 ratio 0.651 -> 0.474
        // and `ratio(80)/ratio(40)` 0.937 -> 0.729 against a
        // parameter-free sqrt law's 0.707, so the map is SUBLINEAR where
        // `crash_amplifier_conditional_sigma` made it asymptotically
        // linear. The index tail goes 3.0677 -> 1.9124 at 252 days.
        p.market_vol_vix_excursion = 1.0;
        // THE CRISIS BLEND, DERIVED TO ZERO AND ITS FORM RETIRED.
        //
        // Three facts from the tape, none of which needs the model
        // (programme/crisis-blend-derivation.md, design repository). The
        // VIX has no crisis attractor: its conditional drift by level is
        // negative in every bin above 22.5 at five and twenty days, and
        // crisis spells above 30.88 have a median length of two sessions.
        // Its cross-sectional correlation is a function of realised common
        // volatility, `rho = -0.366 + 0.277 log(sigma_ann%)` with R^2 0.69
        // (slope sd 0.025, year-block bootstrap), and the VIX level adds
        // nothing once volatility is in. And this model's factor share of
        // each name's variance already gives that curve with no lift:
        // thirty seeds on the held roster read slope 0.283 with every
        // populated bin within 0.03 of the tape's.
        //
        // The shipped blend keyed a loading lift on the VIX level, which
        // fed the identity, which fed the VIX target: a positive feedback
        // that gave the map a stable fixed point at 33 to 36 (settled
        // ladder, ten rosters) and, free-running over 120 rosters, a
        // positive one-day drift of +0.82 at a VIX of 32.5 to 35 where the
        // tape's is -0.35. What it bought, three persistence rows, it
        // bought by holding the model in a crisis regime the tape does not
        // have: years above 60 three times as often as the tape, a
        // highest VIX of 120 against 82.69, crisis spells with a p90 of 36
        // sessions against 13.
        //
        // DERIVED as the identity: the tape supports no lift, and the
        // residual is the tape fit's slope sd, which any lift large enough
        // to matter exceeds. Measured at 0 (b4fix9): the VIX distribution
        // is the tape's on every per-year statistic, spells median 2 and
        // p90 13 exactly the tape's, `index_tail_dn3_pct` 0.624 and 0.696
        // in band; and `corr_persistence_acf1` on the held roster at 504
        // days reads 0.1493 against a floor of 0.19, with
        // `abs_return_acf1` and `vix_ar1_debiased` worse beside it. Those
        // are a monthly-scale volatility and VIX persistence deficit
        // (VIX acf1 of 21-day means 0.46 against the tape's 0.62), owned by
        // the reversion rate and the loop's memory, and are derived next
        // rather than papered over with a lift. Adopted by Simon's ruling
        // of 2026-09-12 (R16), with the row red.
        p.crisis_blend_gain = 0.0;
        // THE COMPOSITION OF 2026-09-21 (design repo, results/ptv19gjr). The
        // VIX persistence row's error was located on the desk: the factor's
        // shock share set the VIX's within-year amplitude at 1.5x the
        // tape's, and the model had a crisis in fifteen of sixteen
        // seed-years where the tape has one in twenty of thirty-five. Three
        // parts, each the tape's own number, measured apart on the box the
        // 2026-09-20 factorial could not separate them on:
        //
        // The GJR triple is the 2026-09-07 fit on the tape's index returns
        // (`market_vol_alpha`'s provenance entry). It shipped on pt-v19 from
        // 2026-09-14 and left on 2026-09-20 inside the market variance
        // family, whose cost the factor level carried. On its own it takes
        // the one-year persistence from k 25 of 30 above the tape to 19 and
        // `fear_gauge_dn3` from 6.40 to 5.87 against 5.73, and costs the
        // short-lag clustering rows and the index tail.
        // (The triple is set on the lines that carried the recomposition
        // below, beside the comments that argued it, so the file keeps one
        // assignment per dial.) The slow pole is the tape's variance impulse
        // response's (`market_vol_slow_persistence`'s entry): +0.025 and
        // +0.062 of `corr_persistence_acf1` toward the tape at 252 and 504.
        // The regime level, on the VIX law and not on the factor's target,
        // DERIVED from the tape's 35 yearly medians of log VIX: spread
        // 0.267, year-to-year autocorrelation 0.59, so phi 0.59^(1/252)
        // and the innovation that gives that spread. It is what makes calm
        // years: with it the model reaches VIX 30 in about half its
        // seed-years, as the tape does, and the persistence RISE from one
        // year to two reads +0.012 [+0.001, +0.027] against the tape's
        // paired +0.012. The two-pole fit's level (0.9965, 0.02555) is the
        // whole-span ACF's slow pole with the crisis decay inside it, and
        // it added within-year variance the tape's calm years do not have.
        p.vix_level_persistence = 0.9979;
        p.vix_level_sigma = 0.0173;
        // THE THIRD COMPOSITION, 2026-09-21 (design repo,
        // results/ptv19fix/RESULT.md, registered first, decision rule written
        // before the numbers). The crisis lever is lost at the excursion
        // form's FIXED POINT: the target is `base (1 - c + c (VIX / I)^e)`
        // with `I ~ sqrt(v)`, so a held VIX settles the variance at
        // `v ~ VIX^(e / (1 + e/2))`, which at the shipped square is `v ~ VIX`
        // and a lever of sqrt(13) before clamps. The tape's lever, 6.16x of
        // volatility for 13x of VIX, is `v ~ VIX^1.42`, which the same form
        // gives at `e = 2s / (2 - s)` = 4.9. DERIVED from that law; the
        // held-VIX read-back then rises 4.6x for 13x (3.0x at the square).
        p.market_vol_vix_exponent = 4.9;
        // And the defect that made every gain in the loop lengthen the VIX's
        // memory: the regime level's spread (0.267, the tape's yearly
        // medians of log VIX) is a spread OF THE VIX, and it was written
        // onto the latent multiplier, which the loop amplifies by
        // `1 / (1 - h)` with `h` the read-back's held-VIX elasticity
        // (`ln 4.6 / ln 13` = 0.595 at this exponent). The gain divides
        // the level's innovation and its stationary opening by that
        // factor, so the VIX carries the tape's spread and not 2.47 times
        // it. DERIVED from the same two held-VIX readings. Measured on the
        // box: the VIX row passes at both horizons (k 19 and 16, rise
        // +0.007 against the tape's +0.012), the lever reads 3.05x, and
        // the sum of squared tape errors falls from 116 to 91 at one year
        // and 82 to 59 at two, the first composition to lower it since the
        // VIX law arrived.
        p.vix_level_loop_gain = 2.4684;
        // THE FOURTH COMPOSITION, 2026-09-22 (design repo, results/ptv19epi3/
        // RESULT.md, registered first; Simon's ruling that ties in the row
        // tally are settled by distance). The crisis epicentre: at each
        // crisis episode one sector is drawn to carry the crisis, on its own
        // stream, from the sector table's weights (financial_services 0.6,
        // none 0.4, the tape's five episodes); its names carry the extra on
        // the parts of their return that are not the market factor and every
        // other name carries a multiple under one, so the roster's crisis
        // variance is redistributed and not added to. DERIVED 1.93, the
        // median of the tape's three epicentre episodes. Measured on the box:
        // the crisis_sector_dispersion row from 1.15 to 1.36 against the
        // tape's 1.34 at two years; the VIX row, the rise, the tail and the
        // fear rows unchanged; sector_excess_corr half a tape error further.
        // The scenario pins it: `Scenario().hold(epicentre="financial_services")`.
        p.crisis_epicentre_extra = 1.93;
        // THE CEILING, WHICH CLAMPS THE STATE AND NOT THE TARGET.
        //
        // `vix_ceiling` bounds the VIX after the reversion step,
        // `x + vix_mean_reversion * (target - x)` (economy/daily.rs:1141).
        // `vix_return_gain * GRADED_ABS_R` = 108.63 is the fear channel's
        // term of the TARGET for a session at the top of the graded range.
        // This preset shipped 108.63 for a day under the claim that it was
        // "the image of the graded range under the fear response" and that
        // fear alone reached it at 6.39 per cent. Neither was about the
        // state: a graded session from rest moves the VIX by 0.10 * 108.63
        // = 10.86 points, and the graded range's image on the state from
        // rest is 34 to 36. programme/results/ceiling-derivation-
        // independent.md in the design repository derives the update and
        // `economy::daily::fear_response_shape` asserts it on
        // `update_economy_daily` itself.
        //
        // What reaches a ceiling is the identity's own level. The VIX
        // reached 108.63 on 4 of 30,240 seed-days over 120 rosters, and on
        // three of the four the index's conditional variance implied a VIX
        // above the ceiling with no fear response at all (146.17, 142.58,
        // 114.10): variance excursions of 8.5 to 30 times the factor's
        // base, with the session a passenger.
        //
        // THE VALUE. 181.3295 solves `C - implied(C) >= 108.63`, the ceiling
        // a VIX already at C comes off on a session at the top of the
        // graded range, with `implied(C)` the settled read-back at a pin,
        // on the map this preset runs, which with `crisis_blend_gain` at
        // 0 is the blend-off map: b4fix7's settled ladder, ten rosters,
        // burn 250 and 80 scored sessions, pins 14 to 260. `C - implied(C)`
        // is monotone, 107.653 at pin 180 and 122.350 at 200, crossing at
        // 181.3295, residual +/- 8.64 from the ladder's spread across
        // rosters through the local slope. The closed-form map on three of
        // those rosters puts the crossing at 176.6 to 184.3.
        //
        // The values this replaces: 108.63 read a term of the target as a
        // bound on the state; 173.1087 was b4fix6's solve of this condition
        // on a 40-session-burn ladder, which read the settled level 9 per
        // cent low. Both are withdrawn.
        //
        // WHAT IT IS NOT PROVEN FOR. The condition is sufficient for the
        // settled map. It does not cover the state the VIX carries on a
        // variance excursion, and with a session at the top of the graded
        // range every day on top of a sustained excursion the state's fixed
        // point is above 300. That is a stated gap. What the record shows
        // at gain 0 is measured: 0 of 30,240 seed-days at the clamp on 120
        // rosters, highest VIX 73.9, highest read-back 93.2, and the map
        // has one fixed point (b4fix9).
        //
        // CHARTER BAR B3, ruled 2026-09-12 (R15): a bound that never binds
        // on the record cannot have been tuned against any graded
        // statistic, so inertness satisfies B3 and the ledger entry stays
        // `derived`, carrying the condition, the solve and the measured
        // clip rate as its evidence.
        //
        // RE-DERIVED ON THE COMPOSED VECTOR'S LAW, 2026-09-14
        // (`programme/results/ceiling-and-omega.md` 4 and 5). The condition
        // above subtracts the target's fear term for a session at the top
        // of the graded range. Under the level-blind law that was the
        // constant `17 * 6.39` = 108.63; under the law this preset now runs
        // it is `8.83 * 6.39^1.4483 * C^-0.4483`, which at C = 181.3295 is
        // 12.5920 -- smaller by a factor of 8.6, two thirds of that the
        // gain and the rest the level factor. So the condition here holds
        // for any settled read-back under 168.7375, and the closed-form map
        // re-run with today's dials reads `implied(181.3295)` at 70.15 to
        // 75.89 with the largest read-back the engine ADMITS at that VIX --
        // the factor variance pinned at its own 32x clamp -- at 121.8 to
        // 134.5. The shipped ceiling cannot fail its own condition on this
        // map at any variance the engine can reach.
        //
        // WHAT THE RE-DERIVATION DOES NOT DO IS PICK THE VALUE. The
        // condition is a LOWER BOUND: monotone increasing on the left,
        // non-increasing on the right, so once met it stays met. Taking the
        // SMALLEST admissible C was defensible while that was 181 and the
        // model never came within 60 points of it. On this law it collapses
        // to 56 to 67 -- inside a record whose measured maximum VIX is
        // 60.59 over 12 rosters at 504 days -- so the rule that produced
        // 181.3295 now produces a ceiling that BINDS and forfeits the B3
        // argument above. 181.3295 is therefore b4fix7's solve CARRIED
        // FORWARD and re-verified, and the derivation's tightness is
        // demoted in the record rather than the value moved to whatever
        // restores an ordering.
        //
        // The ordering itself is withdrawn; see `vix_target_shock_cap`
        // above. The sentence that stood here, "`vix_target_shock_cap` is
        // 255.0 and stays above it", was true of a cap this preset no
        // longer ships and of a consequence the code never had.
        p.vix_ceiling = 181.3295;
        // THE FACTOR'S OWN MEMORY, MEASURED ON THE TAPE INSTEAD OF
        // SEARCHED, which the line above makes possible.
        //
        // Gaussian QMLE GARCH(1,1) on the tape's index over the whole span
        // (`garch-derive-design.md` §0): alpha 0.1059 with a sandwich se of
        // 0.0093 and a year-block bootstrap sd of 0.0128, beta 0.8787 with
        // 0.0092 and 0.0152, `corr(alpha, beta)` -0.88. They replace
        // pt-v14 search optima that carry no error bar at all, which is
        // charter bar B3.
        //
        // WHY THEY ARE ONLY TRANSPORTABLE NOW. Finding 4 of that note says
        // the tape's REDUCED-FORM values placed in the factor and then run
        // through the loop count the loop's memory twice, and its arm G
        // measured the cost on this row: a tail of 3.60 against arm B's
        // 2.61. b4fix2 re-measured it on this build and agreed -- the tape
        // values alone read 3.2271 at 252 against 3.0677 without them,
        // WORSE. `market_vol_vix_excursion` removes the double count at its
        // mechanism, and the same values then read 1.8194.
        //
        // Their own fourth-moment condition `3a^2 + 2ab + b^2` is 0.993
        // against the shipped fast component's 1.104, so the factor gains a
        // finite fourth moment it did not have. The 0.65/0.35 mixture
        // dilutes them to about (0.085, 0.895) -- finding 1 -- which is a
        // KNOWN RESIDUAL and is not corrected here, because inflating a
        // measured coefficient to cancel a mixture is a constant
        // compensating for a mechanism.
        //
        // THE SYMMETRIC FIT IS AN APPROXIMATION AND THE TAPE SAYS SO, so
        // the GJR triple below replaces it rather than sitting beside it.
        // The values here are the GJR fit's, not the GARCH(1,1) fit's.
        //
        // AND THE FIT THAT PRODUCED THEM IS NOT THE MODEL THAT RUNS THEM,
        // which the three provenance entries did not say until 2026-09-14
        // (defect-16, `programme/results/ceiling-and-omega.md` 7 to 9). The
        // fit estimated a FREE `omega` = 0.0202; `market/factor_vol.rs`
        // `component_step` applies the triple VARIANCE-TARGETED,
        // `omega = (1 - alpha - beta - gamma/2) * target`. Those are
        // different models and the tape can tell them apart: the fitted
        // model's own unconditional variance is 0.9656 against the tape's
        // 1.3053 -- 0.7398 of it -- where targeting sets the ratio to one,
        // and the restriction is rejected at a likelihood ratio of 7.06 on
        // one degree of freedom. What it costs the TRIPLE is small: the
        // constrained re-fit reads (0.0110, 0.1701, 0.8884), moves of
        // +0.54, +0.62 and -0.35 of the corrected Bollerslev-Wooldridge
        // bars, joint Wald 4.11 on 3 df. The fit pays for the constraint in
        // persistence instead, 0.9790 -> 0.9844, +1.14 of its own bar. None
        // of that is adopted: the level the constraint pins is the TAPE's
        // and the engine's is `market_factor_sigma`, calibrated on its own
        // evidence, so variance targeting is the only transport of these
        // three that does not require inventing a level. The gap is
        // recorded, not closed.
        // RECOMPOSED 2026-09-20, Simon's ruling: no new preset, pt-v19 IS the
        // vector that certifies. The 2^6 factorial over the six dial families
        // that separate pt-v18 from the 2026-09-14 composition (design repo,
        // programme/results/bestof/RESULT.md and RESULT-504.md, registered
        // first, both parents reproducing their records bit for bit) found
        // the market variance family -- the GJR triple, the slow pole and
        // the stochastic level -- away from the tape on volatility level,
        // cross-sectional correlation, correlation persistence and the fear
        // rows in 32 of 32 pairs at both horizons, against one gain on
        // kurtosis; and the idiosyncratic jump family moving nothing beyond
        // noise. Both return to pt-v18's values below. The VIX law stays,
        // and only WITH the two crisis dials: without them every cell runs
        // away over a two-year window. The derivations the returned values
        // replace stay recorded in the design repository; the comments that
        // argued them are kept above each line as the record of why they
        // were tried.
        //
        // AND RETURNED 2026-09-21 (the composition note above, at
        // `vix_level_persistence`): the factorial measured the family as one
        // block, and ptv19gjr measured its three parts apart. The GJR triple
        // and the slow pole return at the tape's values; the stochastic
        // level on the factor's target does not, and the regime level on the
        // VIX law takes its place.
        p.market_vol_alpha = 0.0066;   // the tape's; pt-v18's 0.28035004 from 2026-09-20 to 2026-09-21
        p.market_vol_beta = 0.8946;    // the tape's; pt-v18's 0.69244622 from 2026-09-20 to 2026-09-21
        // THE LEVERAGE RESPONSE, at a likelihood ratio of 305 on one degree
        // of freedom. `garch-derive-design.md` §2.4 fitted both forms to the
        // same tape and the same window:
        //
        //   GARCH(1,1)  omega 0.0190  alpha 0.1059  ---           beta 0.8787  NLL 3703.97
        //   GJR(1,1)    omega 0.0202  alpha 0.0066  gamma 0.1556  beta 0.8946  NLL 3551.49
        //
        // 2 * 152.5 = 305 on one degree of freedom. That note's own words:
        // "the real index's variance responds to DOWN moves almost
        // exclusively; the symmetric 0.1059 is the pseudo-true symmetric
        // approximation of that." It recorded the triple and did not adopt
        // it because `market_vol_gamma` was outside §2.2's list.
        //
        // WHY IT IS ADOPTED NOW. The symmetric approximation spreads a
        // one-sided response evenly and discards most of it, and what it
        // discards is exactly fourth moment. With it, the envelope's SHAPE
        // panel -- the fourteen rows measured on the HELD roster, which is
        // the protocol that certifies `excess_kurtosis` -- read 6.7284 at
        // 504 days against a band floor of 7.1 and 13 of 14 in band. With
        // the triple it reads **7.3005 and 14 of 14 at both horizons**. The
        // certification protocol's varying roster read 7.2496 and hid the
        // miss; it is not the bar for a shape row and was not used as one.
        //
        // AND IT DID NOT COST THE TAIL, which was the registered risk: a
        // GJR puts variance behind down moves and `index_tail_dn3_pct`
        // counts down moves. Measured, it IMPROVES: 1.8194 -> 1.3280 at 252
        // and 1.5838 -> 1.5905 at 504, both in band, and the panel stays 18
        // of 18 at both horizons on the varying roster.
        //
        // The fourth-moment coefficient of the GJR form (Appendix A:
        // `3a^2 + 3ag + 1.5g^2 + 2ab + bg + b^2`) is 0.9909, under one, so
        // the finite fourth moment the tape's coefficients bought survives
        // the asymmetry. `component_step` loads `alpha + gamma` on a down
        // day and `alpha` on an up one, and omega gives back `gamma/2`, so
        // the dial redistributes variance between the two states rather
        // than adding any -- and it passes 0.0 for the SLOW component,
        // which is where §2.4's fit does not reach.
        p.market_vol_gamma = 0.1556;   // the tape's; pt-v18's 0.0 from 2026-09-20 to 2026-09-21 (see the composition note above)

        // ==================================================================
        // THE COMPOSED VECTOR, adopted 2026-09-13 (`wtcomp1-result.md`).
        //
        // Everything below is measured on the tape in
        // `programme/results/vix-dynamics.md` and scored in
        // `programme/results/wtcomp1-result.md`, a 2^3 factorial plus
        // pt-v18 at 120 rosters at BOTH horizons. Against the whole-tape
        // tables plus the four new rows this vector reads 30.56 / 38.14
        // where pt-v18 reads 31.56 / 41.44: ahead at both horizons, and at
        // 504 ahead on 98 per cent of resampled roster draws with the 90
        // per cent interval of the paired difference excluding zero.
        //
        // The three changes compose ADDITIVELY -- every cell of the 2^3
        // lands within 1.40 of the sum of its main effects -- because they
        // reach different rows. The factor's slow pole is worth nothing at
        // 252 and two points at 504; the two per-name states are worth two
        // at 252 and one at 504. Neither alone cleared both horizons, which
        // is why no earlier arm was proposed.
        //
        // WHY THIS IS NOT THE SCORE THAT STARTED THE CAMPAIGN. pt-v19's
        // original 15-point margin was measured against rule tables centred
        // on 2015-2025. `programme/results/whole-tape.md` re-centres them on
        // the whole tape and that margin does not survive: the preset this
        // block replaces reads 61.5 / 61.0 on the honest tables, behind
        // pt-v18 at both horizons. The vector below is the first arm that
        // is ahead on a table its own centres were not chosen against.

        // THE RESPONSE LAW IS TWO LAWS (vix-dynamics.md section 2). Down
        // sessions are convex in the move and fall with the level; up
        // sessions are concave and proportional to it. The shipped
        // level-blind form is REFUSED at F = 118 against the tape.
        p.vix_return_exponent = 1.4483;
        p.vix_return_level_exponent = 0.4483;
        p.vix_return_exponent_up = 0.5433;
        p.vix_return_level_exponent_up = -1.0;

        // THE MEMORY AND THE GAIN ARE ONE CONSTRAINT, not two dials
        // (vix-dynamics.md section 11, and R13 withdrawn). `vix_return_gain`
        // was never independent of `vix_mean_reversion`: the pair satisfies
        // a single condition and (0.10, 17.0) was a valid point on it read
        // at the WRONG memory. At the tape's memory the gain is 8.83.
        p.vix_mean_reversion = 0.27;
        p.vix_return_gain = 8.83;
        p.vix_return_gain_up = 0.049;

        // THE INNOVATION. `vix_innovation_sigma` derives to ZERO: the VIX's
        // own innovation is the variance forecast's, which is why the tape's
        // residual persists. What remains is the return-coupled term.
        p.vix_innovation_sigma = 0.0;
        p.vix_innovation_return_sigma = 0.0175;
        p.vix_jump_level_scale = 1.700;
        p.vix_jump_return_intensity = 6.199;

        // THE CAP IS STILL THE CLAMP'S OWN IMAGE, under the law above
        // rather than under the level-blind one. The identity generalises:
        // `gain * clamp^p * floor^(-g)`, which at the shipped p = 1 and
        // g = 0 is the `gain * clamp` this block used to derive and at
        // 8.83 * 15^1.4483 * 10^-0.4483 is 158.8524. It is a LITERAL
        // because `powf` is not available in a `const fn`; the identity is
        // asserted in the test suite rather than trusted here.
        //
        // THE CAP IS THE SPIKE'S SUPREMUM, which is the property the
        // derivation wants, and 158.8524 is it. The down spike is
        // `gain * |r|^p * vix^-g` with `g` = 0.4483 > 0, so it rises in the
        // move and FALLS with the level: its supremum over the domain the
        // update admits -- `|r| <= vix_return_clamp` after `:1152`,
        // `vix >= 10` after the state clamp at `:1291` -- is attained at the
        // corner (15, 10) and is this literal. The session at which the cap
        // would truncate is `(cap * x^g / gain)^(1/p)`: 15.0000 exactly at
        // the VIX floor, 18.8725 at a VIX of 21, 36.7817 at 181.33. The
        // clamp binds first everywhere above the floor.
        //
        // THE ORDERING DEFECT, CLOSED 2026-09-14, AND THE INVARIANT
        // WITHDRAWN RATHER THAN RESTORED. This block used to flag that at
        // 158.85 the cap sits BELOW `vix_ceiling` (181.3295), which the
        // ceiling's provenance said must not happen "because a cap under
        // the ceiling binds first". It does sit below it, by 22.4771 points,
        // and nothing binds first: the cap truncates an ADDITIVE TERM of
        // the target (`:1167`) and the ceiling truncates the STATE after
        // the reversion step (`:1291`). They bound different quantities and
        // no expression compares them. A cap under the ceiling in fact
        // makes the ceiling LESS sticky -- a state at C is held there iff
        // `implied + min(cap, spike) >= C`, so the read-back needed to pin
        // the VIX to the ceiling goes from -73.67 (none) at a cap of 255 to
        // +22.48 here -- which is the direction the ceiling's own
        // derivation wants. `programme/results/ceiling-and-omega.md`
        // sections 3 to 5 in the design repository establish that, re-derive
        // the ceiling's condition on THIS law, and record why the
        // re-derivation's own answer (a ceiling of 56 to 67, which would
        // restore the ordering and would clip a record whose measured
        // maximum VIX is 60.59) is refused.
        p.vix_target_shock_cap = 158.8524;

        // THE PER-NAME MEMORY (vix-dynamics.md section 15.4). The tape's
        // per-name |r| autocorrelation needs a persistence the shipped
        // 0.6853 cannot carry; 0.7905 is the value that puts the name's
        // total at the tape's 0.9416.
        p.garch_beta = 0.7905;

        // THE FACTOR'S SLOW POLE (vix-dynamics.md section 17.4), from the
        // tape's forward-21-session realised-variance impulse response:
        // 0.9913 in [0.975, 1.0]. It replaces a 0.98 that was never read off
        // anything, and it is what carries the 504-day horizon.
        p.market_vol_slow_persistence = 0.9913;   // the tape's; pt-v18's 0.98 from 2026-09-20 to 2026-09-21

        // THE TWO PER-NAME STATES (vix-dynamics.md sections 19.5 and 19.7),
        // in the RATIO form: a GARCH(1,1) on the sector factor standardised
        // by its VIX-coupled target, and a jump excitation on the name. The
        // tape puts the sector's variance persistence at 0.904 and jumps at
        // 3.3x the day after one with a branching ratio of 0.13. The
        // ADDITIVE form of the same two states was measured and is worse at
        // 504 by 2.68 against a paired error bar of 1.60 (`whole-tape.md`
        // section 8), which is why the ratio form is what ships.
        p.sector_vol_alpha = 0.067;
        p.sector_vol_beta = 0.837;
        // The idiosyncratic jump family returns to pt-v18 (recomposed
        // 2026-09-20): 2.0 / 0.72 / 1.0 moved no row beyond noise in
        // either 32-pair contrast of the factorial.
        p.jump_idio_excitation = 0.0;
        p.jump_idio_excitation_decay = 0.0;
        p.jump_idio_vix_decoupled = 0.0;
        // THE SLOW VARIANCE LEVEL AND THE SECTOR LOADING, adopted 2026-09-14
        // from `levsec3` (`levsec3-result.md`) after `levelsec1`, `levsec2`
        // and `levsec3` measured them on 22 arms and 120 rosters at both
        // horizons.
        //
        // The level is the mechanism this preset was missing and it is the
        // largest single change the campaign has measured. It takes
        // `index_tail_dn3_pct` from 0.608 to 1.023 against a tape of 1.213,
        // `excess_kurtosis` from 8.56 to 12.21 against 11.06,
        // `corr_persistence_acf1` from -0.007 to a reading that clears its
        // own band, and it leaves `annualised_vol_pct` at 27.10 against
        // 27.66 because it is normalised on the square root of the level and
        // started from the level's stationary distribution.
        //
        // `market_vol_level_sigma` 0.085 is MEASURED and not solved. The
        // derivation in `cascade-fourth-moment.md` 4.3 said 0.047 by setting
        // the LEVEL's window-mean dispersion equal to the INDEX's deficit;
        // the level drives the FACTOR, which is about half the index, and
        // the transmission is measured at 0.50 at 252 and 0.69 at 504, flat
        // in the dose (`level-phi.md` 6 and 7). Read off the engine's own
        // output, the sigma that reproduces the tape's window log-variance
        // dispersion is 0.091 at 252 and 0.078 at 504; 0.085 is the midpoint
        // and the arm confirms the fit: `sd(log var)` reads 0.693 and 0.771
        // against a tape of 0.723 +/- 0.072.
        //
        // `market_vol_level_persistence` stays at 4.3's 0.9977.
        // `level-phi.md` 2 measures a shorter half-life on a better
        // estimator -- 127 to 249 sessions against 295 -- and the two tape
        // spans disagree by more than their own error, so the revision is
        // recorded and NOT taken: no arm has run at it.
        // Recomposed 2026-09-20: the level returns to OFF. Derived against
        // the index tail row alone (`cascade-fourth-moment.md` 4.2), and
        // measured by `ar1lever`, `levelscan` and the factorial to carry
        // three to four other rows the wrong way. 0.9977 / 0.085 until then.
        p.market_vol_level_persistence = 0.0;
        p.market_vol_level_sigma = 0.0;
        // The loading was derived against a centre the record then replaced.
        // `params.rs` recorded 0.8 as the value that "puts it back on centre
        // (0.1641 against 0.1640 at 252)", and 0.1640 was the 2015-2025
        // forty-name centre; the whole tape puts the row at 0.1178. 0.60 is
        // the DERIVED replacement (`sector-loading.md` 6.3) and the
        // `levelsec1` sweep MEASURED the centring loading at 0.596 at 252
        // and 0.609 at 504 on this base. The row goes from a term of 2.67 to
        // 0.00 at both horizons.
        p.sector_loading = 0.60;
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
            "pt-v18" => Some(PT_V18),
            "pt-v19" => Some(PT_V19),
            _ => None,
        }
    }

    /// Names of the shipped presets, for error messages.
    pub fn preset_names() -> &'static [&'static str] {
        &["pt-v1", "pt-v2", "pt-v3", "pt-v4", "pt-v5", "pt-v6", "pt-v7", "pt-v8", "pt-v9", "pt-v10",
          "pt-v11", "pt-v12", "pt-v13", "pt-v14", "pt-v15",
          "pt-v16", "pt-v18", "pt-v19"]
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
            "order_flow_impact_law" => self.order_flow_impact_law,
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
            "crash_amplifier_conditional_sigma" => self.crash_amplifier_conditional_sigma,
            "market_vol_vix_excursion" => self.market_vol_vix_excursion,
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
            "garch_vix_exponent" => self.garch_vix_exponent,
            "garch_floor_multiple" => self.garch_floor_multiple,
            "garch_omega_sector_scaled" => self.garch_omega_sector_scaled,
            "garch_innovation_commensurate" => self.garch_innovation_commensurate,
            "idio_sigma_floor" => self.idio_sigma_floor,
            "market_vol_alpha" => self.market_vol_alpha,
            "market_vol_beta" => self.market_vol_beta,
            "market_vol_gamma" => self.market_vol_gamma,
            "market_vol_alpha_excursion" => self.market_vol_alpha_excursion,
            "market_vol_level_persistence" => self.market_vol_level_persistence,
            "market_vol_level_sigma" => self.market_vol_level_sigma,
            "vix_level_persistence" => self.vix_level_persistence,
            "vix_level_sigma" => self.vix_level_sigma,
            "vix_level_loop_gain" => self.vix_level_loop_gain,
            "vix_anchor_reversion" => self.vix_anchor_reversion,
            "vix_anchor_weight" => self.vix_anchor_weight,
            "vix_anchor_memory" => self.vix_anchor_memory,
            "macro_compound_days_per_year" => self.macro_compound_days_per_year,
            "vix_anchor_centre" => self.vix_anchor_centre,
            "vix_anchor_weight_level" => self.vix_anchor_weight_level,
            "vix_anchor_weight_level_cap" => self.vix_anchor_weight_level_cap,
            "vix_anchor_weight_level_knee" => self.vix_anchor_weight_level_knee,
            "vix_anchor_weight_level_below" => self.vix_anchor_weight_level_below,
            "vix_anchor_weight_level_knee_fixed" => self.vix_anchor_weight_level_knee_fixed,
            "market_burn_in_sessions" => self.market_burn_in_sessions,
            "market_vol_ceiling_multiple" => self.market_vol_ceiling_multiple,
            "market_vol_floor_multiple" => self.market_vol_floor_multiple,
            "market_vol_vix_coupling" => self.market_vol_vix_coupling,
            "market_vol_vix_anchor" => self.market_vol_vix_anchor,
            "market_vol_vix_smooth" => self.market_vol_vix_smooth,
            "market_vol_vix_exponent" => self.market_vol_vix_exponent,
            "market_beta_down_asym" => self.market_beta_down_asym,
            "market_beta_down_asym_lag" => self.market_beta_down_asym_lag,
            "market_beta_down_asym_lag_live" => self.market_beta_down_asym_lag_live,
            "market_beta_down_asym_recentre" => self.market_beta_down_asym_recentre,
            "market_idio_down_suppress" => self.market_idio_down_suppress,
            "oil_supply_response" => self.oil_supply_response,
            "oil_opec_symmetry" => self.oil_opec_symmetry,
            "oil_seasonality_target" => self.oil_seasonality_target,
            "cycle_hazard_per_month" => self.cycle_hazard_per_month,
            "trough_growth_floor" => self.trough_growth_floor,
            "phase_target_range_draw" => self.phase_target_range_draw,
            "neutral_discount_rate" => self.neutral_discount_rate,
            "macro_burn_in_days" => self.macro_burn_in_days,
            "cycle_stationary_opening" => self.cycle_stationary_opening,
            "buyback_payout_share" => self.buyback_payout_share,
            "jump_mean_compensated" => self.jump_mean_compensated,
            "cascade_symmetry" => self.cascade_symmetry,
            "market_vol_slow_persistence" => self.market_vol_slow_persistence,
            "market_vol_slow_gain" => self.market_vol_slow_gain,
            "fair_value_book_floor" => self.fair_value_book_floor,
            "earnings_nominal_growth" => self.earnings_nominal_growth,
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
            "volume_move_jump_share" => self.volume_move_jump_share,
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
            "jump_market_variance_share" => self.jump_market_variance_share,
            "jump_vix_coupling" => self.jump_vix_coupling,
            "overnight_variance_ratio" => self.overnight_variance_ratio,
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
            "vix_return_level_exponent" => self.vix_return_level_exponent,
            "vix_return_exponent_up" => self.vix_return_exponent_up,
            "vix_return_level_exponent_up" => self.vix_return_level_exponent_up,
            "vix_innovation_sigma" => self.vix_innovation_sigma,
            "vix_innovation_return_sigma" => self.vix_innovation_return_sigma,
            "vix_jump_level_scale" => self.vix_jump_level_scale,
            "vix_jump_return_intensity" => self.vix_jump_return_intensity,
            "sector_vol_alpha" => self.sector_vol_alpha,
            "sector_vol_beta" => self.sector_vol_beta,
            "jump_idio_excitation" => self.jump_idio_excitation,
            "jump_idio_excitation_decay" => self.jump_idio_excitation_decay,
            "jump_idio_vix_decoupled" => self.jump_idio_vix_decoupled,
            "forced_flow_gain" => self.forced_flow_gain,
            "forced_flow_threshold" => self.forced_flow_threshold,
            "forced_flow_beta_exponent" => self.forced_flow_beta_exponent,
            "forced_flow_reservoir" => self.forced_flow_reservoir,
            "forced_flow_replenish" => self.forced_flow_replenish,
            "vix_cycle_amplitude" => self.vix_cycle_amplitude,
            "vix_realised_vol_weight" => self.vix_realised_vol_weight,
            "vix_level_identity" => self.vix_level_identity,
            "vix_variance_premium" => self.vix_variance_premium,
            "vix_return_clamp" => self.vix_return_clamp,
            "vix_return_gain" => self.vix_return_gain,
            "vix_return_gain_up" => self.vix_return_gain_up,
            "vix_return_exponent" => self.vix_return_exponent,
            "vix_return_source" => self.vix_return_source,
            "vix_target_shock_cap" => self.vix_target_shock_cap,
            "vix_ceiling" => self.vix_ceiling,
            "vix_target_offset" => self.vix_target_offset,
            "crisis_vix_threshold" => self.crisis_vix_threshold,
            "crisis_epicentre_extra" => self.crisis_epicentre_extra,
            "crisis_epicentre_end_sessions" => self.crisis_epicentre_end_sessions,
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
            "order_flow_impact_law" => out.order_flow_impact_law = value,
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
            "crash_amplifier_conditional_sigma" => out.crash_amplifier_conditional_sigma = value,
            "market_vol_vix_excursion" => out.market_vol_vix_excursion = value,
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
            "garch_vix_exponent" => out.garch_vix_exponent = value,
            "garch_floor_multiple" => out.garch_floor_multiple = value,
            "garch_omega_sector_scaled" => out.garch_omega_sector_scaled = value,
            "garch_innovation_commensurate" => out.garch_innovation_commensurate = value,
            "idio_sigma_floor" => out.idio_sigma_floor = value,
            "market_vol_alpha" => out.market_vol_alpha = value,
            "market_vol_beta" => out.market_vol_beta = value,
            "market_vol_gamma" => out.market_vol_gamma = value,
            "market_vol_alpha_excursion" => out.market_vol_alpha_excursion = value,
            "market_vol_level_persistence" => out.market_vol_level_persistence = value,
            "market_vol_level_sigma" => out.market_vol_level_sigma = value,
            "vix_level_persistence" => out.vix_level_persistence = value,
            "vix_level_sigma" => out.vix_level_sigma = value,
            "vix_level_loop_gain" => out.vix_level_loop_gain = value,
            "vix_anchor_reversion" => out.vix_anchor_reversion = value,
            "vix_anchor_weight" => out.vix_anchor_weight = value,
            "vix_anchor_memory" => out.vix_anchor_memory = value,
            "macro_compound_days_per_year" => out.macro_compound_days_per_year = value,
            "vix_anchor_centre" => out.vix_anchor_centre = value,
            "vix_anchor_weight_level" => out.vix_anchor_weight_level = value,
            "vix_anchor_weight_level_cap" => out.vix_anchor_weight_level_cap = value,
            "vix_anchor_weight_level_knee" => out.vix_anchor_weight_level_knee = value,
            "vix_anchor_weight_level_below" => out.vix_anchor_weight_level_below = value,
            "vix_anchor_weight_level_knee_fixed" => out.vix_anchor_weight_level_knee_fixed = value,
            "market_burn_in_sessions" => out.market_burn_in_sessions = value,
            "market_vol_ceiling_multiple" => out.market_vol_ceiling_multiple = value,
            "market_vol_floor_multiple" => out.market_vol_floor_multiple = value,
            "market_vol_vix_coupling" => out.market_vol_vix_coupling = value,
            "market_vol_vix_anchor" => out.market_vol_vix_anchor = value,
            "market_vol_vix_smooth" => out.market_vol_vix_smooth = value,
            "market_vol_vix_exponent" => out.market_vol_vix_exponent = value,
            "market_beta_down_asym" => out.market_beta_down_asym = value,
            "market_beta_down_asym_lag" => out.market_beta_down_asym_lag = value,
            "market_beta_down_asym_lag_live" => out.market_beta_down_asym_lag_live = value,
            "market_beta_down_asym_recentre" => out.market_beta_down_asym_recentre = value,
            "market_idio_down_suppress" => out.market_idio_down_suppress = value,
            "oil_supply_response" => out.oil_supply_response = value,
            "oil_opec_symmetry" => out.oil_opec_symmetry = value,
            "oil_seasonality_target" => out.oil_seasonality_target = value,
            "cycle_hazard_per_month" => out.cycle_hazard_per_month = value,
            "trough_growth_floor" => out.trough_growth_floor = value,
            "phase_target_range_draw" => out.phase_target_range_draw = value,
            "neutral_discount_rate" => out.neutral_discount_rate = value,
            "macro_burn_in_days" => out.macro_burn_in_days = value,
            "cycle_stationary_opening" => out.cycle_stationary_opening = value,
            "buyback_payout_share" => out.buyback_payout_share = value,
            "jump_mean_compensated" => out.jump_mean_compensated = value,
            "cascade_symmetry" => out.cascade_symmetry = value,
            "market_vol_slow_persistence" => out.market_vol_slow_persistence = value,
            "market_vol_slow_gain" => out.market_vol_slow_gain = value,
            "fair_value_book_floor" => out.fair_value_book_floor = value,
            "earnings_nominal_growth" => out.earnings_nominal_growth = value,
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
            "volume_move_jump_share" => out.volume_move_jump_share = value,
            "volume_variance_gain" => out.volume_variance_gain = value,
            "universe_stress_decay" => out.universe_stress_decay = value,
            "universe_stress_weight" => out.universe_stress_weight = value,
            "regime_stress_points" => out.regime_stress_points = value,
            "market_vol_slow_vix_damp" => out.market_vol_slow_vix_damp = value,
            "jump_intensity_idio" => out.jump_intensity_idio = value,
            "jump_intensity_market" => out.jump_intensity_market = value,
            "jump_mean_market" => out.jump_mean_market = value,
            "overnight_variance_ratio" => out.overnight_variance_ratio = value,
            "garch_beta_dispersion" => out.garch_beta_dispersion = value,
            "jump_momentum_share" => out.jump_momentum_share = value,
            "jump_sigma_idio" => out.jump_sigma_idio = value,
            "jump_market_variance_share" => out.jump_market_variance_share = value,
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
            "vix_return_level_exponent" => out.vix_return_level_exponent = value,
            "vix_return_exponent_up" => out.vix_return_exponent_up = value,
            "vix_return_level_exponent_up" => out.vix_return_level_exponent_up = value,
            "vix_innovation_sigma" => out.vix_innovation_sigma = value,
            "vix_innovation_return_sigma" => out.vix_innovation_return_sigma = value,
            "vix_jump_level_scale" => out.vix_jump_level_scale = value,
            "vix_jump_return_intensity" => out.vix_jump_return_intensity = value,
            "sector_vol_alpha" => out.sector_vol_alpha = value,
            "sector_vol_beta" => out.sector_vol_beta = value,
            "jump_idio_excitation" => out.jump_idio_excitation = value,
            "jump_idio_excitation_decay" => out.jump_idio_excitation_decay = value,
            "jump_idio_vix_decoupled" => out.jump_idio_vix_decoupled = value,
            "forced_flow_gain" => out.forced_flow_gain = value,
            "forced_flow_threshold" => out.forced_flow_threshold = value,
            "forced_flow_beta_exponent" => out.forced_flow_beta_exponent = value,
            "forced_flow_reservoir" => out.forced_flow_reservoir = value,
            "forced_flow_replenish" => out.forced_flow_replenish = value,
            "vix_cycle_amplitude" => out.vix_cycle_amplitude = value,
            "vix_realised_vol_weight" => out.vix_realised_vol_weight = value,
            "vix_level_identity" => out.vix_level_identity = value,
            "vix_variance_premium" => out.vix_variance_premium = value,
            "vix_return_clamp" => out.vix_return_clamp = value,
            "vix_return_gain" => out.vix_return_gain = value,
            "vix_return_gain_up" => out.vix_return_gain_up = value,
            "vix_return_exponent" => out.vix_return_exponent = value,
            "vix_return_source" => out.vix_return_source = value,
            "vix_target_shock_cap" => out.vix_target_shock_cap = value,
            "vix_ceiling" => out.vix_ceiling = value,
            "vix_target_offset" => out.vix_target_offset = value,
            "crisis_vix_threshold" => out.crisis_vix_threshold = value,
            "crisis_epicentre_extra" => out.crisis_epicentre_extra = value,
            "crisis_epicentre_end_sessions" => out.crisis_epicentre_end_sessions = value,
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

    // ── The identities the vector states and nothing checked ──────────────
    //
    // `provenance.py` marks twenty dials `derived`. Exactly two of them are
    // CLOSED FORMS over other fields of this struct -- `vix_target_shock_cap`
    // and `vix_return_level_exponent` -- so exactly two can be checked by
    // arithmetic on a `ModelParams` alone. Seven more are derived by a rule
    // from something the vector does not carry (a tape constant, a fit, a
    // burn-in path, a simulated pin ladder; `vix_ceiling` is one of THOSE and
    // is a lower bound solved at tolerance 8.64, not a closed form). Eleven
    // are switches whose identity is the value.
    //
    // Two things are enforced here and they are different in kind:
    //
    //  - `invariants` is UNIVERSAL. It holds on all eighteen shipped presets
    //    today and a vector that breaks it is refused with no hatch, because
    //    no reading taken on such a vector means anything.
    //  - `claimed_inconsistencies` is PER PRESET. The cap identity is a
    //    property of pt-v19 and not of the type: pt-v1 to pt-v8 ship a cap of
    //    12.0 against an image of 0.75 and pt-v9 to pt-v18 ship 45.0 against
    //    255.0, because those presets carried the cap as a DECLARED shape
    //    parameter. An unconditional assertion would refuse seventeen shipped
    //    presets, so the claim is owned by the preset that makes it.
    //
    // Neither recomputes anything. The cap's literal 158.8524 is a rounding
    // UP of 158.85236 and the rounding direction is what makes the cap
    // strictly inert; a recomputed field would carry 158.852360999924 and
    // every pt-v19 digest would move. Nothing below touches a field, so
    // `to_pairs`, `digest` and `fingerprint` are untouched by construction.

    /// The invariants EVERY vector must satisfy, whatever preset it came
    /// from. `Ok(())`, or the reason it is refused.
    ///
    /// One member today. `market_vol_vix_excursion` is an excursion above the
    /// level the index's own conditional variance implies, and off
    /// `vix_level_identity` there IS no such level: `engine.rs`'s branch at
    /// nonzero excursion divides by `vix_from_variance(premium, index
    /// variance)` whether or not the identity is on, while `derive_vix_anchor`
    /// returns the dial `market_vol_vix_anchor` when the identity is off. The
    /// pair then runs a ratio whose numerator is anchored to one scale and
    /// whose denominator is a read-back on another, silently.
    ///
    /// Pure; allocates only on the failure path.
    pub fn invariants(&self) -> Result<(), String> {
        if self.vix_level_sigma != 0.0 && !(self.vix_level_persistence < 1.0 && self.vix_level_persistence >= 0.0) {
            return Err(format!(
                "vix_level_sigma is {} but vix_level_persistence is {}. A slow level at \
                 or past a persistence of one has no stationary dispersion to open from or \
                 normalise against; it is a random walk on the VIX's own scale. Set the \
                 persistence inside [0, 1) or the sigma to 0.0.",
                self.vix_level_sigma, self.vix_level_persistence));
        }
        if self.vix_level_sigma != 0.0 && self.vix_level_identity == 0.0 {
            return Err(format!(
                "vix_level_sigma is {} but vix_level_identity is 0. The VIX level multiplies \
                 the target the identity derives from the index's own variance; off the \
                 identity the target is the phase table and the multiplier is not applied, \
                 so a non-zero sigma would be a dial that reads as live and does nothing.",
                self.vix_level_sigma));
        }
        if self.crisis_epicentre_extra != 0.0 {
            // BOTH squares, because the mechanism now moves both ways: the
            // epicentre's names up and the rest of the roster down, with the
            // roster's mean non-market variance held. So there is a floor
            // AND a ceiling, and asking the solve which one was hit is
            // better than restating its algebra here and letting the two
            // drift apart.
            let (up2, down2) = crate::market::factors::crisis_epicentre_gain_squares(
                self.crisis_epicentre_extra);
            let (lo, hi) = crate::market::factors::crisis_epicentre_extra_bounds();
            if !(up2 > 0.0 && down2 > 0.0) {
                return Err(format!(
                    "crisis_epicentre_extra is {}. It is a multiple on the epicentre \
                     names' TOTAL volatility, the market factor carries {} of a name's \
                     variance and is not scaled, and the epicentre sector carries {} of \
                     the roster's non-market variance, whose mean the solve holds -- so \
                     the solved squares are ({}, {}) and an extra outside ({}, {}) asks \
                     a name for a negative variance: below it the epicentre's own \
                     non-market parts, above it every other name's. Set it inside that \
                     interval, or to 0.0, where the mechanism does not run.",
                    self.crisis_epicentre_extra,
                    crate::market::factors::CRISIS_EPICENTRE_MARKET_SHARE,
                    crate::market::factors::CRISIS_EPICENTRE_SECTOR_SHARE,
                    up2, down2, lo, hi));
            }
        }
        if self.vix_level_loop_gain != 0.0 && !(self.vix_level_loop_gain > 0.0) {
            return Err(format!(
                "vix_level_loop_gain is {}. It divides the VIX level's dispersion and \
                 the loop's own transmission is 1 / (1 - h) with h the read-back's \
                 held-VIX elasticity in [0, 1), so it is never below one and never \
                 negative. A non-positive value would flip the level's sign or divide \
                 by nothing. Set it above zero or to 0.0, where it is not applied.",
                self.vix_level_loop_gain));
        }
        if self.vix_level_loop_gain != 0.0 && self.vix_level_sigma == 0.0 {
            return Err(format!(
                "vix_level_loop_gain is {} but vix_level_sigma is 0. The gain corrects \
                 the slow level's dispersion for what the variance loop does to it on \
                 the way to the VIX, and with no level there is nothing to correct, so \
                 it would read as live and do nothing. Set vix_level_sigma or the gain \
                 to 0.0.",
                self.vix_level_loop_gain));
        }
        if self.vix_anchor_reversion != 0.0
            && !(self.vix_anchor_reversion > 0.0 && self.vix_anchor_reversion < 1.0)
        {
            return Err(format!(
                "vix_anchor_reversion is {}. It is the share of the distance to the \
                 identity's anchor the VIX takes in ONE session, added to the step \
                 beside vix_mean_reversion, so it lives in [0, 1): at or above one the \
                 step lands on or past the anchor every day and the reversion becomes \
                 an oscillation, and below zero it pushes the VIX away from the anchor \
                 it is named for. Set it inside [0, 1), or to 0.0, where the term is \
                 not added at all.",
                self.vix_anchor_reversion));
        }
        if self.vix_anchor_reversion != 0.0 && self.vix_level_identity == 0.0 {
            return Err(format!(
                "vix_anchor_reversion is {} but vix_level_identity is 0. The reversion \
                 pulls the VIX toward the DERIVED identity anchor -- the VIX the \
                 index's own variance implies at the unconditional point -- times the \
                 slow level multiplier, and off the identity there is no such anchor: \
                 `derive_vix_anchor` returns the dial `market_vol_vix_anchor` and the \
                 VIX's target is the phase table, so the term would pull a VIX on one \
                 scale toward a level on another. Set vix_level_identity to 1.0, or \
                 vix_anchor_reversion to 0.0.",
                self.vix_anchor_reversion));
        }
        if self.vix_anchor_weight != 0.0
            && !(self.vix_anchor_weight > 0.0 && self.vix_anchor_weight < 1.0)
        {
            return Err(format!(
                "vix_anchor_weight is {}. It is the anchor's share of the VIX's \
                 target in logs, so it lives in [0, 1): at one the VIX ignores the \
                 index's variance altogether. Set it inside [0, 1), or to 0.0.",
                self.vix_anchor_weight));
        }
        if !(self.macro_compound_days_per_year >= 1.0 && self.macro_compound_days_per_year <= 366.0) {
            return Err(format!(
                "macro_compound_days_per_year is {}. It is the number of economy steps a \
                 year's GDP and CPI growth is compounded over: 365.0 as shipped, 252.0 on \
                 the session clock. Set it inside [1, 366].",
                self.macro_compound_days_per_year));
        }
        if self.vix_anchor_memory != 0.0
            && !(self.vix_anchor_memory > 0.0 && self.vix_anchor_memory <= 1.0)
        {
            return Err(format!(
                "vix_anchor_memory is {}. It is the share of today's deviation the \
                 anchor's memory takes in one session, so it lives in (0, 1], or \
                 0.0 for the instantaneous form.",
                self.vix_anchor_memory));
        }
        for (name, v) in [("vix_anchor_centre", self.vix_anchor_centre),
                          ("vix_anchor_weight_level", self.vix_anchor_weight_level),
                          ("vix_anchor_weight_level_cap", self.vix_anchor_weight_level_cap)] {
            if v != 0.0 && self.vix_anchor_weight == 0.0 {
                return Err(format!(
                    "{} is {} but vix_anchor_weight is 0. It shapes the anchor weight's \
                     pull and with no weight it is read by nothing. Set \
                     vix_anchor_weight, or {} to 0.0.", name, v, name));
            }
        }
        if !self.vix_anchor_centre.is_finite() || self.vix_anchor_centre.abs() > 3.0 {
            return Err(format!(
                "vix_anchor_centre is {}. It is a log offset on the anchor, so it lives \
                 in [-3, 3]; 0.0 is the anchor itself.", self.vix_anchor_centre));
        }
        if !(self.vix_anchor_weight_level >= 0.0 && self.vix_anchor_weight_level <= 4.0) {
            return Err(format!(
                "vix_anchor_weight_level is {}. It is the exponent of the weight's \
                 level law, in [0, 4]; 0.0 is the constant weight.",
                self.vix_anchor_weight_level));
        }
        if self.vix_anchor_weight_level_cap != 0.0
            && !(self.vix_anchor_weight_level_cap >= 1.0 && self.vix_anchor_weight_level_cap.is_finite())
        {
            return Err(format!(
                "vix_anchor_weight_level_cap is {}. It is a multiple of the centre at \
                 or above one, or 0.0 for no cap.", self.vix_anchor_weight_level_cap));
        }
        for (name, v) in [("vix_anchor_weight_level_knee", self.vix_anchor_weight_level_knee),
                          ("vix_anchor_weight_level_below", self.vix_anchor_weight_level_below),
                          ("vix_anchor_weight_level_knee_fixed", self.vix_anchor_weight_level_knee_fixed)] {
            if v != 0.0 && self.vix_anchor_weight_level == 0.0 {
                return Err(format!(
                    "{} is {} but vix_anchor_weight_level is 0: it shapes the level \
                     law and is read by nothing without it.", name, v));
            }
        }
        if !self.vix_anchor_weight_level_knee.is_finite() || self.vix_anchor_weight_level_knee.abs() > 3.0 {
            return Err(format!(
                "vix_anchor_weight_level_knee is {}. It is a log offset on the anchor, \
                 in [-3, 3].", self.vix_anchor_weight_level_knee));
        }
        if !(self.vix_anchor_weight_level_below == 0.0 || self.vix_anchor_weight_level_below == 1.0) {
            return Err(format!(
                "vix_anchor_weight_level_below is {}. It is a switch, 0.0 or 1.0.",
                self.vix_anchor_weight_level_below));
        }
        if !(self.vix_anchor_weight_level_knee_fixed == 0.0 || self.vix_anchor_weight_level_knee_fixed == 1.0) {
            return Err(format!(
                "vix_anchor_weight_level_knee_fixed is {}. It is a switch, 0.0 or 1.0.",
                self.vix_anchor_weight_level_knee_fixed));
        }
        if self.vix_anchor_weight_level_cap != 0.0 && self.vix_anchor_weight_level == 0.0 {
            return Err(format!(
                "vix_anchor_weight_level_cap is {} but vix_anchor_weight_level is 0: the \
                 cap bounds the level law and is read by nothing without it.",
                self.vix_anchor_weight_level_cap));
        }
        if self.vix_anchor_memory != 0.0 && self.vix_anchor_weight == 0.0 {
            return Err(format!(
                "vix_anchor_memory is {} but vix_anchor_weight is 0. The memory is \
                 what the anchor weight pulls against, and with no weight it is read \
                 by nothing. Set vix_anchor_weight, or vix_anchor_memory to 0.0.",
                self.vix_anchor_memory));
        }
        if self.vix_anchor_weight != 0.0 && self.vix_level_identity == 0.0 {
            return Err(format!(
                "vix_anchor_weight is {} but vix_level_identity is 0. The blend is \
                 between the identity's read-back and its derived anchor, and off \
                 the identity there is neither. Set vix_level_identity to 1.0, or \
                 vix_anchor_weight to 0.0.",
                self.vix_anchor_weight));
        }
        if self.market_vol_vix_excursion != 0.0 && self.vix_level_identity == 0.0 {
            return Err(format!(
                "market_vol_vix_excursion is {} but vix_level_identity is 0. The \
                 excursion reads the VIX's distance ABOVE the level the index's own \
                 conditional variance implies, and off the identity there is no such \
                 level: the ratio's denominator is the variance read-back while its \
                 numerator is anchored to `market_vol_vix_anchor`, so the ratio means \
                 something else and no band would catch it. Set vix_level_identity to \
                 1.0, or set market_vol_vix_excursion to 0.0. There is no override for \
                 this one.",
                self.market_vol_vix_excursion
            ));
        }
        Ok(())
    }

    /// The claims in `claims` evaluated on THIS vector: the ones that fail,
    /// in the order given. Empty is consistent.
    ///
    /// Pure, and `Vec::new()` does not allocate, so the success path is two
    /// or three `f64` comparisons and nothing else.
    pub fn claimed_inconsistencies(&self, claims: &[Claim]) -> Vec<Inconsistency> {
        let mut out: Vec<Inconsistency> = Vec::new();
        for claim in claims {
            let expected = (claim.expected)(self);
            let actual = self.get(claim.dial).unwrap_or(f64::NAN);
            if !((actual - expected).abs() <= claim.tolerance) {
                out.push(Inconsistency {
                    dial: claim.dial,
                    identity: claim.identity,
                    expected,
                    actual,
                    tolerance: claim.tolerance,
                    claimed_by: claim.claimed_by,
                });
            }
        }
        out
    }
}

/// The VIX state floor, the lower arm of the `clamp` in
/// `update_economy_daily` (`economy/daily.rs`, the `new_state.vix = clamp(..,
/// 10.0, inputs.vix_ceiling)` call). The down spike falls with the level, so
/// its supremum over the domain the update admits is attained HERE, and the
/// cap's identity is evaluated at this level. A literal in both places; the
/// `economy::daily` test `the_default_cap_is_the_clamps_own_image` pins it
/// against the update itself rather than against either comment.
pub const VIX_STATE_FLOOR: f64 = 10.0;

/// An identity a named preset claims about one of its own dials, evaluable
/// on a `ModelParams` and on nothing else.
///
/// `expected` is a plain `fn` rather than a closure so the claim tables can
/// be `'static`. It reads only fields of the vector handed to it.
pub struct Claim {
    /// The dial the identity determines.
    pub dial: &'static str,
    /// The identity in words, for the refusal message and for the arm record.
    pub identity: &'static str,
    /// Absolute tolerance on `|actual - expected|`.
    pub tolerance: f64,
    /// The preset that makes this claim.
    pub claimed_by: &'static str,
    /// The identity, evaluated.
    pub expected: fn(&ModelParams) -> f64,
}

/// One claim that did not hold, with both sides of it.
#[derive(Debug, Clone, PartialEq)]
pub struct Inconsistency {
    pub dial: &'static str,
    pub identity: &'static str,
    pub expected: f64,
    pub actual: f64,
    pub tolerance: f64,
    pub claimed_by: &'static str,
}

impl std::fmt::Display for Inconsistency {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{} is {} where {} claims {} ({}), a gap of {} against a tolerance of {}",
            self.dial,
            self.actual,
            self.claimed_by,
            self.expected,
            self.identity,
            (self.actual - self.expected).abs(),
            self.tolerance
        )
    }
}

/// The down spike's supremum: `gain * clamp^p * floor^-g`, evaluated at the
/// VIX state floor. `mathx::pow`, so the arithmetic is the engine's own.
fn cap_image(p: &ModelParams) -> f64 {
    p.vix_return_gain
        * crate::mathx::pow(p.vix_return_clamp, p.vix_return_exponent)
        * crate::mathx::pow(VIX_STATE_FLOOR, -p.vix_return_level_exponent)
}

/// `p - 1`: the standardised down law's level exponent is not free.
fn level_exponent_image(p: &ModelParams) -> f64 {
    p.vix_return_exponent - 1.0
}

/// The tape's per-name |r| autocorrelation decay rate, the six-window mean
/// measured in `vix-dynamics.md` 15.2 (sd 0.024). It is not a field of
/// `ModelParams` and cannot be, so the claim carries it as the constant it
/// is -- which is the whole reason `garch_beta` needs a claim table rather
/// than a universal assertion.
const PER_NAME_DECAY_RHO: f64 = 0.9416;

/// `rho - alpha - gamma/2`: the GJR first-moment persistence identity solved
/// for beta at the shipped alpha and gamma. The half in front of gamma is the
/// indicator's unconditional share.
fn garch_beta_image(p: &ModelParams) -> f64 {
    PER_NAME_DECAY_RHO - p.garch_alpha - p.garch_gamma / 2.0
}

/// pt-v19's claims. Three: the two closed forms, plus `garch_beta` with the
/// tape constant recorded beside it.
///
/// Tolerances. The cap's 1e-3 is the one
/// `economy::daily::tests::the_default_cap_is_the_clamps_own_image` already
/// uses, and the shipped gap is 3.9e-5 -- the literal's own rounding. The
/// level exponent's 1e-9 is far outside the shipped gap of 5.6e-17 and far
/// inside any move a search would make. `garch_beta`'s 1e-4 holds the
/// four-place rounding whose gap is 1.07e-7.
static PT_V19_CLAIMS: &[Claim] = &[
    Claim {
        dial: "vix_target_shock_cap",
        identity: "vix_return_gain * vix_return_clamp ** vix_return_exponent \
                   * 10 ** -vix_return_level_exponent, the down spike's supremum \
                   at the VIX state floor",
        tolerance: 1e-3,
        claimed_by: "pt-v19",
        expected: cap_image,
    },
    Claim {
        dial: "vix_return_level_exponent",
        identity: "vix_return_exponent - 1, the standardised down law",
        tolerance: 1e-9,
        claimed_by: "pt-v19",
        expected: level_exponent_image,
    },
    Claim {
        dial: "garch_beta",
        identity: "0.9416 - garch_alpha - garch_gamma / 2, the GJR persistence \
                   identity at the tape's per-name decay rate",
        tolerance: 1e-4,
        claimed_by: "pt-v19",
        expected: garch_beta_image,
    },
];

/// Nothing is claimed by the other seventeen. pt-v1 to pt-v8 ship the cap at
/// 12.0 against an image of 0.75 and pt-v9 to pt-v18 at 45.0 against 255.0;
/// they carried it as a declared shape parameter and are not wrong, they are
/// a different model. `garch_beta` moved with the tape only at pt-v19.
static NO_CLAIMS: &[Claim] = &[];

/// The identities `preset` claims about its own dials. Empty for a name that
/// claims nothing, including an unknown one.
pub fn claims_of(preset: &str) -> &'static [Claim] {
    match preset {
        "pt-v19" => PT_V19_CLAIMS,
        _ => NO_CLAIMS,
    }
}

/// The settable names, sorted. A function rather than the const above so
/// the list is derived from `to_pairs`' actual coverage in tests.
pub fn settable_names() -> Vec<&'static str> {
    vec![
        "cascade_symmetry",
        "crash_amplifier_conditional_sigma",
        "market_vol_vix_excursion",
        "crash_amplifier_slope",
        "crash_amplifier_threshold",
        "crisis_blend_cap",
        "crisis_blend_gain",
        "crisis_blend_ramp",
        "crisis_blend_source",
        "garch_omega_sector_scaled",
        "garch_innovation_commensurate",
        "idio_sigma_floor",
        "crisis_blend_variance_damp",
        "crisis_vix_threshold",
        "crisis_epicentre_extra",
        "crisis_epicentre_end_sessions",
        "crowd_lean_cap",
        "crowd_momentum_gain",
        "crowd_valuation_gain",
        "daily_credit_floor_gain",
        "endogenous_news_intensity",
        "endogenous_news_sigma",
        "fair_value_book_floor",
        "earnings_nominal_growth",
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
        "garch_vix_exponent",
        "idio_sigma_beta_exponent",
        "idio_sigma_scale",
        "inflation_ceiling",
        "inflation_floor",
        "inflation_reversion",
        "informed_flow_fraction",
        "jump_intensity_idio",
        "jump_intensity_market",
        "jump_market_variance_share",
        "jump_mean_compensated",
        "jump_mean_market",
        "jump_momentum_share",
        "jump_sigma_idio",
        "jump_sigma_market",
        "jump_vix_coupling",
        "market_factor_sigma",
        "market_vol_alpha",
        "market_vol_beta",
        "market_vol_gamma",
        "market_vol_alpha_excursion",
        "market_vol_level_persistence",
        "market_vol_level_sigma",
        "vix_level_persistence",
        "vix_level_sigma",
        "vix_level_loop_gain",
        "vix_anchor_reversion",
        "vix_anchor_weight",
        "vix_anchor_memory",
        "macro_compound_days_per_year",
        "vix_anchor_centre",
        "vix_anchor_weight_level",
        "vix_anchor_weight_level_cap",
        "vix_anchor_weight_level_knee",
        "vix_anchor_weight_level_below",
        "vix_anchor_weight_level_knee_fixed",
        "market_burn_in_sessions",
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
        "market_beta_down_asym_lag_live",
        "market_beta_down_asym_recentre",
        "market_idio_down_suppress",
        "oil_opec_symmetry",
        "oil_seasonality_target",
        "cycle_hazard_per_month",
        "trough_growth_floor",
        "phase_target_range_draw",
        "neutral_discount_rate",
        "macro_burn_in_days",
        "cycle_stationary_opening",
        "buyback_payout_share",
        "oil_supply_response",
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
        "order_flow_impact_law",
        "overnight_variance_ratio",
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
        "vix_return_level_exponent",
        "vix_return_exponent_up",
        "vix_return_level_exponent_up",
        "vix_innovation_sigma",
        "vix_innovation_return_sigma",
        "vix_jump_level_scale",
        "vix_jump_return_intensity",
        "sector_vol_alpha",
        "sector_vol_beta",
        "jump_idio_excitation",
        "jump_idio_excitation_decay",
        "jump_idio_vix_decoupled",
        "forced_flow_gain",
        "forced_flow_threshold",
        "forced_flow_beta_exponent",
        "forced_flow_reservoir",
        "forced_flow_replenish",
        "vix_realised_vol_weight",
        "vix_level_identity",
        "vix_variance_premium",
        "vix_return_clamp",
        "vix_return_gain",
        "vix_return_gain_up",
        "vix_return_exponent",
        "vix_return_source",
        "vix_target_shock_cap",
        "vix_ceiling",
        "vix_target_offset",
        "volume_idio_persistence",
        "volume_idio_sigma",
        "volume_idio_variance_gain",
        "volume_innovation_sigma",
        "volume_move_cap",
        "volume_move_floor",
        "volume_move_jump_share",
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

    /// `market_vol_vix_excursion` is an excursion above the level the
    /// index's own conditional variance implies, and off
    /// `vix_level_identity` there IS no such level: the engine's
    /// `vix_implied_from_market` is either zero or the FACTOR's sigma on a
    /// scale of `anchor / market_factor_sigma` rather than the identity's
    /// `100 sqrt(252)`. A preset that set one without the other would run a
    /// ratio whose denominator means something else, silently, and every
    /// number it produced would be wrong in a way no band would catch.
    ///
    /// Asserted over every shipped preset rather than over pt-v19 alone,
    /// because the mistake this prevents is a FUTURE preset's.
    ///
    /// It asks `ModelParams::invariants` rather than spelling the condition
    /// again, so the eighteen constants and every vector Python constructs
    /// are judged by ONE predicate. The test used to carry its own copy, and
    /// a copy is how the runtime and the test suite come to disagree.
    #[test]
    fn the_excursion_switch_requires_the_identity() {
        for name in ModelParams::preset_names() {
            let p = ModelParams::preset(name).expect("a name from preset_names resolves");
            assert!(
                p.invariants().is_ok(),
                "{name}: {}",
                p.invariants().unwrap_err()
            );
        }
        // And the predicate is not vacuous: the configuration it exists to
        // refuse is refused.
        let broken = ModelParams::preset("pt-v19")
            .expect("shipped")
            .with_override("vix_level_identity", 0.0)
            .expect("settable");
        assert!(broken.invariants().is_err());
    }

    /// **THE ANCHOR REVERSION'S THREE REFUSALS.** A rate at or above one
    /// lands on or past the anchor every session, a negative rate pushes the
    /// VIX away from the anchor it is named for, and off
    /// `vix_level_identity` there is no derived anchor at all -- the VIX's
    /// target is the phase table and `derive_vix_anchor` returns the dial,
    /// so the term would pull a VIX on one scale toward a level on another.
    ///
    /// The last is the same refusal `vix_level_sigma` carries and is written
    /// the same way, so a reader meets one rule rather than two.
    #[test]
    fn the_anchor_reversion_is_a_rate_in_the_unit_interval_and_needs_the_identity() {
        let shipped = ModelParams::preset("pt-v19").expect("shipped");
        // The derived value on the identity is admissible.
        let ok = shipped
            .with_override("vix_anchor_reversion", 0.046081)
            .expect("settable");
        assert!(ok.invariants().is_ok(), "{}", ok.invariants().unwrap_err());
        // A rate outside [0, 1) is not.
        for bad_rate in [-0.1, 1.0, 1.5] {
            let bad = shipped
                .with_override("vix_anchor_reversion", bad_rate)
                .expect("settable");
            let err = bad.invariants().expect_err(
                "a rate of {bad_rate} is outside [0, 1) and must be refused");
            assert!(err.contains("vix_anchor_reversion"), "{err}");
        }
        // And it is refused with the identity off, the way the sigma is.
        let no_identity = ModelParams::preset("pt-v18")
            .expect("shipped")
            .with_override("vix_anchor_reversion", 0.046081)
            .expect("settable");
        assert_eq!(no_identity.vix_level_identity, 0.0, "pt-v18 ships the identity off");
        let err = no_identity
            .invariants()
            .expect_err("a reversion with no derived anchor must be refused");
        assert!(err.contains("vix_level_identity"), "{err}");
        // Every shipped preset ships it at 0.0, where nothing is refused and
        // the term is not added.
        for name in ModelParams::preset_names() {
            let p = ModelParams::preset(name).expect("a name from preset_names resolves");
            assert_eq!(p.vix_anchor_reversion, 0.0, "{name} ships the dial non-zero");
        }
    }

    /// Every shipped preset satisfies the identities IT claims. pt-v19 is
    /// the only one that claims any; the other seventeen claim nothing, and
    /// that is the point -- pt-v9 through pt-v18 ship the cap at 45.0
    /// against an image of 255.0, so an unconditional assertion would refuse
    /// them.
    #[test]
    fn every_preset_satisfies_the_identities_it_claims() {
        for name in ModelParams::preset_names() {
            let p = ModelParams::preset(name).expect("a name from preset_names resolves");
            let bad = p.claimed_inconsistencies(claims_of(name));
            assert!(
                bad.is_empty(),
                "{name} breaks its own claim: {}",
                bad.iter().map(|i| i.to_string()).collect::<Vec<_>>().join("; ")
            );
        }
        // pt-v19 claims three and they are the three this note derives.
        assert_eq!(claims_of("pt-v19").len(), 3);
        assert_eq!(claims_of("pt-v18").len(), 0);
        assert_eq!(claims_of("no-such-preset").len(), 0);
    }

    /// The claims are not vacuous either, and the two closed forms catch the
    /// two mistakes the record actually contains.
    #[test]
    fn a_cap_off_its_image_is_an_undeclared_shape_parameter() {
        let base = ModelParams::preset("pt-v19").expect("shipped");

        // `A5caps` and `A7noident`: the cap set to the round number pt-v18
        // carried, under pt-v19's law.
        let cap45 = base.with_override("vix_target_shock_cap", 45.0).expect("settable");
        let bad = cap45.claimed_inconsistencies(claims_of("pt-v19"));
        assert_eq!(bad.len(), 1);
        assert_eq!(bad[0].dial, "vix_target_shock_cap");
        assert_eq!(bad[0].actual, 45.0);

        // `A2retdn`: the down law reverted to the level-blind form and the
        // cap left where pt-v19 put it. The image is then 255.0 and the
        // shipped 158.8524 is 96.15 points UNDER the supremum, so it
        // truncates sessions the clamp admits -- 9.3443 per cent and up,
        // against a clamp of 15 -- and the arm declared no such parameter.
        // The level-exponent claim still holds (g = p - 1 = 0), so the cap
        // is the only thing wrong and the check says so.
        //
        // WHETHER IT BOUND is a separate question from whether it was
        // declared, and it did NOT. MEASURED 2026-09-17 on the arm's own 30
        // seeds: that vector and the same vector with the cap at 255.0 give
        // BIT-EQUAL panels on all 30 and a bit-equal VIX probe, so the
        // recorded +0.0046 on `sector_excess_corr` is a reading of the down
        // law alone. The cap is live on this vector -- at 50 it moves 2 of 6
        // seeds and at 20 it moves 6 of 6 -- but the driving return never
        // reaches the 9.34 per cent this one needed. That is the same
        // finding `A5caps` already carried at 0.0000 on all 30 seeds with a
        // cap that truncates at 6.28. So this check refuses an UNDECLARED
        // shape parameter, which is what it is for; it is not refusing a
        // reading that was wrong.
        let retdn = base
            .with_override("vix_return_gain", 17.0)
            .expect("settable")
            .with_override("vix_return_exponent", 1.0)
            .expect("settable")
            .with_override("vix_return_level_exponent", 0.0)
            .expect("settable");
        let bad = retdn.claimed_inconsistencies(claims_of("pt-v19"));
        assert_eq!(bad.len(), 1);
        assert_eq!(bad[0].dial, "vix_target_shock_cap");
        assert!((bad[0].expected - 255.0).abs() < 1e-9, "image is {}", bad[0].expected);
        assert_eq!(bad[0].actual, 158.8524);

        // The exponent claim on its own: a search that moved `p` alone off a
        // level-blind base, which is what the wsa16-era arms did.
        let p_alone = base.with_override("vix_return_exponent", 1.2).expect("settable");
        let dials: Vec<&str> = p_alone
            .claimed_inconsistencies(claims_of("pt-v19"))
            .iter()
            .map(|i| i.dial)
            .collect();
        assert!(dials.contains(&"vix_return_level_exponent"), "{dials:?}");

        // And `garch_beta`, the class-B dial with its tape constant recorded
        // in the claim rather than pretended to be a field.
        let gb = base.with_override("garch_beta", 0.7).expect("settable");
        let dials: Vec<&str> = gb
            .claimed_inconsistencies(claims_of("pt-v19"))
            .iter()
            .map(|i| i.dial)
            .collect();
        assert_eq!(dials, vec!["garch_beta"]);
        // ... and the same override on pt-v1, which claims nothing, is fine.
        // `tests/test_model_params.py` does exactly this.
        let v1 = ModelParams::preset("pt-v1")
            .expect("shipped")
            .with_override("garch_beta", 0.7)
            .expect("settable");
        assert!(v1.claimed_inconsistencies(claims_of("pt-v1")).is_empty());
    }

    /// Nothing above writes a field. The proof that the shipped line cannot
    /// move is that the digest of every preset is the same before and after
    /// the predicates run on it.
    #[test]
    fn checking_a_vector_does_not_change_it() {
        for name in ModelParams::preset_names() {
            let p = ModelParams::preset(name).expect("shipped");
            let before = p.digest();
            let _ = p.invariants();
            let _ = p.claimed_inconsistencies(claims_of(name));
            assert_eq!(p.digest(), before);
            assert_eq!(p.fingerprint(), name.to_string());
        }
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
        assert_eq!(crate::params::PT_V18.fingerprint(), "pt-v18");
        // pt-v19 took the default at 0.8.0. While it was composed and not
        // yet the default, the line below this one asserted
        // `DEFAULT_PRESET_NAME == "pt-v18"`, so that composing a preset
        // could not move the default by accident; the move is deliberate
        // now, and the assertion moved with it.
        assert_eq!(crate::params::PT_V19.fingerprint(), "pt-v19");
        assert_eq!(DEFAULT_PRESET_NAME, "pt-v19");
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

    /// `preset_names` is a hand-written list beside a match that resolves
    /// names, and nothing compared the two. A preset added to the match and
    /// not to the list is invisible in the worst possible way: it resolves,
    /// so it runs; it is absent from the list, so `fingerprint` never
    /// recognises it and every consumer that reads the list -- the WASM
    /// surface, the Python binding, `preset_panel.py` -- silently omits it.
    ///
    /// The match cannot be enumerated, so this probes it: every `pt-vN` the
    /// match resolves must be in the list. It walks past gaps rather than
    /// stopping at them, which is the mistake that made this test worth
    /// writing -- `preset_panel.py` stopped at the reserved pt-v17 and
    /// measured every preset except pt-v18.
    #[test]
    fn the_listed_names_are_every_name_the_match_resolves() {
        let listed: Vec<String> =
            ModelParams::preset_names().iter().map(|s| (*s).to_string()).collect();
        let mut resolved = Vec::new();
        for i in 1..100 {
            let name = format!("pt-v{i}");
            if ModelParams::preset(&name).is_some() {
                resolved.push(name);
            }
        }
        assert_eq!(
            resolved, listed,
            "the match resolves {resolved:?} and preset_names lists              {listed:?}; a preset in one and not the other runs under a name              no consumer of the list can see"
        );
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

    /// NEVER RAN UNTIL 2026-09-06. It sat between two `#[test]` functions
    /// without one of its own, so cargo compiled it, warned that it was
    /// dead code among two other long-standing warnings, and no suite ever
    /// called it. A test whose subject moved reports green; a test that is
    /// never invoked reports nothing at all, and the difference is
    /// invisible in a passing run.
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

    // =====================================================================
    // The docstring-claim guard.
    //
    // `provenance.py` catches a dial whose VALUE drifts from its
    // justification. Nothing caught a dial whose DOCUMENTATION drifts from
    // its value, and on 2026-09-17 an audit of all 148 entries found 25 such
    // claims -- every one true when written and silently overtaken by a
    // later preset. This is that missing check.
    // =====================================================================

    /// The text of this file, as the compiler read it. `include_str!`
    /// resolves beside the source, so the docstrings parsed here and the
    /// constructors checked against them can never come from different
    /// trees -- which matters, because the venv's built extension is a
    /// `wip/joint-t-fit` build that disagrees with this tree on five pt-v19
    /// dials. Nothing here reads the built engine.
    const PARAMS_SOURCE: &str = include_str!("params.rs");

    /// The other files in this crate that make claims about dial values.
    ///
    /// The 2026-09-17 audit read `params.rs` and the guard below reads
    /// `params.rs`, which is where a dial's OWN entry lives. It is not
    /// where every claim about a dial's value lives: a call site explains
    /// why its branch is inert by naming the dial and the presets that
    /// leave it at zero, and such a sentence goes stale exactly as a
    /// docstring does. Measured 2026-09-18 over `rust/src`: five files
    /// besides `params.rs` carry a comment naming a settable dial beside a
    /// value and a preset scope. These are those five. Every other file in
    /// the crate has none -- `market_maker.rs`, `microstructure.rs`,
    /// `order_book.rs`, `mispricing.rs`, `mathx.rs`, `sectors.rs`,
    /// `units.rs`, `universe.rs`, `wasm.rs`, `lib.rs`, `types.rs` and the
    /// four other `python_*.rs` files do not name a settable dial beside a
    /// number at all.
    ///
    /// Same `include_str!` discipline and for the same reason: ONE TREE.
    /// The text is what the compiler read beside this file and the values
    /// are what it compiled into `ModelParams::preset`. Nothing here opens
    /// a built extension, a JSON preset on disk or anything else that
    /// could be a different branch.
    const OTHER_SOURCES: &[(&str, &str)] = &[
        ("engine.rs", include_str!("engine.rs")),
        ("rng.rs", include_str!("rng.rs")),
        ("fair_value.rs", include_str!("fair_value.rs")),
        ("python_engine.rs", include_str!("python_engine.rs")),
        ("python_params.rs", include_str!("python_params.rs")),
    ];

    /// Contiguous runs of `//`, `///` and `//!` lines, joined into one
    /// paragraph each, with the line the run starts on.
    ///
    /// Outside `params.rs` a claim has no `pub <name>: f64,` under it to
    /// say whose value it is, so the run itself is the unit and the dial
    /// is read out of the prose.
    fn comment_blocks(src: &str) -> Vec<(usize, String)> {
        let mut out: Vec<(usize, String)> = Vec::new();
        let mut block = String::new();
        let mut start = 0usize;
        for (n, line) in src.lines().enumerate() {
            let s = line.trim();
            if let Some(rest) = s.strip_prefix("//") {
                let rest = rest.strip_prefix('/').unwrap_or(rest);
                let rest = rest.strip_prefix('!').unwrap_or(rest);
                if block.is_empty() {
                    start = n + 1;
                } else {
                    block.push(' ');
                }
                block.push_str(rest.trim());
                continue;
            }
            if !block.is_empty() {
                out.push((start, std::mem::take(&mut block)));
            }
        }
        if !block.is_empty() {
            out.push((start, block));
        }
        out
    }

    /// Every settable dial a block names, in backticks or through a
    /// `ModelParams::` path -- the two forms this file uses to point at a
    /// dial, and the two `names_another_dial` already reads.
    fn dials_named(text: &str) -> Vec<String> {
        let names = settable_names();
        let mut out: Vec<String> = Vec::new();
        let mut push = |token: &str| {
            if names.contains(&token) && !out.iter().any(|s| s == token) {
                out.push(token.to_string());
            }
        };
        for chunk in text.split('`').skip(1).step_by(2) {
            push(chunk);
        }
        let mut rest = text;
        while let Some(at) = rest.find("ModelParams::") {
            let tail = &rest[at + "ModelParams::".len()..];
            let end = tail
                .find(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_'))
                .unwrap_or(tail.len());
            push(&tail[..end]);
            rest = &tail[end..];
        }
        out
    }

    /// One `///` block and the `pub <name>: f64,` it sits above.
    fn dial_doc_blocks(src: &str) -> Vec<(String, String)> {
        let mut out = Vec::new();
        let mut block = String::new();
        for line in src.lines() {
            let s = line.trim();
            if let Some(rest) = s.strip_prefix("///") {
                if !block.is_empty() {
                    block.push(' ');
                }
                block.push_str(rest.trim());
                continue;
            }
            if let Some(name) = s
                .strip_prefix("pub ")
                .and_then(|r| r.strip_suffix(": f64,"))
            {
                if !block.is_empty()
                    && !name.is_empty()
                    && name
                        .bytes()
                        .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_')
                {
                    out.push((name.to_string(), block.clone()));
                }
            }
            if !s.starts_with("#[") {
                block.clear();
            }
        }
        out
    }

    /// Sentences, split after `.` or `;`. A `;` separates a stale clause
    /// from the correction beside it throughout this file, so it has to
    /// break a sentence or the correction never gets read on its own.
    fn sentences(text: &str) -> Vec<&str> {
        let bytes = text.as_bytes();
        let mut out = Vec::new();
        let mut start = 0usize;
        for i in 0..bytes.len() {
            if (bytes[i] == b'.' || bytes[i] == b';')
                && i + 1 < bytes.len()
                && bytes[i + 1] == b' '
            {
                out.push(text[start..=i].trim());
                start = i + 1;
            }
        }
        if start < text.len() {
            out.push(text[start..].trim());
        }
        out.into_iter().filter(|s| !s.is_empty()).collect()
    }

    /// A sentence that reports what the entry USED to claim. The audit's
    /// corrections all carry one ("this line said X until 2026-09-17"), and
    /// reading the retracted wording as a live claim would fail the very
    /// tree that fixed it.
    fn is_historical(sentence: &str) -> bool {
        let lower = sentence.to_lowercase();
        const MARKERS: &[&str] = &[
            "until 20",
            "this line",
            "this paragraph",
            "this docstring",
            "this entry",
            "this sentence",
            "used to call",
            "used to claim",
            "used to read",
            "used to say",
            "no longer the shipped",
            "stopped being the shipped",
            "did not stay",
        ];
        MARKERS.iter().any(|m| lower.contains(m))
    }

    /// A sentence naming some OTHER settable dial may be reporting that
    /// dial's value, not this one's -- `market_idio_down_suppress` quotes
    /// "the shipped 0.025", which is `market_beta_down_asym`'s. Decline
    /// rather than guess.
    fn names_another_dial(sentence: &str, dial: &str) -> bool {
        let names = settable_names();
        let mut found = false;
        let mut scan = |token: &str| {
            if token != dial && names.contains(&token) {
                found = true;
            }
        };
        for chunk in sentence.split('`').skip(1).step_by(2) {
            scan(chunk);
        }
        let mut rest = sentence;
        while let Some(at) = rest.find("ModelParams::") {
            let tail = &rest[at + "ModelParams::".len()..];
            let end = tail
                .find(|c: char| !(c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_'))
                .unwrap_or(tail.len());
            scan(&tail[..end]);
            rest = &tail[end..];
        }
        found
    }

    /// `-?\d+(\.\d+)?([eE]-?\d+)?` at `i`, with a word boundary each side.
    fn number_at(s: &str, i: usize) -> Option<(f64, &str)> {
        let b = s.as_bytes();
        if i > 0 {
            let prev = b[i - 1];
            if prev.is_ascii_alphanumeric() || prev == b'.' || prev == b'-' {
                return None;
            }
        }
        let mut j = i;
        if j < b.len() && b[j] == b'-' {
            j += 1;
        }
        let digits = j;
        while j < b.len() && b[j].is_ascii_digit() {
            j += 1;
        }
        if j == digits {
            return None;
        }
        if j + 1 < b.len() && b[j] == b'.' && b[j + 1].is_ascii_digit() {
            j += 1;
            while j < b.len() && b[j].is_ascii_digit() {
                j += 1;
            }
        }
        if j < b.len() && (b[j] == b'e' || b[j] == b'E') {
            let mut k = j + 1;
            if k < b.len() && (b[k] == b'-' || b[k] == b'+') {
                k += 1;
            }
            let d = k;
            while k < b.len() && b[k].is_ascii_digit() {
                k += 1;
            }
            if k > d {
                j = k;
            }
        }
        // A trailing `.` ends the sentence unless a digit follows it, in
        // which case the token is a version string ("0.8.0"), not a value.
        if j < b.len()
            && (b[j].is_ascii_alphanumeric()
                || (b[j] == b'.' && j + 1 < b.len() && b[j + 1].is_ascii_digit()))
        {
            return None;
        }
        let text = &s[i..j];
        text.parse::<f64>().ok().map(|v| (v, text))
    }

    /// Is a claim of `claimed`, written as `text`, borne out by `actual`?
    /// The entries round -- "158.8524" for 158.85236... -- so the test is
    /// equality at the precision the author wrote, not at the bit.
    fn claim_holds(claimed: f64, actual: f64, text: &str) -> bool {
        if text.contains('e') || text.contains('E') {
            return (actual - claimed).abs() <= claimed.abs() * 1e-9;
        }
        let dp = text.split('.').nth(1).map_or(0, |f| f.len()) as i32;
        let scale = 10f64.powi(dp);
        ((actual * scale).round() / scale - claimed).abs() <= 0.5 * 10f64.powi(-dp - 6)
    }

    fn preset_index(name: &str) -> Option<usize> {
        ModelParams::preset_names().iter().position(|n| *n == name)
    }

    /// `pt-v<digits>` at `i`, returning the name's end and its index.
    fn preset_token_at(s: &str, i: usize) -> Option<(usize, usize)> {
        let rest = &s[i..];
        if !rest.starts_with("pt-v") {
            return None;
        }
        let tail = &rest[4..];
        let d = tail
            .find(|c: char| !c.is_ascii_digit())
            .unwrap_or(tail.len());
        if d == 0 {
            return None;
        }
        let name = &rest[..4 + d];
        preset_index(name).map(|idx| (i + 4 + d, idx))
    }

    fn word_at(s: &str, i: usize, word: &str) -> Option<usize> {
        if s.len() >= i + word.len() && s[i..].as_bytes()[..word.len()].eq_ignore_ascii_case(word.as_bytes()) {
            Some(i + word.len())
        } else {
            None
        }
    }

    /// The outcome of reading a preset-scope phrase.
    enum Scope {
        /// Presets the phrase names, by index into `preset_names()`.
        Presets(Vec<usize>),
        /// A phrase that IS a scope but whose boundary this parser will not
        /// guess: "every preset up to pt-v19" (the audit left it ambiguous
        /// because the file reads "up to" exclusively elsewhere), "every
        /// preset before this dial", "every preset before the fear era".
        /// Declining is the point -- an ambiguous claim must not become a
        /// build failure.
        Undecidable,
    }

    /// Read a preset-scope phrase starting at `i`. Returns where it ends.
    fn scope_at(s: &str, i: usize) -> Option<(usize, Scope)> {
        let last = ModelParams::preset_names().len() - 1;
        // "every [single ][shipped |existing ]preset[ <qualifier>]"
        if let Some(mut j) = word_at(s, i, "every ") {
            for opt in ["single ", "shipped ", "existing ", "SHIPPED "] {
                if let Some(k) = word_at(s, j, opt) {
                    j = k;
                }
            }
            if let Some(mut j) = word_at(s, j, "preset") {
                if let Some(k) = word_at(s, j, "s") {
                    j = k;
                }
                if let Some(k) = word_at(s, j, " before ") {
                    if let Some((e, idx)) = preset_token_at(s, k) {
                        return Some((e, Scope::Presets((0..idx).collect())));
                    }
                    return Some((k, Scope::Undecidable));
                }
                if let Some(k) = word_at(s, j, " through ") {
                    if let Some((e, idx)) = preset_token_at(s, k) {
                        return Some((e, Scope::Presets((0..=idx).collect())));
                    }
                    return Some((k, Scope::Undecidable));
                }
                if let Some(k) = word_at(s, j, " from ") {
                    if let Some((e, a)) = preset_token_at(s, k) {
                        if let Some(m) = word_at(s, e, " to ") {
                            if let Some((e2, b)) = preset_token_at(s, m) {
                                if a <= b {
                                    return Some((e2, Scope::Presets((a..=b).collect())));
                                }
                            }
                        }
                    }
                    return Some((k, Scope::Undecidable));
                }
                if word_at(s, j, " up to ").is_some() || word_at(s, j, " when ").is_some() {
                    return Some((j, Scope::Undecidable));
                }
                return Some((j, Scope::Presets((0..=last).collect())));
            }
        }
        // "pt-vA through pt-vB" | "pt-vA to pt-vB" | "pt-vN onward" | "pt-vN"
        if let Some((e, a)) = preset_token_at(s, i) {
            for sep in [" through ", " to "] {
                if let Some(k) = word_at(s, e, sep) {
                    if let Some((e2, b)) = preset_token_at(s, k) {
                        if a <= b {
                            return Some((e2, Scope::Presets((a..=b).collect())));
                        }
                        return Some((e2, Scope::Undecidable));
                    }
                }
            }
            for tail in [" onwards", " onward", " on "] {
                if let Some(k) = word_at(s, e, tail) {
                    return Some((k, Scope::Presets((a..=last).collect())));
                }
            }
            return Some((e, Scope::Presets(vec![a])));
        }
        None
    }

    /// One checkable assertion lifted out of a docstring.
    struct Claim {
        value: f64,
        text: String,
        presets: Vec<usize>,
        fragment: String,
        sentence: String,
    }

    /// Standalone "zero"/"Zero" reads as the value 0.0 throughout the file.
    fn normalise_zero(sentence: &str) -> String {
        let mut out = String::with_capacity(sentence.len());
        let mut rest = sentence;
        loop {
            let hit = rest
                .match_indices("ero")
                .map(|(i, _)| i)
                .find(|&i| {
                    i >= 1
                        && (rest.as_bytes()[i - 1] == b'Z' || rest.as_bytes()[i - 1] == b'z')
                        && (i == 1 || !rest.as_bytes()[i - 2].is_ascii_alphanumeric())
                        && rest[i + 3..]
                            .chars()
                            .next()
                            .map_or(true, |c| !c.is_ascii_alphanumeric())
                });
            match hit {
                Some(i) => {
                    out.push_str(&rest[..i - 1]);
                    out.push_str("0.0");
                    rest = &rest[i + 3..];
                }
                None => {
                    out.push_str(rest);
                    return out;
                }
            }
        }
    }

    /// What can sit between a value and the preset scope it is claimed
    /// over. Ordered longest-first so ", which is" wins over ",".
    ///
    /// MEASURED, and the measurement is why the list is this short. Two
    /// wider forms were tried on 2026-09-18 and both were dropped:
    ///
    ///   * " on" -- six refutations on this tree, all the same misreading.
    ///     "+0.0483 on pt-v18", "-0.933 on pt-v6", "+0.275 on pt-v3" are
    ///     statistics MEASURED ON a preset, not the dial's value under it.
    ///     " in", " under" and " across" read the same way: a scope after a
    ///     bare preposition names a RUN, not a setting.
    ///   * ", which is" -- it reattaches the scope to whatever number ends
    ///     the clause, and that number is often not the dial's.
    ///     `engine.rs`'s "0.0 means a multiplier of exactly 1.0, which is
    ///     every preset through pt-v18" would read 1.0 as the LEVEL SIGMA
    ///     and refute on seventeen presets, on a sentence that is true.
    ///
    /// A claim this parser cannot reach is a claim a reader has to check,
    /// which is the honest outcome; a claim it reaches WRONGLY is a build
    /// failure on correct prose, which is not.
    const LEADS: &[&str] = &[
        "--", "\u{2014}", ", which", " is what", " ships and is", " is", ",",
    ];

    const VERBS: &[&str] = &[
        "ships ", "ship ", "carries ", "carry ", "sets ", "set ", "uses ", "use ", "runs ",
        "run ",
    ];

    /// Every claim this parser is willing to judge, for one dial.
    ///
    /// `own_entry` says whether `doc` is the dial's OWN docstring. Inside
    /// `params.rs` it is, and an unattributed subject ("Shipped at 0.4",
    /// "the shipped 0.025") therefore belongs to the dial the block sits
    /// above. In a comment anywhere else there is no such attachment and an
    /// unattributed subject could be anybody's, so those forms are off and
    /// only the SCOPE-PAIRED forms -- which carry their own subject in the
    /// sentence -- are read.
    fn claims_in(dial: &str, doc: &str, own_entry: bool) -> Vec<Claim> {
        let default_idx = preset_index(DEFAULT_PRESET_NAME).expect("the default is a shipped preset");
        let mut out = Vec::new();
        for sentence in sentences(doc) {
            if is_historical(sentence) {
                continue;
            }
            let s = normalise_zero(sentence);
            let mut push = |value: f64, text: &str, presets: Vec<usize>, fragment: &str| {
                out.push(Claim {
                    value,
                    text: text.to_string(),
                    presets,
                    fragment: fragment.trim().to_string(),
                    sentence: sentence.to_string(),
                });
            };

            // --- Subject forms. The subject of a sentence that OPENS with
            // a value in this dial's own entry is this dial, so a later
            // mention of another dial cannot re-point it. Only in its own
            // entry: see `own_entry`.
            let head = s.trim_start();
            if own_entry {
                let mut done = false;
                for lead in ["Shipped at ", "SHIPPED at ", "Ships at ", "Shipped ", "SHIPPED ", "Ships "] {
                    if let Some(k) = head.strip_prefix(lead) {
                        let off = head.len() - k.len();
                        if let Some((v, t)) = number_at(head, off) {
                            push(v, t, vec![default_idx], &head[..off + t.len()]);
                            done = true;
                        }
                        break;
                    }
                }
                if !done {
                    if let Some((v, t)) = number_at(head, 0) {
                        if head[t.len()..].starts_with(" ships") {
                            push(v, t, vec![default_idx], &head[..t.len() + 6]);
                        }
                    }
                    if let Some(rest) = head.strip_prefix("At ") {
                        if let Some((v, t)) = number_at(head, 3) {
                            if rest[t.len()..].starts_with(", shipped,") {
                                push(v, t, vec![default_idx], &head[..3 + t.len() + 10]);
                            }
                        }
                    }
                }
                // "(0.0, shipped)" -- the switch-summary form.
                let mut at = 0usize;
                while let Some(p) = s[at..].find('(') {
                    let i = at + p + 1;
                    if let Some((v, t)) = number_at(&s, i) {
                        if s[i + t.len()..].starts_with(", shipped)") {
                            push(v, t, vec![default_idx], &s[i - 1..i + t.len() + 10]);
                        }
                    }
                    at = i;
                }
            }

            // --- Scope-paired forms, declined where the sentence could be
            // reporting another dial's value.
            if names_another_dial(sentence, dial) {
                continue;
            }
            // A scope phrase is consumed whole. "every preset before
            // pt-v18" contains "pt-v18", and reading the inner token as a
            // scope of its own pairs the sentence's value with the ONE
            // preset the phrase excludes -- which is how
            // `engine.rs:375`'s true "inert under every preset before
            // pt-v18, where `earnings_nominal_growth` is 0.0" came out as
            // a claim that pt-v18 is 0.0, refuted by pt-v18 = 1.0.
            let mut consumed_to = 0usize;
            for i in 0..s.len() {
                if i < consumed_to || !s.is_char_boundary(i) {
                    continue;
                }
                let (end, scope) = match scope_at(&s, i) {
                    Some(x) => x,
                    None => continue,
                };
                consumed_to = end;
                let presets = match scope {
                    Scope::Presets(p) => p,
                    Scope::Undecidable => continue,
                };
                let before = s[..i].trim_end();
                let after = &s[end..];

                // <value> -- <scope> -- | <value>, which <scope> | (<value>, <scope>)
                // | <value> is <scope> | shipped <value> in <scope>
                for lead in LEADS {
                    let stem = match before.strip_suffix(lead) {
                        Some(x) => x.trim_end(),
                        None => continue,
                    };
                    let stem = stem.trim_end_matches(['`', '(']).trim_end();
                    let num_start = stem.len() - stem.chars().rev()
                        .take_while(|c| c.is_ascii_digit() || *c == '.' || *c == '-' || *c == 'e' || *c == 'E')
                        .map(|c| c.len_utf8()).sum::<usize>();
                    if let Some((v, t)) = number_at(stem, num_start) {
                        if num_start + t.len() == stem.len() {
                            push(v, t, presets.clone(), &s[num_start..end]);
                            break;
                        }
                    }
                }
                // "<scope>, where `<dial>` is <value>" and "<scope>, where
                // `<a>` and `<b>` are both <value>" -- the call-site form,
                // where the dial is named AFTER the scope rather than
                // before it. The dial has to be the one being judged.
                if let Some(w) = after.find("where ") {
                    let clause = &after[w + 6..];
                    let stop = clause.find(". ").unwrap_or(clause.len());
                    let clause = &clause[..stop];
                    if clause.contains(&format!("`{dial}`")) {
                        for verb in [" is ", " are ", " are both ", " is still ", " is exactly "] {
                            if let Some(k) = clause.find(verb) {
                                let mut j = k + verb.len();
                                for skip in ["both ", "exactly ", "still "] {
                                    if clause[j..].starts_with(skip) {
                                        j += skip.len();
                                    }
                                }
                                if let Some((v, t)) = number_at(clause, j) {
                                    push(v, t, presets.clone(), &format!("where {dial} {} {t}", verb.trim()));
                                    break;
                                }
                            }
                        }
                    }
                }
                // "Shipped at <value> in <scope>"
                if let Some(stem) = before.strip_suffix(" in") {
                    let stem = stem.trim_end();
                    let num_start = stem.len() - stem.chars().rev()
                        .take_while(|c| c.is_ascii_digit() || *c == '.' || *c == '-' || *c == 'e' || *c == 'E')
                        .map(|c| c.len_utf8()).sum::<usize>();
                    if let Some((v, t)) = number_at(stem, num_start) {
                        if num_start + t.len() == stem.len()
                            && stem[..num_start].to_lowercase().contains("shipped")
                        {
                            push(v, t, presets.clone(), &s[num_start..end]);
                        }
                    }
                }
                // <scope> ships <value>
                let after_trim = after.trim_start();
                let skipped = after.len() - after_trim.len();
                for verb in VERBS {
                    if let Some(k) = word_at(after, skipped, verb) {
                        let k = match word_at(after, k, "at ") {
                            Some(m) => m,
                            None => k,
                        };
                        if let Some((v, t)) = number_at(after, k) {
                            push(v, t, presets.clone(), &s[i..end + k + t.len()]);
                        }
                        break;
                    }
                }
                // "At `1.0` ... which is what <scope> does" -- indicative,
                // unlike "at X every preset is bit-identical", which is a
                // counterfactual about a value no preset need set.
                if after_trim.starts_with("does")
                    || after_trim.starts_with("do ")
                    || after_trim.starts_with("did")
                {
                    if s[..i].trim_end().ends_with("what") {
                        let opener = head.strip_prefix("At ").unwrap_or("");
                        let opener = opener.strip_prefix('`').unwrap_or(opener);
                        if let Some((v, t)) = number_at(opener, 0) {
                            push(v, t, presets.clone(), &format!("At {t} ... what {}", &s[i..end]));
                        }
                    }
                }
            }
            // "the shipped <value>" -- this dial's, the sentence naming no
            // other. A subject form, so its own entry only.
            if !own_entry {
                continue;
            }
            let mut at = 0usize;
            while at < s.len() {
                let hit = ["the shipped ", "The shipped "]
                    .iter()
                    .filter_map(|m| s[at..].find(m).map(|p| (p, m.len())))
                    .min();
                let (p, len) = match hit {
                    Some(x) => x,
                    None => break,
                };
                let i = at + p + len;
                if let Some((v, t)) = number_at(&s, i) {
                    push(v, t, vec![default_idx], &s[at + p..i + t.len()]);
                }
                at = i;
            }
        }
        out
    }

    /// Every "shipped X" and "every preset X" claim in a dial docstring,
    /// read against the constructors in this file.
    ///
    /// # What it checks
    ///
    /// For each of the 148 settable dials, the `///` block above the field
    /// is split into sentences and each sentence searched for a value
    /// asserted over a preset scope:
    ///
    ///   * a SHIPPED-VALUE claim -- "Shipped X", "Ships at X", "X ships",
    ///     "(X, shipped)", "At X, shipped,", "the shipped X", which name
    ///     `DEFAULT_PRESET_NAME`;
    ///   * a SCOPED claim -- "X -- every shipped preset --", "X, which
    ///     every preset carries", "pt-v15 onward ship X", "X -- pt-v1
    ///     through pt-v8 --", "Shipped at X in every preset", "at X ...
    ///     which is what every shipped preset does";
    ///
    /// and the value is compared against `ModelParams::preset(name).get()`
    /// for every preset the scope names, at the precision the author wrote.
    /// An inertness claim ("INERT at X, which every shipped preset sets")
    /// is checked through its scope, which is the only part of it the
    /// preset table can refute.
    ///
    /// Both sides come from THIS tree: the values from the constructors the
    /// compiler just read, the text from `include_str!` on this same file.
    /// Nothing reads the built extension, which is a different branch.
    ///
    /// # What it declines to judge, deliberately
    ///
    /// A sentence reporting a RETRACTED claim ("this line said X until
    /// 2026-09-17"), a sentence naming another settable dial (it may be
    /// quoting that dial's value), and a scope whose boundary is not
    /// mechanical: "every preset up to pt-v19" (read exclusively elsewhere
    /// in this file), "every preset before this dial", "every preset before
    /// the fear era". `crisis_vix_threshold`'s "25.5 is the P94",
    /// `usd_crisis_vix_threshold`'s count of overriding presets and
    /// `jump_intensity_market`'s counterfactual "at intensity 0 ... every
    /// shipped preset reproduces exactly" are all outside the grammar and
    /// stay outside it: the audit judged those four ambiguous rather than
    /// false, and an ambiguous claim must not become a build failure.
    ///
    /// # Coverage, and the residue it does NOT cover
    ///
    /// The 2026-09-17 audit read 102 of the 148 entries as claim-bearing
    /// and found 25 false. Replayed against the docstrings as they stood at
    /// `b9a151c`, this guard fires on 18 of those 25, over 45 dials that
    /// carry at least one claim it will judge. A GREEN RUN IS NOT "EVERY
    /// CLAIM VERIFIED". Four of the seven it misses are claims no
    /// preset-table rule can reach, and the audit said so:
    ///
    ///   1. `market_vol_alpha` attributed two values to pt-v13 that are
    ///      pt-v14's -- a claim about WHICH preset, in prose.
    ///   2. `market_vol_level_sigma` presented a derived 0.047 where
    ///      `provenance.py` records the dial measured at 0.085 -- a
    ///      derived-against-measured mismatch, not a preset value.
    ///   3. `trough_growth_floor` cited `daily.rs:247` for a shock that is
    ///      at `:568` -- a stale line reference.
    ///   4. `cycle_stationary_opening` named `DRAW_SCHEDULE_MOVERS`, renamed
    ///      `ECONOMY_STREAM_MOVERS` and absent from the tree.
    ///
    /// The other three are prose with no parseable value: `market_vol_gamma`'s
    /// "symmetric forever", `endogenous_news_intensity`'s "has never fired",
    /// `market_vol_slow_vix_damp`'s "Kept, inert", plus
    /// `vix_return_level_exponent`'s "`g = 0` on both sides", where the
    /// value is an equation rather than a number. A reader who wants those
    /// checked has to read them.
    #[test]
    fn every_shipped_value_claim_in_a_dial_docstring_holds_against_the_preset_table() {
        let names = ModelParams::preset_names();
        let settable = settable_names();
        let mut failures: Vec<String> = Vec::new();
        let mut judged_dials = 0usize;

        for (dial, doc) in dial_doc_blocks(PARAMS_SOURCE) {
            if !settable.contains(&dial.as_str()) {
                continue;
            }
            let claims = claims_in(&dial, &doc, true);
            if !claims.is_empty() {
                judged_dials += 1;
            }
            for claim in claims {
                let mut refuting: Vec<String> = Vec::new();
                for &p in &claim.presets {
                    let actual = ModelParams::preset(names[p])
                        .expect("a name from preset_names resolves")
                        .get(&dial)
                        .expect("a settable name reads back");
                    if !claim_holds(claim.value, actual, &claim.text) {
                        refuting.push(format!("{} = {actual:?}", names[p]));
                    }
                }
                if !refuting.is_empty() {
                    failures.push(format!(
                        "\n  {dial}: the docstring claims {} over {}, and the preset table \
                         refutes it.\n    claim    : \"{}\"\n    sentence : \"{}\"\n    refuted by: {}",
                        claim.text,
                        if claim.presets.len() == 1 {
                            names[claim.presets[0]].to_string()
                        } else {
                            format!(
                                "{} .. {} ({} presets)",
                                names[claim.presets[0]],
                                names[*claim.presets.last().unwrap()],
                                claim.presets.len()
                            )
                        },
                        claim.fragment,
                        claim.sentence,
                        refuting.join(", "),
                    ));
                }
            }
        }

        // A parser that has gone blind passes trivially, which is the one
        // way this guard could rot without anyone noticing. 45 dials carry
        // a judged claim at 5c5c8c9; a floor of 40 leaves room for an entry
        // to be reworded without leaving room for the grammar to stop
        // matching.
        assert!(
            judged_dials >= 40,
            "the docstring-claim parser judged only {judged_dials} dials, against 45 at
             5c5c8c9. Either the claim wording moved out of the grammar or the parser \
             broke; a guard that reads nothing passes everything."
        );

        assert!(
            failures.is_empty(),
            "{} dial docstring claim(s) contradict the constructors in this file. Every \
             one is the 2026-09-17 defect: a claim true when written and overtaken by a \
             later preset. Fix the PROSE -- no dial value moves for this.{}",
            failures.len(),
            failures.join("")
        );
    }

    /// The same claims, in the OTHER files that make them.
    ///
    /// # Why this exists beside the test above
    ///
    /// The guard above reads `params.rs`, where a dial's own entry lives.
    /// A dial's value is also asserted at its CALL SITES, where a comment
    /// explains that a branch is inert because every preset so far leaves
    /// the dial at zero. That sentence is the same kind of claim, goes
    /// stale the same way, and nothing was reading it: `engine.rs:2465`
    /// said `market_vol_level_sigma` was 0.0 "through pt-v19" for as long
    /// as pt-v19 has shipped 0.085.
    ///
    /// # How the subject is decided, since there is no field underneath
    ///
    /// A run of comment lines is the unit. If it names exactly ONE settable
    /// dial, the whole run is read as being about that dial. If it names
    /// several, only the sentences naming that one dial and no other are
    /// read for it. Either way the SUBJECT FORMS are off (see
    /// `claims_in`'s `own_entry`): "the shipped 0.4" in a free comment has
    /// no field under it to belong to, so this guard judges only the
    /// scope-paired forms, which carry their subject in the sentence.
    ///
    /// Same one-tree property as above, and it is the whole point: the
    /// prose comes from `include_str!` on the files beside this one and the
    /// values from `ModelParams::preset` compiled out of this one.
    #[test]
    fn every_shipped_value_claim_outside_params_holds_against_the_preset_table() {
        let names = ModelParams::preset_names();
        let mut failures: Vec<String> = Vec::new();
        let mut judged_blocks = 0usize;

        for &(file, src) in OTHER_SOURCES {
            let mut judged = 0usize;
            for (line, block) in comment_blocks(src) {
                let dials = dials_named(&block);
                if dials.is_empty() {
                    continue;
                }
                let mut judged_here = false;
                for dial in &dials {
                    // OUTSIDE a dial's own entry, only a sentence that
                    // NAMES the dial is read for it. Measured: without
                    // this, `engine.rs:3177`'s "Exactly 1.0 under every
                    // preset before pt-v18" -- a claim about the nominal
                    // SCALE, in a block that happens to mention
                    // `earnings_nominal_growth` six sentences earlier --
                    // reads as a claim about the dial and refutes on
                    // sixteen presets. A comment paragraph is not one
                    // dial's entry and must not be read as one.
                    let doc: String = sentences(&block)
                        .into_iter()
                        .filter(|s| {
                            let d = dials_named(s);
                            d.len() == 1 && &d[0] == dial
                        })
                        .collect::<Vec<_>>()
                        .join(" ");
                    for claim in claims_in(dial, &doc, false) {
                        judged_here = true;
                        let mut refuting: Vec<String> = Vec::new();
                        for &i in &claim.presets {
                            let actual = ModelParams::preset(names[i])
                                .expect("a name from preset_names resolves")
                                .get(dial)
                                .expect("a settable name reads back");
                            if !claim_holds(claim.value, actual, &claim.text) {
                                refuting.push(format!("{} = {actual:?}", names[i]));
                            }
                        }
                        if refuting.is_empty() {
                            continue;
                        }
                        failures.push(format!(
                            "\n  {file}:{line} on `{dial}`: the comment claims {} over {}, \
                             and the preset table refutes it.\n    claim    : \"{}\"\n    \
                             sentence : \"{}\"\n    refuted by: {}",
                            claim.text,
                            if claim.presets.len() == 1 {
                                names[claim.presets[0]].to_string()
                            } else {
                                format!(
                                    "{} .. {} ({} presets)",
                                    names[claim.presets[0]],
                                    names[*claim.presets.last().unwrap()],
                                    claim.presets.len()
                                )
                            },
                            claim.fragment,
                            claim.sentence,
                            refuting.join(", "),
                        ));
                    }
                }
                if judged_here {
                    judged += 1;
                }
            }
            judged_blocks += judged;
        }

        // The same anti-rot floor the entry above carries, for the same
        // reason: a parser that stops matching passes everything. FIVE
        // comment blocks outside `params.rs` carry a claim this grammar
        // judges -- four in `engine.rs`, one in `rng.rs` -- and seven
        // claims between them, over 86 preset readings. The floor is four,
        // one block of headroom for a rewording.
        assert!(
            judged_blocks >= 4,
            "the comment-claim parser judged only {judged_blocks} blocks outside \
             params.rs, against 5 when this was written. Either the wording moved out \
             of the grammar or the parser broke."
        );

        assert!(
            failures.is_empty(),
            "{} comment(s) outside params.rs claim a dial value the constructors in \
             this file refute. Fix the PROSE -- no dial value moves for this.{}",
            failures.len(),
            failures.join("")
        );
    }
}
