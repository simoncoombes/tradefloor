"""The MCP server as a client actually meets it: a subprocess over stdio.

`test_mcp.py` calls the tool functions directly. That is the right shape for
the caveat logic -- most of what matters there is which sentences a result
carries -- but it tests the server the way no client ever uses it. Those
tests pass with tool registration broken, schema generation broken, or a
result that cannot be JSON-encoded, because none of that code runs.

This file closes that gap. It spawns `python -m tradefloor.mcp` as a real
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

import tradefloor as tf

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
    "list_scenarios": {},
    # Authored as INTERVENTIONS rather than as a path, so the wire carries
    # the grammar that arrived with the scenario framework. The path form is
    # exercised by `test_mcp.py`, which calls the function directly.
    "build_scenario": {"shocks": [{"target": "market.liquidity",
                                   "operation": "multiply", "value": 0.4,
                                   "at": 1, "duration": 2}]},
    "evaluate_strategies": {"strategies": {"m": MOMENTUM}, "days": 1,
                            "universe_size": 8},
    "rank_strategies": {"strategies": {"m": MOMENTUM}, "seeds": [1, 2],
                        "days": 1, "universe_size": 8},
    # By NAME, from the pack that ships inside the wheel -- the path a model
    # takes when it has no clone to read files from.
    "run_stress_scenario": {"scenario": "liquidity_crisis", "days": 2,
                            "universe_size": 8},
    "explain_price_move": {"universe_size": 8, "day": 1, "top_n": 2},
    # A small roster and one day, because this tool replays the day once
    # per distinct draw set to check its own tree and a tool call answers
    # inside a conversation.
    "explain": {"universe_size": 8, "day": 1, "depth": 2},
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
                                   args=["-m", "tradefloor.mcp"])
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
    assert info.name == "tradefloor"


def test_the_server_advertises_its_instructions(live):
    """A model meets the server through these before any tool. If they go
    missing the tools still work and the framing is gone."""
    init, _tools, _results = live
    text = getattr(init, "instructions", "") or ""
    assert "caveats" in text.lower(), (
        "the instructions must tell a client the caveats are part of the "
        "result, because that is the one thing a summary tends to drop"
    )


def test_the_catalogue_reaches_a_client_with_its_measurements(live):
    """What an agent reads before authoring anything.

    Not that it lists names -- that every target arrives with what it was
    MEASURED to be worth. A model choosing between twelve targets whose
    effect sizes differ by three orders of magnitude has nothing else to go
    on: `macro.qe_pe_boost` moves the median instrument 19.78% and
    `macro.fear_greed` moves it exactly 0.00%, and both are legitimate to
    write.
    """
    _init, _tools, results = live
    _res, out = results["list_scenarios"]
    assert out["ok"] is True
    assert {entry["name"] for entry in out["shipped"]} == set(
        tf.Scenario.available())
    assert set(out["targets"]) == set(tf.TARGETS)
    for name, target in out["targets"].items():
        assert "easured" in target["note"], name
    # The refusals travel too: "there is no volatility level to set in this
    # model" is a more useful answer to a client than a schema error.
    assert "market.volatility" in out["not_supported"]


def test_a_shipped_scenario_runs_by_name_over_the_wire(live):
    """The pack is in the wheel, so a client with no clone can name one."""
    _init, _tools, results = live
    _res, out = results["run_stress_scenario"]
    assert out["ok"] is True, out
    # The shocked arm and the control, which is what makes the number
    # readable: a return under a scenario alone could be the scenario or
    # could be the market.
    assert out["comparison"]
    assert out["scenario"] == "liquidity_crisis"


def test_an_authored_intervention_survives_the_round_trip(live):
    _init, _tools, results = live
    _res, out = results["build_scenario"]
    assert out["ok"] is True, out
    assert out["fingerprint"].startswith("sha256:")
    assert out["shocks"][0]["target"] == "market.liquidity"
    assert out["shocks"][0]["shape"] == "hold"


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
                                       args=["-m", "tradefloor.mcp"])
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
