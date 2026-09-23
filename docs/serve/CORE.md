MILESTONE 1 pushed aa6eabf

# The session core

`tradefloor.serve.core.LocalSessionService` is the one real implementation of
`SessionService` (docs/serve/CONTRACT.md, contract 0.4). `tradefloor.serve.store`
holds two implementations of the contract's `SessionStore` protocol,
`FileStore` and `MemoryStore`. Both modules import only the standard library
and `tradefloor`.

```python
from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import FileStore, MemoryStore
from tradefloor.serve.types import OrderRequest, SessionConfig

service = LocalSessionService(FileStore())        # ~/.tradefloor/sessions
info = service.open("local", SessionConfig(universe_size=20))
service.place_order("local", info.session_id,
                    OrderRequest(ticker=info.tickers[0], side="buy", quantity=100))
result = service.advance("local", info.session_id, until="close")
report = service.close("local", info.session_id)
```

`LocalSessionService(store=None, *, max_universe=40, max_advance_sessions=20,
headlines=None, cache_size=64)`:

- `store=None` means `FileStore()` at `~/.tradefloor/sessions`. Use
  `MemoryStore()` in tests; it serialises exactly as `FileStore` does.
- `max_universe` caps `universe_size`; `max_advance_sessions` caps one
  `advance` call at that many sessions of ticks. The hosted layer can pass
  lower values.
- `news` comes from `tradefloor.headlines.headlines_for` unless you pass
  `headlines(engine, clock) -> list[Headline]`, which replaces it. Either has
  to be a pure function of the engine state or replay stops being
  deterministic.
- `cache_size` is how many sessions stay live in memory. Others are rebuilt
  from the store on their next call.

Beyond the protocol there are two more read-only methods: `calls(owner,
session_id)` returns the call log and `caveats(owner, session_id)` the caveats
a report would carry now.

## The clock

A session's clock has a trading `day` (from 0), `tick` (ticks run in that
day's session, 0 to 390), `step` (steps run in that session) and
`market_open`. `open` leaves day 0 open at tick 0 with nothing run. Between
two days a session is in one of two states. After a close, `market_open` is
false and the clock shows tick 390 of the day that closed. After
`until="next_open"`, the market is open at tick 0 of the next day and no tick
of it has run.

A session runs 09:30 to 16:00, one tick a minute, on a fixed weekday
(`day_of_week=3`), the way `harness.session_clock` and `TradingEnv` run it.
There are no weekends or holidays, so `day` counts sessions.

`advance(steps=n, until=...)`:

| `until` | `n` counts | called after a close |
|---|---|---|
| `"steps"` | steps of `ticks_per_step` ticks | opens the next day, then steps |
| `"close"` | closes to reach | runs the whole next session |
| `"next_open"` | opens to reach | opens the next day and stops |

A step never crosses the close. The last step of a session is cut short so it
ends at tick 390: with `ticks_per_step=60` a session is six 60-tick steps and
one of 30. Before running anything, `advance` counts the ticks the call would
run, and more than `max_advance_sessions` x 390 (7,800 by default) is
`invalid_request` with nothing changed.

## What one step does

1. If the market is closed, `open_market()`.
2. Queued orders fill against the live book, in submission order.
3. `run_session(hour, minute, 3, ticks, order_flow=portfolio.pending_flow())`,
   then `clear_flow()`, as `TradingEnv.step` does.
4. The step's bar is recorded for every name.
5. Resting limit orders are checked against the prints of the step just run.
6. At tick 390, `close_market()`, the day bar is recorded, and day orders
   still resting expire.

A session nobody trades in prices exactly as a bare engine stepped with
`harness.session_clock`. `test_untraded_session_steps_as_trading_env` checks
every price bit for bit across a close and an open.

## Fill rules

### Market orders

A market order waits for the next step to start and then fills through
`Portfolio.execute`, at the average price of sweeping the live book. Its
shares go into that step's order flow, so the market moves against it. The
fill carries the clock at the start of the step, before any of its ticks.

Several market orders on the same side of the same name in one step are
priced cumulatively. `Portfolio.execute` prices against the book without
taking liquidity out of it, so without this every slice of a split order
would fill at the top of the book. The first order goes through
`Portfolio.execute`; each later one pays the marginal price of the levels the
earlier ones used (the sweep cost of all of them minus the sweep cost of the
ones before), with `Portfolio`'s leverage check and flow accounting. Orders
of 1,000 and 1,200 shares pay what one order of 2,200 pays and leave the same
market behind.

The engine's book is shallow: ten levels a side, 3,806 shares on the offer of
the first name in the 8-name test roster. A market order bigger than the book
fills partially. The order ends `filled` with `filled_quantity` below
`quantity` and a `reason` beginning "partial", and the rest is cancelled.

### Limit orders

When a limit is placed, it is marketable if a buy is priced at or above the
best ask, or a sell at or below the best bid (the last price if that side of
the book is empty). A marketable limit queues like a market order. At the
start of the next step it fills if the whole order sweeps at an average at or
better than its limit, and otherwise it rests.

A resting limit is checked after each step against that step's prints, read
from `Engine.session_prices()`. A buy fills if the lowest print was at or
below the limit, a sell if the highest print was at or above it. The fill is
for the whole order at the limit price, with no price improvement: a buy at
100 in a step that opened at 95 fills at 100. A marketable limit that failed
its sweep at the start of a step is checked against that same step's prints.

Resting fills do not enter the order flow, because the step has already been
simulated, so they do not move the market. The tests check that a session
whose only trades are resting fills keeps the untraded market bit for bit.

There is one print per name per tick, the tick's closing price. A price that
went through the limit within a tick and came back is not seen. There is no
queue position and no partial fill.

### Expiry, cancel and ids

A `day` order expires at the close of the session it was placed in. An order
placed while the market is closed belongs to the next session and expires at
that session's close. A `gtc` order stays until it fills, is cancelled or is
rejected. `cancel_order` works on any accepted order; on a finished one it is
`invalid_request`.

Order ids are `ord-000001`, `ord-000002` and so on, per session. A fork
continues its parent's count.

`Order.updated_at` is the clock of the order's last status change: the
submission for an accepted order, the start of the step for a taker fill or
a rejection there, the end of the step for a resting fill or a rejection
there, the clock after the close (tick 390, market closed) for an expiry, and
the clock of the call for a cancel. `close` cancels every open order with
reason "session closed" and `updated_at` the closing clock, so a closed
session has no live orders.

`client_order_id` is unique within a session. The same id with the same body
(ticker, side, quantity, type, limit price, time in force) returns the
original order as it stands now and creates nothing. A different body is
`conflict`.

### Leverage

With `max_leverage` set, a fill is refused only when its projected leverage
(gross exposure over net worth, by `Portfolio._projected_leverage`) is above
the cap and also above the account's leverage now (contract 0.2). A trade
that lowers leverage always passes, so an account the market has pushed over
its cap can trade back down. `Portfolio`'s own rule refuses any fill
projected over the cap, so the core applies its rule first and then runs
`Portfolio.execute` with the cap lifted for that one call. `Portfolio` is
unchanged.

An insolvent account (net worth at or below zero) may only make trades that
do not add gross exposure, whether or not the session has a cap (contract
0.4). It can cut or close a position, or flip it to a smaller one on the
other side, but not add to it or open another. At submission such an order
raises `insufficient_buying_power` with a message beginning "insolvent". An
order accepted while the account was solvent and reaching the book after it
went insolvent ends `rejected` with reason `"insolvent"`.

At submission the core projects the order on a copy of the account, after the
orders already queued ahead of it, at the book's current sweep price (market)
or its limit (limit), and compares with that copy's leverage. A refusal
raises `insufficient_buying_power` and no order is created. Resting limits do
not use up buying power at submission, because they may never fill. The rule
runs again at the fill. A market order refused there, or a resting limit
whose fill the rule refuses, ends `rejected` with the reason.

## Observations

- `quotes`, one per ticker in roster order. `last` is `Engine.prices()`.
  `day_open`, `day_high`, `day_low`, `prev_close` and `volume` are the
  engine's `open`, `high`, `low`, `previous_close` and `volume` columns; the
  day's high and low include its opening price. `bid` and `ask` are the
  book's best levels. After a close they describe the day that closed, and
  the next open resets them.
  `step_volume` is the volume of the last step in the current session, and
  None before the session's first step (at the open, and after
  `until="next_open"`). After a close it is the closing step's volume.
- `vix`, the economy's VIX.
- `macro`, in the engine's percent units from `state_snapshot()["economy"]`:
  `federal_funds_rate`, `treasury_yield_10y`, `inflation_rate`, `gdp_growth`,
  `unemployment_rate`, and `cycle_phase` as 0 expansion, 1 peak,
  2 contraction, 3 trough, 4 recovery (`core.MACRO_FIELDS`,
  `core.CYCLE_PHASES`).
- `account`, from `Portfolio`. When net worth is at or below zero,
  `leverage` is None and `insolvent` is true (contract 4a).
- `positions`, non-zero holdings in roster order.
- `open_orders`, accepted orders in submission order.
- `news`, `headlines_for(state_snapshot, day=clock.day, tick=clock.tick,
  tickers=roster)`: the current news day's headlines once the clock reaches
  tick 1 of that day. There are none at tick 0 of a new day, and the list
  stays through the close. It is a pure function of the engine state, so
  resume and fork give the same headlines.

## News log

Every headline the session releases is kept in a per-session log, persisted
as the `news` stream (contract 0.4). `advance` returns in
`AdvanceResult.news` every headline released during that call, however many
sessions it runs, so an agent that advances by 20 sessions misses nothing.
`news(owner, session_id, since_day=0, since_tick=0, limit=None)` returns the
log from the clock point `(since_day, since_tick)`, oldest first, and `limit`
keeps the most recent `limit`, as in `bars`.

With `headlines_for`, a day's headlines are all released at tick 1, so the
core asks for them once a day, after the day's first step. A `headlines`
callable can release at any tick, so it is asked after every step, and the
log drops a headline it already holds (same day, tick, tickers, text and
category). Resume and fork carry the log, and a session resumed mid-day
releases nothing twice.

## Bars

`bars(owner, session_id, ticker, resolution="day", since_day=0, limit=None)`
returns `Bar`s oldest first.

A step bar is the step's prints for that name: `open` the first tick's
print, `high` and `low` the extremes, `close` the last print, and `volume`
the day's volume column after the step minus before it. `step` is the index
of the step in its day. Step bars cover the last 20 sessions, the session in
progress included (`core.STEP_BAR_SESSIONS`); older ones are dropped. The
session in progress keeps its step bars in the record; each finished session's
go to the `step_bars` stream at its close, as one entry. When that stream
reaches 40 entries (`core.STEP_BAR_TRIM_AT`) the service trims it back to 20
with `trim_stream`, so the disk holds at most 40 sessions of step bars and a
trim rewrites 20 of them once every 20 sessions.

A day bar has `step=None` and the engine's `open`, `high`, `low` and
`volume` columns with the price after `close_market` as `close`, so it
matches the closing quote. Day bars cover every session. The session in
progress gets a day bar once it has run a step, built from the live quote.

`since_day` drops bars from earlier days, and `limit` keeps the most recent
`limit`. An unknown ticker, an unknown resolution, a negative or non-integer
`since_day`, or a `limit` below 1 is `invalid_request`.

### state_hash

A sha256 over canonical JSON of `Engine.state_hash()`; the preset,
`ticks_per_step` and `max_leverage`; cash and starting cash; each position's
quantity, average cost and realised P&L; each open order and whether it is
queued or resting; the clock (day, tick, step, market open, steps run in
total); the next order number; and the status. Floats go in as their IEEE
bit patterns. The session id, owner, label and wall-clock times stay out, so
two sessions given the same calls hash alike, and so do a fork and its
parent. Closing a session changes its hash, because status is state.

## Persistence and resume

Every mutating call (open, place_order, cancel_order, advance, fork, close)
commits before it returns. A commit is one record plus entries appended to
three streams:

- the record holds the engine's `state_snapshot()` and `state_hash()`, the
  portfolio, the clock, open orders, counters, the session info and the
  step bars of the session in progress, packed as f64. It does not grow with
  history: about 15 KB at 20 names.
- `fills` gets every fill.
- `day_bars` gets each finished session's day bars, one packed row.
- `step_bars` gets each finished session's step bars at its close, and is
  trimmed at session boundaries (see Bars).
- `news` gets each headline as it is released.
- `orders` gets each order once it is finished (filled, cancelled, expired or
  rejected).
- `calls` is the call log, one entry per mutating call: `seq`, `op`, `args`,
  the `state_hash` after it, the clock and the wall time.

`FileStore(root, fsync=False)` keeps one directory per session:
`head.json`, `record-<seq>.json` and `<stream>.jsonl` (`<stream>.<g>.jsonl`
after a stream's g-th trim). A commit truncates each stream to its committed
size, appends, writes the new record file, and then replaces `head.json` by
writing a temp file and renaming it. The rename is the commit point. A
process killed before it leaves the previous commit readable; lines appended
by the unfinished commit are ignored and overwritten by the next one.
`fsync=True` also flushes to the device, for power loss.

`trim_stream(session_id, name, keep_last)` writes the kept entries to the
stream's next generation file and then replaces `head.json` to point at it,
so the same rename is its commit point and a reader sees the stream whole,
before or after. The old generation is deleted afterwards; a reader in
another process that finds it gone reads `head.json` again. `MemoryStore`
trims under its lock.

The store keeps floats bit for bit. The engine's snapshot carries its
generator position as f64 values, and several of them are NaN bit patterns.
Plain `json` would write them all as the same NaN and resume a different
market. `store.encode` writes non-finite floats as `{"$f64": "<hex>"}` and
bytes as `{"$bytes": "<base64>"}`; finite floats use Python's shortest repr,
which round-trips exactly. A store for another backend (the hosted layer's S3)
should use `encode` and `decode` too.

A service that has not seen a session, or finds that the store's version has
moved on, rebuilds it: a fresh `Engine` for the config, `restore_state` with
the saved snapshot, and a check that the result hashes to the saved engine
hash. `restore_state` continues bit-identically mid-day. That was checked at
40 names over 25 days at 12 random cuts (9 of them on days with endogenous
news), and the tests check it after every call of a scripted session and
after a real SIGKILL.

`fork` builds the child through the same path, so a fork is what a restart
would produce. The child's streams start with the parent's whole history, and
both call logs record the fork.

Within a process the service is thread-safe (contract 4d). Each session has
its own lock, so calls on one session run one at a time and calls on
different sessions run concurrently. The tests run four sessions on four
threads against a small cache and check every result against a serial run.

One process writes a store root at a time; there is no cross-process lock. A
second service on the same root sees the first one's commits, because each
call compares the store's version with its cached copy. That is enough to
hand a root from one process to the next, and not enough for two writers.

## Refusals

| code | when |
|---|---|
| `invalid_request` | bad config (unnamed preset, `universe_size` outside 1 to `max_universe`, negative seed, cash not finite and positive, `max_leverage` not positive, `ticks_per_step` outside 1 to 390); owner not a non-empty string; bad `side`, `type`, `time_in_force` or `client_order_id`; unknown field in a dict request; `advance` with steps below 1 or an unknown `until`, or over the tick cap; unknown `status` filter; negative `since_day`; cancelling a finished order; `bars` with an unknown ticker, resolution or a bad `since_day` or `limit`; `news` with a negative `since_day` or `since_tick` or a bad `limit` |
| `not_found` | no such session, a malformed id, another owner's session (same message as a missing one), no such order |
| `invalid_order` | unknown ticker; quantity not finite and positive, or above 1e12; limit without a price; price not finite and positive, or above 1e9; market order with a price |
| `insufficient_buying_power` | the order's projected fill is above `max_leverage` and above the account's leverage, or the account is insolvent and the order adds gross exposure (message begins "insolvent") |
| `session_closed` | place, cancel, advance, fork or close on a closed session |
| `conflict` | `client_order_id` reused with a different body |
| `internal` | a bug, or the store refused a commit; the call is not applied |

Reads (`info`, `observe`, `orders`, `fills`, `bars`, `news`, `calls`) still work on a closed
session. `orders(status=...)` takes the five statuses plus `"open"`,
`"closed"` and `"all"`.

## Caveats

`close` and `caveats` compute the list when they are called:

- the price process is a known model, not a forecast;
- a preset other than `envelope.PRESET` carries none of its certification;
- the long-run check (below);
- `envelope.check(horizon_days=days)` reasons once a session passes the
  certified 252 days, and a SHORT WINDOW caveat under 63 days;
- resting fills do not move the market, with this session's count;
- news timing, while the preset has news on: computed from the preset's
  `endogenous_news_intensity` and `endogenous_news_sigma` and the session's
  `ticks_per_step`. The engine adds 1/390 of an event's impact every tick,
  a headline is released at tick 1, and an agent first sees it at the end of
  its first step. For pt-v19 at 30-tick steps the caveat says 92% of the move
  is still to come, about 129bp an event (HEADLINES.md measured +120bp);
- limit fills see one print per tick and have no queue position or partials;
- other agents are not in the book;
- unbounded leverage when `max_leverage` is None;
- rosters under 30 names;
- `Universe.random` rosters are sector-balanced;
- insolvency, when net worth is at or below zero, with leverage reported as
  null.

The long-run verdict is read from the preset's own record (block `long_run` in
`python/tradefloor/presets/<name>.json`, written by `record.py --long-run` from
the design repo's `programme/longrun/criteria.py`), so a recomposed preset never
inherits its predecessor's verdict. It was formerly `core.LONG_RUN_CHECK`, the free-running crash check from tradefloor-design
`programme/results/crashcheck/` (2026-09-23): 30 histories of 20 years per
preset against the S&P 500 and VIX from 1990 to 2025. A preset fails when any
measure is more than twice or less than half the real figure. pt-v19 fails 4
of 6, so every pt-v19 report says so:

| measure | real | pt-v19 |
|---|---|---|
| 20% bear markets per decade | 1.12 | 2.18 |
| sessions from trough back to peak | 670 | 186 (fails) |
| worst month's volatility in a bear market, % | 63.9 | 29.9 (fails) |
| sessions down more than 5% per decade | 6.2 | 1.8 (fails) |
| share of sessions with the VIX above 40 | 0.023 | 0.060 (fails) |
| index annual volatility, % | 18.1 | 17.7 |

A preset missing from the table gets a caveat saying it has not been checked.
The test sets pt-v19's row to the real figures and sees the caveat go.

## Performance

Measured on 2026-09-23 on the shared 10-core Apple silicon Mac at a load
average of 9 to 11 from other agents' jobs, so the absolute numbers move by a
quarter from run to run. 20 names, pt-v19, `ticks_per_step=30` (13 steps a
session), with 40 sessions already run so the step-bar window is full.

Bytes written per one-step `advance` commit with `FileStore`, averaged over
390 commits (30 sessions, trims included):

| core | record | bytes written per commit |
|---|---|---|
| contract 0.3 (window in the record) | 229 KB | about 230 KB |
| contract 0.4 (window in a trimmed stream) | 15 KB | 24 KB |

The 24 KB is the record, `head.json`, the call log line, the fills, and each
close's step-bar entry and every 20th session's trim, spread over the
session's commits.

One-step `advance` calls a second with `FileStore`, the two cores run
alternately three times each on the same machine (median, range):

| core | calls a second |
|---|---|
| contract 0.3 | 144 (135 to 177) |
| contract 0.4 | 183 (158 to 218) |

In the same session `MemoryStore` ran 314 one-step calls a second.
`advance(until="close")` ran 41 sessions a second against 63 for the bare
engine stepping the same 13 steps, so whole sessions through the service run
at about two thirds of the engine. A one-step call spends about a sixth of its
time in `run_session`. Most of the rest is the commit: engine snapshot and
hash, JSON of the record, two renames. Earlier in the day, on a quieter
machine and before bars existed, the one-step call ran at 358 a second.

The benchmark:

```python
import time
from tradefloor.serve.core import LocalSessionService
from tradefloor.serve.store import FileStore
from tradefloor.serve.types import SessionConfig

svc = LocalSessionService(FileStore("/tmp/bench"))
sid = svc.open("local", SessionConfig(universe_size=20)).session_id
svc.advance("local", sid, 20, until="close")
svc.advance("local", sid, 20, until="close")
t, n = time.perf_counter(), 0
while time.perf_counter() - t < 3:
    svc.advance("local", sid, 1)
    n += 1
print(n / (time.perf_counter() - t), "steps a second")
```

## Known limits

- Limit fills are coarse: one print per tick, no price improvement, no queue
  position, no partial fills, and no effect on the market.
- Market orders bigger than the ten-level book fill partially.
- For a limit placed while the market is closed, the marketable test uses the
  book at the close. The next open can differ. The fill test at the step's
  start still applies, so a limit never fills worse than its price.
- The `step_bars` stream holds up to 40 sessions on disk for a 20-session
  window. The `day_bars`, `fills`, `orders`, `news` and `calls` streams grow
  with the session, by design.
- A `headlines` callable is asked after every step, which costs an engine
  snapshot per step if it reads one; the default source is asked once a day.
- One weekday, no calendar, and `day` counts sessions.
- One writer per store root.
- The engine's own `order_log` lives only in memory, and a rebuilt engine
  starts with an empty one. `Checkpoint.of` on a served engine would not
  replay from day 0. The service does not use it; the call log is the
  session's history.
- Each cached session holds an engine in memory (64 by default).
- The session id and `created_at` are the only values that are not
  deterministic.

## Contract change requests

Everything the core asked for is decided. Contract 0.2 took the leverage
rule, `steps` with `until`, cumulative sweeps and partial fills, null leverage
with `insolvent`, nested `from_dict`, `SessionStore` in `types.py`, the edge
cases, and `headlines_for` in `observe`. Contract 0.4 took the insolvency
rule (trades that do not add gross exposure, reason "insolvent"),
`SessionStore.trim_stream`, and the news log. Moving the long-run table into
`tradefloor.envelope` is deferred, so it stays in `core.LONG_RUN_CHECK`.

One point the contract leaves open, decided here: `news(limit=n)` keeps the
most recent n entries at or after the clock point, the way `bars(limit=n)`
does. A client paging forward through the log should pass `since_day` and
`since_tick` and no limit.

## Tests

In `tests/serve/`: `test_core_determinism.py`, `test_core_orders.py`,
`test_core_sessions.py`, `test_core_crash.py`, `test_core_leverage.py`,
`test_core_bars.py`, `test_core_contract03.py`, `test_core_contract04.py` and
`test_store_files.py`, 184 tests in about 10 seconds with `pytest -n 2` on the
loaded machine. The 17
headlines tests pass beside them, and `python tests/known_answer.py` still
gives `sim f05e769f...3a2a`.
