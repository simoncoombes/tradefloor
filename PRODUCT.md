# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Two audiences of equal weight, confirmed by the plan owner.

**RL and ML researchers** training agents to trade. They need an environment
that reacts to the agent's own orders, unlimited independent episodes, and
results a reviewer can reproduce. Their existing tooling is Gymnasium plus
Stable-Baselines3, RLlib, CleanRL or Tianshou.

**Quant and execution developers** building execution algorithms (TWAP, VWAP,
POV, icebergs) and stress-testing strategies. They need queue position, partial
fills, honest impact costs, and macro scenarios to run a strategy through.

A third, smaller audience reads the docs to teach or study market
microstructure.

The docs site optimises for time-to-first-simulation: the reader installs the
package and runs something.

## Product Purpose

`pretium` simulates an equity market that a strategy can be run against. Given
a seed, a roster of companies and a macro state, it runs prices, a limit order
book, fills and macro state forward. Orders match against real depth, so
trading moves the price.

It exists because historical data cannot answer two questions: what would have
happened had I traded differently, and what actually caused this price move.

## Positioning

Three mechanisms a neighbouring product could not truthfully copy:

1. **Bit-identical determinism across platforms.** The crate ships its own
   `exp`, `log`, `sin` and `cos` rather than calling the platform libm. Every
   release builds wheels for five targets, runs one fixed simulation inside
   each and compares digests; any disagreement fails the release. Verified in
   CI, not asserted.
2. **Readable ground truth.** The simulator computed every price, so the
   `truth` table reports one row per instrument per tick with seven factor
   contributions that sum to the move, residual around 1e-16.
3. **Runnable counterfactuals.** The same seed can be run with and without the
   user's orders, so every fill is priced against the world where they never
   traded.

The nearest open-source comparison, `bourse`, is a limit-order-book and
discrete-event toolkit with no price formation model, no macro layer and no
determinism claim. ABIDES is the academic multi-agent alternative.

## Operating Context

Used from Python, mostly in notebooks and training scripts. Results are read
into polars, pandas, pyarrow or duckdb over the Arrow C Data Interface. Runs
are archived as an order log plus a seed and a universe fingerprint so a
published result can be re-run by someone else.

## Capabilities and Constraints

Confirmed in the shipped package: `Engine`, `EngineBatch`, `Universe`, `Macro`,
`Scenario`, `Checkpoint`, agent evaluation with reference baselines and an
Oracle, ranking with paired sign tests, TCA, sweeps, replay, an SEC EDGAR
loader, a Gymnasium environment, and five Arrow output tables.

Stated limitations, which the documentation must keep visible rather than bury:

- Single venue, no fragmentation, no NBBO, no routing.
- Zero latency; orders arrive instantly.
- No strategic counterparties; the user trades against a market maker and
  aggregate flow, not agents that adapt.
- Return autocorrelation measures +0.219 at lag one where real equities sit
  near zero, so momentum is mechanically profitable here for reasons that do
  not transfer.
- Good results do not predict real returns. The price process comes from a
  known model.

Rates are fractional. Roster order is contractual. Every numeric column is f64.

Pre-release: the package is not yet published to PyPI or crates.io, and the
API may move before 1.0.

## Brand Commitments

Name: `pretium`, lowercase. Latin for *price*.

Voice, established and approved across the README: direct, concrete,
measurement-led, and willing to state limitations in the same breath as
capabilities. Specific bans, set by the plan owner: no "X is not the Y, it is
the Z" constructions, no marketing abstraction, ASCII punctuation only (no em
dashes, en dashes, typographic minus signs or arrows).

Licence Apache-2.0. The repository must not reference the commercial product
the engine was ported from.

**The visual identity is deliberately decoupled from the subject matter.** A
first attempt themed the documentation as trading-desk stationery, deriving
its world from markets. The plan owner rejected it: theming the library to
its domain reads as a game artifact and couples the library's identity to the
product it was extracted from. The standing commitment is the contemporary
enterprise documentation standard, executed straight, with the Anthropic and
Mintlify class of developer documentation as the craft bar. Neutral ground,
one accent, no costume, no subject-derived metaphor. Light and dark are both
supported.

## Evidence on Hand

Real, measured, and already in the docs: the five matching cross-platform
digests; the rebalance-frequency table (+88.7% / +30.9% / -13.2%); the
agent-versus-Oracle table over 384 agent-seed pairs; the paired sign tests;
the four realism statistics against real-equity ranges; the performance
timings; the volatile-regime TCA result over twelve seeds.

`examples/07-research-workflow.py` runs end to end in about fifteen seconds and is
exercised by the test suite.

No testimonials, customers, benchmarks against competitors, pricing or
adoption numbers exist. None may be invented.

## Product Principles

1. Measure rather than assert. Every claim in the documentation is a number
   that was produced by running something.
2. State the limitation next to the capability, never in a footnote.
3. Determinism is the product. Anything that would make two runs differ is a
   correctness bug, not a performance trade.
4. Show the library doing its job rather than describing it.
5. The reader's job leads. Both audiences reach their own path quickly.

## Accessibility & Inclusion

No product-specific requirement established beyond ordinary web accessibility:
the documentation must remain readable and navigable, with code legible at
normal sizes and sufficient contrast.
