# pretium

A market simulator you can run a strategy against. Rust core, Python API.

Give it a seed and a set of companies. It runs a market forward — prices, a
limit order book, fills, macro state — and your orders match against real
depth, so trading moves the price the way it would anywhere else.

Two things you can do here that historical data will never let you do: run the
same market twice with different decisions, and read the true value of every
stock while you trade it.

It's built to show you what breaks a strategy — bad sizing, execution cost, a
rate shock it can't survive. It won't tell you what one earns, and
[the last section](#what-it-is-bad-at) explains why.

**Pre-release.** Everything below works and is tested. The API may move before 1.0.

```
pip install pretium              # core, no dependencies
pip install pretium[rl]          # + numpy, gymnasium
pip install pretium[arrow]       # + pyarrow
```

Wheels are abi3, CPython 3.11+, no Rust toolchain needed.

```python
import pretium as pt

universe = pt.Universe.random(108, seed=7)
engine = pt.Engine(seed=42, universe=universe)
engine.run_days(252)                      # one trading year

bars = engine.bars(grain="day")           # OHLCV, Arrow
truth = engine.truth()                    # what drove every price move
```

## Who this is for

### You're training an agent to trade

Historical data ignores you. Your agent can buy a million shares of a name that
trades ten thousand a day, and the tape carries on exactly as it did in 2019.
Whatever the agent learns about sizing is fiction.

Here the market pushes back. Orders match against a book with price-time
priority, so size costs money and the agent finds that out during training. And
because every market comes from a seed, you get unlimited independent episodes
instead of the same history over and over.

```python
from pretium.gym import TradingEnv

env = TradingEnv(universe=universe, seed=42, days=20)
obs, info = env.reset(seed=42)
obs, reward, terminated, truncated, info = env.step(action)
```

Passes gymnasium's `env_checker`. Actions are target weights in `[-1, 1]`, so a
policy doesn't have to learn each instrument's price range first. Reward is
measured after the market moves, so it already includes the cost of the agent's
own footprint.

### You're building an execution algorithm

TWAP, VWAP, POV, icebergs — the work is all in what happens between the
decision and the fill, which is exactly what a replayed tape can't show you.

You get queue position, partial fills, and a spread that widens under stress.
You also get the number every TCA vendor approximates: run the same seed
without your orders in it, and price every fill against the world where you
never traded.

```python
ex = pt.tca.analyse(my_algo, seed=42, universe=universe, days=5)

ex.shortfall_bps()      # what your footprint cost
ex.by_step()            # where you paid it
ex.partial_fills()      # what you asked for versus what you got
```

Arrival price, VWAP and fitted impact models are all standing in for a
counterfactual nobody can run on real data. This one runs it.

### You're stress-testing a strategy

One history gives you one sample. A strategy that survived 2015–2025 survived
*a* decade, and you can't run the other ones.

Run it across a thousand seeded markets and watch where it breaks. Walk rates
from 2.5% to 5% over fifteen days while it holds positions. Fork a market at
day sixty and take two different paths from an identical state.

```python
shock = pt.Scenario.rate_shock(start=0.025, end=0.05, over=15)
pt.evaluate(agents, seed=7, universe=universe, days=20, scenario=shock)

ranking = pt.rank(lambda: pt.reference_agents(seed=3), seeds=range(12),
                  universe=universe, days=10, workers=4)
ranking.separation("momentum", "mean_reversion")   # 6-6, p = 1.0
```

Use it to kill strategies rather than to bless them. Something that dies under
a rate shock, or whose edge evaporates once impact is charged honestly, is
genuinely dead — and you learned it for the price of some CPU.

### You're testing a model that claims to explain markets

Regime detectors, event-attribution models, factor models. You can't score any
of them on real data, because nobody knows the right answer.

This simulator does know, because it computed the answer before it drew the
price. The `truth` table gives one row per instrument per tick with every
contribution to that move: mean reversion, momentum, crowd, news, order flow,
short squeeze, noise. The seven columns sum to the move with a residual around
1e-16, so you can check a label against the outcome instead of trusting it.

```python
truth = engine.truth()   # fundamental_value, anchor_price, mispricing_s, 7 factors
```

### You're teaching market microstructure

A real matching engine your students can put orders into, with the answer key
attached. They can watch a large order walk the book, then read exactly how
much of the resulting move was their own flow.

## Under the hood

**Same seed, same market, on every machine.** Identical prices to the last bit
on Linux, macOS and Windows, x86 and ARM alike. The library carries its own
`exp`, `log`, `sin` and `cos` rather than calling the platform's, which is what
normally makes float results drift between operating systems.

Every release builds wheels for five targets, runs one fixed simulation inside
each, and compares digests. Any disagreement fails the release.

```
linux-x86_64     112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
linux-aarch64    112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
macos-arm64      112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
macos-x86_64     112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
windows-x86_64   112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
```

**Impact is emergent.** A large order pays worse prices because it ate levels,
with no slippage coefficient anywhere. Holding the signal and horizon fixed and
changing only how often a strategy rebalances:

| rebalances per day | return |
|---|---|
| 3 | +88.7% |
| 6 | +30.9% |
| 12 | −13.2% |

No fees are charged. That gap is spread and depth alone.

**Results are columnar.** Five Arrow tables (bars, truth, macro, fills, book)
read zero-copy by polars, pandas, pyarrow and duckdb, and the package depends
on none of them. Runs stream a batch per day, so a hundred-seed sweep at tick
grain never has to fit in memory.

**Fast enough to sweep.** A 252-day year over 100 instruments takes 27 seconds,
and recording 9.8 million rows of ground truth adds 3%. Sweeps parallelise
about 3.3x on eight cores.

## What it is bad at

**Good results here don't predict real returns.** The price process comes from
a known model, so a strategy that happens to fit that model's structure will
look brilliant and teach you nothing about real markets. A strategy that
*fails* here has still told you something — it broke against a live order book
under honest impact costs. Read the failures, not the P&L.

**Momentum works here for the wrong reason.** Measured return autocorrelation
is +0.219 at lag one where real equities sit near zero. That's the mispricing
process showing through, and an agent trading it has an edge that won't
transfer. `pt.facts.measure()` reports this and three other statistics against
real ranges, including the ones that match.

**Single venue, no latency, no strategic counterparties.** Orders arrive
instantly, there's one book per name, and you trade against a market maker and
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
