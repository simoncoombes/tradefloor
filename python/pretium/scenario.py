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

Pinning `federal_funds_rate` on its own does **nothing inside the first
central-bank meeting window**. Measured on this build, on
``Universe.random(20, seed=4)`` at sim seed 5: a 250bp policy-only ramp over
thirty days moved twenty instruments by exactly 0.00% at 40 days.

That is not a defect, it is the valuation model. Equities are discounted off
the **corporate bond yield**, and the policy rate is only a fallback used when
no yield is present — `Some(0.0)` is a real zero yield and must be used, so
inside the engine, where the economy always carries one, the policy rate never
reaches fair value directly.

Since the macro chain runs endogenously (2026-08), transmission exists but is
lagged: the corporate yield is recomputed from the 10Y at central-bank
MEETINGS, the first of which is scheduled 45 days out. Measured at 60 days,
the same policy-only ramp prices the median instrument down 4.19%. So the
trap is now a horizon trap: a short study sees nothing, silently.

The failure mode survives: you run a month-long rate shock, nothing happens,
and you conclude the model does not care about rates. It cares — at meeting
cadence, through the curve. For an immediate repricing you still have to move
the yield equities actually discount off.

So :meth:`Scenario.rate_shock` moves the whole curve — policy rate and
corporate yield together, separated by a credit spread — which is what a rate
shock is. Moving one alone is still possible through :meth:`ramp`, because
isolating a channel is a legitimate experiment, but you have to ask for it.

With both moving, the same 250bp hike (measured at 60 days, same universe
and seed) prices twenty instruments down a median 4.42%, with the most
rate-sensitive name down 6.94% and the least sensitive one unmoved at
0.00%. That dispersion is the point: a scenario that moved everything
equally would tell a cross-sectional strategy nothing.

## What a VIX path actually moves

For most of this model's history the honest answer was "not volatility",
this section said so, and tests pinned it. That changed in the 2026-08 era:
the shared market factor carries its own conditional-variance process, and
its reversion target is now proportional to VIX squared — VIX read as the
factor's implied volatility, anchored so that VIX 15 (the endogenous mean)
reproduces the autonomous process exactly. The coupling was measured before
it was switched on, and this section was rewritten in the same change that
switched it, because the old claims were load-bearing.

What VIX reaches now:

1. **The market factor's variance target** — the volatility channel. Each
   close feeds the day's VIX into the factor's GARCH reversion target as
   ``(vix / 15)^2``. The per-name idiosyncratic GARCH still has no VIX
   term: what VIX scales is the SHARED component of every return, which is
   why a crisis VIX is simultaneously a volatility regime and a
   correlation regime.
2. The quoted bid-ask, through a spread multiplier
   ``1 + max(0, (vix - 15) / 30)``.
3. Cross-sectional correlation above VIX 25.5 (the crisis threshold since
   the 2026-08 re-site; it was a dead ``vix > 40`` before), where
   idiosyncratic sector factors blend toward the market factor, up to 0.8.
4. Credit spreads in the daily economy step. Since the macro chain runs
   endogenously this channel is LIVE from Python: the corporate spread
   carries a VIX term and is recomputed at central-bank meetings, the first
   of which sits at day 45.

Measured on this build — ``Universe.random(20, seed=11)``, 120 days, sim
seed 3, pinned through the scenario API — annualised realised volatility:

    VIX  5     49.48%
    VIX 15     58.76%   (the anchor; bit-identical to the uncoupled model)
    VIX 45    107.07%
    VIX 65    124.31%

A thirteenfold move in VIX now moves realised volatility by a factor of
2.5. Sub-15 pins are live too — a low VIX CALMS the factor, where before
the coupling it changed nothing at all. VIX 5, 10 and 15 produce identical
prices only for the first day (the first close is where a pin first enters
the variance target); from the second day they diverge. The response to a
held pin saturates: the factor's variance is clamped at 8x its baseline,
so above VIX ~42 a harder pin buys almost no additional factor variance —
quadratic inside the plausible band, flat beyond it.

Mean quoted spread across ``Universe.random(25, seed=11)`` after five days,
sim seed 3:

    VIX  5    11.17 bps
    VIX 15    11.52 bps
    VIX 25    13.92 bps
    VIX 45    18.87 bps
    VIX 65    25.89 bps

(The multiplier still floors at 1.0 below VIX 15; the small 5-vs-15 gap is
the variance channel moving prices, not the spread rule.)

The correlation channel is no longer smaller than the name suggests. Mean
pairwise correlation of daily log returns, the same 25 names over 120 days,
300 pairs: +0.269 at VIX 15, +0.678 at VIX 45, +0.759 at VIX 65. A
high-variance factor regime IS a high-correlation regime, and at crisis
VIX diversification genuinely stops working — which is what real crises
do, and what this model could not produce before the coupling.

So a VIX path now answers both stress questions: what an execution
algorithm does when spreads widen, and what a strategy does when
volatility triples and every name starts moving together. What it still
does not do is move any single name's IDIOSYNCRATIC variance — sizing a
pin to a target per-name volatility goes through the factor's share, not
one-for-one.

## The macro counterfactual is exact on the market stream — and says so

This is the counterfactual real markets cannot offer: you cannot re-run a
year without its hiking cycle, because your only observation is the one that
happened. Here both are runnable.

Before the RNG stream split (2026-08) this was a weaker guarantee than the
ORDER-FLOW counterfactual in :mod:`pretium.tca`, and this docstring said so:
a macro path changes prices, prices changed which settlement branch drew
four uniforms, and the shared draw schedule could shift — measured once at
-4 draws in 425,600 on an older build. The split closed that mechanism. The
market stream's schedule is now a pure function of (market status, active
roster, sector count), so two runs under different macro paths consume —
and therefore see — identical market noise, draw for draw. The economy
stream MAY branch under a different macro path (a chain in contraction
draws a shock the expansion never rolls), which is exactly why it is
counted separately instead of polluting the market comparison.

Re-measured on this build, twenty instruments over forty days, 2,074,800
market draws in the flat run:

    rate_shock 2.5% -> 5%       market draw delta 0
    rate_shock 2.5% -> 10%      market draw delta 0
    vix_shock  15 -> 45         market draw delta 0
    vix_shock  15 -> 80         market draw delta 0

Zero in all four, zero again when three of them are repeated across seeds 1
to 8, and in these 28 comparisons the economy stream happened not to branch
either.

So :func:`compare` reports ``draw_delta`` from the MARKET stream. Zero means
the two worlds saw an identical market noise sequence and the difference is
purely the scenario. A non-zero delta is no longer a small approximation to
tolerate — it means the scenario changed the market's own draw schedule (a
halt, a delisting, a roster change), and the result compares two
structurally different markets. That is worth surfacing, not averaging
away.

## Two pins on one field compose as consecutive segments

Until 2026-08 a second pin on a field simply overwrote the first in a dict,
and because every driver is a total function of the day the survivor
back-filled the whole run. ``step(vix, before=15, after=48, at=60)`` followed
by ``ramp(vix, start=48, end=22, over=45, begin=75)`` opened at VIX 48 on day
ZERO — a market in crisis for the entire run, no warning — and reversing the
two calls produced a crisis that never subsided. No ordering worked, so it was
not an ordering convention anybody could have documented their way out of.

Pins on one field now layer. Each owns ``[its start day, the next pin's start
day)``, the last owns the rest of the run, and the first also owns everything
before its own start (so a lone ``ramp`` still holds its ``start`` from day
zero, exactly as before). A field with one pin behaves identically to the old
surface; nothing that worked has changed.

Start days must therefore be STRICTLY INCREASING within a field, and anything
else is refused by name. Two pins claiming the same day mean one of them
states a value that can never be reached, and a pin declared before an earlier
one would have to back-fill — which is the defect, not a feature. So the
step-then-decay path is written in the order it happens::

    Scenario().hold(vix=15.0).ramp("vix", start=48.0, end=22.0, over=45,
                                   begin=60)

— calm until day 60, a jump to 48 on day 60 because a ramp starts AT its start
value, then the decay. ``hold`` before ``ramp`` is the general idiom for
"a level, then an episode".

## The ready-made shapes are constructors, and say so

:meth:`Scenario.rate_shock`, :meth:`Scenario.vix_shock`, :meth:`vol_shock` and
:meth:`from_json` each build a WHOLE scenario. They read as chainable, and
before 2026-08 ``Scenario().ramp("federal_funds_rate", ...).vix_shock(...)``
silently threw the ramp away and returned a scenario driving only ``vix``.
Python cannot stop a caller writing that, so the library does: calling one of
them on an instance raises, names the fields that would have been discarded,
and gives the composing form.
"""

from __future__ import annotations

import json
import warnings
from typing import Any, Callable, Sequence

from ._core import Engine, Instrument, Macro, ModelParams, ValidationError

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

#: The business-cycle phases the engine accepts. Duplicated from
#: ``_core.CycleName`` so a misspelt phase is caught where it is WRITTEN
#: rather than on the first ``apply``. The engine catches it either way, but
#: a scenario built in one place and run in another reports the mistake at
#: the run, which is the wrong end for a caller reading a traceback.
CYCLES = ("expansion", "peak", "contraction", "trough", "recovery")


def _check(field: str, value: Any) -> None:
    if field not in FIELDS:
        raise ValidationError(
            f"unknown macro field {field!r}. Valid: {', '.join(FIELDS)}"
        )
    if field == "cycle":
        if isinstance(value, str) and value not in CYCLES:
            raise ValidationError(
                f"unknown cycle {value!r}. Valid: {', '.join(CYCLES)}. "
                "A misspelt phase is the same failure a misspelt FIELD would "
                "be, so it is refused where it is written rather than on the "
                "first day of the run."
            )
        return
    if field in RATE_FIELDS and isinstance(value, (int, float)):
        if not RATE_MIN <= value <= RATE_MAX:
            raise ValidationError(
                f"{field} = {value} is outside the plausible range "
                f"[{RATE_MIN}, {RATE_MAX}]. Rates are FRACTIONS here: 5.2% is "
                "0.052, not 5.2."
            )


class _Pin:
    """One declared segment of one field's path.

    ``begin`` is the day the pin takes over the field. ``describe`` is how it
    appears in a conflict message, and it carries the caller's own arguments
    because "two pins on 'vix'" is not an error anybody can act on, while
    "step('vix', before=15.0, after=48.0) at day 60 and ramp('vix', ...) at
    day 60" names the exact two calls to change.
    """

    __slots__ = ("begin", "driver", "describe")

    def __init__(self, begin: int, driver: Callable[[int], Any],
                 describe: str) -> None:
        self.begin = begin
        self.driver = driver
        self.describe = describe


class _constructor:
    """A ``classmethod`` that refuses to be called on an instance.

    ``Scenario.vix_shock(...)`` builds a scenario. ``some_scenario.vix_shock(
    ...)`` is the same call with a receiver Python silently ignores, so it
    returned a NEW scenario and everything configured on the receiver
    vanished with no error and no symptom until somebody read ``.fields``.

    A plain ``classmethod`` cannot tell the two apart — it is handed ``cls``
    either way. A descriptor can: ``__get__`` sees the instance. So the
    instance form is the one place this failure can be caught, and it is
    caught here rather than documented, because the documented version was
    already true and people still wrote it.

    ``advice`` is the composing form, given per constructor. A refusal that
    only said "this is a classmethod" would leave the caller to work out
    what ``rate_shock`` is actually made of.
    """

    def __init__(self, func: Callable[..., Any], advice: str) -> None:
        self._func = func
        self._advice = advice
        self._name = func.__name__
        self.__doc__ = func.__doc__

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name

    def __get__(self, obj: Any, objtype: type | None = None) -> Callable[..., Any]:
        cls = objtype if objtype is not None else type(obj)
        if obj is None:
            def bound(*args: Any, **kwargs: Any) -> Any:
                return self._func(cls, *args, **kwargs)
            bound.__name__ = self._name
            bound.__doc__ = self.__doc__
            return bound

        name, advice = self._name, self._advice
        driving = getattr(obj, "fields", ())

        def refuse(*args: Any, **kwargs: Any) -> Any:
            held = (f"drives {', '.join(driving)}" if driving
                    else "drives nothing yet")
            raise ValidationError(
                f"Scenario.{name} is a CONSTRUCTOR, not a step: it builds a "
                f"whole scenario. Called on an instance it looks chainable "
                f"and is not -- the receiver ({held}) would be discarded and "
                f"you would get back a scenario driving only "
                f"{name}'s own fields, silently. "
                f"Build it on the class instead: Scenario.{name}(...). "
                f"To ADD this shape to a scenario you already have, {advice}"
            )

        refuse.__name__ = name
        return refuse


def _describe_call(name: str, *args: Any, **kwargs: Any) -> str:
    """The call as the caller wrote it, for a conflict message.

    Reconstructed rather than captured because the message has to be
    actionable: the caller has to be able to find these two lines in their
    own source, and a message naming only the field cannot help them.
    """
    parts = [repr(a) for a in args]
    parts += [f"{k}={v!r}" for k, v in kwargs.items()]
    return f"{name}({', '.join(parts)})"


class Scenario:
    """A macro path, built from segments and read a day at a time."""

    __slots__ = ("_drivers", "_label")

    def __init__(self, label: str = "") -> None:
        self._drivers: dict[str, list[_Pin]] = {}
        self._label = label

    # -- per-field composition --------------------------------------------

    def _pin(self, field: str, pin: _Pin) -> None:
        """Layer one pin onto ``field``, or refuse to.

        Pins on a field own consecutive day ranges, so their start days must
        strictly increase. Both other orderings are the silent-wrong-answer
        defect this method exists to close, and they fail differently enough
        to be worth separate messages:

        - a LATER pin declared with an EARLIER start day would have to
          back-fill days the first pin already owns, which is exactly how a
          decay ramp used to put day zero in crisis;
        - two pins on the SAME start day mean the earlier one's value from
          that day on is unreachable, so one of the two calls is dead code
          the caller believes is running.
        """
        self._refuse_conflict(field, pin)
        self._drivers.setdefault(field, []).append(pin)

    def _refuse_conflict(self, field: str, pin: _Pin) -> None:
        """Raise if ``pin`` cannot layer onto ``field``. Never mutates.

        Split from :meth:`_pin` so ``hold`` can validate every keyword before
        applying any of them: a scenario left half-pinned by a raised error
        would be a quieter version of the same defect.
        """
        pins = self._drivers.get(field)
        if not pins:
            return

        last = pins[-1]
        if pin.begin > last.begin:
            return

        if pin.begin == last.begin:
            # Two shapes, and the fix differs. Both starting from day zero is
            # one pin too many and the caller has to drop one. Both starting
            # at day N is usually a level plus an episode written as two
            # overlapping whole-run paths, and the composing form is a `hold`
            # for the level and the episode beginning on its own day.
            fix = (
                f"Keep one of them: two pins from day 0 is one whole path "
                f"stated twice."
                if pin.begin == 0 else
                f"Say the level and the episode separately: "
                f".hold({field}=<the level before day {pin.begin}>) then "
                f".ramp({field!r}, start=..., end=..., over=..., "
                f"begin={pin.begin}) -- a ramp starts AT its start value, so "
                f"that is a jump on day {pin.begin} and then the path."
            )
            raise ValidationError(
                f"two pins on {field!r} both begin on day {pin.begin}: "
                f"{last.describe} and {pin.describe}. Pins on one field "
                f"layer as consecutive segments, so the first one's values "
                f"from day {pin.begin} onward could never be reached -- one "
                f"of these two calls would do nothing, silently. {fix}"
            )

        raise ValidationError(
            f"pins on {field!r} are out of order: {pin.describe} was declared "
            f"after {last.describe}, but begins {last.begin - pin.begin} "
            f"day(s) earlier. Pins on one field layer as consecutive "
            f"segments, each owning from its own start day until the next "
            f"one begins, so they must be declared in the order they happen. "
            f"Swap the two calls -- the day-{pin.begin} pin first, then the "
            f"day-{last.begin} one. (Declared this way round the later pin "
            f"would have to back-fill days the earlier one already owns, "
            f"which is a whole run under the wrong path and no error.)"
        )

    def _value(self, field: str, day: int) -> Any:
        pins = self._drivers[field]
        # The last pin that has started, or -- before any of them has -- the
        # first, which holds its own pre-start value. That keeps a lone ramp
        # or step defined on every day of any run length, which is the rule
        # `ramp` has always documented, and makes a one-pin field behave
        # exactly as it did before pins could layer.
        chosen = pins[0]
        for pin in pins:
            if pin.begin > day:
                break
            chosen = pin
        return chosen.driver(day)

    # -- construction -----------------------------------------------------

    def hold(self, **fields: Any) -> "Scenario":
        """Pin fields to a constant from day zero.

        As the FIRST pin on a field this is the whole path. Before a later
        pin it is the level the run starts from, which is the idiom for
        "calm, then an episode": ``.hold(vix=15.0).ramp("vix", start=48.0,
        end=22.0, over=45, begin=60)``.
        """
        # Every field is validated before any is committed, so a conflict on
        # the second keyword does not leave the first one applied. A
        # half-mutated scenario after a raised error is the same class of
        # quiet wrongness as the conflict itself.
        pins = []
        for field, value in fields.items():
            _check(field, value)
            pins.append((field, _Pin(
                0,
                (lambda v: (lambda _day: v))(value),
                _describe_call("hold", **{field: value}) + " from day 0",
            )))
        for field, pin in pins:
            self._refuse_conflict(field, pin)
        for field, pin in pins:
            self._pin(field, pin)
        return self

    def ramp(self, field: str, *, start: float, end: float, over: int,
             begin: int = 0) -> "Scenario":
        """Move ``field`` linearly from ``start`` to ``end`` over ``over`` days.

        Held at ``start`` before ``begin`` and at ``end`` after, so the path is
        defined on every day of any run length rather than only inside the
        ramp. A path with holes would make the run length change the scenario.

        As a LATER pin on a field the pre-``begin`` hold never applies —
        whatever pinned the field before keeps its days — so ``start`` is
        simply the value the field jumps to on day ``begin``. That is what
        makes ``hold`` then ``ramp`` a step followed by a decay.
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

        self._pin(field, _Pin(
            begin, driver,
            _describe_call("ramp", field, start=start, end=end, over=over)
            + f" from day {begin}",
        ))
        return self

    def step(self, field: str, *, before: Any, after: Any, at: int) -> "Scenario":
        """Jump ``field`` from ``before`` to ``after`` on day ``at``.

        A discontinuity, which a ramp is not. Use this for something that
        genuinely happens at once — a surprise cut, a regime change — and a
        ramp for something the market prices in gradually.

        As a LATER pin on a field, ``before`` never applies: the pin that
        already owns the days up to ``at`` keeps them. Pin the field from day
        zero with ``hold`` if you want to state that level, and the step is
        then only the jump.
        """
        _check(field, before)
        _check(field, after)
        if at < 0:
            raise ValidationError("at cannot be negative")
        def driver(day: int, before=before, after=after, at=at) -> Any:
            return before if day < at else after

        self._pin(field, _Pin(
            at, driver,
            _describe_call("step", field, before=before, after=after)
            + f" at day {at}",
        ))
        return self

    # -- reading ----------------------------------------------------------

    def at(self, day: int) -> dict[str, Any]:
        """The pins for one day.

        Where a field carries several pins, the one in force is the last one
        whose start day has arrived — or, before any has, the first, which
        holds its own pre-start value.
        """
        return {field: self._value(field, day) for field in self._drivers}

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

        ``days`` must be at least one. A zero-day document carries no path,
        and :meth:`from_json` reading it back produced a scenario driving
        NOTHING — a round trip that quietly discarded every field, and a
        reproduced run that applied no pins at all.
        """
        if days < 1:
            raise ValidationError(
                f"days must be at least 1 to serialise a scenario, got {days}. "
                f"The serialised form is the realised PATH, so a zero-day "
                f"document records no path -- reading it back would give a "
                f"scenario driving nothing at all, in place of one driving "
                f"{', '.join(self.fields) or 'nothing'}. Serialise the "
                f"horizon the run actually uses."
            )
        return json.dumps(
            {"schema": 1, "label": self._label, "days": days,
             "path": self.table(days)},
            sort_keys=True, separators=(",", ":"),
        )

    def from_json(cls, text: str) -> "Scenario":
        """Rebuild a scenario from :meth:`to_json` output.

        What comes back is the REALISED PATH as an object: a scenario whose
        every day returns exactly the recorded values, whatever constructor
        originally built them. That is the honest direction of the round trip
        — the serialised form is the path, not the recipe, so the restored
        object is the path too. Beyond the recorded horizon it holds its final
        values, the same rule :meth:`ramp` applies after its end, so a longer
        run is defined rather than an IndexError.

        A newer schema is refused rather than read on a best-effort basis, and
        so is an inconsistent document — a day count that disagrees with the
        path, days out of order, or fields that appear and disappear between
        rows. Each of those describes a scenario nobody constructed, and a
        loader that guessed its way past them would replay a run under pins
        the original never applied.
        """
        payload = json.loads(text)
        if not isinstance(payload, dict) or "path" not in payload:
            raise ValidationError("not a pretium scenario document")
        schema = payload.get("schema", 0)
        if schema > 1:
            raise ValidationError(
                f"scenario schema {schema} is newer than this version "
                "understands. Upgrade pretium rather than reading it partially."
            )
        path = payload["path"]
        days = payload.get("days")
        if not isinstance(path, list) or days != len(path):
            raise ValidationError(
                f"scenario document says {days} days but carries "
                f"{len(path) if isinstance(path, list) else 'no'} path rows. "
                "An inconsistent path is a scenario nobody constructed."
            )
        if not path:
            # An empty path used to load: the field loop never ran, and what
            # came back drove nothing. A run reproduced from it would apply
            # no pins and look like a scenario run.
            raise ValidationError(
                "scenario document carries an empty path, so it names no "
                "fields and no days. Loading it would give a scenario that "
                "drives nothing -- which runs, and produces a market under "
                "no macro path at all, with nothing to distinguish it from "
                "one that was pinned. Serialise at least one day."
            )

        fields: tuple[str, ...] | None = None
        values: dict[str, list[Any]] = {}
        for index, row in enumerate(path):
            if not isinstance(row, dict) or row.get("day") != index:
                raise ValidationError(
                    f"path row {index} is not day {index}. Days must be "
                    "contiguous from zero: a reordered or truncated path is a "
                    "different scenario."
                )
            row_fields = tuple(sorted(k for k in row if k != "day"))
            if fields is None:
                fields = row_fields
                values = {field: [] for field in fields}
            elif row_fields != fields:
                raise ValidationError(
                    f"path row {index} drives {list(row_fields)} where earlier "
                    f"rows drive {list(fields)}. A scenario's fields are fixed "
                    "at construction, so this document was not written by one."
                )
            for field in fields:
                _check(field, row[field])
                values[field].append(row[field])

        scenario = cls(label=payload.get("label", ""))
        last = len(path) - 1
        for field, series in values.items():
            scenario._pin(field, _Pin(
                0,
                (lambda series=series, last=last:
                    lambda day: series[day if day < last else last])(),
                f"from_json path for {field!r} from day 0",
            ))
        return scenario

    from_json = _constructor(from_json, (
        "there is nothing to add: from_json rebuilds a complete recorded "
        "path. Load it on the class and use the result, or take the fields "
        "you want out of Scenario.from_json(text).at(day) and pin them."
    ))

    def __bool__(self) -> bool:
        return bool(self._drivers)

    def __repr__(self) -> str:
        label = f"{self._label!r}, " if self._label else ""
        return f"Scenario({label}driving {', '.join(self.fields) or 'nothing'})"

    # -- ready-made shapes ------------------------------------------------

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

    rate_shock = _constructor(rate_shock, (
        "write the two ramps it is made of: "
        ".ramp('federal_funds_rate', start=..., end=..., over=..., begin=...) "
        "and .ramp('corporate_bond_yield', start=...+credit_spread, "
        "end=...+credit_spread, over=..., begin=...). That is the whole "
        "constructor -- it moves the policy rate and the corporate yield "
        "together, held apart by the spread."
    ))

    def vix_shock(cls, *, calm: float = 15.0, peak: float = 45.0,
                  at: int = 10, over: int = 20) -> "Scenario":
        """A VIX spike that decays back: a volatility, correlation and
        spread stress in one, which is what a real one is.

        Up as a step, down as a ramp, because that is the shape a stress
        episode has: it arrives at once and subsides slowly.

        **This raises realised volatility** — since the 2026-08 coupling of
        the market factor's variance target to VIX, and not before, which
        is why this docstring once said the opposite and was right then.
        Measured on ``Universe.random(20, seed=11)`` over 120 days, sim
        seed 3: the default spike to 45 moves annualised realised
        volatility from a no-scenario 58.17% to 67.01%, a peak of 80 to
        74.57%, and volatility clustering RISES with it (|r| acf(1) 0.334
        to 0.357 and 0.378) — where the pre-coupling model measurably
        moved clustering the wrong way. The spike also widens the quoted
        bid-ask and, above VIX 25.5, pulls returns toward the market
        factor. This module's docstring sets out the four channels and
        what each one is worth.

        A held crisis level is a harder stress than a decaying spike:
        ``Scenario().hold(vix=65.0)`` reads 124% annualised on the same
        method, against this constructor's 67%. Use the hold form to ask
        what a strategy survives when the stress does not subside.
        """
        def driver(day: int) -> float:
            if day < at:
                return calm
            if day >= at + over:
                return calm
            return peak + (calm - peak) * (day - at) / over

        scenario = cls(label=f"vix_shock {calm}->{peak} at day {at}")
        scenario._pin("vix", _Pin(
            0, driver,
            _describe_call("vix_shock", calm=calm, peak=peak, at=at,
                           over=over) + " from day 0",
        ))
        return scenario

    vix_shock = _constructor(vix_shock, (
        "write the shape it is made of: .hold(vix=calm) for the quiet days, "
        "then .ramp('vix', start=peak, end=calm, over=over, begin=at) -- a "
        "jump to the peak on day `at` because a ramp starts AT its start "
        "value, then the decay back to calm."
    ))

    def vol_shock(cls, *, calm: float = 15.0, peak: float = 45.0,
                  at: int = 10, over: int = 20) -> "Scenario":
        """Deprecated alias for :meth:`vix_shock`. Same path, same results.

        History with a twist: the constructor was renamed when it was
        measured that VIX did not drive realised volatility, so "vol_shock"
        was a name making a false claim. The 2026-08 coupling then wired
        VIX into the market factor's variance target, which made the OLD
        name accurate again — but the rename stands. :meth:`vix_shock`
        names the lever (the path it drives is a VIX path), which stays
        true under any future model change, where a name promising an
        effect has already been wrong once. The path is unchanged, so a
        run under this name reproduces exactly; only the ``label``
        differs, because the serialised path carries the honest name.
        """
        warnings.warn(
            "Scenario.vol_shock is deprecated; use Scenario.vix_shock. Both "
            "drive the same VIX path -- which, since the 2026-08 coupling, "
            "does raise realised volatility -- so results do not change; "
            "only the honest name survives.",
            DeprecationWarning,
            stacklevel=2,
        )
        return cls.vix_shock(calm=calm, peak=peak, at=at, over=over)

    vol_shock = _constructor(vol_shock, (
        "use the vix_shock advice -- .hold(vix=calm) then .ramp('vix', "
        "start=peak, end=calm, over=over, begin=at) -- and note that this "
        "name is deprecated in favour of Scenario.vix_shock."
    ))


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
    model: str | ModelParams | None = None,
) -> Engine:
    """Run a market under a macro path. Returns the finished engine.

    The scenario is applied at the START of each day, before that day's
    session, so day zero is already under the path rather than under whatever
    the engine was constructed with.

    ``model`` selects the coefficient set — a preset name or a
    :class:`pretium.ModelParams`, defaulting to the shipped preset. The
    returned engine reports it as ``model_fingerprint``, like any other.
    """
    if days < 1:
        raise ValidationError("days must be at least 1")
    hour, minute, day_of_week = start
    engine = Engine(seed=seed, universe=universe, macro_state=macro,
                    model=model)
    for day in range(days):
        scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(hour, minute, day_of_week, ticks_per_day)
        # Record before the close: the close advances the macro chain into
        # the next day, and the recorded macro row must carry the values the
        # day traded under -- for a pinned series, the pin itself.
        if record:
            engine.record(day)
        engine.close_market()
    return engine


def _path_summary(scenario: Scenario, days: int) -> str:
    """The day-zero pins, for an error message that names the actual values."""
    pins = scenario.at(0)
    if not pins:
        return "it drives no fields at all"
    return ", ".join(f"{field}={value!r}" for field, value in sorted(pins.items()))


def _refuse_self_comparison(scenario: Scenario, days: int) -> None:
    """Refuse a default-baseline comparison the scenario cannot lose.

    The default baseline is ``hold(**scenario.at(0))``. When the scenario is
    CONSTANT across the horizon that is not a counterfactual, it is the same
    world twice, and the difference is exactly zero on every instrument by
    construction rather than by measurement.

    Three shapes reach here and they are worth telling apart, because the
    reader's next action differs:

    - a ``hold``-only scenario, or a ``step`` at day zero: there is no path
      to isolate, only a level, and the caller wants a baseline at a
      DIFFERENT level;
    - a shock whose start day falls at or after ``days``: the path is real
      but the run ends before it begins, so the caller wants a longer run;
    - a scenario driving nothing.

    The zero this replaces is the worst answer the library can give. It is
    wrong, it is confident, it is quoted to three decimal places, and there
    is nothing about it a careful reader could catch.
    """
    day_zero = scenario.at(0)
    if any(scenario.at(day) != day_zero for day in range(1, days)):
        return

    unreached = sorted(
        (pin.begin, field, pin.describe)
        for field, pins in scenario._drivers.items()
        for pin in pins
        if pin.begin >= days
    )

    if unreached:
        listed = "; ".join(f"{describe} (field {field!r})"
                           for _, field, describe in unreached)
        raise ValidationError(
            f"compare() would report exactly 0.00% on every instrument: this "
            f"scenario does not move within the {days}-day horizon, because "
            f"{listed} begins on or after day {days}. The run ends before the "
            f"shock starts, so the default baseline -- hold(**scenario.at(0)) "
            f"-- is the scenario itself. Run at least "
            f"{unreached[-1][0] + 1} days, or start the shock earlier."
        )

    raise ValidationError(
        f"compare() would report exactly 0.00% on every instrument: this "
        f"scenario is CONSTANT over all {days} days ({_path_summary(scenario, days)}), "
        f"and the default baseline is hold(**scenario.at(0)) -- the day-zero "
        f"values held flat -- which for a constant path IS the scenario. "
        f"The default baseline isolates a PATH from the level it starts at, "
        f"so it only means anything for a scenario that moves. To measure a "
        f"held level, name the world WITHOUT it: "
        f"compare(scenario, ..., baseline=Scenario().hold(<the calm levels>)). "
        f"To measure a path, give the scenario one."
    )


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

    That default is only meaningful for a scenario that MOVES inside the
    horizon, and a scenario that does not is refused rather than reported.
    For a ``hold``-only scenario — or a ``step`` at day zero, or any shock
    whose start day falls outside ``days`` — the default baseline IS the
    scenario, and the comparison returns exactly 0.00% on every instrument.
    A confident, meaningless zero reads as "the shock did nothing", which is
    the worst answer available: it is wrong, it looks like a finding, and
    nothing about it looks like a mistake. See :meth:`Scenario.hold`.

    Keyword arguments — ``model=`` among them — pass through to
    :func:`run_scenario` and apply to BOTH worlds, because a shocked world
    differenced against a baseline under a different coefficient set would
    measure the model gap dressed up as the scenario's effect. The result
    records ``model_fingerprint``.
    """
    import struct

    if days < 1:
        raise ValidationError("days must be at least 1")

    if baseline is None:
        _refuse_self_comparison(scenario, days)
        baseline = Scenario(label="flat").hold(**scenario.at(0))
    elif scenario.table(days) == baseline.table(days):
        # An explicit baseline that realises the same path is the same
        # defect arriving by a different route: two runs of one world,
        # differenced, reported as a clean zero.
        raise ValidationError(
            f"compare() would difference two identical worlds: the scenario "
            f"and the baseline realise the same path on all {days} days "
            f"({_path_summary(scenario, days)}). Every instrument would come "
            f"back at exactly 0.00%, which reads as 'the shock did nothing' "
            f"rather than as 'no shock was applied'. Give the baseline the "
            f"levels the shocked world does NOT have."
        )

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

    # A zero baseline price makes that name's percentage move undefined, and
    # a NaN in this list is not merely one bad row: `sorted` with a NaN is
    # ordered arbitrarily and `min`/`max` return whichever value the
    # comparison happened to start from, so median_pct, worst_pct and
    # best_pct would ALL be quietly meaningless -- reported to two decimal
    # places like any other result. Name the instrument instead.
    undefined = [t for t, m in zip(shocked.tickers, moves) if m != m]
    if undefined:
        raise ValidationError(
            f"{', '.join(undefined)} priced at zero in the baseline world, so "
            f"the percentage move is undefined for "
            f"{'them' if len(undefined) > 1 else 'it'}. A NaN here would not "
            f"stay in one row: median_pct, worst_pct and best_pct are a sort "
            f"and a min/max over this list, and all three are arbitrary once "
            f"a NaN is in it. Compare on a universe whose names all carry a "
            f"price, or read move_pct per instrument from two run_scenario "
            f"calls."
        )

    ordered = sorted(moves)
    middle = len(ordered) // 2
    median = (ordered[middle] if len(ordered) % 2
              else (ordered[middle - 1] + ordered[middle]) / 2)

    # Reported from the MARKET stream, the stream that decides whether the
    # two worlds saw the same market. Since the 2026-08 stream split the
    # market schedule is a pure function of (market status, active roster,
    # sector count), so a macro path cannot shift it by moving prices. The
    # economy stream MAY branch under a different macro path, and folding it
    # into this delta -- as the pre-split draws_consumed subtraction
    # effectively did -- would under-claim exactness: a run whose market
    # noise was bit-identical would report exact=False because the macro
    # chain took a draw the baseline did not.
    #
    # Zero therefore means the difference is purely the scenario. Non-zero
    # means the scenario changed the market's own draw schedule -- a halt, a
    # delisting, a roster change -- and the result compares two structurally
    # different markets. Measured at zero across 28 scenario comparisons on
    # this build.
    delta = (shocked.draws_by_stream()["market"]
             - flat.draws_by_stream()["market"])

    return {
        "seed": seed,
        "days": days,
        "scenario": repr(scenario),
        # Both worlds ran it, so one value names the model for the whole
        # comparison -- the same provenance rule as Scorecard's.
        "model_fingerprint": shocked.model_fingerprint,
        "draws": shocked.draws_consumed,
        "draw_delta": delta,
        "exact": delta == 0,
        "tickers": list(shocked.tickers),
        "move_pct": moves,
        "median_pct": median,
        "worst_pct": min(moves),
        "best_pct": max(moves),
    }
