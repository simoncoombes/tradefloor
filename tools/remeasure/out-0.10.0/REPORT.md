# Published-figure re-measurement

Commit `6582c0e4`, 2026-10-05 15:54, tradefloor 0.10.0. Full run: 1397s wall with 6 workers.

| status | figures |
|---|---|
| reproduced | 5 |
| MOVED | 3 |
| machine_bound | 1 |
| structural_ok | 6 |
| covered_by_tests | 2 |

## Doc edits needed

Every row here is a published number the stated (or reconstructed)
method no longer produces. On unchanged main these are documents that
were already stale; after an engine change, this section IS the edit
list.

| where | figure | preset | published | measured |
|---|---|---|---|---|
| site-docs/release-notes.html:165 | federal_funds_rate takes this many distinct values over the run | pt-v20 | 2 | 1 |
| site-docs/index.html:107 | separation('mean_reversion', 'momentum') on the twelve-market grid, wins each way (bound) | pt-v20 | 7-5 | 4-8 |
| site-docs/index.html:107 (also site-docs/index.html:108) | the sign-test p-value of that separation (bound) | pt-v20 | 0.7744 | 0.3877 |

## Every figure, by document

### README.md

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 196 | engine.truth(): the factor columns sum to the move to within about 1e-16 | any | 1e-16 | 7.37e-18 | - | reproduced |
| 427 | the test suite runs the numbered examples, and 07-research-workflow.py asserts its own findings | pt-v20 | True | True | - | structural_ok |

### examples/README.md

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 79 | 07-research-workflow.py takes about forty seconds | any | 40 | 167.8 | 127.8 | machine_bound |

### site-docs/api-scenario.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 129 | a pin declared after one on the same field that begins later is refused as out of order | any | True | True | - | structural_ok |
| 137 | rate_shock is two ramps, policy rate and corporate yield, held credit_spread apart | any | True | True | - | structural_ok |
| 137 | rate_shock called on a configured scenario is refused | any | True | True | - | structural_ok |
| 138 | vix_shock is exactly a hold at the calm level, a step to the peak and a ramp back | any | True | True | - | structural_ok |

### site-docs/core-types.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 78 | fundamental_value moves on every day of the run, for every name | pt-v20 | True | True | - | structural_ok |
| 360 | the three streams draws_by_stream() reports, in order | any | market,economy,external | market,economy,external | - | reproduced |
| 360 | draws_by_stream() reports three of the streams | any | 3 | 3 | 0 | reproduced |

### site-docs/evaluate.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 160 | evaluate()'s steps_per_day defaults to 6 | any | 6 | 6 | 0 | reproduced |
| 302 | reproduce() compares era digests before replaying and refuses a manifest from a build with different arithmetic | any | True | - | - | covered_by_tests |

### site-docs/index.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 107 | separation('mean_reversion', 'momentum') on the twelve-market grid, wins each way (bound) | pt-v20 | 7-5 | 4-8 | - | MOVED |
| 107 | the sign-test p-value of that separation (bound) | pt-v20 | 0.7744 | 0.3877 | -0.3867 | MOVED |

### site-docs/release-notes.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 165 | federal_funds_rate takes this many distinct values over the run | pt-v20 | 2 | 1 | -1 | MOVED |
| 165 | gdp_growth takes this many distinct values over the run | pt-v20 | 2 | 2 | 0 | reproduced |

### site-docs/rl-environment.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 79 | TradingEnv passes gymnasium's env_checker | any | True | - | - | covered_by_tests |

## Notes

- **readme.residual** (README.md:196): Tool fixed at 0.8.1: the recipe summed seven of the ten factors and graded the median, so it checked a sum no page describes.
- **readme.workflow_wall** (examples/README.md:79): A wall clock: reported as machine_bound, never judged. The README this row first cited said five seconds, then ten to twenty; examples/README.md now says about forty, measured with /usr/bin/time at 0.8.5 and rounded up.
- **core.reprice_days** (site-docs/core-types.html:78): Re-keyed at 0.8.1 from reprice_days, which recorded the pt-v12 meeting days (45 and 96). Fair value grows with nominal output from pt-v18.
- **agents.sep_mom_mr** (site-docs/index.html:107): The snippet also appears at docs/running-a-market.html in the code sample; the build writes both from the same fixture.
