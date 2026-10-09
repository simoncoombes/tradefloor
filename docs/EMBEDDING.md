# Embedding the engine in a host

A game or a trading-desk simulator can drive the tradefloor Rust crate's
`Engine` itself, instead of running it through the Python package's
`run_days`, and keep the realism pt-v21 was certified on. That takes the
right opening, a day loop that matches the library's, and a limit on how
much news and how many shocks of your own you add. Every preset's
statistics, and pt-v21's certification, were measured on the library's own
day loop with no outside input. The tables below show how much a host's
choices move them.

All of them were measured on pt-v21, over 20 seeds and 504 sessions,
on a 108-name roster of nine names a sector (one mega cap, two large, three
mid and three small), with the market opened on the host's economy (a kept
opening, below) unless a table says otherwise. The host flow in them is a
browser game's, rebuilt from its source: its news, its economic shocks, its
stock splits and the writes it makes to the economy, at the rates and sizes
it uses. The rebuild leaves out the game's storylines and its political and
bankruptcy writes to the VIX. Index returns are price returns, without
dividends.

## Opening the market

The constructor decides what happens before the first session.

| constructor | `opening_settled()` | what runs before the first session on pt-v21 |
|---|---|---|
| `Engine::new`, `Engine::with_params` | `true` | 755 days of the macro step from the economy you pass, a draw of the business cycle's phase and age, then 504 sessions of market prehistory that hand back the volatility state and the valuation state |
| `Engine::with_params_keeping_opening` | `false` | nothing: the engine opens on exactly the economy and central bank you pass |
| `Engine::with_params_from_opening(.., settle_opening)` | `settle_opening` | the first row when `true`, the second when `false` |

The certification ran on the first row, so every long-run criterion was read
on a market that had lived its prehistory. With a kept opening the
volatility state starts where the constructor seeds it: the market factor's
variance at `market_factor_sigma` squared, no memory in the VIX's slow
levels, no cycle multiplier, and each name's GARCH variance as you passed
it. A market in an expansion holds less volatility than that, so the first
quarter runs hot until the state relaxes. Here the opening economy is
`create_initial_economy_state`'s default, an expansion at age zero with a
VIX of 15, the days are numbered and there is no host flow:

| opening | first 63 sessions: index volatility, VIX mean | 504 sessions: index volatility, VIX mean | VIX above 30 / above 40 | sessions in a contraction |
|---|---|---|---|---|
| library (`with_params`) | 11.6%, 15.4 | 13.3%, 15.5 | 3.4% / 1.1% | 5.5% |
| kept (`with_params_keeping_opening`) | 14.0%, 17.1 | 12.8%, 15.7 | 2.3% / 0.3% | 0.7% |

The kept opening also starts the cycle at age zero in an expansion on every
seed, so its first two years see fewer downturns than the library's, which
draws the phase.

Which to use:

- A new market whose economy is `create_initial_economy_state`'s default:
  use `with_params`. Settling that economy is what the burn-in is for, and
  keeping it gains nothing. Read the settled economy back with
  `engine.economy()` rather than writing yours over it, since a write after
  construction undoes the settling.
- A market restored from a save: `Engine::restore` from what
  `Engine::snapshot` wrote. It brings the volatility state back with
  everything else, where a kept opening rebuilds only the economy.
- A host that must open on a macro state of its own, such as a scenario
  that starts in a recession: `with_params_keeping_opening`, and expect the
  first quarter to run hotter than the certified rows describe. Assert
  `!engine.opening_settled()` once after construction, so a refactor that
  swaps the constructor fails loudly.

No constructor keeps a host's economy and plays the prehistory inside it.

The prehistory makes a library opening slow to build. On an Apple M5, a
native release build of 108 names took 7.6 to 9.6 seconds cold (six runs,
median 8.7) and 20 names about 1.9. A kept opening builds in under a
tenth of a millisecond. The engine keeps the last 16 engines whose build
played a prehistory, so building the same seed, roster, economy and model
again is a copy of the first, the same engine to the bit, in about half a
millisecond. A game that starts each new game on a new seed pays the cold
build every time, so build it off the thread that draws the screen.
`Engine::set_opening_cache_capacity` sizes the cache (0 turns it off) and
`Engine::opening_cache_info` reports what it holds.

## Running a day

The day loop the certification ran, which the Python package's `run_days`
also runs:

```rust
use tradefloor::engine::{Engine, SessionBuffer, SessionRequest};
use tradefloor::market::GameTime;

let mut buffer = SessionBuffer::new();
for day in 0..days {
    engine.set_current_day(day as i64);
    engine.open_market();
    let open = GameTime::new(9, 30, 3);
    engine.run_session(&SessionRequest::new(open, 390), &mut buffer);
    engine.close_day(day as i64 + 1);
}
```

A host that ticks minute by minute with `Engine::tick` instead of
`run_session` gets the same market, and the same rules apply.

Number the days. `open_market`, `run_session` and `close_day` leave the
engine's day where it is, so call `set_current_day` with the trading day,
counted from zero, before each `open_market`. Without it the clock stays at
day zero for the whole run, and pt-v21's calendars stop there: a name whose
earnings report or ex-dividend date falls on day zero reports, or goes ex,
at every session, every other name never does, and the buyback yield never
accrues. With no host flow, the median name's volatility read 19.7% a year
with the clock left at zero and 20.8% with the days numbered.

Tick only the regular session, the 390 minutes from 09:30. A tick before
09:30 or from 16:00 draws noise at the session's per-minute scale all the
same, and on pt-v21 the open already draws the night's share of each name's
daily variance. A host that ticked from 07:00 to 20:00 raised index
volatility from 12.7% to 14.7% a year and the median name's from 19.7% to
23.4%. On pt-v20 the same ticks took index volatility from 15.7% to 19.2%.
To show a price outside the session, show the last close.

Step the economy once a session, which `close_day` does. From pt-v19 the
macro calendar counts trading sessions, so a host that also steps it at
weekends runs months, quarters and the business cycle fast (the game's
loop stepped it 1.39 times a session) and applies its shocks that much more
often. A weekend step with no session in between also reads the last
session's return again.

A host that passes economic shocks closes the day with
`close_day_with_shocks`, which is `close_day` with the shocks in its macro
step:

```rust
use tradefloor::economy::{EconomicShock, ShockKind};

let shocks = [EconomicShock::new(ShockKind::Other, 0.6, -1.5)];
engine.close_day_with_shocks(day as i64 + 1, &shocks);
```

It keeps everything `close_day` does, which a host that calls
`close_market` and `advance_day` itself would have to repeat: the GARCH
innovations and sector variances the engine holds, the market P/E written
before the step, the re-mark of each price to the macro data the step
publishes, and the rate indices' close. The step reads the session's last
cap-weighted tick return as `market_return_pct` and a macro
`DayAdvanceRequest::volatility` of 1.0, the value every preset was fitted
with.

## The host's own flow

Every statistic the model states was measured with the engine making its
own shocks and nothing else: company news drawn at each open, idiosyncratic
and market jumps, one macro step a session with no economic shock, and the
regular session's ticks. `tradefloor::flow::CalibratedFlow` states that
flow for any preset, and `tradefloor::flow::ExternalFlow` keeps a tally of
what you add to it:

| call | when |
|---|---|
| `record_tick(&request)` | every `Engine::tick`, with the request you passed |
| `record_session_news(news)` | every `Engine::run_session` that carried news |
| `record_macro_step(shocks)` | every macro step, with its active shocks |
| `record_fundamental_move(log_change)` | every earnings figure you rewrite with `set_fundamentals` |
| `record_vix_write(delta)` | every change you make to the economy's VIX |
| `record_vix_target_premium(delta)` | every change you make to the VIX target's premium with `set_vix_target_premium` |
| `record_vix_target_floor_session()` | every session you close with a floor set by `set_vix_target_floor` |
| `record_session()` | once a trading session |

`tally.assess(engine.params())` says which channels are outside the fitted
flow, by how much, and why each one matters. The tally takes no draws, so
keeping one changes nothing. In Python,
`tradefloor.envelope.external_flow(...)` runs the same check. The rebuilt
game flow was outside on every channel it uses: its company news carried
1.8 to 3.2 times the fitted variance, economic shocks were active on up to
91% of its macro steps, it wrote the VIX, it stepped the economy 1.39 times
a session, it ticked 390 times a session outside the regular session, and
its split-driven earnings revisions moved fair value down by 9% to 21% a
year.

### Economic shocks

An active shock adds twice the absolute sum of `gdp_impact * severity` to
the VIX's daily target, lowers the next quarter's GDP target by the same
sum, and, by kind, raises oil or inflation. The target, with its other
additions, is capped at 12 points above its base. On pt-v21,
`market_vol_vix_coupling` (0.75) makes the market factor's variance target
follow the VIX, so the higher VIX becomes realised volatility, which the
VIX then reads back. That is where a host's shocks compound with the model.
The game's shock process (a shock in 8% of months, 13% in a contraction,
lasting 3 to 18 months), scaled, with no other host flow:

| shock flow | sessions with a shock | index volatility | VIX mean | VIX above 30 | VIX above 40 |
|---|---|---|---|---|---|
| none | 0 | 12.7% | 15.7 | 2.3% | 0.3% |
| half | 110 | 13.9% | 16.6 | 3.8% | 0.8% |
| as the game runs it | 216 | 14.8% | 17.9 | 5.3% | 1.2% |
| double | 313 | 16.1% | 19.5 | 9.5% | 1.9% |
| as the game runs it, coupling set to 0 | 216 | 13.7% | 18.1 | 3.4% | 0.4% |

With the coupling at 0 the shocks still lift the VIX, to 18.1 from 16.8 at
the same setting without them, but no longer move realised volatility. On
pt-v20, whose coupling is 0.95, the same shocks took index volatility from
15.7% to 18.8%.

The second place they compound is the business cycle. On pt-v21 a market
that falls below its slow average adds to the hazard of a downturn
(`cycle_equity_hazard`), and a contraction raises the market's variance by
the cycle's multiplier. Shocks alone rarely set that off in two years: the
share of sessions in a contraction went from 0% to 1.8%. A host flow that
also drives prices down does set it off: with the whole game flow below,
setting `cycle_equity_hazard` to 0 cut sessions in a contraction from 13.0%
to 1.7%, and days with the VIX above 40 from 2.3% to 1.2%.

To stay inside the certified envelope, pass no shocks. A host that wants
them for play should keep them rare: at half the game's rate the VIX spent
3.8% of days above 30, against 2.3% with none. Do not also write the VIX
for the same shock. The macro step already raises the VIX's target, and a
jump written on top is counted twice.

### News

The fitted company news is 0.05 events a name a session with a log size of
standard deviation 0.0175, and its mean is zero. From pt-v20 a company's
news moves its fair value for good (`fair_value_news_share`), so news that
leans one way moves the market by its sum. The game's negative company
headlines were about twice the size of its positive ones, and its news
moved fair value down by 2.4% a year on average across the 20 seeds. That
lean is too small for one two-year run to tell from sampling noise, so
`ExternalFlow` flagged it on 1 seed of the 20. Pass news with a mean of
zero. A host that wants its own headlines to carry the company news can
switch the preset's off (`endogenous_news_intensity` 0.0) and pass its own
at the fitted rate and size. The result is a modified preset that the
certification does not cover.

### Earnings and stock splits

Fair value is earnings times a target multiple, so `set_fundamentals` moves
fair value one for one with the earnings it writes. The engine has no stock
split. A host that splits a stock by dividing its earnings, without dividing
the engine's price, cuts that name's fair value by the split ratio, and the
price falls to meet it over the following weeks. The game split a name 2:1
to 10:1 once its price passed 150 to 2,000 dollars, about 43 times in two years.
On their own, those earnings cuts took the two-year index return from
+24.9% to -10.7%.

Keep the engine in its own units instead. Hold a cumulative split factor
for each name, send the engine earnings and book value multiplied by it,
and divide the engine's prices by it for display. The engine then never
sees the split, which is correct, since a split changes no company's value.
`ExternalFlow` counts a split written as an earnings cut as a revision of
`-ln(ratio)`, and flagged the game's on all 20 seeds.

### Writing the economy

A write through `economy_mut` replaces what the macro step computed. A
written VIX moves the market factor's variance target on a coupled preset,
as the VIX's own moves do. The tally is outside once written changes
average more than 0.1 points a session. Writing back a value the engine
already holds changes nothing, so a host that round-trips the whole economy
each day only needs to count the fields it changed.

### Fear the macro model does not carry

A written VIX does not last. The engine's reversion pulls it back toward its
own target. The game measured a 6-point write lifting the next session's
VIX by 4.5 points, with a half-life of about 3 sessions. For an event that
should hold the VIX up, such as a bankruptcy's contagion or an escalation,
put a premium on the target instead.
`set_vix_target_premium(points, half_life_sessions)` adds `points` to the
target beside the inflation and shock terms, inside `vix_target_shock_cap`.
Each close then fades it by `0.5^(1 / half_life)`, and 0.0 holds it. The VIX
moves toward the raised target at the engine's own rate, with its own noise
and jumps. In one 40-session run on pt-v21, a 6-point premium at a
13-session half-life raised the mean VIX by 3.9 points, and a 6-point write
by 0.8. A write replaces the premium standing, so to add an event, read
`vix_target_premium()` and write the sum.

For a period that holds fear at a level, such as an election campaign,
`set_vix_target_floor(Some(level))` keeps each close's target at or above
`level` until `set_vix_target_floor(None)` clears it.

Neither consumes a draw. While one stands, the snapshot carries it and the
state hash covers it, so a resume restores it. Both are outside the fitted
flow: record each premium change with `record_vix_target_premium` and each
floored session with `record_vix_target_floor_session`. In Python,
`external_flow` takes them as `vix_target_premiums=` and
`vix_target_floor_sessions=`.

## The combined effect

Each of the game's inputs on its own, then all of them, then all of them
with the host fixed. Every row but the last leaves the days unnumbered, as
the game does. The fixed host numbers its days, ticks only the session,
steps the economy once a session and keeps splits out of the engine. It
still passes the game's news, shocks and VIX writes.

| host input | index volatility | median name volatility | VIX mean | VIX above 30 / above 40 | two-year index return | worst drawdown |
|---|---|---|---|---|---|---|
| none | 12.7% | 19.7% | 15.7 | 2.3% / 0.3% | +24.9% | 14.7% |
| ticks from 07:00 to 20:00 | 14.7% | 23.4% | 17.0 | 3.8% / 1.1% | +17.9% | 19.4% |
| macro steps at weekends too | 14.1% | 21.0% | 16.1 | 2.9% / 1.0% | +24.8% | 18.3% |
| its news | 13.0% | 21.7% | 15.8 | 2.2% / 0.3% | +21.3% | 15.9% |
| splits written as earnings cuts | 14.6% | 23.7% | 16.5 | 3.6% / 0.7% | -10.7% | 26.6% |
| economic shocks and VIX writes | 14.8% | 21.8% | 17.9 | 5.3% / 1.2% | +23.6% | 18.2% |
| all of them | 19.5% | 31.8% | 20.1 | 9.9% / 2.3% | -19.9% | 35.8% |
| all of them, host fixed | 14.2% | 23.8% | 17.5 | 4.1% / 0.7% | +21.3% | 18.3% |

The last two rows also pass the game's macro `volatility` of 0.5 to each
step. The splits written as earnings cuts account for most of the fall in
the index, and the ticks outside the session and the shocks for most of the
rise in volatility. Together the inputs add up to roughly the sum of each
one alone. With the host fixed, every figure sits inside the spread
pt-v21 shows on its own over two years on `random_universe(108, 7)`
over 30 seeds: index volatility from 7.8% to 31.9%, two-year returns from
-45% to +66%.

## Calibrated configurations

| configuration | what the certification covers |
|---|---|
| library opening, numbered days, the regular session's ticks, one macro step a session, no external flow, a sector-balanced roster | all 40 long-run criteria and the one-year table in [STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md), as Python's `tradefloor.preset_record()` states them |
| the same with a kept opening | the same, after the first quarter. The start-up rows ([STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md), B9's start-up ratio and C1) assume the library's opening |
| a roster of your own | as above, within the roster limit in [REALISM.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/REALISM.md) |
| any flow `ExternalFlow::assess` finds outside | none of the rows, and the findings name the channels |

## Measuring what you get

On pt-v21 most companies with positive earnings pay a dividend, and the
price drops by the amount at the ex-date open. An index of prices at fixed
share counts leaves the dividends out: on `random_universe(108, 7)` it
returned +19.5% over two years on average over 30 seeds, against +25.3%
with the dividends reinvested. A market model whose prices do not drop at
the ex-date shows no such gap, so set its price index against pt-v21's
total return, or take the dividends out of both.

Take the day's close after `close_day` and before any later tick. Read
close-to-close changes from `engine.prior_closes()` and
`engine.last_closes()`. Each stock's `previous_close` is the anchor the
engine reads the day's return from: the open on a preset without an
overnight move, and on pt-v21 the price the night started from.

## Saving and restoring

`Engine::snapshot` and `Engine::restore` save and restore the whole engine.
A host that keeps its own flat arrays sizes them from the width constants
at the crate root, and saves the random streams with
`EngineRngState::to_words`. The words run in stream-id order
(`EngineRngState::STREAM_NAMES`), which puts `news` before `volume_idio`,
the reverse of the struct's field order. `to_named_words` and
`from_named_words` save and read the streams by name, so their order cannot
be mixed up. The crate's
[README](https://github.com/simoncoombes/tradefloor/blob/main/rust/README.md)
has the word layout.