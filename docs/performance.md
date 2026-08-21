---
title: Performance
nav_order: 17
rack: reference
---

# Performance

Measured on one desktop machine. Treat the ratios as portable and the absolute
numbers as a rough guide.

| run | wall clock |
|---|---|
| 252 days x 10 instruments | 2.9s |
| 252 days x 100 instruments | 27.4s |
| 252 days x 100, recording 9.8M rows of ground truth | 28.2s |
| 8 seeds x 21 days x 100, serial | 20.0s |
| 8 seeds x 21 days x 100, 8 workers | 6.1s |

Recording costs about 3% for a full year of tick-grain ground truth, because
raw buffers are kept and Arrow batches are built on read.

Sweeps parallelise about 3.3x on eight cores. The engine releases the GIL for
the whole session compute, and `run_many` uses threads, so it works in a
notebook and does not serialise the universe into each worker.

Cost scales roughly linearly in instruments x days.
