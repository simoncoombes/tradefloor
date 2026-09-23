"""The HTTP transport of the trading session server.

Two surfaces over one `SessionService` (docs/serve/CONTRACT.md, section 6):

- NATIVE routes under `/v1/sessions...`, one route per service call, with
  OpenAPI docs at `/docs`. The request and response bodies are the contract
  types in `tradefloor.serve.types`, as JSON.
- A BROKER-SHAPED facade under `/broker/{session_id}/v2/...` that follows the
  shape of Alpaca's v2 trading API (account, positions, orders, clock,
  calendar, assets, fill activities) and a few of its market-data routes,
  closely enough that a bot written against that API can be pointed at a
  session by changing its base URL. What is and is not supported is listed in
  docs/serve/TRANSPORTS.md; anything unsupported is REFUSED with a message
  naming what is, never silently approximated.

Errors are `{"code", "message"}` with the HTTP status the contract assigns to
the code (`STATUS_BY_CODE`), on both surfaces.

FastAPI is an optional dependency. Nothing in this module imports it at import
time; `create_app` does. `import tradefloor.serve` never pulls it in.

`create_app(service, *, owner_resolver=None, middleware=())` is the seam the
hosted layer wraps: `owner_resolver(request) -> str` maps a request to an
owner (default: every request is owner "local") and raises
`ServeError("unauthorized", ...)` to refuse it.
"""

# No `from __future__ import annotations` here, on purpose: the routes are
# closures over models built inside `create_app`, and FastAPI resolves string
# annotations against module globals, where those models do not exist.

import contextlib
import dataclasses
import inspect
import logging
import math
import threading
import typing
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Literal, Optional, Sequence

from tradefloor.serve.types import (
    CONTRACT_VERSION,
    AdvanceResult,
    Clock,
    Fill,
    Observation,
    Order,
    OrderRequest,
    ServeError,
    SessionConfig,
    SessionInfo,
    SessionReport,
    SessionService,
)

log = logging.getLogger("tradefloor.serve.http")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_STORE = Path("~/.tradefloor/sessions")
LOCAL_OWNER = "local"

#: Contract limits, as the contract states them. A hosted server may cap lower.
TICKS_PER_SESSION = 390
MAX_UNIVERSE = 40
MAX_SESSIONS_PER_ADVANCE = 20
MAX_TICKS_PER_ADVANCE = MAX_SESSIONS_PER_ADVANCE * TICKS_PER_SESSION

#: HTTP status by error code (CONTRACT.md section 6).
STATUS_BY_CODE = {
    "invalid_request": 400,
    "invalid_order": 400,
    "unauthorized": 401,
    "insufficient_buying_power": 403,
    "not_found": 404,
    "conflict": 409,
    "session_closed": 409,
    "rate_limited": 429,
    "quota_exceeded": 429,
    "internal": 500,
}


def http_status(code: str) -> int:
    return STATUS_BY_CODE.get(code, 500)


# -- shared with the MCP transport ---------------------------------------------


def default_service(store: "str | Path | None" = None) -> SessionService:
    """The self-run service: `LocalSessionService` over a `FileStore`.

    Imported here, not at module import, so the transports can be built and
    tested against any `SessionService` without the core layer.
    """
    try:
        from tradefloor.serve.core import LocalSessionService
        from tradefloor.serve.store import FileStore
    except ImportError as exc:  # pragma: no cover - depends on the core layer landing
        raise RuntimeError(
            "tradefloor.serve.core is not available in this install, so there is "
            "no local session service to serve. Build against a SessionService "
            f"of your own with create_app(service). ({exc})") from exc
    root = Path(store if store is not None else DEFAULT_STORE).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    return LocalSessionService(FileStore(root))


def _envelope_caveats() -> list[str]:
    """Read from the realism envelope on every call, never typed here."""
    try:
        from tradefloor import envelope
        cert = envelope.certified()
    except Exception as exc:  # the engine is not importable, or the envelope moved
        return [f"The realism envelope could not be read ({type(exc).__name__}: {exc}); "
                "treat every result from this server as uncertified."]
    out = [
        f"Realism is certified for preset {cert['preset']} over "
        f"{cert['certified_horizon_days']} trading days. A session that runs longer, "
        "or uses another preset, is outside what was measured.",
    ]
    for gap in cert.get("gaps", []):
        out.append(f"Known gap '{gap['id']}': {gap['summary']}. It rules out "
                   f"{gap['forbids']}.")
    return out


def describe_payload() -> dict[str, Any]:
    """What the server is, its limits and its caveats. Served by the MCP
    `describe` tool and by `GET /v1/describe`."""
    return {
        "what_it_is": (
            "A trading session server over the tradefloor market simulator. You open "
            "a session (a simulated market, a roster of tickers and a cash account), "
            "look at it, place and cancel orders, and move time forward yourself. "
            "Sessions persist: a crashed client resumes where it was, and a fork "
            "copies a session to try an alternative from the same point."
        ),
        "contract_version": CONTRACT_VERSION,
        "time": (
            "Time moves ONLY when you call advance. There is no wall clock. One tick "
            f"is one simulated minute; a trading session is {TICKS_PER_SESSION} ticks "
            "(09:30 to 16:00). One step is `ticks_per_step` ticks (a session setting). "
            "advance(steps=n) runs n steps, crossing the close and the next open as "
            "needed; until='close' runs to the end of the current session; "
            "until='next_open' runs to the first step of the next one; with either, "
            "steps=n does it n times."
        ),
        "orders": {
            "market": ("Queued, then filled at the START of the next step, before the "
                       "market moves, at the impact-aware price the book gives. Market "
                       "orders move the market."),
            "limit": ("Rest on the server. After each step a resting buy fills in full "
                      "at its limit if the step's low reached it, a sell if the step's "
                      "high did. No partial fills. Resting fills do NOT move the market. "
                      "A limit that is marketable when placed is a market order capped "
                      "at the limit."),
            "time_in_force": "day expires at the session close; gtc persists.",
            "client_order_id": ("An idempotency key per session: resending the same "
                                "order with the same id returns the original; a "
                                "different order under the same id is a conflict."),
            "shorting": "Allowed within max_leverage.",
            "leverage": "Gross exposure divided by net worth, capped by max_leverage.",
        },
        "units": {
            "quantity": "shares",
            "prices": "currency units per share",
            "cash, net_worth, pnl": "currency units",
            "clock.day": "trading day index from 0",
            "clock.tick": f"minutes since the open, 0..{TICKS_PER_SESSION}",
            "clock.step": "steps taken since the session opened",
        },
        "limits": {
            "universe_size": [1, MAX_UNIVERSE],
            "ticks_per_session": TICKS_PER_SESSION,
            "max_sessions_per_advance": MAX_SESSIONS_PER_ADVANCE,
            "max_ticks_per_advance": MAX_TICKS_PER_ADVANCE,
            "presets": "named presets only; no free model coefficients",
            "note": "Self-run limits from the contract. A hosted server may cap lower.",
        },
        "errors": {code: status for code, status in STATUS_BY_CODE.items()},
        "caveats": [
            "The price process is a known model, not a forecast. Doing well here "
            "means doing well against this model; it does not transfer to real returns.",
            "Single venue, zero latency, no strategic counterparties: you trade "
            "against a market maker and aggregate flow, not agents that adapt to you.",
            "Resting limit fills are simulated by the server after each step and do "
            "not feed the engine's order flow in contract 0.1, so they cannot move the "
            "market the way a real resting order would.",
            "Bots do not meet each other in the book: every session is its own market.",
            "A session report carries caveats computed for that session. A summary "
            "that drops them is a misreport.",
            *_envelope_caveats(),
        ],
    }


def request_api_key(request: Any) -> Optional[str]:
    """The API key a request carries, for an `owner_resolver` to look up.

    Checked in order: `Authorization: Bearer <key>`, `X-API-Key`, then the two
    headers an Alpaca client sends (`APCA-API-SECRET-KEY`, then
    `APCA-API-KEY-ID`), so a broker client pointed at the facade authenticates
    without code changes. Returns None when there is none.
    """
    headers = request.headers
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip() or None
    for name in ("x-api-key", "apca-api-secret-key", "apca-api-key-id"):
        value = headers.get(name)
        if value:
            return value
    return None


def error_response(exc: ServeError) -> Any:
    """The JSON response for a ServeError, for middleware that refuses a
    request itself (an exception raised inside middleware does not reach the
    app's error handlers)."""
    from fastapi.responses import JSONResponse

    headers: dict[str, str] = {}
    if exc.code == "unauthorized":
        headers["WWW-Authenticate"] = "Bearer"
    retry_after = getattr(exc, "retry_after", None)
    if retry_after is not None and exc.code in ("rate_limited", "quota_exceeded"):
        headers["Retry-After"] = str(max(0, math.ceil(float(retry_after))))
    return JSONResponse(exc.to_dict(), status_code=http_status(exc.code), headers=headers)


# -- request bodies ------------------------------------------------------------


def _body_model(cls: type, name: str) -> Any:
    """A pydantic model mirroring a contract dataclass, refusing unknown fields.

    An unknown field is refused rather than ignored, because a misspelt
    `univers_size` that silently falls back to the default is a session nobody
    asked for.
    """
    from pydantic import ConfigDict, create_model

    hints = typing.get_type_hints(cls)
    spec: dict[str, Any] = {}
    for f in dataclasses.fields(cls):
        if f.default is not dataclasses.MISSING:
            default = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            default = f.default_factory()  # type: ignore[misc]
        else:
            default = ...
        spec[f.name] = (hints[f.name], default)
    return create_model(name, __config__=ConfigDict(extra="forbid"), **spec)


def _validation_message(errors: Iterable[dict[str, Any]]) -> str:
    parts = []
    for err in errors:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p not in ("body",))
        parts.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
    return "; ".join(parts) or "invalid request"


# -- the app -------------------------------------------------------------------


OwnerResolver = Callable[[Any], Any]


def _default_resolver(owner: str) -> OwnerResolver:
    def resolve(request: Any) -> str:
        return owner
    return resolve


def create_app(service: SessionService, *, owner_resolver: Optional[OwnerResolver] = None,
               middleware: Sequence[Any] = (), serialize: bool = True,
               describe: Optional[Callable[[], dict[str, Any]]] = None,
               title: str = "tradefloor trading session server") -> Any:
    """Build the FastAPI app over `service`.

    owner_resolver: `(request) -> owner`, sync or async. Default: every request
        is owner "local". Raise `ServeError("unauthorized", ...)` to refuse a
        request (401), or `ServeError("rate_limited" | "quota_exceeded", ...)`
        for 429; a `retry_after` attribute on the error becomes `Retry-After`.
    middleware: Starlette `Middleware(...)` entries, `(cls, options)` pairs, or
        bare middleware classes, applied outermost first.
    serialize: hold one lock around every service call. On by default because
        contract 0.1 does not say a SessionService is thread-safe and FastAPI
        runs sync routes on a thread pool.
    describe: what `GET /v1/describe` serves (default `describe_payload`). A
        wrapper that caps limits lower passes its own, so clients read the
        limits that actually apply.

    The service, the lock and the resolver are on `app.state` for a wrapper.
    """
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.middleware import Middleware

    resolver = owner_resolver or _default_resolver(LOCAL_OWNER)
    lock: Any = threading.Lock() if serialize else contextlib.nullcontext()

    stack = []
    for m in middleware:
        if isinstance(m, Middleware):
            stack.append(m)
        elif isinstance(m, tuple):
            cls, opts = m
            stack.append(Middleware(cls, **(opts or {})))
        else:
            stack.append(Middleware(m))

    app = FastAPI(
        title=title,
        version=CONTRACT_VERSION,
        description=(
            "Open a simulated market session, observe it, place orders, and move "
            "time forward. Native routes are under `/v1`; a broker-shaped facade "
            "(Alpaca v2 subset) is under `/broker/{session_id}/v2`. Errors are "
            "`{\"code\", \"message\"}`. See `GET /v1/describe` for the semantics "
            "and caveats."),
        middleware=stack,
    )
    app.state.service = service
    app.state.service_lock = lock
    app.state.owner_resolver = resolver

    @app.exception_handler(ServeError)
    async def _on_serve_error(request: Any, exc: ServeError) -> Any:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    async def _on_invalid(request: Any, exc: RequestValidationError) -> Any:
        return error_response(ServeError("invalid_request", _validation_message(exc.errors())))

    @app.exception_handler(StarletteHTTPException)
    async def _on_http(request: Any, exc: StarletteHTTPException) -> Any:
        code = {401: "unauthorized", 404: "not_found", 409: "conflict", 429: "rate_limited"}.get(
            exc.status_code, "invalid_request" if exc.status_code < 500 else "internal")
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return JSONResponse({"code": code, "message": detail}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    @app.exception_handler(Exception)
    async def _on_crash(request: Any, exc: Exception) -> Any:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return error_response(ServeError(
            "internal", f"unexpected {type(exc).__name__}: {exc}. This is a bug in the "
                        "server; report it with the request that caused it."))

    from starlette.concurrency import run_in_threadpool

    # A coroutine function is awaited here; anything else runs on the thread
    # pool (it may block on a key lookup), and an awaitable it returns (an
    # object with an async __call__) is awaited after.
    is_async = inspect.iscoroutinefunction(resolver)

    from fastapi import Request

    async def owner_dep(request: Request) -> str:
        if is_async:
            owner = await resolver(request)
        else:
            owner = await run_in_threadpool(resolver, request)
            if inspect.isawaitable(owner):
                owner = await owner
        if not isinstance(owner, str) or not owner:
            raise ServeError("internal", "the owner resolver returned no owner")
        request.state.owner = owner
        return owner

    def call(method: str, *args: Any, **kwargs: Any) -> Any:
        with lock:
            return getattr(service, method)(*args, **kwargs)

    app.include_router(_native_router(call, owner_dep, describe or describe_payload))
    app.include_router(_broker_router(call, owner_dep), prefix="/broker/{session_id}/v2")
    return app


# -- native routes -------------------------------------------------------------


def _native_router(call: Callable[..., Any], owner_dep: Callable[..., Any],
                   describe_fn: Callable[[], dict[str, Any]]) -> Any:
    from fastapi import APIRouter, Body, Depends, Path as PathParam, Query

    SessionConfigBody = _body_model(SessionConfig, "SessionConfigBody")
    OrderRequestBody = _body_model(OrderRequest, "OrderRequestBody")

    from pydantic import BaseModel, ConfigDict, Field

    class AdvanceBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        steps: int = Field(1, ge=1, description=(
            "How many times: steps of the session's ticks_per_step ticks when "
            "until='steps', or closes / next opens to run to otherwise. One call may run "
            f"at most {MAX_TICKS_PER_ADVANCE} ticks ({MAX_SESSIONS_PER_ADVANCE} sessions)."))
        until: Literal["steps", "close", "next_open"] = Field("steps", description=(
            "'steps' runs `steps` steps; 'close' runs to the end of the current session; "
            "'next_open' runs to the first step of the next session."))

    class ForkBody(BaseModel):
        model_config = ConfigDict(extra="forbid")
        label: str = ""

    Owner = Depends(owner_dep)
    SID = PathParam(..., description="The session id returned by POST /v1/sessions.")
    r = APIRouter()

    @r.get("/", include_in_schema=False)
    def root() -> dict[str, Any]:
        return {"name": "tradefloor trading session server", "contract_version": CONTRACT_VERSION,
                "docs": "/docs", "openapi": "/openapi.json", "describe": "/v1/describe",
                "native": "/v1/sessions", "broker_facade": "/broker/{session_id}/v2"}

    @r.get("/v1/health", tags=["server"], summary="Liveness check (no owner needed)")
    def health() -> dict[str, Any]:
        return {"ok": True, "contract_version": CONTRACT_VERSION}

    @r.get("/v1/describe", tags=["server"],
           summary="What this server is, its limits and its caveats")
    def describe() -> dict[str, Any]:
        return describe_fn()

    @r.post("/v1/sessions", tags=["sessions"], status_code=201, response_model=SessionInfo,
            summary="Open a session",
            description="Builds a market for a NAMED preset, seed and roster and opens day 0. "
                        "Every field is optional; an unknown field is refused.")
    def open_session(body: Optional[SessionConfigBody] = Body(None), owner: str = Owner) -> Any:  # type: ignore[valid-type]
        config = SessionConfig.from_dict(body.model_dump()) if body is not None else SessionConfig()
        return call("open", owner, config).to_dict()

    @r.get("/v1/sessions", tags=["sessions"], response_model=list[SessionInfo],
           summary="List your sessions")
    def list_sessions(owner: str = Owner) -> Any:
        return [s.to_dict() for s in call("list", owner)]

    @r.get("/v1/sessions/{session_id}", tags=["sessions"], response_model=SessionInfo,
           summary="One session's info")
    def session_info(session_id: str = SID, owner: str = Owner) -> Any:
        return call("info", owner, session_id).to_dict()

    @r.get("/v1/sessions/{session_id}/observation", tags=["sessions"], response_model=Observation,
           summary="Observe the market and the account",
           description="Clock, a quote per ticker, the VIX, a subset of the economy, the "
                       "account, positions, open orders, headlines and the state hash.")
    def observe(session_id: str = SID, owner: str = Owner) -> Any:
        return call("observe", owner, session_id).to_dict()

    @r.post("/v1/sessions/{session_id}/orders", tags=["orders"], status_code=201,
            response_model=Order, summary="Place an order",
            description="Market orders fill at the start of the next step; limit orders rest "
                        "on the server. Resending the same client_order_id with the same body "
                        "returns the original order.")
    def place_order(body: OrderRequestBody, session_id: str = SID, owner: str = Owner) -> Any:  # type: ignore[valid-type]
        request = OrderRequest.from_dict(body.model_dump())
        return call("place_order", owner, session_id, request).to_dict()

    @r.get("/v1/sessions/{session_id}/orders", tags=["orders"], response_model=list[Order],
           summary="List orders")
    def list_orders(session_id: str = SID, owner: str = Owner,
                    status: Optional[Literal["accepted", "filled", "cancelled", "expired", "rejected"]]
                    = Query(None, description="Only orders with this status.")) -> Any:
        return [o.to_dict() for o in call("orders", owner, session_id, status)]

    @r.get("/v1/sessions/{session_id}/orders/{order_id}", tags=["orders"], response_model=Order,
           summary="One order")
    def get_order(order_id: str, session_id: str = SID, owner: str = Owner) -> Any:
        for o in call("orders", owner, session_id):
            if o.order_id == order_id:
                return o.to_dict()
        raise ServeError("not_found", f"no order {order_id!r}")

    @r.delete("/v1/sessions/{session_id}/orders/{order_id}", tags=["orders"], response_model=Order,
              summary="Cancel an order")
    def cancel_order(order_id: str, session_id: str = SID, owner: str = Owner) -> Any:
        return call("cancel_order", owner, session_id, order_id).to_dict()

    @r.get("/v1/sessions/{session_id}/fills", tags=["orders"], response_model=list[Fill],
           summary="List fills")
    def list_fills(session_id: str = SID, owner: str = Owner,
                   since_day: int = Query(0, ge=0, description="Only fills on this day or later.")) -> Any:
        return [f.to_dict() for f in call("fills", owner, session_id, since_day)]

    @r.post("/v1/sessions/{session_id}/advance", tags=["time"], response_model=AdvanceResult,
            summary="Move time forward",
            description="The only way time moves. Returns the new clock, the fills and "
                        "expiries that happened, and a fresh observation.")
    def advance(body: Optional[AdvanceBody] = Body(None), session_id: str = SID,
                owner: str = Owner) -> Any:
        b = body or AdvanceBody()
        return call("advance", owner, session_id, b.steps, b.until).to_dict()

    @r.post("/v1/sessions/{session_id}/fork", tags=["sessions"], status_code=201,
            response_model=SessionInfo, summary="Fork a session",
            description="Copies the session's full state into a new session of yours.")
    def fork(body: Optional[ForkBody] = Body(None), session_id: str = SID, owner: str = Owner) -> Any:
        return call("fork", owner, session_id, (body or ForkBody()).label).to_dict()

    @r.post("/v1/sessions/{session_id}/close", tags=["sessions"], response_model=SessionReport,
            summary="Close a session and get its report",
            description="Ends the session. The report's caveats are computed for this session.")
    def close(session_id: str = SID, owner: str = Owner) -> Any:
        return call("close", owner, session_id).to_dict()

    return r


# -- the broker-shaped facade ----------------------------------------------------
#
# Alpaca's v2 trading API, the subset in docs/serve/TRANSPORTS.md. Three
# translations run through all of it:
#
# TIME. The simulation has trading days and ticks, not dates. Day d is the
# d-th weekday from SIM_EPOCH and tick t is t minutes after 09:30, in a fixed
# UTC-05:00 offset: no holidays, no daylight saving. The dates are synthetic
# and say nothing about when anything happened.
#
# IDS. Alpaca ids are UUIDs and clients parse them as such. A service id that
# is already a UUID (uuid4 hex) is shown in canonical form; any other id is
# mapped to a uuid5, and looked up again by recomputing, so nothing is stored.
#
# NUMBERS. Alpaca sends quantities and money as strings; so does the facade,
# with repr() so a float round-trips exactly.

SIM_EPOCH = date(2000, 1, 3)  # a Monday; day 0
SIM_TZ = timezone(timedelta(hours=-5))
_ID_NS = uuid.UUID("6f1c0b52-7d0a-4bb0-9a0e-2f3c7e1d9a11")

_ORDER_STATUS = {"accepted": "new", "filled": "filled", "cancelled": "canceled",
                 "expired": "expired", "rejected": "rejected"}
_OPEN = {"accepted"}


def sim_date(day: int) -> date:
    weeks, rem = divmod(day, 5)
    return SIM_EPOCH + timedelta(days=7 * weeks + rem)


def sim_datetime(day: int, tick: int) -> datetime:
    d = sim_date(day)
    return datetime(d.year, d.month, d.day, 9, 30, tzinfo=SIM_TZ) + timedelta(minutes=tick)


def _day_of(d: date) -> int:
    """The trading-day index of a date, rounding a weekend forward to Monday."""
    delta = (d - SIM_EPOCH).days
    weeks, rem = divmod(delta, 7)
    return weeks * 5 + min(rem, 5)


def _z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _et(dt: datetime) -> str:
    return dt.isoformat()


def _num(x: float) -> str:
    return repr(float(x))


def _opt_num(x: Optional[float]) -> Optional[str]:
    return None if x is None else _num(x)


def asset_uuid(ticker: str) -> str:
    return str(uuid.uuid5(_ID_NS, f"asset/{ticker}"))


def account_uuid(session_id: str) -> str:
    return str(uuid.uuid5(_ID_NS, f"account/{session_id}"))


def order_uuid(session_id: str, order_id: str) -> str:
    try:
        return str(uuid.UUID(order_id))
    except (ValueError, AttributeError, TypeError):
        return str(uuid.uuid5(_ID_NS, f"order/{session_id}/{order_id}"))


def _parse_num(value: Any, name: str) -> float:
    if isinstance(value, bool):
        raise ServeError("invalid_order", f"{name} must be a number, got {value!r}")
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ServeError("invalid_order", f"{name} must be a number, got {value!r}") from None


def _parse_time(value: Optional[str], name: str) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            dt = datetime.combine(date.fromisoformat(value), datetime.min.time())
        except ValueError:
            raise ServeError("invalid_request", f"{name} must be an RFC 3339 time or a date, "
                                                f"got {value!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=SIM_TZ)
    return dt


def _session_day(o: Order) -> int:
    """The session an order belongs to: one placed while the market is closed
    belongs to the next session."""
    at = o.submitted_at
    return at.day if at.market_open else at.day + 1


def alpaca_order(session_id: str, o: Order, fills: Sequence[Fill] = ()) -> dict[str, Any]:
    """An Order in Alpaca's shape. `fills` are this order's fills, if known."""
    oid = order_uuid(session_id, o.order_id)
    submitted = sim_datetime(o.submitted_at.day, o.submitted_at.tick)
    close = sim_datetime(_session_day(o), TICKS_PER_SESSION)
    filled = sim_datetime(fills[-1].at.day, fills[-1].at.tick) if fills else None
    expired = close if o.status == "expired" else None
    updated = max(t for t in (submitted, filled, expired) if t is not None)
    return {
        "id": oid,
        "client_order_id": o.client_order_id or oid,
        "created_at": _z(submitted),
        "updated_at": _z(updated),
        "submitted_at": _z(submitted),
        "filled_at": _z(filled) if filled else None,
        "expired_at": _z(expired) if expired else None,
        "expires_at": _z(close) if o.time_in_force == "day" else None,
        "canceled_at": None,
        "failed_at": None,
        "replaced_at": None,
        "replaced_by": None,
        "replaces": None,
        "asset_id": asset_uuid(o.ticker),
        "symbol": o.ticker,
        "asset_class": "us_equity",
        "notional": None,
        "qty": _num(o.quantity),
        "filled_qty": _num(o.filled_quantity),
        "filled_avg_price": _opt_num(o.avg_fill_price),
        "order_class": "simple",
        "order_type": o.type,
        "type": o.type,
        "side": o.side,
        "time_in_force": o.time_in_force,
        "limit_price": _opt_num(o.limit_price),
        "stop_price": None,
        "status": _ORDER_STATUS.get(o.status, o.status),
        "extended_hours": False,
        "legs": None,
        "trail_percent": None,
        "trail_price": None,
        "hwm": None,
    }


def alpaca_clock(clock: Clock, *, closed_session: bool = False) -> dict[str, Any]:
    now = sim_datetime(clock.day, clock.tick)
    is_open = clock.market_open and not closed_session
    if clock.market_open:
        next_open = sim_datetime(clock.day + 1, 0)
        next_close = sim_datetime(clock.day, TICKS_PER_SESSION)
    else:
        next_open = sim_datetime(clock.day + 1, 0)
        next_close = sim_datetime(clock.day + 1, TICKS_PER_SESSION)
    return {"timestamp": _et(now), "is_open": is_open,
            "next_open": _et(next_open), "next_close": _et(next_close)}


def alpaca_account(info: SessionInfo, obs: Observation) -> dict[str, Any]:
    a = obs.account
    last = {q.ticker: q for q in obs.quotes}
    long_mv = sum(p.market_value for p in obs.positions if p.quantity > 0)
    short_mv = sum(p.market_value for p in obs.positions if p.quantity < 0)
    last_equity = a.cash + sum(p.quantity * last[p.ticker].prev_close
                               for p in obs.positions if p.ticker in last)
    cap = info.config.max_leverage
    closed = info.status == "closed"
    out: dict[str, Any] = {
        "id": account_uuid(info.session_id),
        "account_number": "TF" + info.session_id[:10].upper(),
        "status": "ACTIVE",
        "currency": "USD",
        "cash": _num(a.cash),
        "portfolio_value": _num(a.net_worth),
        "equity": _num(a.net_worth),
        "last_equity": _num(last_equity),
        "long_market_value": _num(long_mv),
        "short_market_value": _num(short_mv),
        "non_marginable_buying_power": _num(max(0.0, a.cash)),
        "accrued_fees": "0",
        "pending_transfer_in": "0",
        "pending_transfer_out": "0",
        "sma": "0",
        "trading_blocked": closed,
        "transfers_blocked": True,
        "account_blocked": False,
        "trade_suspended_by_user": False,
        "shorting_enabled": True,
        "created_at": _z(sim_datetime(0, 0)),
        "options_approved_level": 0,
        "options_trading_level": 0,
        "options_buying_power": "0",
    }
    if cap is not None:
        bp = 0.0 if closed else max(0.0, cap * a.net_worth - a.gross_exposure)
        out.update(buying_power=_num(bp), regt_buying_power=_num(bp), multiplier=_num(cap))
    return out


def alpaca_position(p: Any, quote: Any, open_orders: Sequence[Order]) -> dict[str, Any]:
    side = "long" if p.quantity > 0 else "short"
    cost = p.quantity * p.avg_price
    last = quote.last if quote is not None else (p.market_value / p.quantity if p.quantity else 0.0)
    prev = quote.prev_close if quote is not None else last
    closing = sum(o.quantity - o.filled_quantity for o in open_orders
                  if o.ticker == p.ticker and o.side == ("sell" if p.quantity > 0 else "buy"))
    available = max(0.0, abs(p.quantity) - closing) * (1 if p.quantity > 0 else -1)
    intraday = p.quantity * (last - prev)
    return {
        "asset_id": asset_uuid(p.ticker),
        "symbol": p.ticker,
        "exchange": "",
        "asset_class": "us_equity",
        "asset_marginable": True,
        "avg_entry_price": _num(p.avg_price),
        "qty": _num(p.quantity),
        "qty_available": _num(available),
        "side": side,
        "market_value": _num(p.market_value),
        "cost_basis": _num(cost),
        "unrealized_pl": _num(p.unrealised_pnl),
        "unrealized_plpc": _num(p.unrealised_pnl / abs(cost) if cost else 0.0),
        "unrealized_intraday_pl": _num(intraday),
        "unrealized_intraday_plpc": _num(intraday / abs(p.quantity * prev) if p.quantity * prev else 0.0),
        "current_price": _num(last),
        "lastday_price": _num(prev),
        "change_today": _num((last - prev) / prev if prev else 0.0),
    }


def alpaca_asset(ticker: str) -> dict[str, Any]:
    return {
        "id": asset_uuid(ticker),
        "class": "us_equity",
        "exchange": "",
        "symbol": ticker,
        "name": ticker,
        "status": "active",
        "tradable": True,
        "marginable": True,
        "shortable": True,
        "easy_to_borrow": True,
        "fractionable": True,
        "attributes": [],
    }


def alpaca_fill_activity(session_id: str, f: Fill, order: Optional[Order], cum_qty: float,
                         index: int) -> dict[str, Any]:
    when = sim_datetime(f.at.day, f.at.tick)
    total = order.quantity if order is not None else f.quantity
    leaves = max(0.0, total - cum_qty)
    stamp = when.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S") + "000"
    return {
        "id": f"{stamp}::{uuid.uuid5(_ID_NS, f'fill/{session_id}/{index}')}",
        "account_id": account_uuid(session_id),
        "activity_type": "FILL",
        "transaction_time": _z(when),
        "type": "fill" if leaves == 0 else "partial_fill",
        "price": _num(f.price),
        "qty": _num(f.quantity),
        "side": f.side,
        "symbol": f.ticker,
        "leaves_qty": _num(leaves),
        "order_id": order_uuid(session_id, f.order_id),
        "cum_qty": _num(cum_qty),
        "order_status": "filled" if leaves == 0 else "partially_filled",
    }


def _fill_activities(session_id: str, fills: Sequence[Fill], orders: Sequence[Order]) -> list[dict[str, Any]]:
    by_id = {o.order_id: o for o in orders}
    cum: dict[str, float] = {}
    out = []
    for i, f in enumerate(fills):
        cum[f.order_id] = cum.get(f.order_id, 0.0) + f.quantity
        out.append(alpaca_fill_activity(session_id, f, by_id.get(f.order_id), cum[f.order_id], i))
    return out


def order_request_from_alpaca(body: dict[str, Any]) -> OrderRequest:
    """An Alpaca order body as an OrderRequest, refusing what is not supported."""
    def refuse(msg: str) -> None:
        raise ServeError("invalid_order", msg)

    for key, why in (("notional", "notional (dollar-amount) orders are not supported; send qty"),
                     ("stop_price", "stop and stop-limit orders are not supported"),
                     ("trail_price", "trailing-stop orders are not supported"),
                     ("trail_percent", "trailing-stop orders are not supported"),
                     ("take_profit", "bracket orders are not supported"),
                     ("stop_loss", "bracket and OTO orders are not supported"),
                     ("legs", "multi-leg orders are not supported")):
        if body.get(key) not in (None, "", [], {}):
            refuse(f"{why}. This facade supports market and limit orders, "
                   "time_in_force day or gtc.")
    if body.get("extended_hours"):
        refuse("extended_hours orders are not supported: the simulated market has one "
               "session a day and no extended hours")
    if body.get("order_class") not in (None, "", "simple"):
        refuse(f"order_class {body.get('order_class')!r} is not supported; only simple orders are")
    symbol = body.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        refuse("symbol is required")
    if body.get("qty") in (None, ""):
        refuse("qty is required (notional orders are not supported)")
    qty = _parse_num(body.get("qty"), "qty")
    side = body.get("side")
    if side not in ("buy", "sell"):
        refuse(f"side must be buy or sell, got {side!r}")
    typ = body.get("type") or body.get("order_type") or "market"
    if typ in ("stop", "stop_limit", "trailing_stop"):
        refuse(f"{typ} orders are not supported; use market or limit")
    if typ not in ("market", "limit"):
        refuse(f"type must be market or limit, got {typ!r}")
    tif = body.get("time_in_force") or "day"
    if tif not in ("day", "gtc"):
        refuse(f"time_in_force {tif!r} is not supported; use day or gtc")
    limit = body.get("limit_price")
    limit_price = None if limit in (None, "") else _parse_num(limit, "limit_price")
    cid = body.get("client_order_id")
    if cid is not None and not isinstance(cid, str):
        refuse("client_order_id must be a string")
    return OrderRequest(ticker=symbol, side=side, quantity=qty, type=typ,  # type: ignore[arg-type]
                        limit_price=limit_price, time_in_force=tif,  # type: ignore[arg-type]
                        client_order_id=cid or None)


def _latest_trade(q: Any, now: datetime) -> dict[str, Any]:
    return {"t": _z(now), "p": q.last, "s": 0, "x": "", "i": 0, "c": [], "z": ""}


def _latest_quote(q: Any, now: datetime) -> dict[str, Any]:
    bid = q.bid if q.bid is not None else q.last
    ask = q.ask if q.ask is not None else q.last
    return {"t": _z(now), "bp": bid, "bs": 0, "bx": "", "ap": ask, "as": 0, "ax": "", "c": [], "z": ""}


def _daily_bar(q: Any, day: int) -> dict[str, Any]:
    d = sim_date(day)
    midnight = datetime(d.year, d.month, d.day, tzinfo=SIM_TZ)
    return {"t": _z(midnight), "o": q.day_open, "h": q.day_high, "l": q.day_low, "c": q.last,
            "v": q.volume, "n": 0, "vw": None}


def _snapshot(q: Any, clock: Clock) -> dict[str, Any]:
    now = sim_datetime(clock.day, clock.tick)
    return {"latestTrade": _latest_trade(q, now), "latestQuote": _latest_quote(q, now),
            "dailyBar": _daily_bar(q, clock.day)}


def _broker_router(call: Callable[..., Any], owner_dep: Callable[..., Any]) -> Any:
    from fastapi import APIRouter, Body, Depends, Query, Response
    from fastapi.responses import JSONResponse

    Owner = Depends(owner_dep)
    r = APIRouter(tags=["broker facade (Alpaca v2 subset)"])

    def _orders_with_fills(session_id: str, owner: str) -> tuple[list[Order], dict[str, list[Fill]]]:
        orders = call("orders", owner, session_id)
        by_order: dict[str, list[Fill]] = {}
        for f in call("fills", owner, session_id):
            by_order.setdefault(f.order_id, []).append(f)
        return orders, by_order

    def _find_order(session_id: str, owner: str, order_id: str) -> Order:
        try:
            wanted = str(uuid.UUID(order_id))
        except ValueError:
            wanted = None
        for o in call("orders", owner, session_id):
            if o.order_id == order_id or (wanted and order_uuid(session_id, o.order_id) == wanted):
                return o
        raise ServeError("not_found", f"order {order_id} not found")

    def _one_order(session_id: str, owner: str, o: Order) -> dict[str, Any]:
        fills = [f for f in call("fills", owner, session_id) if f.order_id == o.order_id]
        return alpaca_order(session_id, o, fills)

    def _tickers(session_id: str, owner: str) -> list[str]:
        return list(call("info", owner, session_id).tickers)

    def _resolve_symbol(session_id: str, owner: str, symbol_or_id: str) -> str:
        tickers = _tickers(session_id, owner)
        if symbol_or_id in tickers:
            return symbol_or_id
        for t in tickers:
            if asset_uuid(t) == symbol_or_id.lower():
                return t
        raise ServeError("not_found", f"asset {symbol_or_id} not found")

    # -- account, clock, calendar, assets --

    @r.get("/account", summary="Account (Alpaca GET /v2/account)")
    def account(session_id: str, owner: str = Owner) -> Any:
        info = call("info", owner, session_id)
        return alpaca_account(info, call("observe", owner, session_id))

    @r.get("/clock", summary="Market clock (Alpaca GET /v2/clock), in simulated time")
    def clock(session_id: str, owner: str = Owner) -> Any:
        info = call("info", owner, session_id)
        obs = call("observe", owner, session_id)
        return alpaca_clock(obs.clock, closed_session=info.status == "closed")

    @r.get("/calendar", summary="Trading days (Alpaca GET /v2/calendar), synthetic weekdays")
    def calendar(session_id: str, owner: str = Owner,
                 start: Optional[str] = Query(None, description="First date, YYYY-MM-DD."),
                 end: Optional[str] = Query(None, description="Last date, YYYY-MM-DD.")) -> Any:
        today = call("observe", owner, session_id).clock.day
        try:
            first = max(0, _day_of(date.fromisoformat(start[:10]))) if start else 0
            last = _day_of(date.fromisoformat(end[:10]) + timedelta(days=1)) - 1 if end else today + 252
        except ValueError:
            raise ServeError("invalid_request", "start and end must be dates, YYYY-MM-DD") from None
        last = min(last, first + 5000)
        out = []
        for d in range(first, last + 1):
            out.append({"date": sim_date(d).isoformat(), "open": "09:30", "close": "16:00",
                        "settlement_date": sim_date(d + 1).isoformat()})
        return out

    @r.get("/assets", summary="Assets (Alpaca GET /v2/assets): the session's roster")
    def assets(session_id: str, owner: str = Owner,
               status: Optional[str] = Query(None), asset_class: Optional[str] = Query(None)) -> Any:
        if status not in (None, "active") or asset_class not in (None, "us_equity"):
            return []
        return [alpaca_asset(t) for t in _tickers(session_id, owner)]

    @r.get("/assets/{symbol_or_asset_id}", summary="One asset (Alpaca GET /v2/assets/{symbol})")
    def asset(session_id: str, symbol_or_asset_id: str, owner: str = Owner) -> Any:
        return alpaca_asset(_resolve_symbol(session_id, owner, symbol_or_asset_id))

    # -- positions --

    def _positions(session_id: str, owner: str) -> list[dict[str, Any]]:
        obs = call("observe", owner, session_id)
        quotes = {q.ticker: q for q in obs.quotes}
        return [alpaca_position(p, quotes.get(p.ticker), obs.open_orders)
                for p in obs.positions if p.quantity != 0]

    @r.get("/positions", summary="Open positions (Alpaca GET /v2/positions)")
    def positions(session_id: str, owner: str = Owner) -> Any:
        return _positions(session_id, owner)

    @r.get("/positions/{symbol_or_asset_id}", summary="One position (Alpaca GET /v2/positions/{symbol})")
    def position(session_id: str, symbol_or_asset_id: str, owner: str = Owner) -> Any:
        symbol = _resolve_symbol(session_id, owner, symbol_or_asset_id)
        for p in _positions(session_id, owner):
            if p["symbol"] == symbol:
                return p
        raise ServeError("not_found", "position does not exist")

    def _close_position(session_id: str, owner: str, symbol: str, qty: Optional[str],
                        percentage: Optional[str]) -> dict[str, Any]:
        obs = call("observe", owner, session_id)
        held = next((p.quantity for p in obs.positions if p.ticker == symbol and p.quantity != 0), None)
        if held is None:
            raise ServeError("not_found", "position does not exist")
        size = abs(held)
        if qty not in (None, "") and percentage not in (None, ""):
            raise ServeError("invalid_request", "give qty or percentage, not both")
        if qty not in (None, ""):
            size = min(size, _parse_num(qty, "qty"))
        elif percentage not in (None, ""):
            pct = _parse_num(percentage, "percentage")
            if not 0 < pct <= 100:
                raise ServeError("invalid_request", f"percentage must be in (0, 100], got {pct:g}")
            size = size * pct / 100.0
        req = OrderRequest(ticker=symbol, side="sell" if held > 0 else "buy", quantity=size,
                           type="market", time_in_force="day")
        return _one_order(session_id, owner, call("place_order", owner, session_id, req))

    @r.delete("/positions/{symbol_or_asset_id}",
              summary="Close a position with a market order (Alpaca DELETE /v2/positions/{symbol})")
    def close_position(session_id: str, symbol_or_asset_id: str, owner: str = Owner,
                       qty: Optional[str] = Query(None), percentage: Optional[str] = Query(None)) -> Any:
        symbol = _resolve_symbol(session_id, owner, symbol_or_asset_id)
        return _close_position(session_id, owner, symbol, qty, percentage)

    @r.delete("/positions", summary="Close every position (Alpaca DELETE /v2/positions)")
    def close_all_positions(session_id: str, owner: str = Owner,
                            cancel_orders: bool = Query(False)) -> Any:
        if cancel_orders:
            _cancel_all(session_id, owner)
        out = []
        for p in call("observe", owner, session_id).positions:
            if p.quantity == 0:
                continue
            try:
                body = _close_position(session_id, owner, p.ticker, None, None)
                out.append({"symbol": p.ticker, "status": 200, "body": body})
            except ServeError as exc:
                out.append({"symbol": p.ticker, "status": http_status(exc.code), "body": exc.to_dict()})
        return JSONResponse(out, status_code=207)

    # -- orders --

    @r.post("/orders", summary="Place an order (Alpaca POST /v2/orders): market or limit, day or gtc")
    def submit_order(session_id: str, owner: str = Owner,
                     body: dict[str, Any] = Body(..., examples=[{
                         "symbol": "AAAA", "qty": "10", "side": "buy", "type": "limit",
                         "time_in_force": "day", "limit_price": "101.5",
                         "client_order_id": "my-order-1"}])) -> Any:
        req = order_request_from_alpaca(body)
        return _one_order(session_id, owner, call("place_order", owner, session_id, req))

    @r.get("/orders", summary="List orders (Alpaca GET /v2/orders)")
    def list_orders(session_id: str, owner: str = Owner,
                    status: str = Query("open", description="open, closed or all"),
                    limit: int = Query(50, ge=1, le=500),
                    after: Optional[str] = Query(None), until: Optional[str] = Query(None),
                    direction: str = Query("desc", description="asc or desc"),
                    symbols: Optional[str] = Query(None, description="Comma-separated symbols."),
                    side: Optional[str] = Query(None),
                    nested: Optional[bool] = Query(None)) -> Any:
        if status not in ("open", "closed", "all"):
            raise ServeError("invalid_request", f"status must be open, closed or all, got {status!r}")
        if direction not in ("asc", "desc"):
            raise ServeError("invalid_request", f"direction must be asc or desc, got {direction!r}")
        lo, hi = _parse_time(after, "after"), _parse_time(until, "until")
        wanted = {s.strip() for s in symbols.split(",") if s.strip()} if symbols else None
        orders, fills = _orders_with_fills(session_id, owner)
        rows = []
        for i, o in enumerate(orders):
            is_open = o.status in _OPEN
            if (status == "open" and not is_open) or (status == "closed" and is_open):
                continue
            if wanted is not None and o.ticker not in wanted:
                continue
            if side is not None and o.side != side:
                continue
            at = sim_datetime(o.submitted_at.day, o.submitted_at.tick)
            if (lo is not None and at <= lo) or (hi is not None and at > hi):
                continue
            rows.append((at, i, o))
        rows.sort(key=lambda t: (t[0], t[1]), reverse=direction == "desc")
        return [alpaca_order(session_id, o, fills.get(o.order_id, ())) for _, _, o in rows[:limit]]

    @r.get("/orders:by_client_order_id", summary="Order by client id (Alpaca GET /v2/orders:by_client_order_id)")
    def order_by_client_id(session_id: str, client_order_id: str = Query(...), owner: str = Owner) -> Any:
        for o in call("orders", owner, session_id):
            if o.client_order_id == client_order_id or (
                    o.client_order_id is None and order_uuid(session_id, o.order_id) == client_order_id):
                return _one_order(session_id, owner, o)
        raise ServeError("not_found", f"order with client_order_id {client_order_id} not found")

    @r.get("/orders/{order_id}", summary="One order (Alpaca GET /v2/orders/{order_id})")
    def get_order(session_id: str, order_id: str, owner: str = Owner,
                  nested: Optional[bool] = Query(None)) -> Any:
        return _one_order(session_id, owner, _find_order(session_id, owner, order_id))

    @r.delete("/orders/{order_id}", status_code=204, response_class=Response,
              summary="Cancel an order (Alpaca DELETE /v2/orders/{order_id}); 204 on success")
    def cancel_order(session_id: str, order_id: str, owner: str = Owner) -> Response:
        o = _find_order(session_id, owner, order_id)
        call("cancel_order", owner, session_id, o.order_id)
        return Response(status_code=204)

    def _cancel_all(session_id: str, owner: str) -> list[dict[str, Any]]:
        out = []
        for o in call("orders", owner, session_id, "accepted"):
            oid = order_uuid(session_id, o.order_id)
            try:
                done = call("cancel_order", owner, session_id, o.order_id)
                out.append({"id": oid, "status": 200, "body": alpaca_order(session_id, done)})
            except ServeError as exc:
                out.append({"id": oid, "status": http_status(exc.code), "body": exc.to_dict()})
        return out

    @r.delete("/orders", summary="Cancel every open order (Alpaca DELETE /v2/orders); 207 multi-status")
    def cancel_all_orders(session_id: str, owner: str = Owner) -> Any:
        return JSONResponse(_cancel_all(session_id, owner), status_code=207)

    # -- activities --

    def _activities(session_id: str, owner: str, types: Optional[str], direction: str,
                    after: Optional[str], until: Optional[str], page_size: int,
                    page_token: Optional[str]) -> list[dict[str, Any]]:
        wanted = {t.strip().upper() for t in types.split(",")} if types else {"FILL"}
        if "FILL" not in wanted:
            return []  # no dividends, fees, transfers or other activity in a simulation
        if direction not in ("asc", "desc"):
            raise ServeError("invalid_request", f"direction must be asc or desc, got {direction!r}")
        lo, hi = _parse_time(after, "after"), _parse_time(until, "until")
        orders = call("orders", owner, session_id)
        rows = _fill_activities(session_id, call("fills", owner, session_id), orders)
        rows = [a for a in rows
                if (lo is None or datetime.fromisoformat(a["transaction_time"].replace("Z", "+00:00")) > lo)
                and (hi is None or datetime.fromisoformat(a["transaction_time"].replace("Z", "+00:00")) <= hi)]
        if direction == "desc":
            rows.reverse()
        if page_token:
            ids = [a["id"] for a in rows]
            if page_token in ids:
                rows = rows[ids.index(page_token) + 1:]
        return rows[:page_size]

    @r.get("/account/activities", summary="Account activities (Alpaca); only FILL exists here")
    def activities(session_id: str, owner: str = Owner,
                   activity_types: Optional[str] = Query(None), direction: str = Query("desc"),
                   after: Optional[str] = Query(None), until: Optional[str] = Query(None),
                   page_size: int = Query(100, ge=1, le=100),
                   page_token: Optional[str] = Query(None)) -> Any:
        return _activities(session_id, owner, activity_types, direction, after, until,
                           page_size, page_token)

    @r.get("/account/activities/{activity_type}",
           summary="Account activities of one type (Alpaca); only FILL exists here")
    def activities_of_type(session_id: str, activity_type: str, owner: str = Owner,
                           direction: str = Query("desc"), after: Optional[str] = Query(None),
                           until: Optional[str] = Query(None),
                           page_size: int = Query(100, ge=1, le=100),
                           page_token: Optional[str] = Query(None)) -> Any:
        return _activities(session_id, owner, activity_type, direction, after, until,
                           page_size, page_token)

    # -- market data (Alpaca data API v2, latest-only subset) --

    def _quotes(session_id: str, owner: str) -> tuple[dict[str, Any], Clock]:
        obs = call("observe", owner, session_id)
        return {q.ticker: q for q in obs.quotes}, obs.clock

    def _symbols(symbols: Optional[str], known: dict[str, Any]) -> list[str]:
        if not symbols:
            raise ServeError("invalid_request", "symbols is required")
        return [s for s in (x.strip() for x in symbols.split(",")) if s in known]

    @r.get("/stocks/trades/latest", summary="Latest trade per symbol (Alpaca data API)")
    def latest_trades(session_id: str, owner: str = Owner, symbols: Optional[str] = Query(None),
                      feed: Optional[str] = Query(None)) -> Any:
        quotes, clk = _quotes(session_id, owner)
        now = sim_datetime(clk.day, clk.tick)
        return {"trades": {s: _latest_trade(quotes[s], now) for s in _symbols(symbols, quotes)}}

    @r.get("/stocks/quotes/latest", summary="Latest quote per symbol (Alpaca data API)")
    def latest_quotes(session_id: str, owner: str = Owner, symbols: Optional[str] = Query(None),
                      feed: Optional[str] = Query(None)) -> Any:
        quotes, clk = _quotes(session_id, owner)
        now = sim_datetime(clk.day, clk.tick)
        return {"quotes": {s: _latest_quote(quotes[s], now) for s in _symbols(symbols, quotes)}}

    @r.get("/stocks/snapshots", summary="Snapshots per symbol (Alpaca data API)")
    def snapshots(session_id: str, owner: str = Owner, symbols: Optional[str] = Query(None),
                  feed: Optional[str] = Query(None)) -> Any:
        quotes, clk = _quotes(session_id, owner)
        return {s: _snapshot(quotes[s], clk) for s in _symbols(symbols, quotes)}

    def _one_quote(session_id: str, owner: str, symbol: str) -> tuple[Any, Clock]:
        quotes, clk = _quotes(session_id, owner)
        if symbol not in quotes:
            raise ServeError("not_found", f"symbol {symbol} not found")
        return quotes[symbol], clk

    @r.get("/stocks/{symbol}/trades/latest", summary="Latest trade (Alpaca data API)")
    def latest_trade(session_id: str, symbol: str, owner: str = Owner,
                     feed: Optional[str] = Query(None)) -> Any:
        q, clk = _one_quote(session_id, owner, symbol)
        return {"symbol": symbol, "trade": _latest_trade(q, sim_datetime(clk.day, clk.tick))}

    @r.get("/stocks/{symbol}/quotes/latest", summary="Latest quote (Alpaca data API)")
    def latest_quote(session_id: str, symbol: str, owner: str = Owner,
                     feed: Optional[str] = Query(None)) -> Any:
        q, clk = _one_quote(session_id, owner, symbol)
        return {"symbol": symbol, "quote": _latest_quote(q, sim_datetime(clk.day, clk.tick))}

    @r.get("/stocks/{symbol}/snapshot", summary="Snapshot (Alpaca data API)")
    def snapshot(session_id: str, symbol: str, owner: str = Owner,
                 feed: Optional[str] = Query(None)) -> Any:
        q, clk = _one_quote(session_id, owner, symbol)
        return {"symbol": symbol, **_snapshot(q, clk)}

    # -- the one thing a broker does not have: moving time --

    @r.post("/tradefloor/advance",
            summary="Move simulated time forward (tradefloor extension, not Alpaca)",
            description="Where a live bot would sleep, call this. Body: {\"steps\": n} or "
                        "{\"until\": \"close\" | \"next_open\"}. Returns the Alpaca-shaped clock, "
                        "the fills as FILL activities and the expired orders.")
    def advance(session_id: str, owner: str = Owner,
                body: Optional[dict[str, Any]] = Body(None)) -> Any:
        body = body or {}
        unknown = set(body) - {"steps", "until"}
        if unknown:
            raise ServeError("invalid_request", f"unknown fields {sorted(unknown)}; send steps or until")
        steps = body.get("steps", 1)
        until = body.get("until", "steps")
        if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
            raise ServeError("invalid_request", f"steps must be an integer >= 1, got {steps!r}")
        if until not in ("steps", "close", "next_open"):
            raise ServeError("invalid_request", f"until must be steps, close or next_open, got {until!r}")
        res = call("advance", owner, session_id, steps, until)
        orders = call("orders", owner, session_id)
        every = _fill_activities(session_id, call("fills", owner, session_id), orders)
        activities = every[len(every) - len(res.fills):] if res.fills else []
        return {"clock": alpaca_clock(res.clock),
                "fills": activities,
                "expired": [alpaca_order(session_id, o) for o in res.expired],
                "state_hash": res.observation.state_hash}

    return r
