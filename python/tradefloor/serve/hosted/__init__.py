"""The hosted layer of the trading session server.

What it takes to let strangers' bots connect: API keys mapped to owners,
per-owner plans with quotas and rate limits, idle-session expiry, an audit log
of every mutating call, an admin CLI, and a store fit for a server. It wraps
any `SessionService`, so the transports serve it unchanged.

    accounts   Accounts            owners, plans, API keys (salted hashes only)
    plans      Plan, DEFAULT_PLANS the limits, as data
    quotas     Quotas              token buckets, daily ledger, session activity
    audit      AuditLog            append-only JSONL
    service    HostedService       the wrapper; Principal (owner + key id)
    resolver   ApiKeyResolver      Bearer key -> owner, for the HTTP app
    app        create_hosted_app   the transport's app plus /healthz, /v1/usage
    s3store    S3Store             SessionStore on S3 (conditional writes)
    admin      python -m tradefloor.serve.hosted.admin
    __main__   python -m tradefloor.serve.hosted   (the hosted server)

Operator's guide: docs/serve/HOSTED.md. Nothing here imports FastAPI, boto3
or the engine at import time; the modules that need them import them lazily.
"""

from tradefloor.serve.hosted.accounts import Accounts, KeyRecord, OwnerRecord  # noqa: F401
from tradefloor.serve.hosted.audit import AuditLog  # noqa: F401
from tradefloor.serve.hosted.plans import DEFAULT_PLANS, Plan, load_plans  # noqa: F401
from tradefloor.serve.hosted.quotas import Quotas, TokenBucket  # noqa: F401
from tradefloor.serve.hosted.service import HostedService, Principal  # noqa: F401
