# The tradefloor model

This document states the tradefloor market model as equations. It describes
**tradefloor 0.10.0** running **pt-v21**, the default preset from 0.10.0.
pt-v21 is pt-v20 with 104 dials moved, and most of the text below was
written for pt-v20, the default from 0.8.5 to 0.9.1. Every equation was read
off the code on the `release/0.8.5` branch at commit `8b7ed44`, and the
sections on what pt-v20's graded arm added (published macro data, the
market's permanent share, volatility feedback, the packaged recession) on
`fix/ptv20-final`; each one names the source line it comes from, as
`file:line` under `rust/src/`, on the tree it was read from. The sections on
mechanisms pt-v21 switches on were read off the 0.10.0 code.

**Parameter values.** Every parameter table below gives a dial's value on
pt-v20 and on pt-v21 in two columns, "same" where they agree, as
`tf.ModelParams.from_preset(...).to_dict()` returns them, rounded to four
significant figures; where the pt-v20 column holds two values, the second is
pt-v19's. A value written in the text without naming a preset is pt-v20's.
[pt-v21](#pt-v21) lists every value pt-v21 moves.

A preset is a frozen set of coefficients. The equations below hold for every
preset, but many terms are switched on or off by a preset's dials. Terms
that pt-v20 switches off are described once, in [Off in pt-v20](#off-in-pt-v20);
many of them are on in pt-v21, and [pt-v21](#pt-v21) says which.

Work published on an earlier preset still replays exactly when it names its
preset: pt-v20 and every older preset keep their values.
[pt-v19: reproducing earlier work](#pt-v19-reproducing-earlier-work) gives
every equation and value where pt-v19 differs from pt-v20.

## pt-v21

pt-v21 was certified by name on the 0.10.0 engine against the 40 long-run
rows pt-v20 was graded on, with the long-run rows read on 270 histories of
21 years, and meets all 40, on the definitions it was registered on
([`validation/pt-v21/programme/ptv21-registration-18.md`](https://github.com/simoncoombes/tradefloor/blob/main/validation/pt-v21/programme/ptv21-registration-18.md)).
The verdict, the scripts that graded it and the boxes' outputs are in
[`validation/pt-v21/`](https://github.com/simoncoombes/tradefloor/tree/main/validation/pt-v21).
On the ruled one-year bands it holds all 19 rows. The index's return and
its rate of 3 per cent falls are read over 360 seeds, as the long-run
criteria read them: the index falls 3 per cent or more on 0.98 per cent of
days, against a band of 0.64 to 2.34.
[STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md)
lists the rows and both presets' readings.

The mechanisms its moved dials switch on, with the section that states each:

- **The cycle and the stock market.** A fall of the index below its slow
  average adds to the hazard of a downturn, and the opening economy is drawn
  with that hazard's average (`cycle_equity_hazard*`;
  [Business cycle](#business-cycle)). The market can price a belief about the
  phase rather than the true phase (`cycle_nowcast_accuracy`), and the
  publication lag is drawn (`cycle_publication_lag_draw`).
- **The central bank and the curve.** Rules for stress and growth, a put that
  cuts into a falling market and holds rises while the index stays down, and
  a curve that prices the expected policy path (`fed_*`,
  `policy_anticipation`, `treasury_path_*`, `treasury_policy_damping`,
  `treasury_haven_gain`, `treasury_put_pricing`;
  [Central bank](#central-bank), [Bond yields](#bond-yields)). The curve's
  rates move intraday and are re-marked at the close (`rate_intraday_live`,
  `rate_close_remark`; [Bonds](#bonds)), and a scenario's pinned macro
  fields hold through the close (`macro_pins_hold`).
- **Credit.** Spreads follow the cycle, the VIX and a leverage term
  (`corporate_spread_*`; [Bond yields](#bond-yields)).
- **Payouts.** Dividends and buybacks are paid out of accrued earnings, with
  the ex-dividend drop at the open (`buyback_accrual`,
  `dividend_payout_share`, `dividend_buyback_substitution`,
  `buyback_payout_share`; [The valuation](#the-valuation)).
- **The market's variance.** It follows the cycle, with a leverage term, a
  slower GARCH component and a smoothed VIX coupling (`market_vol_cycle_*`,
  `market_vol_leverage*`, `market_vol_slow_gamma`, `market_vol_vix_smooth`;
  [Market-factor variance](#market-factor-variance),
  [The business cycle in the market's volatility](#the-business-cycle-in-the-markets-volatility)).
  The VIX carries a stress premium (`vix_stress_premium*`;
  [The published quote](#the-published-quote)), and a pinned VIX is priced
  and fed back (`pinned_vix_*`; [Off in pt-v20](#off-in-pt-v20)).
- **Fair value.** A floor under the market's permanent share above the
  volatility ceiling and a slower give-back of the VIX's discount
  ([The permanent share of market moves](#the-permanent-share-of-market-moves)),
  and a knee under a name's level relative to the roster's
  ([The knee under a name's level](#the-knee-under-a-names-level)).
- **The opening.** The market lives 504 sessions of prehistory on a copy
  before day 0, and opens with that copy's volatility and valuation state
  (`market_prehistory_*`; [The order of a session](#the-order-of-a-session)).
  Each name's beta is divided by the roster's cap-weighted beta
  (`market_beta_normalise`; [The universe and the opening](#the-universe-and-the-opening)).
- **A name's own variance.** An idiosyncratic GARCH with a jump bump
  (`idio_vol_*`), and market jumps that are rarer, larger and skewed down
  ([Jumps](#jumps), [Off in pt-v20](#off-in-pt-v20)).
- **The night and earnings.** Part of each day's draws falls at the open as
  an overnight move (`overnight_*`), and an earnings calendar adds a
  surprise, the earnings-day move, its follow-through and the drift before
  the report (`earnings_*`; [Off in pt-v20](#off-in-pt-v20),
  [The aggregate earnings cycle](#the-aggregate-earnings-cycle)).
- **The traded path.** Impact that remembers recent volume and decays on its
  own clock, a book that crosses at a limit and refills, depth nested behind
  the ladder, arrival order shuffled within a cohort, and injected flow that
  divides by depth once and is linear in a tick's participation
  (`impact_memory_*`, `book_*`, `fill_impact_coefficient`, `order_flow_*`;
  [The metaorder memory](#the-metaorder-memory),
  [The latent depth nested behind the ladder](#the-latent-depth-nested-behind-the-ladder),
  [Arrival order in a cohort](#arrival-order-in-a-cohort),
  [Injected order flow on pt-v21](#injected-order-flow-on-pt-v21)).
- **Unemployment and oil.** Unemployment follows Okun's law, and oil reverts
  to its inventory level and passes through to inflation
  ([Unemployment's anchor and oil's interior](#unemployments-anchor-and-oils-interior)).

Each dial's own documentation in `rust/src/params.rs` says what it does at
the value set here.

| Dial | pt-v20 | pt-v21 |
|---|---|---|
| `book_arrival_shuffle` | 0 | 1 |
| `book_cross_at_limit` | 0 | 1 |
| `book_depth_nesting` | 0 | 1 |
| `buyback_accrual` | 0 | 1 |
| `buyback_payout_share` | 0.75 | 0.9 |
| `corporate_spread_cycle` | 0 | 0.75 |
| `corporate_spread_equity_gain` | 0 | 1.8 |
| `corporate_spread_equity_half_life` | 0 | 126 |
| `corporate_spread_vix_cut` | 0 | 1 |
| `cycle_equity_hazard` | 0 | 5 |
| `cycle_equity_hazard_knee` | 0 | 0.1 |
| `cycle_equity_hazard_opening` | 0 | 0.011 |
| `cycle_nowcast_accuracy` | 0 | 0.4 |
| `cycle_publication_lag_draw` | 0 | 1 |
| `dividend_buyback_substitution` | 0 | 1 |
| `dividend_payout_share` | 0 | 1.2 |
| `earnings_anticipation_drift_half_life` | 0 | 252 |
| `earnings_anticipation_drift_share` | 0 | 0.9 |
| `earnings_cycle_half_life` | 60 | 150 |
| `earnings_followthrough_sigma` | 0 | 1.1 |
| `earnings_session_sigma` | 0 | 1.9 |
| `earnings_surprise_sigma` | 0 | 3.5 |
| `earnings_volume_multiple` | 0 | 1.2 |
| `fair_value_market_excess_share` | 0 | 0.5 |
| `fair_value_relative_half_life` | 0 | 63 |
| `fair_value_relative_knee` | 0 | 4 |
| `fair_value_vix_release_half_life` | 0 | 504 |
| `fed_drawdown_hold` | 0 | 0.12 |
| `fed_growth_cut` | 0 | 2 |
| `fed_put_carry` | 0 | 1 |
| `fed_put_emergency_vix` | 0 | 50 |
| `fed_put_gain` | 0 | 3 |
| `fed_put_half_life` | 0 | 126 |
| `fed_stress_cut` | 0 | 0.1 |
| `fed_stress_hold` | 0 | 42 |
| `fed_stress_inflation_gap` | 1 | 2 |
| `fill_impact_coefficient` | 0.314 | 0.15 |
| `flight_to_quality_gain` | 0.008 | 0.013 |
| `idio_sigma_scale` | 0.5126 | 0.52 |
| `idio_vol_alpha` | 0 | 0.25 |
| `idio_vol_beta` | 0 | 0.5 |
| `idio_vol_jump_bump` | 0 | 1 |
| `impact_memory_coefficient` | 0 | 0.65 |
| `impact_memory_crossover` | 0 | 0.001 |
| `impact_memory_half_life` | 0 | 12 |
| `impact_memory_refill` | 0 | 1 |
| `impact_memory_slow_half_life` | 0 | 780 |
| `impact_memory_slow_weight` | 0 | 0.1 |
| `jump_intensity_idio` | 0.00689 | 0.009 |
| `jump_intensity_market` | 0.02829 | 0.005 |
| `jump_mean_market` | -0.008522 | -0.03 |
| `jump_sigma_idio` | 0.07521 | 0.0318 |
| `jump_sigma_market` | 0.00246 | 0.01 |
| `macro_pins_hold` | 0 | 1 |
| `market_beta_normalise` | 0 | 1 |
| `market_factor_sigma` | 0.006454 | 0.007099 |
| `market_prehistory_sessions` | 0 | 504 |
| `market_prehistory_valuation` | 0 | 1 |
| `market_vol_beta` | 0.8946 | 0.9446 |
| `market_vol_cycle_cap_relative` | 0 | 1 |
| `market_vol_cycle_expansion` | 0 | 0.82 |
| `market_vol_cycle_half_life` | 0 | 10 |
| `market_vol_cycle_pin_neutral` | 0 | 1 |
| `market_vol_cycle_pin_phase` | 0 | 1 |
| `market_vol_cycle_ratio` | 0 | 2.471 |
| `market_vol_cycle_recovery_release` | 0 | 0.45 |
| `market_vol_cycle_recovery_scale` | 0 | 0.1 |
| `market_vol_cycle_relative` | 0 | 0.75 |
| `market_vol_gamma` | 0.1556 | 0.06 |
| `market_vol_leverage` | 0 | 2.5 |
| `market_vol_leverage_half_life` | 0 | 15 |
| `market_vol_leverage_standardise` | 0 | 1 |
| `market_vol_slow_gamma` | 0 | 0.05 |
| `market_vol_vix_coupling` | 0.954 | 0.75 |
| `market_vol_vix_smooth` | 0 | 3 |
| `oil_inflation_passthrough` | 0 | 1 |
| `oil_inventory_reversion` | 0 | 0.002 |
| `order_flow_coefficient` | 50 | 800 |
| `order_flow_depth_law` | 0 | 1 |
| `order_flow_impact_law` | 0 | 1 |
| `overnight_idio_df` | 0 | 4 |
| `overnight_idio_share` | 0 | 0.1 |
| `overnight_market_share` | 0 | 0.55 |
| `pinned_vix_calm_knee` | 0 | 17.6 |
| `pinned_vix_calm_share` | 0 | 0.2 |
| `pinned_vix_feedback` | 0 | 0.8 |
| `pinned_vix_priced_cap` | 0 | 1 |
| `pinned_vix_variance_share` | 0 | 0.7 |
| `policy_anticipation` | 0 | 1.8 |
| `price_hard_cap` | 5e+04 | 1e9 |
| `rate_close_remark` | 0 | 1 |
| `rate_intraday_live` | 0 | 1 |
| `treasury_2y_noise` | 0.022 | 0.008 |
| `treasury_haven_gain` | 0 | 0.014 |
| `treasury_path_half_life` | 0 | 63 |
| `treasury_path_pricing` | 0 | 1 |
| `treasury_policy_damping` | 0 | 0.5 |
| `treasury_put_pricing` | 0 | 1 |
| `unemployment_okun_coefficient` | 0 | 0.75 |
| `vix_level_sigma` | 0.0181 | 0.009 |
| `vix_stress_premium` | 0 | 3 |
| `vix_stress_premium_cap` | 0 | 0.35 |
| `vix_stress_premium_knee` | 0 | 0.6 |
| `volume_idio_variance_gain` | 0.2 | 0.65 |

The realism statistics the model is checked against are defined in
[STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md).
The release lines that get fixes, and for how long, are in
[SUPPORT.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/SUPPORT.md).

## Reading this document

### Parameter kinds

Every parameter table has a **kind** column. It says how the value was set.

| Kind | Meaning |
|---|---|
| measured | estimated on a named data series, with a standard error on record |
| derived | follows from an identity, from measured inputs or from the model's own structure |
| fitted | tuned against the realism statistics, one dial or a search over many; calibrated jointly, not one at a time |
| chosen | set by hand, often inherited from the unpublished [reference implementation](#the-reference-implementation) the engine was ported from |
| guard | a bound that keeps the simulation finite; not a claim about real markets |

**Measured** and **derived** values are calibrated. **Fitted** values are
calibrated jointly: a search found them against the realism statistics, and
no derivation is on record for the single dial. **Chosen** values and
**guards** are assumptions. The kinds follow the project's own ledger,
`python/tradefloor/provenance.py`, with its `unprovenanced` split into fitted
and chosen by what the code and design notes say about each value.

Where a design note or the code names a source, the table gives it. The
data series used most often:

| Short name | Series |
|---|---|
| S&P 500 | ^GSPC daily closes, 1990-01-03 to 2025-07-30; from 1928 for the index drift and tail bands |
| VIX | ^VIX daily closes, 1990 to 2025 |
| Reference roster | 40 US large caps across the sectors, daily, 2015-07 to 2025-07; 32 of them from 1987 |
| NBER and BEA | NBER recession dates and BEA real GDP (GDPC1), 1990 to 2025 |
| FRED CPI | FRED CPIAUCSL, 2015 to 2025 |

### The reference implementation

The engine is a port of an earlier simulator, which this document calls the
reference implementation. It is not published, and it stays unpublished
until the owner names it, so you cannot read its code or check a value
against it. Where a table gives the reference implementation as a value's
source, the value was copied from that code and no design note derives it.
Read it as an assumption, like any other **chosen** value.

The port was checked against it. The Rust parity tests compare the port
with vectors the reference implementation produced, and those vectors are
in `rust/goldens/`, so the tests run on a fresh clone. The code that
produced them is not in this repository.

### Time

| Unit | Length | Code |
|---|---|---|
| tick | one minute of the 09:30 to 16:00 session | `market/hours.rs:22` |
| session (day) | 390 ticks | `python_engine.rs:1923` |
| macro month | 21 sessions | `economy/state.rs:65` |
| macro quarter | 63 sessions | `economy/state.rs:83` |
| year | 252 sessions | `market/tick.rs:217`, `economy/state.rs:62` |

Days are an abstract trading calendar: there are no weekends or holidays.
Within a session a per-day drift is applied as $1/390$ per tick and a per-day
standard deviation as $1/\sqrt{390}$ per tick. Tick $t = 0, \dots, 389$ sits
at session fraction $\tau_t = t/390$.

### Notation

| Symbol | Meaning | Unit |
|---|---|---|
| $i$, $k(i)$ | a company, and its sector | |
| $d$, $t$ | session, and tick within the session | |
| $V_{i,t}$ | fair value | currency per share |
| $s_{i,t}$ | mispricing, the log gap between model price and fair value | log |
| $P_{i,t}^{\ast}$ | model price, $V e^{s}$ | currency |
| $P_{i,t}$ | printed (traded) price | currency |
| $P_{i,d}^{o}$ | the session's opening price | currency |
| $X_d$ | the VIX, fixed within a session | index points |
| $A$ | the VIX anchor: the VIX at which every VIX coupling reads one | index points |
| $\rho_d$ | $X_d / A$ | |
| $v_d$ | market-factor variance | per day |
| $h_{i,d}$ | a company's own (GJR-GARCH) variance | per day |
| $y_d^{c}$ | corporate bond yield | percent a year |
| $\mathbf{1}[\cdot]$ | 1 if the condition holds, else 0 | |
| $\mathrm{clip}(x; a, b)$ | $\min(b, \max(a, x))$ | |
| $Z$, $U$ | a fresh standard normal draw, a fresh uniform draw on $[0, 1)$ | |

Macro quantities are in percent, as the engine stores them. The Python API
converts rates to fractions at its boundary (`units.rs:1-31`).

## The structure

The model has two layers that meet in one number, the model price.

```text
macro economy (daily, at the close)
  business cycle -> growth, unemployment, inflation -> central bank
  -> policy rate -> 2-year, 10-year and corporate bond yields
  -> nominal output (GDP x CPI) and the aggregate earnings cycle
  -> the VIX

fair value V (every tick)            = sector P/E x earnings x rate term
                                        x the company's own fair-value level
mispricing s (every tick)            = mean reversion + herding + market news
                                        + order flow + market noise,
                                        market jumps at the close
model price P* (every tick)          = V exp(s)
printed price P (every tick)         = P* traded through the market maker's
                                        book, quoted around P*
volatility (daily, at the close)     = per-name GJR-GARCH, market-factor
                                        variance, sector variance; all read the VIX
```

**Fair value** moves with the corporate bond yield, which moves every
session; with nominal output and an aggregate earnings cycle, which move
with the business cycle; and with each company's own fair-value level $v$,
which takes the company's and its sector's shocks for good, and the
market's plain shocks up to a volatility ceiling. **Mispricing** carries
what is transient: the market factor's excess over that ceiling and the
terms that are not zero-mean, order flow and the crowd. It reverts to zero with a half-life of about
40 sessions. **The printed price** is the model price after it has gone
through a limit order book that the market maker quotes around the model
price, so trades move it and a large order pays for depth. The close is a
crossing at the model price.

The VIX sits in the middle. It is computed from the index's own conditional
variance, plus a fear response to the day's return, and every variance
process in the market reads it back.

### The order of a session

1. **Open** (`engine.rs:3933-4050`). The crisis episode is stepped (start, end, epicentre), the day's company news is drawn, and each company's opening price $P^{o}$ is set to its last print. On the first session only, the stationary opening splits each company's day-zero premium between $s$ and $v$ (`engine.rs:4533-4572`).
2. **390 ticks** (`market/tick.rs:823-1678`). Each tick: apply the agents' fills from the last step, once, on the first tick (`engine.rs:2521-2545`); draw the market factor and the sector factors; for each company, draw its own noise, update $s$ and $v$, compute $V$ and $P^{\ast}$, draw tick volume, and settle the print through the book. The last tick is the closing cross (`market/tick.rs:1542-1554`).
3. **Close** (`engine.rs:4232-4513`). Each company's GJR variance is updated from the day's noise, the momentum term rolls, jumps are drawn and applied to $s$, the market-factor and sector variances are updated, and the volume state steps.
4. **Macro step** (`engine.rs:5561-5648`, `engine.rs:4877-5177`). The index's conditional variance is computed and the VIX steps, then the economy and the yield curve, the business cycle, the aggregate earnings cycle and the central bank (`engine.rs:5099-5142`).

The next session reads the new VIX, rates and output.

**The previous close is the open.** At the open each company's
`previous_close` is set to its opening price $P^{o}$, after the close's
re-mark to newly published macro data and after any overnight move, not to
the previous session's last print. It is the anchor of the session's ±25%
circuit-breaker band and of the daily return the GARCH update reads, so the
band is a session band and the move between the last print and the open
sits outside it (`market/daily.rs`, `reset_daily_prices`). A day change
taken against `previous_close` is therefore open to close. On pt-v20 the
open differs from the last print on every name every day, by a median of
0.12% and at most 0.55% (119 days of a 20-name roster). For a
close-to-close change, the Rust engine keeps each name's last print:
`Engine::prior_closes()` is the close the current day is measured from and
`Engine::last_closes()` the most recent session's. In Python, the previous
day's bar close is the same number.

**Randomness.** Each process draws from its own stream, derived from the
run's seed (`rng.rs:397-514`): market, economy, news, jumps, overnight,
volume, crisis epicentre and others. The market stream's schedule depends
only on the roster and the sectors, never on a price or a preset, so two
presets run on the same seed see the same market shocks (`params.rs:30-35`,
`market/mod.rs:15-32`). Some streams, such as the economy's, take a number
of draws that depends on the state, and that dependence stays inside the
stream.

**Seeds.** A seed is any integer from 0 to $2^{64}-1$, for the market, the
universe and a surgery alike (`rng.rs:294-350`). Each stream is a PCG32
generator, set by a 64-bit starting value and a sequence number. For a
root seed $s$ below $2^{32}$, stream $k$ starts from the top 32 bits of
$\mathrm{mix}(s \cdot 2^{32} + k)$ on sequence $256 + k$, where mix is the
SplitMix64 finalizer. That is the rule every release before 0.8.5 used, so
every such seed gives the market it always gave. The high 32 bits of a seed
enter only when they are non-zero: then stream $k$ starts from all 64 bits
of $\mathrm{mix}(s \oplus \mathrm{mix}(	exttt{SD64} \cdot 2^{32} + k))$, on
sequence $768 + k$. For one stream that map is a bijection, so no two wide
seeds share a stream, and $768 + k$ is a sequence no 32-bit seed uses, so a
wide seed shares no stream with a narrow one. The universe seed goes into
its generator's state whole, so the same holds for rosters. The reason for
the width is a sealed evaluation: with $2^{32}$ seeds, a hidden seed can be
found by simulating every one against a market's first prices. Draw a
sealed seed from all 64 bits.

**The opening.** Before session 1 the economy runs 755 macro steps on its
own, with the market frozen and the day's return set to zero
(`engine.rs:1995-2064`). The business cycle's opening phase and its age are
drawn from the cycle's stationary law first (`engine.rs:1908-1932`). Because
the market is frozen during this burn-in, the VIX settles near a fixed level
that depends on the roster and hardly on the seed: on
`Universe.random(40, seed=111)` it opens at 17.66 on 27 of seeds 101 to 130,
and at 17.68 to 17.85 on the other three. The opening corporate yield
depends on where the cycle opens, 2.5% to 6.7% across the same seeds.
This opening runs only when the engine builds its own default economy. A
caller that supplies a macro state keeps it exactly: Python's
`macro_state=`, and in Rust `Engine::with_params_keeping_opening`.
`Engine::new` and `Engine::with_params` always run it, over whatever
economy they are given, and `Engine::opening_settled()` says whether it
ran.

**The market's prehistory** (`market_prehistory_sessions`, 504 on pt-v21,
off on every earlier preset). The burn-in has no market, so without it every
volatility state opens at its phase-free baseline: the factor variance at
`market_factor_sigma` squared, the VIX where the index's baseline variance
puts it, the anchor's memory at zero. A run that opens in an expansion then
falls for two quarters to the level its expansions hold, and one that opens
in a contraction rises through the whole of its own. Off zero, the
constructor clones the opening engine, gives the copy generators of its own
(surgery generators of the root seed under the tag `PREH`, and tagged
earnings and publication keys), and plays it the last $N$ sessions of the
burn-in with the economy's recorded phase and age set before each session.
The run then opens with the copy's volatility state: the factor variance
(both components, the mixture, the smoothed VIX, the return memory), the VIX,
the VIX's and the factor's slow levels, the anchor's and the stress premium's
memories, the cycle's volatility multiplier, and each sector's variance and
each name's GARCH and idiosyncratic variance and jump excitation. Prices,
fair values, the economy's other fields and the run's own draws are the ones
it would open with. $N$ = 252 costs about 3 seconds a construction on the
40-name roster; the slow variance component keeps 0.11 of the opening's gap
after 252 sessions, but the copy's recorded path, not its start, sets where
it ends (504 sessions read the same on 81 paired histories). A process keeps
the last 16 engines it built this way, 2,000 names between them at most. A
later build from the same seed, roster, economy and model, to the bit, is
served as a copy of the kept engine, which is the same engine with the same
draw counts (`Engine.opening_cache_info`,
`Engine.set_opening_cache_capacity`).

Measured (sim/r18-opening 8385f016). On R17Bd, 200 held-out histories
(seeds 300201-300400): a run that opens in an expansion opened with factor
variance 5.04e-5 against the 2.98e-5 its expansions hold, the VIX at 17.6
against 16.2, the anchor's memory at +0.02 against -0.22, and index
volatility of 0.17 to 0.18 a year for five months against 0.146; with
$N$ = 252 it opens at 2.92e-5, 16.3 and -0.19. Over 1350 held-out histories
(sets A, B and C from `r14gen`, and the twelve blocks 40201-40830 and
90201-190830 from `lite8.py`), year 0's index volatility less the mean of
years 1-7:

| Arm | without | with $N$ = 252 | PH5 volatility use at 1350 | largest year gap at 270, in its se |
|---|---|---|---|---|
| R17T | +0.0039 (se 0.0021) | +0.0005 (0.0024) | 1.28 to 0.77 | 1.15 to 0.69 |
| R17Bd | +0.0055 (0.0022) | +0.0012 (0.0025) | 1.32 to 0.44 | 1.18 to 0.40 |
| R17Bh (R17Bd at `market_vol_cycle_expansion` 0.85) | -- | +0.0007 (0.0026) | 0.65 | 0.58 |

A Monte Carlo of a fresh 270-history grade on the pooled covariance of the
1350 histories passes the volatility clause 0.73, 0.78 and 0.77 of the time
(R17T and R17Bd without it 0.61 and 0.57), against 0.79 to 0.80 for a model
whose years all share one mean under the same covariance, which is the
clause's own ceiling: seven tests at 2 se against one year 0.

The return clause is not the opening's volatility, and the prehistory leaves
it where it was: year 0's index return is about 2 points below later years'
(4.0 to 4.6 per cent against 5.5 to 7.3), all of it in the first two
quarters, on every arm with and without the prehistory (mean of year 1 less
year 0 +0.017 to +0.020, 1.1 to 1.25 se at 270). Two valuation states open at
zero and drift in that half year: the names' mean mispricing falls to -0.013
by month 6 and settles near -0.008, and the VIX feedback's exposure
(`fair_value_vix_discount`) builds from 0 to about 0.02 over nine months.

**The prehistory's valuation** (`market_prehistory_valuation`, a switch, on
in pt-v21, off on every earlier preset, and refused without a prehistory).
Those are not the only states the burn-in leaves where a market that never
traded would. On
R17Bd with $N$ = 252 (180 histories, seeds 300401-300580) the names'
cap-weighted mispricing went from 0 to -0.011 by month 6, the VIX feedback's
exposure from 0 to 0.035 by month 12 (0.049 over months 12-36, since the
give-back runs at `fair_value_vix_release_half_life` 504), the Fed put's owed
cut from 0 to 0.15 (0.24 settled) with the policy rate a quarter point lower
by month 12, credit's leverage gap from 0 to -0.022, and the market's forecast
of the policy path from -0.086 to +0.028. The first two cheapen the market
over year 0; the last three move the corporate yield down, then up, and
partly hide it. On, the copy's end also hands back each name's mispricing
(which the opening's split takes in place of the name's draw, and leaves out
of the draws' centring), the VIX feedback's exposure, the anticipation's
drift, the earnings cycle (the burn-in's own level, which the constructor
otherwise replaces with the opening phase's target), credit's leverage gap
with the corporate yield moved by the change it makes to the spread formula,
the Fed put's owed cut and stock with the policy rate lowered by the owed cut
(not below zero) and the prime rate, both Treasury yields, the corporate yield
and the mortgage rate moved with it, and the path's forecast with the curve
moved by the share of its change each yield prices ($1 - d$ on the 10-year,
the corporate yield and the mortgage rate, $0.85 + 0.15(1 - d)$ on the 2-year,
$d$ = `treasury_policy_damping`). Everything that moves fair value is booked
into the names' fair-value levels by the opening's split at the first open,
so no price moves at the opening. The run's draws are its own.

Measured (sim/r18-valopen 9a4a524d; boxes r19X1, r19G1, r19G2, r19L2). Each
arm is R17Bd with the prehistory; the paired shift is against R17Bd with
$N$ = 252 alone on the same held-out seeds (sets A, B and C from `r14gen`, and
`lite8` on the twelve blocks 40201-40830 and 90201-190830).

| Arm | carried | $N$ | histories | year 0 shift (se) | year 1 less year 0 shift (se) |
|---|---|---|---|---|---|
| V7 | mispricing, VIX feedback, anticipation drift | 252 | 1014 | +1.62 (0.11) | -1.45 (0.11) |
| V15 | V7 and the leverage gap | 252 | 1034 | +1.35 (0.15) | -1.00 (0.20) |
| V31 | V15 and the Fed put | 252 | 1033 | +1.01 (0.15) | -0.74 (0.20) |
| V31L | V31 | 504 | 1011 | +1.20 (0.19) | -1.28 (0.27) |
| R19V | all, with the earnings cycle and the path's forecast | 504 | 1350 | +1.31 (0.17) | -1.32 (0.24) |

(points of index return). On 405 of the same histories 1008 sessions read as
504 do (year 0 +1.41 against +1.62 on the pair). The earnings cycle alone
moves year 0 by +0.04 (se 0.03, 90 paired histories), since its opening at
the phase's target is too low in a contraction and too high in a recovery by
about as much. R19V over 1350 histories: year 1 less year 0 +0.0063 (0.40 of
its se at 270, against +0.0195 and 1.25), year 0's volatility less years 1-7
+0.0012 (se 0.0025), and a Monte Carlo of a fresh 270-history grade passes
PH5 0.736 of the time (the volatility clause alone 0.776) against 0.767 for a
model whose years share one mean under the same covariance. Year 0 still
returns 5.3 per cent against 6.3 for the mean of years 1-7 (-0.93 points, se
0.52), in its first two quarters (1.10 and 0.90 against 1.5 to 1.6 later);
switching the earnings calendar off moves year 0 by -0.09 (se 0.24, 153
paired histories), so the reporting seasons are not the cause, and what is
has not been found.

## The macro economy

The economy steps once per session, after the close (`engine.rs:5529-5539`).
Its day counter $d$ starts at 1 on the first close. Within one step the order
is: the market P/E and the day's index return are read from prices, the VIX
and the economy are updated, the business cycle may change phase, and then
the central bank meets if a meeting is due (`engine.rs:5583-5648`,
`engine.rs:4970-5152`). All macro draws come from the economy stream.

The price path reads four things from the economy: the **corporate bond
yield**, as the discount rate in fair value; **nominal output** and the
**aggregate earnings cycle**, which scale earnings; and **the VIX**, which
every variance process reads. Everything else in this section matters to
prices only through those four. The bond indices read the 2- and 10-year
yields as well; see [Bonds](#bonds).

Most constants in this section are carried over unchanged from the
[reference implementation](#the-reference-implementation) the engine was
ported from, which is unpublished, and no design note derives them.
They are **chosen**. The dials that pt-v19 and pt-v20 moved carry their own entries.

### The calendar

The macro clock runs on trading sessions. A month starts when
$d \bmod 21 = 0$ and a quarter when $d \bmod 63 = 0$ (`economy/daily.rs:720-721`).
Levels compound over 252 sessions a year.

| Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|
| `macro_calendar_days_per_year` | 252 | same | derived | sessions in a trading year |
| `macro_compound_days_per_year` | 252 | same | derived | same; at 365 the long-run index return was 3.8% a year, at 252 it is 4.9% (design note results/longrun-drift) |
| `macro_burn_in_days` | 755 | same | chosen | the day the slowest field, the corporate yield, enters one stationary sd of its mean |

### Business cycle

**Timescale:** a transition can happen at any close; hazards are per macro
month. **State:** the phase $\mathcal{P} \in \lbrace E, P, C, T, R \rbrace$
(expansion, peak, contraction, trough, recovery, in that cyclic order) and its
age $a$ in months.

The age advances by $1/21$ each session and resets to zero on a transition
(`economy/daily.rs:1774`, `economy/cycle.rs:258-260`). Below a minimum age
$a_{\min}(\mathcal{P})$ no transition is possible. Above it, the phase
ends with a Weibull hazard plus a state-dependent adjustment:

```math
h^{W}(a) = \min\!\Big(0.8,\ \frac{k}{\lambda}\Big(\frac{a}{\lambda}\Big)^{k-1}\Big),
\qquad
h_d = \mathrm{clip}\big(h^{W}(a_d) + \Delta_d;\ 0,\ 1\big),
\qquad
\Pr[\text{transition on day } d] = h_d / 21
```

(`economy/cycle.rs:31-65`, `economy/cycle.rs:238-262`)

The adjustment $\Delta_d$ reads the economy after the day's update
(`economy/cycle.rs:158-217`). $\pi$ is inflation, $r^{p}$ the policy rate,
$y^{2}$ and $y^{10}$ the 2- and 10-year yields, $u$ unemployment, $g$ real
growth and $\mathrm{PE}$ the market's trailing P/E:

- In expansion (E): $\Delta = 0.1 \cdot \mathbf{1}[\pi > 4] + 0.1 \cdot \mathbf{1}[r^{p} > 5] + \min(0.15, 0.08 (y^{2} - y^{10})) \cdot \mathbf{1}[y^{2} > y^{10}] + \min(0.1, 0.005 (\mathrm{PE} - 28)) \cdot \mathbf{1}[\mathrm{PE} > 28]$, and if $u > 8$ or $g < 1$ the hazard $h^{W} + \Delta$ is cut by 0.2, to no less than 0.
- In contraction (C): $\Delta = 0.1 \cdot \mathbf{1}[r^{p} < 1] + 0.05 \cdot \mathbf{1}[g < -2] + 0.05 \cdot \mathbf{1}[u > 10] + 0.05 \cdot \mathbf{1}[y^{10} - y^{2} > 1.5]$.
- In trough (T): $\Delta = 0.1 \cdot \mathbf{1}[r^{p} < 3] + 0.05 \cdot \mathbf{1}[u > 8]$.
- In recovery (R): the Weibull hazard is cut by 0.1, to no less than 0, if $u > 10$.
- In peak (P): $\Delta = 0$.

**The market's fall in the hazard** (`cycle_equity_hazard` $h_q$,
`cycle_equity_hazard_knee` $q_0$). On in pt-v21 at $h_q$ = 5 and
$q_0$ = 0.1, off on every earlier preset. Off zero, in an expansion and at a
peak the adjustment gains, before the expansion's guard,

```math
\Delta \mathrel{+}= h_q \max(0,\ G_d - q_0)
```

where $G_d$ is the index's log fall below its slow average, the gap credit's
leverage term reads (`EconomyState::spread_equity_gap`, half-life
`corporate_spread_equity_half_life`), which the hazard runs even with
`corporate_spread_equity_gain` at 0. A bear market that begins in an expansion
raises the chance the expansion ends, through the wealth effect and tighter
financial conditions; the index leads the cycle (Estrella and Mishkin 1998).
Without it the cycle reads no market, and on R17T 0.48 of 20 per cent bears
have a true contraction between the peak and the trough plus 63 sessions
(B12, 620 bears over 180 held-out histories) against 7 of 11 post-war S&P 500
bears, with 0.89 bears a decade outside a recession against the tape's 0.53.
On the certified roster the gap reads about 0.1 at a bear's 20 per cent line
and about -0.06 at the median session.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $h_q$ | `cycle_equity_hazard` | 0 (off) | 5 | | in [0, 20], per month |
| $q_0$ | `cycle_equity_hazard_knee` | 0 | 0.1 | | in [0, 1] |
| $h_0$ | `cycle_equity_hazard_opening` | 0 (off) | 0.011 | | in [0, 1], per month |

Before day zero the economy runs without an index (the stationary opening's
law and the macro burn-in), so the fall's hazard cannot act there and the
phase a run opens in comes from a cycle with longer expansions than the run's.
`cycle_equity_hazard_opening` $h_0$ stands in for it: while the economy runs
alone, the ladder and the hazard-only law the opening is drawn from add $h_0$
in an expansion and at a peak. Set it so the phase the run opens in has the
run's own phase shares. That is 0.007 a month at the settings below, less
than the mean of $h_q \max(0, G_d - q_0)$ over a run's expansion and peak
sessions (0.010 to 0.012): the market's hazard comes in bursts, and a burst
spends much of its hazard on expansions it has already ended, so a constant
of the same mean ends more of them.

What it was measured to do (sim/r17-b12, on R17T, 30 held-out histories of
5292 sessions, seeds 50201-50230, paired): at $h_q = 5$, $q_0 = 0.1$ with
`market_vol_cycle_expansion` 0.80 (R17T 0.85), B12 reads 0.66 against 0.51,
recessions start 1.37 times a decade against 1.18 (post-war NBER 1.47),
20 per cent bears fall from 1.73 to 1.50 a decade and those outside a
recession from 0.85 to 0.52, index volatility is 18.4 against 18.8 per cent
and sessions under -5 per cent 8.4 against 8.1 a decade. The hazard alone
(expansion 0.85) lifts B12 by about 0.07 at a knee of 0.1 and adds about a
point of index volatility; at a knee of 0.05 it lifts B12 by 0.2 but starts
0.6 more recessions a decade and adds 2.4 points of volatility.

On the screen's 90 held-out histories per set (sets A and B, boxes b12sA and
b12sB4, R17T with $h_q = 5$, $q_0 = 0.1$ and `market_vol_cycle_expansion`
0.82) B12 reads 0.636 and 0.608 against R17T's 0.495 and 0.472, B10 and B11
stay in their bands and F-bear's median cut deepens (-0.75 and -0.60 against
-0.50). A contraction or trough then holds 0.08 of year 0 and 0.10 to 0.12 of
the later years, and PH5's volatility clause read 0.61 and 1.21 of its bound
(R17T 0.75 and 0.94). With $h_0 = 0.011$ year 0's share is 0.10, and over the
270 histories of sets A, B and C year 0's volatility sits 0.005 above the
later years' where R17T's sits 0.003 below; the clause still reads 1.21,
1.00 and 1.12 of its bound on the three sets, against R17T's 0.75, 0.94 and
0.92.

PH5 pooled, with its standard errors over the pooled histories (grade_all's
`ph5`, the registered formula; sets A and B from the screen boxes, set C's
long run and true-phase histories from box ph5pC1 at a5ecbcaf, R17T's set C
long run from c10cC1). "Use" is the volatility clause's worst gap over its
bound; the pass line is 1.00.

| Arm | A+B (180): use, worst year | A+B+C (270): use, worst year | PH5 at 270 | year 0 vol less years 1-7 | contraction share, year 0 / years 1-7 |
|---|---|---|---|---|---|
| R17T | 1.03, year 7 (fails) | 0.73, year 7 | pass | -0.0032 | 0.067 / 0.081 |
| R17Bb | 1.11, year 1 (fails) | 1.20, year 1 (gap 0.0151, bound 0.0126) | fails | -0.0059 | 0.077 / 0.101 |
| R17Bc | 0.70, year 6 | 0.83, year 6 | pass | +0.0048 | 0.100 / 0.094 |

The return clause passes on every arm (at most 0.35 of its bound). Over the
same 270 histories: B12 reads 0.477 (se 0.013), 0.602 (0.014) and 0.581
(0.015) for R17T, R17Bb and R17Bc; F-bear's median policy change in a 20 per
cent bear is -0.50, -0.60 and -0.55 (the share form 0.586, 0.606, 0.606);
C10c breaches 0 of 384 rules on each arm, with the target rules' least margin
3.55, 2.12 and 1.38 se and the least over all rules 2.12, 0.76 and 0.76 se.
R17Bb's calmer year 0 is a bias the pooled clause finds: it fails at 180 and
at 270, and its year 0 sits 0.006 below the later years against R17T's 0.003.
R17Bc passes pooled at 0.83, which is 0.34 se inside the bound in the
difference's own se (R17T 0.54 se).

The opening's value, tuned (615624e3; boxes b12tG1, b12tLP1, b12tX1, b12tX2
and b12tS1). The phase a run opens in, over 1500 construction-only seeds
(200001-201500), against the run's own shares over years 1-20 (270
histories), in the order expansion, peak, contraction, trough, recovery:

| $h_0$ | opening shares | contraction and trough |
|---|---|---|
| 0 | 0.742 0.061 0.050 0.033 0.115 | 0.083 |
| 0.0055 | 0.701 0.071 0.056 0.045 0.127 | 0.101 |
| 0.007 | 0.693 0.070 0.053 0.047 0.137 | 0.100 |
| 0.011 | 0.667 0.073 0.059 0.048 0.153 | 0.107 |
| the run, years 1-20 | 0.690 0.067 0.064 0.039 0.140 | 0.103 |

At 0.007 every share is within 0.01 of the run's; 0.011 opens too few
expansions and too many recoveries. Years 0 to 7 over 1350 histories (sets
A, B and C from r14gen, and twelve more held-out blocks of 90, 40201 to
190830, from `lite8.py`, which runs r14gen's session loop and records the
index and the true phase):

| Arm | $h_0$ | contraction share, year 0 / years 1-7 | year 0 vol less years 1-7 (se) | PH5 use at 270 (A+B+C) | PH5 use at 1350 |
|---|---|---|---|---|---|
| R17T | -- | 0.078 / 0.084 | +0.0039 (0.0021) | 0.73 | 1.28 |
| R17Bb | 0 | 0.095 / 0.104 | +0.0019 (0.0021) | 1.20 | 1.07 |
| R17Bf | 0.0055 | 0.109 / 0.102 (1080) | +0.0075 (0.0026) | -- | 1.54 (1080) |
| R17Bd | 0.007 | 0.103 / 0.101 | +0.0055 (0.0022) | 0.64 | 1.32 |
| R17Bc | 0.011 | 0.106 / 0.096 (630) | +0.0094 (0.0033) | 0.83 | 1.49 (630) |

Sets A, B and C are a calm draw for year 0 on every arm (R17T -0.0032 there,
+0.0056 on the other 1080), which is why R17Bb failed there and R17Bd reads
0.64. The heat is in the market's opening state, not in the phase, and R17T
has it too. A run that opens in an expansion opens with the VIX at 19.2
where the run's expansions hold 16.2 (R17Bd; R17T 19.3 against 17.0), and
index volatility at 0.169 in the first month against 0.14, decaying over a
quarter; a run that opens in a contraction opens calm (VIX 18.7) and lives
the contraction's whole rise in volatility inside year 0. Two thirds or
more of the histories keep the same path across R17Bb, R17Bd and R17Bc (the
opening phase differs in the rest), so the arms' differences are paired: $h_0$ adds
about 0.0005 of year-0 volatility per 0.001, and no value keeps the phase
shares and brings year 0's volatility to the later years'. PH5's clause is
seven tests at 2 se against one year 0; on the pooled covariance of the
yearly means a stationary model fails it at 270 histories about one time in
five, and with year 0 0.0055 above the later years about 0.44 of the time.

R17Bd over sets A, B and C (270): B12 0.597 (se 0.016), F-bear's median
-0.55 (+0.86 se), C10c 0 of 384 breach (least margin 1.14 se,
`out_unemployment_rate_down21` ahead in 0.633), the exploit repro
`c10_mirror` 0 breaches against the exposure-matched position. The L-rate
sign probes over 270 seeds (201-230, 501-530, 801-830 and the same plus
20000 and 50000) read -3.0 to -3.3 per cent a year, positive in 0.30 to 0.34
(R17T -3.3 to -3.7, 0.27 to 0.30); R17Bc's set A reading (UST10Y at step 5,
+3.4, 9 of 12) is noise, and a 12-seed draw from R17Bd's 270 fails the row
about 0.5 per cent of the time for each probe.

The hazard with `market_vol_cycle_expansion` at 0.82 moves two C10c rule
families toward the bound, over the same 270 histories: the mirror of
`out_unemployment_rate_down21` is ahead in 0.633 (R17T 0.570) and of
`out63_after_vix_5d_p99.9_up` in 0.622 (0.530). Each change alone moves them
less: R17T with 0.82 reads 0.574 and 0.570, and R17Bd with 0.85 reads 0.596
and 0.574 (with B12 0.575 and F-bear's median at -0.50). Resampling the 270
histories, some rule breaches in 2.5 per cent of draws for R17T, 3.5 for
R17T with 0.82, 8.7 for R17Bd with 0.85 and 23 for R17Bd. On the tape
(1990-2025) the unemployment mirror beats its exposure-matched position by
1.43 points a year, above the model's median of 0.34; the VIX mirror by
-0.08.

With the market's prehistory (`market_prehistory_sessions` 252, sim/r18-opening
8385f016) the same question over more histories. Each arm below is R17Bd with
the prehistory and the one change named; A+B+C is 270 histories, "540" adds
270 more from the held-out blocks 40201-40830, 90201-90830 and 100201-100830.
"Breach odds" resample 270 histories with replacement and count the draws in
which any of the 384 rules breaches (median over +1 or ahead in over 2/3).

| Arm | change | B12 (se) | F-bear median (margin) | breach odds |
|---|---|---|---|---|
| R17TP | R17T, no hazard | 0.474 (0.014) | -0.50 (0.00 se) | 0.086 (A+B+C) |
| R17BdP | none | 0.587 (0.013), 540 | -0.60 (+2.19 se), 540 | 0.052 (540); 0.131 and 0.111 on each half |
| R17BhP | expansion 0.85 | 0.576 (0.011), 540 | -0.60 (+1.98 se), 540 | 0.121 (540) |
| R18f | hazard 4, expansion 0.85 | 0.571 (0.012), 540 | -0.60 (+2.11 se), 540 | 0.138 (540) |
| R18g | knee 0.12, expansion 0.85 | 0.558 (0.012), 540 | -0.60 (+1.78 se), 540 | 0.107 (540) |
| R18a | hazard 7, expansion 0.85 | 0.595 (0.014) | -0.53 (+0.54 se) | 0.289 (A+B+C) |
| R18b | knee 0.08, expansion 0.85 | 0.588 (0.013) | -0.50 (0.00 se) | 0.227 (A+B+C) |
| R18c | expansion 0.835 | 0.582 (0.019) | -0.60 (+1.65 se) | 0.273 (A+B+C) |
| R18d | hazard 6, knee 0.09, expansion 0.85 | 0.585 (0.013) | -0.50 (0.00 se) | 0.252 (A+B+C) |
| R18e | hazard 10, expansion 0.85 | 0.604 (0.013) | -0.50 (0.00 se) | 0.522 (A+B+C) |
| R19V | prehistory 504 with its valuation (`market_prehistory_valuation`, sim/r18-valopen 9a4a524d) | 0.598 (0.012), 540 | -0.60 (+2.16 se), 540 | 0.013 (540) |

A stronger or earlier hazard (R18a, R18b, R18e) raises the odds, through the
rules that read the cycle itself (`out_contraction_trough`, the GDP-growth
event rules, `out_unemployment_rate_down21`); a weaker or later one (R18f,
R18g) gives up B12 and does not lower them measurably. The two rules the
earlier reading named are not a fixed property of the 0.82 setting: over two
independent sets of 270 R17BdP reads `out_unemployment_rate_down21` ahead in
0.607 and 0.574, and `out63_after_vix_5d_p99.9_up` in 0.615 and 0.600, where
R17Bd without the prehistory read 0.633 and 0.622 on A+B+C. A rule's share
ahead over 270 histories has an se of about 0.03, and resampling one set of 270
counts that noise twice, so odds read off a single set run high; the 540-history
figure is the better estimate. F-bear's median sits on the atom at -0.5 (about
a tenth of the bears cut exactly two quarters) on half the sets of 270 and
moves off it only when the share of bears with more than 0.5 of cuts passes
one half; over 540 every arm reads -0.60, and in every draw of 270 from those
540 the median is at or under -0.5.

The market P/E is the cap-weighted mean of $P_i / (E_i n_d B_{i,d})$ over
profitable companies with a P/E between 0 and 200, using the restated
earnings of [Fair value](#fair-value) (`engine.rs:5583-5629`).

| Phase | Min age (months) | Weibull shape $k$ | Weibull scale $\lambda$ | Mean length (months) |
|---|---|---|---|---|
| E | 6 | 1.8 | 90.45 | 81.0 |
| P | 2 | 2.0 | 6.289 | 6.0 |
| C | 4 | 0.7 | 0.546 | 5.5 |
| T | 2 | 1.5 | 2.564 | 3.5 |
| R | 4 | 1.3 | 10.34 | 12.0 |

(`economy/state.rs:263-302`, `economy/state.rs:351-359`)

The scales are **derived**: they are solved so that each phase's mean length,
from the hazard alone, equals the NBER and BEA phase means for 1990 to 2025
(`economy/state.rs:322-331`; `cycle_us_calibration` = 1). The shapes, the
minimum ages, the 0.8 cap and the ladder constants are **chosen**. With the
ladder acting, a run has 1.05 recessions a decade lasting 9.1 months,
against 1.11 and 9.0 in the US data (design note results/macro-cycle §3-4).

At the opening the phase is drawn with probability proportional to its mean
length, and its age from the phase's survival function
(`economy/cycle.rs:350-408`, `economy/cycle.rs:447-468`;
`cycle_stationary_opening` = 1, derived).

### Growth and output

**Timescale:** a shock on entering a phase, a quarterly and a monthly step,
and a daily level update. **State:** real growth $g$ (percent a year) and
real output $Y$.

Each phase has a growth midpoint $\bar g$: E 3.0, P 1.25, C -1.5, T -0.25,
R 2.25 (`economy/state.rs:263-302`). On entering a phase, growth takes a
shock $\delta$: C $-(2 + 2U)$, T -0.5, R +1, E +0.5, P none
(`economy/daily.rs:745-767`), and then growth is updated in three steps:

```math
\text{quarterly: } g \leftarrow \mathrm{clip}\big(g + 0.25\,(\bar g - g) + 0.3\,Z;\ -10,\ 6\big)
```

```math
\text{monthly: } g \leftarrow \mathrm{clip}\big(g + 0.12\,\kappa\,(\bar g - g) + 0.1\,Z;\ -10,\ 6\big),
\quad \kappa = 2 \text{ when growth has the wrong sign for the phase, else } 1
```

```math
\text{daily: } Y_d = Y_{d-1}\Big(1 + \frac{g_d}{100 \cdot 252}\Big)
```

(`economy/daily.rs:787-840`, `economy/daily.rs:803`). "Wrong sign" means
$g > 0$ in C or T, or $g < 0$ in R or E. Fiscal stimulus adds to $g$ at the
end of the monthly step, below.

### Unemployment

**Timescale:** monthly. **State:** unemployment $u$, long-term unemployment
$\ell$, and the structural rate $u^{\ast}$ (all percent).

```math
u_d = \mathrm{clip}\Big(u + 0.3\,\theta^{u}_{\mathcal{P}} + 0.2\,(2 - g) + 0.06\,(u^{\ast} - u)
 - 0.08\,g\,\mathbf{1}[\mathcal{P} \in \lbrace E, R\rbrace,\ g > 1] + 0.06\,Z;\ 2.5,\ 15\Big)
```

(`economy/daily.rs:843-869`). The phase trends $\theta^{u}$ are E -0.05,
P 0, C 0.30, T 0.04, R -0.10. The $0.2 (2 - g)$ term is Okun's law. The
structural rate carries hysteresis (`economy/daily.rs:1052-1064`):

```math
\ell \leftarrow \begin{cases} \ell + 0.05\,(0.4\,u - \ell) & \mathcal{P} \in \lbrace C, T \rbrace \\ \max(0.5,\ 0.97\,\ell) & \text{otherwise} \end{cases}
\qquad u^{\ast} = 4 + 0.3\,\ell
```

All constants are chosen. Unemployment spends much of an expansion on its
2.5 floor: the design note's long-run mean is 3.6% against 5.7% in the US.
Three switches change this release, all off on every preset through pt-v20.
pt-v21 sets one of them, `unemployment_okun_coefficient`, at 0.75; see
[Unemployment's anchor and oil's interior](#unemployments-anchor-and-oils-interior).

### Inflation

**Timescale:** monthly for inflation and wages; daily for the price level.
**State:** inflation $\pi$, wage growth $w$ (percent a year), the price level
$Q$ (CPI).

```math
w \leftarrow w + 0.15\,(w^{\ast} - w),\qquad
w^{\ast} = \begin{cases} \max\big(0.7\,\pi + 0.5\,(u^{\ast} - u),\ 0.8\,\pi\big) & \pi > 3 \\ 0.7\,\pi + 0.5\,(u^{\ast} - u) & \text{otherwise} \end{cases}
```

```math
\pi_d = \mathrm{clip}\Big(\pi + \kappa_\pi\,(2 - \pi) + 0.04\,\theta^{\pi}_{\mathcal{P}}
 - 0.2\,(u_d - u^{\ast}) + W + R^{r} + \Omega - 0.0003\,(e - 100) + 0.0003\,(\tau - 5) + 0.04\,Z;\ \pi_{\min},\ \pi_{\max}\Big)
```

(`economy/daily.rs:926-935`, `economy/daily.rs:895-961`). Most terms read last
month's values; the Phillips term reads this month's unemployment $u_d$,
and the terms are these:

- $-0.2 (u_d - u^{\ast})$ is the Phillips curve.
- $W = 0.08\max(0, w - 2) + 0.02 (w - 4)(\pi - 3) \cdot \mathbf{1}[w > 4, \pi > 3]$ is wage pressure (`economy/daily.rs:937-945`).
- $R^{r}$ is a real-rate drag: $-0.04 (r^{p} - \pi)$ when the policy rate is above inflation, $-0.015 (r^{p} - 3)$ when it is below inflation but above 3, else 0 (`economy/daily.rs:899-905`).
- $\Omega$ is the oil pass-through: $0.01 (o - 80)$ above USD 80 a barrel, $0.005 (o - 50)$ below USD 50 (`economy/daily.rs`, `oil_inflation_effect`). `oil_inflation_passthrough`, on in pt-v21 at 1 and off on every earlier preset, makes it symmetric.
- $e$ is the dollar index and $\tau$ the tariff rate. The tariff rate stays at 5, so its term is 0, unless a scenario changes it.
- The phase trends $\theta^{\pi}$ are E 0.015, P 0.015, C -0.02, T -0.01, R 0.01.

The price level compounds daily: $Q_d = Q_{d-1} (1 + \pi_d / (100 \cdot 252))$
(`economy/daily.rs:1108`).

| Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|
| inflation target | 2.0 | | chosen | |
| `inflation_reversion` $\kappa_\pi$ | 0.55 a month | same | chosen | reference value; it gives too little persistence against FRED CPI (lag-1 autocorrelation 0.936 against 0.978, `params.rs:313-325`) |
| `inflation_floor` $\pi_{\min}$ | -1.0 | same | guard | |
| `inflation_ceiling` $\pi_{\max}$ | 6.0 | same | guard | real CPI inflation peaked at 9.0 in June 2022, so this binds in a 2022-like episode |
| `phillips_curve_coeff` | 0.2 | same | chosen | |

### Fiscal policy

**Timescale:** monthly (`economy/daily.rs:1080-1104`). In contraction and
trough the government runs a stimulus $F$ (percent of GDP a year) and debt
$b$ rises; otherwise the stimulus decays and debt falls slowly in good times:

```math
\mathcal{P} \in \lbrace C, T\rbrace:\ F = \min\big(6,\ 1 + \mathbf{1}[u > 7]\min(4,\ 0.8\,(u - 5))\big),
\quad g \leftarrow g + \frac{0.3\,F}{12},\quad b \leftarrow b + \frac{F}{12}
```

```math
\text{otherwise: } F \leftarrow \max(0,\ F - 0.2),\quad b \leftarrow \max(60,\ b - 0.05) \text{ if } g > 2
```

The fiscal multiplier 0.3 and the other constants are chosen. Debt enters
the 10-year term premium.

### Central bank

**Timescale:** at meetings, which come every 29 to 38 sessions, or 14 to 21
in an inflation crisis ($\pi > r^{p} + 2$ and $\pi > 4$). An emergency meeting
is held at any close where $\pi - r^{p} > 4$ and $\pi > 4$
(`economy/central_bank.rs:178-190`, `economy/central_bank.rs:548-560`). The
design note measures 7.6 meetings a year against the FOMC's 8.

A Taylor rate is computed, and the gap to it gates a decision ladder. The
bank never jumps to the Taylor rate (`economy/central_bank.rs:201-216`):

```math
r^{T} = 2 + 0.5\Big(\frac{\pi}{2} - 1\Big) + 0.5\,(\pi - 2) + 0.5\,(4 - u) + 0.1\,H
= 2.5 + 0.75\,\pi - 0.5\,u + 0.1\,H,
\qquad \Delta = r^{T} - r^{p}
```

$H \in [-1, 1]$ is a hawkish score the ladder moves. The first matching row
sets the change $\delta$ in the policy rate (`economy/central_bank.rs:233-337`):

| Row | Condition | Change $\delta$ (points) |
|---|---|---|
| 1 | $g < -2$ and $u > 10$ | $-(1 + 0.5U)$ |
| 2 | $g < 0$ and $u > 8$ | $-(0.5 + 0.5U)$ |
| 3 | $u > 8$ and $\pi < 3$ | -0.75 |
| 4 | C or T, $g < 0$, $u > 7$ | -0.25 if $\pi < r^{p}$, else 0 |
| 5 | C or T, $r^{p} > 5$ | -0.25 |
| 6 | $\Delta > 2$, $\pi > 6$, $r^{p} < \pi$ | +1.0 or +0.75 (unreachable: needs $\pi > 6$) |
| 7 | $\pi - r^{p} > 2$ and $\pi > 4$ | +0.75 |
| 8 | $\Delta > 1$ and $\pi > 4$ | +0.5 |
| 9 | $\Delta > 0.5$ and $\pi > 3$ | +0.25 |
| 10 | $\Delta < -0.5$, $u > 5$, $\pi < 4$ | -0.25 |
| 11 | $\Delta < -1$, C or T, $\pi < 3.5$ | -0.5 |
| 12 | $\pi > 3.5$, $r^{p} < \pi$, $u < 8$ | +0.5 if $\pi - r^{p} > 3$, else +0.25 |
| 13 | $\Delta > 0.5$, $u \le 5$, not C or T | +0.25 (lift-off) |

A rise is scaled by an urgency factor $\max(1, \lvert \pi - 2 \rvert / 2)$,
and the rate is kept in $[0, 8]$:
$r^{p} \leftarrow \mathrm{clip}(r^{p} + \delta;\ 0,\ 8)$
(`economy/central_bank.rs:220-225`, `economy/central_bank.rs:341-395`).

The Taylor coefficients, the ladder and the meeting spacing are chosen, from
the reference implementation. Row 13, `fed_liftoff_rule` = 1, was added in
pt-v19: it mirrors the ladder's cut rows, and it moved the long-run mean
policy rate from 1.7% to 2.6%, against 2.9% in the US 1990 to 2025 (design
note results/macro-cycle §4).

The ladder has no market-stress term: on pt-v20 a meeting within 42
sessions of a VIX of 30 to 40 cuts 0.29 of the time and hikes 0.25, against
0.53 and 0.01 on FRED's target rate over 1990-2025. `fed_stress_cut`, on in
pt-v21 at 0.1 and 0.0 on every earlier preset (thirteenth registration, r13
audit), adds one after row 13: at
a meeting where the highest VIX published since the last meeting, $V$, is at
or over `fed_stress_vix` ($V_0$), with $\pi$ under target plus
`fed_stress_inflation_gap` and $r^{p} > 0$, the bank cuts
$\min\big(r^{p},\ c \cdot \min(4,\ 1 + \lfloor (V - V_0)/10 \rfloor)\big)$,
replacing a smaller cut or any rise, with $H$ moved by $-0.2$
(`economy/central_bank.rs`, "The stress cut"). The level resets at each
meeting and is carried in the snapshot while the cut is on.

**The Fed put** (`fed_put_gain`, on in pt-v21 at 3, off on every earlier
preset) is an overlay on the
ladder. Off zero, each close adds the log change of total public market cap
to an intermeeting return $I$, which a meeting reads and restarts. At a
meeting with $\pi < 4$ the put asks for
$E = g_F \max(0, -I - \theta_F)$ (`fed_put_gain`, `fed_put_threshold`),
and the cut $c = \min(\lfloor E \rceil_{0.25},\ r^{p})$, rounded to a quarter
point, replaces $\delta$ when $\delta > -c$. A VIX at or above 30 then holds
any rise. What the overlay takes off the ladder's path is owed, $O$, and
added to a stock $P$ that decays at `fed_put_half_life` sessions; the
ladder reads $r^{T} - O$, and a calm meeting (VIX under 30, no put cut, the
ladder not cutting) returns a quarter point while $O - P > 0.125$. With
`fed_put_emergency_vix` set, a VIX close at or above it, with $\pi < 4$ and
$r^{p} > 0$, brings the next meeting forward to that close once 21 sessions
have passed since the last. The put's arithmetic takes no draw; a meeting
the VIX calls is an ordinary meeting, so it takes a meeting's economy draws
off the calendar and re-anchors the corporate yield at that close's VIX
(`economy/central_bank.rs:208-213`, `economy/central_bank.rs:345-385`,
`economy/central_bank.rs:396-410`, `engine.rs:4908-4926`,
`engine.rs:5173-5191`).
The put acts after the stress cut (`fed_stress_cut`), so with both on the
larger cut stands; the growth cut (`fed_growth_cut`) is a ladder row, which
the put reads as $\delta$. A policy rate pinned through the close
(`macro_pins_hold`) holds against the ladder, the stress cut and the put
alike, and the put neither cuts nor returns anything at that meeting. The
three overlays have not been fitted together.

**The put's unanswered fall** (`fed_put_carry` $\kappa$, on in pt-v21 at 1,
off on every earlier preset, read only with the put on). The put rounds its
ask to a quarter point, so at
$g_F = 3$ an intermeeting fall under about 4.2 per cent asks for nothing, and
a bear that falls 3 or 4 per cent between each pair of meetings is never
answered. Off zero, a meeting at which the put was live ($\pi < 4$, the rate
not pinned) and after which the rate is still above zero restarts $I$ at
$\kappa \min(0,\ I + c / g_F)$ instead of zero, with $c$ the cut the meeting
took, whichever of the ladder, the stress cut and the put chose it: the fall
that cut did not answer, which the index's later returns add to or take
back. The priced put reads the same $I$. No draw, and no state beyond $I$
(`economy/central_bank.rs`, "The unanswered fall").

**The drawdown hold** (`fed_drawdown_hold` $x$, on in pt-v21 at 0.12, off on
every earlier preset). The
engine keeps the log change of total public market cap at each of the last
252 closes, and at a meeting reads the index's log fall from its highest
close in that window. At $x$ or more, with $\pi$ under target plus
`fed_stress_inflation_gap`, the meeting holds any rise and the put returns
nothing, exactly as within the stress hold's sessions; a cut stands. The
stress hold reads the VIX, which reverts within weeks while the index stays
down, so without this a bear whose VIX has settled is hiked into. No draw;
the window and its base are carried in the snapshot and the state hash while
$x$ is set, and a market prehistory hands its window to the run
(`engine.rs`, `index_drawdown`, `book_drawdown_close`).

**The stress hold** (`fed_stress_hold`, on in pt-v21 at 42, off on every
earlier preset) keeps a count of
sessions since the last close whose published VIX was at or over
`fed_stress_vix`. At a meeting within that many sessions of it, with $\pi$
under target plus `fed_stress_inflation_gap`, a rise the ladder chose is
held ($H$ does not move) and the put returns nothing; a cut stands. It runs
after the stress cut and before a pinned rate's hold. No draw; the count is
carried in the snapshot and the state hash while the dial is set.

**The priced path** (`treasury_path_pricing` $k$, `treasury_path_half_life`
$h$, on in pt-v21 at $k$ = 1 and $h$ = 63, off on every earlier preset) is
the market's forecast of the policy rate's
further change, $M$: each change the bank makes is added to $M$, and $M$
decays by $2^{-1/h}$ a session. The 10-year's daily anchor and the 2-year's
formula read $r^{p} + kM$ in place of $r^{p}$, the meeting's 10-year target
adds $kM'$ (the forecast after the decision) and its surprise adds
$k(M' - M)$, so a change the ladder's own serial correlation makes
forecastable is priced on the day it is published rather than in the weeks
after. The put's cut and give-back are left out of $M$ (a change counts
with the change in $O$ added back). No draw; $M$ is carried in the snapshot
and the state hash while $k$ is set. `treasury_policy_damping` $d$ (on
in pt-v21 at 0.5, off on every earlier preset) pulls the rate the 10-year's
anchor and the meeting's target
read toward a neutral 2.5 by the share $d$, and scales the meeting's
surprise by $1 - d$, so with both on a decision moves the 10-year by
$(1-d)(1+k)$ of itself on the day; the 2-year reads the rate undamped.

**The anticipated meeting** (`policy_anticipation` $a$, on in pt-v21 at 1.8
and off on every earlier preset, and `policy_anticipation_cut_share` $c_a$, 0
on every preset). At each close the
engine holds the next meeting now, on the economy as the market sees it (the
published phase, growth and VIX; inflation, unemployment and the rate as they
stand), with tonight's overlays and a silent draw source, so no stream moves.
Its change $S$ (a cut times $c_a$) is priced in proportion to the share $w$ of
the interval from the last meeting to the next already elapsed:
$P = a\,w\,S$. The 10-year's anchor and the 2-year's formula read
$r^{p} + kM + P$, damped as the priced path is, and the close moves the
10-year and the corporate yield by $(1-d)\,\Delta P$ and the 2-year by
$(0.85 + 0.15(1-d))\,\Delta P$. A meeting starts $w$ again at 0, so what was
priced leaves the curve on the day the decision lands and the day's move is
the surprise. $a$ above 1 prices more than the next meeting, as the curve does
through a run of decisions. No draw; $P$ is carried in the snapshot and the
state hash while $a$ is set.

Why. With the curve learning each decision on the day, a hike moved the
10-year and the corporate yield by the whole change that session (R16A, 90
held-out histories: the index fell 1.08 per cent on a hike's day), and the
priced path's forecast then decayed until the next meeting, so the yields
drifted down and the index up for weeks: 2x the index for 21 sessions after a
published rise beat the exposure-matched position in 0.62 and 0.64 of the
histories on the two held-out sets and 0.68 on the thirteenth grade's exam
(C10c's bound is 2/3). Around the 51 FOMC target hikes of 1990-2025 the
2-year rose 0.46 points over the 63 sessions before and 0.01 on the day, the
10-year and Baa did not move on the day, and the S&P 500's excess return was
-0.13 per cent on the day (se 0.18) and -0.64 by 21 sessions (se 0.49).

Measured on a candidate (pt-v21 sets $a$ at 1.8; no earlier preset sets it).
On R16A with $a = 2$ and
`treasury_haven_gain` at 0.010 instead of 0.015 (boxes c10c1 to c10c3, two
held-out sets of 90 histories: A, seeds 201-230, 501-530 and 801-830, and B,
the same plus 20000), around a hike the 2-year rises 0.59 points over the 63
sessions before and moves -0.09 on the day, the 10-year and the corporate
yield +0.03, and the index's excess is -0.04 per cent on the day and -0.12
and -0.07 by 21 sessions on the two sets, against R16A's -1.08/-0.75 and
-1.06/-0.70. The C10c rule 2x for 21 sessions after a published rise reads
median -0.19 and -0.16 points a year, ahead in 0.41 and 0.44 of histories
(R16A +0.36/0.62 and +0.41/0.64), and no mirrored rule breaches on either set
(R16A: none and three, all 2x after a rise in the 10-year or the corporate
yield). The haven's cut keeps the monthly stock-bond correlation H5 at -0.318
and -0.317 against a floor of -0.35 (at $a = 2$ alone -0.336 and -0.333),
since the curve no longer jumps with the index on a hike's day. R4 on the
held close reads 0.374 and 0.356 (R16A 0.388 and 0.372, ceiling 0.39).

**Quantitative easing** starts when the policy rate is at or below 0.25 in a
contraction, with purchases of USD 120bn a month, and tapers by 15 a meeting
in expansion (`economy/central_bank.rs:484-517`). On pt-v20 it reaches
prices only through a small cut in the 10-year yield; its direct channels
into valuation are switched off.

### Bond yields

**Timescale:** daily, in the macro step after the close, for the 10-year,
the 2-year and the corporate yield; the central bank also resets the 2-year
and the corporate yield at each meeting, all in percentage points.

```math
y^{10} \leftarrow \mathrm{clip}\big(y^{10} + 0.05\,(r^{p} + \mathrm{TP} - y^{10}) + \sigma_{10}\,Z;\ 0.5,\ 12\big),
\qquad
\mathrm{TP} = 1 + \max(0,\ 0.3\,(\pi - 2)) + \max(0,\ 0.002\,(b - 100))
```

The 2-year is its own process, pulled toward the formula it used to equal,
with its own noise (`economy/daily.rs:1616-1634`):

```math
y^{2}_{d+1} = \mathrm{clip}\Big(y^{2}_d + 0.05\,\big(0.85\,r^{p} + 0.15\,y^{10} - y^{2}_d\big) + \sigma_{2}\,Z;\ 0,\ 12\Big)
```

Here $y^{10}$ is the value just stepped (`economy/daily.rs:1574-1616`). At a
meeting the 2-year is reset to $0.85\,r^{p} + 0.15\,y^{10}$
(`economy/central_bank.rs:441-442`).

**The Treasury haven and the priced put** (`treasury_haven_gain`,
`treasury_put_pricing`, on in pt-v21 at 0.014 and 1, off on every earlier
preset). With $\pi < 4$ the haven takes
$h \max(0, \mathrm{VIX} - 20)$ off $\mathrm{TP}$, in the daily step and in
the meeting's 10-year target. The priced put replaces $r^{p}$ in the
10-year's anchor and the 2-year's formula by $r^{p} - \kappa \min(E, r^{p})$,
and the meeting's 10-year surprise is $\delta + \kappa \min(E, r^{p})$ rather
than $\delta$, so a priced cut is not news on the day
(`economy/daily.rs:1577-1602`, `economy/central_bank.rs:411-439`).
The haven and the flight to quality below both make the stock-bond
correlation more negative at low inflation, and they are fitted together:
the flight to quality is an increment the 10-year's 5 per cent pull erases
in about 60 sessions, the haven a level that lasts while the VIX does.

**Flight to quality.** The session's cap-weighted index return $R_d$, in
percent, open to close, moves both Treasury yields the same day
(`economy/daily.rs:1636-1670`):

```math
\Delta^{Q}_d = \begin{cases} +g_Q\,R_d & \pi < 3 \\ -g_Q\,R_d & \pi > 4 \\ 0 & \text{otherwise} \end{cases},
\qquad
y^{10} \leftarrow y^{10} + \Delta^{Q}_d,\quad y^{2} \leftarrow y^{2} + \Delta^{Q}_d
```

So in the usual low-inflation regime a 1% fall in the index takes 0.8 basis
points off both yields, and Treasuries rally when stocks fall.

**The corporate yield** moves every session by the change in the 10-year
and by the meeting formula's own VIX term, and the meeting re-sets its level
(`economy/daily.rs:1672-1723`):

```math
y^{c}_{d+1} = \max\Big(y^{c}_d + \big(y^{10}_{d+1} - y^{10}_d\big) + 0.02\,m_{\mathcal{P}}\,\big(X_{d+1} - X_d\big),\ \ y^{10}_{d+1} + 0.8\Big)
```

At a meeting the 10-year is pulled halfway to $r^{p} + 1 + \max(0, 0.3(\pi - 2))$
plus the rate change, and the corporate yield is set
(`economy/central_bank.rs:420-481`):

```math
y^{c} = y^{10} + \mathrm{clip}\Big(\big(1 + 0.02\,(X - 12)\big)\,m_{\mathcal{P}};\ 0.8,\ 6\Big),
\qquad m:\ E\ 1.0,\ P\ 1.1,\ C\ 2.8,\ T\ 3.5,\ R\ 1.4
```

A daily floor, $y^{c} \ge y^{10} + 0.8$, also applies
(`economy/daily.rs:1815-1825`). So the discount rate that fair value reads
moves every session with the 10-year and the VIX, and is re-anchored at
meetings.

**Credit's VIX slope and leverage term** (`corporate_spread_vix_cut` $c$,
`corporate_spread_equity_gain` $g_L$, `corporate_spread_equity_half_life`
$H_L$, on in pt-v21 at $c$ = 1, $g_L$ = 1.8 and $H_L$ = 126, off on every
earlier preset). The VIX reverts within days of a sell-off while
the index stays down, so a spread that is the VIX's formula gives most of a
down day's widening back within the month. With $g_L$ set, each close steps
the index's log fall below its own slow average on the session's return from
the last close, $R_d$ in percent:

```math
D_{d+1} = 2^{-1/H_L}\,\big(D_d - \ln(1 + R_d/100)\big)
```

and the spread formula, at the meeting, in the close's daily move and in the
rate indices' live projection, becomes

```math
\mathrm{clip}\Big(\big(1 + 0.02\,(1 - c)\,(X - 12) + g_L\,D\big)\,m_{\mathcal{P}};\ 0.8,\ 6\Big)
```

so the daily move carries $g_L\,m_{\mathcal{P}}\,(D_{d+1} - D_d)$ beside the
cut VIX term, and a spread widened by a fall stays wide while the index stays
below its average (a structural credit model's leverage: Merton 1974;
Collin-Dufresne, Goldstein and Martin 2001). A VIX pin removes the VIX term
only. The gap is `EconomyState::spread_equity_gap`, carried by the snapshot
and the state hash only while $g_L$ is set. No draw.

| Symbol | Dial | pt-v20 (pt-v19) | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\sigma_{10}$ | `treasury_10y_noise` | 0.038 (0.03) pp a session | same | measured | daily sd of the 10-year's change, FRED DGS10 2015 to 2025, 5.41 bp; 5.12 bp at 0.038 on 90 held-out histories (box ptv20vr9), and graded row R2 reads 4.96 bp against a band of 4.54 to 6.27 (box ptv20g6). pt-v20 before its graded arm had 0.025, which read 4.16 to 4.25 bp and failed R2 |
| $\sigma_{2}$ | `treasury_2y_noise` | 0.022 (0, formula only) | 0.008 | measured | FRED DGS2, 5.23 bp; row R1 reads 3.87 against 3.65 to 6.80 (box ptv20g6) |
| $g_Q$ | `flight_to_quality_gain` | 0.008 (0.02, never fired) pp per % | 0.013 | measured | correlation of stock and Treasury returns, SPY against IEF 2015 to 2025, -0.16; row R3 reads -0.136 (box ptv20g6) |
| | `flight_to_quality_day` | 1 (0) | same | derived | a switch: the step reads the session's own return |
| | `corporate_yield_daily` | 1 (0) | same | derived | a switch; stock and investment-grade bond returns, SPY against LQD, correlate +0.27, and row R4 reads +0.200 (box ptv20g6) |
| | `daily_credit_floor_gain` | 1.0 | same | chosen | without the floor the spread drifted to 0.42 points within 121 days (`params.rs:4884-4899`) |

The 2-year's 0.05 pull, the regime thresholds, the spread multipliers and
the 0.8 floor are chosen. One limit: the model's inflation almost never
leaves the under-3% regime, so stocks and Treasuries are nearly always in
flight to quality. They match the pooled 2015-24 correlation, not the
positive one of a 2022-style inflation regime (`params.rs:7221-7224`).

### The aggregate earnings cycle

**Timescale:** one step a session, in the macro step, after the phase
check and before the central bank (`engine.rs:5099-5124`). **State:**
$\chi_d$, a log level on every company's earnings beyond what nominal
output gives them (`economy/state.rs:430-435`).

The level is pulled toward a target set by the business cycle
(`engine.rs:1523-1533`, `engine.rs:5103-5124`):

```math
\chi^{\ast}(\mathcal{P}) = \begin{cases} -\delta_E & \mathcal{P} \in \lbrace C, T \rbrace \\ \delta_E\,u_E & \mathcal{P} \in \lbrace E, P, R \rbrace \end{cases},
\qquad
\chi_{d+1} = \chi_d + \alpha_E\big(\chi^{\ast}(\mathcal{P}_{d+1}) - \chi_d\big),
\qquad \alpha_E = 1 - 2^{-1/H_E}
```

Earnings therefore fall toward $e^{-0.2} - 1 = -18.1\%$ in a contraction
and trough and recover toward $+1.8\%$ otherwise; the upside is set so the
level averages to zero over a cycle. The run opens at the target of the
phase it opens in (`engine.rs:1213-1220`). $\chi$ multiplies every
company's restated earnings and book value through $n_d$ in
[Fair value](#fair-value), so a move written at the close reaches prices on
the next session.

| Symbol | Dial | pt-v20 (pt-v19) | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\delta_E$ | `earnings_cycle_depth` | 0.2 (0, off) | same | measured | the depth at which row E1, the median fall of aggregate earnings in a contraction, meets Shiller's reported earnings around NBER recessions (median -17%) once the market's plain shocks are permanent: -0.280 at 0.35, -0.173 at 0.2, -0.132 at 0.15 (box ptv20vr4); graded, E1 reads -0.172 (box ptv20g6). pt-v20 before its graded arm had 0.35, picked on row B9 when the cycle carried the index's yearly spread; no error bar |
| $u_E$ | `earnings_cycle_upside` | 0.09 (0) | same | derived | $q/(1-q)$ with $q$ = 9/108, the share of months in contraction and trough in the US phase table |
| $H_E$ | `earnings_cycle_half_life` | 60 sessions | 150 | chosen | not searched |

With the cycle carrying part of the index's year-to-year variance, pt-v20
took transient variance out by as much before its graded arm, and keeps it
out: the market factor's daily sigma is
0.85 of pt-v19's and market jumps come at half the rate (see the tables in
[The factor structure](#the-factor-structure) and [Jumps](#jumps)). Both
were picked on the same grid, which held the bear-market count (row B3)
and the spread of annual returns (row B9) in band.

### Oil and the dollar

**Timescale:** daily. Oil reaches prices only through inflation; the dollar
reaches them through inflation and oil.

```math
o \leftarrow \mathrm{clip}\Big(o + 0.03\,\big[(75 + 3g)(1 + a_d) - o\big] + p^{I} - 0.08\,(e - 100) + \mathrm{opec} + 2Z;\ 35,\ 150\Big)
```

(`economy/daily.rs:1208-1241`). $a_d = 0.03 \sin(2\pi (d' - 62)/252)$ is a
seasonal term on the target, with $d'$ the day of the macro year. $p^{I}$ is
an inventory pressure that is zero while inventory is between 40 and 60
(inventory is a driftless random walk at `oil_supply_response` = 1,
derived; `oil_inventory_reversion`, on in pt-v21 at 0.002 and off on every
earlier preset, pulls it back
toward 50). Every 63 sessions an OPEC decision adds $\pm(2.5 + 3U)$ with
probability 0.55 when the price is more than USD 10 from 80, or
$3(U - 0.5)$ with probability 0.2 otherwise (`economy/daily.rs:1168-1206`).

```math
e \leftarrow \mathrm{clip}\Big(e + 0.02\,\big(100 + 3\,(r^{p} - 2.5) - e\big) + 0.05\,(X - 25.5)^{+} + 0.3\,Z;\ 80,\ 130\Big)
```

(`economy/daily.rs:1297-1314`). Above a VIX of 25.5 the dollar gets a
safe-haven bid (`usd_crisis_vix_threshold`, chosen).

Gold, copper, housing, confidence, the fear and greed index and the trade
balance are computed each day and read by nothing on the price path.

### True and published state

The economy's true state drives prices. An observer reads the state as
published, which can follow the true state the way the real agencies
publish it. The NBER dates a turn of the business cycle months after it
happens: it announced the December 2007 peak on 1 December 2008 and the June
2009 trough on 20 September 2010. The BEA's first estimate of a quarter's GDP
growth comes about a month after the quarter ends.

Five dials, added in 0.8.5 and 0 on every preset through pt-v19, act on
this. Two publish the phase and GDP growth late. One makes the true
unemployment rate turn over months instead of in one monthly step. One
points the fear and greed index at the published figures, and one prices a
macro step at the moment it is published. They answer an independent audit
of pt-v20, which found that timing rules on the reported macro data beat
buy-and-hold. Holding the roster and going to cash while
`macro_fields["cycle"]` read contraction or trough gained 4.39 points a year
over holding, in 30 of 30 21-year histories (pt-v20 audit, finding 1; the
audit is in the project's unpublished design notes).

| Figure | The true value is read by | The published value is read by |
|---|---|---|
| business-cycle phase | the phase hazards and phase terms of the macro step, the central bank, the spread multiplier, the earnings cycle and its anticipation, the stress intensity; a pin writes it; `state_snapshot()["economy"]["cycle_phase"]` | `macro_fields["cycle"]`, `macro_state.cycle`, a World's trace rows, the LLM adapters' observations, the fear and greed index under its switch |
| GDP growth | output, earnings, unemployment, the phase hazards, the central bank; a pin writes it; `state_snapshot()["economy"]["gdp_growth"]`, the `macro.growth` intervention's read, the Oracle's drift | `macro_fields["gdp_growth"]`, `macro_table()` and so a dataset export's `macro.arrow`, the fear and greed index under its switch |

Every other field of `macro_fields` reports the value the engine holds. The
policy rate is known from the meeting that sets it, and yields, the VIX and
oil are market prices, known as they print. Under `vix_stress_premium`,
which is on in pt-v21 at 3 and off on every earlier preset, the published VIX
carries a premium over the
state in stress (see The published quote). Inflation and unemployment change
only at the monthly step and are reported on the close that computes them,
where the BLS publishes both a week or two after the month. A sandboxed
agent's market view serves `macro_fields` through an allowlist of published
fields and refuses `state_snapshot()`, which carries the true state.

With every dial at 0 the published value is the true one, the snapshot and
the state hash are the ones they were before 0.8.5, and every known-answer
digest is unchanged.

### The published phase

**Timescale:** at every close. **State:** the phases of the last $L_c + 1$
closes, oldest first.

With `cycle_publication_lag` $L_c$ in sessions, the phase published after
close $d$ is the true phase $L_c$ closes before:

```math
\hat{\mathcal{P}}_d = \mathcal{P}_{\max(d - L_c,\ 0)}
```

$\mathcal{P}_0$ is the opening phase, the one the run opens in after the
burn-in and the stationary opening draw, so it stays published until $L_c$
sessions have closed (`engine.rs:1379-1384`). At the end of each close's
macro step, after the cycle and the central bank, the engine appends the
phase to the history and drops the oldest (`engine.rs:1431-1440`,
`engine.rs:5164`). The burn-in runs the same step, and the construction then
refills the history with the opening phase (`engine.rs:1224`).

A scenario or a `pin_macro` that sets the phase sets the true phase at once,
so prices, the earnings cycle and the hazards react as they did before. The
new phase is published $L_c$ closes after the first close it holds for, and
a pinned phase reads back from `macro_fields["cycle"]` only then. The
packaged `recession.yml`, for example, sets contraction on day 50 and trough
on day 365, and an observer reads them about $L_c$ sessions later. A
turn announced in a World's trace rows or a hosted market log's cycle events
arrives on the same schedule.

`state_snapshot()["economy"]["cycle_phase"]` stays the true phase. While
$L_c > 0$ the economy block also carries `cycle_history`, the $L_c + 1$ phase
names oldest first, and the state hash takes the history after the phase, as
a `u32` length then each name (`engine.rs:6705-6710`,
`manifest.state_hash`). A restore refuses a history of the wrong length, or
any history on an engine whose lag is 0. It refuses a snapshot without one
under the dial too, naming the dial, because a history refilled from the
restored phase would publish a path the original run did not.

| Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|
| `cycle_publication_lag` $L_c$ | 252 (0, off); a whole number of sessions up to 2520 | same | chosen | the NBER's announcement delay, about a year for the 2007-09 recession; the owner's ruling of 2026-09-25 |

### Published GDP growth

**Timescale:** a quarterly figure, released at a close. **State:** the
figure published, the quarter being averaged (its index, the closes in it
and their sum), and the averaged quarters waiting for release.

With `gdp_publication_lag` $L_g$ in sessions, growth is reported as a
quarterly figure. Quarter $k$ is the days $kq$ to $(k+1)q - 1$ of the macro
calendar, with $q = 63$ on pt-v19 and pt-v20 (90 on the 365-day calendar
of the presets before pt-v19), and day 0, the opening, is the first day of quarter 0.
$g_d$ is the true growth after close $d$, and $g_0$ the opening growth. A
quarter's figure is its mean, released on the close $L_g$ sessions after its
last day:

```math
\bar g_k = \frac{1}{q} \sum_{d = kq}^{(k+1)q - 1} g_d,
\qquad
\hat g_d = \begin{cases} g_0 & d < q - 1 + L_g \\ \bar g_{k^{\ast}},\quad k^{\ast} = \max\lbrace k : (k+1)q - 1 + L_g \le d \rbrace & \text{otherwise} \end{cases}
```

(`engine.rs:1268-1273`, `engine.rs:1337-1364`). The step runs at the end of
each close's macro step, after the phase is recorded (`engine.rs:5168`):
the close's growth joins its quarter, the first close of a new quarter queues
the last one's mean, and every figure due by that close is released. The
construction seeds the figure with the opening growth after the burn-in
(`engine.rs:1226`). A pin on growth writes the true growth, which reaches the
published figure only through the mean of the quarter it falls in.

The daily growth steps at every change of phase: the growth shock on entering
a contraction is $-(2 + 2U)$ points (see
[Growth and output](#growth-and-output)), so a daily figure gave the turn
away on the day it happened. A quarterly mean released late dilutes and
delays that step as the real figure does.

`state_snapshot()["economy"]["gdp_growth"]` stays the true daily growth.
While $L_g > 0$ the economy block also carries `gdp_publication`, with the
keys `published`, `quarter`, `count`, `sum`, `pending_days` and
`pending_values`, in the economy's percent. The state hash takes it after the
unemployment impulse below: the published figure, the quarter, the count, the
sum, then a `u32` count of pending releases and each one's day and figure
(`engine.rs:6719-6730`). A restore refuses the block on an engine whose lag
is 0, a quarter with no close in it, a non-finite figure and releases out of
order. It refuses a snapshot without the block under the dial too, naming
the dial.

| Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|
| `gdp_publication_lag` $L_g$ | 0, off (growth reported daily); a whole number of sessions up to 2520. pt-v20 sets 21 | same | chosen | the BEA's advance estimate, about a month after the quarter; the owner's ruling of 2026-09-25 |

### Unemployment's adjustment

**Timescale:** monthly. **State:** the impulse $m$, the monthly change the
rate is making from its cyclical drivers, in points a month.

[Unemployment](#unemployment) moves at each monthly step by the NAIRU pull,
the noise and its cyclical drive in full,

```math
D = 0.3\,\theta^{u}_{\mathcal{P}} + 0.2\,(2 - g) - 0.08\,g\,\mathbf{1}[\mathcal{P} \in \lbrace E, R\rbrace,\ g > 1]
```

(`economy/daily.rs:688-698`), where $g$ is the month's growth after its
monthly step. So the first monthly step of a contraction carried a rise of
about 1.2 points, four times the spread of a monthly change otherwise, and
announced the turn within a month (desk seeds 201 to 212, 2026-09-25). With
`unemployment_adjustment_half_life` $H_u$ in sessions, the drive reaches the
rate through a partial adjustment (`economy/daily.rs:859-883`):

```math
m_d = m + a\,(D - m),
\qquad
a = 1 - 0.5^{M / H_u},
\qquad
u_d = \mathrm{clip}\big(u + m_d + 0.06\,(u^{\ast} - u) + 0.06\,Z;\ 2.5,\ 15\big)
```

$M$ is the macro month in sessions, 21 on pt-v19 and pt-v20. The NAIRU pull
and the noise are as before, and the noise draw is taken in the same place. The impulse opens at the drive of the starting economy,
before the burn-in, which then runs it (`engine.rs:1209`,
`engine.rs:1243-1252`). At $H_u = 84$ sessions, $a = 0.159$, and the first
monthly rise of a contraction is about 0.16 points on the same seeds. US
unemployment rose from 4.3% to 5.5% over the 2001 recession and from 5.0% to
9.5% from December 2007 to June 2009, by 0.1 to 0.3 points in each first
month.

This dial moves the true unemployment rate, not a published copy of it, and
so moves everything that reads the rate: inflation, confidence, the central
bank and the phase hazards. While $H_u > 0$ the snapshot's economy block
carries `unemployment_impulse`, and the state hash takes it after the phase
history and before the GDP figure (`engine.rs:6620-6622`). A restore refuses
it on an engine whose half-life is 0, and refuses a snapshot without it on
an engine whose half-life is set.

| Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|
| `unemployment_adjustment_half_life` $H_u$ | 84 (0, off); up to 2520 sessions | same | fitted | FRED UNRATE over the 2001 and 2007-09 recessions; matched to their first months, with no standard error |

### Unemployment's anchor and oil's interior

**Timescale:** monthly for unemployment and the pass-through, daily for oil
inventory. **State:** none beyond the fields above. Five switches, each 0.0
on every preset through pt-v20, where each is a branch to the arithmetic
above and is left out of the model's digest, and none of them takes a random
draw. pt-v21 sets three: `unemployment_okun_coefficient` at 0.75,
`oil_inventory_reversion` at 0.002 and `oil_inflation_passthrough` at 1.

Two subsystems have no interior fixed point as shipped. Unemployment's
cyclical drive averages -0.26 points a month on pt-v20 (seeds 101 to 108,
5,292 sessions), -0.46 in an expansion, against a NAIRU pull of 0.06 of the
gap, so the rate runs to its 2.5 floor: over 1,008 sessions it sits there on
78 per cent of days and ends there on 5 of 8 seeds (issue #172). Oil
inventory integrates noise with nothing pulling it back, and outside 40 to
60 it pushes oil by up to 3.2 a day against oil's reversion of 0.03, so a
long excursion pins oil at a clamp (issue #170). And the oil pass-through
pays a rise above 80 and almost nothing of a fall to 50, a positive mean
about oil's own anchor (issue #171).

With `unemployment_okun_coefficient` $\beta > 0$ the drive is Okun's law as
the annual relation the shipped comment states, divided over twelve
releases, with no recovery term:

```math
D = 0.3\,\theta^{u}_{\mathcal{P}} + \frac{\beta}{12}\,(2 - g)
```

in place of $0.2 (2 - g)$ a month plus the recovery term, which together
take an expansion down about 5.5 points a year. With
`unemployment_natural_pull` $k > 0$ the pull is $k (u^{\ast} - u)$ in place
of $0.06 (u^{\ast} - u)$, and with `unemployment_natural_rate` $u_0 > 0$
the NAIRU is $u^{\ast} = u_0 + 0.3\,\ell$ in place of $4 + 0.3\,\ell$, so the
rate and its NAIRU move together and the Phillips gap does not.

With `oil_inventory_reversion` $\kappa > 0$ inventory $I$ closes $\kappa$ of
its gap to 50 each day beside demand, supply and noise:

```math
I \leftarrow \mathrm{clip}\big(I - (0.15\,g - S + 0.5\,Z) + \kappa\,(50 - I);\ 0,\ 100\big)
```

so at `oil_supply_response` = 1 it is stationary with a standard deviation
of $0.5 / \sqrt{2\kappa}$. With `oil_inflation_passthrough` $c > 0$ the
pass-through is $\Omega = 0.01\,c\,(o - 81)$, where 81 is oil's reversion
target at the 2 per cent trend growth Okun's law pivots on.

| Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|
| `unemployment_okun_coefficient` $\beta$ | 0 (off); up to 2.4 | 0.75 | not set | Okun (1962) about 1/3; Ball, Leigh and Loungani (2017) about 0.4 to 0.5 for the US |
| `unemployment_natural_pull` $k$ | 0 (0.06); up to 1 | same | not set | a monthly share; UNRATE's 120-month windows have a lag-12 autocorrelation of 0.53 (median, 1948 to 2026) |
| `unemployment_natural_rate` $u_0$ | 0 (4.0); up to 8 | same | not set | FRED NROU: 4.40 to 4.75 over 2015 to 2026, mean 4.97 over 1990 to 2026 |
| `oil_inventory_reversion` $\kappa$ | 0 (off); up to 1 | 0.002 | not set | the theory of storage (Working 1949; Brennan 1958; Pindyck 1994); the coefficient is not identified there |
| `oil_inflation_passthrough` $c$ | 0 (off); up to 3 | 1 | not set | Kilian and Vigfusson (2011) find no asymmetry; 1.0 is the shipped 0.01 a dollar above 80 |

### The fear and greed index

**Timescale:** daily. **State:** the index $F \in [0, 100]$.

```math
F_d = \mathrm{clip}\big(F + 0.25\,(B - F) + 2\,Z;\ 0,\ 100\big),
\qquad
B = 50 + 3\,g' - 0.8\,(X - 15) + b(\mathcal{P}') + 5\,r_d
```

(`economy/daily.rs:1725-1750`). $r_d$ is the day's index return in percent,
and the phase bonus $b$ is E +15, P +5, C -25, T -20, R +10. The index feeds
consumer confidence and gold (`economy/daily.rs:978`,
`economy/daily.rs:1278`), and through confidence the housing figures and
copper. Nothing a price, the central bank, the cycle or a draw reads is
downstream of it. It is reported as `macro_fields["fear_greed_index"]` and
`macro_state.fear_greed_index`.

With `fear_greed_published_inputs` off, $\mathcal{P}'$ and $g'$ are the true
phase and growth, so the index fell about 35 points in the five sessions
after a contraction began and announced the turn to anyone reading it. With
the switch on they are the published phase and growth as of the previous
close, read before the step (`engine.rs:5078-5082`), so the index steps when
the turn is published. The switch adds no state, and with both publication
lags at 0 it changes nothing. On desk seeds 201 to 212, with the cycle lag at
252, the GDP lag at 21 and the unemployment half-life at 84, a rule that trades a five-session
fall in the index beat holding in 1 of 12 histories with the switch on,
against 9 of 12 with it off.

| Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|
| `fear_greed_published_inputs` | 1 (0, off); a switch, 0 or 1 | same | derived | the real index is built from market data and dates no recession |

### Repricing at publication

**Timescale:** at the end of each close's macro step, and at each
`pin_macro`.

The macro step runs after the close, and its results are readable from then
on: the meeting's policy rate, the corporate yield it re-anchors, output and
the cycle. Fair value reaches the price only at the next session's first
tick, so an agent acting before that tick trades at the price from before the
decision. On pt-v20 with the leading dials (anticipation 126 sessions, rate
sensitivity 3, buyback share 0.75), the index fell 76 bp (se 5) in the first
65 minutes after a published hike and rose 171 bp (se 36) after a cut
(pt-v20 audit, finding 3). Event studies place the S&P 500's whole response to
an FOMC statement inside a 30-minute window (Gurkaynak, Sack and Swanson
2005; Bernanke and Kuttner 2005).

With `macro_publication_repricing` on, each public, solvent name that has
traded is re-marked as the step ends (`engine.rs:5638-5646`,
`engine.rs:5656-5760`). With $P$ its last print, $V_0$ its fair value before
the step and $V_1$ after it, both computed as the tick computes them on the
same day (`market/tick.rs:1764-1797`), the new price solves

```math
P' = \mathrm{clip}\Big(\frac{P}{V_0}\,V_1(P');\ 0.01,\ P_{\max}\Big)
```

$P_{\max}$ is the 50,000 price cap (`price_hard_cap`). $V_1$ reads the price
through the buyback term, so the engine iterates from
$P' = P V_1(P) / V_0$ until a step moves nothing, at most 16 times; with the
buyback share at 0 the first step is exact. The mispricing $s$ is left as it
was, so the next tick starts on the model price the new state implies. The
day's high, low and market cap follow the new price. A `pin_macro` re-marks
the same way, around its write (`python_engine.rs:3049`,
`python_engine.rs:3130-3131`).

The re-mark reads the true state the step leaves, as the next tick would.
The policy rate and the corporate yield are published as they are set, so
for them the two agree. For the phase it prices only what the next tick
would, and publishes nothing. It takes no draw and adds no state: the price
it writes is already in the snapshot and the state hash.

| Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|
| `macro_publication_repricing` | 1 (0, off); a switch, 0 or 1 | same | derived | pt-v20 audit, finding 3; FOMC event studies |

### The economy's outputs

1. **The corporate bond yield** $y^{c}$, as the discount rate in fair value (`fair_value.rs:143-148`). The engine always supplies it, so the fallback to the policy rate never runs.
2. **Nominal output** $N_d = Y_d Q_d$ and **the earnings cycle** $\chi_d$, which scale every company's earnings and book value (`market/tick.rs:375-402`).
3. **The VIX** $X_d$, which every variance process, the jump rate, the crisis gates and the market maker's spread read.

The business cycle reaches prices through these: through growth and
inflation into nominal output, through the earnings cycle, and through the
spread multiplier into the corporate yield. A change of phase starts the
earnings cycle toward its new target at the next close.

## Fair value

**Timescale:** recomputed from scratch every tick for every company
(`market/tick.rs:1094-1128`). Its inputs move at three speeds: the
corporate yield, nominal output and the earnings cycle change once a
session, after the close; the company's fair-value level $v_i$ and the last
print change every tick; a company's published earnings, book value,
revenue growth and sector are fixed unless a scenario writes them.

### The valuation

A company has trailing earnings per share $E_i$, book value per share $K_i$,
revenue growth $\gamma_i$ (a fraction a year) and a sector $k$ with anchor
P/E $\Pi_k$. Earnings are restated each tick for nominal growth, the
earnings cycle, the company's own fair-value level and buybacks:

```math
n_d = \Big(1 + \eta\Big(\frac{N_d}{N_0} - 1\Big)\Big)\,e^{\chi_d},
\qquad
B_{i,t} = \exp\!\Big(\kappa\,\frac{E_i\,n_d\,e^{v_{i,t}}}{P_{i,t-1}}\cdot\frac{d}{252}\Big),
\qquad
\hat E_{i,t} = E_i\,n_d\,e^{v_{i,t}}\,B_{i,t},\quad \hat K_{i,t} = K_i\,n_d\,e^{v_{i,t}}\,B_{i,t}
```

(`market/tick.rs:259-278`, `market/tick.rs:375-427`, `market/tick.rs:1108-1119`).
$N_0$ is nominal output at the end of the burn-in, so the nominal factor is
1 on the first session. $\chi_d$ is the earnings cycle
([above](#the-aggregate-earnings-cycle)) and $v_{i,t}$ the company's
fair-value level ([below](#the-fair-value-level)). $d$ is the number of
sessions already closed. $B = 1$ for a company with no earnings.

The rate term shrinks the anchor P/E when the corporate yield is above its
neutral level, more for fast-growing companies:

```math
D_i = 1 + 2\max(0,\ \gamma_i),
\qquad
R_{i,d} = \max\!\Big(0.5,\ 1 - \lambda\,D_i\Big(\frac{y^{c}_d}{100} - r^{\ast}\Big)\Big)
```

(`fair_value.rs:143-148`, `fair_value.rs:197-203`), and fair value is:

```math
V_{i,t} = \begin{cases}
\max\big(0.01,\ \hat E_{i,t}\,\Pi_{k}\,R_{i,d}\big) & E_i > 0 \\
\max\big(0.01,\ 1.2\,\hat K_{i,t}\big) & E_i \le 0
\end{cases}
```

(`fair_value.rs:213`, `fair_value.rs:299-329`). A loss-making company is
valued at 1.2 times book, with no rate term.

Two public calls return this number. `tradefloor.fair_value(...,
model=...)` returns $V$ for the fundamentals and macro it is given, with
$\hat E = E$ and $\hat K = K$, under every valuation value of that model:
$r^{\ast}$, $\lambda$, the QE gains and the book floor. `Engine.fair_values()`
returns $V$ for each name as the engine's next tick starts from it, with the
restatement above and the VIX discount below. Called with no `model` and
none of those values, `fair_value` uses the reference values ($r^{\ast}$ =
0.04, $\lambda$ = 1.5, a QE adjustment of $1 + \text{boost}$), which no preset
after pt-v15 ships. That form stays fixed across releases, and the
manifest's era fingerprint is computed on it.

What follows from this:

- **Rates.** $\partial \ln V / \partial r = -\lambda D_i / R$, with $\lambda$ = `rate_pe_sensitivity`. At the neutral rate, 100 basis points on the corporate yield moves a profitable company's fair value by 3% to 5.4% on pt-v20 ($\lambda$ = 3), depending on its growth, and by 1.5% to 2.7% on pt-v19 ($\lambda$ = 1.5). On the driven 2022 path the market P/E falls 4.26% per 100 bp against the S&P 500's 5.2% (row R6, box ptv20g6). Loss-makers do not move.
- **Earnings growth** comes only from nominal output and buybacks. Revenue growth sets duration and nothing else. pt-v21 pays dividends and no earlier preset does; see **Dividends** below.
- **Buybacks** add about $\kappa E/P$ a year, about 1.9% at a typical earnings yield. They retire no shares. The yield is read at the current price and applied over every elapsed session, so a company whose price falls toward the 0.01 floor reads a yield in the hundreds. Under `buyback_yield_cap` $\bar b$ the yield in $B$ is $\min(\kappa E_i n_d / P_{i,t-1},\ \bar b)$ (`market/tick.rs:270-277`). pt-v20's $\kappa$ of 0.75 would be 4.2% a year at the median earnings yield, but the index's delivered buyback yield (the cap-weighted log rate of $B$) is 2.0% a year on held-out seeds, and its cap of 0.15 binds only on a company priced under five times earnings.
- **Dividends** are on in pt-v21 at `dividend_payout_share` 1.2 and off on every earlier preset (0). When the dial is set, a profitable company whose revenue growth is under `dividend_growth_cutoff` pays a payout $\delta_i = \min(1, s\,\pi_{\text{sector}})$ of its earnings, a target yield $y_i = \delta_i E_i / P_{i,0}$. Ex-dates fall every 63 sessions from a phase hashed from the ticker. Each amount is declared 21 sessions ahead as $D \leftarrow \max(0, D + c_q(y_i \bar P_i/4 - D))$, capped at `dividend_yield_ceiling` $\cdot\, y_i P_i / 4$, where $\bar P_i$ is an EMA of the company's closes (half-life 21) and $c_q = 1-(1-c)^{1/4}$ for `dividend_adjustment_speed` $c$. Fair value adds $D\,k/63$ at $k$ sessions since the last ex-date, and at the ex-date open the price drops by $D$ exactly (`market/dividends.rs`, `Engine::apply_dividends`). Under `dividend_buyback_substitution` the buyback share in $B$ is $\max(0, \kappa - \delta_i)$, so $\kappa$ is the total payout.
- Nominal output grows 4.8% a year over a long run, against 4.8% in the US 1990 to 2025 (design note results/macro-cycle §0). On pt-v20 the index returns 6.4% a year over 21 years against a target of 6.25% (row B8), and its annual returns have a standard deviation of 16.3% against the S&P 500's 17.4% (row B9), on 90 histories (box ptv20g6).

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $r^{\ast}$ | `neutral_discount_rate` | 0.0482 | same | derived | the corporate yield the economy rests at after pt-v18's burn-in (`params.rs:2912-2954`); see [Known gaps](#known-gaps) |
| $\eta$ | `earnings_nominal_growth` | 1.0 | same | derived | holds the earnings share of nominal output constant |
| $\kappa$ | `buyback_payout_share` | 0.75 (0.3333) | 0.9 | fitted (chosen) | pt-v20's 0.75 is calibrated to the index's one-year drift, not to buybacks: about 4.2% a year at a typical earnings yield. pt-v19's 0.3333 is US large-cap net buybacks of 1.5% to 2.0% of market value, 2000 to 2025 (`params.rs`, `ModelParams::buyback_payout_share`); no error bar |
| $\bar b$ | `buyback_yield_cap` | 0.15 (0, off) | same | guard | keeps the buyback term finite for a company near the price floor |
| $\lambda$ | `rate_pe_sensitivity` | 3 (1.5) | same | fitted (chosen) | pt-v20's 3 was picked on a grid; pt-v19's 1.5 is the reference implementation's. The S&P 500's P/E fell 4.9% to 5.5% per 100 bp of Baa in 2022 |
| | rate-term floor | 0.5 | | guard | |
| | duration scale | 2.0 | | chosen | reference implementation |
| | loss-maker price to book | 1.2 | | chosen | reference implementation |
| | `market_pe_buybacks` | 1.0 | same | derived | the market P/E divides by the same buyback factor |

The sector anchors $\Pi_k$ are in [The sectors](#the-sectors). They are
chosen: carried from the reference implementation, with no market data named.

### The permanent share of market moves

**Timescale:** every tick, and the close's jumps. **State:** a fair-value
level $v_i$ per company, 0 on every preset through pt-v19.

pt-v20 moves part of each tick's shocks out of the mispricing and into a
permanent fair-value level, so that part does not revert. The level scales a
company's restated earnings and book value by $e^{v_i}$ before the buyback
term and the valuation above (`market/tick.rs:1778-1785`). After the tick's
update of $s$ (see [Mispricing](#mispricing)), with $\psi$ =
`fair_value_news_share`:

```math
\Delta v_{i,t} = \psi\,\big(u\,(\varepsilon^{I}_{i,t} + \varepsilon^{S}_{i,t}) + N^{own}_{i,t}\big)
 + \psi_m(\sigma_t)\,\big(u\,m_{i,t} + N^{mkt}_{i,t}\big),
\qquad
s_{i,t+1} \leftarrow s_{i,t+1} - \Delta v_{i,t},
\qquad
v_i \leftarrow v_i + \Delta v_{i,t} - \tfrac{1}{2}\Delta v_{i,t}^{2}
```

(`market/tick.rs:1246-1286`). $\varepsilon^{I}$ and $\varepsilon^{S}$ are the
tick's idiosyncratic and sector noise, $N^{own}$ the news naming the company,
its peers or its sector, $N^{mkt}$ the market-wide news, and $u$ the intraday
curve (0.15 while the market is closed). The price moves by the whole shock
either way; what changes is how much of it later reverts. The
$-\tfrac{1}{2}\Delta v^{2}$ term keeps $e^{v}$ a martingale. The close's jumps
are split the same way: a company's own jump on $\psi$, the market jump on
$\psi_m$ (`engine.rs:4717-4757`).

The market's share $\psi_m$ = `fair_value_market_share` is cut above a
ceiling on the market factor's current daily sigma $\sigma_t$, with $c$ =
`fair_value_market_vol_cap` and $\sigma_F$ = `market_factor_sigma`
(`market/tick.rs:806-821`):

```math
\psi_m(\sigma) = \begin{cases} \psi_m & c = 0 \ \text{or}\ \sigma \le c\,\sigma_F \\ \psi_m\,c\,\sigma_F / \sigma & \text{otherwise} \end{cases}
```

$m_{i,t}$ is the company's market input. With `fair_value_market_linear` at
0 it is the whole input: the loading on the market draw, the down-tick tilt,
the lagged down-day wire, the crisis injection, the crash amplifier and the
recentring. At 1 it is the plain loading $\beta_i F_t$ alone
(`market/factors.rs:1226`), which has zero mean in every regime. The other
terms are not zero-mean once the VIX is high, because the amplifier fires on
a threshold in baseline sigmas; left in $s$ they are a discount that reverts
as the VIX falls, and made permanent they were a drift that ran as long as
the VIX stayed high.

Why pt-v20 takes it. With every market shock in $s$, the index reverted on
the mispricing's half-life. The ratio of its five-year variance to five
times its one-year variance read 0.42 on the leading dials, against 0.87 for
the S&P 500 over 1871-2023 (pt-v20 audit, major 5; twelfth registration,
row V1, in `validation/pt-v20/programme/ptv20-registration.md`). With the
plain market draw permanent up to 1.5 times the base sigma, ordinary market
news is permanent and the excess a fear regime adds reverts, the form the
evidence takes: mean reversion in index returns concentrates in turbulent
periods (Poterba and Summers 1988; Kim, Nelson and Startz 1991; Spierdijk,
Bikker and van den Hoek 2012). On held-out seeds the two-year and five-year
ratios over one year read 0.80 and 0.66, against bands of 0.75 to 1.15 and
0.55 to 1.20 (box ptv20vr9). The earnings cycle then carries less of the
index's yearly spread, and pt-v20's depth goes from 0.35 to 0.2, where the
aggregate fall in a contraction reads -0.173 against Shiller's median -0.17.
`opening_market_sigma`, the spread of the market's opening mispricing, goes
from 0.10 to 0.001, since little of the market's variance stays in $s$.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\psi_m$ | `fair_value_market_share` | 1 (0, off) | same | fitted | the end point; row V1 on grids ptv20vr1 to vr9 |
| | `fair_value_market_linear` | 1 (0, off); a switch | same | derived | the plain loading is the zero-mean part |
| $c$ | `fair_value_market_vol_cap` | 1.5 (0, no ceiling) | same | fitted | a ceiling of 2 took the index volatility to 27.9% against 18.1% (box ptv20vr4) |
| $e$ | `fair_value_market_excess_share` | 0 (the ceiling as it stood) | 0.5 | fitted on a candidate | r16 spike boxes r16cal1 to r16cal3b, held-out seeds |

A floor under the share above the ceiling (`fair_value_market_excess_share`
$e$, on in pt-v21 at 0.5 and off on every earlier preset; `market/tick.rs`,
`market_permanent_share`) puts back
$e$ of what the ceiling takes:

```math
\psi_m(\sigma) = \psi_m\,\frac{c\,\sigma_F}{\sigma} + e\,\Big(\psi_m - \psi_m\,\frac{c\,\sigma_F}{\sigma}\Big) \qquad \sigma > c\,\sigma_F
```

so even the most turbulent market move keeps at least $e\,\psi_m$ of itself
in $v$. It applies wherever the ceiling does: the session's ticks, the night
and the market jump. $e = 1$ is no ceiling, as $c = 0$ gives.

Why. With the ceiling alone a fear regime's market moves are almost wholly
transient, so a crash sits in $s$ and comes back on the 60-session half-life
on a schedule the published VIX announces. On the r15 screen's leading arm
(R15F, 90 held-out histories) the index gained 1.96, 4.81 and 7.61 per cent
over its drift 21, 63 and 126 sessions after a one-day VIX rise in the
history's top 1 per cent, against -1.44, -0.50 and +2.75 (se 1.14, 1.47,
1.89) on the S&P 500 and VIX 1990-2025. A desk decomposition (six held-out
histories) put about 40 per cent of it in $s$'s reversion, 30 per cent in the
volatility feedback's give-back and 20 per cent in rates and earnings.

Measured on a candidate (pt-v21 sets $e$ at 0.5; no earlier preset sets it).
On R15F with $e = 0.5$ and
`fair_value_vix_release_half_life` 504 (box r16g1, 90 held-out histories),
the rise after the same events reads +0.41, +1.98 and +4.73 per cent (se
0.26, 0.39, 0.44), the C10 rules that lever up after a VIX spike are ahead in
0.61 to 0.64 of the histories (0.69 to 0.72 on R15F), and none of the 384
mirrored rules breaches C10c. At 90 histories the 126-session rise splits as
+3.25 in $s$, -0.04 from the discount and +2.19 from rates and earnings,
against +3.72, +1.98 and +2.08 on R15F. V1 reads 0.90 and 0.83 (R15F 0.86
and 0.82; S&P 500 0.93 and 0.87), and all 40 registered rows pass.

### Volatility feedback

**Timescale:** wherever fair value is read, and once a session for the
smoothed exposure. **State:** the exposure $x$, while both the gain and the
half-life are set.

Higher expected volatility raises the return investors require and lowers
the price (French, Schwert and Stambaugh 1987; Campbell and Hentschel 1992).
With `fair_value_vix_discount` $g$ and `fair_value_vix_knee` $K$, every
company's fair value is scaled by

```math
V_{i,t} \leftarrow V_{i,t}\,e^{-g\,\beta_i\,x},
\qquad
x = \begin{cases} \max\big(0,\ \ln(X/K)\big) & H_x = 0 \\ x_d & H_x > 0 \end{cases},
\qquad
x_{d} = x_{d-1} + \big(1 - 0.5^{1/H_x}\big)\big(\max(0, \ln(X_d/K)) - x_{d-1}\big)
```

(`market/tick.rs:1690-1723`, `engine.rs:5126-5136`). $X$ is the VIX and
$H_x$ = `fair_value_vix_half_life` in sessions; the smoothed exposure steps
once at each close, after the VIX has moved, and takes no draw. The discount
is applied in the tick (`market/tick.rs:1128`), the overnight opening print
(`engine.rs:4198`), the re-mark at publication and the stationary opening
(`market/tick.rs:1755`, `market/tick.rs:1796`). It has no permanent part: it
deepens a fall while fear is high and is given back as the VIX comes down.
While $g > 0$ and $H_x > 0$ the snapshot's economy block carries
`vix_feedback`, and the state hash takes it (`engine.rs:6594-6597`).

Why pt-v20 takes it. With the market's plain shocks permanent, the driven
2020 path fell 0.192 in 41 sessions against the S&P 500's 0.339 in 23 (long-run
row F1). Read unsmoothed, the discount's whole daily change landed with the
VIX's move and took the sessions under -5% from 10.6 to 18 to 25 a decade.
With a knee of 40 and a 5-session half-life the fall reads 0.266 in 35.5
sessions and the sessions under -5% 11.5 a decade, against the tape's 6.2 and
a band up to twice it (box ptv20vr9). At knees of 30 and 35 every smoothed
arm tried ran past twice the tape.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $g$ | `fair_value_vix_discount` | 0.35 (0, off) | same | fitted | held-out grids ptv20vr6 to vr9 |
| $K$ | `fair_value_vix_knee` | 40 (30, unread at $g = 0$) | same | fitted | held-out grids ptv20vr8 and vr9 |
| $H_x$ | `fair_value_vix_half_life` | 5 (0, the VIX as it stands) | same | fitted | held-out grids ptv20vr6 to vr9 |
| $H_r$ | `fair_value_vix_release_half_life` | 0 (the build's $H_x$ both ways) | 504 | fitted on a candidate | r16 spike boxes r16cal1 to r16cal3b, held-out seeds |

The give-back (`fair_value_vix_release_half_life` $H_r$, on in pt-v21 at 504
and off on every earlier preset;
`engine.rs`, the close's pull): while the target is below the exposure, the
close pulls at $H_r$ instead of $H_x$,

```math
x_{d} = x_{d-1} + \big(1 - 0.5^{1/H}\big)\big(\max(0, \ln(X_d/K)) - x_{d-1}\big),
\qquad H = \begin{cases} H_r & H_r > 0 \ \text{and}\ \max(0, \ln(X_d/K)) < x_{d-1} \\ H_x & \text{otherwise} \end{cases}
```

so the discount is built as before and outlasts the VIX's own fall: a fear
premium that stays after the VIX has gone, as required returns stay high
after a crisis while risk appetite recovers. No new state: the exposure the
snapshot and the state hash already carry. At one half-life of 5 sessions
the give-back ran on the VIX's own schedule, and a rule that levered up
while the smoothed exposure was above its target (the audit's xfb rule) beat
the exposure-matched position by +0.96 points a year, ahead in 0.64 of
histories.

### The sectors

Twelve sectors, each with an anchor P/E, a relative volatility $v_k$ and a
daily sigma $\sigma_k$ (`sectors.rs:93-106`). All are chosen, from the
reference implementation.

| Sector | P/E | $v_k$ | $\sigma_k$ a day | Epicentre weight |
|---|---|---|---|---|
| technology | 32 | 1.2 | 0.025 | 0 |
| financial_services | 12 | 1.1 | 0.015 | 0.6 |
| healthcare | 24 | 0.9 | 0.018 | 0 |
| energy | 10 | 1.3 | 0.015 | 0 |
| consumer_discretionary | 20 | 1.0 | 0.018 | 0 |
| consumer_staples | 20 | 0.7 | 0.008 | 0 |
| industrials | 17 | 1.0 | 0.015 | 0 |
| materials | 14 | 1.2 | 0.015 | 0 |
| real_estate | 35 | 0.9 | 0.008 | 0 |
| utilities | 16 | 0.6 | 0.008 | 0 |
| telecommunications | 14 | 0.8 | 0.010 | 0 |
| transportation | 15 | 1.1 | 0.015 | 0 |

- $v_k$ centres the betas the universe generator draws, and scales the market maker's spread.
- $\sigma_k^2$ is each company's starting GJR variance and the reference for its floor and ceiling.
- The epicentre weight is the probability that a crisis starts in that sector; the remaining 0.4 is "no epicentre". It is derived from five crises on the reference roster (2000-02, 2008-09, 2011, 2020, 2022), counting a sector hit at least 1.3 times as hard as the median sector (`sectors.rs:52-78`).

The sector table has no per-sector beta or factor loading. Loadings are
global and scale with each company's own beta.

### The universe and the opening

`Universe.random(n, seed=seed)` draws a roster from its own random stream,
independent of the market's (`universe.rs:36`, `universe.rs:204-282`).
Company $i$ is in sector $k = i \bmod 12$, and:

```math
M_i \sim \mathrm{LogU}(2 \times 10^{8},\ 2 \times 10^{12}),\quad
P_{i,0} \sim \mathrm{LogU}(3,\ 600),\quad
S_i = M_i / P_{i,0}
```

```math
E_i = \begin{cases}
-P_{i,0}\,\mathcal{U}(0.01,\ 0.09) & \text{with probability } 0.11 \\
P_{i,0} / (\Pi_k\,u^{PE}_i),\quad u^{PE}_i \sim \mathrm{LogU}(1/1.7,\ 1.7) & \text{otherwise}
\end{cases}
\qquad
K_i = \frac{P_{i,0}}{1.2}\,u^{K}_i,\quad u^{K}_i \sim \mathrm{LogU}(1/2.4,\ 2.4)
```

```math
\gamma_i = \frac{\Pi_k - 10}{100} + \mathcal{U}(-0.06,\ 0.14),\quad
\beta_i = \max\big(0.15,\ v_k\,\mathcal{U}(0.6,\ 1.35)\big),\quad
\bar A_i = \max\big(1000,\ S_i\,\mathcal{U}(0.001,\ 0.02)\big)
```

$\mathcal{U}(a, b)$ is uniform and $\mathrm{LogU}(a, b)$ log-uniform. $M$ is market cap, $S$ shares
outstanding, $\bar A$ average daily volume in shares. Short interest is
$S_i \mathrm{LogU}(0.004, 0.30)$, and all these ranges are chosen.

**The roster's beta, normalised** (`market_beta_normalise` $d$). On in pt-v21
at 1. Off, on every earlier preset, the engine takes no sum and every name
keeps the beta
above. Off zero, `Engine::with_params_from_opening` divides each public name's
beta by the roster's cap-weighted beta at the opening caps before anything
reads it (`normalise_roster_beta` in `engine.rs`):

```math
\beta_i \leftarrow \beta_i \,/\, B^{d},\qquad
B = \frac{\sum_j M_{j,0}\,\beta_j}{\sum_j M_{j,0}}
```

At $d = 1$ the roster's cap-weighted beta is one, so the market factor is the
systematic part of the roster's own index, as the betas of a real index's
constituents average one against it. The generated rosters' $B$ scatters with
their sector mix: the certified roster 111 reads 1.06 (technology 33 per cent
of its cap), a random 40-name roster's median 0.97 (technology 7 per cent).

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $d$ | `market_beta_normalise` | 0 (off) | 1 | | in [0, 1] |

What it was measured to do (sim/r17-tails, on R17A, 252 sessions from the
opening; the certification's varying rosters over 360 held-out seeds per set,
2001-2360 and 22001-22360, and roster 111 over 60). Alone it moves the two
rosters' index volatility opposite ways, 15.4 to 15.7 per cent on the random
rosters and 17.1 to 16.2 on roster 111. With `market_factor_sigma` raised 10
per cent the random rosters read 17.0 and roster 111 17.3, and the
certification's tail row `index_tail_dn3_pct` rises from 0.62 and 0.67 to 0.90
and 0.96 (band [0.64, 2.34]); roster 111's 21-year histories (90 held-out
seeds) read an index volatility of 19.5 against 18.8 (tape 18.1) and 8.8
sessions under -5 per cent a decade against 7.5 (tape 6.2). The same tail from
the return memory instead (`market_vol_leverage` 3 and `vix_level_sigma`
0.0135, no normalisation) put roster 111's histories at 21.1 and 12.2.

**The stationary opening.** A roster opens with a day-zero premium
$g_i = \ln(\max(0.01, P_{i,0})/V_{i,0})$ that has a cross-sectional
standard deviation of about 0.33 (`market/tick.rs:1730-1756`), for a
profitable company $\ln u_i^{PE} - \ln R_{i,0}$ and for a loss-maker
$-\ln u_i^{K}$. The model's own stationary spread of $s$ is about 0.016, so
putting the whole premium into $s$ would open every run with a months-long
drift back to fair value. Instead, at the first open tick, the premium is
split between $s$ and the fair-value level $v$ (`engine.rs:4533-4572`), using
$n + 1$ normals drawn once from the opening stream when the engine is built
(`engine.rs:1121-1130`):

```math
s_{i,0} = \mathrm{clip}\big(\sigma_c\,z_{n+1} + \sigma_o\,(z_i - \bar z);\ -0.9,\ 0.9\big),
\qquad
v_{i,0} = g_i - s_{i,0},
\qquad
\bar z = \frac{\sum_i w_i z_i}{\sum_i w_i}
```

with $w_i$ the opening market caps. Each company's mispricing opens at a
draw from its stationary spread, the market's common level at a draw from
its own, and the rest of the premium becomes fair value, so no price moves
at the open: $V_{i,0} e^{v_{i,0}} e^{s_{i,0}} = P_{i,0}$. A company listed
later opens with $s_0 = \mathrm{clip}(g_i;\ -0.9,\ 0.9)$
(`market/tick.rs:1130-1138`).

| Symbol | Dial | pt-v20 (pt-v19) | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\sigma_o$ | `opening_mispricing_sigma` | 0.016 (0, off) | same | measured | the cross-sectional sd of $s$ over sessions 250 to 2,660 on seeds 201 to 212; 0.0155 to 0.0165 |
| $\sigma_c$ | `opening_market_sigma` | 0.001 (0, off) | same | chosen | with the market's plain shocks permanent, little of the market's variance stays in $s$, and 0.001 is the smallest opening that keeps the stationary form (0 would take the roster's day-zero premium). Not measured on the graded arm; pt-v20 before it had 0.10, the time-series sd of the cap-weighted $s$ on seeds 201 to 203 (0.148, 0.105, 0.042) |

Graded: the start-up ratio in row B9, the index's volatility over the first
60 sessions against sessions 250 to 490, reads 0.80 against a band of two
thirds to 1.5 (box ptv20g6).

Companies can also be built from SEC EDGAR filings (`python/tradefloor/edgar.py`):
diluted EPS, shares outstanding, equity over shares as book value, and
year-on-year revenue growth. By default each opens at its fair value.

## The price path

### The model price

**Timescale:** every tick. The model price is fair value, including the
company's fair-value level, times the exponential of the mispricing
(`market/tick.rs:1291`):

```math
P^{\ast}_{i,t} = \max\big(0.01,\ V_{i,t}\,e^{s_{i,t+1}}\big)
```

If $P^{\ast}$ leaves the session band $[0.75 P_{i,d}^{o},\ 1.25 P_{i,d}^{o}]$
it is clamped to the band and $s$ is re-derived from the clamped price
(`market/tick.rs:1297-1306`). The printed price is $P^{\ast}$ traded through
the book; see [The market maker and the book](#the-market-maker-and-the-book).

### Mispricing

**Timescale:** every tick, with a momentum term that rolls once a day.
**State:** $s_{i,t}$, and $\mu_{i,d}$, the change in $s$ over the previous
session.

```math
s_{i,t+1} = \mathrm{clip}\Big(\phi_\tau\,s_{i,t}
 + \frac{\theta\,\mu_{i,d}}{390}
 + \frac{L(s_{i,t}, \mu_{i,d})}{390}
 + \frac{N_{i,t} + O_{i,t} + Q_{i,d}}{390}
 + u(\tau_t)\,\varepsilon_{i,t};\ -\bar s,\ \bar s\Big)
```

(`market/tick.rs:1223-1287`), with these terms in order:

- **Mean reversion.** $\phi_\tau = 0.5^{1/(390 H)}$, so $s$ decays by half in $H$ sessions if nothing else acts.
- **Herding.** $\theta \mu$: a share of yesterday's re-rating continues today.
- **The crowd.** $L(s, \mu) = \mathrm{clip}(-g_v s + g_m \mu;\ -\bar L,\ \bar L)$ (`mispricing.rs:174-179`). The crowd buys what trades below fair value and chases yesterday's move a little. It reads $s$ before the tick's update.
- **News** $N$, **order flow** $O$ and the **short squeeze and stop cascade** $Q$, each a daily-equivalent log shock applied at 1/390 a tick. They are defined below. $O$ also carries the permanent impact of agents' fills; see [Agent orders](#agent-orders).
- **Noise.** $\varepsilon_{i,t}$ is the tick's shock from the factor structure, below. $u(\tau) = 1 + 0.2 (2\tau - 1)^4$ is the intraday volatility curve: 1.2 at the open and the close, 1.0 at midday (`market/hours.rs:104-106`).

This is the update before the fair-value level takes its share; the next
subsection subtracts it.

The momentum term rolls at the close, before the jumps
(`market/daily.rs:240-244`): $\mu_{i,d+1} = s_i^{\mathrm{close}} - s_i^{\mathrm{ref}}$,
then $s_i^{\mathrm{ref}} \leftarrow s_i^{\mathrm{close}}$. Jumps are excluded
from the next day's momentum (`jump_momentum_share` = 0,
`engine.rs:4674-4679`).

**As a daily AR(2).** Summed over a session, with the crowd term in its
linear range and no clamp binding, the close-to-close mispricing follows

```math
s_{d+1} \approx \psi^{390}\,s_{d} + (\theta + g_m)\,\bar\psi\,(s_{d} - s_{d-1}) + \text{shocks}_d,
\qquad \psi = \phi_\tau - g_v/390
```

with $\bar\psi$ the session mean of $\psi^{k}$. On pt-v20, as on pt-v19, the two
coefficients are 0.9826 and 0.0382, the roots are 0.9819 and 0.0389, and the
process is stationary. The effective half-life is about 40 sessions, not the
nominal 60, because the crowd's valuation lean adds its own pull. This daily
form is our derivation from the tick equation, not a formula in the code.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $H$ | `mispricing_half_life_days` | 60 | same | chosen | |
| $\phi_\tau$ | `s_phi_tick` | 0.99997 | same | derived | $0.5^{1/(390 \cdot 60)}$ |
| $\theta$ | `momentum_theta` | 0.01855 | same | fitted | cut in steps from 0.25; the last cut kept the two-year `return_acf1` under its ceiling |
| $g_v$ | `crowd_valuation_gain` | 0.006 a day | same | chosen | reference implementation |
| $g_m$ | `crowd_momentum_gain` | 0.02 a day | same | chosen | reference implementation |
| $\bar L$ | `crowd_lean_cap` | 0.02 | same | guard | |
| $\bar s$ | `mispricing_cap` | 0.9 | same | guard | $e^{\pm 0.9}$ is 0.41 to 2.46 times fair value |

### The fair-value level

**Timescale:** every tick, and at the close for jumps. **State:** $v_{i,t}$,
a log level on each company's fair value, persistent across sessions
(`market/tick.rs:161-167`).

A company's own shocks are permanent. On each tick the share $\psi$ of its
sector and own noise, and of the news that names it, its peers or its
sector, leaves $s$ for $v$ (`market/tick.rs:1239-1286`):

```math
\Delta_{i,t}^{v} = \psi\Big[u(\tau_t)\big(\xi_i\,\ell_i\,G_{k(i),t} + \xi_i\,I_{i,t}\big) + \frac{N_{i,t} - N_{i,t}^{M}}{390}\Big]
```

```math
s_{i,t+1} \leftarrow \mathrm{clip}\big(s_{i,t+1} - \Delta_{i,t}^{v};\ -\bar s,\ \bar s\big),
\qquad
v_{i,t+1} = v_{i,t} + \Delta_{i,t}^{v} - \tfrac12\big(\Delta_{i,t}^{v}\big)^{2}
```

$N^{M}$ is market-wide news, which stays in $s$. The Ito term keeps
$e^{v}$ a martingale. The price takes the whole shock on the tick either
way; what changes is that the company's part no longer reverts on the
mispricing's half-life. At the close the company's own jump goes to $v$ the
same way (`engine.rs:4709-4757`). The market's share of both, which
pt-v20 also sets, is in [The permanent share of market moves](#the-permanent-share-of-market-moves):

```math
\Delta_{i}^{v,J} = \psi\,J_{i}^{I},
\qquad
s_i \leftarrow s_i - \Delta_{i}^{v,J},
\qquad
v_i \leftarrow v_i + \Delta_{i}^{v,J} - \tfrac12\big(\Delta_{i}^{v,J}\big)^{2}
```

and the jump is kept out of the next day's momentum. With the market's
share at 0, as on every preset through pt-v19, what stays in $s$ is the
market leg of the factor structure, market-wide news, the market jump and
its compensator, order flow and agents' impact, the squeeze and cascade
term, reversion, herding, the crowd and the breaker. On pt-v20 the plain
market loading, market-wide news and the market jump move to $v$ too, up
to the volatility ceiling.

| Symbol | Dial | pt-v20 (pt-v19) | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\psi$ | `fair_value_news_share` | 1.0 (0, off) | same | derived | the end point: a company's variance ratio at 60 sessions (row C5) moves from 0.59 to 0.95 against a real 0.92, and the value and momentum signals that a transient $s$ made profitable (rows C6, C7) fall to real sizes |
| $\psi_m$ | `fair_value_market_share` | 1 (0, off) | same | fitted | see [The permanent share of market moves](#the-permanent-share-of-market-moves) |

#### The knee under a name's level

**Timescale:** once a session, at the close, after the buybacks. **State:**
none beyond $v$.

$v$ has no anchor. Every permanent part of a name's moves adds to it with its
$-\tfrac12\Delta^2$, so over a century the names' levels spread without
bound, and a high-beta name loses about $\tfrac12(\beta^2 - 1)\sigma_m^2$ a
year against the market through every turbulent year. On R16A (the
thirteenth grade's arm; seeds 201-208, 100 years) the cross-sectional sd of
$v_i - \bar v$ is 1.0 at 21 years, 1.5 at 50 and 2.0 at 100, and the deepest
name reaches -11. A name that far down prints at the 0.01 price floor and
stays there: the thirteenth grade's H1-100y failed on one name at the floor
for 277 sessions, and on held-out seeds 20201-20212 R16A has 3,171 name-days
at the floor (seed 20201, one name, decades 80 and 90). A real company that
falls that far is restructured, recapitalised or taken over, or leaves the
index; this roster is fixed.

With `fair_value_relative_knee` $k$ and `fair_value_relative_half_life` $h$
(`engine.rs`, `pull_relative_levels`), at each close, over the public,
solvent, traded names,

```math
\bar v = \frac1n\sum_j v_j,
\qquad
v_i \leftarrow v_i + \big(1 - 2^{-1/h}\big)\,\big(\bar v - k - v_i\big)
\quad\text{when } v_i < \bar v - k
```

and no other name moves. It draws nothing, and the price follows $v$ from
the next tick ($s$ is not touched). $k$ is on in pt-v21 at 4 and off
($k = 0$) on every earlier preset.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $k$ | `fair_value_relative_knee` | 0 (off) | 4 | fitted on a candidate | r17 floor box r17floor1c, held-out seeds |
| $h$ | `fair_value_relative_half_life` | 0 (read only with $k$) | 63 | fitted on a candidate | the same |

Measured on a candidate (pt-v21 sets $k$ at 4 and $h$ at 63; no earlier
preset sets them). On R16A with $k = 4$ and
$h = 63$ (box r17floor1c; 100-year runs on the certified roster), no name
reaches the floor on seeds 201-212, 20201-20212 or 30201-30236: the lowest
close of any name is $e^{2.59}$, $e^{3.20}$ and $e^{1.88}$ times the floor
(R16A: $e^{1.63}$, the floor itself, and $e^{0.42}$), and each seed's lowest
name sits on average $e^{4.24}$, $e^{4.53}$ and $e^{4.52}$ above it (se 0.27,
0.20, 0.16). The deepest name left is a $3.91 name early in a run during a
market trough, 2.6 to 3.1 below the mean, which the knee does not reach.
H1-100y's other clauses stay where R16A has them on all three sets: each
decade's volatility over the first decade's within 0.001, the share of moves
over 20 per cent within 0.0005 points (and never above R16A's), and the tick
autocorrelation's decade mean within 0.003. In a 21-year history the knee is
reached in 5, 7 and 6 of 90 (R16A's relative levels on seeds 201-230,
501-530, 801-830 and the same plus 20000 and 30000); every other history is
R16A's to the bit. On the thirteenth registration's grade job run on both
held-out layouts (boxes r17floorgA2 and r17floorgB), 131 and 130 of the 148
gated rows read exactly R16A's and the rest move by less than 0.001 of their
value, with no row's pass or band usage changing except H1-100y's, which goes
from failing (decades 80 and 90, seeds 20201-20212) to passing.

### The factor structure

**Timescale:** every tick. **Draws:** one market normal $z_t$, one normal
$\zeta_{k,t}$ per sector, one normal $\eta_{i,t}$ per company, all on the
market stream, in that order.

The market factor and the sector factors (`market/tick.rs:876-922`,
`market/tick.rs:296-304`):

```math
f_t = \sqrt{v_d}\,\frac{z_t}{\sqrt{390}},
\qquad
G_{k,t} = \bar\sigma_S\,\rho_d\,\sqrt{h^{S}_{k,d}}\,\frac{\zeta_{k,t}}{\sqrt{390}}
```

$v_d$ is the market-factor variance and $h_{k,d}^{S}$ the sector variance
state, both set at the previous close; see [Volatility](#volatility). The
sector sigma scales with the VIX ratio $\rho_d$ because
`sector_vix_coupling` = 1.

A company's tick shock (`market/factors.rs:850-1115`):

```math
\varepsilon_{i,t} = \Gamma_t\,M_{i,t} + \upsilon_{i,d} + \xi_i\,\ell_i\,G_{k(i),t} + \xi_i\,I_{i,t}
```

```math
M_{i,t} = \beta_i\,f_t\,\big(1 + a\,\mathbf{1}[f_t < 0]\big)\big(1 + a_L\,\mathbf{1}[c_t < 0]\big),
\qquad
c_t = F_{d-1}\,(1 - \tau_t) + \sum_{t' < t} f_{t'}
```

```math
\Gamma_t = 1 + m_A \max\big(0,\ \lvert z_t \rvert - T_A\big),
\qquad
\upsilon_{i,d} = a\,\beta_i\,\frac{\sqrt{v_d/390}}{\sqrt{2\pi}}
```

```math
\ell_i = \ell_0\,\big(1 + b_\ell\,(\beta_i - 1)\big),
\qquad
I_{i,t} = \eta_{i,t}\,\sqrt{\max(h_{i,d},\ h_{\min})}\,\frac{\kappa_I\,c_i}{\sqrt{390}}
```

What each term does:

- $M$ is the market leg. A down tick of the market loads a little more ($a$), and so does any tick while the market is down on a trailing window ($a_L$, the lagged down-beta). $c_t$ blends yesterday's market move, fading through the session, with today's so far. $F_{d-1}$ is yesterday's summed market factor.
- $\Gamma$ is the crash amplifier: a market draw beyond $T_A$ standard deviations loads extra. It fires on 4.6% of ticks.
- $\upsilon$ gives back the mean the down-tick tilt adds, so the tilt changes shape and not drift.
- $\ell_i G$ is the sector leg. The loading rises with beta.
- $I$ is the company's own noise. Its scale is the company's GJR variance $h_{i,d}$, floored, times a size step $c_i$: 0.8 above USD 50bn of market cap, 1.0 above 10bn, 1.3 above 1bn, 1.6 below (`market/factors.rs:722-733`).
- $\xi_i$ is 1 except during a crisis episode with an epicentre, when it is 2.04 for companies in the epicentre sector and 0.80 for the rest; see [Crisis regimes](#crisis-regimes).

Betas are fixed per company when the universe is built. Beta and sector are
the only loadings. There are no style factors such as value, momentum, size
or crowding, and a caller cannot supply a covariance matrix, so a factor
crowding study cannot be set up on this structure.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\bar\sigma_M$ | `market_factor_sigma` | 0.006454 (0.007593) a day | 0.007099 | fitted | 0.85 of pt-v19's, picked on a grid over 90 pooled histories (box ptv20e4) to hold the bear-market count and the spread of annual returns in band once the earnings cycle carries part of the variance; the baseline of $v_d$ |
| $\bar\sigma_S$ | `sector_factor_sigma` | 0.008583 a day | same | fitted | a derivation exists only for another value, 0.0107 |
| | `sector_vix_coupling` | 1.0 | same | chosen | makes the sector sigma scale with the VIX like the market's |
| $\ell_0$ | `sector_loading` | 0.6 | same | measured | centres `sector_excess_corr` on the reference roster, 1987 to 2025 |
| $b_\ell$ | `sector_loading_beta_slope` | 0.7 | same | fitted | |
| $\kappa_I$ | `idio_sigma_scale` | 0.5126 | 0.52 | fitted | search |
| $h_{\min}$ | `idio_sigma_floor` | 0.0001 | same | guard | binds on many company-days |
| $a$ | `market_beta_down_asym` | 0.025 | same | chosen | no daily measurement on record |
| $a_L$ | `market_beta_down_asym_lag` | 0.46 | same | fitted | fitted to the lagged correlation-asymmetry statistic; a 7-point grid's best was 0.375 |
| | `market_beta_down_asym_recentre` | 1.0 | same | derived | $E[f \mathbf{1}(f < 0)] = -\sigma/\sqrt{2\pi}$ |
| $T_A$ | `crash_amplifier_threshold` | 2.0 | same | chosen | reference implementation |
| $m_A$ | `crash_amplifier_slope` | 0.2 | same | chosen | reference implementation |
| | `crash_amplifier_conditional_sigma` | 1.0 | same | derived | measures the shock in today's sigma, which keeps the VIX loop stable |

### Squeeze and cascade terms

**Timescale:** a daily-equivalent drift, set by the previous session's
return $r$ and short interest over float $\mathrm{SIR}$
(`market/factors.rs:1140-1207`):

```math
Q_{i,d} = \min(0.02,\ 0.5\,\mathrm{SIR}\,r) \cdot \mathbf{1}[\mathrm{SIR} > 0.2,\ r > 0.03]
 - C(\lvert r \rvert) \cdot \mathbf{1}[r < -0.025]
 + C(r) \cdot \mathbf{1}[r > 0.025,\ \mathrm{SIR} > 0.1]
```

The cascade $C(x)$ is 0.007 above a 7% move, 0.0045 above 5%, 0.0025 above
3%, else 0.0005. The whole term is scaled by `cascade_gain`, 0.1 on pt-v20
and 1 on pt-v19 (`market/factors.rs:1209-1217`): with the tape tracking the
model price, the ladders were the largest daily momentum left, and 0.1
brings the one-day Lo-MacKinlay contrarian profit to the reference roster's
-1.74 basis points a day (measured; estimate 0.12, s.e. 0.11; row C8 reads
-0.66 against a band of -6.4 to +2.9). `cascade_symmetry` = 1 makes the up
and down ladders mirror images, and it and the ladder constants are chosen.

### News

**Timescale:** events are drawn at the open and absorbed tick by tick.

At each open every company draws a uniform and a normal on the news stream.
With probability $\lambda_N$ it has a news event of log size
$\nu_e = \sigma_N Z$ (`engine.rs:4010-4027`). An event reaches company $i$
with weight

```math
w_{e,i} = \begin{cases}
1 & \text{the event is about } i \\
w_p\,(1 + c_p\,\chi_d) & \text{the event is about another company in the same sector}
\end{cases}
```

(`market/factors.rs:764-821`), where $\chi_d$ is the crisis spike (below).
An event is priced mostly within minutes. The share absorbed by minute $m$
of the session is

```math
\mathcal{A}(m) = (1 - \delta)\,F(m;\ h_f) + \delta\,F(m;\ h_D),
\qquad
F(m;\ h) = \frac{1 - 2^{-m/h}}{1 - 2^{-390/h}}
```

and the news term in the mispricing equation is

```math
N_{i,t} = 390 \sum_{e} w_{e,i}\,\nu_e\,\big(\mathcal{A}(t + 1) - \mathcal{A}(t)\big)
```

(`engine.rs:2451-2461`, `market/factors.rs:536-589`). Over the session an
event moves $s$ by $w \nu_e$: 61% of that in the first minute, 89% by the
fifth and 99% by the 150th. The market
maker re-quotes by the tick's news term before any trade, so the traded price
carries the same profile (`news_quote_revision` = 1, `market/tick.rs:1487-1505`).

News a caller passes to `Engine.tick` or `run_session` is added to these
events, at weight 1 on the company it names, `news_sector_weight` (0.5) on
each name in the sector it names and `news_market_weight` (0.3) on every
name when it names neither, and the preset was fitted without it. The flow each preset is fitted at, and the check on a caller's
own, are in [REALISM.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/REALISM.md#the-shock-flow-behind-every-figure).

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\lambda_N$ | `endogenous_news_intensity` | 0.05 a day | same | fitted | search |
| $\sigma_N$ | `endogenous_news_sigma` | 0.01751 | same | fitted | search |
| $w_p$ | `news_peer_weight`, `news_peer_weight_down` | 0.05, 0.05 | same | chosen | motivated by Foster (1981) and Freeman and Tse (1992) on intra-industry information transfer |
| $c_p$ | `news_peer_vix_coupling` | 8.0 | same | fitted | |
| $h_f$ | `news_absorption_half_life` | 0.6 ticks | same | derived | one-minute share of the earnings-announcement move, Christensen, Timmermann and Veliyev (arXiv 2601.08962, Table 7, 2008 to 2020) |
| $\delta$ | `news_absorption_drift_share` | 0.12 | same | derived | same table: 1 - 1.58/1.80 |
| $h_D$ | `news_absorption_drift_half_life` | 42 ticks | same | chosen | a 60-minute mean life for the drift, to fit Patell and Wolfson (1984)'s "several hours"; not measured |
| | `news_quote_revision` | 1.0 | same | derived | with it the tape holds 0.615 of an event after one tick against the profile's 0.605; without it 0.086 |

### Jumps

**Timescale:** once a day, at the close, on the jumps stream
(`engine.rs:4614-4758`). A market jump hits every company with unit loading;
an idiosyncratic jump hits one company. Both rates rise with the VIX, and the
expected market jump is subtracted so jumps add no drift:

```math
\lambda_{\cdot,d} = \lambda_{\cdot}\,\big((1 - c_J) + c_J\,\rho_d^{2}\big)
```

```math
s_i \leftarrow \mathrm{clip}\Big(s_i
 + \mathbf{1}[U^{M}_d < \lambda_{M,d}]\,(\mu_M + \sigma^{J}_M Z^{M}_d)
 + \mathbf{1}[U_{i,d} < \lambda_{I,d}]\,\sigma^{J}_I Z_{i,d}
 - \lambda_{M,d}\,\mu_M;\ -\bar s,\ \bar s\Big)
```

A jump changes $s$ at the close and so reaches the price at the next
session's first ticks, through the book. At $X = A$ there are about 7
market-jump days a year, of mean -0.85%, and each company has about 1.7
idiosyncratic jumps a year, of standard deviation 7.5%.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\lambda_M$ | `jump_intensity_market` | 0.02829 (0.05658) a day | 0.005 | fitted | half pt-v19's rate, picked on the same grid as the market factor's sigma; pt-v19's was a search for the two-year excess kurtosis |
| $\mu_M$ | `jump_mean_market` | -0.008522 | -0.03 | fitted | search; negative for skew |
| $\sigma_M^{J}$ | `jump_sigma_market` | 0.002460 | 0.01 | fitted | search |
| $\lambda_I$ | `jump_intensity_idio` | 0.006890 a day | 0.009 | fitted | search |
| $\sigma_I^{J}$ | `jump_sigma_idio` | 0.07521 | 0.0318 | fitted | search |
| $c_J$ | `jump_vix_coupling` | 0.2626 | same | fitted | |
| | `jump_mean_compensated` | 1.0 | same | derived | a compensated Poisson process |

### The attribution

Every change in the model price at a fixed published valuation is booked to
one of ten factors, which `engine.truth()` reports
(`market/factors.rs:61-64`, `market/tick.rs:1171-1190`). An eleventh column,
the fair-value shift, books what left $s$ for the fair-value level. It is
new in 0.8.5 (`market/factors.rs`, `python_arrow.rs`), and each tick books
as follows:

| Slot | Factor | Amount |
|---|---|---|
| 0 | reversion | $(\phi_\tau - 1) s_{i,t}$ |
| 1 | momentum | $\theta \mu_{i,d}/390$ |
| 2 | crowd lean | $L(s_{i,t}, \mu_{i,d})/390$ |
| 3 | company news | $N_{i,t}/390$, the whole shock |
| 4 | order flow impact | $O_{i,t}/390$, including agents' permanent impact |
| 5 | short squeeze | $Q_{i,d}/390$ |
| 6 | random noise | $u(\tau_t) \varepsilon_{i,t}$, the whole shock |
| 7 | circuit breaker | the change in $s$ when the breaker binds |
| 8 | jump | the jump, at the close, the whole shock |
| 9 | overnight | the overnight change (zero on pt-v20) |
| 10 | fair value shift | $-\Delta_{i,t}^{v}$, and $-\Delta_{i}^{v,J}$ at the close: the part that left $s$ for $v$ |

Slots 3, 6 and 8 keep the whole shock, because that is what moved the
price; slot 10 takes the permanent part back out of $s$. So, over any run
of rows, to rounding, except a tick where the $\pm\bar s$ cap binds, which
no slot records:

```math
\sum_{k=0}^{10} c_k = \Delta s,
\qquad
\sum_{k=0}^{9} c_k = \Delta s + \sum \Delta^{v} = \Delta s + \Delta v + \tfrac12 \sum \big(\Delta^{v}\big)^{2}
```

The first ten are the model price's log move at a fixed published
valuation, plus the Ito term. On pt-v19 slot 10 is always zero and the ten
sum to $\Delta s$. The close's share is written on the next day's first
row, beside the jump. `explain` adds two more terms, the change in
$\ln V$ (which contains $\Delta v$) and the book's $\ln(P/P^{\ast})$, so
its thirteen terms sum to $\Delta \ln P$.

## Volatility

Three variance processes set the size of the tick shocks. All three update
once a day, at the close, and none takes a random draw of its own. All three
read the VIX ratio $\rho_d = X_d / A$.

### Market-factor variance

**Timescale:** daily, at the close. **State:** a fast component $v^{f}$, a
slow component $v^{s}$, and their mixture $v$, which the next session draws
the market factor with. $F_d = \sum_t f_t$ is the day's summed market factor.
All three start at $b_m = \bar\sigma_M^{2}$.

The VIX sets a target for each component, with a steeper response above the
anchor than below it (`market/factor_vol.rs:374-391`,
`market/factor_vol.rs:721-730`, `market/factor_vol.rs:799-805`):

```math
\mathcal{R}(\rho) = \begin{cases} \rho^{e_-} & \rho < 1 \\ \rho^{e_+} & \rho \ge 1 \end{cases}
\qquad
\bar v^{f}_d = b_m\big(1 - c_m + c_m\,\mathcal{R}(\rho_d)\big),
\qquad
\bar v^{s}_d = b_m\big(1 - c'_m + c'_m\,\mathcal{R}(\rho_d)\big),\quad c'_m = c_m\,(1 - \delta_s)
```

The fast component is a GJR-GARCH(1,1) pulled toward its target; the slow
component is a plain one with a small gain on the day's shock
(`market/factor_vol.rs:325-365`, `market/factor_vol.rs:777-817`):

```math
v^{f}_{d+1} = \mathrm{clip}\Big(\big(1 - \alpha_f - \beta_f - \tfrac{\gamma_f}{2}\big)\,\bar v^{f}_d
 + \big(\alpha_f + \gamma_f\,\mathbf{1}[F_d < 0]\big)\,F_d^{2} + \beta_f\,v^{f}_d\Big)
```

```math
v^{s}_{d+1} = \mathrm{clip}\Big((1 - p_s)\,\bar v^{s}_d + g_s\,p_s\,F_d^{2} + (1 - g_s)\,p_s\,v^{s}_d\Big)
```

```math
v_{d+1} = \mathrm{clip}\big((1 - w_s)\,v^{f}_{d+1} + w_s\,v^{s}_{d+1}\big),
\qquad \mathrm{clip}(x) = \min\big(\max(x,\ 0.05\,b_m),\ 32\,b_m\big)
```

The fast component's persistence is $\alpha_f + \beta_f + \gamma_f/2 = 0.979$,
a half-life of 33 sessions; the slow one's is 0.9913, 79 sessions. The
ceiling caps the market factor at 4.3% a day.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\alpha_f$ | `market_vol_alpha` | 0.0066 | same | measured | GJR-GARCH(1,1) QML on S&P 500 daily log returns, 1990 to 2025; s.e. 0.0082 |
| $\beta_f$ | `market_vol_beta` | 0.8946 | 0.9446 | measured | same fit; s.e. 0.0181 |
| $\gamma_f$ | `market_vol_gamma` | 0.1556 | 0.06 | measured | same fit; s.e. 0.0236 |
| $p_s$ | `market_vol_slow_persistence` | 0.9913 | same | derived | the slow pole of the S&P 500 variance impulse response; bootstrap interval 0.975 to 1.000 |
| $g_s$ | `market_vol_slow_gain` | 0.05 | same | chosen | a round number |
| $w_s$ | `market_vol_slow_weight` | 0.35 | same | fitted | |
| $\delta_s$ | `market_vol_slow_vix_damp` | 0.374 | same | fitted | |
| $c_m$ | `market_vol_vix_coupling` | 0.9540 | 0.75 | fitted | search |
| $e_+$ | `market_vol_vix_exponent` | 4.0 | same | fitted | fitted to the held-VIX crisis lever: 10.3 times against 10.5 on the data |
| $e_-$ | `market_vol_vix_exponent_below` | 2.5 | same | chosen | inside the measured range: shared variance on the reference roster rises as the VIX to the power 2.25 (1.95 to 2.57) |
| | floor, ceiling multiples | 0.05, 32 | | guard | a record VIX of 82.7 against 15 is about 30 times the variance |

Two further mechanisms act on this state. Both are off on every shipped
preset, where the recursions above are the whole of it.

**Slow-component asymmetry** (`market_vol_slow_gamma`, $\gamma_s$). The slow
component takes a GJR term, and its carried share gives back half of it, so
its persistence $p_s$ and its resting level do not change
(`slow_step` in `market/factor_vol.rs`):

```math
v^{s}_{d+1} = \mathrm{clip}\Big((1 - p_s)\,\bar v^{s}_d + \big(g_s\,p_s + \gamma_s\,\mathbf{1}[F_d < 0]\big)\,F_d^{2}
 + \big((1 - g_s)\,p_s - \tfrac{\gamma_s}{2}\big)\,v^{s}_d\Big)
```

$\gamma_f$ acts only on the fast 65% of the factor variance, and the factor
is about three quarters of the index variance, so without $\gamma_s$ the
index remembers a fall for weeks where the S&P 500 remembers it for months.

**Return memory** (`market_vol_leverage` $k$, `market_vol_leverage_half_life`
$H$, `market_vol_leverage_down` $a$). The engine keeps $\ell$, an
exponentially weighted memory of the day factor in baseline sd units, and the
variance the next session draws with is the mixture times a lognormal
multiplier (`close_with_leverage_memory` in `market/factor_vol.rs`):

```math
u_d = -\frac{F_d}{\sqrt{b_m}}\big(1 - a\,\mathbf{1}[F_d > 0]\big) - \frac{a}{\sqrt{2\pi}}\sqrt{\frac{v_d}{b_m}},
\qquad
\ell_{d+1} = \phi\,\ell_d + (1 - \phi)\,u_d,\quad \phi = 2^{-1/H}
```

```math
v_{d+1} = \mathrm{clip}\Big(\bar v_{d+1}\,\exp\big(k\,\ell_{d+1} - \tfrac{k^{2} s^{2}}{2}\big)\Big),
\qquad
s^{2} = \frac{1 - \phi}{1 + \phi}\Big(\frac{1 + (1 - a)^{2}}{2} - \frac{a^{2}}{2\pi}\Big)
```

$\bar v_{d+1}$ is the component mixture above, and the components are fed
$F_d\sqrt{\bar v_d / v_d}$, the day factor in their own units, so a fall is
not counted twice. A forced close (a scenario's `vix_sets_variance`) steps
$\ell$ but writes the forced level without the multiplier. The snapshot and
the state hash carry $\ell$ only while $k$ is set.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\gamma_s$ | `market_vol_slow_gamma` | 0 (off) | 0.05 | | crash-vol-state design; in $[0, 1]$ and at most $2(1 - g_s)p_s$ |
| $k$ | `market_vol_leverage` | 0 (off) | 2.5 | | in $[0, 50]$ |
| $H$ | `market_vol_leverage_half_life` | 0 | 15 | | sessions; positive when $k$ is set |
| $a$ | `market_vol_leverage_down` | 0 | same | | 0 counts rises and falls alike, 1 falls only |
| $\varsigma$ | `market_vol_leverage_standardise` | 0 | 1 | | the unit a day is counted in: $\sqrt{b_m}^{\,1-\varsigma}\sqrt{v_d}^{\,\varsigma}$ in place of $\sqrt{b_m}$ above; 1 is the day's z-score |

With $\varsigma = 1$ the memory's spread is the same at every variance, so the
multiplier's mean is one in a storm as in a calm and a fall raises the next
session's variance by its surprise, not by its size. In baseline units
($\varsigma = 0$) a fall drawn at twice the baseline sd moves $\ell$ twice as
far, which amplifies crashes: on pt-v20 that form bought its leverage sum
with B5 (15.3 against a ceiling of 12.4).

Why the memory could not be turned on by itself, and what it takes
(sim/r15-volstate, held-out seeds 201-230, 501-530 and 801-830). The memory
multiplies the variance the index read-back sees, so the VIX rises with it and
the factor's VIX coupling (0.8, exponent 4 above the anchor) feeds that back
into the variance target: the loop turns a mean-one multiplier into a higher
volatility level and more clustering. On the r14 candidate N4 (45 histories)
a gain of 1.5 on a 15-session half-life moved the leverage sum from -0.63 to
-0.94, but index volatility (B7) from 20.5 to 23.5, sessions under -5% (B5)
from 11.6 to 19.0 and the index |r| lag-1 ACF from 0.31 to 0.38. The memory
works when it replaces return-blind volatility of volatility rather than adding
to it: the VIX's slow level halved (`vix_level_sigma` 0.0181 to 0.009), the
fast component's shock loading moved into its carry at unchanged persistence
(`market_vol_gamma` 0.1556 to 0.06, `market_vol_beta` 0.8946 to 0.9446, so
$\alpha + \beta + \gamma/2$ stays 0.979), and the VIX the variance target
reads smoothed over 3 sessions (`market_vol_vix_smooth`). With $k = 2.5$,
$H = 15$ and $\varsigma = 1$ on those, 90 histories read a leverage sum of
-0.97 (tape -1.35, band [-1.75, -0.80]), B5 9.8, B7 21.2, and the 2020
replay's worst month 72 against N4's 69. The cost is the 60-session
volatility persistence the slow level carried: the seam correlation of log
RV60 falls from 0.627 to 0.559 (tape 0.712).

Measured against baseline units at the same level cut, the z-score count was
not the more efficient form: per unit of leverage sum it cost about as much B5
and B7 (45 histories each). It is kept for the mean-one multiplier in a storm,
not for a better trade.

**A Student-t day** (`market_day_tail_df` $\nu$, `market_day_tail_state_share`
$\varsigma_t$). Off on every shipped preset. Off zero, each open draws one
multiplier on the day's market variance on the overnight stream, after the
night's normals (a gamma by Marsaglia and Tsang), and the night's market draw
and every tick read the session's sigma times its root
(`draw_market_day_scale` and `market_sigma_today` in `engine.rs`):

```math
m_d = \min\Big(\frac{\nu - 2}{X_d},\ \max\big(1,\ C\,b_m / v_d\big)\Big),
\qquad X_d \sim \chi^{2}_{\nu},
\qquad \sigma_{d}^{\text{day}} = \sqrt{m_d\,v_d}
```

$E[m_d] = 1$, so the day's market factor is a unit-variance Student t at the
state's variance and the VIX, which reads $v_d$, does not see it in advance.
$C$ is the ceiling multiple (32). The close feeds the variance state the day
factor times $m_d^{-(1 - \varsigma_t)/2}$: at $\varsigma_t = 0$ a fat-tailed
day moves the next day's variance no more than a normal one, at 1 it moves it
by its size (the GARCH-t recursion). The close clears $m_d$ to 1; the snapshot
and the state hash carry it only between the open and the close.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\nu$ | `market_day_tail_df` | 0 (off) | same | | 0 or in [3, 200]; the S&P 500's GJR-GARCH(1,1)-t fit 1990-2025 reads 6.9 (profile 95% interval 6.0 to 8.0) |
| $\varsigma_t$ | `market_day_tail_state_share` | 0 | same | | in [0, 1]; read only with $\nu$ set |

What it was measured to do (sim/r17-d1tail, R16A, the certification's
varying-roster protocol, 720 held-out seeds: 201-230, 501-530, 801-830,
2001-2270 and the same plus 20000). At $\nu = 7$, $\varsigma_t = 1$ the index's
one-year excess kurtosis rose from a median of 0.8 to 1.6 (tape 1.46) and the
share of seeds with no session at or below -3 per cent fell from 0.66 to
0.58, but the tail row `index_tail_dn3_pct` rose only from 0.64 to 0.76
(tape 1.21 over 1990-2025, band [0.64, 2.34]): at the varying rosters' index
volatility, about 15 per cent against the tape's 18, a -3 per cent day is a
3-sigma day, near where a t and a normal of equal variance cross, and the
t's extra mass sits further out. It is therefore not in the vector the
sim/r17-d1tail recommends on top of R16A; see [Jumps](#jumps) for what it recommends instead.

#### The business cycle in the market's volatility

On in pt-v21 at `market_vol_cycle_ratio` 2.4706 (42/17), off on every earlier
preset (0.0, where the close takes a branch that reads and moves nothing).
Off zero, the close keeps $\ell$,
the log of a multiplier on the market factor's volatility, and moves it toward
the TRUE phase's value (`engine.rs:4464-4482`, `engine.rs:5438-5477`):

```math
\ell^{*} = \begin{cases} \ln(R\,k_e) & \text{contraction, trough} \\ \ln k_e & \text{otherwise} \end{cases}
\qquad
\ell_{d} = \ell_{d-1} + \big(1 - 2^{-1/h}\big)\big(\ell^{*} - \ell_{d-1}\big)
```

The first close, and every close at $h = 0$, sets $\ell = \ell^{*}$. The level
the baseline $b_m$ is scaled by is multiplied by $e^{2\ell}$, and the VIX
ratio's denominator by the scale $c$; the VIX anchor's slow memory and the
anchor level the VIX reverts to are scaled by the same $c$
(`engine.rs:4958`, `engine.rs:4990-4991`, `engine.rs:5496-5517`):

```math
c = \max\!\big(e^{d\,\ell},\ 10 / A\big), \qquad
d = \begin{cases} d_{+} & \ell \ge 0 \\ d_{-} & \ell < 0 \end{cases}
```

with $A$ the VIX anchor and 10 the VIX's floor. At $d = 1$ fear is read
against the phase's normal level. The calm-side power $d_{-}$ is separate
because the tape's calm phase keeps the unconditional fear level (the VIX's
median is 17.0 in an expansion against 17.6 over all sessions): at
$d_{-} = 0$ the anchor and the coupling's reference stay put while the
variance falls, so the fear loop deepens the calm and the VIX falls less per
unit of variance. The floor binds only below a scale of about 0.48, where an
unfloored denominator under the VIX's floor read a quiet phase as a panic. A
forced close (a VIX a scenario pinned) moves $\ell$ but writes the level that
VIX implies, unscaled. At $k_e = 0$ the expansion
multiplier is derived, $k_e = 1/\sqrt{1 - s + R^{2}s}$ with $s$ the
contraction-and-trough share of the cycle's days, which keeps the
share-weighted factor variance. The snapshot and both state hashes carry
$\ell$ only while $R$ is set.

Real index volatility is countercyclical: S&P 500 daily volatility on NBER
recession months over the rest is 1.66 (1950 to 2025), 1.87 (1928 to 2025) and
2.24 (1990 to 2025), and the VIX's median is 27.5 in a recession against 17.0
outside one. pt-v20 reads 1.27 and about 1.0 on held-out histories.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $R$ | `market_vol_cycle_ratio` | 0 (off) | 2.471 | | bear-dynamics design; recession over expansion index volatility |
| $k_e$ | `market_vol_cycle_expansion` | 0 (derived) | 0.82 | | read only with $R$ set |
| $h$ | `market_vol_cycle_half_life` | 0 | 10 | | sessions; read only with $R$ set |
| $d_{+}$ | `market_vol_cycle_relative` | 0 | 0.75 | | a power in [0, 1], read at $\ell \ge 0$; read only with $R$ set |
| $d_{-}$ | `market_vol_cycle_relative_calm` | 0 | same | | a power in [0, 1], read at $\ell < 0$; read only with $R$ set |
| $p$ | `market_vol_cycle_cap_relative` | 0 | 1 | | a power in [0, 1], read at $\ell > 0$; read only with $R$ set |
| | `market_vol_cycle_pin_neutral` | 0 | 1 | switch | 0 or 1; read only with $R$ set |
| | `market_vol_cycle_pin_phase` | 0 | 1 | switch | 0 or 1; read only with $R$ set |
| $g$ | `market_vol_cycle_trough_release` | 0 | same | | a share in [0, 1]; read only with $R$ set |
| $h_{r}$ | `market_vol_cycle_release_half_life` | 0 | same | | sessions, 0 is $h$; read only with $R$ set |
| $g_{m}$ | `market_vol_cycle_recovery_release` | 0 | 0.45 | | a share in [0, 1]; read only with $R$ set |
| $x_{m}$ | `market_vol_cycle_recovery_scale` | 0 | 0.1 | | log points of the index in (0, 2]; read only with $g_{m}$ set |

`fair_value_market_vol_cap`'s ceiling is in multiples of the unscaled
`market_factor_sigma`, so a contraction's higher baseline counts as fear
there and more of a contraction's market moves is transient and reverts
(the permanent share in a contraction 0.93 against 0.96 on the
bear-dynamics review's regrade). With $p$ set the ceiling is multiplied by
$e^{p\ell}$ while $\ell > 0$ (`market/tick.rs`, `market_permanent_share`),
so only volatility above the phase's own normal counts; see
`ModelParams::market_vol_cycle_ratio` and
`ModelParams::market_vol_cycle_cap_relative`.

**Pins, the trough and the release.** Under `market_vol_cycle_pin_neutral`
a session whose VIX a caller pinned (a replay, a scenario's VIX
transmission) applies no multiplier: the level, the denominator, $c$ and the
cap's scale all read $\ell = 0$, and $\ell$ steps toward 0 rather than
$\ell^{*}$, so when the pins stop it moves from there at the half-life.
`market_vol_cycle_pin_phase` does the same on a session whose cycle phase a
caller pinned. A pinned VIX or phase is the caller's statement of the state:
the long run's 2020 replay pins the real VIX over an engine whose own cycle
is in an expansion, and a multiplier under one there cut the replay's worst
month from 69 to 59 per cent (A1); the NBER-phased recession candidate
(`tools/calibration/scenario_candidates/recession_I.yml`) pins the phase and
brings its own VIX, credit and earnings path, and the contraction's doubled
volatility on top of it moved S2 from +64 to +102 per cent. With
$g$ = `market_vol_cycle_trough_release` the trough's target is
$\ln k_e + (1 - g)\ln R$ (at $g = 0$ the contraction's), and while $\ell$
falls toward a lower target it steps at $h_{r}$ =
`market_vol_cycle_release_half_life` instead of $h$ (at 0, $h$): the VIX and
realised volatility peak at the market's low and fall within a quarter of
it (at the 1990, 2002, 2009 and 2020 lows the VIX read 34, 42, 50 and 62,
and 27, 26, 30 and 32 sixty-three sessions later).

**The rally off the low.** With $g_{m}$ =
`market_vol_cycle_recovery_release` the target in a contraction is
$\ln k_e + (1 - g_{m} s)\ln R$ and in a trough $\ln k_e + (1 - g)(1 - g_{m}
s)\ln R$, where $s = \min(1, (c - L)/x_{m})$, $c$ the index's log level at the
last close, $L$ its lowest close since its highest close of the last 252
sessions (total public market cap, the window `fed_drawdown_hold` reads,
kept whenever either dial is set) and $x_{m}$ =
`market_vol_cycle_recovery_scale`. At a new high or a new low $s = 0$. The
release reads the market and not the phase: a trough release keyed to the
true phase lowers volatility on dates a rule reading the published phase
covers, and on R19V with $g = 1$ the rule that levers the published
contraction and trough beat the exposure-matched constant position in 0.689
of 270 pooled held-out histories against C10c's 2/3. The market signal does
not remove that trade-off. Every arm that shortens the storm lowers
volatility in the true recovery, which the published contraction covers, and
raises the same rule's share ahead (sim/r20-mktrelease screen, `r14gen` over
held-out sets A, B and C, 270 histories each, on R20F):

| Arm (on R20F) | VC4f | out_contraction_trough ahead | P(any C10c breach) | PH5 vol use |
|---|---|---|---|---|
| R20F | 0.732 | 0.600 | 0.017 | 0.85 |
| $g_{m}$ 1, $x_{m}$ 0.10 | 0.649 | 0.644 | 0.65 | 0.94 |
| $g_{m}$ 0.5, $x_{m}$ 0.10 | 0.687 | 0.644 | 0.23 | 1.03 |
| $g_{m}$ 1, $x_{m}$ 0.20 | 0.678 | 0.663 | 0.55 | 1.08 |
| return memory 2.5 alone | 0.723 | 0.548 | 0.082 | 0.72 |
| return memory 2.5, $g_{m}$ 0.3, $x_{m}$ 0.10 | 0.706 | 0.570 | 0.086 | 0.73 |
| return memory 2.5, $g_{m}$ 0.45, $x_{m}$ 0.10 (R20M) | 0.697 | 0.593 | 0.075 | 0.82 |

P(any C10c breach) is the share of 2000 bootstrap resamples of the 270
histories in which any of C10c's 384 mirrored rules has a median over +1.0
or more than 2/3 ahead. The return memory (`market_vol_leverage`) at 2.5 in
place of 2 is what takes the published-phase rule down (volatility rises
after a fall and eases in a rally whatever the phase), but alone it puts the
index's absolute-return autocorrelation at lag 1 on its ceiling (VC4a 0.305
against 0.305); a rally release of 0.45 brings VC4a back to 0.273 and VC4f
to 0.697 (band 0.489 to 0.740). On 270 further histories (sets +60000,
+70000 and +80000) R20M reads VC4f 0.708 against R20F's 0.739, the rule
0.607 ahead, P(any C10c breach) 0.084 and PH5's volatility use 0.88; the
release of 0.3 there reads VC4f 0.723 and PH5's use 0.91.

**Volatility persistence on R19V** (measured, no dial added; boxes vcp1 to
vcp6, sim/r18-valopen b93b9999, `r14gen`'s recording over held-out sets A, B
and C, 270 histories). R19V's monthly realised volatility is too persistent:
the lag-one autocorrelation of log monthly realised volatility (VC4f) reads
0.725 against the band 0.489 to 0.740 (CRSP 20-year windows, p10 to p90;
the S&P 500 tape 1990-2025 reads 0.676). The excess is a bias, not noise: the per-history sd is
0.075, so the median's se is about 0.006. It is the cycle multiplier held at
the contraction's level through the trough: with `market_vol_cycle_ratio` off
VC4f reads 0.662 (and B10, B11 and B12 fail), and a VIX-residual of log
realised volatility is as persistent with the multiplier as without it. The
other candidates move it less: `market_vol_slow_gamma`
(+0.009 at 0), `market_vol_vix_smooth` (+0.013 at 0), `vix_level_sigma`
(-0.003 at half), `market_vol_slow_vix_damp`, `market_vol_slow_weight` and
`market_vol_slow_persistence` (+0.008 to +0.012), `market_vol_leverage`
(+0.005 at 1), `market_vol_vix_coupling` (-0.010 at 0.6),
`market_vol_vix_exponent` (-0.016 at 3.5) and `market_day_tail_df` (-0.019
at 7, -0.034 at 5, with CV1 out of band at 5). $g$ = 1, the trough at the expansion's multiplier, takes VC4f
by -0.039 (se 0.003, paired), the absolute-return ACF at lag 1 by -0.030 and
at lag 20 by -0.029, and time with the VIX above 30 from 6.9 to 4.6 per cent
(B1's floor 4.1). A return memory of 2.5 in place of 2 gives back the leverage
sum (CV1 -0.98 against -0.84) and the VIX's time above 30 (5.1 per cent).
Doubling `treasury_haven_gain` to 0.02 takes H4 from -0.28 to -0.30.

| Arm (on R19V) | VC4a | VC4c | VC4f | H1 | H4 | CV1 | VIX > 30 |
|---|---|---|---|---|---|---|---|
| R19V | 0.277 | 0.218 | 0.725 | -0.646 | -0.278 | -0.886 | 6.9% |
| $g$ 1, haven 0.02 (K1) | 0.244 | 0.185 | 0.689 | -0.612 | -0.306 | -0.840 | 4.6% |
| K1 with return memory 2.5 (M1) | 0.267 | 0.198 | 0.691 | -0.610 | -0.303 | -0.977 | 5.1% |
| haven 0.02, exponent 3, memory 2.5 (E2) | 0.262 | 0.195 | 0.687 | -0.620 | -0.309 | -0.996 | 6.4% |

Bands: VC4a 0.158 to 0.305, VC4c 0.077 to 0.245, VC4f 0.489 to 0.740, H1
-0.69 to -0.13, H4 -0.94 to -0.24, CV1 -1.75 to -0.80. M1 on each set of 90
reads VC4f 0.691, 0.698 and 0.682 (paired against R19V -0.036, se 0.004), and R4 on the held close 0.384, 0.376 and
0.373 (R19V 0.381, 0.379 and 0.380). E2 keeps more time above 30 but lowers
`market_vol_vix_exponent`, which moves every pinned-VIX path, and the replays
and the crisis lever were not measured on it.

### Company variance (GJR-GARCH)

**Timescale:** daily, at the close, before the jumps. **State:** $h_{i,d}$,
starting at the sector's $\sigma_k^{2}$.

The reference level follows the VIX (`market/daily.rs:205-212`), and the
recursion is a GJR-GARCH(1,1) clamped to a band around it
(`market/garch.rs:469-505`):

```math
\bar h_{i,d} = \sigma_{k(i)}^{2}\,\big(1 - c_g + c_g\,\rho_d^{2}\big)
```

```math
h_{i,d+1} = \mathrm{clip}\Big(\omega + \big(\alpha + \gamma\,\mathbf{1}[e_{i,d} < 0]\big)\,e_{i,d}^{2} + \beta\,h_{i,d};\
 f_g\,\bar h_{i,d},\ C_g\,\bar h_{i,d}\Big),
\qquad
e_{i,d} = \sum_{t} u(\tau_t)\,\varepsilon_{i,t}
```

The innovation $e_{i,d}$ is the day's whole noise term for the company:
market, sector and own parts together (`engine.rs:3672-3675`). The
persistence $\alpha + \beta + \gamma/2 = 0.9416$ is a half-life of 11.5
sessions. The recursion's own unconditional level,
$\omega / (1 - 0.9416) = 3.4 \times 10^{-5}$, sits below the floor
$0.25 \sigma_k^{2}$ in 8 of the 12 sectors, so in those sectors the floor,
not $\omega$, holds the resting level.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\omega$ | `garch_omega` | 0.000002 | same | chosen | reference implementation |
| $\alpha$ | `garch_alpha` | 0.05951 | same | fitted | search |
| $\beta$ | `garch_beta` | 0.7905 | same | derived | $0.9416 - \alpha - \gamma/2$, where 0.9416 is the decay of the absolute-return autocorrelation on the reference roster |
| $\gamma$ | `garch_gamma` | 0.1832 | same | chosen | the value the same identity gives, 0.65, is not stationary |
| $c_g$ | `garch_vix_coupling` | 0.1422 | same | fitted | search |
| | `garch_vix_exponent` | 2.0 | same | chosen | |
| $f_g$, $C_g$ | `garch_floor_multiple`, `garch_ceiling_multiple` | 0.25, 5.0 | same | guard | the floor binds; see above |

### Sector variance

**Timescale:** daily, at the close. **State:** $h_{k,d}^{S}$, a variance ratio
whose fixed point is 1 (`engine.rs:1669-1697`). $D_{k,d} = \sum_t G_{k,t}$ is
the day's summed sector factor.

```math
h^{S}_{k,d+1} = \mathrm{clip}\Big((1 - a_s - b_s) + a_s\,\frac{D_{k,d}^{2}}{(\bar\sigma_S\,\rho_d)^{2}} + b_s\,h^{S}_{k,d};\ 0.25,\ 5\Big)
```

Its persistence is 0.904, a half-life of 7 sessions.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $a_s$ | `sector_vol_alpha` | 0.067 | same | measured | GARCH(1,1) QML on seven sectors' residuals after the market, reference roster 2015 to 2025, scaled by the VIX; s.e. 0.043 |
| $b_s$ | `sector_vol_beta` | 0.837 | same | measured | same fit; s.e. 0.111 |

## The VIX

The VIX is computed, not simulated on its own. Each day it moves toward the
volatility the index's own conditional variance implies, plus a fear
response to the day's return.

### The index variance and the anchor

**Timescale:** once a day, in the macro step. The index is cap-weighted,
with weights $w_i$ over listed, solvent companies. Its one-day-ahead
variance adds up the tick process's parts (`market/index_var.rs:843-1014`):

```math
V_d = K_u\Big[\,v_{d+1}\,E[z^{2}\Gamma^{2}]\,\bar\beta^{2}\,\Lambda_d\,\tfrac{1 + (1 + a)^{2}}{2}
 + \sum_k\Big(\sum_{i \in k} w_i \ell_i\Big)^{2}\bar\sigma_S^{2}\rho_d^{2}\,h^{S}_{k,d+1}
 + \sum_i w_i^{2}\,\iota_i^{2}\Big] + J^{V}_d
```

```math
J^{V}_d = \lambda_{M,d}\,\big(\mu_M^{2} + (\sigma^{J}_M)^{2}\big) - (\lambda_{M,d}\,\mu_M)^{2}
 + \lambda_{I,d}\,(\sigma^{J}_I)^{2}\sum_i w_i^{2}
 + \lambda_N\,\sigma_N^{2}\sum_i w_i^{2}
```

Here $\bar\beta = \sum_i w_i\beta_i$; $K_u = 1.0844$ is the session mean of
$u(\tau)^{2}$; $E[z^{2}\Gamma^{2}] = 1.0541$ is the crash amplifier's second
moment; $\iota_i$ is the company's own-noise scale from the factor
structure; $\Lambda_d = (1 + a_L)^{2}$ if today's summed market factor
was negative, else 1; and $J_d^{V}$ is the variance the jumps and news add.

The VIX it implies, and the anchor $A$ (`market/index_var.rs:1189-1191`,
`engine.rs:1583-1606`):

```math
I_d = (1 + \varpi)\cdot 100\,\sqrt{252\,V_d},
\qquad
A = (1 + \varpi)\cdot 100\,\sqrt{252\,\bar V}
```

$\varpi$ is the variance risk premium, the gap between the VIX and realised
volatility. $\bar V$ is the same sum at the unconditional point: the factor
at its baseline, sector states at 1, each company's variance at its resting
level, and $\Lambda$ averaged over up and down days. $A$ is computed once, when
the engine is built, and depends on the roster: on pt-v20 20.9 on
`Universe.random(40, seed=111)` and 17.9 on `Universe.random(20, seed=101)`
(pt-v19: 24.0 and 20.6). It is lower than pt-v19's because the market
factor's sigma and the market jump rate are lower.

### The VIX step

**Timescale:** once a day, in the macro step. **Draws:** one normal and one
or two uniforms on the economy stream, and one normal on the VIX level
stream at the close. **State:** $X_d$, a slow log level $q_d$, and the
anchor's slow memory $M_d$.

A slow level wanders around the anchor (`engine.rs:4352-4365`,
`engine.rs:5479-5502`), and a slow memory tracks the read-back
(`engine.rs:4937-4968`):

```math
q_d = \phi_q\,q_{d-1} + \frac{\sigma_\ell}{G_\ell}\,Z,
\qquad
\Xi_d = \exp\Big(q_d - \frac{1}{2}\,\frac{(\sigma_\ell / G_\ell)^{2}}{1 - \phi_q^{2}}\Big),
\qquad
M_d = (1 - h_M)\,M_{d-1} + h_M \ln\frac{I_d}{A\,e^{-c_A}}
```

The target (`economy/daily.rs:1362-1457`):

```math
T_d = \Xi_d\,I_d\,e^{-a(X_d)\,M_d}
 + \min\Big(C,\ \Phi(r_d, X_d) + 0.2\,(\pi - 3)^{+}\Big) - \bar\Phi_d
```

where $r_d$ is the index's open-to-close return in percent, clipped to
$\pm 15$, $\pi$ is inflation, and the fear response is

```math
\Phi(r, X) = \begin{cases}
G_{\downarrow}\,\lvert r \rvert^{e_\downarrow}\,X^{-(e_\downarrow - 1)} & r < 0 \\
-G_{\uparrow}\,r^{e_\uparrow}\,X & r > 0
\end{cases}
```

A fall raises the VIX, by less when the VIX is already high; a rise lowers
it. $\bar\Phi_d$ is the mean of $\Phi$ under a normal return with the index's
own variance, subtracted so the fear response adds no drift
(`economy/daily.rs:658-680`). The weight on the slow memory falls as the VIX
rises: $a(X) = 1 - (1 - a_0) X_d^{\ast} / \mathrm{clip}(X;\ X_d^{\ast},\ 2.216 X_d^{\ast})$ with
$X_d^{\ast} = A \Xi_d e^{-0.3888}$, so $a$ runs from 0.375 to 0.718
(`economy/daily.rs:609-617`).

The step (`economy/daily.rs:1499-1571`):

```math
X_{d+1} = \mathrm{clip}\Big(X_d + \kappa_X\,(T_d - X_d) + \sigma^{X}_d\,Z + J_d;\ 10,\ X_{\max}\Big),
\qquad
\sigma^{X}_d = c_r\,X_d\,\lvert r_d \rvert
```

```math
J_d = J_s\,\sigma^{X}_d\,E\cdot\mathbf{1}\Big[U < \frac{\lambda_J \max(0,\ -r_d)}{252}\Big],\qquad E \sim \mathrm{Exp}(1)
```

The VIX reverts with a half-life of about 2.2 sessions. On a day the index
does not move, it takes no noise and no jump.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $\varpi$ | `vix_variance_premium` | 0.252 | same | measured | median yearly VIX over realised volatility, S&P 500 and VIX 1990 to 2025; interquartile range ±0.13 |
| | `vix_level_identity` | 1.0 | same | measured | switches the target to the index's implied VIX; chosen on a 120-seed paired comparison |
| $\kappa_X$ | `vix_mean_reversion` | 0.27 a day | same | chosen | needs a joint solve with the fear gain |
| | `vix_decay_ratio` | 1.0 | same | measured | the same reversion up and down; 120-seed comparison |
| $G_\downarrow$ | `vix_return_gain` | 8.83 | same | fitted | matches the data's same-day response at $\kappa_X$ = 0.27 |
| $e_\downarrow$ | `vix_return_exponent` | 1.448 | same | measured | regression of the VIX change on the index move and the VIX level, 1990 to 2025; s.e. 0.12 |
| $G_\uparrow$ | `vix_return_gain_up` | 0.049 | same | chosen | |
| $e_\uparrow$ | `vix_return_exponent_up` | 0.5433 | same | measured | up-day fit; s.e. 0.04 |
| $C$ | `vix_target_shock_cap` | 158.9 | same | derived | the largest value $\Phi$ can take; never binds |
| $X_{\max}$ | `vix_ceiling` | 181.3 | same | guard | a lower bound carried from an earlier solve |
| $c_r$ | `vix_innovation_return_sigma` | 0.0175 | same | measured | residual fit on the VIX, 1990 to 2025; s.e. 0.0015 |
| $J_s$ | `vix_jump_level_scale` | 1.7 | same | measured | cumulant fit of the residual; s.e. 0.35 |
| $\lambda_J$ | `vix_jump_return_intensity` | 6.199 | same | derived | 2.24 fear jumps a year, spread over down days |
| $a_0$ | `vix_anchor_weight` | 0.375 | same | derived | from the data's level elasticity, 0.655 |
| $h_M$ | `vix_anchor_memory` | 0.0556 | same | fitted | fitted to the VIX persistence statistic |
| $c_A$ | `vix_anchor_centre` | 0.1515 | same | derived | $\ln(1.252/1.076)$ |
| $\phi_q$, $\sigma_\ell$, $G_\ell$ | `vix_level_persistence`, `vix_level_sigma`, `vix_level_loop_gain` | 0.9979, 0.0181, 1.79 | same | derived | yearly medians of the log VIX, 1990 to 2024 |
| | `vix_return_clamp` | 15 | same | guard | |

### The published quote

**Timescale:** once a day, at the close. **Draws:** none. **State:** a
stress memory $m_d$, carried only while the gain is set.

The loop damps the VIX state against the anchor's slow memory $M_d$, which
it needs for stability. The damping costs the quote its level in stress: on
pt-v20's held-out histories the median VIX over trailing 21-session realised
volatility, on sessions with that volatility at 40 or more, is 0.67, where
the S&P 500 and ^VIX tape, 1990 to 2025, gives 0.83. Three dials, 0 on every
preset, lift the published quote only (`engine.rs:4954-4967`,
`engine.rs:5431-5466`):

```math
m_d = \begin{cases} 0 & \text{the VIX was pinned today} \\
(1 - h_M)\,m_{d-1} + h_M \ln\dfrac{I_d}{A\,e^{-c_A}} & \text{otherwise} \end{cases}
\qquad
\pi_d = P\Big(1 - e^{-g\,(m_d - k)^{+} / P}\Big),
\qquad
Q_d = \min\big(X_d\,e^{\pi_d},\ X_{\max}\big)
```

In a free run $m_d$ is $M_d$ to the bit. $Q_d$ is what `macro_fields["vix"]`,
`macro_state.vix`, `macro_table()` and the wasm getter report. Every reader
inside the engine reads the state $X_d$: the variance couplings, the
fair-value discount, the book, rates, the central bank, fear and greed, the
crisis thresholds and $M_d$ itself. So with the premium on, every price and
every other macro series is the one the premium-off run gives, and
`state_snapshot()["economy"]["vix"]` and the `macro.vix` intervention's read
are the state. A pin writes the state and resets $m_d$, so the quote reads
the pin back.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $g$ | `vix_stress_premium` | 0 (off) | 3 | fitted | vix-peaks design, held-out seeds 201-230, 501-530, 801-830; not adopted |
| $k$ | `vix_stress_premium_knee` | 0 | 0.6 | fitted | on $M_d$'s log scale |
| $P$ | `vix_stress_premium_cap` | 0 | 0.35 | chosen | the largest log premium; the quote is at most $e^{P}$ times the state |

### The fear memory

**Timescale:** once a day, at the close, before the step. **Draws:** none.
**State:** a fear memory $f_d$ in log units, carried only while the uptake
is set.

The step above reverts the VIX to $T_d$ at $\kappa_X$ = 0.27 a session, so a
move of the VIX that the variance read-back does not share is gone in a few
sessions. The tape's moves last longer. A day's change in ^VIX is still 0.67
of itself 11 sessions later, 0.47 at 32 and 0.31 at 74 (local projections
on the day's change with the previous close held, 2004 to 2025), where
pt-v21 reads 0.60, 0.38 and 0.21. CBOE's term structure prices the same
persistence: VIX3M moves 0.64 of a VIX point, VIX6M 0.45 and VIX1Y 0.30.
Two dials, 0 on every preset, keep a memory of the VIX's own excursion over
its target (`economy/daily.rs`, `advance_vix_fear`):

```math
f_d = 2^{-1/H} f_{d-1} + k \ln\frac{X_d}{T^{\ast}_d\, e^{f_{d-1}}},
\qquad
T_d = T^{\ast}_d\, e^{f_d} + \min\Big(C,\ \Phi(r_d, X_d) + 0.2\,(\pi - 3)^{+}\Big) - \bar\Phi_d
```

where $T^{\ast}_d = \Xi_d I_d e^{-a(X_d) M_d}$ is the target's level, so the
step reverts to a level that carries a share $k$ of each excursion the VIX
held and lets it go at the half-life $H$. The forecast
(`forecast_horizon_sessions`) and the live VIX (`vix_intraday_live`) advance
the memory as the close does. The memory reads the VIX state, so a pinned
VIX is an excursion like any other.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $k$ | `vix_fear_uptake` | 0 (off) | 0 (off) | chosen | the share of the excursion taken up a session; in $[0, 1)$ |
| $H$ | `vix_fear_half_life` | 0 | 0 | chosen | the memory's half-life in sessions; read only with $k$ set |

## Crisis regimes

A crisis is a state of the VIX. There is no separate regime switch: every
variance process scales with the VIX, so a high VIX is a violent market.
Three things change above a threshold $X_c$.

**The crisis spike** (`market/tick.rs:330-348`):

```math
\chi_d = \min\big(0.98,\ (X_d - X_c)^{+} / 1.4\big)
```

It saturates at a VIX of about 32.3. On pt-v20 it acts only on news: a
sector peer's news spreads with weight $w_p (1 + 8\chi_d)$. The crisis blend
that once raised every company's market loading in a crisis is switched off
(`crisis_blend_gain` = 0): the data showed no crisis correlation beyond
what the higher common volatility already gives.

**The crisis episode and its epicentre** (`engine.rs:3716-3788`). An episode
starts at the open of the first session with $X_d > X_c$. One uniform on the
epicentre stream picks the sector it starts in: financial services with
probability 0.6, no epicentre with 0.4. The episode ends after 21
consecutive sessions at or below $X_c$. While it runs, and if a company in
the epicentre sector is listed, the sector and own-noise legs of each
company's shock are scaled by $\xi_i$ (`market/factors.rs:334-352`):

```math
\xi_\uparrow^{2} = \frac{m\,(e^{2} - 1)}{1 - m} + e^{2}\,\xi_\downarrow^{2},
\qquad
\xi_\downarrow^{2} = \frac{(1 - m) - m\,w\,(e^{2} - 1)}{(1 - m)\big(1 + w\,(e^{2} - 1)\big)}
```

with $e$ = 1.93, $m$ = 0.3916 and $w$ = 0.1019. This gives
$\xi_\uparrow$ = 2.04 in the epicentre and $\xi_\downarrow$ = 0.80 elsewhere: the
epicentre's non-market volatility is $e$ times the rest's, and the roster's
mean non-market variance is unchanged. A scenario can pick the epicentre.

**Macro gates.** Above a VIX of 25.5 the dollar takes a safe-haven bid
(above). Its threshold is `usd_crisis_vix_threshold`, which does not
follow $X_c$.

$X_c$ is a coefficient like the others, readable and settable from both
languages. In Python it is `engine.model.crisis_vix_threshold`, and a model
with another value is `ModelParams.from_dict` of a `to_dict()` with the key
changed. In Rust it is `engine.crisis_vix_threshold()` (or
`params().crisis_vix_threshold`), set before construction with
`ModelParams::with_override("crisis_vix_threshold", x)`, and
`engine.vix_above_crisis_threshold()` applies the gates' own strict test. A
host with crisis gates of its own should read it from the engine, because
it is 30.88325108 from pt-v13 on and 25.5 before.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $X_c$ | `crisis_vix_threshold` | 30.88 | same | fitted | search |
| $e$ | `crisis_epicentre_extra` | 1.93 | same | derived | median of the epicentre's volatility ratio in three crises on the reference roster: 2.43 (2008-09), 1.93 (2011), 1.41 (2020) |
| | epicentre weights | 0.6, 0.4 | | derived | see [The sectors](#the-sectors) |
| | `crisis_epicentre_end_sessions` | 21 | same | chosen | a month of sessions |
| | `crisis_blend_gain` | 0 | same | derived | the data show no crisis correlation beyond common volatility |
| | `crisis_blend_cap`, `crisis_blend_ramp` | 0.98, 1.4 | same | chosen | now shape only the news spike |
| $c_p$ | `news_peer_vix_coupling` | 8.0 | same | fitted | |

## The market maker and the book

**Timescale:** every tick. The book is rebuilt from scratch each tick; two
things carry over between ticks, the last print $P_{i,t-1}$ and the maker's
inventory $I_i$ in shares (`microstructure.rs:325-380`).

### The quote

The maker quotes around a blend of the last print and the tick's model
price (`market/tick.rs:1487-1505`):

```math
\hat P_{i,t} = P_{i,t-1}^{\,1 - w_Q}\,\big(P_{i,t}^{\ast}\big)^{w_Q}
```

On pt-v20 $w_Q = 1$, so the maker quotes around the model price itself and
the tape tracks it; the print differs from $P^{\ast}$ by the bid-ask bounce
and the inventory skew. At $w_Q = 0$ (pt-v19) the maker quoted around the
last print moved by the tick's news term.

The full spread, in basis points, depends on market-cap tier, volatility,
the VIX and short interest (`microstructure.rs:197-258`):

```math
\sigma^{\mathrm{bps}}_{i} = b(M_i)\,\big(0.7 + 0.3\,v_k\,\beta_i\big)\Big(1 + \max\Big(0,\ \frac{X_d - 15}{30}\Big)\Big)\,m^{SI}_i
```

$b(M)$ is 3, 5, 10 or 30 basis points for a market cap above USD 50bn, above
10bn, above 1bn, or below. $m^{SI} = 1 + 2 (\mathrm{SIR} - 0.15)$ above 15%
short interest over float, else 1. The maker skews the quote against its
inventory (`market_maker.rs:120-168`):

```math
\eta_i = \hat P_{i,t}\,\frac{\sigma^{\mathrm{bps}}_{i}}{2 \cdot 10^{4}},
\qquad
q_i = \max\big(1,\ \lfloor \bar A_i / 100 \rfloor\big),
\qquad
\lambda_i = \mathrm{clip}\Big(\frac{I_i}{12\,q_i};\ -1,\ 1\Big),
\qquad
m_i = \hat P_{i,t} - \lambda_i\,\eta_i
```

The best bid and ask are $m - \eta$ and $m + \eta$, rounded to the cent and
at least a cent apart. Level $j = 0, \dots, 9$ sits $j$ half-spreads further
out and holds $\max(1, \lfloor Q_0 / (1 + 0.3j) \rfloor)$ shares
(`market_maker.rs:199-238`). The top size $Q_0$ is $q_i$, cut to
$q_i \max(0.15, 1 - \lvert \lambda_i \rvert)$ on the side the maker is already
long or short. With no inventory, the book shows about $5 q_i$ shares a
side, about 5% of a day's volume.

All the spread and ladder constants are chosen, from the reference
implementation. The ten levels are the depth the interface has always drawn.

### Settlement

**Timescale:** every tick, for every listed company. **Draws:** four
uniforms $U_1, \dots, U_4$, whether or not they are used.

Background order flow trades against the book. Its buy probability rises
with the gap between the model price and the quote, which is zero on pt-v20,
and with the crowd's lean (`microstructure.rs:740-749`):

```math
\pi^{\mathrm{buy}}_{i,t} = \mathrm{clip}\Big(0.5 + 40\,\frac{P^{\ast}_{i,t} - \hat P_{i,t}}{\hat P_{i,t}} + 10\,L(s_{i,t}, \mu_{i,d});\ 0.05,\ 0.95\Big)
```

Four market orders of $\max(1, \lfloor V_{i,t}^{\mathrm{vol}}/4 \rfloor)$
shares each, where $V^{\mathrm{vol}}$ is the tick's volume below, walk the
book: order $j$ buys if $U_j < \pi^{\mathrm{buy}}$ and sells otherwise. The
print is the price of the last fill; if nothing fills, it is $P^{\ast}$
(`microstructure.rs:755-822`). When every slice fits in the top level, the
print is the ask with probability $\pi^{\mathrm{buy}}$ and the bid otherwise.
The maker's inventory takes the other side of every fill and never decays
(`microstructure.rs:495-512`).

The print is then clamped to the same ±25% session band as the model price:
$P_{i,t} = \mathrm{clip}(P_{i,t}^{\mathrm{set}};\ 0.75 P_{i,d}^{o},\ 1.25 P_{i,d}^{o})$
(`market/tick.rs:1445-1453`). The overnight gap is not clamped. On pt-v20
each session opens at the last print. Under a night split
(`overnight_market_share`, `overnight_idio_share`, below under Off in pt-v20)
the open prints the model price after the night's draw and any earnings
report, and the session band anchors on that open.

**The closing cross.** On the last tick of the session the print is the
model price, clamped to the same band, and the maker's inventory change
from that tick is dropped (`market/tick.rs:1542-1554`, `market/tick.rs:1611`):

```math
P_{i,389} = \mathrm{clip}\big(P_{i,389}^{\ast};\ 0.75 P_{i,d}^{o},\ 1.25 P_{i,d}^{o}\big)
```

So the close sits at the model price, between the bid and the ask, and a
close-to-close strategy earns no bounce.

| Constant | Value | Kind |
|---|---|---|
| gap pressure | 40 | chosen |
| lean tilt | 10 | chosen |
| slices a tick | 4 | chosen |
| buy probability bounds | 0.05, 0.95 | guard |
| `price_breaker_fraction` | 0.25 | guard |
| `price_hard_cap` | 50,000 | guard |
| `quote_model_weight` $w_Q$ | 1 (0) | derived: a switch; Roll's (1984) spread estimate from the tape was 5 times the quoted spread with $w_Q = 0$ and 1.2 times with it (row C4a), and the lag-1 autocorrelation of 65-minute returns moves from -0.19 to -0.02 |
| `closing_auction` | 1 (0) | derived: a switch; the one-day Lo-MacKinlay profit on large names moves from +13 to -1 basis points a day, against a real -1.7 |

## Agent orders

**Timescale:** an agent acts between steps; its orders fill at once
against its own book, and their effect on prices lands on the next tick.

### The agent's book

An agent trades against a book of its own, built for each order
(`agent_book.rs`). It holds three kinds of liquidity: the maker's ladder, as
above, quoted around the last print (`agent_book.rs:551-563`); latent depth;
and other agents' resting orders, at their own limits, behind the maker at
an equal price. An agent never meets its own orders
(`agent_book.rs:604-612`). The same rule holds when its resting orders are
posted into each tick's settlement book: an order that crosses passes over
its own agent's orders and matches the next order behind them
(`order_book.rs:313-325`, `microstructure.rs:695-712`). Neither order is
cancelled. An agent's bid and offer at one price both rest until the flow or
another agent fills each of them, so `Engine.book` can show an agent's bid
at or above its own offer.

Latent depth makes size pay the square-root law. Beside the ladder, each
side holds a pool whose cumulative size $Q$ is priced at no better than
(`agent_book.rs:430-494`)

```math
p^{\mathrm{ask}}(Q) = p^{\mathrm{touch}}\Big(1 + Y\,\sigma_i\Big(\frac{Q}{\bar A_i}\Big)^{\delta}\Big),
\qquad
\sigma_i = \sqrt{h_{i,d} + \beta_i^{2}\,v_d}
```

with bids mirrored, levels on a geometric grid $Q_k = R \bar A_i 1.25^{-k}$,
rounded to the cent. The average cost of $Q$ shares is
$Y \sigma_i (Q/\bar A_i)^{\delta} / (1 + \delta)$, two thirds of the
marginal price at the square root. An order larger than $R \bar A_i$ is
cut off.

A walk takes the cheapest liquidity first. What it takes is gone for later
orders: consumed depth refills with a half-life of $H_B$ ticks
(`agent_book.rs:506-528`, `agent_book.rs:624-631`),

```math
D_{t+1} = D_t\,2^{-1/H_B}
```

and a fill against the maker moves the maker's inventory at the next
re-quote (`engine.rs:2787-2806`), and at the open everything resets.

A limit order's remainder rests (`engine.rs:3248-3264`). Each tick it is
also posted into the settlement book, where the background flow can fill it
at its limit, as a maker fill (`microstructure.rs:695-738`,
`microstructure.rs:757-800`). The flow walks no further than the last price
the maker quotes in that tick's settlement ladder (`microstructure.rs:677-693`),
which holds only as many levels as the tick's volume needs, two to ten. So
an order resting past that ladder waits until the price moves to it, and a
fill against the flow is always at a price inside the maker's quote. A bar
keeps only each tick's last print, so a resting fill can sit below the day's
low or above its high when a later slice in the same tick traded on the
other side. On 40 names over four sessions, 2% of resting fills did.
`tests/test_order_book_depth.py` checks that a limit past the latent depth
stays unfilled for the rest of the session and that one 5 bp outside the
touch still fills.

A resting order that the maker's re-quote moves through (a bid at or above
the new ask) trades against the re-quote before the tick's flow, at the
maker's prices. Its fills are recorded with `liquidity="taker"` and
`counterparty="mm"`, although the order was resting. The agent gets the
maker's price rather than its own limit, and the fills are taker flow that
pays permanent impact on the next tick, so a standing bid at the ask cannot
take the maker's fresh size every tick for free. In a study of maker
rebates or taker fees, count these fills as taken liquidity.

The cost of size in row C9 is this book read for one immediate order.
`tools/calibration/impact_curve.py` takes every name on 40-name rosters and
both sides, reads the average price of an order of 1% to 100% of a day's
volume off the book without trading, divides the cost by the name's realised
daily volatility, and fits exponent and coefficient by least squares of log
cost on log size. The pooled fit is $0.469\,\sigma (Q/V)^{0.495}$, and the
graded record reads 0.484 and 0.424. One name can fit steeper, because the
maker's ladder sits in front of the latent depth and steepens the middle
sizes: a one-shot buy on one name at seed 7 fits an exponent of 0.60 to 0.65
over 3% to 100% of a day's volume, still inside the C9 band.

In a `World` with several agents, orders placed at the same step execute one
after another in label order, sorted alphabetically, for the whole run
(`counterfactual.py`), so a later label meets a book an earlier one has
already walked. Two identical buyers of 10% of a day's volume paid 19.5 to
30.7 bp apart on seeds 1 to 10, the later label paying more, so label order
confounds a study of different agents in one book unless the labels are
rotated across runs. A seeded per-step shuffle would need random state in
forks, checkpoints and manifests, and does not exist yet.

### The path of a fill to the price

All agents' taker fills since the last tick are applied once, on the next
open tick (`engine.rs:2521-2545`). Each agent's net fill leaves a linear
permanent impact on $s$, Almgren's law (`engine.rs:2809-2814`):

```math
\Delta s_i = \sum_{a} \gamma\,\sigma_i\,\frac{b_{a,i} - x_{a,i}}{\bar A_i}
```

with $b$ shares bought and $x$ shares sold by agent $a$. It is booked to the
order-flow slot. A resting order's maker fill carries none. Buying 10% of a
day's volume at a daily sigma of 1.56% moves $s$ by about 4.9 basis
points. The impact lives in $s$, so it decays on the mispricing's
half-life rather than lasting for good. The model's own flow never meets
consumed or latent depth, so an agent's temporary impact reaches the tape
only through the maker's inventory.

### Arrival order in a cohort

Several agents in one `World` are asked together and then execute one after
another against the one book, so on a live book the second meets what the
first left: it pays for the levels the first took and stands behind it in
the queue at an equal price. Which one arrives first is
`Engine.arrival_order` (`engine.rs`, `rng.rs::arrival_priority`). At
`book_arrival_shuffle` 0, which every preset before pt-v21 carries, it is
sorted label
order on every step, so a label is a standing priority: on pt-v20 the later
label of two identical buyers of 10% of a name's daily volume pays about
23 bp more on every held-out seed. At 1 it is the labels sorted by

```math
p_\ell = m\big(m(m(m(\sigma(\text{seed}, 11)) \oplus d) \oplus k) \oplus h(\ell)\big)
```

with $m$ SplitMix64's finalizer, $\sigma$ the stream derivation's input,
$d$ the world's day, $k$ the step within the day and $h$ FNV-1a of the
label. Each label is first equally often and the order is independent from
step to step, as agent-based toolkits reshuffle their activation order
(Axtell 2001), and exchanges rank equal prices by time and never by name
(Nasdaq Rule 4757). A label's priority does not depend on which others are
present, so an externality arm that removes one agent keeps the others'
order. It takes no draw and holds no state.

With $\gamma \ne 0$ agents' flow no longer enters the order-flow law $O$,
which now carries only flow a caller supplies directly:

```math
O_{i,t} = \frac{x^{+} - x^{-}}{x^{+} + x^{-}}\,\max\Big(0.2,\ 0.15\min\Big(\frac{x^{+} + x^{-}}{\max(\bar A_i/390,\ 100)},\ 10\Big)\Big)\cdot\frac{c_{OF}\,f_I}{\max\big(\max(\bar A_i,\ 0.005\,S_i)/390,\ 100\big)}
```

(`calculate_live_factors` and `order_imbalance` in `market/factors.rs`).

The first factor is already participation, flow over the name's minute
volume, and the last divides by a minute volume again. So at equal
participation $O$ falls as depth, and at a fixed share count as depth
squared. With `order_flow_depth_law` at 1 the last denominator is the fixed
$10^6/390$ instead, the minute volume the first factor assumes for a name
that reports no volume. Equal participation is then an equal move on every
name above the 100-share minute floor, and a name trading a million shares a
day is charged what it is charged at 0.

### Injected order flow on pt-v21

pt-v21 sets `order_flow_depth_law` to 1, `order_flow_impact_law` to 1 and
$c_{OF}$ to 800. With the impact law at 1 the participation multiplier is
$0.15\,p$ for a tick's participation $p$ up to 10, with no floor, and
$1.5\sqrt{p/10}$ above 10, where $p$ is the tick's gross flow over the
name's average minute volume. A programme spread evenly over a session
has $p = Q/\bar A_i$ on every tick, so any programme below ten days'
volume stays in the linear part, and its impact grows in proportion to its
size measured in the day's volume.

Measured with `tradefloor.flow_impact` on a name trading 30,318 shares a
day with a daily standard deviation of 2.03 per cent, one session, seed
42, the flow bought at an even rate over the session, impact read on the
close against the same run without it:

| $Q/\bar A_i$ | impact (bp) | impact in daily standard deviations |
|---|---|---|
| 0.01 | 1.3 | 0.006 |
| 0.05 | 6.3 | 0.031 |
| 0.10 | 12.6 | 0.062 |
| 0.25 | 31.6 | 0.156 |
| 1.00 | 127.0 | 0.63 |

Impact is linear in size across this range. Published metaorder studies
fit $Y\sigma\sqrt{Q/V}$ with $Y$ between 0.5 and 1 (Tóth et al. 2011);
read that way these programmes give $Y$ of 0.06 at 1 per cent of the day's
volume, 0.20 at 10 per cent and 0.63 at the whole day's volume, so at
ordinary sizes injected flow costs less than those studies find.

The day's volume scales the effect of a net imbalance. The same net 1,000
shares a tick on this name moves the close 1,763 bp when it is the whole
flow, 380 bp inside 10,000 shares a tick of gross flow, 116 bp inside
100,000 and 36 bp inside a million: above the knee the multiplier reads
the gross flow, and the imbalance is its net share. A single tick of five
times a name's daily volume, on a name trading a million shares a day,
moves the price 60 bp.

### The metaorder memory

Without it, a half-day order's displacement of the tape is linear in its
size and does not decay within the day: on pt-v20 its peak is about
$0.42 f^{1.04}\sigma$ for $f = Q/\bar A_i$, and 99% of it is still there at
the close (`tools/calibration/metaorder_curve.py`). The square-root law is
concave and about a third of the peak is gone by the close (Bucci,
Benzaquen, Lillo and Bouchaud 2019). With $Y_M > 0$ each name keeps a
signed memory of all agents' net taker flow against the house (the maker
and the latent depth) in fractions of $\bar A_i$, a fast part and a slow
part, decaying on open ticks only:

```math
M^{f}_{t+1} = M^{f}_t\,2^{-1/H_1} + \frac{b - x}{\bar A_i},\qquad
M^{s}_{t+1} = M^{s}_t\,2^{-1/H_2} + \frac{b - x}{\bar A_i},\qquad
M = (1 - w) M^{f} + w M^{s}
```

and $s_i$ carries $D = \mathrm{sign}(M)\,Y_M\,\sigma_i\,h(|M|)$, with
$h(m) = m^{\delta}$ at or above $m^*$ and $m\,{m^*}^{\delta - 1}$ below it,
booked through the order-flow slot each tick net of $s$'s own reversion.
The print follows $s$, so $D$ is on the tape and in the closing cross.
On the side the memory leans the latent depth continues from the memory's
point on its own curve, $x_M = (|D|/(Y\sigma_i))^{1/\delta}$; against the
lean, no house share is priced better than the memory's own path,
$P\,e^{D(M - q) - D}$ for a sell; with $M = 0$ the book is the book above.
This is the book of Alfonsi, Fruth and Schied (2010) with a book linear in
distance. The linear $\gamma$ stays as the long-lived part.
(`agent_book.rs`, `MemoryBound` and `append_latent_depth`;
`engine.rs`, `plan_memory`.)

With the memory on, a fill between two agents (one lifts the other's
resting order) is not flow to the market: it feeds neither the memory nor
$\gamma$ (nor the imbalance law). It took no liquidity from the house,
the resting order had just added what it took, and the pair's cash nets
to zero, so counting the taker's side would let one agent rest an ask a
cent inside the spread and another lift it to walk the tape at no cost.
Off, every share an agent takes is flow, as before.

On a name quoted a cent wide that ask cannot go inside the spread: it joins
the maker's queue at the touch, the other agent's buy takes the maker's
size (flow against the house, which feeds $M$), and the ask fills later,
crossed by the maker's re-quote at the maker's higher bid or filled by the
market's own flow, which $M$ does not count. The pair ends flat, the price
is still displaced by the buys, and a third agent sells a holding into it:
row G-rt's wash paid +0.51 bp on R16A's thirteenth grade and up to +18 bp
on held-out trip seeds. Two switches close it, both on in pt-v21 at 1 and
off on every earlier preset.
With `book_cross_at_limit` a resting order the book leaves crossed during
the session trades at its own limit, the improvement going to the arriving
re-quote as price-time priority gives it (the open is unchanged: an order
the night's gap went through fills at the opening ladder's prices, as an
auction fills it). With `impact_memory_refill` a resting order filled
against the memory's lean, by the market's flow or by a crossing, takes its
size off $M$ uncapped and never past zero: in the volume-recovery book the
displacement is the consumed depth, and an order resting on the consumed
side is new depth there (Obizhaeva and Wang 2013; Alfonsi, Fruth and Schied
2010); Eisler, Bouchaud and Kockelkoren (2012) measure a limit order's
impact with the sign opposite to a market order's on its side. A crossed
order's shares beyond zero stay taker flow, and every crossed share still
pays $\gamma$. Taker orders are unchanged, so no market-order statistic
moves. (`engine.rs`, `refill_memory`, `settle_book` and `meet_book`.)

Measured on R16A with both on, `metaorder_curve.py trips` on held-out trip
seeds (2531, 2532, 2534; 22531, 22532, 22534; 42501-42600): the most
profitable round trip on a seed is -1.46 bp on average (sd 0.18, worst
-0.86) against -1.20 (sd 1.94, worst +18.33, three seeds above zero) with
both off; on the cent-wide rows the wash costs 0.12 bp more on average
than its third agent's round trip alone (61 rows), where with both off it
paid 0.94 bp more (68 rows), and the wash legs alone gain nothing. Every other strategy, Q1-Q9, C9, MARK
and AO1-AO4 are unchanged to the bit.

Measured with `metaorder_curve.py` on held-out seeds 2401-2430 (12 names,
box sqfix2), the arm $Y_M = 0.65$, $H_1 = 12$, $H_2 = 780$, $w = 0.1$,
$m^* = 0.001$ with $\gamma = 0.15$ (the memory carries the transient part
Almgren et al.'s 0.314 was fitted beside, so the permanent part is halved
with it on): the half-day print peak is $0.44 f^{0.66}\sigma$, 0.71 of a
day TWAP's displacement is reached halfway, 0.66 of the peak is left at the
close, 0.57 at the next close and 0.33 five closes later, and a day TWAP at
10% of volume costs $0.116\sigma$, 0.82 of a block's cost (0.83 at 3%;
0.53 with the memory off). Below 3% a block fits inside the maker's ladder
and pays only the half-spread, so a sliced order, which pays the memory,
costs 1.1 to 1.3 times as much there.

### The latent depth nested behind the ladder

The latent pool above sits beside the maker's ladder: its $Q$-th share is
priced on the law as if the ladder were not there, so the depth within a
distance of the touch is the ladder's plus the law's. The ladder holds 0.3
to 1% of $\bar A_i$ at its first level and 2.6 to 5% over ten, so a block of
3% of daily volume fills mostly at the ladder's prices and the law's own
front at once. Every share, in a block or a slice, pays the half-spread
(0.044 $\sigma$ at the median name), which is then half of such a block's
cost, and a day TWAP at 3% costs 0.83 of it with the memory on, above the
0.5 to 0.8 of Almgren et al. (2005) and Bacry et al. (2015). With
`book_depth_nesting` $k > 0$ the pool's cumulative size through a level is

```math
Q^{\mathrm{nested}}(p) = \max\big(Q^{\mathrm{placed}},\ Q(p) - k\,L(p)\big)
```

with $Q(p)$ the law's cumulative size at the level's price and $L(p)$ the
ladder's shares at that price or better. At $k = 1$ the depth within any
distance is the larger of the ladder's and the law's: the ladder is the
displayed front of the latent book (Tóth et al. 2011), not a second book
beside it. A slice inside the ladder's first level pays exactly what it
did; a block past the ladder pays the law. (`agent_book.rs`,
`append_latent_depth`.)

Measured on the r14 screen's closest candidate N4 with $k = 1$ (boxes
r15imp1 and r15imp2, held-out seeds only; `metaorder_curve.py` on
2501-2530 x 12 names): a day TWAP costs 0.68 of a block at 3% of daily
volume (0.83 at $k = 0$) and 0.64 at 10% (0.79); Q1-Q7 do not move
(the half-day print exponent 0.645, the peak at 10% 0.377); the cost of
size (row C9) fits $0.469\,\sigma (Q/V)^{0.456}$ (0.436 and 0.460 at
$k = 0$); the best round trip, wash included, loses 0.91 bp; every other
registered and proposed row reads as at $k = 0$, bar C4b (-0.1 points
against -0.0), R7b (-1.00 against -0.98), AO2 to AO4 (22.9 bp, 18 of 30,
+0.03). At $k = 0.5$ the day TWAP costs 0.75 and 0.70 of a block.

| Symbol | Dial | pt-v20 (pt-v19) | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $Y$ | `book_depth_coefficient` | 0.75 (0, off) | same | measured | the cost of size fitted as 0.469 $\sigma (Q/V)^{0.495}$ (tools/calibration/impact_curve.py) inside the 0.33 to 0.67 band of Tóth et al. (2011); row C9 reads exponent 0.484 and coefficient 0.424 on pt-v20, and on pt-v21 row C9 reads exponent 0.515 and coefficient 0.438, for one immediate order of 1% to 100% of a day's volume |
| $\delta$ | `book_depth_exponent` | 0.5 | same | derived | the square-root law (Tóth et al. 2011) |
| $R$ | `book_depth_reach` | 1.0 | same | derived | the latent book reaches one day's volume |
| $k$ | `book_depth_nesting` | 0 (beside) | 1 | for a new registration | the share of the maker's ladder the latent curve counts as its own front; 1 makes the depth the larger of ladder and law (Tóth et al. 2011) |
| | `book_shared` | 1 (0) | same | derived | a switch: agents consume one book |
| $H_B$ | `book_refill_half_life` | 27 ticks (0) | same | measured | refill after a 10% of volume order: 23.1 bp at once, 9.7 after 30 ticks, 1.6 after 130; $39 \ln 2$ at a minute's share of volume (Obizhaeva and Wang 2013) |
| | `book_resting` | 1 (0) | same | derived | a switch: limit orders rest with queue priority |
| | `book_arrival_shuffle` | 0 | 1 | out of scope | a switch: a cohort's arrival order at the book is a seeded shuffle, fresh every step; 0 is label order |
| $\gamma$ | `fill_impact_coefficient` | 0.314 (0) | 0.15 | derived | the permanent coefficient of Almgren, Thum, Hauptmann and Li (2005); linear, so no round trip profits (Huberman and Stanzl 2004) |
| $Y_M$ | `impact_memory_coefficient` | 0 (off) | 0.65 | for a new registration | the square-root law on the tape for agents' metaorders; at most $Y$; Tóth et al. (2011), Zarinelli et al. (2015), Bucci et al. (2019) |
| $H_1$ | `impact_memory_half_life` | 0 | 12 | for a new registration | the memory's fast half-life in open ticks; required with $Y_M$ |
| $H_2$ | `impact_memory_slow_half_life` | 0 (none) | 780 | for a new registration | the slow part; 0.3 to 0.4 of the peak remains after weeks (Bucci et al. 2019) |
| $w$ | `impact_memory_slow_weight` | 0 | 0.1 | for a new registration | the slow part's weight; refused without $H_2$ |
| | `impact_memory_refill` | 0 (off) | 1 | for a new registration | a switch: a resting order filled against the memory's lean takes its size off $M$, never past zero (Obizhaeva and Wang 2013; Alfonsi, Fruth and Schied 2010; Eisler, Bouchaud and Kockelkoren 2012) |
| | `book_cross_at_limit` | 0 (off) | 1 | for a new registration | a switch: a resting order crossed during the session trades at its own limit (price-time priority, Nasdaq Rule 4757) |
| $m^*$ | `impact_memory_crossover` | 0 (pure power) | 0.001 | for a new registration | a size: ANcerno impact is about linear below a volume fraction of about $10^{-3}$ (Zarinelli et al. 2015; Bucci, Mastromatteo et al. 2018; as reported in Bucci et al., PRL 122, 108302, 2019, whose own crossover is in the participation rate) |
| $c_{OF}$ | `order_flow_coefficient` | 50 | 800 | chosen | reference implementation |
| | `order_flow_depth_law` | 0 | 1 | derived | a switch: 1 divides by depth once, restated at a million shares a day (Cont, Kukanov and Stoikov 2014) |
| $f_I$ | `informed_flow_fraction` | 0.35 | same | chosen | the permanent share of impact; published decompositions of 0.3 to 0.5, none named |

### Cash and borrowing

An agent's portfolio pays no commission and no fee to borrow shares for a
short. Once a day, before the close, `Portfolio.accrue` books
`cash * r_p / 252` at the policy rate $r^{p}$ the market publishes that
day. A negative balance, which is borrowing, is charged it by default in
`tf.evaluate`, `tf.rank` and `World` (`margin_interest=True`). The policy
rate is below any broker's margin rate, so a levered strategy's financing
cost is a floor. A positive balance earns it only with `cash_interest=True`,
which is off by default. `margin_interest=False` makes borrowing free, as
it was in every run before 0.8.5. The charge changes cash, net worth and
scores, and never a price.

## The agent's observation

An agent sees the market through a read-only view (`sandbox.py`). The
harness loops in `harness.py`, `counterfactual.py` and `tca.py` hand
`act(obs)` the following:

- `obs.prices`, `obs.tickers`, `obs.avg_volume(t)` and `obs.book(t)`, the last prints, the roster, $\bar A_i$ and a copy of the book at the step's start;
- `obs.engine`, a `MarketView`: the columns price, previous close, previous tick price, open, high, low, volume, $\bar A_i$, market cap, last daily return, $\beta_i$, short interest and float; `bars` of the days recorded so far, which a World run with `record=True` has and `tf.evaluate` does not; the published macro fields (an allowlist, `PUBLISHED_MACRO`, in which `cycle` is the phase as published); the curve; and which names and sectors have news today, without its size;
- `obs.portfolio`, a read-only view of the agent's own cash, positions and fills.

Nothing in the observation carries $s_i$, its momentum $\mu_i$, the maker's
inventory, the GARCH variance $h_{i,d}$, the fundamentals, the attribution,
the news impact, the model's dials, the generator state or the economy's
own state: the true phase, the months in it, the phase's GDP target, the
recession probability, the earnings cycle and its anticipated offset. The
gym environment's `env.engine` and `env.portfolio` are the same views. An agent that declares
`privileged = True` also receives `obs.hidden`, a read-only view of all of
those, and its scorecard records `uses_hidden_state`.
`trusted_agents=True` hands every agent the live engine instead, and every
scorecard records `trusted`.

Around each call into agent code the harness compares `Engine.state_hash`,
the fundamentals, the recording counters and each portfolio's state. A
difference marks the scorecard `tampered`. So does a sandboxed agent's call
to `fork`, `state_snapshot` or `restore_state`, which `Engine.copy_count`
counts, because a copy run ahead is look-ahead that changes no state. A
second engine built from the `seed` and `universe` that the harness's frames
hold, read through `sys._getframe`, is not a copy and is not caught. The
comparison only reads, so no digest depends on it.

## The Oracle baseline

`baselines.Oracle` is the reference that reads state no trader can see. Its
rule is picked from the preset's own dials, never from the preset's name. It
reads that state through `obs.hidden`, the capability its `privileged = True`
declares, and never through the live engine.

- **Cross-sectional rule** (every preset through pt-v19: `fair_value_news_share`, `fair_value_market_share` and `earnings_cycle_depth` all 0.0). Every stock-specific move is mispricing that reverts, so the spread of $s$ across names is the edge. At every step it goes long the `top_k` lowest $s$ and short the `top_k` highest, equal weight, dollar-neutral.
- **Expected-return rule** (any of those dials off zero, as on pt-v20). The cross-section of $s$ is small there: its spread falls from 0.30 to 0.015 and its rank IC against the next day's return from -0.44 to -0.03, so the old rule loses money on 3 of 8 seeds. Once a day, at the open, it forms each equity's expected log return over the session.

The expected return under the second rule has two parts: the mispricing's
reversion and herding, $(\phi_\tau^{390} - 1)s_i + \theta\mu_i$, which carry
the market-wide transient mispricing and any residual, plus the fair value's
drift, which is nominal output growth, the buyback yield and the earnings
cycle's pull toward its phase's level. The residual across names is traded
as the cross-sectional rule trades $s$. The common part is a net position
spread over every equity, long or short by its sign. The gross splits
between the two in proportion to what each earns per unit of gross.

Before pt-v20's graded arm, over 30 days on rosters `Universe.random(20,
seed=3, 42, 11)` at sim seeds 0-3, the Oracle was positive on 12 of 12
markets and ahead of every price-only reference agent on 11 of 12. Its edge
then was the market's opening mispricing (`opening_market_sigma` 0.10),
which reverts.

On the graded arm it trades close to no edge. The market's plain shocks move fair
value for good (`fair_value_market_share` 1.0, `fair_value_market_linear` 1)
and the opening mispricing is 0.001, so little that hidden state knows
predicts a return. The index's next-day return correlates with the rule's
predicted common return at 0.11, and the cross-sectional rank IC is 0.014.
The rule is net long most days and its P&L takes the sign of the market's
month:
- positive on 10 of 14 markets on those rosters (sim seeds 0-3, 0-3 and 0-5), and on 30 of 48 over sim seeds 0-15;
- every loss is a month buy-and-hold lost more; on sim seed 3 the index fell about 11 per cent in log terms, 10 points of it in the names' permanent fair-value offsets, with the VIX below the discount's knee and the earnings cycle unmoved;
- adding the terms the rule leaves out (the crowd's lean on $s$, the anticipated earnings' drift, the volatility discount's approach to its target) moves the count to 26-30 of 48, with the mean P&L still near zero;
- at `opening_market_sigma` 0.10 the same seed 3 pays it +152,102 against buy-and-hold's -175,280.

So the Oracle is measured as a ceiling on pt-v19 (`tests/test_baselines.py`,
`CEILING_PRESET`). On pt-v20 it stays in the reference set as a reference
agent, not a ceiling, and the library reports no capture ratio there. A
fraction of the Oracle's P&L would measure the market's month, not the
agent.

`baselines.ORACLE_NOT_A_CEILING` names the presets this applies to, each
with the reason a result gives. It holds pt-v20 alone. The check reads a
scorecard's `model_fingerprint`, so a custom model (`custom-XXXXXXXX`) keeps
the ratio whatever preset it was built from. Where a preset is named:
- `capture_ratio` returns an empty mapping, whatever the Oracle earned, with a warning that gives the reason, and `capture_withheld` returns the reason as text;
- `versus_buy_and_hold` gives each agent's P&L less buy-and-hold's in the same market, the comparison to quote;
- `rank` sets `Ranking.capture_withheld`, counts no seed as unmeasurable, leaves every capture `None` and out of `as_dict()`, and sorts the table on each agent's mean P&L over buy-and-hold's (`mean_excess_pnl`, with `seeds_ahead`);
- the MCP tools `evaluate_strategies` and `rank_strategies` send no capture field. They send the buy-and-hold comparison and the reason in its place.

On pt-v19 and every earlier preset each of these reports the capture ratio
as before.

## Volume

**Timescale:** tick volume every tick; two persistent states once a day.

Tick volume is a product of multipliers on a minute's share of average
daily volume (`market/tick.rs:1313-1411`):

```math
V^{\mathrm{vol}}_{i,t} = \Big\lfloor \frac{\bar A_i}{390}\,M^{\mathrm{var}}_d\,M^{\mathrm{pers}}_d\,M^{h}_{i,d}\,\Psi_{i,t}\,c^{\mathrm{vol}}(\tau_t) \Big\rfloor
```

```math
M^{\mathrm{var}}_d = \mathrm{clip}\Big(1 + g_V\Big(\frac{v_d}{\bar\sigma_M^{2}} - 1\Big);\ 0.25,\ 4\Big),
\quad
M^{\mathrm{pers}}_d = \mathrm{clip}(e^{\vartheta_d};\ 0.25,\ 4),
\quad
M^{h}_{i,d} = \mathrm{clip}\Big(1 + g_h\Big(\frac{h_{i,d}}{\sigma_k^{2}} - 1\Big);\ 0.25,\ 4\Big)
```

```math
\Psi_{i,t} = f_0 + r_V \min\Big(100\,\frac{\lvert P^{\ast}_{i,t} - P^{o}_{i,d} \rvert}{P^{o}_{i,d}},\ c_V\Big) + n_V\,U_{i,t},
\qquad
c^{\mathrm{vol}}(\tau) = 0.4 + 2.5\,(1 - \tau)^{3} + 2\,\tau^{2.5}
```

$c^{\mathrm{vol}}(\tau)$ is the intraday U-shape: 2.9 at the open, 1.07 at its lowest
around 12:50, 2.4 at the close. Two more multipliers, one for closed-market
ticks and one for news a caller supplies, are 1 in a normal run.
$\Psi$ raises volume with the day's move so far, measured on the model price. The persistent volume state is an AR(1)
stepped at each close (`engine.rs:4805-4817`):

```math
\vartheta_{d+1} = \rho_V\,\vartheta_d + \sigma_V\,Z
```

Average daily volume $\bar A_i$ is fixed for the run. Tick volume also sets
the size of the settlement slices, so a volume dial is also a price dial.

| Symbol | Dial | pt-v20 | pt-v21 | Kind | Source |
|---|---|---|---|---|---|
| $g_V$ | `volume_variance_gain` | 0.02840 | same | fitted | search |
| $\rho_V$ | `volume_persistence` | 0.7 | same | fitted | a 30-seed sweep against `volume_change_acf1` and `volume_abs_return_corr` |
| $\sigma_V$ | `volume_innovation_sigma` | 0.21 | same | fitted | same sweep |
| $g_h$ | `volume_idio_variance_gain` | 0.2 | 0.65 | measured | 120-seed paired measurement on `volume_change_acf1`; flat from 0.2 to 0.3 |
| $f_0$ | `volume_move_floor` | 0.6 | same | chosen | reference implementation |
| $r_V$ | `volume_move_response` | 0.6 (1.0) | same | chosen | pt-v1's value. With less transient market noise, volume tracked a company's own move too tightly: the two-year panel's `volume_abs_return_corr` read 0.639 against a ceiling of 0.63 at 1.0 and 0.618 at 0.8 (box ptv20g2); pt-v20 takes pt-v1's 0.6, where the graded arm read 0.508 at one year and 0.561 at two on the bars before 0.8.5's fix, and reads 0.596 and 0.627 on the fixed bars (`presets/pt-v20.json`) |
| $c_V$ | `volume_move_cap` | 12 | same | fitted | a sweep found 8, 12 and 20 alike |
| $n_V$ | `volume_move_noise` | 0.2 | same | chosen | reference implementation |

The volume rows these dials were fitted or chosen against,
`volume_abs_return_corr` and `volume_change_acf1`, are read off day bars.
Before 0.8.5 a day bar's volume was the sum of the day's running volume
totals, about two hundred times the day's volume and weighted toward the
open, and every reading above was taken on those bars. 0.8.5 fixes the bar
and leaves the dials as they were. On the held roster
(`Universe.random(40, seed=111)`, seeds 101 to 130) pt-v20 now reads 0.596
and -0.268 at one year, against 0.508 and -0.254 before, and 0.627 and
-0.261 at two years, against 0.561 and -0.241. Both rows stay inside their
ruled bands at both horizons, and the two-year `volume_abs_return_corr` is
0.003 under its ceiling of 0.63. These are the readings `envelope.CERTIFIED`,
`envelope.MEASURED_504` and the pt-v20 record carry, re-measured on the
fixed bars on 2026-10-01.

## Bonds

A roster can include three bond indices: `UST2Y`, `UST10Y` and `IGCORP`
(`rates.rs:216-250`), with `Universe.random(..., bonds=True)`. They are
simulated constant-maturity indices, priced off the model's curve; they
read the curve and write nothing back to it.

**Timescale:** each index reprices at the first open after its yield
changes (`rates.rs:512-557`). UST2Y reads $y^{2}$, UST10Y reads $y^{10}$, and
IGCORP reads $y^{10}$ plus a spread re-marked to $y^{c} - y^{10}$ whenever
the corporate yield changes, which on pt-v20 is almost every session
(`rates.rs:266-281`, `rates.rs:503-517`). With the yield change $\Delta y$
as a fraction, the level moves by carry, duration and convexity
(`rates.rs:295-314`):

```math
L \leftarrow L\,\Big(1 + \frac{y^{\mathrm{prev}}}{252} - D\,\Delta y' + \tfrac12\,C\,(\Delta y')^{2}\Big),
\qquad \Delta y' = \min\Big(\Delta y,\ \frac{D}{C}\Big)
```

The carry is added once a session. The cap stops the quadratic from
pricing a large rise as a gain.

Each close's curve therefore reaches the indices one session after it
reaches the equities: an index's close-to-close return is the formula on
the previous close's move (to 0.000 bp on pt-v20; 4.7, 33.6 and 34.9 bp off
the same close's), so corr(the equity index on day $d$, IGCORP on day $d$)
is +0.002 and with day $d+1$ +0.490. Two dials, both on in pt-v21 at 1 and
0.0 on every earlier preset (thirteenth registration, r13 audit), change
that. `rate_close_remark`
re-marks every index to the curve the close's macro step publishes at that
close, and to a pin's curve when it is written (`RateBook::remark_now`); the
next open then adds the night's carry alone, so the return is the formula on
the same close's move. `rate_intraday_live` (which needs it) prints each
index during the session at the published yield plus $E[y_{\text{close}}
\mid \text{the session so far}] - E[y_{\text{close}} \mid \text{the open}]$,
the close's own step (`economy::daily::vix_and_yields`) at its means with no
meeting, the rest of the session integrated on the index's conditional
variance; the mark refreshes every five minutes and commits nothing, so the
closes are the re-mark's to the bit.

| Index | Duration $D$ (years) | Convexity $C$ | Quoted spread | Kind | Source |
|---|---|---|---|---|---|
| UST2Y | 1.9 | 4.6 | 0.6 bp | derived | a 2-year par note at 4%; depth from SHY's dollar volume, 2015 to 2025 |
| UST10Y | 8.5 | 84 | 0.6 bp | derived | a 10-year par note at 3.2%; depth from IEF |
| IGCORP | 7.0 | 100 | 0.8 bp | derived | a 40/30/15/15 mix of 3-, 7-, 20- and 30-year par bonds at 5%; depth from LQD |

Each index has its own ten-level maker ladder around its level, widened by
the VIX. An agent trades it with market orders, whose fills apply once on
the next tick; they move the maker's inventory, which decays with a
15-tick half-life, and never the level (`rates.rs:393-431`,
`rates.rs:564-615`). The agent book, limit orders and news do not apply to
the indices. Graded: rows R1 to R4 read the curve the indices price off.

## Inputs for derivatives

Three switches build what futures and options read. Each is 0.0 on every
shipped preset and is left out of the model's digest there. None writes
anything a price reads, so a model with all three on prints the prices the
model prints without them; `tests/test_derivative_foundations.py` checks
this print by print on four seeds of pt-v21.

`index_level_listed` keeps a price index of the roster,
`sum(price * shares_outstanding) / divisor` over the public, solvent names
(`calculate_market_index`). The divisor is set at the first session's open,
so that session opens at 1000. A listing, a delisting, a bankruptcy or a
name taken private resets the divisor at the prices standing when it
happens, so the level stays where it was. An edit through
`Engine::companies_mut` resets nothing. `Engine::index_level` reports the
level on the prices as they stand, the level on the last close's prints,
the divisor and the number of names.

`vix_intraday_live` publishes the VIX within a session. Without it the
published VIX holds at the last close's value until the next close. With
it the live VIX is the projection of the VIX tonight's close will publish,
given the session so far: the close's own step with its draws at their
means, on the session's return so far and on the variances as the close
would leave them, with the rest of the session integrated as the rate
indices' live mark integrates it and the published stress premium projected
beside it. It refreshes at the open and every fifth session minute, and
outside a session it is the published VIX (`Engine::live_vix`). With the
sector and own-variance states and the slow VIX level off, the last
minute's projection is the VIX the close publishes at its means, to the
bit. pt-v21 runs all three, which the close steps and the projection holds
still.

`forecast_horizon_sessions` makes each close compute a forecast for 1 to
that many sessions ahead (`Engine::forecast`): the published VIX, the
index's and each name's one-session variance, the policy rate and the oil
price. It reads public state only: the VIX, the curve, prices, the
conditional variances, the market's cycle nowcast (the published phase on a
model without one) and the published growth. It does not read the true
phase, the slow VIX level or any draw to come. A pin computes it again on
the pinned state.

The VIX and the variances iterate the model's laws on their expected
inputs one session at a time. The day's market factor is integrated on four
nodes, the belief over the phases moves on the cycle's own exit rates, and
each phase's volatility regime is weighted by it. On 136 pt-v21 histories
of 2,000 sessions (seeds 61001 to 61016, 62001 to 62032, 3001 to 3040 and
7001 to 7048) the VIX state this gives is the state's mean: realised less
forecast was +0.11, +0.11 and +0.14 points at 21, 63 and 126 sessions, under
one standard error, and -0.03 in log VIX, so it sits above the median. The
published VIX is the state times the stress premium, which is a capped hinge
in the stress memory, and the quote at the expected state read +0.18, +0.36
and +0.50 points low, 1.5 to 2.5 standard errors. A history's mean error
follows its own VIX level, which the slow VIX level the forecast cannot see
sets: at 63 sessions -1.6 points on the 21 histories whose VIX averaged
under 16, +5.0 on the 8 that averaged over 24.

The VIX the forecast reports is therefore the quote's expectation over a
spread of the log VIX about the state, whose variance $s_h^2$ grows to
`forecast_vix_dispersion` squared at the half-life
`forecast_vix_dispersion_half_life`. The VIX's errors have a right tail, so
the spread is a skew-normal with skewness `forecast_vix_dispersion_skew`,
on forty nodes, shifted so the VIX's mean under it is the state; at a skew
of 0.0 it is a lognormal. The stress memory at each node moves the same
number of its own standard deviations as the log VIX, since it is an
average of the VIX's log excursions.

The variances the forecast reports run on a second track, where each convex
VIX coupling (the factor's target, the GARCH coupling, the sector sigma and
the jump rate) is taken as its expectation over the same spread, and the
market factor's return memory's multiplier as its expectation over the
memory's own spread. Oil adds the expected inventory push, OPEC decision
and dollar safe-haven drift over the same spreads. The dollar's drift is a
hinge in the VIX above 25.5, so the spread's right tail raises the expected
dollar and lowers oil.

The policy rate takes the shadow of the next meeting, the change
`policy_anticipation` prices, less `forecast_policy_shadow_discount` of it.
Each later meeting repeats `forecast_policy_persistence` of the
shadow-driven change before it and closes `forecast_policy_reversion` of
the gap to `forecast_policy_neutral`. The meeting ladder is discrete, so
these are a projection fitted on held-out histories, where the VIX and oil
are iterated laws. `tools/calibration/forecast_dials.py derive` fits the
VIX's three dials on 40 held-out pt-v21 histories of 2,000 sessions and the
policy path's four on 240, on every close after the first year as RF5 reads
them; it gives 0.30, 31.5, 0.81, 0.36, 0.6, 0.05 and 1.48. A history's
policy rate level lasts its whole run (the histories' mean rates have a
standard deviation of 0.6 points), so a fit on 40 histories pins the
neutral rate to about 0.1. 40 put it at 1.4, and the forecast then read
the rate low by 0.019, 0.038 and 0.072 points at 63, 126 and 252 sessions
on 96 other histories, 2.6 standard errors each.

On 96 pt-v21 histories of 2,000 sessions held out from the fit (seeds
61001 to 61016, 62001 to 62032 and 7001 to 7048), the mean of realised less
forecast over every close after the first year, as `forecast_dials.py
check` reads it, with its standard error across histories:

| Series | 21 sessions | 63 sessions | 126 sessions |
|---|---|---|---|
| Published VIX, points | +0.24 (0.14) | +0.23 (0.20) | +0.29 (0.24) |
| Oil, dollars | +0.13 (0.09) | +0.22 (0.20) | +0.53 (0.33) |
| Policy rate, points | +0.003 (0.003) | +0.012 (0.007) | +0.025 (0.015) |
| Index variance, 1e-6 a session | +8.4 (1.6) | +11.9 (3.5) | +9.5 (4.9) |

The VIX is within two standard errors at each horizon, and on 7001 to 7048
alone it is +0.05, -0.05 and -0.06; seeds 61001 to 62032 hold more
high-VIX histories. The policy rate is within 1.7 standard errors at each
horizon, and +0.048 (0.028) at 252. The index variance reads low by 2 to 5
standard errors at every horizon, as it did before the published VIX was
taken as an expectation.

On 40 names the forecast takes about 6 ms of CPU a close, about 40 per
cent of a session's; the live VIX adds about 17 per cent and the index
under 5.

## Index futures

`futures_index_listed` lists futures on the price index (`index_level_listed`,
which it requires), and `night_session_steps` lets them trade from one close
to the next open. Both are 0.0 on every shipped preset and are left out of
the model's digest there, with their companions `basis_sd` and
`basis_persistence`. A future reads the index, the dividends, the forecast
and the market factor's sigma, and it draws only on its own stream
(`rng::stream::DERIVATIVES`, 14, outside `COUNT`). Nothing a stock price
reads is written, so an untraded run with every phase 1 switch on prints
the stock prices, closes and economy of the run without them, night
included, and a run whose agents trade only futures prints the stocks of a
run nobody trades (`tests/test_index_futures.py`, seeds 3, 17, 101 and 9001
of pt-v21).

The contracts are quarterly. Each expires at the opening print of session
15 of its quarter's last 21-session month, session `63q + 56` counted from
0 (`derivatives::calendar`). The front two are listed, and each rolls six
sessions before its expiry, when `contracts()` marks the next one `front`.
A symbol is the root and the expiry session, `IDX.F0056`
(`derivatives::ContractSymbol`, which also spells options as
`ROOT.Onnnn.Rk.cc`); it names the same contract in a fork, a restore and a
replay. A contract is $250 an index point, on a 0.05 grid.

A future is priced at `F = (S - PV(D)) * exp(r * tau) + b`:

- `S` is the live index within a session, the night's path between
  sessions under `night_session_steps`, and the close's index without it.
- `tau` is the sessions from now to the expiry's open over 252. A session
  uses `k / 390` of its day after `k` ticks, and the night uses none.
- `r` is the mean over those sessions of the policy rate the forecast
  expects to be in force in each (`forecast_horizon_sessions`), or the
  policy rate now without a forecast.
- `D` is each constituent's dividend for every ex-date after the last open
  and no later than the expiry, in index points, discounted at `r` from its
  ex-date: the declared amount within 21 sessions of the ex-date, beyond it
  the dividend rule's declaration on today's state, iterated a quarter at a
  time.
- `b` is the basis, in basis points of the index: an AR(1) with sd
  `basis_sd` and close-to-close persistence `basis_persistence`, stepped on
  every open tick and every night step, plus each contract's mark of
  agents' net flow against the house, `0.15 * sigma * flow / V`, decaying at
  a 30-step half-life. Both are scaled by `min(1, n / 6)`, with `n` the
  sessions to expiry, so a future converges on the index over its roll.

Each close marks every contract at `F` on the close's index. At its expiry
session's open a contract settles in cash on the index of the opening
prints, the level after the open has applied the night and the ex-dates and
before the first tick (the bars' `open`), and `settlements(day)` reports the
value and the reference it was set from. The next contract is listed at the
same open.

Each listed future has an agent-facing book on the equity book's three
layers: the maker's ten levels a side one tick apart, each 0.3 per cent of
the daily volume; latent square-root depth beside them at a coefficient of
0.75 and an exponent of 0.5 out to a day's volume, refilling at a 27-step
half-life; and agents' resting orders. The daily volume is the constituents'
daily dollar volume over a contract's notional, about 200,000 contracts on
`Universe.random(40, seed=111)`. A resting order fills when the moved book
crosses it, at the book's prices. Fills on contracts come back through
`take_fills` after the names' fills, numbered on their own counter. There is
no margin and no `Portfolio` account for futures yet, so a caller keeps a
position's cash itself.

Under `night_session_steps` each close runs the next open on a copy of the
engine. Nothing draws on the OVERNIGHT stream but the open, and nothing
between a close and the next open sets an input to its draws, so the copy
takes the draws the open will take and its opening prints are the open's to
the bit. Its index and each future's fair value on those prints are the
night's targets. `run_night(steps)` walks each future's log fair value to
its target on a Brownian bridge, one normal per step for all of them, with
the night's share of the index's one-session variance
(`overnight_market_share` of it on pt-v21), stepping the basis and filling
resting orders beside it. The last step lands on the open's fair value. The
real open takes its own draws, as it always did. A pin, a listing or
delisting, a change of status, or a fundamentals write from Python between
sessions reruns the copy and moves the path at once by the change in its
targets; a write the copy is not rerun for reaches the futures at the open.
Stocks do not trade at night.

The basis dials are fitted on the model's own histories
(`tools/calibration/basis_dials.py derive`, 24 held-out pt-v21 histories of
21 years, seeds 8101 to 8124) to the real closing basis of ES against its
carry fair value on the S&P 500 from 2020-10-26, when CME's settlement moved
to the 16:00 index close, to 2026-09-09: a residual sd of 3.74 bp and a
lag-1 autocorrelation of 0.42, per window of 252 sessions after a regression
on time to expiry. The fit gives `basis_sd` 3.753 and `basis_persistence`
0.429, which read 3.736 and 0.420 on the fitting histories.
On 24 fresh histories of 21 years (`basis_dials.py measure`, seeds 8201 to
8224, ten night steps):

| Row | Model | Band |
|---|---|---|
| IF1, closing basis sd, bp | 3.72 | 1.21 to 6.27 |
| IF2, its lag-1 autocorrelation | 0.418 | 0.127 to 0.716 |
| NS1, night share of the front future's daily variance | 0.516 | 0.30 to 0.60 |
| NS2, 09:29 against fair value on the opening prints, sd bp | 3.66 | at most 6.27 |
| IF3, settlement less the opening-print index, relative | 0 | at most 1e-12 |

Across the 432 windows of 252 sessions, IF1 runs from 3.41 to 4.07 bp and
IF2 from 0.31 to 0.51 between the 5th and 95th percentiles. Read against
fair value after the session's first tick instead of on the opening prints,
the 09:29 future is 12.4 bp from it, because pt-v21's first tick moves the
index by 11 to 13 bp sd, against about 4 bp at the tenth tick.

On 40 names the futures and their night add under 2 per cent to the time
of a session with the three switches above on.

## Scenarios

A scenario is a file of changes to the economy or the market, applied once
a session, before the open (`python/tradefloor/scenario.py:1162-1201`). It
states its shocks and the knock-on effects it assumes, separately. The model
does not decide what a war or an oil shock does to the economy; the scenario
states it, and the model carries it to prices.

**Timescale:** once a session, before the open. Scenario day $d$ counts
sessions from the start of the run, or from the fork.

### The packaged recession

`recession.yml` is dated on September 2007: day 50 is December 2007, the
NBER peak, and a month is 21 sessions. From 0.8.5 the recession ends and
hands back to the model's own cycle
(`python/tradefloor/scenarios/recession.yml`):

- The cycle is set to contraction on day 50 and held 315 sessions, to March 2009. On day 365 it is set to trough, and on day 428, June 2009, the NBER's trough, to recovery, and then left to the model's own cycle. Left to its own hazards from day 365, the model's cycle kept one seed in 30 in trough for more than 24 months after the onset.
- Growth is held at -2% from day 50 to day 364, then released.
- The VIX is multiplied by 3 for 60 sessions from day 50.
- The corporate yield is 150 bp wider for 378 sessions from day 50, comes back to its day-50 level over the next 252, eases 110 bp more over the 378 after that, and is released to the model's own chain on day 1058. Moody's Baa yield (FRED DBAA) was 6.65% in December 2007, 9.2% at its peak in November 2008, 6.3% in September 2009 and 5.25% in December 2011.
- Every company's earnings are multiplied down to 0.65 of their level by day 301, in two ramps (0.808 over 121 sessions from day 50, 0.805 over 131 from day 171), held there to day 490, September 2009, and restored in two ramps: to 0.96 of their pre-shock level by day 680, June 2010 (1.477 over 189 sessions from day 491), and to 1 by day 932 (1.042 over 252 from day 680). The cut stacks on pt-v20's earnings cycle, which takes about 18% off in a contraction at its depth of 0.2. Together they fall about 47% by day 365. S&P 500 four-quarter operating earnings fell 57%, from 91.47 dollars in Q2 2007 to 39.61 in Q3 2009, and were about 0.92 of the 2007 peak over 2010 and 1.05 over 2011.

The first 120 sessions are the 0.8.5 recalibration's, to within the last
bit, and it measured -44.7% at 120 sessions, paired against the same seed
with no scenario, on the certified roster over seeds 301 to 330. That was
pt-v20 before its graded arm (box ptv20g3). The scenario sets the true phase
and growth. Under [the publication dials](#true-and-published-state) an
observer reads contraction about $L_c$ sessions after day 50 and growth as
quarterly means. The recovery was tuned on the arm's permanent market share
without the volatility feedback (the file's header gives the path), and the
grade measures it on pt-v20 as it ships (rows S1a, S1b and S2 of the
twelfth registration).

A reshaped file, dated on the NBER's phases, is kept as a candidate in
`tools/calibration/scenario_candidates/recession_I.yml` and is not what
ships: the cycle held at peak from day 50 and in contraction from day 71 to
day 364, earnings to 0.93 by day 249 and 0.70 by day 364, the VIX doubled
over 20 sessions from day 250 (Lehman), held 40 and eased to 0.6 of that
level by day 428, and credit moved on the spread over the 10-year
(`macro.corporate_spread`, +70 bp by day 249 and +150 bp by day 289). On
pt-v20 with the r13 and r14 dials and `pinned_vix_feedback` 0.8,
`pinned_vix_variance_share` 0.7 (held-out seeds 201-230), the index wins
back 57% of its fall in the 252 sessions after its low (row S1a; 2009: 62%)
and rises 69.1% in those sessions (S2; 2009: 69%), every seed is out of
contraction within 24 months (S1b), and the day-50 morning moves it -4.5%.
On pt-v20 as graded the candidate reads S1a 0.68, S1b 30 of 30 and S2
+79.8.

### Scenario operations

A **pin** gives a field an exogenous path $x_d = P(d)$
(`python/tradefloor/scenario.py:749-811`):

```math
\text{hold: } P(d) = v,
\qquad
\text{ramp: } P(d) = x_0 + (x_1 - x_0)\,\mathrm{clip}\Big(\frac{d - b}{o};\ 0,\ 1\Big),
\qquad
\text{step: } P(d) = \begin{cases} x_0 & d < a \\ x_1 & d \ge a \end{cases}
```

An **intervention** changes a field relative to its value $\tilde x_a$ on the
day $a$ it fires, by $\mathrm{op} \in \lbrace$ set, add, multiply $\rbrace$
(`python/tradefloor/interventions.py:1298-1306`,
`python/tradefloor/scenario.py:1272-1302`). With
$x^{\dagger} = \mathrm{op}(\tilde x_a, v)$:

```math
\text{impulse: } x_a = x^{\dagger},
\qquad
\text{hold or permanent: } x_d = x^{\dagger},
\qquad
\text{ramp: } x_d = \tilde x_a + (x^{\dagger} - \tilde x_a)\,\frac{d - a + 1}{D}
```

over the active days: $a$ alone for an impulse, $a$ to $a + D - 1$ for a hold
or ramp of length $D$, and every day from $a$ for a permanent change. A
permanent `add` therefore freezes the field at one level; it does not add to
a moving value. On `macro.corporate_yield` that stops credit: under
`rate_shock`, `curve_shock`, `oil_price_spike`, `policy_regime_shift` and
`recession` the corporate yield's daily sd reads 0.00 bp through the write,
against 4.56 bp for the unshocked twin, and the IGCORP index's sd reads 0.
`macro.corporate_spread` writes the spread over the 10-year instead: the
level moves with the 10-year's daily noise and a policy move's
transmission while the spread is held.

**What a pin does to the economy underneath.** A pin writes the value each
morning. The macro step still runs at every close, starting from the pinned
value, and every other field follows from it; the next morning the pin
overwrites the pinned field again (`python_engine.rs:3050-3098`). When the
pin ends, the economy carries on from the last value. Two fixes on
`fix/ptv20-core` for this release (commits `cd15126` and `fe8bcef`) close
a leak the daily corporate yield opened on pt-v20: a pinned corporate yield
now holds through the close, a meeting's re-anchoring included, and a
pinned VIX adds no VIX term to the corporate yield's daily move. Before
them, holding the VIX at 45 walked the yield from 2.81% to 2.42% in five
sessions.

Only the corporate yield holds through the close. On pt-v20 the close's
own step moves every other pinned field (the cycle's hazard roll, the 10-
and 2-year's daily step, a meeting's decision and its re-anchoring of the
curve, the VIX's law), and the next morning's pin writes it back: under
`recession` a held contraction flips to trough at 2 to 3 closes a seed, each
of which re-marks the index about +4% at the close and -4% the next
morning, and a permanent 10-year moves 0.66 to 1.03 pp close to close. A
pinned VIX is published at the open, but the volatility feedback's smoothed
exposure (`fair_value_vix_half_life`) reaches it only through the close's
pull, so the discount lands over the following weeks: a VIX held at x3.5
for 25 sessions moves the paired index 0.00 on the morning it is published
and -21.7% (log) over the next 24 sessions. Three dials, all off on pt-v20,
change this (`macro_pins_hold`, `pinned_vix_feedback`,
`pinned_vix_variance_share`); see [Off in pt-v20](#off-in-pt-v20).

A **pinned spread** (`macro.corporate_spread`,
`pin_macro(corporate_spread=...)`) sets the corporate yield to the 10-year
plus the spread, held within the meeting formula's 0.8% to 6%. The close
holds the spread, the meeting included, and sets the level to the 10-year as
it closed plus the spread. A level pin and a spread pin in one call are
refused; across two calls on one session the later one holds.

### Target channels

On pt-v20 (`python/tradefloor/interventions.py:520-841`):

| Target | Reaches prices through | Delay |
|---|---|---|
| `macro.corporate_yield` | the discount rate in fair value; the IGCORP bond index | same session |
| `macro.corporate_spread` | the same, as a spread over the 10-year, which keeps moving the level | same session |
| `macro.treasury_2y`, `macro.treasury_10y` | the bond indices; the 10-year also moves the corporate yield at the next close | next open |
| `market.earnings` | every company's published earnings and book value, multiplied | next tick |
| `macro.vix` | every variance process, jump rates, the crisis episode, the spread | same session to next |
| `market.liquidity` | average daily volume: book depth, impact, volume | same session |
| `macro.policy_rate` | the 2- and 10-year yields, and through the 10-year the corporate yield | days |
| `macro.inflation` | nominal output, the 10-year term premium, the VIX target | next session on |
| `macro.growth` | nominal output | next session on |
| `macro.cycle` | the earnings cycle's target at the next close; the spread multiplier and the bank | next session on |
| `commodity.oil` | inflation, monthly | a month or more |
| `macro.unemployment` | the bank and the Phillips curve | a meeting or a month |
| `policy.tariff_rate` | inflation, monthly, very weakly | a month or more |
| `macro.qe_pe_boost` | nothing on pt-v20 | |
| `macro.fear_greed` | nothing | |

A **forced VIX** (`vix_sets_variance`) goes further: on a forced close the
market-factor variance is set to its targets, with no shock term
(`market/factor_vol.rs:926-956`):

```math
v^{f}_{d+1} = \mathrm{clip}(\bar v^{f}_d),\quad v^{s}_{d+1} = \mathrm{clip}(\bar v^{s}_d),\quad v_{d+1} = \mathrm{clip}\big((1 - w_s)\,v^{f}_{d+1} + w_s\,v^{s}_{d+1}\big)
```

A pinned VIX below the anchor calms the market: at a VIX of 15 on a roster
whose anchor is 17.9, the fast target is about two thirds of the baseline
variance.

**`market.earnings`** multiplies each company's published earnings, and
scales its book value by the same factor, through `set_fundamentals`
(`python/tradefloor/interventions.py:685-742`). Only `multiply` is
accepted. It stacks on the earnings cycle and moves fair value from the
next tick; when its last window closes the scenario writes the earlier
level back. On pt-v20 a multiply by 0.6 over 60 sessions took the index
down 39.6% on one seed.

**Growth's floor.** A written growth rate may go down to -10%, where every
other rate keeps a floor of -5% (`units.rs:52-92`). US real GDP fell 7.4%
year on year to 2020Q2 and 10.0% annualised in 1958Q1 (FRED GDPC1). The
macro step's own clip was already -10.

Seven scenarios ship: `curve_shock`, `geopolitical_conflict`,
`liquidity_crisis`, `oil_price_spike`, `policy_regime_shift`, `rate_shock`
and `recession`. Two were recalibrated on pt-v20, on the certified roster
over seeds 301 to 330, and are measured again on the graded arm, paired
against the same seed with no scenario (box ptv20g6):

- `recession`, to 2008's path, as [The packaged recession](#the-packaged-recession) sets it out: contraction on day 50 held to March 2009, then trough, recovery on the NBER trough date and the model's own cycle; growth held at -2% to day 364; the VIX times 3 for 60 sessions; credit 150 bp wider, then easing along Baa to 2011; earnings cut to 0.65 by day 301, held to day 490 and restored by day 932. The index falls 28.1% by session 63 and 39.3% by session 120 (30.4% and 44.7% on pt-v20 before its graded arm, box ptv20g3). It wins back 49% of its fall within 252 sessions of the low (row S1a, 2009: 62%) and rises 54.6% in those sessions (row S2, 2009: 69%), and every seed is out of contraction within 24 months (S1b).
- `liquidity_crisis`, to March 2020's speed: book depth times 0.4 and the VIX times 3.5 for 25 sessions, credit +50 bp, earnings times 0.85. The index falls 19.8% in 21 sessions and 14.0% by 63, then recovers (10.0% and 14.8% before the graded arm).
- `curve_shock` is new: the policy rate, both Treasury yields and the corporate yield up 200 bp on day 50. On `Universe.random(20, seed=101, bonds=True)` the 10-year index falls 15.4% on the day and the median stock 3.9% by day 120, measured before the graded arm.

The other four files record effect sizes measured before pt-v20.

Reshaped versions of six of them are kept as candidates in
`tools/calibration/scenario_candidates/` (`*_r21.yml`) and do not ship.
Written in r13, they move credit on `macro.corporate_spread` or as an
impulse the chain carries, since a held level freezes the corporate yield
(daily sd 0.00 bp against 5.3 unshocked), and they ramp
`geopolitical_conflict` and `liquidity_crisis` in.

## Off in pt-v20

These mechanisms exist in the code and are switched off, or cannot act, on
pt-v20. Each dial is 0 unless stated. Earlier presets use some of them.

- **Noise in the earnings cycle** (`earnings_cycle_sigma`): the cycle follows the phase path alone.
- **Overnight move** (`overnight_variance_ratio`): the draws are taken and nothing is applied, so each session opens at the last print.
- **The night as a share of the day** (`overnight_market_share`, `overnight_idio_share`, `overnight_idio_df`): off, so each session opens at the last print and nothing gaps through a stop. On, the day's variance is split, not added to: the open draws the market factor at $\sqrt{w_m}$ and the sector and own draws at $\sqrt{w_i}$ of their daily sigmas (the own draw a unit-variance student t under the df), and every tick draws at $\sqrt{1-w}$. The night joins both GJRs' day innovations and the fair-value level as the session's draws do; its market draw carries the lagged down-wire's variance but none of its mean, and the session's live wire keys on the session's own draws, so the session does not follow the market's gap. The day's return is read from the last close (`previous_close` keeps it), and the open prints the tick's model price with the buyback term's fixed point (`market/tick.rs`, `opening_print`). The real forty (2015-2025) carry 0.39 of a name's variance overnight and 0.46 of the index's.
- **Earnings calendar** (`earnings_surprise_sigma` and `earnings_surprise_df`, `earnings_session_sigma`, `earnings_followthrough_sigma`, `earnings_volume_multiple`, `earnings_cycle_report_share`; needs a night split): off, so no company reports. On, each public company reports once a quarter, on session $63q + o_i + j$, with $o_i$ drawn from the forty real names' offsets into the quarter (EDGAR 8-K Item 2.02, median 18 sessions) and $j$ uniform on $[-3, 3]$, keyed on the seed, the company id and the quarter (`src/earnings.rs`). The surprise $x = s_e \sigma_i t/\mathrm{sd}(t) - (s_e\sigma_i)^2/2$ joins $v$ before the reaction session's opening print, so the open prints it; $\sigma_i$ is the company's non-market draw sigma (own and sector in quadrature). A normal part is walked into $v$ one open minute at a time through the reaction session, and another through the next session, so each session trades its part in with no drift and no first-tick jump, and the reaction session trades a multiple of its volume. `Engine.earnings_calendar()` lists the dates ahead and nothing about a surprise, and the agents' market view (`tradefloor.sandbox.MarketView`) and the framework adapters' payload serve them.
- **Crisis correlation blend** (`crisis_blend_gain`, `crisis_blend_variance_damp`): no extra market loading in a crisis.
- **Forced selling** (`forced_flow_gain` and its four partner dials): no correlated selling above a VIX threshold, so a crowded trade unwinding, or any other correlated deleveraging, cannot be represented on pt-v20.
- **Remembered stress** (`universe_stress_weight`, `universe_stress_decay`, `regime_stress_points`): the crisis spike reads today's VIX only, and the business cycle has no direct path to prices.
- **QE valuation channels** (`qe_pe_gain`, `qe_pe_stock_gain`): QE reaches fair value only through the 10-year yield.
- **Book-value floor on earnings** (`fair_value_book_floor`).
- **Variance cascade and per-name persistence spread** (`garch_cascade_components`, `garch_beta_dispersion`, `garch_omega_sector_scaled`, `garch_innovation_commensurate`).
- **Market-variance extras** (`market_vol_level_sigma`, `market_vol_vix_excursion`, `market_vol_alpha_excursion`, `market_vol_vix_smooth`, `market_burn_in_sessions`).
- **Self-exciting jumps** (`jump_idio_excitation`), and jumps in the market-variance shock (`jump_market_variance_share`).
- **Per-name idiosyncratic variance state** (`idio_vol_alpha`, `idio_vol_beta`, `idio_vol_jump_bump`): no state is kept, so a name's own jump leaves no aftershock. When on, a ratio $s_i$ with a mean of one multiplies the variance of the name's own draw, in the session and overnight, and the VIX identity's idiosyncratic term reads it. At each close $s_i' = \mathrm{clip}\big((1-a-b) + a\,u_i^{2} + b\,s_i + c\,(I_i - \lambda_i);\ f_g,\ C_g\big)$, where $u_i$ is the session's own noise over its expected size, $I_i$ is 1 if the name's own jump landed that session, and $\lambda_i$ is the rate it was drawn at.
- **Smooth size and spread curves** (`size_effect_smoothness`, `spread_size_smoothness`): the step functions above are used.
- **Square-root impact** (`order_flow_impact_law`): the clamped participation law above is used.
- **Depth divided once** (`order_flow_depth_law`): order-flow impact divides by a name's depth twice, as in the formula above.
- **Company volume state** (`volume_idio_persistence`, `volume_idio_sigma`).
- **Down-market idiosyncratic suppression** (`market_idio_down_suppress`) and a beta-dependent idiosyncratic scale (`idio_sigma_beta_exponent`).
- **Macro pins that hold through the close** (`macro_pins_hold`). On, every field pinned on a session holds at its pinned value through that night's close, the meeting included: the cycle's roll is taken and dropped while the phase keeps ageing, the 10- and 2-year's steps are taken and dropped, a pinned policy rate forces a hold after the ladder's draws, and a held 10-year takes its re-anchoring change off the corporate and mortgage rates. Every draw is still taken, so the economy stream does not move. A pin that changes the phase starts the new phase's clock.
- **A pinned VIX priced when it is published** (`pinned_vix_feedback`, a share $w$ in $[0, 1]$; read only with `fair_value_vix_discount` and `fair_value_vix_half_life` set). Above zero, a VIX pin moves the smoothed exposure $w$ of the way to the pinned VIX's own excess over the knee before the pin's re-mark, $e \leftarrow e + w\,(\max(0, \ln(\mathrm{VIX}/K)) - e)$, and the close holds it; the pull resumes on the first session nobody pins. $w = 1$ closes the whole gap (the r13 switch). At $w = 0.8$ the 2008 and 2020 replays' same-day slope of the index's log return on the day's log VIX change, sessions above a VIX of 40, reads -0.335 and -0.341 against the S&P 500's -0.345 and -0.307 (corr -0.69 / -0.86 against -0.85 / -0.78); at 1 it reads -0.42 / -0.44, and with the smoothed exposure alone -0.04 / -0.06. With `macro_pins_hold` and `corporate_yield_daily` also on, and no level or spread pinned that session, the pin charges the corporate yield the close's own VIX term (2 bp a point times the phase's multiplier) on the pin's change, so a pinned rise reaches credit as the fall after its release does.
- **A priced VIX move takes part of the session's market variance** (`pinned_vix_variance_share`, a share $s$ in $[0, 1]$; read only with `pinned_vix_feedback` on). The pin records the discount's change $J = g\,\Delta e$ at a beta of one, summed over the session's pins, and that session's market-factor draws, the tick's and the night's, take

```math
\sigma_d = \sqrt{v_d}\;\sqrt{\max\Big(1 - \frac{J^2}{v_d},\ 1 - s\Big)}
```

  for the state's daily variance $v_d$: a small priced move leaves the day's total at $v_d$, and a large one keeps at least $1 - s$ of the draw. The variance state reads each draw rescaled to $\sqrt{v_d}$, so it evolves as it would have without the scale. Off, a replay that pins the real VIX every session adds the priced move to a full draw: with $w = 1$ the 2008 replay's worst month read 131.8 against the real 84.3 (seed 201). Real: above a VIX of 40 the day's log VIX change explains 0.61 (2020) to 0.72 (2007-09) of the S&P 500's daily variance. The move is carried by the snapshot and both state hashes only while non-zero, and the close clears it.
- **A pinned VIX priced below the knee** (`pinned_vix_calm_knee` $K_c$, `pinned_vix_calm_share` $c$; read only with `pinned_vix_feedback` on). Off, a pin's target is the knee's excess alone, so a scenario that forces the VIX from 12 to 30 moves no price: on R16A, 11 of 30 held-out seeds (201-230) and 15 of 30 on 20201-20230 had a paired morning move of exactly zero under SF1's x2.5 hold, and SF1 read 0.80 / 0.39 on 30 seeds. On, the pin's target is

```math
x = \max\Big(\ln\frac{\mathrm{VIX}}{K},\ c\,\ln\frac{\mathrm{VIX}}{K_c}\Big)
```

  and the close's pull on an unpinned session still reads the knee alone, since a VIX the market itself reached comes with the fall that raised it. $K_c = 17.6$ is the real median VIX (1990-2025); at $c = 0.2$ a pin under the knee moves the index $g\,w\,c = 0.056$ log points per log point of VIX the day it lands, against the S&P 500's 0.05 on one-day spikes of 16 per cent or more from under 20 (1990-2025; -0.110 on every session closing under 40).
- **A held pin priced once** (`pinned_vix_priced_cap`, a switch; read only with `pinned_vix_feedback` on). Off, a VIX held at one pinned level keeps closing the gap: $w$ of it on the day, then $w$ of the rest each pinned session, a fall an agent reading the VIX can sell ahead of, and SF1 reads near $w$ even where the pin prices a large move. On, the step is capped at $\max(e, w\,x)$, so a held pin prices once and holds; a pin below the exposure steps down as before. Screen r17sf1s1 (R16A with $K_c = 17.6$, $c = 0.2$ and the cap): SF1 1.06 / 1.03 on the graded 12 seeds of held-out sets A and B, the driven 2022 P/E per 100 bp of Baa -7.1 / -7.2 (real -5.2), the driven 2020 sessions back to the high 97.5 / 71 (real 126).
- **Published VIX premium** (`vix_stress_premium`, `vix_stress_premium_knee`, `vix_stress_premium_cap`): `macro_fields["vix"]` is the VIX state.
- **The fear memory** (`vix_fear_uptake`, `vix_fear_half_life`): the VIX reverts to the anchor's target with no memory of its own excursions.

- **The Fed put and the Treasury haven** (`fed_put_gain`, `fed_put_threshold`, `fed_put_half_life`, `fed_put_emergency_vix`, `treasury_put_pricing`, `treasury_haven_gain`): the ladder alone sets the policy rate, and the 10-year's term premium does not read the VIX.
- **The stress hold and the priced path** (`fed_stress_hold`, `treasury_path_pricing`, `treasury_path_half_life`, `treasury_policy_damping`): the bank may raise the rate at any meeting the ladder asks, and the curve reads the policy rate as it stands.
- **The put's unanswered fall and the drawdown hold** (`fed_put_carry`, `fed_drawdown_hold`): every meeting restarts the put's clock at zero, and only the stress hold's VIX clock holds a rise.
- **The rally off the low** (`market_vol_cycle_recovery_release`, `market_vol_cycle_recovery_scale`): a contraction and a trough keep their volatility multiplier however far the index has climbed off its low.
- **The anticipated meeting** (`policy_anticipation`, `policy_anticipation_cut_share`): the curve learns a decision on the day it is published.
- **Credit's VIX slope and leverage term** (`corporate_spread_vix_cut`, `corporate_spread_equity_gain`, `corporate_spread_equity_half_life`): the corporate spread is the meeting formula's full VIX slope and does not read the index.
- **The market's fall in the cycle's hazard** (`cycle_equity_hazard`, `cycle_equity_hazard_knee`, `cycle_equity_hazard_opening`): the business cycle does not read the index.
- **The market's prehistory** (`market_prehistory_sessions`): every volatility state opens at the constructor's phase-free baseline.
- **The prehistory's valuation** (`market_prehistory_valuation`): the mispricing, the VIX feedback, the anticipation's drift, the earnings cycle, credit's leverage gap, the Fed put's owed cut and the path's forecast open where the burn-in leaves them.
- **VIX extras** (`vix_anchor_reversion`, `vix_innovation_sigma`, `vix_jump_intensity`, `vix_target_offset`). With `vix_level_identity` = 1, the VIX target no longer reads the business-cycle table, `vix_cycle_amplitude`, `vix_realised_vol_weight` or `market_vol_vix_anchor`, although those dials still carry values.

## pt-v19: reproducing earlier work

pt-v19 was the default in 0.8.0 and 0.8.1. It still runs, and replays
exactly, with `model="pt-v19"`. Every equation above holds for it with the
values below, except where this section gives pt-v19's own form. pt-v19
fails 16 of the 40 rows pt-v20 was graded on, and two are not scored on it
(C9 and D1): B9, C4a, C4b, C5, C6, C7, C8, R1, R4, E1, F1, L1, C10, R7a,
R7b and V1 (`validation/pt-v20/programme/results/ptv20/criteria-g6.txt`).

| Dial | pt-v19 | pt-v20 |
|---|---|---|
| `quote_model_weight` | 0 | 1 |
| `closing_auction` | 0 | 1 |
| `fair_value_news_share` | 0 | 1 |
| `opening_mispricing_sigma` | 0 | 0.016 |
| `opening_market_sigma` | 0 | 0.001 |
| `cascade_gain` | 1 | 0.1 |
| `treasury_10y_noise` | 0.03 | 0.038 |
| `treasury_2y_noise` | 0 | 0.022 |
| `flight_to_quality_gain` | 0.02 | 0.008 |
| `flight_to_quality_day` | 0 | 1 |
| `corporate_yield_daily` | 0 | 1 |
| `book_depth_coefficient` | 0 | 0.75 |
| `book_depth_exponent` | 0 (read as 0.5) | 0.5 |
| `book_depth_reach` | 0 (read as 1) | 1 |
| `book_shared` | 0 | 1 |
| `book_refill_half_life` | 0 | 27 |
| `book_resting` | 0 | 1 |
| `fill_impact_coefficient` | 0 | 0.314 |
| `earnings_cycle_depth` | 0 | 0.2 |
| `earnings_cycle_upside` | 0 | 0.09 |
| `market_factor_sigma` | 0.007593 | 0.006454 |
| `jump_intensity_market` | 0.05658 | 0.02829 |
| `volume_move_response` | 1.0 | 0.6 |
| `buyback_payout_share` | 0.3333 | 0.75 |
| `buyback_yield_cap` | 0 | 0.15 |
| `cycle_publication_lag` | 0 | 252 |
| `earnings_anticipation_half_life` | 0 | 126 |
| `fair_value_market_linear` | 0 | 1 |
| `fair_value_market_share` | 0 | 1 |
| `fair_value_market_vol_cap` | 0 | 1.5 |
| `fair_value_vix_discount` | 0 | 0.35 |
| `fair_value_vix_half_life` | 0 | 5 |
| `fair_value_vix_knee` | 30 | 40 |
| `fear_greed_published_inputs` | 0 | 1 |
| `gdp_publication_lag` | 0 | 21 |
| `macro_publication_repricing` | 0 | 1 |
| `rate_pe_sensitivity` | 1.5 | 3 |
| `unemployment_adjustment_half_life` | 0 | 84 |

Where the equations differ on pt-v19:

- **No fair-value level.** $v \equiv 0$: every shock stays in $s$ and reverts on the mispricing's half-life, and the ten attribution slots sum to $\Delta s$.
- **No earnings cycle.** $\chi \equiv 0$, so $n_d = 1 + \eta (N_d / N_0 - 1)$.
- **The opening.** The whole day-zero premium goes into $s$: $s_{i,0} = \mathrm{clip}(g_i;\ -0.9,\ 0.9)$, with a cross-sectional sd of about 0.33. Every run opens with a drift back toward fair value that lasts months.
- **The quote and the close.** The maker quotes around the last print moved by the tick's news term, $\hat P_{i,t} = P_{i,t-1} e^{N_{i,t}/390}$, and the close is the last minute's print.
- **The 2-year** is the formula at every step: $y^{2} = 0.85 r^{p} + 0.15 y^{10}$.
- **Flight to quality** reads the previous session's closing-minute index return, only when it moved more than 0.5%, at a gain of 0.02. In practice it never fires.
- **The corporate yield** is set only at meetings; between them only the one-way floor $y^{c} \ge y^{10} + 0.8$ moves it, so the discount rate moves in steps.
- **Agents' orders.** An agent's book is the maker's ten levels and nothing else; an order beyond them is cut off, and a fill takes no depth and does not move the maker's inventory. In 0.8.5 its fills reach the market once, on the next tick, through the order-flow law $O$ above. In 0.8.1 the harness held them as order flow for every tick of the next step, 65 times at six steps a day, after the agent had filled at the pre-trade book, so an agent collected its own impact.
- **The squeeze and cascade term** is not scaled, and volume responds 1.0 per 1% a company has moved.
- **Macro data as it happens.** Every publication dial is 0: the phase, GDP growth and unemployment are reported as they are, the fear and greed index reads the true phase and growth, and a rate decision is not re-marked at publication.
- **No market share in fair value.** $\psi_m$ = 0: the market's plain loading, market-wide news and the market jump stay in $s$ and revert. There is no volatility feedback, and fair value reads the earnings cycle's level, not its expected path.
- **The valuation.** $\lambda$ = 1.5, $\kappa$ = 1/3 with no cap on the buyback yield, and the 10-year's noise is 0.03.

pt-v19's values for every other dial are the ones in the tables above.

## Known gaps

These are places where the code does something this document can state but
not defend, or where it could not state the code's behaviour as a clean
equation. They are listed so a reader can judge them. A gap marked *next
preset* needs a change to the simulation, and a shipped preset never
changes, so it waits for a new preset.

**Openings.**

- The neutral rate $r^{\ast}$ = 0.0482 was read off pt-v18's burn-in, which always opened in expansion. The opening corporate yield ranges from 2.5% to 6.7% across seeds. On pt-v20 the stationary opening books the resulting rate term into each company's fair-value level, so no run opens with a drift from it; on pt-v19 it shifts the opening mispricing by -0.04 to +0.03.
- The opening yield also depends on the roster, through the roster-derived VIX anchor acting on the burn-in; the path has not been traced.
- The opening VIX is close to a fixed point of the burn-in, because the market is frozen during it, so every run on a roster opens at nearly the same VIX (unless `market_prehistory_sessions` is set): 17.66 on the certified roster, whatever the seed. One-year statistics therefore describe years that start calm. To start from another state, run the engine forward with `run_days` and fork it; the README's limits table says the same. *Next preset.*
- After the burn-in the macro calendar restarts at day 1, so the first monthly step of a run comes 41 sessions after the last one of the burn-in.
- The opening's no-price-move identity holds because the buyback factor is 1 on day zero; an opening applied later would not be exact.

**Macro.**

- The growth shock on entering a phase fires on the first two sessions of the phase, not one (`economy/daily.rs:742-767`). Whether that is intended is not recorded.
- Unemployment sits on its 2.5% floor for much of an expansion. Its long-run mean is 3.6% against 5.7% in the US.
- The cycle's monthly hazard is turned into a daily probability as $h/21$, an approximation to $1 - (1 - h)^{1/21}$ that runs 4% to 15% high. The opening draw uses the hazard alone, without the ladder, so it is close to stationary but not exactly so.
- Row 6 of the central bank's ladder can never fire, because inflation is capped at 6%. While the emergency condition holds, a meeting is held at every close.
- The QE terms apply once a meeting where the code's comments say once a day. QE has no price effect, so this changes nothing measurable.
- The corporate spread and the earnings anticipation price the TRUE cycle phase, which no published field shows until the lag has run. A phase change between meetings changes the daily VIX term's multiplier at once, so the ratio of the spread's move to 2 bp a VIX point names the new phase, and changes the level only at the next meeting, which re-anchors it by $(1 + 0.02(X - 12))\,\Delta m$: on pt-v20, 111 bp on average at the first meeting after a turn and up to 304, against 2 bp at other meetings, with the spread's daily sd at 8.7 bp against FRED BAA10Y's 3.1. The anticipation's phase term $g_k$ moves by about ±4% at the turn's own close, where the repricing prints it. `cycle_nowcast_accuracy` and `corporate_spread_cycle` (0, off, on every preset) replace the true phase with a filtered belief over it and keep the spread on its formula every session; they are for the thirteenth registration.
- The earnings cycle's pull is fixed at a 60-session half-life and was never searched, so a contraction shorter than about 60 sessions reaches well under half the depth.
- The model's inflation almost never leaves the under-3% regime, so stocks and Treasuries are nearly always in flight to quality.

**Prices.**

- Each session opens at the last print, because `overnight_variance_ratio` is 0 on pt-v20 (see [Off in pt-v20](#off-in-pt-v20)). On a 20-name roster over 80 days the first tick moved with a standard deviation of 0.67% against 2.0% for the whole day, about 11% of the daily variance, and moved more than 1% on 3.6% of days. An agent acting at step 0 sees, and fills at, the previous close. A stop held overnight is safer here than live. *Next preset.*
- Nothing below the 65-minute step is calibrated, and row C4a reads 65-minute returns only. One-minute trade-price returns have a median lag-1 autocorrelation of -0.37 (mega caps -0.26, the smallest names -0.48), one-minute realised variance is 3.9 times the open-to-close variance, the open is only 1.15 times as volatile as midday, and names worth $1tn quote about 4 bp. *Next preset.*
- A jump is written to $s$ at the close and reaches the price at the next session's first ticks. So the index return the VIX reads on the jump's day does not contain it; it contains the previous day's. A jump beyond the ±25% band is partly removed by the breaker.
- The market leg's down-tick tilt is re-centred, but the lagged down-beta multiplies the tilted term, so a small drift of about $-a a_L \beta \sigma/\sqrt{2\pi}$ a tick remains on ticks where the market is down on the trailing window. It has not been measured.
- When the $\pm 0.9$ cap on $s$ binds, the change is not booked to any attribution slot. When the 50,000 price cap binds, $s$ is not re-derived.
- The fair-value level scales fair value inside a tick as if the valuation were homogeneous in earnings and book; the 0.01 floor and the buyback factor break that slightly, and the next tick recomputes.
- `ModelParams`' doc comment says market and sector shocks stay in $s$; the code sends sector noise and sector news to $v$, as this document states.
- The company GJR is fed the whole noise term, market and sector parts included, while its coefficients describe a company's own returns. The floor, not $\omega$, sets the resting level in 8 of 12 sectors.
- The buyback factor is re-evaluated each tick at the current price over all elapsed sessions rather than integrated along the path, and it uses the engine's global session count, so a company listed mid-run is credited with buybacks from before it existed, and no shares are retired.

**Volatility.**

- Volatility clustering is weaker than real at every lag, and the two short-lag rows pass their bands low. pt-v20's certified median `abs_return_acf1` is 0.0282 and `abs_return_acf5` 0.0188, below the lowest of the ten real one-year windows in `tf.facts.REAL_MARKETS_WINDOWS` (0.039 and 0.034; medians 0.1025 and 0.0455). The ruled floors, 0.02 and -0.03, sit below every one of those windows, so "in band" hides the shortfall. The `decay-shape` gap in `tf.envelope.GAPS` has the whole curve. *Next preset.*

**The VIX.**

- With the VIX held at 65 the market is 5.1 times as volatile as with it held at 5, against 6.2 times in real markets (pt-v19: 5.2; pt-v20 before its graded arm: 3.6). The lower market sigma and jump rate take the lever down with them. It is reported beside the grade and does not gate it.
- The index variance $V_d$ leaves out the crisis epicentre's scaling, so during an episode the VIX's read-back is not quite the variance the market realises. It also reads the lagged down-beta's condition once a session, where the market samples it every tick.

**Agents and the book.**

- An agent's permanent impact lives in $s$, so it decays on the mispricing's half-life rather than lasting. *Next preset.*
- The model's own flow never meets depth an agent consumed or the latent depth, so an agent's temporary impact reaches the tape only through the maker's inventory. A one-shot buy of 60% of a day's volume pays 65 bp against the mid, while the public price moves 30 bp, the linear $\gamma \sigma Q/\bar A$, and that move decays with a half-life of about 40 sessions. Volume, depth and the background flow do not respond to agents, so the model cannot produce amplification, predatory trading or a liquidity spiral. *Next preset.*
- An order sliced over time costs far less than the empirical law for such orders. Buying 10% of a day's volume in 36 slices ten minutes apart costs 0.040 of a daily standard deviation on a USD 160bn name, against 0.105 for one block and 0.15 to 0.3 implied by published metaorder studies, because consumed depth refills with a 27-tick half-life and nothing anticipates the order. Row C9 measures one sweep and cannot see this, and a schedule optimiser will overstate the value of trading slowly. *Next preset.*
- On the closing tick, agents' resting-order fills are recorded at book prices while the print is the model price.
- The settlement book keeps 32 orders a side, so a far agent order can be left out of a tick's settlement.
- The maker's inventory never decays; only opposing flow unwinds it.
- Tick volume reads the model price, not the print. Tape volume is the modelled volume, not the traded quantity, and agent trades add none; average daily volume is fixed for the run.

**Scenarios.**

- A permanent change on the corporate yield freezes it: after that, no policy, inflation, VIX or cycle move reaches the discount rate.
- The effect sizes in four of the seven scenario files were measured before pt-v20, and some docstrings describe an older VIX anchor, an older crisis threshold and the switched-off crisis blend. The `macro.treasury_2y` note says the 2-year is recomputed at every close, which is pt-v19's law.
- A pinned change of phase does not get the growth shock a natural phase change gets.
- `World.run` builds a fresh scenario on every call, so a hold or ramp added with `World.apply` loses its anchor between calls. `pin_macro(epicentre=None)` does not clear an epicentre pin.

**Records.** The provenance ledger labels `crisis_epicentre_end_sessions` as
out of scope, but it is read; and it labels `market_beta_down_asym_lag`
as measured, while its own entry says 0.46 is fitted. It has no entry for
two dials pt-v20 moved, `market_factor_sigma` and `jump_intensity_market`,
which this document calls fitted from the grading notes;
`volume_move_response` is back at pt-v1's 0.6 and leaves the ledger, which
asks only about a dial away from pt-v1. It calls `book_refill_half_life` measured where the code derives it.
This document uses the corrected kinds.

## Reproducing a result

The same version, preset, seed, roster and scenario give the same market on
every platform. Each release runs one fixed simulation on five platforms and
stops if any digest differs (`tests/known_answer.json`). The engine uses its
own `exp`, `log`, `pow`, `sin` and `cos`, so no system maths library can
change a result. A `RunManifest` records every input a run needs, and
`RunManifest.reproduce()` stops at the first mismatch.

Agents are Python, so an agent-driven run also depends on the Python
version. Python 3.12 changed built-in `sum()` over floats, and until 0.8.5
that moved the reference agents' orders in their last digit between 3.11
and 3.12. The package now adds floats in a fixed order, and
`tests/test_python_versions.py` checks an agent-driven run on 3.11, 3.12
and 3.13 against one digest. An agent you write that calls `sum()` over
floats can still differ between 3.11 and 3.12, so name the Python version
with a result that depends on one; a manifest records it.

To cite the model, name the version and the preset: "tradefloor 0.8.5,
preset pt-v20". See the README's "Citing tradefloor" section.
