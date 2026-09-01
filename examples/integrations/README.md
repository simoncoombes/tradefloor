# Agent frameworks in a controlled market

**Tradefloor holds no opinion about which agent framework you use.** An
adapter carries somebody else's runtime into one loop: an allowlisted
observation goes out, a decision comes back, Tradefloor executes it against a
live order book and scores what happened.

```
                    OBSERVATION        allowlisted, JSON-able
                         |
                    YOUR FRAMEWORK     function, SDK, graph
                         |
                      DECISION         symbol, side, quantity
                         |
                     EXECUTION         real depth, real impact
                         |
                    EVALUATION         scorecard, record, transcript
```

The market, the macro path, execution, the book, fills, accounting,
checkpoints, forks and the comparison belong to Tradefloor. Interpretation
and the portfolio decision belong to the framework. Nothing in between
changes when the framework does, so two frameworks measured on one seed
differ by the agent and not by the harness.

| | |
|---|---|
| [`callable/five_days.py`](callable/five_days.py) | A plain Python function |
| [`openai_agents/five_days.py`](openai_agents/five_days.py) | The OpenAI Agents SDK |
| [`pydantic_ai/rate_shock.py`](pydantic_ai/rate_shock.py) | A PydanticAI agent |
| [`langgraph/rate_shock.py`](langgraph/rate_shock.py) | A compiled LangGraph graph |
| [`finrobot/`](finrobot/) | FinRobot, in the rate-shock study |
| [`../../python/tradefloor/integrations/`](../../python/tradefloor/integrations/) | The adapters themselves, and the shared layer they sit on |

## Running them

```bash
python examples/integrations/callable/five_days.py
python examples/integrations/openai_agents/five_days.py
python examples/integrations/pydantic_ai/rate_shock.py
python examples/integrations/langgraph/rate_shock.py
```

Each finishes in a few seconds with no API key, no provider account and no
network. The three framework examples drive the real framework, with a
deterministic function standing where a model would sit, so what runs offline
is the adapter and the framework and not a mock of either.

All four have the same shape with one part swapped: build a market, ask the
agent once a simulated day for five days, print the scorecard and the
decision record. The rule is the same five-day mean-reversion rule in every
one, so the part that changes is who reads the payload and answers.

The market is where they diverge, deliberately. Each example sizes its own
book to show something its own section explains, and the LangGraph one runs
a duration-laddered roster it shares with the FinRobot study instead of the
roster the other three use. The scorecards differ accordingly, and a
difference between two rows here is a fact about two markets and not about
two adapters:

| example | trades | return | impact |
|---|---|---|---|
| [`callable/five_days.py`](callable/five_days.py) | 3 | +0.51% | +3.96 bps |
| [`openai_agents/five_days.py`](openai_agents/five_days.py) | 3 | +0.51% | +3.96 bps |
| [`pydantic_ai/rate_shock.py`](pydantic_ai/rate_shock.py) | 3 | +2.32% | +11.70 bps |
| [`langgraph/rate_shock.py`](langgraph/rate_shock.py) | 2 | +0.73% | -5.67 bps |

Comparing two frameworks means holding the market fixed, which is what the
shared contract checks in `tests/test_integrations.py` do.
`tests/test_integration_examples.py` runs all four examples and asserts the
table above against what they print, since a table nothing executes is a
table that goes stale.

## Plain Python

`tradefloor.integrations.callable` needs no extra, since it ships
inside the package.

```python
import tradefloor as tf
from tradefloor.integrations.callable import callable_agent

def rule(payload: dict) -> dict:
    return {"actions": [...], "rationale": "five-day mean reversion"}

agent = callable_agent(rule)
scores = tf.evaluate({"mine": agent}, seed=4242, universe=roster, days=5)
```

Fuller example: [`callable/five_days.py`](callable/five_days.py).

`rule` is handed the serialized payload and never the `Observation`. The
Observation carries `.engine`, which holds the answer key: fair value, the
nine-way attribution of every price move, each company's mispricing, and the
macro path the run has not reached yet. A function given that would step
around the allowlist where no test could see it. A policy that genuinely
needs the Observation is a native Tradefloor agent and implements `act`
directly.

An async function works too, driven through `common.run_sync`.

## OpenAI Agents SDK

```bash
pip install "tradefloor[openai-agents]"      # openai-agents >= 0.22
```

```python
from agents import Agent
from tradefloor.integrations.common import Transcript
from tradefloor.integrations.openai_agents import OpenAIAgentsAdapter

pm = Agent(name="Portfolio Manager",
           instructions="You are a disciplined value investor.",
           model="gpt-5.6-luna")

agent = OpenAIAgentsAdapter(pm, mode="live", recorder=Transcript())
scores = tf.evaluate({"pm": agent}, seed=4242, universe=roster, days=5)
```

Fuller example: [`openai_agents/five_days.py`](openai_agents/five_days.py), which
runs against `agents.testing.ScriptedModel` and then replays itself from the
transcript it just recorded.

The adapter calls `Runner.run`, the async entry point, through the shared
bridge. `Runner.run_sync` raises a bare `RuntimeError` inside a running event
loop, measured on 0.22.0, so an adapter built on it would work in a script
and die in every notebook. `max_turns` bounds one decision at six model
calls, short of the SDK's own default of ten, which was chosen for
interactive use and not for a loop that runs at every cadence step of every
arm. On a malformed answer the SDK makes one call and raises
`ModelBehaviorError` with no client-side retry, so size error handling
assuming no retry exists.

Your agent is cloned and never mutated. The adapter binds the shared decision
contract as the output type on `Agent.clone(...)`, so instructions, tools,
model settings, hooks, handoffs and guardrails all survive and all still run
inside the decision. An agent already declaring its own `output_type` is
refused with a message naming the way to opt in, and a run that ends on a
different agent through a handoff is refused by name, since that agent
carries an output type of its own.

## PydanticAI

```bash
pip install "tradefloor[pydantic-ai]"        # pydantic-ai-slim >= 2.36
```

```python
from tradefloor.integrations.pydantic_ai import PydanticAIAdapter

agent = PydanticAIAdapter(my_agent, deps=my_deps)
scores = tf.evaluate({"pm": agent}, seed=4242, universe=roster, days=5)
```

Fuller example: [`pydantic_ai/rate_shock.py`](pydantic_ai/rate_shock.py), driven by a
`FunctionModel`.

Your agent arrives already built and leaves unmodified. Its `deps_type`, its
tools, its instructions and its output validators all still run, and `deps`
reaches `run(deps=...)` verbatim. An offline model goes in the adapter's
`model=` argument instead of through `Agent.override`, because `override` is
built on context variables and the shared async bridge crosses a thread
boundary those do not cross. PydanticAI retries a malformed answer inside the
turn, which is where its behaviour differs from the Agents SDK by enough for
an error budget to notice.

One limitation follows from leaving `deps` alone. PydanticAI has a single
dependency slot, read by every tool through `RunContext.deps`, and the
adapter puts nothing in it, so a tool of yours cannot query the observation
from inside a decision. A tool that needs the day's prices reads them from a
holder you put on your own deps object and fill yourself.

The extra installs `pydantic-ai-slim`, which provides the `pydantic_ai`
module without the provider SDKs the umbrella package adds: openai,
anthropic, google-genai, groq, mistral, cohere and boto3, none of which any
adapter imports. `TestModel` and `FunctionModel` are both in slim.

## LangGraph

```bash
pip install "tradefloor[langgraph]"          # langgraph >= 1.2
```

```python
from tradefloor.integrations.langgraph import LangGraphAdapter

graph = builder.compile()
agent = LangGraphAdapter(graph)
scores = tf.evaluate({"graph": agent}, seed=4242, universe=roster, days=5)
```

Fuller example: [`langgraph/rate_shock.py`](langgraph/rate_shock.py), a two-node
`StateGraph` with its own state schema and a reducer.

A graph returns its whole state, so unwrapping it is the adapter's job. The
default output parser reads a `Decision`, a mapping carrying `actions`, a
mapping carrying `decision`, or the `MessagesState` shape, and refuses
anything else by name. Returning an empty decision for a state it cannot read
would score a plumbing failure as an agent that considered the market and
declined, and nothing in a scorecard afterwards would tell the two apart.
Pass the compiled graph: a `StateGraph` builder has no `invoke`, and the
adapter refuses it by name.

An interrupt raises `GraphInterruptedError`, because a market loop has
nowhere to resume into once the book has moved on. Measured on 1.2.11, a
`GraphInterrupt` never escapes `invoke` and arrives inside the state as
`__interrupt__`, so the parser checks for it ahead of `decision` and names
the question that went unanswered. Without that check an interrupted graph
would be misdiagnosed as plumbing, and a checkpointed thread could answer
this step with a decision written on an earlier one.

Graph nodes can be plain Python functions, which makes a complete LangGraph
program with no model in it. That is what the example is, and it is why the
example runs offline.

Two things here are called a checkpoint. A LangGraph checkpoint is workflow
state, holding the graph's channel values and which node runs next, and it
was measured to carry no engine, no order book, no prices and no RNG. A
Tradefloor checkpoint is simulated market state: prices, the book, the macro
path, the variance process and the RNG. Neither reconstructs the other and
both directions fail quietly, so keep the pair together by run.

## FinRobot

FinRobot has its own integration in this repository and its own study. It
came first, and the shared layer every adapter above sits on was derived from
it.

```bash
pip install "tradefloor[finrobot]"           # Python 3.11 exactly
python examples/integrations/finrobot/rate_shock.py       # replays a recorded run
```

Its page is [`examples/integrations/finrobot/`](finrobot/): twenty days of shared
history, a checkpoint, a fork proved identical field by field, +200bps in one
branch, and a comparison of what the same agent did next. The default run
replays a genuine recorded FinRobot run and needs no API key, no network and
no FinRobot install.

`tradefloor[finrobot]` is the largest extra here by an order of magnitude and
pins Python 3.11 exactly, since FinRobot declares `>=3.10, <3.12` and
Tradefloor needs `>=3.11`.

## Tracing

Every adapter leaves its framework's own tracing off, and none of them turns
it on. The OpenAI Agents SDK ships tracing enabled and exporting to OpenAI,
so the adapter passes `tracing_disabled=True` on every run unless
`tracing=True` was asked for, and it sets that per run instead of through the
SDK's process-global switch, so it never changes tracing for other code
sharing the process. LangSmith stays off unless one of its environment
variables reads `true`, and PydanticAI instruments nothing until its own
instrumentation is switched on.

Turning it on works, and Tradefloor's identity comes with it. A LangGraph run
traced to LangSmith was measured end to end: two decisions produced twelve
exported runs, and all twelve carried `tradefloor_run_id`. The caller had
supplied four keys through `config={"metadata": {...}}`, and the adapter
added `tradefloor_arm`, `tradefloor_day`, `tradefloor_step` and
`tradefloor_decision_schema` per decision, so a span traces back to the exact
decision point with nobody wiring that up. Use `metadata` for your own keys,
since `run_name` is consumed by the tracer and never reaches a node.

A PydanticAI agent instruments through `logfire.configure()` with
`logfire.instrument_pydantic_ai()`, or through `Agent.instrument_all()`.
There is no `instrument=` constructor argument in 2.36.0. Measured with
Logfire: instrumentation activates, the run completes unaffected, and the
spans export with a clean `force_flush()`. Arrival in the Logfire backend was
left unchecked, since reading it back needs a read token, so what is
supported here is that enabling Logfire works and disturbs nothing.

Tracing sends the rendered observation to whichever provider is switched on.

## Reproducibility

Tradefloor is deterministic given the same simulator configuration and the
same sequence of agent actions. Same seed, same universe, same preset, same
scenario, same decisions: the same market, to the last bit, on every
supported platform.

A model call sits outside that guarantee. A seed reproduces the market an
agent was shown; it has no purchase at all on what a language model chose to
do about it. Nothing here claims otherwise, and a published result citing an
LLM agent needs the framework, the model, the provider and the generation
parameters recorded beside the seed. `adapter.provenance()` is that record,
and it belongs in `Transcript.meta` and in the run manifest.

Recorded decisions do replay exactly. `common.Transcript` stores each
exchange keyed by a digest of the exact input the framework was sent, and
replay returns exactly what was recorded, with no framework imported, no key
and no network reached. An experiment that cost real money to record is
therefore reproducible by anyone, for nothing.

The key derives from the input and never from a step number. Change the
roster, the seed, the cadence or the instructions and the key goes missing:
the run stops and names the step it stopped at. Keyed by position, a replay
would answer the new question with the answer given to the old one, and
nothing in the output would say so.

## When a live run goes wrong

Two things end a long live run, and they end it differently.

An agent can return output that is not an executable decision. `parse` is
strict on purpose -- dropping an unknown field executes a trade the agent
conditioned on something it never got -- so it raises `DecisionError`, and
by default that ends the run. Measured on a 60-decision pilot, one response
in 35 was malformed, and the one that arrived on call 36 took 35 recorded
interactions and 20 days of shared history with it.

```python
world = World(seed=42, universe=roster, agent=agent, on_refusal="skip")
```

Under `"skip"` that step trades nothing, the refusal is recorded, and the
run continues. The count is in the trace, in `World.summary()` and in a
`Comparison` row, as `unusable_responses`. It is kept apart from `refused`,
which counts orders the MARKET rejected: those are different failures with
different remedies, and one column covering both would make an unusable
agent read as an illiquid market. `"raise"` remains the default.

The other way a run dies is outside the agent: a rate limit, a dropped
connection, an interrupt. Hand the next attempt what the last one paid for.

```python
agent = OpenAIAgentsAdapter(pm, mode="live", recorder=Transcript(),
                            prior=Transcript.load("journal.json"))
```

`prior` is consulted before the provider, on the same key the replay path
uses. The market is deterministic, so the resumed run reaches the same
prompts and computes the same digests; a hit replays, a miss calls out. The
new recording carries `replayed_from_prior` and `called_live` in its meta,
so a file stitched from two sessions cannot read as one live run. A `prior`
recorded under different instructions is refused before the run starts, for
the reason the replay guard exists: instructions do not travel in the input
the key is computed over, so every key would still match and nothing in the
output would say the question had changed.

## The decision boundary

A framework returns a decision and never touches engine state.

```json
{"actions": [{"symbol": "TECH_A", "side": "BUY", "quantity": 1200}],
 "rationale": "one line, for the record"}
```

`parse_decision` checks that against the shared schema, and `orders_from`
checks what survives against this market: every symbol against the listed
universe, every side against BUY, SELL and HOLD, and the size against the
participation cap, which clips and records the clip. A well-formed decision
this market cannot take raises `MarketRefusalError`.

Unknown keys are refused by name, at the top level and on an action. A
silently dropped `stop_loss` would leave an agent believing it has protection
this market cannot give. There are no order types and no limit prices at this
boundary either: `Portfolio.execute` sweeps the live book with a signed
quantity, so an `order_type` other than `market`, or any `limit_price`, is
refused with a message naming the capability that is missing.

A mapping carrying no `actions` key is refused instead of being read as a
hold. An unwrapped framework envelope would otherwise score as `trades=0`
with an empty error list, which no scorecard can tell apart from an agent
that looked at the market and declined.

---

Each adapter targets a project this repository neither owns nor is affiliated
with. The module docstrings name the upstream project, its licence and the
version tested. None of those projects endorses this work.

Everything these examples report is ground truth about a simulated market.
Treat it as a controlled synthetic experiment. It predicts nothing about how
real securities would behave.
