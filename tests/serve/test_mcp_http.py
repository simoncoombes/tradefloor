"""The MCP server over streamable HTTP, with an owner per request.

stdio cannot be hosted, so a remote agent reaches MCP over HTTP: on its own
(`create_http_app`, `python -m tradefloor.serve.mcp --http`) or beside the
HTTP API (`create_app(..., mcp_path="/mcp")`, which `python -m
tradefloor.serve` does by default). Each request's owner comes from the same
`owner_resolver` the HTTP API takes, so one key works on both. Tested with the
SDK's own streamable-HTTP client against a live uvicorn server.
"""

import asyncio
import contextlib
import json
import socket
import threading
import time

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")
pytest.importorskip("fastapi", reason="the HTTP server is an opt-in extra")
httpx = pytest.importorskip("httpx")

from fakes import FakeSessionService, core_has, make_service  # noqa: E402
from tradefloor.serve.http import create_app, request_api_key  # noqa: E402
from tradefloor.serve.mcp import create_http_app  # noqa: E402
from tradefloor.serve.types import ServeError  # noqa: E402

CORE04 = pytest.mark.skipif(not core_has("news"), reason="the core has not landed contract 0.4")
KEYS = {"key-alice": "alice", "key-bob": "bob"}


def by_key(request):
    key = request_api_key(request)
    if key not in KEYS:
        raise ServeError("unauthorized", "missing or unknown API key")
    return KEYS[key]


@contextlib.contextmanager
def live(app):
    import uvicorn
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", ws="none", lifespan="on"))
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


def _body(res):
    body = getattr(res, "structured_content", None)
    is_error = getattr(res, "is_error", None)
    return bool(is_error), body


async def _session(url, key, script):
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    headers = {"Authorization": f"Bearer {key}"} if key else {}
    async with httpx2.AsyncClient(headers=headers, timeout=60) as http:
        async with streamable_http_client(url, http_client=http) as streams:
            read, write = streams[0], streams[1]
            async with ClientSession(read, write) as session:
                await session.initialize()

                async def call(name, **arguments):
                    return _body(await session.call_tool(name, arguments))
                tools = [t.name for t in (await session.list_tools()).tools]
                return tools, await script(call)


def run(url, key, script):
    return asyncio.run(_session(url, key, script))


async def _open_and_trade(call):
    _, info = await call("open_session", universe_size=4)
    sid, t0 = info["session_id"], info["tickers"][0]
    await call("place_order", session_id=sid, ticker=t0, side="buy", quantity=3)
    _, res = await call("advance", session_id=sid, steps=1)
    _, listed = await call("list_sessions")
    return info, res, listed


@pytest.mark.parametrize("mount", ["standalone", "beside-the-api"])
def test_each_request_acts_as_the_owner_its_key_names(mount):
    fake = FakeSessionService()
    if mount == "standalone":
        app, path = create_http_app(fake, owner_resolver=by_key), "/mcp"
    else:
        app, path = create_app(fake, owner_resolver=by_key, mcp_path="/mcp"), "/mcp"
    with live(app) as base:
        url = base + path
        tools, (info, res, listed) = run(url, "key-alice", _open_and_trade)
        assert {"get_bars", "get_news", "open_session"} <= set(tools)
        assert info["owner"] == "alice"
        assert res["fills"][0]["quantity"] == 3
        assert [s["session_id"] for s in listed["sessions"]] == [info["session_id"]]

        async def bob(call):
            return (await call("list_sessions"),
                    await call("observe", session_id=info["session_id"]))
        _, (sessions, observe) = run(url, "key-bob", bob)
        assert sessions == (False, {"sessions": []})
        assert observe[0] is True and observe[1]["code"] == "not_found"

        # Without a key, or with a wrong one, the HTTP request itself is refused.
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "t", "version": "0"}}}
        accept = {"Accept": "application/json, text/event-stream"}
        for headers in ({}, {"Authorization": "Bearer nope"}):
            r = httpx.post(url, json=init, headers={**accept, **headers})
            assert r.status_code == 401 and r.json()["code"] == "unauthorized"
            assert r.headers["www-authenticate"] == "Bearer"
    assert {owner for _, owner, _ in fake.calls} == {"alice", "bob"}


def test_the_http_api_and_mcp_share_sessions_and_keys():
    fake = FakeSessionService()
    app = create_app(fake, owner_resolver=by_key, mcp_path="/mcp")
    with live(app) as base:
        info = httpx.post(base + "/v1/sessions", json={"universe_size": 3},
                          headers={"Authorization": "Bearer key-alice"}).json()

        async def observe(call):
            return await call("observe", session_id=info["session_id"])
        _, (is_error, obs) = run(base + "/mcp", "key-alice", observe)
        assert is_error is False and obs["session_id"] == info["session_id"]
        assert httpx.get(base + "/v1/health").status_code == 200


def test_a_rate_limit_on_mcp_is_429_with_retry_after():
    def limited(request):
        raise ServeError("rate_limited", "slow down", retry_after=1.2)
    with live(create_http_app(FakeSessionService(), owner_resolver=limited)) as base:
        r = httpx.post(base + "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                       headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 429 and r.headers["retry-after"] == "2"
        assert r.json() == {"code": "rate_limited", "message": "slow down", "retry_after": 1.2}


def test_the_default_http_owner_is_local():
    fake = FakeSessionService()
    with live(create_http_app(fake)) as base:
        _, (info, _res, _listed) = run(base + "/mcp", None, _open_and_trade)
    assert info["owner"] == "local"


def test_a_server_built_with_a_resolver_refuses_calls_that_are_not_http():
    from tradefloor.serve.mcp import create_server
    server = create_server(FakeSessionService(), owner_resolver=by_key)
    res = asyncio.run(server.call_tool("list_sessions", {}))
    assert res.is_error is True and res.structured_content["code"] == "unauthorized"
    assert json.loads(res.content[0].text)["code"] == "unauthorized"
    assert asyncio.run(server.call_tool("describe", {})).is_error is False


@CORE04
def test_mcp_over_http_on_the_core(tmp_path):
    app = create_app(make_service("core", tmp_path / "sessions"), mcp_path="/mcp")
    with live(app) as base:
        async def script(call):
            info, res, _ = await _open_and_trade(call)
            _, bars = await call("get_bars", session_id=info["session_id"],
                                 ticker=info["tickers"][0], resolution="step")
            return info, res, bars
        _, (info, res, bars) = run(base + "/mcp", None, script)
        assert info["owner"] == "local" and res["fills"]
        assert len(bars["bars"]) == 1
