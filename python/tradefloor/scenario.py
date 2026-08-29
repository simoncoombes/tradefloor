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
scores = tf.evaluate(agents, seed=7, universe=u, days=60, scenario=shock)
```

## Two mechanisms, and they answer different questions

The above is a PATH: every day of the run, the policy rate is whatever the
ramp says, and the endogenous chain does not get a vote. That is right when
the path is the experiment.

A `Scenario` also carries INTERVENTIONS, which are relative changes scheduled
against the live market:

```python
scenario = Scenario.from_yaml("scenarios/liquidity_crisis.yml")
control, stress = tf.branch(engine, 2)
for day in range(80):
    scenario.apply(stress, day)
    ...
```

or, the same thing in Python:

```python
scenario = (Scenario(name="liquidity_crisis")
            .shock("market.liquidity", operation="multiply", value=0.40,
                   at=50, duration=25)
            .assume("macro.corporate_yield", operation="add", value=0.005,
                    at=50, duration=25))
```

`multiply` cannot be a path, because the value it multiplies is whatever the
chain has reached by day 50. So an intervention reads the field, applies its
operation and writes the result back, and the audit trail records the values
it saw rather than the recipe. The registry of what may be moved, what each
target actually reaches and what it was measured to be worth is
:mod:`tradefloor.interventions`.

`shock` and `assume` do the same thing and are deliberately different words.
Nothing here derives a transmission: a scenario that assumes an oil shock
raises inflation by 1.5 points says so under its own heading, and
:meth:`Scenario.describe` prints it there.

The two compose. Pins are written first each day, then interventions on top,
so a scenario can hold VIX calm for sixty days and then spike it.

## The trap this exists to close

Pinning `federal_funds_rate` on its own does **nothing inside the first
central-bank meeting window**. Measured on this build, on
``Universe.random(20, seed=4)`` at sim seed 5: a 250bp policy-only ramp over
thirty days moved twenty instruments by exactly 0.00% at 40 days.

That is not a defect, it is the valuation model. Equities are discounted off
the **corporate bond yield**, and the policy rate is only a fallback used when
no yield is present. `Some(0.0)` is a real zero yield and must be used, so
inside the engine, where the economy always carries one, the policy rate never
reaches fair value directly.

Since the macro chain runs endogenously (2026-08), transmission exists but is
lagged: the corporate yield is recomputed from the 10Y at central-bank
MEETINGS, the first of which is scheduled 45 days out. Measured at 60 days,
the same policy-only ramp prices the median instrument down 4.19%. So the
trap is now a horizon trap: a short study sees nothing, silently.

The failure mode survives: you run a month-long rate shock, nothing happens,
and you conclude the model does not care about rates. It cares, but at meeting
cadence, through the curve. For an immediate repricing you still have to move
the yield equities actually discount off.

So :meth:`Scenario.rate_shock` moves the whole curve, policy rate and
corporate yield together separated by a credit spread, which is what a rate
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
its reversion target is now proportional to VIX squared, with VIX read as the
factor's implied volatility, anchored so that VIX 15 (the endogenous mean)
reproduces the autonomous process exactly. The coupling was measured before
it was switched on, and this section was rewritten in the same change that
switched it, because the old claims were load-bearing.

What VIX reaches now:

1. **The market factor's variance target**, the volatility channel. Each
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

Measured on this build, with ``Universe.random(20, seed=11)``, 120 days, sim
seed 3 and pins through the scenario API, annualised realised volatility:

    VIX  5     49.48%
    VIX 15     58.76%   (the anchor; bit-identical to the uncoupled model)
    VIX 45    107.07%
    VIX 65    124.31%

A thirteenfold move in VIX now moves realised volatility by a factor of
2.5. Sub-15 pins are live too: a low VIX CALMS the factor, where before
the coupling it changed nothing at all. VIX 5, 10 and 15 produce identical
prices only for the first day (the first close is where a pin first enters
the variance target); from the second day they diverge. The response to a
held pin saturates: the factor's variance is clamped at 8x its baseline,
so above VIX ~42 a harder pin buys almost no additional factor variance,
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
VIX diversification genuinely stops working, which is what real crises
do, and what this model could not produce before the coupling.

So a VIX path now answers both stress questions: what an execution
algorithm does when spreads widen, and what a strategy does when
volatility triples and every name starts moving together. What it still
does not do is move any single name's IDIOSYNCRATIC variance. It sizes a
pin to a target per-name volatility goes through the factor's share, not
one-for-one.

## The macro counterfactual is exact on the market stream, and says so

This is the counterfactual real markets cannot offer: you cannot re-run a
year without its hiking cycle, because your only observation is the one that
happened. Here both are runnable.

Before the RNG stream split (2026-08) this was a weaker guarantee than the
ORDER-FLOW counterfactual in :mod:`tradefloor.tca`, and this docstring said so:
a macro path changes prices, prices changed which settlement branch drew
four uniforms, and the shared draw schedule could shift. An older build measured
-4 draws in 425,600 on an older build. The split closed that mechanism. The
market stream's schedule is now a pure function of (market status, active
roster, sector count), so two runs under different macro paths consume, and
therefore see, identical market noise, draw for draw. The economy
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
tolerate. It means the scenario changed the market's own draw schedule (a
halt, a delisting, a roster change), and the result compares two
structurally different markets. That is worth surfacing, not averaging
away.

## Two pins on one field compose as consecutive segments

Until 2026-08 a second pin on a field simply overwrote the first in a dict,
and because every driver is a total function of the day the survivor
back-filled the whole run. ``step(vix, before=15, after=48, at=60)`` followed
by ``ramp(vix, start=48, end=22, over=45, begin=75)`` opened at VIX 48 on day
ZERO, a market in crisis for the entire run with no warning, and reversing the
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
one would have to back-fill, which is the defect rather than a feature. So the
step-then-decay path is written in the order it happens::

    Scenario().hold(vix=15.0).ramp("vix", start=48.0, end=22.0, over=45,
                                   begin=60)

calm until day 60, a jump to 48 on day 60 because a ramp starts AT its start
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
from . import yaml_subset
from .interventions import (
    SCENARIO_SCHEMA,
    TARGETS,
    Firing,
    Intervention,
    ScenarioValidationError,
    apply_operation,
    sha256_of,
    summarise,
)

#: The macro fields a scenario may drive. Anything else is a typo, and a typo
#: that silently did nothing would be the same class of failure this module
#: exists to close.
#:
#: The last four reach a price only through the macro chain -- see
#: :data:`tradefloor.interventions.TARGETS`, which records for every target
#: what reads it and how long it takes to arrive.
FIELDS = (
    "vix",
    "federal_funds_rate",
    "corporate_bond_yield",
    "inflation_rate",
    "qe_pe_boost",
    "fear_greed_index",
    "gdp_growth",
    "unemployment_rate",
    "tariff_rate",
    "oil_price",
    "cycle",
)

#: Fields the engine validates as fractions in [-0.05, 0.50]. Listed so a
#: scenario can reject 5.0-meaning-5% at construction, where the mistake is
#: visible, rather than sixty days into a run.
RATE_FIELDS = ("federal_funds_rate", "corporate_bond_yield", "inflation_rate",
               "gdp_growth", "unemployment_rate", "tariff_rate")

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
    if field == "oil_price" and isinstance(value, (int, float)):
        if value <= 0:
            raise ValidationError(
                f"oil_price = {value} is not a price. It is denominated in "
                "dollars (the engine opens at 75.0), not as a fraction or a "
                "multiplier, and the daily chain clamps it into [35, 150]."
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

    A plain ``classmethod`` cannot tell the two apart, because it is handed ``cls``
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


def _wrap(text: str, width: int = 72) -> list[str]:
    """Paragraphs at a readable width, for `describe`.

    A description read out of a folded YAML block is one long line, and a
    terminal is not one long line.
    """
    out: list[str] = []
    for paragraph in text.strip().splitlines():
        if not paragraph.strip():
            out.append("")
            continue
        line = ""
        for word in paragraph.split():
            if line and len(line) + 1 + len(word) > width:
                out.append(line)
                line = word
            else:
                line = f"{line} {word}".strip()
        if line:
            out.append(line)
    return out


def _interpolate(start: Any, finish: Any, step: float) -> Any:
    """One point on a ramp, over a scalar or over a whole column."""
    if isinstance(start, tuple):
        return tuple(_interpolate(a, b, step)
                     for a, b in zip(start, finish, strict=True))
    return start + (finish - start) * step


def _rerole(item: Intervention, role: str) -> Intervention:
    """The same intervention filed under the other heading.

    `Scenario(shocks=[...])` should not need every Intervention spelled with
    `role="shock"`, and an intervention built for one list and passed to the
    other is a mistake worth silently fixing rather than refusing: the role
    is a property of where it was declared.
    """
    return Intervention(
        item.target, operation=item.operation, value=item.value, at=item.at,
        duration=item.duration, shape=item.shape, role=role,
    )


class Scenario:
    """A macro path and a set of explicit interventions, applied a day at a time.

    Two mechanisms, one object, and they are for different questions.

    **Pins** -- :meth:`hold`, :meth:`ramp`, :meth:`step` -- state a whole
    PATH for a field: every day of the run, that field is whatever the path
    says, and the endogenous chain does not get a vote. That is the right
    shape for "what does a hiking cycle do", where the cycle is the
    experiment.

    **Interventions** -- :meth:`shock`, :meth:`assume`, or a YAML file --
    state a CHANGE relative to whatever the market had arrived at: multiply
    oil by 1.40 on day 50 and let the chain carry it from there. That is the
    right shape for a shock, and it is the only one that can express a
    relative operation, because the value being multiplied is not knowable
    until the day arrives.

    They compose. Pins are written first on each day, then interventions on
    top, so a scenario can hold VIX calm for sixty days and then spike it.

    A scenario also carries a `name` and a `description`, and its
    interventions are split into `shocks` and `transmission`. That split is a
    claim about evidence rather than about mechanism: the engine treats both
    identically, and the point of separating them is that a report can say
    "this scenario ASSUMES a 1.5pp inflation pass-through" instead of
    implying the simulator derived one. See :meth:`describe`.
    """

    __slots__ = ("_drivers", "_label", "_description", "_shocks",
                 "_transmission", "_source", "_log", "_anchors", "_baselines",
                 "_day")

    def __init__(self, label: str = "", *, name: str | None = None,
                 description: str = "",
                 interventions: Sequence[Intervention] = (),
                 shocks: Sequence[Intervention] = (),
                 transmission: Sequence[Intervention] = ()) -> None:
        if name is not None and label and name != label:
            raise ScenarioValidationError(
                f"a scenario has one identity, and this one was given two: "
                f"label={label!r} and name={name!r}. `name` is the spelling "
                f"the YAML uses and `label` is the one the older Python "
                f"surface uses; they are the same field."
            )
        self._drivers: dict[str, list[_Pin]] = {}
        self._label = name if name is not None else label
        self._description = description
        self._shocks: list[Intervention] = []
        self._transmission: list[Intervention] = []
        self._source: str | None = None
        # The audit trail, and the state a hold or a ramp needs. Both belong
        # to ONE run on ONE engine: see `apply`, which resets them when it
        # sees a different engine or a restarted clock.
        self._log: list[Firing] = []
        self._anchors: dict[int, tuple[Any, Any]] = {}
        # Per TARGET, for the two whose value nothing in the engine puts
        # back. See `_release`.
        self._baselines: dict[str, Any] = {}
        self._day = -1

        for given, role in ((interventions, None), (shocks, "shock"),
                            (transmission, "transmission")):
            for item in given:
                self.intervene(item if role is None or item.role == role
                               else _rerole(item, role))

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

        As a LATER pin on a field the pre-``begin`` hold never applies,
        because whatever pinned the field before keeps its days, so ``start`` is
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
        genuinely happens at once, such as a surprise cut or a regime change, and a
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

    # -- interventions ----------------------------------------------------

    def intervene(self, intervention: Intervention) -> "Scenario":
        """Add one built :class:`tradefloor.Intervention`.

        Declared order is kept, and it is what breaks a tie: two
        interventions on the same target on the same day compose in the order
        they were written, the second reading what the first wrote. Shocks
        run before transmission, always, because that is the order the
        scenario claims they happen in.
        """
        if not isinstance(intervention, Intervention):
            raise ScenarioValidationError(
                f"expected an Intervention, got {type(intervention).__name__}. "
                f"Build one with tf.Intervention(target=..., operation=..., "
                f"value=..., at=...) or write the YAML."
            )
        bucket = (self._shocks if intervention.role == "shock"
                  else self._transmission)
        bucket.append(intervention)
        return self

    def shock(self, target: str, *, operation: str = "multiply",
              value: Any = None, at: int = 0, duration: int | None = None,
              shape: str | None = None) -> "Scenario":
        """An exogenous shock: what this scenario asserts happened."""
        return self.intervene(Intervention(
            target, operation=operation, value=value, at=at,
            duration=duration, shape=shape, role="shock"))

    def assume(self, target: str, *, operation: str = "multiply",
               value: Any = None, at: int = 0, duration: int | None = None,
               shape: str | None = None) -> "Scenario":
        """An assumed transmission: what the AUTHOR thinks the shock did next.

        Identical machinery to :meth:`shock`, and deliberately a different
        word. This library cannot tell you that a 40% oil shock raises
        inflation by 1.5 percentage points. It can run a market in which
        somebody assumed exactly that, and :meth:`describe` prints the
        assumption under its own heading so a reader is never left to guess
        which half was derived.
        """
        return self.intervene(Intervention(
            target, operation=operation, value=value, at=at,
            duration=duration, shape=shape, role="transmission"))

    @property
    def shocks(self) -> tuple[Intervention, ...]:
        return tuple(self._shocks)

    @property
    def transmission(self) -> tuple[Intervention, ...]:
        return tuple(self._transmission)

    @property
    def interventions(self) -> tuple[Intervention, ...]:
        """Every intervention, in the order they fire: shocks, then assumptions."""
        return tuple(self._shocks) + tuple(self._transmission)

    @property
    def name(self) -> str:
        """The scenario's identity: what the YAML spells ``name`` and the
        older Python surface calls ``label``. One field, two spellings."""
        return self._label

    @property
    def description(self) -> str:
        return self._description

    @property
    def source(self) -> str | None:
        """Where this scenario was read from, if it was read from a file.

        Provenance, not reproduction: a manifest replays the RESOLVED
        scenario, so a run stays reproducible after the file is edited,
        renamed or deleted. Only the file's NAME travels in the serialised
        document -- a full path is machine-specific, and a fingerprint over
        one would differ between two people running the same experiment.
        """
        return self._source

    # -- the resolved document --------------------------------------------

    def document(self) -> dict[str, Any]:
        """The canonical resolved scenario: what gets fingerprinted and recorded.

        Every key is present whether or not it was written, and every
        intervention carries its defaults, because a fingerprint over a
        document whose keys come and go is a fingerprint over the author's
        typing habits rather than over the experiment.

        Pins are represented by their DECLARED CALL rather than by their
        realised values, because those depend on how many days you run. One
        consequence worth knowing: a scenario rebuilt by :meth:`from_json`
        declares a recorded path, so it fingerprints differently from the
        constructor that produced it. The realised path and the recipe are
        different statements, and :meth:`to_json` records the first.
        """
        return {
            "schema": SCENARIO_SCHEMA,
            "name": self._label,
            "description": self._description,
            "pins": [
                {"field": field, "from_day": pin.begin,
                 "declared": pin.describe}
                for field in sorted(self._drivers)
                for pin in self._drivers[field]
            ],
            "shocks": [item.as_dict() for item in self._shocks],
            "transmission": [item.as_dict() for item in self._transmission],
        }

    @property
    def fingerprint(self) -> str:
        """sha256 over the resolved document. Cite this.

        Two scenarios fingerprint the same when they are the same experiment,
        so a scenario written in YAML fingerprints the same as the identical
        one written in Python: both resolve to the same :meth:`document`, and
        the hash is over that rather than over the file's bytes.
        Reformatting the YAML, reordering its keys or changing its comments
        does not move the fingerprint. Reordering the interventions does,
        because order is what breaks a same-day tie.
        """
        return sha256_of(self.document())

    def describe(self) -> str:
        """The scenario as a reader needs to see it, shocks above assumptions."""
        out = [f"SCENARIO  {self._label or '(unnamed)'}"]
        if self._description:
            out.append("")
            out.extend(_wrap(self._description))
        out.append("")
        out.append(f"Schema      v{SCENARIO_SCHEMA}")
        out.append(f"Fingerprint {self.fingerprint}")
        if self._source:
            out.append(f"Source      {self._source}")

        if self._drivers:
            out.append("")
            out.append("Macro path")
            out.append("-" * 58)
            for field in sorted(self._drivers):
                for pin in self._drivers[field]:
                    out.append(f"  {field:<24} {pin.describe}")

        for items, heading in (
            (self._shocks, "Exogenous shocks"),
            (self._transmission, "Assumed transmission"),
        ):
            if not items:
                continue
            out.append("")
            out.append(heading)
            out.append("-" * 58)
            out.extend("  " + item.describe() for item in items)

        if self._transmission:
            out.append("")
            out.extend(_wrap(
                "The transmission entries are ASSUMPTIONS made by whoever "
                "wrote this scenario. Nothing here derives them, and nothing "
                "here is a forecast of how a real market would respond."
            ))
        return "\n".join(out)

    def without_interventions(self) -> "Scenario":
        """The same macro path with every intervention removed.

        The counterfactual :func:`compare` wants: one world where the shock
        happened and one where everything else was identical and it did not.
        """
        twin = Scenario(f"{self._label} (no interventions)" if self._label
                        else "")
        twin._drivers = {field: list(pins)
                         for field, pins in self._drivers.items()}
        twin._description = self._description
        return twin

    def copy(self) -> "Scenario":
        """An independent scenario with the same declaration.

        Needed to drive two runs AT ONCE from one recipe. A scenario carries
        the audit trail and the hold anchors of the run it is applied to, and
        a run is identified by its clock, so two interleaved runs would share
        one. Sequential runs need no copy: the clock restarting is what
        starts a new trail.
        """
        twin = Scenario(self._label, description=self._description)
        twin._drivers = {field: list(pins)
                         for field, pins in self._drivers.items()}
        twin._shocks = list(self._shocks)
        twin._transmission = list(self._transmission)
        twin._source = self._source
        return twin

    # -- the audit trail ---------------------------------------------------

    @property
    def log(self) -> tuple[Firing, ...]:
        """Every intervention that actually fired, with the values it saw.

        The recipe says x1.40. What a reader needs afterwards is that oil
        went from 82.14 to 115.00 on day 50, which depends on where the
        endogenous chain had arrived. Each :class:`Firing` carries an
        ``as_dict()`` for a machine and a ``str()`` for a person.

        This is the LAST run's trail. A scenario applied to a different
        engine, or to the same one from day zero again, starts a new one --
        see :meth:`apply`.
        """
        return tuple(self._log)

    def firing_table(self) -> list[dict[str, Any]]:
        """The audit trail as plain dicts, for a manifest or a dataframe."""
        return [entry.as_dict() for entry in self._log]

    # -- reading ----------------------------------------------------------

    def at(self, day: int) -> dict[str, Any]:
        """The pins for one day.

        Where a field carries several pins, the one in force is the last one
        whose start day has arrived, or before any has the first, which
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

    def apply(self, engine: Engine, day: int) -> list[Firing]:
        """Drive one day of one run: pins first, then interventions.

        Returns the interventions that actually fired, which is also what
        lands in :attr:`log`.

        # What `day` means, and why it is relative

        `day` is whatever the run loop counts, and every loop in this library
        starts at zero: :func:`run_scenario`, :func:`tradefloor.evaluate`, and
        a loop you write yourself. So `at: 50` is fifty days after the
        scenario starts being applied -- simulation day 50 for a fresh run,
        and fifty days after the FORK for a branch that resumes from a
        checkpoint.

        That is the only reading under which one file means one experiment on
        both sides of a checkpoint, which is the point of forking. The
        alternative -- absolute simulation days -- would make the same YAML
        fire on day 50 of the parent's history, which for a branch taken at
        day 60 is a day that has already happened. Schema 1 therefore has one
        interpretation, `at: {relative: N}` spells it out, and `absolute` is
        refused by name rather than silently treated as relative.

        # Order within a day

        Pins, then any window that ENDED yesterday is released, then shocks
        in declared order, then transmission in declared order. Two
        interventions on one target on one day compose: the second reads what
        the first wrote. Releases go first so that a window closing on the
        same day another opens does not undo the one that opens. Nothing here
        consults a set, a dict ordering or the clock, so two runs of the same
        scenario fire in the same sequence.

        # One scenario, one run, identified by its clock

        A `hold` and a `ramp` both need the value the field had on their
        first day, so the scenario carries that anchor -- and the audit trail
        -- for the run it is being applied to. A run is identified by its
        CLOCK, not by the engine object: `day` going back to or below the
        last day it saw starts a new run and clears both.

        The engine deliberately does not identify the run, because a
        checkpoint resume is a NEW engine continuing the SAME run. Days 0-59
        on the original and 60-119 on the engine `Checkpoint.resume()`
        returns is one experiment, and a hold that began on day 50 has to
        survive the join. Keying the anchor on the engine object would break
        exactly that, and a checkpoint that could not carry a pending
        intervention would make forking useless for the scenarios it is most
        wanted for.

        The cost of that choice, stated plainly: one scenario object driving
        two runs AT ONCE shares one clock between them. Alternating
        `apply(a, 0)`, `apply(b, 0)`, `apply(a, 1)` is not a pattern this
        object supports -- give each run its own :meth:`copy`. A run that
        joins a hold part-way through, which is the mistake this actually
        catches, is refused by name rather than anchored to whatever it
        happens to find.
        """
        if day <= self._day:
            self._log = []
            self._anchors = {}
            self._baselines = {}
        self._day = day

        pins = self.at(day)
        if pins:
            engine.pin_macro(**pins)

        fired: list[Firing] = []
        # Releases first, so that a window ending on the same day another
        # begins does not undo the one that begins.
        for item in self.interventions:
            released = self._release(item, engine, day)
            if released is not None:
                fired.append(released)
        for index, item in enumerate(self.interventions):
            if not item.active_on(day):
                continue
            fired.append(self._fire(index, item, engine, day))
        self._log.extend(fired)
        return fired

    def _release(self, item: Intervention, engine: Engine,
                 day: int) -> Firing | None:
        """Put a field back when the last window on it ends.

        A `hold` says the shock lasts twenty-five days. For a macro field
        that is true without any help: the daily chain recomputes it, or
        mean-reverts it, or the central bank does, so releasing means simply
        not writing any more.

        Two targets have no such dynamics. Nothing in the engine writes
        `avg_volume` -- the shipped close policy is `Hold` -- and nothing
        moves `tariff_rate` at all. On those, "not writing any more" left the
        shock in place for the rest of the run: quoted depth thinned to 25%
        on day 2 was still at 25% on day 14, under a scenario whose own
        description said the window ended on day 7. The measurement was
        right, the window was fiction, and nothing raised.

        # Why this is per TARGET and not per intervention

        Because two holds on one field overlap, and restoring what each one
        personally found is wrong for the second. Holds on days 1-4 and 3-6:
        the first correctly puts back full depth on day 5, and the second
        then puts back HALF depth on day 7, because half is what it found on
        day 3 while the first was running. The run ends at half depth with
        nothing raised -- the same defect this method exists to fix, one
        level further in.

        So the level to restore is the one the field had before ANY hold on
        it began (:attr:`_baselines`, set once), and it goes back when the
        LAST of them ends. A `permanent` on the same target re-asserts itself
        afterwards, because releases run before the day's firings.
        """
        if item.shape not in ("hold", "ramp") or item.last_day is None:
            return None
        if day != item.last_day + 1:
            return None
        target = TARGETS[item.target]
        if target.restores:
            return None
        if any(other.target == item.target
               and other.shape in ("hold", "ramp")
               and other.active_on(day)
               for other in self.interventions):
            return None
        baseline = self._baselines.pop(item.target, None)
        if baseline is None:
            return None
        previous = target.read(engine)
        target.write(engine, baseline)
        return Firing(
            day=day, scenario=self._label, role=item.role, target=item.target,
            operation="release", value=item.value, shape=item.shape,
            previous=summarise(previous), new=summarise(baseline),
        )

    def _fire(self, index: int, item: Intervention, engine: Engine,
              day: int) -> Firing:
        """Read the live value, apply the operation, write it back.

        The anchor is what stops a `hold` compounding. `multiply` by 2.0 held
        for twenty-five days does not mean 2^25: it means the value the field
        had on the first day, doubled, and then that same number written
        every day of the window. So the target is computed ONCE, on day
        ``at``, from whatever the endogenous chain had reached, and the
        window replays it.
        """
        target = TARGETS[item.target]
        previous = target.read(engine)

        if item.shape == "impulse":
            new = apply_operation(item.operation, previous, item.value)
        else:
            if day == item.at:
                anchor = (previous,
                          apply_operation(item.operation, previous, item.value))
                self._anchors[index] = anchor
                if not target.restores:
                    # The level to put back when the LAST window on this
                    # target closes. `setdefault`, so an overlapping second
                    # hold does not adopt the first one's shocked level as
                    # the thing to restore.
                    self._baselines.setdefault(item.target, previous)
            else:
                anchor = self._anchors.get(index)
                if anchor is None:
                    raise ScenarioValidationError(
                        f"{item.target} is inside a {item.shape} that began "
                        f"on day {item.at}, but this run never applied day "
                        f"{item.at}: there is no value to hold. A hold and a "
                        f"ramp are anchored to the level the field had on "
                        f"their first day, so a run that joins one part-way "
                        f"through has nothing to anchor to. Start the run at "
                        f"or before day {item.at}, or move the intervention."
                    )
            start, finish = anchor
            if item.shape == "ramp":
                step = (day - item.at + 1) / item.duration
                new = _interpolate(start, finish, step)
            else:
                new = finish

        # The result, not the recipe, is what the engine has to be able to
        # carry. `check` saw the multiplier at construction and could not see
        # what it would produce, because that depends on where the chain had
        # arrived -- so `add -500` on macro.vix wrote a VIX of -485 and the
        # market traded a session against it, since (vix/15)^2 squares the
        # sign away rather than raising anything.
        reason = target.outside_domain(new)
        if reason is not None:
            raise ScenarioValidationError(
                f"{self._label or 'this scenario'} would write {reason} to "
                f"{item.target} on day {day}: {item.operation} "
                f"{item.value!r} applied to {target.show(summarise(previous))} "
                f"gives {target.show(summarise(new))}. A relative operation is "
                f"relative to whatever the endogenous chain has reached, so "
                f"its result cannot be checked when the scenario is written "
                f"-- only here. Use `set` if you mean an absolute level."
            )

        target.write(engine, new)
        return Firing(
            day=day, scenario=self._label, role=item.role, target=item.target,
            operation=item.operation, value=item.value, shape=item.shape,
            previous=summarise(previous), new=summarise(new),
        )

    @property
    def fields(self) -> tuple[str, ...]:
        return tuple(sorted(self._drivers))

    def to_json(self, days: int | None = None) -> str:
        """The scenario as JSON, so a result can cite what it ran under.

        Two documents, and which one you get depends on what the scenario is.

        A scenario built only from pins serialises as it always has: schema
        1, the realised PATH over ``days``. That is deliberate rather than
        lazy -- every manifest already published carries this shape, and its
        fingerprint has to keep meaning the same thing.

        A scenario carrying interventions serialises as schema 2: the
        resolved shocks and transmission, the name, the description, the
        source file's name and the fingerprint, plus the path if it also has
        pins. What is recorded is the RESOLVED experiment, not a reference to
        a YAML file, so the run replays after the file is edited or deleted.

        ``days`` may be omitted only when the scenario has no pins, because
        then there is no path to realise.

        The PATH rather than the recipe. A recipe is only citable while the
        constructor that built it keeps behaving the same way; the realised
        values are the scenario regardless of what any later version does.

        ``days`` must be at least one. A zero-day document carries no path,
        and :meth:`from_json` reading it back produced a scenario driving
        NOTHING: a round trip that quietly discarded every field, and a
        reproduced run that applied no pins at all.
        """
        if self._drivers and (days is None or days < 1):
            raise ValidationError(
                f"days must be at least 1 to serialise a scenario, got {days}. "
                f"The serialised form is the realised PATH, so a zero-day "
                f"document records no path -- reading it back would give a "
                f"scenario driving nothing at all, in place of one driving "
                f"{', '.join(self.fields) or 'nothing'}. Serialise the "
                f"horizon the run actually uses."
            )
        if not (self._shocks or self._transmission):
            # A pins-only scenario serialises exactly as it always has, byte
            # for byte. Every published manifest and every fingerprint over
            # one stays valid, which matters more than a uniform schema
            # number: a result cited last month has to replay this month.
            return json.dumps(
                {"schema": 1, "label": self._label, "days": days,
                 "path": self.table(days)},
                sort_keys=True, separators=(",", ":"),
            )

        payload: dict[str, Any] = {
            "schema": SCENARIO_SCHEMA + 1,
            "label": self._label,
            "name": self._label,
            "description": self._description,
            "source": self._source_name(),
            "fingerprint": self.fingerprint,
            "shocks": [item.as_dict() for item in self._shocks],
            "transmission": [item.as_dict() for item in self._transmission],
        }
        if self._drivers:
            payload["days"] = days
            payload["path"] = self.table(days)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    def _source_name(self) -> str | None:
        """The file's NAME, never its path.

        A full path is machine-specific, and this document is fingerprinted
        inside a `RunManifest` -- so a path would make two people running the
        same experiment produce two different fingerprints. The name is the
        part that identifies the scenario; `scenario.source` keeps the path
        the caller actually gave.
        """
        if self._source is None:
            return None
        return self._source.replace("\\", "/").rsplit("/", 1)[-1]

    def from_json(cls, text: str) -> "Scenario":
        """Rebuild a scenario from :meth:`to_json` output.

        What comes back is the REALISED PATH as an object: a scenario whose
        every day returns exactly the recorded values, whatever constructor
        originally built them. That is the honest direction of the round trip
        The serialised form is the path rather than the recipe, so the restored
        object is the path too. Beyond the recorded horizon it holds its final
        values, the same rule :meth:`ramp` applies after its end, so a longer
        run is defined rather than an IndexError.

        A newer schema is refused rather than read on a best-effort basis, and
        so is an inconsistent document, such as a day count that disagrees with the
        path, days out of order, or fields that appear and disappear between
        rows. Each of those describes a scenario nobody constructed, and a
        loader that guessed its way past them would replay a run under pins
        the original never applied.
        """
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValidationError("not a tradefloor scenario document")
        schema = payload.get("schema", 0)
        if schema > SCENARIO_SCHEMA + 1:
            raise ValidationError(
                f"scenario schema {schema} is newer than this version "
                "understands. Upgrade tradefloor rather than reading it partially."
            )

        scenario_out: "Scenario | None" = None
        if schema >= SCENARIO_SCHEMA + 1:
            scenario_out = cls._from_schema_2(payload)
            if "path" not in payload:
                return scenario_out
        elif "path" not in payload:
            raise ValidationError("not a tradefloor scenario document")

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

        scenario = scenario_out if scenario_out is not None else cls(
            label=payload.get("label", ""))
        last = len(path) - 1
        for field, series in values.items():
            scenario._pin(field, _Pin(
                0,
                (lambda series=series, last=last:
                    lambda day: series[day if day < last else last])(),
                f"from_json path for {field!r} from day 0",
            ))
        return scenario

    @classmethod
    def _from_schema_2(cls, payload: dict[str, Any]) -> "Scenario":
        """The intervention half of a recorded scenario.

        The recorded fingerprint is checked where checking it means
        something. For a document with no path the round trip is exact, so a
        mismatch is a document somebody edited and it is refused. For a
        document that also carries a realised path it is not: the rebuilt
        pins declare a RECORDED PATH where the original declared a ramp or a
        step, which is a different statement about the same run and
        fingerprints differently. Checking there would fail on every honest
        document, so it does not check.
        """
        scenario = cls(
            label=payload.get("label", payload.get("name", "")),
            description=payload.get("description", "") or "",
        )
        source = payload.get("source")
        if source is not None:
            scenario._source = str(source)
        for role, key in (("shock", "shocks"),
                          ("transmission", "transmission")):
            for index, item in enumerate(payload.get(key) or ()):
                scenario.intervene(Intervention.from_dict(
                    item, role=role, where=f"{key}[{index}]"))

        recorded = payload.get("fingerprint")
        if (recorded is not None and "path" not in payload
                and recorded != scenario.fingerprint):
            raise ValidationError(
                f"this scenario document records fingerprint {recorded} "
                f"and resolves to {scenario.fingerprint}. The two disagree, "
                f"so the document was edited after it was written and no "
                f"longer describes the run that produced it."
            )
        return scenario

    def from_yaml(cls, source: str) -> "Scenario":
        """Read a scenario from a YAML file, or from YAML text.

        ```python
        scenario = tf.Scenario.from_yaml("scenarios/liquidity_crisis.yml")
        ```

        A string that names a readable file is read as one; anything else is
        treated as the document itself, so a scenario can be written inline
        in a notebook or a test.

        The reader is :mod:`tradefloor.yaml_subset`, which implements the
        block-style subset this schema uses and REFUSES everything else by
        name -- tags, anchors, flow collections, multiple documents. It has
        no constructor to reach, so a scenario file cannot name a Python
        type, and the loader then rejects every key outside the schema. The
        library keeps its promise of no dependencies, and a configuration
        file stays configuration.

        The YAML and the Python forms resolve to the same object: the same
        interventions in the same order, and therefore the same
        :attr:`fingerprint`.
        """
        text = source
        path: str | None = None
        if "\n" not in source and len(source) < 4096:
            try:
                with open(source, "r", encoding="utf-8") as handle:
                    text = handle.read()
                path = source
            except OSError:
                text = source
        return cls.from_document(yaml_subset.read(text), source=path)

    from_yaml = _constructor(from_yaml, (
        "there is nothing to add: from_yaml builds a whole scenario from a "
        "document. Load it on the class, or read the file and add its "
        "interventions to this one with .intervene(...)."
    ))

    def from_document(cls, document: Any, *,
                      source: str | None = None) -> "Scenario":
        """Build a scenario from an already-parsed configuration mapping.

        The layer under :meth:`from_yaml`, and the one that decides what a
        scenario document may say. Everything outside the schema is refused
        rather than ignored: an unknown key is either a typo or a newer
        schema, and running it either way applies an experiment nobody wrote.
        """
        if not isinstance(document, dict):
            raise ScenarioValidationError(
                f"a scenario document is a mapping with `version` and "
                f"`scenario` keys, got {type(document).__name__}."
            )
        version = document.get("version")
        if version is None:
            raise ScenarioValidationError(
                "the document has no `version`. Add `version: 1` at the top: "
                "an unversioned configuration format cannot be changed "
                "without silently changing what old files mean."
            )
        if version != SCENARIO_SCHEMA:
            raise ScenarioValidationError(
                f"scenario schema version {version!r} is not one this build "
                f"reads (it reads version {SCENARIO_SCHEMA}). "
                + ("Upgrade tradefloor rather than reading it partially."
                   if isinstance(version, int) and version > SCENARIO_SCHEMA
                   else "Check the `version` line.")
            )
        extra = sorted(set(document) - {"version", "scenario"})
        if extra:
            raise ScenarioValidationError(
                f"unknown top-level key(s) {', '.join(repr(k) for k in extra)}. "
                f"A scenario document has exactly `version` and `scenario`."
            )
        body = document.get("scenario")
        if not isinstance(body, dict):
            raise ScenarioValidationError(
                "the document has no `scenario:` block."
            )
        allowed = {"name", "description", "shocks", "transmission"}
        extra = sorted(set(body) - allowed)
        if extra:
            raise ScenarioValidationError(
                f"unknown key(s) in the scenario block: "
                f"{', '.join(repr(k) for k in extra)}. A scenario has "
                f"{', '.join(sorted(allowed))} and nothing else."
                + ("\n\nInterventions go under `shocks:` (what the scenario "
                   "asserts happened) or `transmission:` (what it assumes "
                   "happened next)."
                   if "interventions" in extra else "")
            )
        name = body.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ScenarioValidationError(
                "the scenario block needs a `name`. A run recorded under an "
                "unnamed scenario cannot be told apart from any other."
            )
        description = body.get("description") or ""
        if not isinstance(description, str):
            raise ScenarioValidationError(
                f"description must be text, got {type(description).__name__}."
            )

        scenario = cls(name=name.strip(), description=description.strip())
        scenario._source = source
        total = 0
        for role, key in (("shock", "shocks"),
                          ("transmission", "transmission")):
            items = body.get(key)
            if items is None:
                continue
            if not isinstance(items, list):
                raise ScenarioValidationError(
                    f"`{key}` must be a list of interventions, got "
                    f"{type(items).__name__}."
                )
            for index, item in enumerate(items):
                scenario.intervene(Intervention.from_dict(
                    item, role=role, where=f"{key}[{index}]"))
                total += 1
        if total == 0:
            raise ScenarioValidationError(
                f"scenario {name!r} declares no interventions, so applying it "
                f"produces a market indistinguishable from one that ran "
                f"without it. Give it at least one `shocks:` entry."
            )
        return scenario

    from_document = _constructor(from_document, (
        "there is nothing to add: from_document builds a whole scenario. "
        "Load it on the class and use the result."
    ))

    from_json = _constructor(from_json, (
        "there is nothing to add: from_json rebuilds a complete recorded "
        "path. Load it on the class and use the result, or take the fields "
        "you want out of Scenario.from_json(text).at(day) and pin them."
    ))

    def __bool__(self) -> bool:
        return bool(self._drivers or self._shocks or self._transmission)

    def __repr__(self) -> str:
        label = f"{self._label!r}, " if self._label else ""
        parts = []
        if self._drivers:
            parts.append(f"driving {', '.join(self.fields)}")
        if self._shocks:
            parts.append(f"{len(self._shocks)} shock(s)")
        if self._transmission:
            parts.append(f"{len(self._transmission)} assumption(s)")
        return f"Scenario({label}{'; '.join(parts) or 'driving nothing'})"

    def __str__(self) -> str:
        return self.describe()

    # -- ready-made shapes ------------------------------------------------

    def rate_shock(cls, *, start: float = 0.025, end: float = 0.05,
                   over: int = 30, begin: int = 0,
                   credit_spread: float = 0.02) -> "Scenario":
        """A hiking (or cutting) cycle across the whole curve.

        Moves the policy rate AND the corporate bond yield, held apart by
        ``credit_spread``. Both, because the valuation discounts off the
        corporate yield and pinning the policy rate alone changes nothing at
        all, silently. See this module's docstring.

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

        **This raises realised volatility**, since the 2026-08 coupling of
        the market factor's variance target to VIX, and not before, which
        is why this docstring once said the opposite and was right then.
        Measured on ``Universe.random(20, seed=11)`` over 120 days, sim
        seed 3: the default spike to 45 moves annualised realised
        volatility from a no-scenario 58.17% to 67.01%, a peak of 80 to
        74.57%, and volatility clustering RISES with it (|r| acf(1) 0.334
        to 0.357 and 0.378), where the pre-coupling model measurably
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
        name accurate again, but the rename stands. :meth:`vix_shock`
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

    ``model`` selects the coefficient set, either a preset name or a
    :class:`tradefloor.ModelParams`, defaulting to the shipped preset. The
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


def _run_in_lockstep(
    left: Scenario, right: Scenario, *, seed: int,
    universe: Sequence[Instrument], days: int, macro: Macro | None = None,
    ticks_per_day: int = 390, start: tuple[int, int, int] = (9, 30, 3),
    record: bool = False, model: str | ModelParams | None = None,
) -> tuple[Engine, Engine, int | None]:
    """Two worlds, a day at a time, and the first day they part.

    The same day loop as :func:`run_scenario`, run twice in step so the
    digests can be compared as they go. Every worry a second loop would
    raise -- that it might apply the scenario at a different point, or record
    on a different day -- is why this is the only other place that loop is
    written, and why the two are checked against each other in
    ``tests/test_scenario.py``.

    The day returned is the first on which the two markets differ at all,
    which for an intervention scenario should be the day its first
    intervention fires. Earlier would mean the scenario reached the market
    before it said it did; later would mean it fired into a market that did
    not notice.
    """
    hour, minute, day_of_week = start
    engines = [Engine(seed=seed, universe=universe, macro_state=macro,
                      model=model) for _ in range(2)]
    from .manifest import market_digest

    first_divergence: int | None = None
    for day in range(days):
        for scenario, engine in zip((left, right), engines, strict=True):
            scenario.apply(engine, day)
            engine.open_market()
            engine.run_session(hour, minute, day_of_week, ticks_per_day)
            if record:
                engine.record(day)
            engine.close_market()
        if first_divergence is None and \
                market_digest(engines[0]) != market_digest(engines[1]):
            first_divergence = day
    return engines[0], engines[1], first_divergence


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
    if scenario.interventions:
        # An intervention scenario is compared against ITSELF WITHOUT THE
        # INTERVENTIONS, so the question is not whether the path moves but
        # whether anything fires inside the horizon. Nothing firing is the
        # same confident zero by a different route.
        fires = [item for item in scenario.interventions if item.at < days]
        if fires:
            return
        earliest = min(item.at for item in scenario.interventions)
        raise ValidationError(
            f"compare() would report exactly 0.00% on every instrument: no "
            f"intervention in {scenario.name or 'this scenario'} fires inside "
            f"a {days}-day run. The earliest is on day {earliest}, so both "
            f"worlds are the baseline. Run at least {earliest + 1} days, or "
            f"move the shock earlier."
        )

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
    trace: bool = False,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the same seed under two macro paths and difference them.

    The counterfactual real markets cannot offer. You cannot re-run a year
    without its hiking cycle. Your only observation is the one that happened.
    Here both are runnable, holding every noise draw, every news item and every
    shock identical, so the difference is the scenario and nothing else.

    ``baseline`` defaults to holding the scenario's fields at their day-zero
    values, which is the right comparison: it isolates the PATH rather than
    conflating it with the level the path started from.

    That default is only meaningful for a scenario that MOVES inside the
    horizon, and a scenario that does not is refused rather than reported.
    For a ``hold``-only scenario, or a ``step`` at day zero, or any shock
    whose start day falls outside ``days``, the default baseline IS the
    scenario, and the comparison returns exactly 0.00% on every instrument.
    A confident, meaningless zero reads as "the shock did nothing", which is
    the worst answer available: it is wrong, it looks like a finding, and
    nothing about it looks like a mistake. See :meth:`Scenario.hold`.

    Keyword arguments, ``model=`` among them, pass through to
    :func:`run_scenario` and apply to BOTH worlds, because a shocked world
    differenced against a baseline under a different coefficient set would
    measure the model gap dressed up as the scenario's effect. The result
    records ``model_fingerprint``.

    ``trace=True`` runs the two worlds a day at a time and reports
    ``first_divergence``: the first day on which the two markets differ at
    all. For an intervention scenario that is a CHECK and not a curiosity,
    because it should equal the day the first intervention fires. Earlier
    means the scenario reached the market before it said it did; later means
    it fired into a market that did not notice. It costs a market digest per
    day per world, which is why it is off by default rather than free.
    """
    import struct

    if days < 1:
        raise ValidationError("days must be at least 1")

    if baseline is None:
        _refuse_self_comparison(scenario, days)
        # For an intervention scenario the counterfactual is the same world
        # with the interventions removed: same pins, same seed, same draws,
        # and the shock is the only difference. For a pins-only scenario it
        # is the day-zero levels held flat, which is what isolates a PATH
        # from the level it starts at.
        baseline = (scenario.without_interventions() if scenario.interventions
                    else Scenario(label="flat").hold(**scenario.at(0)))
    elif (scenario.table(days) == baseline.table(days)
          and scenario.interventions == baseline.interventions):
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

    if trace:
        shocked, flat, first_divergence = _run_in_lockstep(
            scenario, baseline, seed=seed, universe=universe, days=days,
            **kwargs)
    else:
        first_divergence = None
        shocked = run(scenario)
        flat = run(baseline)
    firings = scenario.firing_table()
    # `run` on the baseline may reset the scenario's own trail if the two
    # objects are the same one, which an explicit baseline can make them.
    # Hold the shocked world's trail rather than reading it back afterwards.

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
        # The scenario's own identity, so a comparison can be cited without
        # the object that produced it, and the days its interventions
        # actually fired -- which is where to start looking when the
        # divergence in `move_pct` needs a cause.
        "scenario_fingerprint": scenario.fingerprint,
        "interventions": firings,
        # Both worlds ran it, so one value names the model for the whole
        # comparison -- the same provenance rule as Scorecard's.
        "model_fingerprint": shocked.model_fingerprint,
        "draws": shocked.draws_consumed,
        "draw_delta": delta,
        "exact": delta == 0,
        # None unless `trace=True`. The first day the two markets differ at
        # all, which for an intervention scenario is a check rather than a
        # curiosity: it should equal the day the first intervention fires.
        "first_divergence": first_divergence,
        "tickers": list(shocked.tickers),
        "move_pct": moves,
        "median_pct": median,
        "worst_pct": min(moves),
        "best_pct": max(moves),
    }
