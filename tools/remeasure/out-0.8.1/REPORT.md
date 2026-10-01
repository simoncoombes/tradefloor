# Published-figure re-measurement

Commit `c618089`, 2026-09-24 09:52, tradefloor 0.8.1. Full run: 364s wall with 64 workers.

| status | figures |
|---|---|
| reproduced | 43 |
| machine_bound | 3 |
| structural_ok | 14 |
| covered_by_tests | 5 |

## Every figure, by document

### README.md

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 80 | engine.truth(): the factor columns sum to the move to within about 1e-16 | any | 1e-16 | 1.67e-16 | - | reproduced |
| 217 | the test suite runs the numbered examples, and 07-research-workflow.py asserts its own findings | pt-v19 | True | True | - | structural_ok |

### docs/agents.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 178 | separation('mean_reversion', 'momentum') on the twelve-market grid, wins each way (bound) | pt-v19 | 12-0 | 12-0 | - | reproduced |
| 180 | the sign-test p-value of that separation (bound) | pt-v19 | 0.000488 | 0.000488 | 0 | reproduced |
| 229 | momentum's pooled capture over the twelve-market grid (bound) | pt-v19 | -0.1031 | -0.1031 | 0 | reproduced |
| 229 | mean-reversion's pooled capture over the twelve-market grid (bound) | pt-v19 | 0.9467 | 0.9467 | 0 | reproduced |
| 239 | markets on which mean-reversion has the highest P&L of the four ranked agents (bound) | pt-v19 | 11 | 11 | 0 | reproduced |
| 246 | markets on which mean-reversion out-earns the Oracle (bound) | pt-v19 | 5 | 5 | 0 | reproduced |

### docs/api-scenario.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 82 | a pin declared after one on the same field that begins later is refused as out of order | any | True | True | - | structural_ok |
| 90 | rate_shock is two ramps, policy rate and corporate yield, held credit_spread apart | any | True | True | - | structural_ok |
| 90 | rate_shock called on a configured scenario is refused | any | True | True | - | structural_ok |
| 91 | vix_shock is exactly a hold at the calm level, a step to the peak and a ramp back | any | True | True | - | structural_ok |

### docs/checkpoints.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 68 | tf.branch(engine, 2, ...) takes under 1 ms on a sixty-day, forty-instrument market | any | 1 | 11.53 | 10.53 | machine_bound |
| 68 | Checkpoint.resume() takes 2.7 s on the same market | any | 2.7 | 1.346 | -1.354 | machine_bound |
| 70 | Checkpoint replay is three orders of magnitude slower than branch | any | 3 | 2.067 | -0.9328 | reproduced |

### docs/conventions.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 69 | Universe.random short interest: median over the whole three-letter ticker space, percent of shares outstanding | any | 3.45 | 3.452 | 0.00234 | reproduced |
| 69 | share of generated names whose short interest is above the 20% squeeze threshold, percent | any | 9.54 | 9.541 | 0.00142 | reproduced |
| 73 | the preset dictionary carries eight numbers | any | 8 | 8 | 0 | reproduced |

### docs/core-concepts.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 121 | vix takes a new value on every day of the 120-day run | pt-v19 | 120 | 120 | 0 | reproduced |
| 129 | fundamental_value moves on every day of the run, for every name | pt-v19 | True | True | - | structural_ok |
| 137 | gdp_growth takes this many distinct values over the run | pt-v19 | 6 | 6 | 0 | reproduced |
| 145 | inflation_rate takes this many distinct values over the run | pt-v19 | 6 | 6 | 0 | reproduced |
| 153 | corporate_bond_yield takes this many distinct values over the run | pt-v19 | 5 | 5 | 0 | reproduced |
| 161 | federal_funds_rate takes this many distinct values over the run | pt-v19 | 1 | 1 | 0 | reproduced |

### docs/core-types.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 277 | the three streams draws_by_stream() reports, in order | any | market,economy,external | market,economy,external | - | reproduced |
| 277 | draws_by_stream() reports three of the streams | any | 3 | 3 | 0 | reproduced |

### docs/evaluate.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 78 | evaluate()'s steps_per_day defaults to 6 | any | 6 | 6 | 0 | reproduced |
| 226 | reproduce() compares era digests before replaying and refuses a manifest from a build with different arithmetic | any | True | - | - | covered_by_tests |

### docs/execution-cost.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 176 | the round trip's lowest shortfall over the eight seeds | pt-v19 | -13.53 | -13.53 | 0.004994 | reproduced |
| 178 | the round trip's highest shortfall over the eight seeds | pt-v19 | 3.81 | 3.807 | -0.002748 | reproduced |
| 180 | the round trip's median shortfall over the eight seeds | pt-v19 | -6.24 | -6.236 | 0.004428 | reproduced |
| 180 | round trips with a negative shortfall, of the eight seeds | pt-v19 | 7 | 7 | 0 | reproduced |
| 186 | median absolute impact on the names it traded, bps | pt-v19 | 6.35 | 6.347 | -0.003211 | reproduced |
| 186 | the largest move among the untouched names that moved, bps | pt-v19 | 3.43 | 3.429 | -0.001037 | reproduced |
| 186 | the smallest move among the untouched names that moved, bps | pt-v19 | -15.82 | -15.82 | 0.003472 | reproduced |
| 186 | untouched names that still moved | pt-v19 | 5 | 5 | 0 | reproduced |
| 186 | with the VIX pinned in both worlds, no untouched name moves | pt-v19 | True | True | - | structural_ok |
| 186 | names Momentum() traded over ten days on random(60,11) at seed 7 | pt-v19 | 54 | 54 | 0 | reproduced |
| 186 | names it never touched | pt-v19 | 6 | 6 | 0 | reproduced |

### docs/factors.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 59 | the change in mispricing splits into this many named factors | any | 10 | 10 | 0 | reproduced |
| 69 | the ten factor columns sum to the change in mispricing_s, residual about 1e-16 at worst | pt-v19 | 1e-16 | 1.67e-16 | - | reproduced |

### docs/glossary.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 154 | the market-stream draw divergence compare() reports is zero in every one of the twenty-eight comparisons | pt-v14 | 0 | 0 | 0 | reproduced |
| 202 | a nudged pt-v1 (garch_alpha=0.12) fingerprints as custom-XXXXXXXX, never as pt-v1 | pt-v1 | custom-d70ecdf0 | custom-d70ecdf0 | - | reproduced |
| 210 | a strategy fingerprint ignores whitespace, key order and a uniform scaling of blend weights | any | True | - | - | covered_by_tests |
| 274 | the known-answer digest this build computes is the sha256 committed in tests/known_answer.json | any | True | True | - | structural_ok |
| 362 | the engine derives ten random-number streams from the root seed | any | 10 | 10 | 0 | reproduced |
| 378 | the crisis threshold from pt-v14 | pt-v14 | 30.88 | 30.88 | 0.003251 | reproduced |
| 378 | the crisis threshold before pt-v14: the sector factor blended toward the market above VIX 25.5 | pt-v12 | 25.5 | 25.5 | 0 | reproduced |
| 394 | a hundred recorded 252-day 100-instrument engines alive at once, GB of raw buffers | any | 110 | 110.1 | 0.0736 | reproduced |

### docs/presets.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 63 | the preset the package ships as its default (bound) | pt-v19 | pt-v19 | pt-v19 | - | reproduced |
| 112 | the crisis lever: a market held at VIX 65 against one held at VIX 5, ratio of annualised volatility (bound) | pt-v19 | 5.221 | 3.171 | - | covered_by_tests |
| 159 | the market factor sigma is live and absent from the preset dictionary | any | True | True | - | structural_ok |
| 164 | no preset or setting changes how many draws are taken or in what order | any | True | - | - | covered_by_tests |

### docs/principles.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 204 | momentum's highest per-market capture on the grid (bound) | pt-v19 | 0.3914 | 0.3914 | 0 | reproduced |
| 204 | momentum's lowest per-market capture on the grid (bound) | pt-v19 | -0.5328 | -0.5328 | 0 | reproduced |

### docs/rl-environment.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 79 | TradingEnv passes gymnasium's env_checker | any | True | - | - | covered_by_tests |

### docs/running-a-market.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 109 | a reversed roster fingerprints differently | any | True | True | - | structural_ok |
| 254 | the one-day momentum signal rebalanced three times a day returns this over 30 days (bound) | pt-v19 | 38.6 | 38.6 | 0 | reproduced |
| 263 | the same signal rebalanced six times a day (bound) | pt-v19 | -3.086 | -3.086 | 0 | reproduced |
| 272 | the same signal rebalanced twelve times a day (bound) | pt-v19 | -37.05 | -37.05 | 0 | reproduced |

### docs/scenarios.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 183 | two pins on one field that begin on the same day are refused, in either order | any | True | True | - | structural_ok |

### docs/schemas.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 191 | vix_shock chained onto a built scenario is refused | any | True | True | - | structural_ok |
| 191 | compare() refuses a baseline that realises the same path as the scenario | any | True | True | - | structural_ok |
| 201 | a snapshot carrying eight streams, the 0.7.x shape, is refused on restore | any | True | True | - | structural_ok |

### examples/README.md

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 76 | 07-research-workflow.py takes ten to twenty seconds (the upper end is recorded) | any | 20 | 17.02 | -2.982 | machine_bound |

## Notes

- **readme.residual** (README.md:80): Tool fixed at 0.8.1: the recipe summed seven of the ten factors and graded the median, so it checked a sum no page describes.
- **presets.custom_model_fp** (docs/glossary.html:202): The digest covers every field, so it moves with each release that adds one; the page names the release it was read on (0.8.0), and 0.8.1 added none.
- **groundtruth.residual** (docs/factors.html:69): Tool fixed at 0.8.1: summed seven of the ten factors and graded the median. The glossary's 1.7e-16 is also judged here, at order of magnitude.
- **readme.workflow_wall** (examples/README.md:76): A wall clock: reported as machine_bound, never judged. The README this row first cited said five seconds; examples/README.md now says ten to twenty.
- **core.reprice_days** (docs/core-concepts.html:129): Re-keyed at 0.8.1 from reprice_days, which recorded the pt-v12 meeting days (45 and 96). Fair value grows with nominal output from pt-v18.
- **conv.short_interest_median** (docs/conventions.html:69): Tool fixed at 0.8.1: pooled ten 100-name universes inside a 0.4 band, a different sample from the one the page names.
- **conv.short_interest_tail** (docs/conventions.html:69): Tool fixed at 0.8.1: measured 'one name in N' over pooled 100-name universes; the page states a percentage over the ticker space.
- **fork.branch_ms** (docs/checkpoints.html:68): Tool fixed at 0.8.1: judged a wall clock as less_than on the gate's own hardware, over a 30-day 20-name run the page does not describe. A wall clock is machine_bound; fork.three_orders is the judged form.
- **fork.resume_s** (docs/checkpoints.html:68): A wall clock: machine_bound, never judged. The page says the timing is from one development machine under tradefloor 0.5.0.
- **scen.vix_vol_factor** (docs/presets.html:112): Bound, and not re-measured here. The vix group's short pinned recipe measures 3.17 for the same ratio, which is a different sample and horizon from the record's, so judging the page against it would report a tool difference as a moved figure.
- **scen.crisis_threshold** (docs/glossary.html:378): Tool fixed at 0.8.1: read the CRISIS_VIX_THRESHOLD source constant, which is only the dial's default.
- **scen.drawdiv_zero** (docs/glossary.html:154): Tool fixed at 0.8.1: ran the default preset where the page names pt-v14.
- **rng.snapshot_nine** (docs/glossary.html:362): Re-keyed at 0.8.1 from the snapshot length, which the page does not print.
- **rng.presplit_refused** (docs/schemas.html:201): Tool fixed at 0.8.1: cut rng to the 0.1.x one-stream shape. restore_state accepts a short rng on purpose, and what refuses a 0.7.x snapshot is its sixteen draw counts.
- **agents.sep_mom_mr** (docs/agents.html:178): The snippet also appears at docs/running-a-market.html in the code sample; the build writes both from the same fixture.
- **realism.crisis_threshold** (docs/glossary.html:378): Tool fixed at 0.8.1: read the CRISIS_VIX_THRESHOLD source constant (25.5), the dial's default, which no preset from pt-v14 uses.
