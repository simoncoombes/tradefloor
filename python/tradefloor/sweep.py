"""Columnar results for many seeds, without holding them all.

`run_many` returns prices or summaries, small things, one per seed. This
returns TABLES, which are not small, and the whole point is that you never
hold more than a few at once.

The arithmetic that motivates it. One recorded engine keeps fourteen f64
buffers of `days x ticks x instruments` -- price, volume, mispricing,
fundamental and anchor, plus one per attribution component, and there are nine
of those. At 252 days, 390 ticks and 100 instruments that is 9.8 million
elements per buffer, about 1.10 GB retained, and the `truth` table it
materialises is 9.8 million rows of fifteen columns. A hundred seeds held at
once is roughly 110 gigabytes. Streamed one at a time it is a little over one,
and the analysis is usually a reduction anyway (a mean, a regression, a count)
that never needed the whole thing resident.

```python
for seed, table in pt.sweep(range(100), universe=u, days=252, collect="truth"):
    frame = pl.from_arrow(table)          # one seed's ground truth
    results.append(frame.select(...).mean())
```

## Laziness is the feature, so it is a generator

Returning a list would defeat the purpose: building it would hold every table
before the caller saw the first one. The generator runs a seed, hands it over,
and drops the engine before starting the next, so peak memory is one engine
regardless of how many seeds you ask for.

Which means **consuming it out of order, or keeping the tables, brings the
memory back**. `list(sweep(...))` is exactly the thing this exists to avoid,
and it is worth saying because it looks like an obvious convenience.

## Workers trade memory back for speed, explicitly

`workers=n` keeps `n` engines alive at once, so peak memory is `n` times one
engine. That is a real trade rather than a free speedup, and it is why the
default is one. Ordering is preserved regardless: results come back in seed
order even when they finish out of order, because a sweep whose row order
depended on scheduling would be non-deterministic in exactly the way this
library exists to avoid.
"""

from __future__ import annotations

from typing import Any, Iterable, Iterator, Sequence

from ._core import Engine, Instrument, Macro, ModelParams, ValidationError
from .universe_util import as_universe

#: What a sweep can stream. Both are per-day batched by the engine, so a
#: consumer that reads batch-by-batch never materialises a whole seed either.
COLLECT = ("bars", "truth", "macro")


def _run_one(seed, universe, macro, days, ticks_per_day, start, scenario,
             collect, grain, minutes, model):
    hour, minute, day_of_week = start
    engine = Engine(seed=seed, universe=universe, macro_state=macro,
                    model=model)
    for day in range(days):
        if scenario is not None:
            scenario.apply(engine, day)
        engine.open_market()
        engine.run_session(hour, minute, day_of_week, ticks_per_day)
        # Record before the close: the close advances the macro chain, and
        # the macro row must carry the values the day traded under.
        engine.record(day)
        engine.close_market()
    if collect == "bars":
        return engine.bars(grain=grain, minutes=minutes)
    if collect == "truth":
        return engine.truth()
    return engine.macro_table()


def sweep(
    seeds: Iterable[int],
    *,
    universe: Sequence[Instrument],
    days: int = 1,
    ticks_per_day: int = 390,
    macro: Macro | None = None,
    scenario: Any = None,
    collect: str = "bars",
    grain: str | None = None,
    minutes: int | None = None,
    start: tuple[int, int, int] = (9, 30, 3),
    workers: int = 1,
    model: str | ModelParams | None = None,
) -> Iterator[tuple[int, Any]]:
    """Yield ``(seed, table)`` for each seed, one at a time.

    The table is an Arrow stream batched per day, so a consumer reading it
    batch-by-batch never materialises a whole seed either.

    Peak memory is ``workers`` engines, not ``len(seeds)``. Keeping the tables
    (``list(sweep(...))``) puts it all back.

    ``model`` selects the coefficient set, either a preset name or a
    :class:`tradefloor.ModelParams`, and every seed runs it, for the reason
    :func:`tradefloor.run_many` gives: a sweep is many draws of one market.
    The tables carry no provenance columns (they never have; the engine is
    dropped as each yields), so a caller sweeping a custom model should
    record ``model.fingerprint`` beside whatever it keeps.
    """
    seeds = list(seeds)
    if not seeds:
        raise ValidationError("no seeds given")
    if collect not in COLLECT:
        raise ValidationError(
            f"unknown collect {collect!r}. Valid: {', '.join(COLLECT)}"
        )
    if days < 1 or ticks_per_day < 1:
        raise ValidationError("days and ticks_per_day must both be at least 1")
    if workers < 1:
        raise ValidationError(f"workers must be at least 1, got {workers}")

    roster = as_universe(universe)
    args = (roster, macro, days, ticks_per_day, start, scenario, collect,
            grain, minutes, model)

    if workers == 1:
        for seed in seeds:
            yield seed, _run_one(seed, *args)
        return

    from collections import deque
    from concurrent.futures import ThreadPoolExecutor

    # A bounded window, not `executor.map` over everything. map would submit
    # every seed at once and hold every finished table until the caller got to
    # it, which is the memory this function exists to avoid -- and it would do
    # it while looking lazy, because map returns an iterator.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: deque = deque()
        upcoming = iter(seeds)
        for seed in upcoming:
            pending.append((seed, pool.submit(_run_one, seed, *args)))
            if len(pending) == workers:
                break
        while pending:
            seed, future = pending.popleft()
            # Yielded in SEED order, never completion order. A sweep whose row
            # order depended on scheduling would be non-deterministic in the
            # one way this library exists to avoid.
            yield seed, future.result()
            nxt = next(upcoming, None)
            if nxt is not None:
                pending.append((nxt, pool.submit(_run_one, nxt, *args)))
