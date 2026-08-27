//! Bit-identical parity for `order_book`, by replaying a scripted program.
//!
//! **A hard gate.** The reference implementation's order book has zero
//! imports and zero transcendentals, so every operation is exactly specified by IEEE-754 and any mismatch is a
//! defect in the port.
//!
//! The vectors are a single stateful PROGRAM rather than isolated cases,
//! because the book's interesting bugs are ordering bugs: a newcomer jumping
//! the queue at an existing price, the wrong end trimmed at the depth cap, a
//! partially-filled maker removed instead of decremented. None of those show
//! up in a per-function sweep — they only appear as a divergence in a
//! sequence.
//!
//! So this compares the ENTIRE book after every step, and reports the first
//! step that diverges. That makes a failure a location, not a mystery.

use std::fs;
use std::path::PathBuf;

use pretium::order_book::{OrderBook, Side, SubmitOptions};
use serde::Deserialize;

#[derive(Deserialize)]
struct Vectors {
    steps: Vec<Step>,
}

#[derive(Deserialize)]
struct Step {
    step: usize,
    note: String,
    op: Op,
    state: State,
}

#[derive(Deserialize)]
#[serde(tag = "op")]
enum Op {
    #[serde(rename = "postLimit")]
    PostLimit {
        side: String,
        price: String,
        quantity: String,
        #[serde(rename = "ownerId")]
        owner_id: String,
        #[serde(rename = "orderId")]
        order_id: Option<String>,
    },
    #[serde(rename = "submit")]
    Submit {
        side: String,
        quantity: String,
        #[serde(rename = "takerId")]
        taker_id: String,
        #[serde(rename = "limitPrice")]
        limit_price: Option<String>,
        #[serde(rename = "postRemainder")]
        post_remainder: Option<bool>,
        #[serde(rename = "orderId")]
        order_id: Option<String>,
    },
    #[serde(rename = "cancel")]
    Cancel {
        #[serde(rename = "orderId")]
        order_id: String,
    },
    #[serde(rename = "cancelAllFor")]
    CancelAllFor {
        #[serde(rename = "ownerId")]
        owner_id: String,
    },
    #[serde(rename = "appendMakerLevel")]
    AppendMakerLevel {
        side: String,
        price: String,
        quantity: String,
        #[serde(rename = "ownerId")]
        owner_id: String,
    },
    #[serde(rename = "sweepCost")]
    SweepCost { side: String, quantity: String },
}

#[derive(Deserialize)]
struct State {
    bids: Vec<RestingOrder>,
    asks: Vec<RestingOrder>,
    #[serde(rename = "lastPrice")]
    last_price: Option<String>,
    sequence: u64,
    #[serde(rename = "bestBid")]
    best_bid: Option<String>,
    #[serde(rename = "bestAsk")]
    best_ask: Option<String>,
    #[serde(rename = "midPrice")]
    mid_price: Option<String>,
    spread: Option<String>,
    #[serde(rename = "depthBuy")]
    depth_buy: String,
    #[serde(rename = "depthSell")]
    depth_sell: String,
}

#[derive(Deserialize)]
struct RestingOrder {
    id: String,
    #[serde(rename = "ownerId")]
    owner_id: String,
    price: String,
    quantity: String,
    remaining: String,
    sequence: u64,
}

fn f(hex: &str) -> f64 {
    f64::from_bits(u64::from_str_radix(hex, 16).expect("bad bit pattern"))
}

/// NaN-aware bit comparison. JSON carries NaN as its bit pattern, and the
/// vectors deliberately include NaN inputs to exercise the `!(q > 0)` guard,
/// so `==` would wrongly report a mismatch on two identical NaNs.
fn same_bits(a: f64, b: f64) -> bool {
    a.to_bits() == b.to_bits()
}

fn side_of(s: &str) -> Side {
    match s {
        "buy" => Side::Buy,
        "sell" => Side::Sell,
        other => panic!("bad side {other}"),
    }
}

fn check_side(
    label: &str,
    step: &Step,
    actual: &[pretium::order_book::BookOrder],
    expected: &[RestingOrder],
    problems: &mut Vec<String>,
) {
    if actual.len() != expected.len() {
        problems.push(format!(
            "step {} ({}): {label} depth {} vs expected {}",
            step.step,
            step.note,
            actual.len(),
            expected.len()
        ));
        return;
    }
    for (i, (got, want)) in actual.iter().zip(expected).enumerate() {
        // Order matters as much as content: position IS priority.
        if got.id != want.id || got.owner_id != want.owner_id || got.sequence != want.sequence {
            problems.push(format!(
                "step {} ({}): {label}[{i}] identity — rust id={} owner={} seq={} / ts id={} owner={} seq={}",
                step.step, step.note, got.id, got.owner_id, got.sequence, want.id, want.owner_id, want.sequence
            ));
        }
        for (field, g, w) in [
            ("price", got.price, f(&want.price)),
            ("quantity", got.quantity, f(&want.quantity)),
            ("remaining", got.remaining, f(&want.remaining)),
        ] {
            if !same_bits(g, w) {
                problems.push(format!(
                    "step {} ({}): {label}[{i}].{field} rust={g:?} ts={w:?}",
                    step.step, step.note
                ));
            }
        }
    }
}

#[test]
fn matches_the_reference_across_a_replayed_program() {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("goldens/orderbook.json");
    let raw = fs::read_to_string(&path).unwrap_or_else(|e| {
        panic!(
            "{}: {e}\nRegenerate from the reference implementation's order-book generator",
            path.display()
        )
    });
    let vectors: Vectors = serde_json::from_str(&raw).expect("malformed orderbook.json");

    let mut book = OrderBook::new("ACME", None);
    let mut problems: Vec<String> = Vec::new();

    for step in &vectors.steps {
        match &step.op {
            Op::PostLimit {
                side,
                price,
                quantity,
                owner_id,
                order_id,
            } => {
                book.post_limit(
                    side_of(side),
                    f(price),
                    f(quantity),
                    owner_id,
                    order_id.clone(),
                );
            }
            Op::Submit {
                side,
                quantity,
                taker_id,
                limit_price,
                post_remainder,
                order_id,
            } => {
                book.submit(
                    side_of(side),
                    f(quantity),
                    taker_id,
                    SubmitOptions {
                        limit_price: limit_price.as_deref().map(f),
                        post_remainder: post_remainder.unwrap_or(false),
                        order_id: order_id.clone(),
                    },
                );
            }
            Op::Cancel { order_id } => {
                book.cancel_order(order_id);
            }
            Op::CancelAllFor { owner_id } => {
                book.cancel_all_for(owner_id);
            }
            Op::AppendMakerLevel {
                side,
                price,
                quantity,
                owner_id,
            } => {
                book.append_maker_level(side_of(side), f(price), f(quantity), owner_id);
            }
            Op::SweepCost { side, quantity } => {
                // Read-only, but still replayed so the step indices line up
                // and so a state-mutating regression here would be caught.
                book.sweep_cost(side_of(side), f(quantity));
            }
        }

        check_side("bids", step, &book.bids, &step.state.bids, &mut problems);
        check_side("asks", step, &book.asks, &step.state.asks, &mut problems);

        if book.sequence != step.state.sequence {
            problems.push(format!(
                "step {} ({}): sequence rust={} ts={}",
                step.step, step.note, book.sequence, step.state.sequence
            ));
        }

        for (label, got, want) in [
            (
                "lastPrice",
                book.last_price,
                step.state.last_price.as_deref().map(f),
            ),
            (
                "bestBid",
                book.best_bid(),
                step.state.best_bid.as_deref().map(f),
            ),
            (
                "bestAsk",
                book.best_ask(),
                step.state.best_ask.as_deref().map(f),
            ),
            (
                "midPrice",
                book.mid_price(),
                step.state.mid_price.as_deref().map(f),
            ),
            ("spread", book.spread(), step.state.spread.as_deref().map(f)),
        ] {
            let agrees = match (got, want) {
                (None, None) => true,
                (Some(g), Some(w)) => same_bits(g, w),
                _ => false,
            };
            if !agrees {
                problems.push(format!(
                    "step {} ({}): {label} rust={got:?} ts={want:?}",
                    step.step, step.note
                ));
            }
        }

        for (label, got, want) in [
            (
                "depth(buy)",
                book.depth(Side::Buy),
                f(&step.state.depth_buy),
            ),
            (
                "depth(sell)",
                book.depth(Side::Sell),
                f(&step.state.depth_sell),
            ),
        ] {
            if !same_bits(got, want) {
                problems.push(format!(
                    "step {} ({}): {label} rust={got:?} ts={want:?}",
                    step.step, step.note
                ));
            }
        }

        // Stop at the first diverging step. Once the books differ, every
        // later step compares two different worlds and the noise buries the
        // cause.
        if !problems.is_empty() {
            break;
        }
    }

    let resting: usize = vectors
        .steps
        .iter()
        .map(|s| s.state.bids.len() + s.state.asks.len())
        .sum();
    println!(
        "replayed {} steps, {} resting-order snapshots compared bit-exactly",
        vectors.steps.len(),
        resting
    );

    assert!(
        problems.is_empty(),
        "diverged from the reference implementation:\n  {}",
        problems.join("\n  ")
    );
}
