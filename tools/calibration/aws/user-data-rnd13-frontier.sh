#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd13-frontier
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
  "label": "v12",
  "base": "pt-v12",
  "overrides": {}
 },
 {
  "label": "d3-f10-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.10802741
  }
 },
 {
  "label": "d3-f100-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.10802741,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d3-f100-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.10802741,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "d3-f85-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.09959635
  }
 },
 {
  "label": "d3-f85-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.09959635,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d3-f85-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.09959635,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "d3-f70-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.09038222
  }
 },
 {
  "label": "d3-f70-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.09038222,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d3-f70-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.09038222,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "d4-f100-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.12473931
  }
 },
 {
  "label": "d4-f100-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.12473931,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d4-f100-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.12473931,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "d4-f85-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.11500396
  }
 },
 {
  "label": "d4-f85-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.11500396,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d4-f85-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.11500396,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "d4-f70-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.10436439
  }
 },
 {
  "label": "d4-f70-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.10436439,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d4-f70-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0045439746,
   "jump_sigma_idio": 0.10436439,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "d6-f100-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.15277383
  }
 },
 {
  "label": "d6-f100-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.15277383,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d6-f100-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.15277383,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "d6-f85-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.14085051
  }
 },
 {
  "label": "d6-f85-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.14085051,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d6-f85-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.14085051,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "d6-f70-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.12781975
  }
 },
 {
  "label": "d6-f70-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.12781975,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "d6-f70-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0030293164,
   "jump_sigma_idio": 0.12781975,
   "idio_sigma_scale": 0.63
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

# The gap that has not moved. `scenario-magnitude` calls the driven window
# "the worst axis in the model": model daily return sd 1.565x real AAPL over
# the 2020-21 macro path. Twelve rounds have moved it by about 0.015.
#
# The cause is the calm-market floor. At a pinned VIX 5 the model reads
# 22.68% annualised against a real 17.2% at VIX<12, so it is 32% too
# volatile where markets are quiet, while at VIX 45 it is 96.0 against a
# real 106.1 and 9% too calm. A driven window spends most of its days in
# the middle, so it inherits the floor.
#
# §84 attributed 40.5% of calm variance to the jump process. Round 4 cut it
# and the floor came down exactly as predicted -- and excess kurtosis went
# through its 7.1 floor at every cut, because the jumps ARE the tails.
#
# d3-f10 changes that. It carries kurtosis at 11.1 to 13.8, four to seven
# points of headroom, bought by reshaping rather than by spending. So the
# cut round 4 could not afford may now be affordable.
#
# Four walls and three dials, run as a full factorial rather than more hand
# picked points:
#
#   d  tail shape          3, 4, 6      raises kurtosis, raises xs corr
#   f  jump variance    1.00, .85, .70  lowers the floor AND kurtosis
#   i  idio_sigma_scale  .53, .58, .63  RAISED, not cut
#
# The third is the one nothing has tried. Every candidate in this programme
# cut `idio_sigma_scale` to buy volatility headroom and paid in correlation.
# Raising it does the reverse: it adds per-name variance, which lowers the
# market factor's share, which is what both the cross-sectional correlation
# ceiling and the crisis co-movement ceiling are made of. It buys the budget
# that rounds 7 and 9 could not find, and it pays for it in volatility --
# which is exactly what a lower f gives back.
#
# One block, 201-230, which is one of the two pt-v12 fails. This is a screen
# over 28 vectors, not a verdict; survivors get confirmed on more blocks.
for BLOCK in "--seed-start 201"; do
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
if [ -f /home/ec2-user/out/gates-201.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
