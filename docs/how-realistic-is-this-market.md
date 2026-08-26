---
title: How realistic is this market
nav_order: 15
rack: reference
short: Realism
---

# How realistic is this market

**The numbers live in [the realism envelope](realism-envelope.md).** That
page is the authoritative statement: which statistics pretium matches, at
what measurement horizon, on which held-out axes, and every gap that has
been measured rather than assumed. It is regenerated from a machine-readable
artifact, [`envelope.json`](envelope.json), whenever the shipped preset
changes.

This page is the narrative behind it: what the model could not do, what
fixing each thing took, and what is left. It carries no measured figures of
its own on purpose: two realism pages quoting different numbers is how a
project ends up contradicting itself, and this one did exactly that for a
while, describing a model two presets old.

The short version: at a 252-day horizon the shipped preset matches **all
fourteen** statistics pretium measures and holds that on a universe the
calibration never saw. At 504 days it holds thirteen. Beyond 252 days it is
not certified, and the envelope says why.

## What each gap took to close

The gaps earlier versions of this page documented were investigated with the
intent of correcting them. Each closure is recorded here because the *kind*
of change it needed is the useful part, and most were not calibration at
all.

- **Cross-sectional correlation** was proven unreachable by the factor's
  constant sigma: the band arrived only where kurtosis collapsed. Closed by
  a model change: conditional volatility on the factor, funded from the
  idiosyncratic side.
- **Volatility clustering** resisted every calibration lever. Persistence
  was already at the reference's 0.99, and raising the variance ceiling
  bought +0.016 of clustering for twenty points of volatility. Closed by the
  same factor process, which is what market-wide clustering needed.
- **Volume against volatility** was closed by removing the average-volume
  feedback, which was compounding the level and burying the covariation.
- **The leverage effect** was absent at any coefficients of a symmetric
  GARCH. Closed structurally, by the GJR term.
- **Return autocorrelation** was, as an earlier version of this page put it,
  "one constant away, and the constant stays unpulled." It has since been
  pulled: `momentum_theta` moved from 0.25 to 0.0742 in the calibrated
  preset, and return autocorrelation is now in band. The counterfactual that
  page recorded turned out to be right.
- **The volatility level** was a property of the universe generator rather
  than the price process, and was recalibrated there. It is now in band,
  where for a long time it was the model's most conspicuous failure.

## What is left

Three of these were not visible from a 252-day panel and were found by
measuring somewhere the panel does not look. That is the pattern worth
carrying forward: every remaining gap was found by changing the measurement,
not by tuning harder.

- **Volume dynamics beyond one year.** A held volume level plus independent
  per-tick noise sits near −0.5 at any coefficients, which is why this row
  was called structural for a year. The mechanism arrived rather than the
  coefficient: a persistent log-volume state, switched on in the shipped
  preset, puts the row inside its one-year band without costing the
  volume-and-volatility row its own. The two-year band is tighter, and the
  model sits just outside it there.
- **The certified horizon is 252 days.** The statistics are horizon-dependent
  and the model is roughly five times more horizon-sensitive than the market
  it imitates. Measured against bands re-derived at the matching window, the
  wrong ruler being the easy mistake here, it holds thirteen of fourteen at
  504 days. Nothing beyond 504 days has been measured at all, which is the
  part of this gap that no preset closes.
- **Volatility memory has the wrong shape.** Real markets' decays
  hyperbolically; this model's decays exponentially, because it is built from
  exponentials. Over one year two exponentials fake a power law well enough
  that no statistic in the panel objects. The fake thins out as the window
  grows, and the memory goes mildly *negative* around lag 30 where a real
  market is still weakly positive. No parameter setting turns one slope into
  the other. This is a mechanism gap, and a two-component variance mixture,
  the obvious fix, was tried and is not sufficient.
- **Tails at long horizons, closed and worth recording.** Over two-year
  windows real markets are far more kurtotic than this model used to be, and
  the 252-day band was wide enough to hide it. That gap closed at 0.2.0:
  two-year excess kurtosis now lands inside its band, though near the floor.
  It is listed here rather than deleted because the way it was found, by
  measuring somewhere the panel does not look, is the transferable part.

## What this market is good for

Read that as scope rather than apology, and read
[the envelope](realism-envelope.md) for the precise version, which states
what the model licenses and what it does not, in those terms.

The engine reproduces microstructure honestly: a real limit order book,
honest impact, partial fills, spreads that widen under stress. On top of it
there is a genuine factor structure. Market beta exists, diversification
behaves like a market's including its failure under stress, and systematic
risk is real. Use it for order-level mechanics, for cost and shortfall
against a known counterfactual, for reproducibility and forking experiments,
and for ranking agents against each other under identical conditions.

Be careful with anything leaning on a row that is out of band, on an in-band
row sitting at the band edge rather than its centre, or on a horizon longer
than the certified one. An execution schedule tuned here is tuned against
volume that never surprises you twice running.

## Re-measure after any change

A coefficient change, a different universe generator, or an unusual scenario
can move any of this. `measure()` takes the same arguments as the rest of the
library, so a realism claim can be re-checked rather than inherited:

```python
universe = pt.Universe.random(40, seed=111)
print(pt.facts.report(pt.facts.measure(seed=3, universe=universe)))
```

If the known-answer digest recorded in `envelope.json` does not match the
wheel you have installed, the envelope describes a different model than the
one you are running, and its numbers are not yours.
