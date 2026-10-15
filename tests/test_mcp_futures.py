"""The MCP server's futures (pt-v22 phase 1): list_contracts,
quote_contracts, contract symbols in session_step, and the margin
session_state reports.

No shipped preset lists futures, and a session runs a shipped preset by
name, so on every preset the tools answer with an empty list and say why.
The rest is checked on a session whose engine is built with the phase 1
switches on, the way a library caller would build one.
"""

import json

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")

import tradefloor as tf  # noqa: E402
from tradefloor import mcp  # noqa: E402

CONTRACTS = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
                 forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                 forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
                 forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
                 forecast_policy_neutral=1.48, futures_index_listed=1.0, basis_sd=3.753,
                 basis_persistence=0.429, futures_vix_listed=1.0,
                 futures_vix_live_fast_share=0.713, futures_vix_live_fast_half_life=5.96,
                 futures_vix_live_slow_half_life=71.7, futures_rates_listed=1.0,
                 futures_oil_listed=1.0, margin_scan_coverage=0.99, margin_scan_tail=1.762)


@pytest.fixture(autouse=True)
def no_sessions_left_open():
    for sid in list(mcp._sessions):
        mcp.close_session(sid)
    yield
    for sid in list(mcp._sessions):
        mcp.close_session(sid)


@pytest.fixture
def futures_sessions(monkeypatch):
    """Sessions opened from here on run pt-v21 with every phase 1 switch on:
    what a library caller gets from `tf.Engine(model=...)`, which the
    server's preset argument cannot name."""
    model = tf.ModelParams.from_preset("pt-v21", **CONTRACTS)

    def fresh(self, *, to_restore=False):
        opening = {"macro_state": tf.Macro()} if to_restore else {}
        return tf.Engine(seed=self.seed, universe=self.roster, model=model, **opening)

    monkeypatch.setattr(mcp._Session, "_fresh_engine", fresh)


def opened(**kw):
    r = mcp.open_session(**kw)
    assert r["ok"], r.get("error")
    return r["session_id"]


# -- every shipped preset --------------------------------------------------


def test_a_shipped_preset_lists_nothing_and_says_why():
    sid = opened(universe_size=8)
    listed = mcp.list_contracts(sid)
    assert listed["ok"] and listed["contracts"] == []
    assert listed["lists_futures"] is False and listed["index_level"] is None
    assert "no shipped preset" in listed["note"]
    quoted = mcp.quote_contracts(sid)
    assert quoted["ok"] and quoted["quotes"] == {}
    refused = mcp.quote_contracts(sid, symbols=["IDX.F0056"])
    assert refused["ok"] is False and "no shipped preset" in refused["error"]


def test_a_shipped_preset_session_view_is_the_one_it_was():
    sid = opened(universe_size=8)
    state = mcp.session_state(sid)
    assert "index_level" not in state["market"]
    assert "contracts_listed" not in state["market"]
    assert not {"futures", "margin"} & set(state["agents"]["me"])


def test_a_contract_symbol_on_a_shipped_preset_is_refused_with_the_reason():
    sid = opened(universe_size=8)
    r = mcp.session_step(sid, orders={"IDX.F0056": 1})
    assert r["ok"] is False
    assert "contract symbol" in r["error"] and "no shipped preset" in r["error"]


def test_describe_simulator_lists_the_instrument_kinds():
    d = mcp.describe_simulator()
    futures = d["instruments"]["futures"]
    assert [f["root"] for f in futures["families"]] == ["IDX", "VIX", "FF", "TR3", "OIL"]
    assert futures["on_shipped_presets"] == []
    assert {"equities", "rate_indices"} <= set(d["instruments"])
    assert mcp.describe_simulator() == d


# -- a session that lists futures -------------------------------------------


def test_list_contracts_is_the_engines_list(futures_sessions):
    sid = opened(universe_size=8)
    first = mcp.list_contracts(sid)
    assert first["ok"] and first["lists_futures"] is True
    assert [c["root"] for c in first["contracts"]] == ["IDX", "IDX"]
    assert set(first["index_level"]) == {"level", "close"}
    mcp.session_step(sid, days=1)
    later = mcp.list_contracts(sid)
    engine = mcp._sessions[sid].engine
    assert [c["symbol"] for c in later["contracts"]] == [c["symbol"] for c in engine.contracts()]
    assert {c["root"] for c in later["contracts"]} == set(tf.contracts.FUTURE_ROOTS)
    row = later["contracts"][0]
    assert set(row) == {"symbol", "root", "kind", "expiry", "roll", "front", "multiplier",
                        "tick", "settlement"}
    json.dumps(later)


def test_quote_contracts_quotes_the_fronts_or_the_symbols_named(futures_sessions):
    sid = opened(universe_size=8)
    mcp.session_step(sid, days=1)
    engine = mcp._sessions[sid].engine
    fronts = [c["symbol"] for c in engine.contracts() if c["front"]]
    r = mcp.quote_contracts(sid)
    assert r["ok"] and list(r["quotes"]) == fronts
    q = r["quotes"][fronts[0]]
    live = engine.quote(fronts[0])
    assert q["price"] == round(live["price"], 4)
    assert q["initial_margin"] == round(live["initial_margin"], 2)
    named = [c["symbol"] for c in engine.contracts()][-2:]
    assert list(mcp.quote_contracts(sid, symbols=named)["quotes"]) == named
    refused = mcp.quote_contracts(sid, symbols=["IDX.F9999"])
    assert refused["ok"] is False and fronts[0] in refused["error"]


def test_a_reading_tool_moves_nothing(futures_sessions):
    a, b = opened(universe_size=8), opened(universe_size=8)
    mcp.session_step(a, days=1)
    mcp.session_step(b, days=1)
    mcp.list_contracts(a)
    mcp.quote_contracts(a)
    ea, eb = mcp._sessions[a].engine, mcp._sessions[b].engine
    assert ea.state_hash() == eb.state_hash()


def test_session_step_trades_a_contract_and_session_state_reports_the_margin(futures_sessions):
    sid = opened(universe_size=8)
    mcp.session_step(sid, days=1)
    symbol = next(c["symbol"] for c in mcp.list_contracts(sid)["contracts"]
                  if c["root"] == "IDX" and c["front"])
    r = mcp.session_step(sid, orders={symbol: 2})
    assert r["ok"], r.get("error")
    me = r["agents"]["me"]
    assert me["orders_sent"] == {symbol: 2.0}
    assert [f["ticker"] for f in me["fills"]] == [symbol]
    assert me["futures"][symbol]["quantity"] == 2.0
    mcp.session_step(sid, days=1)
    state = mcp.session_state(sid)
    assert state["market"]["contracts_listed"] == len(mcp._sessions[sid].engine.contracts())
    held = state["agents"]["me"]
    assert held["futures"][symbol]["quantity"] == 2.0
    assert held["margin"]["initial"] > 0
    assert set(held["margin"]) == {"initial", "maintenance", "excess", "call", "calls"}
    assert held["futures"][symbol]["mark"] == round(
        mcp._sessions[sid].engine.quote(symbol)["mark"], 4)


def test_a_fork_carries_the_futures_and_continues_as_the_original(futures_sessions):
    sid = opened(universe_size=8)
    mcp.session_step(sid, days=1)
    symbol = next(c["symbol"] for c in mcp.list_contracts(sid)["contracts"]
                  if c["root"] == "OIL" and c["front"])
    mcp.session_step(sid, orders={symbol: -3})
    child = mcp.session_fork(sid)["session_id"]
    a = mcp.session_step(sid, days=1)
    b = mcp.session_step(child, days=1)
    assert a["agents"] == b["agents"]
    assert a["agents"]["me"]["futures"][symbol]["quantity"] == -3.0


def test_an_unlisted_symbol_names_the_listed_ones(futures_sessions):
    sid = opened(universe_size=8)
    r = mcp.session_step(sid, orders={"IDX.F9999": 1})
    assert r["ok"] is False
    assert "IDX.F9999" in r["error"] and "list_contracts" in r["error"]
