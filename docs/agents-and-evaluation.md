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

The Oracle measures what knowing fair value earns under these constraints, a
question a real market cannot be asked, because answering it means observing
fair value.

Treat it as a reference point rather than a ceiling. It gets the same gross
exposure and participation cap as everything else and spends them on a naive
rule - equal weight, long the five most underpriced names and short the five
most overpriced (`top_k=5` per side). Perfect knowledge of the mispricing
does not make that the best portfolio the same gross can buy, and agents do
out-earn it. Counted on the twelve-market grid of the next section:

| agent | beats the Oracle |
|---|---|
| momentum | 4 of 12 |
| mean_reversion | 1 of 12 |
| buy_and_hold | 1 of 12 |
| random | 0 of 12 |

Momentum clears it most often. The Oracle knows the *level* of mispricing
exactly, but it spends that knowledge on a fixed rule, and this market pays
for something the rule ignores - return continuation, which is all momentum
trades. The other two wins are single markets, and they are instructive:
mean-reversion, which does the Oracle's trade by estimation instead of
revelation, out-earns the revealed version exactly once and barely (capture
1.004), while buy-and-hold's one win is the largest capture on the whole
grid - 1.59, a market that happened to drift far enough that holding it
beat trading the true mispricing through a fixed rule. Only random never
beats it. So a capture ratio above 1.0 is legal and does occur: read it as
the agent harvesting edge the Oracle's naive rule leaves on the table, not
as a broken denominator.

Two things affect the denominator, so quote both with any ratio:

- **Oracle configuration.** Spreading the same information across three times
  as many names makes it worse: on the ranking grid below at ten days, its
  median P&L over sim seeds 0-7 is $93k at the default `top_k=5` and $65k at
  `top_k=15`. Ratios computed with different Oracle settings are not
  comparable.
- **Horizon.** Mispricing reverts on a 60-day half-life, so the Oracle's
  earnings grow with the run: evaluating `pt.reference_agents(seed=3)` on
  seed 2026 over `Universe.random(40, seed=7)` at the `evaluate` defaults,
  it makes $85k in five days and $504k in sixty. The same momentum agent
  scores a capture ratio of 0.40 against the first denominator and 1.24
  against the second - same market, same agent, and the horizon alone
  carries the verdict across 1.0: the five-day reading says momentum
  captured forty percent of what knowing fair value earned, the sixty-day
  reading says it out-earned it. Ratios measured at different horizons are
  not comparable.

### Use more than one seed

Over twelve markets - ten days each on the thirty-name universe
`Universe.random(30, seed=11)`, seeds 0 through 11 - the reference agents
rank momentum +0.593 pooled and mean-reversion +0.160. A single seed picks
the pooled leader only five times in twelve here, and equally often crowns
mean-reversion, pooled at just over a quarter of momentum's capture. Nor
does a lucky draw settle the size: what a seed says momentum is worth runs
from +0.089 to +1.523 depending only on which market it drew, the same
agent, from under a tenth of the Oracle's take to out-earning it. One seed
can miss the winner outright, and even when it names it, miss the size of
the verdict by its whole width.

```python
ranking = pt.rank(lambda: pt.reference_agents(seed=3), seeds=range(12),
                  universe=universe, days=10, workers=4)
print(ranking.report())
ranking.separation("momentum", "mean_reversion")   # 7-5, p = 0.77
ranking.separation("momentum", "random")           # 12-0, p = 0.0005
```

An aggregate gap cannot say whether momentum wins more often or merely wins
bigger when it wins, and no average of returns separates those two cases -
hence the paired sign test alongside the number. Here the two disagree, and
the disagreement is the finding: momentum's pooled capture is nearly four
times mean-reversion's, yet paired across the same twelve markets it wins
only 7-5, p = 0.77 - a coin flip. Its lead lives in how big its wins are,
not in how often they come, which is exactly what pooling sees and a sign
test ignores. Against random the same test is a clean sweep, 12-0, and even
that only reaches p = 0.0005, the floor twelve paired seeds can produce.
The window travels with the verdict too: the identical
momentum-versus-mean-reversion test over seeds 12-23 reads 9-3, p = 0.15 -
so a p-value carries its seed set with it, and one twelve-seed window's p
is a single draw of a noisy statistic.

`rank` takes a factory rather than built agents, because agents are stateful
and reusing one carries a market's history into the next with no visible
symptom.

The headline figure pools numerators and denominators rather than averaging
per-seed ratios. On short horizons some markets offer the Oracle little to
earn: re-run the ranking above at three days over seeds 0-9 and its
per-seed P&L spans $11.2k to $45.6k. On the thinnest of those markets
mean-reversion's ratio is +1.29, against a pooled +0.44 across the ten -
and every ratio above 1.0 it posts sits on one of the four thinnest
denominators. The +1.29 is true of its own seed - the agent really did
out-earn the Oracle there - but it is a fact about an $11.2k denominator on
a $1M book, and averaging the ten ratios instead of pooling them would move
the verdict to +0.61, over a third again. Pooling weights each market by
the opportunity that actually existed in it, so the seeds with least to
earn cannot outvote the rest.
