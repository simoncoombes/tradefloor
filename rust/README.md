# tradefloor

The Rust core of [tradefloor](https://github.com/simoncoombes/tradefloor): a
deterministic market simulator with a real limit order book.

A seed and a starting state run forward into prices, an order book with
depth, fills, volume, macro state and a full order log. The same seed
produces bit-identical output on Linux, macOS and Windows, because the
crate ships its own transcendental math rather than calling the platform's
libm. A cross-platform gate runs on every release and compares digests of
a fixed simulation on five targets, so a seed, a roster and a model preset
name are a complete specification of a market.

Orders match against a real book with price-time priority, and a large
order pays worse prices because it consumed levels of that book. The
simulator knows the fair value it computed and the macro regime it is in,
so both are readable.

Coefficients ship as eighteen frozen presets, named `pt-v1` through
`pt-v19` with `pt-v17` reserved for the `preset/pt-v17` branch. All
eighteen are selectable and each one bit-reproduces. `pt-v19` is the
default (`params::DEFAULT_PRESET_NAME`). A modified coefficient set
fingerprints as `custom-XXXXXXXX` and can never present as a shipped one.

Fourteen statistics are measured against real-market bands and published
as a realism envelope at <https://tradefloor.dev/realism-envelope.html>.
The statistics that fall outside their band are named there as gaps.

## Using it

```rust
use tradefloor::engine::Engine;
use tradefloor::universe::random_universe;

let companies = random_universe(20, 7)
    .iter()
    .enumerate()
    .map(|(i, g)| g.to_init().to_tick_company(i))
    .collect();

let mut engine = Engine::new(
    42,
    companies,
    tradefloor::economy::create_initial_economy_state(&Default::default()),
    tradefloor::economy::create_initial_central_bank_state(0),
    tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect(),
);
engine.close_day(0);
let prices = engine.prices();
```

The Rust API is the engine itself and is low level: it takes tick requests
and day advances and hands back state. Most users want the Python package,
which wraps this crate and adds universes, scenarios, checkpoints, an
Arrow bar reader, strategy evaluation and the realism panel:

```
pip install tradefloor
```

Documentation, including the realism envelope and what the simulator is not
suitable for, is at <https://tradefloor.dev/>.

## Scope of this crate

The published crate carries the engine, its unit tests and three
integration tests that run standalone. The parity corpus that pins this
port against the reference implementation is 140 MB of fixtures and stays
in the repository, so the tests that read it are excluded here rather than shipped
in a state where they cannot pass.

That corpus is not run by CI. The two workflows, `determinism.yml` and
`release.yml`, build wheels and compare known-answer digests, and neither
invokes `cargo` at all. `cargo test --offline` is a manual step in
`RELEASING.md`, run against the repository and again against the packaged
crate before a publish.

## License

MIT or Apache-2.0, at your option. The full texts are in `LICENSE-MIT` and
`LICENSE-APACHE`.
