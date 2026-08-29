---
title: Agents and evaluation
nav_order: 6
rack: measure
---

# Agents and evaluation

An agent sees prices, the order book and its own positions. It does not see
fair value, mispricing or the attribution - inferring those is the task, and
they are used for scoring on the other side of the wall.

```python
from tradefloor.baselines import Momentum, MeanReversion

scores = tf.evaluate({"momentum": Momentum(), "mean_reversion": MeanReversion()},
                     seed=2026, universe=universe, days=5, max_leverage=2.0)

for s in tf.leaderboard(scores):
    print(s.name, s.pnl, s.impact_bps, s.explanation_accuracy)
```

The baseline classes live in `tradefloor.baselines`, not on the top-level
`tradefloor` namespace. On `Universe.random(30, seed=11)` that block prints:

```
mean_reversion 23732.512000050978 10.323275734330448 None
momentum 9369.088040002855 34.437344276334585 None
```

If an agent implements `explain()`, its stated reason is checked against what
actually drove the move. `explanation_accuracy` is `None` above because
neither baseline implements it: there is nothing to score, and a zero would
read as a wrong answer rather than as no answer.

### Reference points

A P&L of $61,000 means nothing alone. `tradefloor.baselines` ships buy-and-hold,
random, momentum and mean-reversion agents, plus an **Oracle** that reads the
true mispricing and trades it without estimation error.

```python
scores = tf.evaluate(tf.reference_agents(), seed=2026, universe=universe, days=60)
tf.capture_ratio(scores)     # each agent's P&L as a fraction of the Oracle's
```

The Oracle measures what knowing fair value earns under these constraints, a
question a real market cannot be asked, because answering it means observing
fair value.

Treat it as a reference point rather than a ceiling. It gets no extra
capital: gross exposure and the 2%-of-ADV participation cap are the same ones
the momentum and mean-reversion baselines get, and it spends them on a naive
rule - equal weight, long the five most underpriced names and short the five
most overpriced (`top_k=5` per side). Perfect knowledge of the mispricing
does not make that the best portfolio the same gross can buy, and agents do
out-earn it. Counted on the twelve-market grid of the next section, under
the shipped default model `pt-v14`:

| agent | beats the Oracle |
|---|---|
| mean_reversion | 5 of 12 |
| buy_and_hold | 1 of 12 |
| momentum | 0 of 12 |
| random | 0 of 12 |

Mean-reversion clears it most often, and it is the instructive case: it is
the agent doing the Oracle's own trade by estimation instead of revelation.
Mispricing reverts on a 60-day half-life, so part of any recent fall is
mispricing that will come back, and buying the recent losers is a noisy read
of the same signal the Oracle gets exactly - which is why its capture climbs
with the horizon, as the Horizon bullet below measures. With no privileged
read of `mispricing_s` at all it still out-earns the revealed version on five
of twelve markets, the largest at capture 1.520. What it has that the Oracle
does not is a ranking that turns over: `Oracle.act` sorts on the *level* of
`mispricing_s` and puts the whole book on the two extremes of it, equal
weight, every step - and a level on a 60-day half-life barely moves inside a
ten-day window, so it holds the same names rather than trading them. On seed
0 of the grid its selected ten change on eleven of the run's fifty-nine step
transitions, and it turns over $5.4M against mean-reversion's $52.4M on the
same market. Knowing the level exactly is not the same as knowing which of
those names will converge inside the window, and the fixed rule has no way
to express the difference.

Buy-and-hold's single win is capture 1.016, a market that happened to drift
far enough that holding it beat trading the true mispricing through a fixed
rule. Momentum and random never beat it here - momentum's best market on the
grid is capture 0.909, its worst -0.503. So a capture ratio above 1.0 is
legal and does occur: read it as the agent harvesting edge the Oracle's naive
rule leaves on the table, not as a broken denominator.

Two things affect the denominator, so quote both with any ratio:

- **Oracle configuration.** Spreading the same information across three times
  as many names makes it worse: on the ranking grid below at ten days, its
  median P&L over sim seeds 0-7 is $60.0k at the default `top_k=5` and $48.1k
  at `top_k=15`. Ratios computed with different Oracle settings are not
  comparable.
- **Horizon.** Mispricing reverts on a 60-day half-life, so the Oracle's
  earnings grow with the run: evaluating `tf.reference_agents(seed=3)` on
  seed 2026 over `Universe.random(40, seed=7)` at the `evaluate` defaults,
  it makes $77.4k in five days and $565.4k in sixty. The same mean-reversion
  agent scores a capture ratio of 0.066 against the first denominator and
  1.18 against the second - same market, same agent, and the horizon alone
  carries the verdict across 1.0: the five-day reading says mean-reversion
  captured under seven percent of what knowing fair value earned, the
  sixty-day reading says it out-earned it. The direction is not a quirk of
  that one seed. Over sim seeds 2026 and 0 through 4 on the same roster,
  mean-reversion averages +0.42 at five days and +1.02 at sixty, and it beats
  momentum on four of the six at five days and on all six at sixty. The
  half-life is why the horizon moves the reading at all: five days converges
  about six percent of a 60-day mispricing, so both the Oracle's dollars and
  a noisy estimate of the same signal have had almost nothing to earn yet.
  Ratios measured at different horizons are not comparable.

### Use more than one seed

Over twelve markets - ten days each on the thirty-name universe
`Universe.random(30, seed=11)`, seeds 0 through 11 - the reference agents
rank mean-reversion +0.783 pooled and momentum +0.259. A single seed picks
the pooled leader eight times in twelve here; the other four crown momentum
three times and buy-and-hold once, and buy-and-hold pools at +0.001, which
is to say at nothing. Nor does a lucky draw settle the size: what a seed
says momentum is worth runs from -0.503 to +0.909 depending only on which
market it drew, the same agent, from losing half of what the Oracle made to
capturing nine tenths of it. One seed can miss the winner outright, and even
when it names it, miss the size of the verdict by its whole width.

```python
ranking = tf.rank(lambda: tf.reference_agents(seed=3), seeds=range(12),
                  universe=universe, days=10, workers=4)
print(ranking.report())
ranking.separation("mean_reversion", "momentum")   # 9-3,  p = 0.15
ranking.separation("mean_reversion", "random")     # 12-0, p = 0.0005
ranking.separation("buy_and_hold", "random")       # 5-7,  p = 0.77
```

`report()` prints the pooled captures, their per-seed ranges, and how many of
the twelve seeds each agent topped:

```
12 seeds on universe 4d4410dfcc14... under model pt-v12
  mean_reversion    capture +0.783  per-seed [+0.191, +1.520]  wins 8/12
  momentum          capture +0.259  per-seed [-0.503, +0.909]  wins 3/12
  buy_and_hold      capture +0.001  per-seed [-0.905, +1.016]  wins 1/12
  random            capture -0.040  per-seed [-0.274, +0.124]  wins 0/12
```

An aggregate gap cannot say whether an agent wins more often or merely wins
bigger when it wins, and no average of returns separates those two cases -
hence the paired sign test alongside the number. Mean-reversion's pooled
capture is three times momentum's, and the sign test agrees on the
direction, 9-3 - but it lands at p = 0.15 and `decisive` comes back
`False`, a flag `separation` sets only on a clean sweep and never off a
p-value threshold. Twelve paired seeds cannot separate even a three-to-one
pooled gap from chance. Against random the same test is a clean sweep,
12-0, and even that only reaches p = 0.0005, the floor twelve paired seeds
can produce: at this many seeds a sign test cannot report more confidence
than that however wide the gap gets.

Where the two measures genuinely disagree is lower down the table.
Buy-and-hold pools ahead of random, +0.001 against -0.040, yet paired it
loses 5-7. Its whole pooled standing rests on one market - seed 1, capture
+1.016, the only seed on which it beats the Oracle - while it trails random
on seven of the twelve. Its lead lives in how big its one win is, not in how
often wins come, which is exactly what pooling sees and a sign test ignores.
Read either alone and you get a different agent.

The window travels with the verdict too: the identical
mean-reversion-versus-momentum test over seeds 12-23 reads 10-2, p = 0.04 -
so a p-value carries its seed set with it, and one twelve-seed window's p
is a single draw of a noisy statistic.

`rank` takes a factory rather than built agents, because agents are stateful
and reusing one carries a market's history into the next with no visible
symptom.

The headline figure pools numerators and denominators rather than averaging
per-seed ratios. On short horizons some markets offer the Oracle little to
earn: re-run the ranking above at three days over seeds 0-9 and its
per-seed P&L spans $13.7k to $32.7k. On the thinnest of those markets
mean-reversion's ratio is +1.57, against a pooled +0.78 across the ten -
and three of the four ratios above 1.0 it posts sit on the four thinnest
denominators. The +1.57 is true of its own seed - the agent really did
out-earn the Oracle there - but it is a fact about a $13.7k denominator on
a $1M book, and averaging the ten ratios instead of pooling them would move
the verdict to +0.88, an eighth again. Pooling weights each market by
the opportunity that actually existed in it, so the seeds with least to
earn cannot outvote the rest.
