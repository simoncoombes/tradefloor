---
title: Strategy specs
nav_order: 6.5
rack: measure
---

# Strategy specs

Everything else in a run serialises, hashes and round-trips: the seed, the
universe fingerprint, the model preset, the scenario, the order log. The
strategy did not. `evaluate` accepts any object with an `act` method, so the
moment a result depended on an agent it depended on a Python callable that
cannot go in a methods section. A reader could re-run your seed and get your
market, then had no way to get your strategy.

A `StrategySpec` is a strategy as data — declarative, versioned, hashable:

```python
spec = pt.StrategySpec.momentum(lookback_days=1.0, top_k=5)
scores = pt.evaluate({"momentum": spec}, seed=7, universe=universe, days=10)
scores["momentum"].strategy_fingerprint    # sha256 -- cite this
```

The scorecard now carries the strategy's identity next to the seed and the
universe fingerprint, so a methods section becomes:

> Evaluated over 12 seeds against universe `a7861d15...`, model preset
> `pt-v3`, strategy spec `e6bbc35c...`, capture ratio quoted against oracle
> spec `f383b990...`.

Every one of those is checkable by someone who has the package and nothing
else of yours.

## The grammar

The shipped baselines were already parameterised data that had never been
written down as data. Four dimensions repeat across all of them — a signal, a
concentration (`top_k`), an exposure (`gross`), and a participation cap — and
the spec is that grammar as JSON:

```json
{
  "spec_version": 1,
  "signal":    {"kind": "momentum", "lookback_days": 1.0},
  "portfolio": {"gross": 1.0, "top_k": 5},
  "execution": {"cadence": "step", "max_participation": 0.02},
  "seed": null
}
```

`signal.kind` is one of `hold`, `random`, `momentum`, `mean_reversion`,
`oracle`, or `blend`. Each carries only its own parameters, so a reader can
see what the strategy knew. The named constructors default to exactly the
shipped baselines — `pt.StrategySpec.momentum()` builds the class `Momentum()`
is, and there is a test asserting the two score bit-identically:

```python
pt.StrategySpec.hold()                 # BuyAndHold
pt.StrategySpec.random(seed=0)         # RandomTrader; its seed is IN the spec
pt.StrategySpec.momentum()             # Momentum
pt.StrategySpec.mean_reversion()       # MeanReversion
pt.StrategySpec.oracle()               # Oracle
```

`hold` and `random` take no `top_k` — one owns the whole roster and the other
weights every name, so a concentration parameter on either would describe
nothing, and the spec refuses it rather than carrying a dead field.

Round-trips hold in both directions: `StrategySpec.from_json(s.to_json()) ==
s`, and `s.build().spec is s`. Without both, this page would be describing
documentation rather than a specification.

## Blends

A single signal is a weak strategy, and the interesting comparisons are
mixtures:

```python
blend = pt.StrategySpec.blend([
    {"kind": "momentum",       "weight": 0.6, "lookback_days": 1.0},
    {"kind": "mean_reversion", "weight": 0.4, "lookback_days": 5.0},
], top_k=10)
```

Weights sit on the signal, not the portfolio, because blending two rankings
and then selecting `top_k` is a different strategy from selecting `top_k`
from each and merging — the spec is unambiguous about which. Each component
ranks the roster (ties broken on ticker, exactly as the baselines break
them); the blended score is the weighted sum of ranks. Ranks rather than raw
scores, because a one-day return, a five-day return and a log mispricing have
incomparable units, and raw scores would be weighted by their variances
rather than by the weights.

`random` is legal as a component: it contributes a uniformly random ranking,
which dilutes a signal with noise — a useful ablation. That is deliberately
not the same strategy as the bare `random` signal, whose weights carry the
draws' magnitudes as well as their order, and the spec keeps the two
distinct.

**Weights are normalised at construction**, to unit absolute mass, signs
preserved. Selection ranks the blended score and takes the top k, so the
agent is invariant under any positive scaling of the weight vector — weights
of `1.2/0.8` and `0.6/0.4` build bit-identical agents. Taking weights as
given would therefore let two textually different specs name the same
strategy under different fingerprints: identity finer than the thing
identified, which defeats comparability from the opposite direction to
semantic drift. What normalisation costs is nothing behavioural — ratios and
signs survive, so a net-short-signal tilt is still expressible with a
negative weight.

The same principle drives two more canonicalisations: identical components
merge (the same signal named twice is one signal with the summed weight), and
a blend of one ranked signal at weight one collapses to the bare signal,
because the rank of a score orders exactly as the score.

## The fingerprint

`spec.fingerprint` is a sha256 over the canonical serialisation: sorted keys,
no whitespace, defaults materialised, weights normalised, components merged
and sorted, `spec_version` included. Whitespace, key order, writing a default
explicitly, or scaling every blend weight by two all leave it unchanged.

The hash is over content rather than keystrokes for the same reason the
universe fingerprint is: a fingerprint that moved when formatting changed
would be worse than none, because it would look stable while identifying
nothing. And `to_json` always writes the full canonical form — a reader of
the JSON sees every parameter the strategy ran under, including the ones the
author never typed.

## The version pins semantics, not syntax

`spec_version` says what the words mean. If `momentum` ever changes what it
ranks, or a blend changes how it combines, that is `spec_version: 2` —
exactly as a coefficient change is a new model preset rather than an edit to
`pt-v1`. Adding a new signal kind or cadence value is not a version bump:
old specs keep meaning what they meant.

A spec with a newer version than the installed package is refused rather than
read on a best-effort basis, as are unknown fields anywhere in the document.
A field silently dropped would round-trip to a strategy nobody wrote, while
fingerprinting as though nothing happened.

The test suite pins reference fingerprints as hard-coded constants. If one of
those tests fails, the canonical form — and with it the meaning of every
published fingerprint — has changed, and the failure message says what that
demands: a version bump, not a patch.

## Cadence is in the spec

How often a strategy re-decides moves results more than any signal parameter:
the rebalance measurement behind `pretium.baselines` swings the same one-day
signal from +97.5% at three decisions a day to +0.1% at twelve, purely by
trading it more often (seed 2026, `Universe.random(40, seed=7)`, 30 days,
measured at `a7994e2`). A strategy whose identity excluded that would not be
identified — two runs of the same fingerprint could differ by the whole
return.

So `execution.cadence` is part of the spec, with two values:

- `"step"` — the default, and what every shipped baseline does: re-decide at
  every decision step the harness offers. The trading frequency is then the
  harness's `steps_per_day`, so a methods section quoting a step-cadence spec
  **must quote `steps_per_day` beside `days` and the seed**.
- `"daily"` — re-decide on the first step of each day, whatever
  `steps_per_day` the harness runs. The wrapped strategy sees a daily view:
  a one-day lookback is one daily observation, and a random signal draws
  once per decision, not once per harness step.

The line between spec and harness: the spec pins the strategy's own decision
rule; `steps_per_day` stays a harness parameter because it also sets how
often every agent is observed, which is experimental apparatus. `hold`
refuses a cadence outright — it trades once, so a cadence on it describes
nothing, and two spellings of one strategy must not fingerprint apart.

## `evaluate` takes specs directly

Anywhere an agent goes, a spec goes. `evaluate` builds it freshly inside
every call, which closes a real trap: agents are stateful — `BuyAndHold`
trades once ever, `Momentum` keeps a rolling window — so a built instance
reused across two evaluations carries the first market's history into the
second with no visible symptom. A spec cannot, because it is not the agent;
it is the instruction for building one. `rank` takes a factory for exactly
this reason, and a factory returning specs is the simplest correct one:

```python
ranking = pt.rank(lambda: {"momentum": pt.StrategySpec.momentum(),
                           "oracle": pt.StrategySpec.oracle()},
                  seeds=range(12), universe=universe, days=10)
```

A hand-built agent still works everywhere it did. Its scorecard's
`strategy_fingerprint` is empty — the honest reading, because such a result
is reproducible only by citing code at a commit.

## The oracle is in the grammar on purpose

A spec naming `oracle` declares privileged access to state no real trader
has, which is exactly what a reviewer needs to see. Leaving it out would push
the one strategy most in need of disclosure into the uncitable escape hatch.
Spec-built oracles — and any blend containing one — carry `privileged =
True`, so a results table can label the row.

Its `top_k` matters more than any other parameter in the library: capture
ratios are quoted against the Oracle, and its configuration moves the
denominator substantially (see [Agents and
evaluation](agents-and-evaluation.html)). A capture ratio without the
oracle's spec fingerprint attached is not a number anyone can compare —
which is why the methods section at the top of this page quotes two
fingerprints, not one.

## What a spec cannot express

Stated plainly, because the limit is the design rather than an omission:

- **Path dependence.** Stop losses, drawdown limits, anything reading its
  own P&L history.
- **Conditional logic.** "Momentum in calm markets, reversion in stress."
- **Custom signals.** Anything not in the registry.
- **Anything reading engine internals** except through `oracle`, which
  declares itself.

The escape hatch stays open: write a Python agent, as always. The cost is
that the result is not citable as a spec — the methods section cites code at
a commit instead, and the scorecard's empty `strategy_fingerprint` records
the difference. That is an honest trade, and stating it is what stops this
grammar from sprawling into a programming language. A strategy the grammar
cannot express is not a reason to grow the grammar; it is a reason to use
the escape hatch and say so.
