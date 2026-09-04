//! `simulateMarketTick`'s live path, ported from the reference
//! implementation.
//!
//! # The order of operations IS the model
//!
//! Four phases, and the boundaries between them matter:
//!
//! 1. **Factors** — per company, compute the four live shock factors. Takes
//!    one normal and one uniform per company. The uniform is *stashed*, not
//!    used: it is consumed in phase 3.
//! 2. **Price** — the `s`-process, then `price = fairValue × exp(s)`, then the
//!    circuit breaker on the MODEL price.
//! 3. **Volume** — consumes the uniforms stashed in phase 1.
//! 4. **Settlement** — the book decides what actually printed, then the
//!    breaker is applied again to the PRINT.
//!
//! The phase-1 uniform being consumed two phases later is the trap here. A
//! port that drew it in phase 3, where it is used, would produce identical
//! counts and a completely different stream — the same class of mistake as
//! WP3's object-literal draw.
//!
//! # The factor model moves fair value OR price, never both
//!
//! `new_prices[i]` after phase 2 is **fair value**, not a price. Phase 4 reads
//! it only as the book's anchor. Treating it as the printed price would
//! double-count every driver, which is why the variable is never assigned to
//! `stock.price` directly.
//!
//! # The breaker is applied twice, deliberately
//!
//! Once to the model price in phase 2, once to the settled print in phase 4.
//! The second is the one that matters: spread widening and multi-level
//! book-walking can settle a print beyond an already-clamped fair value, and
//! because that print becomes the next tick's reference the overshoot
//! compounded within a day. A breaker that bounds an unobservable reference
//! and not the tape is not a breaker. See D6 in the port notes.

use crate::economy::EconomyState;
use crate::fair_value::{compute_fair_value_with, CompanyValuationInputs, EconomyValuationInputs};
use crate::mathx;
use crate::microstructure::{
    decompose, settle_price_through_book, CompanyMicrostructure, SettleOptions,
};
use crate::mispricing::crowd_lean_with;
use crate::params::ModelParams;
use crate::rng::Rng;

use super::factors::{
    calculate_live_factors, order_imbalance, FactorCompany, LiveFactors, NewsEvent,
    SharedFactors,
};
use super::hours::{intraday_vol, intraday_volume, MarketStatus};

/// Per-tick decay of the mispricing process: `MISPRICING_PHI ^ (1/390)`.
///
/// **Hardcoded from V8's recorded bits, deliberately not computed** — the same
/// treatment as `MISPRICING_PHI` and for a sharper reason: this one is applied
/// 390 times per simulated day, so a last-bit difference is not a rounding
/// curiosity but a different half-life compounding all session.
///
/// Measured: `libm::pow`, `f64::powf` and V8 all produce `3FEFFFC1E1385E9E`
/// for this input, so the hardcode is belt-and-braces rather than a
/// workaround. It stays hardcoded because agreeing today on one platform is
/// not a guarantee, and the bits are the contract.
pub const S_PHI_TICK: f64 = f64::from_bits(0x3FEF_FFC1_E138_5E9E);

/// BASELINE standard deviation of the shared market factor, at DAILY
/// scale — since the factor-variance change of the 2026-08 era, the
/// unconditional anchor of the factor's variance process
/// ([`super::factor_vol`]), not the sigma of any given day. The tick
/// draws at the CONDITIONAL sigma the engine passes in
/// ([`TickInputs::market_sigma_daily`]); this constant is the anchor the
/// process reverts to — scaled by (VIX/15)² since the coupling decision,
/// so it is the exact reversion level when VIX sits at its endogenous
/// mean — the unit its floor and ceiling are multiples of, and the
/// absolute denomination of the crash amplifier's threshold
/// (`factors.rs`).
///
/// **History, in three measured acts.** The reference implementation says
/// 0.003, which put a few percent of a typical name's variance in the
/// shared factor and produced cross-sectional correlation of 0.026
/// against a real 0.25-0.35 (findings 7-9). The first recalibration swept
/// the constant (`tools/calibration/sweep_market_factor_sigma.py`) and
/// proved the band UNREACHABLE by it: the factor was Gaussian at constant
/// sigma, so its correlation contribution was its variance share, and
/// every point of share Gaussian-diluted the GARCH tails — the band
/// arrived only at sigma 0.021, where excess kurtosis had collapsed to
/// 1.26 against a floor of 3 (finding 14). 0.0075 shipped as the largest
/// value whose median kurtosis stayed in band, and finding 14 named the
/// escape: give the factor its own fat-tailed conditional volatility,
/// funded from the idiosyncratic side. `factor_vol.rs` is that change,
/// and it alters what this constant buys — a conditional factor
/// CONTRIBUTES kurtosis and market-wide clustering instead of spending
/// them, so the baseline can sit where correlation needs it.
///
/// **0.016 comes from the factor-vol sweep**
/// (`tools/calibration/sweep_market_factor_vol.py`, committed results
/// `results/market-factor-vol-2026-08-2*.json`), chosen JOINTLY with
/// `IDIO_SIGMA_SCALE` = 0.84 — the funding side, which is why total
/// volatility FELL (48.3% -> 41.8% pooled) while the factor's share of it
/// roughly tripled. At the shipped vector, six-seed medians on the
/// published method: correlation 0.260 and excess kurtosis 3.14, both
/// inside their real bands for the first time in this model's history,
/// with volatility clustering 0.245 (in band) and the leverage effect
/// intact at -0.094. The finding-14 trade was dissolved by the process,
/// not re-positioned along the same curve.
///
/// Change this only by re-running the factor-vol sweep, and only TOGETHER
/// with `IDIO_SIGMA_SCALE` and the `factor_vol.rs` shape; a test pins the
/// value to make that deliberate.
pub const MARKET_FACTOR_SIGMA: f64 = 0.016;
/// Standard deviation of a shared sector factor, at DAILY scale.
pub const SECTOR_FACTOR_SIGMA: f64 = 0.002;

/// VIX points past `CRISIS_VIX_THRESHOLD` over which the sector→market
/// blend ramps to 1.0 (before [`CRISIS_BLEND_CAP`] truncates it).
///
/// A §5.4 promotion of the inline `/1.4`: the ramp is half of what makes
/// the re-sited crisis trigger MEAN something — the blend saturates by
/// VIX ≈ 26.6, the ceiling of what the macro chain can produce.
pub const CRISIS_BLEND_RAMP: f64 = 1.4;

/// Ceiling of the crisis correlation blend. A §5.4 promotion of the
/// inline `0.8`: even in a full crisis, sector factors keep 20% of their
/// own identity.
pub const CRISIS_BLEND_CAP: f64 = 0.8;

/// The session circuit breaker, as a fraction of the day's open (±25%).
///
/// A §5.4 promotion of the inline `1.25`/`0.75` pair. A GUARD, not a
/// knob: it is a worst-case guarantee backed by invariant tests, and it
/// is applied twice per tick — to the model price and to the settled
/// print — which is why the params carry it as the derived band
/// multipliers, computed once (§5.3).
pub const PRICE_BREAKER_FRACTION: f64 = 0.25;

/// Absolute cap on any model price. A guard (the reference's `50000`).
pub const PRICE_HARD_CAP: f64 = 50_000.0;

/// The mutable stock state a tick reads and writes.
#[derive(Debug, Clone, PartialEq)]
pub struct TickStock {
    pub price: f64,
    pub previous_close: f64,
    /// `undefined` until the first tick prints — the reference implementation's field is
    /// optional, and a fabricated default here would diverge on tick zero of
    /// any freshly generated company.
    pub previous_tick_price: Option<f64>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub volume: f64,
    pub avg_volume: f64,
    pub shares_outstanding: f64,
    pub market_cap: f64,
    /// `None` until the first tick adopts the current premium/discount.
    pub mispricing_s: Option<f64>,
    pub mispricing_s_prev_close: Option<f64>,
    pub mispricing_momentum: Option<f64>,
    pub maker_inventory: Option<f64>,
    pub garch_variance: f64,
    /// The variance cascade's components, when one is running.
    ///
    /// A fixed array rather than a `Vec`: this is per-name state touched at
    /// every close, and a heap allocation per company per day would be a real
    /// cost for a mechanism that ships switched off. All zeros means the
    /// cascade has never run, and `update_garch_cascade` seeds from the
    /// sector base on its first call. See
    /// [`crate::market::garch::update_garch_cascade`].
    pub garch_cascade: [f64; crate::market::garch::CASCADE_MAX],
    pub last_daily_return: Option<f64>,
    pub beta: Option<f64>,
    pub short_interest: f64,
    pub float: f64,
}

/// One company, as the tick sees it.
///
/// A single struct rather than three composed ones. `fair_value`,
/// `factors` and `microstructure` each declare their own narrow input type by
/// convention, and duplicating `id`/`sector`/`beta` across three copies would
/// invite them to disagree. The narrow views are BUILT from this on demand,
/// so there is one source of truth per field.
#[derive(Debug, Clone, PartialEq)]
pub struct TickCompany {
    pub id: String,
    pub ticker: String,
    pub sector: String,
    pub is_bankrupt: bool,
    pub is_public: bool,
    pub stock: TickStock,
    /// `SECTOR_CONFIGS[sector].volatility`, passed in rather than duplicated.
    pub sector_volatility: Option<f64>,
    /// `SECTOR_CONFIGS[sector].avgPe`, for the valuation. Note the casing —
    /// reading `avgPE` yields `undefined` and silently falls back to the
    /// default PE of 18, which is a real bug this port already shipped once.
    pub sector_avg_pe: Option<f64>,
    pub eps: Option<f64>,
    pub book_value_per_share: Option<f64>,
    pub revenue_growth: Option<f64>,
}

/// The factor the valuation's fundamentals are restated by, from the
/// economy's own integrated nominal output.
///
/// `1.0` whenever `earnings_nominal_growth` is 0.0, through a BRANCH rather
/// than through arithmetic that happens to be neutral, so every preset
/// before pt-v18 reaches `compute_fair_value` with the fundamentals it was
/// built with and reproduces bit for bit.
///
/// # The quantity
///
/// `N = gdp * cpi`, nominal output, which the economy compounds daily at
/// `gdp_growth / 100 / 365` and `inflation_rate / 100 / 365` in
/// [`crate::economy::daily`]. `base` is `N` when the engine was built, so
/// the ratio is exactly 1.0 on day 0 and the opening valuation is
/// unchanged. The economy advances once per market day and the market
/// annualises by 252, so a certified year carries `252 / 365` of both
/// annual rates.
///
/// # The guard
///
/// A base that is not finite and positive gives 1.0. `gdp` and `cpi` are
/// levels the economy never takes to zero, so this reaches nothing the
/// engine builds; it is here because a caller may construct
/// [`TickInputs`] directly, and a NaN entering the valuation would freeze
/// every book downstream with no indication of where it came from.
/// Trading days in a year, the clock a market rate is quoted on.
///
/// Separate from the economy's 365 by intent. A buyback yield is earnings
/// over a TRADED price, so it is an annual rate on the market's own
/// calendar, and an `economy_days_per_year` that unified the macro clocks
/// would leave this alone.
pub const MARKET_DAYS_PER_YEAR: f64 = 252.0;

/// The per-share factor a buyback yield has produced by `elapsed_days`.
///
/// A company returning a share of its earnings as net buybacks retires
/// stock, so earnings and book per share grow faster than the company does,
/// by the buyback yield `b = payout_share * eps / price`. That yield is
/// earnings over price, so the mechanism pays more when the multiple is low
/// and less when it is high, which is the behaviour a real buyback
/// programme has and a flat premium does not.
///
/// # What is exact and what is not
///
/// The share count obeys `dS/S = -payout * eps(u)/P(u) du`, so the exact
/// factor is the exponential of the payout share times the INTEGRAL of the
/// earnings yield. This evaluates the yield at today's price and holds it
/// over the elapsed time, which is the form the design states and needs no
/// per-name accumulator. Measured against the integral on real price paths
/// at pt-v18: over 252 days on 200 name-seeds the difference is +0.026
/// points of annual index return at the median and +0.009 at the mean, and
/// over 1008 days on 120 name-seeds it is -0.015 and -0.068. The multiple
/// mean-reverts, so today's yield estimates the period's average yield at
/// both horizons. The worst single name is 1.39 points over four years and
/// the index averages forty of them with errors on both sides.
///
/// The yield reads the earnings the growth term has already restated, and
/// not the earnings this factor itself restates. Solving for the
/// self-consistent yield is a Lambert W with no bit-pinned implementation
/// here, and the term it omits is second order: at 1.85 per cent over a
/// year it understates the yield by 0.03 of a point, against a market
/// price in the denominator that rises with the same buybacks and pushes
/// the other way.
///
/// # The clamp on a loss-maker
///
/// A negative earnings yield would grow the share count, which is dilution
/// rather than buyback, and a company cannot return earnings it does not
/// have. So a loss-maker neither retires nor issues. That clamp does not
/// carry the hazard this era keeps finding in one-sided terms: `eps` is
/// fixed for a name at construction, so it selects a fixed share of the
/// roster once rather than branching on a zero-mean quantity every day. On
/// `Universe.random(40, seed=111)` it is 3 names of 40.
pub fn buyback_scale(p: &ModelParams, eps: Option<f64>, price: f64, elapsed_days: i64) -> f64 {
    if p.buyback_payout_share == 0.0 {
        return 1.0;
    }
    let eps = match eps {
        Some(e) if e > 0.0 && e.is_finite() => e,
        _ => return 1.0,
    };
    if !(price > 0.0) || !price.is_finite() || elapsed_days <= 0 {
        return 1.0;
    }
    let b = p.buyback_payout_share * eps / price;
    mathx::exp(b * elapsed_days as f64 / MARKET_DAYS_PER_YEAR)
}

pub fn nominal_scale(p: &ModelParams, economy: &EconomyState, base: f64) -> f64 {
    if p.earnings_nominal_growth == 0.0 {
        return 1.0;
    }
    if !(base > 0.0) || !base.is_finite() {
        return 1.0;
    }
    let ratio = economy.gdp * economy.cpi / base;
    // Linear in the dial, so a share of the term is a share of the level
    // the term adds, which is the shape every other dial in this era has.
    1.0 + p.earnings_nominal_growth * (ratio - 1.0)
}

/// Valuation inputs with the fundamentals restated in the price level and
/// output the economy has reached.
///
/// `scale` is `1 + earnings_nominal_growth * (N_t / N_0 - 1)`, from
/// [`nominal_scale`]. Both fundamentals move together, so the valuation
/// stays homogeneous of degree one in nominal terms on the earnings path
/// and on the book path alike, and a loss-maker valued at
/// `book * LOSS_MAKING_PRICE_TO_BOOK` grows with the same output a
/// profitable company does. The sector anchor is a multiple and the
/// revenue growth is a rate, so both are already nominal-neutral and
/// neither moves.
pub fn scale_valuation(
    company: CompanyValuationInputs,
    scale: f64,
) -> CompanyValuationInputs {
    CompanyValuationInputs {
        sector_avg_pe: company.sector_avg_pe,
        eps: company.eps.map(|v| v * scale),
        book_value_per_share: company.book_value_per_share.map(|v| v * scale),
        revenue_growth: company.revenue_growth,
    }
}

impl TickCompany {
    fn valuation(&self) -> CompanyValuationInputs {
        CompanyValuationInputs {
            sector_avg_pe: self.sector_avg_pe,
            eps: self.eps,
            book_value_per_share: self.book_value_per_share,
            revenue_growth: self.revenue_growth,
        }
    }

    fn factor_view(&self) -> FactorCompany {
        FactorCompany {
            id: self.id.clone(),
            sector: self.sector.clone(),
            beta: self.stock.beta,
            market_cap: self.stock.market_cap,
            avg_volume: self.stock.avg_volume,
            shares_outstanding: self.stock.shares_outstanding,
            short_interest: self.stock.short_interest,
            float: self.stock.float,
            garch_variance: self.stock.garch_variance,
            last_daily_return: self.stock.last_daily_return,
        }
    }

    /// The microstructure view of this company at a given price.
    ///
    /// Public so an embedder can build the executable book for an instrument
    /// and trade against it. The book a caller sees is therefore the same one
    /// the tick settles through -- displayed depth IS executable depth, which
    /// is the property that makes slippage emergent rather than modelled.
    pub fn micro_view(&self, price: f64) -> CompanyMicrostructure {
        CompanyMicrostructure {
            id: self.id.clone(),
            sector_volatility: self.sector_volatility,
            price,
            market_cap: self.stock.market_cap,
            beta: self.stock.beta,
            float: Some(self.stock.float),
            short_interest: Some(self.stock.short_interest),
            avg_volume: Some(self.stock.avg_volume),
            volume: Some(self.stock.volume),
            shares_outstanding: Some(self.stock.shares_outstanding),
            maker_inventory: self.stock.maker_inventory,
        }
    }
}

/// Pending order volume for one ticker.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct OrderVolume {
    pub buy: f64,
    pub sell: f64,
}

/// A live news-impact entry, for the volume amplifier.
#[derive(Debug, Clone, PartialEq)]
pub struct NewsImpactEntry {
    pub company_id: Option<String>,
    pub sector: Option<String>,
    pub sectors: Vec<String>,
    pub remaining_impact: f64,
    pub reversal_phase: bool,
}

/// How settlement consumes its four uniforms — the 2026-08 era's fixed
/// schedule, or the reference's conditional one.
///
/// `settle_price_through_book` takes four draws or zero, and WHICH of the
/// two depends on price-and-state guards: a volume that floors to zero, an
/// empty book. That made the draw schedule a function of the trajectory —
/// any perturbation that flipped one settle guard (a trade's impact, a
/// macro path moving a price) shifted every subsequent draw for every
/// consumer, which is exactly the coupling the counterfactual surfaces
/// (`tca`, `scenario.compare`) exist to avoid. It was measured at zero on
/// current builds and observed at -4 draws on an older one: real, rare,
/// and impossible to rule out while the consumption is conditional.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SettleDrawPolicy {
    /// Four uniforms are drawn per active company on every open tick,
    /// whether or not the settle uses them. The draw schedule becomes a
    /// pure function of (market status, active set, sector count) — no
    /// price, no macro value, no order flow can move it. This is the
    /// era's generated schedule and what `Engine::tick` uses.
    FourAlways,
    /// Four or zero, exactly as the guards decide — the reference's
    /// schedule. For replaying a RECORDED stream, where the tape holds
    /// precisely the draws the reference consumed and drawing four
    /// unconditionally would misalign it.
    FourOrZero,
}

/// Everything the tick needs that is not a company.
#[derive(Debug, Clone)]
pub struct TickInputs<'a> {
    pub economy: &'a EconomyState,
    pub market_status: MarketStatus,
    /// Fraction of the session elapsed, from `hours::intraday_fraction`.
    pub intraday_t: f64,
    pub volatility_multiplier: f64,
    pub news: &'a [NewsEvent],
    pub news_impact_queue: &'a [NewsImpactEntry],
    /// Keyed by ticker.
    pub order_volumes: &'a [(String, OrderVolume)],
    /// Sector keys in the order `SECTOR_CONFIGS` enumerates them. The ORDER
    /// is contractual: one normal is drawn per key, in this order.
    pub sector_keys: &'a [String],
    /// Whether yesterday's session accumulated a DOWN market factor.
    /// Read only by the lagged transmission wire
    /// (`market_beta_down_asym_lag`); false everywhere that dial is 0.0,
    /// including every recorded reference stream.
    pub prev_day_down: bool,
    /// Fraction of the forced-flow segment's budget remaining, 1.0 when
    /// the reservoir dial is off. See `ModelParams::forced_flow_reservoir`.
    pub forced_flow_eff: f64,
    /// Today's market-factor sigma at DAILY scale — the conditional level
    /// of the factor's variance process ([`super::factor_vol`]), fixed for
    /// the whole session at the previous close's update.
    ///
    /// The engine passes its `MarketVarianceState`'s current sigma on the
    /// generated schedule, and the constant [`MARKET_FACTOR_SIGMA`] when
    /// replaying a recorded reference stream, whose era drew the factor at
    /// constant sigma. A caller building `TickInputs` directly and wanting
    /// the old constant-sigma behaviour passes the constant.
    pub market_sigma_daily: f64,
    /// The universe's remembered stress, in VIX points above the crisis
    /// threshold, carried from previous days. Zero under every preset
    /// before pt-v4 and under any preset with the memory disabled, which
    /// is what makes the correlation blend below reproduce exactly.
    pub universe_stress: f64,
    /// Shared log-scale volume multiplier state, from the engine's daily
    /// AR(1). 0.0 means a multiplier of exactly 1.0.
    pub volume_state: f64,
    /// Per-NAME volume state, indexed by company (§107). Empty means the
    /// mechanism is off, which every preset before it is.
    pub volume_idio: &'a [f64],
    /// See [`SettleDrawPolicy`]. `FourAlways` unless replaying a recorded
    /// reference stream.
    pub settle_draws: SettleDrawPolicy,
    /// Settle each open tick a second time against unbounded depth, and
    /// report what the difference was.
    ///
    /// Off by default and inert to the market: the second settlement runs on
    /// its own book, its result reaches no company field, and it takes no
    /// draw. It is served the four uniforms the real settlement was served,
    /// rewound, so it requires [`SettleDrawPolicy::FourAlways`] and is
    /// skipped under `FourOrZero`, where the settlement draws from the
    /// caller's recorded source and there is no buffer to rewind.
    pub settle_depth_counterfactual: bool,
    /// Nominal output when the run opened: `gdp * cpi` at construction.
    ///
    /// Read only by [`nominal_scale`], and only when
    /// `earnings_nominal_growth` is nonzero, which is pt-v18 and no preset
    /// before it. A caller building `TickInputs` directly passes the
    /// opening `economy.gdp * economy.cpi` of the run it is simulating; a
    /// single-tick caller passes this tick's own, which makes the ratio
    /// 1.0 and the valuation the one every earlier preset computes.
    pub nominal_output_base: f64,
    /// Trading days this run has closed, which is the buyback factor's
    /// clock.
    ///
    /// Read only by [`buyback_scale`], and only when
    /// `buyback_payout_share` is nonzero, which is pt-v18 and no preset
    /// before it. A single-tick caller passes 0, which makes the factor
    /// 1.0 and the valuation the one every earlier preset computes.
    pub elapsed_days: i64,
    /// The model coefficients (the runtime seam, CALIBRATION.md §5). The
    /// engine passes its own; a caller building `TickInputs` directly
    /// passes [`crate::params::PT_V1`] for the shipped model, which is
    /// bit-identical to the const build.
    pub params: &'a ModelParams,
}

/// What one tick produced, beyond the mutations applied to the companies.
#[derive(Debug, Clone, PartialEq)]
pub struct TickOutcome {
    /// Indices into the input slice, in the order the tick processed them.
    pub active_indices: Vec<usize>,
    /// Fair value per active company — the book's anchor, NOT a price.
    ///
    /// Named for its ROLE in phase 4. It is `fundamental_values * exp(s)`, so
    /// it already contains the mispricing; the valuation itself is
    /// `fundamental_values`. Dividing a print by this gives the
    /// microstructure residual, not the mispricing.
    pub fair_values: Vec<f64>,
    /// The valuation per active company, straight from `compute_fair_value` —
    /// earnings, sector anchor, rates. No mispricing in it.
    pub fundamental_values: Vec<f64>,
    /// Every contribution to this tick's change in `s`, per active company, in
    /// [`S_COMPONENT_KEYS`] order.
    ///
    /// The reason the whole library exists. `factors` below says what the four
    /// shock drivers were; this says what each of them, plus the reversion and
    /// the crowd, actually DID to the mispricing on this tick. They sum to
    /// `Δs` -- so a consumer can verify the label against the outcome rather
    /// than trusting it.
    pub s_components: Vec<[f64; 8]>,
    /// Volume printed per active company.
    pub volumes: Vec<f64>,
    /// The live factor decomposition per active company, in `active_indices`
    /// order.
    ///
    /// This is the labelled-dataset output: the simulator knows WHY each price
    /// moved, which no historical dataset can tell you. Retained rather than
    /// discarded after the drift is summed -- the components cost nothing to
    /// carry and cannot be recovered afterwards.
    pub factors: Vec<LiveFactors>,
    pub shared_factors: SharedFactors,
    /// The shock that arrived, per active company, in log units.
    ///
    /// `log(fair_value / the last print)`: the distance the price model put
    /// between the tape and this tick's anchor, before the book covered any
    /// of it. Phase 4 reports it whether or not the market is open, because
    /// a closed tick still moves fair value and prints the model price.
    pub shock: Vec<f64>,
    /// The depth that absorbed it, per active company, in log units.
    ///
    /// `log(the print / fair_value)`, against the print the tape carries --
    /// so it includes the second circuit breaker, which lands between the
    /// book's last trade and what is published. `shock + absorbed` is the
    /// print's log move away from the last print.
    pub absorbed: Vec<f64>,
    /// How far the second circuit breaker moved the print, per active
    /// company, in log units.
    ///
    /// `log(the print / what the book settled)`, so zero on every tick the
    /// breaker left alone. `absorbed - clamp` is the book's own share of the
    /// distance from the model price to the tape.
    ///
    /// Separate because the two cancel. On every clamped row measured the
    /// book and the breaker pull opposite ways, and on roughly three fifths
    /// of them they cancel to the last bit, so `absorbed` reads exactly zero
    /// on a name the breaker had just moved 513 basis points -- the same
    /// value it takes on a tick that never settled at all.
    pub clamp: Vec<f64>,
    /// What the same tick would have printed against unbounded depth, per
    /// active company.
    ///
    /// Empty unless [`TickInputs::settle_depth_counterfactual`] was set and
    /// the schedule was [`SettleDrawPolicy::FourAlways`]. Same draws, same
    /// book state, same breaker; the only difference is how many resting
    /// levels a slice could walk.
    pub unbounded_print: Vec<f64>,
    /// Liquidity's share of the print's move, per active company.
    ///
    /// `log(print / unbounded_print) / log(print / the last print)`: how far
    /// the depth bound moved the print, as a multiple of the move the print
    /// actually made. Zero when the two prints coincide, which covers every
    /// tick that did not settle, and NaN when the print did not move and the
    /// deeper book would have moved it. Empty alongside `unbounded_print`.
    ///
    /// # It is normally NEGATIVE, and it is not a percentage
    ///
    /// The bound TRUNCATES a walk. A market order that exhausts a shallow
    /// book stops there; against every resting level it keeps filling and
    /// prints further from where it started. So the real print sits between
    /// the last print and the unbounded print, the numerator opposes the
    /// denominator, and the share comes out below zero. Measured on
    /// `Universe.random(12, seed=111)` at seed 42 over three days of pt-v16:
    /// 1,351 rows negative against 72 positive, of 1,436 where the bound
    /// moved the print at all. It read 1,409 against 100 of 1,516 on the
    /// roster the universe generator produced before it was reconciled to
    /// open at fair value, which re-drew the roster and so the market.
    ///
    /// Read it through the identity it satisfies: the unbounded book's move
    /// away from the last print is `1 - share` times the printed move. A
    /// share of -1 means the deeper book would have moved the price twice as
    /// far. Nothing bounds it by one, so a small printed move against a large
    /// truncation gives a large ratio.
    pub liquidity_share: Vec<f64>,
}

/// Run one simulated market minute.
/// Volume's response to the market factor's variance.
///
/// Returns exactly `1.0` when [`crate::params::ModelParams::volume_variance_gain`]
/// is zero, so every preset before pt-v4 prints the volume it always did --
/// `x * 1.0` is `x` for every f64, which the known-answer digest checks on
/// every build.
///
/// The ratio is taken in VARIANCE rather than sigma because the quantity
/// with the documented persistence is the variance, and squaring the sigma
/// ratio is how the rest of this model spells that (see
/// `factor_vol::update_market_variance_with`'s VIX target).
///
/// Clamped to [0.25, 4.0]: volume is a count, and a multiplier that reached
/// zero would print an empty tape.
/// The persistent volume multiplier, `exp(volume_state)`.
///
/// Returns exactly `1.0` at state 0.0 -- by BRANCH, not by evaluating
/// `exp(0.0)`. The two agree in IEEE-754, but the branch says the intent
/// out loud and owes nothing to an argument about a library function.
fn persistent_volume_multiplier(inputs: &TickInputs) -> f64 {
    if inputs.volume_state == 0.0 {
        return 1.0;
    }
    // Clamped for the same reason the variance multiplier is: volume is a
    // count, and an unbounded multiplier turns a plausible process into a
    // number nobody can defend.
    mathx::clamp(mathx::exp(inputs.volume_state), 0.25, 4.0)
}

fn variance_volume_multiplier(inputs: &TickInputs) -> f64 {
    let gain = inputs.params.volume_variance_gain;
    if gain == 0.0 {
        return 1.0;
    }
    let base_sigma = inputs.params.market_factor_sigma;
    if !(base_sigma > 0.0) {
        return 1.0;
    }
    let sigma_ratio = inputs.market_sigma_daily / base_sigma;
    let raw = 1.0 + gain * (sigma_ratio * sigma_ratio - 1.0);
    mathx::max(mathx::min(raw, 4.0), 0.25)
}

pub fn simulate_market_tick(
    companies: &mut [TickCompany],
    inputs: &TickInputs,
    rng: &mut impl Rng,
) -> TickOutcome {
    // A closed market changes NOTHING and costs ZERO draws — the guard sits
    // before the shared-factor draws, so a weekend or an overnight hour does
    // not advance the stream at all. Reproducing that position matters as
    // much as reproducing the return: drawing here would desynchronise every
    // consumer on every closed tick, which is most of the clock.
    if inputs.market_status == MarketStatus::Closed {
        return TickOutcome {
            active_indices: Vec::new(),
            fair_values: Vec::new(),
            fundamental_values: Vec::new(),
            s_components: Vec::new(),
            volumes: Vec::new(),
            factors: Vec::new(),
            shared_factors: SharedFactors {
                market_factor: 0.0,
                sector_factors: Vec::new(),
                crisis_spike: 0.0,
                prev_day_down: false,
                // No tick ran, so no draw had a sigma. Zero rather than
                // the constant: a reader of a closed market's factors
                // should not find a plausible sigma there.
                market_sigma_tick: 0.0,
            },
            shock: Vec::new(),
            absorbed: Vec::new(),
            clamp: Vec::new(),
            unbounded_print: Vec::new(),
            liquidity_share: Vec::new(),
        };
    }

    let tick_scale_factor = 1.0 / 390.0;
    let tick_scale = 1.0 / mathx::sqrt(390.0);
    let economy = inputs.economy;
    let open = inputs.market_status == MarketStatus::Open;
    let p = inputs.params;

    // ── Shared factors: 1 normal, then one per sector ─────────────────────
    // Drawn at PER-TICK scale directly, so the noise is not divided by 390
    // again later. The sigma is the CONDITIONAL level from the factor's
    // variance process, not the constant; when the caller passes the
    // baseline (`MARKET_FACTOR_SIGMA`) this line is bit-identical to the
    // constant-sigma era's spelling, association included.
    rng.site(crate::rng::Site::MarketFactorZ, 0);
    let market_factor = rng.next_normal() * inputs.market_sigma_daily * tick_scale;

    // Crisis correlation: above the crisis threshold, sector factors blend
    // toward the market factor, so diversification stops working exactly
    // when it is most wanted.
    //
    // Re-sited at the 2026-08 era boundary, in step with the economy's
    // crisis gates. The reference's trigger was `vix > 40` with a `/30`
    // ramp, and both halves were dead: endogenous VIX has a measured hard
    // ceiling of 26.57 (zero days above 30 in 42,336; the reference's own
    // economy goldens top out at 29.09, so the gates never fired upstream
    // either), and even a pinned VIX at the old trigger bought a blend of
    // at most 0.25 by the ramp. `CRISIS_VIX_THRESHOLD` (25.5, the P94 of
    // the long-run endogenous distribution — rare and real in this
    // model's own units) makes the trigger reachable, and the /1.4 ramp
    // makes crossing it MEAN something: the blend saturates at its 0.8
    // cap by VIX ≈ 26.6, the ceiling of what the macro chain can produce,
    // instead of asking for a VIX of 64 that cannot exist.
    // Today's stress, in VIX points above the crisis threshold.
    let instant_stress = if economy.vix > p.crisis_vix_threshold {
        economy.vix - p.crisis_vix_threshold
    } else {
        0.0
    };
    // UNIVERSE MEMORY. Without it this blend is a lookup on today's VIX and
    // nothing else: the tick VIX drops back under the threshold and the
    // whole cross-section decouples in the same tick, so a crisis leaves no
    // trace. With it, remembered stress from earlier days holds the blend
    // up while it decays, which is what real correlation does after a
    // panic. The branch keeps every earlier preset bit-identical -- at
    // weight zero this is `instant_stress` and no arithmetic has touched it.
    let effective_stress = if p.universe_stress_weight == 0.0 {
        instant_stress
    } else {
        instant_stress
            + p.universe_stress_weight
                * mathx::max(inputs.universe_stress - instant_stress, 0.0)
    };
    let vix_correlation_spike = if effective_stress > 0.0 {
        mathx::min(p.crisis_blend_cap, effective_stress / p.crisis_blend_ramp)
    } else {
        0.0
    };

    // The sector draw's sigma follows VIX when coupled, on the market
    // factor's own target shape: variance scales with (VIX / anchor)^2, so
    // sigma scales with its square root. A branch at zero keeps every preset
    // before this parameter bit-identical; at the anchor the ratio is exactly
    // 1.0 at any coupling. The draw count is unchanged, so the tape is too.
    let sector_sigma = if p.sector_vix_coupling == 0.0 {
        p.sector_factor_sigma
    } else {
        let ratio = economy.vix / p.market_vol_vix_anchor;
        p.sector_factor_sigma
            * mathx::sqrt(1.0 - p.sector_vix_coupling + p.sector_vix_coupling * (ratio * ratio))
    };
    let mut sector_factors = Vec::with_capacity(inputs.sector_keys.len());
    for (sector_index, sector) in inputs.sector_keys.iter().enumerate() {
        rng.site(crate::rng::Site::SectorZ, sector_index as u32);
        let idiosyncratic = rng.next_normal() * sector_sigma * tick_scale;
        // Where the blend takes from. At source 0.0 the sector draw is
        // attenuated and the market factor injected through this slot, the
        // reference behaviour. At 1.0 the draw is kept whole and the same
        // injection is applied in factors.rs to the market component, scaled
        // by the source weight; in between, proportionally.
        let kept = if p.crisis_blend_source == 0.0 {
            idiosyncratic * (1.0 - vix_correlation_spike) + market_factor * vix_correlation_spike
        } else {
            let consumed = idiosyncratic * (1.0 - vix_correlation_spike)
                + market_factor * vix_correlation_spike;
            consumed * (1.0 - p.crisis_blend_source) + idiosyncratic * p.crisis_blend_source
        };
        sector_factors.push((sector.clone(), kept));
    }
    let shared = SharedFactors {
        market_factor,
        sector_factors,
        crisis_spike: vix_correlation_spike,
        prev_day_down: inputs.prev_day_down,
        // The same expression the draw above multiplied the normal by, so
        // the recentring reads the sigma that was actually used rather
        // than one recomputed from the constant.
        market_sigma_tick: inputs.market_sigma_daily * tick_scale,
    };

    let intraday_vol_mult = intraday_vol(inputs.intraday_t);
    let intraday_volume_mult = intraday_volume(inputs.intraday_t, inputs.market_status);

    // ── Phase 1: factors ──────────────────────────────────────────────────
    let mut active_indices: Vec<usize> = Vec::new();
    let mut all_factors: Vec<LiveFactors> = Vec::new();
    let mut all_drifts: Vec<f64> = Vec::new();
    let mut all_noises: Vec<f64> = Vec::new();
    let mut all_news_vol_mults: Vec<f64> = Vec::new();
    let mut all_randoms: Vec<f64> = Vec::new();

    for (idx, company) in companies.iter().enumerate() {
        if company.is_bankrupt || !company.is_public {
            continue;
        }
        active_indices.push(idx);

        let vol = inputs
            .order_volumes
            .iter()
            .find(|(t, _)| t == &company.ticker)
            .map(|(_, v)| *v)
            .unwrap_or_default();
        let imbalance = order_imbalance(vol.buy, vol.sell, company.stock.avg_volume);

        // DRAW SITE: one normal, inside the factor computation.
        rng.site(crate::rng::Site::FactorIdioZ, idx as u32);
        let factors = calculate_live_factors(
            &company.factor_view(),
            inputs.news,
            imbalance,
            inputs.volatility_multiplier,
            &shared,
            p,
            rng,
        );

        // Only the SHOCK factors reach the price. Every driver lives in fair
        // value now, and every anchor is replaced by `s`'s own reversion.
        let (drift, noise) = if open {
            (
                (factors.company_news + factors.order_flow_impact + factors.short_squeeze_effect)
                    * tick_scale_factor,
                factors.random_noise,
            )
        } else {
            (
                (factors.company_news + factors.order_flow_impact) * tick_scale_factor,
                factors.random_noise * 0.15,
            )
        };
        all_drifts.push(drift);
        all_noises.push(noise);
        all_factors.push(factors);

        // News amplifies volume, capped at 10x.
        let mut news_volume_mult = 1.0;
        let mut impact_sum = 0.0;
        for entry in inputs.news_impact_queue {
            if entry.reversal_phase {
                continue;
            }
            let matches = entry.company_id.as_deref() == Some(company.id.as_str())
                || entry.sector.as_deref() == Some(company.sector.as_str())
                || entry.sectors.iter().any(|s| s == &company.sector);
            if matches {
                impact_sum += entry.remaining_impact;
            }
        }
        if impact_sum > 0.0 {
            news_volume_mult = 1.0 + mathx::min(9.0, impact_sum / 0.01);
        }
        all_news_vol_mults.push(news_volume_mult);

        // DRAW SITE: one uniform, STASHED. It is consumed in phase 3, and
        // drawing it there instead would give the same count on a different
        // stream.
        rng.site(crate::rng::Site::StashU, idx as u32);
        all_randoms.push(rng.next_f64());
    }

    let active_count = active_indices.len();
    let previous_closes: Vec<f64> = active_indices
        .iter()
        .map(|&i| companies[i].stock.previous_close)
        .collect();
    let current_prices: Vec<f64> = active_indices
        .iter()
        .map(|&i| companies[i].stock.price)
        .collect();

    // ── Phase 2: the s-process ────────────────────────────────────────────
    let econ_view = EconomyValuationInputs {
        corporate_bond_yield: Some(economy.corporate_bond_yield),
        federal_funds_rate: economy.federal_funds_rate,
        qe_pe_boost: Some(economy.qe_pe_boost),
        qe_assets_ratio: Some(economy.qe_assets_ratio),
    };

    let mut new_prices = vec![0.0; active_count];
    let mut fundamentals = vec![f64::NAN; active_count];
    let mut s_components = vec![[0.0f64; 8]; active_count];
    let mut crowd_leans = vec![0.0; active_count];

    // The fundamentals restated in the economy's current price level and
    // output, once for the whole tick because it is a macro quantity.
    // Exactly 1.0 under every preset before pt-v18, by the branch in
    // `nominal_scale`, and 1.0 on day 0 under pt-v18 as well.
    let nominal = nominal_scale(p, economy, inputs.nominal_output_base);

    for i in 0..active_count {
        let idx = active_indices[i];
        // A BRANCH rather than a multiply by 1.0. The two agree on every
        // finite value, and the branch is what the preset contract rests
        // on rather than on an argument about how floats behave.
        let grown = if nominal == 1.0 {
            companies[idx].valuation()
        } else {
            scale_valuation(companies[idx].valuation(), nominal)
        };
        // Per NAME, unlike the growth term above: the yield is this
        // company's own earnings over its own price. A BRANCH at 1.0 for
        // the same reason as the one above.
        let buyback = buyback_scale(p, grown.eps, current_prices[i], inputs.elapsed_days);
        let valuation = if buyback == 1.0 {
            grown
        } else {
            scale_valuation(grown, buyback)
        };
        let breakdown = compute_fair_value_with(
            &valuation, &econ_view, p.fair_value_book_floor,
            p.qe_pe_gain, p.qe_pe_stock_gain, p.neutral_discount_rate);
        let fv = breakdown.fair_value;

        // Lazy init: adopt the current premium/discount as the starting `s`,
        // so enabling the model — or loading an old save — causes no level
        // jump.
        if companies[idx].stock.mispricing_s.is_none() {
            let s0 = clamp_s(p, mathx::log(mathx::max(0.01, current_prices[i]) / fv));
            companies[idx].stock.mispricing_s = Some(s0);
            companies[idx].stock.mispricing_s_prev_close = Some(s0);
            companies[idx].stock.mispricing_momentum = Some(0.0);
        }

        let mut s_val = companies[idx].stock.mispricing_s.unwrap();
        let momentum = companies[idx].stock.mispricing_momentum.unwrap_or(0.0);
        fundamentals[i] = fv;

        // Forced flow: the stress-activated COMMON lean (params docstring).
        // Computed once, applied AND recorded in the crowd's slot -- the
        // attribution contract is "recorded as applied". A branch, not
        // arithmetic, so 0.0 is bit-identical.
        let forced = if p.forced_flow_gain != 0.0 {
            let common = -p.forced_flow_gain
                * mathx::max(0.0, economy.vix - p.forced_flow_threshold)
                * inputs.forced_flow_eff;
            // beta^0 through a branch, so screen one's uniform lean and
            // every recorded run reproduce bit for bit.
            if p.forced_flow_beta_exponent != 0.0 {
                let beta = companies[idx].stock.beta.unwrap_or(1.0);
                common * mathx::pow(mathx::max(beta, 0.1), p.forced_flow_beta_exponent)
            } else {
                common
            }
        } else {
            0.0
        };

        // Each contribution recorded as it is applied, in the same spelling
        // the update uses. Reported, never fed back: the update below is
        // written exactly as the source writes it, because `s*phi` and
        // `s + s*(phi-1)` round differently and the benched trajectories were
        // produced by that spelling. Summing these to advance `s` instead
        // would be a different market.
        let raw = &all_factors[i];
        let scale = 1.0 / 390.0;
        s_components[i] = if open {
            [
                s_val * (p.s_phi_tick - 1.0),
                (p.momentum_theta * momentum) / 390.0,
                (if p.forced_flow_gain != 0.0 {
                    crowd_lean_with(p, s_val, momentum) + forced
                } else {
                    crowd_lean_with(p, s_val, momentum)
                }) / 390.0,
                raw.company_news * scale,
                raw.order_flow_impact * scale,
                raw.short_squeeze_effect * scale,
                all_noises[i] * intraday_vol_mult,
                // The breaker's slot, filled below if it binds.
                0.0,
            ]
        } else {
            // Closed: no reversion, no crowd, and the squeeze term is not
            // applied -- `all_drifts` dropped it. Zeros here are the honest
            // report of that, not missing data.
            [
                0.0,
                0.0,
                0.0,
                raw.company_news * scale,
                raw.order_flow_impact * scale,
                0.0,
                all_noises[i],
                0.0,
            ]
        };

        if open {
            // The crowd reacts to the mispricing it can SEE — the pre-update
            // state — so there is no same-tick feedback loop.
            let lean = crowd_lean_with(p, s_val, momentum);
            crowd_leans[i] = lean;
            // Written exactly as the source writes it. `s*φ` and
            // `s + s*(φ-1)` round differently, and the benched trajectories
            // were produced by this spelling.
            s_val = s_val * p.s_phi_tick
                + (p.momentum_theta * momentum) / 390.0
                + (if p.forced_flow_gain != 0.0 { lean + forced } else { lean }) / 390.0
                + all_drifts[i]
                + all_noises[i] * intraday_vol_mult;
        } else {
            s_val = s_val + all_drifts[i] + all_noises[i];
        }
        s_val = clamp_s(p, s_val);

        let mut new_price = mathx::max(0.01, fv * mathx::exp(s_val));

        // Breaker #1 — the MODEL price. If it binds, `s` is re-derived from
        // the clamped price so state and price stay consistent. The band
        // multipliers are derived ONCE, in the params constructor (§5.3),
        // never per call site.
        let max_price = previous_closes[i] * p.breaker_up;
        let min_price = mathx::max(previous_closes[i] * p.breaker_down, 0.01);
        if new_price > max_price || new_price < min_price {
            new_price = mathx::max(min_price, mathx::min(max_price, new_price));
            let before = s_val;
            s_val = clamp_s(p, mathx::log(new_price / fv));
            // What the breaker took off the tick, booked to the breaker. The
            // columns reconstruct the move on a halted day now (§79).
            s_components[i][7] += s_val - before;
        }

        companies[idx].stock.mispricing_s = Some(s_val);
        new_prices[i] = mathx::min(new_price, p.price_hard_cap);
    }

    // ── Phase 3: volume ───────────────────────────────────────────────────
    let volume_multiplier = if open { 1.0 } else { 0.1 };
    // Volume tracks the market factor's variance, which is persistent and
    // exogenous to volume. At gain 0 this is exactly 1.0 and the product
    // below is bit-identical to the single-multiplier form.
    let volume_multiplier = volume_multiplier * variance_volume_multiplier(inputs);
    // The persistent component: exactly 1.0 at state 0.0, so the product
    // is bit-identical for every preset that does not set it.
    let volume_multiplier = volume_multiplier * persistent_volume_multiplier(inputs);
    let mut volumes = vec![0.0; active_count];
    for i in 0..active_count {
        let idx = active_indices[i];
        // The per-NAME persistent component (§107). Guarded on the slice
        // being empty AND on the state being zero, so a preset that does not
        // set it multiplies by nothing at all rather than by `exp(0.0)`.
        let volume_multiplier = match inputs.volume_idio.get(idx) {
            Some(&s) if s != 0.0 => {
                volume_multiplier * mathx::clamp(mathx::exp(s), 0.25, 4.0)
            }
            _ => volume_multiplier,
        };
        // Volume that follows the NAME's own conditional variance (§112).
        //
        // §111 measured why the per-name state of §107 could not be used: it
        // fixes `volume_change_acf1` and takes `volume_abs_return_corr` down
        // with it, exactly as the COMMON component does. The cause is not
        // common-versus-per-name, which is what §107 assumed. It is that
        // volume variance unrelated to a name's own returns dilutes a
        // statistic measuring how well volume tracks the size of that name's
        // move, and per-name noise is as unrelated as market-wide noise.
        //
        // This is the return-RELATED version, and the per-name analogue of
        // `volume_variance_gain`, which does the same thing with the MARKET
        // factor's variance and nothing else. The reference is the sector's
        // base daily variance, a static table lookup, so no plumbing and no
        // new state.
        let volume_multiplier = if inputs.params.volume_idio_variance_gain == 0.0 {
            volume_multiplier
        } else {
            match crate::sectors::by_key(&companies[idx].sector)
                .map(|s| s.base_daily_variance())
            {
                Some(base) if base > 0.0 => {
                    let ratio = companies[idx].stock.garch_variance / base;
                    let raw = 1.0
                        + inputs.params.volume_idio_variance_gain * (ratio - 1.0);
                    volume_multiplier * mathx::clamp(raw, 0.25, 4.0)
                }
                _ => volume_multiplier,
            }
        };
        let stock = &companies[idx].stock;
        // Zero-guard: a newly listed company before `resetDailyPrices` seeds
        // `open` would divide by zero and propagate NaN into the batch.
        let daily_change = if stock.open > 0.0 {
            ((new_prices[i] - stock.open) / stock.open).abs()
        } else {
            0.0
        };
        let base_volume = stock.avg_volume / 390.0;
        let price_magnitude = daily_change / 0.01;
        // The stashed uniform from phase 1, consumed here.
        // Four literals until 0.3.0, shipped at their old values so this is
        // bit-identical wherever a preset leaves them alone. `volume_move_cap`
        // is the one that mattered: at 4.0 every move past four percent
        // trades the same, so no crisis day contributes any covariance to
        // `volume_abs_return_corr`. See ModelParams::volume_move_cap.
        let p = inputs.params;
        let volume_scale = p.volume_move_floor
            + p.volume_move_response * mathx::min(price_magnitude, p.volume_move_cap)
            + all_randoms[i] * p.volume_move_noise;
        volumes[i] = (base_volume
            * volume_multiplier
            * volume_scale
            * intraday_volume_mult
            * all_news_vol_mults[i])
            .floor();
    }

    // ── Phase 4: settlement ───────────────────────────────────────────────
    //
    // The decomposition columns are filled here rather than derived
    // afterwards, because `shock` is measured against the LAST print and a
    // consumer reading a finished table has already lost it at the first tick
    // of a day.
    let mut shock_col = vec![0.0; active_count];
    let mut absorbed_col = vec![0.0; active_count];
    let mut clamp_col = vec![0.0; active_count];
    // The counterfactual arm needs the buffered uniforms, which exist only on
    // the engine's own generated schedule. Under `FourOrZero` the settlement
    // draws lazily from the caller's recorded stream and a second settlement
    // would take draws off it, so the arm does not run and its columns stay
    // empty rather than being filled with the real print twice.
    let depth_arm = inputs.settle_depth_counterfactual
        && inputs.settle_draws == SettleDrawPolicy::FourAlways;
    let mut unbounded_col = if depth_arm { vec![0.0; active_count] } else { Vec::new() };
    let mut share_col = if depth_arm { vec![0.0; active_count] } else { Vec::new() };

    for i in 0..active_count {
        let idx = active_indices[i];
        let fair_value = new_prices[i];
        let volume = volumes[i].floor();
        // The tape's last print, read before this tick overwrites it at the
        // foot of the loop. `micro` below is built from the same field.
        let last_print = companies[idx].stock.price;
        // The breaker's band for this company, hoisted so the counterfactual
        // print is clamped by the same bounds as the real one. Comparing a
        // clamped print against an unclamped one would book the breaker to
        // liquidity.
        let print_max = previous_closes[i] * p.breaker_up;
        let print_min = mathx::max(previous_closes[i] * p.breaker_down, 0.01);
        let breaker = |price: f64| {
            if price > print_max || price < print_min {
                mathx::max(print_min, mathx::min(print_max, price))
            } else {
                price
            }
        };
        // NaN until the arm runs, and never read unless it does.
        let mut unbounded_price = f64::NAN;

        let mut new_price = fair_value;
        if open {
            // DRAW SITE: four uniforms. Under `FourAlways` they are drawn
            // HERE, unconditionally, and the settle is served from the
            // buffer — an early return leaves drawn values unused rather
            // than draws untaken, so the stream's position cannot depend on
            // which guard fired. Under `FourOrZero` the settle draws
            // lazily from the shared source, four or zero, matching what a
            // recorded reference stream actually holds.
            rng.site(crate::rng::Site::SettleU, idx as u32);
            let mut predrawn = match inputs.settle_draws {
                SettleDrawPolicy::FourAlways => Some(PredrawnUniforms::new([
                    rng.next_f64(),
                    rng.next_f64(),
                    rng.next_f64(),
                    rng.next_f64(),
                ])),
                SettleDrawPolicy::FourOrZero => None,
            };
            let micro = companies[idx].micro_view(companies[idx].stock.price);
            let options = SettleOptions {
                // From the params, so a preset that smooths the size curve
                // smooths it in settlement too rather than only in the book.
                spread_size_smoothness: inputs.params.spread_size_smoothness,
                spread_size_exponent: inputs.params.spread_size_exponent,
                vix: economy.vix,
                difficulty: None,
                flow_lean: Some(crowd_leans[i]),
                // The shipped bound. `settle_price_through_book` returns it
                // untouched at 1.0, so this line moves no trajectory.
                depth_multiplier: 1.0,
            };
            let settled = match predrawn.as_mut() {
                Some(buffer) => {
                    settle_price_through_book(&micro, fair_value, volume, &options, buffer)
                }
                None => settle_price_through_book(&micro, fair_value, volume, &options, rng),
            };
            new_price = settled.price;

            // The depth counterfactual: this tick again, from the same state,
            // against every level the maker quotes.
            //
            // It runs HERE, between the settlement and the maker-inventory
            // carry below, for two reasons. The book it quotes against is
            // built from `micro`, which was taken before the settlement, so
            // the arm sees the state the real settlement saw rather than the
            // state it left. And it takes NO draw: the buffer is rewound and
            // serves the same four uniforms a second time, which is why the
            // stream position after this block is what it was without it.
            if depth_arm {
                if let Some(buffer) = predrawn.as_mut() {
                    buffer.rewind();
                    // A second build rather than a clone of the real book,
                    // and the two are the same thing here: `build_live_book`
                    // is a pure function of the company and the options, and
                    // the only option that differs is the depth multiplier.
                    // The arm's book IS the first one with more levels.
                    let deep = settle_price_through_book(
                        &micro,
                        fair_value,
                        volume,
                        &SettleOptions {
                            depth_multiplier: f64::INFINITY,
                            ..options
                        },
                        buffer,
                    );
                    // The maker inventory this returns is DISCARDED, along
                    // with everything else about it except the price. An arm
                    // that fed its fills back would be a second market, and
                    // the next tick would no longer be the one that actually
                    // ran.
                    unbounded_price = breaker(deep.price);
                }
            }

            // Breaker #2 — the PRINT. See the module header for why this one
            // is the load-bearing clamp.
            //
            // The settled price is kept so the clamp can be reported apart
            // from the book. They pull opposite ways on every clamped row
            // and cancel exactly on most of them, so one column carrying
            // both says a halted name absorbed nothing.
            let settled_price = new_price;
            new_price = breaker(new_price);
            clamp_col[i] = mathx::log(new_price / settled_price);

            // Carry maker inventory forward. This is what makes impact
            // PERSIST: a large buy leaves the maker short, so it keeps quoting
            // higher until opposing flow lets it unwind.
            if settled.maker_inventory_delta != 0.0 {
                companies[idx].stock.maker_inventory = Some(
                    companies[idx].stock.maker_inventory.unwrap_or(0.0)
                        + settled.maker_inventory_delta,
                );
            }
        }

        // The decomposition, against the print the tape carries. `absorbed`
        // therefore holds the breaker as well as the book, which is the
        // honest reading of "what stood between the model price and the
        // print" -- on a halted name the breaker IS what absorbed the shock.
        let (shock, absorbed) = decompose(last_print, fair_value, new_price);
        shock_col[i] = shock;
        absorbed_col[i] = absorbed;
        if depth_arm {
            // A tick that never settled leaves the arm unrun, and the honest
            // reading of that is that unbounded depth would have printed the
            // same price. `mathx::log` of a ratio of one is zero, so the
            // share below is zero without a special case.
            let unbounded = if unbounded_price.is_nan() { new_price } else { unbounded_price };
            unbounded_col[i] = unbounded;
            let moved = mathx::log(new_price / last_print);
            share_col[i] = if unbounded == new_price {
                // The two prints coincide, so liquidity's share is zero
                // whatever the move was -- including a move of zero, where
                // the ratio below would be 0/0.
                0.0
            } else if moved == 0.0 {
                // The print did not move and the deeper book would have
                // moved it. There is no move to apportion, so the share is
                // undefined and says so. Dividing anyway gives an infinity
                // that survives into every mean taken over the column;
                // measured at 7 rows in 14,040 on a three-day run of twelve
                // names, so it is rare and it is real.
                f64::NAN
            } else {
                mathx::log(new_price / unbounded) / moved
            };
        }

        let stock = &mut companies[idx].stock;
        stock.previous_tick_price = Some(stock.price);
        stock.price = new_price;
        stock.high = mathx::max(stock.high, new_price);
        stock.low = mathx::min(stock.low, new_price);
        stock.volume += volume;
        stock.market_cap = new_price * stock.shares_outstanding;
    }

    TickOutcome {
        active_indices,
        fair_values: new_prices,
        fundamental_values: fundamentals,
        s_components,
        volumes,
        factors: all_factors,
        shared_factors: shared,
        shock: shock_col,
        absorbed: absorbed_col,
        clamp: clamp_col,
        unbounded_print: unbounded_col,
        liquidity_share: share_col,
    }
}

pub fn clamp_s(params: &ModelParams, s: f64) -> f64 {
    // `Math.max(-CAP, Math.min(CAP, s))` — the min/max spelling, not the
    // ternary, matching the source.
    mathx::max(-params.mispricing_cap, mathx::min(params.mispricing_cap, s))
}

/// Serves settlement's four pre-drawn uniforms under
/// [`SettleDrawPolicy::FourAlways`].
///
/// Overrunning the budget panics rather than wrapping or re-drawing: the
/// settle's four-draw ceiling is a hard contract
/// (`microstructure::settle_price_through_book`), and a fifth request means
/// that contract broke — silently serving anything would convert a schedule
/// bug into a plausible-looking different market. `next_normal` panics for
/// the same reason: settlement draws uniforms only, and a normal request
/// here is a routing error, not a need.
struct PredrawnUniforms {
    draws: [f64; 4],
    at: usize,
}

impl PredrawnUniforms {
    fn new(draws: [f64; 4]) -> Self {
        Self { draws, at: 0 }
    }

    /// Serve the same four uniforms again, from the start.
    ///
    /// This is what makes the depth counterfactual free: the second
    /// settlement is fed the numbers the first one was fed, so the shared
    /// stream is not touched and the tick's draw count is what it was. The
    /// buffer is per-company and per-tick, so a rewind cannot reach any
    /// settlement but the one it belongs to.
    fn rewind(&mut self) {
        self.at = 0;
    }
}

impl Rng for PredrawnUniforms {
    fn next_f64(&mut self) -> f64 {
        let value = self.draws[self.at];
        self.at += 1;
        value
    }
    fn next_normal(&mut self) -> f64 {
        unreachable!("settlement draws uniforms only; a normal request here is a routing bug")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn s_phi_tick_matches_the_recorded_v8_bits() {
        assert_eq!(S_PHI_TICK.to_bits(), 0x3FEF_FFC1_E138_5E9E);
    }

    #[test]
    fn market_factor_sigma_is_the_2026_08_sweep_calibration() {
        // Not a tautology: a gate. This constant is the measured answer of
        // `tools/calibration/sweep_market_factor_vol.py` — the BASELINE of
        // the factor's variance process, chosen jointly with
        // `IDIO_SIGMA_SCALE` and the `factor_vol.rs` shape; the doc
        // comment on it records what the vector bought (correlation and
        // kurtosis in band together). Moving it by hand un-measures it;
        // whoever fails this test should re-run the sweep (one command)
        // and update the constant, its documentation and this pin
        // together. The sweep must also be re-run after any change that
        // re-orders RNG draws, because that re-rolls every statistic the
        // value rests on.
        assert_eq!(MARKET_FACTOR_SIGMA, 0.016);
    }

    #[test]
    fn s_phi_tick_compounds_to_the_daily_phi_across_a_session() {
        // 390 ticks of the per-tick decay must reproduce one day of the daily
        // decay. That relationship is the whole reason the constant exists.
        let mut compounded = 1.0f64;
        for _ in 0..390 {
            compounded *= S_PHI_TICK;
        }
        assert!(
            (compounded - crate::mispricing::MISPRICING_PHI).abs() < 1e-12,
            "390 ticks compounded to {compounded}, daily phi is {}",
            crate::mispricing::MISPRICING_PHI
        );
    }

    #[test]
    fn universe_memory_holds_correlation_up_after_the_shock_passes() {
        // The property this state exists for. Real correlation spikes with
        // a panic and unwinds over weeks; without memory this model's blend
        // is a lookup on today's VIX, so the cross-section re-decouples in
        // the same tick the VIX falls back. Here: shock the memory, then
        // ask what the blend does on a CALM day.
        let base = crate::params::PT_V1;
        let mut remembering = base.clone();
        remembering.universe_stress_weight = 1.0;
        remembering.universe_stress_decay = 0.97;

        let calm_vix = crate::economy::CRISIS_VIX_THRESHOLD - 5.0;
        let carried = 8.0; // VIX points of remembered stress

        let forgetful = blend_for(&base, calm_vix, carried);
        let remembers = blend_for(&remembering, calm_vix, carried);

        assert_eq!(forgetful, 0.0, "without memory a calm day decouples");
        assert!(remembers > 0.0,
                "with memory the blend survives the day the panic ended");
        assert!(remembers <= base.crisis_blend_cap);
    }

    #[test]
    fn universe_memory_is_inert_at_its_legacy_weight() {
        // Bit-identity for every preset before pt-v4: at weight zero the
        // blend must be exactly the today's-VIX lookup, whatever the
        // universe happens to be remembering.
        let base = crate::params::PT_V1;
        assert_eq!(base.universe_stress_weight, 0.0);
        for vix in [5.0, 15.0, 22.0, 30.0, 65.0] {
            for carried in [0.0, 3.0, 25.0] {
                assert_eq!(
                    blend_for(&base, vix, carried),
                    blend_for(&base, vix, 0.0),
                    "vix {vix} carried {carried}"
                );
            }
        }
    }

    /// The blend as `simulate_market_tick` computes it, extracted so the
    /// two tests above can ask about it without driving a whole market.
    fn blend_for(p: &crate::params::ModelParams, vix: f64, carried: f64) -> f64 {
        // The PARAMETER, as `simulate_market_tick` reads it. This helper takes
        // `p` and then read the constant, so it would have stopped mirroring
        // production the moment a test moved the threshold.
        let threshold = p.crisis_vix_threshold;
        let instant = if vix > threshold { vix - threshold } else { 0.0 };
        let effective = if p.universe_stress_weight == 0.0 {
            instant
        } else {
            instant + p.universe_stress_weight * mathx::max(carried - instant, 0.0)
        };
        if effective > 0.0 {
            mathx::min(p.crisis_blend_cap, effective / p.crisis_blend_ramp)
        } else {
            0.0
        }
    }

    /// The economy the scale is read off, at a stated nominal output.
    fn economy_at(gdp: f64, cpi: f64) -> EconomyState {
        let mut e = crate::economy::create_initial_economy_state(
            &crate::economy::InitialEconomyOptions::default());
        e.gdp = gdp;
        e.cpi = cpi;
        e
    }

    fn with_dial(value: f64) -> crate::params::ModelParams {
        let mut p = crate::params::PT_V16;
        p.earnings_nominal_growth = value;
        p
    }

    #[test]
    fn the_scale_is_one_wherever_the_dial_is_off() {
        // The branch, not the arithmetic: an output ten times the base
        // still returns a literal 1.0, so no preset before pt-v18 reaches
        // a multiply at all.
        let p = with_dial(0.0);
        for (gdp, cpi) in [(25000.0, 100.0), (250000.0, 100.0),
                           (25000.0, 1000.0), (0.0, 100.0)] {
            let e = economy_at(gdp, cpi);
            assert_eq!(nominal_scale(&p, &e, 2_500_000.0), 1.0);
        }
    }

    #[test]
    fn the_scale_is_the_output_ratio_when_the_dial_is_full() {
        let p = with_dial(1.0);
        let base = 2_500_000.0;
        assert_eq!(nominal_scale(&p, &economy_at(25000.0, 100.0), base), 1.0);
        assert_eq!(nominal_scale(&p, &economy_at(50000.0, 100.0), base), 2.0);
        assert_eq!(nominal_scale(&p, &economy_at(25000.0, 200.0), base), 2.0);
        // Down as well as up, which is what a contraction reaches and what
        // a flat premium could not do.
        assert_eq!(nominal_scale(&p, &economy_at(20000.0, 100.0), base), 0.8);
    }

    #[test]
    fn a_share_of_the_dial_is_a_share_of_the_level() {
        // Linear in the dial, so half the dial adds half of what the whole
        // of it adds. Stated on the level rather than on the log, which is
        // the shape every other dial in this era has.
        let base = 2_500_000.0;
        let e = economy_at(50000.0, 100.0);
        assert_eq!(nominal_scale(&with_dial(0.5), &e, base), 1.5);
        assert_eq!(nominal_scale(&with_dial(0.25), &e, base), 1.25);
    }

    #[test]
    fn a_base_that_is_not_a_positive_number_gives_one() {
        // Nothing the engine builds reaches this, because `gdp` and `cpi`
        // are levels the economy never takes to zero. A caller building
        // `TickInputs` by hand can, and a NaN entering the valuation would
        // freeze every book downstream with nothing to say where it came
        // from.
        let p = with_dial(1.0);
        let e = economy_at(25000.0, 100.0);
        for base in [0.0, -1.0, f64::NAN, f64::INFINITY] {
            assert_eq!(nominal_scale(&p, &e, base), 1.0);
        }
    }

    #[test]
    fn the_scaled_valuation_moves_both_fundamentals_and_nothing_else() {
        let company = CompanyValuationInputs {
            sector_avg_pe: Some(18.0),
            eps: Some(4.0),
            book_value_per_share: Some(20.0),
            revenue_growth: Some(0.05),
        };
        let scaled = scale_valuation(company, 1.25);
        assert_eq!(scaled.eps, Some(5.0));
        assert_eq!(scaled.book_value_per_share, Some(25.0));
        // A rate and a multiple, both already nominal-neutral.
        assert_eq!(scaled.revenue_growth, Some(0.05));
        assert_eq!(scaled.sector_avg_pe, Some(18.0));
        // An absent fundamental stays absent rather than becoming zero,
        // which is the difference between a company the valuation prices
        // off book and one it prices at nothing.
        let empty = scale_valuation(CompanyValuationInputs {
            sector_avg_pe: None, eps: None,
            book_value_per_share: None, revenue_growth: None,
        }, 1.25);
        assert_eq!(empty.eps, None);
        assert_eq!(empty.book_value_per_share, None);
    }
}
