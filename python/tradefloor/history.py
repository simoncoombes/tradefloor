"""A market's past, for an agent that starts trading in its middle.

## Why this exists

:func:`tradefloor.evaluate` builds a fresh engine and scores from its first
session, so on day zero an agent has one price per name and nothing behind
it. A real trader on day zero has years of bars. Any rule with a lookback,
a 200-day average, a 12-month trend, a 60-day volatility estimate, spends
the start of the run in cash waiting for its window to fill, and a
comparison with buy-and-hold charges that wait to the rule.

Measured with one estimator on both sides, over one-year windows starting
every 21 sessions (the prehistory gap study, 2026-09-26):

======================  ==============  =================
rule                    idle when cold  warm minus cold
======================  ==============  =================
SMA200 filter           79% of the year real +5.1, model +2.5 pts/yr
12-month TSMOM          100%            real +7.7, model +2.8
20-day breakout         8%              real +0.3, model +0.2
60-day vol target       24%             real +2.0, model +1.4
======================  ==============  =================

Real is the S&P 500 price index 1990-2025 (Yahoo ^GSPC daily closes, the
long-run tape); model is the cap-weighted index of pt-v20 on 90 held-out
21-year histories (seeds 201-230, 501-530, 801-830). The model's past carries what a real past carries: the
correlation of log realised volatility over the 20 sessions before and after
a seam is 0.691 in the model and 0.659 in the S&P 500.

## What a pre-history is

:func:`prehistory` runs ``days`` untraded sessions on the engine it is
handed, on exactly the loop the harness's untraded baseline uses, and keeps
one daily bar per instrument from each. Nothing is invented: after N days of
pre-history and T scored days the market is, state hash for state hash, the
market an untraded run of N+T days reaches. A pre-history moves the scored
window later in the same market's life; it does not add a warm-up regime.

The bars hold only what a data vendor sells: the columns in
:data:`tradefloor.sandbox.PUBLIC_COLUMNS` and the macro fields in
:data:`tradefloor.sandbox.PUBLISHED_MACRO`, read the same way the market
view reads them. ``close`` is the price after the close, which under a
closing auction is the cross. ``high`` and ``low`` are the session's range
widened to include the close, because the cross is a print and a vendor's
bar contains it.

Days are labelled ``-N`` to ``-1``. Under the harness the history then
grows by one bar after each scored close, labelled ``0, 1, ...`` like
``obs.day``, so on scored day ``d`` it ends at ``d - 1`` and never shows the
day being traded. Each agent holds its own copy, grown from its own market:
the pre-history is common to all of them, but from day zero an agent's
trading moves its market, and the bars it is shown are the ones it printed.
Handing every agent the untraded baseline's bars instead would show it the
market its own impact was measured against.

Scorecards record ``history_days``. With N days of pre-history, scored day
zero is the session an evaluation without one scores as day N, so
:func:`tradefloor.leaderboard` and :func:`tradefloor.rank` refuse to rank
cards run with different settings together.

The scenario clock starts at scored day zero and the pre-history runs no
scenario, so nothing about a coming scenario is in the bars.

``history_days=252`` is the recommended setting for lookback agents, and
273 for a rule that skips the most recent month (12-1 momentum). The cap is
:data:`MAX_HISTORY_DAYS`, ten years.
"""

from __future__ import annotations

import struct
from array import array
from typing import Any, Sequence

from ._core import Engine, ValidationError
from .sandbox import PUBLISHED_MACRO, SandboxError

#: The longest pre-history any harness accepts: ten years of sessions.
MAX_HISTORY_DAYS = 2520

#: The documented value for an agent with a lookback of up to a year.
RECOMMENDED_HISTORY_DAYS = 252

#: The bar fields, in the order a row of :meth:`History.bars` carries them.
BAR_FIELDS = ("open", "high", "low", "close", "volume")


def _f64(buf: bytes) -> array:
    out = array("d")
    out.frombytes(buf)
    if struct.pack("<d", 1.0) != array("d", [1.0]).tobytes():  # pragma: no cover
        out.byteswap()
    return out


def check_history_days(days: Any) -> int:
    """``days`` as an int in ``0..MAX_HISTORY_DAYS``, or a ValidationError."""
    if isinstance(days, bool) or not isinstance(days, int):
        raise ValidationError(
            f"history_days must be an integer, got {type(days).__name__}")
    if not 0 <= days <= MAX_HISTORY_DAYS:
        raise ValidationError(
            f"history_days must be 0..{MAX_HISTORY_DAYS} (ten years), got "
            f"{days}. 252 is a year and covers a 200-day average or a "
            f"12-month trend; 273 covers a 12-1 rule.")
    return days


class History:
    """Daily bars of sessions already run, read-only.

    Built by :func:`prehistory`; the harness appends each scored day after
    its close. Every accessor returns a copy, so an agent that edits what it
    was handed edits its own copy.
    """

    __slots__ = ("_tickers", "_labels", "_bars", "_macro", "_steps",
                 "_steps_per_day", "_first")

    def __init__(self, tickers: Sequence[str], steps_per_day: int,
                 first_label: int) -> None:
        self._tickers = tuple(tickers)
        self._steps_per_day = int(steps_per_day)
        self._first = int(first_label)
        self._labels: list[int] = []
        self._bars: dict[str, list[array]] = {f: [] for f in BAR_FIELDS}
        self._macro: list[dict[str, Any]] = []
        # One array per day holding every step's opening prices, row after
        # row: steps_per_day * n doubles.
        self._steps: list[array] = []

    # -- building (the harness's side) ------------------------------------

    def _append(self, *, open_: array, high: array, low: array,
                close: array, volume: array, macro: dict[str, Any],
                steps: array) -> None:
        label = self._first + len(self._labels)
        self._labels.append(label)
        self._bars["open"].append(open_)
        self._bars["high"].append(array("d", map(max, high, close)))
        self._bars["low"].append(array("d", map(min, low, close)))
        self._bars["close"].append(close)
        self._bars["volume"].append(volume)
        self._macro.append({k: v for k, v in macro.items()
                            if k in PUBLISHED_MACRO})
        self._steps.append(steps)

    def copy(self) -> "History":
        """An independent history with the same days, to grow separately.

        The harness hands each agent its own, because after day zero each
        agent's market is its own: the bars it is shown are the ones its
        trading printed, never another agent's or the untraded baseline's.
        The days already held are shared, which is safe because nothing
        edits a day once it is appended.
        """
        out = History(self._tickers, self._steps_per_day, self._first)
        out._labels = list(self._labels)
        out._bars = {f: list(rows) for f, rows in self._bars.items()}
        out._macro = list(self._macro)
        out._steps = list(self._steps)
        return out

    # -- shape ------------------------------------------------------------

    @property
    def tickers(self) -> list[str]:
        return list(self._tickers)

    @property
    def days(self) -> list[int]:
        """The day labels, oldest first: ``-N..-1``, then scored days."""
        return list(self._labels)

    @property
    def steps_per_day(self) -> int:
        return self._steps_per_day

    def __len__(self) -> int:
        return len(self._labels)

    def _column(self, field: str, ticker: str) -> tuple[float, ...]:
        try:
            i = self._tickers.index(ticker)
        except ValueError:
            raise ValidationError(f"unknown ticker {ticker!r}") from None
        return tuple(row[i] for row in self._bars[field])

    # -- per instrument ---------------------------------------------------

    def close(self, ticker: str) -> tuple[float, ...]:
        """Daily closes of one name, oldest first."""
        return self._column("close", ticker)

    def open(self, ticker: str) -> tuple[float, ...]:
        return self._column("open", ticker)

    def high(self, ticker: str) -> tuple[float, ...]:
        return self._column("high", ticker)

    def low(self, ticker: str) -> tuple[float, ...]:
        return self._column("low", ticker)

    def volume(self, ticker: str) -> tuple[float, ...]:
        return self._column("volume", ticker)

    # -- whole roster -----------------------------------------------------

    def rows(self, field: str = "close") -> list[list[float]]:
        """One row per day, one value per instrument in roster order."""
        if field not in self._bars:
            raise ValidationError(
                f"unknown bar field {field!r}; valid: {list(BAR_FIELDS)}")
        return [list(row) for row in self._bars[field]]

    def closes(self) -> list[list[float]]:
        """``rows("close")``: the daily closes, a row per day."""
        return self.rows("close")

    def macro(self, field: str) -> tuple[Any, ...]:
        """One published macro field per day, as read before each close."""
        if field not in PUBLISHED_MACRO:
            raise SandboxError(
                f"macro field {field!r} is not published. History serves "
                f"{sorted(PUBLISHED_MACRO)}.")
        return tuple(day.get(field) for day in self._macro)

    def steps(self, last: int | None = None) -> list[list[float]]:
        """Every step's opening prices, oldest first, a row per step.

        The prices an agent would have been shown at each decision point,
        had it been trading: ``steps_per_day`` rows a day. ``last`` keeps
        only the most recent rows.
        """
        n = len(self._tickers)
        out: list[list[float]] = []
        days = self._steps
        if last is not None:
            if last < 0:
                raise ValidationError("last must be >= 0")
            per = max(1, self._steps_per_day)
            days = days[-((last + per - 1) // per):] if last else []
        for day in days:
            for s in range(len(day) // n if n else 0):
                out.append(list(day[s * n:(s + 1) * n]))
        return out[-last:] if last else ([] if last == 0 else out)

    def window(self, count: int, steps_per_day: int) -> list[list[float]]:
        """The last ``count`` price rows at the cadence ``steps_per_day``.

        ``steps_per_day`` equal to the harness's gives step rows; 1 gives
        each day's opening prices, the price a once-a-day decision was
        shown. Any other cadence gets nothing, because rows at it were never
        observed. What the shipped lookback agents prefill their windows
        from.
        """
        if count <= 0:
            return []
        if steps_per_day == self._steps_per_day:
            return self.steps(count)
        if steps_per_day == 1:
            return [list(row) for row in self._bars["open"][-count:]]
        return []

    def bars(self):
        """The daily bars as a ``pyarrow.Table``.

        Columns ``day`` (int32, negative before the scored run), ``bar``
        (always 0), ``instrument_id``, then open, high, low, close and
        volume: the day-grain schema of ``Engine.bars`` with a signed day.
        Needs ``pyarrow`` (the ``arrow`` extra).
        """
        try:
            import pyarrow as pa
        except ImportError as exc:  # pragma: no cover - depends on the env
            raise ImportError("History.bars() needs pyarrow: pip install "
                              "'tradefloor[arrow]'") from exc
        n = len(self._tickers)
        days = [label for label in self._labels for _ in range(n)]
        ids = list(range(n)) * len(self._labels)
        cols: dict[str, Any] = {
            "day": pa.array(days, pa.int32()),
            "bar": pa.array([0] * len(days), pa.uint32()),
            "instrument_id": pa.array(ids, pa.uint32()),
        }
        for field in BAR_FIELDS:
            cols[field] = pa.array([x for row in self._bars[field]
                                    for x in row], pa.float64())
        return pa.table(cols)

    def __repr__(self) -> str:
        if not self._labels:
            return f"History(0 days, {len(self._tickers)} instruments)"
        return (f"History({len(self)} days {self._labels[0]}.."
                f"{self._labels[-1]}, {len(self._tickers)} instruments)")


def read_day_bars(engine: Engine) -> dict[str, Any]:
    """The session's open, high, low, volume and published macro, read
    before the close. Reading changes nothing in the engine."""
    return {"open_": _f64(engine.column("open")),
            "high": _f64(engine.column("high")),
            "low": _f64(engine.column("low")),
            "volume": _f64(engine.column("volume")),
            "macro": {k: v for k, v in engine.macro_fields.items()
                      if k in PUBLISHED_MACRO}}


def record_day(history: History, engine: Engine, before_close: dict[str, Any],
               steps: array) -> None:
    """Append the day ``engine`` just closed. ``before_close`` is what
    :func:`read_day_bars` read before ``close_market``; the close is read
    now. ``steps`` holds each step's opening prices, row after row."""
    history._append(close=_f64(engine.prices()), steps=steps, **before_close)


def prehistory(engine: Engine, days: int, *, steps_per_day: int = 6,
               ticks_per_step: int = 65,
               start: tuple[int, int, int] = (9, 30, 3)) -> History:
    """Run ``days`` untraded sessions on ``engine`` and return their bars.

    The loop is the harness's untraded one: ``open_market``, then
    ``steps_per_day`` calls to ``run_session`` on the advancing
    :func:`~tradefloor.harness.session_clock`, then ``close_market``. No
    scenario is applied. The engine is advanced in place; fork it afterwards
    to hand identical copies to several agents, which is what
    :func:`tradefloor.evaluate` does with ``history_days``.

    Reading the columns changes nothing in the engine, so the market after
    this call is the market ``days`` untraded sessions reach without it.
    """
    days = check_history_days(days)
    if steps_per_day < 1 or ticks_per_step < 1:
        raise ValidationError("steps_per_day and ticks_per_step must be >= 1")
    history = History(engine.tickers, steps_per_day, -days)
    for _ in range(days):
        run_untraded_day(engine, history, steps_per_day, ticks_per_step,
                         start)
    return history


def run_untraded_day(engine: Engine, history: History, steps_per_day: int,
                     ticks_per_step: int, start: tuple[int, int, int]) -> None:
    """One untraded session on the harness clock, appended to ``history``."""
    from .harness import session_clock

    engine.open_market()
    steps = array("d")
    for step in range(steps_per_day):
        steps.extend(_f64(engine.prices()))
        engine.run_session(*session_clock(start, step, ticks_per_step),
                           ticks_per_step)
    before = read_day_bars(engine)
    engine.close_market()
    record_day(history, engine, before, steps)
