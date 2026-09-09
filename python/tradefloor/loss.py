"""The calibration objective: band distance, noise-scaled, diagonally weighted.

This module turns the realism panel of `tradefloor.facts` into the number a
calibration search minimises:

    d_k    = max(0, lo_k - m_k, m_k - hi_k)      # zero inside the band
    L_real = sum over k of (d_k / s_k)^2

where m_k is the panel median of statistic k, [lo_k, hi_k] is its
real-market band (`facts.REAL_MARKETS`), and s_k is its across-seed
standard deviation at the shipped baseline (`facts.SEED_SD`). Each
statistic's band exit is priced in units of its own sampling noise -- the
simulated-method-of-moments weighting discipline with a deliberately
DIAGONAL matrix. Not the full inverse covariance: ten moments estimated
from thirty seeds make the full inverse ill-conditioned, and using it
quietly bets the search on noisy off-diagonal estimates. The diagonal is
honest and revisitable, and every result this module returns says which
weighting was used so the choice stays visible.

There is no unweighted form, on purpose. Pooled volatility is numerically
~40 on a band of width ~20 while every autocorrelation is measured in
hundredths, so an unweighted sum is not a neutral default -- it is a
volatility objective wearing a ten-statistic costume. `band_distance_loss`
therefore refuses to run without a positive s_k for every statistic in the
loss, rather than falling back to weights of one.

## What is in the loss, and what is reported but excluded

Membership is data, not conditionals:

- `LIVE_TARGETS` -- the five statistics the search is trying to move into
  band, lag-5 clustering among them since the band re-derivation closed
  the zero-memory corner phase 2's instrument found.
- `CONSTRAINTS` -- the four statistics in band at the baseline. They
  contribute zero loss there and push back only when a candidate drives
  them out; a calibration that fixed correlation by breaking kurtosis --
  or reached lag-5 clustering by destroying the leverage effect -- would
  trade a documented gap for a new one.
- Structural exclusions -- everything in `facts.REAL_MARKETS` not named
  above, now only the volume-change autocorrelation: a held volume level
  plus independent per-tick noise sits near -0.5 at any coefficients,
  against a real band of -0.32 to -0.20, and no parameter reaches the
  row. It appears in every result this module returns, with its band
  distance, as the standing falsification verdict -- but an optimiser
  pointed at a target no parameter reaches does not fail cleanly: it
  distorts every other parameter chasing it, then "succeeds" by
  overfitting. Excluding it is the identifiability gate applied.

Promoting a structural statistic once a model change makes it reachable
is one edit: append its key to `LIVE_TARGETS` (or to `CONSTRAINTS`, if
it is already in band and only needs defending). That is not
hypothetical any more: the GJR term made `leverage_effect` reachable and
the re-derived band showed it in band, so it moved to `CONSTRAINTS`; the
instrument's lag-5 finding moved `abs_return_acf5` into `LIVE_TARGETS`.
The structural set is derived as the complement, so nothing else moves;
a promoted statistic's band, verdict wording and seed sd already ship in
`tradefloor.facts`.

## What this module is not

`compare_to_real_markets` refuses to emit a single realism score, and that
refusal is a considered position: a model is realistic in some respects
and not others, and one number hides exactly the structure that matters.
This loss does not reopen that question. It is an OPTIMISATION DEVICE --
a search direction for calibration tooling -- not a published metric, and
the published artifact remains the ten-row panel with per-statistic
verdicts. That is why `band_distance_loss` returns the full per-statistic
breakdown with the scalar inside it rather than a bare float, why the
structural rows ride along in every result, and why nothing here is
called, or should ever grow into, a `realism_score`.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import warnings
from typing import Any, Mapping, Sequence

from ._core import ValidationError
from .facts import (AGGREGATE, BAND_WINDOWS, CRISIS, LEVEL, PERSISTENCE,
                    REAL_MARKETS,
                    REAL_MARKETS_504, REAL_MARKETS_PROVENANCE,
                    RULERS_BY_HORIZON, SEED_SD, SEED_SD_504,
                    SEED_SD_PROVENANCE, SHAPE, aggregate_panels,
                    band_distance, band_rule_tolerance, centre_multiplier,
                    check_ruler_horizon, horizon_of_panels, median_se,
                    median_se_bootstrap, pooled_rate_counts, rule_row)

#: The statistics the calibration search is trying to move into band. This
#: is the ONE tuple to edit when a model change makes a structural statistic
#: reachable: append its key here and it enters the loss with the band,
#: verdict wording and seed sd it already has in `tradefloor.facts`.
#: `abs_return_acf5` joined at the band re-derivation: phase 2's instrument
#: found a parameter corner with lag-1 clustering in band and lag-5 memory
#: at -0.001, so lag 5 is banded and live to price that hole out of the
#: search space.
LIVE_TARGETS = (
    "annualised_vol_pct",
    "return_acf1",
    "abs_return_acf1",
    "abs_return_acf5",
    "cross_sectional_corr",
)

#: In band at the baseline; constraints rather than targets. Zero loss
#: where they stand, resistance when a candidate drives them out.
#: `leverage_effect` joined when the re-derived band (per-name Pearson,
#: -0.16 to 0.00) put the shipped GJR-backed model inside it -- it is
#: reachable (the falsification certificates reach -0.12 in band through
#: `garch_gamma`) and in band, which is this tuple's definition.
#: `abs_return_acf20` is here for the same reason lag 5 became live: a
#: measured statistic outside the loss is a direction an optimiser can
#: break for free.
#:
#: It was promoted to LIVE_TARGETS on 2026-08-23 to make the search chase
#: the long-lag clustering the model lacks, and reverted the same day
#: because it CANNOT. Real markets' own year-to-year variation in this
#: statistic (windows spanning -0.015 to +0.141, a range of 0.156) is
#: SIX TIMES the model's entire defect (-0.004 against real's +0.020, a
#: gap of 0.024). The band is wide because that dispersion is real, so no
#: role, margin or penalty can make a single 252-day panel distinguish a
#: market with the right tail from one with none -- three successive
#: searches proved it, each removing a real obstacle and finding another
#: behind it.
#:
#: The tail is a GATE property, not a panel property. It is checked by
#: `decay_curve.py` over thirty seeds, where aggregation kills exactly the
#: noise that defeats the panel: real markets fit a log-log slope of
#: -0.436 there and the model -0.956, which is unambiguous.
CONSTRAINTS = (
    "excess_kurtosis",
    "volume_abs_return_corr",
    "leverage_effect",
    "abs_return_acf20",
)

#: Reported in every result, excluded from the loss: the panel statistics
#: no lever has been shown to move cleanly, so an optimiser pointed at them
#: distorts everything else chasing them. Membership is about the OBJECTIVE,
#: not about reachability. Two members were called structurally unreachable
#: until 0.2.0 and are not: the shipped preset holds volume_change_acf1 and
#: sector_excess_corr in band at the certified horizon. Derived as the
#: complement so that
#: promoting a statistic is genuinely a one-tuple edit, and so a statistic
#: added to `facts.REAL_MARKETS` is excluded-but-reported by default rather
#: than silently optimised against.
#:
#: The level row `index_drift_pct` sits here for now and is the exception
#: to the rule above: it is meant to be charged for, because a search that
#: cannot read the level spends it freely, which is how a market losing a
#: fifth of its value a year certified clean. `facts.SEED_SD` has carried
#: its seed sd on the pinned protocol since 2026-09-04, so the one-tuple
#: edit that promotes it now runs; it is not made here because it changes
#: the objective every recorded calibration score was measured under, at
#: the default preset by 16.5 points against a scale of 9.6, and that is
#: a decision about the search rather than about the row.
#:
#: The index tail row `index_tail_dn3_pct` is structural and is meant to
#: STAY here, which makes it the second exception. Its band is the tape's
#: own uncertainty -- thirty-five years, one of which holds a third of the
#: events -- so it is [0.47, 1.96] on a centre of 1.21, and a band distance
#: is flat at zero across everything a search would try. An objective term
#: that is flat over the whole feasible region contributes nothing but
#: noise at the edges, which is the edge-finding failure the panel's own
#: history is full of. The row exists to GRADE the answer, not to be solved
#: against.
STRUCTURAL = tuple(
    key for key in REAL_MARKETS
    if key not in LIVE_TARGETS and key not in CONSTRAINTS
)


def seed_sd_from_panels(
    panels: Sequence[Mapping[str, Any]],
) -> dict[str, float]:
    """Across-seed sample standard deviation per statistic, from per-seed panels.

    `panels` is a sequence of `facts.measure` results, one per seed. Returns
    a mapping suitable for `band_distance_loss(seed_sd=...)`, covering every
    statistic in `facts.REAL_MARKETS` -- structural ones included, so a
    later promotion needs no re-measurement here.

    This is the estimator behind the shipped `facts.SEED_SD` (there is a
    test re-deriving those constants from the committed thirty-seed panel
    table, two seeds of which it re-measures live), and the calibration
    instrument runs it on its own seed panels. Sample (n-1)
    standard deviation, matching the shipped values' convention.

    A statistic that came back None on any panel is refused rather than
    dropped: an sd computed over a quietly shrunken seed set would carry the
    full set's authority.
    """
    if len(panels) < 2:
        raise ValidationError(
            "seed_sd_from_panels needs at least two per-seed panels; a "
            "standard deviation of one observation is not a noise scale"
        )
    out: dict[str, float] = {}
    for key in REAL_MARKETS:
        values = [panel.get(key) for panel in panels]
        missing = sum(1 for v in values if v is None)
        if missing:
            raise ValidationError(
                f"statistic {key!r} is missing from {missing} of "
                f"{len(panels)} panels; measure it on every seed or drop "
                "the seed, not the statistic"
            )
        out[key] = statistics.stdev(values)
    return out


def band_distance_loss(
    panel: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    *,
    seed_sd: Mapping[str, float] | None = None,
    bands: Mapping[str, tuple[float, float]] | None = None,
) -> dict[str, Any]:
    """L_real: the squared, noise-scaled band distances, summed over the live set.

    `panel` is either one statistics mapping -- a `facts.measure` result, or
    already-aggregated medians -- or a sequence of per-seed panels, in which
    case each statistic's m_k is its MEDIAN across seeds. That is how the
    calibration instrument evaluates a candidate on its fixed seed list.

    `seed_sd` is the noise scale s_k per statistic. The default is the
    shipped `facts.SEED_SD`, measured at the baseline preset; phase 2's
    thirty-seed re-estimate (see `seed_sd_from_panels`) is passed here
    rather than edited in. Every statistic in the loss must have a positive
    scale -- a missing or zero s_k raises instead of defaulting to an
    unweighted term, because an unweighted sum is a choice, and this
    function will not make it by accident.

    Returns the breakdown, with the scalar inside it::

        {
          "loss":       L_real, summed over LIVE_TARGETS + CONSTRAINTS,
          "weighting":  "diagonal",     # which SMM weighting was used
          "statistics": {key: {"measured", "band", "role", "distance",
                               "scaled", "contribution"}, ...},
          "seed_sd":    the scales actually used,
          "seed_sd_provenance": where they came from,
          "panels":     how many per-seed panels were aggregated,
        }

    All thirteen panel statistics appear in `"statistics"`, in panel order.
    Structural rows carry their measured value and band distance --
    the standing falsification verdict rides along with every loss
    evaluation -- but their `"contribution"` is None and they are absent
    from the sum. `"contribution"` is (d_k/s_k)^2 exactly for the rows in
    the loss, so the sum of non-None contributions IS `"loss"`.

    An optimisation device, not a published metric: report the eight-row
    panel (`facts.report`), not this number.
    """
    if isinstance(panel, Mapping):
        panels: Sequence[Mapping[str, Any]] = (panel,)
    else:
        panels = list(panel)
        if not panels:
            raise ValidationError("no panels given")

    if seed_sd is None:
        scales: Mapping[str, float] = SEED_SD
        provenance: Any = SEED_SD_PROVENANCE
    else:
        scales = seed_sd
        provenance = "caller-supplied"

    # `bands` exists so a horizon can be scored against its OWN ruler. The
    # default is the 252-day set, the one every existing caller means.
    # Passing `facts.REAL_MARKETS_504` alongside `facts.SEED_SD_504` scores a
    # 504-day panel; passing one without the other is the mistake this
    # parameter was added to make avoidable, not to make easy.
    table: Mapping[str, tuple[float, float]] = (
        REAL_MARKETS if bands is None else bands
    )

    # And "avoidable" was not enough: `evaluate_axes.py` ran a 504-day axis
    # through the default bands and the default scales for weeks, labelled
    # them "the TRUE bands", and published a `generalises` verdict from the
    # result. The panels say what horizon they were measured at -- `measure`
    # records `days` -- so the pairing is CHECKED here rather than left to
    # the caller. A panel that records no horizon, and a table nobody
    # registered, are unknown rather than wrong and pass unchecked; the
    # defaults are checked, because the defaults are what the mistake used.
    horizon = check_ruler_horizon(
        panel_days=horizon_of_panels(panels, what="the panels given to "
                                                  "band_distance_loss"),
        bands=table, seed_sd=scales, what="band_distance_loss")

    rows: dict[str, dict[str, Any]] = {}
    total = 0.0
    used: dict[str, float] = {}
    graded = aggregate_panels(panels, table.keys())
    for key, (low, high) in table.items():
        in_loss = key in LIVE_TARGETS or key in CONSTRAINTS
        role = (
            "live target" if key in LIVE_TARGETS
            else "constraint" if key in CONSTRAINTS
            else "structural"
        )
        if AGGREGATE.get(key) == "pooled":
            # A pooled row is present on a panel that carries its samples,
            # empty or not; it is missing only where the panel never
            # measured it.
            values = [p.get(key + "_samples") for p in panels]
        elif AGGREGATE.get(key) == "pooled_rate":
            # And a pooled-RATE row is present on a panel that carries its
            # hit count, zero or not. Reading its `_pct` instead would call
            # a seed with no session at -3 percent unmeasured, when zero of
            # 251 is a reading and a third of real years read it.
            values = [p.get(pooled_rate_counts(key)[0]) for p in panels]
        else:
            values = [p.get(key) for p in panels]
        present = [v for v in values if v is not None]
        if len(present) < len(values) or key not in graded:
            if in_loss:
                # A statistic the search optimises against cannot silently
                # contribute zero because a candidate broke its
                # measurability -- that would make unmeasurable an
                # attractive direction.
                raise ValidationError(
                    f"statistic {key!r} is in the loss but missing from "
                    f"{len(values) - len(present)} of {len(values)} "
                    "panels"
                )
            # A structural row degrades to unmeasured: it was never in the
            # sum, and refusing the whole evaluation over it would let a
            # reporting gap block the search.
            rows[key] = {
                "measured": None, "band": (low, high), "role": role,
                "distance": None, "scaled": None, "contribution": None,
            }
            continue

        # Each row by its own estimator: a median for the shape rows, a
        # thirty-seed mean for a level row, and for the crisis rows a pooled
        # median or a pooled RATE, the hits over every seed divided by the
        # sessions over every seed (`facts.AGGREGATE`).
        measured = graded[key]
        distance = band_distance(measured, low, high)
        sd = scales.get(key)
        if in_loss:
            if sd is None or sd <= 0:
                raise ValidationError(
                    f"statistic {key!r} is in the loss but has no positive "
                    f"seed sd (got {sd!r}); an unweighted term is a silent "
                    "re-weighting of the whole objective, so it is refused "
                    "rather than defaulted"
                )
            used[key] = sd
            scaled = distance / sd
            contribution = scaled ** 2
            total += contribution
        else:
            scaled = distance / sd if sd else None
            contribution = None
            if sd:
                used[key] = sd
        rows[key] = {
            "measured": measured, "band": (low, high), "role": role,
            "distance": distance, "scaled": scaled,
            "contribution": contribution,
        }

    from .facts import horizon_of_table

    return {
        "loss": total,
        "weighting": "diagonal",
        "statistics": rows,
        "seed_sd": used,
        "seed_sd_provenance": provenance,
        "panels": len(panels),
        # Which ruler this loss was taken with, and at what horizon, so a
        # serialised certificate carries the answer instead of leaving a
        # reader to infer it from the tool that wrote the file. `None` where
        # the tables are the caller's own and no horizon could be attached.
        "horizon_days": horizon,
        "bands": horizon_of_table(table)[1],
    }


def dual_horizon_loss(
    panels_252: Sequence[Mapping[str, Any]],
    panels_504: Sequence[Mapping[str, Any]],
    *,
    weight_504: float = 1.0,
) -> dict[str, Any]:
    """L_real at BOTH horizons, each against its own ruler, summed.

    Three consecutive calibration searches bought 252-day realism by
    spending 504-day realism, by a different route each time, and the last
    of them was rejected by its own overfitting control on the horizon axis
    after producing the best 252-day fit this project had seen. The cause is
    structural rather than unlucky: the objective read one horizon and the
    validation read the other, so trading the second for the first was free
    to the optimiser and only visible afterwards.

    This is the fix, prescribed twice in the record before it was built. The
    252-day panel is scored against `facts.REAL_MARKETS` with
    `facts.SEED_SD`; the 504-day panel against `facts.REAL_MARKETS_504` with
    `facts.SEED_SD_504`. Each horizon carries its own bands AND its own
    noise scale, because both are horizon-dependent and pairing one with the
    other's is the wrong-ruler error in a subtler dress: measured, the
    504-day scales differ from the 252-day ones by factors from 0.80 to
    3.23, so reusing `SEED_SD` there would over-penalise excess kurtosis
    threefold while under-penalising volatility.

    # The weighting is a choice, stated rather than hidden

    `weight_504` defaults to 1.0 -- equal weight -- and that is a judgement
    rather than a derivation. There is no principled exchange rate between a
    252-day band exit and a 504-day one. Equal weighting says "a
    seed-sd of miss matters the same at either horizon", which is defensible
    and revisitable; what would not be defensible is an unstated weighting
    buried in a scalar. The result carries both components separately, so a
    reader can re-weight without re-running.

    Returns::

        {
          "loss":        combined = loss_252 + weight_504 * loss_504,
          "loss_252":    the 252-day component,
          "loss_504":    the 504-day component, UNWEIGHTED,
          "weight_504":  the weighting actually applied,
          "horizon_252": the full band_distance_loss breakdown,
          "horizon_504": the same at 504 days,
        }

    Both breakdowns ride along in full, because a combined number that
    cannot be decomposed is the single realism score this module refuses to
    publish.
    """
    if not panels_252 or not panels_504:
        raise ValidationError(
            "dual_horizon_loss needs panels at both horizons; scoring one "
            "and validating on the other is the failure this function exists "
            "to prevent"
        )
    if weight_504 < 0:
        raise ValidationError(f"weight_504 must be >= 0, got {weight_504}")

    near = band_distance_loss(panels_252)
    far = band_distance_loss(panels_504, seed_sd=SEED_SD_504,
                             bands=REAL_MARKETS_504)
    return {
        "loss": near["loss"] + weight_504 * far["loss"],
        "loss_252": near["loss"],
        "loss_504": far["loss"],
        "weight_504": weight_504,
        "horizon_252": near,
        "horizon_504": far,
    }


# --------------------------------------------------------------------------
# The scoring rule: a residual objective with curvature everywhere
#
# `band_distance_loss` above is flat inside the band, and the pass count in
# the calibration searchers is flat inside it too: 1,139 of the 1,648
# candidate-block records this project has ever measured read fourteen of
# fourteen in band and tie. A search cannot tell dead centre from one part in
# a thousand inside an edge, so it stops at the first admissible point it
# reaches, which is the mechanical account of twenty-two shipped dials
# sitting on band edges.
#
# The rule replaces the COUNT and leaves the band alone. Per certified row,
#
#     z_i = (T_i - c_i) / sqrt(se_R,i^2 + se_M,i^2)
#     S   = sum_i (nu_i + 1) * ln(1 + z_i^2 / nu_i)          minimised
#
# `T_i` is the row's graded statistic on the candidate's own seeds, by the
# row's own estimator (`facts.AGGREGATE`); `c_i` is the tape centre by the
# SAME estimator at the SAME window length (`facts.rule_row`); `se_R,i` is
# the tape's standard error of that centre; `se_M,i` is the candidate's own
# sampling error at its seed count; and `nu_i` is the Welch-Satterthwaite
# combination of the two. Each term is twice the negative log-density of a
# Student-t with `nu_i` degrees of freedom, up to a constant.
#
# WHY THE t AND NOT A PLAIN SUM OF SQUARES. Near the centre they are the
# same: `d^2 l / dz^2` at zero is `2 (nu + 1) / nu`, at least the square's 2,
# so the surface a search close to the optimum feels is at least as steep.
# Far out the square's influence is unbounded and the t's is not, and that
# is the difference that matters here: under a quadratic, a row the model
# CANNOT reach is bought down at the expense of every row it can, and the
# optimiser reports the best available compensation. At the corpus's own
# degrees of freedom -- one row at z = 6 with nu = 5, three at the centre
# with nu = 8 -- moving the far row to 4 and the three to 1.5 SAVES 13.25
# under the square and COSTS 2.7 under the rule, so the miss stays visible
# at its own |z| instead of being smeared across the panel.
# `tests/test_scoring_rule.py` builds that case and asserts the two signs.
#
# WHY THE TAPE'S STANDARD ERROR AND NOT ITS ACROSS-WINDOW SD. The band is a
# prediction interval for one real year and prices that at
# `BAND_RULE_TOLERANCE`. The graded quantity is a thirty-seed median. An
# objective scaled by the across-year sd asks "could a real year read this",
# which the band already answers; the objective asks "is the model's central
# value the tape's", and the scale of that question is the error of the
# tape's centre.
#
# WHY THE CANDIDATE'S OWN SEED ERROR AND NOT `SEED_SD`. Those tables are
# pt-v1's noise, frozen on purpose as the denominator of every published
# room figure. Measured against the vectors a search actually visits, the
# 504-day table is off by 0.46 to 6.04: it understates `sector_excess_corr`
# six-fold and overstates `leverage_effect` two-fold, because the table is a
# property of pt-v1 and the noise is a property of the vector. An objective
# dividing by pt-v1's number weights the six-fold row thirty-six times too
# heavily. So `scoring_rule_from_medians` takes `se_model` and `df_model` as
# REQUIRED arguments with no default, and warns if a frozen table is handed
# to it anyway.
#
# WHAT IT IS NOT. It is not a certificate: the band remains the
# certification gate and `BAND_RULE_TOLERANCE` remains the gate's
# false-alarm rate. The objective ranks, the band certifies, and neither is
# traded for the other. No band edge enters `S`; the rule reads `c` and
# `se` and never `lo` or `hi`, and the band position is printed beside every
# row as a diagnostic and never summed.
# --------------------------------------------------------------------------

#: The estimator of the model term, per aggregation kind, as data rather than
#: as three branches: a median row's normal-approximation median error, a
#: mean or rate row's ordinary `sd / sqrt(n)`, and a pooled row's seed
#: bootstrap. The NORMAL APPROXIMATION and not the bootstrap for a median,
#: deliberately: the library reports both and they disagree by up to two-fold
#: on the skewed rows, an objective needs exactly one, and the property that
#: decides it is smoothness in the dials. A bootstrap se of a thirty-seed
#: median jumps as seeds reorder; the normal approximation moves with the
#: values. The bootstrap rides along as `se_model_bootstrap` for the reader,
#: and `facts.mechanism_verdict`'s sign test remains the mechanism gate.
MODEL_SE_ESTIMATOR = {
    "median": "facts.median_se, the normal approximation "
              "MEDIAN_SE_FACTOR * sd / sqrt(n)",
    "mean": "sd / sqrt(n) of the per-seed values",
    "pooled_rate": "sd / sqrt(n) of the per-seed rates",
    "pooled": "a seed bootstrap of the pooled median: resample the SEEDS "
              "with replacement, re-pool their session samples, take the "
              "median, and report the sd across draws",
}


def rule_table(horizon_days: int,
               rows: Sequence[str] | None = None) -> dict[str, dict[str, Any]]:
    """The tape side of every row at this horizon, blind rows included.

    One `facts.rule_row(..., require=False)` per row, so a row with no
    centre, no error or no degrees of freedom comes back carrying its
    `missing` tuple rather than raising here: the objective has to be able
    to say WHICH rows it is blind on, and an exception cannot be summed
    over.
    """
    if rows is None:
        rows = tuple(SHAPE) + tuple(LEVEL) + tuple(CRISIS) + tuple(PERSISTENCE)
    return {key: rule_row(key, horizon_days=horizon_days, require=False)
            for key in rows}


def rule_fingerprint(table: Mapping[str, Mapping[str, Any]]) -> str:
    """A digest of every centre, error and df a score was taken against.

    A score is only comparable with another taken against the same tape, and
    the tape moves: the 504-bar windows landed after the corpus was measured,
    the level row's centre was re-derived in September, and
    `fear_gauge_dn3`'s error landed on 2026-09-09 and moved the fingerprint
    at both horizons -- every score taken before it is an eighteen-row sum
    and every score after it a nineteen-row one. So every result carries the
    fingerprint of the table it used, and two scores with different
    fingerprints are two numbers rather than a comparison.
    """
    payload = json.dumps(
        [[key, t.get("centre"), t.get("se"), t.get("df"), t.get("estimator")]
         for key, t in sorted(table.items())],
        sort_keys=True, default=float)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _welch(se_real: float, df_real: float,
           se_model: float, df_model: float) -> tuple[float, float]:
    """The combined error and its Welch-Satterthwaite degrees of freedom."""
    se = math.sqrt(se_real ** 2 + se_model ** 2)
    denom = (se_real ** 4 / df_real) + (se_model ** 4 / df_model)
    return se, (se ** 4 / denom if denom > 0 else math.inf)


def rule_term(z: float, df: float) -> float:
    """`(nu + 1) * ln(1 + z^2 / nu)`, twice a Student-t's negative log-density.

    An identity, so it is written as one and takes no constant. At large
    `nu` it is `z^2`; at small `nu` it grows as a logarithm, which is the
    bounded influence the whole form exists for.
    """
    if df <= 0:
        raise ValidationError(
            f"a t term needs positive degrees of freedom, got {df}")
    if math.isinf(df):
        return z * z
    return (df + 1.0) * math.log1p(z * z / df)


def _band_of(key: str, horizon_days: int) -> tuple[float, float] | None:
    """The row's band at this horizon, for the diagnostic and never for `S`.

    `envelope` owns the seventeen-row table at 504 -- the fourteen shape
    bands plus the level and crisis rows carrying their 252-day bands, each
    argued in that module -- so it is imported HERE rather than at module
    scope: `envelope` reads `loss.STRUCTURAL` inside one of its own
    functions, and a module-level import in both directions is a cycle
    waiting for whichever is loaded first.
    """
    from . import envelope
    table = envelope.RULERS_BY_HORIZON.get(int(horizon_days))
    if table is None:
        return None
    band = table[0].get(key)
    return None if band is None else (float(band[0]), float(band[1]))


def _seed_bootstrap_pooled_median(panels: Sequence[Mapping[str, Any]],
                                  key: str, *, draws: int,
                                  seed: int) -> float | None:
    """The sd of the pooled median under resampling the SEEDS, not the sessions.

    The seed is the unit of replication: a run contributes however many
    sessions its own draw produced, and resampling sessions would price the
    error of a median over one long series rather than over thirty runs.
    """
    per_seed = [tuple(p.get(key + "_samples") or ()) for p in panels]
    per_seed = [s for s in per_seed if s]
    if len(per_seed) < 2:
        return None
    rng = random.Random(seed)
    n = len(per_seed)
    medians = []
    for _ in range(draws):
        pooled: list[float] = []
        for _ in range(n):
            pooled.extend(rng.choice(per_seed))
        if pooled:
            medians.append(statistics.median(pooled))
    if len(medians) < 2:
        return None
    return statistics.stdev(medians)


def _blind_reason(key: str, tape: Mapping[str, Any]) -> str:
    """Why a row with an incomplete tape side is out of the sum, in its words."""
    return (f"the tape side of {key} is missing "
            f"{', '.join(tape['missing'])}: "
            + (REAL_MARKETS_PROVENANCE.get(key, {}).get("centre_se_pending")
               or f"derive it and record it beside {key!r}; the rule never "
                  "substitutes a neighbour's"))


def scoring_rule(panels: Sequence[Mapping[str, Any]], *,
                 horizon_days: int,
                 rows: Sequence[str] | None = None,
                 bootstrap_draws: int = 2000,
                 bootstrap_seed: int = 20260905) -> dict[str, Any]:
    """`S` over the certified rows, from the candidate's own per-seed panels.

    `panels` is one `facts.measure` result per seed, which is what every
    gate run already produces; the model term is taken from those seeds and
    not from a frozen table, so it is the candidate's own.

    Returns `{"S", "S_gauss", "rows", "blind", "n_seeds", "horizon_days",
    "rule_fingerprint"}`. `S_gauss` is `sum z^2`, reported beside `S` and
    never searched on: it is the quadratic whose unbounded influence buys an
    unreachable row down at every other row's expense.

    A ROW THE TAPE CANNOT SCORE IS LISTED, NEVER SUBSTITUTED. `blind` maps
    the row to the reason, `S` sums the rest, and no neighbour's centre or
    error is read for it -- `SEED_SD_LEVEL_PROVENANCE` already rules that for
    the seed scale and the rule inherits it for both terms. A score printed
    without its blind list is not a score: it is a sum over an unnamed subset
    presented as a sum over the panel.
    """
    panels = list(panels)
    if len(panels) < 2:
        raise ValidationError(
            f"the model term is the spread across seeds, so the rule needs "
            f"at least two per-seed panels, got {len(panels)}")
    table = rule_table(horizon_days, rows)
    graded = aggregate_panels(panels, keys=list(table))
    noise = RULERS_BY_HORIZON.get(int(horizon_days), {}).get("seed_sd", {})

    out_rows: dict[str, Any] = {}
    blind: dict[str, str] = {}
    total = gauss = 0.0
    for key, tape in table.items():
        kind = AGGREGATE.get(key, "median")
        values = [p[key] for p in panels if p.get(key) is not None]
        se_m: float | None = None
        se_m_boot: float | None = None
        df_m: float | None = None
        if kind == "pooled":
            se_m = _seed_bootstrap_pooled_median(
                panels, key, draws=bootstrap_draws, seed=bootstrap_seed)
            df_m = None if se_m is None else len(panels) - 1
        elif len(values) >= 2:
            if kind == "median":
                se_m = median_se(values)
                se_m_boot = median_se_bootstrap(
                    values, draws=bootstrap_draws, seed=bootstrap_seed)
            else:
                se_m = statistics.stdev(values) / math.sqrt(len(values))
            df_m = len(values) - 1
        band = _band_of(key, horizon_days)
        measured = graded.get(key)
        row: dict[str, Any] = {
            "row": key,
            "T": measured,
            "centre": tape.get("centre"),
            "se_real": tape.get("se"),
            "df_real": tape.get("df"),
            "se_model": se_m,
            "se_model_bootstrap": se_m_boot,
            "df_model": df_m,
            "estimator": tape.get("estimator"),
            "model_estimator": MODEL_SE_ESTIMATOR[kind],
            "source": tape.get("source"),
            "band": band,
            "in_band": (None if band is None or measured is None
                        else band_distance(measured, *band) == 0),
            # The band POSITION, printed and never summed. The half-width is
            # 2.6 to 6.2 combined standard errors across the rows, set by the
            # band rule's rounding and range, so summing it would weight rows
            # against each other by an accident of the band's geometry --
            # which is the geometry the objective exists to stop reading.
            "band_position": (None if band is None or measured is None
                              or band[1] == band[0]
                              else (measured - band[0]) / (band[1] - band[0])),
            "room_sd": (None if band is None or measured is None
                        or not noise.get(key) else
                        min(measured - band[0], band[1] - measured)
                        / noise[key]),
            "se": None, "z": None, "df": None, "term": None,
        }
        why = None
        if measured is None:
            why = (f"{key} is not in these panels, so the rule has no "
                   "graded value for it")
        elif tape.get("missing"):
            why = _blind_reason(key, tape)
        elif se_m is None or df_m is None:
            why = (f"the model term of {key} needs at least two per-seed "
                   f"readings and these panels carry {len(values)}"
                   + ("; a pooled row needs its per-seed session samples"
                      if kind == "pooled" else ""))
        if why is not None:
            blind[key] = why
            out_rows[key] = row
            continue
        se, df = _welch(tape["se"], tape["df"], se_m, df_m)
        z = (measured - tape["centre"]) / se
        row["se"] = se
        row["z"] = z
        row["df"] = df
        row["term"] = rule_term(z, df)
        total += row["term"]
        gauss += z * z
        out_rows[key] = row

    return {
        "S": total,
        "S_gauss": gauss,
        "rows": out_rows,
        "blind": blind,
        "scored": sorted(k for k in out_rows if k not in blind),
        "n_seeds": len(panels),
        "horizon_days": int(horizon_days),
        "rule_fingerprint": rule_fingerprint(table),
    }


def scoring_rule_from_medians(medians: Mapping[str, float], *,
                              horizon_days: int,
                              se_model: Mapping[str, float],
                              df_model: float,
                              rows: Sequence[str] | None = None
                              ) -> dict[str, Any]:
    """`S` from a record that kept only the aggregated panel, not its seeds.

    The corpus path. `corpus/gates/**` stores one median per row per
    candidate-block and no per-seed values, so the model term cannot be the
    candidate's own and has to come from somewhere the caller names:
    `se_model` and `df_model` are REQUIRED keyword arguments with NO
    DEFAULT, and that is the guard. What it refuses is a call that lets the
    frozen `facts.SEED_SD` tables become the model term by omission -- the
    one substitution the design note measures as wrong by a factor of 0.46
    to 6.04 against the vectors a search actually visits.

    Passing a frozen table EXPLICITLY is allowed and warns, because there
    are readers of the old records for whom pt-v1's scale is the only number
    there is; the warning names it so the reading is not mistaken for the
    candidate's own noise.
    """
    frozen = (("facts.SEED_SD", SEED_SD), ("facts.SEED_SD_504", SEED_SD_504))
    for name, tbl in frozen:
        shared = set(se_model) & set(tbl)
        if shared and all(se_model[k] == tbl[k] for k in shared):
            warnings.warn(
                f"{name} was passed as the scoring rule's model error. It is "
                "pt-v1's across-seed noise, frozen on purpose as the "
                "denominator of every published room figure, and it is NOT "
                "the candidate's: measured against the vectors the corpus "
                "actually holds it runs from 0.46x to 6.04x the truth, so a "
                "row it understates six-fold is weighted thirty-six times "
                "too heavily. Use the candidate's own per-seed spread where "
                "the record has one.",
                RuntimeWarning, stacklevel=2)
            break
    table = rule_table(horizon_days, rows)
    noise = RULERS_BY_HORIZON.get(int(horizon_days), {}).get("seed_sd", {})
    out_rows: dict[str, Any] = {}
    blind: dict[str, str] = {}
    total = gauss = 0.0
    for key, tape in table.items():
        measured = medians.get(key)
        band = _band_of(key, horizon_days)
        se_m = se_model.get(key)
        row: dict[str, Any] = {
            "row": key,
            "T": measured,
            "centre": tape.get("centre"),
            "se_real": tape.get("se"),
            "df_real": tape.get("df"),
            "se_model": se_m,
            "df_model": df_model,
            "estimator": tape.get("estimator"),
            "source": tape.get("source"),
            "band": band,
            "in_band": (None if band is None or measured is None
                        else band_distance(measured, *band) == 0),
            "band_position": (None if band is None or measured is None
                              or band[1] == band[0]
                              else (measured - band[0]) / (band[1] - band[0])),
            "room_sd": (None if band is None or measured is None
                        or not noise.get(key) else
                        min(measured - band[0], band[1] - measured)
                        / noise[key]),
            "se": None, "z": None, "df": None, "term": None,
        }
        why = None
        if measured is None:
            why = f"{key} is not in this record's medians"
        elif tape.get("missing"):
            why = _blind_reason(key, tape)
        elif se_m is None:
            why = (f"no model error was supplied for {key}, and the rule "
                   "never reads a neighbour's")
        if why is not None:
            blind[key] = why
            out_rows[key] = row
            continue
        se, df = _welch(tape["se"], tape["df"], se_m, df_model)
        z = (measured - tape["centre"]) / se
        row["se"] = se
        row["z"] = z
        row["df"] = df
        row["term"] = rule_term(z, df)
        total += row["term"]
        gauss += z * z
        out_rows[key] = row
    return {
        "S": total,
        "S_gauss": gauss,
        "rows": out_rows,
        "blind": blind,
        "scored": sorted(k for k in out_rows if k not in blind),
        "n_seeds": None,
        "horizon_days": int(horizon_days),
        "rule_fingerprint": rule_fingerprint(table),
    }


# --------------------------------------------------------------------------
# R6: score both years separately, report both, combine neither
#
# Simon, 2026-09-06, `programme/RULINGS-2026-09-06.md` R6. This is not either
# of the two options the design note put up. It is NOT the certified horizon
# with the other as a feasibility gate, and it is NOT the two summed under a
# `weight_504` that `dual_horizon_loss` already declines to call a
# derivation. It is: score both, report both, combine neither.
#
# WHY, in one row. `volume_abs_return_corr` sits 0.96 combined standard
# errors BELOW its tape centre at 252 days and at z = +4.38 at 504, where it
# carries 47 per cent of pt-v16's whole score. The model's row moves between
# the horizons and the tape's does not: 0.536 against 0.5345. Any single
# number hides that, whichever way it is formed -- a weighted sum by
# averaging it away, a certified-horizon-only score by never looking.
#
# WHAT IT COSTS, stated because it is not free. There is no maximum any
# more, only a frontier: two candidates can each be better on one horizon
# and neither dominates. `beats` becomes dominance and a generation has a
# non-dominated SET rather than a winner. `atlas.Survey.pareto` makes the
# same argument and this uses its definition of dominance -- no other row at
# least as good on every objective and strictly better on one -- rather than
# a second one.
#
# WHAT IT REFUSES. `dual_scoring_rule` returns no key holding a combined
# score, and `tests/test_scoring_rule.py` asserts that by name. A caller
# that wants one number does not get one from here.
#
# R7, the companion ruling, is stated where it applies: `se_R` is the
# WITHIN-DECADE standard error of the 2015-2025 panel, and the measured
# disagreement between that decade and the 1990-2025 reference -- one to
# three `se_R` on three rows -- is NOT folded into it. That is now a
# decision rather than a default, so `rule_row`'s docstring carries it.
# --------------------------------------------------------------------------

#: The horizons a score is reported at. Both of them, every time.
SCORED_HORIZONS: tuple[int, ...] = (252, 504)


def _dual(results: Mapping[int, Mapping[str, Any]]) -> dict[str, Any]:
    """The two horizons' results side by side, with nothing summed across them.

    The shape is deliberately awkward for a caller that wants a scalar: the
    per-horizon results sit under `horizons`, `S_252` and `S_504` are
    conveniences for reading, and there is no `S`. `combined` records why,
    in the output, so a reader of a serialised score does not have to know
    the ruling to understand the shape.
    """
    out: dict[str, Any] = {
        "horizons": dict(results),
        "scored_horizons": tuple(sorted(results)),
        "combined": None,
        "why_no_combined":
            "R6 (Simon, 2026-09-06): the two horizons are scored, reported "
            "and never combined. volume_abs_return_corr sits 0.96 se below "
            "its tape centre at 252 and at z +4.38 at 504; a single number "
            "hides that whichever way it is formed. Rank on the pair -- "
            "dominance, not a maximum.",
        "rule_fingerprint": {h: r["rule_fingerprint"] for h, r in results.items()},
    }
    for horizon, result in results.items():
        out[f"S_{horizon}"] = result["S"]
        out[f"S_gauss_{horizon}"] = result["S_gauss"]
        out[f"blind_{horizon}"] = result["blind"]
    return out


def dual_scoring_rule(panels_by_horizon: Mapping[int, Sequence[Mapping[str, Any]]],
                      *, rows: Sequence[str] | None = None,
                      bootstrap_draws: int = 2000,
                      bootstrap_seed: int = 20260905) -> dict[str, Any]:
    """`S` at every horizon in `panels_by_horizon`, side by side, uncombined.

    `panels_by_horizon` maps a horizon to that horizon's per-seed panels --
    `{252: [...], 504: [...]}` is what a gate run produces. Each horizon is
    scored against ITS OWN tape table, because the tape's centre and its
    error are both properties of the window length: `abs_return_acf20` reads
    0.020 over 252 bars and 0.029 over 504 on the same reference.

    Raises rather than guessing when a horizon has no panels, and refuses a
    horizon the library has no window table for, through `rule_row`.
    """
    if not panels_by_horizon:
        raise ValidationError(
            "the rule scores both horizons and was given panels for neither; "
            f"pass a mapping like {{252: [...], 504: [...]}}")
    return _dual({int(h): scoring_rule(
        panels, horizon_days=int(h), rows=rows,
        bootstrap_draws=bootstrap_draws, bootstrap_seed=bootstrap_seed)
        for h, panels in panels_by_horizon.items()})


def dual_scoring_rule_from_medians(
        medians_by_horizon: Mapping[int, Mapping[str, float]], *,
        se_model_by_horizon: Mapping[int, Mapping[str, float]],
        df_model: float,
        rows: Sequence[str] | None = None) -> dict[str, Any]:
    """The corpus path at both horizons, from a record's stored medians.

    `se_model_by_horizon` is per horizon and required for the same reason
    `se_model` is: the across-block spread of a block median is a property
    of the window length as much as the tape's is, and at 504 days it runs
    from 0.46x to 6.04x the frozen table depending on the row.
    """
    missing = sorted(set(medians_by_horizon) - set(se_model_by_horizon))
    if missing:
        raise ValidationError(
            f"no model error was supplied at {missing} days; the rule never "
            "reads another horizon's, because the across-block spread of a "
            "block median is a property of the window length")
    return _dual({int(h): scoring_rule_from_medians(
        medians, horizon_days=int(h),
        se_model=se_model_by_horizon[h], df_model=df_model, rows=rows)
        for h, medians in medians_by_horizon.items()})


def dominates(candidate: Mapping[int, float], incumbent: Mapping[int, float],
              *, tolerance: Mapping[int, float]) -> bool:
    """Is `candidate` better at one horizon and no worse at any, beyond noise?

    `atlas.Survey.pareto`'s definition, on a minimised score: no worse on
    every horizon and strictly better on at least one. The tolerance is what
    makes it usable on measurements rather than on exact numbers -- a
    difference inside it is not a difference -- and it is the panel's own
    per-horizon figure, `centre_multiplier(band_rule_tolerance(...))` times
    the paired standard error, rather than a cutoff chosen here.

    Two candidates that each win a horizon dominate each other nowhere and
    both stay on the frontier. That is the cost R6 accepts, and it is a
    property of the model rather than of this function.
    """
    keys = sorted(set(candidate) & set(incumbent))
    if not keys:
        raise ValidationError(
            "dominance needs at least one horizon both were scored at; "
            f"candidate has {sorted(candidate)} and incumbent "
            f"{sorted(incumbent)}")
    no_worse = all(candidate[h] <= incumbent[h] + tolerance.get(h, 0.0)
                   for h in keys)
    better = any(candidate[h] < incumbent[h] - tolerance.get(h, 0.0)
                 for h in keys)
    return no_worse and better


def horizon_cut(horizon_days: int) -> float:
    """The panel's own tolerance at this horizon, as a multiplier on an se.

    `centre_multiplier(band_rule_tolerance(band_windows))`: 1.846 at 252
    days on nine windows and 1.378 at 504 on five, the same construction the
    index tail band took its multiplier from. Nothing is chosen; the number
    falls out of how many real windows the horizon's band rests on.
    """
    return centre_multiplier(band_rule_tolerance(BAND_WINDOWS[int(horizon_days)]))
