# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Four audiences. The first two carry equal weight and were confirmed by the
plan owner; the third and fourth were added on 2026-08-28 when the docs
site began naming them, so that this document and the front door agree
about who the product is for.

**LLM and tool-using agent researchers** evaluating agents that reason, call
tools, read state and make a sequence of trading decisions. The MCP server
is the interface they use.

**Benchmark builders** who need another researcher to recreate the exact
environment: the `RunManifest` and a `reproduce()` that refuses on mismatch.

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

`tradefloor` simulates an equity market that a strategy can be run against. Given
a seed, a roster of companies and a macro state, it runs prices, a limit order
book, fills and macro state forward. Orders match against real depth, so
trading moves the price.

It exists because historical data cannot answer two questions: what would have
happened had I traded differently, and what actually caused this price move.

## Positioning

Three mechanisms a neighbouring product could not truthfully copy:

1. **Bit-identical determinism across platforms.** Every transcendental goes
   through `rust/src/mathx.rs`, which routes `exp`, `log`, `pow`, `sin` and
   `cos` to the pure-Rust `libm` crate rather than to the platform libm, which
   is MSVC's CRT on Windows, glibc on Linux and Apple's on macOS and which do
   not agree with each other. A grep test,
   `no_std_transcendentals_outside_mathx` in `rust/tests/mathx_parity.rs`,
   fails if anything calls `f64::exp` and friends directly. Every release then
   builds wheels for five targets, runs one fixed simulation inside each and
   compares digests; any disagreement fails the release. Verified in CI, not
   asserted.
2. **Readable ground truth.** The simulator computed every price, so the
   `truth` table reports one row per instrument per tick with nine factor
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
loader, a Gymnasium environment, an MCP server (`tradefloor.mcp`), and five
Arrow output tables. Twelve model presets ship, `pt-v1` through `pt-v12`, all
selectable and all reproducing bit for bit; `pt-v12` is the default.

Stated limitations, which the documentation must keep visible rather than bury:

- Single venue, no fragmentation, no NBBO, no routing.
- Zero latency; orders arrive instantly.
- No strategic counterparties; the user trades against a market maker and
  aggregate flow, not agents that adapt.
- The realism envelope is certified to 252 trading days and no further
  (`envelope.CERTIFIED_HORIZON_DAYS`). Five gaps stand behind that number:
  horizon, decay-shape, scenario-magnitude, macro-range and
  roster-concentration, each enumerated in `tradefloor.envelope.GAPS` with what
  it forbids.
- Volatility memory decays exponentially rather than hyperbolically. The
  log-log slope over lags 1 to 20 measures -0.953 against real markets'
  -0.436 (`envelope.DECAY_SLOPE` and `REAL_DECAY_SLOPE`), so an edge keyed on
  volatility memory beyond about lag 20 -- vol targeting, risk parity on a
  one-month or longer estimate -- does not transfer.
- Good results do not predict real returns. The price process comes from a
  known model.

Rates are fractional, roster order is contractual, and every numeric column
is f64.

Published on PyPI as `tradefloor` and on crates.io as the `tradefloor` crate. The
API may still move before 1.0.

## Brand Commitments

Name: `tradefloor`, lowercase. The former name, `pretium`, was Latin for
*price*; the current one carries no such derivation.

<!-- prose: off -->
Voice: reference documentation. Flat, declarative, unmemorable. The reader is
scanning for a fact, not being persuaded. This binds all prose in this
repository and in `tradefloor-docs`: documentation, code comments, commit
messages, pull request titles and bodies, UI copy, labels and error text.

Rules, set by the plan owner:

- Headings and labels are noun phrases. No commas, no trailing clauses, no
  verbs. "Excluded tables", not "What was left alone, and why".
- Every sentence has a subject and a finite verb. No verbless fragments.
- Never tell the reader that something is significant. Delete "and that is the
  point", "which is why this matters", "that is the whole idea".
- Do not define things by negation. No "X is not Y", "not X but Y", "never
  wrong, only incomplete". Say what it is.
- No three-sentence assert, negate, resolve sequences. If three consecutive
  sentences are each under ten words, merge them.
- No sentence under six words may end a paragraph or a section.
- Abstract subjects take plain verbs. "The check failed on 3 of 120 seeds", not
  "the identity held through a crisis".
- No em-dash asides, no rhetorical questions, no single-sentence paragraphs for
  emphasis.
- Prefer the specific number, filename, or field name over a characterisation
  of it.
- ASCII punctuation only. No em dashes, en dashes, typographic minus signs or
  arrows.

When a sentence could open a blog post, rewrite it.

`tools/docs/learn/prose.py` in `tradefloor-docs` checks the mechanical rules
and reports file, line and text. It cannot see register, so a clean run is not
a passing grade.
<!-- prose: on -->

Dual-licensed MIT OR Apache-2.0, at the user's option, which is the
Rust-ecosystem norm and what `pyproject.toml` and `rust/Cargo.toml` both
declare. The repository must not reference the commercial product the engine
was ported from.

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
digests, one per wheel target, compared inside the release workflow; the
rebalance-cadence measurement, which on `pt-v12` swings one signal from
+37.55% at three decisions a day to +9.79% at six and -27.46% at twelve
(seed 2026, `Universe.random(40, seed=7)`, 30 days; the method is written
out in `docs/strategy-specs.md` and re-runs under
`tools/remeasure/remeasure.py --only rebalance`); the
agent-versus-Oracle table over the twelve-market grid; the paired sign tests
beside it; the fourteen realism statistics against real-equity ranges; the
performance timings; the round-trip shortfall range, -17.72 to +2.03 bps over
eight sim seeds.

`examples/07-research-workflow.py` runs end to end in about five seconds on
an idle Apple-silicon laptop, the figure `README.md` and `examples/README.md`
both publish, and is exercised by the test suite. A wall-clock time is
machine-bound rather than reproducible, which is the status `tools/remeasure`
gives every timing it carries rather than claiming to reproduce one.

No testimonials, customers, benchmarks against competitors, pricing or
adoption numbers exist, and none may be invented.

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
