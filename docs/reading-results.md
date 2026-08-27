---
title: Reading results
nav_order: 4
rack: start
short: Reading results
---

# Reading results

Five tables, delivered over the Arrow C Data Interface. polars, pandas, pyarrow
and duckdb all read them zero-copy, and the package depends on none of them.

| table | grain |
|---|---|
| `bars` | tick close and volume, or N-minute and daily OHLCV, downsampled in Rust |
| `truth` | valuation, mispricing and a 9-way decomposition of every move |
| `macro` | evolved macro state, per day |
| `fills` | your executions, joinable to `bars` |
| `book` | order-book depth, opt-in because ten levels a side makes it 20x the rows |

```python
import polars as pl

bars = pl.DataFrame(engine.bars(grain="day"))
truth = pl.DataFrame(engine.truth())
```

Every measurement column is `f64`. There is no `f32` option, because the
known-answer tests and the cross-platform release gate hash these buffers, and
a half-precision copy would be a different market that happens to plot the
same. The keys are `uint32` -- `day`, `tick`, `bar`, `instrument_id`, `step` in
`fills`, and `side` and `level` in `book` -- because they are indices, not
measurements.

`bars` and `truth` stream one batch per recorded day; `macro`, `fills` and
`book` come back as a single batch covering the whole run. Grain is a
read-time decision - the raw buffers are kept and Arrow batches are built on
read - which is why recording ground truth costs a few percent at most rather
than doubling the run. [Performance](performance.md) has the measurement; the
overhead is below what wall-clock timing resolves.
