"""`book_depth_nesting`: the latent depth counts the maker's ladder as its front.

At 0.0, which every preset carries, the latent pool sits beside the ladder
and the depth within any distance of the touch is the two summed. At 1.0 the
latent curve counts the ladder's shares at a price as good or better as
already on it, so the depth is the larger of the two (`agent_book.rs`,
`append_latent_depth`; the Rust test
`nesting_makes_the_depth_the_larger_of_ladder_and_law` holds the shape).
These tests hold:

- it is off on every shipped preset;
- set to 0.0 explicitly it is the preset, fills and prices, with agents trading;
- with no agent trading, 1.0 changes no price: it is read only in the book
  an agent meets;
- a slice inside the ladder's first level pays exactly what it did, and a
  block of 3% of daily volume pays more, never less;
- with the metaorder memory on at the calibrated arm, no round trip of
  `metaorder_curve.py`'s strategies makes money;
- the invariants refuse values outside [0, 1] and the dial without the depth.
"""

import sys
from pathlib import Path

import pytest

import tradefloor as tf

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "calibration"))

MEMORY = dict(impact_memory_coefficient=0.65, impact_memory_half_life=12.0,
              impact_memory_slow_half_life=780.0, impact_memory_slow_weight=0.1,
              impact_memory_crossover=0.001, fill_impact_coefficient=0.15)
UNIVERSE = tf.Universe.random(8, seed=93001)


def warmed(over=None, seed=2011):
    model = tf.ModelParams.from_preset("pt-v20", **(over or {}))
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=model)
    e.open_market()
    e.run_session(9, 30, 3, 390, close_at_end=True)
    e.close_market()
    e.open_market()
    e.run_session(9, 30, 3, 30)
    return e


def cost_bp(e, i, f):
    """Buy `f` of daily volume at once; the average price over the mid, bp."""
    t = e.tickers[i]
    mid = e.book(t).mid_price
    r = e.submit("me", t, f * UNIVERSE[i].avg_volume)
    assert r["filled"] > 0
    return 1e4 * (r["average_price"] / mid - 1.0)


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["book_depth_nesting"] == 0.0


def test_zero_is_the_preset_with_agents_trading():
    a, b = warmed(), warmed(dict(book_depth_nesting=0.0))
    for e in (a, b):
        for i in range(len(UNIVERSE)):
            cost_bp(e, i, 0.05)
        e.run_session(10, 0, 3, 60)
    assert a.prices() == b.prices()
    assert a.take_fills() == b.take_fills()


def test_no_price_moves_without_agents():
    a, b = warmed(), warmed(dict(book_depth_nesting=1.0))
    for e in (a, b):
        e.run_session(10, 0, 3, 200)
    assert a.prices() == b.prices()


def test_a_slice_pays_what_it_did_and_a_block_pays_more():
    dearer = 0
    for over in ({}, MEMORY):
        base = warmed(over)
        nested = warmed({**over, "book_depth_nesting": 1.0})
        for i in range(len(UNIVERSE)):
            x, y = base.fork(1)[0], nested.fork(1)[0]
            assert cost_bp(y, i, 0.0005) == pytest.approx(cost_bp(x, i, 0.0005), abs=1e-9)
            x, y = base.fork(1)[0], nested.fork(1)[0]
            b0, b1 = cost_bp(x, i, 0.03), cost_bp(y, i, 0.03)
            assert b1 >= b0 - 1e-9
            dearer += b1 > b0 + 0.1
    assert dearer >= len(UNIVERSE)


def test_no_round_trip_makes_money():
    import metaorder_curve as mc
    params = tf.ModelParams.from_preset("pt-v20", **MEMORY, book_depth_nesting=1.0)
    rows = mc.round_trips(2012, params, n_names=4)
    assert len(rows) > 40
    assert max(r["edge_bp"] for r in rows) < 0.0


@pytest.mark.parametrize("over", [
    dict(book_depth_nesting=-0.1),
    dict(book_depth_nesting=1.5),
    dict(book_depth_nesting=float("nan")),
    dict(book_depth_coefficient=0.0, book_depth_exponent=0.0, book_depth_reach=0.0,
         book_refill_half_life=0.0, book_depth_nesting=1.0),
])
def test_the_invariants_refuse(over):
    with pytest.raises(Exception, match="book_depth_nesting"):
        tf.ModelParams.from_preset("pt-v20", **over)
