"""The rate indices beside the agent-facing book (the 0.8.5 integration).

The book holds equities only; the indices quote their own ladder. So with the
book's dials on, an order on an index is priced off its own book and its flow
reaches the index through `run_session`'s `fills`, under either impact law.
"""

from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor.portfolio import Portfolio

LIVE = dict(book_depth_coefficient=0.75, book_depth_exponent=0.5,
            book_depth_reach=1.0, book_shared=1.0, book_refill_half_life=27.0,
            book_resting=1.0, fill_impact_coefficient=0.314)
ROSTER = tf.Universe.random(6, seed=93001, bonds=True)


def engine(**over):
    e = tf.Engine(seed=5, universe=ROSTER, model=tf.ModelParams.from_preset(**{**LIVE, **over}))
    e.open_market()
    e.run_session(9, 30, 3, 30)
    return e


def test_the_engine_book_refuses_a_rate_index_by_name():
    e = engine()
    with pytest.raises(Exception, match="rate index"):
        e.submit("agent", "UST10Y", 1000.0)


@pytest.mark.parametrize("linear", [0.314, 0.0])
def test_an_index_order_trades_off_its_own_book_and_its_fills_move_it(linear):
    moved = []
    for size in (0.0, 50_000.0):
        e = engine(fill_impact_coefficient=linear)
        pf = Portfolio(cash=1e8)
        if size:
            fill = pf.execute(e, "UST10Y", size)
            assert fill["quantity"] == pytest.approx(size)
            assert pf.pending_flow() == {"UST10Y": (size, 0.0)}
        e.run_session(10, 0, 3, 30, fills=pf.pending_flow())
        i = e.tickers.index("UST10Y")
        moved.append(e.prices())
        pf.clear_flow()
    import struct
    a = struct.unpack(f"<{len(moved[0]) // 8}d", moved[0])
    b = struct.unpack(f"<{len(moved[1]) // 8}d", moved[1])
    assert b[i] > a[i]


def test_an_equity_still_goes_through_the_engine_book():
    e = engine()
    pf = Portfolio(cash=1e8)
    pf.execute(e, e.tickers[0], 500.0)
    assert pf.pending_flow() == {}
    assert e.state_snapshot().get("book") is not None


def test_a_limit_on_an_index_is_refused_plainly():
    e = engine()
    pf = Portfolio(cash=1e8)
    with pytest.raises(tf.ValidationError, match="rate index"):
        pf.submit_limit(e, "UST2Y", 100.0, 99.0)
