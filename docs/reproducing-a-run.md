---
title: Reproducing a run
nav_order: 11
---

# Reproducing a run

A seed alone does not identify a run. The market an agent trades depends on
that agent's own orders, so one seed with different order flow is a different
market. Reproducing means reproducing every input.

```python
log = engine.order_log                    # plain dicts, JSON-serialisable
same = pt.replay(log, seed=42, universe=universe)
```

That makes a published result replayable without the code that produced it, a
divergence bisectable, and an experiment archivable as data.

Every result object carries the universe fingerprint alongside the seed -
`Scorecard.universe_fingerprint`, `facts.measure()["universe_fingerprint"]`,
`Execution.universe_fingerprint` - because the same seed over a different
roster is a different market.
