//! Roster mutation: what changing the listed universe mid-run guarantees.
//!
//! A listed universe is not static — companies IPO in, go bankrupt, and are
//! acquired — and until now the engine could not represent that. The embedder
//! was left choosing between rebuilding, which resets the generator and
//! destroys reproducibility, and pretending nothing changed, which silently
//! attaches every positional result to the wrong company.
//!
//! These tests pin the distinction that makes the feature safe:
//!
//!   INVARIANCE is not offered and cannot be. The tick draws per company, so
//!   changing `n` shifts every subsequent draw and the whole market moves from
//!   that point. That is the model, not a defect.
//!
//!   REPRODUCIBILITY is offered and is what matters. One seed plus the same
//!   sequence of edits at the same ticks reproduces the same market exactly.
//!
//! Both halves are asserted, because a reader who assumes the first will build
//! something that quietly depends on it.

use pretium::engine::{Engine, TickRequest};
use pretium::market::{GameTime, NewsEvent, NewsImpactEntry, OrderVolume, TickCompany, TickStock};

fn company(id: &str, price: f64) -> TickCompany {
    TickCompany {
        id: id.to_string(),
        ticker: id.to_string(),
        sector: "technology".to_string(),
        is_bankrupt: false,
        is_public: true,
        stock: TickStock {
            price,
            previous_close: price,
            previous_tick_price: None,
            open: price,
            high: price,
            low: price,
            volume: 0.0,
            avg_volume: 1e6,
            shares_outstanding: 1e8,
            market_cap: price * 1e8,
            mispricing_s: None,
            mispricing_s_prev_close: None,
            mispricing_momentum: None,
            maker_inventory: None,
            garch_variance: 0.000625,
            garch_cascade: [0.015 * 0.015; pretium::market::garch::CASCADE_MAX],
            last_daily_return: None,
            beta: Some(1.0),
            short_interest: 0.0,
            float: 1e8,
        },
        sector_volatility: Some(1.2),
        sector_avg_pe: Some(32.0),
        eps: Some(4.0),
        book_value_per_share: Some(20.0),
        revenue_growth: Some(0.1),
    }
}

fn engine(seed: u32, n: usize) -> Engine {
    let companies = (0..n)
        .map(|i| company(&format!("C{i}"), 100.0 + i as f64))
        .collect();
    Engine::new(
        seed,
        companies,
        pretium::economy::create_initial_economy_state(&Default::default()),
        pretium::economy::create_initial_central_bank_state(0),
        pretium::sectors::keys().iter().map(|s| s.to_string()).collect(),
    )
}

fn tick(e: &mut Engine, minute: i64) {
    e.tick(&TickRequest {
        time: GameTime {
            hour: 9 + (30 + minute) / 60,
            minute: (30 + minute) % 60,
            day_of_week: 3,
        },
        volatility_multiplier: 1.0,
        news: &[] as &[NewsEvent],
        news_impact_queue: &[] as &[NewsImpactEntry],
        order_volumes: &[] as &[(String, OrderVolume)],
    });
}

#[test]
fn the_generator_carries_across_a_roster_change() {
    // The whole point. If the draw counter reset, the edit would silently
    // restart the market's randomness and no replay would ever match.
    let mut e = engine(42, 5);
    e.open_market();
    for m in 0..10 {
        tick(&mut e, m);
    }
    let before = e.draws_consumed();
    assert!(before > 0);

    e.add_company(company("NEW", 50.0));
    assert_eq!(e.draws_consumed(), before, "adding a company must not draw");

    e.remove_company(0);
    assert_eq!(e.draws_consumed(), before, "removing a company must not draw");
}

#[test]
fn the_same_edits_at_the_same_ticks_reproduce_the_same_market() {
    // Reproducibility, the property actually on offer. This is what lets a
    // seed identify a run whose universe changed partway through.
    let run = || {
        let mut e = engine(7, 4);
        e.open_market();
        for m in 0..20 {
            tick(&mut e, m);
        }
        e.add_company(company("IPO", 33.0));
        for m in 20..40 {
            tick(&mut e, m);
        }
        e.remove_company(1);
        for m in 40..60 {
            tick(&mut e, m);
        }
        (e.prices(), e.draws_consumed(), e.ids())
    };

    let (a_prices, a_draws, a_ids) = run();
    let (b_prices, b_draws, b_ids) = run();

    assert_eq!(a_draws, b_draws);
    assert_eq!(a_ids, b_ids);
    for (i, (x, y)) in a_prices.iter().zip(&b_prices).enumerate() {
        assert_eq!(x.to_bits(), y.to_bits(), "company {i}");
    }
}

#[test]
fn an_edit_changes_the_rest_of_the_market_and_that_is_correct() {
    // The honest half. A reader who assumes an IPO merely appends a name will
    // build something that depends on invariance the model cannot provide, so
    // the absence is asserted rather than left to a comment.
    let untouched = {
        let mut e = engine(11, 4);
        e.open_market();
        for m in 0..40 {
            tick(&mut e, m);
        }
        e.prices()
    };

    let edited = {
        let mut e = engine(11, 4);
        e.open_market();
        for m in 0..20 {
            tick(&mut e, m);
        }
        e.add_company(company("IPO", 33.0));
        for m in 20..40 {
            tick(&mut e, m);
        }
        e.prices()
    };

    // The original four are still first, and their prices have MOVED, because
    // the fifth company consumed draws they would otherwise have received.
    assert_eq!(untouched.len(), 4);
    assert_eq!(edited.len(), 5);
    let diverged = untouched
        .iter()
        .zip(&edited[..4])
        .any(|(a, b)| a.to_bits() != b.to_bits());
    assert!(
        diverged,
        "adding a company left the others bit-identical, which would mean \
         draws do not scale with roster size - if that is now true, the \
         reproducibility argument above needs rewriting"
    );
}

#[test]
fn removal_preserves_the_order_of_everything_after_it() {
    // Vec::remove, not swap_remove. The cheaper call moves the last company
    // into the hole and silently reorders the roster, and roster order decides
    // which draws each company receives.
    let mut e = engine(1, 5);
    assert_eq!(e.ids(), vec!["C0", "C1", "C2", "C3", "C4"]);

    let removed = e.remove_company(1).expect("in range");
    assert_eq!(removed.id, "C1");
    assert_eq!(
        e.ids(),
        vec!["C0", "C2", "C3", "C4"],
        "the tail must keep its relative order"
    );
}

#[test]
fn add_returns_the_index_the_column_will_use() {
    let mut e = engine(1, 3);
    let index = e.add_company(company("NEW", 12.0));
    assert_eq!(index, 3);
    assert_eq!(e.ids()[index], "NEW");
    assert_eq!(e.prices()[index], 12.0);
}

#[test]
fn an_out_of_range_removal_reports_rather_than_panics() {
    // A panic here would abort the module and take the whole session with it.
    // A removal racing a bankruptcy is an embedder bug worth surfacing, not a
    // reason to destroy the run.
    let mut e = engine(1, 2);
    assert!(e.remove_company(2).is_none());
    assert!(e.remove_company(usize::MAX).is_none());
    assert_eq!(e.len(), 2, "a failed removal must not disturb the roster");
}

#[test]
fn index_of_finds_by_id_and_tracks_edits() {
    let mut e = engine(1, 3);
    assert_eq!(e.index_of("C1"), Some(1));
    e.remove_company(0);
    assert_eq!(e.index_of("C1"), Some(0), "indices shift down after removal");
    assert_eq!(e.index_of("C0"), None);
    assert_eq!(e.index_of("nope"), None);
}

#[test]
fn a_roster_can_be_emptied_and_refilled() {
    // Degenerate but reachable: every company in a small universe going
    // bankrupt. It must not panic, and ticking an empty roster must be inert
    // rather than an error.
    let mut e = engine(3, 2);
    e.open_market();
    tick(&mut e, 0);
    e.remove_company(0);
    e.remove_company(0);
    assert!(e.is_empty());

    let before = e.draws_consumed();
    tick(&mut e, 1);
    assert_eq!(e.len(), 0);
    assert!(e.draws_consumed() >= before);

    e.add_company(company("REBORN", 10.0));
    assert_eq!(e.len(), 1);
    tick(&mut e, 2);
    assert!(e.prices()[0] > 0.0);
}
