# Validation

This folder holds the grades two default presets shipped on: pt-v21, the
default from tradefloor 0.10.0, in `pt-v21/`, and pt-v20, the default in
0.8.5 to 0.9.1, in `pt-v20/`. Each holds the registered rows, the scripts
that graded them, the inputs the grading machines ran on, and what they
wrote. With either you can check the grade on a laptop in under a second, or
run the whole grade again on a machine of your own.

pt-v21 was certified by name on the 0.10.0 engine on 2026-10-05, in box
`ptv21c1` and a supplement, `ptv21c1s`. It passes all 40 rows of pt-v20's
bar on the eighteenth registration's definitions, reading the long-run rows
on 270 histories of 21 years. `tf.preset_record("pt-v21")["long_run"]` is
that verdict. The section on pt-v21 below has the details.

pt-v20 was graded once, on 2026-09-26, in a run called box `ptv20g6`. It
passed all 40 rows of its twelfth registration on 90 histories of 21 years
(seed sets 101 to 130, 401 to 430 and 701 to 730). pt-v19, graded in the same
run as the control, fails 16 of them. `tf.preset_record("pt-v20")["long_run"]`
is that verdict, and its paths point into this folder. The sections up to
"pt-v21" describe pt-v20's folder.

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

## pt-v21

`pt-v21/` has the same layout as `pt-v20/`: everything under
`pt-v21/programme/`, as it was in the design repository, so the recorded
commands run unchanged.

The certification grades the same 40 rows as pt-v20's, with pt-v21 and
pt-v20 both named, on the engine at 931ed3d4 (tradefloor 0.10.0, known-answer
simulation digest `3a063f0d...`). Two things differ from pt-v20's grade.

- The long-run rows (A1 to A3, B1 to B8, C1, C2, V1 and B9's spread) are
  read on 270 histories: the 90 of seed sets 101 to 130, 401 to 430 and 701
  to 730, and 180 more from the same instrument on those blocks offset by
  50000 and by 60000. Every band and rule is pt-v20's. The recession rows
  S1a, S1b and S2 read 90 seeds.
- R4 and D1 are read on the eighteenth registration's definitions, the ones
  pt-v21 was registered and graded on. R4 is the correlation of the index
  with the corporate yield's fall at the held close (+0.328 on 270
  true-phase histories, band +0.15 to +0.39). D1's two level rows are pooled
  over 360 seeds (`index_tail_dn3_pct` 0.98 against a band of 0.47 to 1.96,
  `index_drift_pct` 7.00 against 2.9 to 11.9). The certification box did not
  record those inputs, so the supplement `ptv21c1s` measured them on the
  same engine.

On the twelfth registration's definitions, as pt-v20 was graded, pt-v21
reads 38 of 40: R4 at the last print is +0.112, and D1's tail rate on 30
seeds is 0.598. Both verdicts are here, and `criteria.py` writes either.

| Path under `pt-v21/` | What it is |
|---|---|
| `programme/ptv21-registration-18.md` | the eighteenth registration, which defines R4 and D1 as graded |
| `programme/longrun/criteria.py` | grades every row; `--definitions reg18 --reg18 FILE` reads R4 and D1 as the eighteenth registration does |
| `programme/results/ptv21/ptv21c1-command.md` | the launch commands and the desk commands for both boxes |
| `programme/results/ptv21/ptv21-cert-jobs.sh`, `ptv21c1s-jobs.sh` | the two boxes' jobs |
| `programme/results/ptv21/cert_lr270.py` | builds the 270 histories from the long run and its pool |
| `programme/results/ptv21/cert_reg18.py` | reads R4 and D1's inputs from the supplement into `reg18-c1.json` |
| `programme/r13reg/` | the registration's scripts the supplement ran (`r14gen.py`, `d1pool_box.py`, `seedplan.py`) and `lr270.py` |
| `programme/results/ptv21/box-c1/`, `box-c1s/` | what the two boxes wrote, except raw histories |
| `programme/results/ptv21/verdict-pt-v21-c1.json` | the verdict the preset record carries (40 of 40) |
| `programme/results/ptv21/verdict-pt-v21-c1-reg12.json` | the same grade on the twelfth registration's definitions (38 of 40) |
| `programme/results/ptv21/equiv/` | pt-v21 by name on 0.10.0 against the graded arm's dials on the build the eighteenth grade ran: six trajectory digests, all identical |
| `scripts-as-run.txt` | the sha256 of each of the 47 files the two boxes unpacked |
| `registration-files.txt` | the 37 files the registration cites by name (its seed file, the grade plan, the grading and screen scripts, `CRITERIA-pt-v21.md`), published at the paths it names, with the commit each was read from and its sha256 |
| `run-box.sh` | runs both boxes and the grading again, end to end |

From `validation/pt-v21/`:

```bash
R=programme/results/ptv21; B=$R/box-c1
python programme/longrun/criteria.py --longrun $B/lr270/longrun-report.json \
  --certgrade $R/certgrade-c1.json --edge $B/edge.json \
  --c4 $B/c4a.json --c4 $B/c4b.json --xsec $B/lr270/xsec.json \
  --driven $B/driven2020.json --driven2022 $B/driven2022.json \
  --impact pt-v21=$B/impact-pt-v21.json --c10 $B/c10.json \
  --r7 $B/r7-event.json --r7 $B/r7-eval.json --recession $B/recession.json \
  --v1 $B/lr270/v1.json --arm pt-v20 --arm pt-v21 \
  --definitions reg18 --reg18 $R/reg18-c1.json \
  --out /tmp/criteria-c1-reg18.txt --json /tmp/criteria-c1-reg18.json \
  --verdict /tmp/verdict-pt-v21-c1.json --verdict-arm pt-v21 \
  --box ptv21c1+ptv21c1s --date 2026-10-05
cmp /tmp/criteria-c1-reg18.txt $R/criteria-c1-reg18.txt
```

Leave out `--definitions` and `--reg18`, and pass `--box ptv21c1`, for the
twelfth registration's reading. `tests/test_validation.py` runs both on every
test run. `bash validation/pt-v21/run-box.sh WORKDIR` runs everything
again; the certification box took about 50 minutes on 96 cores.

## Files left out

- The long run's raw histories: 93 MB of NumPy files under
  `box-g6/longrun/`, and for pt-v21 about 2 GB under `box-c1/longrun/`,
  `box-c1/longrun-pool/` and `box-c1s/r13gen/`. The reports and `v1.json`
  are what the grades read from them (`box-c1/lr270/` for pt-v21), and the
  `run-box.sh` scripts write them again. The pool's and the long run's
  `meta.json` files, which say what each arm ran, are kept.
- Grades of presets that did not ship, and the seeds of any grade that has
  not run yet. Seeds are published once their grade has run.
