# Agents

How an agent sees the market, how it is scored, and what the sandbox does
and does not stop. The README has the short version; this page has the
detail behind each warning there.

## The agent's view

A Python agent implements `act(obs)` and returns orders. `obs.engine` is a
read-only market view: prices, the public columns, each book, the bars of days
already recorded (a World run with `record=True`; `tf.evaluate` records none),
the published macro fields, the curve and which names have news today.
`obs.history` holds a daily bar per name and the published macro for every day
the run has closed, in `evaluate`, `rank`, `World` and `tca.analyse`, and
needs no extra package. A bar's close is the day's last print. On pt-v21 the
market's close then re-marks every name, about 11 bp from the last print at
the median on a 20-name roster, and pt-v21 also moves prices overnight, so
the next day opens about 27 bp from the bar's close at the median. A
broker's daily bar closes at the official close. `obs.portfolio` reads the agent's own
positions and cannot trade. Forking the engine, writing to it and reading the
hidden state all raise `tf.SandboxError`. The hidden state includes the true
business-cycle phase; the macro fields carry the phase as published. The gym
environment's `env.engine` and `env.portfolio` are the same views.

On a model that lists futures (pt-v22 lists index, rate and oil futures),
`obs.engine.contracts()`, `obs.engine.quote(symbol)` and
`obs.engine.settlements()` read them, and `act` trades one by its symbol,
`IDX.F0056` for instance, as it trades a ticker. The portfolio holds a
future at its last settlement mark and pays the mark's move in cash at each
close; a close that leaves equity under the contracts' maintenance margin
makes a margin call, and if equity is not back at the initial margin by the
next open, every contract is closed at the market. On a model with
`margin_pretrade_check`, an order that would leave equity under the initial
margin it then asks raises `tf.portfolio.MarginError`. MODEL.md's Margin
section has the rules. `obs.portfolio.margin()` reads the account's initial and
maintenance requirement, its excess over the initial and any open call, and
`obs.portfolio.futures` the positions. `tf.contracts.parse` and
`tf.contracts.format` read and write a symbol. A framework adapter shows its
model the same: the payload (version 3) gains `index`, `futures` and
`margin` on such a model, and an action may name a listed future, counted
in contracts. The MCP server's `list_contracts` and `quote_contracts` read a
session's futures, and `session_step` trades one by its symbol.
`Engine.futures_bars()` and `Engine.settlements_table()` give them as Arrow
tables.

On a model that lists index options (`options_index_listed`, off on every
shipped preset), `obs.engine.option_expiries()` reads each listed expiry's
strike grid, forward, at-the-money volatility and 25-delta skew,
`obs.engine.chain("IDX", expiry)` one expiry's options from the dealer, and
`obs.engine.quote(symbol)` one option with its scan margin. `act` trades an
option by its symbol, `IDX.O0035.C1050.00` being the 1050 call settling at
session 35, in contracts of $100 a point, during the session only. The
premium moves cash at the fill, the position is worth the dealer's mid and
settles in cash on the expiry session's opening prints, and short options
add a scan margin to `obs.portfolio.margin()`; `obs.portfolio.options` holds
the positions. The payload gains `options` (each expiry's summary and the
options held) with `index` and `margin`, and an action may name a listed
option. The MCP server's `option_chain` reads a session's expiries and one
expiry's chain, `quote_contracts` quotes an option, and `session_step`
trades one by its symbol. `Engine.chain_table()` gives the chains now as an
Arrow table.

On a model that lists single-name options (`options_names_listed`; pt-v22
lists them), every name lists American calls and puts from the
first close, 100 shares a contract, each settling at its expiry session's
close: `obs.engine.name_option_roots()` names them, and
`obs.engine.option_expiries("ACME")`, `obs.engine.chain("ACME", expiry)` and
`obs.engine.surface("ACME")` read one name's, the dealer's quote being a
tree's value with the dividends to expiry. `act` trades one by its symbol,
`ACME.O0035.C42.50` being the 42.50 call, and returning
`{"ACME.O0035.C42.50": tf.Exercise(5)}` exercises 5 contracts held at the
close: a call's holder pays the strike for the shares, a put's delivers
them for it. The dealer exercises the longs agents' writing leaves it
(calls on the eve of an ex-date whose dividend beats the time value, deep
puts), and at expiry everything a cent in the money is exercised; both are
assigned to short positions pro rata. So an account can be handed stock,
or go short, without an order. That stock is a holding like any other,
valued at the price and traded by the next order; it is not a trade, and
`obs.portfolio.deliveries` lists what moved. Options are margined by a scan
with the stock held in the same name, and delivered stock is margined from
the close it arrives, so that close can make a margin call. On a model with
`margin_option_scan_range`, a name's options and stock are margined at the
worst of ten moves of the name over that share of its price either way, and
delivered stock at that share of its value (FINRA's portfolio-margin range
for an equity is 15%). On a model with `margin_option_minimum`, an option
asks at least that many dollars a share (FINRA's portfolio-margin minimum
is $.375). An unmet call closes the contracts and the stock in each name
whose options the account held or took delivery in; with
`margin_liquidation_worked` each sale is sent again while the market still
buys, and a long option nobody bids stays held. The payload's `options` adds
`names` (each name's 30-day at-the-money volatility) and an action may name
a name's option, with the side `EXERCISE` to exercise. The MCP server's
`option_chain` reads a name's chain with `root` set to its ticker, and
`session_step` takes `{"exercise": q}`.

In the mapping `act` returns, a plain number is a market order for that many
shares, negative to sell. A native Python agent can also return
`tf.Limit(quantity, price)`, which waits in the book for what does not fill,
`tf.Cancel()`, and on a single-name option held `tf.Exercise(contracts)`.
The framework adapters can send all four: an LLM's action with a
`limit_price` becomes a `tf.Limit`, `side: "CANCEL"` becomes a
`tf.Cancel()` and `side: "EXERCISE"` a `tf.Exercise`. There are no stop, stop-limit or bracket orders, so a stop has
to be checked at each step: at six steps a day an emulated stop filled a
median 26.5 bp past its level, 9 bp at 5-minute steps, and 540 bp at the 90th
percentile in the packaged recession. A trade costs the spread and its impact
on the book. There are no commissions and no borrow fee on a short. Uninvested
cash earns nothing by default. A negative cash balance pays the policy rate
before each close in `tf.evaluate`, `tf.rank` and `World`, which is below a
broker's margin rate, so leverage up to the default `max_leverage=2.0` costs
at least that. `margin_interest=False` makes borrowing free, as it was before
0.8.5, and `cash_interest=True` pays the policy rate on idle cash.

Agents in one `tf.evaluate` or `tf.rank` call run one after another in one
Python process, on the same seed, so the first agent can leave the price path
in a class variable for a later one, and nothing detects it. The read-only
view guards against accidents, and an agent written to cheat can get round
it. One way round it is unflagged: `tf.evaluate`'s own frames hold the
`seed` and the `universe`, an agent can read them through `sys._getframe`,
build a second `tf.Engine` from them and run it ahead. The copy count does
not see a newly built engine, so the card says `tampered=False`. To compare
agents you did not write, or two that might share state, run each in its own
process.

In a `World` with several agents, orders placed at the same step execute in
label order, alphabetical, for the whole run. Two identical buyers of 10% of
a day's volume paid 19.5 to 30.7 bp apart on seeds 1 to 10, the later label
paying more. Rotate the labels across runs when you compare different agents
in one market.

The Oracle reads hidden state by declaring `privileged = True`, which gives
it `obs.hidden` and marks its scorecard. Pass `trusted_agents=True` for
research that needs the live engine, and every scorecard says so. Either way
the harness compares the engine's state hash around each call, and an agent
that changed the market is scored `tampered` and left out of `tf.rank`. So
is a sandboxed agent that forked or snapshotted the engine, however it
reached it.
[`tradefloor/sandbox.py`](https://github.com/simoncoombes/tradefloor/blob/main/python/tradefloor/sandbox.py)
lists what the view serves and what the check cannot catch. The view and the
check guard against accident. Agent code runs in the harness's own process, so
it can reach the engine by walking the interpreter, and a read made that way
leaves no trace. Run code you do not trust in a separate process, through the
MCP server.

## Scoring

Every `evaluate` and `rank` run starts at day 0, so a rule that needs 20
days of prices would sit out the first 20 days while buy-and-hold is
invested, which counts against it in the comparison. `history_days=20` runs
the market for 20 days before day 0 with nobody trading, and
`obs.history.bars(ticker, last=20)` returns those days' bars at the first
decision. Each scored day joins the history after its close, and
`obs.history.macro()` gives the published macro figures for the same days.
The scored days continue the warmed market, so on the same seed they are
different days from a run without the warm-up, and the scorecard records
`history_days`. The reference agents and `StrategySpec` strategies keep
their own price history and do not read `obs.history`.

Add `tf.baselines.reference_agents()` to the entrants to read a score against
buy-and-hold on the same market: `tf.versus_buy_and_hold(scores)` gives each
agent's P&L less buy-and-hold's. The reference set includes an Oracle that
reads the model's fair value. On pt-v19 and earlier `tf.capture_ratio(scores)`
gives each P&L as a fraction of the Oracle's. On pt-v20 and on pt-v21, the
default, it returns `{}` and warns why: market moves there mostly stick, so even perfect
knowledge of fair value leaves little edge, and buy-and-hold is the
comparison to quote.

### Explanation scores

The twelve factors `engine.truth()` reports sum to the change in mispricing,
the log gap between the price and the model's fair value. On pt-v20 and
pt-v21 a shock that sticks is booked whole to `random_noise` and then taken
back out by `fair_value_shift`, so the two move against each other (a
per-tick correlation of -0.99 on pt-v21, three seeds of a 20-name roster
over five days) and should be read together.
`engine.explain(ticker, day)` breaks down the move in the printed price
instead. An agent scored on explaining moves gets an `explanation_accuracy`:
the share of days on which it named the factor that moved prices most, open
to close, summed over every name. `fair_value_shift` moves no price and is
never that answer. A constant answer scores near the top, because
`random_noise` wins most days. On pt-v21 it wins about four days in five and
`overnight` most of the rest, so answering `random_noise` every day scores
0.60 to 1.0 (median 0.83) over ten seeds of 20 days on a 20-name roster. So
the scorecard carries `explanation_baseline`, what a constant answer
scores on the same days, and `explanation_edge`, the accuracy minus the
baseline, and its repr prints the three together. Only the edge means
anything. Quote it, or all three, and never the accuracy alone.

## Agent frameworks

The framework makes the decisions and tradefloor runs the market. Each
adapter in `tradefloor.integrations` takes one framework through the same
steps: the agent sees the market, decides, its orders fill, and the result is
scored. So two frameworks can be compared on the same market.

| Framework | tradefloor support |
|---|---|
| [Plain Python](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/callable/five_days.py) | Generic callable |
| [OpenAI Agents SDK](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/openai_agents/five_days.py) | Adapter |
| [PydanticAI](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/pydantic_ai/rate_shock.py) | Adapter |
| [LangGraph](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/langgraph/rate_shock.py) | Adapter |
| [FinRobot](https://github.com/simoncoombes/tradefloor/tree/main/examples/integrations/finrobot) | Existing integration |

```
pip install "tradefloor[openai-agents]"   # or [pydantic-ai], or [langgraph]
```

A plain Python function needs no extra. FinRobot keeps the `finrobot` extra
it has always had, and its section below covers the rate-shock experiment
that integration was built for.

Each example runs offline in seconds, with a fixed function in place of the
model, so it needs no API key. A seed replays the market exactly, but a live
model gives a different answer each time. So an adapter records each call
and its answer, and can replay the recording later without the framework or
a key. The four examples are in
[`examples/integrations/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/integrations),
which says what each framework contributes and what tradefloor keeps. They
run from a clone of this repository, because they read recorded runs from its
`tests/fixtures/`.

To test your own model-calling function this way, two arguments of
`callable_agent` matter. `postprocess=to_decision` runs
`to_decision(raw, payload)` on what the function returned, in a live run and
in a replay, so the parsing, risk checks and sizing written there are tested
by every replay. The transcript holds the raw response, and code left inside
the function after the model call runs live only. The callable adapter's
replay key is the payload, which does not include your system prompt, so
pass `info=AdapterInfo(framework="callable", instructions_digest=digest(PROMPT))`
when you record and when you replay. A replay under a different prompt is
then refused when the adapter is built. Without it the replay runs on the
old answers. The "Plain Python" section of
[`examples/integrations/README.md`](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/README.md)
has the whole pattern.

Some things multi-agent research needs are not supported yet. Every agent in a
`World` starts with the same cash. There is no
multi-agent Gymnasium environment, and the Gymnasium reward is the step's
change in net worth in dollars. `Ranking.separation` is a sign test with no
effect size, and `externalities` does not aggregate across seeds.

### FinRobot

This runs a [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot)
agent in a controlled tradefloor market. It runs a shared history, checkpoints
the world, forks it, raises rates by 200bps in one branch, and compares how
the same agent responds. It is the README's rate-shock demo with the agent
swapped and nothing else changed.

```bash
git clone https://github.com/simoncoombes/tradefloor
cd tradefloor
python examples/integrations/finrobot/rate_shock.py            # replays a real recorded run
```

By default it replays a recorded FinRobot run, which needs no API key, no
network and no FinRobot install. A plain `pip install tradefloor` runs it on
any supported Python.

Calling FinRobot itself needs the `finrobot` extra, which installs only on
Python 3.11. FinRobot declares Python 3.10 and 3.11, and tradefloor needs
3.11 or later. On 3.12 or 3.13, pip stops with "Could not find a version
that satisfies the requirement finrobot>=0.1.5". uv installs it anyway,
outside FinRobot's declared range, and `uv pip check` then reports the
conflict.

```bash
pip install "tradefloor[finrobot]"                             # Python 3.11 only
python examples/integrations/finrobot/rate_shock.py --live     # calls FinRobot
```

- [`examples/integrations/finrobot/rate_shock.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/finrobot/rate_shock.py)
- [`examples/integrations/finrobot/rate_shock.ipynb`](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/finrobot/rate_shock.ipynb)

FinRobot is a project of the AI4Finance Foundation, licensed Apache-2.0. This
integration is maintained in this repository. AI4Finance has not endorsed it,
and it is not part of FinRobot's own interface.
