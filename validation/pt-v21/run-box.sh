#!/bin/bash
# Run pt-v21's certification again, on a machine of your own: box ptv21c1
# and its supplement ptv21c1s.
#
#   bash validation/pt-v21/run-box.sh WORKDIR
#
# It does what the AWS launcher did on 2026-10-05, without AWS:
#
#   1. copies the files each box ran into WORKDIR/c1/scripts and
#      WORKDIR/c1s/scripts, checking each against scripts-as-run.txt;
#   2. clones tradefloor at 931ed3d4, the 0.10.0 engine both boxes ran, into
#      WORKDIR/src, builds it into a Python 3.11 virtual environment and checks
#      the known-answer simulation digest;
#   3. runs ptv21-cert-jobs.sh (WORKDIR/c1/out) and ptv21c1s-jobs.sh
#      (WORKDIR/c1s/out) with the boxes' settings;
#   4. grades them as the desk did (cert_lr270.py, v1.py, certgrade_box.py,
#      cert_reg18.py, criteria.py --definitions reg18), writing WORKDIR/grade.
#
# Then compare WORKDIR/grade/criteria-c1-reg18.txt with
# programme/results/ptv21/criteria-c1-reg18.txt here. The header lines name
# the paths each run read, so they differ; the table should not.
#
# STAGE_ONLY=1 stops after step 1. The jobs ask for 90 workers in places, as
# the boxes had 96 cores (an AWS c8g.24xlarge); the certification box took
# about 50 minutes for step 3. On fewer cores the same work takes
# proportionally longer.
#
# Needs: git, a Rust toolchain (cargo), python3.11.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WORK="${1:?usage: run-box.sh WORKDIR}"
PIN=931ed3d4fe659e4dec765673ee3e280a6b40ab2b
SIM=3a063f0d207bc37b5ade3b23c60f5e358b275507550b7f75f172a8d7cafb075c
SEEDS=101-130,401-430,701-730
POOL=50101-50130,50401-50430,50701-50730,60101-60130,60401-60430,60701-60730

mkdir -p "$WORK"
WORK="$(cd "$WORK" && pwd)"

# 1: the scripts, as each box unpacked them
python3 - "$HERE" "$WORK" <<'PYEOF'
import hashlib, pathlib, shutil, sys
here, work = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
n = 0
for line in (here / "scripts-as-run.txt").read_text().splitlines():
    if not line.strip() or line.startswith("#"):
        continue
    sha, inside, published = line.split()
    box, _, path = inside.partition(":")
    src = here / published
    got = hashlib.sha256(src.read_bytes()).hexdigest()
    if got != sha:
        sys.exit(f"{published}: sha256 {got}, but {box} ran {sha}")
    dest = work / box.replace("box-", "") / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)
    n += 1
print(f"staged {n} files in {work}")
PYEOF
[ -n "${STAGE_ONLY:-}" ] && exit 0

# 2: the engine the boxes ran on
REPO="$WORK/src"
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
mkdir -p "$WORK/c1/out" "$WORK/c1s/out"
python tests/known_answer.py | tee "$WORK/c1/out/known-answer.txt"
grep -q "sim      $SIM" "$WORK/c1/out/known-answer.txt" \
  || { echo "REFUSED: this build's simulation digest is not $SIM"; exit 1; }

# 3: the boxes' jobs, with the boxes' settings
export REPO WORKERS="${WORKERS:-90}" LONGRUN_SEEDS=30 LONGRUN_YEARS=21
( export SCR="$WORK/c1/scripts" OUT="$WORK/c1/out" LONGRUN_BASE=pt-v20 \
    LONGRUN_SEED_LIST="$SEEDS" XSEC_SEEDS="$SEEDS" REC_SEEDS="$SEEDS" \
    LONGRUN_POOL_SEED_LIST="$POOL"
  bash "$SCR/longrun/ptv21-cert-jobs.sh" 2>&1 | tee "$OUT/jobs.log" )
( export SCR="$WORK/c1s/scripts" OUT="$WORK/c1s/out" \
    GEN_SEEDS=101-190,401-490,701-790 D1POOL_SEEDS=101-300,331-490
  bash "$SCR/longrun/ptv21c1s-jobs.sh" 2>&1 | tee "$OUT/jobs.log" )

# 4: the desk's grading
G="$WORK/grade"
mkdir -p "$G"
D="$HERE/programme"
B="$WORK/c1/out"
python "$D/results/ptv21/cert_lr270.py" "$B"
python "$D/results/ptv20/v1.py" "$B" --out "$B/v1.json"
python "$D/longrun/certgrade_box.py" "$B" --engine "$REPO" \
    --out "$G/certgrade-c1.txt" --json "$G/certgrade-c1.json"
python "$D/results/ptv21/cert_reg18.py" "$WORK/c1s/out" --engine "$REPO" \
    --commit "$(cat "$B/commit.txt")" --gen-seeds 101-190,401-490,701-790 \
    --d1pool-seeds 101-300,331-490 --out "$G/reg18-c1.json"
python "$D/longrun/criteria.py" --longrun "$B/lr270/longrun-report.json" \
    --certgrade "$G/certgrade-c1.json" --edge "$B/edge.json" \
    --c4 "$B/c4a.json" --c4 "$B/c4b.json" --xsec "$B/lr270/xsec.json" \
    --driven "$B/driven2020.json" --driven2022 "$B/driven2022.json" \
    --impact "pt-v21=$B/impact-pt-v21.json" --c10 "$B/c10.json" \
    --r7 "$B/r7-event.json" --r7 "$B/r7-eval.json" \
    --recession "$B/recession.json" --v1 "$B/lr270/v1.json" \
    --arm pt-v20 --arm pt-v21 --definitions reg18 --reg18 "$G/reg18-c1.json" \
    --out "$G/criteria-c1-reg18.txt" --json "$G/criteria-c1-reg18.json" \
    --verdict "$G/verdict-pt-v21-c1.json" --verdict-arm pt-v21 \
    --box ptv21c1+ptv21c1s --date "$(date -u +%Y-%m-%d)"
echo "graded: $G/criteria-c1-reg18.txt"
