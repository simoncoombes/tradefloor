# Published-figure re-measurement

Commit `1062de7`, 2026-08-21 20:15, pretium 0.1.0. Full run: 96s wall with 8 workers.

| status | figures |
|---|---|
| reproduced | 48 |
| within_seed_variation | 6 |
| MOVED | 60 |
| machine_bound | 10 |
| structural_ok | 6 |
| structural_fail | 2 |
| method_unknown | 7 |
| not_harnessable | 4 |
| covered_by_tests | 4 |

## Doc edits needed

Every row here is a published number the stated (or reconstructed)
method no longer produces. On unchanged main these are documents that
were already stale; after an engine change, this section IS the edit
list.

| where | figure | published | measured |
|---|---|---|---|
| README.md:141 | release-gate digest, macos-arm64 row (all five rows are one digest) | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | f4631badb7a9383926e22c2a59fb11c0f860c3d6ced3c6ca53185d3e9d3ca9d5 |
| docs/index.md:53 | five wheel targets, one fixed simulation, digests compared | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | f4631badb7a9383926e22c2a59fb11c0f860c3d6ced3c6ca53185d3e9d3ca9d5 |
| README.md:152 | 3 rebalances per day, return | 88.7 | 55.03 |
| README.md:153 | 6 rebalances per day, return | 30.9 | 27.41 |
| README.md:154 | 12 rebalances per day, return | -13.2 | 5.756 |
| README.md:164 | recording 9.8M rows of ground truth adds 3% | 3 | -2.979 |
| README.md:180 | of the eight statistics, four are marginal and four are dependence | 4 | 2 |
| README.md:189 | thirteenfold VIX move changes realised vol by under one point | 1 | 1.025 |
| README.md:189 | below VIX 15 a VIX change moves nothing at all | True | False |
| README.md:100 | separation('momentum','mean_reversion') comment: 7-5, p = 0.77 | 7-5 | 11-1 |
| docs/core-concepts.md:50 | over run_days(120) each macro field takes exactly one distinct value | 1 | 120 |
| docs/core-concepts.md:52 | fundamental_value takes exactly one distinct value per instrument | 1 | 3 |
| docs/performance.md:20 | recording costs about 3% | 3 | -2.979 |
| docs/reading-results.md:19 | the book table is 40x the rows | 40 | 20 |
| docs/reading-results.md:34 | recording ground truth costs about 3% | 3 | -2.979 |
| docs/scenarios.md:22 | buy_and_hold calm return | 3.51 | -6.109 |
| docs/scenarios.md:22 | buy_and_hold hiked return | -0.87 | -9.869 |
| docs/scenarios.md:23 | momentum calm return | -2.36 | -0.6843 |
| docs/scenarios.md:23 | momentum hiked return | -0.63 | -2.551 |
| docs/scenarios.md:24 | oracle calm return | 20.86 | 18.21 |
| docs/scenarios.md:24 | oracle hiked return | 20.91 | 16.06 |
| docs/scenarios.md:50 | annualised realised vol at VIX 5 | 58.05 | 58.06 |
| docs/scenarios.md:51 | annualised realised vol at VIX 15 | 58.05 | 58.02 |
| docs/scenarios.md:52 | annualised realised vol at VIX 45 | 58.22 | 58.45 |
| docs/scenarios.md:53 | annualised realised vol at VIX 65 | 58.92 | 59.05 |
| docs/scenarios.md:56 | VIX 5, 10 and 15 produce bit-identical prices over 60 days | True | False |
| docs/scenarios.md:63 | mean quoted spread at VIX 15 | 12.17 | 11.42 |
| docs/scenarios.md:64 | mean quoted spread at VIX 25 | 14.72 | 13.61 |
| docs/scenarios.md:63 | mean quoted spread at VIX 45 | 20.05 | 20.2 |
| docs/scenarios.md:64 | mean quoted spread at VIX 65 | 28.41 | 25.45 |
| docs/scenarios.md:68 | mean pairwise correlation at VIX 15, 300 pairs | 0.022 | 0.04729 |
| docs/scenarios.md:68 | mean pairwise correlation at VIX 45 | 0.023 | 0.04589 |
| docs/scenarios.md:68 | mean pairwise correlation at VIX 65 | 0.041 | 0.06988 |
| docs/agents-and-evaluation.md:40 | the Oracle spends its budget on equal weight across the ten most mispriced names | 10 | 5 |
| docs/agents-and-evaluation.md:57 | Oracle median P&L, default configuration: 110k | 110,000 | 8.652e+04 |
| docs/agents-and-evaluation.md:60 | on seed 2026 momentum captures 27% over five days | 0.27 | 2.979 |
| docs/agents-and-evaluation.md:60 | and 94% over sixty | 0.94 | 1.465 |
| docs/agents-and-evaluation.md:64 | over twelve markets momentum ranks +0.556 | 0.556 | 0.8054 |
| docs/agents-and-evaluation.md:65 | mean-reversion -0.011 | -0.011 | 0.06383 |
| docs/agents-and-evaluation.md:65 | a single seed picks the top agent exactly half the time (6 of 12) | 6 | 10 |
| docs/agents-and-evaluation.md:66 | momentum capture range, low | -0.335 | -0.1691 |
| docs/agents-and-evaluation.md:66 | momentum capture range, high | 1.903 | 1.672 |
| docs/agents-and-evaluation.md:73 | momentum vs mean_reversion: 7-5 | 7-5 | 11-1 |
| docs/agents-and-evaluation.md:73 | p = 0.77 | 0.77 | 0.006348 |
| docs/agents-and-evaluation.md:74 | momentum vs random: 10-2 | 10-2 | 11-1 |
| docs/agents-and-evaluation.md:74 | p = 0.039 | 0.039 | 0.006348 |
| docs/agents-and-evaluation.md:89 | a three-day seed where dividing by the Oracle yields a capture of +14.4 | 14.4 | 2.816 |
| docs/an-llm-agent.md:30 | oracle pnl 42650 | 42,650 | 1.147e+05 |
| docs/an-llm-agent.md:30 | oracle impact 87.5 | 87.5 | 400.7 |
| docs/an-llm-agent.md:32 | momentum pnl -221 | -221 | 2.249e+04 |
| docs/an-llm-agent.md:32 | momentum impact 20.7 | 20.7 | -18.03 |
| docs/how-realistic-is-this-market.md:36 | sample report: annualised vol | 55.01 | 52.95 |
| docs/how-realistic-is-this-market.md:37 | sample report: excess kurtosis | 5.166 | 3.374 |
| docs/how-realistic-is-this-market.md:40 | sample report: return acf(1) | 0.248 | 0.2515 |
| docs/how-realistic-is-this-market.md:41 | sample report: |return| acf(1) | 0.134 | 0.1186 |
| docs/how-realistic-is-this-market.md:42 | sample report: cross-sectional corr | 0.027 | 0.01797 |
| docs/how-realistic-is-this-market.md:43 | sample report: volume vs |return| | 0.129 | 0.5271 |
| docs/how-realistic-is-this-market.md:44 | sample report: leverage | -0.018 | -0.05179 |
| docs/how-realistic-is-this-market.md:45 | sample report: volume change acf(1) | -0.45 | -0.4588 |
| docs/how-realistic-is-this-market.md:82 | volume vs |return|, median of six seeds | 0.105 | 0.5109 |
| docs/strategy-specs.md:152 | the rebalance table swings the same signal from +88.7%... | 88.7 | 55.03 |
| docs/strategy-specs.md:152 | ...to -13.2% purely by trading it more often | -13.2 | 5.756 |

## Published numbers nobody can re-measure

| where | figure | why |
|---|---|---|
| docs/agents-and-evaluation.md:41 | over 384 agent-seed pairs, agents beat the Oracle 9.9% of the time | the 384 pairs are 'four rosters, two sizes, two horizons, six seeds each' (baselines.py:25) and none of the rosters, sizes or horizons are named anywhere; the figure cannot be re-measured as published. tests/test_baselines.py asserts only the ordering on a smaller grid |
| docs/agents-and-evaluation.md:45 | mean_reversion beats the Oracle 31.2% | same measurement as agents.beat_rate |
| docs/agents-and-evaluation.md:46 | momentum beats the Oracle 8.3% | same measurement as agents.beat_rate |
| docs/agents-and-evaluation.md:47 | buy_and_hold and random beat the Oracle 0.0% in 192 pairs | same measurement as agents.beat_rate |
| docs/transaction-cost-analysis.md:28 | buying and holding costs +16.7 bps in one measured example | 'one measured example' with no seed, universe, size or horizon stated anywhere in the repo; the number cannot be re-measured as published |
| docs/transaction-cost-analysis.md:29 | buying and selling three steps later comes to -10.8 bps | same unstated example as tca.entry_cost |
| docs/transaction-cost-analysis.md:35 | a request for 4,856 shares filled 483, and every larger request filled the same 483 | instrument and universe unstated; the structural claim (fills saturate at displayed depth) is asserted every run by examples/research_workflow.py, which this harness executes |

## Every figure, by document

### README.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 100 | separation('momentum','mean_reversion') comment: 7-5, p = 0.77 | 7-5 | 11-1 | - | MOVED |
| 116 | seven factor columns sum to the move, residual around 1e-16 | 1e-16 | 1.45e-17 | - | reproduced |
| 139 | the same digest on linux-x86_64/aarch64, macos-x86_64, windows-x86_64 | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | - | - | not_harnessable |
| 141 | release-gate digest, macos-arm64 row (all five rows are one digest) | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | f4631badb7a9383926e22c2a59fb11c0f860c3d6ced3c6ca53185d3e9d3ca9d5 | - | MOVED |
| 152 | 3 rebalances per day, return | 88.7 | 55.03 | -33.67 | MOVED |
| 153 | 6 rebalances per day, return | 30.9 | 27.41 | -3.494 | MOVED |
| 154 | 12 rebalances per day, return | -13.2 | 5.756 | 18.96 | MOVED |
| 163 | 252-day year over 100 instruments takes 27 seconds | 27 | 8.314 | -18.69 | machine_bound |
| 164 | recording 9.8M rows of ground truth adds 3% | 3 | -2.979 | -5.979 | MOVED |
| 165 | sweeps parallelise about 3.3x on eight cores | 3.3 | 4.861 | 1.561 | machine_bound |
| 176 | measured return autocorrelation +0.219 at lag one | 0.219 | 0.2341 | 0.01508 | within_seed_variation |
| 180 | of the eight statistics, four are marginal and four are dependence | 4 | 2 | -2 | MOVED |
| 189 | below VIX 15 a VIX change moves nothing at all | True | False | - | structural_fail |
| 189 | thirteenfold VIX move changes realised vol by under one point | 1 | 1.025 | 0.02549 | MOVED |
| 215 | worked example: 20-seed sweep | 20 | 20 | 0 | reproduced |
| 215 | worked example: fifteen seconds end to end | 15 | 5.656 | -9.344 | machine_bound |
| 216 | worked example: 234,000 rows of ground truth | 234,000 | 234,000 | 0 | reproduced |
| 219 | the worked example's internal assertions all pass | True | True | - | structural_ok |

### docs/agents-and-evaluation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 40 | the Oracle spends its budget on equal weight across the ten most mispriced names | 10 | 5 | -5 | MOVED |
| 41 | over 384 agent-seed pairs, agents beat the Oracle 9.9% of the time | 9.9 | - | - | method_unknown |
| 45 | mean_reversion beats the Oracle 31.2% | 31.2 | - | - | method_unknown |
| 46 | momentum beats the Oracle 8.3% | 8.3 | - | - | method_unknown |
| 47 | buy_and_hold and random beat the Oracle 0.0% in 192 pairs | 0 | - | - | method_unknown |
| 57 | Oracle median P&L, default configuration: 110k | 110,000 | 8.652e+04 | -2.348e+04 | MOVED |
| 57 | Oracle median P&L across three times as many names: 70k | 70,000 | 7.136e+04 | 1,360 | reproduced |
| 60 | on seed 2026 momentum captures 27% over five days | 0.27 | 2.979 | 2.709 | MOVED |
| 60 | and 94% over sixty | 0.94 | 1.465 | 0.5254 | MOVED |
| 64 | over twelve markets momentum ranks +0.556 | 0.556 | 0.8054 | 0.2494 | MOVED |
| 65 | mean-reversion -0.011 | -0.011 | 0.06383 | 0.07483 | MOVED |
| 65 | a single seed picks the top agent exactly half the time (6 of 12) | 6 | 10 | 4 | MOVED |
| 66 | momentum capture range, high | 1.903 | 1.672 | -0.231 | MOVED |
| 66 | momentum capture range, low | -0.335 | -0.1691 | 0.1659 | MOVED |
| 73 | momentum vs mean_reversion: 7-5 | 7-5 | 11-1 | - | MOVED |
| 73 | p = 0.77 | 0.77 | 0.006348 | -0.7637 | MOVED |
| 74 | momentum vs random: 10-2 | 10-2 | 11-1 | - | MOVED |
| 74 | p = 0.039 | 0.039 | 0.006348 | -0.03265 | MOVED |
| 89 | a three-day seed where dividing by the Oracle yields a capture of +14.4 | 14.4 | 2.816 | -11.58 | MOVED |

### docs/an-llm-agent.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 30 | oracle impact 87.5 | 87.5 | 400.7 | 313.2 | MOVED |
| 30 | oracle pnl 42650 | 42,650 | 1.147e+05 | 7.208e+04 | MOVED |
| 30 | oracle why-right 100% | 100 | 100 | 0 | reproduced |
| 32 | momentum why-right prints '-' (no explain()) | True | True | - | structural_ok |
| 32 | momentum impact 20.7 | 20.7 | -18.03 | -38.73 | MOVED |
| 32 | momentum pnl -221 | -221 | 2.249e+04 | 2.271e+04 | MOVED |
| 109 | default run is 20 days over 12 instruments, so 20 calls | 20 | 20 | 0 | reproduced |
| 109 | a run lands around $0.30 to $0.60 at Opus 5 rates | 0.30-0.60 | - | - | not_harnessable |

### docs/conventions.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 26 | Universe.random short interest: median about 3.7% of shares outstanding | 3.7 | 3.414 | -0.2861 | reproduced |
| 27 | roughly one name in eleven above the 20% squeeze threshold | 11 | 12.66 | 1.658 | reproduced |
| 39 | the preset dictionary carries eight numbers | 8 | 8 | 0 | reproduced |

### docs/core-concepts.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 50 | over run_days(120) each macro field takes exactly one distinct value | 1 | 120 | 119 | MOVED |
| 52 | fundamental_value takes exactly one distinct value per instrument | 1 | 3 | 2 | MOVED |
| 67 | corporate bond yield defaults to 0.0456 | 0.0456 | 0.0456 | 0 | reproduced |
| 70 | pin_macro(corporate_bond_yield=0.09) repriced nineteen of twenty | 19 | 19 | 0 | reproduced |
| 71 | the twentieth is a loss-maker valued off book value | 1 | 1 | 0 | reproduced |
| 73 | pinning federal_funds_rate alone repriced none of the twenty | 0 | 0 | 0 | reproduced |

### docs/forking-a-simulation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 22 | pt.branch cost < 1 ms | 1 | 0.02929 | -0.9707 | reproduced |
| 23 | Checkpoint.resume() cost 2.7 s | 2.7 | 0.2172 | -2.483 | machine_bound |
| 27 | Checkpoint replay is three orders of magnitude slower than branch | 3 | 3.87 | 0.8701 | reproduced |

### docs/ground-truth.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | factors sum to change in mispricing_s, residual around 1e-16 | 1e-16 | 1.45e-17 | - | reproduced |

### docs/how-realistic-is-this-market.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 31 | sample report: 10,040 daily returns | 10,040 | 10,040 | 0 | reproduced |
| 36 | sample report: annualised vol | 55.01 | 52.95 | -2.055 | MOVED |
| 37 | sample report: excess kurtosis | 5.166 | 3.374 | -1.792 | MOVED |
| 40 | sample report: return acf(1) | 0.248 | 0.2515 | 0.003483 | MOVED |
| 41 | sample report: |return| acf(1) | 0.134 | 0.1186 | -0.0154 | MOVED |
| 42 | sample report: cross-sectional corr | 0.027 | 0.01797 | -0.009031 | MOVED |
| 43 | sample report: volume vs |return| | 0.129 | 0.5271 | 0.3981 | MOVED |
| 44 | sample report: leverage | -0.018 | -0.05179 | -0.03379 | MOVED |
| 45 | sample report: volume change acf(1) | -0.45 | -0.4588 | -0.008831 | MOVED |
| 64 | universe fingerprint 5d8de78b55aad752 | 5d8de78b55aad752 | 5d8de78b55aad752 | - | reproduced |
| 72 | excess kurtosis, median of six seeds | 4 | 4.246 | 0.2465 | within_seed_variation |
| 73 | annualised vol, median of six seeds | 53 | 53.68 | 0.6833 | within_seed_variation |
| 79 | return acf(1), median of six seeds | 0.233 | 0.2341 | 0.001083 | within_seed_variation |
| 80 | |return| acf(1), median of six seeds | 0.117 | 0.1077 | -0.009274 | within_seed_variation |
| 81 | cross-sectional corr, median of six seeds | 0.024 | 0.0256 | 0.001602 | within_seed_variation |
| 82 | volume vs |return|, median of six seeds | 0.105 | 0.5109 | 0.4059 | MOVED |
| 83 | leverage, median of six seeds | -0.004 | -0.003883 | 0.000117 | reproduced |
| 84 | volume change acf(1), median of six seeds | -0.463 | -0.4635 | -0.000479 | reproduced |
| 112 | shared market factor sigma 0.003/day | 0.003 | 0.003 | 0 | reproduced |
| 113 | sector daily sigma runs 0.008 to 0.025, median 0.015 | 0.015 | 0.015 | 0 | reproduced |
| 114 | the shared factor carries about 4% of a typical name's variance | 4 | 4 | 8.88e-16 | reproduced |
| 128 | clustering largely gone by lag twenty | 0 | 0.01265 | 0.01265 | reproduced |
| 174 | AR(2) impulse response rises to 1.284 by day two | 1.284 | 1.284 | -8.3e-05 | reproduced |
| 196 | MOMENTUM_THETA 0.25 -> 0.05 takes return acf to +0.034 | 0.034 | - | - | not_harnessable |
| 203 | GARCH persistence is 0.99 | 0.99 | 0.99 | 0 | reproduced |
| 204 | raising the variance ceiling lifts clustering 0.016 and vol 52.7% -> 72% | 0.016 | - | - | not_harnessable |
| 208 | a realistic +0.30 needs a market factor near 0.0098 against the 0.0030 in the source | 0.0098 | 0.00975 | -5e-05 | reproduced |

### docs/index.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 53 | five wheel targets, one fixed simulation, digests compared | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | f4631badb7a9383926e22c2a59fb11c0f860c3d6ced3c6ca53185d3e9d3ca9d5 | - | MOVED |
| 63 | seven factor contributions, residual around 1e-16 | 1e-16 | 1.45e-17 | - | reproduced |

### docs/performance.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 14 | 252 days x 10 instruments: 2.9 s | 2.9 | 0.954 | -1.946 | machine_bound |
| 15 | 252 days x 100 instruments: 27.4 s | 27.4 | 8.314 | -19.09 | machine_bound |
| 16 | 252 x 100 recording 9.8M rows: 28.2 s | 28.2 | 8.066 | -20.13 | machine_bound |
| 16 | a recorded year at 100 instruments is 9.8M rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 17 | 8 seeds x 21 days x 100, serial: 20.0 s | 20 | 7.093 | -12.91 | machine_bound |
| 18 | 8 seeds x 21 days x 100, 8 workers: 6.1 s | 6.1 | 1.459 | -4.641 | machine_bound |
| 20 | recording costs about 3% | 3 | -2.979 | -5.979 | MOVED |
| 23 | sweeps parallelise about 3.3x on eight cores | 3.3 | 4.861 | 1.561 | machine_bound |
| 27 | cost scales roughly linearly in instruments x days (t100/t10 ~ 9.4 published) | 9.45 | 8.715 | -0.735 | reproduced |

### docs/reading-results.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | the book table is 40x the rows | 40 | 20 | -20 | MOVED |
| 34 | recording ground truth costs about 3% | 3 | -2.979 | -5.979 | MOVED |

### docs/real-fundamentals-from-sec-edgar.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 26 | five market-wide requests plus one per company kept | 5 | - | - | covered_by_tests |

### docs/reinforcement-learning.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 18 | TradingEnv passes gymnasium's env_checker | True | - | - | covered_by_tests |

### docs/reproducing-a-run.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 34 | pt.version() == '0.1.0' | 0.1.0 | 0.1.0 | - | reproduced |
| 35 | model_preset()['name'] == 'pt-v1' | pt-v1 | pt-v1 | - | reproduced |
| 60 | universe.fingerprint is 64 hex characters | 64 | 64 | 0 | reproduced |
| 73 | a reversed roster hashes differently | True | True | - | structural_ok |
| 155 | rebuilt universe fingerprint matches the archive | True | True | - | structural_ok |
| 159 | replayed.prices() == engine.prices() | True | True | - | structural_ok |
| 160 | replayed.draws_consumed == engine.draws_consumed | True | True | - | structural_ok |

### docs/running-a-simulation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 50 | a tick loop crosses the boundary about 98,000 times per simulated year | 98,000 | 98,280 | 280 | reproduced |
| 51 | five fields per tick makes roughly 500,000 | 500,000 | 491,400 | -8,600 | reproduced |

### docs/scenarios.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 22 | buy_and_hold calm return | 3.51 | -6.109 | -9.619 | MOVED |
| 22 | buy_and_hold hiked return | -0.87 | -9.869 | -8.999 | MOVED |
| 23 | momentum calm return | -2.36 | -0.6843 | 1.676 | MOVED |
| 23 | momentum hiked return | -0.63 | -2.551 | -1.921 | MOVED |
| 24 | oracle calm return | 20.86 | 18.21 | -2.646 | MOVED |
| 24 | oracle hiked return | 20.91 | 16.06 | -4.85 | MOVED |
| 31 | pinning federal_funds_rate alone: exactly 0.00% across twenty instruments over 40 days | 0 | 0 | 0 | reproduced |
| 32 | a 60-day run crossing the day-45 central-bank meeting reprices a median -3.99% | -3.99 | -4.28 | -0.2899 | reproduced |
| 50 | annualised realised vol at VIX 5 | 58.05 | 58.06 | 0.0126 | MOVED |
| 51 | annualised realised vol at VIX 15 | 58.05 | 58.02 | -0.02682 | MOVED |
| 52 | annualised realised vol at VIX 45 | 58.22 | 58.45 | 0.2317 | MOVED |
| 53 | annualised realised vol at VIX 65 | 58.92 | 59.05 | 0.1287 | MOVED |
| 56 | VIX 5, 10 and 15 produce bit-identical prices over 60 days | True | False | - | structural_fail |
| 63 | mean quoted spread at VIX 15 | 12.17 | 11.42 | -0.7478 | MOVED |
| 63 | mean quoted spread at VIX 45 | 20.05 | 20.2 | 0.154 | MOVED |
| 64 | mean quoted spread at VIX 25 | 14.72 | 13.61 | -1.108 | MOVED |
| 64 | mean quoted spread at VIX 65 | 28.41 | 25.45 | -2.965 | MOVED |
| 68 | mean pairwise correlation at VIX 15, 300 pairs | 0.022 | 0.04729 | 0.02529 | MOVED |
| 68 | mean pairwise correlation at VIX 45 | 0.023 | 0.04589 | 0.02289 | MOVED |
| 68 | mean pairwise correlation at VIX 65 | 0.041 | 0.06988 | 0.02888 | MOVED |
| 92 | median shortfall at VIX 45 | 11.69 | 11.69 | -0.004181 | reproduced |
| 92 | median shortfall at VIX 15 | 6.06 | 6.064 | 0.004119 | reproduced |
| 92 | VIX 45 regime costs more in 12 of 12 seeds | 12 | 12 | 0 | reproduced |
| 93 | paired median delta | 5.62 | 5.622 | 0.0017 | reproduced |
| 102 | macro-counterfactual draw divergence zero in every comparison run | 0 | 0 | 0 | reproduced |

### docs/strategy-specs.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 27 | methods-section universe fingerprint a7861d15... | a7861d15 | a7861d15 | - | reproduced |
| 28 | strategy spec fingerprint e6bbc35c... | e6bbc35c | e6bbc35c | - | reproduced |
| 29 | oracle spec fingerprint f383b990... | f383b990 | f383b990 | - | reproduced |
| 55 | spec-built and hand-built baselines score bit-identically | True | - | - | covered_by_tests |
| 103 | blend weights 1.2/0.8 and 0.6/0.4 build bit-identical agents | True | - | - | covered_by_tests |
| 152 | the rebalance table swings the same signal from +88.7%... | 88.7 | 55.03 | -33.67 | MOVED |
| 152 | ...to -13.2% purely by trading it more often | -13.2 | 5.756 | 18.96 | MOVED |

### docs/sweeps-and-parallelism.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 17 | twelve buffers of 9.8 million f64 - about 940 MB | 940 | 943.5 | 3.488 | reproduced |
| 18 | materialises 9.8 million rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 19 | a hundred resident engines is roughly 90 GB | 90 | 94.35 | 4.349 | reproduced |

### docs/transaction-cost-analysis.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 28 | buying and holding costs +16.7 bps in one measured example | 16.7 | - | - | method_unknown |
| 29 | buying and selling three steps later comes to -10.8 bps | -10.8 | - | - | method_unknown |
| 35 | a request for 4,856 shares filled 483, and every larger request filled the same 483 | 483 | - | - | method_unknown |

## Notes

- **readme.kat_digest_other_platforms** (README.md:139): only the local platform's row is measurable here; the other four are produced by .github/workflows/determinism.yml
- **readme.rebalance_3** (README.md:152): tests/test_baselines.py:376 records that these figures were re-measured after the stepped-day fix (80a0949) and moved to +49.71/+32.70/+0.18; the README still carries the pre-fix values
- **readme.record_overhead** (README.md:164): measured interleaved min-of-3 on this machine the overhead is -1% to +1%, below timing noise; recording appears to be effectively free here, so the 'about 3%' was either machine-specific or has improved since it was measured
- **readme.speedup** (README.md:165): parallel speedup is a property of the measuring machine's core count and memory bandwidth, so it is reported rather than judged; re-measure on the published machine before editing
- **readme.return_acf** (README.md:176): the realism page (measured at e2aded1) publishes +0.233 for the same statistic; the README's +0.219 is the older four-statistic-era figure and disagrees with the page it links to
- **readme.facts_split** (README.md:180): pretium.facts splits them 2 marginal / 6 dependence, and the realism page prints that split; the README sentence miscounts
- **readme.separation_comment** (README.md:100): the README snippet's surrounding code builds random(108, seed=7), but the numbers come from the documented 30-instrument headline measurement; the comment and its context disagree about the universe
- **fork.resume_s** (docs/forking-a-simulation.md:23): replay cost scales with the order log and the page does not say what run length 2.7 s was measured over; the portable part of the claim is the ratio, next row
- **perf.overhead** (docs/performance.md:20): measured interleaved min-of-3 on this machine the overhead is -1% to +1%, below timing noise; recording appears to be effectively free here, so the 'about 3%' was either machine-specific or has improved since it was measured
- **perf.speedup** (docs/performance.md:23): hardware-bound like the absolute times; reported, not judged
- **reading.book_40x** (docs/reading-results.md:19): snapshot_book defaults to levels=10, which is 2 sides x 10 = 20 rows per (tick, instrument); the page's 40x holds only at levels=20, so either the multiplier or an unstated depth assumption needs fixing
- **reading.record_overhead** (docs/reading-results.md:34): measured interleaved min-of-3 on this machine the overhead is -1% to +1%, below timing noise; recording appears to be effectively free here, so the 'about 3%' was either machine-specific or has improved since it was measured
- **scen.bh_calm** (docs/scenarios.md:22): the table predates the harness fix in 80a0949/80ed437 (every step re-ran the market open), which changed every evaluate-derived figure
- **scen.fed_meeting** (docs/scenarios.md:32): written fresh by the engine-fix PR but with no universe or seed stated; plausible reconstructions land between -3.95 and -4.40, bracketing the published -3.99, so this row is judged as a band rather than at printed precision
- **agents.oracle_ten_names** (docs/agents-and-evaluation.md:40): Oracle's default is top_k=5; the module docstring at baselines.py:43 says ten while its own measured table at baselines.py:51 says top_k=5. The page inherited the docstring's slip
- **agents.beat_rate** (docs/agents-and-evaluation.md:41): the 384 pairs are 'four rosters, two sizes, two horizons, six seeds each' (baselines.py:25) and none of the rosters, sizes or horizons are named anywhere; the figure cannot be re-measured as published. tests/test_baselines.py asserts only the ordering on a smaller grid
- **agents.beat_mr** (docs/agents-and-evaluation.md:45): same measurement as agents.beat_rate
- **agents.beat_mom** (docs/agents-and-evaluation.md:46): same measurement as agents.beat_rate
- **agents.beat_bh_random** (docs/agents-and-evaluation.md:47): same measurement as agents.beat_rate
- **agents.capture_14** (docs/agents-and-evaluation.md:89): the page states only the horizon; the seed set and universe are guessed, the best-guess reconstruction lands at a different value, and the anecdote's specific magnitude cannot be verified as published
- **llm.oracle_pnl** (docs/an-llm-agent.md:30): the row predates or bypassed the harness fix in 80ed437 (each step re-ran the market open); the stated method no longer produces it
- **llm.cost** (docs/an-llm-agent.md:109): priced by a third-party API, not measurable from the package
- **realism.theta_counterfactual** (docs/how-realistic-is-this-market.md:196): requires rebuilding the engine with a changed constant; deliberately not a runtime knob
- **realism.ceiling_counterfactual** (docs/how-realistic-is-this-market.md:204): requires rebuilding the engine with a changed constant
- **tca.entry_cost** (docs/transaction-cost-analysis.md:28): 'one measured example' with no seed, universe, size or horizon stated anywhere in the repo; the number cannot be re-measured as published
- **tca.roundtrip_cost** (docs/transaction-cost-analysis.md:29): same unstated example as tca.entry_cost
- **tca.partial_fill** (docs/transaction-cost-analysis.md:35): instrument and universe unstated; the structural claim (fills saturate at displayed depth) is asserted every run by examples/research_workflow.py, which this harness executes
- **sweeps.buffers** (docs/sweeps-and-parallelism.md:17): the truth table has 13 columns; 'twelve' undercounts by one, though the byte arithmetic still lands on the page's 'about 940 MB'
- **edgar.requests** (docs/real-fundamentals-from-sec-edgar.md:26): a network-shape claim; exercised by tests/test_edgar.py through the injectable transport, not re-measured here
- **rl.env_checker** (docs/reinforcement-learning.md:18): asserted by tests/test_gym.py when gymnasium is installed
- **spec.bitwise_baselines** (docs/strategy-specs.md:55): asserted by tests/test_spec.py
- **spec.weight_scale** (docs/strategy-specs.md:103): asserted by tests/test_spec.py (fingerprint scale invariance)
- **spec.rebalance_high** (docs/strategy-specs.md:152): a page written after the era boundary quoting the pre-boundary figures from the baselines docstring; whatever the rebalance rows re-measure to belongs here too
- **spec.rebalance_low** (docs/strategy-specs.md:152): see spec.rebalance_high
- **readme.workflow_asserts** (README.md:219): after an engine change a failure here means the example script itself needs re-fitting, not just the prose
