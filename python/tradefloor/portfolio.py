"""Positions, cash and P&L for one trader.

Deliberately Python rather than engine state. Position accounting is
arithmetic over IEEE-754 doubles, identical in both languages, and keeping it
out of the engine means the engine stays a *market* rather than becoming a
broker. It also lets a harness hold several portfolios against one market
without the engine knowing about any of them, which running N agents on one
seed requires.

## Execution and impact are separate channels, on purpose

:meth:`Portfolio.execute` prices a fill against the instrument's live book,
the same book the tick settles through, so slippage is real levels consumed.
That tells you what *you* paid. It tells the market nothing.

The market learns about your trading through the flow
:meth:`Portfolio.pending_flow` accumulates, handed to the next session as
``Engine.run_session(..., fills=...)``, which applies it ONCE, on the
session's first tick (or as ``order_flow`` to a single ``Engine.tick``). A
harness that executes without feeding the flow back has a trader whose fills
are realistic and whose footprint is invisible, profitable in a way no real
trader could be.

A harness that feeds it back on every tick of a step is the opposite error,
and every harness here made it until 0.8.5: ``run_session``'s old
``order_flow`` held the flow for the whole session, so one order was counted
65 times at six steps a day, after the agent had already filled at the
pre-trade book. The agent collected its own impact instead of paying it. A
buy of 1% of daily volume, sold the next step, beat a one-share control by
12 to 52 bp in 10 of the 20 names of ``Universe.random(20, seed=93001)`` that
way (design repo, ``programme/meanrev-edge-ptv19-2026-09-24.md``).
``run_session`` now refuses ``order_flow`` and names the two arguments that
replace it.

## When the book is live, the engine executes

Under a model with ``book_shared`` or ``book_resting`` on
(``Engine.book_live``), :meth:`Portfolio.execute` sends the order to the
engine's own book instead of pricing it off a snapshot. What it takes is
gone for every trader after it until the book refills, its fills can be
another trader's resting order, and the engine applies its flow to the
market itself, once, on the next tick; :meth:`pending_flow` then holds
nothing for that trade, so a harness that passes ``fills=pending_flow()``
counts it once either way. The portfolio's ``owner`` is the label its
orders carry in the book, which is how several portfolios share one.

A limit order (:meth:`submit_limit`) always goes to the engine. Its
unfilled part waits: in the book's queue with ``book_resting`` on, for the
traded range with it off. What fills later, during a session, reaches the
portfolio through :meth:`sync`.

The simulated rate indices (``UST2Y``, ``UST10Y``, ``IGCORP``) are not in
that book: they quote their own ladder whatever the dials say. An order on
one is priced off its book as before and its flow waits in
:meth:`pending_flow` for ``run_session``'s ``fills``, and a limit order on
one is refused.
"""

from __future__ import annotations

import math
import numbers
import struct
from typing import Any, Literal

from . import _core
from ._core import Engine, OrderError, ValidationError
from ._core import rate_specs as _rate_specs

#: The simulated rate indices. They quote their own ladder and are not in the
#: engine's agent-facing book, so an order on one is priced off its book and
#: its flow goes to ``run_session``'s ``fills`` whatever the book's dials say.
_RATE_TICKERS = frozenset(spec["ticker"] for spec in _rate_specs())


class LeverageError(OrderError):
    """An order refused because it would take the portfolio past
    ``max_leverage``.

    A subclass of :class:`OrderError`, so code that catches that still
    catches this. It exists so a harness can count leverage refusals apart
    from the other reasons an order is refused (``Scorecard.leverage_refusals``).
    """


def _describe(value: Any) -> str:
    """A short ``repr`` of what an agent sent, with its type."""
    text = repr(value)
    if len(text) > 80:
        text = text[:77] + "..."
    return f"{text} ({type(value).__name__})"


def shares(value: Any, *, what: str = "quantity") -> float:
    """``value`` as a signed number of shares, or a :class:`ValidationError`.

    Any finite real number passes, numpy scalars included. A ``bool`` does
    not, although Python counts it as an integer, and nor does a string.
    Before 0.8.5 ``True`` and ``"100"`` traded 1 and 100 shares, while the
    framework adapters refused ``"100"`` as not a number of shares. Zero
    passes, and the caller decides what it means. The message shows the
    value with its sign, so ``-inf`` reads as ``-inf``.
    """
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValidationError(
            f"{what} must be a number of shares, got {_describe(value)}")
    try:
        quantity = float(value)
    except (OverflowError, TypeError, ValueError):
        raise ValidationError(
            f"{what} must be a finite number of shares, got "
            f"{_describe(value)}") from None
    if not math.isfinite(quantity):
        raise ValidationError(f"{what} must be finite, got {quantity}")
    return quantity


def order_items(orders: Any) -> list[tuple[Any, Any]]:
    """The ``(ticker, order)`` pairs of what an agent's ``act()`` returned.

    ``act()`` returns a mapping of ticker to order. ``None`` and an empty
    mapping trade nothing. Anything else, a list of pairs, a string, a
    number, raises :class:`ValidationError` with what came back, and the
    harness that asked decides what that costs the agent:
    :func:`tradefloor.evaluate` writes it to the scorecard's ``errors`` and
    trades nothing that step, :class:`tradefloor.World` raises it or, under
    ``on_refusal="skip"``, records the step as unusable, and
    :func:`tradefloor.tca.analyse` raises it.

    The entries themselves are not checked here. :func:`check_order` checks
    one at a time, so one bad entry is refused and the rest still trade.
    """
    if orders is None:
        return []
    items = getattr(orders, "items", None)
    if isinstance(orders, (str, bytes)) or not callable(items):
        raise ValidationError(_shape_message(orders))
    try:
        return [(ticker, order) for ticker, order in items()]
    except Exception:                                   # noqa: BLE001
        # A mapping-like object whose items() does not give pairs. What it
        # raised is less useful to the reader than what it was.
        raise ValidationError(_shape_message(orders)) from None


def _shape_message(orders: Any) -> str:
    kind = type(orders).__name__
    return ("act() must return a mapping of ticker to order, such as "
            "{'AAA': 100} or {'AAA': tf.Limit(100, 25.0)}, or None to trade "
            f"nothing. It returned a {kind}: {_describe(orders)}")


def check_order(ticker: Any, order: Any) -> "Limit | Cancel | float | None":
    """One entry of an ``act()`` mapping, checked.

    Returns a :class:`Limit` or :class:`Cancel` as given, a market order's
    signed share count as a float, or ``None`` for an entry that trades
    nothing (``None`` or zero). Raises :class:`ValidationError`, naming the
    ticker, for a ticker that is not a string and for a value that is not
    one of those: a ``bool``, a string, a complex number, NaN or an
    infinity. Whether the ticker is listed is left to the engine, which
    refuses an unknown one with its own :class:`ValidationError`.
    """
    if not isinstance(ticker, str):
        raise ValidationError(
            f"a ticker must be a string, got {_describe(ticker)}")
    if order is None or isinstance(order, (Limit, Cancel)):
        return order
    if isinstance(order, bool) or not isinstance(order, numbers.Real):
        raise ValidationError(
            f"the order for {ticker!r} must be a number of shares, a "
            f"tf.Limit or a tf.Cancel, got {_describe(order)}")
    quantity = shares(order, what=f"the order for {ticker!r}")
    return quantity if quantity != 0 else None


class Limit:
    """A limit order, as a value in an agent's ``act()`` mapping.

    ``{"AAA": tf.Limit(500, 101.25)}`` buys up to 500 shares at 101.25 or
    better; a negative quantity sells. What does not fill at once waits
    (see :meth:`Portfolio.submit_limit`), and a new ``Limit`` for the same
    ticker from the same agent replaces the one waiting. A plain number in
    the mapping is a market order, as it always was.

    :func:`tradefloor.evaluate` and :class:`tradefloor.World` take it from
    a Python agent. :func:`tradefloor.tca.analyse` refuses it, because the
    part that waits fills inside a session, where the untraded market has
    no price to compare it with. The framework adapters in
    :mod:`tradefloor.integrations` send market orders only: an LLM's
    decision there has no order type and no limit price.
    """

    __slots__ = ("quantity", "price")

    def __init__(self, quantity: float, price: float) -> None:
        quantity = shares(quantity, what="a Limit's quantity")
        if quantity == 0:
            raise ValidationError(
                f"a Limit needs a non-zero, finite quantity, got {quantity}")
        if (isinstance(price, bool) or not isinstance(price, numbers.Real)
                or not (price > 0) or price != price
                or price == float("inf")):
            raise ValidationError(
                f"a Limit needs a finite positive price, got {price!r}")
        self.quantity = quantity
        self.price = float(price)

    def __repr__(self) -> str:
        return f"Limit({self.quantity:g}, {self.price:g})"


class Cancel:
    """Cancel every waiting order on a ticker: ``{"AAA": tf.Cancel()}``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "Cancel()"


class Position:
    """A holding in one instrument.

    ``quantity`` is signed; negative is short. Shorting is a real strategy, and
    a harness that could not express it would quietly narrow what an agent can
    be evaluated on.
    """

    __slots__ = ("ticker", "quantity", "avg_cost", "realised")

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker
        self.quantity = 0.0
        self.avg_cost = 0.0
        self.realised = 0.0

    def market_value(self, price: float) -> float:
        return self.quantity * price

    def unrealised(self, price: float) -> float:
        return (price - self.avg_cost) * self.quantity

    def __repr__(self) -> str:
        return (
            f"Position({self.ticker!r}, quantity={self.quantity:g}, "
            f"avg_cost={self.avg_cost:.4f}, realised={self.realised:.2f})"
        )


class Portfolio:
    """Cash, positions and P&L for one trader."""

    __slots__ = ("cash", "starting_cash", "positions", "_flow", "fills",
                 "max_leverage", "_stamp", "cash_interest", "interest",
                 "owner", "_in_book")

    def __init__(self, cash: float = 1_000_000.0,
                 *, max_leverage: float | None = None,
                 cash_interest: bool = False,
                 owner: str = "agent") -> None:
        """
        ``cash_interest`` makes cash earn the policy rate, one day at a time,
        when :meth:`accrue` is called; the harness calls it once a day, before
        the close. Off by default, and with it off cash earns nothing, which
        is how every run before this option behaved. See :meth:`accrue`.

        ``max_leverage`` caps gross exposure as a multiple of net worth. It
        defaults to ``None``, meaning unconstrained, because a bare simulator should
        not impose a broker's risk policy on a researcher studying, say, what
        an unconstrained strategy does.

        For an EVALUATION harness it should almost always be set. An agent that
        can trade unlimited size is being tested against nothing: the order
        book makes large trades cost
        more, but if there is no funding limit then arbitrarily large is always
        available and "trade everything" becomes a strategy. A leverage cap is
        what makes the impact constraint bite economically rather than only
        mechanically.
        """
        if cash != cash or cash <= 0:
            raise ValidationError(f"cash must be finite and positive, got {cash}")
        if max_leverage is not None and (max_leverage != max_leverage or max_leverage <= 0):
            raise ValidationError(
                f"max_leverage must be finite and positive, got {max_leverage}"
            )
        self.max_leverage = max_leverage
        self.cash_interest = bool(cash_interest)
        # Interest credited so far, net of any charged on a negative balance.
        self.interest = 0.0
        self._stamp = (0, 0, 0)
        self.cash = float(cash)
        self.starting_cash = float(cash)
        self.positions: dict[str, Position] = {}
        self._flow: dict[str, list[float]] = {}
        self.fills: list[dict] = []
        if not isinstance(owner, str) or not owner:
            raise ValidationError(
                f"owner is the label this portfolio's orders carry in the "
                f"book, a non-empty string, got {owner!r}")
        #: The label this portfolio's orders carry in the engine's book.
        self.owner = owner
        # Whether this portfolio has sent anything to the engine's book, so
        # `sync` has something to collect. A portfolio that never has asks
        # the engine nothing, and its run's order log is the one it was.
        self._in_book = False

    # -- trading ----------------------------------------------------------

    def execute(self, engine: Engine, ticker: str, quantity: float) -> dict:
        """Trade ``quantity`` shares at the price the book actually gives.

        Positive buys, negative sells. Returns the fill.

        The price comes from sweeping the live book, so a large order pays
        worse prices because it consumed levels. There is no slippage
        coefficient anywhere on this path.

        A partial fill is reported as partial rather than being completed at a
        made-up price. Filling the remainder at the last level would be
        inventing liquidity that was not there, the kind of convenience that
        makes a backtest profitable and a live strategy not.

        ``quantity`` is a finite real number, numpy scalars included. A
        string or a ``bool`` is refused with a :class:`ValidationError`
        (see :func:`shares`).
        """
        quantity = shares(quantity)
        if quantity == 0:
            raise ValidationError(
                f"quantity must be non-zero and finite, got {quantity}"
            )

        if getattr(engine, "book_live", False) and ticker not in _RATE_TICKERS:
            return self._execute_in_book(engine, ticker, float(quantity), None)

        side: Literal["buy", "sell"] = "buy" if quantity > 0 else "sell"
        size = abs(float(quantity))
        cost = engine.book(ticker).sweep_cost(side, size)
        if cost is None or cost.filled <= 0:
            raise OrderError(f"the book for {ticker!r} could not fill {size:g} shares")

        size = min(size, cost.filled)
        filled = size if side == "buy" else -size
        price = cost.average_price
        notional = filled * price

        if self.max_leverage is not None:
            # Checked BEFORE anything is mutated. A limit enforced after the
            # position moved would leave the portfolio in a state it was not
            # allowed to reach, and unwinding it correctly is harder than not
            # entering it.
            projected = self._projected_leverage(engine, ticker, filled, price, notional)
            if projected > self.max_leverage:
                raise LeverageError(
                    f"trade would take leverage to {projected:.2f}x, above the "
                    f"{self.max_leverage:.2f}x limit"
                )

        position = self.positions.setdefault(ticker, Position(ticker))
        self._apply(position, filled, price)
        self.cash -= notional

        flow = self._flow.setdefault(ticker, [0.0, 0.0])
        flow[0 if filled > 0 else 1] += size

        fill = {
            "ticker": ticker,
            "quantity": filled,
            "price": price,
            "worst_price": cost.worst_price,
            "notional": notional,
            "requested": float(quantity),
            "partial": size < abs(quantity),
            "day": self._stamp[0],
            "step": self._stamp[1],
            "tick": self._stamp[2],
        }
        self.fills.append(fill)
        return fill

    def submit_limit(self, engine: Engine, ticker: str, quantity: float,
                     price: float) -> dict:
        """Send a limit order to the engine's book. Returns the engine's report.

        What the book holds at ``price`` or better fills at once and is
        applied here. The rest waits: in the book's queue, behind the depth
        already at its price, with ``book_resting`` on; outside it, to fill
        in full at ``price`` when a print reaches it, with it off. Either
        way its later fills reach this portfolio through :meth:`sync`.

        The leverage limit is checked against the whole order filling at
        its limit, before anything is sent, because a resting order that
        fills during a session cannot be refused then.

        The part that fills at once is recorded in :attr:`fills` as one
        fill, the way :meth:`execute` records a market order, with the
        engine's ``order_id``, ``liquidity="taker"`` and ``limit=True``. The
        part that waits is recorded fill by fill as :meth:`sync` collects
        it. So an agent's own ``obs.portfolio.fills``, :meth:`fills_table`
        and the scorecard's impact see every share a limit order traded.
        """
        quantity = shares(quantity)
        if quantity == 0:
            raise ValidationError(
                f"quantity must be non-zero and finite, got {quantity}")
        if not (price > 0) or price != price:
            raise ValidationError(f"price must be finite and positive, got {price}")
        if ticker in _RATE_TICKERS:
            raise ValidationError(
                f"{ticker} is a simulated rate index, which the engine's book "
                "does not hold: a limit order cannot wait on it. Trade it with "
                "execute().")
        return self._execute_in_book(engine, ticker, float(quantity), float(price),
                                     report=True)

    def cancel(self, engine: Engine, *, ticker: str | None = None,
               order_id: str | None = None) -> int:
        """Cancel this portfolio's waiting orders: one by id, every one on a
        ticker, or all of them. Returns how many were cancelled."""
        count = 0
        for order in engine.open_orders(self.owner):
            if order_id is not None and order["order_id"] != order_id:
                continue
            if ticker is not None and order["ticker"] != ticker:
                continue
            count += bool(engine.cancel(order["order_id"], agent=self.owner))
        return count

    def open_orders(self, engine: Engine) -> list[dict]:
        """This portfolio's waiting orders, in arrival order."""
        return engine.open_orders(self.owner)

    def sync(self, engine: Engine) -> list[dict]:
        """Apply every fill the engine holds for this portfolio.

        A resting or waiting order fills during a session, when the
        portfolio is not being called, so the engine keeps the fills until
        they are collected. A harness calls this after each session. It is
        a no-op, and asks the engine nothing, for a portfolio that has
        never sent an order to the book. Returns the fills applied.
        """
        if not self._in_book:
            return []
        return self._drain(engine)

    def _drain(self, engine: Engine, skip_order: str | None = None) -> list[dict]:
        """Collect this portfolio's fills and apply them.

        Each is recorded in :attr:`fills` as it happened, except those of
        ``skip_order``, the order :meth:`execute` is reporting as one fill.
        """
        taken = engine.take_fills(self.owner)
        for f in taken:
            signed = f["quantity"] if f["side"] == "buy" else -f["quantity"]
            position = self.positions.setdefault(f["ticker"], Position(f["ticker"]))
            self._apply(position, signed, f["price"])
            self.cash -= signed * f["price"]
            if f["order_id"] == skip_order and f["liquidity"] == "taker":
                continue
            self.fills.append({
                "ticker": f["ticker"],
                "quantity": signed,
                "price": f["price"],
                "worst_price": f["price"],
                "notional": signed * f["price"],
                "requested": signed,
                "partial": False,
                "day": int(f["day"]),
                "step": self._stamp[1],
                "tick": int(f["tick"]),
                "order_id": f["order_id"],
                "liquidity": f["liquidity"],
                "counterparty": f["counterparty"],
            })
        return taken

    def _execute_in_book(self, engine: Engine, ticker: str, quantity: float,
                         limit: float | None, report: bool = False) -> dict:
        """:meth:`execute` and :meth:`submit_limit` against the engine's book.

        Priced first off the book as it stands, read-only, so an order the
        book cannot fill at all, or that would break the leverage limit, is
        refused before anything is sent: the same order of checks the
        snapshot path makes.
        """
        side: Literal["buy", "sell"] = "buy" if quantity > 0 else "sell"
        size = abs(quantity)
        if limit is None:
            cost = engine.book(ticker).sweep_cost(side, size)
            if cost is None or cost.filled <= 0:
                raise OrderError(
                    f"the book for {ticker!r} could not fill {size:g} shares")
            price = cost.average_price
            expected = min(size, cost.filled)
        else:
            # Checked as though it all filled at its limit: a resting order
            # that fills during a session cannot be refused then.
            price = limit
            expected = size
        if self.max_leverage is not None:
            filled = expected if side == "buy" else -expected
            projected = self._projected_leverage(engine, ticker, filled, price,
                                                 filled * price)
            if projected > self.max_leverage:
                raise LeverageError(
                    f"trade would take leverage to {projected:.2f}x, above the "
                    f"{self.max_leverage:.2f}x limit"
                )
        out = engine.submit(self.owner, ticker, quantity, limit_price=limit)
        self._in_book = True
        self._drain(engine, skip_order=out["order_id"])
        if report:
            if out["filled"] > 0:
                # The part of a limit order that filled at once, as one
                # fill. `_drain` skipped these taker fills so they would not
                # be counted twice, and for a market order the block below
                # records them; a limit order returned here before it did.
                signed = out["filled"] if side == "buy" else -out["filled"]
                self.fills.append({
                    "ticker": ticker,
                    "quantity": signed,
                    "price": out["average_price"],
                    "worst_price": out["worst_price"],
                    "notional": sum(
                        (f["quantity"] if side == "buy" else -f["quantity"])
                        * f["price"] for f in out["fills"]),
                    "requested": quantity,
                    "partial": out["filled"] < size,
                    "day": self._stamp[0],
                    "step": self._stamp[1],
                    "tick": self._stamp[2],
                    "order_id": out["order_id"],
                    "liquidity": "taker",
                    "limit": True,
                })
            return out
        if out["filled"] <= 0:
            # The preview filled and the book did not: the only difference
            # between them is this portfolio's own resting orders, which an
            # order never trades against.
            raise OrderError(
                f"the book for {ticker!r} could not fill {size:g} shares "
                f"against anyone but this portfolio's own orders")
        filled = out["filled"] if side == "buy" else -out["filled"]
        fill = {
            "ticker": ticker,
            "quantity": filled,
            "price": out["average_price"],
            "worst_price": out["worst_price"],
            "notional": sum((f["quantity"] if side == "buy" else -f["quantity"])
                            * f["price"] for f in out["fills"]),
            "requested": quantity,
            "partial": out["filled"] < size,
            "day": self._stamp[0],
            "step": self._stamp[1],
            "tick": self._stamp[2],
        }
        self.fills.append(fill)
        return fill

    @staticmethod
    def _apply(position: Position, filled: float, price: float) -> None:
        """Update a position, realising P&L only on the part that closes.

        Average-cost basis. The branch that matters is a trade crossing through
        zero: selling more than you hold flips you short, and only the part
        that actually closed realises P&L. Booking the whole trade as a close
        would report profit on shares that were never held, and it would look
        plausible, because the number would still be finite and the direction
        still right.
        """
        existing = position.quantity

        if existing == 0 or (existing > 0) == (filled > 0):
            total = existing + filled
            if total != 0:
                position.avg_cost = (
                    position.avg_cost * existing + price * filled
                ) / total
            position.quantity = total
            return

        closing = min(abs(filled), abs(existing))
        direction = 1.0 if existing > 0 else -1.0
        position.realised += (price - position.avg_cost) * closing * direction

        remaining = existing + filled
        position.quantity = remaining
        if remaining == 0:
            position.avg_cost = 0.0
        elif (remaining > 0) != (existing > 0):
            # Crossed through zero: the new side begins at this price, not at
            # a cost basis inherited from the side that just closed.
            position.avg_cost = price

    def _projected_leverage(self, engine: Engine, ticker: str, filled: float,
                            price: float, notional: float) -> float:
        """Leverage this trade would produce, without performing it."""
        prices = self.marks(engine)
        gross = 0.0
        for held in self.positions.values():
            quantity = held.quantity + (filled if held.ticker == ticker else 0.0)
            gross += abs(quantity) * prices.get(held.ticker, held.avg_cost)
        if ticker not in self.positions:
            gross += abs(filled) * price
        equity = (self.cash - notional) + sum(
            (held.quantity + (filled if held.ticker == ticker else 0.0))
            * prices.get(held.ticker, held.avg_cost)
            for held in self.positions.values()
        )
        if ticker not in self.positions:
            equity += filled * price
        # Non-positive equity means insolvent, not "infinitely levered".
        # Reporting infinity makes the comparison behave and the message read
        # correctly rather than dividing by zero.
        return float("inf") if equity <= 0 else gross / equity

    def gross_exposure(self, engine: Engine) -> float:
        """Absolute market value across every position, longs and shorts alike.

        Absolute because a long and a short of equal size are two positions
        with two risks, not a flat book. Netting them would report a hedged
        trader and a reckless one as identical.
        """
        prices = self.marks(engine)
        return sum(
            abs(p.quantity) * prices[p.ticker]
            for p in self.positions.values()
            if p.ticker in prices
        )

    def leverage(self, engine: Engine) -> float:
        """Gross exposure as a multiple of net worth."""
        equity = self.net_worth(engine)
        return float("inf") if equity <= 0 else self.gross_exposure(engine) / equity

    # -- valuation --------------------------------------------------------

    def marks(self, engine: Engine) -> dict[str, float]:
        """Current price per ticker, from the engine."""
        values = struct.unpack("<%dd" % len(engine.tickers), engine.prices())
        return dict(zip(engine.tickers, values))

    def market_value(self, engine: Engine) -> float:
        prices = self.marks(engine)
        return sum(
            p.market_value(prices[p.ticker])
            for p in self.positions.values()
            if p.ticker in prices
        )

    def net_worth(self, engine: Engine) -> float:
        """Cash plus the marked value of every position."""
        return self.cash + self.market_value(engine)

    def pnl(self, engine: Engine) -> float:
        """Total profit and loss against starting cash."""
        return self.net_worth(engine) - self.starting_cash

    def unrealised(self, engine: Engine) -> float:
        prices = self.marks(engine)
        return sum(
            p.unrealised(prices[p.ticker])
            for p in self.positions.values()
            if p.ticker in prices
        )

    def realised(self) -> float:
        return sum(p.realised for p in self.positions.values())

    # -- cash -------------------------------------------------------------

    def accrue(self, engine: Engine) -> float:
        """Credit one trading day's interest on cash, if ``cash_interest``.

        ``cash * policy_rate / 252``, at the policy rate in force now
        (``engine.macro_fields["federal_funds_rate"]``), added to cash and to
        :attr:`interest`. Returns the amount, 0.0 with the option off.

        A negative balance, which is borrowing to hold more than the account
        is worth, is charged at the same rate. That is cheaper than any broker
        lends, so a levered strategy's financing cost is a floor here, not an
        estimate.

        Call it once per trading day. The harness calls it just before the
        close, so the day's interest is at the rate the day traded under and
        the close's macro step, which may move the rate, applies to the next
        day. Before this option existed cash earned nothing: a portfolio
        holding cash through a rate shock gained nothing from the higher
        rate, and a 60/40 portfolio's bond sleeve was compared against cash
        that paid zero.
        """
        if not self.cash_interest:
            return 0.0
        rate = engine.macro_fields["federal_funds_rate"]
        amount = self.cash * rate / 252.0
        self.cash += amount
        self.interest += amount
        return amount

    # -- impact -----------------------------------------------------------

    def pending_flow(self) -> dict[str, tuple[float, float]]:
        """Order flow accumulated since the last :meth:`clear_flow`.

        Feed this to the next ``run_session`` as ``fills``, or to the next
        single ``tick`` as ``order_flow``, so the market feels the trading
        once, on the minute after the fills. Then call :meth:`clear_flow`:
        flow left in place is sent again with the next step's.
        """
        return {
            ticker: (buy, sell)
            for ticker, (buy, sell) in self._flow.items()
            if buy > 0 or sell > 0
        }

    def clear_flow(self) -> None:
        """Forget accumulated flow, once it has been applied to a tick."""
        self._flow.clear()

    def fills_table(self, tickers: list[str]):
        """The fill log as an Arrow stream, joinable to the tape.

        ``tickers`` is the roster, so fills carry an ``instrument_id`` index
        rather than a repeated string -- and so this table joins to ``bars``
        and ``truth`` on that key rather than on text.

        The join is the point: ``bars`` says where the price was, this says
        where you were filled, and the gap between them is your execution
        quality. Neither table can answer that alone.

        # Why there is a ``tick`` column as well as a ``step``

        This docstring used to claim the join and only half-deliver it.
        ``bars``, ``truth`` and ``book`` are keyed on a WITHIN-DAY tick;
        ``step`` is a GLOBAL counter, so a fill at ``day=1, step=6`` under
        four steps a day looks wrong and joins to nothing. Recovering the tick
        needed ``steps_per_day`` and ``ticks_per_step``, which appear in no
        table -- so the join was possible only for someone who still had the
        call that produced the data.

        ``tick`` is the number of ticks already run that day when the order
        crossed. Agents act at the START of a step and the session runs
        afterwards, so a fill at within-day step ``k`` carries
        ``k * ticks_per_step``: the index of the next tick to run. Joining on
        ``(day, tick, instrument_id)`` therefore lines a fill up with the bar
        it immediately preceded, which is the bar its impact shows up in.
        """
        index = {t: i for i, t in enumerate(tickers)}
        rows = [f for f in self.fills if f["ticker"] in index]
        return _core.fills_stream(
            [int(f.get("day", 0)) for f in rows],
            [int(f.get("step", 0)) for f in rows],
            [int(f.get("tick", 0)) for f in rows],
            [index[f["ticker"]] for f in rows],
            [f["quantity"] for f in rows],
            [f["price"] for f in rows],
            [f["worst_price"] for f in rows],
            [f["notional"] for f in rows],
        )

    def stamp(self, day: int, step: int, tick: int) -> None:
        """Tag subsequent fills with a day, a global step and a within-day tick.

        Called by the harness between steps. Without it every fill would sit
        at day zero and the fills table could not be joined to anything on
        time, which is most of what it is for.

        ``tick`` is required rather than defaulted. Defaulting it would put
        every fill at tick zero -- a table that joins cleanly, to the wrong
        bar, with nothing to indicate it. That is worse than a TypeError.
        See :meth:`fills_table` for what the value means.
        """
        self._stamp = (int(day), int(step), int(tick))

    def __repr__(self) -> str:
        held = {t: round(p.quantity, 2) for t, p in self.positions.items() if p.quantity}
        return f"Portfolio(cash={self.cash:,.2f}, positions={held})"
