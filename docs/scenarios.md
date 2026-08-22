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

Measured on the reference agents (`pt.reference_agents(seed=3)`) over
`Universe.random(20, seed=4)`, seed 7, 20 days - calm is the same call with no
scenario. Same market, same agents, only the macro path differs:

| agent | calm | hiked | delta |
|---|---|---|---|
| buy_and_hold | -7.16% | -10.75% | -3.59 |
| momentum | -1.17% | -3.52% | -2.36 |
| oracle | +11.11% | +8.70% | -2.41 |

Nobody escapes the walk here. Buy-and-hold is long-only, holds through the
repricing, and loses the most. Momentum and the Oracle can trade around it
and each give up about two and a half points. The Oracle stays far ahead in
both worlds because it trades mispricing, and the shock moves fair value
along with price - but holding positions through a repricing still costs it,
so its edge survives the shock where its level does not. The sizes are the
seed's, as ever: across sim seeds 5 to 9 buy-and-hold gives up 3.4 to 4.7
points and never escapes, while momentum's give-up spans -5.1 to +0.5 - on
one seed in five it trades around the shock entirely.

**One trap.** Pinning `federal_funds_rate` alone does nothing until the first
central-bank meeting - a policy-rate `ramp` from 2.5% to 5% over thirty days
(`Universe.random(20, seed=4)`, sim seed 5) moves every price by exactly
0.00% over 40 days, and a median -4.29% once a 60-day run crosses the
meeting at day 45, where the corporate yield is recomputed off the 10Y.
Equities discount off the corporate bond yield, so a short policy-only study
sees nothing, silently. `rate_shock` moves the whole curve for an immediate
repricing; `ramp` isolates a single lever when that is what you want.

## The second trap, retired: VIX drives volatility now

This page used to state, in bold, that VIX does not drive volatility — and it
was true, measured, and tested when it said so. The 2026-08 era coupled the
market factor's conditional-variance process to VIX after measuring both
variants, and this section was rewritten in the same change that flipped the
constant, because a page that quietly stops saying something is worse than
one that never said it.

**VIX is the market factor's implied volatility.** The factor's variance
reverts to a target proportional to `(vix / 15)^2`, anchored so that VIX 15 —
the endogenous mean — reproduces the uncoupled process exactly. The per-name
GARCH recursion (`omega + alpha * r^2 + beta * v`) still has no VIX term:
what VIX scales is the shared component of every return, so a crisis VIX is a
volatility regime and a correlation regime at once, which is what a real
crisis is. Annualised realised volatility, measured over
`Universe.random(20, seed=11)`, 120 days, sim seed 3, pinned through the
scenario API:

| VIX | annualised realised vol |
|---|---|
| 5 | 49.48% |
| 15 (the anchor) | 58.76% |
| 45 | 107.07% |
| 65 | 124.31% |

A thirteenfold move in VIX moves realised volatility by a factor of 2.5, and
a sub-15 pin now calms the market rather than doing nothing. VIX 5, 10 and 15
produce identical prices only for the first day — the first close is where a
pin first enters the variance target — and diverge from the second:
re-measured on the same universe at sim seed 3, day one's closes are
bit-identical across all three pins and day two's differ for every pair. (An
earlier version of this page claimed bit-identity over 60 days; even before
the coupling that had quietly become false at day 45, where the first
central-bank meeting reprices the corporate yield off a VIX-bearing spread.)

The response to a held pin saturates. The factor's variance is clamped at 8x
its baseline for reasons independent of the coupling (the clamp carries the
process's fourth moment), so above VIX ~42 a harder pin buys almost no
additional factor variance: quadratic inside the plausible band, flat beyond.
A researcher pinning VIX 65 for a year gets a market realising roughly twice
its calm volatility with crisis-level correlation — 2008 sustained, not a
numerical blow-up.

Four channels:

1. **The factor's variance target**, above. The channel that answers "what
   happens when volatility triples" — and, through the same mechanism,
   the crisis-correlation channel.
2. **Quoted bid-ask**, through a multiplier `1 + max(0, (vix - 15) / 30)`.
   Mean quoted spread across `Universe.random(25, seed=11)` after five days,
   sim seed 3: 11.52 bps at VIX 15, 13.92 at 25, 18.87 at 45, 25.89 at 65.
3. **Cross-sectional correlation above VIX 25.5** (the crisis threshold since
   the 2026-08 re-site; the old `vix > 40` trigger sat above the endogenous
   ceiling and could never fire), where sector factors blend toward the
   market factor. Together with channel 1, mean pairwise correlation of daily
   log returns over the same 25-name universe's 300 pairs, 120 days, sim
   seed 3: +0.269 at VIX 15, +0.678 at 45, +0.759 at 65.
   Diversification genuinely stops working at crisis VIX. See
   [How realistic is this market](how-realistic-is-this-market.html).
4. **Credit spreads** in the daily economy step, recomputed at central-bank
   meetings (the first sits at day 45), so a VIX path also reprices the
   yield equities discount off — at meeting cadence. See
   [Core concepts](core-concepts.html).

So a VIX path stresses execution and strategy at once: spreads widen,
volatility rises, and the cross-section starts moving together. What it does
not move is any single name's idiosyncratic variance — VIX sizes the shared
factor's share, not each name's own noise.

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
delta +5.62 bps. That is mostly the spread channel — `BuyAndHold` trades on
day one, before a pin has reached the variance process — and the figures
re-verified bit-for-bit after the volatility coupling for exactly that
reason. An agent trading through the following days pays the volatility
channel too. A pinned VIX also closes the one macro feedback channel the
TCA counterfactual now has — see
[Transaction cost analysis](transaction-cost-analysis.html) for the
boundary and its measurement.

`evaluate`, `tca.analyse` and `run_many` all take `scenario=`.

Macro counterfactuals are exact on the market stream, and `compare()` reports
it. Before the 2026-08 RNG stream split this paragraph said "near-exact": a
macro path changed prices, prices changed which settlement branch drew four
uniforms, and the shared draw schedule could shift — an older build measured
-4 in 425,600 draws. The split removed that mechanism: settlement's uniforms
are drawn unconditionally, and the market stream's schedule is a pure
function of (market status, active roster, sector count), so two runs under
different macro paths see identical market noise, draw for draw. The VIX
volatility coupling preserves this — the variance target reads macro state
already evolved, never a new draw. `compare()` reports `draw_delta` from the
market stream rather than asserting zero: a non-zero delta means the scenario
changed the market's own draw schedule (a halt, a delisting, a roster
change), and the result compares two structurally different markets, which is
worth surfacing rather than averaging away. Measured at zero across every
comparison on this build: four scenarios at seed 3, three of them repeated
across seeds 1 to 8.

`pin_macro` is logged, so a scenario run replays from its own log with no
special handling.
