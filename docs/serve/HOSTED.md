# The hosted service: operator's guide

The trading session server runs two ways. Self-run: a user installs it and
runs `python -m tradefloor.serve` on their own machine as owner `local`.
Hosted: we run it, and other people's bots connect over the internet with an
API key. This document covers the hosted way: what the code does, how to run
it, what it would cost on AWS, how it resists abuse, and what the owner has to
decide before anything goes public.

Nothing described here is deployed. The AWS files are written and linted, not
run. `deploy/aws/deploy.sh` refuses to start without an explicit confirmation
phrase, and it should only be given after the owner approves.

Code: `python/tradefloor/serve/hosted/`. Deployment: `deploy/`. Tests:
`tests/serve/test_hosted_*.py`. The contract: `docs/serve/CONTRACT.md` (0.2),
sections 1, 5, 6 and 7.

## 1. What the hosted layer is

`HostedService(inner, accounts, quotas, audit)` wraps any `SessionService`
and is a `SessionService` itself, so the HTTP transport serves it without
changes. For every call it:

1. checks that the owner is a live, unsuspended account (`unauthorized`);
2. closes any of that owner's sessions that have sat idle past the plan's
   limit;
3. takes one token from the owner's per-minute call bucket (`rate_limited`);
4. applies the plan's per-request caps: universe size, ticks per step, advance
   length, label and order-id length (`invalid_request`);
5. checks capacity: open sessions, stored sessions, storage bytes, open
   orders per session (`quota_exceeded`);
6. checks the daily quotas: simulated days and compute seconds per UTC day
   (`quota_exceeded`);
7. for `advance`, takes steps from the per-minute step bucket (`rate_limited`);
8. makes the inner call, times it, and charges the wall time as compute and
   the ticks the clock actually moved as simulated time;
9. writes one audit line if the call changes state, whether it succeeded or
   was refused.

The owner comes from the API key. The HTTP app's `owner_resolver` is
`ApiKeyResolver`, which reads the key and returns a `Principal`: a `str`
holding the owner id that also carries the key id for the audit log. The core
then does its own check, so one owner's sessions are `not_found` to another.

Overhead, measured on a 20-name session with FileStore on a local SSD: an
`advance` of one 30-tick step takes 1.7 ms on the bare core and 1.8 ms through
the hosted layer. Checking a key takes about 3 µs.

## 2. Quick start on one machine

```bash
export TRADEFLOOR_HOSTED_PEPPER=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')
export TRADEFLOOR_HOSTED_DATA=~/tradefloor-hosted
python -m tradefloor.serve.hosted --host 127.0.0.1 --port 8080 &
python -m tradefloor.serve.hosted.admin create-key me --plan trial
curl -H "Authorization: Bearer tfk_..." http://127.0.0.1:8080/v1/usage
```

With Docker: `deploy/docker-compose.yml`. It publishes the port on
127.0.0.1 only. Its header comment has the commands.

## 3. API keys

A key looks like `tfk_3f9c0a1b2d4e_<43 characters>`:

- `tfk_` is a fixed prefix. It lets secret scanners (GitHub's, gitleaks)
  recognise a leaked key, once we register the pattern with them.
- `3f9c0a1b2d4e` is the key id: 48 random bits. It is public. The admin CLI
  and the audit log show it.
- The rest is the secret: 32 bytes from `secrets.token_urlsafe`.

The server stores the key id, a 16-byte random salt, and
HMAC-SHA256(pepper, salt + secret). It never stores the secret. The pepper is
a server-side secret held outside the data directory (in Secrets Manager on
AWS), so a copy of `accounts.json` on its own cannot be used to test guesses.
There is no bcrypt or argon2 here. Those slow down guessing a password with
maybe 40 bits of entropy. This secret has 256 bits, so no guessing speed
matters, and a slow hash on every request would cost more CPU than the
simulation. `accounts.json` stores a short fingerprint of the pepper, so the
server refuses to start, and the CLI refuses to create keys, if it is run with
a different pepper from the one the keys were made under.

`create-key` prints the key once. After that it exists only as a hash. If it
is lost, revoke it and create another. An owner can hold several keys (one
per bot, say), but quotas count per owner, so a second key does not buy a
second allowance.

Clients can send the key in any of these headers:

| Header | For |
|---|---|
| `Authorization: Bearer tfk_...` | the native API, and hosted MCP |
| `X-API-Key: tfk_...` | clients that cannot set Authorization |
| `APCA-API-KEY-ID: tfk_<id>` + `APCA-API-SECRET-KEY: <secret>`, or the whole key in the secret header | the broker-shaped facade, so a bot written for that API needs only a new base URL and its key split in two |

The accounts file is shared by the server and the admin CLI. Writers take a
file lock and replace the file atomically. The server notices a change on the
next request, so a revoked key stops working at once, with no restart.

## 4. Plans and limits

A plan is data. `deploy/plans.example.json` holds the built-in three, and
`TRADEFLOOR_HOSTED_PLANS` points the server at your own file, which replaces
the built-in plans rather than merging with them. The numbers below are
placeholders until the owner sets the plans and prices (section 13).

| Limit | trial | standard | research | Refusal |
|---|---|---|---|---|
| open sessions | 2 | 5 | 20 | quota_exceeded |
| stored sessions (open + closed) | 20 | 200 | 2000 | quota_exceeded |
| storage | 100 MiB | 1 GiB | 10 GiB | quota_exceeded |
| universe size | 20 | 40 | 40 | invalid_request |
| min ticks per step | 10 | 5 | 1 | invalid_request |
| ticks per `advance` call | 390 (1 session) | 1,950 (5) | 7,800 (20) | invalid_request |
| open orders per session | 50 | 200 | 1000 | quota_exceeded |
| calls per minute | 60 | 300 | 1200 | rate_limited |
| steps per minute | 60 | 300 | 3000 | rate_limited |
| simulated days per UTC day | 100 | 1000 | 20000 | quota_exceeded |
| compute seconds per UTC day | 300 | 1800 | 21600 | quota_exceeded |
| idle expiry | 24 h | 7 days | 30 days | (session closed) |

The refusal codes follow what a bot should do next:

- `rate_limited` (429): a per-minute bucket is empty, and retrying later WILL
  work. The message says which limit and when to retry, and the error carries
  `retry_after`, which the HTTP transport sends as `Retry-After`.
- `quota_exceeded` (429): a daily or capacity allowance is used up. For daily
  quotas the message gives the reset time (UTC midnight) and `retry_after`
  counts down to it. For capacity, it says what to free.
- `invalid_request` (400): no retry on this plan could ever succeed (a
  universe bigger than the plan allows, an advance longer than one call may
  run), so the bot does not sit in a retry loop.

Example messages:

    rate_limited: plan 'trial' allows 60 calls per minute; retry in 0.9s
    quota_exceeded: plan 'trial' allows 100 simulated days per UTC day; 99.80 used and
      this call may run 1.00 more; resets at 2026-09-24T00:00:00Z (in 3h12m)
    invalid_request: advance of 14 steps x 30 ticks = 420 ticks is above the 390 ticks
      (1 session) one call may run on plan 'trial'; split it

How the meter works:

- Both buckets are token buckets that refill continuously. An `advance` asks
  the step bucket for an upper bound on its steps before it runs (from the
  clock and the core's rules for `until`), then refunds whatever it did not
  use. The advance-length cap applies to every mode: under contract 0.2,
  `steps=k` with `until="close"` or `"next_open"` counts k closes or opens,
  so it can run k sessions. A single close or open always fits, because
  every plan allows at least one session per call.
- Simulated time is charged from the clock itself: the ticks between the
  clock before and after the call, 390 ticks to a day. A test checks this
  against the real core for every `until` mode.
- Compute is the wall time spent inside the inner service. A call that
  starts under the quota is allowed to finish, so one call can overshoot. The
  advance-length cap limits how far.
- Buckets live in memory, so a restart refills them, which does no harm.
  Daily counters and per-session activity live in `usage.json`, which is
  written at most once a second. A crash loses at most a second of metering.
- `GET /v1/usage` shows an owner their plan, today's usage and what is left.

## 5. Idle sessions

An open session that has not been touched (any call naming it, reads
included) for longer than the plan's `idle_expiry_seconds` is closed through
the core's own `close`, so its report and fills stay readable. Expiry runs in
three places: a sweeper thread every 60 s, lazily at the start of each of the
owner's calls, and on demand from `admin expire-idle`. Each expiry writes an
audit line with `actor: "system"`. Once a session has expired, a mutating
call to it gets `session_closed`, and the message explains that the server
closed it because it sat idle and names the plan limit.

## 6. The audit log

Every mutating call (open, place_order, cancel_order, advance, fork, close),
succeeded or refused, and every expiry and failed authentication, writes one
JSON line to `<data>/audit/audit-YYYY-MM-DD.jsonl` (UTC day). The file is
opened with O_APPEND and is never rewritten. The server also prints each line
to stdout, which is how the lines reach CloudWatch.

```json
{"actor":"api","call":"advance","detail":{"compute_ms":1.7,"fills":0,"sim_ticks":390,
 "steps":13,"until":"steps"},"duration_ms":1.9,"key_id":"3f9c0a1b2d4e",
 "outcome":"ok","owner":"acme","session_id":"9b1e...","ts":"2026-09-23T14:02:11.482Z"}
```

`outcome` is `ok` or the error code, and `error` holds the message. Failed
authentications are folded: at most one line per key id (or source address)
every 10 s, with a count of the ones it stands for, so a flood of bad keys
cannot fill the disk through the log. Reads are metered but not logged. Set
`audit_reads=True` to log them too.

Retention is the owner's decision (section 13). The files on EFS are the
record, and CloudWatch keeps 90 days by default.

## 7. The admin CLI

`python -m tradefloor.serve.hosted.admin` (on AWS: `deploy/aws/admin.sh ...`,
which runs it inside the task over ECS Exec, so there is no SSH and no public
admin port).

| Command | Does |
|---|---|
| `create-key OWNER [--plan P] [--label L]` | new key, printed once; creates the owner if new |
| `revoke-key KEY_ID` / `revoke-all OWNER` | stops the key(s) on the next request |
| `list-keys [--owner O]` | ids, labels, created, revoked, last used; never secrets |
| `owners` / `set-plan OWNER PLAN` | plans take effect on the next call |
| `suspend OWNER` / `unsuspend OWNER` | refuse every call, keep the data |
| `plans` | the limits in force |
| `usage [OWNER]` | today's usage against the plan (from `usage.json`) |
| `sessions OWNER` | the owner's sessions and their activity |
| `expire-idle [--owner O]` | close idle sessions now |
| `audit [--owner] [--call] [--session] [--tail N]` | the last N audit lines |

`sessions` and `expire-idle` go through the running server, because only that
process may write sessions. The server serves them on a second listener bound
to 127.0.0.1:8081. Every request to it must carry a token, which the server
writes to `<data>/admin.json` (mode 0600) when it starts. `--offline` works
directly on the store, and is safe only while the server is stopped. Add
`--json` to any command for machine-readable output.

## 8. Storage

The data directory holds everything:

    <data>/accounts.json   owners, plans, key hashes (0600)
    <data>/usage.json      the metering ledger
    <data>/audit/          the audit log
    <data>/sessions/       FileStore (one directory per session)
    <data>/admin.json      the admin listener's port and token

**Launch default: FileStore on EFS.** FileStore commits by renaming a file
into place, and a rename on one NFS client is atomic, so the core's crash
guarantees carry over. The storage meter adds up each session directory. A
mutating call rewrites the session's record, which holds the engine snapshot:
about 15 KiB at 20 names and 24 KiB at 40 (measured). A session directory is
about 120 KiB after a few days of trading.

**S3Store** (`hosted/s3store.py`) implements the `SessionStore` protocol
(`types.py`, contract 0.2) on S3, with FileStore's atomicity. S3 has no append and no rename, so a commit
PUTs the stream chunks and the record under fresh names (sequence number plus
a random nonce), then PUTs `head.json` conditionally (`If-Match` on the ETag
it last saw, or `If-None-Match: *` for the first commit). `head.json` is the
commit point: a reader sees a whole commit or none of it, a commit cut off
halfway leaves only objects no head names, and a second writer gets a 412
and a `StoreConflict` instead of silently interleaving. Streams are compacted
every 32 chunks, so a long history stays a few GETs. `gc(session_id)` deletes
leftovers. It passes the same bit-exact float round trip as FileStore, and the
core runs on it and resumes bit for bit after a restart. Tests use an
in-memory fake of S3 and moto, never real S3.

S3 is the wrong place for per-call commits at launch, and the reason is cost.
Each commit is about three PUTs at $0.005 per thousand. At the launch
workload in section 11 (1.1 million mutating calls a day) that is about 3.2
million PUTs a day, roughly $490 a month, against about $49 of EFS writes for
the same traffic. Use S3Store for low-traffic deployments, or later as an
archive for closed sessions.

**Deleting sessions.** Nothing in contract 0.1 deletes a session, so a user
who reaches `max_stored_sessions` or the storage cap cannot free space
themselves. For now the operator deletes old closed sessions by hand while
the server is stopped (`rm -r <data>/sessions/<id>`). See the contract change
requests.

## 9. Scaling: one process per data directory

The service runs as one process with one uvicorn worker. The rate-limit
buckets and the core's session cache are in memory, and FileStore allows one
writer per session. The AWS template enforces this: `DesiredCount: 1`,
`MaximumPercent: 100`. A deploy stops the old task before the new one starts,
so clients see 10 to 30 seconds of 502s and should retry.

Capacity: with FileStore on a local disk, one process handles a few hundred
mutating calls a second. Commits on EFS take longer, because each one makes
several NFS round trips. **Measure this on EFS before launch** (section 13).
At the placeholder plans, 50 bots all running at trial limits make about 50
calls a second, which is well within capacity.

Past one process, split owners across several services, each with its own
data directory, and route by key id (ALB rules on a header, or a small
router). That is not built, and a launch of this size does not need it.

## 10. Hosted MCP

The contract's MCP transport speaks stdio, and stdio cannot be hosted: it
means the server runs on the user's machine as a child process of their
client. For a hosted service, MCP has to use the streamable-HTTP transport,
mounted in the same app at `/mcp`, with the same Bearer key. It is worth
doing, since LLM-driven bots are a main audience and clients such as Claude
Code and Cursor can add a remote MCP server with a custom header.

Two pieces are missing, and both belong to other layers:

1. `tradefloor.serve.mcp.create_server` fixes one owner when the server is
   built. Hosted use needs the owner per request. See contract change
   request 2. With that in place, the hosted side is one line per request:
   `owner_from_headers(hosted, request.headers)` (in `hosted/resolver.py`).
2. Clients that support only OAuth (the MCP authorization spec, which is
   what claude.ai's custom connectors use) would need an OAuth 2.1
   authorization server in front. That is a separate, larger piece of work,
   and not needed for the first launch.

Until then, hosted users drive the HTTP API directly, and self-run users use
stdio MCP.

## 11. Deployment

### The container (`deploy/Dockerfile`)

A two-stage build. The build stage is `python:3.11-slim` with a pinned Rust
toolchain (1.98.1), and it compiles the wheel with `maturin build --release
--locked`. The runtime stage is `python:3.11-slim` with the wheel and
`deploy/requirements-hosted.txt`: no compiler and no source tree. It runs as
uid 10001, on a read-only root filesystem, with a `/healthz` health check.
`deploy/Dockerfile.dockerignore` keeps the 135 MB parity corpus and any local
build output out of the build context. There are no RUSTFLAGS, because
`rust/Cargo.toml` forbids anything that could change float results between
CPUs. Build one architecture per deployment (linux/arm64 for Graviton), and
resume sessions on the architecture that wrote them.

Docker is not installed on the machine where this was written, so the image
has never been built. The build stage's steps were run by hand on a copy of
exactly the files the ignore file lets through: `cargo fetch --locked`, then
`maturin build --release --locked`. The resulting wheel, installed with the
runtime requirements into a clean virtualenv, imports and runs the admin CLI.
`tests/serve/test_hosted_deploy.py::test_docker_image_builds_and_starts`
builds the real image, and skips cleanly when Docker is absent.

### AWS (`deploy/aws/`, written, linted, NOT run)

`cloudformation.yml` passes `cfn-lint` with no findings, and a test parses it
and checks its security properties. It creates:

- a VPC with two public subnets. The ALB needs two availability zones. The
  task gets a public IP so it can pull from ECR without a NAT gateway (which
  would add about $33 a month); its security group admits only the ALB.
- an internet-facing ALB: HTTPS on 443 (TLS 1.3/1.2 policy, ACM certificate
  validated through Route 53), port 80 redirecting to 443, invalid headers
  dropped, and `/admin*` answered with 404 at the ALB.
- ECS Fargate, ARM64, 1 vCPU and 2 GB, exactly one task. The root filesystem
  is read-only, the data volume is EFS mounted through an access point with
  IAM authorisation and TLS in transit, and ECS Exec is on for the admin CLI.
- EFS: encrypted at rest, daily AWS Backup with 35-day retention, and
  infrequent-access storage after 30 days.
- the pepper, generated by Secrets Manager and injected as a secret, so no
  person ever sees it.
- CloudWatch Logs (90 days), a metric filter counting unauthorised calls,
  and alarms for an unhealthy task, 5xx errors and failed-auth spikes, sent
  to an SNS email.
- optionally (`EnableWaf`), a WAF web ACL with a per-IP rate rule and the
  AWS common rule set.

The rollout order: `deploy.sh` creates the ECR repository, builds and pushes a
linux/arm64 image with an immutable tag, deploys the stack, and prints the
next steps. `admin.sh create-key ...` then makes the first key. `deploy.sh`
exits unless `TRADEFLOOR_DEPLOY_CONFIRM=owner-approved-public-launch` is set.

### Cost for a small launch

Prices are us-east-1 on-demand list prices as I know them; London
(eu-west-2) is roughly 10 to 15% higher. Check them against the AWS pricing
pages before deciding. The workload assumed: **50 bots active around the
clock, each making a call every 2 s on average, half of them mutating.** That
is 2.2 million calls a day, 1.1 million of them writing about 25 KB each, with
responses of about 5 KB.

| Item | Basis | $/month |
|---|---|---|
| Fargate task | 1 vCPU + 2 GB ARM, 730 h | 29 |
| ALB | hourly charge + about 1 LCU | 22 |
| Public IPv4 | 2 for the ALB + 1 for the task | 11 |
| EFS writes (Elastic) | 810 GB at $0.06 | 49 |
| EFS reads (Elastic) | about 260 GB at $0.03 | 8 |
| EFS storage + backup | about 5 GB | 2 |
| CloudWatch Logs | about 11 GB of audit lines + app logs | 7 |
| Data out | 324 GB, less the 100 GB free tier | 20 |
| Secrets Manager, Route 53 zone, ECR, alarms | | 2 |
| **Total** | | **about $150** |

- With EFS in Bursting mode instead of Elastic, the throughput charges go
  away: **about $95**. Whether the baseline is enough depends on how much is
  stored, so it has to be checked at launch. New file systems start with a
  large burst credit.
- With lighter use, one call every 10 s per bot (typical of LLM-driven
  bots), the total is **about $75**. The fixed part (task, ALB, IPs) is about
  $60 a month with no traffic at all.
- WAF, if turned on, adds about $7 a month plus $0.60 per million requests,
  **about $46** at this traffic. The template defaults it on. Turning it off
  saves this, and leaves the app-level limits and the failed-auth throttle to
  handle floods.
- The marginal cost is about **$3 per million mutating calls** (EFS writes,
  logs, data out). A bot running flat out at the trial limit costs about
  $4 a month on top of the fixed part. A bot at the standard limit costs
  about $20 a month and uses about 4% of the task's CPU. That is the number
  pricing has to cover.
- A cheaper route, not templated: one t4g.small EC2 instance with a gp3 EBS
  volume and Caddy terminating TLS comes to about $40 to $45 a month at the
  same load. It gives up managed TLS, automatic task replacement, multi-AZ
  storage and a host that needs no patching.

## 12. Abuse and safety

- **No arbitrary parameters.** Sessions take only named presets. A request
  cannot carry model coefficients or code (contract section 2), and every
  number a request can carry is capped by the plan.
- **Compute caps.** Universe size, ticks per step, advance length, steps per
  minute, simulated days per day and compute seconds per day each bound what
  one owner can make the server do. The per-call caps bound how far a single
  call can overshoot the daily ones.
- **Per-key isolation.** Every call runs as the owner of its key. The core
  answers `not_found` for another owner's session, exactly as for one that
  does not exist, and a test checks every call through both the fake and the
  real core. Owner names are limited to `[a-z0-9._-]`.
- **Guessing keys.** It cannot succeed against 256-bit secrets. A source
  address that keeps failing (more than 30 failures a minute) gets
  `rate_limited` for further failures, which are not audited again, while a
  valid key from the same address still works, so one broken bot behind a
  shared NAT address does not lock out its neighbours. The audit log also
  folds repeated failures per key id. On AWS the optional WAF adds a per-IP
  rate limit, and an alarm fires on spikes of unauthorised calls.
- **A leaked key.** (1) `admin revoke-key <id>`, or `revoke-all <owner>` if
  it is unclear which key leaked. It takes effect on the next request. (2)
  Read `admin audit --owner <owner>` and CloudWatch for calls from that key
  id since the leak. (3) Give the owner a new key through a channel that
  keeps no copy. (4) If misuse cost money, `suspend` the owner while you look
  into it. The worst a leaked key can do is spend that owner's quotas and
  read or close that owner's simulated sessions: there is no real money and
  no personal data behind it. Registering the `tfk_` prefix with GitHub
  secret scanning, once there is a domain and a contact, gets leaks in public
  repositories reported to us.
- **Operator access.** There is no SSH and no public admin port. The admin
  CLI runs inside the task over ECS Exec, which CloudTrail records. The
  pepper is never shown to anyone.
- **Denial of service by storage.** Stored-session and byte caps, an
  open-orders cap, and label and order-id length caps.
- **What the terms of use should say** (the owner and a lawyer write the
  real text): the market is simulated, and no real securities, money or
  orders are involved; nothing the service returns is financial advice or a
  forecast; results carry the caveats the service computes (the preset fails
  the long-run check), and anyone republishing results must keep them; no
  guarantee of availability or of retaining data, and sessions may be
  expired or deleted under the published plan limits; one account per
  person or organisation, keys are not to be shared, and the holder answers
  for use of their key; no attempts to break the limits, probe other
  accounts or load-test without permission; usage and audit data are kept for
  N days (the owner picks N); which jurisdiction's law applies.

## 13. Before launch: the owner's decisions

1. **Plans and prices.** Which plans exist, their limits (the table in
   section 4 is a placeholder), and what each costs, set against the marginal
   cost above. Is there a free tier, and how do people sign up for one (keys
   are made by hand for now)?
2. **Region.** us-east-1 is cheapest; eu-west-2 is closest to a UK owner and
   UK users.
3. **Domain.** For example `api.tradefloor.dev`, and whether its DNS zone is
   in Route 53 (the template then handles the certificate and the DNS record)
   or elsewhere (then create the certificate by hand).
4. **Terms of use and privacy notice**, from the points in section 12, and
   who answers abuse reports and where to write to them.
5. **EFS throughput mode** (Elastic, about $150 a month, or Bursting, about
   $95, at the assumed load) and **WAF** on or off (about $46 a month at that
   load).
6. **Retention.** How long closed sessions, audit files and CloudWatch logs
   are kept.
7. **Who holds operator access**, meaning the AWS account and who may run
   ECS Exec, and who receives the alarm emails.
8. **One pre-launch measurement**, not a decision but a gate: run a
   load test against the deployed stack before any real key goes out, to
   check commit latency on EFS and the capacity estimate in section 9.

## 14. Dependencies (for the `serve` extra)

`pyproject.toml` is not the hosted layer's file, so integration adds these:

- runtime: `fastapi>=0.141`, `uvicorn>=0.53` (with the transport), and
  `boto3>=1.43` only for `TRADEFLOOR_HOSTED_STORE=s3`. Listed in
  `deploy/requirements-hosted.txt`, which the image installs.
- tests (optional, each skipped if missing): `moto>=5.2` (S3Store against a
  botocore client), `cfn-lint>=1.57` (the template), `pyyaml` (the template
  parse), Docker (the image build).

## 15. Contract change requests

1. **Delete a session.** Add `delete(owner, session_id)` to `SessionService`
   and `delete(session_id)` to `SessionStore`. Without them, a user who
   reaches the stored-session or storage cap cannot free space, and the
   operator can only remove session directories by hand with the server
   stopped. Retention cannot be automated either.
2. **Per-request owner in MCP.** `create_server(service, owner=..., *,
   owner_resolver=None)`: when a resolver is given, each tool call resolves
   the owner from the request context, so the streamable-HTTP transport can
   be hosted at `/mcp` (section 10).
3. **`ServeError.retry_after`.** The transport already turns a `retry_after`
   attribute into `Retry-After`, and the hosted layer sets it. Make it an
   optional field of `ServeError` in `types.py`, so the arrangement is part
   of the contract and not a convention.
4. **Limits in `describe`.** `describe` (MCP) and `/v1/describe` should
   report the caller's plan limits when the service is hosted. A hook such as
   `describe_extra(owner) -> dict` on the service would do it. Until then,
   `GET /v1/usage` carries them.
5. **`SessionStore.size(session_id)`**, so the hosted storage meter does not
   need to know FileStore's directory layout (today it adds up
   `<root>/<session_id>/`, and S3Store has `size_of`).
6. **Say what `Clock.step` means.** The core counts steps since the current
   trading day opened, and `types.py` says "since the session opened", which
   reads as the whole server session. The hosted layer meters from `day` and
   `tick` for this reason.

## 16. Tests

`pytest tests/serve -k hosted -n 2`:

- `test_hosted_keys.py`: key format, randomness, salt and pepper, that the
  plaintext is never stored, file mode 0600, authentication, revocation
  reaching a running server without a restart, revoke-all and suspension,
  validation of owner names and plans.
- `test_hosted_service.py`, over the in-memory fake: authorisation, owner
  isolation for every call, and every limit in section 4 (open and stored
  sessions, storage, universe, ticks per step, advance length, calls and
  steps per minute with refunds, simulated days with the UTC reset, compute
  seconds, open orders with idempotent replay), idle expiry both swept and
  lazy, the audit log's completeness and order, failed-auth folding, the
  ledger surviving a restart, reconcile, and plan changes.
- `test_hosted_with_core.py`, over LocalSessionService on FileStore:
  metering against the core's clock in every `until` mode, the FileStore
  storage meter, expiry of a real session, resume after a restart through
  the hosted layer, and owner isolation.
- `test_hosted_s3store.py`: the round trip with bit-exact floats, a fresh
  process reading committed state, a crash at each PUT, a second writer
  refused, compaction and gc, heads by owner with a half-created session,
  the core running and resuming on S3Store, and the same checks through
  boto3 against moto.
- `test_hosted_admin.py`: every CLI command, the pepper guard, and
  `sessions`/`expire-idle` through a running admin listener with its token.
- `test_hosted_http.py`: owner isolation, limits and headers end to end
  through the transport's HTTP app (runs once the transport is merged).
- `test_hosted_deploy.py`: the template's security properties and a clean
  cfn-lint, the deploy guard, the loopback-only compose file, the ignore file
  and Dockerfile, and the image build when Docker is present.
