# pretium

A market simulator you can run a strategy against. Rust core, Python API.

Give it a seed and a set of companies. It runs a market forward - prices, a
limit order book, fills, an economy that advances itself daily - and your
orders match against real depth, so trading moves the price the way it would
anywhere else.

Two things you can do here that historical data will never let you do: run the
same market twice with different decisions, and read the true value of every
stock while you trade it.

It's built to show you what breaks a strategy - bad sizing, execution cost, a
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

TWAP, VWAP, POV, icebergs - the work is all in what happens between the
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
counterfactual nobody can run on real data. This one runs it. One boundary,
stated rather than hidden: since VIX began driving the market factor's
variance, the market prices fear of your flow, so names you never traded can
move a few basis points through the fear gauge. That is impact, not error -
and pinning VIX in both worlds makes the subtraction byte-exact again.
[Transaction cost analysis](https://simoncoombes.github.io/pretium/transaction-cost-analysis.html)
measures the channel.

### You're stress-testing a strategy

One history gives you one sample. A strategy that survived 2015-2025 survived
*a* decade, and you can't run the other ones.

Run it across a thousand seeded markets and watch where it breaks. Walk rates
from 2.5% to 5% over fifteen days while it holds positions. Fork a market at
day sixty and take two different paths from an identical state.

```python
shock = pt.Scenario.rate_shock(start=0.025, end=0.05, over=15)
pt.evaluate(agents, seed=7, universe=universe, days=20, scenario=shock)

ranking = pt.rank(lambda: pt.reference_agents(seed=3), seeds=range(12),
                  universe=universe, days=10, workers=4)
ranking.separation("momentum", "mean_reversion")   # wins-losses, sign-test p
```

Use more than one seed. On the twelve-market grid the agents documentation
measures - `Universe.random(30, seed=11)`, ten days, seeds 0 through 11 -
momentum's pooled capture is nearly four times mean-reversion's, yet paired
across the same twelve markets it wins only 7-5, p = 0.77: its lead lives in
how big its wins are, not in how often they come, and a single seed picks the
pooled leader only five times in twelve.

Use it to kill strategies rather than to bless them. Something that dies under
a rate shock, or whose edge evaporates once impact is charged honestly, is
genuinely dead - and you learned it for the price of some CPU.

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

## A result someone else can check

Everything that identifies a run serialises, fingerprints and round-trips: the
seed, the universe, the macro initial conditions, the scenario path, the
model, the strategy, the order log. Three surfaces carry that from "possible"
to "one line".

**A strategy is citable data.** `StrategySpec` writes a strategy down as a
declarative, versioned, hashable document instead of an arbitrary Python
callable, so a result can cite what earned it:

```python
spec = pt.StrategySpec.momentum(lookback_days=1.0, top_k=5)
scores = pt.evaluate({"momentum": spec}, seed=7, universe=universe, days=10)
scores["momentum"].strategy_fingerprint    # sha256 -- cite this
```

A hand-written agent still works everywhere it did; its scorecard's
fingerprint is empty rather than fake, because that result is reproducible
only by citing code at a commit. See
[Strategy specs](https://simoncoombes.github.io/pretium/strategy-specs.html)
for the grammar and its deliberate limits.

**A run is one shareable document.** `RunManifest` embeds every component
above, fingerprints each one, and carries the expected result digest, so the
reader is told they rebuilt the same market rather than eyeballing numbers:

```python
manifest = pt.RunManifest.of(engine, seed=42, universe=universe, strategy=spec)
open("run.json", "w").write(manifest.to_json())

# the reader, with the package and this file and nothing else:
same = pt.RunManifest.from_json(open("run.json").read()).reproduce()
```

`reproduce()` checks the build's arithmetic behaviourally before replaying,
and on any mismatch refuses, naming the culprit - a manifest that silently
reproduced a different market would manufacture false confidence, which is
worse than no manifest at all. See
[Sharing a run](https://simoncoombes.github.io/pretium/sharing-a-run.html).

**A changed model has a different name.** The model's coefficients ship as a
named preset — `pt-v3` is the current default — and are settable at runtime,
with the fingerprint as the honesty mechanism:

```python
custom = pt.ModelParams.from_preset("pt-v1", garch_alpha=0.12)
engine = pt.Engine(seed=42, universe=universe, model=custom)
engine.model_fingerprint     # "custom-0c04c4ba" -- never "pt-v1"
```

A coefficient set bit-identical to the shipped preset fingerprints as the
preset's name; anything else fingerprints as `custom-XXXXXXXX` and cannot
masquerade as the benchmark. Nothing settable changes how many draws are taken
or in what order, so two presets on one seed see identical noise and every
difference in the outcome is a parameter effect. The fingerprint travels
through scorecards, manifests, checkpoints and forks. See
[Model presets](https://simoncoombes.github.io/pretium/model-presets.html).

## Under the hood

**Same seed, same market, on every machine.** The library carries its own
`exp`, `log`, `sin` and `cos` rather than calling the platform's, which is what
normally makes float results drift between operating systems. Every release is
meant to build wheels for five targets, run one fixed simulation inside each,
and compare digests, failing the release on any disagreement
(`.github/workflows/determinism.yml`). What has been measured, by commit:

| commit | known-answer version | platforms built and compared | result |
|---|---|---|---|
| `a5afd1c` | v3 | Windows x86_64, macOS arm64 | identical: `112fd73e...6eff337` |
| `ad91026` | v5 | all five: Linux x86_64 and aarch64, macOS arm64 and x86_64, Windows x86_64 | identical: `76983e65...3180eeb`, each also passing against the committed baseline |
| `6e30497` (current era) | v8 | macOS arm64 only | `1ee64998...fe3581c`, regenerated after this era's model changes; one platform's confirmation until the gate runs again |

The gate has not yet run against a tagged release. The engineering claim - no
platform-varying transcendental reaches the source - is enforced by a test
(`rust/tests/mathx_parity.rs`); read cross-platform bit-identity as measured
for all five targets at `ad91026`, and as engineering intent for the current
baseline. See
[Reproducing a run](https://simoncoombes.github.io/pretium/reproducing-a-run.html)
for the full record.

**The economy runs itself.** The macro chain - a full Taylor/Phillips/Okun
loop - advances at every day close from the initial conditions you construct
the engine with: measured over 120 days on `Universe.random(20, seed=11)`, sim
seed 42, VIX takes a new value every day, the policy fields step at the
central bank's meeting calendar, and fair value reprices per instrument when
the discount rate moves. To impose a path of your own, drive a `Scenario` or
pin a field; a pin overrides the endogenous step from the day it lands. See
[Core concepts](https://simoncoombes.github.io/pretium/core-concepts.html).

**VIX is a volatility lever.** The market factor's variance reverts to a
target that scales with `(VIX/15)^2`, so a pinned VIX moves realised
volatility - 49% annualised at VIX 5, 59% at 15, 107% at 45, 124% at 65
(`Universe.random(20, seed=11)`, 120 days, sim seed 3) - widens the quoted
spread, and above VIX 25.5 blends the cross-section toward the market factor:
mean pairwise correlation reads +0.27 calm, +0.68 at VIX 45, +0.76 at 65
(25 names, same horizon and seed). Diversification fails under stress the way
real crises make it fail. What VIX does not move is any single name's own
noise - it sizes the shared factor, not the idiosyncratic variance.
[Scenarios](https://simoncoombes.github.io/pretium/scenarios.html) has the
numbers and the one trap worth knowing about policy-only rate paths.

**Impact is emergent.** A large order pays worse prices because it ate levels,
with no slippage coefficient anywhere. Holding the same one-day momentum
signal and horizon fixed and changing only how often the strategy re-decides
(seed 2026, `Universe.random(40, seed=7)`, 30 days):

| rebalances per day | return |
|---|---|
| 3 | +97.5% |
| 6 | +33.8% |
| 12 | +0.1% |

No fees are charged. That gap is spread and depth alone.

**Results are columnar.** Five Arrow tables (bars, truth, macro, fills, book)
read zero-copy by polars, pandas, pyarrow and duckdb, and the package depends
on none of them. Runs stream a batch per day, so a hundred-seed sweep at tick
grain never has to fit in memory.

**Fast enough to sweep.** A 252-day year over 100 instruments takes 27 seconds
on the documentation's reference desktop and under seven on an Apple-silicon
laptop. Recording 9.8 million rows of ground truth costs less than wall-clock
timing resolves - measured as interleaved pairs of recorded and plain runs,
nearly half the pairs come out negative. Sweeps parallelise 3-4x on eight
cores.
[Performance](https://simoncoombes.github.io/pretium/performance.html) has the
table.

## What it is bad at

**Good results here don't predict real returns.** The price process comes from
a known model, so a strategy that happens to fit that model's structure will
look brilliant and teach you nothing about real markets. A strategy that
*fails* here has still told you something - it broke against a live order book
under honest impact costs. Read the failures, not the P&L.

**Momentum works here for the wrong reason.** Measured return autocorrelation
is +0.249 at lag one - median of six seeds at the published method,
`Universe.random(40, seed=111)`, 252 days, sim seeds 1 to 6 - where real
equities sit near zero. That's the mispricing process showing through, and an
agent trading it has an edge that won't transfer.

**Realism is a stated envelope, not a score.** `pt.facts.measure()` reports
ten statistics against real-market bands. At a 252-day horizon the shipped
`pt-v3` preset puts **nine of the ten in band**, and - this is the part worth
trusting - it holds the same nine at the same zero band-distance on axes the
calibration never saw: fresh seeds, and a fresh 60-name universe. Five of the
ten were live calibration targets, so an in-band verdict on those is partly
the tuning meeting its target; the held-out axes are what make it more than
that.

The tenth, the autocorrelation of volume changes, fails structurally at
13.7 seed-standard-deviations and is excluded from the calibration objective
deliberately, because an optimiser pointed at an unreachable target does not
fail cleanly - it distorts every other parameter chasing it.

Four further gaps are measured and named rather than assumed: the certified
horizon is 252 days and the model does not hold beyond it, its volatility
memory decays exponentially where real markets' decays hyperbolically, its
tails are too thin over multi-year windows, and scenario response is
directionally right but not calibrated in magnitude.
[The realism envelope](https://simoncoombes.github.io/pretium/realism-envelope.html)
states each gap and what it forbids;
[How realistic is this market](https://simoncoombes.github.io/pretium/how-realistic-is-this-market.html)
is the narrative of what closing each earlier gap took.

**Scale and memory still fail.** Annualised volatility runs 41.5% against a
real 15-35% - a property of the deliberately dispersed generated universes, so
prefer ratios (capture against the Oracle, shortfall in basis points) over raw
percentages. Volume *changes* autocorrelate at -0.45 where real ones sit near
zero, which is structural rather than calibratable: a volume forecast here is
never wrong twice running, so an execution algorithm tested against this
market faces an easier problem than the one it was written for. And
volatility's memory is short - real clustering persists for months; here it is
gone by lag twenty.

**Single venue, no latency, no strategic counterparties.** Orders arrive
instantly, there's one book per name, and you trade against a market maker and
aggregate flow rather than agents that adapt to you.

## Documentation

Full docs: [**simoncoombes.github.io/pretium**](https://simoncoombes.github.io/pretium/)

Scenarios and macro paths, strategy specs, model presets, checkpointing and
forking, sharing a run, Arrow output and streaming sweeps, SEC EDGAR loading,
the RL environment, reproducibility and replay, performance, and the
conventions worth reading before you hit them.

## A worked example

```
python examples/research_workflow.py
```

Seconds end to end - five, measured on an Apple-silicon laptop: universe,
20-seed sweep, five-agent evaluation, TCA checked both ways across the
fear-gauge boundary, 234,000 rows of ground truth, a fork into two macro
futures, a realism report, then archives the run as JSON and replays it to
identical prices.

The test suite runs it. Using the library the way a user would is what caught
the two worst bugs this project has had, both of which had green unit tests.

## Citing this

`CITATION.cff` at the repository root carries the citation metadata in
Citation File Format. GitHub renders it as a "Cite this repository" button and
reference managers read it directly. There's no DOI yet - one hasn't been
invented to fill the gap, and a Zenodo deposit is the next step.

A citation identifies the software, not a run. A methods section needs both.
What identifies a run is the seed, the universe fingerprint and how the
universe was built, the macro initial conditions, the model fingerprint, the
strategy fingerprint, and the archived order log - gathered by hand with
worked examples in
[Reproducing a run](https://simoncoombes.github.io/pretium/reproducing-a-run.html),
or as one self-verifying document by `RunManifest`.

## Licence

Dual-licensed under MIT or Apache-2.0, at your option. See `LICENSE-MIT`,
`LICENSE-APACHE` and `NOTICE`.
