#!/bin/bash
# Dead-man switch first, before anything that can fail. §10 of the runbook:
# two failed launches cost six cents precisely because this line ran first.
#
# 240, not 90. The first survey launch was killed by its own switch at t+91m
# while 63.9% done, running clean at 1383 tasks/min with zero errors and an
# ETA of another 0.8h. The job needs about 2.4 hours; the switch was set for a
# job a third that size. Ninety-one minutes of a 96-core box produced one log
# file and no measurement.
shutdown -h +240

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/survey2
BRANCH=main

dnf -y install gcc git tar gzip python3.11 python3.11-devel awscli-2

# Stream the work directory to S3 while the run is going, not after it.
# §6: retrieve as work completes. atlas_survey.py fsyncs tasks.jsonl before it
# prints progress and resumes from it under a matching plan fingerprint, so a
# synced tasks.jsonl turns a second kill into a resume instead of a restart.
cat > /home/ec2-user/stream.sh <<'STREAM'
#!/bin/bash
BUCKET="$1"
while true; do
  sleep 300
  # The log upload is NOT gated on the work directory existing. Gating it
  # there cost the first streamed run its visibility for the whole build:
  # out/ is created after maturin finishes, so a failure during provisioning
  # uploaded nothing at all and looked identical to a slow build.
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
  if [ -d /home/ec2-user/out ]; then
    aws s3 sync /home/ec2-user/out "$BUCKET/partial/" \
      --exclude "*" --include "tasks.jsonl" --include "meta.json" || true
  fi
done
STREAM
chmod +x /home/ec2-user/stream.sh
nohup /home/ec2-user/stream.sh "$BUCKET" >/var/log/pretium-stream.log 2>&1 &

cat > /home/ec2-user/run.sh <<'WORK'
#!/bin/bash
# set -e INSIDE the work block. Launch 1 of the previous run wrote CERTIFIED
# over a search that never happened, because every step failed, execution
# continued, and the last line was the marker. The marker must describe what
# happened, not that the script reached the end.
set -euxo pipefail

cd /home/ec2-user
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# The repository is public now, so clone the branch rather than shipping a
# presigned tarball. That also removes the runbook's two-worktree trap: the
# engine change and the tooling change are the same commit here.
git clone --depth 1 --branch BRANCH_PLACEHOLDER \
    https://github.com/simoncoombes/pretium.git src
cd src

uv venv --python 3.11 .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
# numpy AND pyarrow: facts.measure reads the daily bars through Arrow, which
# launch 2 of the previous run discovered after a full Rust build.
uv pip install numpy pyarrow maturin pytest
maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall pretium

# Preflight: a missing dependency should cost seconds, not a Rust build.
python -c "import numpy, pyarrow, pretium; print('PREFLIGHT OK', pretium.version())"

# Determinism gate before the search. A Graviton build that disagrees with
# macos-arm64 invalidates everything downstream of it.
# The gate must be able to FAIL. Four earlier runs carried a probe calling a
# function that does not exist and a pytest line with no pytest installed
# whose "|| true" swallowed the error, so the check never ran at all.
python -m pytest tests/test_known_answer.py -q

mkdir -p /home/ec2-user/out

# If a previous attempt streamed partial rows, take them and resume. The
# survey refuses an index-based resume whose plan fingerprint disagrees, so a
# mismatched restore fails loudly rather than mixing two plans.
aws s3 cp BUCKET_PLACEHOLDER/partial/meta.json /home/ec2-user/out/meta.json || true
aws s3 cp BUCKET_PLACEHOLDER/partial/tasks.jsonl /home/ec2-user/out/tasks.jsonl || true
if [ -f /home/ec2-user/out/tasks.jsonl ]; then
  echo "RESUMING from $(wc -l < /home/ec2-user/out/tasks.jsonl) streamed rows"
fi

python tools/calibration/atlas_survey.py plan --samples 1000 \
  --out /home/ec2-user/out
python tools/calibration/atlas_survey.py run \
  --out /home/ec2-user/out --workers 94
python tools/calibration/atlas_survey.py collect --out /home/ec2-user/out
WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|BUCKET_PLACEHOLDER|${BUCKET}|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh

# Run as ec2-user so uv lands in /home/ec2-user/.local/bin rather than root's.
set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

# The certificate's existence on disk is the test, not the script reaching
# its last line.
if [ -f /home/ec2-user/out/atlas-survey.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS no certificate" > /tmp/DONE
fi

# Final sync, including the partial rows: if this run died mid-way the rows
# are what the next one resumes from, and they are worth more than the log.
aws s3 sync /home/ec2-user/out "$BUCKET/partial/" \
  --exclude "*" --include "tasks.jsonl" --include "meta.json" || true
aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true
aws s3 cp /home/ec2-user/out/atlas-survey.json "$BUCKET/" || true
aws s3 cp /home/ec2-user/out/atlas-report.txt "$BUCKET/" || true

shutdown -h now
