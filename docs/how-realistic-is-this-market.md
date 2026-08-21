---
title: How realistic is this market
nav_order: 15
rack: reference
short: Realism
---

# How realistic is this market

**This model gets the shape of a return series right and the way things move
together wrong.** Fat tails land inside the real band, which is the hardest
property to reproduce and the one a Gaussian simulator misses entirely.
Volatility runs high, for a reason that is about how a universe is generated
rather than about the price process. Almost everything about how things move
*together* comes out weaker than a real market: across stocks, across time
within volatility, between volume and returns, and between the sign of a
return and the volatility that follows it.

One dependence comes out too strong, return autocorrelation, and that is the
mispricing process showing through rather than a property of real equities.

That split is the scope of the library. It says what a result measured here is
worth, and the rest of this page is the measurement behind it.

```python
universe = pt.Universe.random(40, seed=111)
print(pt.facts.report(pt.facts.measure(seed=3, universe=universe)))
```

```
seed 3, 40 instruments, 252 days, 10,040 daily returns

statistic                measured    real markets   verdict

marginal: one series on its own
annualised vol %           55.006   15.00 to 35.00   TOO HIGH
excess kurtosis             5.166    3.00 to 10.00   matches

dependence: how things move together
return acf(1)               0.248   -0.05 to 0.05    TOO HIGH
|return| acf(1)             0.134    0.15 to 0.35    TOO LOW
cross-sectional corr        0.027    0.25 to 0.35    TOO LOW
volume vs |return|          0.129    0.30 to 0.60    TOO LOW
leverage, r vs |r+1|       -0.018   -0.30 to -0.10   TOO WEAK
volume change acf(1)       -0.450   -0.05 to 0.15    TOO LOW

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
differ a little.

## The eight statistics

Measured on `pt.Universe.random(40, seed=111)` over 252 days, seeds 1 to 6, at
commit `a57982e`. The median across the six seeds, with the seed-to-seed range
beside it, because one seed of a fourth moment is noise rather than a model
property.

**Marginal - one series on its own**

| statistic | measured | across six seeds | real equities | |
|---|---|---|---|---|
| excess kurtosis | +4.0 | +3.6 to +5.2 | +3 to +10 | matches |
| annualised vol | 53% | 52% to 55% | 15% to 35% | too high |

**Dependence - how things move together**

| statistic | measured | across six seeds | real equities | |
|---|---|---|---|---|
| return acf(1) | +0.233 | +0.211 to +0.249 | -0.05 to +0.05 | too high |
| \|return\| acf(1) | +0.117 | +0.093 to +0.134 | +0.15 to +0.35 | too low |
| cross-sectional corr | +0.024 | +0.021 to +0.030 | +0.25 to +0.35 | too low |
| volume vs \|return\| | +0.105 | +0.087 to +0.129 | +0.30 to +0.60 | too low |
| leverage, r vs \|r+1\| | -0.004 | -0.018 to +0.005 | -0.30 to -0.10 | too weak |
| volume change acf(1) | -0.463 | -0.476 to -0.450 | -0.05 to +0.15 | too low |

The bottom four rows were added after the fact. Every realism gap found in this
project sat outside the four statistics originally reported, and all four of
those come from one instrument's price series taken on its own: nothing looked
across instruments, and nothing looked between price and volume. A realism
report that never leaves a single series keeps passing while the joint
behaviour is wrong, which is what happened here.

## What lands

**Fat tails.** Excess kurtosis +4.0, inside the +3 to +10 of real daily equity
returns in all six seeds. Extreme days are far more common here than a normal
distribution allows. This is the single most robust fact about asset returns
and the one a Gaussian price process gets wrong, so it is worth stating first.

**Volatility clusters in the right direction.** The autocorrelation of absolute
returns is positive at lag one and still positive at lag five. Calm follows
calm, which is the GARCH process doing what it is there for. The strength is a
separate question, below.

## Where it will flatter you

**Stocks barely move together.** Mean pairwise correlation of daily returns is
+0.024 against +0.25 to +0.35 for real equities in a calm market and +0.6 and
above in a crisis. This is the largest gap on the page.

The mechanism is arithmetic. The shared market factor has sigma 0.003 a day
(`rust/src/market/tick.rs`), against a sector daily sigma running 0.008 to
0.025 with a median of 0.015. A shared factor carries `(0.003 / 0.015)^2`, or
about 4%, of a typical name's variance, and that share is the pairwise
correlation it can induce. Beta dispersion across a generated universe pulls it
down further, which is why the measurement lands at +0.024 rather than +0.04.

What it costs you: diversification works far better here than in any real
market, market beta barely exists, an index built from these names is smoother
than a real index, and a hundred-name portfolio carries almost no systematic
risk. Any strategy or risk model that depends on a factor structure is being
tested in a world that does not have much of one.

**Volatility clustering is about half real strength and decays too fast.** Real
markets show +0.15 to +0.35 at lag one with a slow, near-hyperbolic decay that
persists for months. Here it is +0.117 and largely gone by lag twenty. A
strategy whose edge is volatility forecasting will look worse here than it
should.

**Volume and volatility are nearly independent.** Volume correlates with
absolute return at +0.105 against a real +0.30 to +0.60, and volume *changes*
autocorrelate at -0.463 where real ones sit near zero.

That second number is the more informative one. Volume levels here
autocorrelate above +0.9, which sounds like persistence but says nothing: a
level autocorrelation is dominated by the slowly varying level whatever the
daily dynamics are. Differencing separates the two, and -0.463 is the signature
of a smooth level plus independent daily noise. Real volume shocks persist,
because flow and news cluster: a heavy day is followed by another heavy day.

This one matters for execution work specifically. VWAP and POV both live or die
on forecasting the day's volume, and the hard part of execution in a real
market is a volume surprise that keeps going and arrives together with a
volatility surprise. Neither happens here, so an execution algorithm tested
against this market faces an easier problem than the one it was written for.

**There is no leverage effect.** Today's signed return against tomorrow's
absolute return measures -0.004 against a real -0.30 to -0.10. Bad news does
not raise volatility more than good news of the same size does.

Read the seed range on that row before reading the median: across six seeds it
runs -0.018 to +0.005, so the sign is not even stable. An effect whose sign
flips between seeds is absent rather than small, and that is a stronger
statement than "too weak".

It is absent by construction rather than by calibration, and the mechanism is
short enough to state exactly. The variance process is a symmetric GARCH(1,1),
`omega + alpha * r^2 + beta * v` (`rust/src/market/garch.rs`), and squaring the
return discards its sign. No symmetric GARCH can produce a leverage effect, at
any coefficients. Reproducing one needs an asymmetric term such as GJR or
EGARCH, which is a change to the model rather than to a calibration.

The two halves corroborate each other. There is nothing in the model that could
give this term a consistent sign, and measured over six seeds it does not have
one.

## The one that is too strong

**Returns are positively autocorrelated and real ones are not.** +0.233 at lag
one, in six seeds of six, ranging only +0.211 to +0.249. Real daily equity
returns sit near zero and are if anything slightly negative.

That is the AR(2) mispricing process showing through: its impulse response
rises to 1.284 by day two before reverting, so a shock today is amplified
tomorrow.

> Momentum is mechanically profitable in this market in a way it is not in real
> markets. An agent trading serial correlation has an edge that is an artefact
> of the process. If two agents differ mainly in how much of it they exploit,
> their ranking here says very little about which is better anywhere else.

## Volatility runs high

53% annualised against roughly 20% for large caps. A generated universe is
deliberately dispersed and skews small. Prefer ratios - capture against the
Oracle, shortfall in basis points - over raw percentages.

## Which of these can be fixed

The mismatches known before the dependence statistics existed were
investigated with the intent of correcting them rather than defending them, and
the results stand. Those figures come from that earlier work and were not
re-run at the commit above.

**The return autocorrelation can be corrected by one constant.**
`MOMENTUM_THETA` from 0.25 to 0.05 takes it to +0.034, inside the real band,
with volatility and kurtosis unchanged. It also fails seven parity tests,
including the one asserting the model constants match the reference
implementation this library is a port of. Changing that constant makes this a
fork, so the lever sits unpulled and documented.

**Clustering resists the available levers.** GARCH persistence is already 0.99,
which is what real equity GARCH shows, and raising the variance ceiling lifts
clustering by 0.016 while pushing volatility from 52.7% to 72%.

**The cross-sectional correlation is calibration, and the same constraint
applies.** Reaching a realistic calm-market +0.30 needs a market factor around
0.65x the stock sigma, roughly 0.0098 for a 1.5%-a-day name, against the 0.0030
in the source. The port reproduces the reference implementation's constants
deliberately, so that is an era-boundary decision of the same class as
`MOMENTUM_THETA` rather than a knob.

**The leverage effect needs a different variance process**, for the reason
above. A symmetric one has no coefficient that produces asymmetry, so no
calibration reaches it.

## What this market is good for

Read all of that as scope rather than as apology. The engine reproduces
microstructure honestly - a real limit order book, honest impact, partial
fills, spreads that widen under stress - and it reproduces the shape of a
return series. Use it for order-level mechanics, for cost and shortfall
measured against a known counterfactual, for reproducibility and forking
experiments, and for ranking agents against each other under identical
conditions.

Be careful with anything that depends on things moving together. This market
flatters anything that diversifies, anything leaning on a factor structure, and
anything whose risk comes from several things going wrong at once. A tail-risk
result measured here comes from a world in which the tails of different names
are largely independent, and real ones are not. An execution schedule tuned
here is tuned against volume that does not surprise you twice running.

## Re-measure after any change

`model_preset()` is versioned, but a coefficient change, a different universe
generator, or an unusual scenario can move any of these. `measure()` takes the
same arguments as the rest of the library, so a realism claim can be re-checked
rather than inherited. Every measured figure on this page came from running it
at the commit named above, except in the section that says otherwise.
