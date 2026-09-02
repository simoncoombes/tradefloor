#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. 600 cells is the stated budget (P4-realism-red-team.md's own
# usage example). A 40-name, 252-day cell measured 15.4s single-threaded on
# a laptop (tf-p4 smoke, 2026-09-02); on WORKERS_PLACEHOLDER workers that is
# under 600*15.4/WORKERS_PLACEHOLDER seconds of compute, with the Rust
# release build and provisioning the larger share of the wall clock at this
# scale, the same lesson gates.sh and volscreen-d.sh both record.
shutdown -h +90

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/red-team
# feat/realism-red-team, not dev: tools/calibration/red_team.py lands on
# dev only once P4's pull request merges. Repoint this to dev (or main)
# once it has, the way volscreen-d.sh names the specific branch a run
# needs rather than assuming dev already carries it.
BRANCH=feat/realism-red-team
# 64: at 252 days a 40-name panel is lighter than the 504-day figure
# CONTRIBUTING.md measures (about 1.6 GB resident, six workers the
# practical ceiling on a LAPTOP). 64 is this run's starting point for a
# large box (c7g/c8g.16xlarge class or bigger); watch the first minute of
# run.log for a worker being killed (OOM) and relaunch lower if so.
WORKERS=64

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
  # report.txt and budget.json are written only once the whole search
  # finishes -- red_team.py holds its worst-per-statistic result in memory
  # until every requested cell has run, unlike atlas_survey.py's
  # tasks.jsonl. So there is nothing partial to sync from out/ before then;
  # the log is the only progress signal until the marker files appear.
  for f in report.txt budget.json; do
    if [ -f "/home/ec2-user/out/red-team/$f" ]; then
      aws s3 cp "/home/ec2-user/out/red-team/$f" "$BUCKET/$f" 2>&1
    fi
  done
  sleep 45
done
STREAM
chown ec2-user:ec2-user /home/ec2-user/stream.sh
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
# facts.measure reads bars through pyarrow; instrumentlib-style tools need
# numpy for the shim's arithmetic.
uv pip install numpy pyarrow maturin pytest
maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall tradefloor

python -c "import numpy, pyarrow, tradefloor; print('PREFLIGHT OK', tradefloor.version())"

# The gate must be able to fail. Four earlier runs in this project's history
# carried a probe calling a function that does not exist, behind a `|| true`
# that swallowed the error, so the check never ran at all.
test -f tools/calibration/red_team.py
python -m pytest tests/test_known_answer.py -q

mkdir -p /home/ec2-user/out/red-team

# The full run: pt-v16, seeds 1-200, 20 rosters (Universe.random(40, ...)
# each), all six shipped scenarios, 252 days, a 600-cell budget. Matches
# the usage example in docs/design/P4-realism-red-team.md exactly, so a
# reader comparing this run to that note is comparing the same command.
python tools/calibration/red_team.py \
  --preset pt-v16 --seeds 1-200 --rosters 20 --scenarios all --days 252 \
  --budget 600 --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/red-team
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
# artefact rather than some previous run's. report.txt is written last, by
# main() after every manifest has been written, so its presence is the
# whole search having finished rather than only started.
if [ -f /home/ec2-user/out/red-team/report.txt ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS no report.txt" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/red-team/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
