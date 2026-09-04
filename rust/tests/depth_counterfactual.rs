//! The depth counterfactual's two unasserted claims.
//!
//! Both are stated in comments beside the code in
//! `rust/src/market/tick.rs` and neither had a test. A review mutated each
//! one and watched the whole suite stay green, which is the definition of an
//! unguarded claim in this repository.
//!
//! # Adversarial, not representative
//!
//! The shipped roster never puts a counterfactual print outside the circuit
//! breaker's band, so a test built from plausible inputs passes whether or
//! not the clamp is there. The inputs below are built to reach it: a
//! previous close set so the band cuts between the two prints, and flow
//! large enough that the deeper book keeps filling after the shallow one has
//! run out.

use tradefloor::economy::{create_initial_economy_state, InitialEconomyOptions};
use tradefloor::market::{
    simulate_market_tick, MarketStatus, SettleDrawPolicy, TickCompany, TickInputs, TickStock,
    MARKET_FACTOR_SIGMA,
};
use tradefloor::microstructure::{settle_price_through_book, SettleOptions};
use tradefloor::rng::Rng;

/// One fixed uniform and one fixed normal, so a tick's draws can be replayed
/// by hand outside it.
struct Fixed(f64);
impl Rng for Fixed {
    fn next_f64(&mut self) -> f64 {
        self.0
    }
    fn next_normal(&mut self) -> f64 {
        0.0
    }
}

fn sectors() -> Vec<String> {
    vec!["technology".to_string()]
}

fn company(price: f64, previous_close: f64, avg_volume: f64) -> TickCompany {
    TickCompany {
        id: "ACME".into(),
        ticker: "ACME".into(),
        sector: "technology".into(),
        is_bankrupt: false,
        is_public: true,
        sector_volatility: Some(1.0),
        sector_avg_pe: Some(32.0),
        eps: Some(4.0),
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
            avg_volume,
            shares_outstanding: 1e8,
            market_cap: price * 1e8,
            mispricing_s: Some(0.0),
            mispricing_s_prev_close: Some(0.0),
            mispricing_momentum: Some(0.0),
            maker_inventory: None,
            garch_variance: 0.015 * 0.015,
            garch_cascade: [0.015 * 0.015; tradefloor::market::garch::CASCADE_MAX],
            last_daily_return: Some(0.0),
            beta: Some(1.0),
            short_interest: 0.0,
            float: 1e8,
        },
    }
}

/// The one construction all three tests run on.
///
/// A maker short far past its inventory limit quotes an ask side thinned to
/// its floor, so four buy slices exhaust the two levels the depth bound
/// allows and keep filling against all ten. Ordinary inputs never reach the
/// bound -- the book is built so they do not -- which is why this is built
/// rather than sampled.
///
/// `avg_volume` of 1e4 puts one level at 100 shares against a tick that
/// prints 82, and `previous_close` sets where the breaker's ceiling falls.
fn thin_ask_book(previous_close: f64) -> TickCompany {
    let mut c = company(100.0, previous_close, 1e4);
    c.stock.maker_inventory = Some(-5e4);
    c
}

struct Run {
    printed: f64,
    unbounded: f64,
    share: f64,
    shock: f64,
    absorbed: f64,
    clamp: f64,
    fair_value: f64,
    volume: f64,
    band: (f64, f64),
    last_print: f64,
}

/// One open tick with the depth counterfactual on, and everything needed to
/// reproduce its settlement by hand.
fn tick(c: TickCompany, uniform: f64, volatility: f64) -> (Run, TickCompany, TickCompany) {
    let economy = create_initial_economy_state(&InitialEconomyOptions::default());
    let before = c.clone();
    let params = &tradefloor::params::PT_V1;
    let mut roster = vec![c];
    let outcome = simulate_market_tick(
        &mut roster,
        &TickInputs {
            prev_day_down: false,
            forced_flow_eff: 1.0,
            universe_stress: 0.0,
            volume_state: 0.0,
            volume_idio: &[],
            economy: &economy,
            market_status: MarketStatus::Open,
            intraday_t: 0.5,
            volatility_multiplier: volatility,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
            sector_keys: &sectors(),
            market_sigma_daily: MARKET_FACTOR_SIGMA,
            settle_draws: SettleDrawPolicy::FourAlways,
            settle_depth_counterfactual: true,
            // The run's opening nominal output. The growth term is
            // off on every preset these tests pin, so it is read
            // nowhere; this tick's own value is what a single-tick
            // caller opens at.
            nominal_output_base: economy.gdp * economy.cpi,
            // Trading days closed. The buyback factor is off on
            // every preset these tests pin, so it is read
            // nowhere; 0 is what a single-tick caller opens at.
            elapsed_days: 0,
            params,
        },
        &mut Fixed(uniform),
    );
    let previous_close = before.stock.previous_close;
    let run = Run {
        printed: roster[0].stock.price,
        unbounded: outcome.unbounded_print[0],
        share: outcome.liquidity_share[0],
        shock: outcome.shock[0],
        absorbed: outcome.absorbed[0],
        clamp: outcome.clamp[0],
        fair_value: outcome.fair_values[0],
        volume: outcome.volumes[0].floor(),
        band: (
            f64::max(previous_close * params.breaker_down, 0.01),
            previous_close * params.breaker_up,
        ),
        last_print: before.stock.price,
    };
    let after = roster.remove(0);
    (run, before, after)
}

fn settle_options(vix: f64) -> SettleOptions {
    let params = &tradefloor::params::PT_V1;
    SettleOptions {
        spread_size_smoothness: params.spread_size_smoothness,
        spread_size_exponent: params.spread_size_exponent,
        vix,
        difficulty: None,
        // Zero rather than the tick's own crowd lean. The assertions below
        // are equalities, so a preset whose crowd moved this would fail them
        // rather than pass on a coincidence.
        flow_lean: Some(0.0),
        depth_multiplier: f64::INFINITY,
    }
}

/// The counterfactual print goes through the same circuit breaker the real
/// print goes through.
///
/// `rust/src/market/tick.rs` hoists the band above the settlement so both
/// arms are clamped by it. Comparing a clamped print against an unclamped one
/// would book the breaker's own work to liquidity, and on a halted name that
/// is most of the move.
#[test]
fn the_counterfactual_print_is_clamped_by_the_same_breaker() {
    // The previous close puts the band's ceiling between the two prints, so
    // the real print is inside the band and the deeper book's is not.
    let (run, before, _) = tick(thin_ask_book(80.32), 0.02, 1.0);
    let vix = create_initial_economy_state(&InitialEconomyOptions::default()).vix;

    // What the deeper book settled at before anything clamped it.
    let unclamped = settle_price_through_book(
        &before.micro_view(before.stock.price),
        run.fair_value,
        run.volume,
        &settle_options(vix),
        &mut Fixed(0.02),
    )
    .price;

    assert!(
        unclamped > run.band.1,
        "this tick does not walk the deeper book past the ceiling: {} against \
         a band of {:?}",
        unclamped,
        run.band
    );
    assert!(
        run.printed < run.band.1 && run.printed > run.band.0,
        "the real print should sit strictly inside the band, so the two arms \
         are told apart by the clamp rather than by both reaching it: {} in \
         {:?}",
        run.printed,
        run.band
    );
    assert_eq!(
        run.unbounded, run.band.1,
        "the counterfactual print should be clamped to the ceiling rather \
         than reported at {unclamped}"
    );
}

/// The arm settles from the price the real settlement saw.
///
/// It quotes against the company as it stood BEFORE the tick printed, not
/// against the price the tick just produced. Anchoring on the settled price
/// would re-centre the book on a price no settlement had reached yet, and the
/// arm would stop being the same tick.
#[test]
fn the_arm_settles_from_the_price_the_real_settlement_saw() {
    let (run, before, after) = tick(thin_ask_book(100.0), 0.02, 1.0);
    let vix = create_initial_economy_state(&InitialEconomyOptions::default()).vix;

    // The book the arm should have quoted against: the company as it was
    // before the settlement, at the price the tape carried into this tick.
    let from_before = settle_price_through_book(
        &before.micro_view(before.stock.price),
        run.fair_value,
        run.volume,
        &settle_options(vix),
        &mut Fixed(0.02),
    )
    .price;

    // The book it would quote against if it read the price the settlement
    // left behind. This is the mutation the review made survive.
    let from_after = settle_price_through_book(
        &after.micro_view(after.stock.price),
        run.fair_value,
        run.volume,
        &settle_options(vix),
        &mut Fixed(0.02),
    )
    .price;

    assert!(
        from_before != from_after,
        "the two anchors give the same print here, so this tick cannot tell \
         them apart: before {} after {}",
        before.stock.price,
        after.stock.price
    );
    assert_eq!(
        run.unbounded, from_before,
        "the arm's print should be the settlement from the pre-tick price"
    );
    assert!(
        run.unbounded != from_after,
        "the arm's print matches the settlement from the POST-tick price"
    );
}

/// The breaker's share of the absorption is reported apart from the book's.
///
/// On the same tick the clamp test uses, the deeper book walks past the
/// ceiling and the breaker pulls it back. `clamp` is that pull; `absorbed`
/// is the whole distance from the model price to the tape; the difference is
/// the book. The two oppose, and one column carrying both would report a
/// halted print as having absorbed nothing.
#[test]
fn the_clamp_is_the_breakers_share_and_the_book_is_the_rest() {
    // A previous close that puts the ceiling below where the real settlement
    // wants to print, so the REAL print is clamped rather than the arm's.
    let (run, _, _) = tick(thin_ask_book(80.0), 0.02, 1.0);
    assert!(
        run.clamp != 0.0,
        "this tick does not halt the print, so it cannot test the column"
    );
    assert_eq!(
        run.printed, run.band.1,
        "a clamped print sits on the band edge"
    );

    let moved = (run.printed / run.last_print).ln();
    let book = run.absorbed - run.clamp;
    assert!(
        (run.shock + book + run.clamp - moved).abs() < 1e-12,
        "shock {} + book {book} + clamp {} should be the move {moved}",
        run.shock,
        run.clamp
    );
    assert!(
        book * run.clamp < 0.0,
        "the book and the breaker pull opposite ways on a clamped print: \
         book {book}, clamp {}",
        run.clamp
    );
}

/// The arm does not run on the recorded-stream replay path, and reports
/// nothing rather than reporting the real print twice.
///
/// Under `SettleDrawPolicy::FourOrZero` the settlement draws lazily from the
/// caller's source and there is no buffer to rewind, so a second settlement
/// would take draws off a recorded tape. The arm is skipped. Without the
/// policy half of the condition the columns would still be allocated, the
/// settlement would never run, and the fallback would write the real print
/// into `unbounded_print` with a share of zero: a table saying unbounded
/// depth would have printed the same, on a run where nothing was measured.
#[test]
fn the_arm_reports_nothing_on_the_replay_path() {
    let economy = create_initial_economy_state(&InitialEconomyOptions::default());
    let mut roster = vec![thin_ask_book(100.0)];
    let outcome = simulate_market_tick(
        &mut roster,
        &TickInputs {
            prev_day_down: false,
            forced_flow_eff: 1.0,
            universe_stress: 0.0,
            volume_state: 0.0,
            volume_idio: &[],
            economy: &economy,
            market_status: MarketStatus::Open,
            intraday_t: 0.5,
            volatility_multiplier: 1.0,
            news: &[],
            news_impact_queue: &[],
            order_volumes: &[],
            sector_keys: &sectors(),
            market_sigma_daily: MARKET_FACTOR_SIGMA,
            settle_draws: SettleDrawPolicy::FourOrZero,
            settle_depth_counterfactual: true,
            // The run's opening nominal output. The growth term is
            // off on every preset these tests pin, so it is read
            // nowhere; this tick's own value is what a single-tick
            // caller opens at.
            nominal_output_base: economy.gdp * economy.cpi,
            // Trading days closed. The buyback factor is off on
            // every preset these tests pin, so it is read
            // nowhere; 0 is what a single-tick caller opens at.
            elapsed_days: 0,
            params: &tradefloor::params::PT_V1,
        },
        &mut Fixed(0.02),
    );
    assert_eq!(outcome.active_indices.len(), 1, "the tick should have run");
    assert!(
        outcome.unbounded_print.is_empty(),
        "the replay path has no buffer to rewind, so it must report no \
         counterfactual: {:?}",
        outcome.unbounded_print
    );
    assert!(outcome.liquidity_share.is_empty());
    // The decomposition is NOT skipped: it costs no draw and needs no
    // buffer, so a replay still says how far the print sat from the model
    // price.
    assert_eq!(outcome.shock.len(), 1);
    assert_eq!(outcome.absorbed.len(), 1);
}

/// The same tick under the generated schedule DOES report one, so the test
/// above is about the policy rather than about the inputs.
#[test]
fn the_same_tick_reports_a_counterfactual_under_the_generated_schedule() {
    let (run, _, _) = tick(thin_ask_book(100.0), 0.02, 1.0);
    assert!(run.unbounded != run.printed);
}

/// The share is negative where the depth bound truncated the walk.
///
/// A bounded book quotes fewer resting levels, so a sweep runs out of book
/// and stops earlier than the same sweep against every level. The real print
/// therefore sits between the last print and the unbounded print, and the
/// ratio of the two log distances is negative. The unbounded move is
/// `1 - share` times the printed move, which is the reading the columns are
/// documented against.
#[test]
fn the_share_is_negative_where_the_bound_truncates_the_walk() {
    let (run, _, _) = tick(thin_ask_book(100.0), 0.02, 1.0);
    assert!(
        run.unbounded != run.printed,
        "this tick does not reach the bound"
    );
    assert!(
        run.share < 0.0,
        "the bound truncates a walk, so the share is negative: {}",
        run.share
    );

    let printed_move = (run.printed / run.last_print).ln();
    let unbounded_move = (run.unbounded / run.last_print).ln();
    assert!(
        (unbounded_move - printed_move * (1.0 - run.share)).abs() < 1e-12,
        "the unbounded move should be (1 - share) times the printed move: \
         {} against {}",
        unbounded_move,
        printed_move * (1.0 - run.share)
    );
}
