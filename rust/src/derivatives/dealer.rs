//! The options dealer: one market maker quotes every listed option from the
//! surface, and is the counterparty to every option trade.
//!
//! There is no order book per contract. An index chain is hundreds of
//! contracts, the model has no background option flow to fill their books,
//! and listed options are quoted by market makers from a volatility surface
//! in the real market. Quotes from one surface satisfy put-call parity and
//! the butterfly and calendar bounds by construction, where independent
//! books would drift apart and hand agents arbitrage no real market offers.
//! A quote is a pure function of state, computed only for a contract asked
//! for, with no draws.
//!
//! # The quote
//!
//! The dealer's mid is Black's price at the surface's implied volatility
//! shifted by its inventory ([`OptionDealerSpec::inventory_shift`]). Its
//! half-spread, in volatility, is `option_dealer_spread` times the equity
//! maker's VIX multiplier (`microstructure::vix_spread_multiplier`) times
//! `1 + moneyness_slope |z|`, `z` the strike's distance from the forward in
//! at-the-money total sds; in price it is that times the option's vega, so
//! it grows with vega, distance from the money, time to expiry and the VIX.
//! Each side shows [`OptionDealerSpec::levels`] levels of
//! [`OptionDealerSpec::displayed_size`] contracts, level `j` at the
//! half-spread times `1 + j level_step`, rounded away from the mid onto the
//! option's grid (0.05 below 3.00, 0.10 at or above, as Cboe's SPX). A
//! larger order walks that implied-volatility ladder.
//!
//! # Inventory
//!
//! Each trade leaves the dealer holding its other side. Per expiry the
//! dealer keeps a pressure: the contracts it has sold less those it has
//! bought, each weighted by its vega over the expiry's at-the-money vega,
//! decaying at [`OptionDealerSpec::inventory_half_life`] ticks, the rate
//! maker's half-life (`rates::INVENTORY_HALF_LIFE_TICKS`), which stands for
//! the dealer hedging and laying the risk off. The pressure moves the
//! expiry's at-the-money volatility by `inventory_skew` at-the-money
//! half-spreads per displayed size, capped at `inventory_cap` of them, up
//! when the dealer is short. The shift scales the whole expiry's smile
//! through its `theta`, and the shifted thetas are held non-decreasing
//! across expiries ([`dealer_thetas`]), so quoted chains stay free of
//! static arbitrage ([`chain_violations`]).

use super::pricing::{black_price, Greeks};
use super::symbol::Right;
use crate::mathx;

/// The dealer's quoting constants: what its quote looks like, not how wide
/// it is, which is the dial `option_dealer_spread`.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct OptionDealerSpec {
    /// The price grid below `tick_switch`, and at or above it.
    pub tick: f64,
    pub tick_large: f64,
    pub tick_switch: f64,
    /// Contracts shown at each level of each side.
    pub displayed_size: f64,
    /// Levels a side.
    pub levels: usize,
    /// Each level further out adds this share of the half-spread.
    pub level_step: f64,
    /// The half-spread grows by this share per at-the-money total sd the
    /// strike sits from the forward.
    pub moneyness_slope: f64,
    /// At-the-money half-spreads the volatility moves per displayed size of
    /// at-the-money-equivalent pressure, and the most it moves.
    pub inventory_skew: f64,
    pub inventory_cap: f64,
    /// The pressure halves in this many open ticks.
    pub inventory_half_life: f64,
}

/// The index options' dealer.
pub const INDEX_OPTION_DEALER: OptionDealerSpec = OptionDealerSpec {
    tick: 0.05,
    tick_large: 0.10,
    tick_switch: 3.0,
    displayed_size: 50.0,
    levels: 10,
    level_step: 0.5,
    moneyness_slope: 0.25,
    inventory_skew: 0.5,
    inventory_cap: 4.0,
    inventory_half_life: crate::rates::INVENTORY_HALF_LIFE_TICKS,
};

impl OptionDealerSpec {
    /// The grid at `price`.
    pub fn tick_at(&self, price: f64) -> f64 {
        if price >= self.tick_switch {
            self.tick_large
        } else {
            self.tick
        }
    }

    /// The half-spread in volatility at the base `base`
    /// (`option_dealer_spread`), the VIX `vix` and a strike `z` at-the-money
    /// total sds from the forward.
    pub fn half_spread(&self, base: f64, vix: f64, z: f64) -> f64 {
        base * crate::microstructure::vix_spread_multiplier(vix) * (1.0 + self.moneyness_slope * z.abs())
    }

    /// The volatility shift a pressure of `pressure` at-the-money-equivalent
    /// contracts puts on its expiry, `atm_half_spread` the expiry's
    /// at-the-money half-spread: up while the dealer is short.
    pub fn inventory_shift(&self, pressure: f64, atm_half_spread: f64) -> f64 {
        if pressure == 0.0 || !(atm_half_spread > 0.0) {
            return 0.0;
        }
        let cap = self.inventory_cap * atm_half_spread;
        mathx::clamp(self.inventory_skew * atm_half_spread * pressure / self.displayed_size, -cap, cap)
    }

    /// What is left of a pressure after one open tick.
    pub fn decay(&self) -> f64 {
        mathx::pow(0.5, 1.0 / self.inventory_half_life)
    }
}

/// A price rounded onto the grid away from the mid: up for an ask, down for
/// a bid.
fn on_grid(spec: &OptionDealerSpec, price: f64, up: bool) -> f64 {
    let tick = spec.tick_at(price);
    if up {
        (price / tick - 1e-9).ceil() * tick
    } else {
        (price / tick + 1e-9).floor() * tick
    }
}

/// One level of the dealer's ladder.
#[derive(Debug, Clone, Copy, PartialEq)]
#[non_exhaustive]
pub struct Level {
    pub price: f64,
    pub size: f64,
    /// The volatility it is priced at, before the grid.
    pub vol: f64,
}

/// The dealer's ladder for one option: its mid and each side's levels, best
/// first. A bid below one tick is not shown.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct Ladder {
    pub mid: f64,
    pub bids: Vec<Level>,
    pub asks: Vec<Level>,
}

/// The ladder of the option `right` struck at `strike` on the forward
/// `forward`, `years` to expiry, discounted by `discount`, at the mid
/// volatility `vol` and the half-spread `half_spread` (volatility).
#[allow(clippy::too_many_arguments)]
pub fn ladder(
    spec: &OptionDealerSpec,
    right: Right,
    forward: f64,
    strike: f64,
    years: f64,
    discount: f64,
    vol: f64,
    half_spread: f64,
) -> Ladder {
    let mid = black_price(right, forward, strike, years, vol, discount);
    let mut asks: Vec<Level> = Vec::with_capacity(spec.levels);
    let mut bids: Vec<Level> = Vec::with_capacity(spec.levels);
    for j in 0..spec.levels {
        let h = half_spread * (1.0 + j as f64 * spec.level_step);
        let ask_vol = vol + h;
        let mut ask = on_grid(spec, black_price(right, forward, strike, years, ask_vol, discount), true);
        let floor = match asks.last() {
            Some(prev) => prev.price + spec.tick_at(prev.price),
            // Strictly above the mid even where the spread is under a tick.
            None => on_grid(spec, mid, false) + spec.tick_at(mid),
        };
        if ask < floor - 1e-12 {
            ask = floor;
        }
        asks.push(Level { price: ask, size: spec.displayed_size, vol: ask_vol });
    }
    for j in 0..spec.levels {
        let h = half_spread * (1.0 + j as f64 * spec.level_step);
        let bid_vol = mathx::max(1e-4, vol - h);
        let mut bid = on_grid(spec, black_price(right, forward, strike, years, bid_vol, discount), false);
        let cap = match bids.last() {
            Some(prev) => prev.price - spec.tick_at(prev.price - 1e-9),
            // Strictly below the mid.
            None => on_grid(spec, mid, true) - spec.tick_at(mid),
        };
        if bid > cap + 1e-12 {
            bid = cap;
        }
        // The bids fall level by level, so the first under a tick ends them.
        if bid < spec.tick - 1e-12 {
            break;
        }
        bids.push(Level { price: bid, size: spec.displayed_size, vol: bid_vol });
    }
    Ladder { mid, bids, asks }
}

/// Walk a side of a ladder, best first, for `quantity` contracts at
/// `limit` or better (a buy takes asks at or below it, a sell bids at or
/// above): the fills, `(price, contracts)`, in order.
pub fn walk(levels: &[Level], quantity: f64, limit: Option<f64>, buying: bool) -> Vec<(f64, f64)> {
    let mut out = Vec::new();
    let mut left = quantity;
    for level in levels {
        if !(left > 1e-9) {
            break;
        }
        if let Some(l) = limit {
            let ok = if buying { level.price <= l + 1e-12 } else { level.price >= l - 1e-12 };
            if !ok {
                break;
            }
        }
        let take = mathx::min(left, level.size);
        out.push((level.price, take));
        left -= take;
    }
    out
}

/// A pressure after the dealer sells `sold` contracts (negative for
/// contracts it buys) of an option with vega `vega`, its expiry's
/// at-the-money vega `atm_vega`.
pub fn pressure_after(pressure: f64, sold: f64, vega: f64, atm_vega: f64) -> f64 {
    if !(atm_vega > 0.0) {
        return pressure;
    }
    pressure + sold * vega / atm_vega
}

/// The thetas the dealer quotes from: each expiry's (in expiry order)
/// at-the-money total variance `theta` over `sessions`, its at-the-money
/// volatility moved by `shift`, then held non-decreasing across expiries so
/// no calendar arbitrage opens between them. A shift is held above half the
/// volatility's fall.
pub fn dealer_thetas(expiries: &[(f64, f64)], shifts: &[f64]) -> Vec<f64> {
    let mut out = Vec::with_capacity(expiries.len());
    let mut top: f64 = 0.0;
    for (i, &(sessions, theta)) in expiries.iter().enumerate() {
        let shift = shifts.get(i).copied().unwrap_or(0.0);
        let shifted = if shift == 0.0 || !(theta > 0.0 && sessions > 0.0) {
            theta
        } else {
            let vol = mathx::sqrt(theta * 252.0 / sessions);
            let moved = mathx::max(0.5 * vol, vol + shift);
            theta * (moved / vol) * (moved / vol)
        };
        top = mathx::max(top, shifted);
        out.push(top);
    }
    out
}

/// A listed option's quote, as [`crate::engine::Engine::chain`] and
/// [`crate::engine::Engine::option_quote`] read it.
#[derive(Debug, Clone, PartialEq)]
#[non_exhaustive]
pub struct OptionQuote {
    /// `IDX.O0294.C1050.00`.
    pub symbol: String,
    pub root: String,
    pub expiry: i64,
    pub right: Right,
    pub strike: f64,
    /// The dealer's best bid and ask, and the contracts shown at each.
    /// `None` on a side it does not show (a bid under one tick).
    pub bid: Option<f64>,
    pub ask: Option<f64>,
    pub bid_size: f64,
    pub ask_size: f64,
    /// The dealer's mid: Black's price at `iv`. Not on the grid, so the
    /// mids of a strike's call and put meet put-call parity exactly.
    pub mid: f64,
    /// The surface's price, without the dealer's inventory.
    pub fair: f64,
    /// The dealer's mid volatility, and the surface's.
    pub iv: f64,
    pub surface_iv: f64,
    /// Black's Greeks at `iv`.
    pub greeks: Greeks,
    /// The forward to expiry, the index it reads, the financing rate (a
    /// fraction a year) and the dividends' present value, index points.
    pub forward: f64,
    pub index: f64,
    pub rate: f64,
    pub dividends: f64,
    pub sessions_to_expiry: f64,
    /// Dollars per index point.
    pub multiplier: f64,
    /// The grid at the mid.
    pub tick: f64,
    /// Contracts open: every holder's long positions, the dealer's among
    /// them.
    pub open_interest: f64,
    /// The scan's initial margin on one short contract, dollars
    /// (`margin_scan_coverage`), and the maintenance margin; `None` without
    /// margin.
    pub initial_margin: Option<f64>,
    pub maintenance_margin: Option<f64>,
}

/// Static-arbitrage violations on quoted chains (row SV2), each a sentence:
/// within an expiry, call mids not increasing and put mids not decreasing in
/// the strike, both convex (no negative butterfly), and each strike's call
/// and put mids within half the smallest tick of put-call parity on the
/// quote's forward and rate; across consecutive expiries, total implied
/// variance at a fixed log-moneyness not falling, the later expiry's read
/// by linear interpolation between its strikes. Empty when there are none.
pub fn chain_violations(quotes: &[OptionQuote], half_tick: f64) -> Vec<String> {
    let mut out = Vec::new();
    let mut expiries: Vec<i64> = quotes.iter().map(|q| q.expiry).collect();
    expiries.sort_unstable();
    expiries.dedup();
    let mut smiles: Vec<(f64, Vec<(f64, f64)>)> = Vec::new();
    for &e in &expiries {
        for right in [Right::Call, Right::Put] {
            let mut side: Vec<&OptionQuote> = quotes.iter().filter(|q| q.expiry == e && q.right == right).collect();
            side.sort_by(|a, b| a.strike.total_cmp(&b.strike));
            for w in side.windows(2) {
                let tol = 1e-9 * mathx::max(1.0, w[0].mid.abs());
                let bad = match right {
                    Right::Call => w[1].mid > w[0].mid + tol,
                    Right::Put => w[1].mid < w[0].mid - tol,
                };
                if bad {
                    out.push(format!(
                        "expiry {e}: the {} mid moves the wrong way from strike {} ({}) to {} ({})",
                        if right == Right::Call { "call" } else { "put" },
                        w[0].strike, w[0].mid, w[1].strike, w[1].mid
                    ));
                }
            }
            for w in side.windows(3) {
                let left = (w[1].mid - w[0].mid) / (w[1].strike - w[0].strike);
                let rightslope = (w[2].mid - w[1].mid) / (w[2].strike - w[1].strike);
                if rightslope < left - 1e-9 {
                    out.push(format!(
                        "expiry {e}: the butterfly at strike {} is negative ({} then {})",
                        w[1].strike, left, rightslope
                    ));
                }
            }
        }
        let calls: Vec<&OptionQuote> = quotes.iter().filter(|q| q.expiry == e && q.right == Right::Call).collect();
        for c in &calls {
            let Some(p) = quotes.iter().find(|q| q.expiry == e && q.right == Right::Put && q.strike == c.strike) else {
                continue;
            };
            let discount = mathx::exp(-c.rate * c.sessions_to_expiry / 252.0);
            let gap = c.mid - p.mid - discount * (c.forward - c.strike);
            if gap.abs() > half_tick {
                out.push(format!("expiry {e}: strike {} is {gap} off put-call parity", c.strike));
            }
        }
        let mut smile: Vec<(f64, f64)> = calls
            .iter()
            .map(|c| (mathx::log(c.strike / c.forward), c.iv * c.iv * c.sessions_to_expiry / 252.0))
            .collect();
        smile.sort_by(|a, b| a.0.total_cmp(&b.0));
        if let Some(c) = calls.first() {
            smiles.push((c.sessions_to_expiry, smile));
        }
    }
    for w in smiles.windows(2) {
        let (early, late) = (&w[0].1, &w[1].1);
        for &(k, v) in early {
            let Some(j) = late.windows(2).position(|p| p[0].0 <= k && k <= p[1].0) else {
                continue;
            };
            let (a, b) = (late[j], late[j + 1]);
            let later = if b.0 > a.0 { a.1 + (b.1 - a.1) * (k - a.0) / (b.0 - a.0) } else { a.1 };
            if later < v - 1e-12 {
                out.push(format!(
                    "calendar: total variance falls from {v} at {} sessions to {later} at {} at log-moneyness {k}",
                    w[0].0, w[1].0
                ));
            }
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::derivatives::pricing::black_greeks;
    use crate::derivatives::surface::Ssvi;

    const SPEC: OptionDealerSpec = INDEX_OPTION_DEALER;

    #[test]
    fn the_ladder_straddles_the_mid_on_the_grid_and_widens_outward() {
        for (k, t, v) in [(1000.0, 21.0, 0.2), (900.0, 63.0, 0.3), (1100.0, 5.0, 0.15), (600.0, 21.0, 0.6)] {
            for right in [Right::Call, Right::Put] {
                let years = t / 252.0;
                let l = ladder(&SPEC, right, 1000.0, k, years, 0.999, v, 0.004);
                assert_eq!(l.asks.len(), SPEC.levels);
                assert!(l.asks[0].price > l.mid, "{right:?} {k}");
                for w in l.asks.windows(2) {
                    assert!(w[1].price > w[0].price);
                }
                if let Some(b) = l.bids.first() {
                    assert!(b.price < l.mid && b.price >= SPEC.tick - 1e-12);
                }
                for w in l.bids.windows(2) {
                    assert!(w[1].price < w[0].price);
                }
                for level in l.asks.iter().chain(l.bids.iter()) {
                    // On the grid: every price a multiple of 0.05.
                    let steps = level.price / SPEC.tick;
                    assert!((steps - steps.round()).abs() < 1e-6, "{}", level.price);
                }
            }
        }
        // The half-spread in price is about the volatility half-spread times vega.
        let (p, g) = black_greeks(Right::Call, 1000.0, 1000.0, 21.0 / 252.0, 0.2, 0.0);
        let l = ladder(&SPEC, Right::Call, 1000.0, 1000.0, 21.0 / 252.0, 1.0, 0.2, 0.01);
        assert!((l.mid - p).abs() < 1e-9);
        let half = 0.5 * (l.asks[0].price - l.bids[0].price);
        assert!((half - 0.01 * g.vega).abs() <= SPEC.tick_large, "{half} {}", 0.01 * g.vega);
    }

    #[test]
    fn the_spread_grows_with_the_vix_and_the_distance_from_the_money() {
        let h = |vix: f64, z: f64| SPEC.half_spread(0.005, vix, z);
        assert_eq!(h(15.0, 0.0), 0.005);
        assert!(h(30.0, 0.0) > h(15.0, 0.0));
        assert!(h(15.0, 2.0) > h(15.0, 1.0));
        assert_eq!(h(15.0, -2.0), h(15.0, 2.0));
    }

    #[test]
    fn a_walk_takes_levels_in_order_up_to_its_limit() {
        let l = ladder(&SPEC, Right::Put, 1000.0, 980.0, 21.0 / 252.0, 1.0, 0.22, 0.005);
        let all = walk(&l.asks, 120.0, None, true);
        assert_eq!(all.len(), 3);
        assert_eq!(all[2].1, 20.0);
        assert_eq!(all.iter().map(|f| f.1).sum::<f64>(), 120.0);
        let limited = walk(&l.asks, 120.0, Some(l.asks[0].price), true);
        assert_eq!(limited, vec![(l.asks[0].price, 50.0)]);
        assert!(walk(&l.bids, 10.0, Some(l.bids[0].price + 1.0), false).is_empty());
        // More than the ladder shows fills what it shows.
        let big = walk(&l.asks, 1e6, None, true);
        assert_eq!(big.iter().map(|f| f.1).sum::<f64>(), SPEC.levels as f64 * SPEC.displayed_size);
    }

    #[test]
    fn inventory_moves_the_volatility_up_when_short_capped_and_decays() {
        assert_eq!(SPEC.inventory_shift(0.0, 0.01), 0.0);
        assert!((SPEC.inventory_shift(50.0, 0.01) - 0.005).abs() < 1e-15);
        assert!((SPEC.inventory_shift(-50.0, 0.01) + 0.005).abs() < 1e-15);
        assert_eq!(SPEC.inventory_shift(1e9, 0.01), 0.04);
        let d = SPEC.decay();
        assert!((mathx::pow(d, 15.0) - 0.5).abs() < 1e-12);
        let p = pressure_after(0.0, 10.0, 50.0, 100.0);
        assert_eq!(p, 5.0);
        assert_eq!(pressure_after(p, -10.0, 50.0, 100.0), 0.0);
    }

    #[test]
    fn shifted_thetas_stay_non_decreasing() {
        let e = [(10.0, 0.001), (31.0, 0.003), (52.0, 0.005)];
        let t = dealer_thetas(&e, &[0.05, 0.0, -0.05]);
        assert!(t[0] > 0.001);
        assert!(t[1] >= t[0] && t[2] >= t[1]);
        assert_eq!(dealer_thetas(&e, &[]), vec![0.001, 0.003, 0.005]);
        // A shift moves the at-the-money volatility by itself.
        let one = dealer_thetas(&[(21.0, 0.2 * 0.2 * 21.0 / 252.0)], &[0.01]);
        assert!(((one[0] * 252.0 / 21.0).sqrt() - 0.21).abs() < 1e-12);
    }

    fn quotes_from(ssvi: &Ssvi, thetas: &[(i64, f64, f64)], forward: f64, rate: f64) -> Vec<OptionQuote> {
        let mut out = Vec::new();
        for &(expiry, sessions, theta) in thetas {
            let years = sessions / 252.0;
            let fwd = forward * mathx::exp(rate * years);
            for i in 0..41 {
                let strike = 800.0 + 10.0 * i as f64;
                let k = mathx::log(strike / fwd);
                let iv = mathx::sqrt(ssvi.total_variance(k, theta) / years);
                for right in [Right::Call, Right::Put] {
                    let (mid, greeks) = black_greeks(right, forward, strike, years, iv, rate);
                    out.push(OptionQuote {
                        symbol: String::new(),
                        root: "IDX".into(),
                        expiry,
                        right,
                        strike,
                        bid: None,
                        ask: None,
                        bid_size: 0.0,
                        ask_size: 0.0,
                        mid,
                        fair: mid,
                        iv,
                        surface_iv: iv,
                        greeks,
                        forward: fwd,
                        index: forward,
                        rate,
                        dividends: 0.0,
                        sessions_to_expiry: sessions,
                        multiplier: 100.0,
                        tick: 0.05,
                        open_interest: 0.0,
                        initial_margin: None,
                        maintenance_margin: None,
                    });
                }
            }
        }
        out
    }

    #[test]
    fn a_chain_from_one_surface_is_clean_and_a_planted_violation_is_found() {
        let ssvi = Ssvi::new(-0.7, 1.0, 0.5);
        let thetas = [(14, 13.6, 0.0022), (35, 34.6, 0.0056), (56, 55.6, 0.0091)];
        let quotes = quotes_from(&ssvi, &thetas, 1000.0, 0.03);
        assert_eq!(chain_violations(&quotes, 0.025), Vec::<String>::new());
        // A call mid bumped above its neighbour's: monotonicity and a butterfly.
        let mut bad = quotes.clone();
        let i = bad.iter().position(|q| q.expiry == 35 && q.right == Right::Call && q.strike == 1000.0).unwrap();
        bad[i].mid += 2.0;
        let v = chain_violations(&bad, 0.025);
        assert!(v.iter().any(|s| s.contains("butterfly")), "{v:?}");
        assert!(v.iter().any(|s| s.contains("parity")), "{v:?}");
        // A later expiry below an earlier one: a calendar violation.
        let inverted = quotes_from(&ssvi, &[(14, 13.6, 0.0060), (35, 34.6, 0.0056)], 1000.0, 0.03);
        let v = chain_violations(&inverted, 0.025);
        assert!(v.iter().any(|s| s.contains("calendar")), "{v:?}");
    }
}
