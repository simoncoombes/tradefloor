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
use crate::params::ModelParams;
use crate::rng::{stream, DrawKind, DrawOverlay, DrawRecord, GameRng, Rng, RngState, Site};

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

/// The exact position of all three engine streams — the checkpoint half
/// that cannot be reconstructed from the columns.
///
/// One [`RngState`] per stream, because each stream has its own LCG
/// position AND its own Box-Muller spare. A checkpoint that carried only
/// one of the three would restore a market whose untouched domains replay
/// correctly and whose missing one silently starts a different sequence.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EngineRngState {
    pub market: RngState,
    pub economy: RngState,
    pub external: RngState,
    /// The jump stream. Carried here for the reason this type's own
    /// documentation gives: a stream left out of a checkpoint restores to a
    /// DIFFERENT sequence while looking correct. That is harmless while
    /// jumps are inert and silently wrong the day they are not.
    pub jumps: RngState,
    /// The persistent-volume stream, carried for the same reason.
    pub volume: RngState,
    /// The per-name volume stream, carried for the same reason.
    pub volume_idio: RngState,
    /// The endogenous-news stream, carried for the same reason. Left out,
    /// a restored engine would draw a different news sequence from the one
    /// it was checkpointed on, which is invisible while news is inert and
    /// silently wrong the day a preset switches it on.
    pub news: RngState,
    /// The overnight stream, carried for the same reason.
    pub overnight: RngState,
    /// The market factor's slow-level stream, carried for the same reason.
    pub market_vol_level: RngState,
    /// The crisis epicentre's stream, carried for the same reason. It draws
    /// only on the session an unpinned episode starts, so a restore that
    /// dropped it would give the NEXT crisis a different epicentre from the
    /// one the parent would have drawn -- invisible while
    /// `crisis_epicentre_extra` is 0.0 and silently wrong the day it is not.
    pub crisis_epicentre: RngState,
}

/// Cumulative draws per stream. Diagnostic, per D-R1: the single most
/// useful numbers for locating a divergence, and deliberately not part of
/// any behavioural contract.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
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
pub struct MarketDayLayout {
    pub company: u32,
    pub first: u64,
    pub stride: u64,
    pub ticks: u32,
}

/// What the embedder supplies for one tick.
#[derive(Debug, Clone)]
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

/// What one tick produced.
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

/// What the embedder supplies at the close of a simulated day.
#[derive(Debug, Clone)]
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

/// What the embedder supplies for the daily macro step.
#[derive(Debug, Clone)]
pub struct DayAdvanceRequest<'a> {
    pub volatility: f64,
    pub active_shocks: &'a [EconomicShock],
    pub market_return_pct: f64,
    pub game_day: i64,
    /// Game timestamp in minutes, for the central bank's meeting calendar.
    pub timestamp: i64,
}

#[derive(Debug, Clone, PartialEq)]
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
    /// The move each name's `s` took at the last open under the overnight
    /// process, in roster order, 0.0 where nothing moved. Per-day state
    /// like the attribution: the tape books it onto the day's first row.
    overnight_moves: Vec<f64>,
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
    /// is set; 0.0 means "not yet seeded" and reads as 1.0. See
    /// `programme/results/vix-dynamics.md` sections 19 and 19.7.
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
    tick_components: Vec<[f64; 8]>,
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
    current_day: i64,
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
    /// The model coefficients this engine runs (the runtime seam,
    /// CALIBRATION.md §5). [`crate::params::PT_V1`] unless the engine was
    /// built with [`Engine::with_params`]; immutable for the engine's life,
    /// which is what lets its fingerprint be quoted for the whole run.
    params: ModelParams,
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
    pub fn new(
        seed: u32,
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
    /// # The writes that already stand when this refuses
    ///
    /// This write is the boundary. Everything `PyEngine::restore_state`
    /// writes BEFORE it holds the snapshot's value, and everything it
    /// writes AFTER holds the engine's own, because the error propagates
    /// out of here and the later writes are attempted and never reached.
    /// The rule is positional, so it stays true as writes are added on
    /// either side, and reading `restore_state` in order is what tells a
    /// caller which side a given field is on.
    ///
    /// An engine that has caught this error therefore holds one run's
    /// market beside another run's macro state, and it should be dropped
    /// rather than run on. `tests/test_volume_idio_roster.py` asserts one
    /// field on each side of the boundary.
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
    /// Since 0.8.0 this is [`PT_V19`]: pt-v18 with four dials moved and
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
    pub const fn default_model() -> crate::params::ModelParams {
        crate::params::PT_V19
    }

    /// [`Engine::new`] under an explicit model preset (the runtime seam,
    /// CALIBRATION.md §5). With [`crate::params::PT_V1`] this IS `new`: the
    /// preset-constructed engine reproduces the const build's trajectories
    /// bit for bit, draw for draw — the phase-1 acceptance gate.
    pub fn with_params(
        seed: u32,
        companies: Vec<TickCompany>,
        economy: EconomyState,
        central_bank: CentralBankState,
        sector_keys: Vec<String>,
        params: ModelParams,
    ) -> Self {
        Self::with_params_from_opening(seed, companies, economy, central_bank,
                                       sector_keys, params, true)
    }

    /// [`Engine::with_params`], saying whether the opening is the model's to
    /// settle or the caller's to keep.
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
    pub fn with_params_from_opening(
        seed: u32,
        companies: Vec<TickCompany>,
        economy: EconomyState,
        central_bank: CentralBankState,
        sector_keys: Vec<String>,
        params: ModelParams,
        settle_opening: bool,
    ) -> Self {
        let companies_len = companies.len();
        // Read before the economy moves into the struct, and never
        // recomputed: this is where the run's nominal output starts.
        let nominal_output_base = economy.gdp * economy.cpi;
        let mut engine = Self {
            nominal_output_base,
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
            overnight_moves: vec![0.0; companies_len],
            companies,
            economy,
            central_bank,
            attribution: vec![[0.0; crate::market::factors::COMPONENT_COUNT]; companies_len],
            noise_parts: vec![[0.0; 3]; companies_len],
            noise_own_scale2: vec![0.0; companies_len],
            tick_components: vec![[0.0; 8]; companies_len],
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
            crisis_in_episode: false,
            crisis_sessions_under: 0,
            crisis_epicentre: -1,
            crisis_epicentre_pin: None,
            vix_sets_variance_pending: false,
            session_news: Vec::new(),
            volume_idio: vec![0.0; companies_len],
            jump_move: vec![0.0; companies_len],
            sector_variance: vec![0.0; sector_keys.len()],
            sector_day_factor: vec![0.0; sector_keys.len()],
            sector_target_day: 0.0,
            jump_excitation: vec![0.0; companies_len],
            sector_keys,
            draws: StreamDraws::default(),
            current_day: 0,
            day_marks: Vec::new(),
            params,
            // Replaced immediately below. Zero rather than the dial's own
            // value so that a path which somehow skipped the derivation
            // would divide by zero rather than run on a plausible number.
            vix_anchor: 0.0,
            // No day has closed, so no VIX update has read a variance.
            last_index_variance: None,
            last_market_targets: None,
        };
        engine.vix_anchor = engine.derive_vix_anchor();
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
        if settle_opening {
            engine.burn_in_economy();
        }
        engine
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
    /// clamped to the per-name multiples. `programme/results/vix-dynamics.md`
    /// 19.1 measures the tape's sector residual at persistence 0.971 +/-
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
        for c in self.companies.iter() {
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
                garch_variance: c.stock.garch_variance,
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
        let names = self.index_variance_names();
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
        crate::market::index_var::index_conditional_variance_terms_with_states(
            &self.params,
            &names,
            self.sector_keys.len(),
            factor_variance,
            &sigmas,
            &excitations,
            rate_scale,
            crisis_spike,
            // THE LAGGED TRANSMISSION WIRE, and the timing is the point.
            // `close_day_at` has already rolled `day_factor` into
            // `prev_day_factor`, so this bit is the one TOMORROW's ticks
            // will read — which is what a one-day-ahead variance needs. The
            // same accessor the tick calls, not a copy of the comparison.
            self.market_vol.prev_day_down(),
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
    fn burn_in_economy(&mut self) {
        // The day-zero phase and its age first, so what follows relaxes
        // the fields under the phase the run will OPEN in. Inert at the
        // default, where it returns without drawing and every line below
        // is the line that stood here.
        let drawn = self.draw_stationary_opening();
        if self.params.macro_burn_in_days <= 0.0 {
            return;
        }
        let days = self.params.macro_burn_in_days as i64;
        let phase = self.economy.cycle_phase;
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
        }
        if !drawn {
            self.economy.months_in_current_phase = 0.0;
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
    }

    /// The model coefficients this engine runs. Read-only: an engine's
    /// model is fixed at construction, so its fingerprint describes the
    /// whole run rather than the moment someone asked.
    pub fn params(&self) -> &ModelParams {
        &self.params
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
    /// It was a label until pt-v18. The sentence here used to read that
    /// nothing in the tick, the close or the macro chain consults it, so
    /// moving it could not move a trajectory. `buyback_payout_share` made
    /// that false: the buyback factor is an earnings yield over elapsed
    /// time and this is the elapsed time, so under a preset that sets that
    /// share, moving this number reprices every name.
    ///
    /// `open_market` is the only caller that must run before a draw is
    /// taken, because the day mark and the day's news draws are taken
    /// there, and `PyEngine::restore_state` pushes the restored day here
    /// for the mid-day case that no open follows.
    ///
    /// The valuation and the draw log want different things from this
    /// field, elapsed trading days and a label, and they coincide only
    /// because one counter serves both. Giving the valuation its own
    /// counter is the better answer and it costs a snapshot field and a
    /// state-hash entry, beside a `day` key the snapshot already carries,
    /// so it belongs to a preset boundary rather than to a fix inside one.
    pub fn set_current_day(&mut self, day: i64) {
        self.current_day = day;
        for id in 0..stream::COUNT as u32 {
            self.stream_rng_mut(id).set_day(day);
        }
    }

    pub fn current_day(&self) -> i64 {
        self.current_day
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
        }
        outcome
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
            SettleDrawPolicy::FourAlways => self.market_vol.sigma_daily(),
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
                day_factor: self.market_vol.day_factor(),
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
                market_status: status,
                intraday_t: intraday_fraction(request.time),
                volatility_multiplier: request.volatility_multiplier,
                news: request.news,
                news_impact_queue: request.news_impact_queue,
                order_volumes: request.order_volumes,
                sector_keys: &self.sector_keys,
                sector_sigmas: &sector_sigmas,
                market_sigma_daily,
                vix_anchor: self.vix_anchor,
                settle_draws,
                settle_depth_counterfactual: self.settle_depth_counterfactual,
                nominal_output_base: self.nominal_output_base,
                // Resolved at this session's `open_market` and fixed for
                // the day; `None` on every preset before pt-v19.
                crisis_epicentre: epicentre,
                elapsed_days: self.current_day,
                params: &self.params,
            },
            rng,
        );

        // Accumulate the day's factor innovation for the close's variance
        // update. A closed tick contributes exactly zero (the factor is
        // not drawn), and the replay path accumulates harmlessly into
        // state it never reads.
        self.market_vol.accumulate(outcome.shared_factors.market_factor);
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
            *slot = [0.0; 8];
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
                    acc[k] += value;
                }
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

        TickOutcome {
            market_status: status,
            active_indices: outcome.active_indices,
            fair_values: outcome.fair_values,
            volumes: outcome.volumes,
            draws_consumed: 0,
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
    pub fn tick_components(&self) -> &[[f64; 8]] {
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
        // Nine-wide attribution rows predate the overnight slot; they
        // restore with that slot at zero, which is what such a day held.
        let attribution: Vec<f64> = if n > 0 && attribution.len() == n * (crate::market::factors::COMPONENT_COUNT - 1) {
            attribution
                .chunks_exact(crate::market::factors::COMPONENT_COUNT - 1)
                .flat_map(|c| c.iter().copied().chain(std::iter::once(0.0)))
                .collect()
        } else {
            attribution.to_vec()
        };
        for (name, len, want) in [
            ("attribution", attribution.len(), n * crate::market::factors::COMPONENT_COUNT),
            ("tick_components", components.len(), n * 8),
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
            .chunks_exact(8)
            .map(|c| [c[0], c[1], c[2], c[3], c[4], c[5], c[6], c[7]])
            .collect();
        self.tick_fundamental = fundamental.to_vec();
        self.tick_anchor = anchor.to_vec();
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
        let whole = self.attribution_column(random_noise_index());
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

    pub fn open_market(&mut self) {
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
        self.tick_components.clear();
        self.tick_components.resize(self.companies.len(), [0.0; 8]);
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

        // The night, before the day's marks are set, so the session band
        // anchors on the post-gap open and the gap sits outside it.
        self.apply_overnight();

        reset_daily_prices(&mut self.companies);
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
        self.overnight_moves.clear();
        self.overnight_moves.resize(self.companies.len(), 0.0);
        let ratio = self.params.overnight_variance_ratio;
        if ratio == 0.0 {
            return;
        }
        let p = &self.params;
        let scale = crate::mathx::sqrt(ratio);
        let market_sigma_daily = self.market_vol.sigma_daily();
        let sector_sigma =
            crate::market::tick::sector_sigma_at(p, &self.economy, self.vix_anchor);
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
            let valuation = if nominal == 1.0 {
                company.valuation()
            } else {
                crate::market::tick::scale_valuation(company.valuation(), nominal)
            };
            let fv = crate::fair_value::compute_fair_value_with(
                &valuation, &econ_view, p.fair_value_book_floor,
                p.qe_pe_gain, p.qe_pe_stock_gain, p.neutral_discount_rate,
            )
            .fair_value;
            let price = crate::mathx::min(
                crate::mathx::max(fv * crate::mathx::exp(after), 0.01),
                p.price_hard_cap,
            );
            company.stock.price = price;
            company.stock.market_cap = price * company.stock.shares_outstanding;
        }
    }

    /// The move each name's `s` took at the last open under the overnight
    /// process, in roster order; 0.0 where nothing moved.
    pub fn overnight_moves(&self) -> &[f64] {
        &self.overnight_moves
    }

    /// Close-of-day bookkeeping. Zero draws.
    ///
    /// Must run BEFORE any earnings shock the embedder applies that evening:
    /// the momentum roll reads `s` as it stands at the close, and an earnings
    /// gap applied first would be counted again as next-day herding. The
    /// the reference implementation's earnings path patches `sPrevClose` by the shock for the
    /// same reason.
    pub fn close_market(&mut self, request: &DayCloseRequest) {
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
                // what `level-sigma-horizon.md` measures as the whole of
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
    fn apply_jumps(&mut self) {
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

        // The DAY's cap-weighted return, in the same percent units as
        // `market_return_pct`. Read only when `vix_return_source` is
        // non-zero, so the shipped path is untouched. `previous_close` is
        // set at the OPEN (market/daily.rs), so this is open to close, and
        // `apply_jumps` has already run, so the jumps are in `price`.
        let market_day_return_pct = if self.params.vix_return_source == 0.0
            && self.params.vix_level_identity == 0.0
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
            let anchor = self.vix_anchor * mult;
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
        }
        rng.site(Site::EconomyDaily, 0);
        self.economy = update_economy_daily(
            &self.economy,
            &DailyInputs {
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
                vix_anchor_level: self.vix_anchor * self.vix_level_multiplier(),
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
                vix_anchor_slow: self.vix_anchor_slow,
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
                volatility: request.volatility,
                active_shocks: request.active_shocks,
                market_return_pct: request.market_return_pct,
                game_day: request.game_day,
            },
            rng,
        );
        rng.site(Site::EconomyCycle, 0);
        let spec = self.cycle_spec();
        self.economy = check_cycle_transition_for(&self.economy, rng, &spec);

        let policy = crate::economy::PolicyOptions {
            calendar: self.macro_calendar(),
            liftoff: self.params.fed_liftoff_rule,
        };
        let meeting =
            {
                rng.site(Site::CentralBank, 0);
                update_central_bank_with(
                    &self.central_bank, &self.economy, request.timestamp, rng, &policy)
            };
        let meeting_held = meeting.decision.is_some();
        let decision = meeting.decision;
        let announcement_variant = meeting.announcement_variant;
        self.central_bank = meeting.central_bank;
        self.economy = meeting.economy;

        DayAdvanceOutcome {
            phase_changed: self.economy.cycle_phase != phase_before,
            meeting_held,
            decision,
            announcement_variant,
            draws_consumed: 0,
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
        buffer.resize(request.ticks, self.companies.len());
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
        let first_tick_flow = if request.fills.is_empty() {
            None
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
                    unbounded_print: &self.tick_unbounded_print,
                    liquidity_share: &self.tick_liquidity_share,
                },
            );

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

    pub fn close_day(&mut self, game_day: i64) {
        let noise = self.daily_innovation_column();
        let innovations: Vec<Option<f64>> = noise.into_iter().map(Some).collect();
        let variances = self.sector_base_variances();
        self.close_market(&DayCloseRequest {
            daily_innovations: &innovations,
            sector_base_variances: &variances,
            avg_volume: crate::market::AvgVolumePolicy::Hold,
        });
        self.advance_macro_day(game_day);
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
    /// - no active shocks: the shock system is not part of this surface.
    pub fn advance_macro_day(&mut self, game_day: i64) -> DayAdvanceOutcome {
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
                    // `market_pe_buybacks`: the earnings the valuation holds
                    // also carry the buyback term (`market::tick`), so a
                    // multiple read without it rises by the buyback yield
                    // every year. A branch, so 0.0 is the line that stood.
                    let earnings = if self.params.market_pe_buybacks != 0.0 {
                        earnings * crate::market::tick::buyback_scale(
                            &self.params, Some(earnings), c.stock.price, self.current_day)
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
        self.advance_day(&DayAdvanceRequest {
            volatility: 1.0,
            active_shocks: &[],
            market_return_pct,
            game_day,
            timestamp: game_day * 24 * 60,
        })
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
        self.companies.push(company);
        self.attribution.push([0.0; crate::market::factors::COMPONENT_COUNT]);
        self.noise_parts.push([0.0; 3]);
        self.noise_own_scale2.push(0.0);
        self.tick_components.push([0.0; 8]);
        self.tick_fundamental.push(f64::NAN);
        self.tick_anchor.push(f64::NAN);
        self.volume_idio.push(0.0);
        self.jump_move.push(0.0);
        // The per-name jump excitation follows the roster for the reason
        // `volume_idio` above does, and it was left out for the same
        // reason: it landed after this function was written. A name that
        // joins has never jumped, so its excitation is 0.0.
        self.jump_excitation.push(0.0);
        // The print decomposition, on the same argument as everything above
        // it: these are per-SLOT columns, and a roster edit that grew
        // `companies` without growing them would leave the new name reading
        // as a company that never moved.
        self.tick_shock.push(0.0);
        self.tick_absorbed.push(0.0);
        self.tick_clamp.push(0.0);
        // Only where the arm is running. Empty means no arm, and pushing
        // into an empty pair would turn that into a one-row column.
        if !self.tick_unbounded_print.is_empty() {
            self.tick_unbounded_print.push(f64::NAN);
            self.tick_liquidity_share.push(0.0);
        }
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
        if index < self.noise_own_scale2.len() {
            self.noise_own_scale2.remove(index);
        }
        if index < self.volume_idio.len() {
            self.volume_idio.remove(index);
        }
        if index < self.jump_move.len() {
            self.jump_move.remove(index);
        }
        // `Vec::remove` for the reason the line above uses it: the tail
        // shifts down and keeps its relative order, so every remaining
        // name keeps its own excitation.
        if index < self.jump_excitation.len() {
            self.jump_excitation.remove(index);
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
        if index < self.tick_unbounded_print.len() {
            self.tick_unbounded_print.remove(index);
        }
        if index < self.tick_liquidity_share.len() {
            self.tick_liquidity_share.remove(index);
        }
        Some(self.companies.remove(index))
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
    pub fn book_for(&self, index: usize) -> Option<crate::order_book::OrderBook> {
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
        for (i, company) in self.companies.iter_mut().enumerate() {
            company.is_bankrupt = is_bankrupt[i];
            company.is_public = is_public[i];
        }
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

    /// Convenience for the most-read column.
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
        self.state_hash_with_pending(day_count, market_open, &[], &[])
    }

    /// [`Engine::state_hash`], carrying the wrapper's pending tape state.
    pub fn state_hash_with_pending(
        &self,
        day_count: u32,
        market_open: bool,
        pending_jump: &[f64],
        pending_overnight: &[f64],
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
        hash_str(&mut buf, &self.params.fingerprint());

        // The day accumulators, in snapshot order.
        for row in &self.attribution {
            for value in row {
                hash_f64(&mut buf, *value);
            }
        }
        for row in &self.tick_components {
            for value in row {
                hash_f64(&mut buf, *value);
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
        // `qe_assets_ratio` is absent from both, which is a gap in the
        // snapshot rather than a decision taken here.
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
pub struct SessionRequest<'a> {
    pub start: GameTime,
    pub ticks: usize,
    pub volatility_multiplier: f64,
    pub news: &'a [NewsEvent],
    pub news_impact_queue: &'a [NewsImpactEntry],
    /// Order volume held on EVERY tick of the session: a standing rate, in
    /// shares per minute, for a program that trades all session long.
    pub order_volumes: &'a [(String, OrderVolume)],
    /// Order volume that reaches the market ONCE, on the session's first
    /// tick: the trades an agent filled at the step boundary just before it.
    ///
    /// # Why this is not `order_volumes`
    ///
    /// Until 0.9.0 every harness handed an agent's fills to the session as
    /// `order_volumes`, so one order was counted on every tick of the step:
    /// 65 times at six steps a day, 390 at one. It also landed only after the
    /// agent had filled at the pre-trade book, so the agent never paid its
    /// own permanent impact and collected it instead. A spec mean reversion
    /// rule beat buy-and-hold on 20 of 20 suite markets by a median 42
    /// points in 60 days on that alone (design repo,
    /// `programme/meanrev-edge-ptv19-2026-09-24.md`).
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
    pub daily_innovations: &'a [Option<f64>],
    pub sector_base_variances: &'a [f64],
    /// Stop early when a condition is met, for event-driven advancement.
    pub stop: Option<StopCondition>,
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
    /// The seven component columns, each `ticks * companies`, in
    /// `S_COMPONENT_KEYS` order. Seven flat buffers rather than one of
    /// `[f64; 7]`, because each becomes an Arrow column and a column wants a
    /// contiguous run of its own values.
    pub components: [Vec<f64>; 8],
    /// The print decomposition, each `ticks * companies`: the shock that
    /// arrived and the depth that absorbed it, in log units.
    pub shock: Vec<f64>,
    pub absorbed: Vec<f64>,
    /// How far the print breaker moved each print. `absorbed - clamp` is the
    /// book's own share of the distance from the model price to the tape.
    pub clamp: Vec<f64>,
    /// The depth counterfactual, each `ticks * companies` when it ran and
    /// EMPTY when it did not. Emptiness is the signal, which is why these
    /// two are not resized alongside the columns above.
    pub unbounded_print: Vec<f64>,
    pub liquidity_share: Vec<f64>,
}

/// One tick's ground truth, as the engine holds it, handed to
/// [`SessionBuffer::write_tick`].
///
/// A struct rather than nine positional slices. The call site passes nine
/// same-typed buffers and a transposition there would compile, run, and
/// mislabel every row of every column it touched.
pub struct TickTruth<'a> {
    pub components: &'a [[f64; 8]],
    pub fundamental: &'a [f64],
    pub anchor: &'a [f64],
    pub shock: &'a [f64],
    pub absorbed: &'a [f64],
    pub clamp: &'a [f64],
    pub unbounded_print: &'a [f64],
    pub liquidity_share: &'a [f64],
}

impl SessionBuffer {
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
            let row = truth.components.get(i).copied().unwrap_or([0.0; 8]);
            for (k, column) in self.components.iter_mut().enumerate() {
                column[base + i] = row[k];
            }
        }
    }

    /// One tick's cross-section, contiguous.
    pub fn tick_prices(&self, tick: usize) -> &[f64] {
        let base = tick * self.companies;
        &self.prices[base..base + self.companies]
    }
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

    fn engine(seed: u32) -> Engine {
        Engine::new(
            seed,
            vec![company("A", 100.0), company("B", 50.0), company("C", 220.0)],
            create_initial_economy_state(&InitialEconomyOptions::default()),
            create_initial_central_bank_state(0),
            sectors(),
        )
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
        let seeds: Vec<Vec<f64>> = (1u32..=4).map(run).collect();
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
        let run = |fresh_phase: bool| {
            let mut e = engine(4242);
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
        let day = DayAdvanceRequest {
            volatility: 0.7,
            active_shocks: &[],
            market_return_pct: 0.0,
            game_day: 1,
            timestamp: 24 * 60,
        };

        let mut e = engine(21);
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
        let mut counted = engine(21);
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
        let mut down = engine(21);
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

        let mut once = engine(11);
        once.open_market();
        let mut buf = SessionBuffer::new();
        once.run_session(&stepped(65, 0, &[], &fills, &innovations, &variances), &mut buf);

        let mut spelled = engine(11);
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
    /// what every harness did with an agent's fills until 0.9.0, and the
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
            let mut e = engine(11);
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
        let mut stale = engine(11);
        let mut fresh = engine(11);

        let n = fresh.len();
        let (eps, book, growth) = fresh.fundamentals();
        let doubled: Vec<f64> = eps.iter().map(|v| v * 2.0).collect();
        fresh
            .set_fundamentals(&doubled, &book, &growth)
            .expect("one value per company");

        for e in [&mut stale, &mut fresh] {
            e.open_market();
            e.run_session(&session(60, &[None; 3], &[0.000225; 3]), &mut SessionBuffer::new());
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
/// Returns `None` for an unknown preset.
pub fn fixed_simulation_digest(
    size: usize,
    universe_seed: u32,
    seed: u32,
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
    for day in 1..=days {
        engine.open_market();
        engine.run_session(
            &SessionRequest {
                start: crate::market::GameTime { hour: 9, minute: 30, day_of_week: 3 },
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
            },
            &mut buffer,
        );
        engine.close_day(day as i64);
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
