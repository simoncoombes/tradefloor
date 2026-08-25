---
title: Core concepts
nav_order: 2
rack: start
---

# Core concepts

Three things define a run: a **universe** (which companies exist and what their
fundamentals are), a **macro** state (rates, inflation, the cycle), and a
**seed**.

```python
import pretium as pt

universe = pt.Universe.random(108, seed=7)
macro = pt.Macro(federal_funds_rate=0.025, corporate_bond_yield=0.052, vix=16.0)
engine = pt.Engine(seed=42, universe=universe, macro_state=macro)
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

**The macro chain runs endogenously by default.** The `Macro` you construct
the engine with is the day-zero state, not the whole run: every day close
advances the economy - economy update, cycle transition, central bank - so
rates, the cycle and fair value evolve on their own from your initial
conditions. This is a 2026-08 era-boundary change; before it the chain was
implemented in the Rust core but unreachable from Python, and every field
held its initial value for the whole run.

Measured over `run_days(120)` on `Universe.random(20, seed=11)`, sim seed 42:
`vix` takes a new value every day, 120 distinct values in 120 days; the
policy-driven fields step at the central bank's meeting calendar rather than
daily - `federal_funds_rate` takes 2 distinct values over the run,
`corporate_bond_yield` 3, `inflation_rate` 4, `gdp_growth` 6; and
`fundamental_value` takes 3 distinct values per instrument, repricing when
the discount rate moves at a meeting - days 45 and 96 of that run. The one
exception is a loss-maker valued off book value, which never reprices,
because book value carries no discount rate - see
[Conventions](conventions.html).

That matters for what you can conclude. Fair value is an anchor that moves:
mean reversion pulls toward a level that itself reprices at the meeting
calendar, so a run long enough to cross a meeting is not a stationary
experiment. To impose a macro path of your own, drive it with
[Scenarios](scenarios.html) or pin a field directly:

```python
engine.pin_macro(corporate_bond_yield=0.09)   # from a default of 0.0456
```

A pin overrides the endogenous step for that field: a day-by-day pinned
series stays fully exogenous, and a single pin moves the field once, after
which the chain evolves onward from the pinned value rather than freezing it.
Measured on twenty instruments, that pin repriced nineteen of them. The
twentieth is the loss-maker above. Pinning `federal_funds_rate` alone
repriced none of the twenty, for the reason set out under
[Scenarios](scenarios.html).

Read the macro back per day from `day.economy`, or over a whole run from the
`macro` table. The macro row for a day carries the values the day traded
under; the close then advances the chain into the next day.
