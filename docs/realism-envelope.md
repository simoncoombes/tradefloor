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

Every number on this page is measured against the shipped default preset
with a verified known-answer digest, so the claims and the caveats share a
provenance. Numbers here are reproducible; the section at the end says how,
and says exactly which runs produced them.

If you want to know what any of these statistics actually measures, and
what a failure on it means for your results,
[the realism metrics](realism-metrics.md) is the reference. To ask which
parameters move them, or which move YOUR result, see [Atlas](atlas.md).

## The claim, in one sentence

**At a 252-day measurement horizon, the shipped `pt-v10` preset matches ALL
FOURTEEN realism statistics pretium measures, on thirty calibration seeds
and on a held-out 60-name universe measured at the same resolution.** At 504
days it holds thirteen, and the gaps below say which and why.

## What is certified

Shipped default `pt-v10`, 30 seeds, 40 instruments, 252 trading days.
Bands are `pretium.facts.REAL_MARKETS`, derived from real-market windows
of the same length by the method in the calibration docs.

| statistic | measured | band | verdict |
|---|---|---|---|
| `annualised_vol_pct` | +31.4632 | 15.0 to 36.0 | in band |
| `excess_kurtosis` | +7.7618 | 1.6 to 41.0 | in band |
| `return_acf1` | +0.0195 | -0.08 to 0.06 | in band |
| `abs_return_acf1` | +0.0994 | 0.02 to 0.22 | in band |
| `abs_return_acf5` | +0.0487 | 0.02 to 0.09 | in band |
| `abs_return_acf20` | +0.0043 | -0.04 to 0.08 | in band |
| `cross_sectional_corr` | +0.3063 | 0.08 to 0.56 | in band |
| `volume_abs_return_corr` | +0.4784 | 0.46 to 0.66 | in band |
| `leverage_effect` | -0.0336 | -0.16 to 0.0 | in band |
| `volume_change_acf1` | -0.3130 | -0.32 to -0.2 | in band |
| `corr_asymmetry` | -0.0034 | -0.25 to 0.45 | in band |
| `corr_asymmetry_lagged` | +0.0054 | -0.2 to 0.55 | in band |
| `sector_excess_corr` | +0.1346 | 0.11 to 0.23 | in band |
| `corr_persistence_acf1` | +0.1622 | -0.19 to 0.54 | in band |
Band-distance loss `L_real` = **0.0000**, and every statistic is inside its band: pt-v10 is the first preset with no miss at this horizon. The volume-change row was called structurally unreachable until 2026-08-26; gap 1 below says what changed. `corr_persistence_acf1` also carries a 504-day band of 0.19 to 0.49, which is the one that can judge it: twelve 21-day windows in a year cannot.

## The claim survives the axes it was not fitted to

A model fitted to thirty seeds and reported on those same thirty seeds has
demonstrated nothing. The certified claim is re-measured on two axes the
calibration never saw:

| axis | seeds | result |
|---|---|---|
| training seeds (101-130), 40 names | 30 | **14/14** in band |
| **held-out seeds** (1-30), 40 names | 30 | **14/14** in band |
| **held-out universe** (60 names, seed 909), held-out seeds | 30 | **14/14** in band |

All three re-measured on pt-v10 for this release, at the same thirty-seed
resolution as the certification itself. Nothing the calibration fitted to is
load-bearing: the same fourteen statistics land in band on seeds it never
used and on a universe it never saw.

**Read the resolution before the count.** The project's gate tool screens
held-out axes on six seeds, and at six seeds both held-out rows read 13/14,
dropping `corr_persistence_acf1`. That is a property of six seeds rather
than of the model: the statistic's across-seed standard deviation is 0.28,
and its estimate moves from -0.220 at six seeds to +0.183 at thirty, against
a band floor of -0.19. An earlier draft of this page reported the six-seed
count as though it were the certified one. On the same held-out universe and
the same thirty seeds, `pt-v3` holds twelve of fourteen.

## The gaps, measured

Six, down from eight at 0.1.4. Two closed at the era boundary and are
recorded here rather than deleted quietly. **Tails are too thin over
multi-year windows** closed because excess kurtosis at 504 days moved from
5.23, below its band, to 8.26, inside it; it sits about 0.3 seed-sd above
the floor, so Gap 2 carries the caution that remains. **The model has no
sector structure** closed because `sector_excess_corr` moved from 0.0037 to
0.1346 at 252 days and 0.1201 at 504, inside both bands, on training seeds
and on a held-out universe. What that gap said about crises did not close,
and is now measured under Gap 4.

### Gap 1: volume change, reachable since the era boundary and not before

`volume_change_acf1` is the autocorrelation of day-to-day volume changes.
Every preset through pt-v9 read about -0.42 against a real band of -0.32 to
-0.20, and this page called it unreachable without spending a passing
statistic, because the engine's common log-volume state reaches the band and
takes `volume_abs_return_corr` out with it: a market-wide volume multiplier
adds volume variance unrelated to any name's own moves.

That trade was real, and it was priced on the pt-v3 era base. On the pt-v10
base both bands are reachable together, in a window about 0.03 wide in the
innovation sigma. The default now reads **-0.3130** on the statistic and
**0.4784** on the correlation, both inside. Calibration record §73.

**What remains.** At 504 days the band tightens to -0.29 to -0.21 and the
default reads -0.3156, outside it. Longer volume memory moves the figure the
wrong way, and reaching the two-year band needs a bigger innovation, which
takes the correlation through its one-year floor. **So a strategy trading
the change in volume is on solid ground at one year and outside the envelope
at two.**

### Gap 2: the certified horizon is 252 days, and the model does not hold beyond it

The statistics are horizon-dependent, and the model is roughly five times
more horizon-sensitive than the market it imitates. Measured against
bands re-derived at the *matching* 504-day window, not the 252-day bands,
which would be the wrong ruler, the shipped model holds **13 of 14**, missing only the volume-change row:

| statistic | 252d | 504d | 504-matched band | verdict at 504d |
|---|---|---|---|---|
| `annualised_vol_pct` | 31.46 | 32.69 | 16 to 34 | in |
| `excess_kurtosis` | 7.76 | 8.26 | 7.1 to 22 | in |
| `return_acf1` | 0.0195 | 0.0213 | -0.03 to 0.04 | in |
| `abs_return_acf1` | 0.0994 | 0.1749 | 0.04 to 0.22 | in |
| `abs_return_acf5` | 0.0487 | 0.0830 | 0.02 to 0.1 | in |
| `abs_return_acf20` | 0.0043 | 0.0088 | -0.02 to 0.07 | in |
| `cross_sectional_corr` | 0.3063 | 0.3533 | 0.23 to 0.41 | in |
| `volume_abs_return_corr` | 0.4784 | 0.5174 | 0.48 to 0.65 | in |
| `leverage_effect` | -0.0336 | -0.0375 | -0.13 to 0.02 | in |
| `volume_change_acf1` | -0.3130 | -0.3156 | -0.29 to -0.21 | **out** |
| `corr_asymmetry` | -0.0034 | 0.0133 | -0.04 to 0.13 | in |
| `corr_asymmetry_lagged` | 0.0054 | -0.0247 | -0.1 to 0.47 | in |
| `sector_excess_corr` | 0.1346 | 0.1201 | 0.11 to 0.22 | in |
| `corr_persistence_acf1` | 0.1622 | 0.1908 | 0.19 to 0.49 | in |

**Consequence: pretium is not certified for multi-year backtests.** Not
because the two-year panel is bad, since one row of fourteen misses it, but
because certification is a measurement and this one was taken at 252 days.
Two figures deserve a reader's caution at the longer horizon: excess
kurtosis sits inside its band at 8.26 but only about 0.3 seed-sd above the
floor of 7.1, and beyond 504 days nothing has been measured at all.

### Gap 3: volatility memory has the wrong *shape* rather than the wrong length

Real markets' volatility autocorrelation decays hyperbolically. The
model's decays exponentially, because it is built from exponentials: a
per-name GARCH process plus an AR(1) factor variance. Over one year two
exponentials fake a power law well enough that no statistic in the panel
objects. The fake thins out as the window grows.

Measured at the certified horizon, 30 seeds, median across instruments:

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
slope of **−0.436** over lags 1-20, inside the 0.2-0.4 exponent range the
literature publishes. The model fits **−0.953**, about 2.2 times
steeper. In plain terms: slightly too much clustering at lag 1, tracking
real closely at lag 5, crossing below at about lag 8, and **negative** by
lag 30, where real markets stay weakly positive out to lag 60.

The model's volatility memory does not merely fade early. It changes
sign. Where a real market still says "yesterday's turbulence makes today's
more likely" a month later, pretium says the mild opposite.

This is a **mechanism** gap, not a calibration one. No parameter setting
turns one slope into the other; it has been tried, and a two-component
variance mixture, the obvious fix, lands lag 20 while getting lag 60 wrong
in both directions at once.

**Consequence: do not trust strategies whose edge depends on volatility
memory beyond about two weeks.** Vol-targeting and risk-parity overlays
that use a one-month or longer volatility estimate are outside the
envelope.

### Gap 4: scenario response is directional rather than calibrated

Two separate quantities, and they fail differently.

**The steady-state lever**, how much more violent a sustained crisis is
than a calm market, reads about **×3.1** against real markets' **×6.16**.
Roughly half. This is a ratio of annualised **volatility** at high VIX to
volatility at low VIX; it is not a correlation lever, and a mechanism that
only reallocates variance between the market factor and the idiosyncratic
term cannot move it by construction. Crisis correlation is a separate
quantity and is already near the real crisis band: pinned VIX 45 reads
0.66 and VIX 65 reads 0.73.

**The direction of response is right, and that is measured rather than
asserted.** Driving the real 2020-21 macro path through the model and
correlating daily returns against each driver over 504 sessions, beside the
same correlations computed on real AAPL over the same window:

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

The denominator is the defect. Over the same runs the model's daily return sd
is **0.0355** against real AAPL's **0.0236**, and its residual sd is 1.76x
real. The expected response to a scenario is calibrated; the dispersion
around it is too wide, so one run understates how much of its own move was
the scenario. Calibration record §81.

An event study over the
five sessions after each of six dated events agrees on sign five times out of
six; the exception is the Fed's intermeeting cut of 3 March 2020, where an
announcement-effect channel is missing rather than miscalibrated. Worked
through in `examples/09-a-pandemic-shaped-market.ipynb`.

**The transient**, how fast the market *reacts* to a shock as distinct from
where it settles, is weaker still. The shipped preset retains **27.6%**
of the previous preset's shock response, because it raised factor-variance
persistence to 0.989 to buy volatility clustering, and a 63-day half-life
cannot track a twenty-day spike.

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

**Sector structure is the same shortfall measured a second way.** In calm
markets it is in band, 0.1346 at 252 days and 0.1201 at 504 against bands
starting at 0.11. Under a held VIX 45 it reads **+0.035** against a real
**+0.103** in the 2020 window, thirty seeds at 252 days; `pt-v7` on the same
recipe reads +0.064. Industries exist in this market and they hold together
in a crisis about a third as tightly as real ones, which is why the crisis
half of the retired sector gap is carried here.

**Consequence: use scenarios to ask *whether* a strategy breaks, not *how
much*.** A crisis here is about four fifths as violent as a real one and arrives
more slowly, so a strategy that survives one has not been tested as hard as
the label suggests. A sector thesis tested through a crisis is being tested
in a market whose industries loosen when a real one's would tighten.

### Gap 5: the endogenous economy cannot reach its own crisis regimes

Left to itself the macro state stays in a moderate band. Endogenous inflation
peaks at 4.06% to 4.11% over five seeds and five years against a clamp of
6.0% and a US CPI that reached 9.1% in June 2022. The cause is dispersion, sd
1.23 around a mean of 1.99%, not persistence: the monthly series has AR(1)
+0.936 against +0.894 for real CPI across 2020 and 2021.

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
must be left free or the channel is severed. `envelope.check(macro_regime=True)`
refuses the question and quotes these figures.

`inflation_reversion`, `inflation_ceiling` and `inflation_floor` are dials
since 0.1.4, shipped at the values every preset ran on. At reversion 0.20 with
the ceiling at 10 the endogenous series matches the real 2015 to 2025 mean and
dispersion, and the long-horizon equity panel pays two statistics for it, so
no preset takes them. Calibration record §65.

### Gap 6: certification was measured on a sector-balanced roster

`Universe.random()` places exactly five names in each of twelve sectors. No
real index is balanced that way. The S&P is roughly a third technology and
the Nasdaq more so. Varying **only** sector composition, with every name
drawn from one pool:

| roster | in band | `L_real` | vol% |
|---|---|---|---|
| balanced (the certified one) | 9 of 10 | 0.0000 | 27.9 |
| S&P-like mix | 8 of 10 | 0.0176 | 27.4 |
| all technology | 7 of 10 | 0.0043 | 32.8 |

**Measured on `pt-v3` against the ten-statistic panel of the time, and not
re-measured on `pt-v10`.** It is kept because what it establishes is a
property of roster composition rather than of a preset: the more
concentrated the roster, the less of the certification transfers, and part
of the certification is an artifact of that balance. The magnitudes should
be read as an earlier era's.

**Consequence: do not inherit this envelope for a sector-concentrated
roster.** Re-measure the panel on your own universe. `facts.measure()`
takes it directly, and `envelope.intervals()` will report the spread.

## The numbers above are medians, and one run is not the median

Every figure on this page is a median across thirty seeds. That is not what
a single run shows you.

Measured on the shipped preset over thirty seeds, **nine of the fourteen
statistics have their 10th-to-90th-percentile range across seeds crossing a
band edge**. `abs_return_acf1` reads a median of 0.0994 against a ceiling of
0.22, with a p90 of 0.4063 and an across-seed standard deviation of 0.1467,
larger than the median itself.

A statistic can be comfortably in band on the median and out of band on a
large minority of individual seeds. `pretium.envelope.intervals()` reports
the spread beside each median, and reading it before relying on one run is
the difference between a certified claim and a misread one.

## What this licenses

**Use pretium for:**

- Strategy evaluation over horizons up to about one year, where the edge
  depends on volatility level, lag-1 to lag-5 volatility clustering,
  day-to-day return autocorrelation, cross-sectional co-movement, fat
  tails at the annual scale, the volume-level/volatility relationship, or
  the leverage effect.
- **Relative** comparison of strategies under identical conditions. The
  engine is deterministic and bit-reproducible across platforms, so two
  strategies on the same seed differ because of the strategies.
- Testing whether a strategy survives a regime change or a stress
  scenario at all.
- Agent and RL environments where a plausible, self-consistent market is
  needed and absolute realism of every moment is not the claim.

**Do not use pretium for:**

- Multi-year backtests (Gap 2). Nothing beyond 504 days has been measured.
- Sizing a scenario's impact rather than detecting it (Gap 4). A crisis here
  is about four fifths as violent as a real one, and sector structure holds
  together in one about a third as tightly.
- Inheriting these numbers for a sector-concentrated roster (Gap 6).
- Strategies keyed on long-horizon volatility memory (Gap 3).
- Strategies trading the change in volume beyond one year (Gap 1). At one
  year the row is in band, and this list said otherwise before 0.2.0.
- Studying an inflation regime or a policy crisis from the endogenous
  economy alone (Gap 5).
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

Fitting ten numbers at every scale simultaneously optimises against that
noise. Naming what is certified, at what scale, with the gaps measured
and published, is both more honest and more useful.

## Reproducing this page

The machine-readable companion is [`envelope.json`](envelope.json), which
carries the full per-statistic detail, the gap list, and the provenance.
Cite that rather than this prose.

**Two runs, not one, and the reason is worth stating.** The certified
panel, both held-out axes and the 504-day horizon table come from one run;
the decay curve in Gap 3 comes from a second. The first run was killed at
its 1260-day stage by an out-of-memory kill, after it had persisted
everything through 504 days. Nothing was lost, and the 1260-day column
that would have appeared here is simply absent rather than quietly
back-filled from an older measurement. Both runs built from the same
source and report the same known-answer digest,
`992ef95d98e075846f13d0a312231642b26c2030833b10bd8536e374bdc185e3`.

**One caveat on that digest.** After these measurements were taken, a
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

The envelope is re-measured whenever the default preset changes. If the
digest in `envelope.json` does not match the wheel you have installed,
this page describes a different model than the one you are running.
