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

**The macro state is exogenous. It does not evolve on its own.** You set the
initial conditions, and every macro field holds that value for the whole run
unless you move it. Measured over `run_days(120)`: `vix`,
`federal_funds_rate`, `corporate_bond_yield`, `inflation_rate`,
`unemployment_rate`, `gdp_growth`, `qe_pe_boost` and `fear_greed_index` each
take exactly one distinct value, and `fundamental_value` takes exactly one
distinct value per instrument.

The engine does carry a full macro chain - a Taylor rule feeding off Phillips
and Okun relationships over a cycle - and it is implemented and unit-tested in
the Rust core. It is simply not reachable from Python in this release, so it
never steps.

That matters for what you can conclude. A run with no macro path has a
constant fair value, so mean reversion pulls toward a fixed anchor and nothing
reprices. To get a macro that moves, drive it yourself with
[Scenarios](scenarios.html) - and the exogenous path works exactly as
designed:

```python
engine.pin_macro(corporate_bond_yield=0.09)   # from a default of 0.0456
```

Measured on twenty instruments, that repriced nineteen of them. The twentieth
is a loss-maker, valued off book value, and book value carries no discount
rate - see [Conventions](conventions.html). Pinning `federal_funds_rate` alone
repriced none of the twenty, for the reason set out under
[Scenarios](scenarios.html).

Read the macro back per day from `day.economy`, or over a whole run from the
`macro` table.
