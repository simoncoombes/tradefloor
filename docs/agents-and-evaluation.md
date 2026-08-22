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
| momentum | 5 of 12 |
| mean_reversion | 0 of 12 |
| buy_and_hold | 0 of 12 |
| random | 0 of 12 |

Only momentum ever beat it here. The Oracle knows the *level* of mispricing
exactly, but it spends that knowledge on a fixed rule, and this market pays
for something the rule ignores - return continuation, which is all momentum
trades. Mean-reversion, which does the Oracle's trade by estimation instead
of revelation, never out-earned the revealed version, and the two agents
trading no signal at all never beat it either. So a capture ratio above 1.0
is legal and does occur: read it as the agent harvesting edge the Oracle's
naive rule leaves on the table, not as a broken denominator.

Two things affect the denominator, so quote both with any ratio:

- **Oracle configuration.** Spreading the same information across three times
  as many names makes it worse: on the ranking grid below at ten days, its
  median P&L over sim seeds 0-7 is $87k at the default `top_k=5` and $71k at
  `top_k=15`. Ratios computed with different Oracle settings are not
  comparable.
- **Horizon.** Mispricing reverts on a 60-day half-life, so the Oracle's
  earnings grow with the run: on seed 2026 over `Universe.random(40, seed=7)`
  it makes $21k in five days and $568k in sixty. The same momentum agent
  scores a capture ratio of 2.98 against the first denominator and 1.47
  against the second - same market, same agent, and the ratio halves because
  the horizon moved. Ratios measured at different horizons are not
  comparable, and the five-day reading is exactly the small-denominator
  ratio this page closes on.

### Use more than one seed

Over twelve markets - ten days each on the thirty-name universe
`Universe.random(30, seed=11)`, seeds 0 through 11 - the reference agents
rank momentum +0.805 pooled and mean-reversion +0.064. A single seed picks
the pooled leader ten times in twelve here, but what it says that leader is
worth runs from -0.169 to +1.672 depending only on which market it drew: the
same agent, from money-losing to Oracle-beating. One seed can name the
winner and still miss the size of the verdict by its whole width.

```python
ranking = pt.rank(lambda: pt.reference_agents(seed=3), seeds=range(12),
                  universe=universe, days=10, workers=4)
print(ranking.report())
ranking.separation("momentum", "mean_reversion")   # 11-1, p = 0.006
ranking.separation("momentum", "random")           # 11-1, p = 0.006
```

An aggregate gap cannot say whether momentum wins more often or merely wins
bigger when it wins, and no average of returns separates those two cases -
hence the paired sign test alongside the number. Here the test agrees with
the aggregate: paired across the same twelve markets, momentum beats
mean-reversion on eleven and random on eleven, p = 0.006 for both. Do not
read that as settled, though. The identical test over seeds 12-23 puts
momentum over mean-reversion 9-3, p = 0.15: twelve paired seeds is a small
experiment - even a clean sweep only reaches p = 0.0005 - so a p-value
carries its seed set with it, and one twelve-seed window's p is a single
draw of a noisy statistic.

`rank` takes a factory rather than built agents, because agents are stateful
and reusing one carries a market's history into the next with no visible
symptom.

The headline figure pools numerators and denominators rather than averaging
per-seed ratios. On short horizons the Oracle sometimes earns almost
nothing: re-run the ranking above at three days over seeds 0-9 and its
per-seed P&L spans $7.2k to $41.7k. On the thinnest of those markets
mean-reversion's ratio is +2.8, against a pooled +0.27 across the ten. The
+2.8 is true of its own seed - the agent really did earn 2.8 times what the
Oracle did there - but it is a fact about a $7.2k denominator on a $1M book,
and an average of ratios would let that one market outvote the other nine.
Pooling weights each market by the opportunity that actually existed in it,
so a seed with almost nothing to earn cannot dominate the verdict.
