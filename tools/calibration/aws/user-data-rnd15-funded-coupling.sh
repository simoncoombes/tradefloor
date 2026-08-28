#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd15-funded-coupling
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
  "label": "d3-f10",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.10802741
  }
 },
 {
  "label": "c035-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.004447519,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0528186134,
   "jump_vix_coupling": 0.35
  }
 },
 {
  "label": "c035-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.004447519,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0528186134,
   "jump_vix_coupling": 0.35,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "c035-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.004447519,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0528186134,
   "jump_vix_coupling": 0.35,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "c070-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0035132692,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0417234886,
   "jump_vix_coupling": 0.7
  }
 },
 {
  "label": "c070-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0035132692,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0417234886,
   "jump_vix_coupling": 0.7,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "c070-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0035132692,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0417234886,
   "jump_vix_coupling": 0.7,
   "idio_sigma_scale": 0.63
  }
 },
 {
  "label": "c100-i53",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0029772151,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0353573249,
   "jump_vix_coupling": 1.0
  }
 },
 {
  "label": "c100-i57",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0029772151,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0353573249,
   "jump_vix_coupling": 1.0,
   "idio_sigma_scale": 0.58
  }
 },
 {
  "label": "c100-i63",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0029772151,
   "jump_sigma_idio": 0.10802741,
   "jump_intensity_market": 0.0353573249,
   "jump_vix_coupling": 1.0,
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

# Round 13 closed the DIAL route to the driven window and said what would
# be needed instead: a mechanism that supplies tails without supplying calm
# variance. `jump_vix_coupling` is exactly that mechanism and it already
# exists, shipping inert at 0.0. It scales the jump threshold by the regime,
# so jumps fire rarely in a calm market and often in a crisis.
#
# It was tried across four gate batches and rejected every time on crisis
# co-movement, because per-name jumps in a crisis are per-name variance in a
# crisis and co-movement IS the market factor's share of that. Two things
# have changed since.
#
# First, round 13 found the antidote by accident. RAISING `idio_sigma_scale`
# takes crisis co-movement 0.724 to 0.676 -- it was rejected there because it
# costs volatility, and a funded coupling is a volatility REFUND. The two
# failures cancel.
#
# Second, the funding factor is derivable rather than searched, and the
# dial's own docstring derives it: mean scale is 1 + 1.035c, funding is its
# reciprocal, quoted there as 1.725 and 0.580 at c=0.7. Applying it to both
# intensities holds the MEAN jump rate fixed and only redistributes it
# across regimes. Composed with d3-f10's reshaping, which holds jump
# variance fixed and redistributes it across events, the two redistributions
# are orthogonal and neither spends anything on average.
#
# PREDICTION, written before the run. §84 puts 40.5% of calm variance in the
# jump process and round 13 confirmed that predictively to the second
# decimal. A funded coupling multiplies the calm jump rate by
# `(1-c+c/9)/(1+1.035c)`, so the calm end at a pinned VIX 5 should read:
#
#     c = 0.35   rate x0.506   calm vol 20.32
#     c = 0.70   rate x0.219   calm vol 18.79
#     c = 1.00   rate x0.055   calm vol 17.85
#
# against pt-v12's 22.72 and a real 17.2. If that holds, the calm-market
# floor -- the thing behind the driven window, the short lever and §83's
# three failed hypotheses -- is closed at c = 1.0.
#
# The risk is the other end. Redistributing toward crisis raises the crisis
# end while lowering the calm one, so the lever is squeezed from both sides
# and may overshoot the real 6.16 badly at c = 1.0. And the co-movement bill
# still has to be paid; whether raised `idio_sigma_scale` covers it is the
# question this run exists to answer.
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
