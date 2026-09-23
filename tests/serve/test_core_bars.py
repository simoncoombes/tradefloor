"""Bars and step volume (contract 0.3, section 4b)."""
from __future__ import annotations

import struct

import pytest

import tradefloor as tf
from tradefloor.harness import session_clock
from tradefloor.serve.core import STEP_BAR_SESSIONS, LocalSessionService
from tradefloor.serve.store import FileStore, MemoryStore
from tradefloor.serve.types import Bar, OrderRequest, ServeError, SessionConfig

OWNER = "local"
CFG = SessionConfig(seed=4, universe_size=5, ticks_per_step=30)


def f64(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def code_of(fn, *a, **kw) -> str:
    with pytest.raises(ServeError) as e:
        fn(*a, **kw)
    return e.value.code


def test_step_bars_are_the_prints_of_each_step():
    """Against a bare engine stepped as TradingEnv steps it."""
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, CFG).session_id
    engine = tf.Engine(seed=CFG.seed, universe=tf.Universe.random(5, seed=111))
    engine.open_market()
    n, t, j = 5, svc.info(OWNER, sid).tickers[2], 2
    for k in range(4):
        before = f64(engine.column("volume"))[j]
        engine.run_session(*session_clock((9, 30, 3), k, 30), 30, order_flow={})
        series = f64(engine.session_prices())[j::n]
        svc.advance(OWNER, sid)
        bar = svc.bars(OWNER, sid, t, "step")[-1]
        assert bar == Bar(ticker=t, day=0, step=k, open=series[0], high=max(series),
                          low=min(series), close=series[-1],
                          volume=f64(engine.column("volume"))[j] - before)
    quote = next(q for q in svc.observe(OWNER, sid).quotes if q.ticker == t)
    assert quote.step_volume == bar.volume and quote.last == bar.close


def test_day_bars_agree_with_quotes_and_step_bars():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, CFG).session_id
    t = svc.info(OWNER, sid).tickers[0]
    assert svc.bars(OWNER, sid, t) == []                  # nothing traded yet
    closes = []
    for _ in range(3):
        obs = svc.advance(OWNER, sid, until="close").observation
        q = next(x for x in obs.quotes if x.ticker == t)
        closes.append(q)
    svc.advance(OWNER, sid, 2)
    days = svc.bars(OWNER, sid, t)
    assert [b.day for b in days] == [0, 1, 2, 3] and all(b.step is None for b in days)
    for bar, q in zip(days, closes):
        assert (bar.open, bar.high, bar.low, bar.close, bar.volume) == \
            (q.day_open, q.day_high, q.day_low, q.last, q.volume)
    steps = svc.bars(OWNER, sid, t, "step")
    for bar in days[:3]:
        mine = [s for s in steps if s.day == bar.day]
        assert [s.step for s in mine] == list(range(13))
        assert sum(s.volume for s in mine) == bar.volume
        assert max(max(s.high for s in mine), bar.open) == bar.high
        assert min(min(s.low for s in mine), bar.open) == bar.low
        assert mine[-1].close == bar.close
    # The session in progress: its bar so far, up to the current step.
    live = days[-1]
    q = next(x for x in svc.observe(OWNER, sid).quotes if x.ticker == t)
    assert (live.open, live.high, live.low, live.close, live.volume) == \
        (q.day_open, q.day_high, q.day_low, q.last, q.volume)
    assert [s.step for s in steps if s.day == 3] == [0, 1]


def test_step_bars_keep_twenty_sessions_and_day_bars_keep_all():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=4, universe_size=2,
                                        ticks_per_step=130)).session_id
    t = svc.info(OWNER, sid).tickers[1]
    svc.advance(OWNER, sid, 20, until="close")
    svc.advance(OWNER, sid, 5, until="close")
    svc.advance(OWNER, sid)                              # day 25, one step
    days = svc.bars(OWNER, sid, t)
    assert [b.day for b in days] == list(range(26))
    steps = svc.bars(OWNER, sid, t, "step")
    assert sorted({b.day for b in steps}) == list(range(26 - STEP_BAR_SESSIONS, 26))
    assert len(steps) == (STEP_BAR_SESSIONS - 1) * 3 + 1
    # since_day and limit.
    assert [b.day for b in svc.bars(OWNER, sid, t, since_day=24)] == [24, 25]
    assert [b.day for b in svc.bars(OWNER, sid, t, limit=3)] == [23, 24, 25]
    assert svc.bars(OWNER, sid, t, "step", since_day=24, limit=2) == steps[-2:]
    assert svc.bars(OWNER, sid, t, since_day=99) == []


@pytest.mark.parametrize("kw", [dict(ticker="ZZZ"), dict(resolution="minute"),
                                dict(since_day=-1), dict(since_day=1.0),
                                dict(limit=0), dict(limit=-2), dict(limit=2.5),
                                dict(ticker=None)])
def test_bars_refusals(kw):
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, CFG).session_id
    args = dict(ticker=svc.info(OWNER, sid).tickers[0])
    args.update(kw)
    assert code_of(svc.bars, OWNER, sid, **args) == "invalid_request"
    assert code_of(svc.bars, "someone-else", sid, svc.info(OWNER, sid).tickers[0]) \
        == "not_found"


def test_step_volume_through_the_day():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, CFG).session_id
    assert all(q.step_volume is None for q in svc.observe(OWNER, sid).quotes)
    obs = svc.advance(OWNER, sid, 2).observation
    t = obs.quotes[0].ticker
    assert obs.quotes[0].step_volume == svc.bars(OWNER, sid, t, "step")[-1].volume
    obs = svc.advance(OWNER, sid, until="close").observation
    assert obs.quotes[0].step_volume == svc.bars(OWNER, sid, t, "step")[-1].volume
    assert obs.quotes[0].step_volume > 0
    obs = svc.advance(OWNER, sid, until="next_open").observation
    assert all(q.step_volume is None for q in obs.quotes)


@pytest.mark.parametrize("steps", [3, 13, 20])
def test_bars_survive_resume_and_fork(tmp_path, steps):
    svc = LocalSessionService(FileStore(tmp_path))
    sid = svc.open(OWNER, CFG).session_id
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=500))
    svc.advance(OWNER, sid, steps)
    child = svc.fork(OWNER, sid).session_id
    fresh = LocalSessionService(FileStore(tmp_path))
    for tick in svc.info(OWNER, sid).tickers:
        for res in ("day", "step"):
            want = svc.bars(OWNER, sid, tick, res)
            assert fresh.bars(OWNER, sid, tick, res) == want
            assert fresh.bars(OWNER, child, tick, res) == want
    assert fresh.observe(OWNER, sid).quotes == svc.observe(OWNER, sid).quotes
    # And they keep agreeing as all three move on.
    svc.advance(OWNER, sid, 15)
    fresh.advance(OWNER, child, 15)
    assert svc.bars(OWNER, sid, t, "step") == fresh.bars(OWNER, child, t, "step")
    assert svc.bars(OWNER, sid, t) == fresh.bars(OWNER, child, t)
