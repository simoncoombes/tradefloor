//! The constructors a host uses in place of struct literals.
//!
//! Every struct a host builds is `#[non_exhaustive]`, so outside this crate
//! the constructors are the only way to make one. This file is outside the
//! crate on purpose: it is what a host compiles against, and it checks that
//! each constructor gives what its documentation promises.

use tradefloor::economy::{
    create_initial_central_bank_state, create_initial_economy_state, CentralBankState,
    EconomicShock, EconomyState, InitialEconomyOptions, ShockKind,
};
use tradefloor::engine::{DayAdvanceRequest, DayCloseRequest, Engine, EngineRngState, TickRequest};
use tradefloor::market::{AvgVolumePolicy, GameTime, OrderVolume, TickCompany, TickStock};
use tradefloor::rng::RngState;
use tradefloor::universe::{random_universe, InstrumentInit};
use tradefloor::{ENGINE_RNG_STATE_WIDTH, RNG_STREAM_WIDTH};

fn sectors() -> Vec<String> {
    tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect()
}

#[test]
fn a_host_built_company_matches_the_library_mapping() {
    // The recipe TickStock::new documents, applied by hand, gives the same
    // company InstrumentInit::to_tick_company builds.
    let mut init = InstrumentInit::new("ACME", "technology", 120.0, 5e7);
    init.eps = Some(6.0);
    init.book_value_per_share = Some(30.0);
    init.revenue_growth = Some(0.08);
    init.avg_volume = 2e6;
    init.beta = 1.3;
    init.short_interest = 4e5;
    let library = init.to_tick_company(7);

    let sector = tradefloor::sectors::by_key("technology").unwrap();
    let base = sector.base_daily_variance();
    let mut stock = TickStock::new(120.0, 5e7);
    stock.avg_volume = 2e6;
    stock.garch_variance = base;
    stock.garch_cascade = [base; tradefloor::market::garch::CASCADE_MAX];
    stock.beta = Some(1.3);
    stock.short_interest = 4e5;
    let mut host = TickCompany::new("ACME-7", "ACME", "technology", stock);
    host.sector_volatility = Some(sector.volatility);
    host.sector_avg_pe = Some(sector.avg_pe);
    host.eps = Some(6.0);
    host.book_value_per_share = Some(30.0);
    host.revenue_growth = Some(0.08);

    assert_eq!(host, library);
}

#[test]
fn the_state_defaults_are_the_library_openings() {
    assert_eq!(
        EconomyState::default(),
        create_initial_economy_state(&InitialEconomyOptions::default())
    );
    assert_eq!(CentralBankState::default(), create_initial_central_bank_state(0));
    assert_eq!(CentralBankState::new(1440), create_initial_central_bank_state(1440));

    let mut options = InitialEconomyOptions::default();
    options.inflation_rate = Some(4.5);
    assert_eq!(create_initial_economy_state(&options).inflation_rate, 4.5);
}

#[test]
fn the_requests_open_at_their_documented_defaults() {
    let bell = GameTime::new(9, 30, 3);
    let tick = TickRequest::new(bell);
    assert_eq!(tick.time, bell);
    assert_eq!(tick.volatility_multiplier, 1.0);
    assert!(tick.news.is_empty() && tick.news_impact_queue.is_empty());
    assert!(tick.order_volumes.is_empty());

    let close = DayCloseRequest::new(&[], &[]);
    assert_eq!(close.avg_volume, AvgVolumePolicy::Hold);

    let shocks = [EconomicShock::new(ShockKind::OilShock, 0.5, -0.2)];
    let mut advance = DayAdvanceRequest::new(3, 3 * 1440);
    assert_eq!((advance.volatility, advance.market_return_pct), (1.0, 0.0));
    advance.active_shocks = &shocks;
    assert_eq!(advance.active_shocks[0].kind, ShockKind::OilShock);

    assert_eq!(OrderVolume::new(5.0, 2.0), {
        let mut v = OrderVolume::default();
        v.buy = 5.0;
        v.sell = 2.0;
        v
    });
}

#[test]
fn a_host_saves_and_restores_the_streams_as_words() {
    let companies = random_universe(6, 11)
        .iter()
        .enumerate()
        .map(|(i, g)| g.to_init().to_tick_company(i))
        .collect();
    let mut engine = Engine::new(
        11,
        companies,
        create_initial_economy_state(&Default::default()),
        create_initial_central_bank_state(0),
        sectors(),
    );
    engine.open_market();
    for minute in 30..40 {
        engine.tick(&TickRequest::new(GameTime::new(9, minute, 3)));
    }

    let before = engine.rng_state();
    let saved: Vec<f64> = before.to_words().to_vec();
    assert_eq!(saved.len(), ENGINE_RNG_STATE_WIDTH);
    let first: Vec<f64> = (0..4).map(|_| engine.draw_normal()).collect();

    engine.set_rng_state(EngineRngState::from_words(&saved).unwrap());
    let again: Vec<f64> = (0..4).map(|_| engine.draw_normal()).collect();
    assert_eq!(first, again);

    // The market stream is stream 0, so it is the first RNG_STREAM_WIDTH words.
    assert_eq!(RngState::from_words(&saved[..RNG_STREAM_WIDTH]), Ok(before.market));
}
