# pretium

The Rust core of [pretium](https://github.com/simoncoombes/pretium): a
deterministic market simulator with a real limit order book.

A seed and a starting state run forward into prices, an order book with
depth, fills, volume, macro state and a full order log. The same seed
produces bit-identical output on Linux, macOS and Windows, because the
crate ships its own transcendental maths rather than calling the platform's
libm.

## What it gives you

- **Determinism across platforms.** A cross-platform gate runs on every
  release and compares digests of a fixed simulation on five targets. A
  seed, a roster and a model preset name are a complete specification of a
  market.
- **Emergent market impact.** Orders match against a real book with
  price-time priority, so a large order pays worse prices because it
  consumed levels, not because a slippage coefficient said so.
- **Ground truth.** The simulator knows the fair value it computed and the
  macro regime it is in, so both are readable. No real dataset has labels.
- **Named, frozen model presets.** Coefficients ship as `pt-v1` through
  `pt-v12`, all twelve selectable and all twelve bit-reproducing. `pt-v12` is
  the default (`params::DEFAULT_PRESET_NAME`). A modified coefficient set
  fingerprints as `custom-XXXXXXXX` and can never present as a shipped one.
- **A published realism envelope.** Fourteen statistics measured against
  real-market bands, with the misses named as gaps rather than omitted.
  See <https://simoncoombes.github.io/pretium/realism-envelope.html>.

## Using it

```rust
use pretium::engine::Engine;
use pretium::universe::random_universe;

let companies = random_universe(20, 7)
    .iter()
    .enumerate()
    .map(|(i, g)| g.to_init().to_tick_company(i))
    .collect();

let mut engine = Engine::new(
    42,
    companies,
    pretium::economy::create_initial_economy_state(&Default::default()),
    pretium::economy::create_initial_central_bank_state(0),
    pretium::sectors::keys().iter().map(|s| s.to_string()).collect(),
);
engine.close_day(0);
let prices = engine.prices();
```

The Rust API is the engine itself and is low level: it takes tick requests
and day advances and hands back state. Most users want the Python package,
which wraps this crate and adds universes, scenarios, checkpoints, an
Arrow bar reader, strategy evaluation and the realism panel:

```
pip install pretium
```

Documentation, including the realism envelope and what the simulator is not
suitable for, is at <https://simoncoombes.github.io/pretium/>.

## Scope of this crate

The published crate carries the engine, its unit tests and three
integration tests that run standalone. The parity corpus that pins this
port against the reference implementation is 140 MB of fixtures and stays
in the repository, so the tests that read it are excluded here rather than shipped
in a state where they cannot pass.

That corpus is not run by CI. The two workflows -- `determinism.yml` and
`release.yml` -- build wheels and compare known-answer digests; neither
invokes `cargo` at all. `cargo test --offline` is a manual step in
`RELEASING.md`, run against the repository and again against the packaged
crate before a publish. It is a release checklist item, not automation, and
saying so beats implying a gate that does not exist.

## Licence

MIT or Apache-2.0, at your option. See `LICENSE-MIT` and `LICENSE-APACHE`.
