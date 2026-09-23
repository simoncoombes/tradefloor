"""The write-capable MCP server of the trading session server.

`python -m tradefloor.serve.mcp` speaks MCP over stdio. It is a SEPARATE
server from the read-only `tradefloor.mcp`, which is not modified: that one
evaluates strategies as data and can never change anything; this one opens
sessions, places orders and moves time, and every call persists.

The tools map one to one onto `SessionService` (docs/serve/CONTRACT.md,
section 6), for one owner fixed when the server starts (self-run: "local").

Two rules carried over from `tradefloor.mcp`:

- A model has the tool result and nothing else, so each tool description says
  what the call does, its units and limits, and the traps (a market order is
  NOT filled when placed; time does not move on its own).
- Caveats are computed, never retyped. `describe` reads the realism envelope
  on every call, and a session's closing report carries the caveats the core
  computed for that session.

Refusals are tool errors (`is_error=True`) whose body is `{"code",
"message"}`, the same body the HTTP API returns, so a model can correct itself
on the next call. That includes arguments that fail the schema
(`invalid_request`).

The `mcp` SDK is imported by `create_server`, not at import time.
"""

# No `from __future__ import annotations`: the tools are closures whose
# annotations the SDK must evaluate, and pydantic is imported inside
# `create_server` so this module imports without the `mcp` extra.

import argparse
import importlib
import json
import logging
import sys
import threading
from typing import Any, Callable, Optional

from tradefloor.serve.types import (
    ServeError,
    SessionConfig,
    SessionService,
    OrderRequest,
)

log = logging.getLogger("tradefloor.serve.mcp")

LOCAL_OWNER = "local"

INSTRUCTIONS = """\
Trading sessions in the tradefloor market simulator: rehearse a trading agent
in a simulated equity market before it touches money.

Call `describe` first. It says how time, orders and fills work here, the
limits and units, and what this market is and is not certified to reproduce.

The loop: open_session -> observe -> place_order / cancel_order -> advance ->
observe -> ... -> close_session.

Time NEVER moves on its own. Nothing fills and no price changes until you call
`advance`. A market order fills at the START of the next step, so right after
place_order it is 'accepted', not filled.

Sessions persist on the server. list_sessions finds them again after a
restart; fork_session copies one to try an alternative from the same point.

Errors are tool errors with {"code", "message"}; the message says what to
change. A close_session report carries `caveats` computed for that session:
a summary of a result that drops them is a misreport.
"""


def _load_factory(spec: str) -> Callable[[], SessionService]:
    """`module:attribute` -> a zero-argument callable returning a service."""
    module, _, attr = spec.partition(":")
    if not module or not attr:
        raise SystemExit(f"--service must look like module:factory, got {spec!r}")
    return getattr(importlib.import_module(module), attr)


def create_server(service: SessionService, owner: str = LOCAL_OWNER, *,
                  name: str = "tradefloor-trading") -> Any:
    """An MCP server exposing `service` to one owner.

    Returns an `mcp.server.MCPServer`; call `.run("stdio")` on it, or drive it
    in-process with `await server.call_tool(name, arguments)`.
    """
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
        from mcp.types import CallToolResult, TextContent, ToolAnnotations
        from pydantic import Field, ValidationError
    except ImportError as exc:  # pragma: no cover - exercised by the install path
        raise ImportError(
            "The trading MCP server needs the `mcp` package, which tradefloor does "
            "not depend on by default:\n\n    pip install 'tradefloor[mcp]'\n") from exc
    from typing import Annotated, Literal

    import tradefloor
    from tradefloor.serve.http import MAX_TICKS_PER_ADVANCE, MAX_UNIVERSE, describe_payload

    lock = threading.Lock()
    defaults = SessionConfig()

    def error_result(exc: ServeError) -> Any:
        body = exc.to_dict()
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(body))],
                              structured_content=body, is_error=True)

    def run(fn: Callable[[], Any]) -> Any:
        try:
            with lock:
                return fn()
        except ServeError as exc:
            return error_result(exc)
        except Exception as exc:  # a bug: say so, and keep the server alive
            log.exception("tool crashed")
            return error_result(ServeError(
                "internal", f"unexpected {type(exc).__name__}: {exc}. This is a bug in the "
                            "server; report it with the call that caused it."))

    class TradingServer(MCPServer):
        async def call_tool(self, name: str, arguments: dict[str, Any], context: Any = None) -> Any:
            try:
                return await super().call_tool(name, arguments, context)
            except UnexpectedToolError as exc:
                cause = exc.__cause__
                return error_result(ServeError(
                    "internal", f"{exc}" + (f" ({type(cause).__name__}: {cause})" if cause else "")))
            except ToolError as exc:
                cause = exc.__cause__
                if isinstance(cause, ValidationError):
                    parts = [f"{'.'.join(str(p) for p in e['loc']) or name}: {e['msg']}"
                             for e in cause.errors()]
                    message = f"{name}: " + "; ".join(parts)
                else:
                    message = str(exc)
                code = "not_found" if message.startswith("Unknown tool") else "invalid_request"
                return error_result(ServeError(code, message))

    server = TradingServer(name=name, title="tradefloor trading sessions",
                           version=tradefloor.__version__, instructions=INSTRUCTIONS)

    SessionId = Annotated[str, Field(description="The session_id returned by open_session.")]
    READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True,
                           openWorldHint=False)
    WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
                            openWorldHint=False)

    @server.tool(annotations=READ, description=(
        "What this server is, how time, orders and fills work, its limits and units, "
        "the error codes, and caveats read from the simulator's realism envelope at call "
        "time. Call this first."))
    def describe() -> dict[str, Any]:
        return run(describe_payload)

    @server.tool(annotations=WRITE, description=(
        "Open a new simulated market session with its own cash account. Returns "
        "session_id (pass it to every other tool), the tickers you can trade, and the "
        "clock: day 0, tick 0, market open. Every argument is optional. preset must be "
        "a NAMED preset; seed fixes the market's randomness, so the same config and the "
        "same calls give bit-identical results. Nothing happens until you call advance."))
    def open_session(
        preset: Annotated[str, Field(description="A named market preset.")] = defaults.preset,
        seed: Annotated[int, Field(description="Market seed.")] = defaults.seed,
        universe_size: Annotated[int, Field(
            description=f"Number of tickers, 1..{MAX_UNIVERSE}.")] = defaults.universe_size,
        universe_seed: Annotated[int, Field(
            description="Seed that picks the roster of tickers.")] = defaults.universe_seed,
        cash: Annotated[float, Field(description="Starting cash, currency units.")] = defaults.cash,
        max_leverage: Annotated[Optional[float], Field(
            description="Cap on gross exposure / net worth. null = uncapped, which lets "
                        "size beat skill.")] = defaults.max_leverage,
        ticks_per_step: Annotated[int, Field(
            description="Simulated minutes per step; 390 = one whole trading session.")]
            = defaults.ticks_per_step,
        label: Annotated[str, Field(description="Free text for your own records.")] = defaults.label,
    ) -> dict[str, Any]:
        config = SessionConfig(preset=preset, seed=seed, universe_size=universe_size,
                               universe_seed=universe_seed, cash=cash, max_leverage=max_leverage,
                               ticks_per_step=ticks_per_step, label=label)
        return run(lambda: service.open(owner, config).to_dict())

    @server.tool(annotations=READ, description=(
        "List your sessions, open and closed, with their config, tickers, clock and "
        "status. Use it to find a session again after a restart."))
    def list_sessions() -> dict[str, Any]:
        return run(lambda: {"sessions": [s.to_dict() for s in service.list(owner)]})

    @server.tool(annotations=READ, description=(
        "Look at a session without changing it: the clock; one quote per ticker (last, "
        "day open/high/low, previous close, shares traded today, bid/ask when "
        "available); the VIX; macro (a documented subset of the economy); your account "
        "(cash, net worth, gross exposure, leverage, realised and unrealised P&L); "
        "positions; open orders; headlines; and state_hash, which is identical whenever "
        "the state is. Prices change only when you call advance."))
    def observe(session_id: SessionId) -> dict[str, Any]:
        return run(lambda: service.observe(owner, session_id).to_dict())

    @server.tool(annotations=WRITE, description=(
        "Place an order. quantity is in shares, > 0. side 'sell' without a holding opens "
        "a short, allowed within max_leverage. "
        "type 'market': NOT filled now; it fills at the START of the next step (your "
        "next advance), before prices move, at the book's impact-aware price, and it "
        "moves the market. "
        "type 'limit' (needs limit_price, per share): rests on the server and fills in "
        "full at the limit after a step whose low (buy) or high (sell) reaches it; "
        "resting fills do not move the market; no partial fills. A limit that is already "
        "marketable is filled like a market order capped at the limit. "
        "time_in_force 'day' expires at the session close, 'gtc' persists. "
        "client_order_id is an idempotency key: resending the same order under the same "
        "id returns the original instead of placing a duplicate. "
        "Errors: invalid_order, insufficient_buying_power, session_closed."))
    def place_order(
        session_id: SessionId,
        ticker: Annotated[str, Field(description="One of the session's tickers.")],
        side: Annotated[Literal["buy", "sell"], Field(description="buy or sell.")],
        quantity: Annotated[float, Field(description="Shares, > 0.")],
        type: Annotated[Literal["market", "limit"], Field(description="market or limit.")] = "market",
        limit_price: Annotated[Optional[float], Field(
            description="Required for limit orders: price per share, > 0.")] = None,
        time_in_force: Annotated[Literal["day", "gtc"], Field(
            description="day: expires at the session close. gtc: persists.")] = "day",
        client_order_id: Annotated[Optional[str], Field(
            description="Optional idempotency key, unique per session.")] = None,
    ) -> dict[str, Any]:
        request = OrderRequest(ticker=ticker, side=side, quantity=quantity, type=type,
                               limit_price=limit_price, time_in_force=time_in_force,
                               client_order_id=client_order_id)
        return run(lambda: service.place_order(owner, session_id, request).to_dict())

    @server.tool(annotations=WRITE, description=(
        "Cancel an order that is still 'accepted' (not yet filled). Returns the order "
        "with status 'cancelled'. An order that has filled, expired or been rejected "
        "cannot be cancelled."))
    def cancel_order(session_id: SessionId,
                     order_id: Annotated[str, Field(description="The order_id from place_order.")]
                     ) -> dict[str, Any]:
        return run(lambda: service.cancel_order(owner, session_id, order_id).to_dict())

    @server.tool(annotations=READ, description=(
        "List a session's orders, oldest first, optionally only one status: accepted "
        "(working), filled, cancelled, expired or rejected. A rejected or expired order "
        "carries its reason."))
    def list_orders(session_id: SessionId,
                    status: Annotated[Optional[Literal["accepted", "filled", "cancelled",
                                                       "expired", "rejected"]],
                                      Field(description="Only orders with this status.")] = None
                    ) -> dict[str, Any]:
        return run(lambda: {"orders": [o.to_dict() for o in service.orders(owner, session_id, status)]})

    @server.tool(annotations=READ, description=(
        "List a session's fills (executions), oldest first, optionally from one trading "
        "day on. liquidity 'taker' is a market or marketable order filled at the start "
        "of a step; 'resting' is a limit order filled at its price after a step."))
    def list_fills(session_id: SessionId,
                   since_day: Annotated[int, Field(
                       ge=0, description="Only fills on this trading day or later.")] = 0
                   ) -> dict[str, Any]:
        return run(lambda: {"fills": [f.to_dict() for f in service.fills(owner, session_id, since_day)]})

    @server.tool(annotations=WRITE, description=(
        "Move simulated time forward: the ONLY way time passes. Queued market orders "
        "fill at the start of the first step, prices move, resting limits are checked "
        "after each step, and day orders expire at the close. "
        "until='steps' runs `steps` steps of ticks_per_step minutes each, crossing the "
        "close into the next trading day as needed. until='close' runs to the end of "
        "the current session. until='next_open' runs to the start of the next session, "
        "so you can place orders before it trades. "
        f"At most 20 sessions ({MAX_TICKS_PER_ADVANCE} ticks) per call. Returns the new "
        "clock, the fills and expiries this call produced, and a fresh observation."))
    def advance(session_id: SessionId,
                steps: Annotated[int, Field(ge=1, description="Steps to run when until='steps'.")] = 1,
                until: Annotated[Literal["steps", "close", "next_open"], Field(
                    description="steps, close or next_open.")] = "steps",
                ) -> dict[str, Any]:
        return run(lambda: service.advance(owner, session_id, steps, until).to_dict())

    @server.tool(annotations=WRITE, description=(
        "Copy a session's full state (market, account, orders, clock) into a new "
        "session you own, to try an alternative from the same point. Returns the new "
        "session's info with parent_session_id set. The two evolve independently and "
        "deterministically: the same calls on each give the same results."))
    def fork_session(session_id: SessionId,
                     label: Annotated[str, Field(description="Free text for the fork.")] = ""
                     ) -> dict[str, Any]:
        return run(lambda: service.fork(owner, session_id, label).to_dict())

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                             idempotentHint=False, openWorldHint=False),
                 description=(
        "End a session and get its report: the final account, the number of fills, the "
        "trading days it ran, its state_hash, and caveats computed for this session. "
        "Pass the caveats on with any result; a summary without them is a misreport. "
        "A closed session can still be observed but refuses orders and advance."))
    def close_session(session_id: SessionId) -> dict[str, Any]:
        return run(lambda: service.close(owner, session_id).to_dict())

    server.service = service  # type: ignore[attr-defined]
    server.owner = owner  # type: ignore[attr-defined]
    return server


def main(argv: Optional[list[str]] = None) -> None:
    """`python -m tradefloor.serve.mcp`: the trading MCP server over stdio."""
    parser = argparse.ArgumentParser(
        prog="python -m tradefloor.serve.mcp",
        description="The tradefloor trading-session MCP server (stdio).")
    parser.add_argument("--store", default=None,
                        help="Session store directory (default ~/.tradefloor/sessions).")
    parser.add_argument("--owner", default=LOCAL_OWNER,
                        help="The owner every call acts as (default 'local').")
    parser.add_argument("--service", default=None, metavar="MODULE:FACTORY",
                        help="Serve the SessionService a zero-argument factory returns, "
                             "instead of the local one over --store.")
    args = parser.parse_args(argv)
    if args.service:
        service = _load_factory(args.service)()
    else:
        from tradefloor.serve.http import default_service
        service = default_service(args.store)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    create_server(service, owner=args.owner).run("stdio")


if __name__ == "__main__":  # pragma: no cover
    main()
