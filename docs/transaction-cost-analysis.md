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
Selling the same 97 shares three steps later ends anywhere between −13.3 and
+5.8 bps across sim seeds 2026, 1, 2, 3, 4, 5, 7 and 11, negative on six of
the eight, median −8.4. (An earlier version of this page printed one
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
and VIX now sets the shared factor's variance target, so flow that moves the
market return nudges every name's volatility two closes later. Measured on
`Momentum()` over `Universe.random(60, seed=11)`, sim seed 7, ten days: the
agent traded 46 names, and two of the fourteen it never touched moved, by
−6.5 and +3.2 bps, against a 13.0 bps median direct impact on the traded
names. The channel is intermittent. The VIX reaction clamps at a ±0.03%
market return, so the nudge only registers on a day whose closing return
sits inside the clamp: the same configuration run for two, three or four
days leaks nothing. And a one-day analysis cannot be reached at all,
because its final prices predate the first repriced target.

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
empty. `examples/07-research-workflow.py` asserts both halves on every test
run: fear ripples stay well under direct impact, and the pinned-VIX
counterfactual is exact.
