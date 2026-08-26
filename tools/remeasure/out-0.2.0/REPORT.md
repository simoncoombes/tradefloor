# Published-figure re-measurement

Commit `e3396b9`, 2026-08-26 16:24, pretium 0.2.0. Full run: 473s wall with 8 workers.

| status | figures |
|---|---|
| reproduced | 106 |
| within_seed_variation | 5 |
| MOVED | 165 |
| machine_bound | 11 |
| structural_ok | 37 |
| structural_fail | 5 |
| not_harnessable | 12 |
| covered_by_tests | 10 |

## Doc edits needed

Every row here is a published number the stated (or reconstructed)
method no longer produces. On unchanged main these are documents that
were already stale; after an engine change, this section IS the edit
list.

| where | figure | published | measured |
|---|---|---|---|
| README.md:115 | a single seed picks the pooled leader only five times in twelve | 5 | 1 |
| README.md:190 | a nudged garch_alpha fingerprints as custom-0c04c4ba, never pt-v1 | custom-0c04c4ba | custom-a3658094 |
| README.md:214 | the current digest 1ee64998...fe3581c at v8 | 1ee64998...fe3581c | 4e22d5a6...e860378 |
| README.md:214 | the table's current-era row: known-answer v8 at 6e30497 | 8 | 10 |
| README.md:227 | VIX takes a new value every day over the 120-day macro-chain run | 120 | 117 |
| README.md:235 | 49% annualised at VIX 5 | 49 | 31.39 |
| README.md:235 | 59% at 15 | 59 | 37.01 |
| README.md:235 | 107% at 45 | 107 | 104.9 |
| README.md:235 | 124% at 65 | 124 | 125.8 |
| README.md:238 | mean pairwise correlation reads +0.27 calm | 0.27 | 0.1964 |
| README.md:238 | +0.68 at VIX 45 | 0.68 | 0.6218 |
| README.md:238 | +0.76 at 65 | 0.76 | 0.6799 |
| README.md:252 | 3 rebalances per day: +97.5% | 97.5 | 39.77 |
| README.md:253 | 6 rebalances per day: +33.8% | 33.8 | 3.19 |
| README.md:254 | 12 rebalances per day: +0.1% | 0.1 | -32.54 |
| README.md:281 | measured return autocorrelation +0.249 at lag one, median of six seeds at the published method | 0.249 | 0.03343 |
| README.md:291 | volatility clustering +0.242 (+0.15 to +0.35) | 0.242 | 0.1146 |
| README.md:291 | volume against |return| +0.585 (+0.30 to +0.60) | 0.585 | 0.4925 |
| README.md:292 | excess kurtosis +3.1 (+3 to +10) | 3.1 | 7.569 |
| README.md:297 | held out, fresh sim seeds read correlation +0.225 against a +0.25 band floor | 0.225 | 0.2427 |
| README.md:306 | annualised volatility runs 41.5% against a real 15-35% | 41.5 | 30.87 |
| README.md:309 | volume changes autocorrelate at -0.45 where real ones sit near zero | -0.45 | -0.2979 |
| README.md:113 | paired across the same twelve markets momentum wins only 7-5 | 7-5 | 1-11 |
| README.md:113 | p = 0.77 - a coin flip | 0.77 | 0.006348 |
| docs/core-concepts.md:56 | vix takes a new value every day: 120 distinct values in 120 days | 120 | 117 |
| docs/scenarios.md:24 | buy_and_hold calm return | -7.16 | -5.629 |
| docs/scenarios.md:24 | buy_and_hold hiked return | -10.75 | -8.763 |
| docs/scenarios.md:24 | buy_and_hold delta | -3.59 | -3.133 |
| docs/scenarios.md:25 | momentum calm return | -1.17 | -7.88 |
| docs/scenarios.md:25 | momentum hiked return | -3.52 | -9.741 |
| docs/scenarios.md:25 | momentum delta | -2.36 | -1.862 |
| docs/scenarios.md:26 | oracle calm return | 11.11 | 14.98 |
| docs/scenarios.md:26 | oracle hiked return | 8.7 | 12.99 |
| docs/scenarios.md:26 | oracle delta | -2.41 | -1.989 |
| docs/scenarios.md:34 | across sim seeds 5 to 9 buy-and-hold gives up 3.4 to 4.7 points (delta -4.7...) | -4.7 | -4.96 |
| docs/scenarios.md:34 | ...to -3.4 | -3.4 | -3.038 |
| docs/scenarios.md:35 | momentum's give-up spans -5.1... | -5.1 | -1.862 |
| docs/scenarios.md:41 | a 60-day run crossing the day-45 central-bank meeting reprices a median -4.29% | -4.29 | -4.108 |
| docs/scenarios.md:68 | annualised realised vol at VIX 5 | 49.48 | 31.39 |
| docs/scenarios.md:69 | annualised realised vol at VIX 15 | 58.76 | 37.01 |
| docs/scenarios.md:70 | annualised realised vol at VIX 45 | 107.1 | 104.9 |
| docs/scenarios.md:71 | annualised realised vol at VIX 65 | 124.3 | 125.8 |
| docs/scenarios.md:73 | a thirteenfold move in VIX moves realised volatility by a factor of 2.5 | 2.5 | 4.006 |
| docs/scenarios.md:77 | day one's closes are bit-identical across VIX 5, 10 and 15 | True | False |
| docs/scenarios.md:98 | mean quoted spread at VIX 15 | 11.52 | 11.68 |
| docs/scenarios.md:98 | mean quoted spread at VIX 25 | 13.92 | 13.42 |
| docs/scenarios.md:98 | mean quoted spread at VIX 45 | 18.87 | 17.16 |
| docs/scenarios.md:98 | mean quoted spread at VIX 65 | 25.89 | 21.52 |
| docs/scenarios.md:104 | mean pairwise correlation at VIX 15, 300 pairs | 0.269 | 0.1964 |
| docs/scenarios.md:104 | mean pairwise correlation at VIX 45 | 0.678 | 0.6218 |
| docs/scenarios.md:104 | mean pairwise correlation at VIX 65 | 0.759 | 0.6799 |
| docs/rng-streams.md:37 | the era's later model changes have since taken the known-answer version to 8 | 8 | 10 |
| docs/rng-streams.md:136 | state_snapshot()['rng'] is nine numbers, three per stream | 9 | 18 |
| docs/agents-and-evaluation.md:48 | momentum beats the Oracle 4 of 12 on the twelve-market grid | 4 | 0 |
| docs/agents-and-evaluation.md:49 | mean_reversion beats the Oracle 1 of 12 | 1 | 8 |
| docs/agents-and-evaluation.md:60 | mean-reversion's one win is barely above 1.0: capture 1.004 | 1.004 | 1.658 |
| docs/agents-and-evaluation.md:61 | buy-and-hold's one win is capture 1.59... | 1.59 | 1.039 |
| docs/agents-and-evaluation.md:61 | ...the largest capture on the whole grid | True | False |
| docs/agents-and-evaluation.md:70 | Oracle median P&L on the ranking grid at ten days, sim seeds 0-7, default top_k=5: $93k | 93,000 | 62,311 |
| docs/agents-and-evaluation.md:70 | the same information across three times as many names, top_k=15: $65k | 65,000 | 49,480 |
| docs/agents-and-evaluation.md:76 | the Oracle makes $85k in five days on seed 2026 over random(40,7) | 85,000 | 74,107 |
| docs/agents-and-evaluation.md:76 | and $504k in sixty | 504,000 | 578,313 |
| docs/agents-and-evaluation.md:77 | momentum's capture ratio against the five-day denominator: 0.40 | 0.4 | 0.1908 |
| docs/agents-and-evaluation.md:77 | and 1.24 against the sixty-day one - the horizon alone carries the verdict across 1.0 | 1.24 | -0.1804 |
| docs/agents-and-evaluation.md:88 | over twelve markets momentum ranks +0.593 pooled | 0.593 | -0.01797 |
| docs/agents-and-evaluation.md:88 | mean-reversion +0.160 | 0.16 | 1.078 |
| docs/agents-and-evaluation.md:89 | a single seed picks the pooled leader only five times in twelve | 5 | 1 |
| docs/agents-and-evaluation.md:89 | and equally often crowns mean-reversion: 5 of 12 | 5 | 11 |
| docs/agents-and-evaluation.md:92 | momentum capture range, low: +0.089 | 0.089 | -0.4401 |
| docs/agents-and-evaluation.md:92 | momentum capture range, high: +1.523 | 1.523 | 0.7509 |
| docs/agents-and-evaluation.md:101 | momentum vs mean_reversion: 7-5 | 7-5 | 1-11 |
| docs/agents-and-evaluation.md:101 | p = 0.77 | 0.77 | 0.006348 |
| docs/agents-and-evaluation.md:102 | momentum vs random: 12-0, a clean sweep | 12-0 | 5-7 |
| docs/agents-and-evaluation.md:102 | p = 0.0005, the floor twelve paired seeds can produce | 0.0005 | 0.7744 |
| docs/agents-and-evaluation.md:115 | the identical test over seeds 12-23: momentum over mean-reversion 9-3 | 9-3 | 0-12 |
| docs/agents-and-evaluation.md:115 | p = 0.15 | 0.15 | 0.000488 |
| docs/agents-and-evaluation.md:126 | three days, seeds 0-9: the reference's per-seed P&L spans $11.2k... | 11,200 | 12,771 |
| docs/agents-and-evaluation.md:126 | ...to $45.6k | 45,600 | 32,705 |
| docs/agents-and-evaluation.md:127 | mean-reversion's ratio on the thinnest of those markets: +1.29 | 1.29 | 0.8875 |
| docs/agents-and-evaluation.md:127 | against a pooled +0.44 across the ten | 0.44 | 0.8682 |
| docs/agents-and-evaluation.md:128 | every ratio above 1.0 it posts sits on one of the four thinnest denominators | True | False |
| docs/agents-and-evaluation.md:132 | averaging the ten ratios instead of pooling them would move the verdict to +0.61 | 0.61 | 0.9688 |
| docs/an-llm-agent.md:30 | oracle pnl 229751 | 229,751 | 168,593 |
| docs/an-llm-agent.md:32 | momentum pnl 21520 | 21,520 | -22,269 |
| docs/an-llm-agent.md:49 | oracle twenty-day impact spans -235... | -235 | -383.9 |
| docs/an-llm-agent.md:49 | ...to +470 bps across seeds 2020-2031 | 470 | 367.7 |
| docs/an-llm-agent.md:49 | positive in only 7 of 12 seeds | 7 | 5 |
| docs/an-llm-agent.md:50 | momentum's twenty-day impact flips sign the same way | True | False |
| docs/an-llm-agent.md:53 | by day three the oracle's sign already belongs to the seed: positive in 8 of 12 | 8 | 12 |
| docs/an-llm-agent.md:53 | with a span of -29... | -29 | 5.576 |
| docs/an-llm-agent.md:53 | ...to +90 bps | 90 | 115.3 |
| docs/an-llm-agent.md:73 | driver is one of the engine's seven factors | 7 | 9 |
| docs/how-realistic-is-this-market.md:43 | sample report: annualised vol | 43.87 | 32.41 |
| docs/how-realistic-is-this-market.md:44 | sample report: excess kurtosis | 3.172 | 6.806 |
| docs/how-realistic-is-this-market.md:47 | sample report: return acf(1) | 0.274 | 0.07235 |
| docs/how-realistic-is-this-market.md:48 | sample report: |return| acf(1) | 0.244 | 0.1727 |
| docs/how-realistic-is-this-market.md:49 | sample report: cross-sectional corr | 0.265 | 0.3438 |
| docs/how-realistic-is-this-market.md:50 | sample report: volume vs |return| | 0.59 | 0.4931 |
| docs/how-realistic-is-this-market.md:51 | sample report: leverage | -0.137 | -0.06936 |
| docs/how-realistic-is-this-market.md:52 | sample report: volume change acf(1) | -0.425 | -0.2899 |
| docs/how-realistic-is-this-market.md:80 | excess kurtosis, median of six seeds | 3.1 | 7.569 |
| docs/how-realistic-is-this-market.md:81 | annualised vol, median of six seeds | 41.5 | 30.87 |
| docs/how-realistic-is-this-market.md:87 | return acf(1), median of six seeds | 0.249 | 0.03343 |
| docs/how-realistic-is-this-market.md:88 | |return| acf(1), median of six seeds | 0.242 | 0.1146 |
| docs/how-realistic-is-this-market.md:90 | volume vs |return|, median of six seeds | 0.585 | 0.4925 |
| docs/how-realistic-is-this-market.md:92 | volume change acf(1), median of six seeds | -0.446 | -0.2979 |
| docs/how-realistic-is-this-market.md:100 | kurtosis reads +2.4 on one seed of six | 2.4 | 5.149 |
| docs/how-realistic-is-this-market.md:127 | held out, fresh sim seeds 101-106: cross-sectional correlation +0.225 | 0.225 | 0.2427 |
| docs/how-realistic-is-this-market.md:128 | kurtosis +3.67 | 3.67 | 9.272 |
| docs/how-realistic-is-this-market.md:128 | clustering +0.202 | 0.202 | 0.07079 |
| docs/how-realistic-is-this-market.md:129 | volume vs |return| +0.546 | 0.546 | 0.4849 |
| docs/how-realistic-is-this-market.md:129 | leverage reads -0.071 | -0.07 | -0.04803 |
| docs/how-realistic-is-this-market.md:135 | five fresh 60-name universes: correlation medians run +0.29... | 0.29 | 0.3075 |
| docs/how-realistic-is-this-market.md:135 | and kurtosis +3.4... | 3.4 | 6.094 |
| docs/how-realistic-is-this-market.md:135 | ...to +4.7 | 4.7 | 6.596 |
| docs/how-realistic-is-this-market.md:136 | clustering reads +0.20... | 0.2 | 0.07479 |
| docs/how-realistic-is-this-market.md:136 | ...to +0.21 | 0.21 | 0.1098 |
| docs/how-realistic-is-this-market.md:137 | the leverage effect weakens to -0.05... | -0.05 | -0.03307 |
| docs/how-realistic-is-this-market.md:137 | ...to -0.04, half its published-method value | -0.04 | -0.01322 |
| docs/how-realistic-is-this-market.md:141 | the published universe over 504 days: correlation +0.34 | 0.34 | 0.3162 |
| docs/how-realistic-is-this-market.md:141 | kurtosis +3.2 | 3.2 | 8.253 |
| docs/how-realistic-is-this-market.md:141 | clustering +0.25 | 0.25 | 0.1296 |
| docs/how-realistic-is-this-market.md:141 | leverage -0.062 | -0.062 | -0.04714 |
| docs/how-realistic-is-this-market.md:142 | volatility 47.6% | 47.6 | 31.34 |
| docs/how-realistic-is-this-market.md:169 | pinned VIX 45 takes mean pairwise correlation to +0.68 | 0.68 | 0.6218 |
| docs/how-realistic-is-this-market.md:169 | and VIX 65 to +0.76 | 0.76 | 0.6799 |
| docs/how-realistic-is-this-market.md:170 | against +0.27 calm | 0.27 | 0.1964 |
| docs/how-realistic-is-this-market.md:199 | clustering gone by lag twenty: -0.006 | -0.006 | -0.02198 |
| docs/how-realistic-is-this-market.md:199 | clustering reads +0.090 at lag five | 0.09 | 0.05306 |
| docs/reproducing-a-run.md:34 | pt.version() == '0.1.0' | 0.1.0 | 0.2.0 |
| docs/reproducing-a-run.md:35 | model_preset()['name'] == 'pt-v1' | pt-v1 | pt-v10 |
| docs/reproducing-a-run.md:207 | the current digest, 1ee64998...fe3581c at v8 | 1ee64998...fe3581c | 4e22d5a6...e860378 |
| docs/reproducing-a-run.md:207 | 'the current digest ... at v8' - the era named as current | 8 | 10 |
| docs/sharing-a-run.md:67 | pt.version() stayed '0.1.0' across the era boundary | 0.1.0 | 0.2.0 |
| docs/sharing-a-run.md:68 | and the preset stayed 'pt-v1' | pt-v1 | pt-v10 |
| docs/sharing-a-run.md:92 | the current digest, 1ee64998...fe3581c at v8, one platform so far | 1ee64998...fe3581c | 4e22d5a6...e860378 |
| docs/transaction-cost-analysis.md:33 | the round trip ends anywhere between -13.3... | -13.3 | -15.75 |
| docs/transaction-cost-analysis.md:34 | ...and +5.8 bps | 5.8 | -4.779 |
| docs/transaction-cost-analysis.md:34 | negative on six of the eight | 6 | 8 |
| docs/transaction-cost-analysis.md:65 | the agent traded 46 names | 46 | 57 |
| docs/transaction-cost-analysis.md:65 | leaving fourteen untouched | 14 | 3 |
| docs/transaction-cost-analysis.md:66 | by -6.5... | -6.5 | -12.6 |
| docs/transaction-cost-analysis.md:66 | ...and +3.2 bps | 3.2 | 6.096 |
| docs/transaction-cost-analysis.md:66 | against a 13.0 bps median direct impact on the traded names | 13 | 9.631 |
| docs/transaction-cost-analysis.md:69 | the same configuration run for two, three or four days leaks nothing | True | False |
| docs/strategy-specs.md:152 | the rebalance table swings the same signal from +97.5%... | 97.5 | 39.77 |
| docs/strategy-specs.md:152 | ...to +0.1% purely by trading it more often | 0.1 | -32.54 |
| docs/scenario-recipes.md:273 | compare() on the same hold against an explicit calm baseline | 29.72 | 34.59 |
| docs/scenario-recipes.md:281 | the same comparison across sim seeds 1-8 spans -26.56... | -26.56 | -33.52 |
| docs/scenario-recipes.md:281 | ...to +29.72 | 29.72 | 34.59 |
| docs/scenario-recipes.md:336 | hiking cycle: median move at 120 days | -9.26 | -9.475 |
| docs/scenario-recipes.md:336 | hiking cycle: most rate-sensitive name | -13.21 | -13.23 |
| docs/scenario-recipes.md:336 | hiking cycle: least rate-sensitive name | 0.26 | 0 |
| docs/scenario-recipes.md:405 | inflation shock: largest absolute move anywhere at 40 days | 0.26 | 1.456 |
| docs/scenario-recipes.md:406 | inflation shock: median move at 120 days | -10.01 | -9.951 |
| docs/scenario-recipes.md:469 | liquidity crisis: annualised realised volatility, calm baseline | 61.76 | 39.98 |
| docs/scenario-recipes.md:469 | liquidity crisis: annualised realised volatility under the spike | 82.16 | 67.7 |
| docs/scenario-recipes.md:520 | liquidity crisis: volatility uplift in percentage points | 20.4 | 27.72 |
| docs/scenario-recipes.md:470 | liquidity crisis: mean pairwise correlation, calm baseline | 0.493 | 0.4938 |
| docs/scenario-recipes.md:470 | liquidity crisis: mean pairwise correlation under the spike | 0.636 | 0.623 |
| docs/scenario-recipes.md:531 | liquidity crisis: median price move at 120 days | -8.29 | -15.96 |
| docs/scenario-recipes.md:532 | liquidity crisis: worst name at 120 days | -13.07 | -21.82 |
| docs/scenario-recipes.md:561 | contraction regime: median move against an explicit expansion baseline | 2.85 | -1.907 |
| docs/scenario-recipes.md:603 | contraction world: corporate yield at day 119 | 6.41 | 5.912 |
| docs/scenario-recipes.md:604 | expansion world: corporate yield at day 119 | 4.91 | 4.952 |
| docs/scenario-recipes.md:604 | expansion world: inflation at day 119 | 3.09 | 3.05 |
| docs/scenario-recipes.md:683 | compound episode: median move at 120 days | 9.79 | -3.999 |
| docs/scenario-recipes.md:683 | compound episode: worst name | -0.67 | -13.68 |
| docs/scenario-recipes.md:683 | compound episode: best name | 12.04 | 3.539 |
| docs/scenario-recipes.md:792 | compound episode: annualised realised volatility over the run | 80.79 | 73.98 |

## Every figure, by document

### README.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 60 | TradingEnv passes gymnasium's env_checker | True | - | - | covered_by_tests |
| 88 | pinning VIX in both worlds makes the subtraction byte-exact again | True | True | - | structural_ok |
| 113 | paired across the same twelve markets momentum wins only 7-5 | 7-5 | 1-11 | - | MOVED |
| 113 | p = 0.77 - a coin flip | 0.77 | 0.006348 | -0.7637 | MOVED |
| 115 | a single seed picks the pooled leader only five times in twelve | 5 | 1 | -4 | MOVED |
| 129 | seven factor columns sum to the move, residual around 1e-16 | 1e-16 | 1.47e-17 | - | reproduced |
| 190 | a nudged garch_alpha fingerprints as custom-0c04c4ba, never pt-v1 | custom-0c04c4ba | custom-a3658094 | - | MOVED |
| 195 | nothing settable changes how many draws are taken or in what order | True | - | - | covered_by_tests |
| 212 | the by-commit table's a5afd1c row: v3, Windows x86_64 and macOS arm64, identical digest - a historical record | 112fd73e...6eff337 | - | - | not_harnessable |
| 213 | the table's ad91026 row: all five targets, identical digest 76983e65...3180eeb | 76983e65...3180eeb | - | - | not_harnessable |
| 214 | the current digest 1ee64998...fe3581c at v8 | 1ee64998...fe3581c | 4e22d5a6...e860378 | - | MOVED |
| 214 | the table's current-era row: known-answer v8 at 6e30497 | 8 | 10 | 2 | MOVED |
| 214 | one platform's confirmation until the gate runs again: this build reproduces the pinned digest | True | True | - | structural_ok |
| 227 | VIX takes a new value every day over the 120-day macro-chain run | 120 | 117 | -3 | MOVED |
| 235 | 59% at 15 | 59 | 37.01 | -21.99 | MOVED |
| 235 | 107% at 45 | 107 | 104.9 | -2.061 | MOVED |
| 235 | 49% annualised at VIX 5 | 49 | 31.39 | -17.61 | MOVED |
| 235 | 124% at 65 | 124 | 125.8 | 1.755 | MOVED |
| 237 | above VIX 25.5 the cross-section blends toward the market factor | 25.5 | 25.5 | 0 | reproduced |
| 238 | +0.68 at VIX 45 | 0.68 | 0.6218 | -0.05818 | MOVED |
| 238 | +0.76 at 65 | 0.76 | 0.6799 | -0.08008 | MOVED |
| 238 | mean pairwise correlation reads +0.27 calm | 0.27 | 0.1964 | -0.07361 | MOVED |
| 252 | 3 rebalances per day: +97.5% | 97.5 | 39.77 | -57.73 | MOVED |
| 253 | 6 rebalances per day: +33.8% | 33.8 | 3.19 | -30.61 | MOVED |
| 254 | 12 rebalances per day: +0.1% | 0.1 | -32.54 | -32.64 | MOVED |
| 263 | 252-day year over 100 instruments: 27 seconds on the documentation's reference desktop | 27 | 14.38 | -12.62 | machine_bound |
| 264 | and under seven seconds on an Apple-silicon laptop | 7 | 14.38 | 7.379 | machine_bound |
| 265 | recording 9.8M rows of ground truth costs less than wall-clock timing resolves (bound) | 4 | 0.5986 | -3.401 | reproduced |
| 267 | nearly half the interleaved pairs come out negative | 0.5 | 0.375 | -0.125 | reproduced |
| 267 | sweeps parallelise 3-4x on eight cores | 3.5 | 3.694 | 0.1944 | machine_bound |
| 281 | measured return autocorrelation +0.249 at lag one, median of six seeds at the published method | 0.249 | 0.03343 | -0.2156 | MOVED |
| 288 | of the eight statistics, two are marginal and six are dependence | 2 | 2 | 0 | reproduced |
| 290 | cross-sectional correlation +0.257 (real +0.25 to +0.35) | 0.257 | 0.3122 | 0.05521 | within_seed_variation |
| 291 | volatility clustering +0.242 (+0.15 to +0.35) | 0.242 | 0.1146 | -0.1274 | MOVED |
| 291 | volume against |return| +0.585 (+0.30 to +0.60) | 0.585 | 0.4925 | -0.09249 | MOVED |
| 292 | excess kurtosis +3.1 (+3 to +10) | 3.1 | 7.569 | 4.469 | MOVED |
| 293 | a leverage effect at -0.085, just short of its -0.30 to -0.10 band | -0.085 | -0.04529 | 0.03971 | within_seed_variation |
| 293 | the leverage effect holds its sign in six seeds of six | True | True | - | structural_ok |
| 297 | held out, fresh sim seeds read correlation +0.225 against a +0.25 band floor | 0.225 | 0.2427 | 0.01767 | MOVED |
| 306 | annualised volatility runs 41.5% against a real 15-35% | 41.5 | 30.87 | -10.63 | MOVED |
| 309 | volume changes autocorrelate at -0.45 where real ones sit near zero | -0.45 | -0.2979 | 0.1521 | MOVED |
| 335 | worked example: seconds end to end - five, on an Apple-silicon laptop | 5 | 11.32 | 6.321 | machine_bound |
| 336 | worked example: five-agent evaluation | 5 | 5 | 0 | reproduced |
| 336 | worked example: 20-seed sweep | 20 | 20 | 0 | reproduced |
| 337 | worked example: 234,000 rows of ground truth | 234,000 | 234,000 | 0 | reproduced |
| 341 | the worked example's internal assertions all pass | True | True | - | structural_ok |

### docs/agents-and-evaluation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 42 | the Oracle spends its budget equal weight, long the five most underpriced and short the five most overpriced (top_k=5 per side) | 5 | 5 | 0 | reproduced |
| 48 | momentum beats the Oracle 4 of 12 on the twelve-market grid | 4 | 0 | -4 | MOVED |
| 49 | mean_reversion beats the Oracle 1 of 12 | 1 | 8 | 7 | MOVED |
| 50 | buy_and_hold beats the Oracle 1 of 12 | 1 | 1 | 0 | reproduced |
| 51 | random beats the Oracle 0 of 12 | 0 | 0 | 0 | reproduced |
| 60 | mean-reversion's one win is barely above 1.0: capture 1.004 | 1.004 | 1.658 | 0.6544 | MOVED |
| 61 | buy-and-hold's one win is capture 1.59... | 1.59 | 1.039 | -0.551 | MOVED |
| 61 | ...the largest capture on the whole grid | True | False | - | structural_fail |
| 70 | Oracle median P&L on the ranking grid at ten days, sim seeds 0-7, default top_k=5: $93k | 93,000 | 62,311 | -30,689 | MOVED |
| 70 | the same information across three times as many names, top_k=15: $65k | 65,000 | 49,480 | -15,520 | MOVED |
| 73 | mispricing reverts on a 60-day half-life | 60 | 60 | 5.59e-11 | reproduced |
| 76 | the Oracle makes $85k in five days on seed 2026 over random(40,7) | 85,000 | 74,107 | -10,893 | MOVED |
| 76 | and $504k in sixty | 504,000 | 578,313 | 74,313 | MOVED |
| 77 | momentum's capture ratio against the five-day denominator: 0.40 | 0.4 | 0.1908 | -0.2092 | MOVED |
| 77 | and 1.24 against the sixty-day one - the horizon alone carries the verdict across 1.0 | 1.24 | -0.1804 | -1.42 | MOVED |
| 88 | over twelve markets momentum ranks +0.593 pooled | 0.593 | -0.01797 | -0.611 | MOVED |
| 88 | mean-reversion +0.160 | 0.16 | 1.078 | 0.918 | MOVED |
| 89 | and equally often crowns mean-reversion: 5 of 12 | 5 | 11 | 6 | MOVED |
| 89 | a single seed picks the pooled leader only five times in twelve | 5 | 1 | -4 | MOVED |
| 92 | momentum capture range, high: +1.523 | 1.523 | 0.7509 | -0.7721 | MOVED |
| 92 | momentum capture range, low: +0.089 | 0.089 | -0.4401 | -0.5291 | MOVED |
| 101 | momentum vs mean_reversion: 7-5 | 7-5 | 1-11 | - | MOVED |
| 101 | p = 0.77 | 0.77 | 0.006348 | -0.7637 | MOVED |
| 102 | momentum vs random: 12-0, a clean sweep | 12-0 | 5-7 | - | MOVED |
| 102 | p = 0.0005, the floor twelve paired seeds can produce | 0.0005 | 0.7744 | 0.7739 | MOVED |
| 113 | even a clean sweep only reaches p = 0.0005 | 0.0005 | 0.000488 | -1.17e-05 | reproduced |
| 115 | the identical test over seeds 12-23: momentum over mean-reversion 9-3 | 9-3 | 0-12 | - | MOVED |
| 115 | p = 0.15 | 0.15 | 0.000488 | -0.1495 | MOVED |
| 126 | ...to $45.6k | 45,600 | 32,705 | -12,895 | MOVED |
| 126 | three days, seeds 0-9: the reference's per-seed P&L spans $11.2k... | 11,200 | 12,771 | 1,571 | MOVED |
| 127 | against a pooled +0.44 across the ten | 0.44 | 0.8682 | 0.4282 | MOVED |
| 127 | mean-reversion's ratio on the thinnest of those markets: +1.29 | 1.29 | 0.8875 | -0.4025 | MOVED |
| 128 | every ratio above 1.0 it posts sits on one of the four thinnest denominators | True | False | - | structural_fail |
| 132 | averaging the ten ratios instead of pooling them would move the verdict to +0.61 | 0.61 | 0.9688 | 0.3588 | MOVED |

### docs/an-llm-agent.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 30 | oracle pnl 229751 | 229,751 | 168,593 | -61,158 | MOVED |
| 30 | oracle why-right 100% | 100 | 100 | 0 | reproduced |
| 32 | momentum why-right prints '-' (no explain()) | True | True | - | structural_ok |
| 32 | momentum pnl 21520 | 21,520 | -22,269 | -43,789 | MOVED |
| 49 | ...to +470 bps across seeds 2020-2031 | 470 | 367.7 | -102.3 | MOVED |
| 49 | oracle twenty-day impact spans -235... | -235 | -383.9 | -148.9 | MOVED |
| 49 | positive in only 7 of 12 seeds | 7 | 5 | -2 | MOVED |
| 50 | momentum's twenty-day impact flips sign the same way | True | False | - | structural_fail |
| 52 | over two days both agents are positive in 12 of 12 | 12 | 12 | 0 | reproduced |
| 53 | by day three the oracle's sign already belongs to the seed: positive in 8 of 12 | 8 | 12 | 4 | MOVED |
| 53 | ...to +90 bps | 90 | 115.3 | 25.31 | MOVED |
| 53 | with a span of -29... | -29 | 5.576 | 34.58 | MOVED |
| 73 | driver is one of the engine's seven factors | 7 | 9 | 2 | MOVED |
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
| 56 | vix takes a new value every day: 120 distinct values in 120 days | 120 | 117 | -3 | MOVED |
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
| 22 | pt.branch cost < 1 ms | 1 | 0.7537 | -0.2463 | reproduced |
| 23 | Checkpoint.resume() cost 2.7 s | 2.7 | 0.331 | -2.369 | machine_bound |
| 26 | Checkpoint replay is three orders of magnitude slower than branch | 3 | 2.643 | -0.3573 | reproduced |

### docs/ground-truth.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | factors sum to change in mispricing_s, residual around 1e-16 | 1e-16 | 1.47e-17 | - | reproduced |

### docs/how-realistic-is-this-market.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 38 | sample report: 10,040 daily returns | 10,040 | 10,040 | 0 | reproduced |
| 43 | sample report: annualised vol | 43.87 | 32.41 | -11.46 | MOVED |
| 44 | sample report: excess kurtosis | 3.172 | 6.806 | 3.634 | MOVED |
| 47 | sample report: return acf(1) | 0.274 | 0.07235 | -0.2016 | MOVED |
| 48 | sample report: |return| acf(1) | 0.244 | 0.1727 | -0.07126 | MOVED |
| 49 | sample report: cross-sectional corr | 0.265 | 0.3438 | 0.0788 | MOVED |
| 50 | sample report: volume vs |return| | 0.59 | 0.4931 | -0.09692 | MOVED |
| 51 | sample report: leverage | -0.137 | -0.06936 | 0.06764 | MOVED |
| 52 | sample report: volume change acf(1) | -0.425 | -0.2899 | 0.1351 | MOVED |
| 72 | universe fingerprint 5d8de78b55aad752 | 5d8de78b55aad752 | 5d8de78b55aad752 | - | reproduced |
| 80 | excess kurtosis, median of six seeds | 3.1 | 7.569 | 4.469 | MOVED |
| 81 | annualised vol, median of six seeds | 41.5 | 30.87 | -10.63 | MOVED |
| 87 | return acf(1), median of six seeds | 0.249 | 0.03343 | -0.2156 | MOVED |
| 88 | |return| acf(1), median of six seeds | 0.242 | 0.1146 | -0.1274 | MOVED |
| 89 | cross-sectional corr, median of six seeds | 0.257 | 0.3122 | 0.05521 | within_seed_variation |
| 90 | volume vs |return|, median of six seeds | 0.585 | 0.4925 | -0.09249 | MOVED |
| 91 | leverage, median of six seeds | -0.085 | -0.04529 | 0.03971 | within_seed_variation |
| 92 | volume change acf(1), median of six seeds | -0.446 | -0.2979 | 0.1481 | MOVED |
| 100 | kurtosis reads +2.4 on one seed of six | 2.4 | 5.149 | 2.749 | MOVED |
| 101 | correlation's range reaches +0.46 | 0.46 | 0.4597 | -0.000348 | reproduced |
| 127 | held out, fresh sim seeds 101-106: cross-sectional correlation +0.225 | 0.225 | 0.2427 | 0.01767 | MOVED |
| 128 | clustering +0.202 | 0.202 | 0.07079 | -0.1312 | MOVED |
| 128 | kurtosis +3.67 | 3.67 | 9.272 | 5.602 | MOVED |
| 129 | leverage reads -0.071 | -0.07 | -0.04803 | 0.02197 | MOVED |
| 129 | volume vs |return| +0.546 | 0.546 | 0.4849 | -0.06108 | MOVED |
| 135 | ...to +0.35 | 0.35 | 0.3496 | -0.000387 | reproduced |
| 135 | five fresh 60-name universes: correlation medians run +0.29... | 0.29 | 0.3075 | 0.01749 | MOVED |
| 135 | ...to +4.7 | 4.7 | 6.596 | 1.896 | MOVED |
| 135 | and kurtosis +3.4... | 3.4 | 6.094 | 2.694 | MOVED |
| 136 | ...to +0.21 | 0.21 | 0.1098 | -0.1002 | MOVED |
| 136 | clustering reads +0.20... | 0.2 | 0.07479 | -0.1252 | MOVED |
| 137 | ...to -0.04, half its published-method value | -0.04 | -0.01322 | 0.02678 | MOVED |
| 137 | the leverage effect weakens to -0.05... | -0.05 | -0.03307 | 0.01693 | MOVED |
| 141 | clustering +0.25 | 0.25 | 0.1296 | -0.1204 | MOVED |
| 141 | the published universe over 504 days: correlation +0.34 | 0.34 | 0.3162 | -0.02375 | MOVED |
| 141 | kurtosis +3.2 | 3.2 | 8.253 | 5.053 | MOVED |
| 141 | leverage -0.062 | -0.062 | -0.04714 | 0.01486 | MOVED |
| 142 | volatility 47.6% | 47.6 | 31.34 | -16.26 | MOVED |
| 163 | the factor's baseline sigma is 0.016 a day (the 0.003 beside it is named history) | 0.016 | 0.016 | 0 | reproduced |
| 165 | per-name idiosyncratic noise is scaled down by 0.84 | 0.84 | 0.84 | 0 | reproduced |
| 169 | pinned VIX 45 takes mean pairwise correlation to +0.68 | 0.68 | 0.6218 | -0.05818 | MOVED |
| 169 | and VIX 65 to +0.76 | 0.76 | 0.6799 | -0.08008 | MOVED |
| 170 | against +0.27 calm | 0.27 | 0.1964 | -0.07361 | MOVED |
| 173 | the correlation blend engages above VIX 25.5 | 25.5 | 25.5 | 0 | reproduced |
| 182 | the pre-era sweep found the correlation band reachable only where kurtosis had collapsed to 1.26 | 1.26 | - | - | not_harnessable |
| 190 | the per-name GJR-GARCH's effective persistence ALPHA + BETA + GAMMA/2 is 0.99 | 0.99 | 0.99 | 1.11e-16 | reproduced |
| 191 | the factor variance process's shocks decay with a half-life of about 13.5 days | 13.5 | 13.51 | 0.01341 | reproduced |
| 199 | clustering gone by lag twenty: -0.006 | -0.006 | -0.02198 | -0.01598 | MOVED |
| 199 | clustering reads +0.090 at lag five | 0.09 | 0.05306 | -0.03694 | MOVED |
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
| 65 | seven factor contributions, residual around 1e-16 | 1e-16 | 1.47e-17 | - | reproduced |

### docs/performance.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 14 | 252 days x 10 instruments: 2.9 s | 2.9 | 1.518 | -1.382 | machine_bound |
| 15 | 252 days x 100 instruments: 27.4 s | 27.4 | 14.38 | -13.02 | machine_bound |
| 16 | 252 x 100 recording 9.8M rows: 28.2 s | 28.2 | 14.5 | -13.7 | machine_bound |
| 16 | a recorded year at 100 instruments is 9.8M rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 17 | 8 seeds x 21 days x 100, serial: 20.0 s | 20 | 10.76 | -9.238 | machine_bound |
| 18 | 8 seeds x 21 days x 100, 8 workers: 6.1 s | 6.1 | 2.913 | -3.187 | machine_bound |
| 20 | recording a full year of tick-grain ground truth costs a few percent at most (bound) | 4 | 0.5986 | -3.401 | reproduced |
| 24 | nearly half the pairs came out negative | 0.5 | 0.375 | -0.125 | reproduced |
| 24 | the median was about +1% | 1 | 0.5986 | -0.4014 | reproduced |
| 25 | the 0.8s between the two 252x100 rows is run-to-run noise | 0.8 | 0.8 | 6.66e-16 | reproduced |
| 28 | sweeps parallelise about 3.3x on eight cores | 3.3 | 3.694 | 0.3944 | machine_bound |
| 32 | cost scales roughly linearly in instruments x days (t100/t10 ~ 9.4 published) | 9.45 | 9.475 | 0.02509 | reproduced |

### docs/reading-results.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | ten levels a side makes the book table 20x the rows | 20 | 20 | 0 | reproduced |
| 35 | recording ground truth costs a few percent at most (bound) | 4 | 0.5986 | -3.401 | reproduced |

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
| 34 | pt.version() == '0.1.0' | 0.1.0 | 0.2.0 | - | MOVED |
| 35 | model_preset()['name'] == 'pt-v1' | pt-v1 | pt-v10 | - | MOVED |
| 60 | universe.fingerprint is 64 hex characters | 64 | 64 | 0 | reproduced |
| 73 | a reversed roster hashes differently | True | True | - | structural_ok |
| 158 | rebuilt universe fingerprint matches the archive | True | True | - | structural_ok |
| 162 | replayed.prices() == engine.prices() | True | True | - | structural_ok |
| 163 | replayed.draws_consumed == engine.draws_consumed | True | True | - | structural_ok |
| 199 | at ad91026 (v5) all five targets produced the identical digest 76983e65...3180eeb | 76983e65...3180eeb | - | - | not_harnessable |
| 202 | at a5afd1c two independent platform builds produced the identical digest (historical record) | 112fd73e8e5bc0d68788627a3d74d814553094b9527f9ff480c55426e6eff337 | - | - | not_harnessable |
| 207 | the current digest, 1ee64998...fe3581c at v8 | 1ee64998...fe3581c | 4e22d5a6...e860378 | - | MOVED |
| 207 | 'the current digest ... at v8' - the era named as current | 8 | 10 | 2 | MOVED |

### docs/rng-streams.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 10 | the engine derives three independent substreams from the root seed | 3 | 3 | 0 | reproduced |
| 36 | 'KAT_VERSION = 5 at the split' - a historical record pinned to ad91026 | 5 | - | - | not_harnessable |
| 37 | the era's later model changes have since taken the known-answer version to 8 | 8 | 10 | 2 | MOVED |
| 48 | the market stream's schedule is a pure function of (market status, active roster, sector count) | True | True | - | structural_ok |
| 58 | draws_by_stream() reports market, economy, external | market,economy,external | market,economy,external | - | reproduced |
| 68 | the substream derivation contract (splitmix64 finalizer, sequence 256+k) | True | - | - | covered_by_tests |
| 117 | raw sequences 0/1 (constructors), 21 (universe), 99 (reference MAIN) | True | - | - | covered_by_tests |
| 136 | state_snapshot()['rng'] is nine numbers, three per stream | 9 | 18 | 9 | MOVED |
| 141 | a pre-split snapshot (three numbers) is refused on restore | True | True | - | structural_ok |

### docs/running-a-simulation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 50 | a tick loop crosses the boundary about 98,000 times per simulated year | 98,000 | 98,280 | 280 | reproduced |
| 51 | five fields per tick makes roughly 500,000 | 500,000 | 491,400 | -8,600 | reproduced |

### docs/scenario-recipes.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 98 | segment rule: the first pin owns every day before its own start | 15 | 15 | 0 | reproduced |
| 100 | segment rule: the step's own segment opens on day 60 | 48 | 48 | 0 | reproduced |
| 101 | segment rule: each pin owns its days until the next one begins | 48 | 48 | 0 | reproduced |
| 103 | segment rule: inside the ramp's own segment, day 97 | 35.3 | 35.29 | -0.01111 | reproduced |
| 104 | segment rule: the last pin owns the rest of the run | 22 | 22 | 0 | reproduced |
| 135 | the same-day refusal, printed in full on the page | two pins on 'vix' both begin on day 60: step('vix', before=15.0, after=48.0) at day 60 and ramp('vix', start=48.0, end=18.0, over=40) from day 60. Pins on one field layer as consecutive segments, so the first one's values from day 60 onward could never be reached -- one of these two calls would do nothing, silently. Say the level and the episode separately: .hold(vix=<the level before day 60>) then .ramp('vix', start=..., end=..., over=..., begin=60) -- a ramp starts AT its start value, so that is a jump on day 60 and then the path. | two pins on 'vix' both begin on day 60: step('vix', before=15.0, after=48.0) at day 60 and ramp('vix', start=48.0, end=18.0, over=40) from day 60. Pins on one field layer as consecutive segments, so the first one's values from day 60 onward could never be reached -- one of these two calls would do nothing, silently. Say the level and the episode separately: .hold(vix=<the level before day 60>) then .ramp('vix', start=..., end=..., over=..., begin=60) -- a ramp starts AT its start value, so that is a jump on day 60 and then the path. | - | reproduced |
| 146 | the same pair declared the other way round is the same refusal | True | True | - | structural_ok |
| 159 | hold-then-ramp: calm at 15.0 on day zero | 15 | 15 | 0 | reproduced |
| 159 | hold-then-ramp: the decay reaches 18.0 on day 100 | 18 | 18 | 0 | reproduced |
| 159 | hold-then-ramp: still calm on day 30 | 15 | 15 | 0 | reproduced |
| 159 | hold-then-ramp: still calm on the day before the ramp begins | 15 | 15 | 0 | reproduced |
| 159 | hold-then-ramp: the jump to 48.0 on day 60 | 48 | 48 | 0 | reproduced |
| 159 | hold-then-ramp: half way down, day 80 | 33 | 33 | 0 | reproduced |
| 170 | a pin declared after one that begins later is refused as out of order | True | True | - | structural_ok |
| 186 | Scenario.table(5) opens at the held level, not the ramp's start | 15 | 15 | 0 | reproduced |
| 195 | rate_shock called on a configured scenario is refused | True | True | - | structural_ok |
| 196 | from_json called on a configured scenario is refused | True | True | - | structural_ok |
| 199 | the refused call leaves the receiver driving what it drove | fear_greed_index,inflation_rate | fear_greed_index,inflation_rate | - | reproduced |
| 199 | vix_shock called on a configured scenario is refused | True | True | - | structural_ok |
| 213 | constructor first, then chain the other fields: three fields driven | fear_greed_index,inflation_rate,vix | fear_greed_index,inflation_rate,vix | - | reproduced |
| 229 | vix_shock is exactly a hold at the calm level then a ramp back to it | True | True | - | structural_ok |
| 232 | rate_shock is two ramps held apart by credit_spread | True | True | - | structural_ok |
| 250 | compare() with the default baseline refuses a hold-only scenario | True | True | - | structural_ok |
| 250 | ...and a step at day zero, whose day-zero value is the after value | True | True | - | structural_ok |
| 253 | ...and a shock whose start day falls at or after the horizon | True | True | - | structural_ok |
| 256 | ...and a scenario driving nothing at all | True | True | - | structural_ok |
| 258 | an explicit baseline realising the same path is refused too | True | True | - | structural_ok |
| 273 | compare() on the same hold against an explicit calm baseline | 29.72 | 34.59 | 4.868 | MOVED |
| 281 | ...to +29.72 | 29.72 | 34.59 | 4.868 | MOVED |
| 281 | the same comparison across sim seeds 1-8 spans -26.56... | -26.56 | -33.52 | -6.955 | MOVED |
| 281 | ...negative on 2 of the 8 | 2 | 2 | 0 | reproduced |
| 336 | hiking cycle: least rate-sensitive name | 0.26 | 0 | -0.26 | MOVED |
| 336 | hiking cycle: market draws identical across both worlds | True | True | - | structural_ok |
| 336 | hiking cycle: median move at 120 days | -9.26 | -9.475 | -0.2145 | MOVED |
| 336 | hiking cycle: most rate-sensitive name | -13.21 | -13.23 | -0.02077 | MOVED |
| 405 | inflation shock: largest absolute move anywhere at 40 days | 0.26 | 1.456 | 1.196 | MOVED |
| 406 | inflation shock: median move at 120 days | -10.01 | -9.951 | 0.05883 | MOVED |
| 469 | liquidity crisis: annualised realised volatility, calm baseline | 61.76 | 39.98 | -21.78 | MOVED |
| 469 | liquidity crisis: annualised realised volatility under the spike | 82.16 | 67.7 | -14.46 | MOVED |
| 470 | liquidity crisis: mean pairwise correlation, calm baseline | 0.493 | 0.4938 | 0.000782 | MOVED |
| 470 | liquidity crisis: mean pairwise correlation under the spike | 0.636 | 0.623 | -0.01296 | MOVED |
| 520 | liquidity crisis: volatility uplift in percentage points | 20.4 | 27.72 | 7.321 | MOVED |
| 531 | liquidity crisis: median price move at 120 days | -8.29 | -15.96 | -7.674 | MOVED |
| 532 | liquidity crisis: worst name at 120 days | -13.07 | -21.82 | -8.746 | MOVED |
| 561 | contraction regime: market draws identical across both worlds | True | True | - | structural_ok |
| 561 | contraction regime: median move against an explicit expansion baseline | 2.85 | -1.907 | -4.757 | MOVED |
| 603 | contraction world: corporate yield at day 119 | 6.41 | 5.912 | -0.4976 | MOVED |
| 603 | contraction world: policy rate at day 119 | 2 | 2 | 0 | reproduced |
| 603 | contraction world: inflation at day 119 | 1 | 1.002 | 0.001794 | reproduced |
| 604 | expansion world: corporate yield at day 119 | 4.91 | 4.952 | 0.04159 | MOVED |
| 604 | expansion world: policy rate at day 119 | 2.75 | 2.75 | 0 | reproduced |
| 604 | expansion world: inflation at day 119 | 3.09 | 3.05 | -0.04038 | MOVED |
| 683 | compound episode: best name | 12.04 | 3.539 | -8.501 | MOVED |
| 683 | compound episode: market draws identical across both worlds | True | True | - | structural_ok |
| 683 | compound episode: median move at 120 days | 9.79 | -3.999 | -13.79 | MOVED |
| 683 | compound episode: worst name | -0.67 | -13.68 | -13.01 | MOVED |
| 698 | compound path: VIX partway down the decay leg at day 17 | 79.16 | 79.16 | 0 | reproduced |
| 699 | compound path: corporate yield partway up the blow-out leg at day 30 | 0.07364 | 0.07364 | 0 | reproduced |
| 701 | compound path: holds its final policy rate past the recorded horizon | 0.00125 | 0.00125 | 0 | reproduced |
| 728 | the compound path written as ten chained pins is the same scenario | True | True | - | structural_ok |
| 792 | compound episode: annualised realised volatility over the run | 80.79 | 73.98 | -6.808 | MOVED |

### docs/scenarios.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 24 | buy_and_hold calm return | -7.16 | -5.629 | 1.531 | MOVED |
| 24 | buy_and_hold delta | -3.59 | -3.133 | 0.4565 | MOVED |
| 24 | buy_and_hold hiked return | -10.75 | -8.763 | 1.987 | MOVED |
| 25 | momentum calm return | -1.17 | -7.88 | -6.71 | MOVED |
| 25 | momentum delta | -2.36 | -1.862 | 0.4983 | MOVED |
| 25 | momentum hiked return | -3.52 | -9.741 | -6.221 | MOVED |
| 26 | oracle calm return | 11.11 | 14.98 | 3.869 | MOVED |
| 26 | oracle delta | -2.41 | -1.989 | 0.4213 | MOVED |
| 26 | oracle hiked return | 8.7 | 12.99 | 4.29 | MOVED |
| 34 | ...to -3.4 | -3.4 | -3.038 | 0.3617 | MOVED |
| 34 | across sim seeds 5 to 9 buy-and-hold gives up 3.4 to 4.7 points (delta -4.7...) | -4.7 | -4.96 | -0.26 | MOVED |
| 35 | and never escapes | 0 | 0 | 0 | reproduced |
| 35 | ...to +0.5 | 0.5 | 0.5424 | 0.04236 | reproduced |
| 35 | momentum's give-up spans -5.1... | -5.1 | -1.862 | 3.238 | MOVED |
| 36 | on one seed in five it trades around the shock entirely | 1 | 1 | 0 | reproduced |
| 41 | a 60-day run crossing the day-45 central-bank meeting reprices a median -4.29% | -4.29 | -4.108 | 0.1823 | MOVED |
| 41 | pinning federal_funds_rate alone: exactly 0.00% across twenty instruments over 40 days | 0 | 0 | 0 | reproduced |
| 68 | annualised realised vol at VIX 5 | 49.48 | 31.39 | -18.09 | MOVED |
| 69 | annualised realised vol at VIX 15 | 58.76 | 37.01 | -21.75 | MOVED |
| 70 | annualised realised vol at VIX 45 | 107.1 | 104.9 | -2.131 | MOVED |
| 71 | annualised realised vol at VIX 65 | 124.3 | 125.8 | 1.445 | MOVED |
| 73 | a thirteenfold move in VIX moves realised volatility by a factor of 2.5 | 2.5 | 4.006 | 1.506 | MOVED |
| 77 | day one's closes are bit-identical across VIX 5, 10 and 15 | True | False | - | structural_fail |
| 78 | and day two's differ for every pair | True | True | - | structural_ok |
| 83 | the factor's variance is clamped at 8x its baseline | 8 | 8 | 0 | reproduced |
| 96 | quoted bid-ask widens through 1 + max(0, (vix - 15) / 30) | True | True | - | structural_ok |
| 98 | mean quoted spread at VIX 15 | 11.52 | 11.68 | 0.1608 | MOVED |
| 98 | mean quoted spread at VIX 25 | 13.92 | 13.42 | -0.4959 | MOVED |
| 98 | mean quoted spread at VIX 45 | 18.87 | 17.16 | -1.713 | MOVED |
| 98 | mean quoted spread at VIX 65 | 25.89 | 21.52 | -4.372 | MOVED |
| 99 | cross-sectional correlation blends above VIX 25.5 | 25.5 | 25.5 | 0 | reproduced |
| 104 | mean pairwise correlation at VIX 15, 300 pairs | 0.269 | 0.1964 | -0.07261 | MOVED |
| 104 | mean pairwise correlation at VIX 45 | 0.678 | 0.6218 | -0.05618 | MOVED |
| 104 | mean pairwise correlation at VIX 65 | 0.759 | 0.6799 | -0.07908 | MOVED |
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
| 67 | pt.version() stayed '0.1.0' across the era boundary | 0.1.0 | 0.2.0 | - | MOVED |
| 68 | and the preset stayed 'pt-v1' | pt-v1 | pt-v10 | - | MOVED |
| 68 | 'the recalibrated constant is not even in the preset dictionary' | True | True | - | structural_ok |
| 73 | reproduce() recomputes the era fingerprint before replaying and refuses across an era boundary | True | - | - | covered_by_tests |
| 90 | the five-target gate has run: identical digest 76983e65...3180eeb at ad91026 | 76983e65...3180eeb | - | - | not_harnessable |
| 92 | the current digest, 1ee64998...fe3581c at v8, one platform so far | 1ee64998...fe3581c | 4e22d5a6...e860378 | - | MOVED |

### docs/strategy-specs.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 27 | methods-section universe fingerprint a7861d15... | a7861d15 | a7861d15 | - | reproduced |
| 28 | strategy spec fingerprint e6bbc35c... | e6bbc35c | e6bbc35c | - | reproduced |
| 29 | oracle spec fingerprint f383b990... | f383b990 | f383b990 | - | reproduced |
| 55 | spec-built and hand-built baselines score bit-identically | True | - | - | covered_by_tests |
| 103 | blend weights 1.2/0.8 and 0.6/0.4 build bit-identical agents | True | - | - | covered_by_tests |
| 152 | the rebalance table swings the same signal from +97.5%... | 97.5 | 39.77 | -57.73 | MOVED |
| 152 | ...to +0.1% purely by trading it more often | 0.1 | -32.54 | -32.64 | MOVED |

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
| 33 | the round trip ends anywhere between -13.3... | -13.3 | -15.75 | -2.451 | MOVED |
| 34 | ...and +5.8 bps | 5.8 | -4.779 | -10.58 | MOVED |
| 34 | negative on six of the eight | 6 | 8 | 2 | MOVED |
| 35 | the round trip's median across the eight published seeds: -8.4 bps | -8.4 | -12.54 | -4.137 | within_seed_variation |
| 44 | the request was for 4,856 shares (half ADV) | 4,856 | 4,856 | 0 | reproduced |
| 45 | a request for 4,856 shares (half ADV, sim seed 2026) filled 483 | 483 | 483 | 0 | reproduced |
| 46 | requests of 9,713 and 48,563 shares fill the same 483 | True | True | - | structural_ok |
| 47 | 483 at the open on every seed measured - opening depth is a property of the universe | True | True | - | structural_ok |
| 56 | order flow consumes no RNG draws - the per-domain stream split exists for exactly this | True | - | - | covered_by_tests |
| 65 | two of the fourteen it never touched moved | 2 | 2 | 0 | reproduced |
| 65 | the agent traded 46 names | 46 | 57 | 11 | MOVED |
| 65 | leaving fourteen untouched | 14 | 3 | -11 | MOVED |
| 66 | against a 13.0 bps median direct impact on the traded names | 13 | 9.631 | -3.369 | MOVED |
| 66 | ...and +3.2 bps | 3.2 | 6.096 | 2.896 | MOVED |
| 66 | by -6.5... | -6.5 | -12.6 | -6.095 | MOVED |
| 69 | the same configuration run for two, three or four days leaks nothing | True | False | - | structural_fail |
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
- **rec.rule1_seg_day0** (docs/scenario-recipes.md:98): if this stops reading 15.0 the first-pin clause has changed and the page's three-clause statement of the segment rule is stale
- **rec.rule1_seg_day74** (docs/scenario-recipes.md:101): the plateau; if this reads the ramp's value the ramp has started back-filling and two pins no longer lay end to end
- **rec.rule1_same_day_msg** (docs/scenario-recipes.md:135): the page prints this message verbatim, so the message IS the published figure: reword it and the page needs the same edit
- **rec.rule1_same_day_rev** (docs/scenario-recipes.md:146): structural: the page says whichever call was written first is the dead one, which only holds if the rule is symmetric
- **rec.rule1_out_of_order** (docs/scenario-recipes.md:170): structural: the page quotes both of those phrases
- **rec.rule1_ex_day0** (docs/scenario-recipes.md:159): the page's own step-then-decay example, in the form the same-day refusal advises
- **rec.rule1_ex_day60** (docs/scenario-recipes.md:159): a ramp starts AT its start value, so a ramp behind a hold is a jump and then a decay; this is the whole reason the page uses this form
- **rec.rule2_vix_shock_inst** (docs/scenario-recipes.md:199): structural: the page says the refusal names the fields the receiver was driving, so both halves are checked
- **rec.rule2_from_json_inst** (docs/scenario-recipes.md:196): the page lists all four constructors; vol_shock shares the descriptor with vix_shock and is covered by the test suite
- **rec.rule2_base_intact** (docs/scenario-recipes.md:199): a refusal that half-applied would be a quieter version of the defect it replaced
- **rec.rule2_vix_is_hold_ramp** (docs/scenario-recipes.md:229): structural: the page's claim that the constructors are convenience rather than capability, and the reason recipe 3 no longer needs vix_shock for its shape
- **rec.rule3_hold_only** (docs/scenario-recipes.md:250): replaces the row that published the +0.00% this now refuses
- **rec.rule3_after_horizon** (docs/scenario-recipes.md:253): structural: the page says the message names the day and the run length it needs, so both are checked
- **rec.rule3_band_neg** (docs/scenario-recipes.md:281): the page's argument for measuring volatility scenarios with facts.measure rather than a price delta
- **rec.r2_early** (docs/scenario-recipes.md:405): inside the first central-bank meeting window; the pair with rec.r2_late is the page's evidence that the meeting-cadence trap is not specific to the policy rate
- **rec.r3_median** (docs/scenario-recipes.md:531): published with an explicit warning not to lean on it: the sign of a volatility scenario's price effect is seed-dependent (see rec.edge4_band_*)
- **rec.r5_chained** (docs/scenario-recipes.md:728): structural: the segment rule on a four-field episode, and the claim that replaces recipe 5's old 'chaining cannot express two segments'
