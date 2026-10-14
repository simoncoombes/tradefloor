"""Contract symbols: what an agent writes to name a listed derivative.

A symbol carries the root, the expiry session and, for an option, the right
and the strike, so it names one contract for the life of a run and means
the same thing in a fork, a restore or a replay: nothing in it depends on
the order the engine listed contracts in.

- A future is ``ROOT.Fnnnn``: the expiry session after ``F``, at least four
  digits, zero-padded. ``IDX.F0273`` is the index future expiring at the
  opening print of session 273.
- An option is ``ROOT.Onnnn.Rk.cc``: the expiry after ``O``, the right
  (``C`` or ``P``) and the strike in dollars and cents.
  ``ACME.O0294.C42.50`` is the 42.50 call on ACME expiring at session 294.
  No model lists options yet (pt-v22 phase 2).

:func:`parse` reads only the canonical spelling :func:`format` writes, so
the two round-trip exactly. Both call the engine's own reader
(``derivatives::ContractSymbol`` in the crate), so the Python package and
the engine cannot disagree about what a symbol says.

Which contracts trade is the engine's to say: ``Engine.contracts()`` lists
them, ``Engine.quote(symbol)`` quotes one, and a symbol that parses here is
not thereby listed.
"""

from __future__ import annotations

from typing import Any

from . import _core

__all__ = ["FUTURE_ROOTS", "format", "is_contract", "parse"]

#: The root of each futures family pt-v22 phase 1 lists, and the switch
#: that lists it: the price index (``futures_index_listed``), the VIX
#: (``futures_vix_listed``), the policy-rate month and the term-rate quarter
#: (``futures_rates_listed``) and oil (``futures_oil_listed``).
FUTURE_ROOTS: dict[str, str] = {
    "IDX": "futures_index_listed",
    "VIX": "futures_vix_listed",
    "FF": "futures_rates_listed",
    "TR3": "futures_rates_listed",
    "OIL": "futures_oil_listed",
}


def parse(symbol: str) -> dict[str, Any]:
    """``symbol`` read as a contract symbol, a dict: ``symbol``, ``root``,
    ``expiry`` (the session it expires at, counted from 0), ``kind``
    (``"future"`` or ``"option"``), and ``right`` (``"C"`` or ``"P"``) and
    ``strike`` (dollars) for an option, None for a future.

    Raises :class:`tradefloor.ValidationError`, saying why, for anything
    that is not a symbol's canonical spelling: ``IDX.F273`` (three digits)
    and ``idx.f0273`` (lower case) are refused, not corrected.
    """
    if not isinstance(symbol, str):
        raise _core.ValidationError(
            f"a contract symbol is a string, got {type(symbol).__name__}")
    return _core.parse_contract_symbol(symbol)


def format(root: str, expiry: int, right: str | None = None,  # noqa: A001
           strike: float | None = None) -> str:
    """The canonical symbol for the future on ``root`` expiring at session
    ``expiry``, or with ``right`` (``"C"`` or ``"P"``) and ``strike``
    (dollars, a whole number of cents) for the option.

    ``format("IDX", 56)`` is ``"IDX.F0056"``. Raises
    :class:`tradefloor.ValidationError` for a root that cannot head a
    symbol, a negative expiry, or an option missing its right or strike.
    """
    if isinstance(expiry, bool) or not isinstance(expiry, int):
        raise _core.ValidationError(
            f"a contract's expiry is a whole session number, got {expiry!r}")
    return _core.format_contract_symbol(root, expiry, right, strike)


def is_contract(symbol: object) -> bool:
    """Whether ``symbol`` is a contract symbol in its canonical spelling.
    Says nothing about whether any engine lists it."""
    if not isinstance(symbol, str):
        return False
    try:
        _core.parse_contract_symbol(symbol)
    except _core.ValidationError:
        return False
    return True
