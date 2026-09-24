//! Rate instruments: simulated constant-maturity bond indices priced off the
//! engine's own yield curve.
//!
//! The economy already carries a policy rate, 2-year and 10-year treasury
//! yields and an investment-grade corporate yield. Nothing could trade them.
//! This module adds three index instruments that can, and that trade through
//! the same book, fills, portfolio and tape as the equities:
//!
//! | ticker   | what it is                                          | yield it reads |
//! |----------|-----------------------------------------------------|----------------|
//! | `UST2Y`  | simulated 2-year constant-maturity treasury index   | `treasury_yield_2y` |
//! | `UST10Y` | simulated 10-year constant-maturity treasury index  | `treasury_yield_10y` |
//! | `IGCORP` | simulated investment-grade corporate bond index     | the 10-year plus the engine's credit spread |
//!
//! None of them is a real security. Each is a constant-maturity index in the
//! way a bond ETF is: it rolls to keep its duration fixed, so its return is
//! a function of the yield path and nothing else.
//!
//! # The pricing formula
//!
//! Each time the yield an instrument reads changes, the index level moves by
//!
//! ```text
//! r = carry - D * dy + 0.5 * C * dy * dy
//! level = level * (1.0 + r)
//! ```
//!
//! with `dy` the change in yield as a fraction (200 basis points is 0.02),
//! `D` the modified duration, `C` the convexity in years squared, and
//! `carry` equal to `y / 252` at the first open after a close, where `y` is
//! the yield the level was marked at overnight, and zero otherwise. The terms
//! are added in that order and the product taken once, so a caller can
//! reproduce a level bit for bit. When yields change only between sessions,
//! which is how the engine moves them, the close-to-close return is exactly
//! `y_prev / 252 - D * dy + 0.5 * C * dy^2`.
//!
//! The quadratic has its minimum at `dy = D / C` and would price a larger
//! rise as a gain, so a single move above that is priced at `D / C`: 10.1
//! percentage points for the 10-year, 7.0 for the corporate index and 41
//! for the 2-year. The engine clamps its own 10-year to 0.5 to 12 per cent,
//! so only a pin can get there.
//!
//! # Duration and convexity
//!
//! Computed for par bonds paying semiannual coupons (`tools/bonds/durations.py`
//! reproduces them):
//!
//! - `UST2Y`, D 1.9, C 4.6: a 2-year par note at 4% has D 1.904, C 4.62.
//! - `UST10Y`, D 8.5, C 84: a 10-year par note has D 8.80 and C 87.7 at
//!   2.5%, D 8.18 and C 78.9 at 4%; D 8.5 is the note at 3.2%, where C is
//!   83.4. A +200bp move prices at -8.5 * 0.02 + 0.5 * 84 * 0.0004 =
//!   -15.32%. Exact repricing of a 4% 10-year gives -14.88%.
//! - `IGCORP`, D 7.0, C 100: an index is a spread of maturities, and a spread
//!   carries more convexity than a single bond of the same duration. A
//!   40/30/15/15 mix of 3, 7, 20 and 30-year par bonds at 5% has D 7.06 and
//!   C 100.4, where one 8.5-year bond at 4% has D 7.1 and C 60.
//!
//! # Which yield the corporate index reads
//!
//! The engine sets `corporate_bond_yield` at central-bank meetings (and, on
//! presets with `daily_credit_floor_gain`, lifts it to a floor over the
//! 10-year). Between those moments it does not move while the 10-year does.
//! An index priced off that field directly would carry no rate risk between
//! meetings and then jump, which would mislead anyone measuring its risk.
//!
//! So `IGCORP` reads the 10-year plus a credit spread, and the spread is
//! re-marked to `corporate_bond_yield - treasury_yield_10y` every time the
//! engine's corporate yield changes (a meeting, a floor) and every time a
//! caller pins it, even to the value it already had. At each of those
//! moments the index yield IS the engine's corporate yield. In between it
//! moves with the treasury it is quoted over. Equities still discount off
//! `corporate_bond_yield` itself; nothing here writes to the economy.
//!
//! The pin rule matters for a scenario that holds the corporate yield. The
//! packaged `rate_shock.yml` holds it 200bp up from day 50 while the engine's
//! own 10-year climbs toward the higher policy rate over the following weeks.
//! Re-marked only on a change, the spread would have been set on day 50 and
//! the index would then have added the 10-year's climb on top, falling 23%
//! for a 200bp corporate move. Re-marked at each pin, the index reads the
//! held yield every day, which is the scenario's statement.
//!
//! # The intraday path, and why nothing can see tomorrow
//!
//! The economy steps once a day, at the close, and nothing in the engine
//! moves yields inside a session. So an index level is constant through a
//! session unless a pin changes a yield, and every yield change reaches the
//! level on the first tick (or open) after it is written. It is never
//! interpolated toward a later yield: the next yield does not exist until
//! the close computes it, and a path that walked toward it would have to be
//! computed from tomorrow. No tracking noise is added. Noise that reverted at
//! the next open would be a free mean-reversion trade, and noise that did not
//! would be a second rate process the equities never see.
//!
//! A yield set at the close is visible in `Engine.macro_state` from then on,
//! and reaches the index level at the next open. That is the same boundary
//! the equities have: their fair value moves with `corporate_bond_yield` on
//! the first tick of the next day.
//!
//! # Liquidity
//!
//! Books are quoted by the same market-maker ladder the equities use
//! (`market_maker::quote_ladder`), around the index level, with inventory
//! skew. Two things differ and both are set per instrument:
//!
//! - **Spread.** The bond ETFs these resemble (SHY, IEF, LQD) quote one cent
//!   wide at a price near $100, which is 1 basis point, and widen in stress.
//!   The ladder rounds every quote to a cent, so what sets the quoted spread
//!   is whether the half-spread before rounding, `spread_bps / 2` times the
//!   equity rule's VIX multiplier (`microstructure::vix_spread_multiplier`),
//!   is under half a cent. `spread_bps` is 0.6 for the treasuries and 0.8
//!   for the corporate index, so at a $100 level each quotes one cent wide
//!   below VIX 35 (the treasuries) and 22.5 (the corporate index). Above
//!   those the grid gives one or two cents, depending on where the level
//!   sits between whole cents, and wider as the VIX climbs. A `spread_bps`
//!   of 1.0 would quote up to two cents at any VIX above 15, which is most
//!   sessions here. The equity rule would put a $30B name at 3 bp or more
//!   at any VIX.
//! - **Depth.** Quoted off `avg_volume` exactly as the equities are, with
//!   defaults set to the median daily dollar volume of the ETF each index
//!   resembles over 2015-2025 (Yahoo daily bars): SHY $218M, IEF $464M, LQD
//!   $1,426M. At a $100 index level that is 2.2M, 4.6M and 14M units a day.
//!
//! There is no noise-trader flow and no breaker. With no trading, the print
//! is the index level. An order that reaches the book (a fill fed back as
//! `fills`, or a standing `flow_per_tick`) walks the ladder, prints at its
//! last fill and leaves the maker holding inventory; the maker skews its
//! quotes against that inventory by up to a half spread and lays it off with
//! a 15-minute half-life, which stands in for a dealer hedging in the cash
//! and futures markets. So a trade's impact on the tape is transient, and a
//! bond index cannot be pushed away from its yield: arbitrage against the
//! index holds an ETF to its NAV in the same way.
//!
//! # Draws
//!
//! None. Everything here is arithmetic on the economy's yields and the flow
//! a caller supplies, so adding rate instruments leaves every equity price,
//! draw and macro value bit-identical to the same run without them.

use crate::economy::EconomyState;
use crate::market::{
    get_market_status, intraday_fraction, intraday_volume, GameTime, MarketStatus, OrderVolume,
};
use crate::market_maker::{quote_ladder, LadderParams, MakerInventory, QuoteParams, MARKET_MAKER_ID};
use crate::mathx;
use crate::microstructure::{
    decompose, maker_delta_from_fills, vix_spread_multiplier, BOOK_LEVELS, INVENTORY_LIMIT_LEVELS,
};
use crate::order_book::{OrderBook, Side, SubmitOptions};

/// The sector key a rate instrument carries in a universe.
///
/// Not one of the twelve equity sectors, and never passed to anything that
/// reads a sector table: an engine splits these out of the roster before any
/// equity code sees them.
pub const RATE_SECTOR: &str = "rates";

/// Trading days a year, for carry: a day's carry is `yield / 252`.
pub const CARRY_DAYS: f64 = 252.0;

/// Minutes for the maker to lay off half of an inventory it was left with.
pub const INVENTORY_HALF_LIFE_TICKS: f64 = 15.0;

/// Which yield an instrument reads.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CurvePoint {
    Treasury2Y,
    Treasury10Y,
    /// The 10-year plus the credit spread the engine last set. See the
    /// module header.
    InvestmentGrade,
}

impl CurvePoint {
    pub fn as_str(self) -> &'static str {
        match self {
            CurvePoint::Treasury2Y => "treasury_2y",
            CurvePoint::Treasury10Y => "treasury_10y",
            CurvePoint::InvestmentGrade => "investment_grade",
        }
    }
}

/// One instrument's fixed terms.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct RateSpec {
    pub ticker: &'static str,
    /// What it is, in words, for anyone reading a roster.
    pub name: &'static str,
    pub point: CurvePoint,
    /// Modified duration, years.
    pub duration: f64,
    /// Convexity, years squared.
    pub convexity: f64,
    /// Full spread at VIX 15 and below, basis points, before the ladder
    /// rounds the quotes to cents. See the module header for what that
    /// quotes at a $100 level.
    pub spread_bps: f64,
    /// Default average daily volume, index units.
    pub avg_volume: f64,
    /// Default units outstanding. Read for `market_cap` and nothing else.
    pub units_outstanding: f64,
    /// Default starting level.
    pub initial_price: f64,
}

/// The three instruments this build prices. See the module header for every
/// number here.
pub const RATE_SPECS: [RateSpec; 3] = [
    RateSpec {
        ticker: "UST2Y",
        name: "Simulated 2-year constant-maturity US Treasury index (not a real security)",
        point: CurvePoint::Treasury2Y,
        duration: 1.9,
        convexity: 4.6,
        spread_bps: 0.6,
        avg_volume: 2.2e6,
        units_outstanding: 3.0e8,
        initial_price: 100.0,
    },
    RateSpec {
        ticker: "UST10Y",
        name: "Simulated 10-year constant-maturity US Treasury index (not a real security)",
        point: CurvePoint::Treasury10Y,
        duration: 8.5,
        convexity: 84.0,
        spread_bps: 0.6,
        avg_volume: 4.6e6,
        units_outstanding: 3.0e8,
        initial_price: 100.0,
    },
    RateSpec {
        ticker: "IGCORP",
        name: "Simulated investment-grade US corporate bond index (not a real security)",
        point: CurvePoint::InvestmentGrade,
        duration: 7.0,
        convexity: 100.0,
        spread_bps: 0.8,
        avg_volume: 1.4e7,
        units_outstanding: 3.0e8,
        initial_price: 100.0,
    },
];

/// The spec for a rate ticker, or `None` for anything else.
pub fn spec_for(ticker: &str) -> Option<&'static RateSpec> {
    RATE_SPECS.iter().find(|s| s.ticker == ticker)
}

/// The rate tickers, in the order [`RATE_SPECS`] declares them.
pub fn tickers() -> Vec<&'static str> {
    RATE_SPECS.iter().map(|s| s.ticker).collect()
}

/// The yield `point` reads, as a fraction.
///
/// `ig_spread` is the credit spread the book last marked, as a fraction. The
/// economy holds percent; the conversion is `units::percent_to_fraction`.
pub fn curve_yield(point: CurvePoint, economy: &EconomyState, ig_spread: f64) -> f64 {
    match point {
        CurvePoint::Treasury2Y => crate::units::percent_to_fraction(economy.treasury_yield_2y),
        CurvePoint::Treasury10Y => crate::units::percent_to_fraction(economy.treasury_yield_10y),
        CurvePoint::InvestmentGrade => {
            crate::units::percent_to_fraction(economy.treasury_yield_10y) + ig_spread
        }
    }
}

/// The corporate index's spread, as a fraction, from the economy as it now
/// stands: `corporate_bond_yield - treasury_yield_10y`.
pub fn credit_spread(economy: &EconomyState) -> f64 {
    crate::units::percent_to_fraction(economy.corporate_bond_yield)
        - crate::units::percent_to_fraction(economy.treasury_yield_10y)
}

/// The three terms of one repricing, as fractions of the level.
///
/// `dy` above `duration / convexity` is priced at that bound, where the
/// quadratic turns. `carry_yield` is the overnight yield on the step that
/// accrues carry and zero on every other.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Repricing {
    pub carry: f64,
    pub duration: f64,
    pub convexity: f64,
}

impl Repricing {
    pub fn new(duration: f64, convexity: f64, dy: f64, carry_yield: f64) -> Self {
        let dy = if convexity > 0.0 { mathx::min(dy, duration / convexity) } else { dy };
        Repricing {
            carry: carry_yield / CARRY_DAYS,
            duration: -duration * dy,
            convexity: 0.5 * convexity * dy * dy,
        }
    }

    /// `carry + duration + convexity`, added in that order.
    pub fn total(&self) -> f64 {
        self.carry + self.duration + self.convexity
    }

    /// The level after this repricing: `level * (1.0 + total)`.
    pub fn apply(&self, level: f64) -> f64 {
        level * (1.0 + self.total())
    }
}

/// One instrument's state.
#[derive(Debug, Clone, PartialEq)]
pub struct RateInstrument {
    pub spec: RateSpec,
    /// The index level: what the instrument is worth at the yield it is
    /// marked at. The book quotes around it and an untraded print equals it.
    pub level: f64,
    /// The yield `level` is marked at, as a fraction.
    pub marked_yield: f64,
    /// The last print.
    pub price: f64,
    pub previous_close: f64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    /// Units traded so far today.
    pub volume: f64,
    pub avg_volume: f64,
    pub units_outstanding: f64,
    /// The maker's position, units. Negative after the market bought.
    pub maker_inventory: f64,
    /// Today's repricing terms, summed. Reset at the open, before the open
    /// reprices, so the overnight move is in the day it lands on.
    pub day_carry: f64,
    pub day_duration: f64,
    pub day_convexity: f64,
    /// The last tick's print decomposition, as `microstructure::decompose`
    /// defines it. Per-tick output, not state.
    pub tick_shock: f64,
    pub tick_absorbed: f64,
}

impl RateInstrument {
    pub fn new(spec: RateSpec, initial_price: f64, avg_volume: f64, units_outstanding: f64) -> Self {
        RateInstrument {
            spec,
            level: initial_price,
            marked_yield: 0.0,
            price: initial_price,
            previous_close: initial_price,
            open: initial_price,
            high: initial_price,
            low: initial_price,
            volume: 0.0,
            avg_volume,
            units_outstanding,
            maker_inventory: 0.0,
            day_carry: 0.0,
            day_duration: 0.0,
            day_convexity: 0.0,
            tick_shock: 0.0,
            tick_absorbed: 0.0,
        }
    }

    pub fn market_cap(&self) -> f64 {
        self.price * self.units_outstanding
    }

    /// Half the quoted spread, in basis points, at `vix`.
    pub fn half_spread_bps(&self, vix: f64) -> f64 {
        self.spec.spread_bps / 2.0 * vix_spread_multiplier(vix)
    }

    /// Units quoted at the touch: `floor(avg_volume / 100)`, at least one,
    /// the rule the equity book uses.
    pub fn base_size(&self) -> f64 {
        mathx::max(1.0, (self.avg_volume / 100.0).floor())
    }

    fn inventory_limit(&self) -> f64 {
        mathx::max(1.0, self.base_size() * INVENTORY_LIMIT_LEVELS)
    }

    /// The mid the maker quotes: the level, moved against its inventory by
    /// up to a half spread. The level itself when the maker is flat, which is
    /// every tick of a run nobody trades in.
    pub fn quoted_mid(&self, vix: f64) -> f64 {
        if self.maker_inventory == 0.0 {
            return self.level;
        }
        let load = mathx::clamp(self.maker_inventory / self.inventory_limit(), -1.0, 1.0);
        let half_spread = self.level * (self.half_spread_bps(vix) / 10_000.0);
        self.level - load * half_spread
    }

    /// The executable book, now: the equity maker's ladder around the index
    /// level, with this instrument's spread and depth.
    pub fn book(&self, vix: f64) -> OrderBook {
        let mut book = OrderBook::new(self.spec.ticker.to_string(), Some(self.price));
        if !(self.level > 0.0) {
            return book;
        }
        let base_size = self.base_size();
        let (bids, asks) = quote_ladder(&LadderParams {
            quote: QuoteParams {
                fair_value: self.level,
                half_spread_bps: self.half_spread_bps(vix),
                base_size,
                inventory: MakerInventory {
                    position: self.maker_inventory,
                    limit: self.inventory_limit(),
                },
                ..QuoteParams::default()
            },
            levels: BOOK_LEVELS,
            level_step: 0.5,
        });
        for level in &bids {
            book.append_maker_level(Side::Buy, level.price, level.size, MARKET_MAKER_ID);
        }
        for level in &asks {
            book.append_maker_level(Side::Sell, level.price, level.size, MARKET_MAKER_ID);
        }
        book
    }
}

/// Per-tick fraction of a day's volume the maker's own flow prints, summed to
/// one over a 390-minute session. Computed once from the equity profile.
fn volume_profile_total() -> f64 {
    static TOTAL: std::sync::OnceLock<f64> = std::sync::OnceLock::new();
    *TOTAL.get_or_init(|| {
        let mut total = 0.0;
        for k in 0..390 {
            total += intraday_volume(k as f64 / 390.0, MarketStatus::Open);
        }
        total
    })
}

/// The factor the maker's inventory is multiplied by each tick.
fn inventory_decay() -> f64 {
    static DECAY: std::sync::OnceLock<f64> = std::sync::OnceLock::new();
    *DECAY.get_or_init(|| mathx::pow(0.5, 1.0 / INVENTORY_HALF_LIFE_TICKS))
}

/// Every rate instrument an engine holds, and the curve state they share.
///
/// Empty on every engine built without rate instruments, and an empty book
/// is never touched: no hook below runs, no column grows, nothing is hashed.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct RateBook {
    pub instruments: Vec<RateInstrument>,
    /// The corporate index's credit spread over the 10-year, as a fraction,
    /// re-marked whenever the engine's corporate yield changes or a caller
    /// pins it.
    pub ig_spread: f64,
    /// The `corporate_bond_yield` (percent, as the economy holds it) the
    /// spread was last marked against.
    pub last_corporate: f64,
    /// Whether a close has happened since the last open, which is what makes
    /// the next open accrue a day's carry.
    pub closed_since_open: bool,
}

impl RateBook {
    /// Mark every instrument at the curve `economy` holds now.
    pub fn new(mut instruments: Vec<RateInstrument>, economy: &EconomyState) -> Self {
        let ig_spread = credit_spread(economy);
        for inst in &mut instruments {
            inst.marked_yield = curve_yield(inst.spec.point, economy, ig_spread);
        }
        RateBook {
            instruments,
            ig_spread,
            last_corporate: economy.corporate_bond_yield,
            closed_since_open: false,
        }
    }

    pub fn len(&self) -> usize {
        self.instruments.len()
    }

    pub fn is_empty(&self) -> bool {
        self.instruments.is_empty()
    }

    pub fn index_of(&self, ticker: &str) -> Option<usize> {
        self.instruments.iter().position(|i| i.spec.ticker == ticker)
    }

    /// Re-mark the credit spread to the economy as it stands, whether or not
    /// the corporate yield moved. The engine calls this when a caller pins
    /// the corporate yield, so a pin that holds it at yesterday's value still
    /// holds the corporate index there. See the module header.
    pub fn remark_credit_spread(&mut self, economy: &EconomyState) {
        self.ig_spread = credit_spread(economy);
        self.last_corporate = economy.corporate_bond_yield;
    }

    /// Bring every level to the curve `economy` now holds.
    ///
    /// Re-marks the credit spread first if the corporate yield has moved.
    /// `carry` adds a day's carry at each instrument's overnight yield.
    pub fn sync(&mut self, economy: &EconomyState, carry: bool) {
        if economy.corporate_bond_yield != self.last_corporate {
            self.ig_spread = credit_spread(economy);
            self.last_corporate = economy.corporate_bond_yield;
        }
        let ig_spread = self.ig_spread;
        for inst in &mut self.instruments {
            let y = curve_yield(inst.spec.point, economy, ig_spread);
            let dy = y - inst.marked_yield;
            if dy == 0.0 && !carry {
                continue;
            }
            let carry_yield = if carry { inst.marked_yield } else { 0.0 };
            let step = Repricing::new(inst.spec.duration, inst.spec.convexity, dy, carry_yield);
            inst.level = step.apply(inst.level);
            inst.day_carry += step.carry;
            inst.day_duration += step.duration;
            inst.day_convexity += step.convexity;
            inst.marked_yield = y;
        }
    }

    /// The day's open: roll the previous close, accrue the overnight carry if
    /// a close came before, reprice to the curve, and reset the day's marks.
    pub fn open(&mut self, economy: &EconomyState) {
        for inst in &mut self.instruments {
            inst.previous_close = inst.price;
            inst.day_carry = 0.0;
            inst.day_duration = 0.0;
            inst.day_convexity = 0.0;
        }
        let carry = self.closed_since_open;
        self.sync(economy, carry);
        self.closed_since_open = false;
        let vix = economy.vix;
        for inst in &mut self.instruments {
            let print = inst.quoted_mid(vix);
            inst.tick_shock = 0.0;
            inst.tick_absorbed = 0.0;
            inst.price = print;
            inst.open = print;
            inst.high = print;
            inst.low = print;
            inst.volume = 0.0;
        }
    }

    /// One minute. Consumes no draws.
    ///
    /// A closed market does nothing, as it does for the equities. Otherwise:
    /// reprice if a yield moved, put any flow for an instrument through its
    /// book, print, then let the maker lay off a minute's worth of inventory.
    pub fn tick(&mut self, time: GameTime, economy: &EconomyState, flows: &[(String, OrderVolume)]) {
        let status = get_market_status(time);
        if status == MarketStatus::Closed {
            return;
        }
        self.sync(economy, false);
        let share = intraday_volume(intraday_fraction(time), status) / volume_profile_total();
        let decay = inventory_decay();
        let vix = economy.vix;
        for inst in &mut self.instruments {
            let last_print = inst.price;
            let mut print = inst.quoted_mid(vix);
            let mut traded = 0.0;
            let flow = flows
                .iter()
                .find(|(t, _)| t == inst.spec.ticker)
                .map(|(_, v)| *v)
                .unwrap_or_default();
            if flow.buy > 0.0 || flow.sell > 0.0 {
                let mut book = inst.book(vix);
                let mut delta = 0.0;
                for (side, quantity) in [(Side::Buy, flow.buy), (Side::Sell, flow.sell)] {
                    let result = book.submit(
                        side,
                        quantity,
                        "flow",
                        SubmitOptions { limit_price: None, post_remainder: false, order_id: None },
                    );
                    for fill in &result.fills {
                        traded += fill.quantity;
                    }
                    delta += maker_delta_from_fills(&result.fills);
                }
                inst.maker_inventory += delta;
                if traded > 0.0 {
                    if let Some(p) = book.last_price {
                        print = p;
                    }
                }
            }
            let (shock, absorbed) = decompose(last_print, inst.level, print);
            inst.tick_shock = shock;
            inst.tick_absorbed = absorbed;
            inst.price = print;
            inst.high = mathx::max(inst.high, print);
            inst.low = mathx::min(inst.low, print);
            inst.volume += inst.avg_volume * share + traded;
            if inst.maker_inventory != 0.0 {
                inst.maker_inventory *= decay;
            }
        }
    }

    /// The close. Nothing reprices here: the economy steps after this and its
    /// yields reach the levels at the next open.
    pub fn close(&mut self) {
        self.closed_since_open = true;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::economy::{create_initial_economy_state, InitialEconomyOptions};

    fn economy() -> EconomyState {
        create_initial_economy_state(&InitialEconomyOptions::default())
    }

    fn book() -> RateBook {
        let instruments = RATE_SPECS
            .iter()
            .map(|s| RateInstrument::new(*s, s.initial_price, s.avg_volume, s.units_outstanding))
            .collect();
        RateBook::new(instruments, &economy())
    }

    fn at(hour: i64, minute: i64) -> GameTime {
        GameTime { hour, minute, day_of_week: 3 }
    }

    #[test]
    fn a_200bp_rise_prices_the_10_year_at_minus_15_32_per_cent() {
        let r = Repricing::new(8.5, 84.0, 0.02, 0.0);
        assert_eq!(r.duration, -0.17);
        assert!((r.total() - (-0.1532)).abs() < 1e-15, "{}", r.total());
        let two = Repricing::new(1.9, 4.6, 0.02, 0.0).total();
        assert!((two - (-0.03708)).abs() < 1e-15, "{two}");
        let ig = Repricing::new(7.0, 100.0, 0.02, 0.0).total();
        assert!((ig - (-0.12)).abs() < 1e-15, "{ig}");
    }

    #[test]
    fn the_quadratic_is_never_priced_past_its_turning_point() {
        let at_bound = Repricing::new(8.5, 84.0, 8.5 / 84.0, 0.0).total();
        let beyond = Repricing::new(8.5, 84.0, 0.30, 0.0).total();
        assert_eq!(at_bound, beyond);
        // A fall in yields is never clamped: both terms are gains.
        let fall = Repricing::new(8.5, 84.0, -0.30, 0.0).total();
        assert!(fall > 2.5);
    }

    #[test]
    fn an_unchanged_curve_leaves_every_level_alone_inside_a_day() {
        let e = economy();
        let mut b = book();
        b.open(&e);
        let levels: Vec<f64> = b.instruments.iter().map(|i| i.level).collect();
        for m in 0..390 {
            b.tick(at(9 + (30 + m) / 60, (30 + m) % 60), &e, &[]);
        }
        for (inst, before) in b.instruments.iter().zip(levels) {
            assert_eq!(inst.level, before);
            assert_eq!(inst.price, before, "an untraded print is the level");
        }
    }

    #[test]
    fn the_first_open_accrues_no_carry_and_later_opens_accrue_one_day() {
        let e = economy();
        let mut b = book();
        b.open(&e);
        assert_eq!(b.instruments[1].level, 100.0);
        b.close();
        b.open(&e);
        let y = crate::units::percent_to_fraction(e.treasury_yield_10y);
        assert_eq!(b.instruments[1].level, 100.0 * (1.0 + y / 252.0));
        // Opening twice without a close does not pay the night twice.
        let once = b.instruments[1].level;
        b.open(&e);
        assert_eq!(b.instruments[1].level, once);
    }

    #[test]
    fn a_yield_move_reprices_on_the_next_open_by_the_formula() {
        let mut e = economy();
        let mut b = book();
        b.open(&e);
        b.close();
        let y0 = crate::units::percent_to_fraction(e.treasury_yield_10y);
        e.treasury_yield_10y += 2.0;
        b.open(&e);
        let y1 = crate::units::percent_to_fraction(e.treasury_yield_10y);
        let expected = Repricing::new(8.5, 84.0, y1 - y0, y0).apply(100.0);
        assert_eq!(b.instruments[1].level, expected);
        assert_eq!(b.instruments[1].price, expected);
        assert!((expected / 100.0 - 1.0 - (y0 / 252.0 - 0.1532)).abs() < 1e-9);
    }

    #[test]
    fn the_corporate_index_follows_the_10_year_and_re_marks_on_a_corporate_move() {
        let mut e = economy();
        let mut b = book();
        let spread = b.ig_spread;
        b.open(&e);
        // The 10-year moves and the corporate yield does not: the index
        // moves with the treasury, at the old spread.
        e.treasury_yield_10y += 0.10;
        b.sync(&e, false);
        let ig = &b.instruments[2];
        assert_eq!(b.ig_spread, spread);
        assert_eq!(ig.marked_yield, crate::units::percent_to_fraction(e.treasury_yield_10y) + spread);
        // The corporate yield moves: the spread is re-marked to it.
        e.corporate_bond_yield += 2.0;
        b.sync(&e, false);
        assert_eq!(b.ig_spread, credit_spread(&e));
        assert!((b.instruments[2].marked_yield
            - crate::units::percent_to_fraction(e.corporate_bond_yield)).abs() < 1e-15);
    }

    #[test]
    fn a_buy_moves_the_print_up_and_the_impact_decays() {
        let e = economy();
        let mut b = book();
        b.open(&e);
        let level = b.instruments[1].level;
        let flow = vec![("UST10Y".to_string(), OrderVolume { buy: 200_000.0, sell: 0.0 })];
        b.tick(at(9, 30), &e, &flow);
        let inst = &b.instruments[1];
        assert!(inst.maker_inventory < 0.0, "the maker sold");
        assert!(inst.price > level, "the print is the last fill, above the level");
        b.tick(at(9, 31), &e, &[]);
        let skewed = b.instruments[1].price;
        assert!(skewed > level, "quotes skew up after the maker went short");
        for m in 0..300 {
            b.tick(at(9 + (32 + m) / 60, (32 + m) % 60), &e, &[]);
        }
        let later = b.instruments[1].price;
        assert!(later > level && later < skewed, "the skew decays toward the level");
        assert_eq!(b.instruments[1].level, level, "flow never moves the level");
    }

    #[test]
    fn a_closed_market_does_nothing() {
        let e = economy();
        let mut b = book();
        b.open(&e);
        let before = b.clone();
        let flow = vec![("UST2Y".to_string(), OrderVolume { buy: 1e6, sell: 0.0 })];
        b.tick(at(3, 0), &e, &flow);
        assert_eq!(b, before);
    }

    #[test]
    fn a_session_prints_about_one_days_volume() {
        let e = economy();
        let mut b = book();
        b.open(&e);
        for m in 0..390 {
            b.tick(at(9 + (30 + m) / 60, (30 + m) % 60), &e, &[]);
        }
        let inst = &b.instruments[0];
        assert!((inst.volume / inst.avg_volume - 1.0).abs() < 1e-9, "{}", inst.volume);
    }

    #[test]
    fn the_book_quotes_one_cent_wide_around_a_level_near_100() {
        let e = economy();
        let mut b = book();
        b.open(&e);
        let spread_at = |i: usize, vix: f64| {
            let book = b.instruments[i].book(vix);
            assert_eq!(book.bids.len(), 10);
            assert_eq!(book.asks.len(), 10);
            book.best_ask().unwrap() - book.best_bid().unwrap()
        };
        // One cent until the half-spread before rounding reaches half a
        // cent: VIX 35 for the treasuries, 22.5 for the corporate index.
        for (i, calm_to) in [(0, 34.9), (1, 34.9), (2, 22.4)] {
            for vix in [10.0, 15.0, 20.0, calm_to] {
                assert!((spread_at(i, vix) - 0.01).abs() < 1e-9, "{i} at {vix}");
            }
            assert!((spread_at(i, 60.0) - 0.02).abs() < 1e-9, "{i} at 60");
        }
    }
}
