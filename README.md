# tradefloor

[![determinism](https://github.com/simoncoombes/tradefloor/actions/workflows/determinism.yml/badge.svg)](https://github.com/simoncoombes/tradefloor/actions/workflows/determinism.yml)
[![PyPI](https://img.shields.io/pypi/v/tradefloor.svg)](https://pypi.org/project/tradefloor/)
[![crates.io](https://img.shields.io/crates/v/tradefloor.svg)](https://crates.io/crates/tradefloor)
[![license: MIT OR Apache-2.0](https://img.shields.io/badge/license-MIT%20OR%20Apache--2.0-blue.svg)](#license)
[![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

tradefloor is a market simulator you can run a strategy against. It has a
Rust core and a Python API.

Give it a seed and a list of companies. It runs a market forward: prices, a
limit order book, fills, and an economy that moves each day. Your orders match
against the book's depth, so your trades move the price.

Real market data can't tell you what would have happened if you had traded
differently, or what caused a move. tradefloor can, because it computed every
price.

## Documentation

Documentation is at https://tradefloor.dev. It covers install, core
concepts, the realism envelope, presets, the API and the notebooks.

## Install

```
pip install tradefloor
```

> tradefloor was called **pretium** until 0.5.0. Versions through 0.4.3 were
> published under that name and are still installable, and results recorded
> against them replay under those versions. The rename changed no behavior,
> and 0.5.0 reproduces the same known-answer digest on every platform.

There are wheels for Linux, macOS and Windows on CPython 3.11+, and no
dependencies. The same engine is a Rust crate (`cargo add tradefloor`).

The API can still change before 1.0, but published results can't. New
coefficients ship as a new preset, so a run you cited last month replays the
same way this month.

## The demo

```
python examples/rate-shock/counterfactual.py
```

Run an agent in a controlled market, checkpoint the world and fork it, then
raise rates by 200bps in one branch and compare what the same agent does next.

The run takes about two seconds and needs no keys and no network. It prints
the nine checks that show the two branches started identical, the step at
which the agent's behavior changed, and the two branches side by side. The
walkthrough is
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
| `engine.truth()` | why each price moved: ten factors that sum to the move, to 1e-16 |
| `engine.prints()` | how each print was arrived at: the shock that arrived and the depth that absorbed it |
| counterfactual TCA | the same seed with your orders and without them |
| `tf.rank` | many seeds, paired sign tests |
| `RunManifest` | version, preset, seed, universe, macro, scenario. `reproduce()` stops on a mismatch |
| `World` / `compare` | fork a running experiment, change one variable, and measure where the two came apart |
| MCP server | twelve read-only tools for a coding agent, scenarios included |
| more | a Gymnasium environment, Arrow output, checkpoints, SEC EDGAR data, a browser build |

Historical data shows that a stock fell. `truth()` also says why, for example
that 60% of the fall was order flow, and no historical dataset carries that
label.

To drive it from an agent:

```
pip install "tradefloor[mcp]"
claude mcp add tradefloor -- tradefloor-mcp
```

Strategies, universes and scenarios are data, so a tool argument cannot reach
code. Each result carries its own caveats. See
[the MCP page](https://tradefloor.dev/mcp.html).

## Controlled scenarios

```python
scenario = tf.Scenario.load("liquidity_crisis")   # ships with the package

control, stress = tf.branch(engine, 2)
for day in range(80):
    scenario.apply(stress, day)
    ...                                  # run both branches
```

A scenario is a list of market interventions and the assumptions behind
them, written for controlled experiments. It names targets from a registry of
fields the engine reads, and it keeps the shocks it says happened apart from
the transmission it assumes followed:

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

Six scenarios ship inside the package, so `Scenario.load` works after a plain
`pip install`, and each records what it was measured to be worth. Their
[source is here](https://github.com/simoncoombes/tradefloor/tree/main/python/tradefloor/scenarios).
`tradefloor scenario list` names them, and `tradefloor scenario targets`
lists every target and what each one reaches.

## Realism

`tf.facts.measure()` scores fourteen shape statistics against real-market
bands, a fifteenth that reads only in a crisis, and four more on a protocol
that varies the roster with the seed. At the default preset, `pt-v19`, all of
them are in band in all four cells of the record: the certification roster
and a held-out one, at one year and two, thirty seeds each. The row furthest
from real is `sector_excess_corr`, 0.090 against a tape center of 0.164,
which is still in band.

On the four level rows, the index drifts +7.6 percent over one-year windows,
inside a band of 1.1 to 10.3. The -1 percent fear row reads 1.9 against a
tape center of 2.7, and the -3 percent row reads 5.4 against 5.7. The index
falls more than 3 percent on 1.14 percent of sessions, against 1.21 percent
in real markets.

pt-v19 also passes a long-run check. Thirty 21-year histories, plus the 2008
and 2020 crises replayed with the real VIX imposed, are graded on fifteen
criteria a user would notice. They cover how deep crashes go, how long fear
lasts, how often the VIX sits above 30 or below 15, bear markets and
corrections per decade, the long-run return, and whether a headline read
late still pays. pt-v19 meets all fifteen, and pt-v18, the previous default,
meets eight. The 2008 replay falls 41 percent against the real 57, the VIX
spends 8.1 percent of sessions above 30 against a real 8.2, and the index
returns 6.3 percent a year over 21 years. The verdict ships in the preset
record as `tf.preset_record()["long_run"]`.

Three measurements are still off. The worst month of the 2020 replay is about
30 percent milder than the real one. Over two years the VIX forgets a shock a
little too fast. The crisis lever, the ratio of volatility at a held VIX of 65
to volatility at a held VIX of 5, reads 5.2x against 6.2x in real markets.

Each crisis is centered on one sector, drawn at random, and a scenario can
pin it with `Scenario().hold(epicentre="financial_services")`.

Five of the fourteen shape statistics were calibration targets, so the same
bands tuned the model and then graded it, and no market data was held back
for the test.

Five limits are measured and written down:

| limit | what it means |
|---|---|
| horizon | one year is certified against the bands. Longer runs are graded only by the long-run check |
| volatility memory | it decays too fast |
| scenario size | the response has the right sign, but one run cannot size it |
| macro crises | an inflation crisis or a policy crisis needs a scenario to drive it |
| roster | certification used a sector-balanced roster, which no real index is |

`tf.envelope.check()` refuses a question that falls outside a limit, and
[the realism envelope](https://tradefloor.dev/realism-envelope.html) says
what each one forbids.

Good results here do not predict real returns. The prices come from a known
model, so a strategy that happens to match that model will score well here
and may fail on real data. The market has one venue, no latency, and no
counterparty that adapts to you.

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

The twelve numbered [`examples/`](https://github.com/simoncoombes/tradefloor/tree/main/examples) are in reading order, and the test suite runs them:

| | |
|---|---|
| [`00-a-year-in-one-market`](https://github.com/simoncoombes/tradefloor/blob/main/examples/00-a-year-in-one-market.ipynb) | Start here: one company, one year, two crises, one chart |
| [`01-first-simulation`](https://github.com/simoncoombes/tradefloor/blob/main/examples/01-first-simulation.ipynb) | Universe, engine, order book, determinism |
| [`02-evaluating-a-strategy`](https://github.com/simoncoombes/tradefloor/blob/main/examples/02-evaluating-a-strategy.ipynb) | Specs, baselines, ranking across seeds |
| [`03-why-did-the-price-move`](https://github.com/simoncoombes/tradefloor/blob/main/examples/03-why-did-the-price-move.ipynb) | The ten factors that sum to every move |
| [`04-how-realistic-is-this`](https://github.com/simoncoombes/tradefloor/blob/main/examples/04-how-realistic-is-this.ipynb) | The realism panel and the limits |
| [`05-training-an-agent`](https://github.com/simoncoombes/tradefloor/blob/main/examples/05-training-an-agent.ipynb) | The Gymnasium environment, and what size costs |
| [`06-execution-and-impact`](https://github.com/simoncoombes/tradefloor/blob/main/examples/06-execution-and-impact.ipynb) | TCA and the counterfactual run |
| [`07-research-workflow.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/07-research-workflow.py) | A whole study in one file. It runs in ten to twenty seconds and needs `tradefloor[arrow]` |
| [`08-claude-agent.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/08-claude-agent.py) | An LLM agent trading the market through the harness |
| [`09-a-pandemic-shaped-market`](https://github.com/simoncoombes/tradefloor/blob/main/examples/09-a-pandemic-shaped-market.ipynb) | A real 2020-21 macro path, and which fields transmit. Pinned to `pt-v12`, whose QE channel the repair uses, with the same path on `pt-v19` at the end |
| [`10-forking-a-market`](https://github.com/simoncoombes/tradefloor/blob/main/examples/10-forking-a-market.py) | Fork a market, raise the rate in one branch, and compare the futures |
| [`11-scenario-fork.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/11-scenario-fork.py) | A scenario file applied to one branch of a fork, and what it cost |

Next to them, each self-contained study has its own directory and asks one
question. They are in no particular order.

| | |
|---|---|
| [`rate-shock/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/rate-shock) | The demo above: checkpoint, fork, +200bps in one branch, compare |
| [`finrobot/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/integrations/finrobot) | The same experiment with a real FinRobot agent |

## Agent frameworks

The framework makes the decisions and tradefloor runs the market. Each
adapter under `tradefloor.integrations` passes one framework's output through
the same loop of observation, decision, execution and evaluation, so two
frameworks can be measured on the same market with the same harness.

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

Each example runs offline in seconds, with a deterministic function in place
of the model, so running one needs no API key or provider account. The market
replays from a seed but a live model call does not, so an adapter records
each exchange, keyed by a digest of the exact input it sent, and can replay
the recording later without the framework installed. The four examples are
in
[`examples/integrations/`](https://github.com/simoncoombes/tradefloor/tree/main/examples/integrations),
which says what each framework contributes and what tradefloor keeps.

## FinRobot integration

This runs a [FinRobot](https://github.com/AI4Finance-Foundation/FinRobot)
agent in a controlled tradefloor market. It runs a shared history, checkpoints
the world, forks it, raises rates by 200bps in one branch, and compares how the
same agent responds. It is the rate-shock demo above with the agent swapped and
nothing else changed.

```bash
pip install "tradefloor[finrobot]"
python examples/integrations/finrobot/rate_shock.py            # replays a real recorded run
python examples/integrations/finrobot/rate_shock.py --live     # calls FinRobot
```

By default it replays a recorded FinRobot run, which needs no API key, no
network and no FinRobot install.

- [`examples/integrations/finrobot/rate_shock.py`](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/finrobot/rate_shock.py)
- [`examples/integrations/finrobot/rate_shock.ipynb`](https://github.com/simoncoombes/tradefloor/blob/main/examples/integrations/finrobot/rate_shock.ipynb)

FinRobot is a project of the AI4Finance Foundation, licensed Apache-2.0. This
integration is maintained in this repository. AI4Finance has not endorsed it,
and it is not part of FinRobot's own interface.

## More

To contribute, see [CONTRIBUTING.md](https://github.com/simoncoombes/tradefloor/blob/main/CONTRIBUTING.md) and [RELEASING.md](https://github.com/simoncoombes/tradefloor/blob/main/RELEASING.md). The main rule
is that any change to the simulated trajectory is a breaking change, however
small.

To cite the software, see [CITATION.cff](https://github.com/simoncoombes/tradefloor/blob/main/CITATION.cff). To cite a result, use its
`RunManifest`.

## License

tradefloor is licensed under MIT OR Apache-2.0, at your option, which is the
usual choice for Rust crates. See
[LICENSE-MIT](https://github.com/simoncoombes/tradefloor/blob/main/LICENSE-MIT)
and
[LICENSE-APACHE](https://github.com/simoncoombes/tradefloor/blob/main/LICENSE-APACHE).

GitHub's sidebar reads Apache-2.0 because its detection picks one file and
stops. So does the sidebar of `rust-lang/rust`, `serde` and `pyo3`, which
carry the same two files. The grant that applies is the dual one, stated in
`pyproject.toml`, `rust/Cargo.toml` and this section.
