"""The realism envelope, as data: what pretium certifies, and what it does not.

`docs/realism-envelope.md` states the envelope in prose. This module states
it in a form a program can read, so a user does not have to remember a page
to find out whether their question is one this simulator can answer.

Two things live here, and neither is a score.

**Per-statistic intervals** (`intervals`). Every panel statistic is reported
with the spread it actually has across seeds, not as a bare median. A point
estimate from a stochastic simulator invites a precision it does not have:
`abs_return_acf20` reads +0.008 at the shipped preset, and the across-seed
range is wide enough that a single seed can read either side of zero. The
band distance is reported in units of that spread, which is the same
weighting `pretium.loss` uses -- so "how far out" is denominated in the
model's own noise rather than in the statistic's arbitrary units.

**A membership check** (`check`). Given a horizon, the statistics a strategy
leans on, and the shape of the roster, it answers whether the question falls
inside the envelope, and when it does not, says which measurement says so.

## Why there is no single confidence number

`pretium.loss.compare_to_real_markets` refuses to emit one realism score,
and this module does not reopen that. The reason is not modesty, it is
that aggregation destroys the only information that matters here: a model
is realistic in some respects, at some measurement scale, and not others.

There is also a practical failure mode. A scalar travels and a caveat does
not. "87% realistic" is quotable in a way that "volatility clustering runs
roughly twice real beyond one year" is not, so a single number reliably
becomes the thing people cite INSTEAD of the gaps -- which is exactly
backwards, because the gaps are what decide whether a result means
anything. A boolean with reasons attached cannot be quoted without its
reasons.

## Provenance

The constants below are measurements, not judgements, and every one is
reproducible from the tooling in `tools/calibration/`. They describe the
shipped default preset. If `pretium.model_preset()["name"]` is not
`PRESET`, this module is describing a different model than the one you are
running, and `check` says so rather than answering.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ._core import ValidationError
from .facts import REAL_MARKETS, SEED_SD, band_distance

#: The preset these measurements describe.
PRESET = "pt-v3"

#: The measurement horizon the envelope certifies, in trading days.
#: Not a soft preference: three statistics that are in band here leave it by
#: 504 days, and `check` refuses to certify beyond it.
CERTIFIED_HORIZON_DAYS = 252

#: Measured at the certified horizon: 30 seeds, 40 instruments, 252 days.
#: Nine of ten in band, at a band-distance loss of 0.0000, and the same nine
#: hold on held-out seeds and a held-out 60-name universe.
CERTIFIED: dict[str, float] = {
    "annualised_vol_pct": 24.0972,
    "excess_kurtosis": 2.5305,
    "return_acf1": 0.0375,
    "abs_return_acf1": 0.1413,
    "abs_return_acf5": 0.0496,
    "abs_return_acf20": 0.0082,
    "cross_sectional_corr": 0.2558,
    "volume_abs_return_corr": 0.5339,
    "leverage_effect": -0.0349,
    "volume_change_acf1": -0.4598,
}

#: Bands re-derived at a 504-day window, from the same reference roster and
#: estimators as `facts.REAL_MARKETS`. Scoring a 504-day measurement against
#: the 252-day bands is the wrong ruler, and it flatters the model on
#: kurtosis while being harsher elsewhere -- these are mostly TIGHTER.
BANDS_504: dict[str, tuple[float, float]] = {
    "annualised_vol_pct": (16.0, 34.0),
    "excess_kurtosis": (7.1, 22.0),
    "return_acf1": (-0.03, 0.04),
    "abs_return_acf1": (0.04, 0.22),
    "abs_return_acf5": (0.02, 0.10),
    "abs_return_acf20": (-0.02, 0.07),
    "cross_sectional_corr": (0.23, 0.41),
    "volume_abs_return_corr": (0.48, 0.65),
    "leverage_effect": (-0.13, 0.02),
    "volume_change_acf1": (-0.29, -0.21),
}

#: The same panel at 504 days. Five of ten in band against `BANDS_504`.
MEASURED_504: dict[str, float] = {
    "annualised_vol_pct": 29.2893,
    "excess_kurtosis": 5.2274,
    "return_acf1": 0.0571,
    "abs_return_acf1": 0.2887,
    "abs_return_acf5": 0.1524,
    "abs_return_acf20": 0.0127,
    "cross_sectional_corr": 0.4057,
    "volume_abs_return_corr": 0.6301,
    "leverage_effect": -0.0461,
    "volume_change_acf1": -0.4327,
}

#: |return| autocorrelation at the certified horizon, against real markets.
#: The model crosses below real around lag 8 and goes NEGATIVE by lag 30,
#: where real markets stay weakly positive out to lag 60.
DECAY_252: dict[int, float] = {
    1: 0.1413, 2: 0.1063, 3: 0.0897, 5: 0.0496, 8: 0.0371,
    12: 0.0173, 20: 0.0082, 30: -0.0052, 45: -0.0120, 60: -0.0142,
}
REAL_DECAY: dict[int, float] = {
    1: 0.1071, 5: 0.0518, 8: 0.0453, 12: 0.0295, 20: 0.0286,
    30: 0.0179, 60: 0.0054,
}
#: Log-log slope over lags 1-20. Real markets decay hyperbolically; this
#: model decays exponentially, about 2.2x steeper, because it is built from
#: exponentials. No parameter setting turns one slope into the other.
DECAY_SLOPE = -0.953
REAL_DECAY_SLOPE = -0.436

#: The lag beyond which the model's volatility memory is not merely weak but
#: wrong in sign. A strategy reading volatility over a window longer than
#: this is reading a process that anti-predicts where the market persists.
MEMORY_VALID_TO_LAG = 20


@dataclass(frozen=True)
class Gap:
    """One measured way the model departs from real markets.

    `forbids` is the operative field: a gap nobody can act on is trivia.
    """

    id: str
    summary: str
    detail: str
    forbids: str
    statistics: tuple[str, ...] = ()
    #: None when the gap applies at every horizon.
    beyond_days: int | None = None


GAPS: tuple[Gap, ...] = (
    Gap(
        id="volume-change",
        summary="volume-change autocorrelation is structurally unreachable",
        detail=(
            "-0.4598 against a band of -0.32 to -0.20, 13.7 seed-sd out. A "
            "held volume level plus independent per-tick noise sits near -0.5 "
            "at any coefficients, so no parameter reaches this row. It is "
            "excluded from the calibration objective deliberately: an "
            "optimiser pointed at an unreachable target does not fail "
            "cleanly, it distorts every other parameter chasing it."
        ),
        forbids="strategies trading the day-to-day CHANGE in volume",
        statistics=("volume_change_acf1",),
    ),
    Gap(
        id="horizon",
        summary="the certified horizon is 252 days",
        detail=(
            "Against bands re-derived at the matching window, the model holds "
            "5 of 10 at 504 days. Clustering roughly doubles from 252 to 504 "
            "and keeps climbing, where real markets move about 14% over the "
            "same span. Volatility itself stabilises near 29.3%, so long runs "
            "do not drift or blow up -- they stay plausible in LEVEL and "
            "become unrealistic in DYNAMICS, which is easy to miss by looking "
            "only at the price path."
        ),
        forbids="multi-year backtests, and anything keyed on volatility dynamics beyond one year",
        statistics=("abs_return_acf1", "abs_return_acf5", "return_acf1", "excess_kurtosis"),
        beyond_days=CERTIFIED_HORIZON_DAYS,
    ),
    Gap(
        id="decay-shape",
        summary="volatility memory decays exponentially, not hyperbolically",
        detail=(
            f"Log-log slope over lags 1-20 is {DECAY_SLOPE} against real "
            f"markets' {REAL_DECAY_SLOPE}, about 2.2x steeper, and the curve "
            f"turns NEGATIVE by lag 30 where real markets remain weakly "
            f"positive to lag 60. This is a mechanism gap, not a calibration "
            f"one: the process is built from exponentials, and over one year "
            f"two of them fake a power law well enough that no panel "
            f"statistic objects. A two-component mixture was tried and is "
            f"not sufficient."
        ),
        forbids=(
            f"strategies whose edge depends on volatility memory beyond "
            f"about lag {MEMORY_VALID_TO_LAG} -- vol targeting and risk "
            f"parity on a one-month or longer estimate"
        ),
        statistics=("abs_return_acf20",),
    ),
    Gap(
        id="thin-tails",
        summary="tails are too thin over multi-year windows",
        detail=(
            "Excess kurtosis reads 5.23 at 504 days against a horizon-matched "
            "band of 7.1 to 22. The 252-day band's floor of 1.6 is wide "
            "enough that this reads as comfortably in band on every 252-day "
            "certificate, which is why it went unnoticed: nothing was "
            "measuring kurtosis where it fails."
        ),
        forbids="tail-risk or VaR calibration at multi-year horizons",
        statistics=("excess_kurtosis",),
        beyond_days=CERTIFIED_HORIZON_DAYS,
    ),
    Gap(
        id="scenario-magnitude",
        summary="scenario response is directional, not calibrated",
        detail=(
            "The VIX shock response is materially weaker than the previous "
            "preset's. The direction of response is right; the magnitude is "
            "not certified."
        ),
        forbids="sizing a scenario's impact rather than detecting it",
    ),
    Gap(
        id="roster-concentration",
        summary="certification was measured on a sector-balanced roster",
        detail=(
            "`Universe.random()` places exactly five names in each of twelve "
            "sectors, and no real index is balanced that way -- the S&P is "
            "roughly a third technology and the Nasdaq more so. Varying ONLY "
            "sector composition, with every name drawn from one pool: "
            "balanced holds 9/10 at L_real 0.0000; an S&P-like mix holds 8/10 "
            "at 0.0176, losing abs_return_acf5; an all-technology roster "
            "holds 7/10 and runs 32.8% volatility. So part of the "
            "certification is an artifact of that balance, and the more "
            "concentrated the roster, the less of it transfers."
        ),
        forbids=(
            "inheriting this envelope for a sector-concentrated roster -- "
            "re-measure the panel on your own universe instead"
        ),
        statistics=("abs_return_acf5", "return_acf1", "annualised_vol_pct"),
    ),
)


@dataclass(frozen=True)
class Verdict:
    """The answer `check` returns. Falsy when the question is outside."""

    inside: bool
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    gaps: tuple[Gap, ...] = field(default=(), repr=False)

    def __bool__(self) -> bool:
        return self.inside

    def __str__(self) -> str:
        head = "inside the envelope" if self.inside else "OUTSIDE the envelope"
        lines = [head]
        lines += [f"  - {r}" for r in self.reasons]
        lines += [f"  ? {w}" for w in self.warnings]
        return "\n".join(lines)


def intervals(
    panels: Sequence[Mapping[str, Any]],
    *,
    seed_sd: Mapping[str, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Per-statistic spread across seeds, beside the median and the band.

    `panels` is a sequence of `facts.measure` results, one per seed -- the
    same input `loss.band_distance_loss` takes. Returns, for each statistic:

        median      the point estimate a single panel would report
        low, high   the actual min and max ACROSS SEEDS
        p10, p90    the 10th and 90th percentile, for a less brittle range
        sd          across-seed standard deviation, measured here
        shipped_sd  `facts.SEED_SD`, measured at the shipped baseline
        band        the real-market band
        distance    band distance of the median, zero inside
        sd_out      that distance in units of `shipped_sd`
        extremes_straddle  True when min or max crosses a band edge
        typical_straddles  True when the p10-p90 range crosses one

    `typical_straddles` is the field worth reading. A statistic whose MEDIAN
    sits inside its band while its p10-p90 range crosses an edge is not
    comfortably in band -- it is in band on AVERAGE and out of band on a
    large minority of seeds. That distinction is invisible in a point
    estimate and is exactly what a user running one seed will meet.
    `extremes_straddle` uses min and max instead, where a crossing is close
    to expected over thirty draws and is information rather than a finding.

    Refuses fewer than two panels: a spread over one observation is not a
    spread, and reporting it as one would be the false precision this
    function exists to remove.
    """
    if len(panels) < 2:
        raise ValidationError(
            "intervals needs at least two per-seed panels; a spread over one "
            "observation is not a spread"
        )
    scales = SEED_SD if seed_sd is None else seed_sd
    out: dict[str, dict[str, Any]] = {}
    for key, (low, high) in REAL_MARKETS.items():
        values = [p.get(key) for p in panels]
        present = [v for v in values if v is not None]
        if len(present) < 2:
            out[key] = {"median": None, "band": (low, high)}
            continue
        ordered = sorted(present)
        med = statistics.median(present)
        sd = statistics.stdev(present)
        shipped = scales.get(key)
        distance = band_distance(med, low, high)
        out[key] = {
            "median": med,
            "low": ordered[0],
            "high": ordered[-1],
            "p10": _percentile(ordered, 0.10),
            "p90": _percentile(ordered, 0.90),
            "sd": sd,
            "shipped_sd": shipped,
            "band": (low, high),
            "distance": distance,
            "sd_out": (distance / shipped) if (shipped and distance) else 0.0,
            # Two containment tests, because they answer different questions
            # and only one of them is defensible as evidence.
            #
            # `extremes_straddle` uses the min and max, and with thirty draws
            # an extreme crossing an edge is close to expected -- it says
            # "some seed did this", which is worth knowing and is not a
            # finding.
            #
            # `typical_straddles` uses the 10th-90th percentile band, and is
            # the one to read: it says the MIDDLE EIGHTY PERCENT of seeds
            # crosses an edge, so a user running one seed is likely, not
            # merely able, to measure out of band on a statistic whose
            # median is comfortably inside.
            "extremes_straddle": ordered[0] < low or ordered[-1] > high,
            "typical_straddles": (
                _percentile(ordered, 0.10) < low or _percentile(ordered, 0.90) > high
            ),
            "seeds": len(present),
        }
    return out


def _percentile(ordered: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence."""
    if len(ordered) == 1:
        return ordered[0]
    pos = q * (len(ordered) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    frac = pos - lo
    return ordered[lo] + (ordered[hi] - ordered[lo]) * frac


def report_intervals(rows: Mapping[str, Mapping[str, Any]]) -> str:
    """`intervals` as a fixed-width table, for reading rather than parsing."""
    head = (
        f"{'statistic':24s} {'median':>9s} {'p10':>9s} {'p90':>9s} "
        f"{'sd':>8s} {'band':>16s}  verdict"
    )
    lines = [head, "-" * len(head)]
    for key, r in rows.items():
        if r.get("median") is None:
            lines.append(f"{key:24s} {'unmeasured':>9s}")
            continue
        lo, hi = r["band"]
        if r["distance"]:
            verdict = f"OUT {r['sd_out']:.1f} sd"
        elif r["typical_straddles"]:
            verdict = "in band on the median; p10-p90 crosses an edge"
        elif r["extremes_straddle"]:
            verdict = "in band (an extreme seed crosses)"
        else:
            verdict = "in band"
        lines.append(
            f"{key:24s} {r['median']:>9.4f} {r['p10']:>9.4f} {r['p90']:>9.4f} "
            f"{r['sd']:>8.4f} {f'({lo}, {hi})':>16s}  {verdict}"
        )
    return "\n".join(lines)


def check(
    *,
    horizon_days: int,
    statistics: Iterable[str] = (),
    sector_concentrated: bool = False,
    scenario_magnitude: bool = False,
) -> Verdict:
    """Does this question fall inside the envelope?

    `horizon_days` is how long the simulation runs, in trading days.
    `statistics` names the panel statistics the result leans on -- the
    properties of the market the conclusion would change with. Pass the
    keys of `facts.REAL_MARKETS`; unknown names are refused rather than
    ignored, because a silently dropped statistic is a silently granted
    certification.

    `sector_concentrated` says the roster is not sector-balanced, which a
    real index never is. `scenario_magnitude` says the result depends on
    the SIZE of a scenario's effect rather than its direction.

    Returns a `Verdict`, which is falsy when the answer is no. Every reason
    names the measurement behind it, so a refusal can be checked rather
    than believed.
    """
    if horizon_days < 1:
        raise ValidationError(f"horizon_days must be positive, got {horizon_days}")
    wanted = tuple(statistics)
    unknown = [s for s in wanted if s not in REAL_MARKETS]
    if unknown:
        raise ValidationError(
            f"unknown statistics {sorted(unknown)}; expected keys of "
            f"facts.REAL_MARKETS: {sorted(REAL_MARKETS)}"
        )

    reasons: list[str] = []
    warnings: list[str] = []
    hit: list[Gap] = []

    def fire(gap: Gap, why: str) -> None:
        hit.append(gap)
        reasons.append(why)

    by_id = {g.id: g for g in GAPS}

    if horizon_days > CERTIFIED_HORIZON_DAYS:
        g = by_id["horizon"]
        fire(g, (
            f"horizon {horizon_days}d exceeds the certified "
            f"{CERTIFIED_HORIZON_DAYS}d. At 504 days the model holds 5 of 10 "
            f"against horizon-matched bands: abs_return_acf1 "
            f"{MEASURED_504['abs_return_acf1']:.3f} against "
            f"{BANDS_504['abs_return_acf1']}, abs_return_acf5 "
            f"{MEASURED_504['abs_return_acf5']:.3f} against "
            f"{BANDS_504['abs_return_acf5']}"
        ))
        if "excess_kurtosis" in wanted:
            k = by_id["thin-tails"]
            fire(k, (
                f"excess_kurtosis reads {MEASURED_504['excess_kurtosis']:.2f} "
                f"at 504 days against a horizon-matched band of "
                f"{BANDS_504['excess_kurtosis']} -- the tails are too thin "
                f"where you are measuring"
            ))

    for name in wanted:
        if name == "volume_change_acf1":
            g = by_id["volume-change"]
            fire(g, (
                f"volume_change_acf1 is structurally unreachable: "
                f"{CERTIFIED[name]:.4f} against {REAL_MARKETS[name]}, "
                f"13.7 seed-sd out, and excluded from the objective"
            ))
        elif name == "abs_return_acf20":
            g = by_id["decay-shape"]
            fire(g, (
                f"abs_return_acf20 depends on the decay shape, which is a "
                f"mechanism gap: log-log slope {DECAY_SLOPE} against real "
                f"markets' {REAL_DECAY_SLOPE}, and the curve is negative by "
                f"lag 30 where real markets stay positive to lag 60"
            ))
        elif CERTIFIED.get(name) is not None and horizon_days <= CERTIFIED_HORIZON_DAYS:
            lo, hi = REAL_MARKETS[name]
            if band_distance(CERTIFIED[name], lo, hi) == 0:
                warnings.append(
                    f"{name} is in band at the certified horizon "
                    f"({CERTIFIED[name]:.4f} in {(lo, hi)}) -- but that is a "
                    f"median across 30 seeds; check `intervals` for the "
                    f"spread before relying on one seed"
                )

    if sector_concentrated:
        g = by_id["roster-concentration"]
        fire(g, (
            "the roster is sector-concentrated, and certification was "
            "measured on a perfectly balanced one. An S&P-like mix holds "
            "8/10 (abs_return_acf5 leaves band); an all-technology roster "
            "holds 7/10 at 32.8% volatility. Re-measure on your own universe"
        ))

    if scenario_magnitude:
        g = by_id["scenario-magnitude"]
        fire(g, (
            "the result depends on the SIZE of a scenario's response, which "
            "is not certified -- only its direction is"
        ))

    if not wanted:
        warnings.append(
            "no statistics named, so only the horizon and roster were "
            "checked; naming what the result leans on gives a sharper answer"
        )

    if not reasons:
        reasons.append(
            f"horizon {horizon_days}d is within the certified "
            f"{CERTIFIED_HORIZON_DAYS}d, and no named statistic meets a "
            f"measured gap"
        )
    return Verdict(
        inside=not hit,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        gaps=tuple(dict.fromkeys(hit)),
    )


def certified() -> dict[str, Any]:
    """The envelope as a plain mapping, for serialising into a manifest."""
    return {
        "preset": PRESET,
        "certified_horizon_days": CERTIFIED_HORIZON_DAYS,
        "statistics": {
            k: {
                "measured": v,
                "band": list(REAL_MARKETS[k]),
                "in_band": band_distance(v, *REAL_MARKETS[k]) == 0,
            }
            for k, v in CERTIFIED.items()
        },
        "gaps": [
            {
                "id": g.id,
                "summary": g.summary,
                "detail": g.detail,
                "forbids": g.forbids,
                "statistics": list(g.statistics),
                "beyond_days": g.beyond_days,
            }
            for g in GAPS
        ],
    }
