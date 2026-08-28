#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd23-comovement-budget
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
  "label": "p970",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.300507218,
   "market_vol_beta": 0.669492782
  }
 },
 {
  "label": "p970-g15",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.300507218,
   "market_vol_beta": 0.669492782,
   "garch_vix_coupling": 0.15
  }
 },
 {
  "label": "p970-g00",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.300507218,
   "market_vol_beta": 0.669492782,
   "garch_vix_coupling": 0.0
  }
 },
 {
  "label": "p975-g15",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.302056224,
   "market_vol_beta": 0.672943776,
   "garch_vix_coupling": 0.15
  }
 },
 {
  "label": "p975-g00",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.302056224,
   "market_vol_beta": 0.672943776,
   "garch_vix_coupling": 0.0
  }
 },
 {
  "label": "p980-g15",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.30360523,
   "market_vol_beta": 0.67639477,
   "garch_vix_coupling": 0.15
  }
 },
 {
  "label": "p980-g00",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.30360523,
   "market_vol_beta": 0.67639477,
   "garch_vix_coupling": 0.0
  }
 },
 {
  "label": "v12-g00",
  "base": "pt-v12",
  "overrides": {
   "garch_vix_coupling": 0.0
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

# p970 is bounded by exactly one thing: crisis co-movement reads 0.671 at
# its worst block against a real floor of 0.664. Each further step of
# persistence is worth roughly 0.05 on the driven-window ratio and costs
# roughly 0.02 of co-movement, so the question is whether the co-movement
# can be bought from somewhere else.
#
# The calibration record already names the dial. `garch_vix_coupling` is
# how much a NAME's own variance follows the VIX, and crisis co-movement IS
# the market factor's share of total variance, so turning it down raises
# co-movement directly: measured at thirty seeds, 0.3 to 0.0 took pt-v11
# from 0.604 to 0.662. pt-v12 still ships 0.3 and this programme has never
# touched it.
#
# WHY THIS STACK MIGHT WORK WHERE THREE HAVE FAILED. Rounds 7, 9 and 22 all
# failed the same way: two changes that were each good SPENT THE SAME
# MARGIN -- cross-sectional correlation twice, corr_asymmetry once -- and
# together overran it. Here the two are OPPOSED on the binding axis.
# Persistence spends co-movement; garch_vix_coupling buys it. That is the
# condition for a stack to compose and it has not been true of any pair
# tried so far.
#
# The documented cost is 0.41 of crisis lever, and that is the axis this
# could break instead: p970 already reads 5.62 to 5.86 against a real 6.16.
# `v12-g00` is in the pool as the control for the dial alone, because a
# stack cannot be read without knowing what each half does ON THIS BASE --
# the same move gave 0.662 on pt-v11 and 0.740 on pt-v10, and pt-v12 is
# neither.
#
# Blocks 401 and 201 carry p970's two tightest co-movement readings, 0.671
# and 0.705, plus the training block for comparability.
for BLOCK in "--seed-start 401" "--seed-start 201" ""; do
  TAG=$(echo "${BLOCK:-train}" | tr -d ' -' | sed 's/seedstart//')
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
if [ -f /home/ec2-user/out/gates-401.json ] && [ -f /home/ec2-user/out/gates-201.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
