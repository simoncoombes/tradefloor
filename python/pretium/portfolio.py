"""Positions, cash and P&L for one trader.

Deliberately Python rather than engine state. Position accounting is
arithmetic over IEEE-754 doubles, identical in both languages, and keeping it
out of the engine means the engine stays a *market* rather than becoming a
broker. It also lets a harness hold several portfolios against one market
without the engine knowing about any of them, which is what running N agents
on one seed requires.

## Execution and impact are separate channels, on purpose

:meth:`Portfolio.execute` prices a fill against the instrument's live book,
the same book the tick settles through, so slippage is real levels consumed.
That tells you what *you* paid. It tells the market nothing.

The market learns about your trading through ``order_flow`` on the next tick,
which is what :meth:`Portfolio.pending_flow` accumulates. A harness that
executes without feeding the flow back has a trader whose fills are realistic
and whose footprint is invisible, profitable in a way no real trader could be.
"""

from __future__ import annotations

import struct
from typing import Literal

from . import _core
from ._core import Engine, OrderError, ValidationError


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
                 "max_leverage", "_stamp")

    def __init__(self, cash: float = 1_000_000.0,
                 *, max_leverage: float | None = None) -> None:
        """
        ``max_leverage`` caps gross exposure as a multiple of net worth. It
        defaults to ``None``, meaning unconstrained, because a bare simulator should
        not impose a broker's risk policy on a researcher studying, say, what
        an unconstrained strategy does.

        For an EVALUATION harness it should almost always be set. An agent that
        can trade unlimited size is not being tested against the market, it is
        being tested against nothing: the order book makes large trades cost
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
        self._stamp = (0, 0, 0)
        self.cash = float(cash)
        self.starting_cash = float(cash)
        self.positions: dict[str, Position] = {}
        self._flow: dict[str, list[float]] = {}
        self.fills: list[dict] = []

    # -- trading ----------------------------------------------------------

    def execute(self, engine: Engine, ticker: str, quantity: float) -> dict:
        """Trade ``quantity`` shares at the price the book actually gives.

        Positive buys, negative sells. Returns the fill.

        The price comes from sweeping the live book, so a large order pays
        worse prices because it consumed levels. There is no slippage
        coefficient anywhere on this path.

        A partial fill is reported as partial rather than being completed at a
        made-up price. Filling the remainder at the last level would be
        inventing liquidity that was not there, which is exactly the kind of
        convenience that makes a backtest profitable and a live strategy not.
        """
        if quantity != quantity or quantity == 0:
            raise ValidationError(
                f"quantity must be non-zero and finite, got {quantity}"
            )

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
                raise OrderError(
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
        # Non-positive equity is not "infinitely levered", it is insolvent.
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

    # -- impact -----------------------------------------------------------

    def pending_flow(self) -> dict[str, tuple[float, float]]:
        """Order flow accumulated since the last :meth:`clear_flow`.

        Feed this to the next ``tick`` or ``run_session`` as ``order_flow`` so
        the market feels the trading.
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
