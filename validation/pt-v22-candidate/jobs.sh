#!/bin/bash
# The pt-v22 candidate's grade: pt-v21 with lossmaker_rate_sensitivity 11,
# paired against pt-v21 by name, on exam seeds = the registered held-out
# seeds + 270000 (no recorded run used them), on the engine at feat/lossmaker-duration.
set -uo pipefail
REPO=$HOME/Dev/tf-lossmaker
L=$HOME/Dev/tf-lossmaker-grade/c1/scripts/longrun
OUT=$HOME/Dev/tf-lossmaker-grade/g1/out
W=${W:-6}
source "$REPO/.venv/bin/activate"
export REPO OUT
cd "$REPO"
git rev-parse HEAD > "$OUT/commit.txt"
python tests/known_answer.py > "$OUT/known-answer.txt" 2>&1
printf 'pt-v21@pt-v21:\nptv22c@pt-v21:lossmaker_rate_sensitivity=11\n' > "$OUT/arms.txt"
ARMS=(--arm "pt-v21@pt-v21:" --arm "ptv22c@pt-v21:lossmaker_rate_sensitivity=11")
SEEDS90=270101-270130,270401-270430,270701-270730
t() { echo "=== $*  $(date +%H:%M:%S)"; }

t "calibration held out"
python tools/calibration/lossmaker_duration.py measure --seeds 9101-9130 --roster 2022 --grid 0,11 \
  --workers $W --out "$OUT/heldout-roster2022.json" > "$OUT/heldout-roster2022.log" 2>&1
python tools/calibration/lossmaker_duration.py measure --seeds 9101-9130 --roster 4022 --grid 0,11 \
  --workers $W --out "$OUT/heldout-roster4022.json" > "$OUT/heldout-roster4022.log" 2>&1

t "causes in rate_shock"
python tools/calibration/lossmaker_causes.py "$OUT/causes-rate-shock.json" --seeds 270301-270330 --k 11 \
  --workers $W > "$OUT/causes-rate-shock.log" 2>&1

t "driven2022 R5 R6"
python "$L/driven2022.py" "$OUT/driven2022.json" "${ARMS[@]}" --seeds $SEEDS90 --workers $W > "$OUT/driven2022.log" 2>&1

t "scenarios"
python "$L/scenario_size.py" "$OUT/scenario-pt-v21.json" --model pt-v21 --seeds 270301-270330 --workers $W \
  --scenarios rate_shock,curve_shock > "$OUT/scenario-pt-v21.log" 2>&1
python "$L/scenario_size.py" "$OUT/scenario-ptv22c.json" --model pt-v21 --dials lossmaker_rate_sensitivity=11 \
  --seeds 270301-270330 --workers $W --scenarios rate_shock,curve_shock > "$OUT/scenario-ptv22c.log" 2>&1

t "cert cells"
export VIXLEVEL_BASE=pt-v21
pass() { local name=$1 days=$2; shift 2
  python "$L/certrun_box.py" --workers $W --days "$days" "$@" > "$BESTOF_OUT/run-$name-$days.log" 2>&1; }
export BESTOF_OUT="$OUT/cert"; mkdir -p "$BESTOF_OUT"
pass cert 252 --seeds 270101-270130 --lever-seeds 270101-270130 "${ARMS[@]}"
pass cert 504 --seeds 270101-270130 --no-lever "${ARMS[@]}"
export BESTOF_OUT="$OUT/heldseeds"; mkdir -p "$BESTOF_OUT"
NO_VARY=1 pass heldseeds 252 --seeds 270001-270030 --no-lever "${ARMS[@]}"
export BESTOF_OUT="$OUT/heldu"; mkdir -p "$BESTOF_OUT"
HELD_N=60 HELD_SEED=909 NO_VARY=1 pass heldu 252 --seeds 270101-270130 --no-lever "${ARMS[@]}"
t "crisis extension"
python "$L/crisisext_box.py" --out "$OUT" --workers $W > "$OUT/crisisext.log" 2>&1

t "long run"
python "$L/longrun.py" measure --arms-file "$OUT/arms.txt" --base pt-v21 --seed-list $SEEDS90 --years 21 \
  --workers $W --engine "$REPO" --out "$OUT/longrun" > "$OUT/longrun-measure.log" 2>&1
python "$L/longrun.py" report "$OUT/longrun" --out "$OUT/longrun-report.txt" > "$OUT/longrun-report.log" 2>&1
t "done"
echo done > "$OUT/DONE"
