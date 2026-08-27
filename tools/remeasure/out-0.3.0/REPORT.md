# Published-figure re-measurement

Commit `a6cfe55`, 2026-08-27 10:40, pretium 0.3.0. Full run: 207s wall with 6 workers.

| status | figures |
|---|---|
| reproduced | 182 |
| within_seed_variation | 3 |
| MOVED | 37 |
| machine_bound | 8 |
| structural_ok | 37 |
| not_harnessable | 6 |
| covered_by_tests | 10 |

## Doc edits needed

Every row here is a published number the stated (or reconstructed)
method no longer produces. On unchanged main these are documents that
were already stale; after an engine change, this section IS the edit
list.

| where | figure | published | measured |
|---|---|---|---|
| docs/core-concepts.md:56 | vix takes a new value every day: 120 distinct values in 120 days | 120 | 118 |
| docs/rng-streams.md:136 | state_snapshot()['rng'] is nine numbers, three per stream | 9 | 21 |
| docs/agents-and-evaluation.md:70 | Oracle median P&L on the ranking grid at ten days, sim seeds 0-7, default top_k=5: $93k | 93,000 | 59,992 |
| docs/agents-and-evaluation.md:70 | the same information across three times as many names, top_k=15: $65k | 65,000 | 48,113 |
| docs/agents-and-evaluation.md:76 | the Oracle makes $85k in five days on seed 2026 over random(40,7) | 85,000 | 77,374 |
| docs/agents-and-evaluation.md:77 | momentum's capture ratio against the five-day denominator: 0.40 | 0.4 | 0.5126 |
| docs/agents-and-evaluation.md:77 | and 1.24 against the sixty-day one - the horizon alone carries the verdict across 1.0 | 1.24 | -0.01525 |
| docs/agents-and-evaluation.md:88 | over twelve markets momentum ranks +0.593 pooled | 0.593 | 0.259 |
| docs/agents-and-evaluation.md:89 | a single seed picks the pooled leader only five times in twelve | 5 | 3 |
| docs/agents-and-evaluation.md:89 | and equally often crowns mean-reversion: 5 of 12 | 5 | 8 |
| docs/agents-and-evaluation.md:92 | momentum capture range, low: +0.089 | 0.089 | -0.5026 |
| docs/agents-and-evaluation.md:92 | momentum capture range, high: +1.523 | 1.523 | 0.9087 |
| docs/agents-and-evaluation.md:126 | three days, seeds 0-9: the reference's per-seed P&L spans $11.2k... | 11,200 | 13,660 |
| docs/agents-and-evaluation.md:127 | mean-reversion's ratio on the thinnest of those markets: +1.29 | 1.29 | 1.565 |
| docs/an-llm-agent.md:49 | positive in only 7 of 12 seeds | 7 | 5 |
| docs/an-llm-agent.md:53 | by day three the oracle's sign already belongs to the seed: positive in 8 of 12 | 8 | 11 |
| docs/an-llm-agent.md:73 | driver is one of the engine's seven factors | 7 | 9 |
| docs/how-realistic-is-this-market.md:44 | sample report: excess kurtosis | 3.172 | 5.853 |
| docs/how-realistic-is-this-market.md:49 | sample report: cross-sectional corr | 0.265 | 0.357 |
| docs/how-realistic-is-this-market.md:50 | sample report: volume vs |return| | 0.59 | 0.5596 |
| docs/how-realistic-is-this-market.md:52 | sample report: volume change acf(1) | -0.425 | -0.2656 |
| docs/how-realistic-is-this-market.md:80 | excess kurtosis, median of six seeds | 3.1 | 6.344 |
| docs/how-realistic-is-this-market.md:92 | volume change acf(1), median of six seeds | -0.446 | -0.2613 |
| docs/how-realistic-is-this-market.md:100 | kurtosis reads +2.4 on one seed of six | 2.4 | 4.85 |
| docs/how-realistic-is-this-market.md:101 | correlation's range reaches +0.46 | 0.46 | 0.4859 |
| docs/how-realistic-is-this-market.md:127 | held out, fresh sim seeds 101-106: cross-sectional correlation +0.225 | 0.225 | 0.2439 |
| docs/how-realistic-is-this-market.md:129 | volume vs |return| +0.546 | 0.546 | 0.5504 |
| docs/how-realistic-is-this-market.md:129 | leverage reads -0.071 | -0.07 | -0.04706 |
| docs/how-realistic-is-this-market.md:135 | five fresh 60-name universes: correlation medians run +0.29... | 0.29 | 0.3012 |
| docs/how-realistic-is-this-market.md:136 | clustering reads +0.20... | 0.2 | 0.06085 |
| docs/how-realistic-is-this-market.md:137 | the leverage effect weakens to -0.05... | -0.05 | -0.03909 |
| docs/how-realistic-is-this-market.md:141 | the published universe over 504 days: correlation +0.34 | 0.34 | 0.3241 |
| docs/how-realistic-is-this-market.md:169 | pinned VIX 45 takes mean pairwise correlation to +0.68 | 0.68 | 0.6256 |
| docs/how-realistic-is-this-market.md:199 | clustering gone by lag twenty: -0.006 | -0.006 | -0.01367 |
| docs/how-realistic-is-this-market.md:199 | clustering reads +0.090 at lag five | 0.09 | 0.04583 |
| docs/reproducing-a-run.md:34 | pt.version() == '0.1.0' | 0.1.0 | 0.3.0 |
| docs/sharing-a-run.md:68 | and the preset stayed 'pt-v1' | pt-v1 | pt-v12 |

## Every figure, by document

### README.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 76 | nine factor columns sum to the move, residual around 1e-16 | 1e-16 | 1.43e-17 | - | reproduced |
| 183 | the examples are executed by the test suite; the worked example asserts its own findings | True | True | - | structural_ok |
| 199 | worked example: the whole study takes about five seconds | 5 | 5.086 | 0.08617 | machine_bound |

### docs/agents-and-evaluation.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 42 | the Oracle spends its budget equal weight, long the five most underpriced and short the five most overpriced (top_k=5 per side) | 5 | 5 | 0 | reproduced |
| 50 | buy_and_hold beats the Oracle 1 of 12 | 1 | 1 | 0 | reproduced |
| 51 | random beats the Oracle 0 of 12 | 0 | 0 | 0 | reproduced |
| 62 | mean_reversion beats the Oracle 1 of 12 | 5 | 5 | 0 | reproduced |
| 64 | momentum beats the Oracle 4 of 12 on the twelve-market grid | 0 | 0 | 0 | reproduced |
| 70 | Oracle median P&L on the ranking grid at ten days, sim seeds 0-7, default top_k=5: $93k | 93,000 | 59,992 | -33,008 | MOVED |
| 70 | the same information across three times as many names, top_k=15: $65k | 65,000 | 48,113 | -16,887 | MOVED |
| 73 | mispricing reverts on a 60-day half-life | 60 | 60 | 5.59e-11 | reproduced |
| 76 | the Oracle makes $85k in five days on seed 2026 over random(40,7) | 85,000 | 77,374 | -7,626 | MOVED |
| 77 | momentum's capture ratio against the five-day denominator: 0.40 | 0.4 | 0.5126 | 0.1126 | MOVED |
| 77 | and 1.24 against the sixty-day one - the horizon alone carries the verdict across 1.0 | 1.24 | -0.01525 | -1.255 | MOVED |
| 88 | over twelve markets momentum ranks +0.593 pooled | 0.593 | 0.259 | -0.334 | MOVED |
| 89 | and equally often crowns mean-reversion: 5 of 12 | 5 | 8 | 3 | MOVED |
| 89 | a single seed picks the pooled leader only five times in twelve | 5 | 3 | -2 | MOVED |
| 92 | momentum capture range, high: +1.523 | 1.523 | 0.9087 | -0.6143 | MOVED |
| 92 | momentum capture range, low: +0.089 | 0.089 | -0.5026 | -0.5916 | MOVED |
| 102 | momentum vs random: 12-0, a clean sweep | 12-0 | 12-0 | - | reproduced |
| 102 | p = 0.0005, the floor twelve paired seeds can produce | 0.0005 | 0.000488 | -1.17e-05 | reproduced |
| 109 | buy-and-hold's one win is capture 1.59... | 1.02 | 1.016 | -0.004379 | reproduced |
| 113 | even a clean sweep only reaches p = 0.0005 | 0.0005 | 0.000488 | -1.17e-05 | reproduced |
| 126 | three days, seeds 0-9: the reference's per-seed P&L spans $11.2k... | 11,200 | 13,660 | 2,460 | MOVED |
| 127 | mean-reversion's ratio on the thinnest of those markets: +1.29 | 1.29 | 1.565 | 0.2753 | MOVED |
| 128 | three of the four ratios above 1.0 sit on the four thinnest denominators | 3 | 3 | 0 | reproduced |
| 133 | momentum vs mean_reversion: 7-5 | 9-3 | 9-3 | - | reproduced |
| 133 | p = 0.77 | 0.15 | 0.146 | -0.004004 | reproduced |
| 143 | mean-reversion's one win is barely above 1.0: capture 1.004 | 1.52 | 1.52 | -0.000232 | reproduced |
| 143 | mean-reversion +0.160 | 0.783 | 0.7827 | -0.000292 | reproduced |
| 170 | the identical test over seeds 12-23: momentum over mean-reversion 9-3 | 10-2 | 10-2 | - | reproduced |
| 170 | p = 0.15 | 0.04 | 0.03857 | -0.001426 | reproduced |
| 182 | against a pooled +0.44 across the ten | 0.78 | 0.7801 | 5.85e-05 | reproduced |
| 187 | averaging the ten ratios instead of pooling them would move the verdict to +0.61 | 0.88 | 0.8791 | -0.000865 | reproduced |

### docs/an-llm-agent.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 30 | oracle pnl 229751 | 155,707 | 155,707 | -0.4974 | reproduced |
| 30 | oracle why-right 100% | 100 | 100 | 0 | reproduced |
| 32 | momentum why-right prints '-' (no explain()) | True | True | - | structural_ok |
| 32 | momentum pnl 21520 | -2,207 | -2,207 | -0.0192 | reproduced |
| 49 | positive in only 7 of 12 seeds | 7 | 5 | -2 | MOVED |
| 50 | momentum's twenty-day impact flips sign the same way | True | True | - | structural_ok |
| 52 | over two days both agents are positive in 12 of 12 | 12 | 12 | 0 | reproduced |
| 53 | by day three the oracle's sign already belongs to the seed: positive in 8 of 12 | 8 | 11 | 3 | MOVED |
| 63 | ...to +470 bps across seeds 2020-2031 | 418.4 | 418.4 | -0.03699 | reproduced |
| 63 | oracle twenty-day impact spans -235... | -451.1 | -451.1 | -0.03173 | reproduced |
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
| 56 | vix takes a new value every day: 120 distinct values in 120 days | 120 | 118 | -2 | MOVED |
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
| 22 | pt.branch cost < 1 ms | 1 | 0.4685 | -0.5315 | reproduced |
| 23 | Checkpoint.resume() cost 2.7 s | 2.7 | 0.1775 | -2.523 | machine_bound |
| 26 | Checkpoint replay is three orders of magnitude slower than branch | 3 | 2.578 | -0.4215 | reproduced |

### docs/ground-truth.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | factors sum to change in mispricing_s, residual around 1e-16 | 1e-16 | 1.43e-17 | - | reproduced |

### docs/how-realistic-is-this-market.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 38 | sample report: 10,040 daily returns | 10,040 | 10,040 | 0 | reproduced |
| 44 | sample report: excess kurtosis | 3.172 | 5.853 | 2.681 | MOVED |
| 45 | raising the variance ceiling bought +0.016 of clustering for 20 points of volatility | 0.016 | - | - | not_harnessable |
| 49 | sample report: cross-sectional corr | 0.265 | 0.357 | 0.09199 | MOVED |
| 50 | sample report: volume vs |return| | 0.59 | 0.5596 | -0.03036 | MOVED |
| 52 | sample report: volume change acf(1) | -0.425 | -0.2656 | 0.1594 | MOVED |
| 72 | universe fingerprint 5d8de78b55aad752 | 5d8de78b55aad752 | 5d8de78b55aad752 | - | reproduced |
| 80 | excess kurtosis, median of six seeds | 3.1 | 6.344 | 3.244 | MOVED |
| 89 | cross-sectional corr, median of six seeds | 0.257 | 0.3104 | 0.05338 | within_seed_variation |
| 90 | volume vs |return|, median of six seeds | 0.585 | 0.5727 | -0.01229 | within_seed_variation |
| 91 | leverage, median of six seeds | -0.085 | -0.0549 | 0.0301 | within_seed_variation |
| 92 | volume change acf(1), median of six seeds | -0.446 | -0.2613 | 0.1847 | MOVED |
| 100 | kurtosis reads +2.4 on one seed of six | 2.4 | 4.85 | 2.45 | MOVED |
| 101 | correlation's range reaches +0.46 | 0.46 | 0.4859 | 0.02592 | MOVED |
| 127 | held out, fresh sim seeds 101-106: cross-sectional correlation +0.225 | 0.225 | 0.2439 | 0.01893 | MOVED |
| 129 | leverage reads -0.071 | -0.07 | -0.04706 | 0.02294 | MOVED |
| 129 | volume vs |return| +0.546 | 0.546 | 0.5504 | 0.004375 | MOVED |
| 135 | five fresh 60-name universes: correlation medians run +0.29... | 0.29 | 0.3012 | 0.01123 | MOVED |
| 136 | clustering reads +0.20... | 0.2 | 0.06085 | -0.1391 | MOVED |
| 137 | the leverage effect weakens to -0.05... | -0.05 | -0.03909 | 0.01091 | MOVED |
| 141 | the published universe over 504 days: correlation +0.34 | 0.34 | 0.3241 | -0.01589 | MOVED |
| 163 | the factor's baseline sigma is 0.016 a day (the 0.003 beside it is named history) | 0.016 | 0.016 | 0 | reproduced |
| 165 | per-name idiosyncratic noise is scaled down by 0.84 | 0.84 | 0.84 | 0 | reproduced |
| 169 | pinned VIX 45 takes mean pairwise correlation to +0.68 | 0.68 | 0.6256 | -0.05437 | MOVED |
| 173 | the correlation blend engages above VIX 25.5 | 25.5 | 25.5 | 0 | reproduced |
| 182 | the pre-era sweep found the correlation band reachable only where kurtosis had collapsed to 1.26 | 1.26 | - | - | not_harnessable |
| 190 | the per-name GJR-GARCH's effective persistence ALPHA + BETA + GAMMA/2 is 0.99 | 0.99 | 0.99 | 1.11e-16 | reproduced |
| 191 | the factor variance process's shocks decay with a half-life of about 13.5 days | 13.5 | 13.51 | 0.01341 | reproduced |
| 199 | clustering gone by lag twenty: -0.006 | -0.006 | -0.01367 | -0.007673 | MOVED |
| 199 | clustering reads +0.090 at lag five | 0.09 | 0.04583 | -0.04417 | MOVED |
| 215 | the leverage sign is stable: negative in six seeds of six | True | True | - | structural_ok |
| 219 | against ALPHA = 0.02 for a positive one | 0.02 | 0.02 | 0 | reproduced |
| 219 | a negative day's squared return feeds through at ALPHA + GAMMA = 0.36 | 0.36 | 0.36 | 5.55e-17 | reproduced |
| 223 | GAMMA = 0.34, already large against literature GJR fits | 0.34 | 0.34 | 0 | reproduced |

### docs/model-presets.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 92 | a nudged garch_alpha fingerprints as custom-7f290e34, never pt-v1 | custom-7f290e34 | custom-7f290e34 | - | reproduced |
| 162 | nothing settable changes how many draws are taken or in what order | True | - | - | covered_by_tests |

### docs/performance.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 14 | 252 days x 10 instruments: 2.9 s | 2.9 | 0.7651 | -2.135 | machine_bound |
| 15 | 252 days x 100 instruments: 27.4 s | 27.4 | 6.749 | -20.65 | machine_bound |
| 16 | 252 x 100 recording 9.8M rows: 28.2 s | 28.2 | 6.756 | -21.44 | machine_bound |
| 16 | a recorded year at 100 instruments is 9.8M rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 17 | 8 seeds x 21 days x 100, serial: 20.0 s | 20 | 4.571 | -15.43 | machine_bound |
| 18 | 8 seeds x 21 days x 100, 8 workers: 6.1 s | 6.1 | 1.103 | -4.997 | machine_bound |
| 20 | recording a full year of tick-grain ground truth costs a few percent at most (bound) | 4 | -0.09215 | -4.092 | reproduced |
| 24 | nearly half the pairs came out negative | 0.5 | 0.625 | 0.125 | reproduced |
| 24 | the median was about +1% | 1 | -0.09215 | -1.092 | reproduced |
| 25 | the 0.8s between the two 252x100 rows is run-to-run noise | 0.8 | 0.8 | 6.66e-16 | reproduced |
| 28 | sweeps parallelise about 3.3x on eight cores | 3.3 | 4.145 | 0.8448 | machine_bound |
| 32 | cost scales roughly linearly in instruments x days (t100/t10 ~ 9.4 published) | 9.45 | 8.822 | -0.6281 | reproduced |

### docs/reading-results.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 19 | ten levels a side makes the book table 20x the rows | 20 | 20 | 0 | reproduced |
| 35 | recording ground truth costs a few percent at most (bound) | 4 | -0.09215 | -4.092 | reproduced |

### docs/real-fundamentals-from-sec-edgar.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 26 | ten market-wide requests plus one per company kept | 10 | - | - | covered_by_tests |

### docs/reinforcement-learning.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 18 | TradingEnv passes gymnasium's env_checker | True | - | - | covered_by_tests |

### docs/reproducing-a-run.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 34 | pt.version() == '0.1.0' | 0.1.0 | 0.3.0 | - | MOVED |
| 37 | model_preset()['name'] == 'pt-v1' | pt-v12 | pt-v12 | - | reproduced |
| 60 | universe.fingerprint is 64 hex characters | 64 | 64 | 0 | reproduced |
| 73 | a reversed roster hashes differently | True | True | - | structural_ok |
| 158 | rebuilt universe fingerprint matches the archive | True | True | - | structural_ok |
| 162 | replayed.prices() == engine.prices() | True | True | - | structural_ok |
| 163 | replayed.draws_consumed == engine.draws_consumed | True | True | - | structural_ok |
| 212 | 'the identical known-answer v11 digest' - the era named as current | 11 | 11 | 0 | reproduced |
| 215 | the digest the five targets reported at f722ce3, printed in full | 60d475726c8b270df0894da7577523e98d044dd09afc6b536377eaf4b40de590 | 60d475726c8b270df0894da7577523e98d044dd09afc6b536377eaf4b40de590 | - | reproduced |

### docs/rng-streams.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 10 | the engine derives three independent substreams from the root seed | 3 | 3 | 0 | reproduced |
| 36 | 'KAT_VERSION was 5 at the split' - a historical record of the boundary, not a property of this build | 5 | - | - | not_harnessable |
| 37 | the era's later model changes have since taken the known-answer version to 11 | 11 | 11 | 0 | reproduced |
| 48 | the market stream's schedule is a pure function of (market status, active roster, sector count) | True | True | - | structural_ok |
| 58 | draws_by_stream() reports market, economy, external | market,economy,external | market,economy,external | - | reproduced |
| 68 | the substream derivation contract (splitmix64 finalizer, sequence 256+k) | True | - | - | covered_by_tests |
| 117 | raw sequences 0/1 (constructors), 21 (universe), 99 (reference MAIN) | True | - | - | covered_by_tests |
| 136 | state_snapshot()['rng'] is nine numbers, three per stream | 9 | 21 | 12 | MOVED |
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
| 273 | compare() on the same hold against an explicit calm baseline | 29.72 | 29.72 | -0.002645 | reproduced |
| 281 | ...to +29.72 | 29.72 | 29.72 | -0.002645 | reproduced |
| 281 | the same comparison across sim seeds 1-8 spans -26.56... | -26.56 | -26.56 | -0.00353 | reproduced |
| 281 | ...negative on 2 of the 8 | 2 | 2 | 0 | reproduced |
| 336 | hiking cycle: least rate-sensitive name | 0.26 | 0.2634 | 0.003389 | reproduced |
| 336 | hiking cycle: market draws identical across both worlds | True | True | - | structural_ok |
| 336 | hiking cycle: median move at 120 days | -9.26 | -9.262 | -0.00212 | reproduced |
| 336 | hiking cycle: most rate-sensitive name | -13.21 | -13.21 | 0.002453 | reproduced |
| 405 | inflation shock: largest absolute move anywhere at 40 days | 0.26 | 0.265 | 0.004951 | reproduced |
| 406 | inflation shock: median move at 120 days | -10.01 | -10.01 | 0.002104 | reproduced |
| 469 | liquidity crisis: annualised realised volatility, calm baseline | 61.76 | 61.76 | 0.003197 | reproduced |
| 469 | liquidity crisis: annualised realised volatility under the spike | 82.16 | 82.16 | 5.61e-05 | reproduced |
| 470 | liquidity crisis: mean pairwise correlation, calm baseline | 0.493 | 0.4934 | 0.000414 | reproduced |
| 470 | liquidity crisis: mean pairwise correlation under the spike | 0.636 | 0.6364 | 0.000436 | reproduced |
| 520 | liquidity crisis: volatility uplift in percentage points | 20.4 | 20.4 | -0.003141 | reproduced |
| 531 | liquidity crisis: median price move at 120 days | -8.29 | -8.293 | -0.002756 | reproduced |
| 532 | liquidity crisis: worst name at 120 days | -13.07 | -13.07 | 0.004307 | reproduced |
| 561 | contraction regime: market draws identical across both worlds | True | True | - | structural_ok |
| 561 | contraction regime: median move against an explicit expansion baseline | 2.85 | 2.847 | -0.003348 | reproduced |
| 603 | contraction world: corporate yield at day 119 | 6.41 | 6.41 | -5.76e-05 | reproduced |
| 603 | contraction world: policy rate at day 119 | 2 | 2 | 0 | reproduced |
| 603 | contraction world: inflation at day 119 | 1 | 1.003 | 0.002702 | reproduced |
| 604 | expansion world: corporate yield at day 119 | 4.91 | 4.914 | 0.004468 | reproduced |
| 604 | expansion world: policy rate at day 119 | 2.75 | 2.75 | 0 | reproduced |
| 604 | expansion world: inflation at day 119 | 3.09 | 3.086 | -0.004414 | reproduced |
| 683 | compound episode: best name | 12.04 | 12.04 | -0.002476 | reproduced |
| 683 | compound episode: market draws identical across both worlds | True | True | - | structural_ok |
| 683 | compound episode: median move at 120 days | 9.79 | 9.793 | 0.002929 | reproduced |
| 683 | compound episode: worst name | -0.67 | -0.6707 | -0.000736 | reproduced |
| 698 | compound path: VIX partway down the decay leg at day 17 | 79.16 | 79.16 | 0 | reproduced |
| 699 | compound path: corporate yield partway up the blow-out leg at day 30 | 0.07364 | 0.07364 | 0 | reproduced |
| 701 | compound path: holds its final policy rate past the recorded horizon | 0.00125 | 0.00125 | 0 | reproduced |
| 728 | the compound path written as ten chained pins is the same scenario | True | True | - | structural_ok |
| 792 | compound episode: annualised realised volatility over the run | 80.79 | 80.79 | 0.002711 | reproduced |

### docs/scenarios.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 30 | buy_and_hold calm return | -6.08 | -6.083 | -0.002543 | reproduced |
| 30 | buy_and_hold delta | -2.56 | -2.561 | -0.000779 | reproduced |
| 30 | buy_and_hold hiked return | -8.64 | -8.643 | -0.003322 | reproduced |
| 31 | momentum calm return | -5.64 | -5.641 | -0.000937 | reproduced |
| 31 | momentum delta | -1.87 | -1.871 | -0.000678 | reproduced |
| 31 | momentum hiked return | -7.51 | -7.512 | -0.001615 | reproduced |
| 32 | oracle calm return | 13.22 | 13.22 | 0.002289 | reproduced |
| 32 | oracle delta | -2.11 | -2.109 | 0.001366 | reproduced |
| 32 | oracle hiked return | 11.11 | 11.11 | 0.003655 | reproduced |
| 35 | and never escapes | 0 | 0 | 0 | reproduced |
| 36 | on one seed in five it trades around the shock entirely | 1 | 1 | 0 | reproduced |
| 40 | ...to -3.4 | -2.6 | -2.561 | 0.03922 | reproduced |
| 40 | across sim seeds 5 to 9 buy-and-hold gives up 3.4 to 4.7 points (delta -4.7...) | -3.9 | -3.893 | 0.006826 | reproduced |
| 41 | pinning federal_funds_rate alone: exactly 0.00% across twenty instruments over 40 days | 0 | 0 | 0 | reproduced |
| 41 | ...to +0.5 | 1.2 | 1.196 | -0.00416 | reproduced |
| 41 | momentum's give-up spans -5.1... | -1.9 | -1.871 | 0.02932 | reproduced |
| 47 | a 60-day run crossing the day-45 central-bank meeting reprices a median -4.29% | -4 | -4 | -0.000476 | reproduced |
| 77 | VIX 5, 10 and 15 give DIFFERENT day-one closes (the pin acts on day one) | False | False | - | structural_ok |
| 78 | and day two's differ for every pair | True | True | - | structural_ok |
| 83 | the factor's variance is clamped at 8x its baseline | 8 | 8 | 0 | reproduced |
| 96 | quoted bid-ask widens through 1 + max(0, (vix - 15) / 30) | True | True | - | structural_ok |
| 99 | cross-sectional correlation blends above VIX 25.5 | 25.5 | 25.5 | 0 | reproduced |
| 128 | median shortfall at VIX 45 | 11.69 | 11.69 | -0.004181 | reproduced |
| 128 | median shortfall at VIX 15 | 6.06 | 6.064 | 0.004119 | reproduced |
| 128 | VIX 45 regime costs more in 12 of 12 seeds | 12 | 12 | 0 | reproduced |
| 129 | paired median delta | 5.62 | 5.622 | 0.0017 | reproduced |
| 144 | an older build recorded a delta of -4 in 425,600 draws | -4 | - | - | not_harnessable |
| 145 | the market stream's schedule is a pure function of (market status, active roster, sector count) | True | True | - | structural_ok |
| 153 | macro-counterfactual draw divergence zero in every comparison run | 0 | 0 | 0 | reproduced |
| 156 | annualised realised vol at VIX 5 | 30.29 | 30.29 | 0.003537 | reproduced |
| 157 | annualised realised vol at VIX 15 | 36.71 | 36.71 | -0.000182 | reproduced |
| 158 | annualised realised vol at VIX 45 | 112.1 | 112.1 | -0.000287 | reproduced |
| 159 | annualised realised vol at VIX 65 | 135.5 | 135.5 | 0.001539 | reproduced |
| 161 | a thirteenfold move in VIX moves realised volatility by a factor of 2.5 | 4.5 | 4.474 | -0.02639 | reproduced |
| 188 | mean quoted spread at VIX 15 | 11.78 | 11.78 | -0.000718 | reproduced |
| 188 | mean quoted spread at VIX 25 | 14.14 | 14.14 | 0.003029 | reproduced |
| 188 | mean quoted spread at VIX 45 | 17.49 | 17.49 | 0.004051 | reproduced |
| 189 | mean quoted spread at VIX 65 | 22.62 | 22.62 | 0.004268 | reproduced |
| 195 | mean pairwise correlation at VIX 15, 300 pairs | 0.197 | 0.1973 | 0.000314 | reproduced |
| 195 | mean pairwise correlation at VIX 45 | 0.626 | 0.6256 | -0.00037 | reproduced |
| 195 | mean pairwise correlation at VIX 65 | 0.678 | 0.6781 | 0.000131 | reproduced |

### docs/sharing-a-run.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 68 | and the preset stayed 'pt-v1' | pt-v1 | pt-v12 | - | MOVED |
| 68 | 'the recalibrated constant is not even in the preset dictionary' | True | True | - | structural_ok |
| 73 | reproduce() recomputes the era fingerprint before replaying and refuses across an era boundary | True | - | - | covered_by_tests |
| 91 | the five-target gate has run against the shipped baseline: identical v11 digest at f722ce3 | 60d47572...de590 | - | - | not_harnessable |
| 91 | pt.version() stayed '0.1.0' across the era boundary | 0.3.0 | 0.3.0 | - | reproduced |
| 94 | the digest those targets reported is the sha256 committed in tests/known_answer.json | True | True | - | structural_ok |

### docs/strategy-specs.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 27 | methods-section universe fingerprint a7861d15... | a7861d15 | a7861d15 | - | reproduced |
| 28 | strategy spec fingerprint e6bbc35c... | e6bbc35c | e6bbc35c | - | reproduced |
| 29 | oracle spec fingerprint f383b990... | f383b990 | f383b990 | - | reproduced |
| 55 | spec-built and hand-built baselines score bit-identically | True | - | - | covered_by_tests |
| 103 | blend weights 1.2/0.8 and 0.6/0.4 build bit-identical agents | True | - | - | covered_by_tests |
| 156 | the rebalance measurement swings the same one-day signal from +37.55% at three decisions a day... | 37.55 | 37.55 | -0.002815 | reproduced |
| 156 | ...to -27.46% at twelve, purely by trading it more often | -27.46 | -27.46 | 0.001062 | reproduced |
| 156 | ...to +9.79% at six... | 9.79 | 9.795 | 0.004518 | reproduced |

### docs/sweeps-and-parallelism.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 17 | fourteen buffers of 9.8 million f64 - 1.10 GB | 1,101 | 1,101 | 0.036 | reproduced |
| 20 | 4.37 MB a day of buffers | 4.37 | 4.368 | -0.002 | reproduced |
| 20 | materialises 9.8 million rows | 9,828,000 | 9,828,000 | 0 | reproduced |
| 22 | a hundred resident engines is roughly 110 GB | 110 | 110.1 | 0.0736 | reproduced |
| 32 | per-seed parallelism survives the split into seven per-domain substreams | 7 | - | - | covered_by_tests |

### docs/transaction-cost-analysis.md

| line | figure | published | measured | delta | status |
|---|---|---|---|---|---|
| 26 | the first name of random(20,7) has ADV 9,713 shares | 9,713 | 9,713 | 0 | reproduced |
| 30 | buying 97 shares (1% ADV) at the first step and holding costs +16.71 bps | 16.71 | 16.71 | -0.00245 | reproduced |
| 30 | the entry cost is identical on every seed measured | True | True | - | structural_ok |
| 33 | the round trip ends anywhere between -17.72... | -17.72 | -17.72 | 0.001221 | reproduced |
| 34 | ...and +2.03 bps | 2.03 | 2.029 | -0.001126 | reproduced |
| 34 | negative on seven of the eight | 7 | 7 | 0 | reproduced |
| 35 | the round trip's median across the eight published seeds: -12.40 bps | -12.4 | -12.4 | -0.001327 | reproduced |
| 44 | the request was for 4,856 shares (half ADV) | 4,856 | 4,856 | 0 | reproduced |
| 45 | a request for 4,856 shares (half ADV, sim seed 2026) filled 483 | 483 | 483 | 0 | reproduced |
| 46 | requests of 9,713 and 48,563 shares fill the same 483 | True | True | - | structural_ok |
| 47 | 483 at the open on every seed measured - opening depth is a property of the universe | True | True | - | structural_ok |
| 56 | order flow consumes no RNG draws - the per-domain stream split exists for exactly this | True | - | - | covered_by_tests |
| 65 | all three it never touched moved | 3 | 3 | 0 | reproduced |
| 65 | the agent traded 57 names | 57 | 57 | 0 | reproduced |
| 65 | leaving three untouched | 3 | 3 | 0 | reproduced |
| 66 | against a 9.71 bps median direct impact on the traded names | 9.71 | 9.709 | -0.001262 | reproduced |
| 66 | ...+2.00 and +1.97 bps | 2 | 1.998 | -0.001865 | reproduced |
| 66 | by -10.72... | -10.72 | -10.72 | 0.000909 | reproduced |
| 66 | the third leaked name: +1.97 bps | 1.97 | 1.965 | -0.004591 | reproduced |
| 79 | at one and two days untouched_moved() is still empty | True | True | - | structural_ok |
| 81 | nothing leaks at one or two days; nine untouched names leak at three | 9 | 9 | 0 | reproduced |
| 82 | at four days eighteen untouched names move | 18 | 18 | 0 | reproduced |
| 100 | pinned, untouched_moved() comes back empty - byte-exact | True | True | - | structural_ok |

## Notes

- **readme.residual** (README.md:76): the page publishes nine factor contributions (pt.Engine.FACTORS), but g_truth_residual in measures.py still sums the first seven: circuit_breaker and jump have to be added there before this row grades the claim the README actually makes
- **presets.model_crn** (docs/model-presets.md:162): asserted by tests/test_model_params.py: draws_consumed is identical across parameter vectors, the CRN guard that makes panel differences parameter effects
- **readme.workflow_wall** (README.md:199): examples/README.md:34 says five seconds too; the script's own docstring says ten, so one of the two is stale -- judged machine_bound either way
- **core.fv_distinct** (docs/core-concepts.md:60): replaces the retired core.fv_static (published 1): the frozen-macro section inverted at the era boundary
- **fork.resume_s** (docs/forking-a-simulation.md:23): a wall-clock absolute like readme.t100: machine-bound, reported but never judged at printed precision (kind=timing). The portable, judged form of this claim is fork.three_orders, the branch/resume ratio on the same 30-day run, held to a +/-1.2 band on log10
- **perf.overhead** (docs/performance.md:20): 'a few percent at most' operationalised as median per-pair overhead under 4%; the point claim was demoted to a bound after a 32-pair interleaved study across two sessions: median about +1%, nearly half the pairs negative, so no wall-clock point resolves it on a working machine
- **perf.overhead_median** (docs/performance.md:24): judged as a wide band, not at printed precision: the page itself says to expect the measurement to straddle zero, and observed session medians span -2.8% to +1.8% depending on machine load. The row flags only if the median leaves [-3, +5], which would genuinely contradict the page
- **perf.speedup** (docs/performance.md:28): hardware-bound like the absolute times; reported, not judged
- **perf.linear** (docs/performance.md:32): a ratio of two wall clocks, so judged generously: min-of-few timings wobble under load (measured 9.0 quiet, 6.3 loaded against 9.45 published). Anything inside [5.2, 13.7] still supports 'roughly linear' for 10x the work
- **reading.record_overhead** (docs/reading-results.md:35): 'a few percent at most' operationalised as median per-pair overhead under 4%; the point claim was demoted to a bound after a 32-pair interleaved study across two sessions: median about +1%, nearly half the pairs negative, so no wall-clock point resolves it on a working machine
- **scen.bit_day1** (docs/scenarios.md:77): published=false is the assertion: since the 0.2.0 coupling the sector draw reads VIX inside the tick, so a pin reaches the first close. The row carried published=true from the pre-coupling era and reported a structural_fail against prose that already said 'different'.
- **scen.old_draw_delta** (docs/scenarios.md:144): a historical record of a pre-split build, kept on the page as provenance; not re-measurable on this build
- **scen.market_sched** (docs/scenarios.md:145): replaces the retired scen.settle_conditional: the paragraph now states the post-split guarantee instead of the four-or-zero settlement branch
- **rng.kat_v5** (docs/rng-streams.md:36): the split's own version is a property of commit ad91026, not of this build; the current version is measured by rng.kat_now
- **rng.derivation** (docs/rng-streams.md:68): pinned by the golden test rng.rs::substream_derivation_is_the_documented_formula against hand-computed values
- **rng.sequence_bases** (docs/rng-streams.md:117): the universe sequence is exercised by every pinned fingerprint (realism.fingerprint, spec.universe_fp); 99 by the golden-parity replay harnesses; 256+k by the derivation golden test
- **agents.sep_mom_mr** (docs/agents-and-evaluation.md:133): the snippet comment at line 101; the prose repeats the separation at line 110 - edit them together
- **agents.sep_mom_rand** (docs/agents-and-evaluation.md:102): the prose repeats it at line 112
- **agents.mr_3d_thin_structural** (docs/agents-and-evaluation.md:128): was keyed to mr3_gt1_all_on_4_thinnest, an all-of-them assertion matching an earlier draft that said 'every'. The page says three of four and measures three of four; the row was checking the retired sentence.
- **llm.imp3_oracle_pos** (docs/an-llm-agent.md:53): re-keyed from llm.imp3_all_pos (both agents 12 of 12 at three days), which PR 26's re-measurement retired: three days no longer reads clean
- **llm.cost** (docs/an-llm-agent.md:124): priced by a third-party API, not measurable from the package
- **realism.market_sigma** (docs/how-realistic-is-this-market.md:163): the page's old mechanism paragraph (0.003, sector sigmas, variance share) is gone; 0.003 survives only as the reference implementation's value, so this row now pins the recalibrated constant the page publishes
- **realism.sweep_kurtosis_collapse** (docs/how-realistic-is-this-market.md:182): a historical record of the tools/calibration sweeps on the pre-era model; re-measuring it means rebuilding that model
- **realism.ceiling_counterfactual** (docs/how-realistic-is-this-market.md:45): requires rebuilding the engine with a changed constant
- **repro.kat_era_version** (docs/reproducing-a-run.md:212): v5 at the RNG split, v11 after the era's later model changes; tests/known_answer.py is the source
- **share.era_refusal** (docs/sharing-a-run.md:73): asserted by tests/test_manifest.py (test_a_different_era_is_refused_before_anything_replays and neighbours)
- **share.kat_five_targets** (docs/sharing-a-run.md:91): a record of a CI run, not re-measurable from the installed package; the digest itself is judged by repro.kat_current_digest
- **share.kat_current_digest** (docs/sharing-a-run.md:94): the page elides the digest to eight leading and five trailing characters, which no measured form matches, so the row grades the claim the sentence actually makes: that the printed digest is the committed baseline
- **tca.entry_cost** (docs/transaction-cost-analysis.md:30): was method_unknown ('one measured example', nothing stated); stream AB attached the test suite's pinned configuration and the value reproduced
- **tca.roundtrip_cost** (docs/transaction-cost-analysis.md:35): was -8.4 with a -13.3..+5.8 range; the pt-v12 default re-dealt the round trip, so the whole row moved together. Judged as the median against the printed -12.40, falling back to the published -17.72..+2.03 range
- **tca.roundtrip_lo** (docs/transaction-cost-analysis.md:33): measured -17.7188; the page prints two decimals, so the endpoints are judged inside a 0.1 bps band rather than at nearest-decimal precision
- **tca.roundtrip_hi** (docs/transaction-cost-analysis.md:34): see tca.roundtrip_lo on the band
- **tca.partial_fill** (docs/transaction-cost-analysis.md:45): was method_unknown; the mechanism is now stated correctly on the page as the book truncating at displayed depth (tca.fill_saturates, tca.fill_seed_invariant), and examples/research_workflow.py asserts the structural gate every run
- **tca.fill_saturates** (docs/transaction-cost-analysis.md:46): also asserted structurally by examples/research_workflow.py every run
- **tca.no_draws** (docs/transaction-cost-analysis.md:56): asserted by tests/test_tca.py (the untraded world is unmoved where the trader did not go) and tests/test_streams.py; the qualified successor of the retired scen.tca_exact
- **tca.ripple_untouched** (docs/transaction-cost-analysis.md:65): Momentum trades 57 of the 60 over ten days on the pt-v12 default, so three is the denominator and all three of them leak
- **tca.ripple_lo** (docs/transaction-cost-analysis.md:66): this is the LARGEST ripple in magnitude and it exceeds tca.ripple_direct, which is the point the page now makes
- **tca.ripple_short_horizon** (docs/transaction-cost-analysis.md:81): replaces the retired claim that days 2, 3 and 4 all leak nothing, which held only while vix_return_clamp was 0.03 (pt-v1..pt-v8). See tca.ripple_horizon_d2 and tca.ripple_horizon_d4
- **tca.ripple_pinned_empty** (docs/transaction-cost-analysis.md:100): also asserted by examples/research_workflow.py every run (readme.workflow_asserts executes it)
- **sweeps.buffers** (docs/sweeps-and-parallelism.md:17): fourteen f64 buffers per recorded day: prices, volumes, mispricing, fundamental, anchor and the nine attribution components (rust/src/python_arrow.rs, struct RecordedDay). Raw-buffer arithmetic only; resident cost is not published, because peak RSS on macOS is deflated by memory compression and ranged 66 to 165 MB across repeats of the same 26-day recording
- **sweeps.buffers_per_day** (docs/sweeps-and-parallelism.md:20): published alongside the 1.10 GB total so the per-day cost of `record=True` is legible without dividing; measured 4.368
- **sweeps.three_streams** (docs/sweeps-and-parallelism.md:32): seven substreams: market, economy, external, jumps, volume, news, volume_idio. tests/test_streams.py asserts len(state_snapshot()['rng']) == 3 * 7. NOT verifiable through the `stream_count` key, whose measure reads the legacy `draws_by_stream()` reporter and still returns three
- **edgar.requests** (docs/real-fundamentals-from-sec-edgar.md:26): a network-shape claim; exercised by tests/test_edgar.py through the injectable transport, not re-measured here. Counted through that transport on the four-filer market: 10 frame calls plus 4 submissions calls by default, 11 frame calls under rank_by="public_float". The ten are diluted EPS, stockholders' equity, two share-count tags (us-gaap and dei) and three revenue tags at two periods each (python/pretium/edgar.py, _REVENUE_TAGS)
- **rl.env_checker** (docs/reinforcement-learning.md:18): asserted by tests/test_gym.py when gymnasium is installed
- **spec.bitwise_baselines** (docs/strategy-specs.md:55): asserted by tests/test_spec.py
- **spec.weight_scale** (docs/strategy-specs.md:103): asserted by tests/test_spec.py (fingerprint scale invariance)
- **readme.workflow_asserts** (README.md:183): after an engine change a failure here means the example script itself needs re-fitting, not just the prose
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
