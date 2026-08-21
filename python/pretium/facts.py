"""Stylised facts: what these markets look like, measured, next to real ones.

A simulator you cannot characterise is a simulator you cannot reason about. If
you are going to conclude anything from a strategy's performance here, you need
to know which properties of real markets this model reproduces and which it does
not — and the second list is the one that matters, because that is where a
conclusion will fail to transfer.

So this measures, and the numbers below were produced by running it. They are
not targets the model was tuned to hit; two of them are frank mismatches.

## What matches

**Fat tails.** Excess kurtosis about **+5.9** across seeds, against roughly
+3 to +10 for real daily equity returns. Extreme days are far more common than
a normal distribution allows, which is the single most robust fact about asset
returns and the one a Gaussian simulator gets wrong.

**Volatility clustering, in the right direction.** The autocorrelation of
absolute returns is about **+0.10** at lag one and stays positive at lag five.
Calm follows calm and turbulence follows turbulence, which is what the GARCH
process is there to produce.

## What does not match, and what it costs you

**Volatility clustering is too weak and decays too fast.** Real markets show
0.2–0.3 at lag one with a slow, near-hyperbolic decay that persists for months.
Here it is about **+0.10** and largely gone by lag twenty. A strategy whose
edge is volatility forecasting will look worse here than it should.

Worth recording because it was tested: the GARCH process was being fed the
day's TOTAL RETURN rather than its noise component — the documented fallback,
taken by accident on every close. Fixing that was expected to strengthen
clustering, since it is the correction that makes the variance process see the
shock the model says it should. It did the opposite: clustering fell from
+0.12 to +0.10.

The fix stayed anyway. It is what the model specifies, and the alternative is
keeping a bug because it happened to score better on a statistic — which is
how a model gets tuned toward its own report card instead of toward being
right. These numbers are measurements, not targets, and this is what that
commitment costs when the two disagree.

**Returns are positively autocorrelated, and real ones are not.** This is the
big one. Measured at **+0.219** at lag one, in six seeds out of six, ranging
only from +0.203 to +0.262. Real daily equity returns sit near zero and are if
anything slightly negative.

That is a direct consequence of the AR(2) mispricing process: `s` has positive
short-run persistence, so a move today makes a move the same way tomorrow more
likely. It is not a bug — it is the model — but it has a consequence you must
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
absolute return figures as scaled up, and prefer ratios — capture against the
oracle, shortfall in basis points — over raw percentages.

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

#: What the same statistics look like for real daily equity returns, as
#: reported in the empirical finance literature. Ranges rather than points,
#: because they vary by market, period and universe -- and a single number
#: would imply a precision nobody has.
REAL_MARKETS = {
    "annualised_vol_pct": (15.0, 35.0),
    "excess_kurtosis": (3.0, 10.0),
    "return_acf1": (-0.05, 0.05),
    "abs_return_acf1": (0.15, 0.35),
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

    Cross-sectional statistics are pooled where pooling is meaningful and taken
    as a MEDIAN across instruments where it is not. Autocorrelation is the
    latter: pooling returns from sixty instruments into one series would splice
    sixty unrelated histories end to end and measure the joins.
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
    count = len(universe)
    closes: dict[int, list[float]] = {i: [] for i in range(count)}
    for k in range(len(bars["close"])):
        closes[bars["instrument_id"][k]].append(bars["close"][k])

    pooled: list[float] = []
    return_acf1: list[float] = []
    abs_acf1: list[float] = []
    abs_acf5: list[float] = []
    abs_acf20: list[float] = []
    for i in range(count):
        returns = _log_returns(closes[i])
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

    return {
        "seed": seed,
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


def compare_to_real_markets(facts: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Line each measured statistic up against the empirical range.

    Returns a verdict per statistic rather than an overall score. A single
    "realism score" would average a property this model reproduces well against
    one it gets frankly wrong, and the whole value of the exercise is knowing
    WHICH.
    """
    out: dict[str, dict[str, Any]] = {}
    for key, (low, high) in REAL_MARKETS.items():
        value = facts[key]
        out[key] = {
            "measured": value,
            "real_range": (low, high),
            "matches": low <= value <= high,
            "direction": (
                "within" if low <= value <= high
                else ("above" if value > high else "below")
            ),
        }
    return out


def report(facts: dict[str, Any]) -> str:
    """A human-readable summary, honest about the mismatches."""
    verdicts = compare_to_real_markets(facts)
    lines = [
        f"seed {facts['seed']}, {facts['instruments']} instruments, "
        f"{facts['days']} days, {facts['observations']:,} daily returns",
        "",
        f"{'statistic':22s} {'measured':>10s}  {'real markets':>14s}   verdict",
    ]
    labels = {
        "annualised_vol_pct": "annualised vol %",
        "excess_kurtosis": "excess kurtosis",
        "return_acf1": "return acf(1)",
        "abs_return_acf1": "|return| acf(1)",
    }
    for key, verdict in verdicts.items():
        low, high = verdict["real_range"]
        mark = "matches" if verdict["matches"] else verdict["direction"].upper()
        lines.append(
            f"{labels[key]:22s} {verdict['measured']:>10.3f}  "
            f"{low:>6.2f} to {high:<5.2f}   {mark}"
        )
    lines += [
        "",
        "Carry into any conclusion: returns here are positively",
        "autocorrelated where real ones are not, so momentum is mechanically",
        "profitable in this market in a way it is not in real markets.",
    ]
    return "\n".join(lines)
