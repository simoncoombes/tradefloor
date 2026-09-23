"""From an HTTP request to an owner: the hosted layer's owner resolver.

Where the key may be sent, in order of precedence:

    Authorization: Bearer tfk_<id>_<secret>      the native API and hosted MCP
    X-API-Key: tfk_<id>_<secret>                 for clients that cannot set Authorization
    APCA-API-KEY-ID: tfk_<id>                    the broker-shaped facade: a bot written for
    APCA-API-SECRET-KEY: <secret>                a common paper-trading API sends its key in
                                                 these two headers, so pointing it at us needs
                                                 only a base URL and the key split in two
                                                 (or the whole key in the SECRET header)

A source address that keeps failing authentication (30 a minute by default)
gets `rate_limited` instead of `unauthorized` for its further failures, and
those are not audited again. A VALID key from that address still works, so a
broken bot behind a shared NAT address cannot lock out its neighbours.
Guessing cannot succeed anyway (256-bit secrets); the throttle keeps floods
out of the audit log and tells a misconfigured client to back off.

MCP: the contract's MCP transport is stdio, which a hosted service cannot
offer (stdio means the server runs on the user's machine). Hosted MCP means
the streamable-HTTP transport mounted in the same app, where the same header
carries the key; `owner_from_headers` is the function that transport calls
per request. See HOSTED.md, "Hosted MCP".
"""

from __future__ import annotations

import re
import threading
from collections import OrderedDict
from typing import Any, Mapping

from tradefloor.serve.hosted.quotas import TokenBucket, _fmt_wait, rate_limited
from tradefloor.serve.hosted.service import HostedService, Principal
from tradefloor.serve.types import ServeError

_KEY_ID_ONLY = re.compile(r"^tfk_[0-9a-f]{12}$")


def _lower(headers: Mapping[str, str] | Any) -> dict[str, str]:
    items = headers.items() if hasattr(headers, "items") else headers
    return {str(k).lower(): str(v) for k, v in items}


def extract_api_key(headers: Mapping[str, str] | Any) -> str | None:
    h = _lower(headers)
    auth = h.get("authorization", "").strip()
    if auth:
        scheme, _, value = auth.partition(" ")
        if scheme.lower() == "bearer" and value.strip():
            return value.strip()
    if h.get("x-api-key", "").strip():
        return h["x-api-key"].strip()
    secret = h.get("apca-api-secret-key", "").strip()
    if secret:
        if secret.startswith("tfk_"):
            return secret
        key_id = h.get("apca-api-key-id", "").strip()
        if _KEY_ID_ONLY.match(key_id):
            return f"{key_id}_{secret}"
    return None


class ApiKeyResolver:
    """`owner_resolver` for the HTTP app: request -> Principal (an owner id
    carrying the key id), or ServeError("unauthorized" | "rate_limited").

    Accepts a Starlette/FastAPI Request, or anything with `.headers`, or a
    bare header mapping (which is what an MCP tool context can provide)."""

    def __init__(self, hosted: HostedService, *, failed_auth_per_minute: int = 30,
                 trust_forwarded_for: bool = False, max_sources: int = 10_000) -> None:
        self.hosted = hosted
        self.failed_per_minute = failed_auth_per_minute
        self.trust_forwarded_for = trust_forwarded_for
        self.max_sources = max_sources
        self._fails: OrderedDict[str, TokenBucket] = OrderedDict()
        self._lock = threading.Lock()

    def source_of(self, request: Any) -> str | None:
        headers = _lower(getattr(request, "headers", {}) or {})
        if self.trust_forwarded_for and headers.get("x-forwarded-for"):
            # Behind an ALB the RIGHT-most entry is the address the ALB saw;
            # entries to its left are whatever the client chose to send.
            return headers["x-forwarded-for"].split(",")[-1].strip()
        client = getattr(request, "client", None)
        return getattr(client, "host", None)

    def _throttled(self, source: str | None) -> float:
        if source is None:
            return 0.0
        with self._lock:
            b = self._fails.get(source)
            if b is None:
                return 0.0
            b._refill(self.hosted.clock())
            return 0.0 if b.tokens >= 1 else (1 - b.tokens) / b.rate

    def _failed(self, source: str | None) -> None:
        if source is None:
            return
        with self._lock:
            now = self.hosted.clock()
            b = self._fails.get(source)
            if b is None:
                b = TokenBucket(self.failed_per_minute / 60.0, float(self.failed_per_minute), now)
                self._fails[source] = b
            self._fails.move_to_end(source)
            b.take(1, now)
            while len(self._fails) > self.max_sources:
                self._fails.popitem(last=False)

    def __call__(self, request: Any) -> Principal:
        headers = getattr(request, "headers", request)
        source = self.source_of(request) if hasattr(request, "headers") else None
        key = extract_api_key(headers)
        wait = self._throttled(source)
        if wait:
            # A valid key still gets in from a throttled address, so one broken
            # bot behind a shared NAT does not lock out the good ones. Failures
            # from it are answered 429 and not audited again.
            try:
                owner, key_id = self.hosted.accounts.authenticate(key)
            except ServeError:
                self._failed(source)
                raise rate_limited(f"too many failed authentications from this address; "
                                   f"retry in {_fmt_wait(wait)}", wait) from None
            return Principal(owner, key_id)
        try:
            return self.hosted.authenticate(key, source=source)
        except ServeError:
            self._failed(source)
            raise


def owner_from_headers(hosted: HostedService, headers: Mapping[str, str]) -> Principal:
    """For a transport that has the request headers but not a Request object
    (an MCP tool running under the streamable-HTTP transport)."""
    return hosted.authenticate(extract_api_key(headers))
