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
  instrument's book, and the bars of the days the engine has recorded. A
  World run with ``record=True`` records each day as it ends.
  ``evaluate``, ``rank``, ``tca.analyse`` and the gym environment never
  record, so there :meth:`MarketView.bars` refuses and says how to keep a
  history
- the published macro fields (:data:`PUBLISHED_MACRO`, an allowlist) and
  the curve. ``cycle`` there is the phase as published, late, the way the
  NBER dates a turn; the true phase is not served
- which names and sectors have news today, without the size or direction of
  the move, because the engine's own field is the move the story will have
  made by the close
- the agent's own portfolio, through :class:`PortfolioView`, which reads and
  cannot trade or write

Anything else raises :class:`SandboxError`, which names the opt-in. That
covers ``fork``, every ``set_*``, ``pin_macro``, ``tick``, ``run_session``,
``truth``, ``attribution``, ``fundamentals``, ``earnings_anticipation``
(it jumps on the close of the true turn, before the turn is published),
``state_snapshot`` (it carries the generator state, so a copy restored
elsewhere runs the future, and the economy block: the true phase,
``months_in_current_phase``, ``phase_gdp_target``,
``recession_probability`` and ``earnings_cycle``) and the hidden columns
``mispricing_s`` (log price less it is the fundamental),
``mispricing_momentum``, ``maker_inventory`` and ``garch_variance``.
:data:`HIDDEN_STATE` says why for the names a probe reached for.

## Every route gets the same view

- :func:`tradefloor.evaluate`, :func:`tradefloor.rank` and
  :func:`tradefloor.tca.analyse`: the observation's ``engine`` and
  ``portfolio``.
- :class:`tradefloor.World`: the same, per agent, per step.
- The framework adapters (callable, OpenAI Agents, PydanticAI, LangGraph,
  FinRobot) are agents under those harnesses, so they hold the same
  observation, and the framework itself is shown only the serialised
  payload built from the view.
- :class:`tradefloor.gym.TradingEnv`: the observation is an array of
  returns and holdings, and ``env.engine`` and ``env.portfolio`` are the
  views. The env runs the market itself and keeps the live engine as
  ``env._engine``, so the views keep training code from reaching it by
  accident and no further.
- The MCP server: a strategy is data, run through ``evaluate`` and
  ``rank`` with no opt-in. See :mod:`tradefloor.mcp` for its research
  tools, which answer the experimenter after a run and hand no strategy
  anything.

## Hidden state is a declared capability

An agent that sets ``privileged = True`` (the Oracle does, and a
:class:`~tradefloor.StrategySpec` with an ``oracle`` signal does) is also
handed ``obs.hidden``, a :class:`HiddenState`: every column, the factor
attribution, the model's dials and the economy, all read-only. Its scorecard
says ``uses_hidden_state=True``. It still gets no fork and no writes.

## The opt-in: ``trusted_agents=True``

For research that needs the live engine, :func:`tradefloor.evaluate`,
:func:`tradefloor.rank`, :class:`tradefloor.World`,
:func:`tradefloor.tca.analyse` and :class:`tradefloor.gym.TradingEnv` take
``trusted_agents=True``, which hands every agent the live engine and the
live portfolio exactly as before 0.8.5.
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

It is not a security boundary. No view keeps the engine or the portfolio as
an attribute. A table in this module holds them, keyed by the view, so
``dir(obs.engine)`` names nothing that leads to the live engine. The agent
still runs in the harness's own Python process, and code that walks the
interpreter (``gc``, frame objects, this module's table) reaches the engine.
It can also build a second engine from a guessed seed and run it ahead.

The hash check catches a write however it was reached. It cannot see a
read, because a read changes no state: a fork run ahead for look-ahead and
a hidden column read through a reached engine both leave ``tampered`` False
and ``uses_hidden_state`` False. Counting forks on the engine would take a
change to the Rust core and to ``state_hash``, which the ledgers and
:meth:`tradefloor.World.fork` rely on, so it is not done.

Run code you do not trust in a separate process, against the MCP server,
where a strategy is data and there is no Python to submit.
"""

from __future__ import annotations

import weakref
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
#: (``integrations.common.OBSERVABLE_MACRO``). Kept as the record of what
#: was withheld by name; the view itself serves :data:`PUBLISHED_MACRO`.
WITHHELD_MACRO = frozenset({"qe_pe_boost"})

#: The ``Engine.macro_fields`` keys the market view serves: the figures an
#: agency, an exchange or a vendor publishes. An allowlist, so a field the
#: engine gains later is refused until somebody decides a trader could read
#: it. ``cycle`` and ``gdp_growth`` are the engine's PUBLISHED figures:
#: ``cycle`` reads the phase as announced (``cycle_publication_lag``, the
#: NBER's delay), and a GDP publication dial has to put the BEA-style
#: figure under ``gdp_growth`` the same way. The true phase, the months
#: spent in it, the phase's GDP target, the recession probability and the
#: earnings cycle live in ``state_snapshot()["economy"]``, which the view
#: does not serve.
PUBLISHED_MACRO = frozenset({
    "vix", "federal_funds_rate", "corporate_bond_yield", "inflation_rate",
    "cycle", "gdp_growth", "unemployment_rate", "oil_price", "tariff_rate",
    "treasury_yield_10y", "treasury_yield_2y", "fear_greed_index",
})

#: Engine state a probe of pt-v20 read through the live engine, and why each
#: is refused. Named so the refusal says what the thing is; anything not
#: served is refused whether it is named here or not.
HIDDEN_STATE = {
    "state_snapshot": "the whole engine state: the generator (so a copy "
                      "restored elsewhere runs the future) and the economy "
                      "block, which holds the true business-cycle phase, "
                      "months_in_current_phase, phase_gdp_target, "
                      "recession_probability and earnings_cycle",
    "earnings_anticipation": "the earnings cycle's anticipated offset, which "
                             "jumps on the close of every true turn of phase, "
                             "before the turn is published",
    "fundamentals": "the fundamental each price is anchored to",
    "session_mispricing_s": "the mispricing, tick by tick: log(price) less "
                            "it is the fundamental",
    "truth": "the answer key, tick by tick",
    "attribution": "the factor decomposition of every move",
    "model_params": "the model's dials, the cycle's hazards among them",
    "macro_table": "the full macro path, the true phase included",
    "fork": "a copy of the market that can be run ahead: look-ahead",
}

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
    why = HIDDEN_STATE.get(name)
    if why:
        return SandboxError(f"{what} has no {name!r}: it is {why}, which no "
                            f"trader can see. {_OPT_IN}")
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


#: What each view wraps, keyed by the view: the engine for a
#: :class:`MarketView` or :class:`HiddenState`, and ``(portfolio, engine)``
#: for a :class:`PortfolioView`. Kept here rather than on the view, so that
#: no attribute of an observation leads to the live objects and ``dir()``
#: names none. Weak on the view, so an entry goes when its view does. It
#: closes the one-attribute route, and the module docstring says what it
#: leaves open.
_WRAPPED: "weakref.WeakKeyDictionary[Any, Any]" = weakref.WeakKeyDictionary()


def _no_copy(what: str, holds: str) -> SandboxError:
    return SandboxError(
        f"the {what} cannot be copied or pickled, because the copy would "
        f"carry the live {holds}. Keep the values you need rather than the "
        f"view. An agent that holds a view between steps and runs in a "
        f"forked World needs a fork() method that leaves the view behind, as "
        f"tradefloor.baselines.Oracle has.")


_NO_BARS = (
    "bars() needs recorded days, and this engine has none. tf.evaluate, "
    "tf.rank, tf.tca.analyse and tf.gym.TradingEnv never record. A World "
    "records a day as it ends, and only under run(record=True). With "
    "nothing recorded the engine would hand back the prints of the last "
    "step alone, labelled day 0 whatever the day. To keep a daily history, "
    "store what you need as the run goes: obs.prices at each step, and "
    "obs.engine.column('open'), 'high' and 'low', which hold today's open "
    "and the high and low so far, as little-endian f64 bytes. "
    "obs.engine.recorded_days counts the days bars() can serve.")


class MarketView:
    """What a trader can see of the market. The default ``obs.engine``.

    Every method reads the live engine at the moment it is called, so the
    view is always the present and never the future. See the module
    docstring for what is here and what is not.
    """

    # No slot holds the engine: `_WRAPPED` does. `__weakref__` is the one
    # slot, so the table can key on the view.
    __slots__ = ("__weakref__",)

    def __init__(self, engine: Engine) -> None:
        _WRAPPED[self] = engine

    # -- the roster -------------------------------------------------------

    @property
    def tickers(self) -> list[str]:
        return list(_WRAPPED[self].tickers)

    def __len__(self) -> int:
        return len(_WRAPPED[self])

    def index_of(self, ticker: str) -> int | None:
        return _WRAPPED[self].index_of(ticker)

    # -- prices and depth -------------------------------------------------

    def prices(self) -> bytes:
        """Every instrument's last print, as little-endian f64 bytes."""
        return _WRAPPED[self].prices()

    def column(self, field: str) -> bytes:
        """One of :data:`PUBLIC_COLUMNS`, as little-endian f64 bytes."""
        if field not in PUBLIC_COLUMNS:
            raise SandboxError(
                f"column {field!r} is simulator state, not market data"
                + ("; log price less it is the fundamental"
                   if field.startswith("mispricing_s") else "")
                + f". The market view serves {sorted(PUBLIC_COLUMNS)}. "
                f"{_OPT_IN}")
        return _WRAPPED[self].column(field)

    def book(self, ticker: str):
        """A copy of the instrument's order book. Trading on it changes
        nothing: orders go through the ``act`` mapping."""
        return _WRAPPED[self].book(ticker)

    @property
    def recorded_days(self) -> int:
        """How many days the engine has recorded, which is what
        :meth:`bars` can serve. Always 0 under ``tf.evaluate`` and in the
        gym environment."""
        return _WRAPPED[self].recorded_days

    def bars(self, *, day: int | None = None, minutes: int | None = None,
             grain: str | None = None):
        """``Engine.bars`` over the days recorded so far.

        A World run with ``record=True`` records each day as it ends, and
        there this is ``Engine.bars`` unchanged. ``tf.evaluate``,
        ``tf.rank``, ``tf.tca.analyse`` and the gym environment never
        record, and neither does a World run without ``record=True`` or
        before its first close. With nothing recorded the engine falls back
        to the prints of its last ``run_session``, which inside a harness is
        the last step alone, labelled day 0 whatever the day. So with
        :attr:`recorded_days` at 0 this raises :class:`SandboxError` at
        every grain, tick included, and the message says how to keep a
        history of your own.
        """
        engine = _WRAPPED[self]
        if not engine.recorded_days:
            raise SandboxError(_NO_BARS)
        return engine.bars(day=day, minutes=minutes, grain=grain)

    # -- the economy ------------------------------------------------------

    @property
    def macro_state(self) -> PublishedMacro:
        return PublishedMacro(_WRAPPED[self].macro_state)

    @property
    def macro_fields(self) -> dict[str, Any]:
        """The published macro fields (:data:`PUBLISHED_MACRO`)."""
        return {k: v for k, v in _WRAPPED[self].macro_fields.items()
                if k in PUBLISHED_MACRO}

    @property
    def curve(self) -> dict[str, float]:
        """The yield curve, as ``Engine.curve`` (a property there too)
        gives it, copied. Until this was a property it raised on every
        call, because the engine's is not a method."""
        return dict(_WRAPPED[self].curve)

    @property
    def rate_instruments(self) -> list[dict[str, Any]]:
        """Each rate index's quote, as ``Engine.rate_instruments`` (a
        property there too) gives it, copied. Fixed with ``curve``."""
        return [dict(row) for row in _WRAPPED[self].rate_instruments]

    def news(self) -> list[dict[str, Any]]:
        """Today's company and sector news: who, not how much.

        ``Engine.session_news`` carries ``price_impact``, the whole move the
        story adds by the close, which is the answer key. A headline tells a
        trader that there is news on a name; this says that and no more.
        """
        return [{"ticker": e.get("ticker"), "sector": e.get("sector"),
                 "day": e.get("day")}
                for e in _WRAPPED[self].session_news()]

    # -- identity and the clock -------------------------------------------

    @property
    def model_fingerprint(self) -> str:
        return _WRAPPED[self].model_fingerprint

    @property
    def session_tick(self) -> int | None:
        return _WRAPPED[self].session_tick

    # -- everything else --------------------------------------------------

    def __getattr__(self, name: str) -> Any:
        raise _refuse("the market view", name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise SandboxError("the market view is read-only")

    def __reduce_ex__(self, protocol: Any) -> Any:
        # copy.copy, copy.deepcopy and pickle all arrive here.
        raise _no_copy("market view", "engine")

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

    __slots__ = ()

    def column(self, field: str) -> bytes:
        return _WRAPPED[self].column(field)

    def attribution(self, factor: str) -> bytes:
        return _WRAPPED[self].attribution(factor)

    @property
    def FACTORS(self) -> list[str]:
        return list(_WRAPPED[self].FACTORS)

    @property
    def model_params(self) -> dict[str, Any]:
        return dict(_WRAPPED[self].model_params)

    @property
    def macro_state(self) -> Any:
        return _WRAPPED[self].macro_state

    @property
    def macro_fields(self) -> dict[str, Any]:
        return dict(_WRAPPED[self].macro_fields)

    def economy(self) -> dict[str, Any]:
        """The economy block of ``Engine.state_snapshot``, and only that:
        the whole snapshot carries the generator state."""
        return dict(_WRAPPED[self].state_snapshot()["economy"])

    def fundamentals(self) -> tuple[list[float], list[float], list[float]]:
        eps, bv, growth = _WRAPPED[self].fundamentals()
        return list(eps), list(bv), list(growth)

    def session_news(self) -> list[dict[str, Any]]:
        return [dict(e) for e in _WRAPPED[self].session_news()]

    def truth(self, **kwargs: Any):
        return _WRAPPED[self].truth(**kwargs)

    def __getattr__(self, name: str) -> Any:
        raise _refuse("hidden state", name)

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise _no_copy("hidden state", "engine")

    def __repr__(self) -> str:
        return f"HiddenState({len(self)} instruments)"


class PortfolioView:
    """The agent's own portfolio, read-only.

    The valuation methods take an engine argument for compatibility with
    ``obs.portfolio.net_worth(obs.engine)`` and ignore it: the view values
    against the market it was built on. Positions and fills come back as
    copies.
    """

    # As on MarketView: the portfolio and the engine live in `_WRAPPED`.
    __slots__ = ("__weakref__",)

    def __init__(self, portfolio: Any, engine: Engine) -> None:
        _WRAPPED[self] = (portfolio, engine)

    @property
    def cash(self) -> float:
        return _WRAPPED[self][0].cash

    @property
    def starting_cash(self) -> float:
        return _WRAPPED[self][0].starting_cash

    @property
    def max_leverage(self) -> float | None:
        return _WRAPPED[self][0].max_leverage

    @property
    def cash_interest(self) -> bool:
        return _WRAPPED[self][0].cash_interest

    @property
    def interest(self) -> float:
        return _WRAPPED[self][0].interest

    @property
    def owner(self) -> str:
        return _WRAPPED[self][0].owner

    @property
    def positions(self) -> dict[str, Any]:
        from .portfolio import Position

        out: dict[str, Position] = {}
        for ticker, held in _WRAPPED[self][0].positions.items():
            twin = Position(ticker)
            twin.quantity = held.quantity
            twin.avg_cost = held.avg_cost
            twin.realised = held.realised
            out[ticker] = twin
        return out

    @property
    def fills(self) -> list[dict]:
        return [dict(f) for f in _WRAPPED[self][0].fills]

    def marks(self, engine: Any = None) -> dict[str, float]:
        portfolio, live = _WRAPPED[self]
        return portfolio.marks(live)

    def market_value(self, engine: Any = None) -> float:
        portfolio, live = _WRAPPED[self]
        return portfolio.market_value(live)

    def net_worth(self, engine: Any = None) -> float:
        portfolio, live = _WRAPPED[self]
        return portfolio.net_worth(live)

    def pnl(self, engine: Any = None) -> float:
        portfolio, live = _WRAPPED[self]
        return portfolio.pnl(live)

    def unrealised(self, engine: Any = None) -> float:
        portfolio, live = _WRAPPED[self]
        return portfolio.unrealised(live)

    def realised(self) -> float:
        return _WRAPPED[self][0].realised()

    def gross_exposure(self, engine: Any = None) -> float:
        portfolio, live = _WRAPPED[self]
        return portfolio.gross_exposure(live)

    def leverage(self, engine: Any = None) -> float:
        portfolio, live = _WRAPPED[self]
        return portfolio.leverage(live)

    def open_orders(self, engine: Any = None) -> list[dict]:
        portfolio, live = _WRAPPED[self]
        return [dict(o) for o in portfolio.open_orders(live)]

    def __getattr__(self, name: str) -> Any:
        raise SandboxError(
            f"the portfolio view has no {name!r}: an agent trades by "
            f"returning orders from act(), not by calling the portfolio. "
            f"{_OPT_IN}")

    def __setattr__(self, name: str, value: Any) -> None:
        raise SandboxError("the portfolio view is read-only")

    def __reduce_ex__(self, protocol: Any) -> Any:
        raise _no_copy("portfolio view", "portfolio and engine")

    def __repr__(self) -> str:
        return (f"PortfolioView(cash={self.cash:,.2f}, "
                f"positions={len(_WRAPPED[self][0].positions)})")


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
