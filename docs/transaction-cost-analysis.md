---
title: Transaction cost analysis
nav_order: 7
---

# Transaction cost analysis

Run the same seed with and without your orders, and price every fill against
what that instrument did in the world where you never traded.

```python
ex = pt.tca.analyse(my_agent, seed=42, universe=universe, days=5)

ex.shortfall_bps()        # what your footprint cost
ex.by_step()              # where it was paid
ex.partial_fills()        # what you asked for versus what you got
ex.untouched_moved()      # should be empty
```

Arrival price, VWAP and fitted impact models are all proxies for a
counterfactual that cannot be run on real data. This one runs it.

Two results to understand before reading a number:

**Negative shortfall is possible on a round trip.** Buying and holding costs
+16.7 bps in one measured example; buying and selling three steps later comes
to -10.8 bps. The entry pushed the price up, part of that persisted, and the
exit sold into it. Shortfall answers what each execution cost, not whether the
strategy made money - read `pnl` from `evaluate` for that. `by_step()` shows
entry and exit separately rather than netting them.

**Check `partial_fills()` before believing a low cost.** A request for 4,856
shares filled 483, the whole displayed depth, and every larger request filled
the same 483. The cheapest execution is usually the one that did not happen.
