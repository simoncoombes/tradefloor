//! Microstructure — ported from `src/lib/engine/microstructure.ts`.
//!
//! One spread model, one book, shared by display and execution. The
//! TypeScript header explains why that matters and is not repeated here; what
//! follows is what the PORT has to be careful about.
//!
//! # Tier
//!
//! **Tier 2: no transcendentals, but RNG-bearing.** Everything up to and
//! including [`build_live_book`] is a pure function of its inputs and is held
//! to bit-identical parity. [`settle_price_through_book`] additionally draws
//! from the seeded generator, so its parity is conditional on the draw
//! schedule matching exactly — see below.
//!
//! # The draw schedule is load-bearing
//!
//! `settle_price_through_book` consumes **exactly four uniform draws, or
//! exactly zero. Never any other number.** Four when it reaches the flow
//! loop, zero when any guard returns early — and every guard sits *before*
//! the loop except the final "nothing traded" check, which runs after all
//! four draws are already spent.
//!
//! This is not an implementation detail that happens to be true. The
//! generator is a single shared stream: every company settling on every tick
//! draws from it in sequence, so a port that skipped a draw on some early
//! return, or took "up to four" draws by breaking out when a slice fills
//! nothing, would leave the stream at a different position. Every subsequent
//! company on that tick then gets different numbers, and the whole simulation
//! diverges from a difference that has nothing to do with this function's
//! own output. The loop is therefore written to run its full four iterations
//! unconditionally, and [`tests`] asserts the draw count rather than trusting
//! the shape of the code.
//!
//! Since the 2026-08 stream split, the four-or-zero consumption is the
//! REPLAY schedule: it is what a recorded reference stream actually holds,
//! and `market::SettleDrawPolicy::FourOrZero` preserves it. The engine's
//! own generated schedule pre-draws the four uniforms unconditionally
//! (`FourAlways`) and serves this function from the buffer, so the market
//! stream's position cannot depend on which guard fired — an early return
//! leaves drawn values unused rather than draws untaken. This function's
//! own contract is unchanged either way.
//!
//! # Faithfulness notes
//!
//! - **`||` is not `??`.** `baseQuoteSize` falls through a chain of `||`, so
//!   a volume of exactly zero — or a NaN — moves to the next candidate. A
//!   port using "if present" semantics would stop at a real zero and quote a
//!   one-share book. `makerInventory` and `shortInterest`, by contrast, use
//!   `??`, where a real zero must be kept. The distinction is preserved per
//!   field; see [`truthy`].
//! - **`!(x > 0)` and not `x <= 0`.** The negated form also rejects NaN, and
//!   the guards are copied in that form deliberately.

use crate::market_maker::{
    quote_ladder, LadderParams, MakerInventory, QuoteParams, MARKET_MAKER_ID,
};
use crate::mathx;
use crate::order_book::{Fill, OrderBook, Side, SubmitOptions};
use crate::rng::Rng;
use crate::types::Difficulty;

/// Levels quoted per side. Matches the depth the UI has always rendered.
pub const BOOK_LEVELS: f64 = 10.0;

/// How much inventory a maker will carry before its skew saturates, as a
/// multiple of one level's quote size.
///
/// Sets how long impact persists: a trade of N levels leaves the maker skewed
/// for roughly N/`INVENTORY_LIMIT_LEVELS` of its capacity, unwinding as
/// opposing flow arrives.
pub const INVENTORY_LIMIT_LEVELS: f64 = 12.0;

/// Order flow is split into this many child orders per tick.
const FLOW_SLICES: usize = 4;

/// How hard noise-trader flow leans toward closing a gap to fair value.
///
/// Makers quote *around* fair value, but quoting alone does not move the
/// print — someone has to lift the offer. This is what makes a stock trading
/// below fair value attract buyers.
const GAP_PRESSURE: f64 = 40.0;

/// Converts a crowd flow lean (a daily-equivalent log-price shock, bounded at
/// ±0.02 in `mispricing.ts`) into a buy-fraction tilt.
const FLOW_LEAN_TILT: f64 = 10.0;

/// The company fields this module reads, and only those.
///
/// Following `fair_value`'s convention: a narrow input struct rather than a
/// port of the whole `Company` type, and sector table values passed in rather
/// than duplicated into this crate.
///
/// `Option` mirrors JavaScript's `undefined`. Which fallback applies to each
/// one is NOT uniform — see the module note on `||` versus `??`.
#[derive(Debug, Clone, PartialEq)]
pub struct CompanyMicrostructure {
    pub id: String,
    /// `SECTOR_CONFIGS[company.sector]?.volatility`, already looked up.
    pub sector_volatility: Option<f64>,
    /// `company.stock.price`. Treated here as fair value — the book sets the
    /// price an order FILLS at, not the price itself.
    pub price: f64,
    pub market_cap: f64,
    pub beta: Option<f64>,
    pub float: Option<f64>,
    pub short_interest: Option<f64>,
    pub avg_volume: Option<f64>,
    pub volume: Option<f64>,
    pub shares_outstanding: Option<f64>,
    pub maker_inventory: Option<f64>,
}

/// JavaScript truthiness for a possibly-absent number.
///
/// `a || b` in JavaScript falls through on `0`, `-0`, `NaN` and `undefined`
/// alike. `Option::or` does not — it keeps a `Some(0.0)`. This is the
/// difference, made explicit so each call site has to choose.
fn truthy(value: Option<f64>) -> Option<f64> {
    match value {
        // `x != 0.0` is false for BOTH +0 and -0, which is what JavaScript
        // does; and NaN is falsy, which `!=` alone would not catch.
        Some(x) if x != 0.0 && !x.is_nan() => Some(x),
        _ => None,
    }
}

/// Full spread in basis points for a company.
///
/// Market-cap tier, sector volatility × beta, VIX stress, difficulty-gated
/// liquidity crisis, and short-interest widening.
/// Capitalisation, in billions, where the continuous spread curve equals
/// the step function's third tier exactly.
pub const SPREAD_SIZE_REFERENCE_B: f64 = 25.0;

/// Spread in basis points at the reference capitalisation.
pub const SPREAD_SIZE_REFERENCE_BPS: f64 = 5.0;

/// Exponent of the continuous spread curve, least-squares fitted to the
/// step function's own four tiers: 0.5B reads 29.65 bps against a step of
/// 30, 5B reads 10.40 against 10, 25B reads 5.00 against 5, and 100B reads
/// 2.66 against 3.
pub const SPREAD_SIZE_EXPONENT: f64 = 0.455;

/// Bounds on the continuous spread, in basis points. The curve is unbounded
/// as capitalisation goes to zero, and a spread of ten thousand basis points
/// is not a market.
pub const SPREAD_SIZE_BOUNDS: (f64, f64) = (1.0, 100.0);

pub fn compute_spread_bps(
    company: &CompanyMicrostructure,
    vix: f64,
    difficulty: Option<Difficulty>,
) -> f64 {
    compute_spread_bps_with(company, vix, difficulty, 0.0, SPREAD_SIZE_EXPONENT)
}

/// Full spread in basis points, with the size curve selectable.
///
/// # The tiers are a cliff, and this is how it gets removed
///
/// The step function charges 10 bps at $1B and 30 bps at $0.9B -- a THREE
/// TIMES jump in transaction cost from a rounding error in market
/// capitalisation. Every execution study run across that boundary is
/// measuring an artifact of the tier edge rather than a property of size,
/// and a strategy that happens to trade names near $1B pays a cost that
/// does not exist.
///
/// Real spreads scale continuously with size. The power law fitted here
/// reproduces all four tiers closely (see [`SPREAD_SIZE_EXPONENT`]) while
/// removing the cliffs between them.
///
/// At smoothness 0.0 this returns the stepped value by branch, so every
/// preset before pt-v4 is bit-identical.
pub fn compute_spread_bps_with(
    company: &CompanyMicrostructure,
    vix: f64,
    difficulty: Option<Difficulty>,
    size_smoothness: f64,
    size_exponent: f64,
) -> f64 {
    let mcap_billions = company.market_cap / 1e9;
    let stepped = if mcap_billions > 50.0 {
        3.0
    } else if mcap_billions > 10.0 {
        5.0
    } else if mcap_billions > 1.0 {
        10.0
    } else {
        30.0
    };
    let base_bps = if size_smoothness == 0.0 || mcap_billions <= 0.0 {
        stepped
    } else {
        let ratio = mcap_billions / SPREAD_SIZE_REFERENCE_B;
        let (lo, hi) = SPREAD_SIZE_BOUNDS;
        let smooth = mathx::clamp(
            SPREAD_SIZE_REFERENCE_BPS * mathx::pow(ratio, -size_exponent), lo, hi);
        (1.0 - size_smoothness) * stepped + size_smoothness * smooth
    };

    // `?? 1.0`, so a genuine zero volatility or beta is kept as zero.
    let sector_vol = company.sector_volatility.unwrap_or(1.0);
    let beta = company.beta.unwrap_or(1.0);
    let vol_multiplier = 0.7 + 0.3 * sector_vol * beta;

    let mut vix_multiplier = 1.0 + mathx::max(0.0, (vix - 15.0) / 30.0);
    // The TypeScript is `if (difficulty === 'hard' && vix > 25) … else if
    // (difficulty === 'expert' && vix > 25)`. A hard run below VIX 25 falls
    // through BOTH arms, which the guards reproduce.
    //
    // The `vix > 25` guards are in fact REDUNDANT with the `max(0, …)` inside
    // each arm — below the threshold the bracket is negative, clamps to zero,
    // and multiplies by 1. Mutation-tested: deleting a guard changes no
    // output. They are kept because the original has them and because the
    // redundancy is not obvious, but nothing downstream depends on it, so
    // this is the one place in the module where the two spellings are
    // genuinely interchangeable.
    match difficulty {
        Some(Difficulty::Hard) if vix > 25.0 => {
            vix_multiplier *= 1.0 + mathx::max(0.0, (vix - 25.0) * 0.05);
        }
        Some(Difficulty::Expert) if vix > 25.0 => {
            vix_multiplier *= 1.0 + mathx::max(0.0, (vix - 25.0) * 0.1);
        }
        _ => {}
    }

    // Heavily-shorted names are harder to make a market in: borrow is scarce,
    // so makers demand more edge.
    //
    // `float || 1` is TRUTHY-or (a zero float becomes 1), then `Math.max(1, …)`
    // on top. The two are not redundant: the `||` catches zero and NaN, the
    // `max` catches a negative.
    let float = mathx::max(1.0, truthy(company.float).unwrap_or(1.0));
    let short_interest_ratio = company.short_interest.unwrap_or(0.0) / float;
    let short_spread_mult = if short_interest_ratio > 0.15 {
        1.0 + (short_interest_ratio - 0.15) * 2.0
    } else {
        1.0
    };

    base_bps * vol_multiplier * vix_multiplier * short_spread_mult
}

/// Baseline quote size per level — the legacy display-book size, minus the RNG.
pub fn base_quote_size(company: &CompanyMicrostructure) -> f64 {
    // A `||` chain: zero and NaN fall through to the next candidate. Note the
    // third rung is an EXPRESSION — an absent `sharesOutstanding` makes
    // `undefined * 0.005` NaN, which is falsy, so it lands on 100.
    let avg_vol = truthy(company.avg_volume)
        .or_else(|| truthy(company.volume))
        .or_else(|| truthy(company.shares_outstanding.map(|s| s * 0.005)))
        .unwrap_or(100.0);
    mathx::max(1.0, (avg_vol / 100.0).floor())
}

/// A resting player/AI limit order seeded into the book alongside maker
/// liquidity, giving genuine queue position.
#[derive(Debug, Clone, PartialEq)]
pub struct RestingOrder {
    pub id: String,
    pub side: Side,
    pub price: f64,
    pub quantity: f64,
    pub owner_id: String,
}

/// Options for [`build_live_book`]. `Default` carries the TypeScript
/// destructuring defaults (`vix = 15`, `levels = BOOK_LEVELS`).
#[derive(Debug, Clone)]
pub struct LiveBookOptions {
    pub vix: f64,
    pub difficulty: Option<Difficulty>,
    /// Blend from the four-tier spread step toward a continuous power law,
    /// in [0, 1]. 0.0 is the step and is bit-identical.
    pub spread_size_smoothness: f64,
    /// Exponent of the continuous spread curve. Only read when the
    /// smoothness is non-zero.
    pub spread_size_exponent: f64,
    /// A JS `number`, not an integer — see `market_maker::LadderParams::levels`.
    pub levels: f64,
    pub resting_orders: Vec<RestingOrder>,
}

impl Default for LiveBookOptions {
    fn default() -> Self {
        Self {
            vix: 15.0,
            difficulty: None,
            spread_size_smoothness: 0.0,
            spread_size_exponent: SPREAD_SIZE_EXPONENT,
            levels: BOOK_LEVELS,
            resting_orders: Vec::new(),
        }
    }
}

/// Build the executable book for a company at this instant.
///
/// Rebuilt per call rather than persisted: a maker re-quotes every tick
/// anyway, so the book is a pure function of (fair value, spread, resting
/// orders). That keeps the tick pure and replay deterministic for free.
pub fn build_live_book(company: &CompanyMicrostructure, options: &LiveBookOptions) -> OrderBook {
    let fair_value = company.price;
    let mut book = OrderBook::new(company.id.clone(), Some(company.price));

    // Negated comparison, so a NaN price returns an empty book rather than
    // falling through to quote against it.
    if !(fair_value > 0.0) {
        return book;
    }

    let spread_bps = compute_spread_bps_with(
        company, options.vix, options.difficulty,
        options.spread_size_smoothness, options.spread_size_exponent);
    let base_size = base_quote_size(company);
    let (bids, asks) = quote_ladder(&LadderParams {
        quote: QuoteParams {
            fair_value,
            // `computeSpreadBps` returns the FULL spread; the maker takes a half.
            half_spread_bps: spread_bps / 2.0,
            base_size,
            inventory: MakerInventory {
                // `?? 0` — a real zero inventory is a zero, not "absent".
                position: company.maker_inventory.unwrap_or(0.0),
                limit: mathx::max(1.0, base_size * INVENTORY_LIMIT_LEVELS),
            },
            // The TypeScript passes neither `volatilityMultiplier` nor
            // `maxSkew`, so both take their destructuring default of 1.
            ..QuoteParams::default()
        },
        levels: options.levels,
        // `levelStep` is likewise omitted upstream, defaulting to 0.5.
        level_step: 0.5,
    });

    // `quote_ladder` emits both sides best-first, so append straight on
    // rather than paying the insertion scan per level.
    for level in &bids {
        book.append_maker_level(Side::Buy, level.price, level.size, MARKET_MAKER_ID);
    }
    for level in &asks {
        book.append_maker_level(Side::Sell, level.price, level.size, MARKET_MAKER_ID);
    }

    // Player/AI orders join the same queue as maker liquidity and are ranked
    // purely on price-time priority, exactly like a real venue.
    for o in &options.resting_orders {
        book.post_limit(o.side, o.price, o.quantity, &o.owner_id, Some(o.id.clone()));
    }

    book
}

// ── Price discovery through the book ───────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct SettlementResult {
    /// Printed price — the last trade, or fair value if nothing traded.
    pub price: f64,
    /// Shares actually matched.
    pub traded_volume: f64,
    /// True when at least one trade printed.
    pub traded: bool,
    /// Change in maker inventory from this tick's fills. Returned rather than
    /// applied, so this stays a pure function of its inputs and the generator.
    pub maker_inventory_delta: f64,
}

/// Options for [`settle_price_through_book`].
#[derive(Debug, Clone, Copy)]
pub struct SettleOptions {
    pub vix: f64,
    pub difficulty: Option<Difficulty>,
    /// Crowd flow lean: the bounded daily-equivalent log shock from
    /// `mispricing.ts::crowdLean`. `None` means no crowd, matching legacy
    /// callers where behaviour is unchanged.
    pub flow_lean: Option<f64>,
    /// The size-curve settings, carried so a settlement quotes against the
    /// same spread the caller asked for rather than a defaulted one.
    pub spread_size_smoothness: f64,
    pub spread_size_exponent: f64,
}

impl Default for SettleOptions {
    fn default() -> Self {
        Self {
            vix: 15.0,
            difficulty: None,
            flow_lean: None,
            spread_size_smoothness: 0.0,
            spread_size_exponent: SPREAD_SIZE_EXPONENT,
        }
    }
}

/// Net inventory change for the market maker across a set of fills.
///
/// The maker sits opposite the taker: a taker BUY means the maker sold, so
/// its position falls. Fills against other participants do not touch it.
pub fn maker_delta_from_fills(fills: &[Fill]) -> f64 {
    // Folded explicitly from `0.0`, not `.sum()`, for the same reason
    // `order_book::depth` is: `Sum for f64` starts from `-0.0`, so an empty
    // or fully-skipped set would return a negative zero the TypeScript never
    // produces.
    let mut delta = 0.0;
    for f in fills {
        if f.maker_id != MARKET_MAKER_ID {
            continue;
        }
        delta += if f.taker_side == Side::Buy {
            -f.quantity
        } else {
            f.quantity
        };
    }
    delta
}

/// Settle a tick's price through the order book.
///
/// The price model supplies `fair_value`; this decides what actually
/// PRINTED. Price can now sit a half-spread either side of fair value and
/// bounce between bid and offer — real bid-ask bounce rather than a smooth
/// analytic path.
///
/// Consumes exactly four uniform draws from `rng`, or exactly zero. See the
/// module documentation for why that is a hard contract and not an incidental
/// property.
pub fn settle_price_through_book(
    company: &CompanyMicrostructure,
    fair_value: f64,
    tick_volume: f64,
    options: &SettleOptions,
    rng: &mut impl Rng,
) -> SettlementResult {
    let last_price = company.price;

    let no_trade = SettlementResult {
        price: fair_value,
        traded_volume: 0.0,
        traded: false,
        maker_inventory_delta: 0.0,
    };

    // Guard 1 — before any draw.
    if !(fair_value > 0.0) || !(last_price > 0.0) || !(tick_volume > 0.0) {
        return no_trade;
    }

    // Build only the depth this tick's flow can actually reach. A tick prints
    // roughly avgVolume/390 while one level quotes avgVolume/100, so ordinary
    // flow never leaves the top level or two — quoting all ten was ~4x the
    // settlement cost for depth nothing touches.
    let level_size = mathx::max(1.0, base_quote_size(company));
    // Stays f64 all the way into the ladder. Narrowing here would be wrong:
    // an infinite `tick_volume` AND an infinite `level_size` make this NaN,
    // which must yield an EMPTY book and an early return costing zero draws.
    // Any integer cast turns that NaN into one quoted level and four draws.
    let levels_needed = mathx::min(
        BOOK_LEVELS,
        mathx::max(2.0, (tick_volume / level_size).ceil() + 1.0),
    );
    let mut book = build_live_book(
        company,
        &LiveBookOptions {
            vix: options.vix,
            difficulty: options.difficulty,
            // Propagated, not defaulted: this inner book must price with the
            // same size curve as the outer call, or a settlement would quote
            // against a spread the caller did not ask for.
            spread_size_smoothness: options.spread_size_smoothness,
            spread_size_exponent: options.spread_size_exponent,
            levels: levels_needed,
            resting_orders: Vec::new(),
        },
    );

    // Guard 2 — still before any draw.
    if book.asks.is_empty() || book.bids.is_empty() {
        return no_trade;
    }

    // Lean the flow toward whichever side closes the gap to fair value, plus
    // whichever side the crowd is leaning.
    let gap = (fair_value - last_price) / last_price;
    let buy_fraction = mathx::max(
        0.05,
        mathx::min(
            0.95,
            0.5 + gap * GAP_PRESSURE + options.flow_lean.unwrap_or(0.0) * FLOW_LEAN_TILT,
        ),
    );

    let slice = mathx::max(1.0, (tick_volume / FLOW_SLICES as f64).floor());
    let mut traded = 0.0;
    let mut maker_inventory_delta = 0.0;

    // Exactly FLOW_SLICES iterations, unconditionally. There is deliberately
    // no early break: a slice that fills nothing must still cost its draw.
    for _ in 0..FLOW_SLICES {
        let side = if rng.next_f64() < buy_fraction {
            Side::Buy
        } else {
            Side::Sell
        };
        let result = book.submit(
            side,
            slice,
            "flow",
            SubmitOptions {
                limit_price: None,
                post_remainder: false,
                order_id: None,
            },
        );
        for f in &result.fills {
            traded += f.quantity;
        }
        maker_inventory_delta += maker_delta_from_fills(&result.fills);
    }

    // Guard 3 — the only one AFTER the draws, so it still costs four.
    match book.last_price {
        Some(price) if traded != 0.0 => SettlementResult {
            price,
            traded_volume: traded,
            traded: true,
            maker_inventory_delta,
        },
        _ => no_trade,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rng::GameRng;

    fn company() -> CompanyMicrostructure {
        CompanyMicrostructure {
            id: "ACME".to_string(),
            sector_volatility: Some(1.0),
            price: 100.0,
            market_cap: 20e9,
            beta: Some(1.0),
            float: Some(1e8),
            short_interest: Some(0.0),
            avg_volume: Some(1e6),
            volume: Some(1e6),
            shares_outstanding: Some(1e8),
            maker_inventory: Some(0.0),
        }
    }

    // ── The `||` versus `??` distinction ──────────────────────────────────

    #[test]
    fn a_zero_average_volume_falls_through_rather_than_quoting_one_share() {
        let mut c = company();
        c.avg_volume = Some(0.0);
        c.volume = Some(500_000.0);
        // `||` semantics: the zero is skipped and `volume` is used.
        assert_eq!(base_quote_size(&c), 5000.0);
    }

    #[test]
    fn a_nan_volume_also_falls_through() {
        let mut c = company();
        c.avg_volume = Some(f64::NAN);
        c.volume = Some(500_000.0);
        assert_eq!(base_quote_size(&c), 5000.0);
    }

    #[test]
    fn an_absent_shares_outstanding_lands_on_the_hundred_default() {
        let mut c = company();
        c.avg_volume = None;
        c.volume = None;
        c.shares_outstanding = None;
        // undefined * 0.005 is NaN, which is falsy, so the chain reaches 100.
        // Then max(1, floor(100/100)) = 1.
        assert_eq!(base_quote_size(&c), 1.0);
    }

    #[test]
    fn a_zero_maker_inventory_is_kept_as_zero_not_treated_as_absent() {
        let mut c = company();
        c.maker_inventory = Some(0.0);
        let flat = build_live_book(&c, &LiveBookOptions::default());
        c.maker_inventory = None;
        let absent = build_live_book(&c, &LiveBookOptions::default());
        // `??` semantics: both mean zero here, so the books must agree.
        assert_eq!(flat.bids[0].price, absent.bids[0].price);
    }

    // ── Spread model ──────────────────────────────────────────────────────

    #[test]
    fn spread_tightens_with_market_cap() {
        let mut c = company();
        let mut last = f64::INFINITY;
        for mcap in [0.5e9, 5e9, 20e9, 100e9] {
            c.market_cap = mcap;
            let bps = compute_spread_bps(&c, 15.0, None);
            assert!(
                bps < last,
                "spread should tighten as cap rises, got {bps} after {last}"
            );
            last = bps;
        }
    }

    #[test]
    fn vix_stress_widens_the_spread() {
        let c = company();
        assert!(compute_spread_bps(&c, 40.0, None) > compute_spread_bps(&c, 15.0, None));
    }

    #[test]
    fn expert_punishes_a_vix_spike_harder_than_hard() {
        let c = company();
        let normal = compute_spread_bps(&c, 40.0, Some(Difficulty::Normal));
        let hard = compute_spread_bps(&c, 40.0, Some(Difficulty::Hard));
        let expert = compute_spread_bps(&c, 40.0, Some(Difficulty::Expert));
        assert!(expert > hard && hard > normal);
    }

    #[test]
    fn a_hard_run_below_the_vix_threshold_falls_through_both_arms() {
        let c = company();
        // vix <= 25 fails the `hard` guard, and `expert` cannot match a hard
        // difficulty, so the result must equal the no-difficulty case.
        assert_eq!(
            compute_spread_bps(&c, 20.0, Some(Difficulty::Hard)),
            compute_spread_bps(&c, 20.0, None)
        );
    }

    #[test]
    fn heavy_short_interest_widens_the_spread_past_the_threshold() {
        let mut c = company();
        c.float = Some(100.0);
        c.short_interest = Some(10.0); // 10% — below the 15% threshold
        let light = compute_spread_bps(&c, 15.0, None);
        c.short_interest = Some(50.0); // 50%
        assert!(compute_spread_bps(&c, 15.0, None) > light);
    }

    #[test]
    fn a_zero_float_does_not_divide_by_zero() {
        let mut c = company();
        c.float = Some(0.0);
        c.short_interest = Some(1000.0);
        assert!(compute_spread_bps(&c, 15.0, None).is_finite());
    }

    // ── Book construction ─────────────────────────────────────────────────

    #[test]
    fn a_non_positive_price_yields_an_empty_book() {
        for price in [0.0, -10.0, f64::NAN] {
            let mut c = company();
            c.price = price;
            let book = build_live_book(&c, &LiveBookOptions::default());
            assert!(
                book.bids.is_empty() && book.asks.is_empty(),
                "price {price}"
            );
        }
    }

    #[test]
    fn the_book_is_two_sided_and_uncrossed() {
        let book = build_live_book(&company(), &LiveBookOptions::default());
        assert_eq!(book.bids.len(), BOOK_LEVELS as usize);
        assert_eq!(book.asks.len(), BOOK_LEVELS as usize);
        assert!(book.best_bid().unwrap() < book.best_ask().unwrap());
    }

    #[test]
    fn a_resting_order_inside_the_spread_takes_queue_priority() {
        let c = company();
        let maker_only = build_live_book(&c, &LiveBookOptions::default());
        let best_maker_bid = maker_only.best_bid().unwrap();

        let book = build_live_book(
            &c,
            &LiveBookOptions {
                resting_orders: vec![RestingOrder {
                    id: "player-1".to_string(),
                    side: Side::Buy,
                    price: best_maker_bid + 0.05,
                    quantity: 100.0,
                    owner_id: "player".to_string(),
                }],
                ..Default::default()
            },
        );
        assert_eq!(book.bids[0].owner_id, "player");
    }

    // ── Settlement, and the draw schedule ─────────────────────────────────

    /// Counts draws by advancing a reference generator to the same point.
    fn draws_consumed(before: &GameRng, after: &GameRng) -> usize {
        let mut probe = before.clone();
        for n in 0..64 {
            if probe == *after {
                return n;
            }
            probe.next_f64();
        }
        panic!("generator advanced more than 64 draws");
    }

    #[test]
    fn settlement_consumes_exactly_four_draws() {
        let mut rng = GameRng::from_seed(42);
        let before = rng.clone();
        let out = settle_price_through_book(
            &company(),
            100.0,
            10_000.0,
            &SettleOptions::default(),
            &mut rng,
        );
        assert!(out.traded);
        assert_eq!(draws_consumed(&before, &rng), 4);
    }

    #[test]
    fn every_early_return_consumes_exactly_zero_draws() {
        // Each of these must bail BEFORE the flow loop. If a future edit
        // moves a guard below the draws, this catches it — and a drifted
        // stream position is otherwise invisible until the whole simulation
        // has diverged.
        let cases: Vec<(&str, CompanyMicrostructure, f64, f64)> = vec![
            ("fair value zero", company(), 0.0, 10_000.0),
            ("fair value NaN", company(), f64::NAN, 10_000.0),
            ("tick volume zero", company(), 100.0, 0.0),
            ("tick volume NaN", company(), 100.0, f64::NAN),
            (
                "last price zero",
                CompanyMicrostructure {
                    price: 0.0,
                    ..company()
                },
                100.0,
                10_000.0,
            ),
        ];

        for (note, c, fair_value, tick_volume) in cases {
            let mut rng = GameRng::from_seed(42);
            let before = rng.clone();
            let out = settle_price_through_book(
                &c,
                fair_value,
                tick_volume,
                &SettleOptions::default(),
                &mut rng,
            );
            assert!(!out.traded, "{note}: should not trade");
            assert_eq!(draws_consumed(&before, &rng), 0, "{note}: should not draw");
        }
    }

    #[test]
    fn the_flow_loop_never_breaks_early_even_when_slices_fill_nothing() {
        // A book so thin relative to the slice that later slices find an
        // empty side. All four draws must still be spent.
        let mut c = company();
        c.avg_volume = Some(200.0); // one level quotes 2 shares
        let mut rng = GameRng::from_seed(7);
        let before = rng.clone();
        settle_price_through_book(&c, 100.0, 100_000.0, &SettleOptions::default(), &mut rng);
        assert_eq!(draws_consumed(&before, &rng), 4);
    }

    #[test]
    fn settlement_is_reproducible_from_the_same_seed() {
        let run = || {
            let mut rng = GameRng::from_seed(99);
            settle_price_through_book(
                &company(),
                101.0,
                10_000.0,
                &SettleOptions::default(),
                &mut rng,
            )
        };
        assert_eq!(run(), run());
    }

    #[test]
    fn a_gap_to_fair_value_biases_flow_toward_closing_it() {
        // Fair value well above the last price should draw buyers, so the
        // print lands above where a symmetric flow would leave it.
        let c = company();
        let mut up = GameRng::from_seed(5);
        let above =
            settle_price_through_book(&c, 110.0, 50_000.0, &SettleOptions::default(), &mut up);
        let mut down = GameRng::from_seed(5);
        let below =
            settle_price_through_book(&c, 90.0, 50_000.0, &SettleOptions::default(), &mut down);
        assert!(
            above.price > below.price,
            "gap pressure should separate the prints: {} vs {}",
            above.price,
            below.price
        );
    }

    #[test]
    fn a_taker_buy_reduces_maker_inventory() {
        // The directional claim, tested where it is exact rather than
        // through four coin flips.
        let fill = |taker_side| Fill {
            price: 100.0,
            quantity: 50.0,
            maker_order_id: "x".to_string(),
            maker_id: MARKET_MAKER_ID.to_string(),
            taker_id: "flow".to_string(),
            taker_side,
        };
        assert_eq!(maker_delta_from_fills(&[fill(Side::Buy)]), -50.0);
        assert_eq!(maker_delta_from_fills(&[fill(Side::Sell)]), 50.0);
    }

    #[test]
    fn buy_leaning_flow_drains_maker_inventory_and_sell_leaning_builds_it() {
        // Deliberately NOT a single seed. `buy_fraction` saturates at 0.95,
        // so any individual four-draw run can still come out the other way
        // — an assertion on one seed would be asserting a coin flip, and
        // would pass or fail on which seed happened to be typed.
        let mean_delta = |fair_value: f64, flow_lean: f64| {
            let mut total = 0.0;
            for seed in 0..300u32 {
                let mut rng = GameRng::from_seed(seed);
                total += settle_price_through_book(
                    &company(),
                    fair_value,
                    50_000.0,
                    &SettleOptions {
                        spread_size_smoothness: 0.0,
                        spread_size_exponent: SPREAD_SIZE_EXPONENT,
                        flow_lean: Some(flow_lean),
                        ..SettleOptions::default()
                    },
                    &mut rng,
                )
                .maker_inventory_delta;
            }
            total / 300.0
        };

        // Undervalued plus a bullish crowd pins buy_fraction at its 0.95 cap.
        let buying = mean_delta(101.0, 0.02);
        // Overvalued plus a bearish crowd pins it at the 0.05 floor.
        let selling = mean_delta(99.0, -0.02);

        assert!(
            buying < 0.0,
            "maker sells into buying flow, mean delta {buying}"
        );
        assert!(
            selling > 0.0,
            "maker buys from selling flow, mean delta {selling}"
        );
    }

    #[test]
    fn fills_against_other_participants_do_not_touch_maker_inventory() {
        let fills = vec![Fill {
            price: 100.0,
            quantity: 50.0,
            maker_order_id: "x".to_string(),
            maker_id: "player".to_string(),
            taker_id: "flow".to_string(),
            taker_side: Side::Buy,
        }];
        assert_eq!(maker_delta_from_fills(&fills), 0.0);
    }

    #[test]
    fn an_empty_fill_set_yields_positive_zero() {
        // `.sum()` would give -0.0 here; the TypeScript gives +0.
        assert_eq!(maker_delta_from_fills(&[]).to_bits(), 0.0f64.to_bits());
    }
}
