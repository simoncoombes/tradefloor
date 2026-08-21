---
title: Home
nav_order: 1
---

# pretium documentation

A market simulator with a real limit order book. Rust core, Python API.

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

## Where to start

If you have not used it before, read [Core concepts](core-concepts.html) and
[Running a simulation](running-a-simulation.html) first. Between them they
cover the three things that define a run - a universe, a macro state and a
seed - and the four granularities you can step it at.

After that, go to whichever of these is your job:

| you want to | read |
|---|---|
| train an agent | [Reinforcement learning](reinforcement-learning.html), [Agents and evaluation](agents-and-evaluation.html) |
| build an execution algorithm | [Transaction cost analysis](transaction-cost-analysis.html), [Reading results](reading-results.html) |
| stress-test a strategy | [Scenarios](scenarios.html), [Sweeps and parallelism](sweeps-and-parallelism.html), [Forking a simulation](forking-a-simulation.html) |
| score a model against known answers | [Ground truth](ground-truth.html) |
| use real company fundamentals | [Real fundamentals from SEC EDGAR](real-fundamentals-from-sec-edgar.html) |
| publish a result someone else can re-run | [Reproducing a run](reproducing-a-run.html) |

Two pages are worth reading before you trust a number rather than after.
[How realistic is this market](how-realistic-is-this-market.html) sets out
which statistics match real equities and which do not, including the one that
makes momentum profitable here for reasons that will not transfer.
[Conventions](conventions.html) covers the things that bite: fractional rates,
absence differing from zero, and roster order being contractual.

## All pages

- [Core concepts](core-concepts.html)
- [Running a simulation](running-a-simulation.html)
- [Reading results](reading-results.html)
- [Ground truth](ground-truth.html)
- [Agents and evaluation](agents-and-evaluation.html)
- [Transaction cost analysis](transaction-cost-analysis.html)
- [Scenarios](scenarios.html)
- [Forking a simulation](forking-a-simulation.html)
- [Sweeps and parallelism](sweeps-and-parallelism.html)
- [Reproducing a run](reproducing-a-run.html)
- [Real fundamentals from SEC EDGAR](real-fundamentals-from-sec-edgar.html)
- [Reinforcement learning](reinforcement-learning.html)
- [How realistic is this market](how-realistic-is-this-market.html)
- [Conventions](conventions.html)
- [Performance](performance.html)
