---
title: Sweeps and parallelism
nav_order: 10
rack: experiment
short: Sweeps and parallelism
---

# Sweeps and parallelism

```python
for seed, table in pt.sweep(range(100), universe=universe, days=252,
                            collect="truth"):
    results.append(pl.from_arrow(table).select(...).mean())
```

`sweep` streams one seed at a time. One recorded engine at 252 days, 390 ticks
and 100 instruments retains twelve buffers of 9.8 million f64 - about 940 MB -
and materialises 9.8 million rows of ground truth. A hundred of those at once
is roughly 90 GB; one at a time is under one, and the analysis is usually a
reduction that never needed them resident.

`list(sweep(...))` puts that memory straight back.

`workers=n` keeps n engines alive, which is an explicit trade rather than a
free speedup, so the default is 1. Seeds always arrive in seed order, never
completion order.

Parallelism is per seed and only per seed. That survives the split of the
engine's RNG into three per-domain substreams — market, economy, external, see
[RNG streams](rng-streams.html) — because the split is by domain, not by unit
of work. The market stream alone serves every draw in a tick — the market
factor, each sector factor, per-company noise, volume, book settlement — in
one fixed order across the whole roster, so no decomposition within a single
run preserves the draw schedule. The economy's separate stream buys
comparability, not concurrency: a pinned macro path no longer reshuffles the
market's noise, but the day close still feeds the next day's pricing, so the
domains run in sequence whatever their streams do.
