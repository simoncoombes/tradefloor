"""An in-memory SessionService for testing the hosted layer without the engine.

It keeps the parts of the contract the hosted layer depends on: owner
isolation (`not_found` for another owner's session), the clock arithmetic of
`advance` as `LocalSessionService` does it (`until="next_open"` finishes the
current session and opens the next without stepping it; `until="close"` with
the market closed runs the next session; with either, `steps` counts closes or
opens, as contract 0.2 says), `session_closed` after `close`, the
20-session cap on one advance, and `client_order_id` idempotency. It simulates
no market: every quote is 100.
"""

from __future__ import annotations

import copy
import hashlib
import threading
import uuid

from tradefloor.serve.types import (
    Account,
    AdvanceResult,
    Clock,
    Fill,
    Observation,
    Order,
    OrderRequest,
    Quote,
    ServeError,
    SessionConfig,
    SessionInfo,
    SessionReport,
)

TICKS = 390


class FakeSessionService:
    def __init__(self, *, sleep_per_tick: float = 0.0) -> None:
        self.sessions: dict[str, dict] = {}
        self.sleep_per_tick = sleep_per_tick
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str]] = []

    def _get(self, owner: str, sid: str, *, open_only: bool = False) -> dict:
        s = self.sessions.get(sid)
        if s is None or s["info"].owner != owner:
            raise ServeError("not_found", f"no session {sid!r}")
        if open_only and s["info"].status != "open":
            raise ServeError("session_closed", f"session {sid} is closed")
        return s

    def _obs(self, s: dict) -> Observation:
        info = s["info"]
        acct = Account(cash=info.config.cash, net_worth=info.config.cash, gross_exposure=0.0,
                       leverage=0.0, realised_pnl=0.0, unrealised_pnl=0.0,
                       starting_cash=info.config.cash)
        quotes = [Quote(t, 100.0, 100.0, 100.0, 100.0, 100.0, 0.0) for t in info.tickers]
        h = hashlib.sha256(f"{info.session_id}{info.clock}".encode()).hexdigest()[:16]
        return Observation(session_id=info.session_id, clock=copy.copy(info.clock), quotes=quotes,
                           vix=20.0, macro={}, account=acct, positions=[],
                           open_orders=[o for o in s["orders"].values() if o.status == "accepted"],
                           state_hash=h)

    def open(self, owner: str, config: SessionConfig) -> SessionInfo:
        self.calls.append(("open", owner))
        if config.preset not in ("pt-v19", "pt-v18"):
            raise ServeError("invalid_request", f"unknown preset {config.preset!r}")
        if not 1 <= config.universe_size <= 40:
            raise ServeError("invalid_request", "universe_size must be 1..40")
        if not 1 <= config.ticks_per_step <= TICKS:
            raise ServeError("invalid_request", "ticks_per_step must be 1..390")
        sid = uuid.uuid4().hex
        info = SessionInfo(session_id=sid, owner=owner, config=copy.deepcopy(config),
                           model_fingerprint="fake", tickers=[f"T{i:02d}" for i in range(config.universe_size)],
                           clock=Clock(day=0, tick=0, step=0, market_open=True), status="open",
                           created_at="2026-09-23T00:00:00Z")
        with self._lock:
            self.sessions[sid] = {"info": info, "orders": {}, "fills": []}
        return copy.deepcopy(info)

    def info(self, owner: str, session_id: str) -> SessionInfo:
        self.calls.append(("info", owner))
        return copy.deepcopy(self._get(owner, session_id)["info"])

    def list(self, owner: str) -> list[SessionInfo]:
        self.calls.append(("list", owner))
        return [copy.deepcopy(s["info"]) for s in self.sessions.values() if s["info"].owner == owner]

    def observe(self, owner: str, session_id: str) -> Observation:
        self.calls.append(("observe", owner))
        return self._obs(self._get(owner, session_id))

    def place_order(self, owner: str, session_id: str, request: OrderRequest) -> Order:
        self.calls.append(("place_order", owner))
        s = self._get(owner, session_id, open_only=True)
        if request.ticker not in s["info"].tickers:
            raise ServeError("invalid_order", f"unknown ticker {request.ticker!r}")
        if request.client_order_id is not None:
            for o in s["orders"].values():
                if o.client_order_id == request.client_order_id:
                    return copy.deepcopy(o)
        oid = uuid.uuid4().hex
        o = Order(order_id=oid, client_order_id=request.client_order_id, ticker=request.ticker,
                  side=request.side, quantity=request.quantity, type=request.type,
                  limit_price=request.limit_price, time_in_force=request.time_in_force,
                  status="accepted", submitted_at=copy.copy(s["info"].clock))
        s["orders"][oid] = o
        return copy.deepcopy(o)

    def cancel_order(self, owner: str, session_id: str, order_id: str) -> Order:
        self.calls.append(("cancel_order", owner))
        s = self._get(owner, session_id, open_only=True)
        o = s["orders"].get(order_id)
        if o is None:
            raise ServeError("not_found", f"no order {order_id!r}")
        o.status = "cancelled"
        return copy.deepcopy(o)

    def orders(self, owner: str, session_id: str, status: str | None = None) -> list[Order]:
        self.calls.append(("orders", owner))
        s = self._get(owner, session_id)
        return [copy.deepcopy(o) for o in s["orders"].values() if status is None or o.status == status]

    def fills(self, owner: str, session_id: str, since_day: int = 0) -> list[Fill]:
        self.calls.append(("fills", owner))
        return list(self._get(owner, session_id)["fills"])

    def advance(self, owner: str, session_id: str, steps: int = 1, until: str = "steps") -> AdvanceResult:
        self.calls.append(("advance", owner))
        s = self._get(owner, session_id, open_only=True)
        if until not in ("steps", "close", "next_open"):
            raise ServeError("invalid_request", f"until must be steps, close or next_open, got {until!r}")
        if not isinstance(steps, int) or steps < 1:
            raise ServeError("invalid_request", "steps must be a positive integer")
        c: Clock = s["info"].clock
        tps = s["info"].config.ticks_per_step

        def open_next() -> None:
            c.day, c.tick, c.step, c.market_open = c.day + 1, 0, 0, True

        def one_step() -> None:
            if not c.market_open:
                open_next()
            c.tick = min(TICKS, c.tick + tps)
            c.step += 1
            if c.tick >= TICKS:
                c.market_open = False

        start_ticks = c.day * TICKS + c.tick
        if until == "steps":
            if steps * tps > 20 * TICKS:
                raise ServeError("invalid_request", "one call may not run more than 20 sessions")
            for _ in range(steps):
                one_step()
        elif until == "close":      # steps counts closes (contract 0.2)
            if steps > 20:
                raise ServeError("invalid_request", "one call may not run more than 20 sessions")
            for _ in range(steps):
                one_step()
                while c.market_open:
                    one_step()
        else:   # next_open: finish this session (if open), then open the next; steps counts opens
            if steps > 20:
                raise ServeError("invalid_request", "one call may not run more than 20 sessions")
            for _ in range(steps):
                while c.market_open:
                    one_step()
                open_next()
        if self.sleep_per_tick:
            import time
            time.sleep(self.sleep_per_tick * (c.day * TICKS + c.tick - start_ticks))
        return AdvanceResult(clock=copy.copy(c), fills=[], expired=[], observation=self._obs(s))

    def fork(self, owner: str, session_id: str, label: str = "") -> SessionInfo:
        self.calls.append(("fork", owner))
        s = self._get(owner, session_id)
        child = copy.deepcopy(s)
        sid = uuid.uuid4().hex
        child["info"].session_id = sid
        child["info"].parent_session_id = session_id
        child["info"].config.label = label
        child["info"].status = "open"
        with self._lock:
            self.sessions[sid] = child
        return copy.deepcopy(child["info"])

    def close(self, owner: str, session_id: str) -> SessionReport:
        self.calls.append(("close", owner))
        s = self._get(owner, session_id, open_only=True)
        s["info"].status = "closed"
        o = self._obs(s)
        return SessionReport(session_id=session_id, account=o.account, fills=0,
                             days=s["info"].clock.day + 1, caveats=["fake"], state_hash=o.state_hash)
