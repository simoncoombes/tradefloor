"""What agent code is handed, and how the harness notices when it misbehaves.

Until 0.8.5 every harness put the live :class:`~tradefloor.Engine` on the
observation as ``obs.engine``. An agent could fork it and run the fork one
step ahead, which is look-ahead: measured by an independent audit, +11.4% in
five days on four seeds of four. Or it could call ``set_fundamentals`` on a
name it held, which rewrites the market it is scored on: +184%. Neither
scorecard carried an error or a flag.

## The default: a read-only market view

``obs.engine`` is now a :class:`MarketView`. It answers what a trader can see
and nothing else:

- prices, the columns a data vendor publishes (:data:`PUBLIC_COLUMNS`), each
  instrument's book and the bars of the sessions already run
- the published macro fields and the curve (everything but ``qe_pe_boost``,
  a model coefficient no exchange publishes)
- which names and sectors have news today, without the size or direction of
  the move, because the engine's own field is the move the story will have
  made by the close
- the agent's own portfolio, through :class:`PortfolioView`, which reads and
  cannot trade or write

Anything else raises :class:`SandboxError`, which names the opt-in. That
covers ``fork``, every ``set_*``, ``pin_macro``, ``tick``, ``run_session``,
``truth``, ``attribution``, ``state_snapshot`` (it carries the generator
state, so a copy restored elsewhere runs the future) and the hidden columns
``mispricing_s``, ``mispricing_momentum``, ``maker_inventory`` and
``garch_variance``.

## Hidden state is a declared capability

An agent that sets ``privileged = True`` (the Oracle does, and a
:class:`~tradefloor.StrategySpec` with an ``oracle`` signal does) is also
handed ``obs.hidden``, a :class:`HiddenState`: every column, the factor
attribution, the model's dials and the economy, all read-only. Its scorecard
says ``uses_hidden_state=True``. It still gets no fork and no writes.

## The opt-in: ``trusted_agents=True``

For research that needs the live engine, :func:`tradefloor.evaluate`,
:func:`tradefloor.rank`, :class:`tradefloor.World` and
:func:`tradefloor.tca.analyse` take ``trusted_agents=True``, which hands
every agent the live engine and the live portfolio exactly as before 0.8.5.
The scorecard says ``trusted=True``, a ranking marks the row and a World's
manifest records it.

## Tampering is detected either way

Before and after every call into agent code (``act``, and ``explain`` where
the harness asks for one) the harness takes the engine's
:meth:`~tradefloor.Engine.state_hash`, its fundamentals and recording
counters, and each portfolio's cash, positions and fills. Any difference is
a change made by the agent, and the scorecard says ``tampered=True`` with an
error line naming the step. :func:`tradefloor.rank` leaves a tampered agent
out of its table and says so.

## What this is not

It is not a security boundary. The agent runs in the harness's own Python
process, and a determined author can walk the interpreter (``gc``, frame
objects, a closure's cells) to the engine, or build a second engine from a
guessed seed and run it ahead. The view closes the route the harness itself
handed over, and the hash check catches any write, however it was reached;
a second engine built from scratch writes nothing and is not caught. For
code you do not trust, run it out of process against the MCP server, where
strategies are data and there is no Python to submit.
"""

from __future__ import annotations

from typing import Any, Iterable

from ._core import Engine

#: Columns a trader can buy from a data vendor. The rest of
#: ``Engine.column``'s fields are the simulator's own state: the mispricing
#: and its momentum (the answer the agent is meant to infer), the market
#: maker's inventory and the GARCH variance.
PUBLIC_COLUMNS = frozenset({
    "price", "previous_close", "previous_tick_price", "open", "high", "low",
    "volume", "avg_volume", "market_cap", "last_daily_return", "beta",
    "short_interest", "float_shares",
})

#: Macro fields withheld from the view. ``qe_pe_boost`` is the P/E the model
#: grants for quantitative easing: a coefficient, not a statistic anybody
#: publishes. The integrations settled this first
#: (``integrations.common.OBSERVABLE_MACRO``).
WITHHELD_MACRO = frozenset({"qe_pe_boost"})

_OPT_IN = ("Agents see a read-only market view. An agent that needs hidden "
           "state declares `privileged = True` and reads `obs.hidden`; "
           "research that needs the live engine passes `trusted_agents=True`, "
           "which the scorecard records. See `tradefloor.sandbox`.")


class SandboxError(AttributeError):
    """An agent reached for something the observation does not carry.

    An :class:`AttributeError`, so ``hasattr`` and ``getattr(x, name, None)``
    answer "not here" as they would for any missing attribute. Raised from
    inside ``act`` it is recorded on the scorecard like any other exception.
    """


def _refuse(what: str, name: str) -> SandboxError:
    return SandboxError(f"{what} has no {name!r}. {_OPT_IN}")


class PublishedMacro:
    """The macro state as published: ``Macro``'s fields less the withheld.

    Read off the engine's own ``macro_state`` at the moment of the call, so
    every value is the float the engine holds and a serialiser that quotes it
    writes the bytes it wrote before this class existed.
    """

    __slots__ = ("_values",)

    _FIELDS = ("vix", "federal_funds_rate", "corporate_bond_yield",
               "inflation_rate", "qe_assets_ratio", "fear_greed_index",
               "cycle")

    def __init__(self, macro: Any) -> None:
        object.__setattr__(self, "_values",
                           {f: getattr(macro, f) for f in self._FIELDS})

    def __getattr__(self, name: str) -> Any:
        try:
            return self._values[name]
        except KeyError:
            raise _refuse("the published macro state", name) from None

    def __setattr__(self, name: str, value: Any) -> None:
        raise SandboxError("the published macro state is read-only")

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)

    def __repr__(self) -> str:
        v = self._values
        return (f"PublishedMacro(vix={v['vix']:g}, "
                f"federal_funds_rate={v['federal_funds_rate']:g}, "
                f"cycle={v['cycle']!r})")


class MarketView:
    """What a trader can see of the market. The default ``obs.engine``.

    Every method reads the live engine at the moment it is called, so the
    view is always the present and never the future. See the module
    docstring for what is here and what is not.
    """

    # Name-mangled so `view._engine` is not the obvious spelling. This is a
    # guard against accident, not a wall: see the module docstring.
    __slots__ = ("__engine",)

    def __init__(self, engine: Engine) -> None:
        object.__setattr__(self, "_MarketView__engine", engine)

    # -- the roster -------------------------------------------------------

    @property
    def tickers(self) -> list[str]:
        return list(self.__engine.tickers)

    def __len__(self) -> int:
        return len(self.__engine)

    def index_of(self, ticker: str) -> int | None:
        return self.__engine.index_of(ticker)

    # -- prices and depth -------------------------------------------------

    def prices(self) -> bytes:
        """Every instrument's last print, as little-endian f64 bytes."""
        return self.__engine.prices()

    def column(self, field: str) -> bytes:
        """One of :data:`PUBLIC_COLUMNS`, as little-endian f64 bytes."""
        if field not in PUBLIC_COLUMNS:
            raise SandboxError(
                f"column {field!r} is simulator state, not market data. "
                f"The market view serves {sorted(PUBLIC_COLUMNS)}. {_OPT_IN}")
        return self.__engine.column(field)

    def book(self, ticker: str):
        """A copy of the instrument's order book. Trading on it changes
        nothing: orders go through the ``act`` mapping."""
        return self.__engine.book(ticker)

    def bars(self, **kwargs: Any):
        """``Engine.bars``: prints and volume of sessions already run."""
        return self.__engine.bars(**kwargs)

    # -- the economy ------------------------------------------------------

    @property
    def macro_state(self) -> PublishedMacro:
        return PublishedMacro(self.__engine.macro_state)

    @property
    def macro_fields(self) -> dict[str, Any]:
        return {k: v for k, v in self.__engine.macro_fields.items()
                if k not in WITHHELD_MACRO}

    def curve(self) -> dict[str, float]:
        return dict(self.__engine.curve())

    def rate_instruments(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self.__engine.rate_instruments()]

    def news(self) -> list[dict[str, Any]]:
        """Today's company and sector news: who, not how much.

        ``Engine.session_news`` carries ``price_impact``, the whole move the
        story adds by the close, which is the answer key. A headline tells a
        trader that there is news on a name; this says that and no more.
        """
        return [{"ticker": e.get("ticker"), "sector": e.get("sector"),
                 "day": e.get("day")}
                for e in self.__engine.session_news()]

    # -- identity and the clock -------------------------------------------

    @property
    def model_fingerprint(self) -> str:
        return self.__engine.model_fingerprint

    @property
    def session_tick(self) -> int | None:
        return self.__engine.session_tick

    # -- everything else --------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        raise _refuse("the market view", name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise SandboxError("the market view is read-only")

    def __repr__(self) -> str:
        return f"MarketView({len(self)} instruments)"


class HiddenState(MarketView):
    """Read-only access to the simulator's own state, for a declared agent.

    Handed as ``obs.hidden`` to an agent with ``privileged = True``, and
    recorded on its scorecard as ``uses_hidden_state``. Everything the market
    view serves, plus every column, the factor attribution, the model's
    dials, the full macro state, the economy, the fundamentals and the day's
    news with its size. Still no fork, no snapshot and no writes.
    """

    __slots__ = ("__raw",)

    def __init__(self, engine: Engine) -> None:
        super().__init__(engine)
        object.__setattr__(self, "_HiddenState__raw", engine)

    def column(self, field: str) -> bytes:
        return self.__raw.column(field)

    def attribution(self, factor: str) -> bytes:
        return self.__raw.attribution(factor)

    @property
    def FACTORS(self) -> list[str]:
        return list(self.__raw.FACTORS)

    @property
    def model_params(self) -> dict[str, Any]:
        return dict(self.__raw.model_params)

    @property
    def macro_state(self) -> Any:
        return self.__raw.macro_state

    @property
    def macro_fields(self) -> dict[str, Any]:
        return dict(self.__raw.macro_fields)

    def economy(self) -> dict[str, Any]:
        """The economy block of ``Engine.state_snapshot``, and only that:
        the whole snapshot carries the generator state."""
        return dict(self.__raw.state_snapshot()["economy"])

    def fundamentals(self) -> tuple[list[float], list[float], list[float]]:
        eps, bv, growth = self.__raw.fundamentals()
        return list(eps), list(bv), list(growth)

    def session_news(self) -> list[dict[str, Any]]:
        return [dict(e) for e in self.__raw.session_news()]

    def truth(self, **kwargs: Any):
        return self.__raw.truth(**kwargs)

    def __getattr__(self, name: str) -> Any:
        raise _refuse("hidden state", name)

    def __repr__(self) -> str:
        return f"HiddenState({len(self)} instruments)"


class PortfolioView:
    """The agent's own portfolio, read-only.

    The valuation methods take an engine argument for compatibility with
    ``obs.portfolio.net_worth(obs.engine)`` and ignore it: the view values
    against the market it was built on. Positions and fills come back as
    copies.
    """

    __slots__ = ("__portfolio", "__engine")

    def __init__(self, portfolio: Any, engine: Engine) -> None:
        object.__setattr__(self, "_PortfolioView__portfolio", portfolio)
        object.__setattr__(self, "_PortfolioView__engine", engine)

    @property
    def cash(self) -> float:
        return self.__portfolio.cash

    @property
    def starting_cash(self) -> float:
        return self.__portfolio.starting_cash

    @property
    def max_leverage(self) -> float | None:
        return self.__portfolio.max_leverage

    @property
    def cash_interest(self) -> bool:
        return self.__portfolio.cash_interest

    @property
    def interest(self) -> float:
        return self.__portfolio.interest

    @property
    def owner(self) -> str:
        return self.__portfolio.owner

    @property
    def positions(self) -> dict[str, Any]:
        from .portfolio import Position

        out: dict[str, Position] = {}
        for ticker, held in self.__portfolio.positions.items():
            twin = Position(ticker)
            twin.quantity = held.quantity
            twin.avg_cost = held.avg_cost
            twin.realised = held.realised
            out[ticker] = twin
        return out

    @property
    def fills(self) -> list[dict]:
        return [dict(f) for f in self.__portfolio.fills]

    def marks(self, engine: Any = None) -> dict[str, float]:
        return self.__portfolio.marks(self.__engine)

    def market_value(self, engine: Any = None) -> float:
        return self.__portfolio.market_value(self.__engine)

    def net_worth(self, engine: Any = None) -> float:
        return self.__portfolio.net_worth(self.__engine)

    def pnl(self, engine: Any = None) -> float:
        return self.__portfolio.pnl(self.__engine)

    def unrealised(self, engine: Any = None) -> float:
        return self.__portfolio.unrealised(self.__engine)

    def realised(self) -> float:
        return self.__portfolio.realised()

    def gross_exposure(self, engine: Any = None) -> float:
        return self.__portfolio.gross_exposure(self.__engine)

    def leverage(self, engine: Any = None) -> float:
        return self.__portfolio.leverage(self.__engine)

    def open_orders(self, engine: Any = None) -> list[dict]:
        return [dict(o) for o in self.__portfolio.open_orders(self.__engine)]

    def __getattr__(self, name: str) -> Any:
        raise SandboxError(
            f"the portfolio view has no {name!r}: an agent trades by "
            f"returning orders from act(), not by calling the portfolio. "
            f"{_OPT_IN}")

    def __setattr__(self, name: str, value: Any) -> None:
        raise SandboxError("the portfolio view is read-only")

    def __repr__(self) -> str:
        return (f"PortfolioView(cash={self.cash:,.2f}, "
                f"positions={len(self.__portfolio.positions)})")


def declares_hidden_state(agent: Any) -> bool:
    """Whether ``agent`` declared the hidden-state capability."""
    return bool(getattr(agent, "privileged", False))


def hidden_state(obs: Any) -> Any:
    """The hidden-state reader for a privileged agent's observation.

    ``obs.hidden`` when the harness granted it. An observation built by hand
    around a live engine, or handed out under ``trusted_agents=True``, reads
    the engine itself. Anything else refuses with a message saying how to
    declare the capability.
    """
    hidden = getattr(obs, "hidden", None)
    if hidden is not None:
        return hidden
    engine = getattr(obs, "engine", None)
    if isinstance(engine, Engine):
        return engine
    raise SandboxError(
        "this agent reads hidden state and the observation carries none. "
        "Declare it on the agent with `privileged = True`, which the "
        "scorecard records as uses_hidden_state.")


def economy_of(source: Any) -> dict[str, Any]:
    """The economy block, from a :class:`HiddenState` or a live engine."""
    if isinstance(source, HiddenState):
        return source.economy()
    return source.state_snapshot()["economy"]


def _portfolio_state(portfolio: Any) -> tuple:
    positions = tuple(sorted(
        (t, p.quantity, p.avg_cost, p.realised)
        for t, p in portfolio.positions.items()))
    flow = tuple(sorted((t, tuple(v)) for t, v in portfolio._flow.items()))
    return (portfolio.cash, portfolio.starting_cash, portfolio.interest,
            portfolio.max_leverage, portfolio.cash_interest, portfolio.owner,
            len(portfolio.fills), positions, flow, portfolio._in_book)


class TamperGuard:
    """Detects any change agent code makes to the engine or a portfolio.

    Used as a context manager around each call into agent code::

        guard = TamperGuard(engine, portfolios)
        with guard:
            orders = agent.act(obs)
        if guard.tampered: ...

    Compares, before and after: ``Engine.state_hash`` (prices, every column,
    the generator states, the economy, the agent-facing book), the
    fundamentals ``set_fundamentals`` writes (which the hash does not
    cover), the recording counters, and each portfolio's cash, positions,
    fills and pending flow. Compared as ``repr`` so a NaN in a fundamentals
    list compares equal to itself. Reading is free of side effects, so a
    run with the guard is the run without it, digest for digest.
    """

    __slots__ = ("_engine", "_portfolios", "_before", "tampered", "what")

    def __init__(self, engine: Engine, portfolios: Iterable[Any]) -> None:
        self._engine = engine
        self._portfolios = tuple(portfolios)
        self._before: tuple | None = None
        self.tampered = False
        self.what = ""

    def _state(self) -> tuple:
        e = self._engine
        return (e.state_hash(), repr(e.fundamentals()), e.recorded_days,
                e.recorded_book_rows, e.session_ticks_written,
                repr(tuple(_portfolio_state(p) for p in self._portfolios)))

    def __enter__(self) -> "TamperGuard":
        self._before = self._state()
        self.tampered = False
        self.what = ""
        return self

    def __exit__(self, *exc: Any) -> bool:
        after = self._state()
        before = self._before
        assert before is not None
        if after != before:
            self.tampered = True
            parts = []
            if after[0] != before[0]:
                parts.append(f"engine state hash {before[0][:12]} -> "
                             f"{after[0][:12]}")
            if after[1] != before[1]:
                parts.append("fundamentals rewritten")
            if after[2:5] != before[2:5]:
                parts.append("recording changed")
            if after[5] != before[5]:
                parts.append("portfolio changed outside the order path")
            self.what = "; ".join(parts)
        return False
