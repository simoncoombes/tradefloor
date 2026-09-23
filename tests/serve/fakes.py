"""An in-memory SessionService that follows contract 0.3, for testing layers
that sit on top of the service (transports, hosted) without the engine.

This is a FAKE MARKET. Prices are a hashed random walk, not the tradefloor
engine: every number it produces is a test fixture and nothing more. What it
does keep faithful is the contract's behaviour, because that is what the
layers above it are tested against (docs/serve/CONTRACT.md):

- owners: another owner's session is `not_found`, the same as a missing one;
- sessions: named presets only, `universe_size` 1..40, a uuid4-hex id;
- time moves only in `advance`: `steps`, `until="close"`, `until="next_open"`,
  crossing session boundaries, at most 20 sessions (7,800 ticks) per call;
  with `until="close"` or `"next_open"`, `steps` counts closes or opens;
- market orders queue and fill at the START of the next step, before the
  market moves, and their flow moves the price; several on one side of one
  name in one step sweep a finite book cumulatively, and an order larger than
  what is left fills partially (status `filled`, `filled_quantity` below
  `quantity`, reason "partial: ...");
- limit orders rest server-side and fill in full at the limit when the step's
  low (buy) or high (sell) reaches it; a limit marketable at submission is a
  market order capped at the limit; resting fills do not move the price;
- `day` orders expire at the session close, `gtc` persists; a day order placed
  while the market is closed belongs to the next session;
- `client_order_id` is an idempotency key: same body returns the original,
  a different body is `conflict`;
- refusals: bad ticker / quantity / price -> `invalid_order`; a fill whose
  projected leverage is above the cap AND above the current leverage is
  `rejected` with a reason (a trade that reduces risk always passes), and
  `insufficient_buying_power` at submission when that is already knowable;
- `Account.leverage` is None and `insolvent` True when net worth <= 0;
- `Order.updated_at` is the clock of the last status change;
- `orders(status=)` takes None, "all", "open", "closed" or one OrderStatus;
- `bars`: day bars for every session traded, step bars for the last 20
  sessions, the current session included up to the current step;
  `Quote.step_volume` is the last step's volume;
- cancelling an order that is not `accepted` is `invalid_request`;
- closing a session cancels its open orders (reason "session closed"); a
  closed session refuses every mutating call, fork and close included, with
  `session_closed`, and still answers reads;
- `fork` copies the full state; `state_hash` changes with every mutation and
  is identical for identical call sequences.

Where the contract is silent the fake does what the core does, and says so
here, so a test that leans on one of these is visibly leaning on a choice:

- `until="close"` with the market already closed runs the NEXT session to
  its close; `until="next_open"` with the market closed just opens it;
- a market order carrying a `limit_price` is `invalid_order`;
- `macro` uses the engine's field names in percent, `cycle_phase` an index;
- a step bar's `step` is the index of the step in its day (0 for the first),
  so it starts at tick `step * ticks_per_step`; its open is the step's first
  print and its high and low the step's prints (the core's CORE.md, "Bars").

`FakeSessionService(headlines=True)` also publishes one headline a day, from
tick 1, for one name, saying only the direction of the news.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from tradefloor.serve.types import (
    Account,
    AdvanceResult,
    Bar,
    Clock,
    Fill,
    Headline,
    Observation,
    Order,
    OrderRequest,
    Position,
    Quote,
    ServeError,
    SessionConfig,
    SessionInfo,
    SessionReport,
)

TICKS_PER_SESSION = 390
MAX_SESSIONS_PER_ADVANCE = 20
MAX_TICKS_PER_ADVANCE = MAX_SESSIONS_PER_ADVANCE * TICKS_PER_SESSION
MAX_UNIVERSE = 40
STEP_BAR_SESSIONS = 20
PRESETS = ("pt-v19",)
STATUSES = ("accepted", "filled", "cancelled", "expired", "rejected")

_TICK_VOL = 0.0008        # +-8 bps a tick, uniform
_HALF_SPREAD = 0.0002     # bid/ask around last
_IMPACT_PER_SHARE = 2e-7  # fractional price move per share swept
DEPTH = 200_000.0         # shares one side of one name can absorb in one step

_MACRO = {  # the core's MACRO_FIELDS, in the engine's percent units
    "federal_funds_rate": 5.0,
    "treasury_yield_10y": 4.2,
    "inflation_rate": 2.5,
    "gdp_growth": 2.0,
    "unemployment_rate": 4.0,
    "cycle_phase": 0.0,
}


def _unit(*parts: Any) -> float:
    """A deterministic uniform draw in [0, 1) from its address."""
    h = hashlib.blake2b(repr(parts).encode(), digest_size=8).digest()
    return int.from_bytes(h, "big") / 2.0**64


def _tickers(n: int, universe_seed: int) -> list[str]:
    out: list[str] = []
    i = 0
    while len(out) < n:
        u = int(_unit("ticker", universe_seed, i) * 26**4)
        name = ""
        for _ in range(4):
            u, r = divmod(u, 26)
            name += chr(ord("A") + r)
        if name not in out:
            out.append(name)
        i += 1
    return out


@dataclass
class _Book:
    """One ticker's market state."""

    last: float
    day_open: float
    day_high: float
    day_low: float
    prev_close: float
    volume: float = 0.0
    step_volume: float | None = None


@dataclass
class _Pos:
    quantity: float = 0.0
    avg_price: float = 0.0


@dataclass
class _Session:
    owner: str
    info: SessionInfo
    books: dict[str, _Book]
    cash: float
    realised: float = 0.0
    positions: dict[str, _Pos] = field(default_factory=dict)
    orders: list[Order] = field(default_factory=list)
    requests: dict[str, dict[str, Any]] = field(default_factory=dict)  # client id -> body
    by_client: dict[str, str] = field(default_factory=dict)            # client id -> order id
    session_day: dict[str, int] = field(default_factory=dict)          # order id -> session it belongs to
    queued: list[str] = field(default_factory=list)                    # fill at next step start
    resting: list[str] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    day_bars: dict[str, list[Bar]] = field(default_factory=dict)       # finished days
    step_bars: dict[str, list[Bar]] = field(default_factory=dict)
    seq: int = 0
    closed: bool = False


class FakeSessionService:
    """A contract-0.3 SessionService with a fake market. Thread-safe.

    `calls` records `(method, owner, session_id_or_None)` for every call, so a
    transport test can check which owner reached the service.
    """

    def __init__(self, *, presets: tuple[str, ...] = PRESETS,
                 id_factory: Callable[[], str] | None = None,
                 uuid_order_ids: bool = False, headlines: bool = False,
                 now: Callable[[], str] | None = None) -> None:
        self.presets = tuple(presets)
        self._new_id = id_factory or (lambda: uuid.uuid4().hex)
        self._uuid_order_ids = uuid_order_ids
        self._headlines = headlines
        self._now = now or (lambda: datetime.now(timezone.utc).isoformat())
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()
        self.calls: list[tuple[str, str, str | None]] = []

    # -- lookup ---------------------------------------------------------------

    def _get(self, owner: str, session_id: str) -> _Session:
        s = self._sessions.get(session_id)
        if s is None or s.owner != owner:
            raise ServeError("not_found", f"no session {session_id!r}")
        return s

    def _open_session(self, owner: str, session_id: str) -> _Session:
        s = self._get(owner, session_id)
        if s.closed:
            raise ServeError("session_closed", f"session {session_id} is closed")
        return s

    def _log(self, method: str, owner: str, session_id: str | None = None) -> None:
        self.calls.append((method, owner, session_id))

    # -- sessions -------------------------------------------------------------

    def open(self, owner: str, config: SessionConfig) -> SessionInfo:
        with self._lock:
            self._log("open", owner)
            if not isinstance(config, SessionConfig):
                raise ServeError("invalid_request", "config must be a SessionConfig")
            if config.preset not in self.presets:
                raise ServeError("invalid_request",
                                 f"unknown preset {config.preset!r}; named presets: {list(self.presets)}")
            if not 1 <= config.universe_size <= MAX_UNIVERSE:
                raise ServeError("invalid_request",
                                 f"universe_size must be 1..{MAX_UNIVERSE}, got {config.universe_size}")
            if not (math.isfinite(config.cash) and config.cash > 0):
                raise ServeError("invalid_request", f"cash must be finite and positive, got {config.cash}")
            if config.max_leverage is not None and not (
                    math.isfinite(config.max_leverage) and config.max_leverage > 0):
                raise ServeError("invalid_request",
                                 f"max_leverage must be positive or null, got {config.max_leverage}")
            if not 1 <= config.ticks_per_step <= TICKS_PER_SESSION:
                raise ServeError("invalid_request",
                                 f"ticks_per_step must be 1..{TICKS_PER_SESSION}, got {config.ticks_per_step}")

            session_id = self._new_id()
            tickers = _tickers(config.universe_size, config.universe_seed)
            books = {}
            for t in tickers:
                p = round(20 + 180 * _unit("price", config.universe_seed, t), 2)
                books[t] = _Book(last=p, day_open=p, day_high=p, day_low=p, prev_close=p)
            info = SessionInfo(
                session_id=session_id, owner=owner, config=copy.deepcopy(config),
                model_fingerprint="fake:" + hashlib.sha256(config.preset.encode()).hexdigest()[:16],
                tickers=list(tickers),
                clock=Clock(day=0, tick=0, step=0, market_open=True),
                status="open", created_at=self._now())
            self._sessions[session_id] = _Session(
                owner=owner, info=info, books=books, cash=float(config.cash),
                day_bars={t: [] for t in tickers}, step_bars={t: [] for t in tickers})
            return copy.deepcopy(info)

    def info(self, owner: str, session_id: str) -> SessionInfo:
        with self._lock:
            self._log("info", owner, session_id)
            return copy.deepcopy(self._get(owner, session_id).info)

    def list(self, owner: str) -> list[SessionInfo]:
        with self._lock:
            self._log("list", owner)
            return [copy.deepcopy(s.info) for s in self._sessions.values() if s.owner == owner]

    def fork(self, owner: str, session_id: str, label: str = "") -> SessionInfo:
        with self._lock:
            self._log("fork", owner, session_id)
            src = self._open_session(owner, session_id)
            new_id = self._new_id()
            s = copy.deepcopy(src)
            s.info.session_id = new_id
            s.info.parent_session_id = session_id
            s.info.created_at = self._now()
            s.info.config.label = label
            self._sessions[new_id] = s
            return copy.deepcopy(s.info)

    def close(self, owner: str, session_id: str) -> SessionReport:
        with self._lock:
            self._log("close", owner, session_id)
            s = self._open_session(owner, session_id)
            for o in s.orders:
                if o.status == "accepted":
                    self._finish(s, o, "cancelled", "session closed")
            s.queued.clear()
            s.resting.clear()
            s.closed = True
            s.info.status = "closed"
            days = s.info.clock.day + 1
            acct = self._account(s)
            resting_fills = sum(1 for f in s.fills if f.liquidity == "resting")
            caveats = [
                "FAKE SERVICE: prices are a hashed random walk from tests/serve/fakes.py, "
                "not the tradefloor engine. Nothing here measures a strategy.",
                f"The session ran {days} trading day(s) with {len(s.fills)} fill(s).",
            ]
            if resting_fills:
                caveats.append(f"{resting_fills} fill(s) were resting limit fills, which do not "
                               "move the market.")
            if acct.insolvent:
                caveats.append("The account is insolvent: net worth is at or below zero.")
            return SessionReport(session_id=session_id, account=acct,
                                 fills=len(s.fills), days=days, caveats=caveats,
                                 state_hash=self._hash(s))

    # -- reading --------------------------------------------------------------

    def observe(self, owner: str, session_id: str) -> Observation:
        with self._lock:
            self._log("observe", owner, session_id)
            return self._observe(self._get(owner, session_id))

    def orders(self, owner: str, session_id: str, status: str | None = None) -> list[Order]:
        with self._lock:
            self._log("orders", owner, session_id)
            s = self._get(owner, session_id)
            if status in (None, "all"):
                keep = lambda o: True  # noqa: E731
            elif status == "open":
                keep = lambda o: o.status == "accepted"  # noqa: E731
            elif status == "closed":
                keep = lambda o: o.status != "accepted"  # noqa: E731
            elif status in STATUSES:
                keep = lambda o: o.status == status  # noqa: E731
            else:
                raise ServeError("invalid_request",
                                 f"status must be all, open, closed or one of {list(STATUSES)}; "
                                 f"got {status!r}")
            return [copy.deepcopy(o) for o in s.orders if keep(o)]

    def fills(self, owner: str, session_id: str, since_day: int = 0) -> list[Fill]:
        with self._lock:
            self._log("fills", owner, session_id)
            s = self._get(owner, session_id)
            return [copy.deepcopy(f) for f in s.fills if f.at.day >= since_day]

    def bars(self, owner: str, session_id: str, ticker: str, resolution: str = "day",
             since_day: int = 0, limit: int | None = None) -> list[Bar]:
        with self._lock:
            self._log("bars", owner, session_id)
            s = self._get(owner, session_id)
            if ticker not in s.books:
                raise ServeError("invalid_request", f"unknown ticker {ticker!r}")
            if resolution not in ("day", "step"):
                raise ServeError("invalid_request", f"resolution must be day or step, got {resolution!r}")
            if limit is not None and (isinstance(limit, bool) or not isinstance(limit, int) or limit < 1):
                raise ServeError("invalid_request", f"limit must be a positive integer, got {limit!r}")
            if resolution == "step":
                out = list(s.step_bars[ticker])
            else:
                out = list(s.day_bars[ticker])
                clk, b = s.info.clock, s.books[ticker]
                if clk.market_open and clk.step > 0:  # today so far
                    out.append(Bar(ticker=ticker, day=clk.day, step=None, open=b.day_open,
                                   high=b.day_high, low=b.day_low, close=b.last, volume=b.volume))
            out = [bar for bar in out if bar.day >= since_day]
            if limit is not None:
                out = out[-limit:]
            return copy.deepcopy(out)

    # -- orders ---------------------------------------------------------------

    def place_order(self, owner: str, session_id: str, request: OrderRequest) -> Order:
        with self._lock:
            self._log("place_order", owner, session_id)
            s = self._open_session(owner, session_id)
            body = request.to_dict()
            cid = request.client_order_id
            if cid is not None and cid in s.by_client:
                if s.requests[cid] != body:
                    raise ServeError("conflict",
                                     f"client_order_id {cid!r} was used for a different order")
                return copy.deepcopy(self._order(s, s.by_client[cid]))
            self._validate(s, request)

            ref = request.limit_price if request.type == "limit" else s.books[request.ticker].last
            if not self._within_leverage(s, request.ticker, request.side, request.quantity, ref):
                raise ServeError("insufficient_buying_power",
                                 f"{request.side} {request.quantity:g} {request.ticker} at about "
                                 f"{ref:.2f} would take leverage above "
                                 f"{s.info.config.max_leverage:g}x")

            s.seq += 1
            oid = (uuid.uuid5(uuid.NAMESPACE_URL, f"{session_id}/{s.seq}").hex
                   if self._uuid_order_ids else f"ord-{s.seq:06d}")
            clk = s.info.clock
            order = Order(order_id=oid, client_order_id=cid, ticker=request.ticker,
                          side=request.side, quantity=float(request.quantity), type=request.type,
                          limit_price=request.limit_price, time_in_force=request.time_in_force,
                          status="accepted", submitted_at=copy.deepcopy(clk),
                          updated_at=copy.deepcopy(clk))
            s.orders.append(order)
            s.session_day[oid] = clk.day if clk.market_open else clk.day + 1
            if cid is not None:
                s.by_client[cid] = oid
                s.requests[cid] = body
            last = s.books[request.ticker].last
            marketable = request.type == "limit" and (
                (request.side == "buy" and request.limit_price >= last)
                or (request.side == "sell" and request.limit_price <= last))
            if request.type == "market" or marketable:
                s.queued.append(oid)
            else:
                s.resting.append(oid)
            return copy.deepcopy(order)

    def cancel_order(self, owner: str, session_id: str, order_id: str) -> Order:
        with self._lock:
            self._log("cancel_order", owner, session_id)
            s = self._open_session(owner, session_id)
            o = self._order(s, order_id)
            if o.status != "accepted":
                raise ServeError("invalid_request",
                                 f"order {order_id} is {o.status}; only an accepted order can be cancelled")
            self._finish(s, o, "cancelled", "cancelled by the owner")
            for q in (s.queued, s.resting):
                if order_id in q:
                    q.remove(order_id)
            return copy.deepcopy(o)

    def _finish(self, s: _Session, o: Order, status: str, reason: str | None) -> None:
        o.status, o.reason = status, reason  # type: ignore[assignment]
        o.updated_at = copy.deepcopy(s.info.clock)

    def _order(self, s: _Session, order_id: str) -> Order:
        for o in s.orders:
            if o.order_id == order_id:
                return o
        raise ServeError("not_found", f"no order {order_id!r}")

    def _validate(self, s: _Session, r: OrderRequest) -> None:
        if r.ticker not in s.books:
            raise ServeError("invalid_order", f"unknown ticker {r.ticker!r}")
        if r.side not in ("buy", "sell"):
            raise ServeError("invalid_order", f"side must be buy or sell, got {r.side!r}")
        if r.type not in ("market", "limit"):
            raise ServeError("invalid_order", f"type must be market or limit, got {r.type!r}")
        if r.time_in_force not in ("day", "gtc"):
            raise ServeError("invalid_order", f"time_in_force must be day or gtc, got {r.time_in_force!r}")
        q = r.quantity
        if isinstance(q, bool) or not isinstance(q, (int, float)) or not math.isfinite(q) or q <= 0:
            raise ServeError("invalid_order", f"quantity must be a finite number > 0, got {q!r}")
        if r.type == "limit":
            p = r.limit_price
            if p is None:
                raise ServeError("invalid_order", "a limit order needs a limit_price")
            if not isinstance(p, (int, float)) or not math.isfinite(p) or p <= 0:
                raise ServeError("invalid_order", f"limit_price must be finite and > 0, got {p!r}")
        elif r.limit_price is not None:
            raise ServeError("invalid_order", "limit_price is only for limit orders")

    # -- accounting -----------------------------------------------------------

    def _gross_and_worth(self, s: _Session, extra: tuple[str, float, float] | None = None
                         ) -> tuple[float, float]:
        cash = s.cash
        qty = {t: p.quantity for t, p in s.positions.items()}
        if extra is not None:
            t, signed, price = extra
            qty[t] = qty.get(t, 0.0) + signed
            cash -= signed * price
        gross = sum(abs(q) * s.books[t].last for t, q in qty.items())
        worth = cash + sum(q * s.books[t].last for t, q in qty.items())
        return gross, worth

    @staticmethod
    def _leverage(gross: float, worth: float) -> float:
        return gross / worth if worth > 0 else math.inf

    def _within_leverage(self, s: _Session, ticker: str, side: str, qty: float,
                         price: float) -> bool:
        """Contract 0.2: refused only when the projected leverage is above the
        cap AND above the current leverage, so reducing risk always passes."""
        cap = s.info.config.max_leverage
        if cap is None:
            return True
        signed = qty if side == "buy" else -qty
        now = self._leverage(*self._gross_and_worth(s))
        projected = self._leverage(*self._gross_and_worth(s, (ticker, signed, price)))
        return not (projected > cap + 1e-12 and projected > now)

    def _fill(self, s: _Session, o: Order, qty: float, price: float, liquidity: str,
              reason: str | None = None) -> Fill | None:
        if not self._within_leverage(s, o.ticker, o.side, qty, price):
            self._finish(s, o, "rejected",
                         f"filling {qty:g} {o.ticker} at {price:.4f} would take leverage "
                         f"above {s.info.config.max_leverage:g}x")
            return None
        signed = qty if o.side == "buy" else -qty
        pos = s.positions.setdefault(o.ticker, _Pos())
        if pos.quantity == 0 or (pos.quantity > 0) == (signed > 0):
            total = pos.quantity + signed
            pos.avg_price = (pos.avg_price * abs(pos.quantity) + price * abs(signed)) / abs(total)
            pos.quantity = total
        else:
            closing = min(abs(signed), abs(pos.quantity))
            direction = 1.0 if pos.quantity > 0 else -1.0
            s.realised += closing * (price - pos.avg_price) * direction
            total = pos.quantity + signed
            if abs(total) < 1e-12:
                total = 0.0
            if total != 0 and (total > 0) != (pos.quantity > 0):
                pos.avg_price = price
            pos.quantity = total
            if total == 0:
                pos.avg_price = 0.0
        if pos.quantity == 0:
            del s.positions[o.ticker]
        s.cash -= signed * price
        o.filled_quantity = qty
        o.avg_fill_price = price
        self._finish(s, o, "filled", reason)
        f = Fill(order_id=o.order_id, ticker=o.ticker, side=o.side, quantity=qty,
                 price=price, at=copy.deepcopy(s.info.clock), liquidity=liquidity)  # type: ignore[arg-type]
        s.fills.append(f)
        return f

    def _account(self, s: _Session) -> Account:
        gross, worth = self._gross_and_worth(s)
        unreal = sum(p.quantity * (s.books[t].last - p.avg_price) for t, p in s.positions.items())
        insolvent = worth <= 0
        return Account(cash=s.cash, net_worth=worth, gross_exposure=gross,
                       leverage=None if insolvent else gross / worth,
                       realised_pnl=s.realised, unrealised_pnl=unreal,
                       starting_cash=float(s.info.config.cash), insolvent=insolvent)

    # -- time -----------------------------------------------------------------

    def advance(self, owner: str, session_id: str, steps: int = 1,
                until: str = "steps") -> AdvanceResult:
        with self._lock:
            self._log("advance", owner, session_id)
            s = self._open_session(owner, session_id)
            tps = s.info.config.ticks_per_step
            if until not in ("steps", "close", "next_open"):
                raise ServeError("invalid_request",
                                 f"until must be steps, close or next_open, got {until!r}")
            if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
                raise ServeError("invalid_request", f"steps must be an integer >= 1, got {steps!r}")
            self._plan(s, steps, until, tps)
            fills: list[Fill] = []
            expired: list[Order] = []
            for _ in range(steps):
                if until == "steps":
                    self._step(s, fills, expired)
                elif until == "close":
                    if not s.info.clock.market_open:
                        self._open_market(s)
                    while s.info.clock.market_open:
                        self._step(s, fills, expired)
                else:  # next_open
                    while s.info.clock.market_open:
                        self._step(s, fills, expired)
                    self._open_market(s)
            return AdvanceResult(clock=copy.deepcopy(s.info.clock), fills=copy.deepcopy(fills),
                                 expired=copy.deepcopy(expired), observation=self._observe(s))

    @staticmethod
    def _plan(s: _Session, steps: int, until: str, tps: int) -> None:
        """Refuse an advance that would run past the cap, before running it."""
        tick, open_ = s.info.clock.tick, s.info.clock.market_open
        total = 0
        for _ in range(steps):
            if until == "steps":
                if not open_:
                    tick, open_ = 0, True
                run = min(tps, TICKS_PER_SESSION - tick)
                tick, total = tick + run, total + run
                if tick >= TICKS_PER_SESSION:
                    open_ = False
            elif until == "close":
                if not open_:
                    tick, open_ = 0, True
                total += TICKS_PER_SESSION - tick
                tick, open_ = TICKS_PER_SESSION, False
            else:
                if open_:
                    total += TICKS_PER_SESSION - tick
                tick, open_ = 0, True
            if total > MAX_TICKS_PER_ADVANCE:
                raise ServeError("invalid_request",
                                 f"advance would run more than {MAX_SESSIONS_PER_ADVANCE} sessions "
                                 f"({MAX_TICKS_PER_ADVANCE} ticks) in one call; split it")

    def _open_market(self, s: _Session) -> None:
        clk = s.info.clock
        clk.day += 1
        clk.tick = 0
        clk.step = 0
        clk.market_open = True
        for b in s.books.values():
            b.prev_close = b.last
            b.day_open = b.day_high = b.day_low = b.last
            b.volume = 0.0
            b.step_volume = None
        oldest = clk.day - STEP_BAR_SESSIONS + 1
        for t in s.step_bars:
            s.step_bars[t] = [bar for bar in s.step_bars[t] if bar.day >= oldest]

    def _step(self, s: _Session, fills: list[Fill], expired: list[Order]) -> None:
        if not s.info.clock.market_open:
            self._open_market(s)
        clk = s.info.clock
        seed = s.info.config.seed
        start = {t: b.last for t, b in s.books.items()}
        step_vol = {t: 0.0 for t in s.books}

        # 1. Queued orders fill at the start of the step, before the market
        #    moves, sweeping a finite book cumulatively per name and side.
        taken: dict[tuple[str, str], float] = {}
        still_resting = []
        for oid in list(s.queued):
            o = self._order(s, oid)
            b = s.books[o.ticker]
            sign = 1.0 if o.side == "buy" else -1.0
            before = taken.get((o.ticker, o.side), 0.0)
            qty = min(o.quantity, DEPTH - before)
            if qty <= 0:
                self._finish(s, o, "rejected", "the book had no depth left on this side this step")
                continue
            mid = start[o.ticker]
            price = round(mid * (1 + sign * (_HALF_SPREAD + _IMPACT_PER_SHARE * (before + qty / 2))), 6)
            if o.type == "limit" and ((o.side == "buy" and price > o.limit_price)
                                      or (o.side == "sell" and price < o.limit_price)):
                still_resting.append(oid)
                continue
            reason = (f"partial: the book absorbed {qty:g} of {o.quantity:g}; the rest is dropped"
                      if qty < o.quantity else None)
            f = self._fill(s, o, qty, price, "taker", reason)
            if f is not None:
                fills.append(f)
                taken[(o.ticker, o.side)] = before + qty
                b.last = round(b.last * (1 + sign * _IMPACT_PER_SHARE * qty), 6)  # flow moves the market
                b.volume += qty
                step_vol[o.ticker] += qty
        s.queued.clear()
        s.resting.extend(still_resting)

        # 2. The market moves. Each tick prints one price per name.
        n = min(s.info.config.ticks_per_step, TICKS_PER_SESSION - clk.tick)
        prints: dict[str, list[float]] = {t: [] for t in s.books}
        for k in range(n):
            tick = clk.tick + k
            for t, b in s.books.items():
                u = _unit("move", seed, clk.day, tick, t)
                b.last = round(max(0.01, b.last * (1 + (2 * u - 1) * _TICK_VOL)), 6)
                v = 100 + int(900 * _unit("vol", seed, clk.day, tick, t))
                b.volume += v
                step_vol[t] += v
                prints[t].append(b.last)
        step_hi = {t: max(p) for t, p in prints.items()}
        step_lo = {t: min(p) for t, p in prints.items()}
        for t, b in s.books.items():
            b.day_high = max(b.day_high, step_hi[t])
            b.day_low = min(b.day_low, step_lo[t])
            b.step_volume = step_vol[t]
            s.step_bars[t].append(Bar(ticker=t, day=clk.day, step=clk.step, open=prints[t][0],
                                      high=step_hi[t], low=step_lo[t], close=b.last,
                                      volume=step_vol[t]))
        clk.tick += n
        clk.step += 1

        # 3. Resting limits fill in full at the limit; they do not move the market.
        for oid in list(s.resting):
            o = self._order(s, oid)
            if o.status != "accepted":
                s.resting.remove(oid)
                continue
            hit = (step_lo[o.ticker] <= o.limit_price if o.side == "buy"
                   else step_hi[o.ticker] >= o.limit_price)
            if hit:
                s.resting.remove(oid)
                f = self._fill(s, o, o.quantity, float(o.limit_price), "resting")
                if f is not None:
                    fills.append(f)

        # 4. The close: day orders of this session expire; the day bar is final.
        if clk.tick >= TICKS_PER_SESSION:
            clk.market_open = False
            for t, b in s.books.items():
                s.day_bars[t].append(Bar(ticker=t, day=clk.day, step=None, open=b.day_open,
                                         high=b.day_high, low=b.day_low, close=b.last,
                                         volume=b.volume))
            for o in s.orders:
                if (o.status == "accepted" and o.time_in_force == "day"
                        and s.session_day[o.order_id] <= clk.day):
                    self._finish(s, o, "expired", "day order expired at the session close")
                    for q in (s.queued, s.resting):
                        if o.order_id in q:
                            q.remove(o.order_id)
                    expired.append(copy.deepcopy(o))

    # -- observation ----------------------------------------------------------

    def _news(self, s: _Session) -> list[Headline]:
        clk = s.info.clock
        if not self._headlines or clk.tick < 1:
            return []
        tickers = s.info.tickers
        t = tickers[clk.day % len(tickers)]
        up = _unit("news", s.info.config.seed, clk.day) >= 0.5
        return [Headline(day=clk.day, tick=1, tickers=[t], category="company",
                         text=f"{t} shares move {'higher' if up else 'lower'} on company news")]

    def _observe(self, s: _Session) -> Observation:
        quotes = [Quote(ticker=t, last=b.last, day_open=b.day_open, day_high=b.day_high,
                        day_low=b.day_low, prev_close=b.prev_close, volume=b.volume,
                        bid=round(b.last * (1 - _HALF_SPREAD), 6),
                        ask=round(b.last * (1 + _HALF_SPREAD), 6),
                        step_volume=b.step_volume)
                  for t, b in s.books.items()]
        positions = [Position(ticker=t, quantity=p.quantity, avg_price=p.avg_price,
                              market_value=p.quantity * s.books[t].last,
                              unrealised_pnl=p.quantity * (s.books[t].last - p.avg_price))
                     for t, p in sorted(s.positions.items())]
        clk = s.info.clock
        vix = round(12 + 10 * _unit("vix", s.info.config.seed, clk.day), 4)
        return Observation(session_id=s.info.session_id, clock=copy.deepcopy(clk),
                           quotes=quotes, vix=vix, macro=dict(_MACRO),
                           account=self._account(s), positions=positions,
                           open_orders=[copy.deepcopy(o) for o in s.orders if o.status == "accepted"],
                           news=self._news(s), state_hash=self._hash(s))

    def _hash(self, s: _Session) -> str:
        state = {
            "clock": s.info.clock.to_dict(),
            "books": {t: vars(b) for t, b in sorted(s.books.items())},
            "cash": s.cash, "realised": s.realised,
            "positions": {t: vars(p) for t, p in sorted(s.positions.items())},
            "orders": [o.to_dict() for o in s.orders],
            "queued": s.queued, "resting": s.resting, "closed": s.closed,
        }
        blob = json.dumps(state, sort_keys=True, default=str).encode()
        return "sha256:" + hashlib.sha256(blob).hexdigest()


# -- for the transport tests: the same test against the fake and the real core --

def core_available() -> bool:
    """Whether the real LocalSessionService (tradefloor.serve.core) is importable."""
    try:
        import tradefloor.serve.core  # noqa: F401
        import tradefloor.serve.store  # noqa: F401
    except ImportError:
        return False
    return True


def core_has(method: str) -> bool:
    """Whether the core implements `method` yet (for contract items landing
    on feat/serve-core after the contract that names them)."""
    if not core_available():
        return False
    from tradefloor.serve.core import LocalSessionService
    return callable(getattr(LocalSessionService, method, None))


def make_service(kind: str, root: Any = None) -> Any:
    """`"fake"` -> FakeSessionService; `"core"` -> the service `python -m
    tradefloor.serve` would build over the store directory `root` (a
    LocalSessionService over a FileStore)."""
    if kind == "fake":
        return FakeSessionService(headlines=True)
    from tradefloor.serve.http import default_service
    return default_service(root)
