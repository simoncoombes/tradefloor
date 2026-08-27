#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd18-breaker
# `dev`, and the run needs eb44efa or later: gate_batch and macro_range only
# learned --seed-start there. Without it both silently re-measure on the
# calibration seeds and the run confirms nothing while looking identical.
# Verified with `git show origin/dev:...` before launch, runbook trap 16.
BRANCH=dev
# 64, not 96. remeasure uses a THREAD pool, not processes, so the ceiling is
# how much of the engine runs with the GIL released rather than the core
# count. 64 is eight times the laptop's 8 with headroom left for the serial
# wall-clock groups that follow.
# 40, not 190. driven_buckets holds a full pyarrow bar table per job -- 504
# days x 40 instruments -- where a panel job holds a summary, so it cannot
# use the worker count the gate launchers inherit. At 190 on a 96-vCPU box
# the pool died with BrokenProcessPool partway through the first candidate
# and the run delivered nothing. This is the second resource ceiling in this
# programme reached by copying a launcher rather than sizing one.
WORKERS=40

dnf -y install gcc git tar gzip python3.11 python3.11-devel awscli-2

# S3 PREFLIGHT before anything expensive. Trap 6: a launch without
# --iam-instance-profile computes perfectly and delivers nothing.
echo "preflight $(date -u)" > /tmp/PREFLIGHT-S3
if ! aws s3 cp /tmp/PREFLIGHT-S3 "$BUCKET/PREFLIGHT-S3"; then
  echo "ABORTING: cannot write to $BUCKET. Almost certainly a missing"
  echo "--iam-instance-profile Name=pretium-calib-profile on run-instances."
  shutdown -h now
  exit 1
fi

# Stream while the run is going. `cp`, never `sync`: the role has
# PutObject/GetObject and no ListBucket, so sync fails silently (trap 7).
cat > /home/ec2-user/stream.sh <<'STREAM'
#!/bin/bash
BUCKET="$1"
while true; do
  date -u +"%Y-%m-%dT%H:%M:%SZ" > /tmp/stream-alive
  aws s3 cp /tmp/stream-alive "$BUCKET/STREAM-ALIVE" 2>&1 || echo "STREAM UPLOAD FAILED"
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
  sleep 45
done
STREAM
chown ec2-user:ec2-user /home/ec2-user/stream.sh
chmod +x /home/ec2-user/stream.sh
setsid nohup /home/ec2-user/stream.sh "$BUCKET" >/var/log/pretium-stream.log 2>&1 < /dev/null &

cat > /home/ec2-user/candidates.json <<'CANDS'
[
 {
  "label": "brk-0.25",
  "base": "pt-v12",
  "overrides": {}
 },
 {
  "label": "brk-0.40",
  "base": "pt-v12",
  "overrides": {
   "price_breaker_fraction": 0.4
  }
 },
 {
  "label": "brk-0.60",
  "base": "pt-v12",
  "overrides": {
   "price_breaker_fraction": 0.6
  }
 },
 {
  "label": "brk-0.95",
  "base": "pt-v12",
  "overrides": {
   "price_breaker_fraction": 0.95
  }
 }
]
CANDS
chown ec2-user:ec2-user /home/ec2-user/candidates.json

cat > /home/ec2-user/run.sh <<'WORK'
#!/bin/bash
# set -e INSIDE the work block, so the DONE marker describes what happened
# rather than that the script reached its last line.
set -euxo pipefail

cd /home/ec2-user
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

git clone --depth 1 --branch BRANCH_PLACEHOLDER \
    https://github.com/simoncoombes/pretium.git src
cd src

uv venv --python 3.11 .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
# measures.py imports pyarrow directly and pretium.baselines needs numpy.
uv pip install numpy pyarrow maturin pytest
maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall pretium

python -c "import numpy, pyarrow, pretium; print('PREFLIGHT OK', pretium.version())"

# The gate must be able to fail. Four earlier runs in this project's history
# carried a probe calling a function that does not exist, behind a `|| true`
# that swallowed the error, so the check never ran at all.
test -f tools/calibration/gate_batch.py
python -m pytest tests/test_known_answer.py -q

mkdir -p /home/ec2-user/out

# Round 17 sized the driven-window gap in two parts. Bucketed by the same
# session's VIX on both sides, thirty seeds:
#
#   VIX      12-16  16-20  20-25  25-30  30-45   45+
#   ratio     1.42   1.54   1.33   1.62   2.05   1.51
#
# The day-weighted mean is 1.549 against the gate's aggregate 1.565, so the
# decomposition is arithmetically sound. A flat 1.55 explains 52% of the
# worst bucket and the VIX 30-45 spike is the other 48%.
#
# The odd row is the last one. The ratio falls from 2.05 to 1.51 in the
# regime where the model is most stressed, which would mean the model
# becomes ACCURATE again exactly where it is under most strain. The
# alternative is that something clips it, and there is an obvious candidate:
# `price_breaker_fraction` is 0.25, so any session move beyond 25% of the
# open is halted, and the 45+ bucket is where the model's moves are largest.
# §84 measured the same guard from the other side, `circuit_breaker`
# contributing -0.057 to variance at a pinned VIX 65.
#
# PREDICTION, before the run. If the breaker is what pulls 2.05 back to
# 1.51, then widening it RAISES the 45+ ratio and leaves every other bucket
# alone, because no other bucket's moves get near 25%. If instead all the
# buckets move, the breaker is binding far more widely than anyone has
# assumed and the guard is shaping the certified panel rather than guarding
# it. If nothing moves, the model really is more accurate at 45+ than at
# 30-45 and that needs a different explanation.
#
# The dial is documented as "a guard: settable, never searched", so this is
# a diagnosis and not a candidate. Nothing here is proposed for a preset.
python tools/calibration/driven_buckets.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 --seed-start 201 \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/breaker-201.json

WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|WORKERS_PLACEHOLDER|${WORKERS}|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh

set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

# The artefact's existence is the test, and the marker names THIS run's
# artefact rather than some previous run's.
if [ -f /home/ec2-user/out/breaker-201.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
