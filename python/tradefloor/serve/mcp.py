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
import contextlib
import importlib
import inspect
import json
import logging
import sys
import threading
from typing import Any, Callable, Optional, Sequence

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


def _http_request(ctx: Any) -> Any:
    """The HTTP request a tool call arrived on, or None (stdio, in-process)."""
    try:
        return ctx.request_context.request
    except (ValueError, AttributeError):
        return None


async def _await(value: Any) -> Any:
    return await value


def _resolve_in_thread(resolver: Callable[[Any], Any], request: Any) -> Any:
    """Run an owner resolver from a sync tool, which the SDK runs on a worker
    thread: an async resolver is handed back to the event loop."""
    result = resolver(request)
    if inspect.isawaitable(result):
        import anyio.from_thread
        return anyio.from_thread.run(_await, result)
    return result


class OwnerGate:
    """ASGI middleware in front of the MCP endpoint: resolves every HTTP
    request's owner with the HTTP API's `owner_resolver`, answers a refusal
    with the contract's error body and status (401 for `unauthorized`, 429 for
    a rate limit) before the MCP layer sees it, and leaves the owner on
    `request.state.owner` for the tools."""

    def __init__(self, app: Any, resolver: Callable[[Any], Any]) -> None:
        self.app = app
        self.resolver = resolver

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        from starlette.requests import Request

        from tradefloor.serve.http import error_response, resolve_owner
        try:
            owner = await resolve_owner(self.resolver, Request(scope))
        except ServeError as exc:
            await error_response(exc)(scope, receive, send)
            return
        scope.setdefault("state", {})["owner"] = owner
        await self.app(scope, receive, send)


def create_mcp_endpoint(service: SessionService, *,
                        owner_resolver: Optional[Callable[[Any], Any]] = None,
                        path: str = "/mcp",
                        describe: Optional[Callable[[], dict[str, Any]]] = None,
                        serialize: bool = False, transport_security: Any = None,
                        json_response: bool = True, stateless: bool = True) -> tuple[Any, Any]:
    """The MCP server as an ASGI endpoint for streamable HTTP, behind an
    `OwnerGate`. Returns `(endpoint, session_manager)`: route `path` to the
    endpoint and run `session_manager.run()` in the app's lifespan.

    Stateless and JSON by default, so any instance behind a load balancer can
    answer any request. DNS-rebinding protection is off here; the self-run
    server guards the Host header for the whole app (`loopback_guard`), and a
    hosted one at its edge. Pass `transport_security` to turn it on.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    resolver = owner_resolver or (lambda request: LOCAL_OWNER)
    server = create_server(service, owner_resolver=resolver, describe=describe, serialize=serialize)
    security = transport_security or TransportSecuritySettings(enable_dns_rebinding_protection=False)
    inner = server.streamable_http_app(streamable_http_path=path, json_response=json_response,
                                       stateless_http=stateless, transport_security=security)
    route = next(r for r in inner.routes if getattr(r, "path", None) == path)
    return OwnerGate(route.endpoint, resolver), server.session_manager


def create_http_app(service: SessionService, *,
                    owner_resolver: Optional[Callable[[Any], Any]] = None, path: str = "/mcp",
                    middleware: Sequence[Any] = (),
                    describe: Optional[Callable[[], dict[str, Any]]] = None,
                    serialize: bool = False, transport_security: Any = None) -> Any:
    """A Starlette app serving only the MCP server, over streamable HTTP at
    `path`, each request's owner resolved by `owner_resolver` (default: every
    request is "local"). `create_app(..., mcp_path="/mcp")` serves the same
    endpoint beside the HTTP API."""
    from starlette.applications import Starlette

    from tradefloor.serve.http import middleware_stack

    endpoint, manager = create_mcp_endpoint(
        service, owner_resolver=owner_resolver, path=path, describe=describe,
        serialize=serialize, transport_security=transport_security)

    @contextlib.asynccontextmanager
    async def lifespan(app: Any) -> Any:
        async with manager.run():
            yield

    app = Starlette(middleware=middleware_stack(middleware), lifespan=lifespan)
    app.add_route(path, endpoint, methods=["GET", "POST", "DELETE"])
    app.state.service = service
    app.state.mcp_session_manager = manager
    return app


def create_server(service: SessionService, owner: str = LOCAL_OWNER, *,
                  owner_resolver: Optional[Callable[[Any], Any]] = None,
                  name: str = "tradefloor-trading",
                  describe: Optional[Callable[[], dict[str, Any]]] = None,
                  serialize: bool = False) -> Any:
    """An MCP server exposing `service`.

    Every call acts as `owner`, unless `owner_resolver` is given: then each
    call's owner is resolved from its HTTP request (the same resolver
    `create_app` takes), and a call that did not arrive over HTTP is
    `unauthorized`. That is the mode `create_http_app` serves.

    `describe` replaces what the `describe` tool returns (default
    `tradefloor.serve.http.describe_payload`), for a wrapper whose limits differ.
    `serialize` holds one lock around every service call, for a service that is
    not thread-safe (contract 0.3 says a service must be, so it is off).

    Returns an `mcp.server.MCPServer`; call `.run("stdio")` on it, or drive it
    in-process with `await server.call_tool(name, arguments)`.
    """
    try:
        from mcp.server import MCPServer
        from mcp.server.mcpserver import Context
        from mcp.server.mcpserver.exceptions import ToolError, UnexpectedToolError
        from mcp.types import CallToolResult, TextContent, ToolAnnotations
        from pydantic import Field, ValidationError
    except ImportError as exc:  # pragma: no cover - exercised by the install path
        raise ImportError(
            "The trading MCP server needs the `mcp` package, which tradefloor does "
            "not depend on by default:\n\n    pip install 'tradefloor[mcp]'\n") from exc
    from typing import Annotated, Literal

    import tradefloor
    from tradefloor.serve.http import (
        MAX_TICKS_PER_ADVANCE,
        MAX_UNIVERSE,
        STEP_BAR_SESSIONS,
        describe_payload,
    )

    lock: Any = threading.Lock() if serialize else contextlib.nullcontext()
    defaults = SessionConfig()
    describe_fn = describe or describe_payload

    def error_result(exc: ServeError) -> Any:
        body = exc.to_dict()
        return CallToolResult(content=[TextContent(type="text", text=json.dumps(body))],
                              structured_content=body, is_error=True)

    def who_is_calling(ctx: Any) -> str:
        """The owner of this call: fixed (stdio), or resolved from the HTTP
        request by `owner_resolver`, usually already by `OwnerGate`."""
        if owner_resolver is None:
            return owner
        request = _http_request(ctx)
        if request is None:
            raise ServeError("unauthorized", "this server resolves owners from HTTP requests, "
                                             "and this call did not arrive over HTTP")
        resolved = getattr(request.state, "owner", None)
        if resolved is None:
            resolved = _resolve_in_thread(owner_resolver, request)
        if not isinstance(resolved, str) or not resolved:
            raise ServeError("internal", "the owner resolver returned no owner")
        return resolved

    def run(ctx: Any, fn: Callable[[str], Any], *, needs_owner: bool = True) -> Any:
        try:
            who = who_is_calling(ctx) if needs_owner else owner
            with lock:
                return fn(who)
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
    def describe(ctx: Context) -> dict[str, Any]:
        return run(ctx, lambda who: describe_fn(), needs_owner=False)

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
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        config = SessionConfig(preset=preset, seed=seed, universe_size=universe_size,
                               universe_seed=universe_seed, cash=cash, max_leverage=max_leverage,
                               ticks_per_step=ticks_per_step, label=label)
        return run(ctx, lambda who: service.open(who, config).to_dict())

    @server.tool(annotations=READ, description=(
        "List your sessions, open and closed, with their config, tickers, clock and "
        "status. Use it to find a session again after a restart."))
    def list_sessions(ctx: Context) -> dict[str, Any]:
        return run(ctx, lambda who: {"sessions": [s.to_dict() for s in service.list(who)]})

    @server.tool(annotations=READ, description=(
        "Look at a session without changing it: the clock; one quote per ticker (last, "
        "day open/high/low, previous close, shares traded today, bid/ask when "
        "available); the VIX; macro (a documented subset of the economy); your account "
        "(cash, net worth, gross exposure, leverage, realised and unrealised P&L; "
        "leverage is null and insolvent true when net worth is at or below zero); "
        "positions; open orders; today's headlines (which company, which direction, never "
        "how much); and state_hash, which is identical whenever the state is. Prices "
        "change only when you call advance. For history use get_bars."))
    def observe(session_id: SessionId, ctx: Context) -> dict[str, Any]:
        return run(ctx, lambda who: service.observe(who, session_id).to_dict())

    @server.tool(annotations=WRITE, description=(
        "Place an order. quantity is in shares, > 0. side 'sell' without a holding opens "
        "a short, allowed within max_leverage. "
        "type 'market': NOT filled now; it fills at the START of the next step (your "
        "next advance), before prices move, at the book's impact-aware price, and it "
        "moves the market. An order bigger than the book can absorb fills partially "
        "(filled_quantity below quantity, reason 'partial...') and the rest is dropped. "
        "type 'limit' (needs limit_price, per share): rests on the server and fills in "
        "full at the limit after a step whose low (buy) or high (sell) reaches it; "
        "resting fills do not move the market; no partial fills. A limit that is already "
        "marketable is filled like a market order capped at the limit. "
        "time_in_force 'day' expires at the session close, 'gtc' persists. "
        "client_order_id is an idempotency key: resending the same order under the same "
        "id returns the original instead of placing a duplicate. "
        "Leverage: refused only if it would take gross exposure / net worth above "
        "max_leverage AND above where it is now, so reducing risk always passes. "
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
        ctx: Context = None,  # type: ignore[assignment]
    ) -> dict[str, Any]:
        request = OrderRequest(ticker=ticker, side=side, quantity=quantity, type=type,
                               limit_price=limit_price, time_in_force=time_in_force,
                               client_order_id=client_order_id)
        return run(ctx, lambda who: service.place_order(who, session_id, request).to_dict())

    @server.tool(annotations=WRITE, description=(
        "Cancel an order that is still 'accepted' (not yet filled). Returns the order "
        "with status 'cancelled'. An order that has filled, expired or been rejected "
        "cannot be cancelled."))
    def cancel_order(session_id: SessionId,
                     order_id: Annotated[str, Field(description="The order_id from place_order.")],
                     ctx: Context) -> dict[str, Any]:
        return run(ctx, lambda who: service.cancel_order(who, session_id, order_id).to_dict())

    @server.tool(annotations=READ, description=(
        "List a session's orders, oldest first. status: 'all' (default), 'open' (still "
        "working, status accepted), 'closed' (everything else), or one of accepted, "
        "filled, cancelled, expired, rejected. A rejected, expired or cancelled order "
        "carries its reason. A market order the book could only part fill is 'filled' "
        "with filled_quantity below quantity and a reason starting 'partial'; the rest "
        "was dropped, not left working."))
    def list_orders(session_id: SessionId,
                    status: Annotated[Optional[Literal["all", "open", "closed", "accepted",
                                                       "filled", "cancelled", "expired",
                                                       "rejected"]],
                                      Field(description="all, open, closed, or one status.")] = None,
                    ctx: Context = None,  # type: ignore[assignment]
                    ) -> dict[str, Any]:
        return run(ctx, lambda who: {"orders": [o.to_dict() for o in service.orders(who, session_id, status)]})

    @server.tool(annotations=READ, description=(
        "List a session's fills (executions), oldest first, optionally from one trading "
        "day on. liquidity 'taker' is a market or marketable order filled at the start "
        "of a step; 'resting' is a limit order filled at its price after a step."))
    def list_fills(session_id: SessionId,
                   since_day: Annotated[int, Field(
                       ge=0, description="Only fills on this trading day or later.")] = 0,
                   ctx: Context = None,  # type: ignore[assignment]
                   ) -> dict[str, Any]:
        return run(ctx, lambda who: {"fills": [f.to_dict() for f in service.fills(who, session_id, since_day)]})

    @server.tool(annotations=READ, description=(
        "Price history for one ticker: OHLCV bars, oldest first. resolution 'day' gives "
        "one bar per trading day for every session traded; 'step' gives one bar per "
        f"step (ticks_per_step minutes) for the last {STEP_BAR_SESSIONS} sessions only. "
        "Today's bar is included up to the current step. since_day drops earlier days; "
        "limit keeps only the most recent bars. Prices per share, volume in shares. A "
        "step bar's `step` is the clock step after that step ran."))
    def get_bars(session_id: SessionId,
                 ticker: Annotated[str, Field(description="One of the session's tickers.")],
                 resolution: Annotated[Literal["day", "step"], Field(
                     description="day or step.")] = "day",
                 since_day: Annotated[int, Field(ge=0, description="Only bars on this day or later.")] = 0,
                 limit: Annotated[Optional[int], Field(
                     ge=1, description="Keep the most recent this many bars.")] = None,
                 ctx: Context = None,  # type: ignore[assignment]
                 ) -> dict[str, Any]:
        return run(ctx, lambda who: {"bars": [b.to_dict() for b in service.bars(
            who, session_id, ticker, resolution, since_day, limit)]})

    @server.tool(annotations=WRITE, description=(
        "Move simulated time forward: the ONLY way time passes. Queued market orders "
        "fill at the start of the first step, prices move, resting limits are checked "
        "after each step, and day orders expire at the close. "
        "until='steps' runs `steps` steps of ticks_per_step minutes each, crossing the "
        "close into the next trading day as needed. until='close' runs to the end of "
        "the current session. until='next_open' runs to the start of the next session, "
        "so you can place orders before it trades. With until='close' or 'next_open', "
        "steps says how many times (steps=5, until='next_open' skips five days). "
        f"At most 20 sessions ({MAX_TICKS_PER_ADVANCE} ticks) per call. Returns the new "
        "clock, the fills and expiries this call produced, and a fresh observation."))
    def advance(session_id: SessionId,
                steps: Annotated[int, Field(ge=1, description=(
                    "Steps to run, or with until='close'/'next_open' how many times."))] = 1,
                until: Annotated[Literal["steps", "close", "next_open"], Field(
                    description="steps, close or next_open.")] = "steps",
                ctx: Context = None,  # type: ignore[assignment]
                ) -> dict[str, Any]:
        return run(ctx, lambda who: service.advance(who, session_id, steps, until).to_dict())

    @server.tool(annotations=WRITE, description=(
        "Copy a session's full state (market, account, orders, clock) into a new "
        "session you own, to try an alternative from the same point. Returns the new "
        "session's info with parent_session_id set. The two evolve independently and "
        "deterministically: the same calls on each give the same results."))
    def fork_session(session_id: SessionId,
                     label: Annotated[str, Field(description="Free text for the fork.")] = "",
                     ctx: Context = None,  # type: ignore[assignment]
                     ) -> dict[str, Any]:
        return run(ctx, lambda who: service.fork(who, session_id, label).to_dict())

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True,
                                             idempotentHint=False, openWorldHint=False),
                 description=(
        "End a session and get its report: the final account, the number of fills, the "
        "trading days it ran, its state_hash, and caveats computed for this session. "
        "Every order still open is cancelled (reason 'session closed'). "
        "Pass the caveats on with any result; a summary without them is a misreport. "
        "A closed session can still be observed but refuses orders and advance."))
    def close_session(session_id: SessionId, ctx: Context) -> dict[str, Any]:
        return run(ctx, lambda who: service.close(who, session_id).to_dict())

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
    parser.add_argument("--http", action="store_true",
                        help="Serve streamable HTTP instead of stdio.")
    parser.add_argument("--host", default="127.0.0.1", help="With --http: bind address.")
    parser.add_argument("--port", type=int, default=8766, help="With --http: port (default 8766).")
    parser.add_argument("--path", default="/mcp", help="With --http: endpoint path (default /mcp).")
    args = parser.parse_args(argv)
    if args.service:
        service = _load_factory(args.service)()
    else:
        from tradefloor.serve.http import default_service
        service = default_service(args.store)
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
    if not args.http:
        create_server(service, owner=args.owner).run("stdio")
        return
    import uvicorn

    from tradefloor.serve.http import is_loopback, loopback_guard
    fixed = args.owner
    app = create_http_app(service, owner_resolver=lambda request: fixed, path=args.path,
                          middleware=loopback_guard(args.host))
    if not is_loopback(args.host):
        print(f"WARNING: serving MCP on {args.host} with no authentication. Everyone who can "
              f"reach this port trades as owner {fixed!r}.", file=sys.stderr)
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":  # pragma: no cover
    main()
