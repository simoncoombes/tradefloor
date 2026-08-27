//! Economy state, ported from the reference implementation's economy
//! module and its economy types.
//!
//! # What is here, and what is deliberately absent
//!
//! Only the fields the price-relevant chain reads or writes. `EconomyState`
//! in the reference implementation is wider; the omissions are listed in
//! the port notes under WP3 and summarised here:
//!
//! - **`derived` / `computeDerivedIndicators`** — a 70-field struct that
//!   `updateEconomyDaily` recomputes on its last line and nothing in the
//!   price loop reads (the surface audit §0). Verified to consume **zero** draws,
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
/// Monthly fraction of the inflation gap closed toward the target. The
/// shipped value; `ModelParams::inflation_reversion` carries it at runtime.
pub const INFLATION_MEAN_REVERSION: f64 = 0.55;
/// Hard ceiling on endogenous inflation, percent; `ModelParams::inflation_ceiling` at runtime.
pub const INFLATION_CEILING: f64 = 6.0;
/// Hard floor on endogenous inflation, percent; `ModelParams::inflation_floor` at runtime.
pub const INFLATION_FLOOR: f64 = -1.0;

/// Phillips-curve coefficient.
///
/// **D3, decided:** magnitude `0.20`. The SIGN lives at the use site
/// (`daily.rs`: `-unemployment_gap * PHILLIPS_CURVE_COEFF`), matching
/// the reference implementation — so this constant is positive and must stay positive.
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
/// VIX points added to the target per unit of a DOWN day's index return.
/// The shipped 25.0 with the clamp below gives a worst-case +0.75 to the
/// target and about +0.09 on the day; real markets move the VIX a median of
/// +6.03 points on a session at -3% or worse (FRED VIXCLS against SP500,
/// 2,511 common days to 2026-08). Carried at runtime as
/// `ModelParams::vix_return_gain`.
pub const VIX_RETURN_GAIN: f64 = 25.0;
/// Mean of the five cycle-phase VIX targets, the anchor `vix_cycle_amplitude`
/// scales deviations around: (14 + 18 + 25 + 22 + 16) / 5.
pub const VIX_PHASE_MEAN: f64 = 19.0;
/// The same for an UP day, where the real response is about half the size.
pub const VIX_RETURN_GAIN_UP: f64 = 10.0;
/// The index return is clamped to this before it drives the VIX, so a -10%
/// day and a -3% day produce identical fear at the shipped 0.03.
pub const VIX_RETURN_CLAMP: f64 = 0.03;
/// Ceiling on the whole target excursion (return spike plus inflation and
/// shock adjustments), in VIX points.
pub const VIX_TARGET_SHOCK_CAP: f64 = 12.0;
pub const FISCAL_MULTIPLIER: f64 = 0.30;

/// The VIX level above which crisis-gated behaviour engages: the gold
/// crisis premium and the USD safe-haven drift (`daily.rs`), and — once
/// its owning change lands — the correlation blend in `market/tick.rs`.
///
/// **Decided deviation from the reference**, which gates these at
/// inline literals of 30 (economy) and 40 (the blend). Endogenous VIX
/// cannot reach either: the daily target tops out near 26 without
/// exogenous shocks, and the Python surface never supplies shocks
/// (`python_engine.rs`: `active_shocks: &[]`), so under default
/// operation every crisis gate was dead code. Measured across 12 seeds
/// by 2,520 days (`tools/calibration/results/vix-endogenous-long-
/// 2026-08-21.json`): median 16.5, P90 25.3, hard ceiling 26.57, zero
/// days above 30 or 40 in 42,336 across both envelopes — and the
/// reference's own recorded trajectories agree (max 29.09 across all
/// five economy goldens, so the 30-gates never fire in the vectors
/// either).
///
/// 25.5 is the P94 of the long-run endogenous distribution: 5.7% of
/// days cross, all of them in contraction or trough (28% of contraction
/// days), every 10-year seed crosses, and no 252-day expansion-start
/// window does (48/48 stay below 24.4). That is "rare and real" in this
/// model's own units — the analogue of the 5-6% of real trading days
/// with VIX above 30 — rather than a level chosen for its real-world
/// name and unreachable in-model.
pub const CRISIS_VIX_THRESHOLD: f64 = 25.5;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum CyclePhase {
    Expansion,
    Peak,
    Contraction,
    Trough,
    Recovery,
}

impl CyclePhase {
    /// How much stress this phase carries, on 0 (calm) to 1 (worst).
    ///
    /// The business cycle already runs in this engine and the MARKET has
    /// never read it: `cycle_phase` appears nowhere in `src/market/`. The
    /// central bank reads it -- recession cuts, contraction policy, a
    /// different Taylor coefficient per phase -- while the price process
    /// carries on as though the economy were always expanding.
    ///
    /// So every market parameter is static beside a regime chain that is
    /// already switching, already deterministic, and already drawn on its
    /// own stream. This is the number that lets the market listen.
    ///
    /// The profile is the cycle's own shape rather than a fit: expansion
    /// is calm, the peak is where risk builds unnoticed, contraction is
    /// the worst of it, the trough is bad but improving, and recovery
    /// still carries scar tissue.
    pub fn stress_intensity(self) -> f64 {
        match self {
            CyclePhase::Expansion => 0.0,
            CyclePhase::Peak => 0.25,
            CyclePhase::Contraction => 1.0,
            CyclePhase::Trough => 0.75,
            CyclePhase::Recovery => 0.25,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            CyclePhase::Expansion => "expansion",
            CyclePhase::Peak => "peak",
            CyclePhase::Contraction => "contraction",
            CyclePhase::Trough => "trough",
            CyclePhase::Recovery => "recovery",
        }
    }

    /// Not `FromStr`: the name is the reference implementation's union member, and a failed
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
/// The reference implementation declares most of these as required and reaches for `?? default`
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
    /// because the reference implementation genuinely leaves it unset before the first market
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
/// reference-implementation default parameter.
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

impl ForwardGuidance {
    pub fn as_str(self) -> &'static str {
        match self {
            ForwardGuidance::OngoingIncreases => "ongoing_increases",
            ForwardGuidance::Accommodative => "accommodative",
            ForwardGuidance::AsAppropriate => "as_appropriate",
        }
    }

    /// `Option` for the same reason as [`CyclePhase::from_name`]: a failed
    /// parse is a malformed snapshot, not a user input error.
    pub fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "ongoing_increases" => ForwardGuidance::OngoingIncreases,
            "accommodative" => ForwardGuidance::Accommodative,
            "as_appropriate" => ForwardGuidance::AsAppropriate,
            _ => return None,
        })
    }
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

#[cfg(test)]
mod regime_intensity_tests {
    use super::*;

    #[test]
    fn the_cycle_orders_its_stress_the_way_the_cycle_runs() {
        // Not a fit -- the cycle's own shape. Expansion is calm, the peak
        // is where risk builds unnoticed, contraction is the worst of it,
        // the trough is bad but improving, recovery carries scar tissue.
        assert_eq!(CyclePhase::Expansion.stress_intensity(), 0.0);
        assert!(CyclePhase::Contraction.stress_intensity()
                > CyclePhase::Trough.stress_intensity());
        assert!(CyclePhase::Trough.stress_intensity()
                > CyclePhase::Recovery.stress_intensity());
        assert!(CyclePhase::Recovery.stress_intensity()
                >= CyclePhase::Expansion.stress_intensity());
        for p in [CyclePhase::Expansion, CyclePhase::Peak,
                  CyclePhase::Contraction, CyclePhase::Trough,
                  CyclePhase::Recovery] {
            let v = p.stress_intensity();
            assert!((0.0..=1.0).contains(&v), "{} out of range", p.as_str());
        }
    }
}
