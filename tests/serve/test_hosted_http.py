"""End to end through HTTP: the transport's app over HostedService over the
real core on a temporary FileStore. API keys in, owners isolated, limits out
as 429/400 with Retry-After, and every mutating request in the audit log."""

from __future__ import annotations

from dataclasses import replace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
http = pytest.importorskip("tradefloor.serve.http")
core = pytest.importorskip("tradefloor.serve.core")

from fastapi.testclient import TestClient  # noqa: E402

from tradefloor.serve.hosted import AuditLog, HostedService, Quotas  # noqa: E402
from tradefloor.serve.hosted.accounts import Accounts  # noqa: E402
from tradefloor.serve.hosted.app import create_hosted_app  # noqa: E402
from tradefloor.serve.hosted.plans import DEFAULT_PLANS  # noqa: E402
from tradefloor.serve.store import FileStore  # noqa: E402


class Clock:
    def __init__(self) -> None:
        self.t = 1_790_000_000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def world(tmp_path):
    clock = Clock()
    plans = dict(DEFAULT_PLANS)
    plans["tiny"] = replace(plans["trial"], name="tiny", calls_per_minute=8, max_open_sessions=1)
    acc = Accounts(tmp_path / "accounts.json", pepper=b"pepper-for-http-tests", plans=plans,
                   clock=clock)
    hosted = HostedService(core.LocalSessionService(FileStore(tmp_path / "sessions")), acc,
                           Quotas(tmp_path / "usage.json", clock=clock, flush_interval=0),
                           AuditLog(tmp_path / "audit", clock=clock), clock=clock)
    _, alice = acc.create_key("alice", plan="standard")
    _, bob = acc.create_key("bob", plan="standard")
    client = TestClient(create_hosted_app(hosted))
    return hosted, client, alice, bob, clock


def auth(key):
    return {"Authorization": f"Bearer {key}"}


def open_session(client, key, **cfg):
    r = client.post("/v1/sessions", json={"universe_size": 5, **cfg}, headers=auth(key))
    assert r.status_code == 201, r.text
    return r.json()


def test_health_needs_no_key_and_everything_else_does(world):
    _, client, alice, _, _ = world
    assert client.get("/healthz").json() == {"ok": True}
    r = client.get("/v1/sessions")
    assert r.status_code == 401 and r.json()["code"] == "unauthorized"
    assert r.headers.get("www-authenticate", "").lower().startswith("bearer")
    r = client.get("/v1/sessions", headers=auth("tfk_000000000000_" + "x" * 43))
    assert r.status_code == 401 and r.json()["message"] == "API key not recognised"
    assert client.get("/v1/sessions", headers=auth(alice)).status_code == 200


def test_owners_are_isolated_through_http(world):
    _, client, alice, bob, _ = world
    s = open_session(client, alice)
    sid = s["session_id"]
    assert s["owner"] == "alice"
    for method, path, body in [
        ("get", f"/v1/sessions/{sid}", None),
        ("get", f"/v1/sessions/{sid}/observation", None),
        ("post", f"/v1/sessions/{sid}/advance", {"steps": 1}),
        ("post", f"/v1/sessions/{sid}/orders", {"ticker": s["tickers"][0], "side": "buy", "quantity": 1}),
        ("post", f"/v1/sessions/{sid}/close", None),
        ("get", f"/broker/{sid}/v2/account", None),
    ]:
        r = client.request(method.upper(), path, json=body, headers=auth(bob))
        assert r.status_code == 404 and r.json()["code"] == "not_found", (path, r.text)
    assert client.get("/v1/sessions", headers=auth(bob)).json() == []
    assert [x["session_id"] for x in client.get("/v1/sessions", headers=auth(alice)).json()] == [sid]


def test_the_broker_facade_takes_the_key_split_in_two(world):
    _, client, alice, _, _ = world
    sid = open_session(client, alice)["session_id"]
    prefix, key_id, secret = alice.split("_", 2)
    key_id = f"{prefix}_{key_id}"
    split = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
    r = client.get(f"/broker/{sid}/v2/account", headers=split)
    assert r.status_code == 200, r.text
    whole = {"APCA-API-KEY-ID": "whatever", "APCA-API-SECRET-KEY": alice}
    assert client.get(f"/broker/{sid}/v2/account", headers=whole).status_code == 200


def test_rate_limit_is_a_429_with_retry_after(world):
    hosted, client, _, _, clock = world
    _, key = hosted.accounts.create_key("carol", plan="tiny")
    for _ in range(8):
        assert client.get("/v1/sessions", headers=auth(key)).status_code == 200
    r = client.get("/v1/sessions", headers=auth(key))
    assert r.status_code == 429 and r.json()["code"] == "rate_limited"
    assert "8 calls per minute" in r.json()["message"]
    assert int(r.headers["retry-after"]) >= 1
    clock.t += 60
    assert client.get("/v1/sessions", headers=auth(key)).status_code == 200


def test_quotas_and_caps_through_http(world):
    hosted, client, _, _, _ = world
    _, key = hosted.accounts.create_key("dave", plan="tiny")
    r = client.post("/v1/sessions", json={"universe_size": 30}, headers=auth(key))
    assert r.status_code == 400 and r.json()["code"] == "invalid_request"
    assert "tiny" in r.json()["message"]
    open_session(client, key)
    r = client.post("/v1/sessions", json={"universe_size": 5}, headers=auth(key))
    assert r.status_code == 429 and r.json()["code"] == "quota_exceeded"
    assert "1 open sessions" in r.json()["message"]
    usage = client.get("/v1/usage", headers=auth(key)).json()
    assert usage["owner"] == "dave" and usage["plan"]["name"] == "tiny"
    assert usage["sessions"]["open"] == 1 and usage["today"]["refused"] >= 1


def test_daily_quota_reset_time_reaches_the_client(world):
    hosted, client, _, _, _ = world
    plans = hosted.accounts.plans
    plans["oneday"] = replace(plans["trial"], name="oneday", sim_days_per_day=1)
    _, key = hosted.accounts.create_key("erin", plan="oneday")
    sid = open_session(client, key, ticks_per_step=30)["session_id"]
    assert client.post(f"/v1/sessions/{sid}/advance", json={"until": "close"},
                       headers=auth(key)).status_code == 200
    r = client.post(f"/v1/sessions/{sid}/advance", json={"steps": 1}, headers=auth(key))
    assert r.status_code == 429 and r.json()["code"] == "quota_exceeded"
    assert "resets at" in r.json()["message"] and int(r.headers["retry-after"]) > 60


def test_every_mutating_request_is_audited_with_its_key(world):
    hosted, client, alice, bob, _ = world
    s = open_session(client, alice)
    sid = s["session_id"]
    client.post(f"/v1/sessions/{sid}/orders", headers=auth(alice),
                json={"ticker": s["tickers"][0], "side": "buy", "quantity": 10})
    client.post(f"/v1/sessions/{sid}/advance", json={"steps": 2}, headers=auth(alice))
    client.post(f"/v1/sessions/{sid}/advance", json={"steps": 1}, headers=auth(bob))
    client.get(f"/v1/sessions/{sid}/observation", headers=auth(alice))
    client.post(f"/v1/sessions/{sid}/close", headers=auth(alice))
    lines = [(e["owner"], e["call"], e["outcome"]) for e in hosted.audit.read()]
    assert lines == [("alice", "open", "ok"), ("alice", "place_order", "ok"),
                     ("alice", "advance", "ok"), ("bob", "advance", "not_found"),
                     ("alice", "close", "ok")]
    alice_id = alice.split("_")[1]
    assert {e["key_id"] for e in hosted.audit.read(owner="alice")} == {alice_id}


def test_revocation_and_suspension_bite_on_the_next_request(world):
    hosted, client, alice, _, _ = world
    assert client.get("/v1/sessions", headers=auth(alice)).status_code == 200
    other = Accounts(hosted.accounts.path, pepper=hosted.accounts.pepper)   # the admin CLI
    other.set_suspended("alice")
    r = client.get("/v1/sessions", headers=auth(alice))
    assert r.status_code == 401 and "suspended" in r.json()["message"]
    other.set_suspended("alice", False)
    other.revoke_all("alice")
    r = client.get("/v1/sessions", headers=auth(alice))
    assert r.status_code == 401 and "revoked" in r.json()["message"]


def test_a_source_that_keeps_failing_is_throttled(world):
    _, client, alice, _, _ = world
    bad = auth("tfk_000000000000_" + "z" * 43)
    codes = [client.get("/v1/sessions", headers=bad).status_code for _ in range(31)]
    assert codes[:30] == [401] * 30 and codes[30] == 429
    # a valid key from the same address (TestClient is always "testclient")
    # still gets in: a shared NAT address must not lock out good bots
    assert client.get("/v1/sessions", headers=auth(alice)).status_code == 200
    audited = list(world[0].audit.read(call="authenticate"))
    assert len(audited) == 1 and audited[0]["detail"]["source"] == "testclient"
