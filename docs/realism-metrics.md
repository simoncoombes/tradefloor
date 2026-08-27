---
title: The realism metrics
nav_order: 16
rack: reference
short: Metrics
---

# The realism metrics

What every number a preset is judged on actually measures, why it is in the
panel, and what a failure on it means for your results.

This is the reference for the vocabulary. [The realism envelope](realism-envelope.md)
states which of these pretium currently passes and at what horizon; this
page explains what passing one *means*.

Five families, and they answer different questions:

| family | question it answers |
|---|---|
| [the fourteen panel statistics](#the-fourteen-panel-statistics) | does a simulated market look like a real one? |
| [uncertainty](#uncertainty-what-a-single-run-actually-shows-you) | how much does a single run differ from the headline? |
| [the aggregate losses](#the-aggregate-losses) | how far off is it, in one number a search can minimise? |
| [the validation axes](#the-validation-axes) | does the answer survive conditions the calibration never saw? |
| [the scenario metrics](#the-scenario-metrics) | does it respond to a crisis the way a market does? |

There is also [the decay curve](#the-decay-curve), which is not a family so
much as the one measurement that reads the panel's clustering lags as a
*shape* rather than as three unrelated numbers.

## The fourteen panel statistics

`pretium.facts.measure()` returns these. Every band in
`facts.REAL_MARKETS` comes from **ten consecutive 252-day windows of 40 US
large caps, 2015-2025**, measured with this module's own estimators. Ten
were measured and **nine** derive the band: the window straddling the COVID
crash is held out and reported separately as each band's `crisis_window`,
because the panel is a claim about a typical year and crisis behaviour is
measured under pinned scenarios instead. A band is not a textbook figure,
it is what this measurement returns on real data. Each band's empirical
claim ships as data in `facts.REAL_MARKETS_PROVENANCE`, quoted below, where
`windows` is (min, median, max) over those nine and `crisis_window` is the
tenth on its own.

### Marginal properties: one price series on its own

**`annualised_vol_pct`**: pooled across-name annualised daily volatility
over one year.

How violent the market is. The single most consequential number for any
strategy: it sets the scale of every gain and every loss. Too high and
every Sharpe ratio you measure is depressed and every stop is hit too
often; too low and risk looks free. Real windows ran 18.3 / 25.9 / 30.7
(min / median / max).

**`excess_kurtosis`**: excess kurtosis of the pooled standardised daily
returns.

How fat the tails are: how often a day lands far from typical. Zero is a
normal distribution; real markets are strongly positive, because crashes
and melt-ups happen far more often than a bell curve allows. **This is the
statistic that decides whether tail risk means anything in your results.**
Real windows: 5.6 / 11.1 / 36.7.

### Time-series dependence: how a series relates to its own past

**`return_acf1`**: median across names of the lag-1 autocorrelation of
daily log returns.

Whether today's return predicts tomorrow's. Real markets sit near **zero**
by arbitrage: if it were reliably positive, buying yesterday's winners
would be free money and someone would have taken it. A model with a large
positive value hands strategies a momentum edge that does not exist, which
is why an earlier preset reading +0.243 was the single most misleading
defect this project has had. Real windows: −0.046 / −0.006 /
+0.030.

**`abs_return_acf1`, `abs_return_acf5`, `abs_return_acf20`**: median
across names of the lag-1, lag-5 and lag-20 autocorrelation of daily
**|log return|**.

**Volatility clustering**: turbulent days follow turbulent days, calm
follows calm. Returns themselves are unpredictable, but their *size* is
strongly predictable, and this is the most robust empirical regularity in
finance. Three lags rather than one because the *shape* of the decay is
the point, meaning how fast the memory fades. Real windows at lag 1: 0.039 /
0.083 / 0.176; at lag 20: −0.015 / +0.020 / +0.059.

A model that gets lag 1 right and lag 20 wrong has volatility memory of
the wrong *duration*, which matters to anything sizing positions off a
trailing volatility estimate.

**`leverage_effect`**: median across names of the correlation between
today's **signed** return and tomorrow's **|return|**.

Bad news raises volatility more than good news does. Negative in real
markets, and asymmetric in a way a symmetric model cannot produce at any
coefficients. It took a structural change (a GJR term) to reach it here
at all. Real windows: −0.113 / −0.042 / +0.014.

### Cross-sectional dependence: how names relate to each other

**`cross_sectional_corr`**: mean pairwise correlation of daily returns
across the roster.

Whether stocks move together. **This is what decides whether
diversification works**, and whether it stops working in a crisis, which
is exactly when a portfolio needs it to hold. A model with near-zero
cross-sectional correlation makes any long/short or portfolio result
meaningless, because the risk being diversified away was never shared.
Real windows: 0.169 / 0.346 / 0.477.

That single mean is blind to three things a portfolio cares about, and the
three statistics below exist because a search cannot preserve what it cannot
see. Each conditions the same pairwise correlation on something.

**`corr_asymmetry`**: mean pairwise correlation on days the equal-weight
market return is below minus one standard deviation, minus the same on days
above plus one.

Whether stocks move together **more on the way down**. In real markets they
do: diversification is weakest exactly when it is needed. A model whose
factor loading is symmetric in sign reads near zero here. The band is wide,
because a one standard deviation threshold leaves about forty sessions per
tail in a year, and it admits zero. Real windows: −0.154 / +0.083 / +0.348.

**`corr_asymmetry_lagged`**: the same, conditioned on the previous day's
market return.

Separates a same-day signed loading from the lagged route through
volatility, where yesterday's fall raises today's factor variance. The model
has the second route and not the first. Real windows: −0.092 / +0.111 /
+0.438.

**`sector_excess_corr`**: mean same-sector pairwise correlation minus mean
cross-sector, using each instrument's sector label.

Whether names in the same industry co-move more than names in different
ones. **This decides whether a sector bet is a bet at all**: a long energy,
short tech position in a model with no sector structure is a random pair of
names with a label. Real windows: +0.133 / +0.164 / +0.200, and every one of
ten windows including the 2020 crisis sits between +0.10 and +0.20. A decade
that stable makes the band only 0.12 wide, which ties `volume_change_acf1`
and `abs_return_acf20` for second narrowest on the panel behind
`abs_return_acf5` at 0.07. Narrow in market units is not the same as strict,
though. Measured in the units `sd_out` reports and `L_real` scales by, that
0.12 is **17.6** across-seed standard deviations, because `facts.SEED_SD`
reads 0.0068 here: the model's sector spread barely moves from seed to seed,
so this is the second most forgiving band of the fourteen, behind
`excess_kurtosis` at 33.6.

**Consequence if it fails: any result that depends on sector rotation,
sector-neutral construction or industry diversification was produced by a
market that does not have industries.**

### Volume

**`volume_abs_return_corr`**: median across names of the correlation
between daily share volume and the same day's |log return|.

Volume arrives with volatility: big moves are heavily traded. Matters for
execution, because it says whether liquidity is there when the price is
moving. Real windows: 0.502 / 0.536 / 0.617.

**`volume_change_acf1`**: median across names of the lag-1
autocorrelation of daily *relative volume changes*.

Whether volume shocks **persist**. Real markets: mildly negative, meaning
a volume spike partly decays but leaves a residue. A model with volume as
a fixed level plus independent noise lands near −0.5 at any coefficients,
because differencing near-independent draws is mechanically
anti-correlated. Real windows: −0.296 / −0.255 / −0.221.

**Consequence if it fails: an execution schedule tuned in the model is
tuned against volume that never surprises you twice running.**

### Measured but not judged

**`skew`**: the skewness of the pooled standardised daily returns.

Returned by `measure()` on every panel, and **it has no band**. Real equity
returns are negatively skewed: crashes are sharper than rallies, so the left
tail is longer. Nothing in this library currently scores that, which means a
model could have the wrong sign on it indefinitely and no verdict would say
so.

It is called out here rather than left in the dict because it is the natural
gate for the endogenous jump mechanism, because a symmetric jump process produces
fat tails with the *wrong* skew, which would read as "kurtosis improved" on
the panel while getting crises backwards. `jump_mean_market` exists to carry
that asymmetry, and `skew` is what would prove it.

### Banded and judged, but outside the calibration objective

Five of the fourteen are measured, given a band and a verdict, and left out
of the loss the search minimises: `volume_change_acf1`, `corr_asymmetry`,
`corr_asymmetry_lagged`, `sector_excess_corr` and `corr_persistence_acf1`,
which is exactly `pretium.loss.STRUCTURAL`. They appear in every `L_real`
breakdown with the role `structural` and contribute zero to its scalar,
because no lever has been shown to move them cleanly and an optimiser
pointed at one of them distorts everything else chasing it. Membership is a
statement about the objective, not about reachability: the shipped `pt-v12`
holds all five in band at the certified horizon anyway. Four are documented
in the banded sections above; the fifth is only ever discussed here.

**`corr_persistence_acf1`**: mean pairwise correlation on non-overlapping
21-day windows, then the lag-1 autocorrelation of that series.

Whether a panic leaves a trace: correlation that rose last month is still
elevated this month. Real windows at 252 days read -0.05 / 0.23 / 0.40 (min /
median / max), so the band there, (-0.19, 0.54), admits every preset and is
recorded as a ruler that cannot read: twelve windows in a year is too few. At
504 days the four non-crisis windows read 0.25 to 0.43 and the band is (0.19,
0.49). The shipped `pt-v12` reads **+0.1525** at 252 days and **+0.2077** at
504, in band at both, and only 0.018 above the floor of the second. Its seed
noise (0.28 at 252 days, 0.20 at 504) is the largest of any correlation-type
statistic on the panel, which is why this is the one row that leaves band on
the held-out-seed axis: 14/14 on the certification's seeds, 13/14 on thirty
seeds the calibration never used. In this model window correlation is almost
exactly the market factor's variance (r = 0.92 within a run), so this
statistic measures how much memory that variance has; the calibration
record's §64 is the account.

### Provenance fields, not statistics

Every panel also carries `days`, `instruments`, `observations`,
`dependence_instruments`, `dependence_observations`, `seed`,
`model_fingerprint` and `universe_fingerprint`. These are not measurements
of the market; they are what lets a reader reproduce the measurement, and
they answer "how much data is behind this number". A correlation over 8
instruments and one over 60 are not the same claim.

## Uncertainty: what a single run actually shows you

Every banded number above is a **median across seeds**, and the spread
around it is large enough to change the answer. `pretium.envelope.intervals()`
reports it, per statistic:

| field | what it is |
|---|---|
| `median` | the point estimate a single panel would report |
| `low`, `high` | the actual min and max across seeds |
| `p10`, `p90` | the 10th and 90th percentiles |
| `sd` | across-seed standard deviation, measured on these panels |
| `shipped_sd` | `facts.SEED_SD`, measured once at the baseline |
| `distance`, `sd_out` | band distance, and that distance in units of noise |
| `extremes_straddle` | min or max crosses a band edge |
| `typical_straddles` | the **p10 to p90 range** crosses a band edge |

`typical_straddles` is the one to read. `extremes_straddle` fires when a
single seed of thirty crossed an edge, which is close to expected and is
information rather than a finding. `typical_straddles` says the middle
eighty percent crosses, so a user running one seed is *likely*, not merely
able, to measure out of band on a statistic whose median sits comfortably
inside.

Measured on `pt-v10` over thirty seeds, **nine of the fourteen statistics
straddle by that test**. `abs_return_acf1` read a median of 0.0994 against a
ceiling of 0.22, a p90 of 0.4063, and an across-seed standard deviation of
0.1467, larger than the median itself. Read that as the previous era's
dispersion, not the shipped one: `pt-v12` moves that median to **0.1107**
and the spread around it has not been re-measured, so the p90, the standard
deviation and the nine-of-fourteen count all still describe `pt-v10`. The
honest move is to run `envelope.intervals()` on your own seeds rather than
inherit either era's.

### Why there is no confidence score

There is deliberately no scalar. The reason is not modesty: aggregation
destroys the only information that matters here, because a model is
realistic in some respects, at some measurement scale, and not others.

There is also a practical failure mode. A scalar travels and a caveat does
not. "87% realistic" is quotable in a way that "volatility clustering runs
roughly twice real beyond one year" is not, so a single number reliably
becomes the thing people cite *instead of* the gaps, which is exactly
backwards, since the gaps decide whether a result means anything.

What replaces it is per-statistic uncertainty, above, and a membership
check that returns a boolean with its reasons attached:

```python
from pretium import envelope

print(envelope.check(horizon_days=756, statistics=["abs_return_acf20"]))
# OUTSIDE the envelope
#   - horizon 756d exceeds the certified 252d. At 504 days the model holds
#     all 14 against horizon-matched bands. The thin one is
#     annualised_vol_pct at 33.89 against (16.0, 34.0). Beyond 504 days
#     nothing has been measured at all
#   - abs_return_acf20 depends on the decay shape, which is a mechanism
#     gap: log-log slope -0.953 against real markets' -0.436, and the curve
#     is negative by lag 30 where real markets stay positive to lag 60
```

(The two reason lines are one line each in the real output; they are wrapped
here to fit the page.) Note what the first reason does **not** say. Holding
all fourteen at 504 days is not a certification at 504 days, because the
certification is the whole apparatus of validation axes below, not a band
count. The refusal is about the horizon, and it stands.

Unknown statistic names raise rather than being ignored, because a silently
dropped name is a silently granted certification.

## The decay curve

Not part of any panel, and it ships as data rather than as a script.
`envelope.DECAY_252` carries the model's |return| autocorrelation at ten
lags out to 60 and `envelope.REAL_DECAY` carries real markets' at the lags a
real window can resolve; `envelope.DECAY_SLOPE` and
`envelope.REAL_DECAY_SLOPE` carry the exponents fitted through lags 1 to 20.
The thirty-seed 504-day survey behind them is committed at the repo root as
`decay-curve-504.json`, so the fit is reproducible from the tree: a
least-squares line on log-log axes through that file's
`real_median_noncrisis` over lags 1 to 20 returns -0.4364, which is the
shipped -0.436, and the same fit over `envelope.DECAY_252` returns -0.9522,
which is the shipped -0.953. The atlas survey records a cheaper three-lag
proxy for the same shape, `decay_slope()` in
`tools/calibration/atlas_survey.py`, which is a pure function of the panel's
own lag-1, lag-5 and lag-20 medians and so costs no extra simulation;
`tools/calibration/read_decay_survey.py` reads it back out of a survey.

The panel reads lags 1, 5 and 20 as three unrelated levels. The decay curve
reads the **slope through the whole range**, seven lags from 1 to 20, which
is the quantity the literature publishes an exponent for and the one that
separates this model from a market. On log-log axes, where a power law is a
straight line, real markets fit about **−0.44** and the model about
**−0.95**, so the model's memory fades about 2.2 times as fast.

It exists because no single lag reveals a shape. A model can match lag 1 and
lag 5 exactly and still have memory of an entirely wrong kind, which is what
"built from exponentials imitating a power law" looks like from inside the
panel: fine over one year, thinning as the window grows.

## The aggregate losses

**`L_real`**: the band-distance loss, from `pretium.loss`:

```
d_k    = max(0, lo_k - m_k, m_k - hi_k)     # zero INSIDE the band
L_real = sum over k of (d_k / s_k)^2
```

`m_k` is the statistic's median across seeds, `[lo_k, hi_k]` its band, and
`s_k` its across-seed standard deviation at the shipped baseline
(`facts.SEED_SD`). `k` runs over **nine** of the fourteen: the five in
`loss.LIVE_TARGETS` and the four in `loss.CONSTRAINTS`. The other five,
`loss.STRUCTURAL`, are measured and scored and appear in the breakdown with
the role `structural`, and contribute zero to the scalar.

Three properties are deliberate. It is **zero anywhere inside the band**,
because a band is a range of acceptable answers and there is no credit for
sitting in the middle of one. Each exit is priced in units of **that
statistic's own sampling noise**, so a miss on volatility (measured in tens)
and a miss on an autocorrelation (measured in hundredths) are comparable.
And there is **no unweighted version**, because an unweighted sum of these
nine is a volatility objective wearing a nine-statistic costume: drop `s_k`
and a miss on `annualised_vol_pct` is counted in percentage points while a
miss on `abs_return_acf5` is counted in hundredths, so the sum is whatever
volatility is doing. `band_distance_loss` raises on a missing or zero `s_k`
rather than falling back to 1.0, because an unweighted term is a choice and
it will not make that choice by accident.

**It is an optimisation device, not a published score.** The library
refuses to emit a single realism number, and
[the envelope](realism-envelope.md) says why. `L_real` returns the full
per-statistic breakdown with the scalar inside it rather than a bare float.

**`dual_horizon_loss`**: `L_real` at 252 days *plus* `L_real` at 504 days,
each against its own bands (`REAL_MARKETS` and `REAL_MARKETS_504`) and its
own noise scale (`SEED_SD` and `SEED_SD_504`).

This exists because three consecutive calibration searches bought 252-day
realism by spending 504-day realism, each by a different route. The cause
was structural: the objective read one horizon and the validation read the
other, so the trade was invisible to the optimiser and only appeared
afterwards. Both rulers are horizon-specific, and the 504-day noise scales
differ from the 252-day ones by factors from **0.71 to 3.23**
(`corr_persistence_acf1` shrinks most, `excess_kurtosis` grows most), so
pairing one horizon's measurement with the other's scale is a real error,
not a technicality.

## The validation axes

Every calibration certificate reports `L_real` on four axes. Only the
first is what the search optimised; the rest are what make the result worth
anything.

| axis | what it holds out |
|---|---|
| **training seeds** | nothing, this is what was fitted |
| **held-out seeds** | different random draws, same universe |
| **held-out universe** | a different roster of instruments |
| **held-out horizon** | a 504-day window instead of 252 |

A model fitted to thirty seeds and reported on those same thirty seeds has
demonstrated nothing. The overfitting control rejects a candidate on either
of two counts, and every certificate carries the rule as text beside its
verdict: any **trained-to** statistic in band on the training seeds and out
of band on a validation axis, or a validation `L_real` exceeding the
training `L_real` by more than twice the bootstrap spread of training
`L_real` across its own seeds. "Trained-to" means the nine statistics in the
loss, not all fourteen, and the second clause is priced against a resampled
spread rather than a bare multiple, because a bare multiple is a threshold
on a number whose own sampling noise nobody measured.

The first clause is the one that actually fires. In
`tools/calibration/results/calibrate-pt-v3-converged-2026-08-22.json` no
validation axis comes near its threshold -- training `L_real` is 0.0000,
held-out seeds 0.0000, the held-out universe 0.0070 and the held-out
horizon 0.0058, against the certificate's reported threshold of 0.3088 --
and the verdict is still "rejected by §8", on two flips: `return_acf1`
reads 0.0644 on the held-out universe against a band of (-0.08, 0.06), and
`abs_return_acf5` reads 0.0942 on the held-out horizon against (0.02,
0.09). A near-zero aggregate loss hides both, which is the argument for
keeping the per-statistic breakdown.

The horizon axis is scored against its own yardstick: the same 504-day
window measured on the *training* seeds, not the 252-day threshold. The
two horizons are graded against different bands, so comparing them asked
whether the two-year problem is harder than the one-year problem, to which
the answer is yes for every vector ever built, and a control that rejects
the incumbent for that is not measuring the candidate.

## The scenario metrics

Measured by `tools/calibration/scenario_response.py`, and **invisible to
the panel**, because the panel is measured at a single flat VIX on one horizon, so
a vector can be perfect on all fourteen statistics while the market's response
to a crisis changes underneath it. That is not hypothetical: it happened,
and this instrument exists because of it.

**VIX lever (steady-state)**: the ratio of realised volatility at a high
held VIX to a low one. How much more violent a sustained crisis is than a
calm market. Real markets read **×6.16** (17.2% annualised below VIX 12
against 106.1% above VIX 45, on the 40-name reference roster).

**VIX shock (transient)**: the ratio of realised volatility during a
20-day VIX spike to a flat baseline. How fast the market *reacts*, as
distinct from where it eventually settles. The two are separate
mechanically: a variance process with a long half-life reaches the right
level for a sustained crisis and cannot track a short spike, so a model can
have the right steady state and the wrong reaction.

**Both are the metrics that matter most for scenario testing**, and neither
is in the calibration objective. They are checked as gates rather than
optimised, because the panel cannot see them.

## A caution about all of it

Every number here is a **median across seeds**. That is not the same as
what one run shows you: measured on `pt-v10` over thirty seeds, nine of the
fourteen statistics had their 10th-to-90th-percentile range across seeds
crossing a band edge, and that spread has not been re-measured on `pt-v12`.
A statistic can be comfortably in band on the median and out of band on a
large minority of individual seeds. The certification itself shows the
effect in public: `corr_persistence_acf1` is in band at 0.1525 on the
certification's seeds and out of it on thirty seeds the calibration never
used.

`pretium.envelope.intervals()` reports the actual spread beside each
median, and it is the honest thing to read before relying on a single run.
