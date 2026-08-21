"""Shared helper, kept out of ``__init__`` to avoid an import cycle.

``gym`` needs to normalise a universe, and ``__init__`` imports ``gym``, so
``gym`` cannot import ``__init__``.
"""

from __future__ import annotations

from typing import Sequence

from ._core import Instrument


def as_universe(universe: Sequence[Instrument]) -> list[Instrument]:
    """Normalise to a list, preserving order.

    Order is contractual — the engine iterates instruments in index order and
    draws as it goes — so this copies rather than sorting, and refuses an empty
    roster rather than producing an environment with nothing to trade.
    """
    items = list(universe)
    if not items:
        from ._core import ValidationError

        raise ValidationError("universe is empty")
    return items


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
