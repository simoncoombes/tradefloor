"""Replay a recorded run.

A seed reproduces a market only when nothing else varies. It does vary: the
market an agent trades in depends on the agent's own orders, so one seed with
different order flow is a different market — correctly. Reproducing a run
therefore means reproducing every INPUT, and that sequence is what
``Engine.order_log`` holds.

Three things this makes possible that a seed alone cannot:

- **Replaying someone else's result without their code.** The log is data, so
  a published run can be re-executed by anyone with the package.
- **Bisecting a divergence.** Replay the first N entries of two logs and find
  the step where they part.
- **Archiving an experiment.** A script may not run next year; a list of dicts
  will.
"""

from __future__ import annotations

from typing import Any, Sequence

from ._core import Engine, Instrument, Macro, News, ValidationError

# Every operation the log can carry. Declared once so an unknown op is caught
# by name rather than by falling through a chain of ifs into silence.
_OPS = frozenset({
    "open_market", "close_market", "tick", "run_session", "pin_macro",
    "list_instrument", "delist", "draw_uniform", "draw_normal", "record",
})


def replay(
    log: Sequence[dict[str, Any]],
    *,
    seed: int,
    universe: Sequence[Instrument],
    macro: Macro | None = None,
    until: int | None = None,
) -> Engine:
    """Re-execute a recorded log and return the resulting engine.

    ``seed`` and ``universe`` are NOT in the log and must be supplied. That is
    deliberate: they are the identity of the experiment, and burying them in a
    list of operations would make it easy to replay a log against the wrong
    starting conditions without noticing. Passing them explicitly forces the
    question.

    ``until`` stops after that many entries, which is what makes bisecting a
    divergence practical: replay both logs to step N and compare.

    An unknown operation raises rather than being skipped. A replay that
    silently ignored an entry would produce a market that is not the one the
    log describes — and it would look like a successful replay.
    """
    engine = Engine(seed=seed, universe=universe, macro_state=macro)
    entries = list(log)[: until if until is not None else len(log)]

    for i, entry in enumerate(entries):
        op = entry.get("op")
        if op not in _OPS:
            raise ValidationError(
                f"log entry {i}: unknown operation {op!r}. A replay that "
                "skipped it would produce a market the log does not describe."
            )

        if op == "open_market":
            engine.open_market()
        elif op == "close_market":
            engine.close_market()
        elif op == "record":
            engine.record(entry["day"])
        elif op == "draw_uniform":
            engine.draw_uniform()
        elif op == "draw_normal":
            engine.draw_normal()
        elif op == "delist":
            engine.delist(entry["index"])
        elif op == "list_instrument":
            engine.list_instrument(Instrument(
                entry["ticker"], entry["sector"],
                initial_price=entry["initial_price"],
                shares_outstanding=entry["shares_outstanding"],
                eps=entry["eps"],
                book_value_per_share=entry["book_value_per_share"],
                revenue_growth=entry["revenue_growth"],
                avg_volume=entry["avg_volume"],
                beta=entry["beta"],
                short_interest=entry["short_interest"],
            ))
        elif op == "pin_macro":
            engine.pin_macro(**entry["fields"])
        elif op == "tick":
            engine.tick(
                entry["hour"], entry["minute"], entry["day_of_week"],
                volatility=entry["volatility"],
                news=_news(entry),
                order_flow=_flow(entry),
            )
        elif op == "run_session":
            engine.run_session(
                entry["hour"], entry["minute"], entry["day_of_week"],
                entry["ticks"],
                volatility=entry["volatility"],
                close_at_end=entry["close_at_end"],
                news=_news(entry),
                order_flow=_flow(entry),
            )

    return engine


def _news(entry: dict[str, Any]) -> list[News] | None:
    items = entry.get("news") or []
    if not items:
        return None
    return [
        News(ticker=n["ticker"], sector=n["sector"], price_impact=n["price_impact"])
        for n in items
    ]


def _flow(entry: dict[str, Any]) -> dict[str, tuple[float, float]] | None:
    flow = entry.get("order_flow") or {}
    return {t: tuple(v) for t, v in flow.items()} if flow else None
