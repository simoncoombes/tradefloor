# tradefloor

[![determinism](https://github.com/simoncoombes/tradefloor/actions/workflows/determinism.yml/badge.svg)](https://github.com/simoncoombes/tradefloor/actions/workflows/determinism.yml)
[![PyPI](https://img.shields.io/pypi/v/tradefloor.svg)](https://pypi.org/project/tradefloor/)
[![crates.io](https://img.shields.io/crates/v/tradefloor.svg)](https://crates.io/crates/tradefloor)
[![license: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)
[![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

A market simulator you can run a strategy against, with a Rust core and a
Python API.

Give it a seed and a list of companies. It runs a market forward: prices, a
limit order book, fills, and an economy that moves each day. Your orders match
against real depth, so your trades move the price.

Real market data cannot tell you what happens if you trade differently, or
what caused a move, because nobody recorded the alternative. This simulator
computed every price, so it can.

## Documentation

The full documentation lives at **https://tradefloor.dev**: install,
core concepts, the realism envelope, presets, the API surface, and the
notebooks, whose source is maintained in a separate repository.

## Install

```
pip install tradefloor
```

> Formerly **pretium**. Versions through 0.4.3 were published under that
> name and remain installable forever; results recorded against them replay
> under those exact versions. The rename changed no behavior, and 0.5.0
> reproduces the same known-answer digest on every platform.

Wheels for Linux, macOS and Windows on CPython 3.11+. No dependencies. The same
engine is a Rust crate: `cargo add tradefloor`.

The API can still change before 1.0. A published result cannot change,
because new coefficients ship as a new preset, so a run you cited last month
replays this month.

## The demo

```
python examples/rate-shock/counterfactual.py
```

Run an agent in a controlled market, checkpoint the world and fork it, then
raise rates by 200bps in one branch and compare what the same agent does next.

The run changes one variable in the same world with the same agent, takes
two seconds, and needs no keys and no network. It prints the nine checks that
prove the two branches started identical, the step at which the agent's
behavior changed, and the side-by-side. The walkthrough is
[Your first counterfactual experiment](https://github.com/simoncoombes/tradefloor/blob/main/examples/rate-shock/README.md).

## First run

```python
import tradefloor as tf

universe = tf.Universe.random(40, seed=111)

spec = tf.StrategySpec.momentum(lookback_days=1.0, top_k=5)
scores = tf.evaluate({"mine": spec}, seed=7, universe=universe, days=10)

scores["mine"].return_pct            # what it made
scores["mine"].impact_bps            # what its own footprint cost
scores["mine"].strategy_fingerprint  # sha256, cite this
```

That is one market draw. It tells you as much about the seed as about the
strategy. `tf.rank` runs many seeds and compares them with a paired sign test.

## Contents

| | |
|---|---|
| `engine.truth()` | why each price moved: nine factors that sum to the move, to 1e-16 |
| `engine.prints()` | how each print was arrived at: the shock that arrived and the depth that absorbed it |
| counterfactual TCA | the same seed with your orders and without them |
| `tf.rank` | many seeds, paired sign tests |
| `RunManifest` | version, preset, seed, universe, macro, scenario. `reproduce()` stops on a mismatch |
| `World` / `compare` | fork a running experiment, change one variable, and measure where the two came apart |
| MCP server | twelve read-only tools for a coding agent, scenarios included |
| more | a Gymnasium environment, Arrow output, checkpoints, SEC EDGAR data, a browser build |

No historical dataset carries the labels `truth()` gives you. Real data shows
you that a stock fell. It never shows you that 60% of the fall was order flow.

To drive it from an agent:

```
pip install "tradefloor[mcp]"
claude mcp add tradefloor -- tradefloor-mcp
```

Strategies, universes and scenarios are data, so a tool argument cannot reach
code. Each result carries its own caveats. See
[the MCP page](https://simoncoombes.github.io/tradefloor/mcp.html).

## Controlled scenarios

```python
scenario = tf.Scenario.load("liquidity_crisis")   # ships with the package

control, stress = tf.branch(engine, 2)
for day in range(80):
    scenario.apply(stress, day)
    ...                                  # run both branches
```

A scenario is an explicit collection of market interventions and assumptions,
for controlled experiments rather than for forecasting. It names targets from
a registry of fields the engine actually reads, and it keeps what it asserts
happened apart from what it assumes happened next:

```
tradefloor scenario show scenarios/oil_price_spike.yml

Exogenous shocks
  day 50+            commodity.oil            x1.4

Assumed transmission
  day 55..74 ramp    macro.inflation          +1.50pp
  day 55+            macro.corporate_yield    +0.50pp
```

tradefloor does not claim what a war, an election, an oil shock or a
recession will do to markets. It lets you state those assumptions and measure
how an agent behaves under them.

Six scenarios ship inside the package, so `Scenario.load` works on a plain
`pip install`, and each one records what it was measured to be worth. Their
[source is here](https://github.com/simoncoombes/tradefloor/tree/main/python/tradefloor/scenarios).
`tradefloor scenario list` names them, and `tradefloor scenario targets`
lists every target and what it actually reaches.

## Realism

`tf.facts.measure()` scores fourteen shape statistics against real-market
bands, and four more on a protocol that varies the roster with the seed. The
default preset, `pt-v19`, holds all fourteen at one year and at two years, on
the certification roster and on a held-out one, at thirty seeds each, and all
fourteen at the real center where `pt-v18` held twelve.

<!-- DOCS-0.8.0 PLACEHOLDER. The sentence that stood here read: "It holds the
     other four as well: the index level returns +6.5 per cent a year inside a
     band of 2.9 to 11.9, and the -3 per cent fear row reads 5.8 against a
     tape centre of 5.7, where pt-v18 read 3.2 against a floor of 2.6."

     Two of those figures are on the record against a different preset. In
     tradefloor-design, programme/results/cert4b/certcomp-verdict.txt, the
     +6.5 (6.5177) and the 5.8 (5.8162) belong to the arm named
     cert-fourdial, which is pt-v18 plus four dials, not pt-v19. pt-v19's own
     readings on the same varying-roster protocol, from
     programme/results/b4fix2-scored.json, are index_drift_pct 5.5439 at 252
     and fear_gauge_dn3 7.6548, so the fear row is roughly 7.7 rather than
     5.8. The pt-v18 halves (3.2473, band floor 2.6) are correct.

     The paragraph above is also measured on the 2026-09-10 panel, run at
     engine daf20e6, before the last six dials moved. README.md and
     python/tradefloor/envelope.py have not been touched since. Restore this
     sentence from a panel of the shipped preset, and re-confirm the sentence
     above it at the same time. -->

One row reads further from real than before: the crisis lever is 5.28x
against real markets' 6.16x, where `pt-v18` read 6.53x.

Five of the fourteen were calibration targets, and the bands both tuned the
model and graded it. So this is a stated envelope, not a test against market
data that was held back.

Five limits are measured and written down:

| limit | what it means |
|---|---|
| horizon | one year is certified. Two and five years are measured, not certified |
| volatility memory | it decays too fast |
| scenario size | the response has the right sign, but one run cannot size it |
| macro crises | an inflation crisis or a policy crisis needs a scenario to drive it |
| roster | certification used a sector-balanced roster, which no real index is |

`tf.envelope.check()` refuses a question that falls outside a limit, and
[the realism envelope](https://simoncoombes.github.io/tradefloor/realism-envelope.html) says
what each one forbids.

Good results here do not predict real returns. The prices come from a known
model, so a strategy shaped like that model looks excellent and teaches you
nothing. A strategy that fails here tells you more. There is one venue, no
latency, and no counterparty that adapts to you.

## Seed determinism

Each release builds five targets, runs one fixed simulation in each, and
compares digests. A disagreement stops the release. The crate ships its own
`exp`, `log`, `sin` and `cos`, so the platform libm cannot change a result.

`pt-v19` became the default at 0.8.0, taking it from `pt-v18`. Naming your
preset explicitly makes a run replay exactly, and every preset from `pt-v1` on
is still selectable.

```python
eng = tf.Engine(seed=42, universe=u, model="pt-v10")
```

## Examples

Twelve numbered [`examples/`](https://github.com/simoncoombes/tradefloor/tree/main/examples) in reading order, run by the test suite:

| | |
|---|---|
| [`00-a-year-in-one-market`](https://github.com/simoncoombes/tradefloor/blob/main/examples/00-a-year-in-one-market.ipynb) | Start here: one company, one year, two crises, one chart |
| [`01-first-simulation`](https://github.com/simoncoombes/tradefloor/blob/main/examples/01-first-simulation.ipynb) | Universe, engine, order book, determinism |
| [`02-evaluating-a-strategy`](https://github.com/simoncoombes/tradefloor/blob/main/examples/02-evaluating-a-strategy.ipynb) | Specs, baselines, ranking across seeds |
| [`03-why-did-the-price-move`](https://github.com/simoncoombes/tradefloor/blob/main/examples/03-why-did-the-price-move.ipynb) | The nine factors that sum to every move |
| [`04-how-realistic-is-this`](https://github.com/simoncoombes/tradefloor/blob/main/examples/04-how-realistic-is-this.ipynb) | The realism panel and the limits |
| [`05-training-an-agent`](https://github.com/simoncoombes/tradefloor/blob/main/examples/05-training-an-agent.ipynb) | The Gymnasium environment, and what size costs |
| [`06-execution-and-impact`](https://github.com/simoncoombes/tradefloor/blob/main/examples/06-execution-and-impact.ipynb) | TCA and the counterfactual run |
| [`07-research-workflow.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/07-research-workflow.py) | A whole study in one file. It runs in about five seconds |
| [`08-claude-agent.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/08-claude-agent.py) | An LLM agent trading the market through the harness |
| [`09-a-pandemic-shaped-market`](https://github.com/simoncoombes/tradefloor/blob/main/examples/09-a-pandemic-shaped-market.ipynb) | A real 2020-21 macro path, and which fields transmit |
| [`10-forking-a-market`](https://github.com/simoncoombes/tradefloor/blob/main/examples/10-forking-a-market.py) | Fork a market, raise the rate in one branch, and compare the futures |
| [`11-scenario-fork.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/11-scenario-fork.py) | A scenario file applied to one branch of a fork, and what it cost |

Beside them, one directory per self-contained study. Each asks one question,
and they come in no particular order.

| | |
|---|---|
| [`rate-shock/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/rate-shock) | The canonical demo: checkpoint, fork, +200bps in one branch, compare |
| [`finrobot/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/integrations/finrobot) | The same experiment with a real FinRobot agent |

## Agent frameworks

The framework decides, and tradefloor runs the market it decides in. Each
adapter under `tradefloor.integrations` carries one framework's output
through the same loop of observation, decision, execution and evaluation, so
two frameworks can be measured on one market through one harness.

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

Each example runs offline in seconds, with a deterministic function standing
where a model would sit, so reading one costs no API key and no provider
account. The market replays from a seed; a live model call does not, so an
adapter records each exchange keyed by a digest of the exact input it sent
and replays that recording later with the framework uninstalled. The four
sit together in
[`examples/integrations/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/integrations),
which sets out what each framework contributes and what tradefloor keeps.

## FinRobot integration

Evaluate a [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot)
financial AI agent inside a controlled tradefloor market. Run shared history,
checkpoint the world, fork it, raise rates by 200bps in one branch, and compare
how the same agent responds. The canonical rate-shock experiment, with the
agent swapped and nothing else moved.

```bash
pip install "tradefloor[finrobot]"
python examples/integrations/finrobot/rate_shock.py            # replays a real recorded run
python examples/integrations/finrobot/rate_shock.py --live     # calls FinRobot
```

The default run replays a genuine recorded FinRobot run and needs no API key,
no network and no FinRobot install.

- [`examples/integrations/finrobot/rate_shock.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/finrobot/rate_shock.py)
- [`examples/integrations/finrobot/rate_shock.ipynb`](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/finrobot/rate_shock.ipynb)

FinRobot is a project of the AI4Finance Foundation, licensed Apache-2.0. The
notebooks above are a tradefloor integration for FinRobot, maintained in this
repository without endorsement from AI4Finance, and they form no part of
FinRobot's own interface.

## More

Full docs: [**tradefloor.dev**](https://tradefloor.dev/).

To contribute, see [CONTRIBUTING.md](https://github.com/simoncoombes/tradefloor/blob/main/CONTRIBUTING.md) and [RELEASING.md](https://github.com/simoncoombes/tradefloor/blob/main/RELEASING.md). One rule shapes the
rest: a change to the simulated trajectory is a breaking change, whatever its
size.

To cite the software, see [CITATION.cff](https://github.com/simoncoombes/tradefloor/blob/main/CITATION.cff). To cite a result, use its
`RunManifest`.

## License

MIT OR Apache-2.0, at your option, which is the Rust-ecosystem norm. See
[LICENSE-MIT](https://github.com/simoncoombes/tradefloor/blob/main/LICENSE-MIT)
and
[LICENSE-APACHE](https://github.com/simoncoombes/tradefloor/blob/main/LICENSE-APACHE).

GitHub's sidebar reads Apache-2.0 because its detection picks one file and
stops. So does the sidebar of `rust-lang/rust`, `serde` and `pyo3`, which
carry the same two files. The grant that applies is the dual one, stated in
`pyproject.toml`, `rust/Cargo.toml` and this section.
