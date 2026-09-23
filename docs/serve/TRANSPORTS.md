# Transports: MCP and HTTP

The trading session server has two front doors over the same `SessionService`
(docs/serve/CONTRACT.md, section 6). An LLM agent talks to the MCP server over
stdio. A bot talks to the HTTP server, either through the native `/v1` routes
or through a facade shaped like Alpaca's v2 trading API, so a bot already
written for Alpaca can be pointed at a simulated session by changing its base
URL.

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
`~/.tradefloor/sessions`. OpenAPI docs are at http://127.0.0.1:8765/docs.
Options: `--host`, `--port`, `--store DIR`, `--owner NAME` (default `local`),
and `--service module:factory` to serve some other `SessionService`.

There is no authentication. Every request acts as one owner, which is right
on your own machine and wrong anywhere else, so binding to anything other than
loopback prints a warning. The hosted layer adds keys through `create_app`
(see "Embedding" below).

### Connect an agent over MCP

The MCP server runs as a subprocess of the client. For Claude Desktop, add
this to `claude_desktop_config.json`, using the Python that has tradefloor
installed:

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

`python -m tradefloor.serve mcp` does the same thing. The MCP server takes
`--store`, `--owner` and `--service` like the HTTP one, and uses the same
default store, so a session opened over MCP can be read over HTTP by the same
owner. Keep one writer per session, though: the core reloads a session when
the store's copy is newer, but `FileStore` has no cross-process lock, and two
processes writing to one session at once is unsupported in 0.1.

| Tool | Service call | What it does |
|---|---|---|
| `describe` | none | How time, orders and fills work, limits, units, error codes, and caveats read from the realism envelope. The server's instructions tell the agent to call it first. |
| `open_session` | `open` | Opens a session. Every argument is optional and defaults to `SessionConfig()`. |
| `list_sessions` | `list` | Your sessions, open and closed. |
| `observe` | `observe` | Clock, quotes, VIX, macro, account, positions, open orders, headlines, `state_hash`. |
| `place_order` | `place_order` | Market or limit, `day` or `gtc`, optional `client_order_id`. |
| `cancel_order` | `cancel_order` | Cancels an order that is still `accepted`. |
| `list_orders` | `orders` | Optionally one status. |
| `list_fills` | `fills` | Optionally from a trading day on. |
| `advance` | `advance` | `steps`, or `until="close"` / `"next_open"`, where `steps` then counts how many times. |
| `fork_session` | `fork` | Copies a session into a new one. |
| `close_session` | `close` | Ends the session and returns its report with computed caveats. |

Each tool description says what the call does, its units and limits, and the
two traps a model falls into: a market order is not filled when placed, and
time does not move until `advance`. Read-only tools carry `readOnlyHint`;
`close_session` carries `destructiveHint`.

A refusal comes back as a tool error (`isError: true`) whose structured
content and text are both `{"code", "message"}`, the same body the HTTP API
returns. Arguments that fail the schema come back the same way with code
`invalid_request` and the argument named, and an unknown tool is `not_found`.

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
curl -s -X POST localhost:8765/v1/sessions/$S/close
```

### Point a broker client at the facade

The facade for session `S` lives at `http://127.0.0.1:8765/broker/S`, and the
client adds `/v2/...` itself. With alpaca-py:

```python
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

url = f"http://127.0.0.1:8765/broker/{session_id}"
trading = TradingClient("unused", "unused", url_override=url)   # keys are required by the SDK, ignored here
data = StockHistoricalDataClient("unused", "unused", url_override=url)

account = trading.get_account()
price = data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols="AAA"))["AAA"].price
trading.submit_order(MarketOrderRequest(symbol="AAA", qty=10, side=OrderSide.BUY,
                                        time_in_force=TimeInForce.DAY))
trading.post("/tradefloor/advance", {"steps": 1})   # where the live bot would sleep
```

The base URL is the only change, with one exception. Simulated time moves
only when you ask, so the place where a live bot sleeps becomes a call to
`POST /broker/S/v2/tradefloor/advance` (or the native `/v1/sessions/S/advance`).
`tests/serve/test_http_broker.py` runs this with the real alpaca-py 0.44.0
against a live server, on the fake service and on `LocalSessionService`.

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
| `GET /v1/sessions/{id}/orders?status=` | `orders` | 200 `[Order]` |
| `GET /v1/sessions/{id}/orders/{order_id}` | `orders`, filtered | 200 `Order` |
| `DELETE /v1/sessions/{id}/orders/{order_id}` | `cancel_order` | 200 `Order` |
| `GET /v1/sessions/{id}/fills?since_day=` | `fills` | 200 `[Fill]` |
| `POST /v1/sessions/{id}/advance` (body: `{"steps", "until"}`, optional) | `advance` | 200 `AdvanceResult` |
| `POST /v1/sessions/{id}/fork` (body: `{"label"}`, optional) | `fork` | 201 `SessionInfo` |
| `POST /v1/sessions/{id}/close` | `close` | 200 `SessionReport` |
| `GET /v1/describe` | none | 200, the same payload as the MCP `describe` tool |
| `GET /v1/health` | none | 200 `{"ok": true, "contract_version": "0.1"}`, no owner needed |

Every error, on both surfaces, is `{"code", "message"}` with the contract's
status:

| Code | Status |
|---|---|
| `invalid_request`, `invalid_order` | 400 |
| `unauthorized` | 401, with `WWW-Authenticate: Bearer` |
| `insufficient_buying_power` | 403 |
| `not_found` | 404 |
| `conflict`, `session_closed` | 409 |
| `rate_limited`, `quota_exceeded` | 429, with `Retry-After` when the error has a `retry_after` attribute |
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
days and nothing else. Order, fill and activity times are in UTC with a `Z`;
the clock is in the -05:00 offset, as Alpaca sends it.

Ids are UUIDs because clients parse them as UUIDs. A service id that is
already a uuid4 hex string is shown in canonical form. Any other id (the core
uses `ord-000001`) is shown as a uuid5 of the session and the id, and looked
up again by recomputing, so nothing extra is stored. Assets and the account
get uuid5 ids the same way. The native order id is also accepted wherever an
order id is.

Numbers that Alpaca sends as strings (quantities, prices, money) are strings
here, written with `repr()` so a float survives the round trip exactly.
Market-data prices are JSON numbers, as in Alpaca's data API.

### Supported endpoints

All paths are under `/broker/{session_id}/v2`.

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
| `GET /stocks/trades/latest?symbols`, `GET /stocks/{symbol}/trades/latest` | Alpaca data API shape. `p` is the last price; `s` is 0 because the service reports no trade sizes. |
| `GET /stocks/quotes/latest?symbols`, `GET /stocks/{symbol}/quotes/latest` | `bp`/`ap` from the book's best bid and ask, or the last price when the service has none. Sizes are 0. |
| `GET /stocks/snapshots?symbols`, `GET /stocks/{symbol}/snapshot` | `latestTrade`, `latestQuote` and `dailyBar` (today so far: open, high, low, last, volume). |
| `POST /tradefloor/advance` | Not Alpaca. Body `{"steps": n}` or `{"until": "close" | "next_open"}`. Returns the Alpaca-shaped clock, the new fills as `FILL` activities, the expired orders, and `state_hash`. |

The multi-symbol data routes drop symbols the session does not have, as
Alpaca does; the single-symbol routes answer 404.

### Field by field

Orders carry every field alpaca-py's `Order` model reads. `status` maps
`accepted` to `new`, `cancelled` to `canceled`, and the rest by name.
`expires_at` is 16:00 on the order's trading day for `day` orders (the next
day's when the order was placed after the close) and null for `gtc`.
`filled_at` is the time of the fill, and `expired_at` is that 16:00 for an
expired order. `canceled_at` and `failed_at` are always null, because the
contract's `Order` does not record when it was cancelled or rejected (change
request 1). `stop_price`, `trail_*`, `hwm`, `legs`, `notional`, `replaced_*`
are null.

The account has `id`, `account_number`, `status` `ACTIVE`, `currency` `USD`,
`cash`, `equity` and `portfolio_value` (net worth), `long_market_value`,
`short_market_value` (negative, as Alpaca), `buying_power` and
`regt_buying_power` (max_leverage x net worth - gross exposure, floored at 0,
and 0 once the session is closed), `multiplier` (max_leverage),
`non_marginable_buying_power` (cash), `last_equity`, `shorting_enabled` true,
`trading_blocked` (true once the session is closed), `transfers_blocked` true,
and zeros for fees, transfers, SMA and options. `last_equity` is cash plus
each position at the previous close, which is the previous close's equity only
if nothing traded today. An uncapped session (`max_leverage: null`) has no
`buying_power`, `regt_buying_power` or `multiplier`, because there is no
number to report. The fields Alpaca removed on 2026-07-06
(`pattern_day_trader`, `daytrade_count`, `daytrading_buying_power`) are not
sent. Margin requirements are not modelled and are not sent.

Positions have `qty` negative for a short and `side` `long` or `short`,
`avg_entry_price`, `market_value`, `cost_basis`, `unrealized_pl` and
`unrealized_plpc`, `current_price`, `lastday_price` (previous close),
`change_today`, and `qty_available` (the holding less open orders on the
closing side). `unrealized_intraday_pl` is qty x (last - previous close), so
for a position opened today it counts from the previous close where Alpaca
counts from the entry price.

### Not supported

Refused with 400 `invalid_order` and a message naming what is supported:
notional orders, stop, stop-limit and trailing-stop orders, bracket, OCO, OTO
and multi-leg orders, `time_in_force` other than `day` and `gtc` (`ioc`, `fok`,
`opg`, `cls`), and `extended_hours: true`.

Not served at all (404): replacing an order (`PATCH /orders/{id}`), portfolio
history, account configurations, watchlists, corporate actions, options,
crypto, historical bars, trades and quotes (`/stocks/bars`,
`/stocks/{symbol}/bars` and the like, change request 2), latest minute bars,
news, and the streaming APIs (trade updates and market data websockets).

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
from tradefloor.serve.http import create_app, error_response, request_api_key
from tradefloor.serve.mcp import create_server

app = create_app(service,
                 owner_resolver=resolve,   # (request) -> owner; sync or async
                 middleware=[...],         # Middleware(...), (cls, options) or cls
                 serialize=True,           # one lock around every service call
                 describe=None)            # replaces GET /v1/describe's payload
server = create_server(service, owner="local", describe=None)
```

`owner_resolver` runs before every route that touches the service, including
the facade. Raise `ServeError("unauthorized", ...)` to refuse with 401, or
`rate_limited` / `quota_exceeded` for 429; a `retry_after` attribute on the
error (seconds) becomes a `Retry-After` header. A refused request never
reaches the service. `request_api_key(request)` returns the key from
`Authorization: Bearer`, `X-API-Key`, `APCA-API-SECRET-KEY` or
`APCA-API-KEY-ID`, in that order, so a hosted resolver authenticates broker
clients without changes on their side.

An exception raised inside middleware does not reach the app's error
handlers, so middleware that refuses a request should return
`error_response(ServeError(...))`. `app.state.service`,
`app.state.owner_resolver` and `app.state.service_lock` are there for a
wrapper. Pass `describe=` when the wrapper caps limits lower than the
contract, so clients read the limits that apply.

`serialize=True` is the default because the contract does not say a
`SessionService` must be thread-safe and FastAPI runs routes on a thread
pool. The cost is that one long `advance` makes every other request wait
(change request 4).

`tests/serve/fakes.py` has `FakeSessionService`, an in-memory service that
follows the contract with a fake price process, for testing a layer without
the engine. It is thread-safe and records every call's owner in `.calls`.
Where the contract is silent it does what the core does, and
`test_the_fake_does_what_the_core_does` in `tests/serve/test_http_native.py`
runs the same script against both to keep it that way. `make_service("fake")`
and `make_service("core", root)` build either one.

## Dependencies

The transports import their dependencies inside `create_app` and
`create_server`, so `import tradefloor.serve` (and even importing the two
transport modules) loads none of them. A test checks this.

| Package | Needed for | Tested with | Proposed floor |
|---|---|---|---|
| `fastapi` (brings `starlette`, `pydantic`) | HTTP | 0.141.1 (starlette 1.7.0, pydantic 2.13.5) | `fastapi>=0.141` |
| `uvicorn` | `python -m tradefloor.serve` | 0.53.0 | `uvicorn>=0.53` |
| `mcp` | MCP | 2.2.0 | `mcp>=2.2` |
| `httpx` | tests (FastAPI's `TestClient`) | 0.28.1 | test only |
| `alpaca-py` | the SDK test, skipped without it | 0.44.0 | test only, never a dependency |

Proposed extra: `serve = ["fastapi>=0.141", "uvicorn>=0.53", "mcp>=2.2"]`.
The floors are the versions this was written and tested against, following
the rule in `pyproject.toml`.

## Tests

`pytest tests/serve/test_mcp_*.py tests/serve/test_http_*.py -n 2` runs 127
tests in about 10 seconds. Tests marked for the core run against
`LocalSessionService` over a temporary `FileStore`, and skip when the core is
not importable.

| File | Covers |
|---|---|
| `test_http_native.py` | Every native route on the fake and the core; error mapping for every code; owner resolution (default, sync, async, refusal, rate limit); validation; middleware; OpenAPI; optional imports; the fake-versus-core script. |
| `test_http_broker.py` | Every facade route and its shape; unsupported orders refused; a bot loop in raw HTTP on the fake and the core; alpaca-py's `TradingClient` and `StockHistoricalDataClient` against a live uvicorn server, on the fake and the core. |
| `test_http_server.py` | `python -m tradefloor.serve` as a process; a server killed with SIGKILL mid-session and restarted on the same store serves every native and facade view unchanged, then continues to the same `state_hash` as a server that never died. |
| `test_mcp_tools.py` | Registration, descriptions and annotations; every tool on the fake and the core; errors as tool errors; owner isolation; `python -m tradefloor.serve.mcp` over stdio on the fake and the core. |

## Contract change requests

1. Give `Order` a time for its last change (for example `updated_at: Clock`,
   or `closed_at: Clock | None`). The facade cannot fill `canceled_at`,
   `failed_at` or an accurate `updated_at` without it, and a bot that sorts by
   update time sees nulls.
2. Add a history read to `SessionService`, per step or per day OHLCV for a
   ticker over a range. Bars are the market data most bots read first
   (`/v2/stocks/bars`); without them a bot must build its own history by
   polling `observe` after every `advance`, and `prevDailyBar` stays empty.
3. Report the last trade's size, or the volume of the last step, in `Quote`.
   The facade reports trade size 0 today.
4. Say whether a `SessionService` must be thread-safe. `LocalSessionService`
   takes a lock per session, so it looks safe; if the contract says so, the
   self-run server can pass `serialize=False` and stop making every session
   wait for the slowest `advance`.
5. Pin three behaviours the contract leaves open and the core and the fake
   already agree on: cancelling an order that is not `accepted` is
   `invalid_request`; `advance(steps=n, until="close" | "next_open")` does it
   `n` times; closing a session leaves accepted orders accepted. The last is
   worth a decision rather than a default, because a report on a session with
   live orders is ambiguous about what they would have done.
6. Add `retry_after: float | None` to `ServeError` for `rate_limited` and
   `quota_exceeded`. The HTTP transport already turns such an attribute into
   `Retry-After`; making it part of the type lets the hosted layer set it
   without relying on an undeclared attribute.
7. `_Data.from_dict` does not rebuild nested dataclasses:
   `SessionInfo.from_dict(d).config` stays a dict. The transports only
   serialise, so they are unaffected, but a Python client that parses
   responses back into the types will trip on it.
