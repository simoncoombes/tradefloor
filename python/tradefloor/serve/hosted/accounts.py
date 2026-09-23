"""Owners and their API keys, in one JSON file.

An OWNER is the account: it has a plan, and every session belongs to one. A
KEY belongs to an owner; an owner may hold several (one per bot, say) and
revoke one without disturbing the others. Quotas are counted per owner, so a
second key does not buy a second allowance.

The file is shared by the server and the admin CLI. Writers lock it and
replace it atomically; the server re-reads it when it changes on disk, so a
key revoked from the CLI stops working on the next request, without a restart.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from tradefloor.serve.hosted import keys as _keys
from tradefloor.serve.hosted._files import atomic_write_json, locked, read_json
from tradefloor.serve.hosted.plans import DEFAULT_PLANS, Plan
from tradefloor.serve.types import ServeError

OWNER_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,62}$")
DEFAULT_PLAN = "trial"


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class OwnerRecord:
    owner: str
    plan: str
    created_at: str
    suspended: bool = False
    note: str = ""


@dataclass
class KeyRecord:
    key_id: str
    owner: str
    salt: str
    hash: str
    created_at: str
    label: str = ""
    revoked_at: str | None = None

    @property
    def active(self) -> bool:
        return self.revoked_at is None

    def public(self) -> dict:
        """What may be shown: never the salt or hash."""
        return {"key_id": self.key_id, "owner": self.owner, "label": self.label,
                "created_at": self.created_at, "revoked_at": self.revoked_at}


class Accounts:
    def __init__(self, path: str | Path, *, pepper: bytes | str = b"",
                 plans: dict[str, Plan] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.pepper = pepper.encode() if isinstance(pepper, str) else pepper
        self.plans = dict(plans) if plans is not None else dict(DEFAULT_PLANS)
        self.clock = clock
        self._lock = threading.RLock()
        self._sig: tuple | None = None
        self._owners: dict[str, OwnerRecord] = {}
        self._keys: dict[str, KeyRecord] = {}
        self._pepper_check: str | None = None
        self._load()

    # -- persistence -------------------------------------------------------

    def _stat_sig(self) -> tuple | None:
        try:
            st = os.stat(self.path)
        except FileNotFoundError:
            return None
        return (st.st_ino, st.st_mtime_ns, st.st_size)

    def _load(self) -> None:
        raw = read_json(self.path, {"version": 1, "owners": {}, "keys": {}})
        self._owners = {o: OwnerRecord(**r) for o, r in raw.get("owners", {}).items()}
        self._keys = {k: KeyRecord(**r) for k, r in raw.get("keys", {}).items()}
        self._pepper_check = raw.get("pepper_check")
        self._sig = self._stat_sig()

    def _pepper_fingerprint(self) -> str:
        return hmac.new(self.pepper, b"tradefloor-hosted-pepper-check", hashlib.sha256).hexdigest()[:16]

    def pepper_matches(self) -> bool:
        """False when this file's keys were hashed under a DIFFERENT pepper, the
        mistake that makes every key fail: the server and the admin CLI must be
        given the same secret. The stored fingerprint is an HMAC of a constant,
        which says nothing about the pepper itself."""
        with self._lock:
            self._maybe_reload()
            return self._pepper_check in (None, self._pepper_fingerprint())

    def _maybe_reload(self) -> None:
        if self._stat_sig() != self._sig:
            self._load()

    def _save(self) -> None:
        if self._pepper_check is None and self._keys:
            self._pepper_check = self._pepper_fingerprint()
        atomic_write_json(self.path, {
            "version": 1,
            "pepper_check": self._pepper_check,
            "owners": {o: asdict(r) for o, r in self._owners.items()},
            "keys": {k: asdict(r) for k, r in self._keys.items()},
        })
        self._sig = self._stat_sig()

    def _mutate(self, fn):
        with self._lock, locked(self.path):
            self._load()
            out = fn()
            self._save()
            return out

    # -- owners --------------------------------------------------------------

    def _check_plan(self, plan: str) -> None:
        if plan not in self.plans:
            raise ValueError(f"unknown plan {plan!r}; known: {sorted(self.plans)}")

    def ensure_owner(self, owner: str, plan: str | None = None, note: str = "") -> OwnerRecord:
        if not OWNER_RE.match(owner):
            raise ValueError(f"owner {owner!r}: use 1-63 of a-z 0-9 . _ - starting with a letter or digit")
        if plan is not None:
            self._check_plan(plan)

        def fn():
            rec = self._owners.get(owner)
            if rec is None:
                rec = OwnerRecord(owner=owner, plan=plan or DEFAULT_PLAN,
                                  created_at=_iso(self.clock()), note=note)
                self._owners[owner] = rec
            elif plan is not None:
                rec.plan = plan
            return rec
        return self._mutate(fn)

    def set_plan(self, owner: str, plan: str) -> OwnerRecord:
        self._check_plan(plan)

        def fn():
            rec = self._require_owner(owner)
            rec.plan = plan
            return rec
        return self._mutate(fn)

    def set_suspended(self, owner: str, suspended: bool = True) -> OwnerRecord:
        def fn():
            rec = self._require_owner(owner)
            rec.suspended = suspended
            return rec
        return self._mutate(fn)

    def _require_owner(self, owner: str) -> OwnerRecord:
        rec = self._owners.get(owner)
        if rec is None:
            raise KeyError(f"no such owner {owner!r}")
        return rec

    def owner(self, owner: str) -> OwnerRecord | None:
        with self._lock:
            self._maybe_reload()
            return self._owners.get(owner)

    def owners(self) -> list[OwnerRecord]:
        with self._lock:
            self._maybe_reload()
            return sorted(self._owners.values(), key=lambda r: r.owner)

    def plan_of(self, owner: str) -> Plan:
        rec = self.owner(owner)
        name = rec.plan if rec else DEFAULT_PLAN
        try:
            return self.plans[name]
        except KeyError:
            raise ServeError("internal", f"owner {owner!r} is on plan {name!r}, which is "
                             f"not configured; tell the operator") from None

    def check_owner(self, owner: str) -> OwnerRecord:
        """The owner record for an authorised caller, or `unauthorized`."""
        rec = self.owner(str(owner))
        if rec is None:
            raise ServeError("unauthorized", "unknown owner")
        if rec.suspended:
            raise ServeError("unauthorized", f"account {rec.owner!r} is suspended; "
                             "contact the operator")
        return rec

    # -- keys ------------------------------------------------------------------

    def create_key(self, owner: str, *, plan: str | None = None,
                   label: str = "") -> tuple[KeyRecord, str]:
        """A new key for `owner` (created if new, on `plan` or the default).
        Returns the record and the PLAINTEXT key, which exists only in this
        return value: show it once and drop it."""
        if not self.pepper_matches():
            raise ValueError("this accounts file was written with a different pepper "
                             "(TRADEFLOOR_HOSTED_PEPPER); a key made now would never verify")
        self.ensure_owner(owner, plan)
        secret = _keys.new_secret()

        def fn():
            key_id = _keys.new_key_id()
            while key_id in self._keys:
                key_id = _keys.new_key_id()
            salt = _keys.new_salt()
            rec = KeyRecord(key_id=key_id, owner=owner, salt=salt,
                            hash=_keys.hash_secret(secret, salt, self.pepper),
                            created_at=_iso(self.clock()), label=label[:200])
            self._keys[key_id] = rec
            return rec
        rec = self._mutate(fn)
        return rec, _keys.format_key(rec.key_id, secret)

    def revoke_key(self, key_id: str) -> KeyRecord:
        def fn():
            rec = self._keys.get(key_id)
            if rec is None:
                raise KeyError(f"no such key {key_id!r}")
            if rec.revoked_at is None:
                rec.revoked_at = _iso(self.clock())
            return rec
        return self._mutate(fn)

    def revoke_all(self, owner: str) -> list[KeyRecord]:
        """Revoke every live key an owner holds: the first move on a leak."""
        def fn():
            self._require_owner(owner)
            out = []
            for rec in self._keys.values():
                if rec.owner == owner and rec.revoked_at is None:
                    rec.revoked_at = _iso(self.clock())
                    out.append(rec)
            return out
        return self._mutate(fn)

    def keys(self, owner: str | None = None) -> list[KeyRecord]:
        with self._lock:
            self._maybe_reload()
            return sorted((k for k in self._keys.values() if owner is None or k.owner == owner),
                          key=lambda k: (k.owner, k.created_at, k.key_id))

    def authenticate(self, api_key: str | None) -> tuple[str, str]:
        """(owner, key_id) for a live key, else `unauthorized`. The message
        says what is wrong without saying whether a key id exists."""
        if not api_key:
            raise ServeError("unauthorized", "no API key: send 'Authorization: Bearer tfk_...'")
        parsed = _keys.parse_key(api_key)
        if parsed is None:
            raise ServeError("unauthorized", "that is not a tradefloor API key "
                             "(expected tfk_<id>_<secret>)")
        with self._lock:
            self._maybe_reload()
            rec = self._keys.get(parsed.key_id)
            ok = rec is not None and _keys.verify_secret(parsed.secret, rec.salt, rec.hash, self.pepper)
            if not ok:
                raise ServeError("unauthorized", "API key not recognised")
            assert rec is not None
            if rec.revoked_at is not None:
                raise ServeError("unauthorized", f"API key {rec.key_id} was revoked at "
                                 f"{rec.revoked_at}; ask for a new one")
            owner = self._owners.get(rec.owner)
            if owner is None:
                raise ServeError("unauthorized", "API key not recognised")
            if owner.suspended:
                raise ServeError("unauthorized", f"account {owner.owner!r} is suspended; "
                                 "contact the operator")
            return rec.owner, rec.key_id
