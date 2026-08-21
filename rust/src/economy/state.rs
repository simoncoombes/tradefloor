//! Economy state, ported from `src/lib/engine/economy.ts` and
//! `src/types/economy.ts`.
//!
//! # What is here, and what is deliberately absent
//!
//! Only the fields the price-relevant chain reads or writes. `EconomyState`
//! in the TypeScript is wider; the omissions are listed in
//! `docs/rust-port/REMAINING-WORK.md` under WP3 and summarised here:
//!
//! - **`derived` / `computeDerivedIndicators`** — a 70-field struct that
//!   `updateEconomyDaily` recomputes on its last line and nothing in the
//!   price loop reads (`SURFACE.md` §0). Verified to consume **zero** draws,
//!   which is what makes leaving it out safe: an omission that shifted the
//!   stream would not be an omission, it would be a divergence.
//! - **`SECTOR_SENSITIVITIES` / `calculateSectorEconomicImpact`** — feeds
//!   only the discarded `economicImpact` factor.
//! - **Narrative strings** — announcement text, forward guidance, shock
//!   headlines. The strings are out of scope; **the draws that select them
//!   are not**, and are consumed here exactly where the original consumes
//!   them.

use crate::mathx::clamp_via_min_max as clamp;

/// Days between the monthly data-release blocks.
pub const DAYS_PER_MONTH: i64 = 30;
/// Days between quarterly GDP releases.
pub const DAYS_PER_QUARTER: i64 = 90;

pub const INFLATION_TARGET: f64 = 2.0;

/// Phillips-curve coefficient.
///
/// **D3, decided:** magnitude `0.20`. The SIGN lives at the use site
/// (`daily.rs`: `-unemployment_gap * PHILLIPS_CURVE_COEFF`), matching
/// `economy.ts:718` — so this constant is positive and must stay positive.
///
/// `wasm/src/economy.rs:9` reads `-0.20` and is
/// commented "aligned with JS"; only the stale compiled binary in
/// `wasm/pkg` says `-0.18`, and no source in the repository endorses that.
/// Production has been running a coefficient nobody chose.
pub const PHILLIPS_CURVE_COEFF: f64 = 0.20;

pub const OIL_BASELINE: f64 = 75.0;
pub const OIL_OPEC_INTERVAL: i64 = 90;
pub const GOLD_EQUILIBRIUM_BASE: f64 = 2200.0;
pub const GOLD_MEAN_REVERSION: f64 = 0.002;
pub const VIX_MEAN_REVERSION: f64 = 0.12;
pub const FISCAL_MULTIPLIER: f64 = 0.30;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CyclePhase {
    Expansion,
    Peak,
    Contraction,
    Trough,
    Recovery,
}

impl CyclePhase {
    pub fn as_str(self) -> &'static str {
        match self {
            CyclePhase::Expansion => "expansion",
            CyclePhase::Peak => "peak",
            CyclePhase::Contraction => "contraction",
            CyclePhase::Trough => "trough",
            CyclePhase::Recovery => "recovery",
        }
    }

    /// Not `FromStr`: the name is the TypeScript union member, and a failed
    /// parse is a malformed vector rather than a user input error, so it
    /// returns `Option` rather than a `Result` nobody would read.
    pub fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "expansion" => CyclePhase::Expansion,
            "peak" => CyclePhase::Peak,
            "contraction" => CyclePhase::Contraction,
            "trough" => CyclePhase::Trough,
            "recovery" => CyclePhase::Recovery,
            _ => return None,
        })
    }
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PhaseCharacteristics {
    pub gdp_growth_range: (f64, f64),
    pub unemployment_trend: f64,
    pub inflation_trend: f64,
    pub min_months: f64,
    pub max_months: f64,
    pub next_phase: CyclePhase,
}

pub fn phase_characteristics(phase: CyclePhase) -> PhaseCharacteristics {
    match phase {
        CyclePhase::Expansion => PhaseCharacteristics {
            gdp_growth_range: (2.0, 4.0),
            unemployment_trend: -0.05,
            inflation_trend: 0.015,
            min_months: 6.0,
            max_months: 60.0,
            next_phase: CyclePhase::Peak,
        },
        CyclePhase::Peak => PhaseCharacteristics {
            gdp_growth_range: (0.5, 2.0),
            unemployment_trend: 0.0,
            inflation_trend: 0.015,
            min_months: 2.0,
            max_months: 6.0,
            next_phase: CyclePhase::Contraction,
        },
        CyclePhase::Contraction => PhaseCharacteristics {
            gdp_growth_range: (-3.0, 0.0),
            unemployment_trend: 0.30,
            inflation_trend: -0.02,
            min_months: 4.0,
            max_months: 12.0,
            next_phase: CyclePhase::Trough,
        },
        CyclePhase::Trough => PhaseCharacteristics {
            gdp_growth_range: (-1.0, 0.5),
            unemployment_trend: 0.04,
            inflation_trend: -0.01,
            min_months: 2.0,
            max_months: 6.0,
            next_phase: CyclePhase::Recovery,
        },
        CyclePhase::Recovery => PhaseCharacteristics {
            gdp_growth_range: (1.0, 3.5),
            unemployment_trend: -0.1,
            inflation_trend: 0.01,
            min_months: 4.0,
            max_months: 12.0,
            next_phase: CyclePhase::Expansion,
        },
    }
}

/// The economy fields the ported chain touches.
///
/// A plain struct of `f64` rather than `Option<f64>` everywhere: the
/// TypeScript declares most of these as required and reaches for `?? default`
/// only on the handful that were added later. Those defaults are applied at
/// construction, so the `??` sites in the original become ordinary reads
/// here — with one exception noted at [`EconomyState::market_pe`].
#[derive(Debug, Clone, PartialEq)]
pub struct EconomyState {
    // Interest rates
    pub federal_funds_rate: f64,
    pub prime_rate: f64,
    pub corporate_bond_yield: f64,
    pub treasury_yield_10y: f64,
    pub treasury_yield_2y: f64,
    pub mortgage_rate_30y: f64,

    // Inflation
    pub cpi: f64,
    pub inflation_rate: f64,
    pub core_inflation: f64,

    // Growth
    pub gdp_growth: f64,
    pub gdp: f64,
    pub gdp_trend: [f64; 4],

    // Employment
    pub unemployment_rate: f64,
    pub jobs_created: f64,
    pub labor_force_participation: f64,

    // Currency
    pub usd_index: f64,

    // Commodities
    pub oil_price: f64,
    pub gold_price: f64,
    pub copper_price: f64,

    // Housing
    pub housing_index: f64,
    pub home_starts_monthly: f64,
    pub housing_transaction_volume: f64,

    // Labor-market hysteresis
    pub long_term_unemployment_rate: f64,
    pub structural_unemployment: f64,

    // Sentiment
    pub consumer_confidence: f64,
    pub business_confidence: f64,
    pub fear_greed_index: f64,
    pub vix: f64,

    // Trade
    pub tariff_rate: f64,
    pub trade_balance: f64,

    // Oil supply/demand
    pub oil_inventory_level: f64,
    pub oil_last_opec_day: i64,

    // Wages
    pub wage_growth: f64,

    // Cross-asset tracking
    pub previous_day_market_return: f64,
    pub rolling_market_return_30d: f64,
    /// `economy.marketPE ?? 18` at the cycle-transition sites. Kept optional
    /// because the game genuinely leaves it unset before the first market
    /// tick, and the `?? 18` there is a real fallback rather than a
    /// constructor default.
    pub market_pe: Option<f64>,

    // QE pass-through
    pub qe_pe_boost: f64,

    // Fiscal policy
    pub fiscal_stimulus: f64,
    pub government_debt_to_gdp: f64,

    // Cycle
    pub cycle_phase: CyclePhase,
    pub months_in_current_phase: f64,
    pub recession_probability: f64,
}

/// Options for [`create_initial_economy_state`]. `None` selects the
/// TypeScript default parameter.
#[derive(Debug, Clone, Copy, Default)]
pub struct InitialEconomyOptions {
    pub cycle_phase: Option<CyclePhase>,
    pub inflation_rate: Option<f64>,
    pub gdp_growth: Option<f64>,
    pub unemployment_rate: Option<f64>,
}

pub fn create_initial_economy_state(options: &InitialEconomyOptions) -> EconomyState {
    let cycle_phase = options.cycle_phase.unwrap_or(CyclePhase::Expansion);
    let inflation_rate = options.inflation_rate.unwrap_or(2.0);
    let gdp_growth = options.gdp_growth.unwrap_or(2.5);
    let unemployment_rate = options.unemployment_rate.unwrap_or(4.0);

    let confidence_base = 100.0 + gdp_growth * 5.0 - unemployment_rate * 3.0;
    // `Math.max(40, Math.min(130, x))` in the original, spelled out rather
    // than routed through `clamp` because the original spells it out too.
    let confidence = clamp(confidence_base, 40.0, 130.0);

    let fear_greed_base = match cycle_phase {
        CyclePhase::Expansion => 60.0,
        CyclePhase::Recovery => 55.0,
        CyclePhase::Contraction => 30.0,
        CyclePhase::Trough => 25.0,
        CyclePhase::Peak => 55.0,
    };

    let vix = match cycle_phase {
        CyclePhase::Contraction => 25.0,
        CyclePhase::Trough => 30.0,
        CyclePhase::Peak => 18.0,
        _ => 15.0,
    };

    let federal_funds_rate = 2.5;
    let treasury_yield_10y = 3.5;

    // The same VIX-driven spread formula `update_central_bank` uses, so the
    // opening state is consistent with the first meeting's output.
    let mortgage_spread = clamp(1.5 + (vix - 12.0) * 0.015, 1.5, 2.8);
    let corporate_spread = clamp(1.0 + (vix - 12.0) * 0.02, 0.8, 3.5);
    let prime_rate = federal_funds_rate + 3.0;

    let mut mortgage_rate_30y =
        crate::mathx::max(prime_rate + 0.5, treasury_yield_10y + mortgage_spread);
    if mortgage_rate_30y - treasury_yield_10y > 3.5 {
        mortgage_rate_30y = treasury_yield_10y + 3.5;
    }
    let corporate_bond_yield = crate::mathx::max(
        treasury_yield_10y + 0.8,
        treasury_yield_10y + corporate_spread,
    );

    EconomyState {
        federal_funds_rate,
        prime_rate,
        corporate_bond_yield,
        treasury_yield_10y,
        // NOTE the 0.70/0.30 split here. Every OTHER site in the module uses
        // 0.85/0.15 — `update_economy_daily` and `update_central_bank` both
        // do. The opening state is the odd one out, and it is reproduced
        // rather than harmonised: the first daily step overwrites it anyway,
        // so "fixing" it would change the opening 2Y for no benefit and
        // break parity on day zero.
        treasury_yield_2y: federal_funds_rate * 0.70 + treasury_yield_10y * 0.30,
        mortgage_rate_30y,

        cpi: 100.0,
        inflation_rate,
        core_inflation: inflation_rate,

        gdp_growth,
        gdp: 25000.0,
        gdp_trend: [
            gdp_growth - 0.2,
            gdp_growth - 0.1,
            gdp_growth,
            gdp_growth + 0.1,
        ],

        unemployment_rate,
        jobs_created: 200000.0,
        labor_force_participation: 62.5,

        usd_index: 100.0,

        oil_price: 75.0,
        gold_price: 1900.0,
        copper_price: 4.0,

        housing_index: 100.0,
        home_starts_monthly: 1500000.0,
        housing_transaction_volume: 100.0,

        long_term_unemployment_rate: 1.5,
        structural_unemployment: 4.5,

        consumer_confidence: confidence,
        business_confidence: confidence,
        fear_greed_index: fear_greed_base,
        vix,

        tariff_rate: 5.0,
        trade_balance: -50.0,

        oil_inventory_level: 50.0,
        oil_last_opec_day: 0,

        wage_growth: 3.0,

        previous_day_market_return: 0.0,
        rolling_market_return_30d: 0.0,
        market_pe: Some(18.0),

        qe_pe_boost: 0.0,

        fiscal_stimulus: 0.0,
        government_debt_to_gdp: 100.0,

        cycle_phase,
        months_in_current_phase: 0.0,
        recession_probability: match cycle_phase {
            CyclePhase::Contraction => 0.5,
            CyclePhase::Trough => 0.3,
            _ => 0.1,
        },
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ForwardGuidance {
    OngoingIncreases,
    Accommodative,
    AsAppropriate,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct CentralBankState {
    /// Game timestamps are integer minutes.
    pub last_meeting_date: i64,
    pub next_meeting_date: i64,
    pub target_inflation: f64,
    pub target_unemployment: f64,
    pub qe_active: bool,
    pub qe_monthly_purchases: f64,
    /// Momentum from past decisions. **Real state** — declared, maintained,
    /// and read by the Taylor rule under D1. Under the deployed WASM formula
    /// nothing read it, which is the defect D1 corrects.
    pub hawkish_dovish_score: f64,
    pub forward_guidance: ForwardGuidance,
}

pub fn create_initial_central_bank_state(start_timestamp: i64) -> CentralBankState {
    CentralBankState {
        last_meeting_date: start_timestamp,
        next_meeting_date: start_timestamp + 45 * 24 * 60,
        target_inflation: 2.0,
        target_unemployment: 4.0,
        qe_active: false,
        qe_monthly_purchases: 0.0,
        hawkish_dovish_score: 0.0,
        forward_guidance: ForwardGuidance::AsAppropriate,
    }
}

/// An active economic shock, as `update_economy_daily` reads it.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EconomicShock {
    pub kind: ShockKind,
    pub severity: f64,
    pub gdp_impact: f64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ShockKind {
    OilShock,
    Pandemic,
    War,
    Other,
}
