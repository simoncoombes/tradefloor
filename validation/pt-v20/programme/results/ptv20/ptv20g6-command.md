# ptv20g6: the final grading box

One box grades pt-v19 and pt-v20 by name on every registered row: the 34, plus
R7a, R7b, S1a, S1b, S2 and V1 (twelfth registration), with C10 on its event
rules and D1 on its ten readings. V1 is graded on the desk from the long run's
free histories (v1.py), so it has no box job.

Launch it only on fix/ptv20-final (fix/ptv20-core with fix/ptv20-vr-feedback
merged) once pt-v20 sets the registered arm's values (the registration's last
section, "The graded arm").

## The command

    R=programme/results/ptv20
    BRANCH=<the merged engine branch>; PIN=$(git -C <engine worktree> rev-parse $BRANCH)
    KAT=<new sim digest> EXTRA_FILES="$R/ptv20-grade-jobs.sh programme/longrun/c4.py programme/results/news-speed/edge.py $R/desk.py $R/xsec.py $R/grade_xsec.py $R/bands.json $R/scenario_size.py $R/scenarios/recession_proposed.yml $R/scenarios/liquidity_crisis_proposed.yml $R/driven2020.py $R/driven2022.py $R/c10.py $R/r7_event.py $R/r7_eval.py $R/recession_rows.py $R/data/covid-2020-2021.json $R/data/driven-2022.json $R/edgar/DBAA.csv" \
    JOBS=longrun/ptv20-grade-jobs.sh SKIP_GATE=1 DEADMAN_MIN=180 \
    EXTRA_ENV="LONGRUN_SEED_LIST=101-130,401-430,701-730 XSEC_SEEDS=101-130,401-430,701-730" \
    bash programme/longrun/box.sh ptv20g6 $BRANCH $PIN $R/arms-g6.txt $R/cert-arms-g6.txt

What changed from the status file's command:
- EXTRA_FILES adds `r7_event.py`, `r7_eval.py` and `recession_rows.py`.
  `c10.py` was already there. It now carries the event rules.
- The branch is the merged one, not fix/ptv20-core alone. R7 needs
  `macro_publication_repricing` (fix/ptv20-ratenews), and S1/S2 need the
  recession.yml from fix/ptv20-recession.
- recession.yml is not in EXTRA_FILES. The box grades the file packaged in
  the engine it builds (`tf.Scenario.load("recession")`).
  `recession.json` records that file's path and sha256.
- The jobs refuse to start (`REFUSED: not pt-v20`) unless the build's pt-v20
  sets `cycle_publication_lag`, `gdp_publication_lag` and
  `macro_publication_repricing` above 0. `ALLOW_UNPUBLISHED=1` in EXTRA_ENV
  overrides this. Use it only for a deliberate test.
- `arms-g6.txt` holds `pt-v19:` and `pt-v20@pt-v20:`. `cert-arms-g6.txt`
  holds `SHIPPED:` and `pt-v20@pt-v20:`.

## The new jobs (ptv20-grade-jobs.sh, after C10)

| step | script | seeds | size | out |
|---|---|---|---|---|
| 5d C10 | c10.py (no `--lag`: the long run records the published fields) | the long run's 90 | 21 years | c10.json |
| 5e R7a | r7_event.py | `R7_SEEDS`, default 101-130 | 21 years | r7-event.json |
| 5e R7b | r7_eval.py | `R7_SEEDS`, default 101-130 | 10 years | r7-eval.json |
| 5f S1a/S1b/S2 | recession_rows.py | `REC_SEEDS`, default 101-130 | 900 sessions | recession.json |

D1's ten-reading rule (registration, 2026-09-26) adds one step inside
box-jobs.sh, after the four certification cells: `crisisext_box.py` extends
every cell whose own seeds read `crisis_sector_dispersion` fewer than 10
times, in blocks of 30 seeds from 1001 (1241-1270 the last), and writes
`<cell dir>/crisisext[504]/ext-<arm>.json`. box.sh ships the script with
certrun_box.py, so EXTRA_FILES does not change. Worst case, every cell of
both arms running all nine blocks: about 7 minutes; one arm's panel_504 to
the cap alone: about 2 minutes.

The desk estimate is about 15 more minutes on a c8g.24xlarge. The ptv20g5
jobs took about 30 minutes, so DEADMAN_MIN=180 still leaves room.

## Grading after collect

    B=$R/box-g6
    python $R/v1.py $B --out $B/v1.json
    python programme/longrun/criteria.py --longrun $B/longrun-report.json --certgrade $R/certgrade-g6.json \
      --edge $B/edge.json --c4 $B/c4a.json --c4 $B/c4b.json --xsec $B/xsec.json \
      --driven $B/driven2020.json --driven2022 $B/driven2022.json --impact pt-v20=$B/impact-pt-v20.json \
      --c10 $B/c10.json --r7 $B/r7-event.json --r7 $B/r7-eval.json --recession $B/recession.json \
      --v1 $B/v1.json \
      --arm pt-v19 --arm pt-v20 --out $R/criteria-g6.txt --json $R/criteria-g6.json \
      --verdict $R/verdict-pt-v20-g6.json --verdict-arm pt-v20 --box ptv20g6 --date <date>

certgrade_box.py reads the extension records and prints, per cell, the
readings from the own seeds, the extension blocks used, the total, the median,
the band and the verdict; criteria.py repeats them under D1. A cell short of
10 with no extension record is unresolved, and D1 is then not scored.

`--v1` takes v1.py's JSON and grades V1a and V1b against the registered
bands; a file carrying other bands is refused.
