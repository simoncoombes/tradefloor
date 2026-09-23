"""Determinism, kill-and-resume and fork for LocalSessionService.

Contract section 5: same config and the same ordered calls give bit-identical
responses and the same state_hash after every call; a new service over the
same store serves a session exactly as the old one would have; a fork and its
parent differ only once their calls do.
"""
from __future__ import annotations

import json
import struct

import pytest

from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import FileStore, MemoryStore
from tradefloor.serve.types import OrderRequest, SessionConfig

OWNER = "local"


def _bits(value):
    """A value with floats as bit patterns, so == is a bitwise comparison."""
    if isinstance(value, float):
        return struct.pack("<d", value).hex()
    if isinstance(value, dict):
        return {k: _bits(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bits(v) for v in value]
    return value


def _same(a, b) -> bool:
    """Observations equal bit for bit, apart from the session id."""
    da, db = a.to_dict(), b.to_dict()
    da.pop("session_id", None)
    db.pop("session_id", None)
    return _bits(da) == _bits(db)


def _script(tickers):
    """A call sequence exercising every order path and every advance unit."""
    a, b, c = tickers[0], tickers[1], tickers[2]
    return [
        ("order", OrderRequest(ticker=a, side="buy", quantity=2_000)),
        ("order", OrderRequest(ticker=b, side="sell", quantity=5_000)),
        ("advance", 1, "steps"),
        ("order", OrderRequest(ticker=a, side="buy", quantity=500,
                               client_order_id="k1")),
        ("order", OrderRequest(ticker=a, side="buy", quantity=500)),
        ("limit", c, "buy", 0.995, "gtc"),
        ("limit", b, "buy", 0.99, "day"),
        ("advance", 3, "steps"),
        ("order", OrderRequest(ticker=b, side="buy", quantity=5_000)),
        ("limit", a, "sell", 1.004, "gtc"),
        ("advance", 1, "close"),
        ("order", OrderRequest(ticker=c, side="sell", quantity=1_000)),
        ("advance", 1, "next_open"),
        ("limit", c, "buy", 1.001, "day"),     # marketable at submission
        ("advance", 2, "steps"),
        ("cancel_first_open",),
        ("advance", 5, "steps"),
    ]


def _run(service, session_id, call, after=None):
    """Apply one scripted call; returns the observation after it."""
    kind = call[0]
    if kind == "order":
        service.place_order(OWNER, session_id, call[1])
    elif kind == "limit":
        _, ticker, side, frac, tif = call
        obs = service.observe(OWNER, session_id)
        last = next(q.last for q in obs.quotes if q.ticker == ticker)
        service.place_order(OWNER, session_id, OrderRequest(
            ticker=ticker, side=side, quantity=300, type="limit",
            limit_price=round(last * frac, 2), time_in_force=tif))
    elif kind == "advance":
        service.advance(OWNER, session_id, call[1], until=call[2])
    elif kind == "cancel_first_open":
        open_ = service.orders(OWNER, session_id, status="open")
        if open_:
            service.cancel_order(OWNER, session_id, open_[0].order_id)
    return service.observe(OWNER, session_id)


CONFIG = SessionConfig(seed=5, universe_size=8, ticks_per_step=65)


def test_same_calls_same_bytes():
    """(a) Two sessions, same config, same calls: identical after every call."""
    svc = LocalSessionService(MemoryStore())
    s1 = svc.open(OWNER, CONFIG).session_id
    s2 = svc.open(OWNER, CONFIG).session_id
    assert _same(svc.observe(OWNER, s1), svc.observe(OWNER, s2))
    for call in _script(svc.info(OWNER, s1).tickers):
        o1 = _run(svc, s1, call)
        o2 = _run(svc, s2, call)
        assert _same(o1, o2), call
        assert o1.state_hash == o2.state_hash
    f1 = [f.to_dict() for f in svc.fills(OWNER, s1)]
    f2 = [f.to_dict() for f in svc.fills(OWNER, s2)]
    assert _bits(f1) == _bits(f2) and len(f1) > 3
    r1, r2 = svc.close(OWNER, s1), svc.close(OWNER, s2)
    assert r1.state_hash == r2.state_hash
    assert _bits(r1.account.to_dict()) == _bits(r2.account.to_dict())


def test_hash_moves_with_state():
    """The hash is not a constant: an order, a step and a cancel all move it."""
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, CONFIG).session_id
    seen = {svc.observe(OWNER, sid).state_hash}
    t = svc.info(OWNER, sid).tickers[0]
    o = svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=10,
                                                 type="limit", limit_price=1.0))
    seen.add(svc.observe(OWNER, sid).state_hash)
    svc.cancel_order(OWNER, sid, o.order_id)
    seen.add(svc.observe(OWNER, sid).state_hash)
    svc.advance(OWNER, sid)
    seen.add(svc.observe(OWNER, sid).state_hash)
    assert len(seen) == 4


@pytest.mark.parametrize("cut", [2, 7, 11, 14])
def test_kill_and_resume(tmp_path, cut):
    """(b) Run N calls, drop the service, open a new one on the same files:
    observe is bit-identical, and both paths stay identical in lockstep."""
    live = LocalSessionService(FileStore(tmp_path))
    sid = live.open(OWNER, CONFIG).session_id
    twin = live.open(OWNER, CONFIG).session_id     # never restarted
    script = _script(live.info(OWNER, sid).tickers)
    for call in script[:cut]:
        _run(live, sid, call)
        _run(live, twin, call)
    before = live.observe(OWNER, sid)
    del live                                        # the "crash"

    fresh = LocalSessionService(FileStore(tmp_path))
    after = fresh.observe(OWNER, sid)
    assert _bits(before.to_dict()) == _bits(after.to_dict())
    assert after.state_hash == before.state_hash

    other = LocalSessionService(FileStore(tmp_path))  # the uninterrupted path
    for call in script[cut:]:
        o_resumed = _run(fresh, sid, call)
        o_twin = _run(other, twin, call)
        assert _same(o_resumed, o_twin), call
    assert [f.to_dict() for f in fresh.fills(OWNER, sid)] == \
        [f.to_dict() for f in other.fills(OWNER, twin)]
    assert [o.to_dict() for o in fresh.orders(OWNER, sid)] == \
        [o.to_dict() for o in other.orders(OWNER, twin)]


def test_resume_from_disk_after_every_call(tmp_path):
    """A fresh service after EVERY call agrees with the one that made it."""
    live = LocalSessionService(FileStore(tmp_path))
    sid = live.open(OWNER, CONFIG).session_id
    for call in _script(live.info(OWNER, sid).tickers):
        obs = _run(live, sid, call)
        fresh = LocalSessionService(FileStore(tmp_path)).observe(OWNER, sid)
        assert _bits(obs.to_dict()) == _bits(fresh.to_dict()), call


def test_fork_diverges_only_when_calls_differ():
    """(c) Parent and fork given the same calls stay identical; one differing
    call makes them differ from then on."""
    svc = LocalSessionService(MemoryStore())
    parent = svc.open(OWNER, CONFIG).session_id
    script = _script(svc.info(OWNER, parent).tickers)
    for call in script[:6]:
        _run(svc, parent, call)
    child = svc.fork(OWNER, parent, label="arm").session_id
    info = svc.info(OWNER, child)
    assert info.parent_session_id == parent and info.config.label == "arm"
    assert _same(svc.observe(OWNER, parent), svc.observe(OWNER, child))
    for call in script[6:10]:
        assert _same(_run(svc, parent, call), _run(svc, child, call)), call
    # One differing call.
    t = info.tickers[3]
    svc.place_order(OWNER, child, OrderRequest(ticker=t, side="buy", quantity=50_000))
    svc.advance(OWNER, parent)
    svc.advance(OWNER, child)
    assert svc.observe(OWNER, parent).state_hash != svc.observe(OWNER, child).state_hash


def test_fork_mid_day_equals_resume(tmp_path):
    """A fork taken mid-day continues exactly as its parent does."""
    svc = LocalSessionService(FileStore(tmp_path))
    parent = svc.open(OWNER, SessionConfig(seed=9, universe_size=12)).session_id
    svc.advance(OWNER, parent, 4)
    child = svc.fork(OWNER, parent).session_id
    for _ in range(3):
        a = svc.advance(OWNER, parent, 5).observation
        b = svc.advance(OWNER, child, 5).observation
        assert _same(a, b)


def test_call_log_is_append_only_and_complete(tmp_path):
    svc = LocalSessionService(FileStore(tmp_path))
    sid = svc.open(OWNER, CONFIG).session_id
    script = _script(svc.info(OWNER, sid).tickers)
    for call in script[:5]:
        _run(svc, sid, call)
    log = svc.calls(OWNER, sid)
    assert [c["seq"] for c in log] == list(range(1, len(log) + 1))
    assert log[0]["op"] == "open" and log[-1]["op"] == "place_order"
    assert log[-1]["state_hash"] == svc.observe(OWNER, sid).state_hash
    lines = (tmp_path / sid / "calls.jsonl").read_text().splitlines()
    assert [json.loads(line)["seq"] for line in lines] == [c["seq"] for c in log]


def test_fork_carries_history_and_both_logs_say_so(tmp_path):
    svc = LocalSessionService(FileStore(tmp_path))
    parent = svc.open(OWNER, CONFIG).session_id
    for call in _script(svc.info(OWNER, parent).tickers)[:8]:
        _run(svc, parent, call)
    child = svc.fork(OWNER, parent, label="b").session_id
    fresh = LocalSessionService(FileStore(tmp_path))
    assert [f.to_dict() for f in fresh.fills(OWNER, child)] == \
        [f.to_dict() for f in fresh.fills(OWNER, parent)]
    assert [o.to_dict() for o in fresh.orders(OWNER, child)] == \
        [o.to_dict() for o in fresh.orders(OWNER, parent)]
    assert fresh.calls(OWNER, child)[0]["op"] == "fork_of"
    assert fresh.calls(OWNER, child)[0]["args"]["parent_session_id"] == parent
    last = fresh.calls(OWNER, parent)[-1]
    assert last["op"] == "fork" and last["child_session_id"] == child
    assert {i.session_id for i in fresh.list(OWNER)} == {parent, child}
