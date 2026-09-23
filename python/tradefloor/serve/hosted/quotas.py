"""Metering and limits: token buckets for rates, a ledger for daily quotas.

Two kinds of refusal, chosen by what a bot should do next:

    rate_limited    a per-minute bucket is empty. Retrying after the stated
                    number of seconds WILL succeed. (HTTP 429.)
    quota_exceeded  a daily or capacity allowance is used up. Retrying soon
                    will not help; the message says when it resets (UTC
                    midnight for daily ones) or what to free. (HTTP 429.)

A request that no retry could ever satisfy on this plan (a universe larger
than the plan allows, an advance longer than one call may run) is
`invalid_request` (HTTP 400) instead, naming the plan's cap, so a bot's retry
loop does not spin on it.

Buckets live in memory (a restart refills them, which is harmless). Daily
counters and per-session activity live in a JSON ledger flushed at most once a
second, so a crash forgets at most a second of metering.
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from tradefloor.serve.hosted._files import atomic_write_json, read_json
from tradefloor.serve.hosted.plans import TICKS_PER_SESSION, Plan
from tradefloor.serve.types import ServeError


def utc_day(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")


def next_utc_midnight(t: float) -> float:
    d = datetime.fromtimestamp(t, timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return (d + timedelta(days=1)).timestamp()


def _fmt_wait(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 10:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{int(seconds // 60)}m{int(seconds % 60):02d}s"
    return f"{int(seconds // 3600)}h{int(seconds % 3600 // 60):02d}m"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rate_limited(message: str, retry_after: float) -> ServeError:
    err = ServeError("rate_limited", message)
    err.retry_after = retry_after  # type: ignore[attr-defined]  # see HOSTED.md, contract requests
    return err


def quota_exceeded(message: str, retry_after: float | None = None) -> ServeError:
    err = ServeError("quota_exceeded", message)
    err.retry_after = retry_after  # type: ignore[attr-defined]
    return err


class TokenBucket:
    """`capacity` tokens, refilled at `rate` per second.

    A call costing more than the bucket can ever hold is admitted when the
    bucket is FULL and drives it negative (debt), so a legal large request is
    never refused forever; the next calls wait for the debt to refill."""

    __slots__ = ("rate", "capacity", "tokens", "t")

    def __init__(self, rate: float, capacity: float, now: float) -> None:
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.t = now

    def _refill(self, now: float) -> None:
        if now > self.t:
            self.tokens = min(self.capacity, self.tokens + (now - self.t) * self.rate)
            self.t = now

    def take(self, cost: float, now: float) -> float:
        """0.0 and the tokens are taken, or the seconds until they would be."""
        self._refill(now)
        need = min(cost, self.capacity)
        if self.tokens + 1e-9 >= need:
            self.tokens -= cost
            return 0.0
        return (need - self.tokens) / self.rate

    def give_back(self, n: float, now: float) -> None:
        self._refill(now)
        self.tokens = min(self.capacity, self.tokens + n)


@dataclass
class OwnerUsage:
    day: str = ""
    calls: int = 0
    steps: int = 0
    sim_ticks: int = 0
    compute_s: float = 0.0
    refused: int = 0
    total: dict[str, float] = field(default_factory=lambda: {
        "calls": 0, "steps": 0, "sim_ticks": 0, "compute_s": 0.0, "refused": 0})

    @property
    def sim_days(self) -> float:
        return self.sim_ticks / TICKS_PER_SESSION


@dataclass
class SessionActivity:
    owner: str
    status: str                 # "open" | "closed" | "expired"
    created: float
    last_active: float
    expired_at: float | None = None


class Quotas:
    """The meter. Thread-safe; one per server process."""

    def __init__(self, ledger_path: str | Path | None = None, *,
                 clock: Callable[[], float] = time.time,
                 flush_interval: float = 1.0) -> None:
        self.path = Path(ledger_path) if ledger_path is not None else None
        self.clock = clock
        self.flush_interval = flush_interval
        self._lock = threading.RLock()
        self._owners: dict[str, OwnerUsage] = {}
        self._sessions: dict[str, SessionActivity] = {}
        self._key_last_used: dict[str, float] = {}
        self._call_buckets: dict[str, TokenBucket] = {}
        self._step_buckets: dict[str, TokenBucket] = {}
        self._dirty = False
        self._last_flush = 0.0
        self._load()

    # -- persistence -------------------------------------------------------------

    def _load(self) -> None:
        if self.path is None:
            return
        raw = read_json(self.path, {})
        self._owners = {o: OwnerUsage(**u) for o, u in raw.get("owners", {}).items()}
        self._sessions = {s: SessionActivity(**a) for s, a in raw.get("sessions", {}).items()}
        self._key_last_used = dict(raw.get("keys", {}))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "version": 1,
                "owners": {o: vars(u) | {"total": dict(u.total)} for o, u in self._owners.items()},
                "sessions": {s: dict(vars(a)) for s, a in self._sessions.items()},
                "keys": dict(self._key_last_used),
            }

    def flush(self, force: bool = True) -> None:
        if self.path is None:
            return
        with self._lock:
            now = self.clock()
            if not self._dirty or (not force and now - self._last_flush < self.flush_interval):
                return
            data = self.snapshot()
            self._dirty = False
            self._last_flush = now
        atomic_write_json(self.path, data)

    # -- usage ----------------------------------------------------------------------

    def usage(self, owner: str) -> OwnerUsage:
        with self._lock:
            u = self._owners.get(owner)
            if u is None:
                u = self._owners[owner] = OwnerUsage()
            today = utc_day(self.clock())
            if u.day != today:
                u.day, u.calls, u.steps, u.sim_ticks, u.compute_s, u.refused = today, 0, 0, 0, 0.0, 0
            return u

    def _bucket(self, table: dict[str, TokenBucket], owner: str, per_minute: int) -> TokenBucket:
        b = table.get(owner)
        if b is None or b.capacity != per_minute:   # a plan change resizes the bucket
            b = table[owner] = TokenBucket(per_minute / 60.0, float(per_minute), self.clock())
        return b

    def admit_call(self, owner: str, plan: Plan, key_id: str | None = None) -> None:
        """One call against the per-minute call bucket, or `rate_limited`."""
        with self._lock:
            now = self.clock()
            wait = self._bucket(self._call_buckets, owner, plan.calls_per_minute).take(1, now)
            u = self.usage(owner)
            if wait:
                u.refused += 1
                u.total["refused"] += 1
                self._dirty = True
                raise rate_limited(
                    f"plan {plan.name!r} allows {plan.calls_per_minute} calls per minute; "
                    f"retry in {_fmt_wait(wait)}", wait)
            u.calls += 1
            u.total["calls"] += 1
            if key_id:
                self._key_last_used[key_id] = now
            self._dirty = True

    def check_daily(self, owner: str, plan: Plan, *, sim_ticks: int = 0,
                    compute: bool = False) -> None:
        """Refuse up front if this call would take `owner` past a daily quota.
        `sim_ticks` is an UPPER bound on the ticks the call will simulate."""
        with self._lock:
            now = self.clock()
            u = self.usage(owner)
            reset = next_utc_midnight(now)
            when = f"resets at {_iso(reset)} (in {_fmt_wait(reset - now)})"
            limit_ticks = plan.sim_days_per_day * TICKS_PER_SESSION
            if sim_ticks and u.sim_ticks + sim_ticks > limit_ticks:
                self._refuse(u)
                raise quota_exceeded(
                    f"plan {plan.name!r} allows {plan.sim_days_per_day:g} simulated days per UTC day; "
                    f"{u.sim_days:.2f} used and this call may run {sim_ticks / TICKS_PER_SESSION:.2f} more; "
                    f"{when}", reset - now)
            if compute and u.compute_s >= plan.compute_seconds_per_day:
                self._refuse(u)
                raise quota_exceeded(
                    f"plan {plan.name!r} allows {plan.compute_seconds_per_day:g} compute seconds per UTC "
                    f"day and {u.compute_s:.1f} are used; {when}", reset - now)

    def admit_steps(self, owner: str, plan: Plan, steps: int) -> None:
        with self._lock:
            now = self.clock()
            wait = self._bucket(self._step_buckets, owner, plan.steps_per_minute).take(steps, now)
            if wait:
                self._refuse(self.usage(owner))
                raise rate_limited(
                    f"plan {plan.name!r} allows {plan.steps_per_minute} simulation steps per minute "
                    f"and this call needs up to {steps}; retry in {_fmt_wait(wait)}", wait)

    def refund_steps(self, owner: str, steps: int) -> None:
        if steps <= 0:
            return
        with self._lock:
            b = self._step_buckets.get(owner)
            if b is not None:
                b.give_back(steps, self.clock())

    def _refuse(self, u: OwnerUsage) -> None:
        u.refused += 1
        u.total["refused"] += 1
        self._dirty = True

    def charge(self, owner: str, *, steps: int = 0, sim_ticks: int = 0,
               compute_s: float = 0.0) -> None:
        with self._lock:
            u = self.usage(owner)
            u.steps += steps
            u.sim_ticks += sim_ticks
            u.compute_s += compute_s
            u.total["steps"] += steps
            u.total["sim_ticks"] += sim_ticks
            u.total["compute_s"] += compute_s
            self._dirty = True
        self.flush(force=False)

    # -- sessions ----------------------------------------------------------------------

    def track(self, session_id: str, owner: str, status: str = "open") -> None:
        with self._lock:
            now = self.clock()
            a = self._sessions.get(session_id)
            if a is None:
                self._sessions[session_id] = SessionActivity(owner, status, now, now)
            else:
                a.status, a.last_active = status, now
            self._dirty = True

    def touch(self, session_id: str, owner: str) -> None:
        with self._lock:
            a = self._sessions.get(session_id)
            if a is not None and a.owner == owner:
                a.last_active = self.clock()
                self._dirty = True

    def mark(self, session_id: str, status: str) -> None:
        with self._lock:
            a = self._sessions.get(session_id)
            if a is not None:
                a.status = status
                if status == "expired":
                    a.expired_at = self.clock()
                self._dirty = True

    def activity(self, session_id: str) -> SessionActivity | None:
        with self._lock:
            return self._sessions.get(session_id)

    def sessions_of(self, owner: str | None = None) -> dict[str, SessionActivity]:
        with self._lock:
            return {s: a for s, a in self._sessions.items() if owner is None or a.owner == owner}

    def key_last_used(self, key_id: str) -> float | None:
        with self._lock:
            return self._key_last_used.get(key_id)


def estimate_advance_ticks(tick: int, market_open: bool, ticks_per_step: int,
                           steps: int, until: str) -> int:
    """An upper bound on the ticks one `advance` call will simulate, by the
    core's clock rules (contract 0.2): `close` runs to the end of this session
    (the next one if the market is closed); `next_open` finishes this session
    and opens the next without stepping it (nothing, if the market is already
    closed); with either, `steps` counts closes or opens, each further one a
    whole session."""
    if until == "steps":
        return max(0, int(steps)) * ticks_per_step
    remaining = max(TICKS_PER_SESSION - tick, 0) if market_open else 0
    more = (max(1, int(steps)) - 1) * TICKS_PER_SESSION
    if until == "close":
        return (remaining if remaining else TICKS_PER_SESSION) + more
    return remaining + more


def ticks_between(before_day: int, before_tick: int, after_day: int, after_tick: int) -> int:
    """Simulated ticks from one clock reading to a later one."""
    return max(0, (after_day - before_day) * TICKS_PER_SESSION + after_tick - before_tick)


def steps_for(ticks: int, ticks_per_step: int) -> int:
    return math.ceil(ticks / ticks_per_step) if ticks > 0 else 0
