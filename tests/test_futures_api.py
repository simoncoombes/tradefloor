"""The futures' public API (pt-v22 phase 1): contract symbols, the Arrow
tables, the observation and decision payloads at version 3, and the
browser getters' Python twins.

Every surface here reads; none moves a price. On every shipped preset the
futures switches are off, and each surface says so in the shape it had
before: no payload key, an empty table, an empty list.
"""
from __future__ import annotations

import json
import struct

import pytest

import tradefloor as tf
from tradefloor import contracts
from tradefloor.integrations import common as ci
from tradefloor.integrations.callable import CallableAgentAdapter
from tradefloor.render import TextRenderer

SMALL = tf.Universe.random(8, seed=5)

#: Every phase 1 switch on, at the values the accounts tests use.
CONTRACTS = dict(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
                 forecast_vix_dispersion=0.3, forecast_vix_dispersion_half_life=31.5,
                 forecast_vix_dispersion_skew=0.81, forecast_policy_shadow_discount=0.36,
                 forecast_policy_persistence=0.6, forecast_policy_reversion=0.05,
                 forecast_policy_neutral=1.48, futures_index_listed=1.0, basis_sd=3.753,
                 basis_persistence=0.429, futures_vix_listed=1.0,
                 futures_vix_live_fast_share=0.713, futures_vix_live_fast_half_life=5.96,
                 futures_vix_live_slow_half_life=71.7, futures_rates_listed=1.0,
                 futures_oil_listed=1.0, margin_scan_coverage=0.99, margin_scan_tail=1.762)

#: The derivative keys observation schema 3 adds.
DERIVATIVE_KEYS = {"index", "futures", "margin"}


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def engine(**dials):
    return tf.Engine(seed=3, universe=SMALL, model=model(**dials))


def run_days(e, days, *, record=False):
    for _ in range(days):
        e.open_market()
        e.run_session(9, 30, 3, 390)
        if record:
            e.record(e.day_count)
        e.close_market()


def prices(e):
    return struct.unpack("<%dd" % len(e.tickers), e.prices())


# -- contract symbols ---------------------------------------------------------

def test_a_symbol_parses_and_formats_back():
    assert contracts.format("IDX", 56) == "IDX.F0056"
    assert contracts.format("IDX", 12345) == "IDX.F12345"
    assert contracts.parse("IDX.F0273") == {"symbol": "IDX.F0273", "root": "IDX", "expiry": 273,
                                            "kind": "future", "right": None, "strike": None}
    option = contracts.parse("ACME.O0294.C42.50")
    assert (option["kind"], option["right"], option["strike"]) == ("option", "C", 42.5)
    assert contracts.format("ACME", 294, "C", 42.5) == "ACME.O0294.C42.50"
    assert contracts.format("BRK.B", 7, "P", 0.05) == "BRK.B.O0007.P0.05"
    for text in ("IDX.F0056", "VIX.F0035", "FF.F0020", "TR3.F0062", "OIL.F0014",
                 "BRK.B.O0007.P0.05"):
        parsed = contracts.parse(text)
        again = contracts.format(parsed["root"], parsed["expiry"], parsed["right"],
                                 parsed["strike"])
        assert again == text


@pytest.mark.parametrize("bad", ["", "IDX", "IDX.F273", "IDX.F00273", "idx.f0273",
                                 "ACME.O0294.C42.5", "AAA"])
def test_only_the_canonical_spelling_parses(bad):
    with pytest.raises(tf.ValidationError, match="not a contract symbol"):
        contracts.parse(bad)
    assert not contracts.is_contract(bad)


def test_format_refuses_what_cannot_be_a_symbol():
    with pytest.raises(tf.ValidationError):
        contracts.format("IDX", -1)
    with pytest.raises(tf.ValidationError):
        contracts.format("", 3)
    with pytest.raises(tf.ValidationError, match="both right and strike"):
        contracts.format("ACME", 3, "C")
    with pytest.raises(tf.ValidationError, match="C"):
        contracts.format("ACME", 3, "X", 1.0)
    with pytest.raises(tf.ValidationError):
        contracts.format("IDX", 3.0)
    assert contracts.is_contract("IDX.F0056") and not contracts.is_contract(56)


def test_every_listed_contract_is_spelled_as_the_module_spells_it():
    e = engine(**CONTRACTS)
    run_days(e, 2)
    listed = e.contracts()
    assert {c["root"] for c in listed} == set(contracts.FUTURE_ROOTS)
    for c in listed:
        assert contracts.format(c["root"], c["expiry"]) == c["symbol"]
        assert contracts.parse(c["symbol"])["expiry"] == c["expiry"]


# -- the model flag -----------------------------------------------------------

@pytest.mark.parametrize("name", list(tf.preset_names()))
def test_no_shipped_preset_lists_futures(name):
    e = tf.Engine(seed=1, universe=SMALL, model=name)
    assert e.lists_futures is False
    assert tf.MarketView(e).lists_futures is False


def test_the_flag_is_the_models_before_the_first_close_lists_anything():
    e = engine(index_level_listed=1.0, vix_intraday_live=1.0, forecast_horizon_sessions=252.0,
               futures_vix_listed=1.0)
    assert e.lists_futures is True
    assert e.contracts() == []


# -- Arrow --------------------------------------------------------------------

def test_the_tables_are_empty_and_versioned_on_a_shipped_preset():
    pa = pytest.importorskip("pyarrow")
    e = tf.Engine(seed=3, universe=SMALL)
    run_days(e, 2, record=True)
    for stream in (e.futures_bars(), e.settlements_table()):
        table = pa.table(stream)
        assert table.num_rows == 0
        assert table.schema.metadata == {b"schema_version": b"1"}


def test_futures_bars_hold_each_contracts_quote_at_the_record():
    pa = pytest.importorskip("pyarrow")
    e = engine(**CONTRACTS)
    quoted = {}
    for _ in range(3):
        e.open_market()
        e.run_session(9, 30, 3, 390)
        day = e.day_count
        quoted[day] = {c["symbol"]: (c, e.quote(c["symbol"])) for c in e.contracts()}
        e.record(day)
        e.close_market()
    table = pa.table(e.futures_bars())
    assert table.schema.metadata == {b"schema_version": b"1"}
    assert table.column_names == ["day", "symbol", "root", "expiry", "front", "price", "fair",
                                  "basis_bp", "bid", "ask", "mark", "index",
                                  "sessions_to_expiry", "initial_margin"]
    rows = table.to_pylist()
    assert len(rows) == sum(len(v) for v in quoted.values())
    for row in rows:
        c, q = quoted[row["day"]][row["symbol"]]
        assert (row["root"], row["expiry"], row["front"]) == (c["root"], c["expiry"], c["front"])
        for key in ("price", "fair", "basis_bp", "bid", "ask", "mark", "index",
                    "sessions_to_expiry", "initial_margin"):
            assert row[key] == q[key], (row["symbol"], key)
    one = pa.table(e.futures_bars(day=1)).to_pylist()
    assert one and all(r["day"] == 1 for r in one)
    e.clear_recording()
    assert pa.table(e.futures_bars()).num_rows == 0


def test_recording_the_futures_moves_nothing():
    a, b = engine(**CONTRACTS), engine(**CONTRACTS)
    run_days(a, 4, record=True)
    run_days(b, 4)
    assert prices(a) == prices(b)
    assert a.state_hash() == b.state_hash()


def test_the_settlements_table_is_the_settlements_list():
    pa = pytest.importorskip("pyarrow")
    e = engine(**CONTRACTS)
    run_days(e, 30)
    listed = e.settlements()
    assert listed, "no contract settled in 30 sessions"
    table = pa.table(e.settlements_table())
    assert table.schema.metadata == {b"schema_version": b"1"}
    assert table.column_names == ["session", "symbol", "root", "kind", "value", "reference"]
    assert table.to_pylist() == [{k: s[k] for k in table.column_names} for s in listed]
    day = listed[0]["session"]
    assert (pa.table(e.settlements_table(day=day)).to_pylist()
            == [{k: s[k] for k in table.column_names} for s in e.settlements(day)])


# -- observation schema 3 -----------------------------------------------------

def test_the_versions_are_three_and_older_recordings_still_replay():
    assert ci.OBSERVATION_SCHEMA_VERSION == "3"
    assert ci.DECISION_SCHEMA_VERSION == "3"
    assert ci.REPLAYABLE_SCHEMA_VERSIONS == ("1", "2", "3")


class Capture:
    """Records the payloads it is shown; trades what `decide` says."""

    def __init__(self, decide=None):
        self.payloads = []
        self.decide = decide

    def __call__(self, payload):
        self.payloads.append(payload)
        return (self.decide(payload) if self.decide else {"actions": []})


def test_a_shipped_preset_payload_gains_no_key():
    seen = Capture()
    tf.evaluate({"a": CallableAgentAdapter(seen, every=6)}, seed=3, universe=SMALL, days=2)
    assert seen.payloads
    for payload in seen.payloads:
        assert not DERIVATIVE_KEYS & set(payload)
        assert set(payload) == {"step", "day", "steps_per_day", "macro", "assets", "portfolio"}


def test_a_futures_model_payload_carries_the_index_the_futures_and_the_margin():
    def buy_front_index(payload):
        if payload["day"] != 1:
            return {"actions": []}
        front = next(f for f in payload["futures"] if f["root"] == "IDX" and f["front"])
        return {"actions": [{"symbol": front["symbol"], "side": "BUY", "quantity": 2}]}

    seen = Capture(buy_front_index)
    adapter = CallableAgentAdapter(seen, every=6)
    cards = tf.evaluate({"a": adapter}, seed=3, universe=SMALL, model=model(**CONTRACTS), days=4)
    assert cards["a"].trades >= 1, cards["a"].errors
    first, later = seen.payloads[0], seen.payloads[-1]
    for payload in seen.payloads:
        assert DERIVATIVE_KEYS <= set(payload)
        json.dumps(payload)
    assert set(first["index"]) == {"level", "close"}
    assert {f["root"] for f in first["futures"]} == {"IDX"}
    assert {f["root"] for f in later["futures"]} == set(contracts.FUTURE_ROOTS)
    row = later["futures"][0]
    assert set(row) == {"symbol", "root", "expiry", "sessions_to_expiry", "front", "settlement",
                        "price", "fair", "basis_bp", "mark", "best_bid", "best_ask",
                        "underlying", "rate", "dividends", "expected", "premium", "multiplier",
                        "tick", "daily_volume", "max_order_contracts", "initial_margin",
                        "maintenance_margin", "position"}
    assert row["max_order_contracts"] == pytest.approx(ci.MAX_PARTICIPATION * row["daily_volume"])
    held = [f for f in later["futures"] if f["position"]]
    assert [f["position"] for f in held] == [2.0]
    assert set(later["margin"]) == {"initial", "maintenance", "excess", "call"}
    assert later["margin"]["initial"] > 0
    assert later["margin"]["excess"] == pytest.approx(
        later["portfolio"]["net_worth"] - later["margin"]["initial"])
    assert adapter.record[-1]["payload"] is not None


def test_a_contract_order_is_capped_at_its_daily_volume_and_an_unlisted_one_refused():
    e = engine(**CONTRACTS)
    run_days(e, 1)
    e.open_market()
    portfolio = tf.Portfolio(owner="a")
    obs = tf.Observation(0, 1, list(e.tickers), list(prices(e)), tf.PortfolioView(portfolio, e),
                         tf.MarketView(e), tuple(i.avg_volume for i in SMALL), 6)
    front = next(c["symbol"] for c in e.contracts() if c["root"] == "OIL" and c["front"])
    volume = e.quote(front)["daily_volume"]
    decision = ci.parse_decision({"actions": [
        {"symbol": front, "side": "SELL", "quantity": 10 * volume},
        {"symbol": "OIL.F9999", "side": "BUY", "quantity": 1}]})
    refused = []
    orders, notes = ci.orders_from(decision, obs, refused=refused)
    assert orders == {front: -ci.MAX_PARTICIPATION * volume}
    assert "contracts" in notes[0]
    assert "OIL.F9999" in refused[0]["reason"] and front in refused[0]["reason"]


def test_finrobot_and_the_shared_serializer_agree_on_the_derivative_keys():
    from tradefloor.integrations import finrobot

    e = engine(**CONTRACTS)
    run_days(e, 2)
    e.open_market()
    obs = tf.Observation(0, 2, list(e.tickers), list(prices(e)),
                         tf.PortfolioView(tf.Portfolio(owner="a"), e), tf.MarketView(e),
                         tuple(i.avg_volume for i in SMALL), 6)
    shared = ci.serialize_observation(obs)
    own = finrobot.observe(obs)
    for key in DERIVATIVE_KEYS:
        assert own[key] == shared[key]


def test_the_text_rendering_shows_the_futures_only_where_the_payload_has_them():
    e = engine(**CONTRACTS)
    run_days(e, 2)
    e.open_market()
    obs = tf.Observation(0, 2, list(e.tickers), list(prices(e)),
                         tf.PortfolioView(tf.Portfolio(owner="a"), e), tf.MarketView(e),
                         tuple(i.avg_volume for i in SMALL), 6)
    payload = ci.serialize_observation(obs)
    text = TextRenderer().render(payload)
    assert "\nFutures\n" in text and "margin, initial" in text
    for row in payload["futures"]:
        assert row["symbol"] in text
    assert "Contrats a terme" in TextRenderer(language="fr").render(payload)
    bare = {k: v for k, v in payload.items() if k not in DERIVATIVE_KEYS}
    assert "Futures" not in TextRenderer().render(bare)


def test_the_decision_schema_says_a_symbol_may_be_a_contract():
    action = ci.decision_schema()["properties"]["actions"]["items"]["properties"]
    assert "IDX.F0119" in action["symbol"]["description"]
    assert "contracts" in action["quantity"]["description"]


# -- the portfolio's margin ---------------------------------------------------

def test_margin_is_zero_and_asks_nothing_without_futures():
    e = tf.Engine(seed=3, universe=SMALL)
    p = tf.Portfolio(cash=1_000.0)
    assert p.margin(e) == {"initial": 0.0, "maintenance": 0.0, "equity": 1_000.0,
                           "excess": 1_000.0, "call": None}
    view = tf.PortfolioView(p, e)
    assert view.margin() == p.margin(e)
    assert view.futures == {} and view.margin_calls == []


def test_support_md_lists_every_derivative_key():
    """docs/SUPPORT.md states the payload; a key the payload carries and the
    page does not name is a payload nobody wrote down."""
    import pathlib

    support = (pathlib.Path(__file__).resolve().parent.parent / "docs"
               / "SUPPORT.md").read_text(encoding="utf-8")
    e = engine(**CONTRACTS)
    run_days(e, 2)
    e.open_market()
    obs = tf.Observation(0, 2, list(e.tickers), list(prices(e)),
                         tf.PortfolioView(tf.Portfolio(owner="a"), e), tf.MarketView(e),
                         tuple(i.avg_volume for i in SMALL), 6)
    payload = ci.serialize_observation(obs)
    keys = (DERIVATIVE_KEYS | set(payload["index"]) | set(payload["futures"][0])
            | set(payload["margin"]))
    for key in sorted(keys):
        assert f"`{key}`" in support, key
