//! A population: background traders that share the book with the agents.
//!
//! # What it is for
//!
//! An evaluated agent trades against a market maker, latent depth and the
//! model's own flow. None of them reacts to what the agent does, so an edge
//! never meets anyone who has noticed it: ten copies of a strategy earn what
//! one earns (each in its own market), and a programme that buys the same
//! slices at the same minutes every day pays what a randomised one pays. A
//! population is the opt-in answer to "does this edge survive others
//! trading?". It is a small set of deterministic participants that trade in
//! the same agent-facing book as the agents, see the same prices and pay the
//! same costs.
//!
//! # The five archetypes
//!
//! Every participant is a policy over observable state: prices, each name's
//! daily volume and daily sigma as the book reads them, the published VIX,
//! and the agents' taker flow as it reaches the tape. Each sets a target
//! position per name, in shares, and trades toward it with market orders.
//! Sizes are fractions of the name's daily volume `V`.
//!
//! - **Trend** piles into moves: target `size V clamp(m / scale)` with `m`
//!   the log return since the close `lookback` sessions ago over `sigma
//!   sqrt(lookback)`. At a lookback of five it trades the five-day momentum
//!   signal.
//! - **Reversion** supplies the other side of moves: the same signal with
//!   the sign turned. At a lookback of one it trades the one-day reversal.
//! - **Liquidity** leans against short moves while markets are calm and
//!   withdraws as they become volatile: target `-size V w clamp(z / scale)`,
//!   `z` the log price over its own moving average (half-life `half_life`
//!   ticks) in units of `sigma sqrt(half_life / 390)`, and `w` one at or
//!   below a VIX of `vix_calm`, zero at or above `vix_stress`, linear
//!   between. As the VIX rises it unwinds and stops supplying.
//! - **Detector** learns the agents' flow and positions ahead of it. Per
//!   name and per `bucket` ticks of the session it keeps an exponential
//!   memory (half-life `memory` sessions) of the agents' net taker flow,
//!   as a share of `V`: a mean `m` and a variance `v`. Its prediction for a
//!   bucket is `m r^2` with `r = m^2 / (m^2 + v)`, the mean shrunk by how
//!   reliably it recurs, squared so that flow seen once or at scattered
//!   minutes counts for little. It holds the predicted flow of the window from `hold` ticks
//!   behind to `lead` ticks ahead, so it buys `lead` ticks before flow it
//!   expects and sells `hold` ticks after. Persistent flow (the same side
//!   every day) and predictable flow (the same minutes every day) are what
//!   it can learn; flow at random minutes averages out to a small,
//!   unreliable prediction. It observes the agents' net flow per name, not
//!   per label, so four labels sending a quarter each look like one label
//!   sending the whole (co-impact). It adds to a position only where the
//!   quoted spread is at most `max_spread` of the name's daily sigma: on a
//!   name whose spread is a large part of its daily move, a round trip costs
//!   more than riding the flow can earn.
//!   Detectors with the same `memory` and `bucket` see the same flow, so
//!   they keep one profile, held by the first of them; each still trades on
//!   its own ticks and books its own fills.
//! - **Crowd** trades a ranked signal, the one an agent's ranked trend rule
//!   trades: the simple return over `lookback` open ticks, momentum (long
//!   the risers) or reversal (long the fallers), long the first `top_k`
//!   names and short the last `top_k`, each at `size V`. A held name stays
//!   until it slips `buffer` ranks past the edge, so a name on the boundary
//!   does not churn. It decides where the session's tick count, modulo
//!   `interval`, equals `offset`, and keeps its price at each decision, so
//!   a crowd that looks every five ticks reaches a new loser before an agent
//!   that decides every sixty-five, and one that decides near the close is
//!   in before a rule that rebalances at the next open. That is signal
//!   competition: the crowd takes the mispricing first and the agent meets
//!   the price it left. With `stop` above zero it carries a loss limit:
//!   it books what its positions made between its decisions (its price
//!   P&L, before what it paid to trade), and when that has fallen `stop`
//!   of its full book's gross from its best it sells out, then takes back
//!   `recover` of its book each session. Several crowd participants on one
//!   signal with different limits unwind one after another: the first
//!   one's selling moves prices against the rest. That is a crowded exit,
//!   the mechanism behind the quant unwind of August 2007.
//!
//! # How it trades
//!
//! Each participant decides on the ticks where the session's tick count,
//! modulo `interval`, equals its position in the population modulo
//! `interval`, so participants are staggered. It trades only when its gap
//! to target is wider than `band * size * V`, and then at most `rate * V`
//! shares, rounded toward zero to whole shares. Its order meets the
//! agent-facing book as an agent's market order does: it takes the maker's
//! and the latent levels, moves the maker's inventory, can fill an agent's
//! resting order, and its taker flow reaches the market on the tick it is
//! sent, with the agents' own. Its label is `population:<name>`.
//!
//! The population acts at the start of each open tick, before the market
//! moves, so an agent that sends an order between ticks is always ahead of
//! the population on that tick, and the population is ahead of the agent's
//! next decision. Its fills are kept on its own ledger, not in the fills
//! `take_fills` returns, and it writes no impact rows. An agent's resting
//! order it fills is the agent's fill, with the participant as
//! counterparty.
//!
//! # Determinism
//!
//! It takes no random draw. Every decision is arithmetic on engine state
//! that the snapshot carries (`population` in `state_snapshot`, its ledger
//! and memories, a crowd's prices and loss-limit state, required exactly
//! when the engine holds a population) and
//! that both state hashes cover. The same seed, universe, model and
//! population give the same market, bit for bit, and a restored engine
//! continues as the original does. An engine built without a population
//! never calls into this module.
//!
//! # What it is not
//!
//! It is not a matching engine with latency or queue priority: every
//! participant acts at the same instant at the start of a tick. It posts no
//! resting orders, so it supplies liquidity only by trading against moves,
//! and it pays the spread to do it. Its participants observe the agents'
//! taker flow directly rather than inferring it from prints.
//!
//! # Two modes, two questions
//!
//! Isolated evaluation, with no population, is the default and is how
//! `rank` compares strategies: every agent faces the same market to the
//! bit. With a population, the market an agent meets depends on what the
//! agent did, because the population reacts to it. A populated result is
//! reproducible, but two strategies in it no longer face identical markets,
//! so populated mode answers "does this edge survive contact", not "which
//! strategy is better".

use crate::agent_book::{daily_sigma, daily_volume};
use crate::market::TickCompany;
use crate::mathx;
use crate::order_book::Side;

/// Every participant's book label starts with this; an agent may not use it
/// on an engine that holds a population.
pub const LABEL_PREFIX: &str = "population:";

/// The session's length in ticks: the detector's buckets cover it, and the
/// liquidity policy's horizon is scaled by it.
pub const SESSION_TICKS: u32 = 390;

/// The longest lookback a trend or reversion participant may take, in
/// sessions.
pub const MAX_LOOKBACK: u32 = 60;

/// Layout version of [`PopulationRun::to_flat`].
const FLAT_VERSION: f64 = 1.0;

/// One participant's policy and its parameters.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub enum Policy {
    Trend {
        lookback: u32,
        scale: f64,
    },
    Reversion {
        lookback: u32,
        scale: f64,
    },
    Liquidity {
        half_life: f64,
        scale: f64,
        vix_calm: f64,
        vix_stress: f64,
    },
    Detector {
        memory: f64,
        bucket: u32,
        lead: u32,
        hold: u32,
        max_spread: f64,
    },
    Crowd {
        /// True for momentum (long the winners), false for the reversal
        /// (long the losers).
        momentum: bool,
        /// Open ticks between the two prices the signal compares.
        lookback: u32,
        /// The tick of each interval at which it decides: it decides where
        /// the session's tick count, modulo the interval, equals this.
        offset: u32,
        /// Names held long and short.
        top_k: u32,
        /// Extra ranks a held name may slip before it leaves the book: it
        /// enters within the first `top_k` and leaves past `top_k + buffer`.
        buffer: u32,
        /// Loss limit: the fall of its book's price P&L from its best, as a
        /// share of its full book's gross, at which it sells out. 0 is no
        /// limit.
        stop: f64,
        /// Share of its full book it takes back each session after a stop.
        recover: f64,
    },
}

impl Policy {
    pub fn kind(&self) -> &'static str {
        match self {
            Policy::Trend { .. } => "trend",
            Policy::Reversion { .. } => "reversion",
            Policy::Liquidity { .. } => "liquidity",
            Policy::Detector { .. } => "detector",
            Policy::Crowd { .. } => "crowd",
        }
    }
}

/// One participant: its label, its sizing and its policy.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct Participant {
    /// Unique within the population; the book label is
    /// `population:<name>`.
    pub name: String,
    /// Largest position per name, as a share of the name's daily volume.
    pub size: f64,
    /// Largest trade per decision, as a share of daily volume.
    pub rate: f64,
    /// Ticks between decisions.
    pub interval: u32,
    /// The gap to target, as a share of `size`, below which it does not trade.
    pub band: f64,
    pub policy: Policy,
}

impl Participant {
    /// A participant with every field given: its name, its sizing and its
    /// policy, as the fields above describe them. The engine checks it when
    /// a population is set.
    pub fn new(
        name: impl Into<String>,
        size: f64,
        rate: f64,
        interval: u32,
        band: f64,
        policy: Policy,
    ) -> Self {
        Participant { name: name.into(), size, rate, interval, band, policy }
    }

    pub fn label(&self) -> String {
        format!("{LABEL_PREFIX}{}", self.name)
    }

    fn check(&self) -> Result<(), String> {
        let finite_pos = |v: f64| v.is_finite() && v > 0.0;
        if self.name.is_empty() || self.name.chars().any(|c| c.is_whitespace()) {
            return Err(format!(
                "a participant's name is non-empty with no spaces, got {:?}",
                self.name
            ));
        }
        if !finite_pos(self.size) || !finite_pos(self.rate) {
            return Err(format!(
                "{}: size and rate must be finite and above zero",
                self.name
            ));
        }
        if self.interval == 0 || self.interval > SESSION_TICKS {
            return Err(format!(
                "{}: interval must be 1 to {SESSION_TICKS} ticks",
                self.name
            ));
        }
        if !(self.band.is_finite() && (0.0..1.0).contains(&self.band)) {
            return Err(format!("{}: band must be in [0, 1)", self.name));
        }
        match &self.policy {
            Policy::Trend { lookback, scale } | Policy::Reversion { lookback, scale } => {
                if *lookback == 0 || *lookback > MAX_LOOKBACK {
                    return Err(format!(
                        "{}: lookback must be 1 to {MAX_LOOKBACK} sessions",
                        self.name
                    ));
                }
                if !finite_pos(*scale) {
                    return Err(format!(
                        "{}: scale must be finite and above zero",
                        self.name
                    ));
                }
            }
            Policy::Liquidity {
                half_life,
                scale,
                vix_calm,
                vix_stress,
            } => {
                if !finite_pos(*half_life) || !finite_pos(*scale) {
                    return Err(format!(
                        "{}: half_life and scale must be finite and above zero",
                        self.name
                    ));
                }
                if !(vix_calm.is_finite() && vix_stress.is_finite() && vix_calm < vix_stress) {
                    return Err(format!("{}: vix_calm must be below vix_stress", self.name));
                }
            }
            Policy::Detector {
                memory,
                bucket,
                lead,
                hold,
                max_spread,
            } => {
                if !finite_pos(*max_spread) {
                    return Err(format!(
                        "{}: max_spread must be finite and above zero",
                        self.name
                    ));
                }
                if !finite_pos(*memory) {
                    return Err(format!(
                        "{}: memory must be finite and above zero",
                        self.name
                    ));
                }
                if *bucket == 0 || *bucket > SESSION_TICKS {
                    return Err(format!(
                        "{}: bucket must be 1 to {SESSION_TICKS} ticks",
                        self.name
                    ));
                }
                if lead + hold >= SESSION_TICKS - bucket {
                    return Err(format!(
                        "{}: lead and hold together must be shorter than a session less one bucket",
                        self.name
                    ));
                }
            }
            Policy::Crowd {
                lookback,
                top_k,
                offset,
                stop,
                recover,
                ..
            } => {
                if SESSION_TICKS % self.interval != 0 {
                    return Err(format!(
                        "{}: a crowd's interval must divide the session's {SESSION_TICKS} ticks",
                        self.name
                    ));
                }
                if *lookback == 0
                    || lookback % self.interval != 0
                    || *lookback > MAX_LOOKBACK * SESSION_TICKS
                {
                    return Err(format!(
                        "{}: a crowd's lookback is a whole number of intervals, at most \
                         {MAX_LOOKBACK} sessions of {SESSION_TICKS} ticks",
                        self.name
                    ));
                }
                if *top_k == 0 {
                    return Err(format!("{}: top_k must be at least 1", self.name));
                }
                if *offset >= self.interval {
                    return Err(format!("{}: offset must be below the interval", self.name));
                }
                if !(stop.is_finite() && *stop >= 0.0) {
                    return Err(format!(
                        "{}: stop must be finite and not negative",
                        self.name
                    ));
                }
                if !(recover.is_finite() && *recover > 0.0 && *recover <= 1.0) {
                    return Err(format!(
                        "{}: recover must be above 0 and at most 1",
                        self.name
                    ));
                }
            }
        }
        Ok(())
    }

    fn buckets(&self) -> usize {
        match self.policy {
            Policy::Detector { bucket, .. } => SESSION_TICKS.div_ceil(bucket) as usize,
            _ => 0,
        }
    }

    /// A crowd's samples per name: its price at each of its decisions,
    /// enough to reach `lookback` ticks back. 0 for the other kinds.
    fn samples(&self) -> usize {
        match self.policy {
            Policy::Crowd { lookback, .. } => (lookback / self.interval) as usize + 1,
            _ => 0,
        }
    }
}

/// The scalars a crowd carries: the share of its full book it holds, its
/// best price P&L since its last stop, how many times it has stopped out,
/// and its price P&L (what its positions made between its decisions, before
/// what it paid to trade).
const CROWD_SCALARS: usize = 4;

/// One participant's ledger and memory, per name in roster order.
#[derive(Debug, Clone, PartialEq, Default)]
#[non_exhaustive]
pub struct ParticipantState {
    /// Shares held.
    pub position: Vec<f64>,
    /// Cash from its trades on the name: minus what it paid, plus what it
    /// received.
    pub cash: Vec<f64>,
    /// Shares traded, both sides.
    pub volume: Vec<f64>,
    /// Dollars traded, both sides.
    pub notional: Vec<f64>,
    /// Orders sent.
    pub orders: f64,
    /// Liquidity: the moving average of the log price. Empty otherwise.
    pub average: Vec<f64>,
    /// Detector: per name, empty until the agents' flow first reaches the
    /// name, then `3 * buckets` values: the flow's mean per bucket, its
    /// variance, and today's flow so far. Empty for the other kinds. Kept
    /// per name so a run in which agents trade a few names carries a few
    /// profiles, not one per name.
    pub profile: Vec<Vec<f64>>,
    /// Crowd: per name, its price at each of its last decisions, oldest
    /// first, NaN where not yet seen. Empty for the other kinds.
    pub samples: Vec<f64>,
    /// Crowd: `[exposure, peak, stops]` (see `CROWD_SCALARS`). Empty for
    /// the other kinds.
    pub crowd: Vec<f64>,
}

/// A population running inside one engine: the spec and its state.
#[derive(Debug, Clone, PartialEq, Default)]
#[non_exhaustive]
pub struct PopulationRun {
    /// The digest of the spec, as the caller computed it; it travels with
    /// the snapshot, so a snapshot restores only into an engine built with
    /// the same population.
    pub fingerprint: String,
    pub participants: Vec<Participant>,
    /// The roster the per-name arrays follow.
    pub tickers: Vec<String>,
    /// The day label of the last session it saw; `None` before the first.
    pub day: Option<i64>,
    /// The price after the last open tick, per name.
    pub last: Vec<f64>,
    /// The last `depth` closes per name, oldest first, `depth` slots each,
    /// NaN where not yet seen.
    pub closes: Vec<f64>,
    pub depth: usize,
    pub states: Vec<ParticipantState>,
    /// Per participant, the participant whose detector profile it reads:
    /// detectors with the same `memory` and `bucket` see the same flow and so
    /// keep the same profile, which the first of them holds. Its own index
    /// for everyone else. Read from the spec, not state.
    pub owners: Vec<usize>,
    /// With more than one detector: per name, the quoted spread of the
    /// agent-facing book in the name's daily sigmas and the five-tick window
    /// it was read in, so detectors deciding in the same window share one
    /// reading. Empty otherwise (one detector reads the book itself).
    pub gates: Vec<f64>,
}

/// Ticks in a window over which several detectors share one spread reading.
pub const GATE_WINDOW: u32 = 5;

/// Per participant, the first detector with the same `memory` and `bucket`
/// (itself when it is the first, and for every other kind).
fn profile_owners(participants: &[Participant]) -> Vec<usize> {
    let key = |p: &Participant| match p.policy {
        Policy::Detector { memory, bucket, .. } => Some((memory.to_bits(), bucket)),
        _ => None,
    };
    (0..participants.len())
        .map(|k| match key(&participants[k]) {
            Some(own) => (0..k).find(|&j| key(&participants[j]) == Some(own)).unwrap_or(k),
            None => k,
        })
        .collect()
}

/// One order the population sends this tick: participant, roster slot,
/// signed shares.
pub type Order = (usize, usize, f64);

impl PopulationRun {
    pub fn new(
        fingerprint: String,
        participants: Vec<Participant>,
        companies: &[TickCompany],
    ) -> Result<Self, String> {
        if participants.is_empty() {
            return Err("a population needs at least one participant".to_string());
        }
        for (k, p) in participants.iter().enumerate() {
            p.check()?;
            if participants[..k].iter().any(|q| q.name == p.name) {
                return Err(format!("two participants are named {:?}", p.name));
            }
        }
        let depth = participants
            .iter()
            .map(|p| match p.policy {
                Policy::Trend { lookback, .. } | Policy::Reversion { lookback, .. } => {
                    lookback as usize
                }
                _ => 0,
            })
            .max()
            .unwrap_or(0);
        let mut run = PopulationRun {
            fingerprint,
            participants,
            tickers: Vec::new(),
            day: None,
            last: Vec::new(),
            closes: Vec::new(),
            depth,
            states: Vec::new(),
            owners: Vec::new(),
            gates: Vec::new(),
        };
        run.owners = profile_owners(&run.participants);
        run.states = vec![ParticipantState::default(); run.participants.len()];
        for (k, p) in run.participants.iter().enumerate() {
            if matches!(p.policy, Policy::Crowd { .. }) {
                run.states[k].crowd = vec![1.0, 0.0, 0.0, 0.0];
            }
        }
        run.align(companies);
        Ok(run)
    }

    /// Bring every per-name array to the roster, by ticker: a name that
    /// stays keeps its state, a new one starts empty, a gone one is
    /// dropped. A no-op while the roster is unchanged.
    pub fn align(&mut self, companies: &[TickCompany]) {
        if self.tickers.len() == companies.len()
            && self
                .tickers
                .iter()
                .zip(companies)
                .all(|(t, c)| *t == c.ticker)
        {
            return;
        }
        let old: Vec<String> = std::mem::take(&mut self.tickers);
        let n = companies.len();
        let from: Vec<Option<usize>> = companies
            .iter()
            .map(|c| old.iter().position(|t| *t == c.ticker))
            .collect();
        let pick = |v: &[f64], width: usize, empty: f64| -> Vec<f64> {
            let mut out = vec![empty; n * width];
            for (i, f) in from.iter().enumerate() {
                if let Some(j) = f {
                    if (j + 1) * width <= v.len() {
                        out[i * width..(i + 1) * width]
                            .copy_from_slice(&v[j * width..(j + 1) * width]);
                    }
                }
            }
            out
        };
        let last: Vec<f64> = companies
            .iter()
            .enumerate()
            .map(|(i, c)| match from[i] {
                Some(j) if j < self.last.len() => self.last[j],
                _ => c.stock.price,
            })
            .collect();
        self.closes = pick(&self.closes, self.depth, f64::NAN);
        self.gates = if self.shares_gates() {
            pick(&self.gates, 2, f64::NAN)
        } else {
            Vec::new()
        };

        self.last = last;
        for (k, p) in self.participants.iter().enumerate() {
            let s = &mut self.states[k];
            s.position = pick(&s.position, 1, 0.0);
            s.cash = pick(&s.cash, 1, 0.0);
            s.volume = pick(&s.volume, 1, 0.0);
            s.notional = pick(&s.notional, 1, 0.0);
            match p.policy {
                Policy::Liquidity { .. } => {
                    let avg = pick(&s.average, 1, f64::NAN);
                    s.average = avg;
                }
                Policy::Crowd { .. } => {
                    let width = p.samples();
                    let samples = pick(&s.samples, width, f64::NAN);
                    s.samples = samples;
                }
                Policy::Detector { .. } if self.owners[k] != k => {
                    s.profile = Vec::new();
                }
                Policy::Detector { .. } => {
                    s.profile = from
                        .iter()
                        .map(|f| {
                            f.and_then(|j| s.profile.get(j).cloned())
                                .unwrap_or_default()
                        })
                        .collect();
                }
                _ => {}
            }
        }
        self.tickers = companies.iter().map(|c| c.ticker.clone()).collect();
    }

    /// The close `lag` sessions back for one name, or NaN.
    fn close(&self, index: usize, lag: usize) -> f64 {
        if lag == 0 || lag > self.depth {
            return f64::NAN;
        }
        self.closes[index * self.depth + self.depth - lag]
    }

    /// A new session: yesterday's last price becomes a close, and the
    /// detector folds yesterday's flow into its memory.
    pub fn roll_day(&mut self, day: i64) {
        let d = self.depth;
        if d > 0 {
            for i in 0..self.last.len() {
                let row = &mut self.closes[i * d..(i + 1) * d];
                row.rotate_left(1);
                row[d - 1] = self.last[i];
            }
        }
        for (k, p) in self.participants.iter().enumerate() {
            if let Policy::Crowd { recover, .. } = p.policy {
                let g = &mut self.states[k].crowd[0];
                if *g < 1.0 {
                    *g = mathx::min(1.0, *g + recover);
                }
            }
            if let Policy::Detector { memory, .. } = p.policy {
                if self.owners[k] != k {
                    continue;
                }
                let alpha = 1.0 - mathx::pow(0.5, 1.0 / memory);
                let b = p.buckets();
                for row in self.states[k].profile.iter_mut().filter(|r| !r.is_empty()) {
                    for slot in 0..b {
                        let (m, v, x) = (row[slot], row[b + slot], row[2 * b + slot]);
                        let d = x - m;
                        row[slot] = m + alpha * d;
                        row[b + slot] = (1.0 - alpha) * (v + alpha * d * d);
                        row[2 * b + slot] = 0.0;
                    }
                }
            }
        }
        self.day = Some(day);
    }

    /// The agents' net taker flow on one name reaching this tick, signed
    /// shares; every detector books it, as a share of the name's volume.
    pub fn observe_flow(&mut self, index: usize, tick: u32, net: f64, volume: f64) {
        if net == 0.0 || !(volume > 0.0) {
            return;
        }
        for (k, p) in self.participants.iter().enumerate() {
            if let Policy::Detector { bucket, .. } = p.policy {
                if self.owners[k] != k {
                    continue;
                }
                let b = p.buckets();
                let slot = std::cmp::min((tick / bucket) as usize, b - 1);
                if let Some(row) = self.states[k].profile.get_mut(index) {
                    if row.is_empty() {
                        *row = vec![0.0; 3 * b];
                    }
                    row[2 * b + slot] += net / volume;
                }
            }
        }
    }

    /// After an open tick: the prices it left, and the liquidity
    /// participants' moving averages.
    pub fn after_tick(&mut self, companies: &[TickCompany]) {
        for (i, c) in companies.iter().enumerate() {
            if let Some(slot) = self.last.get_mut(i) {
                *slot = c.stock.price;
            }
        }
        for (k, p) in self.participants.iter().enumerate() {
            if let Policy::Liquidity { half_life, .. } = p.policy {
                let a = 1.0 - mathx::pow(0.5, 1.0 / half_life);
                let s = &mut self.states[k];
                for (i, c) in companies.iter().enumerate() {
                    let price = c.stock.price;
                    if !(price > 0.0) {
                        continue;
                    }
                    let x = mathx::log(price);
                    if let Some(avg) = s.average.get_mut(i) {
                        *avg = if avg.is_nan() {
                            x
                        } else {
                            *avg + a * (x - *avg)
                        };
                    }
                }
            }
        }
    }

    /// Whether the population holds more than one detector, so that they
    /// share spread readings (`gates`).
    pub fn shares_gates(&self) -> bool {
        self.participants
            .iter()
            .filter(|p| matches!(p.policy, Policy::Detector { .. }))
            .count()
            > 1
    }

    /// Whether participant `k` decides at this tick of the session.
    fn turn(&self, k: usize, tick: u32) -> bool {
        let p = &self.participants[k];
        match p.policy {
            Policy::Crowd { offset, .. } => tick % p.interval == offset,
            _ => tick % p.interval == (k as u32) % p.interval,
        }
    }

    /// A crowd's book: +1 for a name it holds long, -1 short, 0 otherwise,
    /// ranked exactly as a ranked trend rule ranks (the simple return over
    /// the lookback, ties broken on the ticker). A name enters a side within
    /// its first `top_k` ranks and, once held there, stays until it slips
    /// past `top_k + buffer`. `None` until some name has a price that far
    /// back.
    fn crowd_book(&self, k: usize, companies: &[TickCompany]) -> Option<Vec<f64>> {
        let p = &self.participants[k];
        let Policy::Crowd {
            momentum,
            top_k,
            buffer,
            ..
        } = p.policy
        else {
            return None;
        };
        let width = p.samples();
        let rows = &self.states[k].samples;
        let held = &self.states[k].position;
        let mut ranked: Vec<(f64, usize)> = Vec::new();
        for (i, c) in companies.iter().enumerate() {
            if c.is_bankrupt || !c.is_public || !(c.stock.price > 0.0) {
                continue;
            }
            let Some(row) = rows.get(i * width..(i + 1) * width) else {
                continue;
            };
            let (past, now) = (row[0], row[width - 1]);
            if !(past > 0.0) || !(now > 0.0) {
                continue;
            }
            let r = now / past - 1.0;
            ranked.push((if momentum { -r } else { r }, i));
        }
        let n = ranked.len();
        let kk = std::cmp::min(top_k as usize, n / 2);
        if kk == 0 {
            return None;
        }
        let keep = std::cmp::min(kk + buffer as usize, n / 2);
        ranked.sort_by(|a, b| {
            a.0.partial_cmp(&b.0)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| companies[a.1].ticker.cmp(&companies[b.1].ticker))
        });
        let mut book = vec![0.0; companies.len()];
        for (rank, (_, i)) in ranked.iter().enumerate() {
            let q = held.get(*i).copied().unwrap_or(0.0);
            if rank < kk || (rank < keep && q > 0.0) {
                book[*i] = 1.0;
            } else if rank >= n - kk || (rank >= n - keep && q < 0.0) {
                book[*i] = -1.0;
            }
        }
        Some(book)
    }

    /// Before the participants decide at this tick: each crowd whose turn it
    /// is records its prices, and one with a loss limit marks its book to
    /// the market and sells out when it has lost `stop` of its full book's
    /// gross from its best.
    pub fn prepare(&mut self, tick: u32, companies: &[TickCompany]) {
        for k in 0..self.participants.len() {
            let Policy::Crowd { stop, .. } = self.participants[k].policy else {
                continue;
            };
            if !self.turn(k, tick) {
                continue;
            }
            let width = self.participants[k].samples();
            {
                let s = &mut self.states[k];
                let mut made = 0.0;
                for (i, c) in companies.iter().enumerate() {
                    if let Some(row) = s.samples.get_mut(i * width..(i + 1) * width) {
                        row.rotate_left(1);
                        let price = c.stock.price;
                        row[width - 1] = if price > 0.0 { price } else { f64::NAN };
                        let q = s.position.get(i).copied().unwrap_or(0.0);
                        let (was, now) = (row[width - 2], row[width - 1]);
                        if q != 0.0 && was > 0.0 && now > 0.0 {
                            made += q * (now - was);
                        }
                    }
                }
                s.crowd[3] += made;
            }
            if stop <= 0.0 {
                continue;
            }
            let Some(book) = self.crowd_book(k, companies) else {
                continue;
            };
            let size = self.participants[k].size;
            let mut gross = 0.0;
            for (i, c) in companies.iter().enumerate() {
                if book[i] != 0.0 {
                    gross += size * daily_volume(c) * c.stock.price;
                }
            }
            if !(gross > 0.0) {
                continue;
            }
            let s = &mut self.states[k];
            let pnl = s.crowd[3];
            if pnl > s.crowd[1] {
                s.crowd[1] = pnl;
            }
            if s.crowd[0] > 0.0 && s.crowd[1] - pnl > stop * gross {
                s.crowd[0] = 0.0;
                s.crowd[1] = pnl;
                s.crowd[2] += 1.0;
            }
        }
    }

    /// The detector's predicted flow over its window, as a share of
    /// volume, at this tick.
    fn predicted(&self, k: usize, index: usize, tick: u32) -> f64 {
        let p = &self.participants[k];
        let Policy::Detector {
            bucket, lead, hold, ..
        } = p.policy
        else {
            return 0.0;
        };
        let b = p.buckets();
        let row = match self.states[self.owners[k]].profile.get(index) {
            Some(r) if !r.is_empty() => r,
            _ => return 0.0,
        };
        let (row_m, row_v) = (&row[..b], &row[b..2 * b]);
        // The window wraps across the night: near the close it reaches into
        // the next session's first buckets, and at the open back into the
        // last session's, because the memory is one profile of a session.
        let first = (tick as i64 - hold as i64).div_euclid(bucket as i64);
        let last = (tick as i64 + lead as i64).div_euclid(bucket as i64);
        let mut total = 0.0;
        for at in first..=last {
            let slot = at.rem_euclid(b as i64) as usize;
            let (m, v) = (row_m[slot], row_v[slot]);
            let m2 = m * m;
            if m2 + v > 0.0 {
                let r = m2 / (m2 + v);
                total += m * r * r;
            }
        }
        total
    }

    /// The orders every participant whose turn it is sends at this tick, in
    /// participant order and roster order.
    pub fn decide(
        &self,
        tick: u32,
        companies: &[TickCompany],
        market_sigma_daily: f64,
        vix: f64,
    ) -> Vec<Order> {
        let mut orders = Vec::new();
        for (k, p) in self.participants.iter().enumerate() {
            if !self.turn(k, tick) {
                continue;
            }
            let s = &self.states[k];
            let book = if matches!(p.policy, Policy::Crowd { .. }) {
                self.crowd_book(k, companies)
            } else {
                None
            };
            for (i, c) in companies.iter().enumerate() {
                if c.is_bankrupt || !c.is_public {
                    continue;
                }
                let price = c.stock.price;
                let volume = daily_volume(c);
                let sigma = daily_sigma(c, market_sigma_daily);
                if !(price > 0.0) || !(volume > 0.0) || !(sigma > 0.0) {
                    continue;
                }
                let cap = p.size * volume;
                let target = match p.policy {
                    Policy::Trend { lookback, scale } | Policy::Reversion { lookback, scale } => {
                        let past = self.close(i, lookback as usize);
                        if !(past > 0.0) {
                            continue;
                        }
                        let m = mathx::log(price / past) / (sigma * mathx::sqrt(lookback as f64));
                        let sign = if matches!(p.policy, Policy::Trend { .. }) {
                            1.0
                        } else {
                            -1.0
                        };
                        sign * cap * mathx::clamp(m / scale, -1.0, 1.0)
                    }
                    Policy::Liquidity {
                        half_life,
                        scale,
                        vix_calm,
                        vix_stress,
                    } => {
                        let avg = s.average.get(i).copied().unwrap_or(f64::NAN);
                        if avg.is_nan() {
                            continue;
                        }
                        let w =
                            mathx::clamp((vix_stress - vix) / (vix_stress - vix_calm), 0.0, 1.0);
                        let z = (mathx::log(price) - avg)
                            / (sigma * mathx::sqrt(half_life / SESSION_TICKS as f64));
                        -cap * w * mathx::clamp(z / scale, -1.0, 1.0)
                    }
                    Policy::Crowd { .. } => {
                        let side = book.as_ref().map(|b| b[i]).unwrap_or(0.0);
                        if side == 0.0 && s.position[i] == 0.0 {
                            continue;
                        }
                        side * s.crowd[0] * cap
                    }
                    Policy::Detector { .. } => {
                        let pred = self.predicted(k, i, tick);
                        if pred == 0.0 && s.position[i] == 0.0 {
                            continue;
                        }
                        mathx::clamp(pred * volume, -cap, cap)
                    }
                };
                let gap = target - s.position[i];
                if gap.abs() <= p.band * cap {
                    continue;
                }
                let step = mathx::clamp(gap, -p.rate * volume, p.rate * volume).trunc();
                if step != 0.0 {
                    orders.push((k, i, step));
                }
            }
        }
        orders
    }

    /// The widest quoted spread, in the name's daily sigmas, at which a
    /// participant adds to a position: the detector's `max_spread`, and no
    /// limit for the other kinds.
    pub fn max_spread(&self, k: usize) -> Option<f64> {
        match self.participants[k].policy {
            Policy::Detector { max_spread, .. } => Some(max_spread),
            _ => None,
        }
    }

    /// Whether a signed order adds to the participant's position on a name
    /// (rather than reducing it).
    pub fn adds(&self, k: usize, index: usize, signed: f64) -> bool {
        let held = self.states[k].position[index];
        held == 0.0 || (held > 0.0) == (signed > 0.0)
    }

    /// Book one fill on a participant's ledger.
    pub fn book_fill(&mut self, k: usize, index: usize, side: Side, quantity: f64, price: f64) {
        let s = &mut self.states[k];
        let signed = match side {
            Side::Buy => quantity,
            Side::Sell => -quantity,
        };
        s.position[index] += signed;
        s.cash[index] -= signed * price;
        s.volume[index] += quantity;
        s.notional[index] += quantity * price;
    }

    /// Count one order sent.
    pub fn book_order(&mut self, k: usize) {
        self.states[k].orders += 1.0;
    }

    /// Every number the state holds, in one fixed order: the snapshot's
    /// `population.state` and what both state hashes cover.
    pub fn to_flat(&self) -> Vec<f64> {
        let mut out = vec![
            FLAT_VERSION,
            self.tickers.len() as f64,
            self.depth as f64,
            if self.day.is_some() { 1.0 } else { 0.0 },
            self.day.unwrap_or(0) as f64,
        ];
        out.extend_from_slice(&self.last);
        out.extend_from_slice(&self.closes);
        if self.shares_gates() {
            out.extend_from_slice(&self.gates);
        }
        for (k, s) in self.states.iter().enumerate() {
            out.push(s.orders);
            for v in [&s.position, &s.cash, &s.volume, &s.notional, &s.average] {
                out.push(v.len() as f64);
                out.extend_from_slice(v);
            }
            out.push(s.profile.len() as f64);
            for row in &s.profile {
                out.push(row.len() as f64);
                out.extend_from_slice(row);
            }
            // A crowd's prices and scalars; nothing for the other kinds, so
            // a population without a crowd keeps the layout it had.
            if matches!(self.participants[k].policy, Policy::Crowd { .. }) {
                out.push(s.samples.len() as f64);
                out.extend_from_slice(&s.samples);
                out.extend_from_slice(&s.crowd);
            }
        }
        out
    }

    /// Install a state [`PopulationRun::to_flat`] wrote, for this spec and
    /// roster.
    pub fn set_flat(&mut self, tickers: Vec<String>, flat: &[f64]) -> Result<(), String> {
        let bad = |what: &str| format!("snapshot field population.state is malformed: {what}");
        let mut at = 0usize;
        let mut take = |count: usize| -> Result<&[f64], String> {
            let end = at
                .checked_add(count)
                .filter(|e| *e <= flat.len())
                .ok_or_else(|| bad("too short"))?;
            let out = &flat[at..end];
            at = end;
            Ok(out)
        };
        let head = take(5)?.to_vec();
        if head[0] != FLAT_VERSION {
            return Err(bad("unknown layout version"));
        }
        let n = head[1] as usize;
        if head[1] != n as f64 || n != tickers.len() {
            return Err(bad("the name count does not match the tickers"));
        }
        if head[2] != self.depth as f64 {
            return Err(bad("the close depth does not match this population"));
        }
        let day = if head[3] == 1.0 {
            Some(head[4] as i64)
        } else {
            None
        };
        let last = take(n)?.to_vec();
        let closes = take(n * self.depth)?.to_vec();
        let gates = if self.shares_gates() {
            take(2 * n)?.to_vec()
        } else {
            Vec::new()
        };

        let mut states = Vec::with_capacity(self.participants.len());
        for (k, p) in self.participants.iter().enumerate() {
            let mut s = ParticipantState {
                orders: take(1)?[0],
                ..Default::default()
            };
            let widths = [
                n,
                n,
                n,
                n,
                if matches!(p.policy, Policy::Liquidity { .. }) {
                    n
                } else {
                    0
                },
            ];
            let mut parts: Vec<Vec<f64>> = Vec::with_capacity(5);
            for w in widths {
                let len = take(1)?[0];
                if len != w as f64 {
                    return Err(bad(
                        "an array's length does not match this population and roster",
                    ));
                }
                parts.push(take(w)?.to_vec());
            }
            let mut it = parts.into_iter();
            s.position = it.next().unwrap_or_default();
            s.cash = it.next().unwrap_or_default();
            s.volume = it.next().unwrap_or_default();
            s.notional = it.next().unwrap_or_default();
            s.average = it.next().unwrap_or_default();
            let rows = take(1)?[0];
            let detector = matches!(p.policy, Policy::Detector { .. }) && self.owners[k] == k;
            if rows != if detector { n as f64 } else { 0.0 } {
                return Err(bad(
                    "the detector's profiles do not match this population and roster",
                ));
            }
            for _ in 0..rows as usize {
                let len = take(1)?[0];
                if len != 0.0 && len != (3 * p.buckets()) as f64 {
                    return Err(bad("a detector profile has the wrong length"));
                }
                s.profile.push(take(len as usize)?.to_vec());
            }
            if matches!(p.policy, Policy::Crowd { .. }) {
                let len = take(1)?[0];
                if len != (n * p.samples()) as f64 {
                    return Err(bad(
                        "a crowd's prices do not match this population and roster",
                    ));
                }
                s.samples = take(len as usize)?.to_vec();
                s.crowd = take(CROWD_SCALARS)?.to_vec();
            }
            states.push(s);
        }
        if at != flat.len() {
            return Err(bad("too long"));
        }
        self.tickers = tickers;
        self.day = day;
        self.last = last;
        self.closes = closes;
        self.gates = gates;
        self.states = states;
        Ok(())
    }

    /// Which participant a book label names, if any.
    pub fn participant_of(&self, label: &str) -> Option<usize> {
        let name = label.strip_prefix(LABEL_PREFIX)?;
        self.participants.iter().position(|p| p.name == name)
    }
}

/// Whether a book label is a population participant's.
pub fn is_population_label(label: &str) -> bool {
    label.starts_with(LABEL_PREFIX)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn detector() -> Participant {
        Participant {
            name: "d".into(),
            size: 0.02,
            rate: 0.002,
            interval: 1,
            band: 0.0,
            policy: Policy::Detector {
                memory: 1.0,
                bucket: 5,
                lead: 20,
                hold: 10,
                max_spread: 1.0,
            },
        }
    }

    fn run(p: Participant) -> PopulationRun {
        PopulationRun {
            fingerprint: "x".into(),
            depth: 0,
            states: vec![ParticipantState::default()],
            participants: vec![p],
            tickers: Vec::new(),
            day: None,
            last: Vec::new(),
            closes: Vec::new(),
            owners: vec![0],
            gates: Vec::new(),
        }
    }

    fn with_names(mut r: PopulationRun, n: usize) -> PopulationRun {
        r.tickers = (0..n).map(|i| format!("N{i}")).collect();
        r.last = vec![10.0; n];
        let b = r.participants[0].buckets();
        let s = &mut r.states[0];
        s.position = vec![0.0; n];
        s.cash = vec![0.0; n];
        s.volume = vec![0.0; n];
        s.notional = vec![0.0; n];
        let _ = b;
        s.profile = vec![Vec::new(); n];
        r
    }

    #[test]
    fn the_detector_trusts_flow_that_recurs_and_not_flow_that_does_not() {
        // The same slice at the same bucket every day: the reliability
        // climbs toward one. A slice that lands in that bucket every other
        // day: the mean halves and the variance stays, so it stays low.
        let mut steady = with_names(run(detector()), 1);
        let mut erratic = with_names(run(detector()), 1);
        for day in 0..8 {
            steady.observe_flow(0, 50, 100.0, 10_000.0);
            if day % 2 == 0 {
                erratic.observe_flow(0, 50, 200.0, 10_000.0);
            }
            steady.roll_day(day + 1);
            erratic.roll_day(day + 1);
        }
        let a = steady.predicted(0, 0, 45);
        let b = erratic.predicted(0, 0, 45);
        assert!(a > 0.0095 && a < 0.0101, "steady prediction {a}");
        assert!(b < 0.5 * a, "erratic {b} against steady {a}");
    }

    #[test]
    fn the_flat_state_round_trips() {
        let mut r = with_names(run(detector()), 3);
        r.observe_flow(1, 7, 50.0, 1000.0);
        r.roll_day(4);
        r.book_fill(0, 2, Side::Buy, 12.0, 9.5);
        let flat = r.to_flat();
        let mut s = r.clone();
        s.states[0].position[2] = 0.0;
        s.set_flat(r.tickers.clone(), &flat).unwrap();
        let bits = |v: Vec<f64>| v.into_iter().map(f64::to_bits).collect::<Vec<_>>();
        assert_eq!(bits(s.to_flat()), bits(r.to_flat()));
        assert!(s
            .set_flat(r.tickers.clone(), &flat[..flat.len() - 1])
            .is_err());
    }
}
