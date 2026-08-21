# pretium — migration handoff

State as of the WP7 spike + core migration. Written because a second agent is
working on the same move; this is what exists so the two efforts can merge
instead of colliding.

## What is done

`rust/` is now the full engine, not a placeholder.

- The `mc-engine-core` sources (23 modules, 15 test targets, 9 examples) were
  **copied** from `mc-port/crates/mc-engine-core` and the crate renamed to
  `pretium`. All 267 tests pass here.
- Python bindings live in `rust/src/python.rs`, behind a `python` feature.
  Feature-gated so the game's WASM build never compiles PyO3, and so
  `cargo test` links normally — `pyo3/extension-module` is attached to the
  feature, not the dependency, because it makes the linker expect symbols the
  interpreter supplies at load time, which is right for a wheel and fatal for
  a test binary.
- `rust/src/units.rs` is the single fractional↔percent boundary (settled
  decision: fractional at the Python edge, `0.045` means 4.5%). `check_rate`
  is exposed to Python already, before the valuation surface that will call
  it, because an unenforced convention is a comment.
- `maturin build -F python` produces `pretium-0.0.0-cp311-abi3-win_amd64.whl`.
  Verified: **384/384 draws bit-exact against the TypeScript goldens**, from
  Python, after the migration.

## What is NOT done, and must not be assumed

1. **mc-port is untouched.** This was a copy, not a move.
   `mc-port/crates/mc-engine-core` still exists, the game still builds, the
   oracle is intact (`ec3f42be…`). Deleting it and repointing
   `mc-engine-wasm` at pretium is the remaining half of the migration and has
   not been started. Until it is, the two copies **will drift** — treat
   pretium as authoritative and mc-port's copy as stale from now on.

2. **The 135 MB of goldens are NOT committed.** They were copied to
   `rust/goldens/` and gitignored, because committing 135 MB to a fresh repo
   is a hard-to-reverse decision that belongs to the combined effort, not to
   one agent. Tests read them from `rust/goldens/` and will fail without
   them. `Cargo.toml` already `exclude`s them so they can never ride into a
   published sdist. Someone still has to decide how CI gets them: commit
   them, Git LFS, or generate on demand from mc-port.

3. **`rust/goldens/` came from mc-port and is regenerated there.** The 15
   TypeScript generators in `mc-port/scripts/rust-port/` import the live TS
   engine and cannot move. Goldens are generated in mc-port and consumed
   here — a cross-repo artifact flow that needs a documented procedure.

4. **Version is still 0.0.0.** The settled decision is that Layer 1 ships as
   0.1.0; the bump belongs to the release, not to this migration.

## Scrub status

Settled: the package must not reference margincall anywhere.

- `the game` — 10 in `src`, 2 in tests. **Still present, still to scrub.**
- `margin call` — 2 in `src`. **Still present, still to scrub.**
- `the TypeScript` — 77 across src and tests. **Decided: keep.** They do not
  name margincall, and they are the rationale for why the Rust looks odd in
  specific places (redundant VIX guards, JS truthiness vs nullish, `-0`
  handling). Deleting them would remove the reasoning and leave the quirks.

## Open, and not mine to settle

- §10.2 — how much of `economy.ts` the Python surface exposes.
- §10.5 — extended hours and closed-market ticks.

Both are the plan owner's; the design doc's working assumptions stand until
answered.

## Doc correction already applied

`docs/PYTHON-API-DESIGN.md` §5 claimed cross-OS determinism was "enforced by
tests". It is not: every measurement was taken on Windows, the crate has never
run on Linux or macOS, and the only CI workflow is single-OS. §9.1 already
stated the honest position ("a grep test and an argument"); §5 now carries a
measurement-status table saying which parts are enforced and which are argued.

Note `docs/` is gitignored in this repo, so that correction is on disk and
untracked.
