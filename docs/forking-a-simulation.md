---
title: Forking a simulation
nav_order: 9
rack: experiment
short: Forking a run
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
| `pt.branch(engine, 2, ...)` | 0.6 ms | no |
| `Checkpoint.resume()` | 1.4 s | yes |

Both figures are medians of five calls on one laptop, forking a 40-name
`Universe.random(40, seed=7)` engine at day 60; `resume` is the noisier of the
two and ranged 0.8 s to 1.5 s over those five.

`branch` copies engine state - every column plus the generator position - in
constant time. `Checkpoint` replays the order log, three orders of magnitude
slower, and is what you want when the fork has to outlive the process. Cite the
log in a published result, since that is what someone else can re-run.

A `Checkpoint` records the universe fingerprint and refuses to load against a
roster that changed, because restoring across two same-named universes gives
right prices and wrong fair values - plausible everywhere visible, wrong in the
one place that drives everything.

The low-level `restore_state` cannot make that check, since an engine holds no
fundamentals. It verifies roster order and size, and it verifies the model
fingerprint the snapshot was taken under -- restoring a `pt-v12` snapshot into
a `pt-v1` engine raises `ValidationError: this snapshot was taken under model
"pt-v12" and this engine runs "pt-v1"`. What it cannot see is a roster whose
tickers still match but whose fundamentals do not, so prefer `branch` and
`Checkpoint`.
