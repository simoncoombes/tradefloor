# Published-figure re-measurement

Commit `e35ce06`, 2026-09-26 13:05, tradefloor 0.8.5. Full run: 1151s wall with 64 workers.

| status | figures |
|---|---|
| reproduced | 41 |
| machine_bound | 3 |
| structural_ok | 14 |
| covered_by_tests | 5 |

## Every figure, by document

### README.md

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 118 | engine.truth(): the factor columns sum to the move to within about 1e-16 | any | 1e-16 | 1.26e-17 | - | reproduced |
| 296 | the test suite runs the numbered examples, and 07-research-workflow.py asserts its own findings | pt-v20 | True | True | - | structural_ok |

### docs/agents.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 176 | separation('mean_reversion', 'momentum') on the twelve-market grid, wins each way (bound) | pt-v20 | 7-5 | 7-5 | - | reproduced |
| 178 | the sign-test p-value of that separation (bound) | pt-v20 | 0.7744 | 0.7744 | 0 | reproduced |
| 229 | momentum's mean P&L less buy-and-hold's over the twelve-market grid (bound) | pt-v20 | -35,129 | -35,129 | 0 | reproduced |
| 229 | mean-reversion's mean P&L less buy-and-hold's over the twelve-market grid (bound) | pt-v20 | -31,797 | -31,797 | 0 | reproduced |
| 239 | markets on which mean-reversion has the highest P&L of the four ranked agents (bound) | pt-v20 | 1 | 1 | 0 | reproduced |
| 246 | markets on which mean-reversion out-earns the Oracle (bound) | pt-v20 | 1 | 1 | 0 | reproduced |

### docs/api-scenario.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 80 | a pin declared after one on the same field that begins later is refused as out of order | any | True | True | - | structural_ok |
| 88 | rate_shock is two ramps, policy rate and corporate yield, held credit_spread apart | any | True | True | - | structural_ok |
| 88 | rate_shock called on a configured scenario is refused | any | True | True | - | structural_ok |
| 89 | vix_shock is exactly a hold at the calm level, a step to the peak and a ramp back | any | True | True | - | structural_ok |

### docs/checkpoints.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 66 | tf.branch(engine, 2, ...) takes under 1 ms on a sixty-day, forty-instrument market | any | 1 | 12.66 | 11.66 | machine_bound |
| 66 | Checkpoint.resume() takes 2.7 s on the same market | any | 2.7 | 2.251 | -0.4491 | machine_bound |
| 68 | Checkpoint replay is three orders of magnitude slower than branch | any | 3 | 2.25 | -0.7499 | reproduced |

### docs/conventions.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 67 | Universe.random short interest: median over the whole three-letter ticker space, percent of shares outstanding | any | 3.45 | 3.452 | 0.00234 | reproduced |
| 67 | share of generated names whose short interest is above the 20% squeeze threshold, percent | any | 9.54 | 9.541 | 0.00142 | reproduced |
| 71 | the preset dictionary carries eight numbers | any | 8 | 8 | 0 | reproduced |

### docs/core-concepts.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 119 | vix takes a new value on every day of the 120-day run | pt-v20 | 120 | 120 | 0 | reproduced |
| 127 | corporate_bond_yield takes this many distinct values over the run | pt-v20 | 120 | 120 | 0 | reproduced |
| 135 | fundamental_value moves on every day of the run, for every name | pt-v20 | True | True | - | structural_ok |
| 143 | gdp_growth takes this many distinct values over the run | pt-v20 | 2 | 2 | 0 | reproduced |
| 151 | inflation_rate takes this many distinct values over the run | pt-v20 | 6 | 6 | 0 | reproduced |
| 159 | federal_funds_rate takes this many distinct values over the run | pt-v20 | 2 | 2 | 0 | reproduced |

### docs/core-types.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 323 | the three streams draws_by_stream() reports, in order | any | market,economy,external | market,economy,external | - | reproduced |
| 323 | draws_by_stream() reports three of the streams | any | 3 | 3 | 0 | reproduced |

### docs/evaluate.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 76 | evaluate()'s steps_per_day defaults to 6 | any | 6 | 6 | 0 | reproduced |
| 249 | reproduce() compares era digests before replaying and refuses a manifest from a build with different arithmetic | any | True | - | - | covered_by_tests |

### docs/execution-cost.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 174 | the round trip's lowest shortfall over the eight seeds | pt-v20 | 14.07 | 14.07 | -0.002767 | reproduced |
| 175 | the round trip's highest shortfall over the eight seeds | pt-v20 | 19.16 | 19.16 | -0.003729 | reproduced |
| 177 | the round trip's median shortfall over the eight seeds | pt-v20 | 16.49 | 16.49 | -0.000765 | reproduced |
| 177 | round trips with a negative shortfall, of the eight seeds | pt-v20 | 0 | 0 | 0 | reproduced |
| 183 | median absolute impact on the names it traded, bps | pt-v20 | 0.002 | 0.001963 | -3.68e-05 | reproduced |
| 183 | untouched names that still moved | pt-v20 | 3 | 3 | 0 | reproduced |
| 183 | with the VIX pinned in both worlds, no untouched name moves | pt-v20 | True | True | - | structural_ok |
| 183 | names Momentum() traded over ten days on random(60,11) at seed 7 | pt-v20 | 57 | 57 | 0 | reproduced |
| 183 | names it never touched | pt-v20 | 3 | 3 | 0 | reproduced |

### docs/factors.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 57 | the change in mispricing splits into this many named factors | any | 11 | 11 | 0 | reproduced |
| 67 | the ten factor columns sum to the change in mispricing_s, residual about 1e-16 at worst | pt-v20 | 1e-16 | 1.26e-17 | - | reproduced |

### docs/glossary.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 152 | the market-stream draw divergence compare() reports is zero in every one of the twenty-eight comparisons | pt-v14 | 0 | 0 | 0 | reproduced |
| 208 | a nudged pt-v1 (garch_alpha=0.12) fingerprints as custom-XXXXXXXX, never as pt-v1 | pt-v1 | custom-57d34290 | custom-57d34290 | - | reproduced |
| 216 | a strategy fingerprint ignores whitespace, key order and a uniform scaling of blend weights | any | True | - | - | covered_by_tests |
| 288 | the known-answer digest this build computes is the sha256 committed in tests/known_answer.json | any | True | True | - | structural_ok |
| 376 | the engine derives ten random-number streams from the root seed | any | 10 | 10 | 0 | reproduced |
| 392 | the crisis threshold from pt-v14 | pt-v14 | 30.88 | 30.88 | 0.003251 | reproduced |
| 392 | the crisis threshold before pt-v14: the sector factor blended toward the market above VIX 25.5 | pt-v12 | 25.5 | 25.5 | 0 | reproduced |
| 408 | a hundred recorded 252-day 100-instrument engines alive at once, GB of raw buffers | any | 110 | 110.1 | 0.0736 | reproduced |

### docs/presets.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 61 | the preset the package ships as its default (bound) | pt-v20 | pt-v20 | pt-v20 | - | reproduced |
| 159 | the market factor sigma is live and absent from the preset dictionary | any | True | True | - | structural_ok |
| 164 | no preset or setting changes how many draws are taken or in what order | any | True | - | - | covered_by_tests |

### docs/principles.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 202 | momentum's highest per-market P&L less buy-and-hold's on the grid (bound) | pt-v20 | 18,989 | 18,989 | 0 | reproduced |
| 202 | momentum's lowest per-market P&L less buy-and-hold's on the grid (bound) | pt-v20 | -82,311 | -82,311 | 0 | reproduced |

### docs/realism-envelope.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 797 | the crisis lever: a market held at VIX 65 against one held at VIX 5, ratio of annualised volatility (bound) | pt-v19 | 5.221 | 3.816 | - | covered_by_tests |

### docs/rl-environment.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 77 | TradingEnv passes gymnasium's env_checker | any | True | - | - | covered_by_tests |

### docs/running-a-market.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 107 | a reversed roster fingerprints differently | any | True | True | - | structural_ok |
| 252 | the one-day momentum signal rebalanced three times a day returns this over 30 days (bound) | pt-v20 | -7.9 | -7.9 | 0 | reproduced |
| 261 | the same signal rebalanced six times a day (bound) | pt-v20 | -11.5 | -11.5 | 0 | reproduced |
| 270 | the same signal rebalanced twelve times a day (bound) | pt-v20 | -21.99 | -21.99 | 0 | reproduced |

### docs/scenarios.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 183 | two pins on one field that begin on the same day are refused, in either order | any | True | True | - | structural_ok |

### docs/schemas.html

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 189 | vix_shock chained onto a built scenario is refused | any | True | True | - | structural_ok |
| 189 | compare() refuses a baseline that realises the same path as the scenario | any | True | True | - | structural_ok |
| 199 | a snapshot carrying eight streams, the 0.7.x shape, is refused on restore | any | True | True | - | structural_ok |

### examples/README.md

| line | figure | preset | published | measured | delta | status |
|---|---|---|---|---|---|---|
| 76 | 07-research-workflow.py takes ten to twenty seconds (the upper end is recorded) | any | 20 | 90.26 | 70.26 | machine_bound |

## Notes

- **readme.residual** (README.md:118): Tool fixed at 0.8.1: the recipe summed seven of the ten factors and graded the median, so it checked a sum no page describes.
- **presets.custom_model_fp** (docs/glossary.html:208): The digest covers every field, so it moves with each release that adds one; the page names the release it was read on. It read custom-d70ecdf0 on 0.8.0 and 0.8.1, and custom-fa81d418 on 0.8.5, which adds settable dials. It reads custom-a0c09b02 at the 0.8.5 release commit (bc6361c): custom-fa81d418 was read earlier in the 0.8.5 cycle, before the last fields it adds.
- **groundtruth.residual** (docs/factors.html:67): Tool fixed at 0.8.1: summed seven of the ten factors and graded the median. From 0.8.5 the sum is over eleven factors, fair_value_shift last. The glossary's figure is measured on Universe.random(20, seed=3) (seed 42, five days, 39,000 rows): 1.7e-16 on pt-v19, restated 2026-09-26 as 1.0e-17 on pt-v20. It is judged here at order of magnitude, beside this recipe's random(20, 11), where pt-v20 reads 2.7e-17.
- **readme.workflow_wall** (examples/README.md:76): A wall clock: reported as machine_bound, never judged. The README this row first cited said five seconds; examples/README.md now says ten to twenty.
- **core.reprice_days** (docs/core-concepts.html:135): Re-keyed at 0.8.1 from reprice_days, which recorded the pt-v12 meeting days (45 and 96). Fair value grows with nominal output from pt-v18.
- **conv.short_interest_median** (docs/conventions.html:67): Tool fixed at 0.8.1: pooled ten 100-name universes inside a 0.4 band, a different sample from the one the page names.
- **conv.short_interest_tail** (docs/conventions.html:67): Tool fixed at 0.8.1: measured 'one name in N' over pooled 100-name universes; the page states a percentage over the ticker space.
- **fork.branch_ms** (docs/checkpoints.html:66): Tool fixed at 0.8.1: judged a wall clock as less_than on the gate's own hardware, over a 30-day 20-name run the page does not describe. A wall clock is machine_bound; fork.three_orders is the judged form.
- **fork.resume_s** (docs/checkpoints.html:66): A wall clock: machine_bound, never judged. The page says the timing is from one development machine under tradefloor 0.5.0.
- **scen.vix_vol_factor** (docs/realism-envelope.html:797): Bound, and not re-measured here. The vix group's short pinned recipe measures 3.17 for the same ratio, which is a different sample and horizon from the record's, so judging the page against it would report a tool difference as a moved figure. Re-pointed at 0.8.5: the presets card dropped the crisis lever on 2026-09-24 (24a0880), and the realism envelope's known shortfalls carry it.
- **scen.crisis_threshold** (docs/glossary.html:392): Tool fixed at 0.8.1: read the CRISIS_VIX_THRESHOLD source constant, which is only the dial's default.
- **scen.drawdiv_zero** (docs/glossary.html:152): Tool fixed at 0.8.1: ran the default preset where the page names pt-v14.
- **rng.snapshot_nine** (docs/glossary.html:376): Re-keyed at 0.8.1 from the snapshot length, which the page does not print.
- **rng.presplit_refused** (docs/schemas.html:199): Tool fixed at 0.8.1: cut rng to the 0.1.x one-stream shape. restore_state accepts a short rng on purpose, and what refuses a 0.7.x snapshot is its sixteen draw counts.
- **agents.sep_mom_mr** (docs/agents.html:176): The snippet also appears at docs/running-a-market.html in the code sample; the build writes both from the same fixture.
- **realism.crisis_threshold** (docs/glossary.html:392): Tool fixed at 0.8.1: read the CRISIS_VIX_THRESHOLD source constant (25.5), the dial's default, which no preset from pt-v14 uses.
