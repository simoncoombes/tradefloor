---
title: How realistic is this market
nav_order: 15
rack: reference
short: Realism
---

# How realistic is this market

**At the method this page states, the way things move together in this market
now sits inside the real bands, and what still fails is scale and memory:
volatility runs high, returns trend where real ones do not, and volume shocks
do not persist.** Stocks move together at calm-market strength and stop being
diversifiable in a crisis, volatility clusters at real strength and
market-wide, volume arrives with volatility, and bad news raises volatility
more than good news does - an effect this model could not produce at all one
era ago.

That is the reverse of what this page used to say. Through the 2026-08 era
boundary the measured thesis was *marginals right, dependence wrong*: fat
tails in band, cross-sectional correlation +0.024 against a real +0.25 to
+0.35, clustering below band, and a leverage effect whose sign would not hold
across seeds. Three of the era's model changes were aimed at exactly those
gaps - an asymmetry term in the variance process, conditional volatility on
the shared market factor, that volatility coupled to VIX - and they were
calibrated against the same method and statistics this page publishes. So the
in-band verdicts below are the calibration meeting its own target, and the
held-out section is where to read how far they generalise. The short version:
in band at the published method, at the band edge - on either side of it -
everywhere else.

```python
universe = pt.Universe.random(40, seed=111)
print(pt.facts.report(pt.facts.measure(seed=3, universe=universe)))
```

```
seed 3, 40 instruments, 252 days, 10,040 daily returns

statistic                measured    real markets   verdict

marginal: one series on its own
annualised vol %           43.866   15.00 to 35.00   TOO HIGH
excess kurtosis             3.172    3.00 to 10.00   matches

dependence: how things move together
return acf(1)               0.274   -0.05 to 0.05    TOO HIGH
|return| acf(1)             0.244    0.15 to 0.35    matches
cross-sectional corr        0.265    0.25 to 0.35    matches
volume vs |return|          0.590    0.30 to 0.60    matches
leverage, r vs |r+1|       -0.137   -0.30 to -0.10   matches
volume change acf(1)       -0.425   -0.05 to 0.15    TOO LOW

Read the two sections against each other. A model can get the shape
of one series right and still get every way things move together
wrong, and where it does it will flatter anything that diversifies,
anything leaning on a factor structure, and anything whose risk comes
from several things going wrong at once.

Carry into any conclusion: returns here are positively
autocorrelated where real ones are not, so momentum is mechanically
profitable in this market in a way it is not in real markets.
```

That is one seed. The table below is the median over six of them, so the two
differ a little - seed 3 happens to read leverage inside its band where the
six-seed median stops just short.

## The eight statistics

Measured on `pt.Universe.random(40, seed=111)` over 252 days, seeds 1 to 6, at
commit `a7994e2`, universe fingerprint `5d8de78b55aad752`. The median across
the six seeds, with the seed-to-seed range beside it, because one seed of a
fourth moment is noise rather than a model property.

**Marginal - one series on its own**

| statistic | measured | across six seeds | real equities | |
|---|---|---|---|---|
| excess kurtosis | +3.1 | +2.4 to +5.7 | +3 to +10 | matches |
| annualised vol | 41.5% | 39% to 50% | 15% to 35% | too high |

**Dependence - how things move together**

| statistic | measured | across six seeds | real equities | |
|---|---|---|---|---|
| return acf(1) | +0.249 | +0.237 to +0.443 | -0.05 to +0.05 | too high |
| \|return\| acf(1) | +0.242 | +0.189 to +0.454 | +0.15 to +0.35 | matches |
| cross-sectional corr | +0.257 | +0.205 to +0.456 | +0.25 to +0.35 | matches |
| volume vs \|return\| | +0.585 | +0.541 to +0.655 | +0.30 to +0.60 | matches |
| leverage, r vs \|r+1\| | -0.085 | -0.181 to -0.031 | -0.30 to -0.10 | too weak |
| volume change acf(1) | -0.446 | -0.454 to -0.425 | -0.05 to +0.15 | too low |

Read the verdicts with the leverage band's sign in mind: that band is
negative, so a value above it is an effect *weaker* than real markets show,
not stronger - `pretium.facts` words it "too weak" for exactly that reason,
and -0.085 sits 0.015 above the -0.10 edge.

And read the range column, not only the medians. A median can sit inside a
band its seed range escapes: kurtosis reads +2.4 on one seed of six against a
floor of +3, and correlation's range reaches +0.46. The dependence statistics
are noisier than they were an era ago, because the shared factor is one
realised path per seed and a fat-tailed one now - the price of realistic
co-movement is that six seeds no longer agree to two decimals.

## Four of these were targets

**The dependence rows stopped being pure measurements at this era boundary,
and that changes how to read them.** The sweeps that chose the era's constants
(`tools/calibration/`) scored candidates on these eight statistics, on this
exact method - this universe, these seeds, this horizon. Correlation,
kurtosis, clustering and the leverage effect are calibrated quantities now.
Return autocorrelation, the volatility level and the volume-change
autocorrelation were not targeted, and it shows: they are the three still out
of band.

A statistic a model was tuned to hit stops being evidence about the model and
starts being evidence about the tuning. The previous version of this page
could say "these numbers are not targets"; this one cannot, so it reports
what the tuning did not touch instead.

## Held out from the calibration

Three checks, each with its method, all at commit `a7994e2`.

**Fresh seeds, same universe.** Sim seeds 101 to 106 over the same
`random(40, seed=111)`, 252 days: cross-sectional correlation **+0.225**,
against a band floor of +0.25. Kurtosis +3.67, clustering +0.202 and volume
vs \|return\| +0.546 all hold in band; leverage reads -0.070, a shade weaker
than the published -0.085. Correlation is the one that slips out, by 0.025 -
just under two of its own across-seed standard deviations.

**Fresh universes.** Five 60-name universes - `Universe.random(60, seed=k)`
for k in 1, 7, 11, 42, 222 - each measured over sim seeds 1 to 6, 252 days.
Correlation medians run +0.29 to +0.35 and kurtosis +3.4 to +4.7, inside
their bands in all five. Clustering reads +0.20 to +0.21, in band. The
leverage effect weakens to -0.04 to -0.05, half its published-method value
and clearly outside the band.

**A longer horizon.** The published universe over 504 days, seeds 1 to 6:
correlation +0.34, kurtosis +3.2, clustering +0.25, leverage -0.062,
volatility 47.6%. The dependence structure holds; volatility drifts higher.

The honest summary is the one at the top: in band at the published method, at
the band edge everywhere else. Nothing collapses off the tuning point - no
held-out correlation reads +0.02 again - but the margin is thin, and the
newest, most delicate effect fades fastest: the leverage term is in band on
some single seeds, 0.015 short at the published median, and half strength on
fresh universes. A conclusion that needs a dependence statistic
deep inside its band should re-measure on its own universe and seeds rather
than inherit this table; `measure()` takes the same arguments as the rest of
the library.

## What lands

**Stocks move together, and stop being diversifiable in a crisis.** Mean
pairwise correlation of daily returns is +0.257 against +0.25 to +0.35 for
real equities in a calm market - measured at +0.024 one era ago, the largest
gap this page has ever carried. The mechanism that closed it: the shared
market factor now has its own conditional volatility
(`rust/src/market/factor_vol.rs`), a GARCH(1,1) on the factor's own daily
innovation reverting to a target that scales with (VIX/15)^2, with a baseline
sigma of 0.016 a day against the 0.003 the reference implementation carried.
The increase is funded rather than added: per-name idiosyncratic noise is
scaled down by 0.84, which is why total volatility *fell* across the change
while the factor's share of it roughly tripled.

The VIX coupling is what makes the crisis half real. Pinned VIX 45 takes mean
pairwise correlation to +0.68 and VIX 65 to +0.76 (25 names,
`Universe.random(25, seed=11)`, 120 days, sim seed 3), against +0.27 calm -
diversification fails under stress in the way real crises make it fail, where
one era ago the same pins moved correlation by less than 0.02. The
correlation blend that produces the extreme end engages above VIX 25.5. See
[Scenarios](scenarios.html) for VIX as a lever.

**Fat tails survive the correlation.** Excess kurtosis +3.1, inside the +3 to
+10 of real daily equity returns. Worth stating next to the correlation
because the previous model could have either but not both: with the factor
Gaussian at constant sigma, every point of correlation Gaussian-diluted the
GARCH tails, and the sweep that proved it found the correlation band
reachable only where kurtosis had collapsed to 1.26. A factor whose variance
is itself persistent and shock-driven is fat-tailed by variance mixing, so
the correlated share of every name's return now contributes kurtosis instead
of spending it.

**Volatility clusters at real strength, and market-wide.** The
autocorrelation of absolute returns is +0.242 against a real +0.15 to +0.35 -
measured at +0.117, below band, one era ago. Two processes carry it now: the
per-name GJR-GARCH, whose effective persistence `ALPHA + BETA + GAMMA/2` is
held at the reference's 0.99, and the factor variance process, whose shocks
decay with a half-life of about 13.5 days. That second one is the change:
calm and turbulent stretches are now market-wide regimes, which per-name
variance processes alone could never produce - the factor's share of every
name used to be iid by construction and actively diluted the clustering the
GARCH made.

The memory is still short, and that is the part to carry into a volatility
study. Real clustering decays near-hyperbolically and persists for months;
here it reads +0.090 at lag five and is gone by lag twenty (-0.006, same
method). The strength at lag one is real now; the persistence is not, so a
strategy whose edge is a volatility forecast more than a few weeks out is
still being tested against a market with less to forecast than a real one.

**Volume arrives with volatility.** Volume correlates with absolute return at
+0.585 against a real +0.30 to +0.60. It measured +0.105 before the era
boundary: the average-volume feedback compounded the volume level a
percent-plus a day, and that trend swamped the day-to-day covariation. The
level is now held (`AvgVolumePolicy::Hold`), and the per-tick channel -
volume scales with the size of the day's move by construction - shows
through.

**The leverage effect exists.** Today's signed return against tomorrow's
absolute return measures -0.085, just short of the -0.30 to -0.10 band, and
the sign is stable: negative in six seeds of six, range -0.181 to
-0.031, where the previous era's model could not hold the sign at all. That
model's variance process was a symmetric GARCH(1,1), and no symmetric GARCH
produces a leverage effect at any coefficients. The era added the asymmetry
structurally: a GJR term (`rust/src/market/garch.rs`) feeds a negative day's
squared return through at `ALPHA + GAMMA` = 0.36 against `ALPHA` = 0.02 for a
positive one. "Too weak" on this row is now a calibration statement about the
last 0.015, not a structural impossibility - though note in the held-out
section that the effect fades to half strength off the tuning point, and that
`GAMMA` = 0.34 already reads large against literature GJR fits near 0.1,
because the sector variance ceiling truncates the biggest asymmetric
responses and the constant has to buy the measured effect through that clamp.

## What still fails, and where it will flatter you

**Returns are positively autocorrelated and real ones are not.** +0.249 at
lag one, in six seeds of six. Real daily equity returns sit near zero and are
if anything slightly negative. That is the AR(2) mispricing process showing
through: its impulse response rises to 1.284 by day two before reverting, so
a shock today is amplified tomorrow. Untouched by the era boundary, and not
an accident of it - none of the era's sweeps targeted this row.

> Momentum is mechanically profitable in this market in a way it is not in
> real markets. An agent trading serial correlation has an edge that is an
> artefact of the process. If two agents differ mainly in how much of it they
> exploit, their ranking here says very little about which is better anywhere
> else.

**Volatility runs high.** 41.5% annualised against roughly 20% for large
caps - down from 53% at the previous era, because the factor's variance was
funded out of the idiosyncratic side rather than added, but still above the
band. The reason is about how a universe is generated rather than about the
price process: a generated universe is deliberately dispersed and skews
small. Prefer ratios - capture against the Oracle, shortfall in basis
points - over raw percentages.

**Volume shocks do not persist, by construction.** Volume *changes*
autocorrelate at -0.446 where real ones sit near zero. This is the one row no
calibration can reach: daily volume here is a held per-name level times
bounded per-tick multipliers that are independent day to day, and the first
difference of such a series autocorrelates near -0.5 as arithmetic. Real
volume shocks persist - flow and news cluster, a heavy day is followed by
another heavy day - and producing that needs volume dynamics the engine does
not model, not a constant it does.

This one matters for execution work specifically. VWAP and POV live or die on
forecasting the day's volume, and the hard part of execution in a real market
is a volume surprise that keeps going and arrives together with a volatility
surprise. The arriving-together half is now present; the keeps-going half is
absent, so a volume forecast here is never wrong twice running, and an
execution algorithm tested against this market still faces an easier problem
than the one it was written for.

## What this era closed, and what remains

The gaps the previous version of this page documented were investigated with
the intent of correcting them, and the era boundary is what correcting them
took. For the record, because each closure names the kind of change its gap
needed:

- **Cross-sectional correlation** was proven unreachable by the factor's
  constant sigma - the band arrived only where kurtosis collapsed - and was
  closed by a model change: conditional volatility on the factor, funded from
  the idiosyncratic side.
- **Clustering** resisted every calibration lever (persistence was already at
  the reference's 0.99; raising the variance ceiling bought +0.016 of
  clustering for 20 points of volatility) and was closed by the same factor
  process, which is what market-wide clustering needed.
- **Volume against volatility** was closed by removing the average-volume
  feedback, which was compounding the level and burying the covariation.
- **The leverage effect** was absent at any coefficients of a symmetric
  GARCH and was closed structurally, by the GJR term.

What remains, and what each would take:

- **Return autocorrelation is one constant away, and the constant stays
  unpulled.** `MOMENTUM_THETA` from 0.25 to 0.05 measured +0.034 - inside
  the real band - with volatility and kurtosis essentially unchanged. That
  counterfactual was measured on the pre-era model and has not been re-run;
  the mechanism it names is untouched. Pulling it is a smaller step than it
  once was - this era established the pattern of argued, gated divergence
  from the reference implementation - but it remains a decision about the
  mispricing process itself, not a calibration detail.
- **The volatility level** is a property of the universe generator, not the
  price process, and would be recalibrated there.
- **Volume dynamics** need a model - persistent volume shocks - not a
  constant. Until then -0.45 is structural.
- **The leverage effect's last 0.015** is expensive: `GAMMA` is already
  three times literature fits because the variance ceiling truncates the
  responses that would express it, so further asymmetry buys less and less.

## What this market is good for

Read all of that as scope rather than as apology. The engine reproduces
microstructure honestly - a real limit order book, honest impact, partial
fills, spreads that widen under stress - and now also a factor structure:
market beta exists, diversification behaves like a market's including its
failure under stress, and systematic risk is real. Use it for order-level
mechanics, for cost and shortfall measured against a known counterfactual,
for reproducibility and forking experiments, and for ranking agents against
each other under identical conditions.

Be careful with anything that leans on the rows still out of band, or on an
in-band row being deep in band off the published method. A momentum edge here
is partly the process, not the agent. Absolute volatility is high, so prefer
ratios. An execution schedule tuned here is tuned against volume that never
surprises you twice running. And a result that depends on correlation or
leverage holding at exactly the published strength should re-measure on its
own universe, because held out from the calibration those rows sit at the
band edge, not the band centre.

## Re-measure after any change

`model_preset()` is versioned, but a coefficient change, a different universe
generator, or an unusual scenario can move any of these. `measure()` takes the
same arguments as the rest of the library, so a realism claim can be
re-checked rather than inherited. Every measured figure on this page came from
running it at the commit named above, except in the two places that say
otherwise: the pinned-VIX correlations, measured on the Scenarios page's
method at the same commit, and the `MOMENTUM_THETA` counterfactual, measured
on the pre-era model and recorded as history.
