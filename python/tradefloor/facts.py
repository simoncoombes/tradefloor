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

**At 252 days the default preset holds all fourteen statistics in band. At
504 days, against bands re-derived at that window, it holds thirteen.** The
one that misses is `volume_change_acf1`, whose two-year band is tighter than
its one-year band. This headline read seven of fourteen at 504 days until
the 2026-08-26 era boundary moved the default from pt-v3 to pt-v10.

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

import math
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
    # non-overlapping 21-day sub-windows. Twelve sub-windows at 252 days,
    # and the real windows themselves scatter from -0.05 to +0.40, so this
    # band admits everything and says so; the 504-day band is the ruler.
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
    },
    "corr_asymmetry_lagged": {
        "claim": "as corr_asymmetry, conditioned on the previous day's "
                 "equal-weight market return",
        "windows": (-0.092, 0.111, 0.438),
        "crisis_window": 0.074,
        "sources": (
            "tradefloor-design/realism_bands_reference_panel.py, run 2026-08-25",
        ),
    },
    "sector_excess_corr": {
        "claim": "mean same-sector pairwise correlation minus mean cross-sector, "
                 "GICS labels for the same 40 names, 252-day windows 2015-2025",
        "windows": (0.133, 0.164, 0.200),
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
}

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
#: in `REAL_MARKETS_PROVENANCE` is taken over the nine non-crisis windows, and
#: `tests/test_reference_windows.py` derives all nine reproducible triples
#: from this table rather than trusting that they match.
#:
#: TEN ROWS OF FOURTEEN. The four correlation-structure rows -- corr_asymmetry,
#: corr_asymmetry_lagged, sector_excess_corr and corr_persistence_acf1 -- have
#: no per-window record here, and `abs_return_acf5`'s provenance summarises a
#: different window set from this one, so its triple is not derivable from
#: these values and the test excludes it by name rather than by tolerance.
#:
#: What this unblocks: any re-derivation of a band, a leave-one-window-out
#: null of the panel against real data, and any method that needs the
#: dispersion of a statistic across real years rather than its range.
REAL_MARKETS_WINDOWS = {
    "windows": (
        "2015-07..2016-07", "2016-07..2017-07", "2017-07..2018-07",
        "2018-07..2019-07", "2019-07..2020-07", "2020-07..2021-07",
        "2021-07..2022-07", "2022-07..2023-07", "2023-07..2024-07",
        "2024-07..2025-07",
    ),
    #: Index into `windows` of the one excluded from every band derivation.
    "crisis_index": 4,
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
    },
    #: Rows whose provenance triple this table does NOT reproduce, with why.
    "not_derivable": {
        "abs_return_acf5": "its provenance summarises a different window set, "
                           "an eight-value list rather than these nine",
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
#: Measured because it could not be assumed: the scales differ from the
#: 252-day ones by factors from 0.80 to 3.23. Excess kurtosis is 3.2x
#: noisier at 504 days, so an objective reusing `SEED_SD` there would have
#: over-penalised it threefold while under-penalising volatility.
SEED_SD_504 = {
    "annualised_vol_pct": 5.143322,
    "excess_kurtosis": 3.781635,
    "return_acf1": 0.059668,
    "abs_return_acf1": 0.119851,
    "abs_return_acf5": 0.09079536,
    "abs_return_acf20": 0.05491287,
    "cross_sectional_corr": 0.09997682,
    "volume_abs_return_corr": 0.04210395,
    "leverage_effect": 0.08338545,
    "volume_change_acf1": 0.01183045,
    # 2026-08-25, pt-v3, same protocol, sample sd across the thirty seeds;
    # the run reproduced MEASURED_504's cross_sectional_corr 0.4057 to four
    # places.
    "corr_asymmetry": 0.14982,
    "corr_asymmetry_lagged": 0.13171,
    "sector_excess_corr": 0.00539,
    # 2026-08-25, pt-v3, same protocol; twenty-four points per seed.
    "corr_persistence_acf1": 0.1972443,
}

#: Where SEED_SD_504 came from.
SEED_SD_504_PROVENANCE = {
    "source": "facts.measure() on Universe.random(40, seed=111), 504 days, "
              "seeds 101-130, sample sd across seeds",
    "date": "2026-08-23",
    "model_fingerprint": "pt-v3",
    "bands": "tradefloor-design/bands-504-noncrisis.json, five non-crisis "
             "505-bar windows of the same 40-name reference roster",
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
    "companion_not_re_measured": "SEED_SD_504 and the envelope module's "
                                 "measured tables were taken on the "
                                 "superseded roster and have not been "
                                 "re-measured on this one.",
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
CRISIS = ("fear_gauge_dn1", "fear_gauge_dn3")

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
AGGREGATE = {"index_drift_pct": "mean", "fear_gauge_dn3": "pooled"}

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
                 "AGGREGATE, the median across seeds or the pooled median",
    "days": 252,
}


def aggregate_value(key: str, values: Sequence[float]) -> float:
    """One graded value for `key` from its per-seed readings."""
    if AGGREGATE.get(key, "median") == "mean":
        return statistics.fmean(values)
    return statistics.median(values)


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
            # (tradefloor-design/real-corr-persistence.json, 126 windows). A
            # model whose correlation is a lookup on today's VIX reads near
            # zero here: the cross-section decouples the tick VIX falls.
            # Non-overlapping windows on purpose; overlapping ones
            # manufacture persistence out of shared days. Twelve windows in
            # a 252-day run is a noisy estimate per seed and is reported as a
            # diagnostic rather than judged against a band.
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
    """
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "fear_statistics reads Arrow tables and needs pyarrow. Install "
            "it with: pip install tradefloor[arrow]") from exc
    b = pa.table(bars).to_pydict()
    m = pa.table(macro).to_pydict()
    shares = [float(inst.shares_outstanding) for inst in universe]
    level: dict[int, float] = {}
    for day, ident, close in zip(b["day"], b["instrument_id"], b["close"]):
        if close is None:
            continue
        level[int(day)] = level.get(int(day), 0.0) + float(close) * shares[int(ident)]
    gauge = {int(day): float(v) for day, v in zip(m["day"], m["vix"]) if v is not None}
    days = sorted(level)
    pairs: list[tuple[float, float]] = []
    for prev, day in zip(days, days[1:]):
        if day + 1 not in gauge or day not in gauge or level[prev] <= 0.0:
            continue
        ret = (level[day] / level[prev] - 1.0) * 100.0
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
    return out


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


def compare_to_real_markets(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Line each measured statistic up against the empirical range.

    Returns a verdict per statistic rather than an overall score. A single
    "realism score" would average a property this model reproduces well against
    one it gets frankly wrong, and the whole value of the exercise is knowing
    WHICH.
    """
    out: dict[str, dict[str, Any]] = {}
    for key, (low, high) in REAL_MARKETS.items():
        value = facts.get(key)
        # A statistic that could not be measured -- one instrument, or a run
        # too short to difference volume -- is ABSENT here rather than present
        # as a zero, because zero is a real reading and would land inside some
        # of these bands.
        if value is None:
            continue
        distance = band_distance(value, low, high)
        sd = SEED_SD.get(key)
        out[key] = {
            "measured": value,
            "real_range": (low, high),
            "matches": low <= value <= high,
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
            # (SEED_SD), so exits are comparable across statistics whose
            # scales differ by three orders of magnitude. Per-statistic
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
    """
    verdicts = compare_to_real_markets(facts)

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
        lines += ["", "crisis: the fear gauge on a large down day"]
        lines += [row(key) for key in CRISIS]

    # The ungraded rows, derived from the two tables rather than listed, so
    # a row can never be printed as graded because a list went stale.
    ungraded = [key for key in LABELS if key not in REAL_MARKETS]
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
    lines += [
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
