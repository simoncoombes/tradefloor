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
import tradefloor as pt

universe = pt.Universe.random(108, seed=7)
macro = pt.Macro(federal_funds_rate=0.025, corporate_bond_yield=0.052, vix=16.0)
engine = pt.Engine(seed=42, universe=universe, macro_state=macro)
```

### Universe

`Universe` subclasses `list`, and roster order is contractual. The engine walks
instruments in index order and draws random numbers as it goes, so a re-sorted
universe produces a different market from the same seed. Sorting your tickers
alphabetically upstream will silently give you a different world.

Use `universe.fingerprint` -- a sha256 over the roster's canonical form,
including order -- whenever you need to ask whether two universes are the same
one. Reversing the roster changes the fingerprint and the market both: on
`Universe.random(20, seed=11)` at sim seed 42, AAA closes day 5 at 143.03 in
roster order and 134.88 reversed.

Tickers and sectors are assigned positionally, so `Universe.random(40, seed=1)`
and `Universe.random(40, seed=99)` share every name and every sector, and agree
on the fundamentals of none of the forty.

The universe seed is separate from the simulation seed. Holding the universe
fixed while varying the simulation seed is the standard setup for variance
estimation.

Four constructors:

```python
pt.Universe.random(108, seed=7)                  # generated, plausible per sector
pt.Universe([pt.Instrument(...), ...])           # explicit roster
pt.Universe.from_edgar(snapshot)                 # real SEC fundamentals
pt.Universe.from_json(saved)                     # one saved with to_json
```

### Macro

**The macro chain runs endogenously by default.** The `Macro` you construct
the engine with is the day-zero state, not the whole run: every day close
advances the economy -- economy update, cycle transition, central bank -- so
rates, the cycle and fair value evolve on their own from your initial
conditions. That is where the step lives, in `close_market()`, and it has
worked this way since 0.1.0. If you were expecting a frozen economy, you have
to ask for one: pin the fields you want held, as below.

Measured over `run_days(120)` on `Universe.random(20, seed=11)`, sim seed 42,
default macro: `vix` takes a new value on almost every day, 118 distinct values
in 120 days. It is not 120 only because the series floors at 10.0 and sat on
the floor for three of them. The policy-driven fields step at the central
bank's meeting calendar rather than daily -- `federal_funds_rate` takes 2
distinct values over the run, `corporate_bond_yield` 3, `inflation_rate` 4,
`gdp_growth` 6 -- and `fundamental_value` takes 3 distinct values per
instrument, repricing when the discount rate moves at a meeting, on days 45 and
96 of that run. The one exception is a loss-maker valued off book value, which
never reprices, because book value carries no discount rate -- see
[Conventions](conventions.md). Nineteen of the twenty reprice on both of those
days; the loss-maker holds one value throughout.

Those counts are not knife-edge. Build the engine with the `Macro(...)` from
the block above instead of the default and `vix` comes out at 119 distinct
rather than 118, while all four policy counts are unchanged.

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
Pin `vix=30.0` once, after day 3 of the run above, and day 3 reads the pinned
30.0 while the four days that follow read 22.75, 23.62, 23.89, 19.30: the chain
carried on from 30 and pulled back toward its mean rather than holding.
Measured on twenty instruments, the `corporate_bond_yield` pin repriced
nineteen of them. The twentieth is the loss-maker above. Pinning
`federal_funds_rate` alone repriced none of the twenty, for the reason set out
under [Scenarios](scenarios.html).

Read the macro back for the current day from `engine.macro_state`, which is a
`Macro`, or over a whole run from the `macro` table, `engine.macro_table()`.
That is an Arrow stream keyed by `day` alongside `bars` and `truth`, carrying
`vix`, `federal_funds_rate`, `corporate_bond_yield`, `inflation_rate`,
`unemployment_rate`, `gdp_growth`, `qe_pe_boost`, `fear_greed_index` and
`universe_stress`. The two are not the same field set, which is worth knowing
before you reach for the wrong one: `unemployment_rate`, `gdp_growth` and
`universe_stress` live only in the table, and `cycle` lives only on `Macro`.
The macro row for a day carries the values the day traded under; the close
then advances the chain into the next day. That is why `run_days` records a
day before closing it, and why a hand-driven loop should call `record(day)`
before `close_market()` -- see [Running a simulation](running-a-simulation.md).
