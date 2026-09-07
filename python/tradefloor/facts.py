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
    # Bands from ^VIX against ^GSPC, 1990 to 2026, in `REAL_MARKETS_PROVENANCE`.
    "fear_gauge_dn1": (0.70, 4.03),
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
        "claim": "median change in the volatility index on sessions at or below "
                 "-1 percent, by the panel's shared rule: the statistic per "
                 "252-session window over the reference panel's ten windows "
                 "2015-07 to 2025-07, the COVID window excluded and reported as "
                 "the crisis reading, band = [min - s, max + s] with s the "
                 "across-window sd with the most extreme window dropped",
        "windows": (1.34, 2.66, 3.39),
        "crisis_window": 3.08,
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
        "claim": "median change in the volatility index on sessions at or below "
                 "-3 percent, POOLED across the certification seeds because a "
                 "252-day run holds none on about a third of seeds; the band "
                 "departs from the shared rule for the same reason, since a "
                 "calm year holds no such session (2017 held none): it is the "
                 "shared rule applied across every 252-session window since "
                 "1990 that holds at least five such sessions, ten windows",
        "windows": (3.70, 5.30, 8.48),
        "crisis_window": 7.12,
        "sources": (
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
    "same_run_at_the_default": "pt-v16 on the same protocol and seeds reads "
                               "an sd of 7.0888, the figure beside "
                               "envelope.CERTIFIED_LEVEL; the entry above "
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
    than to relocate. The tape's readings at 252 and 504 differ by 0.029,
    so the substitution is not a small one.
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
#: report already holds the group red until the model earns it. LEVEL would
#: fit the arithmetic -- a rate is a first moment -- and was not chosen
#: because the level row's protocol note and its `SEED_SD` treatment are
#: specific to the drift.
CRISIS = ("fear_gauge_dn1", "fear_gauge_dn3", "index_tail_dn3_pct")

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
    """
    if days < 2:
        raise ValidationError("days must be at least 2 to have a return")

    engine = Engine(seed=seed, universe=universe, macro_state=macro,
                    model=model)
    for day in range(days):
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        # Record before the close: the close advances the macro chain, and
        # the macro row must carry the values the day traded under.
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
    }
    bars = engine.bars(grain="day")
    facts.update(panel_statistics(bars, universe,
                                  min_observations=min_observations))
    facts.update(fear_statistics(engine.bars(grain="day"), engine.macro_table(), universe))
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


def band_rule_tolerance(n_windows: int) -> float:
    """`BAND_RULE`'s false-alarm rate at `n_windows`, measured.

    Raises rather than guessing for a window count nobody has measured: the
    cut a gate reads comes from here, and a tolerance interpolated between
    two measurements would be a chosen constant.
    """
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

    Four rows have no per-window record in `REAL_MARKETS_WINDOWS` -- the two
    conditional-correlation asymmetries, the sector excess and the
    correlation persistence -- so anything that needs the DISPERSION of a
    row across real years, rather than its centre, is undetermined for
    those four in this package.

    Two corpora, not one. `INDEX_TAIL_WINDOWS` holds the index tail row's
    real side: a cap-weighted INDEX over thirty-five years, where
    `REAL_MARKETS_WINDOWS` holds per-NAME readings over one decade of forty
    large caps. The tail row is measured at both certified horizons there
    and its windows are read at the one asked for; every other row exists at
    the one horizon its table was measured at, and `centre_distance` refuses
    the rest rather than rescaling them.
    """
    if key in INDEX_TAIL_WINDOWS["rows"]:
        if int(horizon_days) not in INDEX_TAIL_WINDOWS["windows"]:
            return None
        return index_tail_rates(horizon_days)
    values = REAL_MARKETS_WINDOWS["values"].get(key)
    if values is None:
        return None
    crisis = REAL_MARKETS_WINDOWS["crisis_index"]
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

    None for the four rows with no per-window record: a centre recorded to
    three places gives the centre, not its dispersion, and inverting the
    band edges for it would recover an interval a rounding quantum wide and
    call it a number.
    """
    windows = real_windows(key, horizon_days=horizon_days)
    if windows is None:
        return None
    if AGGREGATE.get(key) in ("mean", "pooled_rate"):
        return statistics.stdev(windows) / math.sqrt(len(windows))
    return MEDIAN_SE_FACTOR * trimmed_sd(windows) / math.sqrt(len(windows))


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

    UNDETERMINED at any horizon but the one `REAL_MARKETS_WINDOWS` was
    measured at, rather than answered with the wrong ruler. The real
    dispersion of a row across years is a property of the window length --
    clustering at lag 20 reads +0.005 over 252 bars and +0.030 over 504 on
    the same reference -- so a 504-day model median against these windows
    would be the same error on the centre side that `envelope.score` refuses
    on the band side, and it would be invisible because the answer is a
    plausible number. The ONE exception is a row whose real side is measured
    at both horizons, which today is `index_tail_dn3_pct` alone: it reads
    the 35-window table at 252 and the 17-window table at 504, and the
    refusal stands for every other row at every other horizon.

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
               else horizon_days == table_horizon)
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
        "multiplier": centre_multiplier(
            band_rule_tolerance(BAND_WINDOWS[table_horizon])),
        "z_r": None,
        "at_centre": None,
        "undetermined": None,
    }
    if not matched:
        out["undetermined"] = (
            (f"INDEX_TAIL_WINDOWS holds "
             f"{sorted(INDEX_TAIL_WINDOWS['windows'])}-return windows"
             if own_table else
             f"REAL_MARKETS_WINDOWS holds {table_horizon}-day readings")
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


def compare_to_real_markets(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Line each measured statistic up against the empirical range.

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
    bands, scales = rulers_for_horizon(days, what="this panel")
    ruler_name = RULERS_BY_HORIZON[int(days)]["bands_name"]

    out: dict[str, dict[str, Any]] = {}
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


def report(facts: dict[str, Any]) -> str:
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
    verdicts = compare_to_real_markets(facts)
    ruler = RULERS_BY_HORIZON[int(facts["days"])]["bands_name"]

    def row(key: str) -> str:
        verdict = verdicts.get(key)
        if verdict is None:
            return f"{LABELS[key]:22s} {'n/a':>10s}"
        low, high = verdict["real_range"]
        mark = "matches" if verdict["matches"] else verdict["verdict"].upper()
        return (
            f"{LABELS[key]:22s} {verdict['measured']:>10.3f}  "
            f"{low:>6.2f} to {high:<5.2f}   {mark}"
        )

    lines = [
        f"seed {facts['seed']}, {facts['instruments']} instruments, "
        f"{facts['days']} days, {facts['observations']:,} daily returns",
        f"graded against {ruler}, derived at {facts['days']} days",
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
    graded_here, _ = rulers_for_horizon(facts["days"])
    ungraded = [key for key in LABELS if key not in graded_here]
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
