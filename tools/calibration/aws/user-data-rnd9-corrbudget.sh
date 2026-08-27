#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd9-corrbudget
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
  "label": "fund-0.47",
  "base": "pt-v12",
  "overrides": {
   "idio_sigma_scale": 0.47
  }
 },
 {
  "label": "d3-f08",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.006058633,
   "jump_sigma_idio": 0.09662265
  }
 },
 {
  "label": "combo",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.006058633,
   "jump_sigma_idio": 0.09662265,
   "idio_sigma_scale": 0.47
  }
 },
 {
  "label": "combo-m0085",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.006058633,
   "jump_sigma_idio": 0.09662265,
   "idio_sigma_scale": 0.47,
   "market_factor_sigma": 0.0085
  }
 },
 {
  "label": "combo-m0082",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.006058633,
   "jump_sigma_idio": 0.09662265,
   "idio_sigma_scale": 0.47,
   "market_factor_sigma": 0.0082
  }
 },
 {
  "label": "combo-m0079",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.006058633,
   "jump_sigma_idio": 0.09662265,
   "idio_sigma_scale": 0.47,
   "market_factor_sigma": 0.0079
  }
 },
 {
  "label": "combo-m0075",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.006058633,
   "jump_sigma_idio": 0.09662265,
   "idio_sigma_scale": 0.47,
   "market_factor_sigma": 0.0075
  }
 },
 {
  "label": "f47-m0082",
  "base": "pt-v12",
  "overrides": {
   "idio_sigma_scale": 0.47,
   "market_factor_sigma": 0.0082
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

# Round 7 answered its question with a no, and named the reason. The two
# headroom mechanisms do not add: every combination is 12/14 at its worst
# block against 13/14 for either alone, and the statistic that breaks is
# always `cross_sectional_corr`.
#
# They are spending one budget. The 504-day band tops out at 0.41 and
# pt-v12 sits at 0.3797 on the balanced roster, so there are 0.0303 to
# spend. idio_sigma_scale 0.47 spends 0.0153 and d3-f08 spends 0.0260;
# together they want 0.0394 and go over by 0.0091. Both raise the market
# factor's share of total variance, because both remove per-name variance.
#
# So buy more budget. Cross-sectional correlation is roughly
# market_var/(market_var + rest), and 0.009 of it back from 0.4191 is about
# seven percent off `market_factor_sigma`, which pt-v12 ships at 0.008829.
# The risk is that it is the same term crisis co-movement is made of, with
# a real floor of 0.664, and the gate measures that on every candidate.
#
# Three blocks, weighted to where things actually fail: 201 and 501 are the
# two pt-v12 loses, 301 is the sanity check.
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
