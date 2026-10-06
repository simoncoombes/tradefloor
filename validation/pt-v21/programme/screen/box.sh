#!/bin/bash
# box.sh: launch a screen box for a plan written by screen.py plan. NOT RUN in
# phase 0; this is the command phase 2 uses.
#
#   bash programme/screen/box.sh RUN BRANCH PIN PLAN.json
#
# BRANCH and PIN: a pushed engine branch and commit (ENGINE_PATCHES as box.sh
# takes them is honoured). Env: TYPE (c8g.16xlarge), REGION (fleet.py's
# ladder), KAT (the full sim digest a skipped gate vouches for; it also has
# to match the wheel's record), DEADMAN_MIN (240), SCREEN_PARALLEL (1),
# SCREEN_VERIFY (set for the final verify run), SCREEN_EXPECT_HITS (set for
# an all-cache relaunch: the box stops on the first miss, and DEADMAN_MIN
# defaults to 20), SCREEN_ALIAS_BUILD (an earlier build digest to read), JOBS_REF (the ref the grade
# job and its tools are read from, default registration/fifteenth), CAP
# (dollars: refuse to launch when the plan's estimate is over it).
#
# A spot reclaim loses only the shard that was running: relaunch the same
# plan under a new run name and every finished shard is read from the cache.
set -euo pipefail
RUN=$1; BRANCH=$2; PIN=$3; PLAN=$4
if [ -n "${SCREEN_EXPECT_HITS:-}" ]; then DEADMAN_MIN=${DEADMAN_MIN:-20}; fi
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
EST=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['estimate']['dollars'])" "$PLAN")
echo "plan $PLAN: about \$$EST"
if [ -n "${CAP:-}" ] && python3 -c "import sys; sys.exit(0 if float(sys.argv[1]) > float(sys.argv[2]) else 1)" "$EST" "$CAP"; then
  echo "REFUSED: the estimate \$$EST is over the cap \$$CAP" >&2; exit 1
fi
STAGE="$(mktemp -d)"; TGZ="$STAGE/$RUN-scripts.tgz"
python3 "$HERE/box/assemble.py" "$STAGE/x" --ref "${JOBS_REF:-registration/fifteenth}" --plan "$PLAN" > /dev/null
cp "$HERE/box/screen-jobs.sh" "$HERE/box/wheel.sh" "$STAGE/x/scripts/longrun/"
if [ -n "${ENGINE_PATCHES:-}" ]; then
  mkdir -p "$STAGE/x/scripts/engine-patches"
  cp "$ENGINE_PATCHES"/*.patch "$STAGE/x/scripts/engine-patches/"
fi
if grep -l $'\r' "$STAGE"/x/scripts/longrun/*.sh; then echo "CRLF in a jobs file" >&2; exit 1; fi
# COPYFILE_DISABLE: macOS tar otherwise packs each file's extended attributes
# as a "._name" file, which Linux unpacks beside it (seen on s1a, 2026-10-04).
COPYFILE_DISABLE=1 tar -czf "$TGZ" -C "$STAGE/x" scripts
cd "$ROOT"
# fleet.py needs boto3: this checkout's .venv, else the ptv20 worktree's.
FLEET_PY=${FLEET_PY:-$( [ -x "$ROOT/.venv/bin/python" ] && echo "$ROOT/.venv/bin/python" || echo "$HOME/Dev/tfd-wt-ptv20/.venv/bin/python")}
"$FLEET_PY" fleet.py upload --file "$TGZ" --key "in/$RUN-scripts.tgz" > /dev/null
VARS=(--var "BRANCH=$BRANCH" --var "PIN=$PIN" --var "JOBS=longrun/screen-jobs.sh"
      --var "SCRIPTS_TGZ=in/$RUN-scripts.tgz"
      --var "EXTRA=WORKERS=90 SCREEN_PARALLEL=${SCREEN_PARALLEL:-1}${SCREEN_VERIFY:+ SCREEN_VERIFY=1}${SCREEN_EXPECT_HITS:+ SCREEN_EXPECT_HITS=1}${SCREEN_ALIAS_BUILD:+ SCREEN_ALIAS_BUILD=$SCREEN_ALIAS_BUILD}"
      --var "FULL_DEPS=1" --var "DEADMAN_MIN=${DEADMAN_MIN:-240}" --var "RUN=$RUN")
[ -n "${KAT:-}" ] && VARS+=(--var "SKIP_GATE_IF_KAT=$KAT")
"$FLEET_PY" fleet.py launch --run "$RUN" --type "${TYPE:-c8g.16xlarge}" ${REGION:+--region $REGION} \
    --user-data programme/scripts/user-data-screen.sh "${VARS[@]}"
rm -rf "$STAGE"
