# pretium documentation

A market simulator with a real limit order book. Rust core, Python API.

- [Core concepts](#core-concepts)
- [Running a simulation](#running-a-simulation)
- [Reading results](#reading-results)
- [Ground truth](#ground-truth)
- [Agents and evaluation](#agents-and-evaluation)
- [Transaction cost analysis](#transaction-cost-analysis)
- [Scenarios](#scenarios)
- [Forking a simulation](#forking-a-simulation)
- [Sweeps and parallelism](#sweeps-and-parallelism)
- [Reproducing a run](#reproducing-a-run)
- [Real fundamentals from SEC EDGAR](#real-fundamentals-from-sec-edgar)
- [Reinforcement learning](#reinforcement-learning)
- [How realistic is this market](#how-realistic-is-this-market)
- [Conventions](#conventions)
- [Performance](#performance)

---

## Core concepts

Three things define a run: a **universe** (which companies exist and what their
fundamentals are), a **macro** state (rates, inflation, the cycle), and a
**seed**.

```python
import pretium as pt

universe = pt.Universe.random(108, seed=7)
macro = pt.Macro(fed_funds=0.025, corporate_bond_yield=0.052, vix=16.0)
engine = pt.Engine(seed=42, universe=universe, macro=macro)
```

### Universe

`Universe` subclasses `list`, and roster order is contractual. The engine walks
instruments in index order and draws random numbers as it goes, so a re-sorted
universe produces a different market from the same seed. Sorting your tickers
alphabetically upstream will silently give you a different world.

Use `universe.fingerprint` — a sha256 over the roster's canonical form,
including order — whenever you need to ask whether two universes are the same
one. Tickers are generated positionally, so `Universe.random(40, seed=1)` and
`Universe.random(40, seed=99)` share every name and no fundamentals.

The universe seed is separate from the simulation seed. Holding the universe
fixed while varying the simulation seed is the standard setup for variance
estimation.

Three constructors:

```python
pt.Universe.random(108, seed=7)                  # generated, plausible per sector
pt.Universe([pt.Instrument(...), ...])           # explicit roster
pt.Universe.from_edgar(snapshot)                 # real SEC fundamentals
```

### Macro

The macro chain runs endogenously by default — a Taylor rule feeding off
Phillips and Okun relationships over a cycle. You set initial conditions and it
evolves. To drive a series yourself, see [Scenarios](#scenarios).

Read it back per day from `day.economy`, or over a whole run from the `macro`
table.

---

## Running a simulation

Four granularities. Which one you want depends on how often your code makes a
decision.

| your decisions happen | use |
|---|---|
| daily or slower | `days()` / `run_days()` |
| on fills, bars or events | `run_until(...)` |
| every tick, one market | `tick()` |
| across many seeds | `run_many` / `EngineBatch` |

```python
# whole span, one call
engine.run_days(252)

# day at a time, inspect between
for day in engine.days(252):
    day.closes          # ndarray in roster order
    day.economy         # macro snapshot

# advance until something happens
ev = engine.run_until(fill=True, day_close=True, max_ticks=390)

# tick granularity
engine.submit(pt.Order("ACME", side="buy", qty=500, type="limit", price=99.5))
tick = engine.tick()
tick.fills
engine.advance_day()    # close bookkeeping: momentum roll, GARCH innovation
```

`days()` and `run_days()` simulate exactly the same thing and differ only in
how many times you get control back. A day is 390 regular-session ticks plus
the close bookkeeping `advance_day()` owns. Mixing granularities is legal.

### Why not a tick loop

A Python `for` loop calling `tick()` crosses the Python↔Rust boundary about
98,000 times per simulated year, and every attribute read on the result is
another crossing. A loop touching five fields per tick makes roughly 500,000.
Prefer `days()` or `run_until` unless you genuinely need per-tick control; the
work happens in Rust either way, and the difference is how much time you spend
at the boundary.

Orders submitted between ticks land in the next tick's pending-order
aggregates, which is the same channel the engine's own flow uses.

---

## Reading results

Five tables, delivered over the Arrow C Data Interface. polars, pandas, pyarrow
and duckdb all read them zero-copy, and the package depends on none of them.

| table | grain |
|---|---|
| `bars` | tick, N-minute or daily OHLCV, downsampled in Rust |
| `truth` | valuation, mispricing and a 7-way decomposition of every move |
| `macro` | evolved macro state, per day |
| `fills` | your executions, joinable to `bars` |
| `book` | order-book depth, opt-in because it is 40x the rows |

```python
import polars as pl

bars = pl.from_arrow(engine.bars(grain="day"))
truth = pl.from_arrow(engine.truth())
```

Every numeric column is `f64`. There is no `f32` option, because the
known-answer tests and the cross-platform release gate hash these buffers, and
a half-precision copy would be a different market that happens to plot the
same.

Results stream one batch per day. Grain is a read-time decision — the raw
buffers are kept and Arrow batches are built on read — which is why recording
ground truth costs about 3% rather than doubling the run.

---

## Ground truth

`truth` carries one row per instrument per tick:

- `fundamental_value` — what the company is worth on its fundamentals
- `anchor_price` — what the model wanted before the book touched it
- `mispricing_s` — log deviation of price from fair value
- seven factor columns — mean reversion, momentum, crowd, news, order flow,
  short squeeze, noise

Keep the three price levels apart. Conflating them ruins the join.

The seven factors sum to the change in `mispricing_s`, with a measured residual
around 1e-16. Difference `mispricing_s` across ticks, add the columns, and you
can verify the label instead of trusting it.

```python
d = truth.sort("tick").with_columns(
    (pl.col("mispricing_s").diff()).alias("move"),
    (pl.col("f_reversion") + pl.col("f_momentum") + pl.col("f_crowd")
     + pl.col("f_news") + pl.col("f_flow") + pl.col("f_squeeze")
     + pl.col("f_noise")).alias("sum_factors"),
)
```

Historical data cannot carry these columns. You can observe that a stock fell;
you cannot observe that 60% of the fall was order flow, because nobody knows.

---

## Agents and evaluation

An agent sees prices, the order book and its own positions. It does not see
fair value, mispricing or the attribution — inferring those is the task, and
they are used for scoring on the other side of the wall.

```python
scores = pt.evaluate({"momentum": Momentum(), "reversion": Reversion()},
                     seed=2026, universe=universe, days=5, max_leverage=2.0)

for s in pt.leaderboard(scores):
    print(s.name, s.pnl, s.impact_bps, s.explanation_accuracy)
```

If an agent implements `explain()`, its stated reason is checked against what
actually drove the move.

### Reference points

A P&L of $61,000 means nothing alone. `pretium.baselines` ships buy-and-hold,
random, momentum and mean-reversion agents, plus an **Oracle** that reads the
true mispricing and trades it without estimation error.

```python
scores = pt.evaluate(pt.reference_agents(), seed=2026, universe=universe, days=60)
pt.capture_ratio(scores)     # each agent's P&L as a fraction of the Oracle's
```

The Oracle measures how much was there to earn, a question a real market cannot
be asked, because answering it means observing fair value.

Treat it as a reference point rather than a ceiling. It gets the same gross
exposure and participation cap as everything else and spends them on a naive
rule — equal weight across the ten most mispriced names. Over 384 agent-seed
pairs, agents beat it 9.9% of the time:

| agent | beats the Oracle |
|---|---|
| mean_reversion | 31.2% |
| momentum | 8.3% |
| buy_and_hold | 0.0% |
| random | 0.0% |

Only agents trading the Oracle's own signal ever beat it; the two trading no
mispricing signal never did in 192 pairs. The edge comes from concentrating
better under the same gross, not from information.

Two things affect the denominator, so quote both with any ratio:

- **Oracle configuration.** Giving it the same information across three times
  as many names makes it worse (median P&L 110k → 70k). Ratios computed with
  different Oracle settings are not comparable.
- **Horizon.** Mispricing reverts on a 60-day half-life. On seed 2026 the same
  momentum agent captures 27% over five days and 94% over sixty.

### Use more than one seed

Over twelve markets the reference agents rank momentum +0.561 and
mean-reversion +0.302. A single seed picks the top agent exactly half the time,
and momentum's own capture runs from +0.075 to +1.133 depending only on which
market it drew.

```python
ranking = pt.rank(lambda: pt.reference_agents(seed=3), seeds=range(12),
                  universe=universe, days=10, workers=4)
print(ranking.report())
ranking.separation("momentum", "mean_reversion")   # 6-6, p = 1.0
ranking.separation("momentum", "buy_and_hold")     # 12-0, p = 0.00049
```

That 2:1 aggregate gap does not establish momentum is better. Paired across the
same twelve markets it wins six and loses six. It wins by more, not more often,
and no average of returns separates those two cases — hence the paired sign
test alongside the number.

`rank` takes a factory rather than built agents, because agents are stateful
and reusing one carries a market's history into the next with no visible
symptom.

The headline figure pools numerators and denominators rather than averaging
per-seed ratios. On short horizons the Oracle sometimes earns almost nothing,
and dividing by it yields a capture of +14.4 that is true of its own seed and
reorders the whole table when averaged in.

---

## Transaction cost analysis

Run the same seed with and without your orders, and price every fill against
what that instrument did in the world where you never traded.

```python
ex = pt.tca.analyse(my_agent, seed=42, universe=universe, days=5)

ex.shortfall_bps()        # what your footprint cost
ex.by_step()              # where it was paid
ex.partial_fills()        # what you asked for versus what you got
ex.untouched_moved()      # should be empty
```

Arrival price, VWAP and fitted impact models are all proxies for a
counterfactual that cannot be run on real data. This one runs it.

Two results to understand before reading a number:

**Negative shortfall is possible on a round trip.** Buying and holding costs
+16.7 bps in one measured example; buying and selling three steps later comes
to −10.8 bps. The entry pushed the price up, part of that persisted, and the
exit sold into it. Shortfall answers what each execution cost, not whether the
strategy made money — read `pnl` from `evaluate` for that. `by_step()` shows
entry and exit separately rather than netting them.

**Check `partial_fills()` before believing a low cost.** A request for 4,856
shares filled 483, the whole displayed depth, and every larger request filled
the same 483. The cheapest execution is usually the one that did not happen.

---

## Scenarios

A rate shock means the rate walking from 2.5% to 5% over thirty days while an
agent holds positions through it. Setting the rate to 5% from the start gives
you a different market instead of an event.

```python
shock = pt.Scenario.rate_shock(start=0.025, end=0.05, over=15)
pt.evaluate(agents, seed=7, universe=universe, days=20, scenario=shock)
```

Measured on seed 7 — same market, same agents, only the macro path differs:

| agent | calm | hiked | delta |
|---|---|---|---|
| buy_and_hold | +3.51% | −0.87% | −4.37 |
| momentum | −2.36% | −0.63% | +1.73 |
| oracle | +20.86% | +20.91% | +0.05 |

Buy-and-hold is long-only and holds through the repricing. Momentum gains
because it can rotate. The Oracle is untouched because it trades mispricing,
and the shock moves fair value along with price.

**One trap.** Pinning `federal_funds_rate` alone does nothing — measured at
exactly 0.00% across twenty instruments. Equities discount off the corporate
bond yield, and the policy rate only applies as a fallback when no yield is
present. `rate_shock` moves the whole curve; `ramp` isolates a single lever
when that is what you want.

Scenarios reach `evaluate`, `tca.analyse` and `run_many` alike:

```python
calm  = pt.tca.analyse(agent, seed=s, universe=u, days=10,
                       scenario=pt.Scenario().hold(vix=15))
spike = pt.tca.analyse(agent, seed=s, universe=u, days=10,
                       scenario=pt.Scenario().hold(vix=45))
```

Does execution cost more in a volatile regime? Paired over twelve seeds, the
volatile regime costs more in 12 of 12, median 4.70 bps against 6.82, paired
median delta +2.99 bps.

Macro counterfactuals are near-exact rather than exact, and `compare()` reports
which. Order flow consumes no RNG draws, so a TCA counterfactual is exact. A
macro path changes prices, prices change which branch the book settlement
takes, and that branch draws either four uniforms or none. Measured divergence:
zero or four draws in 425,600.

`pin_macro` is logged, so a scenario run replays from its own log with no
special handling.

---

## Forking a simulation

Run to day sixty, then ask two questions of the same market, with everything
before the fork identical rather than statistically similar.

```python
mark = pt.Checkpoint.of(engine, universe=universe, seed=42)
calm, hiked = mark.branch(2)
```

Two mechanisms for different jobs:

| | cost | survives the process |
|---|---|---|
| `pt.branch(engine, 2, ...)` | < 1 ms | no |
| `Checkpoint.resume()` | 2.7 s | yes |

`branch` copies engine state — every column plus the generator position — in
constant time. `Checkpoint` replays the order log, three orders of magnitude
slower, and is what you want when the fork has to outlive the process. Cite the
log in a published result, since that is what someone else can re-run.

A `Checkpoint` records the universe fingerprint and refuses to load against a
roster that changed, because restoring across two same-named universes gives
right prices and wrong fair values — plausible everywhere visible, wrong in the
one place that drives everything.

The low-level `restore_state` cannot make that check, since an engine holds no
fundamentals. It verifies roster order and size only. Prefer `branch` and
`Checkpoint`.

---

## Sweeps and parallelism

```python
for seed, table in pt.sweep(range(100), universe=universe, days=252,
                            collect="truth"):
    results.append(pl.from_arrow(table).select(...).mean())
```

`sweep` streams one seed at a time. One recorded engine at 252 days, 390 ticks
and 100 instruments retains twelve buffers of 9.8 million f64 — about 940 MB —
and materialises 9.8 million rows of ground truth. A hundred of those at once
is roughly 90 GB; one at a time is under one, and the analysis is usually a
reduction that never needed them resident.

`list(sweep(...))` puts that memory straight back.

`workers=n` keeps n engines alive, which is an explicit trade rather than a
free speedup, so the default is 1. Seeds always arrive in seed order, never
completion order.

Parallelism is per seed and only per seed. The engine has one shared RNG stream
across the market, the economy and the microstructure, so no decomposition
within a single run preserves the draw schedule.

---

## Reproducing a run

A seed alone does not identify a run. The market an agent trades depends on
that agent's own orders, so one seed with different order flow is a different
market. Reproducing means reproducing every input.

```python
log = engine.order_log                    # plain dicts, JSON-serialisable
same = pt.replay(log, seed=42, universe=universe)
```

That makes a published result replayable without the code that produced it, a
divergence bisectable, and an experiment archivable as data.

Every result object carries the universe fingerprint alongside the seed —
`Scorecard.universe_fingerprint`, `facts.measure()["universe_fingerprint"]`,
`Execution.universe_fingerprint` — because the same seed over a different
roster is a different market.

---

## Real fundamentals from SEC EDGAR

```python
snap = pt.edgar.fetch(as_of="2024-06-30", limit=100,
                      user_agent="Jane Roe jane@example.org")
snap.save("edgar-2024h1.json")            # the artifact, hashed and citable

universe = pt.Universe.from_edgar(snap, federal_funds_rate=0.03)
```

Seeding from filings gives you a cross-section that is real — true valuation
dispersion, actual sector weights, loss-makers in realistic proportion — while
every price path stays synthetic.

Save the snapshot and cite that, not the query. EDGAR is not append-only, so
the same request returns different numbers next year. Snapshots are hashed and
serialisable.

The fetch uses the XBRL frames API: five market-wide requests plus one per
company kept, rather than one per filer. It takes a `transport`, so the
derivation is testable without a socket.

`user_agent` is required and must carry a contact address. The SEC's
fair-access policy asks for one, and this library will not send a fabricated
one on your behalf.

Ranking is by shareholders' equity, because EDGAR carries no market
capitalisation, so the universe skews toward balance-sheet-heavy names.

This loads fundamentals, not behaviour. A loaded ticker gives you a stock with
that company's fundamentals under this model's assumptions — not that company,
not its volatility, not its microstructure.

---

## Reinforcement learning

```python
from pretium.gym import TradingEnv

env = TradingEnv(universe=universe, seed=42, days=20)
obs, info = env.reset(seed=42)
obs, reward, terminated, truncated, info = env.step(action)
```

Passes gymnasium's `env_checker`. Actions are target weights in `[-1, 1]`
rather than share counts, so a policy does not have to learn each instrument's
price range first. Reward is the step's P&L measured after the market moves,
which includes the cost of the agent's own footprint.

```
pip install pretium[rl]
```

---

## How realistic is this market

`pretium.facts.measure()` runs a market and lines its statistics up against
real equities.

```python
print(pt.facts.report(pt.facts.measure(seed=3, universe=universe)))
```

| statistic | measured | real equities | |
|---|---|---|---|
| excess kurtosis | +5.9 | +3 to +10 | matches |
| \|return\| acf(1) | +0.10 | +0.15 to +0.35 | too weak |
| return acf(1) | +0.219 | −0.05 to +0.05 | too high |
| annualised vol | 53% | 15% to 35% | too high |

Fat tails come out right, which is the most robust fact about asset returns and
the one a Gaussian simulator misses. Volatility clusters at about half the real
strength and fades faster.

**Returns are positively autocorrelated here and real ones are not.** +0.219 at
lag one, across six seeds of six, ranging only +0.203 to +0.262. That is the
AR(2) mispricing process showing through: its impulse response rises to 1.284
by day two before reverting, so a shock today is amplified tomorrow.

> Momentum is mechanically profitable in this market in a way it is not in real
> markets. An agent trading serial correlation has an edge that is an artefact
> of the process. If two agents differ mainly in how much of it they exploit,
> their ranking here says very little about which is better anywhere else.

Both mismatches were investigated. The autocorrelation can be corrected by one
constant — `MOMENTUM_THETA` from 0.25 to 0.05 takes it to +0.034, inside the
real band, with volatility and kurtosis unchanged. It also fails seven parity
tests, including the one asserting the model constants match the reference
implementation this library is a port of. Changing that constant makes this a
fork, so the lever sits unpulled and documented.

Clustering resists the available levers. GARCH persistence is already 0.99, and
raising the variance ceiling lifts clustering by 0.016 while pushing volatility
from 52.7% to 72%.

Volatility runs high because a generated universe is deliberately dispersed and
skews small. Prefer ratios — capture against the Oracle, shortfall in basis
points — over raw percentages.

Re-measure after changing the preset, the generator or the scenario.

---

## Conventions

**Rates are fractional.** `0.052` means 5.2%. Passing `5.2` raises, with an
error that says so.

**Absence differs from zero.** `corporate_bond_yield=None` falls through to the
policy rate; `0.0` is a real observation used as given. In columnar reads, where
a column cannot carry `None`, absence is `NaN`, never zero — zero is a real
maker inventory, a real mispricing and a real return.

**Invalid input raises.** `ValidationError` for a malformed scenario,
`OrderError` for a rejected order. Nothing is silently clamped, because a
simulator that repairs your inputs gives you a market you did not specify.

**Negative EPS is legal.** Loss-makers are valued off book value, and a universe
without them is not realistic.

**Short interest is a share count, not a fraction.** The squeeze rule divides it
by the float, so `short_interest=0.03` means three hundredths of one share.
`Universe.random` generates a realistic spread — median about 3.7% of shares
outstanding, with roughly one name in eleven above the 20% squeeze threshold.

**Roster order is contractual.** A re-sorted universe is a different market.
`universe.fingerprint` covers order as well as content.

**Coefficients ship as a preset.** `pt-v1`, named and versioned, rather than as
constructor keywords, so two published results can be compared.
`model_preset()` returns it.

---

## Performance

Measured on one desktop machine. Treat the ratios as portable and the absolute
numbers as a rough guide.

| run | wall clock |
|---|---|
| 252 days x 10 instruments | 2.9s |
| 252 days x 100 instruments | 27.4s |
| 252 days x 100, recording 9.8M rows of ground truth | 28.2s |
| 8 seeds x 21 days x 100, serial | 20.0s |
| 8 seeds x 21 days x 100, 8 workers | 6.1s |

Recording costs about 3% for a full year of tick-grain ground truth, because
raw buffers are kept and Arrow batches are built on read.

Sweeps parallelise about 3.3x on eight cores. The engine releases the GIL for
the whole session compute, and `run_many` uses threads, so it works in a
notebook and does not serialise the universe into each worker.

Cost scales roughly linearly in instruments x days.
