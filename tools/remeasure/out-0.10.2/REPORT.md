# Published-figure re-measurement

Commit `e735974`, 2026-10-09 17:56, tradefloor 0.10.2. Full run: 377s wall with 64 workers.

| status | figures |
|---|---|
| reproduced | 4 |
| machine_bound | 1 |
| structural_ok | 6 |
| covered_by_tests | 2 |

## Every figure, by document

### README.md

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 106 | engine.truth(): the factor columns sum to the move to within about 1e-16 | any | 1e-16 | 7.37e-18 | - | reproduced |
| 292 | the test suite runs the numbered examples, and 07-research-workflow.py asserts its own findings | pt-v20 | True | True | - | structural_ok |

### examples/README.md

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 79 | 07-research-workflow.py takes about forty seconds | any | 40 | 45.21 | 5.211 | machine_bound |

### site-docs/api-scenario.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 132 | a pin declared after one on the same field that begins later is refused as out of order | any | True | True | - | structural_ok |
| 140 | rate_shock is two ramps, policy rate and corporate yield, held credit_spread apart | any | True | True | - | structural_ok |
| 140 | rate_shock called on a configured scenario is refused | any | True | True | - | structural_ok |
| 141 | vix_shock is exactly a hold at the calm level, a step to the peak and a ramp back | any | True | True | - | structural_ok |

### site-docs/core-types.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 81 | fundamental_value moves on every day of the run, for every name | pt-v20 | True | True | - | structural_ok |
| 364 | the three streams draws_by_stream() reports, in order | any | market,economy,external | market,economy,external | - | reproduced |
| 364 | draws_by_stream() reports three of the streams | any | 3 | 3 | 0 | reproduced |

### site-docs/evaluate.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 108 | evaluate()'s steps_per_day defaults to 6 | any | 6 | 6 | 0 | reproduced |
| 254 | reproduce() compares era digests before replaying and refuses a manifest from a build with different arithmetic | any | True | - | - | covered_by_tests |

### site-docs/rl-environment.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 87 | TradingEnv passes gymnasium's env_checker | any | True | - | - | covered_by_tests |

## Notes

- **readme.residual** (README.md:106): Tool fixed at 0.8.1: the recipe summed seven of the ten factors and graded the median, so it checked a sum no page describes.
- **readme.workflow_wall** (examples/README.md:79): A wall clock: reported as machine_bound, never judged. The README this row first cited said five seconds, then ten to twenty; examples/README.md now says about forty, measured with /usr/bin/time at 0.8.5 and rounded up.
- **core.reprice_days** (site-docs/core-types.html:81): Re-keyed at 0.8.1 from reprice_days, which recorded the pt-v12 meeting days (45 and 96). Fair value grows with nominal output from pt-v18.
