//! Price factors — the LIVE subset, ported from
//! `src/lib/engine/market.ts:217`.
//!
//! # Only four factors reach the price
//!
//! `calculatePriceFactors` returns fourteen. Under `STATIONARY_PRICE_MODEL`
//! the tick reads exactly four of them:
//!
//! | Factor | Role |
//! |---|---|
//! | `company_news` | news shock into `s` |
//! | `order_flow_impact` | the PERMANENT (information) component of flow |
//! | `short_squeeze_effect` | forced flow — squeezes and stop cascades |
//! | `random_noise` | the GARCH-scaled innovation |
//!
//! The other ten are dead on this path, and **D7 decided they are not
//! ported**: every driver they carried (economic impact, cycle drift, ERP,
//! earnings drift) now lives inside fair value, and every anchor
//! (`meanReversion`, `peMeanReversion`, `valueGrowthEffect`) is replaced by
//! the `s`-process's own reversion. Applying them here too would
//! double-count. The kill switch falls back to TypeScript rather than to a
//! Rust legacy mode, so a Rust copy would be a second dead model.
//!
//! # Draw schedule
//!
//! **Exactly one normal, always** — the idiosyncratic noise term. It is taken
//! partway through the noise computation, BEFORE `crash_amplifier` is
//! evaluated, and that position is contractual: the tape records draws in
//! order, so a port that hoisted the draw to the top of the function would
//! produce the same count and a different stream.

use crate::mathx;

/// The components of one tick's change in `s`, in the order they are
/// reported.
///
/// Declared here, beside the factors, so the Arrow schema and the tick that
/// fills it cannot drift apart -- two hand-written orderings eventually
/// disagree and the columns would silently swap.
///
/// The first three are the model's own dynamics; the last four are the shock
/// factors. Together they account for `Δs` exactly, except where the `s` clamp
/// or a circuit breaker binds -- and a consumer can see that, because the
/// residual against `Δs` stops being zero.
pub const S_COMPONENT_KEYS: [&str; 7] = [
    "reversion",
    "momentum",
    "crowd_lean",
    "company_news",
    "order_flow_impact",
    "short_squeeze_effect",
    "random_noise",
];

/// Total impact coefficient for order flow, before the informed fraction.
pub const ORDER_FLOW_COEFFICIENT: f64 = 50.0;

/// The share of order-flow impact that is PERMANENT.
///
/// The coefficient above was calibrated to represent both the temporary and
/// permanent components, because at the time there was no book and the factor
/// model was the only place impact could live. Order flow now moves the price
/// physically — orders walk real depth and leave the maker skewed — so
/// leaving the full coefficient here would charge flow twice.
///
/// This is a change of ROLE, not a retune. Published permanent/total
/// decompositions land roughly in 0.3–0.5; this sits at the conservative end
/// and is the one number here that still wants empirical calibration.
pub const INFORMED_FLOW_FRACTION: f64 = 0.35;

/// Shared factors, drawn once per tick and reused by every company.
///
/// This is what makes stocks CORRELATED rather than independently random: a
/// single market draw reaches every name through its beta, and a sector draw
/// reaches everything in that sector. Without it a 108-name index would have
/// almost no aggregate volatility, because independent noise cancels.
#[derive(Debug, Clone, PartialEq)]
pub struct SharedFactors {
    pub market_factor: f64,
    /// Indexed by the same sector key order the tick draws them in.
    pub sector_factors: Vec<(String, f64)>,
}

impl SharedFactors {
    fn sector(&self, sector: &str) -> f64 {
        // `sharedFactors.sectorFactors[company.sector] ?? 0` — an absent
        // sector is zero, not a panic.
        self.sector_factors
            .iter()
            .find(|(k, _)| k == sector)
            .map(|(_, v)| *v)
            .unwrap_or(0.0)
    }
}

/// A news event, as the factor model reads it.
#[derive(Debug, Clone, PartialEq)]
pub struct NewsEvent {
    pub company_id: Option<String>,
    pub sector: Option<String>,
    /// `event.impact.priceImpact || 0` — TRUTHY-or, so a zero or NaN impact
    /// contributes nothing.
    pub price_impact: Option<f64>,
}

/// The company fields the live factor subset reads.
#[derive(Debug, Clone, PartialEq)]
pub struct FactorCompany {
    pub id: String,
    pub sector: String,
    pub beta: Option<f64>,
    pub market_cap: f64,
    pub avg_volume: f64,
    pub shares_outstanding: f64,
    pub short_interest: f64,
    pub float: f64,
    pub garch_variance: f64,
    /// The previous COMPLETED day's return, refreshed once at each close.
    pub last_daily_return: Option<f64>,
}

/// The four live factors.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LiveFactors {
    pub company_news: f64,
    pub order_flow_impact: f64,
    pub short_squeeze_effect: f64,
    pub random_noise: f64,
}

/// Small-cap size multiplier, applied to the idiosyncratic component only.
pub fn cap_size_multiplier(market_cap: f64) -> f64 {
    let mcap_billions = market_cap / 1e9;
    if mcap_billions > 50.0 {
        0.8
    } else if mcap_billions > 10.0 {
        1.0
    } else if mcap_billions > 1.0 {
        1.3
    } else {
        1.6
    }
}

/// Compute the live factor subset.
///
/// `shared_factors` is required rather than optional. The fallback branch in
/// the original exists for callers that predate shared factors, and the live
/// tick is not one of them — it always supplies them. Both branches take one
/// normal draw, so this narrowing cannot change the draw COUNT; it removes a
/// path the live model never takes.
pub fn calculate_live_factors(
    company: &FactorCompany,
    news: &[NewsEvent],
    order_imbalance: f64,
    volatility_multiplier: f64,
    shared: &SharedFactors,
    rng: &mut impl crate::rng::Rng,
) -> LiveFactors {
    // ── News ──────────────────────────────────────────────────────────────
    // Company-specific at full weight, sector-wide at half, market-wide at
    // 0.3. The branches are exclusive and ORDERED: an event with both a
    // companyId and a sector counts once, as company news.
    let mut company_news = 0.0;
    for event in news {
        let impact = truthy(event.price_impact);
        if event.company_id.as_deref() == Some(company.id.as_str()) {
            company_news += impact;
        } else if event.sector.as_deref() == Some(company.sector.as_str())
            && event.company_id.is_none()
        {
            company_news += impact * 0.5;
        } else if event.company_id.is_none() && event.sector.is_none() {
            company_news += impact * 0.3;
        }
    }

    // ── Order flow ────────────────────────────────────────────────────────
    // NOTE this is `Math.max`, not the `||` fallback chain
    // `microstructure::base_quote_size` uses on the same field. A zero
    // `avg_volume` is KEPT here and compared, rather than falling through.
    let avg_daily_volume = mathx::max(company.avg_volume, company.shares_outstanding * 0.005);
    // Per-minute volume, floored so a thin name cannot produce unbounded impact.
    let liquidity_factor = 1.0 / mathx::max(avg_daily_volume / 390.0, 100.0);
    let order_flow_impact =
        order_imbalance * liquidity_factor * ORDER_FLOW_COEFFICIENT * INFORMED_FLOW_FRACTION;

    // ── Noise ─────────────────────────────────────────────────────────────
    let beta = company.beta.unwrap_or(1.0);
    let cap_mult = cap_size_multiplier(company.market_cap);

    let market_component = beta * shared.market_factor;
    let sector_component = 0.5 * shared.sector(&company.sector);

    // `garchVariance` is in DAILY units; the tick needs per-tick sigma.
    let daily_sigma = mathx::sqrt(mathx::max(company.garch_variance, 0.0001));
    let idiosyncratic_sigma = daily_sigma / mathx::sqrt(390.0);

    // DRAW SITE — here, before `crash_amplifier` is computed. The order is
    // contractual for the tape even though the two are independent.
    let idiosyncratic_noise =
        rng.next_normal() * idiosyncratic_sigma * cap_mult * volatility_multiplier;

    // Crash correlation: when the market shock is extreme, everything loads
    // more heavily on it and diversification stops working — which is what
    // actually happens in a crash. `market_factor` is already at per-tick
    // scale, so the magnitude is normalised by the same scale.
    //
    // The normaliser is `MARKET_FACTOR_SIGMA` BY NAME, where the reference
    // implementation inlined the value: "extreme" means beyond two standard
    // deviations of the factor's own distribution, so the threshold must
    // follow the sigma. With the literal kept, recalibrating the sigma would
    // silently re-denominate this threshold in the old units — at 3x it
    // would fire on half of all ticks instead of the designed ~5% tail.
    let shock_magnitude = shared.market_factor.abs()
        / (crate::market::tick::MARKET_FACTOR_SIGMA / mathx::sqrt(390.0));
    let crash_amplifier = if shock_magnitude > 2.0 {
        1.0 + (shock_magnitude - 2.0) * 0.2
    } else {
        1.0
    };

    let random_noise = market_component * crash_amplifier + sector_component + idiosyncratic_noise;

    // ── Forced flow ───────────────────────────────────────────────────────
    // Squeezes and stop cascades react to a move that ALREADY happened —
    // margin calls go out on yesterday's close. `last_daily_return` is the
    // previous COMPLETED day's return, so today's forced flow responds to
    // yesterday's move and today's price cannot re-trigger its own amplifier
    // within the day. That self-triggering is what turned the sector process
    // into observed bubble and crash runaways.
    let mut short_squeeze_effect = 0.0;
    let short_interest_ratio = company.short_interest / mathx::max(1.0, company.float);
    let daily_return = company.last_daily_return.unwrap_or(0.0);

    if short_interest_ratio > 0.2 && daily_return > 0.03 {
        short_squeeze_effect = short_interest_ratio * daily_return * 0.5;
        short_squeeze_effect = mathx::min(0.02, short_squeeze_effect);
    }

    // Downside stop cascade — denser stops at the -3/-5/-7% levels.
    if daily_return < -0.02 {
        let drop = daily_return.abs();
        let stop_cascade = if drop > 0.07 {
            0.008
        } else if drop > 0.05 {
            0.005
        } else if drop > 0.03 {
            0.003
        } else {
            0.001
        };
        short_squeeze_effect -= stop_cascade;
    }

    // Upside cascade — short sellers' buy-stops trigger on rallies. Note this
    // is NOT exclusive with the squeeze block above: a heavily-shorted name up
    // 8% gets both, which is the point.
    if daily_return > 0.03 && short_interest_ratio > 0.1 {
        let buy_cascade = if daily_return > 0.07 {
            0.006
        } else if daily_return > 0.05 {
            0.004
        } else {
            0.002
        };
        short_squeeze_effect += buy_cascade;
    }

    LiveFactors {
        company_news,
        order_flow_impact,
        short_squeeze_effect,
        random_noise,
    }
}

/// JavaScript truthiness for `x || 0` on a possibly-absent number.
fn truthy(value: Option<f64>) -> f64 {
    match value {
        Some(x) if x != 0.0 && !x.is_nan() => x,
        _ => 0.0,
    }
}

/// Order imbalance from a tick's pending orders, from `market.ts:1347-1350`.
///
/// Scales with volume RELATIVE to the name's own average, so a 10,000-share
/// order moves a thin stock and not a liquid one — impact is about
/// participation, not absolute size.
pub fn order_imbalance(buy_vol: f64, sell_vol: f64, avg_volume: f64) -> f64 {
    let total_vol = buy_vol + sell_vol;
    // `company.stock.avgVolume || 1000000` — truthy-or, so a zero average
    // falls through to the default rather than dividing by zero.
    let avg_minute_vol = mathx::max(truthy_or(avg_volume, 1_000_000.0) / 390.0, 100.0);
    let volume_ratio = mathx::min(total_vol / avg_minute_vol, 10.0);
    let raw_imbalance = if total_vol > 0.0 {
        (buy_vol - sell_vol) / total_vol
    } else {
        0.0
    };
    // The floor of 0.2 means even a tiny order carries some impact.
    raw_imbalance * mathx::max(0.2, volume_ratio * 0.15)
}

fn truthy_or(value: f64, fallback: f64) -> f64 {
    if value != 0.0 && !value.is_nan() {
        value
    } else {
        fallback
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::rng::Rng;

    struct Fixed(f64);
    impl Rng for Fixed {
        fn next_f64(&mut self) -> f64 {
            self.0
        }
        fn next_normal(&mut self) -> f64 {
            self.0
        }
    }

    fn company() -> FactorCompany {
        FactorCompany {
            id: "ACME".into(),
            sector: "technology".into(),
            beta: Some(1.0),
            market_cap: 20e9,
            avg_volume: 1e6,
            shares_outstanding: 1e8,
            short_interest: 0.0,
            float: 1e8,
            garch_variance: 0.015 * 0.015,
            last_daily_return: Some(0.0),
        }
    }

    fn shared() -> SharedFactors {
        SharedFactors {
            market_factor: 0.0,
            sector_factors: vec![("technology".into(), 0.0)],
        }
    }

    fn factors(
        c: &FactorCompany,
        news: &[NewsEvent],
        imbalance: f64,
        s: &SharedFactors,
    ) -> LiveFactors {
        calculate_live_factors(c, news, imbalance, 1.0, s, &mut Fixed(0.0))
    }

    #[test]
    fn exactly_one_normal_is_drawn_however_the_branches_fall() {
        // The draw count must not depend on news, flow or squeeze state.
        struct Counting(usize);
        impl Rng for Counting {
            fn next_f64(&mut self) -> f64 {
                panic!("the factor subset must not draw uniforms")
            }
            fn next_normal(&mut self) -> f64 {
                self.0 += 1;
                0.0
            }
        }
        for daily_return in [-0.10, -0.03, 0.0, 0.05, 0.10] {
            for short_interest in [0.0, 3e7] {
                let mut c = company();
                c.last_daily_return = Some(daily_return);
                c.short_interest = short_interest;
                let mut rng = Counting(0);
                calculate_live_factors(&c, &[], 0.5, 1.0, &shared(), &mut rng);
                assert_eq!(rng.0, 1, "return {daily_return}, si {short_interest}");
            }
        }
    }

    // ── News ──────────────────────────────────────────────────────────────

    #[test]
    fn news_weights_are_company_then_sector_then_market_wide() {
        let c = company();
        let at = |e: NewsEvent| factors(&c, &[e], 0.0, &shared()).company_news;

        assert_eq!(
            at(NewsEvent {
                company_id: Some("ACME".into()),
                sector: None,
                price_impact: Some(0.1)
            }),
            0.1
        );
        assert_eq!(
            at(NewsEvent {
                company_id: None,
                sector: Some("technology".into()),
                price_impact: Some(0.1)
            }),
            0.1 * 0.5
        );
        assert_eq!(
            at(NewsEvent {
                company_id: None,
                sector: None,
                price_impact: Some(0.1)
            }),
            0.1 * 0.3
        );
    }

    #[test]
    fn an_event_for_another_company_is_ignored_entirely() {
        let c = company();
        // Has a companyId, so it can never fall through to the sector or
        // market-wide arms even though its sector matches.
        let e = NewsEvent {
            company_id: Some("OTHER".into()),
            sector: Some("technology".into()),
            price_impact: Some(0.5),
        };
        assert_eq!(factors(&c, &[e], 0.0, &shared()).company_news, 0.0);
    }

    #[test]
    fn news_accumulates_across_events() {
        let c = company();
        let news = vec![
            NewsEvent {
                company_id: Some("ACME".into()),
                sector: None,
                price_impact: Some(0.1),
            },
            NewsEvent {
                company_id: None,
                sector: None,
                price_impact: Some(0.1),
            },
        ];
        assert_eq!(factors(&c, &news, 0.0, &shared()).company_news, 0.1 + 0.03);
    }

    #[test]
    fn an_absent_or_zero_impact_contributes_nothing() {
        let c = company();
        for impact in [None, Some(0.0), Some(f64::NAN)] {
            let e = NewsEvent {
                company_id: Some("ACME".into()),
                sector: None,
                price_impact: impact,
            };
            assert_eq!(
                factors(&c, &[e], 0.0, &shared()).company_news,
                0.0,
                "{impact:?}"
            );
        }
    }

    // ── Order flow ────────────────────────────────────────────────────────

    #[test]
    fn a_thin_stock_takes_more_impact_from_the_same_imbalance() {
        let mut thin = company();
        thin.avg_volume = 50_000.0;
        thin.shares_outstanding = 1e6;
        let liquid = company();
        assert!(
            factors(&thin, &[], 0.5, &shared()).order_flow_impact
                > factors(&liquid, &[], 0.5, &shared()).order_flow_impact
        );
    }

    #[test]
    fn order_flow_carries_only_the_informed_fraction() {
        // The structural claim behind INFORMED_FLOW_FRACTION: the book now
        // charges the mechanical part, so the factor must not.
        let c = company();
        let out = factors(&c, &[], 1.0, &shared()).order_flow_impact;
        let avg = 1e6f64.max(1e8 * 0.005);
        let expected = 1.0
            * (1.0 / (avg / 390.0).max(100.0))
            * ORDER_FLOW_COEFFICIENT
            * INFORMED_FLOW_FRACTION;
        assert_eq!(out, expected);
    }

    #[test]
    fn order_flow_uses_max_not_the_truthy_fallback_chain() {
        // `microstructure::base_quote_size` falls THROUGH a zero avgVolume;
        // this takes `Math.max` instead, so a zero is compared and loses to
        // the shares-outstanding term rather than being skipped.
        let mut c = company();
        c.avg_volume = 0.0;
        c.shares_outstanding = 1e8;
        let out = factors(&c, &[], 1.0, &shared()).order_flow_impact;
        let expected = 1.0
            * (1.0 / ((1e8f64 * 0.005) / 390.0).max(100.0))
            * ORDER_FLOW_COEFFICIENT
            * INFORMED_FLOW_FRACTION;
        assert_eq!(out, expected);
    }

    #[test]
    fn the_imbalance_scales_with_participation_not_absolute_size() {
        // 10,000 shares is a lot for a thin name and nothing for a liquid one.
        let thin = order_imbalance(10_000.0, 0.0, 50_000.0);
        let liquid = order_imbalance(10_000.0, 0.0, 500e6);
        assert!(thin > liquid, "{thin} vs {liquid}");
        // Balanced flow is neutral however large.
        assert_eq!(order_imbalance(1e6, 1e6, 1e6), 0.0);
        // No orders at all is neutral, not a division by zero.
        assert_eq!(order_imbalance(0.0, 0.0, 1e6), 0.0);
    }

    #[test]
    fn a_zero_average_volume_falls_through_to_the_default_in_the_imbalance() {
        assert!(order_imbalance(1000.0, 0.0, 0.0).is_finite());
        assert_eq!(
            order_imbalance(1000.0, 0.0, 0.0),
            order_imbalance(1000.0, 0.0, 1_000_000.0)
        );
    }

    // ── Forced flow ───────────────────────────────────────────────────────

    #[test]
    fn a_heavily_shorted_name_rallying_gets_both_the_squeeze_and_the_buy_cascade() {
        // The two blocks are deliberately NOT exclusive.
        let mut c = company();
        c.short_interest = 3e7; // 30% of float
        c.float = 1e8;
        c.last_daily_return = Some(0.08);
        let out = factors(&c, &[], 0.0, &shared()).short_squeeze_effect;
        // squeeze = min(0.02, 0.3 * 0.08 * 0.5) = 0.012, plus 0.006 cascade
        assert!((out - 0.018).abs() < 1e-12, "got {out}");
    }

    #[test]
    fn the_squeeze_saturates_rather_than_running_away() {
        let mut c = company();
        c.short_interest = 9e7;
        c.float = 1e8;
        c.last_daily_return = Some(0.9);
        let out = factors(&c, &[], 0.0, &shared()).short_squeeze_effect;
        // 0.02 cap plus the 0.006 cascade — bounded, not proportional to 90%.
        assert!(out <= 0.02 + 0.006 + 1e-12, "got {out}");
    }

    #[test]
    fn a_falling_price_produces_downward_pressure_in_widening_steps() {
        let mut c = company();
        let at = |c: &mut FactorCompany, r: f64| {
            c.last_daily_return = Some(r);
            factors(c, &[], 0.0, &shared()).short_squeeze_effect
        };
        assert_eq!(
            at(&mut c, -0.01),
            0.0,
            "above the -2% trigger, nothing fires"
        );
        assert_eq!(at(&mut c, -0.025), -0.001);
        assert_eq!(at(&mut c, -0.04), -0.003);
        assert_eq!(at(&mut c, -0.06), -0.005);
        assert_eq!(at(&mut c, -0.08), -0.008);
    }

    #[test]
    fn forced_flow_reacts_to_yesterday_not_today() {
        // `last_daily_return` is the previous COMPLETED day. If today's move
        // fed this, a fall would deepen itself within the session — the
        // runaway the comment in the source describes.
        let mut c = company();
        c.last_daily_return = None;
        assert_eq!(factors(&c, &[], 0.0, &shared()).short_squeeze_effect, 0.0);
    }

    // ── Noise ─────────────────────────────────────────────────────────────

    #[test]
    fn the_shared_market_factor_reaches_every_stock_through_beta() {
        let mut s = shared();
        s.market_factor = 0.001;
        let mut high_beta = company();
        high_beta.beta = Some(2.0);
        let low_beta = company();
        assert!(
            factors(&high_beta, &[], 0.0, &s).random_noise
                > factors(&low_beta, &[], 0.0, &s).random_noise
        );
    }

    #[test]
    fn a_crash_sized_market_shock_amplifies_the_market_loading() {
        // Below the 2-sigma threshold the loading is linear in the factor;
        // above it, it steepens. Diversification stops working in a crash.
        // Denominated in the CURRENT sigma, so the test keeps meaning
        // "2 standard deviations of the factor" across recalibrations.
        let tick_scale = crate::market::tick::MARKET_FACTOR_SIGMA / (390.0f64).sqrt();
        let c = company();
        let noise_at = |mult: f64| {
            let mut s = shared();
            s.market_factor = tick_scale * mult;
            factors(&c, &[], 0.0, &s).random_noise
        };
        let linear = noise_at(1.0);
        let amplified = noise_at(4.0);
        // 4x the shock would be 4x the noise if linear; the amplifier makes
        // it more than that.
        assert!(amplified > linear * 4.0, "{amplified} vs {}", linear * 4.0);
    }

    #[test]
    fn a_small_cap_carries_more_idiosyncratic_noise_than_a_mega_cap() {
        assert_eq!(cap_size_multiplier(100e9), 0.8);
        assert_eq!(cap_size_multiplier(20e9), 1.0);
        assert_eq!(cap_size_multiplier(5e9), 1.3);
        assert_eq!(cap_size_multiplier(0.5e9), 1.6);

        let mut small = company();
        small.market_cap = 0.5e9;
        let mut mega = company();
        mega.market_cap = 100e9;
        let mut rng = Fixed(1.0);
        let s = calculate_live_factors(&small, &[], 0.0, 1.0, &shared(), &mut rng).random_noise;
        let mut rng = Fixed(1.0);
        let m = calculate_live_factors(&mega, &[], 0.0, 1.0, &shared(), &mut rng).random_noise;
        assert!(s > m, "small-cap noise {s} should exceed mega-cap {m}");
    }

    #[test]
    fn the_garch_variance_floor_stops_a_dead_stock_freezing() {
        // A zero variance would make sigma zero and the price would stop
        // moving entirely; the 0.0001 floor prevents that.
        let mut c = company();
        c.garch_variance = 0.0;
        let mut rng = Fixed(1.0);
        let out = calculate_live_factors(&c, &[], 0.0, 1.0, &shared(), &mut rng).random_noise;
        assert!(out > 0.0, "noise collapsed to {out}");
    }
}
