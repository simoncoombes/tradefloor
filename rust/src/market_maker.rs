//! Market-maker quoting — ported from the reference implementation's
//! market-maker module.
//!
//! **Tier 1: zero transcendentals, zero RNG, zero imports.** Every operation
//! is exactly specified by IEEE-754, so this is held to bit-identical parity
//! and its tests assert rather than report.
//!
//! # What the module is for
//!
//! Inventory skew is the load-bearing idea. A maker that is long wants to
//! sell, so it shifts BOTH quotes down: its ask becomes more attractive to
//! buyers and its bid less attractive to sellers. That makes the maker
//! mean-reverting in inventory rather than accumulating an unbounded
//! position, and it is why the book absorbs one-sided flow without running
//! dry.
//!
//! The ladder is deterministic on purpose. The legacy display book randomised
//! level sizes, so the depth a player saw was never the depth they traded
//! against. Same shape, no randomness — displayed depth IS executable depth.
//!
//! # Faithfulness notes
//!
//! - **Prices round to cents** via `round2` on every quote and every ladder
//!   level. Exchanges quote on a tick grid, and rounding keeps prices there;
//!   it also means the port must round at exactly the same points, not once
//!   at the end.
//! - **Sizes floor, with a minimum of 1.** `Math.floor` then `Math.max(1, …)`,
//!   in that order — flooring a sub-1 size to 0 and then raising it is not
//!   the same as raising then flooring.
//! - **The ask is floored to `bidPrice + 0.01`**, not to fair value. A
//!   crossed or zero-width quote is never valid, and this is what prevents
//!   one when a collapsing fair value drives the bid into the floor.

use crate::mathx;
use crate::mathx::clamp;

/// The owner id every maker order carries.
pub const MARKET_MAKER_ID: &str = "mm";

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MakerQuote {
    pub bid_price: f64,
    pub ask_price: f64,
    pub bid_size: f64,
    pub ask_size: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct MakerInventory {
    /// Net shares held. Positive = long, negative = short.
    pub position: f64,
    /// Soft cap. Skew scales with `position / limit` and saturates at ±1.
    pub limit: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct QuoteParams {
    /// Fair value from the factor model.
    pub fair_value: f64,
    /// Half-spread in basis points, before widening.
    pub half_spread_bps: f64,
    /// Baseline quote size per side, in shares.
    pub base_size: f64,
    pub inventory: MakerInventory,
    /// Volatility multiplier, typically VIX-derived. Makers widen when risk
    /// rises — the same reason real spreads blow out in a crisis.
    /// Reference default: 1.
    pub volatility_multiplier: f64,
    /// Maximum skew as a fraction of the half-spread. At 1.0 a fully-loaded
    /// maker shifts its mid by an entire half-spread, putting one side at
    /// fair value and pushing the other away. Reference default: 1.
    pub max_skew: f64,
}

impl Default for QuoteParams {
    fn default() -> Self {
        Self {
            fair_value: 0.0,
            half_spread_bps: 0.0,
            base_size: 0.0,
            inventory: MakerInventory {
                position: 0.0,
                limit: 0.0,
            },
            // Mirrors the reference implementation's destructuring defaults
            // (`volatilityMultiplier = 1`, `maxSkew = 1`).
            volatility_multiplier: 1.0,
            max_skew: 1.0,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LadderLevel {
    pub price: f64,
    pub size: f64,
}

#[derive(Debug, Clone, Copy)]
pub struct LadderParams {
    pub quote: QuoteParams,
    /// Number of price levels to quote per side.
    ///
    /// `f64` and not an integer type, because the reference implementation's field is a
    /// plain `number` and the loop is `for (i = 0; i < Math.max(1, levels))`.
    /// Two behaviours depend on that and are lost by narrowing to an integer:
    /// `2.5` yields THREE levels, and `NaN` yields ZERO — where any integer
    /// cast turns NaN into 0 and the `max(1)` then floors it back up to one.
    /// The NaN case is reachable from `microstructure::settle_price_through_book`,
    /// where it decides whether the book is empty, and therefore whether the
    /// function consumes four draws or none.
    pub levels: f64,
    /// Gap between successive levels, as a fraction of the half-spread. The
    /// legacy display book used 0.5, preserved so quoted depth keeps the
    /// shape it has always had.
    pub level_step: f64,
}

/// Exchanges quote in cents; keeping prices on the tick grid avoids drift.
fn round2(n: f64) -> f64 {
    mathx::js_round(n * 100.0) / 100.0
}

/// Produce a two-sided quote around fair value.
pub fn compute_quote(params: &QuoteParams) -> MakerQuote {
    let half_spread = params.fair_value
        * (params.half_spread_bps / 10_000.0)
        * mathx::max(0.1, params.volatility_multiplier);

    // Saturating inventory pressure in [-1, 1].
    let load = if params.inventory.limit > 0.0 {
        clamp(
            params.inventory.position / params.inventory.limit,
            -1.0,
            1.0,
        )
    } else {
        0.0
    };

    // Long inventory (load > 0) pushes the quoted mid DOWN.
    let skew = -load * half_spread * clamp(params.max_skew, 0.0, 1.0);
    let mid = params.fair_value + skew;

    // Size asymmetry reinforces the skew: quote more on the side that reduces
    // the position, less on the side that would grow it.
    let size_tilt = clamp(1.0 - load.abs(), 0.15, 1.0);
    let bid_size = if load > 0.0 {
        params.base_size * size_tilt
    } else {
        params.base_size
    };
    let ask_size = if load < 0.0 {
        params.base_size * size_tilt
    } else {
        params.base_size
    };

    // Floor the bid above zero — a non-positive quote is never valid, and a
    // crashing fair value must not produce one.
    let bid_price = mathx::max(0.01, mid - half_spread);
    let ask_price = mathx::max(bid_price + 0.01, mid + half_spread);

    MakerQuote {
        bid_price: round2(bid_price),
        ask_price: round2(ask_price),
        bid_size: mathx::max(1.0, bid_size.floor()),
        ask_size: mathx::max(1.0, ask_size.floor()),
    }
}

/// Apply a fill to maker inventory.
///
/// The maker is on the opposite side of the taker: a taker BUY means the
/// maker sold, so the maker's position falls.
pub fn apply_fill_to_inventory(
    inventory: MakerInventory,
    taker_side: crate::order_book::Side,
    quantity: f64,
) -> MakerInventory {
    let delta = match taker_side {
        crate::order_book::Side::Buy => -quantity,
        crate::order_book::Side::Sell => quantity,
    };
    MakerInventory {
        position: inventory.position + delta,
        ..inventory
    }
}

/// Quote a multi-level ladder rather than a single top-of-book pair.
///
/// Depth thins away from the touch by `1 / (1 + i * 0.3)`, matching the shape
/// the display book has always drawn. This is what makes a large order pay
/// progressively worse prices — impact becomes a property of resting
/// liquidity rather than a coefficient.
pub fn quote_ladder(params: &LadderParams) -> (Vec<LadderLevel>, Vec<LadderLevel>) {
    let top = compute_quote(&params.quote);
    let half_spread = (top.ask_price - top.bid_price) / 2.0;
    let step = mathx::max(0.01, half_spread * 2.0 * params.level_step);

    let mut bids = Vec::new();
    let mut asks = Vec::new();
    // `Math.max(1, levels)`, then a `<` comparison per iteration. NaN makes
    // the comparison false immediately, giving zero levels — a `for` over an
    // integer range could not express that. An infinite `levels` loops
    // forever, exactly as the original does, and it is left faithful rather
    // than guarded here.
    //
    // That used to be unreachable. It no longer is: the depth counterfactual
    // passes `f64::INFINITY` as `SettleOptions::depth_multiplier` on every
    // open tick, and the only thing between that and this loop is the
    // `min(BOOK_LEVELS, ...)` in `microstructure::settle_price_through_book`.
    // Removing that cap does not fail a test, it aborts the process on a 34
    // GB allocation, so the cap carries an assertion beside it now.
    let n = mathx::max(1.0, params.levels);

    let mut i = 0i64;
    while (i as f64) < n {
        let depth_factor = 1.0 / (1.0 + i as f64 * 0.3);
        let bid_price = round2(top.bid_price - i as f64 * step);
        let ask_price = round2(top.ask_price + i as f64 * step);

        // Note the asymmetry: bids are dropped once they reach zero, asks
        // never are. Faithful to the original — the ask side has no
        // equivalent guard because it only ever walks upward.
        if bid_price > 0.0 {
            bids.push(LadderLevel {
                price: bid_price,
                size: mathx::max(1.0, (top.bid_size * depth_factor).floor()),
            });
        }
        asks.push(LadderLevel {
            price: ask_price,
            size: mathx::max(1.0, (top.ask_size * depth_factor).floor()),
        });
        i += 1;
    }
    (bids, asks)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::order_book::Side;

    fn base() -> QuoteParams {
        QuoteParams {
            fair_value: 100.0,
            half_spread_bps: 10.0,
            base_size: 500.0,
            inventory: MakerInventory {
                position: 0.0,
                limit: 1000.0,
            },
            ..Default::default()
        }
    }

    #[test]
    fn a_flat_maker_quotes_symmetrically_around_fair_value() {
        let q = compute_quote(&base());
        assert_eq!(q.bid_price, 99.9);
        assert_eq!(q.ask_price, 100.1);
        assert_eq!(q.bid_size, 500.0);
        assert_eq!(q.ask_size, 500.0);
    }

    /// The core behaviour: long inventory pushes BOTH quotes down.
    #[test]
    fn long_inventory_skews_the_quote_down() {
        let mut p = base();
        p.inventory.position = 1000.0; // fully loaded long
        let q = compute_quote(&p);
        let flat = compute_quote(&base());
        assert!(q.bid_price < flat.bid_price, "bid must fall");
        assert!(q.ask_price < flat.ask_price, "ask must fall too");
    }

    #[test]
    fn short_inventory_skews_the_quote_up() {
        let mut p = base();
        p.inventory.position = -1000.0;
        let q = compute_quote(&p);
        let flat = compute_quote(&base());
        assert!(q.bid_price > flat.bid_price);
        assert!(q.ask_price > flat.ask_price);
    }

    /// Size tilt reduces the side that would grow the position.
    #[test]
    fn size_tilts_against_growing_the_position() {
        let mut p = base();
        p.inventory.position = 500.0; // half loaded long
        let q = compute_quote(&p);
        assert!(
            q.bid_size < q.ask_size,
            "long maker should bid smaller than it offers"
        );
    }

    /// Saturation: beyond the limit, nothing gets worse and quotes never invert.
    #[test]
    fn over_limit_inventory_saturates_rather_than_inverting() {
        let mut at = base();
        at.inventory.position = 1000.0;
        let mut way_over = base();
        way_over.inventory.position = 50_000.0;
        assert_eq!(compute_quote(&at), compute_quote(&way_over));
        let q = compute_quote(&way_over);
        assert!(q.ask_price > q.bid_price, "quotes must never cross");
    }

    #[test]
    fn a_zero_limit_disables_skew_rather_than_dividing_by_zero() {
        let mut p = base();
        p.inventory = MakerInventory {
            position: 900.0,
            limit: 0.0,
        };
        let q = compute_quote(&p);
        assert_eq!(q, compute_quote(&base()), "no limit means no skew");
    }

    /// A collapsing fair value must not produce a non-positive or crossed quote.
    #[test]
    fn a_collapsing_fair_value_still_yields_a_valid_quote() {
        let mut p = base();
        p.fair_value = 0.001;
        let q = compute_quote(&p);
        assert!(q.bid_price >= 0.01);
        assert!(q.ask_price >= q.bid_price + 0.01 - 1e-9);
    }

    #[test]
    fn volatility_widens_the_spread() {
        let mut p = base();
        p.volatility_multiplier = 3.0;
        let wide = compute_quote(&p);
        let normal = compute_quote(&base());
        assert!(wide.ask_price - wide.bid_price > normal.ask_price - normal.bid_price);
    }

    /// The multiplier floors at 0.1 — spreads narrow but never vanish.
    #[test]
    fn the_volatility_multiplier_floors_at_a_tenth() {
        let mut tiny = base();
        tiny.volatility_multiplier = 0.0001;
        let mut floored = base();
        floored.volatility_multiplier = 0.1;
        assert_eq!(compute_quote(&tiny), compute_quote(&floored));
    }

    #[test]
    fn a_taker_buy_reduces_maker_inventory() {
        let inv = MakerInventory {
            position: 0.0,
            limit: 1000.0,
        };
        assert_eq!(
            apply_fill_to_inventory(inv, Side::Buy, 100.0).position,
            -100.0
        );
        assert_eq!(
            apply_fill_to_inventory(inv, Side::Sell, 100.0).position,
            100.0
        );
    }

    #[test]
    fn the_ladder_thins_and_walks_away_from_the_touch() {
        let (bids, asks) = quote_ladder(&LadderParams {
            quote: base(),
            levels: 5.0,
            level_step: 0.5,
        });
        assert_eq!(bids.len(), 5);
        assert_eq!(asks.len(), 5);
        for i in 1..5 {
            assert!(bids[i].price < bids[i - 1].price, "bids walk down");
            assert!(asks[i].price > asks[i - 1].price, "asks walk up");
            assert!(bids[i].size <= bids[i - 1].size, "depth thins");
        }
    }

    /// Every level must sit on the cent grid — this is what keeps the book's
    /// prices comparable with printed trades.
    #[test]
    fn every_ladder_price_is_on_the_cent_grid() {
        let (bids, asks) = quote_ladder(&LadderParams {
            quote: base(),
            levels: 10.0,
            level_step: 0.5,
        });
        for l in bids.iter().chain(asks.iter()) {
            let cents = l.price * 100.0;
            assert!(
                (cents - cents.round()).abs() < 1e-9,
                "{} is off-grid",
                l.price
            );
        }
    }

    #[test]
    fn zero_levels_still_quotes_one() {
        let (bids, asks) = quote_ladder(&LadderParams {
            quote: base(),
            levels: 0.0,
            level_step: 0.5,
        });
        assert_eq!(bids.len(), 1);
        assert_eq!(asks.len(), 1);
    }
}

#[cfg(test)]
mod js_round_tests {
    use crate::mathx::js_round;

    /// Values checked against V8 directly (`node -e 'Math.round(x)'`), not
    /// against a reimplementation. `Object.is` was used on the JS side so
    /// that -0 and +0 were distinguished rather than both printing as "0".
    #[test]
    fn matches_v8_including_the_two_edge_cases() {
        // (input, expected) — expected transcribed from V8.
        let cases: &[(f64, f64)] = &[
            (0.5, 1.0),
            (2.5, 3.0),
            (-3.5, -3.0), // half-UP, not away-from-zero: f64::round gives -4
            (-0.5, -0.0), // (x+0.5).floor() gives +0
            (-0.2, -0.0),
            (-0.0, -0.0),
            (0.0, 0.0),
            (0.49999999999999994, 0.0), // (x+0.5).floor() overshoots to 1
            (1.5, 2.0),
            (-1.5, -1.0),
            (4503599627370496.5, 4503599627370496.0),
        ];

        for &(input, expected) in cases {
            let got = js_round(input);
            // Bits, not `==`: the whole point of three of these cases is a
            // zero whose sign differs, and `-0.0 == 0.0` is true.
            assert_eq!(
                got.to_bits(),
                expected.to_bits(),
                "js_round({input:?}) = {got:?}, V8 gives {expected:?}"
            );
        }
    }

    #[test]
    fn propagates_non_finite_like_v8() {
        assert!(js_round(f64::NAN).is_nan());
        assert_eq!(js_round(f64::INFINITY), f64::INFINITY);
        assert_eq!(js_round(f64::NEG_INFINITY), f64::NEG_INFINITY);
    }
}
