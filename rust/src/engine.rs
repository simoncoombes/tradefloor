//! The engine — WP5. Where the port becomes something you can run.
//!
//! # What this owns, and what it deliberately does not
//!
//! It owns the seeded generator, the per-company price state, the economy and
//! the central bank. That is all.
//!
//! Events, AI decisions, whale trades, corporate actions and earnings stay in
//! the embedder and cross this boundary as **data** — news impulses and
//! impact-queue entries, not behaviour. That split is not a simplification;
//! it is what makes the boundary tractable. Whale events, for instance, reach
//! the price only through the generic news channels, so there is nothing to
//! port for them.
//!
//! # The streams are split, and that is the 2026-08 era boundary
//!
//! The reference ran every consumer — market, economy, microstructure,
//! embedder — off ONE PCG32 stream, so changing what any consumer drew
//! shifted every draw every other consumer saw afterwards. This engine
//! instead derives three independent substreams from the root seed
//! ([`crate::rng::stream`] documents the derivation contract):
//!
//! - **market** — everything inside `simulate_market_tick`, settlement
//!   included. With [`SettleDrawPolicy::FourAlways`] its schedule is a pure
//!   function of (status, active set, sector count): no price, macro value
//!   or order flow can move its position.
//! - **economy** — the daily macro chain, whose draw count genuinely
//!   depends on macro state. Its branches stay its own problem now.
//! - **external** — [`Engine::draw_uniform`] / [`Engine::draw_normal`]:
//!   seed-derived, reproducible, and incapable of perturbing the market.
//!
//! One seed still fully determines the whole simulation. What the split
//! buys is the counterfactual: vary the order flow (TCA), the macro path
//! (pinned-versus-baseline), or the embedder's own consumption (cutover),
//! and every other domain's sequence is bit-identical.
//!
//! Each stream keeps its own Box-Muller spare, inside its own `GameRng` —
//! the parity of normal draws is per-stream state and never crosses
//! between domains.
//!
//! Replaying a PRE-SPLIT recorded stream is still possible:
//! [`Engine::tick_with`] and [`Engine::advance_day_with`] take an external
//! draw source and consume it in the reference's shared-stream order,
//! settlement's four-or-zero included.
//!
//! # Columnar access
//!
//! State is held internally as `Vec<TickCompany>` — array-of-structs — because
//! that is what WP4's gated tick operates on, and re-shaping it would
//! invalidate 18,720 verified values for a layout preference.
//!
//! The FFI surface is columnar instead ([`Engine::prices`],
//! [`Engine::write_prices`]): one contiguous `f64` slice per field, which
//! crosses a WASM boundary as a single view rather than 108 marshalled
//! objects. Conversion happens when the embedder reads, not per tick, so the
//! cost is paid once per boundary crossing rather than 390 times a day.

use crate::economy::{
    Decision,
    check_cycle_transition_for, update_central_bank_with, update_economy_daily, CentralBankState,
    DailyInputs, EconomicShock, EconomyState,
};
use crate::market::{
    close_day_with, get_market_status, intraday_fraction, reset_daily_prices,
    simulate_market_tick, AvgVolumePolicy, CloseInputs, GameTime, MarketStatus,
    MarketVarianceState, NewsEvent, NewsImpactEntry, OrderVolume, SettleDrawPolicy, TickCompany,
    TickInputs,
};
pub use crate::opening_cache::OpeningCacheInfo;
use crate::params::ModelParams;
use crate::rng::{stream, DrawKind, DrawOverlay, DrawRecord, GameRng, Rng, RngState, Site};

/// The index, the live VIX and the forecast: what the derivatives read
/// (`index_level_listed`, `vix_intraday_live`, `forecast_horizon_sessions`).
mod foundations;

/// The index futures and their night session (`futures_index_listed`,
/// `night_session_steps`).
mod futures;
mod vix_futures;
mod rate_futures;
mod oil_futures;
mod margin;

/// The reference MAIN stream's sequence. Not 0 and not 1 —
/// both are different streams from the same seed, and picking the wrong one
/// produces a plausible market that matches nothing.
///
/// Since the 2026-08 stream split the ENGINE no longer seeds itself here —
/// it derives per-domain substreams instead (`crate::rng::stream`). The
/// constant remains because it is what a pre-split recording was produced
/// with: a replay harness reconstructing the reference's generator needs
/// `GameRng::new(seed, MAIN_STREAM)`, exactly as before.
pub const MAIN_STREAM: u32 = 99;

/// A fading VIX target premium under this many points is cleared to 0.0
/// ([`Engine::set_vix_target_premium`]), so a faded event stops being state
/// a snapshot carries.
pub const VIX_TARGET_PREMIUM_CLEARED_UNDER: f64 = 1e-6;

/// `Engine::macro_pins_today`: the VIX was pinned today.
pub const PIN_VIX: u16 = 1;
/// `Engine::macro_pins_today`: the corporate yield was pinned today.
pub const PIN_CORPORATE: u16 = 2;
/// `Engine::macro_pins_today`: the cycle phase was pinned today. Kept only
/// under `macro_pins_hold`, where the close holds the phase, or
/// `cycle_nowcast_accuracy`, where the market's belief was put on the pinned
/// phase (a pin is public news) and tonight's close takes no report, so the
/// belief holds on the pin through the close.
pub const PIN_CYCLE: u16 = 4;
/// The 10-year was pinned today (`macro_pins_hold`).
pub const PIN_T10: u16 = 8;
/// The 2-year was pinned today (`macro_pins_hold`).
pub const PIN_T2: u16 = 16;
/// The policy rate was pinned today (`macro_pins_hold`).
pub const PIN_POLICY: u16 = 32;
/// Any of the other fields `pin_macro` writes was pinned today
/// (`macro_pins_hold`); which ones is read off the economy's own copy of
/// them at the start of the close, since nothing between a pin and the
/// close writes any of them.
pub const PIN_INFLATION: u16 = 64;
pub const PIN_GROWTH: u16 = 128;
pub const PIN_UNEMPLOYMENT: u16 = 256;
pub const PIN_FEAR_GREED: u16 = 512;
pub const PIN_OIL: u16 = 1024;
pub const PIN_QE_PE: u16 = 2048;
pub const PIN_QE_ASSETS: u16 = 4096;
pub const PIN_TARIFF: u16 = 8192;
/// Every mark `macro_pins_hold` keeps.
pub const PIN_ALL: u16 = 0x3fff;
/// The corporate SPREAD over the 10-year was pinned today
/// (`pin_macro(corporate_spread=...)`): kept whatever the dials, since only
/// that pin sets it.
pub const PIN_SPREAD: u16 = 0x4000;

/// The fewest steps, in minutes of the engine's clock (a step is a day of
/// 24 * 60), between the last central-bank meeting and an intermeeting one
/// (`fed_put_emergency_vix`): 21, a month of sessions on the session
/// calendar pt-v20 runs, so one stressed month calls at most one.
pub const FED_PUT_EMERGENCY_GAP_MINUTES: i64 = 21 * 24 * 60;

/// The sessions over which `fed_drawdown_hold` reads the index's highest
/// close: a year.
pub const DRAWDOWN_WINDOW: usize = 252;

/// The stress hold's clock before any stressed close (`fed_stress_hold`):
/// longer than any hold the dial allows, and the ceiling the clock ages to.
pub const STRESS_HOLD_NEVER: f64 = 1.0e9;

/// The exact position of the engine's ten random streams, the checkpoint
/// half that cannot be reconstructed from the columns.
///
/// One [`RngState`] per stream, because each stream has its own LCG
/// position AND its own Box-Muller spare. A checkpoint that carried only
/// some of them would restore a market whose untouched domains replay
/// correctly and whose missing ones silently start a different sequence.
///
/// # Word order is not field order
///
/// [`EngineRngState::to_words`] and [`EngineRngState::from_words`] lay the
/// streams out by stream id ([`crate::rng::stream`]), which is the order of
/// [`EngineRngState::STREAM_NAMES`]:
///
/// | words | stream | id |
/// |---|---|---|
/// | 0..5 | `market` | 0 |
/// | 5..10 | `economy` | 1 |
/// | 10..15 | `external` | 2 |
/// | 15..20 | `jumps` | 3 |
/// | 20..25 | `volume` | 4 |
/// | 25..30 | `news` | 5 |
/// | 30..35 | `volume_idio` | 6 |
/// | 35..40 | `overnight` | 7 |
/// | 40..45 | `market_vol_level` | 8 |
/// | 45..50 | `crisis_epicentre` | 9 |
///
/// The fields below are declared with `volume_idio` before `news`, so a
/// host that packs the fields in declaration order puts those two streams
/// in each other's slots, and `from_words` restores them swapped without
/// complaint: both are valid streams. Pack with `to_words`, or by name with
/// [`EngineRngState::to_named_words`] and
/// [`EngineRngState::from_named_words`], which match streams by name and
/// cannot swap them.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct EngineRngState {
    /// Stream 0, words 0..5 of [`EngineRngState::to_words`].
    pub market: RngState,
    /// Stream 1, words 5..10.
    pub economy: RngState,
    /// Stream 2, words 10..15.
    pub external: RngState,
    /// The jump stream, stream 3, words 15..20. Carried here for the reason
    /// this type's own documentation gives: a stream left out of a
    /// checkpoint restores to a DIFFERENT sequence while looking correct.
    /// That is harmless while jumps are inert and silently wrong the day
    /// they are not.
    pub jumps: RngState,
    /// The persistent-volume stream, stream 4, words 20..25, carried for
    /// the same reason.
    pub volume: RngState,
    /// The per-name volume stream, carried for the same reason. Stream 6,
    /// words 30..35: AFTER `news` in [`EngineRngState::to_words`], though
    /// declared before it here.
    pub volume_idio: RngState,
    /// The endogenous-news stream, carried for the same reason. Left out,
    /// a restored engine would draw a different news sequence from the one
    /// it was checkpointed on, which is invisible while news is inert and
    /// silently wrong the day a preset switches it on. Stream 5, words
    /// 25..30: BEFORE `volume_idio` in [`EngineRngState::to_words`], though
    /// declared after it here.
    pub news: RngState,
    /// The overnight stream, stream 7, words 35..40, carried for the same
    /// reason.
    pub overnight: RngState,
    /// The market factor's slow-level stream, stream 8, words 40..45,
    /// carried for the same reason.
    pub market_vol_level: RngState,
    /// The crisis epicentre's stream, stream 9, words 45..50, carried for
    /// the same reason. It draws only on the session an unpinned episode
    /// starts, so a restore that dropped it would give the NEXT crisis a
    /// different epicentre from the one the parent would have drawn --
    /// invisible while `crisis_epicentre_extra` is 0.0 and silently wrong
    /// the day it is not.
    pub crisis_epicentre: RngState,
}

impl EngineRngState {
    /// Every stream in one flat array of
    /// [`ENGINE_RNG_STATE_WIDTH`](crate::ENGINE_RNG_STATE_WIDTH) numbers:
    /// stream `k` of [`crate::rng::stream`] (market 0, economy 1, external
    /// 2, jumps 3, volume 4, news 5, volume_idio 6, overnight 7,
    /// market_vol_level 8, crisis_epicentre 9) at words `k * W .. (k + 1) *
    /// W`, each as [`RngState::to_words`] writes it, where `W` is
    /// [`RNG_STREAM_WIDTH`](crate::RNG_STREAM_WIDTH). The order is the stream
    /// ids', [`EngineRngState::STREAM_NAMES`], not the fields': `news` (5)
    /// comes before `volume_idio` (6), which is declared first. A stream
    /// added later goes on the end.
    pub fn to_words(&self) -> [f64; crate::widths::ENGINE_RNG_STATE_WIDTH] {
        const W: usize = crate::widths::RNG_STREAM_WIDTH;
        let mut out = [0.0; crate::widths::ENGINE_RNG_STATE_WIDTH];
        for (k, s) in self.streams().iter().enumerate() {
            out[k * W..(k + 1) * W].copy_from_slice(&s.to_words());
        }
        out
    }

    /// Read back what [`EngineRngState::to_words`] wrote. Refuses a slice
    /// that is not exactly `ENGINE_RNG_STATE_WIDTH` long, and any stream
    /// [`RngState::from_words`] refuses, naming the stream.
    pub fn from_words(words: &[f64]) -> Result<Self, String> {
        const W: usize = crate::widths::RNG_STREAM_WIDTH;
        const N: usize = crate::widths::ENGINE_RNG_STATE_WIDTH;
        if words.len() != N {
            return Err(format!(
                "the engine's RNG state is {N} numbers ({} streams of {W}), got {}",
                N / W,
                words.len()
            ));
        }
        let read = |id: u32| {
            let k = id as usize;
            RngState::from_words(&words[k * W..(k + 1) * W])
                .map_err(|e| format!("stream {k}: {e}"))
        };
        Ok(EngineRngState {
            market: read(stream::MARKET)?,
            economy: read(stream::ECONOMY)?,
            external: read(stream::EXTERNAL)?,
            jumps: read(stream::JUMPS)?,
            volume: read(stream::VOLUME)?,
            news: read(stream::NEWS)?,
            volume_idio: read(stream::VOLUME_IDIO)?,
            overnight: read(stream::OVERNIGHT)?,
            market_vol_level: read(stream::MARKET_VOL_LEVEL)?,
            crisis_epicentre: read(stream::CRISIS_EPICENTRE)?,
        })
    }

    /// The streams' names in word order: entry `k` is the field whose
    /// stream [`EngineRngState::to_words`] writes at words
    /// `k * RNG_STREAM_WIDTH ..`. `news` comes before `volume_idio` here,
    /// the reverse of the fields' declaration order.
    pub const STREAM_NAMES: [&'static str; crate::widths::ENGINE_RNG_STREAMS] = [
        "market",
        "economy",
        "external",
        "jumps",
        "volume",
        "news",
        "volume_idio",
        "overnight",
        "market_vol_level",
        "crisis_epicentre",
    ];

    /// Every stream with its field's name, in word order: the same words as
    /// [`EngineRngState::to_words`], one block a stream. A host that saves
    /// the blocks under their names, in a map or a keyed record, and reads
    /// them back with [`EngineRngState::from_named_words`] never depends on
    /// the order.
    pub fn to_named_words(
        &self,
    ) -> [(&'static str, [f64; crate::widths::RNG_STREAM_WIDTH]); crate::widths::ENGINE_RNG_STREAMS]
    {
        let streams = self.streams();
        std::array::from_fn(|k| (Self::STREAM_NAMES[k], streams[k].to_words()))
    }

    /// Read streams back by name, in any order: each entry is a field's name
    /// from [`EngineRngState::STREAM_NAMES`] and the stream's words as
    /// [`RngState::to_words`] writes them. Refuses an unknown name, a name
    /// given twice, a stream left out, and any words
    /// [`RngState::from_words`] refuses, naming the stream.
    pub fn from_named_words(streams: &[(&str, &[f64])]) -> Result<Self, String> {
        const N: usize = crate::widths::ENGINE_RNG_STREAMS;
        let mut found: [Option<RngState>; N] = [None; N];
        for (name, words) in streams {
            let Some(k) = Self::STREAM_NAMES.iter().position(|n| n == name) else {
                return Err(format!(
                    "unknown RNG stream {name:?}; the streams are {:?}",
                    Self::STREAM_NAMES
                ));
            };
            if found[k].is_some() {
                return Err(format!("RNG stream {name:?} is given twice"));
            }
            found[k] = Some(
                RngState::from_words(words).map_err(|e| format!("stream {name}: {e}"))?,
            );
        }
        let missing: Vec<&str> = (0..N)
            .filter(|&k| found[k].is_none())
            .map(|k| Self::STREAM_NAMES[k])
            .collect();
        if !missing.is_empty() {
            return Err(format!("the RNG state is missing stream(s) {missing:?}"));
        }
        let s = |k: usize| found[k].expect("checked above");
        Ok(EngineRngState {
            market: s(stream::MARKET as usize),
            economy: s(stream::ECONOMY as usize),
            external: s(stream::EXTERNAL as usize),
            jumps: s(stream::JUMPS as usize),
            volume: s(stream::VOLUME as usize),
            news: s(stream::NEWS as usize),
            volume_idio: s(stream::VOLUME_IDIO as usize),
            overnight: s(stream::OVERNIGHT as usize),
            market_vol_level: s(stream::MARKET_VOL_LEVEL as usize),
            crisis_epicentre: s(stream::CRISIS_EPICENTRE as usize),
        })
    }

    /// The streams in stream-id order. An array literal, so a stream added
    /// to `stream::COUNT` without a place here fails to compile.
    fn streams(&self) -> [RngState; stream::COUNT] {
        const _: () = assert!(
            stream::MARKET == 0
                && stream::ECONOMY == 1
                && stream::EXTERNAL == 2
                && stream::JUMPS == 3
                && stream::VOLUME == 4
                && stream::NEWS == 5
                && stream::VOLUME_IDIO == 6
                && stream::OVERNIGHT == 7
                && stream::MARKET_VOL_LEVEL == 8
                && stream::CRISIS_EPICENTRE == 9
        );
        [
            self.market,
            self.economy,
            self.external,
            self.jumps,
            self.volume,
            self.news,
            self.volume_idio,
            self.overnight,
            self.market_vol_level,
            self.crisis_epicentre,
        ]
    }
}

/// Cumulative draws per stream. Diagnostic, per D-R1: the single most
/// useful numbers for locating a divergence, and deliberately not part of
/// any behavioural contract.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
#[non_exhaustive]
pub struct StreamDraws {
    pub market: usize,
    pub economy: usize,
    pub external: usize,
}

impl StreamDraws {
    pub fn total(&self) -> usize {
        self.market + self.economy + self.external
    }
}

/// The draw positions of every stream at one day's open, with what the
/// market stream's per-tick schedule needs to address a company's normals.
///
/// Within one open-market tick the market stream takes, in this order: one
/// normal for the market factor, one normal per sector, then for each
/// active company one normal (the idiosyncratic factor) and one uniform
/// (stashed for phase 3), then four uniforms per active company at
/// settlement. So the normals per tick are `1 + sectors + active.len()`,
/// and active company `k` (its position in `active`) draws its normal at
/// `normals_at_open + tick * per_tick + 1 + sectors + k`. That arithmetic
/// is `Engine::market_day_layout`; the draw log is the check on it.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct DayMark {
    pub day: i64,
    /// `(uniforms, normals)` per stream, indexed by `crate::rng::stream`.
    pub positions: [(u64, u64); stream::COUNT],
    pub active: Vec<u32>,
    pub sectors: u32,
    pub ticks: u32,
}

/// A company's market-stream normals on one day, as a strided set:
/// `first + t * stride` for `t` in `0..ticks`.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct MarketDayLayout {
    pub company: u32,
    pub first: u64,
    pub stride: u64,
    pub ticks: u32,
}

/// What the embedder supplies for one tick.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct TickRequest<'a> {
    pub time: GameTime,
    /// Difficulty-driven noise multiplier.
    pub volatility_multiplier: f64,
    /// News reduced to the four fields the factor model reads.
    pub news: &'a [NewsEvent],
    pub news_impact_queue: &'a [NewsImpactEntry],
    /// Aggregated pending order volume, keyed by ticker.
    pub order_volumes: &'a [(String, OrderVolume)],
}

impl<'a> TickRequest<'a> {
    /// One tick at `time` with a volatility multiplier of 1.0, no news and
    /// no orders. Set `volatility_multiplier`, `news`, `news_impact_queue`
    /// or `order_volumes` on the value to add them.
    pub fn new(time: GameTime) -> Self {
        TickRequest {
            time,
            volatility_multiplier: 1.0,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
        }
    }
}

/// What one tick produced.
#[non_exhaustive]
#[derive(Debug, Clone, PartialEq)]
pub struct TickOutcome {
    pub market_status: MarketStatus,
    /// Indices of the companies that were active, in processing order.
    pub active_indices: Vec<usize>,
    /// Fair value per active company — the book's anchor, NOT a price.
    pub fair_values: Vec<f64>,
    pub volumes: Vec<f64>,
    /// Draws consumed by this tick. Zero when the market was closed.
    pub draws_consumed: usize,
}

/// What an agent's order did when it met the book.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct OrderReport {
    pub order_id: String,
    pub agent: String,
    pub ticker: String,
    pub side: crate::order_book::Side,
    pub requested: f64,
    /// Shares taken from the book on arrival.
    pub filled: f64,
    /// Volume-weighted across the taker fills, or `None` if none.
    pub average_price: Option<f64>,
    /// The last level the order reached, or `None` if it took nothing.
    pub worst_price: Option<f64>,
    /// The mid of the book the order met.
    pub reference: f64,
    /// Shares left waiting, in the queue or for the traded range.
    pub resting: f64,
    /// Shares of a market order the book could not fill. Zero for a limit
    /// order, whose remainder waits.
    pub unfilled: f64,
    /// How the remainder waits, when it does.
    pub mode: Option<crate::agent_book::RestMode>,
    /// The taker fills, one per level, in the order the order took them.
    pub fills: Vec<crate::agent_book::AgentFill>,
}

/// What the embedder supplies at the close of a simulated day.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct DayCloseRequest<'a> {
    /// Per company, the day's accumulated `randomNoise` from factor
    /// attribution. `None` falls back to the day's total return.
    pub daily_innovations: &'a [Option<f64>],
    /// Per company, `sectorBaseDailyVariance(sector)`.
    pub sector_base_variances: &'a [f64],
    /// How the close treats `avg_volume`. `AvgVolumePolicy::Hold` -- the
    /// shipped default -- unless you are replaying a reference tape; the
    /// argument for the divergence lives on the policy itself.
    pub avg_volume: AvgVolumePolicy,
}

/// The live mark's refresh grid, in session minutes (`rate_intraday_live`):
/// the projection of tonight's curve is recomputed at the open, on the first
/// tick and on every tick whose minute of the session is a multiple of this.
/// Five lines up with `tf.evaluate`'s 65-minute steps.
pub const RATE_LIVE_REFRESH_MINUTES: i64 = 5;

/// The share of a session's market variance still to come after `elapsed`
/// of its 390 minutes, on the intraday profile the tick draws with.
fn session_variance_remaining(elapsed: i64) -> f64 {
    static PROFILE: std::sync::OnceLock<Vec<f64>> = std::sync::OnceLock::new();
    let tail = PROFILE.get_or_init(|| {
        // tail[k] = sum over minutes k..390 of the profile's variance.
        let n = 390usize;
        let mut tail = vec![0.0; n + 1];
        for i in (0..n).rev() {
            let v = crate::market::hours::intraday_vol(i as f64 / n as f64);
            tail[i] = tail[i + 1] + v * v;
        }
        tail
    });
    let k = elapsed.clamp(0, 390) as usize;
    if tail[0] > 0.0 { tail[k] / tail[0] } else { 0.0 }
}

/// The rest of a session's market move, integrated by eight equally likely
/// nodes: the means of a standard normal's eight equal-probability bins
/// (+-0.158, +-0.4913, +-0.8954, +-1.6468), rescaled so the rule's variance
/// is one. Three-point Gauss-Hermite with the factor's sd left the open's
/// expectation of the corporate yield 1.2 to 2.0 bp a day low on pt-v20, a
/// standing long-into-the-close edge; this rule on the index's sd leaves
/// -0.31 bp (r13 prototype).
fn live_mark_nodes() -> &'static [f64; 8] {
    static NODES: std::sync::OnceLock<[f64; 8]> = std::sync::OnceLock::new();
    NODES.get_or_init(|| {
        let bins = [-1.6468, -0.8954, -0.4913, -0.158, 0.158, 0.4913, 0.8954, 1.6468];
        let var: f64 = bins.iter().map(|x| x * x).sum::<f64>() / 8.0;
        let sd = crate::mathx::sqrt(var);
        let mut out = [0.0; 8];
        for (o, b) in out.iter_mut().zip(bins.iter()) {
            *o = b / sd;
        }
        out
    })
}

impl<'a> DayCloseRequest<'a> {
    /// A close under `AvgVolumePolicy::Hold`, the shipped default. Both
    /// slices take one entry per company, in roster order.
    pub fn new(daily_innovations: &'a [Option<f64>], sector_base_variances: &'a [f64]) -> Self {
        DayCloseRequest {
            daily_innovations,
            sector_base_variances,
            avg_volume: AvgVolumePolicy::Hold,
        }
    }
}

/// What the embedder supplies for the daily macro step.
#[derive(Debug, Clone)]
#[non_exhaustive]
pub struct DayAdvanceRequest<'a> {
    pub volatility: f64,
    pub active_shocks: &'a [EconomicShock],
    pub market_return_pct: f64,
    pub game_day: i64,
    /// Game timestamp in minutes, for the central bank's meeting calendar.
    pub timestamp: i64,
}

impl<'a> DayAdvanceRequest<'a> {
    /// The macro step as `Engine::advance_macro_day` would take it without
    /// a market: volatility 1.0, no active shocks and a market return of
    /// zero. Set `volatility`, `active_shocks` or `market_return_pct` on
    /// the value to change them.
    pub fn new(game_day: i64, timestamp: i64) -> Self {
        DayAdvanceRequest {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day,
            timestamp,
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct DayAdvanceOutcome {
    pub phase_changed: bool,
    pub meeting_held: bool,
    /// The action taken, when a meeting was held.
    ///
    /// `None` and `!meeting_held` are the same fact from two directions;
    /// `meeting_held` is kept because removing it would break callers for no
    /// gain. Before 0.4.2 this was computed, reduced to the boolean and
    /// discarded, so an embedder saw the rate move with nothing able to say
    /// why. Reconstructing it from the rate delta is guesswork:
    /// `StagflationHike` and `LaborEmergencyCut` are separated by the
    /// economic context that selected them, not by the size of the move.
    pub decision: Option<Decision>,
    /// Index into the six announcement variants, as `MeetingOutcome` reports
    /// it. Carried instead of the string, for the same reason as there: the
    /// draw is contractual and the prose is not.
    pub announcement_variant: Option<usize>,
    pub draws_consumed: usize,
}

/// One declared cash dividend: the name's roster slot and ticker, the
/// session it was declared on, the session it goes ex, and the amount per
/// share. See [`crate::market::dividends`].
#[derive(Debug, Clone, Default, PartialEq)]
#[non_exhaustive]
pub struct Distribution {
    pub company: usize,
    pub ticker: String,
    pub declared_day: i64,
    pub ex_day: i64,
    pub amount: f64,
}

/// Cloneable, and that is a correctness property rather than a convenience.
///
/// An in-memory fork is `self.clone()`. The alternative -- rebuilding a fresh
/// engine from a hand-written list of fields to copy -- was the mechanism, and
/// the list was incomplete five separate times: the per-day attribution
/// accumulators, the market-open flag, the market factor's variance, the
/// common log-volume state and the day counter were each added after a fork
/// diverged from the parent it was supposed to be a copy of. A derived clone
/// cannot omit a field, because a field added to this struct is copied
/// without anyone remembering to.
#[derive(Clone)]
pub struct Engine {
    /// True only while the economy runs alone before day zero (the
    /// stationary opening's draw and the macro burn-in), when the cycle
    /// reads `cycle_equity_hazard_opening` in place of an index it does not
    /// have. False in every session; not state, so nothing snapshots,
    /// restores or hashes it.
    economy_alone: bool,
    /// The root seed the streams were derived from. Kept only to key
    /// [`Engine::arrival_order`], which is a function of it and holds no
    /// state, so nothing snapshots, restores or hashes it: an engine is
    /// always built from its seed.
    root_seed: u64,
    market_rng: GameRng,
    economy_rng: GameRng,
    external_rng: GameRng,
    jump_rng: GameRng,
    volume_rng: GameRng,
    volume_idio_rng: GameRng,
    news_rng: GameRng,
    overnight_rng: GameRng,
    market_vol_level_rng: GameRng,
    crisis_epicentre_rng: GameRng,
    /// `stream::CYCLE_NOWCAST`: drawn only while `cycle_nowcast_accuracy` is set.
    cycle_nowcast_rng: GameRng,
    /// The opening mispricing draws, one standard normal per name of the
    /// roster the engine was built with and one more for the market's
    /// common level, from the one-shot [`stream::OPENING`]. Empty unless
    /// `opening_mispricing_sigma` or `opening_market_sigma` is non-zero,
    /// and emptied once the opening has been applied.
    opening_z: Vec<f64>,
    /// The mispricing each name opens at under the market's prehistory's
    /// valuation carry (`market_prehistory_valuation`), in roster order,
    /// NaN for a name the copy left without one: the copy's `s` at the end
    /// of its prehistory, which the opening takes in place of its draw.
    /// Empty unless the dial is on, and emptied with `opening_z` once the
    /// opening has been applied.
    opening_carry: Vec<f64>,
    /// The move each name's `s` took at the last open under the overnight
    /// process, in roster order, 0.0 where nothing moved. Per-day state
    /// like the attribution: the tape books it onto the day's first row.
    overnight_moves: Vec<f64>,
    /// What each name's `s` gave up to its fair value at the last open under
    /// a night split with a permanent share (`overnight_market_share` or
    /// `overnight_idio_share` beside `fair_value_news_share`), in roster
    /// order, 0.0 where nothing moved. Per-day output like `overnight_moves`
    /// and for the same reader: the tape books it onto the day's first row
    /// as `fair_value_shift`, so the columns sum to the change in `s` there.
    /// Not state, not hashed, not in the snapshot.
    overnight_fair_value_moves: Vec<f64>,
    /// What each name's `s` gave up to its fair value at the last close's
    /// jump, in roster order (`fair_value_shift`), 0.0 where nothing moved.
    /// Per-day state like `overnight_moves`: the tape books it onto the row
    /// where the jump is observed, beside the jump.
    jump_fair_value_moves: Vec<f64>,
    /// The move each name's `s` took at the last open's ex-date drop
    /// (`dividend_payout_share`), in roster order, 0.0 where nothing went
    /// ex. Per-day state like `overnight_moves`, and for the same reader:
    /// the tape books it onto the day's first row.
    dividend_moves: Vec<f64>,
    /// Every dividend declared so far, in declaration order. A record for
    /// the reader, like the tape: not state, not hashed, not in the
    /// snapshot. Empty on every preset.
    distribution_log: Vec<Distribution>,
    /// The earnings calendar's key (`rng::GameRng::earnings_key` of the
    /// seed the engine was built with): every report's date and draws are
    /// functions of it, the company's id and the quarter (`crate::earnings`).
    /// Derived at construction on every engine and read only with
    /// `earnings_surprise_sigma` set, which is also when the snapshot and
    /// the state hash carry it.
    earnings_key: u64,
    /// The earnings surprise each name's opening print realised at the last
    /// open, in roster order, 0.0 where none did. Per-day output like
    /// `overnight_moves`, recomputed at every open and read by nothing.
    earnings_moves: Vec<f64>,
    /// The part of the aggregate earnings cycle's move each name's fair
    /// value is holding back until its next report
    /// (`earnings_cycle_report_share`), in roster order, as a log level.
    /// Zeros, and neither snapshotted nor hashed, unless that share, the
    /// calendar and the cycle are all on.
    earnings_withheld: Vec<f64>,
    /// Tonight's market-factor draw under a night split
    /// (`overnight_market_share`), per unit of beta, before the tilt: the
    /// part of `market_vol`'s day factor the night put there. The session's
    /// live lagged-wire condition (`market_beta_down_asym_lag_live`) reads
    /// the day factor LESS this, so the wire keys on the session's own draws
    /// and the night's gap does not set the session's drift. Zero at every
    /// close; snapshotted and hashed only while that wire reads it
    /// ([`Engine::carries_night_market_factor`]).
    night_market_factor: f64,
    /// Each name's last print at the most recent `close_market`, in roster
    /// order; the construction price before the first close. Read by
    /// nothing that prices, so it is outside the state hash and the draw
    /// schedule. See [`Engine::last_closes`].
    last_closes: Vec<f64>,
    /// `last_closes` as it stood at the most recent `open_market`: the
    /// close the current day's change is measured from. See
    /// [`Engine::prior_closes`].
    prior_closes: Vec<f64>,
    /// Whether construction ran the model's own opening (the macro burn-in
    /// or the stationary cycle draw) over the economy it was given. See
    /// [`Engine::opening_settled`].
    opening_settled: bool,
    /// The day's endogenous news, generated once in `open_market` (§117).
    /// A field rather than a local because a tick loop and a single
    /// `run_session` are two spellings of the same day and both must read
    /// the same events; when this was a local in `run_session` they did not.
    session_news: Vec<crate::market::NewsEvent>,
    companies: Vec<TickCompany>,
    economy: EconomyState,
    central_bank: CentralBankState,
    sector_keys: Vec<String>,
    /// The per-sector variance state as a RATIO to the VIX-coupled target
    /// (`tick::sector_sigma_at` squared): the sector's daily variance is
    /// `target * s`, `s` a GARCH(1,1) with unconditional mean 1.0 on the
    /// standardised daily factor. Live when `sector_vol_alpha` or `_beta`
    /// is set; 0.0 means "not yet seeded" and reads as 1.0.
    sector_variance: Vec<f64>,
    /// The day's accumulated sector factor per sector key, the shock the
    /// state updates on at the close; reset at each close.
    sector_day_factor: Vec<f64>,
    /// The target variance (`sector_sigma_at` squared) the day's sector
    /// draws were scaled by, recorded in the tick loop so the close
    /// standardises the day's factor by the scale it was drawn at, not by
    /// the target after the close moved the VIX. 0.0 until the first tick.
    sector_target_day: f64,
    /// The per-name jump excitation `h_i`, live when `jump_idio_excitation`
    /// is set: the idiosyncratic arrival rate is `lambda (1 + h_i)`.
    jump_excitation: Vec<f64>,
    /// The per-name idiosyncratic variance RATIO state
    /// (`ModelParams::idio_vol_alpha`): mean one, multiplying the variance
    /// of the name's own draw. 0.0 is unseeded and reads 1.0. Beside it, the
    /// name's own jump the last close applied (the next session realises it,
    /// so it enters that session's shock) with the jump variance expected at
    /// the rate it was drawn, and the same pair for tonight's close, which
    /// is empty between sessions. Never read or written while all three dials
    /// are 0.0, which every preset carries.
    idio_variance: Vec<f64>,
    idio_jump_pending: Vec<f64>,
    idio_jump_var_pending: Vec<f64>,
    idio_jump_today: Vec<f64>,
    idio_jump_var_today: Vec<f64>,
    /// Per-company attribution, accumulated across the current day.
    ///
    /// Four entries per company -- company_news, order_flow_impact,
    /// short_squeeze_effect, random_noise -- summed tick by tick and reset at
    /// `open_market`.
    /// Ten slots: the tick's eight `S_COMPONENT_KEYS`, the daily jump,
    /// which `apply_jumps` writes to `s` outside the tick loop (it was
    /// missing until 2026-08-26, so on any preset carrying jumps the truth
    /// table's components did not reconstruct the move, §74), and the
    /// overnight move, which `apply_overnight` writes to `s` at the open.
    attribution: Vec<[f64; crate::market::factors::COMPONENT_COUNT]>,
    /// The `random_noise` column split into market, sector and
    /// idiosyncratic, accumulated across the day beside `attribution` and
    /// reset with it, plus the day's summed square of the scale the
    /// idiosyncratic part was drawn at with the name's own sigma divided
    /// out (`kappa^2`).
    ///
    /// `random_noise` is what the close feeds the per-name GJR as the day's
    /// innovation, and the column alone cannot say how much of that
    /// innovation is the name's own variance rather than the factor's and
    /// the sector's. These say it. Reported through `noise_part_column`,
    /// and read on the price path only while
    /// `ModelParams::garch_innovation_commensurate` is non-zero -- at 0.0
    /// nothing reads them and no trajectory owes them anything. They are
    /// per-DAY accumulators and `state_snapshot` carries them, exactly as
    /// it carries the attribution they sit beside and for the same reason:
    /// a fork taken mid-day whose close read them at zero would feed the
    /// per-name GJR a different innovation and price differently from the
    /// parent it is meant to be a copy of.
    noise_parts: Vec<[f64; 3]>,
    noise_own_scale2: Vec<f64>,
    /// The day's innovation for each name's GJR under a night split
    /// (`overnight_market_share` or `overnight_idio_share` set): the night's
    /// own noise, then every tick's `random_noise`, summed in that order.
    ///
    /// The attribution counts the night once, in its `overnight` slot, so
    /// its twelve slots sum to the day's change in `s` and agree with the
    /// tape. The GJR steps on the whole day's noise, the night's included,
    /// and this keeps that sum in the order the `random_noise` slot summed
    /// it when it also carried the night, so the close feeds the GJR the
    /// same bits. Empty while the split is off, where the close reads the
    /// `random_noise` slot as before. Per-day state, sized from construction
    /// and snapshotted and hashed exactly while the split is on.
    innovation_day: Vec<f64>,
    /// This tick's ground truth, per company slot.
    ///
    /// `attribution` above sums across the day, which is what a scorer wants
    /// and is useless as a dataset: it can say order flow moved a price today
    /// but not WHEN, and a label that cannot be aligned to a bar is not a
    /// label. The per-tick figures were computed and thrown away; these keep
    /// them.
    ///
    /// Three different quantities that are easy to confuse, so they are named
    /// apart: `fundamental` is the valuation, `anchor` is
    /// `fundamental * exp(s)` -- the price the model wanted before the book
    /// touched it -- and the printed price is what the book actually settled.
    tick_components: Vec<[f64; crate::market::factors::TICK_COMPONENT_COUNT]>,
    tick_fundamental: Vec<f64>,
    tick_anchor: Vec<f64>,
    /// This tick's print decomposition, per company slot: the shock that
    /// arrived and the depth that absorbed it, in log units.
    ///
    /// `anchor` above is the price the model wanted and the print is what the
    /// book settled, so the gap between them was already computable. These
    /// two put the LAST print on the same footing, which is what a consumer
    /// reading a finished table cannot recover: the first tick of a day has
    /// no previous row to difference against.
    tick_shock: Vec<f64>,
    tick_absorbed: Vec<f64>,
    /// How far the print breaker moved this tick's print, per slot. The
    /// book's own share is `tick_absorbed - tick_clamp`.
    tick_clamp: Vec<f64>,
    /// How far each price was moved between its last print and this tick,
    /// per slot, in log units: `log(the price the tick started from / the
    /// last print)`. Zero on every tick nothing wrote the price between
    /// prints. `repriced + shock + absorbed` is the print's log move from
    /// the previous print, where `shock + absorbed` alone is its move from
    /// the price the tick started from.
    tick_repriced: Vec<f64>,
    /// The log move written to each name's price since its last print and
    /// not yet booked onto a tape row, per slot: the close's re-mark and a
    /// `pin_macro`'s (`macro_publication_repricing`) and the opening print
    /// (`overnight_variance_ratio`). The next tick moves it into
    /// `tick_repriced` and zeroes it. NaN where it is not known, which is
    /// after a `restore_state` on a model that can write a price between
    /// prints: the snapshot does not carry it.
    ///
    /// RECORDING state, like the `tick_*` columns: it moves no price and no
    /// draw, it is outside the snapshot and the state hash, and a fork
    /// (`Clone`) carries it.
    repriced_pending: Vec<f64>,
    /// The depth counterfactual, per company slot. Empty unless
    /// [`Engine::set_settle_depth_counterfactual`] turned it on, which is
    /// how the reporting surface knows whether it has an arm to report.
    tick_unbounded_print: Vec<f64>,
    tick_liquidity_share: Vec<f64>,
    /// Whether every open tick settles a second time against unbounded
    /// depth. Off by default, and inert to the market either way: see
    /// [`crate::market::TickInputs::settle_depth_counterfactual`].
    settle_depth_counterfactual: bool,
    /// The market factor's conditional-variance state
    /// (`market::factor_vol`). Advanced at every close from the day's
    /// accumulated factor — zero draws — and read by every generated tick
    /// as the factor's sigma. Recorded-stream replay (`tick_with`)
    /// bypasses it: that era's factor was constant-sigma.
    market_vol: MarketVarianceState,
    /// The universe's remembered stress, in VIX points above the crisis
    /// threshold. Ratchets up with a shock and decays geometrically, so an
    /// event's effect on the cross-section OUTLIVES the day it happened.
    /// Zero, and inert, under every preset before pt-v4.
    universe_stress: f64,
    /// Forced-flow budget spent, in VIX-point-days. 0.0 while the
    /// reservoir dial ships 0.0; see `ModelParams::forced_flow_reservoir`.
    forced_flow_spent: f64,
    /// The market factor's slow variance level, in logs. 0.0 means a
    /// multiplier of exactly 1.0, which is every preset through pt-v18,
    /// where `market_vol_level_sigma` is 0.0, and every shipped preset
    /// since the 2026-09-20 recomposition. A preset with a positive sigma
    /// moves this field every session; pt-v19 did from 2026-09-14 until
    /// that recomposition, when the level went back to 0.0.
    /// This line said "every preset through pt-v19" until 2026-09-18, and
    /// stopped being true when pt-v19 took the dial off zero. See
    /// `ModelParams::market_vol_level_sigma`.
    market_vol_log_level: f64,
    /// The VIX's own slow log-level, in logs; 0.0 means a multiplier of
    /// exactly 1.0 on what the VIX prices, which is every preset. Driven by
    /// the same normal `market_vol_log_level` reads, so it adds no draw.
    vix_log_level: f64,
    /// The anchor's slow memory of the read-back's log deviation. Moves only
    /// with `vix_anchor_memory` nonzero. See `ModelParams::vix_anchor_memory`.
    vix_anchor_slow: f64,
    /// The business cycle's multiplier on the market factor's volatility,
    /// in logs (`l` in `ModelParams::market_vol_cycle_ratio`). `None` until
    /// the first close with the dial set, which puts it at its phase's
    /// value; never touched with the dial at 0.0, where the snapshot and the
    /// state hash do not carry it.
    market_vol_cycle_log: Option<f64>,
    /// The published VIX's stress-premium memory: the read-back's log
    /// deviation from the anchor's centre, kept at `vix_anchor_memory`'s
    /// rate and set to 0.0 by a pinned VIX. Moves only with
    /// `vix_stress_premium` non-zero, which no preset sets, and is carried
    /// by the snapshot and the state hash only then. See
    /// `ModelParams::vix_stress_premium` and [`Engine::published_vix`].
    vix_stress_memory: f64,
    /// The VIX's fear memory, in log units: the share of the VIX's
    /// excursion over its target that the target carries. Moves only with
    /// `vix_fear_uptake` non-zero, which no preset sets, and is carried by
    /// the snapshot and the state hash only then. See
    /// `ModelParams::vix_fear_uptake`.
    vix_fear: f64,
    /// The host's premium on the VIX target, in VIX points, and the
    /// half-life in sessions it fades at (0.0 holds it). 0.0 with nothing
    /// written, where the target reads no term. See
    /// [`Engine::set_vix_target_premium`].
    vix_target_premium: f64,
    vix_target_premium_half_life: f64,
    /// The host's floor under the VIX target, held until cleared. See
    /// [`Engine::set_vix_target_floor`].
    vix_target_floor: Option<f64>,
    /// Whether a crisis EPISODE is running. The episode starts at the open
    /// of the first session whose VIX is above `crisis_vix_threshold` with
    /// no episode running, and ends after `crisis_epicentre_end_sessions`
    /// consecutive sessions back under it.
    ///
    /// False on every session of pt-v1 through pt-v18: the whole block is
    /// gated on `crisis_epicentre_extra`, which those presets set to 0.0.
    /// pt-v19 ships 1.93, so on the default this is true through every
    /// episode. See `ModelParams::crisis_epicentre_extra`.
    crisis_in_episode: bool,
    /// Consecutive sessions the running episode has spent under the
    /// threshold. Reset to zero by any session back above it, which is what
    /// makes one crisis one episode rather than a run of scattered days.
    crisis_sessions_under: i64,
    /// The sector index in `crate::sectors::SECTORS` this episode's
    /// epicentre was drawn at, or `-1` for `none` -- a crisis with no
    /// epicentre, which is two of the tape's five episodes and is a DRAW
    /// rather than the absence of one. Meaningless while
    /// `crisis_in_episode` is false, and written to `-1` when an episode
    /// ends so a snapshot carries no stale sector.
    crisis_epicentre: i32,
    /// The epicentre a scenario has PINNED, if any: `-1` for `none`, else a
    /// sector index. `Some` overrides the draw for every episode that
    /// starts while it is set, and a pinned episode takes NO draw, so a
    /// pinned run consumes nothing on `stream::CRISIS_EPICENTRE`.
    ///
    /// Not part of the episode state: it is an input to the run, written by
    /// `pin_macro` and carried by the snapshot for the same reason the
    /// pinned macro fields are -- a fork that dropped it would resume
    /// drawing its own epicentre part-way through a pinned experiment.
    crisis_epicentre_pin: Option<i32>,
    /// Tonight's close sets the market factor's variance from the VIX
    /// instead of stepping toward it. Written by `pin_macro` when a scenario
    /// forces the VIX with its `vix_sets_variance` switch on, and consumed
    /// by the next `close_market`, which clears it: one forced session, one
    /// set. A session nobody forced closes as a free one.
    ///
    /// False on every session of every run that does not ask for it, so no
    /// preset and no existing scenario ever reads it true. Carried by the
    /// snapshot and the state hash only while true, for the reason the
    /// epicentre pin is: a fork taken between the pin and the close that
    /// dropped it would close a forced session as a free one.
    vix_sets_variance_pending: bool,
    /// What a caller pinned today, for tonight's corporate yield
    /// (`economy::daily`, the corporate yield between meetings). Bit
    /// [`PIN_VIX`]: the close charges the corporate yield no VIX term, since
    /// the close's VIX move is the law's reversion from the written level,
    /// which the next pin discards. Bit [`PIN_CORPORATE`]: the pinned
    /// corporate yield holds through the close. Set only while
    /// `corporate_yield_daily` is on, cleared by the close, and carried by
    /// the snapshot and the state hash only while non-zero, as
    /// `vix_sets_variance_pending` is, so no engine that never pins, and no
    /// preset through pt-v19, hashes or snapshots differently.
    macro_pins_today: u16,
    /// The corporate spread over the 10-year a caller pinned today, in
    /// percent; read only while `PIN_SPREAD` is marked.
    pinned_corporate_spread: f64,
    /// The volatility-feedback discount today's VIX pins moved the index
    /// by, in log points at a beta of one (`pinned_vix_variance_share`):
    /// `fair_value_vix_discount` times the change the pins made to the
    /// smoothed exposure. Summed over the session's pins and cleared by the
    /// close; 0.0, never written, with the dial off. Carried by the snapshot
    /// and the state hash only while non-zero, as `macro_pins_today` is.
    pinned_vix_jump: f64,
    /// The day's market t scale (`market_day_tail_df`): the multiplier on
    /// the market factor's variance for this session, drawn at the open
    /// and cleared to 1.0 by the close. Exactly 1.0, never written, with
    /// the dial off. Carried by the snapshot and the state hash only while
    /// it is not 1.0.
    market_day_scale: f64,
    /// Shared log-scale volume multiplier state. 0.0 means a multiplier of
    /// exactly 1.0, which is every preset before pt-v4.
    volume_state: f64,
    /// Per-NAME volume state, one per company (§107).
    volume_idio: Vec<f64>,
    /// The log jump each name's `s` took at the LAST close, one per
    /// company, carried across the open for the volume scale to subtract.
    ///
    /// Read only while `volume_move_jump_share` is off 1.0, and written
    /// only then, so every shipped preset leaves it all zeros and pays
    /// nothing for it. It is per-DAY state that outlives the open, which
    /// the attribution accumulator it is copied from does not: `apply_jumps`
    /// books the jump at the close of the day BEFORE the session that
    /// trades the gap in, and `open_market` clears the accumulator in
    /// between. Carried by `state_snapshot`, so a restored engine's first
    /// session reads the gap the copy's does; without it a restore lost one
    /// session of the mechanism.
    jump_move: Vec<f64>,
    /// Cumulative draws per stream, including any the embedder took through
    /// [`Engine::draw_uniform`]. The single most useful numbers for
    /// diagnosing a divergence: if these differ between two runs, nothing
    /// downstream is worth comparing.
    draws: StreamDraws,
    /// The day the engine is on, for the draw log and the day marks. Set by
    /// the caller that knows it (`set_current_day`), at each open, so the
    /// draws a close takes carry the day they close; zero on a fresh engine.
    /// A LABEL: the fills the book stamps and the draw log carry it, and
    /// nothing that prices reads it. The valuation reads `elapsed_days`.
    current_day: i64,
    /// Trading days elapsed when the current day opened: the clock the
    /// valuation reads, which is the buyback factor's elapsed time
    /// (`buyback_payout_share`). `set_current_day` sets it with the label,
    /// so a caller of the core sees one number as before; the Python
    /// binding sets the two apart, the label from `first_day` or `set_day`
    /// and this from its own day counter, so a label cannot reprice the
    /// market. They are equal on every run that numbers its days from the
    /// counter, which is every run that passes neither.
    elapsed_days: i64,
    /// The ticks run on the current day, carried across a restore. A
    /// restore drops the day marks, which is where the count normally
    /// lives (`ticks_today`), so a fill after it was stamped tick 0.
    /// `None` until a restore sets it, and ignored once a day mark exists.
    carried_ticks: Option<u32>,
    /// Each company's fair-value inputs as it was built or listed: earnings,
    /// book value and revenue growth per share. What `set_fundamentals`
    /// changes is measured against this, so a snapshot carries the three
    /// only once they have moved and every engine that was never told
    /// anything snapshots and hashes as it did before they were carried.
    base_fundamentals: Vec<[Option<f64>; 3]>,
    /// Each company's share count as it was built or listed. What
    /// `set_shares_outstanding` changes is measured against this, on the
    /// same rule as `base_fundamentals`: a snapshot carries the counts, and
    /// the state hash covers them, only once a host has moved one.
    base_shares: Vec<f64>,
    /// One mark per opened day: the day, the seven streams' draw positions
    /// at the open, the active company indices and the sector count, and
    /// the ticks the day ran. Together they map a `(day, company)` pair to
    /// its market-stream normal indices (`Engine::market_day_layout`).
    day_marks: Vec<DayMark>,
    /// Nominal output when this engine was built: `gdp * cpi` from the
    /// economy it was constructed with.
    ///
    /// The base of the growth term's ratio, so it is a constant of the run
    /// rather than state that advances, and it is carried in the snapshot
    /// for the reason every per-engine field is: an engine restored
    /// without it would rebase its valuation on the day it was restored
    /// and grow from there, which reads as a plausible market and is not
    /// the one the snapshot describes. Inert under every preset before
    /// pt-v18, where `earnings_nominal_growth` is 0.0 and nothing reads
    /// it.
    nominal_output_base: f64,
    /// The model coefficients this engine runs (the runtime seam).
    /// [`crate::params::PT_V1`] unless the engine was
    /// built with [`Engine::with_params`]; immutable for the engine's life,
    /// which is what lets its fingerprint be quoted for the whole run.
    params: ModelParams,
    /// `params.fingerprint()`, taken the first time anything asks and kept.
    ///
    /// The fingerprint hashes the whole preset surface and then every
    /// shipped preset to find a match, about a millisecond, and
    /// [`Engine::state_hash`] folds it in. The sandbox's tamper guard hashes
    /// the state twice around every call into agent code, so re-deriving it
    /// each time was about 90 per cent of the guard's cost and a tenth of an
    /// `evaluate`. `params` is fixed for the engine's life, so the first
    /// answer is the only answer; a clone carries it with the params it
    /// describes.
    model_fingerprint: std::sync::OnceLock<String>,
    /// The VIX at which every variance coupling reads ONE — the factor's
    /// forward map, the sector draw's sigma, the per-name GARCH clamp
    /// reference and the jump arrival rate.
    ///
    /// `params.market_vol_vix_anchor` under every preset before pt-v19, so
    /// every one of those four sites reads exactly the value it read
    /// before. Under [`ModelParams::vix_level_identity`] it is DERIVED at
    /// construction from the index's own unconditional variance
    /// ([`Engine::derive_vix_anchor`]) and the dial is not read at all.
    ///
    /// A field rather than a call, because it is a constant of the run: it
    /// depends on the roster and the parameters, and both are fixed when
    /// the engine is built.
    vix_anchor: f64,
    /// The index variance the LAST VIX update read, term by term.
    ///
    /// `None` until an `advance_day_with` has run under
    /// [`ModelParams::vix_level_identity`], and `None` for ever under
    /// every preset that leaves the identity off — because there the
    /// read-back is not computed at all and a zero here would be a
    /// variance of nothing rather than an absence.
    ///
    /// A REMEMBERED VALUE AND NOT A GETTER, and the difference is a day.
    /// `advance_day_with` computes this from post-close states at
    /// `VIX_{t-1}` — the sector sigma and the jump rate are read at the
    /// VIX the close saw — and then updates the VIX. Anything that
    /// recomputed the identity afterwards would read the instantaneous
    /// terms at `VIX_t` and hand back a variance no VIX update ever saw.
    ///
    /// Pure diagnostic: nothing in the engine reads it, and it is
    /// deliberately NOT in `state_snapshot`, so it moves no state hash and
    /// no digest. A FORK carries it, because a fork is a `Clone` and its
    /// last VIX update really was the parent's. `restore_state` does not,
    /// because the snapshot does not hold it: a restored engine keeps
    /// whatever reading it had of its own — `None` if it had advanced no
    /// day — until its own next day advances. That is the price of
    /// staying out of the hash, and it is stated rather than hidden.
    last_index_variance: Option<crate::market::index_var::IndexVarianceTerms>,
    /// The variance targets the last close reverted toward, `(fast, slow)`.
    /// Diagnostic only, like `last_index_variance`, and kept OUT of
    /// `state_snapshot` and the state hash for the same reason.
    last_market_targets: Option<(f64, Option<f64>)>,
    /// `rate_intraday_live`: the two projections of tonight's curve the rate
    /// indices' live mark differences, as (2-year, 10-year, corporate) in per
    /// cent: `[0..3]` on an empty session (the open's expectation), `[3..6]`
    /// on the session so far at the last refresh. Computed at the open and
    /// refreshed on the grid (`RATE_LIVE_REFRESH_MINUTES`); `None` outside a
    /// session, after a pin until the next tick, and always with the dial
    /// off. Carried in the snapshot and the state hash while the dial is set
    /// and this is `Some`, since a refresh reads the state at its own minute.
    rate_live: Option<[f64; 6]>,
    /// `index_level_listed`: the index's divisor, 0.0 until the first session
    /// opens, and its level on the last close's prints, 0.0 before the first
    /// close. Never touched with the switch off; carried in the snapshot and
    /// the state hash only while it is set. See `engine::derivatives`.
    index_divisor: f64,
    index_close: f64,
    /// `vix_intraday_live`: the projection of tonight's published VIX on the
    /// session so far, at the last refresh. `None` outside a session, after a
    /// pin until the next tick, and always with the switch off. Carried in the
    /// snapshot and the state hash while the switch is set and this is `Some`,
    /// since a refresh reads the state at its own minute.
    vix_live: Option<f64>,
    /// `forecast_horizon_sessions`: the forecast the last close computed.
    /// `None` before the first close and always with the dial off. Carried in
    /// the snapshot and the state hash while it is `Some`.
    forecast: Option<crate::derivatives::Forecast>,
    /// `futures_index_listed`: the index futures, their book, their
    /// settlements and their generator (`stream::DERIVATIVES`); the night's
    /// path under `night_session_steps`. Empty and never drawn from with the
    /// switch off; carried in the snapshot and the state hash only while it
    /// is set. See `engine::futures`.
    futures: futures::FuturesState,
    /// The VIX futures (`futures_vix_listed`): empty and never touched with
    /// the switch off; carried in the snapshot and the state hash only while
    /// it is set. See `engine::vix_futures`.
    vix_futures: vix_futures::VixFuturesState,
    /// The policy-rate and term-rate futures (`futures_rates_listed`): empty
    /// and never touched with the switch off; carried in the snapshot and the
    /// state hash only while it is set. See `engine::rate_futures`.
    rate_futures: rate_futures::RateFuturesState,
    /// The oil futures (`futures_oil_listed`): empty and never touched with
    /// the switch off; carried in the snapshot and the state hash only while
    /// it is set. See `engine::oil_futures`.
    oil_futures: oil_futures::OilFuturesState,
    /// The listed contracts' margin (`margin_scan_coverage`): empty and never
    /// touched with the dial off; carried in the snapshot and the state hash
    /// only while it is set. See `engine::margin`.
    margin: margin::MarginState,
    /// `fed_stress_cut`: the highest published VIX since the last meeting.
    /// 0.0 and never touched with the cut off; carried in the snapshot and
    /// the state hash only while it is set.
    stress_vix_max: f64,
    /// `fed_stress_hold`: sessions since the last close whose published VIX
    /// was at or over `fed_stress_vix`, [`STRESS_HOLD_NEVER`] before the
    /// first. Never touched with the dial off; carried in the snapshot and
    /// the state hash only while it is set.
    stress_hold_age: f64,
    /// `fed_drawdown_hold`: the close-to-close log return of total public
    /// market cap over the last [`DRAWDOWN_WINDOW`] sessions, oldest first,
    /// from which the meeting reads the index's fall from its highest close
    /// in the window. Empty and never touched with the dial off; carried in
    /// the snapshot and the state hash only while it is set.
    drawdown_returns: std::collections::VecDeque<f64>,
    /// `fed_drawdown_hold`: total public market cap at the last close, the
    /// base of the next return. 0.0 before the first close.
    drawdown_mcap_prev: f64,
    /// `treasury_path_pricing`: the market's forecast of the policy rate's
    /// further change, `M`, percentage points: each change decayed at
    /// `treasury_path_half_life` sessions. 0.0 and never touched with the
    /// dial off; carried in the snapshot and the state hash only while it is
    /// set.
    policy_path: f64,
    /// `policy_anticipation`: the share of the next meeting's expected change
    /// the curve prices tonight, `P`, percentage points, signed. 0.0 and
    /// never touched with the dial off; carried in the snapshot and the
    /// state hash only while it is set.
    policy_anticipation_priced: f64,
    /// The rate instruments, if the embedder listed any
    /// ([`Engine::set_rate_instruments`]). Empty on every engine built
    /// without them, and an empty book is never touched: no hook runs, no
    /// column or buffer grows and nothing extra is hashed, so such an engine
    /// is the engine it was before rate instruments existed. They take no
    /// draws and write nothing back to the economy, so adding them leaves
    /// every equity price bit-identical. See `crate::rates`.
    rates: crate::rates::RateBook,

    /// The agent-facing book's state: consumed depth, agents' waiting
    /// orders, their taker flow not yet applied, and fills and impact rows
    /// not yet collected. See `crate::agent_book`.
    ///
    /// Pristine on every engine no agent has sent an order through, and a
    /// pristine state takes no part in a tick, the state hash or the
    /// snapshot, so every such run is the one it was before this existed.
    book: crate::agent_book::BookState,

    /// The phases the economy held at the last `cycle_publication_lag + 1`
    /// closes, oldest first; the front is the phase published
    /// ([`Engine::published_cycle_phase`]). Seeded at construction with the
    /// opening phase, so until the lag's worth of sessions has closed the
    /// opening phase is what an observer reads. Empty, never touched,
    /// unsnapshotted and unhashed while the dial is 0.0, which every preset
    /// through pt-v19 carries, so such an engine is the engine it was
    /// before this existed.
    cycle_history: std::collections::VecDeque<crate::economy::CyclePhase>,

    /// The market's belief over the five phases in `phase_cycle` order
    /// (`cycle_nowcast_accuracy`). Zero, never touched, unsnapshotted and
    /// unhashed while the dial is 0.0.
    cycle_nowcast: [f64; 5],

    /// The nowcast's and the spread blend's constants, fixed for the
    /// engine's life because `params` is: each phase's exit rate `lambda_j =
    /// min(1, 1 / mean_sojourn_j)` and the multiplier's occupancy mean
    /// `m_bar`. Derived once at construction while `cycle_nowcast_accuracy`
    /// or `corporate_spread_cycle` is set (zeros otherwise), since the
    /// sojourn walk is too dear to run at every close.
    cycle_nowcast_terms: ([f64; 5], f64),

    /// The GDP growth figure as published under `gdp_publication_lag`, and
    /// what it is computed from: the quarter being averaged and the
    /// quarters averaged and awaiting release. Seeded at construction with
    /// the opening growth. Default, never touched, unsnapshotted and
    /// unhashed while the dial is 0.0, which every preset through pt-v19
    /// carries.
    gdp_publication: GdpPublication,

    /// `D`, the accumulated expected drift of the anticipated earnings level
    /// the valuation leaves out (`earnings_anticipation_drift_share`), and
    /// `A - e` as the last refresh left it, which the next close adds to
    /// `D`. Zero, never touched, unsnapshotted and unhashed while the share
    /// is 0.0, which every preset carries.
    anticipation_drift: f64,
    anticipation_raw: f64,

    /// The business cycle's publication schedule under
    /// `cycle_publication_lag_draw`. Default, never touched, unsnapshotted
    /// and unhashed while the switch is 0.0, which every preset carries;
    /// only its key is set at construction.
    cycle_publication: CyclePublication,
    /// The key of the oil long factor's draws (`oil_target_drift_sd`),
    /// derived from the root seed and carried by a snapshot that holds the
    /// factor, so a restore onto an engine built from another seed draws
    /// the run's own.
    oil_drift_key: u64,
    /// The log of the oil price's long-term level (`oil_target_drift_sd`):
    /// 0.0 with the dial off, which it never leaves.
    oil_target_drift: f64,

    /// The background traders sharing the agent-facing book, when the
    /// engine was built with them (`crate::population`). `None` on every
    /// engine built without one, which then never calls into that module,
    /// so its market is the market it was before populations existed.
    population: Option<Box<crate::population::PopulationRun>>,
}

/// The state behind the published cycle phase under
/// `cycle_publication_lag_draw`: each true turn is published at its own
/// close, `pi_k = max(pi_(k-1), tau_k + L_k)`, `L_k` a stateless draw on the
/// turn's index (`rng::publication_uniform`).
///
/// Closes are counted from the opening (the construction, or a seeding),
/// and a turn is a close whose true phase differs from the last close's.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct CyclePublication {
    /// The key of the draws, from the root seed (`rng::publication_key`).
    pub key: u64,
    /// The phase an observer reads: the latest turn published, or the
    /// opening phase before the first.
    pub published: crate::economy::CyclePhase,
    /// The true phase at the last close (or the opening), against which the
    /// next close's phase is a turn.
    pub last_true: crate::economy::CyclePhase,
    /// Closes since the opening.
    pub closes: i64,
    /// True turns since the opening: the next turn's draw index.
    pub turns: u64,
    /// Turns not yet published, oldest first: the close each is published
    /// on (non-decreasing), and the phase it turned into.
    pub pending: std::collections::VecDeque<(i64, crate::economy::CyclePhase)>,
}

impl Default for CyclePublication {
    fn default() -> Self {
        CyclePublication {
            key: 0,
            published: crate::economy::CyclePhase::Expansion,
            last_true: crate::economy::CyclePhase::Expansion,
            closes: 0,
            turns: 0,
            pending: std::collections::VecDeque::new(),
        }
    }
}

/// The publication lag of a turn into `phase` under
/// `cycle_publication_lag_draw`, from its uniform `u` in [0, 1): whole
/// sessions uniform in [84, 252] into a peak or a contraction (the NBER's
/// peak announcements, 4 to 12 months) and [168, 441] into a trough, a
/// recovery or an expansion (its trough announcements, 8 to 21 months).
pub fn cycle_publication_lag_of(phase: crate::economy::CyclePhase, u: f64) -> i64 {
    use crate::economy::CyclePhase;
    let (lo, hi) = match phase {
        CyclePhase::Peak | CyclePhase::Contraction => (84i64, 252i64),
        CyclePhase::Trough | CyclePhase::Recovery | CyclePhase::Expansion => (168i64, 441i64),
    };
    let span = (hi - lo + 1) as f64;
    let step = (u * span).floor() as i64;
    lo + if step > hi - lo { hi - lo } else if step < 0 { 0 } else { step }
}

/// The state behind the published GDP growth figure
/// (`gdp_publication_lag`), in the economy's percent.
///
/// Quarters are the macro calendar's own ([`crate::economy::MacroCalendar::days_per_quarter`]):
/// day `d` belongs to quarter `d.div_euclid(q)`, the quarter whose first
/// close takes the quarterly GDP step in `economy::daily`, and day 0, the
/// opening, belongs to quarter 0. A quarter's figure is the mean of the true
/// growth after each of its closes (the opening's value on day 0), and it is
/// released on the close `lag` sessions after the quarter's last day.
#[derive(Debug, Clone, Default, PartialEq)]
#[non_exhaustive]
pub struct GdpPublication {
    /// The figure an observer reads: the last quarter released, or the
    /// opening growth before the first release.
    pub published: f64,
    /// The quarter being averaged.
    pub quarter: i64,
    /// Closes averaged so far in that quarter.
    pub count: u32,
    /// Their growth, summed.
    pub sum: f64,
    /// Quarters averaged and not yet released, oldest first: the close on
    /// which each is released, and its figure.
    pub pending: std::collections::VecDeque<(i64, f64)>,
}

impl Engine {
    /// Derive the three engine streams from the root seed and take
    /// ownership of the state.
    ///
    /// # Both orderings are CONTRACTUAL
    ///
    /// `companies` is an ordered SEQUENCE, never a set or a mapping. The tick
    /// walks it in index order and draws per company as it goes, so reordering
    /// the roster produces a different market from the same seed. A
    /// well-meaning `sort_by(|a, b| a.ticker.cmp(&b.ticker))` anywhere upstream
    /// is a silent, total divergence — nothing will fail, the numbers will just
    /// be different ones.
    ///
    /// `sector_keys` must likewise be in `Object.keys(SECTOR_CONFIGS)` order:
    /// one normal is drawn per key, per tick, in that order.
    ///
    /// Roster SIZE is contractual too, and for the same reason. Adding a
    /// company does not append a name to an otherwise-unchanged market — draws
    /// scale with `n`, so every subsequent draw shifts and the whole market
    /// changes. A 30-name universe and a 100-name universe from one seed have
    /// nothing to do with each other.
    ///
    /// # The economy you pass is a starting point, not the opening
    ///
    /// This runs the default preset's opening over `economy` before day 1,
    /// as [`Engine::with_params`] does: on `pt-v20` a 755-day macro burn-in
    /// (`macro_burn_in_days`) and a draw of the cycle's phase and age
    /// (`cycle_stationary_opening`). A VIX, a policy rate or a phase you
    /// set is relaxed away. To keep the economy you pass, build with
    /// [`Engine::with_params_keeping_opening`].
    pub fn new(
        seed: u64,
        companies: Vec<TickCompany>,
        economy: EconomyState,
        central_bank: CentralBankState,
        sector_keys: Vec<String>,
    ) -> Self {
        Self::with_params(
            seed,
            companies,
            economy,
            central_bank,
            sector_keys,
            Self::default_model(),
        )
    }

    /// Advance the universe's remembered stress by one day.
    ///
    /// A ratchet with decay: today's stress enters immediately and in full,
    /// and what is already remembered decays geometrically. Asymmetric on
    /// purpose, because correlation is — it spikes with the shock and
    /// unwinds over weeks, rather than lagging the shock on the way in.
    ///
    /// `universe_stress_decay` is a per-day survival fraction, so 0.97 is a
    /// 23-day half-life and 0.0 means nothing survives the night, which is
    /// the behaviour of every preset before pt-v4.
    fn update_universe_stress(&mut self) {
        let threshold = self.params.crisis_vix_threshold;
        let from_vix = if self.economy.vix > threshold {
            self.economy.vix - threshold
        } else {
            0.0
        };
        // THE CYCLE, WHICH THE MARKET HAS NEVER READ. The engine runs a
        // five-phase business cycle and `cycle_phase` appears nowhere in
        // src/market/: the central bank changes its entire policy by
        // phase while the price process behaves as though the economy
        // were always expanding. A contraction now reaches the market the
        // same way a VIX spike does, and is remembered the same way.
        let from_regime = self.params.regime_stress_points
            * self.economy.cycle_phase.stress_intensity();
        let instant = crate::mathx::max(from_vix, from_regime);
        let remembered = self.params.universe_stress_decay * self.universe_stress;
        self.universe_stress = crate::mathx::max(instant, remembered);
    }

    /// The universe's remembered stress, for checkpoints and for anyone
    /// asking what the market is still carrying.
    pub fn universe_stress(&self) -> f64 {
        self.universe_stress
    }

    /// The common log-volume state, for checkpoints and forks.
    ///
    /// An AR(1) the whole roster multiplies its volume by. It was missing
    /// from the snapshot until 2026-08-26, which was invisible while the
    /// mechanism shipped switched off and became a divergence the day pt-v10
    /// turned it on: a restored engine traded different volume, which walks
    /// the book differently and prints different prices (§74).
    pub fn volume_state(&self) -> f64 {
        self.volume_state
    }

    /// Put the volume state back. See [`Engine::volume_state`].
    pub fn set_volume_state(&mut self, state: f64) {
        self.volume_state = state;
    }

    /// Put the remembered stress back. See [`Engine::universe_stress`].
    pub fn set_universe_stress(&mut self, stress: f64) {
        self.universe_stress = stress;
    }

    /// The per-name volume states, for checkpoints and forks.
    ///
    /// One AR(1) per company, walked at every tick. Inert under every preset
    /// through pt-v15 (`volume_idio_sigma` is 0.0), which is exactly the
    /// position [`Engine::volume_state`] was in before pt-v10 turned it on
    /// and a restored engine started trading different volume. Carried now,
    /// while carrying it is free.
    pub fn volume_idio(&self) -> &[f64] {
        &self.volume_idio
    }

    /// Put the per-name volume states back. See [`Engine::volume_idio`].
    ///
    /// # A width mismatch is refused rather than resized
    ///
    /// A snapshot written before issue #148 was fixed on 2026-09-02 holds
    /// this array at the width the engine was CONSTRUCTED with, because
    /// `add_company` and `remove_company` left it alone. Any such snapshot
    /// from a run that listed or delisted an instrument therefore carries a
    /// width its own roster disagrees with, and it fails here.
    ///
    /// Resizing it silently would be worse than the failure. The array is
    /// positional against the roster, so a pad or a truncation attaches each
    /// state to whichever company now sits at that index, and the restored
    /// market continues plausibly under states belonging to other names.
    /// The caller is told which two numbers disagree and where the mismatch
    /// came from.
    ///
    /// # Nothing stands when this refuses
    ///
    /// `PyEngine::restore_state` writes into a copy of the engine and keeps
    /// it only once every field has been read, so an engine that has caught
    /// this error holds the state it held before the call.
    /// `tests/test_volume_idio_roster.py` asserts that.
    pub fn set_volume_idio(&mut self, values: &[f64]) -> Result<(), String> {
        if values.len() != self.volume_idio.len() {
            return Err(format!(
                "this snapshot carries {} per-name volume states and the \
                 roster holds {} companies. A snapshot written before issue \
                 #148 was fixed records the array at the width the engine \
                 was constructed with, so a run that listed or delisted an \
                 instrument saved a width its own roster disagrees with. \
                 The states are positional against the roster, so this \
                 restore is refused rather than padded or truncated. \
                 Reproduce the run from its seed and its order log to get a \
                 snapshot at the roster's width.",
                values.len(),
                self.volume_idio.len(),
            ));
        }
        self.volume_idio.copy_from_slice(values);
        Ok(())
    }

    /// The per-SECTOR variance state, for checkpoints and forks.
    ///
    /// A GARCH(1,1) on each sector factor, standardised by its own target, so
    /// its unconditional mean is exactly 1.0 and it multiplies the sector's
    /// variance. Zero-length before the sector table existed, and exactly
    /// 0.0 on every preset through pt-v18, where `sector_vol_alpha` and
    /// `sector_vol_beta` are both 0.0. pt-v19 turns it on, and until this
    /// was carried a restored engine continued the run with every sector
    /// back at its target while the state hash said the two markets were
    /// the same one.
    pub fn sector_variance(&self) -> &[f64] {
        &self.sector_variance
    }

    /// Put the per-sector variance states back. See
    /// [`Engine::sector_variance`].
    ///
    /// A width mismatch is refused rather than resized, for the reason
    /// [`Engine::set_volume_idio`] gives at length: the array is positional
    /// against the sector table, so a pad or a truncation attaches each
    /// state to whichever sector now sits at that index.
    pub fn set_sector_variance(&mut self, values: &[f64]) -> Result<(), String> {
        if values.len() != self.sector_variance.len() {
            return Err(format!(
                "this snapshot carries {} per-sector variance states and                  this engine's sector table holds {}. The states are                  positional against that table, so the restore is refused                  rather than padded or truncated.",
                values.len(),
                self.sector_variance.len(),
            ));
        }
        self.sector_variance.copy_from_slice(values);
        Ok(())
    }

    /// The day's accumulated per-sector factor, for checkpoints and forks.
    ///
    /// Zero at a day boundary and non-zero between the open and the close,
    /// so it matters exactly where `attribution` and `tick_components`
    /// matter: a fork taken mid-day. See [`Engine::sector_target_day`] for
    /// its partner.
    pub fn sector_day_factor(&self) -> &[f64] {
        &self.sector_day_factor
    }

    /// The target variance the day's sector draws were scaled by.
    ///
    /// Recorded in the tick loop so the close standardises the day's factor
    /// by the scale it was DRAWN at rather than by the target after the
    /// close moved the VIX, which makes it per-day state and not a
    /// derivable constant. 0.0 until the first tick of a day.
    pub fn sector_target_day(&self) -> f64 {
        self.sector_target_day
    }

    /// Put the sector day accumulators back. Width refused as elsewhere.
    pub fn set_sector_day(&mut self, factor: &[f64], target: f64) -> Result<(), String> {
        if factor.len() != self.sector_day_factor.len() {
            return Err(format!(
                "this snapshot carries {} per-sector day factors and this                  engine's sector table holds {}. The accumulators are                  positional against that table, so the restore is refused                  rather than padded or truncated.",
                factor.len(),
                self.sector_day_factor.len(),
            ));
        }
        self.sector_day_factor.copy_from_slice(factor);
        self.sector_target_day = target;
        Ok(())
    }

    /// The per-NAME jump excitation state, for checkpoints and forks.
    ///
    /// A Hawkes-style self-excitation: a name that jumped carries a raised
    /// intensity that decays over the following sessions. 0.0 on every
    /// preset through pt-v18, where `jump_idio_excitation` is 0.0, and live
    /// on pt-v19. Carried for the reason every state beside it is: a
    /// checkpoint that dropped it resumed a market where nothing had ever
    /// jumped.
    pub fn jump_excitation(&self) -> &[f64] {
        &self.jump_excitation
    }

    /// Each name's log fair-value level `v`, in roster order, 0.0 where
    /// nothing has moved it. See `ModelParams::fair_value_news_share`. For
    /// checkpoints and forks; every preset through pt-v19 holds zeros.
    pub fn fair_value_offsets(&self) -> Vec<f64> {
        self.companies.iter().map(|c| c.stock.fair_value_offset.unwrap_or(0.0)).collect()
    }

    /// Put the fair-value levels back. A width mismatch is refused, as the
    /// jump excitation's is: the levels are positional against the roster.
    pub fn set_fair_value_offsets(&mut self, values: &[f64]) -> Result<(), String> {
        if values.len() != self.companies.len() {
            return Err(format!(
                "this snapshot carries {} fair-value levels and the roster holds {} \
                 companies. The levels are positional against the roster, so this \
                 restore is refused rather than padded or truncated.",
                values.len(),
                self.companies.len()
            ));
        }
        for (c, &v) in self.companies.iter_mut().zip(values) {
            c.stock.fair_value_offset = if v == 0.0 { None } else { Some(v) };
        }
        Ok(())
    }

    /// The opening draws not yet applied: one standard normal per name of
    /// the roster the engine was built with and one for the market's common
    /// level, until the first open that is not closed takes them, and empty
    /// after it. For checkpoints and forks. `state_hash` has covered them
    /// since pt-v20 composed, and a snapshot that dropped them rebuilt a
    /// pre-open engine that opened at other draws and hashed apart.
    pub fn opening_z(&self) -> &[f64] {
        &self.opening_z
    }

    /// Put the unapplied opening draws back. Empty is a legal state (the
    /// opening has happened), and any other length must be the roster's
    /// plus one, the length the engine drew.
    pub fn set_opening_z(&mut self, values: &[f64]) -> Result<(), String> {
        if !values.is_empty() && values.len() != self.companies.len() + 1 {
            return Err(format!(
                "this snapshot carries {} opening draws and the roster holds {} \
                 companies, which draws {}. The draws are positional against the \
                 roster, so this restore is refused rather than padded or truncated.",
                values.len(),
                self.companies.len(),
                self.companies.len() + 1
            ));
        }
        self.opening_z = values.to_vec();
        Ok(())
    }

    /// The prehistory's carried opening mispricing not yet applied
    /// (`market_prehistory_valuation`): one per name of the roster, NaN for
    /// a name that takes its draw, until the first open that is not closed
    /// takes them, and empty after it or with the dial off. For checkpoints
    /// and forks, beside [`Self::opening_z`].
    pub fn opening_carry(&self) -> &[f64] {
        &self.opening_carry
    }

    /// Put the unapplied carried opening back. Empty is a legal state; any
    /// other length must be the roster's.
    pub fn set_opening_carry(&mut self, values: &[f64]) -> Result<(), String> {
        if !values.is_empty() && values.len() != self.companies.len() {
            return Err(format!(
                "this snapshot carries {} opening mispricings and the roster holds {} \
                 companies. They are positional against the roster, so this restore \
                 is refused rather than padded or truncated.",
                values.len(),
                self.companies.len()
            ));
        }
        self.opening_carry = values.to_vec();
        Ok(())
    }

    /// Whether this engine runs the per-name idiosyncratic variance state
    /// (`ModelParams::idio_vol_alpha`), which is when the snapshot and the
    /// state hash carry it. Off on every preset, whose two dials are 0.0.
    pub fn carries_idio_vol_state(&self) -> bool {
        self.idio_state_on()
    }

    /// The per-name idiosyncratic variance state, for checkpoints and forks:
    /// the ratio (0.0 unseeded), the own jump the last close applied and
    /// the jump variance expected at the rate it was drawn, each in roster
    /// order. Between sessions this is the whole state: tonight's pair is
    /// moved into the pending one at the close and is empty until the next.
    pub fn idio_vol_state(&self) -> (&[f64], &[f64], &[f64]) {
        (&self.idio_variance, &self.idio_jump_pending, &self.idio_jump_var_pending)
    }

    /// Put the per-name idiosyncratic variance state back. See
    /// [`Engine::idio_vol_state`]. Each vector must be the roster's width:
    /// the states are positional, so a mismatch is refused rather than
    /// padded or truncated.
    pub fn set_idio_vol_state(
        &mut self,
        variance: &[f64],
        jump: &[f64],
        jump_var: &[f64],
    ) -> Result<(), String> {
        let n = self.companies.len();
        for (name, v) in [("ratios", variance), ("pending own jumps", jump),
                          ("pending jump variances", jump_var)] {
            if v.len() != n {
                return Err(format!(
                    "this snapshot carries {} idiosyncratic variance {} and the roster \
                     holds {} companies. The states are positional against the roster, \
                     so this restore is refused rather than padded or truncated.",
                    v.len(), name, n));
            }
        }
        self.idio_variance = variance.to_vec();
        self.idio_jump_pending = jump.to_vec();
        self.idio_jump_var_pending = jump_var.to_vec();
        self.idio_jump_today = vec![0.0; n];
        self.idio_jump_var_today = vec![0.0; n];
        Ok(())
    }

    /// Whether this engine's model carries the volatility feedback's
    /// smoothed exposure, which is when the snapshot and the state hash
    /// carry it. Off on every preset through pt-v19; on for pt-v20.
    pub fn carries_vix_feedback(&self) -> bool {
        self.params.fair_value_vix_discount != 0.0 && self.params.fair_value_vix_half_life != 0.0
    }

    /// Whether this engine's model carries the market factor's return
    /// memory, which is when the snapshot and the state hash carry it: only
    /// with `market_vol_leverage` set. Off on every shipped preset.
    pub fn carries_market_vol_leverage(&self) -> bool {
        self.params.market_vol_leverage != 0.0
    }

    /// The market factor's return memory. See
    /// [`crate::params::ModelParams::market_vol_leverage`].
    pub fn market_vol_leverage_memory(&self) -> f64 {
        self.market_vol.leverage_memory()
    }

    /// Put the market factor's return memory back. Call after
    /// [`Engine::set_market_variance_state_with_components`], which resets
    /// it to 0.0.
    pub fn set_market_vol_leverage_memory(&mut self, value: f64) {
        self.market_vol.set_leverage_memory(value);
    }

    /// Whether this engine's model carries the Fed put's state (the
    /// intermeeting return, the put's stock, what it owes and the previous
    /// close's market cap), which is when the snapshot and the state hash
    /// carry it: with `fed_put_gain` non-zero. Off on every preset.
    pub fn carries_fed_put(&self) -> bool {
        self.params.fed_put_gain != 0.0
    }

    /// Whether a meeting tonight holds any rise: within `fed_stress_hold`
    /// sessions of a stressed close, or, under `fed_drawdown_hold`, with
    /// credit's leverage gap (the index's log fall below its slow average)
    /// at or above that dial. False with both off.
    fn stress_hold_now(&self) -> bool {
        (self.params.fed_stress_hold != 0.0 && self.stress_hold_age < self.params.fed_stress_hold)
            || (self.params.fed_drawdown_hold != 0.0
                && self.index_drawdown() >= self.params.fed_drawdown_hold)
    }

    /// The index's log fall from its highest close of the last
    /// [`DRAWDOWN_WINDOW`] sessions (`fed_drawdown_hold`), read on total
    /// public market cap: 0.0 at a new high, and 0.0 with the dial off.
    pub fn index_drawdown(&self) -> f64 {
        let (mut level, mut high) = (0.0, 0.0);
        for r in self.drawdown_returns.iter() {
            level += r;
            high = crate::mathx::max(high, level);
        }
        high - level
    }

    /// Whether this engine keeps the drawdown's window of returns (and so
    /// the snapshot and both state hashes carry it): with
    /// `fed_drawdown_hold` or `market_vol_cycle_recovery_release` set. Off
    /// on every preset.
    pub fn keeps_drawdown_window(&self) -> bool {
        self.params.fed_drawdown_hold != 0.0 || self.params.market_vol_cycle_recovery_release != 0.0
    }

    /// Book tonight's close into the drawdown's window
    /// (`fed_drawdown_hold`): the log change of total public market cap
    /// since the last close, the oldest session dropped once the window is
    /// full. The first close only records its base. Nothing with the dial
    /// off.
    fn book_drawdown_close(&mut self) {
        if !self.keeps_drawdown_window() {
            return;
        }
        let mut mcap = 0.0;
        for c in self.companies.iter() {
            if c.is_public && !c.is_bankrupt {
                mcap += c.stock.market_cap;
            }
        }
        if self.drawdown_mcap_prev > 0.0 && mcap > 0.0 {
            self.drawdown_returns.push_back(crate::mathx::log(mcap / self.drawdown_mcap_prev));
            while self.drawdown_returns.len() > DRAWDOWN_WINDOW {
                self.drawdown_returns.pop_front();
            }
        }
        self.drawdown_mcap_prev = mcap;
    }

    /// Whether this engine's model carries credit's leverage gap
    /// (`EconomyState::spread_equity_gap`), which is when the snapshot and
    /// the state hash carry it: `corporate_spread_equity_gain` or
    /// `cycle_equity_hazard` set. Off on every preset.
    pub fn carries_spread_equity_gap(&self) -> bool {
        self.params.corporate_spread_equity_gain != 0.0 || self.params.cycle_equity_hazard != 0.0
    }

    /// The Fed put's cut the curve prices tonight (`treasury_put_pricing`),
    /// percentage points: the share of the cut the put would ask for at a
    /// meeting now, no more than the policy rate, and 0.0 with inflation at
    /// or above the put's ceiling. 0.0 unless both dials are set.
    fn priced_fed_put(&self) -> f64 {
        let p = &self.params;
        if p.fed_put_gain == 0.0
            || p.treasury_put_pricing == 0.0
            || self.economy.inflation_rate >= crate::economy::central_bank::FED_PUT_INFLATION_CEILING
        {
            return 0.0;
        }
        let asked = crate::economy::central_bank::fed_put_ask(
            p.fed_put_gain, p.fed_put_threshold, self.economy.intermeeting_return);
        p.treasury_put_pricing
            * crate::mathx::min(asked, crate::mathx::max(0.0, self.economy.federal_funds_rate))
    }

    /// Whether this engine's model can move a fair-value level, which is
    /// when the snapshot and the state hash carry them. Off on every preset
    /// through pt-v19, so their snapshots and hashes are the ones they were.
    pub fn carries_fair_value_offsets(&self) -> bool {
        self.params.fair_value_news_share != 0.0
            || self.params.fair_value_market_share != 0.0
            || self.params.opening_mispricing_sigma != 0.0
            || self.params.opening_market_sigma != 0.0
            || self.params.earnings_surprise_sigma != 0.0
    }

    /// Whether this engine's model runs the variance cascade
    /// (`garch_cascade_components` at 1 or more), which is when the snapshot
    /// and the state hash carry its components. Off on every shipped
    /// preset, so their snapshots and hashes are the ones they were.
    pub fn carries_garch_cascade(&self) -> bool {
        self.params.garch_cascade_components >= 1.0
    }

    /// Every company's cascade components, `CASCADE_MAX` per name in roster
    /// order. For checkpoints and forks: the close updates them from the
    /// day's return, so a restore that dropped them ran every name's
    /// variance off another level (1.4 per cent in log price after twenty
    /// days at three components).
    pub fn garch_cascade(&self) -> Vec<f64> {
        self.companies
            .iter()
            .flat_map(|c| c.stock.garch_cascade.iter().copied())
            .collect()
    }

    /// Put the cascade components back, `CASCADE_MAX` per name. A width
    /// mismatch is refused, as the fair-value levels' is: the components
    /// are positional against the roster.
    pub fn set_garch_cascade(&mut self, values: &[f64]) -> Result<(), String> {
        let width = crate::market::garch::CASCADE_MAX;
        if values.len() != self.companies.len() * width {
            return Err(format!(
                "this snapshot carries {} variance cascade components and the \
                 roster holds {} companies of {width} each. The components are \
                 positional against the roster, so this restore is refused \
                 rather than padded or truncated.",
                values.len(),
                self.companies.len()
            ));
        }
        if let Some(v) = values.iter().find(|v| !v.is_finite()) {
            return Err(format!(
                "this snapshot's variance cascade holds {v}. A component is a \
                 variance, and a non-finite one would carry into every price \
                 its name prints."
            ));
        }
        for (c, chunk) in self.companies.iter_mut().zip(values.chunks_exact(width)) {
            c.stock.garch_cascade.copy_from_slice(chunk);
        }
        Ok(())
    }

    /// Put the per-name jump excitation back. See
    /// [`Engine::jump_excitation`]. A width mismatch is refused, on the
    /// reasoning in [`Engine::set_volume_idio`], and this write sits AFTER
    /// that one in `restore_state`, so the boundary that docstring
    /// describes is unchanged.
    pub fn set_jump_excitation(&mut self, values: &[f64]) -> Result<(), String> {
        if values.len() != self.jump_excitation.len() {
            return Err(format!(
                "this snapshot carries {} per-name jump excitation states                  and the roster holds {} companies. The states are                  positional against the roster, so this restore is refused                  rather than padded or truncated.",
                values.len(),
                self.jump_excitation.len(),
            ));
        }
        self.jump_excitation.copy_from_slice(values);
        Ok(())
    }

    /// The day's endogenous news, for checkpoints and forks.
    ///
    /// Generated once in `open_market` and read by every tick of that day, so
    /// it is per-DAY state and not a per-tick input. A fork taken mid-day
    /// without it runs the rest of the day with the news missing and prices
    /// differently from the engine it forked from -- which is what happened,
    /// on the shipped default preset, until this was carried.
    pub fn session_news(&self) -> &[NewsEvent] {
        &self.session_news
    }

    /// Put the day's endogenous news back. See [`Engine::session_news`].
    pub fn set_session_news(&mut self, news: Vec<NewsEvent>) {
        self.session_news = news;
    }

    /// The preset an engine gets when the caller names none.
    ///
    /// One definition, because the alternative is what this replaced: the
    /// default written as a bare `PT_V1` at two call sites, where moving an
    /// era means finding both.
    ///
    /// Since 0.10.0 this is [`PT_V21`]: pt-v20 with 104 dials moved, each
    /// inert on every earlier preset (see [`crate::params::ModelParams::pt_v21`]).
    /// It holds all fifteen rows of the fixed-roster panel at 252 days,
    /// fifteen of fifteen at 504, fifteen on both held-out axes, and all
    /// 40 long-run criteria, read on 270 histories
    /// (`validation/pt-v21/programme/ptv21-registration-18.md`). Volatility
    /// clustering at lag 1 reads 0.095 against a real median of 0.103, and
    /// the index falls 3 per cent or more on 0.98 per cent of days over 360
    /// seeds, inside the ruled band of 0.64 to 2.34. Its gaps: the crisis
    /// lever is 4.98x against a real 6.16x, and the VIX's persistence reads
    /// 0.956 against the tape's 0.930, which the structural sign test
    /// refuses.
    ///
    /// From 0.8.5 to 0.9.1 it was [`PT_V20`]: pt-v19 with a tape that follows the
    /// model price, a closing cross, the stock- and sector-specific part of
    /// every shock in fair value, the agent-facing book on, the curve dials
    /// and the aggregate earnings cycle. It holds all fifteen rows of the
    /// fixed-roster panel at 252 days, fifteen of fifteen at 504, fifteen
    /// on both held-out axes, and all 28 long-run criteria registered for
    /// it (`validation/pt-v20/programme/ptv20-registration.md`). It reads further
    /// from real on two rows: the crisis lever is 3.60x against a real
    /// 6.16x (pt-v19 5.22x), and on the level protocol the index returns
    /// +1.14 per cent a year, inside the ruled band of 1.1 to 10.3 at its
    /// floor (pt-v19 +7.65).
    ///
    /// In 0.8.0 and 0.8.1 it was [`PT_V19`]: pt-v18 with four dials moved and
    /// nothing else. It holds all fourteen shape rows at 252 and 504 days
    /// and on both held-out axes, as pt-v18 did, and every one of the
    /// fourteen at the real centre where pt-v18 held twelve. On the level
    /// protocol the index level returns +6.52 per cent a year against a
    /// band of 2.90 to 11.90 (pt-v18: +5.80), and the -3 per cent fear row
    /// reads 5.82 against a tape centre of 5.73, where pt-v18 read 3.25
    /// and sat a tenth of the way into its band. Nine of the ten graded
    /// mechanisms are shown, as on pt-v18.
    ///
    /// It reads FURTHER from real on one row: the crisis lever is 5.28x
    /// against real markets' 6.16x, where pt-v18 read 6.53x -- 14 per cent
    /// under real where pt-v18 was 6 per cent over. The VIX level identity
    /// reads the market's variance target against a derived anchor rather
    /// than the dial's, so a held VIX 65 is a smaller multiple of it.
    ///
    /// This constant and [`crate::params::DEFAULT_PRESET_NAME`] are the two
    /// things that decide the default, and a test at the bottom of
    /// `params.rs` asserts they agree. Moving one alone changes what the
    /// library REPORTS while every engine keeps running the other, which is
    /// the substitution that shipped once already: `model_preset()` answered
    /// "pt-v1" for runs executing pt-v3.
    ///
    /// Every earlier preset stays selectable and bit-reproducing, so
    /// anything recorded under one replays exactly by naming it.
    ///
    /// [`PT_V19`]: crate::params::PT_V19
    /// [`PT_V20`]: crate::params::PT_V20
    /// [`PT_V21`]: crate::params::PT_V21
    pub const fn default_model() -> crate::params::ModelParams {
        crate::params::PT_V21
    }

    /// [`Engine::new`] under an explicit model preset (the runtime seam).
    /// With [`crate::params::PT_V1`] this IS `new`: the
    /// preset-constructed engine reproduces the const build's trajectories
    /// bit for bit, draw for draw — the phase-1 acceptance gate.
    ///
    /// # It settles the opening, overwriting the economy you pass
    ///
    /// Before day 1 this runs the preset's own opening over `economy`:
    /// `macro_burn_in_days` days of the macro step (755 on `pt-v18` onward)
    /// and, where `cycle_stationary_opening` is on, a draw of the cycle's
    /// phase and age. Every macro field you set comes out as the model's
    /// dynamics left it. Measured on `pt-v20` (the test
    /// `with_params_settles_a_host_economy_and_keeping_opening_keeps_it`),
    /// an economy passed in at a VIX of 45 in a contraction opens at a VIX
    /// of 22.28 in an expansion. Presets before `pt-v18` have no burn-in, so
    /// this arrived in 0.7.0 under callers who had not changed their code.
    ///
    /// That is what you want when `economy` is
    /// [`crate::economy::create_initial_economy_state`]'s default, which
    /// would otherwise open every run in expansion at phase age zero. A
    /// host that owns the macro state, or restores one it saved, wants
    /// [`Engine::with_params_keeping_opening`] instead.
    /// [`Engine::opening_settled`] says afterwards which of the two an
    /// engine got.
    pub fn with_params(
        seed: u64,
        companies: Vec<TickCompany>,
        economy: EconomyState,
        central_bank: CentralBankState,
        sector_keys: Vec<String>,
        params: ModelParams,
    ) -> Self {
        Self::with_params_from_opening(seed, companies, economy, central_bank,
                                       sector_keys, params, true)
    }

    /// [`Engine::with_params`] when the economy you pass is the opening: the
    /// constructor for a host that owns the macro state.
    ///
    /// No burn-in runs and no cycle phase is drawn, so the engine opens on
    /// exactly the `economy` and `central_bank` given, and
    /// [`Engine::opening_settled`] reads `false`. The Python package does
    /// the same whenever a caller passes `macro_state`. It is
    /// [`Engine::with_params_from_opening`] with `settle_opening` false,
    /// under a name that says what it keeps.
    ///
    /// Use it when a host supplies its own starting economy, and when it
    /// rebuilds an engine from saved state. Use [`Engine::with_params`]
    /// when the economy is the library's default and the model should
    /// settle it.
    pub fn with_params_keeping_opening(
        seed: u64,
        companies: Vec<TickCompany>,
        economy: EconomyState,
        central_bank: CentralBankState,
        sector_keys: Vec<String>,
        params: ModelParams,
    ) -> Self {
        Self::with_params_from_opening(seed, companies, economy, central_bank,
                                       sector_keys, params, false)
    }

    /// Whether construction ran the model's own opening over the economy it
    /// was given.
    ///
    /// `true` when the engine was built with [`Engine::new`] or
    /// [`Engine::with_params`] (or `settle_opening` true) under a preset
    /// whose `macro_burn_in_days` or `cycle_stationary_opening` is non-zero,
    /// which is every preset from `pt-v18` on. Then the economy the engine
    /// opened on is not the one passed in. `false` when the opening was
    /// kept, or when the preset has nothing to settle.
    ///
    /// A host that supplies its own economy can assert on this once after
    /// construction, so a constructor swapped in a refactor fails loudly
    /// rather than quietly replacing the host's opening.
    pub fn opening_settled(&self) -> bool {
        self.opening_settled
    }

    /// [`Engine::with_params`], saying whether the opening is the model's to
    /// settle or the caller's to keep.
    /// [`Engine::with_params_keeping_opening`] is the `false` case by name.
    ///
    /// `settle_opening` is true for `with_params` and every path that takes
    /// the DEFAULT macro state, which is what `macro_burn_in_days` exists to
    /// fix: every run otherwise opens in expansion at phase age zero with the
    /// constructor's own field values, and the burn-in relaxes those into
    /// something a run can start from.
    ///
    /// It is FALSE when the caller supplied a macro state, because then the
    /// opening is a statement rather than an artefact. Measured at 0.7.0,
    /// when the default gained the dial: an engine asked for a VIX of 45.0
    /// and a policy rate of 5 per cent opened at 21.55 and 0.00 -- the
    /// burn-in had relaxed the request away over 755 days, and
    /// `Macro`'s own round-trip contract, that a value read back can be
    /// written straight in, was silently false.
    ///
    /// Settling and then restoring the named fields was considered and
    /// refused: the variance state the burn-in leaves behind tracks the VIX
    /// path it actually ran, so writing a crisis VIX back on top of it
    /// produces an engine whose volatility state and VIX disagree. Skipping
    /// is what a caller naming an opening asked for, and it is what every
    /// preset before pt-v18 did.
    ///
    /// # A prehistory is played once a process
    ///
    /// A build that plays a market prehistory (`market_prehistory_sessions`
    /// above zero with the opening settled, which is every `pt-v21` build
    /// that takes the default economy) is kept in a small process-wide
    /// cache, and a later build from the same arguments, to the bit, is a
    /// clone of it rather than a second prehistory. It cannot move a result:
    /// see [`Engine::opening_cache_info`] and
    /// [`Engine::set_opening_cache_capacity`].
    pub fn with_params_from_opening(
        seed: u64,
        companies: Vec<TickCompany>,
        economy: EconomyState,
        central_bank: CentralBankState,
        sector_keys: Vec<String>,
        params: ModelParams,
        settle_opening: bool,
    ) -> Self {
        if !(settle_opening && params.market_prehistory_sessions > 0.0) {
            return Self::build_from_opening(seed, companies, economy, central_bank,
                                            sector_keys, params, settle_opening);
        }
        let key = if crate::opening_cache::on() {
            crate::opening_cache::key(seed, &companies, &economy, &central_bank,
                                      &sector_keys, &params, settle_opening)
        } else {
            None
        };
        if let Some(engine) = key.as_ref().and_then(crate::opening_cache::get) {
            return engine;
        }
        let engine = Self::build_from_opening(seed, companies, economy, central_bank,
                                              sector_keys, params, settle_opening);
        if let Some(key) = key {
            crate::opening_cache::put(key, &engine);
        }
        engine
    }

    /// Hold at most `entries` engines in the opening cache, dropping the
    /// least recently used beyond that; 0 turns it off and empties it. It
    /// holds 16 until this is called.
    ///
    /// The cache keeps engines whose construction played a market
    /// prehistory, which on `pt-v21` is about 1.45 s of the build over 20
    /// names, so a second build from the same seed, roster, economy and
    /// model is a clone of the first. A clone is the same engine to the bit,
    /// so the setting moves no result, only the time a repeated build takes
    /// and the memory the kept engines hold: about 160 KB for a 20-name
    /// engine and 1.9 MB for a 500-name one. The kept engines hold 2,000
    /// names between them at most, whatever the count, and a build whose
    /// arguments hold a NaN is never kept.
    ///
    /// The cache is one per process and shared by every thread.
    pub fn set_opening_cache_capacity(entries: usize) {
        crate::opening_cache::set_capacity(entries);
    }

    /// What the opening cache holds and how many builds it has served
    /// (`hits`) or had to play (`misses`) since the process started. See
    /// [`Engine::set_opening_cache_capacity`].
    pub fn opening_cache_info() -> OpeningCacheInfo {
        crate::opening_cache::info()
    }

    /// [`Engine::with_params_from_opening`] without the opening cache: the
    /// build itself.
    pub(crate) fn build_from_opening(
        seed: u64,
        companies: Vec<TickCompany>,
        economy: EconomyState,
        central_bank: CentralBankState,
        sector_keys: Vec<String>,
        params: ModelParams,
        settle_opening: bool,
    ) -> Self {
        // THE ROSTER'S BETA, normalised before anything reads it
        // (`market_beta_normalise`). A branch, so at 0.0 no sum is taken and
        // every beta is the instrument's bit for bit.
        let companies = if params.market_beta_normalise == 0.0 {
            companies
        } else {
            normalise_roster_beta(companies, params.market_beta_normalise)
        };
        let companies_len = companies.len();
        // Read before the companies move into the struct, and never
        // rewritten: what `set_fundamentals` is measured against.
        let base_fundamentals = companies
            .iter()
            .map(|c| [c.eps, c.book_value_per_share, c.revenue_growth])
            .collect();
        let base_shares = companies.iter().map(|c| c.stock.shares_outstanding).collect();
        // Read before the economy moves into the struct, and never
        // recomputed: this is where the run's nominal output starts.
        let nominal_output_base = economy.gdp * economy.cpi;
        let mut engine = Self {
            economy_alone: false,
            nominal_output_base,
            root_seed: seed,
            market_rng: GameRng::substream(seed, stream::MARKET),
            economy_rng: GameRng::substream(seed, stream::ECONOMY),
            external_rng: GameRng::substream(seed, stream::EXTERNAL),
            jump_rng: GameRng::substream(seed, stream::JUMPS),
            volume_rng: GameRng::substream(seed, stream::VOLUME),
            volume_idio_rng: GameRng::substream(seed, stream::VOLUME_IDIO),
            news_rng: GameRng::substream(seed, stream::NEWS),
            overnight_rng: GameRng::substream(seed, stream::OVERNIGHT),
            market_vol_level_rng: GameRng::substream(seed, stream::MARKET_VOL_LEVEL),
            crisis_epicentre_rng: GameRng::substream(seed, stream::CRISIS_EPICENTRE),
            cycle_nowcast_rng: GameRng::substream(seed, stream::CYCLE_NOWCAST),
            // One normal per name, then one for the market's common level,
            // whenever either opening dial is on.
            opening_z: if params.opening_mispricing_sigma != 0.0
                || params.opening_market_sigma != 0.0
            {
                let mut g = GameRng::substream(seed, stream::OPENING);
                (0..companies_len + 1).map(|_| g.next_normal()).collect()
            } else {
                Vec::new()
            },
            opening_carry: Vec::new(),
            overnight_moves: vec![0.0; companies_len],
            overnight_fair_value_moves: vec![0.0; companies_len],
            jump_fair_value_moves: vec![0.0; companies_len],
            dividend_moves: vec![0.0; companies_len],
            distribution_log: Vec::new(),
            earnings_key: GameRng::earnings_key(seed),
            earnings_moves: vec![0.0; companies_len],
            earnings_withheld: vec![0.0; companies_len],
            night_market_factor: 0.0,
            last_closes: companies.iter().map(|c| c.stock.price).collect(),
            prior_closes: companies.iter().map(|c| c.stock.price).collect(),
            opening_settled: false,
            companies,
            economy,
            central_bank,
            attribution: vec![[0.0; crate::market::factors::COMPONENT_COUNT]; companies_len],
            noise_parts: vec![[0.0; 3]; companies_len],
            noise_own_scale2: vec![0.0; companies_len],
            innovation_day: if params.overnight_market_share != 0.0
                || params.overnight_idio_share != 0.0
            {
                vec![0.0; companies_len]
            } else {
                Vec::new()
            },
            tick_components: vec![[0.0; crate::market::factors::TICK_COMPONENT_COUNT]; companies_len],
            // NaN, not zero: a company that has never ticked has no valuation,
            // and zero is a real one that would silently read as "worthless"
            // rather than as "not yet computed".
            tick_fundamental: vec![f64::NAN; companies_len],
            tick_anchor: vec![f64::NAN; companies_len],
            // Zero, not NaN: a company that has not ticked has not moved, and
            // a move of zero is the true reading rather than a missing one.
            tick_shock: vec![0.0; companies_len],
            tick_absorbed: vec![0.0; companies_len],
            tick_clamp: vec![0.0; companies_len],
            tick_repriced: vec![0.0; companies_len],
            // Nothing has been written to a price before the first print.
            repriced_pending: vec![0.0; companies_len],
            // Empty until the counterfactual is switched on, so emptiness is
            // the one signal that says whether an arm ran.
            tick_unbounded_print: Vec::new(),
            tick_liquidity_share: Vec::new(),
            settle_depth_counterfactual: false,
            market_vol: MarketVarianceState::new_with(&params),
            universe_stress: 0.0,
            volume_state: 0.0,
            forced_flow_spent: 0.0,
            market_vol_log_level: 0.0,
            vix_log_level: 0.0,
            vix_anchor_slow: 0.0,
            market_vol_cycle_log: None,
            vix_stress_memory: 0.0,
            vix_fear: 0.0,
            vix_target_premium: 0.0,
            vix_target_premium_half_life: 0.0,
            vix_target_floor: None,
            crisis_in_episode: false,
            crisis_sessions_under: 0,
            crisis_epicentre: -1,
            crisis_epicentre_pin: None,
            vix_sets_variance_pending: false,
            macro_pins_today: 0,
            pinned_corporate_spread: 0.0,
            pinned_vix_jump: 0.0,
            market_day_scale: 1.0,
            session_news: Vec::new(),
            volume_idio: vec![0.0; companies_len],
            jump_move: vec![0.0; companies_len],
            sector_variance: vec![0.0; sector_keys.len()],
            sector_day_factor: vec![0.0; sector_keys.len()],
            sector_target_day: 0.0,
            jump_excitation: vec![0.0; companies_len],
            idio_variance: vec![0.0; companies_len],
            idio_jump_pending: vec![0.0; companies_len],
            idio_jump_var_pending: vec![0.0; companies_len],
            idio_jump_today: vec![0.0; companies_len],
            idio_jump_var_today: vec![0.0; companies_len],
            sector_keys,
            draws: StreamDraws::default(),
            current_day: 0,
            elapsed_days: 0,
            carried_ticks: None,
            base_fundamentals,
            base_shares,
            day_marks: Vec::new(),
            params,
            model_fingerprint: std::sync::OnceLock::new(),
            // Replaced immediately below. Zero rather than the dial's own
            // value so that a path which somehow skipped the derivation
            // would divide by zero rather than run on a plausible number.
            vix_anchor: 0.0,
            // No day has closed, so no VIX update has read a variance.
            last_index_variance: None,
            last_market_targets: None,
            rate_live: None,
            index_divisor: 0.0,
            index_close: 0.0,
            vix_live: None,
            forecast: None,
            futures: futures::FuturesState::new(seed),
            vix_futures: vix_futures::VixFuturesState::default(),
            rate_futures: rate_futures::RateFuturesState::default(),
            oil_futures: oil_futures::OilFuturesState::default(),
            margin: margin::MarginState::default(),
            stress_vix_max: 0.0,
            stress_hold_age: STRESS_HOLD_NEVER,
            drawdown_returns: std::collections::VecDeque::new(),
            drawdown_mcap_prev: 0.0,
            policy_path: 0.0,
            policy_anticipation_priced: 0.0,
            rates: crate::rates::RateBook::default(),
            book: crate::agent_book::BookState::default(),
            cycle_history: std::collections::VecDeque::new(),
            cycle_nowcast: [0.0; 5],
            cycle_nowcast_terms: ([0.0; 5], 0.0),
            gdp_publication: GdpPublication::default(),
            anticipation_drift: 0.0,
            anticipation_raw: 0.0,
            cycle_publication: CyclePublication {
                key: crate::rng::publication_key(seed),
                ..CyclePublication::default()
            },
            oil_drift_key: crate::rng::oil_drift_key(seed),
            oil_target_drift: 0.0,
            population: None,
        };
        engine.vix_anchor = engine.derive_vix_anchor();
        engine.cycle_nowcast_terms = engine.derive_cycle_nowcast_terms();
        // The front two index futures (`futures_index_listed`); nothing with
        // the switch off.
        engine.futures_list_initial();
        // The opening meeting interval, 45 calendar days, onto the macro
        // calendar's steps. Only a fresh schedule is moved, and never on
        // the shipped calendar, where `scale_days` is the literal anyway.
        {
            let cal = engine.macro_calendar();
            let cb = &mut engine.central_bank;
            if !cal.is_shipped() && cb.next_meeting_date - cb.last_meeting_date == 45 * 24 * 60 {
                cb.next_meeting_date = cb.last_meeting_date + cal.scale_days(45) * 24 * 60;
            }
        }
        // Unemployment's impulse opens at the drive of the economy it opens
        // in, so the adjustment starts stationary; the burn-in then runs it.
        engine.seed_unemployment_impulse();
        // The burn-in's phase path, kept only for the market's prehistory
        // (`market_prehistory_sessions`), and empty with that dial at 0.0.
        let burn_in_path = if settle_opening {
            engine.burn_in_economy()
        } else {
            Vec::new()
        };
        // The same two dials `burn_in_economy` reads: either one rewrites
        // the economy the caller passed in.
        engine.opening_settled = settle_opening
            && (engine.params.macro_burn_in_days > 0.0
                || engine.params.cycle_stationary_opening != 0.0);
        // The earnings cycle opens at the level of the phase the economy
        // opens in, so a market that opens in a contraction does not spend
        // its first months drifting toward it: a stationary opening, as the
        // mispricing's is. The opening's premium split books the difference
        // into the names' fair-value levels, so no opening price moves.
        if engine.params.earnings_cycle_depth != 0.0 {
            engine.economy.earnings_cycle = engine.earnings_cycle_target();
        }
        // The market opens knowing the phase it opens in.
        engine.seed_cycle_nowcast();
        // The burn-in's accumulated drift is not the run's: `D` opens at
        // zero (`earnings_anticipation_drift_share`).
        engine.anticipation_drift = 0.0;
        engine.refresh_earnings_anticipation();
        // After the burn-in and the stationary opening, so the phase the
        // run opens in is the one published until the lag has elapsed.
        engine.seed_cycle_history();
        // Likewise the growth the run opens at, as day 0 of quarter 0.
        engine.seed_gdp_publication(0);
        // THE MARKET'S PREHISTORY, last, on the engine as it opens: a copy
        // lives the burn-in's last sessions and hands back its volatility
        // state. A branch at 0.0, every preset: nothing is copied or run, and
        // the market opens at the constructor's baseline as it always did.
        // Not when the caller named the opening, for the burn-in's reason.
        if settle_opening && engine.params.market_prehistory_sessions > 0.0 {
            engine.live_market_prehistory(&burn_in_path);
        }
        engine
    }

    /// Play the market's last `market_prehistory_sessions` sessions before
    /// day zero on a copy of this engine, and open with the copy's
    /// volatility state.
    ///
    /// # Why
    ///
    /// The burn-in (`macro_burn_in_days`) runs the economy without a market,
    /// so every volatility state the constructor seeds opens at its
    /// phase-free baseline: the factor variance at `market_factor_sigma`
    /// squared, the VIX wherever the index's baseline variance puts it, the
    /// anchor's memory at zero and the cycle's multiplier unset. A run that
    /// opens in an expansion then spends two quarters falling to the level
    /// its expansions hold, and one that opens in a contraction rises
    /// through the whole of its own. Both are travel the run's later years
    /// never show.
    ///
    /// # What the copy lives through
    ///
    /// The economy's own recorded phases, one a session, ending on the
    /// phase and age the run opens in: `path` is the burn-in's phase after
    /// each of its days. A prehistory longer than the burn-in holds its
    /// first phase for the difference (and the opening's phase throughout
    /// when there is no burn-in). The copy's cycle may turn between
    /// sessions; the recorded phase is set again before the next, so the
    /// copy's volatility follows the path the run's economy actually took.
    ///
    /// # What it draws
    ///
    /// Every stream of the copy is a surgery generator of the run's root
    /// seed under [`crate::rng::PREHISTORY_TAG`], and its earnings and
    /// publication keys are mixed with the same tag, so no session of the
    /// prehistory shares a draw with any session of the run. The run's own
    /// generators are not touched: its draw schedule is the one it had.
    ///
    /// # What comes back
    ///
    /// The volatility state and nothing else: the factor variance (its two
    /// components, the mixture, the smoothed VIX and the return memory),
    /// the VIX, the VIX's and the factor's slow levels, the anchor's and
    /// the stress premium's memories, the cycle's volatility multiplier,
    /// and each sector's variance and each name's GARCH and idiosyncratic
    /// variance and jump excitation. Prices, fair values, the economy's
    /// other fields, the central bank and the books are the run's own. The
    /// central bank's stress level (`fed_stress_cut`), the highest VIX
    /// since the last meeting, is restarted at the VIX the run opens on.
    fn live_market_prehistory(&mut self, path: &[(crate::economy::CyclePhase, f64)]) {
        let n = self.params.market_prehistory_sessions as usize;
        let tag = crate::rng::PREHISTORY_TAG;
        let root = self.root_seed;
        let mut pre = self.clone();
        pre.market_rng = GameRng::surgery(root, stream::MARKET, tag);
        pre.economy_rng = GameRng::surgery(root, stream::ECONOMY, tag);
        pre.external_rng = GameRng::surgery(root, stream::EXTERNAL, tag);
        pre.jump_rng = GameRng::surgery(root, stream::JUMPS, tag);
        pre.volume_rng = GameRng::surgery(root, stream::VOLUME, tag);
        pre.volume_idio_rng = GameRng::surgery(root, stream::VOLUME_IDIO, tag);
        pre.news_rng = GameRng::surgery(root, stream::NEWS, tag);
        pre.overnight_rng = GameRng::surgery(root, stream::OVERNIGHT, tag);
        pre.market_vol_level_rng = GameRng::surgery(root, stream::MARKET_VOL_LEVEL, tag);
        pre.crisis_epicentre_rng = GameRng::surgery(root, stream::CRISIS_EPICENTRE, tag);
        pre.cycle_nowcast_rng = GameRng::surgery(root, stream::CYCLE_NOWCAST, tag);
        // The futures write nothing a price reads, and the prehistory hands
        // back prices' state alone, so its copy lists none.
        pre.params.futures_index_listed = 0.0;
        pre.params.night_session_steps = 0.0;
        pre.params.futures_vix_listed = 0.0;
        pre.params.futures_vix_live_fast_share = 0.0;
        pre.params.futures_vix_live_fast_half_life = 0.0;
        pre.params.futures_vix_live_slow_half_life = 0.0;
        pre.params.futures_rates_listed = 0.0;
        pre.params.futures_oil_listed = 0.0;
        pre.params.margin_scan_coverage = 0.0;
        pre.params.margin_scan_tail = 0.0;
        pre.earnings_key = crate::rng::prehistory_key(self.earnings_key);
        pre.cycle_publication.key = crate::rng::prehistory_key(self.cycle_publication.key);
        let opening = (self.economy.cycle_phase, self.economy.months_in_current_phase);
        let mut buffer = SessionBuffer::new();
        let names = pre.companies.len();
        for k in 0..n {
            // Session k of n ends the prehistory at k = n - 1 on the path's
            // last day, which is the phase and age the run opens in.
            let back = n - k;
            let (phase, months) = if path.is_empty() {
                opening
            } else if back <= path.len() {
                path[path.len() - back]
            } else {
                path[0]
            };
            pre.economy.cycle_phase = phase;
            pre.economy.months_in_current_phase = months;
            pre.set_current_day(k as i64);
            pre.open_market();
            let innovations: Vec<Option<f64>> = vec![None; names];
            let variances = pre.sector_base_variances();
            pre.run_session(
                &SessionRequest {
                    start: GameTime { hour: 9, minute: 30, day_of_week: 3 },
                    ticks: 390,
                    volatility_multiplier: 1.0,
                    news: &[],
                    news_impact_queue: &[],
                    order_volumes: &[],
                    fills: &[],
                    close_at_end: false,
                    reopen: false,
                    daily_innovations: &innovations,
                    sector_base_variances: &variances,
                    stop: None,
                },
                &mut buffer,
            );
            pre.close_day(k as i64 + 1);
        }
        self.market_vol = pre.market_vol;
        self.economy.vix = pre.economy.vix;
        self.vix_log_level = pre.vix_log_level;
        self.market_vol_log_level = pre.market_vol_log_level;
        self.vix_anchor_slow = pre.vix_anchor_slow;
        self.vix_stress_memory = pre.vix_stress_memory;
        self.vix_fear = pre.vix_fear;
        self.market_vol_cycle_log = pre.market_vol_cycle_log;
        self.sector_variance = pre.sector_variance.clone();
        self.idio_variance = pre.idio_variance.clone();
        self.jump_excitation = pre.jump_excitation.clone();
        for (c, p) in self.companies.iter_mut().zip(pre.companies.iter()) {
            c.stock.garch_variance = p.stock.garch_variance;
        }
        if self.params.fed_stress_cut != 0.0 {
            self.stress_vix_max = self.economy.vix;
        }
        // The drawdown hold's window: the copy's returns, so the run's first
        // year reads a full window. Its base is the run's own cap, which the
        // first close records.
        if self.keeps_drawdown_window() {
            self.drawdown_returns = pre.drawdown_returns.clone();
        }
        // THE VALUATION STATE, under its own switch: a branch at 0.0, every
        // preset, where the market opens at the valuation it always did.
        if self.params.market_prehistory_valuation != 0.0 {
            self.carry_prehistory_valuation(&pre);
        }
    }

    /// Open with the valuation state the market's prehistory left on its
    /// copy (`market_prehistory_valuation`), booked so no opening price
    /// moves.
    ///
    /// # Why
    ///
    /// The burn-in has no market, so every valuation state the market feeds
    /// opens where a market that never traded leaves it, and drifts to its
    /// level over the first year: the names' mispricing (whose settled mean
    /// is below zero, from the non-zero-mean market terms left in `s`), the
    /// VIX feedback's exposure (built by spikes and given back at
    /// `fair_value_vix_release_half_life`), the anticipation's drift, the
    /// earnings cycle (the burn-in's level, replaced by the phase's target),
    /// credit's leverage gap, the Fed put's owed cut and the policy path's
    /// forecast. On R17Bd with a
    /// 252-session prehistory year 0 returned about two points less than
    /// the years after it, all in its first two quarters.
    ///
    /// # What is carried
    ///
    /// From the copy, at the end of its prehistory:
    ///
    /// - each name's mispricing `s`, which the opening's split takes in
    ///   place of its draw (`opening_carry`), so the name's fair-value level
    ///   absorbs the difference at the first open;
    /// - the VIX feedback's exposure, the anticipation's drift and the
    ///   earnings cycle, which move fair value only, so the same split books
    ///   them into the fair-value levels;
    /// - credit's leverage gap, with the corporate yield moved by the change
    ///   it makes to the spread formula at tonight's VIX and multiplier;
    /// - the Fed put's owed cut and its stock, with the policy rate lowered
    ///   by the owed cut (not below zero) and the prime rate, both Treasury
    ///   yields, the corporate yield and the mortgage rate moved by the same
    ///   amount, which is where the rule, the curve's anchor (whose ladder
    ///   adds the owed cut back) and the spreads put them.
    /// - the market's forecast of the policy path, with the curve moved by
    ///   the share of its change each yield prices (the burn-in leaves the
    ///   forecast where the economy's own early cuts put it).
    ///
    /// Every price the run opens at, its draws and every other state are its
    /// own. The rate instruments are marked after construction, at the curve
    /// the run opens on.
    fn carry_prehistory_valuation(&mut self, pre: &Engine) {
        if !self.opening_z.is_empty() {
            self.opening_carry = self
                .companies
                .iter()
                .zip(pre.companies.iter())
                .map(|(c, p)| match p.stock.mispricing_s {
                    Some(v) if !c.is_bankrupt && c.is_public && !p.is_bankrupt && p.is_public => v,
                    _ => f64::NAN,
                })
                .collect();
        }
        if self.carries_vix_feedback() {
            self.economy.vix_feedback = pre.economy.vix_feedback;
        }
        if self.params.earnings_cycle_depth != 0.0 {
            self.economy.earnings_cycle = pre.economy.earnings_cycle;
        }
        if self.carries_anticipation_drift() {
            self.anticipation_drift = pre.anticipation_drift;
        }
        self.refresh_earnings_anticipation();
        if self.carries_spread_equity_gap() {
            let gap = pre.economy.spread_equity_gap;
            let m = if self.params.cycle_nowcast_accuracy != 0.0
                || self.params.corporate_spread_cycle != 0.0
            {
                self.priced_spread_multiplier(self.economy.cycle_phase)
            } else {
                crate::economy::central_bank::spread_multiplier_of(self.economy.cycle_phase)
            };
            let cut = self.params.corporate_spread_vix_cut;
            let gain = self.params.corporate_spread_equity_gain;
            let vix = self.economy.vix;
            let moved = crate::economy::central_bank::spread_formula_with(vix, m, cut, gain * gap)
                - crate::economy::central_bank::spread_formula_with(
                    vix, m, cut, gain * self.economy.spread_equity_gap);
            self.economy.spread_equity_gap = gap;
            self.economy.corporate_bond_yield += moved;
        }
        if self.params.fed_put_gain != 0.0 {
            let owed = pre.economy.fed_put_owed;
            let ffr = self.economy.federal_funds_rate;
            let cut = ffr - crate::mathx::max(0.0, ffr - owed);
            self.economy.fed_put = if owed > 0.0 { pre.economy.fed_put * cut / owed } else { 0.0 };
            self.economy.fed_put_owed = cut;
            self.economy.federal_funds_rate -= cut;
            self.economy.prime_rate -= cut;
            self.economy.treasury_yield_10y -= cut;
            self.economy.treasury_yield_2y -= cut;
            self.economy.corporate_bond_yield -= cut;
            self.economy.mortgage_rate_30y -= cut;
        }
        if self.params.treasury_path_pricing != 0.0 {
            // The priced path's change moves the 10-year's anchor by `1 - d`
            // of itself and the 2-year's formula by `0.85 + 0.15 (1 - d)`,
            // `d` the damping, as `reprice_anticipated_meeting` books a
            // priced change; the corporate yield and the mortgage rate move
            // with the 10-year.
            let moved = self.params.treasury_path_pricing * (pre.policy_path - self.policy_path);
            let f10 = 1.0 - self.params.treasury_policy_damping;
            let f2 = 0.85 + 0.15 * f10;
            self.policy_path = pre.policy_path;
            self.economy.treasury_yield_10y += f10 * moved;
            self.economy.treasury_yield_2y += f2 * moved;
            self.economy.corporate_bond_yield += f10 * moved;
            self.economy.mortgage_rate_30y += f10 * moved;
        }
    }

    /// The share of the gap to its drive unemployment's impulse closes at a
    /// monthly release, from `unemployment_adjustment_half_life`; 0.0 off.
    fn unemployment_adjustment(&self) -> f64 {
        let h = self.params.unemployment_adjustment_half_life;
        if h == 0.0 {
            return 0.0;
        }
        1.0 - crate::mathx::pow(0.5, self.macro_calendar().month_f64() / h)
    }

    /// Set unemployment's impulse to what its cyclical drivers ask for in
    /// the economy as it stands: at construction, or on a restore from a
    /// snapshot that carried none. Nothing with the dial at 0.0.
    pub fn seed_unemployment_impulse(&mut self) {
        if self.params.unemployment_adjustment_half_life == 0.0 {
            return;
        }
        let e = &self.economy;
        let phase = crate::economy::phase_characteristics_for(
            e.cycle_phase, self.params.cycle_us_calibration != 0.0);
        self.economy.unemployment_impulse = crate::economy::daily::unemployment_drive_with(
            phase.unemployment_trend, e.cycle_phase, e.gdp_growth,
            self.params.unemployment_okun_coefficient);
    }

    /// `gdp_publication_lag` in sessions; 0 is off.
    pub fn gdp_publication_lag(&self) -> i64 {
        self.params.gdp_publication_lag as i64
    }

    /// GDP growth as published, in percent: the mean of the true daily
    /// growth over the last macro-calendar quarter released, released
    /// `gdp_publication_lag` sessions after that quarter's last close, and
    /// the opening growth until the first release. With the dial at 0.0 the
    /// growth the economy runs at, `economy().gdp_growth`.
    ///
    /// Every route that REPORTS growth reads this; everything that MOVES on
    /// it (output, earnings, unemployment, the cycle's hazards, the central
    /// bank) reads the true figure.
    pub fn published_gdp_growth(&self) -> f64 {
        if self.params.gdp_publication_lag == 0.0 {
            return self.economy.gdp_growth;
        }
        self.gdp_publication.published
    }

    /// The published figure and the quarters behind it. Default with the
    /// dial at 0.0.
    pub fn gdp_publication(&self) -> &GdpPublication {
        &self.gdp_publication
    }

    /// Put the published figure's state back (a restore). Refuses any
    /// state while the dial is 0.0, a quarter with no close averaged,
    /// non-finite figures, and releases out of order.
    pub fn set_gdp_publication(&mut self, state: GdpPublication) -> Result<(), String> {
        if self.params.gdp_publication_lag == 0.0 {
            return Err(
                "this snapshot carries a published GDP growth state, and this \
                 engine's gdp_publication_lag is 0, so it keeps none".to_string());
        }
        if state.count == 0 {
            return Err(
                "this snapshot's published GDP growth state averages no close \
                 in its quarter; gdp_publication_lag's state always holds one".to_string());
        }
        let finite = state.published.is_finite() && state.sum.is_finite()
            && state.pending.iter().all(|&(_, v)| v.is_finite());
        if !finite {
            return Err(
                "this snapshot's published GDP growth state (gdp_publication_lag) \
                 carries a non-finite figure".to_string());
        }
        if state.pending.iter().zip(state.pending.iter().skip(1)).any(|(a, b)| a.0 >= b.0) {
            return Err(
                "this snapshot's pending GDP releases (gdp_publication_lag) are \
                 not in release order".to_string());
        }
        self.gdp_publication = state;
        Ok(())
    }

    /// Start the published figure at the growth the economy holds now,
    /// taken as the value of `day`, the last day closed: the opening (day
    /// 0), or a restore from a snapshot that carried no state. The quarter
    /// `day` falls in is averaged from this value on; nothing is pending.
    /// Nothing with the dial at 0.0.
    pub fn seed_gdp_publication(&mut self, day: i64) {
        if self.params.gdp_publication_lag == 0.0 {
            self.gdp_publication = GdpPublication::default();
            return;
        }
        let q = self.macro_calendar().days_per_quarter();
        let g = self.economy.gdp_growth;
        self.gdp_publication = GdpPublication {
            published: g,
            quarter: day.div_euclid(q),
            count: 1,
            sum: g,
            pending: std::collections::VecDeque::new(),
        };
    }

    /// The close of `day` into the published figure: a new quarter closes
    /// the one before (its mean queued for release `lag` sessions after its
    /// last day), the day's growth joins its quarter, and every release due
    /// by this close is published, the latest last. Nothing with the dial
    /// at 0.0.
    fn record_gdp_growth(&mut self, day: i64) {
        let lag = self.gdp_publication_lag();
        if lag == 0 {
            return;
        }
        let q = self.macro_calendar().days_per_quarter();
        let quarter = day.div_euclid(q);
        let growth = self.economy.gdp_growth;
        let p = &mut self.gdp_publication;
        if quarter != p.quarter {
            if p.count > 0 {
                let last_day = (p.quarter + 1) * q - 1;
                p.pending.push_back((last_day + lag, p.sum / f64::from(p.count)));
            }
            p.quarter = quarter;
            p.count = 0;
            p.sum = 0.0;
        }
        p.sum += growth;
        p.count += 1;
        while let Some(&(release, value)) = p.pending.front() {
            if release > day {
                break;
            }
            p.published = value;
            p.pending.pop_front();
        }
    }

    /// `cycle_publication_lag` in sessions; 0 is off, and so is a lag under
    /// `cycle_publication_lag_draw`, which keeps no history.
    pub fn cycle_publication_lag(&self) -> usize {
        if self.params.cycle_publication_lag_draw != 0.0 {
            return 0;
        }
        self.params.cycle_publication_lag as usize
    }

    /// Whether the engine keeps the fixed lag's phase history: the snapshot
    /// and the state hash carry it exactly then.
    pub fn carries_cycle_history(&self) -> bool {
        self.cycle_publication_lag() != 0
    }

    /// The publication schedule under `cycle_publication_lag_draw`.
    pub fn cycle_publication(&self) -> &CyclePublication {
        &self.cycle_publication
    }

    /// Put a publication schedule back (a restore). Refused with the switch
    /// at 0.0, which keeps none, and for pending turns out of order.
    pub fn set_cycle_publication(&mut self, state: CyclePublication) -> Result<(), String> {
        if self.params.cycle_publication_lag_draw == 0.0 {
            return Err("this snapshot carries a cycle publication schedule, and this \
                        engine's cycle_publication_lag_draw is 0, so it keeps none"
                .to_string());
        }
        let mut last = i64::MIN;
        for &(close, _) in &state.pending {
            if close < last {
                return Err(format!(
                    "a cycle publication schedule's pending closes are in order, got {:?}",
                    state.pending));
            }
            last = close;
        }
        if state.closes < 0 {
            return Err(format!("a cycle publication schedule's closes are {}", state.closes));
        }
        self.cycle_publication = state;
        Ok(())
    }

    /// Open the publication schedule on the phase the economy is in: the
    /// opening, or a restore from a snapshot that carried none. The key is
    /// kept. Nothing with `cycle_publication_lag_draw` at 0.0.
    pub fn seed_cycle_publication(&mut self) {
        if self.params.cycle_publication_lag_draw == 0.0 {
            return;
        }
        let phase = self.economy.cycle_phase;
        self.cycle_publication = CyclePublication {
            key: self.cycle_publication.key,
            published: phase,
            last_true: phase,
            closes: 0,
            turns: 0,
            pending: std::collections::VecDeque::new(),
        };
    }

    /// One close of the schedule: a turn draws its lag and joins the queue,
    /// and every turn whose close has come is published, the latest last.
    fn record_cycle_publication(&mut self) {
        let now = self.economy.cycle_phase;
        let p = &mut self.cycle_publication;
        p.closes += 1;
        if now != p.last_true {
            let u = crate::rng::publication_uniform(p.key, p.turns);
            let lag = cycle_publication_lag_of(now, u);
            p.turns += 1;
            let earliest = p.pending.back().map(|&(c, _)| c).unwrap_or(i64::MIN);
            let at = p.closes + lag;
            p.pending.push_back((if at > earliest { at } else { earliest }, now));
            p.last_true = now;
        }
        while let Some(&(close, phase)) = p.pending.front() {
            if close > p.closes {
                break;
            }
            p.published = phase;
            p.pending.pop_front();
        }
    }

    /// The business-cycle phase as published: the phase the economy held at
    /// the close `cycle_publication_lag` sessions ago, or the opening phase
    /// while fewer than that many sessions have closed. With the dial at 0.0
    /// the phase the economy is in, `economy().cycle_phase`.
    ///
    /// Every route that REPORTS the phase reads this; everything that PRICES
    /// or MOVES on it (the earnings cycle and its anticipation, the cycle's
    /// hazards, the stress intensity, the central bank) reads the true phase.
    pub fn published_cycle_phase(&self) -> crate::economy::CyclePhase {
        // Under `cycle_publication_lag_draw` the phase of the latest turn
        // published, and the fixed lag is not read.
        if self.params.cycle_publication_lag_draw != 0.0 {
            return self.cycle_publication.published;
        }
        if self.params.cycle_publication_lag == 0.0 {
            return self.economy.cycle_phase;
        }
        self.cycle_history.front().copied().unwrap_or(self.economy.cycle_phase)
    }

    /// The phase history the published phase is read from, oldest first:
    /// `cycle_publication_lag + 1` phases, the last the current one as of
    /// the last close. Empty with the dial at 0.0.
    pub fn cycle_history(&self) -> &std::collections::VecDeque<crate::economy::CyclePhase> {
        &self.cycle_history
    }

    /// Put a phase history back (a restore). Refuses one whose length is not
    /// `cycle_publication_lag + 1`, or any history while the dial is 0.0.
    pub fn set_cycle_history(
        &mut self,
        history: Vec<crate::economy::CyclePhase>,
    ) -> Result<(), String> {
        let lag = self.cycle_publication_lag();
        if lag == 0 {
            if history.is_empty() {
                return Ok(());
            }
            return Err(format!(
                "this snapshot carries a published-phase history of {} phases, \
                 and this engine's cycle_publication_lag is 0, so it keeps none",
                history.len()));
        }
        if history.len() != lag + 1 {
            return Err(format!(
                "this snapshot carries a published-phase history of {} phases, \
                 and cycle_publication_lag {} keeps {}",
                history.len(), lag, lag + 1));
        }
        self.cycle_history = history.into();
        Ok(())
    }

    /// Fill the history with the current phase: the opening, or a restore
    /// from a snapshot that carried none. Nothing with the dial at 0.0.
    pub fn seed_cycle_history(&mut self) {
        // The drawn schedule opens where the fixed history does.
        self.seed_cycle_publication();
        let lag = self.cycle_publication_lag();
        self.cycle_history.clear();
        if lag > 0 {
            self.cycle_history.extend(std::iter::repeat_n(self.economy.cycle_phase, lag + 1));
        }
    }

    /// Record the phase the close has left the economy in, and drop the
    /// oldest. Nothing with the dial at 0.0.
    fn record_cycle_phase(&mut self) {
        if self.params.cycle_publication_lag_draw != 0.0 {
            self.record_cycle_publication();
            return;
        }
        let lag = self.cycle_publication_lag();
        if lag == 0 {
            return;
        }
        self.cycle_history.push_back(self.economy.cycle_phase);
        while self.cycle_history.len() > lag + 1 {
            self.cycle_history.pop_front();
        }
    }

    /// The anticipated earnings level's phase terms, `g_p` in
    /// [`crate::economy::cycle::phase_cycle`] order, and its weight `c` on
    /// the current level. `None` with the anticipation or the cycle off.
    ///
    /// # The derivation
    ///
    /// The valuation reads `A = E[ integral rho e^(-rho s) e(t+s) ds ]`, the
    /// earnings cycle's level averaged over the future with a discount of
    /// `rho = ln 2 / earnings_anticipation_half_life` a session. The level
    /// follows the engine's own law, `de = kappa (T_p - e)` with `kappa =
    /// ln 2 / earnings_cycle_half_life` toward the phase's target `T_p`
    /// (`-depth` in a contraction or a trough, `+depth * upside` otherwise),
    /// and the phase leaves at the rate `lambda_p = 1 / E[T_p]`, its mean
    /// sojourn on the engine's own hazard table (`mean_sojourn_days_for`),
    /// to the next phase of the cycle. Trying `A = c e + g_p` in the
    /// generator equation `rho A = rho e + kappa (T_p - e) dA/de +
    /// lambda_p (A_next - A)` gives `c = rho / (rho + kappa)` and
    /// `(rho + lambda_p) g_p = kappa c T_p + lambda_p g_next`, five linear
    /// equations around the cycle, solved here in closed form.
    ///
    /// So a turn of phase moves `A` at once, by `g_next - g_p`, and a
    /// trough, from which a recovery is nearer than from a contraction,
    /// already reads above a contraction while the level is still falling:
    /// the price's trough leads the earnings'. The sojourns are treated as
    /// exponential, where the table's are Weibull; that is the one
    /// approximation.
    pub fn earnings_anticipation_terms(&self) -> Option<([f64; 5], f64)> {
        let p = &self.params;
        if p.earnings_anticipation_half_life <= 0.0 || p.earnings_cycle_depth == 0.0 {
            return None;
        }
        let rho = std::f64::consts::LN_2 / p.earnings_anticipation_half_life;
        let kappa = std::f64::consts::LN_2 / p.earnings_cycle_half_life;
        let c = rho / (rho + kappa);
        let (mean, _) = crate::economy::cycle::stationary_phase_shares_for(&self.cycle_spec());
        let phases = crate::economy::cycle::phase_cycle();
        let target = |ph: crate::economy::CyclePhase| match ph {
            crate::economy::CyclePhase::Contraction | crate::economy::CyclePhase::Trough => {
                -p.earnings_cycle_depth
            }
            _ => p.earnings_cycle_depth * p.earnings_cycle_upside,
        };
        let mut a = [0.0; 5];
        let mut b = [0.0; 5];
        for k in 0..5 {
            let lambda = if mean[k] > 0.0 { 1.0 / mean[k] } else { 0.0 };
            a[k] = kappa * c * target(phases[k]) / (rho + lambda);
            b[k] = lambda / (rho + lambda);
        }
        // g_k = a_k + b_k g_(k+1): unroll once around the cycle for g_0,
        // then walk backwards.
        let mut acc_a = 0.0;
        let mut acc_b = 1.0;
        for k in 0..5 {
            acc_a += acc_b * a[k];
            acc_b *= b[k];
        }
        let mut g = [0.0; 5];
        g[0] = acc_a / (1.0 - acc_b);
        for k in (1..5).rev() {
            let next = if k == 4 { g[0] } else { g[k + 1] };
            g[k] = a[k] + b[k] * next;
        }
        Some((g, c))
    }

    /// Keep `economy.earnings_anticipation` current: `A - e`, or 0.0 with
    /// the anticipation off. Called wherever the phase or the level moves.
    /// Under `cycle_nowcast_accuracy` the phase term is the belief's
    /// `pi . g`, not the true phase's `g`.
    pub fn refresh_earnings_anticipation(&mut self) {
        let raw = match self.earnings_anticipation_terms() {
            None => 0.0,
            Some((g, c)) => {
                let gk = if self.params.cycle_nowcast_accuracy != 0.0 {
                    let mut acc = 0.0;
                    for (p, gj) in self.cycle_nowcast.iter().zip(g.iter()) {
                        acc += p * gj;
                    }
                    acc
                } else {
                    let k = crate::economy::cycle::phase_cycle()
                        .iter()
                        .position(|ph| *ph == self.economy.cycle_phase)
                        .unwrap_or(0);
                    g[k]
                };
                gk + c * self.economy.earnings_cycle - self.economy.earnings_cycle
            }
        };
        // `earnings_anticipation_drift_share`: the valuation reads `A - e`
        // less the expected drift accumulated in `D`. A branch, so with the
        // share at 0.0 this is the assignment that stood.
        if self.params.earnings_anticipation_drift_share == 0.0 {
            self.economy.earnings_anticipation = raw;
        } else {
            self.anticipation_raw = raw;
            self.economy.earnings_anticipation = raw - self.anticipation_drift;
        }
    }

    /// Whether the engine keeps `D` (`earnings_anticipation_drift_share`):
    /// the snapshot and the state hash carry it, and the last `A - e`,
    /// exactly then.
    pub fn carries_anticipation_drift(&self) -> bool {
        self.params.earnings_anticipation_drift_share != 0.0
    }

    /// `(D, A - e at the last refresh)`; zeros with the share at 0.0.
    pub fn anticipation_drift(&self) -> (f64, f64) {
        (self.anticipation_drift, self.anticipation_raw)
    }

    /// Put `D` and the last `A - e` back (a restore), and the anticipation
    /// the valuation reads with them. Refused with the share at 0.0, which
    /// keeps neither, and for a value that is not finite.
    pub fn set_anticipation_drift(&mut self, drift: f64, raw: f64) -> Result<(), String> {
        if !self.carries_anticipation_drift() {
            return Err("this snapshot carries an anticipation drift, and this engine's \
                        earnings_anticipation_drift_share is 0, so it keeps none"
                .to_string());
        }
        if !drift.is_finite() || !raw.is_finite() {
            return Err(format!(
                "an anticipation drift and its last A - e are finite, got {drift} and {raw}"));
        }
        self.anticipation_drift = drift;
        self.anticipation_raw = raw;
        self.economy.earnings_anticipation = raw - drift;
        Ok(())
    }

    /// Zero `D`: the opening, or a restore from a snapshot that carried
    /// none. The anticipation is refreshed on it. Nothing with the share at
    /// 0.0.
    pub fn seed_anticipation_drift(&mut self) {
        if !self.carries_anticipation_drift() {
            return;
        }
        self.anticipation_drift = 0.0;
        self.refresh_earnings_anticipation();
    }

    /// `earnings_anticipation_drift_share`: at a close, decay `D` with its
    /// half-life and add the share of the session's expected drift of `A`,
    /// `rho (A - e)` as the last refresh left it. Nothing with the share at
    /// 0.0 or the anticipation off.
    fn advance_anticipation_drift(&mut self) {
        let m = self.params.earnings_anticipation_drift_share;
        let h = self.params.earnings_anticipation_half_life;
        if m == 0.0 || h <= 0.0 || self.params.earnings_cycle_depth == 0.0 {
            return;
        }
        let rho = std::f64::consts::LN_2 / h;
        let hd = if self.params.earnings_anticipation_drift_half_life == 0.0 {
            1260.0
        } else {
            self.params.earnings_anticipation_drift_half_life
        };
        self.anticipation_drift =
            self.anticipation_drift * crate::mathx::exp(-std::f64::consts::LN_2 / hd)
                + m * rho * self.anticipation_raw;
    }

    /// The market's belief over the phases, `phase_cycle` order; zeros with
    /// `cycle_nowcast_accuracy` off.
    pub fn cycle_nowcast(&self) -> [f64; 5] {
        self.cycle_nowcast
    }

    /// Put the whole belief on the phase the economy is in: the opening, or
    /// a restore from a snapshot that carried none. Nothing with the dial off.
    pub fn seed_cycle_nowcast(&mut self) {
        if self.params.cycle_nowcast_accuracy == 0.0 {
            return;
        }
        let k = crate::economy::cycle::phase_cycle()
            .iter()
            .position(|ph| *ph == self.economy.cycle_phase)
            .unwrap_or(0);
        self.cycle_nowcast = [0.0; 5];
        self.cycle_nowcast[k] = 1.0;
    }

    /// The nowcast generator's position, for the snapshot.
    pub fn cycle_nowcast_rng_state(&self) -> crate::rng::RngState {
        self.cycle_nowcast_rng.snapshot()
    }

    /// Put the belief and its generator back from a snapshot. Refused with
    /// `cycle_nowcast_accuracy` at 0.0, which keeps neither, and for a
    /// belief that is not five finite non-negative weights summing to one.
    pub fn set_cycle_nowcast(
        &mut self,
        belief: [f64; 5],
        rng: Option<crate::rng::RngState>,
    ) -> Result<(), String> {
        if self.params.cycle_nowcast_accuracy == 0.0 {
            return Err("this snapshot carries a cycle nowcast, and this engine's \
                        cycle_nowcast_accuracy is 0, so it keeps none"
                .to_string());
        }
        let total: f64 = belief.iter().sum();
        if belief.iter().any(|v| !v.is_finite() || *v < 0.0) || (total - 1.0).abs() > 1e-9 {
            return Err(format!(
                "a cycle nowcast is five finite non-negative weights summing to one, got {belief:?}"));
        }
        self.cycle_nowcast = belief;
        if let Some(state) = rng {
            self.cycle_nowcast_rng = GameRng::restore(state);
        }
        Ok(())
    }

    /// One session of the nowcast: the report, drawn from the phase the
    /// session ran in, and the forward filter's predict and update.
    fn update_cycle_nowcast(&mut self) {
        // A pinned phase is public: the belief was put on it at the pin and
        // holds there through the close, with no report drawn.
        if self.macro_pins_today & PIN_CYCLE != 0 {
            return;
        }
        let q = self.params.cycle_nowcast_accuracy;
        let phases = crate::economy::cycle::phase_cycle();
        let k = phases.iter().position(|ph| *ph == self.economy.cycle_phase).unwrap_or(0);
        self.cycle_nowcast_rng.site(Site::CycleNowcastU, 0);
        let u = self.cycle_nowcast_rng.next_f64();
        let r = if u < q {
            k
        } else {
            // One of the other four, uniformly; a uniform that rounds to
            // the top edge takes the last.
            let j = ((u - q) / (1.0 - q) * 4.0) as usize;
            (k + 1 + if j > 3 { 3 } else { j }) % 5
        };
        let lambda = self.cycle_nowcast_terms.0;
        let mut pred = [0.0; 5];
        for (j, (&lam, &p)) in lambda.iter().zip(self.cycle_nowcast.iter()).enumerate() {
            pred[j] += p * (1.0 - lam);
            pred[(j + 1) % 5] += p * lam;
        }
        let other = (1.0 - q) / 4.0;
        let mut total = 0.0;
        for (j, pj) in pred.iter_mut().enumerate() {
            *pj *= if j == r { q } else { other };
            total += *pj;
        }
        if total > 0.0 && total.is_finite() {
            for pj in pred.iter_mut() {
                *pj /= total;
            }
            self.cycle_nowcast = pred;
        } else {
            self.seed_cycle_nowcast();
        }
    }

    /// The spread's cycle multiplier the market prices: the belief's `pi . m`
    /// under the nowcast, the true phase's `m` otherwise, blended toward the
    /// stationary mean by `corporate_spread_cycle`. `phase` is the true phase
    /// to read with the nowcast off.
    fn priced_spread_multiplier(&self, phase: crate::economy::CyclePhase) -> f64 {
        let phases = crate::economy::cycle::phase_cycle();
        let m = if self.params.cycle_nowcast_accuracy != 0.0 {
            let mut acc = 0.0;
            for (p, &ph) in self.cycle_nowcast.iter().zip(phases.iter()) {
                acc += p * crate::economy::central_bank::spread_multiplier_of(ph);
            }
            acc
        } else {
            crate::economy::central_bank::spread_multiplier_of(phase)
        };
        let s = self.params.corporate_spread_cycle;
        if s == 0.0 {
            return m;
        }
        (1.0 - s) * m + s * self.cycle_nowcast_terms.1
    }

    /// `cycle_nowcast_terms`: each phase's exit rate for the nowcast's
    /// predict step and the spread multiplier's occupancy mean for the
    /// blend, from the cycle's own mean sojourns (the ones
    /// `earnings_anticipation_terms` reads). Zeros with both dials off, so
    /// nothing is walked.
    fn derive_cycle_nowcast_terms(&self) -> ([f64; 5], f64) {
        if self.params.cycle_nowcast_accuracy == 0.0 && self.params.corporate_spread_cycle == 0.0 {
            return ([0.0; 5], 0.0);
        }
        let phases = crate::economy::cycle::phase_cycle();
        let (mean, cycle) = crate::economy::cycle::stationary_phase_shares_for(&self.cycle_spec());
        let mut lambda = [0.0; 5];
        let mut mbar = 0.0;
        for j in 0..5 {
            lambda[j] = if mean[j] > 0.0 { crate::mathx::min(1.0, 1.0 / mean[j]) } else { 0.0 };
            if cycle > 0.0 {
                mbar += mean[j] / cycle * crate::economy::central_bank::spread_multiplier_of(phases[j]);
            }
        }
        (lambda, mbar)
    }

    /// The level the earnings cycle is pulled toward in the current phase:
    /// `-depth` in a contraction or a trough, `+depth * upside` otherwise.
    fn earnings_cycle_target(&self) -> f64 {
        let p = &self.params;
        match self.economy.cycle_phase {
            crate::economy::CyclePhase::Contraction | crate::economy::CyclePhase::Trough => {
                -p.earnings_cycle_depth
            }
            _ => p.earnings_cycle_depth * p.earnings_cycle_upside,
        }
    }

    /// The VIX at which every variance coupling reads one.
    ///
    /// # At `vix_level_identity` 0.0 — the dial's value, unchanged
    ///
    /// A branch, not arithmetic. Every preset before pt-v19 reads exactly
    /// `params.market_vol_vix_anchor` at all four coupling sites, which is
    /// the expression each of them carried, so the whole change is
    /// bit-inert there.
    ///
    /// # At 1.0 — derived, and the dial is not read
    ///
    /// `market_vol_vix_anchor`'s own definition is "the VIX level at which
    /// a coupled target equals the baseline variance"
    /// (`market::factor_vol`). Under the identity a VIX IS a variance —
    /// `VIX = (1 + pi) * 100 * sqrt(252 * V)` — so that definition has an
    /// answer rather than a value:
    ///
    /// ```text
    /// anchor = (1 + pi) * 100 * sqrt(252 * V_uncond(roster, params))
    /// ```
    ///
    /// and the forward map `base * (1 - c + c (VIX / anchor)^2)` and the
    /// read-back then agree at the unconditional point BY CONSTRUCTION.
    /// That is the property `vix_implied_from_market` has always claimed in
    /// its comment ("so the loop is consistent") and the two constants
    /// never delivered: at the shipped pair the factor at its baseline is
    /// 12.05 per cent annualised against an anchor of 15.98.
    ///
    /// # Why it is roster-dependent, and why that is right
    ///
    /// A VIX prices its OWN index. A forty-name index with 5.3 effective
    /// names carries a larger idiosyncratic block than a five-hundred-name
    /// one, and the level a real VIX would take on it is genuinely
    /// different. So the anchor stops being a coefficient of the model and
    /// becomes a function of the model AND the universe — which means a
    /// preset record that quotes it must say which roster it was derived
    /// on.
    ///
    /// # The evaluation point needs no anchor, which is what makes it
    /// non-circular
    ///
    /// At `vix == anchor` every coupling's ratio is exactly 1.0 and every
    /// coupled quantity collapses to its own baseline: the factor's target
    /// is `base` (`1 - c + c * 1`), `sector_sigma_for` returns
    /// `sector_factor_sigma`, the per-name clamp reference is the sector's
    /// base variance and `apply_jumps`' rate scale is one. So
    /// `index_unconditional_variance` evaluates the identity at a point
    /// defined without reference to the number being derived.
    fn derive_vix_anchor(&self) -> f64 {
        if self.params.vix_level_identity == 0.0 {
            return self.params.market_vol_vix_anchor;
        }
        let names = self.index_variance_names();
        let bases = self.sector_base_variances_for(&names);
        let v = crate::market::index_var::index_unconditional_variance(
            &self.params, &names, self.sector_keys.len(), &bases);
        let derived =
            crate::market::index_var::vix_from_variance(self.params.vix_variance_premium, v);
        // A ROSTER WITH NO INDEX HAS NO VIX. An engine built with no public
        // names, or one whose whole roster is bankrupt, leaves an
        // unconditional variance of nothing but the market jump, and the
        // anchor it derives is a denominator four coupling sites divide by.
        // The dial is the honest answer there rather than a number the
        // roster cannot support -- and it is a FALLBACK with a condition,
        // not a clamp: on any roster that has an index at all this branch is
        // not taken.
        if derived.is_finite() && derived > 0.0 {
            derived
        } else {
            self.params.market_vol_vix_anchor
        }
    }

    /// The roster as the variance identity reads it: previous-close cap
    /// weights, betas, sector slots, GARCH states and caps.
    ///
    /// The weights are built from `market_cap`, which is the same quantity
    /// `advance_day_with` builds the day's index return from, so the
    /// variance and the return it is the variance OF are weighted the same
    /// way. Bankrupt and unlisted names are skipped by both.
    /// Whether the per-sector variance state is running: either of its
    /// two dials set. A branch, so every preset at (0.0, 0.0) never reads
    /// or writes the state.
    fn sector_state_on(&self) -> bool {
        self.params.sector_vol_alpha != 0.0 || self.params.sector_vol_beta != 0.0
    }

    /// Whether the per-name idiosyncratic variance state runs: either of
    /// `idio_vol_alpha`, `idio_vol_beta` and `idio_vol_jump_bump` off zero.
    /// Off on every preset.
    fn idio_state_on(&self) -> bool {
        self.params.idio_vol_alpha != 0.0
            || self.params.idio_vol_beta != 0.0
            || self.params.idio_vol_jump_bump != 0.0
    }

    /// The per-name idiosyncratic variance ratio of each name
    /// `index_variance_names` lists, in that order, or EMPTY when the state
    /// is off: the VIX identity then prices the idiosyncratic term exactly
    /// as before. An unseeded name reads 1.0.
    fn index_variance_idio_ratios(&self) -> Vec<f64> {
        if !self.idio_state_on() {
            return Vec::new();
        }
        let mut total = 0.0;
        for c in self.companies.iter() {
            if c.is_public && !c.is_bankrupt && c.stock.market_cap > 0.0 {
                total += c.stock.market_cap;
            }
        }
        if !(total > 0.0) {
            return Vec::new();
        }
        let mut out = Vec::with_capacity(self.companies.len());
        for (i, c) in self.companies.iter().enumerate() {
            if !c.is_public || c.is_bankrupt || c.stock.market_cap <= 0.0 {
                continue;
            }
            out.push(self.idio_ratio_at(i));
        }
        out
    }

    /// One name's idiosyncratic variance ratio as the draws read it: the
    /// state, or 1.0 where it is unseeded.
    fn idio_ratio_at(&self, index: usize) -> f64 {
        match self.idio_variance.get(index) {
            Some(v) if *v > 0.0 => *v,
            _ => 1.0,
        }
    }

    /// The close of the per-name idiosyncratic variance state
    /// (`ModelParams::idio_vol_alpha`). `u^2` is the session's own noise
    /// (the own-noise part the ticks drew, already at the state's scale)
    /// over its expected variance, `kappa^2 max(h, idio_sigma_floor)`
    /// (`kappa^2` is `noise_own_scale2`, which carries the state). The jump
    /// channel adds `idio_vol_jump_bump * (I - lambda)`, `I` whether the own
    /// jump the previous close applied was non-zero and `lambda` the rate it
    /// was drawn at (its expected variance over `jump_sigma_idio^2`). `h_day` is each
    /// name's GJR variance before tonight's `close_day`, the one the day's
    /// draws were scaled by. Then tonight's own jump becomes tomorrow's.
    /// Runs after `apply_jumps`, and not at all while the state is off.
    fn close_idio_state(&mut self, h_day: &[f64]) {
        if !self.idio_state_on() {
            return;
        }
        let p = &self.params;
        let (a, b) = (p.idio_vol_alpha, p.idio_vol_beta);
        let n = self.companies.len();
        for v in [
            &mut self.idio_variance,
            &mut self.idio_jump_pending,
            &mut self.idio_jump_var_pending,
            &mut self.idio_jump_today,
            &mut self.idio_jump_var_today,
        ] {
            if v.len() < n {
                v.resize(n, 0.0);
            }
        }
        let bump = p.idio_vol_jump_bump;
        let var1 = p.jump_sigma_idio * p.jump_sigma_idio;
        for i in 0..n {
            let prev = if self.idio_variance[i] > 0.0 { self.idio_variance[i] } else { 1.0 };
            let own = self.noise_parts.get(i).map(|x| x[2]).unwrap_or(0.0);
            let k2 = self.noise_own_scale2.get(i).copied().unwrap_or(0.0);
            let h = h_day.get(i).copied().unwrap_or(0.0);
            let expected = k2 * crate::mathx::max(h, p.idio_sigma_floor);
            // A name that did not trade today (bankrupt, private, or not yet
            // ticked) has no expected variance and keeps its state.
            if expected > 0.0 && own.is_finite() {
                let u2 = own * own / expected;
                // The jump channel, re-centred on the rate the jump realised
                // today was drawn at, so it adds nothing to the mean.
                let jump_term = if bump != 0.0 && var1 > 0.0 {
                    let rate = self.idio_jump_var_pending[i] / var1;
                    let hit = if self.idio_jump_pending[i] != 0.0 { 1.0 } else { 0.0 };
                    bump * (hit - rate)
                } else {
                    0.0
                };
                let raw = (1.0 - a - b) + a * u2 + b * prev + jump_term;
                self.idio_variance[i] = crate::mathx::max(
                    crate::mathx::min(raw, p.garch_ceiling_multiple),
                    p.garch_floor_multiple,
                );
            }
            self.idio_jump_pending[i] = self.idio_jump_today[i];
            self.idio_jump_var_pending[i] = self.idio_jump_var_today[i];
            self.idio_jump_today[i] = 0.0;
            self.idio_jump_var_today[i] = 0.0;
        }
    }

    /// One daily sigma per sector key from the state, or EMPTY when the
    /// state is off (the tick and the read-back then use the shared
    /// VIX-coupled sigma exactly as before). An unseeded sector reads the
    /// target it would be seeded at.
    fn sector_sigmas_now(&self) -> Vec<f64> {
        if !self.sector_state_on() {
            return Vec::new();
        }
        let target = crate::market::tick::sector_sigma_at(
            &self.params, &self.economy, self.vix_anchor);
        self.sector_variance
            .iter()
            .map(|s| if *s > 0.0 { target * crate::mathx::sqrt(*s) } else { target })
            .collect()
    }

    /// The jump excitation of each name `index_variance_names` lists, in
    /// that order, or EMPTY when the excitation dial is 0.0.
    fn index_variance_excitations(&self) -> Vec<f64> {
        if self.params.jump_idio_excitation == 0.0 {
            return Vec::new();
        }
        let mut total = 0.0;
        for c in self.companies.iter() {
            if c.is_public && !c.is_bankrupt && c.stock.market_cap > 0.0 {
                total += c.stock.market_cap;
            }
        }
        if !(total > 0.0) {
            return Vec::new();
        }
        let mut out = Vec::with_capacity(self.companies.len());
        for (i, c) in self.companies.iter().enumerate() {
            if !c.is_public || c.is_bankrupt || c.stock.market_cap <= 0.0 {
                continue;
            }
            out.push(self.jump_excitation.get(i).copied().unwrap_or(0.0));
        }
        out
    }

    /// The close of the per-sector variance state: a symmetric GARCH(1,1)
    /// per sector on the day's accumulated sector factor, reverting to the
    /// VIX-coupled sigma the stateless draw uses as its long-run level,
    /// clamped to the per-name multiples. On the tape the sector residual
    /// has persistence 0.971 +/-
    /// 0.018 with a shock share of 0.063 +/- 0.018.
    fn close_sector_state(&mut self) {
        if !self.sector_state_on() {
            return;
        }
        let p = &self.params;
        // The scale the day's draws were made at. The ratio form: the
        // GARCH(1,1) runs on the factor standardised by the VIX-coupled
        // target, so the coupling's own state is kept whole and the
        // sector's memory net of it is what the dials carry (19.7).
        let target = if self.sector_target_day > 0.0 {
            self.sector_target_day
        } else {
            let sigma = crate::market::tick::sector_sigma_at(p, &self.economy, self.vix_anchor);
            sigma * sigma
        };
        let a = p.sector_vol_alpha;
        let b = p.sector_vol_beta;
        for k in 0..self.sector_keys.len() {
            let prev = if self.sector_variance[k] > 0.0 { self.sector_variance[k] } else { 1.0 };
            let d = self.sector_day_factor[k];
            let z2 = if target > 0.0 { d * d / target } else { 0.0 };
            let raw = (1.0 - a - b) + a * z2 + b * prev;
            self.sector_variance[k] = crate::mathx::max(
                crate::mathx::min(raw, p.garch_ceiling_multiple),
                p.garch_floor_multiple,
            );
            self.sector_day_factor[k] = 0.0;
        }
    }

    fn index_variance_names(&self) -> Vec<crate::market::index_var::NameVariance> {
        self.index_variance_names_with(None)
    }

    /// [`Self::index_variance_names`] with each slot's GARCH variance taken
    /// from `garch` when given (a projection of the close,
    /// `rate_intraday_live`).
    fn index_variance_names_with(
        &self,
        garch: Option<&[f64]>,
    ) -> Vec<crate::market::index_var::NameVariance> {
        let mut total = 0.0;
        for c in self.companies.iter() {
            if c.is_public && !c.is_bankrupt && c.stock.market_cap > 0.0 {
                total += c.stock.market_cap;
            }
        }
        if !(total > 0.0) {
            return Vec::new();
        }
        let mut out = Vec::with_capacity(self.companies.len());
        for (slot, c) in self.companies.iter().enumerate() {
            if !c.is_public || c.is_bankrupt || c.stock.market_cap <= 0.0 {
                continue;
            }
            out.push(crate::market::index_var::NameVariance {
                weight: c.stock.market_cap / total,
                beta: c.stock.beta.unwrap_or(1.0),
                sector: self
                    .sector_keys
                    .iter()
                    .position(|k| *k == c.sector)
                    .unwrap_or(usize::MAX),
                garch_variance: match garch {
                    Some(g) => g.get(slot).copied().unwrap_or(c.stock.garch_variance),
                    None => c.stock.garch_variance,
                },
                market_cap: c.stock.market_cap,
            });
        }
        out
    }

    /// The sector base variances of the names the identity kept, in the
    /// same order — the clamp reference each of their GARCH processes
    /// reverts between.
    ///
    /// `sector_base_variances()` is per COMPANY SLOT and includes the
    /// bankrupt and unlisted names `index_variance_names` drops, so the two
    /// lists are not the same length and pairing them by index would
    /// silently mis-assign a floor to a name. Built by walking the roster
    /// under the same filter instead.
    fn sector_base_variances_for(
        &self,
        names: &[crate::market::index_var::NameVariance],
    ) -> Vec<f64> {
        let all = self.sector_base_variances();
        let mut out = Vec::with_capacity(names.len());
        for (i, c) in self.companies.iter().enumerate() {
            if !c.is_public || c.is_bankrupt || c.stock.market_cap <= 0.0 {
                continue;
            }
            out.push(all.get(i).copied().unwrap_or(c.stock.garch_variance));
        }
        out
    }

    /// The index's one-day-ahead conditional variance, from the states the
    /// engine holds at the close.
    ///
    /// Called after `close_market` has advanced the factor's variance, the
    /// per-name GARCH states and the jumps, so every state it reads is
    /// TOMORROW's — which is what a one-day-ahead variance means and what
    /// an implied volatility prices. The VIX-driven quantities (the sector
    /// draw's sigma, the jump arrival rate, **and the crisis blend's
    /// spike**) are read at the close's VIX, which is the information the
    /// close has.
    ///
    /// # The regime, and why it is passed rather than assumed
    ///
    /// The variance this returns is now REGIME-AWARE, which is charter bar
    /// B4. Two mechanisms the closed form used to be blind to reach it:
    ///
    /// - the **crash amplifier**, through the conditional factor variance
    ///   already passed as `factor_variance` — `index_var` forms the regime
    ///   ratio `sqrt(v_f) / market_factor_sigma` from it, which is the
    ///   quantity `factors.rs` builds `shock_magnitude` from, so there is
    ///   no new state and nothing here to keep in step;
    /// - the **crisis blend**, through `crisis_spike_for` on the close's VIX
    ///   and the remembered universe stress. That is the SAME function
    ///   `compute_tick` calls, not a copy of it, so the read-back and the
    ///   tick cannot disagree about when a crisis is on;
    /// - the **downside transmission tilt and its lagged wire**, through
    ///   `market_vol.prev_day_down()` — the same accessor `simulate_tick`
    ///   passes into `TickInputs`, read here AFTER `close_day_at` has rolled
    ///   the day's factor into `prev_day_factor`, so it is the bit
    ///   tomorrow's ticks will run under and not today's.
    ///
    /// `crisis_blend_variance_damp` is the one piece of the blend this does
    /// NOT carry: it scales the injection by a clamped fractional power of
    /// the draw itself, which is not a polynomial in `z` and has no moment
    /// in `phi` and `Phi`. It is 0.0 in every shipped preset and inert there
    /// through `factors.rs`'s own branch. A preset that set it would have
    /// this read-back price the UNDAMPED blend and therefore overstate the
    /// crisis, and that is declared here and on
    /// [`IndexVarianceTerms::crisis_raw`] rather than caught at runtime —
    /// the residual is the residual, and a panic on a legal dial
    /// combination would be a worse answer than a stated one.
    ///
    /// [`IndexVarianceTerms::crisis_raw`]:
    ///     crate::market::index_var::IndexVarianceTerms::crisis_raw
    fn index_conditional_variance_terms_now(
        &self,
    ) -> crate::market::index_var::IndexVarianceTerms {
        self.index_conditional_variance_terms_at(self.market_vol.variance())
    }

    /// [`Self::index_conditional_variance_terms_now`] with the market
    /// factor's variance supplied rather than read from the state, so a
    /// forced close can ask what the identity would read at a variance the
    /// state does not hold yet. `_now` passes `market_vol.variance()`, the
    /// same f64 it passed before this existed.
    fn index_conditional_variance_terms_at(
        &self,
        factor_variance: f64,
    ) -> crate::market::index_var::IndexVarianceTerms {
        self.index_conditional_variance_terms_with(
            factor_variance, self.market_vol.prev_day_down(), None)
    }

    /// [`Self::index_conditional_variance_terms_at`] with the lagged
    /// transmission wire's down bit and the names' GARCH variances supplied,
    /// for a projection of the close (`rate_intraday_live`).
    fn index_conditional_variance_terms_with(
        &self,
        factor_variance: f64,
        prev_day_down: bool,
        garch: Option<&[f64]>,
    ) -> crate::market::index_var::IndexVarianceTerms {
        let names = self.index_variance_names_with(garch);
        let sector_sigma = crate::market::tick::sector_sigma_at(
            &self.params, &self.economy, self.vix_anchor);
        let ratio = self.economy.vix / self.vix_anchor;
        let rate_scale = if self.params.jump_vix_coupling == 0.0 {
            1.0
        } else {
            (1.0 - self.params.jump_vix_coupling)
                + ((self.params.jump_vix_coupling * ratio) * ratio)
        };
        let crisis_spike = crate::market::tick::crisis_spike_for(
            &self.params, self.economy.vix, self.universe_stress);
        // The two per-component states, empty when their dials are 0.0, in
        // which case the read-back prices the shared sigma and the
        // independent arrivals exactly as before.
        let sigmas = if self.sector_state_on() {
            self.sector_sigmas_now()
        } else {
            vec![sector_sigma; self.sector_keys.len()]
        };
        let excitations = self.index_variance_excitations();
        let idio_ratios = self.index_variance_idio_ratios();
        crate::market::index_var::index_conditional_variance_terms_with_states(
            &self.params,
            &names,
            self.sector_keys.len(),
            factor_variance,
            &sigmas,
            &excitations,
            &idio_ratios,
            rate_scale,
            crisis_spike,
            // THE LAGGED TRANSMISSION WIRE, and the timing is the point.
            // `close_day_at` has already rolled `day_factor` into
            // `prev_day_factor`, so this bit is the one TOMORROW's ticks
            // will read — which is what a one-day-ahead variance needs. The
            // same accessor the tick calls, not a copy of the comparison.
            prev_day_down,
            crate::market::index_var::intraday_variance_factor(),
        )
    }

    /// The terms of the index variance the last VIX update read, or `None`
    /// if no day has advanced under the identity. See
    /// [`Engine::last_index_variance`] for why it is remembered rather
    /// than recomputed.
    pub fn last_index_variance(
        &self,
    ) -> Option<crate::market::index_var::IndexVarianceTerms> {
        self.last_index_variance
    }

    /// The variance targets the last close reverted toward: `(fast, slow)`.
    ///
    /// `None` before any close. The SLOW element is `None` whenever the
    /// preset has no slow component (`market_vol_slow_weight == 0.0`,
    /// which pt-v1 through pt-v3 and `PT_V1` all ship) — on that branch
    /// `factor_vol.rs` returns before a slow target is ever computed, so
    /// there is none to report. On such a preset the FIRST element is THE
    /// target, not a "fast" one.
    ///
    /// So a `None` in the outer option and a `None` in the inner one mean
    /// different things: no close yet, versus no slow component. A fork
    /// carries the reading (`Engine` derives `Clone`); a restore does not,
    /// because the snapshot has no such key — which is what keeps this out
    /// of the state hash — so a restored engine keeps its own last reading
    /// until its next day.
    pub fn market_variance_target(&self) -> Option<(f64, Option<f64>)> {
        self.last_market_targets
    }

    /// Draw the day-zero cycle phase and its age from the cycle's own
    /// stationary law, and say whether it drew.
    ///
    /// A BRANCH at 0.0, which is every preset before pt-v19: it returns
    /// before touching the economy or the generator, so construction is
    /// what it always was and no arithmetic changes. That is what makes
    /// bit-identity a property of the control flow rather than of
    /// floating-point luck -- and it is asserted anyway, over 9,000 daily
    /// returns on thirty seeds, against a digest taken from a build of the
    /// commit this branch was cut from, by
    /// `tests/test_stationary_opening.py`.
    ///
    /// The uniforms come from the ECONOMY substream, the stream
    /// [`Engine::burn_in_economy`] already consumes, so the market's
    /// day-zero draws sit exactly where they sat and only this domain's
    /// sequence moves. Two draws, counted through the same `Counting`
    /// wrapper the daily step uses, so `draws_consumed` records them.
    ///
    /// They are recorded at [`Site::EconomyCycle`], which is the site they
    /// belong to: what is being drawn is the cycle's own transition law,
    /// read as a distribution instead of rolled forward one day at a time.
    /// No new site is declared, so the draw-schedule registry in
    /// `python/tradefloor/noise.py` is unchanged.
    ///
    /// See [`ModelParams::cycle_stationary_opening`] for the identity, why
    /// the AGE is the part that cannot be skipped, and why the field
    /// relaxation is `macro_burn_in_days`' half of the same opening.
    fn draw_stationary_opening(&mut self) -> bool {
        if self.params.cycle_stationary_opening == 0.0 {
            return false;
        }
        let mut rng = std::mem::replace(&mut self.economy_rng, GameRng::new(0, MAIN_STREAM));
        let mut counting = Counting {
            inner: &mut rng,
            count: 0,
        };
        counting.site(Site::EconomyCycle, 0);
        let u_phase = counting.next_f64();
        let u_age = counting.next_f64();
        let consumed = counting.count;
        self.economy_rng = rng;
        self.draws.economy += consumed;

        let (phase, months) = crate::economy::stationary_opening_for(
            &self.cycle_spec(),
            u_phase,
            u_age,
        );
        self.economy.cycle_phase = phase;
        self.economy.months_in_current_phase = months;
        true
    }

    /// Advance the economy alone to the state its own dynamics reach,
    /// before day zero.
    ///
    /// A BRANCH at 0.0 days, which is every preset before pt-v18: nothing
    /// runs, nothing is drawn, and construction is what it always was.
    ///
    /// # What it fixes
    ///
    /// The economy opens at unemployment 4.00, inflation 2.00 and a
    /// corporate yield of 4.56, and settles at 2.50, 2.74 and 4.82. So a
    /// certified year is spent travelling rather than at rest, and the
    /// travel is a one-way cost to the multiple. The length is measured
    /// rather than round: the last field to enter one stationary standard
    /// deviation of its mean is the corporate yield, on day 755.
    ///
    /// # Three things it does deliberately
    ///
    /// The draws come from the economy's own substream, so the market's
    /// draws for day 0 onward sit exactly where they did. A burn-in
    /// consumes economy draws by running the economy, which is the
    /// mechanism rather than a side effect of it.
    ///
    /// The phase is held for the burn-in's length, because under the
    /// hazard as written 755 days would leave an expansion. It is held by
    /// restoring BOTH the phase and its age whenever a transition fires,
    /// rather than the phase alone. Restoring the phase alone leaves
    /// `months_in_current_phase` at zero, which fires the phase-change
    /// shock on the next day and lifts growth half a point above its
    /// target for the rest of the burn-in. The transition roll still
    /// happens and still consumes its uniform either way.
    ///
    /// The clock is reset to zero at the end, so the year opens at the
    /// start of a phase as every certified year has. Keeping the burn-in's
    /// age would open a random distance into an expansion, which is a
    /// different certified year and a choice rather than a correction.
    ///
    /// # Both of those stop, and only when the opening is DRAWN
    ///
    /// Holding the phase and resetting the clock restore the same point
    /// this burn-in started from, which is the defect
    /// [`ModelParams::cycle_stationary_opening`] exists for: the fields
    /// settle and the cohort survives. When that dial has drawn the phase
    /// and its age, the burn-in runs FREE -- the phase is not held and the
    /// clock is not reset -- because the chain is already in its
    /// stationary law, which a free run preserves by definition, and what
    /// is left to do is relax the fields under the phase path the run has
    /// just lived through. Holding a drawn contraction for 755 days
    /// instead would drive unemployment and the policy rate far past any
    /// state a contraction reaches, and the fields would open inconsistent
    /// with the mix.
    ///
    /// At the shipped default the dial draws nothing and every line here
    /// is the line that stood before it.
    ///
    /// # The growth term's base
    ///
    /// `gdp` and `cpi` compound on every one of these days, so the base of
    /// `earnings_nominal_growth` is re-read at the end. Left at the
    /// pre-burn-in value it would open every valuation about nine per cent
    /// above its earnings, which is a level jump nobody asked for and
    /// which the ratio was never meant to carry.
    fn burn_in_economy(&mut self) -> Vec<(crate::economy::CyclePhase, f64)> {
        // The day-zero phase and its age first, so what follows relaxes
        // the fields under the phase the run will OPEN in. Inert at the
        // default, where it returns without drawing and every line below
        // is the line that stood here.
        //
        // The economy runs alone from here to the end of the burn-in, so the
        // cycle reads `cycle_equity_hazard_opening` for the index it does
        // not have (0.0 on every preset, where nothing changes).
        self.economy_alone = true;
        let drawn = self.draw_stationary_opening();
        // The market's belief on the phase the burn-in opens in; the
        // construction puts it back on the opening phase afterwards. Nothing
        // with `cycle_nowcast_accuracy` at 0.0.
        self.seed_cycle_nowcast();
        if self.params.macro_burn_in_days <= 0.0 {
            self.economy_alone = false;
            return Vec::new();
        }
        let days = self.params.macro_burn_in_days as i64;
        let phase = self.economy.cycle_phase;
        // The phase after each day, for the market's prehistory
        // (`market_prehistory_sessions`); nothing is kept with it at 0.0.
        let keep = self.params.market_prehistory_sessions > 0.0;
        let mut path = Vec::with_capacity(if keep { days as usize } else { 0 });
        for day in 1..=days {
            let months_before = self.economy.months_in_current_phase;
            self.advance_day(&DayAdvanceRequest {
                volatility: 1.0,
                active_shocks: &[],
                market_return_pct: 0.0,
                game_day: day,
                timestamp: day * 24 * 60,
            });
            if !drawn && self.economy.cycle_phase != phase {
                self.economy.cycle_phase = phase;
                self.economy.months_in_current_phase =
                    months_before + 1.0 / self.macro_calendar().month_f64();
            }
            if keep {
                path.push((self.economy.cycle_phase, self.economy.months_in_current_phase));
            }
        }
        if !drawn {
            self.economy.months_in_current_phase = 0.0;
            // The clock the run opens on, which the held burn-in resets.
            if let Some(last) = path.last_mut() {
                last.1 = 0.0;
            }
        }
        // PUT THE CALENDARS BACK ON THE CALLER'S AXIS.
        //
        // The loop above ran on the caller's own day numbering, 1..=days
        // with `timestamp: day * 24 * 60`, so every clock the macro chain
        // keeps as an ABSOLUTE time came out of it `days` in the caller's
        // FUTURE. The run then starts at day 1 (`day_count` is 0 at
        // construction and the first close makes it 1), and each of those
        // clocks is read as a difference against the current day:
        // `update_central_bank` refuses a meeting while
        // `current_timestamp < next_meeting_date`, and the OPEC arm fires on
        // `day - oil_last_opec_day >= OIL_OPEC_INTERVAL`.
        //
        // Measured on `pt-v18` before this, against `pt-v16`: the first
        // central-bank meeting moved from day 45 to day 781 and the first
        // OPEC decision from day 90 to day 810, so 13 meetings landed in
        // 1,400 days where pt-v16 held 29 -- and the certified 252-day
        // horizon contained no monetary policy and no OPEC decision at all.
        // Two macro subsystems, inert for the whole window every published
        // figure is measured on.
        //
        // This is the same restoration the phase lines above perform and it
        // was missing: this function's contract, in `macro_burn_in_days`'
        // own docstring, is that it "HOLDS the phase and RESETS its clock,
        // so it restores the same point after settling the fields". The
        // cycle's two fields were restored and these three were not.
        //
        // SHIFTED, not reset. The burn-in's whole purpose is that the fields
        // arrive settled, and the meeting cadence is one of them: an opening
        // that settles into an inflation crisis carries the 21-30 day
        // cadence `update_central_bank` sets there, where resetting to the
        // construction value would hand day 1 a calm calendar under crisis
        // fields. Every consumer reads a DIFFERENCE, so translating the
        // origin preserves each interval exactly. It is what running the
        // burn-in on days `-days..=0` would have produced, without moving
        // the burn-in's own trajectory to get there.
        let elapsed_minutes = days * 24 * 60;
        self.central_bank.next_meeting_date -= elapsed_minutes;
        self.central_bank.last_meeting_date -= elapsed_minutes;
        self.economy.oil_last_opec_day -= days;
        self.nominal_output_base = self.economy.gdp * self.economy.cpi;
        self.economy_alone = false;
        path
    }

    /// The model coefficients this engine runs. Read-only: an engine's
    /// model is fixed at construction, so its fingerprint describes the
    /// whole run rather than the moment someone asked.
    pub fn params(&self) -> &ModelParams {
        &self.params
    }

    /// `self.params().fingerprint()`, worked out once per engine. A shipped
    /// preset's name, or `custom-XXXXXXXX`. See the field for why it is
    /// kept.
    pub fn model_fingerprint(&self) -> &str {
        self.model_fingerprint.get_or_init(|| self.params.fingerprint())
    }

    /// The macro calendar `macro_calendar_days_per_year` selects.
    pub fn macro_calendar(&self) -> crate::economy::MacroCalendar {
        crate::economy::MacroCalendar::from_days_per_year(self.params.macro_calendar_days_per_year)
    }

    /// The clock and phase table the business cycle reads.
    pub fn cycle_spec(&self) -> crate::economy::CycleSpec {
        crate::economy::CycleSpec {
            per_month: self.params.cycle_hazard_per_month,
            month_days: self.macro_calendar().month_f64(),
            us: self.params.cycle_us_calibration != 0.0,
            equity_hazard: self.params.cycle_equity_hazard,
            equity_knee: self.params.cycle_equity_hazard_knee,
            equity_opening: if self.economy_alone {
                self.params.cycle_equity_hazard_opening
            } else {
                0.0
            },
        }
    }

    /// The VIX at which every variance coupling reads one. See the field.
    ///
    /// Read-only and fixed for the engine's life, like `params`: it is
    /// derived once from the roster and the coefficients, so quoting it
    /// describes the whole run.
    pub fn vix_anchor(&self) -> f64 {
        self.vix_anchor
    }

    // ── Draw delegation ───────────────────────────────────────────────────

    /// Take one uniform from the EXTERNAL stream, on the embedder's behalf.
    ///
    /// The embedder's own subsystems — events, corporate actions, earnings —
    /// drew from the engine's one shared stream in the reference, which
    /// meant an extra event roll on the embedder's side moved every price.
    /// Since the stream split these draws come from a substream of the same
    /// root seed: still fully seed-determined and reproducible, but taking
    /// one — or a thousand — leaves the market's own sequence untouched.
    /// That isolation is what lets an embedder change what IT rolls without
    /// invalidating every seeded market trajectory it embeds.
    pub fn draw_uniform(&mut self) -> f64 {
        self.draws.external += 1;
        self.external_rng.site(Site::External, 0);
        self.external_rng.next_f64()
    }

    /// Take one normal from the EXTERNAL stream. See
    /// [`Engine::draw_uniform`]; the Box-Muller spare involved is the
    /// external stream's own and is never visible to the market.
    pub fn draw_normal(&mut self) -> f64 {
        self.draws.external += 1;
        self.external_rng.site(Site::External, 0);
        self.external_rng.next_normal()
    }

    /// The exact position of all three streams.
    ///
    /// The half of a checkpoint that cannot be reconstructed from the roster.
    /// Every other piece of engine state -- prices, GARCH variance, maker
    /// inventory, the mispricing carry -- is observable through `column()`, so
    /// an embedder that persists its own instruments already has it. The
    /// stream positions are not observable that way, and without them a
    /// restored market continues from a different sequence while looking
    /// correct.
    pub fn rng_state(&self) -> EngineRngState {
        EngineRngState {
            market: self.market_rng.snapshot(),
            economy: self.economy_rng.snapshot(),
            external: self.external_rng.snapshot(),
            jumps: self.jump_rng.snapshot(),
            volume: self.volume_rng.snapshot(),
            volume_idio: self.volume_idio_rng.snapshot(),
            news: self.news_rng.snapshot(),
            overnight: self.overnight_rng.snapshot(),
            market_vol_level: self.market_vol_level_rng.snapshot(),
            crisis_epicentre: self.crisis_epicentre_rng.snapshot(),
        }
    }

    /// Put all three generators back to a captured position.
    ///
    /// Deliberately narrow: it restores the STREAMS and nothing else. Company
    /// state is the caller's to restore, because the caller is the one that
    /// persisted it. A method that pretended to restore everything would have
    /// to be kept in step with every field ever added to a company, and would
    /// fail silently the first time it was not.
    pub fn set_rng_state(&mut self, state: EngineRngState) {
        self.market_rng = GameRng::restore(state.market);
        self.economy_rng = GameRng::restore(state.economy);
        self.external_rng = GameRng::restore(state.external);
        self.jump_rng = GameRng::restore(state.jumps);
        self.volume_rng = GameRng::restore(state.volume);
        self.volume_idio_rng = GameRng::restore(state.volume_idio);
        self.news_rng = GameRng::restore(state.news);
        self.overnight_rng = GameRng::restore(state.overnight);
        self.market_vol_level_rng = GameRng::restore(state.market_vol_level);
        self.crisis_epicentre_rng = GameRng::restore(state.crisis_epicentre);
    }

    /// Cumulative draws across all three streams. The per-stream split is
    /// [`Engine::draws_by_stream`].
    pub fn draws_consumed(&self) -> usize {
        self.draws.total()
    }

    /// Cumulative draws, per stream. Diagnostic (D-R1): equality of the
    /// market counts between two runs is what "the two markets saw the same
    /// noise" means operationally.
    pub fn draws_by_stream(&self) -> StreamDraws {
        self.draws
    }

    // ── Draw addressing ───────────────────────────────────────────────────

    fn stream_rng(&self, id: u32) -> &GameRng {
        match id {
            stream::MARKET => &self.market_rng,
            stream::ECONOMY => &self.economy_rng,
            stream::EXTERNAL => &self.external_rng,
            stream::JUMPS => &self.jump_rng,
            stream::VOLUME => &self.volume_rng,
            stream::NEWS => &self.news_rng,
            stream::VOLUME_IDIO => &self.volume_idio_rng,
            stream::OVERNIGHT => &self.overnight_rng,
            stream::MARKET_VOL_LEVEL => &self.market_vol_level_rng,
            stream::CRISIS_EPICENTRE => &self.crisis_epicentre_rng,
            _ => panic!("unknown stream {id}"),
        }
    }

    fn stream_rng_mut(&mut self, id: u32) -> &mut GameRng {
        match id {
            stream::MARKET => &mut self.market_rng,
            stream::ECONOMY => &mut self.economy_rng,
            stream::EXTERNAL => &mut self.external_rng,
            stream::JUMPS => &mut self.jump_rng,
            stream::VOLUME => &mut self.volume_rng,
            stream::NEWS => &mut self.news_rng,
            stream::VOLUME_IDIO => &mut self.volume_idio_rng,
            stream::OVERNIGHT => &mut self.overnight_rng,
            stream::MARKET_VOL_LEVEL => &mut self.market_vol_level_rng,
            stream::CRISIS_EPICENTRE => &mut self.crisis_epicentre_rng,
            _ => panic!("unknown stream {id}"),
        }
    }

    /// The market jump's effective daily intensity, here and now.
    ///
    /// The threshold `apply_jumps` compares its market uniform against,
    /// at this engine's dials and its current VIX. A caller that wants to
    /// know whether a jump can fire asks for this rather than restating
    /// the scaling: a Python copy of it had already diverged on a zero
    /// anchor, where the copy returned the base intensity and this
    /// divides by zero, and CONTRIBUTING keeps a modelling decision in
    /// one place for exactly that reason.
    ///
    /// Takes no draw and changes nothing.
    ///
    /// It has to agree with `apply_jumps`, and
    /// `test_the_intensity_is_the_threshold_the_jump_fires_on` holds the
    /// two together by behaviour: a market uniform patched just under it
    /// fires and one just over it does not.
    #[rustfmt::skip]
    #[allow(clippy::let_and_return, reason = "the body is generated by tools/mechanism/emit.py, which binds every mechanism output by name")]
    pub fn market_jump_intensity(&self) -> f64 {
        // mechanism:jumps.intensity_market begin -- generated by tools/mechanism/emit.py from
        // tools/mechanism/mechanisms/jumps.py; do not edit by hand.
        let p = &self.params;
        let ratio = self.economy.vix / self.vix_anchor;
        let rate_scale = if p.jump_vix_coupling == 0.0 { 1.0 } else { (1.0 - p.jump_vix_coupling) + ((p.jump_vix_coupling * ratio) * ratio) };
        let intensity_market = if p.jump_vix_coupling == 0.0 { p.jump_intensity_market } else { p.jump_intensity_market * rate_scale };
        intensity_market
        // mechanism:jumps.intensity_market end
    }

    /// The day the next `open_market` opens. The engine cannot know it on
    /// its own: `run_session` and `tick` take no day, and only the caller
    /// that numbers its days does.
    ///
    /// Sets both of the engine's day numbers: the label the draw log, the
    /// day marks and the book's fill stamps carry, and the elapsed trading
    /// days the valuation reads (`elapsed_days`). A caller of the core that
    /// counts its days from zero, which is what this has always asked of
    /// one, sees one number and the behaviour it always had.
    ///
    /// It was a label until pt-v18. `buyback_payout_share` made the number
    /// the valuation's clock as well: the buyback factor is an earnings
    /// yield over elapsed time. With one field serving both, a label moved
    /// the market. `run_days(first_day=1000)` on pt-v20 priced every name as
    /// though a thousand days had passed, 0.17 in log price within thirty
    /// days, and the log recorded no input that said so. So the two are two
    /// fields now, and [`Engine::set_day_label`] moves the label alone.
    ///
    /// `open_market` is the only caller that must run before a draw is
    /// taken, because the day mark and the day's news draws are taken
    /// there, and `PyEngine::restore_state` sets both numbers for the
    /// mid-day case that no open follows.
    ///
    /// # A host numbers its days
    ///
    /// Nothing else advances the clock: not `open_market`, not a session
    /// and not `close_day`. A host that runs its own day loop calls this
    /// with the trading day, counted from zero, before each `open_market`,
    /// as the Python package's `run_days` does. Without it the valuation's
    /// clock stays at day zero for the whole run. On a preset with an
    /// earnings calendar, dividends or buybacks (pt-v21) the calendars stop
    /// there: a name whose report or ex-dividend date falls on day zero
    /// reports, or goes ex, at every session, every other name never does,
    /// and the buyback yield never accrues. Measured on pt-v21, 108 names,
    /// 20 seeds, 504 sessions: the median name's volatility reads 19.7 per
    /// cent a year with the clock left at zero and 20.8 with the days
    /// numbered.
    pub fn set_current_day(&mut self, day: i64) {
        self.set_day_label(day);
        self.elapsed_days = day;
    }

    /// Move the label alone: the day the draw log, the day marks and the
    /// book's fill stamps carry. Nothing that prices reads it.
    pub fn set_day_label(&mut self, day: i64) {
        self.current_day = day;
        for id in 0..stream::COUNT as u32 {
            self.stream_rng_mut(id).set_day(day);
        }
    }

    /// Set the valuation's clock alone: trading days elapsed at the open of
    /// the current day. See `elapsed_days` on the struct.
    pub fn set_elapsed_days(&mut self, days: i64) {
        self.elapsed_days = days;
    }

    pub fn current_day(&self) -> i64 {
        self.current_day
    }

    /// Trading days elapsed at the open of the current day, the number the
    /// valuation reads.
    pub fn elapsed_days(&self) -> i64 {
        self.elapsed_days
    }

    /// The ticks run on the current day: the tick a fill is stamped with.
    pub fn session_ticks(&self) -> u32 {
        self.ticks_today()
    }

    /// Put back the ticks a restored day had run, for the fills stamped
    /// before the next open. See `carried_ticks` on the struct.
    pub fn set_session_ticks(&mut self, ticks: u32) {
        self.carried_ticks = Some(ticks);
    }

    /// `(uniforms, normals)` taken so far on each stream, by stream id.
    pub fn stream_positions(&self) -> [(u64, u64); stream::COUNT] {
        let mut out = [(0u64, 0u64); stream::COUNT];
        for (id, slot) in out.iter_mut().enumerate() {
            *slot = self.stream_rng(id as u32).positions();
        }
        out
    }

    /// Install one substitution at `(stream, kind, index)`.
    pub fn patch_draw(&mut self, stream_id: u32, kind: DrawKind, index: u64, value: f64) {
        self.stream_rng_mut(stream_id).patch(kind, index, value);
    }

    pub fn draw_overlay(&self, stream_id: u32) -> Option<&DrawOverlay> {
        self.stream_rng(stream_id).overlay()
    }

    pub fn set_draw_overlay(&mut self, stream_id: u32, overlay: Option<DrawOverlay>) {
        self.stream_rng_mut(stream_id).set_overlay(overlay);
    }

    /// Record every draw one stream takes on days in `[from_day, to_day]`.
    pub fn enable_draw_log(&mut self, stream_id: u32, from_day: i64, to_day: i64) {
        self.stream_rng_mut(stream_id).enable_log(from_day, to_day);
    }

    /// Drop what every stream's log holds, keeping each range.
    ///
    /// Recording only: the counters, the ranges and every generator's
    /// position are untouched, so a run continues identically. It is for
    /// a copy that will never be asked what it recorded, which is what
    /// the explanation store keeps.
    pub fn clear_draw_log_records(&mut self) {
        for id in 0..stream::COUNT as u32 {
            self.stream_rng_mut(id).clear_log_records();
        }
    }

    pub fn draw_log(&self, stream_id: u32) -> &[DrawRecord] {
        match self.stream_rng(stream_id).log() {
            Some(log) => &log.records,
            None => &[],
        }
    }

    pub fn day_marks(&self) -> &[DayMark] {
        &self.day_marks
    }

    /// Drop every day mark.
    ///
    /// The marks describe the days THIS engine opened, so a restore has to
    /// drop them: restoring a snapshot replaces the run, and marks kept
    /// across it named days the restored engine never ran. An engine that
    /// ran two days, restored a three-day snapshot and ran two more reported
    /// marks for days 0, 1, 3 and 4. `market_day_layout` reads the newest
    /// match and so resolved correctly throughout, which is why this was
    /// invisible. The marks are per-run diagnostic state and a snapshot does
    /// not carry them, so a restored engine starts with none and gains one
    /// per day it opens itself.
    pub fn clear_day_marks(&mut self) {
        self.day_marks.clear();
    }

    /// Where each active company's market-stream normals sit on `day`,
    /// from that day's mark and the per-tick schedule in [`DayMark`].
    pub fn market_day_layout(&self, day: i64) -> Option<Vec<MarketDayLayout>> {
        let mark = self.day_marks.iter().rev().find(|m| m.day == day)?;
        let per_tick = 1 + mark.sectors as u64 + mark.active.len() as u64;
        let base = mark.positions[stream::MARKET as usize].1;
        Some(
            mark.active
                .iter()
                .enumerate()
                .map(|(k, &company)| MarketDayLayout {
                    company,
                    first: base + 1 + mark.sectors as u64 + k as u64,
                    stride: per_tick,
                    ticks: mark.ticks,
                })
                .collect(),
        )
    }

    // ── The tick ──────────────────────────────────────────────────────────

    /// Run one simulated minute.
    ///
    /// A closed market costs zero draws and changes nothing — the guard is
    /// inside [`simulate_market_tick`] and precedes every draw site, which
    /// matters because most of the clock is closed.
    pub fn tick(&mut self, request: &TickRequest) -> TickOutcome {
        // The population acts first, on an open tick, and only on an engine
        // that holds one (`crate::population`).
        let population_open = self.population.is_some()
            && get_market_status(request.time) == MarketStatus::Open;
        if population_open {
            self.population_before_tick();
        }
        // The market generator is moved out, used, and moved back.
        // `tick_inner` takes `&mut self`, so it cannot also borrow
        // `self.market_rng` — and swapping is clearer than duplicating the
        // tick body for the two cases. The placeholder is never drawn from:
        // the real generator is restored before this method returns.
        let mut rng = std::mem::replace(&mut self.market_rng, GameRng::new(0, MAIN_STREAM));
        let mut counting = Counting {
            inner: &mut rng,
            count: 0,
        };
        let mut outcome = self.tick_inner(request, &mut counting, SettleDrawPolicy::FourAlways);
        let consumed = counting.count;
        self.market_rng = rng;
        self.draws.market += consumed;
        outcome.draws_consumed = consumed;
        if let Some(mark) = self.day_marks.last_mut() {
            mark.ticks += 1;
        } else if let Some(ticks) = self.carried_ticks.as_mut() {
            // A restored day with no mark of its own: counted here, so the
            // fills of its later ticks carry the tick the original's did.
            *ticks += 1;
        }
        // The rate indices' minute, after the equities' and reading nothing
        // they wrote: the economy does not move inside a tick, and the flow
        // an instrument reads is keyed by its own ticker, which no equity
        // carries. No draw.
        //
        // Under `rate_intraday_live` the minute's print is around the live
        // mark, the published curve plus what the session so far adds to
        // tonight's expected curve, refreshed on the grid.
        //
        // Under `vix_intraday_live` the live VIX refreshes on the same grid,
        // from the same projection of the close when both are due. It reads
        // the state and writes only its own mark.
        let open = crate::market::get_market_status(request.time) == crate::market::MarketStatus::Open;
        let rate_live_on = self.params.rate_intraday_live != 0.0 && !self.rates.is_empty();
        let vix_live_on = self.params.vix_intraday_live != 0.0;
        if open && (rate_live_on || vix_live_on) {
            let elapsed = (request.time.hour - 9) * 60 + request.time.minute - 30 + 1;
            let on_grid = elapsed <= 1 || elapsed % RATE_LIVE_REFRESH_MINUTES == 0;
            let rates_due = rate_live_on && (on_grid || self.rate_live.is_none());
            let vix_due = vix_live_on && (on_grid || self.vix_live.is_none());
            self.refresh_live_marks(session_variance_remaining(elapsed), rates_due, vix_due);
        }
        if !self.rates.is_empty() {
            let live = if rate_live_on && open { self.rate_live_curve() } else { None };
            self.rates.tick_live(request.time, &self.economy, live, request.order_volumes);
        }
        // The index futures' minute (`futures_index_listed`), after
        // everything a price reads: the basis steps on its own stream and
        // resting orders the moved book crosses fill. Nothing with it off.
        if open {
            self.futures_session_step();
            // The VIX futures' minute (`futures_vix_listed`): their flow's
            // mark steps and resting orders the moved books cross fill.
            self.vix_futures_session_step();
            // The rate futures' minute (`futures_rates_listed`).
            self.rate_futures_session_step();
            // The oil futures' minute (`futures_oil_listed`).
            self.oil_futures_session_step();
        }
        if population_open {
            if let Some(pop) = self.population.as_mut() {
                pop.after_tick(&self.companies);
            }
        }
        outcome
    }

    // ── The population (`crate::population`) ──────────────────────────────

    /// Attach a population, built for this engine's roster. Called once,
    /// right after construction.
    pub fn set_population(
        &mut self,
        fingerprint: String,
        participants: Vec<crate::population::Participant>,
    ) -> Result<(), String> {
        let run = crate::population::PopulationRun::new(fingerprint, participants, &self.companies)?;
        self.population = Some(Box::new(run));
        Ok(())
    }

    /// The population this engine holds, if any.
    pub fn population(&self) -> Option<&crate::population::PopulationRun> {
        self.population.as_deref()
    }

    /// Install a population state from a snapshot; the engine must hold the
    /// population it was taken from.
    pub fn set_population_state(&mut self, tickers: Vec<String>, flat: &[f64]) -> Result<(), String> {
        match self.population.as_mut() {
            Some(pop) => pop.set_flat(tickers, flat),
            None => Err("this engine holds no population".to_string()),
        }
    }

    /// The population's turn at the start of an open tick: it rolls the
    /// day, books the agents' flow that reaches this tick, and sends its
    /// orders through the agent-facing book.
    fn population_before_tick(&mut self) {
        let Some(mut pop) = self.population.take() else {
            return;
        };
        pop.align(&self.companies);
        if pop.day != Some(self.current_day) {
            pop.roll_day(self.current_day);
        }
        let tick = self.ticks_today();
        for (agent, ticker, bought, sold) in &self.book.flow {
            if crate::population::is_population_label(agent) {
                continue;
            }
            if let Some(i) = self.companies.iter().position(|c| &c.ticker == ticker) {
                let volume = crate::agent_book::daily_volume(&self.companies[i]);
                pop.observe_flow(i, tick, bought - sold, volume);
            }
        }
        pop.prepare(tick, &self.companies);
        let orders = pop.decide(tick, &self.companies, self.market_vol.sigma_daily(), self.economy.vix);
        for (k, i, signed) in orders {
            let label = pop.participants[k].label();
            if let Some(limit) = pop.max_spread(k) {
                if pop.adds(k, i, signed) {
                    // The quoted spread in daily sigmas. With several
                    // detectors, one reading per name per five-tick window
                    // (`PopulationRun::gates`) serves every detector deciding
                    // in it; infinite when the book is not two-sided.
                    let window = (self.current_day as f64) * (crate::population::SESSION_TICKS / crate::population::GATE_WINDOW) as f64
                        + (tick / crate::population::GATE_WINDOW) as f64;
                    let cached = match pop.gates.get(2 * i..2 * i + 2) {
                        Some(&[x, w]) if w == window => Some(x),
                        _ => None,
                    };
                    let spread = match cached {
                        Some(x) => x,
                        None => {
                            let sigma = crate::agent_book::daily_sigma(&self.companies[i], self.market_vol.sigma_daily());
                            let x = match self.agent_book_at(i, Some(&label)) {
                                Some(book) => match (book.best_bid(), book.best_ask()) {
                                    (Some(b), Some(a)) if a > b && sigma > 0.0 => (a - b) / (0.5 * (a + b)) / sigma,
                                    _ => f64::INFINITY,
                                },
                                None => f64::INFINITY,
                            };
                            if let Some(slot) = pop.gates.get_mut(2 * i..2 * i + 2) {
                                slot[0] = x;
                                slot[1] = window;
                            }
                            x
                        }
                    };
                    if !(spread <= limit) {
                        continue;
                    }
                }
            }
            let side = if signed > 0.0 {
                crate::order_book::Side::Buy
            } else {
                crate::order_book::Side::Sell
            };
            let (fills, _) = self.meet_book_as(i, &label, &label, side, signed.abs(), None, true, false, false, true);
            pop.book_order(k);
            for f in &fills {
                pop.book_fill(k, i, side, f.quantity, f.price);
            }
        }
        // Its fills live on its own ledger, not in what `take_fills` returns
        // (`meet_book` does not record them).
        self.population = Some(pop);
    }

    /// Run one simulated minute against an EXTERNAL draw source.
    ///
    /// This is what a replay harness needs: the engine's own generator cannot
    /// reproduce a recorded the reference implementation stream, because `next_normal` routes
    /// through `cos` and diverges on 1.545% of draws. Feeding recorded draws
    /// separates the arithmetic under test from the generator that is known to
    /// differ.
    ///
    /// Because the source is a RECORDING of the pre-split shared stream,
    /// this path keeps the pre-split schedule: settlement draws four or
    /// zero, exactly as the guards decided when the tape was cut
    /// ([`SettleDrawPolicy::FourOrZero`]). The engine's own generated
    /// schedule ([`Engine::tick`]) draws settlement's four unconditionally.
    ///
    /// `draws_consumed` in the returned outcome is 0 here — the caller owns
    /// the source and already knows what it handed over.
    pub fn tick_with(&mut self, request: &TickRequest, rng: &mut impl Rng) -> TickOutcome {
        self.tick_inner(request, rng, SettleDrawPolicy::FourOrZero)
    }

    fn tick_inner(
        &mut self,
        request: &TickRequest,
        rng: &mut impl Rng,
        settle_draws: SettleDrawPolicy,
    ) -> TickOutcome {
        // The day's endogenous news, chained onto whatever the caller
        // supplied. Here rather than in `run_session` because this is the
        // one function every path reaches: `run_session`'s loop, the public
        // `tick`, `tick_with`, and `EngineBatch::tick`, which builds its own
        // `TickRequest` with `news: &[]` (§117).
        //
        // Taken out of `self` and put back, the same move `tick` makes with
        // the market generator and for the same reason: `tick_inner` holds
        // `&mut self`, so it cannot also borrow a field. At zero intensity
        // the vector is empty and the caller's slice is used untouched, so
        // every preset before pt-v11 allocates nothing and is bit-identical.
        let day_news = std::mem::take(&mut self.session_news);
        let chained: Vec<NewsEvent> = if day_news.is_empty() {
            Vec::new()
        } else {
            // The caller's events first, then ours: the news arm is ORDERED
            // and exclusive, so order decides which branch an event takes.
            let mut v = Vec::with_capacity(request.news.len() + day_news.len());
            v.extend_from_slice(request.news);
            // WHEN the day's move lands. At `news_absorption_half_life` 0.0,
            // every preset through pt-v18, each tick carries the event whole and the tick
            // divides it by 390, so the move lands in a straight line over
            // the session: this branch is the code that always stood. Off
            // zero, each event is carried at this minute's share of the
            // absorption profile, `390 * (A(m + 1) - A(m))`, and the same
            // division prices `A(m + 1) - A(m)` of it. The weight is the
            // same for every event, so the sign, the peer transfer and the
            // day's total are unchanged. Caller-supplied news is not
            // touched: it has no release time the profile could start from.
            if self.params.news_absorption_half_life == 0.0 {
                v.extend(day_news.iter().cloned());
            } else {
                let minutes = (request.time.hour - 9) * 60 + (request.time.minute - 30);
                let w = crate::market::factors::news_absorption_weight(&self.params, minutes);
                v.extend(day_news.iter().map(|e| NewsEvent {
                    company_id: e.company_id.clone(),
                    sector: e.sector.clone(),
                    price_impact: e.price_impact.map(|x| x * w),
                }));
            }
            v
        };
        let request = &TickRequest {
            news: if chained.is_empty() { request.news } else { &chained },
            news_impact_queue: request.news_impact_queue,
            order_volumes: request.order_volumes,
            time: request.time,
            volatility_multiplier: request.volatility_multiplier,
        };
        let outcome = self.tick_body(request, rng, settle_draws);
        self.session_news = day_news;
        outcome
    }

    fn tick_body(
        &mut self,
        request: &TickRequest,
        rng: &mut impl Rng,
        settle_draws: SettleDrawPolicy,
    ) -> TickOutcome {
        let status = get_market_status(request.time);

        // The factor's sigma follows the draw policy because the two mark
        // the same era boundary: the generated schedule belongs to the era
        // whose market factor carries conditional volatility, while
        // `FourOrZero` replays a RECORDED reference stream, and that era
        // drew the factor at constant sigma. Replaying a tape through
        // today's variance state would price recorded draws under dynamics
        // the recording never had.
        let market_sigma_daily = match settle_draws {
            SettleDrawPolicy::FourAlways => self.market_sigma_today(),
            SettleDrawPolicy::FourOrZero => crate::market::tick::MARKET_FACTOR_SIGMA,
        };

        // The per-sector sigmas from the state, or empty (the stateless draw).
        let sector_sigmas = self.sector_sigmas_now();
        // `'static`, so it does not borrow `self` while the tick takes it
        // mutably. `None` on every preset before pt-v19 and outside an
        // episode on pt-v19.
        let epicentre = self.crisis_epicentre_key();
        if !sector_sigmas.is_empty() {
            let t = crate::market::tick::sector_sigma_at(&self.params, &self.economy, self.vix_anchor);
            self.sector_target_day = t * t;
        }
        if !self.opening_z.is_empty() && status != crate::market::MarketStatus::Closed {
            self.apply_opening();
        }
        // The agent-facing book's part of the tick, before the market moves:
        // the maker re-quotes on the inventory agents left it and the
        // consumed depth refills; agents' taker flow reaches the market,
        // once, on the first tick that is not closed; resting orders join
        // the settlement book. None of it runs on an engine no agent has
        // used, which is what keeps every such tick the tick it was.
        let book_on = !self.book.is_pristine();
        let open_now = status == MarketStatus::Open;
        if book_on && open_now {
            self.requote_book();
        }
        let mut merged_flow: Option<Vec<(String, OrderVolume)>> = None;
        let mut fill_impact: Vec<f64> = Vec::new();
        let mut applied: Vec<(String, String, f64, f64)> = Vec::new();
        if book_on && status != MarketStatus::Closed && !self.book.flow.is_empty() {
            applied = std::mem::take(&mut self.book.flow);
            if self.params.fill_impact_coefficient != 0.0 {
                fill_impact = vec![0.0; self.companies.len()];
                for (_, t, b, s) in &applied {
                    if let Some(i) = self.companies.iter().position(|c| &c.ticker == t) {
                        fill_impact[i] += self.fill_impact_of(i, b - s);
                    }
                }
            } else {
                let mut agg: Vec<(String, OrderVolume)> = Vec::new();
                for (_, t, b, s) in &applied {
                    match agg.iter_mut().find(|(x, _)| x == t) {
                        Some((_, v)) => {
                            v.buy += *b;
                            v.sell += *s;
                        }
                        None => agg.push((t.clone(), OrderVolume { buy: *b, sell: *s })),
                    }
                }
                merged_flow = Some(merge_order_volumes(request.order_volumes, &agg));
            }
        }
        // The metaorder memory: decay, feed, and book its displacement into
        // the order-flow slot beside the linear law's. Only with the dial
        // on and the book used, so every other tick is the tick it was.
        let mut memory_plan: Vec<(usize, [f64; crate::agent_book::MEMORY_WIDTH], f64)> = Vec::new();
        // What this tick books into each name's `s` for the memory; read
        // back below if the breaker cut the tick.
        let mut memory_delta: Vec<f64> = Vec::new();
        if book_on && status != MarketStatus::Closed && self.params.impact_memory_coefficient > 0.0 {
            memory_delta = vec![0.0; self.companies.len()];
        }
        if book_on && status != MarketStatus::Closed && self.params.impact_memory_coefficient > 0.0 {
            memory_plan = self.plan_memory(open_now);
            if !memory_plan.is_empty() && fill_impact.is_empty() {
                fill_impact = vec![0.0; self.companies.len()];
            }
            let phi = self.params.s_phi_tick;
            for (i, row, _) in &memory_plan {
                let booked = self.book.memory[*i][crate::agent_book::MEMORY_BOOKED];
                let target = row[crate::agent_book::MEMORY_BOOKED];
                // `s` keeps `phi` of what was booked on an open tick, so
                // the booking tops it back up to the target.
                let delta = if open_now {
                    (target - booked) + (1.0 - phi) * booked
                } else {
                    target - booked
                };
                memory_delta[*i] = delta;
                if delta != 0.0 {
                    fill_impact[*i] += delta;
                }
            }
        }
        let resting: Vec<Vec<crate::microstructure::RestingOrder>> = if book_on
            && open_now
            && self.book.orders.iter().any(|o| o.mode == crate::agent_book::RestMode::Queue)
        {
            let mut per: Vec<Vec<crate::microstructure::RestingOrder>> =
                vec![Vec::new(); self.companies.len()];
            for o in &self.book.orders {
                if o.mode != crate::agent_book::RestMode::Queue {
                    continue;
                }
                if let Some(i) = self.companies.iter().position(|c| c.ticker == o.ticker) {
                    per[i].push(crate::microstructure::RestingOrder {
                        id: o.id.clone(),
                        side: o.side,
                        price: o.limit,
                        quantity: o.remaining,
                        owner_id: o.agent.clone(),
                    });
                }
            }
            per
        } else {
            Vec::new()
        };
        let prints_before: Vec<f64> = if book_on {
            self.companies.iter().map(|c| c.stock.price).collect()
        } else {
            Vec::new()
        };
        let order_volumes: &[(String, OrderVolume)] = match &merged_flow {
            Some(flow) => flow.as_slice(),
            None => request.order_volumes,
        };
        // The reaction session's discovery and the follow-through, this
        // minute's step into fair value, before the tick prices it. Off the
        // calendar nothing runs.
        if open_now && self.carries_earnings() {
            self.walk_earnings_sessions(request.time);
        }
        // Recomputed from the calendar every tick rather than kept, so a
        // fork taken mid-session needs nothing beyond the key. Empty off the
        // calendar.
        let earnings_volume = self.earnings_volume_column();

        // The per-name idiosyncratic variance ratios, one per roster slot,
        // or EMPTY while the state is off, in which case the tick multiplies
        // nothing.
        let idio_vol_ratios: Vec<f64> = if self.idio_state_on() {
            (0..self.companies.len()).map(|i| self.idio_ratio_at(i)).collect()
        } else {
            Vec::new()
        };
        // Read before the tick borrows the companies.
        let market_permanent_ceiling_scale = self.market_vol_cycle_cap_scale();
        let outcome = simulate_market_tick(
            &mut self.companies,
            &TickInputs {
                economy: &self.economy,
                prev_day_down: self.market_vol.prev_day_down(),
                // Read only by `market_beta_down_asym_lag_live`; at its 0.0
                // the tick never looks at either and the tape is unchanged.
                // `day_factor()` is today's accumulator BEFORE the
                // `accumulate` below, which is the point.
                prev_day_factor: self.market_vol.prev_day_factor(),
                // Less tonight's draw under a night split with the live wire
                // on (`night_market_factor`), so the condition keys on the
                // session's own draws; a branch, so at 0.0 this is the read
                // that stood.
                day_factor: if self.night_market_factor == 0.0 {
                    self.market_vol.day_factor()
                } else {
                    self.market_vol.day_factor() - self.night_market_factor
                },
                forced_flow_eff: if self.params.forced_flow_reservoir > 0.0 {
                    crate::mathx::max(
                        0.0,
                        1.0 - self.forced_flow_spent / self.params.forced_flow_reservoir,
                    )
                } else {
                    1.0
                },
                universe_stress: self.universe_stress,
                volume_state: self.volume_state,
                volume_idio: &self.volume_idio,
                jump_move: &self.jump_move,
                earnings_volume: &earnings_volume,
                market_status: status,
                intraday_t: intraday_fraction(request.time),
                volatility_multiplier: request.volatility_multiplier,
                news: request.news,
                news_impact_queue: request.news_impact_queue,
                order_volumes,
                sector_keys: &self.sector_keys,
                sector_sigmas: &sector_sigmas,
                idio_vol_ratios: &idio_vol_ratios,
                market_sigma_daily,
                market_permanent_ceiling_scale,
                vix_anchor: self.vix_anchor,
                settle_draws,
                settle_depth_counterfactual: self.settle_depth_counterfactual,
                resting_orders: &resting,
                fill_impact: &fill_impact,
                nominal_output_base: self.nominal_output_base,
                // Resolved at this session's `open_market` and fixed for
                // the day; `None` on every preset before pt-v19.
                crisis_epicentre: epicentre,
                elapsed_days: self.elapsed_days,
                params: &self.params,
            },
            rng,
        );

        // Accumulate the day's factor innovation for the close's variance
        // update. A closed tick contributes exactly zero (the factor is
        // not drawn), and the replay path accumulates harmlessly into
        // state it never reads.
        // Under a priced VIX move (`pinned_vix_variance_share`) the state
        // reads the draw at the variance it would have had: the priced move
        // stands in for the part of the draw it took.
        self.market_vol.accumulate(self.factor_for_state(outcome.shared_factors.market_factor));
        if self.sector_state_on() {
            for (k, (_, f)) in outcome.shared_factors.sector_factors.iter().enumerate() {
                if let Some(acc) = self.sector_day_factor.get_mut(k) {
                    *acc += *f;
                }
            }
        }

        // Accumulate the APPLIED contributions, which is the same quantity the
        // `truth` table reports per tick -- so the day total of a column here
        // equals that column summed over the day, and the two surfaces cannot
        // disagree about what drove a price.
        //
        // It used to accumulate the RAW factors, and that was wrong in a way
        // that mattered. The three drift factors are divided by 390 before
        // they reach `s` and the noise term is multiplied by the intraday
        // volatility curve, so raw sums overstate news, flow and squeeze by
        // ~390x relative to noise. Anything ranking factors by magnitude --
        // "was this agent right for the right reasons" -- therefore answered
        // `company_news` on days that were overwhelmingly noise. Measured on
        // one session: raw called it news at 6.0e0 against noise at 6.0e-2;
        // applied calls it noise at 7.8e-1 against news at 1.5e-2.
        // Zeroed rather than left stale. A company that did not tick did not
        // move, so every component contributed exactly zero to its `s` -- which
        // is true, and keeps the columns summing to a Δs of zero. Carrying the
        // previous tick's values forward would invent activity.
        for slot in self.tick_components.iter_mut() {
            *slot = [0.0; crate::market::factors::TICK_COMPONENT_COUNT];
        }
        // The print decomposition is zeroed on the same argument and for the
        // same reason: a company that did not tick moved by nothing, so its
        // shock and the depth that absorbed it are both zero. Carrying the
        // previous tick's pair forward would report the same move twice.
        for slot in self.tick_shock.iter_mut() {
            *slot = 0.0;
        }
        for slot in self.tick_absorbed.iter_mut() {
            *slot = 0.0;
        }
        for slot in self.tick_clamp.iter_mut() {
            *slot = 0.0;
        }
        // Every slot's row prints, active or not, so every slot books what
        // was written to its price since the last print, and the pending
        // value is spent. A name that did not tick prints the price it
        // carries, so its row's move is exactly this.
        self.tick_repriced.clear();
        self.tick_repriced.resize(self.companies.len(), 0.0);
        for (slot, pending) in self.repriced_pending.iter_mut().enumerate() {
            if let Some(v) = self.tick_repriced.get_mut(slot) {
                *v = *pending;
            }
            *pending = 0.0;
        }
        // The counterfactual columns are prices, so their zero is the price
        // the company is carrying: unbounded depth does not move a name that
        // did not settle. Written before the copy below so an inactive slot
        // is right, and an active one is overwritten.
        for (slot, value) in self.tick_unbounded_print.iter_mut().enumerate() {
            *value = self
                .companies
                .get(slot)
                .map(|c| c.stock.price)
                .unwrap_or(f64::NAN);
        }
        for slot in self.tick_liquidity_share.iter_mut() {
            *slot = 0.0;
        }
        for (n, slot) in outcome.active_indices.iter().enumerate() {
            if let (Some(acc), Some(computed)) = (
                self.attribution.get_mut(*slot),
                outcome.s_components.get(n),
            ) {
                for (k, value) in computed.iter().enumerate() {
                    acc[crate::market::factors::attribution_slot_for_tick(k)] += value;
                }
            }
            // The GJR's own sum under a night split, the noise slot's
            // increment added in the same order the slot adds it.
            if let (Some(acc), Some(computed)) = (
                self.innovation_day.get_mut(*slot),
                outcome.s_components.get(n),
            ) {
                *acc += computed[random_noise_index()];
            }
            if let (Some(row), Some(computed)) = (
                self.tick_components.get_mut(*slot),
                outcome.s_components.get(n),
            ) {
                *row = *computed;
            }
            // Beside the attribution and on the same guard: a slot the tick
            // did not fill is left alone rather than zeroed.
            if let (Some(acc), Some(computed)) = (
                self.noise_parts.get_mut(*slot),
                outcome.noise_parts.get(n),
            ) {
                for (k, value) in computed.iter().enumerate() {
                    acc[k] += value;
                }
            }
            if let (Some(acc), Some(computed)) = (
                self.noise_own_scale2.get_mut(*slot),
                outcome.noise_own_scale2.get(n),
            ) {
                *acc += *computed;
            }
            // Levels, unlike contributions, PERSIST between ticks: a company
            // that did not trade still has the valuation and anchor it last
            // had, and blanking them would make the columns unusable for
            // exactly the join they exist for.
            if let Some(v) = outcome.fundamental_values.get(n) {
                if let Some(slot_v) = self.tick_fundamental.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
            if let Some(v) = outcome.fair_values.get(n) {
                if let Some(slot_v) = self.tick_anchor.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
            if let Some(v) = outcome.shock.get(n) {
                if let Some(slot_v) = self.tick_shock.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
            if let Some(v) = outcome.absorbed.get(n) {
                if let Some(slot_v) = self.tick_absorbed.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
            if let Some(v) = outcome.clamp.get(n) {
                if let Some(slot_v) = self.tick_clamp.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
            if let Some(v) = outcome.unbounded_print.get(n) {
                if let Some(slot_v) = self.tick_unbounded_print.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
            if let Some(v) = outcome.liquidity_share.get(n) {
                if let Some(slot_v) = self.tick_liquidity_share.get_mut(*slot) {
                    *slot_v = *v;
                }
            }
        }

        // The memory's new state. A name the tick did not settle carries
        // none of the booking into `s`, so its booked part stays where it
        // was and the next tick it settles books the difference.
        let mut memory_transient: Vec<f64> = Vec::new();
        if !memory_plan.is_empty() {
            memory_transient = vec![0.0; self.companies.len()];
            for (i, row, transient) in &memory_plan {
                let settled = outcome.active_indices.iter().position(|x| x == i);
                let slot = &mut self.book.memory[*i];
                slot[crate::agent_book::MEMORY_FAST] = row[crate::agent_book::MEMORY_FAST];
                slot[crate::agent_book::MEMORY_SLOW] = row[crate::agent_book::MEMORY_SLOW];
                // The flow it paid for is in the memory now; a resting
                // order's taker fill below adds the next tick's.
                slot[crate::agent_book::MEMORY_PAID] = 0.0;
                slot[crate::agent_book::MEMORY_FLOW] = 0.0;
                if let Some(n) = settled {
                    // What landed in `s`: the booking less whatever part of
                    // it the model-price breaker took back this tick (its
                    // slot, 7, is the cut). Booking the target regardless
                    // would record a displacement `s` never received, and
                    // unbooking it as the memory decays would push `s` the
                    // other way by the amount cut.
                    let target = row[crate::agent_book::MEMORY_BOOKED];
                    let delta = memory_delta.get(*i).copied().unwrap_or(0.0);
                    let cut = outcome.s_components.get(n).map(|c| c[7]).unwrap_or(0.0);
                    slot[crate::agent_book::MEMORY_BOOKED] = if delta > 0.0 && cut < 0.0 {
                        target - delta + crate::mathx::max(0.0, delta + cut)
                    } else if delta < 0.0 && cut > 0.0 {
                        target - delta + crate::mathx::min(0.0, delta + cut)
                    } else {
                        target
                    };
                    memory_transient[*i] = *transient;
                }
            }
        }

        if book_on {
            self.settle_book(&outcome, &applied, &prints_before, open_now, &memory_transient);
        }

        TickOutcome {
            market_status: status,
            active_indices: outcome.active_indices,
            fair_values: outcome.fair_values,
            volumes: outcome.volumes,
            draws_consumed: 0,
        }
    }

    /// The maker re-quotes: the inventory agents left it lands, its ladder
    /// is whole again, and consumed latent depth refills by one tick.
    fn requote_book(&mut self) {
        use crate::agent_book::*;
        let refill = refill_factor(&self.params);
        for (i, row) in self.book.taken.iter_mut().enumerate() {
            if row[TAKEN_MAKER_INVENTORY] != 0.0 {
                if let Some(c) = self.companies.get_mut(i) {
                    c.stock.maker_inventory =
                        Some(c.stock.maker_inventory.unwrap_or(0.0) + row[TAKEN_MAKER_INVENTORY]);
                }
                row[TAKEN_MAKER_INVENTORY] = 0.0;
            }
            row[TAKEN_MAKER_BID] = 0.0;
            row[TAKEN_MAKER_ASK] = 0.0;
            for k in [TAKEN_DEPTH_BID, TAKEN_DEPTH_ASK] {
                // Below a millionth of a share it is gone, rather than a
                // geometric tail that never reaches zero.
                row[k] = if row[k] * refill < 1e-6 { 0.0 } else { row[k] * refill };
            }
        }
    }

    /// The metaorder memory's step for this tick, per name it touches:
    /// `(slot, [fast, slow, displacement, 0, 0], transient)`. On an open
    /// tick both memories decay first; then the name's net flow against the
    /// house since the last step ([`crate::agent_book::MEMORY_FLOW`]), over
    /// its daily volume, is added to both; the displacement is `D` of the
    /// combined memory; and `transient` is what that flow alone moved it,
    /// `D(after the flow) - D(decayed)`. A name whose memory is empty is
    /// left out.
    ///
    /// Only flow the house took the other side of feeds the memory: a fill
    /// between two agents moves no liquidity the book has to refill, and
    /// the pair's cash nets to zero, so counting the taker's side would let
    /// two agents walk the tape for nothing (a wash: one rests an ask, the
    /// other lifts it).
    #[allow(clippy::type_complexity)]
    fn plan_memory(&mut self, open_now: bool) -> Vec<(usize, [f64; crate::agent_book::MEMORY_WIDTH], f64)> {
        use crate::agent_book::*;
        let n = self.companies.len();
        if self.book.memory.iter().all(|r| r.iter().all(|x| *x == 0.0)) {
            return Vec::new();
        }
        if self.book.memory.len() < n {
            self.book.memory.resize(n, [0.0; MEMORY_WIDTH]);
        }
        let (delta, _) = depth_shape(&self.params);
        let (fast, slow) = if open_now {
            (memory_decay(self.params.impact_memory_half_life),
             memory_decay(self.params.impact_memory_slow_half_life))
        } else {
            (1.0, 1.0)
        };
        let snap = |x: f64| if x.abs() < 1e-12 { 0.0 } else { x };
        let market_sigma = self.market_vol.sigma_daily();
        let mut plan = Vec::new();
        for i in 0..n {
            let row = self.book.memory[i];
            if row.iter().all(|x| *x == 0.0) {
                continue;
            }
            let v = daily_volume(&self.companies[i]);
            let shares = row[MEMORY_FLOW];
            let net = if v > 0.0 { shares / v } else { 0.0 };
            let sigma = daily_sigma(&self.companies[i], market_sigma);
            let mut next = row;
            next[MEMORY_FAST] = snap(row[MEMORY_FAST] * fast);
            // The slow memory runs only with its half-life set; without it
            // it is carried at zero.
            next[MEMORY_SLOW] = if self.params.impact_memory_slow_half_life > 0.0 {
                snap(row[MEMORY_SLOW] * slow)
            } else {
                0.0
            };
            let at = memory_position(&self.params, &next);
            let decayed = memory_displacement(&self.params, at, sigma);
            next[MEMORY_PAID] = 0.0;
            next[MEMORY_FLOW] = 0.0;
            if net != 0.0 {
                // The flow moves the displacement at most (1 + delta) times
                // what it paid per share, so selling back along the
                // memory's path never recovers more than the flow cost.
                let mut add = net;
                let cap = (1.0 + delta) * row[MEMORY_PAID] / shares.abs();
                let uncapped = memory_displacement(&self.params, at + add, sigma);
                if (uncapped - decayed).abs() > cap {
                    let target = decayed + if net > 0.0 { cap } else { -cap };
                    add = memory_inverse(&self.params, target, sigma) - at;
                }
                next[MEMORY_FAST] += add;
                if self.params.impact_memory_slow_half_life > 0.0 {
                    next[MEMORY_SLOW] += add;
                }
            }
            let d = memory_displacement(&self.params, memory_position(&self.params, &next), sigma);
            next[MEMORY_BOOKED] = d;
            plan.push((i, next, d - decayed));
        }
        plan
    }

    /// `impact_memory_refill`: a resting order filled on the side against
    /// the memory's lean (by the market's flow, or crossed during the
    /// session) is new depth where the lean consumed it, so its `quantity`
    /// shares come off the memory, uncapped and never past zero. On the
    /// side the memory leans it changes nothing. Returns the shares the
    /// memory absorbed. The next tick books the new displacement into `s`
    /// as it books any change of the memory.
    fn refill_memory(&mut self, index: usize, side: crate::order_book::Side, quantity: f64) -> f64 {
        use crate::agent_book::*;
        use crate::order_book::Side;
        let Some(row) = self.book.memory.get(index).copied() else {
            return 0.0;
        };
        let m = memory_position(&self.params, &row);
        let against = match side {
            Side::Sell => m > 0.0,
            Side::Buy => m < 0.0,
        };
        let v = daily_volume(&self.companies[index]);
        if !against || !(v > 0.0) || !(quantity > 0.0) {
            return 0.0;
        }
        let r = crate::mathx::min(quantity / v, m.abs());
        let dr = if m > 0.0 { -r } else { r };
        let slot = &mut self.book.memory[index];
        slot[MEMORY_FAST] += dr;
        if self.params.impact_memory_slow_half_life > 0.0 {
            slot[MEMORY_SLOW] += dr;
        }
        for k in [MEMORY_FAST, MEMORY_SLOW] {
            if slot[k].abs() < 1e-12 {
                slot[k] = 0.0;
            }
        }
        crate::mathx::min(quantity, r * v)
    }

    /// The linear law's change to one name's `s` for a net signed size:
    /// `gamma * sigma * net / V`.
    fn fill_impact_of(&self, index: usize, net: f64) -> f64 {
        let c = &self.companies[index];
        let sigma = crate::agent_book::daily_sigma(c, self.market_vol.sigma_daily());
        self.params.fill_impact_coefficient * sigma * net / crate::agent_book::daily_volume(c)
    }

    /// After the market moved: attribute the flow that was applied, record
    /// the fills of resting orders in the settlement, and fill any waiting
    /// limit the tick's print reached.
    fn settle_book(
        &mut self,
        outcome: &crate::market::TickOutcome,
        applied: &[(String, String, f64, f64)],
        prints_before: &[f64],
        open_now: bool,
        memory_transient: &[f64],
    ) {
        use crate::agent_book::*;
        let (day, tick) = (self.current_day, self.ticks_today());
        let memory_on = self.params.impact_memory_coefficient > 0.0;

        // Permanent impact, per agent and name.
        let linear = self.params.fill_impact_coefficient != 0.0;
        for (agent, t, b, s) in applied {
            if crate::population::is_population_label(agent) {
                continue;
            }
            let Some(i) = self.companies.iter().position(|c| &c.ticker == t) else {
                continue;
            };
            let Some(n) = outcome.active_indices.iter().position(|&x| x == i) else {
                continue;
            };
            let permanent = if linear {
                self.fill_impact_of(i, b - s)
            } else {
                let mut net = 0.0;
                for (_, t2, b2, s2) in applied {
                    if t2 == t {
                        net += b2 - s2;
                    }
                }
                if net == 0.0 {
                    0.0
                } else {
                    outcome.s_components[n][4] * (b - s) / net
                }
            };
            // The memory's part of the tick, shared by signed net shares
            // among the agents whose flow reached this name on it.
            let transient = if memory_on {
                let total = memory_transient.get(i).copied().unwrap_or(0.0);
                let mut net = 0.0;
                for (_, t2, b2, s2) in applied {
                    if t2 == t {
                        net += b2 - s2;
                    }
                }
                Some(if net == 0.0 || total == 0.0 { 0.0 } else { total * (b - s) / net })
            } else {
                None
            };
            self.book.impacts.push(AgentImpact {
                agent: agent.clone(),
                ticker: t.clone(),
                bought: *b,
                sold: *s,
                permanent,
                transient,
                day,
                tick,
            });
        }

        // Resting orders the settlement filled. A resting order the maker's
        // re-quote left crossed took liquidity, so its share of the fills
        // is taker flow like any other agent's, and reaches the market on
        // the next tick. Left out, a standing bid at the ask would take the
        // maker's fresh size every tick and never pay the impact of it.
        let at_limit = self.params.book_cross_at_limit != 0.0;
        let refill = memory_on && self.params.impact_memory_refill != 0.0;
        for (i, f) in &outcome.agent_fills {
            let ticker = self.companies[*i].ticker.clone();
            // `book_cross_at_limit`: a resting order the maker's re-quote
            // left crossed trades at its own limit, and the improvement is
            // the arriving re-quote's. Between two agents the book already
            // fills at the earlier order's price.
            let price = if at_limit && f.taker && is_house(&f.counterparty) {
                self.book.orders.iter().find(|o| o.id == f.order_id).map(|o| o.limit).unwrap_or(f.price)
            } else {
                f.price
            };
            if f.taker {
                // With the metaorder memory on, only what the house took the
                // other side of is flow to the market: a resting order can
                // also cross another agent's (see `meet_book`).
                if !memory_on {
                    self.book.add_flow(&f.agent, &ticker, f.side, f.quantity);
                } else if is_house(&f.counterparty) {
                    self.book.add_flow(&f.agent, &ticker, f.side, f.quantity);
                    // `impact_memory_refill`: a crossed resting order
                    // against the memory's lean refills it first, as a
                    // resting order the flow fills does; only what it
                    // takes beyond zero is flow against the house.
                    let absorbed = if refill { self.refill_memory(*i, f.side, f.quantity) } else { 0.0 };
                    let n = self.companies.len();
                    let reference = prints_before.get(*i).copied().unwrap_or(f64::NAN);
                    self.book.add_house_flow(n, *i, f.side, f.quantity - absorbed, price, reference);
                }
            } else if refill && f.counterparty == FLOW_OWNER {
                self.refill_memory(*i, f.side, f.quantity);
            }
            self.reduce_order(&f.order_id, f.quantity);
            self.push_fill(AgentFill {
                agent: f.agent.clone(),
                order_id: f.order_id.clone(),
                ticker,
                side: f.side,
                quantity: f.quantity,
                price,
                liquidity: if f.taker { Liquidity::Taker } else { Liquidity::Maker },
                counterparty: f.counterparty.clone(),
                reference: prints_before.get(*i).copied().unwrap_or(f64::NAN),
                day,
                tick,
                sequence: 0,
            });
        }

        // Limits waiting for the traded range.
        if open_now {
            let waiting: Vec<AgentOrder> = self
                .book
                .orders
                .iter()
                .filter(|o| o.mode == RestMode::Range)
                .cloned()
                .collect();
            for o in waiting {
                let Some(i) = self.companies.iter().position(|c| c.ticker == o.ticker) else {
                    continue;
                };
                if !outcome.active_indices.contains(&i) {
                    continue;
                }
                let print = self.companies[i].stock.price;
                if !range_reached(o.side, o.limit, print) {
                    continue;
                }
                if refill {
                    self.refill_memory(i, o.side, o.remaining);
                }
                self.reduce_order(&o.id, o.remaining);
                self.push_fill(AgentFill {
                    agent: o.agent.clone(),
                    order_id: o.id.clone(),
                    ticker: o.ticker.clone(),
                    side: o.side,
                    quantity: o.remaining,
                    price: o.limit,
                    liquidity: Liquidity::Range,
                    counterparty: RANGE_OWNER.to_string(),
                    reference: prints_before.get(i).copied().unwrap_or(f64::NAN),
                    day,
                    tick,
                    sequence: 0,
                });
            }
        }
    }

    // ── Agents' orders: the agent-facing book ──────────────────────────────
    //
    // `crate::agent_book` carries the model. What is here is the engine's
    // half: an order meeting the book, what it leaves behind, and what the
    // tick does with it.

    /// The agent-facing book's state, for a snapshot.
    pub fn book_state(&self) -> &crate::agent_book::BookState {
        &self.book
    }

    /// Install a book state, for a restore. `taken` must follow the roster
    /// or be empty.
    pub fn set_book_state(&mut self, state: crate::agent_book::BookState) -> Result<(), String> {
        if !state.taken.is_empty() && state.taken.len() != self.companies.len() {
            return Err(format!(
                "the book's consumed-depth table has {} rows and the roster {} names",
                state.taken.len(),
                self.companies.len()
            ));
        }
        if !state.memory.is_empty() && state.memory.len() != self.companies.len() {
            return Err(format!(
                "the book's metaorder memory has {} rows and the roster {} names",
                state.memory.len(),
                self.companies.len()
            ));
        }
        self.book = state;
        Ok(())
    }

    /// Whether an agent's order must execute in the engine rather than be
    /// priced off a snapshot of the book: when agents consume the book they
    /// share, or when their limit orders rest in it.
    pub fn book_live(&self) -> bool {
        self.params.book_shared != 0.0 || self.params.book_resting != 0.0
    }

    /// The order in which a cohort's orders reach the book on one step:
    /// `labels` sorted, which is the order at `book_arrival_shuffle` 0.0,
    /// or with the switch on sorted by [`crate::rng::arrival_priority`] of
    /// this engine's seed, `day` and `step_of_day`, ties (a 2^-64 event)
    /// broken by the label. Reads no stream and moves nothing.
    pub fn arrival_order(&self, day: u64, step_of_day: u64, labels: &[String]) -> Vec<String> {
        let mut out: Vec<String> = labels.to_vec();
        if self.params.book_arrival_shuffle == 0.0 {
            out.sort();
            return out;
        }
        let seed = self.root_seed;
        let mut keyed: Vec<(u64, String)> = out
            .drain(..)
            .map(|l| (crate::rng::arrival_priority(seed, day, step_of_day, &l), l))
            .collect();
        keyed.sort();
        keyed.into_iter().map(|(_, l)| l).collect()
    }

    /// Whether the book a caller reads is the agent-facing one rather than
    /// the maker's ladder alone.
    fn agent_view(&self) -> bool {
        self.book_live() || self.params.book_depth_coefficient != 0.0 || !self.book.is_pristine()
    }

    /// The ticks already run on the current day: the day mark's count, or
    /// the count a restore carried when no day has opened since it.
    fn ticks_today(&self) -> u32 {
        match self.day_marks.last() {
            Some(m) => m.ticks,
            None => self.carried_ticks.unwrap_or(0),
        }
    }

    /// The agent-facing book for one name, leaving out one agent's orders.
    fn agent_book_at(&self, index: usize, exclude: Option<&str>) -> Option<crate::order_book::OrderBook> {
        let company = self.companies.get(index)?;
        let taken = self
            .book
            .taken
            .get(index)
            .copied()
            .unwrap_or([0.0; crate::agent_book::TAKEN_WIDTH]);
        let memory = self
            .book
            .memory
            .get(index)
            .copied()
            .unwrap_or([0.0; crate::agent_book::MEMORY_WIDTH]);
        Some(crate::agent_book::agent_book(&crate::agent_book::AgentBookInputs {
            company,
            vix: self.economy.vix,
            params: &self.params,
            market_sigma_daily: self.market_vol.sigma_daily(),
            taken,
            memory,
            orders: &self.book.orders,
            exclude_agent: exclude,
        }))
    }

    fn push_fill(&mut self, mut fill: crate::agent_book::AgentFill) -> crate::agent_book::AgentFill {
        fill.sequence = self.book.fill_sequence;
        self.book.fill_sequence += 1;
        self.book.fills.push(fill.clone());
        fill
    }

    /// Reduce a waiting order by a fill, removing it once it is done.
    fn reduce_order(&mut self, order_id: &str, quantity: f64) {
        if let Some(o) = self.book.orders.iter_mut().find(|o| o.id == order_id) {
            o.remaining -= quantity;
        }
        self.book.orders.retain(|o| o.remaining > 1e-9);
    }

    /// An order meets the book an agent trades against, for one name.
    ///
    /// Everything it takes is recorded: the consumed depth and the maker's
    /// inventory when the book is shared, the other agents' resting orders
    /// it hit, a taker fill per level and, when `count_flow`, the taker flow
    /// the next tick applies. Returns the taker fills and the mid the order
    /// met.
    #[allow(clippy::too_many_arguments)]
    fn meet_book(
        &mut self,
        index: usize,
        agent: &str,
        order_id: &str,
        side: crate::order_book::Side,
        quantity: f64,
        limit: Option<f64>,
        count_flow: bool,
        at_limit: bool,
        crossing: bool,
    ) -> (Vec<crate::agent_book::AgentFill>, f64) {
        self.meet_book_as(index, agent, order_id, side, quantity, limit, count_flow, at_limit, crossing, false)
    }

    /// [`Engine::meet_book`], with `quiet` for a population participant's
    /// order: its own fills are returned but not recorded.
    #[allow(clippy::too_many_arguments)]
    fn meet_book_as(
        &mut self,
        index: usize,
        agent: &str,
        order_id: &str,
        side: crate::order_book::Side,
        quantity: f64,
        limit: Option<f64>,
        count_flow: bool,
        at_limit: bool,
        crossing: bool,
        quiet: bool,
    ) -> (Vec<crate::agent_book::AgentFill>, f64) {
        use crate::agent_book::{self as ab, AgentFill, Liquidity};
        use crate::order_book::{Side, SubmitOptions};
        let Some(mut book) = self.agent_book_at(index, Some(agent)) else {
            return (Vec::new(), f64::NAN);
        };
        let ticker = self.companies[index].ticker.clone();
        let reference = book.mid_price().unwrap_or(self.companies[index].stock.price);
        let r = book.submit(
            side,
            quantity,
            agent,
            SubmitOptions { limit_price: limit, post_remainder: false, order_id: None, skip_own: true, house_ids: false },
        );
        let shared = self.params.book_shared != 0.0;
        if shared {
            self.book.fit(self.companies.len());
        }
        let (day, tick) = (self.current_day, self.ticks_today());
        let mut taker = Vec::with_capacity(r.fills.len());
        for f in &r.fills {
            let owner: &str = &f.maker_id;
            // `book_cross_at_limit`: a resting order crossed during the
            // session takes the house's side at its own limit.
            let price = match limit {
                Some(p) if at_limit && ab::is_house(owner) => p,
                _ => f.price,
            };
            if shared {
                if let Some(slot) = ab::taken_slot(side, owner) {
                    self.book.taken[index][slot] += f.quantity;
                }
                if owner == crate::market_maker::MARKET_MAKER_ID {
                    // The maker is opposite the taker; it quotes on this
                    // from its next re-quote.
                    self.book.taken[index][ab::TAKEN_MAKER_INVENTORY] += match side {
                        Side::Buy => -f.quantity,
                        Side::Sell => f.quantity,
                    };
                }
            }
            if !ab::is_house(owner) {
                self.reduce_order(&f.maker_order_id, f.quantity);
                self.push_fill(AgentFill {
                    agent: owner.to_string(),
                    order_id: f.maker_order_id.clone(),
                    ticker: ticker.clone(),
                    side: match side { Side::Buy => Side::Sell, Side::Sell => Side::Buy },
                    quantity: f.quantity,
                    price,
                    liquidity: Liquidity::Maker,
                    counterparty: agent.to_string(),
                    reference,
                    day,
                    tick,
                    sequence: 0,
                });
            }
            // A population participant's own fills go on its ledger, not in
            // the fills `take_fills` returns: it keeps the sequence number
            // the fill would have taken and only the price and size, so the
            // agents' fills are numbered as they were.
            let fill = if quiet {
                let sequence = self.book.fill_sequence;
                self.book.fill_sequence += 1;
                AgentFill {
                    agent: String::new(),
                    order_id: String::new(),
                    ticker: String::new(),
                    side,
                    quantity: f.quantity,
                    price,
                    liquidity: Liquidity::Taker,
                    counterparty: String::new(),
                    reference,
                    day,
                    tick,
                    sequence,
                }
            } else {
                self.push_fill(AgentFill {
                    agent: agent.to_string(),
                    order_id: order_id.to_string(),
                    ticker: ticker.clone(),
                    side,
                    quantity: f.quantity,
                    price,
                    liquidity: Liquidity::Taker,
                    counterparty: owner.to_string(),
                    reference,
                    day,
                    tick,
                    sequence: 0,
                })
            };
            if count_flow {
                // With the metaorder memory on, a fill against another
                // agent's resting order is not flow to the market: it took
                // no liquidity from the house (the maker, the latent depth),
                // the resting order had just added what it took, and the
                // pair's cash nets to zero. Counted, one agent resting an
                // ask and another lifting it would walk the tape, through
                // the memory and the linear law alike, at no cost (a wash).
                // Off, every share taken is flow, as it always was.
                if self.params.impact_memory_coefficient == 0.0 {
                    self.book.add_flow(agent, &ticker, side, f.quantity);
                } else if ab::is_house(owner) {
                    self.book.add_flow(agent, &ticker, side, f.quantity);
                    // `impact_memory_refill`: a resting order crossed during
                    // the session refills the memory first (see
                    // `settle_book`).
                    let absorbed = if crossing && self.params.impact_memory_refill != 0.0 {
                        self.refill_memory(index, side, f.quantity)
                    } else {
                        0.0
                    };
                    let n = self.companies.len();
                    self.book.add_house_flow(n, index, side, f.quantity - absorbed, price, reference);
                }
            }
            taker.push(fill);
        }
        (taker, reference)
    }

    /// Match every resting order on one name that the book now crosses,
    /// against the book at the book's prices, in arrival order.
    ///
    /// A resting order is never posted crossed, and a tick's settlement
    /// matches any its re-quote crosses. What is left is the night: the
    /// open moves every price without a tick, and an order the gap went
    /// through would otherwise sit crossed for the first agent to pick off
    /// at a price the market left behind. Matched here it fills at the
    /// opening ladder's prices, as an opening auction would fill it, and
    /// what it takes is taker flow like any other.
    fn cross_resting(&mut self, index: usize, at_open: bool) {
        use crate::agent_book::RestMode;
        use crate::order_book::Side;
        let Some(ticker) = self.companies.get(index).map(|c| c.ticker.clone()) else {
            return;
        };
        let ids: Vec<String> = self
            .book
            .orders
            .iter()
            .filter(|o| o.mode == RestMode::Queue && o.ticker == ticker)
            .map(|o| o.id.clone())
            .collect();
        for id in ids {
            let Some(o) = self.book.orders.iter().find(|o| o.id == id).cloned() else {
                continue;
            };
            let Some(book) = self.agent_book_at(index, Some(&o.agent)) else {
                continue;
            };
            let crossed = match o.side {
                Side::Buy => book.best_ask().is_some_and(|a| o.limit >= a),
                Side::Sell => book.best_bid().is_some_and(|b| o.limit <= b),
            };
            if !crossed {
                continue;
            }
            // At the open the gap's orders fill at the opening ladder's
            // prices, as an auction fills them; during the session, under
            // `book_cross_at_limit`, at their own limit.
            let at_limit = !at_open && self.params.book_cross_at_limit != 0.0;
            let (fills, _) =
                self.meet_book(index, &o.agent, &o.id, o.side, o.remaining, Some(o.limit), true, at_limit, !at_open);
            let filled: f64 = fills.iter().map(|f| f.quantity).sum();
            self.reduce_order(&o.id, filled);
        }
    }

    /// The book at the open: the maker's pending inventory lands, the
    /// consumed depth is whole again, and crossed resting orders match.
    fn open_book(&mut self) {
        use crate::agent_book::{RestMode, TAKEN_MAKER_INVENTORY};
        for (i, row) in self.book.taken.iter_mut().enumerate() {
            if row[TAKEN_MAKER_INVENTORY] != 0.0 {
                if let Some(c) = self.companies.get_mut(i) {
                    c.stock.maker_inventory =
                        Some(c.stock.maker_inventory.unwrap_or(0.0) + row[TAKEN_MAKER_INVENTORY]);
                }
            }
            *row = [0.0; crate::agent_book::TAKEN_WIDTH];
        }
        let names: Vec<usize> = (0..self.companies.len())
            .filter(|&i| {
                let t = &self.companies[i].ticker;
                self.book.orders.iter().any(|o| o.mode == RestMode::Queue && &o.ticker == t)
            })
            .collect();
        for i in names {
            self.cross_resting(i, true);
        }
    }

    /// Send one agent's order to the book.
    ///
    /// A market order (`limit` None) takes what the book holds up to
    /// `quantity` and the rest is unfilled. A limit order takes what the
    /// book holds at its limit or better, and its remainder waits: resting
    /// in the queue with `book_resting` on, outside the book for the traded
    /// range with it off. Every share taken is taker flow, applied to the
    /// market once, on the next open tick; with `impact_memory_coefficient`
    /// set, every share taken from the house (a fill against another
    /// agent's resting order is not, see `meet_book`).
    ///
    /// Orders are processed in the order they arrive. A caller submitting
    /// for several agents in one step decides that order, and
    /// `PyEngine::submit_many` documents the one it uses.
    #[allow(clippy::too_many_arguments)]
    pub fn submit_order(
        &mut self,
        agent: &str,
        ticker: &str,
        side: crate::order_book::Side,
        quantity: f64,
        limit: Option<f64>,
        order_id: Option<String>,
    ) -> Result<OrderReport, String> {
        use crate::agent_book::{self as ab, AgentOrder, RestMode};
        if agent.is_empty() || ab::is_house(agent) {
            return Err(format!(
                "{agent:?} cannot place orders: agent labels are non-empty and \
                 not one of the book's own owners (mm, depth, flow, range)"
            ));
        }
        if self.population.is_some() && crate::population::is_population_label(agent) {
            return Err(format!(
                "{agent:?} cannot place orders: labels starting {:?} belong to \
                 this engine's population",
                crate::population::LABEL_PREFIX
            ));
        }
        if !(quantity > 0.0) || !quantity.is_finite() {
            return Err(format!("quantity must be finite and greater than zero, got {quantity}"));
        }
        if let Some(p) = limit {
            if !(p > 0.0) || !p.is_finite() {
                return Err(format!("limit_price must be finite and greater than zero, got {p}"));
            }
        }
        // A listed contract trades in its own book (`futures_index_listed`,
        // `futures_vix_listed`).
        if (self.futures_on() || self.vix_futures_on() || self.rate_futures_on() || self.oil_futures_on())
            && !self.companies.iter().any(|c| c.ticker == ticker)
        {
            if let Ok(symbol) = crate::derivatives::ContractSymbol::parse(ticker) {
                if self.vix_futures_on() && symbol.root == crate::derivatives::ContractKind::VixFuture.root() {
                    return self.submit_vix_future_order(agent, ticker, side, quantity, limit, order_id);
                }
                if self.rate_futures_on() && self.rate_futures_slot(ticker).is_some() {
                    return self.submit_rate_future_order(agent, ticker, side, quantity, limit, order_id);
                }
                if self.oil_futures_on() && symbol.root == crate::derivatives::ContractKind::OilFuture.root() {
                    return self.submit_oil_future_order(agent, ticker, side, quantity, limit, order_id);
                }
                return self.submit_contract_order(agent, ticker, side, quantity, limit, order_id);
            }
        }
        // The rate indices quote their own ladder (`crate::rates`) and take
        // their flow from `run_session`'s `fills`; the agent-facing book
        // holds equities only.
        if self.rates.instruments.iter().any(|i| i.spec.ticker == ticker) {
            return Err(format!(
                "{ticker} is a simulated rate index, and the agent-facing book holds \
                 equities only: price it with Portfolio.execute, which reads its own \
                 book, and pass its flow in run_session's fills"
            ));
        }
        let index = self
            .companies
            .iter()
            .position(|c| c.ticker == ticker)
            .ok_or_else(|| format!("no instrument with ticker {ticker:?}"))?;
        let company = &self.companies[index];
        if company.is_bankrupt || !company.is_public {
            return Err(format!("{ticker} does not trade: it is bankrupt or no longer public"));
        }
        let id = match order_id {
            Some(id) => {
                if id.is_empty() {
                    return Err("order_id cannot be empty".to_string());
                }
                id
            }
            None => format!("{agent}-{}", self.book.sequence),
        };
        if self.contract_order_id_taken(&id) {
            return Err(format!("order id {id:?} is already waiting in the book"));
        }
        let sequence = self.book.sequence;
        self.book.sequence += 1;

        if self.book.orders.iter().any(|o| o.mode == RestMode::Queue && o.ticker == ticker) {
            self.cross_resting(index, false);
        }
        let (fills, reference) = self.meet_book(index, agent, &id, side, quantity, limit, true, false, false);
        let mut filled = 0.0;
        let mut notional = 0.0;
        let mut worst: Option<f64> = None;
        for f in &fills {
            filled += f.quantity;
            notional += f.price * f.quantity;
            worst = Some(f.price);
        }
        let remainder = quantity - filled;
        let (resting, mode) = match limit {
            Some(p) if remainder > 1e-9 => {
                let mode = if self.params.book_resting != 0.0 { RestMode::Queue } else { RestMode::Range };
                self.book.orders.push(AgentOrder {
                    id: id.clone(),
                    agent: agent.to_string(),
                    ticker: ticker.to_string(),
                    side,
                    limit: p,
                    quantity,
                    remaining: remainder,
                    sequence,
                    mode,
                });
                (remainder, Some(mode))
            }
            _ => (0.0, None),
        };
        Ok(OrderReport {
            order_id: id,
            agent: agent.to_string(),
            ticker: ticker.to_string(),
            side,
            requested: quantity,
            filled,
            average_price: if filled > 0.0 { Some(notional / filled) } else { None },
            worst_price: worst,
            reference,
            resting,
            unfilled: if mode.is_some() { 0.0 } else { remainder },
            mode,
            fills,
        })
    }

    /// Cancel a waiting order, on a name or on a listed contract. `agent`,
    /// when given, must own it. Returns whether an order was removed.
    pub fn cancel_order(&mut self, order_id: &str, agent: Option<&str>) -> bool {
        let before = self.book.orders.len();
        self.book
            .orders
            .retain(|o| !(o.id == order_id && agent.is_none_or(|a| a == o.agent)));
        if self.book.orders.len() != before {
            return true;
        }
        self.cancel_contract_order(order_id, agent)
            || self.cancel_vix_future_order(order_id, agent)
            || self.cancel_rate_future_order(order_id, agent)
            || self.cancel_oil_future_order(order_id, agent)
    }

    /// Waiting orders, in arrival order, for one agent or all: the names'
    /// first, then the index futures' (`futures_index_listed`), then the VIX
    /// futures' (`futures_vix_listed`).
    pub fn open_orders(&self, agent: Option<&str>) -> Vec<crate::agent_book::AgentOrder> {
        self.book
            .orders
            .iter()
            .chain(self.futures.book.orders.iter())
            .chain(self.vix_futures_orders().iter())
            .chain(self.rate_futures_orders().iter())
            .chain(self.oil_futures_orders().iter())
            .filter(|o| agent.is_none_or(|a| a == o.agent))
            .cloned()
            .collect()
    }

    /// Collect fills, for one agent or all, in the order they happened: the
    /// names' first, then the index futures' (`futures_index_listed`), then
    /// the VIX futures' (`futures_vix_listed`); each family's `sequence`
    /// counts its own fills alone.
    pub fn take_fills(&mut self, agent: Option<&str>) -> Vec<crate::agent_book::AgentFill> {
        let (mut taken, kept): (Vec<_>, Vec<_>) = std::mem::take(&mut self.book.fills)
            .into_iter()
            .partition(|f| agent.is_none_or(|a| a == f.agent));
        self.book.fills = kept;
        if !self.futures.book.fills.is_empty() {
            let (contracts, kept): (Vec<_>, Vec<_>) = std::mem::take(&mut self.futures.book.fills)
                .into_iter()
                .partition(|f| agent.is_none_or(|a| a == f.agent));
            self.futures.book.fills = kept;
            taken.extend(contracts);
        }
        // The VIX futures' fills (`futures_vix_listed`), on their own counter.
        taken.extend(self.take_vix_futures_fills(agent));
        // The rate futures' fills (`futures_rates_listed`), on their own counter.
        taken.extend(self.take_rate_futures_fills(agent));
        // The oil futures' fills (`futures_oil_listed`), on their own counter.
        taken.extend(self.take_oil_futures_fills(agent));
        taken
    }

    /// Collect permanent-impact rows, for one agent or all.
    pub fn take_impacts(&mut self, agent: Option<&str>) -> Vec<crate::agent_book::AgentImpact> {
        let (taken, kept): (Vec<_>, Vec<_>) = std::mem::take(&mut self.book.impacts)
            .into_iter()
            .partition(|f| agent.is_none_or(|a| a == f.agent));
        self.book.impacts = kept;
        taken
    }

    /// Queue fills that arrived from outside the engine (`run_session`'s
    /// `fills`) as anonymous taker flow, so the linear law prices them too.
    /// Only under `fill_impact_coefficient`; the imbalance law merges them
    /// into the first tick's flow as it always has.
    pub fn queue_external_fills(&mut self, fills: &[(String, OrderVolume)]) {
        for (ticker, v) in fills {
            if v.buy > 0.0 {
                self.book.add_flow("", ticker, crate::order_book::Side::Buy, v.buy);
            }
            if v.sell > 0.0 {
                self.book.add_flow("", ticker, crate::order_book::Side::Sell, v.sell);
            }
        }
    }

    // ── Day boundaries ────────────────────────────────────────────────────

    /// Market-open reset. Zero draws.
    ///
    /// This is what anchors the circuit-breaker band to today's open, which is
    /// what makes it a SESSION band and leaves the overnight gap outside it by
    /// design (D6).
    /// Attribution for the current day, four values per company:
    /// `[company_news, order_flow_impact, short_squeeze_effect, random_noise]`.
    ///
    /// # Four, not the reference's six
    ///
    /// The reference declares six attribution keys, but three of them --
    /// `earningsRevision`, `multipleChange` and `sentiment` -- belong to
    /// factors the live flags discard, and its `shortSqueezeEffect` is folded
    /// into `orderFlowImpact` for display rather than reported separately.
    ///
    /// Reporting six columns here would mean shipping three columns of
    /// structural zeros, which is the "knobs wired to nothing" documentation
    /// lie this port has already had to correct once. So the four live
    /// components are reported, and the squeeze is kept separate because it is
    /// genuinely a distinct mechanism.
    pub fn attribution(&self) -> &[[f64; crate::market::factors::COMPONENT_COUNT]] {
        &self.attribution
    }

    /// This tick's `s` decomposition per company slot, in
    /// [`crate::market::factors::S_COMPONENT_KEYS`] order. Zero for a company
    /// that did not tick.
    pub fn tick_components(&self) -> &[[f64; crate::market::factors::TICK_COMPONENT_COUNT]] {
        &self.tick_components
    }

    /// The valuation each company was last measured at. NaN before its first
    /// tick.
    pub fn tick_fundamental(&self) -> &[f64] {
        &self.tick_fundamental
    }

    /// The book anchor each company was last given: `fundamental * exp(s)`.
    pub fn tick_anchor(&self) -> &[f64] {
        &self.tick_anchor
    }

    /// This tick's shock per company slot: `log(anchor / the last print)`.
    ///
    /// Zero for a company that did not tick.
    pub fn tick_shock(&self) -> &[f64] {
        &self.tick_shock
    }

    /// This tick's absorption per company slot: `log(the print / anchor)`.
    ///
    /// Measured against the printed tape, so a halted name books the
    /// breaker's clamp here. Zero for a company that did not tick.
    pub fn tick_absorbed(&self) -> &[f64] {
        &self.tick_absorbed
    }

    /// How far the print breaker moved each company's print this tick. Zero
    /// where it did not fire, and `tick_absorbed - tick_clamp` is the book's
    /// own share.
    pub fn tick_clamp(&self) -> &[f64] {
        &self.tick_clamp
    }

    /// This tick's repricing per company slot: `log(the price the tick
    /// started from / the last print)`, what the close's re-mark, a pin's
    /// re-mark or the opening print wrote to the price between the two.
    /// Zero where nothing did; NaN where it is not known (after a restore;
    /// see `repriced_pending`).
    pub fn tick_repriced(&self) -> &[f64] {
        &self.tick_repriced
    }

    /// Forget what was written to each price since its last print, as not
    /// known: NaN on a model that can write a price between prints
    /// (`macro_publication_repricing` or `overnight_variance_ratio` set),
    /// zero on one that cannot. For a restore, whose snapshot does not
    /// carry it, so the engine restored into does not keep its own.
    pub fn forget_repriced(&mut self) {
        let unknown = self.params.macro_publication_repricing != 0.0
            || self.params.overnight_variance_ratio != 0.0;
        let value = if unknown { f64::NAN } else { 0.0 };
        self.repriced_pending.clear();
        self.repriced_pending.resize(self.companies.len(), value);
        // The rate indices likewise: only the close's re-mark writes their
        // price between prints.
        let rate_value = if self.params.rate_close_remark != 0.0 { f64::NAN } else { 0.0 };
        self.rates.forget_repriced(rate_value);
    }

    /// What each company would have printed this tick against unbounded
    /// depth. Empty while the counterfactual is off.
    pub fn tick_unbounded_print(&self) -> &[f64] {
        &self.tick_unbounded_print
    }

    /// Liquidity's share of each company's move this tick. Empty while the
    /// counterfactual is off.
    pub fn tick_liquidity_share(&self) -> &[f64] {
        &self.tick_liquidity_share
    }

    /// Settle every open tick a second time against unbounded depth.
    ///
    /// Off by default. Switching it on adds a second settlement per active
    /// company per open tick, served the same four uniforms, on its own book,
    /// with its result reaching no company field. The market is the same
    /// market either way and the digest is the same digest; what changes is
    /// that [`Engine::tick_unbounded_print`] and
    /// [`Engine::tick_liquidity_share`] have something in them.
    ///
    /// It adds a second settlement per active company per open tick, and
    /// that settlement quotes every level rather than the two ordinary flow
    /// reaches. A run takes three to four times as long with it on, best of
    /// seven over 24 names and three days of 390 ticks at seed 42 on pt-v16.
    /// The ratio and not just the times move with load on a shared machine,
    /// because the arm-off run is the smaller number, so it is given as a
    /// range rather than to two decimals. It stays off until a caller asks
    /// for it.
    pub fn set_settle_depth_counterfactual(&mut self, on: bool) {
        self.settle_depth_counterfactual = on;
        let n = self.companies.len();
        self.tick_unbounded_print.clear();
        self.tick_liquidity_share.clear();
        if on {
            self.tick_unbounded_print.resize(n, f64::NAN);
            self.tick_liquidity_share.resize(n, 0.0);
        }
    }

    /// Whether the depth counterfactual is running.
    pub fn settle_depth_counterfactual(&self) -> bool {
        self.settle_depth_counterfactual
    }

    /// Restore the per-day accumulators that a column snapshot does not hold.
    ///
    /// The columns are per-COMPANY state -- price, variance, inventory, the
    /// mispricing carry. These four are per-DAY, live beside them, and were
    /// missing from the snapshot, which made a mid-day fork diverge:
    /// `attribution` is fed to GARCH as the day's innovation at the close, so
    /// a fork that lost it closed on a different variance and priced
    /// differently from the parent it was supposed to be identical to.
    ///
    /// Lengths are checked rather than truncated. A short slice would restore
    /// a market correct for its first companies and stale for the rest.
    pub fn restore_day_state(
        &mut self,
        attribution: &[f64],
        components: &[f64],
        fundamental: &[f64],
        anchor: &[f64],
    ) -> Result<(), String> {
        let n = self.companies.len();
        // Nine-wide attribution rows predate the overnight slot, ten-wide
        // ones the fair-value shift and eleven-wide ones the dividend; each
        // restores with the slots it lacks at zero, which is what such a day
        // held. Likewise eight-wide tick rows.
        let width = crate::market::factors::COMPONENT_COUNT;
        let attribution: Vec<f64> = match (n, attribution.len()) {
            (n, len) if n > 0
                && (len == n * (width - 1) || len == n * (width - 2) || len == n * (width - 3)) => {
                let old = len / n;
                attribution
                    .chunks_exact(old)
                    .flat_map(|c| c.iter().copied().chain(std::iter::repeat_n(0.0, width - old)))
                    .collect()
            }
            _ => attribution.to_vec(),
        };
        let tick_width = crate::market::factors::TICK_COMPONENT_COUNT;
        let components: Vec<f64> = if n > 0 && components.len() == n * (tick_width - 1) {
            components
                .chunks_exact(tick_width - 1)
                .flat_map(|c| c.iter().copied().chain(std::iter::once(0.0)))
                .collect()
        } else {
            components.to_vec()
        };
        for (name, len, want) in [
            ("attribution", attribution.len(), n * crate::market::factors::COMPONENT_COUNT),
            ("tick_components", components.len(), n * tick_width),
            ("tick_fundamental", fundamental.len(), n),
            ("tick_anchor", anchor.len(), n),
        ] {
            if len != want {
                return Err(format!("{name} has {len} values, expected {want}"));
            }
        }
        self.attribution = attribution
            .chunks_exact(crate::market::factors::COMPONENT_COUNT)
            .map(|c| {
                let mut row = [0.0; crate::market::factors::COMPONENT_COUNT];
                row.copy_from_slice(c);
                row
            })
            .collect();
        self.tick_components = components
            .chunks_exact(tick_width)
            .map(|c| {
                let mut row = [0.0; crate::market::factors::TICK_COMPONENT_COUNT];
                row.copy_from_slice(c);
                row
            })
            .collect();
        self.tick_fundamental = fundamental.to_vec();
        self.tick_anchor = anchor.to_vec();
        Ok(())
    }

    /// The day's noise each name's GJR steps on at tonight's close, before
    /// `garch_innovation_commensurate` rescales it: the `random_noise`
    /// attribution column, and under a night split the night's noise ahead
    /// of it (`innovation_day`), which the attribution books in `overnight`.
    pub fn day_noise_column(&self) -> Vec<f64> {
        if self.innovation_day.is_empty() {
            return self.attribution_column(random_noise_index());
        }
        (0..self.companies.len())
            .map(|i| self.innovation_day.get(i).copied().unwrap_or(0.0))
            .collect()
    }

    /// The day's GJR sum under a night split, per company slot; empty while
    /// the split is off. See [`Engine::day_noise_column`].
    pub fn innovation_day(&self) -> &[f64] {
        &self.innovation_day
    }

    /// Put the day's GJR sum back (see [`Engine::innovation_day`]). Empty
    /// clears it, which is the state of a model without a night split.
    pub fn restore_innovation_day(&mut self, values: &[f64]) -> Result<(), String> {
        if !values.is_empty() && values.len() != self.companies.len() {
            return Err(format!(
                "innovation_day has {} values, expected {}",
                values.len(),
                self.companies.len()
            ));
        }
        self.innovation_day = values.to_vec();
        Ok(())
    }

    /// The day's `random_noise` split, per company slot: market, sector,
    /// idiosyncratic. See [`Engine::noise_part_column`].
    pub fn noise_parts(&self) -> &[[f64; 3]] {
        &self.noise_parts
    }

    /// The day's summed square of the scale each name's idiosyncratic part
    /// was drawn at. See [`Engine::noise_parts`].
    pub fn noise_own_scale2(&self) -> &[f64] {
        &self.noise_own_scale2
    }

    /// Put the day's noise split back, flattened three values per company
    /// slot for the parts and one per slot for the scale.
    ///
    /// Restored for the reason [`Engine::restore_day_state`] exists: above
    /// zero on `ModelParams::garch_innovation_commensurate` the close builds
    /// the per-name GJR innovation out of these, so a mid-day fork that lost
    /// them closes on a different innovation.
    pub fn restore_noise_split(&mut self, parts: &[f64], scale2: &[f64]) -> Result<(), String> {
        let n = self.companies.len();
        if parts.len() != n * 3 {
            return Err(format!(
                "noise_parts has {} values, expected {}",
                parts.len(),
                n * 3
            ));
        }
        if scale2.len() != n {
            return Err(format!(
                "noise_own_scale2 has {} values, expected {}",
                scale2.len(),
                n
            ));
        }
        self.noise_parts = parts.chunks_exact(3).map(|c| [c[0], c[1], c[2]]).collect();
        self.noise_own_scale2 = scale2.to_vec();
        Ok(())
    }

    /// The jump each name booked at the last close, kept across the open for
    /// the session that trades the gap in. Written and read only off the
    /// shipped `ModelParams::volume_move_jump_share` of 1.0.
    pub fn jump_move(&self) -> &[f64] {
        &self.jump_move
    }

    /// Put that jump back, one value per company slot. See
    /// [`Engine::jump_move`].
    pub fn set_jump_move(&mut self, moves: &[f64]) -> Result<(), String> {
        let n = self.companies.len();
        if moves.len() != n {
            return Err(format!(
                "jump_move has {} values, expected {}",
                moves.len(),
                n
            ));
        }
        self.jump_move = moves.to_vec();
        Ok(())
    }

    /// The market factor's variance state, for checkpoints:
    /// `(variance, day_factor)`.
    ///
    /// Engine-level state with no column to live in: a fork that did not
    /// carry it would re-open at the BASELINE factor sigma mid-regime and
    /// close its first day on a truncated innovation — pricing differently
    /// from the parent it forked from, the exact failure class
    /// [`Engine::restore_day_state`] exists for.
    pub fn market_variance_state(&self) -> (f64, f64, f64, f64, f64, f64) {
        self.market_vol.snapshot()
    }

    /// Put the market factor's variance state back. See
    /// [`Engine::market_variance_state`].
    pub fn set_market_variance_state(&mut self, variance: f64, day_factor: f64) {
        self.market_vol = MarketVarianceState::restore(variance, day_factor);
    }

    /// Put the market factor's variance state back including the slow
    /// component. See [`Engine::market_variance_state`].
    pub fn set_market_variance_state_with_components(
        &mut self,
        variance: f64,
        day_factor: f64,
        fast_variance: f64,
        slow_variance: f64,
        prev_day_factor: f64,
        smoothed_vix: f64,
    ) {
        self.market_vol = MarketVarianceState::restore_with_components(
            variance, day_factor, fast_variance, slow_variance,
            prev_day_factor, smoothed_vix);
    }

    /// One attribution column across all companies, by index 0..7.
    pub fn attribution_column(&self, factor: usize) -> Vec<f64> {
        self.attribution
            .iter()
            .map(|a| a.get(factor).copied().unwrap_or(f64::NAN))
            .collect()
    }

    /// One part of the day's `random_noise` across all companies, by index
    /// 0 (market), 1 (sector) or 2 (idiosyncratic). Reporting only; the
    /// close reads the parts through `daily_innovation_column`.
    pub fn noise_part_column(&self, part: usize) -> Vec<f64> {
        self.noise_parts
            .iter()
            .map(|a| a.get(part).copied().unwrap_or(f64::NAN))
            .collect()
    }

    /// The day's GARCH innovation per company, as `close_market` feeds it.
    ///
    /// At `garch_innovation_commensurate` 0.0 this is the `random_noise`
    /// attribution column and nothing else, returned by branch, so every
    /// preset before the dial is bit-identical.
    ///
    /// Above zero it is the name's OWN noise divided by the `kappa` the
    /// tick drew it with, which is what puts the innovation back in the
    /// units the GJR coefficients were fitted in. See
    /// [`crate::params::ModelParams::garch_innovation_commensurate`] for
    /// the defect, the arithmetic and the measurements.
    ///
    /// A name whose ticks never ran has no scale to divide by, so it keeps
    /// the whole column: that is the pre-dial value, and a zero divisor is
    /// not a smaller innovation, it is no statement at all.
    fn daily_innovation_column(&self) -> Vec<f64> {
        let whole = self.day_noise_column();
        let share = self.params.garch_innovation_commensurate;
        if share == 0.0 {
            return whole;
        }
        whole
            .iter()
            .enumerate()
            .map(|(i, &raw)| {
                let own = self.noise_parts.get(i).map(|a| a[2]).unwrap_or(f64::NAN);
                let kappa2 = self.noise_own_scale2.get(i).copied().unwrap_or(0.0);
                if !(kappa2 > 0.0) || !own.is_finite() {
                    return raw;
                }
                let commensurate = own / crate::mathx::sqrt(kappa2);
                if share == 1.0 {
                    commensurate
                } else {
                    (1.0 - share) * raw + share * commensurate
                }
            })
            .collect()
    }

    /// Step the crisis episode and, on the session one starts, draw its
    /// epicentre.
    ///
    /// Called once per session from `open_market`, which is the one point
    /// every spelling of a day passes through exactly once, and BEFORE the
    /// session's ticks, so the epicentre is fixed for the whole day.
    ///
    /// # Draw discipline
    ///
    /// A BRANCH at `crisis_epicentre_extra` 0.0 -- every preset before pt-v19's
    /// fourth composition of 2026-09-22:
    /// nothing runs, no state moves and no draw is taken, here or anywhere.
    /// When the dial is live the one uniform is taken on
    /// `stream::CRISIS_EPICENTRE` and nowhere else, so `MARKET`, `ECONOMY`,
    /// `EXTERNAL` and the six mechanism streams keep the positions they
    /// would have had and every recorded trajectory replays exactly. That
    /// the draw is CONDITIONAL -- once per episode, and not at all under a
    /// pin -- is allowed here for the reason the stream's own docs give:
    /// the only schedule it can move is its own, and nothing else reads it.
    fn update_crisis_episode(&mut self) {
        if self.params.crisis_epicentre_extra == 0.0 {
            return;
        }
        let above = self.economy.vix > self.params.crisis_vix_threshold;
        if !self.crisis_in_episode {
            // "Crossing from below" needs no previous VIX: an episode
            // cannot be running, so the last session either was under the
            // threshold or was before the run began.
            if above {
                self.crisis_in_episode = true;
                self.crisis_sessions_under = 0;
                self.crisis_epicentre = match self.crisis_epicentre_pin {
                    // PINNED: no draw. The stream's position is where the
                    // last episode left it, so a pinned run and an unpinned
                    // one are not the same random world on this stream --
                    // which is correct, since the pin replaces the draw
                    // rather than overriding its result.
                    Some(pin) => pin,
                    None => {
                        self.crisis_epicentre_rng
                            .site(Site::CrisisEpicentreU, 0);
                        let u = self.crisis_epicentre_rng.next_f64();
                        match crate::sectors::draw_crisis_epicentre(u) {
                            Some(i) => i as i32,
                            None => -1,
                        }
                    }
                };
            }
        } else if above {
            self.crisis_sessions_under = 0;
        } else {
            self.crisis_sessions_under += 1;
            // `>=`, so a value at or below zero ends the episode on the
            // first session back under the threshold rather than never.
            if (self.crisis_sessions_under as f64) >= self.params.crisis_epicentre_end_sessions {
                self.crisis_in_episode = false;
                self.crisis_sessions_under = 0;
                self.crisis_epicentre = -1;
            }
        }
    }

    /// The sector key the running episode's epicentre was drawn at, or
    /// `None` -- no episode, the dial off, the episode drew `none`, or the
    /// sector it drew has no name in THIS roster.
    ///
    /// That last one is the tick's question rather than the episode's, which
    /// is why it is answered here and not in `crisis_episode`: the draw
    /// happened and the state records it either way. But the multiples the
    /// tick applies move the roster's crisis variance from one sector to the
    /// others, and with nobody in the epicentre there is nothing to move it
    /// TO -- every name would be scaled down and the roster would simply go
    /// quiet in a crisis, which is the one thing this mechanism promises not
    /// to do. An epicentre nobody is in is not an epicentre.
    ///
    /// `'static`, because it is a key from the sector table rather than a
    /// borrow of this engine, which is what lets the tick's inputs carry it
    /// while the rest of the engine is borrowed mutably.
    pub fn crisis_epicentre_key(&self) -> Option<&'static str> {
        if !self.crisis_in_episode || self.crisis_epicentre < 0 {
            return None;
        }
        let key = crate::sectors::SECTORS
            .get(self.crisis_epicentre as usize)
            .map(|s| s.key)?;
        if self.companies.iter().any(|c| c.sector == key) {
            Some(key)
        } else {
            None
        }
    }

    /// The episode state: whether one is running, how many consecutive
    /// sessions it has spent under the threshold, and its epicentre as the
    /// sector key or the string `"none"`.
    ///
    /// The epicentre reads `None` only when no episode is running, so a
    /// caller can tell "no crisis" from "a crisis with no epicentre", which
    /// the tick deliberately cannot.
    pub fn crisis_episode(&self) -> (bool, i64, Option<&'static str>) {
        let who = if !self.crisis_in_episode {
            None
        } else if self.crisis_epicentre < 0 {
            Some("none")
        } else {
            crate::sectors::SECTORS
                .get(self.crisis_epicentre as usize)
                .map(|s| s.key)
        };
        (self.crisis_in_episode, self.crisis_sessions_under, who)
    }

    /// The VIX level above which this engine's crisis behaviour runs:
    /// [`ModelParams::crisis_vix_threshold`], 30.88325108 on `pt-v13` onward
    /// and 25.5 before. A host with crisis gates of its own should read
    /// this rather than copy the number, which differs between presets.
    ///
    /// It is a coefficient like any other: read it here or as
    /// `engine.params().crisis_vix_threshold`, and set it on the
    /// `ModelParams` before construction with
    /// `ModelParams::with_override("crisis_vix_threshold", x)`. The dollar's
    /// safe-haven bid has its own threshold,
    /// [`ModelParams::usd_crisis_vix_threshold`] (25.5 on every preset),
    /// which does not follow this one.
    pub fn crisis_vix_threshold(&self) -> f64 {
        self.params.crisis_vix_threshold
    }

    /// Whether the VIX is above [`Engine::crisis_vix_threshold`] now: the
    /// test the crisis gates themselves apply (the universe's stress
    /// memory, the tick's crisis spike, the economy's crisis premium and the
    /// start of a crisis episode). A strict `>`, as theirs is.
    ///
    /// This is the instantaneous gate. A crisis EPISODE
    /// ([`Engine::crisis_episode`]) starts on the same test but ends only
    /// after `crisis_epicentre_end_sessions` sessions back under the
    /// threshold, so the two can disagree for up to that many sessions.
    pub fn vix_above_crisis_threshold(&self) -> bool {
        self.economy.vix > self.params.crisis_vix_threshold
    }

    /// The raw episode state, for the snapshot.
    pub fn crisis_episode_raw(&self) -> (bool, i64, i32, Option<i32>) {
        (
            self.crisis_in_episode,
            self.crisis_sessions_under,
            self.crisis_epicentre,
            self.crisis_epicentre_pin,
        )
    }

    /// Restore the episode state, for a snapshot.
    pub fn set_crisis_episode_raw(
        &mut self,
        in_episode: bool,
        sessions_under: i64,
        epicentre: i32,
        pin: Option<i32>,
    ) {
        self.crisis_in_episode = in_episode;
        self.crisis_sessions_under = sessions_under;
        self.crisis_epicentre = epicentre;
        self.crisis_epicentre_pin = pin;
    }

    /// Pin the epicentre, or clear the pin.
    ///
    /// `Some(-1)` pins `none`, a crisis with no epicentre; `None` clears the
    /// pin and hands the next episode back to the draw.
    pub fn set_crisis_epicentre_pin(&mut self, pin: Option<i32>) {
        self.crisis_epicentre_pin = pin;
    }

    /// Whether tonight's close will SET the market factor's variance from
    /// the VIX rather than step toward it. See `vix_sets_variance_pending`.
    pub fn vix_sets_variance_pending(&self) -> bool {
        self.vix_sets_variance_pending
    }

    /// Mark tonight's close as a forced one, or unmark it. The close
    /// consumes the mark. See [`Self::close_market`] and
    /// `MarketVarianceState::close_day_forced`.
    pub fn set_vix_sets_variance_pending(&mut self, on: bool) {
        self.vix_sets_variance_pending = on;
    }

    /// Today's pins that tonight's corporate yield reads, as bits
    /// ([`PIN_VIX`], [`PIN_CORPORATE`]). See `macro_pins_today`.
    pub fn macro_pins_today(&self) -> u16 {
        self.macro_pins_today
    }

    /// Add today's pins (an OR; the close clears them). A spread pin is
    /// always kept; the VIX and corporate bits while `corporate_yield_daily`
    /// is on, the VIX bit also under `pinned_vix_feedback`; every bit under
    /// `macro_pins_hold`; the cycle bit also while `cycle_nowcast_accuracy` is
    /// set; the VIX bit also while `vix_stress_premium` is set. Each is kept
    /// only while something reads it.
    pub fn mark_macro_pins_today(&mut self, bits: u16) {
        let mut keep = PIN_SPREAD;
        if self.params.corporate_yield_daily != 0.0 {
            keep |= PIN_VIX | PIN_CORPORATE;
        }
        if self.params.pinned_vix_feedback != 0.0 && self.carries_vix_feedback() {
            keep |= PIN_VIX;
        }
        if self.params.macro_pins_hold != 0.0 {
            keep |= PIN_ALL;
        }
        if self.params.cycle_nowcast_accuracy != 0.0 {
            keep |= PIN_CYCLE;
        }
        // The published VIX's premium is another reader of a VIX pin:
        // tonight's close keeps its memory at 0.0 (`Engine::published_vix`,
        // `vix_stress_premium`).
        if self.params.vix_stress_premium != 0.0 {
            keep |= PIN_VIX;
        }
        // And the cycle's volatility multiplier, which a pinned VIX or a
        // pinned phase withholds (`market_vol_cycle_pin_neutral`,
        // `market_vol_cycle_pin_phase`). The phase bit's other readers act
        // only under their own dials (`macro_pins_hold`,
        // `cycle_nowcast_accuracy`).
        if self.params.market_vol_cycle_ratio != 0.0 {
            if self.params.market_vol_cycle_pin_neutral != 0.0 {
                keep |= PIN_VIX;
            }
            if self.params.market_vol_cycle_pin_phase != 0.0 {
                keep |= PIN_CYCLE;
            }
        }
        self.macro_pins_today |= bits & keep;
    }

    /// A PINNED VIX IS PRICED WHEN IT IS PUBLISHED (`pinned_vix_feedback`):
    /// the volatility feedback's smoothed exposure moves the dial's share of
    /// the way to the VIX's own excess over the knee as the pin writes it
    /// (all of it at 1.0), before the pin's re-mark, and the close holds it
    /// there (`advance_day_with`). Under `pinned_vix_variance_share` the
    /// discount's change is recorded for the session's market draws
    /// (`market_sigma_today`). Nothing with the dial off, or without the
    /// gain and the half-life.
    ///
    /// Returns whether the credit leg moved the corporate yield, so the
    /// caller re-marks the corporate index.
    pub fn price_pinned_vix(&mut self, vix_before: f64) -> bool {
        if self.params.pinned_vix_feedback == 0.0 || !self.carries_vix_feedback() {
            return false;
        }
        let exposure_before = self.economy.vix_feedback;
        // The knee's excess, or the calm line's above it (`pinned_vix_calm_knee`).
        let excess = crate::market::tick::pinned_vix_excess(&self.params, self.economy.vix);
        // The whole gap at 1.0, bit for bit; a share of it below.
        let w = self.params.pinned_vix_feedback;
        let stepped = if w == 1.0 {
            excess
        } else {
            exposure_before + w * (excess - exposure_before)
        };
        // A HELD PIN PRICES ONCE (`pinned_vix_priced_cap`): the step never
        // lifts the exposure past the share of the target priced on the day
        // it lands, or past the exposure already standing, so a VIX held at
        // one level closes no more of the gap on the sessions after; a step
        // down is left as it is. A branch, so 0.0 is the step as it stood.
        self.economy.vix_feedback = if self.params.pinned_vix_priced_cap != 0.0 {
            crate::mathx::min(stepped, crate::mathx::max(exposure_before, w * excess))
        } else {
            stepped
        };
        // THE PRICED MOVE IS PART OF THE DAY'S VARIANCE
        // (`pinned_vix_variance_share`): the discount's change is recorded
        // for the session's market factor, which draws only what the
        // variance leaves after it.
        if self.params.pinned_vix_variance_share != 0.0 {
            self.pinned_vix_jump += self.params.fair_value_vix_discount
                * (self.economy.vix_feedback - exposure_before);
        }
        // THE CREDIT LEG: the chain's own VIX slope (the meeting formula's
        // 2 bp a point times the phase's multiplier) on the pin's change, at
        // the pin, where the close would have charged it had the VIX moved
        // there. With a VIX pin the close charges the credit spread nothing
        // (`YieldDials::vix_pinned`), so a pinned rise never reached the
        // corporate yield while the fall after the release did. Only under
        // `macro_pins_hold`, which holds the VIX through the close, so the
        // next morning's pin starts from the pinned level and nothing
        // ratchets; only with `corporate_yield_daily`, whose daily term this
        // is; and never over a level or a spread a caller pinned today.
        if self.params.macro_pins_hold == 0.0
            || self.params.corporate_yield_daily == 0.0
            || self.macro_pins_today & (PIN_CORPORATE | PIN_SPREAD) != 0
        {
            return false;
        }
        // The multiplier the market prices (`cycle_nowcast_accuracy`,
        // `corporate_spread_cycle`), as the close's daily term reads it;
        // the true phase's with both at 0.0.
        let m = if self.params.cycle_nowcast_accuracy != 0.0 || self.params.corporate_spread_cycle != 0.0 {
            self.priced_spread_multiplier(self.economy.cycle_phase)
        } else {
            crate::economy::central_bank::cycle_spread_multiplier(self.economy.cycle_phase)
        };
        // The slope less `corporate_spread_vix_cut`, as the close charges
        // it; a branch, so at 0.0 the slope is the literal that stood.
        let slope = if self.params.corporate_spread_vix_cut != 0.0 {
            0.02 * (1.0 - self.params.corporate_spread_vix_cut)
        } else {
            0.02
        };
        let e = &mut self.economy;
        e.corporate_bond_yield = crate::mathx::max(
            e.corporate_bond_yield + slope * m * (e.vix - vix_before),
            e.treasury_yield_10y + crate::economy::central_bank::CORPORATE_SPREAD_FLOOR,
        );
        true
    }

    /// The market factor's sigma for today's draws: the state's, or, when
    /// today's VIX pins moved the volatility-feedback discount
    /// (`pinned_vix_variance_share`), the state's times
    /// `sqrt(max(1 - J^2 / v, 1 - share))` for the priced move `J` and the
    /// state's daily variance `v`. The priced move is the part of the day's
    /// variance the VIX accounts for; the draw carries the rest, and at
    /// least `1 - share` of it. The state's sigma, bit for bit, with no such
    /// move.
    pub fn market_sigma_today(&self) -> f64 {
        let sigma = self.market_sigma_before_day_scale();
        // The day's t scale (`market_day_tail_df`): exactly 1.0 with the
        // dial off, so the product is skipped and the sigma is the bits it was.
        if self.market_day_scale == 1.0 {
            sigma
        } else {
            sigma * crate::mathx::sqrt(self.market_day_scale)
        }
    }

    /// The session's market sigma before the day's t scale: the state's
    /// sigma, less a priced VIX move's share (`pinned_vix_variance_share`).
    fn market_sigma_before_day_scale(&self) -> f64 {
        let sigma = self.market_vol.sigma_daily();
        let j = self.pinned_vix_jump;
        if j == 0.0 || self.params.pinned_vix_variance_share == 0.0 {
            return sigma;
        }
        let v = self.market_vol.variance();
        if !(v > 0.0) {
            return sigma;
        }
        let keep = crate::mathx::max(1.0 - j * j / v, 1.0 - self.params.pinned_vix_variance_share);
        sigma * crate::mathx::sqrt(keep)
    }

    /// The day's t scale on the market factor's variance
    /// (`market_day_tail_df`), 1.0 with the dial off or before the open
    /// drew one.
    pub fn market_day_scale(&self) -> f64 {
        self.market_day_scale
    }

    /// Put the day's t scale back, for checkpoints.
    pub fn set_market_day_scale(&mut self, value: f64) {
        self.market_day_scale = value;
    }

    /// Draw the day's t scale (`market_day_tail_df`) on the overnight
    /// stream: `m = (nu - 2) / X`, `X = 2 G`, `G` a gamma of shape `nu / 2`
    /// by Marsaglia and Tsang, so `E[m] = 1` and the day's market factor
    /// is a unit-variance Student t times the state's sigma. The day's
    /// variance `m v` is held under the state's ceiling and never below `v`
    /// by that cap: `m <= max(1, C b / v)`, `C` the ceiling multiple and `b`
    /// the baseline variance.
    fn draw_market_day_scale(&mut self) {
        let nu = self.params.market_day_tail_df;
        if nu == 0.0 {
            return;
        }
        let rng = &mut self.overnight_rng;
        rng.site(Site::MarketDayTailChi2, 0);
        let shape = 0.5 * nu;
        let d = shape - 1.0 / 3.0;
        let c = 1.0 / crate::mathx::sqrt(9.0 * d);
        let mut gamma = shape;
        // Accepted within a few tries at every shape the dial allows
        // (above 0.97 per try at shape 1.5); the bound is a guard, and the
        // mean is kept if it binds.
        for _ in 0..64 {
            let x = rng.next_normal();
            let t = 1.0 + c * x;
            if t <= 0.0 {
                continue;
            }
            let v = t * t * t;
            let u = rng.next_f64();
            if u > 0.0 && crate::mathx::log(u) < 0.5 * x * x + d - d * v + d * crate::mathx::log(v) {
                gamma = d * v;
                break;
            }
        }
        let chi2 = 2.0 * gamma;
        let mut m = if chi2 > 0.0 { (nu - 2.0) / chi2 } else { 1.0 };
        let v = self.market_vol.variance();
        let b = self.params.market_factor_sigma * self.params.market_factor_sigma;
        if v > 0.0 && b > 0.0 {
            let cap = crate::mathx::max(1.0, self.params.market_vol_ceiling_multiple * b / v);
            m = crate::mathx::min(m, cap);
        }
        self.market_day_scale = m;
    }

    /// A market-factor draw as the variance state reads it: the draw
    /// itself, or, on a session whose draws a priced VIX move scaled
    /// (`market_sigma_today`), the draw at the state's full sigma, so the
    /// state evolves as it would have without the scale. The draw itself,
    /// bit for bit, with no such move.
    fn factor_for_state(&self, f: f64) -> f64 {
        // The day's t scale, as much of it as the state reads
        // (`market_day_tail_state_share`): the draw times
        // `m^(-(1 - share) / 2)`. The draw itself with the dial off.
        let f = if self.market_day_scale == 1.0 {
            f
        } else {
            let share = self.params.market_day_tail_state_share;
            if share == 1.0 {
                f
            } else {
                f * crate::mathx::pow(self.market_day_scale, -0.5 * (1.0 - share))
            }
        };
        if self.pinned_vix_jump == 0.0 || self.params.pinned_vix_variance_share == 0.0 {
            return f;
        }
        let full = self.market_vol.sigma_daily();
        let today = self.market_sigma_before_day_scale();
        if today > 0.0 && today != full {
            f * (full / today)
        } else {
            f
        }
    }

    /// Today's priced VIX move (`pinned_vix_variance_share`), for the
    /// snapshot; 0.0 off a pinned session.
    pub fn pinned_vix_jump(&self) -> f64 {
        self.pinned_vix_jump
    }

    /// Put today's priced VIX move back, for checkpoints.
    pub fn set_pinned_vix_jump(&mut self, value: f64) {
        self.pinned_vix_jump = value;
    }

    /// PIN THE CORPORATE SPREAD over the 10-year, in percent: the level is
    /// the 10-year plus it now, and tonight's close holds the spread (the
    /// meeting's re-anchoring included) while the 10-year moves the level.
    /// Held within the meeting formula's own range, 0.8 to 6 per cent. The
    /// later of a level pin and a spread pin on one session is the one the
    /// close holds.
    pub fn pin_corporate_spread(&mut self, spread: f64) {
        let s = crate::mathx::clamp(
            spread,
            crate::economy::central_bank::CORPORATE_SPREAD_FLOOR,
            6.0,
        );
        self.pinned_corporate_spread = s;
        self.economy.corporate_bond_yield = self.economy.treasury_yield_10y + s;
        self.macro_pins_today &= !PIN_CORPORATE;
        self.mark_macro_pins_today(PIN_SPREAD);
    }

    /// A LEVEL PIN on the corporate yield: marked for tonight's close under
    /// the dials that read the mark, and it replaces a spread pinned earlier
    /// today.
    pub fn pin_corporate_level(&mut self) {
        self.macro_pins_today &= !PIN_SPREAD;
        self.mark_macro_pins_today(PIN_CORPORATE);
    }

    /// The spread a caller pinned today, in percent, while `PIN_SPREAD` is
    /// marked; `None` otherwise. The snapshot carries it on that rule.
    pub fn pinned_corporate_spread(&self) -> Option<f64> {
        if self.macro_pins_today & PIN_SPREAD != 0 {
            Some(self.pinned_corporate_spread)
        } else {
            None
        }
    }

    /// A caller pinned the cycle phase: the pin is public news, so the
    /// market's belief goes onto the phase now standing and tonight's close
    /// takes no report (`PIN_CYCLE`), and the anticipation is refreshed on
    /// it. With `cycle_nowcast_accuracy` at 0.0 this is only the refresh the
    /// pin path always made.
    pub fn cycle_phase_pinned(&mut self) {
        if self.params.cycle_nowcast_accuracy != 0.0 {
            self.seed_cycle_nowcast();
            self.mark_macro_pins_today(PIN_CYCLE);
        }
        self.refresh_earnings_anticipation();
    }

    /// A pin that CHANGES the cycle phase starts the new phase's clock, as
    /// the model's own transition does, under `macro_pins_hold`; without
    /// it the new phase inherits the old one's age and the hazard reads
    /// it. Nothing with the dial off or when the phase is unchanged.
    pub fn pin_cycle_phase(&mut self, phase: crate::economy::CyclePhase) {
        if self.params.macro_pins_hold != 0.0 && self.economy.cycle_phase != phase {
            self.economy.months_in_current_phase = 0.0;
        }
        self.economy.cycle_phase = phase;
    }

    /// Restore the marks from a snapshot, as they were, with the spread a
    /// `PIN_SPREAD` mark holds (`pinned_corporate_spread`; 0.0 without it).
    pub fn set_macro_pins_today(&mut self, bits: u16, spread: f64) {
        self.macro_pins_today = 0;
        self.pinned_corporate_spread = if bits & PIN_SPREAD != 0 { spread } else { 0.0 };
        self.mark_macro_pins_today(bits);
    }

    /// The VIX-ratio denominator a FORCED close uses.
    ///
    /// Off `market_vol_vix_excursion` the denominator is the anchor, a
    /// constant of the session, and the free close's own denominator is
    /// returned unchanged: the level the law implies is then an explicit
    /// function of the VIX.
    ///
    /// Under the excursion the denominator is the identity's read-back of
    /// the index variance, which the factor variance being set is part of,
    /// so "the level the law implies at this VIX" is a fixed point: the
    /// denominator `d` at which the variance a forced close sets reads back
    /// as `d`. Setting the target at today's read-back instead would move
    /// tomorrow's read-back by the step just taken, and with the exponent
    /// at 4.9 that feedback overshoots and oscillates. The fixed point is
    /// the level a free run would settle at under the same VIX held for
    /// ever, every other term of the index variance as it stands tonight.
    ///
    /// `h(d) = read_back(forced_level(d)) - d` is strictly decreasing
    /// (the level falls as `d` rises, and the read-back rises with the
    /// level), so the root is unique and is bracketed by the read-backs of
    /// the floor and the ceiling. Found by bisection on those bounds. If the
    /// bracket does not hold -- no shipped preset reaches that -- the free
    /// close's denominator is returned, which is the free law's reading.
    fn forced_vix_denominator(&self, free_denominator: f64, level: f64) -> f64 {
        if self.params.market_vol_vix_excursion == 0.0 {
            return free_denominator;
        }
        let vix = self.economy.vix;
        let premium = self.params.vix_variance_premium;
        let read_back = |v: f64| {
            crate::market::index_var::vix_from_variance(
                premium, self.index_conditional_variance_terms_at(v).total())
        };
        let base = self.params.market_factor_sigma * self.params.market_factor_sigma;
        let mut lo = read_back(base * self.params.market_vol_floor_multiple);
        let mut hi = read_back(base * self.params.market_vol_ceiling_multiple);
        let h = |d: f64| {
            read_back(MarketVarianceState::forced_level(&self.params, d, vix, level)) - d
        };
        if !(lo > 0.0 && hi > lo && h(lo) >= 0.0 && h(hi) <= 0.0) {
            return free_denominator;
        }
        for _ in 0..80 {
            let mid = 0.5 * (lo + hi);
            if h(mid) >= 0.0 {
                lo = mid;
            } else {
                hi = mid;
            }
        }
        0.5 * (lo + hi)
    }

    /// Open a trading day. Call once per day, before its first session.
    ///
    /// Steps the overnight processes (the crisis episode, the overnight gap
    /// in each name's `s`, the day's endogenous news), resets the day's
    /// attribution and anchors the daily open. [`Engine::run_session`] with
    /// `reopen: true` calls this itself.
    ///
    /// It does not advance the day: a host calls
    /// [`Engine::set_current_day`] first, with the trading day counted from
    /// zero.
    pub fn open_market(&mut self) {
        // The live marks belong to a session; this one's are computed below.
        self.rate_live = None;
        self.vix_live = None;
        // THE CRISIS EPISODE, stepped before anything else the session does.
        // At `crisis_epicentre_extra` 0.0 -- every preset before pt-v19's fourth
        // composition of 2026-09-22 -- this
        // returns without touching state or taking a draw.
        self.update_crisis_episode();
        // Attribution is per DAY. Resetting here rather than at close means a
        // caller can still read yesterday's decomposition after the close has
        // run, which is when they would actually want it.
        self.attribution.clear();
        self.attribution.resize(self.companies.len(), [0.0; crate::market::factors::COMPONENT_COUNT]);
        self.noise_parts.clear();
        self.noise_parts.resize(self.companies.len(), [0.0; 3]);
        self.noise_own_scale2.clear();
        self.noise_own_scale2.resize(self.companies.len(), 0.0);
        self.innovation_day.clear();
        if self.night_split_on() {
            self.innovation_day.resize(self.companies.len(), 0.0);
        }
        self.tick_components.clear();
        self.tick_components.resize(self.companies.len(), [0.0; crate::market::factors::TICK_COMPONENT_COUNT]);
        self.tick_fundamental.clear();
        self.tick_fundamental.resize(self.companies.len(), f64::NAN);
        self.tick_anchor.clear();
        self.tick_anchor.resize(self.companies.len(), f64::NAN);
        self.tick_shock.clear();
        self.tick_shock.resize(self.companies.len(), 0.0);
        self.tick_absorbed.clear();
        self.tick_absorbed.resize(self.companies.len(), 0.0);
        self.tick_clamp.clear();
        self.tick_clamp.resize(self.companies.len(), 0.0);
        self.tick_repriced.clear();
        self.tick_repriced.resize(self.companies.len(), 0.0);
        // Not the pending repricing: it carries the close's re-mark across
        // the open to the first print. Only its width follows the roster.
        self.repriced_pending.resize(self.companies.len(), 0.0);
        // Resized only while the arm is on, so a roster that changed between
        // days does not leave a short column behind -- and emptiness keeps
        // meaning "no arm ran" rather than "no companies".
        self.tick_unbounded_print.clear();
        self.tick_liquidity_share.clear();
        if self.settle_depth_counterfactual {
            self.tick_unbounded_print
                .resize(self.companies.len(), f64::NAN);
            self.tick_liquidity_share.resize(self.companies.len(), 0.0);
        }
        // The factor-variance day accumulator is per-day state like the
        // attribution above: an abandoned day must not leak its partial
        // innovation into the next close's update.
        self.market_vol.open_day();

        // The day mark: every stream's position at the open, the active
        // roster and the sector count, so a (day, company) pair addresses
        // its market normals (`market_day_layout`). Ticks are counted as
        // they run.
        let active: Vec<u32> = self
            .companies
            .iter()
            .enumerate()
            .filter(|(_, c)| !c.is_bankrupt && c.is_public)
            .map(|(i, _)| i as u32)
            .collect();
        // The day's own mark counts its ticks from here, so a count a
        // restore carried for the day before is spent.
        self.carried_ticks = None;
        self.day_marks.push(DayMark {
            day: self.current_day,
            positions: self.stream_positions(),
            active,
            sectors: self.sector_keys.len() as u32,
            ticks: 0,
        });

        // Endogenous news for the day (§101, moved here by §117). Two draws
        // per company, ALWAYS, on the NEWS stream: the uniform decides
        // occurrence and the normal decides impact, and at zero intensity
        // `u < 0.0` is false for every u in [0, 1). Same draw discipline as
        // `apply_jumps`, for the same reason. Skipped entirely at zero
        // intensity so every preset before pt-v11 is bit-identical.
        //
        // `open_market` is the one point every spelling of a day passes
        // through exactly once, which is what makes a tick loop and a
        // session agree. It takes no other draws, so moving the generation
        // here left the NEWS stream's own order untouched.
        self.session_news.clear();
        if self.params.endogenous_news_intensity != 0.0 {
            let intensity = self.params.endogenous_news_intensity;
            let sigma = self.params.endogenous_news_sigma;
            for i in 0..self.companies.len() {
                self.news_rng.site(Site::NewsU, i as u32);
                let u = self.news_rng.next_f64();
                self.news_rng.site(Site::NewsZ, i as u32);
                let z = self.news_rng.next_normal();
                if u < intensity {
                    self.session_news.push(crate::market::NewsEvent {
                        company_id: Some(self.companies[i].id.clone()),
                        sector: Some(self.companies[i].sector.clone()),
                        price_impact: Some(sigma * z),
                    });
                }
            }
        }

        // The last close, kept only under a night split: the day's return
        // is then read from it, not from the open the night moved.
        let last_closes: Vec<f64> = if self.night_split_on() {
            self.companies.iter().map(|c| c.stock.price).collect()
        } else {
            Vec::new()
        };

        // The close today's change is measured from, before the night or
        // the open's reset can move a price. Read by nothing that prices.
        self.prior_closes.clone_from(&self.last_closes);

        // The night, before the day's marks are set, so the session band
        // anchors on the post-gap open and the gap sits outside it.
        self.apply_overnight();

        // The dividends: declarations, the ex-date drops and the accrual
        // fair value reads today. Nothing, and no state, with
        // `dividend_payout_share` at 0.0.
        self.apply_dividends();

        reset_daily_prices(&mut self.companies);
        // UNDER A NIGHT SPLIT THE DAY RUNS FROM THE LAST CLOSE. The session
        // carries only part of the day's variance, so a day read from the
        // open would hand the VIX (`vix_return_source`), the forced flow
        // (`last_daily_return`), the per-name close and the volume scale a
        // fraction of the move the day made. `previous_close` is what each
        // of them reads, so it keeps the last close; the open, high and low
        // are the session's. No preset splits the day, and without a split
        // this writes nothing.
        for (c, last) in self.companies.iter_mut().zip(last_closes.iter()) {
            c.stock.previous_close = *last;
        }

        // The rate indices' open: a day's carry if a close came before, then
        // the curve as it now stands, after the close's macro step and any
        // pin written since. No draw. Skipped on an engine without them.
        //
        // Under `rate_close_remark` the close already priced its curve, so
        // the open's move is the night's carry (and any pin since), which is
        // booked for the first tape row's `repriced` as the equities' are.
        if !self.rates.is_empty() {
            let before: Vec<f64> = if self.params.rate_close_remark != 0.0 {
                self.rates.instruments.iter().map(|i| i.price).collect()
            } else {
                Vec::new()
            };
            self.rates.open(&self.economy);
            if self.params.rate_close_remark != 0.0 {
                self.rates.book_open_repricing(&before);
            }
        }

        // The index's base, once, on the prices the first session opens at
        // (`index_level_listed`). Nothing with the switch off.
        self.index_open();

        // The book opens whole: the maker quotes afresh on its inventory,
        // the night has refilled any consumed depth, and a resting order
        // the night moved the price through is matched against the opening
        // ladder at the ladder's prices, as an opening auction would fill
        // it, rather than left crossed for the first agent to pick off.
        if !self.book.is_pristine() {
            self.open_book();
        }

        // The live mark's open: both projections on the session as it opens,
        // so a fill before the first tick already reads the night's gap. The
        // live VIX opens the same way (`vix_intraday_live`).
        self.refresh_live_marks(
            1.0,
            self.params.rate_intraday_live != 0.0 && !self.rates.is_empty(),
            self.params.vix_intraday_live != 0.0,
        );

        // The index futures on the opening prints (`futures_index_listed`):
        // an expiring contract settles on them and the next is listed.
        // Nothing with the switch off.
        self.futures_open();
        // The VIX futures at the open (`futures_vix_listed`): an expiring
        // contract settles on the published VIX and the next is listed.
        self.vix_futures_open();
        // The rate futures' session begins (`futures_rates_listed`).
        self.rate_futures_open();
        // The oil futures at the open (`futures_oil_listed`): an expiring
        // contract settles on the oil price and the next is listed.
        self.oil_futures_open();
    }

    /// The overnight move, applied once per name at the open, before the
    /// day's marks are set.
    ///
    /// Nothing moved a price between sessions before this: the price after
    /// `open_market` was the price after the previous `close_market` on
    /// every name-night, the jump the close wrote to `s` reached the price
    /// through the next session's ticks, and the day bar's open read the
    /// first of them (issue #179). Real large caps carry a fifth to two
    /// fifths of their daily variance overnight: the forty-name reference
    /// panel reads a median share of 0.33 across nine non-crisis windows,
    /// band 0.23 to 0.43, by `tools/calibration/overnight_band.py`.
    ///
    /// # What the night carries
    ///
    /// Two things, and only the second is a draw. The state that changed
    /// between the close and the open is realised in the OPENING PRINT
    /// rather than dribbled through the first ticks: the price the session
    /// opens at is fair value as of the open, with the macro step the close
    /// advanced inside it, times `exp(s)`, with the close's jump inside
    /// that. That is what puts the jump's discontinuity where a real
    /// earnings gap sits, at the open, and it is what gives the night its
    /// shape: the reference panel's nights put 0.50 to 0.68 of their
    /// variance in the largest five percent, against 0.36 to 0.49 for the
    /// sessions and 0.28 for a Gaussian.
    ///
    /// And the night's own diffusion: one normal for the market, one per
    /// sector and one per name on the [`stream::OVERNIGHT`] stream,
    /// composed exactly as a session's factor structure is, beta on the
    /// market factor at its conditional daily sigma, the sector loading on
    /// the sector factor, the name's own GARCH sigma at its idiosyncratic
    /// scale and size multiplier, and the whole scaled by the square root
    /// of `overnight_variance_ratio`. The composition is the session's
    /// because the reference panel's nights and sessions read the same
    /// cross-sectional correlation, 0.77 to 1.47 times each other across
    /// windows with no direction; what differs is the size, which the dial
    /// carries, and the shape, which the jump landing here supplies. The
    /// session's crash amplifier, downside tilt, forced flow and book are
    /// session mechanisms and do not run overnight.
    ///
    /// The move lands on `s`, the channel news and the jump use, so it
    /// reverts on the mispricing's own half-life, about one percent of the
    /// night over the following session, against the panel's pooled
    /// reversal slope of -0.02 with a window range of -0.10 to -0.01. It
    /// is kept out of the close's momentum roll, as the jump's carried
    /// share is, because a gap is not herding.
    ///
    /// # Draw discipline
    ///
    /// The draws are taken unconditionally on their own stream, one normal
    /// for the market, one per sector, one per company in roster order, so
    /// the schedule cannot depend on the dial and no other stream moves. At
    /// a ratio of exactly 0.0 nothing after the draws runs: no state is
    /// touched, and every preset before this dial is bit-identical, the
    /// known-answer digests included, since this stream is not among the
    /// three `draws_consumed` counts. A name that has not ticked yet has
    /// no `s` and is left alone, so the first session opens at the
    /// universe's own prices as it always did.
    fn apply_overnight(&mut self) {
        let sectors = self.sector_keys.len();
        self.overnight_rng.site(Site::OvernightMarketZ, 0);
        let z_market = self.overnight_rng.next_normal();
        let mut z_sector = Vec::with_capacity(sectors);
        for k in 0..sectors {
            self.overnight_rng.site(Site::OvernightSectorZ, k as u32);
            z_sector.push(self.overnight_rng.next_normal());
        }
        let mut z_idio = Vec::with_capacity(self.companies.len());
        for i in 0..self.companies.len() {
            self.overnight_rng.site(Site::OvernightIdioZ, i as u32);
            z_idio.push(self.overnight_rng.next_normal());
        }
        // The day's market t scale (`market_day_tail_df`), after the three
        // sites above and before the night reads the market sigma. Nothing
        // is drawn with the dial off.
        self.draw_market_day_scale();
        self.overnight_moves.clear();
        self.overnight_moves.resize(self.companies.len(), 0.0);
        self.overnight_fair_value_moves.clear();
        self.overnight_fair_value_moves.resize(self.companies.len(), 0.0);
        self.earnings_moves.clear();
        self.earnings_moves.resize(self.companies.len(), 0.0);
        if self.night_split_on() {
            // The night's student-t scales, on a site of their own after the
            // three above, and only while the dial is set: every other
            // model's overnight schedule is the one it was.
            let df = self.params.overnight_idio_df;
            let t_scales: Vec<f64> = if df == 0.0 {
                Vec::new()
            } else {
                let k = df as usize;
                (0..self.companies.len())
                    .map(|i| {
                        self.overnight_rng.site(Site::OvernightIdioChi2, i as u32);
                        let mut chi2 = 0.0;
                        for _ in 0..k {
                            let n = self.overnight_rng.next_normal();
                            chi2 += n * n;
                        }
                        if chi2 > 0.0 { crate::mathx::sqrt((df - 2.0) / chi2) } else { 1.0 }
                    })
                    .collect()
            };
            self.apply_night_split(z_market, &z_sector, &z_idio, &t_scales);
            return;
        }
        let ratio = self.params.overnight_variance_ratio;
        if ratio == 0.0 {
            return;
        }
        let p = &self.params;
        let scale = crate::mathx::sqrt(ratio);
        let market_sigma_daily = self.market_sigma_today();
        let sector_sigma =
            crate::market::tick::sector_sigma_at(p, &self.economy, self.vix_anchor);
        let idio_ratios: Vec<f64> = if self.idio_state_on() {
            (0..self.companies.len()).map(|i| self.idio_ratio_at(i)).collect()
        } else {
            Vec::new()
        };
        let night_vix = crate::market::tick::vix_feedback_exposure(&self.params, &self.economy);
        let econ_view = crate::fair_value::EconomyValuationInputs {
            corporate_bond_yield: Some(self.economy.corporate_bond_yield),
            federal_funds_rate: self.economy.federal_funds_rate,
            qe_pe_boost: Some(self.economy.qe_pe_boost),
            qe_assets_ratio: Some(self.economy.qe_assets_ratio),
        };
        let nominal =
            crate::market::tick::nominal_scale(p, &self.economy, self.nominal_output_base);
        for (index, company) in self.companies.iter_mut().enumerate() {
            if company.is_bankrupt || !company.is_public {
                continue;
            }
            let Some(s) = company.stock.mispricing_s else {
                continue;
            };
            let beta = company.stock.beta.unwrap_or(1.0);
            let market = beta * market_sigma_daily * z_market;
            let sector = match self.sector_keys.iter().position(|k| *k == company.sector) {
                Some(k) => {
                    crate::market::factors::sector_loading_for(p, beta) * sector_sigma * z_sector[k]
                }
                None => 0.0,
            };
            // The overnight path's copy of the tick's absolute floor; see
            // `market::factors`, where the same constant is read from the
            // same field. Two spellings of one floor must move together.
            let daily_sigma = crate::mathx::sqrt(
                crate::mathx::max(company.stock.garch_variance, p.idio_sigma_floor));
            let idio = daily_sigma
                * crate::market::factors::idio_scale_for(p, beta)
                * crate::market::factors::cap_size_multiplier_with(p, company.stock.market_cap)
                * z_idio[index];
            // The idiosyncratic variance state reaches the night's own draw
            // as it reaches the session's. A branch, so nothing is
            // multiplied while it is off.
            let idio = if idio_ratios.is_empty() {
                idio
            } else {
                idio * crate::mathx::sqrt(idio_ratios[index])
            };
            let night = scale * (market + sector + idio);
            let after = crate::market::tick::clamp_s(p, s + night);
            let moved = after - s;
            company.stock.mispricing_s = Some(after);
            if let Some(prev) = company.stock.mispricing_s_prev_close {
                company.stock.mispricing_s_prev_close = Some(prev + moved);
            }
            if let Some(acc) = self.attribution.get_mut(index) {
                acc[crate::market::factors::OVERNIGHT_SLOT] += moved;
            }
            self.overnight_moves[index] = moved;
            // The opening print: fair value as of the open times exp(s),
            // the same valuation the first tick would otherwise have
            // settled the print toward over the session.
            //
            // This reads fair value without the buyback term, so under
            // `buyback_payout_share` the open sits below the tick's model
            // price by the compounded buyback yield and the first tick
            // undoes it. The night split (`overnight_market_share`,
            // `overnight_idio_share`) prints through the tick's own fair
            // value and the buyback term's fixed point
            // (`market::tick::opening_print`) instead. This path keeps the
            // arithmetic it had, because a model that sets
            // `overnight_variance_ratio` alone is a model that existed before
            // the split and must price as it did.
            let valuation = if nominal == 1.0 {
                company.valuation()
            } else {
                crate::market::tick::scale_valuation(company.valuation(), nominal)
            };
            let valuation = match company.stock.fair_value_offset {
                Some(v) if v != 0.0 => {
                    crate::market::tick::scale_valuation(valuation, crate::mathx::exp(v))
                }
                _ => valuation,
            };
            let fv = crate::fair_value::compute_fair_value_at(
                &valuation, &econ_view, p.fair_value_book_floor,
                p.qe_pe_gain, p.qe_pe_stock_gain, p.neutral_discount_rate,
                p.rate_pe_sensitivity,
            )
            .fair_value;
            let fv = crate::market::tick::with_vix_discount(p, fv, night_vix, company.stock.beta);
            let price = crate::mathx::min(
                crate::mathx::max(fv * crate::mathx::exp(after), 0.01),
                p.price_hard_cap,
            );
            // For the tape, as at the re-mark: the first print's `repriced`
            // books the opening print's move from the last close.
            if let Some(v) = self.repriced_pending.get_mut(index) {
                *v += crate::mathx::log(price / company.stock.price);
            }
            company.stock.price = price;
            company.stock.market_cap = price * company.stock.shares_outstanding;
        }
    }

    /// Whether the day is split between the night and the session
    /// (`overnight_market_share` or `overnight_idio_share` set). Off on
    /// every preset.
    pub fn night_split_on(&self) -> bool {
        self.params.overnight_market_share != 0.0 || self.params.overnight_idio_share != 0.0
    }

    /// Whether the earnings calendar runs (`earnings_surprise_sigma` set),
    /// which is when the snapshot and the state hash carry its key. Off on
    /// every preset.
    pub fn carries_earnings(&self) -> bool {
        self.params.earnings_surprise_sigma != 0.0
    }

    /// Whether names hold back part of the earnings cycle for their reports
    /// (`earnings_cycle_report_share`, with the calendar and the cycle on),
    /// which is when the snapshot and the state hash carry the amounts.
    pub fn carries_earnings_withheld(&self) -> bool {
        self.carries_earnings()
            && self.params.earnings_cycle_report_share != 0.0
            && self.params.earnings_cycle_depth != 0.0
    }

    /// What each name holds back of the earnings cycle for its next report.
    pub fn earnings_withheld(&self) -> &[f64] {
        &self.earnings_withheld
    }

    /// Put the held-back amounts back, for a restore. A width mismatch is
    /// refused: the amounts are positional against the roster.
    pub fn set_earnings_withheld(&mut self, values: &[f64]) -> Result<(), String> {
        if values.len() != self.companies.len() {
            return Err(format!(
                "this snapshot carries {} held-back earnings-cycle amounts and the roster \
                 holds {} companies. They are positional against the roster, so this \
                 restore is refused rather than padded or truncated.",
                values.len(), self.companies.len()));
        }
        self.earnings_withheld = values.to_vec();
        Ok(())
    }

    /// The earnings calendar's key. See [`Engine::carries_earnings`].
    pub fn earnings_key(&self) -> u64 {
        self.earnings_key
    }

    /// Put the earnings calendar's key back, for a restore.
    pub fn set_earnings_key(&mut self, key: u64) {
        self.earnings_key = key;
    }

    /// The earnings surprise each name's opening print realised at the last
    /// open, in roster order; 0.0 where none did.
    pub fn earnings_moves(&self) -> &[f64] {
        &self.earnings_moves
    }

    /// The reports ahead: `(roster index, session)` for every public,
    /// solvent name's reaction sessions in `[from_day, from_day + horizon)`,
    /// ordered by session and then roster. Dates only: the surprise is a
    /// draw nobody reads before the opening print realises it. Empty with
    /// the calendar off.
    pub fn earnings_calendar(&self, from_day: i64, horizon: i64) -> Vec<(usize, i64)> {
        let mut out = Vec::new();
        if !self.carries_earnings() || horizon <= 0 {
            return out;
        }
        let q = crate::earnings::QUARTER_SESSIONS;
        let first_q = std::cmp::max(from_day, 0).div_euclid(q);
        let last_q = std::cmp::max(from_day + horizon - 1, 0).div_euclid(q);
        for (i, c) in self.companies.iter().enumerate() {
            if !c.is_public || c.is_bankrupt {
                continue;
            }
            let h = crate::earnings::id_hash(&c.id);
            for quarter in first_q..=last_q {
                let d = crate::earnings::reaction_session(self.earnings_key, h, quarter);
                if d >= from_day && d < from_day + horizon {
                    out.push((i, d));
                }
            }
        }
        out.sort_by_key(|&(i, d)| (d, i));
        out
    }

    /// Whether roster name `index` reacts to a report on `day`, and the
    /// multiple its volume trades at if so (`earnings_volume_multiple`).
    /// EMPTY with the calendar off or no multiple set, which the tick reads
    /// as no multiple.
    fn earnings_volume_column(&self) -> Vec<f64> {
        let m = self.params.earnings_volume_multiple;
        if !self.carries_earnings() || m == 0.0 || m == 1.0 {
            return Vec::new();
        }
        self.companies
            .iter()
            .map(|c| {
                let h = crate::earnings::id_hash(&c.id);
                match crate::earnings::reacts_on(self.earnings_key, h, self.elapsed_days) {
                    Some(_) => m,
                    None => 1.0,
                }
            })
            .collect()
    }

    /// Whether the night's market draw is kept for the session's live
    /// lagged-wire condition: a night split on the market
    /// (`overnight_market_share`) with the lagged wire read live
    /// (`market_beta_down_asym_lag` and `market_beta_down_asym_lag_live`
    /// set). Also when the snapshot and the state hash carry it. Off on
    /// every preset.
    pub fn carries_night_market_factor(&self) -> bool {
        self.params.overnight_market_share != 0.0
            && self.params.market_beta_down_asym_lag != 0.0
            && self.params.market_beta_down_asym_lag_live != 0.0
    }

    /// Tonight's market-factor draw, per unit of beta. See the field.
    pub fn night_market_factor(&self) -> f64 {
        self.night_market_factor
    }

    /// Put tonight's market-factor draw back, for a restore.
    pub fn set_night_market_factor(&mut self, value: f64) {
        self.night_market_factor = value;
    }

    /// Each name's earnings unit: its NON-MARKET daily sigma, the own GJR
    /// sigma at its idiosyncratic scale and size multiplier and its sector
    /// loading on the sector's sigma (the sector state's own where it runs),
    /// in quadrature. What a residual on the market reads as the name's
    /// idiosyncratic sd, the unit the real reaction days are measured in.
    /// The same arithmetic `apply_night_split` prices the surprise in, read
    /// on the state now standing; it takes no draw.
    fn earnings_unit_sigma(&self, company: &TickCompany) -> f64 {
        let p = &self.params;
        let beta = company.stock.beta.unwrap_or(1.0);
        let shared_sigma =
            crate::market::tick::sector_sigma_at(p, &self.economy, self.vix_anchor);
        let sector_on = p.sector_vol_alpha != 0.0 || p.sector_vol_beta != 0.0;
        let sector_sigma = match self.sector_keys.iter().position(|k| *k == company.sector) {
            Some(k) => {
                let sigma = if sector_on {
                    match self.sector_variance.get(k) {
                        Some(v) if *v > 0.0 => shared_sigma * crate::mathx::sqrt(*v),
                        _ => shared_sigma,
                    }
                } else {
                    shared_sigma
                };
                crate::market::factors::sector_loading_for(p, beta) * sigma
            }
            None => 0.0,
        };
        let daily_sigma = crate::mathx::sqrt(
            crate::mathx::max(company.stock.garch_variance, p.idio_sigma_floor));
        let own = daily_sigma
            * crate::market::factors::idio_scale_for(p, beta)
            * crate::market::factors::cap_size_multiplier_with(p, company.stock.market_cap);
        crate::mathx::sqrt(own * own + sector_sigma * sector_sigma)
    }

    /// THE REACTION SESSION'S OWN DISCOVERY AND THE FOLLOW-THROUGH, walked
    /// into fair value one open minute at a time (`earnings_session_sigma`
    /// on a name's reaction session, `earnings_followthrough_sigma` on the
    /// session after). Each minute `m` moves the name's fair-value level by
    /// `dv = sigma u tick_scale intraday_vol(m / 390) / sqrt(K)` with the
    /// Ito term (`dv - dv^2 / 2`), `u` a standard normal KEYED on (the
    /// calendar's key, the name, the quarter, the part, the minute), `sigma`
    /// the part's size times the name's earnings unit and `K` the session's
    /// intraday variance factor, so a full session carries `sigma^2` and
    /// `exp(v)` is a martingale. The price follows through `fv exp(s)`: the
    /// session trades the information in as it arrives, with no drift and
    /// no first-tick jump.
    ///
    /// Until the review of 50dfeed each part joined fair value in one piece
    /// straight after the opening print, so it printed on the session's
    /// first tick: that tick carried 1.15 times the reaction session's
    /// idiosyncratic variance, and the night's share of the reaction day
    /// (G3) passed only because a 9:30:01 jump counted as session.
    ///
    /// Keyed, so it takes no draw from any stream, a fork taken mid-session
    /// walks the same minutes, and nothing beyond the key and the
    /// fair-value levels (both already carried with the calendar on) is
    /// state. Called only on an open minute with the calendar on and a part
    /// set.
    fn walk_earnings_sessions(&mut self, time: GameTime) {
        let p = &self.params;
        let (ss, sf) = (p.earnings_session_sigma, p.earnings_followthrough_sigma);
        if ss == 0.0 && sf == 0.0 {
            return;
        }
        let minute = (time.hour - 9) * 60 + (time.minute - 30);
        // The session's 390 open minutes, as the tick draws them.
        const MINUTES: i64 = 390;
        if !(0..MINUTES).contains(&minute) {
            return;
        }
        let per_minute = crate::market::hours::intraday_vol(minute as f64 / MINUTES as f64)
            / crate::mathx::sqrt(
                MINUTES as f64 * crate::market::index_var::intraday_variance_factor());
        let key = self.earnings_key;
        let day = self.elapsed_days;
        let mut moves: Vec<(usize, f64)> = Vec::new();
        for (i, c) in self.companies.iter().enumerate() {
            if c.is_bankrupt || !c.is_public || c.stock.mispricing_s.is_none() {
                continue;
            }
            let name = crate::earnings::id_hash(&c.id);
            let mut sd2 = 0.0;
            let mut u = 0.0;
            if ss != 0.0 {
                if let Some(q) = crate::earnings::reacts_on(key, name, day) {
                    u += ss * crate::earnings::session_unit(key, name, q, minute);
                    sd2 += ss * ss;
                }
            }
            if sf != 0.0 {
                if let Some(q) = crate::earnings::reacts_on(key, name, day - 1) {
                    u += sf * crate::earnings::followthrough_unit(key, name, q, minute);
                    sd2 += sf * sf;
                }
            }
            if sd2 == 0.0 {
                continue;
            }
            let dv = u * self.earnings_unit_sigma(c) * per_minute;
            moves.push((i, dv));
        }
        for (i, dv) in moves {
            let c = &mut self.companies[i];
            let v = c.stock.fair_value_offset.unwrap_or(0.0);
            c.stock.fair_value_offset = Some(v + (dv - 0.5 * dv * dv));
        }
    }

    /// The night as a share of the day (`overnight_market_share`,
    /// `overnight_idio_share`), and the earnings reports it realises.
    ///
    /// The market factor's night is `sqrt(wm)` of its conditional daily
    /// sigma and joins the day's factor innovation, so the market GJR steps
    /// on the whole day; each sector's is `sqrt(wi)` of the sector's sigma
    /// (the sector state's own where it runs, and then it joins the
    /// sector's day factor too); each name's own is `sqrt(wi)` of its GARCH
    /// sigma at its idiosyncratic scale and size multiplier, a student t
    /// under `overnight_idio_df`. The session draws the rest
    /// (`market::tick`), so the night and the session sum to the day.
    ///
    /// The market draw takes the session's down tilt and lagged wire, with
    /// their mean given back as the tick gives it, but not the crash
    /// amplifier or the crisis injection. The whole night joins the name's
    /// `random_noise` slot (its GJR's innovation) and the noise split; the
    /// own part moves the fair-value level at `fair_value_news_share` and
    /// the market part at the market's permanent share, only its plain
    /// loading under `fair_value_market_linear`, as the tick does. The move
    /// lands on `s` and is kept out of the momentum roll.
    ///
    /// Then the earnings report, on a name's reaction session: the surprise
    /// joins the fair-value level before the print, so the open prints it.
    /// The reaction session's own part and the session after's are walked
    /// in through those sessions by [`Engine::walk_earnings_sessions`]. The
    /// opening print
    /// is priced through the tick's fair value with the buyback fixed point
    /// (`market::tick::opening_print`).
    fn apply_night_split(
        &mut self,
        z_market: f64,
        z_sector: &[f64],
        z_idio: &[f64],
        t_scales: &[f64],
    ) {
        let p = &self.params;
        let sm = crate::mathx::sqrt(p.overnight_market_share);
        let si = crate::mathx::sqrt(p.overnight_idio_share);
        let market_sigma_daily = self.market_sigma_today();
        let prev_down = self.market_vol.prev_day_down();
        // The night's market factor, per unit of beta, before the tilt.
        let f = sm * market_sigma_daily * z_market;
        // What the variance state reads: `f` itself unless a priced VIX
        // move scaled the draw (`factor_for_state`).
        let f_state = self.factor_for_state(f);
        self.market_vol.accumulate(f_state);
        // Kept for the session's live lagged-wire condition, which reads the
        // day factor less it (see the field). Written only where that wire
        // reads it, so the state carried is the state read.
        if self.carries_night_market_factor() {
            self.night_market_factor = f_state;
        }
        // The sector draws, at the sector state's sigma where it runs.
        let shared_sigma =
            crate::market::tick::sector_sigma_at(p, &self.economy, self.vix_anchor);
        let sector_on = p.sector_vol_alpha != 0.0 || p.sector_vol_beta != 0.0;
        let sector_draws: Vec<(f64, f64)> = z_sector
            .iter()
            .enumerate()
            .map(|(k, z)| {
                let sigma = if sector_on {
                    match self.sector_variance.get(k) {
                        Some(v) if *v > 0.0 => shared_sigma * crate::mathx::sqrt(*v),
                        _ => shared_sigma,
                    }
                } else {
                    shared_sigma
                };
                (sigma, si * sigma * z)
            })
            .collect();
        let (sector_sigmas, sector_draws): (Vec<f64>, Vec<f64>) = sector_draws.into_iter().unzip();
        if sector_on {
            for (k, g) in sector_draws.iter().enumerate() {
                if let Some(acc) = self.sector_day_factor.get_mut(k) {
                    *acc += *g;
                }
            }
        }
        let tilt = if p.market_beta_down_asym != 0.0 && f < 0.0 {
            1.0 + p.market_beta_down_asym
        } else {
            1.0
        };
        let lag = if p.market_beta_down_asym_lag != 0.0 && prev_down {
            1.0 + p.market_beta_down_asym_lag
        } else {
            1.0
        };
        // The tilt's mean given back, as `market::factors` gives it back at
        // the draw's own sigma, and the WHOLE of the mean the lagged wire's
        // multiple adds to it, whatever `market_beta_down_asym_lag_recentre`
        // says. The session may carry an un-recentred wire (pt-v20 does:
        // lag 0.46, lag recentre 0.0), a drift of about -10 bp a day keyed
        // on yesterday's sign; applied to the night on the same condition,
        // the night and the session drifted together and the equal-weight
        // session return followed the market's gap (slope +0.099 against a
        // real +0.022, the forty 2015-2025). The night keeps the wire's
        // variance, which is what carries the lagged correlation asymmetry
        // into the day, and injects no mean from it.
        let recentre_unit = if p.market_beta_down_asym == 0.0 {
            0.0
        } else {
            let unit = p.market_beta_down_asym * (sm * market_sigma_daily)
                / crate::mathx::sqrt(2.0 * std::f64::consts::PI);
            p.market_beta_down_asym_recentre * unit + (lag - 1.0) * unit
        };
        let permanent = p.fair_value_news_share != 0.0 || p.fair_value_market_share != 0.0;
        let psi = p.fair_value_news_share;
        // The market's permanent share at the ceiling the cycle scales
        // (`market_vol_cycle_cap_relative`), as the session's ticks read it;
        // exactly 1.0 with the cycle off.
        let psim = crate::market::tick::market_permanent_share(
            p, market_sigma_daily, self.market_vol_cycle_cap_scale());
        let earnings = p.earnings_surprise_sigma != 0.0;
        let withhold = earnings
            && p.earnings_cycle_report_share != 0.0
            && p.earnings_cycle_depth != 0.0;
        if withhold && self.earnings_withheld.len() != self.companies.len() {
            self.earnings_withheld.resize(self.companies.len(), 0.0);
        }
        let key = self.earnings_key;
        let day = self.elapsed_days;
        // The idiosyncratic variance state (`idio_vol_alpha`) reaches the
        // night's own draw as it reaches the session's: through the own unit,
        // so `noise_own_scale2` carries it and the close's `u^2` reads the
        // whole day's own noise against the whole day's expected variance.
        // EMPTY while the state is off, a branch.
        let idio_ratios: Vec<f64> = if self.idio_state_on() {
            (0..self.companies.len()).map(|i| self.idio_ratio_at(i)).collect()
        } else {
            Vec::new()
        };
        for (index, company) in self.companies.iter_mut().enumerate() {
            if company.is_bankrupt || !company.is_public {
                continue;
            }
            let Some(s) = company.stock.mispricing_s else {
                continue;
            };
            let beta = company.stock.beta.unwrap_or(1.0);
            let market = beta * f * tilt * lag + beta * recentre_unit;
            let (sector, sector_sigma) =
                match self.sector_keys.iter().position(|k| *k == company.sector) {
                    Some(k) => {
                        let loading = crate::market::factors::sector_loading_for(p, beta);
                        (loading * sector_draws[k], loading * sector_sigmas[k])
                    }
                    None => (0.0, 0.0),
                };
            let daily_sigma = crate::mathx::sqrt(
                crate::mathx::max(company.stock.garch_variance, p.idio_sigma_floor));
            let own_unit = crate::market::factors::idio_scale_for(p, beta)
                * crate::market::factors::cap_size_multiplier_with(p, company.stock.market_cap);
            let own_unit = match idio_ratios.get(index) {
                Some(&r) => own_unit * crate::mathx::sqrt(r),
                None => own_unit,
            };
            let t = t_scales.get(index).copied().unwrap_or(1.0);
            let idio = si * daily_sigma * own_unit * z_idio[index] * t;
            let own = sector + idio;
            let night = market + own;
            let after = crate::market::tick::clamp_s(p, s + night);
            let moved = after - s;
            if let Some(prev) = company.stock.mispricing_s_prev_close {
                company.stock.mispricing_s_prev_close = Some(prev + moved);
            }
            // The night counts once in the attribution, in `overnight`, so
            // the day's slots sum to its change in `s` and match the tape.
            // The GJR still steps on it: it starts the day's innovation.
            if let Some(acc) = self.attribution.get_mut(index) {
                acc[crate::market::factors::OVERNIGHT_SLOT] += moved;
            }
            if let Some(acc) = self.innovation_day.get_mut(index) {
                *acc += night;
            }
            if let Some(parts) = self.noise_parts.get_mut(index) {
                parts[0] += market;
                parts[1] += sector;
                parts[2] += idio;
            }
            if let Some(k2) = self.noise_own_scale2.get_mut(index) {
                *k2 += (si * own_unit) * (si * own_unit);
            }
            self.overnight_moves[index] = moved;
            let mut s_now = after;
            // THE PERMANENT SHARE, as the tick's: the own part at
            // `fair_value_news_share`, the market's plain loading (or the
            // whole market part off `fair_value_market_linear`) at the
            // market's permanent share.
            if permanent {
                let market_perm = if p.fair_value_market_linear == 0.0 { market } else { beta * f };
                let dv = if p.fair_value_market_share == 0.0 {
                    psi * own
                } else {
                    psi * own + psim * market_perm
                };
                if dv != 0.0 {
                    let s_new = crate::market::tick::clamp_s(p, after - dv);
                    if let Some(prev) = company.stock.mispricing_s_prev_close {
                        company.stock.mispricing_s_prev_close = Some(prev - dv);
                    }
                    if let Some(acc) = self.attribution.get_mut(index) {
                        acc[crate::market::factors::FAIR_VALUE_SLOT] += s_new - after;
                    }
                    // For the tape, beside `overnight_moves`: the first
                    // row's `fair_value_shift` carries it, or the columns
                    // miss that row's change in `s` by the night's
                    // permanent part.
                    if let Some(slot) = self.overnight_fair_value_moves.get_mut(index) {
                        *slot = s_new - after;
                    }
                    let v = company.stock.fair_value_offset.unwrap_or(0.0);
                    company.stock.fair_value_offset = Some(v + (dv - 0.5 * dv * dv));
                    s_now = s_new;
                }
            }
            company.stock.mispricing_s = Some(s_now);
            // THE REPORT. The surprise joins the fair-value level before
            // the print, so the open prints it. Its unit is the name's
            // NON-MARKET daily sigma, its own and its sector's draws
            // together: what a residual on the market reads as the name's
            // idiosyncratic sd, the unit the real reaction days are
            // measured in (3.47 sd, the forty names 2015-2025).
            let own_sigma = crate::mathx::sqrt(
                (daily_sigma * own_unit) * (daily_sigma * own_unit) + sector_sigma * sector_sigma);
            let name = if earnings { crate::earnings::id_hash(&company.id) } else { 0 };
            let quarter = if earnings { crate::earnings::reacts_on(key, name, day) } else { None };
            if quarter.is_some() && withhold {
                // The cycle this name has held back since its last report.
                let w = std::mem::take(&mut self.earnings_withheld[index]);
                if w != 0.0 {
                    let v = company.stock.fair_value_offset.unwrap_or(0.0);
                    company.stock.fair_value_offset = Some(v + w);
                }
            }
            if let Some(q) = quarter {
                let x = crate::earnings::mean_one(
                    p.earnings_surprise_sigma * own_sigma,
                    crate::earnings::surprise_unit(key, name, q, p.earnings_surprise_df));
                let v = company.stock.fair_value_offset.unwrap_or(0.0);
                company.stock.fair_value_offset = Some(v + x);
                self.earnings_moves[index] = x;
            }
            let last = company.stock.price;
            let price = crate::market::tick::opening_print(
                p, &self.economy, self.nominal_output_base, day, company, s_now);
            if let Some(v) = self.repriced_pending.get_mut(index) {
                *v += crate::mathx::log(price / last);
            }
            company.stock.price = price;
            company.stock.market_cap = price * company.stock.shares_outstanding;
            // The reaction session's own discovery and the follow-through on
            // the session after are NOT set here: they are walked into fair
            // value tick by tick through those sessions
            // (`Engine::walk_earnings_sessions`), so the session trades them
            // in rather than printing them on its first tick.
        }
    }

    /// The move each name's `s` took at the last open under the overnight
    /// process, in roster order; 0.0 where nothing moved.
    pub fn overnight_moves(&self) -> &[f64] {
        &self.overnight_moves
    }

    /// What each name's `s` gave up to its fair value at the last open
    /// under a night split with a permanent share, in roster order; 0.0
    /// where nothing moved. The tape's `fair_value_shift` on the day's first
    /// row.
    pub fn overnight_fair_value_moves(&self) -> &[f64] {
        &self.overnight_fair_value_moves
    }

    /// The move each name's `s` took at the last open's ex-date drop, in
    /// roster order; 0.0 where nothing went ex.
    pub fn dividend_moves(&self) -> &[f64] {
        &self.dividend_moves
    }

    /// True when the model pays dividends (`dividend_payout_share` set):
    /// the snapshot and the state hash carry the per-name states only then.
    pub fn carries_dividends(&self) -> bool {
        self.params.dividend_payout_share != 0.0
    }

    /// Each name's dividend state as `STATE_WIDTH` f64s, roster order, NaN
    /// for a name without one. For the snapshot.
    pub fn dividend_states(&self) -> Vec<f64> {
        let w = crate::market::dividends::STATE_WIDTH;
        let mut out = Vec::with_capacity(self.companies.len() * w);
        for c in &self.companies {
            match c.stock.dividend {
                Some(d) => out.extend_from_slice(&d.to_array()),
                None => out.extend(std::iter::repeat_n(f64::NAN, w)),
            }
        }
        out
    }

    /// Write the dividend states back from [`Engine::dividend_states`].
    pub fn set_dividend_states(&mut self, values: &[f64]) -> Result<(), String> {
        let w = crate::market::dividends::STATE_WIDTH;
        if values.len() != self.companies.len() * w {
            return Err(format!(
                "dividend has {} values, expected {} ({} per company)",
                values.len(), self.companies.len() * w, w));
        }
        for (c, chunk) in self.companies.iter_mut().zip(values.chunks_exact(w)) {
            c.stock.dividend = crate::market::dividends::DividendState::from_slice(chunk);
        }
        Ok(())
    }

    /// The amount per share each name paid at this session's open, roster
    /// order; 0.0 where nothing went ex, and on every preset.
    pub fn dividends_today(&self) -> Vec<f64> {
        self.companies
            .iter()
            .map(|c| c.stock.dividend.map(|d| d.paid_today).unwrap_or(0.0))
            .collect()
    }

    /// Every dividend declared so far, in declaration order.
    pub fn distributions(&self) -> &[Distribution] {
        &self.distribution_log
    }

    /// THE DIVIDENDS (`dividend_payout_share`), once per name at the open,
    /// after the overnight move and before the day's marks are set.
    ///
    /// Per name: the EMA of closes takes the last close; on the name's
    /// declaration session (21 before its ex-date) the next amount is
    /// declared; on its ex-date the price drops by the amount exactly, `s`
    /// is re-read against the fair value without the accrual (the small
    /// change is the `dividend` slot of the attribution, so the slots still
    /// sum to the change in `s`), a resting agent buy limit on the name is
    /// lowered by the amount (FINRA Rule 5330), and the amount is recorded
    /// as paid today; then the accrual fair value reads today is set. See
    /// [`crate::market::dividends`].
    ///
    /// No draw. With the dial at 0.0 it returns before touching anything,
    /// so every preset is bit-identical.
    fn apply_dividends(&mut self) {
        use crate::market::dividends as dv;
        if self.params.dividend_payout_share == 0.0 {
            return;
        }
        let n = self.companies.len();
        self.dividend_moves.clear();
        self.dividend_moves.resize(n, 0.0);
        for c in self.companies.iter_mut() {
            if let Some(d) = c.stock.dividend.as_mut() {
                d.paid_today = 0.0;
            }
        }
        let day = self.elapsed_days;
        let alpha = dv::ema_weight();
        for i in 0..n {
            let (ticker, price, fresh) = {
                let c = &self.companies[i];
                if c.is_bankrupt || !c.is_public {
                    continue;
                }
                (c.ticker.clone(), c.stock.price, c.stock.dividend.is_none())
            };
            let k = dv::sessions_since_ex(&ticker, day);
            if fresh {
                // A new state takes today's price as its reference and pays
                // nothing today; the accrual starts where the quarter is.
                let mut st = dv::new_state(&self.params, &self.companies[i], price);
                if k >= dv::DIVIDEND_PERIOD - dv::DECLARATION_LEAD {
                    st.declared = true;
                }
                if st.declared && st.target_yield > 0.0 {
                    self.distribution_log.push(Distribution {
                        company: i,
                        ticker: ticker.clone(),
                        declared_day: day,
                        ex_day: day + dv::DIVIDEND_PERIOD - k,
                        amount: st.amount,
                    });
                }
                st.accrual = dv::accrual_for(&self.params, &st, price, k);
                self.companies[i].stock.dividend = Some(st);
                // A name that has traded keeps its price: `s` is re-read
                // against the fair value with the accrual in it.
                if self.companies[i].stock.mispricing_s.is_some() && st.accrual != 0.0 {
                    let fv = crate::market::tick::tick_fair_value(
                        &self.params, &self.economy, self.nominal_output_base, day,
                        &self.companies[i], price);
                    if fv > 0.0 && price > 0.0 {
                        let s_new = crate::market::tick::clamp_s(
                            &self.params, crate::mathx::log(price / fv));
                        let stock = &mut self.companies[i].stock;
                        let before = stock.mispricing_s.unwrap_or(s_new);
                        stock.mispricing_s = Some(s_new);
                        if let Some(prev) = stock.mispricing_s_prev_close {
                            stock.mispricing_s_prev_close = Some(prev + (s_new - before));
                        }
                    }
                }
                continue;
            }
            let mut st = self.companies[i].stock.dividend.unwrap();
            st.price_ema += alpha * (price - st.price_ema);
            let mut paid = 0.0;
            if k == 0 {
                if !st.declared {
                    st.amount = dv::declare(&self.params, &st, price);
                    if st.target_yield > 0.0 {
                        self.distribution_log.push(Distribution {
                            company: i,
                            ticker: ticker.clone(),
                            declared_day: day,
                            ex_day: day,
                            amount: st.amount,
                        });
                    }
                }
                paid = st.amount;
                st.declared = false;
            } else if k == dv::DIVIDEND_PERIOD - dv::DECLARATION_LEAD {
                st.amount = dv::declare(&self.params, &st, price);
                st.declared = true;
                if st.target_yield > 0.0 {
                    self.distribution_log.push(Distribution {
                        company: i,
                        ticker: ticker.clone(),
                        declared_day: day,
                        ex_day: day + dv::DECLARATION_LEAD,
                        amount: st.amount,
                    });
                }
            }
            st.accrual = dv::accrual_for(&self.params, &st, price, k);
            st.paid_today = paid;
            self.companies[i].stock.dividend = Some(st);
            if paid > 0.0 {
                self.go_ex(i, paid);
            }
        }
    }

    /// The ex-date open for one name: the price drops by `amount`, `s` is
    /// re-read against today's fair value (whose accrual has just returned
    /// to zero), and resting agent buy limits drop by the amount.
    fn go_ex(&mut self, i: usize, amount: f64) {
        let day = self.elapsed_days;
        let old = self.companies[i].stock.price;
        let new = crate::mathx::max(0.01, old - amount);
        {
            let stock = &mut self.companies[i].stock;
            stock.price = new;
            stock.market_cap = new * stock.shares_outstanding;
        }
        if let Some(v) = self.repriced_pending.get_mut(i) {
            *v += crate::mathx::log(new / old);
        }
        if self.companies[i].stock.mispricing_s.is_some() {
            let fv = crate::market::tick::tick_fair_value(
                &self.params, &self.economy, self.nominal_output_base, day,
                &self.companies[i], new);
            if fv > 0.0 {
                let s_new = crate::market::tick::clamp_s(&self.params, crate::mathx::log(new / fv));
                let stock = &mut self.companies[i].stock;
                let before = stock.mispricing_s.unwrap_or(s_new);
                let moved = s_new - before;
                stock.mispricing_s = Some(s_new);
                if let Some(prev) = stock.mispricing_s_prev_close {
                    stock.mispricing_s_prev_close = Some(prev + moved);
                }
                if let Some(acc) = self.attribution.get_mut(i) {
                    acc[crate::market::factors::DIVIDEND_SLOT] += moved;
                }
                self.dividend_moves[i] = moved;
            }
        }
        let ticker = self.companies[i].ticker.clone();
        for o in self.book.orders.iter_mut() {
            if o.ticker == ticker && o.side == crate::order_book::Side::Buy {
                o.limit = crate::mathx::max(0.01, o.limit - amount);
            }
        }
    }

    /// What each name's `s` gave up to its fair value at the last close's
    /// jump, in roster order; 0.0 where nothing moved.
    pub fn jump_fair_value_moves(&self) -> &[f64] {
        &self.jump_fair_value_moves
    }

    /// Each name's last print at the most recent close, in roster order:
    /// the price as [`Engine::close_market`] (or [`Engine::close_day`])
    /// found it, before the close re-marks anything. Before the first
    /// close, the price the name was built or listed at.
    ///
    /// # Why this is not `previous_close`
    ///
    /// The `previous_close` field on each stock is reset at
    /// [`Engine::open_market`] to the day's OPENING price, after the
    /// overnight gap and after the close's re-mark to newly published macro
    /// data (`macro_publication_repricing`, on from `pt-v20`). It anchors
    /// the session's circuit-breaker band and the daily return GARCH reads,
    /// so it measures open to now, and the move between the last print and
    /// the open sits outside it by design. For a close-to-close day change,
    /// divide by [`Engine::prior_closes`] instead.
    ///
    /// Not part of the trajectory: nothing that prices reads it, it takes
    /// no draw, and [`Engine::state_hash`] leaves it out. A host that saves
    /// and restores an engine between days carries it with
    /// [`Engine::restore_closes`] if it wants the next day's change to be
    /// close-to-close.
    pub fn last_closes(&self) -> &[f64] {
        &self.last_closes
    }

    /// The close each name's current day is measured from, in roster
    /// order: [`Engine::last_closes`] as it stood at the most recent
    /// [`Engine::open_market`]. So `price / prior_close - 1` is the
    /// close-to-close change of the day in progress, and it stays that
    /// day's change after the close until the next open.
    ///
    /// On `pt-v20`, over 119 days of a 20-name market, the open differed
    /// from the previous day's last print on every name and day, by a
    /// median of 0.12 per cent and at most 0.55, so a day change taken
    /// against `previous_close` is off by that much.
    pub fn prior_closes(&self) -> &[f64] {
        &self.prior_closes
    }

    /// Restore [`Engine::last_closes`] and [`Engine::prior_closes`], one
    /// value per name each, as a host's own save carried them. Lengths are
    /// checked rather than truncated. Changes no price and no draw.
    pub fn restore_closes(&mut self, last: &[f64], prior: &[f64]) -> Result<(), String> {
        let n = self.companies.len();
        for (name, len) in [("last", last.len()), ("prior", prior.len())] {
            if len != n {
                return Err(format!("{name} closes have {len} values, expected {n}"));
            }
        }
        self.last_closes = last.to_vec();
        self.prior_closes = prior.to_vec();
        Ok(())
    }

    /// Close-of-day bookkeeping. Zero draws.
    ///
    /// Must run BEFORE any earnings shock the embedder applies that evening:
    /// the momentum roll reads `s` as it stands at the close, and an earnings
    /// gap applied first would be counted again as next-day herding. The
    /// the reference implementation's earnings path patches `sPrevClose` by the shock for the
    /// same reason.
    pub fn close_market(&mut self, request: &DayCloseRequest) {
        // The session is over: the rate indices mark to the committed level
        // until the close's re-mark and the next open, and the live VIX is
        // the published one.
        self.rate_live = None;
        self.vix_live = None;
        assert_eq!(
            request.daily_innovations.len(),
            self.companies.len(),
            "one innovation per company"
        );
        assert_eq!(
            request.sector_base_variances.len(),
            self.companies.len(),
            "one sector base variance per company"
        );
        // The session's last print, before anything below or the macro step
        // after it can re-mark a price. Read by nothing that prices.
        self.last_closes.clear();
        self.last_closes.extend(self.companies.iter().map(|c| c.stock.price));
        // The index on the same prints (`index_level_listed`). Nothing with
        // the switch off.
        self.index_mark_close();
        // The index futures' settlement marks on that index
        // (`futures_index_listed`). Nothing with the switch off.
        self.futures_close_marks();
        // WHAT THE FACTOR'S VARIANCE TARGET MEASURES THE VIX AGAINST, and
        // it has to be read HERE, before the per-name GARCH loop below
        // moves a single name's variance.
        //
        // At `market_vol_vix_excursion` 0.0 this is `self.vix_anchor`, the
        // expression that stood at the call site, so the argument is the
        // same f64 and every preset through pt-v18 is bit-identical with
        // no arithmetic executed on this branch at all.
        //
        // At nonzero it is the level the identity says today's VIX ought to
        // be: the read-back of the index's conditional variance AS THE
        // SESSION TRADED IT. Every input is engine state that
        // `state_snapshot` carries — the names' GARCH variances before the
        // loop, `market_vol.variance()` before its own close, and
        // `economy.vix` before the macro chain advances — so a restored
        // engine recomputes the same number rather than inheriting one.
        // That is why it is recomputed and not taken from
        // `last_index_variance`, which is deliberately out of the hash.
        //
        // `prev_day_down()` here is the bit TODAY's ticks read, because
        // `close_day_at` has not rolled `day_factor` yet. The economy
        // update's own call reads it after that roll and gets tomorrow's,
        // which is what a one-day-ahead variance needs and this is not:
        // this prices the session that just happened.
        //
        // See `ModelParams::market_vol_vix_excursion` for why the ratio's
        // denominator is the whole mechanism.
        let vix_ratio_denominator = if self.params.market_vol_vix_excursion == 0.0 {
            self.vix_anchor
        } else {
            let implied = crate::market::index_var::vix_from_variance(
                self.params.vix_variance_premium,
                self.index_conditional_variance_terms_now().total(),
            );
            // A read-back of zero or worse is not reachable — the factor
            // variance is floored at `market_vol_floor_multiple` times base
            // and the sum is of non-negative terms — but a denominator is
            // the one place where "not reachable" is worth a line rather
            // than a panic in somebody's overnight run. Falling back to the
            // anchor makes such a day read as the old form did.
            if implied > 0.0 { implied } else { self.vix_anchor }
        };
        // The GJR variance each name's draws were scaled by today, before
        // `close_day` steps it: the idiosyncratic state's expected
        // variance reads it after the jumps. Empty while the state is off.
        let idio_h_day: Vec<f64> = if self.idio_state_on() {
            self.companies.iter().map(|c| c.stock.garch_variance).collect()
        } else {
            Vec::new()
        };
        for (i, company) in self.companies.iter_mut().enumerate() {
            close_day_with(
                &self.params,
                company,
                &CloseInputs {
                    daily_innovation: request.daily_innovations[i],
                    sector_base_daily_variance: request.sector_base_variances[i],
                    vix: self.economy.vix,
                    // The PER-NAME channel is `garch_vix_coupling`, one of
                    // the instantaneous couplings, and it keeps the anchor.
                    // §3.4's option C names the variance arm of the loop and
                    // only the variance arm.
                    vix_anchor: self.vix_anchor,
                    avg_volume: request.avg_volume,
                },
            );
        }
        // The jumps, HERE and not after the sector close, where they stood
        // until `jump_market_variance_share` was wired. They have to run
        // after the per-name closes, because the momentum roll in those
        // closes sets the reference that `jump_momentum_share` then moves;
        // and they have to
        // run before the factor's close below, because that close consumes
        // `day_factor` and zeros it, so a jump added afterwards would reach
        // no shock at all -- `open_market` clears the accumulator again
        // before the next session's ticks.
        //
        // The move costs no preset a bit. Nothing between here and where
        // the call stood reads or writes `mispricing_s`, the attribution
        // accumulator or the jump excitation, and `apply_jumps` reads only
        // the params, the VIX and the anchor, none of which the factor,
        // sector, forced-flow or stress updates touch. The known-answer
        // digest is the proof rather than this paragraph.
        self.apply_jumps();
        self.close_idio_state(&idio_h_day);
        // The market factor's own close: its variance updates from the
        // day's accumulated factor, beside the per-name GARCH updates
        // above and with the same zero-draw discipline. The VIX read here
        // is the day's TRADING value — the macro chain has not advanced
        // yet, exactly as the per-name updates see the day they closed.
        // THE SLOW STOCHASTIC VARIANCE LEVEL, one normal per session.
        //
        // Drawn UNCONDITIONALLY, whatever the dials read, and on a stream of
        // its own: the schedule cannot depend on a settable, and the zero
        // arm of this mechanism is therefore the SAME RANDOM WORLD as the
        // live arm rather than a reshuffled one. See
        // `rng::stream::MARKET_VOL_LEVEL` for why that distinction is what
        // makes the comparison a measurement.
        //
        // At `market_vol_level_sigma` 0.0 -- every preset through pt-v18,
        // where the dial is zero -- the draw is taken,
        // `market_vol_log_level` stays exactly 0.0, the multiplier is
        // exactly 1.0 and `close_day_scaled` calls the very function the
        // close called before this existed. A preset with a positive sigma
        // takes the other branch, the level live and the multiplier not
        // 1.0; pt-v19 did from 2026-09-14 until the 2026-09-20 recomposition
        // returned the level to 0.0, and no shipped preset does now. This line said
        // "every preset through pt-v19" until 2026-09-18, and stopped
        // being true when pt-v19 took the dial off zero.
        self.market_vol_level_rng.site(Site::MarketVolLevelZ, 0);
        let level_z = self.market_vol_level_rng.next_normal();
        // THE VIX'S OWN SLOW LEVEL, on the same draw. A branch at sigma 0.0,
        // which is every shipped preset: no state moves and the multiplier
        // the close hands the VIX is exactly 1.0. Started from its stationary
        // distribution for the factor level's reason, with the same 0.0
        // sentinel; normalised to a mean of one on the LEVEL, since it
        // multiplies the VIX itself and not a variance whose root is read.
        if self.params.vix_level_sigma != 0.0 {
            let phi = self.params.vix_level_persistence;
            // The dial IS the switch above, and what the recursion drives
            // is the dispersion after the loop's own transmission has been
            // divided out. At `vix_level_loop_gain` 0.0 that is the dial's
            // own f64 and this line reads as it did.
            let sigma = self.vix_level_sigma_applied();
            let one_minus = 1.0 - phi * phi;
            let stationary_var = if one_minus > 0.0 { sigma * sigma / one_minus } else { 0.0 };
            self.vix_log_level = if self.vix_log_level == 0.0 {
                crate::mathx::sqrt(stationary_var) * level_z
            } else {
                phi * self.vix_log_level + sigma * level_z
            };
        }
        let market_vol_level = if self.params.market_vol_level_sigma == 0.0 {
            1.0
        } else {
            let phi = self.params.market_vol_level_persistence;
            let sigma = self.params.market_vol_level_sigma;
            // A persistence at or past one has no stationary dispersion, so
            // there is nothing to normalise against and nothing to start
            // from: such a level is a random walk and its own
            // non-stationarity is the thing to notice, not a NaN three
            // thousand sessions later.
            let one_minus = 1.0 - phi * phi;
            let stationary_var = if one_minus > 0.0 { sigma * sigma / one_minus } else { 0.0 };
            // STARTED FROM THE STATIONARY DISTRIBUTION, not from its centre.
            //
            // At the derived half-life of 295 sessions an AR(1) started at
            // zero has covered 42 per cent of its variance by day 252 and
            // the whole of the normalisation has been applied from day one,
            // so every recording would sit about five per cent low in
            // volatility and the box would measure a transient rather than
            // the process. Measured before this line existed: the 120-day
            // sample standard deviation read 0.9605 against a control's
            // 1.0072, which is `exp(-sd^2/4 / 2)` to three figures.
            //
            // The tape's windows are draws from a market that has been
            // running forever. This one has to be too, and one draw at the
            // stationary scale is the whole of the burn-in.
            //
            // The sentinel for "not yet started" is a log level of EXACTLY
            // 0.0, which is also a fresh engine's value and a pre-level
            // snapshot's. It needs no flag beside it and cannot go wrong if
            // it is ever hit by a genuine draw: re-starting a stationary
            // AR(1) from its own stationary distribution leaves it
            // stationary, so the sentinel firing spuriously costs one
            // session's autocorrelation and nothing else.
            self.market_vol_log_level = if self.market_vol_log_level == 0.0 {
                let opening = crate::mathx::sqrt(stationary_var) * level_z;
                // THE MARKET-SIDE WARM-UP, and this is the only place it
                // can go: the level it warms the variance to is drawn on
                // the line above.
                //
                // `macro_burn_in_days` settles the ECONOMY by running
                // `advance_day`, and `close_market` -- this function -- is
                // never called during it, so the two variance components
                // `close_day_scaled` is about to step have never run.
                // They open at the unscaled baseline while the level has
                // already opened at a stationary draw, and they spend the
                // first hundred sessions travelling to it. That travel is
                // what a measurement of the level sigma by horizon found to
                // be the whole of
                // the 252/504 calibration gap.
                //
                // A BRANCH at 0.0 sessions, which is every preset through
                // pt-v19: nothing runs, no state moves and no draw is
                // taken -- here or anywhere, at any setting. See
                // `ModelParams::market_burn_in_sessions` for why a
                // conditional MEAN of the close's own recursion is enough
                // and what it leaves behind, and `warm_to_level` for the
                // recursion.
                //
                // `vix_ratio_denominator` above was computed from the COLD
                // variance, because it is read before the per-name loop and
                // the level is not drawn until here. One session's
                // denominator, on the one session whose ticks already
                // traded at the cold sigma; the VIX's own three-session
                // relaxation closes it long before anything is recorded.
                //
                // `stationary_var > 0.0` is not belt and braces. At a
                // persistence at or past one there is no stationary
                // dispersion, the opening is exactly 0.0, the sentinel
                // above never clears and the level restarts every session
                // -- so a warm-up gated on the sentinel alone would run
                // 504 iterations a day forever on a model that is a random
                // walk and says so. There is also nothing to warm to.
                if self.params.market_burn_in_sessions > 0.0 && stationary_var > 0.0 {
                    self.market_vol.warm_to_level(
                        &self.params,
                        vix_ratio_denominator,
                        self.economy.vix,
                        opening,
                        stationary_var,
                        self.params.market_burn_in_sessions as i64,
                    );
                }
                opening
            } else {
                phi * self.market_vol_log_level + sigma * level_z
            };
            // NORMALISED ON THE SQUARE ROOT, not on the level. With
            // `E[L] = 1` the median annualised volatility falls by
            // `1 - exp(-sd^2/8)`, six per cent at the derived dispersion,
            // and `annualised_vol_pct` is a graded row -- the mechanism
            // would pay for its tail with a level nobody asked it to move.
            // `E[sqrt(L)] = 1` instead, which subtracts `sd^2/4` from the
            // log. A constant of the construction and not a free dial; see
            // `ModelParams::market_vol_level_sigma`.
            //
            crate::mathx::exp(self.market_vol_log_level - 0.25 * stationary_var)
        };
        // A FORCED close sets the variance to the level the law implies at
        // the VIX a scenario forced, instead of stepping toward it; see
        // `MarketVarianceState::close_day_forced`. The mark is consumed
        // here, so the next session closes free unless it is forced again.
        // False on every session nothing forced, which takes the branch that
        // stood here, unchanged.
        //
        // THE CYCLE'S VOLATILITY MULTIPLIER (`market_vol_cycle_ratio`): a
        // branch at 0.0, every preset through pt-v20, that reads and moves
        // nothing (pt-v21 sets it).
        // Off it, the multiplier steps toward the true phase's value every
        // close and scales the level the variance's baseline is taken at
        // and the VIX coupling's denominator -- except on a FORCED close,
        // which moves it but does not apply it: the VIX a scenario pinned
        // already carries the phase, and the forced level is the one that
        // VIX implies.
        let (market_vol_level, vix_ratio_denominator) = if self.params.market_vol_cycle_ratio == 0.0 {
            (market_vol_level, vix_ratio_denominator)
        } else {
            let l = self.step_market_vol_cycle();
            if self.vix_sets_variance_pending {
                (market_vol_level, vix_ratio_denominator)
            } else {
                // As the session applied it: one on a session a caller's
                // pins cover (`market_vol_cycle_pin_neutral`,
                // `market_vol_cycle_pin_phase`), `l` itself otherwise.
                let m = crate::mathx::exp(self.market_vol_cycle_applied_log(l));
                (market_vol_level * m * m, vix_ratio_denominator * self.market_vol_cycle_vix_scale())
            }
        };
        self.last_market_targets = Some(if self.vix_sets_variance_pending {
            self.vix_sets_variance_pending = false;
            let denominator =
                self.forced_vix_denominator(vix_ratio_denominator, market_vol_level);
            self.market_vol.close_day_forced(
                &self.params,
                denominator,
                self.economy.vix,
                market_vol_level,
            )
        } else {
            self.market_vol.close_day_scaled(
                &self.params,
                vix_ratio_denominator,
                self.economy.vix,
                market_vol_level,
            )
        });
        self.close_sector_state();
        // Tonight's market draw was part of the day now closed.
        self.night_market_factor = 0.0;
        // The forced-flow reservoir drains on stress days and rebuilds in
        // calm. Updated only while the mechanism is live: at gain 0 or
        // reservoir 0 the state stays exactly 0.0 and nothing changes.
        if self.params.forced_flow_gain != 0.0 && self.params.forced_flow_reservoir > 0.0 {
            let excess = crate::mathx::max(
                0.0,
                self.economy.vix - self.params.forced_flow_threshold,
            );
            if excess > 0.0 {
                self.forced_flow_spent += excess;
            } else if self.params.forced_flow_replenish != 0.0 {
                self.forced_flow_spent *= 1.0 - self.params.forced_flow_replenish;
            }
        }
        self.update_universe_stress();
        // The jumps ran above, before the factor's close.
        self.carry_jump_moves();
        self.update_volume_state();
        self.update_volume_idio();
        // The rate indices only note that a night has begun, so the next
        // open accrues its carry. They reprice to the economy's new yields at
        // that open, not here: the close's macro step runs after this, and
        // under `rate_close_remark` re-marks them when it ends
        // (`advance_macro_day`).
        if !self.rates.is_empty() {
            self.rates.close();
        }
        self.accrue_buybacks();
        self.pull_relative_levels();
        // The day's market t scale is the session's: the factor's close
        // above read the day it scaled, and the next open draws its own.
        self.market_day_scale = 1.0;
    }

    /// `fair_value_relative_knee`'s close step, after the buybacks. The
    /// reference is the equal-weighted mean fair-value level of the public,
    /// solvent names with a mispricing state; each such name whose level
    /// sits more than the knee below it is pulled toward the knee at
    /// `fair_value_relative_half_life`. A branch at 0.0, and nothing runs
    /// unless the model carries the levels (so the snapshot and the state
    /// hash see every change). No draws.
    fn pull_relative_levels(&mut self) {
        let knee = self.params.fair_value_relative_knee;
        if knee == 0.0 || !self.carries_fair_value_offsets() {
            return;
        }
        let pull = 1.0 - crate::mathx::pow(0.5, 1.0 / self.params.fair_value_relative_half_life);
        let live = |c: &TickCompany| {
            c.is_public && !c.is_bankrupt && c.stock.mispricing_s.is_some()
        };
        let (mut sum, mut n) = (0.0, 0usize);
        for c in self.companies.iter().filter(|c| live(c)) {
            sum += c.stock.fair_value_offset.unwrap_or(0.0);
            n += 1;
        }
        if n < 2 {
            return;
        }
        let floor = sum / n as f64 - knee;
        for c in self.companies.iter_mut().filter(|c| live(c)) {
            let v = c.stock.fair_value_offset.unwrap_or(0.0);
            if v < floor {
                c.stock.fair_value_offset = Some(v + pull * (floor - v));
            }
        }
    }

    /// `buyback_accrual`'s close step, after the rates close. Each public,
    /// solvent name with a mispricing state adds one session's buyback
    /// yield, read at the close price on the earnings the valuation holds
    /// (nominal scale and fair-value level included), to its log share-count
    /// reduction. See `market::tick::buyback_accrual_step`.
    fn accrue_buybacks(&mut self) {
        if !self.carries_buyback_log_shares() {
            return;
        }
        let p = &self.params;
        let nominal = crate::market::tick::nominal_scale(p, &self.economy, self.nominal_output_base);
        for c in self.companies.iter_mut() {
            if !c.is_public || c.is_bankrupt || c.stock.mispricing_s.is_none() {
                continue;
            }
            let Some(eps) = c.eps else { continue };
            let earnings = if nominal == 1.0 { eps } else { eps * nominal };
            let earnings = match c.stock.fair_value_offset {
                Some(v) if v != 0.0 => earnings * crate::mathx::exp(v),
                _ => earnings,
            };
            let l = c.stock.buyback_log_shares.unwrap_or(0.0);
            // The name's own share under `dividend_buyback_substitution`,
            // `buyback_payout_share` otherwise: the one function the
            // valuation's buyback term reads too.
            let share = crate::market::dividends::buyback_share(p, c);
            let dl = crate::market::tick::buyback_accrual_step(p, share, Some(earnings), c.stock.price, l);
            if dl != 0.0 {
                c.stock.buyback_log_shares = Some(l + dl);
            }
        }
    }

    /// Each name's accrued log share-count reduction under
    /// `buyback_accrual`, in roster order, 0.0 where nothing has accrued.
    /// For checkpoints and forks.
    pub fn buyback_log_shares(&self) -> Vec<f64> {
        self.companies.iter().map(|c| c.stock.buyback_log_shares.unwrap_or(0.0)).collect()
    }

    /// Put the accrued log share-count reductions back. A width mismatch is
    /// refused, as the fair-value levels' is: they are positional against
    /// the roster.
    pub fn set_buyback_log_shares(&mut self, values: &[f64]) -> Result<(), String> {
        if values.len() != self.companies.len() {
            return Err(format!(
                "this snapshot carries {} buyback share-count reductions and the \
                 roster holds {} companies. They are positional against the roster, \
                 so this restore is refused rather than padded or truncated.",
                values.len(),
                self.companies.len()
            ));
        }
        for (c, &v) in self.companies.iter_mut().zip(values) {
            c.stock.buyback_log_shares = if v == 0.0 { None } else { Some(v) };
        }
        Ok(())
    }

    /// Whether this engine's model accrues the buyback share count, which is
    /// when the snapshot and the state hash carry it: `buyback_accrual` and
    /// `buyback_payout_share` both set. Off on every preset as shipped.
    pub fn carries_buyback_log_shares(&self) -> bool {
        self.params.buyback_accrual != 0.0 && self.params.buyback_payout_share != 0.0
    }

    /// The stationary opening (`opening_mispricing_sigma`), applied once,
    /// before the first tick that prices anything.
    ///
    /// Each name's day-zero premium of price over its published fair value
    /// is split in two. The mispricing `s` is a common level plus this
    /// name's draw at `opening_mispricing_sigma`, the draws re-centred to
    /// cap-weighted zero. The common level is a draw at
    /// `opening_market_sigma` when that is non-zero, the market opening at
    /// a point of its own stationary mispricing; at 0.0 it is the roster's
    /// cap-weighted premium, so the index opens with the mispricing the
    /// generated roster gives it (and reverts from it over the first months,
    /// which is a start-up drift of up to 30 per cent on a 20-name roster). The rest of the premium is the name's opening
    /// fair-value level `v`. The price does not move: `P = FV exp(v) exp(s)`
    /// holds with the same `P`. What changes is how much of the premium the
    /// model later pulls back: only the stationary part.
    ///
    /// A name listed after the engine was built has no draw and takes the
    /// lazy opening in `market::tick`, which adopts its whole premium as `s`.
    fn apply_opening(&mut self) {
        let z = std::mem::take(&mut self.opening_z);
        let sigma = self.params.opening_mispricing_sigma;
        // The per-name draws are the first `n`, the market's the last.
        let n_draws = z.len().saturating_sub(1);
        let mut gap = vec![f64::NAN; self.companies.len()];
        let (mut wsum, mut wgap, mut wz) = (0.0, 0.0, 0.0);
        // The prehistory's carried mispricing (`market_prehistory_valuation`),
        // empty unless that dial is on: a carried name opens at the copy's
        // `s` and stays out of the draws' centring.
        let carry = std::mem::take(&mut self.opening_carry);
        let mut carried = vec![false; self.companies.len()];
        for (i, c) in self.companies.iter().enumerate() {
            if i >= n_draws || c.stock.mispricing_s.is_some() || c.is_bankrupt || !c.is_public {
                continue;
            }
            let is_carried = carry.get(i).is_some_and(|v| v.is_finite());
            let fv = crate::market::tick::published_fair_value(
                &self.params, &self.economy, self.nominal_output_base, self.elapsed_days, c);
            let g = crate::mathx::log(crate::mathx::max(0.01, c.stock.price) / fv);
            gap[i] = g;
            if is_carried {
                carried[i] = true;
                continue;
            }
            let w = c.stock.price * c.stock.shares_outstanding;
            wsum += w;
            wgap += w * g;
            wz += w * z[i];
        }
        if !(wsum > 0.0) && !carried.iter().any(|&b| b) {
            return;
        }
        let centre = if self.params.opening_market_sigma != 0.0 {
            self.params.opening_market_sigma * z[n_draws]
        } else {
            wgap / wsum
        };
        let zbar = wz / wsum;
        for (i, c) in self.companies.iter_mut().enumerate() {
            if gap[i].is_nan() {
                continue;
            }
            let s0 = if carried[i] {
                crate::market::tick::clamp_s(&self.params, carry[i])
            } else {
                crate::market::tick::clamp_s(&self.params, centre + sigma * (z[i] - zbar))
            };
            c.stock.mispricing_s = Some(s0);
            c.stock.mispricing_s_prev_close = Some(s0);
            c.stock.mispricing_momentum = Some(0.0);
            // With a dividend accrued, fair value is `base * exp(v) + A`
            // rather than `(base + A) * exp(v)`, so the level that opens the
            // name at its own price solves `(base * exp(v) + A) * exp(s0) =
            // P`. A branch: without an accrual, the line that stood.
            let a = crate::market::dividends::accrual(c);
            let v = if a == 0.0 {
                gap[i] - s0
            } else {
                let price = crate::mathx::max(0.01, c.stock.price);
                let base = price / crate::mathx::exp(gap[i]) - a;
                let rest = price / crate::mathx::exp(s0) - a;
                if base > 0.0 && rest > 0.0 {
                    crate::mathx::log(rest / base)
                } else {
                    gap[i] - s0
                }
            };
            c.stock.fair_value_offset = Some(v);
        }
    }

    /// Endogenous jumps, applied once per name at the day close.
    ///
    /// The model has no discontinuities without this. Prices diffuse; real
    /// markets gap, and nothing here ever surprised the market unless a
    /// caller injected news by hand. That is why excess kurtosis reads 5.2
    /// over 504-day windows against real markets' 7.1 to 22 -- fat tails at
    /// that scale are not reachable from a diffusion plus GARCH at any
    /// coefficients, so this is a mechanism gap and not a calibration one.
    ///
    /// # Where the jump lands, and why not on the price
    ///
    /// A jump moves `mispricing_s`, the same channel news already uses,
    /// rather than the price directly. That makes it a gap AWAY from fair
    /// value which then mean-reverts on the existing process -- which is
    /// what a news or panic jump does -- and it reuses a tested path
    /// instead of opening a second way for something to move a price.
    /// The existing cap applies, so a jump cannot dislocate a name further
    /// than the model's own guard allows.
    ///
    /// # Draw discipline
    ///
    /// Two draws for the market, then two per company, ALWAYS -- never
    /// conditionally. A schedule that depended on whether a jump fired
    /// would make the stream position a function of the parameters, and
    /// every preset would stop being comparable under common random
    /// numbers. The uniform decides occurrence, the normal decides size,
    /// and at zero intensity `u < 0.0` is false for every `u` in [0, 1),
    /// so nothing fires.
    ///
    /// These draws come from [`stream::JUMPS`], which no earlier preset
    /// touched. That is what lets a draw-CONSUMING mechanism ship inert:
    /// the market, economy and external streams are untouched, so every
    /// shipped preset reproduces bit for bit.
    ///
    /// `rustfmt::skip` holds the generated body exactly as the emitter
    /// wrote it. A format run would re-wrap the one-line if-else
    /// expressions inside the markers, and the generated body is compared
    /// to the committed text byte for byte, so a routine format run would
    /// leave that comparison failing until somebody regenerated the body.
    #[rustfmt::skip]
    #[allow(clippy::if_same_then_else, reason = "the generated idiosyncratic rate spells out each branch of the spec, two of which give the same value")]
    fn apply_jumps(&mut self) {
        self.jump_fair_value_moves.clear();
        self.jump_fair_value_moves.resize(self.companies.len(), 0.0);
        // The permanent share's reading of the jump (`fair_value_news_share`):
        // each name's `s` before the generated body, so the company's own
        // jump can be told from the market's after it. Empty, and nothing
        // below runs, at 0.0.
        //
        // The idiosyncratic variance state reads the same split for its own
        // shock, so it takes `s_before` too, and the excitation each name's
        // rate is drawn at (the generated body below steps it forward).
        let idio_on = self.idio_state_on();
        let excitation_drawn: Vec<f64> = if idio_on && self.params.jump_idio_excitation != 0.0 {
            self.jump_excitation.clone()
        } else {
            Vec::new()
        };
        let s_before: Vec<f64> = if self.params.fair_value_news_share != 0.0
            || self.params.fair_value_market_share != 0.0
            || idio_on
        {
            self.companies.iter().map(|c| c.stock.mispricing_s.unwrap_or(f64::NAN)).collect()
        } else {
            Vec::new()
        };
        // mechanism:jumps begin -- generated by tools/mechanism/emit.py from
        // tools/mechanism/mechanisms/jumps.py (spec 0ff870280c51); do not edit by hand.
        // Draws: 1 uniform, 1 normal, 1 uniform per company, 1 normal per company on the jumps stream, unconditionally.
        let p = &self.params;
        let ratio = self.economy.vix / self.vix_anchor;
        let rate_scale = if p.jump_vix_coupling == 0.0 { 1.0 } else { (1.0 - p.jump_vix_coupling) + ((p.jump_vix_coupling * ratio) * ratio) };
        let intensity_market = if p.jump_vix_coupling == 0.0 { p.jump_intensity_market } else { p.jump_intensity_market * rate_scale };
        // The idiosyncratic rate: scaled by the VIX like the market's unless
        // `jump_idio_vix_decoupled` says the tape does not support that
        // (vix-dynamics.md 19.1), and excited by the name's own recent jumps
        // when `jump_idio_excitation` is set. Both are branches at 0.0.
        let intensity_idio = if p.jump_vix_coupling == 0.0 { p.jump_intensity_idio } else if p.jump_idio_vix_decoupled != 0.0 { p.jump_intensity_idio } else { p.jump_intensity_idio * rate_scale };
        let excite = p.jump_idio_excitation;
        let excite_decay = p.jump_idio_excitation_decay;
        let mut excitation = if excite != 0.0 {
            let mut e = std::mem::take(&mut self.jump_excitation);
            if e.len() < self.companies.len() { e.resize(self.companies.len(), 0.0); }
            e
        } else {
            Vec::new()
        };
        self.jump_rng.site(Site::JumpMarketU, 0);
        let u_market = self.jump_rng.next_f64();
        self.jump_rng.site(Site::JumpMarketZ, 0);
        let z_market = self.jump_rng.next_normal();
        let market = if u_market < intensity_market { p.jump_mean_market + (p.jump_sigma_market * z_market) } else { 0.0 };
        let compensator = if p.jump_mean_compensated == 0.0 { 0.0 } else { p.jump_mean_compensated * (intensity_market * p.jump_mean_market) };
        for (index, company) in self.companies.iter_mut().enumerate() {
            self.jump_rng.site(Site::JumpCompanyU, index as u32);
            let u = self.jump_rng.next_f64();
            self.jump_rng.site(Site::JumpCompanyZ, index as u32);
            let z = self.jump_rng.next_normal();
            let rate = if excite != 0.0 { intensity_idio * (1.0 + excitation[index]) } else { intensity_idio };
            let jumped = u < rate;
            let idio = if jumped { p.jump_sigma_idio * z } else { 0.0 };
            if excite != 0.0 {
                excitation[index] = (excite_decay * excitation[index]) + (if jumped { excite } else { 0.0 });
            }
            let total = (market + idio) - compensator;
            if total != 0.0 {
                if let Some(s) = company.stock.mispricing_s {
                    let after = crate::market::tick::clamp_s(&self.params, s + total);
                    if let Some(acc) = self.attribution.get_mut(index) {
                        acc[8] += after - s;
                    }
                    company.stock.mispricing_s = Some(after);
                    let carried = (1.0 - p.jump_momentum_share) * (after - s);
                    if carried != 0.0 {
                        if let Some(prev) = company.stock.mispricing_s_prev_close {
                            company.stock.mispricing_s_prev_close = Some(prev + carried);
                        }
                    }
                }
            }
        }
        if excite != 0.0 {
            self.jump_excitation = excitation;
        }
        // mechanism:jumps end
        //
        // THE JUMP JOINS THE DAY'S FACTOR INNOVATION. `market` is the log
        // return the jump put into every name's `s`, and `day_factor` is
        // the sum of the day's per-tick market factors that the factor's
        // GJR steps on. The tape's index fit was on TOTAL returns, so the
        // shock the coefficients were fitted to saw crash days; this is
        // where the model's shock sees one. See
        // `ModelParams::jump_market_variance_share` for the derivation and
        // for why this call sits before the factor's own close.
        //
        // Read outside the generated region and not inside it, because the
        // body between the markers is compared to what the emitter writes
        // and every shipped preset records its digest. `market` is a
        // top-level binding of that body, so it is still in scope here.
        //
        // Guarded on the share AND on the jump, so a day that does not jump
        // adds nothing at all rather than adding a zero to an accumulator
        // that may be holding a negative one.
        if self.params.jump_market_variance_share != 0.0 && market != 0.0 {
            self.market_vol
                .accumulate(self.params.jump_market_variance_share * market);
        }
        // THE COMPANY'S OWN JUMP, SHARED WITH FAIR VALUE. The body above put
        // `market + idio - compensator` into `s`; what is left after taking
        // the common part out is the company's own jump (exactly zero on a
        // name that did not jump, up to the rounding of `(s + x) - s`, which
        // the 1e-9 floor absorbs: a company jump is `jump_sigma_idio` times a
        // normal). `psi` of it moves to the fair-value level, as the tick
        // does with the name's own noise and news. The attribution slot
        // keeps the whole jump: it reports what moved the price.
        // THE OWN JUMP'S EXPECTED VARIANCE, for the idiosyncratic variance
        // state: the rate each name was drawn at tonight (the VIX scale and
        // the excitation before the body stepped it) times
        // `jump_sigma_idio^2`, in the generated body's own spelling.
        if idio_on {
            let p = &self.params;
            let ratio = self.economy.vix / self.vix_anchor;
            let rate_scale = if p.jump_vix_coupling == 0.0 { 1.0 } else { (1.0 - p.jump_vix_coupling) + ((p.jump_vix_coupling * ratio) * ratio) };
            let intensity_idio = if p.jump_vix_coupling == 0.0 { p.jump_intensity_idio } else if p.jump_idio_vix_decoupled != 0.0 { p.jump_intensity_idio } else { p.jump_intensity_idio * rate_scale };
            let var1 = p.jump_sigma_idio * p.jump_sigma_idio;
            for i in 0..self.companies.len() {
                let ex = excitation_drawn.get(i).copied().unwrap_or(0.0);
                let rate = crate::mathx::min(intensity_idio * (1.0 + ex), 1.0);
                if let Some(slot) = self.idio_jump_var_today.get_mut(i) {
                    *slot = rate * var1;
                }
            }
        }
        if !s_before.is_empty() {
            let psi = self.params.fair_value_news_share;
            let psim = crate::market::tick::market_permanent_share(
                &self.params, self.market_vol.sigma_daily(),
                self.market_vol_cycle_cap_scale());
            let common = market - compensator;
            for (index, company) in self.companies.iter_mut().enumerate() {
                let (Some(after), Some(&before)) = (company.stock.mispricing_s, s_before.get(index)) else {
                    continue;
                };
                if before.is_nan() {
                    continue;
                }
                let own = (after - before) - common;
                // The company's own jump on the stock-level share, the
                // market's on the market-wide share (a name the clamp held
                // back from the market jump keeps the share of what it took).
                let own = if own.abs() <= 1e-9 { 0.0 } else { own };
                // The own jump, which tomorrow's session realises and
                // tomorrow's close reads into the idiosyncratic state.
                if idio_on {
                    if let Some(slot) = self.idio_jump_today.get_mut(index) {
                        *slot = own;
                    }
                }
                let taken_common = (after - before) - own;
                let dv = psi * own + psim * taken_common;
                if dv == 0.0 {
                    continue;
                }
                let s_new = after - dv;
                company.stock.mispricing_s = Some(s_new);
                // What left `s` for `v`, booked where the tape can find it:
                // the attribution's `fair_value_shift` and, for the tape's
                // row where the jump is observed, `jump_fair_value_moves`.
                if let Some(acc) = self.attribution.get_mut(index) {
                    acc[crate::market::factors::FAIR_VALUE_SLOT] += s_new - after;
                }
                if let Some(slot) = self.jump_fair_value_moves.get_mut(index) {
                    *slot = s_new - after;
                }
                if let Some(prev) = company.stock.mispricing_s_prev_close {
                    let carried = (1.0 - self.params.jump_momentum_share) * dv;
                    company.stock.mispricing_s_prev_close = Some(prev - carried);
                }
                let v = company.stock.fair_value_offset.unwrap_or(0.0);
                company.stock.fair_value_offset = Some(v + dv - 0.5 * dv * dv);
            }
        }
    }

    /// Carry the day's jumps over the open, for the volume scale.
    ///
    /// `apply_jumps` books each name's clamped jump into the jump slot of
    /// the attribution accumulator and is the only writer of that slot, so
    /// after it has run the slot IS the day's jump for that name -- the
    /// clamped one, which is the move the price will actually take.
    /// `open_market` clears the accumulator, and the gap does not trade in
    /// until the session after the close that booked it, so the number has
    /// to be copied somewhere that survives the open.
    ///
    /// Gated on the dial, so at the shipped 1.0 the vector is never
    /// written, stays all zeros, and the close does no work for a
    /// mechanism nothing reads.
    fn carry_jump_moves(&mut self) {
        if self.params.volume_move_jump_share == 1.0 {
            return;
        }
        if self.jump_move.len() < self.companies.len() {
            self.jump_move.resize(self.companies.len(), 0.0);
        }
        for (index, slot) in self.jump_move.iter_mut().enumerate() {
            *slot = match self.attribution.get(index) {
                Some(acc) => acc[crate::market::factors::JUMP_SLOT],
                None => 0.0,
            };
        }
    }

    /// The shared persistent volume component, stepped once per day.
    ///
    /// Volume is otherwise a LEVEL with independent per-tick noise, so
    /// consecutive volumes are near-independent draws around a fixed mean
    /// and differencing them gives a change autocorrelation near -0.5 at any
    /// coefficients. That is the whole reason `volume_change_acf1` sits 13.7
    /// seed-sd outside its band and is excluded from the objective as
    /// structurally unreachable: the defect is an absent process, and no
    /// parameter reaches it.
    ///
    /// A log-scale AR(1), so a busy day is followed by a busy day. The
    /// draw is unconditional and lives on [`stream::VOLUME`], so the
    /// schedule never depends on the parameters and no earlier preset's
    /// sequence moves.
    ///
    /// COMMON component only: every name shares this multiplier. Real volume
    /// persistence is partly idiosyncratic, and that half is not modelled.
    fn update_volume_state(&mut self) {
        self.volume_rng.site(Site::VolumeZ, 0);
        let z = self.volume_rng.next_normal();
        let p = &self.params;
        // Guarded rather than computed through: at zero persistence and zero
        // innovation the state must stay exactly 0.0, so the multiplier stays
        // exactly 1.0 and the tick's volume arithmetic is untouched.
        if p.volume_persistence == 0.0 && p.volume_innovation_sigma == 0.0 {
            return;
        }
        self.volume_state =
            p.volume_persistence * self.volume_state + p.volume_innovation_sigma * z;
    }

    /// The per-NAME volume state, one draw per company per day.
    ///
    /// On its own stream and drawn UNCONDITIONALLY, the same discipline
    /// `apply_jumps` follows: the count must not depend on a parameter or
    /// the schedule stops being comparable across presets. At zero sigma
    /// every state stays exactly 0.0, so the multiplier stays exactly 1.0
    /// and the tick's volume arithmetic is untouched.
    ///
    /// # The count is the roster's width
    ///
    /// The loop walks `volume_idio`, which [`Engine::add_company`] and
    /// [`Engine::remove_company`] hold at the roster's width. So a run that
    /// lists or delists draws from this stream at the NEW width from that
    /// mutation onward, and its position on any later day differs from the
    /// position the same run reached before issue #148 was fixed on
    /// 2026-09-02, when the two mutations left the array at its
    /// construction width. Every other stream is untouched by the
    /// difference, and every shipped preset holds both coefficients at 0.0,
    /// so no shipped price path moves.
    fn update_volume_idio(&mut self) {
        let p = &self.params;
        let (rho, sigma) = (p.volume_idio_persistence, p.volume_idio_sigma);
        for i in 0..self.volume_idio.len() {
            self.volume_idio_rng.site(Site::VolumeIdioZ, i as u32);
            let z = self.volume_idio_rng.next_normal();
            if rho == 0.0 && sigma == 0.0 {
                continue;
            }
            self.volume_idio[i] = rho * self.volume_idio[i] + sigma * z;
        }
    }

    /// The daily macro step: economy, cycle roll, then the central bank.
    ///
    /// The order is the reference implementation's and is load-bearing — the rates and VIX
    /// the factor model reads on the first tick of a new day are already the
    /// day's NEW values, not yesterday's.
    pub fn advance_day(&mut self, request: &DayAdvanceRequest) -> DayAdvanceOutcome {
        // The ECONOMY stream, not the market's. The macro chain's draw count
        // genuinely depends on macro state — a cycle entering contraction
        // draws a shock the expansion never rolls — and before the split
        // that variability shifted every market draw after it. Now its
        // branches move only its own stream.
        let mut rng = std::mem::replace(&mut self.economy_rng, GameRng::new(0, MAIN_STREAM));
        let mut counting = Counting {
            inner: &mut rng,
            count: 0,
        };
        let mut outcome = self.advance_day_with(request, &mut counting);
        let consumed = counting.count;
        self.economy_rng = rng;
        self.draws.economy += consumed;
        outcome.draws_consumed = consumed;
        outcome
    }

    /// The daily macro step against an EXTERNAL draw source. See
    /// [`Engine::tick_with`].
    pub fn advance_day_with(
        &mut self,
        request: &DayAdvanceRequest,
        rng: &mut impl Rng,
    ) -> DayAdvanceOutcome {
        let phase_before = self.economy.cycle_phase;
        // THE MARKET'S NOWCAST, first: the report on the phase this session
        // ran in, so a turn at tonight's close is reported from tomorrow's.
        let nowcast_on = self.params.cycle_nowcast_accuracy != 0.0;
        let spread_blend = nowcast_on || self.params.corporate_spread_cycle != 0.0;
        let spread_before = if spread_blend { self.priced_spread_multiplier(phase_before) } else { 0.0 };
        if nowcast_on {
            self.update_cycle_nowcast();
        }
        let spread_after_report = if spread_blend { self.priced_spread_multiplier(phase_before) } else { 0.0 };
        // Today's pins and the corporate yield a pin wrote, for the end of
        // the step: a pinned corporate yield holds through tonight's close,
        // a meeting's re-anchoring included (`macro_pins_today`).
        let pins_today = self.macro_pins_today;
        let corporate_pinned_at = self.economy.corporate_bond_yield;
        // EVERY PINNED FIELD HOLDS THROUGH THE CLOSE (`macro_pins_hold`):
        // the fields as the pins left them, which nothing between a pin and
        // the close writes. `None`, copying nothing, with the dial off or no
        // pin today.
        let held = if self.params.macro_pins_hold != 0.0 && pins_today != 0 {
            Some(self.economy.clone())
        } else {
            None
        };
        let holds = |bit: u16| held.is_some() && pins_today & bit != 0;

        // THE FED PUT'S CLOCK (`fed_put_gain`): the close's log change of
        // total public market cap into the intermeeting return, which the
        // next meeting reads and restarts, and one session's decay of the
        // put's stock. Nothing runs with the dial at 0.0, and no draw at any
        // setting. The first close only records its base.
        if self.params.fed_put_gain != 0.0 {
            let mut mcap = 0.0;
            for c in self.companies.iter() {
                if c.is_public && !c.is_bankrupt {
                    mcap += c.stock.market_cap;
                }
            }
            if self.economy.fed_put_mcap_prev > 0.0 && mcap > 0.0 {
                self.economy.intermeeting_return +=
                    crate::mathx::log(mcap / self.economy.fed_put_mcap_prev);
            }
            self.economy.fed_put_mcap_prev = mcap;
            self.economy.fed_put *= crate::mathx::pow(0.5, 1.0 / self.params.fed_put_half_life);
        }

        // THE DRAWDOWN'S WINDOW (`fed_drawdown_hold`), before tonight's
        // meeting reads it. Nothing with the dial off, and no draw.
        self.book_drawdown_close();

        // THE PRICED PATH'S CLOCK (`treasury_path_pricing`): one session's
        // decay of the market's forecast, before tonight's curve reads it.
        // Nothing runs with the dial at 0.0, and no draw at any setting.
        if self.params.treasury_path_pricing != 0.0 {
            self.policy_path *= crate::mathx::pow(0.5, 1.0 / self.params.treasury_path_half_life);
        }

        // The DAY's cap-weighted return, in the same percent units as
        // `market_return_pct`. Read only when `vix_return_source` is
        // non-zero, so the shipped path is untouched. `previous_close` is
        // set at the OPEN (market/daily.rs), so this is open to close, and
        // `apply_jumps` has already run, so the jumps are in `price`.
        let market_day_return_pct = if self.params.vix_return_source == 0.0
            && self.params.vix_level_identity == 0.0
            && self.params.flight_to_quality_day == 0.0
            && self.params.corporate_spread_equity_gain == 0.0
            && self.params.cycle_equity_hazard == 0.0
        {
            0.0
        } else {
            let (mut acc, mut mcap) = (0.0, 0.0);
            for c in self.companies.iter() {
                if !c.is_public || c.is_bankrupt || c.stock.previous_close <= 0.0 {
                    continue;
                }
                let d = (c.stock.price - c.stock.previous_close) / c.stock.previous_close * 100.0;
                acc += d * c.stock.market_cap;
                mcap += c.stock.market_cap;
            }
            if mcap > 0.0 { acc / mcap } else { 0.0 }
        };

        // ONCE, and only under the identity: the read-back and the fear
        // excursion's zero-mean correction are two readings of the same
        // variance, and computing it twice would be one loop over the
        // roster too many and one more place for the two to disagree.
        //
        // The terms are KEPT as well as summed. `total()` is the same
        // operations in the same order as the expression that stood here,
        // so the read-back is the number it always was; what is new is
        // that a measurement can afterwards ask which term of `V_t` the
        // update read, at the VIX it read them at. Off the identity
        // neither the sum nor the terms are computed and the field stays
        // at the `None` it was built with -- `params` is fixed for the
        // engine's life, so that branch has nothing to clear -- and
        // nothing here changes WHEN anything is computed.
        let index_variance = if self.params.vix_level_identity == 0.0 {
            0.0
        } else {
            let terms = self.index_conditional_variance_terms_now();
            self.last_index_variance = Some(terms);
            terms.total()
        };

        // The anchor's slow memory, advanced to today BEFORE the step reads
        // it. Arithmetic on the day's own state, no draw, and not run at all
        // with the dial at 0.0.
        if self.params.vix_anchor_memory != 0.0 && self.params.vix_level_identity != 0.0 {
            let mult = self.vix_level_multiplier();
            let implied = crate::market::index_var::vix_from_variance(
                self.params.vix_variance_premium, index_variance) * mult;
            // Times the cycle's VIX scale, exactly 1.0 with
            // `market_vol_cycle_ratio` at 0.0 or the side's power at 0.0,
            // so the memory reads fear against the phase's normal level.
            let anchor = self.vix_anchor * mult * self.market_vol_cycle_vix_scale();
            // The memory is kept against the CENTRE the weight pulls to;
            // guarded, so at 0.0 it is the anchor exactly.
            let anchor = if self.params.vix_anchor_centre != 0.0 {
                anchor * crate::mathx::exp(-self.params.vix_anchor_centre)
            } else {
                anchor
            };
            if implied > 0.0 && anchor > 0.0 {
                let h = self.params.vix_anchor_memory;
                self.vix_anchor_slow = (1.0 - h) * self.vix_anchor_slow
                    + h * crate::mathx::log(implied / anchor);
            }
            // THE PUBLISHED VIX'S STRESS MEMORY, the same deviation at the
            // same rate, so in a free run it is the anchor's memory to the
            // last bit; a session whose VIX a caller pinned holds it at 0.0,
            // so the quote publishes the pin. Arithmetic only, no draw, and
            // not run at all with `vix_stress_premium` at 0.0.
            if self.params.vix_stress_premium != 0.0 {
                if pins_today & PIN_VIX != 0 {
                    self.vix_stress_memory = 0.0;
                } else if implied > 0.0 && anchor > 0.0 {
                    let h = self.params.vix_anchor_memory;
                    self.vix_stress_memory = (1.0 - h) * self.vix_stress_memory
                        + h * crate::mathx::log(implied / anchor);
                }
            }
        }
        rng.site(Site::EconomyDaily, 0);
        let mut inputs =
            self.daily_inputs(request, market_day_return_pct, index_variance, self.vix_anchor_slow,
                              self.market_vol_cycle_vix_scale());
        // THE FEAR MEMORY, advanced to today on the close's own inputs before
        // the step reads it. Arithmetic on the day's state, no draw, and not
        // run at all with `vix_fear_uptake` at 0.0.
        if self.params.vix_fear_uptake != 0.0 {
            self.vix_fear = crate::economy::daily::advance_vix_fear(
                self.vix_fear, &inputs, self.economy.vix, self.params.vix_fear_half_life);
            inputs.vix_fear = self.vix_fear;
        }
        // The priced spread multiplier at the session's start and after
        // tonight's report (`cycle_nowcast_accuracy`,
        // `corporate_spread_cycle`), which `daily_inputs` cannot know: it
        // projects the close before the report.
        if spread_blend {
            inputs.yields.spread_multiplier = Some((spread_before, spread_after_report));
        }
        // The oil price's long factor (`oil_target_drift_sd`): tonight's log
        // change on its own key, so no stream moves; nothing at 0.0.
        let oil_drift_next = self.oil_drift_step(request.game_day);
        if let Some(next) = oil_drift_next {
            inputs.oil_target_drift = Some((self.oil_target_drift, next));
        }
        self.economy = update_economy_daily(&self.economy, &inputs, rng);
        // The host's premium on the target (`set_vix_target_premium`), read
        // by the step above, faded for the next close.
        self.fade_vix_target_premium();
        if let Some(next) = oil_drift_next {
            // A bound that truncated tonight's price moved the long-run level
            // with it (`EconomyState::oil_bound_log_ratio`), so the factor
            // does not walk past the bounds while the price is pinned.
            self.oil_target_drift = next + self.economy.oil_bound_log_ratio;
        }
        if let Some(h) = &held {
            let e = &mut self.economy;
            if pins_today & PIN_VIX != 0 { e.vix = h.vix; }
            if pins_today & PIN_POLICY != 0 { e.federal_funds_rate = h.federal_funds_rate; }
            if pins_today & PIN_INFLATION != 0 { e.inflation_rate = h.inflation_rate; }
            if pins_today & PIN_GROWTH != 0 { e.gdp_growth = h.gdp_growth; }
            if pins_today & PIN_UNEMPLOYMENT != 0 { e.unemployment_rate = h.unemployment_rate; }
            if pins_today & PIN_FEAR_GREED != 0 { e.fear_greed_index = h.fear_greed_index; }
            if pins_today & PIN_OIL != 0 { e.oil_price = h.oil_price; }
            if pins_today & PIN_QE_PE != 0 { e.qe_pe_boost = h.qe_pe_boost; }
            if pins_today & PIN_QE_ASSETS != 0 { e.qe_assets_ratio = h.qe_assets_ratio; }
            if pins_today & PIN_TARIFF != 0 { e.tariff_rate = h.tariff_rate; }
        }
        rng.site(Site::EconomyCycle, 0);
        let spec = self.cycle_spec();
        let aged = self.economy.months_in_current_phase;
        self.economy = check_cycle_transition_for(&self.economy, rng, &spec);
        // A pinned phase holds: the roll is taken and its outcome dropped,
        // and the phase keeps ageing.
        if holds(PIN_CYCLE) {
            if let Some(h) = &held {
                self.economy.cycle_phase = h.cycle_phase;
                self.economy.months_in_current_phase = aged;
            }
        }

        // THE AGGREGATE EARNINGS CYCLE, one step a session after the phase
        // has moved: every company's earnings, beyond what nominal output
        // gives them, pulled toward a level set by the cycle phase at a
        // half-life of `earnings_cycle_half_life` sessions. Contraction and
        // trough pull toward `-depth`, every other phase toward
        // `+depth * earnings_cycle_upside`, the share that centres the
        // level over a cycle. `earnings_cycle_sigma` adds a normal on the
        // economy stream when it is non-zero, and only then. Nothing runs at
        // zero depth, so every preset through pt-v19 takes no draw here and
        // leaves the level at 0.0. See `ModelParams::earnings_cycle_depth`.
        if self.params.earnings_cycle_depth != 0.0 {
            let target = self.earnings_cycle_target();
            let p = &self.params;
            let pull = 1.0 - crate::mathx::pow(0.5, 1.0 / p.earnings_cycle_half_life);
            let mut level = self.economy.earnings_cycle
                + pull * (target - self.economy.earnings_cycle);
            if p.earnings_cycle_sigma != 0.0 {
                rng.site(Site::EconomyCycle, 1);
                level += p.earnings_cycle_sigma * rng.next_normal();
            }
            self.economy.earnings_cycle = level;
        }

        // THE VOLATILITY FEEDBACK'S SMOOTHED EXPOSURE, one step a session
        // after the VIX has moved: the log excess of the VIX over the knee,
        // pulled at a half-life of `fair_value_vix_half_life` sessions.
        // Nothing runs unless both the gain and the half-life are set, so
        // every preset through pt-v19 leaves the field at 0.0. It takes no
        // draw at any setting.
        // A pinned VIX's exposure was set by its pin and holds through the
        // close (`pinned_vix_feedback`).
        let exposure_pinned = self.params.pinned_vix_feedback != 0.0 && pins_today & PIN_VIX != 0;
        if self.params.fair_value_vix_discount != 0.0 && self.params.fair_value_vix_half_life != 0.0
            && !exposure_pinned
        {
            let target = crate::market::tick::vix_excess(&self.params, self.economy.vix);
            // A fall back toward a lower target is pulled at
            // `fair_value_vix_release_half_life` when that is set; a branch,
            // so 0.0 is the one half-life both ways, as it stood.
            let half_life = if self.params.fair_value_vix_release_half_life != 0.0
                && target < self.economy.vix_feedback
            {
                self.params.fair_value_vix_release_half_life
            } else {
                self.params.fair_value_vix_half_life
            };
            let pull = 1.0 - crate::mathx::pow(0.5, 1.0 / half_life);
            self.economy.vix_feedback += pull * (target - self.economy.vix_feedback);
        }

        // THE STRESS THE MEETING READS (`fed_stress_cut`): the highest VIX
        // published since the last meeting, this close's included. Nothing
        // runs with the cut off.
        if self.params.fed_stress_cut != 0.0 {
            self.stress_vix_max = crate::mathx::max(self.stress_vix_max, self.economy.vix);
        }
        // THE STRESS HOLD'S CLOCK (`fed_stress_hold`): this close's published
        // VIX restarts it at 0 when it is at or over `fed_stress_vix`, and
        // any other close ages it a session. Nothing runs with the dial off.
        if self.params.fed_stress_hold != 0.0 {
            self.stress_hold_age = if self.published_vix() >= self.params.fed_stress_vix {
                0.0
            } else {
                crate::mathx::min(self.stress_hold_age + 1.0, STRESS_HOLD_NEVER)
            };
        }
        let policy = self.policy_options(holds(PIN_POLICY));
        // THE INTERMEETING MEETING (`fed_put_emergency_vix`): a VIX close at
        // or above the dial, with inflation under the put's ceiling and room
        // to cut, brings the next meeting forward to tonight, at least 21
        // sessions after the last and only while the next is not yet due.
        // Real ones: 2001-01-03, 2001-09-17, 2008-01-22, 2008-10-08 and
        // March 2020. The calendar the meeting then sets is the ordinary one.
        // The meeting is an ordinary one: it takes a meeting's economy draws
        // on a session the calendar would not, and re-anchors the corporate
        // yield at tonight's VIX (see `ModelParams::fed_put_emergency_vix`).
        if self.params.fed_put_gain != 0.0
            && self.params.fed_put_emergency_vix != 0.0
            && self.economy.vix >= self.params.fed_put_emergency_vix
            && self.economy.inflation_rate < crate::economy::central_bank::FED_PUT_INFLATION_CEILING
            && self.economy.federal_funds_rate > 0.0
            && request.timestamp - self.central_bank.last_meeting_date >= FED_PUT_EMERGENCY_GAP_MINUTES
            && request.timestamp < self.central_bank.next_meeting_date
        {
            self.central_bank.next_meeting_date = request.timestamp;
        }
        let meeting =
            {
                rng.site(Site::CentralBank, 0);
                update_central_bank_with(
                    &self.central_bank, &self.economy, request.timestamp, rng, &policy)
            };
        let meeting_held = meeting.decision.is_some();
        if meeting_held && self.params.fed_stress_cut != 0.0 {
            self.stress_vix_max = 0.0;
        }
        // The rate change the market has just seen moves its forecast by its
        // own size (`treasury_path_pricing`).
        if meeting_held && self.params.treasury_path_pricing != 0.0 {
            let owed = if self.params.fed_put_gain != 0.0 {
                meeting.economy.fed_put_owed - self.economy.fed_put_owed
            } else {
                0.0
            };
            self.policy_path +=
                meeting.economy.federal_funds_rate - self.economy.federal_funds_rate + owed;
        }
        let decision = meeting.decision;
        let announcement_variant = meeting.announcement_variant;
        self.central_bank = meeting.central_bank;
        self.economy = meeting.economy;
        // The meeting's writes to a pinned field are undone. A held 10-year
        // takes the corporate yield (and the 2-year's formula share) with
        // it, since the meeting set both off the 10-year it re-anchored.
        if let Some(h) = &held {
            let e = &mut self.economy;
            if pins_today & PIN_T10 != 0 && meeting_held {
                let d = h.treasury_yield_10y - e.treasury_yield_10y;
                e.treasury_yield_10y = h.treasury_yield_10y;
                e.corporate_bond_yield += d;
                e.mortgage_rate_30y += d;
                if pins_today & PIN_T2 == 0 {
                    e.treasury_yield_2y += 0.15 * d;
                }
            }
            if pins_today & PIN_T2 != 0 { e.treasury_yield_2y = h.treasury_yield_2y; }
            if pins_today & PIN_POLICY != 0 { e.federal_funds_rate = h.federal_funds_rate; }
            if pins_today & PIN_QE_PE != 0 { e.qe_pe_boost = h.qe_pe_boost; }
            if pins_today & PIN_QE_ASSETS != 0 { e.qe_assets_ratio = h.qe_assets_ratio; }
        }
        // A PINNED CORPORATE YIELD HOLDS THROUGH THE CLOSE, the meeting's
        // re-anchoring to the formula included, on a preset that moves it
        // daily (the mark is kept only there). The close has read today's
        // pins; tomorrow's are their own.
        if pins_today & PIN_CORPORATE != 0 {
            self.economy.corporate_bond_yield = corporate_pinned_at;
        }
        // A PINNED SPREAD HOLDS THROUGH THE CLOSE, the meeting included; the
        // 10-year, as the close left it, moves the level.
        if pins_today & PIN_SPREAD != 0 {
            self.economy.corporate_bond_yield =
                self.economy.treasury_yield_10y + self.pinned_corporate_spread;
        }
        // THE ANTICIPATED MEETING (`policy_anticipation`): what the curve
        // prices of the next decision, re-read on tonight's economy, and the
        // 10-year, the 2-year and the corporate yield moved by the change in
        // what they price. A branch: off, nothing runs, and no draw at any
        // setting.
        if self.params.policy_anticipation != 0.0 {
            self.reprice_anticipated_meeting(request, &policy, meeting_held, pins_today);
        }
        self.macro_pins_today = 0;
        self.pinned_vix_jump = 0.0;
        self.advance_anticipation_drift();
        self.refresh_earnings_anticipation();
        // The close's phase into the published history. The burn-in runs
        // this too, and the construction's seeding overwrites what it left.
        self.record_cycle_phase();
        // The close's growth into the published figure's quarter, and any
        // release now due. The burn-in runs this too, and the
        // construction's seeding overwrites what it left.
        self.record_gdp_growth(request.game_day);

        DayAdvanceOutcome {
            phase_changed: self.economy.cycle_phase != phase_before,
            meeting_held,
            decision,
            announcement_variant,
            draws_consumed: 0,
        }
    }

    /// The meeting's options on the state standing: what tonight's meeting
    /// reads in `advance_day_with`, and what the forecast's shadow of the
    /// next meeting reads (`forecast_horizon_sessions`). `hold_rate` is a
    /// pinned policy rate holding through the close.
    fn policy_options(&self, hold_rate: bool) -> crate::economy::PolicyOptions {
        let spread_blend =
            self.params.cycle_nowcast_accuracy != 0.0 || self.params.corporate_spread_cycle != 0.0;
        crate::economy::PolicyOptions {
            calendar: self.macro_calendar(),
            liftoff: self.params.fed_liftoff_rule,
            // The meeting reads the phase after tonight's transition, as it
            // did; under the nowcast that is the belief, which the transition
            // does not move.
            spread_multiplier: if spread_blend {
                Some(self.priced_spread_multiplier(self.economy.cycle_phase))
            } else {
                None
            },
            growth_cut: self.params.fed_growth_cut,
            stress_cut: self.params.fed_stress_cut,
            stress_vix: self.params.fed_stress_vix,
            stress_inflation_gap: self.params.fed_stress_inflation_gap,
            stress_level: self.stress_vix_max,
            hold_rate,
            put_gain: self.params.fed_put_gain,
            put_threshold: self.params.fed_put_threshold,
            put_pricing: self.params.treasury_put_pricing,
            haven_gain: self.params.treasury_haven_gain,
            put_carry: self.params.fed_put_carry,
            stress_hold: self.stress_hold_now(),
            path_gain: self.params.treasury_path_pricing,
            path_before: self.priced_policy_path(),
            rate_damping: self.params.treasury_policy_damping,
            spread_vix_cut: self.params.corporate_spread_vix_cut,
            spread_equity_gain: self.params.corporate_spread_equity_gain,
        }
    }

    /// The daily step's inputs, as the close builds them, with the session's
    /// return, the index variance the identity reads and the anchor's slow
    /// memory supplied: the close passes its own, and the rate indices' live
    /// mark (`rate_intraday_live`) a projection's. The same expressions in
    /// the same order, so the close's inputs are the f64s they were.
    fn daily_inputs<'a>(
        &self,
        request: &DayAdvanceRequest<'a>,
        market_day_return_pct: f64,
        index_variance: f64,
        vix_anchor_slow: f64,
        cycle_vix_scale: f64,
    ) -> DailyInputs<'a> {
        DailyInputs {
            vix_mean_reversion: self.params.vix_mean_reversion,
            vix_decay_ratio: self.params.vix_decay_ratio,
            // The VIX's own slow reversion toward the identity's anchor,
            // and the anchor it reverts to: the derived anchor times the
            // slow regime level's multiplier, which is exactly 1.0 with
            // `vix_level_sigma` at 0.0. The rate ships 0.0 on every
            // preset, where `daily.rs` does not add the term at all, and
            // `ModelParams::invariants` refuses a rate with the identity
            // off, where the anchor is the dial rather than a derived
            // level. See `ModelParams::vix_anchor_reversion`.
            vix_anchor_reversion: self.params.vix_anchor_reversion,
            // Times the cycle's VIX scale (`market_vol_cycle_ratio`), which
            // is exactly 1.0 with the dial at 0.0: the level the VIX reverts
            // to is the phase's normal level. Passed in, because the close
            // reads it after tonight's step and the live mark's projection
            // reads the step it projects.
            vix_anchor_level: self.vix_anchor * self.vix_level_multiplier() * cycle_vix_scale,
            vix_anchor_weight: self.params.vix_anchor_weight,
            vix_anchor_memory: self.params.vix_anchor_memory,
            macro_compound_days_per_year: self.params.macro_compound_days_per_year,
            macro_calendar: self.macro_calendar(),
            cycle_us_calibration: self.params.cycle_us_calibration,
            vix_anchor_centre: self.params.vix_anchor_centre,
            vix_anchor_weight_level: self.params.vix_anchor_weight_level,
            vix_anchor_weight_level_cap: self.params.vix_anchor_weight_level_cap,
            vix_anchor_weight_level_knee: self.params.vix_anchor_weight_level_knee,
            vix_anchor_weight_level_below: self.params.vix_anchor_weight_level_below,
            vix_anchor_weight_level_knee_fixed: self.params.vix_anchor_weight_level_knee_fixed,
            vix_anchor_level_fixed: self.vix_anchor,
            vix_anchor_slow,
            vix_fear_uptake: self.params.vix_fear_uptake,
            vix_fear: self.vix_fear,
            vix_target_premium: self.vix_target_premium,
            vix_target_floor: self.vix_target_floor,
            vix_jump_intensity: self.params.vix_jump_intensity,
            vix_jump_scale: self.params.vix_jump_scale,
            vix_return_level_exponent: self.params.vix_return_level_exponent,
            vix_return_exponent_up: self.params.vix_return_exponent_up,
            vix_return_level_exponent_up: self.params.vix_return_level_exponent_up,
            vix_innovation_sigma: self.params.vix_innovation_sigma,
            vix_innovation_return_sigma: self.params.vix_innovation_return_sigma,
            vix_jump_level_scale: self.params.vix_jump_level_scale,
            vix_jump_return_intensity: self.params.vix_jump_return_intensity,
            vix_return_gain: self.params.vix_return_gain,
            vix_realised_vol_weight: self.params.vix_realised_vol_weight,
            vix_return_source: self.params.vix_return_source,
            vix_cycle_amplitude: self.params.vix_cycle_amplitude,
            market_day_return_pct,
            // WHAT THE MARKET'S OWN VOLATILITY IS WORTH IN VIX POINTS,
            // and it was wrong twice.
            //
            // The expression below the branch is the shipped one: the
            // market FACTOR's conditional sigma, scaled so a factor at
            // its base sigma reads back as the anchor. That is
            // `anchor / market_factor_sigma` = 2105.1 points per unit
            // of daily sigma where the identity -- one point is one per
            // cent annualised -- is `100 * sqrt(252)` = 1587.5; and its
            // referent is the factor, where the quantity a VIX prices
            // is the INDEX's. On the certified roster the index carries
            // 2.05x the factor's variance and none of the remainder
            // ever reached the VIX.
            //
            // At `vix_level_identity` the read-back is the identity
            // itself on the index's own conditional variance. See
            // `ModelParams::vix_level_identity` and
            // `market::index_var`.
            vix_implied_from_market: if self.params.vix_level_identity != 0.0 {
                // The slow VIX level multiplies the identity's target here
                // and nowhere else: a branch at a multiplier of exactly
                // 1.0, so every preset with `vix_level_sigma` 0.0 wires
                // the very value it wired before the level existed.
                let implied = crate::market::index_var::vix_from_variance(
                    self.params.vix_variance_premium,
                    index_variance,
                );
                let mult = self.vix_level_multiplier();
                if mult == 1.0 { implied } else { implied * mult }
            } else if self.params.vix_realised_vol_weight == 0.0 {
                0.0
            } else {
                self.params.market_vol_vix_anchor * self.market_vol.sigma_daily()
                    / self.params.market_factor_sigma
            },
            // The index's conditional daily sigma in PER CENT, which is
            // the units `market_day_return_pct` is in and therefore the
            // units the zero-mean fear correction needs. Zero, and
            // unread, unless the identity is on.
            vix_index_sigma_pct: if self.params.vix_level_identity == 0.0 {
                0.0
            } else {
                100.0 * crate::mathx::sqrt(index_variance)
            },
            vix_level_identity: self.params.vix_level_identity,
            vix_return_gain_up: self.params.vix_return_gain_up,
            vix_return_exponent: self.params.vix_return_exponent,
            vix_return_clamp: self.params.vix_return_clamp,
            vix_target_shock_cap: self.params.vix_target_shock_cap,
            vix_ceiling: self.params.vix_ceiling,
            vix_target_offset: self.params.vix_target_offset,
            inflation_reversion: self.params.inflation_reversion,
            inflation_ceiling: self.params.inflation_ceiling,
            inflation_floor: self.params.inflation_floor,
            crisis_vix_threshold: self.params.crisis_vix_threshold,
            usd_crisis_vix_threshold: self.params.usd_crisis_vix_threshold,
            daily_credit_floor_gain: self.params.daily_credit_floor_gain,
            oil_supply_response: self.params.oil_supply_response,
            oil_opec_symmetry: self.params.oil_opec_symmetry,
            oil_seasonality_target: self.params.oil_seasonality_target,
            trough_growth_floor: self.params.trough_growth_floor,
            phase_target_range_draw: self.params.phase_target_range_draw,
            unemployment_adjustment: self.unemployment_adjustment(),
            unemployment_natural_pull: self.params.unemployment_natural_pull,
            unemployment_okun_coefficient: self.params.unemployment_okun_coefficient,
            unemployment_natural_rate: self.params.unemployment_natural_rate,
            oil_inventory_reversion: self.params.oil_inventory_reversion,
            oil_mean_reversion: self.params.oil_mean_reversion,
            oil_noise_sd: self.params.oil_noise_sd,
            oil_inventory_level_gain: self.params.oil_inventory_level_gain,
            oil_convenience_yield: self.params.oil_convenience_yield,
            oil_convenience_inventory_var: 0.0,
            oil_convenience_inventory_var_before: 0.0,
            oil_target_drift: None,
            oil_pushes_in_target: self.params.oil_pushes_in_target != 0.0,
            oil_noise_log_sd: self.params.oil_noise_log_sd,
            oil_dollar_elasticity: self.params.oil_dollar_elasticity,
            oil_inventory_noise_sd: self.params.oil_inventory_noise_sd,
            usd_mean_reversion: self.params.usd_mean_reversion,
            usd_noise_sd: self.params.usd_noise_sd,
            usd_safe_haven_gain: self.params.usd_safe_haven_gain,
            oil_price_floor: self.params.oil_price_floor,
            oil_price_ceiling: self.params.oil_price_ceiling,
            oil_inflation_passthrough: self.params.oil_inflation_passthrough,
            // The phase and growth as an observer reads them tonight,
            // before the step: the same moment the economy's own are
            // read at with the switch off.
            fear_greed_published: if self.params.fear_greed_published_inputs != 0.0 {
                Some((self.published_cycle_phase(), self.published_gdp_growth()))
            } else {
                None
            },
            yields: crate::economy::daily::YieldDials {
                treasury_10y_noise: self.params.treasury_10y_noise,
                treasury_2y_noise: self.params.treasury_2y_noise,
                flight_to_quality_gain: self.params.flight_to_quality_gain,
                flight_to_quality_day: self.params.flight_to_quality_day,
                corporate_yield_daily: self.params.corporate_yield_daily,
                vix_pinned: self.macro_pins_today & PIN_VIX != 0,
                corporate_pinned: self.macro_pins_today & PIN_CORPORATE != 0,
                // A pinned 10-year or 2-year holds through the close
                // (`macro_pins_hold`), so the close's step and its projection
                // read the pinned level.
                treasury_10y_pinned: self.params.macro_pins_hold != 0.0
                    && self.macro_pins_today & PIN_T10 != 0,
                treasury_2y_pinned: self.params.macro_pins_hold != 0.0
                    && self.macro_pins_today & PIN_T2 != 0,
                // The multiplier the market prices now, held through the
                // session: a projection of the close runs before tonight's
                // report moves it. `advance_day_with` replaces it with the
                // start and post-report pair. `None` with both dials off.
                spread_multiplier: if self.params.cycle_nowcast_accuracy != 0.0
                    || self.params.corporate_spread_cycle != 0.0
                {
                    let m = self.priced_spread_multiplier(self.economy.cycle_phase);
                    Some((m, m))
                } else {
                    None
                },
                // The Fed put the curve prices tonight and the Treasury
                // haven (`treasury_put_pricing`, `treasury_haven_gain`), both
                // 0.0 unless their dials are set; the live mark's projection
                // reads them as the close does.
                priced_put: self.priced_fed_put(),
                priced_path: self.priced_policy_path(),
                priced_anticipation: self.priced_anticipation(),
                rate_damping: self.params.treasury_policy_damping,
                haven_gain: self.params.treasury_haven_gain,
                // Credit's VIX slope and leverage term
                // (`corporate_spread_vix_cut`, `corporate_spread_equity_gain`),
                // 0.0 unless set; the projection reads them as the close does.
                spread_vix_cut: self.params.corporate_spread_vix_cut,
                spread_equity_gain: self.params.corporate_spread_equity_gain,
                // The gap also runs for the cycle's hazard (`cycle_equity_hazard`).
                spread_equity_decay: if self.carries_spread_equity_gap() {
                    crate::mathx::pow(0.5, 1.0 / self.params.corporate_spread_equity_half_life)
                } else {
                    0.0
                },
            },
            volatility: request.volatility,
            active_shocks: request.active_shocks,
            market_return_pct: request.market_return_pct,
            game_day: request.game_day,
        }
    }

    // ── Day-chunked stepping ──────────────────────────────────────────────

    /// Run a whole session in one call, writing per-tick output into a
    /// caller-owned buffer.
    ///
    /// # Why this is core rather than a wrapper convenience
    ///
    /// A caller looping over [`Engine::tick`] from Python crosses the FFI
    /// boundary roughly 98,000 times per simulated year — and worse in
    /// practice, because every attribute read on a returned object is another
    /// crossing. No binding layer can fix that from the outside: if the only
    /// advancement primitive is one tick, the loop is in the host language and
    /// the crossings are unavoidable. So day-chunking lives here.
    ///
    /// # Nothing accumulates beyond one day
    ///
    /// Output goes into [`SessionBuffer`], which the caller sizes once and
    /// drains between days. At tick grain a year of 100 names is ~9.8M rows
    /// per column, so accumulating Rust-side and returning at the end does not
    /// scale — one day is ~312 KB and is reused.
    ///
    /// Buffers are `f64` throughout with no `f32` option, deliberately: the
    /// known-answer files and the cross-platform release gate hash these
    /// buffers, so a half-precision path would be a silent parity break
    /// dressed as a performance switch.
    pub fn run_session(
        &mut self,
        request: &SessionRequest,
        buffer: &mut SessionBuffer,
    ) -> SessionOutcome {
        // The row width is every instrument, rate indices after equities, so
        // the tape carries them in the same rows as the names they trade
        // beside. Without rate instruments this is `companies.len()`.
        buffer.resize(request.ticks, self.instrument_count());
        buffer.resize_counterfactual(self.settle_depth_counterfactual);

        if request.reopen {
            self.open_market();
        }

        // Endogenous news is generated at the DAY boundary (`open_market`)
        // and chained onto the caller's events inside `tick_inner`, which is
        // the ONE point every spelling of a minute passes through. It used to
        // be generated and chained here, and that was wrong twice over
        // (§117): a session is a CALL, so `EngineBatch::tick` re-rolled the
        // day's news on every minute, and `Engine::tick` bypasses this
        // function altogether with `news: &[]`, so a tick-driven caller saw
        // no endogenous news at all. pt-v11 was the first preset to turn the
        // mechanism on, so both were invisible until it did.
        let mut draws = 0usize;
        let (mut hour, mut minute) = (request.start.hour, request.start.minute);
        let mut halted_at = None;

        // The first tick's flow: the standing rate plus the fills, once.
        // Built only when there are fills, so a session without them reads
        // `order_volumes` on every tick exactly as it always did.
        //
        // Under `fill_impact_coefficient` the fills are agent fills priced by
        // the linear law instead, so they join the book's pending flow and
        // the first open tick applies them with every other agent's.
        //
        // The rate indices are not in the agent-facing book and the linear
        // law does not price them, so their fills stay on the first tick's
        // flow, where their own books read them, under either law.
        let first_tick_flow = if request.fills.is_empty() {
            None
        } else if self.params.fill_impact_coefficient != 0.0 {
            let (equity, rates): (Vec<_>, Vec<_>) = request
                .fills
                .iter()
                .cloned()
                .partition(|(t, _)| !self.rates.instruments.iter().any(|i| i.spec.ticker == t.as_str()));
            self.queue_external_fills(&equity);
            if rates.is_empty() {
                None
            } else {
                Some(merge_order_volumes(request.order_volumes, &rates))
            }
        } else {
            Some(merge_order_volumes(request.order_volumes, request.fills))
        };

        for t in 0..request.ticks {
            let order_volumes = match (&first_tick_flow, t) {
                (Some(flow), 0) => flow.as_slice(),
                _ => request.order_volumes,
            };
            let outcome = self.tick(&TickRequest {
                time: GameTime {
                    hour,
                    minute,
                    day_of_week: request.start.day_of_week,
                },
                volatility_multiplier: request.volatility_multiplier,
                news: request.news,
                news_impact_queue: request.news_impact_queue,
                order_volumes,
            });
            draws += outcome.draws_consumed;
            buffer.write_tick(
                t,
                &self.companies,
                &TickTruth {
                    components: &self.tick_components,
                    fundamental: &self.tick_fundamental,
                    anchor: &self.tick_anchor,
                    shock: &self.tick_shock,
                    absorbed: &self.tick_absorbed,
                    clamp: &self.tick_clamp,
                    repriced: &self.tick_repriced,
                    unbounded_print: &self.tick_unbounded_print,
                    liquidity_share: &self.tick_liquidity_share,
                },
            );
            if !self.rates.is_empty() {
                buffer.write_rates(t, self.companies.len(), &self.rates);
            }

            if let Some(stop) = &request.stop {
                if stop.triggered(t, &self.companies) {
                    halted_at = Some(t);
                    buffer.ticks_written = t + 1;
                    break;
                }
            }

            minute += 1;
            if minute >= 60 {
                minute = 0;
                hour += 1;
            }
        }

        if request.close_at_end && halted_at.is_none() {
            // The engine's OWN accumulated noise, where the caller left the
            // slot empty. It cannot be the caller's job to fill it here: the
            // request is built BEFORE the session runs, and the value being
            // asked for is the noise this session is about to accumulate.
            //
            // So on this path an absent innovation is not a choice, it is the
            // only thing an embedder can supply -- and `None` falls through to
            // the day's total return, which is a different quantity (measured
            // elsewhere: median factor 0.82, tenth percentile 0.22, ninetieth
            // 3.20). A parameter that cannot be supplied correctly is a trap,
            // not a feature.
            let own = self.daily_innovation_column();
            let innovations: Vec<Option<f64>> = request
                .daily_innovations
                .iter()
                .enumerate()
                .map(|(i, supplied)| supplied.or_else(|| own.get(i).copied()))
                .collect();
            self.close_market(&DayCloseRequest {
                daily_innovations: &innovations,
                sector_base_variances: request.sector_base_variances,
                avg_volume: AvgVolumePolicy::default(),
            });
        }

        SessionOutcome {
            draws_consumed: draws,
            halted_at,
        }
    }

    // ── The day loop ──────────────────────────────────────────────────────
    //
    // This lived in `python_engine.rs` until the WebAssembly binding needed
    // it too. That is the whole argument for moving it: the crate's own
    // header says the price model is "compiled once and consumed twice",
    // and a day loop implemented separately in each binding is a fork of
    // the model wearing the costume of glue code. The divergence would be
    // invisible until a whole simulated market had drifted apart -- which
    // is exactly the failure this crate was written to end.
    //
    // A binding still owns its own day COUNTER, because counting is not a
    // modelling decision. Everything below is.

    /// Each company's sector base daily variance, in roster order.
    ///
    /// The fallback matches the reference implementation's default for a
    /// sector it does not recognise; a roster is validated on construction,
    /// so it is unreachable in practice and present so this cannot panic on
    /// a surface that has no way to report the error.
    pub fn sector_base_variances(&self) -> Vec<f64> {
        self.companies
            .iter()
            .map(|c| {
                crate::sectors::by_key(&c.sector)
                    .map(|s| s.base_daily_variance())
                    .unwrap_or(0.000225)
            })
            .collect()
    }

    /// Close the trading day: settle the day, then step the macro chain.
    ///
    /// `game_day` is the day being closed, one-based. The caller keeps the
    /// count; the arithmetic on it is here so both bindings agree about
    /// what a day means.
    ///
    /// Daily innovations come from the engine's OWN accumulated noise
    /// rather than from the caller. A second copy of engine state on the
    /// embedder's side is a divergence waiting for the first day the two
    /// disagree, and the caller cannot supply it correctly in any case --
    /// the value being asked for is the noise the session just accumulated.
    /// Read/write the forced-flow segment's spent budget, for checkpoints.
    pub fn market_vol_log_level(&self) -> f64 {
        self.market_vol_log_level
    }

    pub fn vix_log_level(&self) -> f64 {
        self.vix_log_level
    }

    pub fn set_vix_log_level(&mut self, level: f64) {
        self.vix_log_level = level;
    }

    pub fn vix_anchor_slow(&self) -> f64 {
        self.vix_anchor_slow
    }

    pub fn set_vix_anchor_slow(&mut self, value: f64) {
        self.vix_anchor_slow = value;
    }

    /// Sets the anchor a snapshot carries (`vix_level_identity`), in place
    /// of the one this engine derived from the roster it was built on.
    pub fn set_vix_anchor(&mut self, value: f64) {
        self.vix_anchor = value;
    }

    /// The oil long factor's next log level, or `None` with
    /// `oil_target_drift_sd` at 0.0: `(1 - r) D - sigma^2 / 2 + sigma z`,
    /// `z` the first normal of `GameRng::keyed(key, OIL_DRIFT_TAG, day)`.
    fn oil_drift_step(&self, game_day: i64) -> Option<f64> {
        let sigma = self.params.oil_target_drift_sd;
        if sigma == 0.0 {
            return None;
        }
        let z = crate::rng::GameRng::keyed(
            self.oil_drift_key, crate::rng::OIL_DRIFT_TAG as u64, game_day as u64).next_normal();
        let pull = 1.0 - self.params.oil_target_drift_reversion;
        Some(pull * self.oil_target_drift - 0.5 * sigma * sigma + sigma * z)
    }

    /// The oil long factor's state for a snapshot and the state hash,
    /// `[log level, key high word, key low word]` (each word exact in an
    /// f64), or `None` with `oil_target_drift_sd` at 0.0.
    pub fn oil_drift_words(&self) -> Option<[f64; 3]> {
        if self.params.oil_target_drift_sd == 0.0 {
            return None;
        }
        Some([self.oil_target_drift, (self.oil_drift_key >> 32) as f64,
              (self.oil_drift_key & 0xFFFF_FFFF) as f64])
    }

    /// Put the oil long factor back from [`Engine::oil_drift_words`].
    pub fn set_oil_drift_state(&mut self, words: Option<&[f64]>) -> Result<(), String> {
        match (words, self.params.oil_target_drift_sd != 0.0) {
            (None, false) => Ok(()),
            (Some(w), true) => {
                let whole = |v: f64| v.is_finite() && v >= 0.0 && v <= u32::MAX as f64 && v.fract() == 0.0;
                if w.len() != 3 || !w[0].is_finite() || !whole(w[1]) || !whole(w[2]) {
                    return Err(format!(
                        "this snapshot's oil_target_drift is {w:?}: it is the long factor's log \
                         level and its key's two 32-bit words"));
                }
                self.oil_target_drift = w[0];
                self.oil_drift_key = ((w[1] as u64) << 32) | (w[2] as u64);
                Ok(())
            }
            (None, true) => Err("this model runs oil's long factor (oil_target_drift_sd) and \
                                 the snapshot carries no oil_target_drift".to_string()),
            (Some(_), false) => Err("this snapshot carries oil_target_drift and this model's \
                                      oil_target_drift_sd is 0".to_string()),
        }
    }

    /// Whether this engine's model carries the published VIX's stress
    /// memory, which is when the snapshot and the state hash carry it: only
    /// with `vix_stress_premium` non-zero, which no preset sets.
    pub fn carries_vix_stress_memory(&self) -> bool {
        self.params.vix_stress_premium != 0.0
    }

    pub fn vix_stress_memory(&self) -> f64 {
        self.vix_stress_memory
    }

    pub fn set_vix_stress_memory(&mut self, value: f64) {
        self.vix_stress_memory = value;
    }

    /// Whether this engine's model carries the VIX's fear memory, which is
    /// when the snapshot and the state hash carry it: only with
    /// `vix_fear_uptake` non-zero, which no preset sets.
    pub fn carries_vix_fear(&self) -> bool {
        self.params.vix_fear_uptake != 0.0
    }

    /// The VIX's fear memory in log units (`vix_fear_uptake`); 0.0 with the
    /// switch off.
    pub fn vix_fear(&self) -> f64 {
        self.vix_fear
    }

    pub fn set_vix_fear(&mut self, value: f64) {
        self.vix_fear = value;
    }

    /// Put a premium on the VIX target, in VIX points, that fades at
    /// `half_life_sessions` (0.0 holds it until it is written again).
    ///
    /// For fear the macro model does not carry: a bankruptcy's contagion, a
    /// geopolitical escalation. The premium joins the target beside the
    /// inflation and shock terms, inside `vix_target_shock_cap`, so the
    /// engine's own reversion carries the VIX up to it and back down as it
    /// fades, where a one-off write of the VIX itself is reverted to the
    /// engine's target within days. Each close reads the premium and then
    /// fades it by `0.5^(1 / half_life)`; one under a millionth of a point
    /// is cleared to 0.0.
    ///
    /// This REPLACES the premium standing. A host adding an event to one
    /// still fading reads [`Engine::vix_target_premium`] and writes the sum,
    /// at whichever half-life it wants the whole to fade on.
    ///
    /// Consumes no draws. With nothing written the target is bit for bit
    /// the one the engine computes.
    pub fn set_vix_target_premium(&mut self, points: f64, half_life_sessions: f64) -> Result<(), String> {
        if !points.is_finite() {
            return Err(format!("the VIX target premium must be finite, got {points}"));
        }
        if !(half_life_sessions.is_finite() && half_life_sessions >= 0.0) {
            return Err(format!(
                "the VIX target premium's half-life must be 0.0 (held) or a finite number of \
                 sessions above 0, got {half_life_sessions}"
            ));
        }
        if points == 0.0 {
            self.vix_target_premium = 0.0;
            self.vix_target_premium_half_life = 0.0;
        } else {
            self.vix_target_premium = points;
            self.vix_target_premium_half_life = half_life_sessions;
        }
        self.after_vix_target_write();
        Ok(())
    }

    /// A host wrote the VIX target's premium or floor: what reads the next
    /// close's target is re-read now, as after a pin. The live VIX's
    /// projection is dropped for the next tick to recompute, the forecast
    /// re-read (`forecast_horizon_sessions`), and between sessions the
    /// night's path moved (`night_session_steps`). Nothing with those off.
    fn after_vix_target_write(&mut self) {
        self.vix_live = None;
        self.refresh_forecast(None);
        self.futures_retarget();
    }

    /// The host's premium on the VIX target in points, and the half-life it
    /// fades at: `(0.0, 0.0)` with none standing.
    pub fn vix_target_premium(&self) -> (f64, f64) {
        (self.vix_target_premium, self.vix_target_premium_half_life)
    }

    /// Put a floor under the VIX target, or clear it with `None`.
    ///
    /// For a period in which the host's world holds fear at a level, an
    /// election campaign for one: each close's target is at least the
    /// floor, so the VIX reverts toward it at the engine's own rate, and its
    /// noise, jumps and ceiling still apply. The floor holds until it is
    /// cleared. Consumes no draws.
    pub fn set_vix_target_floor(&mut self, floor: Option<f64>) -> Result<(), String> {
        if let Some(f) = floor {
            if !(f.is_finite() && f > 0.0) {
                return Err(format!("the VIX target floor must be finite and above 0, got {f}"));
            }
        }
        self.vix_target_floor = floor;
        self.after_vix_target_write();
        Ok(())
    }

    /// The host's floor under the VIX target, `None` with none set.
    pub fn vix_target_floor(&self) -> Option<f64> {
        self.vix_target_floor
    }

    /// Put the host's input to the VIX target back as a snapshot carries it
    /// (checked by the restore), touching nothing the setters re-read: the
    /// forecast and the live VIX are the snapshot's own.
    pub(crate) fn restore_vix_target_input(&mut self, premium: (f64, f64), floor: Option<f64>) {
        (self.vix_target_premium, self.vix_target_premium_half_life) = premium;
        self.vix_target_floor = floor;
    }

    /// Whether the host's VIX target premium is standing, which is when the
    /// snapshot and the state hash carry it.
    pub fn carries_vix_target_premium(&self) -> bool {
        self.vix_target_premium != 0.0
    }

    /// The premium one close on: faded at its half-life, cleared under a
    /// millionth of a point. Nothing with none standing, or one held.
    fn fade_vix_target_premium(&mut self) {
        let h = self.vix_target_premium_half_life;
        if self.vix_target_premium == 0.0 || h == 0.0 {
            return;
        }
        let faded = self.vix_target_premium * crate::mathx::pow(0.5, 1.0 / h);
        if faded.abs() < VIX_TARGET_PREMIUM_CLEARED_UNDER {
            self.vix_target_premium = 0.0;
            self.vix_target_premium_half_life = 0.0;
        } else {
            self.vix_target_premium = faded;
        }
    }

    /// A caller wrote the VIX: the stress memory restarts from 0.0, so the
    /// quote publishes the pin now and through tonight's close (which keeps
    /// it at 0.0 on a pinned session). Nothing with `vix_stress_premium` at
    /// 0.0.
    pub fn note_vix_pinned(&mut self) {
        if self.params.vix_stress_premium != 0.0 {
            self.vix_stress_memory = 0.0;
        }
    }

    /// The stress premium the published VIX carries over the state, in log
    /// units: `cap * (1 - exp(-g * max(0, m - knee) / cap))` on the stress
    /// memory `m`. Exactly 0.0 with `vix_stress_premium` at 0.0 or the
    /// memory at or below the knee. See `ModelParams::vix_stress_premium`.
    pub fn vix_stress_premium_now(&self) -> f64 {
        self.vix_stress_premium_at(self.vix_stress_memory)
    }

    /// [`Engine::vix_stress_premium_now`] on a stress memory `memory`, for
    /// a projection of tonight's quote (`vix_intraday_live`).
    fn vix_stress_premium_at(&self, memory: f64) -> f64 {
        let p = &self.params;
        if p.vix_stress_premium == 0.0 {
            return 0.0;
        }
        let excess = memory - p.vix_stress_premium_knee;
        if !(excess > 0.0) {
            return 0.0;
        }
        let cap = p.vix_stress_premium_cap;
        cap * (1.0 - crate::mathx::exp(-p.vix_stress_premium * excess / cap))
    }

    /// THE VIX AS PUBLISHED: what `macro_fields`, `macro_state`, the macro
    /// table and its Arrow batch, and the wasm getter report. The engine's
    /// VIX state (`economy().vix`, `state_snapshot()["economy"]["vix"]`),
    /// which every internal reader reads, times `exp(premium)` under
    /// `vix_stress_premium` and never above `vix_ceiling`; the state itself,
    /// bit for bit, with the dial at 0.0, which every preset carries.
    pub fn published_vix(&self) -> f64 {
        self.published_vix_at(self.economy.vix, self.vix_stress_memory)
    }

    /// The quote [`Engine::published_vix`] makes of a VIX state `vix` with
    /// the stress memory at `memory`: the same expressions, for a projection
    /// of tonight's quote (`vix_intraday_live`) and the forecast.
    pub(crate) fn published_vix_at(&self, vix: f64, memory: f64) -> f64 {
        let premium = self.vix_stress_premium_at(memory);
        if !(premium > 0.0) {
            return vix;
        }
        let quote = vix * crate::mathx::exp(premium);
        if quote > self.params.vix_ceiling {
            crate::mathx::max(self.params.vix_ceiling, vix)
        } else {
            quote
        }
    }

    /// The VIX level's per-session innovation AS APPLIED, after the
    /// variance loop's own transmission has been divided out.
    ///
    /// `vix_level_sigma` is the spread of the tape's yearly medians of log
    /// VIX, which is a spread of the VIX and not of the latent multiplier
    /// the engine holds; the loop carries the multiplier to the VIX with a
    /// gain, so the dialled figure has to be divided by that gain before
    /// the level is driven with it. See
    /// `ModelParams::vix_level_loop_gain`, which is 0.0 on every preset
    /// through pt-v18 and returns the dial's own f64 here with no arithmetic
    /// run at all.
    fn vix_level_sigma_applied(&self) -> f64 {
        if self.params.vix_level_loop_gain == 0.0 {
            self.params.vix_level_sigma
        } else {
            self.params.vix_level_sigma / self.params.vix_level_loop_gain
        }
    }

    /// The phase's target for the cycle multiplier, in logs: `ln(k_e)`
    /// outside a contraction or a trough and `ln(R k_e)` in one, read on
    /// the TRUE phase. `k_e` is `market_vol_cycle_expansion`, or at 0.0 the
    /// value that leaves the stationary share-weighted factor variance
    /// unchanged: `1 / sqrt(1 - s + R^2 s)`, `s` the contraction-and-trough
    /// share of the cycle's days. See `ModelParams::market_vol_cycle_ratio`.
    pub fn market_vol_cycle_target_log(&self) -> f64 {
        self.market_vol_cycle_target_log_for(self.economy.cycle_phase)
    }

    /// [`Engine::market_vol_cycle_target_log`] in `phase`: the same
    /// arithmetic, for the forecast, which weighs each phase by the market's
    /// belief rather than reading the true one.
    pub(crate) fn market_vol_cycle_target_log_for(&self, phase: crate::economy::CyclePhase) -> f64 {
        use crate::economy::CyclePhase;
        let r = self.params.market_vol_cycle_ratio;
        let ke = if self.params.market_vol_cycle_expansion != 0.0 {
            self.params.market_vol_cycle_expansion
        } else {
            let (mean, cycle) =
                crate::economy::cycle::stationary_phase_shares_for(&self.cycle_spec());
            let mut s = 0.0;
            for (k, phase) in crate::economy::cycle::phase_cycle().iter().enumerate() {
                if matches!(phase, CyclePhase::Contraction | CyclePhase::Trough) {
                    s += mean[k] / cycle;
                }
            }
            1.0 / crate::mathx::sqrt(1.0 - s + r * r * s)
        };
        // THE RALLY OFF THE LOW (`market_vol_cycle_recovery_release`): in a
        // contraction or a trough the excess is scaled by `1 - g s`, `s`
        // the index's rise off its low over the drawdown's window. A branch
        // at 0.0, every preset, that reads nothing.
        let g_rally = self.params.market_vol_cycle_recovery_release;
        if g_rally != 0.0
            && matches!(phase, CyclePhase::Contraction | CyclePhase::Trough)
        {
            let share = match phase {
                CyclePhase::Trough => 1.0 - self.params.market_vol_cycle_trough_release,
                _ => 1.0,
            };
            let s = crate::mathx::min(
                1.0, self.index_rally_off_low() / self.params.market_vol_cycle_recovery_scale);
            return crate::mathx::log(ke) + share * (1.0 - g_rally * s) * crate::mathx::log(r);
        }
        let k = match phase {
            CyclePhase::Contraction => r * ke,
            // The turn: the trough gives back the share
            // `market_vol_cycle_trough_release` of the contraction's excess
            // (in logs); at 0.0, every preset, it is the contraction's.
            CyclePhase::Trough => {
                let g = self.params.market_vol_cycle_trough_release;
                if g == 0.0 {
                    r * ke
                } else {
                    return crate::mathx::log(ke) + (1.0 - g) * crate::mathx::log(r);
                }
            }
            _ => ke,
        };
        crate::mathx::log(k)
    }

    /// The index's log rise from its lowest close since its highest close
    /// of the last [`DRAWDOWN_WINDOW`] sessions, read on the drawdown's
    /// window (total public market cap, through the last close booked):
    /// 0.0 at a new high or a new low, and 0.0 with no window kept.
    pub fn index_rally_off_low(&self) -> f64 {
        let (mut level, mut high, mut low) = (0.0, 0.0, 0.0);
        for r in self.drawdown_returns.iter() {
            level += r;
            if level >= high {
                high = level;
                low = level;
            } else if level < low {
                low = level;
            }
        }
        level - low
    }

    /// Step the cycle multiplier toward its phase's value and return it
    /// (in logs). The first step, and every step at a half-life of 0.0,
    /// lands on the target. Called once a close, only with
    /// `market_vol_cycle_ratio` set.
    fn step_market_vol_cycle(&mut self) -> f64 {
        let l = self.market_vol_cycle_next_log();
        self.market_vol_cycle_log = Some(l);
        l
    }

    /// The cycle multiplier tonight's step would leave (in logs), without
    /// taking the step: [`Engine::step_market_vol_cycle`]'s arithmetic, for
    /// it and for the live rate mark's projection of the close
    /// (`rate_intraday_live`), which must read the close's own multiplier.
    fn market_vol_cycle_next_log(&self) -> f64 {
        // On a session a caller's pins cover, the multiplier relaxes toward
        // one (a log of 0.0), which is what the session applies, so a run
        // the pins release steps from there toward its phase's value at
        // the half-life instead of jumping to it
        // (`market_vol_cycle_pin_neutral`, `market_vol_cycle_pin_phase`).
        let target = if self.market_vol_cycle_pinned() {
            0.0
        } else {
            self.market_vol_cycle_target_log()
        };
        // The release half-life (`market_vol_cycle_release_half_life`)
        // while the multiplier falls toward a lower target, as a phase
        // turns up; the one half-life otherwise, and always at 0.0.
        let release = self.params.market_vol_cycle_release_half_life;
        let hl = match self.market_vol_cycle_log {
            Some(prev) if release != 0.0 && target < prev => release,
            _ => self.params.market_vol_cycle_half_life,
        };
        match self.market_vol_cycle_log {
            Some(prev) if hl > 0.0 => {
                let a = 1.0 - crate::mathx::exp(-std::f64::consts::LN_2 / hl);
                prev + a * (target - prev)
            }
            _ => target,
        }
    }

    /// `exp(d l)`: the cycle multiplier to a power, which the VIX
    /// coupling's denominator, the anchor's slow memory and the anchor level
    /// are scaled by. The power is `market_vol_cycle_relative` while the
    /// multiplier is at or over one (`l >= 0`, a stormier phase than
    /// normal) and `market_vol_cycle_relative_calm` while it is under one
    /// (a calmer phase). Exactly 1.0 with `market_vol_cycle_ratio` at 0.0,
    /// with the side's power at 0.0, and before the first close.
    ///
    /// Floored at `VIX_STATE_FLOOR / vix_anchor`, so the scaled denominator
    /// and anchor never fall below the level the VIX itself is floored at.
    /// Without the floor a multiplier under about 0.5 at a power of 1 put
    /// the denominator under the VIX's floor of 10, the VIX-over-denominator
    /// ratio ran away, and the factor variance went to its ceiling: a
    /// quiet phase read as a panic (bear-dynamics review: a 0.05 multiplier
    /// at power 1 gave index volatility of 64 per cent). The floor binds
    /// only there; at the shipped candidates the scale is 0.8 or more
    /// against a floor of about 0.48.
    fn market_vol_cycle_vix_scale(&self) -> f64 {
        self.market_vol_cycle_vix_scale_at(
            self.market_vol_cycle_log.map(|l| self.market_vol_cycle_applied_log(l)))
    }

    /// [`Engine::market_vol_cycle_vix_scale`] at an explicit multiplier.
    fn market_vol_cycle_vix_scale_at(&self, log: Option<f64>) -> f64 {
        match log {
            Some(l) if self.params.market_vol_cycle_ratio != 0.0 => {
                let d = if l < 0.0 {
                    self.params.market_vol_cycle_relative_calm
                } else {
                    self.params.market_vol_cycle_relative
                };
                if d == 0.0 {
                    1.0
                } else {
                    let floor = if self.vix_anchor > 0.0 {
                        crate::params::VIX_STATE_FLOOR / self.vix_anchor
                    } else {
                        0.0
                    };
                    crate::mathx::max(crate::mathx::exp(d * l), floor)
                }
            }
            _ => 1.0,
        }
    }

    /// The factor `fair_value_market_vol_cap`'s ceiling is scaled by:
    /// `exp(p l)` while the cycle multiplier is over one (`l > 0`), `p`
    /// being `market_vol_cycle_cap_relative`, so a stormier phase's own
    /// normal volatility is not read as a fear regime. Exactly 1.0 with
    /// `market_vol_cycle_ratio` or the power at 0.0, in a calmer phase and
    /// before the first close.
    pub fn market_vol_cycle_cap_scale(&self) -> f64 {
        let p = self.params.market_vol_cycle_cap_relative;
        match self.market_vol_cycle_log.map(|l| self.market_vol_cycle_applied_log(l)) {
            Some(l) if self.params.market_vol_cycle_ratio != 0.0 && p != 0.0 && l > 0.0 => {
                crate::mathx::exp(p * l)
            }
            _ => 1.0,
        }
    }

    /// Whether a caller's pins withhold the cycle multiplier today: a
    /// session whose VIX a caller pinned under
    /// `market_vol_cycle_pin_neutral`, or whose cycle phase a caller pinned
    /// under `market_vol_cycle_pin_phase`. Always false with both switches
    /// at 0.0, every preset.
    ///
    /// A pinned VIX or phase is the caller's statement of the state (a
    /// replay's 2020 is 2020 whatever phase the engine's own cycle happens
    /// to be in; a scenario's recession arrives with its own VIX, credit
    /// and earnings transmission), so scaling the variance by the engine's
    /// own phase on top double-counts or contradicts it. See
    /// `ModelParams::market_vol_cycle_pin_neutral`.
    fn market_vol_cycle_pinned(&self) -> bool {
        (self.params.market_vol_cycle_pin_neutral != 0.0 && self.macro_pins_today & PIN_VIX != 0)
            || (self.params.market_vol_cycle_pin_phase != 0.0
                && self.macro_pins_today & PIN_CYCLE != 0)
    }

    /// The cycle multiplier as today's session and tonight's close APPLY
    /// it, in logs, given the multiplier `l` itself: 0.0 (a multiplier of
    /// one) on a session a caller's pins cover
    /// ([`Engine::market_vol_cycle_pinned`]), and `l` itself, bit for bit,
    /// on every other.
    fn market_vol_cycle_applied_log(&self, l: f64) -> f64 {
        if self.market_vol_cycle_pinned() {
            0.0
        } else {
            l
        }
    }

    /// Whether this engine's model carries the cycle's volatility
    /// multiplier, which is when the snapshot and the state hash carry it:
    /// only with `market_vol_cycle_ratio` set, and once a close has set it.
    /// Off on every shipped preset.
    pub fn carries_market_vol_cycle(&self) -> bool {
        self.params.market_vol_cycle_ratio != 0.0 && self.market_vol_cycle_log.is_some()
    }

    /// The cycle's multiplier on the market factor's volatility, in logs;
    /// `None` before the first close with the dial set. See
    /// [`crate::params::ModelParams::market_vol_cycle_ratio`].
    pub fn market_vol_cycle_log(&self) -> Option<f64> {
        self.market_vol_cycle_log
    }

    /// Put the cycle multiplier back (a restore). `None` is a model without
    /// it, or one no close has run on.
    pub fn set_market_vol_cycle_log(&mut self, value: Option<f64>) {
        self.market_vol_cycle_log = value;
    }

    /// The multiplier the VIX's slow level applies to what the VIX prices:
    /// exactly 1.0 at sigma 0.0, mean one otherwise.
    fn vix_level_multiplier(&self) -> f64 {
        if self.params.vix_level_sigma == 0.0 || self.vix_log_level == 0.0 {
            return 1.0;
        }
        let phi = self.params.vix_level_persistence;
        let one_minus = 1.0 - phi * phi;
        // The SAME dispersion the recursion runs on, which is the point of
        // reading it from one place: the log-normal correction below and
        // the opening draw would otherwise disagree about how wide the
        // level is the moment the gain is turned on.
        let sigma = self.vix_level_sigma_applied();
        let stationary_var = if one_minus > 0.0 { sigma * sigma / one_minus } else { 0.0 };
        crate::mathx::exp(self.vix_log_level - 0.5 * stationary_var)
    }

    pub fn set_market_vol_log_level(&mut self, level: f64) {
        self.market_vol_log_level = level;
    }

    pub fn forced_flow_spent(&self) -> f64 {
        self.forced_flow_spent
    }

    pub fn set_forced_flow_spent(&mut self, spent: f64) {
        self.forced_flow_spent = spent;
    }

    /// Nominal output when this engine was built, for checkpoints and
    /// forks. See the field's own comment for what depends on it.
    pub fn nominal_output_base(&self) -> f64 {
        self.nominal_output_base
    }

    /// Put a restored engine back on the base its snapshot was taken
    /// under. A restore that reset the economy and left this alone would
    /// price a market against output it never had.
    pub fn set_nominal_output_base(&mut self, base: f64) {
        self.nominal_output_base = base;
    }

    /// One day of the library's own loop, numbered: the trading day `day`,
    /// counted from zero, as one session of `ticks` minutes from 09:30, then
    /// the close and the macro step into `day + 1`. It is
    /// [`Engine::set_current_day`], [`Engine::open_market`],
    /// [`Engine::run_session`] and [`Engine::close_day`] in that order, the
    /// loop the Python package's `run_days` runs, and what the WebAssembly
    /// `Sim` and [`fixed_simulation_digest`] call, so neither numbers its
    /// days differently from the other.
    pub(crate) fn run_numbered_day(&mut self, day: i64, ticks: usize, buffer: &mut SessionBuffer) {
        self.set_current_day(day);
        self.open_market();
        // `reopen: false`, since the day is opened above, once, and
        // `close_at_end: false`, since `close_day` below settles the day and
        // steps the macro chain; a session that closed itself would settle
        // the day without that step. `day_of_week: 3` is the Python
        // surface's default.
        self.run_session(&SessionRequest::new(crate::market::GameTime::new(9, 30, 3), ticks), buffer);
        self.close_day(day + 1);
    }

    /// Close the trading day and step the economy to the next one.
    ///
    /// This settles the day the ticks just traded: it feeds each name's
    /// accumulated noise to GARCH as the day's innovation, rolls the daily
    /// open and close, then runs [`Engine::advance_macro_day`] for
    /// `game_day` (the macro chain, the central bank and the rates).
    ///
    /// It does not trade and draws no prices. Called on its own, with no
    /// session since the last close, it leaves every price where it was. A
    /// day that moves the market is [`Engine::set_current_day`], then
    /// [`Engine::open_market`], then [`Engine::run_session`] (or
    /// [`Engine::tick`] over the session's 390 minutes), then this.
    pub fn close_day(&mut self, game_day: i64) {
        self.close_day_with_shocks(game_day, &[]);
    }

    /// [`Engine::close_day`] with economic shocks active in tonight's macro
    /// step: the close a host that passes shocks runs.
    ///
    /// The shocks reach the step as
    /// [`DayAdvanceRequest::active_shocks`] would, and everything else is
    /// `close_day`'s: the GARCH innovations and sector variances the engine
    /// holds, the market P/E written before the step, the re-mark of each
    /// price to the macro data the step publishes, and the rate indices'
    /// close. A host that instead calls [`Engine::close_market`] and
    /// [`Engine::advance_day`] itself gets none of those last three. With
    /// no shocks this is `close_day`, draw for draw.
    ///
    /// Every preset was fitted with no shocks, so any shock passed here is
    /// outside the fitted flow; [`crate::flow::ExternalFlow`] measures how
    /// far.
    pub fn close_day_with_shocks(&mut self, game_day: i64, active_shocks: &[EconomicShock]) {
        let noise = self.daily_innovation_column();
        let innovations: Vec<Option<f64>> = noise.into_iter().map(Some).collect();
        let variances = self.sector_base_variances();
        self.close_market(&DayCloseRequest {
            daily_innovations: &innovations,
            sector_base_variances: &variances,
            avg_volume: crate::market::AvgVolumePolicy::Hold,
        });
        self.advance_macro_day_with_shocks(game_day, active_shocks);
    }

    /// Step the macro chain into the next day.
    ///
    /// Inputs assembled the way the reference implementation's day
    /// transition assembles them:
    ///
    /// - `market_pe`: market-cap-weighted trailing PE over public, solvent,
    ///   positive-earnings names, with the same `0 < pe < 200` filter.
    ///   Written BEFORE the step because the cycle-transition logic reads
    ///   it; the reference implementation computes it in the same breath.
    /// - `market_return_pct`: the reference feeds the average of its
    ///   indices' per-TICK `changePercent` (percent units, so a routine
    ///   value is a few hundredths). There is no index state on this
    ///   surface, so the closest faithful quantity is the cap-weighted
    ///   cross-section of the roster's final tick, in percent. Feeding the
    ///   DAY return instead would run two orders of magnitude hot against
    ///   the +-0.03 clamp the VIX update applies to this input.
    /// - `volatility`: 1.0, `update_economy_daily`'s own default. The game
    ///   scales this by difficulty (0.3 to 0.9); the library has no
    ///   difficulty setting and takes the function's default.
    /// - no active shocks: [`Engine::advance_macro_day_with_shocks`] takes
    ///   them.
    pub fn advance_macro_day(&mut self, game_day: i64) -> DayAdvanceOutcome {
        self.advance_macro_day_with_shocks(game_day, &[])
    }

    /// [`Engine::advance_macro_day`] with economic shocks active in the
    /// step. With none it is `advance_macro_day`, draw for draw.
    pub fn advance_macro_day_with_shocks(
        &mut self,
        game_day: i64,
        active_shocks: &[EconomicShock],
    ) -> DayAdvanceOutcome {
        let mut total_mcap = 0.0;
        let mut weighted_pe = 0.0;
        let mut last_tick_mcap = 0.0;
        let mut last_tick_return_pct = 0.0;
        // The trailing PE is a price over an EARNINGS figure, and under
        // `earnings_nominal_growth` the earnings the model believes a
        // company has are its day-0 figure restated in today's price level
        // and output. Reading the day-0 figure against a price that grew
        // with nominal output would report a multiple that rose because
        // the mechanism ran, and the expansion hazard in `economy::cycle`
        // adds `min(0.1, (pe - 28) * 0.005)` a day above a multiple of 28.
        //
        // Measured on one trajectory, pt-v18 on `Universe.random(40,
        // seed=111)` seed 1 over 1008 days: the restated multiple ends at
        // 30.214 and crosses 28 on 32 days for a summed hazard of 0.2536,
        // and the day-0 denominator on the same tape gives 32.284, 42 days
        // and 0.6344. Inside a certified year neither reaches the gate:
        // the multiple at day 251 is 21.671 at a ratio of 1.0448, so this
        // is worth nothing over 252 days and a third of the expansion
        // hazard over four years. Exactly 1.0 under every preset before
        // pt-v18.
        let nominal = crate::market::tick::nominal_scale(
            &self.params, &self.economy, self.nominal_output_base);
        for c in self.companies() {
            if !c.is_public || c.is_bankrupt {
                continue;
            }
            if let Some(eps) = c.eps {
                if eps > 0.0 {
                    // A branch, as at the valuation, so the arithmetic
                    // before pt-v18 is the arithmetic it always was.
                    let earnings = if nominal == 1.0 { eps } else { eps * nominal };
                    // The earnings the valuation holds carry the name's own
                    // fair-value level (`fair_value_news_share`), so the
                    // market P/E reads the earnings the price does. A branch:
                    // `None` on every preset through pt-v19.
                    let earnings = match c.stock.fair_value_offset {
                        Some(v) if v != 0.0 => earnings * crate::mathx::exp(v),
                        _ => earnings,
                    };
                    // `market_pe_buybacks`: the earnings the valuation holds
                    // also carry the buyback term (`market::tick`), so a
                    // multiple read without it rises by the buyback yield
                    // every year. A branch, so 0.0 is the line that stood.
                    let earnings = if self.params.market_pe_buybacks != 0.0 {
                        earnings * crate::market::tick::buyback_factor(
                            &self.params, crate::market::dividends::buyback_share(&self.params, c),
                            Some(earnings), c.stock.price, self.elapsed_days,
                            c.stock.buyback_log_shares)
                    } else {
                        earnings
                    };
                    let pe = c.stock.price / earnings;
                    if pe > 0.0 && pe < 200.0 {
                        total_mcap += c.stock.market_cap;
                        weighted_pe += pe * c.stock.market_cap;
                    }
                }
            }
            if let Some(prev) = c.stock.previous_tick_price {
                if prev > 0.0 {
                    let ret_pct = (c.stock.price - prev) / prev * 100.0;
                    last_tick_return_pct += ret_pct * c.stock.market_cap;
                    last_tick_mcap += c.stock.market_cap;
                }
            }
        }
        if total_mcap > 0.0 {
            self.economy_mut().market_pe = Some(weighted_pe / total_mcap);
        }
        let market_return_pct = if last_tick_mcap > 0.0 {
            last_tick_return_pct / last_tick_mcap
        } else {
            0.0
        };
        // The fair values the market stands on BEFORE the step, taken only
        // with `macro_publication_repricing` on: the step's decision is
        // readable the moment it ends, so the price takes it then.
        let marks = self.published_macro_marks();
        let hold = self.carries_earnings_withheld();
        let level_before = self.economy.earnings_cycle + self.economy.earnings_anticipation;
        let outcome = self.advance_day(&DayAdvanceRequest {
            volatility: 1.0,
            active_shocks,
            market_return_pct,
            game_day,
            timestamp: game_day * 24 * 60,
        });
        // THE CYCLE A NAME LEARNS FROM ITS OWN REPORT
        // (`earnings_cycle_report_share`): the share of the step's move in
        // the cycle's level that each traded name's fair value holds back
        // until its next report. Before the re-mark below, so tonight's
        // price takes only the rest. A branch: off, nothing runs.
        if hold {
            let delta = self.economy.earnings_cycle + self.economy.earnings_anticipation
                - level_before;
            self.withhold_cycle(delta);
        }
        self.reprice_to_published_macro(marks);
        // THE RATE INDICES AT THE SAME CLOSE (`rate_close_remark`): the curve
        // the step just published reaches their levels now, beside the
        // equities' re-mark, and not at the next open. A branch.
        self.rate_live = None;
        self.vix_live = None;
        if self.params.rate_close_remark != 0.0 && !self.rates.is_empty() {
            self.rates.remark_now(&self.economy);
        }
        // The forecast on the state the close leaves (`forecast_horizon_sessions`).
        // Nothing with the dial at 0.0.
        self.refresh_forecast(Some(game_day));
        // The VIX futures' marks on that forecast and the VIX the close
        // published (`futures_vix_listed`). Nothing with the switch off.
        self.vix_futures_close_marks();
        // The rate futures on the policy rate the close set and the forecast
        // (`futures_rates_listed`): a period that ends here settles, and each
        // contract is marked. Nothing with the switch off.
        self.rate_futures_close_marks();
        // The oil futures' marks on the forecast (`futures_oil_listed`).
        self.oil_futures_close_marks();
        // The contracts' margin on every family's marks
        // (`margin_scan_coverage`). Nothing with the dial off.
        self.margin_close_update();
        // The night's path for the index futures, to the next open as a copy
        // runs it (`night_session_steps`). Nothing with the dial at 0.0.
        self.futures_night_setup();
        outcome
    }

    /// A pin wrote the curve: re-mark the rate indices to it now, as the
    /// equities are (`rate_close_remark`), and drop the live mark's
    /// projections, which the next tick recomputes on the pinned state.
    /// Nothing with the switch off or without rate instruments.
    pub fn remark_rates_after_pin(&mut self) {
        self.rate_live = None;
        // The live VIX's projection was of the state before the pin; the next
        // tick projects the pinned one (`vix_intraday_live`).
        self.vix_live = None;
        if self.params.rate_close_remark != 0.0 && !self.rates.is_empty() {
            self.rates.remark_now(&self.economy);
        }
        // The forecast, re-read on the pinned state (`forecast_horizon_sessions`).
        self.refresh_forecast(None);
        // Between sessions, the night's path moves by what the pin does to
        // the next open (`night_session_steps`); nothing otherwise.
        self.futures_retarget();
    }

    /// The live mark's move over the published curve, `now - base`, while a
    /// session carries one (`rate_intraday_live`); `None` otherwise.
    pub fn rate_live_curve(&self) -> Option<crate::rates::LiveCurve> {
        if self.params.rate_intraday_live == 0.0 {
            return None;
        }
        let m = self.rate_live?;
        Some(crate::rates::LiveCurve { d2y: m[3] - m[0], d10y: m[4] - m[1], dcorp: m[5] - m[2] })
    }

    /// Each rate instrument's level and yield (a fraction) as it is marked
    /// now: the live mark's during a session under `rate_intraday_live`, the
    /// committed level and yield otherwise.
    pub fn rate_marks(&self) -> Vec<(f64, f64)> {
        let live = self.rate_live_curve();
        self.rates
            .instruments
            .iter()
            .map(|i| match live {
                Some(c) => {
                    let dy = c.dy(i.spec.point);
                    (i.level_at(dy), i.marked_yield + dy)
                }
                None => (i.level, i.marked_yield),
            })
            .collect()
    }

    /// The live mark's projections, for the snapshot: `Some` only while
    /// `rate_intraday_live` is set and a session carries them.
    pub fn rate_live_marks(&self) -> Option<[f64; 6]> {
        if self.params.rate_intraday_live == 0.0 {
            return None;
        }
        self.rate_live
    }

    /// For a restore. Refused with the dial off, where no engine writes them.
    pub fn set_rate_live_marks(&mut self, marks: Option<[f64; 6]>) -> Result<(), String> {
        if marks.is_some() && self.params.rate_intraday_live == 0.0 {
            return Err("this snapshot carries the rate indices' live mark (rate_live_marks), \
                        which only an engine with rate_intraday_live on writes, and this \
                        engine's model has it off. Restore it into the model it was taken from."
                .to_string());
        }
        self.rate_live = marks;
        Ok(())
    }

    /// The stress level the next meeting reads (`fed_stress_cut`), for the
    /// snapshot: `Some` only while the cut is set.
    pub fn stress_vix_max(&self) -> Option<f64> {
        if self.params.fed_stress_cut == 0.0 {
            None
        } else {
            Some(self.stress_vix_max)
        }
    }

    /// The stress hold's clock (`fed_stress_hold`), for the snapshot:
    /// `Some` only while the dial is set.
    pub fn stress_hold_age(&self) -> Option<f64> {
        if self.params.fed_stress_hold == 0.0 {
            None
        } else {
            Some(self.stress_hold_age)
        }
    }

    /// For a restore. Refused with the dial off, where no engine writes it.
    pub fn set_stress_hold_age(&mut self, age: Option<f64>) -> Result<(), String> {
        match age {
            Some(_) if self.params.fed_stress_hold == 0.0 => Err(
                "this snapshot carries the central bank's stress-hold clock \
                 (fed_stress_hold_age), which only an engine with fed_stress_hold on writes, \
                 and this engine's model has it off. Restore it into the model it was taken \
                 from."
                    .to_string()),
            Some(v) => {
                self.stress_hold_age = v;
                Ok(())
            }
            None => {
                self.stress_hold_age = STRESS_HOLD_NEVER;
                Ok(())
            }
        }
    }

    /// The drawdown's window and its base (`fed_drawdown_hold`), for the
    /// snapshot: `Some` only while the dial is set.
    pub fn drawdown_state(&self) -> Option<(Vec<f64>, f64)> {
        if !self.keeps_drawdown_window() {
            None
        } else {
            Some((self.drawdown_returns.iter().copied().collect(), self.drawdown_mcap_prev))
        }
    }

    /// For a restore. Refused with the dial off, where no engine writes it,
    /// and for a window longer than [`DRAWDOWN_WINDOW`].
    pub fn set_drawdown_state(&mut self, state: Option<(Vec<f64>, f64)>) -> Result<(), String> {
        match state {
            Some(_) if !self.keeps_drawdown_window() => Err(
                "this snapshot carries the drawdown hold's window (fed_drawdown_returns), \
                 which only an engine with fed_drawdown_hold or \
                 market_vol_cycle_recovery_release on writes, and this engine's model has \
                 both off. Restore it into the model it was taken from."
                    .to_string()),
            Some((returns, _)) if returns.len() > DRAWDOWN_WINDOW => Err(format!(
                "this snapshot's fed_drawdown_returns carries {} sessions; the window is {}.",
                returns.len(), DRAWDOWN_WINDOW)),
            Some((returns, prev)) => {
                self.drawdown_returns = returns.into_iter().collect();
                self.drawdown_mcap_prev = prev;
                Ok(())
            }
            None => {
                self.drawdown_returns.clear();
                self.drawdown_mcap_prev = 0.0;
                Ok(())
            }
        }
    }

    /// The market's forecast of the policy path (`treasury_path_pricing`),
    /// for the snapshot: `Some` only while the dial is set.
    pub fn policy_path(&self) -> Option<f64> {
        if self.params.treasury_path_pricing == 0.0 {
            None
        } else {
            Some(self.policy_path)
        }
    }

    /// For a restore. Refused with the dial off, where no engine writes it.
    pub fn set_policy_path(&mut self, path: Option<f64>) -> Result<(), String> {
        match path {
            Some(_) if self.params.treasury_path_pricing == 0.0 => Err(
                "this snapshot carries the market's forecast of the policy path \
                 (treasury_policy_path), which only an engine with treasury_path_pricing on \
                 writes, and this engine's model has it off. Restore it into the model it was \
                 taken from."
                    .to_string()),
            Some(v) => {
                self.policy_path = v;
                Ok(())
            }
            None => {
                self.policy_path = 0.0;
                Ok(())
            }
        }
    }

    /// The policy path the curve prices tonight (`treasury_path_pricing`),
    /// percentage points, signed: the dial times the market's forecast.
    /// 0.0 with the dial off.
    fn priced_policy_path(&self) -> f64 {
        if self.params.treasury_path_pricing == 0.0 {
            0.0
        } else {
            self.params.treasury_path_pricing * self.policy_path
        }
    }

    /// What the curve prices of the next meeting (`policy_anticipation`),
    /// for the snapshot: `Some` only while the dial is set.
    pub fn policy_anticipation_priced(&self) -> Option<f64> {
        if self.params.policy_anticipation == 0.0 {
            None
        } else {
            Some(self.policy_anticipation_priced)
        }
    }

    /// For a restore. Refused with the dial off, where no engine writes it.
    pub fn set_policy_anticipation_priced(&mut self, priced: Option<f64>) -> Result<(), String> {
        match priced {
            Some(_) if self.params.policy_anticipation == 0.0 => Err(
                "this snapshot carries what the curve prices of the next meeting \
                 (policy_anticipation_priced), which only an engine with policy_anticipation \
                 on writes, and this engine's model has it off. Restore it into the model it \
                 was taken from."
                    .to_string()),
            Some(v) => {
                self.policy_anticipation_priced = v;
                Ok(())
            }
            None => {
                self.policy_anticipation_priced = 0.0;
                Ok(())
            }
        }
    }

    /// The next decision the curve prices tonight (`policy_anticipation`),
    /// percentage points, signed. 0.0 with the dial off.
    fn priced_anticipation(&self) -> f64 {
        if self.params.policy_anticipation == 0.0 {
            0.0
        } else {
            self.policy_anticipation_priced
        }
    }

    /// The change the next meeting would make if it were held at tonight's
    /// close, as the market can forecast it: the meeting function on the
    /// economy as published (the published phase, growth and VIX; inflation,
    /// unemployment and the rate as they stand), with tonight's options, on
    /// a silent draw source. No stream is touched and nothing is written.
    fn shadow_meeting_change(&self, request: &DayAdvanceRequest, options: &crate::economy::PolicyOptions) -> f64 {
        struct Silent;
        impl Rng for Silent {
            fn next_f64(&mut self) -> f64 {
                0.5
            }
            fn next_normal(&mut self) -> f64 {
                0.0
            }
        }
        let mut economy = self.economy.clone();
        economy.cycle_phase = self.published_cycle_phase();
        economy.gdp_growth = self.published_gdp_growth();
        economy.vix = self.published_vix();
        let mut cb = self.central_bank;
        cb.next_meeting_date = request.timestamp;
        let options = crate::economy::PolicyOptions {
            stress_level: self.stress_vix_max,
            stress_hold: self.stress_hold_now(),
            path_before: self.priced_policy_path(),
            ..*options
        };
        let shadow = update_central_bank_with(&cb, &economy, request.timestamp, &mut Silent, &options);
        shadow.economy.federal_funds_rate - self.economy.federal_funds_rate
    }

    /// `policy_anticipation` at the close: `P = a w S`, with `S` the shadow
    /// meeting's change (a cut times `policy_anticipation_cut_share`) and `w`
    /// the share of the interval from the last meeting to the next already
    /// elapsed. The curve holds `f P` above what it would be without it,
    /// `f` the pass-through the priced path has (`1 - d` on the 10-year and
    /// the corporate yield, `0.85 + 0.15 (1 - d)` on the 2-year, with `d`
    /// `treasury_policy_damping`): the daily anchor keeps it, and a meeting,
    /// which re-anchors the 10-year halfway to a target that does not carry
    /// it and sets the 2-year from the rate, leaves half of it on the 10-year
    /// and the corporate yield and 0.15 of that on the 2-year. So the close
    /// moves each by what it should price less what it holds. A pinned
    /// field is left where the pin holds it.
    fn reprice_anticipated_meeting(
        &mut self,
        request: &DayAdvanceRequest,
        options: &crate::economy::PolicyOptions,
        meeting_held: bool,
        pins_today: u16,
    ) {
        let a = self.params.policy_anticipation;
        let change = self.shadow_meeting_change(request, options);
        let change = if change < 0.0 { change * self.params.policy_anticipation_cut_share } else { change };
        let (last, next) = (self.central_bank.last_meeting_date, self.central_bank.next_meeting_date);
        let w = if next > last {
            crate::mathx::min(1.0, crate::mathx::max(0.0, (request.timestamp - last) as f64 / (next - last) as f64))
        } else {
            1.0
        };
        let priced = a * w * change;
        let before = self.policy_anticipation_priced;
        self.policy_anticipation_priced = priced;
        let f10 = 1.0 - self.params.treasury_policy_damping;
        let f2 = 0.85 + 0.15 * f10;
        let (held10, held2) = if meeting_held { (0.5 * f10 * before, 0.075 * f10 * before) } else { (f10 * before, f2 * before) };
        let (d10, d2) = (f10 * priced - held10, f2 * priced - held2);
        let e = &mut self.economy;
        if pins_today & PIN_T10 == 0 {
            e.treasury_yield_10y = crate::mathx::clamp_via_min_max(e.treasury_yield_10y + d10, 0.5, 12.0);
        }
        if pins_today & PIN_T2 == 0 {
            e.treasury_yield_2y = crate::mathx::clamp_via_min_max(e.treasury_yield_2y + d2, 0.0, 12.0);
        }
        if pins_today & (PIN_CORPORATE | PIN_SPREAD | PIN_T10) == 0 {
            e.corporate_bond_yield += d10;
        }
    }

    /// For a restore. Refused with the cut off, where no engine writes it.
    pub fn set_stress_vix_max(&mut self, level: Option<f64>) -> Result<(), String> {
        match level {
            Some(_) if self.params.fed_stress_cut == 0.0 => Err(
                "this snapshot carries the central bank's stress level (fed_stress_vix_max), \
                 which only an engine with fed_stress_cut on writes, and this engine's model \
                 has it off. Restore it into the model it was taken from."
                    .to_string()),
            Some(v) => {
                self.stress_vix_max = v;
                Ok(())
            }
            None => {
                self.stress_vix_max = 0.0;
                Ok(())
            }
        }
    }

    /// The session's cap-weighted return so far, in per cent: the quantity
    /// the close's step reads as `market_day_return_pct`, by the same loop.
    fn session_return_pct(&self) -> f64 {
        let (mut acc, mut mcap) = (0.0, 0.0);
        for c in self.companies.iter() {
            if !c.is_public || c.is_bankrupt || c.stock.previous_close <= 0.0 {
                continue;
            }
            let d = (c.stock.price - c.stock.previous_close) / c.stock.previous_close * 100.0;
            acc += d * c.stock.market_cap;
            mcap += c.stock.market_cap;
        }
        if mcap > 0.0 { acc / mcap } else { 0.0 }
    }

    /// Each slot's GARCH variance as tonight's close is expected to leave
    /// it: the close's own update (`market::daily::close_day_with`) on the
    /// session's innovation so far (none when `empty`), with the rest of the
    /// session's innovation, sd `sqrt(variance * remaining)`, integrated by
    /// three-point Gauss-Hermite, which is exact for the quadratic update.
    fn projected_name_variances(&self, empty: bool, remaining: f64) -> Vec<f64> {
        let innovations = if empty { Vec::new() } else { self.daily_innovation_column() };
        let bases = self.sector_base_variances();
        let r3 = crate::mathx::sqrt(3.0);
        self.companies
            .iter()
            .enumerate()
            .map(|(i, c)| {
                let stock = &c.stock;
                let innovation = if empty {
                    0.0
                } else {
                    let daily_return = if stock.previous_close > 0.0 {
                        (stock.price - stock.previous_close) / stock.previous_close
                    } else {
                        0.0
                    };
                    match innovations.get(i) {
                        Some(&x) if x.is_finite() => x,
                        _ => daily_return,
                    }
                };
                let beta = crate::market::garch::garch_beta_for(&self.params, stock.market_cap);
                let base = bases.get(i).copied().unwrap_or(stock.garch_variance);
                let base_variance = if self.params.garch_vix_coupling == 0.0 {
                    base
                } else {
                    let ratio = self.economy.vix / self.vix_anchor;
                    let cpl = self.params.garch_vix_coupling;
                    base * (1.0 - cpl + crate::market::garch::vix_coupled_response(&self.params, cpl, ratio))
                };
                let one = |eps: f64| {
                    if self.params.garch_cascade_components >= 1.0 {
                        let mut cascade = stock.garch_cascade;
                        crate::market::garch::update_garch_cascade(
                            &self.params, beta, &mut cascade, eps, base_variance)
                    } else {
                        crate::market::garch::update_garch_variance_for(
                            &self.params, beta, stock.garch_variance, eps, base_variance)
                    }
                };
                let sd = crate::mathx::sqrt(crate::mathx::max(0.0, stock.garch_variance * remaining));
                if sd > 0.0 {
                    (one(innovation - r3 * sd) + 4.0 * one(innovation) + one(innovation + r3 * sd)) / 6.0
                } else {
                    one(innovation)
                }
            })
            .collect()
    }

    /// The (2-year, 10-year, corporate) yields, in per cent, and the VIX as
    /// published that tonight's step would give if the session ended now
    /// with the index at `session_pct`, the market factor's state `mv` and
    /// the names' GARCH variances `garch` (empty off the identity): the
    /// close's factor update on a copy, the index variance, the anchor's
    /// slow memory and the published VIX's stress memory as the close
    /// computes them, and the step's VIX and yields
    /// (`economy::daily::project_close_state`) with its draws at their means
    /// and no meeting. Reads the state and writes nothing.
    fn projected_close(
        &self,
        session_pct: f64,
        mv: MarketVarianceState,
        garch: &[f64],
        yields: bool,
    ) -> [f64; 4] {
        let mut mv = mv;
        let denominator = if self.params.market_vol_vix_excursion == 0.0 {
            self.vix_anchor
        } else {
            let implied = crate::market::index_var::vix_from_variance(
                self.params.vix_variance_premium,
                self.index_conditional_variance_terms_now().total(),
            );
            if implied > 0.0 { implied } else { self.vix_anchor }
        };
        let level = if self.params.market_vol_level_sigma == 0.0 {
            1.0
        } else {
            let phi = self.params.market_vol_level_persistence;
            let sigma = self.params.market_vol_level_sigma;
            let one_minus = 1.0 - phi * phi;
            let stationary_var = if one_minus > 0.0 { sigma * sigma / one_minus } else { 0.0 };
            crate::mathx::exp(self.market_vol_log_level - 0.25 * stationary_var)
        };
        // The cycle's volatility multiplier as the close will step and apply
        // it (`market_vol_cycle_ratio`): a branch at 0.0 that reads nothing,
        // and not applied on a forced close, as there.
        let (level, denominator, cycle_vix_scale) = if self.params.market_vol_cycle_ratio == 0.0 {
            (level, denominator, 1.0)
        } else {
            let l = self.market_vol_cycle_applied_log(self.market_vol_cycle_next_log());
            let scale = self.market_vol_cycle_vix_scale_at(Some(l));
            if self.vix_sets_variance_pending {
                (level, denominator, scale)
            } else {
                let m = crate::mathx::exp(l);
                (level * m * m, denominator * scale, scale)
            }
        };
        let _ = mv.close_day_scaled(&self.params, denominator, self.economy.vix, level);
        let index_variance = if self.params.vix_level_identity == 0.0 {
            0.0
        } else {
            let g = if garch.is_empty() { None } else { Some(garch) };
            self.index_conditional_variance_terms_with(mv.variance(), mv.prev_day_down(), g).total()
        };
        // The anchor's slow memory and the published VIX's stress memory, as
        // `advance_day_with` steps them before the economy's step.
        let (slow, stress) = if self.params.vix_anchor_memory != 0.0 && self.params.vix_level_identity != 0.0 {
            let mult = self.vix_level_multiplier();
            let implied = crate::market::index_var::vix_from_variance(
                self.params.vix_variance_premium, index_variance) * mult;
            let anchor = self.vix_anchor * mult * cycle_vix_scale;
            let anchor = if self.params.vix_anchor_centre != 0.0 {
                anchor * crate::mathx::exp(-self.params.vix_anchor_centre)
            } else {
                anchor
            };
            if implied > 0.0 && anchor > 0.0 {
                let h = self.params.vix_anchor_memory;
                let slow = (1.0 - h) * self.vix_anchor_slow + h * crate::mathx::log(implied / anchor);
                let stress = if self.params.vix_stress_premium == 0.0 {
                    self.vix_stress_memory
                } else if self.macro_pins_today & PIN_VIX != 0 {
                    0.0
                } else {
                    (1.0 - h) * self.vix_stress_memory + h * crate::mathx::log(implied / anchor)
                };
                (slow, stress)
            } else {
                let stress = if self.params.vix_stress_premium != 0.0
                    && self.macro_pins_today & PIN_VIX != 0
                {
                    0.0
                } else {
                    self.vix_stress_memory
                };
                (self.vix_anchor_slow, stress)
            }
        } else {
            (self.vix_anchor_slow, self.vix_stress_memory)
        };
        let request = DayAdvanceRequest {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: self.elapsed_days + 1,
            timestamp: (self.elapsed_days + 1) * 24 * 60,
        };
        let mut inputs = self.daily_inputs(&request, session_pct, index_variance, slow, cycle_vix_scale);
        // The fear memory as the close will advance it (`vix_fear_uptake`).
        if self.params.vix_fear_uptake != 0.0 {
            inputs.vix_fear = crate::economy::daily::advance_vix_fear(
                self.vix_fear, &inputs, self.economy.vix, self.params.vix_fear_half_life);
        }
        if !yields {
            // The VIX alone (`vix_intraday_live` without rate indices): the
            // same step's VIX half, with the yields left at 0.0.
            let vix = crate::economy::daily::vix_close(
                &self.economy, &inputs, 0.0, &mut crate::economy::daily::MeanDraws);
            return [0.0, 0.0, 0.0, self.published_vix_at(vix, stress)];
        }
        let next = crate::economy::daily::project_close_state(&self.economy, &inputs);
        [
            next.treasury_yield_2y,
            next.treasury_yield_10y,
            next.corporate_bond_yield,
            self.published_vix_at(next.vix, stress),
        ]
    }

    /// E[tonight's (2-year, 10-year, corporate) yields and published VIX |
    /// the session so far], with `remaining` of the session's variance still
    /// to come: the index at `session_pct` plus the rest of its move, sd the
    /// index's conditional sd times `sqrt(remaining)`, and the factor's day
    /// accumulation at `day_factor` plus the same node times the factor's
    /// own, integrated by the eight equally likely nodes of
    /// [`live_mark_nodes`]. `empty` projects a session with nothing in it.
    fn expected_close(
        &self,
        session_pct: f64,
        day_factor: f64,
        empty: bool,
        remaining: f64,
        yields: bool,
    ) -> [f64; 4] {
        let garch = if self.params.vix_level_identity != 0.0 {
            self.projected_name_variances(empty, remaining)
        } else {
            Vec::new()
        };
        let sd_factor = crate::mathx::sqrt(crate::mathx::max(0.0, self.market_vol.variance() * remaining));
        if !(sd_factor > 0.0) {
            return self.projected_close(
                session_pct, self.market_vol.with_day_factor(day_factor), &garch, yields);
        }
        let sd_index = crate::mathx::sqrt(crate::mathx::max(
            0.0, self.index_conditional_variance_terms_now().total() * remaining));
        let mut out = [0.0; 4];
        for &z in live_mark_nodes().iter() {
            let y = self.projected_close(
                session_pct + 100.0 * z * sd_index,
                self.market_vol.with_day_factor(day_factor + z * sd_factor),
                &garch,
                yields,
            );
            for (o, v) in out.iter_mut().zip(y.iter()) {
                *o += v / 8.0;
            }
        }
        out
    }

    /// Recompute the live marks on the state now standing, with `remaining`
    /// of the session's variance to come: the rate indices' two projections
    /// (`rate_intraday_live`, `rates`), the open's only when none is held
    /// (the open, or the first tick after a pin), and the live VIX
    /// (`vix_intraday_live`, `vix`). One projection of the session serves
    /// both. No draw.
    fn refresh_live_marks(&mut self, remaining: f64, rates: bool, vix: bool) {
        if !rates && !vix {
            return;
        }
        let now = self.expected_close(
            self.session_return_pct(), self.market_vol.day_factor(), false, remaining, rates);
        if rates {
            let base = match self.rate_live {
                Some(m) => [m[0], m[1], m[2], 0.0],
                None => self.expected_close(0.0, 0.0, true, 1.0, true),
            };
            self.rate_live = Some([base[0], base[1], base[2], now[0], now[1], now[2]]);
        }
        if vix {
            self.vix_live = Some(now[3]);
        }
    }

    /// Take `earnings_cycle_report_share` of a move `delta` in the earnings
    /// cycle's log level out of every traded name's fair-value level and
    /// keep it for the name's next report. See [`Engine::advance_macro_day`].
    fn withhold_cycle(&mut self, delta: f64) {
        let held = self.params.earnings_cycle_report_share * delta;
        if held == 0.0 {
            return;
        }
        if self.earnings_withheld.len() != self.companies.len() {
            self.earnings_withheld.resize(self.companies.len(), 0.0);
        }
        for (i, c) in self.companies.iter_mut().enumerate() {
            if c.is_bankrupt || !c.is_public || c.stock.mispricing_s.is_none() {
                continue;
            }
            let v = c.stock.fair_value_offset.unwrap_or(0.0);
            c.stock.fair_value_offset = Some(v - held);
            self.earnings_withheld[i] += held;
        }
    }

    /// Every name's fair value as the tick would read it on the state now
    /// standing, at its current price and day, for
    /// [`Engine::reprice_to_published_macro`]. `None`, computing nothing,
    /// with `macro_publication_repricing` off. NaN for a name the re-mark
    /// leaves alone: bankrupt, private, or not yet traded (no `s`), whose
    /// first tick adopts its premium lazily and so moves nothing anyway.
    pub fn published_macro_marks(&self) -> Option<Vec<f64>> {
        if self.params.macro_publication_repricing == 0.0 {
            return None;
        }
        Some(
            self.companies
                .iter()
                .map(|c| {
                    if c.is_bankrupt || !c.is_public || c.stock.mispricing_s.is_none()
                        || !(c.stock.price > 0.0)
                    {
                        f64::NAN
                    } else {
                        crate::market::tick::tick_fair_value(
                            &self.params, &self.economy, self.nominal_output_base,
                            self.elapsed_days, c, c.stock.price)
                    }
                })
                .collect(),
        )
    }

    /// Every company's fair value as the next tick starts from it, in roster
    /// order: [`crate::market::tick::tick_fair_value`] on the state now
    /// standing, at the name's current price and the current day. NaN for a
    /// bankrupt or private name, which the tick does not value.
    pub fn fair_values(&self) -> Vec<f64> {
        self.companies
            .iter()
            .map(|c| {
                if c.is_bankrupt || !c.is_public {
                    f64::NAN
                } else {
                    crate::market::tick::tick_fair_value(
                        &self.params, &self.economy, self.nominal_output_base,
                        self.elapsed_days, c, c.stock.price)
                }
            })
            .collect()
    }

    /// THE PRICE TAKES A MACRO DECISION WHEN IT IS PUBLISHED
    /// (`macro_publication_repricing`).
    ///
    /// The close's macro step -- the economy, the cycle, the central bank's
    /// meeting, the corporate yield it re-anchors -- is readable from the
    /// moment it ends (`macro_fields`, `macro_state`, a World's trace), but
    /// fair value only reached the price at the next session's first tick.
    /// Everything in between, the evening and the opening step of any
    /// harness, traded at the price from before the decision. Here each
    /// name that has traded is re-marked as the step ends to the price its
    /// premium over fair value implies on the published state: `price =
    /// (last / fv_before) * fv_after(price)`, both fair values computed as
    /// the tick computes them (`market::tick::tick_fair_value`) on the same
    /// day, `fv_after` at the new price because the buyback term reads it.
    /// The name's mispricing `s` is left as it was. The next tick therefore
    /// starts on the model price the published state implies, and the move
    /// sits between the day's last print and
    /// the next open, which is where a decision announced after the close
    /// lands on a real tape. `pin_macro` re-marks the same way, so a
    /// scenario's written state is priced the moment it is readable too.
    ///
    /// No draw, no new state: the price is the state it writes, with the
    /// market cap and the day's high and low beside it (the latter are
    /// reset at the next open when the re-mark comes after a close).
    /// `marks` is [`Engine::published_macro_marks`] taken before the change;
    /// `None` does nothing.
    pub fn reprice_to_published_macro(&mut self, marks: Option<Vec<f64>>) {
        let Some(before) = marks else {
            return;
        };
        let p = &self.params;
        let economy = &self.economy;
        let base = self.nominal_output_base;
        let day = self.elapsed_days;
        let pending = &mut self.repriced_pending;
        for (slot, (c, &fv0)) in self.companies.iter_mut().zip(before.iter()).enumerate() {
            if !(fv0 > 0.0) || c.is_bankrupt || !c.is_public {
                continue;
            }
            let last = c.stock.price;
            let fv1 = crate::market::tick::tick_fair_value(p, economy, base, day, c, last);
            if !(fv1 > 0.0) || fv1 == fv0 {
                continue;
            }
            // The price the name's mispricing implies on the published
            // state: `price = (last / fv0) * fv(price)`. Fair value reads
            // the price itself through the buyback term (the yield is
            // earnings over price), so the first step, `last * fv1 / fv0`,
            // is exact only with `buyback_payout_share` at 0.0; with it on,
            // a re-mark that stopped there leaves the next tick to raise
            // fair value by the buyback yield the lower price implies, and
            // measured on pt-v20 with its leading dials that handed back 14
            // bp of a 79 bp hike in the first 65 minutes. The fixed point
            // is a contraction (the term's elasticity is the buyback yield
            // times the years elapsed, well under one), so a few steps
            // settle it; the loop stops when a step moves nothing, or at
            // sixteen, and takes no draw. Under `buyback_accrual` fair value
            // does not read the price, so the first step is exact and the
            // loop stops at its first comparison.
            let ratio = last / fv0;
            let clamp = |x: f64| crate::mathx::min(crate::mathx::max(x, 0.01), p.price_hard_cap);
            let mut price = clamp(ratio * fv1);
            if p.buyback_payout_share != 0.0 {
                for _ in 0..16 {
                    let fv = crate::market::tick::tick_fair_value(p, economy, base, day, c, price);
                    let next = clamp(ratio * fv);
                    if next == price {
                        break;
                    }
                    price = next;
                }
            }
            let stock = &mut c.stock;
            stock.price = price;
            stock.high = crate::mathx::max(stock.high, price);
            stock.low = crate::mathx::min(stock.low, price);
            stock.market_cap = price * stock.shares_outstanding;
            // For the tape: the next print's `repriced` books this move, so
            // the print decomposition still sums to the move from the last
            // print. Recording only; the price above is the whole effect.
            if let Some(v) = pending.get_mut(slot) {
                *v += crate::mathx::log(price / last);
            }
        }
    }

    // ── State access ──────────────────────────────────────────────────────

    pub fn economy(&self) -> &EconomyState {
        &self.economy
    }

    /// Mutable access to the macro state, for driving a scenario.
    ///
    /// A scenario is a PATH, not a feature: a rate shock is `federal_funds_rate`
    /// stepping from 2.5 to 5 over N days, supplied by whoever is running the
    /// study. Expressing that needs the embedder to be able to write the macro
    /// state between days, which is what this is for.
    ///
    /// Writing it MID-SESSION is legal and is not checked, because the engine
    /// cannot tell a deliberate intraday shock from a mistake. It is worth
    /// knowing that the tick reads these fields as it goes, so a write between
    /// two ticks of the same session takes effect immediately and for the rest
    /// of that session — which is either exactly what was wanted or a
    /// surprise, depending on who wrote it.
    pub fn economy_mut(&mut self) -> &mut EconomyState {
        &mut self.economy
    }
    pub fn central_bank(&self) -> &CentralBankState {
        &self.central_bank
    }
    /// Mutable access to the central bank, for restoring a snapshot.
    ///
    /// Exists for the same reason as [`Engine::economy_mut`]: now that the
    /// macro chain advances between days, a fork that did not carry the
    /// bank's meeting calendar would hold a meeting its parent never held.
    pub fn central_bank_mut(&mut self) -> &mut CentralBankState {
        &mut self.central_bank
    }
    pub fn companies(&self) -> &[TickCompany] {
        &self.companies
    }
    pub fn companies_mut(&mut self) -> &mut [TickCompany] {
        &mut self.companies
    }
    pub fn len(&self) -> usize {
        self.companies.len()
    }
    pub fn is_empty(&self) -> bool {
        self.companies.is_empty()
    }

    /// Install rate instruments, marked at the curve the economy holds now.
    ///
    /// Call once, after construction and before the first open: an engine
    /// built with a settled opening has already run its burn-in by then, so
    /// the instruments are marked at the curve the run starts from. They
    /// never draw and never write the economy, so installing them changes no
    /// equity price. See `crate::rates`.
    pub fn set_rate_instruments(&mut self, instruments: Vec<crate::rates::RateInstrument>) {
        self.rates = crate::rates::RateBook::new(instruments, &self.economy);
    }

    pub fn rates(&self) -> &crate::rates::RateBook {
        &self.rates
    }

    /// A caller has written the corporate yield: re-mark the corporate
    /// index's spread to it, even if the value did not change. Nothing on an
    /// engine without rate instruments. See `crate::rates`.
    pub fn remark_credit_spread(&mut self) {
        if !self.rates.is_empty() {
            self.rates.remark_credit_spread(&self.economy);
        }
    }

    /// For a restore, which writes the book back whole.
    pub fn rates_mut(&mut self) -> &mut crate::rates::RateBook {
        &mut self.rates
    }

    /// Equities plus rate instruments: the width of every per-instrument
    /// surface. Equal to [`Engine::len`] on an engine without rate
    /// instruments.
    pub fn instrument_count(&self) -> usize {
        self.companies.len() + self.rates.len()
    }

    /// One field for the rate instruments, positional against
    /// `rates().instruments`, in the units [`Engine::column`] uses.
    ///
    /// NaN where the field does not exist for an index (`garch_variance`,
    /// `beta`, `last_daily_return`). The mispricing fields are zero: an
    /// index level is its own fair value, and the mispricing process never
    /// runs on it.
    pub fn rate_column(&self, field: PriceField) -> Vec<f64> {
        self.rates
            .instruments
            .iter()
            .map(|i| match field {
                PriceField::Price => i.price,
                PriceField::PreviousClose => i.previous_close,
                PriceField::Open => i.open,
                PriceField::High => i.high,
                PriceField::Low => i.low,
                PriceField::Volume => i.volume,
                PriceField::MarketCap => i.market_cap(),
                PriceField::MispricingS => 0.0,
                PriceField::MakerInventory => i.maker_inventory,
                PriceField::GarchVariance => f64::NAN,
                PriceField::PreviousTickPrice => f64::NAN,
                PriceField::MispricingSPrevClose => 0.0,
                PriceField::MispricingMomentum => 0.0,
                PriceField::LastDailyReturn => f64::NAN,
                PriceField::AvgVolume => i.avg_volume,
                PriceField::Beta => f64::NAN,
                PriceField::ShortInterest => 0.0,
                PriceField::FloatShares => i.units_outstanding,
            })
            .collect()
    }

    // ── Roster mutation ───────────────────────────────────────────────────
    //
    // A listed universe is not static: companies IPO in, go bankrupt, and are
    // acquired. An engine that could not represent that would force the
    // embedder to choose between rebuilding — which resets the generator and
    // destroys reproducibility — and pretending, which silently attaches
    // results to the wrong companies because every column is positional.
    //
    // # What these guarantee, and what they cannot
    //
    // They do NOT keep the rest of the market unchanged. They cannot: the tick
    // draws per company, so changing `n` shifts every subsequent draw and the
    // whole market moves from that point. That is a property of the model, not
    // a limitation of the implementation, and `Engine::new` already says so
    // about roster size.
    //
    // What they DO guarantee is reproducibility, which is the property that
    // actually matters: the generator carries forward across the change, so
    // one seed plus the same sequence of roster edits, applied at the same
    // ticks, reproduces the same market exactly. Replay works; invariance was
    // never on offer.

    /// Append a company. Returns its index.
    ///
    /// Appends rather than inserting, because index order is the draw order:
    /// inserting into the middle would renumber every company after it and
    /// change which draws they receive. An embedder that needs a particular
    /// ordering must establish it before the first tick.
    ///
    /// # The five per-slot arrays it carries
    ///
    /// `attribution`, `tick_components`, `tick_fundamental`, `tick_anchor`
    /// and `volume_idio` are all positional against `companies`, so each one
    /// gains a slot here. The new company's `volume_idio` slot is 0.0, which
    /// is what `Engine::with_params` gives every company at construction: a
    /// listing starts with no idiosyncratic volume state.
    ///
    /// `volume_idio` was left out until 2026-09-02 (issue #148), and the
    /// consequence reached two things. `update_volume_idio` draws once per
    /// SLOT, so the draw count per day was the array's stale width rather
    /// than the roster's; from this mutation onward it is the roster's
    /// width. And the tick reads each company's state at its own index, so
    /// under a preset with a non-zero `volume_idio_sigma` every company past
    /// the new one read a state that was not its own.
    pub fn add_company(&mut self, company: TickCompany) -> usize {
        // The index's level before the listing, which the divisor keeps
        // (`index_level_listed`); `None`, and nothing done, with it off.
        let index_before = self.index_before_change();
        self.base_fundamentals
            .push([company.eps, company.book_value_per_share, company.revenue_growth]);
        self.base_shares.push(company.stock.shares_outstanding);
        self.companies.push(company);
        self.attribution.push([0.0; crate::market::factors::COMPONENT_COUNT]);
        self.noise_parts.push([0.0; 3]);
        self.noise_own_scale2.push(0.0);
        if !self.innovation_day.is_empty() {
            self.innovation_day.push(0.0);
        }
        self.tick_components.push([0.0; crate::market::factors::TICK_COMPONENT_COUNT]);
        self.tick_fundamental.push(f64::NAN);
        self.tick_anchor.push(f64::NAN);
        self.volume_idio.push(0.0);
        self.jump_move.push(0.0);
        // The book's per-slot row, only once the book has been used: an
        // engine that never saw an agent's order keeps its empty table.
        if !self.book.taken.is_empty() {
            self.book.taken.push([0.0; crate::agent_book::TAKEN_WIDTH]);
        }
        // The metaorder memory's row likewise, only once it holds any.
        if !self.book.memory.is_empty() {
            self.book.memory.push([0.0; crate::agent_book::MEMORY_WIDTH]);
        }
        // The per-name jump excitation follows the roster for the reason
        // `volume_idio` above does, and it was left out for the same
        // reason: it landed after this function was written. A name that
        // joins has never jumped, so its excitation is 0.0.
        self.jump_excitation.push(0.0);
        // The earnings calendar's per-name columns: a name that joins has
        // realised no report and holds back nothing.
        self.earnings_moves.push(0.0);
        self.earnings_withheld.push(0.0);
        // The idiosyncratic variance state likewise: a name that joins is
        // unseeded (reads 1.0) and has no jump pending.
        for v in [
            &mut self.idio_variance,
            &mut self.idio_jump_pending,
            &mut self.idio_jump_var_pending,
            &mut self.idio_jump_today,
            &mut self.idio_jump_var_today,
        ] {
            v.push(0.0);
        }
        // The print decomposition, on the same argument as everything above
        // it: these are per-SLOT columns, and a roster edit that grew
        // `companies` without growing them would leave the new name reading
        // as a company that never moved.
        self.tick_shock.push(0.0);
        self.tick_absorbed.push(0.0);
        self.tick_clamp.push(0.0);
        self.tick_repriced.push(0.0);
        // A name that joins has had nothing written to its price.
        self.repriced_pending.push(0.0);
        // A name that joins has no earlier close than its listing price.
        let listing = self.companies.last().map_or(f64::NAN, |c| c.stock.price);
        self.last_closes.push(listing);
        self.prior_closes.push(listing);
        // Only where the arm is running. Empty means no arm, and pushing
        // into an empty pair would turn that into a one-row column.
        if !self.tick_unbounded_print.is_empty() {
            self.tick_unbounded_print.push(f64::NAN);
            self.tick_liquidity_share.push(0.0);
        }
        self.index_rebase(index_before);
        // A host's write between sessions moves the night's path at once
        // (`night_session_steps`); nothing otherwise.
        self.futures_retarget();
        self.companies.len() - 1
    }

    /// Remove the company at `index`, returning it.
    ///
    /// `Vec::remove`, so the tail shifts down by one and keeps its relative
    /// order. `swap_remove` would be cheaper and is wrong: it moves the last
    /// company into the hole, silently reordering the roster, and roster order
    /// is contractual.
    ///
    /// Returns `None` for an out-of-range index rather than panicking — a
    /// removal racing a bankruptcy is an embedder bug worth reporting, not a
    /// reason to abort the module and take the session with it.
    ///
    /// # The five per-slot arrays it carries
    ///
    /// The same five [`Engine::add_company`] appends to, each losing the slot
    /// at `index` so the tail shifts down with the roster it is positional
    /// against. `volume_idio` was left out until 2026-09-02 (issue #148),
    /// which left the survivors reading their old neighbours' states and
    /// held the per-day draw count at the pre-removal width; from this
    /// mutation onward the count is the roster's width.
    pub fn remove_company(&mut self, index: usize) -> Option<TickCompany> {
        if index >= self.companies.len() {
            return None;
        }
        // The index's level before the delisting, which the divisor keeps
        // (`index_level_listed`).
        let index_before = self.index_before_change();
        if index < self.tick_components.len() {
            self.tick_components.remove(index);
        }
        if index < self.tick_fundamental.len() {
            self.tick_fundamental.remove(index);
        }
        if index < self.tick_anchor.len() {
            self.tick_anchor.remove(index);
        }
        if index < self.attribution.len() {
            self.attribution.remove(index);
        }
        if index < self.noise_parts.len() {
            self.noise_parts.remove(index);
        }
        if index < self.innovation_day.len() {
            self.innovation_day.remove(index);
        }
        if index < self.noise_own_scale2.len() {
            self.noise_own_scale2.remove(index);
        }
        if index < self.volume_idio.len() {
            self.volume_idio.remove(index);
        }
        if index < self.jump_move.len() {
            self.jump_move.remove(index);
        }
        // A name that leaves takes its book with it: its waiting orders are
        // cancelled and its unapplied flow is dropped, since there is no
        // market left for either to reach.
        if index < self.book.taken.len() {
            self.book.taken.remove(index);
        }
        if index < self.book.memory.len() {
            self.book.memory.remove(index);
        }
        let leaving = self.companies[index].ticker.clone();
        self.book.orders.retain(|o| o.ticker != leaving);
        self.book.flow.retain(|(_, t, _, _)| *t != leaving);
        // `Vec::remove` for the reason the line above uses it: the tail
        // shifts down and keeps its relative order, so every remaining
        // name keeps its own excitation.
        if index < self.jump_excitation.len() {
            self.jump_excitation.remove(index);
        }
        if index < self.earnings_moves.len() {
            self.earnings_moves.remove(index);
        }
        if index < self.earnings_withheld.len() {
            self.earnings_withheld.remove(index);
        }
        for v in [
            &mut self.idio_variance,
            &mut self.idio_jump_pending,
            &mut self.idio_jump_var_pending,
            &mut self.idio_jump_today,
            &mut self.idio_jump_var_today,
        ] {
            if index < v.len() {
                v.remove(index);
            }
        }
        // Removed rather than left behind, because the tail shifts down by
        // one and a column that did not shift with it would report every
        // remaining company's move against its neighbour's slot.
        if index < self.tick_shock.len() {
            self.tick_shock.remove(index);
        }
        if index < self.tick_absorbed.len() {
            self.tick_absorbed.remove(index);
        }
        if index < self.tick_clamp.len() {
            self.tick_clamp.remove(index);
        }
        if index < self.tick_repriced.len() {
            self.tick_repriced.remove(index);
        }
        if index < self.repriced_pending.len() {
            self.repriced_pending.remove(index);
        }
        if index < self.last_closes.len() {
            self.last_closes.remove(index);
        }
        if index < self.prior_closes.len() {
            self.prior_closes.remove(index);
        }
        if index < self.tick_unbounded_print.len() {
            self.tick_unbounded_print.remove(index);
        }
        if index < self.tick_liquidity_share.len() {
            self.tick_liquidity_share.remove(index);
        }
        if index < self.base_fundamentals.len() {
            self.base_fundamentals.remove(index);
        }
        if index < self.base_shares.len() {
            self.base_shares.remove(index);
        }
        let leaving = self.companies.remove(index);
        self.index_rebase(index_before);
        // A host's write between sessions moves the night's path at once
        // (`night_session_steps`); nothing otherwise.
        self.futures_retarget();
        Some(leaving)
    }

    /// Find a company's index by id.
    ///
    /// Linear, because the roster is ~100 names and a map would be a second
    /// structure to keep in step with the ordering that actually matters.
    pub fn index_of(&self, id: &str) -> Option<usize> {
        self.companies.iter().position(|c| c.id == id)
    }

    /// Build the executable order book for one instrument, right now.
    ///
    /// # This is the book the tick itself settles through
    ///
    /// Not a display copy or an approximation of one. The same
    /// `build_live_book` call the price settlement uses, on the same state, so
    /// the depth a caller reads is the depth they would actually trade
    /// against. That is what makes slippage emergent: a large order pays worse
    /// prices because it consumed real levels, not because a coefficient said
    /// large orders cost more.
    ///
    /// Rebuilt per call rather than persisted, which is correct rather than
    /// merely convenient: a market maker re-quotes every tick anyway, so the
    /// book is a pure function of fair value, spread and resting orders. That
    /// keeps the tick pure and replay deterministic for free.
    ///
    /// Returns `None` for an out-of-range index.
    ///
    /// With any of the agent-facing book's dials on, or once an agent has
    /// used the book, this is that book instead
    /// (`crate::agent_book::agent_book`): the ladder less what agents have
    /// taken, the latent depth behind it, and every agent's resting orders.
    /// Otherwise it is the maker's ladder, built exactly as it always was.
    pub fn book_for(&self, index: usize) -> Option<crate::order_book::OrderBook> {
        // Listed contracts sit after the rate instruments, in listing order
        // (`futures_index_listed`); `Engine::contract_book` reads one by
        // symbol.
        if index >= self.instrument_count() {
            return self.contract_book_at(index - self.instrument_count());
        }
        // Rate instruments sit after the equities, so their index is the
        // equity count plus their place in the rate book.
        if index >= self.companies.len() {
            let j = index - self.companies.len();
            let inst = self.rates.instruments.get(j)?;
            // Around the live mark during a session under
            // `rate_intraday_live`, which is what the minute's print quotes.
            return Some(match self.rate_live_curve() {
                Some(c) => inst.book_at(inst.level_at(c.dy(inst.spec.point)), self.economy.vix),
                None => inst.book(self.economy.vix),
            });
        }
        if self.agent_view() {
            return self.agent_book_at(index, None);
        }
        let company = self.companies.get(index)?;
        Some(crate::microstructure::build_live_book(
            &company.micro_view(company.stock.price),
            &crate::microstructure::LiveBookOptions {
                spread_size_smoothness: 0.0,
                spread_size_exponent: crate::microstructure::SPREAD_SIZE_EXPONENT,
                vix: self.economy.vix,
                ..Default::default()
            },
        ))
    }

    /// Company ids, in roster order. The mapping every column is positional against.
    pub fn ids(&self) -> Vec<String> {
        self.companies.iter().map(|c| c.id.clone()).collect()
    }

    /// Company sectors, in roster order. Positional against [`Engine::ids`].
    ///
    /// Exists for the information-transfer channel: news naming a company
    /// has to be resolvable to that company's sector before its peers can be
    /// found, and a caller writing `News(ticker="AWS", price_impact=0.05)`
    /// should not have to restate the sector the roster already knows.
    pub fn sectors(&self) -> Vec<String> {
        self.companies.iter().map(|c| c.sector.clone()).collect()
    }

    /// Columnar read of one field, for the FFI boundary.
    ///
    /// A single contiguous `f64` buffer crosses a WASM boundary as one view;
    /// 108 marshalled objects do not. The `Vec` is built on demand rather than
    /// maintained, because the embedder reads far less often than the tick
    /// writes.
    /// Write one column back onto the roster.
    ///
    /// The inverse of [`Engine::column`], and the second half of a
    /// constant-time checkpoint: a saved market is its columns plus the
    /// generator position, and until now only the reading direction existed.
    ///
    /// # The match is exhaustive on purpose
    ///
    /// Every branch is spelled out with no wildcard, so adding a variant to
    /// [`PriceField`] fails to COMPILE until it is handled here. That is the
    /// difference between this and the "restore everything" method the
    /// docstring on `set_rng_state` warns against: the compiler keeps the two
    /// directions in step rather than a reviewer remembering to.
    ///
    /// # NaN means absent, matching the read side
    ///
    /// The optional fields round-trip through NaN in both directions. Writing
    /// a NaN clears the field rather than storing a NaN, so a column read out
    /// and written back reproduces the original `Option` exactly -- including
    /// the difference between "no mispricing yet" and "a mispricing of zero",
    /// which are different markets.
    ///
    /// Returns an error when the slice length does not match the roster.
    /// Silently writing a prefix would restore a market that was correct for
    /// the first N companies and stale for the rest.
    /// Write the three fair-value inputs back onto the roster.
    ///
    /// [`Engine::set_column`] covers the STOCK fields -- price, variance,
    /// inventory, the mispricing carry. These three live on the company
    /// rather than the stock, and until now nothing could set them after
    /// construction. That made them frozen for the life of an engine, which
    /// is fine for a batch sweep and wrong for an embedder whose companies
    /// report earnings: fair value is `eps * target_pe`, so a stale `eps` is
    /// a company valued on the fundamentals it had on day one, for ever.
    ///
    /// NaN clears the field, matching `set_column` and the columnar contract
    /// everywhere else -- zero is a real EPS (a company that broke exactly
    /// even) and cannot double as the absent marker. `None` here is not
    /// "unknown", it is what the valuation reads as "no earnings path", and
    /// the two must stay distinguishable.
    ///
    /// Consumes no draws, so it cannot move the generator: a caller may sync
    /// as often as it likes without changing the market's trajectory.
    ///
    /// Lengths are checked against the roster rather than truncated. Writing
    /// a prefix would leave a market correct for its first companies and
    /// stale for the rest -- the failure this method exists to end.
    pub fn set_fundamentals(
        &mut self,
        eps: &[f64],
        book_value_per_share: &[f64],
        revenue_growth: &[f64],
    ) -> Result<(), String> {
        let n = self.companies.len();
        for (name, len) in [
            ("eps", eps.len()),
            ("book_value_per_share", book_value_per_share.len()),
            ("revenue_growth", revenue_growth.len()),
        ] {
            if len != n {
                return Err(format!(
                    "{name} has {len} values for {n} companies"
                ));
            }
        }
        fn optional(v: f64) -> Option<f64> {
            if v.is_nan() {
                None
            } else {
                Some(v)
            }
        }
        for (i, company) in self.companies.iter_mut().enumerate() {
            company.eps = optional(eps[i]);
            company.book_value_per_share = optional(book_value_per_share[i]);
            company.revenue_growth = optional(revenue_growth[i]);
        }
        Ok(())
    }

    /// Write the two trading-status flags back onto the roster.
    ///
    /// The last per-company state an embedder could not update. A company that
    /// goes bankrupt or is taken private in the game kept trading here: the
    /// tick skips a company only when it reads `is_bankrupt || !is_public`
    /// (`market/tick.rs`), and the index excludes bankrupt constituents
    /// (`market/index_value.rs`). Without a setter, neither could ever become
    /// true after construction, so a failed company went on printing prices
    /// and went on counting toward the index.
    ///
    /// Narrower than [`Engine::set_fundamentals`] because it does not
    /// compound -- a stale `eps` misprices a company a little more every
    /// quarter, while a stale flag is wrong from the moment it changes and no
    /// worse afterwards. Both are wrong, so both are settable.
    ///
    /// `&[bool]` rather than the f64 columnar convention used elsewhere,
    /// deliberately: there is no "absent" flag for NaN to mean, and a boolean
    /// carried as a float invites a caller to pass 0.5.
    ///
    /// Consumes no draws.
    pub fn set_status(
        &mut self,
        is_bankrupt: &[bool],
        is_public: &[bool],
    ) -> Result<(), String> {
        let n = self.companies.len();
        for (name, len) in [
            ("is_bankrupt", is_bankrupt.len()),
            ("is_public", is_public.len()),
        ] {
            if len != n {
                return Err(format!("{name} has {len} values for {n} companies"));
            }
        }
        // A bankruptcy or a name taken private leaves the index at its last
        // price, which the divisor keeps (`index_level_listed`).
        let index_before = self.index_before_change();
        for (i, company) in self.companies.iter_mut().enumerate() {
            company.is_bankrupt = is_bankrupt[i];
            company.is_public = is_public[i];
        }
        self.index_rebase(index_before);
        // A host's write between sessions moves the night's path at once
        // (`night_session_steps`); nothing otherwise.
        self.futures_retarget();
        Ok(())
    }

    /// The two trading-status flags, in roster order.
    pub fn status(&self) -> (Vec<bool>, Vec<bool>) {
        (
            self.companies.iter().map(|c| c.is_bankrupt).collect(),
            self.companies.iter().map(|c| c.is_public).collect(),
        )
    }

    /// The three fair-value inputs, NaN where absent.
    ///
    /// The read side of [`Engine::set_fundamentals`], so a caller can check
    /// what the engine is actually valuing on rather than assuming its own
    /// copy is what arrived.
    pub fn fundamentals(&self) -> (Vec<f64>, Vec<f64>, Vec<f64>) {
        let nan = f64::NAN;
        (
            self.companies.iter().map(|c| c.eps.unwrap_or(nan)).collect(),
            self.companies
                .iter()
                .map(|c| c.book_value_per_share.unwrap_or(nan))
                .collect(),
            self.companies
                .iter()
                .map(|c| c.revenue_growth.unwrap_or(nan))
                .collect(),
        )
    }

    /// Whether any company's fair-value inputs differ from the ones it was
    /// built or listed with, to the bit. What decides whether a snapshot
    /// carries them and whether the state hash covers them, so an engine
    /// `set_fundamentals` never moved snapshots and hashes as it did before
    /// they were carried.
    pub fn fundamentals_changed(&self) -> bool {
        fn bits(v: Option<f64>) -> Option<u64> {
            v.map(f64::to_bits)
        }
        self.companies.len() != self.base_fundamentals.len()
            || self.companies.iter().zip(&self.base_fundamentals).any(|(c, b)| {
                bits(c.eps) != bits(b[0])
                    || bits(c.book_value_per_share) != bits(b[1])
                    || bits(c.revenue_growth) != bits(b[2])
            })
    }

    /// Put every company's fair-value inputs back to the ones it was built
    /// or listed with. What a restore does when the snapshot carries none:
    /// such a snapshot was taken on an engine whose inputs had not moved.
    pub fn reset_fundamentals(&mut self) {
        for (c, b) in self.companies.iter_mut().zip(&self.base_fundamentals) {
            c.eps = b[0];
            c.book_value_per_share = b[1];
            c.revenue_growth = b[2];
        }
    }

    /// Write each company's share count, for a host whose companies buy
    /// back stock or issue it (#274).
    ///
    /// Without it the count was the one each company was built or listed
    /// with for the life of the engine, and every tick sets the market cap
    /// to `price * shares_outstanding`, so a host that wrote `MarketCap`
    /// saw it overwritten at the next print. Everything the engine weights
    /// by its own capitalisation -- the market factor's loadings, the
    /// roster beta normalisation, the cap-weighted market P/E, the index
    /// variance -- read the opening counts.
    ///
    /// Each market cap follows at once, at the price standing. The listed
    /// index (`index_level_listed`) keeps its level: the divisor is reset
    /// across the write, as it is across a listing, a delisting or a change
    /// of status, so a buyback is not a fall in the index. The float is the
    /// host's to keep in step (`set_column(FloatShares, ..)`); it is not
    /// moved here.
    ///
    /// Every count must be finite and above 0, and the length must be the
    /// roster's, on the same rule as [`Engine::set_fundamentals`]. A
    /// refused write changes nothing. Consumes no draws.
    pub fn set_shares_outstanding(&mut self, shares: &[f64]) -> Result<(), String> {
        let n = self.companies.len();
        if shares.len() != n {
            return Err(format!("shares_outstanding has {} values for {n} companies", shares.len()));
        }
        if let Some((i, v)) = shares.iter().enumerate().find(|(_, v)| !(v.is_finite() && **v > 0.0)) {
            return Err(format!(
                "shares_outstanding[{i}] ({}) is {v}; a share count is finite and above 0",
                self.companies[i].id
            ));
        }
        let index_before = self.index_before_change();
        for (company, &count) in self.companies.iter_mut().zip(shares) {
            company.stock.shares_outstanding = count;
            company.stock.market_cap = company.stock.price * count;
        }
        self.index_rebase(index_before);
        // A host's write between sessions moves the night's path at once
        // (`night_session_steps`); nothing otherwise.
        self.futures_retarget();
        Ok(())
    }

    /// Each company's share count, in roster order.
    pub fn shares_outstanding(&self) -> Vec<f64> {
        self.companies.iter().map(|c| c.stock.shares_outstanding).collect()
    }

    /// Whether any company's share count differs from the one it was built
    /// or listed with, to the bit. What decides whether a snapshot carries
    /// the counts and whether the state hash covers them, so an engine
    /// `set_shares_outstanding` never moved snapshots and hashes as it did
    /// before they were carried.
    pub fn shares_outstanding_changed(&self) -> bool {
        self.companies.len() != self.base_shares.len()
            || self
                .companies
                .iter()
                .zip(&self.base_shares)
                .any(|(c, b)| c.stock.shares_outstanding.to_bits() != b.to_bits())
    }

    /// Put every company's share count back to the one it was built or
    /// listed with. What a restore does when the snapshot carries none.
    /// Moves nothing else: the market caps and the index divisor a restore
    /// writes come from the snapshot.
    pub fn reset_shares_outstanding(&mut self) {
        for (c, b) in self.companies.iter_mut().zip(&self.base_shares) {
            c.stock.shares_outstanding = *b;
        }
    }

    /// Write the share counts a snapshot carries, raw: no market cap is
    /// recomputed and the index divisor is not reset, because a restore
    /// writes both from the snapshot. The length must be the roster's.
    pub fn restore_shares_outstanding(&mut self, shares: &[f64]) -> Result<(), String> {
        if shares.len() != self.companies.len() {
            return Err(format!(
                "this snapshot carries {} share counts and the roster holds {} \
                 companies. They are positional against the roster, so this \
                 restore is refused rather than padded or truncated.",
                shares.len(),
                self.companies.len()
            ));
        }
        if let Some((i, v)) = shares.iter().enumerate().find(|(_, v)| !(v.is_finite() && **v > 0.0)) {
            return Err(format!(
                "this snapshot's shares_outstanding[{i}] is {v}; a share count is \
                 finite and above 0"
            ));
        }
        for (c, &v) in self.companies.iter_mut().zip(shares) {
            c.stock.shares_outstanding = v;
        }
        Ok(())
    }

    pub fn set_column(&mut self, field: PriceField, values: &[f64]) -> Result<(), String> {
        if values.len() != self.companies.len() {
            return Err(format!(
                "expected {} values for {} companies, got {}",
                self.companies.len(),
                self.companies.len(),
                values.len()
            ));
        }
        // NaN in, `None` out. Zero is a real value for every optional field
        // here, so it cannot double as the absent marker.
        fn optional(v: f64) -> Option<f64> {
            if v.is_nan() {
                None
            } else {
                Some(v)
            }
        }
        for (company, &value) in self.companies.iter_mut().zip(values) {
            let stock = &mut company.stock;
            match field {
                PriceField::Price => stock.price = value,
                PriceField::PreviousClose => stock.previous_close = value,
                PriceField::Open => stock.open = value,
                PriceField::High => stock.high = value,
                PriceField::Low => stock.low = value,
                PriceField::Volume => stock.volume = value,
                PriceField::MarketCap => stock.market_cap = value,
                PriceField::MispricingS => stock.mispricing_s = optional(value),
                PriceField::MakerInventory => stock.maker_inventory = optional(value),
                PriceField::GarchVariance => stock.garch_variance = value,
                PriceField::PreviousTickPrice => {
                    stock.previous_tick_price = optional(value)
                }
                PriceField::MispricingSPrevClose => {
                    stock.mispricing_s_prev_close = optional(value)
                }
                PriceField::MispricingMomentum => {
                    stock.mispricing_momentum = optional(value)
                }
                PriceField::LastDailyReturn => stock.last_daily_return = optional(value),
                PriceField::AvgVolume => stock.avg_volume = value,
                PriceField::Beta => stock.beta = optional(value),
                PriceField::ShortInterest => stock.short_interest = value,
                PriceField::FloatShares => stock.float = value,
            }
        }
        Ok(())
    }

    /// One field for every company, in roster order. A field a company does
    /// not have yet (a return before its first close, say) is NaN.
    pub fn column(&self, field: PriceField) -> Vec<f64> {
        self.companies
            .iter()
            .map(|c| match field {
                PriceField::Price => c.stock.price,
                PriceField::PreviousClose => c.stock.previous_close,
                PriceField::Open => c.stock.open,
                PriceField::High => c.stock.high,
                PriceField::Low => c.stock.low,
                PriceField::Volume => c.stock.volume,
                PriceField::MarketCap => c.stock.market_cap,
                PriceField::MispricingS => c.stock.mispricing_s.unwrap_or(f64::NAN),
                PriceField::MakerInventory => c.stock.maker_inventory.unwrap_or(f64::NAN),
                PriceField::GarchVariance => c.stock.garch_variance,
                PriceField::PreviousTickPrice => {
                    c.stock.previous_tick_price.unwrap_or(f64::NAN)
                }
                PriceField::MispricingSPrevClose => {
                    c.stock.mispricing_s_prev_close.unwrap_or(f64::NAN)
                }
                PriceField::MispricingMomentum => {
                    c.stock.mispricing_momentum.unwrap_or(f64::NAN)
                }
                PriceField::LastDailyReturn => c.stock.last_daily_return.unwrap_or(f64::NAN),
                PriceField::AvgVolume => c.stock.avg_volume,
                PriceField::Beta => c.stock.beta.unwrap_or(f64::NAN),
                PriceField::ShortInterest => c.stock.short_interest,
                PriceField::FloatShares => c.stock.float,
            })
            .collect()
    }

    /// Every company's current price, in roster order. Rate indices are not
    /// in it. A copy, so it does not follow later ticks.
    pub fn prices(&self) -> Vec<f64> {
        self.column(PriceField::Price)
    }

    /// Overwrite prices from a columnar buffer.
    ///
    /// For the embedder to apply an effect this engine does not model — an
    /// earnings gap, a corporate action. It does NOT recompute `s`, so a
    /// caller changing the price must also decide what that means for the
    /// mispricing, exactly as the reference implementation's earnings path does.
    pub fn write_prices(&mut self, prices: &[f64]) {
        assert_eq!(prices.len(), self.companies.len(), "one price per company");
        for (company, &price) in self.companies.iter_mut().zip(prices) {
            company.stock.price = price;
            company.stock.market_cap = price * company.stock.shares_outstanding;
        }
    }

    /// This engine's state as one 32-byte digest: the per-day ledger leaf.
    ///
    /// # What it measures
    ///
    /// sha256 over every field `state_snapshot` carries, in one fixed order,
    /// with one encoding per type. Two engines whose hashes agree hold the
    /// same market state to the bit: the same columns, the same generator
    /// positions, the same day accumulators, the same macro chain and the
    /// same central bank. `market_digest` covers nine columns and the draw
    /// count; this covers the macro chain and the generators as well, which
    /// is what a day-by-day ledger needs, because two runs can agree on
    /// today's prices and hold different state for tomorrow's.
    ///
    /// # What it cannot say
    ///
    /// It is a hash of STATE, so it says nothing about history: the order
    /// log and the recorded tape are outside it, exactly as they are
    /// outside `state_snapshot`. Two engines that reached
    /// the same state by different routes hash the same, which is what makes
    /// a replayed day checkable against a recorded one at all.
    ///
    /// # Why the two arguments
    ///
    /// The snapshot carries `market_open` and `day_count` and this struct
    /// holds neither: the session flag and the day counter live in the
    /// binding, which passes the day into `close_day`. The caller supplies
    /// them so the digest covers the whole snapshot, rather than this method
    /// hashing a subset of it and calling that the state.
    ///
    /// # The encoding
    ///
    /// Every float is eight bytes big-endian with `0x7ff8000000000000` for
    /// any NaN, the rule `manifest._f64` and `tests/known_answer.py` share,
    /// so neither a decimal formatter nor a platform's NaN payload can reach
    /// the digest.
    ///
    /// The generator states are the one exception and it is load-bearing. A
    /// PCG32 state is a `u64`, and the snapshot carries it as an `f64` bit
    /// pattern because a `u64` does not survive a Python float. About one
    /// `u64` in a thousand reads as a NaN, so canonicalising those would map
    /// distinct generator positions onto one digest and a tampered position
    /// would verify. They are hashed as raw big-endian `u64` bit patterns.
    ///
    /// A string is length-prefixed, `u32` big-endian then UTF-8, so "AB"
    /// followed by "C" cannot hash as "A" followed by "BC". A bool is one
    /// byte. An `Option` is a presence byte and then the value when present.
    /// `pending_jump` and `pending_overnight` are the day's boundary moves
    /// waiting for the tape row that carries them. They live on the Python
    /// wrapper rather than here, because they are recording state, and they
    /// are passed IN rather than left out: a snapshot carries them, and
    /// `manifest.state_hash` -- the twin this must agree with byte for byte
    /// -- refuses a snapshot holding a field it does not hash. Two states
    /// alike in every column and holding different pending jumps write
    /// different tapes tomorrow, so calling them equal would overclaim.
    ///
    /// Empty slices for a caller that has none, which is every core-only
    /// caller and every test below.
    pub fn state_hash(&self, day_count: u32, market_open: bool) -> [u8; 32] {
        self.state_hash_with_pending(day_count, market_open, &[], &[], &[], &[])
    }

    /// [`Engine::state_hash`], carrying the wrapper's pending tape state.
    pub fn state_hash_with_pending(
        &self,
        day_count: u32,
        market_open: bool,
        pending_jump: &[f64],
        pending_overnight: &[f64],
        pending_fair_value: &[f64],
        pending_dividend: &[f64],
    ) -> [u8; 32] {
        use sha2::{Digest, Sha256};

        let n = self.companies.len();
        let mut buf: Vec<u8> = Vec::new();

        // The eighteen columns, instrument by instrument. Read once each
        // rather than per instrument, because `column` builds a Vec.
        let columns: Vec<Vec<f64>> = STATE_HASH_COLUMNS
            .iter()
            .map(|field| self.column(*field))
            .collect();
        for i in 0..n {
            for column in &columns {
                hash_f64(&mut buf, column[i]);
            }
        }

        // The ten generator states, in the order the snapshot's flat `rng`
        // array carries them. Raw bit patterns, per the encoding note above.
        let rng = self.rng_state();
        for state in [
            rng.market, rng.economy, rng.external, rng.jumps, rng.volume,
            rng.news, rng.volume_idio, rng.overnight, rng.market_vol_level,
            rng.crisis_epicentre,
        ] {
            hash_u64(&mut buf, state.state);
            hash_u64(&mut buf, state.increment);
            hash_bits(&mut buf, state.spare.unwrap_or(f64::NAN));
        }

        // The roster and the model, which the snapshot carries as its two
        // identity guards. Hashed because a leaf that ignored them would let
        // a day taken on another roster, or under another preset, verify as
        // this one: `restore_state` refuses both, and a ledger written
        // outside it would not.
        hash_u32(&mut buf, n as u32);
        for id in self.ids() {
            hash_str(&mut buf, &id);
        }
        hash_str(&mut buf, self.model_fingerprint());

        // The day accumulators, in snapshot order. The fair-value shift's
        // slot, the last in both, follows the rest and only on an engine that
        // carries fair-value offsets, the one place it can be non-zero, so
        // every engine without them hashes as it did before the slot.
        // Also wherever a slot is non-zero, so an edited slot on an engine
        // without offsets is still covered.
        let fv_hashed = self.carries_fair_value_offsets()
            || self.attribution.iter().any(|r| r[crate::market::factors::FAIR_VALUE_SLOT] != 0.0)
            || self.tick_components.iter().any(|r| r[crate::market::factors::TICK_FAIR_VALUE] != 0.0);
        for row in &self.attribution {
            for value in &row[..crate::market::factors::FAIR_VALUE_SLOT] {
                hash_f64(&mut buf, *value);
            }
        }
        for row in &self.tick_components {
            for value in &row[..crate::market::factors::TICK_FAIR_VALUE] {
                hash_f64(&mut buf, *value);
            }
        }
        if fv_hashed {
            for row in &self.attribution {
                hash_f64(&mut buf, row[crate::market::factors::FAIR_VALUE_SLOT]);
            }
            for row in &self.tick_components {
                hash_f64(&mut buf, row[crate::market::factors::TICK_FAIR_VALUE]);
            }
        }
        // The dividend's slot, after the attribution's last, on the same
        // rule: only on a model that pays dividends or where it is non-zero,
        // so every other engine hashes as it did before the slot.
        if self.carries_dividends()
            || self.attribution.iter().any(|r| r[crate::market::factors::DIVIDEND_SLOT] != 0.0)
        {
            for row in &self.attribution {
                hash_f64(&mut buf, row[crate::market::factors::DIVIDEND_SLOT]);
            }
        }
        for value in &self.tick_fundamental {
            hash_f64(&mut buf, *value);
        }
        for value in &self.tick_anchor {
            hash_f64(&mut buf, *value);
        }
        // The day's noise split and the scale its idiosyncratic part was
        // drawn at, hashed beside the accumulators above and for their
        // reason: off zero on `garch_innovation_commensurate` they decide
        // the innovation tonight's close feeds the per-name GJR, so two
        // engines alike in every column and holding different splits close
        // the day differently.
        for row in &self.noise_parts {
            for value in row {
                hash_f64(&mut buf, *value);
            }
        }
        for value in &self.noise_own_scale2 {
            hash_f64(&mut buf, *value);
        }
        // The day's GJR sum under a night split, the only engines that keep
        // one, so every other engine hashes as it did.
        if !self.innovation_day.is_empty() {
            hash_u32(&mut buf, self.innovation_day.len() as u32);
            for value in &self.innovation_day {
                hash_f64(&mut buf, *value);
            }
        }
        // The jump waiting to be traded in, which the volume scale reads on
        // the session after the close that booked it.
        for value in &self.jump_move {
            hash_f64(&mut buf, *value);
        }
        hash_bool(&mut buf, market_open);

        // The engine-level dials.
        let (variance, day_factor, fast_variance, slow_variance,
             prev_day_factor, smoothed_vix) = self.market_variance_state();
        for value in [variance, day_factor, fast_variance, slow_variance,
                      prev_day_factor, smoothed_vix] {
            hash_f64(&mut buf, value);
        }
        hash_f64(&mut buf, self.volume_state);
        for value in &self.volume_idio {
            hash_f64(&mut buf, *value);
        }
        // The two states the composed vector turned on. LENGTH-PREFIXED, the
        // sector one because it follows the sector table rather than the
        // roster and the name one because it is empty on an engine built
        // with no companies -- the same reason the pending buffers below
        // carry their lengths.
        //
        // Unhashed, these let a resumed day verify against a leaf it should
        // not: two engines alike in every column and holding different
        // sector variance revert to different sector targets tonight, and
        // `test_sampled_verification` failed on exactly that.
        hash_u32(&mut buf, self.sector_variance.len() as u32);
        for value in &self.sector_variance {
            hash_f64(&mut buf, *value);
        }
        hash_u32(&mut buf, self.jump_excitation.len() as u32);
        for value in &self.jump_excitation {
            hash_f64(&mut buf, *value);
        }
        // The sector state's two per-DAY companions, hashed for the reason
        // `attribution` and `tick_components` above are: they are zero at a
        // day boundary and they are the day's accumulated sector factor and
        // the scale it was drawn at MID-DAY, which is where a fork is taken.
        hash_u32(&mut buf, self.sector_day_factor.len() as u32);
        for value in &self.sector_day_factor {
            hash_f64(&mut buf, *value);
        }
        hash_f64(&mut buf, self.sector_target_day);
        hash_f64(&mut buf, self.universe_stress);
        hash_f64(&mut buf, self.forced_flow_spent);
        hash_f64(&mut buf, self.market_vol_log_level);
        hash_f64(&mut buf, self.vix_log_level);
        // Only when it can move, so every preset's state hash is the one it
        // was before the field existed.
        if self.params.vix_anchor_memory != 0.0 {
            hash_f64(&mut buf, self.vix_anchor_slow);
        }
        // The market factor's return memory, on the same rule.
        if self.carries_market_vol_leverage() {
            hash_f64(&mut buf, self.market_vol.leverage_memory());
        }
        // The cycle's volatility multiplier, on the same rule: only with
        // `market_vol_cycle_ratio` set, and once a close has set it.
        if self.params.market_vol_cycle_ratio != 0.0 {
            if let Some(l) = self.market_vol_cycle_log {
                hash_f64(&mut buf, l);
            }
        }
        // The published VIX's stress memory, on the same rule.
        if self.carries_vix_stress_memory() {
            hash_f64(&mut buf, self.vix_stress_memory);
        }
        // The aggregate earnings cycle, on the same rule.
        if self.params.earnings_cycle_depth != 0.0 {
            hash_f64(&mut buf, self.economy.earnings_cycle);
        }
        // The volatility feedback's smoothed exposure, on the same rule.
        if self.carries_vix_feedback() {
            hash_f64(&mut buf, self.economy.vix_feedback);
        }
        // The accrued buyback share-count reductions, on the same rule.
        if self.carries_buyback_log_shares() {
            for c in &self.companies {
                hash_f64(&mut buf, c.stock.buyback_log_shares.unwrap_or(0.0));
            }
        }
        // The earnings calendar's key, on the same rule: only with the
        // calendar on, so every other engine hashes as it did.
        if self.carries_earnings() {
            hash_u64(&mut buf, self.earnings_key);
        }
        // What names hold back of the earnings cycle, on the same rule,
        // LENGTH-PREFIXED like the other per-name states added after the
        // roster columns.
        if self.carries_earnings_withheld() {
            hash_u32(&mut buf, self.earnings_withheld.len() as u32);
            for value in &self.earnings_withheld {
                hash_f64(&mut buf, *value);
            }
        }
        // Tonight's market draw, on the same rule: only while the session's
        // live lagged wire reads it.
        if self.carries_night_market_factor() {
            hash_f64(&mut buf, self.night_market_factor);
        }
        // The Fed put's four fields, on the same rule.
        if self.carries_fed_put() {
            hash_f64(&mut buf, self.economy.intermeeting_return);
            hash_f64(&mut buf, self.economy.fed_put);
            hash_f64(&mut buf, self.economy.fed_put_owed);
            hash_f64(&mut buf, self.economy.fed_put_mcap_prev);
        }
        // Credit's leverage gap, on the same rule: only with
        // `corporate_spread_equity_gain` set.
        if self.carries_spread_equity_gap() {
            hash_f64(&mut buf, self.economy.spread_equity_gap);
        }
        // The fair-value levels and the unspent opening draws, on the same
        // rule: only when a dial can move them.
        if self.carries_fair_value_offsets() {
            for c in &self.companies {
                hash_f64(&mut buf, c.stock.fair_value_offset.unwrap_or(0.0));
            }
            hash_u32(&mut buf, self.opening_z.len() as u32);
            for value in &self.opening_z {
                hash_f64(&mut buf, *value);
            }
            // The prehistory's carried opening, only while one waits: empty
            // on every shipped preset, so their hashes are the ones they were.
            if !self.opening_carry.is_empty() {
                hash_u32(&mut buf, self.opening_carry.len() as u32);
                for value in &self.opening_carry {
                    hash_f64(&mut buf, *value);
                }
            }
        }
        // The dividend states, on the same rule: only on a model that pays
        // them. NaN (one pattern) for a name without one.
        if self.carries_dividends() {
            for value in self.dividend_states() {
                hash_f64(&mut buf, value);
            }
        }
        // The per-name idiosyncratic variance state, only while it runs, so
        // every preset hashes as it did. Length-prefixed, each vector.
        if self.carries_idio_vol_state() {
            for v in [&self.idio_variance, &self.idio_jump_pending, &self.idio_jump_var_pending] {
                hash_u32(&mut buf, v.len() as u32);
                for value in v {
                    hash_f64(&mut buf, *value);
                }
            }
        }
        // The crisis episode. Hashed for the reason every field here is:
        // two engines alike in every column, one of them three sessions
        // into a financial-services episode and the other not in an episode
        // at all, price tomorrow differently. The pin goes in beside the
        // state because it is what the NEXT episode will be drawn -- or not
        // drawn -- from, which is the same class of divergence one session
        // later. `-2` for an absent pin, which is no sector index and not
        // the `-1` that means `none`.
        hash_bool(&mut buf, self.crisis_in_episode);
        hash_f64(&mut buf, self.crisis_sessions_under as f64);
        hash_f64(&mut buf, self.crisis_epicentre as f64);
        hash_f64(&mut buf, self.crisis_epicentre_pin.unwrap_or(-2) as f64);
        // A forced close pending tonight. Only while true, so every engine
        // that was never forced hashes as it did before the mark existed.
        if self.vix_sets_variance_pending {
            hash_bool(&mut buf, true);
        }
        // Today's pins, likewise only while any is set, behind a tag so the
        // marks cannot hash as the forced close's.
        if self.macro_pins_today != 0 {
            hash_f64(&mut buf, 7.0);
            hash_f64(&mut buf, self.macro_pins_today as f64);
            if self.macro_pins_today & PIN_SPREAD != 0 {
                hash_f64(&mut buf, self.pinned_corporate_spread);
            }
        }
        // The central bank's stress level, only while `fed_stress_cut` is
        // set, behind its own tag.
        if let Some(level) = self.stress_vix_max() {
            hash_f64(&mut buf, 8.0);
            hash_f64(&mut buf, level);
        }
        // The stress hold's clock and the priced path's forecast, each only
        // while its dial is set, behind its own tag.
        if let Some(age) = self.stress_hold_age() {
            hash_f64(&mut buf, 10.0);
            hash_f64(&mut buf, age);
        }
        if let Some(path) = self.policy_path() {
            hash_f64(&mut buf, 11.0);
            hash_f64(&mut buf, path);
        }
        // The drawdown hold's window and base, only while `fed_drawdown_hold`
        // is set, behind its own tag and length-prefixed.
        if let Some((returns, prev)) = self.drawdown_state() {
            hash_f64(&mut buf, 32.0);
            hash_f64(&mut buf, returns.len() as f64);
            for r in returns {
                hash_f64(&mut buf, r);
            }
            hash_f64(&mut buf, prev);
        }
        // What the curve prices of the next meeting, only while
        // `policy_anticipation` is set, behind its own tag.
        if let Some(priced) = self.policy_anticipation_priced() {
            hash_f64(&mut buf, 31.0);
            hash_f64(&mut buf, priced);
        }
        // The rate indices' live mark, only while `rate_intraday_live` is set
        // and a session holds one, behind its own tag.
        if let Some(marks) = self.rate_live_marks() {
            hash_f64(&mut buf, 9.0);
            for value in marks {
                hash_f64(&mut buf, value);
            }
        }
        // Today's priced VIX move, only while a pin has made one
        // (`pinned_vix_variance_share`), behind its own tag.
        if self.pinned_vix_jump != 0.0 {
            hash_f64(&mut buf, 10.0);
            hash_f64(&mut buf, self.pinned_vix_jump);
        }
        // The day's market t scale (`market_day_tail_df`), only between an
        // open that drew one and the close, behind its own tag.
        if self.market_day_scale != 1.0 {
            hash_f64(&mut buf, 12.0);
            hash_f64(&mut buf, self.market_day_scale);
        }
        // The price index's divisor and close level, only while
        // `index_level_listed` is set, behind its own tag.
        if let Some([divisor, close]) = self.index_state() {
            hash_f64(&mut buf, 41.0);
            hash_f64(&mut buf, divisor);
            hash_f64(&mut buf, close);
        }
        // The live VIX's projection, only while `vix_intraday_live` is set and
        // a session holds one, behind its own tag.
        if let Some(mark) = self.vix_live_mark() {
            hash_f64(&mut buf, 42.0);
            hash_f64(&mut buf, mark);
        }
        // The forecast, only while `forecast_horizon_sessions` is set and a
        // close has computed one, behind its own tag and length-prefixed.
        if let Some(forecast) = self.forecast() {
            let words = forecast.to_words();
            hash_f64(&mut buf, 43.0);
            hash_u32(&mut buf, words.len() as u32);
            for value in words {
                hash_f64(&mut buf, value);
            }
        }
        // The index futures, only while `futures_index_listed` is set, behind
        // their own tag: their generator, then their numbers,
        // length-prefixed; their book behind another tag once an agent has
        // traded a contract; the night's path behind a third while one is
        // walked (`night_session_steps`).
        if let (Some(rng), Some(words)) = (self.derivatives_rng_state(), self.futures_words()) {
            hash_f64(&mut buf, 44.0);
            hash_u64(&mut buf, rng.state);
            hash_u64(&mut buf, rng.increment);
            hash_bits(&mut buf, rng.spare.unwrap_or(f64::NAN));
            hash_f64(&mut buf, rng.uniforms as f64);
            hash_f64(&mut buf, rng.normals as f64);
            hash_u32(&mut buf, words.len() as u32);
            for value in words {
                hash_f64(&mut buf, value);
            }
        }
        if let Some(book) = self.futures_book_state() {
            hash_f64(&mut buf, 45.0);
            hash_book(&mut buf, book);
        }
        if let Some(words) = self.night_bridge_words() {
            hash_f64(&mut buf, 46.0);
            hash_u32(&mut buf, words.len() as u32);
            for value in words {
                hash_f64(&mut buf, value);
            }
        }
        // The VIX's fear memory, only while `vix_fear_uptake` is set, behind
        // its own tag.
        if self.carries_vix_fear() {
            hash_f64(&mut buf, 47.0);
            hash_f64(&mut buf, self.vix_fear);
        }
        // The VIX futures, only while `futures_vix_listed` is set, behind
        // their own tag, length-prefixed; their book behind another once an
        // agent has traded one.
        if let Some(words) = self.vix_futures_words() {
            hash_f64(&mut buf, 48.0);
            hash_u32(&mut buf, words.len() as u32);
            for value in words {
                hash_f64(&mut buf, value);
            }
        }
        if let Some(book) = self.vix_futures_book_state() {
            hash_f64(&mut buf, 49.0);
            hash_book(&mut buf, book);
        }
        // The rate futures, only while `futures_rates_listed` is set, behind
        // their own tag, length-prefixed; their book behind another once an
        // agent has traded one.
        if let Some(words) = self.rate_futures_words() {
            hash_f64(&mut buf, 50.0);
            hash_u32(&mut buf, words.len() as u32);
            for value in words {
                hash_f64(&mut buf, value);
            }
        }
        if let Some(book) = self.rate_futures_book_state() {
            hash_f64(&mut buf, 51.0);
            hash_book(&mut buf, book);
        }
        // The oil futures, only while `futures_oil_listed` is set, behind
        // their own tag, length-prefixed; their book behind another once an
        // agent has traded one.
        if let Some(words) = self.oil_futures_words() {
            hash_f64(&mut buf, 52.0);
            hash_u32(&mut buf, words.len() as u32);
            for value in words {
                hash_f64(&mut buf, value);
            }
        }
        if let Some(book) = self.oil_futures_book_state() {
            hash_f64(&mut buf, 53.0);
            hash_book(&mut buf, book);
        }
        // The contracts' margin, only while `margin_scan_coverage` is set,
        // behind its own tag, length-prefixed.
        if let Some(words) = self.margin_words() {
            hash_f64(&mut buf, 54.0);
            hash_u32(&mut buf, words.len() as u32);
            for value in words {
                hash_f64(&mut buf, value);
            }
        }
        // Oil's long factor, only while `oil_target_drift_sd` is set, behind
        // its own tag: its log level and its key's two words.
        if let Some(words) = self.oil_drift_words() {
            hash_f64(&mut buf, 55.0);
            for value in words {
                hash_f64(&mut buf, value);
            }
        }
        // LENGTH-PREFIXED, because these two are EMPTY between the tape row
        // that consumes them and the close that fills them again, where
        // every per-slot array above always follows the roster. An empty
        // buffer and a roster-length one of zeros are different states.
        for pending in [pending_jump, pending_overnight] {
            hash_u32(&mut buf, pending.len() as u32);
            for value in pending {
                hash_f64(&mut buf, *value);
            }
        }
        // The jump's fair-value shift waiting for its tape row, beside the
        // jump. Only while non-empty, which only a preset with fair-value
        // offsets can make it, so every other engine hashes as it did.
        if !pending_fair_value.is_empty() {
            hash_u32(&mut buf, pending_fair_value.len() as u32);
            for value in pending_fair_value {
                hash_f64(&mut buf, *value);
            }
        }
        // The ex-date's move in `s` waiting for its tape row, on the same
        // rule: only while non-empty, which only a model paying dividends
        // can make it.
        if !pending_dividend.is_empty() {
            hash_u32(&mut buf, pending_dividend.len() as u32);
            for value in pending_dividend {
                hash_f64(&mut buf, *value);
            }
        }
        // The growth term's base, in snapshot order. A constant of the run
        // rather than state that advances, and covered for the reason
        // every other field here is: two engines that agree on today's
        // prices and hold different bases price tomorrow differently.
        hash_f64(&mut buf, self.nominal_output_base);

        // The day's endogenous news. Generated at `open_market` and cleared
        // at the next one, so a close-boundary leaf carries the day's events
        // rather than an empty list.
        hash_u32(&mut buf, self.session_news.len() as u32);
        for event in &self.session_news {
            hash_opt_str(&mut buf, event.company_id.as_deref());
            hash_opt_str(&mut buf, event.sector.as_deref());
            hash_opt_f64(&mut buf, event.price_impact);
        }

        // The economy, in the order `state_snapshot` declares its fields.
        // `qe_assets_ratio` is not hashed. The snapshot carries it only
        // under `qe_pe_stock_gain`, the one dial that reads it, and covering
        // it would move every leaf a run under that dial has written.
        let e = &self.economy;
        for value in [
            e.federal_funds_rate, e.prime_rate, e.corporate_bond_yield,
            e.treasury_yield_10y, e.treasury_yield_2y, e.mortgage_rate_30y,
            e.cpi, e.inflation_rate, e.core_inflation,
            e.gdp_growth, e.gdp,
            e.unemployment_rate, e.jobs_created, e.labor_force_participation,
            e.usd_index, e.oil_price, e.gold_price, e.copper_price,
            e.housing_index, e.home_starts_monthly,
            e.housing_transaction_volume,
            e.long_term_unemployment_rate, e.structural_unemployment,
            e.consumer_confidence, e.business_confidence, e.fear_greed_index,
            e.vix,
            e.tariff_rate, e.trade_balance,
            e.oil_inventory_level,
        ] {
            hash_f64(&mut buf, value);
        }
        hash_i64(&mut buf, e.oil_last_opec_day);
        for value in [e.wage_growth, e.previous_day_market_return,
                      e.rolling_market_return_30d] {
            hash_f64(&mut buf, value);
        }
        hash_opt_f64(&mut buf, e.market_pe);
        for value in [e.qe_pe_boost, e.fiscal_stimulus,
                      e.government_debt_to_gdp, e.months_in_current_phase,
                      e.phase_gdp_target, e.recession_probability] {
            hash_f64(&mut buf, value);
        }
        for value in e.gdp_trend {
            hash_f64(&mut buf, value);
        }
        hash_str(&mut buf, e.cycle_phase.as_str());
        // The market's nowcast and its generator's position, only while
        // `cycle_nowcast_accuracy` is set.
        if self.params.cycle_nowcast_accuracy != 0.0 {
            for v in self.cycle_nowcast {
                hash_f64(&mut buf, v);
            }
            let s = self.cycle_nowcast_rng.snapshot();
            hash_u64(&mut buf, s.state);
            hash_u64(&mut buf, s.increment);
            hash_bits(&mut buf, s.spare.unwrap_or(f64::NAN));
            hash_f64(&mut buf, s.uniforms as f64);
            hash_f64(&mut buf, s.normals as f64);
        }
        // The published-phase history, only while `cycle_publication_lag`
        // keeps one, so every other engine's hash is the one it was.
        if self.carries_cycle_history() {
            hash_u32(&mut buf, self.cycle_history.len() as u32);
            for phase in &self.cycle_history {
                hash_str(&mut buf, phase.as_str());
            }
        }
        // Unemployment's impulse, only while
        // `unemployment_adjustment_half_life` is set.
        if self.params.unemployment_adjustment_half_life != 0.0 {
            hash_f64(&mut buf, e.unemployment_impulse);
        }
        // The oil pushes' part of the price, only while
        // `oil_pushes_in_target` is set.
        if self.params.oil_pushes_in_target != 0.0 {
            hash_f64(&mut buf, e.oil_push_level);
        }
        // The dollar's safe-haven bid, only while `usd_mean_reversion` is set.
        if self.params.usd_mean_reversion != 0.0 {
            hash_f64(&mut buf, e.usd_haven_level);
        }
        // The published GDP growth figure's state, only while
        // `gdp_publication_lag` is set, so every other engine's hash is the
        // one it was.
        if self.params.gdp_publication_lag != 0.0 {
            let p = &self.gdp_publication;
            hash_f64(&mut buf, p.published);
            hash_i64(&mut buf, p.quarter);
            hash_u32(&mut buf, p.count);
            hash_f64(&mut buf, p.sum);
            hash_u32(&mut buf, p.pending.len() as u32);
            for &(release, value) in &p.pending {
                hash_i64(&mut buf, release);
                hash_f64(&mut buf, value);
            }
        }
        // The drawn publication schedule, only while
        // `cycle_publication_lag_draw` is set: the key, the published and
        // last true phases, the two counters, and the pending turns
        // LENGTH-PREFIXED, each its close then its phase.
        if self.params.cycle_publication_lag_draw != 0.0 {
            let p = &self.cycle_publication;
            hash_u64(&mut buf, p.key);
            hash_str(&mut buf, p.published.as_str());
            hash_str(&mut buf, p.last_true.as_str());
            hash_i64(&mut buf, p.closes);
            hash_u64(&mut buf, p.turns);
            hash_u32(&mut buf, p.pending.len() as u32);
            for &(close, phase) in &p.pending {
                hash_i64(&mut buf, close);
                hash_str(&mut buf, phase.as_str());
            }
        }
        // The anticipation's left-out drift `D` and the last `A - e`, only
        // while `earnings_anticipation_drift_share` is set.
        if self.carries_anticipation_drift() {
            hash_f64(&mut buf, self.anticipation_drift);
            hash_f64(&mut buf, self.anticipation_raw);
        }

        // The central bank.
        let bank = &self.central_bank;
        hash_i64(&mut buf, bank.last_meeting_date);
        hash_i64(&mut buf, bank.next_meeting_date);
        hash_f64(&mut buf, bank.target_inflation);
        hash_f64(&mut buf, bank.target_unemployment);
        hash_bool(&mut buf, bank.qe_active);
        hash_f64(&mut buf, bank.qe_monthly_purchases);
        hash_f64(&mut buf, bank.hawkish_dovish_score);
        hash_str(&mut buf, bank.forward_guidance.as_str());

        hash_u32(&mut buf, day_count);

        // The draw-addressing layer's two snapshot fields. The counts are
        // the seven streams' uniform and normal positions, flattened the
        // way the snapshot flattens them; the overlay is the table of
        // substitutions installed on them, walked stream by stream in the
        // same order.
        //
        // Both are hashed because both decide what the engine does next. Two
        // engines alike in every column but differing by one patched draw
        // take different days from here, and a leaf that skipped the table
        // would commit to a state that does not describe them. The hash
        // refuses a snapshot carrying a field it does not know for exactly
        // this reason, and these two arrived after it was written.
        for (uniforms, normals) in self.stream_positions() {
            hash_f64(&mut buf, uniforms as f64);
            hash_f64(&mut buf, normals as f64);
        }
        let mut overlay: Vec<(u32, u8, u64, f64)> = Vec::new();
        for id in 0..stream::COUNT as u32 {
            if let Some(table) = self.draw_overlay(id) {
                for ((kind, index), value) in &table.table {
                    overlay.push((id, *kind as u8, *index, *value));
                }
            }
        }
        hash_u32(&mut buf, overlay.len() as u32);
        for (stream, kind, index, value) in overlay {
            hash_u32(&mut buf, stream);
            hash_u32(&mut buf, u32::from(kind));
            hash_u64(&mut buf, index);
            hash_f64(&mut buf, value);
        }

        // The rate instruments, after every equity field and before the
        // agent-facing book, and only when there are any, so every engine
        // without them hashes exactly as it did before they existed.
        // In the order the snapshot's `rates` block carries them, which is
        // the order `manifest.state_hash` reads. The per-tick print
        // decomposition is output, not state, and is left out as the
        // equities' is.
        if !self.rates.is_empty() {
            hash_str(&mut buf, "rates");
            hash_u32(&mut buf, self.rates.len() as u32);
            for inst in &self.rates.instruments {
                hash_str(&mut buf, inst.spec.ticker);
                for value in [
                    inst.level, inst.marked_yield, inst.price, inst.previous_close,
                    inst.open, inst.high, inst.low, inst.volume, inst.avg_volume,
                    inst.units_outstanding, inst.maker_inventory, inst.day_carry,
                    inst.day_duration, inst.day_convexity,
                ] {
                    hash_f64(&mut buf, value);
                }
            }
            hash_f64(&mut buf, self.rates.ig_spread);
            hash_f64(&mut buf, self.rates.last_corporate);
            hash_bool(&mut buf, self.rates.closed_since_open);
        }

        // The agent-facing book, after the rate instruments, and only when
        // it has been used, so every state that never saw an agent's order
        // hashes as it did before the book existed. Everything in it decides what the next
        // tick or the next agent meets, the undelivered fills included:
        // two engines alike in every column but owing an agent different
        // fills are not the same state. `manifest.state_hash` writes the
        // same bytes from the snapshot's `book` entry.
        if !self.book.is_pristine() {
            hash_book(&mut buf, &self.book);
        }

        // Four fields added after the book, each behind its own name and
        // each only where it can differ from what an engine that never
        // moved it holds, so every state hashed before them hashes the same.
        //
        // The day's label and the valuation's clock, each only when it is not
        // the day `day_count` and the session flag give (`default_day`). A
        // run that numbers its days from the counter never writes either.
        // The label is here although nothing prices off it, because the
        // book stamps fills with it and two engines owing different stamps
        // would hash apart one fill later.
        let usual = default_day(day_count, market_open);
        if self.current_day != usual {
            hash_str(&mut buf, "current_day");
            hash_i64(&mut buf, self.current_day);
        }
        if self.elapsed_days != usual {
            hash_str(&mut buf, "elapsed_days");
            hash_i64(&mut buf, self.elapsed_days);
        }
        // The fair-value inputs, once `set_fundamentals` has moved them off
        // the ones each company was built or listed with. Every price
        // values off them, so two engines alike in every column and valuing
        // different earnings are not the same state; before this they
        // hashed equal and a restore brought back the construction figures.
        if self.fundamentals_changed() {
            hash_str(&mut buf, "fundamentals");
            hash_u32(&mut buf, n as u32);
            for c in &self.companies {
                hash_f64(&mut buf, c.eps.unwrap_or(f64::NAN));
                hash_f64(&mut buf, c.book_value_per_share.unwrap_or(f64::NAN));
                hash_f64(&mut buf, c.revenue_growth.unwrap_or(f64::NAN));
            }
        }
        // The share counts, once `set_shares_outstanding` has moved them off
        // the ones each company was built or listed with: every tick's market
        // cap is the price times the count.
        if self.shares_outstanding_changed() {
            hash_str(&mut buf, "shares_outstanding");
            hash_u32(&mut buf, n as u32);
            for c in &self.companies {
                hash_f64(&mut buf, c.stock.shares_outstanding);
            }
        }
        // The host's input to the VIX target, each behind its name and only
        // while it stands (`set_vix_target_premium`, `set_vix_target_floor`),
        // so an engine no host wrote one on hashes as it did before.
        if self.carries_vix_target_premium() {
            hash_str(&mut buf, "vix_target_premium");
            hash_f64(&mut buf, self.vix_target_premium);
            hash_f64(&mut buf, self.vix_target_premium_half_life);
        }
        if let Some(floor) = self.vix_target_floor {
            hash_str(&mut buf, "vix_target_floor");
            hash_f64(&mut buf, floor);
        }
        // The variance cascade's components, only on a model that runs it.
        if self.carries_garch_cascade() {
            let values = self.garch_cascade();
            hash_str(&mut buf, "garch_cascade");
            hash_u32(&mut buf, values.len() as u32);
            for value in values {
                hash_f64(&mut buf, value);
            }
        }
        // The population, last and only on an engine that holds one: its
        // fingerprint, its roster and every number of its state.
        if let Some(pop) = self.population.as_deref() {
            hash_str(&mut buf, "population");
            hash_str(&mut buf, &pop.fingerprint);
            hash_u32(&mut buf, pop.tickers.len() as u32);
            for t in &pop.tickers {
                hash_str(&mut buf, t);
            }
            let flat = pop.to_flat();
            hash_u32(&mut buf, flat.len() as u32);
            for value in flat {
                hash_f64(&mut buf, value);
            }
        }

        let mut hasher = Sha256::new();
        hasher.update(&buf);
        let out = hasher.finalize();
        let mut digest = [0u8; 32];
        digest.copy_from_slice(&out);
        digest
    }
}

/// Fields exposed columnar-wise across the FFI boundary.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum PriceField {
    Price,
    PreviousClose,
    Open,
    High,
    Low,
    Volume,
    MarketCap,
    /// `NaN` for a company that has never ticked, since a column cannot carry
    /// `None`. The embedder should read it as "unset", not as a number.
    MispricingS,
    /// `NaN` when the maker has never quoted this company.
    ///
    /// Not zero. Zero is a REAL inventory -- a maker holding nothing -- and
    /// before the first tick every company read 0.0, which said "flat" about a
    /// book that did not exist yet. Absence is not zero anywhere else in this
    /// library and it should not have been here.
    MakerInventory,
    GarchVariance,
    /// The previous tick's print. `NaN` before the second tick.
    PreviousTickPrice,
    /// `s` as it stood at the last close, and the momentum term carried from
    /// it. Both `NaN` before the first close: they are day-boundary state, so
    /// a session that has not crossed one has no value to report.
    MispricingSPrevClose,
    MispricingMomentum,
    /// The last completed day's return. `NaN` before the first close.
    LastDailyReturn,
    /// Cross-sectional characteristics, constant through a run. Exposed as
    /// columns so a factor study can join them to `bars` without carrying the
    /// universe alongside.
    AvgVolume,
    Beta,
    ShortInterest,
    FloatShares,
}

/// The columns [`Engine::state_hash`] walks, in the order
/// `python_engine::COLUMN_FIELDS` declares them, so the Rust digest and the
/// Python twin that reads a snapshot see one order.
pub const STATE_HASH_COLUMNS: [PriceField; 18] = [
    PriceField::Price,
    PriceField::PreviousClose,
    PriceField::PreviousTickPrice,
    PriceField::Open,
    PriceField::High,
    PriceField::Low,
    PriceField::Volume,
    PriceField::AvgVolume,
    PriceField::MarketCap,
    PriceField::MispricingS,
    PriceField::MispricingSPrevClose,
    PriceField::MispricingMomentum,
    PriceField::LastDailyReturn,
    PriceField::MakerInventory,
    PriceField::GarchVariance,
    PriceField::Beta,
    PriceField::ShortInterest,
    PriceField::FloatShares,
];

/// The day an engine's two day numbers hold when nothing has moved them
/// off the binding's counter: `day_count` while a session is open, the day
/// just closed once it has closed (`day_count - 1`), and 0 before the first
/// open. [`Engine::state_hash`] covers the label and the valuation's clock
/// only where they differ from this, and a restore that finds neither in
/// its snapshot sets both to it.
///
/// `run_session(close_at_end=True)` closes the day and leaves the session
/// flag set, so after it the two read one day behind this and the snapshot
/// carries them.
pub fn default_day(day_count: u32, market_open: bool) -> i64 {
    let count = i64::from(day_count);
    if market_open || count == 0 {
        count
    } else {
        count - 1
    }
}

/// Where a column sits in [`Engine::state_hash`].
///
/// Exhaustive on `PriceField`, the same discipline `set_column` uses: a
/// nineteenth column fails to compile here until someone decides where it
/// goes, rather than being dropped from every leaf a ledger holds. The test
/// below walks [`STATE_HASH_COLUMNS`] and holds the two in agreement.
pub fn state_hash_position(field: PriceField) -> usize {
    match field {
        PriceField::Price => 0,
        PriceField::PreviousClose => 1,
        PriceField::PreviousTickPrice => 2,
        PriceField::Open => 3,
        PriceField::High => 4,
        PriceField::Low => 5,
        PriceField::Volume => 6,
        PriceField::AvgVolume => 7,
        PriceField::MarketCap => 8,
        PriceField::MispricingS => 9,
        PriceField::MispricingSPrevClose => 10,
        PriceField::MispricingMomentum => 11,
        PriceField::LastDailyReturn => 12,
        PriceField::MakerInventory => 13,
        PriceField::GarchVariance => 14,
        PriceField::Beta => 15,
        PriceField::ShortInterest => 16,
        PriceField::FloatShares => 17,
    }
}

/// The one NaN pattern any digest in this crate writes. IEEE-754 leaves the
/// sign and payload of a NaN to the platform, so hashing the bits as they
/// arrive would make a digest depend on the machine rather than the model.
const CANONICAL_NAN: [u8; 8] = [0x7f, 0xf8, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00];

/// One f64 in canonical digest form: big-endian, one NaN pattern.
fn hash_f64(buf: &mut Vec<u8>, value: f64) {
    if value.is_nan() {
        buf.extend_from_slice(&CANONICAL_NAN);
    } else {
        buf.extend_from_slice(&value.to_be_bytes());
    }
}

/// One f64 as its raw bit pattern, NaN included.
///
/// For a value that is a bit pattern wearing a float, which is how a
/// generator state crosses into Python. Canonicalising one of those would
/// collapse distinct states onto one digest.
fn hash_bits(buf: &mut Vec<u8>, value: f64) {
    buf.extend_from_slice(&value.to_bits().to_be_bytes());
}

fn hash_u64(buf: &mut Vec<u8>, value: u64) {
    buf.extend_from_slice(&value.to_be_bytes());
}

fn hash_i64(buf: &mut Vec<u8>, value: i64) {
    buf.extend_from_slice(&value.to_be_bytes());
}

fn hash_u32(buf: &mut Vec<u8>, value: u32) {
    buf.extend_from_slice(&value.to_be_bytes());
}

fn hash_bool(buf: &mut Vec<u8>, value: bool) {
    buf.push(u8::from(value));
}

/// A string, length-prefixed so two adjacent strings cannot be re-split.
fn hash_str(buf: &mut Vec<u8>, value: &str) {
    hash_u32(buf, value.len() as u32);
    buf.extend_from_slice(value.as_bytes());
}

fn hash_opt_f64(buf: &mut Vec<u8>, value: Option<f64>) {
    match value {
        Some(v) => {
            hash_bool(buf, true);
            hash_f64(buf, v);
        }
        None => hash_bool(buf, false),
    }
}

/// The agent-facing book, in the order the snapshot's `book` entry holds it.
fn hash_book(buf: &mut Vec<u8>, book: &crate::agent_book::BookState) {
    use crate::order_book::Side;
    let side = |s: Side| match s {
        Side::Buy => "buy",
        Side::Sell => "sell",
    };
    hash_str(buf, "book");
    hash_u64(buf, book.sequence);
    hash_u64(buf, book.fill_sequence);
    hash_u32(buf, book.taken.len() as u32);
    for row in &book.taken {
        for v in row {
            hash_f64(buf, *v);
        }
    }
    hash_u32(buf, book.orders.len() as u32);
    for o in &book.orders {
        hash_str(buf, &o.id);
        hash_str(buf, &o.agent);
        hash_str(buf, &o.ticker);
        hash_str(buf, side(o.side));
        hash_f64(buf, o.limit);
        hash_f64(buf, o.quantity);
        hash_f64(buf, o.remaining);
        hash_u64(buf, o.sequence);
        hash_str(buf, o.mode.as_str());
    }
    hash_u32(buf, book.flow.len() as u32);
    for (agent, ticker, bought, sold) in &book.flow {
        hash_str(buf, agent);
        hash_str(buf, ticker);
        hash_f64(buf, *bought);
        hash_f64(buf, *sold);
    }
    hash_u32(buf, book.fills.len() as u32);
    for f in &book.fills {
        hash_str(buf, &f.agent);
        hash_str(buf, &f.order_id);
        hash_str(buf, &f.ticker);
        hash_str(buf, side(f.side));
        hash_f64(buf, f.quantity);
        hash_f64(buf, f.price);
        hash_str(buf, f.liquidity.as_str());
        hash_str(buf, &f.counterparty);
        hash_f64(buf, f.reference);
        hash_i64(buf, f.day);
        // Not `tick`: it is a label counted from this engine's own open,
        // and a restore clears the day marks it is counted from, so a
        // restored engine would hash apart from the one it copied on a
        // label alone. The fill's sequence orders it.
        hash_u64(buf, f.sequence);
    }
    hash_u32(buf, book.impacts.len() as u32);
    for r in &book.impacts {
        hash_str(buf, &r.agent);
        hash_str(buf, &r.ticker);
        hash_f64(buf, r.bought);
        hash_f64(buf, r.sold);
        hash_f64(buf, r.permanent);
        // Only while the metaorder memory is on, so every state without it
        // hashes as it did before it existed.
        if let Some(x) = r.transient {
            hash_str(buf, "transient");
            hash_f64(buf, x);
        }
        hash_i64(buf, r.day);
    }
    // The metaorder memory, after everything else and only while it holds
    // something.
    if !book.memory.is_empty() {
        hash_str(buf, "memory");
        hash_u32(buf, book.memory.len() as u32);
        for row in &book.memory {
            for v in row {
                hash_f64(buf, *v);
            }
        }
    }
}

fn hash_opt_str(buf: &mut Vec<u8>, value: Option<&str>) {
    match value {
        Some(v) => {
            hash_bool(buf, true);
            hash_str(buf, v);
        }
        None => hash_bool(buf, false),
    }
}

/// Index of `random_noise` among the components, found rather than written.
///
/// Every component is an f64, so a literal index would keep compiling and
/// start feeding the variance process the crowd lean the day a component is
/// inserted ahead of it.
fn random_noise_index() -> usize {
    crate::market::factors::S_COMPONENT_KEYS
        .iter()
        .position(|name| *name == "random_noise")
        .expect("random_noise is one of the components")
}

/// Counts draws as they are taken.
///
/// The obvious alternative — clone the generator, replay uniforms until the
/// clone catches up — does NOT work on a mixed stream, and the reason is worth
/// recording because it looks like it should. `next_normal` is Box-Muller: it
/// consumes two PCG steps and leaves a spare cached, and a later normal
/// consumes zero. A probe pulling uniforms never reproduces that spare, so the
/// two states are never equal and the search runs to its bound.
///
/// Counting at the call is exact, costs a `usize` increment, and does not care
/// what kind of draw it was.
struct Counting<'a> {
    inner: &'a mut GameRng,
    count: usize,
}

impl Rng for Counting<'_> {
    fn next_f64(&mut self) -> f64 {
        self.count += 1;
        self.inner.next_f64()
    }
    fn next_normal(&mut self) -> f64 {
        self.count += 1;
        self.inner.next_normal()
    }
    fn site(&mut self, site: Site, tag: u32) {
        self.inner.site(site, tag);
    }
}

/// What a session needs beyond the engine's own state.
///
/// [`SessionRequest::new`] gives a quiet session (no news, no orders,
/// volatility multiplier 1.0) that leaves opening and closing the day to the
/// caller. Set the other fields on the value it returns.
///
/// Outside this crate a `SessionRequest` can only come from `new`, because
/// the struct is `#[non_exhaustive]`: a later patch release can add a field
/// without breaking your code. 0.8.5 added `fills`, and every struct literal
/// written for 0.8.1 stopped compiling. So this does not compile:
///
/// ```compile_fail,E0639
/// use tradefloor::engine::SessionRequest;
/// use tradefloor::market::GameTime;
///
/// let request = SessionRequest {
///     start: GameTime::new(9, 30, 3),
///     ticks: 390,
///     volatility_multiplier: 1.0,
///     news: &[],
///     news_impact_queue: &[],
///     order_volumes: &[],
///     fills: &[],
///     close_at_end: false,
///     reopen: false,
///     daily_innovations: &[],
///     sector_base_variances: &[],
///     stop: None,
/// };
/// ```
#[non_exhaustive]
pub struct SessionRequest<'a> {
    /// The clock at the first tick. Each tick is one minute after the last.
    pub start: GameTime,
    /// How many one-minute ticks to run. 390 is a full session, 09:30 to
    /// 16:00, and is the Python package's default.
    pub ticks: usize,
    /// Scales the per-tick noise. 1.0 is the model as calibrated.
    pub volatility_multiplier: f64,
    /// News events that reach the factor model this session.
    pub news: &'a [NewsEvent],
    /// News already in flight from earlier sessions, still being absorbed.
    pub news_impact_queue: &'a [NewsImpactEntry],
    /// Order volume held on EVERY tick of the session: a standing rate, in
    /// shares per minute, for a program that trades all session long.
    pub order_volumes: &'a [(String, OrderVolume)],
    /// Order volume that reaches the market ONCE, on the session's first
    /// tick: the trades an agent filled at the step boundary just before it.
    ///
    /// # Why this is not `order_volumes`
    ///
    /// Until 0.8.5 every harness handed an agent's fills to the session as
    /// `order_volumes`, so one order was counted on every tick of the step:
    /// 65 times at six steps a day, 390 at one. It also landed only after the
    /// agent had filled at the pre-trade book, so the agent never paid its
    /// own permanent impact and collected it instead. A spec mean reversion
    /// rule beat buy-and-hold on 20 of 20 suite markets by a median 42
    /// points in 60 days on that alone (measured on pt-v19 on 2026-09-24).
    ///
    /// # When it lands, and who pays for it
    ///
    /// The fill is priced against the book standing at the step boundary,
    /// which charges the spread and the depth the order walks. The order's
    /// permanent impact then moves `s` once, on the first tick after the
    /// fill, and the next trader meets the moved price. That is the
    /// discrete Almgren-Chriss convention: a trade executes at the
    /// pre-trade price less its temporary impact, and its permanent impact
    /// reaches the trades after it. The convention is free of a round-trip
    /// profit only while the book charges at least the permanent impact an
    /// order causes, and `tests/test_agent_flow.py` holds that on every
    /// shipped preset: a fill's premium over the print it traded at is
    /// never below the move its own flow leaves behind.
    ///
    /// Summed with `order_volumes` per ticker on the first tick when both
    /// name one. Empty is bit-identical to the session this field did not
    /// exist in: the first tick reads `order_volumes` untouched.
    pub fills: &'a [(String, OrderVolume)],
    /// Run the close bookkeeping when the session finishes normally.
    pub close_at_end: bool,
    /// Open the market before the first tick.
    ///
    /// True is the reference's behaviour and the right default: it runs one
    /// session per day, so opening inside the session and opening the day are
    /// the same act.
    ///
    /// They stop being the same act when a day is made of SEVERAL sessions,
    /// which is what agent stepping does -- act, run some ticks, act again.
    /// `open_market` resets the attribution accumulator and re-anchors the
    /// daily open, so re-opening per step silently made both per-step:
    ///
    /// - `attribution` documents itself as per-DAY and was per-session. A
    ///   large buy in step 0 of a six-step day moved the market -- the tape
    ///   records it -- and `attribution("order_flow_impact")` read exactly
    ///   zero at the close. An agent's own impact was erased from the ground
    ///   truth that scores it.
    /// - Worse, it is not only reporting. `close_at_end` feeds
    ///   `attribution_column(random_noise)` to GARCH as the day's innovation,
    ///   so a stepped day updated variance from the LAST STEP's noise rather
    ///   than the day's.
    ///
    /// Set false for the second and later sessions of one day. A day of one
    /// session is unaffected either way, which is why no parity vector moves.
    pub reopen: bool,
    /// Read only when `close_at_end` is set: each name's daily innovation for
    /// GARCH. A `None` slot takes the noise the engine accumulated this
    /// session, which is what [`Engine::close_day`] always uses.
    pub daily_innovations: &'a [Option<f64>],
    /// Read only when `close_at_end` is set: each sector's base variance.
    pub sector_base_variances: &'a [f64],
    /// Stop early when a condition is met, for event-driven advancement.
    pub stop: Option<StopCondition>,
}

impl<'a> SessionRequest<'a> {
    /// A quiet session of `ticks` minutes starting at `start`.
    ///
    /// No news, no order flow, volatility multiplier 1.0, no stop
    /// condition. It neither opens the market (`reopen` is false) nor
    /// closes it (`close_at_end` is false), so it fits the day loop
    /// [`Engine::open_market`], [`Engine::run_session`], [`Engine::close_day`]
    /// that the WebAssembly binding runs. Set any field on the result to add
    /// news or flow.
    ///
    /// ```
    /// use tradefloor::engine::SessionRequest;
    /// use tradefloor::market::GameTime;
    ///
    /// let open = GameTime::new(9, 30, 3);
    /// let request = SessionRequest::new(open, 390);
    /// assert_eq!(request.ticks, 390);
    /// assert!(!request.reopen && !request.close_at_end);
    /// ```
    pub fn new(start: GameTime, ticks: usize) -> Self {
        SessionRequest {
            start,
            ticks,
            volatility_multiplier: 1.0,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
            fills: &[],
            close_at_end: false,
            reopen: false,
            daily_innovations: &[],
            sector_base_variances: &[],
            stop: None,
        }
    }
}

/// One flow per ticker: `standing` with `once` added to it.
///
/// The tick finds a ticker's flow with the FIRST matching entry, so two
/// entries for one name would drop the second rather than add it. Standing
/// entries keep their order and take any fills for the same name; fills for
/// names the standing flow does not carry follow, in their own order. Both
/// inputs arrive sorted from the bindings, and neither order reaches a price.
pub fn merge_order_volumes(
    standing: &[(String, OrderVolume)],
    once: &[(String, OrderVolume)],
) -> Vec<(String, OrderVolume)> {
    let mut out: Vec<(String, OrderVolume)> = standing.to_vec();
    for (ticker, v) in once {
        match out.iter_mut().find(|(t, _)| t == ticker) {
            Some((_, have)) => {
                have.buy += v.buy;
                have.sell += v.sell;
            }
            None => out.push((ticker.clone(), *v)),
        }
    }
    out
}

/// Why a session might end before its last tick.
///
/// Deliberately limited to what the ENGINE can decide from its own state.
/// Stopping on a fill needs the order book and the caller's resting orders,
/// neither of which this engine owns — that belongs with whoever owns order
/// state, and inventing a half-version here would be worse than not having it.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub enum StopCondition {
    /// A named company's price leaves a band. `None` on a side means unbounded.
    PriceOutside {
        company: usize,
        below: Option<f64>,
        above: Option<f64>,
    },
}

impl StopCondition {
    fn triggered(&self, _tick: usize, companies: &[TickCompany]) -> bool {
        match *self {
            StopCondition::PriceOutside {
                company,
                below,
                above,
            } => {
                let Some(c) = companies.get(company) else {
                    return false;
                };
                let price = c.stock.price;
                below.is_some_and(|b| price < b) || above.is_some_and(|a| price > a)
            }
        }
    }
}

#[non_exhaustive]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SessionOutcome {
    pub draws_consumed: usize,
    /// `Some(tick)` when a [`StopCondition`] fired. The close is NOT run in
    /// that case — the day is not over, the caller interrupted it.
    pub halted_at: Option<usize>,
}

/// A reusable per-day columnar buffer.
///
/// Row-major, `tick * companies + company`, so one company's path is a strided
/// read and one tick's cross-section is contiguous. The cross-section is the
/// hot direction: emission is per tick.
///
/// `f64` only. See [`Engine::run_session`] for why there is no `f32` variant.
#[non_exhaustive]
#[derive(Debug, Default, Clone, PartialEq)]
pub struct SessionBuffer {
    pub companies: usize,
    /// How many ticks the last session actually wrote — less than capacity
    /// when a [`StopCondition`] fired.
    pub ticks_written: usize,
    pub prices: Vec<f64>,
    pub volumes: Vec<f64>,
    pub mispricing_s: Vec<f64>,
    pub fundamental: Vec<f64>,
    pub anchor: Vec<f64>,
    /// The component columns, each `ticks * companies`: the eight
    /// `S_COMPONENT_KEYS` in order, then the tick's fair-value shift
    /// (`TICK_COMPONENT_COUNT` in all). Flat buffers rather than one of
    /// `[f64; TICK_COMPONENT_COUNT]`, because each becomes an Arrow column
    /// and a column wants a contiguous run of its own values.
    pub components: [Vec<f64>; crate::market::factors::TICK_COMPONENT_COUNT],
    /// The print decomposition, each `ticks * companies`: the shock that
    /// arrived and the depth that absorbed it, in log units.
    pub shock: Vec<f64>,
    pub absorbed: Vec<f64>,
    /// How far the print breaker moved each print. `absorbed - clamp` is the
    /// book's own share of the distance from the model price to the tape.
    pub clamp: Vec<f64>,
    /// What was written to each price between its last print and the
    /// tick, in log units. `repriced + shock + absorbed` is the print's log
    /// move from the previous print.
    pub repriced: Vec<f64>,
    /// The depth counterfactual, each `ticks * companies` when it ran and
    /// EMPTY when it did not. Emptiness is the signal, which is why these
    /// two are not resized alongside the columns above.
    pub unbounded_print: Vec<f64>,
    pub liquidity_share: Vec<f64>,
}

/// One tick's ground truth, as the engine holds it, handed to
/// `SessionBuffer::write_tick`.
///
/// A struct rather than nine positional slices. The call site passes nine
/// same-typed buffers and a transposition there would compile, run, and
/// mislabel every row of every column it touched.
#[non_exhaustive]
pub struct TickTruth<'a> {
    pub components: &'a [[f64; crate::market::factors::TICK_COMPONENT_COUNT]],
    pub fundamental: &'a [f64],
    pub anchor: &'a [f64],
    pub shock: &'a [f64],
    pub absorbed: &'a [f64],
    pub clamp: &'a [f64],
    /// What was written to each price between its last print and this
    /// tick (`Engine::tick_repriced`).
    pub repriced: &'a [f64],
    pub unbounded_print: &'a [f64],
    pub liquidity_share: &'a [f64],
}

impl SessionBuffer {
    /// An empty buffer. [`Engine::run_session`] sizes it to the session on
    /// each call, so one buffer serves every day of a run.
    pub fn new() -> Self {
        Self::default()
    }

    fn resize(&mut self, ticks: usize, companies: usize) {
        let needed = ticks * companies;
        if self.prices.len() != needed {
            self.prices.resize(needed, 0.0);
            self.volumes.resize(needed, 0.0);
            self.mispricing_s.resize(needed, 0.0);
            self.fundamental.resize(needed, f64::NAN);
            self.anchor.resize(needed, f64::NAN);
            self.shock.resize(needed, 0.0);
            self.absorbed.resize(needed, 0.0);
            self.clamp.resize(needed, 0.0);
            self.repriced.resize(needed, 0.0);
            for column in self.components.iter_mut() {
                column.resize(needed, 0.0);
            }
        }
        self.companies = companies;
        self.ticks_written = ticks;
    }

    /// Size the counterfactual columns for a session that is about to run
    /// with the arm on, or empty them for one that is not.
    ///
    /// Separate from [`SessionBuffer::resize`] because these two columns are
    /// the only ones whose PRESENCE carries information. Resizing them
    /// unconditionally would leave a run with the arm off holding a full
    /// column of zeros, which reads as "liquidity moved nothing" rather than
    /// as "nobody asked".
    fn resize_counterfactual(&mut self, on: bool) {
        let needed = if on { self.prices.len() } else { 0 };
        if self.unbounded_print.len() != needed {
            self.unbounded_print.clear();
            self.liquidity_share.clear();
            self.unbounded_print.resize(needed, f64::NAN);
            self.liquidity_share.resize(needed, 0.0);
        }
    }

    fn write_tick(&mut self, tick: usize, companies: &[TickCompany], truth: &TickTruth<'_>) {
        let base = tick * self.companies;
        for (i, c) in companies.iter().enumerate() {
            self.prices[base + i] = c.stock.price;
            self.volumes[base + i] = c.stock.volume;
            // NaN for a company that has never ticked — a column cannot carry
            // `None`, and zero would be a real mispricing.
            self.mispricing_s[base + i] = c.stock.mispricing_s.unwrap_or(f64::NAN);
            self.fundamental[base + i] = truth.fundamental.get(i).copied().unwrap_or(f64::NAN);
            self.anchor[base + i] = truth.anchor.get(i).copied().unwrap_or(f64::NAN);
            self.shock[base + i] = truth.shock.get(i).copied().unwrap_or(0.0);
            self.absorbed[base + i] = truth.absorbed.get(i).copied().unwrap_or(0.0);
            self.clamp[base + i] = truth.clamp.get(i).copied().unwrap_or(0.0);
            self.repriced[base + i] = truth.repriced.get(i).copied().unwrap_or(0.0);
            // Guarded on the buffer rather than on the source: a session that
            // sized these columns and then met an engine with the arm off
            // would otherwise write NaN into a column it had promised.
            if !self.unbounded_print.is_empty() {
                self.unbounded_print[base + i] = truth
                    .unbounded_print
                    .get(i)
                    .copied()
                    .unwrap_or(c.stock.price);
                self.liquidity_share[base + i] =
                    truth.liquidity_share.get(i).copied().unwrap_or(0.0);
            }
            let row = truth.components.get(i).copied().unwrap_or([0.0; crate::market::factors::TICK_COMPONENT_COUNT]);
            for (k, column) in self.components.iter_mut().enumerate() {
                column[base + i] = row[k];
            }
        }
    }

    /// Write the rate instruments' rows for one tick, after the equities'.
    ///
    /// The truth columns carry what exists for an index: the model price is
    /// the index level (as both `fundamental` and `anchor`), `s` is zero, the
    /// equity components are zero, and the print decomposition is the book's.
    /// The depth counterfactual never runs on an index, so its print there is
    /// the print and its share zero.
    fn write_rates(&mut self, tick: usize, first: usize, rates: &crate::rates::RateBook) {
        let base = tick * self.companies + first;
        for (j, inst) in rates.instruments.iter().enumerate() {
            let at = base + j;
            self.prices[at] = inst.price;
            self.volumes[at] = inst.volume;
            self.mispricing_s[at] = 0.0;
            self.fundamental[at] = inst.level;
            self.anchor[at] = inst.level;
            self.shock[at] = inst.tick_shock;
            self.absorbed[at] = inst.tick_absorbed;
            self.clamp[at] = 0.0;
            // What the close's re-mark, a pin's and the open wrote to the
            // price since the last print (`rate_close_remark`); zero on
            // every engine without it, where nothing writes between prints
            // but the open, which this column has never booked for a rate
            // row.
            self.repriced[at] = inst.tick_repriced;
            if !self.unbounded_print.is_empty() {
                self.unbounded_print[at] = inst.price;
                self.liquidity_share[at] = 0.0;
            }
            for column in self.components.iter_mut() {
                column[at] = 0.0;
            }
        }
    }

    /// One tick's cross-section, contiguous.
    pub fn tick_prices(&self, tick: usize) -> &[f64] {
        let base = tick * self.companies;
        &self.prices[base..base + self.companies]
    }
}

/// A fixed simulation, hashed — the cross-binding determinism probe.
///
/// Every binding calls THIS rather than hashing state on its own side. A
/// check implemented twice is a fork of the check: the first attempt at
/// this compared a wasm digest against one rebuilt in Python, and the two
/// disagreed because the Python surface reports rates as fractions while
/// the core carries percent. That is a units bug in the harness reported as
/// a determinism failure, which is precisely the confusion a shared
/// implementation removes.
///
/// Hashing rules follow `tests/known_answer.py`: raw big-endian IEEE-754
/// bytes, no decimal formatting anywhere, because a float formatter would
/// make the digest depend on something other than the simulation.
///
/// Prices are tick-rounded to cents, so a price-only digest can agree while
/// two builds have drifted below that. The macro state is carried at full
/// precision and sits downstream of the whole day chain, so it is the part
/// of this probe that can see a low-bit difference.
///
/// Each day runs the core's numbered day, the loop the WebAssembly `Sim`
/// runs: [`Engine::set_current_day`] with the day counted from zero,
/// [`Engine::open_market`], one session and [`Engine::close_day`]. Through
/// 0.10.x
/// the days were not numbered, so on a preset that reads the day (the
/// buyback yield from pt-v18, the earnings and dividend calendars on pt-v21)
/// a run of more than one day hashes differently from those releases.
///
/// Returns `None` for an unknown preset.
pub fn fixed_simulation_digest(
    size: usize,
    universe_seed: u64,
    seed: u64,
    days: usize,
    ticks: usize,
    preset: &str,
) -> Option<String> {
    use sha2::{Digest, Sha256};

    let params = crate::params::ModelParams::preset(preset)?;
    let generated = crate::universe::random_universe(size, universe_seed);
    let companies: Vec<TickCompany> = generated
        .iter()
        .enumerate()
        .map(|(i, g)| g.to_init().to_tick_company(i))
        .collect();
    let mut engine = Engine::with_params(
        seed,
        companies,
        crate::economy::create_initial_economy_state(
            &crate::economy::InitialEconomyOptions::default()),
        crate::economy::create_initial_central_bank_state(0),
        crate::sectors::keys().iter().map(|s| s.to_string()).collect(),
        params,
    );
    let mut buffer = SessionBuffer::new();
    for day in 0..days {
        engine.run_numbered_day(day as i64, ticks, &mut buffer);
    }

    // NaN IS THE ONE PLACE WEBASSEMBLY IS LOOSE.
    //
    // The wasm specification pins IEEE-754 exactly for add, subtract,
    // multiply, divide and square root, which is why a browser build can
    // agree with a native one at all. It deliberately does NOT pin NaN
    // payload bits. So hashing a NaN would compare a bit pattern the spec
    // permits two engines to choose differently -- a digest that could
    // disagree for a reason that is not the model, which is precisely the
    // failure this probe exists to detect.
    //
    // Measured: zero NaN and zero infinities across 16,800 price samples
    // (seven seeds x sixty days x forty names) plus the macro state. So this
    // does not fire today. It is a guard rather than a hope, because "we
    // have never seen one" is not the same claim as "there cannot be one",
    // and the failure it prevents is a determinism report that is wrong in
    // the reassuring direction.
    let mut hasher = Sha256::new();
    for price in engine.prices() {
        if !price.is_finite() {
            return None;
        }
        hasher.update(price.to_be_bytes());
    }
    let e = engine.economy();
    for v in [e.vix, e.federal_funds_rate, e.inflation_rate,
              e.corporate_bond_yield, e.fear_greed_index] {
        if !v.is_finite() {
            return None;
        }
        hasher.update(v.to_be_bytes());
    }
    Some(format!("{:x}", hasher.finalize()))
}


/// Divide each public name's beta by `B^power`, `B` the roster's
/// cap-weighted beta at the opening caps (`market_beta_normalise`).
///
/// `B` is taken over the names that are public and not bankrupt, with a
/// positive finite market cap, reading an absent beta as 1.0 (the loading
/// every reader gives it). A roster with no such name, or a `B` that is not
/// positive and finite, is returned as it came: there is no index to
/// normalise against. The names outside the sum keep their betas too, so a
/// name that lists later carries the beta it was given, as `add_company`'s
/// does.
pub(crate) fn normalise_roster_beta(mut companies: Vec<TickCompany>, power: f64) -> Vec<TickCompany> {
    let mut cap = 0.0;
    let mut weighted = 0.0;
    for c in companies.iter() {
        if !c.is_public || c.is_bankrupt {
            continue;
        }
        let m = c.stock.market_cap;
        if !(m.is_finite() && m > 0.0) {
            continue;
        }
        cap += m;
        weighted += m * c.stock.beta.unwrap_or(1.0);
    }
    if !(cap > 0.0) {
        return companies;
    }
    let b = weighted / cap;
    if !(b.is_finite() && b > 0.0) {
        return companies;
    }
    let k = if power == 1.0 { 1.0 / b } else { crate::mathx::pow(b, -power) };
    for c in companies.iter_mut() {
        if !c.is_public || c.is_bankrupt {
            continue;
        }
        let m = c.stock.market_cap;
        if !(m.is_finite() && m > 0.0) {
            continue;
        }
        c.stock.beta = Some(c.stock.beta.unwrap_or(1.0) * k);
    }
    companies
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::economy::{
        create_initial_central_bank_state, create_initial_economy_state, InitialEconomyOptions,
    };
    use crate::market::TickStock;

    fn sectors() -> Vec<String> {
        ["technology", "energy", "healthcare"]
            .iter()
            .map(|s| s.to_string())
            .collect()
    }

    fn company(id: &str, price: f64) -> TickCompany {
        TickCompany {
            id: id.to_string(),
            ticker: id.to_string(),
            sector: "technology".to_string(),
            is_bankrupt: false,
            is_public: true,
            sector_volatility: Some(1.0),
            sector_avg_pe: Some(32.0),
            eps: Some(4.0),
            book_value_per_share: Some(20.0),
            revenue_growth: Some(0.1),
            stock: TickStock {
                price,
                previous_close: price,
                previous_tick_price: None,
                open: price,
                high: price,
                low: price,
                volume: 0.0,
                avg_volume: 1e6,
                shares_outstanding: 1e8,
                market_cap: price * 1e8,
                mispricing_s: None,
                mispricing_s_prev_close: None,
                mispricing_momentum: None,
                fair_value_offset: None,
                buyback_log_shares: None,
                dividend: None,
                maker_inventory: None,
                garch_variance: 0.015 * 0.015,
                garch_cascade: [0.015 * 0.015; crate::market::garch::CASCADE_MAX],
                last_daily_return: None,
                beta: Some(1.0),
                short_interest: 0.0,
                float: 1e8,
            },
        }
    }

    fn engine(seed: u64) -> Engine {
        Engine::new(
            seed,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
        )
    }

    /// [`engine`] under pt-v19: the snapshot-priced flow path, where an
    /// agent's fills reach the first tick's `order_volumes`. pt-v20 and
    /// pt-v21 turn `fill_impact_coefficient` on and routes them
    /// through the agent-facing book's pending flow instead.
    fn engine_v19(seed: u64) -> Engine {
        Engine::with_params(
            seed,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            crate::params::PT_V19,
        )
    }

    /// [`engine`] under pt-v20 by name: the default until 0.10.0, and the
    /// preset with every one of pt-v21's dials off, which the tests of those
    /// dials read as "off". They read `engine(seed)` while pt-v20 was the
    /// default.
    fn engine_v20(seed: u64) -> Engine {
        Engine::with_params(
            seed,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            crate::params::PT_V20,
        )
    }

    /// The business cycle in the market factor's volatility
    /// (`market_vol_cycle_ratio` and its three companions).
    /// `market_day_tail_df` and `market_day_tail_state_share`: off, the day
    /// scale is never drawn, read or hashed; on, each open draws one on the
    /// overnight stream alone, the session's market sigma carries its root,
    /// the close clears it, and the state hash covers it while it is live.
    /// `market_beta_normalise`: off, every beta is the one the company came
    /// with; on, the roster's cap-weighted beta is divided out at
    /// construction, names outside the index keep theirs, and the market
    /// moves.
    mod market_beta_normalise {
        use super::*;

        fn roster() -> Vec<TickCompany> {
            let mut a = company("A", 100.0);
            let mut b = company("B", 50.0);
            let mut c = company("C", 220.0);
            let mut d = company("D", 80.0);
            a.stock.beta = Some(1.3);
            b.stock.beta = Some(0.8);
            c.stock.beta = None; // read as 1.0
            d.stock.beta = Some(2.0);
            d.is_bankrupt = true; // outside the index, keeps its beta
            vec![a, b, c, d]
        }

        fn with(d: f64) -> Engine {
            let mut params = crate::params::PT_V20;
            params.market_beta_normalise = d;
            Engine::with_params(
                13,
                roster(),
                create_initial_economy_state(&InitialEconomyOptions::default()),
                create_initial_central_bank_state(0),
                sectors(),
                params,
            )
        }

        fn cap_weighted_beta(e: &Engine) -> f64 {
            let (mut cap, mut w) = (0.0, 0.0);
            for c in e.companies().iter().filter(|c| c.is_public && !c.is_bankrupt) {
                cap += c.stock.market_cap;
                w += c.stock.market_cap * c.stock.beta.unwrap_or(1.0);
            }
            w / cap
        }

        #[test]
        fn off_every_beta_is_the_one_given() {
            let e = with(0.0);
            let got: Vec<Option<f64>> = e.companies().iter().map(|c| c.stock.beta).collect();
            let want: Vec<Option<f64>> = roster().iter().map(|c| c.stock.beta).collect();
            assert_eq!(got, want);
        }

        #[test]
        fn on_the_roster_beta_is_one_and_the_ratios_hold() {
            let before = {
                let r = roster();
                let (mut cap, mut w) = (0.0, 0.0);
                for c in r.iter().filter(|c| !c.is_bankrupt) {
                    cap += c.stock.market_cap;
                    w += c.stock.market_cap * c.stock.beta.unwrap_or(1.0);
                }
                w / cap
            };
            assert!((before - 1.0).abs() > 0.05, "{before}");
            let e = with(1.0);
            assert!((cap_weighted_beta(&e) - 1.0).abs() < 1e-12, "{}", cap_weighted_beta(&e));
            let b: Vec<f64> = e.companies().iter().map(|c| c.stock.beta.unwrap()).collect();
            assert!((b[0] / b[1] - 1.3 / 0.8).abs() < 1e-12);
            assert!((b[2] - 1.0 / before).abs() < 1e-12, "an absent beta reads 1.0");
            assert_eq!(b[3], 2.0, "a bankrupt name is outside the index and keeps its beta");
            // Half the power leaves half the log distance.
            let h = with(0.5);
            assert!((cap_weighted_beta(&h) - before.sqrt()).abs() < 1e-12);
        }

        #[test]
        fn on_the_market_moves() {
            let mut on = with(1.0);
            let mut off = with(0.0);
            for e in [&mut on, &mut off] {
                e.open_market();
                for m in 0..10 {
                    e.tick(&request(10, m));
                }
            }
            assert_ne!(on.prices(), off.prices());
        }
    }

    mod market_day_tail {
        use super::*;

        fn with(dials: impl FnOnce(&mut crate::params::ModelParams)) -> Engine {
            let mut params = crate::params::PT_V20;
            dials(&mut params);
            Engine::with_params(
                13,
                vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
                create_initial_economy_state(&InitialEconomyOptions::default()),
                create_initial_central_bank_state(0),
                sectors(),
                params,
            )
        }

        fn session_and_close(e: &mut Engine) {
            for m in 0..10 {
                e.tick(&request(10, m));
            }
            let innovations: Vec<Option<f64>> =
                e.daily_innovation_column().into_iter().map(Some).collect();
            let variances = e.sector_base_variances();
            e.close_market(&DayCloseRequest {
                daily_innovations: &innovations,
                sector_base_variances: &variances,
                avg_volume: crate::market::AvgVolumePolicy::Hold,
            });
        }

        #[test]
        fn off_the_day_scale_is_never_drawn_or_hashed() {
            let mut off = with(|_| {});
            assert_eq!(off.params().market_day_tail_df, 0.0);
            let mut twin = off.clone();
            for _ in 0..3 {
                off.open_market();
                assert_eq!(off.market_day_scale(), 1.0);
                assert_eq!(off.market_sigma_today(), off.market_sigma_before_day_scale());
                session_and_close(&mut off);
            }
            // The state share alone reads nothing.
            let mut share = with(|p| p.market_day_tail_state_share = 1.0);
            for _ in 0..3 {
                twin.open_market();
                share.open_market();
                session_and_close(&mut twin);
                session_and_close(&mut share);
            }
            assert_eq!(share.prices(), twin.prices());
        }

        #[test]
        fn on_the_open_draws_a_scale_the_session_reads_and_the_close_clears() {
            let mut on = with(|p| p.market_day_tail_df = 6.0);
            let mut off = with(|_| {});
            on.open_market();
            off.open_market();
            let m = on.market_day_scale();
            assert!(m > 0.0 && m != 1.0 && m.is_finite(), "{m}");
            let sigma = on.market_sigma_before_day_scale();
            assert!((on.market_sigma_today() - sigma * m.sqrt()).abs() <= 1e-15 * sigma.max(1.0));
            // Only the overnight stream moved.
            assert_eq!(on.market_rng.snapshot(), off.market_rng.snapshot());
            assert_ne!(on.overnight_rng.snapshot(), off.overnight_rng.snapshot());
            // Hashed while live: the same state with another scale hashes apart.
            let mut other = on.clone();
            other.set_market_day_scale(m * 1.5);
            assert_ne!(other.state_hash(0, false), on.state_hash(0, false));
            session_and_close(&mut on);
            assert_eq!(on.market_day_scale(), 1.0);
        }

        #[test]
        fn the_scale_has_mean_one() {
            let nu = 10.0;
            let mut e = with(|p| p.market_day_tail_df = nu);
            let n = 4000;
            let mut sum = 0.0;
            for _ in 0..n {
                e.draw_market_day_scale();
                sum += e.market_day_scale();
            }
            let mean = sum / n as f64;
            // Inverse gamma: variance 2 / (nu - 4).
            let se = (2.0 / (nu - 4.0) / n as f64).sqrt();
            assert!((mean - 1.0).abs() < 4.0 * se, "{mean}");
        }
    }

    mod market_vol_cycle {
        use super::*;
        use crate::economy::CyclePhase;

        fn with(dials: impl FnOnce(&mut crate::params::ModelParams)) -> Engine {
            let mut params = crate::params::PT_V20;
            dials(&mut params);
            Engine::with_params(
                11,
                vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
                create_initial_economy_state(&InitialEconomyOptions::default()),
                create_initial_central_bank_state(0),
                sectors(),
                params,
            )
        }

        /// The four dials, with `relative` as the power on both sides of
        /// one (`market_vol_cycle_relative` and
        /// `market_vol_cycle_relative_calm` alike).
        fn on(ratio: f64, expansion: f64, half_life: f64, relative: f64) -> Engine {
            on_split(ratio, expansion, half_life, relative, relative)
        }

        fn on_split(ratio: f64, expansion: f64, half_life: f64, relative: f64, calm: f64) -> Engine {
            with(|p| {
                p.market_vol_cycle_ratio = ratio;
                p.market_vol_cycle_expansion = expansion;
                p.market_vol_cycle_half_life = half_life;
                p.market_vol_cycle_relative = relative;
                p.market_vol_cycle_relative_calm = calm;
            })
        }

        /// A session of ticks and the market's close, without the macro
        /// step, so the economy the close read is still there to compare.
        fn session_and_close(e: &mut Engine) {
            e.open_market();
            for m in 0..10 {
                e.tick(&request(10, m));
            }
            let innovations: Vec<Option<f64>> =
                e.daily_innovation_column().into_iter().map(Some).collect();
            let variances = e.sector_base_variances();
            e.close_market(&DayCloseRequest {
                daily_innovations: &innovations,
                sector_base_variances: &variances,
                avg_volume: crate::market::AvgVolumePolicy::Hold,
            });
        }

        #[test]
        fn off_the_multiplier_is_never_set_read_or_hashed() {
            // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
            // this test reads as off. Each `engine_v20` here read `engine`.
            let mut e = engine_v20(11);
            assert_eq!(e.params().market_vol_cycle_ratio, 0.0);
            for day in 1..=5 {
                e.close_day(day);
            }
            assert_eq!(e.market_vol_cycle_log(), None);
            assert!(!e.carries_market_vol_cycle());
            assert_eq!(e.market_vol_cycle_vix_scale(), 1.0);
            // The companions alone are unread.
            let mut c = on(0.0, 0.75, 21.0, 1.0);
            for day in 1..=5 {
                c.close_day(day);
            }
            assert_eq!(c.market_vol_cycle_log(), None);
            let mut d = engine_v20(11);
            for day in 1..=5 {
                d.close_day(day);
            }
            assert_eq!(c.market_variance_state(), d.market_variance_state());
        }

        #[test]
        fn the_target_reads_the_true_phase() {
            let mut e = on(2.0, 0.75, 21.0, 1.0);
            for (phase, k) in [
                (CyclePhase::Expansion, 0.75),
                (CyclePhase::Peak, 0.75),
                (CyclePhase::Contraction, 1.5),
                (CyclePhase::Trough, 1.5),
                (CyclePhase::Recovery, 0.75),
            ] {
                e.economy_mut().cycle_phase = phase;
                let got = e.market_vol_cycle_target_log();
                assert!((got - crate::mathx::log(k)).abs() < 1e-15, "{phase:?}: {got}");
            }
        }

        #[test]
        fn the_derived_expansion_multiplier_keeps_the_share_weighted_variance() {
            let mut e = on(2.0, 0.0, 0.0, 1.0);
            let (mean, cycle) =
                crate::economy::cycle::stationary_phase_shares_for(&e.cycle_spec());
            let mut weighted = 0.0;
            for (k, phase) in crate::economy::cycle::phase_cycle().iter().enumerate() {
                e.economy_mut().cycle_phase = *phase;
                let m = crate::mathx::exp(e.market_vol_cycle_target_log());
                weighted += mean[k] / cycle * m * m;
            }
            assert!((weighted - 1.0).abs() < 1e-12, "{weighted}");
            // And the contraction's is R times the expansion's.
            e.economy_mut().cycle_phase = CyclePhase::Expansion;
            let exp = e.market_vol_cycle_target_log();
            e.economy_mut().cycle_phase = CyclePhase::Trough;
            let tr = e.market_vol_cycle_target_log();
            assert!((tr - exp - crate::mathx::log(2.0)).abs() < 1e-14);
            assert!(exp < 0.0);
        }

        #[test]
        fn the_first_step_lands_on_the_target_and_the_gap_halves_at_the_half_life() {
            let mut e = on(2.0, 0.75, 10.0, 1.0);
            e.economy_mut().cycle_phase = CyclePhase::Expansion;
            let first = e.step_market_vol_cycle();
            assert_eq!(first, crate::mathx::log(0.75));
            e.economy_mut().cycle_phase = CyclePhase::Contraction;
            let target = crate::mathx::log(1.5);
            let mut l = first;
            for _ in 0..10 {
                l = e.step_market_vol_cycle();
            }
            let half = 0.5 * (target - first);
            assert!(((target - l) - half).abs() < 1e-12, "{l}");
            // Instant at a half-life of 0.
            let mut i = on(2.0, 0.75, 0.0, 1.0);
            i.economy_mut().cycle_phase = CyclePhase::Expansion;
            i.step_market_vol_cycle();
            i.economy_mut().cycle_phase = CyclePhase::Trough;
            assert_eq!(i.step_market_vol_cycle(), target);
        }

        #[test]
        fn the_vix_scale_is_the_multiplier_to_the_power_relative() {
            let mut e = on(2.0, 0.75, 21.0, 0.5);
            assert_eq!(e.market_vol_cycle_vix_scale(), 1.0, "before the first close");
            e.set_market_vol_cycle_log(Some(0.4));
            assert_eq!(e.market_vol_cycle_vix_scale(), crate::mathx::exp(0.2));
            let mut z = on(2.0, 0.75, 21.0, 0.0);
            z.set_market_vol_cycle_log(Some(0.4));
            assert_eq!(z.market_vol_cycle_vix_scale(), 1.0);
        }

        /// The power on each side of one: `relative` at or over it, and
        /// `relative_calm` under it.
        #[test]
        fn the_vix_scale_takes_the_calm_power_under_one() {
            let mut e = on_split(2.0, 0.75, 21.0, 0.8, 0.3);
            e.set_market_vol_cycle_log(Some(0.4));
            assert_eq!(e.market_vol_cycle_vix_scale(), crate::mathx::exp(0.8 * 0.4));
            e.set_market_vol_cycle_log(Some(-0.25));
            assert_eq!(e.market_vol_cycle_vix_scale(), crate::mathx::exp(0.3 * -0.25));
            e.set_market_vol_cycle_log(Some(0.0));
            assert_eq!(e.market_vol_cycle_vix_scale(), 1.0);
            // A calm power of 0 leaves a calm phase's fear on the
            // unconditional level, and a stormy one still scaled.
            let mut c = on_split(2.0, 0.75, 21.0, 1.0, 0.0);
            c.set_market_vol_cycle_log(Some(-0.25));
            assert_eq!(c.market_vol_cycle_vix_scale(), 1.0);
            c.set_market_vol_cycle_log(Some(0.4));
            assert_eq!(c.market_vol_cycle_vix_scale(), crate::mathx::exp(0.4));
        }

        /// The fair-value cap's ceiling is scaled by the multiplier to the
        /// power `market_vol_cycle_cap_relative` over one only, and the
        /// permanent share reads the scaled ceiling.
        #[test]
        fn the_cap_scale_reads_a_stormy_phase_only() {
            let mut e = with(|p| {
                p.market_vol_cycle_ratio = 2.0;
                p.market_vol_cycle_expansion = 0.75;
                p.market_vol_cycle_cap_relative = 0.5;
            });
            assert_eq!(e.market_vol_cycle_cap_scale(), 1.0, "before the first close");
            e.set_market_vol_cycle_log(Some(0.4));
            assert_eq!(e.market_vol_cycle_cap_scale(), crate::mathx::exp(0.2));
            e.set_market_vol_cycle_log(Some(-0.3));
            assert_eq!(e.market_vol_cycle_cap_scale(), 1.0);
            let mut z = on(2.0, 0.75, 21.0, 1.0);
            z.set_market_vol_cycle_log(Some(0.4));
            assert_eq!(z.market_vol_cycle_cap_scale(), 1.0, "power 0");
            let p = e.params().clone();
            let base = p.fair_value_market_vol_cap * p.market_factor_sigma;
            let sigma = 1.5 * base;
            let unscaled = crate::market::tick::market_permanent_share(&p, sigma, 1.0);
            assert!((unscaled - p.fair_value_market_share / 1.5).abs() < 1e-15);
            // A ceiling scaled past the sigma leaves the whole share.
            assert_eq!(crate::market::tick::market_permanent_share(&p, sigma, 1.6),
                       p.fair_value_market_share);
        }

        /// The scale never takes the denominator or the anchor under the
        /// VIX's own floor.
        #[test]
        fn the_vix_scale_is_floored_at_the_vix_floor_over_the_anchor() {
            let mut e = on(0.05, 1.0, 0.0, 1.0);
            // A close derives the anchor and sets the multiplier.
            e.close_day(1);
            assert!(e.vix_anchor > 0.0);
            let floor = crate::params::VIX_STATE_FLOOR / e.vix_anchor;
            e.set_market_vol_cycle_log(Some(crate::mathx::log(0.05)));
            assert_eq!(e.market_vol_cycle_vix_scale(), floor);
            // Above the floor it is the power, untouched.
            e.set_market_vol_cycle_log(Some(-0.1));
            assert_eq!(e.market_vol_cycle_vix_scale(), crate::mathx::exp(-0.1));
        }

        /// A free close scales the variance baseline by the multiplier
        /// squared: at `relative` 0 the coupling reads the same ratio, so
        /// the fast target is the off engine's times `m^2`. The first
        /// session trades identically on both engines (nothing is applied
        /// before a close), so the close reads the same state.
        #[test]
        fn a_free_close_scales_the_baseline_by_the_multiplier_squared() {
            // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
            // this test reads as off. Each `engine_v20` here read `engine`.
            let mut off = engine_v20(11);
            let mut e = on(2.0, 0.75, 21.0, 0.0);
            let phase = e.economy().cycle_phase;
            assert_eq!(phase, off.economy().cycle_phase);
            let k: f64 = match phase {
                CyclePhase::Contraction | CyclePhase::Trough => 1.5,
                _ => 0.75,
            };
            session_and_close(&mut off);
            session_and_close(&mut e);
            let (t_off, _) = off.market_variance_target().unwrap();
            let (t_on, _) = e.market_variance_target().unwrap();
            assert!((t_on / t_off - k * k).abs() < 1e-12, "{} against {}", t_on / t_off, k * k);
            assert_ne!(e.market_variance_state(), off.market_variance_state());
            // At `relative` 1 the coupling reads fear against the phase's
            // level instead, which is a different target.
            let mut d1 = on(2.0, 0.75, 21.0, 1.0);
            session_and_close(&mut d1);
            let (t_d1, _) = d1.market_variance_target().unwrap();
            assert_ne!(t_d1, t_on);
        }

        /// The anchor's slow memory reads the day's fear against the
        /// SCALED anchor. A forced close writes the same variance on both
        /// engines, so the read-back is the same, and the memory of an
        /// engine at power 1 then differs from one at power 0 by exactly
        /// `-h ln(scale)`, `h` being `vix_anchor_memory`.
        #[test]
        fn the_anchor_memory_reads_fear_against_the_scaled_anchor() {
            let mut a = on(2.0, 0.75, 0.0, 1.0);
            let mut b = on(2.0, 0.75, 0.0, 0.0);
            assert!(a.params().vix_anchor_memory > 0.0);
            for e in [&mut a, &mut b] {
                e.set_vix_sets_variance_pending(true);
                session_and_close(e);
            }
            assert_eq!(a.market_variance_state(), b.market_variance_state());
            assert_eq!(a.vix_anchor_slow(), b.vix_anchor_slow());
            let scale = a.market_vol_cycle_vix_scale();
            assert_ne!(scale, 1.0);
            assert_eq!(b.market_vol_cycle_vix_scale(), 1.0);
            a.advance_macro_day(1);
            b.advance_macro_day(1);
            let want = -a.params().vix_anchor_memory * crate::mathx::log(scale);
            let got = a.vix_anchor_slow() - b.vix_anchor_slow();
            assert!((got - want).abs() < 1e-12, "{got} against {want}");
        }

        /// The level the VIX reverts to is the scaled anchor. Without the
        /// slow memory the VIX's target is the geometric blend of the
        /// read-back and the anchor level, so after a forced close (the
        /// same read-back on both engines) a scale under one gives a lower
        /// VIX and a scale over one a higher one.
        #[test]
        fn the_vix_reverts_toward_the_scaled_anchor_level() {
            let mk = |d: f64| {
                with(|p| {
                    p.market_vol_cycle_ratio = 2.0;
                    p.market_vol_cycle_expansion = 0.75;
                    p.market_vol_cycle_relative = d;
                    p.market_vol_cycle_relative_calm = d;
                    p.vix_anchor_memory = 0.0;
                })
            };
            let (mut a, mut b) = (mk(1.0), mk(0.0));
            for e in [&mut a, &mut b] {
                e.set_vix_sets_variance_pending(true);
                session_and_close(e);
            }
            assert_eq!(a.market_variance_state(), b.market_variance_state());
            let scale = a.market_vol_cycle_vix_scale();
            assert_ne!(scale, 1.0);
            a.advance_macro_day(1);
            b.advance_macro_day(1);
            assert_ne!(a.economy().vix, b.economy().vix);
            assert_eq!(a.economy().vix < b.economy().vix, scale < 1.0);
        }

        /// A forced close (a VIX a scenario pinned) moves the multiplier
        /// but writes the variance the off engine writes.
        #[test]
        fn a_forced_close_moves_the_multiplier_but_does_not_apply_it() {
            // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
            // this test reads as off. Each `engine_v20` here read `engine`.
            let mut off = engine_v20(11);
            let mut e = on(2.0, 0.75, 21.0, 1.0);
            off.set_vix_sets_variance_pending(true);
            e.set_vix_sets_variance_pending(true);
            session_and_close(&mut off);
            session_and_close(&mut e);
            assert_eq!(e.market_variance_state(), off.market_variance_state());
            assert_eq!(e.market_variance_target(), off.market_variance_target());
            assert!(e.market_vol_cycle_log().is_some());
        }

        /// The trough gives back the share `market_vol_cycle_trough_release`
        /// of the contraction's excess, in logs; at 0.0 it is the
        /// contraction's, and the contraction's own is untouched.
        #[test]
        fn the_trough_gives_back_its_share_of_the_contraction() {
            for g in [0.0, 0.25, 1.0] {
                let mut e = with(|p| {
                    p.market_vol_cycle_ratio = 2.0;
                    p.market_vol_cycle_expansion = 0.75;
                    p.market_vol_cycle_trough_release = g;
                });
                e.economy_mut().cycle_phase = CyclePhase::Trough;
                let got = e.market_vol_cycle_target_log();
                let want = crate::mathx::log(0.75) + (1.0 - g) * crate::mathx::log(2.0);
                assert!((got - want).abs() < 1e-14, "g {g}: {got} against {want}");
                e.economy_mut().cycle_phase = CyclePhase::Contraction;
                assert_eq!(e.market_vol_cycle_target_log(), crate::mathx::log(1.5));
            }
            // At 0.0 the trough's target is the contraction's to the bit.
            let mut z = on(2.0, 0.75, 21.0, 1.0);
            z.economy_mut().cycle_phase = CyclePhase::Trough;
            assert_eq!(z.market_vol_cycle_target_log(), crate::mathx::log(1.5));
        }

        /// The rally off the low gives back `g s` of the excess in a
        /// contraction and of the trough's own share in a trough, `s` the
        /// index's rise off its lowest close since its high over the scale,
        /// capped at one; outside the two phases, and with the dial at 0.0,
        /// the target is as it was.
        #[test]
        fn the_rally_off_the_low_gives_back_its_share() {
            let mk = |g: f64, trough: f64| {
                with(|p| {
                    p.market_vol_cycle_ratio = 2.0;
                    p.market_vol_cycle_expansion = 0.75;
                    p.market_vol_cycle_trough_release = trough;
                    p.market_vol_cycle_recovery_release = g;
                    p.market_vol_cycle_recovery_scale = 0.2;
                })
            };
            // Up 0.1 to the high, down 0.3 to the low, up 0.05 off it.
            let path = [0.05, 0.05, -0.2, -0.1, 0.02, 0.03];
            let (lk, lr) = (crate::mathx::log(0.75), crate::mathx::log(2.0));
            for (g, trough) in [(1.0, 0.0), (0.5, 0.0), (1.0, 0.4)] {
                let mut e = mk(g, trough);
                assert!(e.keeps_drawdown_window());
                assert_eq!(e.index_rally_off_low(), 0.0);
                e.drawdown_returns = path.iter().copied().collect();
                let rally = e.index_rally_off_low();
                assert!((rally - 0.05).abs() < 1e-12, "{rally}");
                let s = rally / 0.2;
                e.economy_mut().cycle_phase = CyclePhase::Contraction;
                let want = lk + (1.0 - g * s) * lr;
                assert!((e.market_vol_cycle_target_log() - want).abs() < 1e-14);
                e.economy_mut().cycle_phase = CyclePhase::Trough;
                let want = lk + (1.0 - trough) * (1.0 - g * s) * lr;
                assert!((e.market_vol_cycle_target_log() - want).abs() < 1e-14);
                e.economy_mut().cycle_phase = CyclePhase::Expansion;
                assert_eq!(e.market_vol_cycle_target_log(), lk);
                // A rally past the scale, still under the high, gives back
                // all of g.
                e.drawdown_returns.push_back(0.2);
                e.economy_mut().cycle_phase = CyclePhase::Contraction;
                let want = lk + (1.0 - g) * lr;
                assert!((e.market_vol_cycle_target_log() - want).abs() < 1e-14);
                // A new high: no rally, the contraction's own target.
                e.drawdown_returns.push_back(0.5);
                assert_eq!(e.index_rally_off_low(), 0.0);
                assert!((e.market_vol_cycle_target_log() - (lk + lr)).abs() < 1e-14);
            }
            // At 0.0 no window is kept and a window read changes nothing.
            let mut z = mk(0.0, 0.0);
            assert!(!z.keeps_drawdown_window());
            assert!(z.drawdown_state().is_none());
            z.drawdown_returns = path.iter().copied().collect();
            z.economy_mut().cycle_phase = CyclePhase::Contraction;
            assert_eq!(z.market_vol_cycle_target_log(), crate::mathx::log(1.5));
        }

        /// The release half-life is read only while the multiplier falls;
        /// a rising multiplier, and every step at 0.0, takes the one
        /// half-life.
        #[test]
        fn the_release_half_life_is_read_only_on_the_way_down() {
            let mk = |release: f64| {
                with(|p| {
                    p.market_vol_cycle_ratio = 2.0;
                    p.market_vol_cycle_expansion = 0.75;
                    p.market_vol_cycle_half_life = 20.0;
                    p.market_vol_cycle_release_half_life = release;
                })
            };
            let step = |h: f64, from: f64, to: f64| {
                from + (1.0 - crate::mathx::exp(-std::f64::consts::LN_2 / h)) * (to - from)
            };
            let (up, down) = (crate::mathx::log(1.5), crate::mathx::log(0.75));
            // Down: from the contraction's level to the expansion's.
            let mut e = mk(5.0);
            e.economy_mut().cycle_phase = CyclePhase::Expansion;
            e.set_market_vol_cycle_log(Some(up));
            assert_eq!(e.step_market_vol_cycle(), step(5.0, up, down));
            // Up: the one half-life.
            e.economy_mut().cycle_phase = CyclePhase::Contraction;
            e.set_market_vol_cycle_log(Some(down));
            assert_eq!(e.step_market_vol_cycle(), step(20.0, down, up));
            // Off: the one half-life both ways.
            let mut o = mk(0.0);
            o.economy_mut().cycle_phase = CyclePhase::Expansion;
            o.set_market_vol_cycle_log(Some(up));
            assert_eq!(o.step_market_vol_cycle(), step(20.0, up, down));
        }

        /// `market_vol_cycle_pin_neutral`: on a session whose VIX a caller
        /// pinned, the close writes the variance target an engine without
        /// the multiplier writes, and the multiplier lands on one (its
        /// first step lands on its target, which a pin makes one). Without
        /// the switch the same pinned session applies it.
        #[test]
        fn a_pinned_vix_session_does_not_apply_the_multiplier_under_the_switch() {
            let run = |ratio: f64, switch: f64| {
                let mut e = with(|p| {
                    p.market_vol_cycle_ratio = ratio;
                    p.market_vol_cycle_expansion = 0.75;
                    p.market_vol_cycle_half_life = 21.0;
                    p.market_vol_cycle_relative = 1.0;
                    p.market_vol_cycle_relative_calm = 1.0;
                    p.market_vol_cycle_cap_relative = 1.0;
                    p.market_vol_cycle_pin_neutral = switch;
                });
                e.mark_macro_pins_today(PIN_VIX);
                assert_ne!(e.macro_pins_today() & PIN_VIX, 0, "the pin is kept");
                session_and_close(&mut e);
                e
            };
            let off = run(0.0, 0.0);
            let on = run(2.0, 1.0);
            assert_eq!(on.market_variance_target(), off.market_variance_target());
            assert_eq!(on.market_variance_state(), off.market_variance_state());
            assert_eq!(on.market_vol_cycle_log(), Some(0.0));
            assert_eq!(on.market_vol_cycle_vix_scale(), 1.0);
            assert_eq!(on.market_vol_cycle_cap_scale(), 1.0);
            let unswitched = run(2.0, 0.0);
            assert_ne!(unswitched.market_variance_target(), off.market_variance_target());
            assert_ne!(unswitched.market_vol_cycle_log(), Some(0.0));
            // An unpinned session under the switch applies the multiplier.
            let mut free = with(|p| {
                p.market_vol_cycle_ratio = 2.0;
                p.market_vol_cycle_expansion = 0.75;
                p.market_vol_cycle_pin_neutral = 1.0;
            });
            let mut plain = with(|p| {
                p.market_vol_cycle_ratio = 2.0;
                p.market_vol_cycle_expansion = 0.75;
            });
            session_and_close(&mut free);
            session_and_close(&mut plain);
            assert_eq!(free.market_variance_target(), plain.market_variance_target());
            assert_eq!(free.market_vol_cycle_log(), plain.market_vol_cycle_log());
        }

        /// `market_vol_cycle_pin_phase`: the same rule on a session whose
        /// phase a caller pinned; a pinned VIX alone does not trigger it.
        #[test]
        fn a_pinned_phase_session_does_not_apply_the_multiplier_under_its_switch() {
            let run = |ratio: f64, bits: u16| {
                let mut e = with(|p| {
                    p.market_vol_cycle_ratio = ratio;
                    p.market_vol_cycle_expansion = 0.75;
                    p.market_vol_cycle_pin_phase = 1.0;
                });
                e.mark_macro_pins_today(bits);
                session_and_close(&mut e);
                e
            };
            let off = run(0.0, PIN_CYCLE);
            let on = run(2.0, PIN_CYCLE);
            assert_eq!(on.market_variance_target(), off.market_variance_target());
            assert_eq!(on.market_vol_cycle_log(), Some(0.0));
            let vix_only = run(2.0, PIN_VIX);
            assert_ne!(vix_only.market_variance_target(), off.market_variance_target());
        }

        /// Under a pin the multiplier relaxes toward one at its half-life,
        /// so a released run steps from there rather than jumping to its
        /// phase's value.
        #[test]
        fn a_pinned_session_relaxes_the_multiplier_toward_one() {
            let mut e = with(|p| {
                p.market_vol_cycle_ratio = 2.0;
                p.market_vol_cycle_expansion = 0.75;
                p.market_vol_cycle_half_life = 10.0;
                p.market_vol_cycle_pin_neutral = 1.0;
            });
            e.economy_mut().cycle_phase = CyclePhase::Contraction;
            e.set_market_vol_cycle_log(Some(0.4));
            e.mark_macro_pins_today(PIN_VIX);
            let a = 1.0 - crate::mathx::exp(-std::f64::consts::LN_2 / 10.0);
            assert_eq!(e.step_market_vol_cycle(), 0.4 + a * (0.0 - 0.4));
        }

        #[test]
        fn the_state_hash_carries_the_multiplier_only_while_set() {
            let mut e = on(2.0, 0.75, 21.0, 1.0);
            assert!(!e.carries_market_vol_cycle(), "unset before the first close");
            e.close_day(1);
            assert!(e.carries_market_vol_cycle());
            let before = e.state_hash(1, false);
            let mut other = e.clone();
            other.set_market_vol_cycle_log(Some(e.market_vol_cycle_log().unwrap() + 0.01));
            assert_ne!(other.state_hash(1, false), before);
        }
    }

    /// `cycle_publication_lag`: off, the published phase is the true one and
    /// no history is kept or hashed; on, the published phase is the phase
    /// of `lag` closes before, the opening phase until then, and a pin is
    /// the true phase at once and published `lag` closes after the close
    /// that carried it.
    #[test]
    fn the_published_phase_lags_the_true_one_only_under_the_dial() {
        use crate::economy::CyclePhase;
        // "Off" is pt-v19: the default, pt-v20, sets the lag since its
        // graded arm (2026-09-26). Was `engine(7)`, the default.
        let off = engine_v19(7);
        assert!(off.cycle_history().is_empty());
        assert_eq!(off.published_cycle_phase(), off.economy().cycle_phase);

        let lag = 4usize;
        let params = crate::params::ModelParams {
            cycle_publication_lag: lag as f64,
            ..crate::params::PT_V20
        };
        let mut e = Engine::with_params(
            7,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            params,
        );
        let opening = e.economy().cycle_phase;
        assert_eq!(e.cycle_history().len(), lag + 1);
        let turned = if opening == CyclePhase::Trough { CyclePhase::Peak } else { CyclePhase::Trough };
        e.economy_mut().cycle_phase = turned;
        assert_eq!(e.published_cycle_phase(), opening);
        let mut truth = vec![opening];
        for day in 1..=12i64 {
            e.advance_macro_day(day);
            truth.push(e.economy().cycle_phase);
            let d = day as usize;
            assert_eq!(e.published_cycle_phase(), truth[d.saturating_sub(lag)], "day {d}");
        }
        assert_ne!(truth[1], opening);
        // Hashed while set: the same engine with one phase of history
        // changed hashes apart.
        let before = e.state_hash(12, false);
        let mut other = e.clone();
        let mut history: Vec<CyclePhase> = other.cycle_history().iter().copied().collect();
        history[0] = if history[0] == CyclePhase::Expansion {
            CyclePhase::Recovery
        } else {
            CyclePhase::Expansion
        };
        other.set_cycle_history(history).unwrap();
        assert_ne!(other.state_hash(12, false), before);
        assert!(other.set_cycle_history(vec![opening; lag]).is_err());
        assert!(engine_v19(7).clone().set_cycle_history(vec![opening]).is_err());
    }

    /// The engine the nowcast tests read: pt-v20 by name (the default until
    /// 0.10.0; pt-v21 sets both dials), with
    /// `cycle_nowcast_accuracy` and `corporate_spread_cycle` as given.
    fn engine_nowcast(seed: u64, q: f64, s: f64) -> Engine {
        let params = crate::params::ModelParams {
            cycle_nowcast_accuracy: q,
            corporate_spread_cycle: s,
            ..crate::params::PT_V20
        };
        Engine::with_params(
            seed,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            params,
        )
    }

    /// `cycle_nowcast_accuracy`: off, no belief is kept and the stream is
    /// never drawn; on, the belief opens one-hot on the opening phase, a
    /// true turn nobody announced moves neither the belief nor the
    /// anticipation, each close leaves five non-negative weights summing to
    /// one, a pin is public (one-hot, and held through that close), and the
    /// state hash covers the belief.
    #[test]
    fn the_cycle_nowcast_prices_a_belief_not_the_true_phase_only_under_the_dial() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        let phases = crate::economy::cycle::phase_cycle();
        let off = engine_v20(7);
        assert_eq!(off.cycle_nowcast(), [0.0; 5]);
        assert_eq!(off.cycle_nowcast_terms, ([0.0; 5], 0.0));
        assert!(off.clone().set_cycle_nowcast([1.0, 0.0, 0.0, 0.0, 0.0], None).is_err());

        let mut e = engine_nowcast(7, 0.4, 0.75);
        let k0 = phases.iter().position(|p| *p == e.economy().cycle_phase).unwrap();
        let mut onehot = [0.0; 5];
        onehot[k0] = 1.0;
        assert_eq!(e.cycle_nowcast(), onehot);
        // The blend's occupancy mean of the US table, about 1.22.
        assert!((e.cycle_nowcast_terms.1 - 1.22).abs() < 0.05, "{:?}", e.cycle_nowcast_terms);

        // A true turn nobody announced: what the market prices stays put.
        let a0 = e.economy().earnings_anticipation;
        e.economy_mut().cycle_phase = phases[(k0 + 1) % 5];
        e.refresh_earnings_anticipation();
        assert_eq!(e.economy().earnings_anticipation, a0);
        assert_eq!(e.cycle_nowcast(), onehot);

        let draws = e.cycle_nowcast_rng_state().uniforms;
        for day in 1..=40i64 {
            e.advance_macro_day(day);
            let pi = e.cycle_nowcast();
            assert!(pi.iter().all(|v| v.is_finite() && *v >= 0.0), "{pi:?}");
            assert!((pi.iter().sum::<f64>() - 1.0).abs() < 1e-12, "{pi:?}");
        }
        assert_eq!(e.cycle_nowcast_rng_state().uniforms, draws + 40);
        assert_ne!(e.cycle_nowcast(), onehot);

        // A pin is public: one-hot on the pinned phase, and the close that
        // follows takes no report, so the belief holds through it.
        let pinned = phases[(k0 + 2) % 5];
        e.economy_mut().cycle_phase = pinned;
        e.cycle_phase_pinned();
        let kp = phases.iter().position(|p| *p == pinned).unwrap();
        let mut on_pin = [0.0; 5];
        on_pin[kp] = 1.0;
        assert_eq!(e.cycle_nowcast(), on_pin);
        let draws = e.cycle_nowcast_rng_state().uniforms;
        e.advance_macro_day(41);
        assert_eq!(e.cycle_nowcast(), on_pin);
        assert_eq!(e.cycle_nowcast_rng_state().uniforms, draws);

        // Hashed while set.
        let before = e.state_hash(41, false);
        let mut other = e.clone();
        other.set_cycle_nowcast([0.2; 5], None).unwrap();
        assert_ne!(other.state_hash(41, false), before);
        assert!(other.set_cycle_nowcast([0.5, 0.5, 0.5, 0.0, 0.0], None).is_err());
        assert!(other.set_cycle_nowcast([f64::NAN, 1.0, 0.0, 0.0, 0.0], None).is_err());
    }

    /// Over a long macro path the belief's time-mean matches the true
    /// phase's occupancy, and neither dial adds a draw to the economy
    /// stream (the report is on its own stream).
    #[test]
    fn the_cycle_nowcast_tracks_the_occupancy_on_its_own_stream() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        let phases = crate::economy::cycle::phase_cycle();
        let days = 5040i64;
        let mut e = engine_nowcast(11, 0.4, 0.75);
        let mut control = engine_v20(11);
        let mut mean = [0.0; 5];
        let mut occupancy = [0.0; 5];
        let mut turns = 0;
        let mut phase = e.economy().cycle_phase;
        for day in 1..=days {
            e.advance_macro_day(day);
            control.advance_macro_day(day);
            let pi = e.cycle_nowcast();
            for j in 0..5 {
                mean[j] += pi[j] / days as f64;
            }
            let now = e.economy().cycle_phase;
            if now != phase {
                turns += 1;
                phase = now;
            }
            occupancy[phases.iter().position(|p| *p == now).unwrap()] += 1.0 / days as f64;
        }
        assert!(turns >= 5, "only {turns} true turns in {days} sessions");
        for j in 0..5 {
            assert!((mean[j] - occupancy[j]).abs() < 0.05, "{mean:?} against {occupancy:?}");
        }
        assert_eq!(e.draws.economy, control.draws.economy);
    }

    /// `market_prehistory_sessions`: at 0.0 the engine opens as it stood; on,
    /// it opens with the run's own generators, prices and economy (the VIX
    /// apart) and a copy's volatility state, the same for the same seed;
    /// and a caller's named opening runs no prehistory.
    #[test]
    fn the_market_prehistory_hands_back_the_volatility_state_alone() {
        let off = engine_macro_clock(11, &[]);
        let zero = engine_macro_clock(11, &[("market_prehistory_sessions", 0.0)]);
        assert_eq!(off.state_hash(0, false), zero.state_hash(0, false));
        let on = engine_macro_clock(11, &[("market_prehistory_sessions", 21.0)]);
        let again = engine_macro_clock(11, &[("market_prehistory_sessions", 21.0)]);
        assert_eq!(on.state_hash(0, false), again.state_hash(0, false));
        assert_ne!(on.state_hash(0, false), off.state_hash(0, false));
        // The run's own generators and draw counts are untouched.
        assert_eq!(on.rng_state(), off.rng_state());
        assert_eq!(on.draws_consumed(), off.draws_consumed());
        // Prices and the economy but its VIX open where they would.
        for (a, b) in on.companies().iter().zip(off.companies().iter()) {
            assert_eq!(a.stock.price.to_bits(), b.stock.price.to_bits());
            assert_eq!(a.stock.market_cap.to_bits(), b.stock.market_cap.to_bits());
        }
        let mut econ = on.economy().clone();
        assert_ne!(econ.vix, off.economy().vix);
        econ.vix = off.economy().vix;
        assert_eq!(&econ, off.economy());
        // The factor variance is the copy's, not the constructor's baseline.
        assert_ne!(on.market_variance_state(), off.market_variance_state());
        let base = off.params().market_factor_sigma * off.params().market_factor_sigma;
        assert_eq!(off.market_variance_state().0, base);
        // A named opening is a statement: no prehistory runs.
        let named = |dials: &[(&str, f64)]| {
            let mut params = crate::params::PT_V20;
            for (name, value) in dials {
                params = params.with_override(name, *value).unwrap();
            }
            Engine::with_params_from_opening(
                11,
                vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
                create_initial_economy_state(&InitialEconomyOptions::default()),
                create_initial_central_bank_state(0),
                sectors(),
                params,
                false,
            )
        };
        let a = named(&[]);
        let b = named(&[("market_prehistory_sessions", 21.0)]);
        assert_eq!(a.market_variance_state(), b.market_variance_state());
        assert_eq!(a.economy(), b.economy());
    }

    /// `market_prehistory_valuation`: at 0.0 nothing but the volatility state
    /// is carried; on, the run opens on its own generators, draws and prices
    /// with the copy's valuation state, each name's carried mispricing waits
    /// for the opening, the economy moves only in the carried fields, no
    /// opening price moves at the first tick, the same seed opens the same,
    /// and the switch is refused without a prehistory or off {0, 1}.
    #[test]
    fn the_prehistory_valuation_carries_the_valuation_state_and_moves_no_price() {
        let dials: &[(&str, f64)] = &[
            ("market_prehistory_sessions", 63.0),
            ("fed_put_gain", 3.0),
            ("fed_put_half_life", 126.0),
            ("corporate_spread_equity_gain", 2.0),
            ("corporate_spread_equity_half_life", 126.0),
            ("earnings_anticipation_drift_share", 0.9),
            ("earnings_anticipation_drift_half_life", 252.0),
            ("fair_value_vix_release_half_life", 504.0),
            ("treasury_path_pricing", 1.0),
            ("treasury_path_half_life", 63.0),
            ("treasury_policy_damping", 0.5),
        ];
        let with = |on: f64| {
            let mut d = dials.to_vec();
            d.push(("market_prehistory_valuation", on));
            engine_macro_clock(11, &d)
        };
        let off = with(0.0);
        let on = with(1.0);
        let again = with(1.0);
        assert!(off.opening_carry().is_empty());
        assert_eq!(on.state_hash(0, false), again.state_hash(0, false));
        assert_ne!(on.state_hash(0, false), off.state_hash(0, false));
        assert_eq!(on.rng_state(), off.rng_state());
        assert_eq!(on.draws_consumed(), off.draws_consumed());
        assert_eq!(on.market_variance_state(), off.market_variance_state());
        for (a, b) in on.companies().iter().zip(off.companies().iter()) {
            assert_eq!(a.stock.price.to_bits(), b.stock.price.to_bits());
            assert!(a.stock.mispricing_s.is_none() && a.stock.fair_value_offset.is_none());
        }
        let carry = on.opening_carry().to_vec();
        assert_eq!(carry.len(), on.companies().len());
        assert!(carry.iter().all(|v| v.is_finite()));
        // The economy moves only in the carried fields.
        let mut econ = on.economy().clone();
        let o = off.economy();
        for (field, a, b) in [
            ("federal_funds_rate", econ.federal_funds_rate, o.federal_funds_rate),
            ("corporate_bond_yield", econ.corporate_bond_yield, o.corporate_bond_yield),
        ] {
            assert!(a <= b + 1.0, "{field}");
        }
        econ.vix_feedback = o.vix_feedback;
        econ.earnings_cycle = o.earnings_cycle;
        econ.earnings_anticipation = o.earnings_anticipation;
        econ.spread_equity_gap = o.spread_equity_gap;
        econ.fed_put = o.fed_put;
        econ.fed_put_owed = o.fed_put_owed;
        econ.federal_funds_rate = o.federal_funds_rate;
        econ.prime_rate = o.prime_rate;
        econ.treasury_yield_10y = o.treasury_yield_10y;
        econ.treasury_yield_2y = o.treasury_yield_2y;
        econ.corporate_bond_yield = o.corporate_bond_yield;
        econ.mortgage_rate_30y = o.mortgage_rate_30y;
        assert_eq!(&econ, o);
        let moved = on.economy().federal_funds_rate - o.federal_funds_rate;
        assert!((on.economy().fed_put_owed + moved).abs() < 1e-12);
        let path = on.policy_path().unwrap() - off.policy_path().unwrap();
        assert!(path != 0.0);
        assert!(((on.economy().treasury_yield_10y - o.treasury_yield_10y) - (moved + 0.5 * path)).abs() < 1e-12);
        assert!(((on.economy().mortgage_rate_30y - o.mortgage_rate_30y) - (moved + 0.5 * path)).abs() < 1e-12);
        // The first tick opens every name where it stood: the opening's split
        // booked the carried state into the fair-value level.
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut first = [off.clone(), on.clone()];
        for e in first.iter_mut() {
            let mut buf = SessionBuffer::new();
            e.run_session(&session(1, &innovations, &variances), &mut buf);
        }
        for (i, &ci) in carry.iter().enumerate().take(3) {
            let a = first[0].companies()[i].stock.price;
            let b = first[1].companies()[i].stock.price;
            assert!((a / b).ln().abs() < 1e-3, "name {i}: {a} against {b}");
            // One tick from the copy's mispricing, not from a draw.
            let s = first[1].companies()[i].stock.mispricing_s.unwrap();
            assert!((s - ci).abs() < 0.01, "name {i}: {s} against {ci}");
        }
        assert!(first[1].opening_carry().is_empty());
        // Refused without a prehistory, and off the switch.
        let base = crate::params::PT_V20;
        let refused = |p: Result<ModelParams, String>| p.and_then(|p| p.invariants()).is_err();
        assert!(refused(base.with_override("market_prehistory_valuation", 1.0)));
        let pre = base.with_override("market_prehistory_sessions", 21.0).unwrap();
        assert!(refused(pre.with_override("market_prehistory_valuation", 0.5)));
        assert!(!refused(pre.with_override("market_prehistory_valuation", 1.0)));
    }

    /// The engine the macro-clock tests read: pt-v20 by name (the default
    /// until 0.10.0), with the
    /// dials given.
    fn engine_macro_clock(seed: u64, dials: &[(&str, f64)]) -> Engine {
        let mut params = crate::params::PT_V20;
        for (name, value) in dials {
            params = params.with_override(name, *value).unwrap();
        }
        Engine::with_params(
            seed,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            params,
        )
    }

    /// `earnings_anticipation_drift_share`: off, no `D` is kept or hashed and
    /// the valuation reads `A - e`; on, `D` opens at zero after the burn-in,
    /// each close takes `D <- D 2^(-1/h_D) + share rho (A - e)` with `A - e`
    /// as the last refresh left it, the valuation reads `(A - e) - D`, the
    /// half-life 0.0 reads 1260, and the state hash covers `D`.
    #[test]
    fn the_anticipation_drift_is_left_out_only_under_the_share() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        let off = engine_v20(7);
        assert!(!off.carries_anticipation_drift());
        assert_eq!(off.anticipation_drift(), (0.0, 0.0));
        assert!(off.clone().set_anticipation_drift(0.1, 0.0).is_err());

        for (share, hd, h_read) in [(1.0, 252.0, 252.0), (0.5, 0.0, 1260.0)] {
            let mut e = engine_macro_clock(
                7,
                &[("earnings_anticipation_drift_share", share),
                  ("earnings_anticipation_drift_half_life", hd)],
            );
            assert!(e.params().macro_burn_in_days > 0.0);
            let rho = std::f64::consts::LN_2 / e.params().earnings_anticipation_half_life;
            let decay = crate::mathx::exp(-std::f64::consts::LN_2 / h_read);
            // Zeroed after the burn-in, so the opening prices `A - e`.
            let (d0, raw0) = e.anticipation_drift();
            assert_eq!(d0, 0.0);
            assert_ne!(raw0, 0.0);
            assert_eq!(e.economy().earnings_anticipation, raw0);
            let (mut d, mut raw) = (d0, raw0);
            for day in 1..=60i64 {
                e.advance_macro_day(day);
                let (d1, raw1) = e.anticipation_drift();
                assert_eq!(d1, d * decay + share * rho * raw, "day {day}");
                assert_eq!(e.economy().earnings_anticipation, raw1 - d1, "day {day}");
                d = d1;
                raw = raw1;
            }
            assert!(d.abs() > 1e-4, "{d}");
            // Hashed while set; a restore puts back both and the anticipation.
            let before = e.state_hash(60, false);
            let mut other = e.clone();
            other.set_anticipation_drift(d + 0.01, raw).unwrap();
            assert_ne!(other.state_hash(60, false), before);
            assert_eq!(other.economy().earnings_anticipation, raw - (d + 0.01));
            other.set_anticipation_drift(d, raw).unwrap();
            assert_eq!(other.state_hash(60, false), before);
            assert!(other.set_anticipation_drift(f64::NAN, raw).is_err());
            // The seeding zeroes D and prices A - e again.
            other.seed_anticipation_drift();
            assert_eq!(other.anticipation_drift().0, 0.0);
            assert_eq!(other.economy().earnings_anticipation, other.anticipation_drift().1);
        }
    }

    /// `corporate_spread_cycle` at 1.0 without the nowcast: the multiplier
    /// the meeting and the daily path price is the occupancy mean in every
    /// phase, and at 0.0 it is the phase's own.
    #[test]
    fn the_full_spread_blend_prices_the_occupancy_mean_in_every_phase() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        let e = engine_macro_clock(7, &[("corporate_spread_cycle", 1.0)]);
        let off = engine_v20(7);
        let mbar = e.cycle_nowcast_terms.1;
        let (mean, cycle) = crate::economy::cycle::stationary_phase_shares_for(&e.cycle_spec());
        let phases = crate::economy::cycle::phase_cycle();
        let mut direct = 0.0;
        for k in 0..5 {
            direct += mean[k] / cycle * crate::economy::central_bank::spread_multiplier_of(phases[k]);
        }
        assert!((mbar - direct).abs() < 1e-12);
        assert!(mbar > 1.0 && mbar < 3.5, "{mbar}");
        for phase in phases {
            assert!((e.priced_spread_multiplier(phase) - mbar).abs() < 1e-12);
            assert_eq!(off.priced_spread_multiplier(phase),
                       crate::economy::central_bank::spread_multiplier_of(phase));
        }
    }

    /// The lag of one turn: whole sessions, the whole range reachable, the
    /// peak and contraction range for those two phases and the trough range
    /// for the other three.
    #[test]
    fn a_publication_lag_spans_its_range() {
        use crate::economy::CyclePhase;
        let top = 1.0 - f64::EPSILON / 2.0;
        for (phase, lo, hi) in [(CyclePhase::Peak, 84, 252), (CyclePhase::Contraction, 84, 252),
                                (CyclePhase::Trough, 168, 441), (CyclePhase::Recovery, 168, 441),
                                (CyclePhase::Expansion, 168, 441)] {
            assert_eq!(cycle_publication_lag_of(phase, 0.0), lo);
            assert_eq!(cycle_publication_lag_of(phase, top), hi);
            let mut seen = std::collections::BTreeSet::new();
            for k in 0..20_000u64 {
                let lag = cycle_publication_lag_of(phase, crate::rng::publication_uniform(99, k));
                assert!((lo..=hi).contains(&lag));
                seen.insert(lag);
            }
            assert_eq!(seen.len() as i64, hi - lo + 1);
        }
    }

    /// `cycle_publication_lag_draw`: off, no schedule is kept or hashed; on,
    /// the fixed history is not kept, each true turn is published at
    /// `max(pi_prev, tau + L)` with `L` in [84, 252] into a peak or a
    /// contraction and [168, 441] otherwise, announcements come in the
    /// true turns' order, the schedule is a function of the seed, and it
    /// takes no draw from any stream.
    #[test]
    fn the_drawn_publication_schedule_publishes_each_turn_in_order() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        use crate::economy::CyclePhase;
        let off = engine_v20(7);
        assert!(off.carries_cycle_history());
        assert!(off.clone().set_cycle_publication(off.cycle_publication().clone()).is_err());

        let days = 5040i64;
        let run = |seed: u64| {
            let mut e = engine_macro_clock(seed, &[("cycle_publication_lag_draw", 1.0)]);
            let mut control = engine_v20(seed);
            assert!(!e.carries_cycle_history());
            assert!(e.cycle_history().is_empty());
            assert!(e.clone().set_cycle_history(vec![e.economy().cycle_phase; 253]).is_err());
            let opening = e.economy().cycle_phase;
            assert_eq!(e.published_cycle_phase(), opening);
            let mut truth = vec![opening];
            let mut published = vec![opening];
            for day in 1..=days {
                e.advance_macro_day(day);
                control.advance_macro_day(day);
                truth.push(e.economy().cycle_phase);
                published.push(e.published_cycle_phase());
                assert_eq!(e.economy().cycle_phase, control.economy().cycle_phase, "day {day}");
            }
            // No stream moved: the schedule's draws are stateless.
            assert_eq!(e.draws.economy, control.draws.economy);
            (e, truth, published)
        };
        let (e, truth, published) = run(7);

        // The true turns, and each one's publication close from the formula.
        let mut turns: Vec<(i64, CyclePhase)> = Vec::new();
        for c in 1..truth.len() {
            if truth[c] != truth[c - 1] {
                turns.push((c as i64, truth[c]));
            }
        }
        assert!(turns.len() >= 10, "only {} turns", turns.len());
        let key = e.cycle_publication().key;
        assert_eq!(key, crate::rng::publication_key(7));
        let mut last = i64::MIN;
        let mut expected = vec![truth[0]; truth.len()];
        let mut schedule = Vec::new();
        for (k, &(tau, phase)) in turns.iter().enumerate() {
            let lag = cycle_publication_lag_of(phase, crate::rng::publication_uniform(key, k as u64));
            match phase {
                CyclePhase::Peak | CyclePhase::Contraction => assert!((84..=252).contains(&lag)),
                _ => assert!((168..=441).contains(&lag)),
            }
            let pi = if tau + lag > last { tau + lag } else { last };
            assert!(pi >= last);
            last = pi;
            schedule.push((pi, phase));
        }
        for (c, slot) in expected.iter_mut().enumerate().take(truth.len()) {
            if let Some(&(_, phase)) = schedule.iter().rev().find(|&&(pi, _)| pi <= c as i64) {
                *slot = phase;
            }
        }
        assert_eq!(published, expected);
        // Announcements follow the true turns' order: the published path's
        // changes are a subsequence of the true turns' phases.
        let announced: Vec<CyclePhase> = published.windows(2)
            .filter(|w| w[0] != w[1]).map(|w| w[1]).collect();
        let mut it = turns.iter().map(|&(_, p)| p);
        for phase in &announced {
            assert!(it.any(|p| p == *phase), "{phase:?} out of order");
        }
        assert!(announced.len() >= 8);
        // The state carried agrees with the formula.
        let state = e.cycle_publication();
        assert_eq!(state.closes, days);
        assert_eq!(state.turns, turns.len() as u64);
        assert_eq!(state.last_true, *truth.last().unwrap());
        let pending: Vec<(i64, CyclePhase)> = schedule.iter().copied()
            .filter(|&(pi, _)| pi > days).collect();
        assert_eq!(state.pending.iter().copied().collect::<Vec<_>>(), pending);

        // Reproducible from the seed, and another seed draws other lags.
        let (_, _, again) = run(7);
        assert_eq!(again, published);
        let (other, _, _) = run(8);
        assert_ne!(other.cycle_publication().key, key);

        // Hashed while set, and a restore refuses a schedule out of order.
        let before = e.state_hash(days as u32, false);
        let mut changed = e.clone();
        let mut state = e.cycle_publication().clone();
        state.turns += 1;
        changed.set_cycle_publication(state).unwrap();
        assert_ne!(changed.state_hash(days as u32, false), before);
        let mut bad = e.cycle_publication().clone();
        bad.pending = vec![(10, CyclePhase::Peak), (5, CyclePhase::Contraction)].into();
        assert!(changed.set_cycle_publication(bad).is_err());
    }

    /// `gdp_publication_lag`: off, the published growth is the true one and
    /// no state is kept or hashed; on, it is the mean of each macro-calendar
    /// quarter's true growth (day 0, the opening, included), released `lag`
    /// closes after the quarter's last day, and the opening growth until then.
    #[test]
    fn the_published_growth_is_the_quarter_mean_released_late_only_under_the_dial() {
        // "Off" is pt-v19: the default, pt-v20, sets the lag since its
        // graded arm (2026-09-26). Was `engine(7)`, the default.
        let off = engine_v19(7);
        assert_eq!(off.gdp_publication(), &GdpPublication::default());
        assert_eq!(off.published_gdp_growth(), off.economy().gdp_growth);

        let lag = 5i64;
        let params = crate::params::ModelParams {
            gdp_publication_lag: lag as f64,
            ..crate::params::PT_V20
        };
        let mut e = Engine::with_params(
            7,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            params,
        );
        let q = e.macro_calendar().days_per_quarter();
        let opening = e.economy().gdp_growth;
        assert_eq!(e.published_gdp_growth(), opening);
        let mut truth = vec![opening];
        let mut published = vec![opening];
        for day in 1..=(2 * q + lag) {
            e.advance_macro_day(day);
            truth.push(e.economy().gdp_growth);
            published.push(e.published_gdp_growth());
        }
        let mean = |k: i64| -> f64 {
            let mut sum = 0.0;
            for d in (k * q)..((k + 1) * q) {
                sum += truth[d as usize];
            }
            sum / q as f64
        };
        for d in 0..published.len() as i64 {
            let want = if d >= 2 * q - 1 + lag {
                mean(1)
            } else if d >= q - 1 + lag {
                mean(0)
            } else {
                opening
            };
            assert_eq!(published[d as usize], want, "day {d}");
        }
        // Hashed while set: the same engine with one pending figure
        // changed hashes apart.
        let before = e.state_hash(12, false);
        let mut other = e.clone();
        let mut state = other.gdp_publication().clone();
        state.sum += 1.0;
        other.set_gdp_publication(state).unwrap();
        assert_ne!(other.state_hash(12, false), before);
        let mut empty = e.gdp_publication().clone();
        empty.count = 0;
        assert!(e.clone().set_gdp_publication(empty).is_err());
        assert!(engine_v19(7).clone().set_gdp_publication(GdpPublication {
            count: 1,
            ..GdpPublication::default()
        }).is_err());
    }

    fn request(hour: i64, minute: i64) -> TickRequest<'static> {
        TickRequest {
            time: GameTime {
                hour,
                minute,
                day_of_week: 3,
            },
            volatility_multiplier: 0.7,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
        }
    }

    #[test]
    fn the_same_seed_produces_the_same_market() {
        // The property the whole port exists to preserve.
        let run = || {
            let mut e = engine(4242);
            e.open_market();
            for m in 0..60 {
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
            }
            (e.prices(), e.draws_consumed())
        };
        let (a, da) = run();
        let (b, db) = run();
        assert_eq!(da, db, "draw counts must match");
        for (i, (x, y)) in a.iter().zip(&b).enumerate() {
            assert_eq!(x.to_bits(), y.to_bits(), "company {i}");
        }
    }

    #[test]
    fn different_seeds_produce_different_markets() {
        // The companion assertion: reproducible-because-constant would pass
        // the test above and be worthless.
        //
        // Compares the WHOLE price vector over four seeds, not company A over
        // two. Prices settle to the cent, so a single name colliding across a
        // single pair of seeds is a coincidence rather than a collapse -- and
        // one duly happened at the pt-v12 boundary, where company A landed on
        // 101.54 for both seed 1 and seed 2 after sixty minutes while every
        // other name differed. A test that reads one number cannot tell the
        // two apart, which is the whole point of the property it is named
        // for.
        let run = |seed| {
            let mut e = engine(seed);
            e.open_market();
            for m in 0..60 {
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
            }
            e.prices()
        };
        let seeds: Vec<Vec<f64>> = (1u64..=4).map(run).collect();
        for (i, a) in seeds.iter().enumerate() {
            for b in seeds.iter().skip(i + 1) {
                assert!(
                    a.iter().zip(b).any(|(x, y)| x.to_bits() != y.to_bits()),
                    "two seeds produced an identical market: {a:?} vs {b:?}"
                );
            }
        }
    }

    #[test]
    fn a_closed_market_costs_nothing() {
        let mut e = engine(7);
        let before_prices = e.prices();
        // The DELTA, not the lifetime total. A default preset with a macro
        // burn-in draws during construction -- pt-v18 runs 755 days of it --
        // and this test is about what the operation costs, not about what
        // building an engine costs. Asserting the total made the claim
        // depend on a dial in a different subsystem.
        let before_draws = e.draws_consumed();
        let out = e.tick(&TickRequest {
            time: GameTime {
                hour: 11,
                minute: 0,
                day_of_week: 6,
            },
            ..request(11, 0)
        });
        assert_eq!(out.market_status, MarketStatus::Closed);
        assert_eq!(
            out.draws_consumed, 0,
            "a closed market must not advance the stream"
        );
        assert_eq!(e.draws_consumed() - before_draws, 0);
        assert_eq!(e.prices(), before_prices);
    }

    #[test]
    fn the_draw_schedule_is_what_the_documentation_claims() {
        // 1 market normal + one per sector + 2 per active company, plus 4 more
        // each at settlement when the book runs.
        let mut e = engine(11);
        e.open_market();

        let open = e.tick(&request(10, 0));
        assert_eq!(open.market_status, MarketStatus::Open);
        assert_eq!(open.draws_consumed, 1 + 3 + 2 * 3 + 4 * 3);

        let extended = e.tick(&request(8, 0));
        assert_eq!(extended.market_status, MarketStatus::PreMarket);
        assert_eq!(
            extended.draws_consumed,
            1 + 3 + 2 * 3,
            "extended hours must not settle through the book"
        );
    }

    #[test]
    fn an_inactive_company_costs_no_draws() {
        let mut e = engine(13);
        e.companies_mut()[1].is_bankrupt = true;
        e.open_market();
        let out = e.tick(&request(10, 0));
        assert_eq!(out.active_indices, vec![0, 2]);
        assert_eq!(out.draws_consumed, 1 + 3 + 2 * 2 + 4 * 2);
    }

    #[test]
    fn embedder_draws_leave_the_market_bit_identical() {
        // The CUTOVER half of the stream split. Under the shared stream this
        // test's inverse held — one embedder draw shifted every subsequent
        // market draw, and the old assertion here demanded exactly that. Now
        // the embedder's consumption lives on its own substream, so a game
        // that adds an event roll no longer invalidates every seeded market.
        let run = |extra_draws: usize| {
            let mut e = engine(99);
            e.open_market();
            for _ in 0..extra_draws {
                e.draw_uniform();
                e.draw_normal();
            }
            for m in 0..5i64 {
                e.tick(&request(10, m));
            }
            (e.prices(), e.rng_state())
        };
        let (without, state_without) = run(0);
        let (with_draws, state_with) = run(1000);
        for (i, (a, b)) in without.iter().zip(&with_draws).enumerate() {
            assert_eq!(
                a.to_bits(),
                b.to_bits(),
                "company {i} moved because the embedder drew"
            );
        }
        // Bit-identical POSITION, not merely price: the market stream never
        // saw the embedder's draws at all.
        assert_eq!(state_without.market, state_with.market);
        assert_ne!(
            state_without.external, state_with.external,
            "the embedder's draws were not taken from the external stream"
        );
    }

    #[test]
    fn embedder_draws_are_seed_determined_and_reproducible() {
        // Isolation must not cost reproducibility: the external stream is
        // derived from the same root seed, so an embedder replaying a run
        // gets its own draws back too.
        let a: Vec<f64> = {
            let mut e = engine(7);
            (0..16).map(|_| e.draw_normal()).collect()
        };
        let b: Vec<f64> = {
            let mut e = engine(7);
            (0..16).map(|_| e.draw_normal()).collect()
        };
        assert_eq!(a, b);
    }

    #[test]
    fn macro_branch_differences_leave_the_market_stream_untouched() {
        // The PINNED-VERSUS-BASELINE half of the stream split. The macro
        // chain's draw count depends on macro state — a cycle sitting in
        // contraction rolls a shock the expansion never draws — so two runs
        // whose macro paths branch differently consume different economy
        // draws. Under the shared stream that shifted every market draw
        // after the day boundary; a pinned run and its baseline never saw
        // the same noise again. Now the market stream's position is
        // identical whatever the macro chain consumed.
        // On pt-v19. On the default since pt-v20 took its graded arm
        // (2026-09-26) the two phase ages happen to draw the same number of
        // economy normals at this seed, so the precondition below found
        // nothing to test; the stream split it tests is the same on every
        // preset. Was `engine(4242)`, the default.
        let run = |fresh_phase: bool| {
            let mut e = engine_v19(4242);
            // A phase that changed TODAY draws the phase-change shock
            // uniform; one 0.9 months in draws neither that (window passed)
            // nor the transition roll (min_months not reached). The counts
            // differ by construction, which is the shape of the hazard: a
            // pinned macro path and its baseline sit in different phases and
            // stop consuming in step.
            e.economy_mut().months_in_current_phase = if fresh_phase { 0.0 } else { 0.9 };
            e.advance_day(&DayAdvanceRequest {
                volatility: 1.0,
                active_shocks: &[],
                market_return_pct: 0.0,
                game_day: 1,
                timestamp: 24 * 60,
            });
            // The market's draws, taken AFTER the diverging macro step.
            e.open_market();
            for m in 0..10i64 {
                e.tick(&request(10, m));
            }
            e
        };
        let flat = run(false);
        let shocked = run(true);
        // The precondition that makes the assertion mean something: the two
        // macro chains really did consume different numbers of draws.
        assert_ne!(
            flat.draws_by_stream().economy,
            shocked.draws_by_stream().economy,
            "the two macro paths drew in step; the test constructed nothing"
        );
        assert_eq!(
            flat.rng_state().market,
            shocked.rng_state().market,
            "the macro chain moved the market stream"
        );
        assert_eq!(flat.draws_by_stream().market, shocked.draws_by_stream().market);
    }

    #[test]
    fn a_pinned_macro_run_and_its_baseline_see_identical_market_noise() {
        // The consumer's actual workflow: world A advances the macro chain
        // endogenously; world B replays a PINNED macro path — it never runs
        // the chain at all, it writes the day's values directly. Before the
        // split, world B's skipped macro draws shifted the market stream and
        // the two worlds' intraday noise had nothing to do with each other.
        // Now: pin the same values the endogenous chain produced, and the
        // sessions are bit-identical — which is what makes "the difference
        // is the macro path and nothing else" a guarantee rather than an
        // approximation when the pinned values DO differ.
        let day = |e: &mut Engine| {
            e.open_market();
            for m in 0..30i64 {
                e.tick(&request(10, m));
            }
            e.close_market(&DayCloseRequest {
                daily_innovations: &[None, None, None],
                sector_base_variances: &[0.000225; 3],
                avg_volume: AvgVolumePolicy::default(),
            });
        };

        // World A: the chain runs.
        let mut endogenous = engine(2026);
        endogenous.advance_day(&DayAdvanceRequest {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: 1,
            timestamp: 24 * 60,
        });
        let evolved = endogenous.economy().clone();
        day(&mut endogenous);

        // World B: no chain — the evolved values are pinned directly, as a
        // replay of a recorded macro series would.
        let mut pinned = engine(2026);
        // The DELTA, not the lifetime total. A default preset with a macro
        // burn-in draws during construction -- pt-v18 runs 755 days of it --
        // and this test is about what the operation costs, not about what
        // building an engine costs. Asserting the total made the claim
        // depend on a dial in a different subsystem.
        let before_economy = pinned.draws_by_stream().economy;
        *pinned.economy_mut() = evolved;
        day(&mut pinned);

        assert_eq!(
            pinned.draws_by_stream().economy - before_economy,
            0,
            "the pinned world must not run the macro chain"
        );
        for (i, (a, b)) in endogenous
            .prices()
            .iter()
            .zip(&pinned.prices())
            .enumerate()
        {
            assert_eq!(
                a.to_bits(),
                b.to_bits(),
                "company {i}: pinning the macro path perturbed the market noise"
            );
        }
        assert_eq!(endogenous.rng_state().market, pinned.rng_state().market);
    }

    #[test]
    fn the_cumulative_draw_count_includes_embedder_draws() {
        let mut e = engine(5);
        // The DELTA, not the lifetime total. A default preset with a macro
        // burn-in draws during construction -- pt-v18 runs 755 days of it --
        // and this test is about what the operation costs, not about what
        // building an engine costs. Asserting the total made the claim
        // depend on a dial in a different subsystem.
        let before = e.draws_consumed();
        e.draw_uniform();
        e.draw_normal();
        assert_eq!(e.draws_consumed() - before, 2);
        e.open_market();
        let out = e.tick(&request(10, 0));
        assert_eq!(e.draws_consumed() - before, 2 + out.draws_consumed);
    }

    /// A draw source that records the call sites it is told about, in
    /// order, and counts the draws taken at each. `Site` is a no-op on
    /// every generator that is not the logged one, so the ORDER of the
    /// three macro stages is otherwise unobservable from outside
    /// `advance_day_with`.
    struct SiteTrace {
        inner: GameRng,
        seen: Vec<(Site, usize)>,
    }

    impl Rng for SiteTrace {
        fn next_f64(&mut self) -> f64 {
            if let Some(last) = self.seen.last_mut() {
                last.1 += 1;
            }
            self.inner.next_f64()
        }
        fn next_normal(&mut self) -> f64 {
            if let Some(last) = self.seen.last_mut() {
                last.1 += 1;
            }
            self.inner.next_normal()
        }
        fn site(&mut self, site: Site, tag: u32) {
            self.seen.push((site, 0));
            self.inner.site(site, tag);
        }
    }

    /// The daily step visits the macro chain, then the cycle roll, then the
    /// central bank, in that order, and the first of the three moves state.
    ///
    /// # What this test used to assert, and why that assertion is gone
    ///
    /// It read the VIX before and after and required it to have changed.
    /// Under `vix_level_identity`, which pt-v19 turns on, that is a
    /// property the model deliberately gave up. The VIX's target is the
    /// index's own conditional variance read back in points, less the
    /// closed form of the fear excursion's mean, and neither depends on the
    /// calendar. Its innovation is `vix * sqrt(s0^2 + (s_r * r)^2)` with
    /// `vix_innovation_sigma` derived to 0.0, so on a session whose return
    /// is zero the noise term is exactly zero too. pt-v19 also runs a
    /// 755-day macro burn-in, which drives the VIX onto the floating-point
    /// fixed point of that map before the test's first call. The engine
    /// this module builds never opens a session, so its day return is 0.0
    /// and the VIX reproduces to the bit, day after day. Measured here: it
    /// holds at 23.768839023837387 across eleven consecutive calls.
    ///
    /// The economy still steps, which was the thing being probed. Gold, the
    /// dollar, oil, the ten-year and the greed index all move on the same
    /// call. So the probe is `gold_price`, which carries an unconditional
    /// `random_normal` term and cannot sit still while the chain runs.
    ///
    /// The VIX law that replaced the old one is asserted at the end: the
    /// VIX is a function of the session, so a flat session leaves it
    /// bit-identical and a down session raises it.
    #[test]
    fn the_daily_step_runs_economy_then_cycle_then_the_bank() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        let day = DayAdvanceRequest {
            volatility: 0.7,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: 1,
            timestamp: 24 * 60,
        };

        let mut e = engine_v20(21);
        let before = e.economy().clone();
        let mut trace = SiteTrace {
            inner: GameRng::new(21, stream::ECONOMY),
            seen: Vec::new(),
        };
        e.advance_day_with(&day, &mut trace);

        let sites: Vec<Site> = trace.seen.iter().map(|(s, _)| *s).collect();
        assert_eq!(
            sites,
            vec![Site::EconomyDaily, Site::EconomyCycle, Site::CentralBank],
            "the daily step must visit the three stages once each, in order"
        );
        assert!(
            trace.seen[0].1 > 0,
            "the macro chain must draw; it took {} draws",
            trace.seen[0].1
        );
        assert_ne!(
            e.economy().gold_price,
            before.gold_price,
            "the macro chain ran without moving the state it draws for"
        );

        // The draw count the counting entry point reports is the same work.
        let mut counted = engine_v20(21);
        let out = counted.advance_day(&day);
        assert!(out.draws_consumed > 0, "the daily macro step must draw");

        // THE VIX LAW THIS TEST NOW CARRIES. The flat engine above took the
        // same call and its VIX did not move by one bit, because under the
        // identity a VIX at rest with a zero session return has nothing to
        // move it. Open a session and take the index down five per cent and
        // the same call moves it, which is the direction the fear channel
        // is wired in.
        assert_eq!(
            e.economy().vix.to_bits(),
            before.vix.to_bits(),
            "a flat session moved the VIX, which the identity has no term for"
        );
        let mut down = engine_v20(21);
        down.open_market();
        for c in down.companies_mut().iter_mut() {
            c.stock.price *= 0.95;
        }
        down.advance_day(&day);
        assert!(
            down.economy().vix > before.vix + 1.0,
            "a five per cent down session must raise the VIX; it read {}",
            down.economy().vix
        );
    }

    #[test]
    fn the_open_reset_anchors_the_breaker_to_todays_open() {
        let mut e = engine(3);
        e.companies_mut()[0].stock.price = 137.0;
        e.open_market();
        assert_eq!(e.column(PriceField::PreviousClose)[0], 137.0);
        assert_eq!(e.column(PriceField::Volume)[0], 0.0);
    }

    #[test]
    fn the_close_rolls_momentum_and_takes_no_draws() {
        let mut e = engine(17);
        e.open_market();
        for m in 0..10 {
            e.tick(&request(10, m));
        }
        let before = e.draws_consumed();
        e.close_market(&DayCloseRequest {
            daily_innovations: &[None, None, None],
            sector_base_variances: &[0.000225; 3],
            avg_volume: AvgVolumePolicy::default(),
        });
        assert_eq!(e.draws_consumed(), before, "the close must not draw");
        // `s` has moved during the session, so the roll must record it.
        assert!(e.companies()[0].stock.mispricing_momentum.is_some());
    }

    // ── Day-chunked stepping ──────────────────────────────────────────────

    fn session<'a>(
        ticks: usize,
        innovations: &'a [Option<f64>],
        variances: &'a [f64],
    ) -> SessionRequest<'a> {
        SessionRequest {
            start: GameTime {
                hour: 9,
                minute: 30,
                day_of_week: 3,
            },
            ticks,
            volatility_multiplier: 0.7,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
            fills: &[],
            close_at_end: true,
            // These tests run one session per day, where opening inside the
            // session and opening the day are the same act. True preserves
            // exactly what they measured before `reopen` existed.
            reopen: true,
            daily_innovations: innovations,
            sector_base_variances: variances,
            stop: None,
        }
    }

    // ── Fills: an agent's trades reach the market once ────────────────────

    /// A session of `ticks` from `start_minute` past 09:30, inside a day
    /// already opened, carrying `standing` on every tick and `fills` once.
    fn stepped<'a>(
        ticks: usize,
        start_minute: i64,
        standing: &'a [(String, OrderVolume)],
        fills: &'a [(String, OrderVolume)],
        innovations: &'a [Option<f64>],
        variances: &'a [f64],
    ) -> SessionRequest<'a> {
        let at = 30 + start_minute;
        SessionRequest {
            start: GameTime {
                hour: 9 + at / 60,
                minute: at % 60,
                day_of_week: 3,
            },
            order_volumes: standing,
            fills,
            close_at_end: false,
            reopen: false,
            ..session(ticks, innovations, variances)
        }
    }

    /// `fills` IS one tick of `order_volumes` followed by the rest of the
    /// session without it: the same prices to the bit and the same draws.
    /// That is the whole contract, stated as the market the tick loop
    /// already defines, so it cannot drift into a second meaning.
    #[test]
    fn fills_are_one_tick_of_flow_at_the_start_of_the_session() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let fills = vec![("A".to_string(), OrderVolume { buy: 40_000.0, sell: 0.0 })];

        let mut once = engine_v19(11);
        once.open_market();
        let mut buf = SessionBuffer::new();
        once.run_session(&stepped(65, 0, &[], &fills, &innovations, &variances), &mut buf);

        let mut spelled = engine_v19(11);
        spelled.open_market();
        spelled.tick(&TickRequest {
            order_volumes: &fills,
            ..request(9, 30)
        });
        let mut buf2 = SessionBuffer::new();
        spelled.run_session(&stepped(64, 1, &[], &[], &innovations, &variances), &mut buf2);

        assert_eq!(once.draws_consumed(), spelled.draws_consumed());
        for (i, (x, y)) in once.prices().iter().zip(spelled.prices()).enumerate() {
            assert_eq!(x.to_bits(), y.to_bits(), "company {i}");
        }
        let flow = crate::market::factors::S_COMPONENT_KEYS
            .iter()
            .position(|f| *f == "order_flow_impact")
            .unwrap();
        let a = once.attribution_column(flow);
        assert!(a[0] > 0.0, "the buy reached A: {a:?}");
        assert_eq!(a[1], 0.0);
        assert_eq!(a[2], 0.0);
    }

    /// The same flow held on every tick is counted on every tick. This is
    /// what every harness did with an agent's fills until 0.8.5, and the
    /// ratio is the 65 the investigation measured, not an approximation of
    /// it: a name's flow impact depends on its average volume, which does
    /// not move inside a day.
    #[test]
    fn a_standing_flow_is_counted_on_every_tick_and_fills_once() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let flow = vec![("A".to_string(), OrderVolume { buy: 40_000.0, sell: 0.0 })];
        let column = crate::market::factors::S_COMPONENT_KEYS
            .iter()
            .position(|f| *f == "order_flow_impact")
            .unwrap();

        let run = |standing: &[(String, OrderVolume)], fills: &[(String, OrderVolume)]| {
            let mut e = engine_v19(11);
            e.open_market();
            let mut buf = SessionBuffer::new();
            e.run_session(&stepped(65, 0, standing, fills, &innovations, &variances), &mut buf);
            e.attribution_column(column)[0]
        };
        let once = run(&[], &flow);
        let held = run(&flow, &[]);
        assert!(once > 0.0);
        let ratio = held / once;
        assert!((ratio - 65.0).abs() < 1e-9, "held / once = {ratio}");
    }

    /// With no fills the session is the one that existed before the field:
    /// the first tick reads `order_volumes` untouched. Bit-identical rather
    /// than equal, since the untraded known-answer digest rests on it.
    #[test]
    fn empty_fills_change_nothing() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let standing = vec![("B".to_string(), OrderVolume { buy: 0.0, sell: 9_000.0 })];
        let run = |fills: &[(String, OrderVolume)]| {
            let mut e = engine(5);
            e.open_market();
            let mut buf = SessionBuffer::new();
            e.run_session(&stepped(90, 0, &standing, fills, &innovations, &variances), &mut buf);
            (e.prices(), e.draws_consumed())
        };
        let (a, da) = run(&[]);
        // A zero fill for a name is a no-op too: zero volume is the literal
        // 0.0 imbalance under either impact law.
        let (b, db) = run(&[("C".to_string(), OrderVolume { buy: 0.0, sell: 0.0 })]);
        assert_eq!(da, db);
        for (i, (x, y)) in a.iter().zip(&b).enumerate() {
            assert_eq!(x.to_bits(), y.to_bits(), "company {i}");
        }
    }

    /// One entry per ticker on the first tick, standing and fills summed.
    /// The tick reads the FIRST entry for a name, so a second entry would
    /// be dropped rather than added.
    #[test]
    fn standing_flow_and_fills_merge_per_ticker() {
        let standing = vec![
            ("A".to_string(), OrderVolume { buy: 1.0, sell: 2.0 }),
            ("C".to_string(), OrderVolume { buy: 7.0, sell: 0.0 }),
        ];
        let fills = vec![
            ("A".to_string(), OrderVolume { buy: 3.0, sell: 4.0 }),
            ("B".to_string(), OrderVolume { buy: 5.0, sell: 0.0 }),
        ];
        let merged = merge_order_volumes(&standing, &fills);
        assert_eq!(
            merged,
            vec![
                ("A".to_string(), OrderVolume { buy: 4.0, sell: 6.0 }),
                ("C".to_string(), OrderVolume { buy: 7.0, sell: 0.0 }),
                ("B".to_string(), OrderVolume { buy: 5.0, sell: 0.0 }),
            ]
        );
    }

    // ── The depth counterfactual ──────────────────────────────────────────

    /// The arm is inert to the market. Same seed, same session, one engine
    /// with the second settlement and one without, and every price agrees to
    /// the bit along with the draw count.
    ///
    /// This is the claim the known-answer digest checks at the package level;
    /// asserting it here as well means a build that broke it says so in
    /// seconds rather than at the end of a wheel build.
    #[test]
    fn the_depth_counterfactual_leaves_the_market_and_the_draws_alone() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let run = |on: bool| {
            let mut e = engine(4242);
            e.set_settle_depth_counterfactual(on);
            let mut buf = SessionBuffer::new();
            for _ in 0..3 {
                e.run_session(&session(60, &innovations, &variances), &mut buf);
            }
            (e.draws_by_stream(), buf.prices.clone(), buf.shock.clone())
        };
        let (draws_off, prices_off, shock_off) = run(false);
        let (draws_on, prices_on, shock_on) = run(true);
        assert_eq!(draws_off, draws_on, "the arm must take no draw");
        for (i, (a, b)) in prices_off.iter().zip(prices_on.iter()).enumerate() {
            assert_eq!(a.to_bits(), b.to_bits(), "price {i} moved");
        }
        for (i, (a, b)) in shock_off.iter().zip(shock_on.iter()).enumerate() {
            assert_eq!(a.to_bits(), b.to_bits(), "shock {i} moved");
        }
    }

    /// The columns arrive only when they are asked for, and they are the
    /// length of the table when they do.
    #[test]
    fn the_counterfactual_columns_are_present_only_when_the_arm_runs() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];

        let mut off = engine(7);
        let mut buf_off = SessionBuffer::new();
        off.run_session(&session(60, &innovations, &variances), &mut buf_off);
        assert!(buf_off.unbounded_print.is_empty());
        assert!(buf_off.liquidity_share.is_empty());
        assert_eq!(buf_off.shock.len(), buf_off.prices.len());

        let mut on = engine(7);
        on.set_settle_depth_counterfactual(true);
        let mut buf_on = SessionBuffer::new();
        on.run_session(&session(60, &innovations, &variances), &mut buf_on);
        assert_eq!(buf_on.unbounded_print.len(), buf_on.prices.len());
        assert_eq!(buf_on.liquidity_share.len(), buf_on.prices.len());
    }

    /// Every print splits into a shock and an absorption that add back up to
    /// the move, and liquidity's share is never an infinity.
    ///
    /// Seed 42 over a full session rather than a short one, because the two
    /// branches worth guarding are both rare. On this session 51 rows carry a
    /// counterfactual print that differs from the real one and 2 carry a
    /// share of NaN; a 60-tick session at another seed reached neither, so
    /// the assertions below ran over rows that could not fail them. Both
    /// counts are asserted, so a session that stops reaching a branch fails
    /// here rather than going quiet.
    ///
    /// The fixture NAMES pt-v18, because those counts are pt-v18's at seed
    /// 42 and the property is arithmetic on every print rather than a fact
    /// about the default: when the default moved to pt-v19 at 0.8.0 the same
    /// seed produced no unmoved print at all, and the NaN branch went
    /// unguarded -- which is exactly the failure the counts exist to raise,
    /// and the remedy is a fixture that reaches both branches, not a weaker
    /// assertion.
    #[test]
    fn every_print_decomposes_into_its_shock_and_its_absorption() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = Engine::with_params(
            42,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            crate::params::PT_V18,
        );
        e.set_settle_depth_counterfactual(true);
        let mut buf = SessionBuffer::new();
        e.run_session(&session(390, &innovations, &variances), &mut buf);

        let n = buf.companies;
        let mut checked = 0usize;
        let mut undefined = 0usize;
        let mut bound_moved = 0usize;
        // From tick one, so every row has a previous print on the same tape.
        for t in 1..buf.ticks_written {
            for i in 0..n {
                let row = t * n + i;
                let previous = buf.prices[(t - 1) * n + i];
                let moved = crate::mathx::log(buf.prices[row] / previous);
                let split = buf.shock[row] + buf.absorbed[row];
                assert!(
                    (split - moved).abs() < 1e-12,
                    "row {row}: shock + absorbed is {split}, the move is {moved}"
                );
                if buf.unbounded_print[row] != buf.prices[row] {
                    bound_moved += 1;
                }
                // Never an infinity. A share is NaN only where there was no
                // move to apportion, which is a statement about the row
                // rather than an escaped division.
                let share = buf.liquidity_share[row];
                assert!(!share.is_infinite(), "row {row}: liquidity share is {share}");
                if share.is_nan() {
                    undefined += 1;
                    assert_eq!(
                        buf.prices[row], previous,
                        "row {row}: a NaN share needs a print that did not move"
                    );
                    assert_ne!(
                        buf.unbounded_print[row], buf.prices[row],
                        "row {row}: a NaN share needs a counterfactual that moved"
                    );
                }
                checked += 1;
            }
        }
        assert!(checked > 0, "the session wrote no rows to check");
        assert!(
            bound_moved > 0,
            "no row had the depth bound move its print, so the counterfactual \
             assertions above ran over rows that could not fail them"
        );
        assert!(
            undefined > 0,
            "no row carried an undefined share, so the NaN branch is unguarded"
        );
    }

    #[test]
    fn a_chunked_session_matches_driving_the_ticks_by_hand() {
        // The whole point of `run_session` is that it is the SAME simulation,
        // just without the boundary crossings. If it diverged from the manual
        // loop it would be a second engine.
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];

        let chunked = {
            let mut e = engine(4242);
            let mut buf = SessionBuffer::new();
            e.run_session(&session(60, &innovations, &variances), &mut buf);
            (e.prices(), e.draws_consumed())
        };

        let by_hand = {
            let mut e = engine(4242);
            e.open_market();
            for m in 0..60i64 {
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
            }
            e.close_market(&DayCloseRequest {
                daily_innovations: &innovations,
                sector_base_variances: &variances,
                avg_volume: AvgVolumePolicy::default(),
            });
            (e.prices(), e.draws_consumed())
        };

        assert_eq!(chunked.1, by_hand.1, "draw counts must match");
        for (i, (a, b)) in chunked.0.iter().zip(&by_hand.0).enumerate() {
            assert_eq!(a.to_bits(), b.to_bits(), "company {i}");
        }
    }

    #[test]
    fn the_buffer_holds_every_tick_of_the_session() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(11);
        let mut buf = SessionBuffer::new();
        e.run_session(&session(40, &innovations, &variances), &mut buf);

        assert_eq!(buf.ticks_written, 40);
        assert_eq!(buf.companies, 3);
        assert_eq!(buf.prices.len(), 40 * 3);
        // The last written cross-section is the engine's current state.
        assert_eq!(buf.tick_prices(39), e.prices().as_slice());
        assert!(buf.prices.iter().all(|p| p.is_finite() && *p > 0.0));
    }

    #[test]
    fn the_buffer_is_reused_across_days_rather_than_growing() {
        // Nothing accumulates Rust-side: a year of tick-grain output would be
        // millions of rows, and the buffer is one day.
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(7);
        let mut buf = SessionBuffer::new();

        for _ in 0..5 {
            e.run_session(&session(30, &innovations, &variances), &mut buf);
            assert_eq!(buf.prices.len(), 30 * 3, "the buffer grew across days");
        }
    }

    #[test]
    fn a_stop_condition_halts_the_session_and_leaves_the_day_open() {
        // Event-driven advancement. The close must NOT run: the day is not
        // over, the caller interrupted it, and running the close would roll
        // momentum on a half-day.
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(31337);
        let mut buf = SessionBuffer::new();

        let mut req = session(390, &innovations, &variances);
        // A band tight enough that any movement trips it.
        let start = e.prices()[0];
        req.stop = Some(StopCondition::PriceOutside {
            company: 0,
            below: Some(start * 0.9999),
            above: Some(start * 1.0001),
        });

        let out = e.run_session(&req, &mut buf);
        assert!(out.halted_at.is_some(), "the stop condition never fired");
        let halted = out.halted_at.unwrap();
        assert!(
            halted < 389,
            "halted at the very end, so nothing was skipped"
        );
        assert_eq!(buf.ticks_written, halted + 1);
        // The close did not run. Probed via `last_daily_return`, which ONLY
        // `close_day` writes — `mispricing_momentum` would be the obvious
        // check and is wrong, because the tick lazy-initialises it to
        // Some(0.0) on a company that has never ticked.
        assert!(
            e.companies()[0].stock.last_daily_return.is_none(),
            "the close ran on an interrupted day"
        );
    }

    #[test]
    fn a_stop_condition_that_never_fires_runs_the_whole_session() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(5);
        let mut buf = SessionBuffer::new();
        let mut req = session(50, &innovations, &variances);
        req.stop = Some(StopCondition::PriceOutside {
            company: 0,
            below: Some(0.01),
            above: Some(1e9),
        });
        let out = e.run_session(&req, &mut buf);
        assert_eq!(out.halted_at, None);
        assert_eq!(buf.ticks_written, 50);
    }

    #[test]
    fn the_session_reports_the_draws_it_consumed() {
        let innovations = vec![None; 3];
        let variances = vec![0.000225; 3];
        let mut e = engine(13);
        // The DELTA, not the lifetime total. A default preset with a macro
        // burn-in draws during construction -- pt-v18 runs 755 days of it --
        // and this test is about what the operation costs, not about what
        // building an engine costs. Asserting the total made the claim
        // depend on a dial in a different subsystem.
        let before = e.draws_consumed();
        let mut buf = SessionBuffer::new();
        let out = e.run_session(&session(10, &innovations, &variances), &mut buf);
        // 10 open ticks at 1 + 3 sectors + 2 and 4 per company.
        assert_eq!(out.draws_consumed, 10 * (1 + 3 + 2 * 3 + 4 * 3));
        assert_eq!(e.draws_consumed() - before, out.draws_consumed);
    }

    #[test]
    fn columns_are_aligned_with_the_company_order() {
        let e = engine(1);
        let prices = e.column(PriceField::Price);
        assert_eq!(prices.len(), e.len());
        for (i, c) in e.companies().iter().enumerate() {
            assert_eq!(prices[i], c.stock.price);
        }
    }

    #[test]
    fn writing_prices_updates_market_cap_with_them() {
        let mut e = engine(1);
        e.write_prices(&[1.0, 2.0, 3.0]);
        assert_eq!(e.prices(), vec![1.0, 2.0, 3.0]);
        assert_eq!(e.column(PriceField::MarketCap)[0], 1.0 * 1e8);
    }

    /// `idio_vol_alpha`: the VIX identity's idiosyncratic term reads the
    /// per-name ratio, as the tick's own draw does. A ratio of 4 on every
    /// name is exactly 4 times the term (a power of two scales each
    /// summand without rounding); one name at 4 lands in between; a ratio
    /// of one is the term with the state off. Every other term is the
    /// same. Dropping the ratio from `index_var.rs`, or passing the engine's
    /// ratios as an empty slice, fails here.
    #[test]
    fn the_vix_identity_reads_the_idiosyncratic_variance_ratio() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        let off = engine_v20(7).index_conditional_variance_terms_now();
        let params = crate::params::ModelParams {
            idio_vol_alpha: 0.2,
            idio_vol_beta: 0.5,
            ..crate::params::PT_V20
        };
        let mut e = Engine::with_params(
            7,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            params,
        );
        let one = e.index_conditional_variance_terms_now();
        assert_eq!(one, off);
        e.set_idio_vol_state(&[4.0; 3], &[0.0; 3], &[0.0; 3]).unwrap();
        let four = e.index_conditional_variance_terms_now();
        assert!(one.idio_raw > 0.0);
        assert_eq!(four.idio_raw, 4.0 * one.idio_raw);
        assert_eq!(
            crate::market::index_var::IndexVarianceTerms { idio_raw: one.idio_raw, ..four },
            one
        );
        e.set_idio_vol_state(&[4.0, 1.0, 1.0], &[0.0; 3], &[0.0; 3]).unwrap();
        let first = e.index_conditional_variance_terms_now();
        assert!(first.idio_raw > one.idio_raw && first.idio_raw < four.idio_raw);
    }

    #[test]
    fn a_full_session_runs_and_stays_bounded() {
        // 390 ticks with the day's boundaries, as an embedder would drive it.
        let mut e = engine(31337);
        e.open_market();
        for m in 0..390i64 {
            e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
        }
        e.close_market(&DayCloseRequest {
            daily_innovations: &[None; 3],
            sector_base_variances: &[0.000225; 3],
            avg_volume: AvgVolumePolicy::default(),
        });

        for (i, price) in e.prices().iter().enumerate() {
            assert!(
                price.is_finite() && *price > 0.0,
                "company {i} priced at {price}"
            );
            // The session breaker bounds every print against the open.
            let open = e.companies()[i].stock.previous_close;
            assert!(
                *price <= open * 1.25 + 1e-9 && *price >= (open * 0.75).max(0.01) - 1e-9,
                "company {i} escaped the session band: {price} against an open of {open}"
            );
        }
    }

    #[test]
    fn fundamentals_round_trip_including_the_absent_marker() {
        let mut e = engine(7);
        let n = e.len();
        assert!(n >= 3);

        e.set_fundamentals(
            &[8.0, f64::NAN, 0.0],
            &[25.0, 30.0, f64::NAN],
            &[0.2, 0.3, 0.4],
        )
        .expect("one value per company");

        let (eps, book, growth) = e.fundamentals();
        assert_eq!(eps[0], 8.0);
        assert!(eps[1].is_nan(), "NaN must survive as absent");
        // Zero is a real EPS -- a company that broke exactly even -- and must
        // NOT be confused with absent.
        assert_eq!(eps[2], 0.0);
        assert!(e.companies()[2].eps == Some(0.0));
        assert!(e.companies()[1].eps.is_none());
        assert_eq!(book[1], 30.0);
        assert!(book[2].is_nan());
        assert_eq!(growth[0], 0.2);
    }

    #[test]
    fn a_length_mismatch_is_refused_rather_than_truncated() {
        let mut e = engine(7);
        let n = e.len();
        assert!(e.set_fundamentals(&vec![1.0; n - 1], &vec![1.0; n], &vec![1.0; n]).is_err());
        assert!(e.set_fundamentals(&vec![1.0; n], &vec![1.0; n + 1], &vec![1.0; n]).is_err());
        assert!(e.set_fundamentals(&vec![1.0; n], &vec![1.0; n], &vec![1.0; n]).is_ok());
    }

    #[test]
    fn stale_earnings_move_the_price_which_is_why_this_exists() {
        // The claim the whole sync rests on: fair value is `eps * target_pe`,
        // so an engine that never hears about an earnings revision prices the
        // company on the fundamentals it was built with.
        //
        // Two identical engines, one told that every company doubled its
        // earnings. If the prices came out the same, syncing fundamentals
        // would be pointless work.
        //
        // The revision lands after the open. Before it, pt-v20's stationary
        // opening (pt-v20 and pt-v21) adopts any premium into the
        // mispricing so the opening price holds, and a revision there moved
        // no price inside a 60-tick session; after the open it moves the
        // price on every preset.
        let mut stale = engine(11);
        let mut fresh = engine(11);
        for e in [&mut stale, &mut fresh] {
            e.open_market();
            e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        }

        let n = fresh.len();
        let (eps, book, growth) = fresh.fundamentals();
        let doubled: Vec<f64> = eps.iter().map(|v| v * 2.0).collect();
        fresh
            .set_fundamentals(&doubled, &book, &growth)
            .expect("one value per company");

        for e in [&mut stale, &mut fresh] {
            e.run_session(&stepped(60, 30, &[], &[], &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        }

        let a = stale.prices();
        let b = fresh.prices();
        assert_eq!(a.len(), n);
        assert!(
            a.iter().zip(b.iter()).any(|(x, y)| x != y),
            "doubling every company's earnings changed no price at all"
        );
        // And it is the RNG-free difference: both engines drew the same
        // number of times, so the divergence is the valuation rather than a
        // shifted stream.
        assert_eq!(stale.draws_consumed(), fresh.draws_consumed());
    }

    #[test]
    fn the_relative_knee_pulls_only_a_name_past_it_toward_it() {
        // `fair_value_relative_knee`: at the close, a name whose level sits
        // more than the knee below the equal-weighted mean moves toward the
        // knee by `1 - 0.5^(1/h)` of the gap; every other name is untouched.
        let mut p = crate::params::PT_V20;
        p.fair_value_relative_knee = 2.0;
        p.fair_value_relative_half_life = 10.0;
        let mut e = Engine::with_params(
            5,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            p,
        );
        assert!(e.carries_fair_value_offsets());
        for c in e.companies.iter_mut() {
            c.stock.mispricing_s = Some(0.0);
        }
        e.set_fair_value_offsets(&[-6.0, 0.3, -0.3]).unwrap();
        e.pull_relative_levels();
        let v = e.fair_value_offsets();
        let mean = (-6.0 + 0.3 - 0.3) / 3.0;
        let pull = 1.0 - crate::mathx::pow(0.5, 1.0 / 10.0);
        assert_eq!(v[0], -6.0 + pull * ((mean - 2.0) - (-6.0)));
        assert_eq!(v[1], 0.3);
        assert_eq!(v[2], -0.3);
        // Off at 0.0: nothing moves, whatever the half-life.
        e.params.fair_value_relative_knee = 0.0;
        e.set_fair_value_offsets(&[-6.0, 0.3, -0.3]).unwrap();
        e.pull_relative_levels();
        assert_eq!(e.fair_value_offsets(), vec![-6.0, 0.3, -0.3]);
    }

    #[test]
    fn a_bankrupt_company_stops_ticking_once_the_engine_is_told() {
        // The reason `set_status` exists. The tick skips a company only when
        // it reads `is_bankrupt || !is_public`, so before there was a setter
        // a failed company went on printing prices for ever.
        let mut e = engine(5);
        e.open_market();
        e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        let before = e.prices()[0];

        e.set_status(&[true, false, false], &[true, true, true])
            .expect("one flag per company");
        e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());

        assert_eq!(
            e.prices()[0],
            before,
            "company 0 was marked bankrupt and kept printing"
        );
        // And its neighbours did keep moving, so the test is not observing a
        // dead market.
        assert!(
            e.prices()[1] != before || e.prices()[2] != before,
            "nothing moved at all; this proves nothing about the flag"
        );
    }

    #[test]
    fn a_company_taken_private_stops_ticking_too() {
        let mut e = engine(5);
        e.open_market();
        e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        let before = e.prices()[1];

        e.set_status(&[false; 3], &[true, false, true])
            .expect("one flag per company");
        e.run_session(&session(30, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
        assert_eq!(e.prices()[1], before, "an unlisted company kept printing");
    }

    #[test]
    fn status_round_trips_and_refuses_a_length_mismatch() {
        let mut e = engine(5);
        e.set_status(&[true, false, true], &[false, true, false])
            .expect("one flag per company");
        let (bankrupt, public) = e.status();
        assert_eq!(bankrupt, vec![true, false, true]);
        assert_eq!(public, vec![false, true, false]);
        assert!(e.set_status(&[true; 2], &[true; 3]).is_err());
        assert!(e.set_status(&[true; 3], &[true; 4]).is_err());
    }

    #[test]
    fn no_preset_opens_with_its_macro_calendars_in_the_future() {
        // The defect this guards, measured on pt-v18 before the fix: the
        // 755-day burn-in ran on the caller's own day numbering, so it left
        // `next_meeting_date` at day 781 and `oil_last_opec_day` at day 720.
        // A run starts at day 1, both are read as differences against the
        // current day, and so the first central-bank meeting fell on day 781
        // against pt-v16's day 45 and the first OPEC decision on day 810
        // against day 90. The certified 252-day horizon held neither.
        //
        // Asserted as BEHAVIOUR over the certified horizon rather than as
        // clock values, because the clock values are an implementation of
        // this and the horizon is the claim. Every shipped preset, so a
        // preset that switches the burn-in on later cannot reintroduce it.
        for name in crate::params::ModelParams::preset_names() {
            let params = crate::params::ModelParams::preset(name)
                .expect("a listed preset resolves");
            let mut e = Engine::with_params(
                3,
                vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
                create_initial_economy_state(&InitialEconomyOptions::default()),
                create_initial_central_bank_state(0),
                sectors(),
                params,
            );
            assert!(
                e.economy().oil_last_opec_day <= 0,
                "{name}: the run opens with its last OPEC decision on day {},                  which is in the future of a run that starts at day 1",
                e.economy().oil_last_opec_day
            );
            let mut meetings = 0;
            let mut opec_days = Vec::new();
            let mut last_opec = e.economy().oil_last_opec_day;
            // The certified horizon, 252 days (`envelope.CERTIFIED_HORIZON_DAYS`).
            for day in 1..=252i64 {
                let out = e.advance_macro_day(day);
                if out.meeting_held {
                    meetings += 1;
                }
                if e.economy().oil_last_opec_day != last_opec {
                    last_opec = e.economy().oil_last_opec_day;
                    opec_days.push(day);
                }
            }
            assert!(
                meetings > 0,
                "{name}: no central-bank meeting in the certified 252 days, so \n                 monetary policy is inert across the whole window every \n                 published figure is measured on"
            );
            assert!(
                !opec_days.is_empty(),
                "{name}: no OPEC decision in the certified 252 days"
            );
        }
    }

    #[test]
    fn a_held_meeting_reports_which_decision_produced_the_rate_move() {
        // `update_central_bank` computes a `Decision` and an announcement
        // variant, and `advance_day` used to reduce both to a boolean. An
        // embedder saw the rate move with nothing able to say why, and the
        // obvious workaround -- classifying from the rate delta -- cannot
        // separate `StagflationHike` from `LaborEmergencyCut`, which differ
        // by the context that selected them rather than the size of the move.
        let mut e = engine(3);
        let mut held = 0;
        let mut quiet = 0;
        for day in 1..=400i64 {
            let out = e.advance_macro_day(day);
            if out.meeting_held {
                held += 1;
                assert!(
                    out.decision.is_some(),
                    "day {day}: a meeting was held with no decision reported"
                );
                assert!(
                    out.announcement_variant.is_some(),
                    "day {day}: a meeting was held with no announcement variant"
                );
                assert!(out.announcement_variant.unwrap() < 6, "variant out of range");
            } else {
                quiet += 1;
                assert!(out.decision.is_none());
                assert!(out.announcement_variant.is_none());
            }
        }
        assert!(held > 0, "no meeting in 400 days, so the test proved nothing");
        assert!(quiet > 0, "every day was a meeting, so the None case is untested");
    }

    // ----------------------------------------------------------------------
    // The per-day state hash (P9)
    // ----------------------------------------------------------------------

    #[test]
    fn the_hash_column_order_and_the_position_table_agree() {
        // Two declarations of one order. `state_hash_position` is exhaustive
        // on `PriceField`, so a nineteenth column fails to compile there; this
        // is what stops the two drifting apart once it does.
        for (i, field) in STATE_HASH_COLUMNS.iter().enumerate() {
            assert_eq!(
                state_hash_position(*field),
                i,
                "{field:?} sits at {i} in STATE_HASH_COLUMNS"
            );
        }
        let mut seen: Vec<usize> = STATE_HASH_COLUMNS
            .iter()
            .map(|f| state_hash_position(*f))
            .collect();
        seen.sort_unstable();
        seen.dedup();
        assert_eq!(seen.len(), 18, "every column has its own position");
    }

    #[test]
    fn a_pt_v20_engine_walks_the_cycle_shares_once() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        // `refresh_earnings_anticipation` asks for the cycle's stationary
        // shares on every close, burn-in included, and pt-v20 is the first
        // preset that turns anticipation on. Each answer is a survival walk
        // of about a thousand `pow` calls a phase. Before the memo, building
        // this engine took 756 walks, 96 per cent of its construction time.
        let start = crate::economy::cycle::share_walks();
        let mut e = engine_v20(7);
        assert_ne!(e.params().earnings_anticipation_half_life, 0.0);
        for day in 1..=3i64 {
            e.open_market();
            for m in 0..10 {
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
            }
            e.close_day(day);
        }
        let walks = crate::economy::cycle::share_walks() - start;
        assert!(walks <= 1, "{walks} survival walks for one fixed cycle spec");
    }

    #[test]
    fn the_state_hash_works_out_the_model_fingerprint_once() {
        // pt-v20 by name, the default until 0.10.0: pt-v21 sets the dials
        // this test reads as off. Each `engine_v20` here read `engine`.
        // The sandbox's tamper guard hashes the state before and after every
        // call into agent code. The hash folds in the model's fingerprint,
        // which was twenty digests of the preset surface each time and about
        // 90 per cent of the hash's cost.
        let e = engine_v20(7);
        let first = e.state_hash(0, false);
        let taken = crate::params::digests_taken();
        for _ in 0..5 {
            assert_eq!(e.state_hash(0, false), first);
        }
        let copy = e.clone();
        assert_eq!(copy.state_hash(0, false), first);
        assert_eq!(crate::params::digests_taken(), taken, "the fingerprint was worked out again");
        assert_eq!(e.model_fingerprint(), e.params().fingerprint());
        assert_eq!(e.model_fingerprint(), "pt-v20");
        // And the default engine, which is pt-v21 from 0.10.0.
        assert_eq!(engine(8).model_fingerprint(), "pt-v21");
        assert_eq!(engine_v19(7).model_fingerprint(), "pt-v19");
    }

    #[test]
    fn a_clone_hashes_the_same_and_a_tick_moves_the_hash() {
        let mut e = engine(77);
        e.open_market();
        for m in 0..30 {
            e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
        }
        let copy = e.clone();
        assert_eq!(
            e.state_hash(3, true),
            copy.state_hash(3, true),
            "a clone is the same state, so it is the same leaf"
        );

        let before = e.state_hash(3, true);
        e.tick(&request(10, 5));
        assert_ne!(
            before,
            e.state_hash(3, true),
            "a tick moved prices and the generator, so the leaf must move"
        );
    }

    #[test]
    fn the_two_binding_owned_fields_reach_the_hash() {
        // `day_count` and `market_open` are in the snapshot and not on this
        // struct, so they arrive as arguments. A hash that ignored them would
        // let a leaf taken before a close pass for one taken after it.
        let e = engine(9);
        assert_ne!(e.state_hash(3, false), e.state_hash(4, false));
        assert_ne!(e.state_hash(3, false), e.state_hash(3, true));
    }

    #[test]
    fn two_generator_states_a_float_digest_would_collapse_hash_apart() {
        // The reason the generator states are hashed as `u64` bit patterns
        // rather than through the canonical-NaN float rule. Both states below
        // read as NaN when interpreted as an f64, so a float digest would
        // write the same eight bytes for two different generator positions
        // and a tampered position would verify.
        let left_bits = 0x7ff8_0000_0000_0001u64;
        let right_bits = 0x7ff8_0000_0000_0002u64;
        assert!(f64::from_bits(left_bits).is_nan());
        assert!(f64::from_bits(right_bits).is_nan());

        let mut a = engine(5);
        let mut b = engine(5);
        let mut sa = a.rng_state();
        sa.market.state = left_bits;
        a.set_rng_state(sa);
        let mut sb = b.rng_state();
        sb.market.state = right_bits;
        b.set_rng_state(sb);

        assert_ne!(
            a.state_hash(0, false),
            b.state_hash(0, false),
            "the two generator positions must hash apart"
        );
    }

    #[test]
    fn the_hash_follows_the_macro_chain_and_the_central_bank() {
        // `market_digest` covers nine columns and the draw count, so a macro
        // difference alone leaves it unmoved. This leaf has to see it: the
        // whole reason a ledger hashes state rather than prices.
        let mut e = engine(11);
        let before = e.state_hash(0, false);
        e.economy_mut().vix = e.economy().vix + 1.0;
        assert_ne!(before, e.state_hash(0, false), "the VIX moved");

        let mut f = engine(11);
        let before = f.state_hash(0, false);
        f.central_bank_mut().hawkish_dovish_score += 0.5;
        assert_ne!(before, f.state_hash(0, false), "the bank's score moved");
    }

    #[test]
    fn the_hash_separates_two_days_of_one_run() {
        // What a ledger needs: consecutive leaves of one run are distinct, so
        // a day swapped for its neighbour fails on its own leaf.
        let mut e = engine(31);
        let mut leaves = Vec::new();
        for day in 1..=4i64 {
            e.open_market();
            for m in 0..40 {
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
            }
            e.close_day(day);
            leaves.push(e.state_hash(day as u32, false));
        }
        for i in 0..leaves.len() {
            for j in (i + 1)..leaves.len() {
                assert_ne!(leaves[i], leaves[j], "days {i} and {j} share a leaf");
            }
        }
    }

    /// pt-v20 with every branch of the valuation live: the buyback term,
    /// the earnings cycle and its anticipation, and the re-mark itself.
    fn engine_repricing(seed: u64, on: bool) -> Engine {
        let mut p = ModelParams::preset("pt-v20").expect("pt-v20 ships");
        p.buyback_payout_share = 0.75;
        p.earnings_anticipation_half_life = 126.0;
        p.rate_pe_sensitivity = 3.0;
        p.macro_publication_repricing = if on { 1.0 } else { 0.0 };
        Engine::with_params(
            seed,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
            p,
        )
    }

    #[test]
    fn tick_fair_value_is_the_ticks_own_fundamental_to_the_bit() {
        // The re-mark reads fair value through `tick_fair_value`, a copy of
        // the tick's phase 2. If the two ever part, the re-mark prices a
        // state the tick does not, and the first tick moves again.
        //
        // The tick's `fundamental` is the fair value AFTER its own
        // permanent share of the minute's shocks (`fair_value_news_share`),
        // which the re-mark has no reason to read, so both shares are off
        // here and the column is the valuation the tick started from. The
        // opening's levels, the nominal path, the earnings cycle and the
        // buyback term (the day set, so it is live) all stay on.
        let mut e = engine_repricing(21, false);
        e.params.fair_value_news_share = 0.0;
        e.params.fair_value_market_share = 0.0;
        for day in 1..=3i64 {
            e.set_current_day(day);
            e.open_market();
            for m in 0..30 {
                let start = e.clone();
                e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
                for (i, c) in start.companies().iter().enumerate() {
                    // A name's first tick runs the opening, which writes its
                    // fair-value level inside the tick; the re-mark never
                    // reads a name that has not traded.
                    if c.stock.mispricing_s.is_none() {
                        continue;
                    }
                    let fv = crate::market::tick::tick_fair_value(
                        start.params(), start.economy(), start.nominal_output_base(),
                        start.current_day(), c, c.stock.price);
                    assert_eq!(fv.to_bits(), e.tick_fundamental()[i].to_bits(),
                               "day {day} minute {m} name {i}");
                }
            }
            e.close_day(day);
        }
    }

    #[test]
    fn the_close_prices_what_its_macro_step_publishes() {
        let mut on = engine_repricing(33, true);
        let mut off = engine_repricing(33, false);
        for day in 1..=6i64 {
            for e in [&mut on, &mut off] {
                // The day set, so the buyback term reads the price.
                e.set_current_day(day);
                e.open_market();
                for m in 0..40 {
                    e.tick(&request(9 + (30 + m) / 60, (30 + m) % 60));
                }
                e.close_market(&DayCloseRequest {
                    daily_innovations: &[None, None, None],
                    sector_base_variances: &[0.0004, 0.0004, 0.0004],
                    avg_volume: crate::market::AvgVolumePolicy::Hold,
                });
            }
            // Pinned apart so every day's step moves fair value.
            let bump = if day % 2 == 0 { 0.5 } else { -0.4 };
            on.economy_mut().corporate_bond_yield += bump;
            off.economy_mut().corporate_bond_yield += bump;
            let last: Vec<f64> = on.prices();
            let marks = on.published_macro_marks().expect("on");
            assert!(off.published_macro_marks().is_none(), "off computes nothing");
            let s_before: Vec<Option<f64>> =
                on.companies().iter().map(|c| c.stock.mispricing_s).collect();
            on.advance_macro_day(day);
            off.advance_macro_day(day);
            for (i, c) in on.companies().iter().enumerate() {
                // The re-marked price is the one the name's mispricing
                // implies on the published state, fair value read at that
                // very price (the buyback term reads it).
                let fv1 = crate::market::tick::tick_fair_value(
                    on.params(), on.economy(), on.nominal_output_base(),
                    on.current_day(), c, c.stock.price);
                let want = last[i] / marks[i] * fv1;
                assert!((c.stock.price / want - 1.0).abs() < 1e-13,
                        "day {day} name {i}: {} against {want}", c.stock.price);
                assert_eq!(c.stock.mispricing_s, s_before[i], "the re-mark leaves s alone");
                assert!((c.stock.price / last[i] - 1.0).abs() > 1e-6, "the step moved fair value");
            }
            // Off, the close's price is the last print, as it always was.
            let off_prices = off.prices();
            let off_last: Vec<f64> = off.companies().iter().map(|c| c.stock.price).collect();
            assert_eq!(off_prices, off_last);
            // Put the pair back on one market for the next day, so each day
            // tests the step alone.
            let synced: Vec<f64> = on.prices();
            for (c, p) in off.companies_mut().iter_mut().zip(synced.iter()) {
                c.stock.price = *p;
                c.stock.market_cap = *p * c.stock.shares_outstanding;
            }
        }
    }
}


/// What a host that drives the engine relies on: whose opening it is, where
/// the previous close is, and where the crisis line sits.
#[cfg(test)]
mod host_tests {
    use super::*;
    use crate::economy::{create_initial_central_bank_state, create_initial_economy_state};

    fn roster(seed: u64) -> Vec<TickCompany> {
        crate::universe::random_universe(6, seed)
            .iter()
            .enumerate()
            .map(|(i, g)| g.to_init().to_tick_company(i))
            .collect()
    }

    fn sector_keys() -> Vec<String> {
        crate::sectors::keys().iter().map(|s| s.to_string()).collect()
    }

    /// A host's own opening: a crisis VIX, a 5 per cent policy rate and a
    /// contraction, none of which the default economy holds.
    fn host_economy() -> EconomyState {
        let mut e = create_initial_economy_state(&Default::default());
        e.vix = 45.0;
        e.federal_funds_rate = 5.0;
        e.cycle_phase = crate::economy::CyclePhase::Contraction;
        e
    }

    #[test]
    fn with_params_settles_a_host_economy_and_keeping_opening_keeps_it() {
        let p = ModelParams::preset("pt-v20").unwrap();
        let settled = Engine::with_params(
            7, roster(7), host_economy(), create_initial_central_bank_state(0),
            sector_keys(), p.clone(),
        );
        let kept = Engine::with_params_keeping_opening(
            7, roster(7), host_economy(), create_initial_central_bank_state(0),
            sector_keys(), p,
        );
        eprintln!(
            "pt-v20 host opening VIX 45.0, policy rate 5.00, contraction: \
             with_params opens at VIX {:.2}, rate {:.2}, {:?}; \
             with_params_keeping_opening at VIX {:.2}, rate {:.2}, {:?}",
            settled.economy().vix, settled.economy().federal_funds_rate,
            settled.economy().cycle_phase,
            kept.economy().vix, kept.economy().federal_funds_rate,
            kept.economy().cycle_phase,
        );
        assert!(settled.opening_settled());
        assert!((settled.economy().vix - 45.0).abs() > 5.0, "the burn-in relaxes the VIX");
        assert!(!kept.opening_settled());
        assert_eq!(kept.economy().vix, 45.0);
        assert_eq!(kept.economy().federal_funds_rate, 5.0);
        assert_eq!(kept.economy().cycle_phase, crate::economy::CyclePhase::Contraction);
    }

    #[test]
    fn a_preset_with_nothing_to_settle_reports_an_unsettled_opening() {
        // pt-v1 has no burn-in and no stationary draw, so `with_params`
        // keeps the economy too, and says so.
        let e = Engine::with_params(
            7, roster(7), host_economy(), create_initial_central_bank_state(0),
            sector_keys(), ModelParams::preset("pt-v1").unwrap(),
        );
        assert!(!e.opening_settled());
        assert_eq!(e.economy().vix, 45.0);
    }

    #[test]
    fn keeping_the_opening_is_from_opening_with_settle_false() {
        let p = ModelParams::preset("pt-v20").unwrap();
        let mut a = Engine::with_params_keeping_opening(
            9, roster(9), host_economy(), create_initial_central_bank_state(0),
            sector_keys(), p.clone(),
        );
        let mut b = Engine::with_params_from_opening(
            9, roster(9), host_economy(), create_initial_central_bank_state(0),
            sector_keys(), p, false,
        );
        let mut buffer = crate::engine::SessionBuffer::new();
        for day in 1..=3 {
            for e in [&mut a, &mut b] {
                e.open_market();
                let bell = crate::market::GameTime::new(9, 30, 3);
                e.run_session(&SessionRequest::new(bell, 390), &mut buffer);
                e.close_day(day);
            }
        }
        assert_eq!(a.state_hash(3, false), b.state_hash(3, false));
    }

    fn run_day(e: &mut Engine, day: i64, buffer: &mut SessionBuffer) -> Vec<f64> {
        e.open_market();
        let bell = crate::market::GameTime::new(9, 30, 3);
        e.run_session(&SessionRequest::new(bell, 390), buffer);
        let last_print = e.prices();
        e.close_day(day);
        last_print
    }

    #[test]
    fn prior_closes_are_the_last_print_and_previous_close_is_the_open() {
        let mut e = Engine::with_params(
            11, roster(11), create_initial_economy_state(&Default::default()),
            create_initial_central_bank_state(0), sector_keys(),
            ModelParams::preset("pt-v20").unwrap(),
        );
        let built = e.prices();
        assert_eq!(e.last_closes(), built.as_slice());
        assert_eq!(e.prior_closes(), built.as_slice());

        let mut buffer = SessionBuffer::new();
        let mut last_print = run_day(&mut e, 1, &mut buffer);
        assert_eq!(e.last_closes(), last_print.as_slice());
        let mut differed = 0;
        for day in 2..=6 {
            e.open_market();
            assert_eq!(e.prior_closes(), last_print.as_slice());
            let previous_close = e.column(PriceField::PreviousClose);
            // pt-v20 re-marks at the close, so the open the session band is
            // anchored on is not the last print.
            differed += previous_close
                .iter()
                .zip(&last_print)
                .filter(|(a, b)| a != b)
                .count();
            let bell = crate::market::GameTime::new(9, 30, 3);
            e.run_session(&SessionRequest::new(bell, 390), &mut buffer);
            last_print = e.prices();
            e.close_day(day);
            // Still the day's reference after its close, until the next open.
            assert_ne!(e.prior_closes(), last_print.as_slice());
            assert_eq!(e.last_closes(), last_print.as_slice());
        }
        assert!(differed > 0, "previous_close equalled the last print on every name and day");
    }

    #[test]
    fn closes_follow_the_roster_and_restore_checks_lengths() {
        let mut e = Engine::with_params(
            12, roster(12), create_initial_economy_state(&Default::default()),
            create_initial_central_bank_state(0), sector_keys(),
            ModelParams::preset("pt-v20").unwrap(),
        );
        let n = e.len();
        let mut joiner = roster(99).remove(0);
        joiner.id = "JOIN".into();
        joiner.ticker = "JOIN".into();
        let price = joiner.stock.price;
        e.add_company(joiner);
        assert_eq!(e.last_closes().len(), n + 1);
        assert_eq!(e.prior_closes()[n], price);
        let second = e.last_closes()[1];
        e.remove_company(0);
        assert_eq!(e.last_closes().len(), n);
        assert_eq!(e.last_closes()[0], second);

        assert!(e.restore_closes(&[1.0], &[1.0]).is_err());
        let ones = vec![1.0; n];
        let twos = vec![2.0; n];
        e.restore_closes(&ones, &twos).unwrap();
        assert_eq!(e.last_closes(), ones.as_slice());
        assert_eq!(e.prior_closes(), twos.as_slice());
    }

    #[test]
    fn closes_change_no_price_and_no_hash() {
        // Restoring nonsense closes moves nothing the trajectory reads.
        let build = || Engine::with_params(
            13, roster(13), create_initial_economy_state(&Default::default()),
            create_initial_central_bank_state(0), sector_keys(),
            ModelParams::preset("pt-v20").unwrap(),
        );
        let (mut a, mut b) = (build(), build());
        let n = b.len();
        b.restore_closes(&vec![1e9; n], &vec![-1.0; n]).unwrap();
        let mut buffer = SessionBuffer::new();
        for day in 1..=3 {
            run_day(&mut a, day, &mut buffer);
            run_day(&mut b, day, &mut buffer);
        }
        assert_eq!(a.prices(), b.prices());
        assert_eq!(a.state_hash(3, false), b.state_hash(3, false));
    }

    #[test]
    fn the_crisis_threshold_is_the_presets_and_follows_an_override() {
        let p = ModelParams::preset("pt-v20").unwrap();
        let mut e = Engine::with_params_keeping_opening(
            5, roster(5), create_initial_economy_state(&Default::default()),
            create_initial_central_bank_state(0), sector_keys(), p.clone(),
        );
        assert_eq!(e.crisis_vix_threshold(), 30.88325108);
        assert_eq!(e.crisis_vix_threshold(), e.params().crisis_vix_threshold);
        e.economy_mut().vix = 30.88325108;
        assert!(!e.vix_above_crisis_threshold(), "the gate is a strict >");
        e.economy_mut().vix = 30.9;
        assert!(e.vix_above_crisis_threshold());

        let moved = p.with_override("crisis_vix_threshold", 30.0).unwrap();
        let mut e = Engine::with_params_keeping_opening(
            5, roster(5), create_initial_economy_state(&Default::default()),
            create_initial_central_bank_state(0), sector_keys(), moved,
        );
        assert_eq!(e.crisis_vix_threshold(), 30.0);
        e.economy_mut().vix = 30.5;
        assert!(e.vix_above_crisis_threshold());
        assert!(e.model_fingerprint().starts_with("custom-"));
    }
}
