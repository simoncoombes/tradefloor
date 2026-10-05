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
- **Named, frozen model presets.** Coefficients ship as twenty presets,
  `pt-v1` through `pt-v21` with no `pt-v17`, all selectable and all
  bit-reproducing. `pt-v21` is the default (`params::DEFAULT_PRESET_NAME`). A
  modified coefficient set fingerprints as `custom-XXXXXXXX` and can never
  present as a shipped one.
- **A published realism envelope.** A one-year realism table of 19 statistics
  graded against real-market bands (18 in band for `pt-v21`), and a long-run
  check of 40 registered rows (all met by `pt-v21` and by `pt-v20`), with the
  misses named as gaps rather than omitted.
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
    let bell = GameTime::new(9, 30, 3);
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
flat arrays takes every width from the crate root: `COMPONENT_COUNT` (12
numbers in an attribution row), `TICK_COMPONENT_COUNT` (9 in a tick row),
`RNG_STREAM_WIDTH` (5 per random stream), `ENGINE_RNG_STREAMS` (10 streams)
and the others listed in the `widths` module. `EngineRngState::to_words`
and `from_words` pack and read the streams at those widths. A width derived from
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

From 0.10.0 every public struct with public fields is
`#[non_exhaustive]`, so adding a field to one breaks no build, and so is
every public enum except `Side`, so adding a variant breaks none either. Outside this
crate you build one with its constructor or `Default`, then set or read
fields on the value. The next section lists the constructors.

## Upgrading to 0.10.0

The breaking changes in 0.10.0 are in how a host builds the crate's structs
and matches on its enums. No existing function, field or variant changed,
and a struct built the new way holds what the old literal held. The structs
a host fills in, such as `TickRequest`, `TickCompany`,
`EconomyState` and `RngState`, are now `#[non_exhaustive]`. Outside the
crate a struct literal no longer compiles, `..Default::default()` included,
and a pattern that destructures one needs a trailing `..`. Code that reads
or assigns fields is unchanged.

A tick request written as a literal on 0.9.1:

```rust,compile_fail,E0639
# use tradefloor::engine::TickRequest;
# use tradefloor::market::GameTime;
let request = TickRequest {
    time: GameTime { hour: 9, minute: 30, day_of_week: 3 },
    volatility_multiplier: 0.9,
    news: &[],
    news_impact_queue: &[],
    order_volumes: &[],
};
```

is built from its constructor on 0.10.0, which fills in a quiet tick, and
the fields you need are set on the value:

```rust
# use tradefloor::engine::TickRequest;
# use tradefloor::market::{GameTime, NewsEvent};
let mut news = NewsEvent::default();
news.company_id = Some("ACME-0".to_string());
news.price_impact = Some(0.02);
let news = [news];

let mut request = TickRequest::new(GameTime::new(9, 30, 3));
request.volatility_multiplier = 0.9;
request.news = &news;
```

A host-built company follows the same shape. `TickStock::new(price,
shares_outstanding)` opens the stock at `price` with everything else empty,
and `TickCompany::new(id, ticker, sector, stock)` lists it:

```rust
# use tradefloor::market::{TickCompany, TickStock};
let base = tradefloor::sectors::by_key("technology").unwrap().base_daily_variance();
let mut stock = TickStock::new(120.0, 5e7);
stock.avg_volume = 2e6;
stock.garch_variance = base;
stock.beta = Some(1.3);
let mut company = TickCompany::new("ACME-0", "ACME", "technology", stock);
company.eps = Some(6.0);
```

The constructors for the structs a host builds:

| Struct | Build it with |
|---|---|
| `market::GameTime` | `GameTime::new(hour, minute, day_of_week)` |
| `market::TickStock` | `TickStock::new(price, shares_outstanding)` |
| `market::TickCompany` | `TickCompany::new(id, ticker, sector, stock)` |
| `market::OrderVolume` | `OrderVolume::new(buy, sell)` or `Default` |
| `market::NewsEvent`, `market::NewsImpactEntry` | `Default` |
| `engine::TickRequest` | `TickRequest::new(time)` |
| `engine::DayCloseRequest` | `DayCloseRequest::new(daily_innovations, sector_base_variances)` |
| `engine::DayAdvanceRequest` | `DayAdvanceRequest::new(game_day, timestamp)` |
| `economy::EconomyState` | `create_initial_economy_state(&options)` or `Default` |
| `economy::InitialEconomyOptions` | `Default` |
| `economy::CentralBankState` | `CentralBankState::new(start_timestamp)` or `Default` |
| `economy::EconomicShock` | `EconomicShock::new(kind, severity, gdp_impact)` |
| `rng::RngState`, `engine::EngineRngState` | `from_words`, from what `to_words` wrote |
| `universe::InstrumentInit` | `InstrumentInit::new(ticker, sector, initial_price, shares_outstanding)` |
| `agent_book::AgentOrder` | `AgentOrder::new(id, agent, ticker, side, limit, quantity, mode)` |
| `agent_book::BookState`, `engine::GdpPublication` | `Default` |

The inputs to the model's component functions, such as
`market::TickInputs`, `economy::DailyInputs`, `market::CloseInputs` and
`microstructure::CompanyMicrostructure`, have a `new` or `Default` too, and
each one's documentation says what it fills in. Structs the engine only
hands back, such as `engine::DayAdvanceOutcome` and `agent_book::AgentFill`,
have no constructor because a host never builds one.

Every public enum except `order_book::Side` is `#[non_exhaustive]` as well,
so a release can add a variant. Outside the crate a `match` on one needs a
wildcard arm. `Side` has two variants and keeps them, so a match on it stays
as it is. The enums are `economy::CyclePhase`, `ForwardGuidance`,
`ShockKind` and `central_bank::Decision`, `market::MarketStatus`,
`AvgVolumePolicy` and `SettleDrawPolicy`, `engine::PriceField` and
`StopCondition`, `agent_book::RestMode` and `Liquidity`, `rates::CurvePoint`,
`rng::DrawKind` and `Site`, and `types::Difficulty`.

This match on `ShockKind`, written for 0.9.1 with no wildcard arm, no longer
compiles:

```rust,compile_fail,E0004
# use tradefloor::economy::ShockKind;
fn name(kind: ShockKind) -> &'static str {
    match kind {
        ShockKind::OilShock => "oilShock",
        ShockKind::Pandemic => "pandemic",
        ShockKind::War => "war",
        ShockKind::Other => "other",
    }
}
```

takes a wildcard arm on 0.10.0, which decides what a variant from a later
release means to the host:

```rust
# use tradefloor::economy::ShockKind;
fn name(kind: ShockKind) -> Option<&'static str> {
    match kind {
        ShockKind::OilShock => Some("oilShock"),
        ShockKind::Pandemic => Some("pandemic"),
        ShockKind::War => Some("war"),
        ShockKind::Other => Some("other"),
        _ => None,
    }
}
```

The random streams no longer need their fields named. `to_words` writes a
stream as `RNG_STREAM_WIDTH` numbers and the engine's ten as
`ENGINE_RNG_STATE_WIDTH`, and `from_words` reads them back:

```rust
# use tradefloor::engine::{Engine, EngineRngState};
# let companies = tradefloor::universe::random_universe(4, 1)
#     .iter().enumerate().map(|(i, g)| g.to_init().to_tick_company(i)).collect();
# let mut engine = Engine::new(
#     1,
#     companies,
#     Default::default(),
#     Default::default(),
#     tradefloor::sectors::keys().iter().map(|s| s.to_string()).collect(),
# );
let saved: Vec<f64> = engine.rng_state().to_words().to_vec();
assert_eq!(saved.len(), tradefloor::ENGINE_RNG_STATE_WIDTH);
engine.set_rng_state(EngineRngState::from_words(&saved)?);
# Ok::<(), String>(())
```

The words run in stream-id order (`rng::stream`: market, economy, external,
jumps, volume, news, volume_idio, overnight, market_vol_level,
crisis_epicentre), five per stream: the LCG state and increment as the raw
bits of an `f64`, the Box-Muller spare or NaN for none, and the two draw
counts. The fields are declared with volume_idio before news, so the
order differs from theirs in those two streams. A host that packed the fields in declaration order
and switches to `to_words` swaps those two streams in any state it saved
before the switch, once, when it loads it. The first two words are bit
patterns and some are NaNs, so move them as bytes: JavaScript may rewrite a
NaN's bits and JSON has no NaN. A rewritten NaN has an even increment
word, and `from_words` refuses one.

## Scope of this crate

The published crate carries the engine, the unit tests in its source
modules and ten integration tests that run standalone:
`circuit_breaker`, `depth_counterfactual`, `fed_put`, `host_constructors`,
`maker_ladder_allocations`, `platform_maths`, `postcut`, `roster_mutation`,
`snapshot_restore` and `stream_alignment`. The parity corpus that pins the
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
