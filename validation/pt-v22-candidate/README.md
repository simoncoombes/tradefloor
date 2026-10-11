# pt-v22 candidate: the rate term on a loss-maker's book value

pt-v21 with `lossmaker_rate_sensitivity` 11 (arm `ptv22c`), graded on
2026-10-11 against pt-v21 by name, paired on the same seeds, on
`feat/lossmaker-duration` at a1a9758 (known-answer simulation digest
`3a063f0d...`, unchanged). Run locally by `jobs.sh`, which writes `out/`;
the long run's raw histories are left out, as for pt-v21.

Exam seeds are the registered held-out seeds plus 270000 (270101-270130,
270401-270430, 270701-270730; 270001-270030 for the held-out-seeds cell;
270301-270330 for the scenarios). No recorded run in this repository or the
design repository used them before. The crisis-dispersion extension blocks
(1001-1060) are the protocol's own and were run before. The long-run rows
read the long run's 90 histories, not the 270 the eighteenth registration
pools, so they are graded on the twelfth registration's definitions.

| Grade | pt-v21 | ptv22c |
|---|---|---|
| one-year table, four cells (`out/certgrade-g1.txt`) | 15 of 15 in band in every cell | 15 of 15 in band in every cell |
| two-year panel, `panel_504` | 15 of 15 | 15 of 15 |
| long-run criteria (`out/criteria-g1.txt`) | 17 of 17 | 17 of 17 |
| R5, R6 (`out/driven2022.json`, 90 seeds) | 0.230, -7.36% per 100 bp | 0.232, -7.36% per 100 bp |

The scenario sizes are in `out/scenario-*.log`, the cause check in
`out/causes-rate-shock.log`, and the calibration's held-out reading in
`out/heldout-roster*.log`. `tools/calibration/lossmaker_duration.md` reads
them.
