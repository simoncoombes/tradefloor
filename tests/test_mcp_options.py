"""The MCP server's index options: option_chain, option symbols in
quote_contracts and session_step, and the positions and margin
session_state reports.

No shipped preset lists index options. On a preset that lists no contract
(pt-v21) the tool answers with an empty chain, says so and names the
shipped presets that list contracts. The rest is checked on a session
whose engine is built with the index options switches on, the way a library
caller would build one.
"""

import json

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")

import tradefloor as tf  # noqa: E402
from tradefloor import mcp  # noqa: E402
from test_mcp_futures import PLAIN, says_where  # noqa: E402

#: The surface, the options and their dealer on, with the margin scan, at
#: the values tests/test_index_options.py uses.
OPTIONS = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
               forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
               forecast_vix_dispersion_skew=0.81, surface_ssvi=1.0, surface_skew_physical=-0.8,
               surface_skew_physical_slope=0.3, surface_skew_premium=-1.0, surface_curvature=1.0,
               surface_curvature_exponent=0.5, surface_term_premium_short=-0.03,
               surface_term_premium_long=0.1, surface_earnings_weight=1.0,
               options_index_listed=1.0, option_dealer_spread=0.004, margin_scan_coverage=0.99)

#: The index futures beside them.
FUTURES = dict(futures_index_listed=1.0, basis_sd=3.753, basis_persistence=0.429)


@pytest.fixture(autouse=True)
def no_sessions_left_open():
    for sid in list(mcp._sessions):
        mcp.close_session(sid)
    yield
    for sid in list(mcp._sessions):
        mcp.close_session(sid)


def _sessions_on(monkeypatch, dials):
    model = tf.ModelParams.from_preset("pt-v21", **dials)

    def fresh(self, *, to_restore=False):
        opening = {"macro_state": tf.Macro()} if to_restore else {}
        return tf.Engine(seed=self.seed, universe=self.roster, model=model, **opening)

    monkeypatch.setattr(mcp._Session, "_fresh_engine", fresh)


@pytest.fixture
def options_sessions(monkeypatch):
    """Sessions opened from here on run pt-v21 with the phase 2 switches
    on: what a library caller gets from `tf.Engine(model=...)`, which the
    server's preset argument cannot name."""
    _sessions_on(monkeypatch, OPTIONS)


@pytest.fixture
def both_sessions(monkeypatch):
    """Sessions that list the index futures and the index options."""
    _sessions_on(monkeypatch, dict(OPTIONS, **FUTURES))


def opened(**kw):
    r = mcp.open_session(**kw)
    assert r["ok"], r.get("error")
    return r["session_id"]


def atm(sid, expiry, right="C"):
    """The option at the strike nearest the forward, from option_chain."""
    chain = mcp.option_chain(sid, expiry=expiry, strikes=1)
    rows = [r for r in chain["chain"] if r["right"] == right]
    return min(rows, key=lambda r: abs(r["strike"] - chain["forward"]))["symbol"]


# -- every shipped preset --------------------------------------------------


def test_a_preset_without_contracts_lists_no_option_and_says_where_they_are():
    sid = opened(universe_size=8, preset=PLAIN)
    r = mcp.option_chain(sid)
    assert r["ok"] and r["lists_options"] is False
    assert r["expiries"] == [] and r["chain"] == []
    says_where(r["note"])
    refused = mcp.option_chain(sid, expiry=14)
    assert refused["ok"] is False
    says_where(refused["error"])
    listed = mcp.list_contracts(sid)
    assert "option_expiries" not in listed and "lists_options" not in listed


def test_a_preset_without_contracts_session_view_shows_no_option():
    sid = opened(universe_size=8, preset=PLAIN)
    state = mcp.session_state(sid)
    assert "options_listed" not in state["market"]
    assert not {"options", "margin"} & set(state["agents"]["me"])


def test_an_option_symbol_on_a_preset_without_contracts_is_refused_with_the_reason():
    sid = opened(universe_size=8, preset=PLAIN)
    r = mcp.session_step(sid, orders={"IDX.O0014.C1000.00": 1})
    assert r["ok"] is False and "contract symbol" in r["error"]
    says_where(r["error"])


def test_describe_simulator_lists_the_options():
    instruments = mcp.describe_simulator()["instruments"]
    options = instruments["options"]
    assert options["families"] == [{"root": "IDX", "listed_by": "options_index_listed"}]
    assert options["on_shipped_presets"] == []
    assert "option_chain" in options["how"] and "No shipped preset" in options["how"]
    names = instruments["name_options"]
    assert PLAIN not in names["on_shipped_presets"]
    assert "yet" not in names["how"] and "yet" not in options["how"]


def test_strikes_out_of_range_is_refused():
    sid = opened(universe_size=8)
    for bad in (0, mcp.MAX_CHAIN_STRIKES + 1, True):
        r = mcp.option_chain(sid, strikes=bad)
        assert r["ok"] is False and "strikes" in r["error"]


# -- a session that lists options -------------------------------------------


def test_option_chain_is_the_engines_expiries_and_chain(options_sessions):
    sid = opened(universe_size=8)
    mcp.session_step(sid, days=1)
    engine = mcp._sessions[sid].engine
    expiries = engine.option_expiries()
    r = mcp.option_chain(sid)
    assert r["ok"] and r["lists_options"] is True
    assert [e["expiry"] for e in r["expiries"]] == [e["expiry"] for e in expiries]
    assert r["expiries"][0]["atm_iv"] == round(expiries[0]["atm_iv"], 6)
    assert r["expiries"][0]["skew_25d"] == round(expiries[0]["skew_25d"], 6)
    # The nearest expiry, ten strikes either side of the forward.
    assert r["expiry"] == expiries[0]["expiry"]
    strikes = sorted({row["strike"] for row in r["chain"]})
    assert len(strikes) == 20 and len(r["chain"]) == 40
    assert strikes[9] <= r["forward"] < strikes[10]
    live = {q["symbol"]: q for q in engine.chain("IDX", r["expiry"])}
    for row in r["chain"]:
        q = live[row["symbol"]]
        assert row["mid"] == round(q["mid"], 4) and row["iv"] == round(q["iv"], 6)
        assert row["symbol"] == tf.contracts.format("IDX", r["expiry"], row["right"],
                                                    row["strike"])
    named = mcp.option_chain(sid, expiry=expiries[2]["expiry"], strikes=2)
    assert named["expiry"] == expiries[2]["expiry"] and len(named["chain"]) == 8
    refused = mcp.option_chain(sid, expiry=3)
    assert refused["ok"] is False and str(expiries[0]["expiry"]) in refused["error"]
    json.dumps(r)


def test_list_contracts_summarises_the_expiries_and_lists_no_option_row(options_sessions):
    sid = opened(universe_size=8)
    listed = mcp.list_contracts(sid)
    assert listed["ok"] and listed["contracts"] == []
    assert listed["lists_options"] is True and listed["lists_futures"] is False
    assert listed["option_expiries"] == mcp.option_chain(sid)["expiries"]
    assert "option_chain" in listed["note"]
    assert mcp.quote_contracts(sid)["quotes"] == {}


def test_quote_contracts_quotes_an_option_with_its_margin(options_sessions):
    sid = opened(universe_size=8)
    mcp.session_step(sid, days=1)
    symbol = atm(sid, None, "P")
    q = mcp.quote_contracts(sid, symbols=[symbol])["quotes"][symbol]
    live = mcp._sessions[sid].engine.quote(symbol)
    assert q["kind"] == "option" and q["right"] == "P"
    assert q["initial_margin"] == round(live["initial_margin"], 2) > 0
    assert q["mid"] == round(live["mid"], 4) and q["surface_iv"] == round(live["surface_iv"], 6)
    refused = mcp.quote_contracts(sid, symbols=["IDX.O0014.P1.00"])
    assert refused["ok"] is False and "option_chain" in refused["error"]


def test_a_reading_tool_moves_nothing(options_sessions):
    a, b = opened(universe_size=8), opened(universe_size=8)
    mcp.session_step(a, days=1)
    mcp.session_step(b, days=1)
    mcp.option_chain(a)
    mcp.list_contracts(a)
    mcp.quote_contracts(a, symbols=[atm(a, None)])
    mcp.session_state(a)
    ea, eb = mcp._sessions[a].engine, mcp._sessions[b].engine
    assert ea.state_hash() == eb.state_hash()


def test_options_are_placed_listed_and_cancelled_through_session_step(options_sessions):
    sid = opened(universe_size=8)
    mcp.session_step(sid, days=1)
    symbol = atm(sid, None)
    bought = mcp.session_step(sid, orders={symbol: 3})
    assert bought["ok"], bought.get("error")
    me = bought["agents"]["me"]
    assert me["orders_sent"] == {symbol: 3.0}
    assert [f["ticker"] for f in me["fills"]] == [symbol]
    assert me["options"][symbol]["quantity"] == 3.0
    assert me["margin"]["initial"] > 0
    engine = mcp._sessions[sid].engine
    assert bought["market"]["options_listed"] == sum(
        c["kind"] == "option" for c in engine.contracts())
    assert "contracts_listed" not in bought["market"]
    # A limit far above the dealer's ask waits outside any book, is listed
    # with the agent's open orders, and a cancel withdraws it.
    ask = mcp.quote_contracts(sid, symbols=[symbol])["quotes"][symbol]["ask"]
    waiting = mcp.session_step(sid, orders={symbol: {"quantity": -2,
                                                     "limit_price": round(3 * ask, 1)}})
    assert waiting["ok"], waiting.get("error")
    orders = mcp.session_state(sid)["agents"]["me"]["open_orders"]
    assert [(o["ticker"], o["side"], o["remaining"]) for o in orders] == [(symbol, "sell", 2.0)]
    cancelled = mcp.session_step(sid, orders={symbol: "cancel"})
    assert cancelled["ok"] and cancelled["agents"]["me"]["open_orders"] == []
    # Selling the three back closes the position.
    closed = mcp.session_step(sid, orders={symbol: -3})
    assert closed["ok"] and closed["agents"]["me"]["options"] == {}


def test_a_fork_carries_the_options_and_continues_as_the_original(options_sessions):
    sid = opened(universe_size=8)
    mcp.session_step(sid, days=1)
    symbol = atm(sid, None, "P")
    mcp.session_step(sid, orders={symbol: -4})
    child = mcp.session_fork(sid)["session_id"]
    a = mcp.session_step(sid, days=1)
    b = mcp.session_step(child, days=1)
    assert a["agents"] == b["agents"]
    assert a["agents"]["me"]["options"][symbol]["quantity"] == -4.0


def test_an_unlisted_option_names_option_chain(options_sessions):
    sid = opened(universe_size=8)
    r = mcp.session_step(sid, orders={"IDX.O0014.C1.00": 1})
    assert r["ok"] is False
    assert "IDX.O0014.C1.00" in r["error"] and "option_chain" in r["error"]
    assert "IDX.O0014.C1000.00" not in r["error"], "every listed option spelled out"


# -- a session that lists both ----------------------------------------------


def test_with_futures_and_options_each_is_listed_where_it_belongs(both_sessions):
    sid = opened(universe_size=8)
    mcp.session_step(sid, days=1)
    listed = mcp.list_contracts(sid)
    assert {c["kind"] for c in listed["contracts"]} == {"future"}
    assert listed["lists_futures"] and listed["lists_options"]
    future = next(c["symbol"] for c in listed["contracts"] if c["front"])
    option = atm(sid, None)
    r = mcp.session_step(sid, orders={future: 1, option: 2})
    assert r["ok"], r.get("error")
    me = r["agents"]["me"]
    assert me["futures"][future]["quantity"] == 1.0
    assert me["options"][option]["quantity"] == 2.0
    assert r["market"]["contracts_listed"] == len(listed["contracts"])
    assert r["market"]["options_listed"] > 600
    bad = mcp.session_step(sid, orders={"IDX.F9999": 1})
    assert future in bad["error"] and "option_chain" in bad["error"]
