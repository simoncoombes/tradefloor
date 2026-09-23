"""`python -m tradefloor.serve.hosted`: the hosted server.

    TRADEFLOOR_HOSTED_PEPPER=... python -m tradefloor.serve.hosted --data /data

Serves the public API on --host/--port (default 0.0.0.0:8080) and the admin
listener on 127.0.0.1:--admin-port (default 8081), expires idle sessions
every --sweep seconds, and writes the audit log to <data>/audit and stdout.
ONE process per data directory: the rate-limit buckets and the session cache
are in memory, and FileStore allows one writer per session (HOSTED.md,
"Scaling"). Settings: tradefloor.serve.hosted.config.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading

from tradefloor.serve.hosted._files import atomic_write_json
from tradefloor.serve.hosted.config import Settings, build_hosted, eprint


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m tradefloor.serve.hosted",
                                 description="The hosted tradefloor trading session server.")
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--admin-port", type=int, default=8081)
    ap.add_argument("--data", help="data directory (env TRADEFLOOR_HOSTED_DATA)")
    ap.add_argument("--plans", help="plans file (env TRADEFLOOR_HOSTED_PLANS)")
    ap.add_argument("--store", choices=("file", "s3"), help="env TRADEFLOOR_HOSTED_STORE")
    ap.add_argument("--sweep", type=float, default=60.0, help="idle-expiry sweep interval, seconds")
    ap.add_argument("--trust-proxy", action="store_true", default=None,
                    help="client address from X-Forwarded-For (behind a load balancer)")
    ap.add_argument("--insecure-no-pepper", action="store_true",
                    help="run without TRADEFLOOR_HOSTED_PEPPER (local experiments only)")
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args(argv)

    import uvicorn

    from tradefloor.serve.hosted.app import create_admin_app, create_hosted_app

    s = Settings.from_env(data=args.data, plans_path=args.plans, store=args.store,
                          trust_proxy=args.trust_proxy)
    s.require_pepper(allow_empty=args.insecure_no_pepper)
    s.data.mkdir(parents=True, exist_ok=True)
    try:  # key hashes, usage and the audit log: the service account's eyes only
        os.chmod(s.data, 0o700)
    except OSError:
        pass
    hosted = build_hosted(s, audit_stream=sys.stdout)
    if not hosted.accounts.pepper_matches():
        eprint("refusing to start: accounts.json was written under a different "
               "TRADEFLOOR_HOSTED_PEPPER, so no key would verify")
        return 2
    eprint("reconciled the activity ledger with the store:", json.dumps(hosted.reconcile()))
    hosted.start_sweeper(args.sweep)

    token = secrets.token_urlsafe(32)
    atomic_write_json(s.data / "admin.json", {"port": args.admin_port, "token": token})
    admin = uvicorn.Server(uvicorn.Config(create_admin_app(hosted, token), host="127.0.0.1",
                                          port=args.admin_port, log_level="warning"))
    threading.Thread(target=admin.run, name="tradefloor-hosted-admin", daemon=True).start()

    app = create_hosted_app(hosted, trust_proxy=s.trust_proxy)
    try:
        # proxy_headers off: the resolver reads X-Forwarded-For itself, taking
        # the right-most entry (what the load balancer saw), never the left-most
        # (what the client wrote).
        uvicorn.run(app, host=args.host, port=args.port, workers=1, proxy_headers=False,
                    log_level=args.log_level, access_log=False)
    finally:
        admin.should_exit = True
        hosted.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
