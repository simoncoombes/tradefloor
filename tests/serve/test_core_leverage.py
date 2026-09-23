"""The leverage cap under contract 0.2 and the insolvent account (section 4a).

A fill is refused only when its projected leverage is above the cap AND above
the account's leverage now. So an account the market has pushed over its cap
can always trade back down, which `Portfolio`'s own rule (refuse anything
projected over the cap) would not allow.
"""
from __future__ import annotations

import json

import pytest

from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import FileStore, MemoryStore
from tradefloor.serve.types import OrderRequest, ServeError, SessionConfig

OWNER = "local"


def code_of(fn, *a, **kw) -> str:
    with pytest.raises(ServeError) as e:
        fn(*a, **kw)
    return e.value.code


def over_the_cap(seed: int, side: str, *, store=None):
    """A session holding ~1.97x in its first name, advanced (no trading)
    until the market has taken it past its 2x cap."""
    svc = LocalSessionService(store or MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=seed, universe_size=4, max_leverage=2.0,
                                        cash=100_000.0, ticks_per_step=65)).session_id
    q = svc.observe(OWNER, sid).quotes[0]
    qty = int(1.97 * 100_000 / q.ask)
    svc.place_order(OWNER, sid, OrderRequest(ticker=q.ticker, side=side, quantity=qty))
    svc.advance(OWNER, sid)
    for _ in range(40):
        obs = svc.advance(OWNER, sid).observation
        if obs.account.leverage > 2.05:
            return svc, sid, q.ticker, qty, obs
    raise AssertionError("the market never pushed this account over its cap")


@pytest.mark.parametrize("seed,side", [(1, "buy"), (8, "sell")])
def test_an_account_over_its_cap_can_always_reduce(seed, side):
    svc, sid, t, held, obs = over_the_cap(seed, side)
    before = obs.account.leverage
    assert before > 2.05
    unwind = "sell" if side == "buy" else "buy"
    # A small reduction: leverage stays above the cap after it, so
    # Portfolio's own rule would refuse it. The contract's rule lets it pass.
    small = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side=unwind,
                                                     quantity=round(held * 0.01)))
    r = svc.advance(OWNER, sid)
    (fill,) = r.fills
    assert fill.order_id == small.order_id and fill.liquidity == "taker"
    assert r.observation.account.leverage > 2.0     # still over the cap
    assert svc.orders(OWNER, sid, "filled")[-1].order_id == small.order_id
    # Adding risk is refused at submission, in the same name or another.
    assert code_of(svc.place_order, OWNER, sid, OrderRequest(
        ticker=t, side=side, quantity=10)) == "insufficient_buying_power"
    other = svc.observe(OWNER, sid).quotes[2]
    assert code_of(svc.place_order, OWNER, sid, OrderRequest(
        ticker=other.ticker, side="buy", quantity=10)) == "insufficient_buying_power"
    # A resting limit that reduces fills when crossed, over the cap or not.
    last = next(q.last for q in svc.observe(OWNER, sid).quotes if q.ticker == t)
    lim = svc.place_order(OWNER, sid, OrderRequest(
        ticker=t, side=unwind, quantity=round(held * 0.01), type="limit",
        limit_price=round(last * (0.9995 if unwind == "sell" else 1.0005), 2)))
    for _ in range(20):
        if svc.orders(OWNER, sid, "open") == []:
            break
        svc.advance(OWNER, sid)
    got = next(o for o in svc.orders(OWNER, sid) if o.order_id == lim.order_id)
    assert got.status == "filled", got
    # And the whole position can be closed.
    remaining = next(p.quantity for p in svc.observe(OWNER, sid).positions if p.ticker == t)
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side=unwind,
                                             quantity=abs(remaining)))
    r = svc.advance(OWNER, sid)
    assert r.fills and r.observation.positions == []
    assert r.observation.account.leverage == 0.0
    assert before > 2.0 > r.observation.account.leverage


def test_the_reduction_passes_where_portfolio_would_refuse():
    """Checked on the account itself: the small unwind's projected leverage
    is above the cap (Portfolio.execute would raise) and below now."""
    svc, sid, t, held, obs = over_the_cap(1, "buy")
    s = svc._cache[sid]
    pf, engine = s.portfolio, s.engine
    qty = -round(held * 0.01)
    price = engine.book(t).sweep_cost("sell", -qty).average_price
    projected = pf._projected_leverage(engine, t, qty, price, qty * price)
    assert pf.max_leverage < projected < pf.leverage(engine)
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="sell", quantity=-qty))
    assert svc.advance(OWNER, sid).fills


def test_a_fill_that_raises_leverage_over_the_cap_is_still_refused():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=4, max_leverage=1.0,
                                        cash=100_000.0)).session_id
    q = svc.observe(OWNER, sid).quotes[0]
    assert code_of(svc.place_order, OWNER, sid, OrderRequest(
        ticker=q.ticker, side="buy", quantity=int(1.2e5 / q.ask))) \
        == "insufficient_buying_power"
    # Two orders that pass one at a time: the second is refused at
    # submission because the first is queued ahead of it.
    svc.place_order(OWNER, sid, OrderRequest(ticker=q.ticker, side="buy",
                                             quantity=int(0.6e5 / q.ask)))
    assert code_of(svc.place_order, OWNER, sid, OrderRequest(
        ticker=q.ticker, side="buy", quantity=int(0.6e5 / q.ask))) \
        == "insufficient_buying_power"


def insolvent_session(store=None):
    svc = LocalSessionService(store or MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=6, universe_size=4, max_leverage=60.0,
                                        cash=10_000.0)).session_id
    q = svc.observe(OWNER, sid).quotes[1]
    qty = int(55 * 10_000 / q.ask)
    svc.place_order(OWNER, sid, OrderRequest(ticker=q.ticker, side="buy", quantity=qty))
    svc.advance(OWNER, sid)
    for _ in range(60):
        obs = svc.advance(OWNER, sid).observation
        if obs.account.insolvent:
            return svc, sid, q.ticker, qty, obs
    raise AssertionError("the account never became insolvent")


def test_insolvent_account_reports_null_leverage(tmp_path):
    svc, sid, t, qty, obs = insolvent_session(FileStore(tmp_path))
    a = obs.account
    assert a.net_worth <= 0 and a.insolvent is True and a.leverage is None
    text = json.dumps(obs.to_dict(), allow_nan=False)     # strict JSON works
    assert '"leverage": null' in text and '"insolvent": true' in text
    fresh = LocalSessionService(FileStore(tmp_path)).observe(OWNER, sid)
    assert fresh == obs
    report = svc.close(OWNER, sid)
    assert report.account.leverage is None and report.account.insolvent
    assert any("insolvent" in c for c in report.caveats)


def test_insolvent_account_can_cut_exposure_but_not_add_it():
    svc, sid, t, qty, obs = insolvent_session()
    assert code_of(svc.place_order, OWNER, sid, OrderRequest(
        ticker=t, side="buy", quantity=100)) == "insufficient_buying_power"
    other = obs.quotes[3].ticker
    assert code_of(svc.place_order, OWNER, sid, OrderRequest(
        ticker=other, side="sell", quantity=100)) == "insufficient_buying_power"
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="sell", quantity=qty // 2))
    r = svc.advance(OWNER, sid)
    assert r.fills and r.fills[0].quantity == qty // 2
    assert r.observation.account.gross_exposure < obs.account.gross_exposure


def test_solvent_accounts_report_leverage_and_not_insolvent():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=4)).session_id
    a = svc.observe(OWNER, sid).account
    assert a.leverage == 0.0 and a.insolvent is False
