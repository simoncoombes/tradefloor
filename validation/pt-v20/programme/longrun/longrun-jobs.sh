#!/bin/bash
# longrun -- the long-horizon and crash check (programme/longrun/README.md)
# for every arm in an arms file, on a box launched by fleet.py with
# programme/scripts/user-data-measure-lean.sh. The runner exports REPO (the
# engine checkout, built), OUT, SCR and WORKERS; this file is
# $SCR/longrun/longrun-jobs.sh inside the scripts tgz that pack.sh makes.
#
# Optional, through fleet.py --var EXTRA="KEY=VALUE ..." or the environment:
#   LONGRUN_ARMS_FILE  arms file (default $SCR/longrun/arms.txt)
#   LONGRUN_SEEDS      seeds per arm, from 101 (default 30)
#   LONGRUN_YEARS      years per free-running history (default 21)
#   LONGRUN_BASE       base preset (default pt-v19)
#   LONGRUN_FIRST_SEED first seed (default 101; calibration boxes use others)
#   LONGRUN_SKIP_KAT   set to skip the known-answer test (desk dry run)
#   PY                 python (default python, the box venv)
set -euo pipefail
: "${REPO:?}" ; : "${OUT:?}" ; : "${SCR:?}" ; : "${WORKERS:?}"
PY="${PY:-python}"
LR="$SCR/longrun"
ARMS_FILE="${LONGRUN_ARMS_FILE:-$LR/arms.txt}"
SEEDS="${LONGRUN_SEEDS:-30}"
YEARS="${LONGRUN_YEARS:-21}"
BASE="${LONGRUN_BASE:-pt-v19}"
FIRST="${LONGRUN_FIRST_SEED:-101}"
SEEDLIST="${LONGRUN_SEED_LIST:-}"
mkdir -p "$OUT"
cd "$REPO"
git rev-parse HEAD > "$OUT/commit.txt"
if [ -z "${LONGRUN_SKIP_KAT:-}" ]; then
  "$PY" tests/known_answer.py > "$OUT/known-answer.txt" 2>&1 || true
fi
cp "$ARMS_FILE" "$OUT/arms.txt"
echo "=== longrun measure: $SEEDS seeds x $YEARS years, base $BASE, $WORKERS workers  $(date -u +%H:%M:%S)"
"$PY" "$LR/longrun.py" measure --arms-file "$ARMS_FILE" --base "$BASE" \
    --seeds "$SEEDS" --first-seed "$FIRST" ${SEEDLIST:+--seed-list "$SEEDLIST"} --years "$YEARS" --workers "$WORKERS" --engine "$REPO" \
    --out "$OUT/longrun" 2>&1 | tee "$OUT/longrun-measure.log" | grep -E "^BUILD|REFUSED|tasks, " || true
# every requested part must be on disk before the report is called complete
MISSING=$("$PY" - "$OUT/longrun" "$ARMS_FILE" "$SEEDS" "$FIRST" "$SEEDLIST" <<'PYEOF'
import pathlib, sys
out, arms, n, first = pathlib.Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
seedlist = sys.argv[5] if len(sys.argv) > 5 else ""
if seedlist:
    seeds = [s for part in seedlist.split(",") for s in range(int(part.split("-")[0]), int(part.split("-")[-1]) + 1)]
else:
    seeds = list(range(first, first + n))
names = [l.split("#", 1)[0].split(":", 1)[0].split("@", 1)[0].strip() for l in open(arms)]
miss = [f"{a}/{s}-{p}" for a in names if a for s in seeds for p in ("free", "gfc", "covid")
        if not (out / a / f"{s}-{p}.json").exists()]
print(len(miss), " ".join(miss[:10]))
PYEOF
)
echo "missing parts: $MISSING" | tee "$OUT/longrun-missing.txt"
echo "=== longrun report  $(date -u +%H:%M:%S)"
"$PY" "$LR/longrun.py" report "$OUT/longrun" --out "$OUT/longrun-report.txt" 2>&1 | tail -n 5
echo '{"kind":"longrun"}' > "$OUT/j-longrun.json"
echo "done $(date -u +%H:%M:%S)" > "$OUT/DONE-JOBS"
exit 0
