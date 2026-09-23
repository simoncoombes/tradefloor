"""Where the hosted server keeps things, and how its parts are assembled.

One DATA directory holds everything the server and the admin CLI share:

    <data>/accounts.json         owners, plans, key hashes      (0600)
    <data>/usage.json            the metering ledger            (0600)
    <data>/audit/audit-*.jsonl   the audit log, one file per UTC day
    <data>/sessions/             FileStore root (when the store is "file")
    <data>/admin.json            the admin listener's port and token (0600),
                                 written by the server at start

Settings come from the environment (the container) or flags (the CLI):

    TRADEFLOOR_HOSTED_DATA        data directory     (default ~/.tradefloor/hosted)
    TRADEFLOOR_HOSTED_PEPPER      key-hash pepper    (required; from Secrets Manager)
    TRADEFLOOR_HOSTED_PLANS       plans file (JSON/YAML); default: the built-in plans
    TRADEFLOOR_HOSTED_STORE       "file" (default) or "s3"
    TRADEFLOOR_HOSTED_SESSIONS    FileStore root     (default <data>/sessions)
    TRADEFLOOR_HOSTED_S3_BUCKET   S3Store bucket     (store "s3")
    TRADEFLOOR_HOSTED_S3_PREFIX   S3Store key prefix (store "s3")
    TRADEFLOOR_HOSTED_TRUST_PROXY "1" behind a load balancer: take the client
                                  address from X-Forwarded-For (right-most)
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tradefloor.serve.hosted.accounts import Accounts
from tradefloor.serve.hosted.audit import AuditLog
from tradefloor.serve.hosted.plans import Plan, load_plans
from tradefloor.serve.hosted.quotas import Quotas
from tradefloor.serve.hosted.service import HostedService

PEPPER_ENV = "TRADEFLOOR_HOSTED_PEPPER"


@dataclass
class Settings:
    data: Path
    pepper: bytes = b""
    plans_path: Path | None = None
    store: str = "file"
    sessions: Path | None = None
    s3_bucket: str | None = None
    s3_prefix: str = ""
    trust_proxy: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None, **overrides: Any) -> "Settings":
        env = dict(os.environ if env is None else env)
        data = Path(overrides.pop("data", None) or env.get("TRADEFLOOR_HOSTED_DATA")
                    or Path.home() / ".tradefloor" / "hosted")
        plans = overrides.pop("plans_path", None) or env.get("TRADEFLOOR_HOSTED_PLANS")
        sessions = overrides.pop("sessions", None) or env.get("TRADEFLOOR_HOSTED_SESSIONS")
        s = cls(
            data=data,
            pepper=env.get(PEPPER_ENV, "").encode(),
            plans_path=Path(plans) if plans else None,
            store=env.get("TRADEFLOOR_HOSTED_STORE", "file"),
            sessions=Path(sessions) if sessions else None,
            s3_bucket=env.get("TRADEFLOOR_HOSTED_S3_BUCKET"),
            s3_prefix=env.get("TRADEFLOOR_HOSTED_S3_PREFIX", ""),
            trust_proxy=env.get("TRADEFLOOR_HOSTED_TRUST_PROXY", "") in ("1", "true", "yes"),
        )
        for k, v in overrides.items():
            if v is not None:
                setattr(s, k, v)
        return s

    @property
    def sessions_root(self) -> Path:
        return self.sessions or self.data / "sessions"

    def require_pepper(self, allow_empty: bool = False) -> None:
        if not self.pepper and not allow_empty:
            raise SystemExit(
                f"{PEPPER_ENV} is not set. It is the server-side secret mixed into every key "
                "hash; generate one with `python -c 'import secrets; print(secrets.token_urlsafe(32))'`, "
                "keep it in a secret store, and give the server and the admin CLI the SAME value. "
                "(--insecure-no-pepper runs without one, for local experiments only.)")
        if len(self.pepper) < 16 and not allow_empty:
            raise SystemExit(f"{PEPPER_ENV} is shorter than 16 characters")


def plans_of(s: Settings) -> dict[str, Plan]:
    return load_plans(s.plans_path)


def build_accounts(s: Settings) -> Accounts:
    return Accounts(s.data / "accounts.json", pepper=s.pepper, plans=plans_of(s))


def build_store(s: Settings):
    """The SessionStore and a storage meter for it."""
    if s.store == "file":
        from tradefloor.serve.store import FileStore
        root = s.sessions_root
        store = FileStore(root)

        def meter(owner: str, session_ids: list[str]) -> int:
            total = 0
            for sid in session_ids:
                d = root / sid
                if d.is_dir():
                    total += sum(p.stat().st_size for p in d.iterdir() if p.is_file())
            return total
        return store, meter
    if s.store == "s3":
        from tradefloor.serve.hosted.s3store import S3Store
        if not s.s3_bucket:
            raise SystemExit("TRADEFLOOR_HOSTED_STORE=s3 needs TRADEFLOOR_HOSTED_S3_BUCKET")
        store = S3Store(s.s3_bucket, s.s3_prefix)
        return store, (lambda owner, ids: store.size_of(ids))
    raise SystemExit(f"unknown store {s.store!r}: use file or s3")


def build_hosted(s: Settings, *, inner: Any = None, audit_stream: Any = None,
                 cache_size: int = 256) -> HostedService:
    """The whole hosted service from settings. `inner` overrides the core
    service (tests pass a fake)."""
    accounts = build_accounts(s)
    meter = None
    if inner is None:
        from tradefloor.serve.core import LocalSessionService
        store, meter = build_store(s)
        max_universe = max(p.max_universe_size for p in accounts.plans.values())
        inner = LocalSessionService(store, max_universe=max_universe, cache_size=cache_size)
    quotas = Quotas(s.data / "usage.json")
    audit = AuditLog(s.data / "audit", stream=audit_stream)
    return HostedService(inner, accounts, quotas, audit, storage_meter=meter)


def eprint(*a: Any) -> None:
    print(*a, file=sys.stderr)
