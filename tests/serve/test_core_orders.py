"""Orders and fills in LocalSessionService: contract section 3.

The oracle for "what the market did" is a bare engine stepped exactly as
TradingEnv steps it (harness.session_clock, run_session per step). Resting
limit fills feed no order flow, so a session whose only trades are resting
fills must track that bare engine bit for bit; market orders must not.
"""
from __future__ import annotations

import math
import struct

import pytest

import tradefloor as tf
from tradefloor.harness import session_clock
from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import MemoryStore
from tradefloor.serve.types import OrderRequest, ServeError, SessionConfig

OWNER = "local"
CFG = SessionConfig(seed=5, universe_size=8, ticks_per_step=30)


def f64(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


class Oracle:
    """A bare engine on the session's config, stepped as TradingEnv does."""

    def __init__(self, cfg=CFG):
        self.cfg = cfg
        self.engine = tf.Engine(seed=cfg.seed, model=cfg.preset,
                                universe=tf.Universe.random(cfg.universe_size,
                                                            seed=cfg.universe_seed))
        self.engine.open_market()
        self.tickers = self.engine.tickers
        self.k = 0

    def step(self):
        """One step; returns {ticker: (low, high)} over its prints."""
        tps = self.cfg.ticks_per_step
        ticks = min(tps, 390 - self.k * tps)
        self.engine.run_session(*session_clock((9, 30, 3), self.k, tps), ticks,
                                order_flow={})
        self.k += 1
        n = len(self.tickers)
        p = f64(self.engine.session_prices())
        out = {t: (min(p[i::n]), max(p[i::n])) for i, t in enumerate(self.tickers)}
        if self.k * tps >= 390:
            self.engine.close_market()
            self.engine.open_market()
            self.k = 0
        return out

    def prices(self):
        return f64(self.engine.prices())


def svc_and(cfg=CFG, n=1, **kw):
    svc = LocalSessionService(MemoryStore(), **kw)
    ids = [svc.open(OWNER, cfg).session_id for _ in range(n)]
    return (svc, *ids)


def quote(svc, sid, ticker):
    return next(q for q in svc.observe(OWNER, sid).quotes if q.ticker == ticker)


def lasts(svc, sid):
    return [q.last for q in svc.observe(OWNER, sid).quotes]


# -- the untraded market is TradingEnv's market ------------------------------------

def test_untraded_session_steps_as_trading_env():
    svc, sid = svc_and()
    oracle = Oracle()
    assert lasts(svc, sid) == oracle.prices()
    for _ in range(16):                     # across a close and an open
        svc.advance(OWNER, sid)
        oracle.step()
        assert lasts(svc, sid) == oracle.prices()


# -- market orders ----------------------------------------------------------------------

def test_market_order_fills_at_next_step_start_and_moves_the_market():
    svc, traded, twin = svc_and(n=2)
    t = svc.info(OWNER, traded).tickers[2]
    book_ask = quote(svc, traded, t).ask
    order = svc.place_order(OWNER, traded, OrderRequest(ticker=t, side="buy",
                                                        quantity=1_500))
    assert order.status == "accepted" and order.filled_quantity == 0
    # Nothing fills until time moves.
    assert svc.fills(OWNER, traded) == []
    r = svc.advance(OWNER, traded)
    svc.advance(OWNER, twin)
    (fill,) = r.fills
    assert fill.liquidity == "taker" and fill.quantity == 1_500
    assert fill.at.tick == 0 and fill.at.step == 0          # the step's start
    assert fill.price >= book_ask                           # swept the offer
    done = svc.orders(OWNER, traded, status="filled")[0]
    assert done.avg_fill_price == fill.price and done.filled_quantity == 1_500
    # The flow reached the market: the traded session's market is no longer
    # the untraded twin's.
    a, b = svc.observe(OWNER, traded), svc.observe(OWNER, twin)
    assert a.state_hash != b.state_hash
    i = a.quotes.index(next(q for q in a.quotes if q.ticker == t))
    assert a.quotes[i].last != b.quotes[i].last or a.quotes[i].volume != b.quotes[i].volume
    pos = {p.ticker: p for p in a.positions}[t]
    assert pos.quantity == 1_500 and pos.avg_price == fill.price


def test_split_orders_sweep_cumulatively():
    """Two market buys in one step pay what one order of their sum pays, and
    leave the same market behind: splitting buys no better prices."""
    svc, split, whole = svc_and(n=2)
    t = svc.info(OWNER, split).tickers[0]
    svc.place_order(OWNER, split, OrderRequest(ticker=t, side="buy", quantity=1_000))
    svc.place_order(OWNER, split, OrderRequest(ticker=t, side="buy", quantity=1_200))
    svc.place_order(OWNER, whole, OrderRequest(ticker=t, side="buy", quantity=2_200))
    f1, f2 = svc.advance(OWNER, split).fills
    (fw,) = svc.advance(OWNER, whole).fills
    assert f2.price > f1.price
    assert math.isclose(f1.price * 1_000 + f2.price * 1_200, fw.price * 2_200,
                        rel_tol=1e-12)
    a, b = svc.observe(OWNER, split), svc.observe(OWNER, whole)
    assert [q.last for q in a.quotes] == [q.last for q in b.quotes]
    assert math.isclose(a.account.cash, b.account.cash, rel_tol=1e-12)


def test_market_order_beyond_the_book_fills_partially():
    cfg = SessionConfig(seed=5, universe_size=8, max_leverage=None)
    svc, sid = svc_and(cfg)
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=1e9))
    (fill,) = svc.advance(OWNER, sid).fills
    order = svc.orders(OWNER, sid)[0]
    assert order.status == "filled" and 0 < order.filled_quantity < 1e9
    assert order.reason.startswith("partial")
    assert fill.quantity == order.filled_quantity


def test_sell_short_is_allowed():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[1]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="sell", quantity=800))
    svc.advance(OWNER, sid)
    (p,) = svc.observe(OWNER, sid).positions
    assert p.quantity == -800


# -- limit orders -------------------------------------------------------------------------

def _resting_candidates(svc, sid, ranges):
    """Tickers where a buy at the step low and a sell at the step high are
    both non-marketable now, so they rest."""
    out = []
    for q in svc.observe(OWNER, sid).quotes:
        lo, hi = ranges[q.ticker]
        if lo < q.ask and hi > q.bid:
            out.append(q.ticker)
    return out


def test_resting_limits_fill_on_crossing_range_at_the_limit():
    svc, sid = svc_and()
    oracle = Oracle()
    ranges = oracle.step()
    names = _resting_candidates(svc, sid, ranges)
    assert len(names) >= 2
    t = names[0]
    lo, hi = ranges[t]
    orders = {
        "buy_at_low": OrderRequest(ticker=t, side="buy", quantity=100,
                                   type="limit", limit_price=lo),
        "buy_below": OrderRequest(ticker=t, side="buy", quantity=100,
                                  type="limit", limit_price=lo - 0.01),
        "sell_at_high": OrderRequest(ticker=t, side="sell", quantity=100,
                                     type="limit", limit_price=hi),
        "sell_above": OrderRequest(ticker=t, side="sell", quantity=100,
                                   type="limit", limit_price=hi + 0.01),
    }
    ids = {k: svc.place_order(OWNER, sid, r).order_id for k, r in orders.items()}
    r = svc.advance(OWNER, sid)
    by_order = {f.order_id: f for f in r.fills}
    assert set(by_order) == {ids["buy_at_low"], ids["sell_at_high"]}
    assert by_order[ids["buy_at_low"]].price == lo
    assert by_order[ids["sell_at_high"]].price == hi
    assert all(f.liquidity == "resting" for f in r.fills)
    assert r.fills[0].at.tick == 30 and r.fills[0].at.market_open
    open_ids = {o.order_id for o in r.observation.open_orders}
    assert open_ids == {ids["buy_below"], ids["sell_above"]}
    # Resting fills feed no flow: the market is still the bare engine's.
    assert [q.last for q in r.observation.quotes] == oracle.prices()


def test_marketable_limit_fills_as_capped_market_order():
    svc, sid, twin = svc_and(n=2)
    t = svc.info(OWNER, sid).tickers[3]
    q = quote(svc, sid, t)
    order = svc.place_order(OWNER, sid, OrderRequest(
        ticker=t, side="buy", quantity=300, type="limit",
        limit_price=round(q.ask * 1.05, 2)))
    (fill,) = svc.advance(OWNER, sid).fills
    svc.advance(OWNER, twin)
    assert fill.order_id == order.order_id and fill.liquidity == "taker"
    assert fill.at.tick == 0 and fill.price <= order.limit_price
    assert fill.price < order.limit_price          # the book's price, not the cap
    # It traded as a market order, so it moved the market.
    assert svc.observe(OWNER, sid).state_hash != svc.observe(OWNER, twin).state_hash


def test_marketable_limit_whose_sweep_is_worse_rests():
    """Limit at the ask for more than the top level holds: marketable when
    placed, but the sweep's average is above the limit, so it rests and never
    feeds flow."""
    svc, sid = svc_and()
    oracle = Oracle()
    t = svc.info(OWNER, sid).tickers[0]
    q = quote(svc, sid, t)
    order = svc.place_order(OWNER, sid, OrderRequest(
        ticker=t, side="buy", quantity=3_000, type="limit", limit_price=q.ask))
    assert svc.observe(OWNER, sid).open_orders[0].order_id == order.order_id
    r = svc.advance(OWNER, sid)
    lo, _ = oracle.step()[t]
    assert lo <= q.ask                      # this seed's range crosses it
    (fill,) = r.fills
    assert fill.liquidity == "resting" and fill.price == q.ask
    assert fill.order_id == order.order_id and fill.at.tick == 30
    assert [x.last for x in r.observation.quotes] == oracle.prices()


def test_day_limit_expires_at_close_and_gtc_carries_over():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[4]
    last = quote(svc, sid, t).last
    day = svc.place_order(OWNER, sid, OrderRequest(
        ticker=t, side="buy", quantity=10, type="limit",
        limit_price=round(last * 0.5, 2), time_in_force="day"))
    gtc = svc.place_order(OWNER, sid, OrderRequest(
        ticker=t, side="buy", quantity=10, type="limit",
        limit_price=round(last * 0.5, 2), time_in_force="gtc"))
    r = svc.advance(OWNER, sid, 12)            # one step short of the close
    assert r.expired == [] and len(r.observation.open_orders) == 2
    r = svc.advance(OWNER, sid)                # the close
    assert [o.order_id for o in r.expired] == [day.order_id]
    assert r.expired[0].status == "expired" and "close" in r.expired[0].reason
    assert [o.order_id for o in r.observation.open_orders] == [gtc.order_id]
    r = svc.advance(OWNER, sid, 2, until="close")     # two more sessions
    assert r.expired == []
    assert [o.order_id for o in r.observation.open_orders] == [gtc.order_id]
    assert svc.orders(OWNER, sid, status="expired")[0].order_id == day.order_id


def test_gtc_limit_fills_on_a_later_day():
    """A gtc buy below everything day 0 traded, above a low printed later,
    fills on that later day, at its limit."""
    for seed in range(1, 12):
        cfg = SessionConfig(seed=seed, universe_size=8, ticks_per_step=130)
        oracle = Oracle(cfg)
        day0 = [oracle.step() for _ in range(3)]
        later = [oracle.step() for _ in range(9)]      # days 1..3
        for t in oracle.tickers:
            low0 = min(r[t][0] for r in day0)
            low_later = min(r[t][0] for r in later)
            if low_later < low0 - 0.02:
                break
        else:
            continue
        break
    else:  # pragma: no cover
        pytest.fail("no seed printed a later low")
    svc, sid = svc_and(cfg)
    limit = round(low0 - 0.01, 2)
    order = svc.place_order(OWNER, sid, OrderRequest(
        ticker=t, side="buy", quantity=25, type="limit", limit_price=limit,
        time_in_force="gtc"))
    r = svc.advance(OWNER, sid, until="close")
    assert r.fills == [] and r.observation.open_orders
    r = svc.advance(OWNER, sid, 3, until="close")
    (fill,) = [f for f in r.fills if f.order_id == order.order_id]
    assert fill.at.day >= 1 and fill.price == limit and fill.liquidity == "resting"


def test_limit_order_placed_while_closed_is_for_the_next_session():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[0]
    svc.advance(OWNER, sid, until="close")
    last = quote(svc, sid, t).last
    o = svc.place_order(OWNER, sid, OrderRequest(
        ticker=t, side="buy", quantity=5, type="limit",
        limit_price=round(last * 0.5, 2)))
    assert o.submitted_at.market_open is False
    r = svc.advance(OWNER, sid, until="next_open")
    assert r.expired == [] and r.observation.open_orders[0].order_id == o.order_id
    r = svc.advance(OWNER, sid, until="close")
    assert [x.order_id for x in r.expired] == [o.order_id]


def test_cancel():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[0]
    m = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=10))
    c = svc.cancel_order(OWNER, sid, m.order_id)
    assert c.status == "cancelled"
    assert svc.advance(OWNER, sid).fills == []
    assert svc.orders(OWNER, sid, status="cancelled")[0].order_id == m.order_id
    with pytest.raises(ServeError) as e:
        svc.cancel_order(OWNER, sid, m.order_id)
    assert e.value.code == "invalid_request"


# -- idempotency ----------------------------------------------------------------------------

def test_client_order_id_same_body_returns_original():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[0]
    req = OrderRequest(ticker=t, side="buy", quantity=10, client_order_id="abc")
    first = svc.place_order(OWNER, sid, req)
    again = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy",
                                                     quantity=10.0,
                                                     client_order_id="abc"))
    assert again == first and len(svc.orders(OWNER, sid)) == 1
    svc.advance(OWNER, sid)
    after = svc.place_order(OWNER, sid, req)       # returns its current state
    assert after.order_id == first.order_id and after.status == "filled"
    assert len(svc.fills(OWNER, sid)) == 1


def test_client_order_id_different_body_conflicts():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=10,
                                             client_order_id="abc"))
    for other in (dict(quantity=11), dict(side="sell"), dict(type="limit", limit_price=1.0),
                  dict(time_in_force="gtc"), dict(ticker=svc.info(OWNER, sid).tickers[1])):
        body = dict(ticker=t, side="buy", quantity=10, client_order_id="abc")
        body.update(other)
        with pytest.raises(ServeError) as e:
            svc.place_order(OWNER, sid, OrderRequest(**body))
        assert e.value.code == "conflict"
    assert len(svc.orders(OWNER, sid)) == 1


def test_client_order_id_is_per_session():
    svc, a, b = svc_and(n=2)
    t = svc.info(OWNER, a).tickers[0]
    svc.place_order(OWNER, a, OrderRequest(ticker=t, side="buy", quantity=10,
                                           client_order_id="x"))
    o = svc.place_order(OWNER, b, OrderRequest(ticker=t, side="sell", quantity=3,
                                               client_order_id="x"))
    assert o.side == "sell"


# -- leverage -----------------------------------------------------------------------------------

def test_insufficient_buying_power_at_submission():
    svc, sid = svc_and()
    q = svc.observe(OWNER, sid).quotes[0]
    shares = 2.5 * 1_000_000 / q.last
    with pytest.raises(ServeError) as e:
        svc.place_order(OWNER, sid, OrderRequest(ticker=q.ticker, side="buy",
                                                 quantity=shares, type="limit",
                                                 limit_price=q.last))
    assert e.value.code == "insufficient_buying_power"
    assert svc.orders(OWNER, sid) == []


def test_buying_power_counts_queued_orders_ahead():
    svc, sid = svc_and()
    q = svc.observe(OWNER, sid).quotes[1]
    shares = 1.2 * 1_000_000 / q.last
    svc.place_order(OWNER, sid, OrderRequest(ticker=q.ticker, side="buy",
                                             quantity=shares, type="limit",
                                             limit_price=round(q.ask * 1.01, 2)))
    with pytest.raises(ServeError) as e:
        svc.place_order(OWNER, sid, OrderRequest(ticker=q.ticker, side="buy",
                                                 quantity=shares, type="limit",
                                                 limit_price=round(q.ask * 1.01, 2)))
    assert e.value.code == "insufficient_buying_power"


def test_resting_fill_that_would_breach_leverage_is_rejected():
    cfg = SessionConfig(seed=5, universe_size=8, max_leverage=2.0)
    svc, sid = svc_and(cfg)
    oracle = Oracle(cfg)
    ranges = oracle.step()
    names = _resting_candidates(svc, sid, ranges)
    t, u = names[0], names[1]
    lo = ranges[t][0]
    # A resting buy worth 1.2x, fine on its own when placed...
    lim = svc.place_order(OWNER, sid, OrderRequest(
        ticker=t, side="buy", quantity=1.2e6 / lo, type="limit", limit_price=lo))
    # ...then a market short worth 1.5x, fine when placed (the resting limit
    # may never fill, so it does not count against buying power)...
    px = quote(svc, sid, u).bid
    svc.place_order(OWNER, sid, OrderRequest(ticker=u, side="sell",
                                             quantity=round(1.5e6 / px)))
    r = svc.advance(OWNER, sid)
    (taker,) = r.fills
    assert taker.liquidity == "taker" and taker.ticker == u
    # ...so when the range crosses the limit, the fill would take the account
    # past 2x, and the order is rejected with the reason.
    got = next(o for o in svc.orders(OWNER, sid) if o.order_id == lim.order_id)
    assert got.status == "rejected" and "leverage" in got.reason
    assert got in svc.orders(OWNER, sid, "rejected")
    assert r.observation.account.leverage <= 2.0


# -- refusals -----------------------------------------------------------------------------------

@pytest.mark.parametrize("body", [
    dict(ticker="ZZZ"),
    dict(quantity=0), dict(quantity=-5), dict(quantity=float("nan")),
    dict(quantity=float("inf")), dict(quantity="10"), dict(quantity=2e12),
    dict(type="limit"),                                   # no price
    dict(type="limit", limit_price=0.0), dict(type="limit", limit_price=-1.0),
    dict(type="limit", limit_price=float("nan")),
    dict(type="limit", limit_price=float("inf")),
    dict(limit_price=10.0),                               # market with a price
])
def test_invalid_order(body):
    svc, sid = svc_and()
    base = dict(ticker=svc.info(OWNER, sid).tickers[0], side="buy", quantity=10)
    base.update(body)
    with pytest.raises(ServeError) as e:
        svc.place_order(OWNER, sid, OrderRequest(**base))
    assert e.value.code == "invalid_order"
    assert svc.orders(OWNER, sid) == []


@pytest.mark.parametrize("body", [
    dict(side="hold"), dict(type="stop"), dict(time_in_force="ioc"),
    dict(client_order_id=""), dict(client_order_id="x" * 129),
])
def test_malformed_order_is_invalid_request(body):
    svc, sid = svc_and()
    base = dict(ticker=svc.info(OWNER, sid).tickers[0], side="buy", quantity=10)
    base.update(body)
    with pytest.raises(ServeError) as e:
        svc.place_order(OWNER, sid, OrderRequest(**base))
    assert e.value.code == "invalid_request"


def test_order_as_dict_and_unknown_fields():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[0]
    o = svc.place_order(OWNER, sid, {"ticker": t, "side": "buy", "quantity": 3})
    assert o.quantity == 3.0 and isinstance(o.quantity, float)
    with pytest.raises(ServeError) as e:
        svc.place_order(OWNER, sid, {"ticker": t, "side": "buy", "quantity": 3,
                                     "stop_price": 1})
    assert e.value.code == "invalid_request"


def test_unknown_order_is_not_found():
    svc, sid = svc_and()
    with pytest.raises(ServeError) as e:
        svc.cancel_order(OWNER, sid, "ord-999999")
    assert e.value.code == "not_found"


def test_orders_status_filter():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[0]
    a = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5))
    b = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5,
                                                 type="limit", limit_price=1.0))
    svc.advance(OWNER, sid)
    assert [o.order_id for o in svc.orders(OWNER, sid)] == [a.order_id, b.order_id]
    assert [o.order_id for o in svc.orders(OWNER, sid, "open")] == [b.order_id]
    assert [o.order_id for o in svc.orders(OWNER, sid, "accepted")] == [b.order_id]
    assert [o.order_id for o in svc.orders(OWNER, sid, "closed")] == [a.order_id]
    assert [o.order_id for o in svc.orders(OWNER, sid, "filled")] == [a.order_id]
    assert svc.orders(OWNER, sid, "all") == svc.orders(OWNER, sid)
    with pytest.raises(ServeError) as e:
        svc.orders(OWNER, sid, "bogus")
    assert e.value.code == "invalid_request"


def test_fills_since_day():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5))
    svc.advance(OWNER, sid, until="next_open")
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="sell", quantity=5))
    svc.advance(OWNER, sid)
    assert [f.at.day for f in svc.fills(OWNER, sid)] == [0, 1]
    assert [f.at.day for f in svc.fills(OWNER, sid, since_day=1)] == [1]
    for bad in (-1, 1.5, "0", True):
        with pytest.raises(ServeError) as e:
            svc.fills(OWNER, sid, since_day=bad)
        assert e.value.code == "invalid_request"


def test_returned_objects_are_copies():
    svc, sid = svc_and()
    t = svc.info(OWNER, sid).tickers[0]
    o = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5,
                                                 type="limit", limit_price=1.0))
    o.status = "filled"
    obs = svc.observe(OWNER, sid)
    obs.open_orders[0].quantity = 1e9
    assert svc.orders(OWNER, sid)[0].status == "accepted"
    assert svc.orders(OWNER, sid)[0].quantity == 5
