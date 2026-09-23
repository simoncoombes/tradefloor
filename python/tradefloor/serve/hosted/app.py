"""The hosted server's two ASGI apps.

PUBLIC (0.0.0.0:8080, behind the load balancer): the transport's HTTP app,
served over a HostedService with the API-key owner resolver, plus

    GET /healthz      no auth; the load balancer's health check
    GET /v1/usage     the caller's plan, today's usage and what remains

ADMIN (127.0.0.1:8081, never exposed): what the admin CLI needs from the
RUNNING server, because sessions live in its memory and only one process may
write them. Every request carries the token the server wrote to
`<data>/admin.json` at start (0600).

    GET  /admin/sessions?owner=O     the owner's sessions and their activity
    GET  /admin/usage?owner=O        live usage
    POST /admin/expire-idle[?owner=O]
    POST /admin/reconcile
"""

# No `from __future__ import annotations`: the routes are closures that import
# FastAPI lazily, and FastAPI resolves string annotations against module
# globals, where `Request` is not. (The transport's http.py says the same.)

import hmac
from typing import Any, Callable

from tradefloor.serve.hosted.resolver import ApiKeyResolver
from tradefloor.serve.hosted.service import HostedService
from tradefloor.serve.types import ServeError

def create_hosted_app(hosted: HostedService, *, trust_proxy: bool = False,
                      resolver: ApiKeyResolver | None = None,
                      create_app: Callable[..., Any] | None = None):
    """The transport's app over `hosted`, with the hosted routes in front."""
    from fastapi import APIRouter, Request

    import inspect

    from tradefloor.serve.http import error_response

    if create_app is None:
        from tradefloor.serve.http import create_app
    resolver = resolver or ApiKeyResolver(hosted, trust_forwarded_for=trust_proxy)
    kwargs: dict[str, Any] = {"owner_resolver": resolver}
    if "serialize" in inspect.signature(create_app).parameters:
        # Contract 0.3, section 4d: a SessionService is safe across threads
        # (a lock per session), so no global lock. HostedService's meter,
        # accounts and audit log lock themselves. Bots in different sessions
        # then run concurrently instead of queueing behind each other's writes.
        kwargs["serialize"] = False
    app = create_app(hosted, **kwargs)

    router = APIRouter()

    @router.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, Any]:
        return {"ok": True}

    @router.get("/v1/usage", tags=["hosted"])
    def usage(request: Request):
        """Your plan, today's usage against it (UTC day), and what remains."""
        try:
            return hosted.usage(resolver(request))
        except ServeError as e:
            return error_response(e)

    # In front of the transport's routes, so no catch-all of theirs shadows these.
    app.router.routes[0:0] = router.routes
    return app


def create_admin_app(hosted: HostedService, token: str):
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

    app = FastAPI(title="tradefloor hosted admin", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        got = request.headers.get("x-admin-token", "")
        if not hmac.compare_digest(got.encode(), token.encode()):
            return JSONResponse({"code": "unauthorized", "message": "admin token required"},
                                status_code=401)
        return await call_next(request)

    @app.get("/admin/sessions")
    def sessions(owner: str) -> list[dict[str, Any]]:
        activity = hosted.quotas.sessions_of(owner)
        out = []
        for info in hosted.inner.list(owner):
            a = activity.get(info.session_id)
            d = info.to_dict()
            d["activity"] = None if a is None else dict(vars(a))
            out.append(d)
        return out

    @app.get("/admin/usage")
    def usage(owner: str) -> dict[str, Any]:
        return hosted.usage_report(owner)

    @app.post("/admin/expire-idle")
    def expire(owner: str | None = None) -> dict[str, Any]:
        return {"expired": hosted.expire_idle(owner)}

    @app.post("/admin/reconcile")
    def reconcile() -> dict[str, Any]:
        return hosted.reconcile()

    return app
