//! Issue #268: a host that drives `close_market` and `advance_day` as two
//! calls, and snapshots between them, resumes bit for bit.

use tradefloor::economy::{create_initial_central_bank_state, create_initial_economy_state};
use tradefloor::engine::{DayAdvanceRequest, DayCloseRequest, Engine, PriceField, TickRequest};
use tradefloor::market::GameTime;
use tradefloor::params::ModelParams;
use tradefloor::snapshot::EngineSnapshot;

fn engine(preset: &str) -> Engine {
    engine_on(preset, create_initial_economy_state(&Default::default()))
}

/// Built as margincall builds one: from an opening it names, with no burn-in.
fn engine_on(preset: &str, economy: tradefloor::economy::EconomyState) -> Engine {
    let companies = tradefloor::universe::random_universe(12, 5)
        .iter()
        .enumerate()
        .map(|(i, g)| g.to_init().to_tick_company(i))
        .collect();
    engine_from(preset, companies, economy)
}

fn engine_from(
    preset: &str,
    companies: Vec<tradefloor::market::TickCompany>,
    economy: tradefloor::economy::EconomyState,
) -> Engine {
    Engine::with_params_from_opening(
        7,
        companies,
        economy,
        create_initial_central_bank_state(0),
        tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect(),
        ModelParams::preset(preset).unwrap(),
        false,
    )
}

/// The host's session: the day set, the open, 390 ticks, the close with its
/// own innovations and sector variances.
fn session(e: &mut Engine, day: i64) {
    e.set_current_day(day);
    e.open_market();
    for k in 0..390 {
        let m = 30 + k;
        e.tick(&TickRequest::new(GameTime::new(9 + m / 60, m % 60, day % 5)));
    }
    // The host's own innovations: a fixed pattern by day and name.
    let n = e.column(PriceField::Price).len();
    let innovations: Vec<Option<f64>> =
        (0..n).map(|i| Some(((day as f64 * 7.0 + i as f64 * 3.0).sin()) * 0.01)).collect();
    let variances = e.sector_base_variances();
    e.close_market(&DayCloseRequest::new(&innovations, &variances));
}

/// The host's midnight: the macro step on its own request.
fn midnight(e: &mut Engine, day: i64) {
    let mut req = DayAdvanceRequest::new(day + 1, (day + 1) * 24 * 60);
    req.market_return_pct = 0.25;
    e.advance_day(&req);
}

fn bits(values: &[f64]) -> Vec<u64> {
    values.iter().map(|v| v.to_bits()).collect()
}

#[test]
fn a_snapshot_between_close_market_and_advance_day_resumes_bit_for_bit() {
    for preset in ["pt-v21", "pt-v20"] {
        let mut original = engine(preset);
        for day in 0..4 {
            session(&mut original, day);
            midnight(&mut original, day);
        }
        session(&mut original, 4);
        // 16:00: after the close, before the midnight step.
        let bytes = original.snapshot().to_bytes();
        // The host rebuilds on the economy as it stands at the save, not the
        // opening the run began on, then restores.
        let mut resumed = engine_from(preset, original.companies().to_vec(), original.economy().clone());
        resumed.restore(&EngineSnapshot::from_bytes(&bytes).unwrap()).unwrap();
        assert_eq!(resumed.snapshot().to_bytes(), bytes, "{preset}: the restore is not the snapshot");
        midnight(&mut original, 4);
        midnight(&mut resumed, 4);
        assert_eq!(
            resumed.economy().vix.to_bits(),
            original.economy().vix.to_bits(),
            "{preset}: the VIX after advance_day differs: {} against {}",
            resumed.economy().vix,
            original.economy().vix,
        );
        for day in 5..8 {
            session(&mut original, day);
            session(&mut resumed, day);
            midnight(&mut original, day);
            midnight(&mut resumed, day);
        }
        assert_eq!(bits(&resumed.column(PriceField::Price)), bits(&original.column(PriceField::Price)), "{preset}");
        assert_eq!(resumed.snapshot().to_bytes(), original.snapshot().to_bytes(), "{preset}");
    }
}
