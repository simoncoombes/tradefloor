//! The counterfactual alignment the 2026-08 stream split exists to provide.
//!
//! Three consumers asked the same question — "what would have happened if I
//! changed only this?" — and the shared stream could not answer it:
//!
//! - **TCA** varies order flow and needs every untouched name bit-identical.
//! - **Pinned-versus-baseline macro** varies the macro path and needs the
//!   market's noise sequence unmoved (demonstrated in `engine.rs`'s tests).
//! - **Cutover** varies the embedder's own consumption (also in
//!   `engine.rs`'s tests).
//!
//! This file demonstrates the first, and does it by CONSTRUCTING the failure
//! the old schedule permitted rather than asserting around it. Settlement
//! draws four uniforms or zero, chosen by guards that read the trajectory —
//! a volume that floors to zero settles nothing and draws nothing. Order
//! flow moves a price, the moved price lifts the volume over the floor, the
//! guard flips, and under the pre-split schedule every consumer after that
//! company is reading a shifted stream: names the trader never touched
//! print different numbers. `scenario.py` measured exactly this mechanism
//! at -4 draws on an older build and could only REPORT it (`draw_delta`).
//!
//! Under [`SettleDrawPolicy::FourAlways`] the four draws are taken whether
//! or not the settle uses them, so the schedule cannot depend on the
//! trajectory and the counterfactual is exact BY CONSTRUCTION — which is
//! what `pretium.tca`'s subtraction rests on.

use pretium::economy::{create_initial_economy_state, InitialEconomyOptions};
use pretium::market::{
    reset_daily_prices, simulate_market_tick, MarketStatus, OrderVolume, SettleDrawPolicy,
    TickCompany, TickInputs, TickStock, MARKET_FACTOR_SIGMA,
};
use pretium::rng::{GameRng, Rng};

/// Counts draws so a schedule shift is observable directly, not only through
/// its downstream price damage.
struct Counting {
    inner: GameRng,
    count: usize,
}

impl Rng for Counting {
    fn next_f64(&mut self) -> f64 {
        self.count += 1;
        self.inner.next_f64()
    }
    fn next_normal(&mut self) -> f64 {
        self.count += 1;
        self.inner.next_normal()
    }
}

fn company(id: &str, price: f64, avg_volume: f64, shares: f64) -> TickCompany {
    TickCompany {
        id: id.to_string(),
        ticker: id.to_string(),
        sector: "technology".to_string(),
        is_bankrupt: false,
        is_public: true,
        sector_volatility: Some(1.0),
        sector_avg_pe: Some(32.0),
        eps: Some(price / 25.0),
        book_value_per_share: Some(price * 0.5),
        revenue_growth: Some(0.1),
        stock: TickStock {
            price,
            previous_close: price,
            previous_tick_price: None,
            open: price,
            high: price,
            low: price,
            volume: 0.0,
            avg_volume,
            shares_outstanding: shares,
            market_cap: price * shares,
            mispricing_s: None,
            mispricing_s_prev_close: None,
            mispricing_momentum: None,
            maker_inventory: None,
            garch_variance: 0.015 * 0.015,
            last_daily_return: None,
            beta: Some(1.0),
            short_interest: 0.0,
            float: shares,
        },
    }
}

/// One session-shaped run at the `simulate_market_tick` level.
///
/// Company "THIN" trades ~250 shares a day, so its per-tick volume floors
/// to zero and settlement early-returns — until enough order-flow impact
/// accumulates in its price that the volume model clears 1 share and the
/// settle starts drawing. "B" and "C" are liquid names the trader never
/// touches.
fn run_world(policy: SettleDrawPolicy, trader_flow: f64) -> (Vec<f64>, usize) {
    let mut companies = vec![
        company("THIN", 40.0, 250.0, 1.0e4),
        company("B", 100.0, 1.0e6, 1.0e8),
        company("C", 220.0, 1.0e6, 1.0e8),
    ];
    let economy = create_initial_economy_state(&InitialEconomyOptions::default());
    let sector_keys = vec!["technology".to_string()];
    let order_volumes = vec![(
        "THIN".to_string(),
        OrderVolume {
            buy: trader_flow,
            sell: 0.0,
        },
    )];

    // The same stream shape either world would have seen before the split:
    // one generator, one position.
    let mut rng = Counting {
        inner: GameRng::new(2026, 99),
        count: 0,
    };

    reset_daily_prices(&mut companies);
    for _ in 0..120 {
        simulate_market_tick(
            &mut companies,
            &TickInputs {
                // The mechanism ships inert; 0.0 is the value that
                // preserves the behaviour these tests pin.
                universe_stress: 0.0,
                volume_state: 0.0,
                volume_idio: &[],
                economy: &economy,
                market_status: MarketStatus::Open,
                // Held mid-session so the intraday volume curve is flat and
                // the volume floor is crossed by IMPACT, not by the clock.
                intraday_t: 0.5,
                volatility_multiplier: 0.7,
                news: &[],
                news_impact_queue: &[],
                order_volumes: &order_volumes,
                sector_keys: &sector_keys,
                // The constant-sigma baseline: these tests predate the factor's
                // variance process and pin behaviour at its baseline level.
                market_sigma_daily: MARKET_FACTOR_SIGMA,
                settle_draws: policy,
                params: &pretium::params::PT_V1,
            },
            &mut rng,
        );
    }
    (
        companies.iter().map(|c| c.stock.price).collect(),
        rng.count,
    )
}

/// The demonstration's precondition, asserted so neither test below can rot
/// into vacuity: the trader's flow really does flip a settlement guard.
/// Under the conditional schedule that is visible as a draw-count change.
#[test]
fn the_constructed_flow_flips_a_settlement_guard() {
    let (_, quiet_draws) = run_world(SettleDrawPolicy::FourOrZero, 0.0);
    let (_, traded_draws) = run_world(SettleDrawPolicy::FourOrZero, 10_000.0);
    assert_ne!(
        quiet_draws, traded_draws,
        "the scenario no longer flips a settle guard; the alignment tests \
         below are not demonstrating anything — retune the thin company"
    );
}

/// The pre-split coupling, kept runnable as documentation: under the
/// conditional schedule, a guard flip in one name shifts the stream for
/// every name after it, and the untraded names print different numbers.
/// This is the behaviour `SettleDrawPolicy::FourOrZero` preserves for
/// replaying recorded reference streams — and the reason it must never be
/// used for a counterfactual.
#[test]
fn under_the_conditional_schedule_untouched_names_diverge() {
    let (quiet, _) = run_world(SettleDrawPolicy::FourOrZero, 0.0);
    let (traded, _) = run_world(SettleDrawPolicy::FourOrZero, 10_000.0);
    assert!(
        quiet[1].to_bits() != traded[1].to_bits()
            || quiet[2].to_bits() != traded[2].to_bits(),
        "expected the shifted stream to reach the untraded names"
    );
}

/// The property the split was built for: same worlds, fixed schedule —
/// the trader's impact stays confined to the name they traded, to the bit,
/// even though their flow flips a settlement guard.
#[test]
fn under_the_fixed_schedule_only_the_traded_name_moves() {
    let (quiet, quiet_draws) = run_world(SettleDrawPolicy::FourAlways, 0.0);
    let (traded, traded_draws) = run_world(SettleDrawPolicy::FourAlways, 10_000.0);

    assert_eq!(
        quiet_draws, traded_draws,
        "the fixed schedule must be flow-invariant"
    );
    assert_ne!(
        quiet[0].to_bits(),
        traded[0].to_bits(),
        "the trade had no impact at all, so confinement is vacuous"
    );
    assert_eq!(
        quiet[1].to_bits(),
        traded[1].to_bits(),
        "an untraded name moved: the counterfactual leaked"
    );
    assert_eq!(
        quiet[2].to_bits(),
        traded[2].to_bits(),
        "an untraded name moved: the counterfactual leaked"
    );
}
