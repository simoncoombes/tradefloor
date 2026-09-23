# The trading session server: contract 0.1

What it is for: a long-running agent (a bot) rehearses in a simulated market
before it touches money, and is re-tested every time it changes. It opens a
session, looks at the market, places orders, moves time forward, and resumes
after a crash exactly where it was. Strategy note and the owner's rulings:
tradefloor-design `programme/strategy/persistent-agents.md`, sections 4 and 8.

The owner's rulings that shape this contract (2026-09-23):
users first; offer BOTH a self-run install and a hosted service; trading goes
through a SEPARATE write-capable server beside the read-only `tradefloor.mcp`;
bots do not meet in the book yet (server-side fills now, engine resting orders
in the version after next); text headlines with a leak guard; season results
published with caveats and transcripts.

The types are `python/tradefloor/serve/types.py`. Where this document and that
file disagree, this document governs and the file is the bug.

## 1. Layers and who owns which files

    python/tradefloor/serve/types.py      the contract (this document's types)   integration
    python/tradefloor/serve/core.py       LocalSessionService                   core agent
    python/tradefloor/serve/store.py      SessionStore, FileStore               core agent
    python/tradefloor/serve/mcp.py        write-capable MCP server (stdio)       transport agent
    python/tradefloor/serve/http.py       HTTP API, native + broker-shaped       transport agent
    python/tradefloor/serve/__main__.py   `python -m tradefloor.serve`           transport agent
    python/tradefloor/serve/hosted/       keys, quotas, limits, audit, admin     hosted agent
    deploy/                               container and AWS deployment           hosted agent
    python/tradefloor/headlines.py        news -> text, with the leak guard      headlines agent
    tests/serve/test_<layer>_*.py         each layer's tests, in its own files   each agent
    docs/serve/<LAYER>.md                 each layer's user-facing docs          each agent

No agent edits another's files, `types.py`, this document, `pyproject.toml`,
the Rust engine, or any preset. Need a contract change? Write it in your
layer's doc under "Contract change requests"; integration decides. Need a
dependency? Install it in your own venv and list it in your doc; integration
adds the `serve` extra. The core package still depends on NOTHING.

## 2. Sessions

- `open(owner, config)` builds an engine for `config.preset` (a NAMED preset,
  refused otherwise), `seed`, and `Universe.random(universe_size,
  seed=universe_seed)`, a `Portfolio(cash, max_leverage=...)`, and opens the
  market for day 0. Returns `SessionInfo` with a fresh `session_id`
  (uuid4 hex). `universe_size` 1..40 in self-run; the hosted layer may cap
  lower.
- Every call names an `owner`. A session belonging to another owner is
  `not_found`, indistinguishable from one that does not exist.
- Time moves ONLY when the agent calls `advance`. There is no wall clock in
  contract 0.1 (a paced, wall-clock mode is a later option for hosted "live"
  sessions).
- `advance(steps=n)` runs n steps of `ticks_per_step` ticks each (the
  harness's `session_clock` and `run_session`), crossing session boundaries
  as needed: `close_market` after tick 390, `open_market` before the next
  day's first step. `until="close"` runs to the end of the current session;
  `until="next_open"` to the first step of the next one. One call may not run
  more than 20 sessions (7,800 ticks); larger is `invalid_request`.
- `close` ends the session and returns a `SessionReport` with `caveats`
  COMPUTED at call time from the envelope and measured facts, never retyped
  prose (the rule in `tradefloor/mcp.py`). The caveats must include, while it
  is true, that the preset fails the long-run check: over multi-year sessions
  its volatility regimes and crash frequency drift from real markets
  (tradefloor-design `programme/results/crashcheck/`).

## 3. Orders and fills

- Market orders: queued, then filled at the START of the next step, before the
  market moves, through `Portfolio.execute` (the impact-aware price the book
  gives), and their flow enters that step's `run_session(order_flow=...)`
  exactly as `TradingEnv.step` does. So market orders move the market.
- Limit orders: rest SERVER-SIDE. After each step, a resting buy fills in full
  at its limit price if the step's traded low is at or below the limit; a sell
  if the step's high is at or above it (the way a broker's paper engine fills).
  No partial fills in 0.1. A limit that is marketable at submission is treated
  as a market order capped at the limit: it fills at the next step's execution
  price if that is at or better than the limit, and otherwise rests.
  Resting fills do NOT feed the engine's order flow (the step has already been
  simulated), so they do not move the market. This is stated in
  `docs/serve/CORE.md` and in every report's caveats; engine-level resting
  orders are the version after next (ruling 6).
- `time_in_force="day"` expires at the session close; `"gtc"` persists.
- `client_order_id` is an idempotency key per session: the same id with the
  same body returns the original order; with a different body, `conflict`.
- Refusals: unknown ticker, quantity <= 0 or non-finite, limit without a
  price, or a price <= 0 -> `invalid_order`. A fill that would breach
  `max_leverage` -> the order is `rejected` with the reason and
  `insufficient_buying_power` is raised at submission when it can already be
  known.
- Shorting is allowed within `max_leverage` (as `Portfolio` allows).

## 4. Observations

`observe` returns the clock, a `Quote` per ticker (last, day open/high/low,
previous close, volume so far; bid/ask optional), the VIX, a documented subset
of the economy state (federal funds rate, 10-year yield, inflation, GDP growth,
unemployment, cycle phase as a number), the account, positions, open orders,
headlines (empty until the headlines layer lands), and a `state_hash`.

## 5. Determinism, persistence, resume, fork

- Same config + the same ordered calls -> bit-identical responses and the same
  `state_hash` after every call. `state_hash` combines `Engine.state_hash()`
  with a hash of the portfolio, the order book of resting orders and the
  session clock. This is what regression testing stands on.
- The service persists after EVERY mutating call (open, place_order,
  cancel_order, advance, fork, close) through a `SessionStore`: the engine's
  `state_snapshot()` (JSON), the portfolio, orders, fills and clock, plus an
  append-only log of the calls. A process that dies and restarts serves the
  same session from the store: `observe` after resume equals `observe` before
  the crash, bit for bit, including `state_hash`. `FileStore(root)` is the
  default (`~/.tradefloor/sessions` for self-run); the hosted layer may supply
  another store (e.g. S3) through the same protocol.
- `fork` copies a session's full state into a new session (same owner,
  `parent_session_id` set). Forks evolve independently and deterministically.

## 6. Transports (transport agent)

- MCP, `python -m tradefloor.serve.mcp` (stdio): tools open_session,
  list_sessions, observe, place_order, cancel_order, list_orders, list_fills,
  advance, fork_session, close_session, describe (what the server is, its
  limits and caveats). Built on the same `mcp>=2.0` SDK as `tradefloor.mcp`,
  as a SEPARATE server; the read-only one is not modified.
- HTTP, `python -m tradefloor.serve` (default 127.0.0.1:8765, FileStore):
  native routes under `/v1/sessions...` mapping 1:1 to the service, with
  OpenAPI docs; and a broker-shaped facade under
  `/broker/{session_id}/v2/...` (account, positions, orders, clock) following
  the shape of a common paper-trading API closely enough that an existing bot's
  client can be pointed at it by changing its base URL. Document exactly which
  endpoints and fields are supported and which are not.
- Errors: HTTP status by code (400 invalid_request/invalid_order, 403
  insufficient_buying_power, 404 not_found, 409 conflict/session_closed, 401
  unauthorized, 429 rate_limited/quota_exceeded, 500 internal) with body
  `{"code","message"}`; MCP returns the same body as a tool error.

## 7. Hosted (hosted agent)

Wraps any `SessionService` and the HTTP app: API keys (hashed at rest) mapped
to owners, per-owner quotas (open sessions, steps per minute, simulated days
per day, compute seconds), rate limits, caps on universe size and advance
length, idle-session expiry, an audit log of every mutating call, an admin CLI
to create and revoke keys and read usage, and a pluggable store. Deployment:
a container image and an AWS deployment path with a cost estimate. NOTHING is
deployed publicly or launched on AWS without the owner's say-so; build and
test locally.

## 8. Headlines (headlines agent)

`tradefloor.headlines` turns the engine's endogenous news into `Headline`
text. The leak guard: a headline may say WHAT happened and its direction the
way a real headline would, but never carries magnitude information from which
the news event's `price_impact` (the answer key) can be recovered beyond what
a real headline reveals; show this with a test. Headlines appear no earlier
than the news reaches the market. Read-only on the engine: if the news is not
readable from Python today, say exactly which accessor is missing rather than
changing the Rust engine; integration decides.
