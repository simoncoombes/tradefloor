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

use tradefloor::engine::{Engine, TickRequest};
use tradefloor::market::{GameTime, NewsEvent, NewsImpactEntry, OrderVolume, TickCompany, TickStock};

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
            garch_cascade: [0.015 * 0.015; tradefloor::market::garch::CASCADE_MAX],
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
        tradefloor::economy::create_initial_economy_state(&Default::default()),
        tradefloor::economy::create_initial_central_bank_state(0),
        tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect(),
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
fn every_per_slot_column_shifts_with_the_roster() {
    // A per-slot column that did not shift with a removal would report each
    // remaining company's tick against its neighbour's slot, and every value
    // would still look plausible. Checked on LENGTH and on the value, because
    // a short column reads as a company that never moved.
    let mut e = engine(9, 5);
    e.set_settle_depth_counterfactual(true);
    e.open_market();
    tick(&mut e, 0);

    // EVERY per-slot array beside `companies`. An array missing from this
    // list is one neither roster mutation is tested on, which is the exact
    // shape of the defect this test was written for -- twice, once for the
    // print columns and once for `tick_clamp` a round later. `volume_idio`
    // is here for the same reason: issue #148 resized it and nothing walked
    // it through a listing.
    let columns = |e: &Engine| {
        [
            e.tick_shock().len(),
            e.tick_absorbed().len(),
            e.tick_clamp().len(),
            e.tick_fundamental().len(),
            e.tick_anchor().len(),
            e.tick_components().len(),
            e.tick_unbounded_print().len(),
            e.tick_liquidity_share().len(),
            e.volume_idio().len(),
        ]
    };
    assert_eq!(columns(&e), [5; 9]);

    // The value that has to move with the company, not with the index.
    let anchor_of_c3 = e.tick_anchor()[3];
    let shock_of_c3 = e.tick_shock()[3];
    let clamp_of_c3 = e.tick_clamp()[3];
    e.remove_company(1);
    assert_eq!(e.ids(), vec!["C0", "C2", "C3", "C4"]);
    assert_eq!(columns(&e), [4; 9]);
    assert_eq!(e.tick_anchor()[2], anchor_of_c3);
    assert_eq!(e.tick_shock()[2], shock_of_c3);
    assert_eq!(e.tick_clamp()[2], clamp_of_c3);

    e.add_company(company("NEW", 12.0));
    assert_eq!(columns(&e), [5; 9]);
    assert_eq!(e.tick_shock()[4], 0.0, "a listing has not moved yet");
    assert_eq!(e.tick_clamp()[4], 0.0);

    // And the next tick fills every slot rather than the first four.
    tick(&mut e, 1);
    assert_eq!(e.tick_unbounded_print().len(), 5);
    assert!(e.tick_unbounded_print().iter().all(|v| v.is_finite()));
}

#[test]
fn the_counterfactual_columns_stay_empty_while_the_arm_is_off() {
    // Emptiness is what tells the reporting surface no arm ran, so a roster
    // edit must not turn an empty pair into a one-row column.
    let mut e = engine(9, 3);
    e.open_market();
    tick(&mut e, 0);
    assert!(e.tick_unbounded_print().is_empty());
    e.add_company(company("NEW", 12.0));
    assert!(e.tick_unbounded_print().is_empty());
    assert!(e.tick_liquidity_share().is_empty());
    assert_eq!(e.tick_shock().len(), 4);
    assert_eq!(e.tick_clamp().len(), 4);
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

// -- The per-name volume array, issue #148 --------------------------------
//
// `volume_idio` is the fifth per-slot array and was the one the two
// mutations above left alone. Three separate things read it, and each one
// broke differently while the width was stale.
//
//   `update_volume_idio` draws once per SLOT, before the zero check, so the
//   per-day draw count was the array's construction width rather than the
//   roster's.
//
//   The tick reads each company's state at its own index, so after a
//   delisting every survivor read its old neighbour's state. Invisible on
//   every shipped preset, because all sixteen hold `volume_idio_sigma` at
//   0.0 and every entry stays exactly 0.0.
//
//   A snapshot carries the array, and `set_volume_idio` refuses a width its
//   roster disagrees with, so a checkpoint of a roster-changing run held a
//   width that could not be restored.
//
// Fixing it moves the volume-idio stream position of any run that lists or
// delists, and moves nothing else. The tests below state both halves.

use tradefloor::engine::DayCloseRequest;
use tradefloor::market::AvgVolumePolicy;
use tradefloor::params::ModelParams;
use tradefloor::rng::{stream, GameRng};

/// One market close, which is where `update_volume_idio` runs.
fn close(e: &mut Engine) {
    let n = e.len();
    let innovations = vec![None; n];
    let variances = vec![0.000225; n];
    e.close_market(&DayCloseRequest {
        daily_innovations: &innovations,
        sector_base_variances: &variances,
        avg_volume: AvgVolumePolicy::Hold,
    });
}

/// A model with the per-name volume process switched ON.
///
/// Built here rather than added to `params.rs`, because every shipped preset
/// holds both coefficients at 0.0 and this work adds no preset and no dial.
/// At zero sigma every slot holds exactly 0.0, so the slots are
/// indistinguishable and a test asking whether a survivor kept its OWN value
/// would pass on any implementation. This is the model under which the slots
/// differ from each other.
fn idio_on() -> ModelParams {
    let mut p = Engine::default_model();
    p.volume_idio_persistence = 0.9;
    p.volume_idio_sigma = 0.4;
    p
}

fn engine_with(seed: u32, n: usize, params: ModelParams) -> Engine {
    let companies = (0..n)
        .map(|i| company(&format!("C{i}"), 100.0 + i as f64))
        .collect();
    Engine::with_params(
        seed,
        companies,
        tradefloor::economy::create_initial_economy_state(&Default::default()),
        tradefloor::economy::create_initial_central_bank_state(0),
        tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect(),
        params,
    )
}

#[test]
fn a_listing_widens_the_per_name_volume_array_and_starts_the_new_slot_at_zero() {
    // 0.0 is what the constructor gives every company, so a listing starts
    // with the state a company present from the first tick would hold.
    let mut e = engine(42, 8);
    assert_eq!(e.volume_idio().len(), 8);

    e.add_company(company("IPO", 33.0));
    assert_eq!(e.len(), 9);
    assert_eq!(
        e.volume_idio().len(),
        9,
        "the array is positional against the roster and must be its width"
    );
    assert_eq!(e.volume_idio()[8].to_bits(), 0.0f64.to_bits());
}

#[test]
fn a_delisting_narrows_the_per_name_volume_array() {
    let mut e = engine(42, 8);
    e.remove_company(3);
    assert_eq!(e.len(), 7);
    assert_eq!(e.volume_idio().len(), 7);
}

#[test]
fn every_survivor_of_a_delisting_keeps_its_own_state() {
    // The claim the shipped presets cannot state. Under `idio_on` the eight
    // slots hold eight different numbers, so removing the wrong one, or none
    // at all, is visible: the survivors' values would shift by one place.
    let mut e = engine_with(42, 8, idio_on());
    e.open_market();
    close(&mut e);
    let before: Vec<f64> = e.volume_idio().to_vec();
    assert_eq!(before.len(), 8);
    for (i, v) in before.iter().enumerate() {
        assert!(*v != 0.0, "slot {i} did not move, so this test is vacuous");
    }
    for i in 1..before.len() {
        assert!(
            before[i].to_bits() != before[0].to_bits(),
            "slot {i} equals slot 0, so a shift by one place would be invisible"
        );
    }

    let removed = 2;
    e.remove_company(removed);

    let after: Vec<f64> = e.volume_idio().to_vec();
    let expected: Vec<f64> = before
        .iter()
        .enumerate()
        .filter(|(i, _)| *i != removed)
        .map(|(_, v)| *v)
        .collect();
    assert_eq!(after.len(), 7);
    for (i, (a, b)) in after.iter().zip(&expected).enumerate() {
        assert_eq!(
            a.to_bits(),
            b.to_bits(),
            "survivor {i} holds another company's state"
        );
    }
}

#[test]
fn every_survivor_of_a_listing_keeps_its_own_state() {
    // A listing appends, so nothing before it may move. The mirror of the
    // delisting case, under the same model and for the same reason.
    let mut e = engine_with(42, 8, idio_on());
    e.open_market();
    close(&mut e);
    let before: Vec<f64> = e.volume_idio().to_vec();

    e.add_company(company("IPO", 33.0));

    let after = e.volume_idio();
    assert_eq!(after.len(), 9);
    for (i, (a, b)) in after.iter().zip(&before).enumerate() {
        assert_eq!(a.to_bits(), b.to_bits(), "company {i} moved on a listing");
    }
}

#[test]
fn a_roster_edit_moves_no_streams_position() {
    // A mutation is bookkeeping. It resizes an array and draws nothing, so
    // all seven positions, and all seven Box-Muller spares, stay where the
    // last close left them.
    let mut e = engine_with(42, 6, idio_on());
    e.open_market();
    for m in 0..5 {
        tick(&mut e, m);
    }
    close(&mut e);
    let before = e.rng_state();

    e.add_company(company("IPO", 33.0));
    assert_eq!(e.rng_state(), before, "a listing moved a stream");

    e.remove_company(0);
    assert_eq!(e.rng_state(), before, "a delisting moved a stream");
}

#[test]
fn the_per_day_draw_count_is_the_rosters_width_from_the_mutation_onward() {
    // The authorised change, stated as the schedule rather than as a number
    // copied out of a run. Four companies for one day, then five for one
    // day, is nine draws from the volume-idio stream, and the reference
    // generator below is the same substream stepped nine times.
    //
    // Before issue #148 was fixed this run drew eight, because the array
    // stayed at its construction width. That is what the width assertions
    // here stand in for: the pre-fix engine is not available to compare
    // against, and the stale width is the whole of what it did differently.
    const SEED: u32 = 42;
    let mut e = engine_with(SEED, 4, idio_on());
    e.open_market();
    close(&mut e);
    assert_eq!(e.volume_idio().len(), 4);

    e.add_company(company("IPO", 33.0));
    assert_eq!(e.volume_idio().len(), 5);
    e.open_market();
    close(&mut e);

    let mut reference = GameRng::substream(SEED, stream::VOLUME_IDIO);
    for _ in 0..(4 + 5) {
        reference.next_normal();
    }
    assert_eq!(
        e.rng_state().volume_idio,
        reference.snapshot(),
        "the stream advanced by something other than 4 then 5 draws"
    );

    // A delisting narrows the next day's count the same way.
    e.remove_company(0);
    e.open_market();
    close(&mut e);
    for _ in 0..4 {
        reference.next_normal();
    }
    assert_eq!(e.rng_state().volume_idio, reference.snapshot());
}

#[test]
fn a_fixed_roster_draws_what_it_always_drew() {
    // The other half of the authorised change: a run that never mutates its
    // roster takes the draws it always took, so nothing about it moves.
    const SEED: u32 = 42;
    let mut e = engine_with(SEED, 4, idio_on());
    for _ in 0..3 {
        e.open_market();
        close(&mut e);
    }

    let mut reference = GameRng::substream(SEED, stream::VOLUME_IDIO);
    for _ in 0..(4 * 3) {
        reference.next_normal();
    }
    assert_eq!(e.rng_state().volume_idio, reference.snapshot());
}

#[test]
fn a_shipped_preset_holds_every_slot_at_zero_across_a_roster_change() {
    // Why no shipped price path moves. All sixteen presets hold
    // `volume_idio_sigma` and `volume_idio_persistence` at 0.0, the update
    // returns before writing, and the tick reads the same 0.0 whatever the
    // width is. The fix changes which draws the stream has taken and leaves
    // every state exactly where it was.
    let mut e = engine(42, 6);
    assert_eq!(e.params().volume_idio_sigma, 0.0);
    assert_eq!(e.params().volume_idio_persistence, 0.0);

    e.open_market();
    close(&mut e);
    e.add_company(company("IPO", 33.0));
    e.open_market();
    close(&mut e);
    e.remove_company(0);
    e.open_market();
    close(&mut e);

    assert_eq!(e.volume_idio().len(), e.len());
    for (i, v) in e.volume_idio().iter().enumerate() {
        assert_eq!(v.to_bits(), 0.0f64.to_bits(), "slot {i} moved");
    }
}

#[test]
fn a_snapshot_at_a_stale_width_is_refused_and_the_error_says_why() {
    // The restore path. A checkpoint written before this fix, by a run that
    // listed or delisted, carries the array at its construction width. It is
    // refused rather than padded, because the states are positional and a
    // pad attaches each one to whichever company now sits at that index.
    let mut e = engine(42, 8);
    e.add_company(company("IPO", 33.0));
    assert_eq!(e.volume_idio().len(), 9);

    let stale = vec![0.0; 8]; // what a pre-fix snapshot of this run holds
    let err = e.set_volume_idio(&stale).expect_err("a stale width must fail");
    // The bound phrases, not the bare digits. `err.contains('8')` is
    // satisfied by the "#148" in the message whatever the widths are, and
    // the two format arguments are both `usize` and positional, so swapping
    // them produces a message that reads backwards and passes. Measured: a
    // build with the arguments swapped passed the digit form of this test.
    assert!(err.contains("carries 8"),
            "the error must name the width the snapshot carries");
    assert!(err.contains("holds 9"),
            "the error must name the width the roster holds");
    assert!(err.contains("#148"), "the error must name the issue");

    // The array itself is untouched by the refusal.
    assert_eq!(e.volume_idio().len(), 9);
    for v in e.volume_idio() {
        assert_eq!(v.to_bits(), 0.0f64.to_bits());
    }

    // A matching width still restores.
    let good = vec![0.25; 9];
    e.set_volume_idio(&good).expect("a matching width restores");
    assert_eq!(e.volume_idio()[8], 0.25);
}

#[test]
fn the_same_edits_reproduce_the_same_per_name_volume_states() {
    // Reproducibility, under the model that can see these states at all.
    let run = || {
        let mut e = engine_with(7, 4, idio_on());
        e.open_market();
        close(&mut e);
        e.add_company(company("IPO", 33.0));
        e.open_market();
        close(&mut e);
        e.remove_company(1);
        e.open_market();
        close(&mut e);
        (e.volume_idio().to_vec(), e.rng_state().volume_idio)
    };
    let (a_states, a_rng) = run();
    let (b_states, b_rng) = run();
    assert_eq!(a_rng, b_rng);
    assert_eq!(a_states.len(), 4);
    for (i, (x, y)) in a_states.iter().zip(&b_states).enumerate() {
        assert_eq!(x.to_bits(), y.to_bits(), "company {i}");
    }
}
