#!/bin/bash
# The gate box (PLAN-pt-v22.md, gate amended 2026-10-14): beside each stage B, the enlarged samples the free gate reads
# for the arm in arm.txt:
#   scen/   driven2020.py, recession_rows.py (through graded_scenarios.py, the graded recession.yml), driven2022.py on 200 held-out seeds 9001-9200 (D2, F1, L1, S1a, S1b,
#           S2, R5, R6; the gate draws 90);
#   b9/     b9pp.py's 150 desk histories (30001-30150, 492 sessions; its 100 openings are kept but not used) and
#   open/   1,000 suite openings (openings.py, 97001-98000) for B9's start-up clause in sets of 30 + 20;
#   os2/    OS1 and OS2 on the 200 registered openings (os2.py, 201-400);
#   dump/   270 r14gen histories (8701-8970, 5292 sessions) for the gen-stage rows on 270 (the same ruling applied to the
#           gen stage, 2026-10-14; passprob.py --tp270).
#   parts.txt, when given, names the parts to run (scen b9 open os2 sf200 gen); without it every part runs.
set -euo pipefail
cd "$SCR"
# The guard: every input against MANIFEST.sha256 and the draft's registered hashes; stop before reading anything.
"$PY" guard.py check "$SCR" "$OUT/guard.txt"
part() { [ ! -f parts.txt ] || grep -qw "$1" parts.txt; }
NAME=$(head -c 200 arm.txt | cut -d@ -f1)
mkdir -p "$OUT/scen" "$OUT/b9" "$OUT/open"
# registration 19 (2026-10-22): the 2020 replay with the adopted trailing earnings leg (design ptv22/s2-fix
# programme/ptv22/s2fix/replay2020/graded-2020/, byte for byte) reading this folder; the graded recession.yml is the
# corrected one (s2fix/vix/graded-vix/).
if part scen; then
GATEBOX="$SCR" "$PY" driven2020.py "$OUT/scen/d2020.json" --arm "$(cat arm.txt)" --seeds 9001-9200 --workers "$NPROC" > "$OUT/scen/d2020.txt" 2>&1
# The recession rows on the graded recession.yml through graded_scenarios.py, the route grade 18 took (its screen
# rewrites the recession stage so); fixed 2026-10-15: the job ran recession_rows.py directly, which loads the engine's
# own packaged file (0c5c10fd on core-rates and #304), a different recession. The graded file is registration 19 item
# 8's (ad3c51d5; recession_I 9eac7e1a until 2026-10-27), as MANIFEST.sha256 and guard.py pin it.
REPO="$REPO" "$PY" graded_scenarios.py recession_rows.py "$OUT/scen/rec.json" --arm "$(cat arm.txt)" --seeds 9001-9200 --days 900 --workers "$NPROC" > "$OUT/scen/rec.txt" 2>&1
"$PY" driven2022.py "$OUT/scen/d2022.json" --arm "$(cat arm.txt)" --seeds 9001-9200 --workers "$NPROC" > "$OUT/scen/d2022.txt" 2>&1
fi
part b9 && "$PY" b9pp.py arm.txt 150 100 "$NPROC" "$OUT/b9/b9pp.json" > "$OUT/b9/b9pp.txt" 2>&1
part open && "$PY" openings.py arm.txt 97001 98000 "$NPROC" "$OUT/open/openings.json" > "$OUT/open/openings.txt" 2>&1
part os2 && "$PY" os2.py arm.txt "$NAME" 201-400 "$OUT/os2" "" "$NPROC" > "$OUT/os2.log" 2>&1
# STABILISER.md section 33: SF1's 200-seed sample on this engine (desk_sf.py through graded_scenarios.py, as stage B)
if part sf200; then mkdir -p "$OUT/sf200"
REPO="$REPO" "$PY" graded_scenarios.py desk_sf.py "$OUT/sf200/sf.json" --arm "$(cat arm.txt)" --seeds 9001-9200 --files lc,oil,prs,rate,curve,geo --workers "$NPROC" > "$OUT/sf200/sf.log" 2>&1
fi
if part gen; then
export SEED_PROTOCOL=truephase
G=/home/ec2-user/gen; mkdir -p "$G" "$OUT/dump"
"$PY" r14gen_zlb.py "$G" arm.txt 8701-8970 5292 "$(( NPROC > 4 ? NPROC - 2 : 2 ))" 2>&1 | tail -n 5 | tee "$OUT/gen.log"
for f in "$G"/*/*-free.npz; do d="$OUT/dump/$(basename "$(dirname "$f")")"; mkdir -p "$d"; cp -n "$f" "$d/"; done
N=$(ls "$OUT"/dump/*/*-free.npz | wc -l); echo "free files $N" | tee "$OUT/counts.txt"; [ "$N" -eq 270 ]
fi
ls "$OUT"/*/*.json
