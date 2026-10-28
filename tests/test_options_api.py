"""The index options' public API (pt-v22 phase 2): the expiry summary, the
Arrow option-quotes table, the observation payload's `options` key and
option symbols in a decision, the text rendering, and the read-only views.

Every surface here reads; none moves a price. On every shipped preset
`options_index_listed` is off, and each surface says so in the shape it had
before: no payload key, an empty table, an empty list.
"""
from __future__ import annotations

import json
import math
import pathlib
import struct

import pytest

import tradefloor as tf
from tradefloor import contracts
from tradefloor.integrations import common as ci
from tradefloor.integrations.callable import CallableAgentAdapter
from tradefloor.render import TextRenderer

SMALL = tf.Universe.random(8, seed=5)

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

#: The keys of one listed expiry, in the payload and from the engine.
EXPIRY_KEYS = {"root", "expiry", "sessions_to_expiry", "forward", "strike_low", "strike_high",
               "strike_step", "atm_iv", "put_25d_iv", "call_25d_iv", "skew_25d"}

BASE_KEYS = {"step", "day", "steps_per_day", "macro", "assets", "portfolio"}


def model(**dials):
    return tf.ModelParams.from_preset("pt-v21", **dials)


def engine(**dials):
    return tf.Engine(seed=3, universe=SMALL, model=model(**dials))


def run_days(e, days):
    for _ in range(days):
        e.open_market()
        e.run_session(9, 30, 3, 390)
        e.close_market()


def prices(e):
    return struct.unpack("<%dd" % len(e.tickers), e.prices())


def observation(e):
    return tf.Observation(0, e.day_count, list(e.tickers), list(prices(e)),
                          tf.PortfolioView(tf.Portfolio(owner="a"), e), tf.MarketView(e),
                          tuple(i.avg_volume for i in SMALL), 6)


# -- the model flag and the symbols --------------------------------------------

@pytest.mark.parametrize("name", list(tf.preset_names()))
def test_no_shipped_preset_lists_options(name):
    e = tf.Engine(seed=1, universe=SMALL, model=name)
    assert e.lists_options is False
    assert tf.MarketView(e).lists_options is False
    assert e.option_expiries() == []


def test_the_option_roots_name_their_switch_and_every_listed_option_is_spelled_so():
    assert contracts.OPTION_ROOTS == {"IDX": "options_index_listed"}
    e = engine(**OPTIONS)
    assert e.lists_options is True and e.lists_futures is False
    for c in (c for c in e.contracts() if c["kind"] == "option"):
        assert contracts.format(c["root"], c["expiry"], c["right"], c["strike"]) == c["symbol"]


# -- the expiry summary ---------------------------------------------------------

def _read(chain):
    """The at-the-money and 25-delta volatilities, read again here from the
    chain: linear in log-moneyness at the forward, linear in delta at 25."""
    def side(right):
        return sorted((q for q in chain if q["right"] == right), key=lambda q: q["strike"])

    calls, puts = side("C"), side("P")
    atm = None
    for a, b in zip(calls, calls[1:]):
        ka, kb = math.log(a["strike"] / a["forward"]), math.log(b["strike"] / b["forward"])
        if ka <= 0.0 <= kb:
            atm = a["iv"] + (b["iv"] - a["iv"]) * (0.0 - ka) / (kb - ka)
            break

    def at(rows, target):
        for a, b in zip(rows, rows[1:]):
            if a["delta"] >= target >= b["delta"]:
                return a["iv"] + (b["iv"] - a["iv"]) * (a["delta"] - target) / (a["delta"] - b["delta"])
        return None

    return atm, at(puts, -0.25), at(calls, 0.25)


def test_each_expiry_reads_its_chain_at_the_forward_and_at_25_delta():
    e = engine(**OPTIONS)
    run_days(e, 3)
    e.open_market()
    e.run_session(9, 30, 3, 100)
    read = e.option_expiries()
    assert [r["expiry"] for r in read] == sorted({c["expiry"] for c in e.contracts()})
    for r in read:
        assert set(r) == EXPIRY_KEYS and r["root"] == "IDX"
        chain = e.chain("IDX", r["expiry"])
        atm, put, call = _read(chain)
        assert r["atm_iv"] == pytest.approx(atm, rel=1e-12)
        assert r["put_25d_iv"] == pytest.approx(put, rel=1e-12)
        assert r["call_25d_iv"] == pytest.approx(call, rel=1e-12)
        assert r["skew_25d"] == r["put_25d_iv"] - r["call_25d_iv"]
        assert r["skew_25d"] > 0, "the puts are dearer"
        assert r["forward"] == chain[0]["forward"]
        assert (r["strike_low"], r["strike_high"]) == (chain[0]["strike"], chain[-1]["strike"])
        assert len(chain) == 2 * (round((r["strike_high"] - r["strike_low"]) / r["strike_step"]) + 1)
    assert tf.MarketView(e).option_expiries() == read


def test_reading_the_expiries_and_the_chains_moves_nothing():
    a, b = engine(**OPTIONS), engine(**OPTIONS)
    for _ in range(4):
        for e in (a, b):
            e.open_market()
            e.run_session(9, 30, 3, 390)
        a.option_expiries()
        a.chain_table()
        for e in (a, b):
            e.close_market()
    assert prices(a) == prices(b)
    assert a.state_hash() == b.state_hash()


# -- Arrow ------------------------------------------------------------------------

def test_the_option_table_is_empty_and_versioned_on_a_shipped_preset():
    pa = pytest.importorskip("pyarrow")
    e = tf.Engine(seed=3, universe=SMALL)
    run_days(e, 1)
    table = pa.table(e.chain_table())
    assert table.num_rows == 0
    assert table.schema.metadata == {b"schema_version": b"1"}
    assert pa.table(e.chain_table(expiry=14)).num_rows == 0


def test_the_option_table_is_every_chain_now():
    pa = pytest.importorskip("pyarrow")
    e = engine(**OPTIONS)
    run_days(e, 2)
    e.open_market()
    e.run_session(9, 30, 3, 60)
    table = pa.table(e.chain_table())
    assert table.schema.metadata == {b"schema_version": b"1"}
    assert table.column_names == ["day", "symbol", "expiry", "right", "strike", "bid", "ask",
                                  "bid_size", "ask_size", "mid", "iv", "fair", "surface_iv",
                                  "delta", "gamma", "vega", "theta", "forward", "index", "rate",
                                  "dividends", "sessions_to_expiry", "open_interest"]
    rows = table.to_pylist()
    quoted = [q for x in e.option_expiries() for q in e.chain("IDX", x["expiry"])]
    assert len(rows) == len(quoted) > 600
    for row, q in zip(rows, quoted):
        assert row["day"] == e.day_count
        for key in table.column_names[1:]:
            assert row[key] == q[key], (row["symbol"], key)
    second = e.option_expiries()[1]["expiry"]
    one = pa.table(e.chain_table(expiry=second)).to_pylist()
    assert {r["expiry"] for r in one} == {second}
    assert [r["symbol"] for r in one] == [q["symbol"] for q in e.chain("IDX", second)]
    with pytest.raises(tf.ValidationError, match="option_expiries"):
        e.chain_table(expiry=3)


def test_the_futures_bars_hold_no_option():
    pa = pytest.importorskip("pyarrow")
    e = engine(**OPTIONS, **FUTURES)
    for _ in range(2):
        e.open_market()
        e.run_session(9, 30, 3, 390)
        e.record(e.day_count)
        e.close_market()
    rows = pa.table(e.futures_bars()).to_pylist()
    assert rows and {r["root"] for r in rows} == {"IDX"}
    assert all(contracts.parse(r["symbol"])["kind"] == "future" for r in rows)


# -- observation schema 3 -------------------------------------------------------

class Capture:
    """Records the payloads it is shown; trades what `decide` says."""

    def __init__(self, decide=None):
        self.payloads = []
        self.decide = decide

    def __call__(self, payload):
        self.payloads.append(payload)
        return (self.decide(payload) if self.decide else {"actions": []})


def _buy_the_money_put(payload):
    """Four of the second expiry's put at the strike nearest the forward,
    and one symbol that is not listed, on the first step of day 1."""
    if payload["day"] != 1 or payload["step"] % payload["steps_per_day"]:
        return {"actions": []}
    e = payload["options"]["expiries"][1]
    strike = round(e["forward"] / e["strike_step"]) * e["strike_step"]
    symbol = contracts.format("IDX", e["expiry"], "P", strike)
    return {"actions": [{"symbol": symbol, "side": "BUY", "quantity": 4},
                        {"symbol": "IDX.O0035.P1.00", "side": "BUY", "quantity": 1}]}


def test_a_shipped_preset_payload_gains_no_options_key():
    seen = Capture()
    tf.evaluate({"a": CallableAgentAdapter(seen, every=6)}, seed=3, universe=SMALL, days=2,
                model="pt-v21")
    assert seen.payloads
    for payload in seen.payloads:
        assert set(payload) == BASE_KEYS


def test_an_options_model_payload_carries_the_expiries_the_positions_and_the_margin():
    seen = Capture(_buy_the_money_put)
    adapter = CallableAgentAdapter(seen, every=6)
    cards = tf.evaluate({"a": adapter}, seed=3, universe=SMALL, model=model(**OPTIONS), days=3)
    assert cards["a"].trades == 1, cards["a"].errors
    refused, = cards["a"].errors
    assert "IDX.O0035.P1.00" in refused and "ROOT.Onnnn.Rk.cc" in refused
    for payload in seen.payloads:
        assert set(payload) == BASE_KEYS | {"index", "margin", "options"}
        json.dumps(payload)
    first, later = seen.payloads[0], seen.payloads[-1]
    assert first["options"]["positions"] == []
    assert len(first["options"]["expiries"]) == 8
    assert all(set(e) == EXPIRY_KEYS for e in later["options"]["expiries"])
    held, = later["options"]["positions"]
    assert set(held) == {"symbol", "expiry", "right", "strike", "quantity", "cost",
                         "multiplier", "best_bid", "best_ask", "mid", "iv", "delta",
                         "sessions_to_expiry"}
    assert (held["right"], held["quantity"], held["multiplier"]) == ("P", 4.0, 100.0)
    assert held["delta"] < 0 and held["cost"] > 0
    assert later["margin"]["initial"] > 0
    assert later["margin"]["excess"] == pytest.approx(
        later["portfolio"]["net_worth"] - later["margin"]["initial"])


def test_with_futures_too_the_payload_carries_all_four_keys_and_each_in_its_place():
    e = engine(**OPTIONS, **FUTURES)
    run_days(e, 2)
    e.open_market()
    payload = ci.serialize_observation(observation(e))
    assert list(payload)[len(BASE_KEYS):] == ["index", "futures", "margin", "options"]
    assert {f["root"] for f in payload["futures"]} == {"IDX"}
    assert all(contracts.parse(f["symbol"])["kind"] == "future" for f in payload["futures"])
    assert payload["options"]["expiries"] == e.option_expiries()


def test_an_option_order_trades_whole_a_cancel_withdraws_it_and_an_unlisted_one_is_refused():
    e = engine(**OPTIONS)
    run_days(e, 1)
    e.open_market()
    obs = observation(e)
    x = e.option_expiries()[0]
    call = contracts.format("IDX", x["expiry"], "C", x["strike_low"] + 20 * x["strike_step"])
    decision = ci.parse_decision({"actions": [
        {"symbol": call, "side": "SELL", "quantity": 5_000},
        {"symbol": "IDX.O0014.C1.00", "side": "BUY", "quantity": 1},
        {"symbol": "IDX.F0056", "side": "BUY", "quantity": 1}]})
    refused = []
    orders, notes = ci.orders_from(decision, obs, refused=refused)
    # No daily volume to cap an option at: the dealer's ladder fills what
    # it shows and the leverage limit decides the rest.
    assert orders == {call: -5_000.0} and notes == []
    assert [r["action"]["symbol"] for r in refused] == ["IDX.O0014.C1.00", "IDX.F0056"]
    assert "ROOT.Onnnn.Rk.cc" in refused[0]["reason"]
    cancel = ci.parse_decision({"actions": [{"symbol": call, "side": "CANCEL"}]})
    orders, _ = ci.orders_from(cancel, obs, refused=[])
    assert isinstance(orders[call], tf.Cancel)


def test_finrobot_and_the_shared_serializer_agree_on_the_options():
    from tradefloor.integrations import finrobot

    e = engine(**OPTIONS)
    run_days(e, 2)
    e.open_market()
    obs = observation(e)
    shared = ci.serialize_observation(obs)
    own = finrobot.observe(obs)
    for key in ("index", "margin", "options"):
        assert own[key] == shared[key]


def test_the_text_rendering_shows_the_options_only_where_the_payload_has_them():
    e = engine(**OPTIONS)
    run_days(e, 2)
    e.open_market()
    payload = ci.serialize_observation(observation(e))
    text = TextRenderer().render(payload)
    assert "\nIndex options\n" in text and "Futures" not in text
    # With no futures block, the options block shows the index and margin.
    assert "price index" in text and "margin, initial" in text
    for row in payload["options"]["expiries"]:
        assert f"{row['skew_25d'] * 100:.2f}%" in text
    assert "Options sur indice" in TextRenderer(language="fr").render(payload)
    bare = {k: v for k, v in payload.items() if k not in ("index", "margin", "options")}
    assert "Index options" not in TextRenderer().render(bare)


def test_the_decision_schema_says_a_symbol_may_be_an_option():
    action = ci.decision_schema()["properties"]["actions"]["items"]["properties"]
    assert "IDX.O0035.C1050.00" in action["symbol"]["description"]
    assert "option" in action["quantity"]["description"]


# -- the read-only views ---------------------------------------------------------

def test_the_portfolio_view_hands_out_copies_of_the_options():
    e = engine(**OPTIONS)
    run_days(e, 1)
    e.open_market()
    e.run_session(9, 30, 3, 10)
    p = tf.Portfolio(cash=1_000_000.0, owner="a")
    view = tf.PortfolioView(p, e)
    assert view.options == {}
    x = e.option_expiries()[0]
    symbol = contracts.format("IDX", x["expiry"], "P", x["strike_low"] + 20 * x["strike_step"])
    p.execute(e, symbol, 2.0)
    copy = view.options[symbol]
    assert (copy.quantity, copy.cost) == (p.options[symbol].quantity, p.options[symbol].cost)
    copy.quantity = 99.0
    assert p.options[symbol].quantity == 2.0
    assert view.margin() == p.margin(e) and view.margin()["initial"] > 0


def test_support_md_lists_every_options_key():
    """docs/SUPPORT.md states the payload; a key the payload carries and the
    page does not name is a payload nobody wrote down."""
    support = (pathlib.Path(__file__).resolve().parent.parent / "docs"
               / "SUPPORT.md").read_text(encoding="utf-8")
    seen = Capture(_buy_the_money_put)
    tf.evaluate({"a": CallableAgentAdapter(seen, every=6)}, seed=3, universe=SMALL,
                model=model(**OPTIONS), days=3)
    options = seen.payloads[-1]["options"]
    keys = {"options"} | set(options) | set(options["expiries"][0]) | set(options["positions"][0])
    for key in sorted(keys):
        assert f"`{key}`" in support, key
