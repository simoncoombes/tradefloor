# Published-figure re-measurement

Commit `134fb85`, 2026-08-22 05:19, pretium 0.1.0. Full run: 529s wall with 6 workers.

| status | figures |
|---|---|
| reproduced | 230 |
| MOVED | 1 |
| machine_bound | 11 |
| structural_ok | 26 |
| not_harnessable | 12 |
| covered_by_tests | 10 |

## Doc edits needed

Every row here is a published number the stated (or reconstructed)
method no longer produces. On unchanged main these are documents that
were already stale; after an engine change, this section IS the edit
list.

| where | figure | published | measured |
|---|---|---|---|
| docs/how-realistic-is-this-market.md:129 | leverage reads -0.071 | -0.071 | -0.07045 |

## Every figure, by document

### README.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 60 | TradingEnv passes gymnasium's env_checker | True | - | - | covered_by_tests |
| 88 | pinning VIX in both worlds makes the subtraction byte-exact again | True | True | - | structural_ok |
| 113 | paired across the same twelve markets momentum wins only 7-5 | 7-5 | 7-5 | - | reproduced |
| 113 | p = 0.77 - a coin flip | 0.77 | 0.7744 | 0.004414 | reproduced |
| 115 | a single seed picks the pooled leader only five times in twelve | 5 | 5 | 0 | reproduced |
| 129 | seven factor columns sum to the move, residual around 1e-16 | 1e-16 | 1.43e-17 | - | reproduced |
| 190 | a nudged garch_alpha fingerprints as custom-0c04c4ba, never pt-v1 | custom-0c04c4ba | custom-0c04c4ba | - | reproduced |
| 195 | nothing settable changes how many draws are taken or in what order | True | - | - | covered_by_tests |
| 212 | the by-commit table's a5afd1c row: v3, Windows x86_64 and macOS arm64, identical digest - a historical record | 112fd73e...6eff337 | - | - | not_harnessable |
| 213 | the table's ad91026 row: all five targets, identical digest 76983e65...3180eeb | 76983e65...3180eeb | - | - | not_harnessable |
| 214 | the current digest 1ee64998...fe3581c at v8 | 1ee64998...fe3581c | 1ee64998...fe3581c | - | reproduced |
| 214 | the table's current-era row: known-answer v8 at 6e30497 | 8 | 8 | 0 | reproduced |
| 214 | one platform's confirmation until the gate runs again: this build reproduces the pinned digest | True | True | - | structural_ok |
| 227 | VIX takes a new value every day over the 120-day macro-chain run | 120 | 120 | 0 | reproduced |
| 235 | 59% at 15 | 59 | 58.76 | -0.2369 | reproduced |
| 235 | 107% at 45 | 107 | 107.1 | 0.06673 | reproduced |
| 235 | 49% annualised at VIX 5 | 49 | 49.48 | 0.4798 | reproduced |
| 235 | 124% at 65 | 124 | 124.3 | 0.3115 | reproduced |
| 237 | above VIX 25.5 the cross-section blends toward the market factor | 25.5 | 25.5 | 0 | reproduced |
| 238 | +0.68 at VIX 45 | 0.68 | 0.6777 | -0.002338 | reproduced |
| 238 | +0.76 at 65 | 0.76 | 0.7595 | -0.000529 | reproduced |
| 238 | mean pairwise correlation reads +0.27 calm | 0.27 | 0.2687 | -0.001321 | reproduced |
| 252 | 3 rebalances per day: +97.5% | 97.5 | 97.45 | -0.04618 | reproduced |
| 253 | 6 rebalances per day: +33.8% | 33.8 | 33.84 | 0.04107 | reproduced |
| 254 | 12 rebalances per day: +0.1% | 0.1 | 0.1043 | 0.0043 | reproduced |
| 263 | 252-day year over 100 instruments: 27 seconds on the documentation's reference desktop | 27 | 15.63 | -11.37 | machine_bound |
| 264 | and under seven seconds on an Apple-silicon laptop | 7 | 15.63 | 8.632 | machine_bound |
| 265 | recording 9.8M rows of ground truth costs less than wall-clock timing resolves (bound) | 4 | 0.7636 | -3.236 | reproduced |
| 267 | nearly half the interleaved pairs come out negative | 0.5 | 0.375 | -0.125 | reproduced |
| 267 | sweeps parallelise 3-4x on eight cores | 3.5 | 4.094 | 0.5941 | machine_bound |
| 281 | measured return autocorrelation +0.249 at lag one, median of six seeds at the published method | 0.249 | 0.249 | -1.26e-05 | reproduced |
| 288 | of the eight statistics, two are marginal and six are dependence | 2 | 2 | 0 | reproduced |
| 290 | cross-sectional correlation +0.257 (real +0.25 to +0.35) | 0.257 | 0.2566 | -0.000435 | reproduced |
| 291 | volatility clustering +0.242 (+0.15 to +0.35) | 0.242 | 0.2424 | 0.000435 | reproduced |
| 291 | volume against |return| +0.585 (+0.30 to +0.60) | 0.585 | 0.5853 | 0.000332 | reproduced |
| 292 | excess kurtosis +3.1 (+3 to +10) | 3.1 | 3.114 | 0.01352 | reproduced |
| 293 | a leverage effect at -0.085, just short of its -0.30 to -0.10 band | -0.085 | -0.0854 | -0.000396 | reproduced |
| 293 | the leverage effect holds its sign in six seeds of six | True | True | - | structural_ok |
| 297 | held out, fresh sim seeds read correlation +0.225 against a +0.25 band floor | 0.225 | 0.2253 | 0.000252 | reproduced |
| 306 | annualised volatility runs 41.5% against a real 15-35% | 41.5 | 41.5 | 0.000596 | reproduced |
| 309 | volume changes autocorrelate at -0.45 where real ones sit near zero | -0.45 | -0.4461 | 0.003931 | reproduced |
| 335 | worked example: seconds end to end - five, on an Apple-silicon laptop | 5 | 14.91 | 9.911 | machine_bound |
| 336 | worked example: five-agent evaluation | 5 | 5 | 0 | reproduced |
| 336 | worked example: 20-seed sweep | 20 | 20 | 0 | reproduced |
| 337 | worked example: 234,000 rows of ground truth | 234,000 | 234,000 | 0 | reproduced |
| 341 | the worked example's internal assertions all pass | True | True | - | structural_ok |

### docs/agents-and-evaluation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 42 | the Oracle spends its budget equal weight, long the five most underpriced and short the five most overpriced (top_k=5 per side) | 5 | 5 | 0 | reproduced |
| 48 | momentum beats the Oracle 4 of 12 on the twelve-market grid | 4 | 4 | 0 | reproduced |
| 49 | mean_reversion beats the Oracle 1 of 12 | 1 | 1 | 0 | reproduced |
| 50 | buy_and_hold beats the Oracle 1 of 12 | 1 | 1 | 0 | reproduced |
| 51 | random beats the Oracle 0 of 12 | 0 | 0 | 0 | reproduced |
| 60 | mean-reversion's one win is barely above 1.0: capture 1.004 | 1.004 | 1.004 | -0.000163 | reproduced |
| 61 | buy-and-hold's one win is capture 1.59... | 1.59 | 1.59 | 5.93e-05 | reproduced |
| 61 | ...the largest capture on the whole grid | True | True | - | structural_ok |
| 70 | Oracle median P&L on the ranking grid at ten days, sim seeds 0-7, default top_k=5: $93k | 93,000 | 92,618 | -381.7 | reproduced |
| 70 | the same information across three times as many names, top_k=15: $65k | 65,000 | 64,869 | -131 | reproduced |
| 73 | mispricing reverts on a 60-day half-life | 60 | 60 | 5.59e-11 | reproduced |
| 76 | the Oracle makes $85k in five days on seed 2026 over random(40,7) | 85,000 | 84,768 | -232.1 | reproduced |
| 76 | and $504k in sixty | 504,000 | 504,392 | 392.3 | reproduced |
| 77 | momentum's capture ratio against the five-day denominator: 0.40 | 0.4 | 0.3967 | -0.003337 | reproduced |
| 77 | and 1.24 against the sixty-day one - the horizon alone carries the verdict across 1.0 | 1.24 | 1.242 | 0.001519 | reproduced |
| 88 | over twelve markets momentum ranks +0.593 pooled | 0.593 | 0.5928 | -0.000238 | reproduced |
| 88 | mean-reversion +0.160 | 0.16 | 0.1603 | 0.000318 | reproduced |
| 89 | and equally often crowns mean-reversion: 5 of 12 | 5 | 5 | 0 | reproduced |
| 89 | a single seed picks the pooled leader only five times in twelve | 5 | 5 | 0 | reproduced |
| 92 | momentum capture range, high: +1.523 | 1.523 | 1.523 | 0.000379 | reproduced |
| 92 | momentum capture range, low: +0.089 | 0.089 | 0.08852 | -0.000484 | reproduced |
| 101 | momentum vs mean_reversion: 7-5 | 7-5 | 7-5 | - | reproduced |
| 101 | p = 0.77 | 0.77 | 0.7744 | 0.004414 | reproduced |
| 102 | momentum vs random: 12-0, a clean sweep | 12-0 | 12-0 | - | reproduced |
| 102 | p = 0.0005, the floor twelve paired seeds can produce | 0.0005 | 0.000488 | -1.17e-05 | reproduced |
| 113 | even a clean sweep only reaches p = 0.0005 | 0.0005 | 0.000488 | -1.17e-05 | reproduced |
| 115 | the identical test over seeds 12-23: momentum over mean-reversion 9-3 | 9-3 | 9-3 | - | reproduced |
| 115 | p = 0.15 | 0.15 | 0.146 | -0.004004 | reproduced |
| 126 | ...to $45.6k | 45,600 | 45,608 | 8.227 | reproduced |
| 126 | three days, seeds 0-9: the reference's per-seed P&L spans $11.2k... | 11,200 | 11,183 | -17.3 | reproduced |
| 127 | against a pooled +0.44 across the ten | 0.44 | 0.4368 | -0.003227 | reproduced |
| 127 | mean-reversion's ratio on the thinnest of those markets: +1.29 | 1.29 | 1.293 | 0.003264 | reproduced |
| 128 | every ratio above 1.0 it posts sits on one of the four thinnest denominators | True | True | - | structural_ok |
| 132 | averaging the ten ratios instead of pooling them would move the verdict to +0.61 | 0.61 | 0.6119 | 0.001905 | reproduced |

### docs/an-llm-agent.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 30 | oracle pnl 229751 | 229,751 | 229,751 | -0.09511 | reproduced |
| 30 | oracle why-right 100% | 100 | 100 | 0 | reproduced |
| 32 | momentum why-right prints '-' (no explain()) | True | True | - | structural_ok |
| 32 | momentum pnl 21520 | 21,520 | 21,520 | 0.2585 | reproduced |
| 49 | ...to +470 bps across seeds 2020-2031 | 470 | 469.8 | -0.1769 | reproduced |
| 49 | oracle twenty-day impact spans -235... | -235 | -235.2 | -0.1518 | reproduced |
| 49 | positive in only 7 of 12 seeds | 7 | 7 | 0 | reproduced |
| 50 | momentum's twenty-day impact flips sign the same way | True | True | - | structural_ok |
| 52 | over two days both agents are positive in 12 of 12 | 12 | 12 | 0 | reproduced |
| 53 | by day three the oracle's sign already belongs to the seed: positive in 8 of 12 | 8 | 8 | 0 | reproduced |
| 53 | ...to +90 bps | 90 | 90.2 | 0.2028 | reproduced |
| 53 | with a span of -29... | -29 | -29.19 | -0.1885 | reproduced |
| 73 | driver is one of the engine's seven factors | 7 | 7 | 0 | reproduced |
| 106 | steps_per_day defaults to 6 | 6 | 6 | 0 | reproduced |
| 124 | default run is 20 days over 12 instruments, so 20 calls | 20 | 20 | 0 | reproduced |
| 124 | a run lands around $0.30 to $0.60 at Opus 5 rates | 0.30-0.60 | - | - | not_harnessable |

### docs/conventions.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 26 | Universe.random short interest: median about 3.7% of shares outstanding | 3.7 | 3.414 | -0.2861 | reproduced |
| 27 | roughly one name in eleven above the 20% squeeze threshold | 11 | 12.66 | 1.658 | reproduced |
| 39 | the preset dictionary carries eight numbers | 8 | 8 | 0 | reproduced |

### docs/core-concepts.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 56 | vix takes a new value every day: 120 distinct values in 120 days | 120 | 120 | 0 | reproduced |
| 58 | federal_funds_rate takes 2 distinct values over the run | 2 | 2 | 0 | reproduced |
| 59 | corporate_bond_yield 3 | 3 | 3 | 0 | reproduced |
| 59 | gdp_growth 6 | 6 | 6 | 0 | reproduced |
| 59 | inflation_rate 4 | 4 | 4 | 0 | reproduced |
| 60 | fundamental_value takes 3 distinct values per instrument | 3 | 3 | 0 | reproduced |
| 61 | repricing when the discount rate moves at a meeting - days 45 and 96 of that run | 45,96 | 45,96 | - | reproduced |
| 62 | the loss-maker valued off book value never reprices | 1 | 1 | 0 | reproduced |
| 73 | corporate bond yield defaults to 0.0456 | 0.0456 | 0.0456 | 0 | reproduced |
| 79 | pin_macro(corporate_bond_yield=0.09) repriced nineteen of twenty | 19 | 19 | 0 | reproduced |
| 80 | the twentieth is a loss-maker valued off book value | 1 | 1 | 0 | reproduced |
| 81 | pinning federal_funds_rate alone repriced none of the twenty | 0 | 0 | 0 | reproduced |

### docs/forking-a-simulation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 22 | pt.branch cost < 1 ms | 1 | 0.05879 | -0.9412 | reproduced |
| 23 | Checkpoint.resume() cost 2.7 s | 2.7 | 0.4826 | -2.217 | machine_bound |
| 26 | Checkpoint replay is three orders of magnitude slower than branch | 3 | 3.914 | 0.9143 | reproduced |

### docs/ground-truth.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | factors sum to change in mispricing_s, residual around 1e-16 | 1e-16 | 1.43e-17 | - | reproduced |

### docs/how-realistic-is-this-market.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 38 | sample report: 10,040 daily returns | 10,040 | 10,040 | 0 | reproduced |
| 43 | sample report: annualised vol | 43.87 | 43.87 | 0.000247 | reproduced |
| 44 | sample report: excess kurtosis | 3.172 | 3.172 | 5.89e-05 | reproduced |
| 47 | sample report: return acf(1) | 0.274 | 0.2737 | -0.000314 | reproduced |
| 48 | sample report: |return| acf(1) | 0.244 | 0.2436 | -0.000366 | reproduced |
| 49 | sample report: cross-sectional corr | 0.265 | 0.2653 | 0.000273 | reproduced |
| 50 | sample report: volume vs |return| | 0.59 | 0.5902 | 0.000217 | reproduced |
| 51 | sample report: leverage | -0.137 | -0.1367 | 0.000308 | reproduced |
| 52 | sample report: volume change acf(1) | -0.425 | -0.4249 | 8.54e-05 | reproduced |
| 72 | universe fingerprint 5d8de78b55aad752 | 5d8de78b55aad752 | 5d8de78b55aad752 | - | reproduced |
| 80 | excess kurtosis, median of six seeds | 3.1 | 3.114 | 0.01352 | reproduced |
| 81 | annualised vol, median of six seeds | 41.5 | 41.5 | 0.000596 | reproduced |
| 87 | return acf(1), median of six seeds | 0.249 | 0.249 | -1.26e-05 | reproduced |
| 88 | |return| acf(1), median of six seeds | 0.242 | 0.2424 | 0.000435 | reproduced |
| 89 | cross-sectional corr, median of six seeds | 0.257 | 0.2566 | -0.000435 | reproduced |
| 90 | volume vs |return|, median of six seeds | 0.585 | 0.5853 | 0.000332 | reproduced |
| 91 | leverage, median of six seeds | -0.085 | -0.0854 | -0.000396 | reproduced |
| 92 | volume change acf(1), median of six seeds | -0.446 | -0.4461 | -6.94e-05 | reproduced |
| 100 | kurtosis reads +2.4 on one seed of six | 2.4 | 2.384 | -0.01565 | reproduced |
| 101 | correlation's range reaches +0.46 | 0.46 | 0.4563 | -0.003705 | reproduced |
| 127 | held out, fresh sim seeds 101-106: cross-sectional correlation +0.225 | 0.225 | 0.2253 | 0.000252 | reproduced |
| 128 | clustering +0.202 | 0.202 | 0.2017 | -0.000273 | reproduced |
| 128 | kurtosis +3.67 | 3.67 | 3.665 | -0.004587 | reproduced |
| 129 | leverage reads -0.071 | -0.071 | -0.07045 | 0.000549 | MOVED |
| 129 | volume vs |return| +0.546 | 0.546 | 0.5458 | -0.000228 | reproduced |
| 135 | ...to +0.35 | 0.35 | 0.3469 | -0.003122 | reproduced |
| 135 | five fresh 60-name universes: correlation medians run +0.29... | 0.29 | 0.2878 | -0.002181 | reproduced |
| 135 | ...to +4.7 | 4.7 | 4.671 | -0.02858 | reproduced |
| 135 | and kurtosis +3.4... | 3.4 | 3.362 | -0.03807 | reproduced |
| 136 | ...to +0.21 | 0.21 | 0.2107 | 0.000662 | reproduced |
| 136 | clustering reads +0.20... | 0.2 | 0.197 | -0.003026 | reproduced |
| 137 | ...to -0.04, half its published-method value | -0.04 | -0.04218 | -0.002183 | reproduced |
| 137 | the leverage effect weakens to -0.05... | -0.05 | -0.04914 | 0.000856 | reproduced |
| 141 | clustering +0.25 | 0.25 | 0.2509 | 0.000894 | reproduced |
| 141 | the published universe over 504 days: correlation +0.34 | 0.34 | 0.3405 | 0.000547 | reproduced |
| 141 | kurtosis +3.2 | 3.2 | 3.207 | 0.007382 | reproduced |
| 141 | leverage -0.062 | -0.062 | -0.06189 | 0.000105 | reproduced |
| 142 | volatility 47.6% | 47.6 | 47.56 | -0.03555 | reproduced |
| 163 | the factor's baseline sigma is 0.016 a day (the 0.003 beside it is named history) | 0.016 | 0.016 | 0 | reproduced |
| 165 | per-name idiosyncratic noise is scaled down by 0.84 | 0.84 | 0.84 | 0 | reproduced |
| 169 | pinned VIX 45 takes mean pairwise correlation to +0.68 | 0.68 | 0.6777 | -0.002338 | reproduced |
| 169 | and VIX 65 to +0.76 | 0.76 | 0.7595 | -0.000529 | reproduced |
| 170 | against +0.27 calm | 0.27 | 0.2687 | -0.001321 | reproduced |
| 173 | the correlation blend engages above VIX 25.5 | 25.5 | 25.5 | 0 | reproduced |
| 182 | the pre-era sweep found the correlation band reachable only where kurtosis had collapsed to 1.26 | 1.26 | - | - | not_harnessable |
| 190 | the per-name GJR-GARCH's effective persistence ALPHA + BETA + GAMMA/2 is 0.99 | 0.99 | 0.99 | 1.11e-16 | reproduced |
| 191 | the factor variance process's shocks decay with a half-life of about 13.5 days | 13.5 | 13.51 | 0.01341 | reproduced |
| 199 | clustering gone by lag twenty: -0.006 | -0.006 | -0.006058 | -5.83e-05 | reproduced |
| 199 | clustering reads +0.090 at lag five | 0.09 | 0.09018 | 0.000175 | reproduced |
| 215 | the leverage sign is stable: negative in six seeds of six | True | True | - | structural_ok |
| 219 | against ALPHA = 0.02 for a positive one | 0.02 | 0.02 | 0 | reproduced |
| 219 | a negative day's squared return feeds through at ALPHA + GAMMA = 0.36 | 0.36 | 0.36 | 5.55e-17 | reproduced |
| 223 | GAMMA = 0.34, already large against literature GJR fits | 0.34 | 0.34 | 0 | reproduced |
| 232 | AR(2) impulse response rises to 1.284 by day two | 1.284 | 1.284 | -8.3e-05 | reproduced |
| 279 | raising the variance ceiling bought +0.016 of clustering for 20 points of volatility | 0.016 | - | - | not_harnessable |
| 290 | MOMENTUM_THETA 0.25 -> 0.05 takes return acf to +0.034 | 0.034 | - | - | not_harnessable |

### docs/index.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 55 | measured green on all five targets at commit ad91026 | True | - | - | not_harnessable |
| 56 | the current baseline's digest has been reproduced on one platform so far | True | True | - | structural_ok |
| 65 | seven factor contributions, residual around 1e-16 | 1e-16 | 1.43e-17 | - | reproduced |

### docs/performance.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 14 | 252 days x 10 instruments: 2.9 s | 2.9 | 1.751 | -1.149 | machine_bound |
| 15 | 252 days x 100 instruments: 27.4 s | 27.4 | 15.63 | -11.77 | machine_bound |
| 16 | 252 x 100 recording 9.8M rows: 28.2 s | 28.2 | 17.48 | -10.72 | machine_bound |
| 16 | a recorded year at 100 instruments is 9.8M rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 17 | 8 seeds x 21 days x 100, serial: 20.0 s | 20 | 12.47 | -7.531 | machine_bound |
| 18 | 8 seeds x 21 days x 100, 8 workers: 6.1 s | 6.1 | 3.046 | -3.054 | machine_bound |
| 20 | recording a full year of tick-grain ground truth costs a few percent at most (bound) | 4 | 0.7636 | -3.236 | reproduced |
| 24 | nearly half the pairs came out negative | 0.5 | 0.375 | -0.125 | reproduced |
| 24 | the median was about +1% | 1 | 0.7636 | -0.2364 | reproduced |
| 25 | the 0.8s between the two 252x100 rows is run-to-run noise | 0.8 | 0.8 | 6.66e-16 | reproduced |
| 28 | sweeps parallelise about 3.3x on eight cores | 3.3 | 4.094 | 0.7941 | machine_bound |
| 32 | cost scales roughly linearly in instruments x days (t100/t10 ~ 9.4 published) | 9.45 | 8.925 | -0.5249 | reproduced |

### docs/reading-results.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | ten levels a side makes the book table 20x the rows | 20 | 20 | 0 | reproduced |
| 35 | recording ground truth costs a few percent at most (bound) | 4 | 0.7636 | -3.236 | reproduced |

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
| 158 | rebuilt universe fingerprint matches the archive | True | True | - | structural_ok |
| 162 | replayed.prices() == engine.prices() | True | True | - | structural_ok |
| 163 | replayed.draws_consumed == engine.draws_consumed | True | True | - | structural_ok |
| 199 | at ad91026 (v5) all five targets produced the identical digest 76983e65...3180eeb | 76983e65...3180eeb | - | - | not_harnessable |
| 202 | at a5afd1c two independent platform builds produced the identical digest (historical record) | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | - | - | not_harnessable |
| 207 | the current digest, 1ee64998...fe3581c at v8 | 1ee64998...fe3581c | 1ee64998...fe3581c | - | reproduced |
| 207 | 'the current digest ... at v8' - the era named as current | 8 | 8 | 0 | reproduced |

### docs/rng-streams.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 10 | the engine derives three independent substreams from the root seed | 3 | 3 | 0 | reproduced |
| 36 | 'KAT_VERSION = 5 at the split' - a historical record pinned to ad91026 | 5 | - | - | not_harnessable |
| 37 | the era's later model changes have since taken the known-answer version to 8 | 8 | 8 | 0 | reproduced |
| 48 | the market stream's schedule is a pure function of (market status, active roster, sector count) | True | True | - | structural_ok |
| 58 | draws_by_stream() reports market, economy, external | market,economy,external | market,economy,external | - | reproduced |
| 68 | the substream derivation contract (splitmix64 finalizer, sequence 256+k) | True | - | - | covered_by_tests |
| 117 | raw sequences 0/1 (constructors), 21 (universe), 99 (reference MAIN) | True | - | - | covered_by_tests |
| 136 | state_snapshot()['rng'] is nine numbers, three per stream | 9 | 9 | 0 | reproduced |
| 141 | a pre-split snapshot (three numbers) is refused on restore | True | True | - | structural_ok |

### docs/running-a-simulation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 50 | a tick loop crosses the boundary about 98,000 times per simulated year | 98,000 | 98,280 | 280 | reproduced |
| 51 | five fields per tick makes roughly 500,000 | 500,000 | 491,400 | -8,600 | reproduced |

### docs/scenarios.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 24 | buy_and_hold calm return | -7.16 | -7.16 | 3.58e-06 | reproduced |
| 24 | buy_and_hold delta | -3.59 | -3.589 | 0.000691 | reproduced |
| 24 | buy_and_hold hiked return | -10.75 | -10.75 | 0.000694 | reproduced |
| 25 | momentum calm return | -1.17 | -1.166 | 0.004357 | reproduced |
| 25 | momentum delta | -2.36 | -2.357 | 0.002927 | reproduced |
| 25 | momentum hiked return | -3.52 | -3.523 | -0.002716 | reproduced |
| 26 | oracle calm return | 11.11 | 11.11 | -0.003906 | reproduced |
| 26 | oracle delta | -2.41 | -2.406 | 0.003511 | reproduced |
| 26 | oracle hiked return | 8.7 | 8.7 | -0.000396 | reproduced |
| 34 | ...to -3.4 | -3.4 | -3.373 | 0.027 | reproduced |
| 34 | across sim seeds 5 to 9 buy-and-hold gives up 3.4 to 4.7 points (delta -4.7...) | -4.7 | -4.667 | 0.03338 | reproduced |
| 35 | and never escapes | 0 | 0 | 0 | reproduced |
| 35 | ...to +0.5 | 0.5 | 0.4527 | -0.04731 | reproduced |
| 35 | momentum's give-up spans -5.1... | -5.1 | -5.077 | 0.02317 | reproduced |
| 36 | on one seed in five it trades around the shock entirely | 1 | 1 | 0 | reproduced |
| 41 | a 60-day run crossing the day-45 central-bank meeting reprices a median -4.29% | -4.29 | -4.287 | 0.003003 | reproduced |
| 41 | pinning federal_funds_rate alone: exactly 0.00% across twenty instruments over 40 days | 0 | 0 | 0 | reproduced |
| 68 | annualised realised vol at VIX 5 | 49.48 | 49.48 | -0.000249 | reproduced |
| 69 | annualised realised vol at VIX 15 | 58.76 | 58.76 | 0.003146 | reproduced |
| 70 | annualised realised vol at VIX 45 | 107.1 | 107.1 | -0.003273 | reproduced |
| 71 | annualised realised vol at VIX 65 | 124.3 | 124.3 | 0.00151 | reproduced |
| 73 | a thirteenfold move in VIX moves realised volatility by a factor of 2.5 | 2.5 | 2.512 | 0.01237 | reproduced |
| 77 | day one's closes are bit-identical across VIX 5, 10 and 15 | True | True | - | structural_ok |
| 78 | and day two's differ for every pair | True | True | - | structural_ok |
| 83 | the factor's variance is clamped at 8x its baseline | 8 | 8 | 0 | reproduced |
| 96 | quoted bid-ask widens through 1 + max(0, (vix - 15) / 30) | True | True | - | structural_ok |
| 98 | mean quoted spread at VIX 15 | 11.52 | 11.52 | 0.002534 | reproduced |
| 98 | mean quoted spread at VIX 25 | 13.92 | 13.92 | -0.003654 | reproduced |
| 98 | mean quoted spread at VIX 45 | 18.87 | 18.87 | -0.003907 | reproduced |
| 98 | mean quoted spread at VIX 65 | 25.89 | 25.89 | -0.001277 | reproduced |
| 99 | cross-sectional correlation blends above VIX 25.5 | 25.5 | 25.5 | 0 | reproduced |
| 104 | mean pairwise correlation at VIX 15, 300 pairs | 0.269 | 0.2687 | -0.000321 | reproduced |
| 104 | mean pairwise correlation at VIX 45 | 0.678 | 0.6777 | -0.000338 | reproduced |
| 104 | mean pairwise correlation at VIX 65 | 0.759 | 0.7595 | 0.000471 | reproduced |
| 128 | median shortfall at VIX 45 | 11.69 | 11.69 | -0.004181 | reproduced |
| 128 | median shortfall at VIX 15 | 6.06 | 6.064 | 0.004119 | reproduced |
| 128 | VIX 45 regime costs more in 12 of 12 seeds | 12 | 12 | 0 | reproduced |
| 129 | paired median delta | 5.62 | 5.622 | 0.0017 | reproduced |
| 144 | an older build recorded a delta of -4 in 425,600 draws | -4 | - | - | not_harnessable |
| 145 | the market stream's schedule is a pure function of (market status, active roster, sector count) | True | True | - | structural_ok |
| 153 | macro-counterfactual draw divergence zero in every comparison run | 0 | 0 | 0 | reproduced |

### docs/sharing-a-run.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 67 | pt.version() stayed '0.1.0' across the era boundary | 0.1.0 | 0.1.0 | - | reproduced |
| 68 | and the preset stayed 'pt-v1' | pt-v1 | pt-v1 | - | reproduced |
| 68 | 'the recalibrated constant is not even in the preset dictionary' | True | True | - | structural_ok |
| 73 | reproduce() recomputes the era fingerprint before replaying and refuses across an era boundary | True | - | - | covered_by_tests |
| 90 | the five-target gate has run: identical digest 76983e65...3180eeb at ad91026 | 76983e65...3180eeb | - | - | not_harnessable |
| 92 | the current digest, 1ee64998...fe3581c at v8, one platform so far | 1ee64998...fe3581c | 1ee64998...fe3581c | - | reproduced |

### docs/strategy-specs.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 27 | methods-section universe fingerprint a7861d15... | a7861d15 | a7861d15 | - | reproduced |
| 28 | strategy spec fingerprint e6bbc35c... | e6bbc35c | e6bbc35c | - | reproduced |
| 29 | oracle spec fingerprint f383b990... | f383b990 | f383b990 | - | reproduced |
| 55 | spec-built and hand-built baselines score bit-identically | True | - | - | covered_by_tests |
| 103 | blend weights 1.2/0.8 and 0.6/0.4 build bit-identical agents | True | - | - | covered_by_tests |
| 152 | the rebalance table swings the same signal from +97.5%... | 97.5 | 97.45 | -0.04618 | reproduced |
| 152 | ...to +0.1% purely by trading it more often | 0.1 | 0.1043 | 0.0043 | reproduced |

### docs/sweeps-and-parallelism.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 17 | twelve buffers of 9.8 million f64 - about 940 MB | 940 | 943.5 | 3.488 | reproduced |
| 18 | materialises 9.8 million rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 19 | a hundred resident engines is roughly 90 GB | 90 | 94.35 | 4.349 | reproduced |
| 29 | per-seed parallelism survives the split into three per-domain substreams | 3 | 3 | 0 | reproduced |

### docs/transaction-cost-analysis.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 26 | the first name of random(20,7) has ADV 9,713 shares | 9,713 | 9,713 | 0 | reproduced |
| 30 | buying 97 shares (1% ADV) at the first step and holding costs +16.71 bps | 16.71 | 16.71 | -0.00245 | reproduced |
| 30 | the entry cost is identical on every seed measured | True | True | - | structural_ok |
| 33 | the round trip ends anywhere between -13.3... | -13.3 | -13.25 | 0.05161 | reproduced |
| 34 | ...and +5.8 bps | 5.8 | 5.757 | -0.04272 | reproduced |
| 34 | negative on six of the eight | 6 | 6 | 0 | reproduced |
| 35 | the round trip's median across the eight published seeds: -8.4 bps | -8.4 | -8.357 | 0.04327 | reproduced |
| 44 | the request was for 4,856 shares (half ADV) | 4,856 | 4,856 | 0 | reproduced |
| 45 | a request for 4,856 shares (half ADV, sim seed 2026) filled 483 | 483 | 483 | 0 | reproduced |
| 46 | requests of 9,713 and 48,563 shares fill the same 483 | True | True | - | structural_ok |
| 47 | 483 at the open on every seed measured - opening depth is a property of the universe | True | True | - | structural_ok |
| 56 | order flow consumes no RNG draws - the per-domain stream split exists for exactly this | True | - | - | covered_by_tests |
| 65 | two of the fourteen it never touched moved | 2 | 2 | 0 | reproduced |
| 65 | the agent traded 46 names | 46 | 46 | 0 | reproduced |
| 65 | leaving fourteen untouched | 14 | 14 | 0 | reproduced |
| 66 | against a 13.0 bps median direct impact on the traded names | 13 | 13 | 0.003901 | reproduced |
| 66 | ...and +3.2 bps | 3.2 | 3.234 | 0.03423 | reproduced |
| 66 | by -6.5... | -6.5 | -6.55 | -0.04965 | reproduced |
| 69 | the same configuration run for two, three or four days leaks nothing | True | True | - | structural_ok |
| 84 | pinned, untouched_moved() comes back empty - byte-exact | True | True | - | structural_ok |

## Notes

- **readme.env_checker** (README.md:60): asserted by tests/test_gym.py when gymnasium is installed; the docs page carries the same claim (rl.env_checker)
- **readme.model_crn** (README.md:195): asserted by tests/test_model_params.py: draws_consumed is identical across parameter vectors, the CRN guard that makes panel differences parameter effects
- **index.kat_five_targets** (docs/index.md:55): the page now carries the by-commit record instead of the unqualified five-target claim (retired index.kat_digest); the record itself is pinned to ad91026 - see repro.kat_five_targets
- **readme.kat_digest** (README.md:212): pinned to commit a5afd1c and not re-measurable from the installed package; the current era's digest is checked by readme.kat_current_digest
- **readme.kat_five_targets** (README.md:213): a historical record of the determinism gate's run at ad91026, pinned to that commit; see repro.kat_five_targets
- **readme.t100** (README.md:263): machine-bound and load-bound: this laptop measures 7.1 s quiet (the committed dd718e7 baseline) and 16.8 s contended in the same week
- **readme.t100_apple** (README.md:264): machine- and load-bound: 7.1 s quiet on this laptop (dd718e7 baseline), 16.8 s contended; reported, never judged
- **readme.record_overhead** (README.md:265): the README now carries the bound wording the 32-pair interleaved study demoted the old 'adds 3%' point to; operationalised like perf.overhead as median per-pair overhead under 4%
- **readme.speedup** (README.md:267): parallel speedup is a property of the measuring machine's core count and memory bandwidth, so it is reported rather than judged; the printed 3-4x is a range and this row carries its midpoint
- **readme.separation_comment** (README.md:113): the prose now states the same grid the agents page measures; the old stale snippet comment is gone
- **core.fv_distinct** (docs/core-concepts.md:60): replaces the retired core.fv_static (published 1): the frozen-macro section inverted at the era boundary
- **fork.resume_s** (docs/forking-a-simulation.md:23): a wall-clock absolute like readme.t100: machine-bound, reported but never judged at printed precision (kind=timing). The portable, judged form of this claim is fork.three_orders, the branch/resume ratio on the same 30-day run, held to a +/-1.2 band on log10
- **perf.overhead** (docs/performance.md:20): 'a few percent at most' operationalised as median per-pair overhead under 4%; the point claim was demoted to a bound after a 32-pair interleaved study across two sessions: median about +1%, nearly half the pairs negative, so no wall-clock point resolves it on a working machine
- **perf.overhead_median** (docs/performance.md:24): judged as a wide band, not at printed precision: the page itself says to expect the measurement to straddle zero, and observed session medians span -2.8% to +1.8% depending on machine load. The row flags only if the median leaves [-3, +5], which would genuinely contradict the page
- **perf.speedup** (docs/performance.md:28): hardware-bound like the absolute times; reported, not judged
- **perf.linear** (docs/performance.md:32): a ratio of two wall clocks, so judged generously: min-of-few timings wobble under load (measured 9.0 quiet, 6.3 loaded against 9.45 published). Anything inside [5.2, 13.7] still supports 'roughly linear' for 10x the work
- **reading.record_overhead** (docs/reading-results.md:35): 'a few percent at most' operationalised as median per-pair overhead under 4%; the point claim was demoted to a bound after a 32-pair interleaved study across two sessions: median about +1%, nearly half the pairs negative, so no wall-clock point resolves it on a working machine
- **scen.bit_day1** (docs/scenarios.md:77): replaces the retired 60-day form of scen.bit_identical: the coupling ends bit-identity at the first close a pin can reach
- **scen.old_draw_delta** (docs/scenarios.md:144): a historical record of a pre-split build, kept on the page as provenance; not re-measurable on this build
- **scen.market_sched** (docs/scenarios.md:145): replaces the retired scen.settle_conditional: the paragraph now states the post-split guarantee instead of the four-or-zero settlement branch
- **rng.kat_v5** (docs/rng-streams.md:36): the split's own version is a property of commit ad91026, not of this build; the current version is measured by rng.kat_now
- **rng.derivation** (docs/rng-streams.md:68): pinned by the golden test rng.rs::substream_derivation_is_the_documented_formula against hand-computed values
- **rng.sequence_bases** (docs/rng-streams.md:117): the universe sequence is exercised by every pinned fingerprint (realism.fingerprint, spec.universe_fp); 99 by the golden-parity replay harnesses; 256+k by the derivation golden test
- **agents.sep_mom_mr** (docs/agents-and-evaluation.md:101): the snippet comment at line 101; the prose repeats the separation at line 110 - edit them together
- **agents.sep_mom_rand** (docs/agents-and-evaluation.md:102): the prose repeats it at line 112
- **llm.imp3_oracle_pos** (docs/an-llm-agent.md:53): re-keyed from llm.imp3_all_pos (both agents 12 of 12 at three days), which PR 26's re-measurement retired: three days no longer reads clean
- **llm.cost** (docs/an-llm-agent.md:124): priced by a third-party API, not measurable from the package
- **realism.market_sigma** (docs/how-realistic-is-this-market.md:163): the page's old mechanism paragraph (0.003, sector sigmas, variance share) is gone; 0.003 survives only as the reference implementation's value, so this row now pins the recalibrated constant the page publishes
- **realism.sweep_kurtosis_collapse** (docs/how-realistic-is-this-market.md:182): a historical record of the tools/calibration sweeps on the pre-era model; re-measuring it means rebuilding that model
- **realism.theta_counterfactual** (docs/how-realistic-is-this-market.md:290): requires rebuilding the engine with a changed constant; deliberately not a runtime knob
- **realism.ceiling_counterfactual** (docs/how-realistic-is-this-market.md:279): requires rebuilding the engine with a changed constant
- **repro.kat_historical** (docs/reproducing-a-run.md:202): pinned to commit a5afd1c; see readme.kat_digest
- **repro.kat_five_targets** (docs/reproducing-a-run.md:199): a historical record of the determinism gate's run at ad91026, pinned to that commit; not re-measurable from the installed package
- **repro.kat_era_version** (docs/reproducing-a-run.md:207): the page now carries the by-commit record: v5 measured on all five targets at ad91026 (repro.kat_five_targets), v6/v7/v8 from the era's model changes, v8 current
- **share.era_refusal** (docs/sharing-a-run.md:73): asserted by tests/test_manifest.py (test_a_different_era_is_refused_before_anything_replays and neighbours)
- **share.kat_five_targets** (docs/sharing-a-run.md:90): the page now states the by-commit record instead of 'the gate has not yet run'; the record is pinned to ad91026 - see repro.kat_five_targets
- **tca.entry_cost** (docs/transaction-cost-analysis.md:30): was method_unknown ('one measured example', nothing stated); stream AB attached the test suite's pinned configuration and the value reproduced
- **tca.roundtrip_cost** (docs/transaction-cost-analysis.md:35): was method_unknown and a point (-10.8); the era's re-deal flipped the anchor seed, so the page now publishes a seed range and this row is judged as one: the median against the printed -8.4, falling back to the published -13.3..+5.8 range
- **tca.roundtrip_lo** (docs/transaction-cost-analysis.md:33): the page rounds the range outward (measured -13.25 prints as -13.3), so the endpoints are judged to within one outward-rounding step rather than at nearest-decimal precision
- **tca.roundtrip_hi** (docs/transaction-cost-analysis.md:34): see tca.roundtrip_lo on outward rounding
- **tca.partial_fill** (docs/transaction-cost-analysis.md:45): was method_unknown; the mechanism is now stated correctly on the page as the book truncating at displayed depth (tca.fill_saturates, tca.fill_seed_invariant), and examples/research_workflow.py asserts the structural gate every run
- **tca.fill_saturates** (docs/transaction-cost-analysis.md:46): also asserted structurally by examples/research_workflow.py every run
- **tca.no_draws** (docs/transaction-cost-analysis.md:56): asserted by tests/test_tca.py (the untraded world is unmoved where the trader did not go) and tests/test_streams.py; the qualified successor of the retired scen.tca_exact
- **tca.ripple_untouched** (docs/transaction-cost-analysis.md:65): PR 25 said '2 of 44 untraded names'; on this build Momentum trades 46 of the 60, so the page's 14 is the honest denominator and it reproduces
- **tca.ripple_pinned_empty** (docs/transaction-cost-analysis.md:84): also asserted by examples/research_workflow.py every run (readme.workflow_asserts executes it)
- **sweeps.buffers** (docs/sweeps-and-parallelism.md:17): the truth table has 13 columns; 'twelve' undercounts by one, though the byte arithmetic still lands on the page's 'about 940 MB'
- **edgar.requests** (docs/real-fundamentals-from-sec-edgar.md:26): a network-shape claim; exercised by tests/test_edgar.py through the injectable transport, not re-measured here
- **rl.env_checker** (docs/reinforcement-learning.md:18): asserted by tests/test_gym.py when gymnasium is installed
- **spec.bitwise_baselines** (docs/strategy-specs.md:55): asserted by tests/test_spec.py
- **spec.weight_scale** (docs/strategy-specs.md:103): asserted by tests/test_spec.py (fingerprint scale invariance)
- **readme.workflow_asserts** (README.md:341): after an engine change a failure here means the example script itself needs re-fitting, not just the prose
