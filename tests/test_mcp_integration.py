"""The MCP server as a client actually meets it: a subprocess over stdio.

`test_mcp.py` calls the tool functions directly. That is the right shape for
the caveat logic -- most of what matters there is which sentences a result
carries -- but it tests the server the way no client ever uses it. Those
tests pass with tool registration broken, schema generation broken, or a
result that cannot be JSON-encoded, because none of that code runs.

This file closes that gap. It spawns `python -m pretium.mcp` as a real
subprocess, performs the MCP handshake, and calls **every** tool over the
wire. What it is really checking is the seam:

- the tool is registered and reachable by name;
- its arguments survive schema validation;
- its result serialises through the protocol.

That last one is not hypothetical. The first draft of `rank_strategies`
returned `Ranking.separation` -- a bound method -- which is invisible to a
direct call that only reads `result["records"]`, and reaches a client as a
transport error.

One server process is shared by every test in the module. A session per test
would spend more time on Python startup than on the assertions.
"""

import asyncio
import sys

import pytest

pytest.importorskip("mcp", reason="the MCP server is an opt-in extra")

from mcp import ClientSession, StdioServerParameters, stdio_client  # noqa: E402

MOMENTUM = {"signal": {"kind": "momentum", "lookback_days": 1.0},
            "portfolio": {"top_k": 3}}

#: Every tool, with arguments small enough that the whole file runs in
#: seconds. The point is reachability and serialisation, not simulation
#: quality -- `test_mcp.py` owns the numbers.
CALLS = {
    "describe_simulator": {},
    "check_envelope": {"horizon_days": 252},
    "validate_strategy": {"spec": MOMENTUM},
    "build_universe": {"size": 8, "seed": 111},
    "build_scenario": {"steps": [{"kind": "ramp", "field": "vix",
                                  "start": 15.0, "end": 40.0, "over": 3}]},
    "evaluate_strategies": {"strategies": {"m": MOMENTUM}, "days": 1,
                            "universe_size": 8},
    "rank_strategies": {"strategies": {"m": MOMENTUM}, "seeds": [1, 2],
                        "days": 1, "universe_size": 8},
    "run_stress_scenario": {"scenario": "vix_shock", "days": 2,
                            "universe_size": 8},
    "explain_price_move": {"universe_size": 8, "day": 1, "top_n": 2},
    "start_job": {"tool": "evaluate_strategies",
                  "arguments": {"strategies": {"m": MOMENTUM}, "days": 1,
                                "universe_size": 8}},
    "check_job": {},
}


def _structured(result):
    """The SDK has used both spellings; accept either rather than pin one."""
    for attr in ("structuredContent", "structured_content"):
        value = getattr(result, attr, None)
        if value is not None:
            return value
    return None


async def _drive():
    params = StdioServerParameters(command=sys.executable,
                                   args=["-m", "pretium.mcp"])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            listed = await session.list_tools()
            results = {}
            for name, args in CALLS.items():
                res = await session.call_tool(name, args)
                results[name] = (res, _structured(res))
            return init, listed.tools, results


@pytest.fixture(scope="module")
def live():
    """One server subprocess for the whole module."""
    return asyncio.run(_drive())


# -- the handshake ---------------------------------------------------------


def test_the_server_completes_an_mcp_handshake(live):
    init, _tools, _results = live
    info = getattr(init, "serverInfo", None) or getattr(init, "server_info")
    assert info.name == "pretium"


def test_the_server_advertises_its_instructions(live):
    """A model meets the server through these before any tool. If they go
    missing the tools still work and the framing is gone."""
    init, _tools, _results = live
    text = getattr(init, "instructions", "") or ""
    assert "caveats" in text.lower(), (
        "the instructions must tell a client the caveats are part of the "
        "result, because that is the one thing a summary tends to drop"
    )


def test_every_registered_tool_is_exercised_here(live):
    """The guard against this file going stale.

    A tool added to the server and not added to CALLS would never be called
    over the wire, and this file would keep reporting a green integration
    suite for a surface it does not touch.
    """
    _init, tools, _results = live
    registered = {t.name for t in tools}
    assert registered == set(CALLS), (
        f"registered but never called here: {sorted(registered - set(CALLS))}; "
        f"called but not registered: {sorted(set(CALLS) - registered)}"
    )


# -- every tool, over the wire ---------------------------------------------


@pytest.mark.parametrize("name", sorted(CALLS))
def test_the_tool_is_reachable_and_its_result_serialises(live, name):
    _init, _tools, results = live
    res, structured = results[name]
    assert res.isError is False if hasattr(res, "isError") else True
    assert getattr(res, "is_error", False) is False, (
        f"{name} came back as a transport error"
    )
    assert structured is not None, (
        f"{name} produced no structured content -- its return value did not "
        f"survive serialisation"
    )
    assert structured.get("ok") is True, f"{name}: {structured.get('error')}"


@pytest.mark.parametrize("name", [
    "evaluate_strategies", "rank_strategies", "run_stress_scenario",
])
def test_a_scored_result_carries_caveats_over_the_wire(live, name):
    """The caveats are the product here, and they are a list of strings --
    exactly the shape that a serialisation bug silently empties."""
    _init, _tools, results = live
    _res, structured = results[name]
    caveats = structured.get("caveats")
    assert isinstance(caveats, list) and caveats, f"{name} lost its caveats"
    assert all(isinstance(c, str) and c for c in caveats)


@pytest.mark.parametrize("name", sorted(CALLS))
def test_every_result_carries_provenance_over_the_wire(live, name):
    _init, _tools, results = live
    _res, structured = results[name]
    prov = structured.get("provenance")
    assert isinstance(prov, dict) and prov.get("model_preset"), (
        f"{name} lost its provenance in transit"
    )


def test_the_ranking_survives_the_wire_intact(live):
    """`Ranking.separation` is a bound method. The first draft returned it
    uncalled, which a direct test reading `records` never sees and a client
    receives as a transport error."""
    _init, _tools, results = live
    _res, structured = results["rank_strategies"]
    assert isinstance(structured["paired_sign_tests"], list)
    for row in structured["paired_sign_tests"]:
        assert {"a", "b", "wins_a", "wins_b"} <= set(row)


def test_a_job_started_over_the_wire_is_visible_over_the_wire(live):
    """start_job and check_job are two calls that must agree about state
    living in the server process."""
    _init, _tools, results = live
    _res, started = results["start_job"]
    _res2, listed = results["check_job"]
    assert started["job_id"].startswith("job-")
    assert any(j["job_id"] == started["job_id"] for j in listed["jobs"]), (
        "the job listing did not see the job just started"
    )


def test_a_bad_argument_comes_back_as_a_result_not_a_crash():
    """A refusal has to reach the model as something it can act on. An
    exception reaches it as a transport error with no guidance."""
    async def go():
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "pretium.mcp"])
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(
                    "validate_strategy",
                    {"spec": {"signal": {"kind": "telepathy"}}})

    res = asyncio.run(go())
    structured = _structured(res)
    assert structured is not None
    assert structured["ok"] is False
    assert "telepathy" in structured["error"]
    assert "momentum" in structured["error"], (
        "the error must list the valid kinds, or a model cannot self-correct"
    )
