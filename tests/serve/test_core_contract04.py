"""Contract 0.4 in the core: the insolvency rule, the per-session news log,
and the step-bar window kept as a trimmed stream."""
from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor.portfolio import Portfolio
from tradefloor.serve.core import (INSOLVENT, STEP_BAR_SESSIONS, LocalSessionService,
                                   _risk_refusal)
from tradefloor.serve.store import FileStore, MemoryStore
from tradefloor.serve.types import Headline, OrderRequest, ServeError, SessionConfig

OWNER = "local"
NEWSY = SessionConfig(seed=3, universe_size=40)


def code_of(fn, *a, **kw) -> str:
    with pytest.raises(ServeError) as e:
        fn(*a, **kw)
    return e.value.code


# -- insolvency ---------------------------------------------------------------------------

def test_the_rule_on_a_bare_portfolio():
    engine = tf.Engine(seed=1, universe=tf.Universe.random(3, seed=111))
    engine.open_market()
    a, b = engine.tickers[0], engine.tickers[1]
    for cap in (None, 2.0):
        pf = Portfolio(cash=1_000.0, max_leverage=None)
        pf.execute(engine, a, 100)          # about $20,000 on $1,000 of cash
        pf.max_leverage = cap
        pf.cash -= 50_000.0                 # the market took the rest
        assert pf.net_worth(engine) < 0
        price = engine.book(a).best_bid
        assert _risk_refusal(pf, engine, a, -40, price) is None         # cut
        assert _risk_refusal(pf, engine, a, -100, price) is None        # close
        assert _risk_refusal(pf, engine, a, 10, price) == INSOLVENT     # add
        assert _risk_refusal(pf, engine, a, -150, price) is None        # flip, smaller
        assert _risk_refusal(pf, engine, a, -250, price) == INSOLVENT   # flip, larger
        assert _risk_refusal(pf, engine, b, 1, 10.0) == INSOLVENT       # new name


def test_an_order_accepted_solvent_and_filled_insolvent_is_rejected_insolvent():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=2, universe_size=4, cash=100_000.0,
                                        max_leverage=None)).session_id
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=200))
    svc.advance(OWNER, sid)
    add = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=50))
    cut = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="sell", quantity=50))
    # Losses the market could have made before the next step: the account is
    # insolvent when these orders reach the book.
    svc._cache[sid].portfolio.cash -= 200_000.0
    r = svc.advance(OWNER, sid)
    by_id = {o.order_id: o for o in svc.orders(OWNER, sid)}
    assert by_id[add.order_id].status == "rejected"
    assert by_id[add.order_id].reason == INSOLVENT == "insolvent"
    assert by_id[cut.order_id].status == "filled"
    assert [f.order_id for f in r.fills] == [cut.order_id]
    # And at submission, with no cap at all, adding is refused.
    with pytest.raises(ServeError) as e:
        svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=1))
    assert e.value.code == "insufficient_buying_power"
    assert e.value.message.startswith("insolvent")
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="sell", quantity=150))


# -- the news log ---------------------------------------------------------------------------

def test_advance_returns_every_headline_it_released():
    svc = LocalSessionService(MemoryStore())
    whole = svc.open(OWNER, NEWSY).session_id
    stepped = svc.open(OWNER, NEWSY).session_id
    r = svc.advance(OWNER, whole, 5, until="close")
    seen = []
    for _ in range(5 * 13):
        seen += svc.advance(OWNER, stepped).news
    assert r.news == seen
    assert len({h.day for h in r.news}) >= 3 and len(r.news) >= 6
    assert all(h.tick == 1 for h in r.news)
    # observe().news is still only the current session's.
    assert r.observation.news == [h for h in r.news if h.day == 4]
    assert svc.news(OWNER, whole) == r.news == svc.news(OWNER, stepped)
    # Advancing again releases nothing twice.
    assert all(h not in r.news for h in svc.advance(OWNER, whole, 2).news)


def test_news_query():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, NEWSY).session_id
    assert svc.news(OWNER, sid) == []
    log = svc.advance(OWNER, sid, 6, until="close").news
    assert svc.news(OWNER, sid) == log
    assert svc.news(OWNER, sid, since_day=2) == [h for h in log if h.day >= 2]
    assert svc.news(OWNER, sid, since_day=2, since_tick=2) == [h for h in log if h.day >= 3]
    assert svc.news(OWNER, sid, limit=2) == log[-2:]
    assert svc.news(OWNER, sid, since_day=99) == []
    for kw in (dict(since_day=-1), dict(since_tick=-1), dict(since_day=1.5),
               dict(limit=0), dict(limit=True)):
        assert code_of(svc.news, OWNER, sid, **kw) == "invalid_request"
    assert code_of(svc.news, "stranger", sid) == "not_found"


def test_news_log_survives_resume_and_fork(tmp_path):
    svc = LocalSessionService(FileStore(tmp_path))
    sid = svc.open(OWNER, NEWSY).session_id
    svc.advance(OWNER, sid, 3, until="close")
    svc.advance(OWNER, sid, 4)
    child = svc.fork(OWNER, sid).session_id
    fresh = LocalSessionService(FileStore(tmp_path))
    log = svc.news(OWNER, sid)
    assert log and fresh.news(OWNER, sid) == log == fresh.news(OWNER, child)
    # Mid-day resume: the rest of the day releases nothing already logged,
    # and the two paths stay in step.
    a = svc.advance(OWNER, sid, 20).news
    b = fresh.advance(OWNER, child, 20).news
    assert a == b
    assert fresh.news(OWNER, child) == svc.news(OWNER, sid) == log + a
    assert len(fresh.news(OWNER, child)) == len(set(
        (h.day, h.tick, tuple(h.tickers), h.text) for h in fresh.news(OWNER, child)))


def test_a_headlines_callable_is_logged_once_per_headline():
    def feed(engine, clock):
        if clock.tick < 90:
            return []
        return [Headline(day=clock.day, tick=90, tickers=[engine.tickers[0]],
                         text="Late wire", category="company")]

    svc = LocalSessionService(MemoryStore(), headlines=feed)
    sid = svc.open(OWNER, SessionConfig(seed=1, universe_size=3)).session_id
    assert svc.advance(OWNER, sid, 2).news == []
    (h,) = svc.advance(OWNER, sid, 2).news
    assert (h.day, h.tick, h.text) == (0, 90, "Late wire")
    assert svc.advance(OWNER, sid, 5).news == []
    r = svc.advance(OWNER, sid, until="close")
    r = svc.advance(OWNER, sid, 4)
    assert [x.day for x in r.news] == [1]
    assert [x.day for x in svc.news(OWNER, sid)] == [0, 1]


# -- the step-bar stream ------------------------------------------------------------------------

def test_a_failed_trim_does_not_fail_the_call(tmp_path):
    store = FileStore(tmp_path)
    svc = LocalSessionService(store)
    sid = svc.open(OWNER, SessionConfig(seed=2, universe_size=2,
                                        ticks_per_step=390)).session_id
    t = svc.info(OWNER, sid).tickers[0]
    real = store.trim_stream

    def broken(*a, **kw):
        raise OSError("read-only")

    store.trim_stream = broken
    svc.advance(OWNER, sid, 20, until="close")
    svc.advance(OWNER, sid, 20, until="close")
    svc.advance(OWNER, sid, 5, until="close")
    assert len(store.read_stream(sid, "step_bars")) == 45
    store.trim_stream = real
    svc.advance(OWNER, sid, until="close")         # the next boundary trims
    assert len(store.read_stream(sid, "step_bars")) == STEP_BAR_SESSIONS
    steps = svc.bars(OWNER, sid, t, "step")
    assert [b.day for b in steps] == list(range(46 - STEP_BAR_SESSIONS, 46))
    assert LocalSessionService(FileStore(tmp_path)).bars(OWNER, sid, t, "step") == steps
