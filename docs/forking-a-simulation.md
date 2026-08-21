---
title: Forking a simulation
nav_order: 9
---

# Forking a simulation

Run to day sixty, then ask two questions of the same market, with everything
before the fork identical rather than statistically similar.

```python
mark = pt.Checkpoint.of(engine, universe=universe, seed=42)
calm, hiked = mark.branch(2)
```

Two mechanisms for different jobs:

| | cost | survives the process |
|---|---|---|
| `pt.branch(engine, 2, ...)` | < 1 ms | no |
| `Checkpoint.resume()` | 2.7 s | yes |

`branch` copies engine state - every column plus the generator position - in
constant time. `Checkpoint` replays the order log, three orders of magnitude
slower, and is what you want when the fork has to outlive the process. Cite the
log in a published result, since that is what someone else can re-run.

A `Checkpoint` records the universe fingerprint and refuses to load against a
roster that changed, because restoring across two same-named universes gives
right prices and wrong fair values - plausible everywhere visible, wrong in the
one place that drives everything.

The low-level `restore_state` cannot make that check, since an engine holds no
fundamentals. It verifies roster order and size only. Prefer `branch` and
`Checkpoint`.
