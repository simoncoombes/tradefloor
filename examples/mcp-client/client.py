"""A Python MCP client for tradefloor's MCP server, over stdio.

The server is the one `claude mcp add tradefloor -- tradefloor-mcp` registers
with Claude Code. This script is the same thing from Python: it starts the
server as a subprocess, lists its tools, opens a seeded market, runs a day,
forks it, sends one order in one branch only, and prints how the two branches
differ a day later. No key, no network: the server runs on this machine.

    pip install "tradefloor[mcp]"
    python examples/mcp-client/client.py

`SERVER` below is the whole configuration. Any MCP client that can launch a
stdio server takes the same command and arguments.
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters, stdio_client

#: How to start the server. `python -m tradefloor mcp` is the `tradefloor-mcp`
#: console script under this interpreter, so the server runs against the
#: tradefloor this script imports. The environment is passed on whole: the
#: SDK otherwise hands the child a short list of variables, without
#: PYTHONPATH or a virtual environment's settings.
SERVER = StdioServerParameters(command=sys.executable,
                               args=["-m", "tradefloor", "mcp"],
                               env=dict(os.environ))

SEED = 7
UNIVERSE_SIZE = 8
TICKER = "AAA"
ORDER = 2_000          # shares bought in the branch that trades


def structured(result) -> dict:
    """The tool's JSON result. The SDK has spelled the attribute two ways."""
    for name in ("structured_content", "structuredContent"):
        value = getattr(result, name, None)
        if value is not None:
            return value
    raise RuntimeError(f"no structured result in {result!r}")


async def main() -> dict:
    async with stdio_client(SERVER) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            print(f"{len(tools)} tools: {', '.join(t.name for t in tools)}")

            async def call(name: str, **args) -> dict:
                return structured(await session.call_tool(name, args))

            opened = await call("open_session", universe_size=UNIVERSE_SIZE, seed=SEED)
            control = opened["session_id"]
            await call("session_step", session_id=control, days=1)
            branch = (await call("session_fork", session_id=control))["session_id"]

            # The same next day in both, with one order in the branch only.
            a = await call("session_step", session_id=control, days=1)
            b = await call("session_step", session_id=branch, days=1,
                           orders={TICKER: ORDER})
            for sid in (control, branch):
                await call("close_session", session_id=sid)

    before = opened["market"]["prices"][TICKER]
    after_control = a["market"]["prices"][TICKER]
    after_branch = b["market"]["prices"][TICKER]
    rows = [(f"{TICKER} at the open", before),
            (f"{TICKER} after two days, control", after_control),
            (f"{TICKER} after two days, bought {ORDER:,}", after_branch)]
    print(f"\nseed {SEED}, {UNIVERSE_SIZE} companies, forked after one day")
    for label, price in rows:
        print(f"{label:<34}{price:>10.4f}")
    print(f"{'what the order moved it':<34}{(after_branch / after_control - 1) * 1e4:>+10.2f} bp")
    return {"tools": len(tools), "control": after_control, "branch": after_branch}


if __name__ == "__main__":
    asyncio.run(main())
