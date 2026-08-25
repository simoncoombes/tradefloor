# pretium

[![determinism](https://github.com/simoncoombes/pretium/actions/workflows/determinism.yml/badge.svg)](https://github.com/simoncoombes/pretium/actions/workflows/determinism.yml)
[![licence: MIT OR Apache-2.0](https://img.shields.io/badge/licence-MIT%20OR%20Apache--2.0-blue.svg)](#licence)
[![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

A market simulator you can run a strategy against. Rust core, Python API.

Give it a seed and a set of companies and it runs a market forward: prices, a
limit order book, fills, and an economy that advances itself daily. Your
orders match against real depth, so trading moves the price the way it would
anywhere else.

It exists because historical data can't answer two questions. What would have
happened if I'd traded differently, and what actually caused this price move.

## Install

```
pip install pretium
```

0.1.1 is the current release, and 0.1.0 was the first. The API may still
move before 1.0, and anything that changes the simulated trajectory arrives
as a new model preset rather than an edit to an existing one, so a published
result stays reproducible.

## In thirty seconds

```python
import pretium as pt

universe = pt.Universe.random(40, seed=111)

spec = pt.StrategySpec.momentum(lookback_days=1.0, top_k=5)
scores = pt.evaluate({"mine": spec}, seed=7, universe=universe, days=10)

scores["mine"].return_pct            # what it made
scores["mine"].impact_bps            # what its own footprint cost
scores["mine"].strategy_fingerprint  # sha256, cite this
```

That's one market draw, which tells you as much about the seed as about the
strategy. `pt.rank` runs many seeds and compares them with a paired sign
test.

## What makes it different

**Determinism that's checked.** The crate ships its own `exp`, `log`, `sin`
and `cos` instead of calling the platform libm. Every release builds five
targets, runs one fixed simulation inside each, and compares digests. A
disagreement fails the release. A WebAssembly build produces the same digest
as the native one.

**Ground truth you can read.** The simulator computed every price, so it can
tell you why. One row per instrument per tick, with seven factor
contributions that sum to the move and a residual around 1e-16. No historical
dataset carries those labels. You can see that a stock fell, never that 60%
of the fall was order-flow pressure.

**Counterfactuals you can run.** The same seed runs with and without your
orders, so every fill is priced against the market where you never traded.
That's the number TCA vendors approximate.

**Results someone else can check.** A `StrategySpec` is a declarative,
hashable document rather than an arbitrary callable. A `RunManifest` carries
the seed, universe, macro conditions, scenario, model and strategy together
with the expected digest. `reproduce()` refuses on a mismatch and names the
culprit, because a manifest that quietly reproduced a different market would
be worse than no manifest.

## Driving it from an agent

```
pip install "pretium[mcp]"
claude mcp add pretium -- pretium-mcp
```

Eleven read-only tools over the simulator, so a coding agent can ask whether
a momentum strategy beats buy-and-hold here, and whether the difference is
real. Strategies, universes and scenarios are composed as data. There's no
path from a tool argument to code execution.

Every result carries computed caveats and full provenance. A model
summarising a result has the tool output and nothing else, and will otherwise
report `return_pct: 88.7` as "the strategy made 88.7%".
[The MCP server](https://simoncoombes.github.io/pretium/mcp.html) has the
tool list and the client configuration.

## What it's bad at

**Good results here don't predict real returns.** The price process comes
from a known model, so a strategy that fits that model's structure will look
excellent and teach you nothing. A strategy that fails here is more
informative, because it broke against a live order book under honest impact
costs.

**Realism is a stated envelope, not a score.** `pt.facts.measure()` reports
ten statistics against real-market bands. At 252 days the shipped `pt-v3`
preset holds nine of the ten in band, and holds the same nine on axes the
calibration never saw: fresh seeds, and a fresh 60-name universe.

The tenth is the autocorrelation of volume changes, which misses by 13.7
seed-standard-deviations. It's excluded from the calibration objective
deliberately, because an optimiser pointed at an unreachable target doesn't
fail cleanly, it distorts every other parameter chasing it. In practice a
volume forecast here is never wrong twice running, so an execution algorithm
faces an easier scheduling problem than the one it was written for.

**Six gaps are measured and named rather than assumed.** The certified
horizon is 252 days and the model doesn't hold beyond it. Volatility memory
decays exponentially where real markets decay hyperbolically. Tails are too
thin over multi-year windows. Scenario response is directionally right but
not calibrated in magnitude. And certification was measured on a
sector-balanced roster, which no real index is.
[The realism envelope](https://simoncoombes.github.io/pretium/trust.html)
states each gap and what it forbids. `pt.envelope.check()` refuses to certify
a question that falls outside one.

**Single venue, no latency, no strategic counterparties.** Orders arrive
instantly, there's one book per name, and you trade against a market maker
and aggregate flow rather than agents that adapt to you.

## Worked examples

Six notebooks and two scripts in [`examples/`](examples/), numbered in
reading order and executed as part of the test suite:

| | what it covers |
|---|---|
| [`01-first-simulation`](examples/01-first-simulation.ipynb) | Universe, engine, order book, determinism |
| [`02-evaluating-a-strategy`](examples/02-evaluating-a-strategy.ipynb) | Specs, baselines, ranking across seeds |
| [`03-why-did-the-price-move`](examples/03-why-did-the-price-move.ipynb) | The seven factors that sum to every move |
| [`04-how-realistic-is-this`](examples/04-how-realistic-is-this.ipynb) | The realism panel and the gaps |
| [`05-training-an-agent`](examples/05-training-an-agent.ipynb) | The Gymnasium environment, and what size costs |
| [`06-execution-and-impact`](examples/06-execution-and-impact.ipynb) | TCA and the counterfactual run |

Or a whole study in one file. Sweep, evaluation, TCA and replay in about five
seconds:

```
python examples/07-research-workflow.py
```

## Documentation

Full docs: [**simoncoombes.github.io/pretium**](https://simoncoombes.github.io/pretium/)

Getting started and core concepts. Agents, evaluation and the RL environment.
Scenarios and macro paths, strategy specs, model presets, checkpointing and
forking, sharing a run, Arrow output and streaming sweeps, SEC EDGAR loading,
transaction cost analysis, the MCP server, running in the browser, the
realism envelope and metrics, and the conventions worth reading before you
hit them.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). One rule shapes the rest: a change to
the simulated trajectory is a breaking change, whatever its size. A market
that runs differently from the same seed invalidates every published result
that cited it.

## Citing this

See [CITATION.cff](CITATION.cff), or cite a specific result by its manifest.
The seed, the universe fingerprint, the model fingerprint and the strategy
fingerprint together identify exactly what ran.

## Licence

MIT OR Apache-2.0, at your option. See [LICENSE-MIT](LICENSE-MIT) and
[LICENSE-APACHE](LICENSE-APACHE).
