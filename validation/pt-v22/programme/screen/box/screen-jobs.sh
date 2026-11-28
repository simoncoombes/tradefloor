#!/bin/bash
# screen-jobs.sh: the jobs file of a screen box (programme/screen/box.sh). It
# runs the plan in scripts/longrun/plan.json through screen_box.py, which
# reads and writes the result cache under $CACHE (exported by
# user-data-screen.sh) and merges the shards into $OUT. Env: REPO OUT SCR
# WORKERS CACHE; SCREEN_PARALLEL (shards at once, default 1), SCREEN_VERIFY
# (set: run every shard and compare it with the cache), SCREEN_EXPECT_HITS
# (set: every shard must be in the cache; any miss stops the box at once),
# SCREEN_ALIAS_BUILD (an earlier build digest whose entries may be read).
set -uo pipefail
: "${REPO:?}" ; : "${OUT:?}" ; : "${SCR:?}" ; : "${WORKERS:?}" ; : "${CACHE:?}"
L="$SCR/longrun"
# A grade box (boxes19.sh packs guard19.py): every input, the plan's arm and the engine checkout against the manifest,
# before any shard runs.
if [ -f "$L/guard19.py" ]; then
  python "$L/guard19.py" box "$L" --engine "$REPO" | tee "$OUT/guard19.txt"; [ "${PIPESTATUS[0]}" -eq 0 ] || exit 2
fi
echo "=== screen  $(date -u +%H:%M:%S)"
python "$L/screen_box.py" --plan "$L/plan.json" --stages "$L/stages.json" \
    --jobs "$L/r15-grade-jobs.sh" --scripts "$L" --repo "$REPO" --out "$OUT" \
    --cache "$CACHE" --workers "$WORKERS" --parallel "${SCREEN_PARALLEL:-1}" \
    ${SCREEN_VERIFY:+--verify} ${SCREEN_EXPECT_HITS:+--expect-hits} \
    ${SCREEN_ALIAS_BUILD:+--alias-build "$SCREEN_ALIAS_BUILD"} 2>&1 | tee "$OUT/screen.log"
RC=${PIPESTATUS[0]}
echo '{"kind":"screen"}' > "$OUT/j-screen.json"
echo "done $(date -u +%H:%M:%S) exit $RC" > "$OUT/DONE-JOBS"
exit "$RC"
