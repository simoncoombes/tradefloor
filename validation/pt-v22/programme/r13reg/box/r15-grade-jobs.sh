#!/bin/bash
# r15-grade-jobs.sh -- the r15 screen (engine sim/r15 7432d3a; the packaged recession.yml is recession_I, so
# desk_sf reads rec_pkg with recession_I's holds and the separate recession_I job is dropped; the edge eval adds
# the constant 1.35x agent for the exposure-matched reading; the thirteenth registration grades each levered
# agent against its own mean gross exposure instead, evalem, and keeps const135 as reported). Below, the r14 screen's header (engine sim/r14 e2e2d21): the registered grade on
# held-out seeds only (the r13 screen's r13s-grade-jobs.sh: long run 201-230,501-530,801-830;
# cert 201-230 / 501-530 / 801-830; D1 extension from 2001; edge and c4a from 201; xsec, driven,
# R7, recession on held-out seeds), then the leak repros and every realism row's recording on
# every arm (r14 extras, at the end). Thirteenth registration: every seed block reads a variable (the seed plan
# below, seedplan.py); unset, the held-out values above, so the screen is unchanged.
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
# ---- the seed plan (thirteenth registration). Every seed block below reads one of these variables. Unset, each
# is today's held-out value, so a screen launched without them runs what it ran before. The grade sets them to the
# registered exam seeds (r13reg/grade-seeds.json) and sets REGISTERED_GRADE to the registration commit; seedplan.py
# (shipped beside this file) refuses the plan unless it is held out throughout or exactly the registered set, and
# refuses the old exam seeds (101-190, 401-430, 701-730) always. DRY_RUN=1 prints, per protocol, the seeds this job
# would run, and the verdict, and stops before anything runs (no REPO/OUT/SCR needed).
export LONGRUN_SEED_LIST="${LONGRUN_SEED_LIST:-201-230,501-530,801-830}"  # long run (longrun-jobs.sh)
export GEN_SEEDS="${GEN_SEEDS:-201-290,501-590,801-890}"      # true-phase histories (r14gen.py), 270
export EXPLOITS_LEAK_FIRST="${EXPLOITS_LEAK_FIRST:-201}"       # leak macro rules: 24 of GEN_SEEDS (exploits.py, desk)
export CERT_SEEDS="${CERT_SEEDS:-201-230}"                     # cert panel_252 (and its lever) and panel_504
export HELDSEEDS_SEEDS="${HELDSEEDS_SEEDS:-501-530}"           # cert heldout_seeds
export HELDU_SEEDS="${HELDU_SEEDS:-801-830}"                   # cert heldout_universe
export CRISISEXT_FIRST="${CRISISEXT_FIRST:-2001}"              # D1 extension, 9 blocks of 30 from here
export EDGE_FIRST_SEED="${EDGE_FIRST_SEED:-201}"               # C3, 10 seeds
export C4A_FIRST_SEED="${C4A_FIRST_SEED:-201}"                 # C4a, 8 seeds
export XSEC_SEEDS="${XSEC_SEEDS:-201-230,501-530,801-830}"     # xsec, driven 2020, driven 2022
export R7_SEEDS="${R7_SEEDS:-201-230,501-530,801-830}"         # r7_event: R7a on 90 seeds (owner decision 13)
export R7PRE_SEEDS="${R7PRE_SEEDS:-201-230,501-530,801-830}"   # r7_event_pre: the same 90 (owner decision 13)
export R7EVAL_SEEDS="${R7EVAL_SEEDS:-201-230}"                 # r7_eval: R7b, 30 histories (its 20-of-30 bound)
export REC_SEEDS="${REC_SEEDS:-201-380}"                       # recession_rows, 180 (registration 19, item 5)
export SF_REC_SEEDS="${SF_REC_SEEDS:-201-230}"                 # SF desk, packaged recession
export SF_SEEDS="${SF_SEEDS:-201-400,431-446}"                 # SF desk, the other six files, 216 (registration 19, item 3; 401-430 are old exam seeds)
export MC_RUN_SEEDS="${MC_RUN_SEEDS:-2501-2530}"               # metaorder runs
export MC_TRIP_SEEDS="${MC_TRIP_SEEDS:-2531,2532,2534}"        # metaorder round trips
export MC_MARK_SEED="${MC_MARK_SEED:-2533}"                    # mark the close
export AO_FIRST_SEED="${AO_FIRST_SEED:-3001}"                  # arrival order, 30 seeds (e7_arm.py)
export PH1_SEEDS="${PH1_SEEDS:-2001-2005}"                     # pre-history PH1
export PH3_SEEDS="${PH3_SEEDS:-2001-2030}"                     # pre-history PH3
export EDGEGEN_SEEDS="${EDGEGEN_SEEDS:-2001-2030}"             # stock-level exploit repros, recorded
export EDGEEVAL_SEEDS="${EDGEEVAL_SEEDS:-3001-3020}"           # tf.evaluate agents, eval and evalem
export AUDIT_SEED_SHORT="${AUDIT_SEED_SHORT:-2101}"            # regression probes (regr.tgz), short: 30 from here
export AUDIT_SEED_VAR="${AUDIT_SEED_VAR:-2201}"                # var: 30
export AUDIT_SEED_LONG="${AUDIT_SEED_LONG:-2301}"              # long: 8
export AUDIT_SEED_PROBE="${AUDIT_SEED_PROBE:-2401}"            # impact, mm_thin, pins: 8 at most
export STAB_FIRST_SEED="${STAB_FIRST_SEED:-201}"               # century stability, 12 x 100 years
export PROBE_SEEDS="${PROBE_SEEDS:-201-236}"                   # rate-index timing probe (L-rate), 36
export PTV21_SEEDS="${PTV21_SEEDS:-201-290}"                   # sixteenth registration (90 since the eighteenth): the ptv21 stage (ON1, U, O, SK rows); owner decisions 10-12 follow
export D1POOL_SEEDS="${D1POOL_SEEDS:-201-400,431-590}"         # D1 tail and drift rows, 360 cert seeds (d1pool_box.py)
export AOPOOL_FIRST="${AOPOOL_FIRST:-43001}"                   # AO1 book coin, AO3/AO4 luck removed, 400 seeds
export PTV22_SEEDS="${PTV22_SEEDS:-9001-9270}"                 # nineteenth registration (pt-v22): the ptv22fut, ptv22chain, ptv22names and ptv22traded stages, 270
export LONGRUN_POOL_SEED_LIST="${LONGRUN_POOL_SEED_LIST:-50201-50230,50501-50530,50801-50830,60201-60230,60501-60530,60801-60830}"  # owner decisions 14 and 15 (2026-09-29): C10c and PH5 on 270 long-run histories, LONGRUN_SEED_LIST's 90 and these 180
export PTV22_MP_SEEDS="${PTV22_MP_SEEDS:-9001-9030}"           # nineteenth registration: the ptv22traded stage's metaorder and push parts (IF4, XD3), 30
# The known answers beyond the sim digest (the box's user-data checks that one, SKIP_GATE_IF_KAT): digests.py (shipped beside
# this file) computes the built engine's presets digest and each arm's dial fingerprint and compares them with the registration's
# (PRESETS_DIGEST and ARM_FINGERPRINTS there). With REGISTERED_GRADE set (the grade) a mismatch stops the job before the first
# grading job; otherwise it is printed. DRY_RUN=1 with REPO and its python first on PATH checks ARMS_FILE (default arms.txt here).
digest_gate() {  # $1 engine checkout, then the arms files
  local repo=$1; shift
  python "$L/digests.py" verify "$@" --repo "$repo" ${REGISTERED_GRADE:+--enforce} ${OUT:+--json "$OUT/digests.json"}
}
if [ -n "${DRY_RUN:-}" ]; then
  L="${SCR:+$SCR/longrun}"; L="${L:-$(cd "$(dirname "$0")" && pwd)}"
  "$(command -v python3 || command -v python)" "$L/seedplan.py" show || exit $?
  if [ -n "${REPO:-}" ]; then
    echo "=== digests (dry run, engine $REPO)"
    digest_gate "$REPO" "${ARMS_FILE:-$L/arms.txt}" || exit $?
  fi
  exit 0
fi
: "${REPO:?}" ; : "${OUT:?}" ; : "${SCR:?}" ; : "${WORKERS:?}"
L="$SCR/longrun"
# a seed list ("2531,2532,2534", "2001-2030") as words, for the loops below
seedlist() { python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import seedplan; print(*seedplan.parse(sys.argv[2]))' "$L" "$1"; }
cd "$REPO"
git rev-parse HEAD > "$OUT/commit.txt"
python "$L/seedplan.py" verify --json "$OUT/seedplan.json" 2>&1 | tee "$OUT/seedplan.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "REFUSED: the seed plan"; echo "done" > "$OUT/DONE-JOBS"; exit 1; }
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
CERTF=(); [ -f "$L/cert-arms.txt" ] && CERTF=("$L/cert-arms.txt")
digest_gate "$REPO" "$L/arms.txt" ${CERTF[@]+"${CERTF[@]}"} 2>&1 | tee "$OUT/digests.txt"
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "REFUSED: the presets digest or an arm's fingerprint"; echo "done" > "$OUT/DONE-JOBS"; exit 1; }

ARMS=()
while IFS= read -r line; do
  line="${line%%#*}"; [ -z "${line// }" ] && continue
  ARMS+=(--arm "$line")
done < "$L/arms.txt"

# 1: long run and certification cells
bash "$L/r14-box-jobs.sh" || echo "box-jobs exited $?"
rm -f "$OUT/DONE-JOBS"

# 2: C3
echo "=== edge  $(date -u +%H:%M:%S)"
python "$L/edge.py" "${ARMS[@]}" --base pt-v19 --seeds 10 --first-seed "$EDGE_FIRST_SEED" --days 300 \
    --workers 40 --out "$OUT/edge.json" 2>&1 | tee "$OUT/edge.log" | tail -n 8

# 3: C4a, C4b
echo "=== c4  $(date -u +%H:%M:%S)"
python "$L/c4.py" "${ARMS[@]}" --part a --seeds 8 --first-seed "$C4A_FIRST_SEED" --workers 48 --engine-commit "$(git rev-parse HEAD)" \
    --out "$OUT/c4a.json" 2>&1 | tee "$OUT/c4a.log" | tail -n 8
python "$L/c4.py" "${ARMS[@]}" --part b --workers 48 --engine-commit "$(git rev-parse HEAD)" \
    --out "$OUT/c4b.json" 2>&1 | tee "$OUT/c4b.log" | tail -n 12

# 4: C5-C8
echo "=== xsec  $(date -u +%H:%M:%S)"
python "$L/grade_xsec.py" "$OUT/xsec.json" "${ARMS[@]}" --seeds "$XSEC_SEEDS" --days 2660 --workers 90 \
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
python "$L/driven2020.py" "$OUT/driven2020.json" "${ARMS[@]}" --seeds "$XSEC_SEEDS" --workers 90 \
    2>&1 | tee "$OUT/driven2020.log" | tail -n 4

# 5d: C10, no public macro signal predicts returns (eleventh registration)
echo "=== c10  $(date -u +%H:%M:%S)"
ARMNAMES=$(grep -v '^#' "$L/arms.txt" | sed '/^ *$/d' | cut -d@ -f1 | cut -d: -f1 | paste -sd, -)
# The long run records the fields as the engine publishes them (the phase a year
# late, GDP quarterly), so no --lag: the published record is what is graded.
python "$L/c10.py" "$OUT" --arms "$ARMNAMES" --out "$OUT/c10.json" 2>&1 | tee "$OUT/c10.log"

# 5c: R5 and R6, the driven 2022 market (eighth registration)
echo "=== driven2022  $(date -u +%H:%M:%S)"
python "$L/driven2022.py" "$OUT/driven2022.json" "${ARMS[@]}" --seeds "$XSEC_SEEDS" --workers 90 \
    2>&1 | tee "$OUT/driven2022.log" | tail -n 6

# 5e: R7a and R7b, no drift after a published policy-rate decision (twelfth registration)
echo "=== r7  $(date -u +%H:%M:%S)"
python "$L/r7_event.py" "$OUT/r7-event.json" "${ARMS[@]}" --seeds "$R7_SEEDS" --years 21 --workers 90 \
    2>&1 | tee "$OUT/r7-event.log" | tail -n 4
python "$L/r7_eval.py" "$OUT/r7-eval.json" "${ARMS[@]}" --seeds "$R7EVAL_SEEDS" --years 10 --workers 90 \
    2>&1 | tee "$OUT/r7-eval.log" | tail -n 4

# 5f: S1a, S1b and S2, the packaged recession recovers (twelfth registration): the
# recession.yml inside the engine this box built, not a copy
echo "=== recession  $(date -u +%H:%M:%S)"
python "$L/recession_rows.py" "$OUT/recession.json" "${ARMS[@]}" --seeds "$REC_SEEDS" --days 900 --workers 90 \
    2>&1 | tee "$OUT/recession.log" | tail -n 4

# 5g: V1, the long-horizon variance ratio: graded on the desk by v1.py from
# $OUT/longrun/*/SEED-free.npz, writing v1.json for criteria.py --v1.

# 6: pt-v20's preset panel, for its record (skipped on a grid)
[ -n "${GRID:-}" ] || {
echo "=== preset panel, pt-v20  $(date -u +%H:%M:%S)"
python tools/calibration/preset_panel.py --only pt-v20 --workers 48 --out "$OUT/preset-panel.json" 2>&1 \
  | tee "$OUT/preset-panel.log" | tail -n 12
}


# ===== r14 extras: the audit's leak repros (the r13 screen's) and every realism row's recording,
# on every arm, held-out seeds only.
armlines() { grep -v '^#' "$L/arms.txt" | sed '/^ *$/d'; }
echo "=== r14 extras: stab100 (bg, 48 workers) + free histories r14gen (90 seeds, 44 workers)  $(date -u +%H:%M:%S)"
STAB_ARMS=()
while IFS= read -r line; do
  head="${line%%:*}"; body="${line#*:}"
  STAB_ARMS+=("${head%%@*}:${body}")
done < <(armlines)
( python "$L/stab60.py" "$OUT/stab100" 48 100 "$STAB_FIRST_SEED" 12 "${STAB_ARMS[@]}" > "$OUT/stab100.log" 2>&1
  python "$L/stabrep.py" "$OUT/stab100" > "$OUT/stab100-report.txt" 2>&1; echo "stab100 done $(date -u +%H:%M:%S)" >> "$OUT/stab100.log" ) &
STAB_PID=$!
python "$L/r14gen.py" "$OUT/r13gen" "$L/arms.txt" "$GEN_SEEDS" 5292 44 > "$OUT/r14gen.log" 2>&1; tail -n 3 "$OUT/r14gen.log"
wait $STAB_PID; tail -n 2 "$OUT/stab100.log"

echo "=== r14 extras: rate-index timing probe  $(date -u +%H:%M:%S)"
python "$L/probe_all.py" "$OUT/probe.json" "$L/arms.txt" "$PROBE_SEEDS" 252 90 2>&1 | tee "$OUT/probe.log" | tail -n 30
echo "=== r14 extras: scenario front-run desk  $(date -u +%H:%M:%S)"
SF_ARMS=()
while IFS= read -r line; do SF_ARMS+=(--arm "$line"); done < <(armlines)
python "$L/desk_sf.py" "$OUT/sf-rec.json" "${SF_ARMS[@]}" --seeds "$SF_REC_SEEDS" --files rec_pkg --workers 90 \
  2>&1 | tee "$OUT/sf-rec.log" | tail -n 30
python "$L/desk_sf.py" "$OUT/sf.json" "${SF_ARMS[@]}" --seeds "$SF_SEEDS" --files lc,oil,prs,rate,curve,geo --workers 90 \
  2>&1 | tee "$OUT/sf.log" | tail -n 40

echo "=== r14 extras: R7a with the baseline from the last close  $(date -u +%H:%M:%S)"
python "$L/r7_event_pre.py" "$OUT/r7pre.json" "${ARMS[@]}" --seeds "$R7PRE_SEEDS" --years 21 --workers 90 \
    2>&1 | tee "$OUT/r7pre.log" | tail -n 6

echo "=== r14 extras: metaorder rows (Q1-Q9), round trips, mark, per arm  $(date -u +%H:%M:%S)"
T="$REPO/tools/calibration/metaorder_curve.py"
mkdir -p "$OUT/mc" "$OUT/trips" "$OUT/mark" "$OUT/c9"
: > "$OUT/mc-joblist"
while IFS= read -r line; do
  name="${line%%@*}"; over="${line#*:}"; [ -z "$over" ] && over="-"
  for s in $(seedlist "$MC_RUN_SEEDS"); do echo "run $name $s $over" >> "$OUT/mc-joblist"; done
  for s in $(seedlist "$MC_TRIP_SEEDS"); do echo "trips $name $s $over" >> "$OUT/mc-joblist"; done
  for s in $(seedlist "$MC_MARK_SEED"); do echo "mark $name $s $over" >> "$OUT/mc-joblist"; done
done < <(armlines)
sort -k1,1r "$OUT/mc-joblist" -o "$OUT/mc-joblist"
export T OUT
cat "$OUT/mc-joblist" | xargs -P 90 -L 1 sh -c '
  cmd=$0; name=$1; s=$2; over=$3; [ "$over" = "-" ] && over=""
  case $cmd in
    run)   python "$T" run "$s" --names 12 --over "$over" --out "$OUT/mc/${name}_$s.json" > "$OUT/mc/${name}_$s.txt" 2> "$OUT/mc/${name}_$s.log" ;;
    trips) python "$T" trips "$s" --over "$over" --out "$OUT/trips/${name}_$s.json" > "$OUT/trips/${name}_$s.txt" 2>&1 ;;
    mark)  python "$T" mark "$s" --over "$over" --out "$OUT/mark/${name}_$s.json" > "$OUT/mark/${name}_$s.txt" 2>&1 ;;
  esac'
while IFS= read -r line; do
  name="${line%%@*}"; over="${line#*:}"
  python "$T" report "$OUT"/mc/"${name}"_*.json --json "$OUT/mc-report-$name.json" > "$OUT/mc-report-$name.txt" 2>&1
  head -11 "$OUT/mc-report-$name.txt" | sed "s/^/$name /"
  # C9 with the arm's own dials: impact_curve.py through a from_preset shim
  python - "$over" "$OUT/c9/$name.json" > "$OUT/c9/$name.txt" 2>&1 <<'PYEOF' &
import sys, tradefloor as tf
sys.path.insert(0, "tools/calibration")
over = {k: float(v) for k, v in (x.split("=") for x in sys.argv[1].split(",") if x)}
orig = tf.ModelParams
class Shim:
    @staticmethod
    def from_preset(name, **kw):
        return orig.from_preset(name, **{**kw, **over})
tf.ModelParams = Shim
import impact_curve
impact_curve.tf = tf
sys.exit(impact_curve.main(["--base", "pt-v20", "--out", sys.argv[2]]))
PYEOF
done < <(armlines)
wait
grep -H "^fit\|exponent" "$OUT"/c9/*.txt | head -20

echo "=== r14 extras: arrival-order rows AO1-AO4 ($AO_FIRST_SEED, 30 seeds), prehistory PH1/PH3  $(date -u +%H:%M:%S)"
mkdir -p "$OUT/ao" "$OUT/ph" "$OUT/aoph-jobs"
# one small script per job (the dials string is long, so no xargs -I)
k=0
while IFS= read -r line; do
  name="${line%%@*}"; over="${line#*:}"
  for q in q1 q2 q3 q4; do
    k=$((k+1)); echo "python $L/e7_arm.py $AO_FIRST_SEED $name $q $OUT/ao '$over' > $OUT/ao/$name-$q.log 2>&1" > "$OUT/aoph-jobs/$k.sh"
  done
  for s in $(seedlist "$PH1_SEEDS"); do
    k=$((k+1)); echo "PH_DIALS='$over' python $L/ph_rows.py ph1 $s > $OUT/ph/ph1-$name-$s.json 2> $OUT/ph/ph1-$name-$s.err" > "$OUT/aoph-jobs/$k.sh"
  done
  for s in $(seedlist "$PH3_SEEDS"); do
    for hd in 0 252; do
      k=$((k+1)); echo "PH_DIALS='$over' python $L/ph_rows.py ph3 $s $hd > $OUT/ph/ph3-$name-$s-$hd.json 2> $OUT/ph/ph3-$name-$s-$hd.err" > "$OUT/aoph-jobs/$k.sh"
    done
  done
done < <(armlines)
ls "$OUT"/aoph-jobs/*.sh | xargs -P 90 -n 1 sh
ls "$OUT/ao" | head -30

echo "=== pooled rows (owner decisions 10-12): D1 tail and drift ($D1POOL_SEEDS), AO1/AO3/AO4 ($AOPOOL_FIRST, 400 seeds)  $(date -u +%H:%M:%S)"
# D1 (decisions 11 and 12): the certification's varying-roster protocol on the pooled seed set, every arm, one file
# per arm under d1pool/; certgrade_box_hr.py grades index_tail_dn3_pct and index_drift_pct on it.
python "$L/d1pool_box.py" "$OUT/d1pool" "$L/arms.txt" --workers 90 > "$OUT/d1pool.log" 2>&1; tail -n 6 "$OUT/d1pool.log"
# AO (decision 10): the book coin over 400 seeds x 420 days x 6 steps (ao_coin.py), and q2/q3 over the same 400
# seeds in blocks of 20 (e7_arm.py records each seed's arrival luck); the 30-seed counts above are reported only.
mkdir -p "$OUT/ao-pool" "$OUT/aopool-jobs"
k=0
while IFS= read -r line; do
  name="${line%%@*}"; over="${line#*:}"
  k=$((k+1)); echo "python $L/ao_coin.py $OUT/ao-pool/coin-$name.json $name '$over' --workers 4 > $OUT/ao-pool/coin-$name.log 2>&1" > "$OUT/aopool-jobs/$k.sh"
  for ((b = 0; b < 20; b++)); do
    start=$((AOPOOL_FIRST + 20 * b))
    for q in q2 q3; do
      k=$((k+1)); echo "AO_N=20 AO_PROTOCOL=aopool python $L/e7_arm.py $start $name $q $OUT/ao-pool '$over' > $OUT/ao-pool/$name-$start-$q.log 2>&1" > "$OUT/aopool-jobs/$k.sh"
    done
  done
done < <(armlines)
ls "$OUT"/aopool-jobs/*.sh | xargs -P 90 -n 1 sh
grep -h "^ao_coin" "$OUT"/ao-pool/coin-*.log; ls "$OUT/ao-pool" | grep -c json

echo "=== pooled long run (owner decisions 14 and 15): $LONGRUN_POOL_SEED_LIST  $(date -u +%H:%M:%S)"
# the long run's instrument (longrun.py measure, as longrun-jobs.sh runs it) on two more 90-history blocks, into
# longrun-pool/ARM; grade_all.py and margins.py grade C10c and PH5 on these with longrun/ARM's 90 (270 histories)
python "$L/longrun.py" measure --arms-file "$L/arms.txt" --base "${LONGRUN_BASE:-pt-v19}" \
    --seed-list "$LONGRUN_POOL_SEED_LIST" --years "${LONGRUN_YEARS:-21}" --workers "$WORKERS" --engine "$REPO" \
    --out "$OUT/longrun-pool" > "$OUT/longrun-pool.log" 2>&1
for d in "$OUT"/longrun-pool/*/; do echo "$d $(ls "$d" | grep -c -- '-free.npz') free histories"; done

echo "=== r14 extras: the edge audit's stock-level histories and harness timing rules  $(date -u +%H:%M:%S)"
while IFS= read -r line; do
  name="${line%%@*}"; over="${line#*:}"
  EDGE_DIALS="$over" python "$L/edgegen_arm.py" gen --seeds "$EDGEGEN_SEEDS" --years 21 --roster vary --workers 30 \
      --out "$OUT/edge-$name" > "$OUT/edge-$name-gen.log" 2>&1 &
done < <(armlines)
wait
while IFS= read -r line; do
  name="${line%%@*}"; over="${line#*:}"
  EDGE_DIALS="$over" python "$L/edgegen_arm.py" eval --seeds "$EDGEEVAL_SEEDS" --days 5292 --workers 30 \
      --agents bh,fedcut63,pubpeakcon,fg40,unemp126,const135 --cash-interest 1 --out "$OUT/edge-$name" > "$OUT/edge-$name-eval.log" 2>&1 &
done < <(armlines)
wait
# the thirteenth registration's exploit gate: each levered agent against a constant position at its own mean gross
# exposure on that seed (eval records mean_gross; evalem runs em_AGENT at it). const135 above is reported only.
while IFS= read -r line; do
  name="${line%%@*}"; over="${line#*:}"
  EDGE_DIALS="$over" python "$L/edgegen_arm.py" evalem --seeds "$EDGEEVAL_SEEDS" --days 5292 --workers 30 \
      --agents fedcut63,pubpeakcon,fg40,unemp126 --cash-interest 1 --out "$OUT/edge-$name" > "$OUT/edge-$name-evalem.log" 2>&1 &
done < <(armlines)
wait
echo "=== r14 extras: the regression audit's probes, per arm  $(date -u +%H:%M:%S)"
python -c "import pandas" 2>/dev/null || (uv pip install pandas || pip install pandas) > "$OUT/pip-pandas.txt" 2>&1
while IFS= read -r line; do
  name="${line%%@*}"; over="${line#*:}"
  R="$OUT/regr-$name"; mkdir -p "$R/src"; tar -xzf "$L/regr.tgz" -C "$R/src"
  ( cd "$R/src" && export AUDIT_DIALS="$over" AUDIT_COMMIT="r15-$name" AUDIT_PROCS=30 AUDIT_SHORT_N=30 AUDIT_LONG_N=8 AUDIT_LONG_DAYS=5292
    timeout 1500 python gen_all.py short var long > "$R/gen.log" 2>&1
    for s in short var long; do timeout 900 python strategies.py $s > "$R/strategies_$s.txt" 2>&1; done
    timeout 900 python intraday.py short > "$R/intraday.txt" 2>&1
    timeout 900 python stale_open.py > "$R/stale_open.txt" 2>&1
    timeout 900 python big_days.py > "$R/big_days.txt" 2>&1
    timeout 1200 python probe_impact.py 3 > "$R/impact.txt" 2>&1
    AUDIT_PROCS=8 timeout 1200 python run_mm_thin.py 8 10 5 > "$R/mm_thin.txt" 2>&1
    AUDIT_PIN_SEEDS=3 AUDIT_SKIP_VOLN=1 timeout 1200 python probe_pins_paired.py > "$R/pins.txt" 2>&1
    timeout 600 python probe_bond_stale.py > "$R/bond_stale.txt" 2>&1
    timeout 600 python probe_invariants.py > "$R/invariants.txt" 2>&1
    # the cached markets stay: they are inside $OUT (regr-ARM/src/cache), so they are uploaded, and the desk grader
    # regr_em.py reads vol_target_15's markets from them (thirteenth registration)
    echo "regr $name done $(date -u +%H:%M:%S)" ) &
done < <(armlines)
wait
echo "=== r14 extras done  $(date -u +%H:%M:%S)"

echo '{"kind":"ptv20-grade"}' > "$OUT/j-ptv20-grade.json"
echo "done $(date -u +%H:%M:%S)" > "$OUT/DONE-JOBS"
exit 0
