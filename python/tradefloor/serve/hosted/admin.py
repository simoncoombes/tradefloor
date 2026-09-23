"""`python -m tradefloor.serve.hosted.admin`: the operator's CLI.

    create-key OWNER [--plan P] [--label L]   a new key; printed ONCE, never stored
    revoke-key KEY_ID                         stops working on the next request
    revoke-all OWNER                          every key of an owner (a leak)
    list-keys [--owner O]                     ids, labels, dates, last use; never secrets
    owners                                    owners, plans, suspension
    set-plan OWNER PLAN
    suspend OWNER | unsuspend OWNER           refuse every call, keep the data
    plans                                     the limits of every configured plan
    usage [OWNER]                             today's usage against the plan
    sessions OWNER                            the owner's sessions, from the running server
    expire-idle [--owner O]                   close idle sessions, on the running server
    audit [--owner O] [--call C] [--session S] [--tail N]

Keys, plans and suspension edit `<data>/accounts.json` under a lock; the
running server re-reads it on the next request. `sessions` and `expire-idle`
ask the running server over its loopback admin listener, because only the
server may write sessions; `--offline` does it directly against the store,
and is only safe while the server is stopped. Settings as for the server
(tradefloor.serve.hosted.config); `create-key` needs the same
TRADEFLOOR_HOSTED_PEPPER as the server.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable

from tradefloor.serve.hosted._files import read_json
from tradefloor.serve.hosted.audit import AuditLog
from tradefloor.serve.hosted.config import Settings, build_accounts, build_hosted, plans_of
from tradefloor.serve.hosted.quotas import Quotas
from tradefloor.serve.hosted.service import usage_report


def _iso(t: float | None) -> str:
    if t is None:
        return "-"
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AdminError(Exception):
    pass


def _server_call(s: Settings, method: str, path: str) -> Any:
    info = read_json(s.data / "admin.json", None)
    if not info:
        raise AdminError(f"no running server found ({s.data / 'admin.json'} is missing); "
                         "start it, or use --offline while it is stopped")
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", method=method,
                                 headers={"X-Admin-Token": info["token"]})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise AdminError(f"server refused: {e.code} {e.read().decode(errors='replace')}") from e
    except OSError as e:
        raise AdminError(f"cannot reach the server's admin listener on 127.0.0.1:{info['port']} "
                         f"({e}); is it running? (--offline if it is stopped)") from e


def main(argv: list[str] | None = None, *, out=None,
         hosted_factory: Callable[[Settings], Any] | None = None) -> int:
    out = out or sys.stdout
    ap = argparse.ArgumentParser(prog="python -m tradefloor.serve.hosted.admin",
                                 description="Operate the hosted tradefloor server.")
    ap.add_argument("--data", help="data directory (env TRADEFLOOR_HOSTED_DATA)")
    ap.add_argument("--plans", help="plans file (env TRADEFLOOR_HOSTED_PLANS)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--insecure-no-pepper", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-key")
    p.add_argument("owner")
    p.add_argument("--plan")
    p.add_argument("--label", default="")
    sub.add_parser("revoke-key").add_argument("key_id")
    sub.add_parser("revoke-all").add_argument("owner")
    sub.add_parser("list-keys").add_argument("--owner")
    sub.add_parser("owners")
    p = sub.add_parser("set-plan")
    p.add_argument("owner")
    p.add_argument("plan")
    sub.add_parser("suspend").add_argument("owner")
    sub.add_parser("unsuspend").add_argument("owner")
    sub.add_parser("plans")
    sub.add_parser("usage").add_argument("owner", nargs="?")
    p = sub.add_parser("sessions")
    p.add_argument("owner")
    p.add_argument("--offline", action="store_true")
    p = sub.add_parser("expire-idle")
    p.add_argument("--owner")
    p.add_argument("--offline", action="store_true")
    p = sub.add_parser("audit")
    p.add_argument("--owner")
    p.add_argument("--call")
    p.add_argument("--session")
    p.add_argument("--tail", type=int, default=50)
    args = ap.parse_args(argv)

    s = Settings.from_env(data=args.data, plans_path=args.plans)

    def emit(obj: Any, text: str | None = None) -> None:
        if args.json or text is None:
            print(json.dumps(obj, indent=1, sort_keys=True, default=str), file=out)
        else:
            print(text, file=out)

    try:
        acc = build_accounts(s)
        if args.cmd == "create-key":
            s.require_pepper(allow_empty=args.insecure_no_pepper)
            rec, key = acc.create_key(args.owner, plan=args.plan, label=args.label)
            plan = acc.owner(args.owner).plan
            emit({**rec.public(), "plan": plan, "api_key": key},
                 f"owner   {rec.owner} (plan {plan})\nkey id  {rec.key_id}\n"
                 f"API key {key}\n\nThis is the only time the key is shown. It is stored as a "
                 f"salted hash; if it is lost, revoke {rec.key_id} and create another.")
        elif args.cmd == "revoke-key":
            rec = acc.revoke_key(args.key_id)
            emit(rec.public(), f"revoked {rec.key_id} (owner {rec.owner}) at {rec.revoked_at}")
        elif args.cmd == "revoke-all":
            recs = acc.revoke_all(args.owner)
            emit([r.public() for r in recs], f"revoked {len(recs)} key(s) of {args.owner}: "
                 + ", ".join(r.key_id for r in recs))
        elif args.cmd == "list-keys":
            quotas = Quotas(s.data / "usage.json")
            rows = [{**k.public(), "last_used": _iso(quotas.key_last_used(k.key_id))}
                    for k in acc.keys(args.owner)]
            emit(rows, "\n".join(
                f"{r['key_id']}  {r['owner']:<20} {'REVOKED ' + r['revoked_at'] if r['revoked_at'] else 'active':<30} "
                f"created {r['created_at']}  last used {r['last_used']}  {r['label']}" for r in rows)
                or "no keys")
        elif args.cmd == "owners":
            rows = [vars(o) for o in acc.owners()]
            emit(rows, "\n".join(f"{r['owner']:<24} plan {r['plan']:<10}"
                                 f"{' SUSPENDED' if r['suspended'] else ''}" for r in rows) or "no owners")
        elif args.cmd == "set-plan":
            rec = acc.set_plan(args.owner, args.plan)
            emit(vars(rec), f"{rec.owner} is now on plan {rec.plan}")
        elif args.cmd in ("suspend", "unsuspend"):
            rec = acc.set_suspended(args.owner, args.cmd == "suspend")
            emit(vars(rec), f"{rec.owner} {'suspended' if rec.suspended else 'active'}")
        elif args.cmd == "plans":
            plans = {n: p.to_dict() for n, p in plans_of(s).items()}
            emit(plans)
        elif args.cmd == "usage":
            quotas = Quotas(s.data / "usage.json")
            owners = [args.owner] if args.owner else [o.owner for o in acc.owners()]
            reports = [usage_report(acc, quotas, o) for o in owners]
            emit(reports, "\n".join(
                f"{r['owner']:<24} plan {r['plan']['name']:<10} today {r['today']['calls']} calls, "
                f"{r['today']['sim_days']} sim days, {r['today']['compute_seconds']} compute s, "
                f"{r['today']['refused']} refused; {r['sessions']['open']} open sessions"
                for r in reports) or "no owners")
        elif args.cmd == "sessions":
            if args.offline:
                hosted = (hosted_factory or build_hosted)(s)
                rows = [i.to_dict() for i in hosted.inner.list(args.owner)]
            else:
                rows = _server_call(s, "GET", f"/admin/sessions?owner={urllib.request.quote(args.owner)}")
            emit(rows, "\n".join(
                f"{r['session_id']}  {r['status']:<6} day {r['clock']['day']:>4} tick {r['clock']['tick']:>3}  "
                f"{r['config']['preset']} x{r['config']['universe_size']}  created {r['created_at']}"
                f"{'  fork of ' + r['parent_session_id'] if r.get('parent_session_id') else ''}"
                for r in rows) or "no sessions")
        elif args.cmd == "expire-idle":
            if args.offline:
                hosted = (hosted_factory or build_hosted)(s)
                hosted.reconcile()
                got = {"expired": hosted.expire_idle(args.owner)}
            else:
                q = f"?owner={urllib.request.quote(args.owner)}" if args.owner else ""
                got = _server_call(s, "POST", f"/admin/expire-idle{q}")
            emit(got, f"expired {len(got['expired'])} session(s)"
                 + "".join(f"\n  {sid}" for sid in got["expired"]))
        elif args.cmd == "audit":
            lines = deque(AuditLog(s.data / "audit").read(owner=args.owner, call=args.call,
                                                           session_id=args.session), maxlen=args.tail)
            for e in lines:
                print(json.dumps(e, sort_keys=True), file=out)
        return 0
    except (AdminError, KeyError, ValueError) as e:
        print(f"error: {e.args[0] if e.args else e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
