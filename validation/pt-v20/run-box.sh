#!/bin/bash
# Run grade box ptv20g6 again, on a machine of your own.
#
#   bash validation/pt-v20/run-box.sh WORKDIR
#
# It does what the owner's AWS launcher did on 2026-09-26, without AWS:
#
#   1. copies the 31 scripts and data files the box ran into WORKDIR/scripts,
#      checking each one against scripts-as-run.txt;
#   2. clones tradefloor at b89901979e5a, the engine the grade ran on, into
#      WORKDIR/src, builds it into a Python 3.11 virtual environment and checks
#      the known-answer simulation digest;
#   3. runs programme/results/ptv20/ptv20-grade-jobs.sh with the box's
#      settings, writing WORKDIR/out;
#   4. grades WORKDIR/out with v1.py, certgrade_box.py and criteria.py, as the
#      desk did, writing WORKDIR/grade.
#
# Then compare WORKDIR/grade/criteria-g6.txt with
# programme/results/ptv20/criteria-g6.txt here. The header lines name the
# paths each run read, so they differ; the table should not.
#
# STAGE_ONLY=1 stops after step 1. The jobs ask for 90 workers in places,
# as the box had 96 cores (an AWS c8g.24xlarge). The box took 25 minutes
# for step 3. On fewer cores the same work takes proportionally longer.
#
# Needs: git, a Rust toolchain (cargo), python3.11.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${1:?usage: run-box.sh WORKDIR}"
PIN=b89901979e5ab449446daf1cf8e354667e3f1cec
SIM=72485a9fb16ba12d633fbc587dc63fb7293852b08219c8a84a8ed2cb40b7634e
SEEDS=101-130,401-430,701-730

mkdir -p "$WORK"
WORK="$(cd "$WORK" && pwd)"
export SCR="$WORK/scripts" OUT="$WORK/out" REPO="$WORK/src"

# 1: the scripts, as the box unpacked them
python3 - "$HERE" "$SCR" <<'PYEOF'
import hashlib, pathlib, shutil, sys
here, scr = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
n = 0
for line in (here / "scripts-as-run.txt").read_text().splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    sha, inside, published = line.split()
    src = here / published
    got = hashlib.sha256(src.read_bytes()).hexdigest()
    if got != sha:
        sys.exit(f"{published}: sha256 {got}, but the box ran {sha}")
    dest = scr.parent / inside
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    n += 1
print(f"staged {n} files in {scr}")
PYEOF
[ -n "${STAGE_ONLY:-}" ] && exit 0

# 2: the engine the grade ran on
mkdir -p "$OUT"
if [ ! -d "$REPO/.git" ]; then
  git clone https://github.com/simoncoombes/tradefloor.git "$REPO"
fi
git -C "$REPO" checkout --detach "$PIN"
cd "$REPO"
python3.11 -m venv .venv
export VIRTUAL_ENV="$REPO/.venv" PATH="$REPO/.venv/bin:$PATH"
pip install --quiet numpy pyarrow pyyaml maturin
rm -rf dist
maturin build --release --out dist --features python
pip install --quiet --no-index --find-links dist --force-reinstall tradefloor
python tests/known_answer.py | tee "$OUT/known-answer.txt"
grep -q "sim      $SIM" "$OUT/known-answer.txt" \
  || { echo "REFUSED: this build's simulation digest is not $SIM"; exit 1; }

# 3: the box's jobs, with the box's settings
export WORKERS="${WORKERS:-90}" LONGRUN_SEEDS=30 LONGRUN_YEARS=21
export LONGRUN_SEED_LIST="$SEEDS" XSEC_SEEDS="$SEEDS"
bash "$SCR/longrun/ptv20-grade-jobs.sh" 2>&1 | tee "$OUT/jobs.log"

# 4: the desk's grading
G="$WORK/grade"
mkdir -p "$G"
D="$HERE/programme"
python "$D/results/ptv20/v1.py" "$OUT" --out "$OUT/v1.json"
python "$D/longrun/certgrade_box.py" "$OUT" --engine "$REPO" \
    --out "$G/certgrade-g6.txt" --json "$G/certgrade-g6.json"
python "$D/longrun/criteria.py" --longrun "$OUT/longrun-report.json" \
    --certgrade "$G/certgrade-g6.json" --edge "$OUT/edge.json" \
    --c4 "$OUT/c4a.json" --c4 "$OUT/c4b.json" --xsec "$OUT/xsec.json" \
    --driven "$OUT/driven2020.json" --driven2022 "$OUT/driven2022.json" \
    --impact "pt-v20=$OUT/impact-pt-v20.json" --c10 "$OUT/c10.json" \
    --r7 "$OUT/r7-event.json" --r7 "$OUT/r7-eval.json" \
    --recession "$OUT/recession.json" --v1 "$OUT/v1.json" \
    --arm pt-v19 --arm pt-v20 --out "$G/criteria-g6.txt" --json "$G/criteria-g6.json" \
    --verdict "$G/verdict-pt-v20-g6.json" --verdict-arm pt-v20 \
    --box ptv20g6 --date "$(date -u +%Y-%m-%d)"
echo "graded: $G/criteria-g6.txt"
