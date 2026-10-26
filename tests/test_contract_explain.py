"""Why a contract moved (`tradefloor.contracts.explain_move`) and strategy
specs that hold the index future (`StrategySpec`'s ``contracts`` section).

Both read public state only and add no draw, so a market runs exactly as it
did: an untraded run's closes are compared bit for bit here, and the known
answers in tests/known_answer.py do not move.
"""
from __future__ import annotations

import json
import struct

import pytest

import tradefloor as tf
from tradefloor.contracts import explain_move
from tradefloor.portfolio import Portfolio

from test_futures_accounts import CONTRACTS, SMALL, model
from test_name_options import ON


def closes(engine):
    return struct.unpack("<%dd" % len(engine.tickers), engine.prices())


def run_days(engine, days):
    for _ in range(days):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()


def front(engine, root):
    return next(c["symbol"] for c in engine.contracts() if c["root"] == root and c["front"])


# -- explain_move --------------------------------------------------------------------------


def test_a_futures_move_splits_into_underlying_carry_and_basis_exactly():
    e = tf.Engine(seed=3, universe=SMALL, model=model(**CONTRACTS))
    run_days(e, 1)
    sym = front(e, "IDX")
    before = e.quote(sym)
    run_days(e, 1)
    after = e.quote(sym)
    got = explain_move(before, after, quantity=2)
    assert got["symbol"] == sym and got["kind"] == "future"
    assert set(got["parts"]) == {"underlying", "carry", "basis"}
    assert sum(got["parts"].values()) == got["move"] == after["price"] - before["price"]
    ds = after["index"] - before["index"]
    assert got["parts"]["underlying"] == pytest.approx(ds * before["fair"] / before["index"])
    assert got["dollars"]["basis"] == pytest.approx(got["parts"]["basis"] * 250 * 2)
    assert got["sessions"] == pytest.approx(1.0)


def test_a_rate_futures_move_is_the_expected_rate_the_premium_and_the_basis():
    e = tf.Engine(seed=3, universe=SMALL, model=model(**CONTRACTS))
    run_days(e, 1)
    sym = front(e, "FF")
    before = e.quote(sym)
    run_days(e, 3)
    after = e.quote(sym)
    got = explain_move(before, after)
    assert set(got["parts"]) == {"expected_rate", "premium", "basis"}
    assert sum(got["parts"].values()) == pytest.approx(got["move"], abs=1e-12)
    assert got["parts"]["expected_rate"] == pytest.approx(-(after["expected"] - before["expected"]))


def test_an_options_move_splits_by_its_greeks_and_the_rest_is_named():
    e = tf.Engine(seed=4, universe=SMALL, model=model(**ON))
    run_days(e, 1)
    name = e.name_option_roots()[0]
    expiry = e.option_expiries(name)[0]["expiry"]
    row = min(e.chain(name, expiry), key=lambda r: abs(r["delta"] - 0.5))
    before = e.quote(row["symbol"])
    run_days(e, 1)
    after = e.quote(row["symbol"])
    got = explain_move(before, after, quantity=-3)
    assert set(got["parts"]) == {"underlying", "volatility", "time", "other"}
    assert sum(got["parts"].values()) == pytest.approx(got["move"], abs=1e-12)
    ds = after["index"] - before["index"]
    assert got["parts"]["underlying"] == pytest.approx(before["delta"] * ds + 0.5 * before["gamma"] * ds * ds)
    assert got["parts"]["time"] == pytest.approx(before["theta"] * got["sessions"])
    assert got["dollars"]["time"] == pytest.approx(got["parts"]["time"] * 100 * -3)


def test_two_contracts_or_a_quote_without_its_fields_are_refused():
    e = tf.Engine(seed=3, universe=SMALL, model=model(**CONTRACTS))
    run_days(e, 1)
    a, b = e.quote(front(e, "IDX")), e.quote(front(e, "OIL"))
    with pytest.raises(tf.ValidationError, match="one contract"):
        explain_move(a, b)
    bare = dict(a, fair=None)
    with pytest.raises(tf.ValidationError, match="'fair'"):
        explain_move(bare, a)


def test_explaining_reads_quotes_only_and_moves_no_price():
    one = tf.Engine(seed=3, universe=SMALL, model=model(**CONTRACTS))
    two = tf.Engine(seed=3, universe=SMALL, model=model(**CONTRACTS))
    for _ in range(3):
        run_days(one, 1)
        run_days(two, 1)
        sym = front(two, "IDX")
        q = two.quote(sym)
        explain_move(q, q)
    assert closes(one) == closes(two)
    assert one.state_hash() == two.state_hash()


# -- specs that hold the index future -----------------------------------------------------------


def test_a_spec_without_contracts_is_written_and_hashed_as_before():
    spec = tf.StrategySpec.momentum(top_k=3)
    assert "contracts" not in spec.as_dict() and spec.contracts is None
    assert set(json.loads(spec.to_json())) == {"spec_version", "signal", "portfolio", "execution", "seed"}


def test_the_contracts_section_round_trips_hashes_apart_and_is_checked():
    base = tf.StrategySpec.momentum(top_k=3)
    hedged = base.with_index_future(hedge_ratio=1.0)
    assert hedged.contracts == {"index_future": {"hedge_ratio": 1.0}}
    assert tf.StrategySpec.from_json(hedged.to_json()) == hedged
    assert hedged.fingerprint != base.fingerprint
    assert hedged.spec_version == base.spec_version == 1
    assert base.with_index_future(weight=0.5).contracts == {"index_future": {"weight": 0.5}}
    with pytest.raises(tf.ValidationError, match="one of the two"):
        base.with_index_future(hedge_ratio=1.0, weight=0.5)
    with pytest.raises(tf.ValidationError, match="0 or more"):
        base.with_index_future(hedge_ratio=-1.0)
    with pytest.raises(tf.ValidationError):
        tf.StrategySpec({"kind": "hold"}, contracts={"vix_future": {"weight": 1.0}})


class _Market:
    lists_futures = True

    def __init__(self, listed, price=1000.0):
        self._listed, self._price = listed, price

    def contracts(self):
        return self._listed

    def quote(self, symbol):
        return {"price": self._price, "multiplier": 250.0}


class _Book:
    def __init__(self, held, worth=1_000_000.0):
        self._held, self._worth = held, worth

    def quantity_of(self, ticker):
        return self._held.get(ticker, 0.0)

    def net_worth(self, engine=None):
        return self._worth


class _Inner:
    def __init__(self, orders):
        self.orders = orders

    def act(self, obs):
        return dict(self.orders)


class _Obs:
    def __init__(self, market, book, tickers, prices):
        self.engine, self.portfolio, self.tickers, self.prices = market, book, tickers, prices


def test_the_overlay_hedges_the_book_after_its_orders_and_rolls():
    from tradefloor.spec import _IndexFutureOverlay
    listed = [{"symbol": "IDX.F0056", "root": "IDX", "kind": "future", "front": False},
              {"symbol": "IDX.F0119", "root": "IDX", "kind": "future", "front": True}]
    book = _Book({"AAA": 5000.0, "IDX.F0056": -3.0})
    agent = _IndexFutureOverlay(_Inner({"AAA": 1000.0}), {"hedge_ratio": 1.0})
    got = agent.act(_Obs(_Market(listed), book, ["AAA"], [100.0]))
    # 6,000 shares at 100 is 600,000 of exposure: 600,000 / (1000 x 250) is 2.4, so 2 short.
    assert got == {"AAA": 1000.0, "IDX.F0056": 3.0, "IDX.F0119": -2.0}
    weighted = _IndexFutureOverlay(_Inner({}), {"weight": 0.5})
    assert weighted.act(_Obs(_Market(listed), _Book({}), ["AAA"], [100.0])) == {"IDX.F0119": 2.0}
    off = _Market(listed)
    off.lists_futures = False
    assert agent.act(_Obs(off, book, ["AAA"], [100.0])) == {"AAA": 1000.0}


def test_a_hedged_spec_evaluates_and_trades_the_index_future():
    spec = tf.StrategySpec.momentum(top_k=2).with_index_future(hedge_ratio=1.0)
    cards = tf.evaluate({"hedged": spec, "plain": tf.StrategySpec.momentum(top_k=2)}, seed=3,
                        universe=SMALL, days=3, steps_per_day=2, model=model(**CONTRACTS))
    assert cards["hedged"].rejected == 0 and not cards["hedged"].errors
    assert cards["hedged"].strategy_fingerprint == spec.fingerprint
    assert cards["hedged"].trades > cards["plain"].trades


def test_on_a_model_without_futures_the_section_changes_nothing():
    spec = tf.StrategySpec.momentum(top_k=2)
    cards = tf.evaluate({"hedged": spec.with_index_future(hedge_ratio=1.0), "plain": spec},
                        seed=3, universe=SMALL, days=2, steps_per_day=2, model="pt-v21")
    assert cards["hedged"].trades == cards["plain"].trades
    assert cards["hedged"].pnl == pytest.approx(cards["plain"].pnl)


def test_a_hedged_portfolio_holds_futures_through_the_settlement_hooks():
    e = tf.Engine(seed=3, universe=SMALL, model=model(**CONTRACTS))
    p = Portfolio(cash=1_000_000.0, owner="a")
    run_days(e, 1)
    e.open_market()
    p.settle_open(e)
    p.execute(e, front(e, "IDX"), -2)
    e.run_session(9, 30, 3, 390)
    e.close_market()
    p.settle_close(e)
    assert list(p.futures.values())[0].quantity == -2
