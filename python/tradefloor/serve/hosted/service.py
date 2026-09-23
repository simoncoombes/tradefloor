"""HostedService: any SessionService, authorised, metered, limited and audited.

    hosted = HostedService(LocalSessionService(FileStore(root)), accounts, quotas, audit)
    principal = hosted.authenticate("tfk_...")        # owner from the API key
    hosted.open(principal, SessionConfig(...))        # the SessionService protocol

It IS a SessionService, so every transport serves it unchanged. The `owner`
each method receives is the one the transport's owner resolver produced from
the API key; the hosted layer checks that owner is a live account, applies its
plan, and passes the same owner to the inner service, whose own owner check
keeps one owner's sessions invisible to another (`not_found`).

Order of checks on every call, cheapest first, so a refused call costs little:

    1. the owner is a live, unsuspended account          unauthorized
    2. idle sessions of this owner are expired (lazily)
    3. the per-minute call bucket                          rate_limited
    4. per-request caps of the plan                        invalid_request
    5. capacity (open/stored sessions, storage, orders)    quota_exceeded
    6. daily quotas (simulated days, compute seconds)      quota_exceeded
    7. the per-minute step bucket (advance only)           rate_limited
    8. the inner call, timed; its wall time is charged as compute
"""

from __future__ import annotations

import contextlib
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from tradefloor.serve.hosted.accounts import Accounts
from tradefloor.serve.hosted.audit import MUTATING_CALLS, AuditLog
from tradefloor.serve.hosted.plans import Plan
from tradefloor.serve.hosted.quotas import (
    Quotas,
    _fmt_wait,
    estimate_advance_ticks,
    quota_exceeded,
    steps_for,
    ticks_between,
)
from tradefloor.serve.types import (
    AdvanceResult,
    Bar,
    Fill,
    Headline,
    Observation,
    Order,
    OrderRequest,
    ServeError,
    SessionConfig,
    SessionInfo,
    SessionReport,
    SessionService,
)

MAX_LABEL = 200
MAX_CLIENT_ORDER_ID = 64
_UNTIL = ("steps", "close", "next_open")


class Principal(str):
    """An owner id that remembers which API key authenticated it.

    A plain `str` to every layer that does not care (the transports and the
    core see the owner id), while the hosted layer reads `key_id` for the
    audit log."""

    key_id: str | None

    def __new__(cls, owner: str, key_id: str | None = None) -> "Principal":
        s = super().__new__(cls, owner)
        s.key_id = key_id
        return s


@dataclass
class _Ctx:
    owner: str
    key_id: str | None
    call: str
    session_id: str | None
    plan: Plan | None = None
    compute_s: float = 0.0
    steps: int = 0
    sim_ticks: int = 0
    detail: dict[str, Any] = field(default_factory=dict)
    perf: Callable[[], float] = time.perf_counter

    def timed(self, fn: Callable[..., Any], *args: Any) -> Any:
        t = self.perf()
        try:
            return fn(*args)
        finally:
            self.compute_s += self.perf() - t


StorageMeter = Callable[[str, list[str]], int]


class HostedService:
    """See the module docstring. Thread-safe for concurrent calls from one
    process; one process per data directory (see HOSTED.md, "Scaling")."""

    def __init__(self, inner: SessionService, accounts: Accounts, quotas: Quotas,
                 audit: AuditLog, *, storage_meter: StorageMeter | None = None,
                 audit_reads: bool = False, clock: Callable[[], float] | None = None,
                 perf: Callable[[], float] = time.perf_counter) -> None:
        self.inner = inner
        self.accounts = accounts
        self.quotas = quotas
        self.audit = audit
        self.storage_meter = storage_meter
        self.audit_reads = audit_reads
        self.clock = clock or quotas.clock
        self.perf = perf
        self._owner_locks: dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()
        self._auth_fail: dict[str, tuple[float, int]] = {}
        self._sweeper: threading.Thread | None = None
        self._stop = threading.Event()

    # -- authentication -------------------------------------------------------------

    def authenticate(self, api_key: str | None, *, source: str | None = None) -> Principal:
        """The owner an API key belongs to, or `unauthorized`. Failures are
        audited, at most one line per key id (or source) per 10 seconds with a
        count of the ones folded in, so a flood of bad keys cannot fill the
        disk through the audit log."""
        try:
            owner, key_id = self.accounts.authenticate(api_key)
        except ServeError as e:
            from tradefloor.serve.hosted.keys import parse_key
            parsed = parse_key(api_key or "")
            self._audit_auth_failure(parsed.key_id if parsed else None, source, e)
            raise
        return Principal(owner, key_id)

    def _audit_auth_failure(self, key_id: str | None, source: str | None, e: ServeError) -> None:
        now = self.clock()
        tag = key_id or f"src:{source or '-'}"
        with self._locks_lock:
            last, folded = self._auth_fail.get(tag, (0.0, 0))
            if now - last < 10.0:
                self._auth_fail[tag] = (last, folded + 1)
                return
            self._auth_fail[tag] = (now, 0)
            if len(self._auth_fail) > 10_000:
                cutoff = now - 60.0
                self._auth_fail = {k: v for k, v in self._auth_fail.items() if v[0] >= cutoff}
        detail = {"source": source} if source else {}
        if folded:
            detail["folded"] = folded
        self.audit.record(call="authenticate", outcome=e.code, key_id=key_id,
                          error=e.message, detail=detail or None)

    # -- the wrapper every call goes through -------------------------------------------

    def _owner_lock(self, owner: str) -> threading.Lock:
        with self._locks_lock:
            lock = self._owner_locks.get(owner)
            if lock is None:
                lock = self._owner_locks[owner] = threading.Lock()
            return lock

    @contextlib.contextmanager
    def _call(self, owner: str, call: str, session_id: str | None = None, *,
              heavy: bool = False) -> Iterator[_Ctx]:
        ctx = _Ctx(owner=str(owner), key_id=getattr(owner, "key_id", None), call=call,
                   session_id=session_id, perf=self.perf)
        t0 = self.perf()
        outcome, error = "ok", None
        try:
            self.accounts.check_owner(ctx.owner)
            ctx.plan = self.accounts.plan_of(ctx.owner)
            self._expire_idle_of(ctx.owner, ctx.plan)
            self.quotas.admit_call(ctx.owner, ctx.plan, ctx.key_id)
            if heavy:
                self.quotas.check_daily(ctx.owner, ctx.plan, compute=True)
            if session_id is not None:
                self.quotas.touch(session_id, ctx.owner)
            yield ctx
        except ServeError as e:
            outcome, error = e.code, e.message
            if e.code in ("rate_limited", "quota_exceeded") and ctx.plan is not None:
                self.quotas.note_refusal(ctx.owner)
            explained = self._explain_closed(e, ctx)
            if explained is not e:
                error = explained.message
                raise explained from e
            raise
        except Exception as e:
            outcome, error = "internal", f"{type(e).__name__}: {e}"
            raise
        finally:
            if ctx.compute_s or ctx.steps or ctx.sim_ticks:
                self.quotas.charge(ctx.owner, steps=ctx.steps, sim_ticks=ctx.sim_ticks,
                                   compute_s=ctx.compute_s)
            else:
                self.quotas.flush(force=False)
            if call in MUTATING_CALLS or self.audit_reads:
                detail = dict(ctx.detail)
                if ctx.compute_s:
                    detail["compute_ms"] = round(ctx.compute_s * 1000, 3)
                if ctx.sim_ticks:
                    detail["sim_ticks"] = ctx.sim_ticks
                self.audit.record(call=call, outcome=outcome, owner=ctx.owner, key_id=ctx.key_id,
                                  session_id=ctx.session_id,
                                  duration_ms=(self.perf() - t0) * 1000,
                                  error=error, detail=detail or None)

    def _explain_closed(self, e: ServeError, ctx: _Ctx) -> ServeError:
        """A session the server expired says so, instead of a bare
        `session_closed` the bot's author would have to puzzle over."""
        if e.code != "session_closed" or ctx.session_id is None:
            return e
        a = self.quotas.activity(ctx.session_id)
        if a is None or a.status != "expired" or a.owner != ctx.owner:
            return e
        plan = ctx.plan or self.accounts.plan_of(ctx.owner)
        return ServeError("session_closed", f"{e.message} (closed by the server: idle longer than "
                          f"{_fmt_wait(plan.idle_expiry_seconds)}, the limit on plan {plan.name!r}; "
                          f"its report and fills are still readable)")

    # -- plan checks ----------------------------------------------------------------------

    @staticmethod
    def _check_config(plan: Plan, config: SessionConfig) -> None:
        if not isinstance(config, SessionConfig):
            raise ServeError("invalid_request", "config must be a SessionConfig")
        if isinstance(config.universe_size, int) and config.universe_size > plan.max_universe_size:
            raise ServeError("invalid_request", f"universe_size {config.universe_size} is above the "
                             f"{plan.max_universe_size} plan {plan.name!r} allows")
        if isinstance(config.ticks_per_step, int) and config.ticks_per_step < plan.min_ticks_per_step:
            raise ServeError("invalid_request", f"ticks_per_step {config.ticks_per_step} is below the "
                             f"{plan.min_ticks_per_step} plan {plan.name!r} allows")
        if isinstance(config.label, str) and len(config.label) > MAX_LABEL:
            raise ServeError("invalid_request", f"label is longer than {MAX_LABEL} characters")

    def _check_capacity(self, ctx: _Ctx, plan: Plan) -> None:
        """Open sessions, stored sessions and storage, for open and fork."""
        sessions = self.inner.list(ctx.owner)
        open_ids = [s.session_id for s in sessions if s.status == "open"]
        if len(open_ids) >= plan.max_open_sessions:
            raise quota_exceeded(
                f"plan {plan.name!r} allows {plan.max_open_sessions} open sessions and "
                f"{len(open_ids)} are open ({', '.join(open_ids[:5])}"
                f"{', ...' if len(open_ids) > 5 else ''}); close one first")
        if len(sessions) >= plan.max_stored_sessions:
            raise quota_exceeded(
                f"plan {plan.name!r} keeps at most {plan.max_stored_sessions} sessions (open and "
                f"closed) and {len(sessions)} are stored; ask the operator to delete old ones")
        if self.storage_meter is not None:
            used = self.storage_meter(ctx.owner, [s.session_id for s in sessions])
            if used >= plan.storage_bytes:
                raise quota_exceeded(
                    f"plan {plan.name!r} allows {plan.storage_bytes / 2**20:.0f} MiB of stored "
                    f"sessions and {used / 2**20:.1f} MiB are used; ask the operator to delete old ones")

    # -- the SessionService protocol --------------------------------------------------------

    def open(self, owner: str, config: SessionConfig) -> SessionInfo:
        with self._call(owner, "open", heavy=True) as c:
            assert c.plan is not None
            if isinstance(config, SessionConfig):
                c.detail = {"preset": config.preset, "seed": config.seed,
                            "universe_size": config.universe_size,
                            "ticks_per_step": config.ticks_per_step}
            self._check_config(c.plan, config)
            with self._owner_lock(c.owner):
                self._check_capacity(c, c.plan)
                info = c.timed(self.inner.open, c.owner, config)
                self.quotas.track(info.session_id, c.owner, "open")
            c.session_id = info.session_id
            return info

    def info(self, owner: str, session_id: str) -> SessionInfo:
        with self._call(owner, "info", session_id) as c:
            return c.timed(self.inner.info, c.owner, session_id)

    def list(self, owner: str) -> list[SessionInfo]:
        with self._call(owner, "list") as c:
            return c.timed(self.inner.list, c.owner)

    def observe(self, owner: str, session_id: str) -> Observation:
        with self._call(owner, "observe", session_id) as c:
            return c.timed(self.inner.observe, c.owner, session_id)

    def place_order(self, owner: str, session_id: str, request: OrderRequest) -> Order:
        with self._call(owner, "place_order", session_id) as c:
            assert c.plan is not None
            if isinstance(request, OrderRequest):
                c.detail = {"ticker": request.ticker, "side": request.side,
                            "quantity": request.quantity, "type": request.type,
                            "limit_price": request.limit_price,
                            "time_in_force": request.time_in_force,
                            "client_order_id": request.client_order_id}
                coid = request.client_order_id
                if isinstance(coid, str) and len(coid) > MAX_CLIENT_ORDER_ID:
                    raise ServeError("invalid_request",
                                     f"client_order_id is longer than {MAX_CLIENT_ORDER_ID} characters")
                existing = c.timed(self.inner.orders, c.owner, session_id)
                replay = coid is not None and any(o.client_order_id == coid for o in existing)
                resting = sum(1 for o in existing if o.status == "accepted")
                if not replay and resting >= c.plan.max_open_orders:
                    raise quota_exceeded(
                        f"plan {c.plan.name!r} allows {c.plan.max_open_orders} open orders per "
                        f"session and {resting} are open; cancel some first")
            order = c.timed(self.inner.place_order, c.owner, session_id, request)
            c.detail.update(order_id=order.order_id, status=order.status)
            return order

    def cancel_order(self, owner: str, session_id: str, order_id: str) -> Order:
        with self._call(owner, "cancel_order", session_id) as c:
            c.detail = {"order_id": order_id}
            order = c.timed(self.inner.cancel_order, c.owner, session_id, order_id)
            c.detail["status"] = order.status
            return order

    # History reads return lists whose length the caller chooses (a whole
    # session's step bars, every headline since day 0). Each costs one call per
    # started `plan.items_per_call` items, charged after it runs (see
    # Quotas.charge_items), so a bot paging in small reads and a bot taking it
    # all at once spend the same allowance.

    def _read(self, c: _Ctx, fn: Callable[..., Any], *args: Any) -> Any:
        out = c.timed(fn, c.owner, *args)
        assert c.plan is not None
        extra = self.quotas.charge_items(c.owner, c.plan, len(out))
        c.detail = {"items": len(out), **({"extra_calls": extra} if extra else {})}
        return out

    def orders(self, owner: str, session_id: str, status: str | None = None) -> list[Order]:
        with self._call(owner, "orders", session_id) as c:
            return self._read(c, self.inner.orders, session_id, status)

    def fills(self, owner: str, session_id: str, since_day: int = 0) -> list[Fill]:
        with self._call(owner, "fills", session_id) as c:
            return self._read(c, self.inner.fills, session_id, since_day)

    def bars(self, owner: str, session_id: str, ticker: str, resolution: str = "day",
             since_day: int = 0, limit: int | None = None) -> list[Bar]:
        with self._call(owner, "bars", session_id) as c:
            return self._read(c, self.inner.bars, session_id, ticker, resolution,
                              since_day, limit)

    def news(self, owner: str, session_id: str, since_day: int = 0, since_tick: int = 0,
             limit: int | None = None) -> list[Headline]:
        with self._call(owner, "news", session_id) as c:
            return self._read(c, self.inner.news, session_id, since_day, since_tick, limit)

    def advance(self, owner: str, session_id: str, steps: int = 1,
                until: str = "steps") -> AdvanceResult:
        with self._call(owner, "advance", session_id, heavy=True) as c:
            plan = c.plan
            assert plan is not None
            c.detail = {"steps": steps, "until": until}
            well_formed = (until in _UNTIL and isinstance(steps, int)
                           and not isinstance(steps, bool) and steps >= 1)
            if not well_formed:
                # The core owns the wording of this refusal; nothing is simulated.
                return c.timed(self.inner.advance, c.owner, session_id, steps, until)
            before = c.timed(self.inner.info, c.owner, session_id)
            if before.status != "open":
                return c.timed(self.inner.advance, c.owner, session_id, steps, until)
            tps = before.config.ticks_per_step
            est = estimate_advance_ticks(before.clock.tick, before.clock.market_open, tps, steps, until)
            if est > plan.max_advance_ticks:
                asked = (f"{steps} steps x {tps} ticks = {est} ticks" if until == "steps"
                         else f"until={until!r} x {steps} = up to {est} ticks")
                n = plan.max_advance_ticks / 390
                raise ServeError("invalid_request",
                                 f"advance of {asked} is above the {plan.max_advance_ticks} ticks "
                                 f"({n:g} session{'' if n == 1 else 's'}) one call may run on "
                                 f"plan {plan.name!r}; split it")
            self.quotas.check_daily(c.owner, plan, sim_ticks=est)
            est_steps = steps_for(est, tps)
            self.quotas.admit_steps(c.owner, plan, est_steps)
            try:
                result = c.timed(self.inner.advance, c.owner, session_id, steps, until)
            except BaseException:
                self.quotas.refund_steps(c.owner, est_steps)
                raise
            c.sim_ticks = ticks_between(before.clock.day, before.clock.tick,
                                        result.clock.day, result.clock.tick)
            c.steps = steps if until == "steps" else steps_for(c.sim_ticks, tps)
            self.quotas.refund_steps(c.owner, est_steps - c.steps)
            c.detail.update(fills=len(result.fills), day=result.clock.day, tick=result.clock.tick)
            return result

    def fork(self, owner: str, session_id: str, label: str = "") -> SessionInfo:
        with self._call(owner, "fork", session_id, heavy=True) as c:
            assert c.plan is not None
            if isinstance(label, str) and len(label) > MAX_LABEL:
                raise ServeError("invalid_request", f"label is longer than {MAX_LABEL} characters")
            with self._owner_lock(c.owner):
                self._check_capacity(c, c.plan)
                info = c.timed(self.inner.fork, c.owner, session_id, label)
                self.quotas.track(info.session_id, c.owner, "open")
            c.detail = {"child_session_id": info.session_id}
            return info

    def close(self, owner: str, session_id: str) -> SessionReport:
        with self._call(owner, "close", session_id) as c:
            report = c.timed(self.inner.close, c.owner, session_id)
            self.quotas.mark(session_id, "closed")
            c.detail = {"days": report.days, "fills": report.fills}
            return report

    # -- idle expiry -----------------------------------------------------------------------------

    def _expire_idle_of(self, owner: str, plan: Plan) -> None:
        now = self.clock()
        for sid, a in self.quotas.sessions_of(owner).items():
            if a.status == "open" and now - a.last_active >= plan.idle_expiry_seconds:
                self._expire(sid, owner, plan, now - a.last_active)

    def expire_idle(self, owner: str | None = None) -> list[str]:
        """Close every open session idle longer than its owner's plan allows.
        Returns the session ids closed. Run by the sweeper thread, by the
        admin CLI, and lazily at the start of each owner's calls."""
        now = self.clock()
        out = []
        for sid, a in self.quotas.sessions_of(owner).items():
            if a.status != "open":
                continue
            plan = self.accounts.plan_of(a.owner)
            idle = now - a.last_active
            if idle >= plan.idle_expiry_seconds and self._expire(sid, a.owner, plan, idle):
                out.append(sid)
        self.quotas.flush()
        return out

    def _expire(self, session_id: str, owner: str, plan: Plan, idle: float) -> bool:
        t0 = self.perf()
        try:
            self.inner.close(owner, session_id)
            outcome, error, status = "ok", None, "expired"
        except ServeError as e:
            # Already closed, or gone from the store: nothing to expire.
            outcome, error = e.code, e.message
            status = "closed" if e.code in ("session_closed", "not_found") else "open"
        except Exception as e:  # the sweep must survive one bad session
            outcome, error, status = "internal", f"{type(e).__name__}: {e}", "open"
        self.quotas.mark(session_id, status)
        if status == "open":  # try again next sweep, not on every call
            self.quotas.touch(session_id, owner)
        self.audit.record(actor="system", call="expire", owner=owner, session_id=session_id,
                          outcome=outcome, error=error, duration_ms=(self.perf() - t0) * 1000,
                          detail={"idle_s": round(idle, 1), "plan": plan.name,
                                  "idle_expiry_s": plan.idle_expiry_seconds})
        return status == "expired"

    def reconcile(self) -> dict[str, int]:
        """Bring the activity ledger in line with the store at startup: open
        sessions the ledger does not know (a crash inside the last flush
        interval) are tracked from now; sessions the store has closed are
        marked closed."""
        added = closed = 0
        for rec in self.accounts.owners():
            try:
                sessions = self.inner.list(rec.owner)
            except ServeError:
                continue
            for s in sessions:
                a = self.quotas.activity(s.session_id)
                if s.status == "open" and (a is None or a.status != "open"):
                    self.quotas.track(s.session_id, rec.owner, "open")
                    added += 1
                elif s.status == "closed" and a is not None and a.status == "open":
                    self.quotas.mark(s.session_id, "closed")
                    closed += 1
        self.quotas.flush()
        return {"tracked": added, "marked_closed": closed}

    def start_sweeper(self, interval: float = 60.0) -> None:
        """A daemon thread that expires idle sessions and flushes the ledger."""
        if self._sweeper is not None:
            return

        def loop() -> None:
            while not self._stop.wait(interval):
                try:
                    self.expire_idle()
                except Exception:  # pragma: no cover - keep sweeping
                    pass
        self._sweeper = threading.Thread(target=loop, name="tradefloor-hosted-sweeper", daemon=True)
        self._sweeper.start()

    def stop(self) -> None:
        self._stop.set()
        if self._sweeper is not None:
            self._sweeper.join(timeout=5)
            self._sweeper = None
        self.quotas.flush()

    # -- reporting ------------------------------------------------------------------------------

    def usage(self, owner: str) -> dict[str, Any]:
        """The caller's own plan and usage (`GET /v1/usage`): authorised and
        rate limited like any call, so it cannot be used to probe others."""
        with self._call(owner, "usage") as c:
            return usage_report(self.accounts, self.quotas, c.owner, self.storage_meter)

    def usage_report(self, owner: str) -> dict[str, Any]:
        return usage_report(self.accounts, self.quotas, owner, self.storage_meter)


def usage_report(accounts: Accounts, quotas: Quotas, owner: str,
                 storage_meter: StorageMeter | None = None) -> dict[str, Any]:
    """The plan, today's usage against it, and the owner's sessions: what
    `GET /v1/usage` returns and the admin CLI prints. Needs no inner service,
    so the CLI can print it from the files alone."""
    plan = accounts.plan_of(owner)
    u = quotas.usage(owner)
    sessions = quotas.sessions_of(owner)
    open_n = sum(1 for a in sessions.values() if a.status == "open")
    report: dict[str, Any] = {
        "owner": owner,
        "plan": plan.to_dict(),
        "today": {"utc_day": u.day, "calls": u.calls, "steps": u.steps,
                  "sim_days": round(u.sim_days, 3), "compute_seconds": round(u.compute_s, 3),
                  "refused": u.refused},
        "remaining_today": {
            "sim_days": round(max(0.0, plan.sim_days_per_day - u.sim_days), 3),
            "compute_seconds": round(max(0.0, plan.compute_seconds_per_day - u.compute_s), 3)},
        "total": dict(u.total),
        "sessions": {"open": open_n, "tracked": len(sessions)},
    }
    if storage_meter is not None:
        report["storage_bytes"] = storage_meter(owner, list(sessions))
    return report
