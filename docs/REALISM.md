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
volatility, fat tails, how much stocks move together and how far the VIX
jumps after a fall. `tf.facts.measure()` reads 18 of them over 252 days, and
`tf.facts.crisis_statistics()` the nineteenth, which needs a run with a
crisis in it. `tf.envelope.score()` compares each with the range real
markets show over a year. On the default preset, `pt-v20`, all 19 are inside
their ranges. The check runs 30 random seeds. Fifteen of the statistics are read on one fixed
set of companies, and that fixed-roster panel is repeated on held-out seeds
and on a held-out set of companies.

The 19 of 19 is a verdict on figures pooled over the 30 seeds, the median
for each shape statistic. One seed's year often misses one or more of the 14
shape statistics: on seeds 101 to 116 all 14 were in range on 5 of the 16,
and one seed had 8 of 14. If you run one market per condition, read
`tf.envelope.intervals()`, which gives each statistic's spread across seeds
beside its range.

A shape statistic's range is the median of 35 real one-year windows, plus or
minus 2.1 times their trimmed standard deviation. That makes it wide, and
passing it is weak evidence. Volatility clustering shows the gap.
`abs_return_acf1` reads 0.028 and `abs_return_acf5` 0.019, below the lowest
of the ten 2015 to 2025 windows in `tf.facts.REAL_MARKETS_WINDOWS` (0.039 and
0.034), and both pass because their ranges reach lower than those windows do.
Raising them changes the simulation, so it waits for the next preset.

Four of the 19 describe the index as a whole. An equal-weight index of the
stocks gains 7.7 percent a year over one year, inside a real range of 1.1 to
10.3 (pt-v19 gains 7.6). On a day the index falls 1 percent or more, the VIX
rises a median 1.7 points, inside a real range of 0.39 to 3.03. On a 3
percent fall it rises 4.3, inside 2.6 to 9.58. The index falls 3 percent or
more on 0.89 percent of days, against 1.21 percent in real markets.

### The two-year panel

The two-year panel is the fixed-roster panel run for 504 days. Fourteen
of its 15 statistics have a two-year range, and pt-v20 has all 14 inside.

### The long-run criteria

The long-run criteria are 40 rows over 21 years for pt-v20. The check
runs the market for 21 years, 90 times over, and replays 2008 and 2020 with
the real VIX. The first 15 rows compare what a user would notice with real
markets: how deep crashes go, how long fear lasts, how often the VIX is above
30 or below 15, how many bear markets and corrections a decade brings, the
long-run return, and whether a headline read late still pays. C4a and C4b ask
whether a rule that reads only prices can find an edge real markets do not
have. The other 23 cover the rate indices against real treasury and
corporate bonds, the earnings cycle, value and momentum signals, timing rules
on the published macro data, the cost of size in the book, the real 2020-21
and 2022 macro paths, and the packaged recession. pt-v20 meets all 40.
pt-v19, the previous default, fails 16 of the 40. Its own record has only
the first 17 rows and reads 15 of 17, failing C4a and C4b. On pt-v20 the 2008
replay falls 45 percent against the real 57, the VIX is above 30 on 5.9
percent of days against a real 8.2, and the index returns 6.4 percent a year
over 21 years against a real 6.25. The verdicts ship with the package as
`tf.preset_record()["long_run"]`. The scripts that graded pt-v20, their
inputs and the grading run's output are in `validation/pt-v20/`, and
[validation/README.md](https://github.com/simoncoombes/tradefloor/blob/main/validation/README.md) says how to check the grade
in a second or run it again.

Some rows pass near their edges. A timing rule on the published macro data
uses 92 percent of its tolerance. The price trough leads the earnings trough
in the driven 2020 market by 10 sessions against a real 68. The two-year
yield moves 3.87 basis points a day against a real 5.23. A recession wins
back 49 percent of its fall in a year against 62 percent in 2009. The index
has 2.0 bear markets a decade against a real 1.1.

Two things are still off. The worst month of the 2020 replay is about 19
percent milder than the real one. With the VIX held at 65 the market is 5.1
times as volatile as with it held at 5, against 6.2 times in real markets
(pt-v19 5.2).

Each crisis starts in one sector, picked at random. A scenario can pick it for
you with `Scenario().hold(epicentre="financial_services")`.

pt-v20 keeps pt-v19's coefficients where it does not add a mechanism, and
those were chosen with a scoring rule over 19 statistics that overlaps the
one-year table, so that table helped choose them and cannot also serve as a
held-out test. pt-v20's own dials were chosen against the long-run
criteria. The held-out checks are the fresh seeds and the fresh set of
companies.

## Limits

These limits are measured and written down. The last column says what would
close each one. Anything marked "the next preset" changes the simulation, and
a shipped preset never changes, so those wait for a new one.

| limit | what it means | closed by |
|---|---|---|
| horizon | one year is certified. Two years is graded on the two-year panel, and longer runs only by the long-run criteria | bands derived at longer horizons |
| volatility memory | weaker than real at every lag: about a quarter of real at lag 1 and a sixth at lag 20 | the next preset |
| scenario size | a driven scenario moves prices at a quarter to a half of the real size, in the right direction. On the real 2020-21 path the response to the VIX, the credit yield and valuations is 0.27, 0.47 and 0.26 of real AAPL's. Use a scenario to detect a response, and do not read its size as a forecast | the next preset |
| macro crises | an inflation crisis or a policy crisis needs a scenario to drive it | a scenario |
| roster | certification used a sector-balanced roster. Four concentrated sector mixes were measured on pt-v19 only, on the shape rows and for up to two years | the same measurement on pt-v20 |
| opening state | every run on a roster opens at nearly the same VIX (17.66 on the certified roster, whatever the seed), so one-year figures describe years that start calm. For another starting state, run `engine.run_days(n)` and fork from there | the next preset |
| overnight gaps | each session opens at the last print. The first tick moves with a standard deviation of 0.67% against 2.0% for the whole day, so a position held overnight behind a stop looks safer than it would live | the next preset |
| intraday | nothing below the 65-minute step is calibrated. One-minute returns have a lag-1 autocorrelation of -0.37 from bid-ask bounce | the next preset |
| slicing a large order | one sweep of the book follows the square-root law, but an order spread over a day costs far less. Buying 10% of a day's volume in 36 slices costs 0.04 of a daily standard deviation, against 0.15 to 0.3 from published studies of such orders, so a schedule optimiser will overstate the value of trading slowly | the next preset |
| agent interaction | an agent's temporary impact barely reaches the tape, its permanent impact is linear and fades on the mispricing's half-life, and volume, depth and the background flow ignore it. No liquidity spiral or predatory trading can arise | the next preset |

`tf.envelope.check(horizon_days=...)` refuses a question that falls outside
a limit, and [the realism envelope](https://docs.tradefloor.dev/how-its-measured.html)
says what each one forbids.

The model has no factor structure beyond each company's beta and sector.
There are no style factors, and you cannot supply a covariance matrix.
Forced selling is switched off on pt-v20, so a crowded trade unwinding, or
any other correlated deleveraging, cannot be represented. The market has one
venue, no latency, and no other trader that adapts to you.
