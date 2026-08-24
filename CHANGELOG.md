# Changelog

Notable changes to `pretium`. Dates are the day the work landed.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [semantic versioning](https://semver.org/). One
project-specific rule: **a change to the simulated trajectory is a breaking
change**, whatever its size. A market that runs differently from the same
seed invalidates every published result that cited it, so those changes are
called out here explicitly and carry a new model preset rather than editing
an existing one.

## [Unreleased]

### Added

- **`pt-v4` model preset**, selectable and deliberately *not* the default.
  pt-v3 plus five endogenous jump coefficients, three volume-process
  parameters and `market_factor_sigma`. It halves the dual-horizon
  calibration objective (0.9887 → 0.4863 on training seeds, 1.3990 → 0.7493
  on thirty seeds it never saw) and is the first vector to close the
  thin-tails gap: `excess_kurtosis` at 504 days moves 5.23 → 9.19, inside
  the 7.1–22 band.

  It is a trade, not a free win. At the certified 252-day horizon it holds
  eight of ten statistics in band against pt-v3's nine, surrendering
  `return_acf1`. **pt-v3 for horizons at or under a year; pt-v4 for
  multi-year questions.**

- **WebAssembly build** (`--features wasm`, `wasm32-unknown-unknown`), so
  the engine runs in a browser. Verified bit-identical with the native
  build on a fixed simulation. 182 KB raw, 68 KB gzipped. Build and check
  with `tools/wasm/build.sh`.

- **MCP server** (`pip install "pretium[mcp]"`, run `pretium-mcp`), giving
  an LLM agent eleven read-only tools over the simulator. Strategies,
  universes and scenarios are composed as data; there is no path from a
  tool argument to code execution. Every result carries computed caveats
  and full provenance.

- **`envelope.score`** and **`envelope.regressions`** — read a measured
  panel against the bands for its own horizon, and name the statistics a
  candidate would surrender relative to the shipped preset.

- **`engine::fixed_simulation_digest`** — one fixed simulation, hashed, and
  exposed identically to both bindings, so cross-language determinism is a
  measurement rather than an argument.

### Changed

- The **day loop** — the macro step, the day close, and the day-zero
  instrument mapping — moved from the Python binding into the core, so the
  browser and Python surfaces run one implementation. Behaviour-neutral:
  the known-answer digest is unchanged.

### Fixed

- The **cross-platform determinism gate** now runs and passes on every
  shipped target. `macos-x86_64` and `windows-x86_64` were the last two
  unverified, and both reproduce
  `5bd011be292f823ce1c360d1a12bf46de3362deee058a37283c74ab47069d0c1`.

- Two **parity test suites** (`microstructure_parity`, `economy_parity`) had
  not compiled since the features that changed their option structs, so the
  evidence for the bit-identical-port claim was not running.

## [0.1.0] — unreleased

First public release. Pre-release: not yet on PyPI or crates.io, and the
API may move before 1.0.

### Added

- Deterministic equity market simulation: price formation, a real limit
  order book with queue position and partial fills, macro state, and a
  five-phase business cycle.
- Bit-identical determinism across platforms, verified in CI rather than
  asserted. The crate ships its own `exp`, `log`, `sin` and `cos` rather
  than calling the platform libm.
- Agent evaluation with reference baselines and an oracle, ranking with
  paired sign tests, transaction cost analysis, sweeps, replay, and
  checkpoint/branch.
- A Gymnasium environment and five Arrow output tables.
- An SEC EDGAR loader for real fundamentals.
- The **realism envelope**: ten statistics measured against real-market
  bands, with the gaps named rather than hidden. Nine of ten in band at the
  certified 252-day horizon.
- **Ground truth**: seven factor contributions per instrument per tick that
  sum to the move — a labelled dataset no historical source can provide.
