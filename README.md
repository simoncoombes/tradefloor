# pretium

A market simulator you can run a strategy against. Rust core, Python API.

You give it a seed and a set of companies. It runs a market forward — prices,
a limit order book, fills, macro state — and your orders match against real
depth, so trading moves the price the way it would anywhere else.

Two things you can do here that you cannot do with historical data: run the
same market twice with different decisions, and read the true value of every
stock while you trade it.

**Pre-release.** Everything below works and is tested. The API may move before 1.0.

```
pip install pretium              # core, no dependencies
pip install pretium[rl]          # + numpy, gymnasium
pip install pretium[arrow]       # + pyarrow
```

Wheels are abi3, CPython 3.11+, no Rust toolchain needed.

## Quick start

```python
import pretium as pt

universe = pt.Universe.random(108, seed=7)
engine = pt.Engine(seed=42, universe=universe)
engine.run_days(252)                      # one trading year

bars = engine.bars(grain="day")           # OHLCV, Arrow
truth = engine.truth()                    # what drove every price move
```

## Why you might want it

### Same seed, same market, every machine

Run it on your laptop, your CI box and a reviewer's Mac and get identical
prices down to the last bit. The library carries its own implementation of
`exp`, `log`, `sin` and `cos` instead of calling the platform's, which is what
normally makes float results drift between operating systems.

Every release builds wheels for five targets, runs one fixed simulation inside
each, and compares digests. If any two disagree, the release fails.

```
linux-x86_64     112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
linux-aarch64    112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
macos-arm64      112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
macos-x86_64     112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
windows-x86_64   112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
```

### Labelled data

The simulator computed every price, so it can tell you what moved it. The
`truth` table gives one row per instrument per tick with each contribution to
the move: mean reversion, momentum, crowd, news, order flow, short squeeze,
noise.

The seven columns sum to the move — residual around 1e-16 — so you can check a
label against the outcome rather than trust it.

```python
truth = engine.truth()        # fundamental_value, anchor_price, mispricing_s, 7 factors
```

Useful if you are testing a regime detector, an event-attribution model, or
anything where you need to know the answer to score the guess.

### Your orders move the price

Orders match against a book with price-time priority. A large order pays worse
prices because it eats levels, not because a slippage formula charged it.

Holding the signal and horizon fixed and changing only rebalance frequency:

| rebalances per day | return |
|---|---|
| 3 | +88.7% |
| 6 | +30.9% |
| 12 | −13.2% |

No fees are charged anywhere. That is spread and depth alone.

### Run the world without you in it

Real TCA compares your fill to a proxy — arrival price, VWAP, a fitted impact
model — because the price you would have got without trading is unobservable.
Here you can just run it.

```python
ex = pt.tca.analyse(my_agent, seed=42, universe=universe, days=5)

ex.shortfall_bps()      # what your footprint cost
ex.by_step()            # where you paid it
ex.partial_fills()      # check this before believing a low number
```

Every fill is priced against what that instrument did in the world where you
never traded.

## Comparing agents

Run N agents through identical markets. No leakage, because the market never
existed and nothing has read its history.

```python
scores = pt.evaluate({"momentum": Momentum(), "reversion": Reversion()},
                     seed=2026, universe=universe, days=5, max_leverage=2.0)

for s in pt.leaderboard(scores):
    print(s.name, s.pnl, s.impact_bps)
```

Agents see prices, the book and their own positions. They do not see fair value
or the attribution — inferring those is the job, and they are used for scoring
on the other side of the wall.

**Use more than one seed.** Across twelve markets, a single seed picks the top
agent half the time. `rank` runs a set of seeds and reports a paired sign test
so you can tell "wins more often" from "wins by more".

```python
ranking = pt.rank(lambda: pt.reference_agents(seed=3), seeds=range(12),
                  universe=universe, days=10, workers=4)
print(ranking.report())
ranking.separation("momentum", "mean_reversion")   # 6-6, p = 1.0
```

## What it is bad at

**Do not use it to validate a strategy.** The price process comes from a known
model. Anything that fits the model's structure will look brilliant and teach
you nothing about markets. Use it to kill strategies, size them, and test
execution.

**Momentum works here for the wrong reason.** Measured return autocorrelation
is +0.219 at lag one; real equities sit near zero. That is the mispricing
process showing through, and an agent trading it has an edge that will not
transfer. `pt.facts.measure()` reports this and three other statistics against
real ranges, including the ones that match.

**Single venue, no latency, no strategic counterparties.** Orders arrive
instantly, there is one book per name, and you trade against a market maker and
aggregate flow rather than agents that adapt to you.

## Documentation

Full docs: [**pretium documentation**](docs/index.md)

Scenarios and macro paths, checkpointing and forking, Arrow output and
streaming sweeps, SEC EDGAR loading, the RL environment, reproducibility and
replay, performance, and the conventions worth reading before you hit them.

## A worked example

```
python examples/research_workflow.py
```

Fifteen seconds end to end: universe, 20-seed sweep, five-agent evaluation,
TCA, 234,000 rows of ground truth, a fork into two macro futures, a realism
report, then archives the run as JSON and replays it to identical prices.

The test suite runs it. Using the library the way a user would is what caught
the two worst bugs this project has had, both of which had green unit tests.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
