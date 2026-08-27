---
title: Transaction cost analysis
nav_order: 7
rack: measure
short: Transaction cost analysis
---

# Transaction cost analysis

Run the same seed with and without your orders, and price every fill against
what that instrument did in the world where you never traded.

```python
ex = pt.tca.analyse(my_agent, seed=42, universe=universe, days=5)

ex.shortfall_bps()        # what your footprint cost
ex.by_step()              # where it was paid
ex.partial_fills()        # what you asked for versus what you got
ex.untouched_moved()      # fear ripples only -- see the boundary below
```

Arrival price, VWAP and fitted impact models are all proxies for a
counterfactual that cannot be run on real data. This one runs it.

Two results to understand before reading a number. Both are measured on this
build against the first name of `Universe.random(20, seed=7)`, whose ADV is
9,713 shares, over one six-step day, the configuration the test suite pins.

**Negative shortfall is possible on a round trip.** Buying 97 shares (1% of
ADV) at the first step and holding costs +16.71 bps, identically on every
seed measured: the entry lands at step zero, before the two worlds can
diverge, so its cost is a property of the book rather than of the seed.
Selling the same 97 shares three steps later ends anywhere between −17.72 and
+2.03 bps across sim seeds 2026, 1, 2, 3, 4, 5, 7 and 11, negative on seven of
the eight, median −12.40. (An earlier version of this page printed one
round-trip figure; the sign genuinely flips with the seed, so a range is the
honest number.) The entry pushes the price up, part of that impact persists,
and the exit sells into it; how much survives three steps is the market's
call, which is why the round trip gets a seed range where the entry gets a
number. Shortfall answers what each execution cost, not whether the strategy
made money. Read `pnl` from `evaluate` for that. `by_step()` shows entry
and exit separately rather than netting them.

**Check `partial_fills()` before believing a low cost.** A request for 4,856
shares, half that name's ADV, on sim seed 2026, filled 483, and requests of
9,713 and 48,563 shares filled the same 483. That is the book truncating
every fill at its displayed depth (483 at the open on every seed measured:
opening depth is a property of the universe), so past the book a bigger
request buys nothing but a bigger unfilled remainder. The cheapest execution
is usually the one that did not happen.

## One boundary: the market is afraid of your trading

`untouched_moved()` lists names you never traded whose final price still
differs between the two worlds. It used to be a flat guarantee that this
comes back empty. Order flow consumes no RNG draws, which is what the
per-domain stream split exists for, so an untraded name saw byte-identical noise
and followed a byte-identical path.

Since the 2026-08 VIX volatility coupling that guarantee has a macro
boundary. The fear gauge reacts same-day to the cap-weighted market return,
and VIX sets the shared factor's variance target, so flow that moves the
market return nudges every name's volatility two closes later. Measured on
`Momentum()` over `Universe.random(60, seed=11)`, sim seed 7, ten days: the
agent traded 57 names, and all three it never touched moved, by −10.72, +2.00
and +1.97 bps, against a 9.71 bps median direct impact on the traded names.

Read that ordering carefully: the largest ripple is bigger than the median
direct impact, so this is not a rounding-error channel. It got that big on
purpose. On pt-v12 `vix_return_source` is 1.0, so the fear gauge reads the
whole day's cap-weighted open-to-close index return rather than the closing
minute alone, which is what 0.0 gives and what every preset up to pt-v8 ran.
Flow large enough to move the index therefore reaches names it never touched
at the same order of cost the book charges directly: once at 10.72 bps
against that 9.71 bps median, and twice at about a fifth of it.

The channel needs a horizon before it opens, because the reaction has to cross
two closes. Same configuration, varying only the day count: at one day
`Momentum()` places no orders at all, so there is no flow to ripple; at two
days there is flow but the repricing has not landed yet, and
`untouched_moved()` is still empty; at three days nine untouched names move,
at four days eighteen. By ten days the count is back down to three only
because the agent has traded 57 of the 60 and there is almost nothing left
untouched. The clamp on the VIX reaction is not what limits this --
`vix_return_clamp` is 15.0 on pt-v12, in percent, which no ordinary close
comes near.

That is impact, not error. The draws are still identical; what moved is the
market pricing fear of the flow, and it is a real cost that belongs in the
measurement. When the untouched names must be byte-exact, whether you are
isolating one name's impact or auditing the subtraction, pin VIX in both
worlds:

```python
ex = pt.tca.analyse(my_agent, seed=42, universe=universe, days=5,
                    scenario=pt.Scenario().hold(vix=15.0))
ex.untouched_moved()      # empty again, byte-exact
```

Verified both ways on the run above: pinned, `untouched_moved()` comes back
empty. `examples/07-research-workflow.py` asserts both halves on exactly this
configuration, and the release check runs it (`PRETIUM_SLOW_TESTS=1 python -m
pytest tests/test_examples.py`; the default run only compiles the examples).
It is deliberate about what the first half claims. The ripple assertion is
`abs(impact_bps(name)) < 3.0 * median_direct` -- the same order as direct
impact, not negligible against it, which is all the measurement supports now.
The pinned half asserts `untouched_moved()` is exactly empty, and that one is
a guarantee rather than a bound.
