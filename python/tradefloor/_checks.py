"""Argument checks shared by :func:`tradefloor.evaluate`, :func:`tradefloor.rank`
and :class:`tradefloor.World`.

Each check refuses a wrong argument in words that name the argument, say
what came instead and show a call that works. Before 0.8.5 these arguments
reached Python's internals first, and the caller read things like
"'int' object is not iterable" or "'<' not supported between instances of
'str' and 'int'", or, for an agent passed as its class, nothing at all: the
run finished and scored zero.

None of these checks changes a run that was valid before. Each refuses only
an input that used to raise somewhere deeper or to run as something the
caller did not mean.
"""

from __future__ import annotations

import numbers
from typing import Any, Mapping

from ._core import Macro, ValidationError


def describe(value: Any) -> str:
    """What a wrong argument was, in a few words: "None", "the number 5",
    "the string '1e6'", "a dict"."""
    if value is None:
        return "None"
    if isinstance(value, bool):
        return repr(value)
    if isinstance(value, numbers.Number):
        return f"the number {value!r}"
    if isinstance(value, str):
        text = value if len(value) <= 40 else value[:37] + "..."
        return f"the string {text!r}"
    if isinstance(value, type):
        return f"the class {value.__name__}"
    return f"a {type(value).__name__}"


def whole_number(name: str, value: Any, *, minimum: int = 1) -> int:
    """``value`` as an int of at least ``minimum``, or a refusal naming
    ``name``: "days must be 1 or more, got 0." A float, even 5.0, is
    refused, because a count of days or ticks that is not whole is a
    mistake somewhere upstream."""
    if isinstance(value, bool) or not isinstance(value, numbers.Integral):
        raise ValidationError(
            f"{name} must be a whole number of {minimum} or more, got "
            f"{describe(value) if not isinstance(value, float) else value}.")
    if value < minimum:
        raise ValidationError(f"{name} must be {minimum} or more, got {value}.")
    return int(value)


def number(name: str, value: Any) -> float:
    """``value`` as a float, or "cash must be a number, got the string
    '1e6'." Range checks stay with the code that owns the value."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValidationError(f"{name} must be a number, got {describe(value)}.")
    return float(value)


def start_clock(value: Any) -> tuple[int, int, int]:
    """The ``start`` argument: ``(hour, minute, day_of_week)``."""
    try:
        hour, minute, day_of_week = value
    except (TypeError, ValueError):
        shown = (repr(tuple(value)) if isinstance(value, (tuple, list))
                 else describe(value))
        raise ValidationError(
            "start is (hour, minute, day_of_week), such as (9, 30, 3); got "
            f"{shown}.") from None
    return hour, minute, day_of_week


def macro(value: Any) -> Macro | None:
    """None or a :class:`tradefloor.Macro`. A dict is refused with the
    ``Macro`` call it was probably meant to be."""
    if value is None or isinstance(value, Macro):
        return value
    if isinstance(value, Mapping):
        fields = ", ".join(f"{key}={field!r}" for key, field in value.items())
        raise ValidationError(
            f"macro must be a tf.Macro, got a dict. Use tf.Macro({fields}).")
    raise ValidationError(f"macro must be a tf.Macro, got {describe(value)}.")


def scenario(value: Any) -> Any:
    """None, a :class:`tradefloor.Scenario`, or any object with an
    ``apply(engine, day)`` method. A scenario's name is refused with the
    call that loads it."""
    if value is None:
        return None
    if isinstance(value, str):
        raise ValidationError(
            f"scenario must be a Scenario, got the string {value!r}. Load it "
            f"first: tf.Scenario.load({value!r}).")
    if isinstance(value, type):
        raise ValidationError(
            f"scenario must be a Scenario, got the class {value.__name__} "
            "itself. Load one, such as "
            "tf.Scenario.load('liquidity_crisis'), or build one with "
            "tf.Scenario.hold(...).")
    if not callable(getattr(value, "apply", None)):
        raise ValidationError(
            f"scenario must be a Scenario, got {describe(value)}. Load one "
            "with tf.Scenario.load(name) or tf.Scenario.from_yaml(path).")
    return value


def seeds(value: Any) -> Any:
    """The ``seeds`` argument of :func:`tradefloor.rank`: an iterable of
    seeds. A single number or a string is refused."""
    if isinstance(value, (numbers.Number, str, bytes)) or value is None \
            or not hasattr(value, "__iter__"):
        example = (f" For {value} seeds use range({value})."
                   if isinstance(value, numbers.Integral)
                   and not isinstance(value, bool) and value > 0 else "")
        raise ValidationError(
            "seeds must be a list of seeds, such as range(12); got "
            f"{describe(value)}.{example}")
    return value


def agents(value: Any) -> Mapping[str, Any]:
    """The ``agents`` argument of :func:`tradefloor.evaluate`: a mapping of
    name to agent or spec."""
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (list, tuple)):
        got = f"a {type(value).__name__}; give each agent a name"
    elif callable(getattr(value, "act", None)) and not isinstance(value, type):
        got = f"one agent ({type(value).__name__}); give it a name"
    else:
        got = describe(value)
    raise ValidationError(
        "agents must be a dict of name to agent, such as "
        f"{{'mine': MyAgent()}}. Got {got}.")


def agent(who: str, value: Any, *, specs: bool = True) -> None:
    """Refuse something that is not an agent, before any market runs.

    ``who`` opens the message: "Agent 'a'" for an entry of a mapping,
    "agent=" for :class:`tradefloor.World`'s single-agent form. ``specs``
    says whether a :class:`tradefloor.StrategySpec` is accepted as it is
    (:func:`tradefloor.evaluate` builds it) or must be built first.

    What this catches used to run to the end: every ``act`` call raised,
    each step was scored as one that traded nothing, and the card read
    ``pnl=0.00`` like an agent that chose to hold cash.
    """
    from .spec import StrategySpec

    if isinstance(value, StrategySpec):
        if specs:
            return
        raise ValidationError(
            f"{who} is a StrategySpec, not an agent. Build it first: "
            "spec.build().")
    if isinstance(value, type):
        name = value.__name__
        if issubclass(value, StrategySpec):
            raise ValidationError(
                f"{who} is the class {name}, not a spec. Make one with a "
                "factory, such as "
                "StrategySpec.momentum(lookback_days=1.0, top_k=5).")
        raise ValidationError(
            f"{who} is the class {name}, not an agent. Pass {name}() "
            "instead.")
    if callable(getattr(value, "act", None)):
        return
    if callable(value):
        name = getattr(value, "__qualname__", None) or repr(value)
        if name.startswith("StrategySpec."):
            raise ValidationError(
                f"{who} is the function {name}. Call it to make a spec, such "
                "as StrategySpec.momentum(lookback_days=1.0, top_k=5).")
        raise ValidationError(
            f"{who} is the function {name}, not an agent. An agent is an "
            "object whose act(obs) returns {ticker: shares}; put the "
            "function in a class as its act method.")
    raise ValidationError(
        f"{who} has no act(obs) method. An agent is an object whose "
        "act(obs) returns {ticker: shares}.")
