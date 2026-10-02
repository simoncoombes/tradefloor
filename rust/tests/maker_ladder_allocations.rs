//! What rebuilding a maker's ladder costs in heap allocations.
//!
//! `build_live_book` rebuilds every name's ladder on every tick, so its cost
//! per level is paid about 40 names x 65 ticks x 20 levels a day. Each level
//! needs two strings of its own, the order id and the owner. Until 0.8.5's
//! performance pass it took about four: the id went through `format!`, and
//! `append_maker_level` handed back a clone of the order (two more strings)
//! that every caller dropped unread. A `sample` of `run_days` on pt-v20 put
//! 13 per cent of the day there and 39 per cent of all samples in malloc and
//! free.
//!
//! This binary installs a counting allocator, which is why it is a file of
//! its own: a global allocator applies to the whole test binary.

use std::alloc::{GlobalAlloc, Layout, System};
use std::cell::Cell;

use tradefloor::microstructure::{build_live_book, CompanyMicrostructure, LiveBookOptions};
use tradefloor::order_book::{OrderBook, Side};

struct Counting;

thread_local! {
    // Per thread, because the harness runs tests side by side. A const
    // initialiser with no destructor, so reading it never allocates.
    static ALLOCATIONS: Cell<u64> = const { Cell::new(0) };
}

fn count() {
    let _ = ALLOCATIONS.try_with(|n| n.set(n.get() + 1));
}

unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, layout: Layout) -> *mut u8 {
        count();
        System.alloc(layout)
    }

    unsafe fn dealloc(&self, ptr: *mut u8, layout: Layout) {
        System.dealloc(ptr, layout)
    }

    unsafe fn alloc_zeroed(&self, layout: Layout) -> *mut u8 {
        count();
        System.alloc_zeroed(layout)
    }

    unsafe fn realloc(&self, ptr: *mut u8, layout: Layout, new_size: usize) -> *mut u8 {
        count();
        System.realloc(ptr, layout, new_size)
    }
}

#[global_allocator]
static COUNTING: Counting = Counting;

fn allocations() -> u64 {
    ALLOCATIONS.with(|n| n.get())
}

fn company() -> CompanyMicrostructure {
    CompanyMicrostructure {
        id: "ACME".to_string(),
        sector_volatility: Some(1.0),
        price: 100.0,
        market_cap: 5e9,
        beta: Some(1.0),
        float: Some(4e7),
        short_interest: Some(0.02),
        avg_volume: Some(1e6),
        volume: Some(5e5),
        shares_outstanding: Some(5e7),
        maker_inventory: Some(0.0),
    }
}

/// Allocations `build_live_book` makes for a ladder `levels` deep a side,
/// and the number of levels it built.
fn build(levels: f64) -> (u64, usize) {
    let options = LiveBookOptions { levels, ..LiveBookOptions::default() };
    let company = company();
    let before = allocations();
    let book = build_live_book(&company, &options);
    let taken = allocations() - before;
    (taken, book.bids.len() + book.asks.len())
}

#[test]
fn a_ladder_level_costs_its_two_strings() {
    // The difference between a deep ladder and a shallow one, so the fixed
    // cost (the book's id, the ladder's vectors) cancels. What is left is
    // the two strings per level plus the vectors' few extra doublings.
    let (shallow, shallow_levels) = build(4.0);
    let (deep, deep_levels) = build(28.0);
    assert!(deep_levels > shallow_levels + 40, "{shallow_levels} -> {deep_levels} levels");
    let per_level = (deep - shallow) as f64 / (deep_levels - shallow_levels) as f64;
    assert!(
        per_level < 3.0,
        "{per_level:.2} allocations per ladder level; the id and the owner are two"
    );
}

#[test]
fn pushing_a_level_hands_nothing_back() {
    // `push_maker_level` is the engine's path and returns a bool.
    // `append_maker_level` keeps its signature for the Python book, and its
    // copy is the two strings `push_maker_level` does not make.
    let mut book = OrderBook::new("ACME", Some(100.0));
    book.bids.reserve(64);
    book.asks.reserve(64);
    let before = allocations();
    for i in 0..30 {
        assert!(book.push_maker_level(Side::Buy, 99.0 - i as f64 * 0.01, 100.0, "maker"));
    }
    assert_eq!(allocations() - before, 60, "an id and an owner per level");

    let before = allocations();
    for i in 0..30 {
        let order = book.append_maker_level(Side::Sell, 101.0 + i as f64 * 0.01, 100.0, "maker");
        assert!(order.is_some());
    }
    assert_eq!(allocations() - before, 120, "two for the level, two for its copy");

    // The ids run on from one call to the other, as they did.
    assert_eq!(book.bids[0].id, "ACME-0");
    assert_eq!(book.bids[29].id, "ACME-29");
    assert_eq!(book.asks[0].id, "ACME-30");
    assert_eq!(book.asks[29].id, "ACME-59");
}
