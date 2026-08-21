---
title: How realistic is this market
nav_order: 15
rack: reference
short: Realism
---

# How realistic is this market

`pretium.facts.measure()` runs a market and lines its statistics up against
real equities.

```python
print(pt.facts.report(pt.facts.measure(seed=3, universe=universe)))
```

| statistic | measured | real equities | |
|---|---|---|---|
| excess kurtosis | +5.9 | +3 to +10 | matches |
| \|return\| acf(1) | +0.10 | +0.15 to +0.35 | too weak |
| return acf(1) | +0.219 | -0.05 to +0.05 | too high |
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
constant - `MOMENTUM_THETA` from 0.25 to 0.05 takes it to +0.034, inside the
real band, with volatility and kurtosis unchanged. It also fails seven parity
tests, including the one asserting the model constants match the reference
implementation this library is a port of. Changing that constant makes this a
fork, so the lever sits unpulled and documented.

Clustering resists the available levers. GARCH persistence is already 0.99, and
raising the variance ceiling lifts clustering by 0.016 while pushing volatility
from 52.7% to 72%.

Volatility runs high because a generated universe is deliberately dispersed and
skews small. Prefer ratios - capture against the Oracle, shortfall in basis
points - over raw percentages.

Re-measure after changing the preset, the generator or the scenario.
