---
title: Agents and evaluation
nav_order: 6
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

The Oracle measures how much was there to earn, a question a real market cannot
be asked, because answering it means observing fair value.

Treat it as a reference point rather than a ceiling. It gets the same gross
exposure and participation cap as everything else and spends them on a naive
rule - equal weight across the ten most mispriced names. Over 384 agent-seed
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
  as many names makes it worse (median P&L 110k -> 70k). Ratios computed with
  different Oracle settings are not comparable.
- **Horizon.** Mispricing reverts on a 60-day half-life. On seed 2026 the same
  momentum agent captures 27% over five days and 94% over sixty.

### Use more than one seed

Over twelve markets the reference agents rank momentum +0.556 and
mean-reversion -0.011. A single seed picks the top agent exactly half the time,
and momentum's own capture runs from -0.335 to +1.903 depending only on which
market it drew.

```python
ranking = pt.rank(lambda: pt.reference_agents(seed=3), seeds=range(12),
                  universe=universe, days=10, workers=4)
print(ranking.report())
ranking.separation("momentum", "mean_reversion")   # 7-5,  p = 0.77
ranking.separation("momentum", "random")           # 10-2, p = 0.039
```

That aggregate gap does not establish momentum is better. Paired across the
same twelve markets it wins seven and loses five. It wins by more, not much
more often, and no average of returns separates those two cases - hence the
paired sign test alongside the number. Against random the same test reads
10-2, p = 0.039.

`rank` takes a factory rather than built agents, because agents are stateful
and reusing one carries a market's history into the next with no visible
symptom.

The headline figure pools numerators and denominators rather than averaging
per-seed ratios. On short horizons the Oracle sometimes earns almost nothing,
and dividing by it yields a capture of +14.4 that is true of its own seed and
reorders the whole table when averaged in.
