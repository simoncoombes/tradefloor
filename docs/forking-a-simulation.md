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
| `pt.branch(engine, 2, ...)` | < 1 ms | no |
| `Checkpoint.resume()` | 2.7 s | yes |

`branch` copies engine state - every column plus the generator position - in
constant time. `Checkpoint` replays the order log, three orders of magnitude
slower, and is what you want when the fork has to outlive the process. Cite the
log in a published result, since that is what someone else can re-run.

Both figures are for a sixty-day, forty-instrument run, which is the
measurement `tradefloor.checkpoint`'s own module docstring records. Read the
absolutes as an order of magnitude rather than as a spec: replay cost scales
with the order log, so it moves with the run length as much as with the
machine. `tools/remeasure` re-times the pair on a shorter thirty-day,
twenty-instrument run and reports the resume wall clock as machine-bound,
never judged at printed precision -- 0.331 s there at 0.2.0. What it does
judge is the ratio above, and the ratio is the part of the claim that travels.

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
