"""The native HTTP routes (`/v1/...`) of the trading session server.

Two kinds of test live here:

- CONTRACT tests, parametrised over the fake service and the real core
  (`LocalSessionService` over a temporary `FileStore`, skipped until the core
  layer is importable). They check that a route reaches the service and that
  the contract's semantics survive the trip: fills, idempotency, owners,
  errors and their statuses.
- TRANSPORT tests, fake only: owner resolution, validation, error mapping,
  middleware, OpenAPI, and that the optional server dependencies stay
  optional.
"""

import asyncio
import dataclasses
import subprocess
import sys

import pytest

pytest.importorskip("fastapi", reason="the HTTP server is an opt-in extra")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402

from fakes import FakeSessionService, core_available, make_service  # noqa: E402
from tradefloor.serve import types as T  # noqa: E402
from tradefloor.serve.http import (  # noqa: E402
    STATUS_BY_CODE,
    create_app,
    error_response,
    request_api_key,
)
from tradefloor.serve.types import ERROR_CODES, ServeError  # noqa: E402

CORE = pytest.mark.skipif(not core_available(), reason="tradefloor.serve.core has not landed")
SMALL = {"universe_size": 4, "ticks_per_step": 30}


@pytest.fixture(params=["fake", pytest.param("core", marks=CORE)])
def service(request, tmp_path):
    return make_service(request.param, tmp_path / "sessions")


@pytest.fixture
def client(service):
    return TestClient(create_app(service))


def _keys(cls):
    return {f.name for f in dataclasses.fields(cls)}


def _open(client, **config):
    r = client.post("/v1/sessions", json={**SMALL, **config})
    assert r.status_code == 201, r.text
    return r.json()


def _order(client, sid, **body):
    return client.post(f"/v1/sessions/{sid}/orders", json=body)


def _advance(client, sid, **body):
    r = client.post(f"/v1/sessions/{sid}/advance", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _err(r, status, code):
    assert r.status_code == status, (r.status_code, r.text)
    body = r.json()
    assert set(body) == {"code", "message"}, body
    assert body["code"] == code, body
    assert body["message"]
    return body


# -- contract: every route, against the fake and the core --------------------------


def test_open_returns_a_session_info(client):
    info = _open(client, label="first")
    assert set(info) == _keys(T.SessionInfo)
    assert set(info["config"]) == _keys(T.SessionConfig)
    assert len(info["session_id"]) == 32 and int(info["session_id"], 16) >= 0
    assert info["owner"] == "local"
    assert len(info["tickers"]) == SMALL["universe_size"]
    assert info["status"] == "open"
    assert info["config"]["label"] == "first"
    assert info["clock"]["day"] == 0 and info["clock"]["market_open"] is True


def test_open_with_no_body_uses_the_contract_defaults(client):
    r = client.post("/v1/sessions")
    assert r.status_code == 201, r.text
    assert r.json()["config"] == T.SessionConfig().to_dict()


def test_list_and_info(client):
    a, b = _open(client), _open(client)
    listed = client.get("/v1/sessions").json()
    assert {s["session_id"] for s in listed} >= {a["session_id"], b["session_id"]}
    got = client.get(f"/v1/sessions/{a['session_id']}")
    assert got.status_code == 200
    assert got.json()["session_id"] == a["session_id"]


def test_observe_shape(client):
    info = _open(client)
    obs = client.get(f"/v1/sessions/{info['session_id']}/observation").json()
    assert set(obs) == _keys(T.Observation)
    assert [q["ticker"] for q in obs["quotes"]] == info["tickers"]
    assert set(obs["quotes"][0]) == _keys(T.Quote)
    assert set(obs["account"]) == _keys(T.Account)
    assert obs["account"]["cash"] == info["config"]["cash"]
    assert obs["state_hash"]
    assert isinstance(obs["macro"], dict)


def test_a_market_order_fills_at_the_next_step(client):
    info = _open(client)
    sid, ticker = info["session_id"], info["tickers"][0]
    r = _order(client, sid, ticker=ticker, side="buy", quantity=10)
    assert r.status_code == 201, r.text
    order = r.json()
    assert set(order) == _keys(T.Order)
    assert order["status"] == "accepted"            # NOT filled when placed
    res = _advance(client, sid, steps=1)
    assert set(res) == _keys(T.AdvanceResult)
    assert [f["order_id"] for f in res["fills"]] == [order["order_id"]]
    assert set(res["fills"][0]) == _keys(T.Fill)
    assert res["fills"][0]["liquidity"] == "taker"
    got = client.get(f"/v1/sessions/{sid}/orders/{order['order_id']}").json()
    assert got["status"] == "filled" and got["filled_quantity"] == 10
    held = {p["ticker"]: p["quantity"] for p in res["observation"]["positions"]}
    assert held[ticker] == 10
    fills = client.get(f"/v1/sessions/{sid}/fills").json()
    assert [f["order_id"] for f in fills] == [order["order_id"]]
    assert client.get(f"/v1/sessions/{sid}/fills", params={"since_day": 1}).json() == []


def test_a_limit_order_rests_and_can_be_cancelled(client):
    info = _open(client)
    sid, ticker = info["session_id"], info["tickers"][0]
    last = client.get(f"/v1/sessions/{sid}/observation").json()["quotes"][0]["last"]
    order = _order(client, sid, ticker=ticker, side="buy", quantity=5, type="limit",
                   limit_price=round(last * 0.5, 2), time_in_force="gtc").json()
    _advance(client, sid, steps=1)
    open_orders = client.get(f"/v1/sessions/{sid}/orders", params={"status": "accepted"}).json()
    assert [o["order_id"] for o in open_orders] == [order["order_id"]]
    r = client.delete(f"/v1/sessions/{sid}/orders/{order['order_id']}")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    assert client.get(f"/v1/sessions/{sid}/orders", params={"status": "accepted"}).json() == []
    again = client.delete(f"/v1/sessions/{sid}/orders/{order['order_id']}")
    assert 400 <= again.status_code < 500 and set(again.json()) == {"code", "message"}


def test_a_day_order_expires_at_the_close(client):
    info = _open(client)
    sid, ticker = info["session_id"], info["tickers"][0]
    last = client.get(f"/v1/sessions/{sid}/observation").json()["quotes"][0]["last"]
    order = _order(client, sid, ticker=ticker, side="buy", quantity=5, type="limit",
                   limit_price=round(last * 0.5, 2)).json()
    res = _advance(client, sid, until="close")
    assert res["clock"]["market_open"] is False
    assert [o["order_id"] for o in res["expired"]] == [order["order_id"]]
    assert res["expired"][0]["status"] == "expired"


def test_next_open_crosses_into_the_next_day(client):
    sid = _open(client)["session_id"]
    res = _advance(client, sid, until="next_open")
    assert res["clock"]["day"] == 1


def test_client_order_id_is_an_idempotency_key(client):
    info = _open(client)
    sid, ticker = info["session_id"], info["tickers"][0]
    body = {"ticker": ticker, "side": "buy", "quantity": 3, "client_order_id": "abc"}
    first, second = _order(client, sid, **body), _order(client, sid, **body)
    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["order_id"] == second.json()["order_id"]
    assert len(client.get(f"/v1/sessions/{sid}/orders").json()) == 1
    _err(_order(client, sid, **{**body, "quantity": 4}), 409, "conflict")


@pytest.mark.parametrize("body", [
    {"ticker": "NOPE", "side": "buy", "quantity": 1},
    {"side": "buy", "quantity": 0},
    {"side": "buy", "quantity": -3},
    {"side": "buy", "quantity": 1, "type": "limit"},
    {"side": "buy", "quantity": 1, "type": "limit", "limit_price": 0},
], ids=["unknown-ticker", "zero-qty", "negative-qty", "limit-without-price", "zero-price"])
def test_bad_orders_are_invalid_order(client, body):
    info = _open(client)
    body = {"ticker": info["tickers"][0], **body}
    _err(_order(client, info["session_id"], **body), 400, "invalid_order")


def test_an_order_past_the_leverage_cap_is_refused_or_rejected(client):
    info = _open(client, max_leverage=1.0, cash=10_000.0)
    sid, ticker = info["session_id"], info["tickers"][0]
    r = _order(client, sid, ticker=ticker, side="buy", quantity=1_000_000)
    if r.status_code == 201:  # the contract allows the refusal to come at fill time
        _advance(client, sid, steps=1)
        got = client.get(f"/v1/sessions/{sid}/orders/{r.json()['order_id']}").json()
        assert got["status"] == "rejected" and got["reason"]
    else:
        _err(r, 403, "insufficient_buying_power")


def test_unknown_session_and_order_are_not_found(client):
    _err(client.get("/v1/sessions/0123456789abcdef0123456789abcdef"), 404, "not_found")
    _err(client.get("/v1/sessions/nope/observation"), 404, "not_found")
    sid = _open(client)["session_id"]
    _err(client.get(f"/v1/sessions/{sid}/orders/nope"), 404, "not_found")
    _err(client.delete(f"/v1/sessions/{sid}/orders/nope"), 404, "not_found")


def test_advance_past_the_contract_limit_is_invalid_request(client):
    sid = _open(client, ticks_per_step=390)["session_id"]
    _err(client.post(f"/v1/sessions/{sid}/advance", json={"steps": 21}), 400, "invalid_request")


def test_a_closed_session_reports_and_refuses(client):
    info = _open(client)
    sid, ticker = info["session_id"], info["tickers"][0]
    r = client.post(f"/v1/sessions/{sid}/close")
    assert r.status_code == 200, r.text
    report = r.json()
    assert set(report) == _keys(T.SessionReport)
    assert report["caveats"] and all(isinstance(c, str) and c for c in report["caveats"])
    assert client.get(f"/v1/sessions/{sid}").json()["status"] == "closed"
    _err(_order(client, sid, ticker=ticker, side="buy", quantity=1), 409, "session_closed")
    _err(client.post(f"/v1/sessions/{sid}/advance", json={}), 409, "session_closed")
    _err(client.post(f"/v1/sessions/{sid}/close"), 409, "session_closed")


def test_fork_copies_state_and_evolves_deterministically(client):
    info = _open(client)
    sid, ticker = info["session_id"], info["tickers"][0]
    _order(client, sid, ticker=ticker, side="buy", quantity=7)
    _advance(client, sid, steps=2)
    r = client.post(f"/v1/sessions/{sid}/fork", json={"label": "branch"})
    assert r.status_code == 201, r.text
    fork = r.json()
    assert fork["parent_session_id"] == sid and fork["session_id"] != sid
    parent_obs = client.get(f"/v1/sessions/{sid}/observation").json()
    fork_obs = client.get(f"/v1/sessions/{fork['session_id']}/observation").json()
    assert fork_obs["account"] == parent_obs["account"]
    assert fork_obs["positions"] == parent_obs["positions"]
    a = _advance(client, sid, steps=3)
    b = _advance(client, fork["session_id"], steps=3)
    assert a["observation"]["state_hash"] == b["observation"]["state_hash"]
    assert a["observation"]["quotes"] == b["observation"]["quotes"]


def test_the_same_calls_give_the_same_state_hash(client):
    hashes = []
    for _ in range(2):
        info = _open(client, seed=5)
        sid = info["session_id"]
        _order(client, sid, ticker=info["tickers"][1], side="sell", quantity=4)
        hashes.append(_advance(client, sid, steps=3)["observation"]["state_hash"])
    assert hashes[0] == hashes[1]


def test_another_owners_session_is_indistinguishable_from_a_missing_one(service):
    def by_header(request):
        return request.headers.get("x-owner", "local")
    client = TestClient(create_app(service, owner_resolver=by_header))
    r = client.post("/v1/sessions", json=SMALL, headers={"x-owner": "alice"})
    sid = r.json()["session_id"]
    assert r.json()["owner"] == "alice"
    missing = client.get("/v1/sessions/0123456789abcdef0123456789abcdef", headers={"x-owner": "bob"})
    theirs = client.get(f"/v1/sessions/{sid}", headers={"x-owner": "bob"})
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json()["code"] == missing.json()["code"] == "not_found"
    assert client.get("/v1/sessions", headers={"x-owner": "bob"}).json() == []
    assert client.get(f"/v1/sessions/{sid}", headers={"x-owner": "alice"}).status_code == 200


# -- transport: fake only ------------------------------------------------------------


@pytest.fixture
def fake():
    return FakeSessionService()


def test_the_default_owner_is_local(fake):
    client = TestClient(create_app(fake))
    client.post("/v1/sessions", json=SMALL)
    client.get("/v1/sessions")
    assert {owner for _, owner, _ in fake.calls} == {"local"}


def test_an_async_owner_resolver_is_awaited(fake):
    async def resolver(request):
        await asyncio.sleep(0)
        return "async-owner"
    client = TestClient(create_app(fake, owner_resolver=resolver))
    assert client.post("/v1/sessions", json=SMALL).json()["owner"] == "async-owner"


def test_a_resolver_that_refuses_is_401_with_a_challenge(fake):
    def resolver(request):
        key = request_api_key(request)
        if key != "good":
            raise ServeError("unauthorized", "missing or unknown API key")
        return "keyholder"
    client = TestClient(create_app(fake, owner_resolver=resolver))
    r = client.get("/v1/sessions")
    _err(r, 401, "unauthorized")
    assert r.headers["www-authenticate"] == "Bearer"
    ok = client.get("/v1/sessions", headers={"Authorization": "Bearer good"})
    assert ok.status_code == 200
    assert fake.calls == [("list", "keyholder", None)]  # the refused call never reached the service


def test_rate_limits_are_429_with_retry_after(fake):
    def resolver(request):
        exc = ServeError("rate_limited", "slow down")
        exc.retry_after = 2.5
        raise exc
    r = TestClient(create_app(fake, owner_resolver=resolver)).get("/v1/sessions")
    _err(r, 429, "rate_limited")
    assert r.headers["retry-after"] == "3"


def test_a_resolver_returning_no_owner_is_internal(fake):
    client = TestClient(create_app(fake, owner_resolver=lambda request: ""))
    _err(client.get("/v1/sessions"), 500, "internal")


def test_every_contract_error_code_has_its_status():
    assert set(STATUS_BY_CODE) == set(ERROR_CODES)
    assert STATUS_BY_CODE == {
        "invalid_request": 400, "invalid_order": 400, "insufficient_buying_power": 403,
        "not_found": 404, "conflict": 409, "session_closed": 409, "unauthorized": 401,
        "rate_limited": 429, "quota_exceeded": 429, "internal": 500}


@pytest.mark.parametrize("code", ERROR_CODES)
def test_a_service_error_maps_to_its_status(code):
    class Refusing(FakeSessionService):
        def list(self, owner):
            raise ServeError(code, f"refused with {code}")
    r = TestClient(create_app(Refusing())).get("/v1/sessions")
    _err(r, STATUS_BY_CODE[code], code)
    assert r.json()["message"] == f"refused with {code}"


def test_a_crash_is_500_internal_and_says_it_is_a_bug():
    class Crashing(FakeSessionService):
        def list(self, owner):
            raise RuntimeError("boom")
    client = TestClient(create_app(Crashing()), raise_server_exceptions=False)
    body = _err(client.get("/v1/sessions"), 500, "internal")
    assert "RuntimeError" in body["message"] and "bug" in body["message"]


def test_unknown_fields_are_refused_not_ignored(fake):
    client = TestClient(create_app(fake))
    body = _err(client.post("/v1/sessions", json={"univers_size": 4}), 400, "invalid_request")
    assert "univers_size" in body["message"]
    assert fake.calls == []


def test_schema_failures_are_invalid_request_naming_the_field(fake):
    client = TestClient(create_app(fake))
    sid = client.post("/v1/sessions", json=SMALL).json()["session_id"]
    body = _err(_order(client, sid, ticker="X", side="hold", quantity=1), 400, "invalid_request")
    assert "side" in body["message"]
    _err(_order(client, sid, side="buy", quantity=1), 400, "invalid_request")     # no ticker
    _err(client.post(f"/v1/sessions/{sid}/advance", json={"steps": 0}), 400, "invalid_request")
    _err(client.post(f"/v1/sessions/{sid}/advance", json={"until": "tomorrow"}), 400, "invalid_request")
    _err(client.get(f"/v1/sessions/{sid}/orders", params={"status": "open"}), 400, "invalid_request")
    _err(client.get(f"/v1/sessions/{sid}/fills", params={"since_day": -1}), 400, "invalid_request")
    r = client.post("/v1/sessions", content=b"{not json", headers={"content-type": "application/json"})
    _err(r, 400, "invalid_request")


def test_unknown_routes_and_methods_answer_in_the_error_shape(fake):
    client = TestClient(create_app(fake))
    _err(client.get("/v2/nothing"), 404, "not_found")
    _err(client.put("/v1/sessions"), 405, "invalid_request")


def test_advance_and_fork_take_an_empty_body(fake):
    client = TestClient(create_app(fake))
    sid = client.post("/v1/sessions", json=SMALL).json()["session_id"]
    assert client.post(f"/v1/sessions/{sid}/advance").json()["clock"]["step"] == 1
    assert client.post(f"/v1/sessions/{sid}/fork").status_code == 201


def test_middleware_is_applied_in_every_accepted_form(fake):
    from starlette.middleware import Middleware

    class Tag(BaseHTTPMiddleware):
        def __init__(self, app, name="x-tag", value="1"):
            super().__init__(app)
            self.name, self.value = name, value

        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers[self.name] = self.value
            return response

    app = create_app(fake, middleware=[Tag, (Tag, {"name": "x-two", "value": "2"}),
                                       Middleware(Tag, name="x-three", value="3")])
    r = TestClient(app).get("/v1/health")
    assert (r.headers["x-tag"], r.headers["x-two"], r.headers["x-three"]) == ("1", "2", "3")


def test_middleware_can_refuse_with_the_error_shape(fake):
    class Gate(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            if request.url.path.startswith("/v1/sessions"):
                return error_response(ServeError("quota_exceeded", "no more sessions today"))
            return await call_next(request)
    client = TestClient(create_app(fake, middleware=[Gate]))
    _err(client.post("/v1/sessions", json=SMALL), 429, "quota_exceeded")
    assert client.get("/v1/health").status_code == 200


def test_request_api_key_reads_the_usual_headers():
    class R:
        def __init__(self, headers):
            from starlette.datastructures import Headers
            self.headers = Headers(headers)
    assert request_api_key(R({"Authorization": "Bearer k1"})) == "k1"
    assert request_api_key(R({"X-API-Key": "k2"})) == "k2"
    assert request_api_key(R({"APCA-API-KEY-ID": "id", "APCA-API-SECRET-KEY": "k3"})) == "k3"
    assert request_api_key(R({"APCA-API-KEY-ID": "k4"})) == "k4"
    assert request_api_key(R({})) is None


def test_the_service_and_resolver_are_exposed_for_a_wrapper(fake):
    resolver = lambda request: "w"  # noqa: E731
    app = create_app(fake, owner_resolver=resolver)
    assert app.state.service is fake and app.state.owner_resolver is resolver


def test_describe_health_and_root(fake):
    client = TestClient(create_app(fake))
    assert client.get("/v1/health").json() == {"ok": True, "contract_version": T.CONTRACT_VERSION}
    d = client.get("/v1/describe").json()
    assert d["contract_version"] == T.CONTRACT_VERSION
    assert d["errors"] == STATUS_BY_CODE
    assert any("not a forecast" in c for c in d["caveats"])
    assert client.get("/").json()["docs"] == "/docs"
    assert fake.calls == []  # none of these touch the service or need an owner


def test_describe_reads_its_caveats_from_the_envelope(fake):
    from tradefloor import envelope
    cert = envelope.certified()
    caveats = TestClient(create_app(fake)).get("/v1/describe").json()["caveats"]
    text = " ".join(caveats)
    assert cert["preset"] in text and str(cert["certified_horizon_days"]) in text
    for gap in cert["gaps"]:
        assert f"'{gap['id']}'" in text


def test_the_openapi_document_covers_every_route(fake):
    doc = TestClient(create_app(fake)).get("/openapi.json").json()
    paths = doc["paths"]
    for path, method in [
        ("/v1/sessions", "post"), ("/v1/sessions", "get"), ("/v1/sessions/{session_id}", "get"),
        ("/v1/sessions/{session_id}/observation", "get"),
        ("/v1/sessions/{session_id}/orders", "post"), ("/v1/sessions/{session_id}/orders", "get"),
        ("/v1/sessions/{session_id}/orders/{order_id}", "get"),
        ("/v1/sessions/{session_id}/orders/{order_id}", "delete"),
        ("/v1/sessions/{session_id}/fills", "get"), ("/v1/sessions/{session_id}/advance", "post"),
        ("/v1/sessions/{session_id}/fork", "post"), ("/v1/sessions/{session_id}/close", "post"),
        ("/v1/describe", "get"), ("/v1/health", "get"),
        ("/broker/{session_id}/v2/account", "get"), ("/broker/{session_id}/v2/orders", "post"),
    ]:
        assert method in paths.get(path, {}), (method, path)
    schemas = doc["components"]["schemas"]
    for name in ("SessionInfo", "Observation", "Order", "Fill", "AdvanceResult", "SessionReport",
                 "SessionConfigBody", "OrderRequestBody"):
        assert name in schemas, name
    assert schemas["OrderRequestBody"]["additionalProperties"] is False


def test_the_transports_do_not_load_their_dependencies_on_import():
    code = ("import sys, tradefloor.serve, tradefloor.serve.http, tradefloor.serve.mcp; "
            "loaded = [m for m in ('fastapi', 'starlette', 'uvicorn', 'mcp') if m in sys.modules]; "
            "print(loaded); sys.exit(1 if loaded else 0)")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# -- the fake agrees with the core --------------------------------------------------


def _script(client):
    """Calls whose outcomes (not prices) the contract, or the core's reading of
    it, fixes. Returns what happened, price-free, for comparison."""
    out = []

    def rec(tag, r, *keys):
        body = r.json() if r.content else None
        row = [tag, r.status_code]
        if isinstance(body, dict) and "code" in body and set(body) == {"code", "message"}:
            row.append(body["code"])
        for k in keys:
            row.append(k(body))
        out.append(tuple(row))
        return body

    clock = lambda b: tuple(b["clock"][k] for k in ("day", "tick", "step", "market_open"))  # noqa: E731
    info = rec("open", client.post("/v1/sessions", json=SMALL), clock)
    sid, (t0, t1) = info["session_id"], info["tickers"][:2]
    obs = client.get(f"/v1/sessions/{sid}/observation").json()
    far = round(obs["quotes"][1]["last"] * 0.5, 2)
    orders = f"/v1/sessions/{sid}/orders"
    status = lambda b: b["status"]  # noqa: E731
    rec("market", client.post(orders, json={"ticker": t0, "side": "buy", "quantity": 10}), status)
    gtc = rec("gtc", client.post(orders, json={"ticker": t1, "side": "buy", "quantity": 1, "type": "limit",
                                               "limit_price": far, "time_in_force": "gtc"}), status)
    rec("day", client.post(orders, json={"ticker": t1, "side": "buy", "quantity": 1, "type": "limit",
                                         "limit_price": far}), status)
    rec("market-with-price", client.post(orders, json={"ticker": t0, "side": "buy", "quantity": 1,
                                                       "limit_price": 5}))
    adv = f"/v1/sessions/{sid}/advance"
    fills = lambda b: [(f["side"], f["quantity"], f["liquidity"]) for f in b["fills"]]  # noqa: E731
    expired = lambda b: [o["status"] for o in b["expired"]]  # noqa: E731
    rec("step", client.post(adv, json={"steps": 1}), clock, fills)
    body = {"ticker": t0, "side": "sell", "quantity": 2, "client_order_id": "k"}
    a = rec("cid", client.post(orders, json=body), status)
    b = rec("cid-again", client.post(orders, json=body), status)
    out.append(("cid-same", a["order_id"] == b["order_id"]))
    rec("cid-conflict", client.post(orders, json={**body, "quantity": 3}))
    rec("bad-ticker", client.post(orders, json={**body, "ticker": "NOPE", "client_order_id": None}))
    rec("cancel", client.delete(f"{orders}/{gtc['order_id']}"), status)
    rec("cancel-again", client.delete(f"{orders}/{gtc['order_id']}"))
    rec("close", client.post(adv, json={"until": "close"}), clock, fills, expired)
    rec("after-close", client.post(orders, json={"ticker": t1, "side": "buy", "quantity": 1,
                                                 "type": "limit", "limit_price": far}), status)
    rec("next-open", client.post(adv, json={"until": "next_open"}), clock, expired)
    rec("close-2", client.post(adv, json={"until": "close"}), clock, expired)
    rec("two-opens", client.post(adv, json={"steps": 2, "until": "next_open"}), clock)
    rec("close-from-open", client.post(adv, json={"until": "close"}), clock)
    rec("close-from-closed", client.post(adv, json={"until": "close"}), clock)
    rec("too-far", client.post(adv, json={"steps": 21, "until": "close"}))
    rec("list-accepted", client.get(orders, params={"status": "accepted"}),
        lambda b: [o["ticker"] for o in b])
    fork = rec("fork", client.post(f"/v1/sessions/{sid}/fork", json={"label": "f"}),
               lambda b: b["parent_session_id"] == sid, lambda b: b["config"]["label"])
    rec("fork-orders", client.get(f"/v1/sessions/{fork['session_id']}/orders"),
        lambda b: [(o["order_id"], o["status"]) for o in b])
    rec("close-session", client.post(f"/v1/sessions/{sid}/close"), lambda b: b["days"])
    rec("orders-after-close", client.get(orders), lambda b: [o["status"] for o in b])
    rec("observe-closed", client.get(f"/v1/sessions/{sid}/observation"))
    rec("order-closed", client.post(orders, json={"ticker": t0, "side": "buy", "quantity": 1}))
    rec("fork-closed", client.post(f"/v1/sessions/{sid}/fork", json={}))
    rec("cancel-closed", client.delete(f"{orders}/ord-000001"))
    return out


@CORE
def test_the_fake_does_what_the_core_does(tmp_path):
    fake = _script(TestClient(create_app(FakeSessionService())))
    core = _script(TestClient(create_app(make_service("core", tmp_path / "sessions"))))
    assert len(fake) == len(core)
    for f, c in zip(fake, core):
        assert f == c, (f, c)


def test_a_wrapper_can_replace_describe(fake):
    client = TestClient(create_app(fake, describe=lambda: {"limits": {"universe_size": [1, 10]}}))
    assert client.get("/v1/describe").json() == {"limits": {"universe_size": [1, 10]}}


def test_a_callable_object_with_an_async_call_is_awaited(fake):
    class Resolver:
        async def __call__(self, request):
            return "object-owner"
    client = TestClient(create_app(fake, owner_resolver=Resolver()))
    assert client.post("/v1/sessions", json=SMALL).json()["owner"] == "object-owner"
