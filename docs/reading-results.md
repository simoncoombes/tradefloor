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
| `bars` | tick, N-minute or daily OHLCV, downsampled in Rust |
| `truth` | valuation, mispricing and a 7-way decomposition of every move |
| `macro` | evolved macro state, per day |
| `fills` | your executions, joinable to `bars` |
| `book` | order-book depth, opt-in because it is 40x the rows |

```python
import polars as pl

bars = pl.from_arrow(engine.bars(grain="day"))
truth = pl.from_arrow(engine.truth())
```

Every numeric column is `f64`. There is no `f32` option, because the
known-answer tests and the cross-platform release gate hash these buffers, and
a half-precision copy would be a different market that happens to plot the
same.

Results stream one batch per day. Grain is a read-time decision - the raw
buffers are kept and Arrow batches are built on read - which is why recording
ground truth costs about 3% rather than doubling the run.
