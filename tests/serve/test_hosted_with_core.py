"""The hosted layer over the real core: LocalSessionService on FileStore, the
engine underneath. The metering must match the core's own clock, and the
hosted wrapper must not disturb resume-after-restart."""

from __future__ import annotations

import pytest

core = pytest.importorskip("tradefloor.serve.core")

from tradefloor.serve.hosted import AuditLog, HostedService, Quotas  # noqa: E402
from tradefloor.serve.hosted.accounts import Accounts  # noqa: E402
from tradefloor.serve.hosted.config import Settings, build_store  # noqa: E402
from tradefloor.serve.types import OrderRequest, ServeError, SessionConfig  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.t = 1_790_000_000.0

    def __call__(self) -> float:
        return self.t


def build(tmp_path, clock, plan="standard"):
    s = Settings(data=tmp_path, pepper=b"pepper-for-tests-0123")
    store, meter = build_store(s)
    acc = Accounts(tmp_path / "accounts.json", pepper=s.pepper, clock=clock)
    hosted = HostedService(core.LocalSessionService(store), acc,
                           Quotas(tmp_path / "usage.json", clock=clock, flush_interval=0),
                           AuditLog(tmp_path / "audit", clock=clock), storage_meter=meter,
                           clock=clock)
    return hosted


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    hosted = build(tmp_path, clock)
    _, key = hosted.accounts.create_key("acme", plan="standard")
    return hosted, hosted.authenticate(key), clock


def test_metering_follows_the_core_clock(world):
    hosted, p, _ = world
    s = hosted.open(p, SessionConfig(universe_size=5, ticks_per_step=100))
    runs = [dict(steps=1), dict(until="close"), dict(steps=1), dict(until="next_open"),
            dict(until="next_open"), dict(until="close"), dict(steps=5)]
    ticks = []
    before = hosted.info(p, s.session_id).clock
    for kw in runs:
        r = hosted.advance(p, s.session_id, **kw)
        ticks.append((r.clock.day - before.day) * 390 + r.clock.tick - before.tick)
        before = r.clock
    audited = [e["detail"].get("sim_ticks", 0) for e in hosted.audit.read(call="advance")]
    assert audited == ticks
    assert ticks == [100, 290, 100, 290, 390, 390, 490]
    assert hosted.usage_report(p)["today"]["sim_days"] == pytest.approx(sum(ticks) / 390, abs=1e-3)


def test_storage_is_metered_from_the_filestore(world, tmp_path):
    hosted, p, _ = world
    s = hosted.open(p, SessionConfig(universe_size=5))
    hosted.advance(p, s.session_id, steps=3)
    used = hosted.usage_report(p)["storage_bytes"]
    on_disk = sum(f.stat().st_size for f in (tmp_path / "sessions" / s.session_id).iterdir())
    assert used == on_disk > 0


def test_idle_expiry_closes_a_real_session_and_its_report_stays(world):
    hosted, p, clock = world
    s = hosted.open(p, SessionConfig(universe_size=5))
    hosted.place_order(p, s.session_id, OrderRequest(s.tickers[0], "buy", 10))
    hosted.advance(p, s.session_id, steps=2)
    clock.t += 8 * 86400                 # standard plan: 7 days idle
    assert hosted.expire_idle() == [s.session_id]
    assert hosted.info(p, s.session_id).status == "closed"
    with pytest.raises(ServeError) as e:
        hosted.advance(p, s.session_id)
    assert e.value.code == "session_closed" and "idle longer than" in e.value.message
    assert hosted.fills(p, s.session_id)     # the history is still readable


def test_resume_after_restart_is_untouched_by_the_hosted_layer(world, tmp_path):
    hosted, p, clock = world
    s = hosted.open(p, SessionConfig(universe_size=5, ticks_per_step=65))
    hosted.place_order(p, s.session_id, OrderRequest(s.tickers[1], "sell", 50))
    hosted.advance(p, s.session_id, steps=4)
    before = hosted.observe(p, s.session_id)
    hosted.stop()
    again = build(tmp_path, clock)           # a new process on the same volume
    assert again.reconcile() == {"tracked": 0, "marked_closed": 0}
    after = again.observe(p, s.session_id)
    assert after.to_dict() == before.to_dict()
    assert again.quotas.usage("acme").steps == 4


def test_owner_isolation_through_the_real_core(world):
    hosted, p, _ = world
    _, other_key = hosted.accounts.create_key("other", plan="standard")
    q = hosted.authenticate(other_key)
    s = hosted.open(p, SessionConfig(universe_size=3))
    for call in (hosted.observe, hosted.advance, hosted.close, hosted.fork, hosted.info):
        with pytest.raises(ServeError) as e:
            call(q, s.session_id)
        assert e.value.code == "not_found"
    assert hosted.list(q) == []
