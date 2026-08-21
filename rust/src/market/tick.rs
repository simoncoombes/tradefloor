//! `simulateMarketTick`'s live path, ported from
//! `src/lib/engine/market.ts:1230`.
//!
//! # The order of operations IS the model
//!
//! Four phases, and the boundaries between them matter:
//!
//! 1. **Factors** — per company, compute the four live shock factors. Takes
//!    one normal and one uniform per company. The uniform is *stashed*, not
//!    used: it is consumed in phase 3.
//! 2. **Price** — the `s`-process, then `price = fairValue × exp(s)`, then the
//!    circuit breaker on the MODEL price.
//! 3. **Volume** — consumes the uniforms stashed in phase 1.
//! 4. **Settlement** — the book decides what actually printed, then the
//!    breaker is applied again to the PRINT.
//!
//! The phase-1 uniform being consumed two phases later is the trap here. A
//! port that drew it in phase 3, where it is used, would produce identical
//! counts and a completely different stream — the same class of mistake as
//! WP3's object-literal draw.
//!
//! # The factor model moves fair value OR price, never both
//!
//! `new_prices[i]` after phase 2 is **fair value**, not a price. Phase 4 reads
//! it only as the book's anchor. Treating it as the printed price would
//! double-count every driver, which is why the variable is never assigned to
//! `stock.price` directly.
//!
//! # The breaker is applied twice, deliberately
//!
//! Once to the model price in phase 2, once to the settled print in phase 4.
//! The second is the one that matters: spread widening and multi-level
//! book-walking can settle a print beyond an already-clamped fair value, and
//! because that print becomes the next tick's reference the overshoot
//! compounded within a day. A breaker that bounds an unobservable reference
//! and not the tape is not a breaker. See D6 in the port notes.

use crate::economy::EconomyState;
use crate::fair_value::{compute_fair_value, CompanyValuationInputs, EconomyValuationInputs};
use crate::mathx;
use crate::microstructure::{settle_price_through_book, CompanyMicrostructure, SettleOptions};
use crate::mispricing::{crowd_lean, MISPRICING_CAP, MOMENTUM_THETA};
use crate::rng::Rng;

use super::factors::{
    calculate_live_factors, order_imbalance, FactorCompany, NewsEvent, SharedFactors,
};
use super::hours::{intraday_vol, intraday_volume, MarketStatus};

/// Per-tick decay of the mispricing process: `MISPRICING_PHI ^ (1/390)`.
///
/// **Hardcoded from V8's recorded bits, deliberately not computed** — the same
/// treatment as `MISPRICING_PHI` and for a sharper reason: this one is applied
/// 390 times per simulated day, so a last-bit difference is not a rounding
/// curiosity but a different half-life compounding all session.
///
/// Measured: `libm::pow`, `f64::powf` and V8 all produce `3FEFFFC1E1385E9E`
/// for this input, so the hardcode is belt-and-braces rather than a
/// workaround. It stays hardcoded because agreeing today on one platform is
/// not a guarantee, and the bits are the contract.
pub const S_PHI_TICK: f64 = f64::from_bits(0x3FEF_FFC1_E138_5E9E);

/// Standard deviation of the shared market factor, at DAILY scale.
pub const MARKET_FACTOR_SIGMA: f64 = 0.003;
/// Standard deviation of a shared sector factor, at DAILY scale.
pub const SECTOR_FACTOR_SIGMA: f64 = 0.002;

/// The mutable stock state a tick reads and writes.
#[derive(Debug, Clone, PartialEq)]
pub struct TickStock {
    pub price: f64,
    pub previous_close: f64,
    /// `undefined` until the first tick prints — the TypeScript field is
    /// optional, and a fabricated default here would diverge on tick zero of
    /// any freshly generated company.
    pub previous_tick_price: Option<f64>,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub volume: f64,
    pub avg_volume: f64,
    pub shares_outstanding: f64,
    pub market_cap: f64,
    /// `None` until the first tick adopts the current premium/discount.
    pub mispricing_s: Option<f64>,
    pub mispricing_s_prev_close: Option<f64>,
    pub mispricing_momentum: Option<f64>,
    pub maker_inventory: Option<f64>,
    pub garch_variance: f64,
    pub last_daily_return: Option<f64>,
    pub beta: Option<f64>,
    pub short_interest: f64,
    pub float: f64,
}

/// One company, as the tick sees it.
///
/// A single struct rather than three composed ones. `fair_value`,
/// `factors` and `microstructure` each declare their own narrow input type by
/// convention, and duplicating `id`/`sector`/`beta` across three copies would
/// invite them to disagree. The narrow views are BUILT from this on demand,
/// so there is one source of truth per field.
#[derive(Debug, Clone, PartialEq)]
pub struct TickCompany {
    pub id: String,
    pub ticker: String,
    pub sector: String,
    pub is_bankrupt: bool,
    pub is_public: bool,
    pub stock: TickStock,
    /// `SECTOR_CONFIGS[sector].volatility`, passed in rather than duplicated.
    pub sector_volatility: Option<f64>,
    /// `SECTOR_CONFIGS[sector].avgPe`, for the valuation. Note the casing —
    /// reading `avgPE` yields `undefined` and silently falls back to the
    /// default PE of 18, which is a real bug this port already shipped once.
    pub sector_avg_pe: Option<f64>,
    pub eps: Option<f64>,
    pub book_value_per_share: Option<f64>,
    pub revenue_growth: Option<f64>,
}

impl TickCompany {
    fn valuation(&self) -> CompanyValuationInputs {
        CompanyValuationInputs {
            sector_avg_pe: self.sector_avg_pe,
            eps: self.eps,
            book_value_per_share: self.book_value_per_share,
            revenue_growth: self.revenue_growth,
        }
    }

    fn factor_view(&self) -> FactorCompany {
        FactorCompany {
            id: self.id.clone(),
            sector: self.sector.clone(),
            beta: self.stock.beta,
            market_cap: self.stock.market_cap,
            avg_volume: self.stock.avg_volume,
            shares_outstanding: self.stock.shares_outstanding,
            short_interest: self.stock.short_interest,
            float: self.stock.float,
            garch_variance: self.stock.garch_variance,
            last_daily_return: self.stock.last_daily_return,
        }
    }

    fn micro_view(&self, price: f64) -> CompanyMicrostructure {
        CompanyMicrostructure {
            id: self.id.clone(),
            sector_volatility: self.sector_volatility,
            price,
            market_cap: self.stock.market_cap,
            beta: self.stock.beta,
            float: Some(self.stock.float),
            short_interest: Some(self.stock.short_interest),
            avg_volume: Some(self.stock.avg_volume),
            volume: Some(self.stock.volume),
            shares_outstanding: Some(self.stock.shares_outstanding),
            maker_inventory: self.stock.maker_inventory,
        }
    }
}

/// Pending order volume for one ticker.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct OrderVolume {
    pub buy: f64,
    pub sell: f64,
}

/// A live news-impact entry, for the volume amplifier.
#[derive(Debug, Clone, PartialEq)]
pub struct NewsImpactEntry {
    pub company_id: Option<String>,
    pub sector: Option<String>,
    pub sectors: Vec<String>,
    pub remaining_impact: f64,
    pub reversal_phase: bool,
}

/// Everything the tick needs that is not a company.
#[derive(Debug, Clone)]
pub struct TickInputs<'a> {
    pub economy: &'a EconomyState,
    pub market_status: MarketStatus,
    /// Fraction of the session elapsed, from `hours::intraday_fraction`.
    pub intraday_t: f64,
    pub volatility_multiplier: f64,
    pub news: &'a [NewsEvent],
    pub news_impact_queue: &'a [NewsImpactEntry],
    /// Keyed by ticker.
    pub order_volumes: &'a [(String, OrderVolume)],
    /// Sector keys in the order `SECTOR_CONFIGS` enumerates them. The ORDER
    /// is contractual: one normal is drawn per key, in this order.
    pub sector_keys: &'a [String],
}

/// What one tick produced, beyond the mutations applied to the companies.
#[derive(Debug, Clone, PartialEq)]
pub struct TickOutcome {
    /// Indices into the input slice, in the order the tick processed them.
    pub active_indices: Vec<usize>,
    /// Fair value per active company — the book's anchor, NOT a price.
    pub fair_values: Vec<f64>,
    /// Volume printed per active company.
    pub volumes: Vec<f64>,
    pub shared_factors: SharedFactors,
}

/// Run one simulated market minute.
pub fn simulate_market_tick(
    companies: &mut [TickCompany],
    inputs: &TickInputs,
    rng: &mut impl Rng,
) -> TickOutcome {
    // A closed market changes NOTHING and costs ZERO draws — the guard sits
    // before the shared-factor draws, so a weekend or an overnight hour does
    // not advance the stream at all. Reproducing that position matters as
    // much as reproducing the return: drawing here would desynchronise every
    // consumer on every closed tick, which is most of the clock.
    if inputs.market_status == MarketStatus::Closed {
        return TickOutcome {
            active_indices: Vec::new(),
            fair_values: Vec::new(),
            volumes: Vec::new(),
            shared_factors: SharedFactors {
                market_factor: 0.0,
                sector_factors: Vec::new(),
            },
        };
    }

    let tick_scale_factor = 1.0 / 390.0;
    let tick_scale = 1.0 / mathx::sqrt(390.0);
    let economy = inputs.economy;
    let open = inputs.market_status == MarketStatus::Open;

    // ── Shared factors: 1 normal, then one per sector ─────────────────────
    // Drawn at PER-TICK scale directly, so the noise is not divided by 390
    // again later.
    let market_factor = rng.next_normal() * MARKET_FACTOR_SIGMA * tick_scale;

    // Crisis correlation: above VIX 40 sector factors blend toward the market
    // factor, so diversification stops working exactly when it is most wanted.
    let vix_correlation_spike = if economy.vix > 40.0 {
        mathx::min(0.8, (economy.vix - 40.0) / 30.0)
    } else {
        0.0
    };

    let mut sector_factors = Vec::with_capacity(inputs.sector_keys.len());
    for sector in inputs.sector_keys {
        let idiosyncratic = rng.next_normal() * SECTOR_FACTOR_SIGMA * tick_scale;
        sector_factors.push((
            sector.clone(),
            idiosyncratic * (1.0 - vix_correlation_spike) + market_factor * vix_correlation_spike,
        ));
    }
    let shared = SharedFactors {
        market_factor,
        sector_factors,
    };

    let intraday_vol_mult = intraday_vol(inputs.intraday_t);
    let intraday_volume_mult = intraday_volume(inputs.intraday_t, inputs.market_status);

    // ── Phase 1: factors ──────────────────────────────────────────────────
    let mut active_indices: Vec<usize> = Vec::new();
    let mut all_drifts: Vec<f64> = Vec::new();
    let mut all_noises: Vec<f64> = Vec::new();
    let mut all_news_vol_mults: Vec<f64> = Vec::new();
    let mut all_randoms: Vec<f64> = Vec::new();

    for (idx, company) in companies.iter().enumerate() {
        if company.is_bankrupt || !company.is_public {
            continue;
        }
        active_indices.push(idx);

        let vol = inputs
            .order_volumes
            .iter()
            .find(|(t, _)| t == &company.ticker)
            .map(|(_, v)| *v)
            .unwrap_or_default();
        let imbalance = order_imbalance(vol.buy, vol.sell, company.stock.avg_volume);

        // DRAW SITE: one normal, inside the factor computation.
        let factors = calculate_live_factors(
            &company.factor_view(),
            inputs.news,
            imbalance,
            inputs.volatility_multiplier,
            &shared,
            rng,
        );

        // Only the SHOCK factors reach the price. Every driver lives in fair
        // value now, and every anchor is replaced by `s`'s own reversion.
        let (drift, noise) = if open {
            (
                (factors.company_news + factors.order_flow_impact + factors.short_squeeze_effect)
                    * tick_scale_factor,
                factors.random_noise,
            )
        } else {
            (
                (factors.company_news + factors.order_flow_impact) * tick_scale_factor,
                factors.random_noise * 0.15,
            )
        };
        all_drifts.push(drift);
        all_noises.push(noise);

        // News amplifies volume, capped at 10x.
        let mut news_volume_mult = 1.0;
        let mut impact_sum = 0.0;
        for entry in inputs.news_impact_queue {
            if entry.reversal_phase {
                continue;
            }
            let matches = entry.company_id.as_deref() == Some(company.id.as_str())
                || entry.sector.as_deref() == Some(company.sector.as_str())
                || entry.sectors.iter().any(|s| s == &company.sector);
            if matches {
                impact_sum += entry.remaining_impact;
            }
        }
        if impact_sum > 0.0 {
            news_volume_mult = 1.0 + mathx::min(9.0, impact_sum / 0.01);
        }
        all_news_vol_mults.push(news_volume_mult);

        // DRAW SITE: one uniform, STASHED. It is consumed in phase 3, and
        // drawing it there instead would give the same count on a different
        // stream.
        all_randoms.push(rng.next_f64());
    }

    let active_count = active_indices.len();
    let previous_closes: Vec<f64> = active_indices
        .iter()
        .map(|&i| companies[i].stock.previous_close)
        .collect();
    let current_prices: Vec<f64> = active_indices
        .iter()
        .map(|&i| companies[i].stock.price)
        .collect();

    // ── Phase 2: the s-process ────────────────────────────────────────────
    let econ_view = EconomyValuationInputs {
        corporate_bond_yield: Some(economy.corporate_bond_yield),
        federal_funds_rate: economy.federal_funds_rate,
        qe_pe_boost: Some(economy.qe_pe_boost),
    };

    let mut new_prices = vec![0.0; active_count];
    let mut crowd_leans = vec![0.0; active_count];

    for i in 0..active_count {
        let idx = active_indices[i];
        let breakdown = compute_fair_value(&companies[idx].valuation(), &econ_view);
        let fv = breakdown.fair_value;

        // Lazy init: adopt the current premium/discount as the starting `s`,
        // so enabling the model — or loading an old save — causes no level
        // jump.
        if companies[idx].stock.mispricing_s.is_none() {
            let s0 = clamp_s(mathx::log(mathx::max(0.01, current_prices[i]) / fv));
            companies[idx].stock.mispricing_s = Some(s0);
            companies[idx].stock.mispricing_s_prev_close = Some(s0);
            companies[idx].stock.mispricing_momentum = Some(0.0);
        }

        let mut s_val = companies[idx].stock.mispricing_s.unwrap();
        let momentum = companies[idx].stock.mispricing_momentum.unwrap_or(0.0);

        if open {
            // The crowd reacts to the mispricing it can SEE — the pre-update
            // state — so there is no same-tick feedback loop.
            let lean = crowd_lean(s_val, momentum);
            crowd_leans[i] = lean;
            // Written exactly as the source writes it. `s*φ` and
            // `s + s*(φ-1)` round differently, and the benched trajectories
            // were produced by this spelling.
            s_val = s_val * S_PHI_TICK
                + (MOMENTUM_THETA * momentum) / 390.0
                + lean / 390.0
                + all_drifts[i]
                + all_noises[i] * intraday_vol_mult;
        } else {
            s_val = s_val + all_drifts[i] + all_noises[i];
        }
        s_val = clamp_s(s_val);

        let mut new_price = mathx::max(0.01, fv * mathx::exp(s_val));

        // Breaker #1 — the MODEL price. If it binds, `s` is re-derived from
        // the clamped price so state and price stay consistent.
        let max_price = previous_closes[i] * 1.25;
        let min_price = mathx::max(previous_closes[i] * 0.75, 0.01);
        if new_price > max_price || new_price < min_price {
            new_price = mathx::max(min_price, mathx::min(max_price, new_price));
            s_val = clamp_s(mathx::log(new_price / fv));
        }

        companies[idx].stock.mispricing_s = Some(s_val);
        new_prices[i] = mathx::min(new_price, 50000.0);
    }

    // ── Phase 3: volume ───────────────────────────────────────────────────
    let volume_multiplier = if open { 1.0 } else { 0.1 };
    let mut volumes = vec![0.0; active_count];
    for i in 0..active_count {
        let idx = active_indices[i];
        let stock = &companies[idx].stock;
        // Zero-guard: a newly listed company before `resetDailyPrices` seeds
        // `open` would divide by zero and propagate NaN into the batch.
        let daily_change = if stock.open > 0.0 {
            ((new_prices[i] - stock.open) / stock.open).abs()
        } else {
            0.0
        };
        let base_volume = stock.avg_volume / 390.0;
        let price_magnitude = daily_change / 0.01;
        // The stashed uniform from phase 1, consumed here.
        let volume_scale = 0.6 + 0.6 * mathx::min(price_magnitude, 4.0) + all_randoms[i] * 0.2;
        volumes[i] = (base_volume
            * volume_multiplier
            * volume_scale
            * intraday_volume_mult
            * all_news_vol_mults[i])
            .floor();
    }

    // ── Phase 4: settlement ───────────────────────────────────────────────
    for i in 0..active_count {
        let idx = active_indices[i];
        let fair_value = new_prices[i];
        let volume = volumes[i].floor();

        let mut new_price = fair_value;
        if open {
            // DRAW SITE: four uniforms, or zero on an early return.
            let micro = companies[idx].micro_view(companies[idx].stock.price);
            let settled = settle_price_through_book(
                &micro,
                fair_value,
                volume,
                &SettleOptions {
                    vix: economy.vix,
                    difficulty: None,
                    flow_lean: Some(crowd_leans[i]),
                },
                rng,
            );
            new_price = settled.price;

            // Breaker #2 — the PRINT. See the module header for why this one
            // is the load-bearing clamp.
            let print_max = previous_closes[i] * 1.25;
            let print_min = mathx::max(previous_closes[i] * 0.75, 0.01);
            if new_price > print_max || new_price < print_min {
                new_price = mathx::max(print_min, mathx::min(print_max, new_price));
            }

            // Carry maker inventory forward. This is what makes impact
            // PERSIST: a large buy leaves the maker short, so it keeps quoting
            // higher until opposing flow lets it unwind.
            if settled.maker_inventory_delta != 0.0 {
                companies[idx].stock.maker_inventory = Some(
                    companies[idx].stock.maker_inventory.unwrap_or(0.0)
                        + settled.maker_inventory_delta,
                );
            }
        }

        let stock = &mut companies[idx].stock;
        stock.previous_tick_price = Some(stock.price);
        stock.price = new_price;
        stock.high = mathx::max(stock.high, new_price);
        stock.low = mathx::min(stock.low, new_price);
        stock.volume += volume;
        stock.market_cap = new_price * stock.shares_outstanding;
    }

    TickOutcome {
        active_indices,
        fair_values: new_prices,
        volumes,
        shared_factors: shared,
    }
}

fn clamp_s(s: f64) -> f64 {
    // `Math.max(-CAP, Math.min(CAP, s))` — the min/max spelling, not the
    // ternary, matching the source.
    mathx::max(-MISPRICING_CAP, mathx::min(MISPRICING_CAP, s))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn s_phi_tick_matches_the_recorded_v8_bits() {
        assert_eq!(S_PHI_TICK.to_bits(), 0x3FEF_FFC1_E138_5E9E);
    }

    #[test]
    fn s_phi_tick_compounds_to_the_daily_phi_across_a_session() {
        // 390 ticks of the per-tick decay must reproduce one day of the daily
        // decay. That relationship is the whole reason the constant exists.
        let mut compounded = 1.0f64;
        for _ in 0..390 {
            compounded *= S_PHI_TICK;
        }
        assert!(
            (compounded - crate::mispricing::MISPRICING_PHI).abs() < 1e-12,
            "390 ticks compounded to {compounded}, daily phi is {}",
            crate::mispricing::MISPRICING_PHI
        );
    }
}
