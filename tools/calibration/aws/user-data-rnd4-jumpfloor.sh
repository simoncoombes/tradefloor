#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd4-jumpfloor
# `dev`, and the run needs eb44efa or later: gate_batch and macro_range only
# learned --seed-start there. Without it both silently re-measure on the
# calibration seeds and the run confirms nothing while looking identical.
# Verified with `git show origin/dev:...` before launch, runbook trap 16.
BRANCH=dev
# 64, not 96. remeasure uses a THREAD pool, not processes, so the ceiling is
# how much of the engine runs with the GIL released rather than the core
# count. 64 is eight times the laptop's 8 with headroom left for the serial
# wall-clock groups that follow.
WORKERS=94   # c8g.24xlarge, 96 vCPU

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
 {"label":"v12-control",  "base":"pt-v12","overrides":{}},
 {"label":"ji-0.014",     "base":"pt-v12","overrides":{"jump_intensity_idio":0.014}},
 {"label":"ji-0.011",     "base":"pt-v12","overrides":{"jump_intensity_idio":0.011}},
 {"label":"ji-0.009",     "base":"pt-v12","overrides":{"jump_intensity_idio":0.009}},
 {"label":"ji-0.006",     "base":"pt-v12","overrides":{"jump_intensity_idio":0.006}},
 {"label":"js-0.052",     "base":"pt-v12","overrides":{"jump_sigma_idio":0.052}},
 {"label":"js-0.044",     "base":"pt-v12","overrides":{"jump_sigma_idio":0.044}},
 {"label":"ji-0.011-mk",  "base":"pt-v12","overrides":{"jump_intensity_idio":0.011,"jump_intensity_market":0.090}},
 {"label":"ji-0.011-f47", "base":"pt-v12","overrides":{"jump_intensity_idio":0.011,"idio_sigma_scale":0.47}}
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

# §84 decomposed calm-market variance and found the jump process supplying
# 40.5% of it at a pinned VIX 5 and 1.1% at VIX 65, at a day-cell count flat
# to within sampling: a constant-intensity per-name process that cannot know
# the regime. That is the floor §83 looked for in three other terms and did
# not find, and the floor is why the calm end reads 22.68% annualised against
# a real 17.2%.
#
# Every jump run since went the other way. `jump_vix_coupling` ADDS per-name
# variance in a crisis, and crisis co-movement is the market factor's share
# of total variance, so four gate batches bought the lever and sold the
# co-movement, 0.604 against a real floor of 0.664, every time.
#
# This cuts the per-name intensity instead and leaves the MARKET jump alone.
# Round 2 is the evidence that the class works: idio_sigma_scale 0.53 -> 0.47
# raised co-movement 0.696 -> 0.718 and the lever 6.04 -> 6.13 by removing
# per-name variance rather than adding a mechanism.
#
# The binding constraint is kurtosis, not volatility: the 504-day vol band is
# (16, 34) and pt-v12 sits at 33.89, so there are 17.9 points of room BELOW
# and cutting variance moves away from the ceiling. But jumps are where the
# model's fat tails come from and the 504-day excess_kurtosis floor is 7.1,
# so the cut that fixes the floor may take the tails with it. That is a
# measurement, which is what this run is.
python tools/calibration/gate_batch.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/gates-train.json

# The same nine on a disjoint block, so anything that survives arrives
# already carrying its own confirmation rather than needing another round.
python tools/calibration/gate_batch.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 --seed-start 301 \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/gates-301.json
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
if [ -f /home/ec2-user/out/gates-train.json ] \
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
