---
title: The realism envelope
nav_order: 14
rack: reference
short: Envelope
---

# The realism envelope

What pretium certifies, at what horizon, and — just as precisely — what it
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
[the realism metrics](realism-metrics.md) is the reference.

## The claim, in one sentence

**At a 252-day measurement horizon, the shipped `pt-v3` preset matches
nine of the ten realism statistics pretium measures, and the tenth fails
structurally and is named below.**

## What is certified

Shipped default `pt-v3`, 30 seeds, 40 instruments, 252 trading days.
Bands are `pretium.facts.REAL_MARKETS`, derived from real-market windows
of the same length by the method in the calibration docs.

| statistic | measured | band | verdict |
|---|---|---|---|
| `annualised_vol_pct` | 24.10 | 15.0 – 36.0 | in band |
| `excess_kurtosis` | 2.53 | 1.6 – 41.0 | in band |
| `return_acf1` | +0.0375 | −0.08 – 0.06 | in band |
| `abs_return_acf1` | +0.1413 | 0.02 – 0.22 | in band |
| `abs_return_acf5` | +0.0496 | 0.02 – 0.09 | in band |
| `abs_return_acf20` | +0.0082 | −0.04 – 0.08 | in band |
| `cross_sectional_corr` | +0.2558 | 0.08 – 0.56 | in band |
| `volume_abs_return_corr` | +0.5339 | 0.46 – 0.66 | in band |
| `leverage_effect` | −0.0349 | −0.16 – 0.00 | in band |
| `volume_change_acf1` | −0.4598 | −0.32 – −0.20 | **out, 13.7 sd** |

Band-distance loss `L_real` = **0.0000**.

## The claim survives the axes it was not fitted to

A model fitted to thirty seeds and reported on those same thirty seeds has
demonstrated nothing. The certified claim is re-measured on two axes the
calibration never saw:

| axis | result | `L_real` |
|---|---|---|
| training seeds (101–130), 40 names | 9/10 in band | 0.0000 |
| **held-out seeds** (1–6), 40 names | 9/10 in band | 0.0000 |
| **held-out universe** (60 names, different draw) | 9/10 in band | 0.0000 |

The same nine statistics, the same zero loss, on instruments and seeds the
search never touched. This is the part of the claim worth trusting.

## The gaps, measured

### Gap 1 — the tenth statistic is structurally unreachable

`volume_change_acf1` reads −0.46 against a real band of −0.32 to −0.20:
**13.7 seed-standard-deviations out**, and no parameter setting in the
model reaches it. A held volume level plus independent per-tick noise sits
near −0.5 at any coefficients. It is excluded from the calibration
objective deliberately — an optimiser pointed at an unreachable target
does not fail cleanly, it distorts every other parameter chasing it and
then "succeeds" by overfitting — and it is reported in every result the
library produces, as a standing falsification verdict rather than a
footnote.

**Consequence: do not trust strategies that trade the day-to-day *change*
in volume.** The volume *level*'s relationship to volatility is in band
and is fine to use.

### Gap 2 — the certified horizon is 252 days, and the model does not hold beyond it

The statistics are horizon-dependent, and the model is roughly five times
more horizon-sensitive than the market it imitates. Measured against
bands re-derived at the *matching* 504-day window — not the 252-day bands,
which would be the wrong ruler — the shipped model holds **5 of 10**:

| statistic | 252d | 504d | 504-matched band | verdict at 504d |
|---|---|---|---|---|
| `abs_return_acf1` | 0.141 | 0.289 | 0.04 – 0.22 | out |
| `abs_return_acf5` | 0.050 | 0.152 | 0.02 – 0.10 | out |
| `return_acf1` | 0.037 | 0.057 | −0.03 – 0.04 | out |
| `excess_kurtosis` | 2.53 | 5.23 | **7.1 – 22** | out |
| `annualised_vol_pct` | 24.1 | 29.3 | 16 – 34 | in |

**Consequence: pretium is not certified for multi-year backtests.** A
strategy evaluated over two years or more is being evaluated in a market
whose volatility clustering is roughly twice real, and whose tails are too
thin (see Gap 4).

### Gap 3 — volatility memory has the wrong *shape*, not the wrong length

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
slope of **−0.436** over lags 1–20 — inside the 0.2–0.4 exponent range
the literature publishes. The model fits **−0.953**, about 2.2 times
steeper. In plain terms: slightly too much clustering at lag 1, tracking
real closely at lag 5, crossing below at about lag 8, and **negative** by
lag 30, where real markets stay weakly positive out to lag 60.

The model's volatility memory does not merely fade early — it changes
sign. Where a real market still says "yesterday's turbulence makes today's
more likely" a month later, pretium says the mild opposite.

This is a **mechanism** gap, not a calibration one. No parameter setting
turns one slope into the other; it has been tried, and a two-component
variance mixture — the obvious fix — lands lag 20 while getting lag 60
wrong in both directions at once.

**Consequence: do not trust strategies whose edge depends on volatility
memory beyond about two weeks.** Vol-targeting and risk-parity overlays
that use a one-month or longer volatility estimate are outside the
envelope.

### Gap 4 — tails are too thin at long horizons

Over 504-day windows real markets show excess kurtosis of **7.1 to 22**.
The model shows **5.2**. The 252-day band's floor of 1.6 is wide enough
that this reads "comfortably in band" on every 252-day certificate the
project produces, which is why it went unnoticed: nothing was measuring
kurtosis where it fails.

**Consequence: do not calibrate tail risk or VaR against pretium at
multi-year horizons.** At the certified 252-day horizon, kurtosis is in
band.

### Gap 5 — scenario response is directional, not calibrated

The VIX shock response is materially weaker than the previous preset's.
The direction of response is right; the magnitude is not certified. Use
scenarios to ask *whether* a strategy breaks, not *how much*.

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

- Multi-year backtests (Gap 2).
- Strategies keyed on long-horizon volatility memory (Gap 3).
- Tail-risk or VaR calibration at multi-year horizons (Gap 4).
- Strategies trading the change in volume (Gap 1).
- Sizing a scenario's impact rather than detecting it (Gap 5).
- Any claim that absolute simulated performance forecasts live results.
  That is not a gap in this model; it is true of every market simulator,
  and no amount of realism work will change it.

## Why "done" is defined this way

An earlier definition of done — every statistic in band at every horizon —
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
coefficient (`momentum_theta`) for every run — and `manifest.py` folded
those into the run digest whose purpose is catching precisely that
substitution. The fix changes no simulated value: the engine had always
run `pt-v3`, and the only coefficient that differs between the two presets
in that dictionary is one the reporting path alone consumed. It does move
the known-answer digest, because the digest hashes the reported preset.
The measurements on this page are unaffected.

The envelope is re-measured whenever the default preset changes. If the
digest in `envelope.json` does not match the wheel you have installed,
this page describes a different model than the one you are running.
