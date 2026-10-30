"""An account in deficit is liquidated at the next open (`margin_deficit_liquidates`).

A broker covers a customer's deficit with its own money from the close it is computed at (17 CFR 1.22(c)) and
liquidates an account in deficit rather than wait for the market to restore it. Off zero, a margin call made by a
close that leaves the account below zero is unmet whatever the next open shows; 0.0, on every preset, decides it on
the equity at the open, as it stood. Read by the account, not the engine, so no price moves by itself.
"""
from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor.portfolio import Portfolio

SMALL = tf.Universe.random(10, seed=5)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v22", **dials)


def day(engine):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.close_market()


def _called(deficit, equity_at_close):
    """A Portfolio long two front index futures, its cash set at the close so its equity there is
    `equity_at_close` dollars ("half" for half the maintenance margin: above zero, under the maintenance, so
    the close makes a call either way), and the next open's equity restored past the initial margin, as a
    night's rally would. Returns (portfolio, call, the future)."""
    engine = tf.Engine(seed=19, universe=SMALL, model=model(margin_deficit_liquidates=deficit))
    for _ in range(3):
        day(engine)
    engine.open_market()
    front = next(c["symbol"] for c in engine.contracts() if c.get("kind") == "future" and c["root"] == "IDX")
    p = Portfolio(cash=1e6, owner="a")
    p.execute(engine, front, 2.0)
    engine.run_session(9, 30, 3, 390)
    engine.close_market()
    p.settle_close(engine)
    if equity_at_close == "half":
        equity_at_close = 0.5 * p.margin_requirement(engine, maintenance=True)
    p.cash += equity_at_close - p.net_worth(engine)
    p.margin_calls.clear()
    p._margin_call = None
    p.settle_close(engine)          # the close's call, on the equity now
    assert p.margin_calls and p.margin_calls[-1]["equity"] == pytest.approx(equity_at_close)
    engine.run_night()
    engine.open_market()
    p.cash += p.margin_requirement(engine) * 2 - p.net_worth(engine) + 1.0
    p.settle_open(engine)
    return p, p.margin_calls[-1], front


def test_a_call_made_in_deficit_is_met_by_a_recovery_at_zero_and_liquidated_at_one():
    p, call, front = _called(0.0, -5_000.0)
    assert call["met"] is True and "deficit" not in call and p.futures.get(front) is not None
    p, call, front = _called(1.0, -5_000.0)
    assert call["met"] is False and call["deficit"] is True
    assert front not in p.futures
    assert [x["symbol"] for x in p.liquidations] == [front]


def test_a_call_made_above_zero_is_decided_as_before():
    for deficit in (0.0, 1.0):
        p, call, front = _called(deficit, "half")
        assert call["met"] is True and "deficit" not in call and p.futures.get(front) is not None


def test_the_switch_is_silent_at_zero_inert_on_prices_and_refused_without_the_margin():
    assert "margin_deficit_liquidates" in set(tf.ModelParams.digest_silent_at_zero())
    for name in tf.preset_names():
        assert tf.ModelParams.from_preset(name).margin_deficit_liquidates == 0.0
    assert model(margin_deficit_liquidates=0.0).fingerprint == model().fingerprint
    a = tf.Engine(seed=19, universe=SMALL, model=model())
    b = tf.Engine(seed=19, universe=SMALL, model=model(margin_deficit_liquidates=1.0))
    for _ in range(5):
        day(a)
        day(b)
    # The state hash carries the model's fingerprint, which names the dial at 1; the market is the same.
    assert a.prices() == b.prices()
    with pytest.raises(Exception, match="margin_scan_coverage"):
        tf.ModelParams.from_preset("pt-v21", margin_deficit_liquidates=1.0)
    with pytest.raises(Exception, match="switch"):
        model(margin_deficit_liquidates=0.5)
