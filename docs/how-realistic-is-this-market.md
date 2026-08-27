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
fixing each thing took, and what is left. It measures nothing of its own on
purpose: every figure here is quoted from the envelope, from the calibration
record behind it, or from a preset's own coefficients, and the only numbers
computed on this page are the printed output of code you can run yourself.
Two realism pages each taking their own measurement is how a project ends up
contradicting itself, and this one did exactly that for a while, describing
a model two presets old.

The short version: at a 252-day horizon the shipped preset matches **all
fourteen** statistics pretium measures and holds that on a universe the
calibration never saw. Against bands re-derived at 504 days it holds all
fourteen there too, which is the first two-year clean sheet this project has
measured; on a held-out set of seeds it holds thirteen. Beyond 252 days it
is still not certified, and the envelope says why: the horizon is where the
certification was taken, not a count of rows in band.

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
  pulled, and then pulled twice more: `momentum_theta` moved from 0.25 to
  0.0742 at `pt-v3`, halved at `pt-v6` and halved again at `pt-v9`, so the
  shipped `pt-v12` runs 0.0186. Return autocorrelation is in band. The
  counterfactual that page recorded turned out to be right. Both later
  halvings were driven by the two-year reading rather than the one-year one:
  by `pt-v5` return autocorrelation was already inside its 252-day band and
  outside the 504-day one, which is what `pt-v6` halved the coefficient to
  fix, and `pt-v9` halved it again to keep the two-year reading inside.
- **The volatility level** was a property of the universe generator rather
  than the price process, and was recalibrated there. It is now in band,
  where for a long time it was the model's most conspicuous failure.
- **Volume change from one day to the next** was called structural for a
  year: a held volume level plus independent per-tick noise sits near −0.5
  whatever the coefficients. The mechanism arrived rather than the
  coefficient, and it took two steps. A persistent log-volume state put the
  one-year row inside its band without costing the volume-and-volatility row
  its own; the two-year row stayed just outside until `pt-v12` lifted
  `volume_move_cap` off the hard-coded 4.0 in `tick.rs` that had saturated a
  name's volume response at a 4% daily move, so that every crisis day traded
  like a bad Tuesday. Both rows are inside both bands now, which is what
  took the envelope from six gaps to five.
- **Tails at long horizons** were far too thin over two-year windows, and
  the 252-day band was wide enough to hide that completely. Closed at 0.2.0:
  two-year excess kurtosis lands inside its band, though near the floor. It
  is recorded rather than deleted because of how it was found, by measuring
  somewhere the certified panel does not look, which is the transferable
  part.

## What is left

Five gaps ship with `pt-v12`, and the envelope carries the measurement
behind each one. Not one of them was visible from the certified 252-day
panel. Each was found by changing the measurement rather than by tuning
harder: a longer window, a lag past the last one the panel reports, a driven
scenario, a five-year run of the endogenous economy, a differently shaped
roster. That is the pattern worth carrying forward.

- **The certified horizon is 252 days.** The statistics are
  horizon-dependent and the model is roughly five times more
  horizon-sensitive than the market it imitates, a ratio measured in the
  `pt-v10` era and not re-measured since. Against bands re-derived at the
  matching window, the wrong ruler being the easy mistake here, it holds all
  fourteen at 504 days. The horizon stays at 252 anyway, and the band count
  is not the reason: a certification is a measurement, and this one was
  taken at 252 days on thirty seeds. Beyond 504 days is measured rather than
  silent now, `pt-v12` having been run to 756, 1260 and 2520 days on thirty
  seeds, but it is measured against the 504-day bands, which are the wrong
  ruler for a ten-year window and the only ruler anyone has derived. What
  that ruler does settle is that nothing runs away: annualised volatility
  year by year across the ten years is flat.
- **Volatility memory has the wrong shape.** Real markets' volatility
  autocorrelation decays hyperbolically; this model's decays exponentially,
  because it is built from exponentials. Over one year two exponentials fake
  a power law well enough that no statistic in the panel objects. The fake
  thins out as the window grows, and the memory goes mildly *negative*
  around lag 30 where a real market is still weakly positive. No parameter
  setting turns one slope into the other. This is a mechanism gap, and a
  two-component variance mixture, the obvious fix, was tried and is not
  sufficient. The ten-year run added one thing worth having: the model
  already carries two timescales rather than one. De-trend the slowly
  varying variance level and the lag-1 and lag-5 memory mostly survives,
  which is the GJR recursion, while most of lag 20 does not, which means the
  long lags are a variance *level* rather than memory.
- **A scenario's size is right on average and unreliable in one run.**
  Driving a real macro path through the model and regressing daily returns
  on each driver's daily change, every channel carries the sign theory fixes
  in advance and the gains land within ten percent of the real name they are
  measured against, on a driven window measured in the `pt-v10` era. Two
  things are left. The spread, which is the one figure from that run
  re-measured on `pt-v12`: the model's residual dispersion over that window
  is about half again as wide as real, the worst axis in the model, so one
  run can put the size almost anywhere. And the speed: the shock arrives
  more slowly than a real one, because the factor-variance persistence that
  buys volatility clustering carries a half-life too long to track a
  twenty-day spike. A two-timescale variance
  mixture was built to separate those two jobs and made the horizon
  monotonically worse, so this is a structural limit rather than a
  calibration nobody has run. Ask a scenario whether a strategy breaks, and
  size the break across seeds rather than from one run.
- **The endogenous economy cannot reach its own macro crisis regimes.** A
  volatility crisis is endogenous, the model's own VIX crossing its crisis
  threshold at roughly the rate a real one does, on a measurement taken in
  the `pt-v10` era. A macro crisis is not. Left to itself, inflation stays in
  a moderate band well under what real CPI reached in 2022, and the central
  bank's crisis cadence, which depends on it, never fires in a default run.
  So an inflation regime or a policy crisis has to be driven through a
  scenario, and so does the policy response to it. The dials that would
  widen the range have existed since 0.1.4 and no preset takes them. The
  repository records two reasons for that and settles neither: that what a
  real inflation range does to the equity panel has not been scored, and
  that it costs the two-year panel.
- **Certification was measured on a sector-balanced roster.**
  `Universe.random()` assigns sectors round-robin, so a roster is as close to
  balanced as its size allows, and no real index is. That used to cost the
  one-year panel and on `pt-v12` it no longer does: an S&P-like mix, a
  technology-heavy one and a defensive one each hold all fourteen at one
  year, and a single-sector roster holds all thirteen that are defined on it.
  What concentration costs now is the second year. The gap is kept for that
  reason, and because what it establishes is a property of roster
  composition rather than of a preset.

## What this market is good for

Read that as scope rather than apology, and read
[the envelope](realism-envelope.md) for the precise version, which states
what the model licenses and what it does not, in those terms.

The engine reproduces microstructure honestly: a real limit order book,
honest impact, partial fills, spreads that widen under stress. On top of it
there is a genuine factor structure. Market beta exists, diversification
behaves like a market's including its failure under stress, and systematic
risk is real. There is a sector structure as well, in band at one year and
at two, and since `pt-v12` it tightens under a held crisis about as much as
a real one's, where it used to tighten about a third as much. Use it for
order-level mechanics, for cost and shortfall against a known
counterfactual, for reproducibility and forking experiments, and for ranking
agents against each other under identical conditions.

No row of the fourteen is out of band at the certified horizon, so the
caution has moved to where it belongs. Be careful with anything leaning on
an in-band row that sits at the band edge rather than its centre, which four
of them do at 504 days, or on a horizon longer than the certified one. And
do not read one run's move as the size of a scenario: the expected response
is calibrated and the dispersion around it is not.

## Re-measure after any change

A coefficient change, a different universe generator, or an unusual scenario
can move any of this. `measure()` takes the same arguments as the rest of the
library, so a realism claim can be re-checked rather than inherited:

```python
import pretium as pt

universe = pt.Universe.random(40, seed=111)
print(pt.facts.report(pt.facts.measure(seed=3, universe=universe)))
```

That is one seed, and every figure in the envelope is a median across
thirty. A single run misses rows the median holds, and this one does: it
reports return acf(1) above its band, on a preset whose median for that row
is inside it. `envelope.intervals()` takes the panels you measured and
reports the across-seed spread beside each median, which is what a claim
should be read against:

```python
import pretium as pt
from pretium import envelope

universe = pt.Universe.random(40, seed=111)
panels = [pt.facts.measure(seed=s, universe=universe) for s in range(5)]
row = envelope.intervals(panels)["return_acf1"]
print(row["median"], row["band"], row["typical_straddles"])
```

```
0.0017059960621254296 (-0.08, 0.06) True
```

The median sits near zero, inside the band, and `typical_straddles` says the
10th-to-90th-percentile range crosses a band edge, so a single seed landing
outside is the expected behaviour of the statistic rather than a defect in
your universe.

`envelope.json` records the preset its figures describe rather than a
digest, so the check that the envelope is describing the model you are
running is a one-liner, run from wherever you keep the file:

```python
import json, pretium as pt

print(pt.model_preset()["name"] == json.load(open("envelope.json"))["preset"])
```

If those differ, the envelope describes a different model than the one you
are running, and its numbers are not yours.
