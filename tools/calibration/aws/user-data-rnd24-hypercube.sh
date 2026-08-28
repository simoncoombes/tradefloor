#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd24-hypercube
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

# Fifty-one vectors do not fit: user-data is capped at 25,600 bytes and the
# candidate list alone is past it. The list lives in S3 and the instance
# fetches it, which also means a survey's inputs are recoverable afterwards
# from the same bucket as its outputs rather than only from the launcher.
if ! aws s3 cp "s3://dia-test-101631415962-us-east-2-an/pretium-calib/in/cands-rnd24.json" \
      /home/ec2-user/candidates.json; then
  echo "ABORTING: cannot fetch the candidate list"
  shutdown -h now
  exit 1
fi
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

# Twenty-three rounds of one-dial-at-a-time have found three SHARED
# BUDGETS, and every one of them only when a stack was measured rather than
# assumed: cross-sectional correlation in rounds 7 and 9, `corr_asymmetry`
# in round 22, and the crisis lever in round 23. Hand search is structurally
# blind to interactions, and this programme now has the receipts.
#
# So stop guessing pairs. Six axes, each bounded by what has already been
# measured rather than by the parameter's legal range -- an unbounded cube
# would spend most of its samples where rounds 4, 13, 15 and 21 have already
# been:
#
#   persistence   0.9631 - 0.980   0.985 breaks the 252-day panel
#   jump d        1 - 6            d>=6 loses volume_abs_return_corr
#   jump f        0.80 - 1.00      f<0.8 loses cross-sectional correlation
#   idio_sigma    0.47 - 0.58      0.44 loses xs corr, 0.63 loses volatility
#   garch_vix     0.00 - 0.45      0.0 buys co-movement and costs lever
#   jump_vix      0.00 - 0.35      raises lever, costs co-movement
#
# Forty-eight Latin hypercube samples plus pt-v12, d3-f10 and p970 as
# references. `jump_vix_coupling` carries its derived funding, 1/(1+1.035c),
# on both intensities -- round 15 established that an unfunded coupling
# leaves the volatility band on every vector, so an unfunded sample would be
# a wasted row.
#
# ONE BLOCK, 201-230, and this is a SCREEN not a verdict. Round 13 screened
# 28 vectors on this block and its winner did not survive round 14 on the
# four blocks it had not seen. Anything that survives here gets confirmed
# the same way.
#
# The gate now scores crisis co-movement against 0.664-0.727 AND the crisis
# lever against 6.16, so a vector that buys the panel by selling either is
# visible in the output rather than discovered three rounds later.
for BLOCK in "--seed-start 201"; do
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
