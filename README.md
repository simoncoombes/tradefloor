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

Three documents in this repository are for anyone publishing with it:

- [docs/MODEL.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/MODEL.md):
  the model as equations, with every coefficient's value on the default
  preset and where it came from
- [docs/STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md):
  the named sets of realism statistics, so a count on one page can be
  matched to a count on another
- [docs/SUPPORT.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/SUPPORT.md):
  which release lines get fixes, and for how long

## Install

```
pip install tradefloor
```

> tradefloor was called **pretium** until 0.5.0. Versions up to 0.4.3 can
> still be installed under that name, and results recorded with them still
> replay. The rename changed nothing in the model.

There are wheels for Linux, macOS and Windows on CPython 3.11+, and no
dependencies. The same engine is a Rust crate (`cargo add tradefloor`).

The API may change before 1.0. Results will not, because model changes ship
as a new preset, so a run you cited last month replays the same way this
month.

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

That result comes from one random market, so it says as much about the seed
as about the strategy. `tf.rank` runs many seeds and compares strategies with
a paired sign test.

## The agent's view

A Python agent implements `act(obs)` and returns orders. `obs.engine` is a
read-only market view: prices, the public columns, each book, bars, the
published macro fields, the curve and which names have news today.
`obs.portfolio` reads the agent's own positions and cannot trade. Forking the
engine, writing to it and reading the hidden state all raise
`tf.SandboxError`. The hidden state includes the true business-cycle phase;
the macro fields carry the phase as published. The gym environment's `env.engine` and
`env.portfolio` are the same views.

The Oracle reads hidden state by declaring `privileged = True`, which gives
it `obs.hidden` and marks its scorecard. Pass `trusted_agents=True` for
research that needs the live engine, and every scorecard says so. Either way
the harness compares the engine's state hash around each call, and an agent
that changed the market is scored `tampered` and left out of `tf.rank`.
[`tradefloor/sandbox.py`](https://github.com/simoncoombes/tradefloor/blob/main/python/tradefloor/sandbox.py)
lists what the view serves and what the check cannot catch.

## Contents

| | |
|---|---|
| `engine.truth()` | why each price moved: eleven factors that sum to the mispricing's move, to 1e-16 |
| `engine.prints()` | how each trade price came about: the shock, and the order book depth that absorbed it |
| counterfactual TCA | your trading cost, from the same seed run with your orders and without them |
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

A scenario is a file of changes to the market and the assumptions behind
them, for controlled experiments. Each change targets a field the engine
reads. The file keeps the shock apart from the knock-on effects you assume
follow it:

```
tradefloor scenario show scenarios/oil_price_spike.yml

Exogenous shocks
  day 50+            commodity.oil            x1.4

Assumed transmission
  day 55..74 ramp    macro.inflation          +1.50pp
  day 55+            macro.corporate_yield    +0.50pp
```

tradefloor does not predict what a war, an election, an oil shock or a
recession will do to markets. You state the assumptions and it measures how an
agent behaves under them.

Seven scenarios ship with the package, so `Scenario.load` works after a plain
`pip install`. Each one records how big its effect was measured to be. Their
[source is here](https://github.com/simoncoombes/tradefloor/tree/main/python/tradefloor/scenarios).
`tradefloor scenario list` names them, and `tradefloor scenario targets`
lists every target and what each one reaches.

### Bonds

`Universe.random(40, seed=1, bonds=True)` adds three simulated rate indices
after the equities: `UST2Y` and `UST10Y`, constant-maturity 2-year and
10-year treasury indices, and `IGCORP`, an investment-grade corporate bond
index. They are not real securities. Each is priced off the engine's own
curve, returning `yield / 252 - D * dy + 0.5 * C * dy**2` a day, with
duration and convexity of 1.9 and 4.6, 8.5 and 84, and 7.0 and 100. They
trade through the same books, fills, portfolio and tape as the equities, and
they leave every equity price unchanged. `scenarios/curve_shock.yml` moves
the whole curve 200 basis points in one day, which takes about 15% off
`UST10Y`; `tradefloor.baselines.Balanced` is a 60/40 portfolio with a drift
band. `evaluate(..., cash_interest=True)` pays uninvested cash the policy
rate, and is off by default.

## Realism

tradefloor checks its market against real ones with three named sets of
statistics. [docs/STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md)
lists every member of each, and the counts below refer to them.

**The one-year table** has 19 statistics of a simulated market, such as
volatility, fat tails, how much stocks move together and how far the VIX
jumps after a fall. `tf.facts.measure()` reads 18 of them over 252 days, and
`tf.facts.crisis_statistics()` the nineteenth, which needs a run with a
crisis in it. `tf.envelope.score()` compares each with the range real
markets show over a year. On the default preset, `pt-v20`, all 19 are inside
their ranges. The check runs 30 random seeds. Fifteen of the statistics are read on one fixed
set of companies, and that fixed-roster panel is repeated on held-out seeds
and on a held-out set of companies.

Four of the 19 describe the index as a whole. An equal-weight index of the
stocks gains 1.1 percent a year over one year, at the floor of a real range
of 1.1 to 10.3 (pt-v19 gained 7.6). On a day the index falls 1 percent or
more, the VIX rises a median 1.7 points, inside a real range of 0.39 to
3.03. On a 3 percent fall it rises 5.1, inside 2.6 to 9.58. The index falls
3 percent or more on 0.82 percent of days, against 1.21 percent in real
markets.

**The two-year panel** is the fixed-roster panel run for 504 days. Fourteen
of its 15 statistics have a two-year range, and pt-v20 has all 14 inside.

**The long-run criteria** are 17 checks over 21 years from 0.8.5, and 15
before it. The check runs the market for 21 years, 30 times over, and
replays 2008 and 2020 with the real VIX. Fifteen criteria compare what a
user would notice with real markets: how deep crashes go, how long fear
lasts, how often the VIX is above 30 or below 15, how many bear markets and
corrections a decade brings, the long-run return, and whether a headline
read late still pays. The other two, C4a and C4b, ask whether a rule that
reads only prices can find an edge real markets do not have. pt-v20 meets
all 17, and the eleven more registered for it, among them the rate indices against real
treasury and corporate bonds, the earnings cycle, value and momentum
signals, and the cost of size in the book. pt-v19, the previous default,
meets 15 of the 17 and fails C4a and C4b. On pt-v20 the 2008 replay falls 47
percent against the real 57, the VIX is above 30 on 5.8 percent of days
against a real 8.2, and the index returns 5.7 percent a year over 21 years
against a real 6.25. The verdicts ship with the package as
`tf.preset_record()["long_run"]`.

Three things are still off. The worst month of the 2020 replay is about 20
percent milder than the real one. The index has 2.1 bear markets a decade
against a real 1.1, inside the criterion's range of half to twice real but
near its edge. With the VIX held at 65 the market is 3.6 times as volatile
as with it held at 5, against 6.2 times in real markets (pt-v19 5.2).

Each crisis starts in one sector, picked at random. A scenario can pick it for
you with `Scenario().hold(epicentre="financial_services")`.

pt-v20 keeps pt-v19's coefficients where it does not add a mechanism, and
those were chosen with a scoring rule over 19 statistics that overlaps the
one-year table, so that table helped choose them and cannot also serve as a
held-out test. pt-v20's own dials were chosen against the long-run
criteria. The held-out checks are the fresh seeds and the fresh set of
companies.

Five limits are measured and written down:

| limit | what it means |
|---|---|
| horizon | one year is certified. Two years is graded on the two-year panel, and longer runs only by the long-run criteria |
| volatility memory | it decays too fast |
| scenario size | the response has the right sign, but one run cannot size it |
| macro crises | an inflation crisis or a policy crisis needs a scenario to drive it |
| roster | certification used a sector-balanced roster. Four concentrated sector mixes are also measured, on the shape rows only and for up to two years |

`tf.envelope.check()` refuses a question that falls outside a limit, and
[the realism envelope](https://tradefloor.dev/realism-envelope.html) says
what each one forbids.

Good results here do not predict real returns. The prices come from a known
model, so a strategy that happens to match it will score well here and may
fail on real data. The market has one venue, no latency, and no other trader
that adapts to you.

## Seed determinism

The same seed gives the same market on every platform. Each release builds
for five platforms, runs one fixed simulation on each, and stops if any result
differs. tradefloor ships its own `exp`, `log`, `pow`, `sin` and `cos`, so
the system's math library cannot change a result.

`pt-v20` became the default in 0.8.5, replacing `pt-v19`. If you name your
preset, a run replays exactly, and every preset from `pt-v1` on can still be
selected. Each release checks that one, with a digest per shipped preset.

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
| [`03-why-did-the-price-move`](https://github.com/simoncoombes/tradefloor/blob/main/examples/03-why-did-the-price-move.ipynb) | The eleven factors that sum to every move |
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

## Citing tradefloor

Cite the version you ran and name the preset. The same version can run
several presets, and results depend on the preset.

```bibtex
@software{tradefloor,
  author  = {Coombes, Simon},
  title   = {tradefloor: a deterministic market simulator with a limit order book},
  version = {0.8.5},
  year    = {2026},
  url     = {https://github.com/simoncoombes/tradefloor},
  doi     = {10.5281/zenodo.XXXXXXX},
  note    = {Model preset pt-v20}
}
```

The DOI is a placeholder. Zenodo will mint one DOI per release once the
archive is switched on; until then, cite the version and the URL and leave
the `doi` line out. [CITATION.cff](https://github.com/simoncoombes/tradefloor/blob/main/CITATION.cff)
carries the same details, and GitHub's "Cite this repository" button reads
it.

In the text, say which model you used, for example: "tradefloor 0.8.5,
preset pt-v20, specified in its docs/MODEL.md". To let a reader rerun a
result, publish its `RunManifest`: it records the version, preset, seed,
universe, macro state and scenario, and `reproduce()` stops on a mismatch.
[docs/SUPPORT.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/SUPPORT.md)
says which release to pin for a long study.

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
