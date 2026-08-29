---
title: Scenarios
nav_order: 8
rack: experiment
---

# Scenarios

A rate shock means the rate walking from 2.5% to 5% over fifteen days while an
agent holds positions through it. Setting the rate to 5% from the start gives
you a different market instead of an event.

```python
shock = pt.Scenario.rate_shock(start=0.025, end=0.05, over=15)
pt.evaluate(agents, seed=7, universe=universe, days=20, scenario=shock)
```

`over=` is the whole event, so write it out rather than taking the
constructor's default of 30. The same call at `over=30` is a gentler cycle
spread over more of the horizon, and it costs buy-and-hold 2.17 points here
against the 2.56 in the table below.

Measured on the reference agents (`pt.reference_agents(seed=3)`) over
`Universe.random(20, seed=4)`, seed 7, 20 days, on the shipped `pt-v12` --
calm is the same call with no scenario. Same market, same agents, only the
macro path differs:

| agent | calm | hiked | delta |
|---|---|---|---|
| buy_and_hold | -6.08% | -8.64% | -2.56 |
| momentum | -5.64% | -7.51% | -1.87 |
| oracle | +13.22% | +11.11% | -2.11 |

Nobody escapes the walk here. Buy-and-hold is long-only, holds through the
repricing, and gives up the most. Momentum and the Oracle can trade around it
and give up about two points each. The Oracle stays far ahead in both worlds
because it trades mispricing, and the shock moves fair value
along with price -- but holding positions through a repricing still costs it,
so its edge survives the shock where its level does not. The sizes are the
seed's, as ever: across sim seeds 5 to 9 buy-and-hold gives up 2.6 to 3.9
points and never escapes, while momentum's give-up spans -1.9 to +1.2 -- on
one seed in five it trades around the shock entirely.

**One trap.** Pinning `federal_funds_rate` alone does nothing until the first
central-bank meeting -- a policy-rate `ramp` from 2.5% to 5% over thirty days
(`Universe.random(20, seed=4)`, sim seed 5, `pt-v12`) moves every price by
exactly 0.00% over 40 days, and a median -4.00% once a 60-day run crosses the
meeting at day 45, where the corporate yield is recomputed off the 10Y.
Equities discount off the corporate bond yield, so a short policy-only study
sees nothing, silently. `rate_shock` moves the whole curve for an immediate
repricing; `ramp` isolates a single lever when that is what you want.

## What each field transmits, and when

The trap above is narrow and it is easy to over-read. "Nothing happens before
day 45" is false; **"a policy-only rate path does nothing before day 45"** is
true. Most of the macro surface transmits on the day you move it.

The difference matters because two questions get conflated. A field can move
what a company is *worth*, or it can move the *path* a price takes without
touching fair value, and VIX does the second while the policy rate does the
first. Both are transmission; only one is a re-rating.

Measured on `pt-v12` by holding `Universe.random(20, seed=4)` fixed at sim
seed 5, introducing each shock on **day 5** with `compare()`, and reading the
median instrument at day 25, which is deliberately before the first
*scheduled* meeting at day 45. The fair-value column is the same median taken
over each name's own change in `fundamental_value` from `truth()`, so the two
columns are the same statistic on price and on worth:

| field | median price move by day 25 | fair value | route |
|---|---|---|---|
| `vix` 15 to 60 | -7.90% | unchanged | volatility of the market factor, immediate |
| `qe_pe_boost` 0 to -0.30 | -27.12% | -30.0% | a direct multiple on fair value, immediate |
| `corporate_bond_yield` 5.5% to 11.4% | -13.01% | -10.9% | the discount rate itself, immediate |
| `federal_funds_rate` 1.6% to 10% | 0.00% | unchanged | only by steering the yield, at the next scheduled meeting |
| `inflation_rate` 2% to 9% | -11.12% | -10.6% | by steering the reaction function, which this shock forces to meet the next day |
| `fear_greed_index` 50 to 0 | 0.00% | unchanged | none. Nothing reads it |

Read the `vix` row carefully, because it is the one row whose *sign* is not a
mechanism. VIX moves the variance of the shared market factor and leaves fair
value alone, so one seed's price delta is a draw from a wide distribution
rather than a direction: the same shock spans -31.9% to +8.5% across this
universe's twenty names. The rows that move fair value carry their sign
honestly.

**The `inflation_rate` row is the one this page used to get wrong.** It
reported 0.00% and explained the zero structurally, as "no route exists before
the first meeting". That reason is true of the policy rate and false of
inflation. `update_central_bank` (`rust/src/economy/central_bank.rs:65-68`)
skips the `next_meeting_date` check entirely when inflation runs more than 4pp
above the policy rate *and* is itself above 4%, which is exactly what a step
to 9% against a 2.5% policy rate does. Recorded day by day, the shock lands on
day 5 and the reaction lands on day 6: `federal_funds_rate` 2.500% to 7.167%
and `corporate_bond_yield` 4.560% to 10.392%, thirty-nine days before the
first scheduled meeting. Inflation does reach price only by steering the
reaction function, and the reaction can be tomorrow.

The old 0.00% was reproducible, but only under a pin the table never
disclosed. `tests/test_macro_transmission.py` runs this probe with
`corporate_bond_yield` held on every day of the path, and that pin severs the
only route inflation has, so under it the shocked run really is bit-identical
to base. Pinning the yield is a legitimate thing to do, and the first
consequence below is about it -- but it is a different measurement from the
one this table states, and the difference was the whole of the error.

Three further consequences worth stating separately.

**`corporate_bond_yield` is the discount rate, and it is the only route the
slow fields have.** Pin it to a path of your own and `federal_funds_rate` and
`inflation_rate` are severed completely, because the thing they steer is no
longer free to move. That is usually what you want when you have a real credit
series to drive, and it is worth knowing you have done it.

**`qe_pe_boost` is the only field that can express a re-rating on its own.** It
moves fair value one for one, it takes negative values, and it bypasses the
yield. A market that falls a third because the multiple compressed cannot be
built out of the rate fields; the yield's whole journey from 5.5% to 11.4%,
which is the 2020 credit seizure end to end, is worth about 11%.

**`fear_greed_index` is inert.** It is settable, range-validated and reported
in `macro_table`, and no pricing code reads it. It is computed as a diagnostic
and exposed as though it were a lever. `tests/test_macro_transmission.py` pins
that as current behaviour rather than leaving it to be rediscovered; wiring
sentiment to price would be a new mechanism needing calibration.

Worked through end to end, on real 2020-21 data, in
[notebook 09](https://github.com/simoncoombes/tradefloor/blob/main/examples/09-a-pandemic-shaped-market.ipynb),
which got this wrong first and shows the diagnosis.

## The second trap, retired: VIX drives volatility now

This page used to state, in bold, that VIX does not drive volatility, and it
was true, measured and tested when it said so. The 2026-08 era coupled the
market factor's conditional-variance process to VIX after measuring both
variants, and this section was rewritten in the same change that flipped the
constant, because a page that quietly stops saying something is worse than
one that never said it.

**VIX is the market factor's implied volatility.** The factor's variance
reverts to a target proportional to `(vix / 15)^2`, anchored so that VIX 15,
the endogenous mean, reproduces the uncoupled process exactly. The per-name
GARCH recursion (`omega + alpha * r^2 + beta * v`) still has no VIX term, and
up to pt-v9 that settled it. It does not now: since pt-v10 the static
per-sector variance the recursion's clamps are multiples of carries the same
`(vix / 15)^2` map, through `garch_vix_coupling` (below), so the anchor is
again exact and the regime reaches a name's own variance as well as the
shared one. VIX scales the shared component of every return and the level
each name's own variance is held to, so a crisis VIX is a volatility regime
and a correlation regime at once, which is what a real crisis is. Annualised
realised volatility, measured on `pt-v12` over `Universe.random(20, seed=11)`,
120 days, sim seed 3, pinned through the scenario API:

| VIX | annualised realised vol |
|---|---|
| 5 | 30.29% |
| 15 (the anchor) | 36.71% |
| 45 | 112.10% |
| 65 | 135.52% |

A thirteenfold move in VIX moves realised volatility by a factor of 4.5, and
a sub-15 pin calms the market rather than doing nothing. Since the 0.2.0 era
boundary a pin acts on the FIRST day rather than the second, because the
sector draw's volatility reads the VIX inside the tick: VIX 5, 10 and 15 now
give different day-one closes, where before they were bit-identical on day
one and diverged from day two. (An
earlier version of this page claimed bit-identity over 60 days; even before
the coupling that had quietly become false at day 45, where the first
central-bank meeting reprices the corporate yield off a VIX-bearing spread.)

The response to a held pin saturates. The factor's variance is clamped at
`market_vol_ceiling_multiple` times its baseline for reasons independent of
the coupling (the clamp carries the process's fourth moment). That was 8x
through pt-v6, 16x from pt-v7, and is 32x on the shipped pt-v12, set at the
level a record VIX actually implies, so the saturation sits far higher than it
used to: quadratic across the plausible band rather than flat above VIX ~42.
A researcher holding VIX 65 gets a market realising 3.7 times the volatility
of the VIX 15 anchor, with crisis-level correlation. That is 2008 sustained,
not a numerical blow-up.

Five channels:

1. **The factor's variance target**, above. The channel that answers "what
   happens when volatility triples", and, through the same mechanism,
   the crisis-correlation channel.
2. **Quoted bid-ask**, through a multiplier `1 + max(0, (vix - 15) / 30)`.
   Mean quoted spread across `Universe.random(25, seed=11)` after five days,
   sim seed 3, on `pt-v12`: 11.78 bps at VIX 15, 14.14 at 25, 17.49 at 45,
   22.62 at 65.
3. **Cross-sectional correlation above VIX 25.5** (the crisis threshold since
   the 2026-08 re-site; the old `vix > 40` trigger sat above the endogenous
   ceiling and could never fire), where sector factors blend toward the
   market factor. Together with channel 1, mean pairwise correlation of daily
   log returns over the same 25-name universe's 300 pairs, 120 days, sim
   seed 3, on `pt-v12`: +0.197 at VIX 15, +0.626 at 45, +0.678 at 65.
   This particular measurement barely moved across the pt-v11 boundary, which
   is worth recording because the obvious prediction was that it would:
   pt-v11 raised `crisis_blend_gain` 0.5 to 0.8 and `sector_vix_coupling`
   0.25 to 1.0, both of which act in exactly this regime, and the same three
   pins read +0.196 / +0.622 / +0.680 on pt-v10. A single seed's 300 pairs at
   a held VIX is already saturated by channel 1; what those two dials moved is
   the crisis panel over thirty seeds, not this probe.
   Diversification genuinely stops working at crisis VIX. See
   [How realistic is this market](how-realistic-is-this-market.md).
4. **Credit spreads** in the daily economy step, recomputed at central-bank
   meetings (the first sits at day 45), so a VIX path also reprices the
   yield equities discount off, at meeting cadence. See
   [Core concepts](core-concepts.md).
5. **Company news reaching sector peers**, since pt-v11. A peer weight that
   was constant could not tell March 2020 from a quiet July, so the transfer
   is scaled by `1 + news_peer_vix_coupling * crisis_spike`
   (`news_peer_vix_coupling` 8.0 on the shipped preset) and the spike is zero
   below the same 25.5 threshold: a calm market is untouched at any coupling
   and only the crisis moves. This is the channel that carries crisis sector
   structure. At a held VIX 45, pt-v12's sector excess correlation reads
   +0.109 against a real +0.103, measured on thirty seeds. The older claim
   that industries hold together in a crisis about a third as tightly as real
   ones was withdrawn at this boundary: it was measured before this channel
   existed.

So a VIX path stresses execution and strategy at once: spreads widen,
volatility rises, the cross-section starts moving together, and one company's
bad news reaches its sector peers harder than it would on a quiet day. Since the
0.2.0 era boundary it also moves a name's OWN variance, through
`garch_vix_coupling` 0.3 on the shipped preset: this page used to say VIX
sized the shared factor's share and never each name's own noise, and that
was true up to pt-v9 and is not true now.

## Scenarios reach every entry point

```python
calm  = pt.tca.analyse(agent, seed=s, universe=u, days=10,
                       scenario=pt.Scenario().hold(vix=15))
spike = pt.tca.analyse(agent, seed=s, universe=u, days=10,
                       scenario=pt.Scenario().hold(vix=45))
```

Does execution cost more when VIX is high? Measured with `BuyAndHold` over
`Universe.random(20, seed=11)`, ten days, seeds 1 to 12, on `pt-v12`: the VIX
45 regime costs more in 12 of 12, median shortfall 11.69 bps against 6.06,
paired median delta +5.62 bps. That is mostly the spread channel, because
`BuyAndHold` trades on day one, before a pin has reached the variance
process, and the figures re-verified bit-for-bit after the volatility
coupling for exactly that reason. They survived the pt-v12 boundary the same
way: all four figures are identical on pt-v10 and pt-v12, which is the
cleanest evidence available that this measurement is the spread channel and
not the price path. An agent trading through the following days pays the
volatility channel too. A pinned VIX also closes the one macro feedback
channel the TCA counterfactual now has. See
[Transaction cost analysis](transaction-cost-analysis.md) for the
boundary and its measurement.

`evaluate`, `tca.analyse` and `run_many` all take `scenario=`.

Macro counterfactuals are exact on the market stream, and `compare()` reports
it. Before the 2026-08 RNG stream split this paragraph said "near-exact": a
macro path changed prices, prices changed which settlement branch drew four
uniforms, and the shared draw schedule could shift. An older build measured
-4 in 425,600 draws. The split removed that mechanism: settlement's uniforms
are drawn unconditionally, and the market stream's schedule is a pure
function of (market status, active roster, sector count), so two runs under
different macro paths see identical market noise, draw for draw. The VIX
volatility coupling preserves this, because the variance target reads macro state
already evolved, never a new draw. pt-v11's endogenous news preserves it for
the same kind of reason on its own stream: its two draws per company are taken
unconditionally at the day boundary, and the crisis coupling scales an impact
that was already drawn rather than deciding whether to draw it.
`compare()` reports `draw_delta` from the market stream rather than
asserting zero: a non-zero delta means the scenario
changed the market's own draw schedule (a halt, a delisting, a roster
change), and the result compares two structurally different markets, which is
worth surfacing rather than averaging away. Measured at zero across all
twenty-eight comparisons on `pt-v12`: four scenarios at seed 3, three of them
repeated across seeds 1 to 8, twenty names, forty days.

`pin_macro` is logged, so a scenario run replays from its own log with no
special handling.
