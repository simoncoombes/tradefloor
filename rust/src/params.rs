//! The runtime parameter seam — `ModelParams`, the settable half of the
//! model preset.
//!
//! # What this is
//!
//! The model's coefficients as a value instead of a rebuild. Every constant
//! in the live dynamics chain — the tick loop, the per-name GARCH close, the
//! market factor's variance process — is carried here as a plain `f64`, and
//! the engine reads the field where it used to read the `pub const`. The
//! constants themselves REMAIN, as the definition of the shipped preset:
//! [`PT_V1`](crate::params::PT_V1) is built from them, so every existing test asserting a constant
//! still guards the preset, and a build whose constants moved fingerprints
//! differently by construction.
//!
//! # Bit-identity is the contract
//!
//! Replacing a `const` read with a field read does not change IEEE-754
//! arithmetic: same values, same operations, same order. Rust neither
//! reassociates nor contracts floating point under any default profile, and
//! this crate additionally bans `mul_add` and non-`mathx` transcendentals.
//! The one hazard, a `const` deriving another, is handled by
//! deriving once, in the constructor: the circuit-breaker band multipliers
//! ([`breaker_up`](crate::params::ModelParams::breaker_up) and
//! [`breaker_down`](crate::params::ModelParams::breaker_down)) are computed
//! when the params are built, never per call site. The acceptance gate is
//! trajectory equality: an engine built from `PT_V1` must reproduce the
//! const build's known-answer digest bit for bit, and does — see
//! `tests/test_model_params.py`.
//!
//! # Membership
//!
//! Four classes, and the draw-schedule rule above all: **nothing settable
//! may change how many draws are taken or in what order.** A preset changes
//! what the draws are multiplied into, never the schedule — that is what
//! keeps every preset comparable under common random numbers and replayable
//! against order logs.
//!
//! 1. **Settable** — the live dynamics numbers ([`settable_names`](crate::params::settable_names)): the searched
//!    surface (both variance processes, the factor sigmas and their scale,
//!    the mispricing dynamics) plus the guards that live in the threaded
//!    chain (the mispricing cap, the crowd lean cap, the price breaker and
//!    hard cap). Guards are settable but excluded from any *search* — a
//!    loss that can widen a breaker to buy kurtosis will do so; that
//!    exclusion lives in the search configuration, not here, because "you
//!    may not change the model" was never the rule. "A changed model has a
//!    different name" is.
//! 2. **Derived bits** — `mispricing_phi` and `s_phi_tick` are carried as
//!    recorded bits and are never set directly. Overriding
//!    `mispricing_half_life_days` recomputes both via `mathx::pow`,
//!    documented as deterministic-but-not-bit-identical to any recorded
//!    constant. An override equal to the shipped
//!    half-life keeps the recorded bits — sameness of value must mean
//!    sameness of bits.
//! 3. **Carried, read-only**: the rest of the preset surface:
//!    fair-value coefficients, the economy's daily-chain constants, the
//!    book geometry, the sector sigma table, `daily_shock_cap` and
//!    `crisis_vix_threshold`. Visible in the dict and covered by the
//!    fingerprint, but an override is REFUSED by name: these are not yet
//!    threaded through their call sites, and accepting an override the
//!    engine would ignore is exactly the fingerprint lie this type exists
//!    to make impossible. They become settable when their chains are
//!    threaded.
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
///
/// Build one with [`ModelParams::preset`] and change it with
/// [`ModelParams::with_override`], which checks the value and recomputes the
/// fields derived from it. Outside this crate that is the only way, because
/// the struct is `#[non_exhaustive]`: new coefficients arrive in patch
/// releases (0.8.5 added 37), and each would otherwise break a struct
/// literal. Struct update syntax is refused too, since it would copy a
/// derived field such as `breaker_up` without recomputing it:
///
/// ```compile_fail,E0639
/// use tradefloor::params::{ModelParams, PT_V1};
///
/// let wider = ModelParams { quote_model_weight: 0.5, ..PT_V1 };
/// ```
///
/// ```
/// use tradefloor::params::ModelParams;
///
/// let base = ModelParams::preset("pt-v20").unwrap();
/// let wider = base.with_override("quote_model_weight", 0.5).unwrap();
/// assert_ne!(wider, base);
/// ```
#[non_exhaustive]
#[derive(Debug, Clone, PartialEq)]
pub struct ModelParams {
    // ── Factor structure (market/tick.rs, market/factors.rs) ────────────
    /// Baseline daily sigma of the shared market factor. It anchors the
    /// factor's variance process and is the unit the crash amplifier's
    /// threshold is measured in.
    /// pt-v21 ships 0.007099478.
    pub market_factor_sigma: f64,
    /// The power of the roster's cap-weighted beta each name's beta is divided
    /// by when the engine is built: `beta_i / B^d`, with `B = sum(cap_i *
    /// beta_i) / sum(cap_i)` over the public names at their opening caps. 0.0,
    /// which every preset through pt-v20 carries, is the beta the instrument
    /// gave, bit for bit: no sum is taken. 1.0 makes the roster's cap-weighted
    /// beta exactly one, so the market factor is the systematic part of the
    /// roster's OWN index, as a real index's constituents' betas measured
    /// against it average one by definition. Read once, at construction
    /// (`Engine::with_params_from_opening`); every reader of `stock.beta` then
    /// sees the normalised value, and a snapshot carries it. A name added later
    /// (`add_company`) keeps the beta it is given. In [0, 1].
    ///
    /// Why. The certification's tail row (`index_tail_dn3_pct`, sessions
    /// at or below -3 per cent on 40-name random rosters) reads 0.62 to
    /// 0.67 on R17A against [0.64, 2.34], while the long run's certified
    /// roster (seed 111) reads the tape's rate (31.5 sessions under -3 per
    /// cent a decade against 30.1). The two rosters differ in the index's
    /// systematic exposure: roster 111's cap-weighted beta is 1.06 with
    /// technology 33 per cent of its cap, a random roster's median is 0.97
    /// with technology 7 per cent, so the random index's volatility is 15.4
    /// per cent against roster 111's 17.1 (one year from the opening, 360
    /// and 60 held-out seeds) and the tape's 18.1. Normalising the beta
    /// moves the two the opposite ways (15.4 to 15.7, 17.1 to 16.2), and a
    /// market factor raised to restore roster 111 then lifts every roster's
    /// systematic variance alike.
    ///
    /// Measured (sim/r17-tails, on R17A). At 1.0 with `market_factor_sigma`
    /// 10 per cent higher, the tail row reads 0.81 and 0.85 over 360
    /// certification seeds per set (201-400 and 431-590, and the same plus
    /// 20000) against R17A's 0.58 and 0.56, and roster 111's 21-year
    /// histories read an index volatility of 19.5 (R17A 18.8, tape 18.1)
    /// and 8.8 sessions under -5 per cent a decade (7.5, tape 6.2).
    /// pt-v21 ships 1.0.
    pub market_beta_normalise: f64,
    /// How much the sector draw's variance follows VIX, on the same
    /// `(VIX / anchor)^2` target the market factor's variance uses
    /// (`factor_vol.rs`). At 0.0 the sector sigma is static, bit-identical by
    /// branch. At 1.0 it scales fully.
    ///
    /// A static sector sigma is the one variance term on the tick path that
    /// does not scale with stress, so any positive `sector_factor_sigma`
    /// raises calm volatility and leaves crisis volatility alone. Measured
    /// with this coupling at 0.0, raising the sector sigma cuts the crisis
    /// lever by a tenth on pt-v3 and pt-v6 alike (3.07x to 2.78x, 2.68x to
    /// 2.49x). This coupling lets
    /// the model carry sector structure without that cost. 0.0 on presets
    /// before pt-v7, 0.25 on pt-v7 to pt-v10, and 1.0 from pt-v11 on.
    pub sector_vix_coupling: f64,
    /// Daily sigma of each shared sector factor, loaded at `sector_loading`
    /// by every member of the sector (`market/factors.rs`).
    ///
    /// pt-v1 to pt-v6 carry 0.002, about a quarter of one percent of a
    /// name's daily variance: at that value the model has a market factor
    /// and nothing else, and residual correlation after it is diagonal.
    /// pt-v7 onward carry a larger sigma, and pt-v16 to pt-v20 ship
    /// 0.008583053614. On the 0.002 setting `sector_excess_corr`
    /// (same-sector minus cross-sector mean pairwise correlation) reads
    /// 0.004 against a real band of 0.11 to 0.23, fifteen seed-sd out.
    ///
    /// Measured on thirty seeds, pt-v3 base, 252 days: `sector_excess_corr`
    /// is monotone and roughly quadratic in sigma. 0.012 puts it at 0.155,
    /// inside its band, with no other panel statistic leaving its own, and
    /// 0.020 overshoots the band and drops kurtosis through its floor. The
    /// panel does not show two costs. The crisis volatility lever falls from
    /// 3.07x to 2.78x unless `sector_vix_coupling` is on, and kurtosis
    /// thins by about 0.3 seed-sd at 504 days. Above the crisis threshold
    /// the blend in `market/tick.rs` replaces the sector draw with the
    /// market draw, so sector structure reads zero at VIX 45 whatever this
    /// is set to.
    pub sector_factor_sigma: f64,
    /// How hard a name loads on its own sector's factor, as a multiplier on
    /// the sector draw. 0.5 on pt-v1 to pt-v12; pt-v19 and pt-v20 ship 0.6.
    ///
    /// On its own this is one loading shared by every member of every
    /// sector. A name's exposure to the market varies with its beta, and
    /// `sector_loading_beta_slope` gives its exposure to its own industry
    /// the same kind of spread.
    pub sector_loading: f64,
    /// How much a name's sector loading follows its market beta. 0.0 on
    /// presets before pt-v15, which gives every member the same loading;
    /// pt-v16 onward ship 0.7.
    ///
    /// At `s` the loading is `sector_loading * (1 + s * (beta - 1))`, so a
    /// high-beta name loads harder on its industry and a defensive one
    /// loads less. Tied to beta rather than to a fresh draw on purpose: it
    /// reuses a per-name attribute the universe already carries, so it needs
    /// no RNG stream and cannot move the draw schedule.
    ///
    /// This gives sector exposure a cross-sectional spread. `sector_excess_corr`
    /// is a mean over pairs, so a fixed loading makes every same-sector pair
    /// identically exposed, while real industries contain pure plays and
    /// conglomerates.
    pub sector_loading_beta_slope: f64,
    /// How far the crisis blend's market injection is decoupled from the
    /// market factor's own magnitude, from 0.0 (fully coupled) to 1.0 (fully
    /// decoupled). 0.0 on every shipped preset.
    ///
    /// The injection is `source * gain * crisis_spike * market_factor`, and
    /// at a held VIX the spike is pinned at
    /// [`crisis_blend_cap`](Self::crisis_blend_cap), so the only thing that
    /// varies between seed blocks is `market_factor`'s magnitude. That is
    /// the market variance level, which GARCH persistence governs.
    ///
    /// So crisis co-movement's across-block range tracks persistence at rho
    /// +0.85, and tightening the range by the existing dials means lowering
    /// persistence and paying for it in the 504-day panel. A 4x4 grid over
    /// that trade found no cell escaping it.
    ///
    /// At `d` the injection is scaled by `|market_factor / baseline|^-d`, so
    /// at 1.0 its magnitude no longer depends on how large the market factor
    /// happens to be and the crisis correlation it produces stops inheriting
    /// the variance level. The baseline is
    /// [`market_factor_sigma`](Self::market_factor_sigma) at tick scale, the
    /// same normalizer `crash_amplifier` already uses, so "ordinary" means
    /// the same thing in both places.
    ///
    /// Whether it narrows the co-movement spread at an acceptable cost has
    /// not been measured, and no preset turns it on.
    pub crisis_blend_variance_damp: f64,
    /// Gain on the QE valuation channel, where the target P/E takes
    /// `1 + qe_pe_gain * qe_pe_boost`. 1.0 on pt-v1 to pt-v15; pt-v16 onward
    /// ship 0.0, which turns the channel off.
    ///
    /// Every other macro channel has a gain between input and response
    /// (`garch_vix_coupling`, `jump_vix_coupling`, `sector_vix_coupling` and
    /// `market_vol_vix_coupling`), and at 1.0 this one has none.
    ///
    /// Freezing each macro channel of the driven test in turn shows why it
    /// matters. The VIX channel alone produces a ratio of 1.136 against real
    /// AAPL and the full four produce 1.394. `qe_pe_boost` carries about
    /// 0.25 of that 0.39 excess, more than VIX's own 0.14, and the policy
    /// channel contributes nothing. The response is convex, so halving the
    /// amplitude removes 68% of the contribution: a gain near 0.5 would move
    /// the driven ratio from 1.394 to 1.222.
    ///
    /// The `qe_pe_boost` series the driven test supplies is not measured
    /// quantitative easing. `gate_pick._covid_inputs` derives it as the S&P
    /// against its own 200-day EMA, clamped to +/-0.35. A gain calibrated
    /// against that proxy encodes the proxy's amplitude as if it were the
    /// model's physics, so any value fitted to the current driven test
    /// carries that qualification.
    pub qe_pe_gain: f64,
    /// Gain on the QE stock channel: the target P/E takes
    /// `+ qe_pe_stock_gain * ln(qe_assets_ratio)`, concave in the level of
    /// holdings and zero at the neutral baseline. 0.0 on every shipped
    /// preset, where it has no effect.
    ///
    /// The flow channel (`qe_pe_gain`) is linear in
    /// monthly purchases and overshoots when fed the measured Fed series.
    /// This form can take real holdings data.
    pub qe_pe_stock_gain: f64,
    /// Multiplier on every name's idiosyncratic GARCH sigma. 1.0 leaves it
    /// unscaled; shipped presets run 0.51 to 0.84, and pt-v16 to pt-v20 ship
    /// 0.5125981926.
    /// pt-v21 ships 0.52.
    pub idio_sigma_scale: f64,
    /// How strongly a name's idiosyncratic volatility follows its market
    /// beta, as an exponent. 0.0 on every shipped preset, which gives every
    /// name the same scale.
    ///
    /// At `k` the scale is `idio_sigma_scale * beta^k`, bounded by
    /// [`IDIO_BETA_BOUNDS`](crate::market::factors::IDIO_BETA_BOUNDS). Like
    /// [`sector_loading_beta_slope`](Self::sector_loading_beta_slope) it reuses a
    /// per-name attribute the universe already carries rather than drawing a
    /// fresh one, so it costs no RNG stream and cannot move the draw
    /// schedule.
    ///
    /// `idio_sigma_scale` is otherwise the last homogeneous term in a name's
    /// volatility. Cap size varies volatility through `cap_size_multiplier`
    /// and GARCH varies it through each name's own conditional variance, but
    /// the scale itself is one number for every name in the roster. Real
    /// rosters disperse more: over ten non-overlapping 252-day windows of
    /// the forty-name reference panel the interquartile ratio of annualized
    /// name volatility runs 1.273 to 1.486, where pt-v12 averages 1.205
    /// across thirty seeds. That is a dispersion gap with the level right,
    /// and no dial that moves every name together can close it.
    ///
    /// It is an exponent because the linear form `1 + s(beta - 1)` drives
    /// any name with beta below `1 - 1/s` to exactly zero volatility, and on
    /// the certified roster that starts at `s` near 2, before the form
    /// reaches the bottom of the real range. `beta^k` is positive for every
    /// positive beta, so it disperses without deleting names.
    pub idio_sigma_beta_exponent: f64,
    /// Order-flow impact coefficient: the scale of the price move that
    /// injected order flow causes, before `informed_flow_fraction` splits it.
    /// 50.0 on every preset through pt-v20.
    /// pt-v21 ships 800.
    pub order_flow_coefficient: f64,
    /// Which participation law the order-flow impact multiplier follows, a
    /// switch. 0.0, on every preset through pt-v20, is the clamped linear law;
    /// 1.0 is linear up to a knee and a square root above it.
    ///
    /// At 0.0 the multiplier is `max(0.2, min(participation, 10) * 0.15)`.
    /// At 1.0 it is `0.15 * participation` up to the knee at ten and
    /// `1.5 * sqrt(participation / 10)` above it. The zero case is a branch,
    /// so presets at 0.0 reproduce bit for bit.
    ///
    /// Participation is a tick's total order volume over the name's average
    /// minute volume, which is its average daily volume over 390, the
    /// minutes in a session. That is the only rate this dial touches, and
    /// its clock is the trading day. The 365-day economy never enters here.
    ///
    /// The law at 0.0 has three regimes. The floor and the linear term meet
    /// at exactly 4/3, because 0.15 times 4/3 is 0.2, so the multiplier is
    /// the linear law clamped at both ends. Its elasticity in participation
    /// is 0 below 4/3, 1 between 4/3 and 10, and 0 again above 10.
    ///
    /// The measured law is linear below the knee: Cont, Kukanov and
    /// Stoikov (Journal of Financial Econometrics 12(1) 47-88, 2014) find
    /// price change over short intervals linear in order-flow imbalance
    /// with slope inversely proportional to depth, which is this function's
    /// scale exactly. Above it the exponent is one half: Toth, Lemperiere,
    /// Deremble, de Lataillade, Kockelkoren and Bouchaud (Physical Review X
    /// 1, 021006, 2011) give the square root for orders large against
    /// available volume, and Almgren, Thum, Hauptmann and Li (Risk 18(7)
    /// 58-62, 2005) measure 0.6 on the same object. Cont et al. derive the
    /// square root from their own linear model by a scaling argument, so
    /// the two regimes are one law.
    ///
    /// It adds no constant. 0.15 and 10 are the numbers already in the
    /// source, the knee does not move, the two branches agree at 1.5 where
    /// they meet, and the band between 4/3 and 10 is unchanged to the bit.
    /// Only the two clamped tails differ, and neither carries a coefficient
    /// anyone could tune.
    ///
    /// It does not restate the depth exponent. At
    /// `order_flow_depth_law` 0.0, `calculate_live_factors` multiplies this
    /// by `liquidity_factor`, which divides by the same per-minute volume a
    /// second time, so impact falls as depth squared at a fixed share
    /// count where the cited law gives depth. That is issue #182, and
    /// `order_flow_depth_law` is its switch.
    ///
    /// It also changes nothing in a run with no flow in the order-flow
    /// channel: at zero volume the raw imbalance is the literal `0.0`, whose
    /// product with either multiplier is `+0.0`. An untraded run has none.
    /// Flow reaches the channel from `flow_per_tick` and
    /// `tick(order_flow=...)`, and from an agent's fills on presets where
    /// `fill_impact_coefficient` is 0.0, which is every preset before
    /// pt-v20, so there this dial does change what agents pay.
    /// pt-v21 ships 1.0.
    pub order_flow_impact_law: f64,
    /// How many times the order-flow impact divides by the name's depth, a
    /// switch. 0.0, on every preset through pt-v20, divides twice; 1.0 divides
    /// once.
    ///
    /// `order_imbalance` already returns participation, the tick's flow
    /// over the name's average minute volume. At 0.0 the factor then
    /// multiplies by `1 / max(max(avg_volume, 0.005 * shares) / 390, 100)`,
    /// a minute volume a second time. So at equal participation the impact
    /// falls as depth, and at a fixed share count as depth squared: five
    /// times its own minute volume moves a name trading 154 shares a minute
    /// 1,500 times as far as one trading 230,769. Cont, Kukanov and Stoikov
    /// (Journal of Financial Econometrics 12(1) 47-88, 2014) find price
    /// change linear in order-flow imbalance over depth, which is
    /// participation, so at equal participation the move in return terms
    /// does not depend on depth.
    ///
    /// At 1.0 the second division is by a fixed minute volume instead:
    /// 1,000,000 / 390 shares, the daily volume `order_imbalance` already
    /// assumes for a name that reports none. That restates
    /// `order_flow_coefficient` rather than retuning it. A name trading a
    /// million shares a day is charged exactly what 0.0 charges it, and
    /// every other name moves by its own depth over that one. Equal
    /// participation is then equal impact on every name above the 100-share
    /// minute floor, and the `0.005 * shares` depth, which only the second
    /// division read, is no longer used.
    ///
    /// It adds no price term. The cited law is stated in ticks, so in
    /// return terms it carries the tick over the price, while the
    /// square-root and Almgren et al. laws scale a return by the name's
    /// volatility. The two sources disagree there, and this switch takes
    /// neither.
    ///
    /// The zero case is a branch, so presets at 0.0 reproduce bit for bit,
    /// and a value of 0.0 is left out of the model's digest, so it moves no
    /// fingerprint either. With no flow in the order-flow channel the
    /// imbalance is `+0.0` and the result is `+0.0` at either value. Flow
    /// reaches the channel from `flow_per_tick` and `tick(order_flow=...)`,
    /// and from an agent's fills on presets where `fill_impact_coefficient`
    /// is 0.0, which is every preset before pt-v20.
    /// pt-v21 ships 1.0.
    pub order_flow_depth_law: f64,
    /// Share of order-flow impact that is permanent (information), from 0 to
    /// 1. 0.35 on every shipped preset.
    pub informed_flow_fraction: f64,
    /// How fast endogenous inflation reverts toward its 2% target each
    /// month, as a fraction of the gap. 0.55 on every shipped preset, a
    /// half-life under a month.
    ///
    /// The one coefficient sets both how persistent inflation is and how
    /// far it can wander. At 0.55 the endogenous economy reaches neither the
    /// persistence (monthly acf1 0.936 against real CPI's 0.978, FRED
    /// CPIAUCSL 2015-2025) nor the dispersion (sd 1.23 against 2.18; peak
    /// 4.1% against 9.0%) of the real series. Lower is more persistent and
    /// wider. Nothing else in the economy is touched.
    pub inflation_reversion: f64,
    /// The hard ceiling on endogenous inflation, in percent. 6.0 on every
    /// shipped preset.
    ///
    /// Once `inflation_reversion` is loosened
    /// enough to give inflation its real dispersion, the series sits on this
    /// clamp where the real one reached 9.0% in June 2022 (FRED CPIAUCSL).
    /// The floor is `inflation_floor`.
    pub inflation_ceiling: f64,
    /// The hard floor on endogenous inflation, in percent. -1.0 on every
    /// shipped preset.
    ///
    /// With the reversion loosened to the real dispersion the series sits on
    /// this floor at every setting, where real CPI year-on-year bottomed at
    /// -0.2 in 2015-2025 and -2.0 in 2009 (FRED CPIAUCSL).
    pub inflation_floor: f64,
    /// Daily probability that a company generates its own news event. 0.0 on
    /// presets before pt-v11, which turns endogenous news off; pt-v11 onward
    /// ship 0.05.
    ///
    /// At 0.0 the only news in a run is what the caller supplies:
    /// `SessionRequest.news` is a slice the engine never fills, and the only
    /// populated path is `tradefloor.replay`, which feeds a recorded log's
    /// news back in. So in a free simulation `company_news` is exactly zero
    /// (0 nonzero day-cells out of 30240 at every pinned VIX), and
    /// `news_sector_weight`, `news_market_weight` and the two peer weights
    /// move no certified statistic.
    ///
    /// That leaves the jump process as the market's only idiosyncratic
    /// shock, which this file calls the earnings-surprise channel. A jump
    /// lands on one name and reaches no other. Real earnings surprises
    /// transfer: one cloud company's miss moves its peers. Switching this on
    /// gives sector co-movement an endogenous contagion route. Without it,
    /// co-movement is entirely exogenous, a per-tick sector draw plus market
    /// beta, and nothing travels between members.
    pub endogenous_news_intensity: f64,
    /// Standard deviation of an endogenous news event's price impact, in the
    /// units `NewsEvent::price_impact` carries. pt-v11 ships 0.03 and pt-v16
    /// onward ship 0.01751004376.
    pub endogenous_news_sigma: f64,
    /// Weight on sector-wide news, an event tagged with a sector and no
    /// company. It is distinct from peer transfer, which is one named
    /// company's news reaching another. This is news about the industry
    /// itself.
    pub news_sector_weight: f64,
    /// Weight of market-wide news on every name. 0.3 on every shipped
    /// preset.
    pub news_market_weight: f64,
    /// Weight of one company's good news on its sector peers. This is the
    /// information-transfer channel.
    ///
    /// Without it, a company-tagged event moves only the company it names.
    /// The news dispatch is an if/else-if chain whose sector branch requires
    /// `company_id.is_none()`, so an earnings beat at one cloud name reaches
    /// no other cloud name, in either direction, and sector co-movement
    /// arrives only as exogenous shared shocks (a per-tick sector factor
    /// draw and market beta).
    ///
    /// Real markets transfer: a surprise at one name moves its close
    /// competitors, typically at a fraction of the announcer's move (Foster
    /// 1981; Freeman and Tse 1992). The realism panel cannot see this at all,
    /// because `cross_sectional_corr` is unconditional and dominated by the
    /// market factor, while transfer is a conditional, event-time effect.
    ///
    /// 0.0 means no transfer, as on presets before pt-v11; pt-v11 onward
    /// ship 0.05. The branch is skipped entirely at zero instead of adding
    /// `0.0 * impact`, so it is bit-identical when off.
    pub news_peer_weight: f64,
    /// Weight of one company's bad news on its sector peers.
    ///
    /// Separate from [`ModelParams::news_peer_weight`] because the effect is
    /// asymmetric in the literature: negative surprises transfer more
    /// strongly than positive ones. One parameter with a sign flip would
    /// impose symmetry the data does not support, and a search cannot
    /// discover the asymmetry it was never given room to express.
    ///
    /// Applies when the event's price impact is negative. 0.0 is inert.
    pub news_peer_weight_down: f64,
    /// How much harder news transfers to a peer in a crisis. 0.0 on presets
    /// before pt-v11; pt-v11 onward ship 8.0.
    ///
    /// The peer weights are constants, so without this coupling contagion
    /// runs as hard in a quiet July as in March 2020. Measured, that makes
    /// endogenous news unusable: calm-market sector excess is already in
    /// band at +0.166 against a 0.11-to-0.22 ceiling, and constant transfer
    /// pushes it to +0.256 at both horizons in exchange for the crisis
    /// figure that was wanted.
    ///
    /// At `c` the peer weight becomes `base * (1 + c * crisis_spike)`. The
    /// spike is zero below `crisis_vix_threshold`, so a calm market is
    /// untouched at any coupling and only the crisis moves. Real
    /// information contagion works this way: when one bank misses, the
    /// market re-reads every other bank, and it does that harder in a panic.
    pub news_peer_vix_coupling: f64,
    /// How fast the market prices an endogenous news event, as the half-life
    /// in ticks (minutes) of the fast part of its move. 0.0, on every preset
    /// through pt-v18, spreads the move in a straight line over the session;
    /// pt-v19 and pt-v20 ship 0.6.
    ///
    /// # Why
    ///
    /// At 0.0 an event drawn at `open_market` adds `price_impact / 390` on
    /// every tick of its day, so its move lands in a straight line across
    /// the session: 5% of it by tick 30, half by lunch, all of it by the
    /// close. An agent that reads the headline at tick 30 and trades its
    /// direction earns about +120 bp an event, 82% of the time. Real prices
    /// take in firm news in minutes, so that is an edge a bot would learn
    /// here and lose with money.
    ///
    /// # The profile
    ///
    /// Off zero, the share of the move priced after `n` ticks is
    /// `A(n) = (1 - d) * F(n; h) + d * F(n; h_d)`, where `F(n; h) = (1 -
    /// 2^(-n/h)) / (1 - 2^(-390/h))`, `h` is this dial, and `d` and `h_d`
    /// are `news_absorption_drift_share` and
    /// `news_absorption_drift_half_life`. Each part is rescaled to be
    /// complete at the close, so the day's total move is the same at every
    /// setting; only its timing changes. Tick `m` of the session prices
    /// `A(m + 1) - A(m)` of the event, and its sector peers the same share
    /// of their transfer. See `market::factors::news_absorbed_share`.
    ///
    /// Caller-supplied news (`tick(news=...)`, `run_session(news=...)`) is
    /// not reshaped. Off the regular session the profile prices nothing.
    ///
    /// # The shipped values
    ///
    /// Half-life 0.6 ticks, drift share 0.12, drift half-life 42 ticks,
    /// with `news_quote_revision` 1.0. Christensen, Timmermann and Veliyev
    /// ("Warp speed price moves: jumps after earnings announcements",
    /// arXiv 2601.08962, Table 7, liquid US stocks 2008-2020) buy on the
    /// surprise at the first trade after the release: 0.74% by 30 seconds,
    /// 1.05% by one minute, 1.58% by five and 1.80% by 6:30pm, so 58% of
    /// the move is in after one minute and 88% after five. The half-life
    /// puts `A(1)` at 0.60, the 12% after five minutes is the drift share,
    /// and its 42-tick half-life (a 60-minute mean life) lands it over the
    /// next hours, as Patell and Wolfson (1984, JFE) find return serial
    /// correlation disturbed "for several hours" after the bulk of the
    /// reaction is over within five to ten minutes. Kim, Lin and Slovin
    /// (1997, JFQA) find news released before the open priced within the
    /// first five minutes of NYSE trading; Busse and Green (2002, JFE) find
    /// good news priced within a minute and bad news over fifteen. The
    /// 2016-2020 half of the sample gives the same one-minute share (68%)
    /// and nothing after five minutes, so the drift share is the slower of
    /// the two readings. No drift past the close: post-earnings drift in
    /// large caps has been nil since 2006 (Martineau 2022, CFR), and the
    /// engine's momentum already carries about 5% of a news day's move into
    /// the next.
    pub news_absorption_half_life: f64,
    /// The share of an endogenous news event's move that arrives as
    /// post-news drift, after the fast part: `d` in
    /// [`ModelParams::news_absorption_half_life`]'s profile. 0.0, on every
    /// preset through pt-v18, is no drift part; pt-v19 and pt-v20 ship 0.12.
    /// Read only with that dial off zero.
    pub news_absorption_drift_share: f64,
    /// The half-life in ticks of the post-news drift part, `h_d` in
    /// [`ModelParams::news_absorption_half_life`]'s profile. 0.0 lands the
    /// drift share in a straight line over the session; pt-v19 and pt-v20
    /// ship 42.0. Read only with `news_absorption_drift_share` off zero.
    pub news_absorption_drift_half_life: f64,
    /// Whether the market maker re-quotes on public news, a switch. 0.0, on
    /// every preset through pt-v18, quotes the book around the last print;
    /// pt-v19 and pt-v20 ship 1.0.
    ///
    /// At 0.0 a news move in the model price reaches the tape only as fast
    /// as the tick's flow can walk the book. 1.0 quotes it around the last
    /// print moved by the tick's news term (`company_news / 390` as
    /// applied), the way dealers revise quotes on a public announcement
    /// without waiting for a trade. See `market::tick`, the settlement phase.
    pub news_quote_revision: f64,
    /// Where the market maker centers its book, as a weight in [0, 1] on the
    /// model price. 0.0, on every preset through pt-v19, quotes around the
    /// last print; pt-v20 ships 1.0, which quotes around the model price.
    ///
    /// At 0.0 the center is the last print (moved by the tick's news term
    /// when `news_quote_revision` is on), so the print chases the model
    /// price at about a half-spread a tick and the maker's inventory skew
    /// carries it past. On pt-v19 the print sits about 37 bp (sd) off the
    /// model price in every liquidity bucket, and 65-minute returns carry a
    /// lag-one autocorrelation of -0.135 against a Roll spread 5.8x the
    /// quoted one. 1.0 quotes around the tick's model price, the way dealers
    /// revise quotes on every change in the efficient price and not only on
    /// a headline. In between, the center is the geometric blend
    /// `last^(1-w) * model^w`. See `market::tick`, the settlement phase.
    pub quote_model_weight: f64,
    /// Whether the session closes with a cross at the model price, a switch.
    /// 0.0, on every preset through pt-v19, closes on the last minute's
    /// print; pt-v20 ships 1.0.
    ///
    /// At 0.0 the close is whichever side of the book the final tick's flow
    /// hit, and at the close the intraday volume curve is at its peak, so
    /// the flow walks deepest. The closing print then sits 14 bp (sd) off
    /// the model price on a large name of the certified roster and 45 bp on
    /// a small one, and that noise reverts the next day: a daily
    /// Lo-MacKinlay contrarian book earns +13 bp a day on the large names
    /// from it alone, against the certified forty's -1.7 +/- 2.3, whose
    /// closes are auction prices. 1.0 prints the session's final regular
    /// tick at the model price, as a closing auction clears at the efficient
    /// price. The tick's settlement still runs, so it costs the same draws,
    /// and its book fills do not move the maker's inventory. See
    /// `market::tick`, the settlement phase.
    pub closing_auction: f64,
    /// The aggregate earnings cycle's depth: the log level every company's
    /// earnings are pulled toward in a contraction or a trough, beyond what
    /// nominal output alone gives them. 0.0, on every preset through pt-v19,
    /// has no cycle; pt-v20 ships 0.2.
    ///
    /// At 0.0 earnings grow with nominal output and a recession reaches
    /// equities only through rates, credit and fear, so a year's index
    /// return spreads 9 to 12 percent (sd) against the S&P's 17.4, and a
    /// replayed crisis leaves the index a third as far down as the real one.
    /// S&P reported earnings fell 29, 54, 92 and 33 percent around the 1990,
    /// 2001, 2008 and 2020 recessions (Shiller's series; operating earnings
    /// about 40 in 2008), and their twelve-month log growth has an sd of
    /// 0.18 over 1950-2023 with the 2008 write-down capped.
    pub earnings_cycle_depth: f64,
    /// The earnings cycle's upside share: every phase other than a
    /// contraction or a trough pulls earnings toward `+depth * upside`,
    /// which centers the level over a cycle so the cycle moves earnings
    /// around the nominal-output path without shifting it. pt-v20 ships
    /// 0.09. Read only with `earnings_cycle_depth` non-zero.
    pub earnings_cycle_upside: f64,
    /// Half-life in sessions of the pull toward the phase's level, 60 on every
    /// preset through pt-v20. Read only with `earnings_cycle_depth` non-zero.
    /// pt-v21 ships 150.
    pub earnings_cycle_half_life: f64,
    /// The daily sd of the earnings level's own noise; 0.0 is the phase path
    /// alone and takes no draw. Read only with `earnings_cycle_depth`
    /// non-zero.
    pub earnings_cycle_sigma: f64,
    /// Half-life, in sessions, of the discount the valuation puts on the
    /// earnings cycle's expected path. 0.0, on every preset through pt-v19,
    /// prices today's level alone; pt-v20 ships 126.
    ///
    /// At 0.0 a recession reaches prices only as fast as earnings fall and a
    /// recovery only as they recover, so the index cannot fall at the turn
    /// and look through the dip, as the S&P 500 did in 2020 (trough 23
    /// March, earnings' trough the quarter to June). Off zero, fair value
    /// reads the level averaged over the expected path, `A = c e + g_phase`,
    /// from the cycle's own hazards and the level's own pull
    /// (`Engine::earnings_anticipation_terms`): a turn of phase moves it at
    /// once, and a trough reads above a contraction because a recovery is
    /// near. Read only with `earnings_cycle_depth` non-zero.
    pub earnings_anticipation_half_life: f64,
    /// P/E compression per unit of discount rate above neutral, times a
    /// name's growth duration: the target multiple's rate adjustment is
    /// `1 - (yield - neutral) * rate_pe_sensitivity * duration`. 1.5 on
    /// every preset through pt-v19; pt-v20 ships 3.0.
    ///
    /// At 1.5 fair value moves about 2 percent per 100 bp of the corporate
    /// yield at the median duration. The S&P 500's trailing P/E fell 4.9 to
    /// 5.5 percent per 100 bp of Baa over 2022.
    pub rate_pe_sensitivity: f64,
    /// Sessions between a turn of the business cycle and its publication.
    /// 0.0, on every preset through pt-v19, publishes the phase the economy
    /// is in; pt-v20 sets 252.0.
    ///
    /// Off zero, every route that reports the phase (`macro_fields`,
    /// `macro_state`, and what reads them: a World's trace rows, a hosted
    /// market log's cycle events) reports the phase of this many sessions
    /// before, as the NBER dates a recession about a year after it began,
    /// and a turn is announced when it is published. The true phase stays
    /// internal: the earnings cycle, the anticipated earnings path, the
    /// hazards and the stress read it, and a scenario that sets the phase
    /// sets the true one at once. Before this many sessions have closed
    /// the opening phase is published. A whole number of sessions; the
    /// engine keeps the last `lag + 1` phases
    /// (`Engine::published_cycle_phase`), and its snapshot and state hash
    /// carry them only while this is set.
    pub cycle_publication_lag: f64,
    /// Draws each turn's publication lag instead of using the fixed
    /// `cycle_publication_lag`. 0.0, which every preset through pt-v20 carries,
    /// is the fixed lag, under which the published phase is the true phase
    /// shifted by exactly that many sessions: a clock, since a recession's
    /// length has an sd of 2.2 months on the US table, so a peak published 252
    /// sessions late lands a median 1.9 months before the recovery.
    ///
    /// At 1.0 each true turn `k`, at close `tau_k`, draws its own lag `L_k`,
    /// uniform in whole sessions: [84, 252] for a turn into a peak or a
    /// contraction (the NBER announced peaks 4 to 12 months after them,
    /// 1980-2020) and [168, 441] for a turn into a trough, a recovery or an
    /// expansion (troughs 8 to 21 months). It is published at the close
    /// `pi_k = max(pi_(k-1), tau_k + L_k)`, so announcements stay in order,
    /// and the published phase is that of the latest turn with `pi_k` at or
    /// before the close. The draw is stateless and takes nothing from any
    /// stream: `u_k = splitmix64_mix(key ^ splitmix64_mix(PUBLICATION_TAG <<
    /// 32 | k))`, the key derived from the root seed
    /// (`rng::publication_key`), so every other draw is untouched. Turns are
    /// counted from the opening. `cycle_publication_lag` is not read while
    /// this is set. The snapshot and the state hash carry the schedule
    /// (`Engine::cycle_publication`) only while this is set. A switch.
    /// pt-v21 ships 1.0.
    pub cycle_publication_lag_draw: f64,
    /// Sessions between the end of a quarter and the publication of its GDP
    /// growth, as the BEA's advance estimate comes about a month after the
    /// quarter. 0.0, on every preset through pt-v19, reports `gdp_growth`
    /// daily as the economy runs it; pt-v20 sets 21.0.
    ///
    /// Off zero, every route that reports growth
    /// (`macro_fields["gdp_growth"]` and the recorded `macro_table()`, and
    /// what reads them: a dataset export's `macro.arrow`, an explanation's
    /// state) reports a quarterly figure, the mean of the true daily growth
    /// over the macro calendar's quarter (`MacroCalendar::days_per_quarter`,
    /// 63 sessions on the 252-session calendar, 90 on the shipped one; day
    /// 0, the opening, is the first day of quarter 0), released on the close
    /// this many sessions after the quarter's last day. Before the first
    /// release the opening growth is published. The true daily growth stays
    /// internal: output, earnings, unemployment, the cycle's hazards and the
    /// central bank read it, a pin sets it at once, and `state_snapshot()`
    /// carries it. A whole number of sessions; lag 0 with quarterly
    /// averaging is not offered, since no agency publishes on the quarter's
    /// last day (`Engine::published_gdp_growth`). The snapshot and the state
    /// hash carry its state only while this is set.
    pub gdp_publication_lag: f64,
    /// The half-life, in sessions, of unemployment's response to its
    /// cyclical drivers. 0.0, on every preset through pt-v19, moves it in
    /// one step at each monthly release; pt-v20 sets 84.0.
    ///
    /// At 0.0 the rate moves at each monthly release by the whole of what
    /// the phase's trend and Okun's law on the day's growth ask for, so the
    /// first release after a contraction begins carries a rise of about
    /// 1.2 pp (seeds 201-212), four times the spread of a release otherwise,
    /// and announces the turn. At 84 sessions it is 0.16 pp. Off zero, the
    /// monthly change is an impulse partially adjusted toward that drive,
    /// closing `1 - 0.5^(month / half_life)` of the gap at each release
    /// (`EconomyState::unemployment_impulse`), so the rise builds over
    /// months: UNRATE went from 4.3 to 5.5 over the 2001 recession and from
    /// 5.0 to 9.5 over December 2007 to June 2009, a first month of 0.1 to
    /// 0.3 pp each time. The NAIRU pull and the noise act as before. It
    /// moves the true unemployment rate and so everything that reads it
    /// (inflation, confidence, the bank, the cycle's hazards). The snapshot
    /// and the state hash carry the impulse only while this is set.
    pub unemployment_adjustment_half_life: f64,
    /// The share of unemployment's gap to the natural rate closed at each
    /// monthly release, a dial. 0.0, on every shipped preset, keeps the
    /// release's own constant 0.06 and is a branch, so every preset reproduces bit for bit
    /// and a value of 0.0 is left out of the model's digest.
    ///
    /// # Why the shipped pull holds nothing
    ///
    /// The release adds the phase's trend, Okun's law on the day's growth
    /// and a recovery term, and pulls toward the natural rate at 0.06 of
    /// the gap. On pt-v20, seeds 101-108 over 5,292 sessions, those drivers
    /// average -0.26 points a month (-0.46 in an expansion, where the
    /// economy spends 72 per cent of its months), against a pull of 0.06
    /// times a gap of at most 2 points. So the rate runs to its 2.5 floor
    /// and stays: on pt-v20, seeds 101-108 over 1,008 sessions, it sits at
    /// the floor on 78 per cent of days and ends there on 5 of 8 seeds
    /// (issue #172). FRED UNRATE has never been below 2.5 (1948 to 2026:
    /// mean 5.65, 10th to 90th percentile 3.7 to 7.8) and spent 2 per cent
    /// of months at or under 3.0, all of them in 1951 to 1953.
    ///
    /// # The natural rate
    ///
    /// The pull acts toward `EconomyState::structural_unemployment`, the
    /// rate the Phillips curve and wage growth already read as the NAIRU:
    /// 4.0 plus 0.3 times long-term unemployment, so 4.15 after a long
    /// expansion and up to about 4.6 after a deep recession. The CBO's
    /// natural rate (FRED NROU) averaged 4.50 over 2015 to 2026 (range 4.40
    /// to 4.75) and 4.80 over 2000 to 2026. Anchoring to the rate the
    /// Phillips curve reads, and not to a second constant, means a held
    /// unemployment rate stops pushing inflation one way. UNRATE's mean of
    /// 5.65 sits above the NAIRU because recessions raise it quickly and
    /// expansions bring it down slowly, which the cycle supplies.
    ///
    /// # The dial
    ///
    /// The release's pull is `k * (natural - unemployment)` a month in
    /// place of `0.06 * (natural - unemployment)`. A month's share k is a
    /// half-life of `ln 2 / -ln(1 - k)` months: 0.03 is 23 months, 0.05 is
    /// 13.5, 0.10 is 6.6. Alone it cannot hold the rate near the natural
    /// rate without losing its persistence, because the drivers' -0.26 a
    /// month set the gap at about `-0.26 / k`. Measured on pt-v20, seeds
    /// 101-108 over 2,520 sessions: at 0.3 alone the rate still sits at the
    /// floor on 8 per cent of days, with a 12-month autocorrelation of
    /// 0.22. With `unemployment_okun_coefficient` at 0.5 the drivers average
    /// about -0.02 a month, and the constant 0.06 already keeps the rate off
    /// the floor (mean 3.79, 0.37 under the natural rate); 0.10 takes the
    /// mean to 3.94 and 0.03 lets the floor back on 6 per cent of days. It
    /// takes no draw.
    pub unemployment_natural_pull: f64,
    /// Okun's law at the monthly release, as the annual coefficient it states:
    /// points of unemployment a year per point of growth below 2 per cent. 0.0,
    /// on every preset through pt-v20, is the shipped term and is a branch, so
    /// every preset reproduces bit for bit and a value of 0.0 is left out of
    /// the model's digest.
    ///
    /// # The shipped term
    ///
    /// At 0.0 the release adds `(2 - growth) * 0.20` and, in an expansion
    /// or recovery above 1 per cent growth, `-growth * 0.08`, every month.
    /// The comment above it says 1 point of growth below trend is about
    /// 0.5 points of unemployment, which is Okun's law as an annual
    /// relation. Applied at every monthly release it is 2.4 points a year.
    /// The recovery term counts the same growth a second time: at 3 per
    /// cent growth it is -0.24 a month, six times the Okun term at a
    /// coefficient of 0.5. Together they take an expansion's unemployment
    /// down 5.5 points a year; UNRATE fell 0.6 a year over 2010 to 2019 and
    /// 0.5 a year over 1992 to 2000.
    ///
    /// # Off zero
    ///
    /// The release adds `(2 - growth) * beta / 12` a month and no recovery
    /// term, in the release and in the impulse
    /// `unemployment_adjustment_half_life` adjusts. The phase's trend, the
    /// natural-rate pull and the noise act as before. Okun (1962) put the
    /// coefficient near 1/3; Ball, Leigh and Loungani (Journal of Money,
    /// Credit and Banking 49(7), 2017) estimate about 0.4 to 0.5 for the
    /// US, stable since 1948. 0.5 is the value the shipped comment states.
    /// A contraction at the model's mean growth of -2.6 per cent then adds
    /// about 0.28 a month with the phase's trend, a rise near 2 points over
    /// a nine-month recession; UNRATE rose 1.6 to 2.4 points in 1990-91 and
    /// 2001 and 5.0 in 2007-09. Measured on pt-v20, seeds 101-108 over
    /// 2,520 sessions, the largest rise in the 18 months after a
    /// contraction begins goes from 4.5 points at 0.0 to 1.8 at 0.5 and 3.1
    /// at 1.0 with `unemployment_natural_rate` 5.0.
    ///
    /// The central bank's recession cuts read unemployment's LEVEL (above
    /// 7, 8 and 10 per cent) and its Taylor rule a fixed 4.0 target. At 0.5
    /// on the shipped natural rate a recession no longer reaches 7, so the
    /// policy rate changes 0.6 times a year where it changed 1.9 times, and
    /// cuts 0.4 points in the year after a contraction begins where it cut
    /// 1.3. At 1.0 with a natural rate of 5.0 it changes 1.7 times a year
    /// and cuts 1.2. It takes no draw.
    /// pt-v21 ships 0.75.
    pub unemployment_okun_coefficient: f64,
    /// The natural rate of unemployment with no long-term unemployment,
    /// percent, a dial. 0.0, on every shipped preset, is the release's own
    /// constant 4.0 and is a branch, so every preset reproduces bit for bit and a value
    /// of 0.0 is left out of the model's digest.
    ///
    /// The monthly release sets `EconomyState::structural_unemployment` to
    /// this plus 0.3 times long-term unemployment, the NAIRU the Phillips
    /// curve, wage growth and the natural-rate pull read. Moving it moves
    /// unemployment and its NAIRU together, so the Phillips gap is
    /// unchanged and what moves is everything that reads the level:
    /// consumer and business confidence, housing volume above 5 per cent,
    /// participation in a contraction and discretionary fiscal stimulus
    /// above 7 per cent.
    ///
    /// The constant 4.0 gives a NAIRU of 4.15 to about 4.6, which matches
    /// the CBO's natural rate (FRED NROU) over 2015 to 2026, 4.40 to 4.75.
    /// Over 1990 to 2026 NROU averaged 4.97 and over 1949 to 2026 5.40,
    /// and UNRATE averaged 5.65 over both 1948 to 2026 and 1990 to 2026,
    /// with a 120-month window's mean between 4.62 and 7.12 (10th to 90th
    /// percentile, 1948 to 2026). A screen that wants the long history and
    /// not the last decade would set about 4.5 to 5.0. The bank's Taylor
    /// rule keeps its own 4.0 target, so a higher natural rate is also a
    /// standing dovish gap: at 5.0 with `unemployment_okun_coefficient` 1.0
    /// the policy rate averages 0.58 points lower on pt-v20 (seeds 101-108,
    /// 2,520 sessions). It takes no draw.
    pub unemployment_natural_rate: f64,
    /// The daily share of oil inventory's gap to its normal level, 50,
    /// closed by production and storage, a dial. 0.0, on every preset through
    /// pt-v20, is a branch, so every preset reproduces bit for bit and a
    /// value of 0.0 is left out of the model's digest.
    ///
    /// # The defect at 0.0
    ///
    /// Inventory is a pure integrator of demand against supply plus noise
    /// (driftless at `oil_supply_response` 1.0), and outside its 40 to 60
    /// dead zone it pushes the oil price by `0.08` a day per unit, up to
    /// plus or minus 3.2 a day at its bounds. Oil's own reversion is 0.03 a
    /// day toward about 81, so a saturated push rests oil at about 190 or
    /// -25, outside both clamps (issue #170). Nothing returns inventory to
    /// the dead zone, so a random walk that wanders far enough pins oil at
    /// a clamp for the rest of the run. On pt-v20, seeds 101-108 over 1,008
    /// sessions, one seed's inventory reached 100 and its oil sat at the 35
    /// floor on 44 per cent of days.
    ///
    /// # Off zero
    ///
    /// Inventory moves by `k * (50 - inventory)` a day beside demand,
    /// supply and noise, so it is an Ornstein-Uhlenbeck process around the
    /// middle of the dead zone, with a standard deviation of
    /// `0.5 / sqrt(2k)` units at `oil_supply_response` 1.0. That gives the
    /// oil price a stable interior: the push is bounded in distribution
    /// and oil's own reversion holds the level. In the theory of storage
    /// (Working, American Economic Review 39(6), 1949; Brennan, AER 48(1),
    /// 1958) stocks above normal depress the spot price and draw down as
    /// carry turns costly, and stocks below normal raise it until
    /// production and imports refill them, so inventory reverts to a
    /// normal level; Pindyck (Journal of Political Economy 102(2), 1994)
    /// estimates that adjustment for crude and products. The coefficient is
    /// not identified by those papers and is a dial: at 0.002 (a half-life
    /// of 347 sessions) inventory's spread is 7.9 units, and oil's
    /// multi-year swings come from inventory's slow excursions out of the
    /// dead zone, as the long-run factor of Schwartz and Smith (Management
    /// Science 46(7), 2000) does. With `oil_supply_response` at 0.0
    /// inventory's mean sits at `50 - 0.15 * growth / k`, so the dial is
    /// meant beside a supply response of 1.0. It takes no draw.
    /// pt-v21 ships 0.002.
    pub oil_inventory_reversion: f64,
    /// Inflation's monthly response to the oil price, the same either side of
    /// oil's anchor, as a multiple of the 0.01 a dollar the release pays
    /// above 80.
    /// 0.0, on every preset through pt-v20, is the
    /// shipped three-way branch and is a branch, so every preset
    /// reproduces bit for bit and a value of 0.0 is left out of the
    /// model's digest.
    ///
    /// # The asymmetry at 0.0
    ///
    /// The release adds `(oil - 80) * 0.01` above 80, `(oil - 50) * 0.005`
    /// below 50 and nothing between. Oil opens at 75 and reverts toward
    /// about 81, so a rise of 10 pays at 0.01 and the matching fall of 10
    /// pays nothing: for any oil path symmetric about its anchor the term
    /// has a positive mean, and oil raises inflation while it is itself
    /// driftless (issue #171). Measured on pt-v20, seeds 101-108 over 1,008
    /// sessions, the slope of the monthly inflation change on oil's
    /// distance above 75 is 0.0008 and below 75 -0.0001.
    ///
    /// # Off zero
    ///
    /// The release adds `0.01 * c * (oil - 81)`, where 81 is the level oil's own
    /// reversion target takes at the 2 per cent trend growth Okun's law
    /// pivots on (`OIL_BASELINE + 3 * 2`), so oil at its anchor adds
    /// nothing and a rise and a fall of equal size add equal and opposite
    /// amounts. Kilian and Vigfusson (Quantitative Economics 2(3), 2011)
    /// find no evidence that the US economy responds asymmetrically to oil
    /// price increases and decreases once the response is estimated
    /// symmetrically in the shock. At 1.0, the shipped coefficient above
    /// 80 on both sides, a sustained 10 per cent rise from 81 adds 0.08 a
    /// month, which the inflation reversion of 0.55 holds at about 0.15
    /// points of inflation; energy is about 7 per cent of the CPI basket (BLS
    /// relative importance), and motor fuel, about half of it, moves
    /// roughly half as much as crude, a direct effect near 0.2 points. It
    /// takes no draw.
    /// pt-v21 ships 1.0.
    pub oil_inflation_passthrough: f64,
    /// Whether the engine keeps a price index of its roster. 0.0, on every
    /// shipped preset, keeps none: no level, no state, nothing in the
    /// snapshot or the state hash, and a value of 0.0 is left out of the
    /// model's digest.
    ///
    /// Off zero the index is `sum(price * shares_outstanding) / divisor` over
    /// the public, solvent names, through
    /// [`crate::market::calculate_market_index`]. The divisor is set at the
    /// first session's open, so the index opens that session at 1000. A
    /// listing, a delisting, a bankruptcy or a name taken private resets the
    /// divisor when it happens, at the prices then standing, so the event
    /// changes the constituents and leaves the level where it was.
    /// [`crate::engine::Engine::index_level`] reads the level on the prices as
    /// they stand and the level at the last close. The index reads prices
    /// and writes nothing any price reads, so every price is the one the
    /// engine prints without it. It takes no draw. A switch.
    pub index_level_listed: f64,
    /// Whether the VIX is published live within the session. 0.0, on every
    /// shipped preset, publishes it at the close only: on pt-v21 the
    /// published VIX holds at one value through every step of a session and
    /// moves only at the close. A value of 0.0 is left out of the model's
    /// digest.
    ///
    /// Off zero, while the market is open, the live VIX is the projection of
    /// the VIX tonight's close will publish, given the session so far: the
    /// close's own daily step (`economy::daily::vix_and_yields`) with its
    /// draws at their means, on the session's return so far, the market
    /// factor's day so far and the names' variances as the close would
    /// leave them, with the rest of the session's move integrated by the
    /// eight-point rule `rate_intraday_live` uses, and the published
    /// stress premium projected with it. It refreshes at the open and on
    /// every fifth session minute (`RATE_LIVE_REFRESH_MINUTES`). Outside a
    /// session it is the published VIX, so at the close it becomes the VIX
    /// the close published. It writes nothing back: the VIX the model's
    /// laws read is the close's, and every price is the one the engine
    /// prints without it. [`crate::engine::Engine::live_vix`] reads it. The
    /// projection is carried in the snapshot and the state hash while this
    /// is set and a session holds one. It takes no draw. A switch.
    pub vix_intraday_live: f64,
    /// How many sessions ahead the engine's forecast runs, computed at each
    /// close. 0.0, on every shipped preset, computes none, and a value of
    /// 0.0 is left out of the model's digest.
    ///
    /// Off zero, each close (`Engine::advance_macro_day`) computes the
    /// expected published VIX, the index's and each name's one-session
    /// variance, the policy rate and the oil price at horizons 1 to this
    /// many sessions ([`crate::derivatives::Forecast`]), from public state
    /// only: the VIX, the curve, prices, the conditional variances, the
    /// market's cycle nowcast (or the published phase) and the published
    /// growth. It does not read the true cycle phase, the slow VIX level or
    /// any draw to come. The VIX and the variances iterate the expected
    /// value of the model's own laws; the policy rate takes the shadow of
    /// the next meeting and a projection for the meetings after it. The
    /// forecast is carried in the snapshot and the state hash while this is
    /// set. It writes nothing back and takes no draw, so every price is the
    /// one the engine prints without it. A whole number of sessions in
    /// [0, 252].
    pub forecast_horizon_sessions: f64,
    /// The forecast's measure of how far the VIX can be from its expectation:
    /// the sd of the log VIX about the forecast at a long horizon, measured
    /// on the model's own held-out histories. Read only with
    /// `forecast_horizon_sessions` set. 0.0, on every shipped preset, is a
    /// branch: the forecast's variances iterate the laws on the expected VIX
    /// alone, and a value of 0.0 is left out of the model's digest.
    ///
    /// The market factor's variance target rises with the VIX at a power of
    /// 4 above the anchor (2.5 below it on pt-v21), so the expected target
    /// at a horizon is higher than the target at the expected VIX. On
    /// pt-v21, 16 held-out seeds over 800 sessions, the forecast's factor
    /// variance on the expected VIX alone read low by 4, 13, 24, 29 and 37
    /// per cent at 5, 21, 63, 126 and 252 sessions. Off zero the forecast
    /// takes each convex coupling (the factor's target, the per-name GARCH
    /// coupling, the sector sigma, the jump rate, the dollar's safe-haven
    /// drift that oil reads) as its expectation over a lognormal VIX with
    /// this sd at long horizons. The VIX's own path is not changed.
    pub forecast_vix_dispersion: f64,
    /// The half-life, in sessions, of the gap between the log VIX's spread
    /// about the forecast and its long-horizon level
    /// (`forecast_vix_dispersion`): its variance at `h` sessions is
    /// `sd^2 * (1 - 0.5^(2h / half-life))`. Read only with that dial set;
    /// 0.0, on every shipped preset, reaches the long-horizon spread from
    /// the first session, and a value of 0.0 is left out of the model's
    /// digest.
    pub forecast_vix_dispersion_half_life: f64,
    /// The share of the next meeting's shadow change the forecast's policy
    /// path leaves out. The shadow is the change the meeting function makes
    /// on the economy as published, the change `policy_anticipation` prices.
    /// Read only with `forecast_horizon_sessions` set; 0.0, on every shipped
    /// preset, takes the shadow whole, and a value of 0.0 is left out of the
    /// model's digest. On pt-v21, 16 held-out seeds over 1,000 sessions, the
    /// change realised by the next meeting was 0.76 of the shadow; the fit of
    /// all four policy dials together on 40 held-out histories
    /// (`tools/calibration/forecast_dials.py derive`) puts this at 0.31.
    pub forecast_policy_shadow_discount: f64,
    /// The forecast's policy path's momentum: the share of a meeting's
    /// expected change expected again at the meeting after it. Read only
    /// with `forecast_horizon_sessions` set; 0.0, on every shipped preset, is
    /// a branch to no momentum, and a value of 0.0 is left out of the
    /// model's digest. A projection fitted on the model's own held-out
    /// histories, since the meeting ladder is discrete.
    pub forecast_policy_persistence: f64,
    /// The share of the gap to `forecast_policy_neutral` the forecast's
    /// policy path expects each meeting to close. Read only with
    /// `forecast_horizon_sessions` set; 0.0, on every shipped preset, is a
    /// branch to no reversion, and a value of 0.0 is left out of the model's
    /// digest. On pt-v21, 40 held-out histories of 2,000 sessions, the fit
    /// closes 0.0475 of the gap a meeting toward 1.4 per cent. The ladder
    /// hikes in small steps and cuts in large ones, so the errors are skewed
    /// and the fit needs many histories: 16 put the neutral rate at 1.3.
    pub forecast_policy_reversion: f64,
    /// The policy rate the forecast's path reverts toward, per cent. Read
    /// only with `forecast_policy_reversion` set; at its default of 2.5, the
    /// neutral rate the curve's damping reads (`TREASURY_NEUTRAL_RATE`), it
    /// is left out of the model's digest.
    pub forecast_policy_neutral: f64,
    /// Switch that makes the fear/greed index read the business cycle and
    /// GDP growth as published instead of as they are. 0.0, on every preset
    /// through pt-v19, is off; pt-v20 sets 1.0.
    ///
    /// The index's target reads the business-cycle phase (a bonus of +15 in
    /// an expansion to -25 in a contraction) and GDP growth. Off, it reads
    /// the values the economy runs at, so it falls about 35 points in the
    /// five sessions after a contraction begins and announces the turn. On,
    /// it reads them as published (`cycle_publication_lag`,
    /// `gdp_publication_lag`), so it steps when the turn is published, which
    /// is public already. It moves the index itself, and through it consumer
    /// confidence, housing, copper and gold; nothing a price, the bank, the
    /// cycle or a draw reads. With both lags at 0 it has no effect. No
    /// state.
    pub fear_greed_published_inputs: f64,
    /// Whether a name's price takes the change the close's macro step makes
    /// to its fair value at the moment the step is published, a switch. 0.0,
    /// on every preset through pt-v19, leaves it to the next session's first
    /// tick; pt-v20 sets 1.0.
    ///
    /// At 0.0 the central bank meets at the close, the new policy rate,
    /// corporate yield and cycle are readable from then on, and the price
    /// stays at the day's last print until the first tick of the next
    /// session re-values the name, so an agent that reads the decision fills
    /// at the price from before it. On pt-v20 with this switch off (and
    /// anticipation 126, rate sensitivity 3, buyback share 0.75) the index's
    /// first 65 minutes after a published hike fell 76 bp (se 5) from that
    /// price and after a cut rose 171 (se 36), and the published corporate
    /// yield's overnight change correlated -0.32 with the next session's
    /// return. A real FOMC statement is priced within minutes: event studies
    /// read the S&P 500's whole response inside a 30-minute window around it
    /// (Gurkaynak, Sack and Swanson 2005; Bernanke and Kuttner 2005).
    ///
    /// 1.0 re-marks every name that has traded as the step ends, to the
    /// price its premium over fair value implies on the published state, so
    /// its mispricing `s` is unchanged and the next tick starts from the
    /// model price the published state implies. A `pin_macro` re-marks the
    /// same way. The move sits between the day's last print and the next
    /// open, where a decision announced after the close lands. No draw. See
    /// `Engine::reprice_to_published_macro`.
    pub macro_publication_repricing: f64,
    /// The 10-year Treasury yield's daily noise, in percentage points. 0.03
    /// on every preset through pt-v19; pt-v20 ships 0.038.
    ///
    /// At 0.03, with the pull toward the policy rate, it gives a daily change
    /// of about 3.1 bp against the tape's 5.4 (FRED DGS10, 2015-2025).
    pub treasury_10y_noise: f64,
    /// The 2-year Treasury yield's own daily noise, in percentage points.
    /// 0.0, on every preset through pt-v19, makes the 2-year a fixed blend
    /// of the policy rate and the 10-year; pt-v20 ships 0.022.
    ///
    /// At 0.0 the 2-year is the formula `0.85 policy rate + 0.15 10-year`,
    /// which between meetings moves by 0.15 of the 10-year's noise: 0.46 bp
    /// a session against the tape's 5.2 (FRED DGS2, 2015-2025). Off zero the
    /// 2-year is its own process, pulled toward the formula at the 10-year's
    /// rate (0.05 a session), with this noise; one more normal on the
    /// economy stream, taken only under the dial.
    /// pt-v21 ships 0.008.
    pub treasury_2y_noise: f64,
    /// The flight to quality's size: percentage points of 10-year yield per
    /// percent of index return, down with the market when inflation is
    /// under 3 percent and up when it is over 4. 0.02 on every preset
    /// through pt-v19; pt-v20 ships 0.008.
    /// pt-v21 ships 0.013.
    pub flight_to_quality_gain: f64,
    /// Which return the flight to quality reads, a switch. 0.0, on every
    /// preset through pt-v19, reads the previous session's closing minute;
    /// pt-v20 ships 1.0, which reads this session's index return.
    ///
    /// At 0.0 the rule reads the previous session's closing-minute return
    /// behind a 0.5 percent gate, which that return never crosses, so the
    /// rule never fires and the curve carries no stock-bond correlation
    /// (tape: -0.16 for Treasuries, +0.27 for IG corporates, 2015-2025). 1.0
    /// reads this session's index return, with no gate.
    pub flight_to_quality_day: f64,
    /// Whether the corporate yield moves between central-bank meetings, a
    /// switch. 0.0, on every preset through pt-v19, writes it only at a
    /// meeting; pt-v20 ships 1.0.
    ///
    /// At 0.0 fair value's discount rate and an IG bond priced off it sit
    /// still for six weeks at a time. 1.0 moves the yield every session by
    /// the 10-year's move plus the meeting formula's VIX slope on the
    /// session's VIX change; the next meeting re-anchors the level.
    pub corporate_yield_daily: f64,
    /// THE MARKET'S CYCLE NOWCAST. 0.0, which every preset through pt-v20
    /// carries, prices the TRUE phase: the earnings anticipation reads `g` of
    /// the phase the economy is in and the corporate spread its multiplier, so
    /// every true turn moves `A` by `g_next - g` at the close it happens on
    /// (about -4.5, -3.3, +4.1 and +4.0 per cent on pt-v20, into peak,
    /// contraction, trough and recovery) and the first meeting after it
    /// re-anchors the spread by `base * dm` (111 bp on average, up to 304),
    /// while the daily VIX term's ratio to 2 bp a point returns the true
    /// multiplier on any unclamped session: a price-only agent reads the hidden
    /// phase off one close (r13 audit).
    ///
    /// Off zero the market holds a belief `pi` over the five phases, in
    /// `phase_cycle` order, a forward filter on the engine's own chain
    /// (exponential sojourns at the mean sojourns
    /// `earnings_anticipation_terms` already uses, `lambda_j = 1 /
    /// mean_sojourn_j`). At each close one report names a phase: the phase
    /// the session ran in with this probability, otherwise one of the other
    /// four uniformly, from one uniform on `stream::CYCLE_NOWCAST`. The
    /// filter predicts (`pi'_j = pi_j (1 - lambda_j) + pi_(j-1)
    /// lambda_(j-1)`), multiplies by `q` on the reported phase and by
    /// `(1 - q) / 4` elsewhere, and normalises, so no session moves the
    /// belief's log-odds by more than ln(4q / (1 - q)). The anticipation
    /// reads `pi . g + c e` in place of `g` of the true phase, and the
    /// corporate spread prices `pi . m` (see `corporate_spread_cycle`).
    ///
    /// The belief opens one-hot on the opening phase (and on a restore from
    /// a snapshot that carries none), and a pinned phase is public news: the
    /// pin puts it one-hot on the pinned phase and that session's close takes
    /// no report, so a driven path that pins the phase prices exactly what it
    /// priced at 0.0. The belief and its generator are carried in the
    /// snapshot (`economy.cycle_nowcast`, `cycle_nowcast_rng`) and the state
    /// hash only while the dial is set. Proposed for pt-v20 at 0.4 (the
    /// thirteenth registration): the market is 50 per cent sure of a new
    /// phase about 10 sessions after it starts and 90 per cent after about
    /// 19. In {0} and (0.2, 1]; 0.2 would be a report that carries nothing.
    /// pt-v21 ships 0.4.
    pub cycle_nowcast_accuracy: f64,
    /// The share of the corporate spread's cycle multiplier replaced by its
    /// occupancy-weighted mean over the phases (about 1.22 on the US table):
    /// the market prices `(1 - s) m + s m_bar`, where `m` is the belief's `pi .
    /// m` under `cycle_nowcast_accuracy` and the true phase's multiplier
    /// without it. 0.0, which every preset through pt-v20 carries, is the
    /// multiplier as it stood; 1.0 leaves the spread's widening to the VIX
    /// alone.
    ///
    /// While either this or `cycle_nowcast_accuracy` is set, the meeting
    /// re-anchors to the priced multiplier, and on a preset with
    /// `corporate_yield_daily` the daily move carries the meeting formula's
    /// whole change `S(VIX', m') - S(VIX, m)` (the 50 bp daily cap and the floor
    /// still apply), so the level is on the formula every session and a
    /// meeting has nothing to re-anchor. With the nowcast at 0.4, 0.0 here
    /// leaves the spread's daily sd at 7.7 bp against FRED BAA10Y's 3.1
    /// (belief moves times the full multiplier); 0.75 gives 3.8. Proposed
    /// for pt-v20 at 0.75. In [0, 1].
    /// pt-v21 ships 0.75.
    pub corporate_spread_cycle: f64,
    /// The share of the anticipated earnings level's EXPECTED drift the
    /// valuation leaves out. 0.0, which every preset through pt-v20 carries,
    /// prices `A - e` as `earnings_anticipation_half_life` defines it:
    /// `A = c e + g_phase` is the discounted mean of the level's expected path, so its
    /// expected change is `rho (A - e)` a session (`rho = ln 2 / half-life`), a
    /// drift the phase alone predicts. On pt-v20 that drift is -2.9 points over
    /// the 63 sessions after a contraction begins and +4.5 after a recovery
    /// begins, and the price inherits it as excess return a timing rule can
    /// harvest (r13 macro-clock audit: 2x while the published phase is peak or
    /// contraction beat holding in 0.97 of 90 histories).
    ///
    /// Off zero the engine keeps `D`: at each close `D <- D * 2^(-1/h_D) +
    /// share * rho * (A - e)`, with `A - e` as the last refresh left it and
    /// `h_D` from `earnings_anticipation_drift_half_life`, and the valuation
    /// reads `(A - e) - D`. The price then moves with the news about `A`
    /// (the turns, the level's noise) and not with its expected catch-up,
    /// and `h_D` lets the left-out share return slowly. `D` is zeroed after
    /// the burn-in. The snapshot and the state hash carry `D` and the last
    /// `A - e` only while this is set. Read only with
    /// `earnings_anticipation_half_life` and `earnings_cycle_depth` set.
    /// In [0, 1].
    /// pt-v21 ships 0.9.
    pub earnings_anticipation_drift_share: f64,
    /// The half-life, in sessions, of `D` under
    /// `earnings_anticipation_drift_share`. 0.0 is 1260. Read only with the
    /// share set. In [0, 5040].
    /// pt-v21 ships 252.
    pub earnings_anticipation_drift_half_life: f64,
    /// A risk-management cut: the TRUE growth rate, in per cent a year, below
    /// which the central bank cuts 25 bp at a meeting when inflation is under
    /// target plus 1.5 and the rate is above zero. The branch sits after the
    /// stagflation guard and before the lift-off branch, and moves
    /// `hawkish_dovish_score` by -0.2. 0.0, which every preset through pt-v20
    /// carries, is off (a threshold of exactly zero is not offered). The
    /// ladder's other cut branches read unemployment, which under
    /// `unemployment_adjustment_half_life` 84 rises over months, so on pt-v20
    /// the first cut of an easing comes a median 105 sessions after a
    /// contraction begins, at the trough, and "after the first cut" means "the
    /// recovery rally"; the Fed's first cut came 0 to 13 months BEFORE the NBER
    /// peak in 1989, 1990, 2001, 2007 and 2019 (FRED DFEDTAR and DFEDTARU). In
    /// [-5, 5]. pt-v21 ships 2.
    pub fed_growth_cut: f64,
    /// Whether a field a caller pins holds through that night's close. 0.0,
    /// which every preset through pt-v20 carries, lets the close's own step
    /// move a pinned field (the VIX's law, the cycle's hazard roll, the
    /// 10-year's and the 2-year's daily step, a meeting's decision and its
    /// re-anchoring of the curve) and the next morning's pin write it back, so
    /// a held field is never the pinned value overnight: a held contraction
    /// flips to trough at the close about three times in 315 sessions, the
    /// earnings anticipation re-marks every name about +4 per cent at that
    /// close and -4 the next morning, and the published phase shows a one-day
    /// trough a year later. 1.0 holds every field pinned today at its pinned
    /// value through the close, the meeting included (the corporate yield
    /// already does under `corporate_yield_daily`); the close's draws are all
    /// still taken, so the economy stream's schedule does not move. A pin that
    /// changes the phase starts the new phase's clock, as the model's own
    /// transition does. A switch. pt-v21 ships 1.0.
    pub macro_pins_hold: f64,
    /// The share of each idiosyncratic shock that moves the name's fair
    /// value for good instead of its mispricing, in [0, 1]. 0.0, on every
    /// preset through pt-v19, sends the whole shock to the mispricing;
    /// pt-v20 ships 1.0.
    ///
    /// At 0.0 every stock-specific move reverts on the mispricing half-life:
    /// on pt-v19 96% of a name's own daily variance is mispricing, its
    /// 60-day idiosyncratic variance ratio is 0.51 against the certified
    /// forty's 0.92, and a value screen on published fundamentals predicts
    /// the next 20 days with rank IC +0.38 at stationarity against a real
    /// one near +0.01.
    ///
    /// Off zero, a share `psi` of the name's own shocks (the idiosyncratic
    /// and sector draws of the tick's noise, the news that names the
    /// company, a peer or its sector, and the company's own jump at the
    /// close) lands in a per-name log fair-value level `v`
    /// (`TickStock::fair_value_offset`) and `1 - psi` in `s`. The price
    /// moves by the whole shock on impact either way; what changes is how
    /// much of it later reverts. `v` scales the published fundamentals
    /// (earnings and book) the valuation reads, so the market P/E and the
    /// buyback yield read the same earnings the price does, and it carries
    /// an Ito term so `E[exp(v)]` stays one and the index's expected return
    /// does not move. The market-wide part of each shock stays in `s`
    /// (`fair_value_market_share` moves that); the sector part goes with
    /// the name's own, as listed above.
    pub fair_value_news_share: f64,
    /// The share of each market-wide shock that moves fair value for good,
    /// in [0, 1]: the name's loading on the market factor's draw,
    /// market-wide news and the market jump. 0.0, on every preset through
    /// pt-v19, sends them all to the mispricing; pt-v20 ships 1.0.
    ///
    /// At 0.0 the mispricing's pull reverts every market move on its
    /// half-life: on pt-v19 the index's calendar-year returns spread 11.4
    /// percent (sd) against the S&P's 17.4 at the same daily volatility, a
    /// 20 percent bear market recovers in 158 sessions against the tape's
    /// 670, and a replayed crisis leaves the index a third as far down at
    /// the window's end as the real one did. Off zero, that share joins the
    /// name's fair-value level, as the stock-level share does.
    pub fair_value_market_share: f64,
    /// Which part of a market shock `fair_value_market_share` makes
    /// permanent, a switch. 0.0, on every preset through pt-v19, is the
    /// whole of the name's market input; pt-v20 sets 1.0, which is its plain
    /// loading on the draw.
    ///
    /// At 1.0 the permanent part is `beta * F`, which has zero mean in every
    /// regime. The down-tick tilt, the lagged down-day wire, the crisis
    /// injection, the crash amplifier and the recentering stay in `s` and
    /// revert.
    ///
    /// Those terms are not zero-mean once the market is volatile: the
    /// amplifier fires on a threshold in baseline sigmas, so at a high VIX
    /// it multiplies the tilted down side on most ticks, and the recentering
    /// gives back only the unamplified, unlagged form. In `s` that is a
    /// discount that grows with the VIX and reverts as it falls, which is
    /// how a replayed crash reaches its depth. Made permanent, it is a drift
    /// that runs as long as the VIX is high: on the driven 2020 path, with
    /// this switch at 0.0 and a share of 1.0, the cap-weighted fair-value
    /// level falls about 0.45 in the hundred sessions after the VIX peak
    /// (seed 101). Read only with `fair_value_market_share` non-zero.
    pub fair_value_market_linear: f64,
    /// A ceiling, in multiples of `market_factor_sigma`, on the market
    /// volatility whose shocks `fair_value_market_share` makes permanent,
    /// in [0, 32]. 0.0, on every preset through pt-v19, is no ceiling;
    /// pt-v20 sets 1.5.
    ///
    /// Off zero, a market shock drawn at a daily sigma above `this *
    /// market_factor_sigma` moves fair value by the share times `this *
    /// market_factor_sigma / sigma` of it, and the rest stays in `s` and
    /// reverts on the mispricing's half-life: ordinary news is permanent,
    /// and the excess a fear regime adds is transient. Reversion
    /// concentrates in turbulent periods in the data (Poterba and Summers
    /// 1988 on 1926-40; Kim, Nelson and Startz 1991; Spierdijk, Bikker and
    /// van den Hoek 2012). The same ceiling applies to the market jump's
    /// permanent share. Read only with `fair_value_market_share` non-zero.
    pub fair_value_market_vol_cap: f64,
    /// A floor under the market's permanent share above the ceiling
    /// `fair_value_market_vol_cap` sets: the share of what the ceiling takes
    /// off that moves fair value for good anyway. 0.0, which every preset
    /// through pt-v20 carries, is the ceiling as it stood (the share falls as
    /// `cap / sigma` and the rest reverts on the mispricing half-life); 1.0 is
    /// no ceiling. Off zero the share above the ceiling is `capped + this *
    /// (share - capped)`, `capped` the ceiling's share, so even the most
    /// turbulent market move keeps at least this share of it. It applies
    /// wherever the ceiling does: the session's ticks, the night and the market
    /// jump.
    ///
    /// Why. With the ceiling alone, a fear regime's market moves are almost
    /// wholly transient: a crash sits in `s` and comes back on the 60-session
    /// half-life, so the index rises after a VIX spike on a schedule the VIX
    /// announces. On the r15 screen's leading arm (R15F, 90 held-out
    /// histories) the index gained 1.96, 4.81 and 7.61 per cent over its
    /// unconditional drift 21, 63 and 126 sessions after a one-day VIX rise
    /// in the history's top 1 per cent, against -1.44, -0.50 and +2.75 (se
    /// 1.14, 1.47, 1.89) on the S&P 500 and VIX 1990-2025; the C10 rules
    /// that lever up after such a rise were ahead in 0.69 to 0.72 of the
    /// histories, where on the tape's 21-year windows they are ahead in 0
    /// to 0.35. Measured on R15F at 0.5 with
    /// `fair_value_vix_release_half_life` 504 (box r16g1, held-out seeds):
    /// +0.41/+1.98/+4.73 per cent, the lever-up rules ahead in 0.61 to 0.64
    /// of histories, no C10c breach of 384, V1 0.90/0.83 (R15F 0.86/0.82),
    /// all 40 registered rows in. Read only with `fair_value_market_share`
    /// and `fair_value_market_vol_cap` non-zero. In [0, 1].
    /// pt-v21 ships 0.5.
    pub fair_value_market_excess_share: f64,
    /// Volatility feedback: a discount on every name's fair value while the
    /// VIX is above `fair_value_vix_knee`, `exp(-this * beta * ln(vix /
    /// knee))`, in [0, 1]. 0.0, on every preset through pt-v19, is none;
    /// pt-v20 sets 0.35.
    ///
    /// A function of the VIX alone and no state, so it is transient by
    /// construction: it deepens a fall while fear is high and is given back
    /// as the VIX comes down, at the VIX's own pace, and it moves nothing
    /// that a permanent share or the mispricing's pull carries. Higher
    /// expected volatility raises the required return and lowers the price
    /// (French, Schwert and Stambaugh 1987; Campbell and Hentschel 1992).
    /// Under `macro_publication_repricing` the close's VIX reaches prices at
    /// the close, with the day's move; without it, at the next tick. Read
    /// wherever fair value is: the tick, the overnight opening print, the
    /// re-mark and the stationary opening.
    pub fair_value_vix_discount: f64,
    /// The VIX level, in points, above which `fair_value_vix_discount`
    /// applies. In (0, 200].
    ///
    /// Presets through pt-v19 carry 30.0, which is unread while their
    /// discount is 0.0. pt-v20 sets 40.0.
    pub fair_value_vix_knee: f64,
    /// Half-life, in sessions, of the VIX exposure the volatility-feedback
    /// discount reads. In [0, 252], and 0.0 reads the VIX as it stands.
    ///
    /// Presets through pt-v19 carry 0.0 and pt-v20 sets 5.0. At 0.0 the
    /// discount's whole daily change lands with the VIX's move: on a
    /// held-out grid that doubled the sessions under -5 per cent and took
    /// the 2008 replay's worst month to 122 per cent against 84. Off zero,
    /// the close pulls a smoothed exposure (`EconomyState::vix_feedback`)
    /// toward the VIX's log excess over the knee at this half-life, so the
    /// discount builds over a fearful month and goes as the fear does,
    /// without a session of its own. The snapshot and the state hash carry
    /// the exposure only while this and the gain are both set.
    pub fair_value_vix_half_life: f64,
    /// Half-life, in sessions, at which the volatility feedback's smoothed
    /// exposure falls back toward a LOWER target: the discount is built at
    /// `fair_value_vix_half_life` and given back at this. 0.0, which every
    /// preset through pt-v20 carries, is the one half-life both ways, as it
    /// stood. Read only with the gain and `fair_value_vix_half_life` set; no
    /// new state (the exposure the snapshot and the state hash already carry).
    ///
    /// Why. At one half-life of 5 sessions the discount is given back as
    /// fast as the VIX falls, so the index's rise after a VIX spike runs on
    /// a schedule the published VIX announces: on the r15 screen's leading
    /// arm (R15F) the discount's give-back alone was worth +2.5 and +3.8
    /// per cent 63 and 126 sessions after a one-day VIX rise in the
    /// history's top 1 per cent (desk decomposition, seeds 201-206), where
    /// the S&P 500's whole excess return after the same events 1990-2025
    /// was -0.5 and +2.75. A fear premium that outlasts the VIX's own fall,
    /// as required returns stay high after a crisis while risk appetite
    /// recovers, gives the same depth with a slower, smaller rebound.
    /// Measured on R15F at 504 with `fair_value_market_excess_share` 0.5
    /// (box r16g1, held-out seeds): the give-back's share of the 126-session
    /// rise goes from +1.98 to -0.04 per cent, the audit's xfb lever rule
    /// from +0.96 to +0.26 points a year over the exposure-matched position
    /// (ahead 0.64 to 0.57), and D2's sessions back to the high from 67 to
    /// 78 (real 126); the driven 2020 fall (F1) is unchanged. In [0, 2520].
    /// pt-v21 ships 504.
    pub fair_value_vix_release_half_life: f64,
    /// A knee on how far a name's own fair-value level `v` may sit below the
    /// roster's: the depth, in log units below the equal-weighted mean `v` of
    /// the public, solvent, traded names, past which the close pulls the name's
    /// level back toward the knee at `fair_value_relative_half_life`. 0.0,
    /// which every preset through pt-v20 carries, is a branch: no pull, nothing
    /// read. Off zero, each close adds `(1 - 0.5^(1/h)) (-k - rel)` to the
    /// level of every name whose `rel = v - mean(v)` is below `-k`, and nothing
    /// to any other name. It draws nothing and holds no state beyond `v` (which
    /// the snapshot and the state hash already carry), and it runs only while
    /// the model carries the levels. The pull moves the fair value, not `s`, so
    /// the price follows it at the next tick.
    ///
    /// Why. `v` is a random walk with no anchor: the permanent share of a
    /// name's own news, its beta's share of every market move, and its
    /// earnings surprises all add to it for good, each with its `-dv^2/2`,
    /// so a high-beta name loses `(beta^2 - 1) sigma_m^2 / 2` a year against
    /// the market through every turbulent year. Over a century the spread of
    /// the levels grows without bound: on R16A (seeds 201-208, 100 years) the
    /// equal-weighted relative level's cross-sectional sd is 1.0 at 21 years,
    /// 1.5 at 50 and 2.0 at 100, and a name's deepest relative level reaches
    /// -11. A name that far down, with the market's own trough on top, prints
    /// at the 0.01 price floor and sits there (the thirteenth grade's
    /// H1-100y: a name at the floor for 277 sessions). A real company that
    /// falls that far is restructured, recapitalised or taken over; one that
    /// is not leaves the index. With a fixed roster the knee stands in for
    /// that: only the names past the knee are touched, and every other
    /// name's level is left as it was. Measured on R16A at 4.0 with a
    /// 63-session half-life (box r17floor1c, 100-year runs on held-out seeds
    /// 201-212, 20201-20212 and 30201-30236): no name at the floor, where R16A
    /// has 3,171 name-days at it on 20201-20212; the lowest close of any name
    /// e^1.88 times the floor or more; H1-100y's other clauses as R16A's. A
    /// 21-year history reaches the knee in 5 to 7 of 90. In [0, 20].
    /// pt-v21 ships 4.
    pub fair_value_relative_knee: f64,
    /// Half-life, in sessions, of the pull `fair_value_relative_knee` puts on
    /// a name's fair-value level below the knee. Read only with the knee
    /// set, and then it must be positive. In [0, 25200].
    /// pt-v21 ships 63.
    pub fair_value_relative_half_life: f64,
    /// How much of a pinned VIX is priced the moment it is published: the share
    /// of the gap between the smoothed exposure and the pinned VIX's own excess
    /// that the pin closes. 0.0, which every preset through pt-v20 carries,
    /// leaves the smoothed exposure the discount reads
    /// (`EconomyState::vix_feedback`) to the close's pull at
    /// `fair_value_vix_half_life`, so a VIX a scenario forces is published at
    /// the open and reaches the price over weeks: under a VIX held at x3.5 for
    /// 25 sessions the paired index falls a further 22 per cent (log) after the
    /// published jump, and an agent that reads the VIX front-runs it. Above
    /// zero the pin moves the exposure this share of the way to the pinned
    /// VIX's excess in the pin's re-mark and holds it there through that
    /// session's close, so the discount lands with the published VIX; the pull
    /// resumes on the first session nobody pins. 1.0 closes the whole gap. 0.8
    /// gives the real same-day slope: on the 2008 and 2020 replays the index's
    /// log return on the day's log VIX change, sessions above a VIX of 40,
    /// reads -0.335 / -0.341 against the S&P 500's -0.345 / -0.307 (1.0 reads
    /// -0.42 / -0.44, the smoothed exposure alone -0.04 / -0.06). With
    /// `macro_pins_hold` and `corporate_yield_daily` also on, and no corporate
    /// level or spread pinned that session, the pin also charges the corporate
    /// yield the close's own VIX term on the pin's change, which the close
    /// skips under a VIX pin, so a pinned rise reaches credit as the fall after
    /// the release does. Read only with the gain and the half-life set. In [0,
    /// 1]. pt-v21 ships 0.8.
    pub pinned_vix_feedback: f64,
    /// How much of a session's market-factor variance a pinned VIX's priced
    /// move may take (`pinned_vix_feedback`). 0.0, which every preset through
    /// pt-v20 carries, draws the session's market factor at the state's full
    /// variance on top of the discount the pin priced, so on a replay that pins
    /// the real VIX every session the priced move and the draw add and the
    /// month's volatility counts the VIX twice. Above zero the priced move `J`
    /// (the discount's change today, at a beta of one) is part of the day's
    /// variance `v`: the session draws at `v * max(1 - J^2 / v, 1 - share)`, so
    /// a small priced move leaves the day's total at `v` and a large one keeps
    /// at least `1 - share` of the draw. Real: above a VIX of 40 the daily log
    /// VIX change explains 0.61 (2020) to 0.72 (2007-09) of the S&P 500's daily
    /// variance (corr -0.78 / -0.85, slope -0.31 / -0.35 on the log change).
    /// Read only with `pinned_vix_feedback` on. In [0, 1]. pt-v21 ships 0.7.
    pub pinned_vix_variance_share: f64,
    /// The VIX level from which a PINNED VIX is priced below the knee
    /// (`pinned_vix_calm_share`). 0.0, which every preset through pt-v20
    /// carries, is none: a pin is priced only on its excess over
    /// `fair_value_vix_knee`, so a scenario that forces the VIX from 12 to 30
    /// moves no price at all. Above zero, with the share set, the target a pin
    /// moves the smoothed exposure toward (`pinned_vix_feedback`) is the larger
    /// of the knee's excess `ln(vix / fair_value_vix_knee)` and
    /// `pinned_vix_calm_share * ln(vix / this)`: a second, shallower line that
    /// starts at this level. Only a pin reads it; the close's pull on an
    /// unpinned session still reads the knee alone, since a VIX the market
    /// itself reached comes with the fall that raised it.
    ///
    /// Why. The SF1 row (a forced VIX priced the day it is published)
    /// read 0.82 on held-out seeds and 0.33 on the thirteenth grade's: on
    /// every seed whose VIX was under 16 on the day a x2.5 pin landed, the
    /// pinned VIX stayed under the knee of 40, the paired move was zero and
    /// the statistic a ratio of noise (11 of 30 held-out seeds 201-230, 15
    /// of 30 on 20201-20230). The S&P 500 prices a VIX rise below 40 too:
    /// the index's same-day log return on the log VIX change is -0.110
    /// (corr -0.73) on sessions closing under 40, -0.05 on one-day spikes
    /// of 16 per cent or more from under 20 (1990-2025). The real median
    /// VIX, 17.6 over 1990-2025, is the natural level for the line to start.
    ///
    /// Measured on R16A (sim/r17-sf1 screen r17sf1s1, held-out set A
    /// 201-230/501-530/801-830 and set B, the same plus 20000) with 17.6,
    /// `pinned_vix_calm_share` 0.2 and `pinned_vix_priced_cap` 1.0: SF1 on
    /// the graded 12 seeds 1.06 / 1.03 (R16A 0.82 / 0.66), every one of 30
    /// seeds on set B at 0.86 or more; the driven 2022 P/E per 100 bp of Baa
    /// -7.1 / -7.2 against the S&P 500's -5.2 (R16A -3.8 / -4.0); the driven
    /// 2020 sessions back to the high 97.5 / 71 against 126 (R16A 78 / 61).
    /// Read only with `pinned_vix_feedback` on. In [0, 200].
    /// pt-v21 ships 17.6.
    pub pinned_vix_calm_knee: f64,
    /// The slope of the calm line (`pinned_vix_calm_knee`) as a share of the
    /// knee's: log exposure per log VIX above the calm knee. 0.0, which every
    /// preset through pt-v20 carries, is none. At 0.2 with
    /// `fair_value_vix_discount` 0.35 and `pinned_vix_feedback` 0.8 a pin under
    /// the knee moves the index 0.056 log points per log point of VIX the day
    /// it lands, against the S&P 500's 0.05 on one-day spikes from under 20
    /// (1990-2025); 0.3 and 0.4 take the driven 2022 P/E slope to -8.7 and
    /// -10.2 (band -10.4 to -2.6). Read only with `pinned_vix_feedback` on and
    /// the calm knee set. In [0, 1]. pt-v21 ships 0.2.
    pub pinned_vix_calm_share: f64,
    /// A switch: a pin never lifts the volatility feedback's exposure past the
    /// share of its target it prices the day it lands (`pinned_vix_feedback`).
    /// 0.0, which every preset through pt-v20 carries, is the share-of-the-gap
    /// step as it stood, so a VIX held at one pinned level closes the rest of
    /// the gap over the following pinned sessions (80 per cent on the day at
    /// 0.8, 96 by the next, and so on): a fall an agent reading the published
    /// VIX can sell ahead of. 1.0 caps the step at `pinned_vix_feedback *
    /// target`, or at the exposure already standing if that is higher and the
    /// target higher still, so a held pin prices once and holds; a pin below
    /// the exposure steps down as before. Without it a pin at 0.8 reads SF1
    /// near 0.8 even where it prices a large move (0.76 to 0.90 on R16A's seeds
    /// above the knee), so the row sits a noise's width over its 0.75 floor;
    /// with it the row reads 1.02 to 1.06 on both held-out sets (screen
    /// r17sf1s1). Read only with `pinned_vix_feedback` on and below 1.0. In [0,
    /// 1]. pt-v21 ships 1.0.
    pub pinned_vix_priced_cap: f64,
    /// A ceiling on the annual buyback yield `buyback_payout_share * eps /
    /// price` that the buyback term compounds over the elapsed years. In
    /// [0, 1], and 0.0 means no ceiling.
    ///
    /// Presets through pt-v19 carry 0.0 and pt-v20 sets 0.15. The term reads
    /// the yield at today's price and applies it over the whole elapsed
    /// time, so a name whose price collapses toward the 0.01 floor reads a
    /// yield of hundreds, its fair value runs to exp(hundreds) and the
    /// close's re-mark (whose fixed point assumes the term's elasticity is
    /// well under one) diverges. Measured without the ceiling on settings
    /// close to pt-v20, a name went from 0.10 to 38,220 in one close and the
    /// index rose 86-fold (seed 821, session 4851), and a second setting did
    /// the same on 1 of 90 held-out histories. Off zero, the yield is capped
    /// at this value, so the term's elasticity stays under one. Read only
    /// with `buyback_payout_share` non-zero.
    pub buyback_yield_cap: f64,
    /// The buyback term as accrued state. A switch: 0.0, which every preset
    /// through pt-v20 carries, is the term that stood, `exp(b(P_today) *
    /// elapsed / 252)` with `b = min(buyback_payout_share * eps / P_today,
    /// buyback_yield_cap)`.
    ///
    /// That term reads the yield at today's price and applies it to every
    /// elapsed year, so `d ln FV / d ln P_today = -b t`: fair value moves
    /// against the price it anchors, with a gain that grows linearly in the
    /// elapsed years. On pt-v20 the measured slope is -0.29 by year 10 and
    /// -0.49 by year 40; once `b t` passes one the close's re-mark stops
    /// contracting and the price flips each session, and over 100-year runs
    /// (30 seeds x 40 names) the median name vol goes from 0.23 to 1.70 by
    /// years 40-50 with the worst name's tick autocorrelation at -1.000.
    /// Because the elapsed days enter the level, relabelling the calendar
    /// origin moves prices by up to 1.50 in logs on one seed.
    ///
    /// 1.0: each name carries a running log share-count reduction `L`
    /// (`TickStock::buyback_log_shares`). The close adds
    /// `min(buyback_payout_share * E * exp(L) / P_close, buyback_yield_cap)
    /// / 252`, with `E` the earnings the valuation holds (nominal scale and
    /// fair-value level included), and fair value reads `exp(L)`. `L` is
    /// fixed within a session, so the elasticity is zero, and it does not
    /// read the calendar. New listings start at 0; loss-makers and bankrupt
    /// names do not accrue. The snapshot and the state hash carry `L` only
    /// while this and `buyback_payout_share` are both set. In {0, 1}.
    /// pt-v21 ships 1.0.
    pub buyback_accrual: f64,
    /// Whether the rate indices (`UST2Y`, `UST10Y`, `IGCORP`) re-mark to the
    /// curve the close's macro step publishes at that close, beside the
    /// equities' re-mark (`macro_publication_repricing`), and to a
    /// `pin_macro`'s curve when it is written. 0.0, which every preset through
    /// pt-v20 carries, leaves them on the curve they were marked at until the
    /// next open's repricing, so each index prices each close's curve one
    /// session late: on pt-v20 its close-to-close return matches
    /// `carry - D dy + C dy^2 / 2` to 0.000 bp on the PREVIOUS close's curve move and misses
    /// the same close's by 4.7, 33.6 and 34.9 bp (median), corr(equity index on
    /// day d, IGCORP on day d) is +0.002 against +0.490 with day d+1, and an
    /// after-close fill trades at the stale level (r13 audit).
    ///
    /// Off zero the close's step is followed by `RateBook::remark_now`: every
    /// level reprices to the published curve, without carry, and prints
    /// there. The night's carry still accrues at the next open, at the yield
    /// marked here. The tape's `repriced` column books the re-mark and the
    /// open's move for rate rows as it does for equities. A switch.
    /// pt-v21 ships 1.0.
    pub rate_close_remark: f64,
    /// Whether the rate indices mark intraday to the curve the session so far
    /// implies for tonight's close. 0.0, which every preset through pt-v20
    /// carries, holds them at the published curve all session, and on pt-v20
    /// the close's move is then readable from the session (the 10-year's flight
    /// to quality reads the session return, the corporate yield reads the VIX
    /// the return moves): with `rate_close_remark` alone a sign-timing agent on
    /// IGCORP still earns +13.2 per cent a year at 1x net worth.
    ///
    /// Off zero, while the market is open, each index marks to the published
    /// yield plus `E[tonight's yield | the session so far] - E[tonight's
    /// yield | the open]`, where `E` is the close's own daily step
    /// (`economy::daily::vix_and_yields`) run with its draws at their means
    /// and no meeting, and the rest of the session's market move is
    /// integrated by an eight-point equal-probability rule on the index's
    /// conditional variance times the share of the intraday variance
    /// profile still to come. The mark refreshes on a five-minute grid and
    /// commits nothing: the level and its yield move only at the open, a pin
    /// and the close, so the close-to-close return is the one-step formula
    /// on the same close's curve. The two expectations are carried in the
    /// snapshot and the state hash while this is set. A switch; requires
    /// `rate_close_remark`.
    /// pt-v21 ships 1.0.
    pub rate_intraday_live: f64,
    /// A market-stress cut at a meeting, in points per step. 0.0, which every
    /// preset through pt-v20 carries, is the ladder as it stood, which has no
    /// stress term: on pt-v20 P(a cut within 42 sessions | VIX 30-40) is 0.29
    /// with P(hike) 0.25, against 0.53 and 0.01 on FRED's target rate over
    /// 1990-2025 (r13 audit).
    ///
    /// Off zero the engine keeps the highest published VIX since the last
    /// meeting; at a meeting where it is at or over `fed_stress_vix`,
    /// inflation is under target plus `fed_stress_inflation_gap` and the
    /// rate is over zero, the bank cuts `min(rate, cut * min(4, 1 +
    /// floor((level - fed_stress_vix) / 10)))`, replacing any smaller cut or
    /// any hike the ladder chose (an emergency cut over 0.25), and the
    /// dovish score moves -0.2. No draw. The level is carried in the
    /// snapshot and the state hash while this is set. In [0, 1].
    /// pt-v21 ships 0.1.
    pub fed_stress_cut: f64,
    /// The VIX at which `fed_stress_cut` starts. Read only with the cut on.
    /// In [10, 200].
    pub fed_stress_vix: f64,
    /// How far over target inflation may be for `fed_stress_cut` to fire, in
    /// points. Read only with the cut on. 1.0 as defined; about 80 per cent
    /// of sim stress days carry inflation of 3 or more, so at 1.0 the gate
    /// binds and the bank still hikes in stress. In [0, 10].
    /// pt-v21 ships 2.
    pub fed_stress_inflation_gap: f64,
    /// A market-wide scale on each sector's dividend payout
    /// (`crate::sectors::Sector::dividend_payout`, the share of earnings a
    /// paying name distributes at its sector's anchor multiple). 0.0, which
    /// every preset through pt-v20 carries, is no dividend: a branch, no state,
    /// no draw.
    ///
    /// Off zero, a name pays a cash dividend each quarter. Its payout is
    /// `min(1, this * sector payout)` when its earnings are positive and its
    /// revenue growth is under `dividend_growth_cutoff`, and 0 otherwise; its
    /// target yield is that payout times its earnings over its price at
    /// construction, both public. Ex-dates fall every 63 sessions from a
    /// per-name phase (a hash of the ticker, no draw); the amount is
    /// declared 21 sessions before each ex-date by a Lintner partial
    /// adjustment toward the target yield times an EMA of the name's own
    /// closes (`dividend_adjustment_speed`, capped by
    /// `dividend_yield_ceiling`). Fair value accrues the declared amount
    /// between ex-dates, and at the ex-date open the price drops by the
    /// amount exactly, so a holder's total return is the price return plus
    /// the yield. The rule reads the name's own past closes and nothing the
    /// engine hides. US large caps paid about 1.8 per cent a year over
    /// 2001-2025 (Damodaran, S&P 500), with dividends smooth against
    /// earnings (Lintner 1956). The snapshot and the state hash carry the
    /// per-name state only while this is set. In [0, 2].
    /// pt-v21 ships 1.2.
    pub dividend_payout_share: f64,
    /// Revenue growth at or above which a profitable name pays no dividend.
    /// 0.30, which every preset carries, is read only with
    /// `dividend_payout_share` set. In [0, 10].
    pub dividend_growth_cutoff: f64,
    /// The dividend's annual Lintner adjustment speed toward its target:
    /// the declared amount moves `1 - (1 - this)^(1/4)` of the way each
    /// quarter. 0.4, which every preset carries, is read only with
    /// `dividend_payout_share` set. It is calibrated to the sd of the
    /// index's annual dividend growth (5.2 per cent against a real 7.1,
    /// Shiller 1990-2023), not measured: Lintner fits of the S&P 500's
    /// dividend give 0.11 to 0.13 a year on earnings and about 0 on the
    /// price, the rule's input. In (0, 1].
    pub dividend_adjustment_speed: f64,
    /// A ceiling on a declared quarterly dividend, as a multiple of the
    /// name's target yield at the declaring close: the amount is at most
    /// `this * target yield * close / 4`. A forced cut once the yield has
    /// doubled, so a collapsed name does not pay tens of per cent a year.
    /// 2.0, which every preset carries, is read only with
    /// `dividend_payout_share` set. In [1, 100].
    pub dividend_yield_ceiling: f64,
    /// Switch. At 1, a name's buyback share is `max(0, buyback_payout_share -
    /// its dividend payout)`, so `buyback_payout_share` reads as the TOTAL
    /// payout and a dividend substitutes for buybacks (Grullon and Michaely
    /// 2002). 0.0, which every preset through pt-v20 carries, leaves the
    /// buyback term as it stands; read only with `dividend_payout_share` set. 0
    /// or 1. pt-v21 ships 1.0.
    pub dividend_buyback_substitution: f64,
    /// The night's share of the day's MARKET-factor variance. 0.0, which every
    /// preset through pt-v20 carries, is no split: the session carries the
    /// whole day and nothing moves a price between the close and the open
    /// unless `overnight_variance_ratio` adds a night of its own.
    ///
    /// Off zero the day's variance is SPLIT, not added to. At each open
    /// `Engine::apply_overnight` draws the market factor's night at
    /// `sqrt(w)` times its conditional daily sigma, and every tick of the
    /// session draws it at `sqrt(1 - w)` times its usual scale, so the
    /// night and the session sum to the day the market GJR was fitted to.
    /// The night's draw joins the GJR's day innovation, so the variance
    /// process sees the whole day. The split has to stay exact: the GJR's
    /// persistence is 0.8946 + 0.0844 k^2 at an innovation scale k, and a
    /// night plus session of 1.17 days (k^2 = 1.17) took the index's
    /// volatility from 17.4 to 33 per cent in the earnings-gaps prototype.
    ///
    /// The night's market draw takes the session's down tilt and lagged
    /// wire, with the tilt's mean given back and ALL of the mean the lagged
    /// wire's multiple adds (whatever `market_beta_down_asym_lag_recentre`
    /// says), but not the crash amplifier: one draw carrying half the day's
    /// variance crosses the amplifier's threshold far more often than 390
    /// tick draws do. The session's live lagged-wire condition
    /// (`market_beta_down_asym_lag_live`) reads the day's factor less the
    /// night's draw. Both keep the session from following the gap: with the
    /// night on pt-v20's un-recentred wire and in its live condition, the
    /// equal-weight session return's slope on the night's was +0.099
    /// against a real +0.022 (the review of 50dfeed). Under
    /// `fair_value_market_linear` only its plain loading is permanent, as
    /// the tick's is. Real large caps carry about 0.46 of the forty names'
    /// equal-weighted market variance overnight (2015-2025, the mean of the
    /// names' log returns). Refused beside `overnight_variance_ratio`. In
    /// [0, 0.9].
    /// pt-v21 ships 0.55.
    pub overnight_market_share: f64,
    /// The night's share of the day's SECTOR and IDIOSYNCRATIC variance, as
    /// `overnight_market_share` is of the market's: drawn at `sqrt(w)` at the
    /// open and at `sqrt(1 - w)` through the session. 0.0, which every preset
    /// through pt-v20 carries, is no split.
    ///
    /// The night's own draw joins the name's `random_noise` slot, so the
    /// name's GJR steps on the whole day, and it moves the name's fair-value
    /// level at `fair_value_news_share`, as the session's own noise does.
    /// Real large caps carry a median 0.31 of their idiosyncratic variance
    /// overnight, event nights included (the forty names 2015-2025, EDGAR
    /// 8-K Item 2.02 dates). In [0, 0.9].
    /// pt-v21 ships 0.1.
    pub overnight_idio_share: f64,
    /// Degrees of freedom of the night's idiosyncratic draw. 0.0, which every
    /// preset through pt-v20 carries, is a normal. Off zero, an integer in [3,
    /// 30]: the draw becomes a unit-variance student t, the normal times
    /// `sqrt((nu - 2) / chi2_nu)`, the chi-square the sum of `nu` squared
    /// normals taken on the overnight stream at a site of its own
    /// (`Site::OvernightIdioChi2`), and only while the dial is set and a split
    /// is on, so the stream's schedule moves only on a model that reads it.
    /// Real nights have a kurtosis of about 33 against a session's 4.5 (the
    /// forty names 2015-2025). pt-v21 ships 4.
    pub overnight_idio_df: f64,
    /// The earnings calendar's master switch and the surprise's size, in
    /// units of the name's current idiosyncratic daily sigma. 0.0, which
    /// every preset through pt-v20 carries, is no calendar.
    ///
    /// Off zero every public company reports once a quarter: on session
    /// `63 q + o + j`, where `o` is the name's own offset into the quarter,
    /// drawn once from the forty real names' median offsets (EDGAR 8-K Item
    /// 2.02 acceptance times, 2015-2025: the reaction session falls a median
    /// 18 sessions after the quarter's first), and `j` a jitter in [-3, 3]
    /// drawn each quarter, the session held inside the quarter. The draws are
    /// keyed on the engine's seed, the company's id and the quarter
    /// (`rng::stream::EARNINGS`), so the calendar is a function of the run
    /// and takes no draw from any stream; `Engine.earnings_calendar()` lists
    /// the dates ahead, as a real calendar does, and nothing about the
    /// surprise.
    ///
    /// The surprise is realised at the reaction session's OPENING PRINT: `x
    /// = s * sigma * t / sd(t) - (s sigma)^2 / 2`, mean one in level, joins
    /// the name's fair-value level, and the open prints it. `sigma` is the
    /// name's NON-MARKET daily sigma: its GJR sigma at its idiosyncratic
    /// scale and size multiplier and its sector loading on the sector's
    /// sigma, in quadrature: the part of the name's draws a residual on the
    /// market reads as idiosyncratic. On the forty-name roster at 3.7, with
    /// `earnings_session_sigma` 1.9, the reaction session reads 3.45 of the
    /// name's realised non-event idiosyncratic sd, against a real 3.47
    /// (held-out seeds 2001-2016). Needs a split (`overnight_idio_share` or
    /// `overnight_market_share`) so the opening print realises it. Real
    /// reaction days carry 10.2 times a normal day's idiosyncratic variance
    /// (bootstrap 7.3 to 12.6) and 76 per cent of it in the night. In [0, 20].
    /// pt-v21 ships 3.5.
    pub earnings_surprise_sigma: f64,
    /// Degrees of freedom of the earnings surprise. 0.0 is a normal; off
    /// zero an integer in [3, 30], a unit-variance student t. Read only with
    /// `earnings_surprise_sigma` non-zero.
    pub earnings_surprise_df: f64,
    /// The reaction session's own discovery, in the surprise's units: a
    /// normal part, mean one in level, walked into the name's fair-value
    /// level one open minute at a time through the reaction session, at the
    /// intraday volatility profile's weights, so the session trades it in as
    /// it arrives: no drift, and the first tick carries about 1/390 of it
    /// (`Engine::walk_earnings_sessions`; the draws keyed on the minute).
    /// Until the review of 50dfeed it joined fair value in one piece after
    /// the opening print and printed on the first tick. 0.0 is none. Read
    /// only with `earnings_surprise_sigma` non-zero. In [0, 20].
    /// pt-v21 ships 1.9.
    pub earnings_session_sigma: f64,
    /// The same on the session after the reaction session, walked in the
    /// same way: the real day-after idiosyncratic variance is 1.71 times a
    /// normal day's
    /// (bootstrap 1.34 to 2.14). 0.0 is none. Read only with
    /// `earnings_surprise_sigma` non-zero. In [0, 20].
    /// pt-v21 ships 1.1.
    pub earnings_followthrough_sigma: f64,
    /// The reaction session's volume scale: every tick of a name's reaction
    /// session trades this multiple of what it otherwise would. 0.0, like
    /// 1.0, is no multiple. Real reaction sessions trade 2.16 times a normal
    /// session (bootstrap 2.06 to 2.25). Read only with
    /// `earnings_surprise_sigma` non-zero. In [0, 10].
    /// pt-v21 ships 1.2.
    pub earnings_volume_multiple: f64,
    /// The share of the aggregate earnings cycle's move that a name's fair
    /// value holds back until its next report. 0.0, which every preset
    /// carries, is none: the cycle (`earnings_cycle_depth`) reaches every
    /// name's valuation the session it moves, through the common multiplier.
    ///
    /// Off zero, at each close's macro step this share of the change in the
    /// cycle's level (and its anticipation) is taken out of every traded
    /// name's fair-value level and kept aside for it, and the name's next
    /// report gives the whole kept amount back at its opening print, with
    /// the surprise: the market learns a company's part of the cycle from
    /// the company's own report. Read only with `earnings_surprise_sigma`
    /// and `earnings_cycle_depth` non-zero; the amounts kept are carried by
    /// the snapshot and the state hash only then. In [0, 1].
    pub earnings_cycle_report_share: f64,
    /// THE BUSINESS CYCLE IN THE MARKET'S VOLATILITY: the market factor's
    /// volatility in a contraction or a trough over its volatility in every
    /// other phase, read on the TRUE phase (`EconomyState::cycle_phase`, not
    /// the published one). 0.0, which every preset through pt-v20 carries, is
    /// off: the close takes a branch that reads nothing and moves nothing, and
    /// the snapshot and state hash do not carry the multiplier.
    ///
    /// Off zero the close keeps `l`, the log of a multiplier on the market
    /// factor's volatility, and moves it toward its phase's value:
    ///
    /// ```text
    /// l*  = ln(k_e)            expansion, peak, recovery
    ///     = ln(R * k_e)        contraction, trough
    /// l  <- l + (1 - 2^(-1/h)) * (l* - l)      (h = market_vol_cycle_half_life;
    ///                                           h = 0, and the first close, set l = l*)
    /// ```
    ///
    /// `k_e` is `market_vol_cycle_expansion`. The level the factor's
    /// variance baseline is scaled by is multiplied by `exp(2 l)`, and the
    /// VIX-coupling denominator passed to the factor's close by
    /// `exp(d l)`; the VIX anchor's slow memory and the anchor level the
    /// VIX reverts to are scaled by the same `exp(d l)`, so at `d = 1` fear
    /// is read against the phase's normal level. `d` is
    /// `market_vol_cycle_relative` while `l >= 0` (a stormier phase than
    /// normal) and `market_vol_cycle_relative_calm` while `l < 0` (a calmer
    /// one), and `exp(d l)` is floored at `VIX_STATE_FLOOR / vix_anchor`
    /// (below). A FORCED close (a VIX a scenario pinned) moves `l` but does
    /// not apply it: the pinned VIX already carries the phase.
    ///
    /// # Why
    ///
    /// Real index volatility is countercyclical. S&P 500 daily realised
    /// volatility on NBER recession months over the rest is 1.66 (1950-2025,
    /// 23.8 against 14.3 per cent), 1.87 (1928-2025) and 2.24 (1990-2025);
    /// the VIX's median is 27.5 in a recession against 17.0 outside one
    /// (the tape's VIX, Yahoo ^VIX, which agrees with FRED VIXCLS, by
    /// NBER USREC month, 1990-2025). pt-v20 reads 1.27 and 17.2
    /// against 17.0 on held-out histories: under `vix_level_identity` the
    /// phase table is not read, and nothing else puts the cycle into the
    /// market variance, so its bears fall anywhere (35 to 38 per cent,
    /// by window, overlap a contraction against 7 of 11 post-war S&P
    /// bears) and its index moves
    /// like a random walk at its own moments (2.1-2.4 bears a decade against
    /// 1.45 post-war). Calm expansions with strong drift and bears gathered
    /// in recessions are what make real bears rarer (Schwert 1989; Hamilton
    /// and Lin 1996).
    ///
    /// # Small multipliers
    ///
    /// A multiplier under one scales the VIX's denominator and anchor DOWN
    /// by `exp(d l)`, while the VIX itself is floored at 10
    /// (`VIX_STATE_FLOOR`). Unfloored, a multiplier under about 0.5 at a
    /// power of 1 put the denominator under the VIX's floor: the VIX over
    /// its denominator ran away and the factor variance went to its 32x
    /// ceiling, so a quiet phase read as a panic (bear-dynamics review: a
    /// contraction multiplier of 0.05 at power 1 gave index volatility of
    /// 64 per cent; an expansion multiplier of 0.05, 65 per cent; and the
    /// response turned non-monotone below about 0.5). The variance floor
    /// (`market_vol_floor_multiple`) does NOT bound that, which is what this
    /// line said until the review. The scale is therefore floored at
    /// `VIX_STATE_FLOOR / vix_anchor` (about 0.48 on the certified roster),
    /// which binds only in that region; the shipped candidates' scales are
    /// 0.8 or more. A small ratio is a quiet contraction, which no tape
    /// shows.
    ///
    /// # The fair-value market-volatility cap
    ///
    /// `fair_value_market_vol_cap` (1.5 on pt-v20) splits the market
    /// factor's move into a permanent fair-value part and a transient one
    /// once the factor's conditional sigma exceeds the cap times
    /// `market_factor_sigma` (market/tick.rs, `fair_value_market_vol_cap`).
    /// It reads the UNSCALED `market_factor_sigma`, not the phase's
    /// `exp(l)` times it. So a contraction's higher baseline counts as
    /// fear there and more of a contraction's moves are transient, while at
    /// `d = 1` the VIX coupling reads fear against the phase's own level.
    /// On the bear-dynamics review's regrade of the 90 held-out histories
    /// the mean permanent share in a contraction goes from 0.96 to 0.93
    /// (arm E75R250d75) and in an expansion from 0.96 to 0.97. Small per
    /// session, but it is the contraction's sessions that carry the large
    /// moves, and their transient part reverts: that is a faster recovery
    /// and more long-horizon mean reversion (V1b, the five-year variance
    /// ratio, read 0.58-0.60 on the cycle arms against 0.66 unscaled, on
    /// fresh seeds 2001-2090). `market_vol_cycle_cap_relative` scales the
    /// ceiling by `exp(p l)` in a stormier phase, so the cap reads fear
    /// against the phase's normal volatility as the VIX does; at 0.0 the
    /// ceiling is the unscaled one, as it was.
    ///
    /// # Measured settings (bear-dynamics fix, fresh held-out seeds)
    ///
    /// On 180 fresh histories (seeds 2001-2180, 21 years, years 2-21, the
    /// certified roster), pt-v20 with ratio 2.5, expansion 0.8, half-life
    /// 21, `market_vol_cycle_relative` 0.75, `market_vol_cycle_relative_calm`
    /// 0 and `market_vol_cycle_cap_relative` 1 reads 1.71 bears a decade
    /// against 1.94 off, time with the VIX above 30 of 5.88 per cent against
    /// 6.12 off (floor 4.1), index volatility in a contraction over the rest
    /// of 1.87 (real 1.87, 1928-2025), the VIX's median in a contraction
    /// over the rest of 1.46 (real 1.62) and 54 per cent of bears touching
    /// a contraction (real 64). The first arm recommended (expansion 0.75,
    /// ratio 2.5, one power of 0.75 on both sides) read 4.02 per cent above
    /// 30 on seeds 2001-2090 and 2.82 on 2001-2011.
    ///
    /// # Measured settings (bearcycle fix, sim/r15-bearcycle)
    ///
    /// On top of the r14 screen's N4 arm, with both pin switches on
    /// (`market_vol_cycle_pin_neutral`, `market_vol_cycle_pin_phase`), a
    /// contraction multiplier of 2.1 over an expansion of 0.85 (ratio
    /// 2.47), half-life 10, `market_vol_cycle_relative` 0.75,
    /// `market_vol_cycle_relative_calm` 0 and `market_vol_cycle_cap_relative`
    /// 1 read, on the 90 held-out histories (201-230, 501-530, 801-830):
    /// index volatility in a contraction over the rest 1.98 (real 1.87), the
    /// VIX's median 1.67 (real 1.62), 50 per cent of 20 per cent bears
    /// touching a contraction (real 64), and all forty registered rows in
    /// (box r15bcg1). The half-life and the power matter for the audit's
    /// timing rules: at 21 sessions and a power of 1 (the r14 screen's
    /// N4B85), 23 levered turn rules and 12 spread rules beat holding by
    /// more than a point a year in more than two thirds of histories; at 10
    /// sessions and a power of 0.75, none (N4: 0 and 2). A calmer expansion
    /// (0.8) brought 18 turn rules back.
    ///
    /// In [0, 5]; 0 is off.
    /// pt-v21 ships 2.4706 (42/17).
    pub market_vol_cycle_ratio: f64,
    /// The market factor's volatility multiplier outside a contraction or a
    /// trough (`k_e` in `market_vol_cycle_ratio`'s formula). 0.0 derives it
    /// from the cycle's stationary phase shares, `1 / sqrt(1 - s + R^2 s)`
    /// with `s` the contraction-and-trough share of days
    /// (`economy::cycle::stationary_phase_shares_for`), so the share-weighted
    /// factor variance is unchanged. That form scales the factor alone, which
    /// is about 72 per cent of the index's variance, and so leaves the
    /// index's expansion volatility nearly where it was (bear-dynamics
    /// design: B3 2.17-2.38 on the derived arms), which is why an explicit
    /// value exists. Unread at `market_vol_cycle_ratio` 0.0. In [0, 2].
    /// pt-v21 ships 0.82.
    pub market_vol_cycle_expansion: f64,
    /// Half-life, in sessions, of the cycle multiplier's move (in logs)
    /// toward its phase's value. 0.0 is instant. Unread at
    /// `market_vol_cycle_ratio` 0.0. In [0, 2520].
    /// pt-v21 ships 10.
    pub market_vol_cycle_half_life: f64,
    /// The power of the cycle multiplier by which the VIX-coupling
    /// denominator, the VIX anchor's slow memory and the anchor level are
    /// scaled (`d` in `market_vol_cycle_ratio`'s formula). 1.0 reads fear
    /// against the phase's normal level; 0.0 against the unconditional
    /// level, where the variance's VIX coupling reads a contraction's higher
    /// VIX as fear and amplifies the multiplier (sessions under -5 per cent
    /// 16.6-19.9 a decade against a band ceiling of 12.4 on the design's
    /// d = 0 arms). Read while the multiplier is at or over one;
    /// `market_vol_cycle_relative_calm` is the power under one. Unread at
    /// `market_vol_cycle_ratio` 0.0. In [0, 1].
    /// pt-v21 ships 0.75.
    pub market_vol_cycle_relative: f64,
    /// The power `d` of `market_vol_cycle_ratio`'s formula while the
    /// multiplier is UNDER one (`l < 0`: an expansion, peak or recovery at
    /// `market_vol_cycle_expansion` under one). 0.0 reads a calm phase's
    /// fear against the unconditional level: the VIX anchor and the
    /// coupling's denominator stay where they are while the variance
    /// baseline falls.
    ///
    /// Why a second power. On the tape the VIX's median in an expansion
    /// is 17.0 against 17.6 over all sessions (1990-2025): the calm phase's
    /// normal fear is the unconditional one, while a recession's is 27.5.
    /// One power on both sides lowered the expansion's anchor with its
    /// variance (the anchor times `0.75^d`), and the VIX fell with the
    /// variance it reads: on the bear-dynamics grid the arm E75R250d75's
    /// expansion VIX median was 14.2 and time above 30 in an expansion 3.0
    /// per cent (tape 5.2), so B1 (time above 30, floor 4.1) read 4.9 on
    /// the 90 held-out histories and 2.8 on fresh seeds 2001-2011. At 0.0
    /// the calm side keeps the coupling's reference too, so the fear loop
    /// deepens the calm (the variance falls further for a given
    /// multiplier) while the VIX falls less per unit of variance.
    /// Unread at `market_vol_cycle_ratio` 0.0. In [0, 1].
    pub market_vol_cycle_relative_calm: f64,
    /// The power `p` by which the cycle multiplier scales the ceiling of
    /// `fair_value_market_vol_cap` while the multiplier is over one: the
    /// ceiling is `cap * market_factor_sigma * exp(p l)` for `l > 0`, and
    /// unscaled otherwise. 0.0 leaves it unscaled.
    ///
    /// Why. The cap makes the part of the market's moves above a ceiling
    /// on the factor's sigma transient, as mean reversion in real index
    /// returns concentrates in turbulent periods. The ceiling is in
    /// multiples of the UNSCALED sigma, so with the cycle multiplier on, a
    /// contraction's normal volatility (twice an expansion's on the
    /// candidate arms) counts as turbulence, and much of a contraction's
    /// fall reverts: the index recovers faster and its five-year variance
    /// ratio falls (see `market_vol_cycle_ratio`, # The fair-value
    /// market-volatility cap). At `p = 1` only volatility above the
    /// phase's own normal counts. Unread at `market_vol_cycle_ratio` 0.0.
    /// In [0, 1].
    /// pt-v21 ships 1.0.
    pub market_vol_cycle_cap_relative: f64,
    /// A switch, 0.0 or 1.0: on a session whose VIX a caller pinned
    /// (`pin_macro(vix=...)`: a replay's path, a scenario's VIX
    /// transmission) the cycle multiplier is not applied -- the session's
    /// cap scale, tonight's variance level and coupling denominator and
    /// the VIX anchor's scale all read a multiplier of one -- and the
    /// multiplier itself steps toward one at its half-life instead of
    /// toward its phase's value, so when the pins stop it rises or falls
    /// from there to where its phase puts it rather than jumping. 0.0,
    /// every preset through pt-v20, is the multiplier as it was. A forced close
    /// (`vix_sets_variance`) already did not apply it; this extends the
    /// rule to every pinned VIX. Unread at `market_vol_cycle_ratio` 0.0.
    ///
    /// Why. A pinned VIX is the caller's statement of the market's fear,
    /// and the variance the model takes from it already carries the state.
    /// The long run's 2020 replay pins the real VIX of 2020-21 over an
    /// engine whose own cycle is in an expansion on almost every seed (six
    /// of six on 201-206 held the expansion through the whole replay but
    /// one, which the engine does not know is 2020). There an expansion
    /// multiplier under one scaled the replay's variance by `k_e^2`
    /// whatever the pinned VIX said: on N4 with the cycle at expansion
    /// 0.85 the replay's worst month read 59 per cent against 69 without
    /// it (A1, floor 66.2) and the peak stock correlation 0.70 against
    /// 0.73 (A3), the multiplier at ln 0.85 on every replay session
    /// (sim/r14 e2e2d21, r14 screen arm N4B85, seeds 201-230, 501-530,
    /// 801-830). With the switch the replay reads what it reads without
    /// the cycle, given the state the burn-in left.
    /// pt-v21 ships 1.0.
    pub market_vol_cycle_pin_neutral: f64,
    /// A switch, 0.0 or 1.0: the rule `market_vol_cycle_pin_neutral`
    /// states for a pinned VIX, on a session whose cycle PHASE a caller
    /// pinned (`pin_macro(cycle=...)`: a scenario's `macro.cycle` shock).
    /// 0.0, every preset through pt-v20, is the multiplier as it was. Unread at
    /// `market_vol_cycle_ratio` 0.0.
    ///
    /// Why. A scenario that pins the phase states the recession and brings
    /// its own transmission with it -- the packaged recession.yml triples
    /// the VIX for sixty sessions, widens credit and cuts earnings 35 per
    /// cent, and its S1a/S2 were tuned on the model without the multiplier
    /// (fix/ptv20-recession2). Applied on top, the contraction's doubled
    /// factor volatility over the 315 pinned sessions deepened the fall and
    /// moved the low later, onto the scenario's turn (median low day 380
    /// against 294, drawdown -66 per cent against -60, VIX at the low 68
    /// against 43, seeds 201-212), and the index's level 252 sessions
    /// after the low is where the recovering earnings put it either way
    /// (0.70 of the peak against 0.69), so the rise from the deeper low
    /// read +106 per cent against +72 (S2, band +25 to +80; +102 against
    /// +64 on 201-230 in the r14 screen).
    /// pt-v21 ships 1.0.
    pub market_vol_cycle_pin_phase: f64,
    /// The share `g` of the contraction's excess, in logs, that the TROUGH
    /// gives back: the trough's target is `ln(k_e) + (1 - g) ln(R)`. 0.0,
    /// every preset, is the trough at the contraction's multiplier, as it
    /// was; 1.0 is the trough at the expansion's. Unread at
    /// `market_vol_cycle_ratio` 0.0. In [0, 1].
    ///
    /// Why. The trough is the cycle turning up, and real volatility falls
    /// as it does: the VIX and realised volatility peak near the market's
    /// low, which leads the NBER trough. 2009: the VIX 49.7 at the 9 March
    /// low, 26.4 at the 30 June trough; 2020: 61.6 at the 23 March low,
    /// 34.2 at the April trough (Yahoo ^VIX, the long run's tape
    /// 1990-2025). Held at the contraction's level through the trough, the
    /// multiplier keeps the storm on while the recovery starts. See
    /// `market_vol_cycle_release_half_life`.
    pub market_vol_cycle_trough_release: f64,
    /// The half-life, in sessions, of the cycle multiplier's move while it
    /// FALLS toward a lower target (a phase turning up, or a trough under
    /// `market_vol_cycle_trough_release`). 0.0, every preset, is
    /// `market_vol_cycle_half_life` both ways, as it was. Unread at
    /// `market_vol_cycle_ratio` 0.0. In [0, 2520].
    ///
    /// Why. Volatility falls fast after a bear's low. At the four VIX-era
    /// lows (1990-10-11, 2002-10-09, 2009-03-09, 2020-03-23) the VIX read
    /// 34, 42, 50 and 62, and 63 sessions later 27, 26, 30 and 32 (medians
    /// of the 21 sessions around); the index's realised volatility over
    /// sessions 42-63 after the low was 12.5, 21.4, 26.0 and 26.4 per cent
    /// against 20.8, 30.4, 38.1 and 82.7 over the 21 before (Yahoo ^GSPC
    /// and ^VIX, the long run's tape). One half-life both ways keeps a
    /// contraction's multiplier on well into the recovery.
    pub market_vol_cycle_release_half_life: f64,
    /// The share `g` of the contraction's excess, in logs, that the index's
    /// RALLY OFF ITS LOW gives back in a contraction or a trough: the phase's
    /// target is `ln(k_e) + e (1 - g s) ln(R)`, `e` the phase's own share of
    /// the excess (1 in a contraction, `1 - market_vol_cycle_trough_release` in
    /// a trough) and `s` the rally, `min(1, (c - L) /
    /// market_vol_cycle_recovery_scale)`: `c` the index's log level at the last
    /// close, `L` its lowest close since its highest of the last 252 sessions
    /// (the window `fed_drawdown_hold` reads, total public market cap). 0.0,
    /// every preset through pt-v20, is the multiplier as it was and keeps no
    /// window. Unread at `market_vol_cycle_ratio` 0.0. In [0, 1].
    ///
    /// Why. The release reads the market, not the cycle's phase. Real
    /// volatility falls as the index climbs off its low, which leads the
    /// NBER trough (2009: the VIX 49.7 at the 9 March low and 26.4 at the
    /// 30 June trough; 2020: 61.6 at the 23 March low and 34.2 at the April
    /// trough, Yahoo ^VIX), and a release keyed to the trough phase is a
    /// release a rule reading the published phase can time: R19V with
    /// `market_vol_cycle_trough_release` 1.0 levered the published
    /// contraction and trough ahead of the constant position in 0.689 of
    /// 270 pooled held-out histories against C10c's 2/3 (sim/r20 screen).
    /// pt-v21 ships 0.45.
    pub market_vol_cycle_recovery_release: f64,
    /// The rally off the low, in log points of the index, at which
    /// `market_vol_cycle_recovery_release` is fully given back. Read only with
    /// that dial set, where it must be in (0, 2]; 0.0 on every preset through
    /// pt-v20. pt-v21 ships 0.1.
    pub market_vol_cycle_recovery_scale: f64,
    /// The published VIX's stress premium: the gain `g` of a premium the QUOTE
    /// carries over the engine's VIX state while the variance read-back's
    /// memory is high. 0.0, which every preset through pt-v20 carries, is none:
    /// `Engine::published_vix` is the state bit for bit and no state is
    /// written.
    ///
    /// The VIX loop damps its state against the anchor's slow memory
    /// (`vix_anchor_memory`, `vix_anchor_weight`), which the loop needs for
    /// stability and which costs the quote its stress level: on pt-v20's
    /// held-out histories the median VIX over trailing 21-session realised
    /// volatility, on sessions whose realised volatility is 40 or more,
    /// reads 0.668 against the S&P 500 and ^VIX tape's 0.831 (1990-2025,
    /// calendar-year bootstrap SE 0.045), and the time above 50 is 0.41 per
    /// cent against 0.84. Off zero, the close keeps a memory `m` of the
    /// read-back's log deviation from the anchor's centre at
    /// `vix_anchor_memory`'s rate (in a free run it equals the anchor's own
    /// memory; a session whose VIX is pinned sets it to 0.0), and the
    /// published quote is `min(vix * exp(pi), vix_ceiling)` with
    /// `pi = cap * (1 - exp(-g * max(0, m - knee) / cap))`.
    ///
    /// The quote feeds nothing back: every internal reader of the VIX (the
    /// variance couplings, the fair-value discount, the book, rates, the
    /// central bank, fear and greed, the crisis thresholds, the anchor
    /// memory) reads the state, so with the premium on every price and
    /// every other macro series is the one the premium-off run gives.
    /// `state_snapshot()["economy"]["vix"]` is the state. Requires
    /// `vix_level_identity` and `vix_anchor_memory`. In [0, 10].
    /// pt-v21 ships 3.
    pub vix_stress_premium: f64,
    /// Where the stress premium starts, on the anchor memory's log scale
    /// (0.0 is the anchor's centre). Read only with `vix_stress_premium`
    /// non-zero. In [0, 3]: at or above zero, so a pinned session, whose
    /// memory is 0.0, publishes the pin.
    /// pt-v21 ships 0.6.
    pub vix_stress_premium_knee: f64,
    /// The most the published quote can exceed the state, in log units:
    /// the premium approaches this and never passes it. Must be positive
    /// with `vix_stress_premium` non-zero, and is read by nothing
    /// otherwise. In [0, 1].
    /// pt-v21 ships 0.35.
    pub vix_stress_premium_cap: f64,
    /// The VIX's fear memory: the share of the VIX's log excursion over its
    /// target that the target takes up each session. 0.0, which every preset
    /// carries, is off: the target is the anchor's and no state is written.
    ///
    /// Under the identity the VIX reverts at `vix_mean_reversion` (0.27 a
    /// session) to a target that is the variance read-back held against the
    /// anchor, so a move of the VIX that the read-back does not share is
    /// gone in a few sessions. The tape's is not: the CBOE term structure
    /// moves 0.64 of a VIX point at three months (VIX3M), 0.45 at six
    /// (VIX6M) and 0.30 at a year (VIX1Y), and a day's VIX change is still
    /// 0.67 of itself 11 sessions later, 0.47 at 32 and 0.31 at 74 (local
    /// projections on ^VIX, 2004-2025), where pt-v21 reads 0.60, 0.38 and
    /// 0.21. Off zero, before each close the memory `f` takes up this share
    /// of the excursion `ln(vix / (T exp(f)))` over the target `T` the step
    /// reverts to, and decays at `vix_fear_half_life`:
    /// `f' = 0.5^(1 / H) f + k ln(vix / (T exp(f)))`, and the step reverts
    /// to `T exp(f')`. A move the VIX holds is then carried into the target
    /// and leaves at the memory's own half-life. Requires
    /// `vix_level_identity`. In [0, 1).
    pub vix_fear_uptake: f64,
    /// The fear memory's half-life in sessions. Read only with
    /// `vix_fear_uptake` non-zero, where it must be in (0, 2520]; 0.0 on
    /// every preset.
    pub vix_fear_half_life: f64,
    /// The Fed put: percentage points of policy-rate cut per unit of the
    /// index's log fall since the last meeting. 0.0, which every preset through
    /// pt-v20 carries, is off: no state is written, the Taylor ladder decides
    /// every meeting and the curve reads the policy rate as it stands.
    ///
    /// The ladder has no financial-conditions term, so pt-v20 does not ease
    /// into a sell-off and hikes into one as often as it cuts: on held-out
    /// histories (seeds 201-230, 21 years) the policy rate moves -0.03pp
    /// over the 63 sessions after a VIX close at or above 30 (inflation
    /// under 4, rate at least 0.5), against -0.41 (calendar-year bootstrap
    /// SE 0.14) on the S&P 500 and VIX tape with FRED DFF, 1990-2025, and
    /// 52 per cent of its rate changes at a VIX close of 30 or more with
    /// inflation under 4 are hikes (n 108), against none of the 9 FOMC
    /// target changes on the same filter (FRED DFEDTAR and DFEDTARU; none of
    /// the 14 at a VIX of 30 or more at any inflation, the other 5 cuts
    /// coming at CPI 4.1 to 6.2). Cieslak and Vissing-Jorgensen (2021, RFS)
    /// find about 30bp of cut per 10 per cent intermeeting fall, a gain of
    /// about 3. At 5, the value screened for pt-v20, with the threshold at
    /// 0.0, any intermeeting fall of 2.5 per cent or more rounds to a
    /// quarter-point cut: rate changes double (4.9 a year against 2.35
    /// without the put and 3.0 real) and the mean policy rate falls from
    /// about 2.6 to 1.9, against 2.88 real (review, held-out seeds 801-830).
    ///
    /// Off zero, each close adds the log change of total public market cap
    /// to `EconomyState::intermeeting_return`. At a meeting with inflation
    /// under 4 the put asks for `E = gain * max(0, -I - fed_put_threshold)`,
    /// rounded to a quarter point and no more than the rate; that cut
    /// replaces the ladder's decision when the ladder would cut less, hike
    /// or hold, and a VIX at or above 30 holds any hike. What the put takes
    /// off the ladder's path is owed (`EconomyState::fed_put_owed`), the
    /// Taylor rate the ladder reads is lowered by it, and it is given back a
    /// quarter point at a calm meeting (VIX under 30, no put cut, the ladder
    /// not cutting) once the put's own decaying stock (`EconomyState::fed_put`,
    /// half-life `fed_put_half_life`) is an eighth of a point under it. The
    /// intermeeting return restarts at every meeting. The put's arithmetic
    /// takes no draw and leaves the meeting's draw table as it stands; the
    /// rate path it moves can change which of the economy's state-dependent
    /// draw sites fire later, as any macro dial's does, and a meeting
    /// `fed_put_emergency_vix` calls takes a meeting's draws. The snapshot
    /// and the state hash carry the four fields only while this is non-zero.
    /// In [0, 10].
    /// pt-v21 ships 3.
    pub fed_put_gain: f64,
    /// The intermeeting log fall the Fed put ignores. 0.0 is none; 0.05
    /// lets a five per cent fall pass. Read only with `fed_put_gain`
    /// non-zero. In [0, 0.2].
    pub fed_put_threshold: f64,
    /// Half-life, in sessions, of the Fed put's stock (`EconomyState::fed_put`),
    /// which sets how long a put cut stays in before the calm meetings give
    /// it back. Must be positive with `fed_put_gain` non-zero; read by
    /// nothing otherwise. In [0, 504].
    /// pt-v21 ships 126.
    pub fed_put_half_life: f64,
    /// A VIX close at or above which the bank meets between meetings, as it
    /// did on 2001-01-03, 2001-09-17, 2008-01-22, 2008-10-08 and in March
    /// 2020: with inflation under 4, a policy rate above zero, at least 21
    /// sessions since the last meeting and the next one not yet due, the
    /// next meeting is brought forward to tonight. 0.0 is never. Read only
    /// with `fed_put_gain` non-zero. In [0, 90]; a level under about 25 calls
    /// a meeting every 21 sessions in an ordinary market.
    ///
    /// The meeting it calls is an ordinary meeting, with two costs. It
    /// takes a meeting's draws from the economy stream (the announcement
    /// variant and the next meeting's date) on a session the calendar would
    /// not, so from the first one on an arm with this set no longer shares
    /// the control's economy draws. And it re-anchors the corporate yield
    /// to the meeting's formula at the session's VIX: on the prototype
    /// screened for the design (box bhf1, held-out seeds 201-230), meetings
    /// called at a VIX of 40 widened the spread by 2.3 to 3.75 points at
    /// once, and the 10th percentile of each history's worst session went
    /// from -12.3 to -14.7 per cent. 0.0 avoids both.
    /// pt-v21 ships 50.
    pub fed_put_emergency_vix: f64,
    /// The share of the Fed put's expected cut the curve prices before the
    /// meeting. 0.0 prices none, so the 10-year and 2-year move only when the
    /// cut lands. Off zero the daily anchor of the 10-year reads the policy
    /// rate minus this share of `E` (no more than the rate), and the
    /// meeting's surprise on the 10-year is the rate change plus the same
    /// share of `E`, so a priced cut is not news on the day. Read only with
    /// `fed_put_gain` non-zero. In [0, 1].
    /// pt-v21 ships 1.0.
    pub treasury_put_pricing: f64,
    /// The Treasury haven: percentage points off the 10-year's term premium per
    /// VIX point above 20, while inflation is under 4, in the daily anchor and
    /// in the meeting's 10-year target. 0.0, which every preset through pt-v20
    /// carries, is none.
    ///
    /// In 63-session windows with the index down more than 10 per cent and
    /// inflation under 4, the 10-year falls 0.62pp on FRED DGS10 against the
    /// tape, 1990-2025 (SE 0.11), and 0.00 on pt-v20's held-out histories;
    /// the monthly correlation of the index with the 10-year's fall, in
    /// months starting with inflation under 3, is -0.19 real (SE 0.08) and
    /// +0.02 on pt-v20 (Connolly, Stivers and Sun 2005; Baele, Bekaert and
    /// Inghelbrecht 2010; Campbell, Sunderam and Viceira 2017). No draw.
    /// No state. In [0, 0.05].
    ///
    /// Fit it with `flight_to_quality_gain`. That dial moves the 10-year by
    /// an increment on the session's return, which the anchor's 5 per cent
    /// daily pull erases in about 60 sessions, so it sets the daily
    /// stock-bond correlation and barely moves the 63-session one; this
    /// moves the anchor, so it lasts while the VIX stays up. Both make the
    /// monthly correlation more negative at low inflation, and the flight
    /// to quality does more of it. With the put at 5 (box bhf1, held-out
    /// seeds 201-230), the correlation in months starting with inflation
    /// under 3 read -0.20 with this at 0.0, -0.24 at 0.015, and -0.32 at
    /// 0.015 with the flight to quality raised from pt-v20's 0.008 to
    /// 0.016, against -0.19 real and a proposed band of [-0.35, -0.02]; on
    /// seeds 501-530 (box bhf2) 0.010 read -0.21 against 0.015's -0.22,
    /// both at 0.008. So at a flight to quality of 0.016 take this to 0.010,
    /// and at 0.020 or more to 0.0 to 0.010; neither pair has been run.
    /// pt-v21 ships 0.014.
    pub treasury_haven_gain: f64,
    /// Sessions after a stressed close in which the bank does not raise the
    /// policy rate. 0.0, which every preset through pt-v20 carries, is off: no
    /// state, and the ladder, the stress cut and the put decide as they stood.
    ///
    /// With the stress cut and the put on (r14's N4 arm, held-out seeds
    /// 201-230, 501-530 and 801-830), 0.19 of the sessions with a published
    /// VIX of 30 or more are followed by a hike within 42 sessions, against
    /// 0.07 on FRED's target rate (DFEDTAR and DFEDTARU against VIXCLS,
    /// 1990-2025) and about 0.01 with CPI under 4: the hikes are lift-off
    /// from zero and the put giving its cut back at the first calm meeting,
    /// a median 26 on the state VIX, weeks after the stress. The shortest
    /// real wait from a VIX of 30 to the next hike at CPI under 4 was about
    /// 30 sessions (February to March 2018); after 1998, 2002, 2011 and 2015
    /// it was four months to four years.
    ///
    /// Off zero the engine counts the sessions since the last close whose
    /// published VIX was at or over `fed_stress_vix`. At a meeting within
    /// this many sessions of it, with inflation under target plus
    /// `fed_stress_inflation_gap`, a rise the ladder chose is held (the
    /// dovish score does not move) and the put gives nothing back. A cut
    /// stands. No draw. The count is carried in the snapshot and the state
    /// hash while this is set. In [0, 504].
    ///
    /// Measured (box r15pcfin, arm PC1: N4 with this at 42,
    /// `fed_stress_cut` 0.10, `fed_put_gain` 4 and the priced path at
    /// `treasury_path_pricing` 1, half-life 63, damping 0.5; held-out seeds
    /// 201-230, 501-530 and 801-830): P(hike within 42 | VIX 30+) 0.011
    /// against N4's 0.19, P(cut within 42 | VIX 40+) 0.49 against 0.38,
    /// and the 63-session policy change after a VIX of 30, -0.56 against
    /// -0.69 (-0.41 real).
    /// pt-v21 ships 42.
    pub fed_stress_hold: f64,
    /// The share of the policy path it expects that the curve prices: the
    /// 10-year's daily anchor and the meeting's 10-year target, and the
    /// 2-year's formula, read the policy rate plus this times the market's
    /// forecast of the rate's further change, `M`. 0.0, which every preset
    /// through pt-v20 carries, is off: no state, and the curve reads the rate
    /// as it stands.
    ///
    /// The curve and the corporate yield read the rate as it stands, and
    /// the ladder's rate changes are serially correlated, so the rate's
    /// next moves are forecastable from its last ones and the discount rate
    /// every fair value reads keeps moving after a decision in a direction
    /// known the day it is published. On r14's N4 arm (held-out seeds
    /// 201-230), a cut that follows a cut is followed by a further -0.16pp
    /// by 63 sessions and -0.36 by 126, and the corporate yield falls a
    /// further 0.18pp by 63 sessions and 0.30 by 126 after a cut, which is
    /// the index's excess drift after a cut (+0.46 and +1.08 per cent).
    ///
    /// Off zero `M` is the sum of the policy rate's past changes, each
    /// decayed at `treasury_path_half_life` sessions: the market's forecast
    /// that a cycle continues. The Fed put's own cut and give-back are left
    /// out (a change counts with the change in `fed_put_owed` added back),
    /// since what the put takes is given back. At a meeting the rate change moves `M` by its own
    /// size, and the 10-year's surprise is the change in the rate plus this
    /// times the change in `M`, so the expected path is priced the day it is
    /// published and not in the weeks after. No draw. `M` is carried in the
    /// snapshot and the state hash while this is set. In [0, 3]; needs
    /// `treasury_path_half_life`.
    /// pt-v21 ships 1.0.
    pub treasury_path_pricing: f64,
    /// The half-life, in sessions, of each policy change's weight in the
    /// market's forecast `M` (`treasury_path_pricing`). Read only with the
    /// pricing on, which needs it above 0. In [0, 504].
    /// pt-v21 ships 63.
    pub treasury_path_half_life: f64,
    /// The share of the ladder's rate's distance from a neutral 2.5 per cent
    /// that the 10-year's anchor and the meeting's 10-year target leave out:
    /// the rate the ladder sets (the policy rate plus what the Fed put owes)
    /// plus the priced path is pulled toward neutral, and the put's own overlay
    /// (its cut, and what the curve prices of it) passes through whole. 0.0,
    /// which every preset through pt-v20 carries, is off: the 10-year reads the
    /// policy rate one for one, and a meeting's surprise moves it by the whole
    /// change. The 2-year's formula reads the rate undamped.
    ///
    /// With `treasury_path_pricing` on, a change moves the 10-year by
    /// `(1 - d)(1 + k)` of itself on the day, so this keeps the day's move
    /// what it was while the forecast takes the drift out of the weeks
    /// after. No draw, no state. In [0, 0.9].
    ///
    /// Measured with the pricing at 1 and half-life 63 (box r15pcfin, arm
    /// PC1, as under `fed_stress_hold`): the index's excess after a cut is
    /// +0.44 per cent by 63 sessions and +0.97 by 126 against N4's +0.55 and
    /// +1.23 (SE 0.15 at 63); C10c 21 of 384 rules against 27; in
    /// tf.evaluate, fedcut63 over a constant 1.35x a median +0.26 pts/yr,
    /// ahead 12 of 20. The pricing alone at 1 took the drift to +0.05 by 63
    /// sessions but doubled the 10-year's move in months with a rate change
    /// (monthly sd 0.60 against 0.35) and took the monthly stock-bond
    /// correlation at inflation under 3 from -0.20 to -0.01; this keeps the
    /// day's move and the correlation (-0.26) where they were.
    /// pt-v21 ships 0.5.
    pub treasury_policy_damping: f64,
    /// How much of the next meeting's expected policy change the curve prices
    /// before the meeting. 0.0, which every preset through pt-v20 carries, is
    /// off: no state, and the curve learns a decision on the day it is
    /// published.
    ///
    /// On pt-v20 the curve and the corporate yield move with a decision on
    /// the day it is published (the 10-year by the whole change on R16A, a
    /// 30 bp hike taking the index down 1.1 per cent that session), and the
    /// priced path's forecast then decays until the next meeting, so the
    /// yields drift down and the index up in the weeks after a hike: on
    /// R16A's thirteenth grade, 2x the index for 21 sessions after a
    /// published rise in the policy rate beat the exposure-matched position
    /// in 0.68 of the 90 exam histories (C10c, limit 2/3). On the S&P 500
    /// and FRED's target rate 1990-2025 the 2-year rises about 0.46 points
    /// over the 63 sessions before a hike and 0.01 on the day, the 10-year
    /// and Baa do not move on the day, and the index's excess return over
    /// the 21 sessions after a hike is -0.5 per cent (se 0.5).
    ///
    /// Off zero, at each close the engine takes the change the next meeting
    /// would make if it were held tonight on the published economy (the
    /// published phase, growth and VIX; inflation, unemployment and the rate
    /// as they stand), a shadow meeting on a silent draw source that takes
    /// no draw from any stream, and the curve prices `a` times it times the
    /// fraction of the meeting interval already elapsed: `P = a w S`.
    /// The 10-year's anchor and the 2-year's formula read the rate plus `P`
    /// (damped as the priced path is, under `treasury_policy_damping`), and
    /// the close moves the 10-year, the 2-year and the corporate yield by
    /// the change in what they price, so a decision the market saw coming
    /// is in the curve before the meeting and the meeting moves it by the
    /// surprise alone. A cut is priced at `policy_anticipation_cut_share`
    /// times that. Above 1 the curve prices more than the next meeting:
    /// the ladder's changes come in runs, so 2 prices the next two meetings
    /// as if the second repeated the first (the 2-year rose 0.46 points
    /// before real hikes that averaged 0.33). `P` is carried in the snapshot
    /// and the state hash while this is set. In [0, 3].
    ///
    /// Measured on R16A with this at 2 and `treasury_haven_gain` taken from
    /// 0.015 to 0.010 (boxes c10c1 to c10c3; held-out sets A, 201-230,
    /// 501-530 and 801-830, and B, the same plus 20000; 90 histories each):
    /// the index's excess on a hike's day -0.04 per cent on both sets
    /// against R16A's -1.08 and -1.06, and -0.12 and -0.07 by 21 sessions
    /// (R16A -0.75 and -0.70, so +0.33 and +0.36 of rebound after the day);
    /// the 2-year +0.59 points over the 63 sessions before a hike and -0.09
    /// on the day (set A). The rule 2x for 21 sessions after a published
    /// rise reads -0.19/0.41 and -0.16/0.44 (median/ahead) against R16A's
    /// +0.36/0.62 and +0.41/0.64; C10c breaches 0 and 0 against 0 and 3.
    /// The haven cut keeps H5 (-0.318 and -0.317, floor -0.35) where R16A
    /// had it: at 2 with the haven at 0.015 it read -0.336 and -0.333.
    /// pt-v21 ships 1.8.
    pub policy_anticipation: f64,
    /// The share of `policy_anticipation` a shadow meeting's cut is priced
    /// at: 0.0 prices rises only, 1.0 cuts as rises. Read only with
    /// `policy_anticipation` set. The priced put (`treasury_put_pricing`)
    /// and the priced path already price the put's cut and a cycle's next
    /// cut, and a stress cut is news, so the rises are priced first.
    /// In [0, 1].
    pub policy_anticipation_cut_share: f64,
    /// The share of the VIX slope taken out of the corporate spread's formula,
    /// `(1 + 0.02 (1 - cut) (VIX - 12) + ...) * multiplier`, in the meeting's
    /// re-anchor, the close's daily move and a pinned VIX's credit leg. 0.0,
    /// which every preset through pt-v20 carries, is the slope that stood.
    ///
    /// The VIX reverts within days of a sell-off while the index stays down,
    /// so a spread that is the VIX's formula widens with the session and
    /// then narrows back through the month. On the r14 screen's N4 arm
    /// (sim/r14 e2e2d21, ten held-out histories of 3000 sessions) 76 per
    /// cent of the same-close covariance between the index and minus the
    /// corporate yield's change is reversed over the next 20 sessions and
    /// the spread change's variance ratio at 21 sessions is 0.55; on the
    /// screen's 90 histories the spread's daily sd is 4.4 bp against 3.1 on
    /// FRED BAA10Y (1990-2026), and the daily stock-IG correlation on held
    /// closes reads 0.43 and the monthly 0.15, against +0.27 and +0.47 for
    /// SPY and LQD, 2015-2025. See
    /// `corporate_spread_equity_gain`, which puts the credit's link to the
    /// index on the index itself. No draw. No state. In [0, 1].
    /// pt-v21 ships 1.0.
    pub corporate_spread_vix_cut: f64,
    /// Credit's leverage term: percentage points of corporate spread, times the
    /// cycle's spread multiplier, per unit of the index's log fall below its
    /// own slow average (`EconomyState::spread_equity_gap`, half-life
    /// `corporate_spread_equity_half_life`). 0.0, which every preset through
    /// pt-v20 carries, is off: no state is written and the spread is the VIX's
    /// formula.
    ///
    /// In a structural model of default (Merton 1974; Collin-Dufresne,
    /// Goldstein and Martin 2001) the spread widens as the firm's equity
    /// falls against its debt and stays wide until the equity recovers or
    /// the firm re-levers, so the credit move made on a down day is not
    /// given back when the VIX reverts. Off zero, each close steps
    /// `D = 0.5^(1/H) (D - ln(1 + r))` on the session's index return `r`
    /// (from the last close, the quantity the flight to quality reads), and
    /// the formula's base gains `gain * D`: in the close's daily move
    /// (`corporate_yield_daily`), in the meeting's re-anchor and in the
    /// rate indices' live projection of the close. A VIX pin does not stop
    /// it; a pinned corporate yield or spread does, as it stops the VIX
    /// term. No draw. The snapshot and the state hash carry the gap only
    /// while this is non-zero.
    ///
    /// Measured on the r14 screen's N4 arm with `corporate_spread_vix_cut`
    /// 1.0, this at 2.0, `corporate_spread_equity_half_life` 126 and
    /// `treasury_10y_noise` 0.025 (box r15bondcorr3, held-out seeds
    /// 201-230, 501-530 and 801-830): the daily stock-IG correlation on held
    /// closes reads 0.378 (N4 0.434), the monthly 0.279 (N4 0.151), graded
    /// row R4 on the last print +0.165 (N4 +0.181), the spread's daily sd
    /// 3.0 bp (N4 4.3) and the share of the same-close covariance reversed
    /// within 20 sessions 0.29 (N4 0.71); all 40 registered rows pass. The
    /// held close still reads about 0.21 above the last print, because the
    /// close's re-mark carries the corporate yield's change into the
    /// equities, and most of that change on a meeting day is the 10-year's
    /// jump (27 bp sd on meeting sessions against 4 bp on the rest). In
    /// [0, 10].
    /// pt-v21 ships 1.8.
    pub corporate_spread_equity_gain: f64,
    /// Half-life, in sessions, of the index's slow average that
    /// `corporate_spread_equity_gain` measures the fall against: how long a
    /// spread widened by a fall stays wide while the index stays down. Must
    /// be positive with the gain set; read by nothing otherwise. In [0, 1260].
    /// pt-v21 ships 126.
    pub corporate_spread_equity_half_life: f64,
    /// Monthly cycle hazard added per unit of the index's log fall below its
    /// slow average beyond `cycle_equity_hazard_knee`, in an expansion and at a
    /// peak (`adjust_transition_probability`, economy/cycle.rs). The fall is
    /// `EconomyState::spread_equity_gap`, credit's leverage gap, at
    /// `corporate_spread_equity_half_life`; with this set the gap runs even
    /// when `corporate_spread_equity_gain` is 0.0. 0.0, which every preset
    /// through pt-v20 carries, adds nothing and leaves the gap unrun unless the
    /// credit gain runs it. In [0, 20].
    ///
    /// Why. A bear market that begins in an expansion raises the chance the
    /// expansion ends: the wealth effect and tighter financial conditions,
    /// and the index's standing as a leading indicator (Estrella and
    /// Mishkin 1998; the Conference Board's leading index carries the
    /// S&P 500). The engine's cycle read no market at all, so its 20 per
    /// cent bears fell in an expansion as often as in a recession: 0.48 of
    /// them have a true contraction between the peak and the trough plus 63
    /// sessions on R17T (620 bears over 180 held-out histories, sets A and
    /// B), against 7 of 11 post-war S&P 500 bears (0.64); 0.89 bears a
    /// decade fall outside a recession against the tape's 0.53.
    /// pt-v21 ships 5.
    pub cycle_equity_hazard: f64,
    /// The index's log fall below its slow average (the gap
    /// `cycle_equity_hazard` reads) under which that dial adds nothing. Read
    /// only with `cycle_equity_hazard` set. In [0, 1].
    /// pt-v21 ships 0.1.
    pub cycle_equity_hazard_knee: f64,
    /// Monthly hazard added in an expansion and at a peak where the economy
    /// runs without a market: the stationary opening's law
    /// (`cycle_stationary_opening`) and the macro burn-in
    /// (`macro_burn_in_days`). Neither has an index, so `cycle_equity_hazard`
    /// adds nothing there, and the phase the run opens in is drawn from a cycle
    /// whose expansions last longer than the run's. This stands in for the
    /// market's hazard: set it so the phase a run opens in has the run's own
    /// phase shares. That is below the mean of `cycle_equity_hazard * max(0,
    /// gap - knee)` over a run's expansion and peak sessions, because the
    /// market's hazard comes in bursts and a constant of the same mean ends
    /// more expansions. 0.0, which every preset through pt-v20 carries, adds
    /// nothing and the opening law is the one that stood. Read only in those
    /// two places, never in a session with a market. In [0, 1].
    ///
    /// Why. With `cycle_equity_hazard` at 5 and a knee of 0.1 on R17T a
    /// contraction or trough holds 0.08 of year 0's sessions and 0.10 to
    /// 0.12 of each later year's (90 held-out histories, sets A and B). The
    /// mean added hazard over expansion and peak sessions is 0.010 to 0.012
    /// a month (30 histories, seeds 50201-50230), but at 0.011 the opening
    /// holds 0.667 expansion and 0.153 recovery against the run's 0.690 and
    /// 0.140; at 0.007 each of the five shares is within 0.01 of the run's
    /// (1500 openings, docs/MODEL.md).
    /// pt-v21 ships 0.011.
    pub cycle_equity_hazard_opening: f64,
    /// Sessions of market the run has lived before day zero: the last this many
    /// days of the economy's burn-in (`macro_burn_in_days`), played on a copy
    /// of the opening engine with the economy's recorded phase set each day and
    /// every stream drawn from generators of its own. The run then opens with
    /// that copy's volatility state: the factor variance components and their
    /// return memory, the VIX and its slow level, the anchor's and the stress
    /// premium's memories, the cycle's volatility multiplier, and each name's
    /// and sector's variance and jump excitation. Nothing else is copied, so
    /// prices, fair values, the economy and every draw the run itself takes are
    /// the ones the engine would open with. 0.0, which every preset through
    /// pt-v20 carries, runs nothing and the market opens at the constructor's
    /// baseline. Read only at construction, and only when the opening is
    /// settled (not when a caller supplies the macro state). A whole number in
    /// [0, 2520].
    ///
    /// Why. The burn-in runs the economy without a market, so every
    /// volatility state opens at the phase-free baseline: a run that opens
    /// in an expansion starts with its factor variance at 1.8 times the
    /// level its expansions hold, the VIX near 19 where they hold 16, and
    /// index volatility of 0.17 in its first months against 0.145, which
    /// takes two quarters to settle; a run that opens in a contraction
    /// starts calm. Year 0 then reads about 0.004 hotter than the years
    /// after it over 1350 held-out histories on R17T and R17Bd, which
    /// PH5's volatility clause reads. The slow variance component's
    /// persistence (0.9913 a session) leaves 0.11 of the opening's gap
    /// after 252 sessions and 0.012 after 504.
    /// pt-v21 ships 504.
    pub market_prehistory_sessions: f64,
    /// Whether the run also opens with the valuation state the market's
    /// prehistory (`market_prehistory_sessions`) left on its copy: a switch,
    /// 0.0 or 1.0. On, the copy's end hands back, beside the volatility state,
    /// each name's mispricing (which the opening's split takes in place of its
    /// draw), the VIX feedback's exposure, the anticipation's drift, the
    /// earnings cycle, credit's leverage gap (with the corporate yield moved by
    /// what it adds to the spread) and the Fed put's owed cut and stock (with
    /// the policy rate and the curve lowered by the owed cut) and the market's
    /// forecast of the policy path (with the curve moved by what it prices).
    /// Whatever moves fair value is booked into the names' fair-value levels by
    /// the opening's split, so no opening price moves. 0.0, which every preset
    /// through pt-v20 carries, carries none of it. Requires
    /// `market_prehistory_sessions` above zero; read only at construction.
    ///
    /// Why. Those states open where a market that never traded leaves them
    /// and drift over the first year: on R17Bd with a 252-session prehistory
    /// the names' mean mispricing fell to -0.013 by month 6 and the VIX
    /// feedback's exposure built from 0 to 0.035 by month 12, while the Fed
    /// put's owed cut built from 0 to 0.24 and took the policy rate down by
    /// a quarter point. Year 0's index return was 1.95 points below year 1's
    /// over 1350 held-out histories, all in its first two quarters, which
    /// PH5's return clause reads.
    /// pt-v21 ships 1.0.
    pub market_prehistory_valuation: f64,
    /// The share of the intermeeting fall the Fed put's cut left unanswered
    /// that the next meeting's clock starts from. 0.0, which every preset
    /// through pt-v20 carries, restarts the clock at zero at every meeting, as
    /// it stood. Read only with `fed_put_gain` non-zero. In [0, 1].
    ///
    /// The put rounds its ask to a quarter point, so at a gain of 3 an
    /// intermeeting fall under about 4.2 per cent asks for nothing, and a
    /// bear that falls 3 to 4 per cent between each pair of meetings is
    /// never answered however far it goes: on R19V's held-out histories
    /// (sets A and B, 602 bears of 20 per cent) the median policy change
    /// from peak to trough sits on -0.50, the quarter-point atom, with 0.44
    /// to 0.48 of bears cut by 0.75 or more by their trough and 0.70 to 0.73
    /// by 63 sessions after it. Off zero, at a meeting where the put was
    /// live and not held at the rate, the clock restarts at this share of
    /// `min(0, I + c / gain)`, where `I` is the intermeeting return the
    /// meeting read and `c` the cut the put took: the fall its cut did not
    /// answer, which the index's later returns then add to or take back.
    /// Nothing is carried when the put's cut stopped at the policy rate, or
    /// with inflation at or above the put's ceiling. The priced put
    /// (`treasury_put_pricing`) reads the same clock. No draw. No state
    /// beyond the intermeeting return the put already carries.
    /// pt-v21 ships 1.0.
    pub fed_put_carry: f64,
    /// The index's log fall from its highest close of the last 252 sessions
    /// (total public market cap, close to close) at or above which a meeting
    /// holds any rise, as the stress hold does (`fed_stress_hold`): with
    /// inflation under target plus `fed_stress_inflation_gap`, a rise the
    /// ladder chose is held and the Fed put gives nothing back. 0.0, which
    /// every preset through pt-v20 carries, is off: no state. On, the engine
    /// keeps the window's returns, carried in the snapshot and the state hash,
    /// and a market prehistory hands its window to the run. In [0, 1].
    ///
    /// The stress hold reads the VIX, which reverts within weeks of a
    /// sell-off while the index stays down, so a bear whose VIX has settled
    /// is hiked into: on R19V's held-out histories (sets A and B) 0.43 to
    /// 0.47 of 20 per cent bears see a rise between peak and trough, and
    /// those rises leave the median policy change from peak to trough on the
    /// -0.50 atom. The FOMC's rises at CPI under 4 came with the S&P 500
    /// within about 10 per cent of its high, December 2018 (16 per cent
    /// down) the exception.
    /// pt-v21 ships 0.12.
    pub fed_drawdown_hold: f64,
    /// The cross-sectional sd of each name's opening mispricing. 0.0 adopts
    /// each name's whole day-zero premium of price over fair value as its
    /// mispricing `s`.
    ///
    /// Presets through pt-v19 carry 0.0 and pt-v20 sets 0.016. At 0.0, on a
    /// generated roster that premium is the P/E scatter, sd about 0.32
    /// against a stationary `s` of about 0.06, so every name drifts toward
    /// fair value in a known direction for months (the value screen's rank
    /// IC in a market's first 60 days is +0.75 on pt-v19, and a 5-day
    /// momentum rule earns +10% there).
    ///
    /// Off zero, the opening `s` is the roster's cap-weighted premium (so
    /// the index opens with the mispricing it always had) plus a draw of
    /// this sd per name, re-centered to cap-weighted zero, and the rest of
    /// each name's premium is its opening fair-value level `v`. The
    /// published fundamentals are then a noisy read of fair value, and the
    /// price is not taken to be wrong by the whole gap. The draw is one
    /// normal per name on its own stream (`rng::stream::OPENING`), taken
    /// when the engine is built, so no other stream moves.
    pub opening_mispricing_sigma: f64,
    /// The sd of the market's common opening mispricing, the index's own
    /// premium over fair value on day zero. 0.0 uses the roster's
    /// cap-weighted day-zero premium of price over fair value.
    ///
    /// Presets through pt-v19 carry 0.0 and pt-v20 sets 0.001. At 0.0 (under
    /// `opening_mispricing_sigma`, or without it, where every name adopts its
    /// whole premium and the index comes out the same) a generated roster
    /// opens its index that far from fair value by construction: +0.11 on
    /// the certified roster, and -0.33 to +0.30 on the suite's 20-name
    /// rosters. The index then reverts on the mispricing half-life, which
    /// gives a drift of up to 30 per cent in the first months with nothing
    /// happening, and a certified year one of +2 per cent against the
    /// tape's +10.
    ///
    /// Off zero, the common level is a draw at this sd, the market opening
    /// at a point of its own stationary mispricing; the rest of each name's
    /// premium is its fair-value level. One more normal on
    /// `rng::stream::OPENING`, after the per-name draws.
    pub opening_market_sigma: f64,
    /// Market-shock size, in baseline sigmas, above which the crash
    /// amplifier fires. Every preset ships 2.0.
    pub crash_amplifier_threshold: f64,
    /// Extra market loading the crash amplifier adds per baseline sigma of
    /// shock beyond `crash_amplifier_threshold`. Every preset ships 0.2.
    ///
    /// # Loop gain through the VIX
    ///
    /// With `crash_amplifier_conditional_sigma` at 0.0 (presets before
    /// pt-v19), `shock_magnitude` is normalized by the base sigma rather
    /// than the conditional one, and `factors.rs` explains that choice. The
    /// amplifier's conditional second moment
    /// (`market::index_var::amplifier_moments`) then grows as the square of
    /// the regime ratio. Under `vix_level_identity` that moment is in the
    /// VIX's own target, so the map from the VIX to the variance it implies
    /// is superlinear at the top of the VIX's range. Measured on a pin
    /// ladder with the crisis blend off (`tools/calibration/pin_ladder.py`),
    /// `implied(v) / v` falls to 0.732 at VIX 40 and then rises: 0.854 at
    /// 80, 0.941 at 100. On that slope it would cross one somewhere past 110
    /// (an extrapolation), and it crosses one inside `vix_ceiling` on a
    /// roster whose factor block is a larger share of the index's variance.
    ///
    /// So the amplifier has a loop gain. Against a factor-variance process
    /// whose ordinary excursions reach twenty to fifty times its target (see
    /// `market_vol_alpha`, and note that the shipped mixture's fourth moment
    /// is finite) it takes the VIX to `vix_ceiling` and the loop holds it
    /// there: 81 of 7,560 seed-days on three of the certification
    /// protocol's thirty rosters, with the crisis blend switched entirely
    /// off. See `market::index_var`.
    pub crash_amplifier_slope: f64,
    /// Switch for the sigma the crash amplifier measures a shock in: 0.0
    /// uses the baseline constant, any nonzero value the tick's own
    /// conditional sigma. pt-v1 through pt-v18 carry 0.0. pt-v19 and pt-v20
    /// carry 1.0.
    ///
    /// # A switch
    ///
    /// There is no half-normalized shock. `factors.rs` branches on
    /// `== 0.0` and reads the conditional sigma on every other value, so
    /// the magnitude is unused and the two meaningful values are 0.0 and
    /// 1.0. A survey axis over `[0, 1]` never draws exactly zero and would
    /// map only the switched-on side, so `tools/calibration/atlas_survey.py`
    /// lists it in `SWITCH_DIALS`, with `cycle_stationary_opening` and
    /// `garch_omega_sector_scaled`, which are the same shape.
    ///
    /// At 0.0 nothing extra is computed and no draw moves, so pt-v1 to
    /// pt-v18 reproduce bit for bit.
    ///
    /// # What it changes, in one line
    ///
    /// `shock_magnitude` is `|F| / sigma`. With the baseline sigma the
    /// amplifier's argument is `s|z|` for the regime ratio
    /// `s = sqrt(v_f) / market_factor_sigma`, so a high-variance regime
    /// pushes more ticks past `crash_amplifier_threshold` and pushes them
    /// further past it. With the conditional sigma the argument is `|z|`
    /// and the regime drops out entirely.
    ///
    /// # Why pt-v19 sets it
    ///
    /// In `market::index_var`'s closed form the amplifier's second moment
    /// is `E[z^2 A^2]` at `a = m s`, `c = T / s`, which grows without
    /// bound in `s`. Under `vix_level_identity` that moment is inside the
    /// VIX's own target, so the map `v -> implied(v)` is superlinear at the
    /// top of the VIX's range: `implied(v) / v` falls to 0.732 at VIX 40
    /// and then rises, 0.854 at 80 and 0.941 at 100, and it crosses one
    /// inside `vix_ceiling` on a roster whose factor block is a larger
    /// share of the index's variance. The ceiling is then absorbing, and
    /// measured with the baseline sigma the VIX sits on it on 81 of 7,560
    /// seed-days on three of the certification protocol's thirty rosters
    /// with the crisis blend switched off, and on 11 of 120 rosters in a
    /// wider census.
    ///
    /// At nonzero this switch sets `a = m` and `c = T`, so `E[z^2 A^2]` is
    /// constant in the regime, the amplified factor block is linear in
    /// `v_f` exactly as the unamplified one is, and the map cannot cross
    /// the diagonal however far the factor variance moves. That removes the
    /// runaway at its source. Splitting the runs traced it to the draw stream
    /// (14 of 120 runs with the roster held, 0 of 120 with the stream held).
    ///
    /// # What it costs
    ///
    /// Measured on a preset whose VIX could not see the amplifier, the
    /// conditional sigma cost 0.03 of volatility clustering, 0.10 of excess
    /// kurtosis and 0.006 of correlation. `CHANGELOG.md` has the figures
    /// re-measured on pt-v19. The cost buys the loop's stability.
    ///
    /// The realism the baseline normalizer kept is real and is given up:
    /// with a constant normalizer the amplifier turns a variance regime into
    /// a correlation regime, which is what crises do. The rest of the crisis
    /// apparatus takes over that job (the blend, `sector_vix_coupling` and
    /// the jump coupling), and none of it has an unbounded loop gain through
    /// the read-back.
    pub crash_amplifier_conditional_sigma: f64,
    /// Switch for the VIX reading the market factor's variance target uses:
    /// 0.0 reads the VIX level against a fixed anchor, any nonzero value
    /// reads only the excursion above the level the index's own conditional
    /// variance implies. Every shipped preset carries 0.0.
    ///
    /// # A switch
    ///
    /// There is no half-excursion. `engine.rs` branches on `== 0.0` and
    /// reads the identity's own read-back on every other value, so the
    /// magnitude is unused and the two meaningful values are 0.0 and 1.0.
    /// `tools/calibration/atlas_survey.py` lists it in `SWITCH_DIALS` beside
    /// [`ModelParams::crash_amplifier_conditional_sigma`],
    /// `cycle_stationary_opening` and `garch_omega_sector_scaled`, because
    /// a survey axis over `[0, 1]` never draws exactly zero and would map
    /// only the switched-on side.
    ///
    /// At 0.0 the engine passes `self.vix_anchor`, so nothing extra is
    /// computed and no draw moves.
    ///
    /// # The double count it removes
    ///
    /// Under [`ModelParams::vix_level_identity`] the VIX is the index's own
    /// conditional variance in points, plus a fear excursion. The factor's
    /// target then reads `(VIX / anchor)^2`, which is mostly the factor's
    /// own variance coming back to it: the target reverts the factor toward
    /// `c * s_f` of its own level. So under the identity
    /// `market_vol_vix_coupling`, a fear-to-variance channel when the VIX
    /// came from a phase table, is a loop-gain dial, which its name does not
    /// say.
    ///
    /// The loop's static gain is `theta = sum_k s_k c_k`, and the factor
    /// term carries about 0.45 of it against 0.11 for the instantaneous
    /// sector, jump and per-name couplings together. A standing bias `L`
    /// in the target moves the level by `1 / (1 - theta)`, which is 2.7x at
    /// the shipped theta of about 0.62. Every excursion in the fear channel
    /// is amplified by that factor before it reaches the variance, and the
    /// variance's own excursions are amplified again on the way back.
    ///
    /// # What it changes, in one line
    ///
    /// The ratio's denominator. At 0.0 it is `market_vol_vix_anchor` (or
    /// the derived anchor under the identity), a constant. At nonzero it is
    /// `vix_from_variance(vix_variance_premium, V_t)` evaluated on the
    /// session's own index variance, the level the identity says this VIX
    /// ought to be. The ratio is then 1.0 whenever the VIX is exactly what
    /// the variance implies, the target is exactly `base`, and only the
    /// fear excursion lifts it.
    ///
    /// # Stability
    ///
    /// At a pinned VIX `v` the target becomes `base (1 - c + c v^2 / I(V)^2)`
    /// with `I(V)` the read-back, which is decreasing in the variance where
    /// the old form was constant in it. The variance's fixed point solves an
    /// increasing function against a decreasing one, so it is unique and
    /// globally attracting at every pin.
    ///
    /// Free-running, the VIX is `I + E` for a fear excursion `E` that
    /// `vix_return_clamp` bounds. The ratio is `(1 + E / I)`, and `I` grows
    /// with the variance while `E` does not, so the regime ratio decays as
    /// the variance moves up and the target falls back to `base`. The
    /// runaway left once `crash_amplifier_conditional_sigma` removes the
    /// amplifier's superlinearity (5 of 120 rosters, 25 seed-days) is a
    /// day-to-day excursion amplified 2.7x around a map that already
    /// contracts. This switch takes the amplification to about 1.1x by
    /// removing the term.
    ///
    /// # What it leaves alone
    ///
    /// `theta_i`, the instantaneous couplings (`sector_vix_coupling`,
    /// `jump_vix_coupling`, `garch_vix_coupling`). Those read the VIX the
    /// same day it prints and close the loop through the tick, outside the
    /// variance target. They are about 0.11 of theta and they stay.
    ///
    /// It also leaves alone the four fear-channel dials that `flattens_at`
    /// reads (`vix_return_clamp`, `vix_return_gain`, `vix_return_exponent`
    /// and `vix_target_shock_cap`), so the bound they put on the VIX does
    /// not change.
    ///
    /// # It is only meaningful under the identity
    ///
    /// Off `vix_level_identity` there is no read-back to be an excursion
    /// above, and `vix_implied_from_market` is either zero or the factor's
    /// sigma on a different scale. The test
    /// `the_excursion_switch_requires_the_identity` checks that no shipped
    /// preset sets one without the other.
    pub market_vol_vix_excursion: f64,
    /// VIX points past `CRISIS_VIX_THRESHOLD` for the sector-to-market
    /// crisis blend to reach 1.0 before its cap. Every preset ships 1.4.
    pub crisis_blend_ramp: f64,
    /// Ceiling of the crisis correlation blend. pt-v1 to pt-v6 carry 0.8 and
    /// pt-v7 onward 0.98.
    pub crisis_blend_cap: f64,
    /// How hard a crisis loads every name onto the market factor, as a
    /// multiplier on the crisis spike. 0.0 turns the extra loading off,
    /// which is what pt-v19 and pt-v20 ship.
    ///
    /// pt-v1 to pt-v10 carry 0.5. pt-v11 and pt-v12 carry 0.8. pt-v13 to
    /// pt-v18 carry 0.8275881. The crisis blend adds `crisis_blend_source * gain *
    /// crisis_spike * market_factor` to a name's market component. The
    /// spike is capped at `crisis_blend_cap`, 0.98, so the extra market
    /// loading a crisis can produce is the gain times 0.98 of beta (0.49 at
    /// a gain of 0.5).
    ///
    /// Measured at thirty seeds, every other route to a real-sized crisis
    /// lever adds variance that is outside the market factor (jumps are
    /// per-name, the sector draw is per-sector), and crisis-state
    /// cross-sectional correlation is the market factor's share of total
    /// variance. So the lever and co-movement trade against each other, and
    /// this gain is the channel that can raise both, because it multiplies
    /// the market factor and nothing else. The spike saturates its cap in
    /// any crisis, because `effective_stress` is the raw point excess over
    /// the 25.5 threshold and is about 19.5 at a held VIX 45.
    ///
    /// # Why pt-v19 and pt-v20 set it to zero
    ///
    /// The tape's VIX has no crisis attractor and its correlation is a
    /// function of realized common volatility, which this model's factor
    /// share already reproduces with no lift. A lift keyed on the VIX
    /// level gives the map a second stable fixed point the tape refutes.
    /// See `pt_v19`. The rest of this entry is about the value pt-v13 to
    /// pt-v18 ship.
    ///
    /// # The pt-v13 to pt-v18 value
    ///
    /// 0.8275881 came from a search against a VIX read-back that could not
    /// see the blend at all. `market::index_var` sees it now, so the gain
    /// has a stability condition: `implied(v) < v` for every `v` above
    /// `crisis_vix_threshold`, which binds at `vix_ceiling` and is a
    /// quadratic in this gain with a closed-form root. That root is 0.1496
    /// on seeds 101 to 103.
    ///
    /// The root does not stop the runaway. At a gain of exactly 0.0 the VIX
    /// still reaches `vix_ceiling` on 81 of 7,560 seed-days over the
    /// certification protocol's thirty rosters. The runaway is the crash
    /// amplifier's second moment against the factor variance's own ordinary
    /// dispersion, and this gain can make it worse but cannot fix it. See
    /// `market::index_var`'s module documentation for the measurement and
    /// for the three routes that would settle it, none of them a dial.
    ///
    /// A gain near 0.05 passes the certified bands (18 of 18 at both
    /// horizons, the down-tail row at 1.9522 of a 1.9600 ceiling at 252 and
    /// 1.7561 at 504), and no preset ships it. It comes from a grid search
    /// on one row, and that row is a `pooled_rate` with no seed scale, so
    /// the value cannot carry an error bar. The loop is no better at it
    /// either: the ceiling is reached on 93 of 7,560 seed-days against 81
    /// at a gain of zero.
    pub crisis_blend_gain: f64,
    /// Where the crisis correlation injection comes from. At 0.0 it comes
    /// out of the sector slot, and at 1.0 it is added to the market
    /// component, leaving the sector draw whole. pt-v1 to pt-v6 carry 0.0
    /// and pt-v7 onward 1.0.
    ///
    /// At 0.0 the sector draw is attenuated by the spike and the market
    /// factor is injected through the sector slot, which consumes the
    /// sector draw exactly when sector structure matters most. That path is
    /// bit-identical by branch. At 1.0 the sector draw is left intact and
    /// the same market injection is added to the market component directly.
    ///
    /// Measured with the sector draw consumed, `sector_excess_corr` reads
    /// -0.007 at a held VIX 45 whatever `sector_factor_sigma` is, where the
    /// real 2020 window reads +0.10, and a longer window reads lower sector
    /// excess than a shorter one on every base, because it contains more
    /// crisis days.
    pub crisis_blend_source: f64,

    // ── Per-name GJR-GARCH (market/garch.rs) ────────────────────────────
    /// The GJR-GARCH constant: the variance a name reverts toward
    /// when neither yesterday's shock nor yesterday's variance pulls
    /// it. Small by construction, because the long-run level is set
    /// by the sector's base variance rather than by this term.
    pub garch_omega: f64,
    /// Weight on yesterday's squared shock: how sharply a name's variance
    /// reacts to its own last move. Higher alpha is a twitchier name. The
    /// persistence that carries the reaction forward is `garch_beta`.
    pub garch_alpha: f64,
    /// Weight on yesterday's variance: how long a name's volatility
    /// remembers. `alpha + beta + gamma/2` is the persistence, and it
    /// must stay under one or the variance process has no stationary
    /// level and a long run drifts without bound. The calibration
    /// searches (persistence, alpha share) rather than these two
    /// directly, because independent boxes around them put half
    /// their mass across that line (`TRANSFORMED_AXES` in
    /// `tools/calibration/atlas_survey.py`).
    pub garch_beta: f64,
    /// GJR leverage-effect asymmetry: the extra weight a negative shock
    /// gets in a name's next variance. Zero recovers symmetric GARCH(1,1)
    /// bit for bit.
    pub garch_gamma: f64,
    /// Ceiling on a name's GARCH variance, as a multiple of the sector's
    /// long-run variance. Every preset ships 5.0. It is a guard, but it was
    /// measured binding on volatility clustering, so calibration searches
    /// it under bounds.
    pub garch_ceiling_multiple: f64,
    /// How much a name's own variance follows the VIX, on the market
    /// factor's own target shape. 0.0 turns it off and is bit-identical by
    /// branch.
    ///
    /// pt-v1 to pt-v9 carry 0.0. pt-v10 to pt-v12 carry 0.3. pt-v13 carries
    /// 0.0269. pt-v14 onward carry 0.14219611. At 0.0 the per-name GJR-GARCH reads no
    /// macro state at all: its clamps are multiples of a static per-sector
    /// variance, and its own unconditional level sits below the floor those
    /// clamps impose (5.6% annualized against a floor of 19.8% for
    /// technology), so a name's variance hovers near that floor whatever the
    /// market is doing. That 5.6% is at a persistence of 0.8364. pt-v19's
    /// 0.9416 puts the same level at 9.3% annualized, which is still under
    /// technology's floor and over the 6.4% floor the three lowest-sigma
    /// sectors carry. So `garch_ceiling_multiple` was measured not to bind
    /// at any value with the coupling at 0.0: the variance never gets
    /// within twenty times of it.
    ///
    /// The consequence is the crisis lever. Total volatility is the market
    /// factor plus the name's own, the factor scales with the VIX squared
    /// and the name's does not, so a held VIX 65 raises one term and leaves
    /// the other where it was: the lever reads 4.75x against a real 6.16x
    /// with the factor's own clamp already past what a record VIX implies.
    /// Real single-stock volatility rises with the market's in a crisis;
    /// at 0.0 here it cannot.
    ///
    /// At `c` the clamp reference becomes `base * (1 - c + c * (vix /
    /// market_vol_vix_anchor)^e)`, with `e` the exponent
    /// [`ModelParams::garch_vix_exponent`] carries, the same map the market
    /// factor's target uses, so at the anchor the reference is exactly the
    /// base at any coupling and the two variance processes read the regime
    /// the same way.
    pub garch_vix_coupling: f64,
    /// Exponent on the VIX ratio in a name's variance reference. Every
    /// preset ships 2.0, the square.
    ///
    /// At 2.0 the code in `market/daily.rs` takes the literal square by
    /// branch, so `mathx::pow` never runs. Same spelling and same
    /// discipline as [`crate::market::factor_vol`]'s `vix_response` for the
    /// market factor's own target, which makes the two shapes separable:
    /// the index and the roster can be given different laws.
    ///
    /// # What the shipped pair reaches, and what it does not
    ///
    /// `garch_vix_coupling` has an effect on the shipped vector, though it
    /// does not reach the variance's level.
    /// [`crate::market::garch::update_garch_variance_for`] takes the
    /// constant `garch_omega` while `garch_omega_sector_scaled` is 0.0, so
    /// the coupled reference does not reach the process's level, but both
    /// clamps are multiples of that same reference. The recursion's own
    /// constant level, `garch_omega / (1 - persistence)`, is 3.42e-5 at
    /// pt-v19's persistence of 0.9416: under the floor for eight of the
    /// twelve sectors and at most 2.2x it for the other four, so what
    /// places a name's variance is the reference and the shock rather than
    /// the constant. Measured on the held roster
    /// (`Universe.random(40, seed=111)`, the VIX pinned at 5 against 65,
    /// medians over 60 graded days after 252): the reference moves 2.28x, a
    /// name's GARCH variance 5.41x, and the variance the tick actually draws
    /// with, `max(variance, idio_sigma_floor)`, 3.57x. At coupling 0.0 the
    /// same three read 1.00x, 4.61x and 3.16x. So the coupling buys 1.17x
    /// of the 5.41x and the rest arrives by another road.
    ///
    /// That road is the shock. `close_day_with` feeds the recursion the
    /// day's `random_noise` attribution, and
    /// [`crate::market::factors::calculate_live_factors`] builds it as
    /// `market_component * crash_amplifier + tilt_recentre +
    /// sector_component + idiosyncratic_noise`. A name's variance is
    /// therefore re-excited by the market factor's own VIX-coupled variance
    /// at a gain of `(alpha + gamma / 2) / (1 - beta)`, which is 0.72 on
    /// pt-v19. What a name's variance does in a crisis is what the factor
    /// does, attenuated and floored; nothing in it is a statement about the
    /// roster.
    ///
    /// # The value the roster's own scaling law gives
    ///
    /// The tape's names scale like its index: realized volatility ~
    /// `VIX^0.7088` across the roster, which is the 6.16x lever the record
    /// grades against a 13x of VIX. A variance is a volatility squared, so
    /// a name's variance reference must be proportional to `VIX^(2 *
    /// 0.7088)` = `VIX^1.4176`.
    ///
    /// A blend `1 - c + c r^e` is a power law only at `c = 1`. Below it the
    /// `1 - c` term is a floor the low end never leaves, and no exponent
    /// makes such a map a law. So the law is a pair of values:
    /// `garch_vix_coupling` at 1.0 and this dial at 1.4176, where the
    /// reference moves `13^1.4176` = 37.9x for the 13x of VIX the lever row
    /// reads. Neither figure is fitted to that row; both come off the
    /// tape's exponent. No shipped preset uses this pair.
    pub garch_vix_exponent: f64,
    /// Floor on a name's GARCH variance, as a multiple of the sector's
    /// long-run variance. Every preset ships 0.25.
    pub garch_floor_multiple: f64,
    /// Switch that scales `garch_omega` by each sector's base variance in
    /// place of one constant for every sector. 0.0, which every shipped
    /// preset carries, keeps the constant and is read by branch.
    ///
    /// The identity, which is the cascade path's arithmetic verbatim
    /// ([`crate::market::garch::update_garch_cascade`]):
    ///
    /// ```text
    /// omega_i = sector_base_variance * (1 - alpha - beta_i - gamma/2)
    /// ```
    ///
    /// which is exactly the omega that makes the recursion's unconditional
    /// variance `omega / (1 - persistence)` equal the sector's own base
    /// variance. No number is chosen: every term is already a parameter or
    /// the sector table's own `daily_sigma` squared.
    ///
    /// At the shipped constant of 2e-6 the recursion's unconditional level
    /// is about 1.2e-5 for every sector, against clamp floors running
    /// 1.6e-5 to 1.56e-4, so the level is global while the band around it
    /// is per sector. The sector table's 3.1x spread in `daily_sigma` then
    /// reaches the price as roughly 1.4x, and per-name volatility carries
    /// no sector ordering at all against a real corpus ordered from staples
    /// 17.0 per cent to technology 29.4.
    pub garch_omega_sector_scaled: f64,
    /// Feeds the per-name GJR-GARCH the name's own noise, in the units the
    /// coefficients were fitted in, in place of the whole `random_noise`
    /// column. 0.0, which every shipped preset carries, keeps the column
    /// and is read by branch; 1.0 is the whole form.
    ///
    /// # The defect
    ///
    /// `engine.rs` hands `close_day_with` the day's `random_noise`
    /// attribution as the innovation, and
    /// [`crate::market::factors::calculate_live_factors`] builds that
    /// column as `market_component * crash_amplifier + tilt_recentre +
    /// sector_component + idiosyncratic_noise`. Three of the four terms
    /// are outside the name's own variance. Measured on the held roster
    /// (`Universe.random(40, seed=111)`, 504 days, eight seeds, medians
    /// over 320 names), in multiples of the `h` the recursion carries: the
    /// whole column's mean square is 1.387 h, of which the market's and
    /// the sector's draws together are 0.810 h and the name's own is only
    /// `kappa^2` = 0.473 h.
    ///
    /// `kappa^2` is under one because the tick draws the name's own noise
    /// as `sqrt(max(h, idio_sigma_floor)) * idio_sigma_scale * cap_mult *
    /// volatility_multiplier / sqrt(390)` per tick, so the session's sum
    /// carries that factor squared in place of `h`. Two consequences, both
    /// measured:
    ///
    /// - the shock coefficient acts on the name's own variance at
    ///   `(alpha + gamma P(neg)) kappa^2` = 0.0728 rather than the 0.1511
    ///   the dials say, so the process's self-persistence is 0.863 where
    ///   the dial vector reads 0.9416 and the tape's names read 0.938. The
    ///   fitted GJR persistence of the model's names is 0.894, between the
    ///   two, because the rest arrives from outside;
    /// - the name is re-excited by the market's own noise at a gain of
    ///   0.72, so a name's variance memory is partly the factor's memory
    ///   wearing the name's label.
    ///
    /// # The form
    ///
    /// The close divides the name's own noise by the scale the tick drew
    /// it with. `kappa^2` is accumulated in the engine the same way the
    /// attribution is, as the day's sum of
    /// `(idio_scale / sqrt(390) * cap_mult * volatility_multiplier *
    /// suppress * tick_scale)^2`, so it is the scale that actually ran, and
    /// the innovation is
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
    /// a name that is no longer re-excited by the market rests on the
    /// floor. The companion is therefore
    /// [`ModelParams::garch_omega_sector_scaled`] at 1.0, which makes the
    /// recursion's own level the sector's base variance times the coupled
    /// reference, with [`ModelParams::garch_vix_coupling`] 1.0 and
    /// [`ModelParams::garch_vix_exponent`] 1.4176 (the roster's own
    /// scaling law, `volatility ~ VIX^0.7088`). With the level following
    /// the reference, the regime reaches a name as a level, and the shock
    /// is the name's own.
    ///
    /// [`ModelParams::idio_sigma_floor`] is the one constant this form
    /// cannot put in the right units. It ships at 1e-4 against a median
    /// name variance of 8.9e-5, so on 59 per cent of name-days the draw
    /// the innovation is divided by was taken at the floor and not at `h`,
    /// and `eps^2` then reads the floor: on the shipped level the fed
    /// innovation would be 1.28 h rather than the 1.00 h the form is
    /// after. The companion repairs most of it by lifting the level. With
    /// the sector-scaled omega the median variance is 1.29e-4, the floor
    /// binds on 31 per cent of name-days, and the fed innovation reads
    /// 1.09 h. The floor stays at its shipped value here, and the residual
    /// 0.09 is part of why the form lands short of the dialed 0.9416.
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
    /// This dial alone, without the sector-scaled omega, shows why the two
    /// travel together: the median variance falls to 8.19e-5, the floor
    /// binds on 64 per cent of name-days, and the log AR(1) reads 0.8673,
    /// below the shipped 0.8770. Taking the common noise out of the
    /// innovation takes the level with it, and the name comes to rest on
    /// the clamp floor.
    pub garch_innovation_commensurate: f64,
    /// The absolute floor under a name's daily variance in the tick and in
    /// the overnight path, in daily variance units. Every preset ships
    /// 1e-4, and 0.0 removes it.
    ///
    /// It guards against a zero variance, which would freeze a price.
    /// Against a recursion whose own level is about 6.3e-5 it is also the
    /// binding constraint on 61 to 90 per cent of name-days in nine of the
    /// twelve sectors. The guard's job is already done per sector by the
    /// `[0.25, 5.0] x sector_base_variance` clamp in
    /// [`crate::market::garch::update_garch_variance_for`], which cannot
    /// return zero for a positive base, so a preset that sets this to 0.0
    /// has one fewer constant in it.
    pub idio_sigma_floor: f64,

    // ── Market-factor variance process (market/factor_vol.rs) ───────────
    /// The market factor's own GARCH reaction term: how sharply market-wide
    /// variance responds to the last market-wide shock. This process gives
    /// every name a common volatility regime, and a name's own GJR-GARCH is
    /// idiosyncratic on top of it.
    ///
    /// # Stationarity and the fourth moment
    ///
    /// `alpha + beta + gamma / 2 < 1` makes the process revert to its target
    /// in expectation. pt-v14 to pt-v18 carry 0.28035 and 0.69245, which
    /// give 0.9728 and pass it. The fourth-moment condition
    /// `3 alpha^2 + 2 alpha beta + beta^2 < 1` reads 1.1035 for this fast
    /// component alone and fails, but the process is a 0.65/0.35 mixture
    /// with `market_vol_slow_weight`, its own condition is the spectral
    /// radius of a 4x4 matrix, and on pt-v16 to pt-v18 that reads 0.9870,
    /// so the factor variance there has a finite fourth moment. pt-v19 and
    /// pt-v20 move all three (alpha 0.0066, beta 0.8946, gamma 0.1556) and
    /// `market_vol_slow_persistence` to 0.9913. The GJR fourth-moment
    /// coefficient at that triple reads 0.9908, recorded under
    /// [`ModelParams::market_vol_alpha_excursion`]; the mixture's 4x4
    /// spectral radius has not been recomputed for it.
    ///
    /// What the process has is heavy, finite dispersion: variance-of-variance
    /// 3.4 times its mean squared, implied factor kurtosis 13 against the
    /// tape's own GARCH at 2.8 and 11.3. The per-seed spread is the
    /// finite-sample dispersion of any GARCH at this persistence (a bare
    /// recursion with no engine reproduces it), and the tape's own years
    /// spread as widely. Pinned at a VIX whose target is 0.45 of baseline,
    /// the variance is measured sitting at 11.7 times baseline for eighty
    /// consecutive sessions on one of thirty certification rosters, and that
    /// is ordinary rather than pathological.
    ///
    /// That dispersion matters for the VIX loop. `market::index_var` prices
    /// the crash amplifier into the VIX, so an excursion of that ordinary
    /// size can imply a VIX above `vix_ceiling`, and the loop holds it
    /// there. These two coefficients were not derived with that stability
    /// property in view. A GARCH fit to the real index gives 0.1059 and
    /// 0.8787, with error bars.
    pub market_vol_alpha: f64,
    /// The market factor's variance persistence. `alpha + beta` is this
    /// process's persistence and is subject to the same stationarity limit
    /// as the per-name one, and to the same reparameterization in the
    /// survey.
    /// pt-v21 ships 0.9446.
    pub market_vol_beta: f64,
    /// GJR leverage on the market factor's variance update: the extra weight
    /// a down day's squared shock gets. 0.0 is the symmetric update, carried
    /// by pt-v1 through pt-v18; pt-v19 and pt-v20 ship 0.1556.
    ///
    /// A down day loads `market_vol_alpha + market_vol_gamma` on the
    /// squared shock where an up day loads `market_vol_alpha` alone, and
    /// omega compensates by `gamma/2` so the unconditional level stays on
    /// target. The dial moves variance between down and up states without
    /// adding any. The per-name asymmetry (`garch_gamma`) has existed since
    /// pt-v2, but the common factor ran symmetric through pt-v18, and that
    /// is why those presets cannot produce correlation asymmetry.
    /// Correlations that rise in falling markets are the common factor's
    /// leverage effect, and on those presets `corr_asymmetry` sits at
    /// -0.016 against a real +0.015 with no other dial able to move it.
    ///
    /// Applies to the fast component only. The slow component's own asymmetry
    /// is `market_vol_slow_gamma`, 0.0 on every preset through pt-v20. pt-v21
    /// ships 0.06.
    pub market_vol_gamma: f64,
    /// GJR asymmetry on the SLOW component of the market factor's variance.
    /// 0.0, which every preset through pt-v20 carries, is the symmetric slow
    /// step: the close takes a branch that makes the call it always made.
    ///
    /// Off zero, a down day loads `a_s + gamma` on the squared factor where
    /// an up day loads `a_s`, and the carried share gives back `gamma/2`:
    ///
    /// ```text
    /// v_s' = (1 - a_s - b_s) * target_s + (a_s + gamma * 1[F < 0]) * F^2
    ///        + (b_s - gamma/2) * v_s,     (a_s, b_s) = (gain * p_s, (1 - gain) * p_s)
    /// ```
    ///
    /// so the slow component's own persistence `p_s` and its resting level
    /// are unchanged (over a symmetric factor the down arm loads `gamma/2`
    /// on average, which is what the carried share gives back).
    ///
    /// # What it does to the mixture
    ///
    /// The slow component is fed the day factor, whose variance is the
    /// MIXTURE's, so moving `gamma/2` from its own level onto the shock
    /// makes it follow the faster mixture more closely. On pt-v20's
    /// coefficients the joint mean recursion's slow pole goes from 0.9855
    /// (a 47-session half-life) to 0.9814 at 0.3 and 0.9805 at 0.6 (35
    /// sessions), and its fast pole from 0.923 to 0.733. The unclamped
    /// fourth moment of the joint recursion (spectral radius of
    /// `E[A (x) A]` over a normal factor) is finite on pt-v20 at 0.0
    /// (0.984) and stops being so at 0.129 (1.016 at 0.3, 1.039 at 0.6):
    /// past that the `market_vol_ceiling_multiple` clamp bounds the tail,
    /// as it does on pt-v1, whose single component violates the condition
    /// knowingly (`factor_vol.rs`,
    /// `the_recursion_reverts_and_the_clamp_carries_the_fourth_moment`).
    ///
    /// # Why
    ///
    /// `market_vol_gamma` (the tape's GJR, 0.1556) acts on the fast 65 per
    /// cent of the factor's variance, and the factor is about three
    /// quarters of the index's, so the index's response to a fall is about
    /// a third of the tape's and it is gone within a month. Measured on
    /// held-out pt-v20 histories (crash-vol-state design, design
    /// repository): the sum over lags 1-20 of corr(r_t, |r_t+k|) reads
    /// -0.53 against the S&P 500's -1.35 (1990-2025; every 20-year US
    /// window since 1926 lies in -0.79 to -1.75), and monthly skew -0.24
    /// against -0.80. Index leverage decays over weeks to months (Bouchaud,
    /// Matacz and Potters 2001; Corsi and Reno 2012), which is the slow
    /// component's timescale.
    ///
    /// # Measured (held-out seeds 201-230, 501-530, 801-830; boxes cvsg1, cvsg2)
    ///
    /// On pt-v20, 90 histories of 21 years, the leverage sum reads -0.51,
    /// -0.62, -0.68, -0.76, -0.78, -0.82 and -0.87 at 0, 0.1, 0.15, 0.2,
    /// 0.3, 0.45 and 0.6, and monthly skew -0.26, -0.37, -0.42, -0.43,
    /// -0.47, -0.60 and -0.57 (the tape -1.35 and -0.80). Two registered
    /// rows bind: B5, the sessions under -5 per cent a decade (ceiling
    /// 12.4), reads 11.5 at 0, 11.7 at 0.2, 12.3 at 0.3 and 12.7 and 12.9 at
    /// 0.45 and 0.6; and D1's excess kurtosis in the held-out-seeds cell
    /// (ceiling 24.0) reads 18.8 at 0, 23.4 at 0.2 and 24.1 to 24.9 from
    /// 0.3. So 0.3, 0.45 and 0.6 fail, and 0.2 is the largest value tried
    /// that passes the rows graded at it. That is not the full grade:
    ///
    /// - Graded at 0.1, 0.15 and 0.2 (box cvsg2): A1-A3, B1-B8, C1, C2 and
    ///   D1 in all four certification cells, all pass; V1a and V1b from the
    ///   same long run pass (V1b 0.649 at 0.2, floor 0.55).
    /// - Graded at 0, 0.3, 0.45 and 0.6 only (box cvsg1), so inferred at
    ///   0.1-0.2: B9, C4a, C4b, C5-C8, R1-R6, E1, D2, F1, L1 and C10. All
    ///   pass on those arms.
    /// - Not measured at any value: C3, C9, R7a, R7b, S1a, S1b and S2.
    ///
    /// The certification's VIX AR(1) row (reported, not gated) moves up:
    /// on pt-v20 it passes panel_252 and held-out seeds and reads
    /// refused/below in panel_504; at 0.1 it passes all three; it reads
    /// refused/above in panel_252 from 0.15 and in panel_504 at 0.2.
    ///
    /// Read only with `market_vol_slow_weight` non-zero: the single-component
    /// close has no slow component. In [0, 1], with
    /// `(1 - market_vol_slow_gain) * market_vol_slow_persistence - gamma/2`
    /// at or above zero so the carried share cannot go negative.
    /// pt-v21 ships 0.05.
    pub market_vol_slow_gamma: f64,
    /// Gain of the market factor's variance on its RETURN MEMORY, in log
    /// variance per unit of the memory. 0.0, which every preset through pt-v20
    /// carries, is off: the memory never moves, the snapshot and state hash
    /// do not carry it, and the variance is the component mixture bit for
    /// bit.
    ///
    /// Off zero the engine keeps `l`, an exponentially weighted memory of
    /// the day factor in baseline sd units, sign flipped so a fall adds:
    ///
    /// ```text
    /// u  = -F / sigma_b             (a fall)
    ///    = -(1 - down) * F / sigma_b  (a rise)
    ///      - down * sqrt(v / b) / sqrt(2 pi)   (re-centred)
    /// l' = phi * l + (1 - phi) * u,   phi = 0.5^(1 / half_life)
    /// ```
    ///
    /// where `b = sigma_b^2 = market_factor_sigma^2` and `v` is the variance
    /// the day was drawn at. The variance the next session draws with --
    /// and the index-variance identity, and so the VIX, reads -- is
    ///
    /// ```text
    /// clamp(mixture * exp(k * l - k^2 * s^2 / 2)),
    /// s^2 = (1 - phi) / (1 + phi) * ((1 + (1 - down)^2) / 2 - down^2 / (2 pi))
    /// ```
    ///
    /// `s^2` is the memory's stationary variance at the baseline, so the
    /// multiplier's mean is about one. The GJR components are fed the day
    /// factor over the square root of the multiplier the day was drawn at
    /// (the variance over the mixture), so a fall is not counted twice.
    ///
    /// A FORCED close (a scenario's `vix_sets_variance`) moves the memory
    /// with the day's factor but sets the variance to the law's level
    /// without the multiplier, so a scenario's asserted fear is the fear the
    /// session draws at; the first free close after it applies the memory.
    ///
    /// This is a return-path memory (Black 1976; Christie 1982; the
    /// exponential leverage kernel of Bouchaud, Matacz and Potters 2001),
    /// which a GARCH recursion on squared shocks cannot express: a GJR term
    /// remembers the size of a fall, not the fall.
    ///
    /// Measured (held-out seeds 201-230, 501-530, 801-830, box cvsg1): at a
    /// gain of 3 on a 40-session half-life with `market_vol_slow_gamma` 0.3
    /// and `market_factor_sigma` cut 10 per cent, the leverage sum reads
    /// -0.96 against pt-v20's -0.51, but B5 reads 15.3 against a ceiling of
    /// 12.4 and C10 fails (a rule on the corporate yield's 99.9th-percentile
    /// rise runs ahead in 0.70 of histories), so it is not ready for a
    /// preset while the corporate yield's one-session steps and the
    /// roster's concentration supply most of the index's -5 per cent days.
    /// In [0, 50].
    /// pt-v21 ships 2.5.
    pub market_vol_leverage: f64,
    /// Half-life of the return memory, in sessions. Unread at
    /// `market_vol_leverage` 0.0; must be positive off it. In [0, 2520].
    /// pt-v21 ships 15.
    pub market_vol_leverage_half_life: f64,
    /// How much of an UP day the return memory ignores. 0.0 counts up and
    /// down days alike, so the memory is linear in the return path; 1.0
    /// counts falls only, and the memory is re-centred on the mean that
    /// asymmetry adds at the variance the day was drawn at. Unread at
    /// `market_vol_leverage` 0.0. In [0, 1].
    pub market_vol_leverage_down: f64,
    /// The unit the return memory counts a day in, as a power `s` on the day's
    /// own sd: `u = -F / (sigma_b^(1-s) * sqrt(v)^s)`, `v` the variance the day
    /// was drawn at. 0.0, which every preset through pt-v20 carries, is the
    /// baseline sd `sigma_b`, the form that stood; 1.0 is the day's z-score.
    /// Unread at `market_vol_leverage` 0.0. In [0, 1].
    ///
    /// Why. In baseline units a day drawn at twice the baseline sd moves the
    /// memory twice as far, so the memory's spread -- and the multiplier's
    /// spread `exp(k l)` -- grows with the variance it drives: a fall in a
    /// storm raises the next session's variance by more than the same
    /// z-score in a calm, which is a crash amplifier rather than a leverage
    /// effect. Measured on pt-v20 (box cvsg1) that form bought its leverage
    /// sum with B5 (15.3 against 12.4) and needed `market_factor_sigma` cut
    /// by a tenth to hold the level, because `E[u^2] = E[v / b] > 1` puts the
    /// multiplier's mean over one. Counted in z-scores the memory's
    /// stationary variance is `s^2` at every level, so the multiplier's mean
    /// is one without a level cut and the response to a fall is the
    /// response to its surprise, which is the tape's shape: the log VIX's
    /// response to a return scales with the return over the prevailing
    /// volatility (Bouchaud, Matacz and Potters 2001 normalise the index
    /// kernel the same way).
    /// pt-v21 ships 1.0.
    pub market_vol_leverage_standardise: f64,
    /// Degrees of freedom of the day's market draw: each session's
    /// market-factor variance is multiplied by `m = (nu - 2) / X`, `X` a
    /// chi-square with `nu` degrees of freedom drawn once at the open, so
    /// the day's market factor is a Student t with `nu` degrees of freedom
    /// and the variance the state set (a GARCH-t innovation). 0.0, which
    /// every preset carries, is the normal day that stood: no draw, no
    /// state. `E[m] = 1`, so the mean variance, the VIX's read of it and
    /// the index volatility level do not move; what moves is the day's
    /// tail. The multiplier scales the night's market draw and every tick's
    /// alike, so the whole day is one t draw, and the day's variance
    /// `m * v` is held under the state's ceiling (`market_vol_ceiling_multiple`
    /// baselines), never below `v`. Drawn on `stream::OVERNIGHT` after the
    /// night's normals (a gamma by Marsaglia and Tsang, so `nu` need not be
    /// an integer); the snapshot and the state hash carry `m` only between
    /// an open that drew one and the close. 0 or in [3, 200].
    ///
    /// Why. The certification's tail row (`index_tail_dn3_pct`, sessions at
    /// or below -3 per cent) read 0.52 on R16A's thirteenth grade against
    /// [0.64, 2.34]; on held-out seeds R16A reads 0.81 on the varying
    /// rosters (60 seeds) against a tape centre of 1.21 (1990-2025) and
    /// 1.49 (1928-2025), with half the seeds at zero. The index's own
    /// one-year excess kurtosis reads a median of 0.9 against the tape's
    /// 1.46: at a given variance the model's day is Gaussian, so a -3 per
    /// cent day needs a storm. A GJR-GARCH(1,1) with Student-t
    /// innovations fitted by maximum likelihood to the S&P 500's daily log
    /// returns 1990-2025 (^GSPC closes; the same fit with normal
    /// innovations gives `market_vol_alpha`, `market_vol_beta` and
    /// `market_vol_gamma` to four places) puts the degrees of freedom at
    /// 6.9, a profile-likelihood 95 per cent interval of 6.0 to 8.0; the
    /// normal fit's standardised residuals have an excess kurtosis of 2.05
    /// and 1.38 per cent of days below -2.5 against a normal's 0.62.
    ///
    /// Measured on R16A (sim/r17-d1tail, the certification's varying-roster
    /// protocol, 720 held-out seeds, 201-230, 501-530, 801-830, 2001-2270
    /// and each plus 20000): at 7, with `market_day_tail_state_share` 1, the
    /// one-year index kurtosis went from 0.8 to 1.6 and the share of seeds
    /// with no -3 per cent session from 0.66 to 0.58, but the tail row only
    /// from 0.64 to 0.76; at 4, 5 and 6 no higher. On the varying rosters a
    /// -3 per cent day is near three of the index's sigmas, about where a t
    /// and a normal of one variance cross. Not recommended on R16A for that
    /// row; it stays inert.
    pub market_day_tail_df: f64,
    /// The share of the day's t scale the market variance state reads: the
    /// state is fed the day factor times `m^(-(1 - this) / 2)`. 0.0, which
    /// every preset carries, feeds it the day as if drawn at the state's
    /// own variance, so a fat-tailed day moves the next day's variance, the
    /// VIX's target and the return memory no more than a normal one; 1.0
    /// feeds it the day as it landed, the GARCH-t recursion, where a large
    /// day raises the next day's variance by its size. Read only with
    /// `market_day_tail_df` set. In [0, 1].
    pub market_day_tail_state_share: f64,

    /// How far the common factor's shock share moves with the factor's own
    /// variance excursion. 0.0, which every shipped preset carries, is a
    /// constant share, and at 0.0 the arithmetic is bit-identical to the
    /// form without this dial.
    ///
    /// # What the tape says
    ///
    /// Measured on the index, 19,014 sessions 1950-2025. Regress
    /// `|r_t|` on `|r_{t-1}|`, the standardized log of trailing realized
    /// volatility over `w` sessions ending at `t-1`, and their product.
    /// The product's coefficient is how much clustering moves with the
    /// state, and the tape reads:
    ///
    /// ```text
    ///   w =   5 sessions   +0.0941 +/- 0.0047      (20 error bars)
    ///   w =  21 sessions   +0.0694 +/- 0.0042
    ///   w =  63 sessions   +0.0474 +/- 0.0047
    ///   w = 252 sessions   +0.0187 +/- 0.0059
    /// ```
    ///
    /// The response is strongest at a week and decays as the window
    /// lengthens, so it is a days-to-weeks mechanism and not a regime. The
    /// tape's baseline clustering at the weekly window is negative
    /// (-0.058): almost all of the real market's volatility clustering is
    /// conditional on the last week having been rough, and a constant shock
    /// share cannot express that.
    ///
    /// The model without this dial has about a fifth of the response
    /// (+0.0158 +/- 0.0064 at w = 5 on pt-v19's vector, +0.0483 on pt-v18)
    /// and the wrong sign at a quarter and a year.
    ///
    /// # The form
    ///
    /// `delta = excursion * ln(variance / target)`, then
    /// `alpha_t = alpha + delta` and `beta_t = beta - delta`. The pair
    /// moves together, so `alpha + beta` is invariant and with it the
    /// persistence, the unconditional level and omega's mean reversion.
    /// What changes is the split between yesterday's shock and the carried
    /// state, which is what clustering is.
    ///
    /// Raising alpha alone runs into the fourth moment. The GJR coefficient
    /// `3a^2 + 3ag + 1.5g^2 + 2ab + bg + b^2` reads 0.9908 at the pt-v18
    /// triple, and raising alpha alone crosses one at 0.0120, half a
    /// thousandth of headroom. Rotating gives six times as much (`delta`
    /// to 0.020 at 0.9984), because the fourth moment limits total
    /// persistence and the rotation adds none.
    ///
    /// `delta` is clamped at the value that holds that coefficient at
    /// 0.999, computed from `beta` and `gamma`, so a preset that moves
    /// either keeps a finite fourth moment.
    pub market_vol_alpha_excursion: f64,

    /// Session-to-session persistence of a slow multiplier on the market
    /// factor's variance target, in (0, 1). With `market_vol_level_sigma`
    /// it makes a lognormal AR(1) level that drifts over months; 0.0, which
    /// every shipped preset carries, turns it off.
    ///
    /// # Why a level and not a third component
    ///
    /// Every way of buying fat tails by adding variance-of-variance to the
    /// recursion (a heavier `alpha`, a longer slow pole, a third component,
    /// a random `gamma`) is a random-coefficient recursion. Those are the
    /// entries of the fourth-moment operator `T`, whose spectral radius has
    /// to stay under one, and at the pt-v18 triple `rho(T)` already reads
    /// 0.9841.
    ///
    /// A term that moves only `omega` does not appear in `T` at all.
    /// Scaling the target scales `omega_i = (1 - pers_i) * target` and
    /// leaves every `a_i, b_i, g_i` untouched, so the combined condition
    /// becomes `rho(T) < 1` AND `E[L^2] < infinity`, and the second holds
    /// for every stationary lognormal at every `phi < 1` and every `sigma`.
    /// The two conditions separate, so the level's tail costs nothing
    /// against the moment condition.
    ///
    /// # The derived value
    ///
    /// 0.9977 (0.9945 to 0.9992), a half-life of 295 sessions (126 to
    /// 866), derived from two measured statistics of ^GSPC: the window
    /// log-variance dispersion the model does not account for, and its
    /// window-to-window autocorrelation, solved through the closed-form
    /// autocorrelation of the session means of an AR(1). The two
    /// 252-session tape windows (1990-2025 and 1950-2026) agree at 0.6 of
    /// their own error.
    ///
    /// The estimator is the weak part. The 504-session window's
    /// autocorrelation is the weakest of the four inputs and pulls the
    /// 504 solve down to 0.995. A better estimate would fit the log
    /// realized-variance autocorrelation over a range of lags.
    ///
    /// At 0.0, with `market_vol_level_sigma` 0.0, the multiplier is exactly
    /// 1.0 and the run is bit-identical to one without this term.
    pub market_vol_level_persistence: f64,

    /// Per-session innovation of the slow variance level, in log units.
    /// 0.0, which every shipped preset carries, pins the level at exactly
    /// 1.0 and leaves the target arithmetic untouched, to the bit.
    ///
    /// The value derived from the tape is 0.047 (0.035 to 0.064), a
    /// stationary `sd(log L)` of 0.69 (0.56 to 0.87): the dispersion of
    /// window log-variance the tape has and the model does not,
    /// `sqrt(tape^2 - model^2)`, carried through the AR(1) shrinkage factor
    /// at the persistence above. The range is the propagated jackknife
    /// error on the two tape sds. That derivation sets the level's
    /// window-mean dispersion equal to the index's deficit, but the level
    /// drives the factor, which is about half the index, so 0.047 is low by
    /// about a factor of two. Read off the engine's own output, 0.085 puts
    /// sd(log var) inside the tape's band at both horizons.
    ///
    /// # What it costs
    ///
    /// 1. **The level would fall if `E[L]` were normalized.** At 0.085,
    ///    with `market_vol_level_persistence` 0.9977, the stationary
    ///    `sd(log L)` is `0.085 / sqrt(1 - 0.9977^2)` = 1.254, so with
    ///    `E[L] = 1` the mean root `E[sqrt(L)]` is `exp(-sd^2/8)` = 0.822
    ///    and median annualized volatility would drop 18 per cent, on a
    ///    graded row (`annualised_vol_pct`). At the derived 0.047 the same
    ///    sums give `sd(log L)` 0.69, `E[sqrt(L)]` 0.942 and a 6 per cent
    ///    drop. So the normalization is `E[sqrt(L)] = 1`, which subtracts
    ///    `sd^2/4` from the log level. It is a constant of the
    ///    construction, applied at whatever sigma the preset carries.
    /// 2. **A draw.** One normal per session, on
    ///    [`crate::rng::stream::MARKET_VOL_LEVEL`] and not on `MARKET`, so
    ///    the schedule does not move and nothing else reshuffles. See that
    ///    constant for why the isolation makes the zero setting a control
    ///    and not a different random world.
    /// 3. **The VIX loop can amplify it, by an unmeasured factor.** With
    ///    `market_vol_vix_excursion` at 1.0 the target reads the VIX's
    ///    excursion from the index's own implied level, and a slow level
    ///    moves that level. As a bound: the standalone factor's window
    ///    dispersion is 0.249 and the engine's index reads 0.225, so the
    ///    loop plus the 45 per cent non-factor share transmits at about
    ///    0.9. If that ratio holds, the derived value is right to within its
    ///    own range; if the loop amplifies, it is high. Treat the derived
    ///    pair as a starting point for a search and not a finished
    ///    calibration.
    ///
    /// # Expected effect
    ///
    /// The derivation predicts `index_tail_dn3_pct` at 252 moving from 0.608
    /// to 0.86 to 1.03 against a tape center of 1.213, with
    /// `excess_kurtosis` unmoved, because a per-window level shift
    /// multiplies every name equally and the pooled standardized statistic
    /// is invariant to it by construction. If the tail row does not reach
    /// 0.80 at the derived pair, the mechanism is wrong, and more of the
    /// same dial will not fix it.
    pub market_vol_level_sigma: f64,

    /// Session-to-session persistence of the VIX's own slow log-level, a
    /// lognormal AR(1) multiplier on the VIX target under
    /// `vix_level_identity`. 0.0 on pt-v1 through pt-v18; pt-v19 and
    /// pt-v20 ship 0.9979.
    ///
    /// # Why it exists
    ///
    /// The real VIX's persistence rises from one-year to two-year windows
    /// (`facts.REAL_VIX_AR1_RISE`): two thirds of its variance is a slow
    /// regime level with a half-life near two hundred sessions, and a
    /// single-pole process cannot show that on the realism gate's
    /// estimator. A slow level on the factor's variance target
    /// (`market_vol_level_sigma`) moves every row that reads the variance
    /// process. This one multiplies the VIX target alone, the read-back
    /// `vix_implied_from_market` at the one place the engine wires it, so
    /// the factor's variance and the fourteen shape rows it drives are
    /// untouched at first order, and the VIX loop sees the level only
    /// through the coupling every preset already carries.
    ///
    /// # Derivation
    ///
    /// A two-pole fit to the ACF of log VIX over 1990-2025 at lags 1 to 504
    /// reads a fast pole of 0.942 carrying 21 per cent of the variance and a
    /// slow pole of 0.9965 (half-life 198 sessions) carrying 79 per cent:
    /// stationary sd 0.306 in logs, innovation 0.0256 per session. Those are
    /// the tape-derived values. The shipped pair is 0.9979 and 0.0181.
    ///
    /// At 0.0, with `vix_level_sigma` 0.0, the multiplier is exactly 1.0
    /// and the run is bit-identical. Read only while `vix_level_sigma` is
    /// nonzero.
    pub vix_level_persistence: f64,

    /// Per-session innovation of the VIX's own slow log-level, in log units.
    /// 0.0 on pt-v1 through pt-v18; pt-v19 and pt-v20 ship 0.0181.
    ///
    /// At 0.0 the log-level stays exactly 0.0, the multiplier is exactly 1.0
    /// by a branch, and no draw is added or moved. The normal it would
    /// consume is the one the factor level already takes unconditionally on
    /// its own stream, so a preset carrying both levels drives them with
    /// one draw. The tape derivation gives 0.0256 at persistence 0.9965
    /// (see `vix_level_persistence`). Read only under
    /// `vix_level_identity`, where the VIX target is what the index's own
    /// conditional variance implies. Off the identity the target is the
    /// phase table and this multiplier is not applied.
    /// pt-v21 ships 0.009.
    pub vix_level_sigma: f64,

    /// The VIX loop's own gain on the slow VIX level, which the level's
    /// innovation is divided by. 0.0, which pt-v1 through pt-v18 carry,
    /// skips the division and those presets reproduce bit for bit; pt-v19
    /// and pt-v20 ship 1.79.
    ///
    /// # The problem it corrects
    ///
    /// `vix_level_sigma` is the spread of the tape's yearly medians of log
    /// VIX written onto the latent multiplier, and the two are different
    /// quantities. Under `vix_level_identity` the VIX's target is the
    /// read-back of the index's own conditional variance times this level,
    /// and the read-back answers the VIX back: a slightly higher level
    /// raises the factor's variance target, which raises the read-back,
    /// which raises the VIX again. So the spread the model's VIX shows is
    /// the level's spread times the loop's gain, and a spread measured on
    /// the output is being applied to the input. This is why every gain in
    /// the variance loop lengthens the VIX's memory: at
    /// `market_vol_vix_exponent` 4.9 the same level arrives at the VIX half
    /// again as large, the slow share of the VIX's variance grows with it,
    /// and `vix_ar1_debiased` rises at both horizons.
    ///
    /// # The form
    ///
    /// Hold the VIX and the excursion target's fixed point makes the
    /// read-back a power of it, `I = A * VIX^h`, which is what a held-VIX
    /// run measures. Let the loop run and the level multiplies:
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
    /// the stationary opening too, because the two are one dispersion and
    /// the opening draw is the level's own stationary distribution.
    ///
    /// # Derivation
    ///
    /// `h` is read off a pair of held-VIX runs, VIX 5 against VIX 65: the
    /// read-back `vix_implied_from_market` rises 3.00x at exponent 2.0 and
    /// 4.60x at 4.9, over a VIX that rises 13x, so `h` is
    /// `ln 3.00 / ln 13` = 0.428 and `ln 4.60 / ln 13` = 0.595. The gain is
    /// then 1.75 at exponent 2.0 and 2.47 at 4.9. Nothing was fitted to a
    /// graded row: each is a ratio of two readings, and the form above is
    /// the loop's own algebra.
    ///
    /// A gain must be strictly positive: it divides a dispersion, and the
    /// loop's is `1 / (1 - h)` with `h` in `[0, 1)`, so it is never below
    /// one. `ModelParams::invariants` refuses a non-positive value and
    /// refuses the dial while `vix_level_sigma` is 0.0, where there is no
    /// level for it to correct.
    ///
    /// # What it moves, measured on 16 seeds of a fixed roster
    ///
    /// On a base vector with `market_vol_vix_exponent` 2.0,
    /// `vix_ar1_debiased` reads 0.9523 at 252 and 0.9637 at 504. At
    /// exponent 4.9, which is what the crisis lever asks for, it reads
    /// 0.9581 and 0.9752, and the 504 reading is refused. With the gain
    /// divided out at 4.9 it reads 0.9492 and 0.9592, under the base at
    /// both horizons, with a rise of +0.0100 against the base's +0.0114,
    /// and the lever is untouched: the read-back still rises 4.60x and
    /// realized index volatility 4.85x for a VIX of 5 against 65.
    ///
    /// The level carries the exponent's whole cost, and that can be
    /// checked on its own. With `vix_level_sigma` at 0.0 the same exponent
    /// change reads 0.9451 and 0.9581 at 2.0 against 0.9413 and 0.9569 at
    /// 4.9: with no regime level in the loop, the loop's own gain does not
    /// lengthen the VIX's lag-one memory at all.
    pub vix_level_loop_gain: f64,

    /// Per-session rate at which the VIX reverts toward the identity's
    /// anchor, as a share of the distance in `[0, 1)`. 0.0, which every
    /// shipped preset carries, skips the term and is bit-identical.
    ///
    /// # The problem it addresses
    ///
    /// Under [`ModelParams::vix_level_identity`] the VIX reverts to the
    /// read-back of the index's own conditional variance, and the variance
    /// reverts to a target that reads the VIX. There is no third thing.
    /// Nothing in the pair reverts to a level: the loop's only anchor is
    /// that its static gain is under one, so the closer that gain is to one
    /// the longer the pair wanders and the larger every standing bias
    /// becomes at the level. On the tape the VIX does sit near a regime
    /// mean over months, and the model supplies that from outside, as a
    /// driven regime level (`vix_level_sigma`), because the loop itself
    /// cannot.
    ///
    /// That is why the crisis lever and the VIX's memory trade against one
    /// another. The excursion form (`market_vol_vix_excursion` 1.0) buys
    /// stability by making the factor's target fall as the read-back rises,
    /// and the same mechanism caps the held-VIX response: at
    /// `market_vol_vix_exponent` e the held-VIX fixed point is
    /// `v ~ VIX^(e / (1 + e/2))`, so 4.9 gives 1.42 where the tape's index
    /// variance rises as VIX^1.83, and 1.83 would need e = 21.5. Damping
    /// the free loop this way also shuts the lever.
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
    /// `vix_mean_reversion` and to its regime level at this rate, and the
    /// pair has an anchor that is not itself a function of the VIX. With
    /// the anchor in place the variance target can read the VIX's level
    /// with the tape's own exponent (`market_vol_vix_excursion` 0.0,
    /// `market_vol_vix_exponent` 1.83) without the loop becoming a random
    /// walk.
    ///
    /// # Derivation
    ///
    /// Linearize the closed loop in logs about its fixed point, writing
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
    /// `rho` is the factor mixture's effective persistence, the weighted
    /// average of the two components' own poles: the fast component's GJR
    /// triple gives `alpha + beta + gamma / 2` = 0.979 and the slow
    /// component's `market_vol_slow_persistence` is 0.9913 at
    /// `market_vol_slow_weight` 0.35, so
    /// `0.65 * 0.979 + 0.35 * 0.9913` = 0.98331. The weighted average is
    /// the right combination because both components are fed by the same
    /// innovation and both revert toward the same VIX-coupled target
    /// (`factor_vol.rs`), so the mixture's response to a step in that
    /// target is the weighted sum of two exponentials and 0.98331 is its
    /// one-pole stand-in.
    ///
    /// The characteristic equation is linear in `kappa`, so a target pole
    /// inverts in closed form:
    ///
    /// ```text
    /// kappa = [ -l^2 + l (rho + 1 - mr) - rho (1 - mr) + (1 - rho) p mr / 2 ]
    ///         / (l - rho)
    /// ```
    ///
    /// This gives 0.047 at `l` = 0.9965, the tape's own slow pole of log VIX
    /// (the two-pole fit: fast 0.942 with 21 per cent of the variance, slow
    /// 0.9965 with 79), with `p` = 1.83 and `mr` = 0.27. At `kappa` 0 the
    /// same algebra puts the loop's slow pole at 0.9987, slower than the
    /// tape's, and the dial is the amount of anchor that brings it back.
    /// The slow pole then comes from the loop and not from a driven regime
    /// level.
    ///
    /// # Invariants
    ///
    /// `kappa` lives in `[0, 1)`: it is a share of the distance to the
    /// anchor taken in one session, and at one or above the step
    /// overshoots the anchor every day. `ModelParams::invariants` refuses
    /// it with `vix_level_identity` at 0.0, the way `vix_level_sigma` is
    /// refused. Off the identity there is no derived anchor for the VIX to
    /// revert to: `derive_vix_anchor` returns the dial
    /// `market_vol_vix_anchor` and the VIX's target is the phase table, so
    /// the reversion would pull one scale toward another.
    ///
    /// # What it moves, measured on 16 seeds of a fixed roster
    ///
    /// The arm is `market_vol_vix_excursion` 0.0, `market_vol_vix_exponent`
    /// 1.83 and this dial at 0.046081, against a base on the excursion form
    /// at exponent 4.9. The regime level is on at the base's values (a), on
    /// at the gain the same algebra re-derives on this form, 2.3552 (b),
    /// and off (c):
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
    /// The level law misses the crisis lever, and the cause is upstream of
    /// this dial. The exponent 1.83 assumed that, with the excursion
    /// damping removed, the target's held-VIX exponent passes through to
    /// the variance one for one. It does not: the transmission measured on
    /// this form is about 0.8 from the target's own effective exponent to
    /// the factor variance's, and about 0.9 again from there to the
    /// index's, so p = 1.83 gives an index lever of 4.61x where 8x to 11x
    /// was expected, and where the excursion form at exponent 4.9 already
    /// reads 4.90x. The exponent ladder on this form, with the anchor
    /// reversion on, measures the transmission directly: p = 1.83 gives
    /// 4.61x, p = 3.0 gives 7.89x and p = 4.9 gives 11.27x, so the tape's
    /// 10.5x sits near p = 4.5. For that reason the dial is 0.0 on every
    /// shipped preset.
    ///
    /// The dial itself does what the algebra says. The free VIX's log ACF
    /// at lags 21 and 63 rises from 0.33 / 0.01 to 0.53 / 0.11 toward the
    /// tape's 0.80 / 0.63, which is the loop's own slow pole arriving, and
    /// it does so with `vix_ar1_debiased` unmoved: within 0.001 of the base
    /// at 252 and within 0.003 at 504, on the level arm at the re-derived
    /// gain and on the level-off arm alike. No seed's VIX reaches
    /// `vix_ceiling` on any arm. One graded row leaves its band on the level
    /// law, and it is the same row on all three level arms:
    /// `index_tail_dn3_pct` 0.398 against 0.64 to 2.34 at both horizons,
    /// where the base reads 0.797 and 0.696. A three-sigma index day is half
    /// as likely under a target that reads the VIX's level, because the
    /// level moves slowly and the excursion form's target does not.
    pub vix_anchor_reversion: f64,

    /// Share of the VIX's target taken by the identity's anchor, as a
    /// geometric weight in `[0, 1)`. 0.0, which pt-v1 through pt-v18 carry,
    /// makes the target the read-back exactly, bit for bit; pt-v19 and
    /// pt-v20 ship 0.375.
    ///
    /// The shipped 0.375 is the calm weight the tape's
    /// VIX-to-realized-volatility elasticity gives through
    /// `theta = (1 - a) k`, which is a different identity from the
    /// slow-pole weight derived below.
    ///
    /// This does the job of [`ModelParams::vix_anchor_reversion`] in a
    /// different place. That dial adds a second reversion rate beside
    /// `vix_mean_reversion`, so the VIX reverts `mr + kappa` of the way each
    /// session and its lag-one persistence collapses with the loop's fast
    /// pole. This one leaves the rate at `mr` and moves the target instead:
    ///
    /// ```text
    /// target = implied^(1 - a) * (L * anchor)^a
    /// ```
    ///
    /// Linearized, `y' = (1 - mr) y + mr (1 - a) f / 2`. The static gain is
    /// `p (1 - a) / 2`, the same as the rate form's at `1 - a = w`, and the
    /// trace of the loop matrix is `rho + 1 - mr` whatever `a` is. So pinning
    /// the slow pole pins the fast pole too: at the tape's 0.9965 the fast
    /// pole is 0.717 at every exponent, where the rate form's runs from 0.671
    /// down to 0.146. Solved in closed form from the characteristic
    /// equation, `1 - a = (rho - l)(1 - mr - l) / ((1 - rho) p mr / 2)`
    /// gives 0.6099 at `market_vol_vix_exponent` 4.0. The blend is geometric
    /// because on the anchor form the held read-back rises about as
    /// `VIX^(p / 2)`, so an arithmetic blend keeps a tail that grows faster
    /// than the VIX, and the geometric one grows as `VIX^((1 - a) p / 2)`,
    /// under one at the derived weight.
    pub vix_anchor_weight: f64,

    /// Per-session rate at which the anchor's memory of the read-back
    /// updates. 0.0, which pt-v1 through pt-v18 carry, is the instantaneous
    /// form; pt-v19 and pt-v20 ship 0.0556 (one eighteenth).
    ///
    /// At 0.0 the target is `implied^(1 - a) (L anchor)^a`, today's
    /// read-back against the anchor. Nonzero, the anchor pulls against a
    /// slow memory of the read-back's log deviation instead,
    /// `s' = (1 - h) s + h log(implied / L anchor)`, and the target is
    /// `implied * exp(-a s)`. Today's move in the variance then reaches the
    /// VIX in full, which is the same-day fear response and the range the
    /// instantaneous form damps by `1 - a`, while a deviation that persists
    /// is pulled back at weight `a`. At h = 1 it is the instantaneous form
    /// less one session's lag.
    pub vix_anchor_memory: f64,

    /// Economy steps per year used to compound annual GDP and CPI growth.
    /// 365.0 on pt-v1 through pt-v18; pt-v19 and pt-v20 ship 252.0, one
    /// step per trading session.
    ///
    /// # Why 252
    ///
    /// The economy steps once per trading session, 252 to a year. At 365.0
    /// it compounds `rate / 365` each step, so a trading year receives
    /// 252/365 of its annual growth: nominal output, and with it every
    /// name's earnings and book (`earnings_nominal_growth`), grows about 1.1
    /// points a year too slowly. Measured on 30 seeds x 21 years of pt-v19,
    /// the index's long-run return moves from 3.83 to 4.92 per cent with
    /// only the two divisors changed. The rest of the macro calendar
    /// (30-session months) is set by `macro_calendar_days_per_year`.
    pub macro_compound_days_per_year: f64,

    /// Economy steps per macro year for the rest of the macro calendar:
    /// months, quarters, the seasonal year and central-bank meetings. 365.0
    /// on pt-v1 through pt-v18; pt-v19 and pt-v20 ship 252.0.
    ///
    /// At 365.0 the calendar is 30-step months (`DAYS_PER_MONTH`), 90-step
    /// quarters, a 90-step OPEC interval, a 365-step seasonal year, a
    /// 30-step month on the cycle's phase clock and its per-day hazard, a
    /// 30-step market-return memory, and central-bank meetings every 42 to
    /// 55 steps (21 to 30 in a crisis).
    ///
    /// # Why 252
    ///
    /// The economy steps once per trading session, so at 365.0 a macro
    /// year is 365 sessions, 1.45 trading years, and every business-cycle
    /// phase, release and meeting interval runs 1.45 times slow in market
    /// time. At 252.0, the session clock (taken from the session count, not
    /// fitted), a month is 21 sessions, a quarter 63 and the year 252, and
    /// calendar-day intervals (meetings, the seasonal valley) are scaled by
    /// 252/365 and rounded. A monthly rate stays a monthly rate, and
    /// per-step processes are not touched. Pair it with
    /// `macro_compound_days_per_year` 252.0.
    pub macro_calendar_days_per_year: f64,

    /// Switch for the business-cycle phase table derived from NBER and BEA
    /// data (`economy::state::us_phase_characteristics`). 0.0, which pt-v1
    /// through pt-v18 carry, reads the original table; any other value
    /// reads the US table, as pt-v19 and pt-v20 do (1.0).
    ///
    /// Its durations are months of the macro calendar, so they mean real
    /// months only with `macro_calendar_days_per_year` 252.0. See the
    /// table's own docstring for each number's derivation.
    pub cycle_us_calibration: f64,

    /// Switch for a lift-off branch in the central bank's rate ladder. 0.0,
    /// which pt-v1 through pt-v18 carry, leaves it out; pt-v19 and pt-v20
    /// ship 1.0.
    ///
    /// Without the branch every hike needs inflation at least a point above
    /// target, so after the first recession the rate sits at zero for the
    /// rest of a run. At any nonzero value the bank also hikes 25bp when its
    /// own Taylor rate is 50bp above the policy rate, unemployment is not
    /// more than a point over target, and the cycle is not in contraction
    /// or trough: the ladder's own cut branch mirrored. See
    /// `economy::central_bank`.
    pub fed_liftoff_rule: f64,

    /// Switch that includes buybacks in the earnings behind `market_pe`.
    /// 0.0, which pt-v1 through pt-v18 carry, leaves them out; pt-v19 and
    /// pt-v20 ship 1.0.
    ///
    /// At 0.0 `market_pe` divides price by `eps * nominal` without the
    /// buyback term the valuation applies, so the multiple rises by about
    /// the buyback yield a year (22.6 to 28.0 over 21 years, measured on a
    /// pt-v19 vector with the switch off) and slowly raises the expansion
    /// hazard, which adds above a multiple of 28. At any nonzero value the
    /// earnings carry `market::tick::buyback_scale`, as the valuation's do.
    pub market_pe_buybacks: f64,

    /// Log offset below the identity's derived anchor that the anchor
    /// weight pulls toward: the blend and the memory's reference use
    /// `L * anchor * exp(-c)`. 0.0, which pt-v1 through pt-v18 carry, makes
    /// the reference `L * anchor` exactly; pt-v19 and pt-v20 ship 0.1515,
    /// ln(1.252 / 1.076).
    ///
    /// The forward map's denominator (`vix_ratio_denominator`) does not
    /// move, so a held VIX drives the same variance it did.
    ///
    /// The anchor is the identity at the unconditional (mean) variance, a
    /// root-mean-square level. The geometric blend pulls log VIX toward it,
    /// so in calm markets it lifts the VIX above its own read-back by
    /// `(L anchor / implied)^a`, 1.19 to 1.26 on the base it was measured
    /// on, where the tape's VIX over realized volatility says the read-back
    /// alone is already right. Pulling to a center below the anchor removes
    /// that lift.
    pub vix_anchor_centre: f64,

    /// How the anchor weight rises with the VIX's level, as an exponent.
    /// 0.0, which pt-v1 through pt-v18 carry, keeps the constant weight
    /// `vix_anchor_weight`; pt-v19 and pt-v20 ship 1.0.
    ///
    /// Nonzero,
    ///
    /// ```text
    /// 1 - a(x) = (1 - a) * (K / clamp(x, K, r K))^eta
    /// ```
    ///
    /// with `x` the VIX entering the session, `K` the knee
    /// (`L anchor exp(-k)`, [`ModelParams::vix_anchor_weight_level_knee`])
    /// and `r` [`ModelParams::vix_anchor_weight_level_cap`]. At and below the
    /// knee the weight is the dial. The loop's local gain is
    /// `(1 - a(x)) g(x)` with `g` the held read-back's elasticity to the VIX.
    /// Measured on the base the law was built on, `g` crosses one at a held
    /// VIX of 18.5 and rises about in proportion to the VIX from there to
    /// about 36, so `eta = 1` holds the local gain at its knee value over
    /// that range. That removes the fear trap at 30 to 50, where a constant
    /// weight of 0.45 leaves the gain at one.
    pub vix_anchor_weight_level: f64,

    /// Multiple of the knee above which the level law stops raising the
    /// anchor weight. 0.0 is no cap; pt-v19 and pt-v20 ship 2.2159.
    ///
    /// The cap sits where the held read-back's elasticity stops rising.
    /// Read only with [`ModelParams::vix_anchor_weight_level`] on.
    pub vix_anchor_weight_level_cap: f64,

    /// Where the level law starts raising the anchor weight, as a log
    /// offset below `L * anchor`. 0.0 puts the knee at `L * anchor`; pt-v19
    /// and pt-v20 ship 0.3888.
    ///
    /// Read only with [`ModelParams::vix_anchor_weight_level`] on.
    pub vix_anchor_weight_level_knee: f64,

    /// Switch that also runs the level law below the knee, where it lowers
    /// the anchor weight toward zero. 0.0, which every shipped preset
    /// carries, holds the weight at `vix_anchor_weight` there.
    ///
    /// Read only with the level law on.
    pub vix_anchor_weight_level_below: f64,

    /// Switch that fixes the level law's knee to the anchor alone, without
    /// the slow regime level: `K = anchor exp(-k)` in place of
    /// `K = L anchor exp(-k)`. 0.0, which every shipped preset carries,
    /// keeps `L` in the knee; it is read only with the level law on.
    ///
    /// # Why
    ///
    /// The knee is derived from the held-VIX map: it is where the
    /// read-back's elasticity `g(x)` to a held VIX reaches `G* / (1 - a)`,
    /// and `g` is a property of the VIX's absolute level (the index
    /// variance's floor and coupling), which the slow regime level `L`
    /// ([`ModelParams::vix_level_sigma`]) does not move. With the knee on
    /// `L anchor`, a turbulent era (`L > 1`) lifts the knee, leaving the band
    /// from `anchor exp(-k)` to `L anchor exp(-k)` at the constant weight,
    /// where `(1 - a) g(x)` exceeds the gain the law holds. That band is
    /// toward the fear trap the law exists to remove, and a calm era lowers
    /// the knee. The center and the read-back keep `L`, since `L` is the
    /// era.
    pub vix_anchor_weight_level_knee_fixed: f64,

    /// Sessions of warm-up given to the market factor's variance components
    /// before session one. 0.0, which every preset through pt-v20 carries,
    /// runs nothing, touches no state and is bit-identical.
    ///
    /// # What it fixes
    ///
    /// [`ModelParams::macro_burn_in_days`] runs `Engine::advance_day`, the
    /// economy path. `Engine::close_market`, where the slow variance level
    /// is drawn and where the factor's two variance components close, is
    /// never called during it. So the level opens from its own stationary
    /// distribution (`market_vol_level_sigma`'s third paragraph) and every
    /// variance state it acts through opens cold, at the unscaled baseline
    /// `market_factor_sigma^2`.
    ///
    /// Measured: cut each 504-session recording at session 252 and the two
    /// halves, both 252-session windows of a level that is stationary from
    /// session one, do not read the same. `sd(log window variance)` across
    /// 120 rosters reads 0.6938 +/- 0.0602 in the first half and
    /// 0.9570 +/- 0.0609 in the second at `market_vol_level_sigma` 0.085,
    /// and the effect is absent at sigma 0 (ratio 1.09, interval 0.90 to
    /// 1.30). The transient lasts 98 to 145 sessions and is flat from
    /// about session 350.
    ///
    /// # What it does, and why it takes no draw
    ///
    /// The level is a lognormal AR(1) and the components are linear in
    /// their target, so the state a fully run-in engine would hold has a
    /// closed conditional mean given the level the run opens on. This
    /// steps each component's mean recursion,
    /// `v <- (1 - p) * target + p * v` with `p = alpha + beta + gamma/2`
    /// (which is `E[v_{t+1} | v_t]` exactly, since `E[f^2] = v` and the
    /// leverage arm fires on half the days), along
    /// `E[log L_{1-k} | log L_1] = phi^k * log L_1`, the level's own
    /// expected backward path.
    ///
    /// So the warm-up is a deterministic function of a draw the close
    /// already takes. It consumes nothing, from any stream, at any
    /// setting: the draw schedule cannot move and no new stream is
    /// declared. See `MarketVarianceState::warm_to_level`.
    ///
    /// The component's state given the opening level still has a
    /// conditional spread (the level's own innovations over the
    /// component's memory, and the GARCH shocks), which a conditional mean
    /// cannot carry. Derived for the level's half of it: its variance is
    /// 0.096 of the component's total for the fast component and 0.207 for
    /// the slow, and because it decays on the component's own memory while
    /// the conditional-mean term does not, a 252-session window average
    /// retains 99.0 per cent of the stationary window statistic's variance
    /// against the cold start's 59.6 (Monte Carlo on the linearised
    /// recursion, 40,000 replications,
    /// `tools/calibration/warmup_probe.py derive`).
    ///
    /// Measured on the engine, that 99 per cent is optimistic and the
    /// recovery is partial. With the warm-up on, the factor's level
    /// envelope over the first quarter reads 0.770 against 0.846
    /// equilibrated (0.91, against a cold engine's 0.50), and the per-name
    /// GARCH and the VIX inherit it within twenty sessions, at 0.98 of
    /// their own equilibria against 0.64 and 0.56 cold. But the window
    /// statistic the calibration reads recovers 46 per cent of its deficit:
    /// the split-half ratio goes from 1.2539 to 1.1197 +/- 0.1818 on 48
    /// rosters. The three readings are each about one standard error apart
    /// and have not been reconciled. Closing the rest of the gap would take
    /// a stochastic warm-up.
    ///
    /// # Why 504
    ///
    /// Derived from the measured envelope: the transient is flat from
    /// session 350 and 504 is the next round number past it. The recursion
    /// converges geometrically at the slow component's persistence, so 504
    /// sessions leave `0.9913^504` = 0.012 of the initial condition, and
    /// anything past about 700 is arithmetically indistinguishable. It is a
    /// session count rather than a switch so that the length can be tested.
    ///
    /// # Two things it does not do
    ///
    /// It does not run at `market_vol_level_sigma` 0.0, for a measured
    /// reason. With no level, the components' target is `base` times the
    /// VIX's excursion from the index's own implied level, and that
    /// excursion averages one, so the constructor's `base` is close to the
    /// right location and the block means show no travel an error bar can
    /// separate from noise (32 seeds, eight 63-session blocks: block one
    /// sits +0.080 in the log above the mean of blocks 3 to 8, about one
    /// standard error, and block eight sits +0.137 above it in the same
    /// direction). What is cold there is the cross-seed dispersion: 0.087
    /// in the log on day one and 0.594 by day 300, 0.63 of equilibrium over
    /// the first quarter. A deterministic warm-up cannot supply a
    /// dispersion, since every seed would get the same number, so running
    /// this at sigma 0 would buy nothing. That would need a warm-up that
    /// draws, which needs its own stream. Keeping the zero-sigma case
    /// untouched also keeps it usable as the control for both a warmed and
    /// an unwarmed ladder.
    ///
    /// The run's first session still trades at the cold sigma, because the
    /// level the components are warmed to is not drawn until that session's
    /// close. That is one session in 504, and the alternative is a draw at
    /// construction.
    pub market_burn_in_sessions: f64,

    /// Cap on the market factor's variance, as a multiple of its calm level.
    /// pt-v1 to pt-v6 ship 8. pt-v7 to pt-v9 ship 16. pt-v10 onward ship
    /// 32.
    ///
    /// It does nothing until the variance reaches it, so raising it above
    /// where it already binds changes little. Measured from 32 to 50 it
    /// moves the crisis lever by under 3% and crisis co-movement not at
    /// all. For scale, a real record VIX of 82.7 against this model's
    /// anchor of 15 is a variance ratio of about 30.
    pub market_vol_ceiling_multiple: f64,
    /// Floor on the market factor's variance, as a multiple of its
    /// calm level. Stops a quiet stretch from compounding into a
    /// market with no shared movement at all.
    pub market_vol_floor_multiple: f64,
    /// How far the market factor's variance target follows the VIX, from
    /// 0.0 (a fixed target) to 1.0 (fully proportional to the VIX response,
    /// the squared VIX ratio at the default `market_vol_vix_exponent`). The
    /// target is the baseline times `1 - c + c * response`.
    /// pt-v21 ships 0.75.
    pub market_vol_vix_coupling: f64,
    /// VIX level at which a coupled target equals the baseline variance.
    pub market_vol_vix_anchor: f64,
    /// Days of EMA smoothing on the VIX that the market variance target reads.
    /// 0, which every preset through pt-v20 carries, reads each day's print
    /// raw, bit for bit.
    ///
    /// Measured on the driven test with the QE channel switched off, all of
    /// the remaining excess comes from the fear response passing day-to-day
    /// VIX churn into the variance target, where real volatility follows
    /// sustained fear. It affects the market factor only. The per-name
    /// GARCH VIX coupling still reads the raw print.
    /// pt-v21 ships 3.
    pub market_vol_vix_smooth: f64,
    /// Exponent on the market variance target's VIX ratio. 2.0 is the
    /// literal square, bit for bit, and is what every preset through pt-v18
    /// carries. pt-v19 and pt-v20 ship 4.0 on the anchor form, applied above
    /// the anchor only (see `market_vol_vix_exponent_below`).
    ///
    /// Measured along real VIX paths, the square is too convex through
    /// mid-VIX. A lower exponent, with the coupling re-fit to hold
    /// T(45)/T(5), flattens the middle and keeps the crisis lever. Below
    /// one, the target at very low VIX can go negative and the variance
    /// floor clamps it, so check the reading at VIX 5 when you set it.
    pub market_vol_vix_exponent: f64,
    /// Exponent on the market variance target's VIX ratio when that ratio is
    /// below one, where the VIX sits under the ratio's denominator (the
    /// derived anchor under the level form, the read-back under the
    /// excursion form). 0.0, which every preset through pt-v18 carries,
    /// uses `market_vol_vix_exponent` on both sides and never branches, bit
    /// for bit. pt-v19 and pt-v20 ship 2.5.
    ///
    /// Nonzero, the response is `r^below` for `r < 1` and
    /// `r^market_vol_vix_exponent` at and above one. It is continuous at the
    /// anchor, so the held-VIX response from the anchor up (the crisis
    /// side) is untouched and only the calm side is reshaped. The tape's
    /// common variance (mean pairwise correlation times a name's variance,
    /// 40-name roster 1990-2025) scales as about VIX^2.1 below the anchor
    /// and steeper above it, and a single exponent of 4.0 fitted for the
    /// crisis lever starves calm markets of shared variance.
    pub market_vol_vix_exponent_below: f64,
    /// Extra transmission of the market factor on a down tick: every name
    /// receives `beta * factor * (1 + this)`. 0.0, which pt-v1 through
    /// pt-v15 carry, is bit-identical. pt-v16 onward ship 0.025.
    ///
    /// It wires in correlation asymmetry directly. Names co-moving harder on
    /// the way down is the exceedance correlation real markets show and the
    /// panel's `corr_asymmetry` statistic measures. It raises down-day
    /// co-movement and some volatility asymmetry with it, so watch the
    /// `leverage_effect` row when you move it.
    pub market_beta_down_asym: f64,
    /// The lagged downside transmission: on the session after a down day,
    /// every name receives `beta * factor * (1 + this)` whatever the tick's
    /// own sign. 0.0, which pt-v1 through pt-v16 carry, is bit-identical.
    /// pt-v18 ships 0.375, and pt-v19 and pt-v20 ship 0.46.
    ///
    /// pt-v18's 0.375 is the best score on a seven-point grid, with nothing
    /// derived behind it. The 0.46 on pt-v19 and pt-v20 is fitted against
    /// the lagged asymmetry row's held-out count, with the condition
    /// sampled live through the session (`market_beta_down_asym_lag_live`
    /// 1.0). The reason for the wire is measured: real down-moves continue
    /// into the next session, and the same-day wire alone cannot express
    /// that.
    pub market_beta_down_asym_lag: f64,
    /// Where the lagged wire's down-day condition is sampled: at the open
    /// (0.0), live through the session (1.0), or with the sign reversed as a
    /// diagnostic (2.0). 0.0, which every preset through pt-v18 carries, is
    /// bit-identical. pt-v19 and pt-v20 ship 1.0.
    ///
    /// It has no number to derive. It changes which state the condition is
    /// read from and leaves the size of the boost alone. At 0.0 the branch
    /// returns `inputs.prev_day_down` unchanged.
    ///
    /// # The three values
    ///
    /// - 0.0, at the open. The condition is `prev_day_factor < 0`, sampled
    ///   once at the open and held for the whole session. Exact at the bell
    ///   and a day stale by the close.
    /// - 1.0, live. At tick `k` of 390 the condition is
    ///   `prev_day_factor * (1 - k/390) + day_factor_so_far < 0`, which is
    ///   `E[sum of the last 390 tick factors | prev_day_factor, day_factor]`
    ///   under exchangeable within-day draws: yesterday's remaining tail
    ///   decays linearly across the session while today's own running sum
    ///   takes over. At `k = 0` it is `prev_day_down`, and at `k = 390` it
    ///   is today's own sign.
    /// - 2.0, the sign control. The same quantity with the comparison
    ///   reversed (`c_k > 0`). A diagnostic setting only, never one to ship:
    ///   a live sample that moves `corr_asymmetry` the same way under both
    ///   signs is adding variance instead of re-timing a signed response.
    ///
    /// # Why this injects no first moment
    ///
    /// The multiplier `m_t = 1 + lag * 1{c_t < 0}` is a function of draws
    /// strictly before tick `t`. `day_factor_so_far` is the accumulator as
    /// it stands when the tick is called, and `engine.rs` adds this tick's
    /// factor only after `simulate_market_tick` returns. The tick's own
    /// factor `f_t` is an independent zero-mean draw, so
    /// `E[m_t f_t] = E[m_t] E[f_t] = 0` tick by tick and the day's
    /// delivered factor has mean zero. Compare `market_beta_down_asym`,
    /// which scales the draw whose own sign it branches on and therefore
    /// needs `market_beta_down_asym_recentre` beside it. There is no
    /// recentring dial here and no `return_acf1` channel:
    /// `E[F_d F_{d+1}] = 0` by the same independence. The 0.0 setting has
    /// this property too.
    ///
    /// # No draw
    ///
    /// A branch on state the snapshot already holds
    /// (`MarketVarianceState::prev_day_factor` and `::day_factor`). The
    /// draw schedule is a pure function of market status, active set and
    /// sector count and this touches none of them, so no stream is added
    /// and no restore offset moves.
    pub market_beta_down_asym_lag_live: f64,
    /// How much of the mean that `market_beta_down_asym` injects is given
    /// back, from 0.0 (none) to 1.0 (all of it). 0.0, which every preset
    /// before pt-v18 carries, is bit-identical. pt-v18 onward ship 1.0.
    ///
    /// # Why the tilt injects a first moment at all
    ///
    /// It scales one side of a zero-mean draw. Scaling only the down ticks
    /// of a symmetric factor moves its mean, and a mean in the price
    /// process is a drift: `E[f 1{f<0}]` for `f ~ N(0, s^2)` is
    /// `-s / sqrt(2 pi)`, so the tilt adds `a * beta * -s / sqrt(2 pi)` to
    /// every name every tick. Nobody chose that. It is a by-product of a
    /// correlation mechanism, and it cost the equal-weight index 7.9
    /// percentage points a year at pt-v16.
    ///
    /// # What is given back, exactly
    ///
    /// `this * a * beta * s / sqrt(2 pi)`, where `s` is the conditional
    /// per-tick sigma the factor was actually drawn with (and not
    /// `market_factor_sigma`). Every quantity in it is known exactly at the
    /// point it is applied, so the correction is arithmetic, not an
    /// estimate, and it cannot be defeated by the factor's variance
    /// process, its VIX coupling or a scenario that pins VIX: a hotter
    /// tick injects more and gives back more, in the same ratio.
    ///
    /// `beta` is per name because the injection into name `i` is `beta_i`
    /// times the form, so the correction has to carry it too. Using 1.0
    /// instead would leave a residual proportional to `beta_i - 1`, which
    /// is a cross-sectional bias as well as a mean one.
    ///
    /// # What is not given back
    ///
    /// The crash amplifier. It multiplies the market channel above a
    /// threshold in baseline sigmas, and it is exactly the tail the tilt
    /// scales, so the true injected mean is the form above times
    /// `E[f 1{f<0} A] / E[f 1{f<0}]`. That ratio has no closed form. It
    /// was measured at 1.00 to 1.38 across conditional sigmas and sits
    /// near 1.01 at the sigmas that occur, so correcting it would mean
    /// either a new bit-pinned transcendental or a fitted constant. The
    /// residual is therefore known, signed and about one per cent of the
    /// term, and it is left as it is.
    ///
    /// The offset is applied after the amplifier for the same reason. An
    /// offset added before it would itself be amplified, delivering the
    /// form times `E[A]`.
    pub market_beta_down_asym_recentre: f64,
    /// How much of the extra mean that the lagged down-day wire adds to the
    /// tilt is given back, from 0.0 (none) to 1.0 (all of it). 0.0, which
    /// every preset through pt-v20 carries, is bit-identical.
    ///
    /// `market_beta_down_asym_recentre` gives back the tilt's mean at a
    /// lag multiplier of one. On a session after a down day
    /// (`market_beta_down_asym_lag`) the whole transmission, the tilt
    /// included, is multiplied by `1 + lag`, so the tilt's mean is
    /// `(1 + lag)` times the form and `lag * a * beta * s / sqrt(2 pi)` of
    /// it is left in every name every tick of that session. At pt-v20's
    /// 0.025 and 0.46 that is about -8 per cent a year of the cap-weighted
    /// market input (measured: -11.3 per cent a year with the wire, -3.5
    /// with it off, se 3 to 4, seeds 101-104, 756 sessions). While every
    /// market shock sits in `s` the pull turns it into a constant discount
    /// of about 2 per cent and it costs no drift. Under
    /// `fair_value_market_share` it accumulates in the fair-value level and
    /// is a drift (measured on a grid: the index's long-run return is 5.6,
    /// 1.3 and -3.1 per cent at shares 0, 0.5 and 1).
    ///
    /// Off zero, on a lagged session the recentring offset is multiplied by
    /// `1 + this * lag`, so at 1.0 it gives back the lagged tilt's mean
    /// exactly as `market_beta_down_asym_recentre` gives back the unlagged
    /// one, after the amplifier and with the same one per cent left. Read
    /// only when `market_beta_down_asym_recentre`, `market_beta_down_asym`
    /// and `market_beta_down_asym_lag` are all non-zero. Range 0 to 1.
    pub market_beta_down_asym_lag_recentre: f64,

    /// Shrinks a name's idiosyncratic shock on a down tick of the market
    /// factor and inflates it on an up tick, so same-day down-market
    /// co-movement rises while the unconditional variance is held exactly.
    /// 0.0, which every shipped preset carries, is bit-identical by branch,
    /// the way every wire in `factors.rs` is.
    ///
    /// # The tape measurement this exists for
    ///
    /// Measured on pt-v19: `corr_asymmetry` at 504 sessions is the largest
    /// single row of the nineteen-row whole-tape score (3.29 of 9.19), and
    /// the miss is in where the co-movement lands, with the level about
    /// right. The row and its lagged partner sum to 0.1195 to 0.1590 across
    /// all thirty measured variant-horizon cells, against a tape of 0.1291
    /// at 252 and 0.1377 at 504, so the model has as much down-conditioned
    /// co-movement as the tape. The tape puts 47 per cent of it on the same
    /// day at 252 and 60 per cent at 504, and every pt-v19 variant measured
    /// puts 8 to 15 per cent there. No existing dial reaches it: paired over
    /// 120 rosters and 22 variants, the largest move any pt-v19 dial
    /// produces at 504 is +0.0026 +/- 0.0012, three per cent of a 0.065 gap.
    ///
    /// # Why the existing tilt cannot do it
    ///
    /// `market_beta_down_asym` is the same-day wire, and its best value on
    /// the nineteen-row score, on slopes measured on the pt-v19 base, is
    /// the shipped 0.025 at both horizons. The reason is that it
    /// multiplies. Scaling the factor leg by `(1 + a)` on a down tick
    /// raises the name's conditional variance by
    /// `beta^2 s_f^2 ((1 + a)^2 - 1)`, so the factor's share (which is what
    /// a pairwise correlation is) rises only as
    /// `q (1+a)^2 / (1 + q ((1+a)^2 - 1))` and falls short of `q (1+a)^2`,
    /// while the whole of the excess variance lands on
    /// `annualised_vol_pct`, `excess_kurtosis` and the tails. The measured
    /// bill on the pt-v19 base is +9.5 points of annualized volatility and
    /// +0.27 of `return_acf1` per unit against +0.199 of the row, and
    /// `return_acf1` has a tape error of 0.0106.
    ///
    /// # The form
    ///
    /// The market leg is left exactly alone and the idiosyncratic shock is
    /// scaled instead:
    ///
    /// ```text
    /// down tick (market_factor < 0):  e -> e * (1 - c)
    /// up tick   (market_factor >= 0): e -> e * sqrt(2 - (1 - c)^2)
    /// ```
    ///
    /// Nothing is added anywhere. The factor's share is raised where the
    /// statistic looks and lowered where it does not. Derived, with `q` the
    /// unconditional pairwise correlation (`cross_sectional_corr` reads
    /// 0.3219 on pt-v19 at 504), the tick-level conditional correlations
    /// are
    ///
    /// ```text
    /// q_down = q / (q + (1 - q) (1 - c)^2)
    /// q_up   = q / (q + (1 - q) (2 - (1 - c)^2))
    /// ```
    ///
    /// These are 0.394 and 0.269 at `c` = 0.15, a tick-level difference of
    /// 0.125. At the day-level attenuation of 0.47 read off the existing
    /// tilt's own contrast, that predicts about +0.059 of day-level
    /// `corr_asymmetry` at 504 against a gap of 0.0646. The test that
    /// decides it: if `d(corr_asymmetry)/dc` measures at or below the
    /// tilt's own +0.199, the reallocating form buys nothing the
    /// multiplying form does not. The 0.47 is read off one contrast of a
    /// different wire and is the weakest number in the derivation.
    ///
    /// # The neutrality, exactly
    ///
    /// Let `f` be the tick's market factor and `e` the name's
    /// idiosyncratic draw, with `E[e] = 0`, `Var[e] = s^2`, and `e` drawn
    /// independently of `f`. The transform is `e' = m(f) e` with
    /// `m = (1 - c)` on `{f < 0}` and `m = sqrt(2 - (1 - c)^2)` elsewhere.
    ///
    /// The mean is exactly zero for any `m`:
    /// `E[e'] = E[m(f)] E[e] = 0` for any split of the line, because `e`
    /// is an independent zero-mean draw. Compare
    /// `market_beta_down_asym_recentre`: the tilt injects a mean because it
    /// scales the draw whose own sign it branches on, and `E[f 1{f<0}]` is
    /// not zero. This branches on a different draw, so there is no first
    /// moment to give back and no recentring dial beside it.
    ///
    /// The variance is held exactly, and that is what the symmetry buys.
    /// `E[e'^2] = E[m(f)^2] s^2` with
    /// `E[m(f)^2] = p (1-c)^2 + (1-p) (2 - (1-c)^2)` at `p = P(f < 0)`.
    /// The inflation is defined as the complement, so at `p = 1/2` the two
    /// squared scales average to exactly 1 for every `c`. That is why there
    /// is no funding dial here either, and why `market::index_var` needs no
    /// new term: its `idio * idio` is the unconditional variance and this
    /// leaves it alone.
    ///
    /// # Where "exactly" stops
    ///
    /// 1. Exact in expectation, `O(N^{-1/2})` in a realization. Over `N`
    ///    ticks the realized multiplier on the idiosyncratic variance is
    ///    `M_N = (k/N)(1-c)^2 + (1 - k/N)(2 - (1-c)^2)` with
    ///    `k ~ Binomial(N, 1/2)`, since the sign of a symmetric draw is a
    ///    fair coin independent of every sigma in the recursion. So
    ///    `E[M_N] = 1` exactly and `sd(M_N) = |1 - (1-c)^2| / sqrt(N)`. The
    ///    test `the_variance_residual_is_the_binomial_one` checks it against
    ///    that closed form. At `c` = 0.15 it is 1.4 per cent of the
    ///    idiosyncratic variance over one 390-tick session and 0.089 per
    ///    cent over a 252-session window, which on a name whose
    ///    idiosyncratic leg is two thirds of its variance is 0.03 per cent
    ///    of its sigma.
    /// 2. One ulp of arithmetic. `(1-c)^2` and `2 - (1-c)^2` are computed
    ///    in doubles, so their mean is 1 to a relative `2^-52` and not to
    ///    the bit. Pinned by
    ///    `the_two_scales_average_to_one_to_within_an_ulp`.
    /// 3. The zero tick. The branch is `< 0.0`, the convention
    ///    `market_beta_down_asym` already uses, so a factor of exactly
    ///    `0.0` takes the up branch. On a live draw that has probability
    ///    zero. On a degenerate configuration where the factor cannot move
    ///    (`market_factor_sigma` 0.0, or a fixture that hands the tick a
    ///    zero factor) every tick takes the up branch and the idiosyncratic
    ///    variance is inflated by `2 - (1-c)^2`. The neutrality assumes half
    ///    the ticks are down, which fails when none of them is. A
    ///    zero-factor fixture is where somebody would go to measure
    ///    neutrality, and they would find it absent.
    ///
    /// # Costs and limits
    ///
    /// 1. It is not conditionally variance-neutral, and cannot be. A name's
    ///    per-tick total variance is `beta^2 s_f^2 + m(f)^2 s_i^2`, which
    ///    is lower on a down tick and higher on an up tick, and that
    ///    inequality is the raised factor share. So a down day, which holds
    ///    more down ticks than an up day, carries slightly less name-level
    ///    variance: at `c` = 0.15 a 55/45 day carries 0.972 of its
    ///    idiosyncratic variance. The per-name GJR GARCH innovation is the
    ///    day's noise and `garch_gamma` reads exactly that conditional
    ///    moment, so the leverage channel sees a slightly smaller down-day
    ///    squared return. The effect is signed and small.
    /// 2. It covers the intraday leg only. The overnight move composes a
    ///    name through `idio_scale_for` as the tick does, and it is
    ///    untouched here because there is no tick sign at the open. Nothing
    ///    is lost on the shipped presets, where `overnight_variance_ratio`
    ///    is 0.0 and no price moves between sessions. On a preset that
    ///    turned it on, the reallocation would cover the intraday part of
    ///    the close-to-close return and leave the gap alone.
    ///
    /// It takes no draw. The transform reshapes a shock the tick has
    /// already taken, so the draw count and the draw order are untouched
    /// at every value and on every branch.
    pub market_idio_down_suppress: f64,
    /// How much of oil demand is answered by supply on the daily step, from
    /// 0.0 (none) to 1.0 (supply equals demand in expectation). 0.0, which
    /// every preset before pt-v18 carries, is bit-identical and is what the
    /// reference implementation does. pt-v18 onward ship 1.0.
    ///
    /// # What happens at 0.0
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
    /// That supply term is the whole cause of the one-way rate path seen at
    /// 0.0. No rate mechanism is involved.
    ///
    /// # Why 1.0
    ///
    /// At 1.0 supply equals demand in expectation, so `inventory_change`
    /// is the noise term alone and inventory is driftless. That is the
    /// stationarity condition of the inventory process, read off the
    /// process itself, and the `0.15` it uses is the coefficient already
    /// there. Nothing here is fitted to a target.
    ///
    /// The pressure term is already two-sided, pushing up below 40 and
    /// down above 60, so a driftless inventory gives a two-sided oil price
    /// and a two-sided rate path without any of them being made two-sided
    /// by hand.
    pub oil_supply_response: f64,
    /// Removes the direction from the OPEC production rule while keeping its
    /// size. 0.0, which every preset before pt-v18 carries, is
    /// bit-identical. pt-v18 onward ship 1.0.
    ///
    /// # The asymmetry at 0.0
    ///
    /// The rule reacts to the oil price against an 80 target. Below it by
    /// more than 10 it cuts production with probability 0.6 and magnitude
    /// 3 to 6; above it by more than 10 it raises production with
    /// probability 0.5 and magnitude 2 to 5. Expected `+2.700` against
    /// `-1.750`, so the cut is 1.54 times the increase and the rule pushes
    /// the oil price up on net. Nothing in the code says that was intended
    /// and the two branches read as a pair that should mirror.
    ///
    /// # At 1.0
    ///
    /// Both branches use one probability and one magnitude range, each the
    /// mean of the two the rule already carries: probability 0.55,
    /// magnitude 2.5 to 5.5. That is the unique symmetric rule which
    /// preserves the total intervention the rule performs, so it removes
    /// the direction without choosing a side or inventing a number.
    /// Expected impact is then equal and opposite either side of the
    /// target, and zero on net.
    ///
    /// It is worth about +0.95 of oil price per firing and the rule fires
    /// every 90 days, so this is a small term.
    pub oil_opec_symmetry: f64,
    /// Where oil's seasonal shape acts: on the price level itself (0.0) or
    /// on the price the process reverts toward (1.0). 0.0, which every
    /// preset before pt-v18 carries, is bit-identical. pt-v18 onward ship
    /// 1.0.
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
    /// # The target instead of the level, at the same amplitude
    ///
    /// At 1.0 the whole amplitude multiplies the reversion target and none
    /// of it multiplies the level, so the shape moves where the price is
    /// pulled toward by plus or minus 3 per cent and integrates to +0.672
    /// per cent of oil over a certified year. The amplitude is the 0.03 the
    /// term already carries and this dial is a share of it, so it changes
    /// where the shape acts and leaves its size alone. Nothing here is
    /// fitted: a seasonal shape has to be neutral over the window as well
    /// as over its own period, and moving it off the level is what makes
    /// that possible without touching its size.
    ///
    /// Between the ends the amplitude is split, `1 + g * a` on the target
    /// and `1 + (1 - g) * a` on the level, so the total is conserved and
    /// past 1.0 the level would carry the shape inverted.
    pub oil_seasonality_target: f64,
    /// Reads the business cycle's hazard as a rate per month: at 1.0 the
    /// monthly transition probability is divided by 30 before the daily
    /// draw. 0.0, which every preset before pt-v18 carries, applies the
    /// monthly rate once a day, bit-identically, so the cycle runs about
    /// thirty times too fast. pt-v18 onward ship 1.0.
    ///
    /// # A rate per month drawn once a day
    ///
    /// `weibull_hazard` returns `(shape / scale) * pow(months / scale,
    /// shape - 1)`, and every scale in `cycle_hazard_params` is in months:
    /// 36 for an expansion, 6 for a peak, 12 for a contraction. A hazard
    /// whose scale is in months is a rate per month, and
    /// `check_cycle_transition` compares it against a uniform once a day.
    /// At 0.0 a full cycle takes 2.6 trading years where the same constants
    /// read per month give 9.7, and a 252-day run opening at the start of
    /// an expansion leaves it 63 per cent of the time against 3.
    ///
    /// Both figures are the hazard alone. The condition ladder below adds
    /// to it and never subtracts except through the expansion guard, so
    /// each bounds a phase's length from above. The ratio of thirty is
    /// unaffected, since both readings exclude the ladder equally, and the
    /// ladder is scaled with the base, because the conversion below is
    /// applied after it.
    ///
    /// # When the ladder's conditions fire
    ///
    /// Every contraction condition adds to the hazard, so the ladder can
    /// only shorten a phase. The four contraction conditions do not all
    /// fire early in a phase. Measured on four-year runs they fire late:
    /// growth under -2.0 on day 1 from the phase-change shock, the policy
    /// rate under 1.0 on day 190 at the median, and unemployment over 10.0
    /// on day 239.
    ///
    /// A spell's count of fired conditions therefore records how long it
    /// has already run. Completed contraction spells read 162 days at one
    /// condition, 226 at two and 335 at three, which sorts them by duration
    /// and says nothing about depth. No deep contraction ended early by its
    /// own ladder was found.
    ///
    /// `months_in_current_phase` advances by exactly `1/30` a day, so the
    /// month this model keeps is 30 days and the divisor is read off the
    /// engine's own clock. At 1.0 the daily probability is the monthly rate
    /// divided by 30 to the last bit.
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
    /// Measured over thirty seeds at 1008 days. Read per month the clamp
    /// binds on 527 of 2082 peak rolls and 153 of 203 trough rolls on the
    /// hazard alone, and on 0 of 18352 expansion and 0 of 1495 contraction
    /// rolls. Drawn per day it binds on none of them.
    ///
    /// With the ladder the trough is different, under either setting. A
    /// trough adds 0.1 for a policy rate under 3.0 and 0.05 for
    /// unemployment over 8.0, against a hazard of 0.265 at its two-month
    /// minimum, so the clamp binds on its first eligible roll under either
    /// reading: 203 of 203 read per month and 97 of 97 drawn per day, over
    /// the same runs. The clamp was already doing work in a trough, and at
    /// 1.0 phases last long enough to reach one. A certified year reaches
    /// no trough at all and records 0 of 2121 rolls clamped under either
    /// reading.
    pub cycle_hazard_per_month: f64,
    /// Raises the bottom of the trough phase's GDP growth range from -1.0
    /// (at 0.0) to 0.0 (at 1.0), moving proportionally in between; the top
    /// stays at 0.5. Every preset ships 0.0, the original `(-1.0, 0.5)`
    /// range, bit-identically.
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
    /// frequency measures. The share of days in a contraction reads 18.21
    /// and the share with output falling reads 27.31, while contraction
    /// or trough reads 27.61: the second and third agree to 0.3 points,
    /// so the nine-point gap is this phase. The NBER's two measures agree
    /// to 1.2 points, 10.72 per cent of months in a contraction against
    /// 11.93 per cent of quarters with real GDP falling, because a
    /// contraction there is the falling-output period.
    ///
    /// At 1.0 the lower end moves to 0.0 and the upper end keeps the 0.5
    /// the table already states. Zero is the only non-arbitrary floor,
    /// being the boundary between falling and rising output, so nothing
    /// here is fitted.
    ///
    /// # Why every preset ships 0.0
    ///
    /// It closes 1.52 points of that nine-point gap. Thirty seeds at a
    /// hundred years: the output measure moves from 27.309 to 25.791 and
    /// the contraction share moves 0.10, so the mechanism is the one
    /// described and its size is a sixth of what the accounting implied.
    ///
    /// A target is only reached if the phase lasts long enough to
    /// approach it. Growth enters a trough near -3.0, having left a
    /// contraction whose realized rate is about -2.5 and then taken this
    /// phase's own -0.5 entry shock, and it reverts by `gap * 0.12` a
    /// month and `gap * 0.25` a quarter. Under the original range it
    /// approaches -0.25 and never crosses zero. At 1.0 it crosses at month
    /// 11 or 12. A trough runs 2 to 6 months, so output falls through the
    /// whole phase under either setting and this dial changes where growth
    /// is heading, with little effect on where it is.
    ///
    /// So the binding constraint is the reversion rate against the phase
    /// length, and the declared range matters little. The evidence points
    /// at the -0.5 growth shock a trough takes on entry, in
    /// `economy/daily.rs`: of the phases after a contraction, only the one
    /// named for the turn still pushes growth down on entry. The dial stays
    /// at 0.0 so the measurement can be repeated.
    pub trough_growth_floor: f64,
    /// Whether a phase's growth target is drawn from its declared range
    /// (1.0) or fixed at the range's midpoint (0.0). Every shipped preset
    /// carries 0.0, which takes no draw.
    ///
    /// # A declared range the mechanism reduces to its midpoint
    ///
    /// `gdp_growth_range` is stated for all five phases and read at
    /// exactly two sites, both of which take `(lo + hi) / 2.0`. At 0.0 the
    /// declared width is discarded everywhere, so the table behaves as a
    /// list of five midpoints. The same struct has two related quirks:
    /// `max_months` is declared for every phase and read by nothing, and
    /// the trough's phase-change shock carries a sign that contradicts its
    /// own phase.
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
    /// # Why it ships at 0.0
    ///
    /// Measured alone this dial widens the depth spread, and so does its
    /// companion recession dial. Turned on together they are worse than
    /// either, because the pair eliminates shallow recessions. A preset
    /// that wants the wider spread has to measure the dials together.
    pub phase_target_range_draw: f64,
    /// The corporate bond yield at which the target multiple sits exactly on
    /// its sector anchor, as a fraction (0.04 is 4 per cent). pt-v1 through
    /// pt-v16 carry 0.04; pt-v18 onward carry 0.0482.
    ///
    /// # A neutral point the economy never visits
    ///
    /// `compute_target_pe` compresses the multiple by
    /// `(discount - neutral) * RATE_PE_SENSITIVITY * duration`, so a name
    /// is valued on its anchor exactly when the discount rate equals this.
    /// The economy opens at a corporate yield of 4.56 per cent and settles
    /// at 4.82, and it never visits 4.00. So at 0.04 every profitable name
    /// opens about one per cent below the price the generator drew for it,
    /// which is +0.0107 of day-zero mispricing at the opening and +0.014 at
    /// the settled state, and the market spends the year unwinding it on a
    /// 60-day half-life. At a measured -94.872 index points per unit of
    /// opening level that is 1.0 to 1.3 points of the first year, on every
    /// seed, and nothing in a stationary year.
    ///
    /// # Read off the economy
    ///
    /// The value that zeroes the day-zero term is the yield the economy
    /// opens at: 0.0456 at a cold start and 0.0482 at the settled state the
    /// dynamics reach. Both are read off the process, the second from the
    /// burn-in table, so neither is a matter of degree.
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
    /// # One name for one quantity
    ///
    /// This name was on the carried read-only surface before it became
    /// settable. `to_pairs` merges that surface with the settable one and
    /// sorts, so moving the name between them leaves every preset's pairs,
    /// fingerprint and coefficient digest untouched wherever the value has
    /// not moved.
    pub neutral_discount_rate: f64,
    /// Days the economy is advanced alone, before day zero, so a run opens
    /// on settled macro fields. 0.0 draws nothing; pt-v18 onward carry 755.
    ///
    /// # Why the economy needs settling
    ///
    /// The economy opens at unemployment 4.00, inflation 2.00 and a
    /// corporate yield of 4.56, and its own dynamics reach 2.50, 2.74 and
    /// 4.82. Without a burn-in, every certified window is one in which
    /// nothing has settled, and the travel is one-way on the multiple.
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
    /// day-0 draws are where they were. It consumes economy draws, which
    /// makes it the only settable field besides the volatility jump that
    /// moves a draw count, and it does so by running the economy.
    pub macro_burn_in_days: f64,
    /// Switch (0.0 or 1.0) that draws the day-zero cycle phase and its age
    /// from the cycle's own stationary law, instead of opening every run at
    /// the start of an expansion. Presets through pt-v18 carry 0.0, which
    /// draws nothing; pt-v19 and pt-v20 carry 1.0, so certification runs open
    /// at a random point in the business cycle.
    ///
    /// # A cohort, not a transient
    ///
    /// At 0.0 every run opens in EXPANSION at phase age ZERO. A phase has a
    /// minimum duration before a transition can roll (`economy/state.rs`,
    /// `phase_characteristics`: 6, 2, 4, 2, 4 months) and then a Weibull
    /// hazard (`economy/cycle.rs`), and at this clock the exit after the
    /// minimum is steep, so thirty seeds leave their first expansion at
    /// nearly the same age and move through the first cycle in step. Year
    /// two is a synchronized recession (contraction share 0.51 on pt-v1,
    /// pt-v16 and a pinned-VIX arm alike, so it is a property of the
    /// construction and not of a preset), and the macro fields travel with
    /// it: unemployment 2.8, 5.8, 7.0, 4.9 by year on pt-v16, the recession
    /// probability 0.13, 0.54, 0.14, 0.37.
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
    /// is the engine's own (`cycle_hazard_params`, `min_months`, the
    /// hazard's cap of 0.8, the clamp at 0.3 and
    /// [`ModelParams::cycle_hazard_per_month`]), and the walk stops where
    /// the remaining tail cannot move an f64 sum of it.
    ///
    /// The age matters as much as the phase. `f_i` is the renewal identity
    /// for the backward recurrence time. Drawing the phase and setting the
    /// age to zero would start a smaller cohort at the same point: at this
    /// clock 73 per cent of stationary expansions are younger than the
    /// 180-day minimum a fresh one has to clear, with a median age of 123
    /// days against a mean sojourn of 246.
    ///
    /// # The fields, and the ladder
    ///
    /// `adjust_transition_probability` adds to the hazard from the macro
    /// state, so the TRUE stationary law depends on the fields, which
    /// depend on the phase path. There is no closed form. The draw above
    /// is the hazard-only law. The fields are then relaxed by
    /// `macro_burn_in_days` days of the ordinary daily step with the phase
    /// NOT held and its clock NOT reset. The chain is already in its
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
    /// # Why it is a switch
    ///
    /// A day-zero state is either drawn from the stationary law or it is
    /// not; there is no half-drawn phase. Every non-zero value therefore
    /// gives the same opening, which
    /// `test_every_non_zero_value_gives_the_same_opening` in
    /// `tests/test_stationary_opening.py` asserts. The two admissible
    /// values are 0.0 and 1.0.
    ///
    /// # What it costs
    ///
    /// Two uniforms from the economy substream at construction, so the
    /// market's day-zero draws sit where they sat, plus about 2,500 to
    /// 67,000 multiplies once for the identity's walk, whose length is set
    /// by the clock and not by the run. Nothing per session.
    ///
    /// # It moves the economy's draw schedule in two places
    ///
    /// The first is the two construction uniforms, as `macro_burn_in_days`
    /// also moves it. The second lasts the whole run:
    /// `check_cycle_transition` returns before drawing while a phase is
    /// younger than its minimum duration, so a run opening at age zero
    /// rolls no exit for its first 180 days while one opening past the
    /// minimum rolls one every day. That count is the mechanism itself (a
    /// phase past its minimum is a phase whose exit is being rolled), and
    /// it is not a construction cost.
    ///
    /// The three-day perturbation probe cannot see either, and reads the
    /// count IDENTICAL at its own seed: the phase-change block in
    /// `economy/daily.rs` takes a uniform on both of the two days it fires
    /// and a drawn age past two days skips both, cancelling the two
    /// construction draws exactly. Measured over six seeds at 1, 2, 3, 5,
    /// 10 and 30 days the difference runs 0, +1, +2, +4 and +30, which is
    /// why `ECONOMY_STREAM_MOVERS` in `tests/test_model_params.py` carries
    /// this dial for the mechanism rather than on the probe's evidence. No
    /// dial moves the market stream.
    pub cycle_stationary_opening: f64,
    /// The share of earnings a company returns as net buybacks, paid as a
    /// yield on the price. 0.0 turns it off; pt-v18 and pt-v19 carry one
    /// third, and pt-v20 carries 0.75.
    ///
    /// # A chosen constant
    ///
    /// Most dials in this group are derived: a stationarity condition, a
    /// symmetric mean, a unit, a measured transient, the yield the economy
    /// opens at. This one is a statement about how US large-cap companies
    /// behave, taken from company filings, where total shareholder return
    /// runs near half of earnings split between dividends and net
    /// buybacks. A third in net buybacks sits inside that record and a
    /// reader can check it against the same source.
    ///
    /// It is not fitted to anything this engine produces, which is the
    /// property that makes it checkable: a value fitted to the model's own
    /// distribution would be unfalsifiable outside it.
    ///
    /// # What a third buys, and the check on it
    ///
    /// The model's own median annual earnings yield is 0.0555 at pt-v18 on
    /// its certified roster, so a third of it is a buyback yield of 1.85
    /// per cent. US large-cap net buybacks over 2000 to 2025 run near 1.5
    /// to 2.0 per cent of market value. That agreement is a check on the
    /// share, in that order: the share comes from the earnings record and
    /// the yield it implies is then compared against the value record.
    ///
    /// # Why it is a yield
    ///
    /// Earnings over price, so it pays more when the multiple is low and
    /// less when it is high. A flat premium would pay the same in a market
    /// priced at forty times earnings as at ten, which is the opposite of
    /// what a buyback program does with a fixed budget. See
    /// [`crate::market::tick::buyback_scale`] for the arithmetic, its
    /// residual against the exact path integral, and the clamp on a
    /// loss-maker.
    ///
    /// # pt-v20's 0.75 is a calibration
    ///
    /// None of the filings argument above is the source of pt-v20's 0.75.
    /// The earnings anticipation and the rate sensitivity cost the index
    /// its one-year drift, no setting at a third held the floor of the
    /// level band, and 0.75 restores it. At the 0.0555 earnings yield above
    /// it would be a buyback yield of about 4.2 per cent, but that is the
    /// median name: the index's delivered yield (the cap-weighted log rate
    /// of the buyback factor) is 2.0 per cent on held-out seeds, and decays
    /// from 3.3 in year 2 to 0.8 in year 21. pt-v20 pays no dividends
    /// (`dividend_payout_share` is on in pt-v21 at 1.2 and off on every
    /// earlier preset), so the term carries the whole of the drift that
    /// payouts would; that is a reading of the gap, not a
    /// measurement behind the value. Under `dividend_buyback_substitution`
    /// this is the total payout, and a name's buyback share is it less the
    /// name's dividend payout.
    /// pt-v21 ships 0.9.
    pub buyback_payout_share: f64,
    /// How much of the drift the market jump's mean carries is given back,
    /// from 0.0 (none) to 1.0, which subtracts the compensator and makes the
    /// jump a martingale. pt-v1 through pt-v16 carry 0.0; pt-v18 onward
    /// carry 1.0.
    ///
    /// # The mean is there for skew, and it also buys a drift
    ///
    /// `jump_mean_market` is negative so that crashes are larger than
    /// rallies, which is a real property of index returns. But a jump that
    /// arrives with probability `lambda` and mean `m` contributes
    /// `lambda * m` to the expected return every day whether it fires or
    /// not, so the skew comes with an unintended drift. At pt-v16 that is
    /// -0.11769 per name per year at the day-zero intensity, and 2.6
    /// percentage points of annual index level. The mean was set in the
    /// pt-v4 era by a search whose objective could not read a first
    /// moment, and was inherited unchanged through eleven presets.
    ///
    /// # Compensated rather than re-derived
    ///
    /// Solving for a smaller mean that buys skew without the drift does not
    /// work: for a compound Poisson jump the drift and the skew are both
    /// linear in the mean, so trading one against the other is a matter of
    /// degree and any answer would be a fitted constant.
    ///
    /// Subtracting `lambda * m` instead is the standard compensated-Poisson
    /// construction. It makes the jump term a martingale, and because the
    /// compensator is a deterministic offset it moves the first moment and
    /// leaves every central moment untouched. The skew and the fat tail
    /// survive exactly, at the calibrated mean, and the drift goes to zero.
    ///
    /// `lambda` is the CONDITIONAL intensity, already scaled by the VIX
    /// coupling, so the compensator tracks the arrival rate. The realized
    /// drift measured 1.084 times the day-zero closed form for exactly that
    /// reason, and a compensator on the day-zero rate would have left that
    /// 8 per cent behind.
    pub jump_mean_compensated: f64,
    /// How much of the direction in the stop-cascade ladders is removed,
    /// from 0.0 (the original asymmetric ladders) to 1.0 (mirror images).
    /// pt-v1 through pt-v16 carry 0.0; pt-v18 onward carry 1.0.
    ///
    /// # The asymmetry at 0.0
    ///
    /// Forced flow from resting stop orders runs both ways: stop-losses
    /// under longs on the way down, buy-stops over shorts on the way up.
    /// At 0.0 the two ladders that express it do not match. The downside
    /// fires at a 2 per cent move and the upside at 3; the downside has
    /// four tiers and the upside three; and at every matched size the
    /// downside is larger, 0.008 against 0.006, 0.005 against 0.004, 0.003
    /// against 0.002, with a fourth downside tier of 0.001 that has no
    /// partner. Every one of those is a bare literal with no parameter and
    /// no recorded reason for the difference.
    ///
    /// Over a symmetric distribution of daily returns a ladder that
    /// subtracts more than it adds is a drift.
    ///
    /// # What is matched, and what is left alone
    ///
    /// The gates stay. A stop-loss sits under every long, so the downside
    /// needs no condition; a buy-stop needs shorts to exist, so the upside
    /// keeps `short_interest_ratio > 0.1`.
    ///
    /// The threshold and the tier magnitudes are matched, at the mean of
    /// the two ladders: threshold 0.025, tiers 0.007, 0.0045, 0.0025 and
    /// 0.0005. That is the same construction the OPEC rule uses. It
    /// chooses neither side and it preserves the total intervention the
    /// pair performs exactly, 0.029 across both ladders before and after.
    ///
    /// # What is not derived
    ///
    /// Unlike the tilt, the jump and the oil supply term, this one has no
    /// stationarity condition or closed form behind it. Odd symmetry in
    /// the return is the structural claim; the mean is a rule for picking
    /// the numbers under it rather than a value read off the process. The
    /// tier literals themselves are not parameters.
    pub cascade_symmetry: f64,
    /// A scale on the whole forced-flow term: the short squeeze and both
    /// stop ladders ([`ModelParams::cascade_symmetry`]). 1.0, which every
    /// preset through pt-v19 carries, is the ladder as it stands; 0.0
    /// switches it off, and pt-v20 carries 0.1.
    ///
    /// The term reacts to the name's own previous day (a stop cascade after
    /// any fall of 2.5 per cent, a buy cascade on a heavily shorted rally),
    /// so it is a next-day continuation in the model price. With the tape
    /// following the model price (`quote_model_weight`) it is the largest
    /// daily momentum left in it: on pt-v19's certified roster it carries
    /// about -18 bp a day of a Lo-MacKinlay one-day contrarian book's -23,
    /// against the certified forty's -1.7 +/- 2.3.
    pub cascade_gain: f64,
    /// Daily persistence of the slow component of the market factor's
    /// variance (Engle-Lee style). The fast component tracks the
    /// VIX-scaled target and this one carries long-horizon clustering; 0.0
    /// disables it and recovers the single-component update bit for bit.
    pub market_vol_slow_persistence: f64,
    /// How much of each day's variance surprise the slow component takes
    /// up. 0.0 disables it.
    pub market_vol_slow_gain: f64,
    /// Switch that applies the loss-maker book floor to profitable
    /// companies too, making fair value continuous at zero earnings. 0.0,
    /// which every shipped preset carries, is off.
    ///
    /// At 0.0 the valuation switches hard at `eps > 0`, as the reference
    /// implementation does: a company earning 0.01 is valued on earnings
    /// and one earning exactly 0 is valued at
    /// `book * LOSS_MAKING_PRICE_TO_BOOK`. Fair value therefore JUMPS UP as
    /// earnings fall through zero, and a barely profitable company is worth
    /// less than a loss-making one with the same book.
    ///
    /// At 1.0 the floor applies on both sides, `max(eps * pe, book * 1.2)`, so
    /// fair value is continuous at zero and non-decreasing in earnings.
    ///
    /// It is off by default because it is a large change: 42.8% of
    /// instruments from `Universe.random` sit below the floor, some at a fifth
    /// of it, so switching it on re-values a large part of any universe and
    /// re-bases every calibrated statistic. It exists so that a time-varying
    /// earnings path has somewhere monotonic to run; adopting it needs a new
    /// preset and a recalibration.
    pub fair_value_book_floor: f64,
    /// How much of nominal output growth the valuation's earnings carry,
    /// from 0.0 (earnings fixed at construction) to 1.0 (the earnings share
    /// of nominal output held constant). pt-v1 through pt-v16 carry 0.0;
    /// pt-v18 onward carry 1.0.
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
    /// # The clock
    ///
    /// `N` is integrated by the economy, which compounds `gdp` by
    /// `gdp_growth / 100 / 365` and `cpi` by `inflation_rate / 100 / 365`
    /// on every day it advances, while the market trades 252 days to a
    /// year and annualizes by 252. The economy advances once per market
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
    /// would have paid the same rate through all of that.
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
    /// # What it does not claim
    ///
    /// Earnings are a share of nominal output, and the share is a quantity
    /// this model does not carry. At 1.0 the share is held constant, which
    /// is the only value that is read off the process rather than chosen;
    /// below 1.0 the share falls every year and above 1.0 it rises
    /// forever, both of which are assertions about an unmodeled quantity.
    ///
    /// A real price index also earns a return above nominal output growth,
    /// through buybacks and the drift of the earnings share, worth 3 to 4
    /// points a year. The model has nothing to derive that from, so this
    /// term does not attempt it and the gap is reported rather than
    /// closed.
    pub earnings_nominal_growth: f64,
    /// Weight of the slow variance component in the market factor's
    /// two-component mixture, from 0.0 (single component) to 1.0. pt-v15
    /// onward carry 0.35.
    ///
    /// The mixture exists because real volatility memory decays
    /// hyperbolically and a single exponential cannot imitate that past
    /// about a year. Two exponentials imitate it better and still come
    /// apart at long lags, which is a known gap in the model.
    pub market_vol_slow_weight: f64,
    /// How strongly realized volume tracks the market factor's variance.
    /// 0.0 disables it and leaves volume exactly as it was; pt-v4 onward
    /// carry about 0.0284.
    ///
    /// Volume in this engine is a pure function of `avg_volume`, which the
    /// close holds fixed ([`crate::market::daily::AvgVolumePolicy::Hold`]),
    /// so without this term daily volume changes are very nearly
    /// independent noise and difference to an autocorrelation near -0.5
    /// (measured -0.46 against a real -0.32 to -0.20). Real volume is a
    /// persistent level plus large day-to-day noise, and the persistence is
    /// what this supplies.
    ///
    /// The driver is the market factor's variance, which is already
    /// persistent and is EXOGENOUS to volume, the property the `Hold`
    /// docstring names as the precondition for reintroducing any feedback.
    /// Feeding realized volume back was tried and removed: it is a pure
    /// function of `avg_volume`, so the loop carried no information and
    /// compounded at about 1.7% a day. This carries information, because
    /// volume and volatility co-move.
    pub volume_variance_gain: f64,
    /// Daily persistence of a per-name volume state, so each name has busy
    /// and quiet spells of its own. 0.0, which every shipped preset
    /// carries, turns it off.
    ///
    /// `volume_persistence` carries a COMMON multiplier: every name shares
    /// it, so the whole market is busy or quiet together. That function's
    /// own docstring says "real volume persistence is partly
    /// idiosyncratic, and that half is not modelled". This is that half.
    ///
    /// `volume_change_acf1` at 504 days reads about -0.316 against a band
    /// of -0.29 to -0.21 on presets without it, and the model is too
    /// NEGATIVE, which is what independent per-tick noise does to the
    /// change in a series. Reaching the band through the common component
    /// needs a bigger innovation, and that takes `volume_abs_return_corr`
    /// out with it: a market-wide volume multiplier adds volume variance
    /// unrelated to any name's own moves. A per-name state raises each
    /// name's own volume autocorrelation without touching the common
    /// component. Measured, it lowers `volume_abs_return_corr` as well;
    /// see [`ModelParams::volume_idio_variance_gain`].
    pub volume_idio_persistence: f64,
    /// Gain that makes a name's volume follow its own conditional variance,
    /// so a name trades more when its own volatility is high. 0.0 turns it
    /// off; pt-v19 and pt-v20 carry 0.2.
    ///
    /// `volume_variance_gain` couples volume to the MARKET factor's
    /// variance and nothing else, so without this a name whose OWN
    /// volatility is elevated trades no more than a quiet one in the same
    /// market.
    ///
    /// Per-name volume PERSISTENCE alone fixes `volume_change_acf1` and
    /// takes `volume_abs_return_corr` down with it, exactly as the common
    /// component does. `volume_abs_return_corr` measures how well volume
    /// tracks the size of a name's own move, so any volume variance
    /// UNRELATED to that name's returns dilutes it, and per-name noise is
    /// as unrelated as market-wide noise. This is the return-related
    /// version: at `g` the multiplier is
    /// `1 + g * (garch_variance / sector base - 1)`, clamped.
    /// pt-v21 ships 0.65.
    pub volume_idio_variance_gain: f64,
    /// Innovation size of the per-name volume state. 0.0 on every shipped
    /// preset. See [`ModelParams::volume_idio_persistence`].
    pub volume_idio_sigma: f64,

    /// How many components a name's variance cascade carries. 0 is off and
    /// is what every shipped preset uses, so the single-component GJR
    /// recursion runs bit for bit.
    ///
    /// See [`crate::market::garch::update_garch_cascade`] for why more than
    /// two matters: a superposition of exponentials with geometrically
    /// spaced timescales approximates a power law, and the count you need is
    /// about `log(range)/log(ratio)`. Six at ratio 3 covers lags 1 to 60.
    /// Capped at [`crate::market::garch::CASCADE_MAX`].
    pub garch_cascade_components: f64,
    /// Half-life spacing between cascade components: component `k` has a
    /// half-life `ratio^k` times component 0's. Every shipped preset
    /// carries 3.0.
    ///
    /// Component 0 keeps the name's own `garch_beta`, so per-name
    /// persistence dispersion survives. Measured at ratio 3 with six
    /// components, the latent decay slope reads -0.536 against a
    /// one-component -1.273 and a real -0.436.
    pub garch_cascade_ratio: f64,
    /// How much of the variance comes from the cascade rather than from the
    /// single-component process. 0.0 is the single-component process
    /// exactly, and 1.0 is the cascade alone.
    ///
    /// A dial rather than a switch so a preset can take part of the
    /// cascade's shape without paying all of its cost, and so the two can be
    /// separated in a search: `components` sets the shape, this sets how
    /// much of it reaches the price.
    pub garch_cascade_weight: f64,

    /// Base volume multiplier for a name on a day it does not move at all.
    /// Every shipped preset carries 0.6.
    ///
    /// Volume per tick is `base * (floor + response * min(move, cap) +
    /// noise * u)`, where `move` is the day's move from the open in units
    /// of one percent and `u` is a uniform draw. These four numbers were
    /// literals `0.6`, `0.6`, `4.0` and `0.2` in the tick engine until
    /// 0.3.0. Presets before pt-v12 carry exactly those values, so they
    /// print the volume they always did.
    ///
    /// See [`ModelParams::volume_move_cap`] for what the cap costs.
    pub volume_move_floor: f64,
    /// How much more a name trades per one percent it has moved today.
    /// 0.6 on most presets; pt-v16 to pt-v19 carry 1.0.
    ///
    /// This is the contemporaneous channel `volume_abs_return_corr`
    /// measures: that statistic asks how well a name's volume tracks the
    /// size of its own move, and this is the only term in the volume
    /// expression that ties the two together on the SAME day.
    /// `volume_variance_gain` and
    /// [`ModelParams::volume_idio_variance_gain`] both couple volume to a
    /// conditional variance, which is a forecast made from yesterday's
    /// information, and a forecast measurably tracks today's realized move
    /// far more loosely than today's move does.
    pub volume_move_response: f64,
    /// Where the volume response to a move saturates, in units of one
    /// percent. pt-v1 through pt-v11 carry 4.0; pt-v12 onward carry 12.0.
    ///
    /// At 4.0 a name that falls twelve percent trades exactly as much as
    /// one that falls four. Real markets do not do that: volume on a
    /// limit-down day is a multiple of a bad-Tuesday day, and the
    /// relationship keeps rising well past four percent. A low cap holds
    /// `volume_abs_return_corr` down and makes it easy to dilute, because
    /// every crisis day is pinned to one value and contributes no
    /// covariance at all.
    ///
    /// Raising it also raises volume in crises, which changes returns as
    /// well: volume feeds the book depth that prices settle through, so a
    /// volume dial is a price dial.
    pub volume_move_cap: f64,
    /// Amplitude of the return-unrelated noise in a name's daily volume.
    /// Every shipped preset carries 0.2.
    ///
    /// It multiplies a uniform draw, so it widens volume without any
    /// relation to what the name did, which dilutes
    /// `volume_abs_return_corr`. Lowering it should raise that statistic
    /// and cost realism in whatever a real market's unexplained volume
    /// variation represents, so it is a dial rather than a thing to
    /// minimize.
    pub volume_move_noise: f64,
    /// How much of a jump's share of the day's move the volume scale
    /// counts, from 0.0 (none) to 1.0. Every shipped preset carries 1.0,
    /// where the arithmetic is the original one.
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
    /// That couples two things. The model's idiosyncratic jump is five
    /// times the tape's size at a fortieth of its rate, and the rare huge
    /// private jumps are what hold `volume_change_acf1` inside its band.
    /// Take them out and the band is left; leave them in and the names'
    /// excess kurtosis is made by jumps the tape does not have. This dial
    /// separates the two: it decides whether the volume process is allowed
    /// to see a jump at all.
    ///
    /// At share `q` the day's move is measured from the open the name
    /// would have had if `(1 - q)` of the jump had gapped overnight:
    /// `open_eff = open * exp((1 - q) * j)`, with `j` the log jump the
    /// close booked into `s`, read back from the jump slot of the
    /// attribution accumulator, which `apply_jumps` is the only writer of.
    /// At 0.0 the volume scale reads the diffusion move alone, which is
    /// what a gap is: volume on a gap day is made at the open, not by the
    /// name traveling that distance through the book.
    ///
    /// The right value is undetermined. What would determine it is volume
    /// on jump days read off the tape (the share of a gap day's volume
    /// that the gap itself explains), and that has not been measured. 1.0
    /// ships because it is the original arithmetic.
    pub volume_move_jump_share: f64,

    // ── Universe memory (market/tick.rs, engine.rs) ─────────────────────
    /// Daily decay factor of the universe's remembered stress level, which
    /// keeps crisis correlation elevated after the VIX falls back. 0.0,
    /// which every shipped preset carries, means the level never survives
    /// a day.
    ///
    /// Without it the crisis correlation blend is a LOOKUP ON TODAY'S VIX:
    /// `spike = min(cap, (vix - threshold) / ramp)` with no state at all,
    /// so the tick VIX falls back under the threshold and the whole
    /// cross-section decouples in the same tick. A crisis leaves the
    /// universe exactly as it found it.
    ///
    /// Real correlation spikes with the shock and decays over weeks, which
    /// is the most-observed crisis fact there is. This carries a stress
    /// level that ratchets up instantly and decays geometrically, so an
    /// event has an effect that outlives it. At 0.0 the blend is exactly
    /// what it always was.
    pub universe_stress_decay: f64,
    /// How much of the remembered stress reaches the correlation blend.
    ///
    /// 0.0 disables the memory entirely; the blend then reads today's VIX
    /// and nothing else, bit for bit. Every shipped preset carries 0.0.
    pub universe_stress_weight: f64,
    /// Stress the business cycle adds to the correlation blend, in
    /// VIX-equivalent points at full intensity (a contraction).
    ///
    /// The engine runs a five-phase cycle (expansion, peak, contraction,
    /// trough, recovery). The central bank changes its policy by phase, but
    /// without this dial the price process behaves as though the economy
    /// were always expanding.
    ///
    /// It feeds the same remembered stress VIX does, so a contraction
    /// raises correlation across the whole cross-section and keeps it
    /// raised while the phase lasts and for weeks after it ends. Regime
    /// switching is also, per Diebold and Inoue, indistinguishable from
    /// long memory in the data, so this may reproduce the decay curve a
    /// second variance timescale was added to chase.
    ///
    /// 0.0, which every shipped preset carries, means the market ignores
    /// the cycle.
    pub regime_stress_points: f64,
    /// How far the slow variance component's target is decoupled from VIX,
    /// in [0, 1].
    ///
    /// With a shared target the two components chase VIX together. At 0.0
    /// the slow component tracks VIX exactly as the fast one does and the
    /// branch is skipped, so pt-v1 to pt-v3 reproduce bit for bit. At 1.0
    /// it ignores VIX entirely and reverts to the autonomous baseline,
    /// leaving the fast component to carry the whole response. pt-v1 to
    /// pt-v14 carry 0.0 and pt-v15 onward ship 0.374.
    ///
    /// # Measured effect
    ///
    /// The dial was built on the reasoning that a VIX-coupled slow
    /// component blunts the response to a VIX spike, so decoupling it
    /// should sharpen that response: pt-v3 retains 95.2% of pt-v1's
    /// steady-state VIX lever and only 27.6% of its transient. Swept at
    /// thirty seeds over eighteen configurations, damping makes both the
    /// shock ratio and the steady-state lever monotonically worse, at every
    /// fast persistence and every weight:
    ///
    /// | damp | shock | lever |
    /// |---|---|---|
    /// | 0.0 | 1.228 | 4.446 |
    /// | 0.5 | 1.208 | 4.116 |
    /// | 1.0 | 1.194 | 3.821 |
    ///
    /// Response speed is set by the fast component's persistence, and the
    /// slow component's VIX coupling contributes gain rather than lag, so
    /// removing the coupling removes gain. A value above zero needs its own
    /// measurement to justify it.
    ///
    /// The fast component's persistence is what restores the transient:
    /// 0.95 gives shock 1.228 and lever 4.446 where 0.97 gives 1.170 and
    /// 3.944. pt-v3's 0.989 buys volatility clustering and costs the
    /// scenario response.
    ///
    /// Neither lever is near real markets: measured on the 40-name
    /// reference roster, real is x6.16 (17.2% annualized below VIX 12
    /// against 106.1% above VIX 45) against roughly x3.1 in the
    /// configurations swept here.
    pub market_vol_slow_vix_damp: f64,

    // ── Endogenous jumps (engine.rs, applied at the day close) ──────────
    /// Daily probability that a market-wide jump fires.
    ///
    /// The model has no discontinuities without this. Prices diffuse; real
    /// markets gap. Nothing surprises this market unless a caller injects
    /// news by hand, and that is why excess kurtosis reads 5.2 over 504-day
    /// windows against real markets' 7.1 to 22. Fat tails at that scale are
    /// not reachable from a diffusion plus GARCH at any coefficients.
    ///
    /// Jumps are drawn from their OWN RNG stream ([`crate::rng::stream::JUMPS`]),
    /// which is what lets a draw-consuming mechanism ship inert: at intensity
    /// 0 the draws still happen, but they happen on a stream no earlier preset
    /// ever touched, so the market, economy and external streams are
    /// bit-identical and every shipped preset reproduces exactly.
    /// pt-v21 ships 0.005.
    pub jump_intensity_market: f64,
    /// Mean of the market jump in log-return units. Negative by intent,
    /// because real crash jumps are asymmetric. A symmetric jump process
    /// produces fat tails with the wrong skew, which would read as "kurtosis
    /// fixed" on the panel while getting crises backwards.
    /// pt-v21 ships -0.03.
    pub jump_mean_market: f64,
    /// Standard deviation of the market jump, in log-return units.
    /// pt-v21 ships 0.01.
    pub jump_sigma_market: f64,
    /// Daily probability that a per-name idiosyncratic jump fires. This is
    /// the earnings-surprise channel, independent across names.
    /// pt-v21 ships 0.009.
    pub jump_intensity_idio: f64,
    /// Standard deviation of the idiosyncratic jump, in log-return units.
    /// pt-v21 ships 0.0318.
    pub jump_sigma_idio: f64,
    /// How much of the market jump's log return joins the day's factor
    /// innovation, so the GJR variance update sees a crash day. Every
    /// shipped preset carries 0.0, where nothing is added.
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
    /// jump's log return, the same number every name's `s` took. The
    /// compensator is left out on purpose: `jump_mean_compensated` gives
    /// back a deterministic drift, the first moment, and a variance shock
    /// is a second moment. On a day no jump fires `market` is exactly 0.0
    /// and nothing is added at all.
    ///
    /// By that argument the derived value is 1.0: the whole of the jump's
    /// return belongs in the shock because the whole of it was in the
    /// returns the coefficients were fitted to. A share between the two
    /// would be claiming the fit saw part of a crash day.
    ///
    /// # It lands in the day the jump moved
    ///
    /// `apply_jumps` runs at the close, immediately after the per-name
    /// closes and before the factor's own close, so the addition lands in
    /// the shock that close computes. Nothing between the two reads or
    /// writes what `apply_jumps` touches.
    pub jump_market_variance_share: f64,
    /// How much a jump's arrival rate follows the VIX. 0.0 on pt-v1 to
    /// pt-v12, which reproduce bit for bit; pt-v13 onward ship 0.2626.
    ///
    /// Without it both intensities are per-day probabilities that ignore
    /// the regime, so the number of jump days in a dead-calm market and in
    /// a panic is the same. Decomposing the nine attribution components
    /// under a pinned VIX measured what that costs: jumps carry 40.5% of
    /// the variance of a market pinned at VIX 5 and 1.1% of one pinned at
    /// VIX 65, on 3003 and 2998 jump day-cells respectively. Real markets
    /// are the other way round, and jump clustering in crises is the
    /// documented fact this misses. It is also the floor under the calm end
    /// of the crisis lever: a market that cannot stop jumping cannot get
    /// quiet.
    ///
    /// At `c` both intensities are scaled by `1 - c + c * (vix /
    /// market_vol_vix_anchor)^2`, the same map `garch_vix_coupling` and the
    /// market factor's target use, so at the anchor the rate is exactly the
    /// shipped rate at any coupling and the mechanisms read the regime
    /// alike. `apply_jumps` draws two uniforms and two normals
    /// unconditionally whatever the rate is, so this moves a THRESHOLD and
    /// never a stream position.
    ///
    /// # It adds variance, which must be funded
    ///
    /// The scale averages ABOVE one over this model's own VIX distribution,
    /// so raising the coupling adds jump variance rather than only moving it
    /// between regimes. Measured on an undriven 504-day run: VIX mean
    /// 20.24, `E[(vix / 15)^2]` = 2.035, so the mean scale is
    /// `1 - c + 2.035c`, which is 1.725 at `c` = 0.7, meaning jumps fire 72%
    /// more often on average.
    ///
    /// Left unfunded that shows up as total volatility. At `c` = 0.7 on the
    /// pt-v10 base it takes the 252-day panel to 14 of 14 and the 504-day
    /// panel from 13 of 14 to 11, losing `annualised_vol_pct` at 34.9
    /// against a ceiling of 34.0 and `sector_excess_corr` at 0.1058 against
    /// a floor of 0.11.
    ///
    /// Fund it by scaling `jump_intensity_market` and `jump_intensity_idio`
    /// by the reciprocal of the mean scale, which is derivable rather than
    /// searched: 0.580 at `c` = 0.7, 0.491 at `c` = 1.0. That is the same
    /// bookkeeping `idio_sigma_scale` does for the market factor's variance.
    pub jump_vix_coupling: f64,
    /// The variance of the overnight move as a fraction of a session's,
    /// per name. Every shipped preset carries 0.0, where prices do not move
    /// between sessions.
    ///
    /// At 0.0 the price after `open_market` is the price after the
    /// previous `close_market` on every name-night, so the engine's
    /// overnight variance share is identically zero against a real 0.23
    /// to 0.43 (the forty-name reference panel over nine non-crisis
    /// windows, median 0.33, by `tools/calibration/overnight_band.py`).
    ///
    /// Above zero `Engine::apply_overnight` does two things at each open,
    /// before the day's marks are set. It realizes in the opening print
    /// the state that changed between the sessions, the close's jump on
    /// `s` and the macro step in fair value, instead of leaving them for
    /// the first ticks to settle toward; that is what gives the night its
    /// shape, since the reference panel's nights put 0.50 to 0.68 of their
    /// variance in the largest five percent against 0.36 to 0.49 for the
    /// sessions. And it adds a draw on `s` built exactly as a session's
    /// factor structure is, beta on the market factor at its conditional
    /// daily sigma, the sector loading on the sector factor, the name's own
    /// GARCH sigma at its idiosyncratic scale, scaled by the square root of
    /// this dial. It is built like the session's because the panel's
    /// nights and sessions read the same cross-sectional correlation. The
    /// move reverts on the mispricing's own half-life and is kept out of
    /// the momentum roll, as the jump's carried share is.
    ///
    /// The session band anchors on the post-gap open, so the gap sits
    /// outside it, as market/mod.rs defines the circuit breaker's band.
    /// A value is a ratio of variances, so 0.5 is a night carrying half a
    /// session's variance; with the jump realized at the open the share
    /// the row reads is more than the ratio alone would give.
    pub overnight_variance_ratio: f64,
    /// Cross-sectional spread in volatility persistence, in raw `beta`
    /// units. Every shipped preset carries 0.0, where it has no effect.
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
    /// `dispersion * clamp(log(cap / reference) / scale, -1, 1)`, so the
    /// reference cap gets exactly `garch_beta` and the spread is bounded by
    /// `dispersion` in both directions. Derived from the roster rather than
    /// from a draw, so the RNG stream schedule is untouched and no earlier
    /// preset's trajectory moves.
    ///
    /// Clamped so GJR persistence `alpha + beta + gamma/2` stays below
    /// [`GARCH_PERSISTENCE_CEILING`](crate::market::garch::GARCH_PERSISTENCE_CEILING).
    /// A name whose variance process is not stationary does not produce
    /// fat tails, it produces a number that grows until a guard catches it.
    ///
    /// # It does not close the decay-shape gap
    ///
    /// Measured, thirty seeds at 504 days: the log-log slope of the `|r|`
    /// autocorrelation over lags 1 to 20 reads -0.436 in real markets and
    /// -0.933 on pt-v6. At dispersion 0.15 it reads -0.944, slightly
    /// further from real. The spread shaves a little off the short lags and
    /// more off the long ones: lag 30 goes from 0.0026 to 0.0001 against a
    /// real 0.0179. A toy AR/GARCH simulation had it roughly doubling the
    /// `acf20/acf1` ratio; the engine carries a GJR term, a factor variance
    /// process and VIX coupling on top, and the result did not transfer.
    ///
    /// What it does do, measured on the same run, is improve room at 504 on
    /// `excess_kurtosis`, +0.58 to +0.74 seed-sd, and on
    /// `annualised_vol_pct`, +0.17 to +0.34. It also moves
    /// `abs_return_acf5` across its 504 band edge, but by 0.09 seed-sd,
    /// which is inside the flip margin and not a real crossing.
    pub garch_beta_dispersion: f64,

    /// How much of a jump the herding term is allowed to continue, in
    /// [0, 1]. pt-v1 to pt-v4 carry 1.0 and pt-v5 onward ship 0.0.
    ///
    /// A jump lands on `mispricing_s`, and the momentum term reads the
    /// change in `s` across closes. So at 1.0 a jump is a re-rating like
    /// any other and `momentum_theta` carries a share of it into the next
    /// day. That couples two things: the only mechanism that reaches the
    /// 504-day tail also pushes 252-day return autocorrelation out of its
    /// band, because fattening the tail and adding continuation are the
    /// same act.
    ///
    /// This dial splits them. At `1.0` the jump feeds herding in full,
    /// which is why pt-v1 to pt-v4 reproduce bit for bit. Below `1.0` the
    /// jump moves the momentum reference point with it, so herding sees the
    /// post-jump level as the new baseline rather than as a change to
    /// continue. At `0.0` the jump is invisible to momentum: it decays on
    /// `s_phi` alone, giving a fat tail with no continuation attached to it.
    ///
    /// The jump's own mean reversion is unchanged either way; it always
    /// decays back through the existing mispricing process. What moves is
    /// only whether the herding term amplifies it on the way.
    pub jump_momentum_share: f64,

    // ── Persistent volume (engine.rs close, market/tick.rs phase 3) ─────
    /// Day-to-day persistence of the shared volume component, in [0, 1).
    ///
    /// At 0.0 volume is a LEVEL, `avg_volume` scaled by multipliers, with
    /// an independent uniform each tick. Consecutive volumes are then
    /// near-independent draws around a fixed level, and differencing that
    /// gives a change autocorrelation near -0.5 at ANY coefficients. That is
    /// why `volume_change_acf1` sits 13.7 seed-sd outside a real band of
    /// -0.32 to -0.20 without it, and no other parameter can reach that row.
    ///
    /// This supplies the process: a log-scale AR(1) multiplier, so a busy
    /// day is followed by a busy day. It models the COMMON component only,
    /// market-wide volume persistence shared by every name. Real volume
    /// persistence is partly idiosyncratic too (a name in play stays in
    /// play), and that part is not modeled here, because per-name state
    /// would touch the column and checkpoint surface for a second-order
    /// effect.
    ///
    /// 0.0 with a zero innovation leaves the multiplier at exactly 1.0 and
    /// the branch is skipped, which is pt-v1 to pt-v3. pt-v10 onward ship
    /// 0.7.
    pub volume_persistence: f64,
    /// Standard deviation of the daily log-volume innovation.
    pub volume_innovation_sigma: f64,

    // ── Continuous size effect (market/factors.rs) ──────────────────────
    /// Blend from the four-tier size step toward a continuous power law,
    /// in [0, 1].
    ///
    /// The step function gives a $49B company 1.0 and a $51B company 0.8,
    /// a 25% jump in idiosyncratic volatility from a $2B difference. Every
    /// name lands on one of four levels, which puts cliffs in the
    /// cross-section that no real market has and compresses the dispersion
    /// of volatility across names into four spikes.
    ///
    /// 0.0 returns the step value by branch, and every shipped preset
    /// carries 0.0. 1.0 is the pure power law.
    pub size_effect_smoothness: f64,
    /// Exponent of the continuous size effect: `(cap / 25B) ^ -exponent`.
    ///
    /// Ships at 0.15, fitted to the step function's own tiers where those
    /// tiers are informative: 5B reads 1.273 against a step of 1.3, 25B
    /// reads 1.000 against 1.0, 100B reads 0.812 against 0.8. It departs
    /// below $1B, where the step stops being a size effect and becomes a
    /// floor, and that departure is the mechanism's purpose.
    pub size_effect_exponent: f64,
    /// Blend from the four-tier spread step toward a continuous power law,
    /// in [0, 1].
    ///
    /// The step charges 10 bps at $1B and 30 bps at $0.9B, a three times
    /// jump in transaction cost from a rounding error in capitalization. Any
    /// execution study spanning that edge measures the tier rather than the
    /// size effect. 0.0 is the step, and every shipped preset carries it.
    pub spread_size_smoothness: f64,
    /// Exponent of the continuous spread curve. Ships at 0.455, least-squares
    /// fitted to the step's own four tiers: 29.65 bps against 30 at $0.5B,
    /// 10.40 against 10 at $5B, 5.00 against 5 at $25B, 2.66 against 3 at
    /// $100B.
    pub spread_size_exponent: f64,

    // ── The agent-facing book (agent_book.rs, engine.rs) ─────────────────
    //
    // Dials 0.0 on every preset through pt-v19, and every one read only
    // on the path an AGENT's order takes; pt-v20 sets seven of them and
    // pt-v21 sets all sixteen, at the values each dial's note gives. The
    // market's own flow settles through the maker's ladder exactly as it
    // always has, so an untraded run is bit-identical at any setting of any
    // of them: they change what an agent pays and what it does to the
    // market, never the market nobody traded. `agent_book.rs` carries the
    // model and its sources; the notes here say what each dial moves.

    /// Coefficient `Y` of the square-root law that sets the latent depth
    /// behind the maker's ladder. pt-v20 ships 0.75; 0.0 turns the latent
    /// depth off.
    ///
    /// At 0.0, which pt-v1 to pt-v19 carry, there is no depth past the
    /// maker's ten levels, so an order larger than the ladder fills what
    /// the ladder holds and drops the rest (whole-book depth is 2.6 to 5%
    /// of daily volume per side). Off zero, levels are appended behind the
    /// ladder out to `book_depth_reach` times daily volume, with the
    /// cumulative depth to a price distance `d` from the touch set so that
    /// the MARGINAL price of the `Q`-th share is never below
    /// `touch * (1 + Y sigma (Q/V)^delta)`: `sigma` the name's conditional
    /// daily volatility, `V` its average daily volume and `delta`
    /// [`ModelParams::book_depth_exponent`]. An order walks those levels at
    /// worse prices instead of being cut off.
    ///
    /// That is the latent order book of Toth, Lemperiere, Deremble, de
    /// Lataillade, Kockelkoren and Bouchaud (Physical Review X 1, 021006,
    /// 2011): depth that grows linearly with distance from the price gives
    /// a peak impact `Y sigma sqrt(Q/V)`, with `Y` of order one in their
    /// data. The average cost of walking such a book is `delta / (1 +
    /// delta)` of the marginal, two thirds at the square root.
    pub book_depth_coefficient: f64,
    /// The exponent `delta` of the latent depth's price-for-size law. Read
    /// only with [`ModelParams::book_depth_coefficient`] off zero, and
    /// refused off zero without it. 0.0 reads as 0.5, the square root of
    /// Toth et al. (2011), so the coefficient alone turns the law on.
    /// pt-v20 ships 0.5. Almgren, Thum, Hauptmann and Li (Risk 18(7) 58-62,
    /// 2005) measure 0.6 on the temporary cost of US equity executions.
    pub book_depth_exponent: f64,
    /// How far the latent depth reaches, in multiples of the name's average
    /// daily volume per side. Read only with
    /// [`ModelParams::book_depth_coefficient`] off zero, and refused off
    /// zero without it. 0.0 reads as one day's volume, and pt-v20 ships
    /// 1.0. An order past the reach is cut off there, as an order past the
    /// ladder is without the tail.
    pub book_depth_reach: f64,
    /// How much of the maker's ladder the latent depth counts as its own
    /// front, in [0, 1]. Read only with
    /// [`ModelParams::book_depth_coefficient`] off zero, and refused off
    /// zero without it.
    ///
    /// 0.0, which every preset through pt-v20 carries: the latent pool sits
    /// BESIDE the ladder, its `Q`-th share priced on the law as if the ladder
    /// were not there, so the depth within a distance `d` of the touch is the
    /// ladder's plus the law's. Near the touch the ladder holds 0.3 to 1% of
    /// daily volume at its first level and 2.6 to 5% over ten, so an immediate
    /// order of 3% of daily volume fills mostly from the ladder and the law's
    /// own front at once, paying the half-spread and little more: on N4
    /// (sim/r14) a day TWAP at 3% costs 0.83 of such a block against the 0.5 to
    /// 0.8 of Almgren et al. (2005) and Bacry et al. (2015), because the
    /// half-spread every share pays is half the block's cost.
    ///
    /// 1.0: the latent curve counts every ladder share at a price as good
    /// or better as already on it, so the depth within `d` is the larger of
    /// the ladder's and the law's, not their sum. The ladder is the
    /// displayed front of the latent book (Toth et al. 2011; Bouchaud,
    /// Bonart, Donier and Gould, Trades, Quotes and Prices, 2018, ch. 19),
    /// not a second book beside it: the law holds for everything past it,
    /// and past the ladder the next share is priced where the law puts the
    /// shares already taken. Between 0 and 1 that fraction of the ladder
    /// is counted. Read only in the book an agent meets, so no untraded
    /// statistic moves; a slice smaller than the first level pays exactly
    /// what it did.
    /// pt-v21 ships 1.0.
    pub book_depth_nesting: f64,
    /// Switch for whether agents' orders consume the book they share: 1.0
    /// on, 0.0 off. pt-v20 ships 1.0.
    ///
    /// At 0.0, which pt-v1 to pt-v19 carry, an agent's order is priced
    /// against the book and removes nothing from it (`Portfolio.execute`
    /// reads `sweep_cost`), so two agents buying the same name in one step
    /// fill at the same price against the same levels, and the maker's
    /// inventory never hears about an agent.
    ///
    /// At 1.0 an agent's order executes in the engine. The levels it takes
    /// are gone for every agent after it until they refill: the maker's
    /// ladder at the next tick, when the maker re-quotes, and the latent
    /// depth at `book_refill_half_life`. Its fills against the maker are
    /// the maker's trades too, so the maker's inventory moves and it skews
    /// its quotes until opposing flow unwinds it, exactly as it does for
    /// the model's own flow.
    pub book_shared: f64,
    /// Half-life in ticks at which consumed LATENT depth refills. Read only
    /// with `book_shared` on and the depth tail on, and refused off zero
    /// otherwise. 0.0 refills it at the next tick, with the maker's ladder.
    /// pt-v20 ships 27.
    ///
    /// Obizhaeva and Wang (Journal of Financial Markets 16(1) 1-32, 2013)
    /// model a book whose consumed depth recovers exponentially;
    /// Alfonsi, Fruth and Schied (Quantitative Finance 10(2) 143-157, 2010)
    /// show that when the recovery acts on the consumed VOLUME, as here,
    /// a book of any shape admits no profitable round trip. The half-life
    /// is the one free number; see `agent_book.rs` for the value derived.
    pub book_refill_half_life: f64,
    /// Switch for whether an agent's unfilled limit order rests in the
    /// book: 1.0 on, 0.0 off. pt-v20 ships 1.0.
    ///
    /// At 0.0, which pt-v1 to pt-v19 carry, an unfilled limit waits outside
    /// the book and fills in full at its limit when a later print reaches
    /// it (the traded-range convention). It takes no queue, meets no flow,
    /// and no other agent can trade against it.
    ///
    /// At 1.0 the remainder rests at its price with time priority behind
    /// the depth already there, including the maker's, which re-quotes every
    /// tick and is therefore always ahead at an equal price. It fills when
    /// the model's own flow or another agent's order trades through it,
    /// partially when that flow is smaller than the queue ahead of it plus
    /// the order, and at its own price. Cancelling removes it.
    pub book_resting: f64,
    /// Whether the arrival order of a cohort's orders at the shared book is
    /// a seeded shuffle, fresh every step. A switch.
    ///
    /// 0.0, which every preset through pt-v20 carries: a `World` cohort
    /// executes in sorted label order on every step, so on a live book
    /// (`book_shared`) the same label always takes the levels first and stands
    /// first in the resting queue. The label is then a latency advantage nobody
    /// chose: on pt-v20's graded arm the later label of two identical
    /// 10%-of-ADV buyers pays about 23 bp more on 30 of 30 held-out seeds, and
    /// two identical momentum agents split by about 4 per cent in 20 days.
    ///
    /// 1.0: each step's order is the labels sorted by
    /// [`crate::rng::arrival_priority`], a counter-based function of the
    /// run's seed, the world's day, the step within the day and the label.
    /// Each label is first equally often and the order is independent from
    /// step to step, so a name buys no priority (Nasdaq Rule 4757 ranks
    /// orders by price and then time, never by identity; agent-based
    /// toolkits reshuffle the activation order every step, Axtell 2001).
    /// The priority is pairwise, so removing or freezing an agent never
    /// reorders the others. It holds no state and takes no draw from any
    /// stream, so nothing is snapshotted, restored or hashed, and no market
    /// draw moves. Read only by a cohort (`World` with two or more agents):
    /// a single agent, `evaluate` and an untraded market never read it.
    /// Off the live book it orders only `World.rejected`.
    /// pt-v21 ships 1.0.
    pub book_arrival_shuffle: f64,
    /// Whether an agent's resting order that the book has left crossed
    /// during the session trades at its OWN limit. A switch.
    ///
    /// 0.0, which every preset through pt-v20 carries: an order the maker's
    /// re-quote leaves crossed (a resting ask below the maker's new bid) trades
    /// against the ladder at the ladder's prices before the tick's flow, so the
    /// price improvement goes to the resting order.
    ///
    /// 1.0: it trades at its own limit, as a continuous book fills a
    /// standing order at its price and gives any improvement to the order
    /// that arrives, here the maker's re-quote (Nasdaq Rule 4757, NYSE
    /// Pillar 7.36-7.37). It is still taker flow, and still pays its
    /// impact. Without it an agent that pushes the price up with taker buys
    /// while resting asks at the touch sells those asks at the maker's
    /// higher bid, more than it paid for the same shares a tick earlier:
    /// the round-trip guard's wash on a name quoted a cent wide (R16A,
    /// held-out seeds). The open is unchanged: an order the night's gap
    /// went through fills at the opening ladder's prices, as an opening
    /// auction would fill it.
    /// pt-v21 ships 1.0.
    pub book_cross_at_limit: f64,
    /// Permanent impact of an agent's fills, linear in size: `gamma` in
    /// `ds = gamma * sigma * (bought - sold) / V`, applied to the name's
    /// mispricing `s` once, on the first tick after the fills. pt-v20
    /// ships 0.314.
    ///
    /// At 0.0, which pt-v1 to pt-v19 carry, fills go through the
    /// order-imbalance law the model's standing flow uses
    /// (`order_flow_impact`), which is concave and floored: a one-share
    /// order carries the imbalance floor of 0.2, worth up to 0.9 bp of `s`
    /// in the thinnest names, so a trader holding a position can lift its
    /// mark with a stream of one-share buys at almost no cost. Off zero the
    /// law is linear, which is the condition Huberman and Stanzl
    /// (Econometrica 72(4) 1247-1275, 2004) prove necessary for permanent
    /// impact to admit no price-manipulation round trip, and it is additive
    /// across agents, so each agent's share of a name's impact is exact.
    /// Almgren, Thum, Hauptmann and Li (2005) measure `gamma` = 0.314 on the
    /// same form.
    /// pt-v21 ships 0.15.
    pub fill_impact_coefficient: f64,
    /// The metaorder memory's `Y`: the square-root law of impact on the TAPE,
    /// for agents' orders. 0.0, which every preset through pt-v20 carries, is
    /// off, and the market is the one it was to the bit.
    ///
    /// Without it an agent's temporary impact never reaches the print: the
    /// maker re-quotes a full ladder around the model price every tick, so
    /// only `fill_impact_coefficient`'s linear `gamma sigma Q/V` moves `s`.
    /// Measured on pt-v20 (held-out seeds 2001-2008, six names), the peak
    /// displacement of a half-day order is linear in its size, `0.42
    /// f^1.04` sigma in the print, and 99% of it is still there at the
    /// close. The square-root law of impact is concave, `Y sigma (Q/V)^0.5`
    /// with `Y` about 0.4 to 0.5 (Toth et al., Physical Review X 1, 021006,
    /// 2011; Zarinelli, Treccani, Farmer and Lillo, Market Microstructure
    /// and Liquidity 1(2), 2015; Bucci, Benzaquen, Lillo and Bouchaud,
    /// Physical Review Letters 122, 108302, 2019), and about a third of the
    /// peak has gone by the close (Bucci et al., "Slow decay of impact in
    /// equity markets", 2019).
    ///
    /// Off zero, each name keeps a decaying memory `M` of agents' net taker
    /// flow against the house (the maker and the latent depth; a fill
    /// between two agents is not flow to the market at all with the memory
    /// on) in fractions of daily volume, and its `s` carries `D = sign(M)
    /// Y sigma h(|M|)`, with `h(m) = m^delta` (`delta` the latent book's
    /// [`ModelParams::book_depth_exponent`]) and `sigma` the name's
    /// [`crate::agent_book::daily_sigma`]. The latent depth continues from
    /// the memory's point on its own curve, so an order in the direction
    /// the memory leans walks on from where the last one left the price.
    /// This is the volume-recovery book of Alfonsi, Fruth and Schied
    /// (Quantitative Finance 10(2), 2010) with a book linear in distance.
    /// Needs `book_shared` on and the depth tail, and is at most
    /// `book_depth_coefficient`, which keeps every fill no better than the
    /// memory's own price. In [0, book_depth_coefficient].
    /// pt-v21 ships 0.65.
    pub impact_memory_coefficient: f64,
    /// The memory's fast half-life, in OPEN ticks. Required with the
    /// coefficient on, and read by nothing with it off (like each dial
    /// below, and like `fair_value_vix_knee` beside its discount, it is
    /// allowed there, unread). The memory decays only on
    /// open ticks, so it holds overnight (Bucci et al. 2019 measure the
    /// decay in volume time). In (0, 39000].
    /// pt-v21 ships 12.
    pub impact_memory_half_life: f64,
    /// The memory's slow half-life, in open ticks. 0.0 is no slow part;
    /// otherwise at least the fast half-life. The slow part is the
    /// long-lived remainder of impact, about 0.3 to 0.4 of the peak after
    /// weeks (Bucci et al. 2019) and 0.15 after 15 days once the trader's
    /// information is removed (Brokmann et al., Market Microstructure and
    /// Liquidity 1(2), 2015). In [0, 98280].
    /// pt-v21 ships 780.
    pub impact_memory_slow_half_life: f64,
    /// The slow part's weight `w` in `M = (1 - w) M_fast + w M_slow`.
    /// Refused off zero without the slow half-life. In [0, 1).
    /// pt-v21 ships 0.1.
    pub impact_memory_slow_weight: f64,
    /// The crossover `m*`, in fractions of daily volume (a size, not a
    /// participation rate), below which the memory's displacement is
    /// linear, `m m*^(delta - 1)`, rather than a power: on ANcerno
    /// metaorders impact is close to a square root for volume fractions
    /// from about 1e-3 to 1e-1 and about linear below 1e-3 (Zarinelli,
    /// Treccani, Farmer and Lillo 2015; Bucci, Mastromatteo, Eisler, Lillo,
    /// Bouchaud and Lehalle 2018; both as reported by Bucci, Benzaquen,
    /// Lillo and Bouchaud, Physical Review Letters 122, 108302, 2019, whose
    /// own crossover, about 3e-3, is in the participation rate). The linear
    /// foot also keeps the power law's infinite marginal impact at zero
    /// size from rewarding a stream of tiny orders. 0.0 is a pure power
    /// law. In [0, 0.05].
    /// pt-v21 ships 0.001.
    pub impact_memory_crossover: f64,
    /// Whether an agent's resting order that fills AGAINST the metaorder
    /// memory's lean refills the memory. A switch.
    ///
    /// 0.0, which every preset through pt-v20 carries: only taker flow against
    /// the house moves the memory. A resting order the market's own flow fills
    /// does not, and one the maker's re-quote leaves crossed is taker flow
    /// whose move is capped by what it paid, which at its own limit is about
    /// nothing.
    ///
    /// 1.0: a resting order filled on the side against the lean (an ask
    /// while agents' flow has displaced the price up, a bid while it has
    /// displaced it down), by the market's flow or by a crossing during the
    /// session, takes its size off the memory, uncapped, never past zero.
    /// A crossed order's shares beyond zero are taker flow as before, and
    /// every crossed share still pays the linear law. On the lean's own
    /// side nothing changes: a bid filled under a buy lean adds depth the
    /// lean did not take.
    ///
    /// Why. Without it, a group of agents that buys as a taker and sells
    /// the same shares back through asks resting at the touch ends flat,
    /// but the memory has seen only the buys, and a third agent sells a
    /// holding into the displacement. The round-trip guard's wash
    /// (`metaorder_curve.py trips`) found it on names quoted a cent wide,
    /// where the inside ask joins the maker's queue at the touch: +0.51 bp
    /// on the thirteenth grade and up to +18 bp on held-out seeds (R16A).
    /// In the volume-recovery book of Obizhaeva and Wang (Journal of
    /// Financial Markets 16(1), 2013) and Alfonsi, Fruth and Schied (2010)
    /// the displacement is the consumed depth, and an order resting on the
    /// consumed side is new depth there; Eisler, Bouchaud and Kockelkoren
    /// (Quantitative Finance 12(9), 2012) measure a limit order's impact
    /// with the sign opposite to a market order's on its side. Taker orders
    /// are unchanged. Read only with `impact_memory_coefficient` on.
    /// pt-v21 ships 1.0.
    pub impact_memory_refill: f64,

    // ── Crisis gates (economy/daily.rs, market/tick.rs, engine.rs) ──────
    /// How fast VIX reverts toward its target, as the fraction of the gap
    /// closed each day. pt-v19 and pt-v20 ship 0.27.
    ///
    /// This sets how long a VIX spike lasts. The variance process has a
    /// 63-day half-life and cannot track a twenty-day VIX spike, and a
    /// faster variance process was measured to cost long-horizon realism.
    /// A longer or shorter spike set here does not touch variance
    /// persistence. Earlier presets carry 0.12 (pt-v1 to pt-v15), 0.06
    /// (pt-v16 and pt-v17) and 0.1 (pt-v18).
    pub vix_mean_reversion: f64,
    /// Multiplier on the VIX mean reversion on days the target sits below
    /// the current VIX, so fear can decay more slowly than it arrives.
    /// Rising days keep the full rate. 1.0 is symmetric reversion; pt-v16
    /// to pt-v18 ship 0.6 and every other preset carries 1.0. In real
    /// markets, up-moves average 1.20x the size of down-moves (2004-2025).
    pub vix_decay_ratio: f64,
    /// Rate of exogenous fear events, per year, each a jump in the VIX
    /// level. Every shipped preset carries 0.0, which turns them off.
    ///
    /// Real VIX spikes often arrive from news rather than accumulated
    /// market moves, and the target's small Gaussian noise cannot make that
    /// tail: P(VIX>30) reads about 0.007 endogenous against a real 0.082.
    /// Above zero a rare jump lands directly on the VIX level (a jump in
    /// the target would be absorbed by the mean reversion before it showed)
    /// and decays through the slow side of
    /// [`ModelParams::vix_decay_ratio`], up fast and down slow, like fear.
    /// At 0.0 no random draws are taken, so the schedule and every recorded
    /// run reproduce bit for bit.
    pub vix_jump_intensity: f64,
    /// Mean size of a fear event, in VIX points (exponential draw).
    pub vix_jump_scale: f64,
    /// How the down-side fear response falls with the VIX the session opened
    /// from: the spike is `gain * |r|^p * VIX^(-this)`.
    ///
    /// # The measurement
    ///
    /// On ^GSPC and
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
    /// (`g = 0` on both sides, which pt-v1 to pt-v18 run) is rejected at
    /// F(2, 8951) = 118, and the multiplicative "ratio" form `dVIX/VIX ~ r`
    /// is rejected harder still (F = 588): on the down side the response in
    /// points FALLS with the level, as `VIX^(-0.49)`, and the level
    /// exponent is the same in every decade (0.47, 0.50, 0.47, 0.47 for the
    /// 1990s to the 2020s) while the scale `b` moves by +/- 20 percent.
    ///
    /// # The form the tape supports, and what this dial's value is
    ///
    /// `g_d = p_d - 1` is inside the error bar (0.44 against 0.49 +/-
    /// 0.12; the restriction costs 0.15 percent of the weighted SSR), and
    /// it is the standardized-return form: `dVIX = gain VIX (|r| / VIX)^p`,
    /// the VIX responding in proportion to itself to the return measured in
    /// units of itself. It carries no reference level, so the gain has a
    /// meaning without one. The derived value is therefore
    /// `vix_return_exponent - 1` = **0.448** at the derived exponent 1.448,
    /// and the gain that reproduces the tape's same-day response is
    /// `3.754 / vix_mean_reversion` (the update transmits `mr` of the
    /// target spike on the day), less the part the identity's read-back
    /// already contributes. pt-v19 and pt-v20 ship 0.4483.
    ///
    /// 0.0, which pt-v1 to pt-v18 carry, is a branch:
    /// `economy::daily::return_spike_at_level` returns `return_spike_for`
    /// there and the level is never read.
    pub vix_return_level_exponent: f64,
    /// The up side's own exponent: the spike on an up session is
    /// `-gain_up * |r|^this * VIX^(-vix_return_level_exponent_up)`.
    ///
    /// Measured with the down side above: **0.596 +/- 0.037** whole span
    /// (0.49 to 0.66 by decade), and 0.543 in the restricted form this
    /// dial and its partner implement; pt-v19 and pt-v20 ship 0.5433. The
    /// up side is CONCAVE: a +2 percent session lowers the VIX by less than
    /// twice what a +1 percent one does. A fit with no level term reads
    /// 1.04, because big up sessions come at high levels. 1.0, which pt-v1
    /// to pt-v18 carry, is the linear form, and is bit-identical with the
    /// two level exponents at 0.0.
    pub vix_return_exponent_up: f64,
    /// How the up-side response scales with the level. Measured
    /// **-0.852 +/- 0.122** (the response in points rises with the level,
    /// nearly in proportion), against which the ratio form `-1.0` is within
    /// 1.2 standard errors and is what the restricted form carries:
    /// `-gain_up * VIX * |r|^p_u`, the VIX giving back a fraction of itself
    /// on an up session. The two sides are different laws (surprise in units
    /// of priced volatility on the way up, proportional relief on the way
    /// down), and the tape distinguishes them at F(2, 8951) = 43. 0.0 is the
    /// level-blind branch, which pt-v1 to pt-v18 carry; pt-v19 and pt-v20
    /// ship -1.0.
    pub vix_return_level_exponent_up: f64,
    /// Base scale of the VIX's own daily innovation, as a fraction of its
    /// level. Every shipped preset carries 0.0.
    ///
    /// # Why it exists
    ///
    /// With both innovation dials at 0.0 the noise is `N(0, 0.15)` POINTS,
    /// under one percent of the level, and `vix_jump_intensity` is 0.0 on
    /// every preset, so the model's VIX is an image of its own index
    /// return: corr(dVIX, r) -0.977 against the tape's -0.804, R^2 0.954
    /// against 0.646, the sd of the daily log change 0.105 against 0.064
    /// and its excess kurtosis 0.16 against 2.66. Once the response form
    /// above is removed from the tape's dVIX, what is left is proportional
    /// to the level (`log|e| ~ 1.08 log VIX`, bootstrap sd 0.06) and its
    /// scale grows with the session's own size:
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
    /// expression, and the draw is the same draw, so pt-v1 to pt-v18, which
    /// carry both at 0.0, reproduce to the bit. pt-v19 and pt-v20 carry
    /// 0.0 here and 0.0175 on the partner.
    pub vix_innovation_sigma: f64,
    /// The part of the innovation scale that rises with the session return,
    /// per percent: see `vix_innovation_sigma`. Measured 0.0179 [0.0135,
    /// 0.0232]; pt-v19 and pt-v20 ship 0.0175. What it buys, on the tape's
    /// own returns: the per-window excess kurtosis of the daily log VIX
    /// change reads 1.48 with `s0` alone and 2.09 with both, against 2.66
    /// measured. It is also the component the whole-span kurtosis of 6.9
    /// mostly comes from.
    pub vix_innovation_return_sigma: f64,
    /// A fear event's mean size in units of the day's innovation scale
    /// (`VIX * sqrt(s0^2 + (c r)^2)`, or `VIX` when the innovation dials
    /// are off), exponential draw. Non-zero selects that unit over
    /// `vix_jump_scale`'s points. pt-v19 and pt-v20 ship 1.7.
    ///
    /// Derived by cumulant inversion on the scale-standardized within-
    /// window residual `z`: with `kappa_3 = 6 lam mu^3` and `kappa_4 = 24
    /// lam mu^4` for a Poisson(lam) count of Exp(mu) jumps on a Gaussian,
    /// `mu = kappa_4 / (4 kappa_3)` = **1.70** and `lam = kappa_3 / (6
    /// mu^3)` = 0.0089 a day (2.24 a year), leaving the Gaussian 94.9
    /// percent of the variance. The closed form is checked against 2-D
    /// quadrature of the density to 2e-13 with a negative control that
    /// fails at 2e-2. The skew of `z` is 0.26 +/- 0.04 and its excess
    /// kurtosis 1.77 +/- 0.21 (window bootstrap), so the innovation is not
    /// Gaussian at seven standard errors; the rate's own error bar is wide
    /// (bootstrap 0.05 to 3.8 a year) because two decades hold most of the
    /// events.
    pub vix_jump_level_scale: f64,
    /// The part of the fear-event arrival rate, per year per percent of down
    /// session, that rises with the session: the daily probability is
    /// `(vix_jump_intensity + this * max(0, -r)) / 252`. pt-v19 and pt-v20
    /// ship 6.199.
    ///
    /// The tape's residual skew on down sessions rises with the size of the
    /// session (0.38, 0.46, 0.59, 1.76 for |r| under 0.5, 0.5-1, 1-2, 2-3
    /// percent within windows) and is flat on up sessions, so the events
    /// cluster with the crashes rather than arriving on their own clock. At
    /// the derived mean rate of 2.24 a year spread this way the value is
    /// **6.2** (0.0246 a day per percent). Non-zero takes the arrival draw.
    /// With both intensities at 0.0 no draw is taken and the schedule is
    /// the shipped one.
    pub vix_jump_return_intensity: f64,
    /// Shock weight (alpha) of the per-sector GARCH(1,1) variance state.
    /// pt-v19 and pt-v20 ship 0.067; with this and `sector_vol_beta` both
    /// at 0.0 the state is off.
    ///
    /// The sector factor's daily variance is `T * s`, with `T` the
    /// VIX-coupled sigma squared the stateless draw uses
    /// (`tick::sector_sigma_at`) and `s` a symmetric GARCH(1,1) of
    /// unconditional mean 1 on the factor standardized by `T`:
    /// `s' = (1 - a - b) + a d^2 / T + b s`, `d` the day's accumulated
    /// sector factor, `s` clamped to the per-name multiples. The ratio form
    /// keeps the coupling's own state whole (at `sector_vix_coupling` 1.0
    /// the target already tracks the VIX) and adds the sector's memory net
    /// of it. Either dial non-zero switches the state on. At (0.0, 0.0) the
    /// tick draws at `sector_sigma_at` exactly as before and the read-back
    /// prices the same scalar.
    ///
    /// Measured: a GARCH(1,1) QMLE on each of seven sectors' market
    /// residuals of the 40-name reference panel, 2015-2025, divided by the
    /// VIX close the session opened from over its median, reads alpha
    /// **0.067 +/- 0.043**, beta 0.837 +/- 0.111, persistence 0.904 +/-
    /// 0.069 (half-life about seven sessions). The plain fit without the
    /// VIX division (alpha 0.063, beta 0.908, persistence 0.971) absorbs
    /// the VIX's own level into beta; added on top of the coupled target it
    /// smoothed that channel away and the clustering rows fell.
    pub sector_vol_alpha: f64,
    /// The per-sector variance state's persistence term, **0.837** by the
    /// same measurement, which pt-v19 and pt-v20 ship. See
    /// [`ModelParams::sector_vol_alpha`].
    pub sector_vol_beta: f64,
    /// The per-NAME idiosyncratic variance state's shock share. A RATIO of
    /// unconditional mean one, the same form as the sector state
    /// (`sector_vol_alpha`): the variance of the name's own idiosyncratic
    /// draw is its GJR variance times `s`, with
    /// `s' = (1 - a - b) + a u^2 + b s + c (I - lambda)` at the close,
    /// clamped to [`garch_floor_multiple`, `garch_ceiling_multiple`]. `u` is
    /// the session's own NOISE (the own-noise part the ticks drew) over its
    /// expected sd, `kappa^2 max(h, idio_sigma_floor)`, where `kappa^2`
    /// (`noise_own_scale2`) already carries `s`, so `E[u^2] = 1` whatever
    /// `s` is. The jump term is `idio_vol_jump_bump` (`c`): `I` is whether
    /// the name's own jump was realised this session and `lambda` the rate
    /// it was drawn at. The tick scales the own draw by `sqrt(s)` through
    /// the per-company volatility multiplier; the overnight own draw and
    /// the VIX identity's idiosyncratic term read the same `s`. The per-name
    /// GJR, the market factor and the VIX process are untouched.
    ///
    /// Why: the per-name GJR is fed the day's NOISE (market, sector and own
    /// together), never the name's own jumps or news, so a name's large
    /// idiosyncratic move has no aftershock. On the forty-name reference
    /// panel (2015-2025, ten 252-return windows, residual on the
    /// leave-one-out equal-weight roster) the idiosyncratic `|e|` lag-1
    /// ACF reads a median 0.088 (se 0.010, every window at least 0.068)
    /// and the mean `|e|` the day after a top-2.5% day is 1.29x the mean
    /// (se 0.062, every window at least 1.18); pt-v20 reads 0.029 and 1.05
    /// on held-out seeds. The panel's own excitation measurement (see
    /// `jump_idio_excitation`) gives the fast response a half-life of about
    /// two sessions, which is what this state carries; the per-name long
    /// lags are already above the tape's, so the missing memory is short.
    ///
    /// Any of the three dials non-zero switches the state on. At (0.0, 0.0,
    /// 0.0), which every preset through pt-v20 carries, no state is read or
    /// written, the tick receives an empty ratio slice and nothing is
    /// multiplied: every trajectory and digest is the one it was. The snapshot
    /// and the state hash carry the state (the ratio, the own jump pending for
    /// tomorrow's shock and its expected variance) only while it runs. `a >=
    /// 0`, `b >= 0`, `a + b < 1`. pt-v21 ships 0.25.
    pub idio_vol_alpha: f64,
    /// The per-name idiosyncratic variance state's persistence term. See
    /// [`ModelParams::idio_vol_alpha`].
    /// pt-v21 ships 0.5.
    pub idio_vol_beta: f64,
    /// The per-name idiosyncratic variance state's jump channel: the
    /// session after the name's own jump is realised, the ratio moves by
    /// `c (1 - lambda)`, and by `-c lambda` on every other session, `lambda`
    /// the own-jump probability the name was drawn at. Re-centred on the
    /// rate, so the ratio's mean stays one. The diffusive shock `u^2` above
    /// reads the own noise only: fed the jump itself, `u^2` runs to tens on
    /// a jump session and the ceiling clamp (`garch_ceiling_multiple`) takes
    /// the excess, which put the ratio's mean at 0.86 on pt-v20 (40 names,
    /// 110 sessions; 1.00 with the idiosyncratic jumps off). 0.0 is no jump
    /// channel; `0 <= c <= 4`. Off zero alone it switches the state on.
    /// pt-v21 ships 1.0.
    pub idio_vol_jump_bump: f64,
    /// Self-excitation of a name's idiosyncratic jumps: after a jump the
    /// name's arrival rate is `lambda (1 + h)` with `h' = decay h + this`.
    /// 0.0, which every shipped preset carries, is a branch: the rate is
    /// `lambda` and the state is never read or written. The arrival test
    /// takes the same uniform per name per session at every rate, so no
    /// draw moves either way.
    ///
    /// Measured: on the reference panel the rate of a 3-sd idiosyncratic
    /// move at lag k after one reads 3.29, 1.84, 2.13, 1.68, 1.58 times the
    /// base at k = 1..5 and 1.0 by k = 12, fitted as
    /// `1 + 2.0 x 0.72^(k-1)`: amplitude **2.0** [1.5, 2.8], branching ratio
    /// 0.13 (stationary by a wide margin). The model's independent arrivals
    /// read a lag-one ratio of 1.7 with no memory past a day.
    pub jump_idio_excitation: f64,
    /// The excitation's daily decay, **0.72** [0.48, 0.79] (half-life two
    /// sessions) by the same measurement. Unread while
    /// [`ModelParams::jump_idio_excitation`] is 0.0.
    pub jump_idio_excitation_decay: f64,
    /// Switch that takes the VIX-squared scaling (`jump_vix_coupling`) off
    /// the idiosyncratic arrival rate, leaving it on the market jump.
    /// Every shipped preset carries 0.0, which keeps the scaling on both.
    ///
    /// Measured: the panel's idiosyncratic jump rate, in units of the
    /// name's own trailing sd, reads `var^-0.20` against the name's own
    /// variance and `var^0.05` against the market's. That is close to flat,
    /// so the data do not support scaling this rate with VIX squared.
    pub jump_idio_vix_decoupled: f64,
    /// Common forced-selling flow in stress: a log-shock per VIX point
    /// above `forced_flow_threshold`, per day, applied identically to every
    /// name. Every shipped preset carries 0.0, which turns it off.
    ///
    /// It models forced, correlated selling above a fear threshold, the
    /// real-market mechanism proposed to explain why a crash's co-movement
    /// is so tight (real 2020 crash co-movement 0.781, this model 0.22 to
    /// 0.73 across seeds). It is applied alongside the crowd lean. The term
    /// varies only as the VIX varies, so a replayed crash (VIX moving from
    /// 30 to 80) receives strong common motion while the held-VIX crisis
    /// instrument (constant 45) receives a constant drift and near-zero
    /// added correlation. 0.0 is a branch and changes nothing.
    pub forced_flow_gain: f64,
    /// VIX level, in points, above which forced flow applies. Every shipped
    /// preset carries 40. Below it the term is absent, so forced flow has
    /// no effect in calm markets.
    pub forced_flow_threshold: f64,
    /// How unevenly forced selling lands across names, as an exponent on
    /// beta. 0.0 on every shipped preset, which is the uniform lean.
    ///
    /// At `k` the per-name lean is the common forced-flow term times
    /// `beta^k`, with beta floored at 0.1. At 0.0 the factor is 1.0 through
    /// a branch, with no pow call, so the uniform behavior reproduces bit
    /// for bit.
    ///
    /// A uniform lean pushes every name the same way at the same time. In a
    /// measured run it raised crash cohesion from 0.52 to 0.69 and halved
    /// its IQR, and cut crash dispersion from 0.48 to 0.34 of real. Real
    /// forced selling is uneven: leveraged and high-beta names get sold
    /// hardest. Read only while `forced_flow_gain` is non-zero, and every
    /// shipped preset sets that to 0.0.
    pub forced_flow_beta_exponent: f64,
    /// Total forced-selling budget, in VIX-point-days. 0.0, on every shipped
    /// preset, means the forced sellers never run out.
    ///
    /// Each day the VIX is above `forced_flow_threshold` spends its excess
    /// over the threshold, and the lean is scaled by the fraction of the
    /// budget left. Real forced sellers are finite: with no budget, sixty
    /// held days of constant sell pressure in a measured run ground prices
    /// into their clamps and broke the crisis lever downward. At 0.0 the
    /// unlimited behavior reproduces bit for bit. Read only while
    /// `forced_flow_gain` is non-zero.
    pub forced_flow_reservoir: f64,
    /// Fraction of the spent forced-selling budget recovered on each day the
    /// VIX is at or below `forced_flow_threshold` (deleveraging capacity
    /// rebuilds in calm). 0.0 means it never refills.
    pub forced_flow_replenish: f64,
    /// Weight of the market's own volatility in the VIX target, from 0.0 to
    /// 1.0. 0.0 on pt-v1 through pt-v15 (bit-identical by branch) and 0.3
    /// from pt-v16 on; pt-v19 and pt-v20 don't read it, because
    /// `vix_level_identity` replaces the target it blends into.
    ///
    /// At zero the VIX is a function of the business cycle phase, a one-day
    /// return spike that decays with a 5.4-day half-life, and noise. The
    /// market's own realized volatility is not an input. Measured: real
    /// implied volatility tracks trailing 21-day realized volatility at
    /// +0.818 (FRED VIXCLS against SP500, 2,491 days), the model at +0.275
    /// on pt-v3 and +0.337 on pt-v8, and the endogenous VIX crosses its own
    /// crisis threshold on 0.0% of days in a year against a real 12.5%. At
    /// 0.0 the market's own moves can't push the VIX into a crisis.
    ///
    /// At `w` the target becomes `(1 - w) * target + w * implied`, where
    /// `implied` is the market factor's current sigma read back through the
    /// same anchor the forward coupling uses: `market_vol_vix_anchor *
    /// sigma_today / market_factor_sigma`. Using the forward map's own
    /// inverse means the loop is consistent at equilibrium and introduces no
    /// second calibration constant. The VIX clamp (10 to `vix_ceiling`,
    /// which is 80 through pt-v18 and 181.3295 on pt-v19 and pt-v20) and the
    /// factor's ceiling multiple bound the feedback.
    pub vix_realised_vol_weight: f64,
    /// Switch that sets the VIX level from the index's own conditional
    /// variance instead of a table of per-phase constants. 0.0, on pt-v1
    /// through pt-v18, is the table bit for bit; pt-v19 and pt-v20 ship 1.0.
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
    /// so about two thirds of the level was constants and their
    /// cancellations (a phase table shrunk toward 19, an offset cancelling
    /// the standing excursion an asymmetric gain injects, an earnings bump).
    /// The remaining third was the market factor's sigma read through a
    /// conversion of 2105.1 VIX points per unit of daily sigma, where the
    /// identity is `100 * sqrt(252)` = 1587.5. Measured at that setting:
    /// mean VIX 13.76 on an index realizing 15.60 per cent annualized, a
    /// ratio of 0.882 where the tape's is 1.252.
    ///
    /// The 0.0 form also reads the wrong quantity, because the index is not
    /// the factor. On the certified roster the index carries 2.05x the
    /// factor's variance (factor 55 per cent, jumps 23, sector and
    /// idiosyncratic 11, the intraday curve 4.6, news 3), and none of the
    /// rest reached the VIX at any value of any dial.
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
    /// Three things follow:
    ///
    /// - `market_vol_vix_anchor` is DERIVED, from the same identity at the
    ///   unconditional point, and the dial is not read at all. The forward
    ///   map `base * (1 - c + c (VIX/anchor)^2)` and the read-back then
    ///   agree at that point by construction, which the two constants of
    ///   the 0.0 form never achieved.
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
    /// effective persistence of `(alpha + beta) + (1 - alpha - beta) c s_f`,
    /// about 0.99 at the shipped coefficients. The level is held by the
    /// variance processes' own reversion, weakened but not removed, which
    /// is where a real index's long-run variance lives.
    pub vix_level_identity: f64,
    /// The variance risk premium `pi`: how far a real VIX sits above the
    /// realized volatility of its own index, as a fraction. 0.252 on every
    /// shipped preset, read only while [`ModelParams::vix_level_identity`]
    /// is non-zero.
    ///
    /// # Measurement
    ///
    /// Measured on ^GSPC and ^VIX adjusted closes, 1990-01-03 to
    /// 2025-07-30, 8,959 aligned sessions with a return. Five estimators,
    /// because the answer depends on which one the model's own statistic
    /// corresponds to:
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
    /// The value is 0.252 and the residual is the per-year IQR, 1.128 to
    /// 1.398, so +/- 0.13 on `pi`. The per-year and rolling-window rows are
    /// the like-for-like estimators, because the certified panel's
    /// statistic is a relationship on a 252-session window. The pooled
    /// 1.076 is listed so the choice is visible; it changes no conclusion,
    /// because the model reads 0.88.
    ///
    /// # Why the default is the measured value
    ///
    /// The dial is not read while `vix_level_identity` is 0.0, so on pt-v1
    /// through pt-v18 any default is inert. The default decides what a
    /// preset that turns the identity on gets without setting this. Zero
    /// would make the VIX equal realized volatility, which the measurement
    /// above rejects.
    ///
    /// # What could not be determined
    ///
    /// The premium's form. By VIX level the difference grows from +3.3
    /// points below VIX 12 to +8.9 above 30 while the ratio holds at 1.44,
    /// 1.42, 1.44, 1.37, 1.35, 1.35 from 0 to 40 and falls to 1.23 only
    /// above 40; by realized-vol tercile of years the ratio reads 1.40,
    /// 1.24, 1.16 from calm to violent while the difference reads +4.1,
    /// +3.0, +4.2. Neither form is exact. The ratio is the more stable one
    /// over the range this model lives in and is the one used. This
    /// measurement doesn't support a state-dependent premium, and none is
    /// fitted.
    pub vix_variance_premium: f64,
    /// Which index return the VIX reacts to: the session's final minute
    /// (0.0, pt-v1 through pt-v8) or the whole day's (1.0, pt-v9 onward),
    /// blended in between.
    ///
    /// At 0.0 the VIX's return channel reads `market_return_pct`, which the
    /// engine builds from `previous_tick_price`: the cap-weighted move over
    /// the final minute of the session. Measured at 0.0: an index day of
    /// -7.87% moves the VIX +0.15 points, half the worst days move it DOWN,
    /// and with the gain raised to 5000 and the clamp opened the day's index
    /// return still correlates -0.065 with the next day's VIX change.
    /// Raising the gain amplifies the closing minute, which is noise, so
    /// gain changes alone do nothing.
    ///
    /// At 1.0 the channel reads the day's cap-weighted open-to-close return
    /// instead, in the same percent units, which includes the jumps that
    /// `apply_jumps` adds after the tick loop. Real markets move the VIX
    /// about 2 points per percent the index falls, so a calibrated setting
    /// is source 1.0 with `vix_return_gain` near 2.0 and
    /// `vix_return_clamp` in percentage points rather than fractions.
    pub vix_return_source: f64,
    /// How much of the VIX's level comes from the business cycle, from 0.0
    /// (none) to 1.0 (the full per-phase constants).
    ///
    /// The VIX target starts at a constant per cycle phase: 14 in expansion,
    /// 18 at a peak, 25 in contraction, 22 in a trough, 16 in recovery. Those
    /// five numbers move on a multi-YEAR clock, which is why the model's
    /// volatility clustering is a function of the measurement window: lag-5
    /// clustering reads 0.0136 over 252 days, below its 0.02 floor, and
    /// 0.0828 over 504, because only the longer window contains a phase
    /// change. Real markets read inside 0.02 to 0.09 at ONE year and 0.02 to
    /// 0.10 at two, because their clustering comes from episodes lasting
    /// weeks.
    ///
    /// 1.0 uses the five constants as they are (pt-v1 through pt-v8).
    /// pt-v9 ships 0.6. pt-v10 to pt-v15 carry 0.0. pt-v16 onward carry
    /// 0.85. At `a` each constant is pulled toward their mean of 19.0:
    /// `19.0 + a * (phase - 19.0)`, so 0.0 makes the cycle contribute
    /// nothing to the VIX and any episodes have to come from the market.
    /// It works with `vix_return_source`, which supplies those episodes.
    /// pt-v19 and pt-v20 don't read it, because `vix_level_identity`
    /// replaces the phase table.
    pub vix_cycle_amplitude: f64,
    /// VIX points added to its target per unit of a down day's index return,
    /// before the clamp and cap below. Under a `vix_return_exponent` other
    /// than 1.0 it is the scale of a power law instead.
    ///
    /// 25.0 on pt-v1 through pt-v8, 17.0 on pt-v9 to pt-v18, and 8.83 on
    /// pt-v19 and pt-v20, where it is the scale of the power law
    /// `vix_return_exponent` makes of it. Measured against real markets
    /// (FRED VIXCLS and SP500, 2,511 common days to 2026-08): a session at
    /// -3% or worse moves the VIX a median of +6.03 points, and -2% to -1%
    /// moves it +1.95. The 25.0 gain with the 0.03 clamp beside it added at
    /// most 0.75 points to the TARGET, of which the day traverses
    /// `vix_mean_reversion`, about 0.09 points. Raising the gain to the real
    /// slope doesn't fix that: measured, it moves the within-year VIX sd
    /// from 1.54 to 1.79 against a real 4.0 and leaves lag-5 clustering
    /// where it was. The missing piece was the realized-volatility feedback
    /// in `vix_realised_vol_weight`.
    pub vix_return_gain: f64,
    /// The up-day counterpart of `vix_return_gain`: how far an up day's index
    /// return moves the VIX target. 10.0 on pt-v1 through pt-v8, 17.0 on
    /// pt-v9 to pt-v18, and 0.049 on pt-v19 and pt-v20, which use the ratio
    /// form ([`ModelParams::vix_return_level_exponent_up`] -1.0).
    ///
    /// # The real up-to-down ratio
    ///
    /// Measured on ^GSPC and ^VIX over 8,960 aligned sessions, by fitting
    /// each side separately and evaluating the two curves:
    ///
    /// | move | down response | up response | up / down |
    /// |---|---|---|---|
    /// | 1% | 1.003 | 0.897 | 0.89 |
    /// | 3% | 3.73 | 2.80 | 0.75 |
    /// | 6% | 8.58 | 5.75 | 0.67 |
    ///
    /// So the real up-to-down ratio is 0.67 to 0.89. The 25:10 gains of
    /// pt-v1 through pt-v8 give a ratio of 0.4, about twice the real
    /// asymmetry.
    ///
    /// The ratio also varies with the size of the move, because the two
    /// sides have different exponents (1.1996 down against 1.0410 up). No
    /// single value of this dial can express a ratio that varies with the
    /// size of the move. That is the same class of defect as the linear
    /// response the exponent fixes: the parameter's form cannot represent
    /// the quantity it names. Expressing it properly needs an up-side
    /// scale set against the down-side scale at the fit level, 0.894, and
    /// the residual variation left over is the up side's own exponent,
    /// which this data cannot resolve.
    pub vix_return_gain_up: f64,
    /// Exponent of the VIX's response to a down day's return: 1.0 is
    /// linear, above 1.0 is convex. pt-v1 through pt-v18 carry 1.0, which
    /// is bit-identical to the linear arithmetic; pt-v19 and pt-v20 ship
    /// 1.4483.
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
    /// # The exponent's error bar
    ///
    /// Refitting those medians: residual sd 0.0671 in logs, which is 6.9
    /// per cent in `dVIX`, worst bucket -9.8 per cent; standard error of
    /// the exponent 0.0357, so a 95 per cent interval of 1.112 to 1.287 on
    /// six degrees of freedom.
    ///
    /// A second, larger uncertainty is the weighting. The fit above is
    /// unweighted over buckets holding 22 to 994 sessions (the shallow
    /// buckets carry forty-five times the sessions of the deep ones), and
    /// weighting by count gives 1.132 instead. Unweighted asks what shape
    /// the curve has, and count-weighted asks what shape a typical session
    /// sees. The first is the right question for a tail, so 1.200 is the
    /// measured value, but the weighting is a choice and the range it
    /// spans is worth 8 per cent of the response at -3% and 13 per cent at
    /// -6.4%.
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
    /// # The up side
    ///
    /// The exponent applies to down sessions only. The up side was fitted
    /// on the same 8,960 sessions with the same alignment, estimator and
    /// buckets, taking `-dVIX` on up sessions (the same fit on down
    /// sessions reproduces the figures above exactly):
    ///
    /// | side | scale | exponent | R squared | worst bucket |
    /// |---|---|---|---|---|
    /// | down | 1.0033 | 1.1996 | 0.9947 | 9.8% |
    /// | up | 0.8965 | 1.0410 | 0.9655 | 35.0% |
    ///
    /// The down side is convex and the up side is very nearly linear, so
    /// one exponent across both would be wrong on the up side and gain
    /// nothing. `return_spike_for` never applies this exponent to an up
    /// session, and a test pins that at every exponent.
    ///
    /// The up side keeps the linear form, because at an R squared of
    /// 0.9655 with a worst bucket missing by 35 per cent on 23 sessions
    /// this data cannot tell 1.0410 from 1.0. The direction is robust
    /// across every bucket and the fourth decimal is not.
    ///
    /// 0.8965 is not the target for `vix_return_gain_up`. It is the scale
    /// of a `p = 1.041` power fit, and no dial implements that form. The
    /// quantity the up side's dial reproduces is the scale of a fit with
    /// the exponent pinned at 1, which is what the code runs. Refitting the
    /// same eight up buckets that way gives 0.9278 (residual sd 0.1425 in
    /// logs, 14.2 per cent in `dVIX`; standard error of the mean 0.0504,
    /// so a 95 per cent interval of 0.824 to 1.045 on seven degrees of
    /// freedom; worst bucket 38.5 per cent; count-weighted 0.8904). The
    /// same refit on the DOWN buckets gives 1.1872 with a worst residual
    /// of 29.6 per cent against the power form's 9.8, which is the
    /// convexity stated in the units a linear dial would have to work in.
    ///
    /// # The zero-mean correction moves with this dial
    ///
    /// Under [`ModelParams::vix_level_identity`] the standing excursion
    /// an asymmetric response injects is cancelled by its own closed form
    /// rather than by a fitted offset, and that closed form is the
    /// response's FIRST MOMENT. A power form changes it: see
    /// [`crate::economy::daily::expected_return_spike`], which carries
    /// this exponent for that reason. The two dials are independent of
    /// each other (either works alone), but a setup that has one reaching
    /// the spike and not the correction cancels the wrong quantity, by a
    /// factor that scales as `sigma^(p - 1)` and is therefore right at
    /// exactly one volatility.
    pub vix_return_exponent: f64,
    /// The index return is clamped to +/- this before it drives the VIX, in
    /// the units of the return source. 0.03 on pt-v1 through pt-v8 and 15.0
    /// from pt-v9 on.
    ///
    /// On pt-v1 through pt-v8 the return is a fraction, and 0.03 makes a
    /// -10% day and a -3% day produce identical fear, which is worst in a
    /// crash. From pt-v9 the value is in the percentage points the
    /// day-return source (`vix_return_source` 1.0) works in.
    pub vix_return_clamp: f64,
    /// Ceiling on the VIX target's whole excursion, in points: the return
    /// spike plus the inflation and shock adjustments. 12.0 on pt-v1
    /// through pt-v8, 45.0 on pt-v9 to pt-v18, and 158.8524 on pt-v19 and
    /// pt-v20, where it is derived from the return clamp and does not bind
    /// on its own.
    ///
    /// # Before pt-v19 it shaped the response
    ///
    /// At 45.0 against a `vix_return_gain` of 17.0 it bound at 2.647 per
    /// cent of session return, deep inside the 6.39 per cent the tape
    /// supplies a conditional median for, so a -2.7 per cent session and a
    /// -6.4 per cent one produced identical fear. That makes it a shape
    /// parameter, and `economy::daily::fear_response_shape` is the guard
    /// that makes every preset declare it.
    ///
    /// A loop-gain measurement showed the cap was compensating for a VIX
    /// read-back that left out the crisis blend. The index realized 4.0 to
    /// 4.9 times the variance `V_t` priced above `crisis_vix_threshold` and
    /// 1.2 to 1.4 below it, so the fear arm had nothing to balance it above
    /// the threshold and the cap was the brake on the divergence. With
    /// `market::index_var` pricing its own regime, that brake has a
    /// mechanism, and the cap goes back to being a boundary condition.
    ///
    /// pt-v19 therefore sets it to the SUPREMUM of the spike over the domain
    /// the update admits: `vix_return_gain * clamp^vix_return_exponent *
    /// floor^-vix_return_level_exponent`, where the floor is the 10.0 of the
    /// state clamp. The return is already bounded one step earlier and the
    /// spike falls with the level, so at this value the cap cannot bind
    /// anywhere the clamp does not, and the pair has one binding constraint
    /// between them instead of two. Under the level-blind law
    /// (`vix_return_level_exponent` 0) the same expression is the product
    /// `gain * clamp`, which is 255.0 at a gain of 17.0 and a clamp of 15.0.
    /// Under pt-v19's law it is `8.83 * 15^1.4483 * 10^-0.4483` = 158.8524.
    ///
    /// The cap has no required order against `vix_ceiling`. The cap
    /// truncates an additive term of the TARGET and the ceiling truncates
    /// the STATE, so neither binds first at any pair of values, and a cap
    /// under the ceiling makes the ceiling less sticky rather than more.
    /// See `ModelParams::pt_v19` at the cap's own assignment.
    ///
    /// The test `the_default_cap_is_the_clamps_own_image` checks the value
    /// against its derivation, `gain * clamp^p * floor^(-g)` = 158.85236 at
    /// the VIX floor of 10, which is the spike's supremum over the domain
    /// the update admits.
    pub vix_target_shock_cap: f64,
    /// Upper bound on the VIX state itself, in points. 80.0 on pt-v1 through
    /// pt-v18; pt-v19 and pt-v20 ship 181.3295.
    ///
    /// The pt-v19 value is derived: a lower bound solved on a simulated pin
    /// ladder at tolerance 8.64. It has no closed form over the other
    /// dials, so unlike `vix_target_shock_cap` it cannot be recomputed from
    /// them. The 80.0 of earlier presets reproduced the literal the dial
    /// replaced.
    ///
    /// Two facts bound what it may be set to. The real index closed at 82.69
    /// on 2020-03-16, so any bound below that makes a peak target derived
    /// from that day unreachable by construction. And the derived response
    /// peaks at 93.0 when driven over the 2020 tape with the ceiling off, so
    /// a bound of at least 93 is INERT ON THAT TAPE and one below it
    /// truncates. That 93 is a statement about one year rather than a bound
    /// on the process, and a different tape would move it.
    pub vix_ceiling: f64,
    /// A constant added to the VIX target, in points. 0.0 on every shipped
    /// preset, where it is inert, and pt-v19 and pt-v20 don't read it.
    ///
    /// It exists because an ASYMMETRIC return gain puts a standing positive
    /// excursion on the target: the down side is worth more per unit than the
    /// up side and a session is equally likely either way, so the mean
    /// excursion is `0.5 * mean|r| * (vix_return_gain - vix_return_gain_up)`
    /// and the level it adds is that times `1 - vix_realised_vol_weight`. On
    /// the 2020 tape that is 10.5 points of excursion and 7.34 of level,
    /// against a measured overshoot of 6.38.
    ///
    /// Anyone who changes this market's volatility changes the bias this
    /// cancels. The bias is proportional to the mean ABSOLUTE session
    /// return, which is a property of the run rather than of the model, so
    /// one constant cancels it exactly at one value of `mean|r|` and
    /// approximately elsewhere. A calm year carries a smaller bias than a
    /// crisis year and the same offset over-corrects it.
    pub vix_target_offset: f64,
    /// VIX level at which crisis behavior begins. 25.5 on pt-v1 through
    /// pt-v12 and 30.88325108 from pt-v13 on.
    ///
    /// It gates the sector-to-market correlation blend, the universe stress
    /// memory, the economy's crisis premium and the crisis epicentre's
    /// episodes. 25.5 is the P94 of the long-run endogenous VIX
    /// distribution, chosen so the trigger is reachable at all.
    pub crisis_vix_threshold: f64,
    /// How much more volatile the crisis epicentre sector's names are than
    /// the other sectors' at the same VIX, as a ratio of total volatility.
    /// pt-v19 and pt-v20 ship 1.93, derived below. 0.0, on pt-v1 through
    /// pt-v18, tracks no episode, draws no epicentre and takes no draw on
    /// any stream, so those presets are bit-identical.
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
    /// their return that are NOT the market factor (the sector leg and
    /// their own idiosyncratic noise, both in
    /// `market::factors::calculate_live_factors`), and every OTHER name
    /// carries a multiple below one on the same two parts. The market
    /// component is untouched, so the index, the VIX and the fear rows do
    /// not move. Only who carries the crisis moves.
    ///
    /// # It redistributes variance
    ///
    /// The second multiple makes this dial a statement about WHO carries a
    /// crisis, and leaves how large crises are alone. At a given VIX the
    /// roster's total crisis variance is set by `crisis_blend_*`, the GARCH
    /// and the market factor, and this dial does not move it. So the pair
    /// of multiples is solved under a conservation condition (the roster's
    /// MEAN non-market variance is unchanged) alongside the tape's ratio.
    ///
    /// An additive form, which lifted the epicentre's names and lowered no
    /// one, was measured and rejected. It moved the row it was built for
    /// (`crisis_sector_dispersion` 1.15 to 1.39 against the tape's 1.34 at
    /// two years) and moved three rows away with it: `annualised_vol_pct`
    /// 24.3 to 25.5 against 23.7, `cross_sectional_corr` 0.330 to 0.314
    /// against 0.353, and `volume_abs_return_corr` 0.535 to 0.545. The
    /// three had one cause, a roster whose total crisis variance had risen.
    /// The additive form is the `w = 0` edge of this solve; see
    /// [`crate::market::factors::CRISIS_EPICENTRE_SECTOR_SHARE`].
    ///
    /// # Derivation
    ///
    /// Five crisis episodes on the tape, 39 real names of the roster mapped
    /// to the engine's sector keys, each sector's median episode volatility
    /// over each name's own calm-day volatility (VIX under 12), then each
    /// sector relative to the median sector of that episode.
    /// The rule was fixed before the numbers were read: the epicentre is
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
    /// `m` is [`CRISIS_EPICENTRE_MARKET_SHARE`](crate::market::factors::CRISIS_EPICENTRE_MARKET_SHARE)
    /// and `w` is
    /// [`CRISIS_EPICENTRE_SECTOR_SHARE`](crate::market::factors::CRISIS_EPICENTRE_SECTOR_SHARE), both measured on
    /// pt-v19 and recorded there with their recipes. At `m = 0.3916`,
    /// `w = 0.1019` and `e = 1.93` that is `g_down^2 = 0.4996655 /
    /// 0.7773328 = 0.642795`, `g_down = 0.801745`, and `g_up^2 = 1.753897 +
    /// 2.394346 = 4.148243`, `g_up = 2.036724`. See
    /// [`crate::market::factors::crisis_epicentre_gains`], which is where
    /// the solve lives, and
    /// [`crate::market::factors::crisis_epicentre_extra_bounds`], which
    /// holds the two extras that would ask a name for a negative variance:
    /// 0.6052 below and 4.0307 above.
    ///
    /// # What it does not do
    ///
    /// It doesn't set correlation directly and isn't a second crisis blend.
    /// The sector leg it multiplies is the one every member of the sector
    /// shares, so lifting it lifts both the epicentre's volatility and its
    /// internal correlation (which is what an epicentre is), and the
    /// idiosyncratic leg is lifted beside it so the split between the two
    /// is the one the name already had.
    ///
    /// The conservation condition also keeps it from being a volatility
    /// dial: the roster's mean non-market INNOVATION variance is the same
    /// at any extra, to the bit. What a long run realizes is not, because
    /// the per-name GARCH is convex in what it is fed and gives some of it
    /// back; [`crate::market::factors::crisis_epicentre_gains`] measures
    /// how much and says why moving `w` to chase it would be fitting.
    ///
    /// An epicentre no name in the roster is in does nothing at all, and
    /// `Engine::crisis_epicentre_key` is where that is decided: the draw
    /// still happened and the episode still reports it, but with nobody to
    /// move the variance TO there is nothing for the tick to do with it.
    pub crisis_epicentre_extra: f64,
    /// How many consecutive sessions under `crisis_vix_threshold` end a
    /// crisis episode. 21, a month of sessions, on every shipped preset.
    ///
    /// This hysteresis keeps an episode together. The tape's five episodes
    /// are months long (the shortest, 2011, is four months) and the VIX
    /// crosses back under the threshold repeatedly inside each of them.
    /// Without the counter an epicentre would be redrawn on every
    /// re-crossing, which would average three sectors across one crisis and
    /// show none of them.
    ///
    /// Read only while `crisis_epicentre_extra` is non-zero, which is true
    /// on pt-v19 and pt-v20 (1.93) and on no earlier preset, so it is inert
    /// on pt-v1 through pt-v18. A value at or below zero ends an episode on
    /// the first session back under the threshold, which
    /// `ModelParams::invariants` allows as a degenerate hysteresis.
    pub crisis_epicentre_end_sessions: f64,
    /// The VIX above which the dollar catches a safe-haven bid. 25.5 on
    /// every shipped preset.
    ///
    /// It starts at the same 25.5 as `crisis_vix_threshold` and is a
    /// separate dial, so moving the crisis threshold does not move the
    /// dollar: pt-v13 onward set `crisis_vix_threshold` to 30.88325108 and
    /// leave this at 25.5. A preset that wants the two gates to move
    /// together sets both.
    pub usd_crisis_vix_threshold: f64,
    /// How strongly the credit spread floors are re-applied on every daily
    /// step, from 0.0 (off) to 1.0 (both floors in full). 0.0 on pt-v1
    /// through pt-v14; pt-v15 onward ship 1.0.
    ///
    /// `update_economy_daily` moves the 10y treasury daily and never writes
    /// the credit yields, so between periodic meetings the corporate spread
    /// drifts below its 0.8 floor. Measured at 0.0, it falls to 0.4216,
    /// first breaching on day 121, which is an investment-grade yield under
    /// the risk-free curve.
    ///
    /// It is a dial because that function runs for every preset: flooring
    /// unconditionally would move the economy trajectory of every preset
    /// including `pt-v1`, and a trajectory change has to arrive as a new
    /// preset.
    pub daily_credit_floor_gain: f64,

    // ── Mispricing dynamics (mispricing.rs, market/tick.rs) ─────────────
    /// Trading days for half of a mispricing to decay. The one settable knob
    /// for the decay: overriding it recomputes `mispricing_phi` and
    /// `s_phi_tick` via `mathx::pow`.
    pub mispricing_half_life_days: f64,
    /// Daily AR(1) coefficient of the mispricing, derived from
    /// `mispricing_half_life_days`. At the shipped half-life it holds the
    /// recorded reference bits; when the half-life is overridden it is
    /// recomputed deterministically, though not bit-identical to any
    /// recorded constant.
    pub mispricing_phi: f64,
    /// Per-tick decay, `mispricing_phi^(1/390)`. Same bits policy.
    pub s_phi_tick: f64,
    /// Herding: fraction of yesterday's re-rating that continues today.
    pub momentum_theta: f64,
    /// Hard bound on |s|. A guard: settable, never searched.
    pub mispricing_cap: f64,
    /// Crowd valuation gain per day on `s`.
    pub crowd_valuation_gain: f64,
    /// Crowd herding gain per day on yesterday's change in `s`.
    pub crowd_momentum_gain: f64,
    /// Bound on the crowd's daily log-price shock. A guard.
    pub crowd_lean_cap: f64,

    // ── Session guards (market/tick.rs) ─────────────────────────────────
    /// Circuit-breaker band as a fraction of the session open (+/-25%
    /// shipped). A guard: settable, never searched.
    pub price_breaker_fraction: f64,
    /// Absolute cap on any model price (50,000 shipped). A guard.
    ///
    /// A name that reaches it stops moving, and over a century that is not
    /// harmless: the winners reach it first, they are the index's largest
    /// weights, and a frozen name adds no variance to the index's read-back,
    /// so the VIX falls and, through the factor's VIX coupling, every other
    /// name's volatility with it. Measured on the r14 candidate (N4, 12
    /// held-out seeds x 100 years, sim/r15-volstate): capped names carried 35
    /// per cent of the cap weight by years 90-100, the VIX averaged 13.8
    /// against 20.3 in years 0-10, and name volatility below the cap read
    /// 0.64x of the first decade. With the cap at 1e9 on the same seeds the
    /// VIX read 15.9 and the ratio 0.68x; started with its five largest names
    /// at 45,000, the roster's other 35 names lose a tenth of their index
    /// volatility over five years (0.153 to 0.138), and lifting the cap
    /// restores the uncapped run to the bit, since prices are scale-free
    /// (a roster at 50 times the price runs the same market). A long run
    /// should set it far above any price it can reach.
    /// pt-v21 ships 1e9.
    pub price_hard_cap: f64,

    // ── Derived, computed once at construction ──────────────────────────
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
/// `tools/calibration/results/calibrate-pt-v2-2026-08-22.json`. Built as `pt_v1()` with
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
/// switched on -- see [`ModelParams::pt_v19`]. The default in 0.8.0 and
/// 0.8.1.
pub const PT_V19: ModelParams = ModelParams::pt_v19();
/// pt-v19 with a tape that follows the model price, a closing cross, the
/// stock- and sector-specific part of every shock moved into fair value, a
/// stationary opening and a smaller stop ladder -- see
/// [`ModelParams::pt_v20`]. The default from 0.8.5 to 0.9.1.
pub const PT_V20: ModelParams = ModelParams::pt_v20();
/// pt-v20 with the economy's cycle, the Fed's stress rules, the curve, the
/// market's variance, the opening, overnight returns, a name's own variance
/// and the traded path's impact memory moved; see
/// [`ModelParams::pt_v21`]. THE DEFAULT since 0.10.0:
/// `DEFAULT_PRESET_NAME` names it and `Engine::default_model` returns it,
/// and the test at the bottom of this file asserts the two agree.
pub const PT_V21: ModelParams = ModelParams::pt_v21();

/// pt-v21's `cycle_equity_hazard_opening`, held apart because it is the one
/// value the vectors graded for pt-v21 differ in: a larger opening hazard
/// lifts the first year's volatility toward the run's (PH5) and reads the
/// one-year drift lower. Moving it renames the preset's digest, so
/// `PT_V21_DIGEST_PREFIX` moves with it, and every known answer of the
/// default is re-based.
const PT_V21_OPENING_HAZARD: f64 = 0.011;

/// The first eight hex digits of pt-v21's digest at
/// `PT_V21_OPENING_HAZARD`: the fingerprint the graded vector had as
/// overrides on pt-v20 (`custom-2dc32068`). A test pins it.
#[cfg(test)]
const PT_V21_DIGEST_PREFIX: &str = "2dc32068";

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
pub const DEFAULT_PRESET_NAME: &str = "pt-v21";

/// Every coefficient `pt-v3` moved, with the exact bits the converged
/// certificate recorded. Read only by the tests.
#[cfg(test)]
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

#[cfg(test)]
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
            market_beta_normalise: 0.0,
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
            order_flow_depth_law: 0.0,
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
            market_vol_slow_gamma: 0.0,
            market_vol_leverage: 0.0,
            market_vol_leverage_half_life: 0.0,
            market_vol_leverage_down: 0.0,
            market_vol_leverage_standardise: 0.0,
            market_day_tail_df: 0.0,
            market_day_tail_state_share: 0.0,
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
            macro_calendar_days_per_year: 365.0,
            cycle_us_calibration: 0.0,
            fed_liftoff_rule: 0.0,
            market_pe_buybacks: 0.0,
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
            market_vol_vix_exponent_below: 0.0,
            market_beta_down_asym: 0.0,
            market_beta_down_asym_lag: 0.0,
            market_beta_down_asym_lag_live: 0.0,
            market_beta_down_asym_recentre: 0.0,
            market_beta_down_asym_lag_recentre: 0.0,
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
            cascade_gain: 1.0,
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
            // The seven below are the VIX-dynamics dials. Each default is the
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
            // The two per-component states of the VIX-dynamics measurement
            // and the idiosyncratic-rate switch, all branches at 0.0.
            sector_vol_alpha: 0.0,
            sector_vol_beta: 0.0,
            idio_vol_alpha: 0.0,
            idio_vol_beta: 0.0,
            idio_vol_jump_bump: 0.0,
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
            news_absorption_half_life: 0.0,
            news_absorption_drift_share: 0.0,
            news_absorption_drift_half_life: 0.0,
            news_quote_revision: 0.0,
            quote_model_weight: 0.0,
            closing_auction: 0.0,
            earnings_cycle_depth: 0.0,
            earnings_cycle_upside: 0.0,
            earnings_cycle_half_life: 60.0,
            earnings_cycle_sigma: 0.0,
            earnings_anticipation_half_life: 0.0,
            rate_pe_sensitivity: crate::fair_value::RATE_PE_SENSITIVITY,
            cycle_publication_lag: 0.0,
            cycle_publication_lag_draw: 0.0,
            gdp_publication_lag: 0.0,
            unemployment_adjustment_half_life: 0.0,
            unemployment_natural_pull: 0.0,
            unemployment_okun_coefficient: 0.0,
            unemployment_natural_rate: 0.0,
            oil_inventory_reversion: 0.0,
            oil_inflation_passthrough: 0.0,
            index_level_listed: 0.0,
            vix_intraday_live: 0.0,
            forecast_horizon_sessions: 0.0,
            forecast_vix_dispersion: 0.0,
            forecast_vix_dispersion_half_life: 0.0,
            forecast_policy_shadow_discount: 0.0,
            forecast_policy_persistence: 0.0,
            forecast_policy_reversion: 0.0,
            forecast_policy_neutral: 2.5,
            fear_greed_published_inputs: 0.0,
            macro_publication_repricing: 0.0,
            treasury_10y_noise: 0.03,
            treasury_2y_noise: 0.0,
            flight_to_quality_gain: 0.02,
            flight_to_quality_day: 0.0,
            corporate_yield_daily: 0.0,
            cycle_nowcast_accuracy: 0.0,
            corporate_spread_cycle: 0.0,
            earnings_anticipation_drift_share: 0.0,
            earnings_anticipation_drift_half_life: 0.0,
            fed_growth_cut: 0.0,
            macro_pins_hold: 0.0,
            fair_value_news_share: 0.0,
            fair_value_market_share: 0.0,
            fair_value_market_linear: 0.0,
            fair_value_market_vol_cap: 0.0,
            fair_value_market_excess_share: 0.0,
            fair_value_vix_discount: 0.0,
            fair_value_vix_knee: 30.0,
            fair_value_vix_half_life: 0.0,
            fair_value_vix_release_half_life: 0.0,
            fair_value_relative_knee: 0.0,
            fair_value_relative_half_life: 0.0,
            pinned_vix_feedback: 0.0,
            pinned_vix_variance_share: 0.0,
            pinned_vix_calm_knee: 0.0,
            pinned_vix_calm_share: 0.0,
            pinned_vix_priced_cap: 0.0,
            buyback_yield_cap: 0.0,
            buyback_accrual: 0.0,
            rate_close_remark: 0.0,
            rate_intraday_live: 0.0,
            fed_stress_cut: 0.0,
            fed_stress_vix: 30.0,
            fed_stress_inflation_gap: 1.0,
            dividend_payout_share: 0.0,
            dividend_growth_cutoff: 0.30,
            dividend_adjustment_speed: 0.4,
            dividend_yield_ceiling: 2.0,
            dividend_buyback_substitution: 0.0,
            overnight_market_share: 0.0,
            overnight_idio_share: 0.0,
            overnight_idio_df: 0.0,
            earnings_surprise_sigma: 0.0,
            earnings_surprise_df: 0.0,
            earnings_session_sigma: 0.0,
            earnings_followthrough_sigma: 0.0,
            earnings_volume_multiple: 0.0,
            earnings_cycle_report_share: 0.0,
            market_vol_cycle_ratio: 0.0,
            market_vol_cycle_expansion: 0.0,
            market_vol_cycle_half_life: 0.0,
            market_vol_cycle_relative: 0.0,
            market_vol_cycle_relative_calm: 0.0,
            market_vol_cycle_cap_relative: 0.0,
            market_vol_cycle_pin_neutral: 0.0,
            market_vol_cycle_pin_phase: 0.0,
            market_vol_cycle_trough_release: 0.0,
            market_vol_cycle_release_half_life: 0.0,
            market_vol_cycle_recovery_release: 0.0,
            market_vol_cycle_recovery_scale: 0.0,
            vix_stress_premium: 0.0,
            vix_stress_premium_knee: 0.0,
            vix_stress_premium_cap: 0.0,
            vix_fear_uptake: 0.0,
            vix_fear_half_life: 0.0,
            fed_put_gain: 0.0,
            fed_put_threshold: 0.0,
            fed_put_half_life: 0.0,
            fed_put_emergency_vix: 0.0,
            treasury_put_pricing: 0.0,
            treasury_haven_gain: 0.0,
            fed_stress_hold: 0.0,
            treasury_path_pricing: 0.0,
            treasury_path_half_life: 0.0,
            treasury_policy_damping: 0.0,
            policy_anticipation: 0.0,
            policy_anticipation_cut_share: 0.0,
            corporate_spread_vix_cut: 0.0,
            corporate_spread_equity_gain: 0.0,
            corporate_spread_equity_half_life: 0.0,
            cycle_equity_hazard: 0.0,
            cycle_equity_hazard_knee: 0.0,
            cycle_equity_hazard_opening: 0.0,
            market_prehistory_sessions: 0.0,
            market_prehistory_valuation: 0.0,
            fed_put_carry: 0.0,
            fed_drawdown_hold: 0.0,
            opening_mispricing_sigma: 0.0,
            opening_market_sigma: 0.0,
            book_depth_coefficient: 0.0,
            book_depth_exponent: 0.0,
            book_depth_reach: 0.0,
            book_depth_nesting: 0.0,
            book_shared: 0.0,
            book_refill_half_life: 0.0,
            book_resting: 0.0,
            book_arrival_shuffle: 0.0,
            book_cross_at_limit: 0.0,
            fill_impact_coefficient: 0.0,
            impact_memory_coefficient: 0.0,
            impact_memory_half_life: 0.0,
            impact_memory_slow_half_life: 0.0,
            impact_memory_slow_weight: 0.0,
            impact_memory_crossover: 0.0,
            impact_memory_refill: 0.0,
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
    /// Six coefficients move from pt-v6. `sector_factor_sigma` 0.002 to
    /// 0.012 gives the sector draw real
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
    /// Seven coefficients move from pt-v7.
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
    /// Seven coefficients move from pt-v8. `vix_return_source` 1.0 makes
    /// the VIX's fear channel read the
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
    /// Five coefficients move from pt-v9. `garch_vix_coupling` 0.0 to 0.3
    /// lets a NAME's own variance
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
    /// Three coefficients move from pt-v10. `crisis_blend_gain` 0.5 to 0.8
    /// loads names onto the market
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
    /// alone. `jump_vix_coupling` starts here: it had shipped inert at 0.0
    /// in every preset before this one, and §84 designed it to let
    /// idiosyncratic news flow cluster with the regime.
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
    /// ([`qe_pe_gain`](Self::qe_pe_gain)) ships inert because the driven test feeds it a
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
        // R10 for that second job. R10 asks only whether the CMA-ES search may
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
        // Registered before the run and then measured; the eighteen-row objective that
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
    /// RECOMPOSED since, keeping its name because it has never been released
    /// (0.7.x ships pt-v18); the constructor below carries each composition
    /// in order. The FIFTH, of 2026-09-23, is twenty-three dials at the end
    /// of the constructor: the long-run VIX law (LAWC-D), the macro session clock
    /// and cycle, news priced within minutes and the calm-regime variance
    /// target, taken on the owner's adopted long-run pass bar
    /// (`validation/pt-v20/programme/longrun/CRITERIA.md`). The
    /// constructor reads as a history: a later assignment replaces an
    /// earlier one and says so.
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
    /// Every one is MEASURED, and the
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
    /// `volume_change_acf1` to a per-name channel every preset before
    /// this one shipped at 0.0 and measured on the same 120 seeds
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
    /// and `excess_kurtosis` back by as much at 120 seeds.
    /// Two thirds of that damage is the decay
    /// ratio's, and turning it back costs the fear rows twelve points, so
    /// the row is a price of the regime the fear fix needs and not a
    /// mistake in it. Bar B4 -- a fear response that RISES across the
    /// graded range -- is unmet by this and by every preset; that is a
    /// mechanism change, not
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
        // brake standing in for that (the loop-gain run showed it) and at
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
        // THE LOOP'S VARIANCE ARM, CUT. The GARCH derivation found that
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
        // Off again from the fifth composition (2026-09-23, below): the VIX
        // law became the anchor form, which runs with the excursion at 0.0.
        p.market_vol_vix_excursion = 1.0;
        // THE CRISIS BLEND, DERIVED TO ZERO AND ITS FORM RETIRED.
        //
        // Three facts from the tape, none of which needs the model.
        // The VIX has no crisis attractor: its conditional drift by level is
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
        // THE COMPOSITION OF 2026-09-21. The
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
        // 0.0181 from the fifth composition (below), re-derived on its loop.
        p.vix_level_sigma = 0.0173;
        // THE THIRD COMPOSITION, 2026-09-21 (registered first, decision rule
        // written before the numbers). The crisis lever is lost at the excursion
        // form's FIXED POINT: the target is `base (1 - c + c (VIX / I)^e)`
        // with `I ~ sqrt(v)`, so a held VIX settles the variance at
        // `v ~ VIX^(e / (1 + e/2))`, which at the shipped square is `v ~ VIX`
        // and a lever of sqrt(13) before clamps. The tape's lever, 6.16x of
        // volatility for 13x of VIX, is `v ~ VIX^1.42`, which the same form
        // gives at `e = 2s / (2 - s)` = 4.9. DERIVED from that law; the
        // held-VIX read-back then rises 4.6x for 13x (3.0x at the square).
        // 4.0 from the fifth composition (below), under the anchor form.
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
        // VIX law arrived. 1.79 from the fifth composition (below),
        // re-derived on the anchor form's loop.
        p.vix_level_loop_gain = 2.4684;
        // THE FOURTH COMPOSITION, 2026-09-22 (registered first; Simon's ruling that ties in the row
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
        // rest is 34 to 36. An independent re-derivation of the ceiling works
        // through the update, and `economy::daily::fear_response_shape` asserts it on
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
        // RE-DERIVED ON THE COMPOSED VECTOR'S LAW, 2026-09-14.
        // The condition
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
        // Gaussian QMLE GARCH(1,1) on the tape's index over the whole span:
        // alpha 0.1059 with a sandwich se of
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
        // (defect-16). The
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
        // that separate pt-v18 from the 2026-09-14 composition (registered
        // first, both parents reproducing their records bit for bit) found
        // the market variance family -- the GJR triple, the slow pole and
        // the stochastic level -- away from the tape on volatility level,
        // cross-sectional correlation, correlation persistence and the fear
        // rows in 32 of 32 pairs at both horizons, against one gain on
        // kurtosis; and the idiosyncratic jump family moving nothing beyond
        // noise. Both return to pt-v18's values below. The VIX law stays,
        // and only WITH the two crisis dials: without them every cell runs
        // away over a two-year window. The derivations the returned values
        // replace stay recorded in the project's unpublished design notes;
        // the comments that argued them are kept above each line as the
        // record of why they were tried.
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
        // of freedom. The GARCH derivation fitted both forms to the
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
        // which is where that fit does not reach.
        p.market_vol_gamma = 0.1556;   // the tape's; pt-v18's 0.0 from 2026-09-20 to 2026-09-21 (see the composition note above)

        // ==================================================================
        // THE COMPOSED VECTOR, adopted 2026-09-13.
        //
        // Everything below is measured on the tape and scored in a 2^3 factorial plus
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
        // on 2015-2025. Re-centred on
        // the whole tape, that margin does not survive: the preset this
        // block replaces reads 61.5 / 61.0 on the honest tables, behind
        // pt-v18 at both horizons. The vector below is the first arm that
        // is ahead on a table its own centres were not chosen against.

        // THE RESPONSE LAW IS TWO LAWS. Down
        // sessions are convex in the move and fall with the level; up
        // sessions are concave and proportional to it. The shipped
        // level-blind form is REFUSED at F = 118 against the tape.
        p.vix_return_exponent = 1.4483;
        p.vix_return_level_exponent = 0.4483;
        p.vix_return_exponent_up = 0.5433;
        p.vix_return_level_exponent_up = -1.0;

        // THE MEMORY AND THE GAIN ARE ONE CONSTRAINT, not two dials
        // (R13 withdrawn). `vix_return_gain`
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
        // derivation wants. The ceiling's derivation establishes that,
        // re-derives the ceiling's condition on THIS law, and records why the
        // re-derivation's own answer (a ceiling of 56 to 67, which would
        // restore the ordering and would clip a record whose measured
        // maximum VIX is 60.59) is refused.
        p.vix_target_shock_cap = 158.8524;

        // THE PER-NAME MEMORY. The tape's
        // per-name |r| autocorrelation needs a persistence the shipped
        // 0.6853 cannot carry; 0.7905 is the value that puts the name's
        // total at the tape's 0.9416.
        p.garch_beta = 0.7905;

        // THE FACTOR'S SLOW POLE, from the
        // tape's forward-21-session realised-variance impulse response:
        // 0.9913 in [0.975, 1.0]. It replaces a 0.98 that was never read off
        // anything, and it is what carries the 504-day horizon.
        p.market_vol_slow_persistence = 0.9913;   // the tape's; pt-v18's 0.98 from 2026-09-20 to 2026-09-21

        // THE TWO PER-NAME STATES,
        // in the RATIO form: a GARCH(1,1) on the sector factor standardised
        // by its VIX-coupled target, and a jump excitation on the name. The
        // tape puts the sector's variance persistence at 0.904 and jumps at
        // 3.3x the day after one with a branching ratio of 0.13. The
        // ADDITIVE form of the same two states was measured and is worse at
        // 504 by 2.68 against a paired error bar of 1.60,
        // which is why the ratio form is what ships.
        p.sector_vol_alpha = 0.067;
        p.sector_vol_beta = 0.837;
        // The idiosyncratic jump family returns to pt-v18 (recomposed
        // 2026-09-20): 2.0 / 0.72 / 1.0 moved no row beyond noise in
        // either 32-pair contrast of the factorial.
        p.jump_idio_excitation = 0.0;
        p.jump_idio_excitation_decay = 0.0;
        p.jump_idio_vix_decoupled = 0.0;
        // THE SLOW VARIANCE LEVEL AND THE SECTOR LOADING, adopted 2026-09-14
        // from `levsec3` after `levelsec1`, `levsec2`
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
        // fourth-moment derivation said 0.047 by setting
        // the LEVEL's window-mean dispersion equal to the INDEX's deficit;
        // the level drives the FACTOR, which is about half the index, and
        // the transmission is measured at 0.50 at 252 and 0.69 at 504, flat
        // in the dose. Read off the engine's own
        // output, the sigma that reproduces the tape's window log-variance
        // dispersion is 0.091 at 252 and 0.078 at 504; 0.085 is the midpoint
        // and the arm confirms the fit: `sd(log var)` reads 0.693 and 0.771
        // against a tape of 0.723 +/- 0.072.
        //
        // `market_vol_level_persistence` stays at the derivation's 0.9977.
        // A later measurement finds a shorter half-life on a better
        // estimator -- 127 to 249 sessions against 295 -- and the two tape
        // spans disagree by more than their own error, so the revision is
        // recorded and NOT taken: no arm has run at it.
        // Recomposed 2026-09-20: the level returns to OFF. Derived against
        // the index tail row alone, and
        // measured by `ar1lever`, `levelscan` and the factorial to carry
        // three to four other rows the wrong way. 0.9977 / 0.085 until then.
        p.market_vol_level_persistence = 0.0;
        p.market_vol_level_sigma = 0.0;
        // The loading was derived against a centre the record then replaced.
        // `params.rs` recorded 0.8 as the value that "puts it back on centre
        // (0.1641 against 0.1640 at 252)", and 0.1640 was the 2015-2025
        // forty-name centre; the whole tape puts the row at 0.1178. 0.60 is
        // the DERIVED replacement and the
        // `levelsec1` sweep MEASURED the centring loading at 0.596 at 252
        // and 0.609 at 504 on this base. The row goes from a term of 2.67 to
        // 0.00 at both horizons.
        p.sector_loading = 0.60;
        // THE FIFTH COMPOSITION, 2026-09-23. A candidate of twenty-three
        // dials, taken on the owner's adopted pass bar
        // (`validation/pt-v20/programme/longrun/CRITERIA.md`):
        // what a user would notice over thirty 21-year histories, the 2008
        // and 2020 replays, the headline edge and the one-year table. The
        // fourth composition fails eight of its criteria; this vector
        // passes them all. Every assignment below is a plain field write
        // in its setter, so `from_preset("pt-v19")` here is bit for bit the
        // fourth composition's `from_preset("pt-v19", **arm)` (checked
        // across the two builds). Two
        // values are FITTED against certification gates (the anchor's
        // memory 1/18 and the down-day wire's lag 0.46), the exponent 4.0
        // was read off a ladder against the crisis lever, and the calm
        // exponent 2.5 is CHOSEN inside a tape window; `provenance.py`
        // carries each dial's kind.
        //
        // The VIX law, LAWC-D:
        // the variance target follows the VIX through `(VIX / anchor)^4`
        // with no excursion term, and the VIX's target blends the read-back
        // with a derived anchor through a weight with a memory, a centre
        // and a level law.
        p.market_vol_vix_excursion = 0.0;
        p.market_vol_vix_exponent = 4.0;
        // DERIVED: the tape's VIX-to-realised-volatility elasticity, 0.655,
        // re-applied through `theta = (1 - a) k`.
        p.vix_anchor_weight = 0.375;
        // FITTED against the VIX persistence gate.
        p.vix_anchor_memory = 0.05555555555555555;
        // DERIVED: ln(1.252 / 1.076), two tape readings of the premium.
        p.vix_anchor_centre = 0.1515;
        // DERIVED off the engine's held map: the log-slope of the
        // read-back's gain between VIX 18.5 and 30.
        p.vix_anchor_weight_level = 1.0;
        // The level law's cap and knee, re-derived for the 0.375 weight
        // with the crisis side held: knee += ln(0.625 / 0.55) / eta, cap *=
        // the same ratio.
        p.vix_anchor_weight_level_cap = 2.2159;
        p.vix_anchor_weight_level_knee = 0.3888;
        // The slow level re-derived from the tape's yearly-median spread
        // on the anchor form's loop.
        p.vix_level_sigma = 0.0181;
        p.vix_level_loop_gain = 1.79;
        // The calm side of the variance target: 2.5 below the anchor,
        // CHOSEN as the least change from 4.0 inside the tape's
        // shared-variance window.
        p.market_vol_vix_exponent_below = 2.5;
        // The down-day wire, sampled on the live session; the lag 0.46 is
        // FITTED against the lagged asymmetry row's held-out count.
        p.market_beta_down_asym_lag_live = 1.0;
        p.market_beta_down_asym_lag = 0.46;
        // The macro fix: the session clock and
        // calendar, the NBER cycle table, the Fed's lift-off rule and
        // buybacks in the market P/E; and the opening drawn from the
        // cycle, by the owner's ruling that certification runs open at a
        // random point in the business cycle.
        p.macro_compound_days_per_year = 252.0;
        p.macro_calendar_days_per_year = 252.0;
        p.cycle_us_calibration = 1.0;
        p.fed_liftoff_rule = 1.0;
        p.market_pe_buybacks = 1.0;
        p.cycle_stationary_opening = 1.0;
        // News priced within minutes: the absorption
        // profile from Christensen, Timmermann and Veliyev (arXiv
        // 2601.08962, Table 7), with the maker re-quoting on news.
        p.news_absorption_half_life = 0.6;
        p.news_absorption_drift_share = 0.12;
        p.news_absorption_drift_half_life = 42.0;
        p.news_quote_revision = 1.0;
        p
    }

    /// pt-v19 with the market-behaviour faults the mean-reversion
    /// investigation of 2026-09-24 found fixed. The registered rows and the
    /// grade are published under `validation/pt-v20/`. Selectable
    /// and NOT the default: composing it moves no digest.
    ///
    /// # What it changes, and why
    ///
    /// THE TAPE. The maker quoted around the last print, so the print chased
    /// the model price and the inventory skew carried it past: 65-minute
    /// returns carried a lag-one autocorrelation of -0.135 and a Roll spread
    /// 5.8x the quoted one, and a one-step reversal rule beat buy-and-hold
    /// after costs in 8 markets of 8. `quote_model_weight` 1.0 centres the
    /// book on the model price every tick; `closing_auction` 1.0 prints the
    /// session's last tick at the model price, as a closing cross does, so
    /// the close-to-close return carries no bid-ask bounce.
    ///
    /// THE CROSS-SECTION. 96 per cent of a name's own daily variance was
    /// mispricing and none of it fair value, so every stock-specific move
    /// reverted on the 60-day half-life: a value screen on published
    /// fundamentals ranked the next 20 days at IC +0.38 (+0.75 in a market's
    /// first 60 days) against a real +0.01, and 12-1 momentum ran at -0.17
    /// against a real +0.03. `fair_value_news_share` 1.0 moves the whole
    /// stock- and sector-specific part of every shock into the name's fair
    /// value; the mispricing keeps the market-wide part. The published
    /// fundamentals do not move, so they are a noisy read of fair value.
    /// `opening_mispricing_sigma` opens each name's mispricing at the model's
    /// stationary spread instead of the whole day-zero premium, and
    /// `opening_market_sigma` opens the market's common level at a draw from
    /// its own stationary spread instead of the roster's cap-weighted
    /// premium, which drifted a 20-name suite market by up to 30 per cent in
    /// its first months with nothing happening.
    ///
    /// THE CURVE. The 2-year had no noise of its own (0.85 policy + 0.15
    /// 10-year), the flight to quality read a closing minute behind a gate it
    /// never crossed, and the corporate yield moved only at meetings, so the
    /// bonds priced off the curve were quiet and uncorrelated with stocks.
    /// `treasury_2y_noise`, `flight_to_quality_day` and `_gain`,
    /// `corporate_yield_daily` and `treasury_10y_noise` fix all three.
    ///
    /// THE MARKET'S YEARS. Every market-wide move was mispricing and
    /// reverted on the 60-day half-life, so the index's year-to-year spread
    /// was 11.7 per cent against a real 17.4. `earnings_cycle_depth` and
    /// `_upside` give aggregate earnings a cycle that falls in a contraction
    /// and recovers in an expansion, and `market_factor_sigma` and
    /// `jump_intensity_market` take the transient part down by as much.
    /// With less market noise a name's volume tracks its own move more
    /// tightly, so `volume_move_response` 0.6 keeps that tie inside the
    /// certified band at every horizon.
    ///
    /// THE MARKET'S LONG HORIZON. With every market shock in the
    /// mispricing, the index reverted far faster than the S&P: its five-year
    /// variance ratio over one year read 0.42 against a real 0.87 (audit
    /// major 5). `fair_value_market_share` 1.0 with `fair_value_market_linear`
    /// makes the plain market draw permanent, and `fair_value_market_vol_cap`
    /// 1.5 keeps the excess a fear regime adds transient. The earnings cycle
    /// then carries less of the index's yearly spread, and 0.2 deep puts its
    /// contraction on Shiller's median fall. A volatility-feedback discount
    /// above a VIX of 40 (`fair_value_vix_discount`, `_knee`, `_half_life`)
    /// gives a crash its depth and gives it back as the fear goes.
    ///
    /// LOOKING AHEAD. Fair value reads the earnings cycle's expected path
    /// (`earnings_anticipation_half_life` 126), a P/E compresses by
    /// `rate_pe_sensitivity` 3 per unit of yield, and `buyback_payout_share`
    /// 0.75, capped at a 15 per cent yield (`buyback_yield_cap`), restores
    /// the drift the first two cost.
    ///
    /// WHAT IS PUBLISHED. Timing rules on the reported macro data beat
    /// buy-and-hold (pt-v20 audit, findings 1 and 3). The phase is published a
    /// year late and GDP growth quarterly a month late, as the NBER and the
    /// BEA publish them; unemployment turns over months; the fear and greed
    /// index reads the published figures; and a macro step is priced when
    /// it is published (`cycle_publication_lag`, `gdp_publication_lag`,
    /// `unemployment_adjustment_half_life`, `fear_greed_published_inputs`,
    /// `macro_publication_repricing`).
    ///
    /// A LIMIT. The model's inflation almost never leaves the under-3-per-
    /// cent regime, so stocks and Treasuries are always in flight to
    /// quality: their correlation matches the 2015-24 pooled figure, not the
    /// positive one of an inflation regime such as 2022's.
    ///
    /// THE DAILY CONTINUATION. With the tape honest, the stop and squeeze
    /// ladders were the largest daily momentum left in the model price;
    /// `cascade_gain` scales them to the certified forty's daily
    /// Lo-MacKinlay reading.
    ///
    /// Every value, its derivation or measurement, and the residual it
    /// leaves, is in `python/tradefloor/provenance.py`.
    pub const fn pt_v20() -> ModelParams {
        let mut p = ModelParams::pt_v19();
        p.quote_model_weight = 1.0;
        p.closing_auction = 1.0;
        p.fair_value_news_share = 1.0;
        p.opening_mispricing_sigma = 0.016;
        // 0.10 until the graded arm (2026-09-26): the market's own
        // mispricing's stationary spread while every market shock sat in it.
        // With the plain market draw permanent (below) that mispricing
        // carries only the excess above the volatility ceiling, and the
        // grid carried the opening down with the transient share (0.04 at a
        // share of 0.6, 0.025 at 0.75, 0.015 at 0.85; boxes ptv20vr1-vr4).
        // 0.001 at a share of 1.0 is CHOSEN, not measured: the smallest
        // opening that keeps the stationary form, where 0.0 would adopt the
        // roster's day-zero premium.
        p.opening_market_sigma = 0.001;
        p.cascade_gain = 0.1;
        // The yield curve (2026-09-24, for the bonds this release prices off
        // it): the 2-year its own process, the flight to quality reading the
        // session's return, the corporate yield moving between meetings, and
        // the 10-year's noise trimmed because its meeting-day moves already
        // carry most of its variance. Trimmed to 0.025 on 2026-09-24; on the
        // arms with the publication dials and the permanent market share
        // that read 4.16 to 4.25 bp a day against the tape's 5.41 and failed
        // R2 (boxes ptv20vr4-vr7), and 0.038 reads 5.12 (ptv20vr9).
        p.treasury_10y_noise = 0.038;
        p.treasury_2y_noise = 0.022;
        p.flight_to_quality_gain = 0.008;
        p.flight_to_quality_day = 1.0;
        p.corporate_yield_daily = 1.0;
        // The agent-facing book (feature/order-book-depth, E4): size walks a
        // latent book to the square-root law and pays for it, agents rest
        // orders and meet each other, and an agent's fill leaves Almgren's
        // linear permanent impact. Read only on an agent's path, so no
        // untraded statistic moves with any of them.
        p.book_depth_coefficient = 0.75;
        p.book_depth_exponent = 0.5;
        p.book_depth_reach = 1.0;
        p.book_shared = 1.0;
        p.book_refill_half_life = 27.0;
        p.book_resting = 1.0;
        p.fill_impact_coefficient = 0.314;
        // The market's variance, moved from transient to lasting (the
        // co-tune grid, box ptv20e4, 90 pooled histories). An aggregate
        // earnings cycle a third deep in a contraction and 9 per cent up in
        // an expansion, against Shiller's reported earnings around the NBER
        // recessions (median fall 17 per cent, 2 to 54 per cent), gives the
        // index its real year-to-year spread; the market factor's daily
        // shock at 0.85 of pt-v19's and market jumps at half their rate take
        // out the transient variance the cycle adds, so the mispricing's
        // share of index variance stays in its band. 0.35 until the graded
        // arm (2026-09-26): once the market's own shocks are permanent
        // (below) the cycle no longer has to carry the yearly spread, and
        // at 0.35 the aggregate earnings fall in a contraction read -0.28
        // against Shiller's median -0.17 (row E1). 0.2 reads -0.173 and
        // keeps B9 at 18.1 against 17.4 (boxes ptv20vr3b, vr4, vr9).
        p.earnings_cycle_depth = 0.2;
        p.earnings_cycle_upside = 0.09;
        p.market_factor_sigma = 0.006454071;
        p.jump_intensity_market = 0.02828766685;
        // With less transient market noise, the common volume multiplier is
        // a smaller share of a name's volume, which then tracks its own move
        // more tightly: the 504-session certification panel's
        // `volume_abs_return_corr` read 0.639 against the band's 0.63 (box
        // ptv20g2). 0.8 put it at 0.618 there, but it kept rising with the
        // horizon and crossed 0.63 from 1,260 sessions (0.641 at 2,520 on
        // the release's envelope run). At 2,520 sessions (nine histories,
        // the certified roster) it reads 0.633 at 0.8, 0.621 at 0.7 and
        // 0.609 at 0.6, where pt-v19 reads 0.616; 0.6 holds it under the
        // ceiling at every horizon measured (the fifth registration).
        p.volume_move_response = 0.6;
        // THE GRADED ARM (2026-09-26): the twelfth registration,
        // `validation/pt-v20/programme/ptv20-registration.md`, "The graded arm".
        // Chosen on held-out seeds (201-230, 501-530, 801-830; box
        // ptv20vr9), where it passes all 40 registered rows; the grade seeds
        // (101-130, 401-430, 701-730) had not been run on it. Each value's
        // kind and source are in `python/tradefloor/provenance.py`.
        //
        // What is published (pt-v20 audit, findings 1 and 3). The phase a
        // year late, as the NBER dates a turn (owner, 2026-09-25); GDP
        // growth as the BEA's quarterly advance estimate, a month after the
        // quarter (owner, 2026-09-25); unemployment adjusting with an
        // 84-session half-life, which puts the first monthly rise of a
        // contraction at 0.16 pp where UNRATE's first months of 2001 and
        // 2008 rose 0.1 to 0.3; the fear and greed index on the published
        // figures; and the close's macro step priced when it is published.
        p.cycle_publication_lag = 252.0;
        p.gdp_publication_lag = 21.0;
        p.unemployment_adjustment_half_life = 84.0;
        p.fear_greed_published_inputs = 1.0;
        p.macro_publication_repricing = 1.0;
        // Looking ahead (grids ptv20e6-e8). Fair value reads the earnings
        // cycle's expected path at a 126-session half-life, so the price
        // trough leads the earnings trough (L1); a P/E compresses by 3 per
        // unit of yield, where 1.5 moved the 2022 replay's P/E 1.2 per cent
        // per 100 bp of Baa against the S&P's 5.2 (R6). Both cost the
        // one-year drift, and the buyback share restores it: at 1/3 no arm
        // held the level band's 1.1 floor, at 0.75 H126 rate 3 reads 1.79
        // (desk, 30 rosters). 0.75 is a CALIBRATION to that drift, not a
        // measurement of buybacks; B8, the long-run return, bounds it.
        p.earnings_anticipation_half_life = 126.0;
        p.rate_pe_sensitivity = 3.0;
        p.buyback_payout_share = 0.75;
        // The market's long horizon (audit major 5, row V1). The plain
        // loading on the market draw moves fair value for good, up to a
        // daily market sigma of 1.5 times `market_factor_sigma`; above it
        // the excess stays in `s` and reverts, so fear-regime excess is
        // transient (Poterba and Summers 1988; Kim, Nelson and Startz 1991;
        // Spierdijk, Bikker and van den Hoek 2012). V1 reads 0.80 and 0.66
        // against bands of 0.75 to 1.15 and 0.55 to 1.20 (ptv20vr9); the
        // leading arm without it read 0.70 and 0.42. A ceiling of 2 took
        // B7, the index volatility, to 27.9 against 18.1 (ptv20vr4).
        p.fair_value_market_share = 1.0;
        p.fair_value_market_linear = 1.0;
        p.fair_value_market_vol_cap = 1.5;
        // Volatility feedback (French, Schwert and Stambaugh 1987; Campbell
        // and Hentschel 1992): fair value discounted by exp(-0.35 beta
        // ln(vix / 40)) on the VIX smoothed over a 5-session half-life. It
        // takes the driven 2020 fall to 0.266 in 35.5 sessions (F1, real
        // 0.339 in 23; 0.192 in 41 without it). At a knee of 30 or 35 the
        // sessions under -5 per cent ran past twice the tape's (B5; boxes
        // ptv20vr8, vr9).
        p.fair_value_vix_discount = 0.35;
        p.fair_value_vix_knee = 40.0;
        p.fair_value_vix_half_life = 5.0;
        // A GUARD. The buyback term compounds today's yield over every
        // elapsed year, and a name near the price floor read a yield in the
        // hundreds: the index rose 86-fold in one close on 1 of 90 held-out
        // histories (grid ptv20vr6). At 0.75 of earnings the cap binds only
        // below a P/E of 5.
        p.buyback_yield_cap = 0.15;
        p
    }

    /// pt-v20 with 104 dials moved: the economy's cycle feeding back from
    /// equity stress, the Fed's stress and growth rules and a curve that
    /// prices the policy path, payouts as accrued state, a market variance
    /// that follows the cycle with a leverage term and a VIX stress premium,
    /// a market that opens on two years of prehistory, a name's own variance
    /// clustering, overnight and earnings-day returns, a traded path whose
    /// impact remembers recent volume, unemployment on Okun's law and oil
    /// that reverts to its inventory level. THE DEFAULT since 0.10.0.
    ///
    /// Every dial it moves is a switch or a dial that is inert at 0.0 on
    /// every earlier preset (or at its default, where that is not zero), so
    /// pt-v20 and every preset before it replay exactly. The values are the
    /// graded vector: pt-v20 with these overrides has the fingerprint this
    /// preset's digest replaces, which the test at the bottom of this file
    /// pins (`PT_V21_DIGEST_PREFIX`). Each dial's own documentation says what it
    /// does at the value set here.
    pub const fn pt_v21() -> ModelParams {
        let mut p = ModelParams::pt_v20();
        // The economy's cycle and the earnings it drives. Equity stress
        // raises the hazard of a turn, in the opening's draw as in the run,
        // the cycle is nowcast and published with a drawn lag, credit
        // spreads follow the cycle, fair value leans on the expected
        // earnings drift, and the aggregate earnings cycle reaches the
        // phase's level more slowly.
        p.cycle_equity_hazard = 5.0;
        p.cycle_equity_hazard_knee = 0.1;
        p.cycle_equity_hazard_opening = PT_V21_OPENING_HAZARD;
        p.cycle_nowcast_accuracy = 0.4;
        p.cycle_publication_lag_draw = 1.0;
        p.corporate_spread_cycle = 0.75;
        p.earnings_anticipation_drift_half_life = 252.0;
        p.earnings_anticipation_drift_share = 0.9;
        p.earnings_cycle_half_life = 150.0;
        // Unemployment follows output by Okun's law, and oil reverts toward its
        // inventory level and passes through to inflation symmetrically.
        p.unemployment_okun_coefficient = 0.75;
        p.oil_inventory_reversion = 0.002;
        p.oil_inflation_passthrough = 1.0;
        // The Fed and the curve. The bank cuts for weak growth and for stress,
        // holds a rise through stress and after a drawdown, and carries a put
        // the curve prices before the meeting; the curve prices the expected
        // policy path, the 2-year carries less noise of its own, the rate
        // indices re-mark at the close and mark live in the session, a pinned
        // field holds through the close, and credit spreads follow equities.
        p.fed_growth_cut = 2.0;
        p.fed_stress_cut = 0.1;
        p.fed_stress_inflation_gap = 2.0;
        p.fed_stress_hold = 42.0;
        p.fed_put_gain = 3.0;
        p.fed_put_half_life = 126.0;
        p.fed_put_carry = 1.0;
        p.fed_put_emergency_vix = 50.0;
        p.fed_drawdown_hold = 0.12;
        p.rate_close_remark = 1.0;
        p.rate_intraday_live = 1.0;
        p.macro_pins_hold = 1.0;
        p.policy_anticipation = 1.8;
        p.treasury_put_pricing = 1.0;
        p.treasury_haven_gain = 0.014;
        p.treasury_path_pricing = 1.0;
        p.treasury_path_half_life = 63.0;
        p.treasury_policy_damping = 0.5;
        p.treasury_2y_noise = 0.008;
        p.flight_to_quality_gain = 0.013;
        p.corporate_spread_vix_cut = 1.0;
        p.corporate_spread_equity_gain = 1.8;
        p.corporate_spread_equity_half_life = 126.0;
        // Payouts. The buyback term is accrued state, dividends are paid,
        // and a dividend substitutes for buybacks within one total payout.
        p.buyback_accrual = 1.0;
        p.buyback_payout_share = 0.9;
        p.dividend_payout_share = 1.2;
        p.dividend_buyback_substitution = 1.0;
        // The market's variance and the VIX. A cycle-dependent market volatility
        // with a leverage term, a VIX stress premium, a pinned VIX that feeds
        // back into variance and is priced calm below its knee, a market factor
        // on a normalised beta, and rarer, larger market jumps.
        p.market_factor_sigma = 0.007099478;
        p.market_beta_normalise = 1.0;
        p.market_vol_gamma = 0.06;
        p.market_vol_beta = 0.9446;
        p.market_vol_slow_gamma = 0.05;
        p.market_vol_vix_coupling = 0.75;
        p.market_vol_vix_smooth = 3.0;
        p.market_vol_cycle_ratio = 2.4705882352941178;
        p.market_vol_cycle_expansion = 0.82;
        p.market_vol_cycle_half_life = 10.0;
        p.market_vol_cycle_relative = 0.75;
        p.market_vol_cycle_cap_relative = 1.0;
        p.market_vol_cycle_pin_neutral = 1.0;
        p.market_vol_cycle_pin_phase = 1.0;
        p.market_vol_cycle_recovery_release = 0.45;
        p.market_vol_cycle_recovery_scale = 0.1;
        p.market_vol_leverage = 2.5;
        p.market_vol_leverage_half_life = 15.0;
        p.market_vol_leverage_standardise = 1.0;
        p.vix_level_sigma = 0.009;
        p.vix_stress_premium = 3.0;
        p.vix_stress_premium_knee = 0.6;
        p.vix_stress_premium_cap = 0.35;
        p.pinned_vix_feedback = 0.8;
        p.pinned_vix_variance_share = 0.7;
        p.pinned_vix_calm_knee = 17.6;
        p.pinned_vix_calm_share = 0.2;
        p.pinned_vix_priced_cap = 1.0;
        p.jump_intensity_market = 0.005;
        p.jump_mean_market = -0.03;
        p.jump_sigma_market = 0.01;
        // Fair value and the opening. A floor under the market's permanent share
        // above the volatility ceiling, a slower give-back of the volatility
        // discount, a relative-value knee, and a market that opens on two years
        // of prehistory at its valuation.
        p.fair_value_market_excess_share = 0.5;
        p.fair_value_vix_release_half_life = 504.0;
        p.fair_value_relative_knee = 4.0;
        p.fair_value_relative_half_life = 63.0;
        p.market_prehistory_sessions = 504.0;
        p.market_prehistory_valuation = 1.0;
        // A name's own variance. Idiosyncratic volatility clusters and jumps
        // bump it, jumps are rarer and the base sigma lower, and volume reads
        // the name's own variance.
        p.idio_sigma_scale = 0.52;
        p.idio_vol_alpha = 0.25;
        p.idio_vol_beta = 0.5;
        p.idio_vol_jump_bump = 1.0;
        p.jump_intensity_idio = 0.009;
        p.jump_sigma_idio = 0.0318;
        p.volume_idio_variance_gain = 0.65;
        // Overnight and earnings. A share of the market's and each name's
        // variance arrives overnight with fat tails, and an earnings day carries
        // a surprise, the session's reaction, a follow-through and its volume.
        p.overnight_market_share = 0.55;
        p.overnight_idio_share = 0.1;
        p.overnight_idio_df = 4.0;
        p.earnings_surprise_sigma = 3.5;
        p.earnings_session_sigma = 1.9;
        p.earnings_followthrough_sigma = 1.1;
        p.earnings_volume_multiple = 1.2;
        // The traded path. Impact remembers recent volume on two timescales and
        // refills, a fill's linear impact is smaller, a cohort's arrival order
        // at the book is shuffled, the latent depth nests the maker's ladder, a
        // crossed resting order trades at its own limit, and injected order flow
        // divides by depth once, linear in a tick's participation up to ten
        // times the name's minute volume.
        p.impact_memory_coefficient = 0.65;
        p.impact_memory_half_life = 12.0;
        p.impact_memory_slow_half_life = 780.0;
        p.impact_memory_slow_weight = 0.1;
        p.impact_memory_crossover = 0.001;
        p.impact_memory_refill = 1.0;
        p.fill_impact_coefficient = 0.15;
        p.book_arrival_shuffle = 1.0;
        p.book_depth_nesting = 1.0;
        p.book_cross_at_limit = 1.0;
        p.order_flow_depth_law = 1.0;
        p.order_flow_impact_law = 1.0;
        p.order_flow_coefficient = 800.0;
        // A guard, as a long run needs: the price cap far above any price
        // a run reaches.
        p.price_hard_cap = 1000000000.0;
        p
    }

    /// Look a shipped preset up by name. `"pt-v1"` remains selectable and
    /// bit-reproducing forever; `"pt-v2"` is the calibrated candidate that
    /// joined the table on 2026-08-22; `"pt-v3"` is
    /// the converged margined optimum that replaced it as the default the
    /// same day.
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
            "pt-v20" => Some(PT_V20),
            "pt-v21" => Some(PT_V21),
            _ => None,
        }
    }

    /// Names of the shipped presets, for error messages.
    pub fn preset_names() -> &'static [&'static str] {
        &["pt-v1", "pt-v2", "pt-v3", "pt-v4", "pt-v5", "pt-v6", "pt-v7", "pt-v8", "pt-v9", "pt-v10",
          "pt-v11", "pt-v12", "pt-v13", "pt-v14", "pt-v15",
          "pt-v16", "pt-v18", "pt-v19", "pt-v20", "pt-v21"]
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
            "market_beta_normalise" => self.market_beta_normalise,
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
            "order_flow_depth_law" => self.order_flow_depth_law,
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
            "market_vol_slow_gamma" => self.market_vol_slow_gamma,
            "market_vol_leverage" => self.market_vol_leverage,
            "market_vol_leverage_half_life" => self.market_vol_leverage_half_life,
            "market_vol_leverage_down" => self.market_vol_leverage_down,
            "market_vol_leverage_standardise" => self.market_vol_leverage_standardise,
            "market_day_tail_df" => self.market_day_tail_df,
            "market_day_tail_state_share" => self.market_day_tail_state_share,
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
            "macro_calendar_days_per_year" => self.macro_calendar_days_per_year,
            "cycle_us_calibration" => self.cycle_us_calibration,
            "fed_liftoff_rule" => self.fed_liftoff_rule,
            "market_pe_buybacks" => self.market_pe_buybacks,
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
            "market_vol_vix_exponent_below" => self.market_vol_vix_exponent_below,
            "market_beta_down_asym" => self.market_beta_down_asym,
            "market_beta_down_asym_lag" => self.market_beta_down_asym_lag,
            "market_beta_down_asym_lag_live" => self.market_beta_down_asym_lag_live,
            "market_beta_down_asym_recentre" => self.market_beta_down_asym_recentre,
            "market_beta_down_asym_lag_recentre" => self.market_beta_down_asym_lag_recentre,
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
            "cascade_gain" => self.cascade_gain,
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
            "idio_vol_alpha" => self.idio_vol_alpha,
            "idio_vol_beta" => self.idio_vol_beta,
            "idio_vol_jump_bump" => self.idio_vol_jump_bump,
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
            "news_absorption_half_life" => self.news_absorption_half_life,
            "news_absorption_drift_share" => self.news_absorption_drift_share,
            "news_absorption_drift_half_life" => self.news_absorption_drift_half_life,
            "news_quote_revision" => self.news_quote_revision,
            "quote_model_weight" => self.quote_model_weight,
            "closing_auction" => self.closing_auction,
            "earnings_cycle_depth" => self.earnings_cycle_depth,
            "earnings_cycle_upside" => self.earnings_cycle_upside,
            "earnings_cycle_half_life" => self.earnings_cycle_half_life,
            "earnings_cycle_sigma" => self.earnings_cycle_sigma,
            "earnings_anticipation_half_life" => self.earnings_anticipation_half_life,
            "rate_pe_sensitivity" => self.rate_pe_sensitivity,
            "cycle_publication_lag" => self.cycle_publication_lag,
            "cycle_publication_lag_draw" => self.cycle_publication_lag_draw,
            "gdp_publication_lag" => self.gdp_publication_lag,
            "unemployment_adjustment_half_life" => self.unemployment_adjustment_half_life,
            "unemployment_natural_pull" => self.unemployment_natural_pull,
            "unemployment_okun_coefficient" => self.unemployment_okun_coefficient,
            "unemployment_natural_rate" => self.unemployment_natural_rate,
            "oil_inventory_reversion" => self.oil_inventory_reversion,
            "oil_inflation_passthrough" => self.oil_inflation_passthrough,
            "index_level_listed" => self.index_level_listed,
            "vix_intraday_live" => self.vix_intraday_live,
            "forecast_horizon_sessions" => self.forecast_horizon_sessions,
            "forecast_vix_dispersion" => self.forecast_vix_dispersion,
            "forecast_vix_dispersion_half_life" => self.forecast_vix_dispersion_half_life,
            "forecast_policy_shadow_discount" => self.forecast_policy_shadow_discount,
            "forecast_policy_persistence" => self.forecast_policy_persistence,
            "forecast_policy_reversion" => self.forecast_policy_reversion,
            "forecast_policy_neutral" => self.forecast_policy_neutral,
            "fear_greed_published_inputs" => self.fear_greed_published_inputs,
            "macro_publication_repricing" => self.macro_publication_repricing,
            "treasury_10y_noise" => self.treasury_10y_noise,
            "treasury_2y_noise" => self.treasury_2y_noise,
            "flight_to_quality_gain" => self.flight_to_quality_gain,
            "flight_to_quality_day" => self.flight_to_quality_day,
            "corporate_yield_daily" => self.corporate_yield_daily,
            "cycle_nowcast_accuracy" => self.cycle_nowcast_accuracy,
            "corporate_spread_cycle" => self.corporate_spread_cycle,
            "earnings_anticipation_drift_share" => self.earnings_anticipation_drift_share,
            "earnings_anticipation_drift_half_life" => self.earnings_anticipation_drift_half_life,
            "fed_growth_cut" => self.fed_growth_cut,
            "macro_pins_hold" => self.macro_pins_hold,
            "fair_value_news_share" => self.fair_value_news_share,
            "fair_value_market_share" => self.fair_value_market_share,
            "fair_value_market_linear" => self.fair_value_market_linear,
            "fair_value_market_vol_cap" => self.fair_value_market_vol_cap,
            "fair_value_market_excess_share" => self.fair_value_market_excess_share,
            "fair_value_vix_discount" => self.fair_value_vix_discount,
            "fair_value_vix_knee" => self.fair_value_vix_knee,
            "fair_value_vix_half_life" => self.fair_value_vix_half_life,
            "fair_value_vix_release_half_life" => self.fair_value_vix_release_half_life,
            "fair_value_relative_knee" => self.fair_value_relative_knee,
            "fair_value_relative_half_life" => self.fair_value_relative_half_life,
            "pinned_vix_feedback" => self.pinned_vix_feedback,
            "pinned_vix_variance_share" => self.pinned_vix_variance_share,
            "pinned_vix_calm_knee" => self.pinned_vix_calm_knee,
            "pinned_vix_calm_share" => self.pinned_vix_calm_share,
            "pinned_vix_priced_cap" => self.pinned_vix_priced_cap,
            "buyback_yield_cap" => self.buyback_yield_cap,
            "buyback_accrual" => self.buyback_accrual,
            "rate_close_remark" => self.rate_close_remark,
            "rate_intraday_live" => self.rate_intraday_live,
            "fed_stress_cut" => self.fed_stress_cut,
            "fed_stress_vix" => self.fed_stress_vix,
            "fed_stress_inflation_gap" => self.fed_stress_inflation_gap,
            "dividend_payout_share" => self.dividend_payout_share,
            "dividend_growth_cutoff" => self.dividend_growth_cutoff,
            "dividend_adjustment_speed" => self.dividend_adjustment_speed,
            "dividend_yield_ceiling" => self.dividend_yield_ceiling,
            "dividend_buyback_substitution" => self.dividend_buyback_substitution,
            "overnight_market_share" => self.overnight_market_share,
            "overnight_idio_share" => self.overnight_idio_share,
            "overnight_idio_df" => self.overnight_idio_df,
            "earnings_surprise_sigma" => self.earnings_surprise_sigma,
            "earnings_surprise_df" => self.earnings_surprise_df,
            "earnings_session_sigma" => self.earnings_session_sigma,
            "earnings_followthrough_sigma" => self.earnings_followthrough_sigma,
            "earnings_volume_multiple" => self.earnings_volume_multiple,
            "earnings_cycle_report_share" => self.earnings_cycle_report_share,
            "market_vol_cycle_ratio" => self.market_vol_cycle_ratio,
            "market_vol_cycle_expansion" => self.market_vol_cycle_expansion,
            "market_vol_cycle_half_life" => self.market_vol_cycle_half_life,
            "market_vol_cycle_relative" => self.market_vol_cycle_relative,
            "market_vol_cycle_relative_calm" => self.market_vol_cycle_relative_calm,
            "market_vol_cycle_cap_relative" => self.market_vol_cycle_cap_relative,
            "market_vol_cycle_pin_neutral" => self.market_vol_cycle_pin_neutral,
            "market_vol_cycle_pin_phase" => self.market_vol_cycle_pin_phase,
            "market_vol_cycle_trough_release" => self.market_vol_cycle_trough_release,
            "market_vol_cycle_release_half_life" => self.market_vol_cycle_release_half_life,
            "market_vol_cycle_recovery_release" => self.market_vol_cycle_recovery_release,
            "market_vol_cycle_recovery_scale" => self.market_vol_cycle_recovery_scale,
            "vix_stress_premium" => self.vix_stress_premium,
            "vix_stress_premium_knee" => self.vix_stress_premium_knee,
            "vix_stress_premium_cap" => self.vix_stress_premium_cap,
            "vix_fear_uptake" => self.vix_fear_uptake,
            "vix_fear_half_life" => self.vix_fear_half_life,
            "fed_put_gain" => self.fed_put_gain,
            "fed_put_threshold" => self.fed_put_threshold,
            "fed_put_half_life" => self.fed_put_half_life,
            "fed_put_emergency_vix" => self.fed_put_emergency_vix,
            "treasury_put_pricing" => self.treasury_put_pricing,
            "treasury_haven_gain" => self.treasury_haven_gain,
            "fed_stress_hold" => self.fed_stress_hold,
            "treasury_path_pricing" => self.treasury_path_pricing,
            "treasury_path_half_life" => self.treasury_path_half_life,
            "treasury_policy_damping" => self.treasury_policy_damping,
            "policy_anticipation" => self.policy_anticipation,
            "policy_anticipation_cut_share" => self.policy_anticipation_cut_share,
            "corporate_spread_vix_cut" => self.corporate_spread_vix_cut,
            "corporate_spread_equity_gain" => self.corporate_spread_equity_gain,
            "corporate_spread_equity_half_life" => self.corporate_spread_equity_half_life,
            "cycle_equity_hazard" => self.cycle_equity_hazard,
            "cycle_equity_hazard_knee" => self.cycle_equity_hazard_knee,
            "cycle_equity_hazard_opening" => self.cycle_equity_hazard_opening,
            "market_prehistory_sessions" => self.market_prehistory_sessions,
            "market_prehistory_valuation" => self.market_prehistory_valuation,
            "fed_put_carry" => self.fed_put_carry,
            "fed_drawdown_hold" => self.fed_drawdown_hold,
            "opening_mispricing_sigma" => self.opening_mispricing_sigma,
            "opening_market_sigma" => self.opening_market_sigma,
            "book_depth_coefficient" => self.book_depth_coefficient,
            "book_depth_exponent" => self.book_depth_exponent,
            "book_depth_reach" => self.book_depth_reach,
            "book_depth_nesting" => self.book_depth_nesting,
            "book_shared" => self.book_shared,
            "book_refill_half_life" => self.book_refill_half_life,
            "book_resting" => self.book_resting,
            "book_arrival_shuffle" => self.book_arrival_shuffle,
            "book_cross_at_limit" => self.book_cross_at_limit,
            "fill_impact_coefficient" => self.fill_impact_coefficient,
            "impact_memory_coefficient" => self.impact_memory_coefficient,
            "impact_memory_half_life" => self.impact_memory_half_life,
            "impact_memory_slow_half_life" => self.impact_memory_slow_half_life,
            "impact_memory_slow_weight" => self.impact_memory_slow_weight,
            "impact_memory_crossover" => self.impact_memory_crossover,
            "impact_memory_refill" => self.impact_memory_refill,
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
            "market_beta_normalise" => out.market_beta_normalise = value,
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
            "order_flow_depth_law" => out.order_flow_depth_law = value,
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
            "market_vol_slow_gamma" => out.market_vol_slow_gamma = value,
            "market_vol_leverage" => out.market_vol_leverage = value,
            "market_vol_leverage_half_life" => out.market_vol_leverage_half_life = value,
            "market_vol_leverage_down" => out.market_vol_leverage_down = value,
            "market_vol_leverage_standardise" => out.market_vol_leverage_standardise = value,
            "market_day_tail_df" => out.market_day_tail_df = value,
            "market_day_tail_state_share" => out.market_day_tail_state_share = value,
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
            "macro_calendar_days_per_year" => out.macro_calendar_days_per_year = value,
            "cycle_us_calibration" => out.cycle_us_calibration = value,
            "fed_liftoff_rule" => out.fed_liftoff_rule = value,
            "market_pe_buybacks" => out.market_pe_buybacks = value,
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
            "market_vol_vix_exponent_below" => out.market_vol_vix_exponent_below = value,
            "market_beta_down_asym" => out.market_beta_down_asym = value,
            "market_beta_down_asym_lag" => out.market_beta_down_asym_lag = value,
            "market_beta_down_asym_lag_live" => out.market_beta_down_asym_lag_live = value,
            "market_beta_down_asym_recentre" => out.market_beta_down_asym_recentre = value,
            "market_beta_down_asym_lag_recentre" => out.market_beta_down_asym_lag_recentre = value,
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
            "cascade_gain" => out.cascade_gain = value,
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
            "idio_vol_alpha" => out.idio_vol_alpha = value,
            "idio_vol_beta" => out.idio_vol_beta = value,
            "idio_vol_jump_bump" => out.idio_vol_jump_bump = value,
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
            "news_absorption_half_life" => out.news_absorption_half_life = value,
            "news_absorption_drift_share" => out.news_absorption_drift_share = value,
            "news_absorption_drift_half_life" => out.news_absorption_drift_half_life = value,
            "news_quote_revision" => out.news_quote_revision = value,
            "quote_model_weight" => out.quote_model_weight = value,
            "closing_auction" => out.closing_auction = value,
            "earnings_cycle_depth" => out.earnings_cycle_depth = value,
            "earnings_cycle_upside" => out.earnings_cycle_upside = value,
            "earnings_cycle_half_life" => out.earnings_cycle_half_life = value,
            "earnings_cycle_sigma" => out.earnings_cycle_sigma = value,
            "earnings_anticipation_half_life" => out.earnings_anticipation_half_life = value,
            "rate_pe_sensitivity" => out.rate_pe_sensitivity = value,
            "cycle_publication_lag" => out.cycle_publication_lag = value,
            "cycle_publication_lag_draw" => out.cycle_publication_lag_draw = value,
            "gdp_publication_lag" => out.gdp_publication_lag = value,
            "unemployment_adjustment_half_life" => out.unemployment_adjustment_half_life = value,
            "unemployment_natural_pull" => out.unemployment_natural_pull = value,
            "unemployment_okun_coefficient" => out.unemployment_okun_coefficient = value,
            "unemployment_natural_rate" => out.unemployment_natural_rate = value,
            "oil_inventory_reversion" => out.oil_inventory_reversion = value,
            "oil_inflation_passthrough" => out.oil_inflation_passthrough = value,
            "index_level_listed" => out.index_level_listed = value,
            "vix_intraday_live" => out.vix_intraday_live = value,
            "forecast_horizon_sessions" => out.forecast_horizon_sessions = value,
            "forecast_vix_dispersion" => out.forecast_vix_dispersion = value,
            "forecast_vix_dispersion_half_life" => out.forecast_vix_dispersion_half_life = value,
            "forecast_policy_shadow_discount" => out.forecast_policy_shadow_discount = value,
            "forecast_policy_persistence" => out.forecast_policy_persistence = value,
            "forecast_policy_reversion" => out.forecast_policy_reversion = value,
            "forecast_policy_neutral" => out.forecast_policy_neutral = value,
            "fear_greed_published_inputs" => out.fear_greed_published_inputs = value,
            "macro_publication_repricing" => out.macro_publication_repricing = value,
            "treasury_10y_noise" => out.treasury_10y_noise = value,
            "treasury_2y_noise" => out.treasury_2y_noise = value,
            "flight_to_quality_gain" => out.flight_to_quality_gain = value,
            "flight_to_quality_day" => out.flight_to_quality_day = value,
            "corporate_yield_daily" => out.corporate_yield_daily = value,
            "cycle_nowcast_accuracy" => out.cycle_nowcast_accuracy = value,
            "corporate_spread_cycle" => out.corporate_spread_cycle = value,
            "earnings_anticipation_drift_share" => out.earnings_anticipation_drift_share = value,
            "earnings_anticipation_drift_half_life" => out.earnings_anticipation_drift_half_life = value,
            "fed_growth_cut" => out.fed_growth_cut = value,
            "macro_pins_hold" => out.macro_pins_hold = value,
            "fair_value_news_share" => out.fair_value_news_share = value,
            "fair_value_market_share" => out.fair_value_market_share = value,
            "fair_value_market_linear" => out.fair_value_market_linear = value,
            "fair_value_market_vol_cap" => out.fair_value_market_vol_cap = value,
            "fair_value_market_excess_share" => out.fair_value_market_excess_share = value,
            "fair_value_vix_discount" => out.fair_value_vix_discount = value,
            "fair_value_vix_knee" => out.fair_value_vix_knee = value,
            "fair_value_vix_half_life" => out.fair_value_vix_half_life = value,
            "fair_value_vix_release_half_life" => out.fair_value_vix_release_half_life = value,
            "fair_value_relative_knee" => out.fair_value_relative_knee = value,
            "fair_value_relative_half_life" => out.fair_value_relative_half_life = value,
            "pinned_vix_feedback" => out.pinned_vix_feedback = value,
            "pinned_vix_variance_share" => out.pinned_vix_variance_share = value,
            "pinned_vix_calm_knee" => out.pinned_vix_calm_knee = value,
            "pinned_vix_calm_share" => out.pinned_vix_calm_share = value,
            "pinned_vix_priced_cap" => out.pinned_vix_priced_cap = value,
            "buyback_yield_cap" => out.buyback_yield_cap = value,
            "buyback_accrual" => out.buyback_accrual = value,
            "rate_close_remark" => out.rate_close_remark = value,
            "rate_intraday_live" => out.rate_intraday_live = value,
            "fed_stress_cut" => out.fed_stress_cut = value,
            "fed_stress_vix" => out.fed_stress_vix = value,
            "fed_stress_inflation_gap" => out.fed_stress_inflation_gap = value,
            "dividend_payout_share" => out.dividend_payout_share = value,
            "dividend_growth_cutoff" => out.dividend_growth_cutoff = value,
            "dividend_adjustment_speed" => out.dividend_adjustment_speed = value,
            "dividend_yield_ceiling" => out.dividend_yield_ceiling = value,
            "dividend_buyback_substitution" => out.dividend_buyback_substitution = value,
            "overnight_market_share" => out.overnight_market_share = value,
            "overnight_idio_share" => out.overnight_idio_share = value,
            "overnight_idio_df" => out.overnight_idio_df = value,
            "earnings_surprise_sigma" => out.earnings_surprise_sigma = value,
            "earnings_surprise_df" => out.earnings_surprise_df = value,
            "earnings_session_sigma" => out.earnings_session_sigma = value,
            "earnings_followthrough_sigma" => out.earnings_followthrough_sigma = value,
            "earnings_volume_multiple" => out.earnings_volume_multiple = value,
            "earnings_cycle_report_share" => out.earnings_cycle_report_share = value,
            "market_vol_cycle_ratio" => out.market_vol_cycle_ratio = value,
            "market_vol_cycle_expansion" => out.market_vol_cycle_expansion = value,
            "market_vol_cycle_half_life" => out.market_vol_cycle_half_life = value,
            "market_vol_cycle_relative" => out.market_vol_cycle_relative = value,
            "market_vol_cycle_relative_calm" => out.market_vol_cycle_relative_calm = value,
            "market_vol_cycle_cap_relative" => out.market_vol_cycle_cap_relative = value,
            "market_vol_cycle_pin_neutral" => out.market_vol_cycle_pin_neutral = value,
            "market_vol_cycle_pin_phase" => out.market_vol_cycle_pin_phase = value,
            "market_vol_cycle_trough_release" => out.market_vol_cycle_trough_release = value,
            "market_vol_cycle_release_half_life" => out.market_vol_cycle_release_half_life = value,
            "market_vol_cycle_recovery_release" => out.market_vol_cycle_recovery_release = value,
            "market_vol_cycle_recovery_scale" => out.market_vol_cycle_recovery_scale = value,
            "vix_stress_premium" => out.vix_stress_premium = value,
            "vix_stress_premium_knee" => out.vix_stress_premium_knee = value,
            "vix_stress_premium_cap" => out.vix_stress_premium_cap = value,
            "vix_fear_uptake" => out.vix_fear_uptake = value,
            "vix_fear_half_life" => out.vix_fear_half_life = value,
            "fed_put_gain" => out.fed_put_gain = value,
            "fed_put_threshold" => out.fed_put_threshold = value,
            "fed_put_half_life" => out.fed_put_half_life = value,
            "fed_put_emergency_vix" => out.fed_put_emergency_vix = value,
            "treasury_put_pricing" => out.treasury_put_pricing = value,
            "treasury_haven_gain" => out.treasury_haven_gain = value,
            "fed_stress_hold" => out.fed_stress_hold = value,
            "treasury_path_pricing" => out.treasury_path_pricing = value,
            "treasury_path_half_life" => out.treasury_path_half_life = value,
            "treasury_policy_damping" => out.treasury_policy_damping = value,
            "policy_anticipation" => out.policy_anticipation = value,
            "policy_anticipation_cut_share" => out.policy_anticipation_cut_share = value,
            "corporate_spread_vix_cut" => out.corporate_spread_vix_cut = value,
            "corporate_spread_equity_gain" => out.corporate_spread_equity_gain = value,
            "corporate_spread_equity_half_life" => out.corporate_spread_equity_half_life = value,
            "cycle_equity_hazard" => out.cycle_equity_hazard = value,
            "cycle_equity_hazard_knee" => out.cycle_equity_hazard_knee = value,
            "cycle_equity_hazard_opening" => out.cycle_equity_hazard_opening = value,
            "market_prehistory_sessions" => out.market_prehistory_sessions = value,
            "market_prehistory_valuation" => out.market_prehistory_valuation = value,
            "fed_put_carry" => out.fed_put_carry = value,
            "fed_drawdown_hold" => out.fed_drawdown_hold = value,
            "opening_mispricing_sigma" => out.opening_mispricing_sigma = value,
            "opening_market_sigma" => out.opening_market_sigma = value,
            "book_depth_coefficient" => out.book_depth_coefficient = value,
            "book_depth_exponent" => out.book_depth_exponent = value,
            "book_depth_reach" => out.book_depth_reach = value,
            "book_depth_nesting" => out.book_depth_nesting = value,
            "book_shared" => out.book_shared = value,
            "book_refill_half_life" => out.book_refill_half_life = value,
            "book_resting" => out.book_resting = value,
            "book_arrival_shuffle" => out.book_arrival_shuffle = value,
            "book_cross_at_limit" => out.book_cross_at_limit = value,
            "fill_impact_coefficient" => out.fill_impact_coefficient = value,
            "impact_memory_coefficient" => out.impact_memory_coefficient = value,
            "impact_memory_half_life" => out.impact_memory_half_life = value,
            "impact_memory_slow_half_life" => out.impact_memory_slow_half_life = value,
            "impact_memory_slow_weight" => out.impact_memory_slow_weight = value,
            "impact_memory_crossover" => out.impact_memory_crossover = value,
            "impact_memory_refill" => out.impact_memory_refill = value,
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
    ///
    /// A switch in [`DIGEST_SILENT_AT_ZERO`] is left out while it is 0.0
    /// (either sign), because at zero its branch is the model that existed
    /// before the switch did. So adding one moves no preset's digest, no
    /// custom model's fingerprint, and no state hash or manifest that
    /// carries one. Off zero it is hashed like any other dial. A dial in
    /// [`DIGEST_SILENT_AT_DEFAULT`] is left out the same way while it holds
    /// its (non-zero) default, which its gating switch at zero never reads.
    pub fn digest(&self) -> String {
        #[cfg(test)]
        DIGESTS_TAKEN.with(|n| n.set(n.get() + 1));
        let mut hasher = Sha256::new();
        for (name, value) in self.to_pairs() {
            if value == 0.0 && DIGEST_SILENT_AT_ZERO.contains(&name.as_str()) {
                continue;
            }
            if DIGEST_SILENT_AT_DEFAULT
                .iter()
                .any(|(n, d)| *n == name.as_str() && value.to_bits() == d.to_bits())
            {
                continue;
            }
            hasher.update(name.as_bytes());
            hasher.update(b"=");
            hasher.update(value.to_bits().to_be_bytes());
            hasher.update(b"\n");
        }
        lower_hex(&hasher.finalize())
    }

    /// The honest name: a shipped preset's name when bit-identical to it,
    /// `custom-XXXXXXXX` (first 8 hex of the digest) otherwise. A run under
    /// a non-shipped preset can never present as a standard one.
    ///
    /// The shipped presets' own digests are constants of the build, so they
    /// are worked out once per process (in a private table) rather than on
    /// every call. The comparison is the one it was, in the same order.
    pub fn fingerprint(&self) -> String {
        let digest = self.digest();
        for (name, shipped) in shipped_digests() {
            if *shipped == digest {
                return (*name).to_string();
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
    /// The agent-facing book's dials: ranges, and the companions each is
    /// read by nothing without. Part of [`ModelParams::invariants`].
    fn book_invariants(&self) -> Result<(), String> {
        let y = self.book_depth_coefficient;
        if !(0.0..=10.0).contains(&y) {
            return Err(format!(
                "book_depth_coefficient is {y}. It is the latent depth's Y in \
                 Y sigma (Q/V)^delta, a non-negative number of order one; 0.0 \
                 is no depth past the maker's ladder. Set it inside [0, 10]."));
        }
        if y == 0.0 {
            for (name, v) in [("book_depth_exponent", self.book_depth_exponent),
                              ("book_depth_reach", self.book_depth_reach),
                              ("book_depth_nesting", self.book_depth_nesting)] {
                if v != 0.0 {
                    return Err(format!(
                        "{name} is {v} but book_depth_coefficient is 0: it shapes \
                         the latent depth and is read by nothing without it."));
                }
            }
        }
        let d = self.book_depth_exponent;
        if !(0.0..=1.0).contains(&d) {
            return Err(format!(
                "book_depth_exponent is {d}. It is the exponent of the \
                 price-for-size law, in [0, 1]: 0.5 is the square root, 1.0 is \
                 linear, and 0.0 reads as the square root."));
        }
        let r = self.book_depth_reach;
        if !(0.0..=10.0).contains(&r) {
            return Err(format!(
                "book_depth_reach is {r}. It is how far the latent depth reaches, \
                 in multiples of daily volume, inside [0, 10]; 0.0 reads as one \
                 day's volume."));
        }
        let k = self.book_depth_nesting;
        if !(0.0..=1.0).contains(&k) {
            return Err(format!(
                "book_depth_nesting is {k}. It is the share of the maker's ladder \
                 the latent depth counts as its own front, in [0, 1]: 0.0 puts \
                 the latent pool beside the ladder, 1.0 makes the depth within \
                 any distance the larger of the two."));
        }
        let h = self.book_refill_half_life;
        if !(0.0..=390.0).contains(&h) {
            return Err(format!(
                "book_refill_half_life is {h}. It is a half-life in ticks inside \
                 the 390-tick session, in [0, 390]; 0.0 refills at the next tick."));
        }
        if h != 0.0 && (self.book_shared == 0.0 || y == 0.0) {
            return Err(format!(
                "book_refill_half_life is {h} but the consumed latent depth it \
                 refills needs book_shared on and book_depth_coefficient off zero: \
                 it is read by nothing without both."));
        }
        let g = self.fill_impact_coefficient;
        if !(0.0..=5.0).contains(&g) {
            return Err(format!(
                "fill_impact_coefficient is {g}. It is gamma in gamma sigma Q/V, \
                 non-negative and of order 0.1 to 1 (Almgren et al. 2005 measure \
                 0.314); 0.0 is the order-imbalance law. Set it inside [0, 5]."));
        }
        self.impact_memory_invariants()
    }

    /// The metaorder memory's dials: ranges, and the companions each needs.
    /// Part of [`ModelParams::invariants`] through `book_invariants`.
    fn impact_memory_invariants(&self) -> Result<(), String> {
        let y = self.impact_memory_coefficient;
        let (h1, h2, w, m) = (self.impact_memory_half_life, self.impact_memory_slow_half_life,
                              self.impact_memory_slow_weight, self.impact_memory_crossover);
        // Each refusal states the set the dial accepts beside the others, so
        // a range read from the text is the range the validator keeps
        // (tests/test_dial_ranges.py). A refusal that two dials' values
        // cause together names first the dial whose set it states.
        let bdc = self.book_depth_coefficient;
        let can_run = self.book_shared == 1.0 && bdc > 0.0 && h1 > 0.0;
        let y_hi = if can_run { crate::mathx::min(bdc, 10.0) } else { 0.0 };
        if !(0.0..=10.0).contains(&y) {
            return Err(format!(
                "impact_memory_coefficient is {y}. It is the metaorder memory's Y in \
                 Y sigma (M)^delta, in [0, {y_hi}] here; 0.0 is off. On, it is at most \
                 10 and at most book_depth_coefficient ({bdc}), and it needs book_shared \
                 on and impact_memory_half_life above 0."));
        }
        // The shape dials are range-checked always and read only with the
        // coefficient on, so a vector may carry them at the coefficient's
        // 0.0 (the perturbation table's base does).
        let h1_hi = if h2 > 0.0 && h2 <= 98280.0 { crate::mathx::min(h2, 39000.0) } else { 39000.0 };
        let h1_lo = if y != 0.0 { "(0" } else { "[0" };
        if !(0.0..=39000.0).contains(&h1) {
            return Err(format!(
                "impact_memory_half_life is {h1}. It is a half-life in open ticks, in \
                 {h1_lo}, {h1_hi}] here: at most 39000, at most \
                 impact_memory_slow_half_life when the slow part is on, and above 0 \
                 with the memory on."));
        }
        if h2 > 0.0 && h2 <= 98280.0 && h2 < h1 {
            return Err(format!(
                "impact_memory_half_life is {h1} but impact_memory_slow_half_life is \
                 {h2}: the slow part's half-life is at least the fast part's, so \
                 impact_memory_half_life is in {h1_lo}, {h1_hi}] here."));
        }
        if !(h2 == 0.0 || (h2 >= h1 && h2 <= 98280.0)) {
            return Err(format!(
                "impact_memory_slow_half_life is {h2}. It is 0.0 (no slow part) or a \
                 half-life in open ticks from impact_memory_half_life ({h1}) to 98280."));
        }
        if !(0.0..1.0).contains(&w) {
            return Err(format!(
                "impact_memory_slow_weight is {w}. It is the slow part's weight, in [0, 1)."));
        }
        if w != 0.0 && h2 == 0.0 {
            return Err(format!(
                "impact_memory_slow_weight is {w} but impact_memory_slow_half_life is 0: \
                 the weight is read by nothing without the slow part."));
        }
        if !(0.0..=0.05).contains(&m) {
            return Err(format!(
                "impact_memory_crossover is {m}. It is a fraction of daily volume, in \
                 [0, 0.05]; 0.0 is a pure power law."));
        }
        let r = self.impact_memory_refill;
        if !(r == 0.0 || r == 1.0) {
            return Err(format!(
                "impact_memory_refill is {r}. It is a switch: 0.0 as shipped, 1.0 on."));
        }
        if y == 0.0 {
            return Ok(());
        }
        if self.book_shared != 1.0 || !(bdc > 0.0) {
            return Err(format!(
                "impact_memory_coefficient is {y} but the memory needs book_shared on \
                 and book_depth_coefficient off zero: it continues the latent depth's \
                 curve and is fed by the flow the shared book records. Here it is in \
                 [0, {y_hi}], off."));
        }
        if y > bdc {
            return Err(format!(
                "impact_memory_coefficient is {y} but book_depth_coefficient is {bdc}. The \
                 memory's Y must not exceed the latent book's, or a fill could be priced \
                 better than the displacement it leaves (Alfonsi, Fruth and Schied 2010), \
                 so here it is in [0, {y_hi}]."));
        }
        if !(h1 > 0.0) {
            return Err(format!(
                "impact_memory_coefficient is {y} but impact_memory_half_life is 0: the \
                 memory needs a half-life, in open ticks, in (0, 39000], so here it is \
                 in [0, {y_hi}], off."));
        }
        Ok(())
    }

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
            // The solve reads the extra's square, so a negative extra would
            // run as its own magnitude; the interval is the one stated.
            let x = self.crisis_epicentre_extra;
            if !(x > lo && x < hi && up2 > 0.0 && down2 > 0.0) {
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
        if self.market_vol_vix_exponent_below != 0.0
            && !(self.market_vol_vix_exponent_below > 0.0
                 && self.market_vol_vix_exponent_below.is_finite())
        {
            return Err(format!(
                "market_vol_vix_exponent_below is {}. It is the exponent on the VIX \
                 ratio below the anchor, so it is positive and finite, or 0.0 to read \
                 market_vol_vix_exponent on both sides.",
                self.market_vol_vix_exponent_below));
        }
        if !(self.macro_calendar_days_per_year >= 24.0
            && self.macro_calendar_days_per_year <= 365.0
            && self.macro_calendar_days_per_year.fract() == 0.0)
        {
            return Err(format!(
                "macro_calendar_days_per_year is {}. It is the number of economy steps in a \
                 macro year: 365.0 as shipped, 252.0 on the session calendar. Set a whole \
                 number inside [24, 365].",
                self.macro_calendar_days_per_year));
        }
        if !(self.cycle_nowcast_accuracy == 0.0
            || (self.cycle_nowcast_accuracy > 0.2 && self.cycle_nowcast_accuracy <= 1.0))
        {
            return Err(format!(
                "cycle_nowcast_accuracy is {}. It is the probability a session's report names \
                 the true phase, in (0.2, 1], or 0.0 to price the true phase.",
                self.cycle_nowcast_accuracy));
        }
        if !(self.corporate_spread_cycle >= 0.0 && self.corporate_spread_cycle <= 1.0) {
            return Err(format!(
                "corporate_spread_cycle is {}. It is a share, in [0, 1].",
                self.corporate_spread_cycle));
        }
        if !(self.earnings_anticipation_drift_share >= 0.0
            && self.earnings_anticipation_drift_share <= 1.0)
        {
            return Err(format!(
                "earnings_anticipation_drift_share is {}. It is a share, in [0, 1]; 0 prices the \
                 anticipated level as it stood.",
                self.earnings_anticipation_drift_share));
        }
        if !(self.earnings_anticipation_drift_half_life >= 0.0
            && self.earnings_anticipation_drift_half_life <= 5040.0)
        {
            return Err(format!(
                "earnings_anticipation_drift_half_life is {}. It is sessions, in [0, 5040]; 0 is 1260.",
                self.earnings_anticipation_drift_half_life));
        }
        if !(self.fed_growth_cut >= -5.0 && self.fed_growth_cut <= 5.0) {
            return Err(format!(
                "fed_growth_cut is {}. It is a growth rate in per cent a year, in [-5, 5]; 0 is off.",
                self.fed_growth_cut));
        }
        if !(self.cycle_publication_lag_draw == 0.0 || self.cycle_publication_lag_draw == 1.0) {
            return Err(format!(
                "cycle_publication_lag_draw is {}. It is a switch: 0 (the fixed \
                 cycle_publication_lag) or 1 (a lag drawn for each turn).",
                self.cycle_publication_lag_draw));
        }
        for (name, v) in [("cycle_us_calibration", self.cycle_us_calibration),
                          ("fed_liftoff_rule", self.fed_liftoff_rule),
                          ("market_pe_buybacks", self.market_pe_buybacks),
                          ("news_quote_revision", self.news_quote_revision),
                          ("closing_auction", self.closing_auction),
                          ("flight_to_quality_day", self.flight_to_quality_day),
                          ("corporate_yield_daily", self.corporate_yield_daily),
                          ("macro_pins_hold", self.macro_pins_hold),
                          ("macro_publication_repricing", self.macro_publication_repricing),
                          ("book_shared", self.book_shared),
                          ("book_resting", self.book_resting),
                          ("book_arrival_shuffle", self.book_arrival_shuffle),
                          ("book_cross_at_limit", self.book_cross_at_limit)] {
            if !(v == 0.0 || v == 1.0) {
                return Err(format!(
                    "{name} is {v}. It is a switch: 0.0 as shipped, 1.0 on."));
            }
        }
        for (name, v, hi) in [("treasury_10y_noise", self.treasury_10y_noise, 0.5),
                              ("treasury_2y_noise", self.treasury_2y_noise, 0.5),
                              ("flight_to_quality_gain", self.flight_to_quality_gain, 0.5)] {
            if !(v >= 0.0 && v <= hi) {
                return Err(format!(
                    "{name} is {v}. It is in percentage points of yield, in [0, {hi}]."));
            }
        }
        if !(self.earnings_cycle_depth >= 0.0 && self.earnings_cycle_depth <= 1.5) {
            return Err(format!(
                "earnings_cycle_depth is {}. It is a log level in [0, 1.5]; 0.0 is no cycle.",
                self.earnings_cycle_depth));
        }
        if !(self.earnings_cycle_upside >= 0.0 && self.earnings_cycle_upside <= 1.0) {
            return Err(format!(
                "earnings_cycle_upside is {}. It is a share of the depth, in [0, 1].",
                self.earnings_cycle_upside));
        }
        if !(self.earnings_cycle_half_life >= 1.0 && self.earnings_cycle_half_life <= 2520.0) {
            return Err(format!(
                "earnings_cycle_half_life is {}. It is a half-life in sessions, in [1, 2520].",
                self.earnings_cycle_half_life));
        }
        if !(self.earnings_anticipation_half_life >= 0.0
            && self.earnings_anticipation_half_life <= 5040.0)
        {
            return Err(format!(
                "earnings_anticipation_half_life is {}. It is a half-life in sessions, in [0, 5040]; 0 is off.",
                self.earnings_anticipation_half_life));
        }
        if !(self.buyback_accrual == 0.0 || self.buyback_accrual == 1.0) {
            return Err(format!(
                "buyback_accrual is {}. It is a switch: 0.0 is the term that stood, 1.0 accrues the share count.",
                self.buyback_accrual));
        }
        if !(self.rate_close_remark == 0.0 || self.rate_close_remark == 1.0) {
            return Err(format!(
                "rate_close_remark is {}. It is a switch, 0.0 off or 1.0 on, and 1.0 \
                 while rate_intraday_live is on.",
                self.rate_close_remark));
        }
        if !(self.rate_intraday_live == 0.0 || self.rate_intraday_live == 1.0) {
            return Err(format!(
                "rate_intraday_live is {}. It is a switch, 0.0 off or 1.0 on, and on \
                 only with rate_close_remark on.",
                self.rate_intraday_live));
        }
        if self.rate_intraday_live != 0.0 && self.rate_close_remark == 0.0 {
            return Err(format!(
                "rate_intraday_live is {} but rate_close_remark is 0. The live mark commits \
                 nothing, so without the close's re-mark the curve it anticipated would reach \
                 the rate indices only at the next open: with rate_intraday_live on, \
                 rate_close_remark is 1.0.",
                self.rate_intraday_live));
        }
        if !(self.fed_stress_cut >= 0.0 && self.fed_stress_cut <= 1.0) {
            return Err(format!(
                "fed_stress_cut is {}. It is a cut in points per step, in [0, 1]; 0 is off.",
                self.fed_stress_cut));
        }
        if !(self.fed_stress_vix >= 10.0 && self.fed_stress_vix <= 200.0) {
            return Err(format!(
                "fed_stress_vix is {}. It is a VIX level, in [10, 200].",
                self.fed_stress_vix));
        }
        if !(self.fed_stress_inflation_gap >= 0.0 && self.fed_stress_inflation_gap <= 10.0) {
            return Err(format!(
                "fed_stress_inflation_gap is {}. It is points of inflation over target, in [0, 10].",
                self.fed_stress_inflation_gap));
        }
        for (name, v) in [("overnight_market_share", self.overnight_market_share),
                          ("overnight_idio_share", self.overnight_idio_share)] {
            if !(0.0..=0.9).contains(&v) {
                return Err(format!(
                    "{name} is {v}. It is the night's share of the day's variance, in \
                     [0, 0.9]; 0 is no split."));
            }
        }
        if self.overnight_variance_ratio != 0.0
            && (self.overnight_market_share != 0.0 || self.overnight_idio_share != 0.0)
        {
            return Err(format!(
                "overnight_variance_ratio is {} and a night share is set \
                 (overnight_market_share {}, overnight_idio_share {}). The ratio ADDS a \
                 night to the day and the shares SPLIT the day between the night and \
                 the session; set one or the other.",
                self.overnight_variance_ratio, self.overnight_market_share,
                self.overnight_idio_share));
        }
        for (name, v) in [("overnight_idio_df", self.overnight_idio_df),
                          ("earnings_surprise_df", self.earnings_surprise_df)] {
            if !(v == 0.0 || ((3.0..=30.0).contains(&v) && v == v.floor())) {
                return Err(format!(
                    "{name} is {v}. It is a student t's degrees of freedom, a whole \
                     number in [3, 30], or 0.0 for a normal."));
            }
        }
        for (name, v, hi) in [("earnings_surprise_sigma", self.earnings_surprise_sigma, 20.0),
                              ("earnings_session_sigma", self.earnings_session_sigma, 20.0),
                              ("earnings_followthrough_sigma", self.earnings_followthrough_sigma, 20.0),
                              ("earnings_volume_multiple", self.earnings_volume_multiple, 10.0)] {
            if !(v >= 0.0 && v <= hi) {
                return Err(format!(
                    "{name} is {v}. It is in [0, {hi}]; 0 is off."));
            }
        }
        if !(self.earnings_cycle_report_share >= 0.0 && self.earnings_cycle_report_share <= 1.0) {
            return Err(format!(
                "earnings_cycle_report_share is {}. It is a share of the cycle's move, in [0, 1].",
                self.earnings_cycle_report_share));
        }
        if self.earnings_cycle_report_share != 0.0 && self.earnings_surprise_sigma == 0.0 {
            return Err(format!(
                "earnings_cycle_report_share is {} and earnings_surprise_sigma is 0.0. The \
                 share is given back at a name's report, and without the calendar no \
                 name reports; set the calendar too.",
                self.earnings_cycle_report_share));
        }
        if self.earnings_surprise_sigma != 0.0
            && self.overnight_market_share == 0.0
            && self.overnight_idio_share == 0.0
        {
            return Err(format!(
                "earnings_surprise_sigma is {} and no night share is set. The surprise \
                 is realised at the reaction session's opening print, which only a \
                 split prices; set overnight_idio_share (or overnight_market_share) too.",
                self.earnings_surprise_sigma));
        }
        if !(self.pinned_vix_feedback >= 0.0 && self.pinned_vix_feedback <= 1.0) {
            return Err(format!(
                "pinned_vix_feedback is {}. It is the share of the gap to the pinned VIX's excess \
                 a VIX pin prices, in [0, 1]: 0 is off, 1 the whole gap.",
                self.pinned_vix_feedback));
        }
        if !(self.pinned_vix_variance_share >= 0.0 && self.pinned_vix_variance_share <= 1.0) {
            return Err(format!(
                "pinned_vix_variance_share is {}. It is the most of a session's market-factor variance a pinned VIX's priced move may take, in [0, 1].",
                self.pinned_vix_variance_share));
        }
        if !(self.pinned_vix_calm_knee >= 0.0 && self.pinned_vix_calm_knee <= 200.0) {
            return Err(format!(
                "pinned_vix_calm_knee is {}. It is the VIX level from which a pinned VIX is priced below the knee, in [0, 200]; 0 is none.",
                self.pinned_vix_calm_knee));
        }
        if !(self.pinned_vix_calm_share >= 0.0 && self.pinned_vix_calm_share <= 1.0) {
            return Err(format!(
                "pinned_vix_calm_share is {}. It is the calm line's slope as a share of the knee's, in [0, 1].",
                self.pinned_vix_calm_share));
        }
        if !(self.pinned_vix_priced_cap >= 0.0 && self.pinned_vix_priced_cap <= 1.0) {
            return Err(format!(
                "pinned_vix_priced_cap is {}. It is a switch, in [0, 1].",
                self.pinned_vix_priced_cap));
        }
        if !(self.market_vol_slow_gamma >= 0.0 && self.market_vol_slow_gamma <= 1.0) {
            return Err(format!(
                "market_vol_slow_gamma is {}. It is a GJR loading on the slow component's squared shock, in [0, 1].",
                self.market_vol_slow_gamma));
        }
        if self.market_vol_slow_gamma != 0.0
            && (1.0 - self.market_vol_slow_gain) * self.market_vol_slow_persistence
                - 0.5 * self.market_vol_slow_gamma < 0.0
        {
            return Err(format!(
                "market_vol_slow_gamma is {}, and the slow component carries only {} of its \
                 persistence on its own level ((1 - market_vol_slow_gain) * \
                 market_vol_slow_persistence). The dial gives back half of itself from that \
                 share, so it may be at most twice it.",
                self.market_vol_slow_gamma,
                (1.0 - self.market_vol_slow_gain) * self.market_vol_slow_persistence));
        }
        if !(self.market_vol_leverage >= 0.0 && self.market_vol_leverage <= 50.0) {
            return Err(format!(
                "market_vol_leverage is {}. It is a gain on log variance per unit of the return memory, in [0, 50]; 0 is off.",
                self.market_vol_leverage));
        }
        if !(self.market_vol_leverage_half_life >= 0.0 && self.market_vol_leverage_half_life <= 2520.0) {
            return Err(format!(
                "market_vol_leverage_half_life is {}. It is a half-life in sessions, in [0, 2520].",
                self.market_vol_leverage_half_life));
        }
        if self.market_vol_leverage != 0.0 && self.market_vol_leverage_half_life == 0.0 {
            return Err(format!(
                "market_vol_leverage is {} but market_vol_leverage_half_life is 0. The return \
                 memory needs a half-life in sessions.",
                self.market_vol_leverage));
        }
        if !(self.market_vol_leverage_down >= 0.0 && self.market_vol_leverage_down <= 1.0) {
            return Err(format!(
                "market_vol_leverage_down is {}. It is the share of an up day the return memory ignores, in [0, 1].",
                self.market_vol_leverage_down));
        }
        if !(self.market_beta_normalise >= 0.0 && self.market_beta_normalise <= 1.0) {
            return Err(format!(
                "market_beta_normalise is {}. It is the power of the roster's cap-weighted beta each name's beta is divided by, in [0, 1].",
                self.market_beta_normalise));
        }
        if !(self.market_vol_leverage_standardise >= 0.0 && self.market_vol_leverage_standardise <= 1.0) {
            return Err(format!(
                "market_vol_leverage_standardise is {}. It is the power on the day's own sd the return memory counts a day in, in [0, 1].",
                self.market_vol_leverage_standardise));
        }
        if !(self.market_day_tail_df == 0.0
            || (self.market_day_tail_df >= 3.0 && self.market_day_tail_df <= 200.0))
        {
            return Err(format!(
                "market_day_tail_df is {}. It is the degrees of freedom of the day's market draw, in [3, 200], or 0.0 for a normal day.",
                self.market_day_tail_df));
        }
        if !(self.market_day_tail_state_share >= 0.0 && self.market_day_tail_state_share <= 1.0) {
            return Err(format!(
                "market_day_tail_state_share is {}. It is the share of the day's t scale the market variance state reads, in [0, 1].",
                self.market_day_tail_state_share));
        }
        if !(self.buyback_yield_cap >= 0.0 && self.buyback_yield_cap <= 1.0) {
            return Err(format!(
                "buyback_yield_cap is {}. It is an annual yield, in [0, 1]; 0 is none.",
                self.buyback_yield_cap));
        }
        if !(self.dividend_payout_share >= 0.0 && self.dividend_payout_share <= 2.0) {
            return Err(format!(
                "dividend_payout_share is {}. It is a scale on the sector payout, in [0, 2]; 0 is no dividend.",
                self.dividend_payout_share));
        }
        if !(self.dividend_growth_cutoff >= 0.0 && self.dividend_growth_cutoff <= 10.0) {
            return Err(format!(
                "dividend_growth_cutoff is {}. It is a revenue growth rate, in [0, 10].",
                self.dividend_growth_cutoff));
        }
        if !(self.dividend_adjustment_speed > 0.0 && self.dividend_adjustment_speed <= 1.0) {
            return Err(format!(
                "dividend_adjustment_speed is {}. It is an annual adjustment speed, in (0, 1].",
                self.dividend_adjustment_speed));
        }
        if !(self.dividend_yield_ceiling >= 1.0 && self.dividend_yield_ceiling <= 100.0) {
            return Err(format!(
                "dividend_yield_ceiling is {}. It is a multiple of the target yield, in [1, 100].",
                self.dividend_yield_ceiling));
        }
        if !(self.dividend_buyback_substitution == 0.0 || self.dividend_buyback_substitution == 1.0) {
            return Err(format!(
                "dividend_buyback_substitution is {}. It is a switch, 0 or 1.",
                self.dividend_buyback_substitution));
        }
        if !(self.market_vol_cycle_ratio >= 0.0 && self.market_vol_cycle_ratio <= 5.0) {
            return Err(format!(
                "market_vol_cycle_ratio is {}. It is the market factor's volatility in a contraction \
                 or a trough over its volatility in every other phase, in [0, 5]; 0 is off.",
                self.market_vol_cycle_ratio));
        }
        if !(self.market_vol_cycle_expansion >= 0.0 && self.market_vol_cycle_expansion <= 2.0) {
            return Err(format!(
                "market_vol_cycle_expansion is {}. It is the market factor's volatility multiplier \
                 outside a contraction or a trough, in [0, 2]; 0 derives it from the phase shares.",
                self.market_vol_cycle_expansion));
        }
        if !(self.market_vol_cycle_half_life >= 0.0 && self.market_vol_cycle_half_life <= 2520.0) {
            return Err(format!(
                "market_vol_cycle_half_life is {}. It is a half-life in sessions, in [0, 2520]; 0 is instant.",
                self.market_vol_cycle_half_life));
        }
        if !(self.market_vol_cycle_relative >= 0.0 && self.market_vol_cycle_relative <= 1.0) {
            return Err(format!(
                "market_vol_cycle_relative is {}. It is the power of the cycle multiplier the VIX's \
                 reading of fear is scaled by, in [0, 1].",
                self.market_vol_cycle_relative));
        }
        if !(self.market_vol_cycle_relative_calm >= 0.0 && self.market_vol_cycle_relative_calm <= 1.0) {
            return Err(format!(
                "market_vol_cycle_relative_calm is {}. It is the power of the cycle multiplier the \
                 VIX's reading of fear is scaled by while the multiplier is under one, in [0, 1].",
                self.market_vol_cycle_relative_calm));
        }
        if !(self.market_vol_cycle_cap_relative >= 0.0 && self.market_vol_cycle_cap_relative <= 1.0) {
            return Err(format!(
                "market_vol_cycle_cap_relative is {}. It is the power of the cycle multiplier the \
                 fair-value volatility cap's ceiling is scaled by in a stormier phase, in [0, 1].",
                self.market_vol_cycle_cap_relative));
        }
        if !(self.market_vol_cycle_pin_neutral == 0.0 || self.market_vol_cycle_pin_neutral == 1.0) {
            return Err(format!(
                "market_vol_cycle_pin_neutral is {}. It is a switch, 0 or 1: the cycle \
                 multiplier is not applied on a session whose VIX a caller pinned.",
                self.market_vol_cycle_pin_neutral));
        }
        if !(self.market_vol_cycle_pin_phase == 0.0 || self.market_vol_cycle_pin_phase == 1.0) {
            return Err(format!(
                "market_vol_cycle_pin_phase is {}. It is a switch, 0 or 1: the cycle \
                 multiplier is not applied on a session whose cycle phase a caller pinned.",
                self.market_vol_cycle_pin_phase));
        }
        if !(self.market_vol_cycle_trough_release >= 0.0 && self.market_vol_cycle_trough_release <= 1.0) {
            return Err(format!(
                "market_vol_cycle_trough_release is {}. It is the share of the contraction's \
                 volatility multiplier (in logs) the trough gives back, in [0, 1].",
                self.market_vol_cycle_trough_release));
        }
        if !(self.market_vol_cycle_release_half_life >= 0.0
            && self.market_vol_cycle_release_half_life <= 2520.0) {
            return Err(format!(
                "market_vol_cycle_release_half_life is {}. It is a half-life in sessions, in \
                 [0, 2520]; 0 is market_vol_cycle_half_life.",
                self.market_vol_cycle_release_half_life));
        }
        if !(self.market_vol_cycle_recovery_release >= 0.0
            && self.market_vol_cycle_recovery_release <= 1.0) {
            return Err(format!(
                "market_vol_cycle_recovery_release is {}. It is the share of the contraction's \
                 volatility multiplier (in logs) the index's rally off its low gives back, in \
                 [0, 1].",
                self.market_vol_cycle_recovery_release));
        }
        if self.market_vol_cycle_recovery_release != 0.0
            && !(self.market_vol_cycle_recovery_scale > 0.0
                && self.market_vol_cycle_recovery_scale <= 2.0) {
            return Err(format!(
                "market_vol_cycle_recovery_scale is {} with market_vol_cycle_recovery_release \
                 set. It is the rally off the low, in log points of the index, at which the \
                 release is complete, in (0, 2].",
                self.market_vol_cycle_recovery_scale));
        }
        if !(self.market_vol_cycle_recovery_scale >= 0.0
            && self.market_vol_cycle_recovery_scale <= 2.0) {
            return Err(format!(
                "market_vol_cycle_recovery_scale is {}. It is a rally in log points of the \
                 index, in [0, 2].",
                self.market_vol_cycle_recovery_scale));
        }
        if !(self.vix_stress_premium >= 0.0 && self.vix_stress_premium <= 10.0) {
            return Err(format!(
                "vix_stress_premium is {}. It is the published VIX premium's gain per \
                 unit of the anchor memory above the knee, in [0, 10]; 0 is none.",
                self.vix_stress_premium));
        }
        if !(self.vix_stress_premium_knee >= 0.0 && self.vix_stress_premium_knee <= 3.0) {
            return Err(format!(
                "vix_stress_premium_knee is {}. It is a log deviation of the anchor \
                 memory, in [0, 3]; at or above 0 so a pinned session, whose memory \
                 is 0, publishes the pin.",
                self.vix_stress_premium_knee));
        }
        if !(self.vix_stress_premium_cap >= 0.0 && self.vix_stress_premium_cap <= 1.0) {
            return Err(format!(
                "vix_stress_premium_cap is {}. It is the largest log premium of the \
                 published VIX over the state, in [0, 1].",
                self.vix_stress_premium_cap));
        }
        if self.vix_stress_premium != 0.0 {
            if self.vix_stress_premium_cap == 0.0 {
                return Err(format!(
                    "vix_stress_premium is {} but vix_stress_premium_cap is 0. The \
                     premium approaches the cap, so it needs one above 0.",
                    self.vix_stress_premium));
            }
            if self.vix_level_identity == 0.0 || self.vix_anchor_memory == 0.0 {
                return Err(format!(
                    "vix_stress_premium is {} but vix_level_identity is {} and \
                     vix_anchor_memory is {}. The premium reads the identity's \
                     variance read-back at the anchor memory's rate, so it needs \
                     both. Set them, or vix_stress_premium to 0.0.",
                    self.vix_stress_premium, self.vix_level_identity,
                    self.vix_anchor_memory));
            }
        }
        if !(self.vix_fear_uptake >= 0.0 && self.vix_fear_uptake < 1.0) {
            return Err(format!(
                "vix_fear_uptake is {}. It is the share of the VIX's excursion over its \
                 target the fear memory takes up each session, in [0, 1); 0 is off.",
                self.vix_fear_uptake));
        }
        if self.vix_fear_uptake != 0.0 {
            if !(self.vix_fear_half_life > 0.0 && self.vix_fear_half_life <= 2520.0) {
                return Err(format!(
                    "vix_fear_half_life is {}. With vix_fear_uptake set it is the fear \
                     memory's half-life in sessions, in (0, 2520].",
                    self.vix_fear_half_life));
            }
            if self.vix_level_identity == 0.0 {
                return Err(format!(
                    "vix_fear_uptake is {} but vix_level_identity is 0. The fear memory \
                     moves the identity's target, so it needs the identity. Set it, or \
                     vix_fear_uptake to 0.0.",
                    self.vix_fear_uptake));
            }
        }
        if !(self.fed_put_gain >= 0.0 && self.fed_put_gain <= 10.0) {
            return Err(format!(
                "fed_put_gain is {}. It is percentage points of policy-rate cut per \
                 unit of the index's log fall since the last meeting, in [0, 10]; 0 is off.",
                self.fed_put_gain));
        }
        if !(self.fed_put_threshold >= 0.0 && self.fed_put_threshold <= 0.2) {
            return Err(format!(
                "fed_put_threshold is {}. It is the intermeeting log fall the Fed put \
                 ignores, in [0, 0.2].",
                self.fed_put_threshold));
        }
        if !(self.fed_put_half_life >= 0.0 && self.fed_put_half_life <= 504.0) {
            return Err(format!(
                "fed_put_half_life is {}. It is a half-life in sessions, in [0, 504].",
                self.fed_put_half_life));
        }
        if !(self.fed_put_emergency_vix >= 0.0 && self.fed_put_emergency_vix <= 90.0) {
            return Err(format!(
                "fed_put_emergency_vix is {}. It is a VIX level, in [0, 90]; 0 is never.",
                self.fed_put_emergency_vix));
        }
        if !(self.treasury_put_pricing >= 0.0 && self.treasury_put_pricing <= 1.0) {
            return Err(format!(
                "treasury_put_pricing is {}. It is the share of the Fed put's expected \
                 cut the curve prices, in [0, 1].",
                self.treasury_put_pricing));
        }
        if !(self.treasury_haven_gain >= 0.0 && self.treasury_haven_gain <= 0.05) {
            return Err(format!(
                "treasury_haven_gain is {}. It is percentage points off the 10-year's \
                 term premium per VIX point above 20, in [0, 0.05]; 0 is none.",
                self.treasury_haven_gain));
        }
        if !(self.fed_stress_hold >= 0.0 && self.fed_stress_hold <= 504.0) {
            return Err(format!(
                "fed_stress_hold is {}. It is sessions after a stressed close in which the \
                 bank does not raise the rate, in [0, 504]; 0 is off.",
                self.fed_stress_hold));
        }
        if !(self.treasury_path_pricing >= 0.0 && self.treasury_path_pricing <= 3.0) {
            return Err(format!(
                "treasury_path_pricing is {}. It is the share of the expected policy path \
                 the curve prices, in [0, 3]; 0 is off.",
                self.treasury_path_pricing));
        }
        if !(self.treasury_path_half_life >= 0.0 && self.treasury_path_half_life <= 504.0) {
            return Err(format!(
                "treasury_path_half_life is {}. It is a half-life in sessions, in [0, 504].",
                self.treasury_path_half_life));
        }
        if !(self.treasury_policy_damping >= 0.0 && self.treasury_policy_damping <= 0.9) {
            return Err(format!(
                "treasury_policy_damping is {}. It is the share of the policy rate's \
                 distance from neutral the 10-year leaves out, in [0, 0.9].",
                self.treasury_policy_damping));
        }
        if !(self.policy_anticipation >= 0.0 && self.policy_anticipation <= 3.0) {
            return Err(format!(
                "policy_anticipation is {}. It is how much of the next meeting's expected \
                 change the curve prices before the meeting, in multiples of it, in [0, 3]; \
                 0 is off.",
                self.policy_anticipation));
        }
        if !(self.policy_anticipation_cut_share >= 0.0 && self.policy_anticipation_cut_share <= 1.0) {
            return Err(format!(
                "policy_anticipation_cut_share is {}. It is the share of policy_anticipation \
                 an expected cut is priced at, in [0, 1]; 0 prices rises only.",
                self.policy_anticipation_cut_share));
        }
        if self.treasury_path_pricing != 0.0 && self.treasury_path_half_life == 0.0 {
            return Err(format!(
                "treasury_path_pricing is {} but treasury_path_half_life is 0. The \
                 market's forecast of the policy path decays at that half-life, so the \
                 pricing needs one above 0.",
                self.treasury_path_pricing));
        }
        if !(self.corporate_spread_vix_cut >= 0.0 && self.corporate_spread_vix_cut <= 1.0) {
            return Err(format!(
                "corporate_spread_vix_cut is {}. It is the share of the VIX slope taken out \
                 of the corporate spread's formula, in [0, 1]; 0 is the slope as it stands.",
                self.corporate_spread_vix_cut));
        }
        if !(self.corporate_spread_equity_gain >= 0.0 && self.corporate_spread_equity_gain <= 10.0) {
            return Err(format!(
                "corporate_spread_equity_gain is {}. It is percentage points of corporate \
                 spread per unit of the index's log fall below its slow average, in [0, 10]; \
                 0 is off.",
                self.corporate_spread_equity_gain));
        }
        if !(self.corporate_spread_equity_half_life >= 0.0
            && self.corporate_spread_equity_half_life <= 1260.0)
        {
            return Err(format!(
                "corporate_spread_equity_half_life is {}. It is a half-life in sessions, \
                 in [0, 1260].",
                self.corporate_spread_equity_half_life));
        }
        if self.corporate_spread_equity_gain != 0.0 && self.corporate_spread_equity_half_life == 0.0 {
            return Err(format!(
                "corporate_spread_equity_gain is {} but corporate_spread_equity_half_life is 0. \
                 The gain reads the index's fall below its own average at that half-life, \
                 so it needs one above 0.",
                self.corporate_spread_equity_gain));
        }
        if !(self.cycle_equity_hazard >= 0.0 && self.cycle_equity_hazard <= 20.0) {
            return Err(format!(
                "cycle_equity_hazard is {}. It is monthly cycle hazard per unit of the index's \
                 log fall below its slow average beyond the knee, in [0, 20]; 0 is off.",
                self.cycle_equity_hazard));
        }
        if !(self.cycle_equity_hazard_knee >= 0.0 && self.cycle_equity_hazard_knee <= 1.0) {
            return Err(format!(
                "cycle_equity_hazard_knee is {}. It is a log fall of the index below its slow \
                 average, in [0, 1].",
                self.cycle_equity_hazard_knee));
        }
        if !(self.cycle_equity_hazard_opening >= 0.0 && self.cycle_equity_hazard_opening <= 1.0) {
            return Err(format!(
                "cycle_equity_hazard_opening is {}. It is monthly cycle hazard added in an \
                 expansion and at a peak where the economy runs without a market, in [0, 1].",
                self.cycle_equity_hazard_opening));
        }
        if !(self.market_prehistory_sessions >= 0.0
            && self.market_prehistory_sessions <= 2520.0
            && self.market_prehistory_sessions.fract() == 0.0)
        {
            return Err(format!(
                "market_prehistory_sessions is {}. It is the number of sessions the market \
                 lives before day zero, a whole number in [0, 2520].",
                self.market_prehistory_sessions));
        }
        if !(self.market_prehistory_valuation == 0.0 || self.market_prehistory_valuation == 1.0) {
            return Err(format!(
                "market_prehistory_valuation is {}. It is a switch: 0 opens the valuation \
                 state as it stood, 1 carries it from the market's prehistory.",
                self.market_prehistory_valuation));
        }
        if self.market_prehistory_valuation != 0.0 && self.market_prehistory_sessions == 0.0 {
            return Err(
                "market_prehistory_valuation is 1 but market_prehistory_sessions is 0. The \
                 valuation state is carried from the market's prehistory, so set the \
                 prehistory's length as well."
                    .to_string());
        }
        if !(self.fed_put_carry >= 0.0 && self.fed_put_carry <= 1.0) {
            return Err(format!(
                "fed_put_carry is {}. It is the share of the Fed put's unanswered fall \
                 carried to the next meeting, in [0, 1]; 0 is none.",
                self.fed_put_carry));
        }
        if !(self.fed_drawdown_hold >= 0.0 && self.fed_drawdown_hold <= 1.0) {
            return Err(format!(
                "fed_drawdown_hold is {}. It is the index's log fall from its highest close \
                 of the last 252 sessions at or above which the bank holds any rise, in \
                 [0, 1]; 0 is off.",
                self.fed_drawdown_hold));
        }
        if self.cycle_equity_hazard != 0.0 && self.corporate_spread_equity_half_life == 0.0 {
            return Err(format!(
                "cycle_equity_hazard is {} but corporate_spread_equity_half_life is 0. \
                 The hazard reads the index's fall below its own average at that half-life, \
                 so it needs one above 0.",
                self.cycle_equity_hazard));
        }
        if self.fed_put_gain != 0.0 && self.fed_put_half_life == 0.0 {
            return Err(format!(
                "fed_put_gain is {} but fed_put_half_life is 0. The put's stock decays \
                 at that half-life and the calm meetings give the cut back as it does, \
                 so a put needs one above 0.",
                self.fed_put_gain));
        }
        if !(self.fair_value_vix_discount >= 0.0 && self.fair_value_vix_discount <= 1.0) {
            return Err(format!(
                "fair_value_vix_discount is {}. It is a log discount per log VIX above the knee, in [0, 1].",
                self.fair_value_vix_discount));
        }
        // The per-name idiosyncratic variance state's shock share and
        // persistence: each at least 0 and their sum under 1, or the state
        // has no mean of one to revert to. Each refusal states the dial's
        // own interval beside the other's value.
        let (a, b) = (self.idio_vol_alpha, self.idio_vol_beta);
        let b_set = if (0.0..1.0).contains(&b) { b } else { 0.0 };
        if !(0.0..1.0).contains(&a) {
            return Err(format!(
                "idio_vol_alpha is {a}. It is the idiosyncratic variance state's shock \
                 share, in [0, {}) beside idio_vol_beta {b}: at least 0, and under 1 \
                 less the persistence, or the state has no mean of one to revert to.",
                1.0 - b_set));
        }
        if !(0.0..1.0).contains(&b) {
            return Err(format!(
                "idio_vol_beta is {b}. It is the idiosyncratic variance state's \
                 persistence, in [0, {}) beside idio_vol_alpha {a}: at least 0, and \
                 under 1 less the shock share, or the state has no mean of one to \
                 revert to.",
                1.0 - a));
        }
        if !(a + b < 1.0) {
            return Err(format!(
                "idio_vol_alpha is {a}, and idio_vol_beta is {b}. Their sum must be \
                 under 1, or the idiosyncratic variance state has no mean of one to \
                 revert to, so idio_vol_alpha is in [0, {}) here and idio_vol_beta in \
                 [0, {}).",
                1.0 - b, 1.0 - a));
        }
        if !(self.idio_vol_jump_bump >= 0.0 && self.idio_vol_jump_bump <= 4.0) {
            return Err(format!(
                "idio_vol_jump_bump is {}. It is the idiosyncratic variance ratio's move \
                 the session after an own jump, in [0, 4]; 0 is none.",
                self.idio_vol_jump_bump));
        }
        if !(self.fair_value_vix_half_life >= 0.0 && self.fair_value_vix_half_life <= 252.0) {
            return Err(format!(
                "fair_value_vix_half_life is {}. It is a half-life in sessions, in [0, 252]; 0 reads the VIX as it stands.",
                self.fair_value_vix_half_life));
        }
        if !(self.fair_value_vix_release_half_life >= 0.0 && self.fair_value_vix_release_half_life <= 2520.0) {
            return Err(format!(
                "fair_value_vix_release_half_life is {}. It is a half-life in sessions, in [0, 2520]; 0 gives the discount back at fair_value_vix_half_life.",
                self.fair_value_vix_release_half_life));
        }
        if !(self.fair_value_relative_knee >= 0.0 && self.fair_value_relative_knee <= 20.0) {
            return Err(format!(
                "fair_value_relative_knee is {}. It is a depth in log units below the roster's mean fair-value level, in [0, 20]; 0 is no knee.",
                self.fair_value_relative_knee));
        }
        if !(self.fair_value_relative_half_life >= 0.0 && self.fair_value_relative_half_life <= 25200.0) {
            return Err(format!(
                "fair_value_relative_half_life is {}. It is a half-life in sessions, in [0, 25200], and above 0 while fair_value_relative_knee is set.",
                self.fair_value_relative_half_life));
        }
        if self.fair_value_relative_knee != 0.0 && self.fair_value_relative_half_life == 0.0 {
            return Err(format!(
                "fair_value_relative_knee is {} but fair_value_relative_half_life is 0. The knee pulls at that half-life, so set it (in sessions, above 0).",
                self.fair_value_relative_knee));
        }
        if !(self.fair_value_vix_knee > 0.0 && self.fair_value_vix_knee <= 200.0) {
            return Err(format!(
                "fair_value_vix_knee is {}. It is a VIX level, in (0, 200].",
                self.fair_value_vix_knee));
        }
        if !(self.fair_value_market_vol_cap >= 0.0 && self.fair_value_market_vol_cap <= 32.0) {
            return Err(format!(
                "fair_value_market_vol_cap is {}. It is a multiple of market_factor_sigma, in [0, 32]; 0 is no ceiling.",
                self.fair_value_market_vol_cap));
        }
        if !(self.fair_value_market_excess_share >= 0.0 && self.fair_value_market_excess_share <= 1.0) {
            return Err(format!(
                "fair_value_market_excess_share is {}. It is the share of what the volatility ceiling takes off the market's permanent share that stays permanent, in [0, 1].",
                self.fair_value_market_excess_share));
        }
        if !(self.fair_value_market_linear >= 0.0 && self.fair_value_market_linear <= 1.0) {
            return Err(format!(
                "fair_value_market_linear is {}. It is a switch, in [0, 1].",
                self.fair_value_market_linear));
        }
        if !(self.market_beta_down_asym_lag_recentre >= 0.0
            && self.market_beta_down_asym_lag_recentre <= 1.0)
        {
            return Err(format!(
                "market_beta_down_asym_lag_recentre is {}. It is the share of the lagged tilt's mean given back, in [0, 1].",
                self.market_beta_down_asym_lag_recentre));
        }
        if !(self.rate_pe_sensitivity >= 0.0 && self.rate_pe_sensitivity <= 10.0) {
            return Err(format!(
                "rate_pe_sensitivity is {}. It is P/E compression per unit of yield, in [0, 10].",
                self.rate_pe_sensitivity));
        }
        if !(self.cycle_publication_lag >= 0.0 && self.cycle_publication_lag <= 2520.0
            && self.cycle_publication_lag.fract() == 0.0)
        {
            return Err(format!(
                "cycle_publication_lag is {}. It is a whole number of sessions, in [0, 2520]; 0 is off.",
                self.cycle_publication_lag));
        }
        if !(self.gdp_publication_lag >= 0.0 && self.gdp_publication_lag <= 2520.0
            && self.gdp_publication_lag.fract() == 0.0)
        {
            return Err(format!(
                "gdp_publication_lag is {}. It is a whole number of sessions after a quarter's \
                 last day, in [0, 2520]; 0 is off (growth reported daily).",
                self.gdp_publication_lag));
        }
        if !(self.unemployment_adjustment_half_life >= 0.0
            && self.unemployment_adjustment_half_life <= 2520.0)
        {
            return Err(format!(
                "unemployment_adjustment_half_life is {}. It is a half-life in sessions, \
                 in [0, 2520]; 0 is off.",
                self.unemployment_adjustment_half_life));
        }
        if !(self.unemployment_natural_pull >= 0.0 && self.unemployment_natural_pull <= 1.0) {
            return Err(format!(
                "unemployment_natural_pull is {}. It is the share of the gap to the natural \
                 rate closed at a monthly release, in [0, 1]; 0 keeps the shipped 0.06.",
                self.unemployment_natural_pull));
        }
        if !(self.unemployment_okun_coefficient >= 0.0 && self.unemployment_okun_coefficient <= 2.4) {
            return Err(format!(
                "unemployment_okun_coefficient is {}. It is points of unemployment a year per \
                 point of growth below 2 per cent, in [0, 2.4]; 0 is the shipped term.",
                self.unemployment_okun_coefficient));
        }
        if !(self.unemployment_natural_rate >= 0.0 && self.unemployment_natural_rate <= 8.0) {
            return Err(format!(
                "unemployment_natural_rate is {}. It is the natural rate with no long-term \
                 unemployment, percent, in [0, 8]; 0 is the shipped 4.0.",
                self.unemployment_natural_rate));
        }
        if !(self.oil_inventory_reversion >= 0.0 && self.oil_inventory_reversion <= 1.0) {
            return Err(format!(
                "oil_inventory_reversion is {}. It is the daily share of inventory's gap to 50 \
                 closed, in [0, 1]; 0 is off.",
                self.oil_inventory_reversion));
        }
        if !(self.oil_inflation_passthrough >= 0.0 && self.oil_inflation_passthrough <= 3.0) {
            return Err(format!(
                "oil_inflation_passthrough is {}. It is the oil pass-through as a multiple of \
                 the shipped 0.01 a dollar, both sides, in [0, 3]; 0 is the shipped branch.",
                self.oil_inflation_passthrough));
        }
        if !(self.index_level_listed == 0.0 || self.index_level_listed == 1.0) {
            return Err(format!(
                "index_level_listed is {}. It is a switch, 0.0 off or 1.0 on.",
                self.index_level_listed));
        }
        if !(self.vix_intraday_live == 0.0 || self.vix_intraday_live == 1.0) {
            return Err(format!(
                "vix_intraday_live is {}. It is a switch, 0.0 off or 1.0 on.",
                self.vix_intraday_live));
        }
        if !(self.forecast_horizon_sessions >= 0.0
            && self.forecast_horizon_sessions <= 252.0
            && self.forecast_horizon_sessions.fract() == 0.0)
        {
            return Err(format!(
                "forecast_horizon_sessions is {}. It is a whole number of sessions in \
                 [0, 252]; 0 computes no forecast.",
                self.forecast_horizon_sessions));
        }
        for (name, value, hi, unit) in [
            ("forecast_vix_dispersion", self.forecast_vix_dispersion, 2.0,
             "the sd of the log VIX about the forecast at a long horizon"),
            ("forecast_vix_dispersion_half_life", self.forecast_vix_dispersion_half_life, 2520.0,
             "a half-life in sessions"),
            ("forecast_policy_shadow_discount", self.forecast_policy_shadow_discount, 1.0,
             "the share of the next meeting's shadow change left out"),
            ("forecast_policy_reversion", self.forecast_policy_reversion, 1.0,
             "the share of the gap to the neutral rate closed a meeting"),
        ] {
            if !(value >= 0.0 && value <= hi) {
                return Err(format!(
                    "{name} is {value}. It is {unit}, in [0, {hi}]; 0 is off."));
            }
        }
        if !(self.forecast_policy_persistence >= 0.0 && self.forecast_policy_persistence < 1.0) {
            return Err(format!(
                "forecast_policy_persistence is {}. It is the share of a meeting's expected \
                 change expected again at the next, in [0, 1); 0 is off.",
                self.forecast_policy_persistence));
        }
        if !(self.forecast_policy_neutral >= -5.0 && self.forecast_policy_neutral <= 20.0) {
            return Err(format!(
                "forecast_policy_neutral is {}. It is the policy rate the forecast reverts \
                 toward, per cent, in [-5, 20].",
                self.forecast_policy_neutral));
        }
        if !(self.fear_greed_published_inputs == 0.0 || self.fear_greed_published_inputs == 1.0) {
            return Err(format!(
                "fear_greed_published_inputs is {}. It is a switch: 0 (the index reads the \
                 true phase and growth) or 1 (it reads them as published).",
                self.fear_greed_published_inputs));
        }
        if !(self.earnings_cycle_sigma >= 0.0 && self.earnings_cycle_sigma <= 0.05) {
            return Err(format!(
                "earnings_cycle_sigma is {}. It is a daily sd, in [0, 0.05].",
                self.earnings_cycle_sigma));
        }
        if !(self.cascade_gain >= 0.0 && self.cascade_gain <= 1.0) {
            return Err(format!(
                "cascade_gain is {}. It scales the squeeze and stop ladders, in [0, 1]; \
                 1.0 as shipped.", self.cascade_gain));
        }
        for (name, v) in [("quote_model_weight", self.quote_model_weight),
                          ("fair_value_news_share", self.fair_value_news_share),
                          ("fair_value_market_share", self.fair_value_market_share)] {
            if !(0.0..=1.0).contains(&v) {
                return Err(format!(
                    "{name} is {v}. It is a weight in [0, 1]; 0.0 as shipped."));
            }
        }
        if !(self.opening_market_sigma >= 0.0 && self.opening_market_sigma <= 0.9) {
            return Err(format!(
                "opening_market_sigma is {}. It is the sd of the market's opening \
                 mispricing, in [0, 0.9]; 0.0 keeps the roster's own premium, as shipped.",
                self.opening_market_sigma));
        }
        if !(self.opening_mispricing_sigma >= 0.0 && self.opening_mispricing_sigma <= 0.9) {
            return Err(format!(
                "opening_mispricing_sigma is {}. It is the sd of the opening mispricing, \
                 in [0, 0.9]; 0.0 adopts the whole day-zero premium, as shipped.",
                self.opening_mispricing_sigma));
        }
        for (name, v) in [("news_absorption_half_life", self.news_absorption_half_life),
                          ("news_absorption_drift_half_life",
                           self.news_absorption_drift_half_life)] {
            if !(0.0..=390.0).contains(&v) {
                return Err(format!(
                    "{name} is {v}. It is a half-life in ticks inside the 390-tick \
                     session, in [0, 390]; 0.0 is the straight-line spread."));
            }
        }
        if !(self.news_absorption_drift_share >= 0.0 && self.news_absorption_drift_share <= 1.0) {
            return Err(format!(
                "news_absorption_drift_share is {}. It is a share of the move, in [0, 1].",
                self.news_absorption_drift_share));
        }
        if self.news_absorption_drift_share != 0.0 && self.news_absorption_half_life == 0.0 {
            return Err(format!(
                "news_absorption_drift_share is {} but news_absorption_half_life is 0: \
                 it splits the fast profile and is read by nothing without it.",
                self.news_absorption_drift_share));
        }
        self.book_invariants()?;
        // Two guards the audit of 0.8.5 found open. A hard cap at or below
        // zero clamps every price to nothing, and a payout share outside
        // [0, 1] pays out more than the earnings or buys shares back with a
        // negative budget. Every preset through pt-v20 carries 50,000 and a
        // share of 0.0, 1/3 or 0.75; pt-v21 carries 1e9 and 0.9.
        if !(self.price_hard_cap.is_finite() && self.price_hard_cap > 0.0) {
            return Err(format!(
                "price_hard_cap is {}. It is the absolute cap on any model price, \
                 in dollars (50,000 shipped), so it is finite and above zero.",
                self.price_hard_cap));
        }
        if !(self.buyback_payout_share >= 0.0 && self.buyback_payout_share <= 1.0) {
            return Err(format!(
                "buyback_payout_share is {}. It is the share of earnings spent on \
                 buybacks, in [0, 1]; 0.0 is none.",
                self.buyback_payout_share));
        }
        if self.news_absorption_drift_half_life != 0.0 && self.news_absorption_drift_share == 0.0 {
            return Err(format!(
                "news_absorption_drift_half_life is {} but news_absorption_drift_share \
                 is 0: it shapes the drift part and is read by nothing without it.",
                self.news_absorption_drift_half_life));
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
#[non_exhaustive]
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
#[non_exhaustive]
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
/// measured on the tape (sd 0.024). It is not a field of
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

/// Bytes as lowercase hex, two digits each: what `format!("{byte:02x}")`
/// per byte wrote, into one string allocated at its final length.
pub(crate) fn lower_hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut hex = String::with_capacity(bytes.len() * 2);
    for &byte in bytes {
        hex.push(char::from(DIGITS[usize::from(byte >> 4)]));
        hex.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
    }
    hex
}

/// Every shipped preset's [`ModelParams::digest`], in
/// [`ModelParams::preset_names`] order, worked out on first use and kept for
/// the process.
///
/// A preset is a `const` and the carried read-only surface its digest
/// includes is made of constants, so the digests cannot change while the
/// process runs. Recomputing them was nineteen digests of the full preset
/// surface on every [`ModelParams::fingerprint`] call, about a millisecond.
fn shipped_digests() -> &'static [(&'static str, String)] {
    static DIGESTS: std::sync::OnceLock<Vec<(&'static str, String)>> =
        std::sync::OnceLock::new();
    DIGESTS.get_or_init(|| {
        ModelParams::preset_names()
            .iter()
            .filter_map(|name| ModelParams::preset(name).map(|p| (*name, p.digest())))
            .collect()
    })
}

/// Switches whose value 0.0 is left out of [`ModelParams::digest`].
///
/// Each one branches on `== 0.0` and takes, at zero, exactly the arithmetic
/// that stood before it was added, so a model with it at zero is the model
/// without it and should hash the same. Without this, adding a switch
/// renames every custom model: a manifest recorded under `custom-XXXXXXXX`
/// rebuilds to a different digest and its replay is refused, and every
/// recorded state hash of a custom model moves. Only a switch whose zero is
/// a branch belongs here; a dial whose zero is arithmetic does not.
pub const DIGEST_SILENT_AT_ZERO: &[&str] = &[
    "book_arrival_shuffle",
    "book_cross_at_limit",
    "book_depth_nesting",
    "buyback_accrual",
    "corporate_spread_cycle",
    "corporate_spread_equity_gain",
    "corporate_spread_equity_half_life",
    "corporate_spread_vix_cut",
    "cycle_equity_hazard",
    "cycle_equity_hazard_knee",
    "cycle_equity_hazard_opening",
    "cycle_nowcast_accuracy",
    "cycle_publication_lag_draw",
    "dividend_buyback_substitution",
    "dividend_payout_share",
    "earnings_anticipation_drift_half_life",
    "earnings_anticipation_drift_share",
    "earnings_cycle_report_share",
    "earnings_followthrough_sigma",
    "earnings_session_sigma",
    "earnings_surprise_df",
    "earnings_surprise_sigma",
    "earnings_volume_multiple",
    "fair_value_market_excess_share",
    "fair_value_relative_half_life",
    "fair_value_relative_knee",
    "fair_value_vix_release_half_life",
    "fed_drawdown_hold",
    "fed_growth_cut",
    "fed_put_carry",
    "fed_put_emergency_vix",
    "fed_put_gain",
    "fed_put_half_life",
    "fed_put_threshold",
    "fed_stress_cut",
    "fed_stress_hold",
    "idio_vol_alpha",
    "idio_vol_beta",
    "idio_vol_jump_bump",
    "impact_memory_coefficient",
    "impact_memory_crossover",
    "impact_memory_half_life",
    "impact_memory_refill",
    "impact_memory_slow_half_life",
    "impact_memory_slow_weight",
    "macro_pins_hold",
    "market_beta_normalise",
    "market_day_tail_df",
    "market_day_tail_state_share",
    "market_prehistory_sessions",
    "market_prehistory_valuation",
    "market_vol_cycle_cap_relative",
    "market_vol_cycle_expansion",
    "market_vol_cycle_half_life",
    "market_vol_cycle_pin_neutral",
    "market_vol_cycle_pin_phase",
    "market_vol_cycle_ratio",
    "market_vol_cycle_recovery_release",
    "market_vol_cycle_recovery_scale",
    "market_vol_cycle_relative",
    "market_vol_cycle_relative_calm",
    "market_vol_cycle_release_half_life",
    "market_vol_cycle_trough_release",
    "market_vol_leverage",
    "market_vol_leverage_down",
    "market_vol_leverage_half_life",
    "market_vol_leverage_standardise",
    "market_vol_slow_gamma",
    "order_flow_depth_law",
    "overnight_idio_df",
    "overnight_idio_share",
    "overnight_market_share",
    "pinned_vix_calm_knee",
    "pinned_vix_calm_share",
    "pinned_vix_feedback",
    "pinned_vix_priced_cap",
    "pinned_vix_variance_share",
    "policy_anticipation",
    "policy_anticipation_cut_share",
    "rate_close_remark",
    "rate_intraday_live",
    "treasury_haven_gain",
    "treasury_path_half_life",
    "treasury_path_pricing",
    "treasury_policy_damping",
    "treasury_put_pricing",
    "vix_stress_premium",
    "vix_stress_premium_cap",
    "vix_stress_premium_knee",
    "vix_fear_uptake",
    "vix_fear_half_life",
    "unemployment_natural_pull",
    "unemployment_okun_coefficient",
    "unemployment_natural_rate",
    "oil_inventory_reversion",
    "oil_inflation_passthrough",
    "index_level_listed",
    "vix_intraday_live",
    "forecast_horizon_sessions",
    "forecast_vix_dispersion",
    "forecast_vix_dispersion_half_life",
    "forecast_policy_shadow_discount",
    "forecast_policy_persistence",
    "forecast_policy_reversion",
];

#[cfg(test)]
thread_local! {
    /// How many times [`ModelParams::digest`] has run on this thread. Tests
    /// read it to hold the fingerprint's callers to working it out once.
    static DIGESTS_TAKEN: std::cell::Cell<u64> = const { std::cell::Cell::new(0) };
}

/// [`ModelParams::digest`] calls made so far on this thread. Test-only.
#[cfg(test)]
pub(crate) fn digests_taken() -> u64 {
    DIGESTS_TAKEN.with(|n| n.get())
}

/// Dials left out of [`ModelParams::digest`] while they hold these values,
/// their defaults, which are not 0.0.
///
/// Each is read only while a switch in [`DIGEST_SILENT_AT_ZERO`] is set
/// (`fed_stress_cut`, `dividend_payout_share` or
/// `forecast_policy_reversion`), so at its default with
/// that switch off it is the model that existed before it, and with the
/// switch on the default is the value the switch was built and measured
/// with. Off its default each enters the digest as every other dial does.
pub const DIGEST_SILENT_AT_DEFAULT: &[(&str, f64)] = &[
    ("fed_stress_vix", 30.0),
    ("fed_stress_inflation_gap", 1.0),
    ("dividend_growth_cutoff", 0.30),
    ("dividend_adjustment_speed", 0.4),
    ("dividend_yield_ceiling", 2.0),
    ("forecast_policy_neutral", 2.5),
];

/// The settable names, sorted. A function rather than the const above so
/// the list is derived from `to_pairs`' actual coverage in tests.
pub fn settable_names() -> Vec<&'static str> {
    vec![
        "cascade_symmetry",
        "cascade_gain",
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
        "market_beta_normalise",
        "market_vol_alpha",
        "market_vol_beta",
        "market_vol_gamma",
        "market_vol_slow_gamma",
        "market_vol_leverage",
        "market_vol_leverage_half_life",
        "market_vol_leverage_down",
        "market_vol_leverage_standardise",
        "market_day_tail_df",
        "market_day_tail_state_share",
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
        "macro_calendar_days_per_year",
        "cycle_us_calibration",
        "fed_liftoff_rule",
        "market_pe_buybacks",
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
        "market_vol_vix_exponent_below",
        "market_beta_down_asym",
        "market_beta_down_asym_lag",
        "market_beta_down_asym_lag_live",
        "market_beta_down_asym_recentre",
        "market_beta_down_asym_lag_recentre",
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
        "news_absorption_half_life",
        "news_absorption_drift_share",
        "news_absorption_drift_half_life",
        "news_quote_revision",
        "quote_model_weight",
        "closing_auction",
        "earnings_cycle_depth",
        "earnings_cycle_upside",
        "earnings_cycle_half_life",
        "earnings_cycle_sigma",
        "earnings_anticipation_half_life",
        "rate_pe_sensitivity",
        "cycle_publication_lag",
        "cycle_publication_lag_draw",
        "gdp_publication_lag",
        "unemployment_adjustment_half_life",
        "unemployment_natural_pull",
        "unemployment_okun_coefficient",
        "unemployment_natural_rate",
        "oil_inventory_reversion",
        "oil_inflation_passthrough",
        "index_level_listed",
        "vix_intraday_live",
        "forecast_horizon_sessions",
        "forecast_vix_dispersion",
        "forecast_vix_dispersion_half_life",
        "forecast_policy_shadow_discount",
        "forecast_policy_persistence",
        "forecast_policy_reversion",
        "forecast_policy_neutral",
        "fear_greed_published_inputs",
        "macro_publication_repricing",
        "treasury_10y_noise",
        "treasury_2y_noise",
        "flight_to_quality_gain",
        "flight_to_quality_day",
        "corporate_yield_daily",
        "cycle_nowcast_accuracy",
        "corporate_spread_cycle",
        "earnings_anticipation_drift_share",
        "earnings_anticipation_drift_half_life",
        "fed_growth_cut",
        "macro_pins_hold",
        "fair_value_news_share",
        "fair_value_market_share",
        "fair_value_market_linear",
        "fair_value_market_vol_cap",
        "fair_value_market_excess_share",
        "fair_value_vix_discount",
        "fair_value_vix_knee",
        "fair_value_vix_half_life",
        "fair_value_vix_release_half_life",
        "fair_value_relative_knee",
        "fair_value_relative_half_life",
        "pinned_vix_feedback",
        "pinned_vix_variance_share",
        "pinned_vix_calm_knee",
        "pinned_vix_calm_share",
        "pinned_vix_priced_cap",
        "buyback_yield_cap",
        "buyback_accrual",
        "rate_close_remark",
        "rate_intraday_live",
        "fed_stress_cut",
        "fed_stress_vix",
        "fed_stress_inflation_gap",
        "dividend_payout_share",
        "dividend_growth_cutoff",
        "dividend_adjustment_speed",
        "dividend_yield_ceiling",
        "dividend_buyback_substitution",
        "overnight_market_share",
        "overnight_idio_share",
        "overnight_idio_df",
        "earnings_surprise_sigma",
        "earnings_surprise_df",
        "earnings_session_sigma",
        "earnings_followthrough_sigma",
        "earnings_volume_multiple",
        "earnings_cycle_report_share",
        "market_vol_cycle_ratio",
        "market_vol_cycle_expansion",
        "market_vol_cycle_half_life",
        "market_vol_cycle_relative",
        "market_vol_cycle_relative_calm",
        "market_vol_cycle_cap_relative",
        "market_vol_cycle_pin_neutral",
        "market_vol_cycle_pin_phase",
        "market_vol_cycle_trough_release",
        "market_vol_cycle_release_half_life",
        "market_vol_cycle_recovery_release",
        "market_vol_cycle_recovery_scale",
        "vix_stress_premium",
        "vix_stress_premium_knee",
        "vix_stress_premium_cap",
        "vix_fear_uptake",
        "vix_fear_half_life",
        "fed_put_gain",
        "fed_put_threshold",
        "fed_put_half_life",
        "fed_put_emergency_vix",
        "treasury_put_pricing",
        "treasury_haven_gain",
        "fed_stress_hold",
        "treasury_path_pricing",
        "treasury_path_half_life",
        "treasury_policy_damping",
        "policy_anticipation",
        "policy_anticipation_cut_share",
        "corporate_spread_vix_cut",
        "corporate_spread_equity_gain",
        "corporate_spread_equity_half_life",
        "cycle_equity_hazard",
        "cycle_equity_hazard_knee",
        "cycle_equity_hazard_opening",
        "market_prehistory_sessions",
        "market_prehistory_valuation",
        "fed_put_carry",
        "fed_drawdown_hold",
        "opening_mispricing_sigma",
        "opening_market_sigma",
        "book_depth_coefficient",
        "book_depth_exponent",
        "book_depth_reach",
        "book_depth_nesting",
        "book_shared",
        "book_refill_half_life",
        "book_resting",
        "book_arrival_shuffle",
        "book_cross_at_limit",
        "fill_impact_coefficient",
        "impact_memory_coefficient",
        "impact_memory_half_life",
        "impact_memory_slow_half_life",
        "impact_memory_slow_weight",
        "impact_memory_crossover",
        "impact_memory_refill",
        "news_peer_vix_coupling",
        "news_peer_weight",
        "news_peer_weight_down",
        "news_sector_weight",
        "order_flow_coefficient",
        "order_flow_impact_law",
        "order_flow_depth_law",
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
        "idio_vol_alpha",
        "idio_vol_beta",
        "idio_vol_jump_bump",
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
        // pt-v20 took the default at 0.8.5, on the same deliberate move.
        assert_eq!(crate::params::PT_V20.fingerprint(), "pt-v20");
        // pt-v21 took it at 0.10.0. Before it did, the line below asserted
        // `DEFAULT_PRESET_NAME == "pt-v20"`.
        assert_eq!(crate::params::PT_V21.fingerprint(), "pt-v21");
        assert_eq!(DEFAULT_PRESET_NAME, "pt-v21");
        // pt-v21 is the graded vector: its digest is the one the vector had
        // as overrides on pt-v20 while it was graded.
        assert_eq!(&crate::params::PT_V21.digest()[..8], PT_V21_DIGEST_PREFIX);
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
    fn lower_hex_writes_what_format_wrote() {
        let every: Vec<u8> = (0..=255u8).collect();
        let by_format: String = every.iter().map(|b| format!("{b:02x}")).collect();
        assert_eq!(lower_hex(&every), by_format);
        assert_eq!(lower_hex(&[]), "");
    }

    #[test]
    fn a_switch_silent_at_zero_is_left_out_of_the_digest_only_at_zero() {
        // The digest a build without the switch computed: every pair but
        // the silent ones, in the same canonical form.
        fn without_silent(p: &ModelParams) -> String {
            let mut hasher = Sha256::new();
            for (name, value) in p.to_pairs() {
                if DIGEST_SILENT_AT_ZERO.contains(&name.as_str())
                    || DIGEST_SILENT_AT_DEFAULT.iter().any(|(n, _)| *n == name.as_str())
                {
                    continue;
                }
                hasher.update(name.as_bytes());
                hasher.update(b"=");
                hasher.update(value.to_bits().to_be_bytes());
                hasher.update(b"\n");
            }
            lower_hex(&hasher.finalize())
        }
        let custom = PT_V19.with_override("book_resting", 1.0).unwrap();
        for p in [PT_V1, PT_V19, PT_V20, custom] {
            assert_eq!(p.digest(), without_silent(&p));
            for name in DIGEST_SILENT_AT_ZERO {
                // The surface still carries the name; only the hash skips it.
                assert!(p.to_pairs().iter().any(|(n, _)| n == name), "{name}");
                assert_eq!(p.get(name), Some(0.0), "{name}");
                let negative = p.with_override(name, -0.0).unwrap();
                assert_eq!(negative.digest(), p.digest(), "{name} at -0.0");
                let on = p.with_override(name, 1.0).unwrap();
                assert_ne!(on.digest(), p.digest(), "{name} at 1.0");
                assert!(on.fingerprint().starts_with("custom-"), "{name}");
            }
        }
        // And every name listed is a settable dial.
        for name in DIGEST_SILENT_AT_ZERO {
            assert!(settable_names().contains(name), "{name}");
        }
    }

    #[test]
    fn a_dial_silent_at_its_default_is_left_out_of_the_digest_only_there() {
        let custom = PT_V19.with_override("book_resting", 1.0).unwrap();
        for p in [PT_V1, PT_V19, PT_V20, custom] {
            for (name, default) in DIGEST_SILENT_AT_DEFAULT {
                // Every shipped preset carries the default, and the surface
                // still carries the name.
                assert_eq!(p.get(name).map(f64::to_bits), Some(default.to_bits()), "{name}");
                let moved = p.with_override(name, default + 1.0).unwrap();
                assert_ne!(moved.digest(), p.digest(), "{name} off its default");
                assert!(moved.fingerprint().starts_with("custom-"), "{name}");
                let back = moved.with_override(name, *default).unwrap();
                assert_eq!(back.digest(), p.digest(), "{name} back at its default");
            }
        }
        for (name, default) in DIGEST_SILENT_AT_DEFAULT {
            assert!(settable_names().contains(name), "{name}");
            assert!(*default != 0.0, "{name}: a dial silent at 0.0 belongs in DIGEST_SILENT_AT_ZERO");
            assert!(!DIGEST_SILENT_AT_ZERO.contains(name), "{name} is listed twice");
        }
    }

    #[test]
    fn the_kept_preset_digests_are_the_live_ones() {
        // The table `fingerprint` compares against is worked out once. It
        // must hold every shipped preset, in `preset_names` order, at the
        // digest the preset has now.
        let kept = shipped_digests();
        assert_eq!(kept.len(), ModelParams::preset_names().len());
        for ((name, digest), listed) in kept.iter().zip(ModelParams::preset_names()) {
            assert_eq!(name, listed);
            assert_eq!(*digest, ModelParams::preset(name).unwrap().digest(), "{name}");
        }
    }

    #[test]
    fn a_fingerprint_takes_one_digest_once_the_presets_are_kept() {
        // Before the presets' digests were kept, every fingerprint took
        // twenty: its own and one per shipped preset. The engine's state
        // hash folds the fingerprint in, and the sandbox hashes the state
        // around every call into agent code.
        let _ = PT_V20.fingerprint();
        let before = digests_taken();
        assert_eq!(PT_V19.fingerprint(), "pt-v19");
        let custom = PT_V1.with_override("garch_alpha", 0.12).unwrap();
        assert!(custom.fingerprint().starts_with("custom-"));
        assert_eq!(digests_taken() - before, 2);
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
        if s.len() >= i + word.len() && s.as_bytes()[i..][..word.len()].eq_ignore_ascii_case(word.as_bytes()) {
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
                            .is_none_or(|c| !c.is_ascii_alphanumeric())
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
                if (after_trim.starts_with("does")
                    || after_trim.starts_with("do ")
                    || after_trim.starts_with("did"))
                    && s[..i].trim_end().ends_with("what")
                {
                    let opener = head.strip_prefix("At ").unwrap_or("");
                    let opener = opener.strip_prefix('`').unwrap_or(opener);
                    if let Some((v, t)) = number_at(opener, 0) {
                        push(v, t, presets.clone(), &format!("At {t} ... what {}", &s[i..end]));
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
