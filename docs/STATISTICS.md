# The realism statistics

tradefloor checks its market against real markets with several sets of
statistics. The sets overlap, have different counts, and are measured over
different horizons, so "19 of 19", "15 of 15", "14 of 14" and "15 of 17" can
all be true of the same preset at once. This page names each set, lists what is in it,
and says where it is used. The README and the documentation site use these
names.

The model itself is specified in
[MODEL.md](https://github.com/simoncoombes/tradefloor/blob/main/docs/MODEL.md).

## The sets at a glance

| Set | Count | Horizon | pt-v19 |
|---|---|---|---|
| [The one-year table](#the-one-year-table) | 19 statistics | 252 sessions | 19 of 19 in band |
| [The two-year panel](#the-two-year-panel) | 15 statistics, 14 with a band | 504 sessions | 14 of 14 in band |
| [The long-run criteria](#the-long-run-criteria) | 17 criteria from 0.8.5; 15 before | 21 years | 15 of 17 met; 15 of 15 up to 0.8.1 |
| [The hosted report](#the-hosted-report) | no statistics of its own | | quotes the long-run criteria |

Three parts of the one-year table have counts of their own, and appear on
some pages: [the 14 shape statistics](#the-shape-statistics), [the
fixed-roster panel](#the-fixed-roster-panel) of 15, and [the 4 index
rows](#the-index-rows).

## The one-year table

**19 statistics, graded over 252 sessions.** This is the set the realism
envelope publishes, over the one-year horizon it certifies. pt-v19 has all
19 in band.

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
absent and says why.

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
`tf.envelope.certified()` returns pt-v19's table.

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
and a held-out roster (`Universe.random(60, seed=909)`). pt-v19 has 15 of 15
in band. Crisis dispersion joined in 0.8.0, so records of earlier presets
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
shape statistics and crisis dispersion. pt-v19 has 14 of 14 in band.

The index rows have two-year bands, and this count leaves them out because
they were measured at one year only. The certified horizon is one year, and
two years is graded as well.

Records of presets before pt-v19 have no crisis dispersion row, so their
two-year counts are out of 13: pt-v18 has 13 of 13, pt-v10 12 of 13, pt-v3
7 of 13. pt-v19's 14 of 14 is the same 13 plus crisis dispersion.

**Where it is used.** The Realism envelope and The metrics pages ("14 of 14
graded rows at 504 days"), the Presets page ("In band, 504d"), and the
hosted preset list.

## The long-run criteria

**17 criteria over 21 years, from 0.8.5; 15 before.** Thirty free-running
histories of 21 years each, plus replays of 2008 and 2020 with the real VIX
imposed, plus a probe of how much a late-read headline is worth. Each
criterion is something a user would notice, with a tolerance that is easy to
read. The result is in `tf.preset_record()["long_run"]`.

pt-v19 was adopted as the default on the first 15, and meets all 15;
pt-v18, the previous default, meets 8. Two criteria were added on
2026-09-24 and ship in 0.8.5: C4a and C4b ask whether a rule that reads
only prices finds an edge real markets do not offer. pt-v19 fails both, so
its record reads 15 of 17 from 0.8.5. Records up to 0.8.1 carry the first
15 rows, and the documentation site, which describes the released 0.8.1
package, shows those 15 until 0.8.5 is released.

| Id | Criterion | Tolerance |
|---|---|---|
| A1 | worst month's volatility in the 2008 and 2020 replays | within 30% of real |
| A2 | maximum drawdown in the 2008 and 2020 replays | within 30% of real |
| A3 | peak stock correlation in the 2008 and 2020 replays | within 0.15 of real |
| B1 | share of sessions with the VIX above 30 | half to twice real |
| B2 | mean length of a spell with the VIX above 30 | half to twice real |
| B3 | 20% bear markets a decade | half to twice real |
| B4 | 10% corrections a decade | half to twice real |
| B5 | sessions down more than 5% a decade | half to twice real |
| B6 | share of sessions with the VIX under 15 | half to twice real |
| B7 | the index's annual volatility | within 20% of real |
| B8 | the index's long-run return a year | within 2 points of the target |
| C1 | crash rate in years 3 to 21 against years 1 to 2 | two thirds to 1.5 times |
| C2 | histories that touch the VIX ceiling | at most 1 of 30 |
| C3 | the edge from reading a headline 5 ticks late | under 20 basis points |
| C4a | lag-1 autocorrelation of 65-minute returns, median name, and the Roll spread against the quoted spread (from 0.8.5) | at or above -0.05, or Roll at most twice quoted |
| C4b | the best price-only rule on the published suite of 20 markets: median points over buy-and-hold, and markets beaten (from 0.8.5) | every rule at most +5 points and 14 of 20 |
| D1 | the one-year table, in band on all four cells | every band in |

On pt-v19, C4a reads -0.187 with a Roll spread 5.0 times the quoted one,
and in C4b a mean-reversion rule that trades every 65 minutes beats
buy-and-hold in 18 of 20 markets by a median 13.6 points; five-day momentum
also fails. The next preset is meant to fix both; see
[Coming in pt-v20](https://github.com/simoncoombes/tradefloor/blob/main/docs/MODEL.md#coming-in-pt-v20)
in the model specification.

D1 contains the one-year table: it requires the fixed-roster panel in band
at one year, at two years, on held-out seeds and on a held-out roster, and
the index rows in band at one year.

**Where it is used.** The README and `rust/README.md`, the Realism
envelope, Presets, Parameters and Release notes pages, and the hosted
report.

## The hosted report

The hosted service's report card shows no realism statistics of its own.
Its one count is the long-run verdict, "passes all 15 of its long-run
checks", which is the long-run criteria as records up to 0.8.1 hold them; with a 0.8.5
record the same line counts 17. The hosted preset list quotes the
fixed-roster panel and the two-year panel: "all 15 checks over one year and
all 14 over two". Both use the word "checks" for different sets; the names
on this page are the ones to use.

## Other sets you may meet

- **The scoring rule (19 statistics, different members).** pt-v19's dials were chosen with a scoring rule over the 14 shape statistics, the 4 index rows and the VIX's persistence, without crisis dispersion, centred on 2015 to 2025 medians (`loss.rule_table`). So the one-year table helped choose pt-v19 and cannot also serve as a held-out test of it. The held-out checks are the fresh seeds and the fresh roster.
- **The VIX persistence row.** `vix_ar1_debiased`, the VIX's own day-to-day persistence. It is scored and reported, and it has no band.
- **Reported, not graded (4).** `fear_gauge_dn5`, `fear_gauge_up1`, `index_excess_kurtosis` and `index_tail_up3_pct`. `tf.facts.measure()` returns them beside the graded rows, with the reason each has no band.
- **Rows a model without the mechanism could pass (5).** `abs_return_acf20`, `leverage_effect`, `corr_asymmetry`, `corr_asymmetry_lagged` and `corr_persistence_acf1`. Their bands include the value a market with no such effect would give, so being in band shows the model is not wrong, not that it has the effect. `tf.facts.report()` names them.
- **The decade table (18).** The older bands from 2015 to 2025 only, over the shape statistics and the index rows. `basis="shipped"` selects it. The one-year table uses the longer record.
