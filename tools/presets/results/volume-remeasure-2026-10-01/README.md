# pt-v20 volume rows on the fixed bars, 2026-10-01

Owner decision 1 fixed bar volume at `0a02fda`: a day bar had read the sum of
the day's running volume totals instead of the volume traded in the day. Two
certified rows read day-bar volume through `facts.measure`. This run
re-measures them for pt-v20 in every cell the certification measured, and
writes the new readings into `presets/pt-v20.json`, `envelope.CERTIFIED` and
`envelope.MEASURED_504`.

Build: `rel085/candidate` at `ada871e`, tradefloor 0.8.5, known answers
unchanged (simulation 72485a9f, presets 87f0b185, pt-v20 07ab6e0c, book
81aceb27). Run on the Mac with five workers (about six minutes in total).

Commands:

    python tools/calibration/preset_panel.py --only pt-v20 --workers 5 --out preset-panel-pt-v20.json
    python tools/presets/level_panel.py pt-v20 level-pt-v20.json --workers 5
    python tools/presets/level_panel.py pt-v19 level-pt-v19.json --workers 5
    python tools/presets/level_rows.py --target level-pt-v20.json --control level-pt-v19.json --out level-rows-pt-v20.json
    python tools/presets/record.py --panel preset-panel-pt-v20.json
    python tools/presets/record.py --level-rows level-rows-pt-v20.json
    python tools/presets/envelope_tables.py --record python/tradefloor/presets/pt-v20.json --write

Protocol, as recorded in the preset record: `Universe.random(40, seed=111)`,
seeds 101-130 at 252 and 504 days; held-out seeds 1-30; held-out universe
`Universe.random(60, seed=909)`, seeds 101-130; level protocol with the
roster varying with the seed, seeds 101-130, 252 days. Every non-volume row
of `panel_252` and `panel_504`, the four dispersion blocks, the crisis lever
and the in-band counts reproduced the committed record to the last digit.
The pt-v19 control arm reproduced its four published level and crisis
constants to four places, and pt-v20's level and crisis rows reproduced
theirs.

Medians, before (old bars) and after, against the ruled bands
(`facts.REAL_MARKETS_RULED` at 252 days, `REAL_MARKETS_RULED_504` at 504):

| cell | row | before | after | ruled band | verdict | room |
|---|---|---|---|---|---|---|
| panel_252 | volume_abs_return_corr | 0.5084 | 0.5958 | 0.35 to 0.64 | in | 0.0442 |
| panel_252 | volume_change_acf1 | -0.2540 | -0.2681 | -0.30 to -0.20 | in | 0.0319 |
| panel_504 | volume_abs_return_corr | 0.5614 | 0.6266 | 0.34 to 0.63 | in | 0.0034 |
| panel_504 | volume_change_acf1 | -0.2415 | -0.2606 | -0.30 to -0.20 | in | 0.0394 |
| heldout_seeds | volume_abs_return_corr | 0.5273 | 0.6122 | 0.35 to 0.64 | in | 0.0278 |
| heldout_seeds | volume_change_acf1 | -0.2462 | -0.2656 | -0.30 to -0.20 | in | 0.0344 |
| heldout_universe | volume_abs_return_corr | not kept | 0.5898 | 0.35 to 0.64 | in | 0.0502 |
| heldout_universe | volume_change_acf1 | not kept | -0.2673 | -0.30 to -0.20 | in | 0.0327 |
| level protocol | volume_abs_return_corr | 0.5134 | 0.6027 | 0.35 to 0.64 | in | 0.0373 |
| level protocol | volume_change_acf1 | -0.2540 | -0.2665 | -0.30 to -0.20 | in | 0.0335 |

The committed record keeps no held-out-universe panel, so that cell has no
"before" figure; its in-band count was 15 of 15 before and after.

All ten readings are inside their ruled bands, and in-band counts are
unchanged (15, 14 of 14 readable, 15, 15). The 504-day correlation is the
thin one: 0.0034 under its ceiling, about 0.4 of the 504-day seed sd
(`facts.SEED_SD_504`, 0.0087), and 13 of the 30 seeds read above 0.63
(range 0.453 to 0.701). On the 2015-2025 bands (`envelope.BANDS_504`, 0.48
to 0.65) it has 0.0234 of room.

What else moved. `volume_abs_return_corr` is no longer at the real centre of
0.536 in the mechanism blocks: z = 2.91 on `mechanism_252`, 3.21 on
`mechanism_heldout_seeds` and 3.19 on `level_protocol.certification`, against
-1.33, -0.37 and -1.09 before. The at-centre count falls from 9 to 8 of 14 on
both 252-day cells and from 11 to 10 of 14 on the level protocol. The
mechanism verdicts (shown 10 of 10) and the band counts do not move.
`mechanism_heldout_seeds` also changes in the last bit of two non-volume
floats (`return_acf1` centre median and `abs_return_acf20` z0_bootstrap),
which is summation-order noise and not a reading.
