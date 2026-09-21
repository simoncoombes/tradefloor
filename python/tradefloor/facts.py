"""Stylised facts: what these markets look like, measured, next to real ones.

A simulator you cannot characterise is a simulator you cannot reason about. If
you are going to conclude anything from a strategy's performance here, you need
to know which properties of real markets this model reproduces and which it
does not, and the second list is the one that matters, because that is where a
conclusion will fail to transfer.

So this measures, and the numbers below were produced by running it. An
earlier era could add "they are not targets the model was tuned to hit";
this one cannot -- four of the panel's statistics became calibration
targets at the 2026-08 era boundary -- so the disclosure of which, and of
how the held-out checks read, is part of the measurement now. The bands
themselves carry provenance since 2026-08-22 (`REAL_MARKETS_PROVENANCE`):
the previous set was inherited, and a verdict against an unprovenanced
band is the same defect as an unreproducible figure, one level up.

## The headline

**At 252 days the default preset holds all fourteen statistics in band, and
at 504 days, against bands re-derived at that window, all fourteen again.**
The committed record `python/tradefloor/presets/pt-v16.json` is what says so
and `tests/test_preset_records.py` holds `envelope.CERTIFIED` and
`MEASURED_504` to it. This headline read "thirteen at 504, the one that
misses is `volume_change_acf1`" until 2026-09-05: that described pt-v10 and
pt-v11, whose miss `pt-v12`'s `volume_move_cap` closed on 2026-08-26, and it
survived two era boundaries because nothing tested a sentence.

**And fourteen of fourteen answers ONE question: could a real YEAR read
these numbers.** It does not answer whether the mechanisms the rows are named
for are present, and on five of the fourteen it cannot: `BAND_RULE` builds a
prediction interval for one real year and the panel grades a thirty-seed
median about five times more precisely than that, so the band contains the
mechanism-absent reading on `abs_return_acf20`, `leverage_effect`,
`corr_asymmetry`, `corr_asymmetry_lagged` and `corr_persistence_acf1`. A
mechanism-absent null model passes three of those bands with probability
1.000. That is what `NULLS`, `mechanism_verdict` and `envelope.certify` are
for, and why a certificate here is THREE counts and not one.

Two eras of caveats attach. First, the 2026-08 model changes (the GJR
asymmetry term, conditional volatility on the shared market factor, that
volatility's VIX coupling) were CALIBRATED against the statistics this
module reports -- see "Which statistics were targets" below. Second, the
BANDS themselves were re-derived on 2026-08-22: the previous bands were
inherited without provenance, and re-deriving them from real data at this
module's own method moved several verdicts in BOTH directions --
clustering at lag one left its band, the leverage effect entered its
band -- see "Where the bands come from" below. A verdict is a comparison
of a measurement against a band, and both halves now carry provenance.

Fourteen is the SHAPE count and not the graded count. A fifteenth row,
`index_drift_pct`, is the panel's only first moment, and it is graded
against a band measured from real series on 2026-09-03: the long-run
price return of the cap-weighted index over 75 years plus the
equal-weight premium read off two equal-weight series against it, a
centre of 7.37 with a standard error of 2.25, so a band of 2.9 to 11.9
(see `REAL_MARKETS_PROVENANCE`). It exists because the fourteen shape
statistics could all read in band on a market losing a fifth of its
value in a year -- and did. The certified set is therefore split: `SHAPE`
rows are graded as a median over the certification seeds, `LEVEL` rows
as a thirty-seed mean because their seed noise is a large fraction of
their band, and `CRISIS` rows are reserved for the fear gauge. The level
row reports the daily-rebalanced equal-weight portfolio, which is the
convention a real index band is stated in; every decomposition in this
engine is additive in log returns and keeps that convention instead, and
the two differ by half the cross-sectional variance. See
`_index_drift_pct`.

Every figure below: `Universe.random(40, seed=111)` (fingerprint
5d8de78b55aad752), 252 days, `measure()` per sim seed, median over seeds 1
to 6 -- re-measured at known-answer v8 (era digest 1ee64998...), where the
superseded figures beside them are marked with the era they belonged to.

**That roster no longer exists.** The universe generator was reconciled so
that a drawn roster opens at its own fair value rather than above it, which
changed every generated name's earnings and book value, and
`Universe.random(40, seed=111)` now fingerprints 9be68b9bc37e7978. Every
figure below, `SEED_SD_504` and the `envelope` module's measured tables were
taken on the roster the OLD generator produced and have not been
re-measured. They are stale, and the stale figures are kept rather than
quietly swapped, because a figure carrying a fingerprint it was not measured
under is the defect this module corrected once already.

`SEED_SD` is the exception and has been re-measured on the current roster,
which `SEED_SD_PROVENANCE` records. It reads that roster from a committed
fixture rather than from the generator, so it no longer moves when the
generator does, and the fourteen scales shift by -6.02 to +5.22 per cent
against their superseded values.

How stale, measured rather than guessed: over seeds 101 to 110 at 252 days
on pt-v16, the fourteen graded medians move by at most 0.26 of their own
`SEED_SD` (the largest is `volume_change_acf1`), and all fourteen sit in
band before and after. So the verdicts hold and the digits do not. The row
that moves is the ungraded one: `index_drift_pct` improves by 3.83
percentage points a year on every one of thirty seeds, measured in the log
convention that row carried at the time.

## What lands

**Stocks move together, and stop being diversifiable in a crisis.** Mean
pairwise correlation of daily returns is **+0.257** (seed range +0.205 to
+0.456), inside the re-derived +0.08 to +0.56 -- a real decade's own
calm-market spread, which the superseded +0.25/+0.35 band was narrower
than -- and measured at +0.024 one era ago, the largest gap this module
has ever carried. The mechanism that closed it: the shared market factor
now carries its own conditional-variance process at a baseline sigma of
0.016 against the reference's 0.003, funded by scaling per-name
idiosyncratic noise by 0.84 rather than added on top. The crisis half is
the VIX coupling: pinned VIX 45 takes the same correlation to +0.68 (see
`tradefloor.scenario`); the real crisis reading is +0.63.

**Fat tails survive the correlation -- at the thin end of a wide band.**
Excess kurtosis is **+3.1** (+2.4 to +5.7 across seeds), inside the
re-derived +1.6 to +41. The width is honest: a fourth moment on 252 days
is noise-dominated, and one genuine single-name event (NVDA's +26%
earnings day in 2016) put a real window at 36.7. Two readings follow.
The old +3/+10 band claimed a precision the estimator does not have --
its top sat below the real windows' MEDIAN of 11. And the model sits
below every one of ten real windows (minimum 5.6): its pooled tails are
thin for a real cross-section, just not provably outside a band this
noisy a statistic can honestly carry.

**Clustering's memory profile at lag twenty.** |return| autocorrelation
at lag twenty is **-0.006** (-0.044 to +0.009), inside the re-derived
-0.04 to +0.08. Read the band before the verdict: real WITHIN-YEAR
lag-20 readings are themselves near zero (-0.015 to +0.059 across
windows), so this row says the model matches real markets at the
horizon the panel measures -- not that its volatility memory matches
the long-sample fact that real clustering persists for months. It does
not; a 252-day window simply cannot see that fact, in the model or in
real data.

**Volume arrives with volatility.** Volume against absolute return is
**+0.585** (+0.541 to +0.655), inside the re-derived +0.46 to +0.66 --
the tightest band on the panel, because every real window of a decade
reads 0.50 to 0.64. It read +0.105 before the era boundary: the
`avg_volume` feedback compounded the level a percent-plus a day, and
that trend swamped the covariation. The level is held now, and the
per-tick channel -- volume scales with the size of the day's move by
construction -- shows through.

**The leverage effect exists, and at the honest band it is in band.**
Today's signed return against tomorrow's absolute return is **-0.085**,
negative on six seeds of six (range -0.181 to -0.031), inside the
re-derived -0.16 to 0.00. The superseded -0.30/-0.10 band demanded
index-strength leverage from a per-name estimator: Bouchaud, Matacz and
Potters measured the single-stock effect an order of magnitude weaker
than the index effect, and real per-name windows read -0.11 to +0.01
(median -0.04). The GJR term that made the sign stable remains real
work -- a symmetric variance process produces no asymmetry at any
coefficients -- but the "too weak" verdict this row carried was a
verdict against the wrong band. Mind the sign when reading this row: a
value ABOVE a band whose top is zero is an effect too WEAK, not too
strong, so `_verdict` and `band_distance` below carry their own sign
handling.

## What still fails, and what it costs you

**Returns are positively autocorrelated, and real ones are not.** Measured
at **+0.249** at lag one (+0.237 to +0.443), in six seeds of six, against
a real band around zero. The AR(2) mispricing process showing through --
untouched by the era boundary, and none of the era's sweeps targeted it.
It has a consequence you must carry into any conclusion drawn here:

> **Momentum is mechanically profitable in this market in a way it is not
> in real markets.** An agent that trades serial correlation has an edge
> here that is an artefact of the process, not a skill that transfers.

This is the specific mechanism behind the general warning that this harness
ranks agents against each other rather than certifying real-world skill. If
two agents differ mainly in how much serial correlation they exploit, their
ranking here says very little about which is better anywhere else.

**Volatility clustering is too strong at short lags for the horizon it
is measured on.** |return| autocorrelation at lag one is **+0.242**
(+0.189 to +0.454) against a re-derived within-year band of +0.02 to
+0.22, and at lag five **+0.090** against +0.02 to +0.09 -- outside by
two parts in ten thousand, on the boundary at any noise scale, but
strictly outside and reported as such. The lag-one verdict is a band
correction, not a model change: the superseded +0.15/+0.35 band was the
LONG-SAMPLE textbook value (S&P daily over 66 years reads ~0.3 at lag
one), and a 252-day window measures a genuinely smaller quantity,
because a year sits mostly inside one volatility regime. Real windows
read 0.04 to 0.18. The era's calibration raised clustering toward the
long-sample number at a within-year method, and against the honest band
it overshot. Lag five was banded (2026-08-22) for a sharper reason:
phase 2's instrument found a parameter corner with lag-one clustering
comfortably in band and lag-five at -0.001 -- the lag-one statistic
satisfied while the memory behind it is zero -- so every measured lag
is now banded and the corner is priced.

**Volatility is high.** About **41.5% annualised** (39% to 50% across
seeds) against a re-derived band of 15 to 36 -- the calm-decade spread
of a real 40-name large-cap cross-section, its ceiling extended to
CLMX's since-1997 typical-stock 36%. Down from 53% pre-era, because the
factor's variance was funded rather than added, and still above the
band for a reason about how a universe is generated rather than about
the price process: a generated roster is deliberately dispersed and
skews small, which the mega-cap reference biases against, and the real
crisis year read 45. Prefer ratios -- capture against the oracle,
shortfall in basis points -- over raw percentages.

**Volume shocks do not persist, by construction.** Volume CHANGES
autocorrelate at **-0.446** (-0.454 to -0.425) against a re-derived
band of -0.32 to -0.20. The superseded band (-0.05 to +0.15) said real
markets sit near zero here, and at this estimator that was simply wrong:
every real window of a decade reads -0.22 to -0.30, because
real daily volume is a persistent level plus large day-to-day noise,
and differencing such a series is negatively autocorrelated. The band
was relocated, not widened -- and the model is still outside it,
because its volume noise is PURELY independent day to day and
differencing that sits near -0.5 as arithmetic. The gap to real
markets shrank from a mislocated 0.40 to a real 0.13, and it still
needs volume dynamics the engine does not model. Execution work is
where it bites: VWAP and POV live or die on forecasting the day's
volume, and the hard part in a real market is a volume surprise that
keeps going and arrives with a volatility surprise. The
arriving-together half is now present; the keeps-going half is absent,
so a forecast here is never wrong twice running.

## Which statistics were targets

The dependence rows stopped being pure measurements at the era boundary:
the sweeps that chose the era's constants (`tools/calibration/`) scored
candidates on the panel statistics, at this exact method -- this
universe, these seeds, this horizon. Correlation, kurtosis, clustering
at lag one and the leverage effect are calibrated quantities; return
autocorrelation, the volatility level and the volume-change
autocorrelation were not targeted. A statistic a model was tuned to hit
is evidence about the tuning, not the model -- and the band
re-derivation sharpened that reading in both directions. Clustering at
lag one, a calibrated statistic, is now OUT of band: the calibration
drove it toward the long-sample textbook value at a within-year method,
the shape tuning toward an unprovenanced target takes when seen from the
other side. And the held-out fragility this section used to report --
correlation slipping under the floor on fresh seeds, leverage halving
on fresh universes -- largely dissolves under honest bands: on thirty
fresh seeds (101-130) every one of the ten verdicts is the SAME as on
the published seeds, because verdicts that flipped on a re-measurement
were a symptom of bands narrower than the statistic's own seed noise.
A conclusion that needs a dependence statistic deep inside its band
should still re-measure on its own universe and seeds rather than
inherit these figures.

## Where the bands come from

The bands were re-derived on 2026-08-22, because the previous set was
inherited without provenance -- nobody could say which market, period,
frequency or estimator "+0.25 to +0.35" described. Each band now
carries its provenance as data in `REAL_MARKETS_PROVENANCE`: the
empirical claim, the reference-panel windows (ten 252-day windows of 40
US large caps, 2015-2025, measured with THIS module's estimators at
THIS panel's method), the retrieved sources with what each actually
measured, and any named judgement call. The derivation rule was fixed
before any verdict was looked at; the two inward clamps it needed (the
clustering floor, the leverage top) are named on their rows and decide
no current verdict. One band is marked INDICATIVE (volume-change
autocorrelation: own measurement only, no published figure for the
estimator was recoverable). The full derivation record, window tables
and verdict moves are in tradefloor-design/REALISM-BANDS.md.

## Why there are fourteen statistics and not four

The first four this module reported were chosen before anyone looked at
dependence, and all four come from one instrument's price series taken on its
own. Nothing looked across instruments and nothing looked between price and
volume. Every realism gap later found in this project sat in that blind spot:
the cross-sectional correlation, the volume behaviour and the missing leverage
effect were all invisible to the report while it kept passing. A report that
never leaves a single series will keep passing while the joint behaviour is
wrong.

The four dependence statistics cost one function and no modelling decision, and
they are the ones that say where a conclusion drawn here stops transferring.
The last two, clustering at lags five and twenty, were promoted from
measured-but-unbanded to banded when phase 2's instrument demonstrated the
general lesson: any statistic that is measured but not banded is a hole an
optimiser can walk through, and it found the lag-five hole unprompted.

## What the era closed, and what remains

An earlier version of this section asked "can the mismatches be fixed?"
and answered that each was a decision about diverging from the reference
implementation. The 2026-08 era took those decisions -- argued, gated
divergence, each with its sweep committed under `tools/calibration/` --
and the record of which gap needed which KIND of change is worth keeping:

- **Cross-sectional correlation** was proven unreachable by the factor's
  constant sigma (the band arrived only where kurtosis had collapsed) and
  was closed by a model change: conditional volatility on the factor,
  funded from the idiosyncratic side.
- **Clustering** resisted every calibration lever -- persistence already
  at the reference's 0.99, and raising the variance ceiling bought +0.016
  of clustering for twenty points of volatility -- and was closed by the
  same factor process, the one market-wide clustering needed.
- **Volume against volatility** was closed by removing the average-volume
  feedback that compounded the level and buried the covariation.
- **The leverage effect** was absent at any coefficients of a symmetric
  GARCH and was closed structurally, by the GJR term.

What remains, and what each would take:

- **Return autocorrelation is one constant away, and the constant stays
  unpulled.** `MOMENTUM_THETA` from 0.25 to 0.05 measured +0.034 --
  inside the band -- with volatility and kurtosis essentially unchanged.
  That counterfactual was measured on the PRE-era model and has not been
  re-run; the mechanism it names is untouched. It remains a decision
  about the mispricing process itself (the herding term is load-bearing
  for the model's identity), not a calibration detail -- though
  `ModelParams.from_preset("pt-v1", momentum_theta=...)` now lets anyone
  measure the counterfactual without a rebuild, honestly fingerprinted.
- **The volatility level** is a property of the universe generator, not
  the price process, and would be recalibrated there.
- **Volume dynamics** need a model -- persistent volume shocks -- not a
  constant. Until then -0.45 is structural; the honest gap to real
  markets is 0.13, not the 0.40 the mislocated band used to charge.
- **Short-lag clustering strength** is the era's own overshoot: the
  calibration pushed lag-one clustering toward a long-sample value at a
  within-year method. Unwinding it is a re-run of the same sweeps
  against the re-derived band, not a model change.

## What a measurement is for, when it disagrees with you

Worth recording because it was tested: the GARCH process was being fed the
day's TOTAL RETURN rather than its noise component -- the documented fallback,
taken by accident on every close. Fixing that was expected to strengthen
clustering, since it is the correction that makes the variance process see the
shock the model says it should. It did the opposite: clustering fell from +0.12
to +0.10.

The fix stayed anyway. It is what the model specifies, and the alternative is
keeping a bug because it happened to score better on a statistic. That is how
a model gets tuned toward its own report card instead of toward being right.
When this was recorded these numbers were measurements rather than targets;
four of them have since BECOME targets -- the calibration the era boundary
performed, disclosed above. The held-out checks exist for that: they are
where the report card stops being the thing that was tuned.

## Re-measure after any change

`model_preset()` is versioned, but a change to a coefficient, a different
universe generator, or an unusual scenario can move these. `measure()` takes
the same arguments the rest of the library does, so a claim about realism can
be re-checked rather than inherited.
"""

from __future__ import annotations

import functools
import math
import random
import statistics
import textwrap
from typing import Any, Iterable, Mapping, Sequence

from ._core import Engine, Instrument, Macro, ModelParams, ValidationError
from .universe_util import fingerprint_of

#: What the same statistics look like for real daily equity returns, at THIS
#: module's own measurement method. Ranges rather than points, because they
#: vary by market, period and universe -- and a single number would imply a
#: precision nobody has. Every band was re-derived on 2026-08-22 from a
#: reference panel of real markets measured with this module's own estimator
#: functions, reconciled against the retrieved stylised-facts literature;
#: `REAL_MARKETS_PROVENANCE` below carries, per band, what the empirical
#: claim is and where each edge comes from. The bands these replaced were
#: inherited without provenance, and re-derivation moved most of them --
#: including relocating one (volume-change autocorrelation) whose old range
#: did not contain ANY observed real-market reading at this estimator.
REAL_MARKETS = {
    "annualised_vol_pct": (15.0, 36.0),
    "excess_kurtosis": (1.6, 41.0),
    "return_acf1": (-0.08, 0.06),
    "abs_return_acf1": (0.02, 0.22),
    "abs_return_acf5": (0.01, 0.12),
    "abs_return_acf20": (-0.04, 0.08),
    "cross_sectional_corr": (0.08, 0.56),
    "volume_abs_return_corr": (0.46, 0.66),
    "leverage_effect": (-0.16, 0.00),
    "volume_change_acf1": (-0.32, -0.20),
    # Conditional correlation, added 2026-08-25 (tradefloor-design/REALISM-BANDS.md,
    # "Conditional correlation"). The unconditional mean over all pairs cannot
    # see sign, sector or time, and a search cannot preserve what it cannot see.
    "corr_asymmetry": (-0.25, 0.45),
    "corr_asymmetry_lagged": (-0.20, 0.55),
    "sector_excess_corr": (0.11, 0.23),
    # Correlation persistence: acf1 of mean pairwise correlation over
    # non-overlapping 21-day sub-windows. ELEVEN sub-windows at 252 model
    # days (`sub_window_count`; this comment read twelve, which is the BAR
    # count), and the real windows themselves scatter from -0.05 to +0.40,
    # so this band admits everything and says so. What certifies the row at
    # 252 days is the mechanism gate against the estimator's own null of
    # -0.09 (`NULLS`), not a longer window: both presets read 22 to 24 of
    # thirty seeds on the real side there.
    "corr_persistence_acf1": (-0.19, 0.54),
    # The LEVEL row, graded from 2026-09-03. Annualised return of the
    # daily-rebalanced equal-weight portfolio, percent a year, a price
    # return. The centre is the cap-weighted S&P 500 price return over 75
    # calendar years plus the equal-weight premium in price terms, and the
    # width is two standard errors of that centre, which exceed the model's
    # own resolution at thirty seeds; the derivation and its three URLs are
    # in `REAL_MARKETS_PROVENANCE` and reproducible with
    # tools/calibration/index_band.py. A band chosen so the current model
    # passes was refused: the default preset reads far below the floor and
    # the row is held red until the level is right.
    "index_drift_pct": (2.9, 11.9),
    # The CRISIS rows, graded from 2026-09-03: the median change in the
    # volatility index on a session whose cap-weighted index return is at or
    # below -1 percent, and the same at or below -3 percent. Two rows and not
    # one, because the defect they exist to catch is a channel that
    # saturates on the down side: against a real index the model's response
    # ratio falls from 0.60 at -1 to -3 percent to 0.32 at -3 to -5 and 0.18
    # below -5, while the up side stays flat. A graded row on the -3 percent
    # bucket alone would have scored this as mildly out of band for three
    # eras, and mildly out of band is the verdict that gets tuned at rather
    # than fixed; the -1 percent row reads inside its band on the same model.
    # Bands from ^VIX against ^GSPC, in `REAL_MARKETS_PROVENANCE`, and THE TWO
    # ROWS ARE ON DIFFERENT SPANS AND DIFFERENT FORMS. The -1 percent row was
    # a 2015-2025 decade band until 2026-09-15 and is now the whole tape its
    # data supports, 1990-01-03 to 2025-07-31, under
    # `ruling-longest-tape-per-row`; the -3 percent row was never a decade
    # object and keeps the spread rule it was built with. `FEAR_DN1_WINDOWS`
    # below carries the -1 percent row's window readings so its band derives
    # here rather than being typed in; the -3 percent row's do not exist in
    # this package and its provenance says so.
    "fear_gauge_dn1": (0.39, 3.03),
    "fear_gauge_dn3": (2.60, 9.58),
    # The index TAIL row, graded from 2026-09-06: the share of sessions, in
    # percent, whose cap-weighted index return is at or below -3 percent --
    # the count of the sessions the two rows above condition on. Until this
    # row existed, everything the project knew about the index tail was
    # ungraded, and a model could reach the real count with a third more
    # volatility and a nearly Gaussian tail without any row saying so.
    #
    # The band is the LEVEL row's form and not the shape rows'. This row
    # grades a mean over the certification seeds, so the question is
    # "is the model's ensemble rate consistent with the real long-run rate,
    # given how well the tape knows it" -- centre plus or minus the
    # multiplier times the centre's own standard error -- not "could one
    # real year read this", which is what `BAND_RULE` answers and which on
    # these windows gives [-1.18, 14.27]: a floor below zero and a ceiling
    # that admits 2008 every year.
    #
    #   centre 1.2132 percent, 107 hits in 8,820 sessions over 35
    #   non-overlapping 252-return windows of ^GSPC, the sessions
    #   1990-07-24 to 2025-07-31 under the anchor rule INDEX_TAIL_WINDOWS
    #   states; across-window sd 2.3613, so the standard error of the mean
    #   is 0.3991 (moving-block bootstrap 0.393 at block length one, 0.410
    #   at two, 0.412 at three; lag-1 autocorrelation of the counts +0.06,
    #   so adjacent years do not share their crashes at this window length);
    #   multiplier 1.846 = centre_multiplier(band_rule_tolerance(9)), the
    #   panel's own measured tolerance rather than a convention; raw band
    #   [0.4763, 1.9500], rounded outward to the shipped edges. In the
    #   centre's own units that is 0.39x to 1.62x.
    #
    # The ceiling is the one edge where the rounding RULE decides the
    # printed number rather than the third decimal: the raw high is
    # 1.950042, four parts in a hundred thousand above 1.95, so
    # `round_outward` gives 1.96 where rounding to nearest would give 1.95.
    # Outward is the rule this panel applies to every band and the one the
    # row is derived under, because rounding a band edge inward makes the
    # band stricter than the tolerance it claims.
    #
    # RESIDUAL: one window, 2008-06..2009-06, carries 33 of the 107 hits.
    # Without it the centre falls to 0.864 and the standard error to 0.199,
    # which is a different quantity -- the non-2008 crash rate -- and a
    # model that never produces a 2008 would pass it. The band says
    # "the unconditional crash rate, 2008 included, known to about a third
    # of itself at one standard error", and that is the true state of
    # thirty-five years of evidence rather than a tighter number the tape
    # does not have. It is why the row cannot fail a thin tail: 0.688
    # percent is 1.3 tape standard errors below the centre, and no seed
    # count shrinks the tape's error. `index_excess_kurtosis` reads the
    # thin side and is REPORTING_ONLY.
    #
    # Derivable with tools/calibration/tail_band.py; the window table is
    # INDEX_TAIL_WINDOWS below and tests/test_reference_windows.py
    # re-derives these two edges from it.
    "index_tail_dn3_pct": (0.47, 1.96),
}

#: Where each band comes from, carried as data so a reader can ask the
#: library rather than trust a docstring. The full derivation -- the window
#: table, the retrieved sources with what each actually measured, and the
#: verdict moves -- is recorded in tradefloor-design/REALISM-BANDS.md.
#:
#: The shared derivation, applied blind to every statistic before any
#: verdict was looked at: the reference panel is 40 US large-cap stocks
#: (Yahoo Finance daily bars, adjusted close for returns, reported volume),
#: measured over ten consecutive 252-trading-day windows covering 2015-07
#: to 2025-07 with THIS module's estimator functions at THIS panel's method
#: (per-instrument medians, pooled marginals, mean pairwise correlation).
#: The window straddling the COVID crash is excluded and reported beside
#: each band as the crisis reading -- the panel measures a typical year,
#: and this library measures crisis behaviour under pinned scenarios
#: instead. Band = [min - s, max + s] over the nine remaining windows,
#: where s is the across-window sd with the single most extreme window
#: dropped (so one draw cannot inflate the noise scale it is priced in),
#: edges rounded outward: two decimals for correlations, two significant
#: figures otherwise. Literature reconciliation may move an edge OUTWARD
#: to a retrieved, horizon-compatible value; the two INWARD clamps are
#: named on their rows. "windows" is (min, median, max) over the nine.
#: The sentence every row whose band cannot fail a mechanism-absent model
#: carries, under `admits_the_null`. One fact about the band RULE rather
#: than five observations about five rows, so it is written once.
_BAND_ADMITS_NULL = (
    "the band admits its own null: it contains the reading of a model "
    "WITHOUT this mechanism, so no band verdict on this row can fail one. "
    "What certifies the mechanism is the sign test in `NULLS` and "
    "`mechanism_verdict`; the band answers fidelity alone. "
)

REAL_MARKETS_PROVENANCE = {
    "annualised_vol_pct": {
        "claim": "pooled across-name annualised daily vol of a 40-stock "
                 "US large-cap cross-section over one year, 2015-2025",
        "windows": (18.3, 25.9, 30.7),
        "crisis_window": 45.3,
        "sources": (
            "Campbell, Lettau, Malkiel & Xu, NBER w29916 (2022): "
            "value-weighted market/industry/idiosyncratic vol averaged "
            "18%/14%/28% since 1997 (12%/9%/26% over 1962-1997), so a "
            "typical stock's total annualised vol is ~36% since 1997, "
            "~30% over 1962-1997, higher equal-weighted",
        ),
        "derivation": "floor from the windows; ceiling extended outward "
                      "from the windows' 34 to CLMX's since-1997 "
                      "typical-stock 36 (a crisis-inclusive average)",
        "comparability": "argued: the reference roster is mega-cap while "
                         "a generated roster is dispersed and skews small, "
                         "which biases this band's ceiling LOW for the "
                         "simulator's universe; CLMX equal-weighted runs "
                         "higher but ships no comparable single figure",
        "supersedes": (15.0, 35.0),
    },
    "excess_kurtosis": {
        "claim": "excess kurtosis of the pooled standardised daily returns "
                 "of a 40-stock US cross-section over one year",
        "windows": (5.6, 11.1, 36.7),
        "crisis_window": 11.4,
        "sources": (
            "Cont, Quantitative Finance 1 (2001), facts 2 and 4: heavy "
            "tails with daily tail index 2-5, kurtosis decreasing with "
            "aggregation; his Table 1 kurtosis figures (S&P futures ~16) "
            "are 5-MINUTE increments and are NOT this band",
            "own reference panel: the 36.7 window is a genuine single-name "
            "event (NVDA +26% on 2016-11-11 earnings), not a data error",
        ),
        "derivation": "mechanical from the windows; wide because a "
                      "fourth moment on 252 days is noise-dominated "
                      "(Cont section 4.1 makes exactly this point)",
        "comparability": "argued: pooling 40 names of unequal vol adds "
                         "cross-name variance mixing to each name's own "
                         "kurtosis, in the simulator and the reference "
                         "panel alike; the old (3, 10) band's top sat "
                         "BELOW the real windows' median of 11.1",
        "supersedes": (3.0, 10.0),
    },
    "return_acf1": {
        "claim": "median across names of the lag-1 autocorrelation of "
                 "daily log returns over one year",
        "windows": (-0.046, -0.006, 0.030),
        "crisis_window": -0.244,
        "sources": (
            "Cont (2001), fact 1: linear autocorrelations insignificant "
            "beyond ~20 minutes",
            "Granger & Ding, J. Econometrics 73 (1996): S&P 500 daily "
            "1928-1991, return acf small beyond the first two lags",
            "CLMX w29916 (2022): firm-level daily autocorrelations near "
            "zero in recent decades",
        ),
        "derivation": "mechanical from the windows",
        "comparability": "direct",
        "supersedes": (-0.05, 0.05),
    },
    "abs_return_acf1": {
        "claim": "median across names of the lag-1 autocorrelation of "
                 "daily |log return| WITHIN one 252-day window",
        "windows": (0.039, 0.083, 0.176),
        "crisis_window": 0.430,
        "sources": (
            "Granger & Ding (1996): S&P 500 daily 1928-1991 |r| acf "
            "~0.3 at lag 1 -- a 17,054-day estimate of a long-memory "
            "process, NOT a within-year value, and the reason the old "
            "0.15-0.35 band does not describe this measurement",
            "Cont (2001), facts 6 and 8: positive, decaying as a power "
            "law with exponent 0.2-0.4",
        ),
        "derivation": "top mechanical from the windows; the mechanical "
                      "floor (-0.003) is clamped INWARD to +0.02 because "
                      "zero or negative clustering appears in no retrieved "
                      "source and no observed window -- the clamp closes "
                      "the zero-memory hole and decides no current verdict",
        "comparability": "argued: within-window clustering is genuinely "
                         "smaller than the long-sample textbook value, "
                         "because a year sits mostly inside one volatility "
                         "regime; the band is for THIS horizon",
        "supersedes": (0.15, 0.35),
    },
    "abs_return_acf5": {
        "claim": "median across names of the lag-5 autocorrelation of "
                 "daily |log return| within one 252-day window",
        "windows": (0.011, 0.015, 0.016, 0.018, 0.021, 0.040, 0.047, 0.099),
        "crisis_window": 0.343,
        "sources": (
            "own reference panel (primary)",
            "Cont (2001), fact 8: power-law decay with exponent 0.2-0.4 "
            "puts lag 5 at 0.52-0.72 of lag 1, consistent with the "
            "windows' observed ratio ~0.45",
        ),
        "derivation": "mechanical from the windows, upper edge; lower edge "
                      "held above zero on purpose -- see below",
        "comparability": "argued as for lag 1; banded because phase 2's "
                         "instrument found a parameter corner with lag-1 "
                         "clustering in band and lag-5 at -0.001 -- lag-1 "
                         "strength with no memory behind it -- so an "
                         "unbanded lag 5 was a hole a search walks through",
        "supersedes": (
            "(0.02, 0.09), derived 2026-08-22 from THREE non-crisis windows "
            "reading 0.034, 0.046, 0.073. Re-derived 2026-08-28 from EIGHT "
            "non-crisis windows of the same forty-name panel, which span "
            "0.011 to 0.099 -- a wider spread than three windows showed, and "
            "the old band excluded five of the eight. Scoring real markets "
            "against this envelope for the first time, abs_return_acf5 was "
            "the ONLY statistic of thirteen that real non-crisis windows "
            "failed, and it failed on five of eight. A band real markets "
            "step outside of five times in eight is not measuring realism. "
            "The upper edge 0.12 is the rule applied to the larger evidence "
            "base. The lower edge is NOT: the rule gives -0.01, and a "
            "negative floor would re-open the exact hole this band exists to "
            "close, admitting lag-1 clustering with no lag-5 memory behind "
            "it. 0.01 sits below every observed window and above zero, which "
            "keeps both jobs. Correcting this band changes ZERO blocks for "
            "pt-v12 or pt-v14: every block that missed it also missed "
            "something else."
        ),
    },
    "abs_return_acf20": {
        "claim": "median across names of the lag-20 autocorrelation of "
                 "daily |log return| within one 252-day window",
        "windows": (-0.015, 0.020, 0.059),
        "crisis_window": 0.141,
        "sources": (
            "own reference panel (primary); real within-year lag-20 "
            "readings are small and sometimes negative, so the "
            "long-sample 'clustering persists for months' fact does not "
            "band this horizon",
        ),
        "derivation": "mechanical from the windows",
        "comparability": "argued as for lag 1",
        "supersedes": None,
        "admits_the_null": _BAND_ADMITS_NULL + (
            "Measured: a null model's thirty-seed median passes this band "
            "with probability 1.000, and the band's null-side edge sits 4.2 "
            "model standard errors on the far side of zero. This row is also "
            "the one the gate does not COUNT at 252 days "
            "(MECHANISM_DIAGNOSTIC): the real within-year effect is inside "
            "real year-to-year noise on the longer record."),
    },
    "cross_sectional_corr": {
        "claim": "mean pairwise correlation of daily returns across a "
                 "40-stock US large-cap roster over one year",
        "windows": (0.169, 0.346, 0.477),
        "crisis_window": 0.633,
        "sources": (
            "Preis, Kenett, Stanley, Helbing & Ben-Jacob, Sci. Rep. 2 "
            "(2012): DJIA pairs, daily, 1939-2010 -- mean correlation "
            "~0.19-0.27 in the calm regime (their regression intercepts), "
            "rising sharply with market stress",
            "CLMX w29916 (2022): average pairwise correlation higher "
            "since the late 1990s, spiking in the GFC and COVID",
        ),
        "derivation": "mechanical from the windows",
        "comparability": "direct; the old 0.25-0.35 band was narrower "
                         "than one real decade's own spread -- real "
                         "windows sat outside it on BOTH sides",
        "supersedes": (0.25, 0.35),
    },
    "volume_abs_return_corr": {
        "claim": "median across names of the Pearson correlation between "
                 "daily share volume and same-day |log return| over one "
                 "year, 2015-2025",
        "windows": (0.502, 0.536, 0.617),
        "crisis_window": 0.645,
        "sources": (
            "own reference panel (primary; remarkably tight, 0.50-0.64 "
            "in every window including the crisis one)",
            "Cont (2001), fact 10, and Podobnik, Horvatic, Petersen & "
            "Stanley, PNAS 106 (2009): the positive volume-volatility "
            "relation, qualitative",
        ),
        "derivation": "mechanical from the windows",
        "comparability": "argued: the level is modern-US-market; the old "
                         "0.30 floor may describe older markets but no "
                         "source for it was recoverable",
        "supersedes": (0.30, 0.60),
    },
    "leverage_effect": {
        "claim": "median across names of the Pearson correlation between "
                 "today's signed daily return and tomorrow's |return|, "
                 "per name, over one year",
        "windows": (-0.113, -0.042, 0.014),
        "crisis_window": -0.128,
        "sources": (
            "Bouchaud, Matacz & Potters, PRL 87 (2001): 437 US stocks "
            "daily 1990-2000; single-stock leverage amplitude A=1.9 in "
            "their normalisation against A=18 for indices -- converted "
            "to a per-name Pearson correlation this is order -0.01 to "
            "-0.05, and the old -0.30/-0.10 band demanded index-strength "
            "leverage from a single-name estimator",
            "Cont (2001), fact 9: the sign, qualitative",
        ),
        "derivation": "floor mechanical from the windows; the mechanical "
                      "top (+0.05) is clamped INWARD to 0.00 because "
                      "every retrieved source agrees the effect's sign is "
                      "negative -- a top above zero would certify a "
                      "REVERSED leverage effect as real-market behaviour. "
                      "The one positive window (+0.014) is 2020-21, the "
                      "meme-stock year, within noise of zero",
        "comparability": "argued: per-name Pearson at 252 days is a WEAK "
                         "effect in real data; index-level and "
                         "parametric-model magnitudes do not band it",
        "supersedes": (-0.30, -0.10),
        "admits_the_null": _BAND_ADMITS_NULL + (
            "Here the inward clamp puts the ceiling EXACTLY on the null, so a "
            "null model's median passes this band with probability 0.50 -- a "
            "coin flip, measured. Under the mechanism gate the clamp decides "
            "the fidelity wording and the gate does the excluding, which is "
            "what the clamp's own ruling asked of it."),
    },
    "volume_change_acf1": {
        "claim": "median across names of the lag-1 autocorrelation of "
                 "daily relative volume changes over one year",
        "windows": (-0.296, -0.255, -0.221),
        "crisis_window": -0.284,
        "sources": (
            "own reference panel ONLY -- no published figure for this "
            "estimator was recoverable, so this band is INDICATIVE",
            "Podobnik et al. (2009) analyse |volume change| and find it "
            "long-range correlated with heavy (inverse-cubic) tails, but "
            "publish no signed lag-1 autocorrelation",
        ),
        "derivation": "mechanical from the windows",
        "comparability": "argued: the old band said real markets sit "
                         "near zero here; every observed window reads "
                         "-0.22 to -0.30, so the old band did not contain "
                         "ANY real reading at this estimator. Real daily "
                         "volume is a persistent level plus large "
                         "day-to-day noise, and differencing such a "
                         "series is negatively autocorrelated -- just "
                         "not the -0.5 of PURE independent noise, "
                         "because real volume shocks partly persist",
        "supersedes": (-0.05, 0.15),
    },
    "corr_asymmetry": {
        "claim": "mean pairwise correlation on days the equal-weight market "
                 "return is below -1 sd minus the same above +1 sd, 40-name "
                 "US large-cap roster, 252-day windows 2015-2025",
        "windows": (-0.154, 0.083, 0.348),
        "crisis_window": 0.167,
        "sources": (
            "tradefloor-design/realism_bands_reference_panel.py, run 2026-08-25; "
            "no literature reconciliation applied, the record carries no "
            "verified exceedance-correlation number for single stocks",
        ),
        "admits_the_null": _BAND_ADMITS_NULL + (
            "Measured: a null model passes this band with probability 1.000 "
            "and the null-side edge sits 12 model standard errors beyond "
            "zero. It is also the weakest real fact on the panel -- 2.1 "
            "across-window standard errors over nine windows, with four of "
            "the nine negative -- so a verdict here is worth less than the "
            "same verdict elsewhere."),
    },
    "corr_asymmetry_lagged": {
        "claim": "as corr_asymmetry, conditioned on the previous day's "
                 "equal-weight market return",
        "windows": (-0.092, 0.111, 0.438),
        "crisis_window": 0.074,
        "sources": (
            "tradefloor-design/realism_bands_reference_panel.py, run 2026-08-25",
        ),
        "admits_the_null": _BAND_ADMITS_NULL + (
            "Measured: a null model passes this band with probability 1.000 "
            "and the null-side edge sits 10 model standard errors beyond "
            "zero. The band admits more than the null on this row: the "
            "shipped default reads the effect BACKWARDS -- real names co-move "
            "MORE the day after a market fall, +0.111 in eight of nine "
            "windows, and the model reads -0.064 with 25 of 30 seeds on the "
            "wrong side -- and (-0.20, 0.55) admits that with room. The "
            "mechanism gate reads it REVERSED."),
    },
    "sector_excess_corr": {
        "claim": "mean same-sector pairwise correlation minus mean cross-sector, "
                 "GICS labels for the same 40 names, 252-day windows 2015-2025",
        # The maximum read 0.200 until 2026-09-05, when the per-window
        # readings joined REAL_MARKETS_WINDOWS and the nine non-crisis
        # windows put it at 0.199462. The 0.200 was the prose's "sits
        # between +0.10 and +0.20" carried into the triple; the band does
        # not move, because 0.1995 + s and 0.200 + s both round up to 0.23.
        "windows": (0.133, 0.164, 0.199),
        "crisis_window": 0.103,
        "sources": (
            "tradefloor-design/realism_bands_reference_panel.py, run 2026-08-25. "
            "Every one of ten windows including the 2020 crisis sits between "
            "+0.10 and +0.20; trimmed noise scale 0.021",
        ),
    },
    "corr_persistence_acf1": {
        "claim": "acf1 of mean pairwise correlation on non-overlapping 21-day "
                 "sub-windows; nine non-crisis 252-day windows -0.05 to +0.40, "
                 "four non-crisis 504-day windows +0.25 to +0.43",
        "windows": (-0.050, 0.229, 0.402),
        "crisis_window": 0.374,
        "sources": (
            "tradefloor-design/real_corr_persistence_bands.py, run 2026-08-25, "
            "same roster and estimator as facts.measure; the 252-day band is "
            "wide enough to admit every preset and is recorded as such.",
        ),
        "admits_the_null": _BAND_ADMITS_NULL + (
            "Measured: a null model passes this band with probability 0.92. "
            "The null here is not zero but the ESTIMATOR's own small-sample "
            "median, -0.09 at eleven sub-windows, and against that null both "
            "presets are shown at 252 days on 22 to 24 of thirty seeds -- so "
            "the row does not need a 504-day window, and the earlier "
            "recommendation to move it there was a repair of the band's "
            "form."),
    },
    # The level row's band is a LONG-RUN MEAN and not a window range, so
    # `windows` carries the three inputs to the centre instead of a window
    # spread: the cap-weighted long-run return, the RSP premium the centre
    # uses, and the ^SPXEW premium that cross-checks it. Its standard error
    # is the width, because a mean is the hardest statistic on this panel to
    # know: an annual return has a standard deviation near 16 points, so
    # even 75 years put the centre inside about two.
    "index_drift_pct": {
        "claim": "long-run price return of an equal-weight US large-cap index, "
                 "percent a year, daily-rebalanced portfolio convention: the "
                 "cap-weighted S&P 500 price return, +7.75 as the mean of 75 "
                 "calendar-year log returns 1951-2025 (sd 16.23, se 1.87), plus "
                 "the equal-weight premium in price terms, -0.38 as the mean of "
                 "22 calendar-year differences of RSP against ^GSPC on unadjusted "
                 "closes 2004-2025 (se 1.24); centre 7.37, se 2.25; band = centre "
                 "+/- max(2 se, the model's resolution at thirty seeds, 2.37)",
        "windows": (7.75, -0.38, -0.85),
        "crisis_window": None,
        # The tape side of the scoring rule for this row, which has no
        # per-window table because its quantity is a long-run MEAN and not a
        # year's reading. `centre` and `centre_se` are the claim's own 7.37
        # and 2.25; `centre_components` is what they were combined from, so
        # both re-derive here rather than standing as two typed numbers, and
        # `centre_df` is the Welch-Satterthwaite combination of those
        # components: se^4 / (1.87^4 / 74 + 1.24^4 / 21) = 91. The same
        # centre and error grade a 504-day reading, for the reason
        # `envelope.BANDS_504` gives: a long-run mean's uncertainty is the
        # centre's, not the window's.
        "centre": 7.37,
        "centre_se": 2.25,
        "centre_df": 91,
        "centre_components": ((7.75, 1.87, 75), (-0.38, 1.24, 22)),
        "centre_estimator": "the mean of 75 calendar-year S&P 500 price "
                            "log returns plus the mean of 22 calendar-year "
                            "equal-weight premia, each with its own standard "
                            "error, combined in quadrature",
        "sources": (
            "tools/calibration/index_band.py, run 2026-09-03 on the cache "
            "tools/shadow/data.py writes; ^GSPC 1950-01-03 to 2026-09-02, "
            "19,289 sessions, fetched 2026-09-03T22:11:12Z from "
            "https://query1.finance.yahoo.com/v8/finance/chart/^GSPC"
            "?period1=-631152000&period2=1788393600&interval=1d&events=split",
            "RSP 2003-05-01 to 2026-09-02, 5,873 sessions, fetched "
            "2026-09-03T22:10:39Z from https://query1.finance.yahoo.com/v8/"
            "finance/chart/RSP?period1=1049155200&period2=1788393600"
            "&interval=1d&events=split; unadjusted close, so no dividend "
            "enters; the fund carries about 0.20 a year of expense and "
            "rebalances quarterly where the row rebalances daily, so the "
            "premium read off it sits a few tenths low",
            "^SPXEW 2006-12-08 to 2026-09-02, 4,949 sessions, fetched "
            "2026-09-03T22:10:40Z from https://query1.finance.yahoo.com/v8/"
            "finance/chart/^SPXEW?period1=631152000&period2=1788393600"
            "&interval=1d&events=split; the equal-weight index itself, premium "
            "-0.85 over 19 calendar years (se 1.41), the cross-check on RSP",
        ),
    },
    # The real side of both fear rows pairs the ^VIX close on session d
    # minus the close on d-1 with d's own close-to-close ^GSPC return, which
    # is the natural pairing there; the MODEL side is inverted by the
    # recording convention, see `fear_statistics`. Every free-run reading of
    # the model is held to its free-run figure and never to a solved one: a
    # shadow solver fits the draws until the closes are the tape's, so a
    # gauge read downstream of solved closes repeats the tape back.
    "fear_gauge_dn1": {
        # THIS ROW IS GRADED BY TWO WINDOW SETS AND THAT IS A KNOWN SPLIT,
        # not an oversight, so read the two halves of this entry apart.
        #
        # THE BAND is the whole tape, adopted 2026-09-15: 33 non-crisis
        # 252-session windows and 15 at 504, 1990-01-03 to 2025-07-31, under
        # `ruling-longest-tape-per-row`. Everything about it is in the `band`
        # block below and its readings are in `FEAR_DN1_WINDOWS`.
        #
        # THE CENTRE AND ITS ERROR are still the 2015-2025 decade's, 2.66 on
        # nine windows, because moving them is a change to the SCORING RULE
        # and not to a band: `loss.rule_table` reads `windows[1]` through
        # `real_centre`, so every score on the record is against 2.66 and
        # replacing it reopens all of them. That decision is decision 2 of
        # `programme/results/ruling-four-level-rows.md` and it is Simon's.
        # The size of the disagreement, recorded here rather than left to be
        # rediscovered: the same estimator over the 33 whole-tape windows
        # gives 1.71 and the pooled median over all 1,124 qualifying
        # sessions since 1990 gives +1.85, so the decade centre runs 0.95
        # above the tape. That gap is 3.55 of `centre_se` below, so a reading
        # sitting exactly at the new band's centre is scored as three and a
        # half tape errors low while being mid-band on the band's own scale.
        "claim": "median change in the volatility index on sessions at or below "
                 "-1 percent. BAND: the whole-tape rule, median +/- t(n) times "
                 "the across-window trimmed sd, over the non-crisis "
                 "252-session windows of the paired ^VIX and ^GSPC record "
                 "1990-01-03 to 2025-07-31, 33 of them at 252 and 15 at 504. "
                 "CENTRE: still the reference panel's ten windows 2015-07 to "
                 "2025-07, the COVID window excluded and reported as the "
                 "crisis reading, median of the nine non-crisis window "
                 "medians. The band superseded the decade's [min - s, max + s] "
                 "= (0.70, 4.03) on 2026-09-15; the centre did not move and "
                 "the triple below is still the decade's",
        "windows": (1.34, 2.66, 3.39),
        "crisis_window": 3.08,
        # The adopted band, every term, so the edges derive here instead of
        # being trusted. `tests/test_reference_windows.py` rebuilds both
        # from `FEAR_DN1_WINDOWS` and refuses a typed edge.
        #
        # THE MULTIPLIER IS NOT A STUDENT-T QUANTILE. It is the project's
        # own tolerance solve, 200,000 standard-normal draws at seed
        # 20260905 against the nine-window false-alarm target
        # `BAND_RULE_TOLERANCE[9]`, which is the same solve that produced
        # `certification-bands/universal.json`'s multipliers for the
        # fourteen shape rows; t(0.975) on 32 degrees of freedom is 2.0369
        # and nobody should read 2.1310 as one. `band_rule_tolerance(33)`
        # REFUSES, because the tolerance itself is measured at 4, 5, 7 and 9
        # windows only, so carrying the nine-window rate to 33 is an
        # assumption this entry inherits from the universal rule rather than
        # one it introduces.
        #
        # THE WINDOW ANCHOR IS THE FIRST PAIRED SESSION AND NOT THE LAST BAR,
        # which is where this band differs from every other whole-tape object
        # in the project and the difference is named here so it is found
        # rather than assumed. `INDEX_TAIL_WINDOWS` anchors at the tape's last
        # bar and argues for it; `whole-tape/scripts/panel32.py` walks
        # backward from the last bar too, so the universal band's windows end
        # on 2025-07-31. This row's cut runs forward from 1990-01-03 and
        # discards 140 returns at 252 (2025-01-08 to 2025-07-31) and 392 at
        # 504 (2024-01-08 to 2025-07-31). MEASURED, at the same cut and the
        # same n: the last-bar anchor gives [0.53, 2.95] at 252 and
        # [0.78, 2.78] at 504, each edge rounded OUTWARD by this row's own
        # rule. An earlier reading here put the 504 pair at [0.79, 2.77],
        # which is the same unrounded edges taken to nearest instead of
        # outward; 252 is unaffected because the two agree there. So the
        # anchor used here is the WIDER of the two at both horizons, by 8.9
        # and 7.4 per cent of the last-bar band's UNROUNDED width (2.6324
        # against 2.4168 at 252, and 2.1308 against 1.9845 at 504; the
        # percentages are on those unrounded widths and do not reproduce
        # from the rounded pairs above). The direction favours any model
        # sitting near the floor. It
        # changes NO verdict on the record: over the 31 retained rec-* arms
        # and both presets at both horizons the two anchors agree on all 66.
        "band": {
            "adopted": "2026-09-15",
            "supersedes": (0.70, 4.03),
            "rule": "median +/- t(n) * trimmed_sd, each edge rounded outward",
            "series": "^VIX close change paired with the same session's "
                      "close-to-close ^GSPC return",
            "tape": ("1990-01-03", "2025-07-31"),
            "anchor": "forward from the first paired session",
            "crisis_rule": "a window is dropped if it holds 1987-10-19, "
                           "2008-10-15 or 2020-03-16",
            "horizons": {
                252: {"blocks": 35, "n": 33, "t": 2.1309672358021348,
                      "centre": 1.7100, "trimmed_sd": 0.6176423652439147,
                      "unrounded": (0.3938234406945389, 3.0261747282507736),
                      "band": (0.39, 3.03)},
                504: {"blocks": 17, "n": 15, "t": 2.466699237340168,
                      "centre": 1.6600, "trimmed_sd": 0.4319092157049549,
                      "unrounded": (0.5946097144325069, 2.7253899803917117),
                      "band": (0.59, 2.73)},
            },
            "source": "programme/results/five-rows/dn1-band.json, "
                      "bands/to-2025-07-31; derived by "
                      "five-rows/scripts/derive_dn1.py, which exits non-zero "
                      "unless it first reproduces the shipped (0.70, 4.03), "
                      "the recorded triple and universal.json's multipliers, "
                      "and which opens no preset file. Re-run 2026-09-15 from "
                      "the same caches: byte-identical output",
        },
        # The tape side of the scoring rule. The model's row is a per-seed
        # median across a run's sessions aggregated as the median over
        # seeds, so the like-for-like tape centre is the MEDIAN of the nine
        # non-crisis 252-window medians, which is the middle of the triple
        # above, 2.66. The error is the panel's own form on the recorded
        # across-window trimmed sd, MEDIAN_SE_FACTOR * 0.64 / sqrt(9), and
        # `centre_df` is 9 - 2: the trim drops one window and the sd spends
        # one. The nine window VALUES are not in this package -- they are
        # `fear_band.py`'s output and were never committed -- so the error
        # here re-derives from a summary and not from the readings, which is
        # the one place on this row where a record stands in for a
        # measurement.
        "centre_se": 0.2673736826273067,
        "centre_df": 7,
        "trimmed_sd": 0.64,
        "n_windows": 9,
        "centre_estimator": "the median of the nine non-crisis 252-session "
                            "window medians",
        "sources": (
            "tools/calibration/fear_band.py, run 2026-09-04 on the cache "
            "tools/shadow/data.py writes; ^VIX 1990-01-02 to 2026-09-02, 9,236 "
            "sessions, fetched 2026-09-04T02:47:57Z from "
            "https://query1.finance.yahoo.com/v8/finance/chart/^VIX"
            "?period1=631152000&period2=1788393600&interval=1d&events=split, "
            "against the ^GSPC fetch of 2026-09-03T22:11:12Z; nine non-crisis "
            "windows read 1.34 to 3.39 with a trimmed sd of 0.64, the crisis "
            "window 3.08 over 39 sessions; pooled over 1,124 sessions since "
            "1990 the median is +1.85",
        ),
    },
    "fear_gauge_dn3": {
        # THE ROW'S RULER, NAMED, which is what it was missing rather than a
        # band. Recorded 2026-09-15.
        #
        # `ruling-longest-tape-per-row` grades every row against the longest
        # tape that row's own data supports. This row reads ^VIX against
        # ^GSPC, and ^VIX BEGINS 1990-01-02: not the request floor but the
        # series, confirmed by a fetch with `period1` at 1980-01-01 that
        # returns 9,282 rows whose first bar is 1990-01-02, and again by a
        # second independent fetch. So the row is already at its cap and
        # there is nothing longer to move it to. It needs no re-derivation
        # under the ruling and it never carried a decade band to supersede,
        # which is what separates it from `fear_gauge_dn1`.
        #
        # ITS RULER IS 1990-01-03 TO 2026-09-02, 9,234 paired sessions and 36
        # whole 252-session windows. NOT 1990-2025, and the difference is
        # worth a line because this is the ONLY one of the nineteen graded
        # rows whose ruler runs past the project's 2025-07-31 tape cut. The
        # fourteen shape rows end 2025-07-31, `index_tail_dn3_pct` ends
        # 2025-07-31, `index_drift_pct` ends 2025, `vix_ar1_debiased` ends
        # 2025-07-30, and `fear_gauge_dn1`'s new band is cut at 2025-07-31.
        #
        # WHAT THE EXTRA YEAR BUYS, measured rather than assumed, by
        # re-deriving at both cuts on the same caches: NOTHING THE BAND CAN
        # SEE. At 2025-07-31 there are 35 whole windows against 36, and the
        # SAME ten hold at least five qualifying sessions, because the last
        # qualifying window ends 2023-01-04. Same triple (3.70, 5.30, 8.48),
        # same trimmed sd 1.0972, same band (2.60, 9.58). The pooled centre
        # is +5.7300 over the same 107 sessions at either cut. One term
        # moves: the windows holding at least one qualifying session, which
        # are the error's bootstrap blocks, are 20 here and would be 19 at
        # the shorter cut, so `centre_blocks` below is the 2026-09-02 count.
        #
        # THE FORM IS THE SPREAD RULE AND NOT THE WHOLE-TAPE RULE, and that
        # is a separate question from the span. The re-derivation is RULED
        # ON and DONE: `ruling-nineteen-rows-with-dn3-re-derived` (Simon,
        # 2026-09-15) took the option the recommendation had declined, and
        # `section14` below carries what it produced. The short version is
        # that the [2.16, 7.38] the ruling priced does not exist at the
        # anchor section 14's own rule uses.
        #
        # [2.16, 7.38] IS THE FRONT-ANCHORED FIGURE. It reproduces to the
        # quantum from the forward cut and from no other. Section 14's rule
        # runs on `whole-tape/scripts/panel32.py`'s windows, which walk
        # backward from the last bar, and `INDEX_TAIL_WINDOWS` anchors the
        # same way. Cut the same tape backward and the five-session
        # condition keeps FIVE windows instead of ten, two of them the
        # crisis windows the rule drops, so n = 3 at 252: t(3) = 34.98 on a
        # scale estimated from two kept points, and the interval is
        # [-19.16, 28.58]. That is not a band. At 504 it is n = 4 and
        # [-1.73, 11.14], whose floor is below anything this row can read.
        #
        # WHY THIS ROW AND NOT `fear_gauge_dn1`. The -1 per cent row's every
        # window holds qualifying sessions, so its anchor moves each median
        # a little. Here sixteen of thirty-six windows hold none and the
        # qualifying sessions cluster inside a few months, so the anchor
        # moves whole windows across the five-session condition. The band is
        # anchor-sensitive where the other fear row's is not.
        #
        # WHAT IT WOULD HAVE COST THE CANDIDATES, measured rather than
        # feared, because the ruling accepted the risk in advance: NOTHING.
        # pt-v19 reads 6.3920 +/- 0.5001 at 252 and 6.1676 +/- 0.1921 at
        # 504, pt-v18 3.2473 +/- 0.0800 and 3.1382 +/- 0.0598, and all four
        # are inside all thirty of the band-producing cells in the anchor by
        # conditioning sweep, the tightest of which is [2.16, 7.38] at 2.0
        # of pt-v19's own standard errors. No count moves under any of them.
        #
        # AND THE CORRECTED DERIVATION IS A WEAKER RULER THAN THE ONE IT
        # WOULD REPLACE, which is the finding that decides this. Over the
        # 173 arm readings retained in the five per-seed boxes, the shipped
        # band rejects 8, the front-anchored section 14 band rejects 12, and
        # the best correctly anchored one, `last_bar_keep_crisis` below,
        # rejects 5. Section 14's third ground for the universal band was
        # that it is not a rubber stamp; on this row, at this row's own
        # anchor, it is more of one than the band it replaces. So the
        # re-derivation is RECORDED HERE AND NOT ADOPTED, on the same
        # construction ground `programme/results/ship-bar-five-rows.md`
        # section 3 refused it for `index_drift_pct` and
        # `index_tail_dn3_pct`, and the row keeps the spread rule. The
        # decade objection that motivates the universal band never reached
        # this row: it was never a decade object.
        "section14": {
            "ruled": "ruling-nineteen-rows-with-dn3-re-derived, Simon "
                     "2026-09-15",
            "outcome": "derived and recorded, NOT adopted: at the project's "
                       "own window anchor the rule gives no band at 252 and "
                       "a floor below the row's range at 504, and its best "
                       "correctly anchored form rejects 5 of 173 retained "
                       "arm readings where the shipped band rejects 8",
            "rule": "median +/- t(n) * trimmed_sd, each edge rounded "
                    "outward, over the row's own conditioned windows",
            "anchor_used_by_section_14": "last bar, as panel32.py and "
                                         "INDEX_TAIL_WINDOWS both cut",
            #: `(n, t, centre, trimmed_sd, low, high)` per variant. The
            #: first two are the figure the ruling priced and are FRONT
            #: anchored; the rest are the project's own anchor.
            "variants": {
                "front_drop_crisis_252": (8, 3.175579, 4.767499, 0.819959,
                                          2.16, 7.38),
                "front_drop_crisis_504": (6, 3.991761, 4.519999, 0.918269,
                                          0.85, 8.19),
                "last_bar_drop_crisis_252": (3, 34.977805, 4.709999, 0.682357,
                                             -19.16, 28.58),
                "last_bar_drop_crisis_504": (4, 7.930551, 4.705001, 0.810739,
                                             -1.73, 11.14),
                "last_bar_keep_crisis_252": (5, 5.039474, 5.869999, 0.983925,
                                             0.91, 10.83),
                "last_bar_keep_crisis_504": (6, 3.991761, 5.607500, 1.189435,
                                             0.85, 10.36),
            },
            "arm_rejections_of_173": {"shipped": 8, "front_drop_crisis": 12,
                                      "last_bar_keep_crisis": 5},
            "moves_no_count": "pt-v19 and pt-v18 are IN at both horizons "
                              "under every variant above and under the "
                              "shipped band",
            "source": "programme/results/dn3-rederive.md, and the three "
                      "scripts under programme/results/dn3-rederive/scripts",
        },
        "ruler": {
            "named": "2026-09-15",
            "cap": "^VIX, whose first bar is 1990-01-02",
            "tape": ("1990-01-03", "2026-09-02"),
            "paired_sessions": 9234,
            "blocks": 36,
            "band_windows": 10,
            "error_blocks": 20,
            "compliant_with": "ruling-longest-tape-per-row: the span is the "
                              "row's own data cap, so no longer tape exists",
            "form": "the spread rule, kept. Section 14's rule was ruled on "
                    "and re-derived on 2026-09-15 and did not replace it; "
                    "`section14` above carries the measurement and the "
                    "construction ground",
            "anchor": "forward from the first paired session, which is NOT "
                      "the project's rule and which this band is sensitive "
                      "to; FEAR_DN3_WINDOWS carries the cut and the cost",
            "cut_sensitivity": "band, triple, trimmed sd and pooled centre "
                               "are identical at a 2025-07-31 cut; only "
                               "error_blocks falls from 20 to 19",
        },
        "claim": "median change in the volatility index on sessions at or below "
                 "-3 percent, POOLED across the certification seeds because a "
                 "252-day run holds none on about a third of seeds; the band "
                 "departs from the shared rule for the same reason, since a "
                 "calm year holds no such session (2017 held none): it is the "
                 "shared rule applied across every 252-session window since "
                 "1990 that holds at least five such sessions, ten windows",
        "windows": (3.70, 5.30, 8.48),
        # 7.12 IS A DECADE-PANEL READING AND EVERY OTHER NUMBER IN THIS BLOCK
        # IS WHOLE-TAPE. Corrected 2026-09-15 by naming it rather than by
        # changing it, because nothing reads the value and the fault is that
        # it was unlabelled. It is the 2019-07-03..2020-07-01 window of the
        # ten 252-session windows cut from 2015-07-01, which reads +7.1150
        # over 14 qualifying sessions and is the window `fear_gauge_dn1`'s
        # superseded decade band also reports as its crisis reading. The
        # sources line below reads "ten windows since 1990 ... the 2020
        # window 7.12 over 14 sessions", which invites reading 7.12 as one
        # of those ten. It is not one of them. The whole-tape table's own
        # 2020 window is 2020-01-06..2021-01-04 at +6.98 over 16 sessions,
        # and it is IN the ten and IN the band, because this band excludes
        # no window. `FEAR_DN3_WINDOWS` carries both crisis windows and
        # `tests/test_reference_windows.py` asserts the distinction.
        "crisis_window": 7.12,
        "crisis_window_corpus": "the ten 252-session windows cut from "
                                "2015-07-01, NOT the whole-tape thirty-six "
                                "this band is built on",
        #: The whole-tape crisis windows, which ARE in the band's own ten.
        "crisis_windows_whole_tape": ((6.39, 23), (6.98, 16)),
        # THE CENTRE IS THE POOLED MEDIAN, +5.73, and not the 5.30 in the
        # triple above. The model's row is POOLED over every seed's sessions
        # (`AGGREGATE`), so the like-for-like tape quantity is the median of
        # the 107 real sessions since 1990 -- which the `sources` entry
        # below already records -- and 5.30 is the median of the ten WINDOW
        # medians the band was built from. Two estimators of two quantities;
        # scoring the pooled model row against 5.30 is the wrong-ruler error
        # on this row, and it is the reason the centre is written here as a
        # field rather than left to be read positionally out of the triple.
        "centre": 5.73,
        # THE ERROR IS A WINDOW-BLOCK BOOTSTRAP OF THAT POOLED MEDIAN, and
        # the blocks are EVERY 252-session window since 1990 holding at
        # least one session at or below -3 per cent: twenty of them, holding
        # all 107 of the centre's sessions. Twenty drawn with replacement,
        # their sessions pooled, the median taken, 2,000 draws at seed
        # 20260905 (`tools/calibration/fear_band.py`, `dn3_error`), sd
        # 0.6539, and the bootstrap centres at +5.60, 0.13 below the
        # recorded centre -- a fifth of its own sd. Seeds 20260906-8 give
        # 0.642, 0.643, 0.646; 10,000 draws give 0.6533.
        #
        # NOT the ten windows the band was built from. That was the form
        # first written here, and it was run (2026-09-08, `programme/
        # results/objective-blind-spots.md` section 3.1): sd 0.588 on nine
        # degrees of freedom, but its ten blocks hold 92 of the 107 sessions
        # and it centres at +5.33, 0.40 below the centre -- two thirds of
        # its own sd. The five-session floor is the BAND's condition, so a
        # window can carry a median of its own; the pooled median needs no
        # floor, and an error estimated on a different sample from the
        # centre's is the wrong-ruler pattern this entry's own comment
        # above forbids, in miniature. The all-window form resamples the
        # centre's own sample; ruled by Simon on 2026-09-09.
        "centre_se": 0.6539,
        # 20 - 1: the block bootstrap over twenty windows spends one. NOT
        # `n_windows` - 1: `n_windows` is the BAND's ten, and the error's
        # blocks are a different, larger set.
        "centre_df": 19,
        "centre_blocks": 20,
        "n_windows": 10,
        "centre_estimator": "the median of the 107 real sessions at or below "
                            "-3 per cent since 1990, pooled, with the sd of "
                            "a window-block bootstrap of that median over "
                            "the twenty 252-session windows holding at "
                            "least one such session as its error",
        "sources": (
            "tools/calibration/fear_band.py, run 2026-09-09 on the same two "
            "series (^VIX fetched 2026-09-04T02:47:57Z, ^GSPC "
            "2026-09-03T22:11:12Z, 9,234 common sessions 1990-01-03 to "
            "2026-09-02): centre_se 0.6539 is the sd of the pooled median "
            "under a window-block bootstrap, twenty blocks (every "
            "252-session window since 1990 holding at least one session at "
            "or below -3 per cent, together holding all 107), 2,000 draws "
            "at seed 20260905, bootstrap mean +5.60 against the centre "
            "+5.73, 2.5-97.5 percentiles +4.56 to +6.98, centre_df 19. The "
            "band's ten windows as blocks would give 0.588 on df 9 but hold "
            "92 of the 107 sessions and centre at +5.33; recorded here as "
            "the stated limit and not used",
            "tools/calibration/fear_band.py, run 2026-09-04, the same two "
            "series; ten windows since 1990 with at least five sessions at -3 "
            "or worse read 3.70 to 8.48 with a trimmed sd of 1.10; the 2020 "
            "window 7.12 over 14 sessions; pooled over 107 sessions since "
            "1990 the median is +5.73, which agrees with the +6.03 the engine "
            "cites for VIX_RETURN_GAIN in rust/src/economy/state.rs from FRED "
            "VIXCLS against SP500 over 2,511 common days to 2026-08",
            "the five-session floor is the rule's own condition and not a "
            "choice made to include or exclude a window; a threshold at -5 "
            "was considered and rejected because it was chosen from the 2020 "
            "tape, where five such sessions exist in one year, and a "
            "certified window rarely holds one: 22 sessions since 1990, "
            "three in 2,520 model sessions across ten seeds",
        ),
    },
    "index_tail_dn3_pct": {
        "claim": "the share of sessions, in percent, with a ^GSPC unadjusted "
                 "close-to-close return at or below -3 percent. Centre: the "
                 "mean over 35 non-overlapping 252-return windows, the "
                 "sessions 1990-07-24 to 2025-07-31, of each window's rate, 1.2132 "
                 "percent, which is 107 hits in 8,820 sessions because the "
                 "windows are of equal length. Band: centre +/- "
                 "centre_multiplier(band_rule_tolerance(9)) = 1.846 times "
                 "the across-window standard error 2.3613 / sqrt(35) = "
                 "0.3991, each edge rounded outward. NOT the shared rule: "
                 "the row grades a MEAN over the certification seeds, so its "
                 "question is whether the model's ensemble rate is "
                 "consistent with the real long-run rate given how well the "
                 "tape knows it, and [min - s, max + s] answers a different "
                 "one",
        "windows": (0.000, 0.397, 13.095),
        # Recorded at 252 and DERIVED at every horizon: the row's windows
        # exist at both, so `real_centre_df` reads them rather than this
        # field, which would be right at 252 (35 windows, one spent on the
        # mean) and wrong at 504 (17 windows give 16).
        # `tests/test_scoring_rule.py` asserts the two agree at 252.
        "centre_df": 34,
        "centre_estimator": "the mean of the 35 non-overlapping 252-return "
                            "window rates, with sd / sqrt(35) as its error",
        # NO window is excluded. The 2008-06..2009-06 window carries 33 of
        # the 107 hits and it is the sensitivity rather than a crisis
        # reading to be set aside: dropping it moves the centre to 0.864 and
        # the standard error to 0.199, which is the NON-2008 rate, a
        # different quantity that a model incapable of a 2008 would pass.
        # `fear_gauge_dn3`'s band excludes no window either, for the related
        # reason that a calm year holds no such session at all.
        "crisis_window": None,
        "sensitivity": "2008-07-22..2009-07-21 holds 33 of the 107 hits, "
                       "13.095 percent of its own sessions. It is IN the "
                       "centre and in the scale; without it the centre is "
                       "0.864 and the standard error 0.199, band [0.49, "
                       "1.24], which is the non-2008 crash rate and is shown "
                       "rather than adopted. The scale is therefore one "
                       "window's, and the band says so",
        "sources": (
            "tools/calibration/tail_band.py, run 2026-09-06 on the cache "
            "tools/shadow/data.py writes; ^GSPC 1990-01-02 to 2025-07-31, "
            "8,961 closes and 8,960 returns, fetched 2026-09-05T17:16:06Z "
            "from https://query1.finance.yahoo.com/v8/finance/chart/^GSPC"
            "?period1=631152000&period2=1754006400&interval=1d&events=split; "
            "the UNADJUSTED close, because an index pays no dividend and the "
            "model's session return carries none either",
            "the window table is INDEX_TAIL_WINDOWS in this module, from the "
            "same run, and tests/test_reference_windows.py re-derives the "
            "centre, the standard error and both band edges from it",
            "over the whole 8,960-return series the count is the same 107 at "
            "a rate of 1.1942 percent, and 93 sessions read at or above +3 "
            "percent (1.0379); the three real rates in use across the "
            "programme -- 107/9,236, 107/8,960 and this row's 107/8,820 -- "
            "span 4.7 percent, which is why a tail figure is stated as a "
            "percentage of sessions against this band and not as a multiple "
            "of an unnamed real rate",
            "tradefloor-design/programme/tail-rows-design.md, the design "
            "note: the estimator ruling, the window table with each window's "
            "sd, excess kurtosis and NBER overlap, and the four band forms "
            "that were considered and not adopted",
        ),
    },
}

#: The tape's own index tail, window by window, as data.
#:
#: `REAL_MARKETS_WINDOWS` is a different corpus -- forty US large caps over
#: one decade, read PER NAME -- and this row's real side is a cap-weighted
#: INDEX over thirty-five years, so it cannot live there and does not. The
#: two tables are read through the same three functions (`real_windows`,
#: `real_centre`, `real_centre_se`), which branch on which corpus holds the
#: row.
#:
#: `(start, end, hits, sessions)` per window, oldest first, and `start` and
#: `end` are the first and last SESSION the window counts, not the bars
#: either side of it.
#:
#: THE ANCHOR RULE, stated because a band whose anchoring is implicit is not
#: reproducible and this one decides a band edge. The windows are
#: non-overlapping blocks of consecutive RETURNS, anchored at the LATEST
#: return and walking back; the remainder at the start of the series is
#: dropped, and it is derived from the series length rather than chosen --
#: 8,960 returns less 35 blocks of 252 is 140. Two consequences, both load
#: bearing:
#:
#: the anchor is the tape's last bar, the one fixed point that does not move
#: when the cache is refetched, where anchoring at the first bar would
#: silently discard the most recent data and shift every window each time
#: the series grew;
#:
#: and the blocks are contiguous in RETURN space, so no session falls
#: between two of them. Blocking the BARS instead -- 253-bar blocks giving
#: 252 returns each -- drops one seam return at every boundary, 34 of them
#: here, and those seams are sessions the tape holds. The two constructions
#: agree on 107 hits in 8,820 sessions and on the centre, and differ in the
#: across-window sd, 2.3613 under this rule against 2.3711 bar-blocked,
#: because the hits fall into different windows.
#:
#: BOTH horizons, because a per-session rate has to be the same number at
#: both and this is where that is checked rather than asserted: 252 gives
#: [0.4733, 1.9530] and 504 gives [0.4798, 1.9945], the same band within a
#: twentieth of its own width. `envelope.BANDS_504` therefore carries the
#: 252-day band for this row with that as its argument.
INDEX_TAIL_WINDOWS: dict[str, Any] = {
    "series": "^GSPC",
    "column": "unadjusted close",
    "threshold_pct": -3.0,
    "source": "tools/calibration/tail_band.py, 2026-09-06",
    #: `(start, end, hits, sessions)`, keyed on the window's return count.
    "windows": {
        252: (
            ("1990-07-24", "1991-07-22", 1, 252),
            ("1991-07-23", "1992-07-20", 1, 252),
            ("1992-07-21", "1993-07-19", 0, 252),
            ("1993-07-20", "1994-07-18", 0, 252),
            ("1994-07-19", "1995-07-17", 0, 252),
            ("1995-07-18", "1996-07-15", 1, 252),
            ("1996-07-16", "1997-07-14", 0, 252),
            ("1997-07-15", "1998-07-14", 1, 252),
            ("1998-07-15", "1999-07-14", 5, 252),
            ("1999-07-15", "2000-07-12", 3, 252),
            ("2000-07-13", "2001-07-12", 3, 252),
            ("2001-07-13", "2002-07-18", 3, 252),
            ("2002-07-19", "2003-07-18", 7, 252),
            ("2003-07-21", "2004-07-20", 0, 252),
            ("2004-07-21", "2005-07-19", 0, 252),
            ("2005-07-20", "2006-07-19", 0, 252),
            ("2006-07-20", "2007-07-20", 1, 252),
            ("2007-07-23", "2008-07-21", 2, 252),
            ("2008-07-22", "2009-07-21", 33, 252),
            ("2009-07-22", "2010-07-21", 5, 252),
            ("2010-07-22", "2011-07-20", 0, 252),
            ("2011-07-21", "2012-07-19", 6, 252),
            ("2012-07-20", "2013-07-23", 0, 252),
            ("2013-07-24", "2014-07-23", 0, 252),
            ("2014-07-24", "2015-07-23", 0, 252),
            ("2015-07-24", "2016-07-22", 3, 252),
            ("2016-07-25", "2017-07-24", 0, 252),
            ("2017-07-25", "2018-07-24", 2, 252),
            ("2018-07-25", "2019-07-25", 3, 252),
            ("2019-07-26", "2020-07-24", 14, 252),
            ("2020-07-27", "2021-07-26", 2, 252),
            ("2021-07-27", "2022-07-26", 6, 252),
            ("2022-07-27", "2023-07-27", 2, 252),
            ("2023-07-28", "2024-07-29", 0, 252),
            ("2024-07-30", "2025-07-31", 3, 252),
        ),
        504: (
            ("1991-07-23", "1993-07-19", 1, 504),
            ("1993-07-20", "1995-07-17", 0, 504),
            ("1995-07-18", "1997-07-14", 1, 504),
            ("1997-07-15", "1999-07-14", 6, 504),
            ("1999-07-15", "2001-07-12", 6, 504),
            ("2001-07-13", "2003-07-18", 10, 504),
            ("2003-07-21", "2005-07-19", 0, 504),
            ("2005-07-20", "2007-07-20", 1, 504),
            ("2007-07-23", "2009-07-21", 35, 504),
            ("2009-07-22", "2011-07-20", 5, 504),
            ("2011-07-21", "2013-07-23", 6, 504),
            ("2013-07-24", "2015-07-23", 0, 504),
            ("2015-07-24", "2017-07-24", 3, 504),
            ("2017-07-25", "2019-07-25", 5, 504),
            ("2019-07-26", "2021-07-26", 16, 504),
            ("2021-07-27", "2023-07-27", 8, 504),
            ("2023-07-28", "2025-07-31", 3, 504),
        ),
    },
    #: Which graded row this table is the real side of. One row today; the
    #: up-tail companion reads the same series and is REPORTING_ONLY, so it
    #: takes no band from here.
    "rows": ("index_tail_dn3_pct",),
}


def index_tail_rates(horizon_days: int) -> tuple[float, ...]:
    """Each real window's rate at `horizon_days`, in percent of sessions."""
    windows = INDEX_TAIL_WINDOWS["windows"].get(int(horizon_days))
    if windows is None:
        raise ValidationError(
            f"the index tail table holds no {horizon_days}-return windows; "
            f"measured horizons are {sorted(INDEX_TAIL_WINDOWS['windows'])}. "
            "Run tools/calibration/tail_band.py at that horizon and record "
            "the windows rather than rescaling a rate from another one")
    return tuple(100.0 * hits / sessions for _, _, hits, sessions in windows)


#: The -1 per cent fear row's own window readings, as data.
#:
#: WHY THIS TABLE EXISTS. Until 2026-09-15 this row's provenance said, in its
#: own words, that "the nine window VALUES are not in this package -- they are
#: `fear_band.py`'s output and were never committed -- so the error here
#: re-derives from a summary and not from the readings, which is the one place
#: on this row where a record stands in for a measurement". Adopting a new
#: band on a row in that state would have moved the constant and left the
#: gap, so the readings the band is built from are committed here and
#: `tests/test_reference_windows.py` rebuilds both edges from them. A typed
#: edge now fails a test instead of being believed.
#:
#: `(start, end, median, sessions, crisis)` per window, oldest first.
#: `median` is the median ^VIX close change over the window's sessions whose
#: same-day ^GSPC return was at or below -1 per cent, and `sessions` is how
#: many such sessions the window held. `crisis` marks the windows the rule
#: drops. The medians are recorded to four decimal places, which is lossless:
#: ^VIX closes carry two, so a median is a two-place value or the midpoint of
#: two, and both bands reproduce exactly from these figures.
#:
#: THE ANCHOR IS THE FIRST PAIRED SESSION, walking FORWARD, which is the
#: opposite of `INDEX_TAIL_WINDOWS` above and of the universal panel, and the
#: remainder is dropped at the END: 140 returns at 252 and 392 at 504. The
#: cost is measured in `REAL_MARKETS_PROVENANCE["fear_gauge_dn1"]["band"]`
#: and it moves no verdict on the record, but this is not the project's own
#: anchor rule and the table says so rather than leaving it to be inferred.
#:
#: NO window here is empty. Every one of the 35 and 17 blocks holds at least
#: four qualifying sessions, so the band's window count is the block count
#: less the crisis drops and nothing else.
FEAR_DN1_WINDOWS: dict[str, Any] = {
    "series": "^VIX close change against the same session's ^GSPC return",
    "threshold_pct": -1.0,
    "tape": ("1990-01-03", "2025-07-31"),
    "anchor": "forward from the first paired session; remainder dropped at "
              "the end",
    "crisis_dates": ("1987-10-19", "2008-10-15", "2020-03-16"),
    "source": "programme/results/five-rows/dn1-band.json, "
              "bands/to-2025-07-31, re-run 2026-09-15 byte-identical",
    #: `(start, end, median, sessions, crisis)`, keyed on the window's
    #: return count.
    "windows": {
        252: (
            ("1990-01-03", "1990-12-31", 1.66, 42, False),
            ("1991-01-02", "1991-12-30", 0.73, 25, False),
            ("1991-12-31", "1992-12-28", 1.08, 11, False),
            ("1992-12-29", "1993-12-27", 1.42, 7, False),
            ("1993-12-28", "1994-12-23", 1.39, 15, False),
            ("1994-12-27", "1995-12-22", 0.79, 4, False),
            ("1995-12-26", "1996-12-20", 1.74, 16, False),
            ("1996-12-23", "1997-12-19", 1.41, 31, False),
            ("1997-12-22", "1998-12-21", 2.68, 33, False),
            ("1998-12-22", "1999-12-21", 1.595, 40, False),
            ("1999-12-22", "2000-12-19", 1.33, 52, False),
            ("2000-12-20", "2001-12-26", 1.55, 55, False),
            ("2001-12-27", "2002-12-26", 1.545, 72, False),
            ("2002-12-27", "2003-12-26", 1.085, 38, False),
            ("2003-12-29", "2004-12-28", 1.34, 20, False),
            ("2004-12-29", "2005-12-27", 1.35, 17, False),
            ("2005-12-28", "2006-12-27", 2.33, 13, False),
            ("2006-12-28", "2007-12-28", 2.29, 34, False),
            ("2007-12-31", "2008-12-29", 2.08, 75, True),
            ("2008-12-30", "2009-12-29", 1.92, 54, False),
            ("2009-12-30", "2010-12-29", 2.43, 38, False),
            ("2010-12-30", "2011-12-28", 2.575, 48, False),
            ("2011-12-29", "2012-12-31", 2.11, 21, False),
            ("2013-01-02", "2013-12-31", 1.69, 17, False),
            ("2014-01-02", "2014-12-31", 2.37, 19, False),
            ("2015-01-02", "2015-12-31", 1.93, 31, False),
            ("2016-01-04", "2016-12-30", 1.99, 22, False),
            ("2017-01-03", "2018-01-02", 4.37, 4, False),
            ("2018-01-03", "2019-01-03", 2.66, 33, False),
            ("2019-01-04", "2020-01-03", 2.925, 14, False),
            ("2020-01-06", "2021-01-04", 2.49, 46, True),
            ("2021-01-05", "2022-01-03", 3.315, 20, False),
            ("2022-01-04", "2023-01-04", 1.71, 63, False),
            ("2023-01-05", "2024-01-05", 1.33, 28, False),
            ("2024-01-08", "2025-01-07", 1.97, 20, False),
        ),
        504: (
            ("1990-01-03", "1991-12-30", 1.31, 67, False),
            ("1991-12-31", "1993-12-27", 1.195, 18, False),
            ("1993-12-28", "1995-12-22", 1.26, 19, False),
            ("1995-12-26", "1997-12-19", 1.46, 47, False),
            ("1997-12-22", "1999-12-21", 2.19, 73, False),
            ("1999-12-22", "2001-12-26", 1.48, 107, False),
            ("2001-12-27", "2003-12-26", 1.4, 110, False),
            ("2003-12-29", "2005-12-27", 1.35, 37, False),
            ("2005-12-28", "2007-12-28", 2.33, 47, False),
            ("2007-12-31", "2009-12-29", 2.0, 129, True),
            ("2009-12-30", "2011-12-28", 2.48, 86, False),
            ("2011-12-29", "2013-12-31", 1.775, 38, False),
            ("2014-01-02", "2015-12-31", 2.1, 50, False),
            ("2016-01-04", "2018-01-02", 2.01, 26, False),
            ("2018-01-03", "2020-01-03", 2.79, 47, False),
            ("2020-01-06", "2022-01-03", 2.915, 66, True),
            ("2022-01-04", "2024-01-05", 1.66, 91, False),
        ),
    },
    #: Which graded row this table is the real side of.
    "rows": ("fear_gauge_dn1",),
}


def fear_dn1_windows(horizon_days: int, *,
                     include_crisis: bool = False) -> tuple[float, ...]:
    """The window medians the `fear_gauge_dn1` band is built from.

    Crisis windows are EXCLUDED by default, because the band's rule drops
    them; pass ``include_crisis=True`` to read the whole block set, which is
    what a sensitivity on the crisis rule needs.
    """
    windows = FEAR_DN1_WINDOWS["windows"].get(int(horizon_days))
    if windows is None:
        raise ValidationError(
            f"the fear_gauge_dn1 window table holds no {horizon_days}-session "
            f"windows; measured horizons are "
            f"{sorted(FEAR_DN1_WINDOWS['windows'])}. Run "
            "programme/results/five-rows/scripts/derive_dn1.py at that "
            "horizon and record the windows rather than rescaling a band "
            "from another one")
    return tuple(median for _, _, median, _, crisis in windows
                 if include_crisis or not crisis)


#: The -3 per cent fear row's own corpus, and the fourth window table.
#:
#: `REAL_MARKETS_PROVENANCE["fear_gauge_dn3"]` carried a triple, a trimmed sd
#: and a window count and nothing a test could re-derive them from, which is
#: the state `fear_gauge_dn1` was in until 2026-09-15. Landed 2026-09-15 with
#: the section 14 re-derivation under `ruling-nineteen-rows-with-dn3-re-
#: derived`, so the shipped edges stop being hand-typed literals.
#:
#: THE ANCHOR IS THE FIRST PAIRED SESSION, walking FORWARD, and the shipped
#: band derives from that cut and no other. This is the same deviation
#: `FEAR_DN1_WINDOWS` above records, and until now nothing said so for this
#: row: `INDEX_TAIL_WINDOWS` anchors at the tape's last bar and argues for
#: it, `whole-tape/scripts/panel32.py` walks backward from the last bar, and
#: both fear rows walk forward. The remainder is dropped at the END, 162
#: returns at both horizons.
#:
#: WHAT THE ANCHOR COSTS HERE IS NOT WHAT IT COSTS ON `fear_gauge_dn1`, and
#: the difference is the reason this table exists. On that row every window
#: holds qualifying sessions, so moving the cut moves each window's median a
#: little. On this one SIXTEEN of the thirty-six windows hold no qualifying
#: session at all and the qualifying ones cluster inside a few months, so
#: moving the cut moves windows ACROSS the five-session condition: ten
#: windows qualify at this anchor and five at the last-bar anchor. The band
#: is therefore anchor-sensitive in a way the -1 per cent row's is not, and
#: `REAL_MARKETS_PROVENANCE["fear_gauge_dn3"]["section14"]` carries the
#: measurement at both anchors rather than one.
#:
#: `(start, end, median, sessions, crisis)` per window, oldest first, keyed
#: on the window's return count. `median` is None where the window holds no
#: qualifying session, which is what `fear_gauge_dn1`'s table never needs.
FEAR_DN3_WINDOWS: dict[str, Any] = {
    "series": "^VIX close change against the same session's ^GSPC return",
    "threshold_pct": -3.0,
    "tape": ("1990-01-03", "2026-09-02"),
    "anchor": "forward from the first paired session; remainder dropped at "
              "the end, 162 returns at both horizons",
    "crisis_dates": ("1987-10-19", "2008-10-15", "2020-03-16"),
    "band_condition": "at least five qualifying sessions in the window; no "
                      "window is excluded for being a crisis, because a calm "
                      "year holds no qualifying session at all and dropping "
                      "the stressed ones leaves a different quantity",
    "error_condition": "at least one qualifying session in the window, which "
                       "is the twenty-block set `centre_se` bootstraps over",
    "source": "programme/results/dn3-rederive/scripts/emit_table.py, run "
              "2026-09-15 against the same two caches the band was built "
              "from",
    "windows": {
        252: (
            ("1990-01-03", "1990-12-31", 7.17, 1, False),
            ("1991-01-02", "1991-12-30", 7.22, 1, False),
            ("1991-12-31", "1992-12-28", None, 0, False),
            ("1992-12-29", "1993-12-27", None, 0, False),
            ("1993-12-28", "1994-12-23", None, 0, False),
            ("1994-12-27", "1995-12-22", None, 0, False),
            ("1995-12-26", "1996-12-20", 4.21, 1, False),
            ("1996-12-23", "1997-12-19", 7.95, 1, False),
            ("1997-12-22", "1998-12-21", 4.87, 5, False),
            ("1998-12-22", "1999-12-21", None, 0, False),
            ("1999-12-22", "2000-12-19", 2.83, 3, False),
            ("2000-12-20", "2001-12-26", 4.57, 5, False),
            ("2001-12-27", "2002-12-26", 3.7, 7, False),
            ("2002-12-27", "2003-12-26", 1.72, 1, False),
            ("2003-12-29", "2004-12-28", None, 0, False),
            ("2004-12-29", "2005-12-27", None, 0, False),
            ("2005-12-28", "2006-12-27", None, 0, False),
            ("2006-12-28", "2007-12-28", 7.16, 1, False),
            ("2007-12-31", "2008-12-29", 6.39, 23, True),
            ("2008-12-30", "2009-12-29", 4.665, 12, False),
            ("2009-12-30", "2010-12-29", 6.02, 5, False),
            ("2010-12-30", "2011-12-28", 8.48, 6, False),
            ("2011-12-29", "2012-12-31", None, 0, False),
            ("2013-01-02", "2013-12-31", None, 0, False),
            ("2014-01-02", "2014-12-31", None, 0, False),
            ("2015-01-02", "2015-12-31", 10.8, 2, False),
            ("2016-01-04", "2016-12-30", 8.51, 1, False),
            ("2017-01-03", "2018-01-02", None, 0, False),
            ("2018-01-03", "2019-01-03", 5.73, 5, False),
            ("2019-01-04", "2020-01-03", None, 0, False),
            ("2020-01-06", "2021-01-04", 6.98, 16, True),
            ("2021-01-05", "2022-01-03", None, 0, False),
            ("2022-01-04", "2023-01-04", 4.17, 8, False),
            ("2023-01-05", "2024-01-05", None, 0, False),
            ("2024-01-08", "2025-01-07", None, 0, False),
            ("2025-01-08", "2026-01-09", 8.51, 3, False),
        ),
        504: (
            ("1990-01-03", "1991-12-30", 7.195, 2, False),
            ("1991-12-31", "1993-12-27", None, 0, False),
            ("1993-12-28", "1995-12-22", None, 0, False),
            ("1995-12-26", "1997-12-19", 6.08, 2, False),
            ("1997-12-22", "1999-12-21", 4.87, 5, False),
            ("1999-12-22", "2001-12-26", 3.8, 8, False),
            ("2001-12-27", "2003-12-26", 3.415, 8, False),
            ("2003-12-29", "2005-12-27", None, 0, False),
            ("2005-12-28", "2007-12-28", 7.16, 1, False),
            ("2007-12-31", "2009-12-29", 5.73, 35, True),
            ("2009-12-30", "2011-12-28", 7.93, 11, False),
            ("2011-12-29", "2013-12-31", None, 0, False),
            ("2014-01-02", "2015-12-31", 10.8, 2, False),
            ("2016-01-04", "2018-01-02", 8.51, 1, False),
            ("2018-01-03", "2020-01-03", 5.73, 5, False),
            ("2020-01-06", "2022-01-03", 6.98, 16, True),
            ("2022-01-04", "2024-01-05", 4.17, 8, False),
            ("2024-01-08", "2026-01-09", 8.51, 3, False),
        ),
    },
    #: Which graded row this table is the real side of.
    "rows": ("fear_gauge_dn3",),
}


def fear_dn3_windows(horizon_days: int, *,
                     condition: int = 5,
                     drop_crisis: bool = False) -> tuple[float, ...]:
    """The window medians the `fear_gauge_dn3` band is built from.

    `condition` is the minimum number of qualifying sessions a window must
    hold, and it defaults to the band's own five. Pass 1 for the twenty-block
    set the row's standard error bootstraps over.

    Crisis windows are KEPT by default, because this band's rule excludes no
    window; pass ``drop_crisis=True`` for section 14's form, which drops them
    and which `REAL_MARKETS_PROVENANCE` prices rather than adopts.
    """
    windows = FEAR_DN3_WINDOWS["windows"].get(int(horizon_days))
    if windows is None:
        raise ValidationError(
            f"the fear_gauge_dn3 window table holds no {horizon_days}-session "
            f"windows; measured horizons are "
            f"{sorted(FEAR_DN3_WINDOWS['windows'])}. Run "
            "programme/results/dn3-rederive/scripts/emit_table.py at that "
            "horizon and record the windows rather than rescaling a band "
            "from another one")
    return tuple(median for _, _, median, sessions, crisis in windows
                 if sessions >= condition and not (drop_crisis and crisis))


#: The reference panel's per-window readings, as data.
#:
#: `REAL_MARKETS_PROVENANCE` carries only three numbers per row, the min,
#: median and max across windows, which is enough to read a band's derivation
#: and not enough to re-derive one. This is the table those three summarise:
#: ten 253-bar windows of the same 40 US large caps, 2015-07 to 2025-07,
#: measured with THIS module's estimators at THIS panel's method.
#:
#: The 2019-07 window straddles the COVID crash and is EXCLUDED from band
#: derivation, reported beside each band as the crisis reading. Every summary
#: in `REAL_MARKETS_PROVENANCE` is taken over the nine non-crisis windows by
#: `BAND_RULE` below, and
#: `tests/test_reference_windows.py` derives all nine reproducible triples
#: from this table rather than trusting that they match.
#:
#: FOURTEEN ROWS OF FOURTEEN since 2026-09-05. The four correlation-structure
#: rows -- corr_asymmetry, corr_asymmetry_lagged, sector_excess_corr and
#: corr_persistence_acf1 -- had no per-window record here until the mechanism
#: gate needed the DISPERSION of a row across real years and not only its
#: centre: `facts.real_centre_se` cannot be derived from a min, a median and a
#: max, and reading one off the band edges would recover an interval a
#: rounding quantum wide and call it a number. The four rows below are the
#: readings the same run already held, at the same ten windows, the same
#: crisis index and the same estimators; each of their shipped bands
#: re-derives from them by `band_from_windows` with no adjustment, which is
#: what `tests/test_reference_windows.py` now checks for all fourteen.
#: `abs_return_acf5`'s provenance TRIPLE still summarises a different window
#: set from this one, so that triple is not derivable from these values and
#: the test excludes it by name rather than by tolerance -- the row's
#: per-window readings here are this window set's, and its centre and
#: dispersion come from them.
#:
#: What this unblocks: any re-derivation of a band, a leave-one-window-out
#: null of the panel against real data, and any method that needs the
#: dispersion of a statistic across real years rather than its range -- which
#: is what the mechanism gate's centre diagnostic needs on every row.
REAL_MARKETS_WINDOWS = {
    "windows": (
        "2015-07..2016-07", "2016-07..2017-07", "2017-07..2018-07",
        "2018-07..2019-07", "2019-07..2020-07", "2020-07..2021-07",
        "2021-07..2022-07", "2022-07..2023-07", "2023-07..2024-07",
        "2024-07..2025-07",
    ),
    #: Index into `windows` of the one excluded from every band derivation.
    "crisis_index": 4,
    #: The horizon these readings were measured at, in trading days. Load
    #: bearing: `real_centre_se` is the dispersion of a row across real years
    #: AT THIS WINDOW LENGTH, and clustering and correlation persistence both
    #: read two to six times higher over 504 bars, so scoring a 504-day model
    #: median against these is the wrong-ruler error `envelope.score` exists
    #: to prevent on the band side. `centre_distance` reads this rather than
    #: assuming a year.
    "horizon_days": 252,
    "roster": "40 US large caps, common to all ten windows",
    "source": "tradefloor-design/REALISM-BANDS.md, the window table",
    "values": {
        "annualised_vol_pct": (25.9, 18.3, 21.5, 25.7, 45.3, 28.2, 30.7, 29.0, 23.4, 29.9),
        "excess_kurtosis": (5.71, 36.72, 5.64, 11.06, 11.36, 5.60, 10.20, 13.44, 15.13, 13.79),
        "return_acf1": (0.030, -0.015, -0.046, -0.009, -0.244, -0.046, 0.028, 0.006, 0.018, -0.006),
        "abs_return_acf1": (0.176, 0.083, 0.165, 0.128, 0.430, 0.071, 0.076, 0.057, 0.039, 0.122),
        "abs_return_acf5": (0.045, 0.034, 0.066, 0.045, 0.343, 0.073, 0.046, 0.036, 0.036, 0.068),
        "abs_return_acf20": (0.013, -0.012, 0.001, 0.059, 0.141, 0.020, 0.020, 0.030, -0.015, 0.028),
        "cross_sectional_corr": (0.477, 0.208, 0.352, 0.357, 0.633, 0.269, 0.346, 0.369, 0.169, 0.297),
        "volume_abs_return_corr": (0.617, 0.616, 0.584, 0.527, 0.645, 0.544, 0.513, 0.502, 0.503, 0.536),
        "leverage_effect": (-0.109, -0.020, -0.087, -0.113, -0.128, 0.014, -0.038, -0.043, -0.007, -0.042),
        "volume_change_acf1": (-0.221, -0.242, -0.255, -0.259, -0.284, -0.266, -0.238, -0.296, -0.263, -0.239),
        # The four correlation-structure rows, added 2026-09-05 from the same
        # measurements the triples above summarise. The first three come from
        # tradefloor-design/real_panel_results.json (retrieved 2026-08-25, the
        # ten windows in this table's order) and the fourth from
        # tradefloor-design/real-corr-persistence-bands.json (retrieved
        # 2026-08-25, `horizons.252.windows`, whose window labels and crisis
        # flag match this table's row for row). SIX decimal places, not the
        # two or three the rows above carry: at three, rounding the reading
        # and then rounding again for the provenance triple disagrees with
        # the triple in the last place on two of the four rows, and the
        # triples are what `tests/test_reference_windows.py` derives from
        # this table. Six re-derives all four bands and all four triples.
        "corr_asymmetry": (0.083620, 0.111329, 0.347536, -0.154308, 0.167156, 0.130683, 0.083401, -0.005660, -0.021105, -0.026730),
        "corr_asymmetry_lagged": (0.110982, 0.153005, 0.262461, 0.194994, 0.074015, 0.077962, -0.091566, 0.088895, 0.104868, 0.437507),
        "sector_excess_corr": (0.151341, 0.187726, 0.145666, 0.133220, 0.103284, 0.193363, 0.199462, 0.177668, 0.151042, 0.163995),
        "corr_persistence_acf1": (-0.030074, 0.228778, 0.345540, 0.287463, 0.374252, -0.049544, 0.122706, 0.402453, 0.197257, 0.277800),
    },
    #: Rows whose provenance triple this table does NOT reproduce, with why.
    "not_derivable": {
        "abs_return_acf5": "its provenance summarises a different window set, "
                           "an eight-value list rather than these nine",
    },
}

#: The same panel at a 505-BAR window, one row per reference window, as data.
#:
#: `REAL_MARKETS_WINDOWS` is the 252-bar table and this is its 504-bar twin:
#: the same forty US large caps, the same estimators, six consecutive
#: 505-bar windows (504 daily log returns each) covering 2013-07 to 2025-07,
#: promoted from the design repository's
#: `realism-bands-504-reference-panel.json` (retrieved 2026-08-29). Until it
#: existed the library carried 504-bar BANDS with no windows underneath
#: them, so `real_centre_se` -- the dispersion of a row across real years at
#: the window length it is graded at -- was undetermined at 504 on every
#: row, and `centre_distance` refused the horizon for want of one.
#:
#: THIRTEEN rows, not fourteen: `corr_persistence_acf1` is measured on its
#: own sub-window construction and carries its own table,
#: `REAL_PERSISTENCE_WINDOWS_504`, for the same reason it carries its own
#: entry in `BAND_WINDOWS_EXCEPTIONS`.
#:
#: SIX decimal places, from the same argument the four correlation rows in
#: `REAL_MARKETS_WINDOWS` carry: the bands are re-derived from this table by
#: `tests/test_reference_windows.py`, and a table rounded shorter than the
#: band's own quantum cannot re-derive an edge that sits near one.
#:
#: `crisis_index` is 3, the 2019-07-23..2021-07-22 window that holds the
#: COVID crash, excluded from every band derivation and from every centre
#: exactly as index 4 is at 252 days. The window is kept in the table
#: rather than deleted, so the exclusion is a property of the reader and
#: visible to it.
REAL_MARKETS_WINDOWS_504 = {
    "windows": (
        "2013-07-16..2015-07-16", "2015-07-17..2017-07-18",
        "2017-07-19..2019-07-22", "2019-07-23..2021-07-22",
        "2021-07-23..2023-07-26", "2023-07-27..2025-07-31",
    ),
    #: Index into `windows` of the one excluded from every band derivation.
    "crisis_index": 3,
    "horizon_days": 504,
    "roster": "40 US large caps, common to all six windows",
    "source": "tradefloor-design/realism-bands-504-reference-panel.json, "
              "the six panels; Yahoo Finance v8 daily bars, retrieved "
              "2026-08-29",
    "values": {
        "annualised_vol_pct": (19.216427, 22.353833, 23.738032, 37.725517, 29.963137, 26.826592),
        "excess_kurtosis": (18.756861, 13.213448, 9.570653, 13.275513, 11.656521, 15.210755),
        "return_acf1": (-0.007142, 0.019115, 0.003875, -0.185553, 0.013391, -0.011487),
        "abs_return_acf1": (0.086844, 0.185348, 0.151582, 0.365624, 0.075641, 0.107090),
        "abs_return_acf5": (0.037661, 0.081906, 0.051818, 0.296852, 0.042329, 0.058708),
        "abs_return_acf20": (-0.001156, 0.052856, 0.053149, 0.127220, 0.028624, 0.020961),
        "cross_sectional_corr": (0.356987, 0.387843, 0.351402, 0.527947, 0.352781, 0.247227),
        "volume_abs_return_corr": (0.534510, 0.622733, 0.548402, 0.606696, 0.500665, 0.530010),
        "leverage_effect": (-0.018603, -0.083786, -0.092402, -0.088098, -0.039799, -0.025019),
        "volume_change_acf1": (-0.249799, -0.227681, -0.256171, -0.270115, -0.270525, -0.249609),
        "corr_asymmetry": (0.024013, 0.094736, 0.040630, 0.156259, 0.050145, -0.010517),
        "corr_asymmetry_lagged": (0.025554, 0.120831, 0.249207, 0.034493, 0.010205, 0.359768),
        "sector_excess_corr": (0.131241, 0.165127, 0.139761, 0.129726, 0.190655, 0.161457),
    },
}

#: `corr_persistence_acf1`'s own 504-bar windows, because its construction is
#: its own.
#:
#: The row is the lag-1 autocorrelation of the mean pairwise correlation over
#: non-overlapping 21-day SUB-windows, so a 505-bar window holds 24 of them
#: and the row's evidence base is not the panel's. The design repository
#: measured it separately (`real-corr-persistence-bands.json`, retrieved
#: 2026-08-25) over five 504-bar windows rather than six -- its series starts
#: one window later -- and FOUR of them are non-crisis, which is why
#: `BAND_WINDOWS_EXCEPTIONS` already records a four-window band for this row
#: at this horizon. Reading it out of `REAL_MARKETS_WINDOWS_504` would take a
#: dispersion across six windows of a quantity measured on five.
REAL_PERSISTENCE_WINDOWS_504 = {
    "windows": (
        "2015-07-17..2017-07-18", "2017-07-19..2019-07-22",
        "2019-07-23..2021-07-22", "2021-07-23..2023-07-26",
        "2023-07-27..2025-07-31",
    ),
    "crisis_index": 2,
    "horizon_days": 504,
    "roster": "the 40 US large caps of real-corr-persistence-bands.json",
    "source": "tradefloor-design/real-corr-persistence-bands.json, "
              "horizons.504.windows; retrieved 2026-08-25",
    "sub_window": 21,
    "values": {
        "corr_persistence_acf1": (0.248393, 0.428851, 0.360519, 0.356755, 0.265590),
    },
}

#: The horizons at which the shape panel has a per-window real record.
#:
#: `real_windows` and everything downstream of it REFUSE any other horizon by
#: name rather than answering from the 252-bar table, which is what they did
#: before this tuple existed: `real_windows("abs_return_acf20", horizon_days=756)`
#: returned the 252-bar readings, and the row reads six times higher over 504
#: bars than over 252. The refusal is the wrong-ruler guard moved from
#: `centre_distance`, which could only apply it to itself, into the function
#: that holds the windows.
WINDOW_HORIZONS: tuple[int, ...] = (252, 504)


#: The band rule every window-derived band on this panel is built with, as
#: code rather than as a sentence three tools and a test each paraphrased.
#: REALISM-BANDS.md states it: over the non-crisis windows, s is the sample sd
#: with the single most extreme window dropped, the band is [min - s, max + s]
#: and each edge is rounded outward at the rule's precision. "Most extreme"
#: needs a centre, and the sentence never named one. It is the MEDIAN, for the
#: reason the trim exists: the trim keeps one draw from inflating the noise
#: scale it is priced in, and a mean is pulled toward the very member the trim
#: is meant to drop, so a mean-centred trim can drop a different window when a
#: cluster sits on the other side of it. Two shipped edges sit where the two
#: centres disagree, the 252-bar `cross_sectional_corr` floor and the 505-bar
#: `sector_excess_corr` ceiling, and both are the median's. Before this
#: constant existed the test helper and one design-repo tool trimmed around
#: the mean and the two band tools around the median; every band they had
#: produced was checked against both, and only those two edges differed.
BAND_RULE = (
    "over the non-crisis windows: s is the across-window sample sd with the "
    "single window farthest from the MEDIAN dropped; band = [min - s, max + s] "
    "over the untrimmed windows; each edge rounded outward, to two decimal "
    "places, or to two significant figures for volatility and kurtosis"
)

#: The rows whose band edges round to two significant figures rather than two
#: decimal places: the two whose scale is tens rather than hundredths.
TWO_SIGNIFICANT_FIGURES = frozenset({"annualised_vol_pct", "excess_kurtosis"})


def trimmed_sd(values: Sequence[float]) -> float:
    """The across-window sd with the window farthest from the median dropped.

    Fewer than three values cannot be trimmed and give the plain sample sd,
    or zero for a single value, which is what the two band tools did.
    """
    values = list(values)
    if len(values) < 3:
        return statistics.stdev(values) if len(values) > 1 else 0.0
    centre = statistics.median(values)
    kept = sorted(values, key=lambda v: abs(v - centre))[:-1]
    return statistics.stdev(kept)


def shared_rule(values: Sequence[float]) -> tuple[float, float, float]:
    """`BAND_RULE` before rounding: (min - s, max + s, s) over the values."""
    values = list(values)
    s = trimmed_sd(values)
    return (min(values) - s, max(values) + s, s)


def round_outward(value: float, edge: str, key: str) -> float:
    """One band edge rounded away from the interior at the rule's precision."""
    if key in TWO_SIGNIFICANT_FIGURES:
        if value == 0:
            return 0.0
        magnitude = math.floor(math.log10(abs(value)))
        quantum = 10.0 ** (magnitude - 1)
    else:
        quantum = 0.01
    if edge == "low":
        return math.floor(value / quantum) * quantum
    if edge == "high":
        return math.ceil(value / quantum) * quantum
    raise ValueError(f"edge must be 'low' or 'high', not {edge!r}")


def band_from_windows(key: str, values: Sequence[float]) -> tuple[float, float]:
    """`BAND_RULE` in full: the rounded band the windows alone produce for `key`."""
    low, high, _ = shared_rule(values)
    return (round_outward(low, "low", key), round_outward(high, "high", key))


#: Where a shipped 252-bar band departs from `band_from_windows` on its nine
#: non-crisis windows, and why: `{row: {edge: (shipped value, kind, reason)}}`.
#: REALISM-BANDS.md allows an edge to move OUTWARD to a retrieved,
#: horizon-compatible literature value, and names two INWARD clamps, both sign
#: corrections every retrieved source supports. This table is those moves as
#: data, so `tests/test_reference_windows.py` derives every shipped band as
#: the rule plus its named adjustment and a band that is neither is caught.
#: A row absent here ships the rule's band exactly. `abs_return_acf5` is
#: absent because its provenance summarises a different window set and its
#: band is not derivable from `REAL_MARKETS_WINDOWS` at all.
#:
#: Clamp #2 has a cost the table states: the 2020-07..2021-07 window reads
#: +0.014, inside the rule's ceiling of +0.06 and outside the clamped 0.00, so
#: the clamp rejects one real year in nine. It decides no current verdict
#: (every certified reading is negative) and it is what keeps `_verdict`'s
#: "too weak" wording correct for a reversed effect. Whether the sign prior
#: outranks the excluded window is a ruling, recorded as open.
REAL_MARKETS_ADJUSTMENTS: dict[str, dict[str, tuple[float, str, str]]] = {
    "annualised_vol_pct": {
        "high": (36.0, "outward",
                 "Campbell, Lettau, Malkiel & Xu (2022): a typical stock's "
                 "total annualised vol is about 36 since 1997; the rule's 34 "
                 "is the windows' 30.7 plus s, rounded"),
    },
    "abs_return_acf1": {
        "low": (0.02, "inward",
                "clamp #1: zero or negative clustering appears in no "
                "retrieved source and no observed window; the rule's -0.01 "
                "would admit a model with no volatility memory"),
    },
    "leverage_effect": {
        "high": (0.0, "inward",
                 "clamp #2: every retrieved source gives the effect a negative "
                 "sign, and the rule's +0.06 would certify a reversed effect as "
                 "real-market behaviour; the cost is the 2020-07..2021-07 "
                 "window at +0.014, excluded"),
    },
}


#: Where a shipped 504-bar band departs from `band_from_windows` on its
#: non-crisis windows, and why. EMPTY, and that is the finding rather than an
#: omission: `REAL_MARKETS_504`'s own note records that the literature
#: reconciliation applied to `REAL_MARKETS` "needs a retrieved,
#: horizon-compatible source per statistic and is a human judgement that has
#: not been made at this horizon", so every 504-bar edge is the mechanical
#: rule's and nothing else. The table exists so the derivation test has one
#: shape at both horizons and so the first 504-bar adjustment has to be
#: written down here with its reason rather than appearing as a band the
#: rule cannot reproduce.
REAL_MARKETS_ADJUSTMENTS_504: dict[str, dict[str, tuple[float, str, str]]] = {}


#: The across-seed standard deviation of each statistic at the shipped
#: preset. It ships beside the bands because a band exit is only comparable
#: across statistics once it is priced in units of that statistic's own
#: sampling noise: pooled volatility runs ~40 on a band of width ~20 while
#: every autocorrelation is measured in hundredths, and any comparison that
#: ignores the scales silently becomes a comparison of volatility alone.
#: `tradefloor.loss` consumes these as its diagonal weighting.
#:
#: Provenance, and the history behind the values. These are measured on the
#: CURRENT model (pt-v1) over THIRTY seeds (101-130), at the published
#: method: `Universe.random(40, seed=111)`, 252 days, `measure()` per seed,
#: sample (n-1) standard deviation. They are 2x to 8x LARGER than the
#: six-seed values they replace (vol 0.878 -> 6.46, |r| acf(1) 0.0165 ->
#: 0.0946, cross-sectional corr 0.0137 -> 0.1087), and the change is the
#: model, not the seed count: under the factor-variance process, whether a
#: seed's 252 days contain a market-variance regime is itself a per-seed
#: random draw, so the panel statistics carry a regime-occurrence random
#: effect the legacy constant-sigma factor did not have. Seed 114 is the
#: visible case: 65.7% volatility and |r| acf(20) of 0.24 on the same
#: protocol every other seed ran. The legacy values priced band exits in
#: noise units 4-8x too small, which overstated every scaled distance the
#: loss reported. A six-seed sd also carries ~32% relative sampling error
#: (1/sqrt(2(n-1))) against ~13% at thirty seeds, and thirty matches the
#: phase-2 instrument's protocol, so these scales and the instrument's
#: Jacobian rows are directly comparable -- the independently measured
#: seed_sd in tools/calibration/results/jacobian-pt-v1-2026-08-22-chunk1
#: .json agrees with every overlapping entry here to six significant
#: figures. The values are re-derivable in-repo (the engine is
#: deterministic per seed); tests/test_loss.py pins them to a live
#: re-measurement rather than to a committed artifact.
#: `tradefloor.loss.seed_sd_from_panels` remains the estimator, and the loss
#: takes a replacement as a parameter rather than requiring an edit here.
SEED_SD = {
    "annualised_vol_pct": 6.45368,
    "excess_kurtosis": 1.17811,
    "return_acf1": 0.0525798,
    "abs_return_acf1": 0.0955416,
    "abs_return_acf5": 0.0567399,
    "abs_return_acf20": 0.0467066,
    "cross_sectional_corr": 0.108444,
    "volume_abs_return_corr": 0.0415843,
    "leverage_effect": 0.0769232,
    "volume_change_acf1": 0.0107678,
    # These four joined the table on 2026-08-25 on the same protocol as the
    # rest of it. A first draft measured three of them on pt-v3 with the
    # population estimator and was caught by the test that re-derives this
    # table; the pt-v3 values (0.1435, 0.1451, 0.0071) are recorded in the
    # calibration record beside the certification medians. pt-v1 is
    # deliberate: this table is the frozen denominator of every "seed-sd
    # out" figure the project publishes, so it stays at the baseline preset
    # rather than moving with each era.
    "corr_asymmetry": 0.163194,
    "corr_asymmetry_lagged": 0.115927,
    "sector_excess_corr": 0.0063937,
    # The largest seed sd of any correlation-type statistic: a twelve-point
    # acf1 per seed. See CALIBRATION-FOLLOWUPS.md section 64.
    "corr_persistence_acf1": 0.279423,
    # The level row, on its own protocol and at the table's preset: pt-v1
    # on `LEVEL_PROTOCOL`, the roster varying with the seed, seeds 101 to
    # 130, 252 days, measured 2026-09-04 on the box run named in
    # `SEED_SD_LEVEL_PROVENANCE`. The only entry not on the held roster,
    # and the only one that moves when the roster generator does, by the
    # protocol's own definition. The two crisis rows carry no entry, for
    # the two reasons the same provenance states.
    "index_drift_pct": 9.55716,
}

#: Real-market bands re-derived at a 504-DAY measurement window.
#:
#: `REAL_MARKETS` comes from ten 252-bar windows. These come from the same
#: reference roster, the same estimators and the same band rule at 505 bars,
#: because these statistics are strongly horizon-dependent and scoring a
#: 504-day measurement against the 252-day bands is grading with the wrong
#: ruler. This project made that error three times in one day before the
#: bands were promoted out of the design notes and shipped here.
#:
#: They are mostly TIGHTER, not looser, which is the opposite of what the
#: first attempt assumed: the model looked flattered by the 252-day bands on
#: kurtosis specifically, whose 252-day floor of 1.6 hid a real failure --
#: real markets read 7.1 to 22 over two-year windows against the model's 5.2.
#:
#: Mechanical bands only. The literature reconciliation applied to
#: `REAL_MARKETS` needs a retrieved, horizon-compatible source per statistic
#: and is a human judgement that has not been made at this horizon.
REAL_MARKETS_504 = {
    "annualised_vol_pct": (16, 34),
    "excess_kurtosis": (7.1000000000000005, 22),
    "return_acf1": (-0.03, 0.04),
    "abs_return_acf1": (0.04, 0.22),
    "abs_return_acf5": (0.02, 0.1),
    "abs_return_acf20": (-0.02, 0.07),
    "cross_sectional_corr": (0.23, 0.41000000000000003),
    "volume_abs_return_corr": (0.48, 0.65),
    "leverage_effect": (-0.13, 0.02),
    "volume_change_acf1": (-0.29, -0.21),
    # Five non-crisis 505-bar windows, same rule; tradefloor-design/bands-504-conditional-corr.json.
    "corr_asymmetry": (-0.04, 0.13),
    "corr_asymmetry_lagged": (-0.10, 0.47),
    "sector_excess_corr": (0.11, 0.22),
    # Twenty-four sub-windows; four non-crisis windows +0.25 to +0.43,
    # median +0.31 (tradefloor-design/real-corr-persistence-bands.json).
    "corr_persistence_acf1": (0.19, 0.49),
}

#: The across-seed noise scale at 504 days, the companion to `SEED_SD`.
#:
#: Measured because it could not be assumed: on the same preset, roster and
#: seeds the 504-day scales run from 0.46 of the 252-day ones
#: (`volume_abs_return_corr`) to 0.86 (`corr_asymmetry_lagged`). EVERY row
#: falls, which is what an estimator does on a longer window, so an
#: objective reusing `SEED_SD` at 504 days under-penalises every row.
#:
#: RE-MEASURED 2026-09-06. The table shipped until then was pt-v3 on roster
#: fingerprint 5d8de78b, which the generator reconciliation retired, while
#: its numerators were pt-v16 and pt-v18 on the roster that ships. Nothing
#: bound it: `tests/test_facts.py` asserted only that its persistence entry
#: is the largest correlation-type scale. It is the denominator of every
#: 504-day `room_sd`, of the 504 arm of `loss.dual_horizon_loss`, of
#: `calibrate.py --dual-horizon`, of `section8_check.py` and of
#: `atlas_survey.py`, so a stale one restates all of them.
#:
#: The old table said kurtosis was 3.21x NOISIER at 504 days, and that ratio
#: was the stated reason this table exists. On one preset, one roster and one
#: seed set it reads 0.62x: less noisy, not more. The 3.21 compared pt-v3 on
#: a retired roster with pt-v1 on the live one, and the direction it reported
#: was the difference between them rather than the horizon.
#:
#: The consequence is the largest in the table. Every 504-day `room_sd` this
#: project has published divided by a denominator up to 5.2x too large
#: (kurtosis, 3.78 against 0.73), so a statistic reported as sitting 0.6 seed
#: sd inside its 504-day band sits over three. The band VERDICTS are
#: unaffected -- a scale moves no band -- and the 504 arm of
#: `loss.dual_horizon_loss` is affected as the square, so kurtosis
#: contributed about 27x less to that objective than it should have.
SEED_SD_504 = {
    "annualised_vol_pct": 4.779408306,
    "excess_kurtosis": 0.7265305516,
    "return_acf1": 0.04042526606,
    "abs_return_acf1": 0.05930312554,
    "abs_return_acf5": 0.04497743519,
    "abs_return_acf20": 0.03579083816,
    "cross_sectional_corr": 0.06981599524,
    "volume_abs_return_corr": 0.01899405944,
    "leverage_effect": 0.05607850586,
    "volume_change_acf1": 0.008456262085,
    "corr_asymmetry": 0.1349171341,
    "corr_asymmetry_lagged": 0.09982258299,
    "sector_excess_corr": 0.004748081416,
    "corr_persistence_acf1": 0.1983588686,
}

#: Where SEED_SD_504 came from.
SEED_SD_504_PROVENANCE = {
    "source": "facts.measure() at pt-v1 on the committed forty-name panel "
              "roster, 504 days, seeds 101-130, sample sd across seeds. The "
              "252-day companion's protocol with the horizon changed and "
              "nothing else, so the two tables are a same-source pair for "
              "the first time",
    "date": "2026-09-06",
    "model_fingerprint": "pt-v1",
    "universe_fingerprint": "9be68b9bc37e79785765df2f395a9348"
                            "650a4e9293507680532293fdf78808dd",
    "days": 504,
    "seeds": tuple(range(101, 131)),
    "estimator": "sample standard deviation (n - 1) across seeds",
    "script": "programme/scripts/hruler-seedsd504.py in the design "
              "repository, run on the box hruler1",
    "measured_at_commit": "2bfb2dbf56b2444f25cc78cd2961bcf152e1cd5b",
    # The error bar. An identity for a normal sample and the leading term
    # generally: the relative standard error of a sample sd on n draws is
    # 1 / sqrt(2(n - 1)), which is 13.1 per cent at thirty seeds. Every
    # entry above carries it. A scale shipped without one is a chosen
    # constant with more decimal places.
    "relative_standard_error": 0.13130643285972254,
    "relative_standard_error_identity": "1 / sqrt(2 * (n - 1)), n = 30",
    # The instrument. The same script measured the same seeds at 252 days
    # and reproduced the committed `SEED_SD` to 4.5e-06 relative, worst row.
    # A harness whose 252-day arm cannot reproduce the shipped table has
    # not earned the right to replace the 504-day one, and without this
    # check a harness defect would look exactly like a horizon effect.
    "instrument_check": "the same run's 252-day arm reproduces facts.SEED_SD "
                        "to 4.5e-06 relative on its worst row",
    "roster_note": "the roster is read from tests/fixtures/"
                   "panel-roster-40.json rather than drawn by "
                   "Universe.random, so this table no longer moves when the "
                   "generator does. That is exactly what happened to the "
                   "table this replaces",
    "pinned_by": "tests/test_loss.py re-measures two of the thirty seeds "
                 "live at 504 days and re-derives the sd from the committed "
                 "per-seed table",
    "bands": "facts.REAL_MARKETS_504, from tradefloor-design/"
             "bands-504-noncrisis.json and bands-504-conditional-corr.json, "
             "five non-crisis 505-bar windows of the same forty-name "
             "reference roster; unchanged by this measurement",
    "supersedes": {
        "date": "2026-08-23",
        "model_fingerprint": "pt-v3",
        "universe": "Universe.random(40, seed=111) on the generator before "
                    "the 2026-09-03 reconciliation, roster fingerprint "
                    "5d8de78b",
        "why": "two eras of preset and a retired roster, bound by nothing "
               "but a max-ordering assert",
    },
}

#: Where SEED_SD's values come from, carried as data so any consumer -- the
#: loss report, a calibration manifest -- can quote it rather than assert it.
SEED_SD_PROVENANCE = {
    "source": "re-measured on the shipped baseline preset: facts.measure() "
              "on the committed panel roster, 252 days, seeds 101-130, "
              "sample sd across seeds",
    "date": "2026-09-03",
    "model_fingerprint": "pt-v1",
    "universe_fingerprint": "9be68b9bc37e79785765df2f395a9348"
                            "650a4e9293507680532293fdf78808dd",
    "days": 252,
    "seeds": tuple(range(101, 131)),
    "estimator": "sample standard deviation (n - 1) across seeds",
    "roster_note": "the roster is read from tests/fixtures/"
                   "panel-roster-40.json rather than drawn by "
                   "Universe.random, so this table no longer moves when the "
                   "generator does. It moved once for that reason: the "
                   "generator was reconciled so a drawn roster opens at its "
                   "own fair value, every roster re-rolled, and all fourteen "
                   "values went stale on a change that touched no "
                   "coefficient and no estimator.",
    "pinned_by": "tests/test_loss.py re-measures two of the thirty seeds "
                 "live and re-derives the sd from the committed per-seed "
                 "table",
    # The cross-check, written as three claims because the single sentence
    # it replaced implied one larger one.
    "cross_check": "the base panels of a jacobian.py run on the same "
                   "roster, preset, horizon and seeds hold the same thirty "
                   "panels to the bit, all 420 values",
    "cross_check_detects": "a transcription error in a hand-maintained "
                           "block of 420 floats, and a platform or "
                           "interpreter difference when the two sides are "
                           "run on different machines",
    "cross_check_cannot_detect": "an estimator defect. Both sides call "
                                 "facts.measure and derive the scale "
                                 "through loss.seed_sd_from_panels, so they "
                                 "share the estimator and cannot disagree "
                                 "about it. A genuinely independent check "
                                 "would be a second estimator written "
                                 "against the recorded bars, which does not "
                                 "exist.",
    # A tighter agreement here is agreeing about LESS than the looser one it
    # replaced, and the difference is worth stating so nobody reads it the
    # other way.
    "cross_check_caveat": "this pair is bit-exact and single-platform. The "
                          "pair it replaces agreed to six significant "
                          "figures and spanned two platforms, macOS arm64 "
                          "under CPython 3.11.15 against a table measured "
                          "elsewhere, so it also carried evidence about "
                          "portability that this one does not. The market "
                          "is bit-reproducible across platforms and the "
                          "statistics derived from it are not: the same "
                          "panel under CPython 3.11.16 on Linux and 3.13.12 "
                          "on Windows differs by up to 8.4e-15 relative on "
                          "excess kurtosis while all three known-answer "
                          "digests match.",
    "companion_not_re_measured": "the envelope module's measured tables "
                                 "were taken on the superseded roster and "
                                 "have not been re-measured on this one. "
                                 "SEED_SD_504 was, on 2026-09-06, by the "
                                 "protocol above with the horizon changed "
                                 "and nothing else.",
}

#: The provenance of the `SEED_SD` entries measured on `LEVEL_PROTOCOL`
#: rather than on the held panel roster, and the reason each crisis row
#: carries none. A consumer that scales a row by its seed sd reads
#: `SEED_SD.get(row)` and reports None where there is no entry; it never
#: substitutes a neighbour's.
SEED_SD_LEVEL_PROVENANCE = {
    "rows": ("index_drift_pct",),
    "source": "facts.measure() at pt-v1 on Universe.random(40, seed=s) with "
              "market seed s, 252 days, seeds 101-130, sample sd across "
              "seeds; the box run era-level of 2026-09-04 on feat/level-row "
              "at 6326337, four arms through programme/scripts/level-jobs.sh "
              "in the tradefloor-design repository, of which the pt-v1 arm "
              "is this one",
    "date": "2026-09-04",
    "model_fingerprint": "pt-v1",
    "estimator": "sample standard deviation (n - 1) across seeds",
    "roster_note": "drawn per seed by Universe.random rather than read from "
                   "the committed fixture, so this entry moves when the "
                   "generator does, by the protocol's own definition, where "
                   "the held-roster entries do not",
    "pinned_by": "tests/test_loss.py re-measures two of the thirty seeds "
                 "live on the level protocol and re-derives the sd from the "
                 "committed per-seed table those panels must match",
    "same_run_at_the_default": "pt-v18 on the same protocol and seeds reads "
                               "an sd of 6.4767, the figure beside "
                               "envelope.CERTIFIED_LEVEL; pt-v16 read 7.0888 "
                               "there on the same run, and the entry above "
                               "is at pt-v1 because the table freezes its "
                               "denominators there",
    "unmeasured": {
        "fear_gauge_dn3": "pooled over every seed's sessions, so no per-seed "
                          "value exists to take a standard deviation of; its "
                          "graded value stands on the pooled session count "
                          "reported beside it",
        "index_tail_dn3_pct": "a per-seed value exists, so an sd could be "
                              "taken, and it is not frozen here. The row's "
                              "seed dispersion is a property of the "
                              "certified model's MIXTURE -- how many seeds "
                              "hold no session at -3 percent at all -- and "
                              "at pt-v1 that mixture does not exist: every "
                              "run opens in expansion at phase age zero, so "
                              "an sd taken there is the opening's and not "
                              "the model's. It is measured on the "
                              "certification run instead, where envelope."
                              "certify reports it beside the rate as se_m "
                              "with the share of seeds at zero, the share at "
                              "five or more hits and the maximum. Nothing "
                              "divides by it: the band's width is the tape's "
                              "own standard error and does not read this",
        "fear_gauge_dn1": "the table freezes its scale at pt-v1, where the "
                          "return channel cannot answer a session: "
                          "vix_return_source is 0.0 there, so the gauge reads "
                          "the last tick's cap-weighted move rather than the "
                          "day's, clamped at 0.03 percent of it with a gain "
                          "of 25.0 and a reversion of 0.12, a ceiling of 0.75 "
                          "on the target and about 0.09 on the day, which "
                          "rust/src/economy/state.rs states beside "
                          "VIX_RETURN_GAIN. pt-v9 moved the source to the "
                          "session and the clamp to 15.0 in the same step, a "
                          "change of units rather than a loosening, so the "
                          "two clamps do not compare as numbers. On the level "
                          "protocol the row reads 0.021 at the median with an "
                          "sd of 0.026 across seeds at pt-v1, against 0.950 "
                          "and 0.149 at pt-v16, so the rule would record the "
                          "noise draw's sd under the name of a response. The "
                          "row's room_sd reads None until a rule for a row "
                          "whose mechanism postdates pt-v1 is decided",
    },
}

# --------------------------------------------------------------------------
# The VIX's persistence, and the ruler a run of a given length is graded by
#
# One estimator, one window, one debias, and all three named wherever the
# number appears. The row this section exists for -- the model's
# `vix_ar1_debiased` -- is a PER-RUN reading: one seed's daily VIX levels
# over the run, the lag-one autocorrelation about that run's own mean, the
# median across seeds, then the first-order small-sample correction at the
# run's length. The figure it was compared against for months, 0.976, is the
# WHOLE-SPAN autocorrelation of ^VIX -- one series of 8,960 bars, and it
# checks at 0.9772.
#
# Those are two different quantities and the model's is the smaller one. The
# same estimator on the same tape, cut into 252-session windows, reads a
# median of 0.9151 raw, and debiased at the run length it lands about 0.047
# BELOW the whole-span figure. So the gap runs the other way from the way it
# was read: every identity arm of the level fix is more persistent than the
# real VIX rather than short of it, and `id-v18-d100`'s 0.9763 "MET" was a
# comparison between two estimators.
#
# RULED (`programme/PT-V19-CHARTER.md` section 1.5, 2026-09-06): the ruler is
# the WINDOWED, DEBIASED figure, derived from the tape by the same estimator
# the model uses, at the same window length as the horizon being graded. Not
# the whole-span number. A run of `CERTIFIED_HORIZON_DAYS` sessions is the
# model's natural unit and the panel certifies at that length; re-estimating
# the model whole-span would mean concatenating seeds, which are different
# market paths and not one series.
#
# And so it is horizon-parameterised, like every other ruler here rather than
# by a second convention: it is registered in `RULERS_BY_HORIZON` below and
# `real_vix_ar1` REFUSES a horizon nobody derived it at, instead of handing
# back the 252-day figure for a 504-day run. That is this same defect one
# length further out, and section 1.3 moves the certified horizon to 504.


def level_ar1(series: Sequence[float]) -> float:
    """Lag-one autocorrelation of a LEVEL series about its own mean.

    `_autocorrelation` at lag one, CALLED rather than reimplemented. A
    second lag-one autocorrelation in this module would be two functions
    that agree numerically today, which is the whole of the defect this
    section repairs; `tests/test_vix_ar1_ruler.py` asserts that the tape's
    ruler and the model's row both resolve this one object, and constructs
    an exactly-agreeing twin to show the assertion rejects it.

    A series with no lag-one pair, or a constant one, is REFUSED rather
    than reported as 0.0, which is what `_autocorrelation` returns for
    both. A constant VIX is not a VIX with no persistence, and a
    fabricated zero inside a median across runs moves that median without
    announcing itself.
    """
    values = [float(v) for v in series]
    if len(values) < 3:
        raise ValidationError(
            f"a lag-one autocorrelation needs at least three observations, "
            f"got {len(values)}")
    mean = statistics.fmean(values)
    if sum((v - mean) ** 2 for v in values) == 0.0:
        raise ValidationError(
            "a constant series has no lag-one autocorrelation, and 0.0 -- "
            "which `_autocorrelation` returns for one -- would enter a "
            "median across runs as a reading rather than as the absence of "
            "one")
    return _autocorrelation(values, 1)


def debias_ar1(rho: float, n: int) -> float:
    """`rho + (1 + 3 rho) / n`: the Marriott-Pope / Kendall first correction.

    A lag-one autocorrelation estimated about the sample's OWN mean is
    biased DOWN by about `(1 + 3 rho) / n`. At n = 252 and rho near 0.93
    that is 0.015 -- the same size as the distance between the model and
    the tape -- so leaving it out would read a correct model as too fast.

    Both sides of the comparison carry it, which is the point of putting it
    here: the correction cancels only if the ruler and the row apply the
    same one at the same n, and the ruler's n is the window length while
    the row's is the run length, which is why they are the same number.
    """
    if n < 3:
        raise ValidationError(
            f"the debias divides by the length the reading was taken over, "
            f"which must be at least 3, got {n}")
    return float(rho) + (1.0 + 3.0 * float(rho)) / int(n)


def median_ar1_debiased(readings: Iterable[float], *, n: int) -> float:
    """The median of several raw lag-one readings, debiased at their length.

    The aggregation the model's row already used -- median first, then the
    correction on the median -- kept in that order because the correction
    is monotone in rho and the rows on the record were computed this way.
    """
    values = [float(r) for r in readings]
    if not values:
        raise ValidationError(
            "no lag-one readings to take a median of; a ruler derived from "
            "nothing is the failure this refusal exists for")
    return debias_ar1(statistics.median(values), n)


def median_level_ar1(runs: Iterable[Sequence[float]], *, length: int) -> float:
    """`level_ar1` of every run, the median across them, debiased at `length`.

    THE function. The tape's ruler is this applied to non-overlapping
    `length`-session blocks of ^VIX closes; the model's row is this applied
    to one `length`-day VIX level series per seed. Same function, different
    data, which is the property the estimator has to have and did not: the
    number the model was graded against was computed by different code over
    a different span.

    A run of any other length is REFUSED. The debias divides by a length,
    and a 504-day model row read through the 252-day ruler is this
    section's defect at the next horizon, reached by the same route, which
    is that nothing checked.
    """
    series = [list(run) for run in runs]
    if not series:
        raise ValidationError(
            "no runs to take a median lag-one autocorrelation over")
    wrong = sorted({len(run) for run in series} - {int(length)})
    if wrong:
        raise ValidationError(
            f"every run must be {length} observations long, which is the "
            f"length the debias divides by and the horizon the ruler is "
            f"derived at; got runs of {wrong}. Grade a run at its own "
            f"horizon (`facts.real_vix_ar1`) rather than through this one.")
    return median_ar1_debiased([level_ar1(run) for run in series],
                               n=int(length))


def vix_levels(macro: Any) -> list[float]:
    """The daily VIX level series of a recorded run, from its macro table.

    `Engine.macro_table()`'s `vix` column in day order -- the series every
    harness reporting a VIX AR1 has been rebuilding for itself out of
    `state_snapshot()["economy"]["vix"]`, one seed at a time. One reader, so
    the MODEL side of the comparison is as fixed as the estimator is.
    """
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "vix_levels reads Arrow tables and needs pyarrow. Install it "
            "with: pip install tradefloor[arrow]") from exc
    table = pa.table(macro).to_pydict()
    rows = [(int(day), v) for day, v in zip(table["day"], table["vix"])
            if v is not None]
    return [float(v) for _, v in sorted(rows)]


#: The tape's own raw lag-one readings, per window, per horizon: what
#: `tools/calibration/vix_ar1_ruler.py` cuts out of ^VIX, recorded here so
#: the ruler below is a DERIVATION rather than a literal.
#:
#: ^VIX daily closes, 1990-01-02..2025-07-30, 8,960 bars, through
#: `tools/shadow/data.py` (vendor data, not committed, so the tool
#: re-derives and this module records). Cut front-anchored into consecutive
#: non-overlapping blocks of `days` closes with the incomplete tail dropped:
#: 35 blocks to 2025-01-06 at 252, 17 to 2024-01-04 at 504. Non-overlapping
#: because the model's runs are independent paths and overlapping windows
#: would share bars; front-anchored because the tape's own start is the one
#: anchor nobody chooses, and the end-anchored cut is reported beside it as
#: the residual.
REAL_VIX_AR1_WINDOWS: dict[int, tuple[float, ...]] = {
    252: (
        0.932986, 0.936984, 0.932669, 0.831825, 0.891625, 0.807711,
        0.863434, 0.950051, 0.963172, 0.883080, 0.912968, 0.944540,
        0.968119, 0.980974, 0.902529, 0.875963, 0.919307, 0.950860,
        0.974381, 0.967820, 0.923876, 0.951197, 0.892371, 0.811283,
        0.898589, 0.915077, 0.936773, 0.801044, 0.904585, 0.840256,
        0.954118, 0.823957, 0.903528, 0.941548, 0.848448,
    ),
    504: (
        0.952398, 0.941728, 0.899211, 0.960833, 0.951699, 0.942366,
        0.976975, 0.934406, 0.959890, 0.974375, 0.945409, 0.918508,
        0.920164, 0.952086, 0.902994, 0.957686, 0.967174,
    ),
}

#: The ruler: the tape's windowed, debiased VIX AR1, per horizon.
#:
#: DERIVED at import from the readings above, by the same two functions the
#: model's row goes through, so there is nowhere to write the number down and
#: no way for it to drift from the readings it comes from. The value is not
#: quoted here either, and `tests/test_vix_ar1_ruler.py` asserts it appears
#: nowhere in this file: a ruler with a literal beside its derivation has two
#: spellings, and the one a reader copies is the stale one. Print it with
#: `tools/calibration/vix_ar1_ruler.py`.
def persistence_statistics(macro: Any, *, days: int) -> dict[str, Any]:
    """The VIX's own persistence, as `measure` reports it: one row.

    THE THIRD PART, beside `panel_statistics` and `fear_statistics`, and it
    exists as a function for the reason `test_noise_attribution` asserts:
    everything `measure` reports comes from a part that can be called and
    checked on its own, so a key appearing in a panel and in none of the
    parts is a failure by name. Computing this inline in `measure` would
    have made it the one row with no independent caller.

    ONE RUN, so this is `level_ar1` debiased at the run length and NOT
    `median_level_ar1`, which is the median ACROSS runs. A harness taking
    the median of these across seeds lands on the same number, because
    `debias_ar1` is affine and increasing -- which is what lets a per-run
    row answer for a ruler defined across runs.

    `level_ar1` REFUSES a series with no lag-one pair and a constant one,
    and neither is a VIX with no persistence. The row is then reported
    ABSENT with its reason under `vix_ar1_debiased_blind` rather than
    defaulted: the scoring rule already reports a row missing from a record
    as blind, and a fabricated 0.0 inside a median across seeds would move
    it without announcing itself.
    """
    try:
        return {VIX_AR1_ROW: debias_ar1(level_ar1(vix_levels(macro)), days)}
    except ValidationError as exc:
        return {VIX_AR1_ROW + "_blind": str(exc)}


#: The MODEL row this ruler grades, spelled once. `facts.measure` emits it
#: and `real_windows` answers for it; a second spelling is how the ruler and
#: the row came to be two different quantities in the first place.
VIX_AR1_ROW = "vix_ar1_debiased"

REAL_VIX_AR1: dict[int, float] = {
    days: median_ar1_debiased(raws, n=days)
    for days, raws in REAL_VIX_AR1_WINDOWS.items()
}

REAL_VIX_AR1_PROVENANCE = {
    "kind": "derived",
    "claim": "the lag-one autocorrelation a correct model's VIX level "
             "series would read over a run of `days` sessions, on the "
             "estimator and at the length the model's own row uses",
    "series": "^VIX daily close, 1990-01-02..2025-07-30, 8,960 bars, via "
              "tools/shadow/data.py (Yahoo v8 chart API; vendor data is "
              "not committed, so the tool re-derives and this module "
              "records the windows)",
    "estimator": "facts.median_level_ar1: facts.level_ar1 -- which is "
                 "facts._autocorrelation at lag one, about each window's "
                 "own mean -- per window, the median across windows, then "
                 "facts.debias_ar1",
    "window": "consecutive non-overlapping blocks of `days` closes, "
              "front-anchored at the tape's first bar, the incomplete tail "
              "dropped: 35 windows at 252 (to 2025-01-06, 140 bars "
              "dropped), 17 at 504 (to 2024-01-04, 392 dropped)",
    "debias": "rho + (1 + 3 rho) / days, Marriott-Pope / Kendall first "
              "order, at the WINDOW length, which is the run length the "
              "model's row debiases at",
    "residual": "at 252, 35 windows, raw median 0.915077, sd across "
                "windows 0.0513, bootstrap se of the median 0.0120 "
                "(20,000 resamples, seed 20260906); the end-anchored cut "
                "reads 0.930052, moving the ruler by 0.000113. At 504, 17 "
                "windows, raw median 0.951699, sd 0.0230, bootstrap se of "
                "the median 0.0061, and the end-anchored cut reads "
                "0.950772 -- the anchoring choice moves the 504 ruler by "
                "0.0086, MORE than its own sampling error, because "
                "seventeen windows place 2008 differently. The 504 figure "
                "carries that residual and the 252 figure does not",
    "not_the_ruler": "the whole-span reading of the same series is 0.9772 "
                     "raw and 0.9777 debiased, 0.047 above this one. It is "
                     "`REAL_AR1 = 0.976` in the design repository's "
                     "programme scripts, correctly documented as "
                     "whole-span there and compared against 252-day model "
                     "rows anyway",
    "source": "tradefloor-design/programme/PT-V19-CHARTER.md section 1.5, "
              "ruled 2026-09-06; derived by "
              "tools/calibration/vix_ar1_ruler.py",
}


def real_vix_ar1(days: Any, *, what: str = "this measurement") -> float:
    """The windowed, debiased VIX AR1 a `days`-session run is graded against.

    Raises for a horizon the ruler was not derived at, on the argument
    `rulers_for_horizon` raises on: handing back the 252-day figure for a
    504-day run compares a reading against a number computed over a
    different length, which is the defect this ruler exists to end rather
    than to relocate. The tape's readings at 252 and 504 differ by about
    three hundredths, so the substitution is not a small one.
    """
    try:
        return REAL_VIX_AR1[int(days)]
    except (KeyError, TypeError, ValueError):
        raise ValidationError(
            f"the VIX AR1 ruler has not been derived at {days!r} days, so "
            f"{what} cannot be graded against it. The horizons with one "
            f"are {sorted(REAL_VIX_AR1)}. Cut the tape at {days!r} with "
            f"tools/calibration/vix_ar1_ruler.py and record its windows in "
            f"REAL_VIX_AR1_WINDOWS, rather than reading the ruler for "
            f"another length."
        ) from None


# --------------------------------------------------------------------------
# Which ruler belongs to which horizon
#
# A band table and a noise scale are each derived AT a horizon, and a panel is
# measured at one. Pairing a 504-day measurement with the 252-day bands has
# been made repeatedly in this project and was never caught by anything here,
# because the horizon lived in the caller: `measure` records `days` and
# nothing read it. `loss.dual_horizon_loss` exists because a search bought one
# horizon's realism with the other's; `tools/calibration/evaluate_axes.py`
# graded the one axis whose purpose is a different horizon against the 252-day
# bands while labelling them "the TRUE bands", and published a `generalises`
# verdict from it.
#
# So the horizon becomes data. Any scoring call can now ask what horizon its
# panel came from and what horizon its ruler was derived at, and REFUSE the
# pair when they disagree, rather than computing a number that means nothing.

#: The horizon `REAL_MARKETS` and `SEED_SD` were derived at, and `measure`'s
#: default. `envelope.CERTIFIED_HORIZON_DAYS` is this value.
CERTIFIED_HORIZON_DAYS = 252

#: The horizons that HAVE a ruler, and what makes each one up.
#:
#: A scoring call looks a horizon up here instead of choosing a table, so a
#: horizon with no band set is refused BY NAME rather than falling through to
#: the 252-day set. Adding a horizon means deriving ALL THREE parts at it:
#: bands without a noise scale cannot report how far inside a band a statistic
#: sits, a noise scale without bands grades nothing, and `vix_ar1` is here
#: because a persistence ruler that does not move with the horizon is how a
#: 252-day model row came to be graded against a whole-span tape figure
#: (`REAL_VIX_AR1` above).
#:
#: No 60-day, 180-day or 756-day entry exists because no band set has been
#: derived at those horizons. `realism_bands_horizon.py` in the design
#: repository measured real windows at 756, 1260 and 2520 bars on a 32-name
#: sub-roster, and none of those tables has had the literature reconciliation
#: applied, so none of them is a shipped ruler.
RULERS_BY_HORIZON: dict[int, dict[str, Any]] = {
    CERTIFIED_HORIZON_DAYS: {
        "bands": REAL_MARKETS,
        "bands_name": "facts.REAL_MARKETS",
        "seed_sd": SEED_SD,
        "seed_sd_name": "facts.SEED_SD",
        "vix_ar1": REAL_VIX_AR1[CERTIFIED_HORIZON_DAYS],
        "vix_ar1_name": "facts.REAL_VIX_AR1[252]",
    },
    504: {
        "bands": REAL_MARKETS_504,
        "bands_name": "facts.REAL_MARKETS_504",
        "seed_sd": SEED_SD_504,
        "seed_sd_name": "facts.SEED_SD_504",
        "vix_ar1": REAL_VIX_AR1[504],
        "vix_ar1_name": "facts.REAL_VIX_AR1[504]",
    },
}

#: Every table whose horizon is known, so a ruler handed in as an argument can
#: be identified rather than trusted. `envelope` registers its own
#: seventeen-row 504-day table here at import, because a table this module
#: cannot see is a table this module cannot check.
_KNOWN_TABLES: list[tuple[Any, int, str]] = [
    (REAL_MARKETS, CERTIFIED_HORIZON_DAYS, "facts.REAL_MARKETS"),
    (REAL_MARKETS_504, 504, "facts.REAL_MARKETS_504"),
    (SEED_SD, CERTIFIED_HORIZON_DAYS, "facts.SEED_SD"),
    (SEED_SD_504, 504, "facts.SEED_SD_504"),
]


def register_ruler_table(table: Mapping[str, Any], days: int,
                         name: str) -> None:
    """Record the horizon a band or noise table was derived at.

    For a table this module does not define. `envelope.BANDS_504` is the
    seventeen-row 504-day set -- the fourteen shape rows of
    `REAL_MARKETS_504` plus the level and crisis rows carrying their 252-day
    bands, each with its argument inline there -- and it is the table
    `envelope.score` grades a 504-day panel with. Registered rather than
    copied here, because a second copy of a band table is a second thing to
    keep in step, and `test_envelope` already pins that these two agree.
    """
    for existing, known_days, known_name in _KNOWN_TABLES:
        if existing is table:
            if known_days != days:
                raise ValidationError(
                    f"{name} is already registered as {known_name} at "
                    f"{known_days} days; a table cannot be derived at two "
                    f"horizons")
            return
    _KNOWN_TABLES.append((table, int(days), name))


def rulers_for_horizon(
    days: Any, *, what: str = "this measurement",
) -> tuple[Mapping[str, tuple[float, float]], Mapping[str, float]]:
    """The bands and the noise scale derived at `days`, or a refusal.

    The refusal is the point. Every horizon this project has measured at
    other than 252 and 504 -- 60 in a shipped example, 180 in a test, 756 and
    1008 and 2520 on boxes -- has no band set, and grading one of those
    against the 252-day bands compares two different quantities and returns a
    plausible number. So this raises, and names the horizons that do have a
    ruler.
    """
    try:
        row = RULERS_BY_HORIZON[int(days)]
    except (KeyError, TypeError, ValueError):
        raise ValidationError(
            f"no band set has been derived at {days!r} days, so {what} "
            f"cannot be graded. The horizons with a ruler are "
            f"{sorted(RULERS_BY_HORIZON)}. Falling back to the "
            f"{CERTIFIED_HORIZON_DAYS}-day bands would grade a measurement "
            f"of one quantity against a ruler for another, which is the "
            f"error this refusal exists to make impossible; measure at a "
            f"horizon that has a ruler, or derive one at {days!r} days and "
            f"register it in RULERS_BY_HORIZON."
        ) from None
    return row["bands"], row["seed_sd"]


def horizon_of_table(table: Any) -> tuple[int | None, str | None]:
    """Which horizon a band or noise table was derived at, and its name.

    ``(None, None)`` for a table nobody registered -- a caller's own bands, or
    a re-estimated noise scale from `loss.seed_sd_from_panels`. Unknown is
    reported as unknown rather than guessed: an unregistered table is
    UNCHECKED, and a guess is the fallback this module is removing.

    Identity first, then equality, because `dict(facts.REAL_MARKETS)` is the
    same ruler and passing a copy is how several callers hand one over.
    """
    for known, days, name in _KNOWN_TABLES:
        if table is known:
            return days, name
    for known, days, name in _KNOWN_TABLES:
        if table == known:
            return days, name
    return None, None


def horizon_of_panels(panels: Iterable[Any], *,
                      what: str = "these panels") -> int | None:
    """The single horizon a set of measured panels was taken at.

    ``None`` when no panel records one -- an already-aggregated median, or a
    mapping a caller built by hand -- which is unknown rather than 252. A MIX
    raises: a median over panels taken at two horizons is a statistic of
    neither, and that is exactly as wrong as the ruler mismatch and harder to
    see.
    """
    seen = set()
    for panel in panels:
        if not isinstance(panel, Mapping):
            continue
        days = panel.get("days")
        if days is not None:
            seen.add(int(days))
    if not seen:
        return None
    if len(seen) > 1:
        raise ValidationError(
            f"{what} were measured at {sorted(seen)} days and cannot be "
            f"aggregated: a median across horizons is a statistic of no "
            f"horizon, and no ruler grades it.")
    return next(iter(seen))


def check_ruler_horizon(*, panel_days: int | None,
                        bands: Any = None, seed_sd: Any = None,
                        what: str = "this call") -> int | None:
    """Refuse a measurement graded by a ruler derived at another horizon.

    Three disagreements, each of which has happened:

    * a 504-day panel against the 252-day bands -- `evaluate_axes.py`'s
      horizon axis, and the reason this function exists;
    * a 60-day panel against the 252-day bands -- `07-research-workflow.py`;
    * the right bands with the other horizon's noise scale, which
      `dual_horizon_loss` calls "the wrong-ruler error in a subtler dress"
      and which rescales every distance without changing a band verdict.

    Returns the horizon the call was checked at, or ``None`` where nothing
    could be checked, so a caller can record which it was. A table nobody
    registered is unknown rather than wrong and passes; a caller that can
    name its table should register it, because silence about an unregistered
    table is the one place this design still permits the error.
    """
    band_days, band_name = (horizon_of_table(bands) if bands is not None
                            else (None, None))
    sd_days, sd_name = (horizon_of_table(seed_sd) if seed_sd is not None
                        else (None, None))

    if band_days is not None and sd_days is not None and band_days != sd_days:
        raise ValidationError(
            f"{what} pairs {band_name} (derived at {band_days} days) with "
            f"{sd_name} (derived at {sd_days} days). Each horizon carries "
            f"its own bands AND its own noise scale; pairing one with the "
            f"other divides every distance by the wrong denominator.")

    for ruler_days, ruler_name in ((band_days, band_name), (sd_days, sd_name)):
        if (panel_days is not None and ruler_days is not None
                and panel_days != ruler_days):
            raise ValidationError(
                f"{what} grades a {panel_days}-day measurement against "
                f"{ruler_name}, which was derived at {ruler_days} days. "
                f"Those are two different quantities and the comparison "
                f"between them is meaningless however plausible its number "
                f"looks. Score the panel against the ruler for its own "
                f"horizon (the horizons with one are "
                f"{sorted(RULERS_BY_HORIZON)}), or re-measure at "
                f"{ruler_days} days.")

    return panel_days if panel_days is not None else band_days

#: The first two are MARGINAL: properties of one series taken on its own. The
#: other six are DEPENDENCE: how returns move together across time, across
#: stocks, with volume, and asymmetrically with their own sign. Which half a
#: statistic falls in is the finding this module reports, so the split is a
#: constant rather than a presentation detail.
MARGINAL = ("annualised_vol_pct", "excess_kurtosis")

#: The graded panel in three groups, and the certified set is split along
#: them. SHAPE rows are the fourteen a market losing a fifth of its value in
#: a year could hold in band, because each is invariant or nearly so to a
#: drift; the certification gate has always read them and still does. LEVEL
#: rows read a first moment, and CRISIS rows are reserved for the fear
#: gauge's response to a large down day. A level or crisis row the default
#: preset fails is held RED in `envelope` and never widened to pass: a
#: graded first moment with a flattering band would certify the failure it
#: exists to catch. Listed explicitly, so a row added to `REAL_MARKETS`
#: must be placed in a group on purpose; `test_facts` asserts the three
#: cover the table.
SHAPE = (
    "annualised_vol_pct", "excess_kurtosis", "return_acf1", "abs_return_acf1",
    "abs_return_acf5", "abs_return_acf20", "cross_sectional_corr",
    "volume_abs_return_corr", "leverage_effect", "volume_change_acf1",
    "corr_asymmetry", "corr_asymmetry_lagged", "sector_excess_corr",
    "corr_persistence_acf1",
)
LEVEL = ("index_drift_pct",)
#: The index tail row is CRISIS and not LEVEL: it counts the sessions the
#: two fear rows condition on, so the three are read together, and the
#: report already gives the group its own verdict. LEVEL would
#: fit the arithmetic -- a rate is a first moment -- and was not chosen
#: because the level row's protocol note and its `SEED_SD` treatment are
#: specific to the drift.
CRISIS = ("fear_gauge_dn1", "fear_gauge_dn3", "index_tail_dn3_pct")
#: SCORED BUT NOT BANDED, and the only group that is.
#:
#: `SHAPE + LEVEL + CRISIS` is an exact partition of `REAL_MARKETS` -- the
#: banded rows, which `envelope.certify` reads -- and `test_facts` asserts
#: it. This group sits outside that partition on purpose: section 1.5 ruled
#: on the RULER for the VIX's persistence and not on a band for it, and a
#: band is a separate derivation nobody has done. So the row enters the
#: scoring rule, where a centre and an error are all it needs, and does NOT
#: enter certification, where it would need a width no one has derived.
#:
#: Why it has to enter something. The ruler was delivered, recorded in
#: `RULERS_BY_HORIZON`, and READ BY NOTHING: not `envelope.CERTIFIED`, not
#: `loss.rule_table`, not `measure`. With the row absent, raising
#: `vix_mean_reversion` improved `S` by 12 to 31 points while taking this
#: statistic from within one standard error of its ruler to five and more --
#: an objective that priced one end of a dial and not the other. That is
#: section 1.3's defect exactly: the row that could not fail was the row
#: that was not there.
PERSISTENCE = (VIX_AR1_ROW,)

#: How a row is read across seeds. The shape rows are medians over the
#: certification seeds, which is what every recorded panel and band was
#: derived with. A level row is a MEAN over thirty seeds: its seed standard
#: deviation is about 6.5 points a year on a band nine points wide, so a
#: ten-seed median of a model sitting at the band's centre would fail a
#: two-point margin about a third of the time, and the mean is the estimator
#: whose error the band's width was set against.
#: A POOLED row is read over the sessions of every certification run
#: together, because its bucket is empty on some runs: about a third of
#: 252-day runs hold no session at -3 percent or worse, and a median over an
#: empty bucket and one over a full bucket are the same shape in a table. A
#: pooled row's per-run panel carries the samples under `<row>_samples` and
#: their count under `<row>_sessions`, and the graded value is the median
#: of the pooled samples with the pooled count reported beside it.
#: A POOLED_RATE row is a THIRD kind and is named as one, because "pooled"
#: was already two things in this module and a rate is neither of them: the
#: graded value is `100 * sum(hits) / sum(sessions)` over the certification
#: runs, a ratio of two sums rather than a median of a concatenated sample.
#: Its per-run panel carries `<row>_hits` and `<row>_sessions`. At equal run
#: lengths the ratio IS the mean of the per-run rates, which is what
#: `aggregate_value` returns for it and what the band's width was set
#: against; the two differ only on panels of unequal length, and
#: `tests/test_facts.py` pins both halves of that.
#:
#: Why a COUNT row is never a median across seeds. Per seed the value is
#: `k / 251` with `k` a small integer, and on the tape thirteen of
#: thirty-five real 252-return windows hold no session at or below -3
#: percent at all: the median real window reads 0.397 percent against a real
#: mean of 1.213. A median over thirty such seeds moves only when a seed
#: crosses an integer near the middle, and is unchanged by any seed above it
#: going from two hits to thirty-three, so it is nearly blind to the
#: quantity its row names. One run read both ways differed by 3.2x, which is
#: the finding this kind exists to close
#: (tradefloor-design/programme/results/tail-estimator.md).
AGGREGATE = {"index_drift_pct": "mean", "fear_gauge_dn3": "pooled",
             "index_tail_dn3_pct": "pooled_rate"}

#: Which seed the certification varies, per group. The shape rows vary the
#: market seed on one roster, `Universe.random(40, seed=111)`, held fixed,
#: which is the protocol every band and every recorded panel was derived
#: on. A LEVEL row varies the ROSTER with the seed as well: a level is a
#: property of the roster as much as of the model, because a drawn roster
#: opens away from fair value by a draw with an across-roster standard
#: deviation near 0.05 in log, worth about five points of first-year drift,
#: and the held roster sits 0.78 of that above the population. Thirty
#: market seeds on one roster certify that roster; thirty rosters certify
#: the model. So the level row's certified value is the mean over seeds
#: 101 to 130 of a run on `Universe.random(40, seed=s)` with market seed
#: `s`, and its seed sd is measured on the same protocol. See the note on
#: which seed varies in `_index_drift_pct`.
#:
#: The CRISIS rows are certified on the same run. A gauge's answer to a
#: down day, pooled or medianed over thirty rosters, describes the model
#: rather than roster 111, and one protocol for every graded row outside
#: the shape set keeps `envelope` to two tables under one rule. The same
#: preset reads +1.9740 on the level row with roster 111 held against
#: -13.6431 with the roster varying, a gap of 3.3 points on one preset at
#: thirty seeds each, so the protocol is part of a level figure's value
#: and not a detail of its measurement: a reader cannot compare a level
#: figure with any other unless both name their protocol, and a value
#: from one protocol is never compared with a band or a certified value
#: from the other. The consumers that hold one roster
#: (`tools/calibration/shapley.py`) withhold the verdict on these rows
#: and say why.
LEVEL_PROTOCOL = {
    "groups": ("level", "crisis"),
    "seeds": tuple(range(101, 131)),
    "roster": "Universe.random(40, seed=<seed>)",
    "estimator": "mean across seeds for the level row; the crisis rows by "
                 "AGGREGATE, which is the median across seeds for "
                 "fear_gauge_dn1, the median of the pooled samples for "
                 "fear_gauge_dn3, and the pooled RATE -- the hits over every "
                 "seed divided by the sessions over every seed -- for "
                 "index_tail_dn3_pct",
    "days": 252,
}


def aggregate_value(key: str, values: Sequence[float]) -> float:
    """One graded value for `key` from its per-seed readings.

    A `pooled_rate` row reads the MEAN here, which is the pooled rate
    exactly when every run contributed the same number of sessions --
    the certification protocol's case. `aggregate_panels` divides the two
    sums instead and needs no such condition, so a consumer holding the
    per-seed counts should use that; this function has only the rates.
    """
    if AGGREGATE.get(key, "median") in ("mean", "pooled_rate"):
        return statistics.fmean(values)
    return statistics.median(values)


def pooled_rate_counts(key: str) -> tuple[str, str]:
    """The two per-run panel keys a `pooled_rate` row's graded value sums.

    The convention, in one place: a rate row is named `<row>_pct` and its
    counts `<row>_hits` and `<row>_sessions`. Stated as a function rather
    than as three string literals in three consumers, and refused for a row
    not named that way, so a rate row added without its counts fails here
    instead of being silently medianed somewhere else.
    """
    if not key.endswith("_pct"):
        raise ValidationError(
            f"a pooled_rate row is named <row>_pct and carries <row>_hits "
            f"and <row>_sessions beside it; {key!r} is not")
    stem = key[:-len("_pct")]
    return stem + "_hits", stem + "_sessions"


def aggregate_panels(panels: Sequence[Mapping[str, Any]],
                     keys: Iterable[str] | None = None) -> dict[str, float]:
    """The graded panel from per-seed panels, each row by its own estimator.

    A row absent from every panel is omitted; a row absent from some is
    aggregated over the seeds that carry it.
    """
    out: dict[str, float] = {}
    for key in (REAL_MARKETS if keys is None else keys):
        if AGGREGATE.get(key) == "pooled":
            pooled = [x for p in panels for x in (p.get(key + "_samples") or ())]
            if pooled:
                out[key] = statistics.median(pooled)
            continue
        if AGGREGATE.get(key) == "pooled_rate":
            # The ratio of two sums, never the median of a list of rates.
            # A panel carrying the rate but not its two counts is not enough
            # to pool, so the row is OMITTED rather than aggregated by a
            # different estimator under the same name.
            hit_key, session_key = pooled_rate_counts(key)
            hits = [p.get(hit_key) for p in panels]
            sessions = [p.get(session_key) for p in panels]
            if (all(h is not None for h in hits)
                    and all(n is not None for n in sessions)
                    and sum(sessions)):
                out[key] = 100.0 * sum(hits) / sum(sessions)
            continue
        present = [p[key] for p in panels if p.get(key) is not None]
        if present:
            out[key] = aggregate_value(key, present)
    return out


def pooled_sessions(panels: Sequence[Mapping[str, Any]], key: str) -> int:
    """How many sessions a pooled row's graded value stands on."""
    return sum(len(p.get(key + "_samples") or ()) for p in panels)

#: Panel rows that are MEASURED AND REPORTED BUT NOT GRADED, against the
#: reason no band exists for them. A key here is deliberately absent from
#: `REAL_MARKETS`, so it earns no verdict, cannot pass, cannot fail, and
#: cannot enter `envelope.CERTIFIED` -- and `report` says why at the point
#: it prints the number, reading this dict rather than a retyped sentence.
#:
#: A row with no band is worth having anyway. The alternative is not
#: reporting the quantity, and an unreported quantity is one nobody
#: measures; see `_index_drift_pct` for the case that put this here.
#:
#: Empty since 2026-09-03: `index_drift_pct` sat here from the day it was
#: added until its band was derived from real series, and the dict stays
#: so the next measured-but-ungraded row has somewhere to carry its reason.
REPORTING_ONLY: dict[str, str] = {
    "fear_gauge_dn5": (
        "no band: a session at -5 percent or worse is too rare for a "
        "certified window, 22 in the real series since 1990 and three in "
        "2,520 model sessions across ten seeds, so the row is a diagnostic "
        "of the saturating channel and the -3 percent row is the graded one. "
        "An empty cell here is a property of the WINDOW and not of the "
        "model: most real 252-session windows hold no such session either, "
        "so a blank reads as nothing to measure rather than as a failure"
    ),
    "fear_gauge_up1": (
        "no band: the up-side response is reported so the down-side "
        "saturation can be read as a ratio against it; the real up side "
        "stays flat where the down side falls"
    ),
    "index_excess_kurtosis": (
        "no band, and it is the SCALE-FREE companion to index_tail_dn3_pct: "
        "the count row can be reached by a model with a third more "
        "volatility and no fat tail at all, and this row is what separates "
        "the two. The tape reads a median of 1.456 over the same 35 windows, "
        "with a standard error of the median of 0.428, 33 of 35 above zero "
        "and 23 above 1.0. Three band forms and none of them certifies "
        "anything: the shared rule gives [-2.24, 17.89], because a real year "
        "(2003-07..2004-07) read -0.06 and the fidelity form therefore "
        "admits a Gaussian index; the mechanism form's null is zero and is "
        "excluded by any variance process, so it says nothing about "
        "thinness; and the centre form is never a gate, for the reason "
        "centre_distance's docstring gives. Printed with its distance from "
        "1.456 beside the count row, so the count is never read alone"
    ),
    "index_tail_up3_pct": (
        "no band: the up side of the count, reported so the down side is "
        "never read as an asymmetry when it is a volatility reading. The "
        "tape holds 93 sessions at or above +3 percent in 8,960 (1.038 "
        "percent) against 107 below -3 (1.194), a down-to-up ratio of 1.15; "
        "the model's excess, where it has one, has been nearly symmetric, "
        "1.64-1.87x down against 1.60-1.77x up, and a down-only row would "
        "hide that"
    ),
}

#: Row labels for `report`. The dict order of REAL_MARKETS above is the print
#: order, and these name the rows.
LABELS = {
    "annualised_vol_pct": "annualised vol %",
    "excess_kurtosis": "excess kurtosis",
    "return_acf1": "return acf(1)",
    "abs_return_acf1": "|return| acf(1)",
    "abs_return_acf5": "|return| acf(5)",
    "abs_return_acf20": "|return| acf(20)",
    "cross_sectional_corr": "cross-sectional corr",
    "volume_abs_return_corr": "volume vs |return|",
    "leverage_effect": "leverage, r vs |r+1|",
    "volume_change_acf1": "volume change acf(1)",
    "corr_asymmetry": "corr, down vs up days",
    "corr_asymmetry_lagged": "corr, after down vs up",
    "sector_excess_corr": "same-sector excess corr",
    "corr_persistence_acf1": "corr persistence acf(1)",
    "index_drift_pct": "index drift %/yr",
    "fear_gauge_dn1": "VIX move, day <= -1%",
    "fear_gauge_dn3": "VIX move, day <= -3%",
    "fear_gauge_dn5": "VIX move, day <= -5%",
    "fear_gauge_up1": "VIX move, day >= +1%",
    "index_tail_dn3_pct": "sessions <= -3%, %",
    "index_tail_up3_pct": "sessions >= +3%, %",
    "index_excess_kurtosis": "index excess kurtosis",
}


def _autocorrelation(series: Sequence[float], lag: int) -> float:
    if len(series) <= lag + 1:
        return 0.0
    mean = statistics.mean(series)
    variance = sum((x - mean) ** 2 for x in series)
    if variance == 0:
        return 0.0
    return sum(
        (series[i] - mean) * (series[i - lag] - mean)
        for i in range(lag, len(series))
    ) / variance


def _log_returns(prices: Sequence[float]) -> list[float]:
    return [
        math.log(prices[i] / prices[i - 1])
        for i in range(1, len(prices))
        if prices[i - 1] > 0 and prices[i] > 0
    ]


def _unit_centred(series: Sequence[float]) -> list[float] | None:
    """Centre a series and scale it to unit length, or None if it is constant.

    Two of these dotted together give the Pearson correlation, which turns the
    all-pairs cross-sectional loop from N^2 correlations into N centrings and
    N^2 dot products.
    """
    mean = statistics.mean(series)
    centred = [x - mean for x in series]
    norm = math.sqrt(sum(x * x for x in centred))
    if norm == 0:
        return None
    return [x / norm for x in centred]


def _correlation(a: Sequence[float], b: Sequence[float]) -> float | None:
    """Pearson correlation over the common length, or None where there is none.

    A constant series has NO correlation rather than a zero one, and returning
    0.0 would put an undefined reading in the middle of a real-market band.
    """
    n = min(len(a), len(b))
    if n < 3:
        return None
    unit_a, unit_b = _unit_centred(a[:n]), _unit_centred(b[:n])
    if unit_a is None or unit_b is None:
        return None
    return sum(x * y for x, y in zip(unit_a, unit_b))


def _zumbach_terms(
    returns: Sequence[float], n: int
) -> tuple[list[float], list[float], list[float], list[float]]:
    """The four per-window terms of `zumbach_asymmetry`, for ONE series.

    Kept separate so names can be pooled by their TERMS rather than by
    concatenating their returns. Concatenation would build windows straddling
    two companies, which reads the end of one and the start of another as a
    single trend and returns a plausible number for a quantity nobody
    computed.
    """
    past_trend_sq: list[float] = []
    future_var: list[float] = []
    past_var: list[float] = []
    future_trend_sq: list[float] = []
    for t in range(n, len(returns) - n):
        back = returns[t - n:t]
        fwd = returns[t + 1:t + 1 + n]
        if len(fwd) < n:
            break
        past_trend_sq.append(math.fsum(back) ** 2)
        past_var.append(math.fsum(x * x for x in back))
        future_trend_sq.append(math.fsum(fwd) ** 2)
        future_var.append(math.fsum(x * x for x in fwd))
    return past_trend_sq, future_var, past_var, future_trend_sq


def zumbach_asymmetry(returns: Sequence[float], n: int) -> float | None:
    """Time-reversal asymmetry of volatility feedback, at horizon `n`.

    `A(n) = corr(P^2, Qp) - corr(Qm, F^2)`, where over a window of `n`
    sessions either side of `t`, `P` is the past trend, `Qm` the past realised
    variance, `F` the future trend and `Qp` the future realised variance. It
    asks whether a past TREND predicts future variance better than past
    variance predicts a future trend, which for a time-reversible process it
    does not.

    Positive in equity data at scales of days to weeks (Muller and others
    1997; Zumbach 2009; Chicheportiche and Bouchaud 2014).

    ## Why this is a null control and not only a target

    A(n) is EXACTLY ZERO in population for any stationary process
    `r_t = sigma_t * z_t` whose `z` are independent, symmetric and independent
    of the past, and whose `sigma_t` is any measurable function of past
    SQUARES. So a reading away from zero identifies a variance law that reads
    a signed return with memory beyond a day, and nothing else can produce it.

    The proof is three lines. `P^2 = Qm + 2 * sum_{i<j} r_{t-i} r_{t-j}`, and
    flipping the sign of one past innovation leaves every sigma and every
    future square unchanged while flipping each cross term, so
    `Cov(P^2, Qp) = Cov(Qm, Qp)`. `F^2 = Qp + 2 * sum_{i<j} r_{t+i} r_{t+j}`,
    and each future cross term has conditional mean zero given a past that
    contains `Qm`, so `Cov(Qm, F^2) = Cov(Qm, Qp)`. Stationarity makes the two
    denominators equal pairwise. Both correlations are then the same ratio.

    `tests/test_zumbach.py` checks that numerically rather than trusting it:
    a symmetric GARCH and a one-day-sign GJR read near zero, and a variance
    reading a six-scale signed sum reads an order of magnitude higher.

    ## What it is NOT

    Two different estimators carry this name in the research record. This is
    the one the theorem is about. The other,
    `corr(RV_past(20), r^2_next) - corr(r^2_t, RV_future(20))`, is a different
    quantity that did not discriminate the models it was tried on, and a band
    derived for one is not a band for the other.

    Returns None where the series is too short to form a window either side,
    for the reason `_correlation` does: an undefined reading must not arrive
    as a number.
    """
    if n < 1 or len(returns) < 2 * n + 3:
        return None
    a, b, c, d = _zumbach_terms(returns, n)
    forward = _correlation(a, b)
    reverse = _correlation(c, d)
    if forward is None or reverse is None:
        return None
    return forward - reverse


def zumbach_asymmetry_pooled(
    series: Sequence[Sequence[float]], n: int
) -> float | None:
    """`zumbach_asymmetry` across names, pooled by terms after standardising.

    Pooling is what makes the estimator usable at 252 sessions: it is a
    fourth-moment object and one name of one year does not carry it. Each name
    is divided by its OWN sample standard deviation first, so a volatile name
    does not dominate the pool through its scale, and the windows are formed
    WITHIN a name before pooling, never across the join between two.

    One difference from the bare form, which is a property rather than a
    defect: standardising also CENTRES each name, and centring changes the
    trend terms whenever a name's mean return is not zero. So on a drifting
    series the two functions differ, and neither is wrong. Pass a single name
    to this function rather than to `zumbach_asymmetry` if the centred
    convention is the one wanted.
    """
    a: list[float] = []
    b: list[float] = []
    c: list[float] = []
    d: list[float] = []
    for one in series:
        if n < 1 or len(one) < 2 * n + 3:
            continue
        unit = _unit_centred(one)
        if unit is None:
            continue
        wa, wb, wc, wd = _zumbach_terms(unit, n)
        a.extend(wa)
        b.extend(wb)
        c.extend(wc)
        d.extend(wd)
    forward = _correlation(a, b)
    reverse = _correlation(c, d)
    if forward is None or reverse is None:
        return None
    return forward - reverse


def _daily_series(bars: dict) -> dict[int, list[tuple[int, float, float]]]:
    """Group the bars table into per-instrument (day, close, volume) rows.

    The table is DAY-major, so consecutive rows are different instruments.
    Walking it without grouping computes returns between unrelated companies,
    and that mistake is worth naming because it fails silently and returns a
    plausible number.
    """
    grouped: dict[int, list[tuple[int, float, float]]] = {}
    for k in range(len(bars["close"])):
        grouped.setdefault(bars["instrument_id"][k], []).append(
            (bars["day"][k], bars["close"][k], bars["volume"][k])
        )
    return {i: sorted(rows) for i, rows in grouped.items()}


#: Sessions in a year, for annualising a per-session quantity.
TRADING_DAYS_PER_YEAR = 252


def _index_drift_pct(
    series: dict[int, list[tuple[int, float, float]]],
) -> float | None:
    """Annualised drift of the equal-weight index, in percent a year.

    The panel's FIRST MOMENT. Every other row here is a shape statistic --
    a spread, a fourth moment, an autocorrelation, a correlation -- and
    every one of them is invariant, or nearly so, to what the index level
    does. Nine of the fourteen are exactly invariant to adding a constant
    drift to every name on every day, because they centre their arguments
    before they measure them; the five that are not move by less than a
    tenth of their own seed noise under it. So the panel could be read
    fourteen for fourteen by a market that loses a fifth of its value in a
    year, and it was.

    # Which index, of the two

    An equal-weight index is a portfolio rebalanced to equal weights every
    day, so its daily return is the MEAN OF THE SIMPLE RETURNS across
    names, and this row reports the log of that summed over the window.
    That is the quantity a real index band would be stated in, since a
    published index return is a portfolio return.

    The other convention is the mean across names of the daily LOG return,
    which is what a decomposition has to use, because log returns are
    additive across time and across the terms of an identity while a
    portfolio return carries neither. Every attribution in this engine and
    every figure in the era that produced this row is in that convention,
    so a number from a decomposition and a number from this row are not
    the same quantity.

    The two differ by half the cross-sectional variance of the daily
    returns, which is Jensen's term and is positive whenever the names
    disperse at all. That is measured rather than argued. On
    `Universe.random(40, seed=111)` over 252 days, seeds 1 to 30:

    | arm | gap, points a year | half the variance | worst miss |
    |---|---|---|---|
    | pt-v16 | +2.067 median, sd 0.297 | +2.066 | 0.010 |
    | pt-v18 | +1.927 median, sd 0.236 | +1.919 | 0.011 |

    So predicting the gap as half the summed cross-sectional variance is
    accurate to about a hundredth of a point across sixty seed-years, and
    the residual is centred at -0.001 with a standard deviation of 0.005.
    An independent sweep regressed the gap on that variance and read a
    slope of 1.0081, an intercept of -0.0160 and an r squared of 0.9982,
    and read +2.064 on the shipped default over thirty seeds with the
    prediction accurate to a median of 0.011 and a worst case of 0.036.

    # Why a band forces the choice

    The gap is a variance rather than an offset anyone could subtract
    once. It moves with anything that moves dispersion: across thirty-one
    rosters it reads 1.914 with a standard deviation of 0.075, and across
    the arms of one era it runs from 1.494 to 1.952, because an arm that
    changes dispersion changes the gap by construction.

    So grading the log-mean row against a band derived from a real index
    would grade a different statistic whose offset from the band's own
    quantity varies with the preset under test. The row and its band have
    to name one convention.

    The choice then follows. A band in the log convention exists only by
    deriving it in that convention from real data, which means computing
    the cross-sectional variance of a real index's constituents over a
    matched window rather than taking a published index return, and nobody
    has done that derivation. Reporting the portfolio number needs no new
    derivation at all.

    # Which seed varies, and for which question

    Two seeds decide a run and they answer different questions. The market
    seed drives every draw the engine takes, and the universe seed decides
    the roster it takes them against. Every arm of the pt-v18 era varied
    the market seed over 1 to 30 on one roster, `Universe.random(40,
    seed=111)`, held fixed, which is the protocol every figure below was
    measured on.

    That roster opens 0.78 of a population standard deviation above fair
    value, so its LEVEL carries a draw as well as a model: one build reads
    -8.603 on it against +3.989 and +7.050 on rosters 204 and 209. Those
    three across-roster figures come from the era's roster sweep and are
    recorded in the design note `programme/index-architecture.md`, which
    also carries roster 111's +0.038 mean log deviation, the population
    mean of -0.002 and the across-roster standard deviation of 0.052 that
    the 0.78 is computed from.

    A paired difference between two arms on the held roster is a property
    of the model, because the roster's own draw is common to both arms and
    cancels. A level is a property of that roster, and a level that has to
    describe the model is measured by varying the universe seed instead.

    How much that costs is measured. A roster sweep puts roster 111 at
    1.12 standard deviations dearer than the population at the open, and
    index drift moves at about minus a hundred points per unit of opening
    mispricing, so this roster carries a handicap near six points a year.
    Across a hundred rosters, pt-v18 without the growth term reads a
    portfolio median of +1.55 a year with 65 of 101 rosters above zero,
    where roster 111 reads about minus five. So every level in the table
    below is that roster's and reads about six points worse than the model
    does. An earlier and smaller sample in the design note puts the same
    roster at 0.78 standard deviations, and the two have not been
    reconciled. The handicap was then measured directly, on 2026-09-04 at
    thirty seeds each: pt-v18 reads +1.974 in this row's convention with
    roster 111 held and +5.281 with the roster varying, 3.3 points, and
    pt-v16 reads -13.643 varying, so the direct figure is about half the
    six estimated from the sweep's slope. A level figure therefore carries
    its protocol as part of its value, and no two level figures compare
    unless both name theirs (`LEVEL_PROTOCOL`).

    # Which horizon

    The certified value is the first 252 sessions from the opening, and it
    is not the level a longer study sees. The era that produced this row
    was decomposed at both horizons in `programme/terms-by-horizon.md` in
    the design repository: six of its ten terms are one-off level shifts
    that work through the mispricing, and `s` is a stationary process at a
    60-day half-life, so a persistent injection settles at an offset that
    is bought once and does not compound, with four-year to one-year
    ratios from 0.11 to 0.36. The growth term compounds at 0.88, and the
    cycle clock is worth nothing inside a year and +1.8 a year over four.
    So the ranking of the terms inverts with the horizon, and a reader of
    a multi-year run has to take this row as the first-year figure it is.

    # Measured

    Every figure in the table is the LOG convention, on
    `Universe.random(40, seed=111)`, 252 days, seeds 1 to 30, which are the
    drift seeds rather than the ten panel seeds beside them. Taking the
    median over all forty rows of a file that carries both reads -18.525
    where the drift seeds read -18.344, so the two sets are separated
    wherever a figure is quoted.

    | build | median | min | max | seeds above zero |
    |---|---|---|---|---|
    | pt-v16 at `df0fe62` | -22.155 | -43.797 | -16.360 | 0 of 30 |
    | pt-v16, reconciled generator | -18.344 | -39.896 | -12.526 | 0 of 30 |
    | pt-v18, the finished era | -2.304 | -19.213 | +5.628 | 9 of 30 |

    In this row's own convention the last of those reads -0.340, with a
    minimum of -16.827, a maximum of +7.686 and 13 of 30 seeds above zero,
    against pt-v16's -16.150 and 0 of 30. The two conventions are 1.9 to
    2.1 points apart on this roster and both are quoted so that neither
    can be read as the other.

    The derivation of the first row is
    `tradefloor-design/programme/index-drift-investigation.md`, and the
    terms it names are a down tilt in the market factor, a rising rate
    path, a negative jump mean, an asymmetric stop cascade and the
    roster's own opening condition. The pt-v18 era gives each of those
    back and adds a growth term; its own figures are in the design note
    `programme/index-architecture.md`, and they are in the log convention
    too, so about 1.9 points is added to each of them to read them in this
    row's convention.

    # There is no band, deliberately

    This row is REPORTING ONLY. It is absent from `REAL_MARKETS`, so it has
    no verdict, no pass and no fail, and `envelope` never sees it. The
    reason is recorded as data in `REPORTING_ONLY` and printed by `report`.

    A band for it is OUTSTANDING rather than omitted. The fourteen graded
    rows are shape statistics with published real-market analogues measured
    at this panel's own method. A first-moment band would have to be
    derived from a real index over a matched window, and that derivation
    has not been done here. Shipping a plausible-looking band instead would
    grade every future preset against a number nobody measured, which is
    the unprovenanced-band defect this module already corrected once, in
    2026-08 (see `REAL_MARKETS_PROVENANCE`). A number with no band is
    honest, and a band with no derivation grades against nothing. What this
    row's convention change buys is that the number is already in the units
    such a band would be stated in, so deriving one later needs no
    translation.

    # Method

    For each day, the mean across names of the simple return of the close,
    turned into a log return and summed over the window, divided by the
    number of days that carried a return, times 252. The mean of the simple
    returns is the daily rebalance: every name is held at equal weight at
    every open, whatever it did the day before.

    Two choices worth naming, because both are the difference between this
    row and a flattering one.

    **Every name counts, including a short-lived one.** `min_observations`
    filters the other rows, and does not filter this one. A delisted name's
    losses are exactly what an index drift has to carry; dropping it is
    survivorship bias, which is the classic way to measure an index level
    wrong. A day on which a name has no return contributes the names that
    do have one, which is what an index does when a constituent leaves.

    **A gap in a name's bars is spanned, not dropped.** A return is formed
    between consecutive RECORDED rows for that name and attributed to the
    later day, so the sum over the window is exact even where a name is
    missing days. It is the sum that this statistic reports.
    """
    by_day: dict[int, list[float]] = {}
    for rows in series.values():
        for k in range(1, len(rows)):
            previous, close = rows[k - 1][1], rows[k][1]
            if previous > 0 and close > 0:
                # The GROSS return, so the mean below is the portfolio's
                # and not a name's. Taking logs first and averaging those
                # gives the other convention, which is 1.9 points lower on
                # the roster this row is quoted on.
                by_day.setdefault(rows[k][0], []).append(close / previous)
    if not by_day:
        return None
    daily = [math.log(statistics.mean(values)) for values in by_day.values()]
    return sum(daily) / len(daily) * TRADING_DAYS_PER_YEAR * 100.0


#: Window, in sessions, for the correlation-persistence diagnostic. The
#: real reference (real-corr-persistence.json) was measured at 21.
CORR_PERSISTENCE_WINDOW = 21


def _dependence(
    series: dict[int, list[tuple[int, float, float]]],
    min_observations: int,
    sectors: Mapping[int, str] | None = None,
) -> dict[str, Any]:
    """The statistics for how things move together, from grouped bars.

    Median across instruments, like the autocorrelations above and for the
    same reason. The exceptions are the cross-sectional statistics, which are
    inherently pairwise and are means over pairs.

    Three of them condition the pairwise correlation on something, and exist
    because the unconditional mean over all pairs is blind to the structure
    that matters (CORRELATION-REVIEW-2026-08-25.md §1): a single scalar cannot
    see whether correlation is higher on down days than up days, whether
    same-sector pairs co-move more than cross-sector pairs, or whether either
    varies in time. A search cannot preserve what it cannot see, so these are
    measured BEFORE any mechanism that would move them is built.

    ``corr_asymmetry`` is mean pairwise correlation on days the equal-weight
    market return is below minus one standard deviation, minus the same on
    days above plus one. Positive in real markets: downside exceedance
    correlation exceeds upside. ``corr_asymmetry_lagged`` conditions on the
    PREVIOUS day's market return instead, which separates a same-day signed
    factor loading from the lagged volatility route. ``sector_excess_corr``
    is mean same-sector pairwise correlation minus mean cross-sector, and is
    None when no sector labels are supplied.
    """
    returns: dict[int, list[float]] = {}
    volumes: dict[int, list[float]] = {}
    for i, rows in series.items():
        instrument_returns: list[float] = []
        instrument_volumes: list[float] = []
        for (_, previous, _), (_, current, volume) in zip(rows, rows[1:]):
            if previous > 0 and current > 0:
                instrument_returns.append(math.log(current / previous))
                instrument_volumes.append(volume)
        if len(instrument_returns) >= min_observations:
            returns[i] = instrument_returns
            volumes[i] = instrument_volumes

    keys = sorted(returns)
    common = min((len(returns[i]) for i in keys), default=0)

    # Cross-sectional correlation over every pair, on a common window, so a
    # pair is compared over the same days rather than over whatever length
    # each series happened to reach.
    pairwise: list[float] = []
    same_sector: list[float] = []
    cross_sector: list[float] = []
    corr_asymmetry: float | None = None
    corr_asymmetry_lagged: float | None = None
    corr_persistence_acf1: float | None = None
    if len(keys) >= 2 and common >= 3:
        unit = {i: _unit_centred(returns[i][:common]) for i in keys}
        for position, a in enumerate(keys):
            if unit[a] is None:
                continue
            for b in keys[position + 1:]:
                if unit[b] is None:
                    continue
                rho = sum(x * y for x, y in zip(unit[a], unit[b]))
                pairwise.append(rho)
                if sectors is not None and a in sectors and b in sectors:
                    (same_sector if sectors[a] == sectors[b] else cross_sector).append(rho)

        # Conditional correlation: the same pairwise mean, on a subset of
        # days chosen by the standardised equal-weight market return. The
        # threshold is one standard deviation each side, so at 252 days each
        # tail holds roughly forty sessions; expect a wide across-seed spread
        # and read the band beside its seed-sd rather than as a point.
        live = [i for i in keys if unit[i] is not None]
        if len(live) >= 2:
            market = [statistics.fmean(returns[i][k] for i in live) for k in range(common)]
            m_mean = statistics.fmean(market)
            m_sd = statistics.pstdev(market)

            def conditional(select_days: list[int]) -> float | None:
                if len(select_days) < 3:
                    return None
                sub = {i: _unit_centred([returns[i][k] for k in select_days]) for i in live}
                rhos = []
                for position, a in enumerate(live):
                    if sub[a] is None:
                        continue
                    for b in live[position + 1:]:
                        if sub[b] is None:
                            continue
                        rhos.append(sum(x * y for x, y in zip(sub[a], sub[b])))
                return statistics.fmean(rhos) if rhos else None

            if m_sd > 0:
                z = [(m - m_mean) / m_sd for m in market]
                down = conditional([k for k in range(common) if z[k] < -1.0])
                up = conditional([k for k in range(common) if z[k] > 1.0])
                if down is not None and up is not None:
                    corr_asymmetry = down - up
                # Lagged: condition day k on the market return of day k-1.
                down_l = conditional([k for k in range(1, common) if z[k - 1] < -1.0])
                up_l = conditional([k for k in range(1, common) if z[k - 1] > 1.0])
                if down_l is not None and up_l is not None:
                    corr_asymmetry_lagged = down_l - up_l

            # Correlation PERSISTENCE: mean pairwise correlation on
            # non-overlapping 21-day windows, then the lag-1 autocorrelation
            # of that series. Real markets on the 40-name reference roster
            # read 0.388 with a half-life near fifteen days
            # (tradefloor-design/real-corr-persistence.json, 126 windows).
            # Non-overlapping windows on purpose; overlapping ones
            # manufacture persistence out of shared days.
            #
            # ELEVEN windows in a 252-day run, not twelve: the returns above
            # are differences of prices, so 252 bars give 251 returns and
            # `251 // 21` whole sub-windows. `facts.sub_window_count` is that
            # arithmetic and `facts.persistence_null` needs it, because the
            # estimator's own null at eleven draws is -0.09 rather than zero.
            # The comment here read "twelve" for two eras.
            #
            # This comment also read "a model whose correlation is a lookup
            # on today's VIX reads near zero here: the cross-section
            # decouples the tick VIX falls". That is REFUTED for this model:
            # the arm with the VIX pinned at its anchor every day reads +0.15
            # on thirty seeds, higher than the free arm's +0.13, and the
            # paired difference across the same seeds is -0.006 with a t of
            # -0.4 (tradefloor-design/programme/band-form-design.md 5f). So
            # the row certifies that correlation varies WITH MEMORY and says
            # nothing about which channel carries it; attributing it needs a
            # constant-factor-variance arm, whose dial is not identified.
            n_windows = common // CORR_PERSISTENCE_WINDOW
            if n_windows >= 6:
                per_window = []
                for w in range(n_windows):
                    days_w = list(range(w * CORR_PERSISTENCE_WINDOW,
                                        (w + 1) * CORR_PERSISTENCE_WINDOW))
                    value = conditional(days_w)
                    if value is not None:
                        per_window.append(value)
                if len(per_window) >= 6:
                    corr_persistence_acf1 = _autocorrelation(per_window, 1)

    volume_corr: list[float | None] = []
    leverage: list[float | None] = []
    volume_change_acf: list[float | None] = []
    for i in keys:
        instrument_returns = returns[i]
        instrument_volumes = volumes[i]
        absolute = [abs(x) for x in instrument_returns]

        volume_corr.append(_correlation(absolute, instrument_volumes))

        # Leverage: today's SIGNED return against tomorrow's absolute return.
        # Negative in real equities, where bad news raises volatility more
        # than good news of the same size does.
        leverage.append(_correlation(instrument_returns[:-1], absolute[1:]))

        # Volume CHANGES, not levels. The level autocorrelation is dominated
        # by a slowly varying level and reads high whatever the dynamics, so
        # it cannot tell one model of volume from another. The change
        # autocorrelation can: real markets, whose volume shocks partly
        # persist, read about -0.25 here, and a smooth level plus PURELY
        # independent daily noise drives it toward -0.5.
        changes = [
            later / earlier - 1.0
            for earlier, later in zip(instrument_volumes, instrument_volumes[1:])
            if earlier > 0
        ]
        volume_change_acf.append(_correlation(changes[:-1], changes[1:]))

    def median_of(values: Sequence[float | None]) -> float | None:
        present = [x for x in values if x is not None]
        return statistics.median(present) if present else None

    return {
        "dependence_instruments": len(keys),
        "dependence_observations": common,
        "cross_sectional_corr": statistics.fmean(pairwise) if pairwise else None,
        "corr_asymmetry": corr_asymmetry,
        "corr_asymmetry_lagged": corr_asymmetry_lagged,
        "corr_persistence_acf1": corr_persistence_acf1,
        "sector_excess_corr": (
            statistics.fmean(same_sector) - statistics.fmean(cross_sector)
            if same_sector and cross_sector else None
        ),
        "volume_abs_return_corr": median_of(volume_corr),
        "leverage_effect": median_of(leverage),
        "volume_change_acf1": median_of(volume_change_acf),
    }


def _verdict(value: float, low: float, high: float) -> str:
    """How one statistic reads against its band, in words.

    A band whose top is at or below zero needs its own wording. A leverage
    effect of +0.05 against a band of -0.16 to 0.00 is numerically above
    the band and semantically absent (reversed, even), so reporting it as
    "too high" would state the opposite of the finding; likewise a
    volume-change autocorrelation of -0.45 against -0.32 to -0.20 is
    numerically below its band and semantically mean-reversion that is too
    STRONG, not "too low".
    """
    if low <= value <= high:
        return "matches"
    if high <= 0:
        return "too weak" if value > high else "too strong"
    return "too high" if value > high else "too low"


def band_distance(value: float, low: float, high: float) -> float:
    """How far a statistic sits outside its band: max(0, low-value, value-high).

    Zero anywhere inside the band, including on either boundary, and the
    distance to the NEAREST edge outside it. Defined here, next to `_verdict`,
    because the two share the hazard: on a band that sits at or below zero --
    leverage, -0.16 to 0.00, or volume-change acf, -0.32 to -0.20 -- naive
    handling silently inverts. `_verdict` solves the wording half (above such
    a band is "too weak", and the improving direction is DOWN); this solves
    the arithmetic half. The specific failure this form exists to prevent is
    the one-sided max(0, value - high), under which a leverage effect of
    -0.5 -- a large OVERSHOOT past the strong edge -- would read as
    satisfying the band. The two-sided form charges it low - value = 0.34,
    on the same footing as the absent-effect exit on the weak side.

    This is a distance on raw signed values; it does not know which side of a
    band is "weak", and does not need to. Direction and wording stay
    `_verdict`'s job.
    """
    return max(0.0, low - value, value - high)


def measure(
    *,
    seed: int,
    universe: Sequence[Instrument],
    days: int = 252,
    burn: int = 0,
    macro: Macro | None = None,
    scenario: Any = None,
    min_observations: int = 30,
    model: str | ModelParams | None = None,
) -> dict[str, Any]:
    """Run a market and report its statistical properties.

    Ten statistics against `REAL_MARKETS`: two marginal, describing one
    return series on its own, and eight dependence, describing how things
    move together -- across time, across stocks, with volume, and
    asymmetrically with their own sign. The split is the finding, so
    `report` prints it in two sections.

    ``model`` selects the coefficient set the market runs, either a preset
    name or a :class:`tradefloor.ModelParams`, defaulting to the shipped preset. This
    is the seam the calibration search evaluates through: the panel at a
    candidate vector is ``measure(model=candidate)``, no rebuild. The result
    carries ``model_fingerprint`` beside ``universe_fingerprint`` for the
    same reason that field exists, because a realism claim is only checkable
    against the exact model it measured, and ``custom-XXXXXXXX`` can never
    present as the shipped one.

    Cross-sectional statistics are pooled where pooling is meaningful and taken
    as a MEDIAN across instruments where it is not. Autocorrelation is the
    latter: pooling returns from sixty instruments into one series would splice
    sixty unrelated histories end to end and measure the joins. The one
    exception is `cross_sectional_corr`, which is inherently pairwise and is a
    mean over every pair.

    A dependence statistic that cannot be measured on the run -- pairwise
    correlation over a single instrument, say -- comes back as None rather than
    as zero, and `compare_to_real_markets` omits it.

    # `burn`: sessions run before the window, and why a pinned run needs them

    ``burn`` sessions are traded and thrown away before day zero of the
    recorded window. The run is one run -- same engine, same streams, same
    scenario -- and the panel is measured on the last ``days`` of it.

    It exists because a PINNED scenario has a transient and the panel has no
    way to see it. ``Scenario().hold(vix=65)`` fixes the VIX from day zero,
    but the factor variance opens at the preset's unconditional level and
    walks to the pinned target at the preset's own
    ``alpha + beta + gamma/2``; a whole-window statistic then averages that
    walk. `measurement-integrity.md` 1.1 measured it on the crisis lever:
    the low pin settles DOWN and the high pin UP, so the ratio of the two is
    biased low by four to eight per cent, by an amount that is a property of
    the preset and not of the lever. A burn is the only reading under which
    "volatility at a held VIX of 65" is a number about the model rather than
    about how long the window was.

    THE SCENARIO'S CLOCK STARTS AT THE WINDOW, NOT AT THE BURN. The burn
    runs at days ``-burn .. -1``, so a ``hold`` -- constant from day zero and
    before it -- is in force for the whole burn, which is what makes the burn
    settle anything; and an intervention at ``at: 50`` still fires fifty days
    into the RECORDED window, where its note says it does. That is the same
    reading :meth:`Scenario.apply` already documents for a checkpoint
    resume: day zero is where the experiment starts, not where the engine
    did.

    A burn is NOT the market-side warm-up. ``market_burn_in_sessions``
    settles the same states deterministically and without a draw, at the
    level the close is about to read; it ships at 0.0 and it is a property
    of the MODEL, so turning it on moves the coefficient fingerprint. This
    argument is a property of the MEASUREMENT and moves nothing but the
    window.
    """
    if days < 2:
        raise ValidationError("days must be at least 2 to have a return")
    if burn < 0:
        raise ValidationError(
            f"burn is a number of sessions to discard before the window, "
            f"got {burn}")

    engine = Engine(seed=seed, universe=universe, macro_state=macro,
                    model=model)
    for day in range(-burn, days):
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        # Record before the close: the close advances the macro chain, and
        # the macro row must carry the values the day traded under.
        #
        # The burn is traded and not recorded, so every statistic below --
        # the bars table, the macro table, the fear buckets and the VIX's
        # own persistence -- reads the window and nothing before it.
        if day >= 0:
            engine.record(day)
        engine.close_market()

    facts: dict[str, Any] = {
        "seed": seed,
        # Which market these statistics describe. A realism claim without it
        # is unfalsifiable: "kurtosis is +4.8" is only checkable against the
        # roster it was measured on, and tickers do not identify a roster.
        "universe_fingerprint": fingerprint_of(universe),
        # And which MODEL produced them. The panel is the calibration
        # search's objective, so a panel row that does not name its
        # coefficient set carries the ambiguity the fingerprint exists to
        # remove.
        "model_fingerprint": engine.model_fingerprint,
        "days": days,
        # The window's own provenance, and it travels with the panel for the
        # reason the two fingerprints do: a number read over a settled window
        # and a number read from a cold open are different measurements, and
        # a record that does not say which cannot be compared with either.
        # Always present, 0 where nothing was discarded, so a reader never
        # has to decide whether a missing key means zero or means unstated.
        "burn": burn,
    }
    bars = engine.bars(grain="day")
    facts.update(panel_statistics(bars, universe,
                                  min_observations=min_observations))
    facts.update(fear_statistics(engine.bars(grain="day"), engine.macro_table(), universe))

    # The VIX's own persistence. Section 1.5's ruler was derived, recorded in
    # RULERS_BY_HORIZON and read by nothing: every harness that wanted this
    # row rebuilt the series for itself, which is what `vix_levels` was
    # written to end. The series is already recorded above -- `record(day)`
    # once per session -- so the row costs one lag-one autocorrelation and
    # no extra simulation.
    #
    # ONE RUN, so this is `level_ar1` debiased at the run length and NOT
    # `median_level_ar1`, which is the median ACROSS runs. A harness takes
    # the median of these across seeds and lands on the same number, because
    # `debias_ar1` is affine and increasing.
    #
    # `level_ar1` REFUSES a series with no lag-one pair and a constant one,
    # and neither is a VIX with no persistence. The row is then OMITTED with
    # its reason rather than defaulted: the scoring rule already reports a
    # row missing from a record as blind, and a fabricated 0.0 inside a
    # median across seeds would move it without announcing itself.
    facts.update(persistence_statistics(engine.macro_table(), days=days))
    return facts


#: The session-return thresholds of the fear rows, percent.
FEAR_BUCKETS = {"fear_gauge_dn1": -1.0, "fear_gauge_dn3": -3.0, "fear_gauge_dn5": -5.0}

#: The threshold of the index tail rows, percent. The same bucket the fear
#: rows condition on, so the count row and the response rows are counting and
#: answering the same sessions.
INDEX_TAIL_THRESHOLD = 3.0


def _index_session_returns(
    bars: Any,
    universe: Sequence[Instrument],
) -> list[tuple[int, float]]:
    """The cap-weighted index session returns of a recorded run, percent.

    One `(day, return)` per pair of consecutive recorded days, the level
    being the roster's shares outstanding times the close, which is the
    series `fear_statistics` buckets and the series the index tail rows
    count. Factored out of `fear_statistics` so that the rows conditioned
    on a session and the rows COUNTING those sessions cannot drift into two
    definitions of "a session at -3 percent": they read the same list.

    It is the CAP-WEIGHTED CLOSE form, this library's own, and not the
    snapshot form the programme harnesses use (price against
    `previous_close`, weighted by market cap). The two agree to a
    correlation of +0.9998 over 2,520 sessions and differ where a jump
    lands between the last tick and the close, so a programme figure and a
    panel figure can differ in the third decimal and a note comparing them
    has to say which form each is.
    """
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "_index_session_returns reads Arrow tables and needs pyarrow. "
            "Install it with: pip install tradefloor[arrow]") from exc
    b = pa.table(bars).to_pydict()
    shares = [float(inst.shares_outstanding) for inst in universe]
    level: dict[int, float] = {}
    for day, ident, close in zip(b["day"], b["instrument_id"], b["close"]):
        if close is None:
            continue
        level[int(day)] = level.get(int(day), 0.0) + float(close) * shares[int(ident)]
    days = sorted(level)
    return [(day, (level[day] / level[prev] - 1.0) * 100.0)
            for prev, day in zip(days, days[1:]) if level[prev] > 0.0]


def fear_statistics(
    bars: Any,
    macro: Any,
    universe: Sequence[Instrument],
) -> dict[str, Any]:
    """The fear gauge rows of a recorded run, from its daily bars and macro table.

    For each session the cap-weighted close-to-close return of the roster,
    in percent, with the weights the roster's shares outstanding times the
    close; the alternative, the engine's own last-tick return against
    `previous_close`, agrees with it to a correlation of +0.9998 over 2,520
    sessions and differs only where a jump lands between them, and this is
    the one stated. The gauge change answering session d is the volatility
    index after day d's macro step minus the index after day d-1's.

    # The alignment, which the obvious implementation gets wrong

    `measure` records each day BEFORE `close_market`, deliberately, so the
    macro row for day d carries the values the session traded under. The
    consequence, proved on a five-day run against the gauge read after each
    close: macro row d holds the gauge the session OPENED with, so the
    change that answers session d is row d+1 minus row d, and the last
    recorded session has no answer in the table. Differencing the recorded
    column and bucketing by the same row's return pairs every session with
    the answer to the session before it, which is the off-by-one that made
    a shadow record report this gauge moving against the real one for
    months. `tests/test_facts.py` pins the convention with two
    correlations that swap under the wrong pairing.

    Rows: `fear_gauge_dn1`, the median change over sessions at or below -1
    percent, a per-run row; `fear_gauge_dn3`, the same at or below -3
    percent, whose per-run value is None on a run holding no such session
    and whose graded value is pooled across runs (`AGGREGATE`); each with
    `_sessions` beside it and, for the pooled row, `_samples`;
    `fear_gauge_dn5` and `fear_gauge_up1` as reported diagnostics.

    And the INDEX TAIL rows, which count the sessions the rows above
    condition on rather than answering them: `index_tail_dn3_pct`, the
    share of sessions at or below -3 percent, graded as a POOLED RATE over
    the certification seeds with `index_tail_dn3_hits` and
    `index_tail_dn3_sessions` beside it; `index_tail_up3_pct` with its own
    hit count, and `index_excess_kurtosis`, both reported and not graded
    (`REPORTING_ONLY`). They are computed here, from the same index level,
    because a count row measured somewhere else would be a second
    definition of the session a fear row already buckets.

    The tail counts every session return and the fear rows score one fewer:
    the last recorded session has no gauge answer, by the alignment above.
    So `index_tail_dn3_hits >= fear_gauge_dn3_sessions` on every run with a
    difference of at most one, which `tests/test_facts.py` asserts.
    """
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "fear_statistics reads Arrow tables and needs pyarrow. Install "
            "it with: pip install tradefloor[arrow]") from exc
    m = pa.table(macro).to_pydict()
    index = _index_session_returns(bars, universe)
    gauge = {int(day): float(v) for day, v in zip(m["day"], m["vix"]) if v is not None}
    pairs: list[tuple[float, float]] = []
    for day, ret in index:
        if day + 1 not in gauge or day not in gauge:
            continue
        pairs.append((ret, gauge[day + 1] - gauge[day]))
    out: dict[str, Any] = {"fear_sessions_scored": len(pairs)}
    for key, threshold in FEAR_BUCKETS.items():
        samples = [c for r, c in pairs if r <= threshold]
        out[key] = statistics.median(samples) if samples else None
        out[key + "_sessions"] = len(samples)
        if AGGREGATE.get(key) == "pooled" or key == "fear_gauge_dn5":
            out[key + "_samples"] = samples
    up = [c for r, c in pairs if r >= 1.0]
    out["fear_gauge_up1"] = statistics.median(up) if up else None
    out["fear_gauge_up1_sessions"] = len(up)
    out.update(index_tail_statistics(index))
    return out


def index_tail_statistics(
    index: Sequence[tuple[int, float]],
) -> dict[str, Any]:
    """The index tail rows from the index session returns of one run.

    Separated from `fear_statistics` so a test can feed it a series and read
    the rows without running a market, and so the arithmetic of the graded
    row is one short function rather than three lines inside a longer one.

    `index_tail_dn3_pct` is `100 * hits / sessions` on ONE run, which is not
    the graded value: the graded value pools the counts over every
    certification seed (`AGGREGATE`, `aggregate_panels`). The per-run rate
    is carried anyway because a mixture is a property of the seeds and the
    certificate reports the share of seeds at zero beside the rate.
    """
    returns = [r for _, r in index]
    n = len(returns)
    out: dict[str, Any] = {}
    for key, hit in (("index_tail_dn3", lambda r: r <= -INDEX_TAIL_THRESHOLD),
                     ("index_tail_up3", lambda r: r >= INDEX_TAIL_THRESHOLD)):
        hits = sum(1 for r in returns if hit(r))
        out[key + "_hits"] = hits
        out[key + "_sessions"] = n
        out[key + "_pct"] = 100.0 * hits / n if n else None
    out["index_excess_kurtosis"] = _excess_kurtosis(returns)
    return out


def _excess_kurtosis(values: Sequence[float]) -> float | None:
    """The population fourth standardised moment less three, or None.

    `panel_statistics`'s form, on the index's own return series rather than
    on the pooled per-name one. None where the series is too short to
    standardise or does not move at all: zero is a real reading of a
    Gaussian series and would be a false pass on an empty one.
    """
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values)
    if sd == 0:
        return None
    standard = [(x - mean) / sd for x in values]
    return sum(x ** 4 for x in standard) / len(standard) - 3.0


def panel_statistics(
    table: Any,
    universe: Sequence[Instrument],
    *,
    min_observations: int = 30,
) -> dict[str, Any]:
    """The panel statistics of a recorded run, from its daily bars.

    What :func:`measure` reports after it has run the market, as a
    function of the bars table alone, so a run recorded elsewhere (a
    :class:`~tradefloor.counterfactual.World` under a draw surgery, say)
    gets the same numbers from the same code. ``table`` is
    ``Engine.bars(grain="day")`` or anything ``pyarrow.table`` reads.

    The identity fields (seed, fingerprints, days) are ``measure``'s to
    add: this function does not know what produced the table, and says
    nothing it cannot read off it.
    """
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "tradefloor.facts.panel_statistics reads the daily bars table and needs "
            "pyarrow. Install it with: pip install tradefloor[arrow]"
        ) from exc

    bars = pa.table(table).to_pydict()
    series = _daily_series(bars)
    count = len(universe)

    pooled: list[float] = []
    return_acf1: list[float] = []
    abs_acf1: list[float] = []
    abs_acf5: list[float] = []
    abs_acf20: list[float] = []
    for i in range(count):
        returns = _log_returns([row[1] for row in series.get(i, ())])
        if len(returns) < min_observations:
            continue
        pooled.extend(returns)
        absolute = [abs(x) for x in returns]
        return_acf1.append(_autocorrelation(returns, 1))
        abs_acf1.append(_autocorrelation(absolute, 1))
        abs_acf5.append(_autocorrelation(absolute, 5))
        abs_acf20.append(_autocorrelation(absolute, 20))

    if not pooled:
        raise ValidationError(
            f"no instrument produced {min_observations} daily returns; "
            "run for more days"
        )

    mean = statistics.mean(pooled)
    sd = statistics.pstdev(pooled)
    standard = [(x - mean) / sd for x in pooled] if sd else [0.0] * len(pooled)

    facts: dict[str, Any] = {
        "instruments": count,
        "observations": len(pooled),
        "annualised_vol_pct": sd * math.sqrt(252) * 100.0,
        "excess_kurtosis": sum(x ** 4 for x in standard) / len(standard) - 3.0,
        "skew": sum(x ** 3 for x in standard) / len(standard),
        # Medians across instruments, not a pooled series. Splicing sixty
        # histories end to end would measure the joins.
        "return_acf1": statistics.median(return_acf1),
        "abs_return_acf1": statistics.median(abs_acf1),
        "abs_return_acf5": statistics.median(abs_acf5),
        "abs_return_acf20": statistics.median(abs_acf20),
        # The panel's one first-moment row, reported and NOT graded. It is
        # built from `series` rather than from `pooled`, because `pooled`
        # has already dropped every name under `min_observations` and an
        # index drift that drops its losers is not one. See
        # `_index_drift_pct`.
        "index_drift_pct": _index_drift_pct(series),
    }
    # The four dependence statistics carry the same keys whether or not they
    # could be measured, so a caller reading the result does not have to test
    # for their presence as well as for their value.
    facts.update(_dependence(
        series, min_observations,
        sectors={i: inst.sector for i, inst in enumerate(universe)},
    ))
    return facts


# --------------------------------------------------------------------------
# The mechanism gate: the second half of one instrument
# --------------------------------------------------------------------------

#: How tolerant `BAND_RULE` is of a CORRECT reading, per window count.
#:
#: MEASURED, with its residual, because the rule's false-alarm rate is a
#: property of the rule and not a matter of preference: draw `n` standard
#: normal windows, build `[min - s, max + s]` with `trimmed_sd` exactly as
#: `shared_rule` does, and ask how often a fresh draw from the same law
#: falls outside. Nine windows -- the count every shipped 252-bar band rests
#: on -- lets a correct reading out 6.49 per cent of the time.
#: `band_rule_false_alarm` re-derives these live and
#: `tests/test_mechanism_gate.py` holds the table to a re-derivation rather
#: than to itself.
#:
#: This is the number the mechanism gate's cut is taken from (`sign_cut`),
#: which is what keeps the cut from being chosen: the mechanism half of the
#: instrument is exactly as tolerant of a correct model as the fidelity half
#: already is. A row graded against a 504-day band is graded at THAT band's
#: tolerance, which is two and a half times looser because five windows
#: support it, and copying the 252 cut across would silently tighten it.
BAND_RULE_TOLERANCE: dict[int, float] = {
    4: 0.23998,
    5: 0.16800,
    7: 0.09730,
    9: 0.06486,
}

BAND_RULE_TOLERANCE_PROVENANCE = {
    "kind": "measured",
    "claim": "the probability that a fresh reading from the same law falls "
             "outside the band BAND_RULE builds from n readings of it",
    "estimator": "facts.band_rule_false_alarm: n + 1 iid standard normal "
                 "draws, the band from the first n by facts.shared_rule "
                 "(unrounded, since rounding is per-row and outward), the "
                 "verdict on the last",
    "draws": 200_000,
    "seed": 20260905,
    "residual": "binomial standard error sqrt(p(1-p)/draws): 0.00055 at "
                "nine windows, 0.00066 at seven, 0.00084 at five, 0.00095 "
                "at four",
    "window_counts": "nine is the non-crisis count behind every 252-bar "
                     "band (REAL_MARKETS_WINDOWS, ten windows less the "
                     "crisis one); five is the count behind the 504-bar "
                     "bands and four behind the 504-bar persistence band "
                     "(REAL_MARKETS_504)",
    "source": "tradefloor-design/programme/band-form-design.md section 4 "
              "and results/bandform-measured.md follow-up 7, 2026-09-05",
    "unrounded_note": "the rate is measured on the UNROUNDED band. Outward "
                      "rounding widens a shipped band by up to one quantum "
                      "an edge, so a shipped band is at most this tolerant "
                      "and the cut derived from it is at most this strict",
}

#: The band RULES this project has built a band with, by name, because a
#: tolerance belongs to a rule and not to a window count.
#:
#: `spread` is `shared_rule`, `[min - s, max + s]`, and its false-alarm rate
#: FOLLOWS the window count -- 0.06486 at nine, 0.16800 at five, 0.23998 at
#: four, and 0.00688 at thirty-five. `BAND_RULE_TOLERANCE` is that rule's
#: table and only that rule's.
#:
#: `fixed` is `median +/- t(n) * trimmed_sd` with `t(n)` SOLVED so the rate
#: is the same at every window count. It is the rule the universal band of
#: `certification-bands.md` section 14 is built with, adopted under
#: `ruling-the-ruler-is-the-universal-band`, and under it the rate is an
#: input rather than an output: the window count improves the ESTIMATE and
#: does not change the size of the test.
#:
#: The two are named rather than switched on a flag because the defect this
#: distinction exists to prevent is a function whose name promises one rule
#: and whose body computes another. `band_rule_tolerance` takes the rule.
BAND_RULES = ("spread", "fixed")

#: The rate the `fixed` rule holds at every window count, which is the
#: project's own nine-window design point, `BAND_RULE_TOLERANCE[9]`.
#:
#: DERIVED, and the derivation is the rule's definition rather than a
#: measurement: `t(n)` is solved to make the rate this, so the rate is this
#: by construction at any n. It is the same number the shipped 252-day bands
#: have carried since 2026-09-05 and it was not chosen today -- which is
#: what keeps the mechanism gate's cut from being fitted to a preset when
#: the band under it moves.
BAND_RULE_FIXED_TOLERANCE: float = 0.06486

#: `t(n)` for the `fixed` rule: the multiplier that puts the rate at
#: `BAND_RULE_FIXED_TOLERANCE` on `n` windows.
#:
#: MEASURED, by the same Monte Carlo `band_rule_false_alarm` runs and under
#: the same null -- `n + 1` iid standard normals, the band from the first
#: `n`, the verdict on the last -- at 200,000 draws and seed 20260905.
#: `band_rule_fixed_false_alarm` re-derives any row of it.
#:
#: t(5) = 5.04 is the measurement that says the shipped 504-day bands are
#: not the 252-day test: reaching the 252-day rate on five windows takes
#: five standard deviations, because a five-window trimmed sd is a scale
#: estimate on three kept points.
BAND_RULE_FIXED_MULTIPLIER: dict[int, float] = {
    5: 5.039474,
    9: 2.982334,
    16: 2.417688,
    35: 2.111347,
}

BAND_RULE_FIXED_MULTIPLIER_PROVENANCE = {
    "kind": "measured",
    "claim": "the multiplier t(n) at which median +/- t * trimmed_sd over n "
             "readings leaves a fresh reading from the same law outside "
             "BAND_RULE_FIXED_TOLERANCE of the time",
    "estimator": "facts.band_rule_fixed_false_alarm, solved for t: n + 1 iid "
                 "standard normal draws, the band from the first n as the "
                 "median plus and minus t times facts.trimmed_sd "
                 "(unrounded), the verdict on the last",
    "draws": 200_000,
    "seed": 20260905,
    "residual": "binomial standard error sqrt(p(1-p)/draws) = 0.00055 at "
                "every row. CHECKED OUT OF SAMPLE, because a solve at its "
                "own seed is in-sample by construction: at seed 20260914 "
                "the achieved rates are 0.06545 at n=9, 0.06535 at n=16 and "
                "0.06512 at n=35, and at seed 20260906 they are 0.06451, "
                "0.06522 and 0.06494 -- every one inside 1.1 standard "
                "errors of the target, on two fresh seeds derived "
                "independently",
    "instrument_check": "the same harness re-measures the spread rule at 4, "
                        "5, 7 and 9 windows and reproduces "
                        "BAND_RULE_TOLERANCE at a worst residual of 1e-05, "
                        "which is what earns it the right to extend the "
                        "rule to counts nobody has shipped",
    "source": "tradefloor-design/programme/results/certification-bands.md "
              "section 13.1 and results/band-basis-sweep.md section 2, "
              "2026-09-14",
    "why_not_extend_BAND_RULE_TOLERANCE": "because that table measures the "
        "SPREAD rule, whose rate at 35 windows is 0.00688 and at 16 is "
        "0.02535. Adding those keys would leave a table whose name reads "
        "like the band's tolerance and whose body is a different band's",
}

#: The universal panel's per-window readings, as data.
#:
#: WHY THIS TABLE EXISTS. Until it landed, `REAL_MARKETS_UNIVERSAL` and
#: `REAL_MARKETS_UNIVERSAL_504` were fifty-six typed edges with nothing in
#: this package underneath them. The readings lived in the design
#: repository's `whole-tape/panel32.json` and were never committed here, so
#: no test could tell a measured edge from a mistyped one, and a typo in
#: either table would have shipped green. That is the state `fear_gauge_dn1`
#: was in until 2026-09-15, one table over, and it is the state any band is
#: in when its provenance carries a summary in place of the numbers the
#: summary is of. `tests/test_band_derivations.py` rebuilds all fifty-six
#: edges from the readings below.
#:
#: WHAT IT IS. 32 of the certified forty, the names trading before 1990,
#: measured with THIS module's estimators over non-overlapping windows that
#: walk BACKWARD from the tape's last bar: 38 windows of 252 bars and 19 of
#: 504, the newest of each ending 2025-07-31, over a span of 9,842 common
#: bars opening 1986-07-09. The remainder is dropped at the START, which is
#: `INDEX_TAIL_WINDOWS`'s anchor rule and the opposite of
#: `FEAR_DN1_WINDOWS`'s.
#:
#: THE CRISIS DROPS ARE DERIVED AND NOT FLAGGED, which is the one place this
#: table's shape differs from `FEAR_DN1_WINDOWS`. A window is crisis when it
#: holds one of `crisis_dates`, which is the test the deriving tool applies,
#: and three windows hold one at each horizon. That leaves 35 readings at
#: 252 bars and 16 at 504, the two counts `BAND_BASIS` already records for
#: these tables. A flag would be a second place to make the same mistake.
#:
#: SIX DECIMAL PLACES, and the precision decides a shipped edge rather than
#: tidying one. `volume_abs_return_corr`'s 252-bar floor comes out of the
#: rule at 0.35003030, three parts in a hundred thousand above 0.35, and
#: `round_outward` floors it to 0.35. Round these readings to four places
#: and the same arithmetic gives 0.34. One edge of fifty-six turns on the
#: fifth decimal place of its inputs, so a table rounded shorter than this
#: ships a different band.
#:
#: The last six 504-bar windows are `REAL_MARKETS_WINDOWS_504`'s six, label
#: for label, because the two panels share an anchor and a window length and
#: differ in roster and span alone.
UNIVERSAL_WINDOWS: dict[str, Any] = {
    "roster": "32 of the certified forty, the names trading before 1990",
    "tape": ("1986-07-09", "2025-07-31"),
    "common_bars": 9842,
    "anchor": "backward from the last bar; remainder dropped at the start",
    "crisis_dates": ("1987-10-19", "2008-10-15", "2020-03-16"),
    "source": "tradefloor-design/programme/results/whole-tape/panel32.json, "
              "promoted 2026-09-15; the bands it derives are "
              "certification-bands.md section 14's, adopted under "
              "ruling-the-ruler-is-the-universal-band",
    #: Which band tables these readings are the real side of, per horizon.
    "tables": {252: "facts.REAL_MARKETS_UNIVERSAL",
               504: "facts.REAL_MARKETS_UNIVERSAL_504"},
    #: `start..end` per window, oldest first, keyed on the window's bar count.
    "windows": {
        252: (
            "1987-06-03..1988-06-01", "1988-06-02..1989-06-01",
            "1989-06-02..1990-06-01", "1990-06-04..1991-06-03",
            "1991-06-04..1992-06-02", "1992-06-03..1993-06-02",
            "1993-06-03..1994-06-02", "1994-06-03..1995-06-02",
            "1995-06-05..1996-06-03", "1996-06-04..1997-06-03",
            "1997-06-04..1998-06-04", "1998-06-05..1999-06-07",
            "1999-06-08..2000-06-06", "2000-06-07..2001-06-07",
            "2001-06-08..2002-06-14", "2002-06-17..2003-06-17",
            "2003-06-18..2004-06-18", "2004-06-21..2005-06-20",
            "2005-06-21..2006-06-21", "2006-06-22..2007-06-25",
            "2007-06-26..2008-06-25", "2008-06-26..2009-06-26",
            "2009-06-29..2010-06-29", "2010-06-30..2011-06-29",
            "2011-06-30..2012-06-29", "2012-07-02..2013-07-05",
            "2013-07-08..2014-07-08", "2014-07-09..2015-07-09",
            "2015-07-10..2016-07-11", "2016-07-12..2017-07-12",
            "2017-07-13..2018-07-13", "2018-07-16..2019-07-17",
            "2019-07-18..2020-07-17", "2020-07-20..2021-07-20",
            "2021-07-21..2022-07-21", "2022-07-22..2023-07-25",
            "2023-07-26..2024-07-26", "2024-07-29..2025-07-31",
        ),
        504: (
            "1987-06-30..1989-06-27", "1989-06-28..1991-06-26",
            "1991-06-27..1993-06-24", "1993-06-25..1995-06-23",
            "1995-06-26..1997-06-23", "1997-06-24..1999-06-24",
            "1999-06-25..2001-06-25", "2001-06-26..2003-07-02",
            "2003-07-03..2005-07-05", "2005-07-06..2007-07-09",
            "2007-07-10..2009-07-09", "2009-07-10..2011-07-11",
            "2011-07-12..2013-07-15", "2013-07-16..2015-07-16",
            "2015-07-17..2017-07-18", "2017-07-19..2019-07-22",
            "2019-07-23..2021-07-22", "2021-07-23..2023-07-26",
            "2023-07-27..2025-07-31",
        ),
    },
    "values": {
        252: {
            "annualised_vol_pct": (47.553602, 24.702041, 27.768408, 35.519058,
                28.333651, 27.461842, 27.709702, 24.528770, 26.193550,
                28.331313, 31.907621, 41.522924, 43.256897, 43.255948,
                31.419448, 39.113277, 21.345142, 20.592574, 19.840855,
                18.511218, 29.250382, 68.738987, 26.760179, 20.295611,
                29.617168, 19.147637, 16.493299, 18.465835, 24.996818,
                16.924291, 20.725225, 23.362097, 45.721387, 27.456448,
                27.864962, 26.357369, 21.802010, 28.497702),
            "excess_kurtosis": (27.697675, 12.489926, 5.179617, 2.671543,
                9.016349, 5.444510, 12.402307, 3.852995, 2.692265, 26.159179,
                11.638725, 5.388458, 7.002522, 73.240618, 7.825345, 4.098790,
                11.401950, 48.841708, 5.324672, 8.733467, 5.077461, 11.563627,
                3.060190, 3.248565, 8.156041, 4.909856, 6.295597, 5.248241,
                3.768983, 9.527720, 5.511385, 5.336323, 12.154527, 6.314437,
                3.746005, 4.628931, 15.020817, 15.147852),
            "return_acf1": (0.021189, -0.031655, 0.021175, 0.075673, 0.010005,
                -0.002154, -0.008761, -0.030510, -0.006145, 0.000441,
                -0.055543, -0.034634, 0.033149, -0.019500, -0.001113,
                -0.045624, -0.024927, -0.004544, 0.002306, 0.013049, -0.010789,
                -0.090485, -0.005584, 0.003652, -0.106959, -0.030329,
                -0.002429, -0.029579, 0.023732, -0.021307, -0.039326,
                -0.004744, -0.237206, -0.048067, 0.031761, -0.003690, 0.025677,
                -0.004002),
            "abs_return_acf1": (0.295262, 0.058754, 0.122034, 0.135267,
                0.108061, 0.085651, 0.077678, 0.076602, 0.070829, 0.095068,
                0.083175, 0.142042, 0.111162, 0.067406, 0.085507, 0.146195,
                0.026198, 0.086808, 0.053317, 0.067132, 0.044392, 0.183506,
                0.003106, 0.035682, 0.161408, 0.096663, 0.065694, 0.097626,
                0.183839, 0.082535, 0.160455, 0.128213, 0.444010, 0.063127,
                0.091134, 0.048776, 0.050082, 0.128579),
            "abs_return_acf5": (0.207594, -0.001700, 0.003055, 0.042255,
                0.004942, 0.036785, 0.003548, -0.001363, 0.017991, 0.045158,
                -0.003228, 0.064301, 0.039302, 0.029921, 0.076158, 0.122071,
                0.015421, 0.025687, 0.033481, 0.042058, 0.034917, 0.206641,
                0.077356, -0.006618, 0.128206, 0.007841, 0.024701, 0.033143,
                0.036559, 0.016151, 0.058422, 0.045264, 0.353807, 0.084169,
                0.040546, 0.012769, 0.022923, 0.055731),
            "abs_return_acf20": (0.022864, 0.002518, 0.026662, -0.000475,
                0.010889, 0.007789, -0.010172, -0.006659, 0.024211, 0.020196,
                0.006097, 0.023257, 0.042938, -0.004847, -0.006161, 0.043979,
                -0.002724, 0.001657, -0.013316, -0.002852, -0.040411, 0.101925,
                0.023613, -0.035023, 0.073663, -0.017384, 0.029891, -0.030127,
                0.002288, -0.016663, 0.004556, 0.055307, 0.141282, 0.031681,
                0.018703, 0.030019, -0.018484, 0.025849),
            "cross_sectional_corr": (0.567469, 0.329196, 0.356467, 0.366649,
                0.263070, 0.178299, 0.140048, 0.164978, 0.174190, 0.272961,
                0.324635, 0.282499, 0.207203, 0.133112, 0.259477, 0.449937,
                0.289465, 0.276191, 0.265320, 0.289831, 0.405576, 0.578953,
                0.436345, 0.410907, 0.625201, 0.370786, 0.317532, 0.412820,
                0.481511, 0.200814, 0.348083, 0.344638, 0.632600, 0.277017,
                0.328427, 0.362520, 0.170525, 0.282782),
            "volume_abs_return_corr": (0.462333, 0.371121, 0.360979, 0.377470,
                0.366214, 0.414698, 0.387374, 0.418878, 0.350275, 0.380422,
                0.400576, 0.462166, 0.489796, 0.473389, 0.490949, 0.521812,
                0.489878, 0.499371, 0.499642, 0.510813, 0.518316, 0.488241,
                0.460503, 0.461103, 0.536787, 0.495719, 0.471824, 0.530761,
                0.614406, 0.608574, 0.586661, 0.497999, 0.644925, 0.537091,
                0.504846, 0.493305, 0.498059, 0.521496),
            "leverage_effect": (-0.135007, 0.023361, -0.063808, -0.029574,
                -0.007300, -0.033266, -0.012047, 0.000939, 0.010640, -0.019945,
                -0.055192, -0.048037, 0.012075, -0.052446, -0.041309,
                -0.039703, -0.058483, -0.043742, -0.000745, -0.025163,
                -0.008333, -0.095088, -0.065755, -0.002566, -0.086718,
                -0.034262, -0.053845, 0.019669, -0.119136, 0.004985, -0.084896,
                -0.117802, -0.137361, 0.008207, -0.046720, -0.042352,
                -0.007179, -0.036570),
            "volume_change_acf1": (-0.247007, -0.238848, -0.269536, -0.222354,
                -0.239032, -0.182918, -0.219982, -0.278114, -0.205037,
                -0.247061, -0.238994, -0.251375, -0.210951, -0.236130,
                -0.257574, -0.252883, -0.219132, -0.244460, -0.245062,
                -0.272221, -0.281531, -0.266974, -0.275182, -0.277185,
                -0.264219, -0.251481, -0.263894, -0.254947, -0.232997,
                -0.250623, -0.270219, -0.260605, -0.284683, -0.269213,
                -0.244851, -0.298974, -0.269397, -0.244342),
            "corr_asymmetry": (0.378005, -0.007232, 0.286864, -0.043035,
                0.104860, 0.020770, 0.027422, 0.039761, 0.149462, 0.074034,
                0.153287, 0.068261, -0.047820, 0.026746, 0.041254, -0.122980,
                0.021752, -0.035174, 0.007803, 0.204310, 0.000300, -0.051040,
                0.163837, 0.058134, 0.230296, 0.078307, -0.008692, 0.023758,
                0.085293, 0.135383, 0.356487, -0.102110, 0.206879, 0.075954,
                0.061987, -0.017337, -0.020530, 0.039555),
            "corr_asymmetry_lagged": (0.280762, 0.148189, 0.282129, 0.003807,
                0.062607, 0.080033, 0.138378, 0.083333, 0.031715, 0.038405,
                0.212550, 0.127203, 0.007575, 0.003529, 0.090711, 0.007046,
                0.132037, 0.092441, 0.118661, 0.264773, -0.069722, 0.154311,
                0.135701, 0.072705, 0.172336, -0.013921, 0.205905, -0.171229,
                0.143587, 0.117106, 0.199113, 0.419065, 0.056832, 0.135447,
                -0.109537, 0.075845, 0.088450, 0.406664),
            "sector_excess_corr": (0.005025, 0.060771, 0.064696, 0.124803,
                0.096862, 0.195562, 0.162453, 0.112099, 0.149797, 0.101473,
                0.105178, 0.121100, 0.159142, 0.205958, 0.172604, 0.111058,
                0.108568, 0.115612, 0.136152, 0.132867, 0.113385, 0.126614,
                0.116712, 0.126543, 0.086024, 0.109223, 0.126591, 0.169565,
                0.160588, 0.225870, 0.179085, 0.162995, 0.119349, 0.235264,
                0.236962, 0.197766, 0.173653, 0.176876),
            "corr_persistence_acf1": (0.308527, 0.279320, -0.467629, 0.064058,
                -0.298265, -0.456119, 0.370172, 0.137297, 0.688931, -0.189652,
                0.284150, 0.372912, -0.112543, 0.471594, 0.296863, 0.031037,
                -0.240169, -0.421899, -0.252407, 0.182274, 0.055839, 0.470124,
                0.119592, 0.140506, 0.449633, 0.085069, 0.350149, -0.355760,
                0.040606, 0.107607, 0.328961, 0.242444, 0.381648, -0.083515,
                0.092939, 0.342837, -0.202587, 0.287776),
        },
        504: {
            "annualised_vol_pct": (37.988152, 31.840505, 28.048137, 25.888098,
                27.433065, 37.103249, 43.229551, 35.368002, 20.922066,
                19.137987, 52.956806, 23.592836, 24.919052, 17.506794,
                21.329889, 22.145330, 37.731511, 27.187147, 25.351876),
            "excess_kurtosis": (36.360670, 3.579054, 7.771604, 8.710463,
                16.421249, 7.488189, 40.237784, 5.585210, 29.097130, 6.977199,
                18.054907, 3.624101, 9.536314, 5.734480, 5.881289, 5.466941,
                14.716459, 4.222618, 16.413339),
            "return_acf1": (0.019902, 0.053511, 0.010506, -0.011177, 0.000214,
                -0.029998, 0.015851, -0.024916, -0.008823, 0.002207, -0.082162,
                0.010329, -0.062307, -0.002907, 0.015849, 0.005918, -0.185552,
                0.015576, -0.002283),
            "abs_return_acf1": (0.273809, 0.145440, 0.108583, 0.083841,
                0.091794, 0.148630, 0.088899, 0.159955, 0.074945, 0.063704,
                0.245245, 0.045720, 0.145054, 0.087864, 0.188566, 0.151580,
                0.377889, 0.075643, 0.107796),
            "abs_return_acf5": (0.217476, 0.042639, 0.045404, 0.016718,
                0.030612, 0.069973, 0.039212, 0.120916, 0.052489, 0.041054,
                0.271815, 0.054341, 0.115157, 0.036614, 0.067013, 0.050773,
                0.304851, 0.040998, 0.051888),
            "abs_return_acf20": (0.066351, 0.030934, 0.015475, 0.008021,
                0.040072, 0.042183, 0.029291, 0.055036, 0.015310, 0.013526,
                0.157565, 0.013227, 0.080701, 0.005225, 0.049162, 0.041562,
                0.134907, 0.027410, 0.023442),
            "cross_sectional_corr": (0.514414, 0.360802, 0.215649, 0.152200,
                0.227281, 0.292403, 0.171509, 0.380136, 0.271287, 0.273212,
                0.539356, 0.423745, 0.529938, 0.367665, 0.387306, 0.342026,
                0.535801, 0.340850, 0.238228),
            "volume_abs_return_corr": (0.422011, 0.361634, 0.407698, 0.385106,
                0.372068, 0.449182, 0.457755, 0.516158, 0.499736, 0.479283,
                0.526433, 0.471414, 0.531663, 0.527327, 0.613660, 0.528575,
                0.594509, 0.495023, 0.509120),
            "leverage_effect": (-0.100984, -0.048716, -0.012328, -0.018358,
                -0.014597, -0.051689, -0.007438, -0.034585, -0.051340,
                -0.019724, -0.073068, -0.047073, -0.076106, -0.016807,
                -0.080849, -0.096359, -0.102107, -0.045276, -0.025020),
            "volume_change_acf1": (-0.216034, -0.240227, -0.204612, -0.219858,
                -0.220262, -0.254332, -0.211951, -0.259610, -0.242476,
                -0.258262, -0.264543, -0.278475, -0.245738, -0.261795,
                -0.232085, -0.258842, -0.272957, -0.276355, -0.251747),
            "corr_asymmetry": (0.386807, 0.086706, 0.060795, 0.027303,
                0.111064, 0.087321, -0.026165, -0.064215, -0.005679, 0.100939,
                0.019278, 0.104120, 0.194968, 0.027992, 0.092545, 0.088251,
                0.162526, 0.025561, 0.032389),
            "corr_asymmetry_lagged": (0.260696, 0.023755, 0.047362, 0.105595,
                0.039762, 0.161451, -0.014413, 0.009822, 0.092627, 0.151413,
                0.130869, 0.155244, 0.111407, -0.023503, 0.164274, 0.303050,
                0.032857, 0.000053, 0.361708),
            "sector_excess_corr": (0.015804, 0.098810, 0.152750, 0.137831,
                0.121482, 0.116282, 0.180208, 0.136154, 0.113291, 0.134643,
                0.125689, 0.119903, 0.095753, 0.153125, 0.180548, 0.172149,
                0.150739, 0.218027, 0.175858),
            "corr_persistence_acf1": (0.332173, -0.264004, 0.107614, 0.155850,
                0.630274, 0.150822, 0.528826, 0.660670, -0.342393, 0.291133,
                0.430331, 0.165456, 0.549624, -0.110857, 0.278577, 0.389668,
                0.360935, 0.331504, 0.219027),
        },
    },
}


def universal_window_is_crisis(label: str) -> bool:
    """Whether a `start..end` window holds one of the panel's crisis sessions.

    The crisis rule as code, because the drop count is the difference
    between a 35-window band and a 38-window one and the table records no
    flag to disagree with.
    """
    start, _, end = label.partition("..")
    return any(start <= date <= end
               for date in UNIVERSAL_WINDOWS["crisis_dates"])


def universal_windows(key: str, horizon_days: int, *,
                      include_crisis: bool = False) -> tuple[float, ...]:
    """The 32-name whole-tape readings the universal band for `key` is built from.

    Crisis windows are EXCLUDED by default, because the band's rule drops
    them; pass ``include_crisis=True`` for the whole block set, which is what
    a sensitivity on the crisis rule needs.
    """
    horizon_days = int(horizon_days)
    values = UNIVERSAL_WINDOWS["values"].get(horizon_days)
    if values is None:
        raise ValidationError(
            f"the universal window table holds no {horizon_days}-bar "
            f"windows; measured horizons are "
            f"{sorted(UNIVERSAL_WINDOWS['values'])}. Re-run the 32-name pull "
            "at that horizon and record the windows rather than rescaling a "
            "band from another one")
    if key not in values:
        raise ValidationError(
            f"{key!r} has no universal window readings. The 32-name panel "
            f"carries equities, so the level and crisis rows read off ^VIX, "
            f"^GSPC and RSP are absent from it by construction; the rows it "
            f"holds are {sorted(values)}")
    labels = UNIVERSAL_WINDOWS["windows"][horizon_days]
    return tuple(
        value for label, value in zip(labels, values[key])
        if include_crisis or not universal_window_is_crisis(label))


def band_from_windows_fixed(key: str, values: Sequence[float],
                            multiplier: float) -> tuple[float, float]:
    """The `fixed` rule in full: the band `multiplier` and the windows produce.

    `BAND_RULES` names two rules and until now only one of them had code.
    `band_from_windows` is `spread`, whose false-alarm rate follows the
    window count; this is `fixed`, the median plus and minus `multiplier`
    times the trimmed sd, where the multiplier is solved per window count so
    the rate does not move. `BAND_BASIS` carries the multiplier each shipped
    fixed-rule table was built with.
    """
    centre = statistics.median(list(values))
    scale = trimmed_sd(values)
    return (round_outward(centre - multiplier * scale, "low", key),
            round_outward(centre + multiplier * scale, "high", key))


_Adjustments = dict[str, dict[str, tuple[float, str, str]]]

#: Where a shipped universal band departs from `band_from_windows_fixed` on
#: its own windows, per horizon: `{horizon: {row: {edge: (value, kind, why)}}}`.
#:
#: `BAND_BASIS` states these in a sentence ("clamp #1 and clamp #2
#: re-applied; the Campbell ceiling retired as redundant"). This is the same
#: fact as data, so `tests/test_band_derivations.py` derives every shipped
#: edge as the rule plus a named move and an edge that is neither fails.
#:
#: The two clamps are `REAL_MARKETS_ADJUSTMENTS`'s, re-applied to the whole
#: span and costing more here than they do on the decade: clamp #2 excludes
#: seven of the 35 windows at 252 rather than one of nine, because the
#: leverage effect reads positive in seven whole-tape years. The 504 table
#: carries no move at all, which is `REAL_MARKETS_ADJUSTMENTS_504`'s state
#: and holds for the same reason: neither clamp binds there, the leverage
#: effect being negative in all 16 windows and the clustering floor already
#: above 0.02 raw.
#:
#: The Campbell ceiling is ABSENT rather than retired quietly: the rule's own
#: ceiling at 252 is 41.0, past the 36.0 the shipped decade band was moved
#: out to, so applying the literature move would round the band INWARD and
#: REALISM-BANDS.md allows an outward move only.
REAL_MARKETS_UNIVERSAL_ADJUSTMENTS: dict[int, _Adjustments] = {
    252: {
        "abs_return_acf1": {
            "low": (0.02, "inward",
                    "clamp #1, as on the decade band: zero or negative "
                    "clustering appears in no retrieved source, and the "
                    "rule's 0.00 would admit a model with no volatility "
                    "memory. One of the 35 windows reads under 0.02, at "
                    "0.0031, so the clamp costs one real year here"),
        },
        "leverage_effect": {
            "high": (0.0, "inward",
                     "clamp #2, as on the decade band: every retrieved "
                     "source gives the effect a negative sign and the rule's "
                     "+0.04 would certify a reversed one. The cost is SEVEN "
                     "of the 35 windows, the years reading up to +0.0234, "
                     "against one of nine on the decade band"),
        },
    },
    504: {},
}


#: The universal band: the fourteen shape rows scored against the whole tape
#: rather than against one decade of it.
#:
#: `median +/- t(n) * trimmed_sd` over the 32-name 1987-06..2025-07
#: non-crisis windows, unbridged, n = 35, with the two inward clamps of
#: `REAL_MARKETS_ADJUSTMENTS` re-applied and the Campbell ceiling retired as
#: redundant -- the band's own ceiling now reaches 41.0, past the 36.0 the
#: adjustment moved the shipped edge out to.
#:
#: Derived in `certification-bands.md` section 14 and adopted under
#: `ruling-the-ruler-is-the-universal-band`. THE MODEL IS MEASURED ON
#: REALISM, NOT AGAINST ARTIFICIAL BANDS THAT MAY NOT BE ACCURATE, and this
#: table is that sentence as data.
#:
#: CONFLICT OF INTEREST, RECORDED DELIBERATELY AND NOT TO BE DROPPED. It was
#: known BEFORE adoption that pt-v19 goes 13 to 14 rows under this band
#: while pt-v18 is untouched at 14 and 14. Choosing the ruler that passes
#: your own candidate is the B3 defect one level up. The decision rests on
#: three grounds that have nothing to do with that outcome: the shipped band
#: is one decade against a rule spanning four; the fairness test passes at
#: 4.8 per cent rejection against a designed 6.5; and this band still TAKES
#: count away from four presets the decade band passed.
#:
#: Four rows carry a caveat and `BAND_BASIS` states each one:
#: `excess_kurtosis`'s floor of -13.0 is below the theoretical minimum of -2
#: and the row is roster-limited rather than era-limited;
#: `corr_persistence_acf1` at 504 is on a protocol the 32-name pull does not
#: reproduce; `abs_return_acf5` replaces a band whose window set no file
#: holds; and the five level rows have NO universal band at either horizon,
#: because the 32-name panel carries equities and four of the five are read
#: off ^VIX, ^GSPC and RSP.
REAL_MARKETS_UNIVERSAL: dict[str, tuple[float, float]] = {
    "annualised_vol_pct": (12.0, 41.0),
    "excess_kurtosis": (-13.0, 24.0),
    "return_acf1": (-0.07, 0.06),
    "abs_return_acf1": (0.02, 0.17),
    "abs_return_acf5": (-0.03, 0.1),
    "abs_return_acf20": (-0.05, 0.06),
    "cross_sectional_corr": (0.09, 0.49),
    "volume_abs_return_corr": (0.35, 0.64),
    "leverage_effect": (-0.11, 0.0),
    "volume_change_acf1": (-0.3, -0.2),
    "corr_asymmetry": (-0.15, 0.23),
    "corr_asymmetry_lagged": (-0.15, 0.33),
    "sector_excess_corr": (0.04, 0.23),
    "corr_persistence_acf1": (-0.48, 0.69),
}

#: The universal band at 504 bars, n = 16, on the same rule and the same
#: windows. `REAL_MARKETS_ADJUSTMENTS_504` is empty and stays empty, so this
#: table carries no clamp -- the same mechanism as the shipped 504 band,
#: which also carries none. MEASURED that the clamp ruling costs nothing
#: here either way: re-applying clamp #1 and clamp #2 at 504 moves zero of
#: the 504 shape-row verdicts on the eighteen committed records.
REAL_MARKETS_UNIVERSAL_504: dict[str, tuple[float, float]] = {
    "annualised_vol_pct": (12.0, 40.0),
    "excess_kurtosis": (-9.3, 24.0),
    "return_acf1": (-0.05, 0.05),
    "abs_return_acf1": (0.01, 0.19),
    "abs_return_acf5": (-0.01, 0.11),
    "abs_return_acf20": (-0.01, 0.07),
    "cross_sectional_corr": (0.11, 0.52),
    "volume_abs_return_corr": (0.34, 0.63),
    "leverage_effect": (-0.1, 0.02),
    "volume_change_acf1": (-0.3, -0.2),
    "corr_asymmetry": (-0.06, 0.21),
    "corr_asymmetry_lagged": (-0.12, 0.32),
    "sector_excess_corr": (0.06, 0.21),
    "corr_persistence_acf1": (-0.38, 0.88),
}

#: What every band table in this module IS, as opposed to what it is called.
#:
#: THIS IS THE DEFECT THE UNIVERSAL RULING EXPOSED, AND IT IS WHY THIS DICT
#: EXISTS. A record that stamps `"bands_252": "facts.REAL_MARKETS"` records a
#: NAME. Swap that dict's contents and every record on disk still asserts the
#: same provenance, every count under it changes, and nothing anywhere
#: disagrees. A name is not a basis. An era, a window count and a rule are.
#:
#: Anything that publishes a band verdict stamps the basis, not the symbol,
#: and `programme/scripts/guards.py` guard 20
#: (`published-constant-binds-its-vector`) refuses a band named without one.
BAND_BASIS: dict[str, dict[str, Any]] = {
    "facts.REAL_MARKETS": {
        "era": "2015-07..2025-07",
        "roster": "the certified forty, exactly",
        "n_windows": 9,
        "rule": "spread",
        "tolerance": 0.06486,
        "rows": 18,
        "note": "one decade, one crisis window excluded. "
                "corr_persistence_acf1 is the only row on a different count "
                "at 504 (four, BAND_WINDOWS_EXCEPTIONS)",
    },
    "facts.REAL_MARKETS_504": {
        "era": "2015-07..2025-07",
        "roster": "the certified forty, exactly",
        "n_windows": 5,
        "rule": "spread",
        "tolerance": 0.16800,
        "rows": 14,
        "note": "five windows, so this table is a LOOSER test than the 252 "
                "one by a factor of two and a half, and four windows looser "
                "still on corr_persistence_acf1 at 0.23998",
    },
    "facts.REAL_MARKETS_UNIVERSAL": {
        "era": "1987-06..2025-07",
        "roster": "32 of the certified forty, the ones trading before 1990; "
                  "unbridged, carrying a stated level bias under 10 per cent "
                  "of band width on 13 of 14 rows",
        "n_windows": 35,
        "rule": "fixed",
        "tolerance": BAND_RULE_FIXED_TOLERANCE,
        "multiplier": 2.111347,
        "rows": 14,
        "adjustments": "clamp #1 and clamp #2 re-applied; the Campbell "
                       "ceiling retired as redundant",
        "note": "four eras, three crisis windows excluded. Tested out of "
                "sample: admits 120 of 126 forty-name real year-rows, a "
                "rejection rate of 4.8 per cent against a designed 6.5",
    },
    "facts.REAL_MARKETS_UNIVERSAL_504": {
        "era": "1987-06..2025-07",
        "roster": "32 of the certified forty, the ones trading before 1990; "
                  "unbridged",
        "n_windows": 16,
        "rule": "fixed",
        "tolerance": BAND_RULE_FIXED_TOLERANCE,
        "multiplier": 2.417688,
        "rows": 14,
        "adjustments": "none, the same as REAL_MARKETS_ADJUSTMENTS_504",
        "note": "admits 64 of 65 forty-name real year-rows. "
                "corr_persistence_acf1 is carried here on the walked "
                "six-window protocol and NOT on the shipped sub-window one, "
                "so its universal band is a band for a different quantity "
                "until that row-definition ruling is made",
    },
}


def band_basis(name: str) -> dict[str, Any]:
    """What the band table called `name` is: its era, window count and rule.

    Raises for a table with no recorded basis, because a band grading a
    record with nothing said about where it came from is the state this
    function exists to end.
    """
    try:
        return BAND_BASIS[name]
    except KeyError:
        raise ValidationError(
            f"{name!r} has no recorded band basis. A record that stamps a "
            f"band's NAME and not its era, window count and rule asserts a "
            f"provenance that survives the table's contents being replaced; "
            f"tables with a basis are {sorted(BAND_BASIS)}"
        ) from None


register_ruler_table(REAL_MARKETS_UNIVERSAL, CERTIFIED_HORIZON_DAYS,
                     "facts.REAL_MARKETS_UNIVERSAL")
register_ruler_table(REAL_MARKETS_UNIVERSAL_504, 504,
                     "facts.REAL_MARKETS_UNIVERSAL_504")


# --------------------------------------------------------------------------
# The RULED band: every row against the longest tape its own data supports
#
# `REAL_MARKETS_UNIVERSAL` covers the fourteen shape rows and stops there,
# because the 32-name panel it is built from carries equities and the level
# and crisis rows are read off ^VIX, ^GSPC and RSP. Registering it was not
# enough to make anything read it: until this block landed, both
# `RULERS_BY_HORIZON` tables held the 2015-2025 decade set at 252 and
# `envelope.BANDS_504` at 504, so `envelope.score`, `envelope.certify` and
# `loss.scoring_rule` could not produce a universal-basis verdict at all.
# The ruling was adopted against one object and computed against another.
#
# This composes the ruled table under `ruling-longest-tape-per-row`: each
# row takes the band derived on the longest span its own instrument
# supports, and a row with no such band is ABSENT rather than filled from
# the decade table. Absent is the honest state and it is what
# `RULED_UNREADABLE` records; filling it would reproduce exactly the defect
# this block exists to end, one row lower down.
# --------------------------------------------------------------------------

#: The 1928 tail row's own window readings, as data, back to where
#: `INDEX_TAIL_WINDOWS` opens.
#:
#: WHY THIS TABLE EXISTS. `RULED_TAIL_BAND` below grades `index_tail_dn3_pct`
#: on the basis `envelope.BAR_BAND_BASIS` names, so it is one of the sixteen
#: bands the release bar is read on, and until this table landed it was two
#: typed numbers. Its derivation was written out in the comment beneath it
#: and there was nothing in this package to run that derivation against.
#:
#: WHAT IS HERE AND WHAT IS NOT. Only the windows OLDER than the shipped
#: 1990 span: 62 at 252 returns and 31 at 504. The newer ones are
#: `INDEX_TAIL_WINDOWS` unchanged, and `ruled_tail_windows` returns the two
#: concatenated, so the 97-window and 48-window sets have one spelling of
#: every window they share. The join is a real join and not an assertion
#: about one: the last window here ends 1990-07-23 at 252 and the shipped
#: table's first window opens 1990-07-24.
#:
#: `(start, end, hits, sessions)` per window, oldest first, keyed on the
#: window's return count, which is `INDEX_TAIL_WINDOWS`'s form exactly. Same
#: series, same threshold, same anchor at the tape's last bar, and the
#: remainder dropped at the start, so the first counted session is
#: 1928-04-09 at 252 returns and 1929-04-11 at 504.
RULED_TAIL_WINDOWS: dict[str, Any] = {
    "series": "^GSPC",
    "column": "unadjusted close",
    "threshold_pct": -3.0,
    "anchor": "backward from the last bar; remainder dropped at the start",
    "continues": "facts.INDEX_TAIL_WINDOWS, which holds the 1990 span",
    "source": "tradefloor-design/programme/results/longest-tape/"
              "tail-band.json, the 1927 cut; promoted 2026-09-15",
    "rows": ("index_tail_dn3_pct",),
    "windows": {
        252: (
            ("1928-04-09", "1929-04-10", 4, 252),
            ("1929-04-11", "1930-04-11", 17, 252),
            ("1930-04-14", "1931-04-16", 18, 252),
            ("1931-04-17", "1932-04-15", 38, 252),
            ("1932-04-18", "1933-04-28", 48, 252),
            ("1933-05-01", "1934-05-02", 22, 252),
            ("1934-05-03", "1935-05-07", 4, 252),
            ("1935-05-08", "1936-05-07", 8, 252),
            ("1936-05-08", "1937-05-10", 4, 252),
            ("1937-05-11", "1938-05-10", 24, 252),
            ("1938-05-11", "1939-05-15", 11, 252),
            ("1939-05-16", "1940-05-16", 4, 252),
            ("1940-05-17", "1941-05-19", 7, 252),
            ("1941-05-20", "1942-05-22", 2, 252),
            ("1942-05-25", "1943-05-25", 1, 252),
            ("1943-05-26", "1944-05-24", 2, 252),
            ("1944-05-25", "1945-05-28", 0, 252),
            ("1945-05-29", "1946-06-06", 3, 252),
            ("1946-06-07", "1947-06-09", 9, 252),
            ("1947-06-10", "1948-06-11", 1, 252),
            ("1948-06-14", "1949-06-13", 5, 252),
            ("1949-06-14", "1950-06-16", 0, 252),
            ("1950-06-19", "1951-06-20", 4, 252),
            ("1951-06-21", "1952-06-24", 0, 252),
            ("1952-06-25", "1953-06-26", 1, 252),
            ("1953-06-29", "1954-06-29", 0, 252),
            ("1954-06-30", "1955-06-28", 0, 252),
            ("1955-06-29", "1956-06-27", 1, 252),
            ("1956-06-28", "1957-06-28", 0, 252),
            ("1957-07-01", "1958-06-27", 0, 252),
            ("1958-06-30", "1959-06-29", 0, 252),
            ("1959-06-30", "1960-06-28", 0, 252),
            ("1960-06-29", "1961-06-29", 1, 252),
            ("1961-06-30", "1962-06-29", 2, 252),
            ("1962-07-02", "1963-07-01", 0, 252),
            ("1963-07-02", "1964-07-01", 0, 252),
            ("1964-07-02", "1965-07-01", 0, 252),
            ("1965-07-02", "1966-06-30", 0, 252),
            ("1966-07-01", "1967-06-30", 0, 252),
            ("1967-07-03", "1968-07-11", 0, 252),
            ("1968-07-12", "1969-08-13", 0, 252),
            ("1969-08-14", "1970-08-11", 0, 252),
            ("1970-08-12", "1971-08-10", 0, 252),
            ("1971-08-11", "1972-08-07", 0, 252),
            ("1972-08-08", "1973-08-09", 0, 252),
            ("1973-08-10", "1974-08-08", 2, 252),
            ("1974-08-09", "1975-08-07", 1, 252),
            ("1975-08-08", "1976-08-05", 0, 252),
            ("1976-08-06", "1977-08-05", 0, 252),
            ("1977-08-08", "1978-08-04", 0, 252),
            ("1978-08-07", "1979-08-03", 0, 252),
            ("1979-08-06", "1980-08-01", 1, 252),
            ("1980-08-04", "1981-08-03", 0, 252),
            ("1981-08-04", "1982-08-02", 0, 252),
            ("1982-08-03", "1983-07-29", 1, 252),
            ("1983-08-01", "1984-07-27", 0, 252),
            ("1984-07-30", "1985-07-26", 0, 252),
            ("1985-07-29", "1986-07-28", 1, 252),
            ("1986-07-29", "1987-07-27", 1, 252),
            ("1987-07-28", "1988-07-25", 8, 252),
            ("1988-07-26", "1989-07-24", 0, 252),
            ("1989-07-25", "1990-07-23", 1, 252),
        ),
        504: (
            ("1929-04-11", "1931-04-16", 35, 504),
            ("1931-04-17", "1933-04-28", 86, 504),
            ("1933-05-01", "1935-05-07", 26, 504),
            ("1935-05-08", "1937-05-10", 12, 504),
            ("1937-05-11", "1939-05-15", 35, 504),
            ("1939-05-16", "1941-05-19", 11, 504),
            ("1941-05-20", "1943-05-25", 3, 504),
            ("1943-05-26", "1945-05-28", 2, 504),
            ("1945-05-29", "1947-06-09", 12, 504),
            ("1947-06-10", "1949-06-13", 6, 504),
            ("1949-06-14", "1951-06-20", 4, 504),
            ("1951-06-21", "1953-06-26", 1, 504),
            ("1953-06-29", "1955-06-28", 0, 504),
            ("1955-06-29", "1957-06-28", 1, 504),
            ("1957-07-01", "1959-06-29", 0, 504),
            ("1959-06-30", "1961-06-29", 1, 504),
            ("1961-06-30", "1963-07-01", 2, 504),
            ("1963-07-02", "1965-07-01", 0, 504),
            ("1965-07-02", "1967-06-30", 0, 504),
            ("1967-07-03", "1969-08-13", 0, 504),
            ("1969-08-14", "1971-08-10", 0, 504),
            ("1971-08-11", "1973-08-09", 0, 504),
            ("1973-08-10", "1975-08-07", 3, 504),
            ("1975-08-08", "1977-08-05", 0, 504),
            ("1977-08-08", "1979-08-03", 0, 504),
            ("1979-08-06", "1981-08-03", 1, 504),
            ("1981-08-04", "1983-07-29", 1, 504),
            ("1983-08-01", "1985-07-26", 0, 504),
            ("1985-07-29", "1987-07-27", 2, 504),
            ("1987-07-28", "1989-07-24", 8, 504),
            ("1989-07-25", "1991-07-22", 2, 504),
        ),
    },
    #: The moving-block bootstrap error of the centre, block length 3, at
    #: 200,000 draws and seed 20260905. RECORDED rather than derived at
    #: import because the draw is a second of CPU a horizon and this module
    #: is imported to read a band, not to re-run one;
    #: `moving_block_bootstrap_se` below is the derivation and
    #: `tests/test_band_derivations.py` runs it against these two figures.
    #:
    #: The iid error the shipped 1990 span is licensed to use is 0.3213354
    #: at 252, so the correction is a factor of 1.428. The licence is the
    #: lag-1 autocorrelation of the window counts, +0.0595 on the 1990 span
    #: and +0.5952 here.
    "block_bootstrap_se": {252: 0.4589760671, 504: 0.4921387122},
    "block_length": 3,
    "bootstrap_draws": 200_000,
    "bootstrap_seed": 20260905,
}


def ruled_tail_windows(horizon_days: int) -> tuple[tuple[Any, ...], ...]:
    """The whole ^GSPC record's tail windows: the old ones plus the shipped ones.

    One sequence, built from two tables, so no window is written down twice.
    97 windows at 252 returns and 48 at 504.
    """
    horizon_days = int(horizon_days)
    older = RULED_TAIL_WINDOWS["windows"].get(horizon_days)
    newer = INDEX_TAIL_WINDOWS["windows"].get(horizon_days)
    if older is None or newer is None:
        raise ValidationError(
            f"the 1928 tail table holds no {horizon_days}-return windows; "
            f"measured horizons are {sorted(RULED_TAIL_WINDOWS['windows'])}. "
            "Run the longest-tape cut at that horizon and record the windows "
            "rather than rescaling a rate from another one")
    return tuple(older) + tuple(newer)


def ruled_tail_rates(horizon_days: int) -> tuple[float, ...]:
    """Each whole-record window's rate at `horizon_days`, in percent of sessions."""
    return tuple(100.0 * hits / sessions
                 for _, _, hits, sessions in ruled_tail_windows(horizon_days))


def moving_block_bootstrap_se(values: Sequence[float], block_length: int, *,
                              draws: int = 200_000,
                              seed: int = 20260905) -> float:
    """The standard error of the mean under resampling in blocks of `block_length`.

    The correction a serially dependent sample needs and `sd/sqrt(n)` does
    not give. Blocks are drawn with replacement from every starting position
    until the resample is at least as long as the sample, then trimmed to the
    sample's length, which is the form the 1928 tail band was derived with.

    Stdlib only, like `band_rule_false_alarm`: this module takes no runtime
    dependency to re-derive one of its own constants.
    """
    values = list(values)
    if block_length < 1 or block_length > len(values):
        raise ValidationError(
            f"block_length must be between 1 and {len(values)}, got "
            f"{block_length}")
    if draws < 2:
        raise ValidationError(f"draws must be at least 2, got {draws}")
    rng = random.Random(seed)
    starts = len(values) - block_length + 1
    means = []
    for _ in range(draws):
        sample: list[float] = []
        while len(sample) < len(values):
            k = rng.randrange(starts)
            sample.extend(values[k:k + block_length])
        means.append(statistics.fmean(sample[:len(values)]))
    return statistics.stdev(means)


#: `index_tail_dn3_pct` on the whole ^GSPC record, 1928-2025, with the error
#: bar corrected for clustering. Adopted under
#: `ruling-tail-row-1928-with-clustering-corrected`.
#:
#: DERIVED, and the derivation is the same `centre +/- t * se` the shipped
#: band uses with one term replaced. Over the 97 non-overlapping 252-return
#: windows the centre is 1.4891180 and `t` is
#: `centre_multiplier(band_rule_tolerance(9))` = 1.8462218, both unchanged
#: from the shipped span. The error is the MOVING-BLOCK BOOTSTRAP at block
#: length 3, 0.4589761, in place of the iid `sd/sqrt(97)` of 0.3213354.
#: 1.4891180 -/+ 1.8462218 * 0.4589761 = (0.641746, 2.336490), rounded
#: outward at 0.01.
#:
#: WHY THE ERROR IS CORRECTED HERE AND NOWHERE ELSE, which is the whole
#: argument. `REAL_MARKETS_PROVENANCE` licenses the iid form on two
#: diagnostics this row runs and prints: a lag-1 autocorrelation of the
#: window counts of +0.06, and a block bootstrap agreeing with the iid
#: error to 3 per cent. On the 1928 span those two read +0.5952 and 43 per
#: cent. 209 of the 364 hits fall in the 13 windows covering 1928-1940, so
#: consecutive years do share their crashes and `sd/sqrt(97)` is about 40
#: per cent too small at both edges. Correcting a variance estimate the
#: data itself refuses is the same discipline applied to the error that is
#: applied to the centre. `index_drift_pct` gets no such correction at the
#: same 1928 start because its annual returns read a lag-1 of +0.0265,
#: which licenses the iid form.
#:
#: THE COST, RECORDED SO IT IS NOT DISCOVERED LATER. A band this wide
#: discriminates less, and this row will not decide between pt-v19 and
#: pt-v18: both are in at both horizons. That is the honest representation
#: of a row whose population moves this far, rather than a weakness. The
#: -3 per cent session rate runs 6.38 per cent over 1928-40, 0.61 over
#: 1941-59, 0.25 over 1960-89 and 1.21 over 1990-2025, a factor of 25
#: between the extreme eras.
RULED_TAIL_BAND: tuple[float, float] = (0.64, 2.34)

#: The same construction at 504 bars, as the CHECK on carrying the 252 band
#: at both horizons rather than as a second band.
#:
#: DERIVED here so the carry is argued rather than assumed, on the same
#: grounds `BANDS_504` already gives for the shipped span: the row is a per
#: SESSION rate, so a longer window measures the same quantity with more
#: sessions. Over the 48 non-overlapping 504-return windows the centre is
#: 1.4880952 and the block-3 bootstrap error is 0.4921387, giving
#: (0.579498, 2.396692) and a rounded (0.57, 2.40). That is within 0.07 of
#: `RULED_TAIL_BAND` at the floor and 0.06 at the ceiling, each about a
#: twenty-fifth of the band's own width of 1.70, which is the same order of
#: agreement the shipped span shows between [0.47, 1.96] and [0.47, 2.00].
#: So `RULED_TAIL_BAND` grades both horizons and this number is the
#: residual on that decision.
RULED_TAIL_BAND_504_CHECK: tuple[float, float] = (0.57, 2.40)

#: The two drift bands' input legs, as data.
#:
#: WHAT THIS CLOSES AND WHAT IT DOES NOT, said first because the difference
#: is the whole value of the entry. `REAL_MARKETS["index_drift_pct"]` and
#: `RULED_DRIFT_BAND` were both typed pairs: the arithmetic that produces
#: them is written out in their comments, and none of the arithmetic's inputs
#: was in this package, so a mistyped edge read the same as a measured one.
#: The legs below make both bands a function of committed numbers, and
#: `tests/test_band_derivations.py` rebuilds all four edges from them.
#:
#: They are SUMMARY STATISTICS AND NOT READINGS. `mean` and `sd` are taken
#: over `n` calendar-year returns and the years themselves are still not
#: committed anywhere, so this table cannot catch a mistyped LEG the way
#: `UNIVERSAL_WINDOWS` catches a mistyped band. That is a smaller claim than
#: the window tables make and it is the honest one: committing the readings
#: is 98 annual ^GSPC returns, 22 RSP premia and 19 ^SPXEW premia, and it
#: needs `tools/calibration/index_band.py` re-run against a ^GSPC cache back
#: to 1927 plus the RSP and ^SPXEW pulls. This checkout holds ^GSPC from
#: 1990 alone.
#:
#: `se` is NOT stored: it is `sd / sqrt(n)` and reproduces to ten places on
#: every leg, so storing it would be a fifth literal saying what four already
#: say.
#:
#: THE MISMATCH THE ROW ALREADY MADE, kept visible rather than smoothed
#: over: the equal-weight premium is measured over 22 calendar years and
#: applied to a 75-year or 98-year cap-weighted mean. No longer equal-weight
#: record exists. `spxew` is the cross-check leg, 19 years of the index
#: itself rather than the fund, and it reads the premium 0.47 points a year
#: more negative than RSP does.
DRIFT_LEGS: dict[str, dict[str, Any]] = {
    "cw_1950": {
        "series": "^GSPC", "first_year": 1951, "last_year": 2025, "n": 75,
        "mean": 7.752456476408867, "sd": 16.233712459034702,
        "lag1": -0.09362413446089514,
        "what": "calendar-year S&P 500 price log returns, percent a year",
    },
    "cw_1927": {
        "series": "^GSPC", "first_year": 1928, "last_year": 2025, "n": 98,
        "mean": 6.081678157692151, "sd": 19.181607798611594,
        "lag1": 0.02654499849314216,
        "what": "the same over the whole ^GSPC record. The lag-1 of +0.0265 "
                "is what licenses the iid error here where the TAIL row's "
                "+0.5952 on the same span does not",
    },
    "rsp": {
        "series": "RSP", "first_year": 2004, "last_year": 2025, "n": 22,
        "mean": -0.3845924269384446, "sd": 5.832419663859103,
        "what": "calendar-year differences of RSP against ^GSPC on "
                "unadjusted closes, the equal-weight premium in price terms",
    },
    "spxew": {
        "series": "^SPXEW", "first_year": 2007, "last_year": 2025, "n": 19,
        "mean": -0.8534634431809142, "sd": 6.148950242528466,
        "what": "the same premium off the equal-weight INDEX rather than the "
                "fund; the cross-check leg, not a band input",
    },
    #: The model's own resolution at thirty seeds, which is the width's floor:
    #: two seed sds over the root of the seed count. The seed sd is the 6.5
    #: the row was derived against on 2026-09-03; `SEED_SD["index_drift_pct"]`
    #: is 9.55716 today, measured later and on the varying roster, and the
    #: band was NOT re-derived when it moved because two centre standard
    #: errors bind at both spans and the resolution term never decides an
    #: edge. `tests/test_band_derivations.py` asserts that it still does not.
    "resolution": {"model_sd": 6.5, "seeds": 30,
                   "half_width": 2.0 * 6.5 / math.sqrt(30)},
    #: Which leg pairs with which cap-weighted span to make which band.
    "bands": {"facts.REAL_MARKETS['index_drift_pct']": ("cw_1950", "rsp"),
              "facts.RULED_DRIFT_BAND": ("cw_1927", "rsp")},
    "rule": "centre = cap-weighted mean + premium mean; centre se in "
            "quadrature; half-width = max(2 * centre se, the model's "
            "resolution); each edge printed to one decimal place, which "
            "rounds to NEAREST and not outward as the window-derived bands "
            "do",
    "source": "tools/calibration/index_band.py; the figures from "
              "tradefloor-design/programme/results/longest-tape/"
              "drift-band.json, run 2026-09-14, and the 1950 leg reproduces "
              "the 2026-09-03 run REAL_MARKETS_PROVENANCE cites",
}


def drift_leg_band(cap_weighted: str, premium: str = "rsp") -> tuple[float, float]:
    """One drift band, rebuilt from `DRIFT_LEGS`.

    Returns the pair as it is printed, one decimal place, because the shipped
    constants ARE that printed pair. Rounding is to nearest here and outward
    on every window-derived band in this module; the difference is real and
    it is `index_band.py`'s `%.1f`, not an oversight to be corrected in
    passing.
    """
    try:
        cw, prem = DRIFT_LEGS[cap_weighted], DRIFT_LEGS[premium]
    except KeyError:
        raise ValidationError(
            f"no such drift leg; the legs are "
            f"{sorted(k for k, v in DRIFT_LEGS.items() if 'mean' in v)}"
        ) from None
    centre = cw["mean"] + prem["mean"]
    centre_se = math.hypot(cw["sd"] / math.sqrt(cw["n"]),
                           prem["sd"] / math.sqrt(prem["n"]))
    half = max(2.0 * centre_se, DRIFT_LEGS["resolution"]["half_width"])
    return (round(centre - half, 1), round(centre + half, 1))


#: `index_drift_pct` on the whole ^GSPC record, 1928-2025, 98 complete
#: calendar years. Adopted under `ruling-longest-tape-per-row`.
#:
#: DERIVED, and every term is the shipped derivation's with the
#: cap-weighted leg's span moved. `tools/calibration/index_band.py` hard
#: codes `SERIES = {"^GSPC": "1950-01-01", ...}` and the series begins
#: 1927-12-30, so the row had 98 complete years and used 75; no note in the
#: programme gives a reason for 1950. On 98 years the cap-weighted mean is
#: +6.0816782 with an sd of 19.1816078 and an se of 1.9376350, against
#: +7.7524565 and 1.8745077 on 75. The RSP equal-weight premium leg is
#: untouched at -0.3845924 with an se of 1.2434761, so the centre is
#: +5.6970857 with an se of 2.3023167 and the band is the centre plus and
#: minus the larger of two centre standard errors (4.6046333) and the
#: model's own resolution at thirty seeds (2.3734644), giving
#: (1.0924524, 10.3017191).
#:
#: IT MOVES NO COUNT, and that was PREDICTED TO HELP pt-v19 AND FALSIFIED.
#: pt-v19 reads 6.6238 at 252 and 6.0151 at 504; pt-v18 reads 5.7957 and
#: 5.1238. All four were already well clear of the old floor of 2.9 and all
#: four stay in. Both presets move from the lower third of the band toward
#: its middle.
#:
#: ONE LIMITATION, stated because extending the cap-weighted leg widens it:
#: the equal-weight premium is measured over 22 calendar years and applied
#: to a 98-year cap-weighted mean. The row already made that mismatch at 75
#: years. No longer equal-weight record exists, so it can be named and not
#: closed.
RULED_DRIFT_BAND: tuple[float, float] = (1.1, 10.3)

#: `fear_gauge_dn1`'s ruled band, per horizon. RULED on 2026-09-15 by
#: `ruling-nineteen-rows-with-dn3-re-derived` (design-repo verdict ledger),
#: in terms: "fear_gauge_dn1 on its new whole-tape band, [0.39, 3.03] at
#: 252 and [0.59, 2.73] at 504". Landed in the composed table on
#: 2026-09-19; until then the row sat in `RULED_UNREADABLE` waiting for a
#: ruling that had already been made.
#:
#: READ FROM THE ROW'S OWN PROVENANCE BLOCK rather than typed, so the band
#: the bar grades against is the one `FEAR_DN1_WINDOWS` derives and
#: `test_reference_windows.py` re-derives: ^VIX close change paired with
#: the same session's ^GSPC return, 1990-01-03 to 2025-07-31, the whole
#: tape the row's data supports under `ruling-longest-tape-per-row`, on the
#: `fixed` rule at t(33) and t(15). The 32-name equity panel of section 14
#: cannot carry this row, which is why it is composed in here beside the
#: two index rows and is not in `REAL_MARKETS_UNIVERSAL`.
RULED_FEAR_DN1_BAND: dict[int, tuple[float, float]] = {
    h: tuple(REAL_MARKETS_PROVENANCE["fear_gauge_dn1"]["band"]
             ["horizons"][h]["band"])
    for h in (CERTIFIED_HORIZON_DAYS, 504)
}

#: `fear_gauge_dn3`'s ruled band, the same at both horizons: the SHIPPED
#: whole-record ruler, the spread rule over the 1990-01-03 to 2026-09-02
#: windows holding five or more qualifying sessions
#: (`REAL_MARKETS_PROVENANCE['fear_gauge_dn3']['ruler']`).
#:
#: RULED on 2026-09-19. Simon's words, on being asked which of four rulers
#: the row keeps: "whatever is needed to ensure this measures as the best.
#: I want the model to dictate the performance, not alter the measurement
#: to make it appear working." Applied on the desk's own measurement
#: (`dn3derive-the-corrected-derivation-is-a-weaker-ruler-and-should-not-
#: be-adopted`, design-repo verdict ledger): over 173 retained arm readings
#: this band rejects 8; the one section 14 form that is valid at the
#: project's own last-bar anchor, [0.91, 10.83] and [0.85, 10.36], rejects
#: 5; the tighter front-anchored [2.16, 7.38] rejects 12 but is cut at an
#: anchor the rule does not use, which is the growing-series failure
#: `INDEX_TAIL_WINDOWS` names. So the shipped ruler is the strongest one
#: with a sound construction, and it is NEUTRAL ON THE COUNT: pt-v19 and
#: pt-v18 are in under every variant measured, so no choice here could
#: have made a preset appear to work. The 2026-09-15 section 14
#: re-derivation stays recorded under `['section14']` and is not adopted.
RULED_FEAR_DN3_BAND: tuple[float, float] = tuple(REAL_MARKETS["fear_gauge_dn3"])

#: The composed ruled band at the certified horizon: the fourteen shape
#: rows on the universal table, plus the four level and crisis rows that
#: have a whole-tape band of their own.
REAL_MARKETS_RULED: dict[str, tuple[float, float]] = dict(
    REAL_MARKETS_UNIVERSAL,
    index_drift_pct=RULED_DRIFT_BAND,
    index_tail_dn3_pct=RULED_TAIL_BAND,
    fear_gauge_dn1=RULED_FEAR_DN1_BAND[CERTIFIED_HORIZON_DAYS],
    fear_gauge_dn3=RULED_FEAR_DN3_BAND,
)

#: The composed ruled band at 504 bars. `corr_persistence_acf1` is HELD OUT
#: here and not carried: `BAND_BASIS` records that its universal 504 band
#: is on the walked six-window protocol and not the shipped sub-window one,
#: so it is a band for a different quantity until the row-definition ruling
#: is made. A row graded against a band for a different quantity is the
#: defect this whole block exists to end, so the row is absent and
#: `RULED_UNREADABLE` says why.
REAL_MARKETS_RULED_504: dict[str, tuple[float, float]] = {
    key: band for key, band in REAL_MARKETS_UNIVERSAL_504.items()
    if key != "corr_persistence_acf1"
}
REAL_MARKETS_RULED_504["index_drift_pct"] = RULED_DRIFT_BAND
REAL_MARKETS_RULED_504["index_tail_dn3_pct"] = RULED_TAIL_BAND
REAL_MARKETS_RULED_504["fear_gauge_dn1"] = RULED_FEAR_DN1_BAND[504]
REAL_MARKETS_RULED_504["fear_gauge_dn3"] = RULED_FEAR_DN3_BAND

#: Every graded row with NO ruled band, per horizon, and what would give it
#: one. A consumer reads this instead of inferring absence from a missing
#: key, and `envelope.score` reports these cells as UNREADABLE rather than
#: dropping them or filling them from the decade table.
#:
#: Each of these is a named ship blocker held by Simon and none of them is
#: the model missing a row.
#: A marker, not a reason. Rows carrying it at 504 take the 252-day reason
#: verbatim in the loop below this dict, so nothing that reads
#: `RULED_UNREADABLE` ever gets a cross-reference in place of the text.
AS_AT_252 = "as at 252"

RULED_UNREADABLE: dict[int, dict[str, str]] = {
    CERTIFIED_HORIZON_DAYS: {
        VIX_AR1_ROW:
            "NO BAND TABLE IN THIS LIBRARY CARRIES THE ROW, and that alone "
            "is what holds it here: REAL_MARKETS, REAL_MARKETS_UNIVERSAL "
            "and REAL_MARKETS_RULED all lack it at both horizons. The "
            "adoption is RULED AND NOT LANDED, which is the opposite of "
            "what this entry said until 2026-09-18: a band of [0.82, 1.04] "
            "at 252 and [0.90, 1.01] at 504 was derived off-library and "
            "Simon ruled it this row's ruled band on 2026-09-15 "
            "(design-repo verdict-ledger.json, ruling-vix-ar1-band-adopted, "
            "which also publishes the row FLOOR-ONLY under "
            "ruling-dead-edges-are-not-counted). The blocker this entry "
            "used to name, vix-ar1-band-not-adopted, was closed the same "
            "day by closes-vix-ar1-band-not-adopted and must not be quoted "
            "as live. What is outstanding is the table entry, tracked as "
            "the standing open dn3derive-the-ruling-names-a-vix-ar1-ruler-"
            "the-library-does-not-carry. NO BAND IS INVENTED HERE AND NONE "
            "IS READ: until the entry lands, the row is ungraded. "
            "CITATION CORRECTED: this entry cited "
            "vix-ar1-band-derivation.md section 9, and that note was never "
            "written -- it appears in no commit of either repository. The "
            "derivation's terms survive in the ledger instead, under "
            "ruling-prep-vix-ar1-separately-derived-band",
    },
    504: {
        "corr_persistence_acf1":
            "TWO independent grounds, either of which alone holds this row "
            "out. First, the universal 504 band is carried on the walked "
            "six-window protocol and not the shipped sub-window one, so it "
            "is a band for a different quantity. Second, and the stronger: "
            "its floor would go NEGATIVE at -0.38 on a statistic whose "
            "whole question is whether persistence is positive, and the "
            "band is 1.260 wide against the decade band's 0.300, a factor "
            "of 4.2. A floor below zero is also one no reading on the "
            "record comes near, and that is an EMPIRICAL statement rather "
            "than the arithmetic one this entry used to make. Corrected "
            "2026-09-14 under producerband-corr-persistence-504-is-not-a-"
            "dead-edge: an autocorrelation is bounded in [-1, 1], so -0.38 "
            "is attainable in principle and the floor is NOT dead the way "
            "vix_ar1_debiased's ceiling is dead, where debias_ar1's own "
            "bound of 1 + 4/n puts the ceiling out of reach by arithmetic. "
            "MEASURED over all 36 committed preset record cells the "
            "readings run -0.00039 (pt-v2 at 252) to 0.32927 (pt-v19 at "
            "504), and the lowest clears -0.38 by 0.3796. So switching "
            "this row's constant would not make any preset on the record "
            "pass, and it would retire "
            "ruling-gain-zero-and-the-red-row-stands by accident, since "
            "that ruling was decided on this exact statistic. The FIRST "
            "ground above is the recorded one and holds on its own. "
            "Blocker corr-persistence-504-unbanded, waiting on the "
            "row-definition ruling",
        VIX_AR1_ROW: AS_AT_252,
    },
}

# A 504-day cell's reason is RESOLVED here rather than left pointing at the
# 252-day block. `envelope.score` returns this string as the cell's own
# `unreadable` field and a 504-day record carries the 504 block alone, so a
# reason reading "as at 252" sends the reader to text the record does not
# hold. That is `row-value-carries-its-container` applied to the reason
# instead of to the value, and it costs one loop to avoid.
def _resolve_as_at_252() -> None:
    at_252 = RULED_UNREADABLE[CERTIFIED_HORIZON_DAYS]
    for row, why in list(RULED_UNREADABLE[504].items()):
        if why is AS_AT_252:
            RULED_UNREADABLE[504][row] = (
                "the same at 504 bars as at 252, and the reason there is "
                "the reason here. " + at_252[row])


_resolve_as_at_252()

#: Which EDGES of a ruled band can reject, under
#: `ruling-dead-edges-are-not-counted`: an edge is LIVE when a reading past
#: it is resolvable by the row's own instrument, and DEAD otherwise.
#:
#: WHY THIS IS DATA AND NOT A COUNT. "Nineteen of nineteen" asserts
#: nineteen rows each of which could have failed two ways. Two of them
#: could not. Publishing the total alone claims a test that was never run,
#: which is the same shape as a band named without its basis.
#:
#: `excess_kurtosis`'s floor is DEAD by arithmetic: excess kurtosis has a
#: theoretical minimum of -2 and the floor is -13.0 at 252 and -9.3 at 504,
#: so no reading exists below it. The floor is that low because the band is
#: roster-limited rather than era-limited, which is why the row is
#: published as roster-limited rather than merely one-sided. Blocker
#: excess-kurtosis-floor-cannot-reject.
#:
#: `vix_ar1_debiased` is the FLOOR-ONLY row, and it is also one of the rows
#: with no adopted ruled band. THOSE TWO FACTS CONTRADICT EACH OTHER AND
#: THE CONTRADICTION IS LEFT STANDING HERE, because resolving it is a
#: ruling and not a code change.
#:
#: Its ceiling is dead: `debias_ar1` is bounded above by `1 + 4/n` and the
#: band's ceiling sits above that bound at 252, so no reading can fall
#: outside it, and at 504 the ceiling is 0.42 of one ceiling standard error
#: away, which the row's own instrument cannot resolve. So IF the row is
#: graded, it is graded on its floor alone.
#:
#: Its band was derived OFF-LIBRARY and not from the universal panel,
#: because ^VIX is not in the 32-name equity set. This block cited that
#: derivation as `vix-ar1-band-derivation.md` section 9 until 2026-09-18;
#: no such note was ever written, in either repository, and the surviving
#: record of the derivation is the design repo's ledger entry
#: `ruling-prep-vix-ar1-separately-derived-band`. The ruling the old text
#: said was pending has LANDED -- `ruling-vix-ar1-band-adopted`,
#: 2026-09-15, which adopts the band and publishes the row floor-only --
#: but the table entry has not, so `RULED_UNREADABLE` still holds the row
#: and it is not graded at all. A row cannot be both floor-only and
#: unreadable, so the entry below carries `class: "open"` and
#: `edge_liveness_counts` reports it under whichever of the two states
#: `RULED_UNREADABLE` puts it in. Changing that is one line, in
#: `RULED_UNREADABLE`, once the band lands in the tables.
#:
#: WHAT THE PUBLISHED FORM NEEDS. "17 of 17 two-sided, 1 of 1 floor-only,
#: 1 of 1 roster-limited" needs all nineteen rows readable: the two fear
#: rows banded AND this row's derivation adopted. It is the state after
#: every level-row ruling lands, not a description of today.
BAND_EDGE_LIVENESS: dict[str, dict[str, Any]] = {
    "excess_kurtosis": {
        "low": False, "high": True, "class": "roster-limited",
        "reason": "excess kurtosis has a theoretical minimum of -2 and the "
                  "floor is below it, so no reading can fall outside; the "
                  "band is roster-limited rather than era-limited",
    },
    VIX_AR1_ROW: {
        "low": True, "high": False, "class": "open",
        "would_be": "floor-only",
        "reason": "the ceiling is dead: debias_ar1 is bounded above by "
                  "1 + 4/n and the ceiling sits past that bound at 252, and "
                  "at 504 it is 0.42 of one ceiling standard error away. "
                  "The row is nonetheless UNREADABLE today, because its "
                  "band is derived and not adopted, and a row cannot be "
                  "both. The ruling decides which",
    },
}


def ruled_band(key: str, days: Any) -> tuple[float, float] | None:
    """The ruled band for one row at one horizon, or ``None`` when it has none.

    ``None`` is a STATE and not a failure: `RULED_UNREADABLE[days][key]`
    says why, and a caller that substitutes the decade band for it has
    reintroduced the defect this module spent a day removing.
    """
    table = RULED_BY_HORIZON.get(int(days))
    if table is None:
        raise ValidationError(
            f"no ruled band set exists at {days!r} days; the horizons with "
            f"one are {sorted(RULED_BY_HORIZON)}")
    band = table.get(key)
    return None if band is None else (float(band[0]), float(band[1]))


def edge_liveness(key: str) -> dict[str, Any]:
    """Which of a row's two band edges can reject, and its published class."""
    row = BAND_EDGE_LIVENESS.get(key)
    if row is None:
        return {"low": True, "high": True, "class": "two-sided",
                "reason": "both edges are resolvable by the row's own "
                          "instrument"}
    return dict(row)


def edge_liveness_counts(days: Any) -> dict[str, int]:
    """The live/dead split of the ruled rows at one horizon, as a count each.

    The form a count is PUBLISHED in, so "19 of 19" cannot be written by
    accident: a reader gets "17 of 17 two-sided, 1 of 1 floor-only, 1 of 1
    roster-limited" and can see that two of the nineteen were tested on one
    edge.
    """
    table = RULED_BY_HORIZON[int(days)]
    unreadable = RULED_UNREADABLE.get(int(days), {})
    out: dict[str, int] = {}
    for key in sorted(set(table) | set(unreadable)):
        cls = ("unreadable" if key in unreadable
               else edge_liveness(key)["class"])
        out[cls] = out.get(cls, 0) + 1
    return out


def published_edge_form(days: Any) -> str:
    """`edge_liveness_counts` as the sentence a report prints."""
    counts = edge_liveness_counts(days)
    order = ("two-sided", "floor-only", "ceiling-only", "roster-limited",
             "unreadable")
    parts = [f"{counts[c]} of {counts[c]} {c}"
             for c in order if counts.get(c)]
    return ", ".join(parts)


#: The ruled band set per horizon, which is what a basis-aware scorer reads.
RULED_BY_HORIZON: dict[int, dict[str, tuple[float, float]]] = {
    CERTIFIED_HORIZON_DAYS: REAL_MARKETS_RULED,
    504: REAL_MARKETS_RULED_504,
}

BAND_BASIS["facts.REAL_MARKETS_RULED"] = {
    "era": "1987-06..2025-07 on the fourteen shape rows, 1928-2025 on "
           "index_drift_pct and index_tail_dn3_pct, 1990-01..2025-07 on "
           "fear_gauge_dn1, 1990-01..2026-09 on fear_gauge_dn3",
    "roster": "32 of the certified forty on the shape rows; ^GSPC and RSP "
              "on the two whole-record rows; ^VIX against ^GSPC on the "
              "two fear rows",
    "n_windows": 35,
    "rule": "fixed",
    "tolerance": BAND_RULE_FIXED_TOLERANCE,
    "multiplier": 2.111347,
    "rows": len(REAL_MARKETS_RULED),
    "composed": {
        **{k: "facts.REAL_MARKETS_UNIVERSAL" for k in REAL_MARKETS_UNIVERSAL},
        "index_drift_pct": "facts.RULED_DRIFT_BAND, 98 calendar years of "
                           "^GSPC from 1927-12-30 plus the RSP premium",
        "index_tail_dn3_pct": "facts.RULED_TAIL_BAND, 97 non-overlapping "
                              "252-return windows from 1928-04-09 with a "
                              "block-3 bootstrap error",
        "fear_gauge_dn1": "facts.RULED_FEAR_DN1_BAND[252], 33 non-crisis "
                          "252-session windows of ^VIX against ^GSPC from "
                          "1990-01-03, fixed rule at t(33), read from "
                          "REAL_MARKETS_PROVENANCE['fear_gauge_dn1']['band']",
        "fear_gauge_dn3": "facts.RULED_FEAR_DN3_BAND, the shipped "
                          "whole-record ruler: the spread rule over the ten "
                          "1990-2026 windows holding five or more qualifying "
                          "sessions, ruled 2026-09-19 on its power against "
                          "173 retained readings",
    },
    "adjustments": "clamp #1 and clamp #2 re-applied on the shape rows; the "
                   "Campbell ceiling retired as redundant. The tail row's "
                   "error bar is corrected for clustering and the drift "
                   "row's is not, because the drift row's annual returns "
                   "read a lag-1 of +0.0265 and the tail row's window "
                   "counts read +0.5952",
    "unreadable": sorted(RULED_UNREADABLE[CERTIFIED_HORIZON_DAYS]),
    "note": "the composed band each row is graded against under "
            "ruling-longest-tape-per-row. One graded row has no ruled "
            "band and is absent rather than filled from the decade table",
}

BAND_BASIS["facts.REAL_MARKETS_RULED_504"] = {
    "era": "1987-06..2025-07 on the thirteen shape rows carried, 1928-2025 "
           "on index_drift_pct and index_tail_dn3_pct, 1990-01..2025-07 on "
           "fear_gauge_dn1, 1990-01..2026-09 on fear_gauge_dn3",
    "roster": "32 of the certified forty on the shape rows; ^GSPC and RSP "
              "on the two whole-record rows; ^VIX against ^GSPC on the "
              "two fear rows",
    "n_windows": 16,
    "rule": "fixed",
    "tolerance": BAND_RULE_FIXED_TOLERANCE,
    "multiplier": 2.417688,
    "rows": len(REAL_MARKETS_RULED_504),
    "composed": {
        **{k: "facts.REAL_MARKETS_UNIVERSAL_504"
           for k in REAL_MARKETS_RULED_504
           if k not in ("index_drift_pct", "index_tail_dn3_pct",
                        "fear_gauge_dn1", "fear_gauge_dn3")},
        "fear_gauge_dn3": "facts.RULED_FEAR_DN3_BAND, the same band at both "
                          "horizons: the row is pooled over sessions and "
                          "its ruler is the shipped whole-record one, ruled "
                          "2026-09-19",
        "fear_gauge_dn1": "facts.RULED_FEAR_DN1_BAND[504], 15 non-crisis "
                          "504-session windows of ^VIX against ^GSPC from "
                          "1990-01-03, fixed rule at t(15), read from "
                          "REAL_MARKETS_PROVENANCE['fear_gauge_dn1']['band']",
        "index_drift_pct": "facts.RULED_DRIFT_BAND, the same band at both "
                           "horizons: its width is the centre's own "
                           "uncertainty and the centre's resolution does "
                           "not improve with the model's",
        "index_tail_dn3_pct": "facts.RULED_TAIL_BAND, the same band at both "
                              "horizons; facts.RULED_TAIL_BAND_504_CHECK "
                              "carries the 48-window re-derivation that "
                              "argues the carry",
    },
    "adjustments": "none on the shape rows, the same as "
                   "REAL_MARKETS_ADJUSTMENTS_504",
    "unreadable": sorted(RULED_UNREADABLE[504]),
    "note": "corr_persistence_acf1 is HELD OUT here rather than carried: "
            "its universal 504 band is on the walked six-window protocol "
            "and not the shipped sub-window one",
}

register_ruler_table(REAL_MARKETS_RULED, CERTIFIED_HORIZON_DAYS,
                     "facts.REAL_MARKETS_RULED")
register_ruler_table(REAL_MARKETS_RULED_504, 504,
                     "facts.REAL_MARKETS_RULED_504")


#: How many non-crisis real windows each band set rests on, which is what
#: sets that band's own tolerance. The 504 entry is the count for thirteen
#: of the fourteen rows; `corr_persistence_acf1` rests on four there, so a
#: gate reads the count per row through `band_windows` rather than off this
#: dict alone.
BAND_WINDOWS: dict[int, int] = {252: 9, 504: 5}

#: The one 504-bar band built on a different window count, and it is
#: recorded in `REAL_MARKETS_504` beside the band itself.
BAND_WINDOWS_EXCEPTIONS: dict[int, dict[str, int]] = {
    504: {"corr_persistence_acf1": 4},
}

#: The sampling sd of a median over the mean's, for a large sample from a
#: smooth law: sqrt(pi / 2). An identity, so it is written as one.
MEDIAN_SE_FACTOR = math.sqrt(math.pi / 2)

#: The mechanism-absent reading of each shape row -- what a model WITHOUT
#: the mechanism the row is named for reads on it -- with how it was arrived
#: at, in the three-word vocabulary this project grades a derivation by:
#: `derived` from an identity, `measured` with a residual, or
#: `undetermined`. A row whose null the run's own shape decides carries an
#: `estimator` and no value, so no such number is typed here.
#:
#: Why a graded panel needs this at all. `BAND_RULE` builds
#: `[min - s, max + s]` over the non-crisis real windows: a prediction
#: interval for ONE real year, which a fresh correct year leaves 6.5 per
#: cent of the time (`BAND_RULE_TOLERANCE`). The panel grades the MEDIAN of
#: thirty seeds, whose sampling sd is about a quarter of one seed's, so the
#: graded quantity is known about five times more precisely than the
#: interval it is judged against -- and the interval was never built to
#: exclude anything. It contains the mechanism-absent reading whenever the
#: real effect at its weak end is within about one across-year sd of the
#: null, and measured with each preset's own seed noise a null model's
#: graded median passes the shipped band with probability 1.000 on
#: `abs_return_acf20`, `corr_asymmetry` and `corr_asymmetry_lagged`, 0.92 on
#: `corr_persistence_acf1` and 0.50 on `leverage_effect`.
#:
#: A band verdict answers "could a real year read this". It cannot also
#: answer "is a model without the mechanism excluded", because one interval
#: has one width and the two questions have different scales -- real
#: across-year dispersion for the first, the protocol's own resolution at
#: thirty seeds for the second. So the panel publishes THREE counts
#: (`envelope.certify`) and this table is what the second is measured
#: against. Source: tradefloor-design/programme/band-form-design.md,
#: sections 2 and 3, 2026-09-05.
NULLS: dict[str, dict[str, Any]] = {
    "annualised_vol_pct": {
        "value": None, "kind": "undetermined",
        "derivation": "a level has no mechanism-absent reading: there is no "
                      "model of this market that lacks volatility. The row "
                      "is graded for fidelity and reported against the real "
                      "centre, and it certifies no mechanism",
    },
    "excess_kurtosis": {
        "value": 0.0, "kind": "derived",
        "derivation": "Gaussian returns have zero excess kurtosis, so a "
                      "model whose per-name returns are normal reads zero "
                      "here whatever else it does",
    },
    "return_acf1": {
        "value": 0.0, "kind": "derived",
        "derivation": "independence of successive returns. This row is the "
                      "one whose null IS the real value (-0.0055, half an "
                      "across-window standard error from zero over nine "
                      "windows), so the band certifies that the model is "
                      "NOT distinguishable from independence and a sign "
                      "test against zero would be a test for the ABSENCE "
                      "of a mechanism, which is why the row is EQUIVALENCE "
                      "and not MECHANISM",
    },
    "abs_return_acf1": {
        "value": 0.0, "kind": "derived",
        "derivation": "independence of |r| across days: with no variance "
                      "memory the absolute returns are iid and every lag "
                      "reads zero in expectation",
    },
    "abs_return_acf5": {
        "value": 0.0, "kind": "derived",
        "derivation": "as lag 1: independence of |r| across days",
    },
    "abs_return_acf20": {
        "value": 0.0, "kind": "derived",
        "derivation": "as lag 1: independence of |r| across days",
    },
    "cross_sectional_corr": {
        "value": 0.0, "kind": "derived",
        "derivation": "independence across names: with no shared factor the "
                      "mean pairwise correlation is zero in expectation",
    },
    "volume_abs_return_corr": {
        "value": 0.0, "kind": "derived",
        "derivation": "independence of volume and |r|: with no channel from "
                      "a move to the volume that answers it the correlation "
                      "is zero in expectation",
    },
    "leverage_effect": {
        "value": 0.0, "kind": "derived",
        "derivation": "symmetric response. corr(r_t, |r_t+1|) is zero for "
                      "any r_t = sigma_t z_t whose sigma reads only past "
                      "SQUARES, because the sign of r_t then carries no "
                      "information about |r_t+1| -- the Zumbach argument "
                      "this module already states beside zumbach_asymmetry",
    },
    "volume_change_acf1": {
        "value": None, "kind": "undetermined",
        "derivation": "iid volume drives the change autocorrelation toward "
                      "-0.5, but the exact value depends on the volume "
                      "noise law and deriving it would take a simulation of "
                      "that law. Not needed for any verdict: the row sits "
                      "eleven frozen seed sd from -0.5, so it is graded for "
                      "fidelity and against the real centre only",
    },
    "corr_asymmetry": {
        "value": 0.0, "kind": "derived",
        "derivation": "symmetric loading on the market factor: if names "
                      "load on the factor the same way on down days as on "
                      "up days, the difference of the two conditional mean "
                      "correlations is zero in expectation",
    },
    "corr_asymmetry_lagged": {
        "value": 0.0, "kind": "derived",
        "derivation": "as corr_asymmetry, conditioned on the PREVIOUS day's "
                      "market return instead of today's",
    },
    "sector_excess_corr": {
        "value": 0.0, "kind": "derived",
        "derivation": "no sector factor: with only a market factor the "
                      "same-sector excess over the cross-sectional mean is "
                      "zero in expectation",
    },
    "corr_persistence_acf1": {
        "value": None, "kind": "measured", "estimator": "persistence_null",
        "derivation": "the ESTIMATOR's own null, not zero. The row is the "
                      "lag-1 autocorrelation of a short series of "
                      "sub-window mean correlations, and _autocorrelation "
                      "on N iid draws is biased below zero at small N: its "
                      "median is about -0.094 at the eleven sub-windows a "
                      "252-day run yields. The MEDIAN is the null a sign "
                      "test needs, because that is the value each seed "
                      "falls either side of with probability one half. "
                      "Computed from the run's own sub-window count by "
                      "`persistence_null`, never typed, because the count "
                      "moves with the horizon: eleven at 252 model days, "
                      "twenty-three at 504",
    },
}

#: The three classes of shape row, and what each certifies. Listed rather
#: than inferred, so a row added to `SHAPE` must be placed in a class on
#: purpose; `test_facts` and `test_mechanism_gate` assert the three
#: partition `SHAPE`.
#:
#: MECHANISM rows have a constructible mechanism-absent reading, so a sign
#: test against it can fail a model that lacks the mechanism. EQUIVALENCE
#: rows are the ones whose real value IS the null: the band certifies that
#: the model is not distinguishable from it, and there is no mechanism to
#: show. LEVEL_ONLY rows have no mechanism-absent reading at all.
MECHANISM = (
    "excess_kurtosis", "abs_return_acf1", "abs_return_acf5",
    "abs_return_acf20", "cross_sectional_corr", "volume_abs_return_corr",
    "leverage_effect", "corr_asymmetry", "corr_asymmetry_lagged",
    "sector_excess_corr", "corr_persistence_acf1",
)
EQUIVALENCE = ("return_acf1",)
LEVEL_ONLY = ("annualised_vol_pct", "volume_change_acf1")

#: Mechanism rows whose verdict is REPORTED and NOT COUNTED at a given
#: horizon, against the reason, so a row cannot be quietly graded where a
#: correct model would fail it. Keyed by horizon in trading days.
#:
#: `abs_return_acf20` at 252 days is the case this exists for. The row is
#: named for a long-memory fact that is not visible inside one year on the
#: longer real record: over 34 non-crisis 252-bar windows of a 32-name
#: 1990-2025 reference the within-year lag-20 reading is +0.005, 1.1
#: across-window standard errors from zero, and a CORRECT model -- thirty
#: seeds about that centre with that dispersion -- passes the sign gate 17
#: per cent of the time. The panel's own decade reads +0.020 and would
#: certify it, so the row's certifiability depends on which decade is taken
#: as the truth, and that is not a certificate. At 504 days both references
#: read +0.030 and a correct model passes at 1.00, which is where the row
#: belongs. Two conditions travel with the move and neither is met yet: the
#: 504 fidelity band rests on five windows rather than nine, and the 504
#: reading is inflated by the year step until the opening is drawn from the
#: stationary law. Until both, reported and not counted.
MECHANISM_DIAGNOSTIC: dict[int, dict[str, str]] = {
    252: {
        "abs_return_acf20": (
            "not certifiable at 252 days: the real within-year effect is "
            "inside real year-to-year noise on the 1990-2025 reference "
            "(median +0.005 over 34 windows, 1.1 across-window standard "
            "errors from zero), so a correct model passes the gate 17 per "
            "cent of the time. Re-state at 504 days, where both references "
            "read +0.030 and a correct model passes at 1.00; diagnostic "
            "until the 504 arm and the stationary opening exist"
        ),
    },
}


def band_rule_false_alarm(n_windows: int, *, draws: int = 200_000,
                          seed: int = 20260905) -> tuple[float, float]:
    """Re-derive `BAND_RULE_TOLERANCE` for `n_windows`: (rate, its se).

    The band is built UNROUNDED, by `shared_rule`, because outward rounding
    is per-row and only ever widens: a shipped band is at most this
    tolerant, so a cut derived from this rate is at most this strict.
    """
    if n_windows < 2:
        raise ValidationError(
            f"n_windows must be at least 2 to have a band, got {n_windows}")
    if draws < 1:
        raise ValidationError(f"draws must be positive, got {draws}")
    rng = random.Random(seed)
    outside = 0
    for _ in range(draws):
        windows = [rng.gauss(0.0, 1.0) for _ in range(n_windows)]
        low, high, _ = shared_rule(windows)
        fresh = rng.gauss(0.0, 1.0)
        if fresh < low or fresh > high:
            outside += 1
    rate = outside / draws
    return rate, math.sqrt(rate * (1.0 - rate) / draws)


def band_rule_fixed_false_alarm(n_windows: int, multiplier: float, *,
                                draws: int = 200_000,
                                seed: int = 20260905) -> tuple[float, float]:
    """The `fixed` rule's false-alarm rate at `n_windows`: (rate, its se).

    The same null and the same draws as `band_rule_false_alarm`, and a
    different band: `median +/- multiplier * trimmed_sd` over the first
    `n_windows` draws, the verdict on one more.

    This exists so the `fixed` rule's tolerance is re-derivable the way the
    `spread` rule's already is. Pass `BAND_RULE_FIXED_MULTIPLIER[n]` and it
    returns `BAND_RULE_FIXED_TOLERANCE` to within the binomial error, which
    is the check that the multiplier table has not drifted from the target
    it was solved against.
    """
    if n_windows < 2:
        raise ValidationError(
            f"n_windows must be at least 2 to have a band, got {n_windows}")
    if draws < 1:
        raise ValidationError(f"draws must be positive, got {draws}")
    if not multiplier > 0.0:
        raise ValidationError(
            f"multiplier must be positive, got {multiplier}")
    rng = random.Random(seed)
    outside = 0
    for _ in range(draws):
        windows = [rng.gauss(0.0, 1.0) for _ in range(n_windows)]
        centre = statistics.median(windows)
        scale = trimmed_sd(windows)
        fresh = rng.gauss(0.0, 1.0)
        if abs(fresh - centre) > multiplier * scale:
            outside += 1
    rate = outside / draws
    return rate, math.sqrt(rate * (1.0 - rate) / draws)


def band_rule_tolerance(n_windows: int, *, rule: str = "spread") -> float:
    """The false-alarm rate of `rule`'s band built on `n_windows`, measured.

    THE RULE, NOT ONLY THE COUNT. A tolerance belongs to a band rule. The
    `spread` rule's rate follows its window count and is looked up in
    `BAND_RULE_TOLERANCE`; the `fixed` rule's is `BAND_RULE_FIXED_TOLERANCE`
    at every count, because that is the quantity `t(n)` was solved against.
    Handing a `fixed` band a `spread` tolerance -- or the reverse -- reads a
    cut off the wrong instrument and returns a plausible integer, which is
    the same wrong-ruler shape one level down.

    Raises rather than guessing for a `spread` window count nobody has
    measured: the cut a gate reads comes from here, and a tolerance
    interpolated between two measurements would be a chosen constant.
    """
    if rule not in BAND_RULES:
        raise ValidationError(
            f"{rule!r} is not a band rule this project has built a band "
            f"with; the rules are {list(BAND_RULES)}")
    if rule == "fixed":
        if n_windows not in BAND_RULE_FIXED_MULTIPLIER:
            raise ValidationError(
                f"the fixed rule's multiplier at {n_windows} windows is not "
                f"measured; measured counts are "
                f"{sorted(BAND_RULE_FIXED_MULTIPLIER)}. The TOLERANCE is "
                f"{BAND_RULE_FIXED_TOLERANCE} at every count by "
                f"construction, but a band nobody has solved t(n) for "
                f"cannot be built, so solve it with "
                f"facts.band_rule_fixed_false_alarm and record it with its "
                f"residual rather than interpolating.")
        return BAND_RULE_FIXED_TOLERANCE
    try:
        return BAND_RULE_TOLERANCE[n_windows]
    except KeyError:
        raise ValidationError(
            f"BAND_RULE's tolerance at {n_windows} windows is not measured; "
            f"measured counts are {sorted(BAND_RULE_TOLERANCE)}. Run "
            f"facts.band_rule_false_alarm({n_windows}) and record it with "
            f"its residual rather than interpolating."
        ) from None


def band_windows(key: str, horizon_days: int) -> int:
    """How many non-crisis real windows `key`'s band at this horizon rests on."""
    if horizon_days not in BAND_WINDOWS:
        raise ValidationError(
            f"no band set is recorded at {horizon_days} days; recorded "
            f"horizons are {sorted(BAND_WINDOWS)}")
    return BAND_WINDOWS_EXCEPTIONS.get(horizon_days, {}).get(
        key, BAND_WINDOWS[horizon_days])


def binomial_two_sided(n: int, k: int) -> float:
    """The exact two-sided probability of a count as extreme as `k` of `n`.

    Under `Bin(n, 1/2)`: twice the smaller tail, capped at one. Exact, from
    `math.comb`, so the gate needs no normal approximation and no standard
    error estimator anywhere.
    """
    if n < 1 or not 0 <= k <= n:
        raise ValidationError(f"need 0 <= k <= n and n >= 1, got k={k}, n={n}")
    total = 2 ** n
    upper = sum(math.comb(n, j) for j in range(k, n + 1)) / total
    lower = sum(math.comb(n, j) for j in range(0, k + 1)) / total
    return min(1.0, 2.0 * min(upper, lower))


def sign_cut(n: int, tolerance: float) -> int:
    """The seeds-on-the-real-side count that reads SHOWN at `tolerance`.

    The smallest `k` above `n / 2` whose exact two-sided binomial
    probability is nearest `tolerance`. Nothing is chosen: `tolerance` is
    `BAND_RULE`'s own false-alarm rate, so the mechanism half of the
    instrument is as tolerant of a correct model as the fidelity half, and
    the integer comes out of the binomial rather than off a table.
    """
    if n < 2:
        raise ValidationError(
            f"a sign test needs at least two readings, got {n}")
    if not 0.0 < tolerance < 1.0:
        raise ValidationError(
            f"tolerance must be a probability strictly inside (0, 1), got "
            f"{tolerance}")
    best: tuple[float, int] | None = None
    for k in range(n // 2 + 1, n + 1):
        distance = abs(binomial_two_sided(n, k) - tolerance)
        if best is None or (distance, k) < best:
            best = (distance, k)
    assert best is not None
    return best[1]


@functools.lru_cache(maxsize=None)
def persistence_null(sub_windows: int, *, draws: int = 50_000,
                     seed: int = 20260905) -> float:
    """`corr_persistence_acf1`'s mechanism-absent reading at `sub_windows`.

    The MEDIAN of `_autocorrelation(x, 1)` over `draws` samples of
    `sub_windows` iid standard normals -- the estimator's own null, computed
    by calling the estimator the panel calls rather than a re-derivation of
    it, so a change to `_autocorrelation` moves the null with it.

    The median rather than the mean, because the median is the value each
    seed falls either side of with probability one half, which is what an
    exact sign test's size rests on. The mean is close to `-1 / N` and the
    two differ by about 0.004 at eleven sub-windows.

    Monte Carlo, so it carries an error: the sampling sd of the median is
    about `MEDIAN_SE_FACTOR * 0.259 / sqrt(draws)`, 0.0015 at the default
    draws, which no per-seed reading on any arm on record sits within.
    """
    if sub_windows < 3:
        raise ValidationError(
            f"_autocorrelation needs more than lag + 1 values, got "
            f"{sub_windows}")
    rng = random.Random(seed)
    return statistics.median(
        _autocorrelation([rng.gauss(0.0, 1.0) for _ in range(sub_windows)], 1)
        for _ in range(draws)
    )


def sub_window_count(horizon_days: int) -> int:
    """How many `CORR_PERSISTENCE_WINDOW` sub-windows a run of this length has.

    Eleven at 252 days, not twelve: the panel differences prices into
    returns first, so a 252-bar run carries 251 returns and
    `251 // 21 = 11`. The twelve in the older comment counted BARS.
    """
    if horizon_days < 2:
        raise ValidationError(
            f"horizon_days must be at least 2 to have a return, got "
            f"{horizon_days}")
    return (horizon_days - 1) // CORR_PERSISTENCE_WINDOW


def null_value(key: str, *, horizon_days: int | None = None) -> float | None:
    """`key`'s mechanism-absent reading, resolving any estimator it names."""
    entry = NULLS.get(key)
    if entry is None:
        raise ValidationError(
            f"no null recorded for {key!r}; recorded rows are "
            f"{sorted(NULLS)}")
    if entry.get("estimator") == "persistence_null":
        if horizon_days is None:
            raise ValidationError(
                f"{key}'s null is computed from the run's own sub-window "
                "count, so horizon_days is required")
        return persistence_null(sub_window_count(horizon_days))
    return entry["value"]


def real_windows(key: str, *,
                 horizon_days: int = TRADING_DAYS_PER_YEAR
                 ) -> tuple[float, ...] | None:
    """`key`'s non-crisis real readings, one per window, or None if unrecorded.

    Every one of the fourteen shape rows has one, at both certified
    horizons. The four correlation-structure rows joined the 252-bar table
    on 2026-09-05 and the 504-bar tables landed with the scoring rule; the
    sentence this docstring used to carry, that four rows have no record,
    was stale from the first of those and is the reason it is written here
    as a claim a test re-derives rather than as prose.

    Two corpora, not one. `INDEX_TAIL_WINDOWS` holds the index tail row's
    real side: a cap-weighted INDEX over thirty-five years, where
    `REAL_MARKETS_WINDOWS` holds per-NAME readings over one decade of forty
    large caps. The tail row is measured at both certified horizons there
    and its windows are read at the one asked for.

    THE HORIZON IS REFUSED BY NAME, not ignored. The shape panel has a
    per-window record at the two horizons in `WINDOW_HORIZONS`, 252 from
    `REAL_MARKETS_WINDOWS` and 504 from `REAL_MARKETS_WINDOWS_504` (with
    `corr_persistence_acf1` reading its own `REAL_PERSISTENCE_WINDOWS_504`,
    for the reason that table gives). Any other horizon raises rather than
    handing back the 252-bar readings under a longer window's name, which
    is what this function did before the 504 table existed: clustering at
    lag 20 reads +0.005 over 252 bars and +0.030 over 504 on the same
    reference, so the answer would have been wrong and plausible at once.
    """
    if key in INDEX_TAIL_WINDOWS["rows"]:
        if int(horizon_days) not in INDEX_TAIL_WINDOWS["windows"]:
            return None
        return index_tail_rates(horizon_days)
    if key == VIX_AR1_ROW:
        # DEBIASED, one per window, because the MODEL's row is debiased and
        # section 1.5's whole finding was a debiased reading graded against
        # a raw one. `debias_ar1` is affine and increasing, so the median of
        # these IS `REAL_VIX_AR1[horizon_days]` to the last bit -- asserted
        # in `tests/test_vix_ar1_ruler.py` -- and the ruler cannot drift away
        # from the rule by being computed twice.
        #
        # The error is then this module's own median estimator,
        # MEDIAN_SE_FACTOR * trimmed_sd / sqrt(n), the same one the fourteen
        # shape rows use, and NOT the 0.0120 bootstrap figure
        # REAL_VIX_AR1_PROVENANCE records: two rows summed in one `S` whose
        # errors come from different estimators are not comparable terms.
        # The two disagree by 13 per cent at 252 (0.010420 against 0.0120)
        # and 2.6 at 504, so this is a choice and it is recorded here.
        raws = REAL_VIX_AR1_WINDOWS.get(int(horizon_days))
        if raws is None:
            return None
        return tuple(debias_ar1(r, int(horizon_days)) for r in raws)
    if int(horizon_days) not in WINDOW_HORIZONS:
        raise ValidationError(
            f"no per-window real record for {key!r} at {horizon_days} days; "
            f"the shape panel's windows are recorded at "
            f"{sorted(WINDOW_HORIZONS)} days, and a row's dispersion across "
            "real years is a property of the window length, so it is refused "
            "here rather than answered from another horizon's table")
    if int(horizon_days) == TRADING_DAYS_PER_YEAR:
        table = REAL_MARKETS_WINDOWS
    elif key in REAL_PERSISTENCE_WINDOWS_504["values"]:
        table = REAL_PERSISTENCE_WINDOWS_504
    else:
        table = REAL_MARKETS_WINDOWS_504
    values = table["values"].get(key)
    if values is None:
        return None
    crisis = table["crisis_index"]
    return tuple(v for i, v in enumerate(values) if i != crisis)


def real_centre(key: str, *,
                horizon_days: int = TRADING_DAYS_PER_YEAR) -> float | None:
    """The real reading for `key` a correct model aims at, by the row's estimator.

    The MEDIAN over the windows for a row graded as a median, which is every
    row but one, and the MEAN for a row graded as a mean or a pooled rate --
    the estimator has to follow the quantity on the real side too, or the
    band's centre and the value graded against it are two different
    statistics. On the index tail row the two differ by 3.1x, 1.213 against
    0.397, for the reason `AGGREGATE` gives.

    From the per-window table where it exists, and from the middle of
    `REAL_MARKETS_PROVENANCE`'s (min, median, max) triple otherwise -- the
    same quantity, recorded to three places. A provenance entry whose
    `windows` is not an ORDERED triple is not a triple of that kind and is
    refused rather than read positionally: `abs_return_acf5` records eight
    values there and `index_drift_pct` records three that are not a range.
    """
    windows = real_windows(key, horizon_days=horizon_days)
    if windows is not None:
        if AGGREGATE.get(key) in ("mean", "pooled_rate"):
            return statistics.fmean(windows)
        return statistics.median(windows)
    # The fallback is a MEDIAN -- that is what the middle of the triple is --
    # so it answers for a row graded as a median and for no other. Returning
    # it for a mean row would hand back a different statistic under the same
    # name, and on the index tail row the two differ by a factor of three
    # (0.397 against 1.213). It is also the horizon guard: a row whose
    # windows are absent AT THIS HORIZON must not be answered from a triple
    # summarising another one.
    if AGGREGATE.get(key) in ("mean", "pooled_rate"):
        return None
    recorded = REAL_MARKETS_PROVENANCE.get(key, {}).get("windows")
    if (recorded is not None and len(recorded) == 3
            and recorded[0] <= recorded[1] <= recorded[2]):
        return float(recorded[1])
    return None


def real_centre_se(key: str, *,
                   horizon_days: int = TRADING_DAYS_PER_YEAR) -> float | None:
    """The standard error of `key`'s real centre, or None where undetermined.

    `MEDIAN_SE_FACTOR * trimmed_sd(windows) / sqrt(len(windows))` for a row
    whose centre is a median, on the same trimmed sd `BAND_RULE` prices its
    width in. For a row whose centre is a MEAN it is the ordinary
    `sd / sqrt(n)`: no median factor, because the estimator is not a median,
    and no trim, because a centre that counts the extreme window and a scale
    that pretends it is not in the draw are inconsistent. On the index tail
    row that is the difference between 0.3991 and 0.2452, and the larger one
    is the honest number -- one window of thirty-five carries a third of the
    events.

    None for a row with no per-window record, which since the 504-bar
    windows landed is the level row and the two fear rows and no shape row
    at either certified horizon: a centre recorded to three places gives
    the centre, not its dispersion, and inverting the band edges for it
    would recover an interval a rounding quantum wide and call it a number.
    Those three carry a `centre_se` in `REAL_MARKETS_PROVENANCE` where one
    has been derived, and `rule_row` -- not this function -- is what reads
    it: this is the WINDOW dispersion, and a recorded error is a different
    provenance under the same name.
    """
    windows = real_windows(key, horizon_days=horizon_days)
    if windows is None:
        return None
    if AGGREGATE.get(key) in ("mean", "pooled_rate"):
        return statistics.stdev(windows) / math.sqrt(len(windows))
    return MEDIAN_SE_FACTOR * trimmed_sd(windows) / math.sqrt(len(windows))


def real_centre_df(key: str, *,
                   horizon_days: int = TRADING_DAYS_PER_YEAR) -> int | None:
    """The degrees of freedom of `key`'s real centre, or None where unrecorded.

    For a row with a per-window record it is derived from the windows and
    nothing is chosen: `n - 2` where the centre is a median and its scale a
    TRIMMED sd, because the trim drops one window and the sd spends one;
    `n - 1` where the centre is a mean or a pooled rate, whose scale is the
    plain sd and spends one. The two are the estimators `real_centre` and
    `real_centre_se` actually use, read off the same `AGGREGATE` entry, so
    the count cannot drift from the scale it belongs to.

    For the three rows with no window table -- the level row and the two
    fear rows -- it is `centre_df` in `REAL_MARKETS_PROVENANCE`, recorded
    beside the one-line derivation that produced it. The index tail row
    records one too and does NOT read it here: its windows exist at both
    horizons, so a fixed 34 would be right at 252 and wrong at 504, where
    seventeen windows give 16. `tests/test_scoring_rule.py` asserts the
    recorded 34 equals the derived one at 252, which is what keeps the
    record honest without letting it answer for a horizon it was not
    measured at.
    """
    windows = real_windows(key, horizon_days=horizon_days)
    if windows is not None:
        if AGGREGATE.get(key) in ("mean", "pooled_rate"):
            return len(windows) - 1
        return len(windows) - 2
    recorded = REAL_MARKETS_PROVENANCE.get(key, {}).get("centre_df")
    return None if recorded is None else int(recorded)


def rule_row(key: str, *, horizon_days: int = TRADING_DAYS_PER_YEAR,
             require: bool = True) -> dict[str, Any]:
    """The TAPE side of the scoring rule for one row: centre, error, and df.

    `{"row", "horizon_days", "centre", "se", "df", "estimator", "source"}`.
    The objective in `tradefloor.loss.scoring_rule` reads exactly this and
    nothing else about real markets, so what the rule aims at is one
    function with one contract rather than four call sites each deciding
    which table to open.

    THE ESTIMATOR FOLLOWS THE ROW, which is the rule the note this
    implements calls the AR1 ruler's: the same estimator the model's row is
    graded by, applied to the tape at the same window length as the horizon
    graded. A median row takes the median of the non-crisis windows at that
    horizon with `real_centre_se`'s trimmed-sd error; a mean or pooled-rate
    row takes the mean and the plain `sd / sqrt(n)`; and a row with no
    window table takes the centre, error and df recorded in
    `REAL_MARKETS_PROVENANCE`, whose `centre_estimator` names the quantity.
    A centre by a different estimator than the row it grades is the error
    that read a pooled model median against a median of window medians for
    months.

    THIS IS THE GUARD, and what it refuses is a row whose tape side is not
    all three of a centre, a standard error and a degrees of freedom. On
    the shipped table it refuses nothing at either horizon: the last row
    without a scale was `fear_gauge_dn3`, whose centre had been on the
    record as the pooled tape median, +5.73 over the 107 sessions since
    1990, with no error beside it until the window-block bootstrap ran
    (2026-09-09, `REAL_MARKETS_PROVENANCE["fear_gauge_dn3"]["centre_se"]`,
    0.6539 on 19 degrees of freedom) -- four days in which every score
    taken was a sum over eighteen of nineteen rows and said so. What
    would trip the guard again is a row added to `REAL_MARKETS` with a
    centre and no window table and no recorded `centre_se` or
    `centre_df`, or a window table too short to carry a trimmed sd. The
    refusal names the row and which of the three is missing, because a
    scoring rule that silently dropped it would publish a sum over
    eighteen rows under the name of a sum over nineteen.

    `require=False` returns the same dict with `None` in place of whatever
    is missing and a `missing` tuple naming it, which is how
    `scoring_rule` builds its `blind` list with a reason rather than by
    reading an exception's message.

    R7, THE ERROR BARS, ruled by Simon on 2026-09-06 and recorded in
    `tradefloor-design/programme/RULINGS-2026-09-06.md`: `se` is the
    WITHIN-DECADE standard error of the 2015-2025 reference panel, and the
    measured disagreement between that decade and the 32-name 1990-2025
    reference -- one to three `se` on three rows, recorded in
    `centre_distance`'s docstring -- is NOT folded into it. Widening `se`
    by that gap would make the objective honest about the decade at the
    cost of discrimination on exactly the rows where it bites. So a fit to
    this centre is a fit to the decade, and that is now a decision rather
    than a default: the limit is stated here because this is where the
    number enters.

    EVERY VALUE IS DERIVED FROM THE WINDOWS, and no stored summary is read
    from any file. `centre`, `se` and `df` come from `real_centre`,
    `real_centre_se` and `real_centre_df`, which read the window tables and
    `trimmed_sd` -- the median-centred trim `BAND_RULE` names. The design
    repository's `bands-504-noncrisis.json` carries a `trimmed_sd` field
    per row that was written on 2026-08-22 and never regenerated after the
    trim centre was named on 2026-09-04, so its values are the superseded
    mean-centred ones; on `return_acf1` the two drop different windows and
    the standard error differs by 2.6 per cent. Reading such a field would
    take the old answer from a file whose own tool has since been fixed,
    which is why nothing here reads one. The three rows with no window
    table read `centre`, `centre_se` and `centre_df` from
    `REAL_MARKETS_PROVENANCE`, where the record IS the measurement and is
    labelled as such.
    """
    windows = real_windows(key, horizon_days=horizon_days)
    prov = REAL_MARKETS_PROVENANCE.get(key, {})
    if windows is not None:
        rate = AGGREGATE.get(key) in ("mean", "pooled_rate")
        out: dict[str, Any] = {
            "row": key,
            "horizon_days": int(horizon_days),
            "centre": real_centre(key, horizon_days=horizon_days),
            "se": real_centre_se(key, horizon_days=horizon_days),
            "df": real_centre_df(key, horizon_days=horizon_days),
            "estimator": (
                # The VIX row's windows are consecutive blocks of the whole
                # tape with no crisis exclusion, so calling them non-crisis
                # would describe a filter that was never applied. They are
                # also debiased before the median, which is the property
                # section 1.5 is about, so the string says so.
                f"median of the {len(windows)} {int(horizon_days)}-day "
                f"windows, each debiased by facts.debias_ar1 at "
                f"{int(horizon_days)} before the median, with "
                f"MEDIAN_SE_FACTOR * trimmed_sd / sqrt({len(windows)}) as "
                f"its error"
                if key == VIX_AR1_ROW else
                f"{'mean' if rate else 'median'} of the {len(windows)} "
                f"non-crisis {int(horizon_days)}-day windows, with "
                f"{'sd' if rate else 'MEDIAN_SE_FACTOR * trimmed_sd'}"
                f" / sqrt({len(windows)}) as its error"),
            "source": ("facts.REAL_VIX_AR1_WINDOWS"
                       if key == VIX_AR1_ROW else
                       "facts.INDEX_TAIL_WINDOWS"
                       if key in INDEX_TAIL_WINDOWS["rows"] else
                       "facts.REAL_MARKETS_WINDOWS"
                       if int(horizon_days) == TRADING_DAYS_PER_YEAR else
                       "facts.REAL_PERSISTENCE_WINDOWS_504"
                       if key in REAL_PERSISTENCE_WINDOWS_504["values"] else
                       "facts.REAL_MARKETS_WINDOWS_504"),
        }
    else:
        if key not in REAL_MARKETS:
            raise ValidationError(
                f"{key!r} is not a graded row; graded rows are "
                f"{sorted(REAL_MARKETS)}")
        out = {
            "row": key,
            "horizon_days": int(horizon_days),
            "centre": prov.get("centre", real_centre(
                key, horizon_days=horizon_days)),
            "se": prov.get("centre_se"),
            "df": real_centre_df(key, horizon_days=horizon_days),
            "estimator": prov.get("centre_estimator",
                                  "recorded in REAL_MARKETS_PROVENANCE"),
            "source": f"facts.REAL_MARKETS_PROVENANCE[{key!r}]",
        }
    missing = tuple(f for f in ("centre", "se", "df") if out[f] is None)
    out["missing"] = missing
    if missing and require:
        raise ValidationError(
            f"the scoring rule has no tape side for {key!r} at "
            f"{int(horizon_days)} days: {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not on the record. "
            + (prov.get("centre_se_pending", "")
               or f"Derive it and record it beside {key!r} in "
                  "REAL_MARKETS_PROVENANCE, or in the window table for its "
                  "horizon; the rule never substitutes a neighbour's."))
    return out


def median_se(values: Sequence[float]) -> float:
    """The sampling sd of the median of `values`, normal approximation.

    `MEDIAN_SE_FACTOR * sd / sqrt(n)`. Reported beside the bootstrap and
    never used as a gate: the two disagree by up to a factor of two on the
    skewed rows, and a verdict that turns on which estimator was picked is
    a chosen constant wearing a derivation's clothes.
    """
    if len(values) < 2:
        raise ValidationError(
            f"need at least two readings for a spread, got {len(values)}")
    return MEDIAN_SE_FACTOR * statistics.stdev(values) / math.sqrt(len(values))


def median_se_bootstrap(values: Sequence[float], *, draws: int = 2000,
                        seed: int = 20260905) -> float:
    """The sampling sd of the median of `values`, by resampling them.

    `draws` defaults to the 2000 this repository's other bootstrap already
    uses (`tools/calibration/calibrate.py`, `bootstrap_spread`). Reported,
    never a gate, for the reason in `median_se`.
    """
    values = list(values)
    if len(values) < 2:
        raise ValidationError(
            f"need at least two readings to resample, got {len(values)}")
    if draws < 2:
        raise ValidationError(f"draws must be at least 2, got {draws}")
    rng = random.Random(seed)
    n = len(values)
    return statistics.stdev(
        statistics.median(rng.choices(values, k=n)) for _ in range(draws)
    )


def centre_multiplier(tolerance: float) -> float:
    """The two-sided normal multiplier with false-alarm rate `tolerance`.

    1.846 at `BAND_RULE`'s nine-window tolerance. The CENTRE diagnostic's
    threshold, so that it too is as tolerant of a correct model as the
    fidelity band already is.
    """
    if not 0.0 < tolerance < 1.0:
        raise ValidationError(
            f"tolerance must be a probability strictly inside (0, 1), got "
            f"{tolerance}")
    return -statistics.NormalDist().inv_cdf(tolerance / 2.0)


def mechanism_verdict(values: Sequence[float], key: str, *,
                      horizon_days: int = TRADING_DAYS_PER_YEAR,
                      bootstrap_draws: int = 2000,
                      bootstrap_seed: int = 20260905) -> dict[str, Any]:
    """Is a model WITHOUT `key`'s mechanism excluded by these per-seed readings?

    An exact sign test of the per-seed readings against the row's
    mechanism-absent reading, at the tolerance `BAND_RULE` itself carries.
    `k` seeds sit on the real side of the null; SHOWN at `k >= sign_cut`,
    REVERSED at `k <= n - sign_cut`, NOT SHOWN between. REVERSED is a
    verdict of its own and not a shade of "not shown": a certified reversed
    mechanism is the failure the leverage row's inward clamp exists to
    prevent, and on that row alone.

    Deliberately not a `z_0 >= multiplier` test. The two standard-error
    estimators for a thirty-seed median disagree by up to a factor of two on
    the skewed rows and two verdicts on the shipped default flip with the
    choice, so the gate is the exact, distribution-free test that needs
    neither -- it IS the order-statistic confidence interval of the median
    excluding the null -- and both standard errors are reported beside it as
    effect sizes. What that costs is about 64 per cent of a z-test's power
    on a Gaussian row, which still leaves a correct model certified at 0.95
    to 1.00 on every row certifiable at 252 days.

    `counted` is False for a row this horizon reports and does not grade
    (`MECHANISM_DIAGNOSTIC`), which is a state distinct from any verdict:
    the row still gets one, and it does not enter the count.

    At a horizon other than the one `REAL_MARKETS_WINDOWS` was measured at,
    the only thing taken from the real side is the SIGN of the effect, which
    is a property of the market rather than of the window: every row's real
    median has the same sign at 252 and 504 bars on both references
    (band-form-design 5d, follow-ups 4 and 5). The magnitudes are not used
    here, and `centre_distance` refuses to use them across horizons.
    """
    if key not in MECHANISM:
        raise ValidationError(
            f"{key!r} certifies no mechanism, so a sign test on it would be "
            f"a test against a null nothing derives. Mechanism rows are "
            f"{sorted(MECHANISM)}; {key!r} is "
            + ("EQUIVALENCE, whose real value IS its null"
               if key in EQUIVALENCE else
               "LEVEL_ONLY, which has no mechanism-absent reading"
               if key in LEVEL_ONLY else "not a shape row"))
    values = [v for v in values if v is not None]
    if len(values) < 2:
        raise ValidationError(
            f"a sign test on {key} needs at least two per-seed readings, got "
            f"{len(values)}")

    null = null_value(key, horizon_days=horizon_days)
    centre = real_centre(key)
    if null is None or centre is None:
        raise ValidationError(
            f"{key}'s null or real centre is undetermined, so no real "
            "direction can be derived for a sign test")
    direction = 1.0 if centre >= null else -1.0

    n = len(values)
    windows = band_windows(key, horizon_days)
    tolerance = band_rule_tolerance(windows)
    cut = sign_cut(n, tolerance)
    k = sum(1 for v in values if direction * (v - null) > 0)
    if k >= cut:
        verdict = "shown"
    elif k <= n - cut:
        verdict = "reversed"
    else:
        verdict = "not shown"

    median = statistics.median(values)
    se_normal = median_se(values)
    se_boot = median_se_bootstrap(values, draws=bootstrap_draws,
                                  seed=bootstrap_seed)
    signed = direction * (median - null)
    diagnostic = MECHANISM_DIAGNOSTIC.get(horizon_days, {}).get(key)
    return {
        "row": key,
        "n": n,
        "null": null,
        "null_kind": NULLS[key]["kind"],
        "direction": direction,
        "k": k,
        "cut": cut,
        "tolerance": tolerance,
        "band_windows": windows,
        "p": binomial_two_sided(n, k),
        "verdict": verdict,
        "at_the_cut": k == cut or k == n - cut,
        "median": median,
        # Effect sizes, both estimators, never the gate.
        "se_normal": se_normal,
        "se_bootstrap": se_boot,
        "z0_normal": signed / se_normal if se_normal else None,
        "z0_bootstrap": signed / se_boot if se_boot else None,
        "counted": diagnostic is None,
        "diagnostic": diagnostic,
    }


#: The rows the STRUCTURAL gate grades, and today it is one row.
#:
#: A structural row is one a fidelity band cannot judge because it HAS NO
#: BAND -- `RULED_UNREADABLE` holds `vix_ar1_debiased` at both horizons and
#: no band table in this library carries it -- while its tape ruler is on
#: the record in full: `REAL_VIX_AR1_WINDOWS` is 35 per-window readings at
#: 252 and 17 at 504. A row with a centre, an error and no width is exactly
#: the shape a sign test reads, and `structure_verdict` is that test.
#:
#: WHY IT IS A SEPARATE ROSTER AND NOT AN ELEVENTH MECHANISM ROW. Simon's
#: ruling, `ruling-structural-rows-go-in-a-second-gate-beside-the-panel-not-
#: mixed-into-it`: a second gate BESIDE the panel, with no structural row
#: mixed into it. The two gates ask different questions and their nulls are
#: different objects -- `MECHANISM`'s null is the model WITHOUT the
#: mechanism (`NULLS`), and this one's reference is the TAPE ITSELF -- so a
#: count that added them would be a count of two things.
#:
#: WHAT THE GATE COSTS, stated where the roster is declared rather than left
#: for a reader to find out by failing it. The sign test has no width, and
#: the width has not gone: it has become the assumption that the tape's
#: centre is exact. At 30 seeds the test's own 50-per-cent-power offset is
#: 0.4713 per-seed sd, and this row's tape se is 0.010997 -- 1.29 times that
#: resolution, so the centre's error is NOT small against what the gate can
#: see, and the gate's false-alarm rate rises with seed count while the
#: tape's error stays put. `programme/widthless-design.md` derives both
#: numbers and refuses this form as a SOUNDNESS test on three other
#: candidate rows for exactly that reason. It is admitted here on the one
#: row whose per-window tape distribution exists, as a NON-REGRESSION gate
#: and not as a soundness certificate: what it protects is a reading a
#: preset once had and must not lose, which is a comparison between two
#: models on one ruler and does not need the ruler to be exact.
STRUCTURE: tuple[str, ...] = (VIX_AR1_ROW,)


def structure_verdict(values: Sequence[float], key: str, *,
                      horizon_days: int = TRADING_DAYS_PER_YEAR
                      ) -> dict[str, Any]:
    """Do these per-seed readings straddle the real tape's centre?

    THE WIDTHLESS FORM. `mechanism_verdict` signs the readings against the
    row's mechanism-absent null and asks whether the model is on the tape's
    SIDE of it. This asks the other question, the one a band would answer if
    the row had one: is the model's reading centred on the tape at all. The
    reference is `real_centre(key)` itself, the test is the same exact,
    distribution-free sign test at the same tolerance `BAND_RULE` carries,
    and it is TWO-SIDED because a model above the tape and a model below it
    are both off it.

      k          per-seed readings strictly ABOVE the tape centre
      cut        `sign_cut(n, tolerance)` -- 21 at n = 30, tolerance 0.06486
      REFUSED    `k >= cut` (the model sits high) or `k <= n - cut` (low)
      PASS       between them: the readings straddle the centre and the run
                 is not distinguishable from one centred on the tape

    A PASS IS NOT A CERTIFICATE THAT THE MODEL IS NEAR THE TAPE, and the
    asymmetry is the whole limit of the form. Three tape standard errors
    past the centre and thirty seeds read the same `k`; the test says the
    model sits on one side of a POINT more often than chance, never that it
    sits close to it. `STRUCTURE` records what that costs in full.

    `side` names which way a refusal went, because "high" and "low" are
    different repairs and a bare REFUSED would waste the reading. `at_the_
    cut` marks `k` exactly on either boundary, where one seed decides the
    verdict -- the shipped default sits there at 252 and a reader must be
    able to see that without recomputing the test.

    `se_real` is carried beside the verdict and is NOT a gate: it is the
    tape centre's own standard error, and the gap between it and the test's
    resolution is what `STRUCTURE` warns about. A reader comparing a
    refusal's `offset` with `se_real` can see whether the model is off the
    tape by more than the tape is known to.
    """
    if key not in STRUCTURE:
        raise ValidationError(
            f"{key!r} is not a structural row, so a sign test against the "
            f"tape's centre is not the test this library runs on it. "
            f"Structural rows are {sorted(STRUCTURE)}; a graded row is "
            f"scored by `envelope.score` against its band and, where it "
            f"certifies a mechanism, by `mechanism_verdict` against its null")
    values = [v for v in values if v is not None]
    if len(values) < 2:
        raise ValidationError(
            f"a sign test on {key} needs at least two per-seed readings, got "
            f"{len(values)}")

    centre = real_centre(key, horizon_days=horizon_days)
    if centre is None:
        raise ValidationError(
            f"{key}'s real centre is undetermined at {horizon_days} days, so "
            f"there is no point to sign the readings against. The horizons "
            f"with a per-window tape record are {sorted(REAL_VIX_AR1_WINDOWS)}")

    n = len(values)
    # The row's own band-window count and the tolerance that count carries,
    # by the same route `mechanism_verdict` takes, so the two gates are as
    # tolerant of a correct model as each other and as the band is. Nothing
    # is chosen here: nine windows at 252 give 0.06486 and a cut of 21 at
    # thirty seeds.
    windows = band_windows(key, horizon_days)
    tolerance = band_rule_tolerance(windows)
    cut = sign_cut(n, tolerance)
    k = sum(1 for v in values if v > centre)
    if k >= cut:
        verdict, side = "refused", "above"
    elif k <= n - cut:
        verdict, side = "refused", "below"
    else:
        verdict, side = "pass", None

    median = statistics.median(values)
    return {
        "row": key,
        "n": n,
        "horizon_days": horizon_days,
        "real_centre": centre,
        "se_real": real_centre_se(key, horizon_days=horizon_days),
        "tape_windows": len(REAL_VIX_AR1_WINDOWS.get(horizon_days, ())),
        "k": k,
        "cut": cut,
        "tolerance": tolerance,
        "band_windows": windows,
        "p": binomial_two_sided(n, k),
        "verdict": verdict,
        "side": side,
        "at_the_cut": k == cut or k == n - cut,
        "median": median,
        # Effect size, never the gate, and signed so a reader can see which
        # way a refusal went without re-reading `side`.
        "offset": median - centre,
        "se_normal": median_se(values),
    }


#: The tape's RISE in `vix_ar1_debiased` from the one-year window to the
#: two-year one: the second gate's one verdict since Simon's ruling of
#: 2026-09-21 (design repo, `ruling-the-second-gate-grades-the-rise-...`).
#:
#: WHY A RISE AND NOT TWO CENTRES. The debiased lag-1 autocorrelation of
#: the real VIX reads `REAL_VIX_AR1[252]` on 35 one-year windows and
#: `REAL_VIX_AR1[504]` on 17 two-year ones, about three hundredths higher,
#: and the Marriott-Pope term explains none of the debiased rise. Graded
#: one horizon at a time the row read REFUSED-ABOVE at 252 and
#: REFUSED-BELOW at 504 on the same model, which is two contradictory
#: verdicts on one fact; graded as the rise it is one measurement.
#: `structure_rise_verdict` is that measurement. The per-horizon readings
#: stay on the record by name.
#:
#: WHY THE TAPE'S RISE IS THE PAIRED ONE, corrected 2026-09-21 (design
#: repo, `ptv19gjr-registration.md`). The model's statistic is PAIRED: each
#: seed's two-year reading minus its own first year, because the 504-day
#: run contains the 252-day one. The tape target this module first carried
#: was the difference of two INDEPENDENT window medians, and on the tape's
#: own 17 two-year blocks the paired estimator reads less than half of
#: that: a block that is calm in its first year and crises in its second
#: rises a lot, and most blocks do not. Same estimator both sides, or the
#: ruler and the row are two quantities again. The 17 paired readings are
#: derived from the window records already here: every other one-year
#: window is the first half of a two-year one (both cuts are front-anchored
#: from the same first close).
REAL_VIX_AR1_PAIRED_RISES: tuple[float, ...] = tuple(
    debias_ar1(r504, 504) - debias_ar1(REAL_VIX_AR1_WINDOWS[TRADING_DAYS_PER_YEAR][2 * i], TRADING_DAYS_PER_YEAR)
    for i, r504 in enumerate(REAL_VIX_AR1_WINDOWS[504])
)
assert 2 * len(REAL_VIX_AR1_WINDOWS[504]) <= len(REAL_VIX_AR1_WINDOWS[TRADING_DAYS_PER_YEAR]), (
    "the paired rise pairs each two-year window with the one-year window it opens with")
REAL_VIX_AR1_RISE: float = statistics.median(REAL_VIX_AR1_PAIRED_RISES)

#: The seed bootstrap `structure_rise_verdict` takes its interval from:
#: fixed so the verdict a record carries is reproducible from its rows.
STRUCTURE_RISE_DRAWS = 2000
STRUCTURE_RISE_SEED = 20260921


def real_rise_se(key: str = VIX_AR1_ROW) -> float:
    """The tape's paired rise's own standard error: the median's normal
    approximation over the 17 paired block readings, the same estimator
    `real_centre_se` uses for a median centre."""
    if key != VIX_AR1_ROW:
        raise ValidationError(f"{key} has no paired rise record; only {VIX_AR1_ROW} does")
    return median_se(REAL_VIX_AR1_PAIRED_RISES)


def structure_rise_verdict(values_252: Sequence[float],
                           values_504: Sequence[float],
                           key: str = VIX_AR1_ROW) -> dict[str, Any]:
    """Does the model's persistence rise from one year to two the way the
    tape's does?

    The per-seed readings are PAIRED BY POSITION: both lists are the same
    seeds on the same roster, emitted in seed order (HARNESS-NOTES 1), so
    `values_504[i] - values_252[i]` is one seed's own rise and the pairing
    removes the seed's level from the difference. The statistic is the
    median rise over seeds, its interval a fixed-seed bootstrap over seeds,
    and the verdict is where `REAL_VIX_AR1_RISE` sits against that interval:

      matches   the interval contains the tape's rise
      below     the whole interval is under it -- the model's persistence
                is one pole where the tape's has a slow component
      above     the whole interval is over it

    `side` is `below`/`above`/None as the structural sign test spells it.
    The tape rise's own error is carried beside the verdict and is not a
    gate, for `structure_verdict`'s reason.
    """
    if key not in STRUCTURE:
        raise ValidationError(f"{key!r} is not a structural row; structural rows are {sorted(STRUCTURE)}")
    a = [v for v in values_252 if v is not None]
    b = [v for v in values_504 if v is not None]
    if len(a) != len(b) or len(a) < 2:
        raise ValidationError(
            f"a rise on {key} needs the same seeds at both horizons, at least "
            f"two of them; got {len(a)} at 252 and {len(b)} at 504")
    rises = [y - x for x, y in zip(a, b)]
    median = statistics.median(rises)
    rng = random.Random(STRUCTURE_RISE_SEED)
    n = len(rises)
    draws = sorted(statistics.median([rises[rng.randrange(n)] for _ in range(n)])
                   for _ in range(STRUCTURE_RISE_DRAWS))
    lo, hi = draws[int(0.05 * STRUCTURE_RISE_DRAWS)], draws[int(0.95 * STRUCTURE_RISE_DRAWS) - 1]
    tape = REAL_VIX_AR1_RISE
    if hi < tape:
        verdict, side = "below", "below"
    elif lo > tape:
        verdict, side = "above", "above"
    else:
        verdict, side = "matches", None
    return {
        "row": key,
        "n": n,
        "horizons": [TRADING_DAYS_PER_YEAR, 504],
        "tape_rise": tape,
        "se_real": real_rise_se(key),
        "median_252": statistics.median(a),
        "median_504": statistics.median(b),
        "median_rise": median,
        "ci90": [lo, hi],
        "draws": STRUCTURE_RISE_DRAWS,
        "seed": STRUCTURE_RISE_SEED,
        "verdict": verdict,
        "side": side,
        "share_of_tape": median / tape if tape else None,
    }


def centre_distance(values: Sequence[float], key: str, *,
                    horizon_days: int = TRADING_DAYS_PER_YEAR) -> dict[str, Any]:
    """How far the graded median sits from the real centre, in both errors.

    `z_r = (median - centre) / sqrt(se_m^2 + se_real^2)`, reported and never
    a gate: the real centre is one decade of one market, `se_real` is a
    within-decade error, and a gate on it would encode 2015-2025 as the
    truth with a precision the reference does not have -- the 32-name
    1990-2025 reference puts three rows one to three `se_real` away from the
    panel's decade.

    It is the quantity "aim at the real central value" names, so it belongs
    in the report and in a calibration objective. `z_r` is None where
    `real_centre_se` is undetermined, with the reason beside it.

    UNDETERMINED at any horizon with no per-window record, rather than
    answered with the wrong ruler. The real dispersion of a row across years
    is a property of the window length -- clustering at lag 20 reads +0.005
    over 252 bars and +0.030 over 504 on the same reference -- so a 504-day
    model median against the 252-bar windows would be the same error on the
    centre side that `envelope.score` refuses on the band side, and it would
    be invisible because the answer is a plausible number. Since the 504-bar
    windows landed (`REAL_MARKETS_WINDOWS_504`) the refusal no longer falls
    on 504: the horizon has a table of its own, so the diagnostic is
    answered AT the window length graded, which is the whole of what the
    refusal was protecting. `index_tail_dn3_pct` reads its own 35-window
    table at 252 and its 17-window table at 504, and every horizon in
    neither corpus is still refused by name.

    THE MULTIPLIER FOLLOWS THE HORIZON TOO. `band_windows(key,
    horizon_days)` gives the window count the row's band at that horizon
    rests on -- nine at 252, five at 504, four for `corr_persistence_acf1`
    at 504 -- and the threshold is `centre_multiplier` of that count's own
    `band_rule_tolerance`: 1.846, 1.378 and 1.175. Reading a 504-day panel
    against the 252-day tolerance would hold a five-window band to a
    nine-window band's false-alarm rate, which is the same wrong-ruler shape
    one level down.

    THE ESTIMATOR FOLLOWS THE ROW. `median` and `se_m` above are a median
    and its normal-approximation standard error for a row graded as a
    median. For the pooled-rate row they are the MEAN of the per-seed rates
    and `sd / sqrt(n)`, because a median of a zero-inflated count is not an
    estimator of its frequency; the field keeps its name so a serialised
    certificate has one shape, and `estimator` says which quantity it holds.
    """
    if key not in REAL_MARKETS:
        raise ValidationError(
            f"{key!r} is not a graded row; graded rows are "
            f"{sorted(REAL_MARKETS)}")
    values = [v for v in values if v is not None]
    if len(values) < 2:
        raise ValidationError(
            f"need at least two per-seed readings for {key}, got "
            f"{len(values)}")
    rate_row = AGGREGATE.get(key) == "pooled_rate"
    if rate_row:
        median = statistics.fmean(values)
        se_m = statistics.stdev(values) / math.sqrt(len(values))
    else:
        median = statistics.median(values)
        se_m = median_se(values)
    table_horizon = REAL_MARKETS_WINDOWS["horizon_days"]
    own_table = key in INDEX_TAIL_WINDOWS["rows"]
    matched = (int(horizon_days) in INDEX_TAIL_WINDOWS["windows"] if own_table
               else int(horizon_days) in WINDOW_HORIZONS)
    centre = real_centre(key, horizon_days=horizon_days) if matched else None
    se_r = real_centre_se(key, horizon_days=horizon_days) if matched else None
    out: dict[str, Any] = {
        "row": key,
        "n": len(values),
        "horizon_days": horizon_days,
        "estimator": "mean" if rate_row else "median",
        "median": median,
        "se_m": se_m,
        "real_centre": centre,
        "se_real": se_r,
        "multiplier": centre_multiplier(band_rule_tolerance(
            band_windows(key, int(horizon_days))
            if int(horizon_days) in BAND_WINDOWS
            else BAND_WINDOWS[table_horizon])),
        "z_r": None,
        "at_centre": None,
        "undetermined": None,
    }
    if not matched:
        out["undetermined"] = (
            (f"INDEX_TAIL_WINDOWS holds "
             f"{sorted(INDEX_TAIL_WINDOWS['windows'])}-return windows"
             if own_table else
             f"the shape panel's windows are recorded at "
             f"{sorted(WINDOW_HORIZONS)} days")
            + f" and this panel is {horizon_days} days. The real dispersion "
            "of a row across years moves with the window length, so a centre "
            "distance taken across horizons would be the wrong-ruler error "
            "with a plausible-looking answer. Measure the windows at this "
            "horizon first"
        )
        return out
    if centre is None:
        out["undetermined"] = (
            f"{key} has neither a per-window real record in "
            "REAL_MARKETS_WINDOWS nor an ordered (min, median, max) "
            "provenance triple, so it has no real centre in this package"
        )
        return out
    if se_r is None:
        out["undetermined"] = (
            f"{key} has no per-window real record in REAL_MARKETS_WINDOWS, "
            "so the dispersion of the row across real years is not in this "
            "package and se_real cannot be derived. Its centre is recorded "
            "to three places in REAL_MARKETS_PROVENANCE, which gives the "
            "centre and not its error"
        )
        return out
    combined = math.sqrt(se_m ** 2 + se_r ** 2)
    out["z_r"] = (median - centre) / combined
    out["at_centre"] = abs(out["z_r"]) < out["multiplier"]
    return out


#: The basis every band-reading producer grades with when a caller names
#: none. `envelope` imports this name rather than defining its own, so the
#: default lives in the module that depends on nothing and there is exactly
#: one of it.
#:
#: CHANGED FROM `shipped` TO `ruled` ON 2026-09-15, and the change is the
#: whole point of this line. `ruling-the-ruler-is-the-universal-band` made
#: the bar the universal whole-tape band on 2026-09-14 and the default was
#: held at `shipped` deliberately, on the argument that flipping it would
#: move every count in forty-odd tools and tests in one commit. That
#: argument was about the cost of the change and never about which band is
#: right, and the cost it names is the cost of a ruling that has been made:
#: while the default read `shipped`, `facts.REAL_MARKETS` -- the 2015-2025
#: decade table -- was what actually graded every caller who did not know
#: to ask, which is every caller written before the basis argument existed.
#: Simon's framing is the reason: the model has to be measured on REALISM,
#: not against artificial bands that may not be accurate.
#:
#: WHAT THIS DOES NOT DO. It does not change `BAND_BASIS`, any band table,
#: or any committed record. `shipped` is still a basis and still reachable
#: by name, because a count taken against the decade band is still a real
#: reading of a real table and the records on disk were taken that way.
#: What moves is which one a caller gets for free.
DEFAULT_BAND_BASIS = "ruled"


def bands_for_basis(days: Any, basis: str = DEFAULT_BAND_BASIS
                    ) -> tuple[dict[str, tuple[float, float]],
                               dict[str, float], str, dict[str, str]]:
    """The band table, the seed scale, the table's name and its blind rows.

    The one place in this module that turns a basis into a ruler, so a
    producer asks for a basis rather than reaching for a table. Returns the
    unreadable map as its fourth item because a basis that cannot read a row
    is a STATE a caller has to be able to report: dropping the row silently
    is how "13 of 13" and "13 of 14" become the same printed number.

    `shipped` keeps `RULERS_BY_HORIZON` exactly as it was, including the
    known disagreement with `envelope.BANDS_504` at 504 -- that table has
    seventeen rows to this one's fourteen and the two grade the same panel.
    At `ruled` the disagreement does not arise, because both modules read
    `RULED_BY_HORIZON`.
    """
    if basis == "shipped":
        row = RULERS_BY_HORIZON.get(int(days))
        if row is None:
            rulers_for_horizon(days)          # raises with the full message
        return row["bands"], row["seed_sd"], row["bands_name"], {}
    if basis == "ruled":
        table = RULED_BY_HORIZON.get(int(days))
        if table is None:
            raise ValidationError(
                f"no ruled band set exists at {days!r} days; the horizons "
                f"with one are {sorted(RULED_BY_HORIZON)}")
        row = RULERS_BY_HORIZON[int(days)]
        name = ("facts.REAL_MARKETS_RULED" if int(days) == CERTIFIED_HORIZON_DAYS
                else "facts.REAL_MARKETS_RULED_504")
        return (table, row["seed_sd"], name,
                dict(RULED_UNREADABLE.get(int(days), {})))
    raise ValidationError(
        f"{basis!r} is not a band basis; the bases are ['ruled', 'shipped']. "
        f"A basis is an era, a roster, a window count and a rule, and "
        f"`facts.band_basis` states each one")


def compare_to_real_markets(facts: dict[str, Any], *,
                            basis: str = DEFAULT_BAND_BASIS
                            ) -> dict[str, dict[str, Any]]:
    """Line each measured statistic up against the empirical range.

    THE BASIS IS AN ARGUMENT, since 2026-09-15. Until then this function
    read `RULERS_BY_HORIZON` and there was no way to ask it for anything
    else, so the sentence "the producers read the ruled band" was true of
    `envelope.score` and false here. `basis` defaults to
    `DEFAULT_BAND_BASIS`, which is now `ruled`.

    A ROW THE BASIS CANNOT READ IS PRESENT AND CARRIES ITS REASON. The
    ruled table is absent three rows at 252 and four at 504, each a named
    ship blocker. Such a row appears here with `matches` None, `verdict`
    "unreadable" and no `real_range`, rather than being dropped: a caller
    counting `matches` gets the same answer either way, and a caller
    listing rows can tell "not tested" from "not measured".

    Returns a verdict per statistic rather than an overall score. A single
    "realism score" would average a property this model reproduces well against
    one it gets frankly wrong, and the whole value of the exercise is knowing
    WHICH.

    THE RULER COMES FROM THE PANEL. `measure` records `days`, and this reads
    it: a 504-day panel is graded against `REAL_MARKETS_504`, and a panel at a
    horizon with no band set is REFUSED rather than graded against the
    252-day bands. Until 2026-09-05 this function used `REAL_MARKETS`
    unconditionally and never looked at `days`, which is how a shipped
    example came to grade a 60-day run against a ruler derived from 253-bar
    windows. Each row carries `horizon_days` and `ruler`, so a verdict
    printed anywhere says which bands produced it.
    """
    days = facts.get("days")
    if days is None:
        raise ValidationError(
            "this panel does not record what horizon it was measured at, so "
            "no ruler can be chosen for it. `facts.measure` records `days`; "
            "a panel assembled by hand must carry it too. Defaulting to the "
            f"{CERTIFIED_HORIZON_DAYS}-day bands is the error this refusal "
            "exists to prevent.")
    rulers_for_horizon(days, what="this panel")   # the horizon refusal, kept
    bands, scales, ruler_name, blind = bands_for_basis(days, basis)

    out: dict[str, dict[str, Any]] = {}
    for key, why in blind.items():
        # Only a row this panel actually measured. An unmeasured row is
        # absent for a different reason and the two must not be merged.
        if facts.get(key) is None:
            continue
        out[key] = {
            "measured": facts[key],
            "real_range": None,
            "matches": None,
            "horizon_days": int(days),
            "ruler": ruler_name,
            "basis": basis,
            "verdict": "unreadable",
            "direction": None,
            "band_distance": None,
            "scaled_distance": None,
            "unreadable": why,
        }
    for key, (low, high) in bands.items():
        value = facts.get(key)
        # A statistic that could not be measured -- one instrument, or a run
        # too short to difference volume -- is ABSENT here rather than present
        # as a zero, because zero is a real reading and would land inside some
        # of these bands.
        if value is None:
            continue
        distance = band_distance(value, low, high)
        sd = scales.get(key)
        out[key] = {
            "measured": value,
            "real_range": (low, high),
            "matches": low <= value <= high,
            # Which ruler this verdict came from, on the row rather than
            # beside it, because a verdict travels alone: `report` prints
            # rows, a certificate serialises rows, and a row that does not
            # name its bands can be read against the wrong ones by the next
            # reader as easily as it was computed against them.
            "horizon_days": int(days),
            "ruler": ruler_name,
            # The BASIS beside the name, for the reason `BAND_BASIS` exists:
            # a symbol whose contents can be swapped names no era.
            "basis": basis,
            # `verdict` reads the sign of the band; `direction` is the raw
            # numeric comparison. They differ exactly where it matters: an
            # absent leverage effect is ABOVE its negative band and "too weak"
            # against it.
            "verdict": _verdict(value, low, high),
            "direction": (
                "within" if low <= value <= high
                else ("above" if value > high else "below")
            ),
            # How FAR outside, twice over: in the statistic's own units, and
            # in units of its across-seed sampling noise at the baseline
            # (`SEED_SD`, or `SEED_SD_504` for a 504-day panel -- the scale
            # for the panel's OWN horizon, since the two differ by factors
            # from 0.80 to 3.23), so exits are comparable across statistics
            # whose scales differ by three orders of magnitude. Per-statistic
            # fields, deliberately -- this function still refuses to add
            # them up, for the reason in the docstring.
            "band_distance": distance,
            "scaled_distance": distance / sd if sd else None,
        }
    return out


def report(facts: dict[str, Any], *,
           basis: str = DEFAULT_BAND_BASIS) -> str:
    """A human-readable summary, honest about the mismatches.

    Printed in two sections, because the split between them is the finding: a
    statistic taken on one series at a time is a different kind of claim from
    one about how two things move together, and this model does not do equally
    well at both.

    The header names the RULER as well as the horizon. A reader who sees "252
    days" above a table headed "real markets" has no way to tell which band
    set produced the verdicts, and for two years there was only one answer;
    now there are two and the report says which.
    """
    verdicts = compare_to_real_markets(facts, basis=basis)
    graded_here, _, ruler, blind = bands_for_basis(facts["days"], basis)

    def row(key: str) -> str:
        verdict = verdicts.get(key)
        if verdict is None:
            return f"{LABELS[key]:22s} {'n/a':>10s}"
        if verdict["real_range"] is None:
            # Present, measured, and NOT TESTED. Printed in the table rather
            # than quietly moved to the ungraded section, because the row is
            # one the bar counts and the reason it carries is a blocker.
            return (f"{LABELS[key]:22s} {verdict['measured']:>10.3f}  "
                    f"{'no ruled band':>14s}   UNREADABLE")
        low, high = verdict["real_range"]
        mark = "matches" if verdict["matches"] else verdict["verdict"].upper()
        return (
            f"{LABELS[key]:22s} {verdict['measured']:>10.3f}  "
            f"{low:>6.2f} to {high:<5.2f}   {mark}"
        )

    lines = [
        f"seed {facts['seed']}, {facts['instruments']} instruments, "
        f"{facts['days']} days, {facts['observations']:,} daily returns",
        f"graded against {ruler} on the {basis} basis, "
        f"derived at {facts['days']} days",
        "",
        f"{'statistic':22s} {'measured':>10s}  {'real markets':>14s}   verdict",
        "",
        "marginal: one series on its own",
    ]
    lines += [row(key) for key in SHAPE if key in MARGINAL]
    lines += [
        "",
        "dependence: how things move together",
    ]
    lines += [row(key) for key in SHAPE if key not in MARGINAL]
    # The first moment, in its own section: the shape rows above can all
    # read in band on a market that loses a fifth of its value in a year.
    lines += ["", "level: the first moment, held red until it is right"]
    lines += [row(key) for key in LEVEL]
    if CRISIS:
        # Not "the fear gauge on a large down day" any more: the group holds
        # the COUNT of those days as well as the gauge's answer to them, and
        # a heading that names only the response would mislabel the row that
        # counts. The two questions are different and the group asks both.
        lines += ["", "crisis: how often a large down day, and the fear "
                      "gauge's answer to one"]
        lines += [row(key) for key in CRISIS]

    # The ungraded rows, derived from the ruler in use rather than listed, so
    # a row can never be printed as graded because a list went stale -- and so
    # a horizon whose band set covers fewer rows says which rows it lost. At
    # 504 days `REAL_MARKETS_504` holds the fourteen shape rows only, so the
    # level and crisis rows move into this section: their 504-day bands are
    # `envelope.BANDS_504`'s judgement to reuse the 252-day ones with an
    # argument per row, and that judgement is not this module's to make
    # silently.
    # `graded_here` is the basis's own table, bound at the top of this
    # function. It used to be re-read from `rulers_for_horizon`, which is
    # the shipped table whatever basis the verdicts above were taken at, so
    # this section listed the wrong rows the moment a basis was passed.
    # A row the basis cannot read is NOT listed here: it was printed in the
    # table above as UNREADABLE, and listing it twice under two different
    # reasons is the thing this section exists to avoid.
    ungraded = [key for key in LABELS
                if key not in graded_here and key not in blind]
    shown_blind = [k for k in blind if facts.get(k) is not None]
    if shown_blind:
        lines += ["", f"unreadable on the {basis} basis: measured, and the "
                      "ruler has no band for it"]
        for key in shown_blind:
            # `LABELS.get`, not `LABELS[...]`: `vix_ar1_debiased` is a graded
            # row with no print label, so indexing raised a KeyError on every
            # panel that carried it, which `facts.measure` emits on every run.
            lines += textwrap.wrap(f"{LABELS.get(key, key)}: {blind[key]}",
                                   72, initial_indent="  ",
                                   subsequent_indent="  ")
    if ungraded:
        lines += ["", "reporting only: measured, not graded"]
        for key in ungraded:
            value = facts.get(key)
            shown = "n/a" if value is None else f"{value:.3f}"
            lines.append(f"{LABELS[key]:22s} {shown:>10s}  {'no band':>14s}")
            reason = REPORTING_ONLY.get(key)
            if reason:
                lines += textwrap.wrap(reason, 72,
                                       initial_indent="  ", subsequent_indent="  ")
            elif key in REAL_MARKETS:
                lines += textwrap.wrap(
                    f"graded at {CERTIFIED_HORIZON_DAYS} days; no band for "
                    f"it has been derived at {facts['days']}.", 72,
                    initial_indent="  ", subsequent_indent="  ")
    lines += [
        "",
        "Every verdict above answers ONE question: could a real year read",
        "this number. It does not say the mechanism the row is named for is",
        "present, and on five rows it cannot -- the band contains the reading",
        "of a model without the mechanism, and a mechanism-absent model",
        "passes three of those bands every time. What answers 'is a model",
        "WITHOUT the mechanism excluded' is a sign test of the per-seed",
        "readings against the row's null: facts.mechanism_verdict, and",
        "envelope.certify for the three counts together. One measurement",
        "cannot answer it, because the question is about a distribution.",
        "",
        "Read the two sections against each other. A model can get the shape",
        "of one series right and still get every way things move together",
        "wrong, and where it does it will flatter anything that diversifies,",
        "anything leaning on a factor structure, and anything whose risk comes",
        "from several things going wrong at once.",
        "",
        "Carry into any conclusion: returns here are positively",
        "autocorrelated where real ones are not, so momentum is mechanically",
        "profitable in this market in a way it is not in real markets.",
    ]
    return "\n".join(lines)
