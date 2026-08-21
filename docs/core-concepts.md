---
title: Core concepts
nav_order: 2
---

# Core concepts

Three things define a run: a **universe** (which companies exist and what their
fundamentals are), a **macro** state (rates, inflation, the cycle), and a
**seed**.

```python
import pretium as pt

universe = pt.Universe.random(108, seed=7)
macro = pt.Macro(fed_funds=0.025, corporate_bond_yield=0.052, vix=16.0)
engine = pt.Engine(seed=42, universe=universe, macro=macro)
```

### Universe

`Universe` subclasses `list`, and roster order is contractual. The engine walks
instruments in index order and draws random numbers as it goes, so a re-sorted
universe produces a different market from the same seed. Sorting your tickers
alphabetically upstream will silently give you a different world.

Use `universe.fingerprint` - a sha256 over the roster's canonical form,
including order - whenever you need to ask whether two universes are the same
one. Tickers are generated positionally, so `Universe.random(40, seed=1)` and
`Universe.random(40, seed=99)` share every name and no fundamentals.

The universe seed is separate from the simulation seed. Holding the universe
fixed while varying the simulation seed is the standard setup for variance
estimation.

Three constructors:

```python
pt.Universe.random(108, seed=7)                  # generated, plausible per sector
pt.Universe([pt.Instrument(...), ...])           # explicit roster
pt.Universe.from_edgar(snapshot)                 # real SEC fundamentals
```

### Macro

The macro chain runs endogenously by default - a Taylor rule feeding off
Phillips and Okun relationships over a cycle. You set initial conditions and it
evolves. To drive a series yourself, see [Scenarios](scenarios.html).

Read it back per day from `day.economy`, or over a whole run from the `macro`
table.
