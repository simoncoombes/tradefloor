"""LocalSessionService: the one real implementation of `SessionService`.

A session is an engine for a named preset, a `Portfolio`, the orders and
fills of one owner, and a clock that moves only when the owner calls
`advance`. Every mutating call is committed to a `SessionStore` before it
returns, so a new process over the same store serves the session exactly as
the old one would have. The semantics are docs/serve/CONTRACT.md; the fill
rules, limits and measured speed are docs/serve/CORE.md.

## How a step runs (the TradingEnv order, with orders instead of weights)

1. If the market is closed, `open_market` (the next trading day).
2. Queued market orders, and limit orders that were marketable when placed,
   fill against the live book through `Portfolio.execute`, in submission
   order. Their flow is what `Portfolio.pending_flow` holds.
3. `run_session(hour, minute, 3, ticks, order_flow=pending_flow())`, then
   `clear_flow`, exactly as `TradingEnv.step` does. So market orders move
   the market.
4. The step's bars are recorded from `Engine.session_prices()` (one print
   per name per tick of the step just run) and the day's volume column.
5. Resting limit orders are checked against the step's traded high and low.
   They fill in full at the limit and do NOT feed order flow.
6. At tick 390, `close_market`, the day bar is recorded, and day orders
   expire.

The leverage cap refuses a fill only when it would leave leverage above the
cap AND above where the account stands now (contract 0.2), so a trade that
reduces risk always passes. An insolvent account may only make trades that do
not add gross exposure, cap or no cap (contract 0.4). `Portfolio`'s own rule refuses any fill projected
over the cap; the core runs `Portfolio.execute` with the cap lifted for that
one call after applying its own rule.

Several market orders for the same name and side in one step sweep the book
CUMULATIVELY: the second is priced at the levels the first did not take, so
splitting an order does not buy it top-of-book prices. The first goes
through `Portfolio.execute` unchanged; the rest use the same book, the same
leverage check and the same flow accounting, at the marginal price.

## Determinism

Nothing here reads a clock or a random source except `open` and `fork`,
which draw a session id (uuid4) and stamp `created_at`. Neither enters an
observation's numbers or the `state_hash`. Order ids count up per session
(`ord-000001`, ...), and a fork continues its parent's count, so two
sessions given the same calls return the same bytes apart from their ids.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import math
import re
import struct
import threading
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timezone
from typing import Any, Callable

import tradefloor as _tf
from tradefloor import envelope as _envelope
from tradefloor._core import Engine, OrderError, ValidationError
from tradefloor.headlines import headlines_for
from tradefloor.portfolio import Portfolio
from tradefloor.portfolio import Position as _Holding
from tradefloor.serve.store import FileStore
from tradefloor.serve.types import SessionStore
from tradefloor.serve.types import (
    CONTRACT_VERSION,
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

__all__ = ["LocalSessionService", "MACRO_FIELDS", "CYCLE_PHASES",
           "TICKS_PER_SESSION", "LONG_RUN_BLOCK", "STEP_BAR_SESSIONS"]

TICKS_PER_SESSION = 390
#: The session opens at 09:30 on a fixed weekday, as `TradingEnv` and the
#: harness run it: the day of week is not advanced (see `harness.session_clock`).
OPEN_MINUTE = 9 * 60 + 30
DAY_OF_WEEK = 3

#: The economy fields `observe` reports, in the engine's percent units, plus
#: `cycle_phase` as the index of the phase in `CYCLE_PHASES`.
MACRO_FIELDS = ("federal_funds_rate", "treasury_yield_10y", "inflation_rate",
                "gdp_growth", "unemployment_rate", "cycle_phase")
CYCLE_PHASES = ("expansion", "peak", "contraction", "trough", "recovery")

#: Order sizes past these are refused as `invalid_order`: they are not
#: trades, and on an uncapped account they drive cash to infinity.
MAX_QUANTITY = 1e12
MAX_PRICE = 1e9
MAX_CASH = 1e15

#: Step bars are kept for this many sessions, the current one included.
STEP_BAR_SESSIONS = 20
#: Finished sessions' step bars go to the store's `step_bars` stream, which is
#: trimmed back to STEP_BAR_SESSIONS entries at the session boundary where it
#: reaches this many. So a trim rewrites the window once every
#: STEP_BAR_SESSIONS sessions, not at every close.
STEP_BAR_TRIM_AT = 2 * STEP_BAR_SESSIONS
#: The rejection reason for an insolvent account's trade that adds exposure.
INSOLVENT = "insolvent"
#: The fields of a bar, packed per name as little-endian f64.
_BAR_FIELDS = ("open", "high", "low", "close", "volume")

RECORD_SCHEMA = 3
_SESSION_ID = re.compile(r"^[0-9a-f]{32}$")
_UNTIL = ("steps", "close", "next_open")
_STATUSES = ("accepted", "filled", "cancelled", "expired", "rejected")


# -- the long-run check ----------------------------------------------------------
#
#: The long-run check's verdict lives in the preset's OWN RECORD
#: (python/tradefloor/presets/<name>.json, block "long_run"), written by
#: `tools/presets/record.py --long-run` from tradefloor-design
#: `programme/longrun/criteria.py`: 30 histories x 21 years free running plus
#: the 2008 and 2020 replays, against the S&P 500 and the VIX 1990-2025, graded
#: on the owner's adopted criteria (`programme/longrun/CRITERIA.md`). Reading it
#: from the record rather than a table here means a recomposed preset cannot
#: inherit its predecessor's verdict: the record is re-measured with the
#: composition, and `tests/test_preset_records.py` guards it against drift.
LONG_RUN_BLOCK = "long_run"


def long_run_verdict(preset: str) -> dict[str, Any] | None:
    """The preset record's long-run block, or None if it has none."""
    try:
        from tradefloor import records as _records
        rec = _records.preset_record(preset)
    except Exception:
        return None
    blk = rec.get(LONG_RUN_BLOCK) if isinstance(rec, dict) else None
    return blk if isinstance(blk, dict) and "verdict" in blk else None


def _fmt(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " / ".join(_fmt(x) for x in v)
    if isinstance(v, float):
        return f"{v:.3g}"
    return str(v)


def long_run_failures(preset: str) -> list[str] | None:
    """The long-run criteria `preset` fails, as sentences; [] if it passes
    them all; None if it has not been checked. Computed from the record on
    every call."""
    blk = long_run_verdict(preset)
    if blk is None:
        return None
    return [f"{r['id']} {r['words']}: {_fmt(r['value'])} against {_fmt(r['real'])} "
            f"real ({r['rule']})" for r in blk.get("rows", []) if not r.get("pass")]


# -- small helpers -----------------------------------------------------------------

def _f64(buf: bytes) -> list[float]:
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def _hx(x: float | None) -> str | None:
    return None if x is None else struct.pack("<d", float(x)).hex()


def _is_int(x: Any) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _is_num(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _bad(message: str) -> ServeError:
    return ServeError("invalid_request", message)


def _order_from(d: dict[str, Any]) -> Order:
    # Built by hand rather than with Order.from_dict: a resume reads every
    # finished order, and from_dict resolves type hints on each call.
    d = dict(d)
    d["submitted_at"] = Clock(**d["submitted_at"])
    if d.get("updated_at") is not None:
        d["updated_at"] = Clock(**d["updated_at"])
    return Order(**d)


def _fill_from(d: dict[str, Any]) -> Fill:
    d = dict(d)
    d["at"] = Clock(**d["at"])
    return Fill(**d)


def _info_from(d: dict[str, Any]) -> SessionInfo:
    d = dict(d)
    d["config"] = SessionConfig(**d["config"])
    d["clock"] = Clock(**d["clock"])
    return SessionInfo(**d)


def _order_no(order_id: str) -> int:
    return int(order_id.rsplit("-", 1)[-1])


def _pack(values: list[float]) -> bytes:
    return struct.pack("<%dd" % len(values), *values)


def _bar_of(blob: bytes, j: int) -> tuple[float, ...]:
    """Name j's (open, high, low, close, volume) from a packed row of bars."""
    width = len(_BAR_FIELDS)
    return struct.unpack_from("<%dd" % width, blob, 8 * width * j)


def _gross_after(pf: Portfolio, engine: Engine, ticker: str, signed: float,
                 price: float) -> float:
    """Gross exposure after a trade, valued as `_projected_leverage` values it."""
    marks = pf.marks(engine)
    gross = sum(abs(h.quantity + (signed if h.ticker == ticker else 0.0))
                * marks.get(h.ticker, h.avg_cost) for h in pf.positions.values())
    if ticker not in pf.positions:
        gross += abs(signed) * price
    return gross


def _risk_refusal(pf: Portfolio, engine: Engine, ticker: str, signed: float,
                  price: float) -> str | None:
    """Why this fill is refused, or None if it passes.

    An insolvent account (net worth at or below zero) may only make trades
    that do not add gross exposure, and the reason is exactly `INSOLVENT`
    (contract 0.4). Otherwise, with a cap, a fill is refused only when its
    projected leverage is above the cap AND above the account's leverage now
    (contract 0.2), so a trade that lowers leverage always passes.
    `Portfolio._projected_leverage` does the projection.
    """
    if pf.net_worth(engine) <= 0:
        if _gross_after(pf, engine, ticker, signed, price) <= pf.gross_exposure(engine):
            return None
        return INSOLVENT
    cap = pf.max_leverage
    if cap is None:
        return None
    projected = pf._projected_leverage(engine, ticker, signed, price, signed * price)
    if projected <= cap:
        return None
    current = pf.leverage(engine)
    if projected <= current:
        return None
    shown = "insolvency" if math.isinf(projected) else f"{projected:.2f}x"
    return (f"trade would take leverage to {shown}, above the {cap:.2f}x "
            f"limit and above the account's {current:.2f}x now")


class _Uncapped:
    """`Portfolio.execute` with its own leverage check lifted for one call,
    after `_risk_refusal` has applied the contract's rule."""

    def __init__(self, pf: Portfolio) -> None:
        self.pf = pf
        self.cap = pf.max_leverage

    def __enter__(self) -> Portfolio:
        self.pf.max_leverage = None
        return self.pf

    def __exit__(self, *exc: Any) -> None:
        self.pf.max_leverage = self.cap


# -- one session's live state --------------------------------------------------------

class _Session:
    """Everything a session is, in memory. Serialised whole by `to_record`."""

    def __init__(self) -> None:
        self.session_id = ""
        self.owner = ""
        self.config = SessionConfig()
        self.created_at = ""
        self.parent: str | None = None
        self.status = "open"
        self.fingerprint = ""
        self.tickers: list[str] = []
        self.index: dict[str, int] = {}
        self.engine: Engine | None = None
        self.portfolio: Portfolio | None = None
        self.day = 0
        self.tick = 0
        self.step = 0
        self.market_open = False
        self.total_steps = 0
        self.orders: dict[str, Order] = {}
        self.queue: list[str] = []      # fill at the start of the next step
        self.resting: list[str] = []    # limits checked against each step's range
        self.by_client: dict[str, str] = {}
        self.next_order = 1
        self.fills: list[Fill] = []
        self.seq = 0
        # History produced since the last commit, appended to the store's
        # streams by the next commit.
        self.new_fills: list[Fill] = []
        self.new_done: list[Order] = []
        self.new_day_bars: list[tuple[int, bytes]] = []
        self.new_step_days: list[tuple[int, list[bytes]]] = []
        self.new_news: list[Headline] = []
        # Bars. A day bar is one packed row (every name's open, high, low,
        # close, volume) per finished session; all are kept, in the store's
        # `day_bars` stream. Step bars are one packed row per step: `today`
        # for the session in progress (in the record), `step_window` for the
        # finished sessions before it (the store's `step_bars` stream, one
        # entry per session, trimmed at session boundaries).
        self.day_bars: list[tuple[int, bytes]] = []
        self.step_window: deque[tuple[int, list[bytes]]] = deque()
        self.today: list[bytes] = []
        self.step_stream_len = 0
        # Every headline released so far, oldest first (the `news` stream).
        self.news_log: list[Headline] = []
        self.news_keys: set[tuple] = set()
        # The engine's hash and snapshot, cached until the engine next runs.
        # Only open_market, run_session and close_market change engine state
        # (a book sweep prices against the book and leaves it as it was), and
        # each of those calls `touch`.
        self._engine_hash: str | None = None
        self._snapshot: dict[str, Any] | None = None

    def touch(self) -> None:
        self._engine_hash = None
        self._snapshot = None

    def engine_hash(self) -> str:
        assert self.engine is not None
        if self._engine_hash is None:
            self._engine_hash = self.engine.state_hash()
        return self._engine_hash

    def snapshot(self) -> dict[str, Any]:
        assert self.engine is not None
        if self._snapshot is None:
            self._snapshot = self.engine.state_snapshot()
        return self._snapshot

    # -- views --

    def clock(self) -> Clock:
        return Clock(day=self.day, tick=self.tick, step=self.step,
                     market_open=self.market_open,
                     ticks_per_session=TICKS_PER_SESSION)

    def info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id, owner=self.owner,
            config=copy.deepcopy(self.config),
            model_fingerprint=self.fingerprint, tickers=list(self.tickers),
            clock=self.clock(), status=self.status,  # type: ignore[arg-type]
            created_at=self.created_at, parent_session_id=self.parent)

    def open_orders(self) -> list[Order]:
        """Accepted orders (queued or resting), in submission order."""
        return [self.orders[i] for i in sorted(self.queue + self.resting,
                                                key=_order_no)]

    def finish(self, order: Order, status: str, at: Clock,
               reason: str | None = None) -> None:
        order.status = status  # type: ignore[assignment]
        order.updated_at = copy.deepcopy(at)
        if reason is not None:
            order.reason = reason
        self.new_done.append(order)

    def end_day_steps(self) -> None:
        """Move today's step bars into the window at the close."""
        if self.today:
            self.step_window.append((self.day, self.today))
            self.new_step_days.append((self.day, self.today))
            self.today = []
        while len(self.step_window) > STEP_BAR_SESSIONS:
            self.step_window.popleft()

    def log_news(self, headline: Headline) -> bool:
        """Add a released headline to the log; False if it is already there."""
        key = (headline.day, headline.tick, tuple(headline.tickers),
               headline.text, headline.category)
        if key in self.news_keys:
            return False
        self.news_keys.add(key)
        self.news_log.append(headline)
        self.new_news.append(headline)
        return True

    def step_sessions(self) -> list[tuple[int, list[bytes]]]:
        """Step bars for the last STEP_BAR_SESSIONS sessions, oldest first."""
        days = list(self.step_window)
        if self.today:
            days.append((self.day, self.today))
        return days[-STEP_BAR_SESSIONS:]

    def state_hash(self) -> str:
        """`Engine.state_hash()` with the portfolio, open orders and clock.

        Floats enter as their bit patterns, so two states that print alike
        and differ in the last bit hash differently. The session's identity
        (id, owner, label, wall times) is left out on purpose: a fork and its
        parent, or two sessions given the same calls, hash alike.
        """
        assert self.engine is not None and self.portfolio is not None
        pf = self.portfolio
        queued = set(self.queue)
        payload = {
            "contract": CONTRACT_VERSION,
            "engine": self.engine_hash(),
            "config": [self.config.preset, self.config.ticks_per_step,
                       _hx(self.config.max_leverage)],
            "cash": _hx(pf.cash),
            "starting_cash": _hx(pf.starting_cash),
            "positions": [[t, _hx(p.quantity), _hx(p.avg_cost), _hx(p.realised)]
                          for t, p in sorted(pf.positions.items())],
            "orders": [[o.order_id, o.client_order_id, o.ticker, o.side,
                        _hx(o.quantity), o.type, _hx(o.limit_price),
                        o.time_in_force,
                        [o.submitted_at.day, o.submitted_at.tick,
                         o.submitted_at.step, o.submitted_at.market_open],
                        "queued" if o.order_id in queued else "resting"]
                       for o in self.open_orders()],
            "clock": [self.day, self.tick, self.step, self.market_open,
                      self.total_steps],
            "next_order": self.next_order,
            "status": self.status,
        }
        text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    # -- persistence --

    def to_record(self) -> dict[str, Any]:
        assert self.engine is not None and self.portfolio is not None
        pf = self.portfolio
        return {
            "schema": RECORD_SCHEMA,
            "seq": self.seq,
            "head": self.info().to_dict(),
            "engine": self.snapshot(),
            "engine_hash": self.engine_hash(),
            "portfolio": {
                "cash": pf.cash,
                "starting_cash": pf.starting_cash,
                "max_leverage": pf.max_leverage,
                "positions": [[p.ticker, p.quantity, p.avg_cost, p.realised]
                              for p in pf.positions.values()],
                "stamp": list(pf._stamp),
            },
            "clock": {"day": self.day, "tick": self.tick, "step": self.step,
                      "market_open": self.market_open,
                      "total_steps": self.total_steps},
            "open_orders": [o.to_dict() for o in self.open_orders()],
            "queue": list(self.queue),
            "resting": list(self.resting),
            "next_order": self.next_order,
            "step_bars": {"today": b"".join(self.today)},
            "counts": {"fills": len(self.fills),
                       "orders": len(self.orders) - len(self.open_orders()),
                       "day_bars": len(self.day_bars),
                       "news": len(self.news_log)},
        }

    @classmethod
    def from_record(cls, record: dict[str, Any],
                    streams: dict[str, list[dict[str, Any]]]) -> "_Session":
        if record.get("schema") not in (1, 2, RECORD_SCHEMA):
            raise ServeError("internal", f"session record schema "
                             f"{record.get('schema')!r} is not {RECORD_SCHEMA}")
        s = cls()
        info = _info_from(record["head"])
        s.session_id = info.session_id
        s.owner = info.owner
        s.config = info.config
        s.created_at = info.created_at
        s.parent = info.parent_session_id
        s.status = info.status
        s.fingerprint = info.model_fingerprint
        s.tickers = list(info.tickers)
        s.index = {t: i for i, t in enumerate(s.tickers)}
        s.seq = int(record["seq"])

        cfg = s.config
        universe = _tf.Universe.random(cfg.universe_size, seed=cfg.universe_seed)
        engine = Engine(seed=cfg.seed, universe=universe, model=cfg.preset)
        engine.restore_state(record["engine"])
        if engine.state_hash() != record["engine_hash"]:
            raise ServeError("internal", f"session {s.session_id}: the engine "
                             "restored from the store does not hash to what "
                             "was saved; report this with the session files")
        s.engine = engine
        s._engine_hash = record["engine_hash"]

        p = record["portfolio"]
        pf = Portfolio(cash=p["starting_cash"], max_leverage=p["max_leverage"])
        pf.cash = p["cash"]
        for ticker, qty, avg, realised in p["positions"]:
            h = _Holding(ticker)
            h.quantity, h.avg_cost, h.realised = qty, avg, realised
            pf.positions[ticker] = h
        pf._stamp = tuple(p["stamp"])
        s.portfolio = pf

        c = record["clock"]
        s.day, s.tick, s.step = c["day"], c["tick"], c["step"]
        s.market_open, s.total_steps = c["market_open"], c["total_steps"]

        counts = record["counts"]
        orders = [_order_from(d) for d in streams["orders"][:counts["orders"]]]
        orders += [_order_from(d) for d in record["open_orders"]]
        orders.sort(key=lambda o: _order_no(o.order_id))
        s.orders = {o.order_id: o for o in orders}
        s.by_client = {o.client_order_id: o.order_id for o in orders
                       if o.client_order_id is not None}
        s.queue = list(record["queue"])
        s.resting = list(record["resting"])
        s.next_order = int(record["next_order"])
        s.fills = [_fill_from(d) for d in streams["fills"][:counts["fills"]]]
        s.day_bars = [(int(e["day"]), e["bars"])
                      for e in streams.get("day_bars", [])[:counts.get("day_bars", 0)]]
        bars = record.get("step_bars", {"today": b""})
        width = 8 * len(_BAR_FIELDS) * len(s.tickers)

        def split(blob: bytes) -> list[bytes]:
            return [blob[i:i + width] for i in range(0, len(blob), width)]

        if "window" in bars:     # schema 2 kept the window in the record
            window = [(int(d), split(base64.b64decode(t))) for d, t in bars["window"]]
        else:
            entries = streams.get("step_bars", [])
            s.step_stream_len = len(entries)
            window = [(int(e["day"]), split(e["bars"]))
                      for e in entries[-STEP_BAR_SESSIONS:]]
        s.step_window = deque(window)
        s.today = split(bars["today"])
        for d in streams.get("news", [])[:counts.get("news", 0)]:
            s.log_news(Headline(**d))
        s.new_news.clear()
        return s

    def streams(self) -> dict[str, list[dict[str, Any]]]:
        """The whole history as the store's streams hold it."""
        return {"fills": [f.to_dict() for f in self.fills],
                "orders": sorted((o.to_dict() for o in self.orders.values()
                                  if o.status != "accepted"),
                                 key=lambda d: _order_no(d["order_id"])),
                "day_bars": [{"day": d, "bars": b} for d, b in self.day_bars],
                "step_bars": [{"day": d, "bars": b"".join(rows)}
                              for d, rows in self.step_window],
                "news": [h.to_dict() for h in self.news_log]}


# -- the service -----------------------------------------------------------------------

class LocalSessionService:
    """`SessionService` over a real engine, persisted through a `SessionStore`.

    `max_universe` caps `universe_size` (the hosted layer may pass lower);
    `max_advance_sessions` caps one `advance` at that many sessions of ticks.
    `news` is `tradefloor.headlines.headlines_for` at the observation's
    clock unless `headlines` is given, in which case it is called as
    `headlines(engine, clock)`; either must be a pure function of the engine
    state for replay to stay deterministic.
    `cache_size` is how many sessions stay live in memory; the rest are
    reloaded from the store on their next call.
    """

    def __init__(self, store: SessionStore | None = None, *,
                 max_universe: int = 40, max_advance_sessions: int = 20,
                 headlines: Callable[[Engine, Clock], list[Headline]] | None = None,
                 cache_size: int = 64) -> None:
        self.store: SessionStore = store if store is not None else FileStore()
        self.max_universe = int(max_universe)
        self.max_advance_sessions = int(max_advance_sessions)
        self.headlines = headlines
        self.cache_size = max(1, int(cache_size))
        self._cache: OrderedDict[str, _Session] = OrderedDict()
        self._locks: dict[str, threading.RLock] = {}
        self._lock = threading.RLock()

    # -- plumbing ---------------------------------------------------------------

    def _session_lock(self, session_id: str) -> threading.RLock:
        with self._lock:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = self._locks[session_id] = threading.RLock()
            return lock

    def _evict(self, session_id: str) -> None:
        with self._lock:
            self._cache.pop(session_id, None)

    def _remember(self, s: _Session) -> None:
        with self._lock:
            self._cache[s.session_id] = s
            self._cache.move_to_end(s.session_id)
            while len(self._cache) > self.cache_size:
                self._cache.popitem(last=False)

    @staticmethod
    def _check_owner(owner: Any) -> str:
        if not isinstance(owner, str) or not owner:
            raise _bad("owner must be a non-empty string")
        return owner

    def _load(self, owner: str, session_id: Any) -> _Session:
        """The session, if it exists AND belongs to `owner`; else not_found."""
        missing = ServeError("not_found", f"no session {session_id!r}")
        if not isinstance(session_id, str) or not _SESSION_ID.match(session_id):
            raise missing
        version = self.store.version(session_id)
        if version is None:
            self._evict(session_id)
            raise missing
        with self._lock:
            s = self._cache.get(session_id)
        if s is None or s.seq != version:
            record = self.store.load(session_id)
            if record is None:
                raise missing
            if record["head"].get("owner") != owner:
                # Checked before rebuilding the engine: another owner's
                # session costs nothing to refuse and says nothing.
                raise missing
            s = _Session.from_record(record, {
                name: self.store.read_stream(session_id, name)
                for name in ("fills", "orders", "day_bars", "step_bars", "news")})
        if s.owner != owner:
            raise missing
        self._remember(s)
        return s

    def _call(self, owner: Any, session_id: Any, fn: Callable[[_Session], Any],
              *, mutate: bool = False) -> Any:
        owner = self._check_owner(owner)
        lock = self._session_lock(session_id if isinstance(session_id, str) else "")
        with lock:
            s = self._load(owner, session_id)
            if mutate and s.status == "closed":
                raise ServeError("session_closed",
                                 f"session {session_id} is closed")
            try:
                return fn(s)
            except ServeError:
                # Every refusal inside a call is raised before the session is
                # touched; the one raised after (a failed commit) evicts it.
                raise
            except Exception as exc:
                self._evict(s.session_id)
                raise ServeError("internal", f"{type(exc).__name__}: {exc} "
                                 "(the call was not applied; report this)") from exc

    def _commit(self, s: _Session, op: str, args: dict[str, Any],
                extra: dict[str, Any] | None = None) -> str:
        """Persist the session and log the call. Returns the new state_hash."""
        s.seq += 1
        state_hash = s.state_hash()
        call = {"seq": s.seq, "op": op, "args": args, "state_hash": state_hash,
                "clock": s.clock().to_dict(), "wall": _now()}
        if extra:
            call.update(extra)
        appends = {"fills": [f.to_dict() for f in s.new_fills],
                   "orders": [o.to_dict() for o in s.new_done],
                   "day_bars": [{"day": d, "bars": b} for d, b in s.new_day_bars],
                   "step_bars": [{"day": d, "bars": b"".join(rows)}
                                 for d, rows in s.new_step_days],
                   "news": [h.to_dict() for h in s.new_news],
                   "calls": [call]}
        try:
            self.store.commit(s.session_id, s.to_record(), appends)
        except Exception as exc:
            self._evict(s.session_id)
            raise ServeError("internal", f"the store refused the commit: "
                             f"{type(exc).__name__}: {exc}") from exc
        s.new_fills.clear()
        s.new_done.clear()
        s.new_day_bars.clear()
        s.new_news.clear()
        s.step_stream_len += len(s.new_step_days)
        s.new_step_days.clear()
        if s.step_stream_len >= STEP_BAR_TRIM_AT:
            # At a session boundary (only a close appends to this stream).
            # The commit has landed; a failed trim only leaves the stream
            # longer, and the next boundary tries again.
            try:
                self.store.trim_stream(s.session_id, "step_bars", STEP_BAR_SESSIONS)
                s.step_stream_len = STEP_BAR_SESSIONS
            except Exception:  # noqa: BLE001
                pass
        return state_hash

    # -- SessionService ------------------------------------------------------------

    def open(self, owner: str, config: SessionConfig) -> SessionInfo:
        owner = self._check_owner(owner)
        cfg = self._check_config(config)
        universe = _tf.Universe.random(cfg.universe_size, seed=cfg.universe_seed)
        try:
            engine = Engine(seed=cfg.seed, universe=universe, model=cfg.preset)
            portfolio = Portfolio(cash=cfg.cash, max_leverage=cfg.max_leverage)
        except (ValidationError, ValueError, OverflowError, TypeError) as exc:
            raise _bad(f"config refused by the engine: {exc}") from exc
        engine.open_market()

        s = _Session()
        s.session_id = uuid.uuid4().hex
        s.owner = owner
        s.config = cfg
        s.created_at = _now()
        s.fingerprint = engine.model_fingerprint
        s.tickers = list(engine.tickers)
        s.index = {t: i for i, t in enumerate(s.tickers)}
        s.engine = engine
        s.portfolio = portfolio
        s.market_open = True
        lock = self._session_lock(s.session_id)
        with lock:
            self._commit(s, "open", {"config": cfg.to_dict()})
            self._remember(s)
            return s.info()

    def info(self, owner: str, session_id: str) -> SessionInfo:
        return self._call(owner, session_id, lambda s: s.info())

    def list(self, owner: str) -> list[SessionInfo]:
        owner = self._check_owner(owner)
        infos = [_info_from(h) for h in self.store.heads(owner)]
        infos.sort(key=lambda i: (i.created_at, i.session_id))
        return infos

    def observe(self, owner: str, session_id: str) -> Observation:
        return self._call(owner, session_id, self._observe)

    def place_order(self, owner: str, session_id: str,
                    request: OrderRequest) -> Order:
        return self._call(owner, session_id,
                          lambda s: self._place(s, request), mutate=True)

    def cancel_order(self, owner: str, session_id: str, order_id: str) -> Order:
        return self._call(owner, session_id,
                          lambda s: self._cancel(s, order_id), mutate=True)

    def orders(self, owner: str, session_id: str,
               status: str | None = None) -> list[Order]:
        if status is not None and status not in (*_STATUSES, "open", "closed", "all"):
            raise _bad(f"status must be one of {', '.join(_STATUSES)}, "
                       f"'open', 'closed' or 'all'; got {status!r}")

        def pick(s: _Session) -> list[Order]:
            out = []
            for o in s.orders.values():
                if status in (None, "all") or o.status == status \
                        or (status == "open" and o.status == "accepted") \
                        or (status == "closed" and o.status != "accepted"):
                    out.append(copy.deepcopy(o))
            return out
        return self._call(owner, session_id, pick)

    def fills(self, owner: str, session_id: str, since_day: int = 0) -> list[Fill]:
        if not _is_int(since_day) or since_day < 0:
            raise _bad(f"since_day must be a non-negative integer, got {since_day!r}")
        return self._call(owner, session_id, lambda s: [
            copy.deepcopy(f) for f in s.fills if f.at.day >= since_day])

    def news(self, owner: str, session_id: str, since_day: int = 0,
             since_tick: int = 0, limit: int | None = None) -> list[Headline]:
        """The session's news log from clock point (since_day, since_tick),
        oldest first; `limit` keeps the most recent `limit`, as `bars` does."""
        for name, v in (("since_day", since_day), ("since_tick", since_tick)):
            if not _is_int(v) or v < 0:
                raise _bad(f"{name} must be a non-negative integer, got {v!r}")
        if limit is not None and (not _is_int(limit) or limit < 1):
            raise _bad(f"limit must be a positive integer or None, got {limit!r}")

        def pick(s: _Session) -> list[Headline]:
            out = [copy.deepcopy(h) for h in s.news_log
                   if (h.day, h.tick) >= (since_day, since_tick)]
            return out[-limit:] if limit is not None else out
        return self._call(owner, session_id, pick)

    def bars(self, owner: str, session_id: str, ticker: str,
             resolution: str = "day", since_day: int = 0,
             limit: int | None = None) -> list[Bar]:
        if resolution not in ("day", "step"):
            raise _bad(f"resolution must be 'day' or 'step', got {resolution!r}")
        if not _is_int(since_day) or since_day < 0:
            raise _bad(f"since_day must be a non-negative integer, got {since_day!r}")
        if limit is not None and (not _is_int(limit) or limit < 1):
            raise _bad(f"limit must be a positive integer or None, got {limit!r}")
        return self._call(owner, session_id, lambda s: self._bars(
            s, ticker, resolution, since_day, limit))

    def advance(self, owner: str, session_id: str, steps: int = 1,
                until: str = "steps") -> AdvanceResult:
        if until not in _UNTIL:
            raise _bad(f"until must be one of {', '.join(_UNTIL)}; got {until!r}")
        if not _is_int(steps) or steps < 1:
            raise _bad(f"steps must be a positive integer, got {steps!r}")
        return self._call(owner, session_id,
                          lambda s: self._advance(s, steps, until), mutate=True)

    def fork(self, owner: str, session_id: str, label: str = "") -> SessionInfo:
        if not isinstance(label, str):
            raise _bad("label must be a string")
        return self._call(owner, session_id,
                          lambda s: self._fork(s, label), mutate=True)

    def close(self, owner: str, session_id: str) -> SessionReport:
        return self._call(owner, session_id, self._close, mutate=True)

    # -- extras (not in the protocol) -------------------------------------------------

    def calls(self, owner: str, session_id: str) -> list[dict[str, Any]]:
        """The session's append-only call log, oldest first."""
        return self._call(owner, session_id,
                          lambda s: self.store.read_stream(s.session_id, "calls"))

    def caveats(self, owner: str, session_id: str) -> list[str]:
        """The caveats a report on this session would carry now."""
        return self._call(owner, session_id, self._caveats)

    # -- open ------------------------------------------------------------------------

    def _check_config(self, config: Any) -> SessionConfig:
        if isinstance(config, dict):
            config = SessionConfig.from_dict(config)
        if not isinstance(config, SessionConfig):
            raise _bad("config must be a SessionConfig")
        c = copy.deepcopy(config)
        presets = _tf.preset_names()
        if not isinstance(c.preset, str) or c.preset not in presets:
            raise _bad(f"preset must be a named preset ({', '.join(presets)}); "
                       f"got {c.preset!r}")
        for name in ("seed", "universe_seed"):
            v = getattr(c, name)
            if not _is_int(v) or not 0 <= v < 2**63:
                raise _bad(f"{name} must be an integer in 0..2**63-1, got {v!r}")
        if not _is_int(c.universe_size) or not 1 <= c.universe_size <= self.max_universe:
            raise _bad(f"universe_size must be an integer in 1..{self.max_universe}, "
                       f"got {c.universe_size!r}")
        if not _is_num(c.cash) or not math.isfinite(c.cash) or not 0 < c.cash <= MAX_CASH:
            raise _bad(f"cash must be finite, positive and at most {MAX_CASH:g}, "
                       f"got {c.cash!r}")
        c.cash = float(c.cash)
        if c.max_leverage is not None:
            if not _is_num(c.max_leverage) or not math.isfinite(c.max_leverage) \
                    or c.max_leverage <= 0:
                raise _bad(f"max_leverage must be positive and finite, or None; "
                           f"got {c.max_leverage!r}")
            c.max_leverage = float(c.max_leverage)
        if not _is_int(c.ticks_per_step) or not 1 <= c.ticks_per_step <= TICKS_PER_SESSION:
            raise _bad(f"ticks_per_step must be an integer in 1..{TICKS_PER_SESSION}, "
                       f"got {c.ticks_per_step!r}")
        if not isinstance(c.label, str) or len(c.label) > 256:
            raise _bad("label must be a string of at most 256 characters")
        return c

    # -- observe -----------------------------------------------------------------------

    def _observe(self, s: _Session) -> Observation:
        engine, pf = s.engine, s.portfolio
        assert engine is not None and pf is not None
        last = _f64(engine.prices())
        opens = _f64(engine.column("open"))
        highs = _f64(engine.column("high"))
        lows = _f64(engine.column("low"))
        prev = _f64(engine.column("previous_close"))
        volume = _f64(engine.column("volume"))
        # The last step's volume: None until a step has run in this session.
        last_step = s.today[-1] if s.market_open and s.today else (
            s.step_window[-1][1][-1] if not s.market_open and s.step_window
            and s.step_window[-1][0] == s.day else None)
        quotes = []
        for i, t in enumerate(s.tickers):
            book = engine.book(t)
            quotes.append(Quote(ticker=t, last=last[i], day_open=opens[i],
                                day_high=highs[i], day_low=lows[i],
                                prev_close=prev[i], volume=volume[i],
                                bid=book.best_bid, ask=book.best_ask,
                                step_volume=(None if last_step is None
                                             else _bar_of(last_step, i)[4])))
        economy = s.snapshot()["economy"]
        macro = {k: float(economy[k]) for k in MACRO_FIELDS[:-1]}
        phase = economy.get("cycle_phase")
        macro["cycle_phase"] = float(CYCLE_PHASES.index(phase)
                                     if phase in CYCLE_PHASES else -1)
        positions = []
        for i, t in enumerate(s.tickers):
            h = pf.positions.get(t)
            if h is None or h.quantity == 0:
                continue
            positions.append(Position(
                ticker=t, quantity=h.quantity, avg_price=h.avg_cost,
                market_value=h.quantity * last[i],
                unrealised_pnl=(last[i] - h.avg_cost) * h.quantity))
        clock = s.clock()
        news = self._visible_news(s, clock)
        return Observation(
            session_id=s.session_id, clock=clock, quotes=quotes,
            vix=float(economy["vix"]), macro=macro, account=self._account(s),
            positions=positions,
            open_orders=[copy.deepcopy(o) for o in s.open_orders()],
            news=news, state_hash=s.state_hash())

    def _visible_news(self, s: _Session, clock: Clock) -> list[Headline]:
        """The headlines visible at `clock`: `headlines_for`, or the
        `headlines` callable when one was given."""
        if self.headlines is not None:
            assert s.engine is not None
            return list(self.headlines(s.engine, clock))
        return headlines_for(s.snapshot(), day=clock.day, tick=clock.tick,
                             tickers=s.tickers)

    def _collect_news(self, s: _Session, released: list[Headline]) -> None:
        """Log the headlines released by the step just run.

        `headlines_for` releases a day's news at tick 1, all at once, so with
        the default source the first step of each day is the only one that
        can release anything, and only it pays for the engine snapshot. A
        `headlines` callable may release at any tick, so it is asked after
        every step and the log drops what it already holds.
        """
        if self.headlines is None and s.step != 1:
            return
        for h in self._visible_news(s, s.clock()):
            if s.log_news(h):
                released.append(copy.deepcopy(h))

    @staticmethod
    def _account(s: _Session) -> Account:
        engine, pf = s.engine, s.portfolio
        assert engine is not None and pf is not None
        net_worth = float(pf.net_worth(engine))
        insolvent = net_worth <= 0
        return Account(cash=float(pf.cash), net_worth=net_worth,
                       gross_exposure=float(pf.gross_exposure(engine)),
                       leverage=None if insolvent else float(pf.leverage(engine)),
                       realised_pnl=float(pf.realised()),
                       unrealised_pnl=float(pf.unrealised(engine)),
                       starting_cash=float(pf.starting_cash),
                       insolvent=insolvent)

    # -- orders --------------------------------------------------------------------------

    def _check_request(self, s: _Session, request: Any) -> OrderRequest:
        if isinstance(request, dict):
            request = OrderRequest.from_dict(request)
        if not isinstance(request, OrderRequest):
            raise _bad("request must be an OrderRequest")
        r = copy.deepcopy(request)
        if r.side not in ("buy", "sell"):
            raise _bad(f"side must be 'buy' or 'sell', got {r.side!r}")
        if r.type not in ("market", "limit"):
            raise _bad(f"type must be 'market' or 'limit', got {r.type!r}")
        if r.time_in_force not in ("day", "gtc"):
            raise _bad(f"time_in_force must be 'day' or 'gtc', got {r.time_in_force!r}")
        if r.client_order_id is not None and (
                not isinstance(r.client_order_id, str)
                or not 1 <= len(r.client_order_id) <= 128):
            raise _bad("client_order_id must be a string of 1 to 128 characters")
        if not isinstance(r.ticker, str) or r.ticker not in s.index:
            raise ServeError("invalid_order", f"unknown ticker {r.ticker!r}; this "
                             f"session trades {', '.join(s.tickers)}")
        if not _is_num(r.quantity) or not math.isfinite(r.quantity) or r.quantity <= 0:
            raise ServeError("invalid_order", f"quantity must be finite and "
                             f"positive, got {r.quantity!r}")
        if r.quantity > MAX_QUANTITY:
            raise ServeError("invalid_order", f"quantity {r.quantity:g} is above "
                             f"the {MAX_QUANTITY:g}-share limit")
        r.quantity = float(r.quantity)
        if r.type == "market":
            if r.limit_price is not None:
                raise ServeError("invalid_order", "a market order takes no "
                                 "limit_price; use type='limit'")
        else:
            if r.limit_price is None:
                raise ServeError("invalid_order", "a limit order needs a limit_price")
            if not _is_num(r.limit_price) or not math.isfinite(r.limit_price) \
                    or r.limit_price <= 0:
                raise ServeError("invalid_order", f"limit_price must be finite "
                                 f"and positive, got {r.limit_price!r}")
            if r.limit_price > MAX_PRICE:
                raise ServeError("invalid_order", f"limit_price {r.limit_price:g} "
                                 f"is above {MAX_PRICE:g}")
            r.limit_price = float(r.limit_price)
        return r

    @staticmethod
    def _body(o: OrderRequest | Order) -> tuple:
        return (o.ticker, o.side, float(o.quantity), o.type,
                None if o.limit_price is None else float(o.limit_price),
                o.time_in_force)

    def _place(self, s: _Session, request: Any) -> Order:
        r = self._check_request(s, request)
        if r.client_order_id is not None and r.client_order_id in s.by_client:
            existing = s.orders[s.by_client[r.client_order_id]]
            if self._body(existing) == self._body(r):
                return copy.deepcopy(existing)
            raise ServeError("conflict", f"client_order_id {r.client_order_id!r} "
                             f"is order {existing.order_id} with a different body")
        self._check_buying_power(s, r)

        engine = s.engine
        assert engine is not None
        order = Order(order_id=f"ord-{s.next_order:06d}",
                      client_order_id=r.client_order_id, ticker=r.ticker,
                      side=r.side, quantity=r.quantity, type=r.type,
                      limit_price=r.limit_price, time_in_force=r.time_in_force,
                      status="accepted", submitted_at=s.clock(),
                      updated_at=s.clock())
        s.next_order += 1
        s.orders[order.order_id] = order
        if order.client_order_id is not None:
            s.by_client[order.client_order_id] = order.order_id
        if order.type == "market" or self._marketable(s, order):
            s.queue.append(order.order_id)
        else:
            s.resting.append(order.order_id)
        self._commit(s, "place_order", {"request": r.to_dict()},
                     {"order_id": order.order_id})
        return copy.deepcopy(order)

    @staticmethod
    def _marketable(s: _Session, order: Order) -> bool:
        """Would this limit trade now: a buy at or above the ask, a sell at or
        below the bid (the last price when that side of the book is empty)."""
        assert s.engine is not None and order.limit_price is not None
        book = s.engine.book(order.ticker)
        last = _f64(s.engine.prices())[s.index[order.ticker]]
        if order.side == "buy":
            ask = book.best_ask
            return order.limit_price >= (ask if ask is not None else last)
        bid = book.best_bid
        return order.limit_price <= (bid if bid is not None else last)

    def _check_buying_power(self, s: _Session, r: OrderRequest) -> None:
        """Refuse at submission what the leverage cap would already refuse.

        Projected with `Portfolio`'s own arithmetic on a copy of the account,
        after the orders already queued ahead of this one, at the current
        book's sweep price (market) or the limit (limit), under the
        contract's rule (`_risk_refusal`). The fill is checked again when it
        happens; this only refuses what is already known."""
        pf, engine = s.portfolio, s.engine
        assert pf is not None and engine is not None
        trial = Portfolio(cash=pf.starting_cash, max_leverage=pf.max_leverage)
        trial.cash = pf.cash
        for t, h in pf.positions.items():
            c = _Holding(t)
            c.quantity, c.avg_cost, c.realised = h.quantity, h.avg_cost, h.realised
            trial.positions[t] = c
        for oid in s.queue:
            o = s.orders[oid]
            signed, price = self._estimate(s, o.ticker, o.side, o.quantity,
                                           o.limit_price)
            holding = trial.positions.setdefault(o.ticker, _Holding(o.ticker))
            Portfolio._apply(holding, signed, price)
            trial.cash -= signed * price
        signed, price = self._estimate(s, r.ticker, r.side, r.quantity, r.limit_price)
        why = _risk_refusal(trial, engine, r.ticker, signed, price)
        if why == INSOLVENT:
            raise ServeError("insufficient_buying_power",
                             "insolvent: net worth is at or below zero, so only "
                             "trades that reduce gross exposure are accepted")
        if why is not None:
            raise ServeError("insufficient_buying_power",
                             f"{why} (projected at {price:.4f} on current "
                             f"holdings and the orders queued ahead)")

    @staticmethod
    def _estimate(s: _Session, ticker: str, side: str, quantity: float,
                  limit: float | None) -> tuple[float, float]:
        assert s.engine is not None
        signed = quantity if side == "buy" else -quantity
        if limit is not None:
            return signed, limit
        cost = s.engine.book(ticker).sweep_cost(side, quantity)
        if cost is None or cost.filled <= 0:
            return signed, _f64(s.engine.prices())[s.index[ticker]]
        return signed, cost.average_price

    def _cancel(self, s: _Session, order_id: Any) -> Order:
        order = s.orders.get(order_id) if isinstance(order_id, str) else None
        if order is None:
            raise ServeError("not_found", f"no order {order_id!r} in this session")
        if order.status != "accepted":
            raise _bad(f"order {order_id} is {order.status} and cannot be cancelled")
        if order_id in s.queue:
            s.queue.remove(order_id)
        if order_id in s.resting:
            s.resting.remove(order_id)
        s.finish(order, "cancelled", s.clock(), "cancelled by the owner")
        self._commit(s, "cancel_order", {"order_id": order_id})
        return copy.deepcopy(order)

    # -- time ------------------------------------------------------------------------------

    def _plan_ticks(self, s: _Session, steps: int, until: str) -> int:
        """How many ticks this advance would run, without running them."""
        cap = self.max_advance_sessions * TICKS_PER_SESSION
        refuse = _bad(f"advance would run more than {self.max_advance_sessions} "
                      f"sessions ({cap} ticks) in one call; split it")
        # Every step runs at least one tick, and every close or next_open
        # after the first at least one session, so these bound the loop.
        if (until == "steps" and steps > cap) or (until != "steps" and steps > cap + 1):
            raise refuse
        tps = s.config.ticks_per_step
        tick, open_ = s.tick, s.market_open
        total = 0
        for _ in range(steps):
            if until == "steps":
                if not open_:
                    tick, open_ = 0, True
                run = min(tps, TICKS_PER_SESSION - tick)
                tick += run
                total += run
                if tick >= TICKS_PER_SESSION:
                    open_ = False
            elif until == "close":
                if not open_:
                    tick, open_ = 0, True
                total += TICKS_PER_SESSION - tick
                tick, open_ = TICKS_PER_SESSION, False
            else:  # next_open
                if open_:
                    total += TICKS_PER_SESSION - tick
                tick, open_ = 0, True
            if total > cap:
                raise refuse
        return total

    def _advance(self, s: _Session, steps: int, until: str) -> AdvanceResult:
        self._plan_ticks(s, steps, until)
        fills: list[Fill] = []
        expired: list[Order] = []
        released: list[Headline] = []
        for _ in range(steps):
            if until == "steps":
                self._step(s, fills, expired, released)
            elif until == "close":
                if not s.market_open:
                    self._open_day(s)
                while s.market_open:
                    self._step(s, fills, expired, released)
            else:
                while s.market_open:
                    self._step(s, fills, expired, released)
                self._open_day(s)
        self._commit(s, "advance", {"steps": steps, "until": until})
        return AdvanceResult(clock=s.clock(), fills=[copy.deepcopy(f) for f in fills],
                             expired=[copy.deepcopy(o) for o in expired],
                             observation=self._observe(s), news=released)

    @staticmethod
    def _open_day(s: _Session) -> None:
        assert s.engine is not None and not s.market_open
        s.engine.open_market()
        s.touch()
        s.day += 1
        s.tick = 0
        s.step = 0
        s.market_open = True

    def _step(self, s: _Session, fills: list[Fill], expired: list[Order],
              released: list[Headline]) -> None:
        engine, pf = s.engine, s.portfolio
        assert engine is not None and pf is not None
        if not s.market_open:
            self._open_day(s)
        pf.stamp(s.day, s.total_steps, s.tick)
        self._execute_queue(s, fills)

        ticks = min(s.config.ticks_per_step, TICKS_PER_SESSION - s.tick)
        hour, minute = divmod(OPEN_MINUTE + s.tick, 60)
        volume_before = _f64(engine.column("volume"))
        engine.run_session(hour, minute, DAY_OF_WEEK, ticks,
                           order_flow=pf.pending_flow())
        s.touch()
        pf.clear_flow()
        s.tick += ticks
        s.step += 1
        s.total_steps += 1

        n = len(s.tickers)
        prints = _f64(engine.session_prices())
        volume_after = _f64(engine.column("volume"))
        row: list[float] = []
        for j in range(n):
            series = prints[j::n]
            row += [series[0], max(series), min(series), series[-1],
                    volume_after[j] - volume_before[j]]
        s.today.append(_pack(row))
        self._collect_news(s, released)

        if s.resting:
            self._match_resting(s, fills, prints)
        if s.tick >= TICKS_PER_SESSION:
            engine.close_market()
            s.touch()
            s.market_open = False
            day_row: list[float] = []
            closes = _f64(engine.prices())
            cols = [_f64(engine.column(c)) for c in ("open", "high", "low")]
            for j in range(n):
                day_row += [cols[0][j], cols[1][j], cols[2][j], closes[j],
                            volume_after[j]]
            s.day_bars.append((s.day, _pack(day_row)))
            s.new_day_bars.append(s.day_bars[-1])
            s.end_day_steps()
            closed = s.clock()
            keep = []
            for oid in s.resting:
                o = s.orders[oid]
                if o.time_in_force == "day":
                    s.finish(o, "expired", closed, f"day order unfilled at the "
                             f"close of day {s.day}")
                    expired.append(o)
                else:
                    keep.append(oid)
            s.resting = keep

    def _execute_queue(self, s: _Session, fills: list[Fill]) -> None:
        """Fill queued orders at the start of a step, before the market moves."""
        engine, pf = s.engine, s.portfolio
        assert engine is not None and pf is not None
        taken: dict[tuple[str, str], float] = {}
        at = s.clock()
        queue, s.queue = s.queue, []
        for oid in queue:
            o = s.orders[oid]
            key = (o.ticker, o.side)
            before = taken.get(key, 0.0)
            book = engine.book(o.ticker)
            cost = book.sweep_cost(o.side, before + o.quantity)
            got = 0.0 if cost is None else min(before + o.quantity, cost.filled) - before
            if got <= 0:
                if o.type == "limit":
                    s.resting.append(oid)
                else:
                    s.finish(o, "rejected", at, f"the book for {o.ticker} could "
                             f"not fill any of {o.quantity:g} shares")
                continue
            assert cost is not None
            if before > 0:
                prior = book.sweep_cost(o.side, before)
                assert prior is not None
                price = (cost.average_price * (before + got)
                         - prior.average_price * before) / got
            else:
                price = cost.average_price
            if o.type == "limit":
                assert o.limit_price is not None
                worse = price > o.limit_price if o.side == "buy" else price < o.limit_price
                if got < o.quantity or worse:
                    # Not fillable in full at or better than the limit: it
                    # rests, and this step's range can still fill it.
                    s.resting.append(oid)
                    continue
            signed = got if o.side == "buy" else -got
            why = _risk_refusal(pf, engine, o.ticker, signed, price)
            if why is not None:
                s.finish(o, "rejected", at, why)
                continue
            try:
                if before == 0:
                    with _Uncapped(pf):
                        fill = pf.execute(engine, o.ticker, signed if o.type == "limit"
                                          else (o.quantity if o.side == "buy"
                                                else -o.quantity))
                    got, price = abs(fill["quantity"]), fill["price"]
                else:
                    self._take_marginal(s, o.ticker, signed, price)
            except (OrderError, ValidationError) as exc:
                s.finish(o, "rejected", at, str(exc))
                continue
            taken[key] = before + got
            o.filled_quantity = got
            o.avg_fill_price = price
            reason = None
            if got < o.quantity:
                reason = (f"partial: the book held {got:g} of {o.quantity:g} "
                          f"shares; the rest is cancelled")
            s.finish(o, "filled", at, reason)
            f = Fill(order_id=oid, ticker=o.ticker, side=o.side, quantity=got,
                     price=price, at=copy.deepcopy(at), liquidity="taker")
            s.fills.append(f)
            s.new_fills.append(f)
            fills.append(f)
        # The service keeps its own fill log; the portfolio's would grow
        # without bound and duplicate it.
        pf.fills.clear()

    @staticmethod
    def _take_marginal(s: _Session, ticker: str, signed: float,
                       price: float) -> None:
        """`Portfolio.execute` for the second and later order on one side of
        one book in a step: same accounting, same flow, at the marginal price
        of the levels the earlier orders left. The caller has applied the
        leverage rule."""
        engine, pf = s.engine, s.portfolio
        assert engine is not None and pf is not None
        notional = signed * price
        holding = pf.positions.setdefault(ticker, _Holding(ticker))
        Portfolio._apply(holding, signed, price)
        pf.cash -= notional
        flow = pf._flow.setdefault(ticker, [0.0, 0.0])
        flow[0 if signed > 0 else 1] += abs(signed)

    def _match_resting(self, s: _Session, fills: list[Fill],
                       prints: list[float]) -> None:
        """Fill resting limits against the step's traded range, at the limit."""
        engine, pf = s.engine, s.portfolio
        assert engine is not None and pf is not None
        n = len(s.tickers)
        at = s.clock()
        at.market_open = True
        keep = []
        for oid in s.resting:
            o = s.orders[oid]
            assert o.limit_price is not None
            j = s.index[o.ticker]
            series = prints[j::n]
            if not series:
                keep.append(oid)
                continue
            hit = (min(series) <= o.limit_price if o.side == "buy"
                   else max(series) >= o.limit_price)
            if not hit:
                keep.append(oid)
                continue
            signed = o.quantity if o.side == "buy" else -o.quantity
            price = o.limit_price
            notional = signed * price
            why = _risk_refusal(pf, engine, o.ticker, signed, price)
            if why is not None:
                s.finish(o, "rejected", at, why)
                continue
            holding = pf.positions.setdefault(o.ticker, _Holding(o.ticker))
            Portfolio._apply(holding, signed, price)
            pf.cash -= notional
            o.filled_quantity = o.quantity
            o.avg_fill_price = price
            s.finish(o, "filled", at)
            f = Fill(order_id=oid, ticker=o.ticker, side=o.side,
                     quantity=o.quantity, price=price, at=copy.deepcopy(at),
                     liquidity="resting")
            s.fills.append(f)
            s.new_fills.append(f)
            fills.append(f)
        s.resting = keep

    # -- bars ------------------------------------------------------------------------------------

    @staticmethod
    def _bars(s: _Session, ticker: Any, resolution: str, since_day: int,
              limit: int | None) -> list[Bar]:
        if not isinstance(ticker, str) or ticker not in s.index:
            raise _bad(f"unknown ticker {ticker!r}; this session trades "
                       f"{', '.join(s.tickers)}")
        j = s.index[ticker]
        out: list[Bar] = []
        if resolution == "day":
            for day, blob in s.day_bars:
                if day >= since_day:
                    out.append(Bar(ticker, day, None, *_bar_of(blob, j)))
            if s.market_open and s.tick > 0 and s.day >= since_day:
                # The session in progress, up to the current step.
                engine = s.engine
                assert engine is not None
                live = [_f64(engine.column(c))[j] for c in ("open", "high", "low")]
                out.append(Bar(ticker, s.day, None, *live,
                               _f64(engine.prices())[j],
                               _f64(engine.column("volume"))[j]))
        else:
            for day, rows in s.step_sessions():
                if day < since_day:
                    continue
                for k, blob in enumerate(rows):
                    out.append(Bar(ticker, day, k, *_bar_of(blob, j)))
        return out[-limit:] if limit is not None else out

    # -- fork and close ----------------------------------------------------------------------

    def _fork(self, s: _Session, label: str) -> SessionInfo:
        # The fork is built the way a restart builds a session, from the
        # record and the history, so it is exactly what a resume would give.
        child = _Session.from_record(s.to_record(), s.streams())
        child.session_id = uuid.uuid4().hex
        child.parent = s.session_id
        child.created_at = _now()
        child.seq = 0
        if label:
            child.config.label = label
        # Every fill and finished order is the child's history too.
        child.new_fills = list(child.fills)
        child.new_done = sorted((o for o in child.orders.values()
                                 if o.status != "accepted"),
                                key=lambda o: _order_no(o.order_id))
        child.new_day_bars = list(child.day_bars)
        child.new_step_days = list(child.step_window)
        child.step_stream_len = 0
        child.new_news = list(child.news_log)
        with self._session_lock(child.session_id):
            self._commit(child, "fork_of", {"parent_session_id": s.session_id,
                                            "parent_seq": s.seq})
            self._remember(child)
        self._commit(s, "fork", {"label": label},
                     {"child_session_id": child.session_id})
        return child.info()

    def _close(self, s: _Session) -> SessionReport:
        at = s.clock()
        for o in s.open_orders():
            s.finish(o, "cancelled", at, "session closed")
        s.queue, s.resting = [], []
        s.status = "closed"
        state_hash = self._commit(s, "close", {})
        return SessionReport(session_id=s.session_id, account=self._account(s),
                             fills=len(s.fills), days=s.day + 1,
                             caveats=self._caveats(s), state_hash=state_hash)

    # -- caveats ------------------------------------------------------------------------------

    def _caveats(self, s: _Session) -> list[str]:
        """The caveats this session earns, computed now from the envelope,
        the long-run check and the session's own facts (the rule
        `tradefloor.mcp` states: never retyped prose)."""
        cfg = s.config
        days = s.day + 1
        out = [
            "The price process is a known model, not a forecast. A strategy "
            "that does well here has done well against this model; that does "
            "not transfer to real returns.",
        ]
        certified = _envelope.PRESET
        if cfg.preset != certified:
            out.append(f"Preset {cfg.preset} is not the certified default "
                       f"({certified}). The realism envelope was measured on "
                       f"{certified}, so none of its certification carries to "
                       f"this session.")

        failures = long_run_failures(cfg.preset)
        blk = long_run_verdict(cfg.preset)
        if failures is None:
            out.append(f"Preset {cfg.preset} has not been through the long-run "
                       f"check (21-year free runs and the 2008 and 2020 "
                       f"replays against the S&P 500 and VIX), so how its "
                       f"crashes and volatility regimes compare with real "
                       f"markets over multi-year sessions is unknown.")
        elif failures:
            out.append(
                f"Preset {cfg.preset} FAILS the long-run check on "
                f"{len(failures)} of {blk.get('of', '?')} criteria: over "
                f"multi-year sessions its volatility regimes and crash "
                f"frequency drift from real markets. "
                + "; ".join(failures)
                + f" ({blk.get('criteria', 'long-run criteria')}). This "
                f"session has run {days} trading day{'s' if days != 1 else ''}; "
                f"the certification below covers at most "
                f"{_envelope.CERTIFIED_HORIZON_DAYS}.")
        else:
            out.append(
                f"Preset {cfg.preset} passes the long-run check "
                f"({blk.get('passed')} of {blk.get('of')} criteria, "
                f"{blk.get('criteria', '')}): over 20 simulated years its "
                f"crash frequency, fear spells, volatility and returns sit "
                f"within the tolerances a user would notice, and the 2008 "
                f"and 2020 replays land within them. That is realism to a "
                f"degree, not a forecast: the tolerances are ratios such as "
                f"half to double the real rate.")

        verdict = _envelope.check(horizon_days=days)
        if not verdict.inside:
            out.append("Outside the certified realism envelope: "
                       + "; ".join(verdict.reasons))
        horizon = _envelope.CERTIFIED_HORIZON_DAYS
        if days < horizon // 4:
            out.append(
                f"SHORT WINDOW: {days} trading day{'s' if days != 1 else ''} "
                f"against a realism certification measured over {horizon}. "
                f"Volatility, autocorrelation and co-movement are annual "
                f"measurements and are not established over a window this "
                f"short.")

        resting = sum(1 for f in s.fills if f.liquidity == "resting")
        out.append(
            f"Resting limit fills do not move the market: {resting} of "
            f"{len(s.fills)} fills in this session were resting limits, filled "
            f"in full at the limit against each step's traded range after the "
            f"step was simulated, so the market never felt them. Market orders "
            f"(and limits marketable when placed) fill at the start of the "
            f"next step at the book's impact-aware price, and their flow does "
            f"move the market.")
        news = self._news_caveat(s)
        if news:
            out.append(news)
        out.append(
            "Limit fills see one print per name per tick: a price that "
            "crossed the limit inside a tick and came back is not seen, and "
            "there is no queue position and no partial fill.")
        out.append(
            "Orders meet only the simulator's market makers: other agents are "
            "not in the book, latency is zero, and there are no strategic "
            "counterparties.")
        if cfg.max_leverage is None:
            out.append(
                "Leverage is unbounded. The book makes large trades expensive, "
                "but with no funding limit arbitrarily large size is always "
                "available.")
        if cfg.universe_size < 30:
            out.append(f"A {cfg.universe_size}-name roster is small enough "
                       f"that one instrument's draw can dominate the result.")
        out.append("The roster is Universe.random, sector-BALANCED, which no "
                   "real index is: a named gap in the envelope.")
        assert s.engine is not None and s.portfolio is not None
        if s.portfolio.net_worth(s.engine) <= 0:
            out.append("The account is insolvent: net worth is at or below "
                       "zero, so leverage is reported as null.")
        return out

    @staticmethod
    def _news_caveat(s: _Session) -> str | None:
        """How much of a news move is left when its headline is first seen,
        from the preset's own news parameters and this session's step size.

        The engine adds `price_impact / 390` to the announcer's price on every
        tick of the news day (docs/serve/HEADLINES.md), and a headline is
        released at tick 1, so an agent first sees it at the end of its first
        step with the rest of the move still to come.
        """
        assert s.engine is not None
        params = s.engine.model_params
        intensity = float(params.get("endogenous_news_intensity", 0.0) or 0.0)
        sigma = float(params.get("endogenous_news_sigma", 0.0) or 0.0)
        if intensity <= 0 or sigma <= 0:
            return None
        seen = min(s.config.ticks_per_step, TICKS_PER_SESSION)
        left = (TICKS_PER_SESSION - seen) / TICKS_PER_SESSION
        mean_abs = sigma * math.sqrt(2.0 / math.pi)
        per_day = intensity * s.config.universe_size
        return (
            f"News in {s.config.preset} is priced in a straight line across the "
            f"whole session: the engine adds 1/{TICKS_PER_SESSION} of a news "
            f"event's price impact on every tick from the open to the close. "
            f"With news on {intensity:.0%} of names a day (about {per_day:.2g} "
            f"events a day on this roster) and a mean absolute impact of "
            f"{mean_abs:.2%}, a headline first seen at tick {seen} still has "
            f"about {left:.0%} of its move to come, worth about "
            f"{mean_abs * left * 1e4:.0f}bp an event to an agent that trades "
            f"its direction at once. Real markets price news in minutes, so "
            f"that edge does not carry over to money.")

