"""The trading MCP server (`python -m tradefloor.serve.mcp`).

In-process tests drive `create_server(service, owner)` through the SDK's own
`call_tool`, which runs argument validation and result conversion exactly as
a client call does. The stdio test then spawns the real module as a
subprocess and calls every tool over the wire, because a tool that works
in-process can still fail to register or to serialise (see
tests/test_mcp_integration.py for the read-only server's version of this).
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")

from fakes import FakeSessionService, core_available, make_service  # noqa: E402
from tradefloor.serve import types as T  # noqa: E402
from tradefloor.serve.mcp import create_server  # noqa: E402

CORE = pytest.mark.skipif(not core_available(), reason="tradefloor.serve.core has not landed")
HERE = Path(__file__).resolve().parent

TOOLS = {"open_session", "list_sessions", "observe", "place_order", "cancel_order",
         "list_orders", "list_fills", "advance", "fork_session", "close_session", "describe"}


def call(server, name, **args):
    res = asyncio.run(server.call_tool(name, args))
    return res.is_error, res.structured_content, res


def ok(server, name, **args):
    is_error, body, _ = call(server, name, **args)
    assert is_error is False, (name, body)
    return body


def refused(server, name, code, **args):
    is_error, body, res = call(server, name, **args)
    assert is_error is True, (name, body)
    assert set(body) == {"code", "message"} and body["code"] == code, body
    assert json.loads(res.content[0].text) == body    # the text a client without structured content reads
    return body["message"]


@pytest.fixture(params=["fake", pytest.param("core", marks=CORE)])
def server(request, tmp_path):
    return create_server(make_service(request.param, tmp_path / "sessions"))


def _open(server, **config):
    return ok(server, "open_session", universe_size=4, **config)


# -- registration ---------------------------------------------------------------------


def test_the_contract_tools_are_registered_with_real_descriptions():
    tools = asyncio.run(create_server(FakeSessionService()).list_tools())
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.description and len(t.description) > 80, t.name
        assert t.input_schema["type"] == "object", t.name
    by_name = {t.name: t for t in tools}
    for name in ("observe", "list_sessions", "list_orders", "list_fills", "describe"):
        assert by_name[name].annotations.read_only_hint is True, name
    assert by_name["close_session"].annotations.destructive_hint is True
    assert by_name["place_order"].annotations.read_only_hint is False
    # The traps a model falls into are named in the descriptions it reads.
    assert "NOT filled now" in by_name["place_order"].description
    assert "ONLY way time passes" in by_name["advance"].description
    props = by_name["place_order"].input_schema["properties"]
    assert props["side"]["enum"] == ["buy", "sell"] and "Shares" in props["quantity"]["description"]


def test_open_session_defaults_are_the_contract_defaults():
    tools = asyncio.run(create_server(FakeSessionService()).list_tools())
    props = next(t for t in tools if t.name == "open_session").input_schema["properties"]
    for key, value in T.SessionConfig().to_dict().items():
        assert props[key]["default"] == value, key


def test_the_instructions_say_time_does_not_move_on_its_own():
    server = create_server(FakeSessionService())
    assert "NEVER moves on its own" in server.instructions
    assert "caveats" in server.instructions


# -- every tool, against the fake and the core ------------------------------------------


def test_a_session_through_every_tool(server):
    info = _open(server, label="mcp")
    assert set(info) == {f for f in T.SessionInfo.__dataclass_fields__}
    sid, t0 = info["session_id"], info["tickers"][0]
    assert [s["session_id"] for s in ok(server, "list_sessions")["sessions"]] == [sid]

    obs = ok(server, "observe", session_id=sid)
    assert [q["ticker"] for q in obs["quotes"]] == info["tickers"] and obs["state_hash"]

    order = ok(server, "place_order", session_id=sid, ticker=t0, side="buy", quantity=10)
    assert order["status"] == "accepted"
    res = ok(server, "advance", session_id=sid, steps=1)
    assert [f["order_id"] for f in res["fills"]] == [order["order_id"]]
    assert ok(server, "list_fills", session_id=sid)["fills"][0]["order_id"] == order["order_id"]
    assert ok(server, "list_fills", session_id=sid, since_day=1)["fills"] == []

    last = res["observation"]["quotes"][1]["last"]
    limit = ok(server, "place_order", session_id=sid, ticker=info["tickers"][1], side="buy",
               quantity=2, type="limit", limit_price=round(last * 0.5, 2), time_in_force="gtc",
               client_order_id="c-1")
    again = ok(server, "place_order", session_id=sid, ticker=info["tickers"][1], side="buy",
               quantity=2, type="limit", limit_price=round(last * 0.5, 2), time_in_force="gtc",
               client_order_id="c-1")
    assert again["order_id"] == limit["order_id"]
    assert [o["order_id"] for o in ok(server, "list_orders", session_id=sid, status="accepted")["orders"]] \
        == [limit["order_id"]]
    assert ok(server, "cancel_order", session_id=sid, order_id=limit["order_id"])["status"] == "cancelled"
    assert len(ok(server, "list_orders", session_id=sid)["orders"]) == 2

    fork = ok(server, "fork_session", session_id=sid, label="branch")
    assert fork["parent_session_id"] == sid
    a = ok(server, "advance", session_id=sid, until="close")
    b = ok(server, "advance", session_id=fork["session_id"], until="close")
    assert a["observation"]["state_hash"] == b["observation"]["state_hash"]
    assert a["clock"]["market_open"] is False

    report = ok(server, "close_session", session_id=sid)
    assert set(report) == {f for f in T.SessionReport.__dataclass_fields__}
    assert report["caveats"]
    refused(server, "place_order", "session_closed", session_id=sid, ticker=t0, side="buy", quantity=1)


def test_service_refusals_are_tool_errors_with_the_contract_body(server):
    info = _open(server)
    sid, t0 = info["session_id"], info["tickers"][0]
    refused(server, "observe", "not_found", session_id="0" * 32)
    refused(server, "place_order", "invalid_order", session_id=sid, ticker="NOPE", side="buy", quantity=1)
    refused(server, "place_order", "invalid_order", session_id=sid, ticker=t0, side="buy", quantity=0)
    refused(server, "place_order", "invalid_order", session_id=sid, ticker=t0, side="buy",
            quantity=1, type="limit")
    ok(server, "place_order", session_id=sid, ticker=t0, side="buy", quantity=1, client_order_id="k")
    refused(server, "place_order", "conflict", session_id=sid, ticker=t0, side="buy", quantity=2,
            client_order_id="k")
    refused(server, "cancel_order", "not_found", session_id=sid, order_id="nope")
    refused(server, "open_session", "invalid_request", preset="no-such-preset")
    refused(server, "open_session", "invalid_request", universe_size=41)


def test_schema_failures_are_invalid_request_naming_the_argument():
    server = create_server(FakeSessionService())
    sid = _open(server)["session_id"]
    msg = refused(server, "place_order", "invalid_request", session_id=sid, ticker="X",
                  side="hold", quantity="many")
    assert "side" in msg and "quantity" in msg
    refused(server, "advance", "invalid_request", session_id=sid, steps=0)
    refused(server, "advance", "invalid_request", session_id=sid, until="tomorrow")
    refused(server, "observe", "invalid_request")                       # missing session_id
    refused(server, "no_such_tool", "not_found")


def test_a_crash_is_an_internal_tool_error_and_the_server_survives():
    class Crashing(FakeSessionService):
        def list(self, owner):
            raise RuntimeError("boom")
    server = create_server(Crashing())
    msg = refused(server, "list_sessions", "internal")
    assert "RuntimeError" in msg and "bug" in msg
    ok(server, "describe")


def test_the_server_acts_as_its_owner_only():
    fake = FakeSessionService()
    alice, bob = create_server(fake, owner="alice"), create_server(fake, owner="bob")
    sid = _open(alice)["session_id"]
    assert {owner for _, owner, _ in fake.calls} == {"alice"}
    assert ok(bob, "list_sessions")["sessions"] == []
    refused(bob, "observe", "not_found", session_id=sid)
    assert ok(alice, "observe", session_id=sid)["session_id"] == sid


def test_describe_is_computed_from_the_envelope():
    from tradefloor import envelope
    from tradefloor.serve.http import describe_payload
    body = ok(create_server(FakeSessionService()), "describe")
    assert body == describe_payload()
    cert = envelope.certified()
    text = " ".join(body["caveats"])
    assert cert["preset"] in text
    assert all(f"'{g['id']}'" in text for g in cert["gaps"])


# -- over the wire ------------------------------------------------------------------------


async def _drive(args, env):
    from mcp import ClientSession, StdioServerParameters, stdio_client

    params = StdioServerParameters(command=sys.executable, args=args, env=env)
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            tools = (await session.list_tools()).tools
            seen = {}

            async def call(name, **arguments):
                res = await session.call_tool(name, arguments)
                body = getattr(res, "structured_content", None) or getattr(res, "structuredContent", None)
                is_error = getattr(res, "is_error", None)
                if is_error is None:
                    is_error = getattr(res, "isError", False)
                seen[name] = (is_error, body)
                return is_error, body

            await call("describe")
            _, info = await call("open_session", universe_size=4)
            sid, t0 = info["session_id"], info["tickers"][0]
            await call("list_sessions")
            await call("observe", session_id=sid)
            _, order = await call("place_order", session_id=sid, ticker=t0, side="buy", quantity=5)
            await call("advance", session_id=sid, steps=1)
            await call("list_orders", session_id=sid)
            await call("list_fills", session_id=sid)
            last = seen["advance"][1]["observation"]["quotes"][0]["last"]
            _, resting = await call("place_order", session_id=sid, ticker=t0, side="buy", quantity=1,
                                    type="limit", limit_price=round(last * 0.5, 2))
            await call("cancel_order", session_id=sid, order_id=resting["order_id"])
            await call("fork_session", session_id=sid)
            await call("close_session", session_id=sid)
            error = await call("place_order", session_id=sid, ticker=t0, side="buy", quantity=1)
            return init, tools, seen, error


@pytest.mark.parametrize("kind", ["fake", pytest.param("core", marks=CORE)])
def test_every_tool_over_stdio(kind, tmp_path):
    env = dict(os.environ)
    if kind == "fake":
        env["PYTHONPATH"] = os.pathsep.join([str(HERE), env.get("PYTHONPATH", "")])
        args = ["-m", "tradefloor.serve.mcp", "--service", "fakes:FakeSessionService"]
    else:
        args = ["-m", "tradefloor.serve.mcp", "--store", str(tmp_path / "sessions")]
    init, tools, seen, error = asyncio.run(_drive(args, env))
    info = getattr(init, "server_info", None) or getattr(init, "serverInfo")
    assert info.name == "tradefloor-trading"
    assert {t.name for t in tools} == TOOLS == set(seen)
    for name, (is_error, body) in seen.items():
        if name == "place_order":
            continue  # its last call is the refusal below
        assert is_error is False, (name, body)
        assert isinstance(body, dict), name
    assert error == (True, error[1]) and error[1]["code"] == "session_closed"
