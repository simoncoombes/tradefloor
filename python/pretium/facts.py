"""Stylised facts: what these markets look like, measured, next to real ones.

A simulator you cannot characterise is a simulator you cannot reason about. If
you are going to conclude anything from a strategy's performance here, you need
to know which properties of real markets this model reproduces and which it
does not, and the second list is the one that matters, because that is where a
conclusion will fail to transfer.

So this measures, and the numbers below were produced by running it. They are
not targets the model was tuned to hit; most of them are frank mismatches.

## The headline

**This model gets the SHAPE of a return series right and the way things move
TOGETHER wrong.** Fat tails land inside the real band; volatility runs high,
for a reason about how a universe is generated rather than about the price
process. Almost everything about how things move together comes out weaker
than a real market: across stocks, across time within volatility, between
volume and returns, and between the sign of a return and the volatility that
follows it. The one dependence that comes out too strong is return
autocorrelation, and that is the mispricing process showing through.

Every figure below: `Universe.random(40, seed=111)`, 252 days, median over
seeds 1 to 6, at commit a57982e.

## What matches

**Fat tails.** Excess kurtosis **+4.0**, inside the +3 to +10 of real daily
equity returns in all six seeds. Extreme days are far more common than a normal
distribution allows, which is the single most robust fact about asset returns
and the one a Gaussian simulator gets wrong.

**Volatility clustering, in the right direction.** The autocorrelation of
absolute returns is **+0.117** at lag one and stays positive at lag five. Calm
follows calm and turbulence follows turbulence, which is what the GARCH process
is there to produce. Its strength is a separate question, below.

## What does not match, and what it costs you

**Stocks barely move together.** Mean pairwise correlation of daily returns is
**+0.024**, against +0.25 to +0.35 for real equities in a calm market and +0.6
and above in a crisis. The largest gap here, and arithmetic rather than
mystery: the shared market factor has sigma 0.003 a day against a sector daily
sigma of 0.008 to 0.025, so it carries a few percent of a typical name's
variance, and that share is the correlation it can induce. Diversification
works far better here than in any real market, and market beta barely exists.

**Volatility clustering is too weak and decays too fast.** Real markets show
+0.15 to +0.35 at lag one with a slow, near-hyperbolic decay that persists for
months. Here it is +0.117 and largely gone by lag twenty. A strategy whose edge
is volatility forecasting will look worse here than it should.

**Volume and volatility are nearly independent.** Volume correlates with
absolute return at **+0.105**, against a real +0.30 to +0.60. Volume CHANGES
autocorrelate at **-0.463** where real ones sit near zero, which is the
signature of a smooth level plus independent daily noise rather than of
persistent volume shocks. Volume levels autocorrelate above +0.9, which sounds
like persistence and says nothing, because a level autocorrelation is dominated
by the slowly varying level whatever the daily dynamics are.

Execution work is where that bites: VWAP and POV live or die on forecasting the
day's volume, and the hard part in a real market is a volume surprise that
keeps going and arrives with a volatility surprise. Neither happens here.

**There is no leverage effect.** Today's signed return against tomorrow's
absolute return measures **-0.004**, against a real -0.30 to -0.10, and across
six seeds its sign is not even reliably negative. Absent by construction rather
than by calibration: the variance process is a symmetric GARCH(1,1),
`omega + alpha * r^2 + beta * v`, and squaring the return discards its sign. No
symmetric GARCH can produce a leverage effect at any coefficients. Reproducing
one needs an asymmetric term, GJR or EGARCH, which is a model change and not a
calibration.

**Returns are positively autocorrelated, and real ones are not.** This is the
one dependence that is too STRONG. Measured at **+0.233** at lag one, in six
seeds out of six, ranging only from +0.211 to +0.249. Real daily equity returns
sit near zero and are if anything slightly negative.

That is a direct consequence of the AR(2) mispricing process: `s` has positive
short-run persistence, so a move today makes a move the same way tomorrow more
likely. It is the model rather than a bug, and it has a consequence you must
carry into any conclusion drawn here:

> **Momentum is mechanically profitable in this market in a way it is not in
> real markets.** An agent that trades serial correlation has an edge here that
> is an artefact of the process, not a skill that transfers.

This is the specific mechanism behind the general warning that this harness
ranks agents against each other rather than certifying real-world skill. If two
agents differ mainly in how much serial correlation they exploit, their ranking
here says very little about which is better anywhere else.

**Volatility is high.** About **53% annualised**, against roughly 20% for large
caps. A generated universe is deliberately dispersed and skews small; treat
absolute return figures as scaled up, and prefer ratios -- capture against the
oracle, shortfall in basis points -- over raw percentages.

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

## Can the mismatches be fixed? Measured, and the answer is a decision

Investigated with the intent of correcting them rather than defending them.
"Unfixed" and "unfixable here" are different states, and these figures come
from that earlier work rather than from the run above.

**The return autocorrelation CAN be fixed, and the price is the port.** It
comes from the herding term in the mispricing process, `MOMENTUM_THETA = 0.25`.
Lowering it to 0.05 brings the AR(2) impulse response at lag two from 1.284 to
1.029 and the measured autocorrelation from **+0.219 to +0.034 -- inside the
real-market band**, with volatility and kurtosis essentially unchanged.

It also fails seven parity tests, including the one that asserts the model
constants match the reference implementation. These constants are not this
library's to choose: it is a PORT, and its coefficients are the reference's
coefficients. Changing one makes it a fork and invalidates the entire golden
corpus. That is a product decision about whether the two implementations may
diverge, and it cannot be taken from inside a stylised-fact report. The lever
is one constant when that decision is made.

**The cross-sectional correlation is the same class of decision.** Reaching a
realistic +0.30 needs a market factor around 0.65x the stock sigma, roughly
0.0098 for a 1.5%-a-day name, against the 0.0030 in the source. Calibration,
and the port's calibration to make.

**The clustering resists every lever available.** Persistence is not the
problem: `ALPHA + BETA` is 0.99, which is what real equity GARCH shows. The
variance is clamped to between 0.25x and 5x its sector base, and that clamp
binds 16.4% of the time -- 13.9% at the ceiling, which truncates exactly the
high-volatility days where clustering is most visible. Raising the ceiling to
10x lifts clustering only from +0.096 to +0.112, still below the real band,
while pushing annualised volatility from 52.7% to 72%. Lowering
`MOMENTUM_THETA` makes clustering worse, so the two mismatches are coupled
through the same term and cannot both be improved by moving it.

**The leverage effect is not a calibration at all.** A symmetric variance
process has no coefficient that produces asymmetry, so it is a modelling
redesign, under the same port constraint.

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
These numbers are measurements rather than targets, and this is what that
commitment costs when the two disagree.

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

from ._core import Engine, Instrument, Macro, ValidationError
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


def measure(
    *,
    seed: int,
    universe: Sequence[Instrument],
    days: int = 252,
    macro: Macro | None = None,
    scenario: Any = None,
    min_observations: int = 30,
) -> dict[str, Any]:
    """Run a market and report its statistical properties.

    Eight statistics against `REAL_MARKETS`: two marginal, describing one
    return series on its own, and six dependence, describing how things move
    together. The split is the finding, so `report` prints it in two sections.

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

    engine = Engine(seed=seed, universe=universe, macro_state=macro)
    for day in range(days):
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
        engine.record(day)

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
