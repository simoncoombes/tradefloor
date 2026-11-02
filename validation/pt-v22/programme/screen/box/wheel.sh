#!/bin/bash
# wheel.sh: install the engine for this checkout from the cache's wheel, or
# build it once, gate it once and put it in the cache for every later box.
#
#   source "$SCR/longrun/wheel.sh"      (from the engine checkout, after the
#                                        pin and any engine patches are applied)
#
# Needs: CACHE (s3://BUCKET/pretium-calib/out/cache), OUT, SCR. Optional:
# SKIP_GATE_IF_KAT (the full sim digest a skipped gate vouches for, as the
# lean user-data reads it), WHEEL_LOCAL (a folder in place of S3, desk tests).
# Leaves: the venv active with tradefloor installed, KAT_SIM, PYTEST_RC, CARGO_RC.
#
# The key is the commit, the patch series, the machine and the Python
# version, so one key is one build. The record beside the wheel carries its
# sha256, the known-answer sim digest it produced and the gate result; a box
# that fetches the wheel refuses to run when its own known-answer digest is
# not the recorded one, and inherits the recorded gate result, so the gate
# runs once per commit rather than once per box. The role may put objects
# only under pretium-calib/out/ and may not list, so the wheel lives under
# out/cache/wheels/KEY/ with a fixed record name.
set -euo pipefail
: "${CACHE:?}" ; : "${OUT:?}" ; : "${SCR:?}"
s3get() { if [ -n "${WHEEL_LOCAL:-}" ]; then cp "$WHEEL_LOCAL/$1" "$2" 2>/dev/null; else aws s3 cp --only-show-errors "$CACHE/$1" "$2" 2>/dev/null; fi; }
s3put() { if [ -n "${WHEEL_LOCAL:-}" ]; then mkdir -p "$(dirname "$WHEEL_LOCAL/$2")" && cp "$1" "$WHEEL_LOCAL/$2"; else aws s3 cp --only-show-errors "$1" "$CACHE/$2"; fi; }
sha() { if command -v sha256sum > /dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
PY=${WHEEL_PY:-3.11}
PATCH_SHA=$( (cat "$SCR"/engine-patches/*.patch 2>/dev/null || true) | sha | cut -c1-64)
KEY=$(printf '%s\n' "$(git rev-parse HEAD)" "$PATCH_SHA" "$(uname -m)" "cp$PY" "maturin --release --features python" | sha | cut -c1-32)
echo "wheel key $KEY (commit $(git rev-parse HEAD), patches ${PATCH_SHA:0:12})" | tee "$OUT/wheel.txt"
uv venv --python "$PY" .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
uv pip install numpy pyarrow pytest pytest-xdist pyyaml
mkdir -p dist
REC=/tmp/wheel-record.json
if s3get "wheels/$KEY/RECORD.json" "$REC"; then
  NAME=$(python -c "import json; print(json.load(open('$REC'))['wheel'])")
  SHA=$(python -c "import json; print(json.load(open('$REC'))['sha256'])")
  s3get "wheels/$KEY/$NAME" "dist/$NAME" || { echo "REFUSED: the wheel record is in the cache but the wheel is not" | tee "$OUT/gate-status.txt"; exit 7; }
  echo "$SHA  dist/$NAME" | sha -c - || { echo "REFUSED: the cached wheel's sha256 is not its record's" | tee "$OUT/gate-status.txt"; exit 7; }
  uv pip install --no-index --find-links dist --force-reinstall tradefloor
  KAT_SIM=$(python tests/known_answer.py 2>/dev/null | awk '/^  sim /{print $2}')
  WANT=$(python -c "import json; print(json.load(open('$REC'))['kat_sim'])")
  [ "$KAT_SIM" = "$WANT" ] || { echo "REFUSED: this box's sim digest $KAT_SIM is not the wheel's recorded $WANT" | tee "$OUT/gate-status.txt"; exit 9; }
  PYTEST_RC=$(python -c "import json; print(json.load(open('$REC'))['pytest_rc'])")
  CARGO_RC=$(python -c "import json; print(json.load(open('$REC'))['cargo_rc'])")
  cp "$REC" "$OUT/wheel-record.json"
  echo "wheel from cache: $NAME, sim $KAT_SIM, gate as built: pytest=$PYTEST_RC cargo=$CARGO_RC" | tee -a "$OUT/wheel.txt"
else
  export PATH="$HOME/.cargo/bin:$PATH"
  command -v cargo > /dev/null || curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
  uv pip install maturin
  maturin build --release --out dist --features python
  uv pip install --no-index --find-links dist --force-reinstall tradefloor
  KAT_SIM=$(python tests/known_answer.py 2>/dev/null | awk '/^  sim /{print $2}')
  if [ -n "${SKIP_GATE_IF_KAT:-}" ] && [ "$KAT_SIM" = "$SKIP_GATE_IF_KAT" ]; then
    PYTEST_RC=0; CARGO_RC=0; GATE="skipped: sim digest is the gated $SKIP_GATE_IF_KAT"
  else
    set +e
    timeout 1800 python -m pytest tests/ -q -n auto > "$OUT/gate-pytest-full.txt" 2>&1; PYTEST_RC=$?
    timeout 1800 cargo test --manifest-path rust/Cargo.toml > "$OUT/gate-cargo.txt" 2>&1; CARGO_RC=$?
    set -e
    GATE="ran on this box"
  fi
  NAME=$(cd dist && ls tradefloor-*.whl | head -1)
  python - "$REC" "$NAME" "$KAT_SIM" "$PYTEST_RC" "$CARGO_RC" "$GATE" "$KEY" <<'PYEOF'
import hashlib, json, subprocess, sys
rec, name, kat, prc, crc, gate, key = sys.argv[1:]
json.dump({"key": key, "wheel": name, "sha256": hashlib.sha256(open(f"dist/{name}", "rb").read()).hexdigest(),
           "commit": subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip(),
           "kat_sim": kat, "pytest_rc": int(prc), "cargo_rc": int(crc), "gate": gate}, open(rec, "w"), indent=1)
PYEOF
  # The wheel first and its record last: a box that finds the record finds the wheel.
  s3put "dist/$NAME" "wheels/$KEY/$NAME" && s3put "$REC" "wheels/$KEY/RECORD.json" \
    || echo "wheel upload failed; later boxes will build again" | tee -a "$OUT/wheel.txt"
  cp "$REC" "$OUT/wheel-record.json"
  echo "wheel built and cached: $NAME, sim $KAT_SIM, gate $GATE pytest=$PYTEST_RC cargo=$CARGO_RC" | tee -a "$OUT/wheel.txt"
fi
if [ -n "${SKIP_GATE_IF_KAT:-}" ] && [ "$KAT_SIM" != "$SKIP_GATE_IF_KAT" ]; then
  echo "REFUSED: this build's sim digest is $KAT_SIM, not the $SKIP_GATE_IF_KAT you said was gated" | tee "$OUT/gate-status.txt"
  exit 9
fi
python -c "import numpy, pyarrow, tradefloor; print('PREFLIGHT OK', tradefloor.version())"
echo "known-answer sim: $KAT_SIM" | tee "$OUT/kat.txt"
echo "pytest=$PYTEST_RC cargo=$CARGO_RC" > "$OUT/gate-status.txt"
