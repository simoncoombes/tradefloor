# Validation

This folder holds the grade that pt-v20, the default preset in tradefloor
0.8.5, shipped on: the registered rows, the scripts that graded them, the
inputs the grading machine ran on, and what it wrote. With it you can check
the grade on a laptop in under a second, or run the whole grade again on a
machine of your own.

pt-v20 was graded once, on 2026-09-26, in a run called box `ptv20g6`. It
passed all 40 rows of its twelfth registration on 90 histories of 21 years
(seed sets 101 to 130, 401 to 430 and 701 to 730). pt-v19, graded in the same
run as the control, fails 16 of them. `tf.preset_record("pt-v20")["long_run"]`
is that verdict, and its paths point into this folder.

## The files

Everything sits under `pt-v20/programme/`, laid out as it was in the project's
private design repository. The scripts and their outputs name each other by
those paths, so keeping the layout means the recorded commands run unchanged
and give byte-identical output.

| Path under `pt-v20/` | What it is |
|---|---|
| `programme/longrun/CRITERIA.md` | the first 17 rows, adopted 2026-09-23 and 2026-09-24 |
| `programme/ptv20-registration.md` | the registration: the other 23 rows, written before the grade ran, and the arm it graded |
| `programme/longrun/criteria.py` | grades every row and writes the verdict |
| `programme/longrun/certgrade_box.py` | grades the certification cells (row D1 and the one-year table) |
| `programme/results/ptv20/v1.py` | row V1, the long-horizon variance ratio, from the long run's histories |
| `programme/results/ptv20/ptv20-grade-jobs.sh` | the grading run itself; it calls the other scripts in `programme/longrun/` and `programme/results/` |
| `programme/results/ptv20/ptv20g6-command.md` | the launch command and the desk commands, as recorded |
| `programme/results/ptv20/arms-g6.txt`, `cert-arms-g6.txt` | the presets the run graded |
| `programme/results/ptv20/box-g6/` | every file the run wrote, except the long run's raw histories |
| `programme/results/ptv20/certgrade-g6.txt`, `criteria-g6.txt` | the grade, as text; `.json` beside each |
| `programme/results/ptv20/verdict-pt-v20-g6.json` | the verdict the preset record carries |
| `scripts-as-run.txt` | the sha256 of each of the 31 files the run unpacked |
| `run-box.sh` | runs the grade again, end to end |

The run unpacked its scripts from an archive whose sha256 is in
`box-g6/scripts-sha256.txt`. `scripts-as-run.txt` lists the archive's 31
files with their hashes and where each one is here, and
`tests/test_validation.py` checks that the files here still match. The desk
scripts (`criteria.py`, `certgrade_box.py`, `v1.py`) are copied from the
design repository's commit of the grade.

The real-market inputs are frozen copies: the S&P 500 and VIX tape
(`programme/longrun/data/tape.json.gz`, Yahoo Finance), the 2020-21 and 2022
macro paths (`programme/results/ptv20/data/`, Yahoo Finance and FRED), and
Moody's Baa yield from FRED (`programme/results/ptv20/edgar/DBAA.csv`).

## Check the grade

From `validation/pt-v20/`, with Python 3.9 or later and nothing else
installed, run the desk command the grade was made with:

```bash
R=programme/results/ptv20; B=$R/box-g6
python programme/longrun/criteria.py --longrun $B/longrun-report.json \
  --certgrade $R/certgrade-g6.json --edge $B/edge.json \
  --c4 $B/c4a.json --c4 $B/c4b.json --xsec $B/xsec.json \
  --driven $B/driven2020.json --driven2022 $B/driven2022.json \
  --impact pt-v20=$B/impact-pt-v20.json --c10 $B/c10.json \
  --r7 $B/r7-event.json --r7 $B/r7-eval.json --recession $B/recession.json \
  --v1 $B/v1.json --arm pt-v19 --arm pt-v20 \
  --out /tmp/criteria-g6.txt --json /tmp/criteria-g6.json \
  --verdict /tmp/verdict-pt-v20-g6.json --verdict-arm pt-v20 \
  --box ptv20g6 --date 2026-09-26
cmp /tmp/criteria-g6.txt $R/criteria-g6.txt
```

All three files it writes match the committed ones byte for byte.
`tests/test_validation.py` runs the same command on every test run.

The certification grade needs tradefloor installed, and you run it from
`validation/pt-v20/programme/longrun/` with this command:

```bash
python certgrade_box.py ../results/ptv20/box-g6 --engine ../../../.. \
  --out /tmp/certgrade-g6.txt --json /tmp/certgrade-g6.json
```

`--engine` names a tradefloor checkout; the script reads the one-year bands
from its `tools/calibration/preset_panel.py` and `tradefloor.facts`. On the
0.8.5 tree both files match the committed ones byte for byte. A later
release that moves a band will change the text, and the grade then has to
be read against b89901979e5a, the engine it ran on.

## Run the grade again

```bash
bash validation/pt-v20/run-box.sh ~/ptv20g6
```

The script copies the 31 files into `~/ptv20g6/scripts` and checks their
hashes, clones tradefloor at b89901979e5a, builds it, checks that the
known-answer simulation digest is `72485a9f...`, runs the grading jobs and
grades the output. It needs git, a Rust toolchain and Python 3.11. The
original run took 25 minutes on 96 cores (an AWS c8g.24xlarge). Compare
`~/ptv20g6/grade/criteria-g6.txt` with the committed one; the header lines
name the paths each run read, so only the table should match.

The simulation is deterministic for a given build and seed, but this script
has not been run end to end outside the original machine. The owner's
launcher starts an AWS instance and copies results to storage under the
owner's credentials, so it is not here. `run-box.sh` does the same steps on
the machine you run it on.

Two scripts, `certrun_box.py` and `crisisext_box.py`, default `REPO` to a
path on the maintainer's laptop. The grading jobs set `REPO`, so the default
is never read; the files are left as they ran.

## Files left out

- The long run's raw histories: 93 MB of NumPy files under
  `box-g6/longrun/`. `longrun-report.json` and `v1.json` are what the grade
  reads from them, and `run-box.sh` writes them again.
- Grades of presets that did not ship, and the seeds of any grade that has
  not run yet. Seeds are published once their grade has run.
