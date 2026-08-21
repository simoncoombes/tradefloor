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
market — on Linux, macOS and Windows, because the library ships its own
transcendental maths rather than calling the platform's libm. Every release
runs one fixed simulation on every wheel target and compares digests; no wheel
ships that disagrees with the others.

**It knows why every price moved.** The simulator computed the reasons, so it
can report them: how much of a move was company news, order-flow pressure, a
short squeeze, or noise. You can observe from history that a stock fell. You
cannot observe that sixty per cent of the fall was order flow — no real dataset
carries that column.

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
cf = pt.counterfactual(seed=42, universe=universe,
                       order_flow={"AAA": (6_000_000, 0)})
cf.cost_bps("AAA")        # what your own footprint cost you
```

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
| `truth` | fair value and mispricing per instrument |
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

## Install

```
pip install pretium              # core, no dependencies
pip install pretium[rl]          # + numpy, gymnasium
pip install pretium[arrow]       # + pyarrow
```

Wheels are abi3 and cover CPython 3.11 and later.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
