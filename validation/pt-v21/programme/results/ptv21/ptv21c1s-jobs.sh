#!/bin/bash
# ptv21c1s-jobs.sh -- the supplement to pt-v21's by-name certification box
# (ptv21c1): the two readings the eighteenth registration's R4 and D1 need and
# ptv21c1 did not record, on pt-v21 by name, same engine commit, same build
# check. Nothing else is measured.
#   R4 on the held close: true-phase histories (r14gen.py), 270 x 5292
#     sessions, the long run's three blocks each extended to 90 (GEN_SEEDS
#     101-190, 401-490, 701-790), as the eighteenth registration extends its
#     own long-run blocks (201-290, 501-590, 801-890).
#   D1's pooled level rows: d1pool_box.py on 360 seeds, 200 from the cert
#     cell's first seed and 160 from 230 past it (D1POOL_SEEDS 101-300,
#     331-490), as the registration lays out 201-400, 431-590 from its 201.
# cert_reg18_box.py runs both (see its docstring: the launched seed lists in
# place of seedplan.py's rule, and the preset by name).
set -uo pipefail
: "${REPO:?}" ; : "${OUT:?}" ; : "${SCR:?}" ; : "${WORKERS:?}"
: "${GEN_SEEDS:?}" ; : "${D1POOL_SEEDS:?}"
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
python tests/known_answer.py > "$OUT/known-answer.txt" 2>&1 || true
cp "$L/arms.txt" "$OUT/arms.txt"

echo "=== true-phase histories (R4 on the held close): $GEN_SEEDS  $(date -u +%H:%M:%S)"
python "$L/cert_reg18_box.py" gen "$OUT/r13gen" "$L/arms.txt" "$GEN_SEEDS" 5292 90 > "$OUT/r14gen.log" 2>&1
echo "gen exited $?"; tail -n 3 "$OUT/r14gen.log"
for d in "$OUT"/r13gen/*/; do echo "$d $(ls "$d" | grep -c -- '-free.npz') free, $(ls "$d" | grep -c '^names-') names"; done

echo "=== pooled level rows (D1): $D1POOL_SEEDS  $(date -u +%H:%M:%S)"
python "$L/cert_reg18_box.py" d1pool "$OUT/d1pool" "$L/arms.txt" "$D1POOL_SEEDS" --workers 90 > "$OUT/d1pool.log" 2>&1
echo "d1pool exited $?"; tail -n 3 "$OUT/d1pool.log"

echo '{"kind":"ptv21c1s"}' > "$OUT/j-ptv21c1s.json"
echo "done $(date -u +%H:%M:%S)" > "$OUT/DONE-JOBS"
exit 0
