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

Measured, on twenty instruments over forty days — 425,600 draws in the flat
run:

    rate_shock 2.5% -> 5%      -4 draws
    rate_shock 2.5% -> 10%      0 draws
    vol_shock  15 -> 45        -4 draws
    vol_shock  15 -> 80        -4 draws

Always zero or a multiple of four, which identifies the mechanism exactly:
settling a price through the book draws four uniforms, or none if it returns
early, and a macro-induced price difference flipped that branch once. The
largest divergence is 0.00094% of the schedule.

So :func:`compare` REPORTS the divergence rather than asserting it away. A
``draw_delta`` of zero means the two worlds saw an identical random sequence
and the difference is purely the scenario. A non-zero one means they diverged
slightly, and you should read the result as a very good approximation rather
than an exact difference. Hiding that behind an average would be the more
comfortable choice and the wrong one.
"""

from __future__ import annotations

import json
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
    def vol_shock(cls, *, calm: float = 15.0, peak: float = 45.0,
                  at: int = 10, over: int = 20) -> "Scenario":
        """A volatility spike that decays back.

        Up as a step, down as a ramp, because that is the shape volatility
        actually has: it arrives at once and subsides slowly.
        """
        def driver(day: int) -> float:
            if day < at:
                return calm
            if day >= at + over:
                return calm
            return peak + (calm - peak) * (day - at) / over

        scenario = cls(label=f"vol_shock {calm}->{peak} at day {at}")
        scenario._drivers["vix"] = driver
        return scenario


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
