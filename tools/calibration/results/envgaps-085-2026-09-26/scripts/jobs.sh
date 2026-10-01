#!/bin/bash
# envgaps-085: envgaps-pt-v20's jobs re-run unchanged on pt-v20's graded arm
# as integrated for 0.8.5 (integration/0.8.5 ba3f020; garch_beta 0.7905,
# sim 72485a9f), with pt-v19 as a same-build control where the job is cheap.
# Same tools, seeds and protocols as docs080 / docs080b (2026-09-24).
# Run from the engine checkout ($REPO). At most 48 workers for 504-day or
# longer runs.
set -x
cd "$REPO"
free -g
echo '[{"label":"pt-v20","base":"pt-v20","overrides":{}},{"label":"pt-v19","base":"pt-v19","overrides":{}}]' > $OUT/macro-candidates.json
# 1. decay curve on the certified protocol (docs080's decay.py, unchanged)
( time python $SCR/decay.py pt-v20,pt-v19 60 $OUT/decay.json ) > $OUT/decay.log 2>&1
echo "decay exit $?" >> $OUT/status.txt
# 2. driven 2020-21 window, seven seeds, and notebook 09's event study
( time python $SCR/driven.py pt-v20,pt-v19 2020,101,102,103,104,105,106 $OUT/driven.json 14 ) > $OUT/driven.log 2>&1
echo "driven exit $?" >> $OUT/status.txt
# 3. macro range, five years, thirty seeds
( time python tools/calibration/macro_range.py --candidates $OUT/macro-candidates.json --seeds 30 --years 5 --workers 48 --out $OUT/macro.json ) > $OUT/macro.log 2>&1
echo "macro exit $?" >> $OUT/status.txt
# 4. memory against drift, ten years, twenty seeds
( time python tools/calibration/memory_vs_drift.py --preset pt-v20 --seeds 20 --workers 20 --out $OUT/memory-vs-drift.json ) > $OUT/memory.log 2>&1
echo "memory exit $?" >> $OUT/status.txt
# 5. the panel past two years, thirty seeds
( time python tools/calibration/long_horizon.py --preset pt-v20 --seeds 30 --workers 48 --mem-gb 110 --out $OUT/long-horizon.json ) > $OUT/long.log 2>&1
echo "long exit $?" >> $OUT/status.txt
free -g
