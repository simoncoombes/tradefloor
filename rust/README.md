# tradefloor

The Rust core of [tradefloor](https://github.com/simoncoombes/tradefloor): a
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
- **Named, frozen model presets.** Coefficients ship as nineteen presets,
  `pt-v1` through `pt-v20` with no `pt-v17`, all selectable and all
  bit-reproducing. `pt-v20` is the default (`params::DEFAULT_PRESET_NAME`). A
  modified coefficient set fingerprints as `custom-XXXXXXXX` and can never
  present as a shipped one.
- **A published realism envelope.** A one-year realism table of 19 statistics
  graded against real-market bands, and a long-run check of 17 criteria (40
  rows registered for `pt-v20`, all met), with the misses named as gaps
  rather than omitted.
  See <https://tradefloor.dev/realism-envelope.html>.

## Using it

A trading day is three calls: `open_market`, `run_session` for the ticks,
then `close_day`, which settles the day and steps the economy. This runs
five days of a 20-name market, with 390 one-minute ticks a day as the
Python package does:

```rust
use tradefloor::economy::{create_initial_central_bank_state, create_initial_economy_state};
use tradefloor::engine::{Engine, SessionBuffer, SessionRequest};
use tradefloor::market::GameTime;
use tradefloor::universe::random_universe;

let seed = 42;
let companies = random_universe(20, seed)
    .iter()
    .enumerate()
    .map(|(i, g)| g.to_init().to_tick_company(i))
    .collect();

let mut engine = Engine::new(
    seed,
    companies,
    create_initial_economy_state(&Default::default()),
    create_initial_central_bank_state(0),
    tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect(),
);

let opening = engine.prices();
let mut buffer = SessionBuffer::new();
for day in 1..=5 {
    engine.open_market();
    let bell = GameTime { hour: 9, minute: 30, day_of_week: 3 };
    engine.run_session(&SessionRequest::new(bell, 390), &mut buffer);
    engine.close_day(day);
}
assert_ne!(engine.prices(), opening);
```

`close_day` on its own does not trade, so a loop that skips `run_session`
leaves every price where it started. `SessionRequest::new` is a session with
no news and no orders; set its `news`, `order_volumes` or `fills` fields to
add them. `SessionBuffer` holds the last session's prices, volumes and
attribution, one row per tick.

The Rust API is the engine itself and is low level: it takes tick requests
and day advances and hands back state. Most users want the Python package,
which wraps this crate and adds universes, scenarios, checkpoints, an
Arrow bar reader, strategy evaluation and the realism panel:

```sh
pip install tradefloor
```

Documentation, including the realism envelope and what the simulator is not
suitable for, is at <https://tradefloor.dev/>.

## Upgrading from 0.8.1

The crate follows the Python package's version, and 0.8.5 breaks Rust code
written against 0.8.1 even though Cargo treats 0.8.5 as a compatible update.
Seeds are `u64` rather than `u32`, several public structs gained fields, the
default preset is `pt-v20` rather than `pt-v19`, and
`Engine::tick_components` rows have nine entries rather than eight.
The repository's
[CHANGELOG](https://github.com/simoncoombes/tradefloor/blob/main/CHANGELOG.md)
lists every change under "The Rust crate since 0.8.1". Pin `tradefloor = "=0.8.1"` to stay on the old API.

`ModelParams` and `SessionRequest` are now `#[non_exhaustive]`, so adding a
field to either no longer breaks a build. Make them with
`ModelParams::preset`, `with_override` and `SessionRequest::new`.

## Scope of this crate

The published crate carries the engine, the unit tests in its source
modules and six integration tests that run standalone:
`circuit_breaker`, `depth_counterfactual`, `maker_ladder_allocations`,
`platform_maths`, `roster_mutation` and `stream_alignment`. The parity corpus that pins the
engine's output is 140 MB of fixtures and stays in the repository, so the
tests that read it are left out of the package rather than shipped in a
state where they cannot pass.

In the repository, the `suite.yml` workflow runs `cargo test --release`
with the parity corpus, `cargo clippy` with warnings as errors, a build on
the declared minimum Rust (1.83) and `cargo doc` with warnings as errors.
Before it publishes, `release.yml` packages the crate and runs
`cargo test --offline` on the package.

## Licence

MIT or Apache-2.0, at your option. See `LICENSE-MIT` and `LICENSE-APACHE`.
