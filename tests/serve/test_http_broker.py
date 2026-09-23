"""The broker-shaped facade: `/broker/{session_id}/v2/...`, an Alpaca v2 subset.

The claim the facade makes is that a bot written against that API can be
pointed at a session by changing its base URL. Three kinds of test check it:

- ROUTE tests (fake): every supported endpoint, its JSON shape, and that
  everything unsupported is refused with a message rather than approximated.
- A BOT test, parametrised over the fake and the real core: a small client in
  the shape a bot has (raw HTTP, Alpaca paths and fields), running its loop.
- The REAL Alpaca SDK (`alpaca-py`, skipped when not installed), pointed at a
  live server with `url_override`: the strictest reader of the shapes there
  is, because it parses every response into pydantic models.
"""

import contextlib
import socket
import threading
import time
import uuid
from datetime import date, datetime

import pytest

pytest.importorskip("fastapi", reason="the HTTP server is an opt-in extra")

from fastapi.testclient import TestClient  # noqa: E402

from fakes import FakeSessionService, core_available, core_has, make_service  # noqa: E402
from tradefloor.serve.http import create_app, sim_date, sim_datetime  # noqa: E402

CORE = pytest.mark.skipif(not core_available(), reason="tradefloor.serve.core has not landed")
CORE04 = pytest.mark.skipif(not core_has("news"), reason="the core has not landed contract 0.4")
SMALL = {"universe_size": 4, "ticks_per_step": 30}


def _session(client, **config):
    r = client.post("/v1/sessions", json={**SMALL, **config})
    assert r.status_code == 201, r.text
    return r.json()


class Broker:
    """The facade as a bot sees it: a base URL and Alpaca paths."""

    def __init__(self, client, session_id):
        self.client, self.base = client, f"/broker/{session_id}/v2"

    def get(self, path, **params):
        return self.client.get(self.base + path, params=params)

    def post(self, path, body=None):
        return self.client.post(self.base + path, json=body)

    def delete(self, path, **params):
        return self.client.delete(self.base + path, params=params)

    def ok(self, response, status=200):
        assert response.status_code == status, (response.status_code, response.text)
        return response.json() if response.content else None


@pytest.fixture
def fake():
    return FakeSessionService()


@pytest.fixture
def setup(fake):
    client = TestClient(create_app(fake))
    info = _session(client)
    return client, info, Broker(client, info["session_id"])


def _uuid(s):
    return str(uuid.UUID(s)) == s


def _err(r, status, code):
    assert r.status_code == status, (r.status_code, r.text)
    assert set(r.json()) == {"code", "message"} and r.json()["code"] == code, r.json()
    return r.json()["message"]


# -- account, clock, calendar, assets ------------------------------------------------


def test_account_matches_the_native_account(setup):
    client, info, b = setup
    acct = b.ok(b.get("/account"))
    native = client.get(f"/v1/sessions/{info['session_id']}/observation").json()["account"]
    assert _uuid(acct["id"]) and acct["status"] == "ACTIVE" and acct["currency"] == "USD"
    for field in ("cash", "equity", "portfolio_value", "last_equity", "buying_power",
                  "regt_buying_power", "long_market_value", "short_market_value", "multiplier"):
        assert isinstance(acct[field], str), field
        float(acct[field])
    assert float(acct["cash"]) == native["cash"]
    assert float(acct["equity"]) == float(acct["portfolio_value"]) == native["net_worth"]
    assert float(acct["multiplier"]) == info["config"]["max_leverage"]
    assert float(acct["buying_power"]) == pytest.approx(
        info["config"]["max_leverage"] * native["net_worth"] - native["gross_exposure"])
    assert acct["trading_blocked"] is False and acct["shorting_enabled"] is True
    # Fields Alpaca removed on 2026-07-06 are not reintroduced here.
    for gone in ("pattern_day_trader", "daytrade_count", "daytrading_buying_power"):
        assert gone not in acct


def test_an_uncapped_account_has_no_buying_power_figure(fake):
    client = TestClient(create_app(fake))
    info = _session(client, max_leverage=None)
    acct = Broker(client, info["session_id"]).ok(Broker(client, info["session_id"]).get("/account"))
    assert "buying_power" not in acct and "multiplier" not in acct


def test_clock_is_simulated_time(setup):
    client, info, b = setup
    c = b.ok(b.get("/clock"))
    assert c == {"timestamp": "2000-01-03T09:30:00-05:00", "is_open": True,
                 "next_open": "2000-01-04T09:30:00-05:00",
                 "next_close": "2000-01-03T16:00:00-05:00"}
    b.ok(b.post("/tradefloor/advance", {"until": "close"}))
    c = b.ok(b.get("/clock"))
    assert c["is_open"] is False and c["timestamp"] == "2000-01-03T16:00:00-05:00"
    assert c["next_open"] == "2000-01-04T09:30:00-05:00"


def test_simulated_dates_are_weekdays_from_the_epoch():
    assert [sim_date(d).isoformat() for d in (0, 4, 5, 9, 10)] == [
        "2000-01-03", "2000-01-07", "2000-01-10", "2000-01-14", "2000-01-17"]
    assert all(sim_date(d).weekday() < 5 for d in range(40))
    assert sim_datetime(2, 390).isoformat() == "2000-01-05T16:00:00-05:00"


def test_calendar(setup):
    _, _, b = setup
    days = b.ok(b.get("/calendar", start="2000-01-01", end="2000-01-11"))
    assert [d["date"] for d in days] == ["2000-01-03", "2000-01-04", "2000-01-05", "2000-01-06",
                                         "2000-01-07", "2000-01-10", "2000-01-11"]
    assert days[0]["open"] == "09:30" and days[0]["close"] == "16:00"
    assert len(b.ok(b.get("/calendar"))) > 200
    _err(b.get("/calendar", start="yesterday"), 400, "invalid_request")


def test_assets_are_the_roster(setup):
    _, info, b = setup
    assets = b.ok(b.get("/assets"))
    assert [a["symbol"] for a in assets] == info["tickers"]
    a = assets[0]
    assert a["class"] == "us_equity" and a["tradable"] and a["shortable"] and _uuid(a["id"])
    assert b.ok(b.get(f"/assets/{a['symbol']}")) == a
    assert b.ok(b.get(f"/assets/{a['id']}")) == a
    assert b.ok(b.get("/assets", status="inactive")) == []
    _err(b.get("/assets/NOPE"), 404, "not_found")


# -- orders ------------------------------------------------------------------------


def test_a_market_order_round_trip(setup):
    _, info, b = setup
    t = info["tickers"][0]
    o = b.ok(b.post("/orders", {"symbol": t, "qty": "10", "side": "buy", "type": "market",
                                "time_in_force": "day"}))
    assert _uuid(o["id"]) and _uuid(o["asset_id"]) and o["client_order_id"]
    assert (o["status"], o["qty"], o["filled_qty"], o["filled_avg_price"]) == ("new", "10.0", "0.0", None)
    assert o["order_class"] == "simple" and o["type"] == o["order_type"] == "market"
    assert o["submitted_at"] == "2000-01-03T14:30:00Z"
    assert o["expires_at"] == "2000-01-03T21:00:00Z"
    adv = b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    assert [f["order_id"] for f in adv["fills"]] == [o["id"]]
    got = b.ok(b.get(f"/orders/{o['id']}"))
    assert got["status"] == "filled" and got["filled_qty"] == "10.0"
    assert float(got["filled_avg_price"]) > 0 and got["filled_at"]
    assert b.ok(b.get("/orders:by_client_order_id", client_order_id=o["client_order_id"]))["id"] == o["id"]


def test_client_order_id_round_trips_and_is_idempotent(setup):
    _, info, b = setup
    body = {"symbol": info["tickers"][1], "qty": 2, "side": "sell", "type": "market",
            "time_in_force": "gtc", "client_order_id": "bot-7"}
    first, again = b.ok(b.post("/orders", body)), b.ok(b.post("/orders", body))
    assert first["id"] == again["id"] and first["client_order_id"] == "bot-7"
    assert b.ok(b.get("/orders:by_client_order_id", client_order_id="bot-7"))["id"] == first["id"]
    _err(b.post("/orders", {**body, "qty": 3}), 409, "conflict")
    _err(b.get("/orders:by_client_order_id", client_order_id="nope"), 404, "not_found")


def test_limit_orders_rest_and_cancel(setup):
    client, info, b = setup
    t = info["tickers"][0]
    last = client.get(f"/v1/sessions/{info['session_id']}/observation").json()["quotes"][0]["last"]
    o = b.ok(b.post("/orders", {"symbol": t, "qty": "5", "side": "buy", "type": "limit",
                                "limit_price": str(round(last * 0.5, 2)), "time_in_force": "gtc"}))
    assert o["limit_price"] == repr(round(last * 0.5, 2)) and o["expires_at"] is None
    b.ok(b.post("/tradefloor/advance", {"steps": 2}))
    assert [x["id"] for x in b.ok(b.get("/orders"))] == [o["id"]]           # default: open
    r = b.delete(f"/orders/{o['id']}")
    assert r.status_code == 204 and r.content == b""
    canceled = b.ok(b.get(f"/orders/{o['id']}"))
    assert canceled["status"] == "canceled"
    assert canceled["canceled_at"] == canceled["updated_at"] == "2000-01-03T15:30:00Z"
    assert b.ok(b.get("/orders")) == []
    assert [x["status"] for x in b.ok(b.get("/orders", status="closed"))] == ["canceled"]
    _err(b.get(f"/orders/{uuid.uuid4()}"), 404, "not_found")


def test_a_day_order_shows_its_expiry(setup):
    client, info, b = setup
    last = client.get(f"/v1/sessions/{info['session_id']}/observation").json()["quotes"][0]["last"]
    o = b.ok(b.post("/orders", {"symbol": info["tickers"][0], "qty": "1", "side": "buy",
                                "type": "limit", "limit_price": round(last * 0.5, 2),
                                "time_in_force": "day"}))
    adv = b.ok(b.post("/tradefloor/advance", {"until": "close"}))
    assert [x["id"] for x in adv["expired"]] == [o["id"]]
    got = b.ok(b.get(f"/orders/{o['id']}"))
    assert got["status"] == "expired" and got["expired_at"] == "2000-01-03T21:00:00Z"
    assert got["updated_at"] == got["expired_at"] and got["canceled_at"] is None


def test_listing_orders_filters_like_alpaca(setup):
    _, info, b = setup
    t0, t1 = info["tickers"][:2]
    ids = []
    for t in (t0, t1, t0):
        ids.append(b.ok(b.post("/orders", {"symbol": t, "qty": 1, "side": "buy",
                                           "time_in_force": "day"}))["id"])
        b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    every = b.ok(b.get("/orders", status="all"))
    assert [o["id"] for o in every] == ids[::-1]                            # newest first
    assert [o["id"] for o in b.ok(b.get("/orders", status="all", direction="asc"))] == ids
    assert [o["id"] for o in b.ok(b.get("/orders", status="all", limit=2))] == ids[:0:-1]
    assert {o["symbol"] for o in b.ok(b.get("/orders", status="all", symbols=t1))} == {t1}
    after = b.ok(b.get("/orders", status="all", after="2000-01-03T14:30:00Z"))
    assert [o["id"] for o in after] == ids[:0:-1]
    until = b.ok(b.get("/orders", status="all", until="2000-01-03T14:30:00Z"))
    assert [o["id"] for o in until] == ids[:1]
    assert b.ok(b.get("/orders", status="all", side="sell")) == []
    _err(b.get("/orders", status="pending"), 400, "invalid_request")
    _err(b.get("/orders", status="all", after="not-a-time"), 400, "invalid_request")


def test_cancel_all_is_multi_status(setup):
    client, info, b = setup
    last = client.get(f"/v1/sessions/{info['session_id']}/observation").json()["quotes"][0]["last"]
    for _ in range(2):
        b.ok(b.post("/orders", {"symbol": info["tickers"][0], "qty": 1, "side": "buy",
                                "type": "limit", "limit_price": round(last * 0.5, 2),
                                "time_in_force": "gtc"}))
    r = b.delete("/orders")
    assert r.status_code == 207
    rows = r.json()
    assert len(rows) == 2 and all(row["status"] == 200 for row in rows)
    assert all(row["body"]["status"] == "canceled" for row in rows)
    assert b.ok(b.get("/orders")) == []


@pytest.mark.parametrize("extra, word", [
    ({"notional": "100"}, "notional"),
    ({"type": "stop", "stop_price": "10"}, "stop"),
    ({"type": "stop_limit", "limit_price": "10", "stop_price": "9"}, "stop"),
    ({"type": "trailing_stop", "trail_percent": "1"}, "trailing"),
    ({"order_class": "bracket", "take_profit": {"limit_price": "200"}}, "bracket"),
    ({"order_class": "oco"}, "oco"),
    ({"time_in_force": "ioc"}, "ioc"),
    ({"time_in_force": "opg"}, "opg"),
    ({"extended_hours": True}, "extended_hours"),
    ({"side": "short"}, "side"),
    ({"qty": "ten"}, "qty"),
], ids=lambda v: v if isinstance(v, str) else None)
def test_unsupported_orders_are_refused_naming_what_is_supported(setup, extra, word):
    _, info, b = setup
    body = {"symbol": info["tickers"][0], "qty": "1", "side": "buy", "type": "market",
            "time_in_force": "day", **extra}
    if "notional" in extra:
        body.pop("qty")
    message = _err(b.post("/orders", body), 400, "invalid_order")
    assert word in message
    assert b.ok(b.get("/orders", status="all")) == []


def test_an_unknown_symbol_is_invalid_order(setup):
    _, _, b = setup
    _err(b.post("/orders", {"symbol": "NOPE", "qty": 1, "side": "buy", "type": "market",
                            "time_in_force": "day"}), 400, "invalid_order")


# -- positions -----------------------------------------------------------------------


def _buy_and_fill(b, symbol, qty, side="buy"):
    o = b.ok(b.post("/orders", {"symbol": symbol, "qty": qty, "side": side, "type": "market",
                                "time_in_force": "day"}))
    b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    return o


def test_positions(setup):
    client, info, b = setup
    t0, t1 = info["tickers"][:2]
    _buy_and_fill(b, t0, 10)
    _buy_and_fill(b, t1, 4, side="sell")
    positions = {p["symbol"]: p for p in b.ok(b.get("/positions"))}
    long, short = positions[t0], positions[t1]
    assert (long["side"], long["qty"], short["side"], short["qty"]) == ("long", "10.0", "short", "-4.0")
    native = {p["ticker"]: p for p in client.get(
        f"/v1/sessions/{info['session_id']}/observation").json()["positions"]}
    assert float(long["market_value"]) == native[t0]["market_value"]
    assert float(long["avg_entry_price"]) == native[t0]["avg_price"]
    assert float(long["unrealized_pl"]) == native[t0]["unrealised_pnl"]
    assert float(long["cost_basis"]) == pytest.approx(10 * native[t0]["avg_price"])
    assert _uuid(long["asset_id"]) and long["asset_class"] == "us_equity"
    assert b.ok(b.get(f"/positions/{t0}")) == long
    assert b.ok(b.get(f"/positions/{long['asset_id']}")) == long
    _err(b.get(f"/positions/{info['tickers'][2]}"), 404, "not_found")


def test_closing_positions_places_market_orders(setup):
    _, info, b = setup
    t0, t1, t2 = info["tickers"][:3]
    _buy_and_fill(b, t0, 10)
    _buy_and_fill(b, t1, 6, side="sell")
    _buy_and_fill(b, t2, 8)
    half = b.ok(b.delete(f"/positions/{t2}", percentage="50"))
    assert (half["side"], half["qty"], half["type"]) == ("sell", "4.0", "market")
    b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    assert b.ok(b.get(f"/positions/{t2}"))["qty"] == "4.0"
    close = b.ok(b.delete(f"/positions/{t0}"))
    assert (close["side"], close["qty"]) == ("sell", "10.0")
    r = b.delete("/positions", cancel_orders="true")
    assert r.status_code == 207
    rows = {row["symbol"]: row for row in r.json()}
    assert rows[t1]["status"] == 200 and rows[t1]["body"]["side"] == "buy"
    b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    assert b.ok(b.get("/positions")) == []
    _err(b.delete(f"/positions/{t0}"), 404, "not_found")
    _err(b.delete(f"/positions/{t0}", qty="1", percentage="5"), 404, "not_found")


# -- activities and market data -------------------------------------------------------


def test_fill_activities(setup):
    _, info, b = setup
    o = _buy_and_fill(b, info["tickers"][0], 3)
    acts = b.ok(b.get("/account/activities/FILL"))
    assert len(acts) == 1
    a = acts[0]
    assert a["activity_type"] == "FILL" and a["type"] == "fill" and a["order_id"] == o["id"]
    assert (a["qty"], a["cum_qty"], a["leaves_qty"], a["side"]) == ("3.0", "3.0", "0.0", "buy")
    assert "::" in a["id"] and a["order_status"] == "filled"
    assert b.ok(b.get("/account/activities", activity_types="FILL")) == acts
    assert b.ok(b.get("/account/activities/DIV")) == []
    _buy_and_fill(b, info["tickers"][1], 1)
    newest, oldest = b.ok(b.get("/account/activities/FILL"))
    assert b.ok(b.get("/account/activities/FILL", page_size=1)) == [newest]
    assert b.ok(b.get("/account/activities/FILL", page_size=1, page_token=newest["id"])) == [oldest]


def test_market_data_latest_and_snapshots(setup):
    client, info, b = setup
    t0, t1 = info["tickers"][:2]
    b.ok(b.post("/tradefloor/advance", {"steps": 2}))
    q = {x["ticker"]: x for x in client.get(
        f"/v1/sessions/{info['session_id']}/observation").json()["quotes"]}
    trades = b.ok(b.get("/stocks/trades/latest", symbols=f"{t0},{t1},NOPE"))["trades"]
    assert set(trades) == {t0, t1} and trades[t0]["p"] == q[t0]["last"]
    assert trades[t0]["t"] == "2000-01-03T15:30:00Z"
    quotes = b.ok(b.get("/stocks/quotes/latest", symbols=t0))["quotes"]
    assert quotes[t0]["bp"] == q[t0]["bid"] and quotes[t0]["ap"] == q[t0]["ask"]
    snap = b.ok(b.get("/stocks/snapshots", symbols=t0))[t0]
    bar = snap["dailyBar"]
    assert (bar["o"], bar["h"], bar["l"], bar["c"], bar["v"]) == (
        q[t0]["day_open"], q[t0]["day_high"], q[t0]["day_low"], q[t0]["last"], q[t0]["volume"])
    assert b.ok(b.get(f"/stocks/{t0}/trades/latest"))["trade"] == trades[t0]
    assert b.ok(b.get(f"/stocks/{t0}/quotes/latest"))["symbol"] == t0
    assert b.ok(b.get(f"/stocks/{t0}/snapshot"))["dailyBar"] == bar
    _err(b.get("/stocks/NOPE/trades/latest"), 404, "not_found")
    _err(b.get("/stocks/trades/latest"), 400, "invalid_request")


# -- errors and the session boundary ------------------------------------------------------


def test_the_facade_uses_the_contract_errors(setup, fake):
    client, info, b = setup
    _err(Broker(client, "0" * 32).get("/account"), 404, "not_found")
    client.post(f"/v1/sessions/{info['session_id']}/close")
    acct = b.ok(b.get("/account"))
    assert acct["trading_blocked"] is True and acct["buying_power"] == "0.0"
    assert b.ok(b.get("/clock"))["is_open"] is False
    _err(b.post("/orders", {"symbol": info["tickers"][0], "qty": 1, "side": "buy",
                            "time_in_force": "day"}), 409, "session_closed")
    _err(b.post("/tradefloor/advance", {"steps": 1}), 409, "session_closed")


def test_the_advance_extension_validates_its_body(setup):
    _, _, b = setup
    _err(b.post("/tradefloor/advance", {"steps": 0}), 400, "invalid_request")
    _err(b.post("/tradefloor/advance", {"until": "later"}), 400, "invalid_request")
    _err(b.post("/tradefloor/advance", {"days": 1}), 400, "invalid_request")
    assert b.ok(b.post("/tradefloor/advance"))["clock"]["timestamp"] == "2000-01-03T10:00:00-05:00"


def test_uuid_order_ids_are_shown_as_themselves():
    fake = FakeSessionService(uuid_order_ids=True)
    client = TestClient(create_app(fake))
    info = _session(client)
    b = Broker(client, info["session_id"])
    o = b.ok(b.post("/orders", {"symbol": info["tickers"][0], "qty": 1, "side": "buy",
                                "time_in_force": "day"}))
    native = client.get(f"/v1/sessions/{info['session_id']}/orders").json()[0]
    assert o["id"] == str(uuid.UUID(native["order_id"]))
    assert b.ok(b.get(f"/orders/{native['order_id']}"))["id"] == o["id"]


# -- a bot, against the fake and the real core ----------------------------------------------


@pytest.mark.parametrize("kind", ["fake", pytest.param("core", marks=CORE)])
def test_a_bot_runs_its_loop_against_the_facade(kind, tmp_path):
    """The loop a simple momentum bot runs, in Alpaca's paths and fields."""
    client = TestClient(create_app(make_service(kind, tmp_path / "sessions")))
    info = _session(client)
    b = Broker(client, info["session_id"])
    symbols = [a["symbol"] for a in b.ok(b.get("/assets"))]
    start = float(b.ok(b.get("/account"))["equity"])
    last_price = {}
    for _ in range(6):
        clock = b.ok(b.get("/clock"))
        if not clock["is_open"]:
            b.ok(b.post("/tradefloor/advance", {"until": "next_open"}))
            continue
        trades = b.ok(b.get("/stocks/trades/latest", symbols=",".join(symbols)))["trades"]
        held = {p["symbol"]: float(p["qty"]) for p in b.ok(b.get("/positions"))}
        for s in symbols:
            price = trades[s]["p"]
            if s in last_price and price > last_price[s] and held.get(s, 0) <= 0:
                b.ok(b.post("/orders", {"symbol": s, "qty": "5", "side": "buy",
                                        "type": "market", "time_in_force": "day"}))
            elif s in last_price and price < last_price[s] and held.get(s, 0) > 0:
                b.ok(b.delete(f"/positions/{s}"))
            last_price[s] = price
        b.ok(b.post("/tradefloor/advance", {"steps": 1}))    # where a live bot would sleep
    fills = b.ok(b.get("/account/activities/FILL"))
    acct = b.ok(b.get("/account"))
    assert float(acct["equity"]) != start or not fills
    for o in b.ok(b.get("/orders", status="all")):
        assert o["status"] in {"new", "filled", "canceled", "expired", "rejected"}
    r = b.delete("/positions", cancel_orders="true")
    assert r.status_code == 207


# -- the real Alpaca SDK, against a live server -------------------------------------------------


@contextlib.contextmanager
def _live(app):
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", ws="none"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(10)
        sock.close()


@pytest.mark.parametrize("kind", ["fake", pytest.param("core", marks=[CORE, CORE04])])
def test_the_alpaca_sdk_drives_a_session_by_changing_its_base_url(kind, tmp_path):
    pytest.importorskip("alpaca", reason="alpaca-py is not installed")
    from alpaca.common.exceptions import APIError
    from alpaca.data.historical import NewsClient, StockHistoricalDataClient
    from alpaca.data.requests import (
        NewsRequest,
        StockBarsRequest,
        StockLatestQuoteRequest,
        StockLatestTradeRequest,
        StockSnapshotRequest,
    )
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    from alpaca.trading.client import TradingClient
    from alpaca.trading.enums import OrderSide, OrderStatus, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import (
        ClosePositionRequest,
        GetCalendarRequest,
        GetOrdersRequest,
        LimitOrderRequest,
        MarketOrderRequest,
    )

    app = create_app(make_service(kind, tmp_path / "sessions"))
    with _live(app) as base:
        info = TestClient(app).post("/v1/sessions", json=SMALL).json()
        sid, t0, t1 = info["session_id"], info["tickers"][0], info["tickers"][1]
        url = f"{base}/broker/{sid}"                       # the ONLY change a bot makes
        tc = TradingClient("any-key", "any-secret", url_override=url)
        data = StockHistoricalDataClient("any-key", "any-secret", url_override=url)

        acct = tc.get_account()
        assert float(acct.equity) == info["config"]["cash"]
        assert tc.get_clock().is_open is True
        assert [a.symbol for a in tc.get_all_assets()] == info["tickers"]
        assert tc.get_asset(t0).symbol == t0
        days = tc.get_calendar(GetCalendarRequest(start=date(2000, 1, 3), end=date(2000, 1, 7)))
        assert [d.date.isoformat() for d in days] == [f"2000-01-0{i}" for i in range(3, 8)]

        trade = data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=[t0, t1]))
        assert set(trade) == {t0, t1} and trade[t0].price > 0
        quote = data.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=t0))
        assert quote[t0].ask_price >= quote[t0].bid_price > 0
        snap = data.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=[t0]))
        assert snap[t0].daily_bar.close == trade[t0].price

        order = tc.submit_order(MarketOrderRequest(symbol=t0, qty=10, side=OrderSide.BUY,
                                                   time_in_force=TimeInForce.DAY))
        assert order.status == OrderStatus.NEW and isinstance(order.id, uuid.UUID)
        advanced = tc.post("/tradefloor/advance", {"steps": 1})
        assert advanced["fills"][0]["order_id"] == str(order.id)
        filled = tc.get_order_by_id(order.id)
        assert filled.status == OrderStatus.FILLED and float(filled.filled_qty) == 10
        assert isinstance(filled.filled_at, datetime)
        pos = tc.get_open_position(t0)
        assert float(pos.qty) == 10 and pos.side.value == "long"
        assert [p.symbol for p in tc.get_all_positions()] == [t0]

        limit = round(trade[t1].price * 0.5, 2)
        resting = tc.submit_order(LimitOrderRequest(symbol=t1, qty=3, side=OrderSide.BUY,
                                                    time_in_force=TimeInForce.GTC,
                                                    limit_price=limit, client_order_id="sdk-1"))
        assert tc.get_order_by_client_id("sdk-1").id == resting.id
        assert [o.id for o in tc.get_orders()] == [resting.id]
        tc.cancel_order_by_id(resting.id)
        assert tc.get_order_by_id(resting.id).status == OrderStatus.CANCELED
        every = tc.get_orders(GetOrdersRequest(status=QueryOrderStatus.ALL))
        assert {o.id for o in every} == {order.id, resting.id}

        half = tc.close_position(t0, ClosePositionRequest(percentage="50"))
        assert half.side == OrderSide.SELL and float(half.qty) == 5
        tc.post("/tradefloor/advance", {"steps": 1})
        cancelled = tc.cancel_orders()
        assert cancelled == []
        closed = tc.close_all_positions(cancel_orders=True)
        assert [c.symbol for c in closed] == [t0] and closed[0].status == 200

        with pytest.raises(APIError) as refused:
            tc.submit_order(MarketOrderRequest(symbol="NOPE", qty=1, side=OrderSide.BUY,
                                               time_in_force=TimeInForce.DAY))
        assert refused.value.status_code == 400 and refused.value.code == "invalid_order"
        with pytest.raises(APIError) as unsupported:
            tc.submit_order(MarketOrderRequest(symbol=t0, qty=1, side=OrderSide.BUY,
                                               time_in_force=TimeInForce.IOC))
        assert "ioc" in unsupported.value.message

        # History and news: bars by day and by step, after a day has closed.
        tc.post("/tradefloor/advance", {"until": "next_open"})
        tc.post("/tradefloor/advance", {"steps": 2})
        daily = data.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=[t0, t1], timeframe=TimeFrame.Day, start=datetime(2000, 1, 1)))
        assert [b.timestamp.date().isoformat() for b in daily[t0]] == ["2000-01-03", "2000-01-04"]
        assert daily[t1][-1].close == data.get_stock_latest_trade(
            StockLatestTradeRequest(symbol_or_symbols=t1))[t1].price
        steps = data.get_stock_bars(StockBarsRequest(
            symbol_or_symbols=t0, timeframe=TimeFrame(30, TimeFrameUnit.Minute)))
        assert [b.timestamp.isoformat() for b in steps[t0]] == [
            "2000-01-04T14:30:00+00:00", "2000-01-04T15:00:00+00:00"]    # today only, by default
        snap = data.get_stock_snapshot(StockSnapshotRequest(symbol_or_symbols=t0))[t0]
        assert snap.previous_daily_bar.close == daily[t0][0].close
        with pytest.raises(APIError):
            data.get_stock_bars(StockBarsRequest(symbol_or_symbols=t0,
                                                 timeframe=TimeFrame(5, TimeFrameUnit.Minute)))
        news = NewsClient("any-key", "any-secret", url_override=url).get_news(NewsRequest(limit=50))
        headlines = TestClient(app).get(f"/v1/sessions/{sid}/news").json()
        assert [item.headline for item in news.data["news"]] == [h["text"] for h in headlines][::-1]
        if kind == "fake":
            assert len(headlines) == 2


# -- contract 0.2 and 0.3 in the facade ---------------------------------------------------


def test_a_partial_fill_is_a_canceled_order_with_its_filled_quantity(fake):
    client = TestClient(create_app(fake))
    info = _session(client, cash=1e9, max_leverage=None)
    b = Broker(client, info["session_id"])
    o = b.ok(b.post("/orders", {"symbol": info["tickers"][0], "qty": "300000", "side": "buy",
                                "type": "market", "time_in_force": "day"}))
    adv = b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    got = b.ok(b.get(f"/orders/{o['id']}"))
    assert (got["status"], got["qty"], got["filled_qty"]) == ("canceled", "300000.0", "200000.0")
    assert got["filled_at"] == got["canceled_at"] == "2000-01-03T14:30:00Z"
    assert got in b.ok(b.get("/orders", status="closed")) and b.ok(b.get("/orders")) == []
    fill = adv["fills"][0]
    assert (fill["type"], fill["qty"], fill["leaves_qty"], fill["order_status"]) == (
        "partial_fill", "200000.0", "100000.0", "partially_filled")
    assert b.ok(b.get("/account/activities/FILL")) == [fill]
    assert b.ok(b.get(f"/positions/{info['tickers'][0]}"))["qty"] == "200000.0"


def test_a_rejected_order_has_failed_at(fake):
    class Rejecting(FakeSessionService):
        def _within_leverage(self, s, ticker, side, qty, price):
            return not s.queued  # accept at submission, refuse at fill
    client = TestClient(create_app(Rejecting()))
    info = _session(client)
    b = Broker(client, info["session_id"])
    o = b.ok(b.post("/orders", {"symbol": info["tickers"][0], "qty": "1", "side": "buy",
                                "time_in_force": "day"}))
    b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    got = b.ok(b.get(f"/orders/{o['id']}"))
    assert got["status"] == "rejected" and got["failed_at"] == got["updated_at"]
    assert got["filled_at"] is None and got["canceled_at"] is None


def test_an_insolvent_account_has_no_buying_power(fake):
    class Insolvent(FakeSessionService):
        def _account(self, s):
            a = super()._account(s)
            a.cash, a.net_worth, a.leverage, a.insolvent, a.gross_exposure = -5.0, -5.0, None, True, 0.0
            return a
    client = TestClient(create_app(Insolvent()))
    info = _session(client)
    acct = Broker(client, info["session_id"]).ok(Broker(client, info["session_id"]).get("/account"))
    assert acct["equity"] == "-5.0" and acct["buying_power"] == "0.0"


def test_latest_trade_size_is_the_last_step_volume(setup):
    client, info, b = setup
    t0 = info["tickers"][0]
    assert b.ok(b.get(f"/stocks/{t0}/trades/latest"))["trade"]["s"] == 0    # no step yet today
    b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    q = next(x for x in client.get(f"/v1/sessions/{info['session_id']}/observation").json()["quotes"]
             if x["ticker"] == t0)
    assert b.ok(b.get(f"/stocks/{t0}/trades/latest"))["trade"]["s"] == q["step_volume"] > 0


def test_bars_by_day_and_by_step(setup):
    client, info, b = setup
    t0, t1 = info["tickers"][:2]
    b.ok(b.post("/tradefloor/advance", {"until": "next_open"}))
    b.ok(b.post("/tradefloor/advance", {"steps": 3}))
    native_days = client.get(f"/v1/sessions/{info['session_id']}/bars/{t0}").json()
    days = b.ok(b.get("/stocks/bars", symbols=f"{t0},{t1},NOPE", timeframe="1Day", start="2000-01-01"))
    assert days["next_page_token"] is None and set(days["bars"]) == {t0, t1}
    assert [(x["t"], x["o"], x["h"], x["l"], x["c"], x["v"]) for x in days["bars"][t0]] == [
        ("2000-01-03T05:00:00Z", n["open"], n["high"], n["low"], n["close"], n["volume"])
        if n["day"] == 0 else
        ("2000-01-04T05:00:00Z", n["open"], n["high"], n["low"], n["close"], n["volume"])
        for n in native_days]
    # Alpaca's default window is the current day.
    assert [x["t"] for x in b.ok(b.get("/stocks/bars", symbols=t0, timeframe="1Day"))["bars"][t0]] == [
        "2000-01-04T05:00:00Z"]
    steps = b.ok(b.get(f"/stocks/{t0}/bars", timeframe="30Min"))
    assert steps["symbol"] == t0
    assert [x["t"] for x in steps["bars"]] == [
        "2000-01-04T14:30:00Z", "2000-01-04T15:00:00Z", "2000-01-04T15:30:00Z"]
    assert b.ok(b.get(f"/stocks/{t0}/bars", timeframe="30T"))["bars"] == steps["bars"]
    every = b.ok(b.get(f"/stocks/{t0}/bars", timeframe="30Min", start="2000-01-03"))["bars"]
    assert len(every) == 13 + 3
    desc = b.ok(b.get(f"/stocks/{t0}/bars", timeframe="30Min", start="2000-01-03", sort="desc"))["bars"]
    assert desc == every[::-1]
    window = b.ok(b.get(f"/stocks/{t0}/bars", timeframe="30Min", start="2000-01-03T15:00:00Z",
                        end="2000-01-03T16:00:00Z"))["bars"]
    assert [x["t"] for x in window] == ["2000-01-03T15:00:00Z", "2000-01-03T15:30:00Z",
                                        "2000-01-03T16:00:00Z"]
    page1 = b.ok(b.get(f"/stocks/{t0}/bars", timeframe="30Min", start="2000-01-03", limit=10))
    assert page1["bars"] == every[:10] and page1["next_page_token"]
    page2 = b.ok(b.get(f"/stocks/{t0}/bars", timeframe="30Min", start="2000-01-03", limit=10,
                       page_token=page1["next_page_token"]))
    assert page2["bars"] == every[10:] and page2["next_page_token"] is None
    for tf in ("1Min", "1Hour", "5Min", "1Week"):
        assert "30Min" in _err(b.get(f"/stocks/{t0}/bars", timeframe=tf), 400, "invalid_request")
    _err(b.get("/stocks/NOPE/bars", timeframe="1Day"), 404, "not_found")
    _err(b.get(f"/stocks/{t0}/bars", timeframe="1Day", sort="up"), 400, "invalid_request")


def test_snapshots_carry_the_previous_day(setup):
    client, info, b = setup
    t0 = info["tickers"][0]
    assert "prevDailyBar" not in b.ok(b.get(f"/stocks/{t0}/snapshot"))       # day 0 has none
    b.ok(b.post("/tradefloor/advance", {"until": "next_open"}))
    prev = client.get(f"/v1/sessions/{info['session_id']}/bars/{t0}").json()[0]
    snap = b.ok(b.get("/stocks/snapshots", symbols=t0))[t0]
    assert snap["prevDailyBar"]["c"] == prev["close"] and snap["prevDailyBar"]["t"] == "2000-01-03T05:00:00Z"
    assert snap["dailyBar"]["o"] == prev["close"]


def test_news_is_the_sessions_news_log():
    client = TestClient(create_app(FakeSessionService(headlines=True)))
    info = _session(client)
    sid = info["session_id"]
    b = Broker(client, sid)
    url = f"/broker/{sid}/v1beta1/news"
    assert client.get(url).json() == {"news": [], "next_page_token": None}     # tick 0: not out yet
    adv = b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    headline = client.get(f"/v1/sessions/{sid}/observation").json()["news"][0]
    assert [n["headline"] for n in adv["news"]] == [headline["text"]]
    news = client.get(url).json()["news"]
    assert len(news) == 1
    item = news[0]
    assert item["headline"] == headline["text"] and item["symbols"] == headline["tickers"]
    assert item["created_at"] == "2000-01-03T14:31:00Z" and isinstance(item["id"], int)
    assert client.get(url, params={"symbols": headline["tickers"][0]}).json()["news"] == news
    other = next(t for t in info["tickers"] if t not in headline["tickers"])
    assert client.get(url, params={"symbols": other}).json()["news"] == []
    assert client.get(url, params={"start": "2000-01-03T15:00:00Z"}).json()["news"] == []
    # Later days add to the log; the facade pages through it newest first.
    b.ok(b.post("/tradefloor/advance", {"steps": 2, "until": "next_open"}))
    b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    every = client.get(url).json()["news"]
    assert [n["created_at"][:10] for n in every] == ["2000-01-05", "2000-01-04", "2000-01-03"]
    assert every[-1] == item                                                   # ids are stable
    assert [n["headline"] for n in every] == [
        h["text"] for h in client.get(f"/v1/sessions/{sid}/news").json()][::-1]
    page = client.get(url, params={"limit": 2}).json()
    assert page["news"] == every[:2] and page["next_page_token"]
    rest = client.get(url, params={"limit": 2, "page_token": page["next_page_token"]}).json()
    assert rest == {"news": every[2:], "next_page_token": None}
    asc = client.get(url, params={"sort": "asc", "start": "2000-01-04"}).json()["news"]
    assert asc == every[:2][::-1]


# -- finding your way around the facade ------------------------------------------------------


def test_advance_is_also_answered_without_v2(setup):
    client, info, b = setup
    sid = info["session_id"]
    alias = client.post(f"/broker/{sid}/tradefloor/advance", json={"steps": 1})
    assert alias.status_code == 200
    assert alias.json()["clock"]["timestamp"] == "2000-01-03T10:00:00-05:00"
    v2 = b.ok(b.post("/tradefloor/advance", {"steps": 1}))
    assert v2["clock"]["timestamp"] == "2000-01-03T10:30:00-05:00"
    _err(client.post(f"/broker/{sid}/tradefloor/advance", json={"steps": 0}), 400, "invalid_request")
    _err(client.post(f"/broker/{'0' * 32}/tradefloor/advance", json={}), 404, "not_found")


@pytest.mark.parametrize("method, path, nearest", [
    ("GET", "/v2/positons", "GET /broker/{session_id}/v2/positions"),
    ("GET", "/account", "GET /broker/{session_id}/v2/account"),
    ("GET", "/clock", "GET /broker/{session_id}/v2/clock"),
    ("POST", "/v2/order", "POST /broker/{session_id}/v2/orders"),
    ("GET", "/v2/stocks/AAA/barz", "GET /broker/{session_id}/v2/stocks/{symbol}/bars"),
    ("GET", "/v2/news", "GET /broker/{session_id}/v1beta1/news"),
    ("POST", "/tradefloor/advanse", "/tradefloor/advance"),
])
def test_an_unknown_broker_path_names_the_nearest_route(setup, method, path, nearest):
    client, info, _ = setup
    r = client.request(method, f"/broker/{info['session_id']}{path}")
    message = _err(r, 404, "not_found")
    assert "The nearest supported route is " in message and nearest in message, message
    assert "docs/serve/TRANSPORTS.md" in message


def test_a_path_with_nothing_near_says_where_the_list_is(setup):
    client, info, _ = setup
    message = _err(client.get(f"/broker/{info['session_id']}/nothing/at/all/here"), 404, "not_found")
    assert "nearest" not in message and "/docs" in message


def test_a_wrong_method_on_a_broker_route_names_the_right_ones(setup):
    client, info, _ = setup
    sid = info["session_id"]
    message = _err(client.get(f"/broker/{sid}/v2/tradefloor/advance"), 405, "invalid_request")
    assert message.endswith("use POST.")
    message = _err(client.patch(f"/broker/{sid}/v2/orders/abc"), 405, "invalid_request")
    assert "use DELETE or GET" in message
