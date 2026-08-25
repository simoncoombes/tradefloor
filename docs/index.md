---
title: Home
nav_order: 1
---

# pretium

A market simulator you can run a strategy against. Rust core, Python API,
deterministic across platforms.

```
pip install pretium
```

```python
import pretium as pt

universe = pt.Universe.random(108, seed=7)
engine = pt.Engine(seed=42, universe=universe)
engine.run_days(252)

bars = engine.bars(grain="day")     # OHLCV, Arrow
truth = engine.truth()              # what drove every price move
```

Wheels are abi3 and cover CPython 3.11 and later. The core package has no
dependencies.

## Start here

Read [Core concepts](core-concepts.html) and
[Running a simulation](running-a-simulation.html) first. Between them they
cover the three things that define a run, a universe, a macro state and a
seed, and the four granularities you can step it at.

## Find your task

| You want to | Read |
|---|---|
| Train an agent | [Reinforcement learning](reinforcement-learning.html), [Agents and evaluation](agents-and-evaluation.html) |
| Drive an agent with an LLM | [An LLM agent](an-llm-agent.html) |
| Build an execution algorithm | [Transaction cost analysis](transaction-cost-analysis.html), [Reading results](reading-results.html) |
| Stress-test a strategy | [Scenarios](scenarios.html), [Sweeps and parallelism](sweeps-and-parallelism.html), [Forking a simulation](forking-a-simulation.html) |
| Score a model against known answers | [Ground truth](ground-truth.html) |
| Use real company fundamentals | [SEC EDGAR](real-fundamentals-from-sec-edgar.html) |
| Publish a reproducible result | [Reproducing a run](reproducing-a-run.html) |

## What the library guarantees

**The same seed gives the same market on every machine.** Identical output to
the last bit on Linux, macOS and Windows, x86 and ARM alike, because the
library ships its own `exp`, `log`, `sin` and `cos` rather than calling the
platform's. The release gate builds wheels for five targets, runs one fixed
simulation inside each, and compares digests, failing on any disagreement -
measured green on all five targets at commit `ad91026`; the current
baseline's digest has been reproduced on one platform so far. See
[Reproducing a run](reproducing-a-run.html) for the by-commit record.

**Orders move the price.** Matching runs against a real book with price-time
priority, so a large order pays worse prices because it consumed levels, not
because a slippage formula charged it.

**Every price move is labelled.** The `truth` table gives one row per
instrument per tick with seven factor contributions that sum to the move,
residual around 1e-16.

## Before you trust a number

Two pages are worth reading before you draw a conclusion rather than after.

[How realistic is this market](how-realistic-is-this-market.html) sets out
which statistics match real equities and which do not, including the one that
makes momentum profitable here for reasons that will not transfer to real
markets.

[Conventions](conventions.html) covers what bites: rates are fractional,
absence differs from zero, and roster order is contractual.
