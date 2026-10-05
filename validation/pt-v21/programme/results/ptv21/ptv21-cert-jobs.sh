#!/bin/bash
# ptv21-cert-jobs.sh -- pt-v21's by-name certification box (ptv21c1), the
# protocol of box ptv20g6 (validation/pt-v20) unchanged except for the arms:
# pt-v21 by name, pt-v20 by name as the control (the previous default, as
# pt-v19 was in g6). Same seeds (long run 101-130, 401-430, 701-730 x 21
# years; certification cells 101-130 / 1-30 / held-out universe 909; edge
# 101-110 x 300; xsec and driven on the long run's 90; R7 and recession
# 101-130), same criteria.py, certgrade_box.py and v1.py on the desk.
# The long-run rows are read on 270 histories, as the eighteenth registration
# reads them (r13reg/lr270.py): the long run's 90 above plus a pool of 180
# recorded by the same instrument (step 1b), the long run's three blocks
# offset by 50000 and 60000 (LONGRUN_POOL_SEED_LIST). cert_lr270.py builds the
# 270 on the desk; criteria.py reads --longrun, --v1 and --xsec from it.
# Launched through box.sh with LONGRUN_BASE=pt-v20 in EXTRA_ENV; both arm
# files name both presets explicitly (NAME@NAME:), so no arm takes a base.
set -uo pipefail
: "${REPO:?}" ; : "${OUT:?}" ; : "${SCR:?}" ; : "${WORKERS:?}"
L="$SCR/longrun"
cd "$REPO"
git rev-parse HEAD > "$OUT/commit.txt"
python - <<'PYEOF' 2>&1 | tee "$OUT/fingerprint.txt"
import tradefloor as tf
m = tf.ModelParams.from_preset("pt-v21")
p = m.to_dict()
keys = ["cycle_equity_hazard_opening", "order_flow_coefficient", "order_flow_impact_law",
        "overnight_market_share", "impact_memory_coefficient", "market_factor_sigma",
        "closing_auction", "earnings_cycle_depth"]
# the twelfth registration's publication dials: graded rows C10 and R7 read them
pub = ["cycle_publication_lag", "gdp_publication_lag", "macro_publication_repricing",
       "unemployment_adjustment_half_life", "fear_greed_published_inputs"]
print("pt-v21", m.fingerprint, {k: p[k] for k in keys}, {k: p.get(k) for k in pub},
      "default", tf.model_preset()["name"], "version", tf.version())
assert str(m.fingerprint) == "pt-v21" and tf.model_preset()["name"] == "pt-v21", "pt-v21 is not this build's named default"
assert p["cycle_equity_hazard_opening"] == 0.011, "this build does not carry R21E1, the arm graded in grade 18"
assert tf.version() == "0.10.0", "this build is not the 0.10.0 release engine"
import os
if not all((p.get(k) or 0) > 0 for k in pub) and not os.environ.get("ALLOW_UNPUBLISHED"):
    raise SystemExit("this build's pt-v21 does not set the publication dials %s" % {k: p.get(k) for k in pub})
PYEOF
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "REFUSED: not pt-v21"; echo "done" > "$OUT/DONE-JOBS"; exit 1; }

ARMS=()
while IFS= read -r line; do
  line="${line%%#*}"; [ -z "${line// }" ] && continue
  ARMS+=(--arm "$line")
done < "$L/arms.txt"

# 1: long run and certification cells
bash "$L/box-jobs.sh" || echo "box-jobs exited $?"
rm -f "$OUT/DONE-JOBS"

# 1b: the pooled long run, 180 more histories (eighteenth registration, lr270.py): the
# long run's instrument on the same arms, into longrun-pool/ARM
: "${LONGRUN_POOL_SEED_LIST:?set the pool seed list}"
echo "=== pooled long run: $LONGRUN_POOL_SEED_LIST  $(date -u +%H:%M:%S)"
python "$L/longrun.py" measure --arms-file "$L/arms.txt" --base "${LONGRUN_BASE:-pt-v19}" \
    --seed-list "$LONGRUN_POOL_SEED_LIST" --years "${LONGRUN_YEARS:-21}" --workers "$WORKERS" --engine "$REPO" \
    --out "$OUT/longrun-pool" > "$OUT/longrun-pool.log" 2>&1
for d in "$OUT"/longrun-pool/*/; do echo "$d $(ls "$d" | grep -c -- '-free.npz') free histories"; done

# 2: C3
echo "=== edge  $(date -u +%H:%M:%S)"
python "$L/edge.py" "${ARMS[@]}" --base pt-v20 --seeds 10 --first-seed 101 --days 300 \
    --workers 40 --out "$OUT/edge.json" 2>&1 | tee "$OUT/edge.log" | tail -n 8

# 3: C4a, C4b
echo "=== c4  $(date -u +%H:%M:%S)"
python "$L/c4.py" "${ARMS[@]}" --part a --workers 48 --engine-commit "$(git rev-parse HEAD)" \
    --out "$OUT/c4a.json" 2>&1 | tee "$OUT/c4a.log" | tail -n 8
python "$L/c4.py" "${ARMS[@]}" --part b --workers 48 --engine-commit "$(git rev-parse HEAD)" \
    --out "$OUT/c4b.json" 2>&1 | tee "$OUT/c4b.log" | tail -n 12

# 4: C5-C8
echo "=== xsec  $(date -u +%H:%M:%S)"
python "$L/grade_xsec.py" "$OUT/xsec.json" "${ARMS[@]}" --seeds "${XSEC_SEEDS:-101-130}" --days 2660 --workers 90 \
    --longrun "$OUT/longrun" \
    2>&1 | tee "$OUT/xsec.log" | tail -n 12

# 4b: C9, the cost of size, on every arm that carries the agent-facing book
echo "=== impact  $(date -u +%H:%M:%S)"
for m in pt-v21 pt-v20; do
python tools/calibration/impact_curve.py --base $m --seeds 3 --names 40 --days 60 \
    --out "$OUT/impact-$m.json" 2>&1 | tee "$OUT/impact-$m.log" | tail -n 14
done

# 5: scenario size, pt-v19 and pt-v20 (skipped on a grid: GRID=1)
[ -n "${GRID:-}" ] || {
echo "=== scenarios  $(date -u +%H:%M:%S)"
for m in pt-v20 pt-v21; do
  python "$L/scenario_size.py" "$OUT/scenario-$m.json" --model "$m" --seeds 301-330 --workers 90 \
      2>&1 | tee "$OUT/scenario-$m.log" | tail -n 8
done
for y in recession_proposed liquidity_crisis_proposed; do
  python "$L/scenario_size.py" "$OUT/scenario-pt-v21-$y.json" --model pt-v21 --seeds 301-330 --workers 90 \
      --scenarios "$L/$y.yml" 2>&1 | tee "$OUT/scenario-pt-v21-$y.log" | tail -n 4
done
}

# 5b: D2, the driven 2020-21 market (fifth registration), on the long run's arms
echo "=== driven2020  $(date -u +%H:%M:%S)"
python "$L/driven2020.py" "$OUT/driven2020.json" "${ARMS[@]}" --seeds "${XSEC_SEEDS:-101-130}" --workers 90 \
    2>&1 | tee "$OUT/driven2020.log" | tail -n 4

# 5d: C10, no public macro signal predicts returns (eleventh registration)
echo "=== c10  $(date -u +%H:%M:%S)"
ARMNAMES=$(grep -v '^#' "$L/arms.txt" | sed '/^ *$/d' | cut -d@ -f1 | cut -d: -f1 | paste -sd, -)
# The long run records the fields as the engine publishes them (the phase a year
# late, GDP quarterly), so no --lag: the published record is what is graded.
python "$L/c10.py" "$OUT" --arms "$ARMNAMES" --out "$OUT/c10.json" 2>&1 | tee "$OUT/c10.log"

# 5c: R5 and R6, the driven 2022 market (eighth registration)
echo "=== driven2022  $(date -u +%H:%M:%S)"
python "$L/driven2022.py" "$OUT/driven2022.json" "${ARMS[@]}" --seeds "${XSEC_SEEDS:-101-130}" --workers 90 \
    2>&1 | tee "$OUT/driven2022.log" | tail -n 6

# 5e: R7a and R7b, no drift after a published policy-rate decision (twelfth registration)
echo "=== r7  $(date -u +%H:%M:%S)"
python "$L/r7_event.py" "$OUT/r7-event.json" "${ARMS[@]}" --seeds "${R7_SEEDS:-101-130}" --years 21 --workers 90 \
    2>&1 | tee "$OUT/r7-event.log" | tail -n 4
python "$L/r7_eval.py" "$OUT/r7-eval.json" "${ARMS[@]}" --seeds "${R7_SEEDS:-101-130}" --years 10 --workers 90 \
    2>&1 | tee "$OUT/r7-eval.log" | tail -n 4

# 5f: S1a, S1b and S2, the packaged recession recovers (twelfth registration): the
# recession.yml inside the engine this box built, not a copy
echo "=== recession  $(date -u +%H:%M:%S)"
python "$L/recession_rows.py" "$OUT/recession.json" "${ARMS[@]}" --seeds "${REC_SEEDS:-101-130}" --days 900 --workers 90 \
    2>&1 | tee "$OUT/recession.log" | tail -n 4

# 5g: V1, the long-horizon variance ratio: graded on the desk by v1.py from
# $OUT/longrun/*/SEED-free.npz, writing v1.json for criteria.py --v1.

# 6: the paired preset panel for pt-v21's record, on the same commit as the long run
[ -n "${GRID:-}" ] || {
echo "=== preset panel, pt-v21 and pt-v20  $(date -u +%H:%M:%S)"
python tools/calibration/preset_panel.py --only pt-v21,pt-v20 --workers 48 --out "$OUT/preset-panel.json" 2>&1 \
  | tee "$OUT/preset-panel.log" | tail -n 12
}

echo '{"kind":"ptv21-cert"}' > "$OUT/j-ptv21-cert.json"
echo "done $(date -u +%H:%M:%S)" > "$OUT/DONE-JOBS"
exit 0
