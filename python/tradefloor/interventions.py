"""What a scenario is made of: named targets, three operations, one clock.

A scenario in this library is not a feature flag. There is no `start_war()`,
no `elect_president()`, no `recession=True`. A scenario is a **named,
inspectable collection of explicit interventions**, and the narrative that
motivated it is a description, not a mechanism.

The distinction is the whole point. `OilCrisis` hides its assumptions inside a
class name; a scenario that says

    shocks:       commodity.oil  x1.40  at 50
    transmission: macro.inflation +1.5pp at 55

states them. The second is falsifiable and the first is not, and this library
cannot predict how a real oil shock transmits to real inflation. What it can
do is let you say what you are assuming and then measure how an agent behaves
under that assumption.

## The registry, and why it is short

An intervention names a TARGET, and the targets are an explicit registry, not
a path into the object graph. `target: simulator.internal.foo.bar` is
refused, and so is every economic variable this model does not have. A
registry that accepted `sector.energy.earnings` because the phrase sounds
plausible would be lying: nothing in the engine carries per-sector earnings,
and a scenario that appeared to run would be measuring nothing.

So every entry here is a field some part of the engine actually reads, and
each one records WHAT reads it and HOW FAST it arrives. That second column is
not decoration. Only five things reach a price directly -- the policy rate,
the corporate bond yield, the QE boost, VIX and the cycle phase, plus quoted
depth through the book. Everything else in the economy reaches the market
through the macro chain: a monthly inflation update, then the central bank's
next MEETING, then the curve. The first meeting is scheduled 45 days out. A
sixty-day study of an oil shock will see very little, and it will not warn
you. `note` on each target says so before you run it.

## Three operations and one clock

`set`, `add`, `multiply`. Nothing else, and deliberately no expressions: a
configuration language grows until it needs a debugger, and the interesting
part of an experiment is never the arithmetic.

`at` counts days from the moment the run loop starts applying the scenario.
For a fresh run that is simulation day zero. For a fork it is the day of the
fork, which is what makes the same YAML mean the same experiment on both
sides of a checkpoint. See :meth:`tradefloor.Scenario.apply`.

## Relative operations read the live market

`multiply` is not expressible as a path computed in advance, because the value
it multiplies is whatever the endogenous chain has arrived at by day `at`.
So an intervention READS the field, applies its operation, and writes the
result back -- which is also why the audit trail can report `previous: 82.14,
new: 115.00` rather than restating the recipe.

That read and that write have to be in the same units, or a `multiply` by 1.4
is a factor of a hundred out on a plausible-looking trajectory. Both go
through `Engine.macro_fields`, which is the read side of `pin_macro` in
`pin_macro`'s own denomination, for exactly this reason.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import struct
from typing import Any, Callable, Sequence

from ._core import Engine, ValidationError

#: The version of the YAML/JSON scenario document this build reads. A document
#: that names a newer one is refused rather than read on a best-effort basis:
#: a loader that guessed its way past an unknown key would run an experiment
#: nobody wrote.
SCENARIO_SCHEMA = 1

#: The whole operation vocabulary. `set` ignores the live value; `add` and
#: `multiply` are relative to it.
OPERATIONS = ("set", "add", "multiply")

#: What an intervention does over time.
#:
#: - ``impulse``    write once on day ``at``, then let the chain carry it.
#: - ``permanent``  write the same value every day from ``at`` to the end.
#: - ``hold``       write the same value on ``duration`` consecutive days.
#: - ``ramp``       move from the live value to the target over ``duration``.
#:
#: ``duration`` always means DAYS THE INTERVENTION IS ACTIVE, so a `hold` at
#: day 50 for 25 days owns days 50 to 74 and day 75 belongs to the chain
#: again, and a one-day `ramp` is an `impulse`.
#:
#: "Belongs to the chain again" is doing real work in that sentence. For a
#: macro field, ending a window means simply not writing any more: the daily
#: chain recomputes the field, or mean-reverts it, or the central bank does.
#: Two targets have no such dynamics -- nothing in the engine writes
#: `avg_volume` or `tariff_rate` -- so for those the scenario puts the level
#: back itself when the last window on that target closes, and records the
#: restore in the audit trail. Without that, a twenty-five day liquidity
#: crisis lasted for the rest of the run while its own description said it
#: ended. See :meth:`tradefloor.Scenario._release`.
SHAPES = ("impulse", "permanent", "hold", "ramp")

#: Shapes that need a duration, and shapes that refuse one.
_NEEDS_DURATION = ("hold", "ramp")

#: Which half of a scenario an intervention belongs to. The engine treats
#: both identically -- the split is a claim about EVIDENCE, not about
#: mechanism. A shock is what the scenario asserts happened; a transmission is
#: what the author assumes it did next. Keeping them apart is what lets a
#: report say "this scenario assumes a 1.5pp inflation pass-through" instead
#: of implying the simulator derived one.
ROLES = ("shock", "transmission")

#: Cycle phases, for the one target whose value is a name rather than a
#: number. Duplicated from `_core.CycleName` so a misspelt phase is caught
#: where it is written.
CYCLES = ("expansion", "peak", "contraction", "trough", "recovery")

_RATE_MIN, _RATE_MAX = -0.05, 0.50


class ScenarioValidationError(ValidationError):
    """A scenario that cannot be run, reported where it was written.

    A subclass of :class:`tradefloor.ValidationError`, so code that already
    catches the library's construction errors catches these too. It exists as
    its own type because a scenario is usually written by hand in a file, and
    "which file, which line, which of the four legal operations" is a
    different conversation from a bad argument to a constructor.
    """


# ---------------------------------------------------------------------------
# The target registry
# ---------------------------------------------------------------------------


class Target:
    """One thing a scenario may move, and what moving it actually reaches.

    ``read`` and ``write`` are the whole mechanism. Everything else on this
    class exists so that a mistake is caught at construction and a result can
    be read without the reader having to know the engine.
    """

    __slots__ = ("name", "units", "note", "numeric", "restores", "_read",
                 "_write", "_check", "_domain", "_format")

    def __init__(self, name: str, *, units: str, note: str,
                 read: Callable[[Engine], Any],
                 write: Callable[[Engine, Any], None],
                 check: Callable[[str, Any], None],
                 format: Callable[[float], str],
                 domain: Callable[[Any], str | None] | None = None,
                 numeric: bool = True, restores: bool = True) -> None:
        self.name = name
        self.units = units
        self.note = note
        # False for a target whose value is a NAME. There is one, and it is
        # what makes this flag worth carrying: a `ramp` interpolates between
        # two values, and interpolating between two cycle phases raised a
        # bare TypeError from inside the run rather than being refused where
        # it was written.
        self.numeric = numeric
        # Whether the engine's own dynamics move this field back on their
        # own. Every macro field does -- the daily chain recomputes it, or
        # mean-reverts it, or the central bank does. Two do not: nothing in
        # the engine writes `avg_volume` (the close policy is Hold) or
        # `tariff_rate`. For those, "held for twenty-five days" has to be
        # made true by writing the level back, or a temporary shock is a
        # permanent one that says otherwise.
        self.restores = restores
        self._read = read
        self._write = write
        self._check = check
        self._domain = domain
        self._format = format

    def read(self, engine: Engine) -> Any:
        return self._read(engine)

    def write(self, engine: Engine, value: Any) -> None:
        self._write(engine, value)

    def check(self, operation: str, value: Any) -> None:
        """Refuse a value this target cannot take, at construction time."""
        self._check(operation, value)

    def outside_domain(self, value: Any) -> str | None:
        """Why this COMPUTED value cannot be written, or None.

        The other half of `check`, and the half that matters for a relative
        operation. `check` sees the multiplier; it cannot see what the
        multiplier will produce, because that depends on where the endogenous
        chain has arrived. So the result is checked too, on the day it is
        written -- which is the only place the answer exists.

        Without this, `add -500` on macro.vix wrote a VIX of -485 and the
        market traded a session against it: `(vix/15)^2` squares away the
        sign, so the variance target rose instead of anything raising an
        error.
        """
        if self._domain is None:
            return None
        if isinstance(value, tuple):
            for item in value:
                reason = self._domain(item)
                if reason is not None:
                    return reason
            return None
        return self._domain(value)

    def show(self, value: float) -> str:
        return self._format(value)

    def __repr__(self) -> str:
        return f"Target({self.name!r}, units={self.units!r})"


def _macro(field: str) -> tuple[Callable[[Engine], Any], Callable[[Engine, Any], None]]:
    """Read and write one pinnable macro field, in `pin_macro`'s own units.

    Both halves go through the same surface on purpose. `macro_state` returns
    a `Macro` (seven fields, fractional) and `state_snapshot()["economy"]`
    returns the core's percent denomination; a `multiply` that read one and
    wrote the other would be out by a hundred and would still produce a
    plausible market. `Engine.macro_fields` is the read side of `pin_macro`,
    field for field and unit for unit.
    """
    def read(engine: Engine) -> Any:
        return engine.macro_fields[field]

    def write(engine: Engine, value: Any) -> None:
        engine.pin_macro(**{field: value})

    return read, write


def _rate_check(low: float = _RATE_MIN, high: float = _RATE_MAX) -> Callable[[str, Any], None]:
    """Catch the percent-for-fraction slip where it is written.

    Rates are FRACTIONS at this boundary: 2% is 0.02. Passing 2 gives 200%,
    which the engine refuses -- but sixty days later, inside a run, with a
    traceback that names `pin_macro` rather than the line of YAML that said
    it. A `set` is checked against the band directly; an `add` is checked
    against the band's WIDTH, because the largest honest delta cannot exceed
    the range it moves inside.
    """
    def check(operation: str, value: Any) -> None:
        _finite(value)
        if operation == "set" and not low <= value <= high:
            raise ScenarioValidationError(
                f"value {value} is outside the plausible band [{low}, {high}] "
                f"for a rate. Rates are FRACTIONS here: 5.2% is 0.052, not "
                f"5.2."
            )
        if operation == "add" and abs(value) > high - low:
            raise ScenarioValidationError(
                f"add {value} is larger than the whole plausible band for a "
                f"rate ({high - low}). Rates are FRACTIONS here: 200 basis "
                f"points is 0.02, not 2."
            )
        if operation == "multiply":
            _positive_multiplier(value)

    return check


def _positive_check(what: str) -> Callable[[str, Any], None]:
    def check(operation: str, value: Any) -> None:
        _finite(value)
        if operation == "multiply":
            _positive_multiplier(value)
        elif operation == "set" and value <= 0:
            raise ScenarioValidationError(
                f"set {value} is not a positive {what}."
            )

    return check


def _range_check(low: float, high: float, what: str) -> Callable[[str, Any], None]:
    """A `set` inside the range, and any multiplier that is a multiplier.

    Separate from `_positive_check` because a range can legitimately include
    zero: fear and greed runs 0 to 100, and refusing `set 0` would refuse
    the most extreme fear the index can express.
    """
    def check(operation: str, value: Any) -> None:
        _finite(value)
        if operation == "multiply":
            _positive_multiplier(value)
        elif operation == "set" and not low <= value <= high:
            raise ScenarioValidationError(
                f"set {value} is outside the {what} range [{low:g}, {high:g}]."
            )

    return check


def _finite_check(operation: str, value: Any) -> None:
    """Any finite number, including zero and negatives.

    For a target whose neutral value IS zero. `qe_pe_boost` opens at 0.0, so
    a check that refused `set 0` refused the one value a scenario is most
    likely to want: turning the boost off.
    """
    _finite(value)
    if operation == "multiply":
        _positive_multiplier(value)


def _finite(value: Any) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ScenarioValidationError(
            f"value must be a number, got {value!r}."
        )
    if value != value or value in (float("inf"), float("-inf")):
        raise ScenarioValidationError(f"value must be finite, got {value!r}.")


def _positive_multiplier(value: Any) -> None:
    if value <= 0:
        raise ScenarioValidationError(
            f"multiply by {value} is not a multiplier. A non-positive factor "
            f"does not scale a level, it replaces it with a different sign or "
            f"with zero -- say what you mean with `set`."
        )


def _domain_positive(what: str) -> Callable[[Any], str | None]:
    def domain(value: Any) -> str | None:
        if not isinstance(value, (int, float)) or value != value:
            return f"{value!r} is not a number"
        if value <= 0:
            return f"{value:g} is not a positive {what}"
        return None
    return domain


def _domain_between(low: float, high: float, what: str) -> Callable[[Any], str | None]:
    def domain(value: Any) -> str | None:
        if not isinstance(value, (int, float)) or value != value:
            return f"{value!r} is not a number"
        if not low <= value <= high:
            return f"{value:g} is outside the {what} range [{low:g}, {high:g}]"
        return None
    return domain


def _domain_rate(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or value != value:
        return f"{value!r} is not a number"
    if not _RATE_MIN <= value <= _RATE_MAX:
        return (f"{value:g} is outside the plausible rate band "
                f"[{_RATE_MIN}, {_RATE_MAX}]")
    return None


def _domain_finite(value: Any) -> str | None:
    if not isinstance(value, (int, float)) or value != value:
        return f"{value!r} is not a number"
    if value in (float("inf"), float("-inf")):
        return "infinity is not a value the engine can carry"
    return None


def _cycle_check(operation: str, value: Any) -> None:
    if operation != "set":
        raise ScenarioValidationError(
            f"macro.cycle takes {operation!r} but a business-cycle phase is a "
            f"NAME, not a number: only `set` means anything. Valid phases: "
            f"{', '.join(CYCLES)}."
        )
    if value not in CYCLES:
        raise ScenarioValidationError(
            f"unknown cycle phase {value!r}. Valid: {', '.join(CYCLES)}."
        )


def _pp(value: float) -> str:
    return f"{value * 100:+.2f}pp"


def _points(value: float) -> str:
    return f"{value:+.2f}"


def _dollars(value: float) -> str:
    return f"${value:,.2f}"


def _shares(value: float) -> str:
    return f"{value:,.0f} sh"


def _plain(value: float) -> str:
    return f"{value:+.4f}"


def _liquidity_read(engine: Engine) -> tuple[float, ...]:
    raw = engine.column("avg_volume")
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def _liquidity_write(engine: Engine, values: Sequence[float]) -> None:
    engine.set_avg_volume(list(values))


def _liquidity_check(operation: str, value: Any) -> None:
    _finite(value)
    if operation == "multiply":
        _positive_multiplier(value)
    elif operation == "set" and value <= 0:
        raise ScenarioValidationError(
            "set 0 is not an empty book: the market maker reads a zero "
            "`avg_volume` as ABSENT and quotes off realised volume instead, "
            "so it thins nothing. Multiply the column down to thin it."
        )


def _make_macro_target(name: str, field: str, *, units: str, note: str,
                       check: Callable[[str, Any], None],
                       format: Callable[[float], str],
                       domain: Callable[[Any], str | None] | None = None,
                       numeric: bool = True, restores: bool = True) -> Target:
    read, write = _macro(field)
    return Target(name, units=units, note=note, read=read, write=write,
                  check=check, format=format, domain=domain, numeric=numeric,
                  restores=restores)


#: Every intervention target this build supports, and nothing else.
#:
#: The `note` on each is what a reader needs before believing a result: which
#: mechanism reads the field, how long it takes to reach a price, and what it
#: was MEASURED to be worth.
#:
#: Every number in a note below comes from the same experiment, run before
#: any of this was written down. `Universe.random(20, seed=101)`, seeds 3, 11
#: and 29, 120 days, the intervention on day 20, and the shocked world
#: differenced against the identical world without it. What is reported is
#: the median instrument's percentage move at day 120, and every one of the
#: 39 comparisons behind these numbers came back with a market draw delta of
#: zero, so the difference is the intervention and nothing else.
#:
#: Read them before believing a scenario. Four of the twelve targets are
#: honest mechanisms with effects too small to see over a hundred days, and
#: one of them is measurably worth exactly nothing. Knowing which is which is
#: the difference between an experiment and a number.
TARGETS: dict[str, Target] = {}


def _register(target: Target) -> Target:
    TARGETS[target.name] = target
    return target


# -- the five levers the market reads directly ------------------------------

_register(_make_macro_target(
    "macro.corporate_yield", "corporate_bond_yield",
    units="fraction",
    note=(
        "The discount rate every equity is valued off, and the only rate "
        "that reprices fair value on the day it moves. Sustain it: the "
        "chain recomputes the corporate yield from the 10-year at the "
        "central bank's next MEETING, so a one-shot write is erased. "
        "Measured, +200bp: -0.23% as an impulse, -3.50% as a permanent."
    ),
    check=_rate_check(), format=_pp, domain=_domain_rate,
))

_register(_make_macro_target(
    "macro.policy_rate", "federal_funds_rate",
    units="fraction",
    note=(
        "The central bank's rate. It is a FALLBACK inside fair value, used "
        "only when no corporate yield is present, and inside the engine "
        "there always is one -- so it changes nothing on the day it moves. "
        "It reaches prices through the curve at the bank's next MEETING, "
        "the first of which is 45 days out, and nothing overwrites it in "
        "between. Measured, +200bp: -3.57%, the same as a permanent, "
        "because for this field an impulse already is one."
    ),
    check=_rate_check(), format=_pp, domain=_domain_rate,
))

_register(_make_macro_target(
    "macro.vix", "vix",
    units="index points",
    note=(
        "Four channels, all immediate: the market factor's conditional "
        "variance target scales as (vix/15)^2, the quoted bid-ask widens by "
        "1 + max(0, (vix-15)/30), cross-sectional correlation rises above "
        "the crisis threshold, and the economy's own crisis premium keys "
        "off it. The strongest short-horizon stress lever here. Measured, "
        "x2.0 held 25 days: -2.23% median, worst name -9.64%. Held for the "
        "whole run instead it measures +16.8% median with a +65% best, "
        "which is a dispersion effect rather than a crisis -- use a "
        "duration for a crisis."
    ),
    check=_positive_check("VIX level"), format=_points,
    domain=_domain_positive("VIX level"),
))

_register(_make_macro_target(
    "macro.qe_pe_boost", "qe_pe_boost",
    units="P/E points",
    note=(
        "Added to the target P/E in fair value. Immediate, and the "
        "strongest lever in the registry: it moves valuation without "
        "touching a rate. Measured, set to 3.0: +19.78%."
    ),
    check=_finite_check, format=_plain, domain=_domain_finite,
))

_register(_make_macro_target(
    "macro.cycle", "cycle",
    units="phase name",
    note=(
        "The business-cycle phase. Immediate through the universe's stress "
        "intensity, and it retargets GDP growth, unemployment and the "
        "recession probability at the next monthly step. `set` only: a "
        "phase is a name. Measured, set to contraction: -3.04%."
    ),
    check=_cycle_check, format=str, numeric=False,
))

_register(Target(
    "market.liquidity",
    units="shares (avg_volume)",
    note=(
        "The `avg_volume` column, which is what the market maker quotes "
        "off: every ladder level is a fraction of a base size derived from "
        "it, and a tick's printed volume is bounded by avg_volume/390. "
        "Immediate, and the only target here that touches execution rather "
        "than valuation -- so it is nearly invisible in prices and "
        "expensive in fills. Measured over five seeds after twenty days: "
        "quoted depth scales exactly with the multiplier (175,060 shares to "
        "70,019 at x0.40), and sweeping 50,000 shares costs 6.08bp at x1.0, "
        "8.57bp at x0.40 and 14.59bp at x0.10. The quoted SPREAD does not "
        "move -- that is VIX's job. `multiply` is the only form that means "
        "anything across a mixed roster. Nothing in the engine writes this "
        "column back, so a `hold` on it restores the pre-shock depth itself "
        "when its window closes."
    ),
    read=_liquidity_read,
    write=_liquidity_write,
    check=_liquidity_check,
    domain=_domain_positive("share count"),
    # Nothing in the engine writes this column back: the shipped close
    # policy is AvgVolumePolicy::Hold. So a hold has to put the depth
    # back itself, or a twenty-five day liquidity crisis quietly lasts
    # for the rest of the run.
    restores=False,
    format=_shares,
))

# -- the chain levers: real, and slower than a short study ------------------

_register(_make_macro_target(
    "macro.inflation", "inflation_rate",
    units="fraction",
    note=(
        "Nothing in the market reads inflation. It reaches prices through "
        "the central bank's reaction function at its next MEETING, then the "
        "curve. The endogenous update is MONTHLY and closes 55% of the gap "
        "to target, so a pinned level persists for weeks rather than days. "
        "Measured, +150bp: -0.60% as an impulse, -1.27% held."
    ),
    check=_rate_check(), format=_pp, domain=_domain_rate,
))

_register(_make_macro_target(
    "commodity.oil", "oil_price",
    units="dollars",
    note=(
        "Real, and weak. Oil above $80 adds (price-80)*0.01 to the MONTHLY "
        "inflation step and below $50 subtracts; that is the whole channel, "
        "and it reaches equities through inflation, the bank and the curve. "
        "The daily chain mean-reverts it 3% a day toward a target near "
        "$75-80 and clamps it into [35, 150]. Measured, x1.40: -0.02% as an "
        "impulse, +0.09% held for the whole run; oil pinned at $140 for a "
        "hundred days measures -0.83%. If a scenario needs a 40% oil shock "
        "to move a market, the pass-through has to be stated as an "
        "assumption -- see scenarios/oil_price_spike.yml."
    ),
    check=_positive_check("oil price"), format=_dollars,
    domain=_domain_positive("oil price"),
))

_register(_make_macro_target(
    "macro.growth", "gdp_growth",
    units="fraction",
    note=(
        "Annualised GDP growth. Feeds unemployment, confidence, the "
        "recession probability, the oil target and the cycle's phase "
        "transitions, all MONTHLY, and reaches equities only through the "
        "central bank -- whose response to weaker growth is to cut, which "
        "supports prices. Measured, -300bp held for a hundred days: +0.36%, "
        "and that sign is the rates channel, not a defect. `macro.cycle` is "
        "the lever a downturn scenario actually wants."
    ),
    check=_rate_check(), format=_pp, domain=_domain_rate,
))

_register(_make_macro_target(
    "macro.unemployment", "unemployment_rate",
    units="fraction",
    note=(
        "The other half of the central bank's mandate, and the Phillips "
        "curve's gap term. Monthly, and it reaches prices through the bank. "
        "Measured, +200bp held: +1.17%, again the dovish-response sign."
    ),
    check=_rate_check(), format=_pp, domain=_domain_rate,
))

_register(_make_macro_target(
    "policy.tariff_rate", "tariff_rate",
    units="fraction",
    note=(
        "An import tariff rate, opening at 5%. Nothing in the engine moves "
        "it endogenously, so it is the one target here that is PERMANENT by "
        "construction: an impulse stays. It adds (rate-5pp)*0.01 to the "
        "monthly inflation step and drags the trade balance. Measured, "
        "+800bp: 0.00% median (worst -0.25%, best +0.37%), which at three "
        "seeds is indistinguishable from noise. Being unmoved by the engine "
        "cuts both ways: a `hold` on it restores the old rate itself when its "
        "window closes, because nothing else would."
    ),
    check=_rate_check(), format=_pp, domain=_domain_rate,
    # The one macro field nothing in the engine moves, which is what
    # makes an impulse on it permanent -- and what means a HOLD on it
    # has to put the old rate back when its window ends.
    restores=False,
))

_register(_make_macro_target(
    "macro.fear_greed", "fear_greed_index",
    units="index points 0-100",
    note=(
        "Sentiment, 0 to 100, opening at 50. Measured at EXACTLY 0.00% on "
        "every instrument, on every seed, over 120 days: nothing in the "
        "market reads it, and its only consumers are the monthly confidence "
        "target and the gold drift, neither of which reaches an equity "
        "price. Registered because it is pinnable and because a target that "
        "silently did nothing would be worse than one that says so."
    ),
    check=_range_check(0.0, 100.0, "sentiment"), format=_points,
    domain=_domain_between(0.0, 100.0, "sentiment"),
))


#: Targets a reader may reasonably expect, and what this build actually has.
#:
#: Every one of these is a phrase that appears in scenario writing and does
#: NOT exist as a mechanism here. Registering them would be the failure this
#: module is built to avoid: a scenario that runs, produces numbers, and
#: measures nothing. Naming them instead means a typo and a missing mechanism
#: get different answers.
UNSUPPORTED: dict[str, str] = {
    "market.volatility": (
        "there is no volatility level to set. This model's volatility is "
        "produced by a GARCH process whose reversion target is driven by "
        "VIX. Use macro.vix, which is the regime lever and moves realised "
        "volatility, the quoted spread and cross-sectional correlation "
        "together"
    ),
    "market.spread": (
        "the quoted spread is computed, not stored: it is a function of VIX "
        "and of the name's own size and volume. Move macro.vix to widen "
        "every spread, or market.liquidity to thin the book"
    ),
    "market.risk_premium": (
        "there is no equity risk premium term. Valuation discounts off "
        "macro.corporate_yield, so a wider required return is a higher "
        "corporate yield here"
    ),
    "execution.depth": (
        "depth is quoted, not set: the maker builds a ladder off avg_volume. "
        "Use market.liquidity"
    ),
    "execution.market_impact": (
        "impact is MEASURED here, not configured. It is what your order pays "
        "walking a real book, and tf.tca reports it against a run where you "
        "did not trade. Thin the book with market.liquidity and impact rises "
        "because there is less to trade against"
    ),
    "macro.interest": (
        "there is no single interest rate here. There is the central bank's "
        "macro.policy_rate, and there is the macro.corporate_yield equities "
        "are actually discounted off, and moving one is not moving the other"
    ),
    "sector.energy.demand": (
        "there is no per-sector economic state. Sectors carry a volatility "
        "multiplier and a correlation loading, not demand, earnings or input "
        "costs, and the per-sector shock mechanism that does exist (News) is "
        "a per-tick input rather than something a scenario can schedule"
    ),
    "sector.energy.earnings": "see sector.energy.demand: no per-sector earnings exist",
    "sector.transport.input_cost": "see sector.energy.demand: no per-sector costs exist",
    "policy.corporate_tax": (
        "there is no tax rate in the economy. Earnings are an instrument "
        "fundamental (eps), fixed for the run"
    ),
    "fiscal.government_spending": (
        "fiscal_stimulus exists but is RECOMPUTED every day from the "
        "automatic stabilisers, so a written value is erased before it "
        "compounds. Registering it would be a lever that does nothing"
    ),
}


def suggest(name: str) -> str:
    """The tail of an unknown-target message: what to write instead.

    Three cases, and they are worth telling apart. A near-miss on a real
    target is a typo and gets the spelling. A name in :data:`UNSUPPORTED` is
    not a typo at all -- the reader has a mechanism in mind that this model
    does not have -- and gets the reason and the nearest real lever. Anything
    else gets the whole registry, because a list of twelve names is shorter
    than a conversation.
    """
    if name in UNSUPPORTED:
        return f"\n\n{name} is not a mechanism in this model: {UNSUPPORTED[name]}."

    close = difflib.get_close_matches(name, list(TARGETS), n=3, cutoff=0.6)
    if close:
        return "\n\nDid you mean:\n  " + "\n  ".join(close)

    prefix = name.split(".")[0] + "."
    family = [t for t in TARGETS if t.startswith(prefix)]
    if family:
        return f"\n\nTargets in {prefix}:\n  " + "\n  ".join(sorted(family))

    return "\n\nSupported targets:\n  " + "\n  ".join(sorted(TARGETS))


def resolve(name: Any) -> Target:
    """A registered target, or a refusal that says what to write instead."""
    if not isinstance(name, str):
        raise ScenarioValidationError(
            f"target must be a string naming a registered target, got "
            f"{name!r}.{suggest(str(name))}"
        )
    target = TARGETS.get(name)
    if target is None:
        raise ScenarioValidationError(
            f"unknown intervention target:\n  {name}{suggest(name)}"
        )
    return target


# ---------------------------------------------------------------------------
# One intervention
# ---------------------------------------------------------------------------


class Intervention:
    """One explicit change to one target at one time.

    Immutable, validated at construction, and the same object whether it came
    from YAML or from Python -- which is what makes a fingerprint comparable
    across the two.

    ``at`` counts days from the start of the run the scenario is applied to.
    See :meth:`tradefloor.Scenario.apply` for what that means after a fork.
    """

    __slots__ = ("target", "operation", "value", "at", "duration", "shape",
                 "role", "_target")

    def __init__(self, target: str, *, operation: str = "multiply",
                 value: Any = None, at: int = 0,
                 duration: int | None = None, shape: str | None = None,
                 role: str = "shock") -> None:
        self._target = resolve(target)
        self.target = self._target.name

        if operation not in OPERATIONS:
            raise ScenarioValidationError(
                f"unknown operation {operation!r} on {self.target}. The whole "
                f"vocabulary is: {', '.join(OPERATIONS)}. There is no "
                f"expression language here on purpose -- a scenario is a "
                f"statement of what changed, not a program."
            )
        if role not in ROLES:
            raise ScenarioValidationError(
                f"unknown role {role!r}. An intervention is either a "
                f"{ROLES[0]!r} -- what the scenario asserts happened -- or a "
                f"{ROLES[1]!r}, which is what the author ASSUMES it did next."
            )
        if value is None:
            raise ScenarioValidationError(
                f"{self.target} needs a value: {operation} of what?"
            )
        self._target.check(operation, value)

        at = _whole("at", at)
        if at < 0:
            raise ScenarioValidationError(
                f"at cannot be negative, got {at}. It counts days forward "
                f"from the start of the run this scenario is applied to."
            )

        shape = _resolve_shape(shape, duration, self.target)
        if shape == "ramp" and not self._target.numeric:
            raise ScenarioValidationError(
                f"{self.target} cannot ramp: its value is a "
                f"{self._target.units}, and a ramp interpolates between two "
                f"numbers. Use `impulse`, `hold` or `permanent`."
            )
        if shape in _NEEDS_DURATION:
            duration = _whole("duration", duration)
            if duration < 1:
                raise ScenarioValidationError(
                    f"a {shape} needs a duration of at least one day, got "
                    f"{duration}."
                )
        else:
            duration = None

        self.operation = operation
        self.value = value
        self.at = at
        self.duration = duration
        self.shape = shape
        self.role = role

    # -- timing ------------------------------------------------------------

    @property
    def last_day(self) -> int | None:
        """The final day this intervention writes, or None for a permanent.

        Used to tell a scenario that never fires inside a horizon from one
        that does, which is the difference between an honest zero and a
        meaningless one.
        """
        if self.shape == "permanent":
            return None
        if self.duration is None:
            return self.at
        return self.at + self.duration - 1

    def active_on(self, day: int) -> bool:
        if day < self.at:
            return False
        if self.shape == "permanent":
            return True
        if self.duration is None:
            return day == self.at
        return day < self.at + self.duration

    # -- serialisation -----------------------------------------------------

    def as_dict(self) -> dict[str, Any]:
        """The canonical resolved form. This is what gets fingerprinted.

        Every field appears, including the defaults, because a fingerprint
        over a document whose keys come and go is a fingerprint over the
        author's typing habits. ``duration`` is null rather than absent for
        the same reason.
        """
        return {
            "target": self.target,
            "operation": self.operation,
            "value": self.value,
            "at": self.at,
            "duration": self.duration,
            "shape": self.shape,
        }

    @classmethod
    def from_dict(cls, payload: Any, *, role: str = "shock",
                  where: str = "") -> "Intervention":
        if not isinstance(payload, dict):
            raise ScenarioValidationError(
                f"{where or 'an intervention'} must be a mapping of "
                f"target/operation/value/at, got {type(payload).__name__}."
            )
        allowed = {"target", "operation", "value", "at", "duration", "shape"}
        extra = sorted(set(payload) - allowed)
        if extra:
            raise ScenarioValidationError(
                f"{where or 'an intervention'} carries unknown key(s) "
                f"{', '.join(repr(k) for k in extra)}. An intervention has "
                f"exactly: {', '.join(sorted(allowed))}. A key this build "
                f"does not understand is either a typo or a document from a "
                f"newer schema, and running it either way would apply an "
                f"experiment nobody wrote."
            )
        if "target" not in payload:
            raise ScenarioValidationError(
                f"{where or 'an intervention'} names no target."
            )
        try:
            return cls(
                payload["target"],
                operation=payload.get("operation", "multiply"),
                value=payload.get("value"),
                at=_read_at(payload.get("at", 0), where),
                duration=payload.get("duration"),
                shape=payload.get("shape"),
                role=role,
            )
        except ScenarioValidationError as exc:
            if where:
                raise ScenarioValidationError(f"{where}: {exc}") from None
            raise

    # -- reading -----------------------------------------------------------

    def describe(self) -> str:
        """One line: when, what, how much."""
        target = self._target
        if self.operation == "multiply":
            change = f"x{self.value:g}"
        elif self.operation == "add":
            change = target.show(self.value)
        else:
            change = f"= {target.show(self.value)}"

        when = f"day {self.at}"
        if self.shape == "hold":
            when += f"-{self.at + self.duration - 1}"
        elif self.shape == "ramp":
            when += f"..{self.at + self.duration - 1} ramp"
        elif self.shape == "permanent":
            when += "+"
        return f"{when:<18} {self.target:<24} {change}"

    def __repr__(self) -> str:
        bits = [repr(self.target), f"operation={self.operation!r}",
                f"value={self.value!r}", f"at={self.at}"]
        if self.duration is not None:
            bits.append(f"duration={self.duration}")
        bits.append(f"shape={self.shape!r}")
        return f"Intervention({', '.join(bits)})"

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, Intervention):
            return NotImplemented
        return (self.as_dict() == other.as_dict()
                and self.role == other.role)

    def __hash__(self) -> int:
        return hash((self.role, tuple(sorted(self.as_dict().items(),
                                             key=lambda kv: kv[0]))))


def _whole(name: str, value: Any) -> int:
    """An integer day count, refusing the float that looks like one.

    ``at: 50.0`` is almost always a YAML slip rather than an intention, and
    accepting it would put a float in the fingerprint where an int belongs --
    so the same scenario written two ways would fingerprint differently.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ScenarioValidationError(
            f"{name} must be a whole number of days, got {value!r}."
        )
    if isinstance(value, float):
        if value != int(value):
            raise ScenarioValidationError(
                f"{name} must be a whole number of days, got {value!r}. "
                f"Days are the simulator's step; there is no half day."
            )
        value = int(value)
    return int(value)


def _read_at(raw: Any, where: str = "") -> int:
    """``at: 50`` or the explicit ``at: {relative: 50}``.

    The two are the same thing. The long form exists because "day 50" after a
    fork is genuinely ambiguous in the abstract, and a document that says
    which one it means stays readable when this library grows the other. This
    build supports RELATIVE only -- days since the run began applying the
    scenario -- and refuses `absolute` by name rather than silently treating
    it as relative, which would move every shock in the file.
    """
    if isinstance(raw, dict):
        keys = sorted(raw)
        if keys == ["relative"]:
            return _whole("at.relative", raw["relative"])
        if keys == ["absolute"]:
            raise ScenarioValidationError(
                "at: {absolute: N} is not supported in schema 1. Timing here "
                "is RELATIVE: `at` counts days from the moment the run starts "
                "applying the scenario, which for a fresh run is simulation "
                "day zero and for a fork is the day of the fork. That is what "
                "makes one file mean one experiment on both sides of a "
                "checkpoint. Write `at: N` or `at: {relative: N}`."
            )
        raise ScenarioValidationError(
            f"at takes a number of days, or {{relative: N}}. Got keys "
            f"{keys}."
        )
    return _whole("at", raw)


def _resolve_shape(shape: str | None, duration: Any, target: str) -> str:
    """Pick the shape, and refuse the combinations that mean two things.

    A duration with no shape is a hold, and no duration with no shape is an
    impulse: those are the readings anyone would give the YAML, so they are
    the defaults. Every other combination is stated rather than guessed.
    """
    if shape is None:
        return "hold" if duration is not None else "impulse"
    if shape in ("temporary", "spike"):
        raise ScenarioValidationError(
            f"shape {shape!r} is not one of this build's shapes. A temporary "
            f"shock is `hold` with a duration; a spike is a short one. "
            f"Shapes: {', '.join(SHAPES)}."
        )
    if shape not in SHAPES:
        raise ScenarioValidationError(
            f"unknown shape {shape!r} on {target}. Shapes: "
            f"{', '.join(SHAPES)}."
        )
    if shape in _NEEDS_DURATION and duration is None:
        raise ScenarioValidationError(
            f"shape {shape!r} needs a duration: how many days is it active?"
        )
    if shape not in _NEEDS_DURATION and duration is not None:
        fix = ("`permanent` runs to the end of the run, so a duration would "
               "contradict it -- use `hold` for a fixed window"
               if shape == "permanent" else
               "an `impulse` is one write and the chain carries it from "
               "there -- use `hold` to pin it for a window")
        raise ScenarioValidationError(
            f"shape {shape!r} takes no duration, but one was given "
            f"({duration}). {fix}."
        )
    return shape


# ---------------------------------------------------------------------------
# The audit trail
# ---------------------------------------------------------------------------


class Firing:
    """One write the scenario made, on the day it made it, with real values.

    The point of recording this rather than the recipe: a `multiply` says
    x1.40, and what a reader needs to know afterwards is that oil went from
    82.14 to 115.00 -- which depends on where the endogenous chain had got to.

    ``operation`` is the intervention's own operation, with one addition:
    ``"release"`` is the write that puts a level back when the last window on
    a target the engine never restores closes. It is in the trail rather than
    silent, because a reader comparing two runs needs to see the depth come
    back as much as they need to see it go.

    A column target (there is one, `market.liquidity`) reports the column
    TOTAL before and after, because forty per-instrument pairs in a log is not
    an audit trail anybody reads. The per-name values are in the engine.
    """

    __slots__ = ("day", "scenario", "role", "target", "operation", "value",
                 "shape", "previous", "new")

    def __init__(self, *, day: int, scenario: str, role: str, target: str,
                 operation: str, value: Any, shape: str,
                 previous: Any, new: Any) -> None:
        self.day = day
        self.scenario = scenario
        self.role = role
        self.target = target
        self.operation = operation
        self.value = value
        self.shape = shape
        self.previous = previous
        self.new = new

    def as_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "scenario": self.scenario,
            "role": self.role,
            "target": self.target,
            "operation": self.operation,
            "value": self.value,
            "shape": self.shape,
            "previous": self.previous,
            "new": self.new,
        }

    def __repr__(self) -> str:
        return (f"Firing(day={self.day}, {self.target}, {self.operation} "
                f"{self.value!r}, {self.previous!r} -> {self.new!r})")

    def __str__(self) -> str:
        target = TARGETS.get(self.target)
        show = target.show if target else repr
        return (f"day {self.day:>4}  {self.target:<24} "
                f"{self.operation} {self.value!r:<8} "
                f"{show(self.previous)} -> {show(self.new)}")


# ---------------------------------------------------------------------------
# Applying one intervention
# ---------------------------------------------------------------------------


def apply_operation(operation: str, current: Any, value: Any) -> Any:
    """`set`, `add` or `multiply`, over a scalar or over a column."""
    if isinstance(current, tuple):
        return tuple(apply_operation(operation, c, value) for c in current)
    if operation == "set":
        return value
    if operation == "add":
        return current + value
    return current * value


def summarise(value: Any) -> Any:
    """What goes in the audit trail: a scalar, or a column's total."""
    if isinstance(value, tuple):
        return sum(value)
    return value


def canonical_json(payload: Any) -> str:
    """One byte sequence per document, so a fingerprint is a fingerprint."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def sha256_of(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()
