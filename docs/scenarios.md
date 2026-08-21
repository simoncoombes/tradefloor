---
title: Scenarios
nav_order: 8
rack: experiment
---

# Scenarios

A rate shock means the rate walking from 2.5% to 5% over thirty days while an
agent holds positions through it. Setting the rate to 5% from the start gives
you a different market instead of an event.

```python
shock = pt.Scenario.rate_shock(start=0.025, end=0.05, over=15)
pt.evaluate(agents, seed=7, universe=universe, days=20, scenario=shock)
```

Measured on seed 7 - same market, same agents, only the macro path differs:

| agent | calm | hiked | delta |
|---|---|---|---|
| buy_and_hold | +3.51% | -0.87% | -4.37 |
| momentum | -2.36% | -0.63% | +1.73 |
| oracle | +20.86% | +20.91% | +0.05 |

Buy-and-hold is long-only and holds through the repricing. Momentum gains
because it can rotate. The Oracle is untouched because it trades mispricing,
and the shock moves fair value along with price.

**One trap.** Pinning `federal_funds_rate` alone does nothing until the first
central-bank meeting - measured at exactly 0.00% across twenty instruments
over 40 days, and a median -3.99% once a 60-day run crosses the meeting at
day 45, where the corporate yield is recomputed off the 10Y. Equities
discount off the corporate bond yield, so a short policy-only study sees
nothing, silently. `rate_shock` moves the whole curve for an immediate
repricing; `ramp` isolates a single lever when that is what you want.

## The second trap: VIX does not drive volatility

`Scenario.vix_shock` was called `vol_shock` until it was measured. The old name
still works and warns; the path it produces is unchanged.

**There is no VIX term in the variance process.** The GARCH recursion is
`omega + alpha * r^2 + beta * v` with a sector-relative clamp, and neither it
nor the noise magnitude reads VIX. Annualised realised volatility, measured on
20 instruments over 120 days, seed 3:

| VIX | annualised realised vol |
|---|---|
| 5 | 58.05% |
| 15 (default) | 58.05% |
| 45 | 58.22% |
| 65 | 58.92% |

A thirteenfold move in VIX changes realised volatility by under one point.
Below VIX 15 it changes nothing at all: VIX 5, 10 and 15 produce bit-identical
prices over 60 days, because the spread multiplier floors at 1.0 and the
correlation blend has not started.

Here VIX is a **liquidity and spread** variable. Three channels:

1. **Quoted bid-ask**, through a multiplier `1 + max(0, (vix - 15) / 30)`.
   Mean quoted spread across 25 instruments after five days: 12.17 bps at VIX
   15, 14.72 at 25, 20.05 at 45, 28.41 at 65. This is the channel that
   genuinely moves.
2. **Cross-sectional correlation above VIX 40**, where sector factors blend
   toward the market factor. Mean pairwise correlation of daily log returns
   over 300 pairs: +0.022 at VIX 15, +0.023 at 45, +0.041 at 65. The blend is
   correct in construction and close to invisible in output, because it acts
   on sector factors with sigma 0.002 against per-stock noise running 0.008 to
   0.025. Diversification keeps working at VIX 65. See
   [How realistic is this market](how-realistic-is-this-market.html).
3. **Credit spreads** in the daily economy step, which is not reachable from
   Python in this release. See [Core concepts](core-concepts.html).

So use a VIX path to ask what an execution algorithm does when spreads widen.
Do not use it to ask what happens when volatility triples, and do not expect
much from the correlation channel. Nothing in this model raises realised
volatility, and that limitation is stated rather than papered over.

## Scenarios reach every entry point

```python
calm  = pt.tca.analyse(agent, seed=s, universe=u, days=10,
                       scenario=pt.Scenario().hold(vix=15))
spike = pt.tca.analyse(agent, seed=s, universe=u, days=10,
                       scenario=pt.Scenario().hold(vix=45))
```

Does execution cost more when VIX is high? Measured with `BuyAndHold` over
`Universe.random(20, seed=11)`, ten days, seeds 1 to 12: the VIX 45 regime
costs more in 12 of 12, median shortfall 11.69 bps against 6.06, paired median
delta +5.62 bps. That is the spread channel, and it is the channel a VIX
scenario is good for.

`evaluate`, `tca.analyse` and `run_many` all take `scenario=`.

Macro counterfactuals are near-exact rather than exact, and `compare()` reports
which. Order flow consumes no RNG draws, so a TCA counterfactual is exact. A
macro path changes prices, prices change which branch the book settlement
takes, and that branch draws either four uniforms or none. Re-measured on this
build over 20 instruments and 40 days, the divergence is zero in every
comparison run: four scenarios at seed 3, and three of them repeated across
seeds 1 to 8. An older build recorded a delta of -4 in 425,600 draws, which is
where the mechanism was identified. The mechanism has not been removed, so
`compare()` still reports `draw_delta` rather than asserting it away.

`pin_macro` is logged, so a scenario run replays from its own log with no
special handling.
