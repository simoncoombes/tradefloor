"""Shared helper, kept out of ``__init__`` to avoid an import cycle.

``gym`` needs to normalise a universe, and ``__init__`` imports ``gym``, so
``gym`` cannot import ``__init__``.
"""

from __future__ import annotations

from typing import Sequence

from ._core import Instrument


def as_universe(universe: Sequence[Instrument]) -> list[Instrument]:
    """Normalise to a list, preserving order.

    Order is contractual, because the engine iterates instruments in index
    order and draws as it goes, so this copies rather than sorting, and refuses an empty
    roster rather than producing an environment with nothing to trade.

    A wrong type is refused in the words ``Engine`` uses for it (see
    :func:`universe_refusal`), since callers such as :func:`tradefloor.evaluate`
    read the roster before any engine does.
    """
    from ._core import ValidationError

    if isinstance(universe, (str, bytes)) or not _iterable(universe):
        raise ValidationError(universe_refusal(universe))
    items = list(universe)
    if any(not isinstance(item, Instrument) for item in items):
        raise ValidationError(universe_refusal(items))
    if not items:
        raise ValidationError("universe is empty")
    return items


def _iterable(value: object) -> bool:
    try:
        iter(value)  # type: ignore[call-overload]
    except TypeError:
        return False
    return True


def universe_refusal(value: object) -> str:
    """Why ``value`` is not a universe, and what to pass instead.

    The same words as the engine's refusal in ``rust/src/python_engine.rs``
    (``universe_from``). Before 0.8.5 a wrong type surfaced as Python's
    internals: "'NoneType' object is not iterable", "'str' object has no
    attribute 'ticker'".
    """
    head = ("universe must be a list of instruments, such as "
            "tf.Universe.random(40, seed=1). ")
    if value is None:
        return head + "Got None."
    if isinstance(value, bool):
        return head + f"Got {value}."
    if isinstance(value, int):
        return (head + f"Got the number {value}. For {value} random companies "
                f"use tf.Universe.random({value}, seed=1).")
    if isinstance(value, str):
        return (head + f"Got the string {value!r}. Tickers alone are not "
                "enough: the simulator needs each company's price, shares "
                "and sector.")
    kind = type(value).__name__
    if not _iterable(value):
        return head + f"Got a {kind}."
    items = list(value)  # type: ignore[call-overload]
    if items and all(isinstance(item, str) for item in items):
        return (head + "Got a list of ticker strings; the simulator needs each "
                "company's price, shares and sector, not only its name. "
                "Universe.from_edgar builds instruments for real companies.")
    for at, item in enumerate(items):
        if not isinstance(item, Instrument):
            return (head + f"Got a {kind} holding a {type(item).__name__} "
                    f"at position {at}.")
    return head + f"Got a {kind}."


def fingerprint_of(universe: Sequence[Instrument]) -> str:
    """The roster's content hash, from anything list-shaped.

    Lives here rather than being computed at each call site so the three
    result types stamp the SAME value from the same definition. Two places
    computing "the universe's identity" slightly differently would be worse
    than one place computing it wrongly, because only one of those is
    findable.
    """
    from . import Universe

    return Universe(as_universe(universe)).fingerprint
