---
title: The realism envelope
nav_order: 14
rack: reference
short: Envelope
---

# The realism envelope

What pretium certifies, at what horizon, and, just as precisely, what it
does not.

This page exists because "realistic" is not a property a simulator either
has or lacks. A market model is realistic in some respects, at some
measurement scale, and not others. A single realism score would hide
exactly the structure that decides whether your result means anything, so
pretium does not publish one. It publishes this envelope instead: the
statistics it matches, the horizon it matches them at, the axes the claim
survives, and every gap that has been measured rather than assumed.

Every number on this page is measured rather than asserted, and where a
figure has not been re-measured since an earlier preset the text names the
preset it belongs to, so the claims and the caveats share a provenance.
Numbers here are reproducible; the section at the end says how, and says
exactly which runs produced them.

If you want to know what any of these statistics actually measures, and
what a failure on it means for your results,
[the realism metrics](realism-metrics.md) is the reference. To ask which
parameters move them, or which move YOUR result, see [Atlas](atlas.md).

## The claim, in one sentence

**At a 252-day measurement horizon, the shipped `pt-v12` preset matches ALL
FOURTEEN realism statistics pretium measures, on thirty calibration seeds
and on a held-out 60-name universe measured at the same resolution.** At 504
days, measured against bands re-derived at that length, it holds all
fourteen as well. On a held-out set of seeds it holds thirteen, and the axes
section below says which and why. The certified horizon is still 252 days,
because that is the horizon the certification was measured at.

## What is certified

Shipped default `pt-v12`, 30 seeds, 40 instruments, 252 trading days.
Bands are `pretium.facts.REAL_MARKETS`, derived from real-market windows
of the same length by the method in the calibration docs.

| statistic | measured | band | verdict |
|---|---|---|---|
| `annualised_vol_pct` | +32.7604 | 15.0 to 36.0 | in band |
| `excess_kurtosis` | +6.7001 | 1.6 to 41.0 | in band |
| `return_acf1` | +0.0239 | -0.08 to 0.06 | in band |
| `abs_return_acf1` | +0.1107 | 0.02 to 0.22 | in band |
| `abs_return_acf5` | +0.0428 | 0.02 to 0.09 | in band |
| `abs_return_acf20` | +0.0040 | -0.04 to 0.08 | in band |
| `cross_sectional_corr` | +0.3177 | 0.08 to 0.56 | in band |
| `volume_abs_return_corr` | +0.5599 | 0.46 to 0.66 | in band |
| `leverage_effect` | -0.0401 | -0.16 to 0.0 | in band |
| `volume_change_acf1` | -0.2656 | -0.32 to -0.2 | in band |
| `corr_asymmetry` | -0.0147 | -0.25 to 0.45 | in band |
| `corr_asymmetry_lagged` | +0.0181 | -0.2 to 0.55 | in band |
| `sector_excess_corr` | +0.2079 | 0.11 to 0.23 | in band |
| `corr_persistence_acf1` | +0.1525 | -0.19 to 0.54 | in band |

Band-distance loss `L_real` = **0.0000**, and every statistic is inside its
band: `pt-v10` was the first preset with no miss at this horizon, and
`pt-v12` is the first to carry that to 504 days as well. The volume-change
row was called structurally unreachable until the `pt-v10` era boundary and
was still out of band at two years until `pt-v12`; the retired-gap note below
says what changed. `corr_persistence_acf1` also carries a 504-day band of
0.19 to 0.49, which is the one that can judge it: twelve 21-day windows in a
year cannot. It reads 0.2077 there, inside.

## The claim survives the axes it was not fitted to

A model fitted to thirty seeds and reported on those same thirty seeds has
demonstrated nothing. The certified claim is re-measured on two axes the
calibration never saw:

| axis | seeds | result |
|---|---|---|
| training seeds (101-130), 40 names | 30 | **14/14** in band |
| **held-out seeds** (1-30), 40 names | 30 | **13/14** in band, missing `corr_persistence_acf1` |
| **held-out universe** (60 names, seed 909), held-out seeds | 30 | **14/14** in band |

All three re-measured on `pt-v12` at the same thirty-seed resolution as the
certification itself. Almost nothing the calibration fitted to is
load-bearing: fourteen of fourteen land in band on a universe the model
never saw, and thirteen of fourteen on seeds it never used. The single miss
is `corr_persistence_acf1`, which is the statistic this page already warns
is the hardest of the fourteen to estimate at a one-year window.

**Read the resolution before the count.** The project's gate tool screens
held-out axes on six seeds, which is not enough for that statistic. Measured
on `pt-v10`, its across-seed standard deviation was 0.28 and its estimate
moved from -0.220 at six seeds to +0.183 at thirty, against a band floor of
-0.19: a six-seed miss on this row is a property of six seeds rather than of
the model. An earlier draft of this page reported the six-seed count as
though it were the certified one. What is different under `pt-v12` is that
the held-out-seed row misses it at thirty seeds too, so the miss is now
reported in the table above rather than argued away, and the 504-day band of
0.19 to 0.49 is the ruler that can settle it. On the same held-out universe
and the same thirty seeds, `pt-v3` holds twelve of fourteen.

## The gaps, measured

Five, down from six at the `pt-v12` boundary and from eight at 0.1.4. Three
have closed, and they are recorded here rather than deleted quietly.

**Tails are too thin over multi-year windows** closed because excess
kurtosis at 504 days moved from 5.23, below its band, to 8.26 under `pt-v10`
and 7.7528 under `pt-v12`, inside it both times. It clears the 7.1 floor by
0.65, so Gap 1 carries the caution that remains.

**The model has no sector structure** closed because `sector_excess_corr`
moved from 0.0037 to 0.2079 at 252 days and 0.1761 at 504, inside both
bands, on training seeds and on a held-out universe. What that gap said
about crises stayed open through `pt-v11` and closed at `pt-v12`; the
measurement that closed it is recorded under Gap 3.

**Volume change cannot be reached without spending a passing statistic**
closed in two steps, and the second step is what took this list from six
gaps to five. `volume_change_acf1` is the autocorrelation of day-to-day
volume changes. Every preset through pt-v9 read about -0.42 against a real
band of -0.32 to -0.20, and this page called the row unreachable because the
engine's common log-volume state reaches the band and takes
`volume_abs_return_corr` out with it: a market-wide volume multiplier adds
volume variance unrelated to any name's own moves. That trade was real, and
it was priced on the pt-v3 era base. On the pt-v10 base both one-year bands
became reachable together, in a window about 0.03 wide in the innovation
sigma, and the row read -0.3130 at 252 days but -0.3156 at 504 against a
band of -0.29 to -0.21, outside it (calibration record §73). What closed the
two-year half was not more volume memory but `volume_move_cap`, shipped at
12.0 in `pt-v12`. The cap had been a hard-coded literal 4.0 in `tick.rs`,
which saturated a name's volume response at a 4% daily move, so every crisis
day traded like a bad Tuesday. Lifting it reads **-0.2656** at 252 days and
**-0.2572** at 504, inside both, with `volume_abs_return_corr` at 0.5599 and
0.6088, also inside both. A strategy trading the change in volume is now
inside the envelope at one year and at two.

### Gap 1: certification is a 252-day measurement, and the horizon is not free

<!-- FLAG: the "five times more horizon-sensitive" ratio in the next
     sentence was measured in the pt-v10 era and has not been re-measured on
     pt-v12. -->

The statistics are horizon-dependent, and the model is roughly five times
more horizon-sensitive than the market it imitates. Measured against bands
re-derived at the *matching* 504-day window, not the 252-day bands, which
would be the wrong ruler, the shipped model holds **14 of 14**:

| statistic | 252d | 504d | 504-matched band | verdict at 504d |
|---|---|---|---|---|
| `annualised_vol_pct` | 32.76 | 33.89 | 16 to 34 | in |
| `excess_kurtosis` | 6.70 | 7.75 | 7.1 to 22 | in |
| `return_acf1` | 0.0239 | 0.0250 | -0.03 to 0.04 | in |
| `abs_return_acf1` | 0.1107 | 0.2084 | 0.04 to 0.22 | in |
| `abs_return_acf5` | 0.0428 | 0.0864 | 0.02 to 0.1 | in |
| `abs_return_acf20` | 0.0040 | 0.0052 | -0.02 to 0.07 | in |
| `cross_sectional_corr` | 0.3177 | 0.3797 | 0.23 to 0.41 | in |
| `volume_abs_return_corr` | 0.5599 | 0.6088 | 0.48 to 0.65 | in |
| `leverage_effect` | -0.0401 | -0.0543 | -0.13 to 0.02 | in |
| `volume_change_acf1` | -0.2656 | -0.2572 | -0.29 to -0.21 | in |
| `corr_asymmetry` | -0.0147 | -0.0049 | -0.04 to 0.13 | in |
| `corr_asymmetry_lagged` | 0.0181 | -0.0017 | -0.1 to 0.47 | in |
| `sector_excess_corr` | 0.2079 | 0.1761 | 0.11 to 0.22 | in |
| `corr_persistence_acf1` | 0.1525 | 0.2077 | 0.19 to 0.49 | in |

**Consequence: pretium is still not certified for multi-year backtests, and
the reason is now only the measurement.** The two-year panel is not bad, and
since `pt-v12` no row of the fourteen misses it. Certification remains a
252-day claim because that is where it was taken, on the axes above, and a
504-day claim would have to be certified the same way before it could be
quoted the same way.

Four figures deserve a reader's caution at the longer horizon. Volatility
reads 33.89 against a 34.0 ceiling, clearing it by about a tenth of a point.
`abs_return_acf1` reads 0.2084 against a 0.22 ceiling, having nearly doubled
from the 0.1107 it reads at one year. `corr_persistence_acf1` reads 0.2077
against a 0.19 floor. Excess kurtosis reads 7.75 against a 7.1 floor. None of
the four has room to spare, and a single seed can sit on the wrong side of
any of them.

**Beyond 504 days is now measured, and that sentence used to say
otherwise.** `pt-v12` has been run to 756, 1260 and 2520 days on thirty
seeds. At 2520 days it holds 10 of 14, but against the 504-day bands, which
are the wrong ruler for a ten-year window and are quoted here only because
no ten-year bands have been derived. The one thing that ruler does settle is
that nothing runs away: annualised volatility year by year over the ten
years reads 31.5, 35.6, 30.2, 33.5, 33.0, 33.1, 31.3, 32.4, 32.4 and 31.6
percent, which is flat. A long run drifts out of the panel because the
statistics move with the window, not because the market it describes decays.

### Gap 2: volatility memory has the wrong *shape* rather than the wrong length

Real markets' volatility autocorrelation decays hyperbolically. The
model's decays exponentially, because it is built from exponentials: a
per-name GARCH process plus an AR(1) factor variance. Over one year two
exponentials fake a power law well enough that no statistic in the panel
objects. The fake thins out as the window grows.

Measured at the certified horizon, 30 seeds, median across instruments:

<!-- FLAG: this decay curve, the log-log slopes below it and the
     two-component mixture result were measured in the pt-v10 era and have
     not been re-measured on pt-v12. The gap is a mechanism gap and pt-v12
     changed no variance mechanism, but the figures are an earlier era's. -->

| lag | model | real markets |
|---|---|---|
| 1 | +0.1413 | +0.1071 |
| 5 | +0.0496 | +0.0518 |
| 8 | +0.0371 | +0.0453 |
| 12 | +0.0173 | +0.0295 |
| 20 | +0.0082 | +0.0286 |
| 30 | **−0.0052** | +0.0179 |
| 60 | **−0.0142** | +0.0054 |

On log-log axes, where a power law is a straight line, real markets fit a
slope of **−0.436** over lags 1-20, a shade steeper than the 0.2-0.4
power-law exponent Cont (2001) reports, which is the source
`facts.REAL_MARKETS_PROVENANCE` cites for this band. The model fits
**−0.953**, about 2.2 times steeper still. In plain terms: slightly too much
clustering at lag 1, tracking real closely at lag 5, crossing below at about
lag 8, and **negative** by lag 30, where real markets stay weakly positive out
to lag 60.

The model's volatility memory does not merely fade early. It changes
sign. Where a real market still says "yesterday's turbulence makes today's
more likely" a month later, pretium says the mild opposite.

This is a **mechanism** gap, not a calibration one. No parameter setting
turns one slope into the other; it has been tried, and a two-component
variance mixture, the obvious fix, lands lag 20 while getting lag 60 wrong
in both directions at once.

**Consequence: do not trust strategies whose edge depends on volatility
memory beyond about lag 20**, a trading month, which is where
`pretium.envelope.MEMORY_VALID_TO_LAG` draws the line in code.
Vol-targeting and risk-parity overlays that use a one-month or longer
volatility estimate are outside the envelope.

### Gap 3: a scenario's size is right on average and unreliable in one run

Two separate quantities, and they fail differently. The first one no longer
fails, and that is the change at this era boundary.

**The steady-state lever**, how much more violent a sustained crisis is
than a calm market, reads **×6.04** against real markets' **×6.16**, up from
×5.05 at `pt-v10` and ×3.07 at the default before that. It is measured from
a held VIX 5 to a held VIX 65 on the certified 40-name roster over 252 days
at thirty seeds, which is not the quantity a pair of pinned 120-day runs on a
20-name roster gives. That second recipe is the held-VIX half of
`tools/calibration/scenario_response.py`, and on `pt-v12` it reads **×3.51**
from VIX 15 to VIX 45 on the medians of thirty seeds: 27.29% annualised held
at 15 against 95.92% held at 45. One pinned pair lands anywhere from ×2.50 to
×4.41, which is the reason to read even that number at thirty seeds. Three
numbers on this site describe how violent a crisis is; check which one you
are reading. This is a ratio of annualised **volatility** at high VIX to
volatility at low VIX; it is not a correlation lever, and a mechanism that
only reallocates variance between the market factor and the idiosyncratic
term cannot move it by construction. It moved across two presets: `pt-v11`'s
crisis work (`crisis_blend_gain`, `sector_vix_coupling`, endogenous news
with peer transfer) and then `pt-v12` lifting `volume_move_cap` off the
literal 4.0 that had been compiled into `tick.rs`, which saturated a name's
volume response at a 4% daily move and left the engine no way to trade a
crisis day differently from a bad Tuesday. Crisis correlation is a separate
quantity and is inside the real crisis range: at a held VIX 45 co-movement
reads 0.696 against a real 0.664 to 0.727.

**The direction of response is right, and that is measured rather than
asserted.** Driving the real 2020-21 macro path through the model and
correlating daily returns against each driver over 504 sessions, beside the
same correlations computed on real AAPL over the same window:

<!-- FLAG: the driven-window channel table and the OLS gains below it were
     measured on pt-v10 and have not been re-measured on pt-v12. The one
     figure from this run that has been re-measured is the noise ratio. -->

| channel | simulated | real AAPL | sign fixed in advance |
|---|---|---|---|
| return vs change in VIX | -0.423 | -0.622 | negative |
| return vs change in credit yield | -0.496 | -0.592 | negative |
| return vs change in valuation | +0.573 | +0.803 | positive |
| absolute return vs VIX level | +0.512 | +0.489 | positive |

All four carry the right sign, and the volatility-clustering channel is close
to exact.

**The response size was misread, and is corrected here.** This page used to
read those three directional correlations as response sizes and say the model
ran at seventy to eighty-five percent of real. A correlation is
`beta * sd(driver) / sd(return)`, which is signal share rather than gain.
Measured as gains, by OLS slope of daily return on each driver's daily change
over the same 504 sessions, the three channels are right: **-0.00461**
against real AAPL's -0.00500 for the VIX, **-8.106** against -7.445 for the
credit yield, and **+1.226** against +1.272 for valuation. All within ten
percent, with real AAPL inside the model's six-seed range every time.

The denominator is the defect, and it is what keeps this a gap now that the
crisis lever has arrived. Over the same driven window the model's residual sd
is **1.565x** real, down from 1.76x at `pt-v10` and barely moved from 1.555x
at `pt-v11`. That is the worst axis in the model. The expected response to a
scenario is calibrated; the dispersion around it is too wide, so one run
understates how much of its own move was the scenario. On `pt-v10` the daily
return sd behind that ratio was 0.0355 against real AAPL's 0.0236.
Calibration record §81.
<!-- FLAG: the daily-return-sd pair (0.0355 / 0.0236) is the pt-v10
     measurement; only the noise ratio has been re-measured on pt-v12. -->

An event study over the five sessions after each of six dated events agrees
on sign **two times out of six**, and the four misses say what the driven
window can and cannot carry. The Fed's intermeeting cut of 3 March 2020 goes
the wrong way, +9.9% simulated against AAPL's -1.4%: the model reads a cut as
good for equities and has no representation of an emergency cut reading as a
panic signal, so that channel is missing rather than miscalibrated. The VIX
record close of 16 March misses by declining to move, +0.3% against -7.4%. The
vaccine result and Omicron are single-name news for Apple, and nothing in a
run driven only by a macro path can know that. The two that agree are the two
the macro path does carry, the February selloff and the trough with unlimited
QE. Worked through in `examples/09-a-pandemic-shaped-market.ipynb`, which
prints the table and the count.

**The transient**, how fast the market *reacts* to a shock as distinct from
where it settles, is the half of this gap that did not close. Measured on
`pt-v10`, the preset retained **27.6%** of its predecessor's shock response,
because it raised factor-variance persistence to 0.989 to buy volatility
clustering, and a 63-day half-life cannot track a twenty-day spike. Nothing
in `pt-v11` or `pt-v12` touched that persistence, so the ceiling it imposes
is unchanged.
<!-- FLAG: 27.6% is the pt-v10 measurement and has not been re-measured on
     pt-v12; the claim carried forward is the unchanged persistence. -->

**This has been attacked directly and the attack failed, which is why it is
a gap rather than a task.** A two-timescale variance mixture was built to
separate the two jobs: a fast component to chase spikes, a slow one to
carry clustering. Measured, capping persistence at a 14-day half-life does
restore the transient (1.062 → 1.203), and it **doubles** the 504-day loss
(0.9887 → 2.0164). Raising the slow component's weight to buy that back
makes the horizon monotonically worse, 2.02 → 4.11 at weight 0.60, while
the transient stays flat.

So within this model class, **restoring the crisis transient costs
long-horizon realism, and the mechanism built to buy it back cannot.** That
is a structural limit, not a calibration that has not been run yet.

**Sector structure used to be this same shortfall measured a second way, and
is not any more.** In calm markets it is in band, 0.2079 at 252 days and
0.1761 at 504 against bands starting at 0.11. Under a held VIX 45 it reads
**+0.109** against a real **+0.103** in the 2020 window, thirty seeds at 252
days. This page said until the `pt-v12` boundary that industries here hold
together in a crisis about a third as tightly as real ones, on a measurement
of +0.035 against that same +0.103; `pt-v7` on the same recipe read +0.064.
That claim is withdrawn. A sector thesis tested through a crisis is now
being tested in a market whose industries tighten about as much as a real
one's.

**Consequence: use scenarios to ask *whether* a strategy breaks and to size
the break across seeds, not from one run.** How violent a sustained crisis
is, how tightly names co-move in it and how tightly sectors hold in it are
all now measured against real and land there. What is left is the speed and
the spread: the shock arrives more slowly than a real one, and a single run's
dispersion around the calibrated response is 1.565x real, so one run can put
the size almost anywhere. Read the scenario's effect as a distribution over
seeds, which is the resolution every other number on this page is quoted at.

### Gap 4: the endogenous economy cannot reach its own *macro* crisis regimes

Distinguish two kinds of crisis. A **volatility** crisis is endogenous since
0.2.0: measured on `pt-v10`, the preset's own VIX crossed its crisis
threshold on 10.2% of days against a real 12.5%, where the default before it
reached the threshold on none. A **macro** crisis is not, and that is this
gap.

<!-- FLAG: the 10.2% crisis-day frequency is the pt-v10 measurement and has
     not been re-measured on pt-v12. -->

Left to itself the macro state stays in a moderate band. Measured over thirty
seeds and five years, endogenous inflation peaks at 4.0% on every seed against
a clamp of 6.0%, with sd 1.2 around a mean of 2.0%. US CPI year-on-year over
2015-2025 (FRED CPIAUCSL) has sd 2.18 and a peak of 9.0% in June 2022. What
the endogenous series is short of is spread, sd 1.2 against 2.18, rather than
memory: the monthly series has AR(1) +0.958 against real CPI's +0.978, so the
model is slightly the *less* persistent of the two rather than the more. An
earlier five-seed reading of the same measurement is still quoted in two
places and should not be mistaken for this one: the `macro_regime` reason
string in `envelope.check` gives the peaks as 4.06% to 4.11% against a real
9.1%, and the 0.1.4 changelog entry adds sd 1.23 around a mean of 1.99%. The
thirty-seed figures here are the ones `envelope.json` publishes and are the
ones to cite.

The central bank's crisis cadence depends on it, so it is unreachable too.
That path pulls the next meeting in to 21 to 30 days when a decision leaves
the bank more than 2pp behind an inflation rate above 4%, and it fires in
22.0% of the 11,898 central-bank cases in the parity corpus, but a default run
never gets there. It also fires in stagflation rather than in high inflation
as such, so pinning inflation high with unemployment low will not trigger it
however high you pin it.

**Consequence: an inflation regime or a policy crisis has to be driven
through a scenario, and so does the policy response to it.** The recipe was
run before it was published, on real 2022 data over six seeds against a real
S&P of -20.0%: no scenario returns -12.6%, the real seven-hike path alone
returns -13.1%, and the published CPI path returns -23.3%. Inflation is the
lever because it steers the bank's own reaction into the corporate bond
yield; a pinned policy rate does not reproduce that, and `corporate_bond_yield`
must be left free or the channel is severed.
`envelope.check(horizon_days=252, macro_regime=True)` refuses the question and
quotes this gap. `horizon_days` is keyword-only and has no default, so the
call needs it; without it the check raises `TypeError`.

`inflation_reversion` and `inflation_ceiling` are dials since 0.1.4, and
`inflation_floor` since 0.2.0, all three shipped at the values every preset
ran on. At reversion 0.15 the endogenous series matches the real 2015 to 2025
mean and sd to the second decimal, 2.85 and 2.10 against 2.87 and 2.18, and
then sits on the clamps; persistence does not move with the dial, because it
comes from the cycle, wages and unemployment rather than from the reversion.
No preset takes any of the three, and the repository records two different
reasons: the gap text in `envelope.py` says what a real inflation range does
to the equity panel has not been scored, while the 0.2.0 release note says it
costs the two-year panel. That disagreement is not settled here. Calibration
record §65.

### Gap 5: certification was measured on a sector-balanced roster

`Universe.random()` assigns sectors round-robin over the twelve that
`pretium.sectors()` lists, so a roster is as close to balanced as its size
allows: the certified 40 names put four in each of four sectors and three in
each of the other eight. No real index is balanced that way. The S&P is
roughly a third technology and the Nasdaq more so. (This gap said "exactly
five names in each of twelve sectors" until 2026-08-26, which is 60 names:
that describes the held-out universe above, not the certified 40-name roster
this gap is about.) Varying **only** sector composition, thirty seeds, with
every name drawn from one pool:

| roster | 252 days | 504 days | out at 504 |
|---|---|---|---|
| balanced (the certified one) | 14 of 14 | 14 of 14 | -- |
| S&P-like mix | 14 of 14 | 13 of 14 | `annualised_vol_pct` |
| technology-heavy | 14 of 14 | 11 of 14 | vol, `corr_persistence_acf1`, `cross_sectional_corr` |
| defensive | 14 of 14 | 14 of 14 | -- |
| all technology | 13 of 13 | 10 of 13 | vol, `corr_persistence_acf1`, `cross_sectional_corr` |

**Every mix tested holds the full panel at one year on `pt-v12`.** The
all-technology roster is 13 rather than 14 because a single-sector roster has
no cross-sector excess to measure, so `sector_excess_corr` is undefined rather
than missed. The earlier era's measurement, on `pt-v3` at six seeds against
the ten-statistic panel of the time, read 9 of 10 balanced, 8 of 10 for the
S&P-like mix and 7 of 10 all technology, and is what this gap used to quote;
concentration cost the one-year panel then and does not now.

**What concentration costs on `pt-v12` is the second year, not the first**,
and the mechanism is visible rather than mysterious. Cross-sectional
correlation rises monotonically with concentration at 504 days, 0.3797
balanced, 0.3813 S&P-like, 0.4112 technology-heavy and 0.5316 all technology,
which is the model behaving *correctly*, since names in one industry should
move together more. It rises past the 504-day band's top of 0.41 and
annualised volatility follows it out, so part of what the table records is a
broad-market band being the wrong ruler for a single-sector portfolio. The gap
is kept for that reason, and because what it establishes is a property of
roster composition rather than of a preset. Measured by
`tools/calibration/roster_shapes.py`.

**Consequence: a one-year panel transfers to a concentrated roster and a
two-year one does not.** Re-measure the panel on your own universe rather
than inheriting either. `facts.measure()` takes it directly, and
`envelope.intervals()` will report the spread.

## The numbers above are medians, and one run is not the median

Every figure on this page is a median across thirty seeds. That is not what
a single run shows you.

Measured on `pt-v10` over thirty seeds, **nine of the fourteen statistics
have their 10th-to-90th-percentile range across seeds crossing a band
edge**. `abs_return_acf1` read a median of 0.0994 against a ceiling of 0.22,
with a p90 of 0.4063 and an across-seed standard deviation of 0.1467, larger
than the median itself. `pt-v12` moves that median to 0.1107 and the spread
has not been re-measured, so read those dispersion figures as the previous
era's.
<!-- FLAG: the nine-of-fourteen count and the abs_return_acf1 p90/sd are
     pt-v10 measurements awaiting a re-run on pt-v12. -->

A statistic can be comfortably in band on the median and out of band on a
large minority of individual seeds. The held-out-seed row above is that
effect happening in public: `corr_persistence_acf1` is in band at 0.1525 on
the certification's seeds and out of it on thirty seeds the calibration
never used. `pretium.envelope.intervals()` reports the spread beside each
median, and reading it before relying on one run is the difference between a
certified claim and a misread one.

## What this licenses

**Use pretium for:**

- Strategy evaluation over horizons up to about one year, where the edge
  depends on volatility level, lag-1 to lag-5 volatility clustering,
  day-to-day return autocorrelation, cross-sectional co-movement, fat
  tails at the annual scale, the volume-level/volatility relationship, the
  day-to-day change in volume, or the leverage effect.
- **Relative** comparison of strategies under identical conditions. The
  engine is deterministic and bit-reproducible across platforms, so two
  strategies on the same seed differ because of the strategies.
- Testing whether a strategy survives a regime change or a stress
  scenario at all.
- Agent and RL environments where a plausible, self-consistent market is
  needed and absolute realism of every moment is not the claim.

**Do not use pretium for:**

- Multi-year backtests (Gap 1). The 504-day panel now holds all fourteen
  against matched bands, and runs out to 2520 days have been measured, but
  certification is a 252-day measurement and only the 252-day claim is one.
- Sizing a scenario's impact from a single run (Gap 3). The crisis lever, the
  crisis co-movement and the crisis sector structure all land on real now;
  what does not is one run's dispersion around them, at 1.565x real.
- Inheriting the *two-year* panel for a sector-concentrated roster (Gap 5).
  At one year every mix tested holds, so this list is narrower than it was.
- Strategies keyed on long-horizon volatility memory (Gap 2).
- Studying an inflation regime or a policy crisis from the endogenous
  economy alone (Gap 4).
- Any claim that absolute simulated performance forecasts live results.
  That is not a gap in this model; it is true of every market simulator,
  and no amount of realism work will change it.

## Why "done" is defined this way

An earlier definition of done, every statistic in band at every horizon,
turned out to be a treadmill. Three successive calibration searches each
bought 252-day realism by spending 504-day realism, by a different route
each time, and the overfitting control correctly rejected the last of
them. Real markets are not stationary either: the bands themselves come
from a handful of windows whose own dispersion is large, and in one case
real markets' window-to-window spread is six times the model's entire
defect.

Fitting all fourteen numbers at every scale simultaneously optimises against
that noise. Naming what is certified, at what scale, with the gaps measured
and published, is both more honest and more useful.

## Reproducing this page

The machine-readable companion is [`envelope.json`](envelope.json), which
carries the full per-statistic detail, the gap list, and the provenance.
Cite that rather than this prose.

**More than one run, and which figure came from which is worth stating.**
The certified panel, both held-out axes and the 504-day horizon table come
from the `pt-v12` measurement run at thirty seeds; the long horizons quoted
under Gap 1 come from a later `pt-v12` run that reached 2520 days. The
figures still carrying an earlier preset's name, flagged inline where they
appear, come from the paired runs of that era: the first carried the
certified panel and the 504-day table of the time, the second the decay curve
in Gap 2. That first run was killed at its 1260-day stage by an
out-of-memory kill, after it had persisted everything through 504 days, so no
1260-day column appeared here at the time rather than one being quietly
back-filled from an older measurement. Both of that era's runs built from the
same source and report the same known-answer digest,
`992ef95d98e075846f13d0a312231642b26c2030833b10bd8536e374bdc185e3`.

**One caveat on that digest.** After those two runs were taken, a
reporting bug was fixed: `model_preset()`'s default argument was the
literal `"pt-v1"` and had not moved when the engine's default became
`pt-v3`, so the library reported the wrong preset name and one wrong
coefficient (`momentum_theta`) for every run, and `manifest.py` folded
those into the run digest whose purpose is catching precisely that
substitution. The fix changes no simulated value: the engine had always
run `pt-v3`, and the only coefficient that differs between the two presets
in that dictionary is one the reporting path alone consumed. It does move
the known-answer digest, because the digest hashes the reported preset.
The measurements on this page are unaffected.

That digest is the pt-v3 era's, and it is kept as the record of what those two
runs ran. It is not the determinism baseline. The baseline is whatever
`tests/known_answer.json` carries, because that is the file the release
workflow reads when it checks every wheel before uploading. At 0.3.0 it is
known-answer **v11**,
`60d475726c8b270df0894da7577523e98d044dd09afc6b536377eaf4b40de590`, whose
simulation digest is
`7c63282b57955400bad4c61ca7c24c9f6bb0b94b9486870f491eac00cf157a20`. Running
`tests/known_answer.py` prints both. The v10 digest `4e22d5a6...e860378` was
the baseline of the era before this one and is now checked by nothing;
`katVersion` 10 in fact carried three digests over its life, so quote the
version and the digest together or neither.

The envelope is re-measured whenever the default preset changes.
`envelope.json` carries the preset the figures describe rather than a
digest, so the check to run is
`pretium.model_preset()["name"] == json.load(open("envelope.json"))["preset"]`.
If those differ, this page describes a different model than the one you are
running.
