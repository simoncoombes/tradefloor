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
    economy = engine.macro_state                           # NEXT day's macro

# advance until one name leaves a price band: the tick it fired on, or
# None if max_ticks ran out first
aaa = closes[engine.index_of("AAA")]
at = engine.run_until(ticker="AAA", above=aaa * 1.005, below=aaa * 0.995)

# tick granularity. Two channels: the book prices your fill, order_flow is
# how the market hears about it.
fill = engine.book("AAA").submit("buy", 500, taker="me")
engine.tick(9, 52, 3, order_flow={"AAA": (500.0, 0.0)})
engine.close_market()   # close bookkeeping: momentum roll, GARCH, macro step
```

That block runs as written. `closes[:3]` is `[124.43 361.33 32.43]`,
`economy.vix` is 16.038872548154373, `aaa` is 124.43, `at` is 21 -- AAA left
the half-percent band twenty-one ticks in -- and the 500-share buy filled whole
at an average of 125.16.

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

`engine.macro_state` shifts with the same step: read after `close_market()` it
is already the next day's, which is why the loop above records before it closes
and why the `economy` it leaves you holding is day 262's rather than day
261's.

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
times 252 days, and every attribute read on the result is another crossing, so
a loop touching five fields per tick adds 491,400 more. Those counts are real.
What they imply about the clock is not, because the Rust work dominates: the
crossing `run_session` saves was measured at 0.357us against 249us of engine
work per tick at a hundred instruments, which is 0.14% of the tick. That
measurement is recorded where it was taken, in the docstrings of the two tests
that pin a chunked call and a hand loop to the same market:
`test_run_days_is_the_same_simulation_as_doing_it_by_hand` in
`tests/test_regimes.py` and `test_run_session_matches_a_loop_of_ticks` in
`tests/test_equivalence.py`. Charge all six crossings of a five-field tick the
full call price -- an upper bound, since reading a field is cheaper than making
a call -- and the boundary is still under one percent of the tick.

So choose the granularity your decisions actually need rather than the one you
think is cheaper. The reason to prefer `run_days()` is that it is four calls
you cannot get out of order and it records for you, not speed. The suite puts
it the same way where it pins the equivalence: "it is not a meaningful
speedup".

One thing a tick loop must get right: walk the clock. `run_session` advances
game time one minute per tick, so a loop passing the same `hour, minute` 390
times is simulating a different market. Checked on five days of ten names: the
loop that walks the clock ends with `prices()` byte-equal to `run_days`; the
frozen-clock loop does not. That equivalence is held on every commit by the
second of the two tests above.

### Filling and moving are two different channels

`engine.book(ticker)` hands back a snapshot rebuilt from current state on every
call, and it is detached. Filling against it prices your execution honestly --
you pay the levels you consume, so impact is emergent rather than a coefficient
-- but the market does not learn that you traded. Your pressure reaches it only
through `order_flow=` on the next `tick()` or `run_session()`.

That is deliberate, and it is worth measuring rather than assuming. On
`Universe.random(10, seed=11)` at sim seed 42, one day driven as two 195-tick
sessions: a 500,000-share AAA buy submitted into the book between them takes
all ten resting ask levels -- 20,624 shares filled at an average of 137.045
against a 135.67 close, the other 479,376 unfilled -- and the run still ends
with `prices()` and `maker_inventory` byte-identical to the run that never
traded. Push the same 500,000 through `order_flow` on the second session
instead and AAA closes at 137.45 rather than 135.67. A harness that wants both
a realistic fill price and a realistic footprint must do both.
