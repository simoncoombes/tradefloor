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
linux-x86_64     112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
linux-aarch64    112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
macos-arm64      112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
macos-x86_64     112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
windows-x86_64   112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337
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

Which means an agent cannot win by turning a dial up. Holding the signal and
the horizon fixed at one day and changing only how often it rebalances:

| rebalances per day | return |
|---|---|
| 3 | +88.7% |
| 6 | +30.9% |
| 12 | −13.2% |

Nothing charges a fee. The orders simply cross a real spread and consume real
depth four times as often.

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

That warning is usually vague. Here it is specific, because the structure is
measured — see below.

## How realistic is this market?

`pretium.facts.measure()` runs a market and lines its statistics up against
real equities. The mismatches are the point, and they are not tuned away:

| statistic | measured | real equities | |
|---|---|---|---|
| excess kurtosis | **+5.9** | +3 to +10 | matches |
| \|return\| acf(1) | +0.10 | +0.15 to +0.35 | too weak |
| return acf(1) | **+0.219** | −0.05 to +0.05 | **too high** |
| annualised vol | 53% | 15% to 35% | too high |

Fat tails are right — the single most robust fact about asset returns, and the
one a Gaussian simulator gets wrong. Volatility clusters, but about half as
strongly as reality and it fades faster.

**Returns are positively autocorrelated and real ones are not.** +0.219 at lag
one, in six seeds of six, ranging only +0.203 to +0.262. That is the AR(2)
mispricing process showing through: its impulse response *rises* to 1.284 by
day two before reverting, so a shock today is amplified tomorrow. Two
independent measurements of one mechanism — the process's own impulse response,
and the autocorrelation of the prices it produces.

The consequence, which you should carry into any conclusion drawn here:

> **Momentum is mechanically profitable in this market in a way it is not in
> real markets.** An agent trading serial correlation has an edge that is an
> artefact of the process, not a skill that transfers. If two agents differ
> mainly in how much of it they exploit, their ranking here says very little
> about which is better anywhere else.

Volatility is high because a generated universe is deliberately dispersed and
skews small. Prefer ratios — capture against the oracle, shortfall in basis
points — to raw percentages.

```python
print(pt.facts.report(pt.facts.measure(seed=3, universe=universe)))
```

Re-measure after changing the preset, the generator or the scenario. A claim
about realism should be checked, not inherited.

## Fork a simulation mid-flight

Run to day sixty, then ask two questions of the same market — everything
before the fork identical, not statistically similar but *identical*.

```python
mark = pt.Checkpoint.of(engine, universe=universe, seed=42)
calm, hiked = mark.branch(2)
```

A third counterfactual, distinct from the other two: `tca.analyse` asks what
your trading cost, `scenario.compare` asks what a macro path did, and this asks
what happens **next** from a state you have already reached and want to keep.

Two ways to fork, for different jobs:

| | cost | survives the process |
|---|---|---|
| `pt.branch(engine, 2, ...)` | **< 1 ms** | no |
| `Checkpoint.resume()` | 2.7 s | yes |

`branch` copies the engine's state — every column plus the generator position
— in constant time. `Checkpoint` replays the order log, three orders of
magnitude slower, and is the one you want when the fork has to outlive the
process. A snapshot is a *state*; a log is a *history*, and a published result
cites the history because that is what someone else can re-run.

**Tickers are not identity.** They are generated positionally, so
`Universe.random(40, seed=1)` and `Universe.random(40, seed=99)` share every
name and share no earnings. Use `universe.fingerprint` — a sha256 over the
roster's canonical form — anywhere you need to ask whether two universes are
the same one.

Every result carries it. `Scorecard.universe_fingerprint`,
`facts.measure()["universe_fingerprint"]` and
`Execution.universe_fingerprint` all name the market they came from, alongside
the seed — because a seed alone does not identify a market, and the same seed
over a different roster is a different one.

A `Checkpoint` records it and refuses to load against a roster that arrived
changed, because restoring across two same-named universes gives right prices
and wrong fair values: plausible in every visible way, wrong in the one that
drives everything.

The low-level `restore_state` cannot do that check — an engine holds no
fundamentals, so it verifies roster order and size and nothing more. Prefer
`branch` and `Checkpoint`, which have the universe and use it.

## Scenarios are paths, not settings

A rate shock is not "the rate is 5%". It is the rate walking from 2.5% to 5%
over thirty days while an agent holds positions through it. The first is a
different market; the second is an event, and events are what a strategy
either survives or does not.

```python
shock = pt.Scenario.rate_shock(start=0.025, end=0.05, over=15)
pt.evaluate(agents, seed=7, universe=universe, days=20, scenario=shock)
```

Measured on seed 7 — the same market, the same agents, only the macro path
differs:

| agent | calm | hiked | delta |
|---|---|---|---|
| buy_and_hold | +3.51% | −0.87% | **−4.37** |
| momentum | −2.36% | −0.63% | **+1.73** |
| oracle | +20.86% | +20.91% | +0.05 |

Buy-and-hold is long-only and holds through the repricing. Momentum *gains*,
because it can rotate. The oracle is untouched, because it trades mispricing
and the shock moves fair value with it. You cannot re-run a year without its
hiking cycle; here you can, holding every noise draw fixed.

**One trap worth knowing.** Pinning `federal_funds_rate` alone does nothing —
measured, exactly 0.00% across twenty instruments. Equities discount off the
corporate bond yield, and the policy rate is only the fallback when no yield is
present. `rate_shock` moves the whole curve for you; `ramp` lets you isolate a
single lever when that is what you actually want.

Scenarios reach `evaluate`, `tca.analyse` and `run_many` alike, which makes
questions like this answerable exactly:

```python
calm  = pt.tca.analyse(agent, seed=s, universe=u, days=10, scenario=pt.Scenario().hold(vix=15))
spike = pt.tca.analyse(agent, seed=s, universe=u, days=10, scenario=pt.Scenario().hold(vix=45))
```

*Does execution cost more in a volatile regime?* Yes, and by how much: paired
over twelve seeds, the volatile regime costs more in **12 of 12**, median 4.70
bps against 6.82, paired median delta **+2.99 bps**. You cannot re-run last
week's execution with the volatility turned down; here both worlds run the
identical macro path, so the difference is the trading and not the regime.

A scenario run also replays from its own log with no special handling —
`pin_macro` is logged, so the path is captured in the archive.

Macro counterfactuals are *near*-exact rather than exact, and `compare()`
reports which. Order flow consumes no RNG draws, so a TCA counterfactual is
exact; a macro path changes prices, prices change which branch the book
settlement takes, and that branch draws four uniforms or none. Measured
divergence: zero or four draws in 425,600.

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
policy rate; `0.0` is a real observation used as given. In columnar reads,
where a column cannot carry `None`, absence is `NaN` — never zero, because
zero is a real maker inventory, a real mispricing and a real return.

**Invalid input raises.** `ValidationError` for a malformed scenario,
`OrderError` for a rejected order. Nothing is silently clamped: a simulator
that helpfully repairs your inputs produces a market nobody specified.

**Negative EPS is legal.** Loss-makers are valued off book value. A universe
without them is not a realistic universe.

**Short interest is a share COUNT, not a fraction.** The squeeze rule divides
it by the float, so `short_interest=0.03` means three hundredths of one share,
not three per cent. `Universe.random` generates a realistic spread — median
about 3.7% of shares outstanding, with roughly one name in eleven above the
20% threshold where a squeeze can fire.

**Roster order is contractual.** The engine iterates instruments in index
order and draws as it goes, so a re-sorted universe is a different market.
`universe.fingerprint` covers order as well as content, so a re-sort changes
the hash.

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
