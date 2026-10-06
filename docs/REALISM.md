# Realism

The numbers behind the README's realism claims, and every limit that has
been measured. The model these statistics describe is in
[MODEL.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/MODEL.md).

## The three sets of statistics

tradefloor checks its market against real ones with three named sets of
statistics. [docs/STATISTICS.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/STATISTICS.md)
lists every member of each, and the counts below refer to them.

### The one-year table

The one-year table has 19 statistics of a simulated market, such as
volatility, fat tails, how much stocks move together and how far the VIX jumps
after a fall. `tf.facts.measure()` reads 18 of them over 252 days, and
`tf.facts.crisis_statistics()` the nineteenth, which needs a run with a crisis
in it. `tf.envelope.score()` compares each with the range real markets show
over a year. On `pt-v21`, the default from 0.10.0, 18 of the 19 are inside
their ranges on the ruled bands `score()` uses. The tail rate is under the
floor on the 30 certification seeds and inside it pooled over 360: the index
falls 3 percent or more on 0.598 percent of days against a range of 0.64 to
2.34, and on 0.98 percent pooled over 360 seeds.
The check runs 30 random seeds. Fifteen of the statistics are read on one
fixed set of companies, and that fixed-roster panel is repeated on held-out
seeds and on a held-out set of companies.

The count is a verdict on figures pooled over the 30 seeds, the median for
each shape statistic. One seed's year often misses one or more of the 14
shape statistics: on pt-v21, seeds 101 to 116 had all 14 in range on 8 of
the 16, and one seed had 11 of 14. If you run one market per condition, read
`tf.envelope.intervals()`, which gives each statistic's spread across seeds
beside its range.

A shape statistic's range is the median of 35 real one-year windows, plus or
minus 2.1 times their trimmed standard deviation. That makes it wide, and
passing it is weak evidence. Volatility clustering shows the gap. On pt-v21
`abs_return_acf1` reads 0.095, near the median real year's 0.1025, but
`abs_return_acf5` reads 0.020, below the lowest of the ten 2015 to 2025
windows in `tf.facts.REAL_MARKETS_WINDOWS` (0.034), and passes because its
range reaches lower than those windows do. `sector_excess_corr`, 0.112, is
also below every one of those windows (the lowest is 0.133). Changing them
changes the simulation, so it waits for the next preset.

Four of the 19 describe the index as a whole. On pt-v21 an equal-weight index
of the stocks gains 8.8 percent a year over one year, inside a real range of
1.1 to 10.3. On a day the index falls 1 percent or more,
the VIX rises a median 2.0 points, inside a real range of 0.39 to 3.03. On a
3 percent fall it rises 5.3, inside 2.6 to 9.58. The index falls 3 percent or
more on 0.60 percent of days, against 1.21 percent in real markets. That
is a gap: the row is under its floor of 0.64 on the 30 certification seeds.
Over 360 seeds it reads 0.98, inside the band.

The VIX's own day-to-day persistence (`vix_ar1_debiased`) has no band and is
graded by a sign test against the real tape's centre. pt-v21 reads 0.956
against the tape's 0.930 and the test refuses it, at one year and on held-out
seeds. That is a gap: the VIX is stickier from day to day than the real
one.

### The two-year panel

The two-year panel is the fixed-roster panel run for 504 days. Fourteen
of its 15 statistics have a two-year range, and pt-v21 has all 14 inside.

### The long-run criteria

The long-run criteria are 40 rows over 21 years. The check runs the market
for 21 years many times over and replays 2008 and 2020 with the real VIX.
The first 15 rows compare what a user would notice with real markets: how
deep crashes go, how long fear lasts, how often the VIX is above 30 or below
15, how many bear markets and corrections a decade brings, the long-run
return, and whether a headline read late still pays. C4a and C4b ask whether
a rule that reads only prices can find an edge real markets do not have. The
other 23 cover the rate indices against real treasury and corporate bonds,
the earnings cycle, value and momentum signals, timing rules on the
published macro data, the cost of size in the book, the real 2020-21 and
2022 macro paths, and the packaged recession.

pt-v21 meets all 40, with the long-run rows read on 270 histories and R4 and
D1 on the definitions it was registered on. On the twelfth registration's
definitions it reads 38 of 40: the correlation of the index with the corporate
bond at the last print is +0.112 against a floor of +0.15, and the tail rate
on the 30 certification seeds is the miss above. On pt-v21 the 2008 replay
falls 47 percent against the real 57, the VIX is above 30 on 5.2 percent of
days against a real 8.2, and the index returns 6.4 percent a year over 21
years against a real 6.25. The verdicts ship with
the package as `tf.preset_record()["long_run"]`. The scripts that graded
pt-v21 and pt-v20, their inputs and the grading runs' output are in
`validation/pt-v21/` and `validation/pt-v20/`, and
[validation/README.md](https://github.com/simoncoombes/tradefloor/blob/main/validation/README.md) says how to check either grade
in a second or run it again.

Some rows pass near their edges. On pt-v21 a timing rule on the published
macro data uses 83 percent of its tolerance. The index's daily correlation
with Treasuries is -0.319, against a real -0.161 and a floor of -0.36. A
headline read five ticks late still earns 15 basis points, under a ceiling
of 20. A recession wins back 52 percent of its fall in a year against 62
percent in 2009. The price trough leads the earnings trough in the driven
2020 market by 19 sessions against a real 68.

Two things are still off. The worst month of the 2020 replay is 74 percent
volatility against a real 95, about a fifth milder. With the VIX held at 65
the market is 5.0 times as volatile as with it held at 5, against 6.2 times
in real markets.

Each crisis starts in one sector, picked at random. A scenario can pick it for
you with `Scenario().hold(epicentre="financial_services")`.

pt-v21 moves 104 of pt-v20's dials, to values picked in screens against
registered long-run rows on held-out seeds and graded on fresh ones. Many of
the dials it keeps are pt-v19's, chosen with a scoring rule over 19
statistics that overlaps the one-year table. So that table helped choose them and cannot also serve as a
held-out test. The held-out checks are the fresh seeds and the fresh set of
companies.

### The shock flow behind every figure

Every figure on this page was measured with the engine making its own shocks
and nothing else. A preset draws company news at each open, with probability
`endogenous_news_intensity` a name and a log size of sd
`endogenous_news_sigma`. It draws idiosyncratic and market jumps. It draws no
sector or market-wide news. It steps the economy once a trading session, with
no economic shocks, and ticks only the 390 minutes of the regular session.
`tf.envelope.CALIBRATED_FLOW` holds this flow for every preset, read from its
parameters, and the Rust crate states it as `tradefloor::flow::CalibratedFlow`.

| preset | company news a name a session | news log sd | news variance a name a year | idiosyncratic jumps a name a session, log sd | market jumps a session, mean, sd | market factor daily sd | macro steps a year |
|---|---|---|---|---|---|---|---|
| pt-v14 | 0.05 | 0.0204 | 0.0052 | 0.0069, 0.0875 | 0.0566, -0.0085, 0.0029 | 0.0088 | 365 |
| pt-v16 | 0.05 | 0.0175 | 0.0039 | 0.0069, 0.0752 | 0.0566, -0.0085, 0.0025 | 0.0076 | 365 |
| pt-v18 | 0.05 | 0.0175 | 0.0039 | 0.0069, 0.0752 | 0.0566, -0.0085, 0.0025 | 0.0076 | 365 |
| pt-v19 | 0.05 | 0.0175 | 0.0039 | 0.0069, 0.0752 | 0.0566, -0.0085, 0.0025 | 0.0076 | 252 |
| pt-v20 | 0.05 | 0.0175 | 0.0039 | 0.0069, 0.0752 | 0.0283, -0.0085, 0.0025 | 0.0065 | 252 |
| pt-v21 | 0.05 | 0.0175 | 0.0039 | 0.0090, 0.0318 | 0.0050, -0.0300, 0.0100 | 0.0071 | 252 |

There is no pt-v17. From pt-v19 the macro calendar counts trading sessions,
so a macro month is 21 sessions. From pt-v20 a company's news moves its fair
value for good (`fair_value_news_share` 1), where on earlier presets it moved
the mispricing, which reverts. On pt-v21 part of each day's draws falls at
the open as an overnight move; the day's variance is split, not added to, so
the flow above is the same size.

A host that hands the engine its own news (`Engine.tick`, `run_session`),
economic shocks (`advance_day`) or earnings revisions (`set_fundamentals`)
adds to this flow, and the figures above do not describe the sum.
`tf.envelope.external_flow(...)`, or `tf.envelope.check(...,
external_flow=...)`, takes the log moves a host's events landed, its earnings
revisions and its macro steps, and says which channels are outside the
fitted flow. The Rust crate's `tradefloor::flow::ExternalFlow` keeps the same
tally from the `NewsEvent` slices and shocks a host passes, and both read one
implementation. A channel is outside when any of these holds for it:

- company news adds more variance a name than the preset's own company news
  carries, so the news channel runs at more than twice its fitted size;
- sector and market-wide news add more than a tenth of the market factor's
  base daily variance (`market_factor_sigma` squared);
- earnings revisions add more variance a name than the preset's own company
  news, since fair value is earnings times a target multiple and moves one
  for one with them;
- any macro step carries an active economic shock, since each step's shocks
  add twice the absolute sum of `gdp_impact x severity` to the VIX target,
  and the market factor's variance target follows the VIX
  (`market_vol_vix_coupling` 0.75 on pt-v21);
- the host writes the economy's VIX by more than 0.1 points a session on
  average, about a sixth of the VIX's own mean daily change on pt-v20;
- the economy steps more than 1.05 times a trading session, as it does when
  a host steps it on weekends.

The tolerances, a tenth of the factor variance, 0.1 VIX points and 1.05
steps, are chosen, not fitted.

A host that wants its own headlines to carry the company news can take the
preset's out, with `ModelParams.from_preset("pt-v21",
endogenous_news_intensity=0.0)` in Python or `with_override` in Rust, and
pass company news at the fitted rate and size (0.05 events a name a session,
log sd 0.0175 on pt-v21 and pt-v20). The news channel then keeps its fitted size, though
the events land on one tick rather than over the preset's absorption profile,
and the result is a modified preset that the certification does not cover.
The other channels have no counterpart in the preset to swap out, so the way
to stay inside them is to pass less.

Pre-market and after-hours ticks are outside the fitted schedule too, and
`external_flow` does not count them, because in the replay below they moved
index volatility by less than half a point.

A host-driven embedder's flow, recorded over 504 sessions on five seeds,
was outside on every channel. Its company news added 1.9 to 2.6 times the
fitted company news variance, its sector and market-wide news 0.41 to 0.80 of
the market factor's base daily variance, and its earnings revisions 50 to 105
times the fitted company news variance (about four a name a year, at an rms
log change of 0.23 to 0.34). Economic shocks were active on 52 to 74 percent
of its macro steps, it wrote the VIX by 0.08 to 0.72 points a session and up
to 25 points at once, and it stepped the economy 1.39 times a session. The
same five seeds on pt-v20 with none of this read an index volatility of 8.4
to 29.3 percent on the host's own roster, a mean of 17.8. Replayed into
pt-v20 on a 108-name `Universe.random` roster over 20 seeds and 504 sessions,
without the burn-in unless the row says so:

| arm | index volatility, % a year | correlation with the market | VIX mean | days with VIX above 40, % | largest daily index move, % | days with GDP growth at the host's 5% cap, % |
|---|---|---|---|---|---|---|
| pt-v20 alone | 12.0 | 0.59 | 13.4 | 0.2 | 3.1 | 0 |
| with the burn-in (10 seeds) | 13.3 | 0.62 | 13.8 | 0.4 | 4.8 | 0 |
| the host's macro stepping: weekends, its volatility and its market return | 12.2 | 0.57 | 13.5 | 0.2 | 3.5 | 0 |
| that stepping and pre-market and after-hours ticks | 12.6 | 0.53 | 13.7 | 0.1 | 3.0 | 0 |
| its company, sector and market-wide news | 14.5 | 0.60 | 13.9 | 0.2 | 5.3 | 0 |
| that stepping and its economic shocks | 16.2 | 0.63 | 16.9 | 0.5 | 4.9 | 0 |
| its earnings revisions | 13.3 | 0.46 | 13.6 | 0.2 | 3.5 | 0 |
| that stepping and its VIX and GDP writes | 14.0 | 0.59 | 14.6 | 0.5 | 3.9 | 46 |
| all of these | 23.1 | 0.55 | 18.8 | 1.3 | 7.3 | 47 |
| all of these, with the burn-in (10 seeds) | 25.5 | 0.57 | 19.4 | 2.4 | 8.2 | 46 |

Each row below the first two is pt-v20 alone with the channels it names
added. The seed spread is wide: index volatility on pt-v20 alone runs from 8.4 to 21.8
across the 20 seeds, and with all of it from 12.7 to 47.1.

The response follows the size of the flow and does not run away. With the
host's macro stepping, its news and its shocks at half, full and double
their recorded size, index volatility reads 14.9, 18.9 and 32.4 against 12.2
with neither, so the variance they add grows about as the square of their
size, a little faster at double, where the VIX passes 40 on 5.4 percent of
days.

## Limits

These limits are measured and written down. The last column says what would
close each one. Anything marked "the next preset" changes the simulation, and
a shipped preset never changes, so those wait for a new one.

| limit | what it means | closed by |
|---|---|---|
| horizon | one year is certified. Two years is graded on the two-year panel, and longer runs only by the long-run criteria | bands derived at longer horizons |
| volatility memory | on pt-v21 the |return| autocorrelation reads 0.095 against a real 0.107 at lag 1, 0.020 against 0.052 at lag 5 and 0.001 against 0.029 at lag 20, so the memory fades faster than real after the first day | the next preset |
| scenario size | a driven scenario moves prices at about half to four-fifths of the real size, in the right direction. On the real 2020-21 path pt-v21's response to the VIX, the credit yield and valuations is 0.72, 0.79 and 0.54 of real AAPL's. Use a scenario to detect a response, and do not read its size as a forecast | the next preset |
| macro crises | an inflation crisis or a policy crisis needs a scenario to drive it | a scenario |
| roster | certification used a sector-balanced roster. Four concentrated sector mixes hold every graded shape row for up to two years on pt-v19, so `check` grants them on pt-v19 only. On pt-v21 they hold at one year, but at two years the technology-heavy and all-technology mixes read `volume_abs_return_corr` at 0.630 and 0.641 against a ceiling of 0.63 | the next preset |
| external flow | every figure is measured under the preset's own shocks. News, economic shocks or earnings revisions a host adds on top move the market outside them, and `tf.envelope.external_flow` says which channels | scale the host's flow to `CALIBRATED_FLOW` |
| opening state | on pt-v21 the market lives 504 sessions of prehistory before day 0, so the opening VIX varies with the seed, from 10.3 to 43.2 on the certified roster over seeds 101 to 130 (median 17.6) | no limit on pt-v21 |
| overnight gaps | on pt-v21 each session opens after an overnight move, and a position held overnight can gap through a stop. The open sits 0.52% from the previous close at one standard deviation against 1.05% for the whole day (`Universe.random(20, seed=7)`, seed 2026, 30 days), so about a quarter of a name's daily variance falls overnight, against 0.39 in real large caps from 2015 to 2025 | the next preset |
| intraday | nothing below the 65-minute step is calibrated. One-minute returns have a lag-1 autocorrelation of -0.40 on pt-v21 from bid-ask bounce | the next preset |
| slicing a large order | one sweep of the book follows the square-root law. On pt-v21 the metaorder memory makes a sliced order pay for the volume it has already taken: buying 10% of a day's volume in 36 slices over a day costs a median 0.13 of a daily standard deviation (0.06 to 0.35 over 40 programmes), against 0.15 to 0.3 from published studies | the next preset |
| agent interaction | in isolated mode, the default, no other trader reacts to an agent: volume, depth and the background flow ignore it, so no liquidity spiral or predatory trading can arise. Populated mode (`population=`) adds background traders that trade its signals and front-run predictable flow, but a predictable programme pays only about 2.4% more there than in isolated mode, against the 169% van Kervel and Menkveld (2019) report, because impact here is mostly transient | the next preset |

`tf.envelope.check(horizon_days=...)` refuses a question that falls outside
a limit, and [the realism envelope](https://docs.tradefloor.dev/how-its-measured.html)
says what each one forbids.

The model has no factor structure beyond each company's beta and sector.
There are no style factors, and you cannot supply a covariance matrix.
Forced selling is switched off on pt-v21, so a correlated deleveraging cannot
be represented in isolated mode. In populated mode the crowded population's
loss limits unwind crowded trades: when the momentum crowd sells out, holders
of the five-day momentum signal lose 0.06 of a daily standard deviation that
day and get it back over the following week. The market has one venue and no
latency.
