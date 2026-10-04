"""The MCP server's market sessions: one market kept between calls.

A session is the one surface on this server where state lives between
calls, so what matters here is that the state is reproducible (the same
calls from the same open give the same bytes, a fork's twin continues as
the original does, a rewind puts back exactly what was there), that the
loop is the library's own, and that the caller sees no more of the market
than a sandboxed agent does.
"""

import json

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")

import tradefloor as tf  # noqa: E402
from tradefloor import mcp, sandbox  # noqa: E402

MOMENTUM = {"signal": {"kind": "momentum", "lookback_days": 1.0},
            "portfolio": {"top_k": 3}}


@pytest.fixture(autouse=True)
def no_sessions_left_open():
    """Each test starts with no sessions and closes what it opened, so the
    cap cannot carry from one test into the next."""
    for sid in list(mcp._sessions):
        mcp.close_session(sid)
    yield
    for sid in list(mcp._sessions):
        mcp.close_session(sid)


def opened(**kw):
    r = mcp.open_session(**kw)
    assert r["ok"], r.get("error")
    return r["session_id"]


def comparable(result):
    """A result with what names the session taken out: its id, the id it
    was forked from, its lineage and the sentence that reports it."""
    out = dict(result)
    out.pop("session_id", None)
    out.pop("forked_from", None)
    out["provenance"] = {k: v for k, v in out["provenance"].items()
                         if k != "lineage"}
    out["caveats"] = [c for c in out["caveats"]
                      if not c.startswith("SESSION")]
    return json.dumps(out, sort_keys=True)


def script(sid):
    """A run of calls with every kind of order in it, across a close."""
    return [
        mcp.session_step(sid, steps=2, agent="me", orders={"AAA": 300}),
        mcp.session_step(sid, steps=3, orders={
            "AAB": {"quantity": 200, "limit_price": 10.0}}),
        mcp.session_step(sid, days=2, orders={"AAA": -100, "AAB": "cancel"}),
        mcp.session_step(sid, steps=4),
    ]


# -- determinism -----------------------------------------------------------


def test_the_same_calls_from_the_same_open_give_the_same_bytes():
    a = script(opened(agents={"me": None, "m": MOMENTUM}, universe_size=12))
    # The agents in the other order: execution is by label, so the
    # request's key order is not part of the market.
    b = script(opened(agents={"m": MOMENTUM, "me": None}, universe_size=12))
    assert all(r["ok"] for r in a), [r.get("error") for r in a]
    assert [comparable(r) for r in a] == [comparable(r) for r in b]


def test_a_fork_and_its_original_continue_identically():
    sid = opened(universe_size=12)
    mcp.session_step(sid, steps=4, orders={"AAC": 500})
    fork = mcp.session_fork(sid)
    assert fork["ok"], fork
    assert fork["forked_from"] == sid and fork["session_id"] != sid
    twin = fork["session_id"]
    for call in ({"days": 3, "orders": {"AAD": 100}},
                 {"steps": 2, "orders": {"AAC": {"quantity": -100,
                                                 "limit_price": 1.0}}},
                 {"steps": 5}):
        assert comparable(mcp.session_step(sid, **call)) == comparable(
            mcp.session_step(twin, **call))


def test_a_fork_is_independent_of_its_original():
    sid = opened(universe_size=12)
    mcp.session_step(sid, steps=3)
    twin = mcp.session_fork(sid)["session_id"]
    before = comparable(mcp.session_state(sid))
    moved = mcp.session_step(twin, days=2, orders={"AAA": 1000})
    assert moved["ok"], moved
    assert comparable(mcp.session_state(sid)) == before


def test_a_fork_taken_mid_day_continues_bit_for_bit():
    """A mid-day fork is where the engine's own copy once lost the day's
    accumulators. Through a snapshot and a restore it must not."""
    sid = opened(universe_size=12, steps_per_day=6)
    mcp.session_step(sid, steps=8, orders={"AAA": 200})
    assert mcp.session_state(sid)["clock"]["market_open"] is True
    twin = mcp.session_fork(sid)["session_id"]
    mcp.session_step(sid, days=2)
    mcp.session_step(twin, days=2)
    assert (mcp._sessions[sid].engine.state_hash()
            == mcp._sessions[twin].engine.state_hash())


def test_a_rewind_puts_back_exactly_what_was_there():
    sid = opened(agents={"me": None, "m": MOMENTUM}, universe_size=12)
    mcp.session_step(sid, steps=4, orders={"AAA": 300})
    kept = mcp.session_step(sid, steps=3, orders={
        "AAB": {"quantity": 100, "limit_price": 5.0}})
    mark = kept["checkpoint"]
    state = comparable(mcp.session_state(sid))
    engine_hash = mcp._sessions[sid].engine.state_hash()
    later = mcp.session_step(sid, days=2, orders={"AAA": -300})
    assert later["checkpoint"] in mcp.session_state(sid)["checkpoints"]

    back = mcp.session_rewind(sid, mark)
    assert back["ok"], back
    assert mcp._sessions[sid].engine.state_hash() == engine_hash
    # The state the caller sees is the one it saw, apart from the lineage
    # and the orders sent on the line that was dropped.
    now = json.loads(comparable(mcp.session_state(sid)))
    was = json.loads(state)
    assert now == was
    assert back["checkpoints"] == [c for c in later["checkpoints"]
                                   if c <= mark]
    # And the market from there is the market that was there: the same
    # calls give the same results as they did before the rewind.
    again = mcp.session_step(sid, days=2, orders={"AAA": -300})
    assert comparable(again) == comparable(later)


def test_a_rewind_is_recorded_and_refuses_a_step_it_did_not_keep():
    sid = opened(universe_size=8)
    mcp.session_step(sid, steps=2)
    mcp.session_step(sid, steps=2)
    r = mcp.session_rewind(sid, 1)
    assert r["ok"] is False and "[0, 2, 4]" in r["error"]
    r = mcp.session_rewind(sid, 2)
    assert r["ok"], r
    assert r["provenance"]["lineage"] == [{"rewound_from": 4, "to_step": 2}]
    assert any("rewound from step 4 to step 2" in c for c in r["caveats"])


# -- the loop is the library's ---------------------------------------------


@pytest.mark.parametrize("preset", [None, "pt-v19"])
def test_a_strategy_agent_scores_what_evaluate_scores(preset):
    """One strategy agent stepped through the days ends on the net worth
    `tf.evaluate` scores for the same spec on the same market, which pins
    the session loop to the harness's."""
    sid = opened(agents={"m": MOMENTUM}, universe_size=12, seed=5,
                 preset=preset)
    stepped = mcp.session_step(sid, days=3)
    mcp.session_step(sid, steps=7)
    final = mcp.session_step(sid, days=1)
    spec = tf.StrategySpec.from_json(mcp._normalise(MOMENTUM)[0])
    cards = tf.evaluate({"m": spec}, seed=5,
                        universe=tf.Universe.random(12, seed=111), days=5,
                        trusted_agents=False, model=preset)
    assert stepped["ok"] and final["clock"]["days_closed"] == 5
    assert final["agents"]["m"]["net_worth"] == round(
        cards["m"].final_net_worth, 2)
    assert final["agents"]["m"]["trades"] == cards["m"].trades


def test_a_hand_agent_that_sends_nothing_leaves_the_untraded_market():
    sid = opened(universe_size=10, seed=9)
    r = mcp.session_step(sid, days=2)
    engine = tf.Engine(seed=9, universe=tf.Universe.random(10, seed=111))
    for day in range(2):
        engine.open_market()
        for step in range(mcp.DEFAULT_STEPS_PER_DAY):
            engine.run_session(*tf.harness.session_clock(
                (9, 30, 3), step, 65), 65)
        engine.close_market()
    prices = dict(zip(engine.tickers, tf.harness._f64(engine.prices())))
    assert r["market"]["prices"] == {t: round(p, 4) for t, p in prices.items()}


def test_orders_trade_and_a_limit_order_waits_until_cancelled():
    sid = opened(universe_size=8)
    price = mcp.session_state(sid)["market"]["prices"]["AAB"]
    r = mcp.session_step(sid, orders={
        "AAA": 100, "AAB": {"quantity": 50, "limit_price": round(price / 2, 2)}})
    me = r["agents"]["me"]
    assert me["orders_sent"]["AAA"] == 100.0
    assert me["positions"]["AAA"]["quantity"] == 100.0
    assert [o["ticker"] for o in me["open_orders"]] == ["AAB"]
    r = mcp.session_step(sid, orders={"AAB": "cancel"})
    assert r["agents"]["me"]["open_orders"] == []


def test_an_order_the_market_refuses_is_listed_and_the_rest_trade():
    sid = opened(universe_size=8, cash=10_000.0)
    r = mcp.session_step(sid, orders={"AAA": 10_000_000, "AAB": 10})
    me = r["agents"]["me"]
    assert len(me["refused"]) == 1 and "AAA" in me["refused"][0]
    assert me["positions"]["AAB"]["quantity"] == 10.0


@pytest.mark.parametrize("orders,named", [
    ({"ZZZ": 100}, "No ticker 'ZZZ'"),
    ({"aaa": 100}, "Did you mean 'AAA'"),
    ({"AAA": "100"}, "AAA"),
    ({"AAA": {"quantity": 5}}, "limit_price"),
    ({"AAA": {"quantity": 5, "limit_price": 1.0, "stop": 2}}, "limit order"),
    ({"AAA": True}, "AAA"),
])
def test_a_malformed_order_is_refused_before_anything_runs(orders, named):
    sid = opened(universe_size=8)
    r = mcp.session_step(sid, orders=orders)
    assert r["ok"] is False and named in r["error"], r
    assert mcp.session_state(sid)["clock"]["step"] == 0


def test_orders_go_to_an_agent_driven_by_orders():
    sid = opened(agents={"a": None, "b": None, "m": MOMENTUM},
                 universe_size=8)
    r = mcp.session_step(sid, orders={"AAA": 10})
    assert r["ok"] is False and "name the agent" in r["error"]
    r = mcp.session_step(sid, agent="m", orders={"AAA": 10})
    assert r["ok"] is False and "trades by its strategy spec" in r["error"]
    r = mcp.session_step(sid, agent="b", orders={"AAA": 10})
    assert r["ok"], r
    assert r["agents"]["b"]["positions"]["AAA"]["quantity"] == 10.0
    assert r["agents"]["a"]["positions"] == {}


def test_days_advance_through_the_next_close():
    sid = opened(universe_size=6, steps_per_day=4)
    assert mcp.session_step(sid, steps=1)["clock"]["step"] == 1
    r = mcp.session_step(sid, days=1)
    assert r["clock"] == {"step": 4, "day": 1, "step_of_day": 0,
                          "market_open": False, "days_closed": 1}
    assert mcp.session_step(sid, days=2)["clock"]["step"] == 12


# -- what the caller sees --------------------------------------------------

#: Engine methods that read simulator state a trader cannot see, or copy
#: the market. The session's views must not call any of them.
FORBIDDEN = set(sandbox.HIDDEN_STATE) | {"economy", "restore_state",
                                          "session_mispricing_s"}


class WatchedEngine:
    """An engine that fails on any read a sandboxed agent may not make."""

    def __init__(self, engine):
        object.__setattr__(self, "_engine", engine)

    def __getattr__(self, name):
        if name in FORBIDDEN:
            raise AssertionError(f"the session read {name!r}")
        attr = getattr(self._engine, name)
        if name == "column":
            def column(field):
                assert field in sandbox.PUBLIC_COLUMNS, field
                return attr(field)
            return column
        return attr

    def __len__(self):
        return len(self._engine)


def test_a_session_shows_only_what_the_market_view_serves():
    sid = opened(agents={"me": None, "m": MOMENTUM}, universe_size=8)
    mcp.session_step(sid, steps=3, orders={"AAA": 50})
    sess = mcp._sessions[sid]
    live = sess.engine
    sess.engine = WatchedEngine(live)
    try:
        state = mcp.session_state(sid, tickers=["AAA", "AAB"])
        closed = mcp.close_session(sid)
    finally:
        sess.engine = live
    assert state["ok"] and closed["ok"], (state, closed)
    assert state["market"]["macro"] == sandbox.MarketView(live).macro_fields
    assert set(state["market"]["macro"]) <= sandbox.PUBLISHED_MACRO
    text = json.dumps(state)
    for hidden in ("mispricing", "fundamental", "cycle_phase",
                   "recession_probability", "months_in_current_phase",
                   "earnings_cycle", "price_impact", "qe_pe_boost"):
        assert hidden not in text, hidden


def test_every_session_result_says_it_is_not_a_score():
    sid = opened(universe_size=8)
    for r in (mcp.session_state(sid), mcp.session_step(sid),
              mcp.session_fork(sid), mcp.session_rewind(sid, 0)):
        assert r["ok"], r
        assert any(c.startswith("SESSION:") for c in r["caveats"])


# -- the caps ---------------------------------------------------------------


def test_the_session_cap_refuses_one_more_and_names_the_open_ones():
    ids = [opened(universe_size=4) for _ in range(mcp.MAX_SESSIONS)]
    r = mcp.open_session(universe_size=4)
    assert r["ok"] is False and ids[0] in r["error"]
    r = mcp.session_fork(ids[0])
    assert r["ok"] is False and "close_session" in r["error"]
    mcp.close_session(ids[0])
    assert mcp.open_session(universe_size=4)["ok"]


def test_a_session_idle_past_the_limit_is_closed(monkeypatch):
    sid = opened(universe_size=4)
    mcp._sessions[sid].touched -= mcp.SESSION_IDLE_SECONDS + 1
    r = mcp.session_step(sid)
    assert r["ok"] is False and "minutes with no call" in r["error"]
    assert sid not in mcp._sessions


def test_checkpoints_are_capped_and_keep_the_first(monkeypatch):
    monkeypatch.setattr(mcp, "MAX_CHECKPOINTS", 4)
    sid = opened(universe_size=4)
    for _ in range(6):
        mcp.session_step(sid)
    assert mcp.session_state(sid)["checkpoints"] == [0, 4, 5, 6]


@pytest.mark.parametrize("call,expect", [
    (lambda sid: mcp.session_step(sid, steps=mcp.MAX_SESSION_STEPS + 1),
     "Step in parts"),
    (lambda sid: mcp.session_step(sid, steps=0), "steps must be 1 or more"),
    (lambda sid: mcp.session_step(sid, days=0), "days must be 1 or more"),
    (lambda sid: mcp.session_step(sid, steps=1, days=1), "not both"),
])
def test_a_step_past_the_limits_is_refused(call, expect):
    sid = opened(universe_size=4)
    r = call(sid)
    assert r["ok"] is False and expect in r["error"]


def test_a_session_stops_at_its_longest_horizon(monkeypatch):
    monkeypatch.setattr(mcp, "MAX_SESSION_DAYS", 2)
    sid = opened(universe_size=4, steps_per_day=1)
    assert mcp.session_step(sid, days=2)["ok"]
    r = mcp.session_step(sid)
    assert r["ok"] is False and "at most 2 days" in r["error"]


@pytest.mark.parametrize("kwargs,expect", [
    ({"agents": {}}, "at least one"),
    ({"agents": {f"a{i}": None for i in range(9)}}, "at most"),
    ({"agents": {"m": {"signal": {"kind": "telepathy"}}}}, "telepathy"),
    ({"steps_per_day": 0}, "steps_per_day"),
    ({"cash": float("inf")}, "cash"),
    ({"seed": -1}, "seed"),
])
def test_a_bad_open_is_refused_and_registers_nothing(kwargs, expect):
    r = mcp.open_session(universe_size=4, **kwargs)
    assert r["ok"] is False and expect in r["error"], r
    assert not mcp._sessions


def test_an_unknown_session_says_why_it_might_be_missing():
    for call in (lambda: mcp.session_step("session-0"),
                 lambda: mcp.session_state("session-0"),
                 lambda: mcp.session_fork("session-0"),
                 lambda: mcp.session_rewind("session-0", 0),
                 lambda: mcp.close_session("session-0")):
        r = call()
        assert r["ok"] is False and "server process only" in r["error"]


def test_listing_sessions_needs_no_id():
    sid = opened(universe_size=4, preset="pt-v19")
    listed = mcp.session_state()
    assert listed["ok"]
    [row] = listed["sessions"]
    assert row["session_id"] == sid and row["preset"] == "pt-v19"


def test_close_returns_the_orders_that_re_run_the_session():
    """The provenance is enough to rebuild the session: open with the same
    arguments and send each logged order at its step."""
    sid = opened(universe_size=8, seed=4)
    mcp.session_step(sid, steps=2, orders={"AAA": 100})
    mcp.session_step(sid, steps=3, orders={"AAB": -40})
    mcp.session_step(sid, steps=1)
    closed = mcp.close_session(sid)
    assert closed["closed"] and sid not in mcp._sessions
    log = closed["provenance"]["orders"]
    assert log == [{"step": 0, "agent": "me", "orders": {"AAA": 100.0}},
                   {"step": 2, "agent": "me", "orders": {"AAB": -40.0}}]

    # Replayed in two calls rather than three: where a call ends changes
    # which checkpoints are kept and nothing about the market.
    again = opened(universe_size=8, seed=4)
    ends = [entry["step"] for entry in log[1:]] + [closed["clock"]["step"]]
    for entry, end in zip(log, ends):
        assert mcp.session_state(again)["clock"]["step"] == entry["step"]
        mcp.session_step(again, steps=end - entry["step"],
                         orders=entry["orders"])
    assert mcp.session_state(again)["agents"] == closed["agents"]


@pytest.mark.parametrize("name", ["mm", "depth", "flow", "range"])
def test_an_agent_named_after_the_books_own_owners_is_refused(name):
    """The engine refuses every order from such a label, so a session
    under one would trade nothing; it is refused when the session opens."""
    r = mcp.open_session(agents={name: None}, universe_size=4)
    assert r["ok"] is False and "cannot place orders" in r["error"]
    assert not mcp._sessions


def test_a_call_that_waited_on_a_closed_session_is_refused():
    sid = opened(universe_size=4)
    sess = mcp._sessions[sid]
    with mcp._sessions_lock:
        mcp._sessions.pop(sid)
    with sess.lock:
        assert "was closed" in mcp._closed_under(sess)["error"]
