# Published-figure re-measurement

Commit `a5afd1c`, 2026-08-21 20:05, pretium 0.1.0. Full run: 83s wall with 8 workers.

| status | figures |
|---|---|
| reproduced | 76 |
| within_seed_variation | 1 |
| MOVED | 31 |
| machine_bound | 10 |
| structural_ok | 7 |
| method_unknown | 7 |
| not_harnessable | 4 |
| covered_by_tests | 2 |

## Doc edits needed

Every row here is a published number the stated (or reconstructed)
method no longer produces. On unchanged main these are documents that
were already stale; after an engine change, this section IS the edit
list.

| where | figure | published | measured |
|---|---|---|---|
| README.md:152 | 3 rebalances per day, return | 88.7 | 57.35 |
| README.md:153 | 6 rebalances per day, return | 30.9 | 28.83 |
| README.md:154 | 12 rebalances per day, return | -13.2 | 5.311 |
| README.md:164 | recording 9.8M rows of ground truth adds 3% | 3 | -5.434 |
| README.md:180 | of the eight statistics, four are marginal and four are dependence | 4 | 2 |
| docs/performance.md:20 | recording costs about 3% | 3 | -5.434 |
| docs/reading-results.md:19 | the book table is 40x the rows | 40 | 20 |
| docs/reading-results.md:34 | recording ground truth costs about 3% | 3 | -5.434 |
| docs/scenarios.md:22 | buy_and_hold calm return | 3.51 | -5.169 |
| docs/scenarios.md:22 | buy_and_hold hiked return | -0.87 | -8.993 |
| docs/scenarios.md:23 | momentum calm return | -2.36 | -0.321 |
| docs/scenarios.md:23 | momentum hiked return | -0.63 | -3.099 |
| docs/scenarios.md:24 | oracle calm return | 20.86 | 21.84 |
| docs/scenarios.md:24 | oracle hiked return | 20.91 | 19.23 |
| docs/agents-and-evaluation.md:40 | the Oracle spends its budget on equal weight across the ten most mispriced names | 10 | 5 |
| docs/agents-and-evaluation.md:57 | Oracle median P&L, default configuration: 110k | 110,000 | 9.226e+04 |
| docs/agents-and-evaluation.md:57 | Oracle median P&L across three times as many names: 70k | 70,000 | 6.385e+04 |
| docs/agents-and-evaluation.md:60 | on seed 2026 momentum captures 27% over five days | 0.27 | 0.6769 |
| docs/agents-and-evaluation.md:60 | and 94% over sixty | 0.94 | 0.9961 |
| docs/agents-and-evaluation.md:64 | over twelve markets momentum ranks +0.556 | 0.556 | 0.6348 |
| docs/agents-and-evaluation.md:65 | mean-reversion -0.011 | -0.011 | 0.07243 |
| docs/agents-and-evaluation.md:65 | a single seed picks the top agent exactly half the time (6 of 12) | 6 | 7 |
| docs/agents-and-evaluation.md:66 | momentum capture range, low | -0.335 | -0.02228 |
| docs/agents-and-evaluation.md:66 | momentum capture range, high | 1.903 | 1.305 |
| docs/agents-and-evaluation.md:74 | momentum vs random: 10-2 | 10-2 | 11-1 |
| docs/agents-and-evaluation.md:74 | p = 0.039 | 0.039 | 0.006348 |
| docs/agents-and-evaluation.md:89 | a three-day seed where dividing by the Oracle yields a capture of +14.4 | 14.4 | 3.762 |
| docs/an-llm-agent.md:30 | oracle pnl 42650 | 42,650 | 1.435e+05 |
| docs/an-llm-agent.md:30 | oracle impact 87.5 | 87.5 | -209.6 |
| docs/an-llm-agent.md:32 | momentum pnl -221 | -221 | 2.631e+04 |
| docs/an-llm-agent.md:32 | momentum impact 20.7 | 20.7 | 38.13 |

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
| 100 | separation('momentum','mean_reversion') comment: 7-5, p = 0.77 | 7-5 | 7-5 | - | reproduced |
| 116 | seven factor columns sum to the move, residual around 1e-16 | 1e-16 | 1.34e-17 | - | reproduced |
| 139 | the same digest on linux-x86_64/aarch64, macos-x86_64, windows-x86_64 | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | - | - | not_harnessable |
| 141 | release-gate digest, macos-arm64 row (all five rows are one digest) | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | - | reproduced |
| 152 | 3 rebalances per day, return | 88.7 | 57.35 | -31.35 | MOVED |
| 153 | 6 rebalances per day, return | 30.9 | 28.83 | -2.067 | MOVED |
| 154 | 12 rebalances per day, return | -13.2 | 5.311 | 18.51 | MOVED |
| 163 | 252-day year over 100 instruments takes 27 seconds | 27 | 7.43 | -19.57 | machine_bound |
| 164 | recording 9.8M rows of ground truth adds 3% | 3 | -5.434 | -8.434 | MOVED |
| 165 | sweeps parallelise about 3.3x on eight cores | 3.3 | 4.601 | 1.301 | machine_bound |
| 176 | measured return autocorrelation +0.219 at lag one | 0.219 | 0.2326 | 0.01364 | within_seed_variation |
| 180 | of the eight statistics, four are marginal and four are dependence | 4 | 2 | -2 | MOVED |
| 189 | below VIX 15 a VIX change moves nothing at all | True | True | - | structural_ok |
| 189 | thirteenfold VIX move changes realised vol by under one point | 1 | 0.8642 | -0.1358 | reproduced |
| 215 | worked example: 20-seed sweep | 20 | 20 | 0 | reproduced |
| 215 | worked example: fifteen seconds end to end | 15 | 5.476 | -9.524 | machine_bound |
| 216 | worked example: 234,000 rows of ground truth | 234,000 | 234,000 | 0 | reproduced |

### docs/agents-and-evaluation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 40 | the Oracle spends its budget on equal weight across the ten most mispriced names | 10 | 5 | -5 | MOVED |
| 41 | over 384 agent-seed pairs, agents beat the Oracle 9.9% of the time | 9.9 | - | - | method_unknown |
| 45 | mean_reversion beats the Oracle 31.2% | 31.2 | - | - | method_unknown |
| 46 | momentum beats the Oracle 8.3% | 8.3 | - | - | method_unknown |
| 47 | buy_and_hold and random beat the Oracle 0.0% in 192 pairs | 0 | - | - | method_unknown |
| 57 | Oracle median P&L, default configuration: 110k | 110,000 | 9.226e+04 | -1.774e+04 | MOVED |
| 57 | Oracle median P&L across three times as many names: 70k | 70,000 | 6.385e+04 | -6,148 | MOVED |
| 60 | on seed 2026 momentum captures 27% over five days | 0.27 | 0.6769 | 0.4069 | MOVED |
| 60 | and 94% over sixty | 0.94 | 0.9961 | 0.05609 | MOVED |
| 64 | over twelve markets momentum ranks +0.556 | 0.556 | 0.6348 | 0.07881 | MOVED |
| 65 | mean-reversion -0.011 | -0.011 | 0.07243 | 0.08343 | MOVED |
| 65 | a single seed picks the top agent exactly half the time (6 of 12) | 6 | 7 | 1 | MOVED |
| 66 | momentum capture range, high | 1.903 | 1.305 | -0.5982 | MOVED |
| 66 | momentum capture range, low | -0.335 | -0.02228 | 0.3127 | MOVED |
| 73 | momentum vs mean_reversion: 7-5 | 7-5 | 7-5 | - | reproduced |
| 73 | p = 0.77 | 0.77 | 0.7744 | 0.004414 | reproduced |
| 74 | momentum vs random: 10-2 | 10-2 | 11-1 | - | MOVED |
| 74 | p = 0.039 | 0.039 | 0.006348 | -0.03265 | MOVED |
| 89 | a three-day seed where dividing by the Oracle yields a capture of +14.4 | 14.4 | 3.762 | -10.64 | MOVED |

### docs/an-llm-agent.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 30 | oracle impact 87.5 | 87.5 | -209.6 | -297.1 | MOVED |
| 30 | oracle pnl 42650 | 42,650 | 1.435e+05 | 1.008e+05 | MOVED |
| 30 | oracle why-right 100% | 100 | 100 | 0 | reproduced |
| 32 | momentum why-right prints '-' (no explain()) | True | True | - | structural_ok |
| 32 | momentum impact 20.7 | 20.7 | 38.13 | 17.43 | MOVED |
| 32 | momentum pnl -221 | -221 | 2.631e+04 | 2.653e+04 | MOVED |
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
| 50 | over run_days(120) each macro field takes exactly one distinct value | 1 | 1 | 0 | reproduced |
| 52 | fundamental_value takes exactly one distinct value per instrument | 1 | 1 | 0 | reproduced |
| 67 | corporate bond yield defaults to 0.0456 | 0.0456 | 0.0456 | 0 | reproduced |
| 70 | pin_macro(corporate_bond_yield=0.09) repriced nineteen of twenty | 19 | 19 | 0 | reproduced |
| 71 | the twentieth is a loss-maker valued off book value | 1 | 1 | 0 | reproduced |
| 73 | pinning federal_funds_rate alone repriced none of the twenty | 0 | 0 | 0 | reproduced |

### docs/forking-a-simulation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 22 | pt.branch cost < 1 ms | 1 | 0.01971 | -0.9803 | reproduced |
| 23 | Checkpoint.resume() cost 2.7 s | 2.7 | 0.2065 | -2.493 | machine_bound |
| 27 | Checkpoint replay is three orders of magnitude slower than branch | 3 | 4.02 | 1.02 | reproduced |

### docs/ground-truth.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | factors sum to change in mispricing_s, residual around 1e-16 | 1e-16 | 1.34e-17 | - | reproduced |

### docs/how-realistic-is-this-market.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 31 | sample report: 10,040 daily returns | 10,040 | 10,040 | 0 | reproduced |
| 36 | sample report: annualised vol | 55.01 | 55.01 | -0.0004 | reproduced |
| 37 | sample report: excess kurtosis | 5.166 | 5.166 | 0.000189 | reproduced |
| 40 | sample report: return acf(1) | 0.248 | 0.2483 | 0.000256 | reproduced |
| 41 | sample report: |return| acf(1) | 0.134 | 0.1338 | -0.000244 | reproduced |
| 42 | sample report: cross-sectional corr | 0.027 | 0.02665 | -0.000355 | reproduced |
| 43 | sample report: volume vs |return| | 0.129 | 0.1293 | 0.000308 | reproduced |
| 44 | sample report: leverage | -0.018 | -0.01778 | 0.000225 | reproduced |
| 45 | sample report: volume change acf(1) | -0.45 | -0.4502 | -0.000152 | reproduced |
| 64 | universe fingerprint 5d8de78b55aad752 | 5d8de78b55aad752 | 5d8de78b55aad752 | - | reproduced |
| 72 | excess kurtosis, median of six seeds | 4 | 4.001 | 0.00111 | reproduced |
| 73 | annualised vol, median of six seeds | 53 | 53.27 | 0.2719 | reproduced |
| 79 | return acf(1), median of six seeds | 0.233 | 0.2326 | -0.000363 | reproduced |
| 80 | |return| acf(1), median of six seeds | 0.117 | 0.117 | -2.27e-05 | reproduced |
| 81 | cross-sectional corr, median of six seeds | 0.024 | 0.02429 | 0.000294 | reproduced |
| 82 | volume vs |return|, median of six seeds | 0.105 | 0.105 | -1.61e-05 | reproduced |
| 83 | leverage, median of six seeds | -0.004 | -0.003532 | 0.000468 | reproduced |
| 84 | volume change acf(1), median of six seeds | -0.463 | -0.4633 | -0.000271 | reproduced |
| 112 | shared market factor sigma 0.003/day | 0.003 | 0.003 | 0 | reproduced |
| 113 | sector daily sigma runs 0.008 to 0.025, median 0.015 | 0.015 | 0.015 | 0 | reproduced |
| 114 | the shared factor carries about 4% of a typical name's variance | 4 | 4 | 8.88e-16 | reproduced |
| 128 | clustering largely gone by lag twenty | 0 | 0.01207 | 0.01207 | reproduced |
| 174 | AR(2) impulse response rises to 1.284 by day two | 1.284 | 1.284 | -8.3e-05 | reproduced |
| 196 | MOMENTUM_THETA 0.25 -> 0.05 takes return acf to +0.034 | 0.034 | - | - | not_harnessable |
| 203 | GARCH persistence is 0.99 | 0.99 | 0.99 | 0 | reproduced |
| 204 | raising the variance ceiling lifts clustering 0.016 and vol 52.7% -> 72% | 0.016 | - | - | not_harnessable |
| 208 | a realistic +0.30 needs a market factor near 0.0098 against the 0.0030 in the source | 0.0098 | 0.00975 | -5e-05 | reproduced |

### docs/index.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 53 | five wheel targets, one fixed simulation, digests compared | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | - | reproduced |
| 63 | seven factor contributions, residual around 1e-16 | 1e-16 | 1.34e-17 | - | reproduced |

### docs/performance.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 14 | 252 days x 10 instruments: 2.9 s | 2.9 | 0.7317 | -2.168 | machine_bound |
| 15 | 252 days x 100 instruments: 27.4 s | 27.4 | 7.43 | -19.97 | machine_bound |
| 16 | 252 x 100 recording 9.8M rows: 28.2 s | 28.2 | 7.026 | -21.17 | machine_bound |
| 16 | a recorded year at 100 instruments is 9.8M rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 17 | 8 seeds x 21 days x 100, serial: 20.0 s | 20 | 5.226 | -14.77 | machine_bound |
| 18 | 8 seeds x 21 days x 100, 8 workers: 6.1 s | 6.1 | 1.136 | -4.964 | machine_bound |
| 20 | recording costs about 3% | 3 | -5.434 | -8.434 | MOVED |
| 23 | sweeps parallelise about 3.3x on eight cores | 3.3 | 4.601 | 1.301 | machine_bound |
| 27 | cost scales roughly linearly in instruments x days (t100/t10 ~ 9.4 published) | 9.45 | 10.16 | 0.7053 | reproduced |

### docs/reading-results.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | the book table is 40x the rows | 40 | 20 | -20 | MOVED |
| 34 | recording ground truth costs about 3% | 3 | -5.434 | -8.434 | MOVED |

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
| 22 | buy_and_hold calm return | 3.51 | -5.169 | -8.679 | MOVED |
| 22 | buy_and_hold hiked return | -0.87 | -8.993 | -8.123 | MOVED |
| 23 | momentum calm return | -2.36 | -0.321 | 2.039 | MOVED |
| 23 | momentum hiked return | -0.63 | -3.099 | -2.469 | MOVED |
| 24 | oracle calm return | 20.86 | 21.84 | 0.9798 | MOVED |
| 24 | oracle hiked return | 20.91 | 19.23 | -1.679 | MOVED |
| 31 | pinning federal_funds_rate alone: exactly 0.00% across twenty instruments | 0 | 0 | 0 | reproduced |
| 48 | annualised realised vol at VIX 5 | 58.05 | 58.05 | 0.004557 | reproduced |
| 49 | annualised realised vol at VIX 15 | 58.05 | 58.05 | 0.004557 | reproduced |
| 50 | annualised realised vol at VIX 45 | 58.22 | 58.22 | -0.003171 | reproduced |
| 51 | annualised realised vol at VIX 65 | 58.92 | 58.92 | -0.001291 | reproduced |
| 54 | VIX 5, 10 and 15 produce bit-identical prices over 60 days | True | True | - | structural_ok |
| 61 | mean quoted spread at VIX 15 | 12.17 | 12.17 | -0.000171 | reproduced |
| 61 | mean quoted spread at VIX 25 | 14.72 | 14.72 | -0.004409 | reproduced |
| 61 | mean quoted spread at VIX 45 | 20.05 | 20.05 | 0.004571 | reproduced |
| 62 | mean quoted spread at VIX 65 | 28.41 | 28.41 | 0.001571 | reproduced |
| 66 | mean pairwise correlation at VIX 15, 300 pairs | 0.022 | 0.02176 | -0.000241 | reproduced |
| 66 | mean pairwise correlation at VIX 45 | 0.023 | 0.02325 | 0.000254 | reproduced |
| 66 | mean pairwise correlation at VIX 65 | 0.041 | 0.04073 | -0.000273 | reproduced |
| 90 | median shortfall at VIX 45 | 11.69 | 11.69 | -0.004181 | reproduced |
| 90 | median shortfall at VIX 15 | 6.06 | 6.064 | 0.004119 | reproduced |
| 90 | VIX 45 regime costs more in 12 of 12 seeds | 12 | 12 | 0 | reproduced |
| 91 | paired median delta | 5.62 | 5.622 | 0.0017 | reproduced |
| 100 | macro-counterfactual draw divergence zero in every comparison run | 0 | 0 | 0 | reproduced |

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
