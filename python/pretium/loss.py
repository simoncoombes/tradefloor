"""The calibration objective: band distance, noise-scaled, diagonally weighted.

This module turns the realism panel of `pretium.facts` into the number a
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
`pretium.facts`.

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

import statistics
from typing import Any, Mapping, Sequence

from ._core import ValidationError
from .facts import REAL_MARKETS, SEED_SD, SEED_SD_PROVENANCE, band_distance

#: The statistics the calibration search is trying to move into band. This
#: is the ONE tuple to edit when a model change makes a structural statistic
#: reachable: append its key here and it enters the loss with the band,
#: verdict wording and seed sd it already has in `pretium.facts`.
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
#: `abs_return_acf20` joined for the same reason lag 5 became live: a
#: measured statistic outside the loss is a direction an optimiser can
#: break for free.
CONSTRAINTS = (
    "excess_kurtosis",
    "volume_abs_return_corr",
    "leverage_effect",
    "abs_return_acf20",
)

#: Reported in every result, excluded from the loss: the panel statistics
#: found structurally unreachable. Derived as the complement so that
#: promoting a statistic is genuinely a one-tuple edit, and so a statistic
#: added to `facts.REAL_MARKETS` is excluded-but-reported by default rather
#: than silently optimised against.
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
    table, two seeds of which it re-measures live), and it is what the
    calibration instrument runs on its own seed panels. Sample (n-1)
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
) -> dict[str, Any]:
    """L_real: the squared, noise-scaled band distances, summed over the live set.

    `panel` is either one statistics mapping -- a `facts.measure` result, or
    already-aggregated medians -- or a sequence of per-seed panels, in which
    case each statistic's m_k is its MEDIAN across seeds, which is how the
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

    All ten panel statistics appear in `"statistics"`, in panel order.
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

    rows: dict[str, dict[str, Any]] = {}
    total = 0.0
    used: dict[str, float] = {}
    for key, (low, high) in REAL_MARKETS.items():
        in_loss = key in LIVE_TARGETS or key in CONSTRAINTS
        role = (
            "live target" if key in LIVE_TARGETS
            else "constraint" if key in CONSTRAINTS
            else "structural"
        )
        values = [p.get(key) for p in panels]
        present = [v for v in values if v is not None]
        if len(present) < len(values):
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

        measured = statistics.median(present)
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

    return {
        "loss": total,
        "weighting": "diagonal",
        "statistics": rows,
        "seed_sd": used,
        "seed_sd_provenance": provenance,
        "panels": len(panels),
    }
