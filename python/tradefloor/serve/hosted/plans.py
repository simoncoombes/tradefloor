"""Plans: the limits an owner runs under.

A plan is data. The operator picks the numbers (docs/serve/HOSTED.md says
which ones are decisions to make before launch); the code only enforces them.
Every limit is named in the refusal it produces, so a bot author reading a
429 learns which dial they hit and when it resets.

What each limit protects:

    max_open_sessions        memory and the idle-expiry sweep
    max_stored_sessions      disk (closed sessions are kept for reports)
    storage_bytes            disk, measured when the store can measure it
    max_universe_size        compute per tick (cost is linear in names)
    min_ticks_per_step       per-step overhead: fills, persistence, a snapshot
    max_advance_ticks        the longest a single call may hold a worker
    max_open_orders          resting orders are checked every step
    calls_per_minute         request rate, all calls (token bucket)
    steps_per_minute         simulation rate (token bucket, charged per step)
    sim_days_per_day         simulated trading days per UTC day
    compute_seconds_per_day  wall seconds spent inside the service per UTC day
    idle_expiry_seconds      an open session untouched this long is closed
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from tradefloor.serve.types import ServeError

TICKS_PER_SESSION = 390


@dataclass(frozen=True)
class Plan:
    name: str
    max_open_sessions: int
    max_stored_sessions: int
    storage_bytes: int
    max_universe_size: int
    min_ticks_per_step: int
    max_advance_ticks: int
    max_open_orders: int
    calls_per_minute: int
    steps_per_minute: int
    sim_days_per_day: float
    compute_seconds_per_day: float
    idle_expiry_seconds: int

    def __post_init__(self) -> None:
        for f in fields(self):
            if f.name == "name":
                continue
            v = getattr(self, f.name)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not v > 0:
                raise ValueError(f"plan {self.name!r}: {f.name} must be a positive number, got {v!r}")
        if not 1 <= self.max_universe_size <= 40:
            raise ValueError(f"plan {self.name!r}: max_universe_size must be 1..40 (the core's range)")
        if self.min_ticks_per_step > TICKS_PER_SESSION:
            raise ValueError(f"plan {self.name!r}: min_ticks_per_step above one session")
        if self.max_advance_ticks > 20 * TICKS_PER_SESSION:
            raise ValueError(f"plan {self.name!r}: max_advance_ticks above the core's 20-session cap")
        if self.max_advance_ticks < TICKS_PER_SESSION:
            # `until="close"` / `"next_open"` must always be possible.
            raise ValueError(f"plan {self.name!r}: max_advance_ticks must be at least one session (390)")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, name: str, d: dict[str, Any]) -> "Plan":
        known = {f.name for f in fields(cls)}
        unknown = set(d) - known
        if unknown:
            raise ValueError(f"plan {name!r}: unknown fields {sorted(unknown)}")
        return cls(**{**d, "name": name})


# Placeholders until the owner sets prices and plans (HOSTED.md, "Decisions").
# They are sized so that 50 bots all at their limits fit one 1-vCPU task: the
# engine costs about 6 ms per simulated day at 20 names (measured on an M-class
# Mac, 2026-09-23), so the binding costs are request handling and persistence,
# which is why calls and steps per minute are the tight dials.
DEFAULT_PLANS: dict[str, Plan] = {
    p.name: p for p in (
        Plan(
            name="trial",
            max_open_sessions=2,
            max_stored_sessions=20,
            storage_bytes=100 * 2**20,
            max_universe_size=20,
            min_ticks_per_step=10,
            max_advance_ticks=TICKS_PER_SESSION,
            max_open_orders=50,
            calls_per_minute=60,
            steps_per_minute=60,
            sim_days_per_day=100,
            compute_seconds_per_day=300,
            idle_expiry_seconds=24 * 3600,
        ),
        Plan(
            name="standard",
            max_open_sessions=5,
            max_stored_sessions=200,
            storage_bytes=1024 * 2**20,
            max_universe_size=40,
            min_ticks_per_step=5,
            max_advance_ticks=5 * TICKS_PER_SESSION,
            max_open_orders=200,
            calls_per_minute=300,
            steps_per_minute=300,
            sim_days_per_day=1000,
            compute_seconds_per_day=1800,
            idle_expiry_seconds=7 * 24 * 3600,
        ),
        Plan(
            name="research",
            max_open_sessions=20,
            max_stored_sessions=2000,
            storage_bytes=10 * 1024 * 2**20,
            max_universe_size=40,
            min_ticks_per_step=1,
            max_advance_ticks=20 * TICKS_PER_SESSION,
            max_open_orders=1000,
            calls_per_minute=1200,
            steps_per_minute=3000,
            sim_days_per_day=20000,
            compute_seconds_per_day=6 * 3600,
            idle_expiry_seconds=30 * 24 * 3600,
        ),
    )
}


def load_plans(path: str | Path | None) -> dict[str, Plan]:
    """Plans from a JSON (or, if PyYAML is installed, YAML) file mapping plan
    name to its limits; None gives DEFAULT_PLANS. A file REPLACES the
    defaults rather than merging, so what is enforced is exactly what the
    operator wrote down."""
    if path is None:
        return dict(DEFAULT_PLANS)
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix in (".yml", ".yaml"):
        import yaml  # optional; only for YAML plan files
        raw = yaml.safe_load(text)
    else:
        raw = json.loads(text)
    if not isinstance(raw, dict) or not raw:
        raise ValueError(f"{p}: expected a mapping of plan name to limits")
    return {name: Plan.from_dict(name, body) for name, body in raw.items()}


def plan_or_error(plans: dict[str, Plan], name: str) -> Plan:
    try:
        return plans[name]
    except KeyError:
        raise ServeError("internal", f"plan {name!r} is not configured; "
                         f"known plans: {sorted(plans)}") from None
