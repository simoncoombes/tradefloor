"""The contract of the trading session server. Every layer builds against this.

`tradefloor.serve` lets a long-running agent open a session in a simulated
market, look at it, place and cancel orders, move time forward, and come back
after a crash to exactly where it was. The layers:

    core        tradefloor.serve.core       LocalSessionService (the one real
                                            implementation of SessionService)
    store       tradefloor.serve.store      SessionStore implementations
    transports  tradefloor.serve.mcp        write-capable MCP server
                tradefloor.serve.http       HTTP API (native + broker-shaped)
    hosted      tradefloor.serve.hosted     accounts, API keys, quotas, abuse
                                            limits, audit log (wraps a service)

This module holds ONLY types and the service protocol. It imports nothing
beyond the standard library so every layer, and every test fake, can import it
without pulling in the engine. See docs/serve/CONTRACT.md for the semantics
these types carry; where the two disagree the contract document governs and
this file is the bug.

Every type is plain data and round-trips through JSON with `to_dict` /
`from_dict`, because two of the three consumers (MCP, HTTP) speak JSON and the
third (the store) persists it.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
import sys
import types as _pytypes
import typing
from typing import Any, Literal, Mapping, Protocol, Sequence, runtime_checkable

CONTRACT_VERSION = "0.4"

Side = Literal["buy", "sell"]
OrderType = Literal["market", "limit"]
TimeInForce = Literal["day", "gtc"]
OrderStatus = Literal["accepted", "filled", "cancelled", "expired", "rejected"]
AdvanceUnit = Literal["steps", "close", "next_open"]
BarResolution = Literal["day", "step"]


def _decode(hint: Any, value: Any) -> Any:
    """Rebuild `value` (plain JSON) as `hint` describes: nested dataclasses,
    lists of them, and optionals. Anything else passes through unchanged."""
    if value is None:
        return None
    origin = typing.get_origin(hint)
    if origin in (typing.Union, _pytypes.UnionType):
        for arg in typing.get_args(hint):
            if arg is type(None):
                continue
            if isinstance(arg, type) and issubclass(arg, _Data) and isinstance(value, dict):
                return arg.from_dict(value)
            if typing.get_origin(arg) is list and isinstance(value, list):
                return _decode(arg, value)
        return value
    if origin is list:
        (inner,) = typing.get_args(hint) or (Any,)
        return [_decode(inner, v) for v in value]
    if isinstance(hint, type) and issubclass(hint, _Data) and isinstance(value, dict):
        return hint.from_dict(value)
    return value


class _Data:
    """JSON round-trip for the dataclasses below. `from_dict` rebuilds nested
    types (contract 0.2), so a transport or store that reads JSON back gets the
    same object graph the service returned."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls, d: dict[str, Any]):
        if not isinstance(d, dict):
            raise ServeError("invalid_request", f"{cls.__name__}: expected an object, got {type(d).__name__}")
        names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
        unknown = set(d) - names
        if unknown:
            raise ServeError("invalid_request", f"{cls.__name__}: unknown fields {sorted(unknown)}")
        hints = typing.get_type_hints(cls, globalns=vars(sys.modules[cls.__module__]))
        try:
            return cls(**{k: _decode(hints.get(k, Any), v) for k, v in d.items()})  # type: ignore[call-arg]
        except TypeError as e:
            raise ServeError("invalid_request", f"{cls.__name__}: {e}") from None


# -- errors ------------------------------------------------------------------

ERROR_CODES = (
    "invalid_request",       # malformed or out-of-range input
    "not_found",             # no such session / order for this owner
    "invalid_order",         # order refused on its own terms (bad ticker, qty, price)
    "insufficient_buying_power",  # refused by the leverage / cash policy
    "session_closed",        # the session has been closed
    "conflict",              # client_order_id reused with a different body
    "rate_limited",          # hosted layer: too many calls
    "quota_exceeded",        # hosted layer: sessions / steps / compute used up
    "unauthorized",          # hosted layer: bad or missing API key
    "internal",              # a bug; the message says what to report
)


class ServeError(Exception):
    """Every refusal the service makes. `code` is one of ERROR_CODES."""

    def __init__(self, code: str, message: str, *, retry_after: float | None = None) -> None:
        if code not in ERROR_CODES:
            code = "internal"
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        # Seconds until a rate_limited / quota_exceeded refusal would succeed,
        # when known (contract 0.3). HTTP sends it as Retry-After.
        self.retry_after = retry_after

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.retry_after is not None:
            d["retry_after"] = self.retry_after
        return d


# -- configuration -----------------------------------------------------------

@dataclass
class SessionConfig(_Data):
    """What a session is. Only NAMED presets: no free model coefficients
    (the rule `tradefloor.mcp` states: an improvised vector is a market nobody
    calibrated, reported with a preset's authority)."""

    preset: str = "pt-v19"
    seed: int = 1
    universe_size: int = 20          # Universe.random(universe_size, seed=universe_seed)
    universe_seed: int = 111
    cash: float = 1_000_000.0
    max_leverage: float | None = 2.0  # gross exposure / net worth; None = uncapped
    ticks_per_step: int = 30          # one step = this many ticks; 390 ticks = one session
    label: str = ""                   # free text for the owner


# -- market state --------------------------------------------------------------

@dataclass
class Clock(_Data):
    day: int                          # trading day index from 0
    tick: int                         # ticks elapsed in the current session, 0..390
    step: int                         # steps taken since the session opened
    market_open: bool
    ticks_per_session: int = 390


@dataclass
class Quote(_Data):
    ticker: str
    last: float
    day_open: float
    day_high: float
    day_low: float
    prev_close: float
    volume: float                     # shares traded so far today
    bid: float | None = None          # optional
    ask: float | None = None
    step_volume: float | None = None  # shares traded in the last step (0.3)


@dataclass
class Headline(_Data):
    """Text news for agents. Filled by tradefloor.headlines; empty until then.
    NEVER carries magnitude information from which price impact can be read
    (the leak guard; see docs/serve/CONTRACT.md)."""

    day: int
    tick: int
    tickers: list[str]
    text: str
    category: str


@dataclass
class Position(_Data):
    ticker: str
    quantity: float
    avg_price: float
    market_value: float
    unrealised_pnl: float


@dataclass
class Account(_Data):
    cash: float
    net_worth: float
    gross_exposure: float
    leverage: float | None            # None when insolvent (net worth <= 0): JSON has no infinity
    realised_pnl: float
    unrealised_pnl: float
    starting_cash: float
    insolvent: bool = False


@dataclass
class Observation(_Data):
    session_id: str
    clock: Clock
    quotes: list[Quote]
    vix: float
    macro: dict[str, float]           # a documented subset of the economy state
    account: Account
    positions: list[Position]
    open_orders: list["Order"]
    news: list[Headline] = field(default_factory=list)
    state_hash: str = ""              # verifies deterministic replay


# -- orders ----------------------------------------------------------------------

@dataclass
class OrderRequest(_Data):
    ticker: str
    side: Side
    quantity: float                   # shares, > 0
    type: OrderType = "market"
    limit_price: float | None = None  # required for limit
    time_in_force: TimeInForce = "day"
    client_order_id: str | None = None  # idempotency key, unique per session


@dataclass
class Order(_Data):
    order_id: str
    client_order_id: str | None
    ticker: str
    side: Side
    quantity: float
    type: OrderType
    limit_price: float | None
    time_in_force: TimeInForce
    status: OrderStatus
    submitted_at: Clock
    filled_quantity: float = 0.0
    avg_fill_price: float | None = None
    reason: str | None = None         # why rejected / expired / cancelled
    updated_at: Clock | None = None   # clock of the last status change (0.3)


@dataclass
class Fill(_Data):
    order_id: str
    ticker: str
    side: Side
    quantity: float
    price: float
    at: Clock
    liquidity: Literal["taker", "resting"] = "taker"


@dataclass
class Bar(_Data):
    """One OHLCV bar (0.3). `step` is None for a day bar."""

    ticker: str
    day: int
    step: int | None
    open: float
    high: float
    low: float
    close: float
    volume: float


# -- results -----------------------------------------------------------------------

@dataclass
class SessionInfo(_Data):
    session_id: str
    owner: str
    config: SessionConfig
    model_fingerprint: str
    tickers: list[str]
    clock: Clock
    status: Literal["open", "closed"]
    created_at: str                   # ISO-8601 wall time
    parent_session_id: str | None = None   # set on a fork


@dataclass
class AdvanceResult(_Data):
    clock: Clock
    fills: list[Fill]
    expired: list[Order]
    observation: Observation
    news: list[Headline] = field(default_factory=list)   # every headline released during this advance (0.4)


@dataclass
class SessionReport(_Data):
    session_id: str
    account: Account
    fills: int
    days: int
    caveats: list[str]                # computed at call time, never hardcoded prose
    state_hash: str


# -- persistence (contract 0.2) -------------------------------------------------------

@runtime_checkable
class SessionStore(Protocol):
    """Persistence behind LocalSessionService, implemented by FileStore and
    MemoryStore (tradefloor.serve.store) and by any hosted store. `commit` is
    the atomic commit point: after it returns, `load` sees `record` and every
    stream has `appends` appended, or (on a crash mid-commit) neither."""

    def commit(self, session_id: str, record: dict[str, Any],
               appends: Mapping[str, Sequence[dict[str, Any]]]) -> None: ...
    def load(self, session_id: str) -> dict[str, Any] | None: ...
    def read_stream(self, session_id: str, name: str) -> list[dict[str, Any]]: ...
    def version(self, session_id: str) -> int | None: ...
    def heads(self, owner: str) -> list[dict[str, Any]]: ...
    # 0.4: keep only the last `keep_last` entries of a stream (older ones are
    # dropped). Atomic with respect to `read_stream`: a reader sees the stream
    # before or after the trim, never part of it.
    def trim_stream(self, session_id: str, name: str, keep_last: int) -> None: ...


# -- the service ---------------------------------------------------------------------

@runtime_checkable
class SessionService(Protocol):
    """Transport-agnostic. Every call names its `owner`; the core refuses any
    session that does not belong to that owner (`not_found`, never a hint that
    it exists). The hosted layer maps an API key to an owner; self-run uses
    owner "local"."""

    def open(self, owner: str, config: SessionConfig) -> SessionInfo: ...
    def info(self, owner: str, session_id: str) -> SessionInfo: ...
    def list(self, owner: str) -> list[SessionInfo]: ...
    def observe(self, owner: str, session_id: str) -> Observation: ...
    def place_order(self, owner: str, session_id: str, request: OrderRequest) -> Order: ...
    def cancel_order(self, owner: str, session_id: str, order_id: str) -> Order: ...
    def orders(self, owner: str, session_id: str, status: str | None = None) -> list[Order]: ...
    # status: None or "all" (every order), "open", "closed", or one OrderStatus
    def fills(self, owner: str, session_id: str, since_day: int = 0) -> list[Fill]: ...
    def news(self, owner: str, session_id: str, since_day: int = 0,
             since_tick: int = 0, limit: int | None = None) -> list[Headline]: ...
    def bars(self, owner: str, session_id: str, ticker: str,
             resolution: BarResolution = "day", since_day: int = 0,
             limit: int | None = None) -> list[Bar]: ...
    def advance(self, owner: str, session_id: str, steps: int = 1,
                until: AdvanceUnit = "steps") -> AdvanceResult: ...
    def fork(self, owner: str, session_id: str, label: str = "") -> SessionInfo: ...
    def close(self, owner: str, session_id: str) -> SessionReport: ...
