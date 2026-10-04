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
  See <https://docs.tradefloor.dev/how-its-measured.html>.

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

To pause a run and continue it later, possibly in another process,
`engine.snapshot()` captures the whole state and `engine.restore(&snapshot)`
puts it back onto an engine built the same way, and the restored run
continues bit for bit. The `snapshot` module documents the fields, the checks
a restore makes, and `EngineSnapshot::to_bytes` and `from_bytes`, which give a
snapshot one exact binary form to store.

Every preset is fitted with the engine making its own news and jumps, one
economy step a session and no economic shocks. A host that adds its own
news, shocks or earnings revisions can keep a `tradefloor::flow::ExternalFlow`
beside the engine, feed it what it passes in, and call `assess` with the
engine's `params()` to learn which channels are outside the fitted flow.
`tradefloor::flow::CalibratedFlow::of` states that flow for any preset.

The Rust API is the engine itself and is low level: it takes tick requests
and day advances and hands back state. Most users want the Python package,
which wraps this crate and adds universes, scenarios, checkpoints, an
Arrow bar reader, strategy evaluation and the realism panel:

```sh
pip install tradefloor
```

Documentation, including the realism envelope and what the simulator is not
suitable for, is at <https://docs.tradefloor.dev/>.

## Driving the engine from a host

A host that owns the economy (a game, a trading desk simulator, anything
that feeds the engine its own macro state and news) needs four things from
this crate that the example above does not show.

**Keep your opening.** `Engine::new` and `Engine::with_params` run the
preset's own opening over the economy you pass: on `pt-v20` that is 755
days of the macro step before day 1 and a draw of the business cycle's
phase. That is right for the library's default economy, which would
otherwise open every run in expansion, and wrong for yours: an economy
passed in at a VIX of 45 in a contraction opens at 22.28 in an expansion.
Build with `Engine::with_params_keeping_opening` to keep exactly what you
pass, and check `engine.opening_settled()` once after construction if you
want a refactor that swaps the constructor to fail loudly. The burn-in
arrived with `pt-v18` in 0.7.0; presets before it have none.

**Size buffers from the width constants.** A host that saves state into
flat arrays takes every width from the crate root: `COMPONENT_COUNT` (11
numbers in an attribution row), `TICK_COMPONENT_COUNT` (9 in a tick row),
`RNG_STREAM_WIDTH` (5 per random stream), `ENGINE_RNG_STREAMS` (10 streams)
and the others listed in the `widths` module. A width derived from
something else, such as `S_COMPONENT_KEYS.len() + 1`, keeps compiling when
the engine gains a slot and under-sizes the buffer. A test pins every
value, and a change to one is a breaking change with its own CHANGELOG
line.

**Measure a day change from `prior_closes`.** Each stock's
`previous_close` is reset at `open_market` to the day's opening price,
after the overnight gap and after the close re-marks prices to newly
published macro data. It anchors the session's 25 per cent circuit-breaker
band and the daily return GARCH reads, so it measures open to now. For a
close-to-close change use `engine.prior_closes()`, the previous session's
last print, and `engine.last_closes()` for the session just closed. On
`pt-v20` the two anchors differ on every name every day, by a median of
0.12 per cent. Neither is part of the trajectory; a host that saves an
engine between days carries them with `restore_closes`.

**Read the crisis line from the engine.** The VIX level above which the
crisis behaviour runs is a preset coefficient, 30.88325108 from `pt-v13` on
and 25.5 before. Read it with `engine.crisis_vix_threshold()` rather than
copying it, and `engine.vix_above_crisis_threshold()` applies the same
strict test the engine's gates use. It is settable like any coefficient:
`ModelParams::preset("pt-v20")?.with_override("crisis_vix_threshold", x)`.
The dollar's safe-haven bid has its own threshold,
`usd_crisis_vix_threshold`, 25.5 on every preset.

## Versions and API stability

The crate takes the Python package's version number and, from 0.10.0,
follows [Cargo's semver rules](https://doc.rust-lang.org/cargo/reference/semver.html)
for its public API. While the version is 0.x, a minor release (0.10 to
0.11) may break the API and a patch release (0.10.0 to 0.10.1) does not.
So `tradefloor = "0.10"` takes every patch release safely. The release
workflow runs `cargo semver-checks` against the newest published crate and
refuses to publish a patch release that breaks the API. Every break in a
minor release is listed in the CHANGELOG, and so is every change to a
state width or to what a constructor does with its arguments, which the
compiler cannot catch for you.

Releases before 0.10.0 did not follow this. 0.8.5 broke code written for
0.8.1 in a patch release; the CHANGELOG lists every change under "The Rust
crate since 0.8.1", and `tradefloor = "=0.8.1"` stays on the old API.

`ModelParams`, `SessionRequest`, `SessionBuffer`, `SessionOutcome`,
`TickTruth` and both `TickOutcome` structs are `#[non_exhaustive]`, so
adding a field to one breaks no build. Build them with
`ModelParams::preset`, `with_override`, `SessionRequest::new` and
`SessionBuffer::new`, then set or read fields on the value.

## Scope of this crate

The published crate carries the engine, the unit tests in its source
modules and seven integration tests that run standalone:
`circuit_breaker`, `depth_counterfactual`, `maker_ladder_allocations`,
`platform_maths`, `roster_mutation`, `snapshot_restore` and
`stream_alignment`. The parity corpus that pins the
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
