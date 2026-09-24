"""Sessions in LocalSessionService: contract sections 2 and 4, and the caveats.

Owner isolation, agent-paced time in its three units, the per-call cap,
observation contents, close and its computed caveats, and the refusals that
belong to sessions rather than orders.
"""
from __future__ import annotations

import math
import threading

import pytest

import tradefloor as tf
from tradefloor import envelope
from tradefloor.serve import core
from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import FileStore, MemoryStore
from tradefloor.serve.types import (Clock, Headline, OrderRequest, ServeError,
                                    SessionConfig, SessionService)

OWNER = "local"
SMALL = SessionConfig(seed=3, universe_size=4, ticks_per_step=30)


def code_of(fn, *a, **kw) -> str:
    with pytest.raises(ServeError) as e:
        fn(*a, **kw)
    return e.value.code


def test_is_a_session_service():
    assert isinstance(LocalSessionService(MemoryStore()), SessionService)


# -- open -----------------------------------------------------------------------------

def test_open_builds_the_named_session():
    svc = LocalSessionService(MemoryStore())
    info = svc.open(OWNER, SessionConfig(seed=4, universe_size=6, label="t"))
    assert len(info.session_id) == 32 and int(info.session_id, 16) >= 0
    assert info.owner == OWNER and info.status == "open"
    bare = tf.Engine(seed=4, universe=tf.Universe.random(6, seed=111))
    assert info.tickers == bare.tickers and len(info.tickers) == 6
    assert info.model_fingerprint == "pt-v19"
    assert info.clock == Clock(day=0, tick=0, step=0, market_open=True)
    assert info.config.label == "t" and info.parent_session_id is None
    assert info.created_at.endswith("+00:00")


@pytest.mark.parametrize("change", [
    dict(preset="pt-v17"), dict(preset="custom"), dict(preset=None),
    dict(universe_size=0), dict(universe_size=41), dict(universe_size="20"),
    dict(universe_size=True), dict(seed=-1), dict(seed=1.5), dict(universe_seed=-3),
    dict(cash=0.0), dict(cash=-1.0), dict(cash=float("nan")), dict(cash=float("inf")),
    dict(max_leverage=0.0), dict(max_leverage=float("nan")), dict(max_leverage="2"),
    dict(ticks_per_step=0), dict(ticks_per_step=391), dict(ticks_per_step=30.0),
    dict(label=5),
])
def test_open_refuses_bad_config(change):
    svc = LocalSessionService(MemoryStore())
    cfg = SessionConfig(**{**SessionConfig().to_dict(), **change})
    assert code_of(svc.open, OWNER, cfg) == "invalid_request"
    assert svc.list(OWNER) == []


def test_open_accepts_every_named_preset_and_a_dict():
    svc = LocalSessionService(MemoryStore())
    info = svc.open(OWNER, {"preset": "pt-v18", "universe_size": 3})
    assert info.model_fingerprint == "pt-v18"
    assert code_of(svc.open, OWNER, {"preset": "pt-v19", "colour": "red"}) \
        == "invalid_request"


def test_max_universe_is_configurable():
    svc = LocalSessionService(MemoryStore(), max_universe=10)
    assert code_of(svc.open, OWNER, SessionConfig(universe_size=11)) == "invalid_request"
    svc.open(OWNER, SessionConfig(universe_size=10))


# -- owners ------------------------------------------------------------------------------

def test_other_owners_sessions_do_not_exist():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open("alice", SMALL).session_id
    ghost = "0" * 32
    t = svc.info("alice", sid).tickers[0]
    order = svc.place_order("alice", sid, OrderRequest(ticker=t, side="buy",
                                                       quantity=1, type="limit",
                                                       limit_price=1.0))
    calls = [
        lambda o, s: svc.info(o, s), lambda o, s: svc.observe(o, s),
        lambda o, s: svc.place_order(o, s, OrderRequest(ticker=t, side="buy",
                                                        quantity=1)),
        lambda o, s: svc.cancel_order(o, s, order.order_id),
        lambda o, s: svc.orders(o, s), lambda o, s: svc.fills(o, s),
        lambda o, s: svc.advance(o, s), lambda o, s: svc.fork(o, s),
        lambda o, s: svc.close(o, s), lambda o, s: svc.calls(o, s),
    ]
    for call in calls:
        with pytest.raises(ServeError) as foreign:
            call("bob", sid)
        with pytest.raises(ServeError) as missing:
            call("bob", ghost)
        assert foreign.value.code == missing.value.code == "not_found"
        # Nothing in the message says the session exists.
        assert foreign.value.message.replace(sid, "X") == \
            missing.value.message.replace(ghost, "X")
    assert svc.list("bob") == []
    assert [i.session_id for i in svc.list("alice")] == [sid]
    # And alice's session is untouched by bob's attempts.
    assert svc.info("alice", sid).clock.tick == 0
    assert len(svc.orders("alice", sid)) == 1


@pytest.mark.parametrize("sid", ["", "../../etc", "x" * 32, "A" * 32, None, 7])
def test_malformed_session_ids_are_not_found(sid, tmp_path):
    svc = LocalSessionService(FileStore(tmp_path))
    assert code_of(svc.observe, OWNER, sid) == "not_found"


@pytest.mark.parametrize("owner", ["", None, 3])
def test_owner_must_be_a_string(owner):
    svc = LocalSessionService(MemoryStore())
    assert code_of(svc.open, owner, SMALL) == "invalid_request"
    assert code_of(svc.list, owner) == "invalid_request"


def test_list_is_per_owner_and_ordered(tmp_path):
    svc = LocalSessionService(FileStore(tmp_path))
    a = [svc.open("alice", SMALL).session_id for _ in range(3)]
    svc.open("bob", SMALL)
    listed = svc.list("alice")
    assert [i.session_id for i in listed] == sorted(
        a, key=lambda s: (svc.info("alice", s).created_at, s))
    svc.advance("alice", a[0], 2)
    svc.close("alice", a[1])
    fresh = {i.session_id: i for i in LocalSessionService(FileStore(tmp_path)).list("alice")}
    assert fresh[a[0]].clock.tick == 60 and fresh[a[1]].status == "closed"


# -- time ---------------------------------------------------------------------------------

def clock(svc, sid):
    c = svc.info(OWNER, sid).clock
    return (c.day, c.tick, c.step, c.market_open)


def test_advance_steps_crosses_the_close_and_the_open():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SMALL).session_id
    assert svc.advance(OWNER, sid).clock.tick == 30
    svc.advance(OWNER, sid, 11)
    assert clock(svc, sid) == (0, 360, 12, True)
    svc.advance(OWNER, sid)
    assert clock(svc, sid) == (0, 390, 13, False)
    svc.advance(OWNER, sid)                     # opens day 1, runs its first step
    assert clock(svc, sid) == (1, 30, 1, True)
    svc.advance(OWNER, sid, 25)                 # 12 to finish day 1, 13 of day 2
    assert clock(svc, sid) == (2, 390, 13, False)


def test_a_short_last_step_ends_at_the_close():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=4,
                                        ticks_per_step=60)).session_id
    svc.advance(OWNER, sid, 6)
    assert clock(svc, sid) == (0, 360, 6, True)
    svc.advance(OWNER, sid)                     # 30 ticks, not 60
    assert clock(svc, sid) == (0, 390, 7, False)


def test_advance_until_close_and_next_open():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SMALL).session_id
    svc.advance(OWNER, sid, 3)
    svc.advance(OWNER, sid, until="close")
    assert clock(svc, sid) == (0, 390, 13, False)
    svc.advance(OWNER, sid, until="close")      # from a close: the next session's
    assert clock(svc, sid) == (1, 390, 13, False)
    svc.advance(OWNER, sid, until="next_open")  # from a close: just the open
    assert clock(svc, sid) == (2, 0, 0, True)
    svc.advance(OWNER, sid, until="next_open")  # from an open: a whole session
    assert clock(svc, sid) == (3, 0, 0, True)
    svc.advance(OWNER, sid, 2)
    svc.advance(OWNER, sid, 3, until="close")   # three closes
    assert clock(svc, sid) == (5, 390, 13, False)
    svc.advance(OWNER, sid, 2, until="next_open")
    assert clock(svc, sid) == (7, 0, 0, True)


def test_stepping_and_whole_sessions_reach_the_same_market():
    svc = LocalSessionService(MemoryStore())
    a = svc.open(OWNER, SMALL).session_id
    b = svc.open(OWNER, SMALL).session_id
    svc.advance(OWNER, a, 13 * 3)
    svc.advance(OWNER, b, 3, until="close")
    oa, ob = svc.observe(OWNER, a), svc.observe(OWNER, b)
    assert oa.state_hash == ob.state_hash and oa.quotes == ob.quotes


def test_the_twenty_session_cap():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=2,
                                        ticks_per_step=390)).session_id
    before = svc.observe(OWNER, sid).state_hash
    assert code_of(svc.advance, OWNER, sid, 21, until="close") == "invalid_request"
    assert code_of(svc.advance, OWNER, sid, 21) == "invalid_request"
    assert code_of(svc.advance, OWNER, sid, 10**12) == "invalid_request"
    assert code_of(svc.advance, OWNER, sid, 22, until="next_open") == "invalid_request"
    assert svc.observe(OWNER, sid).state_hash == before     # refusals change nothing
    svc.advance(OWNER, sid, 20, until="close")              # exactly 7,800 ticks
    assert clock(svc, sid) == (19, 390, 1, False)
    svc.advance(OWNER, sid, 20, until="next_open")          # opens, then 19 sessions
    assert clock(svc, sid) == (39, 0, 0, True)
    assert code_of(svc.advance, OWNER, sid, 21, until="next_open") == "invalid_request"


def test_the_cap_counts_ticks_for_steps():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=2,
                                        ticks_per_step=30)).session_id
    svc.advance(OWNER, sid, 5)
    # 8 steps finish day 0, then 13 per session: 20 sessions' worth of ticks
    # from here is 7,800, which 8 + 13*19 + 5 steps reach exactly.
    assert code_of(svc.advance, OWNER, sid, 8 + 13 * 19 + 6) == "invalid_request"
    svc.advance(OWNER, sid, 8 + 13 * 19 + 5)
    assert clock(svc, sid) == (20, 150, 5, True)


def test_max_advance_sessions_is_configurable():
    svc = LocalSessionService(MemoryStore(), max_advance_sessions=2)
    sid = svc.open(OWNER, SMALL).session_id
    assert code_of(svc.advance, OWNER, sid, 3, until="close") == "invalid_request"
    svc.advance(OWNER, sid, 2, until="close")


@pytest.mark.parametrize("steps,until", [(0, "steps"), (-1, "steps"), (1.5, "steps"),
                                         (True, "steps"), ("2", "steps"),
                                         (1, "forever"), (1, None)])
def test_advance_refuses_bad_arguments(steps, until):
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SMALL).session_id
    assert code_of(svc.advance, OWNER, sid, steps, until=until) == "invalid_request"


# -- observe --------------------------------------------------------------------------------

def test_observation_contents():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=5)).session_id
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=100))
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=1,
                                             type="limit", limit_price=1.0,
                                             time_in_force="gtc"))
    svc.advance(OWNER, sid, 3)
    obs = svc.observe(OWNER, sid)
    assert obs.session_id == sid and obs.clock.tick == 90
    assert [q.ticker for q in obs.quotes] == svc.info(OWNER, sid).tickers
    for q in obs.quotes:
        assert q.day_low <= q.last <= q.day_high
        assert q.day_low <= q.day_open <= q.day_high
        assert q.volume > 0 and q.prev_close > 0
        assert q.bid is not None and q.ask is not None and q.bid < q.ask
    assert 5 < obs.vix < 100
    assert list(obs.macro) == list(core.MACRO_FIELDS)
    assert obs.macro["cycle_phase"] in range(len(core.CYCLE_PHASES))
    assert all(isinstance(v, float) and math.isfinite(v) for v in obs.macro.values())
    (pos,) = obs.positions
    assert pos.ticker == t and pos.quantity == 100
    q = obs.quotes[0]
    assert pos.market_value == 100 * q.last
    assert pos.unrealised_pnl == (q.last - pos.avg_price) * 100
    a = obs.account
    assert a.starting_cash == 1_000_000 and a.cash == 1_000_000 - 100 * pos.avg_price
    assert math.isclose(a.net_worth, a.cash + pos.market_value)
    assert math.isclose(a.leverage, a.gross_exposure / a.net_worth)
    assert a.realised_pnl == 0.0 and a.unrealised_pnl == pos.unrealised_pnl
    assert [o.limit_price for o in obs.open_orders] == [1.0]
    assert obs.news == [] and len(obs.state_hash) == 64


def test_quotes_reset_at_the_open():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SMALL).session_id
    closed = svc.advance(OWNER, sid, until="close").observation
    opened = svc.advance(OWNER, sid, until="next_open").observation
    for c, o in zip(closed.quotes, opened.quotes):
        assert o.prev_close == c.last
        assert o.volume == 0.0 and o.day_high == o.day_low == o.day_open == o.last


def test_headlines_hook_fills_news():
    seen = []

    def headlines(engine, clock):
        seen.append(clock.tick)
        return [Headline(day=clock.day, tick=clock.tick, tickers=[engine.tickers[0]],
                         text="Something happened", category="company")]

    svc = LocalSessionService(MemoryStore(), headlines=headlines)
    sid = svc.open(OWNER, SMALL).session_id
    obs = svc.advance(OWNER, sid, 2).observation
    assert obs.news[0].tick == 60 and seen[-1] == 60


# -- close and the report ---------------------------------------------------------------------

def test_close_reports_and_freezes():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SMALL).session_id
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=10))
    svc.advance(OWNER, sid, until="next_open")
    obs = svc.observe(OWNER, sid)
    report = svc.close(OWNER, sid)
    assert report.session_id == sid and report.fills == 1 and report.days == 2
    assert report.account == obs.account
    assert report.state_hash != obs.state_hash          # status is state
    assert report.state_hash == svc.observe(OWNER, sid).state_hash
    assert svc.info(OWNER, sid).status == "closed"
    for call in (lambda: svc.advance(OWNER, sid),
                 lambda: svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy",
                                                                  quantity=1)),
                 lambda: svc.cancel_order(OWNER, sid, "ord-000001"),
                 lambda: svc.fork(OWNER, sid), lambda: svc.close(OWNER, sid)):
        assert code_of(call) == "session_closed"
    # Reading still works.
    assert len(svc.fills(OWNER, sid)) == 1 and len(svc.orders(OWNER, sid)) == 1


def test_caveats_are_computed():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=4,
                                        max_leverage=None)).session_id
    cav = svc.caveats(OWNER, sid)
    text = " | ".join(cav)
    # The long-run caveat, while it is true of the preset: pt-v19's record
    # passes all fifteen adopted criteria since its fifth composition
    # (2026-09-23; the fourth failed eight and this test asserted the
    # failing wording until then).
    blk = core.long_run_verdict("pt-v19")
    assert "passes the long-run check" in text
    assert f"({blk['passed']} of {blk['of']} criteria" in text
    assert "FAILS the long-run check" not in text
    assert "SHORT WINDOW: 1 trading day" in text
    assert "Leverage is unbounded" in text
    assert "A 4-name roster" in text
    assert "0 of 0 fills" in text
    # Made false, the caveat turns: it is computed from the preset's record,
    # not typed, and a failing verdict names the rows it fails.
    real = core.long_run_verdict
    try:
        core.long_run_verdict = lambda preset: {
            "verdict": "fail", "passed": 14, "of": 15, "criteria": "test",
            "rows": [{"id": "A1", "words": "worst month's volatility",
                      "value": [46, 38], "real": [84, 95], "rule": "within 30%",
                      "pass": False},
                     {"id": "B7", "words": "index volatility", "value": 17.7,
                      "real": 18.1, "rule": "within 20%", "pass": True}]}
        text2 = " | ".join(svc.caveats(OWNER, sid))
        assert "passes the long-run check" not in text2
        assert "FAILS the long-run check on 1 of 15 criteria" in text2
        assert "volatility regimes and crash frequency drift" in text2
        assert "A1 worst month's volatility: 46 / 38 against 84 / 95 real" in text2
        assert "B7" not in text2
    finally:
        core.long_run_verdict = real
    assert core.long_run_failures("pt-v19") == []   # and it passes today


def test_caveats_for_an_unchecked_preset():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(preset="pt-v18", universe_size=30)).session_id
    text = " | ".join(svc.close(OWNER, sid).caveats)
    assert "has not been through the long-run check" in text
    assert f"not the certified default ({envelope.PRESET})" in text
    assert "-name roster" not in text


def test_caveats_count_resting_fills_and_track_the_horizon():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SessionConfig(seed=3, universe_size=2,
                                        ticks_per_step=390)).session_id
    t = svc.info(OWNER, sid).tickers[0]
    last = svc.observe(OWNER, sid).quotes[0].last
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=1))
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="sell", quantity=1,
                                             type="limit",
                                             limit_price=round(last * 0.97, 2)))
    svc.advance(OWNER, sid)
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=1,
                                             type="limit", limit_price=last * 5))
    svc.advance(OWNER, sid)
    fills = svc.fills(OWNER, sid)
    resting = sum(f.liquidity == "resting" for f in fills)
    text = " | ".join(svc.caveats(OWNER, sid))
    assert f"{resting} of {len(fills)} fills" in text
    horizon = envelope.CERTIFIED_HORIZON_DAYS
    for _ in range(0, horizon + 1, 20):
        svc.advance(OWNER, sid, 20, until="close")
    days = svc.info(OWNER, sid).clock.day + 1
    assert days > horizon
    text = " | ".join(svc.caveats(OWNER, sid))
    assert "Outside the certified realism envelope" in text
    assert "SHORT WINDOW" not in text
    # The session's own day count, as the envelope check reports it.
    assert f"horizon {days}d exceeds the certified {horizon}d" in text
    # And the long-run caveat's own sentence carrying it, which only a
    # FAILING preset's caveat has: pt-v19 passes the long-run check since its
    # fifth composition, so the failing wording is read off a failing block.
    real = core.long_run_verdict
    try:
        core.long_run_verdict = lambda preset: {
            "verdict": "fail", "passed": 14, "of": 15, "criteria": "test",
            "rows": [{"id": "B1", "words": "time with VIX above 30",
                      "value": 0.194, "real": 0.082, "rule": "1/2x to 2x",
                      "pass": False}]}
        text = " | ".join(svc.caveats(OWNER, sid))
    finally:
        core.long_run_verdict = real
    assert f"This session has run {days} trading days" in text
    assert f"the certification below covers at most {horizon}" in text


# -- safety ----------------------------------------------------------------------------------------

def test_a_failed_commit_applies_nothing(tmp_path):
    store = FileStore(tmp_path)
    svc = LocalSessionService(store)
    sid = svc.open(OWNER, SMALL).session_id
    t = svc.info(OWNER, sid).tickers[0]
    svc.place_order(OWNER, sid, OrderRequest(ticker=t, side="buy", quantity=10))
    before = svc.observe(OWNER, sid)
    real = store.commit

    def broken(*a, **kw):
        raise OSError("disk full")

    store.commit = broken
    assert code_of(svc.advance, OWNER, sid, 3) == "internal"
    store.commit = real
    after = svc.observe(OWNER, sid)
    assert after == before and svc.fills(OWNER, sid) == []
    svc.advance(OWNER, sid, 3)                  # and it carries on normally
    assert len(svc.fills(OWNER, sid)) == 1


def test_a_second_instance_sees_the_first_ones_writes(tmp_path):
    a = LocalSessionService(FileStore(tmp_path))
    b = LocalSessionService(FileStore(tmp_path))
    sid = a.open(OWNER, SMALL).session_id
    b.observe(OWNER, sid)                       # b caches it
    a.advance(OWNER, sid, 4)                    # a moves it on
    assert b.observe(OWNER, sid) == a.observe(OWNER, sid)
    b.advance(OWNER, sid)
    assert a.info(OWNER, sid).clock.tick == 150


def test_the_cache_evicts_and_reloads(tmp_path):
    svc = LocalSessionService(FileStore(tmp_path), cache_size=2)
    ids = [svc.open(OWNER, SMALL).session_id for _ in range(4)]
    for sid in ids:
        svc.advance(OWNER, sid, 2)
    assert len(svc._cache) == 2
    for sid in ids:
        assert svc.info(OWNER, sid).clock.tick == 60


def test_threads_on_one_session_serialise():
    svc = LocalSessionService(MemoryStore())
    sid = svc.open(OWNER, SMALL).session_id
    errors = []

    def worker():
        try:
            for _ in range(5):
                svc.advance(OWNER, sid)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert errors == []
    assert clock(svc, sid) == (1, 7 * 30, 7, True)      # 20 steps = 13 + 7
    assert [c["seq"] for c in svc.calls(OWNER, sid)] == list(range(1, 22))
