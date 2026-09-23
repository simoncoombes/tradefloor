# Transports: MCP and HTTP

The trading session server has two front doors over the same `SessionService`
(docs/serve/CONTRACT.md, section 6, contract 0.4). An LLM agent talks to the
MCP server, over stdio on your machine or over streamable HTTP when the server
is somewhere else. A bot talks to the HTTP server, either through the native
`/v1` routes or through a facade shaped like Alpaca's v2 trading API, so a bot
already written for Alpaca can be pointed at a simulated session by changing
its base URL.

Files: `python/tradefloor/serve/mcp.py`, `python/tradefloor/serve/http.py`,
`python/tradefloor/serve/__main__.py`. Tests: `tests/serve/test_mcp_*.py`,
`tests/serve/test_http_*.py`, and the contract fake in `tests/serve/fakes.py`.

## Quickstart

### Run it on your machine

```
pip install "tradefloor[serve]"
python -m tradefloor.serve
```

Until the `serve` extra exists, install the pieces by hand:
`pip install tradefloor fastapi uvicorn mcp`.

The server listens on `127.0.0.1:8765` and keeps sessions in
`~/.tradefloor/sessions`. It serves the HTTP API, the broker facade, and MCP
at `/mcp`. OpenAPI docs are at http://127.0.0.1:8765/docs. Options: `--host`,
`--port`, `--store DIR`, `--owner NAME` (default `local`), `--no-mcp`, and
`--service module:factory` to serve some other `SessionService`.

There is no authentication. Every request acts as one owner, which is right
on your own machine and wrong anywhere else, so binding to anything other than
loopback prints a warning. Bound to 127.0.0.1 or localhost, the server refuses
any request whose Host header is not one of those two, so a web page cannot
reach it by rebinding a hostname to 127.0.0.1. The hosted layer adds keys
through `create_app` (see "Embedding").

### Connect an agent over MCP

Over stdio, the MCP server runs as a subprocess of the client. For Claude
Desktop, add this to `claude_desktop_config.json`, using the Python that has
tradefloor installed:

```json
{
  "mcpServers": {
    "tradefloor-trading": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "tradefloor.serve.mcp"]
    }
  }
}
```

For Claude Code:

```
claude mcp add tradefloor-trading -- /path/to/venv/bin/python -m tradefloor.serve.mcp
```

Over HTTP, point the client at a running server instead:

```
claude mcp add --transport http tradefloor-trading http://127.0.0.1:8765/mcp
```

`python -m tradefloor.serve.mcp --http` serves MCP on its own (port 8766 by
default, path `/mcp`). Both MCP entry points take `--store`, `--owner` and
`--service` like the HTTP server and use the same default store, so a session
opened over MCP can be read over HTTP by the same owner. Keep one process per
store, though: `FileStore` has no cross-process lock, and two processes
writing to one session at once is unsupported in 0.4.

| Tool | Service call | What it does |
|---|---|---|
| `describe` | none | How time, orders and fills work, limits, units, error codes, and caveats read from the realism envelope. The server's instructions tell the agent to call it first. |
| `open_session` | `open` | Opens a session. Every argument is optional and defaults to `SessionConfig()`. |
| `list_sessions` | `list` | Your sessions, open and closed. |
| `observe` | `observe` | Clock, quotes, VIX, macro, account, positions, open orders, the current session's headlines, `state_hash`. |
| `place_order` | `place_order` | Market or limit, `day` or `gtc`, optional `client_order_id`. |
| `cancel_order` | `cancel_order` | Cancels an order that is still `accepted`. |
| `list_orders` | `orders` | `all`, `open`, `closed`, or one status. |
| `list_fills` | `fills` | Optionally from a trading day on. |
| `get_bars` | `bars` | OHLCV by day (all history) or by step (last 20 sessions). |
| `get_news` | `news` | The session's headline log from a clock point. |
| `advance` | `advance` | `steps`, or `until="close"` / `"next_open"`, where `steps` then counts closes or opens. The result carries every headline released during the advance. |
| `fork_session` | `fork` | Copies a session into a new one. |
| `close_session` | `close` | Ends the session, cancels its open orders, and returns its report with computed caveats. |

Each tool description says what the call does, its units and limits, and the
traps a model falls into: a market order is not filled when placed, time does
not move until `advance`, a big market order can part fill, and an insolvent
account can only reduce positions. Read-only tools carry `readOnlyHint`;
`close_session` carries `destructiveHint`.

A refusal comes back as a tool error (`isError: true`) whose structured
content and text are both the contract's error body, `{"code", "message"}`
(plus `retry_after` when the error has one). Arguments that fail the schema
come back the same way with code `invalid_request` and the argument named, and
an unknown tool is `not_found`.

### Connect over HTTP

```
curl -s -X POST localhost:8765/v1/sessions -H 'content-type: application/json' \
     -d '{"universe_size": 5, "seed": 7}'
# -> {"session_id": "3f0c...", "tickers": ["AAA", ...], "clock": {...}, ...}

S=3f0c...
curl -s localhost:8765/v1/sessions/$S/observation
curl -s -X POST localhost:8765/v1/sessions/$S/orders -H 'content-type: application/json' \
     -d '{"ticker": "AAA", "side": "buy", "quantity": 10}'
curl -s -X POST localhost:8765/v1/sessions/$S/advance -H 'content-type: application/json' \
     -d '{"steps": 1}'
curl -s "localhost:8765/v1/sessions/$S/bars/AAA?resolution=step"
curl -s -X POST localhost:8765/v1/sessions/$S/close
```

### Point a broker client at the facade

The facade for session `S` lives at `http://127.0.0.1:8765/broker/S`, and the
client adds `/v2/...` (or `/v1beta1/news`) itself. With alpaca-py:

```python
from datetime import datetime

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

url = f"http://127.0.0.1:8765/broker/{session_id}"
trading = TradingClient("unused", "unused", url_override=url)   # keys are required by the SDK, ignored here
data = StockHistoricalDataClient("unused", "unused", url_override=url)

account = trading.get_account()
bars = data.get_stock_bars(StockBarsRequest(symbol_or_symbols="AAA", timeframe=TimeFrame.Day,
                                            start=datetime(2000, 1, 1)))
trading.submit_order(MarketOrderRequest(symbol="AAA", qty=10, side=OrderSide.BUY,
                                        time_in_force=TimeInForce.DAY))
trading.post("/tradefloor/advance", {"steps": 1})   # where the live bot would sleep
```

The base URL is the only change, with one exception. Simulated time moves
only when you ask, so the place where a live bot sleeps becomes a call to
`POST /broker/S/v2/tradefloor/advance` (or the native `/v1/sessions/S/advance`).
`tests/serve/test_http_broker.py` runs this with the real alpaca-py 0.44.0
(`TradingClient`, `StockHistoricalDataClient`, `NewsClient`) against a live
server, on the fake service and on `LocalSessionService`.

## Native HTTP routes

One route per service call. Request bodies refuse unknown fields, so a
misspelt `univers_size` is a 400 and never a session with the default size.

| Method and path | Service call | Success |
|---|---|---|
| `POST /v1/sessions` (body: `SessionConfig`, all optional; empty body allowed) | `open` | 201 `SessionInfo` |
| `GET /v1/sessions` | `list` | 200 `[SessionInfo]` |
| `GET /v1/sessions/{id}` | `info` | 200 `SessionInfo` |
| `GET /v1/sessions/{id}/observation` | `observe` | 200 `Observation` |
| `POST /v1/sessions/{id}/orders` (body: `OrderRequest`) | `place_order` | 201 `Order` |
| `GET /v1/sessions/{id}/orders?status=` (`all`, `open`, `closed`, or a status) | `orders` | 200 `[Order]` |
| `GET /v1/sessions/{id}/orders/{order_id}` | `orders`, filtered | 200 `Order` |
| `DELETE /v1/sessions/{id}/orders/{order_id}` | `cancel_order` | 200 `Order` |
| `GET /v1/sessions/{id}/fills?since_day=` | `fills` | 200 `[Fill]` |
| `GET /v1/sessions/{id}/bars/{ticker}?resolution=day\|step&since_day=&limit=` | `bars` | 200 `[Bar]` |
| `GET /v1/sessions/{id}/news?since_day=&since_tick=&limit=` | `news` | 200 `[Headline]` |
| `POST /v1/sessions/{id}/advance` (body: `{"steps", "until"}`, optional) | `advance` | 200 `AdvanceResult` |
| `POST /v1/sessions/{id}/fork` (body: `{"label"}`, optional) | `fork` | 201 `SessionInfo` |
| `POST /v1/sessions/{id}/close` | `close` | 200 `SessionReport` |
| `GET /v1/describe` | none | 200, the same payload as the MCP `describe` tool |
| `GET /v1/health` | none | 200 `{"ok": true, "contract_version": "0.4"}`, no owner needed |
| `GET, POST, DELETE /mcp` | the MCP server | streamable HTTP, when `mcp_path` is set |

Every response body is the contract type's `to_dict()`, so
`T.from_dict(response.json())` rebuilds it, nested types included.

Every error, on both surfaces, is the contract's error body with the
contract's status:

| Code | Status |
|---|---|
| `invalid_request`, `invalid_order` | 400 |
| `unauthorized` | 401, with `WWW-Authenticate: Bearer` |
| `insufficient_buying_power` | 403 |
| `not_found` | 404 |
| `conflict`, `session_closed` | 409 |
| `rate_limited`, `quota_exceeded` | 429; when the error carries `retry_after`, the body has it and `Retry-After` is its ceiling in seconds |
| `internal` | 500 |

A body that fails the schema, malformed JSON, and a bad query parameter are
`invalid_request`. An unknown route is 404 `not_found`, a wrong method 405
`invalid_request`. An unhandled exception is 500 `internal` with the exception
named and the words "this is a bug".

## The broker facade

### Translations

Time is synthetic. Trading day `d` is the `d`-th weekday counted from Monday
2000-01-03, and tick `t` is `t` minutes after 09:30, at a fixed UTC-05:00
offset with no daylight saving and no holidays. The dates identify simulated
days and nothing else. Order, fill, bar and news times are in UTC with a `Z`;
the clock is in the -05:00 offset, as Alpaca sends it.

Ids are UUIDs because clients parse them as UUIDs. A service id that is
already a uuid4 hex string is shown in canonical form. Any other id (the core
uses `ord-000001`) is shown as a uuid5 of the session and the id, and looked
up again by recomputing, so nothing extra is stored. Assets and the account
get uuid5 ids the same way. The native order id is also accepted wherever an
order id is. News ids are integers, as Alpaca's are, derived from the session
and the headline's place in the log.

Numbers that Alpaca sends as strings (quantities, prices, money) are strings
here, written with `repr()` so a float survives the round trip exactly.
Market-data prices are JSON numbers, as in Alpaca's data API.

### Supported endpoints

All paths are under `/broker/{session_id}/v2`, except news.

| Endpoint | Notes |
|---|---|
| `GET /account` | See the field list below. |
| `GET /clock` | `is_open` is false once the session is closed. |
| `GET /calendar?start&end` | Weekdays, open 09:30, close 16:00, `settlement_date` the next weekday. Starts at day 0; without `end`, runs to today plus 252 trading days. |
| `GET /assets`, `GET /assets/{symbol or id}` | The session's roster. `class` `us_equity`, `exchange` `""`, `name` is the symbol, tradable, marginable, shortable, fractionable. `status=inactive` or another `asset_class` returns `[]`. |
| `GET /positions`, `GET /positions/{symbol or id}` | 404 when there is no position. |
| `DELETE /positions/{symbol}?qty&percentage` | Places a market order that closes all or part of the position and returns it. |
| `DELETE /positions?cancel_orders` | One close order per position, 207 with `[{"symbol", "status", "body"}]`. |
| `POST /orders` | Market or limit, `day` or `gtc`, `qty` (fractions allowed), `limit_price`, `client_order_id`. `extended_hours: false`, `order_class: "simple"` and `position_intent` are accepted and ignored. |
| `GET /orders?status&limit&after&until&direction&symbols&side` | `status` `open` (default), `closed` or `all`; `limit` default 50, at most 500; `direction` default `desc`. `nested` is accepted and ignored. |
| `GET /orders/{id}` | |
| `GET /orders:by_client_order_id?client_order_id=` | An order placed without one answers to its own UUID, which is what the facade reports as its `client_order_id`. |
| `DELETE /orders/{id}` | 204 with no body, as Alpaca. |
| `DELETE /orders` | Cancels every open order, 207 with `[{"id", "status", "body"}]`. |
| `GET /account/activities?activity_types`, `GET /account/activities/{type}` | `FILL` only; any other type returns `[]` because a simulation has no dividends, fees or transfers. `direction`, `after`, `until`, `page_size` (max 100) and `page_token` work. |
| `GET /stocks/bars?symbols&timeframe`, `GET /stocks/{symbol}/bars?timeframe` | `timeframe` `1Day` for day bars, or `<ticks_per_step>Min` (also `<n>T`, or `1Hour` when a step is 60 ticks) for one bar per step; anything else is 400 naming the two this session has. `start`, `end`, `limit` (default 1000, max 10000), `sort` and `page_token` work; `feed`, `adjustment`, `asof` and `currency` are accepted and ignored. Without `start`, bars begin at the start of the current day, as Alpaca's do. |
| `GET /stocks/trades/latest?symbols`, `GET /stocks/{symbol}/trades/latest` | `p` is the last price; `s` is the last step's volume (`Quote.step_volume`), 0 before the day's first step. |
| `GET /stocks/quotes/latest?symbols`, `GET /stocks/{symbol}/quotes/latest` | `bp`/`ap` from the book's best bid and ask, or the last price when the service has none. Sizes are 0. |
| `GET /stocks/snapshots?symbols`, `GET /stocks/{symbol}/snapshot` | `latestTrade`, `latestQuote`, `dailyBar` (today so far) and, from day 1, `prevDailyBar`. |
| `GET /broker/{session_id}/v1beta1/news?symbols&start&end&limit&sort&page_token` | Alpaca's news API over the session's headline log: `headline` is the text, `symbols` the tickers, `created_at` when it reached the market, `source` and `author` `tradefloor`, `summary` and `content` empty. `limit` default 10, max 50; `sort` default `desc`. |
| `POST /tradefloor/advance` | Not Alpaca. Body `{"steps": n}` and/or `{"until": "close" | "next_open"}`. Returns the Alpaca-shaped clock, the new fills as `FILL` activities, the headlines released as news items, the expired orders, and `state_hash`. |

The multi-symbol data routes drop symbols the session does not have, as
Alpaca does; the single-symbol routes answer 404.

### Field by field

Orders carry every field alpaca-py's `Order` model reads. `status` maps
`accepted` to `new`, `cancelled` to `canceled`, and the rest by name, with one
exception. A market order the book could only part fill (contract 0.2: status
`filled`, `filled_quantity` below `quantity`, reason "partial ...") is
`canceled` with its real `filled_qty`, `filled_avg_price` and `filled_at`, and
`canceled_at` at the same moment. That is how Alpaca reports an order whose
unfilled rest will never trade, such as an IOC order that part filled.
`partially_filled` would tell a bot the rest is still working, and it would
wait for shares that are never coming. The order's `FILL` activity is
`partial_fill`, with `leaves_qty` the dropped rest and `order_status`
`partially_filled`, which is what Alpaca records at the moment of such a fill.

Order times come from the contract's clocks: `submitted_at` and `created_at`
from `submitted_at`, and `updated_at` from `updated_at` (0.3), which is also
`canceled_at`, `expired_at` or `failed_at` for an order in that state.
`filled_at` is the last fill's time. `expires_at` is 16:00 on the order's
trading day for `day` orders (the next day's when placed after the close) and
null for `gtc`. A rejected order (over the leverage cap, or "insolvent") is
`rejected` with `failed_at`; Alpaca has no field for the reason, so a bot that
needs it reads the native order. `stop_price`, `trail_*`, `hwm`, `legs`,
`notional` and `replaced_*` are null.

The account has `id`, `account_number`, `status` `ACTIVE`, `currency` `USD`,
`cash`, `equity` and `portfolio_value` (net worth), `long_market_value`,
`short_market_value` (negative, as Alpaca), `buying_power` and
`regt_buying_power` (max_leverage x net worth - gross exposure, floored at 0,
so 0 when insolvent and 0 once the session is closed), `multiplier`
(max_leverage), `non_marginable_buying_power` (cash), `last_equity`,
`shorting_enabled` true, `trading_blocked` (true once the session is closed),
`transfers_blocked` true, and zeros for fees, transfers, SMA and options.
`last_equity` is cash plus each position at the previous close, which is the
previous close's equity only if nothing traded today. An uncapped session
(`max_leverage: null`) has no `buying_power`, `regt_buying_power` or
`multiplier`, because there is no number to report. The fields Alpaca removed
on 2026-07-06 (`pattern_day_trader`, `daytrade_count`,
`daytrading_buying_power`) are not sent. Margin requirements are not modelled
and are not sent.

Positions have `qty` negative for a short and `side` `long` or `short`,
`avg_entry_price`, `market_value`, `cost_basis`, `unrealized_pl` and
`unrealized_plpc`, `current_price`, `lastday_price` (previous close),
`change_today`, and `qty_available` (the holding less open orders on the
closing side). `unrealized_intraday_pl` is qty x (last - previous close), so
for a position opened today it counts from the previous close where Alpaca
counts from the entry price.

Bars have `t` (the bar's start: midnight for a day bar, the step's first
minute for a step bar), `o`, `h`, `l`, `c`, `v`, and `n` and `vw` null because
the service reports no trade count or VWAP.

### Not supported

Refused with 400 `invalid_order` and a message naming what is supported:
notional orders, stop, stop-limit and trailing-stop orders, bracket, OCO, OTO
and multi-leg orders, `time_in_force` other than `day` and `gtc` (`ioc`, `fok`,
`opg`, `cls`), and `extended_hours: true`.

Not served at all (404): replacing an order (`PATCH /orders/{id}`), portfolio
history, account configurations, watchlists, corporate actions, options,
crypto, historical trades and quotes (`/stocks/trades`, `/stocks/quotes`),
latest bars (`/stocks/bars/latest`, a minute bar in Alpaca), and the streaming
APIs (trade updates, market data and news websockets).

### Where it differs from Alpaca

- Status codes follow the contract, so an invalid order is 400 where Alpaca
  sends 422, and a closed session is 409. Error `code` is the contract's
  string (`"invalid_order"`), where Alpaca sends a number. alpaca-py raises
  `APIError` for both and its `.code` and `.message` work.
- API keys are ignored when self-run. alpaca-py insists on non-empty keys, so
  pass any strings. The hosted layer can read them (see `request_api_key`).
- Every session is its own market with its own account. There is no
  cross-session account and no way for two bots to meet in one book.

## Embedding

This is the seam for the hosted layer.

```python
from tradefloor.serve.http import create_app, error_response, request_api_key, resolve_owner
from tradefloor.serve.mcp import create_server, create_http_app, create_mcp_endpoint

app = create_app(service,
                 owner_resolver=resolve,   # (request) -> owner; sync or async
                 middleware=[...],         # Middleware(...), (cls, options) or cls
                 mcp_path="/mcp",          # also serve MCP over streamable HTTP, same resolver
                 serialize=False,          # True: one lock around every service call
                 describe=None)            # replaces the describe payload (HTTP and MCP)
server = create_server(service, owner="local")               # stdio: one fixed owner
mcp_app = create_http_app(service, owner_resolver=resolve)   # MCP over HTTP on its own
```

`owner_resolver` runs before every route that touches the service, the facade
and `/mcp` included. Raise `ServeError("unauthorized", ...)` to refuse with
401, or `rate_limited` / `quota_exceeded` for 429, with `retry_after` when the
wait is known. A refused request never reaches the service.
`request_api_key(request)` returns the key from `Authorization: Bearer`,
`X-API-Key`, `APCA-API-SECRET-KEY` or `APCA-API-KEY-ID`, in that order, so a
hosted resolver authenticates broker clients and MCP clients without changes
on their side.

MCP over HTTP resolves the owner per HTTP request. `OwnerGate`, in front of
the MCP endpoint, runs the resolver, answers a refusal with the contract's
body and status before the MCP layer sees the request, and leaves the owner
on `request.state.owner` for the tools. The endpoint is stateless and answers
in JSON, so any instance behind a load balancer can take any request. MCP's
own DNS-rebinding check is off because the Host header is guarded for the
whole app instead: `loopback_guard(host)` when self-run, the edge when hosted.
A server built by `create_server(..., owner_resolver=...)` refuses a call that
did not arrive over HTTP with `unauthorized`.

The app's lifespan runs the MCP session manager, and uvicorn runs the
lifespan. A wrapper that mounts the app inside another app must enter
`app.state.mcp_session_manager.run()` in its own lifespan, and a test client
must be used as a context manager (`with TestClient(app)`).

An exception raised inside middleware does not reach the app's error
handlers, so middleware that refuses a request should return
`error_response(ServeError(...))`. `app.state.service`,
`app.state.owner_resolver` and `app.state.service_lock` are there for a
wrapper. Pass `describe=` when the wrapper caps limits lower than the
contract, so clients read the limits that apply.

Contract 0.3 (section 4d) makes a `SessionService` responsible for its own
thread safety, one lock per session, so the transports no longer hold a
global lock and calls on different sessions run in parallel.
`serialize=True` restores the lock for a service that is not thread-safe.

`tests/serve/fakes.py` has `FakeSessionService`, an in-memory service that
follows contract 0.4 with a fake price process, for testing a layer without
the engine. It is thread-safe, records every call's owner in `.calls`, and
with `headlines=True` releases one headline a day. Where the contract is
silent it does what the core does, and `test_the_fake_does_what_the_core_does`
in `tests/serve/test_http_native.py` runs the same script against both to keep
it that way. `make_service("fake")` and `make_service("core", root)` build
either one.

## Dependencies

The transports import their dependencies inside `create_app`,
`create_server` and `create_http_app`, so `import tradefloor.serve` (and even
importing the two transport modules) loads none of them. A test checks this.

| Package | Needed for | Tested with | Proposed floor |
|---|---|---|---|
| `fastapi` (brings `starlette`, `pydantic`) | HTTP | 0.141.1 (starlette 1.7.0, pydantic 2.13.5) | `fastapi>=0.141` |
| `uvicorn` | `python -m tradefloor.serve`, `... serve.mcp --http` | 0.53.0 | `uvicorn>=0.53` |
| `mcp` | MCP, stdio and streamable HTTP | 2.2.0 | `mcp>=2.2` |
| `httpx` | tests (FastAPI's `TestClient`) | 0.28.1 | test only |
| `alpaca-py` | the SDK test, skipped without it | 0.44.0 | test only, never a dependency |

Proposed extra: `serve = ["fastapi>=0.141", "uvicorn>=0.53", "mcp>=2.2"]`.
The floors are the versions this was written and tested against, following
the rule in `pyproject.toml`.

## Tests

`pytest tests/serve/test_mcp_*.py tests/serve/test_http_*.py -n 2` runs about
170 tests in about 15 seconds. Tests marked for the core run against
`LocalSessionService` over a temporary `FileStore`, and skip when the core
does not yet implement the contract version they need.

| File | Covers |
|---|---|
| `test_http_native.py` | Every native route on the fake and the core; `from_dict` round trips; error mapping for every code; owner resolution (default, sync, async, refusal, rate limit); validation; middleware; OpenAPI; optional imports; the fake-versus-core script. |
| `test_http_broker.py` | Every facade route and its shape; partial fills, order times, bars, snapshots, news; unsupported orders refused; a bot loop in raw HTTP on the fake and the core; alpaca-py's trading, data and news clients against a live uvicorn server, on the fake and the core. |
| `test_http_server.py` | `python -m tradefloor.serve` and `python -m tradefloor.serve.mcp --http` as processes; the Host guard; a server killed with SIGKILL mid-session and restarted on the same store serves every native and facade view unchanged (bars included), then continues to the same `state_hash` as a server that never died. |
| `test_mcp_tools.py` | Registration, descriptions and annotations; every tool on the fake and the core; errors as tool errors; owner isolation; `python -m tradefloor.serve.mcp` over stdio on the fake and the core. |
| `test_mcp_http.py` | MCP over streamable HTTP with the SDK's client: an owner per key, on its own and beside the API; 401 and 429 from the gate; sessions shared with the HTTP API; the core. |

## Contract change requests

Decided in contracts 0.2 to 0.4, from this document's earlier list:
`Order.updated_at`, `bars`, `Quote.step_volume`, per-session thread safety,
the cancel, advance and close edge cases, `ServeError.retry_after`, nested
`from_dict`, and the news log.

Still open:

1. A stable id on `Headline`. The facade numbers news by its place in the
   log, which holds while the log only grows; an id from the service would
   survive any future trimming.
2. `bars` for several tickers in one call. The facade's `/v2/stocks/bars` and
   snapshots make one call per symbol, which a hosted layer that meters calls
   counts as many.
3. Pin `news(limit=)` and `bars(limit=)` as "the most recent `limit`" in the
   contract text for both, so a client knows which end a limit cuts.
