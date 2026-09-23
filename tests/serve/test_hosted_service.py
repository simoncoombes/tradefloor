"""HostedService over an in-memory SessionService: authorisation, every quota
and rate limit, idle expiry, and the completeness of the audit log."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tradefloor.serve.hosted import AuditLog, HostedService, Principal, Quotas
from tradefloor.serve.hosted.accounts import Accounts
from tradefloor.serve.hosted.audit import MUTATING_CALLS
from tradefloor.serve.hosted.plans import DEFAULT_PLANS
from tradefloor.serve.hosted.testing import FakeSessionService
from tradefloor.serve.types import OrderRequest, ServeError, SessionConfig, SessionService

T0 = 1_790_000_000.0  # 2026-09-21T13:33:20Z


class Clock:
    def __init__(self, t: float = T0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def tick(self, dt: float) -> None:
        self.t += dt


class SlowInner(FakeSessionService):
    """Charges `cost_per_tick` fake seconds of compute per simulated tick."""

    def __init__(self, clock: Clock, cost_per_tick: float = 0.0) -> None:
        super().__init__()
        self.clock, self.cost = clock, cost_per_tick

    def advance(self, owner, session_id, steps=1, until="steps"):
        before = self.sessions[session_id]["info"].clock if session_id in self.sessions else None
        start = (before.day * 390 + before.tick) if before else 0
        r = super().advance(owner, session_id, steps, until)
        self.clock.tick(self.cost * (r.clock.day * 390 + r.clock.tick - start))
        return r


def make(tmp_path, plan="trial", clock=None, cost_per_tick=0.0, plans=None, **plan_overrides):
    clock = clock or Clock()
    plans = dict(plans or DEFAULT_PLANS)
    if plan_overrides:
        plans[plan] = replace(plans[plan], **plan_overrides)
    acc = Accounts(tmp_path / "accounts.json", pepper=b"pep", plans=plans, clock=clock)
    quotas = Quotas(tmp_path / "usage.json", clock=clock, flush_interval=0)
    audit = AuditLog(tmp_path / "audit", clock=clock)
    inner = SlowInner(clock, cost_per_tick)
    hosted = HostedService(inner, acc, quotas, audit, clock=clock, perf=clock)
    _, key_a = acc.create_key("alice", plan=plan)
    _, key_b = acc.create_key("bob", plan=plan)
    return hosted, hosted.authenticate(key_a), hosted.authenticate(key_b), clock


def cfg(**kw):
    return SessionConfig(universe_size=kw.pop("universe_size", 5), **kw)


def code_of(fn, *a, **kw):
    with pytest.raises(ServeError) as e:
        fn(*a, **kw)
    return e.value


def test_is_a_session_service(tmp_path):
    hosted, *_ = make(tmp_path)
    assert isinstance(hosted, SessionService)


def test_authentication_and_authorisation(tmp_path):
    hosted, alice, bob, _ = make(tmp_path)
    assert isinstance(alice, Principal) and alice == "alice" and alice.key_id
    assert code_of(hosted.authenticate, "tfk_000000000000_" + "x" * 43).code == "unauthorized"
    # an owner the accounts file does not know is refused even if a transport passes it
    assert code_of(hosted.open, "mallory", cfg()).code == "unauthorized"
    hosted.accounts.set_suspended("alice")
    assert code_of(hosted.list, alice).code == "unauthorized"


def test_owner_isolation(tmp_path):
    hosted, alice, bob, _ = make(tmp_path)
    s = hosted.open(alice, cfg())
    for call, args in [("observe", ()), ("info", ()), ("advance", ()), ("close", ()),
                       ("fork", ()), ("orders", ()), ("fills", ()),
                       ("place_order", (OrderRequest("T00", "buy", 1),)),
                       ("cancel_order", ("x",))]:
        assert code_of(getattr(hosted, call), bob, s.session_id, *args).code == "not_found", call
    assert hosted.list(bob) == []
    assert [x.session_id for x in hosted.list(alice)] == [s.session_id]


def test_max_open_sessions_counts_forks_and_frees_on_close(tmp_path):
    hosted, alice, bob, _ = make(tmp_path, max_open_sessions=2)
    a = hosted.open(alice, cfg())
    hosted.fork(alice, a.session_id)
    e = code_of(hosted.open, alice, cfg())
    assert e.code == "quota_exceeded" and "2 open sessions" in e.message and "trial" in e.message
    assert code_of(hosted.fork, alice, a.session_id).code == "quota_exceeded"
    hosted.open(bob, cfg())  # bob's allowance is his own
    hosted.close(alice, a.session_id)
    hosted.open(alice, cfg())


def test_max_stored_sessions(tmp_path):
    hosted, alice, *_ = make(tmp_path, max_stored_sessions=2, max_open_sessions=5)
    for _ in range(2):
        hosted.close(alice, hosted.open(alice, cfg()).session_id)
    e = code_of(hosted.open, alice, cfg())
    assert e.code == "quota_exceeded" and "keeps at most 2 sessions" in e.message


def test_storage_cap(tmp_path):
    hosted, alice, *_ = make(tmp_path, storage_bytes=1000, max_open_sessions=5)
    hosted.storage_meter = lambda owner, ids: 600 * len(ids)
    hosted.open(alice, cfg())
    hosted.open(alice, cfg())
    e = code_of(hosted.open, alice, cfg())
    assert e.code == "quota_exceeded" and "MiB" in e.message


def test_per_request_caps_are_invalid_request_not_429(tmp_path):
    hosted, alice, *_ = make(tmp_path, max_universe_size=10, min_ticks_per_step=10)
    e = code_of(hosted.open, alice, cfg(universe_size=11))
    assert e.code == "invalid_request" and "10" in e.message and "trial" in e.message
    assert code_of(hosted.open, alice, cfg(ticks_per_step=5)).code == "invalid_request"
    assert code_of(hosted.open, alice, cfg(label="x" * 201)).code == "invalid_request"
    hosted.open(alice, cfg(universe_size=10, ticks_per_step=10))


def test_max_advance_length(tmp_path):
    hosted, alice, *_ = make(tmp_path, max_advance_ticks=390, steps_per_minute=1000)
    s = hosted.open(alice, cfg(ticks_per_step=30))
    e = code_of(hosted.advance, alice, s.session_id, steps=14)
    assert e.code == "invalid_request" and "390 ticks" in e.message
    hosted.advance(alice, s.session_id, steps=13)
    hosted.advance(alice, s.session_id, until="next_open")   # always allowed
    hosted.advance(alice, s.session_id, until="close")


def test_calls_per_minute_rate_limit(tmp_path):
    hosted, alice, bob, clock = make(tmp_path, calls_per_minute=5)
    for _ in range(5):
        hosted.list(alice)
    e = code_of(hosted.list, alice)
    assert e.code == "rate_limited" and "5 calls per minute" in e.message and "retry in" in e.message
    assert 0 < e.retry_after <= 12
    hosted.list(bob)                      # buckets are per owner
    clock.tick(e.retry_after)
    hosted.list(alice)


def test_steps_per_minute_rate_limit_and_refund(tmp_path):
    hosted, alice, _, clock = make(tmp_path, steps_per_minute=13, calls_per_minute=1000,
                                   max_advance_ticks=780)
    s = hosted.open(alice, cfg(ticks_per_step=30))
    hosted.advance(alice, s.session_id, steps=13)
    e = code_of(hosted.advance, alice, s.session_id, steps=1)
    assert e.code == "rate_limited" and "steps per minute" in e.message
    clock.tick(60)
    # until="next_open" from the close estimates 390+30 ticks but runs 1 step:
    # the difference is refunded, so 12 more steps fit in the same minute.
    hosted.advance(alice, s.session_id, until="next_open")
    hosted.advance(alice, s.session_id, steps=12)


def test_sim_days_per_day_quota_resets_at_utc_midnight(tmp_path):
    hosted, alice, _, clock = make(tmp_path, sim_days_per_day=2, steps_per_minute=10_000,
                                   calls_per_minute=10_000, max_advance_ticks=780)
    s = hosted.open(alice, cfg(ticks_per_step=30))
    hosted.advance(alice, s.session_id, steps=26)             # two days
    e = code_of(hosted.advance, alice, s.session_id, steps=1)
    assert e.code == "quota_exceeded"
    assert "2 simulated days per UTC day" in e.message and "resets at" in e.message
    assert e.retry_after > 0
    clock.tick(e.retry_after + 1)
    hosted.advance(alice, s.session_id, steps=1)
    assert hosted.usage_report(alice)["today"]["sim_days"] == pytest.approx(30 / 390, abs=1e-3)


def test_compute_seconds_per_day_quota(tmp_path):
    hosted, alice, *_ = make(tmp_path, cost_per_tick=0.01, compute_seconds_per_day=5,
                             steps_per_minute=10_000, calls_per_minute=10_000)
    s = hosted.open(alice, cfg(ticks_per_step=30))
    hosted.advance(alice, s.session_id, steps=13)      # 3.9 s
    hosted.advance(alice, s.session_id, steps=13)      # 7.8 s: allowed to start, overshoots once
    e = code_of(hosted.advance, alice, s.session_id, steps=1)
    assert e.code == "quota_exceeded" and "compute seconds" in e.message
    assert code_of(hosted.open, alice, cfg()).code == "quota_exceeded"
    hosted.observe(alice, s.session_id)                # reads still work
    assert hosted.usage_report(alice)["today"]["compute_seconds"] == pytest.approx(7.8)


def test_max_open_orders_and_idempotent_replay(tmp_path):
    hosted, alice, *_ = make(tmp_path, max_open_orders=2)
    s = hosted.open(alice, cfg())
    req = OrderRequest("T00", "buy", 1, type="limit", limit_price=90, client_order_id="c1")
    o1 = hosted.place_order(alice, s.session_id, req)
    hosted.place_order(alice, s.session_id, OrderRequest("T00", "buy", 1, type="limit", limit_price=91))
    e = code_of(hosted.place_order, alice, s.session_id, OrderRequest("T01", "sell", 1))
    assert e.code == "quota_exceeded" and "2 open orders" in e.message
    assert hosted.place_order(alice, s.session_id, req).order_id == o1.order_id  # replay is fine
    hosted.cancel_order(alice, s.session_id, o1.order_id)
    hosted.place_order(alice, s.session_id, OrderRequest("T01", "sell", 1))
    long_id = OrderRequest("T00", "buy", 1, client_order_id="x" * 65)
    assert code_of(hosted.place_order, alice, s.session_id, long_id).code == "invalid_request"


def test_idle_expiry(tmp_path):
    hosted, alice, bob, clock = make(tmp_path, idle_expiry_seconds=3600)
    idle = hosted.open(alice, cfg())
    busy = hosted.open(alice, cfg())
    other = hosted.open(bob, cfg())
    clock.tick(3000)
    hosted.observe(alice, busy.session_id)
    clock.tick(700)
    assert sorted(hosted.expire_idle()) == sorted([idle.session_id, other.session_id])
    assert hosted.expire_idle() == []
    assert hosted.info(alice, idle.session_id).status == "closed"
    assert hosted.info(alice, busy.session_id).status == "open"
    e = code_of(hosted.advance, alice, idle.session_id)
    assert e.code == "session_closed" and "idle longer than" in e.message
    lines = list(hosted.audit.read(call="expire"))
    assert {x["session_id"] for x in lines} == {idle.session_id, other.session_id}
    assert all(x["actor"] == "system" and x["outcome"] == "ok" for x in lines)


def test_idle_expiry_is_also_lazy(tmp_path):
    hosted, alice, _, clock = make(tmp_path, idle_expiry_seconds=60)
    s = hosted.open(alice, cfg())
    clock.tick(61)
    hosted.list(alice)          # any call of the owner sweeps their idle sessions
    assert hosted.inner.sessions[s.session_id]["info"].status == "closed"


def test_audit_log_is_complete_for_mutating_calls(tmp_path):
    hosted, alice, bob, clock = make(tmp_path, max_open_sessions=1)
    s = hosted.open(alice, cfg())
    o = hosted.place_order(alice, s.session_id, OrderRequest("T00", "buy", 1, type="limit", limit_price=90))
    hosted.cancel_order(alice, s.session_id, o.order_id)
    hosted.advance(alice, s.session_id, steps=2)
    code_of(hosted.open, alice, cfg())                               # refused: quota
    code_of(hosted.advance, bob, s.session_id)                       # refused: not_found
    code_of(hosted.place_order, alice, s.session_id, OrderRequest("ZZZ", "buy", 1))  # core refusal
    hosted.observe(alice, s.session_id)                              # a read: not audited
    code_of(hosted.fork, alice, s.session_id)                        # refused: quota
    hosted.close(alice, s.session_id)
    code_of(hosted.authenticate, "tfk_000000000000_" + "y" * 43)
    lines = list(hosted.audit.read())
    calls = [(x["call"], x["outcome"]) for x in lines]
    assert calls == [
        ("open", "ok"), ("place_order", "ok"), ("cancel_order", "ok"), ("advance", "ok"),
        ("open", "quota_exceeded"), ("advance", "not_found"), ("place_order", "invalid_order"),
        ("fork", "quota_exceeded"), ("close", "ok"), ("authenticate", "unauthorized"),
    ]
    for x in lines[:-1]:
        assert x["owner"] in ("alice", "bob") and x["key_id"] and "duration_ms" in x
        refused_open = x["call"] == "open" and x["outcome"] != "ok"
        assert x["session_id"] == (None if refused_open else s.session_id)
        assert x["call"] in MUTATING_CALLS
    assert lines[3]["detail"]["sim_ticks"] == 60
    assert lines[4]["error"] and "open sessions" in lines[4]["error"]
    assert lines[-1]["key_id"] == "000000000000"


def test_failed_auth_audit_is_throttled(tmp_path):
    hosted, _, _, clock = make(tmp_path)
    for _ in range(50):
        code_of(hosted.authenticate, "tfk_000000000000_" + "y" * 43)
    clock.tick(11)
    code_of(hosted.authenticate, "tfk_000000000000_" + "y" * 43)
    lines = list(hosted.audit.read(call="authenticate"))
    assert len(lines) == 2 and lines[1]["detail"]["folded"] == 49


def test_ledger_survives_restart_and_reconcile(tmp_path):
    hosted, alice, _, clock = make(tmp_path, steps_per_minute=1000)
    s = hosted.open(alice, cfg())
    hosted.advance(alice, s.session_id, steps=3)
    hosted.stop()
    again = Quotas(tmp_path / "usage.json", clock=clock)
    assert again.usage("alice").steps == 3
    assert again.activity(s.session_id).status == "open"
    # a session the ledger missed (crash inside the flush interval) is picked up
    lost = hosted.inner.open("alice", cfg())
    h2 = HostedService(hosted.inner, hosted.accounts, again, hosted.audit, clock=clock, perf=clock)
    assert h2.reconcile() == {"tracked": 1, "marked_closed": 0}
    assert again.activity(lost.session_id).status == "open"


def test_plan_change_takes_effect(tmp_path):
    hosted, alice, *_ = make(tmp_path)
    assert code_of(hosted.open, alice, cfg(universe_size=30)).code == "invalid_request"
    hosted.accounts.set_plan("alice", "standard")
    hosted.open(alice, cfg(universe_size=30))
