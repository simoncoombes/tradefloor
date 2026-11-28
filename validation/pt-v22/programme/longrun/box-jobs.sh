#!/bin/bash
# box-jobs -- the long-run instrument on $SCR/longrun/arms.txt, then (if
# $SCR/longrun/cert-arms.txt exists) the certification cells on those arms:
# 30 seeds at 252 with the lever and 504, held-out seeds 1-30, held-out
# universe (60 names, seed 909), and the crisis-dispersion extension on the
# cells short of ten readings (crisisext_box.py). Launched by box.sh. Env: REPO OUT SCR WORKERS
# (from user-data), LONGRUN_SEEDS/YEARS optional, CERT_WORKERS (default 48:
# a 504-day certification measurement holds ~2 GB).
set -euo pipefail
: "${REPO:?}" ; : "${OUT:?}" ; : "${SCR:?}" ; : "${WORKERS:?}"
bash "$SCR/longrun/longrun-jobs.sh" || echo "longrun-jobs exited $?"
rm -f "$OUT/DONE-JOBS"
CA="$SCR/longrun/cert-arms.txt"
if [ -s "$CA" ]; then
  export VIXLEVEL_BASE="${LONGRUN_BASE:-pt-v19}"
  CERT_WORKERS="${CERT_WORKERS:-48}"
  ARMS=()
  while IFS= read -r line; do
    line="${line%%#*}"; [ -z "${line// }" ] && continue
    ARMS+=(--arm "$line")
  done < "$CA"
  cd "$REPO"
  pass() { local name=$1 days=$2; shift 2
    echo "=== cert $name $days  $(date -u +%H:%M:%S)"
    python "$SCR/longrun/certrun_box.py" --workers "$CERT_WORKERS" --days "$days" "$@" 2>&1 \
      | tee "$BESTOF_OUT/run-$name-$days.log" | grep -E "^BASE|REFUSED|  vol " || true; }
  export BESTOF_OUT="$OUT/cert"; mkdir -p "$BESTOF_OUT"
  pass cert 252 --seeds 101-130 --lever-seeds 101-130 "${ARMS[@]}"
  pass cert 504 --seeds 101-130 --no-lever "${ARMS[@]}"
  export BESTOF_OUT="$OUT/heldseeds"; mkdir -p "$BESTOF_OUT"
  NO_VARY=1 pass heldseeds 252 --seeds 1-30 --no-lever "${ARMS[@]}"
  export BESTOF_OUT="$OUT/heldu"; mkdir -p "$BESTOF_OUT"
  HELD_N=60 HELD_SEED=909 NO_VARY=1 pass heldu 252 --seeds 101-130 --no-lever "${ARMS[@]}"
  # D1's crisis sector dispersion needs at least ten readings (ptv20-registration.md,
  # 2026-09-26): a cell whose seeds read it fewer than 10 times is extended, for
  # that row only, in blocks of 30 seeds from 1001 (cap 1270); certgrade_box.py grades it.
  echo "=== cert crisis extension  $(date -u +%H:%M:%S)"
  python "$SCR/longrun/crisisext_box.py" --out "$OUT" --workers "$CERT_WORKERS" 2>&1 \
    | tee "$OUT/crisisext.log" | grep -vE "^  .*: block " || true
fi
echo '{"kind":"box-jobs"}' > "$OUT/j-box-jobs.json"
echo "done $(date -u +%H:%M:%S)" > "$OUT/DONE-JOBS"
exit 0
