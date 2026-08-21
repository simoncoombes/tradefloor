# pretium

A deterministic market simulator with a real limit order book.

**Pre-release.** Everything below is implemented and tested. Interfaces may
move before 1.0.

```python
import pretium as pt

universe = pt.Universe.random(108, seed=7)
engine = pt.Engine(seed=42, universe=universe)
engine.run_days(252)                      # a trading year

bars  = engine.bars(grain="day")          # Arrow, streams per day
truth = engine.truth()                    # why each price moved
```

## What makes it different

Four things, and the combination is the point.

**The market is reproducible to the bit.** Same seed, same inputs, same
market — on Linux, macOS and Windows, and on x86_64 and arm64 alike, because
the library ships its own transcendental maths rather than calling the
platform's libm.

That is measured, not argued. Every release builds a wheel on all five
targets, runs one fixed simulation inside each, and compares digests; no wheel
ships that disagrees with the others. The current run:

```
linux-x86_64     4cdfb033b42a53c42d83155bf695a2805d53da58610e19c9d7da8c3f6bf0e3b8
linux-aarch64    4cdfb033b42a53c42d83155bf695a2805d53da58610e19c9d7da8c3f6bf0e3b8
macos-arm64      4cdfb033b42a53c42d83155bf695a2805d53da58610e19c9d7da8c3f6bf0e3b8
macos-x86_64     4cdfb033b42a53c42d83155bf695a2805d53da58610e19c9d7da8c3f6bf0e3b8
windows-x86_64   4cdfb033b42a53c42d83155bf695a2805d53da58610e19c9d7da8c3f6bf0e3b8
```

A missing target is not a pass — the gate fails on four of five, because a
platform that did not report is a platform whose determinism is unverified.

**It knows why every price moved, and the reasons add up.** The simulator
computed them, so it can report them — and the `truth` table carries one row
per instrument per tick giving every contribution to that tick's mispricing:
mean reversion, momentum, the crowd, company news, order-flow pressure, a
short squeeze, and noise.

They **sum to the move**, which is what makes it a dataset rather than a
commentary. Difference `mispricing_s` across ticks, add the seven columns, and
check the label against the outcome instead of trusting it. Measured residual:
1e-16, which is float rounding.

You can observe from history that a stock fell. You cannot observe that sixty
per cent of the fall was order flow — no real dataset carries that column,
because nobody knows.

Three levels, kept apart because conflating them is easy and ruins the join:
`fundamental_value` is the valuation, `anchor_price` is what the model wanted
before the book touched it, and the printed price is what the book settled.

**Market impact is emergent.** Orders match against a real book with
price-time priority, so a large order pays worse prices because it *consumed
levels*, not because a slippage coefficient said large orders cost more.
Displayed depth is executable depth.

**Counterfactuals exist.** Run the same seed with and without your own orders
and measure exactly what your trading cost. In a real market you observe the
price you got and can never observe the price you would have got had you not
traded — your trading is part of why that price happened. Here both worlds are
runnable.

```python
ex = pt.tca.analyse(my_agent, seed=42, universe=universe, days=5)

ex.shortfall_bps()        # what your own footprint cost you
ex.by_step()              # where it was paid
ex.untouched_moved()      # should be empty: nothing else moved
```

Every fill is priced against what that instrument was doing in the world where
you never traded. That is the benchmark real TCA cannot have — arrival price,
VWAP and fitted impact models are all proxies standing in for a counterfactual
nobody can run.

Two results worth knowing before you read a number:

**A round trip can show a negative shortfall.** Buying and holding costs
+16.7 bps on one measured example; buying and selling three steps later comes
to −10.8 bps. Nothing is wrong — the entry pushed the price up, part of that
persisted, and the exit sold into it. Shortfall answers *what did each
execution cost*, not *did this strategy make money*; for the latter read `pnl`
from `evaluate`. `by_step()` shows the entry paying and the exit recouping
rather than netting them into one figure.

**Check `partial_fills()` before believing a low cost.** A request for 4,856
shares filled 483 — the whole displayed depth — and every larger request filled
the same 483. The cheapest execution is the one that did not happen.

## Evaluating agents

Same seed, N agents, identical markets. Contamination-free, because the market
never existed and no model has read its history.

```python
scores = pt.evaluate({"momentum": Momentum(), "reversion": Reversion()},
                     seed=2026, universe=universe, days=5, max_leverage=2.0)

for s in pt.leaderboard(scores):
    print(s.name, s.pnl, s.impact_bps, s.explanation_accuracy)
```

An agent sees prices, the order book and its own positions. It does **not**
see the mispricing, the fair value or the attribution — those are what it is
meant to infer, and they are used for scoring on the other side of the wall.
If an agent implements `explain()`, its stated reason is checked against what
actually drove the move.

### How much was there to win?

A P&L of $61,000 means nothing on its own. `pretium.baselines` ships the
reference points that make it readable — buy-and-hold, random, momentum,
mean-reversion — and an **Oracle** that reads the true mispricing and trades
it without estimation error.

```python
scores = pt.evaluate(pt.reference_agents(), seed=2026, universe=universe, days=60)
pt.capture_ratio(scores)     # each agent's P&L as a fraction of the Oracle's
```

The Oracle is not competing; it is a measuring instrument. It answers the
question a real market cannot even be asked — *how much alpha was available at
all* — because doing so requires observing fair value, and the gap between
price and fair value is precisely what is unobservable out there.

That converts a bare number into a fraction. And it is a **real** ceiling, not
an informational one: the Oracle's orders hit the same book as everyone
else's, so past some participation the impact eats the edge. Perfect
information does not buy unlimited size.

Quote the horizon with the ratio. Mispricing reverts on a 60-day half-life, so
a short evaluation sees only the start of the convergence: on seed 2026 the
same momentum agent captures **27% of the ceiling over five days and 94% over
sixty**.

**What this does not tell you:** whether an agent would trade real markets
well. This is a model market with knowable structure, and a determined agent
can learn that structure in ways that will not transfer. Use it to rank agents
against each other, not to certify one.

## Reinforcement learning

```python
from pretium.gym import TradingEnv

env = TradingEnv(universe=universe, seed=42, days=20)
obs, info = env.reset(seed=42)
obs, reward, terminated, truncated, info = env.step(action)
```

Passes gymnasium's `env_checker`. Actions are target weights in `[-1, 1]`
rather than share counts — scale-free, so a policy does not have to learn each
instrument's price range first. Reward is the step's P&L, measured after the
market moves, so it includes the cost of the agent's own footprint.

`pip install pretium[rl]`

## Results are columnar

Five tables over the Arrow C Data Interface, so polars, pandas, pyarrow and
duckdb read them zero-copy — and the package depends on none of them.

| table | grain |
|---|---|
| `bars` | tick, N-minute or daily OHLCV — downsampled in Rust |
| `truth` | why each price moved — valuation, mispricing, and a 7-way decomposition |
| `macro` | the evolved macro state, per day |
| `fills` | your executions, joinable to `bars` |
| `book` | order-book depth — opt-in, because it is 40x the rows |

Every numeric column is `f64`. There is no `f32` option and there will not be
one: bit-exactness is the product, and a half-precision copy would be a
different market that happens to plot the same.

Results stream as one batch per day, so a hundred-seed sweep at tick grain
does not have to fit in memory.

## Reproducing a run

A seed alone is not enough, and the reason is interesting: the market an agent
trades in depends on the agent's own orders, so one seed with different order
flow is a different market — correctly. Reproducing a run means reproducing
every input.

```python
log = engine.order_log                    # plain dicts; JSON-serialisable
same = pt.replay(log, seed=42, universe=universe)
```

That makes a published result replayable without the code that produced it,
a divergence bisectable, and an experiment archivable as data.

## Real fundamentals

```python
snap = pt.edgar.fetch(as_of="2024-06-30", limit=100,
                      user_agent="Jane Roe jane@example.org")
snap.save("edgar-2024h1.json")            # the artifact, hashed and citable

universe = pt.Universe.from_edgar(snap, federal_funds_rate=0.03)
```

Seeding from SEC filings gives an experiment whose cross-sectional structure
is real — true valuation dispersion, actual sector weights, loss-makers in
realistic proportion — while every price path stays synthetic.

The snapshot is the artifact, not the query: EDGAR is not append-only, so the
same request returns different numbers next year. Snapshots are hashed and
serialisable; a citable specification names one alongside the seed. The fetch
uses the XBRL frames API — five market-wide requests plus one per company
kept, rather than one per filer — and takes a `transport` so the derivation is
testable without a socket.

`user_agent` is required and must carry a contact address; the SEC's
fair-access policy asks for one and this library will not send a fabricated
one on your behalf. Ranking is by shareholders’ equity, because EDGAR carries
no market capitalisation — so the universe skews towards balance-sheet-heavy
names.

Note this loads *fundamentals*, not behaviour. A loaded ticker is a stock with
that company's fundamentals under this model's assumptions — not that company,
not its volatility, not its microstructure.

## Conventions worth knowing

**Rates are fractional.** `0.052` means 5.2%. Passing `5.2` raises, with an
error that says so.

**Absence is not zero.** `corporate_bond_yield=None` falls through to the
policy rate; `0.0` is a real observation used as given.

**Invalid input raises.** `ValidationError` for a malformed scenario,
`OrderError` for a rejected order. Nothing is silently clamped: a simulator
that helpfully repairs your inputs produces a market nobody specified.

**Negative EPS is legal.** Loss-makers are valued off book value. A universe
without them is not a realistic universe.

**Roster order is contractual.** The engine iterates instruments in index
order and draws as it goes, so a re-sorted universe is a different market.

## Model coefficients

Coefficients ship as a named, versioned preset (`pt-v1`) rather than as
constructor keywords, so two published results can be compared.
`model_preset()` returns it.

## How fast

Measured, on one desktop machine — treat the ratios as portable and the
absolute numbers as a rough guide.

| run | wall clock |
|---|---|
| 252 days x 10 instruments | 2.9s |
| 252 days x 100 instruments | 27.4s |
| 252 days x 100, recording 9.8M rows of ground truth | 28.2s |
| 8 seeds x 21 days x 100, serial | 20.0s |
| 8 seeds x 21 days x 100, 8 workers | 6.1s |

Two things worth knowing from that table. **Recording is roughly free** — 3%
for a full year of tick-grain ground truth, because the raw buffers are kept
and the Arrow batches are built on read, so grain stays a read-time decision.
And **sweeps parallelise about 3.3x on eight cores**, because the engine
releases the GIL for the whole session compute; `run_many` uses threads, so it
works in a notebook and does not serialise the universe into each worker.

Cost scales roughly linearly in instruments x days. Parallelism is per seed
and only per seed: the engine has one shared RNG stream across the market, the
economy and the microstructure, so there is no decomposition *within* a run
that preserves the draw schedule.

## A worked example

`examples/research_workflow.py` runs the whole thing end to end in about ten
seconds — universe, 20-seed sweep, five-agent evaluation, TCA, 234,000 rows of
ground truth, then archives the run as JSON and replays it to identical prices.

```
python examples/research_workflow.py
```

It is run by the test suite, deliberately. Using the library the way a user
would, rather than only running its unit tests, is what caught the two worst
defects this project has had — a parallel sweep that hung forever in any
notebook, and an order log that did not compare equal to itself after a JSON
round trip. Both had green unit tests.

## Install

```
pip install pretium              # core, no dependencies
pip install pretium[rl]          # + numpy, gymnasium
pip install pretium[arrow]       # + pyarrow
```

Wheels are abi3 and cover CPython 3.11 and later.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
