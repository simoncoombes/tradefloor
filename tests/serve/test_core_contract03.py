"""Contract 0.2 and 0.3 behaviour not covered elsewhere: order updated_at,
close cancelling open orders, headlines in observe and their caveat, and
thread safety (section 4d)."""
from __future__ import annotations

import threading

import pytest

from tradefloor.headlines import headlines_for
from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import FileStore, MemoryStore
from tradefloor.serve.types import Clock, OrderRequest, SessionConfig

OWNER = "local"
CFG = SessionConfig(seed=4, universe_size=5, ticks_per_step=30)


# -- updated_at -------------------------------------------------------------------------

def test_updated_at_follows_each_status_change():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, CFG).session_id
    t = svc.info(OWNER, sid).tickers[0]
    last = svc.observe(OWNER, sid).quotes[0].last
    svc.advance(OWNER, sid, 2)
    market = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5))
    day = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5,
                                                   type="limit",
                                                   limit_price=round(last * 0.5, 2)))
    gone = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5,
                                                    type="limit", limit_price=1.0,
                                                    time_in_force="gtc"))
    assert market.updated_at == market.submitted_at == Clock(0, 60, 2, True)
    svc.advance(OWNER, sid, 3)
    cancelled = svc.cancel_order(OWNER, sid, gone.order_id)
    assert cancelled.updated_at == Clock(0, 150, 5, True)
    assert cancelled.submitted_at == Clock(0, 60, 2, True)
    svc.advance(OWNER, sid, until="close")
    by_id = {o.order_id: o for o in svc.orders(OWNER, sid)}
    assert by_id[market.order_id].updated_at == Clock(0, 60, 2, True)   # filled at step start
    assert by_id[day.order_id].status == "expired"
    assert by_id[day.order_id].updated_at == Clock(0, 390, 13, False)


def test_updated_at_on_rejection_and_resting_fill():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=4, universe_size=5, max_leverage=None)).session_id
    q = svc.observe(OWNER, sid).quotes[1]
    svc.place_order(OWNER, sid, OrderRequest(ticker=q.ticker, side="buy", quantity=5,
                                             type="limit", limit_price=q.last * 2))
    svc.advance(OWNER, sid)
    (o,) = svc.orders(OWNER, sid)
    assert o.status == "filled" and o.updated_at == Clock(0, 0, 0, True)
    lo = min(b.low for b in svc.bars(OWNER, sid, q.ticker, "step"))
    svc.place_order(OWNER, sid, OrderRequest(ticker=q.ticker, side="sell", quantity=5,
                                             type="limit", limit_price=round(lo * 1.5, 2),
                                             time_in_force="gtc"))
    rest = svc.orders(OWNER, sid, "open")[0]
    assert rest.updated_at == rest.submitted_at


# -- close ----------------------------------------------------------------------------------

def test_close_cancels_every_open_order():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, CFG).session_id
    t = svc.info(OWNER, sid).tickers[0]
    svc.advance(OWNER, sid, 3)
    queued = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5))
    resting = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5,
                                                       type="limit", limit_price=1.0,
                                                       time_in_force="gtc"))
    report = svc.close(OWNER, sid)
    assert report.fills == 0
    closing = Clock(0, 90, 3, True)
    for oid in (queued.order_id, resting.order_id):
        o = next(x for x in svc.orders(OWNER, sid) if x.order_id == oid)
        assert o.status == "cancelled" and o.reason == "session closed"
        assert o.updated_at == closing
    obs = svc.observe(OWNER, sid)
    assert obs.open_orders == [] and svc.orders(OWNER, sid, "open") == []
    assert report.state_hash == obs.state_hash


def test_close_cancellations_survive_resume(tmp_path):
    svc = LocalSessionService(FileStore(tmp_path))
    sid = svc.open(OWNER, CFG).session_id
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=5,
                                             type="limit", limit_price=1.0))
    svc.close(OWNER, sid)
    fresh = LocalSessionService(FileStore(tmp_path))
    assert fresh.orders(OWNER, sid) == svc.orders(OWNER, sid)
    assert fresh.orders(OWNER, sid)[0].status == "cancelled"


# -- headlines -----------------------------------------------------------------------------------

def test_observe_carries_todays_headlines():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=40)).session_id
    assert svc.observe(OWNER, sid).news == []            # tick 0: nothing released
    obs = svc.advance(OWNER, sid).observation
    assert len(obs.news) == 3
    s = svc._cache[sid]
    assert obs.news == headlines_for(s.engine, day=0, tick=30, tickers=s.tickers)
    assert all(h.day == 0 and h.tick == 1 for h in obs.news)
    assert {t for h in obs.news for t in h.tickers} <= set(s.tickers)
    # Still today's headlines at the close; none after the next open.
    closed = svc.advance(OWNER, sid, until="close").observation
    assert closed.news == obs.news
    opened = svc.advance(OWNER, sid, until="next_open").observation
    assert opened.news == []


def test_headlines_resume_and_fork_bit_identically(tmp_path):
    svc = LocalSessionService(FileStore(tmp_path))
    sid = svc.open(OWNER, SessionConfig(seed=5, universe_size=40)).session_id
    for _ in range(4):
        obs = svc.advance(OWNER, sid, 7).observation
        child = svc.fork(OWNER, sid).session_id
        fresh = LocalSessionService(FileStore(tmp_path))
        assert fresh.observe(OWNER, sid) == obs
        assert fresh.observe(OWNER, child).news == obs.news
    assert any(svc.advance(OWNER, sid, 13).observation.news for _ in range(3))


def test_a_headlines_callable_overrides_the_default():
    svc = LocalSessionService(MemoryStore(), headlines=lambda engine, clock: [])
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=40)).session_id
    assert svc.advance(OWNER, sid).observation.news == []


def test_news_caveat_is_computed_from_the_preset():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=40,
                                        ticks_per_step=30)).session_id
    (line,) = [c for c in svc.caveats(OWNER, sid) if c.startswith("News in")]
    assert "pt-v19" in line and "tick 30" in line and "92%" in line
    assert "5% of names" in line and "about 2 events a day" in line
    assert "1.40%" in line and "129bp" in line
    other = svc.open(OWNER, SessionConfig(seed=3, universe_size=10,
                                          ticks_per_step=195)).session_id
    (line,) = [c for c in svc.caveats(OWNER, other) if c.startswith("News in")]
    assert "tick 195" in line and "50%" in line
    quiet = svc.open(OWNER, SessionConfig(preset="pt-v10", universe_size=4)).session_id
    assert not [c for c in svc.caveats(OWNER, quiet) if c.startswith("News in")]
    assert all(svc.advance(OWNER, quiet, 13).observation.news == [] for _ in range(3))


# -- threads ----------------------------------------------------------------------------------------

def script(svc, sid):
    t = svc.info(OWNER, sid).tickers
    for k in range(8):
        svc.place_order(OWNER, sid, OrderRequest(ticker=t[k % len(t)],
                                                 side="buy" if k % 2 else "sell",
                                                 quantity=50 + k))
        svc.advance(OWNER, sid, 1 + k % 3)
        svc.observe(OWNER, sid)
    return svc.observe(OWNER, sid)


@pytest.mark.parametrize("store", ["memory", "file"])
def test_different_sessions_run_concurrently_and_match_serial_runs(tmp_path, store):
    make = (lambda: MemoryStore()) if store == "memory" else (lambda: FileStore(tmp_path))
    configs = [SessionConfig(seed=s, universe_size=6) for s in (1, 2, 3, 4)]
    serial_svc = LocalSessionService(MemoryStore())
    serial = []
    for cfg in configs:
        serial.append(script(serial_svc, serial_svc.open(OWNER, cfg).session_id))

    svc = LocalSessionService(make(), cache_size=2)      # forces evictions too
    ids = [svc.open(OWNER, cfg).session_id for cfg in configs]
    results: dict[int, object] = {}
    errors: list[BaseException] = []
    start = threading.Barrier(len(ids))

    def run(i):
        try:
            start.wait()
            results[i] = script(svc, ids[i])
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(len(ids))]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    for i, want in enumerate(serial):
        got = results[i]
        assert got.state_hash == want.state_hash
        assert got.quotes == want.quotes and got.account == want.account


def test_one_session_from_many_threads_stays_consistent(tmp_path):
    svc = LocalSessionService(FileStore(tmp_path))
    sid = svc.open(OWNER, CFG).session_id
    t = svc.info(OWNER, sid).tickers[0]
    errors: list[BaseException] = []

    def writer():
        try:
            for _ in range(5):
                svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=1))
                svc.advance(OWNER, sid)
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    def reader():
        try:
            for _ in range(20):
                obs = svc.observe(OWNER, sid)
                assert len(obs.state_hash) == 64
                svc.orders(OWNER, sid)
                svc.bars(OWNER, sid, t, "step")
        except BaseException as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=f) for f in (writer, writer, reader, reader)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    log = svc.calls(OWNER, sid)
    assert [c["seq"] for c in log] == list(range(1, 22))
    assert len(svc.fills(OWNER, sid)) == 10
    assert LocalSessionService(FileStore(tmp_path)).observe(OWNER, sid) == \
        svc.observe(OWNER, sid)
