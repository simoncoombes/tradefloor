#!/bin/bash
# Dead-man switch first, before anything that can fail. Section 10 of the
# runbook: two failed launches cost six cents precisely because this line
# ran first.
#
# 180 minutes. The run is 128 vectors x 30 seeds = 3,840 panels at 40 names
# and 252 days. One 40-name 252-day panel measured at 26 seconds on the
# laptop that wrote this (one core, the 0.6.1 wheel), so the job is about
# 28 core-hours: under an hour on 94 workers, after 20 to 25 minutes of
# provisioning and the Rust build. The gates batch was killed by a
# 90-minute switch at 63.9% done; this leaves room for a build twice as
# slow and a run twice as long.
shutdown -h +180

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/shapley-v16
# The branch that carries the tool. feat/mechanism-shapley until its pull
# request lands on dev, then dev. The `test -f` below turns a branch
# without the tool into a failure at the clone rather than forty minutes
# in (trap 16).
BRANCH=feat/mechanism-shapley
# 94, not 96. Two cores left for the streamer and the OS; a pool sized to
# the full core count starves its own parent and the progress line stops.
WORKERS=94
BASE=pt-v14
TARGET=pt-v16
# The certified roster and the held-out seed block preset_panel.py measures
# as `heldout_seeds`: thirty seeds the calibration never drew, on the roster
# the envelope certifies.
SEEDS=1-30
DAYS=252
NAMES=40
UNIVERSE_SEED=111

dnf -y install gcc git tar gzip python3.11 python3.11-devel awscli-2

# S3 PREFLIGHT before anything expensive. Trap 6: a launch without
# --iam-instance-profile computes perfectly and delivers nothing, and looks
# healthy from outside until the marker never appears.
echo "preflight $(date -u)" > /tmp/PREFLIGHT-S3
if ! aws s3 cp /tmp/PREFLIGHT-S3 "$BUCKET/PREFLIGHT-S3"; then
  echo "ABORTING: cannot write to $BUCKET. Almost certainly a missing"
  echo "--iam-instance-profile Name=pretium-calib-profile on run-instances."
  shutdown -h now
  exit 1
fi

# Stream the work directory to S3 while the run is going, not after it.
# shapley.py fsyncs every finished row to tasks.jsonl and resumes from it
# under a matching plan fingerprint in meta.json, so a synced tasks.jsonl
# turns a second kill into a resume instead of a restart. `cp`, never
# `sync`: the role has PutObject/GetObject and no ListBucket, so sync lists
# the destination and fails silently (trap 7).
cat > /home/ec2-user/stream.sh <<'STREAM'
#!/bin/bash
BUCKET="$1"
# Proof of life before the first sleep, so "the streamer is dead" and "the
# streamer has not ticked yet" are distinguishable from outside the box.
date -u > /tmp/stream-alive
aws s3 cp /tmp/stream-alive "$BUCKET/STREAM-ALIVE" 2>&1 || echo "STREAM UPLOAD FAILED"
while true; do
  # 120, not 300. A spot reclaim gives two minutes of warning, so the sync
  # interval is the upper bound on how many rows a reclaim throws away.
  sleep 120
  # The log upload is NOT gated on the work directory existing: out/ is
  # created after maturin finishes, and a failure during provisioning must
  # be visible rather than look like a slow build.
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
  {
    echo "=== $(date -u) ==="
    for f in tasks.jsonl meta.json; do
      if [ -f "/home/ec2-user/out/$f" ]; then
        aws s3 cp "/home/ec2-user/out/$f" "$BUCKET/partial/$f" 2>&1
        echo "cp $f exit=$?"
      fi
    done
  } >> /var/log/pretium-stream.log 2>&1
  aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
done
STREAM
chmod +x /home/ec2-user/stream.sh
setsid nohup /home/ec2-user/stream.sh "$BUCKET" >/var/log/pretium-stream.log 2>&1 < /dev/null &

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
    https://github.com/simoncoombes/tradefloor.git src
cd src

uv venv --python 3.11 .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
# numpy AND pyarrow: facts.measure reads the daily bars through Arrow.
uv pip install numpy pyarrow maturin pytest
maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall tradefloor

# A missing dependency should cost seconds, not a Rust build.
python -c "import numpy, pyarrow, tradefloor; print('PREFLIGHT OK', tradefloor.version())"

# The tool has to exist on the branch we cloned (trap 16).
test -f tools/calibration/shapley.py

# The declaration against the preset table this build carries: every dial
# settable, no dial in two groups, the union from the base carrying the
# target's fingerprint. A build whose table disagrees stops here.
python tools/calibration/shapley.py --check \
  --base BASE_PLACEHOLDER --target TARGET_PLACEHOLDER

# Determinism gate before the measurement. A Graviton build that disagrees
# with macos-arm64 invalidates every number downstream of it.
python -m pytest tests/test_known_answer.py -q

mkdir -p /home/ec2-user/out

# If a previous attempt streamed partial rows, take them and resume. The
# tool refuses a directory whose meta.json names another plan, so a
# mismatched restore fails loudly rather than mixing two plans.
aws s3 cp BUCKET_PLACEHOLDER/partial/meta.json /home/ec2-user/out/meta.json || true
aws s3 cp BUCKET_PLACEHOLDER/partial/tasks.jsonl /home/ec2-user/out/tasks.jsonl || true
if [ -f /home/ec2-user/out/tasks.jsonl ]; then
  echo "RESUMING from $(wc -l < /home/ec2-user/out/tasks.jsonl) streamed rows"
fi

python tools/calibration/shapley.py \
  --base BASE_PLACEHOLDER --target TARGET_PLACEHOLDER \
  --seeds SEEDS_PLACEHOLDER --days DAYS_PLACEHOLDER \
  --names NAMES_PLACEHOLDER --universe-seed UNIVERSE_SEED_PLACEHOLDER \
  --workers WORKERS_PLACEHOLDER --out /home/ec2-user/out
WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|BUCKET_PLACEHOLDER|${BUCKET}|g" /home/ec2-user/run.sh
sed -i "s|WORKERS_PLACEHOLDER|${WORKERS}|" /home/ec2-user/run.sh
sed -i "s|BASE_PLACEHOLDER|${BASE}|g" /home/ec2-user/run.sh
sed -i "s|TARGET_PLACEHOLDER|${TARGET}|g" /home/ec2-user/run.sh
sed -i "s|SEEDS_PLACEHOLDER|${SEEDS}|" /home/ec2-user/run.sh
sed -i "s|DAYS_PLACEHOLDER|${DAYS}|" /home/ec2-user/run.sh
sed -i "s|NAMES_PLACEHOLDER|${NAMES}|" /home/ec2-user/run.sh
sed -i "s|UNIVERSE_SEED_PLACEHOLDER|${UNIVERSE_SEED}|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh

# Run as ec2-user so uv lands in /home/ec2-user/.local/bin rather than root's.
set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

# The artefact's existence on disk is the test, and the marker names THIS
# run's artefact. shapley.json is written after the CRN guard and the
# shares, so its presence means the guard passed on every seed.
if [ -f /home/ec2-user/out/shapley.json ]; then
  echo "COMPLETE exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS no shapley.json" > /tmp/DONE
fi

# Final sync, including the partial rows: if this run died mid-way the rows
# are what the next one resumes from, and they are worth more than the log.
for f in tasks.jsonl meta.json; do
  [ -f "/home/ec2-user/out/$f" ] && aws s3 cp "/home/ec2-user/out/$f" "$BUCKET/partial/$f" || true
done
aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
aws s3 cp /home/ec2-user/out/shapley.json "$BUCKET/" || true
aws s3 cp /home/ec2-user/out/report.txt "$BUCKET/" || true
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
