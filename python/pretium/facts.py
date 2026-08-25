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

**Against re-derived bands, five of the ten statistics are in band: the
dependence structure mostly holds, and what fails is scale, trend, and
where the clustering sits.** Volatility runs high, returns trend where
real ones do not, volatility clustering is concentrated into short lags
more strongly than a real year shows, and volume-change dynamics remain
structural. Kurtosis, lag-20 clustering, cross-sectional correlation,
volume against volatility, and the leverage effect are inside their
bands.

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

Every figure below: `Universe.random(40, seed=111)` (fingerprint
5d8de78b55aad752), 252 days, `measure()` per sim seed, median over seeds 1
to 6 -- re-measured at known-answer v8 (era digest 1ee64998...), where the
superseded figures beside them are marked with the era they belonged to.

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
`pretium.scenario`); the real crisis reading is +0.63.

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
strong, which is why `_verdict` and `band_distance` below carry their
own sign handling.

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
markets sit near zero here, and that was simply wrong at this
estimator: every real window of a decade reads -0.22 to -0.30, because
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
which is what tuning toward an unprovenanced target looks like from the
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
and verdict moves are in pretium-design/REALISM-BANDS.md.

## Why there are ten statistics and not four

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
  same factor process, which is what market-wide clustering needed.
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
keeping a bug because it happened to score better on a statistic, which is how
a model gets tuned toward its own report card instead of toward being right.
When this was recorded these numbers were measurements rather than targets;
four of them have since BECOME targets -- the calibration the era boundary
performed, disclosed above -- which is why the held-out checks exist: they
are where the report card stops being the thing that was tuned.

## Re-measure after any change

`model_preset()` is versioned, but a change to a coefficient, a different
universe generator, or an unusual scenario can move these. `measure()` takes
the same arguments the rest of the library does, so a claim about realism can
be re-checked rather than inherited.
"""

from __future__ import annotations

import math
import statistics
from typing import Any, Sequence

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
    "abs_return_acf5": (0.02, 0.09),
    "abs_return_acf20": (-0.04, 0.08),
    "cross_sectional_corr": (0.08, 0.56),
    "volume_abs_return_corr": (0.46, 0.66),
    "leverage_effect": (-0.16, 0.00),
    "volume_change_acf1": (-0.32, -0.20),
}

#: Where each band comes from, carried as data so a reader can ask the
#: library rather than trust a docstring. The full derivation -- the window
#: table, the retrieved sources with what each actually measured, and the
#: verdict moves -- is recorded in pretium-design/REALISM-BANDS.md.
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
        "windows": (0.034, 0.046, 0.073),
        "crisis_window": 0.343,
        "sources": (
            "own reference panel (primary)",
            "Cont (2001), fact 8: power-law decay with exponent 0.2-0.4 "
            "puts lag 5 at 0.52-0.72 of lag 1, consistent with the "
            "windows' observed ratio ~0.45",
        ),
        "derivation": "mechanical from the windows",
        "comparability": "argued as for lag 1; banded because phase 2's "
                         "instrument found a parameter corner with lag-1 "
                         "clustering in band and lag-5 at -0.001 -- lag-1 "
                         "strength with no memory behind it -- so an "
                         "unbanded lag 5 was a hole a search walks through",
        "supersedes": None,
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
}

#: The across-seed standard deviation of each statistic at the shipped
#: preset. It ships beside the bands because a band exit is only comparable
#: across statistics once it is priced in units of that statistic's own
#: sampling noise: pooled volatility runs ~40 on a band of width ~20 while
#: every autocorrelation is measured in hundredths, and any comparison that
#: ignores the scales silently becomes a comparison of volatility alone.
#: `pretium.loss` consumes these as its diagonal weighting.
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
#: `pretium.loss.seed_sd_from_panels` remains the estimator, and the loss
#: takes a replacement as a parameter rather than requiring an edit here.
SEED_SD = {
    "annualised_vol_pct": 6.46066,
    "excess_kurtosis": 1.17181,
    "return_acf1": 0.0532019,
    "abs_return_acf1": 0.0945748,
    "abs_return_acf5": 0.0550538,
    "abs_return_acf20": 0.0466668,
    "cross_sectional_corr": 0.10875,
    "volume_abs_return_corr": 0.0433524,
    "leverage_effect": 0.0759765,
    "volume_change_acf1": 0.0102341,
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
}

#: Where SEED_SD_504 came from.
SEED_SD_504_PROVENANCE = {
    "source": "facts.measure() on Universe.random(40, seed=111), 504 days, "
              "seeds 101-130, sample sd across seeds",
    "date": "2026-08-23",
    "model_fingerprint": "pt-v3",
    "bands": "pretium-design/bands-504-noncrisis.json, five non-crisis "
             "505-bar windows of the same 40-name reference roster",
}

#: Where SEED_SD's values come from, carried as data so any consumer -- the
#: loss report, a calibration manifest -- can quote it rather than assert it.
SEED_SD_PROVENANCE = {
    "source": "re-measured on the shipped preset: facts.measure() on "
              "Universe.random(40, seed=111), 252 days, seeds 101-130, "
              "sample sd across seeds",
    "date": "2026-08-22",
    "model_fingerprint": "pt-v1",
    "universe_fingerprint": "5d8de78b55aad752307740018791"
                            "54c0f29aa8fc0c63f3c6a4ac791165ca7380",
    "days": 252,
    "seeds": tuple(range(101, 131)),
    "estimator": "sample standard deviation (n - 1) across seeds",
    "cross_check": "agrees to six significant figures with the seed_sd of "
                   "tools/calibration/results/"
                   "jacobian-pt-v1-2026-08-22-chunk1.json, measured "
                   "independently by the phase-2 instrument on the same "
                   "thirty seeds",
    "pinned_by": "tests/test_loss.py re-measures two of the thirty seeds "
                 "live and re-derives the sd from the committed per-seed "
                 "table",
}

#: The first two are MARGINAL: properties of one series taken on its own. The
#: other six are DEPENDENCE: how returns move together across time, across
#: stocks, with volume, and asymmetrically with their own sign. Which half a
#: statistic falls in is the finding this module reports, so the split is a
#: constant rather than a presentation detail.
MARGINAL = ("annualised_vol_pct", "excess_kurtosis")

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


def _dependence(
    series: dict[int, list[tuple[int, float, float]]],
    min_observations: int,
) -> dict[str, Any]:
    """The four statistics for how things move together, from grouped bars.

    Median across instruments, like the autocorrelations above and for the
    same reason. The exception is cross-sectional correlation, which is
    inherently pairwise and is a mean over all pairs.
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
    if len(keys) >= 2 and common >= 3:
        unit = {i: _unit_centred(returns[i][:common]) for i in keys}
        for position, a in enumerate(keys):
            if unit[a] is None:
                continue
            for b in keys[position + 1:]:
                if unit[b] is None:
                    continue
                pairwise.append(sum(x * y for x, y in zip(unit[a], unit[b])))

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

    ``model`` selects the coefficient set the market runs — a preset name or
    a :class:`pretium.ModelParams` — defaulting to the shipped preset. This
    is the seam the calibration search evaluates through: the panel at a
    candidate vector is ``measure(model=candidate)``, no rebuild. The result
    carries ``model_fingerprint`` beside ``universe_fingerprint`` for the
    same reason that field exists — a realism claim is only checkable
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

    table = engine.bars(grain="day")
    try:
        import pyarrow as pa
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "pretium.facts.measure reads the daily bars table and needs "
            "pyarrow. Install it with: pip install pretium[arrow]"
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
        "seed": seed,
        # Which market these statistics describe. A realism claim without it
        # is unfalsifiable: "kurtosis is +4.8" is only checkable against the
        # roster it was measured on, and tickers do not identify a roster.
        "universe_fingerprint": fingerprint_of(universe),
        # And which MODEL produced them. The panel is the calibration
        # search's objective, so a panel row that does not name its
        # coefficient set is exactly the ambiguity the fingerprint exists
        # to remove.
        "model_fingerprint": engine.model_fingerprint,
        "days": days,
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
    }
    # The four dependence statistics carry the same keys whether or not they
    # could be measured, so a caller reading the result does not have to test
    # for their presence as well as for their value.
    facts.update(_dependence(series, min_observations))
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
    lines += [row(key) for key in REAL_MARKETS if key in MARGINAL]
    lines += [
        "",
        "dependence: how things move together",
    ]
    lines += [row(key) for key in REAL_MARKETS if key not in MARGINAL]
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
