#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd3-confirm
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
 {"label":"v12-control", "base":"pt-v12","overrides":{}},
 {"label":"fund-0.47",   "base":"pt-v12","overrides":{"idio_sigma_scale":0.47}},
 {"label":"r25-f0.47",   "base":"pt-v12","overrides":{"inflation_reversion":0.25,"idio_sigma_scale":0.47}},
 {"label":"r35-f0.50",   "base":"pt-v12","overrides":{"inflation_reversion":0.35,"idio_sigma_scale":0.50}}
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

# ---------------------------------------------------------------- §8
# The overfitting control, four vectors side by side. A rejection here is
# not a verdict on a candidate until the base has been through the same
# check: pt-v3 is REJECTED by the horizon flip test with no override at
# all, so the control is the only thing that makes the others readable.
# Runbook trap 11.
export PRETIUM_S8_WORKERS=44
for spec in "v12-control:" \
            "fund-0.47:idio_sigma_scale=0.47" \
            "r25-f0.47:inflation_reversion=0.25,idio_sigma_scale=0.47" \
            "r35-f0.50:inflation_reversion=0.35,idio_sigma_scale=0.50"; do
  name="${spec%%:*}"; ov="${spec#*:}"
  # `|| echo`, not `; echo`: run.sh runs under set -e, so a bare failing
  # python in a background subshell kills the subshell before it can record
  # anything and then fails `wait`, taking the gates down with it. The §8
  # control is the least important artefact here and must not be able to
  # abort the two that matter.
  ( python tools/calibration/section8_check.py --preset pt-v12 --overrides "$ov" \
      > "/home/ec2-user/out/s8-${name}.txt" 2>&1 \
    || echo "S8 RUN FAILED exit=$?" >> "/home/ec2-user/out/s8-${name}.txt" ) &
done
wait
grep -H "VERDICT" /home/ec2-user/out/s8-*.txt || true

# -------------------------------------------------------- disjoint gates
# TWO blocks, neither of them the calibration seeds. Round 2 found these
# candidates on 101-130 and scored them on 101-130, which reproduces a
# candidate's own fluctuation exactly and cannot distinguish a real gain
# from a lucky draw. 201-230 is the block §8 already uses for its horizon
# axis; 301-330 is untouched by anything.
python tools/calibration/gate_batch.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 --seed-start 201 \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/gates-201.json

python tools/calibration/gate_batch.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 --seed-start 301 \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/gates-301.json

# --------------------------------------------------------- disjoint macro
# The gain, on seeds the gain was not chosen on. Round 2's macro probe ran
# on the calibration seeds, so its sd 1.18 -> 1.98 is a training number.
python tools/calibration/macro_range.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 --years 5 --seed-start 201 \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/macro-201.json
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
   && [ -f /home/ec2-user/out/gates-301.json ] \
   && [ -f /home/ec2-user/out/macro-201.json ] \
   && [ -f /home/ec2-user/out/s8-v12-control.txt ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
