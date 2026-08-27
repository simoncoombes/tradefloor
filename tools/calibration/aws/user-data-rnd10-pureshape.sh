#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd10-pureshape
# `dev`, and the run needs eb44efa or later: gate_batch and macro_range only
# learned --seed-start there. Without it both silently re-measure on the
# calibration seeds and the run confirms nothing while looking identical.
# Verified with `git show origin/dev:...` before launch, runbook trap 16.
BRANCH=dev
# 64, not 96. remeasure uses a THREAD pool, not processes, so the ceiling is
# how much of the engine runs with the GIL released rather than the core
# count. 64 is eight times the laptop's 8 with headroom left for the serial
# wall-clock groups that follow.
WORKERS=190

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
  "label": "v12-control",
  "base": "pt-v12",
  "overrides": {}
 },
 {
  "label": "d2-f10",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0090879492,
   "jump_sigma_idio": 0.08820401
  }
 },
 {
  "label": "d3-f10",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.10802741
  }
 },
 {
  "label": "d4-f10",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.12473931
  }
 },
 {
  "label": "d6-f10",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.15277383
  }
 },
 {
  "label": "d8-f10",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0022719873,
   "jump_sigma_idio": 0.17640802
  }
 },
 {
  "label": "d12-f10",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0015146582,
   "jump_sigma_idio": 0.21605482
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

# pt-v12 is boxed in on four sides at once, and rounds 2 through 9 mapped
# every wall:
#
#   504-day volatility     33.89 against a ceiling of 34.0
#   504-day kurtosis        7.75 against a floor of 7.1
#   cross-sectional corr   0.380 against a ceiling of 0.410
#   crisis co-movement     0.696 against a real ceiling of 0.727
#
# Adding variance breaks the first. Diluting the jumps breaks the second.
# Removing per-name variance breaks the third and fourth together, because
# both are the market factor's share of the total, which is what every
# candidate so far has been quietly spending.
#
# Round 6 swept jump variance at 0.8, 0.6 and 0.5 of pt-v12's and never at
# 1.0, which is the one setting that spends nothing. Jump variance is
# lambda*sigma^2 and the tail term goes as lambda*sigma^4, so dividing the
# intensity by d and scaling sigma by sqrt(d) leaves the variance IDENTICAL
# -- to the digit -- and multiplies the tail term by d.
#
# The prediction is therefore sharp and the run can falsify it: volatility,
# cross-sectional correlation and crisis co-movement should not move at all,
# and excess kurtosis should rise roughly with d. If it holds, the kurtosis
# floor stops being a wall for free. If volatility or either correlation
# moves, the variance decomposition this programme has been reasoning from
# is wrong, which is worth knowing on its own.
#
# The risk is the other end: the 504-day kurtosis ceiling is 22.0, and d12
# is there to find it rather than to be shipped.
for BLOCK in "--seed-start 201" "--seed-start 501" "--seed-start 301"; do
  TAG=$(echo "$BLOCK" | tr -d ' -' | sed 's/seedstart//')
  python tools/calibration/gate_batch.py \
    --candidates /home/ec2-user/candidates.json \
    --seeds 30 $BLOCK \
    --workers WORKERS_PLACEHOLDER \
    --out "/home/ec2-user/out/gates-${TAG}.json"
done


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
if [ -f /home/ec2-user/out/gates-201.json ] \
   && [ -f /home/ec2-user/out/gates-501.json ] \
   && [ -f /home/ec2-user/out/gates-301.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
