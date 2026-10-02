#!/bin/bash
# ptv20-grade-jobs.sh -- the grading box for pt-v20, as registered in
# programme/ptv20-registration.md before it ran. Launched through box.sh:
#
#   ENGINE_PATCHES=<dir of git format-patch files over PIN> \
#   EXTRA_FILES="programme/results/ptv20/ptv20-grade-jobs.sh programme/longrun/c4.py \
#     programme/results/news-speed/edge.py programme/results/ptv20/desk.py \
#     programme/results/ptv20/xsec.py programme/results/ptv20/grade_xsec.py \
#     programme/results/ptv20/bands.json programme/results/ptv20/scenario_size.py" \
#   JOBS=longrun/ptv20-grade-jobs.sh \
#   bash programme/longrun/box.sh ptv20g1 main <PIN> \
#        programme/results/ptv20/arms.txt programme/results/ptv20/cert-arms.txt
#
#   1. the long run (30 seeds x 21 years and the 2008 / 2020 replays) and the
#      four certification cells, on every arm (box-jobs.sh);
#   2. C3, the headline edge (edge.py, seeds 101-110 x 300 sessions);
#   3. C4a and C4b (c4.py, E2's instrument);
#   4. C5-C8 (grade_xsec.py: 30 histories of 2,660 sessions, seeds 101-130,
#      and the 20 suite markets' first 60 sessions), bands from bands.json;
#   5. the packaged scenarios' price effects, pt-v19, pt-v20 and pt-v20-m05, paired against
#      the same seeds without the scenario (scenario_size.py, seeds 301-330);
#   6. pt-v20's preset panel for its record (preset_panel.py --only pt-v20).
# Twelfth registration (box ptv20g6 on): C10 with the event rules, on the
# published fields as the long run records them (no --lag); R7a and R7b
# (r7_event.py, r7_eval.py, seeds R7_SEEDS, default 101-130); S1a, S1b and S2
# (recession_rows.py, the packaged recession.yml of the box's engine, seeds
# REC_SEEDS, default 101-130: the file was tuned on 301-330); V1 has no box
# job: v1.py reads the long run's free histories on the desk.
# After collect, on the desk: certgrade_box.py --json, then criteria.py with
# --c4 and --xsec (and --driven, --driven2022, --impact, --c10, --r7 twice,
# --recession), one verdict per arm.
set -uo pipefail
: "${REPO:?}" ; : "${OUT:?}" ; : "${SCR:?}" ; : "${WORKERS:?}"
L="$SCR/longrun"
cd "$REPO"
git rev-parse HEAD > "$OUT/commit.txt"
python - <<'PYEOF' 2>&1 | tee "$OUT/fingerprint.txt"
import tradefloor as tf
p = tf.ModelParams.from_preset("pt-v20").to_dict()
keys = ["quote_model_weight", "closing_auction", "fair_value_news_share", "opening_mispricing_sigma",
        "opening_market_sigma", "cascade_gain", "earnings_cycle_depth", "market_factor_sigma",
        "corporate_yield_daily", "book_depth_coefficient"]
# the twelfth registration's publication dials: graded rows C10 and R7 read them
pub = ["cycle_publication_lag", "gdp_publication_lag", "macro_publication_repricing",
       "unemployment_adjustment_half_life", "fear_greed_published_inputs"]
print("pt-v20", {k: p[k] for k in keys}, {k: p.get(k) for k in pub},
      "default", tf.model_preset()["name"], "version", tf.version())
assert p["closing_auction"] == 1.0 and p["earnings_cycle_depth"] > 0.0, "this build does not carry pt-v20's final vector"
import os
if not all((p.get(k) or 0) > 0 for k in pub) and not os.environ.get("ALLOW_UNPUBLISHED"):
    raise SystemExit("this build's pt-v20 does not set the publication dials %s" % {k: p.get(k) for k in pub})
PYEOF
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "REFUSED: not pt-v20"; echo "done" > "$OUT/DONE-JOBS"; exit 1; }

ARMS=()
while IFS= read -r line; do
  line="${line%%#*}"; [ -z "${line// }" ] && continue
  ARMS+=(--arm "$line")
done < "$L/arms.txt"

# 1: long run and certification cells
bash "$L/box-jobs.sh" || echo "box-jobs exited $?"
rm -f "$OUT/DONE-JOBS"

# 2: C3
echo "=== edge  $(date -u +%H:%M:%S)"
python "$L/edge.py" "${ARMS[@]}" --base pt-v19 --seeds 10 --first-seed 101 --days 300 \
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
python tools/calibration/impact_curve.py --base pt-v20 --seeds 3 --names 40 --days 60 \
    --out "$OUT/impact-pt-v20.json" 2>&1 | tee "$OUT/impact-pt-v20.log" | tail -n 14

# 5: scenario size, pt-v19 and pt-v20 (skipped on a grid: GRID=1)
[ -n "${GRID:-}" ] || {
echo "=== scenarios  $(date -u +%H:%M:%S)"
for m in pt-v19 pt-v20; do
  python "$L/scenario_size.py" "$OUT/scenario-$m.json" --model "$m" --seeds 301-330 --workers 90 \
      2>&1 | tee "$OUT/scenario-$m.log" | tail -n 8
done
for y in recession_proposed liquidity_crisis_proposed; do
  python "$L/scenario_size.py" "$OUT/scenario-pt-v20-$y.json" --model pt-v20 --seeds 301-330 --workers 90 \
      --scenarios "$L/$y.yml" 2>&1 | tee "$OUT/scenario-pt-v20-$y.log" | tail -n 4
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

# 6: pt-v20's preset panel, for its record (skipped on a grid)
[ -n "${GRID:-}" ] || {
echo "=== preset panel, pt-v20  $(date -u +%H:%M:%S)"
python tools/calibration/preset_panel.py --only pt-v20 --workers 48 --out "$OUT/preset-panel.json" 2>&1 \
  | tee "$OUT/preset-panel.log" | tail -n 12
}

echo '{"kind":"ptv20-grade"}' > "$OUT/j-ptv20-grade.json"
echo "done $(date -u +%H:%M:%S)" > "$OUT/DONE-JOBS"
exit 0
