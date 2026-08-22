# Published-figure re-measurement

Commit `0ff683a`, 2026-08-21 23:10, pretium 0.1.0. Full run: 170s wall with 6 workers.

| status | figures |
|---|---|
| reproduced | 72 |
| within_seed_variation | 2 |
| MOVED | 80 |
| machine_bound | 10 |
| structural_ok | 11 |
| structural_fail | 3 |
| method_unknown | 3 |
| not_harnessable | 6 |
| covered_by_tests | 8 |

## Doc edits needed

Every row here is a published number the stated (or reconstructed)
method no longer produces. On unchanged main these are documents that
were already stale; after an engine change, this section IS the edit
list.

| where | figure | published | measured |
|---|---|---|---|
| README.md:143 | the README table's current-era row: known-answer v4 at 0b4579d | 4 | 5 |
| docs/index.md:53 | five wheel targets, one fixed simulation, digests compared | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | 76983e655bd0fcc380efeaed6d5f4b863bca743019a1e28dc9c83bc0a3180eeb |
| README.md:157 | 3 rebalances per day, return | 88.7 | 103.1 |
| README.md:158 | 6 rebalances per day, return | 30.9 | 59.33 |
| README.md:159 | 12 rebalances per day, return | -13.2 | 24.08 |
| README.md:169 | recording 9.8M rows of ground truth adds 3% | 3 | -1.509 |
| README.md:181 | measured return autocorrelation +0.219 at lag one | 0.219 | 0.2577 |
| README.md:184 | of the eight statistics, four are marginal and four are dependence | 4 | 2 |
| README.md:194 | thirteenfold VIX move changes realised vol by under one point | 1 | 3.253 |
| README.md:194 | below VIX 15 a VIX change moves nothing at all | True | False |
| README.md:100 | separation('momentum','mean_reversion') comment: 7-5, p = 0.77 | 7-5 | 9-3 |
| README.md:100 | the same comment's p = 0.77 | 0.77 | 0.146 |
| docs/core-concepts.md:50 | over run_days(120) each macro field takes exactly one distinct value | 1 | 120 |
| docs/core-concepts.md:52 | fundamental_value takes exactly one distinct value per instrument | 1 | 3 |
| docs/scenarios.md:24 | buy_and_hold calm return | -6.11 | -8.85 |
| docs/scenarios.md:24 | buy_and_hold hiked return | -9.87 | -12.42 |
| docs/scenarios.md:24 | buy_and_hold delta | -3.76 | -3.573 |
| docs/scenarios.md:25 | momentum calm return | -0.68 | -2.515 |
| docs/scenarios.md:25 | momentum hiked return | -2.55 | -4.441 |
| docs/scenarios.md:25 | momentum delta | -1.87 | -1.926 |
| docs/scenarios.md:26 | oracle calm return | 18.21 | 8.426 |
| docs/scenarios.md:26 | oracle hiked return | 16.06 | 6.278 |
| docs/scenarios.md:55 | annualised realised vol at VIX 5 | 58.05 | 63.93 |
| docs/scenarios.md:56 | annualised realised vol at VIX 15 | 58.05 | 64.04 |
| docs/scenarios.md:57 | annualised realised vol at VIX 45 | 58.22 | 64.82 |
| docs/scenarios.md:58 | annualised realised vol at VIX 65 | 58.92 | 67.19 |
| docs/scenarios.md:61 | VIX 5, 10 and 15 produce bit-identical prices over 60 days | True | False |
| docs/scenarios.md:68 | mean quoted spread at VIX 15 | 12.17 | 11.57 |
| docs/scenarios.md:69 | mean quoted spread at VIX 25 | 14.72 | 14.38 |
| docs/scenarios.md:69 | mean quoted spread at VIX 45 | 20.05 | 18.67 |
| docs/scenarios.md:69 | mean quoted spread at VIX 65 | 28.41 | 24.88 |
| docs/scenarios.md:73 | mean pairwise correlation at VIX 15, 300 pairs | 0.022 | 0.1078 |
| docs/scenarios.md:73 | mean pairwise correlation at VIX 45 | 0.023 | 0.1215 |
| docs/scenarios.md:73 | mean pairwise correlation at VIX 65 | 0.041 | 0.1595 |
| docs/scenarios.md:106 | 'that branch draws either four uniforms or none' - settlement draws conditioned on the trajectory | False | True |
| docs/agents-and-evaluation.md:48 | momentum beats the Oracle 5 of 12 on the twelve-market grid | 5 | 4 |
| docs/agents-and-evaluation.md:66 | Oracle median P&L on the ranking grid at ten days, sim seeds 0-7, default top_k=5: $87k | 87,000 | 90,253 |
| docs/agents-and-evaluation.md:66 | the same information across three times as many names, top_k=15: $71k | 71,000 | 65,965 |
| docs/agents-and-evaluation.md:71 | the Oracle makes $21k in five days on seed 2026 over random(40,7) | 21,000 | 63,464 |
| docs/agents-and-evaluation.md:71 | and $568k in sixty | 568,000 | 410,968 |
| docs/agents-and-evaluation.md:72 | momentum's capture ratio against the five-day denominator: 2.98 | 2.98 | 1.058 |
| docs/agents-and-evaluation.md:72 | and 1.47 against the sixty-day one | 1.47 | 2.434 |
| docs/agents-and-evaluation.md:82 | over twelve markets momentum ranks +0.805 pooled | 0.805 | 0.7727 |
| docs/agents-and-evaluation.md:82 | mean-reversion +0.064 | 0.064 | -0.009731 |
| docs/agents-and-evaluation.md:83 | a single seed picks the pooled leader ten times in twelve | 10 | 9 |
| docs/agents-and-evaluation.md:84 | momentum capture range, low | -0.169 | 0.007172 |
| docs/agents-and-evaluation.md:84 | momentum capture range, high | 1.672 | 2.834 |
| docs/agents-and-evaluation.md:92 | momentum vs mean_reversion: 11-1 | 11-1 | 9-3 |
| docs/agents-and-evaluation.md:92 | p = 0.006 | 0.006 | 0.146 |
| docs/agents-and-evaluation.md:102 | the identical test over seeds 12-23: momentum over mean-reversion 9-3 | 9-3 | 10-2 |
| docs/agents-and-evaluation.md:102 | p = 0.15 | 0.15 | 0.03857 |
| docs/agents-and-evaluation.md:114 | three days, seeds 0-9: the reference's per-seed P&L spans $7.2k... | 7,200 | 10,600 |
| docs/agents-and-evaluation.md:114 | ...to $41.7k | 41,700 | 36,803 |
| docs/agents-and-evaluation.md:115 | mean-reversion's ratio on the thinnest of those markets: +2.8 | 2.8 | 3.85 |
| docs/agents-and-evaluation.md:115 | against a pooled +0.27 across the ten | 0.27 | 0.5264 |
| docs/an-llm-agent.md:30 | oracle pnl 114727 | 114,727 | 270,720 |
| docs/an-llm-agent.md:32 | momentum pnl 22488 | 22,488 | 12,301 |
| docs/an-llm-agent.md:49 | oracle twenty-day impact spans -181... | -181 | -240.8 |
| docs/an-llm-agent.md:49 | ...to +577 bps across seeds 2020-2031 | 577 | 380.4 |
| docs/an-llm-agent.md:49 | positive in only 8 of 12 seeds | 8 | 6 |
| docs/an-llm-agent.md:51 | over three days both are positive in 12 of 12 | 12 | 10 |
| docs/how-realistic-is-this-market.md:36 | sample report: annualised vol | 55.01 | 57.51 |
| docs/how-realistic-is-this-market.md:37 | sample report: excess kurtosis | 5.166 | 3.132 |
| docs/how-realistic-is-this-market.md:40 | sample report: return acf(1) | 0.248 | 0.2585 |
| docs/how-realistic-is-this-market.md:41 | sample report: |return| acf(1) | 0.134 | 0.1086 |
| docs/how-realistic-is-this-market.md:42 | sample report: cross-sectional corr | 0.027 | 0.08731 |
| docs/how-realistic-is-this-market.md:43 | sample report: volume vs |return| | 0.129 | 0.5113 |
| docs/how-realistic-is-this-market.md:44 | sample report: leverage | -0.018 | -0.01176 |
| docs/how-realistic-is-this-market.md:45 | sample report: volume change acf(1) | -0.45 | -0.4572 |
| docs/how-realistic-is-this-market.md:72 | excess kurtosis, median of six seeds | 4 | 3.107 |
| docs/how-realistic-is-this-market.md:73 | annualised vol, median of six seeds | 53 | 56.92 |
| docs/how-realistic-is-this-market.md:79 | return acf(1), median of six seeds | 0.233 | 0.2577 |
| docs/how-realistic-is-this-market.md:81 | cross-sectional corr, median of six seeds | 0.024 | 0.08359 |
| docs/how-realistic-is-this-market.md:82 | volume vs |return|, median of six seeds | 0.105 | 0.5082 |
| docs/how-realistic-is-this-market.md:83 | leverage, median of six seeds | -0.004 | -0.02514 |
| docs/how-realistic-is-this-market.md:111 | shared market factor sigma 0.003/day | 0.003 | 0.0075 |
| docs/how-realistic-is-this-market.md:114 | the shared factor carries about 4% of a typical name's variance | 4 | 25 |
| docs/how-realistic-is-this-market.md:208 | 'against the 0.0030 in the source' - the calibration paragraph's reference to the constant | 0.003 | 0.0075 |
| docs/reproducing-a-run.md:198 | '0b4579d ... bumped the known-answer baseline to v4' - named as the current era | 4 | 5 |
| docs/strategy-specs.md:152 | the rebalance table swings the same signal from +88.7%... | 88.7 | 103.1 |
| docs/strategy-specs.md:152 | ...to -13.2% purely by trading it more often | -13.2 | 24.08 |
| README.md:200 | 'the macro is exogenous - it holds whatever initial conditions you gave it': macro fields take one distinct value unless driven | 1 | 120 |
| README.md:203 | 'fair value is constant unless you drive it' | 1 | 3 |

## Published numbers nobody can re-measure

| where | figure | why |
|---|---|---|
| docs/transaction-cost-analysis.md:28 | buying and holding costs +16.7 bps in one measured example | 'one measured example' with no seed, universe, size or horizon stated anywhere in the repo; the number cannot be re-measured as published |
| docs/transaction-cost-analysis.md:29 | buying and selling three steps later comes to -10.8 bps | same unstated example as tca.entry_cost |
| docs/transaction-cost-analysis.md:35 | a request for 4,856 shares filled 483, and every larger request filled the same 483 | instrument and universe unstated; the structural claim (fills saturate at displayed depth) is asserted every run by examples/research_workflow.py, which this harness executes |

## Every figure, by document

### README.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 100 | separation('momentum','mean_reversion') comment: 7-5, p = 0.77 | 7-5 | 9-3 | - | MOVED |
| 100 | the same comment's p = 0.77 | 0.77 | 0.146 | -0.624 | MOVED |
| 116 | seven factor columns sum to the move, residual around 1e-16 | 1e-16 | 1.42e-17 | - | reproduced |
| 142 | release-gate digest measured at a5afd1c (v3) on Windows x86_64 and macOS arm64 - a historical record | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | - | - | not_harnessable |
| 143 | the README table's current-era row: known-answer v4 at 0b4579d | 4 | 5 | 1 | MOVED |
| 143 | the current-era baseline is self-consistent on this platform | True | True | - | structural_ok |
| 157 | 3 rebalances per day, return | 88.7 | 103.1 | 14.43 | MOVED |
| 158 | 6 rebalances per day, return | 30.9 | 59.33 | 28.43 | MOVED |
| 159 | 12 rebalances per day, return | -13.2 | 24.08 | 37.28 | MOVED |
| 168 | 252-day year over 100 instruments takes 27 seconds | 27 | 7.073 | -19.93 | machine_bound |
| 169 | recording 9.8M rows of ground truth adds 3% | 3 | -1.509 | -4.509 | MOVED |
| 170 | sweeps parallelise about 3.3x on eight cores | 3.3 | 4.224 | 0.9243 | machine_bound |
| 181 | measured return autocorrelation +0.219 at lag one | 0.219 | 0.2577 | 0.03867 | MOVED |
| 184 | of the eight statistics, four are marginal and four are dependence | 4 | 2 | -2 | MOVED |
| 194 | below VIX 15 a VIX change moves nothing at all | True | False | - | structural_fail |
| 194 | thirteenfold VIX move changes realised vol by under one point | 1 | 3.253 | 2.253 | MOVED |
| 200 | 'the macro is exogenous - it holds whatever initial conditions you gave it': macro fields take one distinct value unless driven | 1 | 120 | 119 | MOVED |
| 203 | 'fair value is constant unless you drive it' | 1 | 3 | 2 | MOVED |
| 220 | worked example: five-agent evaluation | 5 | 5 | 0 | reproduced |
| 220 | worked example: 20-seed sweep | 20 | 20 | 0 | reproduced |
| 220 | worked example: fifteen seconds end to end | 15 | 4.624 | -10.38 | machine_bound |
| 221 | worked example: 234,000 rows of ground truth | 234,000 | 234,000 | 0 | reproduced |
| 224 | the worked example's internal assertions all pass | True | True | - | structural_ok |

### docs/agents-and-evaluation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 42 | the Oracle spends its budget equal weight, long the five most underpriced and short the five most overpriced (top_k=5 per side) | 5 | 5 | 0 | reproduced |
| 48 | momentum beats the Oracle 5 of 12 on the twelve-market grid | 5 | 4 | -1 | MOVED |
| 49 | mean_reversion beats the Oracle 0 of 12 | 0 | 0 | 0 | reproduced |
| 50 | buy_and_hold beats the Oracle 0 of 12 | 0 | 0 | 0 | reproduced |
| 51 | random beats the Oracle 0 of 12 | 0 | 0 | 0 | reproduced |
| 66 | Oracle median P&L on the ranking grid at ten days, sim seeds 0-7, default top_k=5: $87k | 87,000 | 90,253 | 3,253 | MOVED |
| 66 | the same information across three times as many names, top_k=15: $71k | 71,000 | 65,965 | -5,035 | MOVED |
| 69 | mispricing reverts on a 60-day half-life | 60 | 60 | 5.59e-11 | reproduced |
| 71 | the Oracle makes $21k in five days on seed 2026 over random(40,7) | 21,000 | 63,464 | 42,464 | MOVED |
| 71 | and $568k in sixty | 568,000 | 410,968 | -157,032 | MOVED |
| 72 | momentum's capture ratio against the five-day denominator: 2.98 | 2.98 | 1.058 | -1.922 | MOVED |
| 72 | and 1.47 against the sixty-day one | 1.47 | 2.434 | 0.964 | MOVED |
| 82 | over twelve markets momentum ranks +0.805 pooled | 0.805 | 0.7727 | -0.03235 | MOVED |
| 82 | mean-reversion +0.064 | 0.064 | -0.009731 | -0.07373 | MOVED |
| 83 | a single seed picks the pooled leader ten times in twelve | 10 | 9 | -1 | MOVED |
| 84 | momentum capture range, high | 1.672 | 2.834 | 1.162 | MOVED |
| 84 | momentum capture range, low | -0.169 | 0.007172 | 0.1762 | MOVED |
| 92 | momentum vs mean_reversion: 11-1 | 11-1 | 9-3 | - | MOVED |
| 92 | p = 0.006 | 0.006 | 0.146 | 0.14 | MOVED |
| 93 | momentum vs random: 11-1 | 11-1 | 11-1 | - | reproduced |
| 93 | p = 0.006 | 0.006 | 0.006348 | 0.000348 | reproduced |
| 102 | the identical test over seeds 12-23: momentum over mean-reversion 9-3 | 9-3 | 10-2 | - | MOVED |
| 102 | p = 0.15 | 0.15 | 0.03857 | -0.1114 | MOVED |
| 103 | even a clean sweep only reaches p = 0.0005 | 0.0005 | 0.000488 | -1.17e-05 | reproduced |
| 114 | ...to $41.7k | 41,700 | 36,803 | -4,897 | MOVED |
| 114 | three days, seeds 0-9: the reference's per-seed P&L spans $7.2k... | 7,200 | 10,600 | 3,400 | MOVED |
| 115 | against a pooled +0.27 across the ten | 0.27 | 0.5264 | 0.2564 | MOVED |
| 115 | mean-reversion's ratio on the thinnest of those markets: +2.8 | 2.8 | 3.85 | 1.05 | MOVED |

### docs/an-llm-agent.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 30 | oracle pnl 114727 | 114,727 | 270,720 | 155,993 | MOVED |
| 30 | oracle why-right 100% | 100 | 100 | 0 | reproduced |
| 32 | momentum why-right prints '-' (no explain()) | True | True | - | structural_ok |
| 32 | momentum pnl 22488 | 22,488 | 12,301 | -10,187 | MOVED |
| 49 | ...to +577 bps across seeds 2020-2031 | 577 | 380.4 | -196.6 | MOVED |
| 49 | oracle twenty-day impact spans -181... | -181 | -240.8 | -59.82 | MOVED |
| 49 | positive in only 8 of 12 seeds | 8 | 6 | -2 | MOVED |
| 50 | momentum's twenty-day impact flips sign the same way | True | True | - | structural_ok |
| 51 | over three days both are positive in 12 of 12 | 12 | 10 | -2 | MOVED |
| 76 | driver is one of the engine's seven factors | 7 | 7 | 0 | reproduced |
| 105 | steps_per_day defaults to 6 | 6 | 6 | 0 | reproduced |
| 122 | default run is 20 days over 12 instruments, so 20 calls | 20 | 20 | 0 | reproduced |
| 123 | a run lands around $0.30 to $0.60 at Opus 5 rates | 0.30-0.60 | - | - | not_harnessable |

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
| 22 | pt.branch cost < 1 ms | 1 | 0.02333 | -0.9767 | reproduced |
| 23 | Checkpoint.resume() cost 2.7 s | 2.7 | 0.1787 | -2.521 | machine_bound |
| 26 | Checkpoint replay is three orders of magnitude slower than branch | 3 | 3.884 | 0.8841 | reproduced |

### docs/ground-truth.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | factors sum to change in mispricing_s, residual around 1e-16 | 1e-16 | 1.42e-17 | - | reproduced |

### docs/how-realistic-is-this-market.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 31 | sample report: 10,040 daily returns | 10,040 | 10,040 | 0 | reproduced |
| 36 | sample report: annualised vol | 55.01 | 57.51 | 2.509 | MOVED |
| 37 | sample report: excess kurtosis | 5.166 | 3.132 | -2.034 | MOVED |
| 40 | sample report: return acf(1) | 0.248 | 0.2585 | 0.01053 | MOVED |
| 41 | sample report: |return| acf(1) | 0.134 | 0.1086 | -0.02544 | MOVED |
| 42 | sample report: cross-sectional corr | 0.027 | 0.08731 | 0.06031 | MOVED |
| 43 | sample report: volume vs |return| | 0.129 | 0.5113 | 0.3823 | MOVED |
| 44 | sample report: leverage | -0.018 | -0.01176 | 0.006244 | MOVED |
| 45 | sample report: volume change acf(1) | -0.45 | -0.4572 | -0.007215 | MOVED |
| 64 | universe fingerprint 5d8de78b55aad752 | 5d8de78b55aad752 | 5d8de78b55aad752 | - | reproduced |
| 72 | excess kurtosis, median of six seeds | 4 | 3.107 | -0.8934 | MOVED |
| 73 | annualised vol, median of six seeds | 53 | 56.92 | 3.922 | MOVED |
| 79 | return acf(1), median of six seeds | 0.233 | 0.2577 | 0.02467 | MOVED |
| 80 | |return| acf(1), median of six seeds | 0.117 | 0.1243 | 0.007337 | within_seed_variation |
| 81 | cross-sectional corr, median of six seeds | 0.024 | 0.08359 | 0.05959 | MOVED |
| 82 | volume vs |return|, median of six seeds | 0.105 | 0.5082 | 0.4032 | MOVED |
| 83 | leverage, median of six seeds | -0.004 | -0.02514 | -0.02114 | MOVED |
| 84 | volume change acf(1), median of six seeds | -0.463 | -0.4592 | 0.00384 | within_seed_variation |
| 111 | shared market factor sigma 0.003/day | 0.003 | 0.0075 | 0.0045 | MOVED |
| 112 | sector daily sigma runs 0.008... | 0.008 | 0.008 | 0 | reproduced |
| 113 | ...to 0.025 | 0.025 | 0.025 | 0 | reproduced |
| 113 | sector daily sigma runs 0.008 to 0.025, median 0.015 | 0.015 | 0.015 | 0 | reproduced |
| 114 | the shared factor carries about 4% of a typical name's variance | 4 | 25 | 21 | MOVED |
| 126 | clustering largely gone by lag twenty | 0 | 0.006338 | 0.006338 | reproduced |
| 174 | AR(2) impulse response rises to 1.284 by day two | 1.284 | 1.284 | -8.3e-05 | reproduced |
| 196 | MOMENTUM_THETA 0.25 -> 0.05 takes return acf to +0.034 | 0.034 | - | - | not_harnessable |
| 203 | GARCH persistence is 0.99 | 0.99 | 0.99 | 0 | reproduced |
| 204 | raising the variance ceiling lifts clustering 0.016 and vol 52.7% -> 72% | 0.016 | - | - | not_harnessable |
| 208 | a realistic +0.30 needs a market factor near 0.0098 against the 0.0030 in the source | 0.0098 | 0.00975 | -5e-05 | reproduced |
| 208 | 'against the 0.0030 in the source' - the calibration paragraph's reference to the constant | 0.003 | 0.0075 | 0.0045 | MOVED |

### docs/index.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 53 | five wheel targets, one fixed simulation, digests compared | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | 76983e655bd0fcc380efeaed6d5f4b863bca743019a1e28dc9c83bc0a3180eeb | - | MOVED |
| 63 | seven factor contributions, residual around 1e-16 | 1e-16 | 1.42e-17 | - | reproduced |

### docs/performance.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 14 | 252 days x 10 instruments: 2.9 s | 2.9 | 0.8801 | -2.02 | machine_bound |
| 15 | 252 days x 100 instruments: 27.4 s | 27.4 | 7.073 | -20.33 | machine_bound |
| 16 | 252 x 100 recording 9.8M rows: 28.2 s | 28.2 | 7.455 | -20.75 | machine_bound |
| 16 | a recorded year at 100 instruments is 9.8M rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 17 | 8 seeds x 21 days x 100, serial: 20.0 s | 20 | 4.581 | -15.42 | machine_bound |
| 18 | 8 seeds x 21 days x 100, 8 workers: 6.1 s | 6.1 | 1.084 | -5.016 | machine_bound |
| 20 | recording a full year of tick-grain ground truth costs a few percent at most (bound) | 4 | -1.509 | -5.509 | reproduced |
| 24 | nearly half the pairs came out negative | 0.5 | 0.75 | 0.25 | reproduced |
| 24 | the median was about +1% | 1 | -1.509 | -2.509 | reproduced |
| 25 | the 0.8s between the two 252x100 rows is run-to-run noise | 0.8 | 0.8 | 6.66e-16 | reproduced |
| 28 | sweeps parallelise about 3.3x on eight cores | 3.3 | 4.224 | 0.9243 | machine_bound |
| 32 | cost scales roughly linearly in instruments x days (t100/t10 ~ 9.4 published) | 9.45 | 8.036 | -1.414 | reproduced |

### docs/reading-results.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | ten levels a side makes the book table 20x the rows | 20 | 20 | 0 | reproduced |
| 35 | recording ground truth costs a few percent at most (bound) | 4 | -1.509 | -5.509 | reproduced |

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
| 196 | at a5afd1c two independent platform builds produced the identical digest (historical record) | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | - | - | not_harnessable |
| 198 | '0b4579d ... bumped the known-answer baseline to v4' - named as the current era | 4 | 5 | 1 | MOVED |

### docs/rng-streams.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 10 | the engine derives three independent substreams from the root seed | 3 | 3 | 0 | reproduced |
| 35 | the split is part of the 2026-08 era boundary (KAT_VERSION = 5) | 5 | 5 | 0 | reproduced |
| 46 | the market stream's schedule is a pure function of (market status, active roster, sector count) | True | True | - | structural_ok |
| 56 | draws_by_stream() reports market, economy, external | market,economy,external | market,economy,external | - | reproduced |
| 67 | the substream derivation contract (splitmix64 finalizer, sequence 256+k) | True | - | - | covered_by_tests |
| 116 | raw sequences 0/1 (constructors), 21 (universe), 99 (reference MAIN) | True | - | - | covered_by_tests |
| 134 | state_snapshot()['rng'] is nine numbers, three per stream | 9 | 9 | 0 | reproduced |
| 138 | a pre-split snapshot (three numbers) is refused on restore | True | True | - | structural_ok |

### docs/running-a-simulation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 50 | a tick loop crosses the boundary about 98,000 times per simulated year | 98,000 | 98,280 | 280 | reproduced |
| 51 | five fields per tick makes roughly 500,000 | 500,000 | 491,400 | -8,600 | reproduced |

### docs/scenarios.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 24 | buy_and_hold calm return | -6.11 | -8.85 | -2.74 | MOVED |
| 24 | buy_and_hold delta | -3.76 | -3.573 | 0.1872 | MOVED |
| 24 | buy_and_hold hiked return | -9.87 | -12.42 | -2.553 | MOVED |
| 25 | momentum calm return | -0.68 | -2.515 | -1.835 | MOVED |
| 25 | momentum delta | -1.87 | -1.926 | -0.05624 | MOVED |
| 25 | momentum hiked return | -2.55 | -4.441 | -1.891 | MOVED |
| 26 | oracle calm return | 18.21 | 8.426 | -9.784 | MOVED |
| 26 | oracle delta | -2.15 | -2.148 | 0.0025 | reproduced |
| 26 | oracle hiked return | 16.06 | 6.278 | -9.782 | MOVED |
| 36 | pinning federal_funds_rate alone: exactly 0.00% across twenty instruments over 40 days | 0 | 0 | 0 | reproduced |
| 37 | a 60-day run crossing the day-45 central-bank meeting reprices a median -3.99% | -3.99 | -4.189 | -0.1988 | reproduced |
| 55 | annualised realised vol at VIX 5 | 58.05 | 63.93 | 5.883 | MOVED |
| 56 | annualised realised vol at VIX 15 | 58.05 | 64.04 | 5.992 | MOVED |
| 57 | annualised realised vol at VIX 45 | 58.22 | 64.82 | 6.604 | MOVED |
| 58 | annualised realised vol at VIX 65 | 58.92 | 67.19 | 8.267 | MOVED |
| 61 | VIX 5, 10 and 15 produce bit-identical prices over 60 days | True | False | - | structural_fail |
| 68 | mean quoted spread at VIX 15 | 12.17 | 11.57 | -0.6034 | MOVED |
| 69 | mean quoted spread at VIX 25 | 14.72 | 14.38 | -0.3399 | MOVED |
| 69 | mean quoted spread at VIX 45 | 20.05 | 18.67 | -1.377 | MOVED |
| 69 | mean quoted spread at VIX 65 | 28.41 | 24.88 | -3.526 | MOVED |
| 73 | mean pairwise correlation at VIX 15, 300 pairs | 0.022 | 0.1078 | 0.08579 | MOVED |
| 73 | mean pairwise correlation at VIX 45 | 0.023 | 0.1215 | 0.09851 | MOVED |
| 73 | mean pairwise correlation at VIX 65 | 0.041 | 0.1595 | 0.1185 | MOVED |
| 75 | the correlation blend acts on sector factors with sigma 0.002 | 0.002 | 0.002 | 0 | reproduced |
| 75 | against per-stock noise running 0.008... | 0.008 | 0.008 | 0 | reproduced |
| 76 | ...to 0.025 | 0.025 | 0.025 | 0 | reproduced |
| 97 | median shortfall at VIX 45 | 11.69 | 11.69 | -0.004181 | reproduced |
| 97 | median shortfall at VIX 15 | 6.06 | 6.064 | 0.004119 | reproduced |
| 97 | VIX 45 regime costs more in 12 of 12 seeds | 12 | 12 | 0 | reproduced |
| 98 | paired median delta | 5.62 | 5.622 | 0.0017 | reproduced |
| 104 | order flow consumes no RNG draws, so a TCA counterfactual is exact | True | - | - | covered_by_tests |
| 106 | 'that branch draws either four uniforms or none' - settlement draws conditioned on the trajectory | False | True | - | structural_fail |
| 107 | macro-counterfactual draw divergence zero in every comparison run | 0 | 0 | 0 | reproduced |
| 109 | an older build recorded a delta of -4 in 425,600 draws | -4 | - | - | not_harnessable |

### docs/sharing-a-run.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 67 | pt.version() stayed '0.1.0' across the era boundary | 0.1.0 | 0.1.0 | - | reproduced |
| 68 | and the preset stayed 'pt-v1' | pt-v1 | pt-v1 | - | reproduced |
| 68 | 'the recalibrated constant is not even in the preset dictionary' | True | True | - | structural_ok |
| 73 | reproduce() recomputes the era fingerprint before replaying and refuses across an era boundary | True | - | - | covered_by_tests |

### docs/strategy-specs.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 27 | methods-section universe fingerprint a7861d15... | a7861d15 | a7861d15 | - | reproduced |
| 28 | strategy spec fingerprint e6bbc35c... | e6bbc35c | e6bbc35c | - | reproduced |
| 29 | oracle spec fingerprint f383b990... | f383b990 | f383b990 | - | reproduced |
| 55 | spec-built and hand-built baselines score bit-identically | True | - | - | covered_by_tests |
| 103 | blend weights 1.2/0.8 and 0.6/0.4 build bit-identical agents | True | - | - | covered_by_tests |
| 152 | the rebalance table swings the same signal from +88.7%... | 88.7 | 103.1 | 14.43 | MOVED |
| 152 | ...to -13.2% purely by trading it more often | -13.2 | 24.08 | 37.28 | MOVED |

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

- **readme.kat_digest** (README.md:142): pinned to commit a5afd1c and not re-measurable from the installed package; the current era's digest is pinned in tests/known_answer.json and checked by readme.kat_self_consistent
- **readme.kat_era_version** (README.md:143): ad91026 split the RNG and bumped the known-answer to v5 (rng-streams.md names the era); the table's current-era row is one era behind and needs a v5/ad91026 row
- **index.kat_digest** (docs/index.md:53): index.md still carries the unqualified five-target release-gate claim that 0790bf4 rewrote in README.md and reproducing-a-run.md as a by-commit record; the digest that claim rested on belongs to the a5afd1c era, so this row stays MOVED until the page gets the same treatment
- **readme.rebalance_3** (README.md:157): tests/test_baselines.py:376 records that these figures were re-measured after the stepped-day fix (80a0949) and moved to +49.71/+32.70/+0.18; the README still carries the pre-fix values
- **readme.record_overhead** (README.md:169): the point claim was demoted to a bound after a 32-pair interleaved study across two sessions: median about +1%, nearly half the pairs negative, so no wall-clock point resolves it on a working machine; performance.md and reading-results.md now publish 'a few percent at most' but the README still prints 'adds 3%', so this row stays MOVED until the README gets the same bound wording
- **readme.speedup** (README.md:170): parallel speedup is a property of the measuring machine's core count and memory bandwidth, so it is reported rather than judged; re-measure on the published machine before editing
- **readme.return_acf** (README.md:181): the realism page (measured at e2aded1) publishes +0.233 for the same statistic; the README's +0.219 is the older four-statistic-era figure and disagrees with the page it links to
- **readme.facts_split** (README.md:184): pretium.facts splits them 2 marginal / 6 dependence, and the realism page prints that split; the README sentence miscounts
- **readme.separation_comment** (README.md:100): cbd51ee re-measured this to 11-1, p = 0.006 on the agents page; the README comment still carries the pre-era 7-5, and its surrounding snippet builds random(108, seed=7) while the numbers come from the 30-instrument headline grid
- **readme.separation_comment_p** (README.md:100): stale together with the 7-5; the agents page now measures 0.006
- **fork.resume_s** (docs/forking-a-simulation.md:23): replay cost scales with the order log and the page does not say what run length 2.7 s was measured over; the portable part of the claim is the ratio, next row
- **perf.overhead** (docs/performance.md:20): 'a few percent at most' operationalised as median per-pair overhead under 4%; the point claim was demoted to a bound after a 32-pair interleaved study across two sessions: median about +1%, nearly half the pairs negative, so no wall-clock point resolves it on a working machine
- **perf.overhead_median** (docs/performance.md:24): judged as a wide band, not at printed precision: the page itself says to expect the measurement to straddle zero, and observed session medians span -2.8% to +1.8% depending on machine load. The row flags only if the median leaves [-3, +5], which would genuinely contradict the page
- **perf.speedup** (docs/performance.md:28): hardware-bound like the absolute times; reported, not judged
- **perf.linear** (docs/performance.md:32): a ratio of two wall clocks, so judged generously: min-of-few timings wobble under load (measured 9.0 quiet, 6.3 loaded against 9.45 published). Anything inside [5.2, 13.7] still supports 'roughly linear' for 10x the work
- **reading.record_overhead** (docs/reading-results.md:35): 'a few percent at most' operationalised as median per-pair overhead under 4%; the point claim was demoted to a bound after a 32-pair interleaved study across two sessions: median about +1%, nearly half the pairs negative, so no wall-clock point resolves it on a working machine
- **scen.fed_meeting** (docs/scenarios.md:37): written fresh by the engine-fix PR but with no universe or seed stated; plausible reconstructions land between -3.95 and -4.40, bracketing the published -3.99, so this row is judged as a band rather than at printed precision
- **scen.tca_exact** (docs/scenarios.md:104): asserted by tests/test_tca.py (order flow consumes zero draws) and tests/test_scenario.py:235
- **scen.settle_conditional** (docs/scenarios.md:106): true before the RNG split; since ad91026 the four are drawn unconditionally per active company per open tick (rng-streams.md), so the market stream cannot diverge and this paragraph - including 'near-exact rather than exact' - describes the previous era
- **scen.old_draw_delta** (docs/scenarios.md:109): a historical record of a pre-split build, kept on the page as provenance; not re-measurable on this build
- **rng.derivation** (docs/rng-streams.md:67): pinned by the golden test rng.rs::substream_derivation_is_the_documented_formula against hand-computed values
- **rng.sequence_bases** (docs/rng-streams.md:116): the universe sequence is exercised by every pinned fingerprint (realism.fingerprint, spec.universe_fp); 99 by the golden-parity replay harnesses; 256+k by the derivation golden test
- **agents.sep_mom_mr** (docs/agents-and-evaluation.md:92): the prose at lines 99-100 repeats both separations; edit them together
- **llm.cost** (docs/an-llm-agent.md:123): priced by a third-party API, not measurable from the package
- **realism.market_sigma** (docs/how-realistic-is-this-market.md:111): 58837b3 recalibrated MARKET_FACTOR_SIGMA from a sweep; the mechanism paragraph at lines 111-116 and the 'largest gap' framing above it predate that. Stream N is re-confirming the constant post-split, so re-run before editing the page
- **realism.theta_counterfactual** (docs/how-realistic-is-this-market.md:196): requires rebuilding the engine with a changed constant; deliberately not a runtime knob
- **realism.ceiling_counterfactual** (docs/how-realistic-is-this-market.md:204): requires rebuilding the engine with a changed constant
- **realism.market_sigma_030** (docs/how-realistic-is-this-market.md:208): same constant as realism.market_sigma; the calibration argument's premise moved when the sigma was recalibrated in 58837b3
- **repro.kat_historical** (docs/reproducing-a-run.md:196): pinned to commit a5afd1c; see readme.kat_digest
- **repro.kat_era_version** (docs/reproducing-a-run.md:198): ad91026 bumped it again to v5 when the RNG split regenerated the baseline; this paragraph and the README table are one era behind
- **share.era_refusal** (docs/sharing-a-run.md:73): asserted by tests/test_manifest.py (test_a_different_era_is_refused_before_anything_replays and neighbours)
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
- **readme.workflow_asserts** (README.md:224): after an engine change a failure here means the example script itself needs re-fitting, not just the prose
- **readme.macro_exogenous** (README.md:200): 0b4579d made the macro chain run endogenously by default; this paragraph, and core-concepts.md's frozen-macro section, describe the previous era
- **readme.fv_constant** (README.md:203): see readme.macro_exogenous
