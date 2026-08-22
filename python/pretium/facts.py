"""Stylised facts: what these markets look like, measured, next to real ones.

A simulator you cannot characterise is a simulator you cannot reason about. If
you are going to conclude anything from a strategy's performance here, you need
to know which properties of real markets this model reproduces and which it
does not, and the second list is the one that matters, because that is where a
conclusion will fail to transfer.

So this measures, and the numbers below were produced by running it. An
earlier era could add "they are not targets the model was tuned to hit";
this one cannot -- four of the eight became calibration targets at the
2026-08 era boundary -- so the disclosure of which four, and of how the
held-out checks read, is part of the measurement now.

## The headline

**At the published method, the way things move together now sits inside the
real bands, and what still fails is scale and memory**: volatility runs
high, returns trend where real ones do not, and volume shocks do not
persist. That is the reverse of what this docstring said through the
2026-08 era boundary -- the measured thesis then was "marginals right,
dependence wrong", with cross-sectional correlation at +0.024 against a
real +0.25 to +0.35. Three of the era's model changes (the GJR asymmetry
term, conditional volatility on the shared market factor, that volatility's
VIX coupling) were aimed at exactly those gaps and were CALIBRATED against
the statistics this module reports, which changes how the in-band verdicts
must be read -- see "Four of the eight were targets" below.

Every figure below: `Universe.random(40, seed=111)` (fingerprint
5d8de78b55aad752), 252 days, `measure()` per sim seed, median over seeds 1
to 6 -- re-measured at known-answer v8 (era digest 1ee64998...), where the
superseded figures beside them are marked with the era they belonged to.

## What lands

**Stocks move together, and stop being diversifiable in a crisis.** Mean
pairwise correlation of daily returns is **+0.257** (seed range +0.205 to
+0.456), inside the real +0.25 to +0.35 for a calm market -- measured at
+0.024 one era ago, the largest gap this module has ever carried. The
mechanism that closed it: the shared market factor now carries its own
conditional-variance process at a baseline sigma of 0.016 against the
reference's 0.003, funded by scaling per-name idiosyncratic noise by 0.84
rather than added on top. The crisis half is the VIX coupling: pinned VIX
45 takes the same correlation to +0.68 (see `pretium.scenario`).

**Fat tails survive the correlation.** Excess kurtosis **+3.1** (+2.4 to
+5.7 across seeds), inside the real +3 to +10. Worth stating next to the
correlation, because the pre-era model could have either but not both: a
constant-sigma Gaussian factor diluted the GARCH tails in exact proportion
to the correlation it induced. A factor whose variance is persistent and
shock-driven is fat-tailed by variance mixing, so the correlated share now
contributes kurtosis instead of spending it.

**Volatility clusters at real strength, and market-wide.** |return|
autocorrelation at lag one is **+0.242** (+0.189 to +0.454), inside the
real +0.15 to +0.35 -- it read +0.117, below band, one era ago. The memory
is still short: +0.090 at lag five, gone by lag twenty (-0.006), where
real clustering persists for months. The strength is real now; the
persistence is not, so a volatility forecaster is still tested against a
market with less to forecast than a real one.

**Volume arrives with volatility.** Volume against absolute return is
**+0.585** (+0.541 to +0.655), inside the real +0.30 to +0.60. It read
+0.105 before the era boundary: the `avg_volume` feedback compounded the
level a percent-plus a day, and that trend swamped the covariation. The
level is held now, and the per-tick channel -- volume scales with the size
of the day's move by construction -- shows through.

**The leverage effect exists, and is the weakest thing the era bought.**
Today's
signed return against tomorrow's absolute return is **-0.085**, just above
(weaker than) the real -0.30 to -0.10, with the sign finally stable:
negative on six seeds of six, range -0.181 to -0.031, where the symmetric
pre-era GARCH could not hold the sign at all -- no symmetric variance
process produces asymmetry at any coefficients, and the era added a GJR
term instead. Mind the negative band when reading this row: a value ABOVE
a negative band is an effect too WEAK, not too strong, which is why
`_verdict` and `band_distance` below carry their own sign handling.

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

**Volatility is high.** About **41.5% annualised** (39% to 50% across
seeds) against roughly 20% for large caps -- down from 53% pre-era,
because the factor's variance was funded rather than added, and still
above the band for a reason about how a universe is generated rather than
about the price process: a generated roster is deliberately dispersed and
skews small. Prefer ratios -- capture against the oracle, shortfall in
basis points -- over raw percentages.

**Volume shocks do not persist, by construction.** Volume CHANGES
autocorrelate at **-0.446** (-0.454 to -0.425) where real ones sit near
zero: daily volume is a held per-name level times bounded per-tick
multipliers independent day to day, and the first difference of such a
series autocorrelates near -0.5 as arithmetic. No constant reaches this
row; it needs volume dynamics the engine does not model. Execution work is
where it bites: VWAP and POV live or die on forecasting the day's volume,
and the hard part in a real market is a volume surprise that keeps going
and arrives with a volatility surprise. The arriving-together half is now
present; the keeps-going half is absent, so a forecast here is never wrong
twice running.

## Four of the eight were targets

The dependence rows stopped being pure measurements at the era boundary:
the sweeps that chose the era's constants (`tools/calibration/`) scored
candidates on these eight statistics, at this exact method -- this
universe, these seeds, this horizon. Correlation, kurtosis, clustering and
the leverage effect are calibrated quantities now; return autocorrelation,
the volatility level and the volume-change autocorrelation were not
targeted, and it shows -- they are the three still out of band. A
statistic a model was tuned to hit is evidence about the tuning, not the
model, so this docstring must not sound more confident than the held-out
results: on fresh sim seeds (101-106) correlation slips to +0.225, just
under the band floor; on five fresh 60-name universes the leverage effect
halves to -0.04/-0.05; on 504 days the dependence structure holds and
volatility drifts to 47.6%. In band at the published method, at the band
edge -- on either side of it -- everywhere else. A conclusion that needs a
dependence statistic deep inside its band should re-measure on its own
universe and seeds rather than inherit these figures.
`docs/how-realistic-is-this-market.md` carries the full held-out record
with every method stated.

## Why there are eight statistics and not four

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
  constant. Until then -0.45 is structural.

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

#: What the same statistics look like for real daily equity returns, as
#: reported in the empirical finance literature. Ranges rather than points,
#: because they vary by market, period and universe -- and a single number
#: would imply a precision nobody has.
REAL_MARKETS = {
    "annualised_vol_pct": (15.0, 35.0),
    "excess_kurtosis": (3.0, 10.0),
    "return_acf1": (-0.05, 0.05),
    "abs_return_acf1": (0.15, 0.35),
    "cross_sectional_corr": (0.25, 0.35),
    "volume_abs_return_corr": (0.30, 0.60),
    "leverage_effect": (-0.30, -0.10),
    "volume_change_acf1": (-0.05, 0.15),
}

#: The across-seed standard deviation of each statistic at the shipped
#: baseline (MARKET_FACTOR_SIGMA = 0.0075), from the post-RNG-split sweep
#: re-run. It ships beside the bands because a band exit is only comparable
#: across statistics once it is priced in units of that statistic's own
#: sampling noise: pooled volatility runs ~55 on a band of width 20 while
#: every autocorrelation is measured in hundredths, and any comparison that
#: ignores the scales silently becomes a comparison of volatility alone.
#: `pretium.loss` consumes these as its diagonal weighting.
#:
#: Provenance, and the history behind the values. CALIBRATION.md section 6.1
#: tabulates seed sds measured pre-stream-split (git_rev 7c2877e) -- and, it
#: turns out, at the OLD baseline sigma of 0.003, the shipped value at
#: measurement time; every entry of that table matches the pre-split sweep
#: file's 0.003 point to the printed digit. The values here are re-derived at
#: the CURRENT baseline (0.0075) from the post-split re-run, so two changes
#: separate them from the document's table: the sigma recalibration and the
#: RNG stream split. The full chain, sample sd over the same six seeds:
#:
#:   statistic              doc @0.003   pre-split @0.0075   here @0.0075
#:   annualised vol %          0.87           0.888              0.878
#:   excess kurtosis           0.39           0.317              0.248
#:   return acf(1)             0.015          0.009              0.015
#:   |return| acf(1)           0.013          0.016              0.017
#:   cross-sectional corr      0.013          0.017              0.014
#:   volume vs |return|        0.015          0.009              0.013
#:   leverage                  0.022          0.021              0.014
#:   volume change acf(1)      0.008          0.007              0.006
#:
#: A six-seed sd is itself noisy -- its relative sampling error is roughly
#: 1/sqrt(2(n-1)) = 32% -- and no move in either step exceeds about 1.5 of
#: those sigmas, so the table corroborates the sweep re-run's finding: the
#: split re-dealt the draws without changing the process law. Phase 2 of the
#: calibration programme re-estimates these on thirty seeds;
#: `pretium.loss.seed_sd_from_panels` is the estimator, and the loss takes
#: the result as a parameter rather than requiring an edit here.
SEED_SD = {
    "annualised_vol_pct": 0.878001,
    "excess_kurtosis": 0.24832,
    "return_acf1": 0.0152672,
    "abs_return_acf1": 0.0165008,
    "cross_sectional_corr": 0.0137033,
    "volume_abs_return_corr": 0.013005,
    "leverage_effect": 0.0141133,
    "volume_change_acf1": 0.00565191,
}

#: Where SEED_SD's values come from, carried as data so any consumer -- the
#: loss report, a calibration manifest -- can quote it rather than assert it.
SEED_SD_PROVENANCE = {
    "source": "tools/calibration/results/"
              "market-factor-sigma-2026-08-21-post-rng-split.json",
    "git_rev": "ad91026",
    "sweep_point": "MARKET_FACTOR_SIGMA = 0.0075 (the shipped baseline)",
    "seeds": (1, 2, 3, 4, 5, 6),
    "estimator": "sample standard deviation (n - 1) across seeds",
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
        # autocorrelation can: persistent volume shocks, which real markets
        # have, keep it near zero, and a smooth level plus independent daily
        # noise drives it toward -0.5.
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

    A NEGATIVE band needs its own wording. A leverage effect of -0.01 against
    a band of -0.30 to -0.10 is numerically above the band and semantically
    absent, so reporting it as "too high" states the opposite of the finding.
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
    because the two share the hazard: on a band that is entirely negative --
    leverage, -0.30 to -0.10 -- naive handling silently inverts. `_verdict`
    solves the wording half (above a negative band is "too weak", and the
    improving direction is DOWN); this solves the arithmetic half. The
    specific failure this form exists to prevent is the one-sided
    max(0, value - high), under which a leverage effect of -0.5 -- a large
    OVERSHOOT past the strong edge -- would read as satisfying the band. The
    two-sided form charges it low - value = 0.2, on the same footing as the
    absent-effect exit on the weak side.

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

    Eight statistics against `REAL_MARKETS`: two marginal, describing one
    return series on its own, and six dependence, describing how things move
    together. The split is the finding, so `report` prints it in two sections.

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
