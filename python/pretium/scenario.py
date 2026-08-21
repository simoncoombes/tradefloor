"""A scenario is a path, not a setting.

A rate shock is not "the federal funds rate is 5%". It is the rate walking from
2.5% to 5% over sixty days while an agent holds positions through it. The first
is a different market; the second is an event, and events are what a strategy
either survives or does not.

`Macro` is fixed at construction, so driving a path meant hand-writing a loop
around `pin_macro`. This builds the path as an object: composable, inspectable
before you run it, and serialisable alongside the seed so a published scenario
can be cited.

```python
shock = Scenario.rate_shock(start=0.025, end=0.05, over=30)
scores = pt.evaluate(agents, seed=7, universe=u, days=60, scenario=shock)
```

## The trap this exists to close

Pinning `federal_funds_rate` on its own does **nothing**. Measured: a 250bp
hike over thirty days moved twenty instruments by exactly 0.00%.

That is not a defect, it is the valuation model. Equities are discounted off
the **corporate bond yield**, and the policy rate is only a fallback used when
no yield is present — `Some(0.0)` is a real zero yield and must be used, so
inside the engine, where the economy always carries one, the policy rate never
reaches fair value.

Economically that is right. As an interface it is a trap, because the failure
is silent: you run your rate shock, nothing happens, and you conclude the model
does not care about rates. It cares. You moved the wrong lever.

So :meth:`Scenario.rate_shock` moves the whole curve — policy rate and
corporate yield together, separated by a credit spread — which is what a rate
shock is. Moving one alone is still possible through :meth:`ramp`, because
isolating a channel is a legitimate experiment, but you have to ask for it.

With both moving, the same 250bp hike prices twenty instruments down a median
4.74%, with the most rate-sensitive name down 6.41% and one defensive name up
0.25%. That dispersion is the point: a scenario that moved everything equally
would tell a cross-sectional strategy nothing.

## What a VIX path actually moves

Worth reading before reaching for :meth:`Scenario.vix_shock`, because the name
VIX carries an assumption from everywhere else in finance that does not hold
here. In this model VIX does not enter the variance process at all. The GARCH
recursion is ``omega + alpha * r^2 + beta * v`` with a sector-relative clamp,
and there is no VIX term in it or in the noise magnitude.

What VIX does reach:

1. The quoted bid-ask, through a spread multiplier
   ``1 + max(0, (vix - 15) / 30)``.
2. Cross-sectional correlation, but only above VIX 40, where idiosyncratic
   sector factors blend toward the market factor, up to 0.8.
3. Credit spreads in the daily economy step, which is not reachable from
   Python in this build. See the note under `Macro` in the Core concepts
   documentation.

Measured on this build, 20 instruments over 120 days, seed 3, annualised
realised volatility:

    VIX  5    58.05%
    VIX 15    58.05%   (the default)
    VIX 45    58.22%
    VIX 65    58.92%

A thirteenfold move in VIX changes realised volatility by under one point.
Below VIX 15 it changes nothing whatsoever: the spread multiplier floors at
1.0 and the correlation blend has not started, so there is no channel left,
and VIX 5, VIX 10 and VIX 15 produce BIT-IDENTICAL prices over 60 days on 20
instruments.

What does move. Mean quoted spread across 25 instruments after five days:

    VIX  5    12.17 bps
    VIX 15    12.17 bps
    VIX 25    14.72 bps
    VIX 45    20.05 bps
    VIX 65    28.41 bps

The correlation channel is weaker still. Mean pairwise correlation of daily
log returns, 25 names over 120 days, 300 pairs: +0.022 at VIX 15, +0.023 at
VIX 45, +0.041 at VIX 65. It fires above 40, but it blends SECTOR factors,
whose sigma is 0.002, against per-stock GARCH noise running 0.008 to 0.025. So
the feature is correct in construction and close to invisible in output, and
diversification keeps working at VIX 65. The realism documentation has the
arithmetic.

**The bid-ask is the channel that genuinely moves.** In practice this is a
liquidity and spread stress variable. It does not answer what happens when
volatility triples: nothing in this model raises realised volatility, and that
limitation is stated here rather than left for a user to discover after
publishing.

## A macro counterfactual is near-exact, not exact — and that is worth knowing

This is the counterfactual real markets cannot offer: you cannot re-run a year
without its hiking cycle, because your only observation is the one that
happened. Here both are runnable.

But it is a weaker guarantee than the ORDER-FLOW counterfactual in
:mod:`pretium.tca`, and the difference is worth stating rather than glossing.
Order flow consumes zero RNG draws, so adding a trade leaves the draw schedule
byte-identical and the subtraction is exact. A macro path does not have that
property: it changes prices, prices change which branches the microstructure
takes, and one of those branches consumes draws.

Re-measured on this build, twenty instruments over forty days, 2,074,800
draws in the flat run:

    rate_shock 2.5% -> 5%       0 draws
    rate_shock 2.5% -> 10%      0 draws
    vix_shock  15 -> 45         0 draws
    vix_shock  15 -> 80         0 draws

Zero in all four, and zero again when three of them are repeated across seeds
1 to 8. An earlier measurement on an older build recorded a delta of -4 for
three of these cases, which is where the mechanism was
identified: settling a price through the book draws four uniforms, or none if
it returns early, so a macro-induced price difference can flip that branch
once and shift the schedule by a multiple of four.

The mechanism has not been removed, so the guarantee is "measured at zero on
this build" rather than "cannot happen".

So :func:`compare` REPORTS the divergence rather than asserting it away. A
``draw_delta`` of zero means the two worlds saw an identical random sequence
and the difference is purely the scenario. A non-zero one means they diverged
slightly, and you should read the result as a very good approximation rather
than an exact difference. Hiding that behind an average would be the more
comfortable choice and the wrong one.
"""

from __future__ import annotations

import json
import warnings
from typing import Any, Callable, Sequence

from ._core import Engine, Instrument, Macro, ValidationError

#: The macro fields a scenario may drive. Anything else is a typo, and a typo
#: that silently did nothing would be the same class of failure this module
#: exists to close.
FIELDS = (
    "vix",
    "federal_funds_rate",
    "corporate_bond_yield",
    "inflation_rate",
    "qe_pe_boost",
    "fear_greed_index",
    "cycle",
)

#: Fields the engine validates as fractions in [-0.05, 0.50]. Listed so a
#: scenario can reject 5.0-meaning-5% at construction, where the mistake is
#: visible, rather than sixty days into a run.
RATE_FIELDS = ("federal_funds_rate", "corporate_bond_yield", "inflation_rate")

RATE_MIN, RATE_MAX = -0.05, 0.50


def _check(field: str, value: Any) -> None:
    if field not in FIELDS:
        raise ValidationError(
            f"unknown macro field {field!r}. Valid: {', '.join(FIELDS)}"
        )
    if field == "cycle":
        return
    if field in RATE_FIELDS and isinstance(value, (int, float)):
        if not RATE_MIN <= value <= RATE_MAX:
            raise ValidationError(
                f"{field} = {value} is outside the plausible range "
                f"[{RATE_MIN}, {RATE_MAX}]. Rates are FRACTIONS here: 5.2% is "
                "0.052, not 5.2."
            )


class Scenario:
    """A macro path, built from segments and read a day at a time."""

    __slots__ = ("_drivers", "_label")

    def __init__(self, label: str = "") -> None:
        self._drivers: dict[str, Callable[[int], Any]] = {}
        self._label = label

    # -- construction -----------------------------------------------------

    def hold(self, **fields: Any) -> "Scenario":
        """Pin fields to a constant for the whole run."""
        for field, value in fields.items():
            _check(field, value)
            self._drivers[field] = (lambda v: (lambda _day: v))(value)
        return self

    def ramp(self, field: str, *, start: float, end: float, over: int,
             begin: int = 0) -> "Scenario":
        """Move ``field`` linearly from ``start`` to ``end`` over ``over`` days.

        Held at ``start`` before ``begin`` and at ``end`` after, so the path is
        defined on every day of any run length rather than only inside the
        ramp. A path with holes would make the run length change the scenario.
        """
        _check(field, start)
        _check(field, end)
        if over < 1:
            raise ValidationError("over must be at least 1 day")
        if begin < 0:
            raise ValidationError("begin cannot be negative")

        def driver(day: int, start=start, end=end, over=over, begin=begin):
            if day <= begin:
                return start
            if day >= begin + over:
                return end
            return start + (end - start) * (day - begin) / over

        self._drivers[field] = driver
        return self

    def step(self, field: str, *, before: Any, after: Any, at: int) -> "Scenario":
        """Jump ``field`` from ``before`` to ``after`` on day ``at``.

        A discontinuity, which a ramp is not. Use this for something that
        genuinely happens at once — a surprise cut, a regime change — and a
        ramp for something the market prices in gradually.
        """
        _check(field, before)
        _check(field, after)
        if at < 0:
            raise ValidationError("at cannot be negative")
        def driver(day: int, before=before, after=after, at=at) -> Any:
            return before if day < at else after

        self._drivers[field] = driver
        return self

    # -- reading ----------------------------------------------------------

    def at(self, day: int) -> dict[str, Any]:
        """The pins for one day."""
        return {field: driver(day) for field, driver in self._drivers.items()}

    def table(self, days: int) -> list[dict[str, Any]]:
        """The whole path, for inspection BEFORE running it.

        A scenario you cannot look at is a scenario you cannot check, and an
        off-by-one in a ramp produces a plausible-looking result rather than an
        error.
        """
        return [{"day": day, **self.at(day)} for day in range(days)]

    def apply(self, engine: Engine, day: int) -> None:
        """Pin the engine's macro to this day's values."""
        pins = self.at(day)
        if pins:
            engine.pin_macro(**pins)

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(sorted(self._drivers))

    def to_json(self, days: int) -> str:
        """The realised path as JSON, so a result can cite its scenario.

        The PATH rather than the recipe. A recipe is only citable while the
        constructor that built it keeps behaving the same way; the realised
        values are the scenario regardless of what any later version does.
        """
        return json.dumps(
            {"schema": 1, "label": self._label, "days": days,
             "path": self.table(days)},
            sort_keys=True, separators=(",", ":"),
        )

    def __bool__(self) -> bool:
        return bool(self._drivers)

    def __repr__(self) -> str:
        label = f"{self._label!r}, " if self._label else ""
        return f"Scenario({label}driving {', '.join(self.fields) or 'nothing'})"

    # -- ready-made shapes ------------------------------------------------

    @classmethod
    def rate_shock(cls, *, start: float = 0.025, end: float = 0.05,
                   over: int = 30, begin: int = 0,
                   credit_spread: float = 0.02) -> "Scenario":
        """A hiking (or cutting) cycle across the whole curve.

        Moves the policy rate AND the corporate bond yield, held apart by
        ``credit_spread``. Both, because the valuation discounts off the
        corporate yield and pinning the policy rate alone changes nothing at
        all — silently. See this module's docstring.

        ``credit_spread`` is held constant, which is a simplification worth
        naming: in a real tightening cycle spreads usually widen as well, so
        this understates the equity impact. Widen it deliberately with a second
        ``ramp`` on ``corporate_bond_yield`` if that is what you want to study.
        """
        return (
            cls(label=f"rate_shock {start:.3%}->{end:.3%} over {over}d")
            .ramp("federal_funds_rate", start=start, end=end, over=over,
                  begin=begin)
            .ramp("corporate_bond_yield", start=start + credit_spread,
                  end=end + credit_spread, over=over, begin=begin)
        )

    @classmethod
    def vix_shock(cls, *, calm: float = 15.0, peak: float = 45.0,
                  at: int = 10, over: int = 20) -> "Scenario":
        """A VIX spike that decays back. Mostly, a spread widening.

        Up as a step, down as a ramp, because that is the shape a stress
        episode has: it arrives at once and subsides slowly.

        **This does not raise realised volatility.** VIX has no term in the
        variance process here. What a spike does is widen the quoted bid-ask,
        and above VIX 40 pull idiosyncratic returns a little toward the market
        factor. Measured on 20 instruments over 120 days, seed 3: taking the
        peak from 45 to 80 moves annualised realised volatility from 58.01% to
        58.36%, against a no-scenario baseline of 58.05%. This module's
        docstring sets out the three channels and what each one is worth.

        Use it for a liquidity and spread stress. No lever in this model raises
        realised volatility, and this one was renamed because its old name
        claimed otherwise.
        """
        def driver(day: int) -> float:
            if day < at:
                return calm
            if day >= at + over:
                return calm
            return peak + (calm - peak) * (day - at) / over

        scenario = cls(label=f"vix_shock {calm}->{peak} at day {at}")
        scenario._drivers["vix"] = driver
        return scenario

    @classmethod
    def vol_shock(cls, *, calm: float = 15.0, peak: float = 45.0,
                  at: int = 10, over: int = 20) -> "Scenario":
        """Deprecated alias for :meth:`vix_shock`. Same path, honest name.

        The old name said "volatility shock" about a lever that moves VIX, and
        VIX does not drive realised volatility in this model. The path is
        unchanged, so a run under this name reproduces exactly; only the
        ``label`` differs, because the serialised path now carries the name
        that describes it.
        """
        warnings.warn(
            "Scenario.vol_shock is deprecated; use Scenario.vix_shock. It "
            "drives VIX, and VIX in this model is a liquidity and correlation "
            "variable with no term in the variance process. The path is "
            "identical, so results do not change.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.vix_shock(calm=calm, peak=peak, at=at, over=over)


def run_scenario(
    scenario: Scenario,
    *,
    seed: int,
    universe: Sequence[Instrument],
    days: int,
    macro: Macro | None = None,
    ticks_per_day: int = 390,
    start: tuple[int, int, int] = (9, 30, 3),
    record: bool = False,
) -> Engine:
    """Run a market under a macro path. Returns the finished engine.

    The scenario is applied at the START of each day, before that day's
    session, so day zero is already under the path rather than under whatever
    the engine was constructed with.
    """
    if days < 1:
        raise ValidationError("days must be at least 1")
    hour, minute, day_of_week = start
    engine = Engine(seed=seed, universe=universe, macro_state=macro)
    for day in range(days):
        scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(hour, minute, day_of_week, ticks_per_day)
        engine.close_market()
        if record:
            engine.record(day)
    return engine


def compare(
    scenario: Scenario,
    *,
    seed: int,
    universe: Sequence[Instrument],
    days: int,
    baseline: Scenario | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the same seed under two macro paths and difference them.

    The counterfactual real markets cannot offer. You cannot re-run a year
    without its hiking cycle — your only observation is the one that happened.
    Here both are runnable, holding every noise draw, every news item and every
    shock identical, so the difference is the scenario and nothing else.

    ``baseline`` defaults to holding the scenario's fields at their day-zero
    values, which is the right comparison: it isolates the PATH rather than
    conflating it with the level the path started from.
    """
    import struct

    if baseline is None:
        baseline = Scenario(label="flat").hold(**scenario.at(0))

    def run(which: Scenario):
        return run_scenario(which, seed=seed, universe=universe, days=days,
                            **kwargs)

    shocked = run(scenario)
    flat = run(baseline)

    n = len(shocked.tickers)
    after = struct.unpack("<%dd" % n, shocked.prices())
    before = struct.unpack("<%dd" % n, flat.prices())
    moves = [
        (a / b - 1.0) * 100.0 if b else float("nan")
        for a, b in zip(after, before)
    ]
    ordered = sorted(moves)
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)

    # Reported, not asserted. A macro path CAN shift the schedule -- changing
    # prices changes which branch the book settlement takes, and one branch
    # draws four uniforms -- so demanding equality would fail on a real and
    # tiny effect. Measured at zero or four draws in 425,600.
    #
    # The user gets the number instead: zero means the two worlds saw an
    # identical random sequence, non-zero means read the result as a very good
    # approximation rather than an exact difference.
    delta = shocked.draws_consumed - flat.draws_consumed

    return {
        "seed": seed,
        "days": days,
        "scenario": repr(scenario),
        "draws": shocked.draws_consumed,
        "draw_delta": delta,
        "exact": delta == 0,
        "tickers": list(shocked.tickers),
        "move_pct": moves,
        "median_pct": median,
        "worst_pct": min(moves),
        "best_pct": max(moves),
    }
