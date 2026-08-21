---
title: Running a simulation
nav_order: 3
---

# Running a simulation

Four granularities. Which one you want depends on how often your code makes a
decision.

| your decisions happen | use |
|---|---|
| daily or slower | `days()` / `run_days()` |
| on fills, bars or events | `run_until(...)` |
| every tick, one market | `tick()` |
| across many seeds | `run_many` / `EngineBatch` |

```python
# whole span, one call
engine.run_days(252)

# day at a time, inspect between
for day in engine.days(252):
    day.closes          # ndarray in roster order
    day.economy         # macro snapshot

# advance until something happens
ev = engine.run_until(fill=True, day_close=True, max_ticks=390)

# tick granularity
engine.submit(pt.Order("ACME", side="buy", qty=500, type="limit", price=99.5))
tick = engine.tick()
tick.fills
engine.advance_day()    # close bookkeeping: momentum roll, GARCH innovation
```

`days()` and `run_days()` simulate exactly the same thing and differ only in
how many times you get control back. A day is 390 regular-session ticks plus
the close bookkeeping `advance_day()` owns. Mixing granularities is legal.

### Why not a tick loop

A Python `for` loop calling `tick()` crosses the Python<->Rust boundary about
98,000 times per simulated year, and every attribute read on the result is
another crossing. A loop touching five fields per tick makes roughly 500,000.
Prefer `days()` or `run_until` unless you genuinely need per-tick control; the
work happens in Rust either way, and the difference is how much time you spend
at the boundary.

Orders submitted between ticks land in the next tick's pending-order
aggregates, which is the same channel the engine's own flow uses.
