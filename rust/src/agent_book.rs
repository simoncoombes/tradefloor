//! The book an agent trades against: depth past the maker's ladder, depth
//! that agents consume and that refills, and agents' own resting orders.
//!
//! # What was missing
//!
//! Three things, found by two outside reviewers of 0.8.x.
//!
//! - **Size had no price.** The maker quotes ten levels, 2.6 to 5% of daily
//!   volume a side. `Portfolio.execute` filled what those held and dropped
//!   the rest, so an order past about 2% of daily volume per step met a
//!   silent cut-off rather than a worse price.
//! - **Agents did not take levels from each other.** `sweep_cost` reads the
//!   ladder and removes nothing, so two agents buying one name in one step
//!   filled at the same price against the same levels.
//! - **A limit order never entered the book.** The hosted service filled a
//!   resting limit in full at its limit whenever a later print reached it:
//!   no queue, no partial fill, and nothing another agent could hit.
//!
//! Each is fixed behind a dial in [`crate::params::ModelParams`] that ships
//! at 0.0 on every preset. None of them touches the path the model's own
//! flow settles through, so a market nobody trades is the same market to
//! the bit at any setting; see "What the market's own flow sees" below.
//!
//! # The book, in three layers
//!
//! 1. **The maker's ladder**, exactly as `microstructure::build_live_book`
//!    quotes it around the last print: ten levels, inventory skew and all.
//! 2. **Latent depth** (`book_depth_coefficient` off zero): a second pool
//!    beside the ladder, owned by [`DEPTH_OWNER`], out to `book_depth_reach`
//!    times the name's daily volume. Measured from the ladder's best price,
//!    its own `Q`-th share is priced at `touch * (1 + Y sigma (Q /
//!    V)^delta)` or a grid step worse, where `sigma` is [`daily_sigma`] and
//!    `V` [`daily_volume`]: the latent order book of Toth et al. (Physical
//!    Review X 1, 021006, 2011), whose depth grows linearly with distance
//!    from the price and gives the square-root law of impact. The levels sit
//!    on a geometric grid of cumulative size, each [`DEPTH_LEVEL_RATIO`]
//!    times the one before and priced at its LAST share. A walk meets
//!    whichever pool is cheaper at each price, so a large order pays the law
//!    and a small one pays the maker's spread.
//! 3. **Agents' resting orders** (`book_resting` on), inserted in arrival
//!    order by the ordinary price-time rule. The maker's and the latent
//!    levels are appended first, so an agent's order at a price the maker
//!    also quotes queues behind the maker's size there.
//!
//! # Consumption and refill (`book_shared`)
//!
//! What an agent's order takes is recorded per name and side as a volume,
//! [`BookState::taken`], and removed from the FRONT of that layer whenever
//! the book is built again: the first `taken` shares of the ladder, and the
//! first `taken` shares of the latent depth. Two speeds, because the two
//! layers are different liquidity:
//!
//! - The maker re-quotes every tick, so its ladder is whole again at the
//!   next tick. Until then, every agent after the first meets what the first
//!   left. Its fills against the maker move the maker's inventory, which it
//!   quotes on from the next tick, exactly as fills against the model's own
//!   flow do.
//! - The latent depth refills exponentially, at `book_refill_half_life`
//!   ticks. This is the volume-recovery model of Obizhaeva and Wang (Journal
//!   of Financial Markets 16(1), 2013) with a general book shape, which
//!   Alfonsi, Fruth and Schied (Quantitative Finance 10(2), 2010) solve and
//!   Alfonsi and Schied (SIAM Journal on Financial Mathematics 1, 2010) show
//!   admits no price-manipulation strategy. The round-trip tests in
//!   `tests/test_order_book_depth.py` are the check on this implementation;
//!   the citation is why they are expected to pass.
//!
//! The half-life is a dial because it is the one number the literature does
//! not pin to a value this model can use. The book is measured to recover
//! within minutes of a large trade (Biais, Hillion and Spatt, Journal of
//! Finance 50(5), 1995; Degryse, de Jong, van Ravenswaaij and Wuyts, Review
//! of Finance 9(2), 2005). The value suggested for a preset is derived from
//! the model's own volume clock instead: latent depth consumed at a tenth of
//! daily volume, replaced at the rate the name trades (`V / 390` a tick),
//! takes 39 ticks on average, which is an exponential half-life of
//! `39 ln 2`, 27 ticks. A large order's temporary impact is then 81% gone
//! by the next step at six steps a day and all but gone by the next day.
//!
//! # What the curve measures, against the literature
//!
//! `tools/calibration/impact_curve.py`, pt-v19 with the depth at `Y` 0.75,
//! `delta` 0.5 and a reach of one day's volume, on three rosters of forty
//! names, cost against the arrival mid over each name's volatility realised
//! across sixty sessions (results in `tools/calibration/results/`):
//!
//! - The average price of an immediate order from 1% to 100% of daily
//!   volume fits `0.45 sigma (Q/V)^0.495`, and the worst price reached
//!   `0.64 sigma (Q/V)^0.488`. The square-root law's peak impact is
//!   `Y sigma sqrt(Q/V)` with `Y` of order 0.5 to 1 (Toth et al. 2011), and
//!   the average cost of reaching it along a linear latent book is two
//!   thirds of that, 0.33 to 0.67. Both fits sit inside. `Y` 0.9 moves them
//!   to 0.53 and 0.77, the middle of each band.
//! - At one step of six a day, Almgren, Thum, Hauptmann and Li (Risk, 2005)
//!   predict a cost of `0.120 sigma` at 10% of daily volume and `0.249
//!   sigma` at 30% if their permanent term is read without its turnover
//!   factor; this book charges `0.142` and `0.249`. Below 3% the maker's
//!   spread sets the cost and the book is dearer than their estimate, which
//!   is for patient program trading. Their fitted law is `sigma [(gamma/2)
//!   (X/V) (Theta/V)^(1/4) + eta (X/(VT))^(3/5)]`, `Theta` the shares
//!   outstanding: the factor `(Theta/V)^(1/4)` is part of the fit. At the
//!   sim roster's median `Theta/V` of 81 (factor 3.0) or the real forty's
//!   223 (3.87; EDGAR shares over daily volume, 2019 and 2023) the two
//!   figures are `0.152` to `0.165` at 10% and `0.343` to `0.384` at 30%.
//! - Cost follows volatility: with the fear gauge held at 35 rather than 15
//!   realised volatility rises by a factor of 1.61 and the cost of 10% and
//!   30% of daily volume by 1.71 and 1.72 (spreads widen too).
//! - The temporary part decays at the refill: a second buy of 10% of daily
//!   volume pays 11.0 bp more than it would have one tick after the first,
//!   6.2 after thirty ticks, 3.0 after a step and 0.6 after two. The
//!   permanent part, 4.9 bp at `gamma` 0.314, stays.
//!
//! # Resting orders and the queue (`book_resting`)
//!
//! A resting order lives in [`BookState::orders`] and is inserted into every
//! book built for its name: the book another agent trades against between
//! ticks, and the book the model's flow settles through inside a tick. So
//! the model's flow fills it when a slice reaches its price and the depth
//! ahead of it is gone, partially when the slice is smaller than that, and
//! at its own price. The maker re-quotes each tick, so at an equal price the
//! maker's size is always ahead of an agent's order: an order at the touch
//! fills when the flow exhausts the maker's size there, or when the maker's
//! re-quote steps back behind it after a print through it, which is what a
//! queue behind a dealer means.
//!
//! The flow walks no further than the deepest price the maker quotes in the
//! settlement's ladder, which holds the levels the tick's volume needs
//! (`microstructure::settle_price_through_book_with_orders`). An order past
//! that waits for the price to come to it. Without the bound, a slice that
//! emptied the ladder went on into whatever rested behind it and filled it
//! at its own limit: a persona review of the 0.8.5 candidate had a bid at 4%
//! of the market fill thousands of shares and print its price on the tape.
//!
//! A resting order the maker's re-quote leaves crossed (a bid at the price
//! the maker now asks) trades against the re-quote before the tick's flow,
//! at the maker's price, and that is taking liquidity: it is taker flow and
//! pays its permanent impact on the next tick. The maker's inventory skew
//! then moves its quote away, so a standing bid at the ask takes a few
//! ticks of the maker's size and stops. Its fills say so: `liquidity`
//! "taker", counterparty the maker, at the maker's price. `docs/MODEL.md`
//! tells users, because a maker-rebate study would count them otherwise.
//!
//! Off, a limit's unfilled part waits outside the book and fills in full at
//! its limit on the first tick whose print reaches it: the traded-range
//! convention, [`RestMode::Range`].
//!
//! An agent never trades against its own resting orders. They are left out
//! of the book its own order meets, and when the tick posts every resting
//! order into its settlement book, an order that crosses passes over its own
//! agent's orders (`SubmitOptions::skip_own`) and matches the next one behind
//! them. Neither order is cancelled, so an agent's bid and offer at one price
//! both rest, each for the flow or another agent to fill. The 0.8.5
//! pre-release builds matched them with each other, a wash trade.
//!
//! # What the market's own flow sees
//!
//! The settlement book inside a tick is the maker's ladder, quoted fresh,
//! plus any resting agent orders. It never sees consumed depth, because the
//! maker has re-quoted by then and the flow's four slices never reach past
//! the ladder into the latent depth. So, with the metaorder memory off, an
//! agent's temporary impact never reaches the tape as a lifted print for
//! the market to re-centre on: only its permanent impact does, through `s`
//! (`fill_impact_coefficient`, or the imbalance law), and through the
//! maker's inventory. That is the line between temporary and permanent
//! impact this model draws without the memory, and it makes the tape's
//! impact linear in size and permanent within the day: on pt-v20 a
//! half-day order's peak displacement is `0.42 f^1.04` sigma and 99% of it
//! is still there at the close (`tools/calibration/metaorder_curve.py`).
//!
//! # The metaorder memory (`impact_memory_coefficient`)
//!
//! Off zero, each name keeps a signed memory of agents' net taker flow
//! against the house (the maker and the latent depth), in fractions of its
//! daily volume. A fill between two agents is left out: it consumed no
//! liquidity the house must refill, and the pair's cash nets to zero, so
//! counting the taker's side would let one agent rest an ask and another
//! lift it to walk the tape at no cost. Fills passed in from outside the
//! engine (`queue_external_fills`) are left out too; they have no price
//! the book set. The memory has a fast part `Mf` decaying at
//! `impact_memory_half_life` and a slow part `Ms` at
//! `impact_memory_slow_half_life`, combined as `M = (1 - w) Mf + w Ms`,
//! decaying on open ticks only, so it holds overnight. The name's `s`
//! carries `D(M) = sign(M) Y_M sigma h(|M|)`, `h(m) = m^delta` above the
//! crossover `m*` and `m m*^(delta - 1)` below it, booked through the
//! order-flow slot on each tick so that the part of `s` it owns is `D`
//! despite `s`'s own reversion ([`BookState::memory`]'s third column). The
//! print follows `s`, so the displacement is on the tape; the closing
//! cross prints it, and it carries overnight in `s`. The memory aggregates
//! every agent, so splitting an order across agent names does not escape
//! it. The linear `gamma` stays as the long-lived permanent part.
//!
//! Two switches, both 0 on every preset, handle agents' RESTING orders
//! against the memory (sim/r17-wash). `impact_memory_refill`: a resting
//! order filled against the memory's lean (by the market's flow, or crossed
//! during the session) takes its size off the memory, never past zero, as
//! new depth on the side the lean consumed; without it a group that buys as
//! a taker and sells back through asks at the touch leaves the memory
//! displaced for free. `book_cross_at_limit`: a resting order the book
//! leaves crossed during the session trades at its own limit, not at the
//! maker's better price. See `Engine::refill_memory`.
//!
//! What a tick's flow may add to the displacement is capped by what it
//! paid: `|D(after) - D(before)| <= (1 + delta) * paid / |net shares|`,
//! `paid` the flow's `sum shares * |log(price / reference)|`. A sell back
//! along the memory's path recovers `1 / (1 + delta)` of the displacement
//! on average, so without the cap a block that walked a deep maker ladder
//! for little more than the spread would leave a displacement worth more
//! than it cost, and selling it back would pay (measured: `+2` to `+6` bp
//! on a 3% block sold back in six slices, pt-v20, `Y_M` 0.5 to 0.6). With
//! it, the round trip's recovery is at most its cost before spreads and
//! decay. On the latent curve the cap does not bind, since the walk's
//! average is `Y_B / (1 + delta)` of its marginal and `Y_M <= Y_B`.
//!
//! The latent depth on the side the memory leans continues from the
//! memory's point on its own curve, `x_M = (|D| / (Y_B sigma))^(1/delta)`:
//! its `Q`-th share is priced at `touch (1 + Y_B sigma ((x_M + Q/V)^delta
//! - x_M^delta))`, and the front removed for consumption is what `taken`
//! holds beyond `x_M V`. At `M` = 0 this is the rule above, exactly.
//!
//! This is the volume-recovery book of Alfonsi, Fruth and Schied
//! (Quantitative Finance 10(2), 2010) with a book linear in distance, the
//! same family as the latent refill. Against the lean, the house's side is
//! bounded by the memory's own path (`MemoryBound`): a sell into a
//! buy-displaced price fetches at most `P exp(D(M - q) - B)`, so unwinding
//! walks the displacement back down rather than cashing it at the spread.
//! On the lean's side the latent depth continues the curve at `Y_B >= Y_M`;
//! only the maker's ladder, a few tenths of a percent of daily volume at
//! the spread, is cheaper than the curve there, as it is on a fresh book.
//! With `M` = 0 nothing is bounded, so a single block on a fresh book costs
//! what it did without the memory (C9 is unchanged). Alfonsi, Fruth and
//! Schied show their book admits no price manipulation for a single
//! exponential (`w` = 0); this book is not exactly theirs, and the
//! round-trip tests in `tests/test_impact_memory.py` are the check.

use crate::market::TickCompany;
use crate::market_maker::MARKET_MAKER_ID;
use crate::mathx;
use crate::microstructure::{build_live_book, LiveBookOptions, BOOK_LEVELS};
use crate::order_book::{BookOrder, OrderBook, Side};
use crate::params::ModelParams;

/// The owner id of every latent-depth level.
pub const DEPTH_OWNER: &str = "depth";

/// The owner id the model's own flow trades under in settlement.
pub const FLOW_OWNER: &str = "flow";

/// The owner id recorded as the counterparty of a traded-range fill.
pub const RANGE_OWNER: &str = "range";

/// Per-side cap on the book an agent trades against. The maker's ten levels,
/// the latent depth (tens of levels) and every agent's resting orders must
/// all fit; the default cap of 32 would trim the far latent levels as soon as
/// agents rested orders, which is the cut-off the depth exists to remove.
pub const AGENT_BOOK_CAP: usize = 4096;

/// Cumulative size ratio from one latent level to the next.
///
/// 1.25 keeps a walk within one step of the continuous law: at the square
/// root a level priced at its last share overcharges its first by at most
/// `sqrt(1.25) - 1`, 11.8% of the distance from the touch, and the average
/// over the level by about half that. Tens of levels reach a whole day's
/// volume, which the per-side cap holds with room for agents' orders.
pub const DEPTH_LEVEL_RATIO: f64 = 1.25;

/// The smallest cumulative size, as a fraction of daily volume, the latent
/// grid descends to. Below it the maker's ladder is always deeper.
const DEPTH_GRID_FLOOR: f64 = 1e-4;

/// Indices into [`BookState::taken`], per company slot.
pub const TAKEN_MAKER_BID: usize = 0;
pub const TAKEN_MAKER_ASK: usize = 1;
pub const TAKEN_DEPTH_BID: usize = 2;
pub const TAKEN_DEPTH_ASK: usize = 3;
/// The maker's inventory change from agents' fills since its last quote,
/// applied when it next re-quotes (the next tick, or the open).
pub const TAKEN_MAKER_INVENTORY: usize = 4;
pub const TAKEN_WIDTH: usize = 5;

/// Indices into [`BookState::memory`], per company slot: the fast and slow
/// memories of agents' net flow against the house, in fractions of daily
/// volume; the displacement already booked into `s`, in log units; what
/// the taker flow waiting for the next tick paid the house, `sum shares *
/// |log(price / reference)|`, which caps how far that flow may move the
/// displacement; and that flow itself, signed shares (bought less sold)
/// that the house took the other side of. A fill between two agents is in
/// neither of the last two: it moved no house liquidity.
pub const MEMORY_FAST: usize = 0;
pub const MEMORY_SLOW: usize = 1;
pub const MEMORY_BOOKED: usize = 2;
pub const MEMORY_PAID: usize = 3;
pub const MEMORY_FLOW: usize = 4;
pub const MEMORY_WIDTH: usize = 5;

/// How an order that did not fill in full waits.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum RestMode {
    /// In the book, in the queue at its price (`book_resting` on).
    Queue,
    /// Outside the book, filled at its limit when a print reaches it
    /// (`book_resting` off).
    Range,
}

impl RestMode {
    pub fn as_str(self) -> &'static str {
        match self {
            RestMode::Queue => "queue",
            RestMode::Range => "range",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "queue" => Some(RestMode::Queue),
            "range" => Some(RestMode::Range),
            _ => None,
        }
    }
}

/// An agent's order that is waiting: resting in the queue, or waiting for the
/// traded range.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct AgentOrder {
    pub id: String,
    pub agent: String,
    pub ticker: String,
    pub side: Side,
    pub limit: f64,
    /// Size when submitted, in shares.
    pub quantity: f64,
    /// Shares still waiting.
    pub remaining: f64,
    /// Engine-wide arrival counter: the time half of price-time priority
    /// among agents' orders.
    pub sequence: u64,
    pub mode: RestMode,
}

impl AgentOrder {
    /// A waiting order with nothing filled: `remaining` is `quantity` and
    /// `sequence` is 0. Set `remaining` and `sequence` on the value to
    /// restore one that has part filled or that queued behind others.
    pub fn new(
        id: impl Into<String>,
        agent: impl Into<String>,
        ticker: impl Into<String>,
        side: Side,
        limit: f64,
        quantity: f64,
        mode: RestMode,
    ) -> Self {
        AgentOrder {
            id: id.into(),
            agent: agent.into(),
            ticker: ticker.into(),
            side,
            limit,
            quantity,
            remaining: quantity,
            sequence: 0,
            mode,
        }
    }
}

/// Which side of a trade an agent was on.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum Liquidity {
    /// The agent's order crossed and took liquidity.
    Taker,
    /// The agent's resting order was traded against.
    Maker,
    /// A waiting limit filled at its limit because a print reached it.
    Range,
}

impl Liquidity {
    pub fn as_str(self) -> &'static str {
        match self {
            Liquidity::Taker => "taker",
            Liquidity::Maker => "maker",
            Liquidity::Range => "range",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s {
            "taker" => Some(Liquidity::Taker),
            "maker" => Some(Liquidity::Maker),
            "range" => Some(Liquidity::Range),
            _ => None,
        }
    }
}

/// One fill of one agent's order, at one price.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct AgentFill {
    pub agent: String,
    pub order_id: String,
    pub ticker: String,
    /// The AGENT's side.
    pub side: Side,
    pub quantity: f64,
    pub price: f64,
    pub liquidity: Liquidity,
    /// Who was on the other side: [`MARKET_MAKER_ID`], [`DEPTH_OWNER`],
    /// [`FLOW_OWNER`], [`RANGE_OWNER`], or another agent's label.
    pub counterparty: String,
    /// The price the fill is measured against: the mid of the book the
    /// order met, for a taker; the last print before the tick that filled
    /// it, for a resting or waiting order.
    pub reference: f64,
    /// The engine's day, and the ticks already run that day, when it filled.
    pub day: i64,
    pub tick: u32,
    /// Engine-wide fill counter, so fills sort into the order they happened.
    pub sequence: u64,
}

/// One agent's permanent impact on one name, applied on one tick.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct AgentImpact {
    pub agent: String,
    pub ticker: String,
    pub bought: f64,
    pub sold: f64,
    /// The change to the name's `s` this agent's fills made, in log units:
    /// exact under `fill_impact_coefficient`, where the law is linear and
    /// additive; the tick's flow impact shared pro rata by signed shares
    /// under the imbalance law, where it is neither.
    pub permanent: f64,
    /// The metaorder memory's part: the tick's flow-driven change to the
    /// displacement, `D(M after the flow) - D(M decayed)`, shared pro rata
    /// by each agent's signed net shares. `None` while
    /// `impact_memory_coefficient` is 0.0, and then absent from the Python
    /// row, the snapshot and the state hash.
    pub transient: Option<f64>,
    pub day: i64,
    pub tick: u32,
}

/// Everything the agent-facing book carries between calls.
///
/// Pristine (every field empty or zero) on an engine no agent has sent an
/// order to, and a pristine state is left out of the state hash and the
/// snapshot, so every run that never uses this hashes as it did before it
/// existed.
#[derive(Debug, Clone, Default, PartialEq)]
#[non_exhaustive]
pub struct BookState {
    /// Per company slot, in roster order: see the `TAKEN_*` indices.
    pub taken: Vec<[f64; TAKEN_WIDTH]>,
    /// Waiting orders, every name, in arrival order.
    pub orders: Vec<AgentOrder>,
    /// Taker flow not yet applied to the market: `(agent, ticker, bought,
    /// sold)`, one row per pair, in the order each pair first traded.
    pub flow: Vec<(String, String, f64, f64)>,
    /// Fills not yet collected by `take_fills`.
    pub fills: Vec<AgentFill>,
    /// Impact rows not yet collected by `take_impacts`.
    pub impacts: Vec<AgentImpact>,
    /// The next order's arrival number.
    pub sequence: u64,
    /// The next fill's number.
    pub fill_sequence: u64,
    /// Per company slot, the metaorder memory: see the `MEMORY_*` indices.
    /// Empty unless `impact_memory_coefficient` is set and agents' flow has
    /// reached the market, and then carried by the snapshot and the state
    /// hash; empty, it is in neither.
    pub memory: Vec<[f64; MEMORY_WIDTH]>,
}

impl BookState {
    /// True when nothing has ever happened here.
    pub fn is_pristine(&self) -> bool {
        self.sequence == 0
            && self.fill_sequence == 0
            && self.orders.is_empty()
            && self.flow.is_empty()
            && self.fills.is_empty()
            && self.impacts.is_empty()
            && self.taken.iter().all(|row| row.iter().all(|v| *v == 0.0))
            && self.memory.iter().all(|row| row.iter().all(|v| *v == 0.0))
    }

    /// Bring `taken` to the roster's length, adding zero rows.
    pub fn fit(&mut self, companies: usize) {
        if self.taken.len() < companies {
            self.taken.resize(companies, [0.0; TAKEN_WIDTH]);
        }
    }

    /// Record an agent's taker fill against the house (the maker or the
    /// latent depth, never another agent) for the metaorder memory: its
    /// signed shares on the name's [`MEMORY_FLOW`], and what it paid
    /// against its reference, `quantity * |log(price / reference)|`, on
    /// [`MEMORY_PAID`] for the cap. Called only with the memory on.
    pub fn add_house_flow(
        &mut self,
        companies: usize,
        index: usize,
        side: Side,
        quantity: f64,
        price: f64,
        reference: f64,
    ) {
        if !(quantity > 0.0) {
            return;
        }
        if self.memory.len() < companies {
            self.memory.resize(companies, [0.0; MEMORY_WIDTH]);
        }
        if let Some(row) = self.memory.get_mut(index) {
            row[MEMORY_FLOW] += match side {
                Side::Buy => quantity,
                Side::Sell => -quantity,
            };
            if price > 0.0 && reference > 0.0 {
                row[MEMORY_PAID] += quantity * mathx::log(price / reference).abs();
            }
        }
    }

    /// Add taker flow for one agent and name.
    pub fn add_flow(&mut self, agent: &str, ticker: &str, side: Side, quantity: f64) {
        let (b, s) = match side {
            Side::Buy => (quantity, 0.0),
            Side::Sell => (0.0, quantity),
        };
        match self
            .flow
            .iter_mut()
            .find(|(a, t, _, _)| a == agent && t == ticker)
        {
            Some(row) => {
                row.2 += b;
                row.3 += s;
            }
            None => self.flow.push((agent.to_string(), ticker.to_string(), b, s)),
        }
    }
}

/// The name's conditional daily volatility, as the square-root law reads it:
/// its own GARCH variance plus its beta's share of the market factor's.
///
/// The sector factor is left out; at every shipped sigma it is a few percent
/// of a name's variance, and leaving it out keeps this a function of the
/// company and one engine number.
pub fn daily_sigma(company: &TickCompany, market_sigma_daily: f64) -> f64 {
    let beta = company.stock.beta.unwrap_or(1.0);
    let idio = mathx::max(0.0, company.stock.garch_variance);
    let market = beta * market_sigma_daily;
    mathx::sqrt(idio + market * market)
}

/// The name's daily volume, as the maker sizes its ladder from it: the same
/// `avg_volume || volume || shares * 0.005 || 100` chain as
/// `microstructure::base_quote_size`, before its division by one hundred.
pub fn daily_volume(company: &TickCompany) -> f64 {
    let truthy = |v: f64| if v != 0.0 && !v.is_nan() { Some(v) } else { None };
    truthy(company.stock.avg_volume)
        .or_else(|| truthy(company.stock.volume))
        .or_else(|| truthy(company.stock.shares_outstanding * 0.005))
        .unwrap_or(100.0)
}

/// Cents, rounded AWAY from the touch: up for an ask, down for a bid. A
/// level is never priced better than the law it follows.
fn cents_away(price: f64, side: Side) -> f64 {
    let cents = price * 100.0;
    // A hair inside the rounding so a price that IS on the grid, give or
    // take the last bit of a multiply, stays on its own cent.
    match side {
        Side::Sell => (cents - 1e-7).ceil() / 100.0,
        Side::Buy => (cents + 1e-7).floor() / 100.0,
    }
}

/// Add the latent depth to one side of the book.
///
/// The latent pool is independent of the maker's ladder and sits beside it:
/// its own cumulative size, measured from the ladder's best price `touch`,
/// follows the law, so its `Q`-th share is priced at `touch * (1 + Y sigma
/// (Q / V)^delta)` or one cent worse. The levels are a geometric grid of
/// cumulative size, from a ten-thousandth of daily volume out to the reach,
/// each priced at its LAST share and rounded away from the touch; levels that
/// round to the same cent merge. `side` is the side of the BOOK (`Sell` for
/// asks).
///
/// Beside the ladder rather than behind it, because the ladder's shape is
/// the legacy display book's and does not scale with a name's volatility:
/// ten levels a half-spread apart, a percent of daily volume each. Behind
/// it, the cost of three percent of daily volume was the ladder's alone,
/// at 2.4 times the square-root law for a wide-spread name and a quarter of
/// it for a tight one (`tools/calibration/impact_curve.py`, measured on the
/// first version of this function). Beside it, a walk meets whichever pool
/// is cheaper at each price, so the book is never dearer than the law, and
/// past the ladder's few percent of daily volume it IS the law.
///
/// `from` is where on its own curve the pool starts, in fractions of daily
/// volume: 0.0 is the law above; off zero, on the side the metaorder memory
/// leans, the `Q`-th share is priced at `touch * (1 + Y sigma ((from +
/// Q/V)^delta - from^delta))`, the curve continued from the memory's point.
///
/// With `book_depth_nesting` `k` off zero, the pool counts `k` times the
/// ladder's shares at a price as good as a level's or better as already on
/// its curve: the cumulative size placed through that level is `Q - k L`
/// (never less than what is already placed), `Q` the law's cumulative size
/// at the level's price and `L` the ladder's. At `k` = 1 the depth within
/// any distance of the touch is the larger of the ladder's and the law's.
/// The ladder is read from `book` as it stands, so this is called before
/// anything else is added to that side. At 0.0 nothing here is read.
pub fn append_latent_depth(
    book: &mut OrderBook,
    side: Side,
    touch: f64,
    sigma: f64,
    volume: f64,
    params: &ModelParams,
    from: f64,
) {
    let y = params.book_depth_coefficient;
    let (delta, reach) = depth_shape(params);
    let reach = reach * volume;
    if !(y > 0.0) || !(volume > 0.0) || !(touch > 0.0) || !(sigma > 0.0) {
        return;
    }
    // The grid, descending from the reach by the ratio, then walked outward.
    // Anchored at the reach so a level's boundary is a property of the name
    // and not of anything else in the book.
    let floor = volume * DEPTH_GRID_FLOOR;
    let mut bounds = vec![reach];
    while let Some(&last) = bounds.last() {
        let next = last / DEPTH_LEVEL_RATIO;
        if next < floor {
            break;
        }
        bounds.push(next);
    }
    bounds.reverse();

    // The ladder on this side, best first, for the nesting.
    let nest = params.book_depth_nesting;
    let ladder: Vec<(f64, f64)> = if nest > 0.0 {
        let orders = match side {
            Side::Buy => &book.bids,
            Side::Sell => &book.asks,
        };
        orders.iter().map(|o| (o.price, o.remaining)).collect()
    } else {
        Vec::new()
    };
    // Integer shares: a level is the difference of two floored cumulative
    // sizes, so the floors never accumulate. Levels on the same cent merge
    // into one order, carried until the price moves on.
    let mut placed = 0.0;
    let mut pending: Option<(f64, f64)> = None;
    let mut levels: Vec<(f64, f64)> = Vec::new();
    for bound in bounds {
        // A branch, so the pool without a memory is priced as it always was.
        let distance = if from > 0.0 {
            y * sigma * (mathx::pow(from + bound / volume, delta) - mathx::pow(from, delta))
        } else {
            y * sigma * mathx::pow(bound / volume, delta)
        };
        let raw = match side {
            Side::Sell => touch * (1.0 + distance),
            Side::Buy => touch * (1.0 - distance),
        };
        let price = cents_away(raw, side);
        if !(price > 0.0) {
            break;
        }
        // A branch, so the pool beside the ladder is placed as it always was.
        let bound = if nest > 0.0 {
            let ahead: f64 = ladder
                .iter()
                .filter(|(p, _)| match side {
                    Side::Sell => *p <= price,
                    Side::Buy => *p >= price,
                })
                .map(|(_, q)| *q)
                .sum();
            mathx::max(placed, bound - nest * ahead)
        } else {
            bound
        };
        let shares = bound.floor() - placed;
        if !(shares >= 1.0) {
            continue;
        }
        placed += shares;
        pending = match pending {
            Some((p, q)) if p == price => Some((p, q + shares)),
            Some(level) => {
                levels.push(level);
                Some((price, shares))
            }
            None => Some((price, shares)),
        };
    }
    if let Some(level) = pending {
        levels.push(level);
    }
    for (price, shares) in levels {
        book.rest_limit(side, price, shares, DEPTH_OWNER);
    }
}

/// The latent depth's exponent and reach, with 0.0 read as their defaults:
/// the square root and one day's volume.
pub fn depth_shape(params: &ModelParams) -> (f64, f64) {
    let delta = if params.book_depth_exponent == 0.0 { 0.5 } else { params.book_depth_exponent };
    let reach = if params.book_depth_reach == 0.0 { 1.0 } else { params.book_depth_reach };
    (delta, reach)
}

/// The metaorder memory's combined position `M = (1 - w) Mf + w Ms`, in
/// fractions of daily volume.
pub fn memory_position(params: &ModelParams, row: &[f64; MEMORY_WIDTH]) -> f64 {
    let w = params.impact_memory_slow_weight;
    if w == 0.0 {
        row[MEMORY_FAST]
    } else {
        (1.0 - w) * row[MEMORY_FAST] + w * row[MEMORY_SLOW]
    }
}

/// The memory's displacement of `s` for a position `m` (fractions of daily
/// volume) at daily sigma `sigma`: `sign(m) Y_M sigma h(|m|)`, with `h(x) =
/// x^delta` at or above the crossover `m*` and `x m*^(delta - 1)` below it.
pub fn memory_displacement(params: &ModelParams, m: f64, sigma: f64) -> f64 {
    let y = params.impact_memory_coefficient;
    if !(y > 0.0) || m == 0.0 || !(sigma > 0.0) {
        return 0.0;
    }
    let (delta, _) = depth_shape(params);
    let a = m.abs();
    let star = params.impact_memory_crossover;
    let h = if star > 0.0 && a < star {
        a * mathx::pow(star, delta - 1.0)
    } else {
        mathx::pow(a, delta)
    };
    let d = y * sigma * h;
    if m > 0.0 { d } else { -d }
}

/// The per-open-tick decay of a memory at half-life `h` ticks: `2^(-1/h)`,
/// or 1.0 (none) at 0.0.
pub fn memory_decay(h: f64) -> f64 {
    if h > 0.0 { mathx::pow(0.5, 1.0 / h) } else { 1.0 }
}

/// The memory's point on the latent book's own curve, in fractions of
/// daily volume, for a booked displacement `booked`: `(|D| / (Y_B
/// sigma))^(1/delta)`. Zero without the memory or the depth.
pub fn memory_point(params: &ModelParams, booked: f64, sigma: f64) -> f64 {
    let yb = params.book_depth_coefficient;
    if booked == 0.0 || !(yb > 0.0) || !(sigma > 0.0) || !(params.impact_memory_coefficient > 0.0) {
        return 0.0;
    }
    let (delta, _) = depth_shape(params);
    mathx::pow(booked.abs() / (yb * sigma), 1.0 / delta)
}

/// The inverse of [`memory_displacement`]: the position whose displacement
/// is `d`.
pub fn memory_inverse(params: &ModelParams, d: f64, sigma: f64) -> f64 {
    let y = params.impact_memory_coefficient;
    if !(y > 0.0) || d == 0.0 || !(sigma > 0.0) {
        return 0.0;
    }
    let (delta, _) = depth_shape(params);
    let a = d.abs() / (y * sigma);
    let star = params.impact_memory_crossover;
    let m = if star > 0.0 && a < mathx::pow(star, delta) {
        a / mathx::pow(star, delta - 1.0)
    } else {
        mathx::pow(a, 1.0 / delta)
    };
    if d > 0.0 { m } else { -m }
}

/// The metaorder memory's bound on the house's side of the book: no share
/// an agent takes from the maker or the latent depth is priced better than
/// the memory's own price once that share is in it.
///
/// A buy's `Q`-th share costs at least `P exp(D(M + Q/V) - B)` and a sell's
/// fetches at most `P exp(D(M - Q/V) - B)`, where `P` is the last print,
/// `B` the displacement already booked into it and `M` the memory with the
/// flow still waiting for the next tick. It acts only on the side AGAINST
/// the memory's lean (the bids while agents have net bought, the asks while
/// they have net sold), and not at all while `M` is zero: on the lean's own
/// side the latent depth continues the memory's curve, and a fresh book is
/// the book it was, so a single block costs what it always did. At the
/// memory's own position the bound is the print; past it, the maker's
/// ladder is pulled down (or lifted) to the curve. This is
/// what closes the round trip the tape's displacement would otherwise open:
/// the maker re-quotes around the displaced print with its full depth, so a
/// sell against the lean would fetch the displaced price for the price of a
/// spread, and alternating buys and sells of a percent of daily volume
/// would earn the displacement. Bounded, both legs pay the memory's path,
/// as they do in the book of Alfonsi, Fruth and Schied (2010).
struct MemoryBound<'a> {
    params: &'a ModelParams,
    sigma: f64,
    volume: f64,
    reference: f64,
    position: f64,
    booked: f64,
}

impl MemoryBound<'_> {
    /// The bound on the `q`-th share (cumulative shares) on one side: the
    /// book's `Sell` side is what a buy meets.
    fn price(&self, side: Side, q: f64) -> f64 {
        let m = match side {
            Side::Sell => self.position + q / self.volume,
            Side::Buy => self.position - q / self.volume,
        };
        let d = memory_displacement(self.params, m, self.sigma);
        self.reference * mathx::exp(d - self.booked)
    }

    /// Cumulative shares up to which a level at `price` is inside the bound.
    fn reach(&self, side: Side, price: f64) -> f64 {
        let x = mathx::log(price / self.reference) + self.booked;
        let m = memory_inverse(self.params, x, self.sigma);
        match side {
            Side::Sell => (m - self.position) * self.volume,
            Side::Buy => (self.position - m) * self.volume,
        }
    }

    /// One side's house orders, best first, with every share past the
    /// bound re-priced onto it. A level is kept at its price up to where
    /// the bound crosses it; the rest is cut on a grid of cumulative size
    /// (each piece a quarter longer than what came before it, and at least
    /// a ten-thousandth of daily volume), each piece priced at its LAST
    /// share and rounded away from the touch, as the latent levels are. The
    /// result is sorted as the book requires.
    fn apply(&self, orders: Vec<BookOrder>, side: Side, company_id: &str) -> Vec<BookOrder> {
        let worse = |a: f64, b: f64| match side {
            Side::Sell => mathx::max(a, b),
            Side::Buy => mathx::min(a, b),
        };
        let step = self.volume * DEPTH_GRID_FLOOR;
        let mut out: Vec<BookOrder> = Vec::with_capacity(orders.len());
        let mut placed = 0.0;
        let mut last: Option<f64> = None;
        // One buffer for every order's pieces, emptied at each.
        let mut pieces: Vec<(f64, f64)> = Vec::new();
        for o in orders {
            let mut left = o.remaining;
            let within = mathx::max(0.0, mathx::min(left, self.reach(side, o.price) - placed));
            pieces.clear();
            if within > 0.0 {
                pieces.push((o.price, within));
                left -= within;
                placed += within;
            }
            while left > 1e-9 {
                let span = mathx::max(step, 0.25 * placed);
                let take = mathx::min(left, span);
                placed += take;
                left -= take;
                let bound = cents_away(self.price(side, placed), side);
                pieces.push((worse(o.price, bound), take));
            }
            for (k, &(price, shares)) in pieces.iter().enumerate() {
                let price = match last {
                    Some(prev) => worse(price, prev),
                    None => price,
                };
                last = Some(price);
                out.push(if k == 0 {
                    BookOrder {
                        price,
                        quantity: shares,
                        remaining: shares,
                        ..o.clone()
                    }
                } else {
                    o.piece(k as u32, price, shares, company_id)
                });
            }
        }
        out
    }
}

/// Remove the first `quantity` shares owned by `owner` from one side of the
/// book, best price first. Orders owned by anyone else are left in place.
pub fn remove_front(book: &mut OrderBook, side: Side, owner: &str, quantity: f64) {
    if !(quantity > 0.0) {
        return;
    }
    let orders = match side {
        Side::Buy => &mut book.bids,
        Side::Sell => &mut book.asks,
    };
    let mut left = quantity;
    for o in orders.iter_mut() {
        if !(left > 0.0) {
            break;
        }
        if o.owner_id != owner {
            continue;
        }
        let take = mathx::min(o.remaining, left);
        o.remaining -= take;
        left -= take;
    }
    orders.retain(|o| o.remaining > 0.0);
}

/// What building the agent-facing book needs.
#[non_exhaustive]
pub struct AgentBookInputs<'a> {
    pub company: &'a TickCompany,
    pub vix: f64,
    pub params: &'a ModelParams,
    /// The market factor's daily sigma today, for [`daily_sigma`].
    pub market_sigma_daily: f64,
    /// This company's row of [`BookState::taken`], or zeros.
    pub taken: [f64; TAKEN_WIDTH],
    /// This name's row of [`BookState::memory`], or zeros. Its
    /// [`MEMORY_FLOW`] is the flow against the house the next tick adds.
    pub memory: [f64; MEMORY_WIDTH],
    /// Waiting orders; only this name's [`RestMode::Queue`] orders are used.
    pub orders: &'a [AgentOrder],
    /// Leave this agent's own orders out: the book an agent's order meets.
    pub exclude_agent: Option<&'a str>,
}

impl<'a> AgentBookInputs<'a> {
    /// The book for `company` with nothing taken from it, no metaorder
    /// memory, no waiting orders and no agent left out. Set `taken`,
    /// `memory`, `orders` or `exclude_agent` on the value to add them.
    pub fn new(
        company: &'a TickCompany,
        vix: f64,
        params: &'a ModelParams,
        market_sigma_daily: f64,
    ) -> Self {
        AgentBookInputs {
            company,
            vix,
            params,
            market_sigma_daily,
            taken: [0.0; TAKEN_WIDTH],
            memory: [0.0; MEMORY_WIDTH],
            orders: &[],
            exclude_agent: None,
        }
    }
}

/// The maker's ladder, quoted the way the tick quotes it for this name.
///
/// The spread curve comes from the preset, which is what settlement uses.
/// `Engine::book_for`'s dial-off path passes the step curve (0.0) whatever
/// the preset says; every shipped preset ships 0.0, so the two agree on all
/// of them, and this path is the one that agrees with settlement for a
/// preset that does not.
pub fn maker_ladder(company: &TickCompany, vix: f64, params: &ModelParams) -> OrderBook {
    build_live_book(
        &company.micro_view(company.stock.price),
        &LiveBookOptions {
            vix,
            spread_size_smoothness: params.spread_size_smoothness,
            spread_size_exponent: params.spread_size_exponent,
            levels: BOOK_LEVELS,
            ..Default::default()
        },
    )
    .with_cap(AGENT_BOOK_CAP)
}

/// Build the book an agent trades against, for one name, right now.
///
/// The three layers in the module note, in order, then consumption removed
/// from the front of the first two, then the resting orders inserted.
pub fn agent_book(inputs: &AgentBookInputs<'_>) -> OrderBook {
    let company = inputs.company;
    let params = inputs.params;
    let mut book = maker_ladder(company, inputs.vix, params);

    // The metaorder memory's point on the latent curve, and the side it
    // leans: the ask when agents have net bought (the book's `Sell` side).
    let mut lean: Option<(Side, f64, f64)> = None;
    if params.book_depth_coefficient > 0.0 {
        let sigma = daily_sigma(company, inputs.market_sigma_daily);
        let volume = daily_volume(company);
        let booked = inputs.memory[MEMORY_BOOKED];
        let x_m = memory_point(params, booked, sigma);
        if x_m > 0.0 {
            let side = if booked > 0.0 { Side::Sell } else { Side::Buy };
            lean = Some((side, x_m, volume));
        }
        for side in [Side::Buy, Side::Sell] {
            let touch = match side {
                Side::Buy => book.best_bid(),
                Side::Sell => book.best_ask(),
            };
            let from = match lean {
                Some((s, x, _)) if s == side => x,
                _ => 0.0,
            };
            if let Some(touch) = touch {
                append_latent_depth(&mut book, side, touch, sigma, volume, params, from);
            }
        }
    }

    let t = inputs.taken;
    // On the side the memory leans, the memory already holds the front of
    // what was taken: only what `taken` holds beyond `x_M V` is removed.
    let depth_front = |side: Side, taken: f64| match lean {
        Some((s, x, v)) if s == side => mathx::max(0.0, taken - x * v),
        _ => taken,
    };
    remove_front(&mut book, Side::Buy, MARKET_MAKER_ID, t[TAKEN_MAKER_BID]);
    remove_front(&mut book, Side::Sell, MARKET_MAKER_ID, t[TAKEN_MAKER_ASK]);
    remove_front(&mut book, Side::Buy, DEPTH_OWNER, depth_front(Side::Buy, t[TAKEN_DEPTH_BID]));
    remove_front(&mut book, Side::Sell, DEPTH_OWNER, depth_front(Side::Sell, t[TAKEN_DEPTH_ASK]));

    // No house share is better than the memory's price after it.
    if params.impact_memory_coefficient > 0.0 {
        let sigma = daily_sigma(company, inputs.market_sigma_daily);
        let volume = daily_volume(company);
        let reference = company.stock.price;
        if volume > 0.0 && sigma > 0.0 && reference > 0.0 {
            let m = memory_position(params, &inputs.memory) + inputs.memory[MEMORY_FLOW] / volume;
            let bound = MemoryBound {
                params,
                sigma,
                volume,
                reference,
                position: m,
                booked: inputs.memory[MEMORY_BOOKED],
            };
            // Only against the lean: a sell into a buy-displaced price, or
            // a buy into a sell-displaced one. On the lean's own side the
            // latent depth already continues the memory's curve, and with
            // no memory there is no bound, so an order on a fresh book
            // meets the book it always did.
            if m > 0.0 {
                book.bids = bound.apply(std::mem::take(&mut book.bids), Side::Buy, &book.company_id);
            } else if m < 0.0 {
                book.asks = bound.apply(std::mem::take(&mut book.asks), Side::Sell, &book.company_id);
            }
        }
    }

    for o in inputs.orders {
        if o.mode != RestMode::Queue || o.ticker != company.ticker {
            continue;
        }
        if inputs.exclude_agent == Some(o.agent.as_str()) {
            continue;
        }
        book.post_limit(o.side, o.limit, o.remaining, &o.agent, Some(o.id.clone()));
    }
    book
}

/// Which `taken` slots a taker's fill against `owner` consumes.
pub fn taken_slot(taker_side: Side, owner: &str) -> Option<usize> {
    match (taker_side, owner) {
        (Side::Buy, MARKET_MAKER_ID) => Some(TAKEN_MAKER_ASK),
        (Side::Sell, MARKET_MAKER_ID) => Some(TAKEN_MAKER_BID),
        (Side::Buy, DEPTH_OWNER) => Some(TAKEN_DEPTH_ASK),
        (Side::Sell, DEPTH_OWNER) => Some(TAKEN_DEPTH_BID),
        _ => None,
    }
}

/// True for the owners that are not agents.
pub fn is_house(owner: &str) -> bool {
    owner == MARKET_MAKER_ID || owner == DEPTH_OWNER || owner == FLOW_OWNER || owner == RANGE_OWNER
}

/// The per-tick refill factor for consumed latent depth: `0.5^(1/h)`, or
/// zero (a full refill each tick) at a half-life of zero.
pub fn refill_factor(params: &ModelParams) -> f64 {
    let h = params.book_refill_half_life;
    if h > 0.0 {
        mathx::pow(0.5, 1.0 / h)
    } else {
        0.0
    }
}

/// Whether a print reached a waiting limit: at or below a buy's limit, at or
/// above a sell's.
pub fn range_reached(side: Side, limit: f64, print: f64) -> bool {
    match side {
        Side::Buy => print <= limit,
        Side::Sell => print >= limit,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::market::{TickCompany, TickStock};
    use crate::order_book::SubmitOptions;

    fn company(price: f64, adv: f64, garch_variance: f64) -> TickCompany {
        TickCompany {
            id: "ACME".into(),
            ticker: "ACME".into(),
            sector: "technology".into(),
            is_bankrupt: false,
            is_public: true,
            stock: TickStock {
                price,
                previous_close: price,
                previous_tick_price: None,
                open: price,
                high: price,
                low: price,
                volume: 0.0,
                avg_volume: adv,
                shares_outstanding: 1e8,
                market_cap: price * 1e8,
                mispricing_s: None,
                mispricing_s_prev_close: None,
                mispricing_momentum: None,
                maker_inventory: Some(0.0),
                garch_variance,
                garch_cascade: [0.0; crate::market::garch::CASCADE_MAX],
                last_daily_return: None,
                beta: Some(1.0),
                short_interest: 0.0,
                float: 1e8,
                fair_value_offset: None,
                buyback_log_shares: None,
                dividend: None,
            },
            sector_volatility: Some(1.0),
            sector_avg_pe: None,
            eps: Some(5.0),
            book_value_per_share: Some(20.0),
            revenue_growth: Some(0.05),
        }
    }

    fn tail_params(y: f64) -> ModelParams {
        let mut p = crate::params::PT_V1;
        p.book_depth_coefficient = y;
        p.book_depth_exponent = 0.5;
        p.book_depth_reach = 1.0;
        p
    }

    fn inputs<'a>(c: &'a TickCompany, p: &'a ModelParams, orders: &'a [AgentOrder]) -> AgentBookInputs<'a> {
        AgentBookInputs {
            company: c,
            vix: 15.0,
            params: p,
            market_sigma_daily: 0.01,
            taken: [0.0; TAKEN_WIDTH],
            memory: [0.0; MEMORY_WIDTH],
            orders,
            exclude_agent: None,
        }
    }

    fn memory_params(y_m: f64) -> ModelParams {
        let mut p = tail_params(0.75);
        p.book_shared = 1.0;
        p.impact_memory_coefficient = y_m;
        p.impact_memory_half_life = 90.0;
        p.impact_memory_crossover = 0.001;
        p
    }

    /// The displacement is the square root above the crossover, linear
    /// below it and continuous at it, odd in the position, and inverted by
    /// `memory_inverse`; off, it is zero.
    #[test]
    fn the_memory_displacement_is_a_square_root_with_a_linear_foot() {
        let p = memory_params(0.6);
        let sigma = 0.02;
        let d = |m: f64| memory_displacement(&p, m, sigma);
        assert!((d(0.1) - 0.6 * sigma * 0.1f64.sqrt()).abs() < 1e-15);
        assert!((d(-0.1) + d(0.1)).abs() < 1e-15);
        assert!((d(0.0005) - 0.6 * sigma * 0.0005 / 0.001f64.sqrt()).abs() < 1e-15);
        assert!((d(0.001 - 1e-12) - d(0.001)).abs() < 1e-12);
        for m in [-0.3, -0.01, -0.0002, 0.0002, 0.004, 0.2] {
            assert!((memory_inverse(&p, d(m), sigma) - m).abs() < 1e-12, "{m}");
        }
        let off = memory_params(0.0);
        assert_eq!(memory_displacement(&off, 0.1, sigma), 0.0);
        assert_eq!(memory_point(&off, 0.01, sigma), 0.0);
        assert_eq!(memory_decay(0.0), 1.0);
        assert!((memory_decay(90.0).powf(90.0) - 0.5).abs() < 1e-12);
    }

    /// With an empty memory the book is the book without it: the bound acts
    /// only against a lean, so a block on a fresh book (row C9) is priced
    /// as it always was.
    #[test]
    fn an_empty_memory_leaves_the_book_as_it_was() {
        let c = company(50.0, 2e5, 0.0004);
        let on = memory_params(0.6);
        let mut off = on.clone();
        off.impact_memory_coefficient = 0.0;
        off.impact_memory_half_life = 0.0;
        let a = agent_book(&inputs(&c, &on, &[]));
        let b = agent_book(&inputs(&c, &off, &[]));
        assert_eq!(a.asks, b.asks);
        assert_eq!(a.bids, b.bids);
    }

    /// `book_depth_nesting`: at 1.0 the depth within any price of the touch
    /// is the larger of the ladder's and the law's (to a share and the
    /// grid's rounding), never their sum and never less than the law; the
    /// ladder itself is untouched; at 0.0 the book is the book beside it.
    #[test]
    fn nesting_makes_the_depth_the_larger_of_ladder_and_law() {
        let c = company(50.0, 2e5, 0.0004);
        let beside = tail_params(0.75);
        let mut nested = beside.clone();
        nested.book_depth_nesting = 1.0;
        let a = agent_book(&inputs(&c, &beside, &[]));
        let b = agent_book(&inputs(&c, &nested, &[]));
        let ladder = maker_ladder(&c, 15.0, &beside);
        let within = |orders: &[BookOrder], owner: Option<&str>, price: f64| -> f64 {
            orders.iter().filter(|o| o.price <= price && owner.map_or(true, |w| o.owner_id == w))
                .map(|o| o.remaining).sum()
        };
        // The ladder is the same in both.
        let maker = |bk: &OrderBook| -> Vec<(f64, f64)> {
            bk.asks.iter().filter(|o| o.owner_id == MARKET_MAKER_ID).map(|o| (o.price, o.remaining)).collect()
        };
        assert_eq!(maker(&a), maker(&b));
        let mut thinner = false;
        for o in &a.asks {
            let p = o.price;
            let lad = within(&ladder.asks, None, p);
            let law_beside = within(&a.asks, Some(DEPTH_OWNER), p);
            let total_nested = within(&b.asks, None, p);
            // Never less than either pool alone, never more than the larger
            // plus the one level the law's grid may carry past it.
            assert!(total_nested + 1.0 >= lad.max(law_beside) - 1.0, "{p}");
            assert!(total_nested <= lad.max(law_beside) + law_beside * 0.25 + 2.0, "{p}");
            if total_nested + 1.0 < within(&a.asks, None, p) {
                thinner = true;
            }
        }
        assert!(thinner, "nesting must remove the double-counted front somewhere");
        // Off, identical to the book without the dial.
        let mut zero = beside.clone();
        zero.book_depth_nesting = 0.0;
        assert_eq!(agent_book(&inputs(&c, &zero, &[])).asks, a.asks);
    }

    /// Leaning long (agents have net bought), the asks continue the latent
    /// curve from the memory's point and the bids are bounded by the
    /// memory's own path, so a sell back fetches no more than the path.
    #[test]
    fn a_long_lean_bounds_the_bids_and_continues_the_asks() {
        let c = company(50.0, 2e5, 0.0004);
        let p = memory_params(0.6);
        let sigma = daily_sigma(&c, 0.01);
        let m = 0.05;
        let booked = memory_displacement(&p, m, sigma);
        let mut inp = inputs(&c, &p, &[]);
        inp.memory = [m, 0.0, booked, 0.0, 0.0];
        let book = agent_book(&inp);
        let mut plain = p.clone();
        plain.impact_memory_coefficient = 0.0;
        plain.impact_memory_half_life = 0.0;
        let fresh = agent_book(&inputs(&c, &plain, &[]));
        // Asks: the latent depth continues the curve from the memory's
        // point, where it is flatter, so a buy of a tenth of a day's volume
        // reaches less far past the touch than on a fresh curve (the touch
        // already carries `booked`).
        let worst = |b: &OrderBook| b.sweep_cost(Side::Buy, 0.1 * 2e5).unwrap().worst_price;
        assert!(worst(&book) < worst(&fresh), "{} {}", worst(&book), worst(&fresh));
        // Bids: selling the whole memory back fetches at most the path.
        let q = m * 2e5;
        let got = book.sweep_cost(Side::Sell, q).unwrap().average_price;
        let path = 50.0 * (-(booked - 0.6 * sigma * (2.0 / 3.0) * m.sqrt())).exp();
        assert!(got <= path + 0.02, "{got} above the path's {path}");
        assert!(got < fresh.sweep_cost(Side::Sell, q).unwrap().average_price + 1e-9);
    }

    /// Off, the agent book is the maker's ten levels and nothing else.
    #[test]
    fn without_the_tail_the_book_is_the_ladder() {
        let c = company(100.0, 1e6, 0.0002);
        let p = crate::params::PT_V1;
        let book = agent_book(&inputs(&c, &p, &[]));
        assert_eq!(book.asks.len(), BOOK_LEVELS as usize);
        assert_eq!(book.bids.len(), BOOK_LEVELS as usize);
        let plain = maker_ladder(&c, 15.0, &p);
        assert_eq!(book.asks, plain.asks);
    }

    /// The latent pool reaches the dial's reach, walks away from the touch,
    /// and keeps the book in price order with the ladder beside it.
    #[test]
    fn the_latent_pool_reaches_a_days_volume_and_is_monotone() {
        let c = company(100.0, 1e6, 0.0002);
        let p = tail_params(0.5);
        let book = agent_book(&inputs(&c, &p, &[]));
        for side in [Side::Buy, Side::Sell] {
            let orders = if side == Side::Buy { &book.bids } else { &book.asks };
            let latent: Vec<_> = orders.iter().filter(|o| o.owner_id == DEPTH_OWNER).collect();
            let total: f64 = latent.iter().map(|o| o.remaining).sum();
            assert!(total > 0.99e6 && total <= 1e6, "{side:?} depth {total}");
            for w in latent.windows(2) {
                match side {
                    Side::Buy => assert!(w[1].price < w[0].price),
                    Side::Sell => assert!(w[1].price > w[0].price),
                }
            }
            for w in orders.windows(2) {
                match side {
                    Side::Buy => assert!(w[1].price <= w[0].price),
                    Side::Sell => assert!(w[1].price >= w[0].price),
                }
            }
        }
    }

    /// Every latent share is priced at or beyond the law for its place in
    /// the latent pool, so the pool never sells cheaper than the law says.
    #[test]
    fn no_latent_share_is_cheaper_than_the_law() {
        let c = company(50.0, 2e5, 0.0004);
        let p = tail_params(0.7);
        let book = agent_book(&inputs(&c, &p, &[]));
        let sigma = daily_sigma(&c, 0.01);
        let touch = book.asks[0].price;
        let mut cumulative = 0.0;
        for o in book.asks.iter().filter(|o| o.owner_id == DEPTH_OWNER) {
            cumulative += o.remaining;
            let law = touch * (1.0 + 0.7 * sigma * (cumulative / 2e5).sqrt());
            assert!(o.price >= law - 1e-9, "{} below {law} at {cumulative}", o.price);
        }
    }

    /// And the whole book, the two pools together, is never dearer than the
    /// law: the maker's ladder only adds depth.
    #[test]
    fn the_book_is_never_dearer_than_the_law_past_a_grid_step() {
        let c = company(50.0, 2e5, 0.0004);
        let p = tail_params(0.7);
        let book = agent_book(&inputs(&c, &p, &[]));
        let sigma = daily_sigma(&c, 0.01);
        let touch = book.asks[0].price;
        for f in [0.01, 0.03, 0.1, 0.3, 0.9] {
            let q = f * 2e5;
            let worst = book.sweep_cost(Side::Buy, q).unwrap().worst_price;
            let law = touch * (1.0 + 0.7 * sigma * (f * DEPTH_LEVEL_RATIO).sqrt());
            assert!(worst <= law + 0.01, "{f}: {worst} past {law}");
        }
    }

    /// A buy of a third of a day's volume fills in full at a price that
    /// grows like the square root, instead of stopping at the ladder.
    #[test]
    fn a_large_order_walks_the_tail_instead_of_being_cut_off() {
        let c = company(100.0, 1e6, 0.0002);
        let off = crate::params::PT_V1;
        let on = tail_params(0.5);
        let mut cut = agent_book(&inputs(&c, &off, &[]));
        let mut deep = agent_book(&inputs(&c, &on, &[]));
        let a = cut.submit(Side::Buy, 300_000.0, "a", SubmitOptions::default());
        let b = deep.submit(Side::Buy, 300_000.0, "a", SubmitOptions::default());
        assert!(a.unfilled > 200_000.0, "the ladder alone holds under 5%: {}", a.unfilled);
        assert_eq!(b.unfilled, 0.0);
        assert!(b.average_price.unwrap() > a.average_price.unwrap());
    }

    /// Consumption comes off the front, a layer at a time.
    #[test]
    fn taken_volume_comes_off_the_front_of_its_own_layer() {
        let c = company(100.0, 1e6, 0.0002);
        let p = tail_params(0.5);
        let full = agent_book(&inputs(&c, &p, &[]));
        let maker = |b: &OrderBook| -> Vec<(f64, f64)> {
            b.asks.iter().filter(|o| o.owner_id == MARKET_MAKER_ID)
                .map(|o| (o.price, o.remaining)).collect()
        };
        let latent = |b: &OrderBook| -> Vec<(f64, f64)> {
            b.asks.iter().filter(|o| o.owner_id == DEPTH_OWNER)
                .map(|o| (o.price, o.remaining)).collect()
        };
        let first = maker(&full)[0].1;
        let mut inp = inputs(&c, &p, &[]);
        inp.taken[TAKEN_MAKER_ASK] = first + 10.0;
        let less = agent_book(&inp);
        assert_eq!(maker(&less)[0], (maker(&full)[1].0, maker(&full)[1].1 - 10.0));
        assert_eq!(latent(&less), latent(&full), "the latent pool is its own layer");
        assert_eq!(less.bids, full.bids, "the other side is untouched");
    }

    /// A resting order queues behind the maker at an equal price, ahead of
    /// nobody, and a taker fills it only after the maker's size there.
    #[test]
    fn a_resting_order_queues_behind_the_maker_at_its_price() {
        let c = company(100.0, 1e6, 0.0002);
        let p = crate::params::PT_V1;
        let ladder = maker_ladder(&c, 15.0, &p);
        let touch = ladder.asks[0].price;
        let at_touch = ladder.asks[0].remaining;
        let orders = vec![AgentOrder {
            id: "b-0".into(),
            agent: "b".into(),
            ticker: "ACME".into(),
            side: Side::Sell,
            limit: touch,
            quantity: 500.0,
            remaining: 500.0,
            sequence: 0,
            mode: RestMode::Queue,
        }];
        let mut book = agent_book(&inputs(&c, &p, &orders));
        assert_eq!(book.asks[0].owner_id, MARKET_MAKER_ID);
        assert_eq!(book.asks[1].owner_id, "b");
        let r = book.submit(Side::Buy, at_touch + 200.0, "a", SubmitOptions::default());
        let from_b: f64 = r.fills.iter().filter(|f| f.maker_id == "b").map(|f| f.quantity).sum();
        assert_eq!(from_b, 200.0, "partial: the queue ahead took the rest");
    }

    #[test]
    fn an_agent_never_meets_its_own_orders() {
        let c = company(100.0, 1e6, 0.0002);
        let p = crate::params::PT_V1;
        let orders = vec![AgentOrder {
            id: "a-0".into(),
            agent: "a".into(),
            ticker: "ACME".into(),
            side: Side::Sell,
            limit: 99.0,
            quantity: 5.0,
            remaining: 5.0,
            sequence: 0,
            mode: RestMode::Queue,
        }];
        let mut inp = inputs(&c, &p, &orders);
        inp.exclude_agent = Some("a");
        let book = agent_book(&inp);
        assert!(book.asks.iter().all(|o| o.owner_id != "a"));
    }

    #[test]
    fn a_range_order_is_not_in_the_book() {
        let c = company(100.0, 1e6, 0.0002);
        let p = crate::params::PT_V1;
        let orders = vec![AgentOrder {
            id: "a-0".into(),
            agent: "a".into(),
            ticker: "ACME".into(),
            side: Side::Buy,
            limit: 99.99,
            quantity: 5.0,
            remaining: 5.0,
            sequence: 0,
            mode: RestMode::Range,
        }];
        let book = agent_book(&inputs(&c, &p, &orders));
        assert!(book.bids.iter().all(|o| o.owner_id == MARKET_MAKER_ID));
    }

    #[test]
    fn the_refill_factor_halves_at_the_half_life() {
        let mut p = tail_params(0.5);
        p.book_shared = 1.0;
        p.book_refill_half_life = 27.0;
        let f = refill_factor(&p);
        let mut x = 1.0;
        for _ in 0..27 {
            x *= f;
        }
        assert!((x - 0.5).abs() < 1e-12, "{x}");
        p.book_refill_half_life = 0.0;
        assert_eq!(refill_factor(&p), 0.0);
    }

    #[test]
    fn a_pristine_state_says_so_and_a_used_one_does_not() {
        let mut s = BookState::default();
        s.fit(3);
        assert!(s.is_pristine());
        s.add_flow("a", "ACME", Side::Buy, 10.0);
        assert!(!s.is_pristine());
        s.add_flow("a", "ACME", Side::Sell, 4.0);
        assert_eq!(s.flow, vec![("a".to_string(), "ACME".to_string(), 10.0, 4.0)]);
    }
}
