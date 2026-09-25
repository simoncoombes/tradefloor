# The realism statistics

tradefloor checks its market against real markets with several sets of
statistics. The sets overlap, have different counts, and are measured over
different horizons, so "19 of 19", "15 of 15", "14 of 14" and "28 of 28" can
all be true of the same preset at once. This page names each set, lists what is in it,
and says where it is used. The README and the documentation site use these
names.

The model itself is specified in
[MODEL.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/MODEL.md).

## The sets at a glance

| Set | Count | Horizon | pt-v20, the default from 0.8.5 | pt-v19 |
|---|---|---|---|---|
| [The one-year table](#the-one-year-table) | 19 statistics | 252 sessions | 19 of 19 in band | 19 of 19 |
| [The two-year panel](#the-two-year-panel) | 15 statistics, 14 with a band | 504 sessions | 14 of 14 in band | 14 of 14 |
| [The long-run criteria](#the-long-run-criteria) | 28 registered rows for pt-v20; 15 in records up to 0.8.1 | 21 years | 28 of 28 met | fails 10 of the 28, C9 not scored; its own record reads 15 of 17 |
| [The hosted report](#the-hosted-report) | no statistics of its own | | quotes the long-run criteria | |

Three parts of the one-year table have counts of their own, and appear on
some pages: [the 14 shape statistics](#the-shape-statistics), [the
fixed-roster panel](#the-fixed-roster-panel) of 15, and [the 4 index
rows](#the-index-rows).

## The one-year table

**19 statistics, graded over 252 sessions.** This is the set the realism
envelope publishes, over the one-year horizon it certifies. pt-v20 has all
19 in band, and so does pt-v19.

| Statistic | What it measures | Group |
|---|---|---|
| `annualised_vol_pct` | a stock's annualised volatility | shape |
| `excess_kurtosis` | how fat the tails of daily returns are | shape |
| `return_acf1` | lag-1 autocorrelation of daily returns | shape |
| `abs_return_acf1` | volatility clustering at lag 1 | shape |
| `abs_return_acf5` | volatility clustering at lag 5 | shape |
| `abs_return_acf20` | volatility clustering at lag 20 | shape |
| `cross_sectional_corr` | how much stocks move together | shape |
| `volume_abs_return_corr` | volume against the size of the move | shape |
| `leverage_effect` | today's return against tomorrow's volatility | shape |
| `volume_change_acf1` | lag-1 autocorrelation of volume changes | shape |
| `corr_asymmetry` | correlation on down days less up days | shape |
| `corr_asymmetry_lagged` | correlation after down days less after up days | shape |
| `sector_excess_corr` | same-sector correlation beyond the market | shape |
| `corr_persistence_acf1` | how long high correlation lasts | shape |
| `crisis_sector_dispersion` | how unevenly a crisis falls across sectors | dispersion |
| `index_drift_pct` | the equal-weight index's return a year | index |
| `fear_gauge_dn1` | the VIX's rise on a day the index falls 1% or more | index |
| `fear_gauge_dn3` | the VIX's rise on a day the index falls 3% or more | index |
| `index_tail_dn3_pct` | the share of days the index falls 3% or more | index |

`tf.facts.measure()` returns 18 of the 19 on every run. The nineteenth,
`crisis_sector_dispersion`, comes from `tf.facts.crisis_statistics()`, which
`measure()` calls, and it appears only when a run holds at least 30 sessions
with the VIX above the crisis threshold; otherwise `measure()` reports it
absent and says why. On pt-v20 one of the thirty certification seeds reads
it at one year.

**How it is measured.** Thirty seeds, 101 to 130, of a 252-session run.

- The shape statistics and crisis dispersion are read on one fixed roster, `Universe.random(40, seed=111)`, and each is the median across seeds. Crisis dispersion needs 30 sessions with the VIX above the crisis threshold, so it is the median over the seeds that have them: 4 of 30 at one year.
- The four index rows are read on a roster that changes with the seed, `Universe.random(40, seed=s)`, because an index level depends on the roster as much as on the model. The index return is a mean across seeds, the 1% fear row a median, the 3% fear row a median of the pooled days, and the tail row a pooled rate.

**What grades it.** Each statistic has a band: the range real markets
show over a year. Each band comes from the longest record its statistic
can be read on: 32 of the roster's 40 US large caps from June 1987 to July
2025 for the shape rows; the S&P 500 from 1928 for the index return and tail
rows; the VIX against the S&P 500 from 1990 for the fear rows and crisis
dispersion. A fixed rule sets each band from the spread of one-year windows
on that record. `tf.envelope.score()` grades a run against the bands, and
`tf.envelope.certified()` returns the default preset's table.

**Where it is used.** The README and `rust/README.md` ("19 statistics"), and on the
documentation site the Realism envelope, The metrics, Principles, The two
loops, Running a market and Agents pages, and notebook 04.

### The shape statistics

The 14 rows in the shape group. They describe the shape of returns and
volume, and a drift in the index barely moves them. This is the set the
certificate in `tf.envelope.certify()` grades, so it is the "14 of 14" in
the certificate's output. In the code the set is `facts.SHAPE`.

### The fixed-roster panel

The 14 shape statistics plus crisis dispersion: the 15 rows read on the
fixed roster. This is the panel stored in each preset's record
(`tf.preset_record()["panel_252"]`) and repeated on held-out seeds (1 to 30)
and a held-out roster (`Universe.random(60, seed=909)`). pt-v20 and pt-v19
each have 15 of 15 in band. Crisis dispersion joined in 0.8.0, so records of earlier presets
carry 14 rows. Used on the documentation site's Presets page ("In band,
252d") and in the hosted preset list.

### The index rows

The four rows about the index as a whole: `index_drift_pct`,
`fear_gauge_dn1`, `fear_gauge_dn3` and `index_tail_dn3_pct`. They are read
on the changing roster described above, over 252 sessions only. They are not
in the fixed-roster panel and not in the two-year panel. The code calls the
first a level row and the other three crisis rows (`facts.LEVEL`,
`facts.CRISIS`).

## The two-year panel

**The fixed-roster panel, measured again over 504 sessions: 15 statistics,
14 with a two-year band.** `corr_persistence_acf1` has no two-year band,
because the only band that exists for it was built on a different window
protocol and describes a different quantity. So the panel is graded on 13
shape statistics and crisis dispersion. pt-v20 and pt-v19 each have 14 of
14 in band.

The index rows have two-year bands, and this count leaves them out because
they were measured at one year only. The certified horizon is one year, and
two years is graded as well.

Records of presets before pt-v19 have no crisis dispersion row, so their
two-year counts are out of 13: pt-v18 has 13 of 13, pt-v10 12 of 13, pt-v3
7 of 13. The 14 of 14 of pt-v19 and pt-v20 is the same 13 plus crisis
dispersion.

**Where it is used.** The Realism envelope and The metrics pages ("14 of 14
graded rows at 504 days"), the Presets page ("In band, 504d"), and the
hosted preset list.

## The long-run criteria

**28 registered rows over 21 years for pt-v20.** The set grew as the model
did: 15 criteria when pt-v19 was adopted, 17 when C4a and C4b were added,
and 28 when pt-v20 was registered. Each row is something a user would
notice, with a tolerance that is easy to read. The result is in
`tf.preset_record()["long_run"]`.

pt-v20's rows were registered before the boxes that graded them (design
repository, `programme/ptv20-registration.md`). The final grade, box
`ptv20g3`, pools 90 free-running histories of 21 years (seed sets 101 to
130, 401 to 430 and 701 to 730), replays 2008 and 2020 with the real VIX,
and runs the late-headline probe, the price-only rules on the published
suite of 20 markets, and the cost-of-size fit. pt-v20 meets all 28. On the
same pooled histories pt-v19 fails 10: B9, C4a, C4b, C5, C6, C7, C8, R1, R4
and E1 (`programme/results/ptv20/criteria-g3.txt`). pt-v19's own record,
from an earlier box of 30 histories, carries 17 rows and reads 15 of 17.
Records up to 0.8.1 carry the first 15, and pt-v18 met 8 of those.

| Id | Criterion | Tolerance | pt-v20 | Real |
|---|---|---|---|---|
| A1 | worst month's volatility in the 2008 and 2020 replays, % | within 30% | 86.7, 75.4 | 84.3, 94.5 |
| A2 | maximum drawdown in the 2008 and 2020 replays | within 30% | 0.469, 0.353 | 0.568, 0.339 |
| A3 | peak stock correlation in the 2008 and 2020 replays | within 0.15 | 0.782, 0.778 | 0.748, 0.872 |
| B1 | share of sessions with the VIX above 30 | half to twice | 0.058 | 0.082 |
| B2 | mean length of a spell with the VIX above 30, sessions | half to twice | 26 | 22 |
| B3 | 20% bear markets a decade | half to twice | 2.07 | 1.12 |
| B4 | 10% corrections a decade | half to twice | 4.83 | 3.65 |
| B5 | sessions down more than 5% a decade | half to twice | 6.2 | 6.2 |
| B6 | share of sessions with the VIX under 15 | half to twice | 0.396 | 0.326 |
| B7 | the index's annual volatility, % | within 20% | 17.0 | 18.1 |
| B8 | the index's long-run return, % a year | within 2 points of the target | 5.7 | 6.25 |
| B9 | sd of annual index returns, %; start-up volatility ratio | within 20%; two thirds to 1.5 | 16.8; 1.19 | 17.4; 1 |
| C1 | crash rate in years 3 to 21 against years 1 to 2 | two thirds to 1.5 | 0.88 | 1.0 |
| C2 | histories that touch the VIX ceiling | at most 3 of 90 | 0 | 0 |
| C3 | the edge from reading a headline 5 ticks late, bp | under 20 | 15.7 | 0 |
| C4a | lag-1 autocorrelation of 65-minute returns; Roll spread over quoted | at or above -0.05, or Roll at most twice quoted | -0.019; 1.24 | 0; 1 |
| C4b | the best price-only rule on the suite: points over buy-and-hold; markets beaten of 20 | at most +5 and 14 | -0.2; 10 | 0; 10 |
| C5 | a company's variance ratio at 60 sessions | 0.80 to 1.05 | 0.947 | 0.924 |
| C6 | value signal's rank IC over 20 sessions, whole run and first 60 sessions | -0.03 to +0.05 | -0.000; 0.004 | 0.009 |
| C7 | momentum's rank IC, 12-1 and 6-1 | -0.04 to +0.095; -0.02 to +0.10 | -0.006; -0.005 | 0.027; 0.041 |
| C8 | one-day Lo-MacKinlay contrarian profit, bp a day | -6.4 to +2.9 | -0.66 | -1.74 |
| C9 | cost of size: exponent; coefficient | 0.4 to 0.7; 0.33 to 0.67 | 0.487; 0.468 | 0.5; 0.5 |
| R1 | daily sd of the 2-year yield, bp | 3.65 to 6.80 | 4.48 | 5.23 |
| R2 | daily sd of the 10-year yield, bp | 4.54 to 6.27 | 4.72 | 5.41 |
| R3 | correlation of index and Treasury returns | -0.36 to +0.03 | -0.154 | -0.161 |
| R4 | correlation of index and investment-grade bond returns | +0.15 to +0.39 | +0.231 | +0.272 |
| E1 | median fall in earnings through a contraction | -0.40 to -0.046 | -0.280 | -0.17 |
| D1 | the one-year table, in band on all four cells | every band in | all | all |

The real figures come from the S&P 500 and the VIX (A, B, C1 to C4), the
40-company reference roster (C5 to C8), published impact studies (C9,
Tóth et al. 2011), FRED DGS2 and DGS10 and the SPY, IEF and LQD funds
2015 to 2025 (R1 to R4), and Shiller's reported earnings 1953 to 2020 (E1).
The R bands are the real figure plus or minus two bootstrap standard
errors. B3 is nearest its edge, using 88% of its tolerance.

D1 contains the one-year table: it requires the fixed-roster panel in band
at one year, at two years, on held-out seeds and on a held-out roster, and
the index rows in band at one year.

**Where it is used.** The README and `rust/README.md`, the Realism
envelope, Presets, Parameters and Release notes pages, and the hosted
report.

## The hosted report

The hosted service's report card shows no realism statistics of its own.
Its one count is the long-run verdict, "passes all 15 of its long-run
checks", which is the long-run criteria as records up to 0.8.1 hold them;
with pt-v20's record the same line counts 28. The hosted preset list quotes the
fixed-roster panel and the two-year panel: "all 15 checks over one year and
all 14 over two". Both use the word "checks" for different sets; the names
on this page are the ones to use.

## Other sets you may meet

- **The scoring rule (19 statistics, different members).** pt-v19's dials were chosen with a scoring rule over the 14 shape statistics, the 4 index rows and the VIX's persistence, without crisis dispersion, centred on 2015 to 2025 medians (`loss.rule_table`). pt-v20 keeps those dials, and its new ones were picked on grids and screens against long-run rows (B3, B9, C8, R1 to R4) and the two-year panel. So neither the one-year table nor those rows is a held-out test; the held-out checks are the fresh seeds, the fresh roster, and the rows registered before the final box.
- **The VIX persistence row.** `vix_ar1_debiased`, the VIX's own day-to-day persistence. It is scored and reported, and it has no band.
- **Reported, not graded (4).** `fear_gauge_dn5`, `fear_gauge_up1`, `index_excess_kurtosis` and `index_tail_up3_pct`. `tf.facts.measure()` returns them beside the graded rows, with the reason each has no band.
- **Rows a model without the mechanism could pass (5).** `abs_return_acf20`, `leverage_effect`, `corr_asymmetry`, `corr_asymmetry_lagged` and `corr_persistence_acf1`. Their bands include the value a market with no such effect would give, so being in band shows the model is not wrong, not that it has the effect. `tf.facts.report()` names them.
- **The decade table (18).** The older bands from 2015 to 2025 only, over the shape statistics and the index rows. `basis="shipped"` selects it. The one-year table uses the longer record.
