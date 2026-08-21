# pretium — state of play

## Architecture

```
pretium/rust          the Rust core of the economic simulator
                      + Python wrapper behind the `python` feature
mc-port/crates/
  mc-engine-wasm      separate TS/WASM wrapper, depends on pretium
pretium-design        private; PYTHON-API-DESIGN.md (the WP7 spec)
```

The core is consumed twice and lives once. The Python wrapper is feature-gated
rather than a separate crate, so there is one thing to publish; the TS wrapper
is separate because it lives with the application that uses it.

## Done

- **Core migrated out of mc-port.** `crates/mc-engine-core` deleted there; the
  27 modules, 15 test targets and 9 examples live here as `pretium`. Verified
  by hashing every file in both trees *before* deleting anything — only
  `lib.rs` differed, as expected. 271 tests pass, clippy clean.
- **mc-port repointed.** `mc-engine-wasm` is a wrapper over `pretium`: 11/11,
  clippy clean, tsc clean, Next.js production build succeeds, oracle intact at
  `ec3f42be`.
- **PyO3 + maturin proven end to end.** `maturin build -F python` produces an
  abi3 wheel for CPython ≥3.11. **384/384 draws bit-exact against the
  TypeScript goldens, from Python.**
- **Units boundary.** `src/units.rs` is the single place a factor of 100
  exists. `check_rate` is exposed to Python and names the percent-versus-
  fractional confusion explicitly, because that is what an out-of-range rate
  almost always is.
- **Sector table.** `src/sectors.rs` — the twelve sectors, in contractual
  declaration order. The core previously took `sector_avg_pe` as a
  pre-looked-up number on the grounds that "the TypeScript owns the table";
  that reasoning expired when this crate became the library. Anchors are
  gated against `meta.sectorAnchors` in `goldens/fairvalue.json`.
- **Provenance scrub.** No references to the game or the product name remain
  in `src/` or `tests/`. The 28 dangling citations to the port's internal
  documents are gone too — they pointed at files that will never ship beside
  this crate.
  - **Deliberately kept:** `factors.rs` "margin calls go out on yesterday's
    close" is the real financial term, not the product name. A blind replace
    would have mangled correct domain language.
  - **Deliberately kept:** the ~77 "the TypeScript" comments. They do not name
    the product, and they are the reasoning for why the Rust looks odd in
    specific places (redundant VIX guards, JS truthiness vs nullish, `-0`).
    Decided by the plan owner.
- **Golden manifest hole closed.** `index.json` covered only 20 of 46 golden
  files — everything from WP2–WP5 was uncovered, including `fairvalue.json`,
  `mathx-v8.json` and `orderbook.json`. `mc-port/scripts/rust-port/rebuild-
  golden-manifest.mjs` now walks the directory rather than trusting a
  generator to declare itself. 46/46 covered.
- **`sync-goldens.py`** replaces the manual 135 MB copy and verifies against
  the manifest, because a truncated vector set is otherwise a silent pass.

## Open — plan owner's, not settleable by implementation

1. **§10.5** — extended hours and closed-market ticks.
2. **Does `mispricing.rs` join the 0.1.0 surface?** The recorded decision named
   four leaf modules and this landed after. Recommendation: yes, now that the
   daily step is the headline public model.
3. **Licensing.** Undecided, and it blocks publication. `Cargo.toml` currently
   says Apache-2.0 as a placeholder — that is a default, not a decision.
4. **RNG stream-splitting.** Deferred in the port notes pending justification;
   three consumers now want draw alignment (TCA counterfactual, pinned-vs-
   baseline macro, cutover). Worth pricing before an API hardens around the
   measuring approach.

## Known gaps

- **Goldens are not committed.** 135 MB, gitignored, `exclude`d from any
  published sdist. Tests fail without them present — the honest failure. CI
  still needs a source: commit, LFS, or generate on demand.
- **Cross-repo coupling.** mc-port path-depends on `../../../pretium/rust`, so
  building the game needs this checkout adjacent. Acceptable in that direction
  only: mc-port is private, pretium is destined for publication.
- **Sector volatility has no golden gate.** `fairvalue.json` pins `avgPe`
  only. The twelve volatility values were transferred at migration and are
  guarded by a unit test alone.
- **Version is 0.0.0.** Layer 1 ships as 0.1.0; the bump belongs to the
  release.
- **Cross-OS determinism is argued, not measured.** Every measurement was
  taken on Windows. See the §5 correction in `pretium-design`.
