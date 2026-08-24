# pretium

[![determinism](https://github.com/simoncoombes/pretium/actions/workflows/determinism.yml/badge.svg)](https://github.com/simoncoombes/pretium/actions/workflows/determinism.yml)
[![licence: MIT OR Apache-2.0](https://img.shields.io/badge/licence-MIT%20OR%20Apache--2.0-blue.svg)](#licence)
[![python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)

A market simulator you can run a strategy against. Rust core, Python API.

Give it a seed and a set of companies. It runs a market forward — prices, a
limit order book, fills, an economy that advances itself daily — and your
orders match against real depth, so trading moves the price the way it would
anywhere else.

It exists because historical data cannot answer two questions: *what would
have happened had I traded differently*, and *what actually caused this
price move*.

## Install

```
pip install pretium
```

Pre-release: not yet on PyPI or crates.io, and the API may move before 1.0.

## In thirty seconds

```python
import pretium as pt

universe = pt.Universe.random(40, seed=111)

spec = pt.StrategySpec.momentum(lookback_days=1.0, top_k=5)
scores = pt.evaluate({"mine": spec}, seed=7, universe=universe, days=10)

scores["mine"].return_pct            # what it made
scores["mine"].impact_bps            # what its own footprint cost
scores["mine"].strategy_fingerprint  # sha256 — cite this
```

One seed measures the seed as much as the strategy. `pt.rank` runs many and
compares them with a paired sign test, which is the version worth believing.

## What makes it different

**Bit-identical determinism, verified rather than asserted.** The crate ships
its own `exp`, `log`, `sin` and `cos` rather than calling the platform libm.
Every release builds five targets, runs one fixed simulation inside each and
compares digests; any disagreement fails the release. The same digest comes
out of a WebAssembly build in a browser.

**Readable ground truth.** The simulator computed every price, so it can tell
you why: one row per instrument per tick with seven factor contributions that
sum to the move, residual around 1e-16. No historical dataset carries those
labels — you can observe that a stock fell, never that 60% of the fall was
order-flow pressure.

**Runnable counterfactuals.** The same seed runs with and without your
orders, so every fill is priced against the world where you never traded —
the number every TCA vendor approximates.

**Results someone else can check.** A `StrategySpec` is a declarative,
hashable document rather than an arbitrary callable, and a `RunManifest`
carries the seed, universe, macro conditions, scenario, model and strategy
with the expected digest. `reproduce()` refuses on mismatch and names the
culprit, because a manifest that silently reproduced a different market
would manufacture false confidence.

## Driving it from an agent

```
pip install "pretium[mcp]"
claude mcp add pretium -- pretium-mcp
```

Eleven read-only tools over the simulator, so a coding agent can ask *does a
momentum strategy beat buy-and-hold here, and is the difference real?* and
get a measured answer. Strategies, universes and scenarios are composed as
data; there is no path from a tool argument to code execution.

Every result carries computed caveats and full provenance, because a model
summarising a result has the tool output and nothing else — and will
otherwise report `return_pct: 88.7` as "the strategy made 88.7%".
[The MCP server](https://simoncoombes.github.io/pretium/mcp.html) has the
tool list and the client configuration.

## What it is bad at

**Good results here don't predict real returns.** The price process comes
from a known model, so a strategy that fits that model's structure will look
brilliant and teach you nothing. A strategy that *fails* here has still told
you something — it broke against a live order book under honest impact costs.
Read the failures, not the P&L.

**Realism is a stated envelope, not a score.** `pt.facts.measure()` reports
ten statistics against real-market bands. At 252 days the shipped `pt-v3`
preset puts **nine of the ten in band**, and holds the same nine on axes the
calibration never saw — fresh seeds and a fresh 60-name universe.

The tenth, the autocorrelation of volume changes, fails structurally at 13.7
seed-standard-deviations and is excluded from the calibration objective
deliberately: an optimiser pointed at an unreachable target does not fail
cleanly, it distorts every other parameter chasing it. In practice a volume
forecast here is never wrong twice running, so an execution algorithm faces
an easier problem than the one it was written for.

**Six gaps are measured and named rather than assumed** — the certified
horizon is 252 days and the model does not hold beyond it; volatility memory
decays exponentially where real markets decay hyperbolically; tails are too
thin over multi-year windows; scenario response is directionally right but
not calibrated in magnitude; and certification was measured on a
sector-balanced roster, which no real index is.
[The realism envelope](https://simoncoombes.github.io/pretium/realism-envelope.html)
states each gap and what it forbids, and `pt.envelope.check()` will refuse to
certify a question that falls outside one.

**Single venue, no latency, no strategic counterparties.** Orders arrive
instantly, there is one book per name, and you trade against a market maker
and aggregate flow rather than agents that adapt to you.

## Worked examples

Four notebooks in [`examples/`](examples/), each runnable end to end:

| notebook | what it answers |
|---|---|
| [`01-first-simulation.ipynb`](examples/01-first-simulation.ipynb) | What does a market look like, and what did I just run? |
| [`02-evaluating-a-strategy.ipynb`](examples/02-evaluating-a-strategy.ipynb) | Is my strategy any good, and how would I know? |
| [`03-why-did-the-price-move.ipynb`](examples/03-why-did-the-price-move.ipynb) | Ground truth: the seven factors that sum to every move |
| [`04-how-realistic-is-this.ipynb`](examples/04-how-realistic-is-this.ipynb) | What is this certified to reproduce, and where does it fail? |

Or as a script — universe, 20-seed sweep, five-agent evaluation and TCA in
about five seconds:

```
python examples/research_workflow.py
```

## Documentation

Full docs: [**simoncoombes.github.io/pretium**](https://simoncoombes.github.io/pretium/)

Getting started and core concepts; agents, evaluation and the RL environment;
scenarios and macro paths; strategy specs; model presets; checkpointing and
forking; sharing a run; Arrow output and streaming sweeps; SEC EDGAR loading;
transaction cost analysis; the MCP server; running in the browser; the
realism envelope and metrics; and the conventions worth reading before you
hit them.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). One rule shapes the rest: **a change
to the simulated trajectory is a breaking change**, whatever its size — a
market that runs differently from the same seed invalidates every published
result that cited it.

## Citing this

See [CITATION.cff](CITATION.cff), or cite a specific result by its manifest:
the seed, the universe fingerprint, the model fingerprint and the strategy
fingerprint together identify exactly what ran.

## Licence

MIT OR Apache-2.0, at your option. See [LICENSE-MIT](LICENSE-MIT) and
[LICENSE-APACHE](LICENSE-APACHE).
