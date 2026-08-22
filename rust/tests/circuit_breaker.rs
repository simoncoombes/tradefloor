//! INVARIANTS 1.13 — the circuit breaker actually binds.
//!
//! # The gap this closes
//!
//! the invariants notes records 1.13 as **"CLAIMED, NOT DIRECTLY TESTED"**, and is
//! blunt about why that matters: the closest thing to a check in the
//! TypeScript suite (`tests/unit/simulation.test.ts:895-944`) explicitly
//! EXCLUDES any day whose move exceeds 40% before asserting the remaining days
//! stay under 50%. The one test that looks at daily moves is written to route
//! around the exact bound this invariant is about.
//!
//! So the hard safety rail that everything else assumes is present has never
//! had a test proving it binds. This is that test.
//!
//! # Adversarial, not representative
//!
//! A test built from plausible inputs would pass without exercising the
//! clamp at all — ordinary ticks land nowhere near ±25%. So the inputs here
//! are chosen to break it: fair values orders of magnitude away from the
//! previous close, `s` pinned at both ends of its cap, and penny stocks where
//! the `$0.01` floor collides with the percentage band.
//!
//! # Both breakers
//!
//! The band is applied TWICE in `simulate_market_tick` — once to the model
//! price (`fairValue × exp(s)`) and once to the settled print. The second is
//! the one that matters: spread widening and multi-level book-walking can
//! settle a print beyond an already-clamped fair value, and that print becomes
//! the next tick's reference. A breaker that bounds an unobservable reference
//! and not the tape is not a breaker. Both are asserted here, via the printed
//! price, which is what a player and a backtest actually see.

use pretium::economy::{create_initial_economy_state, InitialEconomyOptions};
use pretium::market::{
    simulate_market_tick, MarketStatus, SettleDrawPolicy, TickCompany, TickInputs, TickStock,
};
use pretium::rng::Rng;

/// A generator that returns whatever is most likely to break the bound.
///
/// Not zero, and not "realistic" — extremes, so the noise term pushes as hard
/// as it can against the clamp on every tick.
struct Extreme(f64);
impl Rng for Extreme {
    fn next_f64(&mut self) -> f64 {
        self.0.clamp(0.0, 0.999_999)
    }
    fn next_normal(&mut self) -> f64 {
        self.0 * 40.0
    }
}

fn sectors() -> Vec<String> {
    vec!["technology".to_string()]
}

#[allow(clippy::too_many_arguments)]
fn company(price: f64, previous_close: f64, eps: f64, s: Option<f64>) -> TickCompany {
    TickCompany {
        id: "ACME".into(),
        ticker: "ACME".into(),
        sector: "technology".into(),
        is_bankrupt: false,
        is_public: true,
        sector_volatility: Some(1.0),
        sector_avg_pe: Some(32.0),
        eps: Some(eps),
        book_value_per_share: Some(20.0),
        revenue_growth: Some(0.1),
        stock: TickStock {
            price,
            previous_close,
            previous_tick_price: None,
            open: previous_close,
            high: price,
            low: price,
            volume: 0.0,
            avg_volume: 1e6,
            shares_outstanding: 1e8,
            market_cap: price * 1e8,
            mispricing_s: s,
            mispricing_s_prev_close: s,
            mispricing_momentum: Some(0.0),
            maker_inventory: None,
            garch_variance: 0.015 * 0.015,
            last_daily_return: Some(0.0),
            beta: Some(1.0),
            short_interest: 0.0,
            float: 1e8,
        },
    }
}

/// Run one open-market tick and return the printed price.
fn tick_once(mut c: TickCompany, rng_value: f64) -> (f64, f64) {
    let economy = create_initial_economy_state(&InitialEconomyOptions::default());
    let previous_close = c.stock.previous_close;
    let mut roster = vec![c.clone()];
    let mut rng = Extreme(rng_value);
    simulate_market_tick(
        &mut roster,
        &TickInputs {
            economy: &economy,
            market_status: MarketStatus::Open,
            intraday_t: 0.5,
            volatility_multiplier: 0.9,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
            sector_keys: &sectors(),
            settle_draws: SettleDrawPolicy::FourAlways,
        },
        &mut rng,
    );
    c = roster.remove(0);
    (c.stock.price, previous_close)
}

/// The bound, exactly as `market.ts` computes it.
fn assert_within_band(price: f64, previous_close: f64, note: &str) {
    let max = previous_close * 1.25;
    // The floor is a MAX, not a separate clamp: for a penny stock the dollar
    // floor is the binding constraint and the percentage band is not.
    let min = (previous_close * 0.75).max(0.01);
    assert!(
        price <= max,
        "{note}: printed {price} above the +25% ceiling {max} (previousClose {previous_close})"
    );
    assert!(
        price >= min,
        "{note}: printed {price} below the floor {min} (previousClose {previous_close})"
    );
}

#[test]
fn an_extreme_fair_value_jump_upward_cannot_escape_the_band() {
    // The case the invariant names: a fair value orders of magnitude away
    // from the previous close, which is what an earnings gap looks like to
    // the model. Without the clamp the print would follow fair value.
    for eps in [10.0, 100.0, 1_000.0, 100_000.0] {
        for rng_value in [0.0, 0.5, 0.999] {
            let (price, prev) = tick_once(company(100.0, 100.0, eps, None), rng_value);
            assert_within_band(price, prev, &format!("eps {eps}, rng {rng_value}"));
        }
    }
}

#[test]
fn an_extreme_fair_value_collapse_cannot_escape_the_band() {
    for eps in [1.0, 0.1, 0.001, -5.0] {
        for rng_value in [0.0, 0.5, 0.999] {
            let (price, prev) = tick_once(company(100.0, 100.0, eps, None), rng_value);
            assert_within_band(price, prev, &format!("eps {eps}, rng {rng_value}"));
        }
    }
}

#[test]
fn s_pinned_at_either_cap_cannot_escape_the_band() {
    // `s` at ±MISPRICING_CAP is exp(±0.9) — a 2.46x or 0.41x multiplier on
    // fair value, both far outside ±25%.
    for s in [-0.9, -0.5, 0.0, 0.5, 0.9] {
        for eps in [0.5, 4.0, 500.0] {
            let (price, prev) = tick_once(company(100.0, 100.0, eps, Some(s)), 0.75);
            assert_within_band(price, prev, &format!("s {s}, eps {eps}"));
        }
    }
}

#[test]
fn the_band_holds_when_the_price_already_starts_outside_it() {
    // The day-zero gap: 58% of companies begin more than 25% from their own
    // fair value, and the book's gap-chasing flow used to close that in one
    // session. Bounded, it takes several days — which is what a real market
    // does with a halted, badly mispriced name.
    for (price, prev) in [
        (100.0, 40.0),  // price already 2.5x the reference
        (40.0, 100.0),  // price already 0.4x
        (100.0, 1.0),   // absurdly far
        (1.0, 100.0),
    ] {
        for rng_value in [0.0, 0.5, 0.999] {
            let (out, p) = tick_once(company(price, prev, 4.0, None), rng_value);
            assert_within_band(out, p, &format!("price {price} prev {prev}"));
        }
    }
}

#[test]
fn a_penny_stock_is_held_by_the_dollar_floor_not_the_percentage_band() {
    // `max(previousClose * 0.75, 0.01)` — at a previous close of $0.01 the
    // percentage band would allow $0.0075, and the dollar floor forbids it.
    for prev in [0.01, 0.02, 0.05, 0.5] {
        for eps in [-10.0, 0.0001, 1000.0] {
            let (price, p) = tick_once(company(prev, prev, eps, None), 0.5);
            assert_within_band(price, p, &format!("penny prev {prev}, eps {eps}"));
            assert!(price >= 0.01, "penny stock printed {price}, below the $0.01 floor");
        }
    }
}

#[test]
fn the_band_holds_across_a_whole_session_of_adversarial_ticks() {
    // A single tick can be clamped correctly while the band still leaks over a
    // session, because each print becomes the next tick's reference. That
    // compounding is exactly what the pre-fix engine did: measured at 34
    // violations across 17,280 company-days, up to 40.30%.
    //
    // `previousClose` is held fixed here, as it is within a real session —
    // it is set once at the open.
    let economy = create_initial_economy_state(&InitialEconomyOptions::default());
    for rng_value in [0.0f64, 0.25, 0.5, 0.9, 0.999] {
        // A fair value far above the reference, so the clamp binds every tick.
        let mut roster = vec![company(100.0, 100.0, 5_000.0, None)];
        let previous_close = roster[0].stock.previous_close;
        let mut rng = Extreme(rng_value);

        for t in 0..390i64 {
            simulate_market_tick(
                &mut roster,
                &TickInputs {
                    economy: &economy,
                    market_status: MarketStatus::Open,
                    intraday_t: t as f64 / 390.0,
                    volatility_multiplier: 0.9,
                    news: &[],
                    news_impact_queue: &[],
                    order_volumes: &[],
                    sector_keys: &sectors(),
                    settle_draws: SettleDrawPolicy::FourAlways,
                },
                &mut rng,
            );
            assert_within_band(
                roster[0].stock.price,
                previous_close,
                &format!("tick {t}, rng {rng_value}"),
            );
        }
    }
}

#[test]
fn the_band_holds_in_extended_hours_too() {
    // Extended hours skip book settlement, so only the MODEL clamp runs. That
    // is a different code path and it must bound the price just the same.
    let economy = create_initial_economy_state(&InitialEconomyOptions::default());
    for status in [MarketStatus::PreMarket, MarketStatus::AfterHours] {
        for eps in [0.001, 4.0, 10_000.0] {
            let mut roster = vec![company(100.0, 100.0, eps, None)];
            let mut rng = Extreme(0.9);
            simulate_market_tick(
                &mut roster,
                &TickInputs {
                    economy: &economy,
                    market_status: status,
                    intraday_t: 0.0,
                    volatility_multiplier: 0.9,
                    news: &[],
                    news_impact_queue: &[],
                    order_volumes: &[],
                    sector_keys: &sectors(),
                    settle_draws: SettleDrawPolicy::FourAlways,
                },
                &mut rng,
            );
            assert_within_band(roster[0].stock.price, 100.0, &format!("{status:?}, eps {eps}"));
        }
    }
}

#[test]
fn the_clamp_is_actually_binding_and_not_merely_unreached() {
    // Without this, every assertion above could pass because the price never
    // went near the band — a green suite proving nothing.
    //
    // It has to run a SESSION, not a tick, and that is a finding in itself:
    // a single tick cannot reach the band from a standing start however
    // extreme the fair value, because the print comes from the order book and
    // the book is quoted around the CURRENT price. One tick moves the print by
    // about a spread. The gap closes over a session as the flow lean walks
    // price toward fair value — which is when the breaker binds, and is
    // exactly the compounding the print-side clamp was added to stop.
    //
    // Measured: with an extreme fair value the peak lands on 125.0000 and the
    // trough on 75.0000 — the bounds exactly, not near them.
    let economy = create_initial_economy_state(&InitialEconomyOptions::default());
    let mut hit_ceiling = false;
    let mut hit_floor = false;

    for (eps, rng_value) in [(100_000.0f64, 0.0f64), (0.0001, 0.999), (-50.0, 0.5)] {
        let mut roster = vec![company(100.0, 100.0, eps, None)];
        let mut rng = Extreme(rng_value);
        for t in 0..390i64 {
            simulate_market_tick(
                &mut roster,
                &TickInputs {
                    economy: &economy,
                    market_status: MarketStatus::Open,
                    intraday_t: t as f64 / 390.0,
                    volatility_multiplier: 0.9,
                    news: &[],
                    news_impact_queue: &[],
                    order_volumes: &[],
                    sector_keys: &sectors(),
                    settle_draws: SettleDrawPolicy::FourAlways,
                },
                &mut rng,
            );
            let p = roster[0].stock.price;
            if (p - 125.0).abs() < 1e-9 {
                hit_ceiling = true;
            }
            if (p - 75.0).abs() < 1e-9 {
                hit_floor = true;
            }
        }
    }

    assert!(
        hit_ceiling,
        "no case reached the +25% ceiling — this suite would pass without the breaker"
    );
    assert!(
        hit_floor,
        "no case reached the -25% floor — this suite would pass without the breaker"
    );
}

#[test]
fn one_tick_cannot_reach_the_band_from_a_standing_start() {
    // The companion to the above, asserted rather than left as a comment,
    // because it is a real property of the design and a future change could
    // break it without anyone noticing: the book bounds how far a single print
    // can travel, so an extreme fair value does NOT gap the tape in one tick.
    // That is what makes the breaker a backstop rather than the mechanism.
    for eps in [100_000.0f64, 0.0001, -50.0] {
        let (price, _) = tick_once(company(100.0, 100.0, eps, None), 0.5);
        assert!(
            (price - 100.0).abs() < 5.0,
            "eps {eps} moved the print to {price} in ONE tick — the book is no              longer bounding per-tick movement, and the breaker is now load-bearing              where it used to be a backstop"
        );
    }
}
