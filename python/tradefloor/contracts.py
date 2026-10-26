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
  ``IDX.O0035.C1050.00`` is the 1050 call on the price index settling at
  session 35, which a model with ``options_index_listed`` lists;
  ``ACME.O0294.C42.50`` is the 42.50 call on ACME, an American option
  settling in shares at session 294's close, which a model with
  ``options_names_listed`` lists on every name.

:func:`parse` reads only the canonical spelling :func:`format` writes, so
the two round-trip exactly. Both call the engine's own reader
(``derivatives::ContractSymbol`` in the crate), so the Python package and
the engine cannot disagree about what a symbol says.

Which contracts trade is the engine's to say: ``Engine.contracts()`` lists
them, ``Engine.quote(symbol)`` quotes one, and a symbol that parses here is
not thereby listed.

:func:`explain_move` says why a contract's price moved between two of its
quotes: for a future, how much came from the underlying, from the carry its
fair value holds and from the basis to that fair value; for a rate future,
from the expected policy rate and the premium; for an option, from the
underlying through its delta and gamma, from its implied volatility through
its vega, from time through its theta, and what is left.
"""

from __future__ import annotations

from typing import Any

from . import _core

__all__ = ["FUTURE_ROOTS", "NAME_OPTIONS", "OPTION_ROOTS", "explain_move", "format",
           "is_contract", "parse"]

#: The root of each futures family the engine can list, and the switch
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

#: The underlying of each options family the engine can list, and the
#: switch that lists it: European options on the price index
#: (``options_index_listed``). American options on the roster's names are
#: listed under ``NAME_OPTIONS``, each headed by its name's ticker.
OPTION_ROOTS: dict[str, str] = {
    "IDX": "options_index_listed",
}

#: The switch that lists American options on every name,
#: each symbol headed by the name's ticker: ``Engine.name_option_roots()``
#: lists the names that have them.
NAME_OPTIONS = "options_names_listed"


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


#: Rate futures: priced as 100 less the expected rate and its premium.
_RATE_ROOTS = frozenset({"FF", "TR3"})


def _field(quote: Any, name: str, symbol: str) -> float:
    value = quote.get(name) if isinstance(quote, dict) else None
    if value is None:
        raise _core.ValidationError(
            f"the quotes of {symbol} carry no {name!r}; pass two quotes "
            "from Engine.quote, read while the contract was listed")
    return float(value)


def explain_move(before: dict[str, Any], after: dict[str, Any],
                 quantity: float = 1.0) -> dict[str, Any]:
    """Why a contract's price moved from ``before`` to ``after``, two of its
    quotes from :meth:`Engine.quote` (the last close's and now, say), as a
    dict: ``symbol``, ``kind``, ``move`` (the price's change), ``parts``
    (name to its share of the move, in price points, summing to ``move``
    exactly), ``dollars`` (each part times the multiplier and ``quantity``
    contracts, signed, for a position's P&L) and ``sessions`` (the sessions
    between the two quotes).

    A future's parts, from its carry fair value ``FV = (S - PV(D)) e^(r tau)``:

    - ``underlying``: the index's move carried to expiry, ``(S1 - S0) FV0/S0``;
    - ``carry``: the rest of the fair value's change (the financing rate,
      the dividends to expiry, time passing);
    - ``basis``: the change in the price's distance from its fair value.

    A rate future (``FF``, ``TR3``), priced 100 less the expected rate and
    its premium:

    - ``expected_rate``: minus the change in the policy rate the forecast
      expects over its period, in rate points;
    - ``premium``: minus the change in the premium over that forecast;
    - ``basis``: the change in the price's distance from that fair value.

    An option's parts, from the Greeks of ``before`` and its dealer's mid:

    - ``underlying``: ``delta dS + gamma dS^2 / 2``;
    - ``volatility``: ``vega d(iv)``;
    - ``time``: ``theta`` times the sessions passed;
    - ``other``: what those leave, the spread, the carry and higher-order
      terms.

    Reads the two quotes only, so it works on quotes kept from an earlier
    day and changes nothing in any engine. Raises
    :class:`tradefloor.ValidationError` for quotes of two different
    contracts, or ones missing a field the split needs.
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        raise _core.ValidationError("explain_move takes two quotes from Engine.quote")
    symbol = before.get("symbol")
    if symbol is None or symbol != after.get("symbol"):
        raise _core.ValidationError(
            f"explain_move compares two quotes of one contract, got "
            f"{before.get('symbol')!r} and {after.get('symbol')!r}")
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) \
            or quantity != quantity:
        raise _core.ValidationError(f"quantity is a number of contracts, got {quantity!r}")
    info = parse(symbol)
    multiplier = _field(after, "multiplier", symbol)
    sessions = (_field(before, "sessions_to_expiry", symbol)
                - _field(after, "sessions_to_expiry", symbol))
    parts: dict[str, float]
    if info["kind"] == "option":
        p0, p1 = _field(before, "mid", symbol), _field(after, "mid", symbol)
        ds = _field(after, "index", symbol) - _field(before, "index", symbol)
        underlying = (_field(before, "delta", symbol) * ds
                      + 0.5 * _field(before, "gamma", symbol) * ds * ds)
        volatility = _field(before, "vega", symbol) * (
            _field(after, "iv", symbol) - _field(before, "iv", symbol))
        time = _field(before, "theta", symbol) * sessions
        move = p1 - p0
        parts = {"underlying": underlying, "volatility": volatility, "time": time,
                 "other": move - underlying - volatility - time}
    else:
        p0, p1 = _field(before, "price", symbol), _field(after, "price", symbol)
        f0, f1 = _field(before, "fair", symbol), _field(after, "fair", symbol)
        move = p1 - p0
        basis = (p1 - f1) - (p0 - f0)
        if info["root"] in _RATE_ROOTS and before.get("expected") is not None:
            expected = -(_field(after, "expected", symbol) - _field(before, "expected", symbol))
            premium = -(_field(after, "premium", symbol) - _field(before, "premium", symbol))
            # Whatever the fair value did beyond the two (none, as quoted).
            rest = (f1 - f0) - expected - premium
            parts = {"expected_rate": expected, "premium": premium + rest, "basis": basis}
        else:
            s0, s1 = _field(before, "index", symbol), _field(after, "index", symbol)
            underlying = (s1 - s0) * (f0 / s0) if s0 else 0.0
            parts = {"underlying": underlying, "carry": (f1 - f0) - underlying,
                     "basis": basis}
    # The parts sum to the move to the last bit: the largest absorbs rounding.
    gap = move - sum(parts.values())
    if gap:
        biggest = max(parts, key=lambda k: abs(parts[k]))
        parts[biggest] += gap
    scale = multiplier * float(quantity)
    return {"symbol": symbol, "kind": info["kind"], "move": move, "parts": parts,
            "dollars": {k: v * scale for k, v in parts.items()}, "sessions": sessions}
