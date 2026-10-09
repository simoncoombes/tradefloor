"""Futures in accounts (pt-v22 phase 1): variation margin, settlement,
margin calls and liquidation.

A `Portfolio` holds a future at a mark: a trade pays only its distance from
the mark, each close pays the mark's move (variation margin), and expiry
pays the last move, to the settlement price. A close that leaves equity
under the maintenance requirement makes a margin call; at the next open the
call is met with equity back at or above the initial requirement, or the
account's futures are closed through their books (row MG2). A portfolio
that never traded a future asks the engine nothing new.
"""
from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor.portfolio import LeverageError, Portfolio, is_future_symbol

SMALL = tf.Universe.random(8, seed=5)
CONTRACTS = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
                 forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                 forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
                 forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
                 forecast_policy_neutral=1.48, futures_index_listed=1.0, basis_sd=3.753,
                 basis_persistence=0.429, futures_vix_listed=1.0,
                 futures_vix_live_fast_share=0.713, futures_vix_live_fast_half_life=5.96,
                 futures_vix_live_slow_half_life=71.7, futures_rates_listed=1.0,
                 futures_oil_listed=1.0, margin_scan_coverage=0.99, margin_scan_tail=1.762)


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def first(engine, root):
    return next(c["symbol"] for c in engine.contracts() if c["root"] == root)


class Day:
    """An engine and a portfolio, stepped a day at a time with the hooks."""

    def __init__(self, seed=3, cash=1_000_000.0, **kw):
        self.e = tf.Engine(seed=seed, universe=SMALL, model=model(**CONTRACTS))
        self.p = Portfolio(cash=cash, owner="a", **kw)

    def open(self):
        self.e.open_market()
        self.p.collect_dividends(self.e)
        self.p.settle_open(self.e)
        self.e.run_session(9, 30, 3, 10)

    def close(self):
        self.e.run_session(9, 40, 3, 380)
        self.e.close_market()
        self.p.settle_close(self.e)

    def run(self, days):
        for _ in range(days):
            self.open()
            self.close()


def test_a_future_symbol_is_spelled_root_dot_f_and_an_expiry():
    assert is_future_symbol("IDX.F0056") and is_future_symbol("TR3.F0062")
    assert not is_future_symbol("BRK.B") and not is_future_symbol("ACME")


def test_a_portfolio_without_futures_asks_the_engine_nothing_new():
    class Counting:
        def __init__(self, engine):
            self.engine, self.calls = engine, []

        def __getattr__(self, name):
            self.calls.append(name)
            return getattr(self.engine, name)

    d = Day()
    d.run(2)
    spy = Counting(d.e)
    d.p.settle_open(spy)
    d.p.settle_close(spy)
    assert spy.calls == []


def test_a_trade_pays_only_its_distance_from_the_mark_and_the_close_pays_variation():
    d = Day()
    d.run(3)
    d.open()
    sym = first(d.e, "OIL")
    q = d.e.quote(sym)
    cash = d.p.cash
    fill = d.p.execute(d.e, sym, 20.0)
    mult = q["multiplier"]
    held = d.p.futures[sym]
    assert held.mark == q["mark"]
    assert d.p.cash - cash == pytest.approx(20.0 * mult * (q["mark"] - fill["price"]))
    assert fill["notional"] == pytest.approx(20.0 * fill["price"] * mult)
    before = d.p.cash
    d.close()
    mark = d.e.quote(sym)["mark"]
    assert d.p.cash - before == pytest.approx(20.0 * mult * (mark - q["mark"]))
    assert d.p.futures[sym].mark == mark
    assert d.p.net_worth(d.e) == pytest.approx(d.p.cash + d.p.futures_value(d.e))


def test_a_holding_through_expiry_earns_its_move_from_entry_to_settlement():
    for root, settles_at_open in (("IDX", True), ("FF", False)):
        d = Day(seed=7)
        d.run(2)
        d.open()
        sym = first(d.e, root)
        start_cash = d.p.cash
        fill = d.p.execute(d.e, sym, 5.0)
        mult = d.e.quote(sym)["multiplier"]
        d.close()
        while sym in d.p.futures:
            d.open()
            if sym in d.p.futures:
                d.close()
        settled, = (s for s in d.p.settled if s["symbol"] == sym)
        value = settled["price"]
        assert d.p.cash - start_cash == pytest.approx(5.0 * mult * (value - fill["price"]), rel=1e-12)
        exchange, = (s for s in d.e.settlements() if s["symbol"] == sym)
        assert exchange["value"] == value
        assert sym not in d.p.futures


def test_the_margin_requirement_is_the_contracts_margins_and_the_leverage_counts_notional():
    d = Day(max_leverage=2.0)
    d.run(3)
    d.open()
    sym = first(d.e, "IDX")
    q = d.e.quote(sym)
    d.p.execute(d.e, sym, 2.0)
    assert d.p.margin_requirement(d.e) == pytest.approx(2.0 * q["initial_margin"])
    assert d.p.margin_requirement(d.e, maintenance=True) == pytest.approx(2.0 * q["maintenance_margin"])
    assert d.p.gross_exposure(d.e) == pytest.approx(2.0 * q["multiplier"] * d.e.quote(sym)["price"])
    with pytest.raises(LeverageError):
        d.p.execute(d.e, sym, 40.0)


def _called(top_up=0.0):
    """An account whose equity is half its futures' maintenance requirement
    at a close, so the close makes a margin call, then the next open with
    ``top_up`` deposited before it."""
    d = Day(seed=11)
    d.run(3)
    d.open()
    sym = [c["symbol"] for c in d.e.contracts() if c["root"] == "VIX"][2]
    d.p.execute(d.e, sym, 40.0)
    d.p.cash -= d.p.net_worth(d.e) - 0.5 * d.p.margin_requirement(d.e, maintenance=True)
    d.close()
    assert d.p.margin_calls, "the close made no call"
    d.p.cash += top_up
    d.open()
    return d, sym


def test_mg2_a_call_met_by_the_next_open_leaves_the_futures():
    d, sym = _called(top_up=50_000_000.0)
    call = d.p.margin_calls[-1]
    assert call["equity"] < call["maintenance"]
    assert call["met"] is True
    assert d.p.liquidations == []
    assert sym in d.p.futures


def test_mg2_a_call_not_met_by_the_next_open_liquidates_the_futures():
    d, sym = _called()
    call = d.p.margin_calls[-1]
    assert call["met"] is False
    assert [x["symbol"] for x in d.p.liquidations] == [sym]
    assert d.p.liquidations[0]["filled"] == -40.0
    assert d.p.futures == {}


# ── Each harness runs the hooks ─────────────────────────────────────────────

class Hold:
    """Buys one front oil future at its first step and holds it."""

    def __init__(self):
        self.done = False

    def act(self, obs):
        if self.done:
            return {}
        listed = [c["symbol"] for c in obs.engine.contracts() if c["root"] == "OIL"]
        if not listed:
            return {}
        symbol = listed[0]
        self.done = True
        return {symbol: 3.0}


def test_evaluate_marks_and_settles_a_futures_position():
    cards = tf.evaluate({"hold": Hold()}, seed=3, universe=SMALL, model=model(**CONTRACTS), days=40)
    card = cards["hold"]
    assert card.trades >= 1, card.errors
    assert not card.errors, card.errors[:3]


def test_a_world_marks_and_settles_a_futures_position():
    world = tf.World(seed=3, universe=SMALL, agent=Hold(), model=model(**CONTRACTS))
    world.run(3)
    held = world.portfolio.futures
    assert len(held) == 1, world.portfolio
    symbol, = held
    cash = world.portfolio.cash
    world.run(1)
    if symbol in world.portfolio.futures:
        assert world.portfolio.futures[symbol].mark == world.engine.quote(symbol)["mark"]
    assert world.portfolio.cash != cash
    world.run(20)
    assert any(s["symbol"] == symbol for s in world.portfolio.settled)
    assert symbol not in world.portfolio.futures
