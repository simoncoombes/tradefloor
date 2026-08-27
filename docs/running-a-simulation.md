---
title: Running a simulation
nav_order: 3
rack: start
short: Running a simulation
---

# Running a simulation

Four granularities. Which one you want depends on how often your code makes a
decision.

| your decisions happen | use |
|---|---|
| daily or slower | `run_days()` |
| when one name crosses a price level | `run_until(...)` |
| every tick, one market | `tick()` |
| across many seeds | `run_many` / `EngineBatch` |

```python
import numpy as np
import pretium as pt

engine = pt.Engine(seed=42, universe=pt.Universe.random(20, seed=11))

# whole span, one call
engine.run_days(252)

# day at a time, inspect between
for day in range(252, 262):
    engine.open_market()
    engine.run_session(9, 30, 3, 390)
    engine.record(day)                                     # BEFORE the close
    engine.close_market()
    closes = np.frombuffer(engine.prices(), dtype="<f8")   # roster order
    economy = engine.macro_state                           # macro snapshot

# advance until one name leaves a price band: the tick it fired on, or
# None if max_ticks ran out first
aaa = closes[engine.index_of("AAA")]
at = engine.run_until(ticker="AAA", above=aaa * 1.005, below=aaa * 0.995)

# tick granularity. Two channels: the book prices your fill, order_flow is
# how the market hears about it.
fill = engine.book("AAA").submit("buy", 500, taker="me")
engine.tick(9, 52, 3, order_flow={"AAA": (500.0, 0.0)})
engine.close_market()   # close bookkeeping: momentum roll, GARCH innovation
```

That block runs as written. `closes[:3]` is `[124.43 361.33 32.43]`, `economy.vix`
is 16.038872548154373, `aaa` is 124.43, `at` is 21 -- AAA left the half-percent
band twenty-one ticks in -- and the 500-share buy filled whole at an average of
125.16.

The hand-driven loop is what `run_days()` does for you: open, session, record,
close, once per day. Driven that way it is not merely equivalent, it is
bit-identical -- `run_days(5)` and the four-call loop on `Universe.random(20,
seed=11)`, sim seed 42, end with byte-equal `prices()`.

**Record before the close, not after.** The close advances the macro chain into
the next day, so a row recorded after it carries the values the next day will
trade under. Prices are unaffected either way; the `macro` table shifts a day
early. Over `run_days(5)` on that same setup the recorded `vix` column is
`[15.0, 11.357967, 16.519971, 14.339763, 10.0]`, and recording after the close
instead gives `[11.357967, 16.519971, 14.339763, 10.0, 13.026587]`.

A day is 390 regular-session ticks plus the close bookkeeping `close_market()`
owns. Mixing granularities is legal.

### What `run_until` will and will not stop on

It is a price-band trigger on one named instrument, and `ticker=` is required.
Give it `above=`, `below=` or both; it advances ticks until that ticker's price
leaves the band, returns the tick index it fired on, and returns `None` if
`max_ticks` (default 390) ran out first. `None` is a real outcome, not a
failure: "it never got there" is usually the answer you needed.

There is no fill, bar, day-close or event condition. It opens the market if the
market is closed, and it does not run the close when it fires -- the day is not
over, you interrupted it. An inverted band and a band with neither bound are
both refused rather than fired.

### What a tick loop costs

Less than the shape of the API suggests. A Python `for` loop calling `tick()`
crosses the Python<->Rust boundary 98,280 times per simulated year, 390 ticks
times 252 days, and every attribute read on the result is another crossing --
but the Rust work dominates both. Measured, best of three runs each, on this
machine:

| 252 days x 20 instruments | wall clock |
|---|---|
| `run_days(252)` | 1.366s |
| hand-written tick loop | 1.312s |
| tick loop plus five attribute reads per tick, 491,400 more crossings | 1.347s |

At 108 instruments over 63 days the same three are 1.913s, 1.764s and 1.864s.
So choose the granularity your decisions actually need rather than the one you
think is cheaper. The reason to prefer `run_days()` is that it is four calls
you cannot get out of order, not speed.

One thing a tick loop must get right: walk the clock. `run_session` advances
game time one minute per tick, so a loop passing the same `hour, minute` 390
times is simulating a different market. Walked properly, a tick loop reproduces
`run_days` byte for byte -- checked on `prices()` over 252 days x 20 names and
63 days x 108 names.

### Filling and moving are two different channels

`engine.book(ticker)` hands back a snapshot rebuilt from current state on every
call, and it is detached. Filling against it prices your execution honestly --
you pay the levels you consume, so impact is emergent rather than a coefficient
-- but the market does not learn that you traded. Your pressure reaches it only
through `order_flow=` on the next `tick()` or `run_session()`.

That is deliberate, and it is worth measuring rather than assuming. Two
otherwise identical runs, one of which submitted 500,000 AAA shares into the
book between sessions and filled ten levels at an average of 138.02, ended with
byte-identical `prices()` and `maker_inventory`. Push the same 500,000 through
`order_flow` instead and AAA closes at 140.45 rather than 140.41. A harness
that wants both a realistic fill price and a realistic footprint must do both.
