//! Central limit order book — price-time priority matching.
//!
//! Ported from the reference implementation's order book, which is
//! **zero imports and zero transcendentals**: the most self-contained module
//! in the whole port, and therefore held to strict bit-identical parity like
//! `fair_value`.
//!
//! # What this module is for
//!
//! Execution used to run through a deterministic spread model with the
//! rendered book being display-only. That conflicts with the realism mandate:
//! real exchanges do not compute a fill price from a formula, they match two
//! orders. This is the matching core, and the consequence that matters is that
//! **slippage on size becomes emergent rather than modelled** — a large order
//! pays more because it eats real levels, not because a coefficient says so.
//!
//! # Faithfulness notes
//!
//! Several behaviours here look like they could be tidied and must not be:
//!
//! - **Trades print at the RESTING order's price**, never the incoming one.
//!   That is what gives a taker genuine price improvement when it crosses a
//!   better quote.
//! - **Depth is trimmed from the far end** at [`MAX_DEPTH_PER_SIDE`]. Dropping
//!   the worst-priced orders cannot change a fill that would have happened,
//!   which is why the cap is safe.
//! - **`!(quantity > 0)` rather than `quantity <= 0`.** The negation is
//!   load-bearing: it also rejects NaN, which `<=` would let through.
//! - **A posted remainder counts as filled, not unfilled.** `submitOrder` sets
//!   `remaining = 0` once the rest is parked in the book, because it was
//!   accepted rather than rejected.

/// Maximum resting orders per side. Bounded so insertion stays a short scan.
use crate::mathx;

pub const MAX_DEPTH_PER_SIDE: usize = 32;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

/// A resting order.
#[derive(Debug, Clone, PartialEq)]
pub struct BookOrder {
    pub id: String,
    pub side: Side,
    pub price: f64,
    /// Original size, in shares.
    pub quantity: f64,
    /// Unfilled remainder, in shares.
    pub remaining: f64,
    /// Monotonic arrival counter — the "time" half of price-time priority.
    pub sequence: u64,
    pub owner_id: String,
}

/// One executed trade.
#[derive(Debug, Clone, PartialEq)]
pub struct Fill {
    /// Always the resting (maker) order's price.
    pub price: f64,
    pub quantity: f64,
    pub maker_order_id: String,
    pub maker_id: String,
    pub taker_id: String,
    pub taker_side: Side,
}

#[derive(Debug, Clone, PartialEq)]
pub struct MatchResult {
    pub fills: Vec<Fill>,
    /// Shares that could not be filled. Zero when a remainder was posted.
    pub unfilled: f64,
    /// Volume-weighted average across `fills`, or `None` if nothing filled.
    pub average_price: Option<f64>,
    /// The remainder as a resting order, when one was posted.
    pub resting: Option<BookOrder>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PriceLevel {
    pub price: f64,
    pub quantity: f64,
    pub orders: u32,
}

#[derive(Debug, Clone, PartialEq)]
pub struct SweepCost {
    pub average_price: f64,
    pub worst_price: f64,
    pub filled: f64,
}

/// Options for [`OrderBook::submit`].
#[derive(Debug, Clone, Default)]
pub struct SubmitOptions {
    /// `None` is a market order.
    pub limit_price: Option<f64>,
    /// Rest any unfilled remainder. Ignored for market orders, which never
    /// rest — an unfilled market remainder is simply unfilled.
    pub post_remainder: bool,
    /// Stable id for the incoming order. Only used when it rests.
    pub order_id: Option<String>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OrderBook {
    pub company_id: String,
    /// Descending by price, then ascending by sequence.
    pub bids: Vec<BookOrder>,
    /// Ascending by price, then ascending by sequence.
    pub asks: Vec<BookOrder>,
    /// Last traded price. `None` until the book has printed a trade.
    pub last_price: Option<f64>,
    pub sequence: u64,
}

impl OrderBook {
    pub fn new(company_id: impl Into<String>, last_price: Option<f64>) -> Self {
        Self {
            company_id: company_id.into(),
            bids: Vec::new(),
            asks: Vec::new(),
            last_price,
            sequence: 0,
        }
    }

    pub fn best_bid(&self) -> Option<f64> {
        self.bids.first().map(|o| o.price)
    }

    pub fn best_ask(&self) -> Option<f64> {
        self.asks.first().map(|o| o.price)
    }

    /// `None` unless BOTH sides are populated.
    pub fn mid_price(&self) -> Option<f64> {
        match (self.best_bid(), self.best_ask()) {
            (Some(b), Some(a)) => Some((b + a) / 2.0),
            _ => None,
        }
    }

    /// `None` unless both sides are populated.
    pub fn spread(&self) -> Option<f64> {
        match (self.best_bid(), self.best_ask()) {
            (Some(b), Some(a)) => Some(a - b),
            _ => None,
        }
    }

    /// Total shares resting on a side.
    ///
    /// Folded explicitly from `0.0` rather than using `Iterator::sum`, and the
    /// difference is observable. Rust's `Sum for f64` uses **`-0.0`** as its
    /// identity — a deliberate choice, since `-0.0 + x == x` preserves sign
    /// where `0.0 + (-0.0)` would not. The reference implementation starts from `let total =
    /// 0`, which is `+0.0`.
    ///
    /// So on an empty side `sum()` returns `-0.0` and the original returns
    /// `+0.0`. They compare equal under `==` and print the same at most
    /// precisions, but the bit patterns differ, and `1.0 / -0.0` is negative
    /// infinity where `1.0 / 0.0` is positive. Caught by the parity gate on
    /// step 0 of the replay — a good advertisement for comparing bits rather
    /// than values.
    pub fn depth(&self, side: Side) -> f64 {
        let mut total = 0.0;
        for o in self.side(side) {
            total += o.remaining;
        }
        total
    }

    fn side(&self, side: Side) -> &Vec<BookOrder> {
        match side {
            Side::Buy => &self.bids,
            Side::Sell => &self.asks,
        }
    }

    fn side_mut(&mut self, side: Side) -> &mut Vec<BookOrder> {
        match side {
            Side::Buy => &mut self.bids,
            Side::Sell => &mut self.asks,
        }
    }

    /// Aggregate a side into price levels, best-first.
    ///
    /// Note the loop shape: the level cap is checked only when opening a NEW
    /// level, so an order at the same price as the last level is always merged
    /// even once `max_levels` is reached. Faithful to the original, where the
    /// `break` sits inside the else-branch.
    pub fn price_levels(&self, side: Side, max_levels: usize) -> Vec<PriceLevel> {
        let mut levels: Vec<PriceLevel> = Vec::new();
        for o in self.side(side) {
            if let Some(last) = levels.last_mut() {
                if last.price == o.price {
                    last.quantity += o.remaining;
                    last.orders += 1;
                    continue;
                }
            }
            if levels.len() >= max_levels {
                break;
            }
            levels.push(PriceLevel {
                price: o.price,
                quantity: o.remaining,
                orders: 1,
            });
        }
        levels
    }

    /// Insert preserving price-time priority.
    ///
    /// Advances past everything that ranks better than OR EQUAL TO the
    /// newcomer, so an order at an existing price lands behind the queue at
    /// that price. That is time priority, and it falls out of the comparison
    /// being strict — using `>=` here would silently give newcomers queue
    /// precedence over orders that arrived first.
    fn insert_resting(&mut self, order: BookOrder) {
        let is_buy = order.side == Side::Buy;
        let price = order.price;
        let side = self.side_mut(order.side);

        let mut i = 0;
        while i < side.len() {
            let better = if is_buy {
                price > side[i].price
            } else {
                price < side[i].price
            };
            if better {
                break;
            }
            i += 1;
        }
        side.insert(i, order);

        // Trim the far end. The worst-priced orders are the ones that would
        // never trade, so dropping them cannot change any fill.
        if side.len() > MAX_DEPTH_PER_SIDE {
            side.truncate(MAX_DEPTH_PER_SIDE);
        }
    }

    /// Does `taker_price` cross `resting_price`? A market order crosses all.
    fn crosses(side: Side, taker_price: Option<f64>, resting_price: f64) -> bool {
        match taker_price {
            None => true,
            Some(p) => match side {
                Side::Buy => p >= resting_price,
                Side::Sell => p <= resting_price,
            },
        }
    }

    /// Submit an order and match it against the book, mutating in place.
    pub fn submit(
        &mut self,
        side: Side,
        quantity: f64,
        taker_id: &str,
        options: SubmitOptions,
    ) -> MatchResult {
        let limit_price = options.limit_price;
        let mut fills: Vec<Fill> = Vec::new();

        // `!(q > 0)` and not `q <= 0` — the negation also rejects NaN.
        if !(quantity > 0.0) {
            return MatchResult {
                fills,
                unfilled: 0.0,
                average_price: None,
                resting: None,
            };
        }

        let mut remaining = quantity;
        let opposite_side = match side {
            Side::Buy => Side::Sell,
            Side::Sell => Side::Buy,
        };

        while remaining > 0.0 && !self.side(opposite_side).is_empty() {
            let maker_price = self.side(opposite_side)[0].price;
            if !Self::crosses(side, limit_price, maker_price) {
                break;
            }

            let (traded, exhausted, maker_order_id, maker_id) = {
                let maker = &mut self.side_mut(opposite_side)[0];
                let traded = mathx::min(remaining, maker.remaining);
                maker.remaining -= traded;
                (
                    traded,
                    maker.remaining <= 0.0,
                    maker.id.clone(),
                    maker.owner_id.clone(),
                )
            };

            remaining -= traded;
            fills.push(Fill {
                price: maker_price,
                quantity: traded,
                maker_order_id,
                maker_id,
                taker_id: taker_id.to_string(),
                taker_side: side,
            });

            self.last_price = Some(maker_price);
            if exhausted {
                self.side_mut(opposite_side).remove(0);
            }
        }

        let mut resting: Option<BookOrder> = None;
        if remaining > 0.0 {
            if let (true, Some(limit)) = (options.post_remainder, limit_price) {
                let order = BookOrder {
                    id: options
                        .order_id
                        .unwrap_or_else(|| format!("{}-{}", self.company_id, self.sequence)),
                    side,
                    price: limit,
                    quantity,
                    remaining,
                    sequence: self.sequence,
                    owner_id: taker_id.to_string(),
                };
                self.sequence += 1;
                self.insert_resting(order.clone());
                resting = Some(order);
                // Parked in the book rather than rejected, so it is not
                // "unfilled" from the caller's point of view.
                remaining = 0.0;
            }
        }

        let average_price = if fills.is_empty() {
            None
        } else {
            let mut notional = 0.0;
            let mut shares = 0.0;
            for f in &fills {
                notional += f.price * f.quantity;
                shares += f.quantity;
            }
            Some(notional / shares)
        };

        MatchResult {
            fills,
            unfilled: remaining,
            average_price,
            resting,
        }
    }

    /// Rest a passive order without attempting to cross. Used by makers.
    pub fn post_limit(
        &mut self,
        side: Side,
        price: f64,
        quantity: f64,
        owner_id: &str,
        order_id: Option<String>,
    ) -> Option<BookOrder> {
        if !(quantity > 0.0) || !(price > 0.0) {
            return None;
        }
        let order = BookOrder {
            id: order_id.unwrap_or_else(|| format!("{}-{}", self.company_id, self.sequence)),
            side,
            price,
            quantity,
            remaining: quantity,
            sequence: self.sequence,
            owner_id: owner_id.to_string(),
        };
        self.sequence += 1;
        self.insert_resting(order.clone());
        Some(order)
    }

    /// Remove a resting order by id.
    pub fn cancel_order(&mut self, order_id: &str) -> bool {
        for side in [Side::Buy, Side::Sell] {
            let orders = self.side_mut(side);
            if let Some(idx) = orders.iter().position(|o| o.id == order_id) {
                orders.remove(idx);
                return true;
            }
        }
        false
    }

    /// Remove every resting order owned by `owner_id` — a maker re-quote.
    pub fn cancel_all_for(&mut self, owner_id: &str) -> u32 {
        let mut removed = 0;
        for side in [Side::Buy, Side::Sell] {
            let orders = self.side_mut(side);
            let before = orders.len();
            orders.retain(|o| o.owner_id != owner_id);
            removed += (before - orders.len()) as u32;
        }
        removed
    }

    /// Cost to sweep `quantity` shares, walking real depth.
    ///
    /// This replaces the modelled market-impact coefficient: impact stops
    /// being a parameter and becomes a consequence of resting liquidity.
    /// `None` when nothing at all could be filled.
    pub fn sweep_cost(&self, side: Side, quantity: f64) -> Option<SweepCost> {
        let opposite = match side {
            Side::Buy => Side::Sell,
            Side::Sell => Side::Buy,
        };
        let mut remaining = quantity;
        let mut notional = 0.0;
        let mut worst_price = 0.0;

        for o in self.side(opposite) {
            if remaining <= 0.0 {
                break;
            }
            let traded = mathx::min(remaining, o.remaining);
            notional += o.price * traded;
            remaining -= traded;
            worst_price = o.price;
        }

        let filled = quantity - remaining;
        if filled <= 0.0 {
            return None;
        }
        Some(SweepCost {
            average_price: notional / filled,
            worst_price,
            filled,
        })
    }

    /// Append a maker level that is ALREADY in priority order.
    ///
    /// `post_limit` runs an insertion scan per order, which is quadratic when
    /// building a ladder — and the ladder is generated best-first, so the scan
    /// always walks to the end and finds nothing. Across 108 companies every
    /// game-minute that dominated the tick cost (measured in the original:
    /// 0.59 of 0.83 ms/tick).
    ///
    /// Callers MUST append in priority order. Anything out of order goes
    /// through `post_limit` instead.
    pub fn append_maker_level(
        &mut self,
        side: Side,
        price: f64,
        quantity: f64,
        owner_id: &str,
    ) -> Option<BookOrder> {
        if !(quantity > 0.0) || !(price > 0.0) {
            return None;
        }
        if self.side(side).len() >= MAX_DEPTH_PER_SIDE {
            return None;
        }
        let order = BookOrder {
            id: format!("{}-{}", self.company_id, self.sequence),
            side,
            price,
            quantity,
            remaining: quantity,
            sequence: self.sequence,
            owner_id: owner_id.to_string(),
        };
        self.sequence += 1;
        self.side_mut(side).push(order.clone());
        Some(order)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn book() -> OrderBook {
        OrderBook::new("TEST", None)
    }

    #[test]
    fn bids_rank_by_descending_price_then_arrival() {
        let mut b = book();
        b.post_limit(Side::Buy, 100.0, 10.0, "a", None);
        b.post_limit(Side::Buy, 101.0, 10.0, "b", None);
        b.post_limit(Side::Buy, 100.0, 10.0, "c", None); // ties with `a`
        let owners: Vec<&str> = b.bids.iter().map(|o| o.owner_id.as_str()).collect();
        assert_eq!(
            owners,
            vec!["b", "a", "c"],
            "equal prices must keep arrival order"
        );
    }

    #[test]
    fn asks_rank_by_ascending_price_then_arrival() {
        let mut b = book();
        b.post_limit(Side::Sell, 101.0, 10.0, "a", None);
        b.post_limit(Side::Sell, 100.0, 10.0, "b", None);
        b.post_limit(Side::Sell, 101.0, 10.0, "c", None);
        let owners: Vec<&str> = b.asks.iter().map(|o| o.owner_id.as_str()).collect();
        assert_eq!(owners, vec!["b", "a", "c"]);
    }

    /// The taker gets the maker's price, not its own — this is where price
    /// improvement comes from.
    #[test]
    fn trades_print_at_the_makers_price() {
        let mut b = book();
        b.post_limit(Side::Sell, 100.0, 10.0, "maker", None);
        let r = b.submit(
            Side::Buy,
            10.0,
            "taker",
            SubmitOptions {
                limit_price: Some(105.0),
                ..Default::default()
            },
        );
        assert_eq!(r.fills.len(), 1);
        assert_eq!(
            r.fills[0].price, 100.0,
            "should fill at 100, not the 105 bid"
        );
        assert_eq!(b.last_price, Some(100.0));
    }

    /// Slippage as a consequence of depth rather than a coefficient.
    #[test]
    fn sweeping_multiple_levels_pays_a_worse_average() {
        let mut b = book();
        b.post_limit(Side::Sell, 100.0, 10.0, "m1", None);
        b.post_limit(Side::Sell, 101.0, 10.0, "m2", None);
        b.post_limit(Side::Sell, 102.0, 10.0, "m3", None);
        let r = b.submit(Side::Buy, 25.0, "taker", SubmitOptions::default());
        assert_eq!(r.fills.len(), 3);
        assert_eq!(r.unfilled, 0.0);
        // (100*10 + 101*10 + 102*5) / 25
        assert_eq!(r.average_price, Some((1000.0 + 1010.0 + 510.0) / 25.0));
    }

    #[test]
    fn a_market_order_never_rests() {
        let mut b = book();
        b.post_limit(Side::Sell, 100.0, 5.0, "m", None);
        let r = b.submit(
            Side::Buy,
            20.0,
            "taker",
            SubmitOptions {
                post_remainder: true,
                ..Default::default()
            },
        );
        assert_eq!(r.unfilled, 15.0);
        assert!(r.resting.is_none());
        assert!(b.bids.is_empty());
    }

    /// A posted remainder is accepted, not rejected, so it is not "unfilled".
    #[test]
    fn a_posted_remainder_counts_as_filled() {
        let mut b = book();
        b.post_limit(Side::Sell, 100.0, 5.0, "m", None);
        let r = b.submit(
            Side::Buy,
            20.0,
            "taker",
            SubmitOptions {
                limit_price: Some(100.0),
                post_remainder: true,
                order_id: None,
            },
        );
        assert_eq!(r.unfilled, 0.0);
        assert_eq!(r.resting.as_ref().unwrap().remaining, 15.0);
        assert_eq!(b.bids.len(), 1);
    }

    #[test]
    fn a_non_crossing_limit_does_not_trade() {
        let mut b = book();
        b.post_limit(Side::Sell, 100.0, 10.0, "m", None);
        let r = b.submit(
            Side::Buy,
            10.0,
            "taker",
            SubmitOptions {
                limit_price: Some(99.0),
                ..Default::default()
            },
        );
        assert!(r.fills.is_empty());
        assert_eq!(r.unfilled, 10.0);
    }

    /// NaN must be rejected by `!(q > 0)`, which `q <= 0` would let through.
    #[test]
    fn nan_and_nonpositive_sizes_are_rejected() {
        let mut b = book();
        b.post_limit(Side::Sell, 100.0, 10.0, "m", None);
        for q in [0.0, -5.0, f64::NAN] {
            let r = b.submit(Side::Buy, q, "taker", SubmitOptions::default());
            assert!(r.fills.is_empty(), "quantity {q} should not trade");
            assert_eq!(r.unfilled, 0.0);
        }
        assert!(b
            .post_limit(Side::Buy, 100.0, f64::NAN, "x", None)
            .is_none());
        assert!(b.post_limit(Side::Buy, f64::NAN, 10.0, "x", None).is_none());
    }

    #[test]
    fn depth_is_capped_and_trimmed_from_the_worst_end() {
        let mut b = book();
        // Ascending bids: each new one is the best, pushing the worst out.
        for i in 0..(MAX_DEPTH_PER_SIDE + 10) {
            b.post_limit(Side::Buy, 100.0 + i as f64, 1.0, "m", None);
        }
        assert_eq!(b.bids.len(), MAX_DEPTH_PER_SIDE);
        assert_eq!(b.bids[0].price, 100.0 + (MAX_DEPTH_PER_SIDE + 9) as f64);
    }

    #[test]
    fn append_maker_level_refuses_past_the_cap() {
        let mut b = book();
        for i in 0..MAX_DEPTH_PER_SIDE {
            assert!(b
                .append_maker_level(Side::Buy, 100.0 - i as f64, 1.0, "mm")
                .is_some());
        }
        assert!(b.append_maker_level(Side::Buy, 1.0, 1.0, "mm").is_none());
    }

    #[test]
    fn cancel_all_for_removes_both_sides() {
        let mut b = book();
        b.post_limit(Side::Buy, 99.0, 1.0, "mm", None);
        b.post_limit(Side::Sell, 101.0, 1.0, "mm", None);
        b.post_limit(Side::Buy, 98.0, 1.0, "other", None);
        assert_eq!(b.cancel_all_for("mm"), 2);
        assert_eq!(b.bids.len(), 1);
        assert!(b.asks.is_empty());
    }

    #[test]
    fn sweep_cost_reports_none_when_the_book_is_empty() {
        let b = book();
        assert!(b.sweep_cost(Side::Buy, 10.0).is_none());
    }

    #[test]
    fn sweep_cost_reports_a_partial_fill() {
        let mut b = book();
        b.post_limit(Side::Sell, 100.0, 5.0, "m", None);
        let s = b.sweep_cost(Side::Buy, 20.0).unwrap();
        assert_eq!(s.filled, 5.0);
        assert_eq!(s.average_price, 100.0);
        assert_eq!(s.worst_price, 100.0);
    }

    /// Shares in equals shares out: nothing is created or destroyed by a match.
    #[test]
    fn matching_conserves_shares() {
        let mut b = book();
        b.post_limit(Side::Sell, 100.0, 7.0, "m1", None);
        b.post_limit(Side::Sell, 101.0, 9.0, "m2", None);
        let before = b.depth(Side::Sell);
        let r = b.submit(Side::Buy, 12.0, "taker", SubmitOptions::default());
        let traded: f64 = r.fills.iter().map(|f| f.quantity).sum();
        assert_eq!(traded + b.depth(Side::Sell), before);
        assert_eq!(traded, 12.0);
    }
}
