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

**One trap.** Pinning `federal_funds_rate` alone does nothing - measured at
exactly 0.00% across twenty instruments. Equities discount off the corporate
bond yield, and the policy rate only applies as a fallback when no yield is
present. `rate_shock` moves the whole curve; `ramp` isolates a single lever
when that is what you want.

Scenarios reach `evaluate`, `tca.analyse` and `run_many` alike:

```python
calm  = pt.tca.analyse(agent, seed=s, universe=u, days=10,
                       scenario=pt.Scenario().hold(vix=15))
spike = pt.tca.analyse(agent, seed=s, universe=u, days=10,
                       scenario=pt.Scenario().hold(vix=45))
```

Does execution cost more in a volatile regime? Paired over twelve seeds, the
volatile regime costs more in 12 of 12, median 4.70 bps against 6.82, paired
median delta +2.99 bps.

Macro counterfactuals are near-exact rather than exact, and `compare()` reports
which. Order flow consumes no RNG draws, so a TCA counterfactual is exact. A
macro path changes prices, prices change which branch the book settlement
takes, and that branch draws either four uniforms or none. Measured divergence:
zero or four draws in 425,600.

`pin_macro` is logged, so a scenario run replays from its own log with no
special handling.
