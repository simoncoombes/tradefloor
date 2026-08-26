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

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/survey6
BRANCH=main
# 4000 is the tool default and what the 2026-08-25 survey actually ran:
# 192000 tasks, about 2.4 hours on 94 workers at ~1385 tasks/min.
SAMPLES=3000
# The §72 surface: the fear channel on the pt-v9 base. pt-v9 was calibrated
# by hand from a real slope estimate and a four-point sweep at six seeds, in
# a space with seven dimensions, and it pays for its one-year gain with the
# crisis blend (3.16x to 2.29x). The axes are the channel itself (gain,
# clamp, target cap), what it reacts to (jump_sigma_market), what sets the
# regime clock (vix_cycle_amplitude), the crisis blend cap that the channel
# costs, and the idio scale to pay. Seven axes, 3000 LHS points, 144000
# tasks, about 70 minutes on 94 workers. Columns to read: the fourteen-stat
# panel at BOTH horizons, corr_blend, vol_lever and sector_ex_45.
BASE=pt-v9
ONLY=vix_return_gain,vix_return_clamp,vix_target_shock_cap,vix_cycle_amplitude,jump_sigma_market,crisis_blend_cap,idio_sigma_scale

dnf -y install gcc git tar gzip python3.11 python3.11-devel awscli-2

# S3 PREFLIGHT, before anything expensive. The 2026-08-25 survey ran 192,000
# tasks over 2.4 hours on 96 cores with zero errors and produced NOTHING,
# because the instance was launched without --iam-instance-profile and every
# `aws s3 cp` died with "Unable to locate credentials". From outside the box
# that is indistinguishable from a healthy run until the very end, when the
# certificate does not appear and the instance has already terminated.
#
# The script already refuses to spend a Rust build on a missing Python
# dependency. Credentials deserve the same treatment, and cost one second.
echo "preflight $(date -u)" > /tmp/PREFLIGHT-S3
if ! aws s3 cp /tmp/PREFLIGHT-S3 "$BUCKET/PREFLIGHT-S3"; then
  echo "ABORTING: cannot write to $BUCKET. Almost certainly a missing"
  echo "--iam-instance-profile Name=pretium-calib-profile on run-instances."
  shutdown -h now
  exit 1
fi

# Stream the work directory to S3 while the run is going, not after it.
# §6: retrieve as work completes. atlas_survey.py fsyncs tasks.jsonl before it
# prints progress and resumes from it under a matching plan fingerprint, so a
# synced tasks.jsonl turns a second kill into a resume instead of a restart.
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
  # The log upload is NOT gated on the work directory existing. Gating it
  # there cost the first streamed run its visibility for the whole build:
  # out/ is created after maturin finishes, so a failure during provisioning
  # uploaded nothing at all and looked identical to a slow build.
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
  # The sync's own errors went to this log and nowhere else, which is why the
  # 2026-08-25 run could be watched uploading run.log every 120s while never
  # once producing partial/tasks.jsonl. The loop was demonstrably alive, the
  # include pattern was verified correct locally, and out/ demonstrably
  # existed, so the cause sat in output nobody could read. It is uploaded now.
  # cp, NOT sync. DIAGNOSED 2026-08-25: pretium-calib-role grants s3:PutObject
  # and s3:GetObject and nothing else, and `aws s3 sync` needs s3:ListBucket on
  # the destination to compare against. So run.log (a cp) uploaded every 120s
  # for a whole run while partial/tasks.jsonl (a sync) never appeared once, and
  # a spot reclaim would have thrown the entire run away. Two plain cps need no
  # permission the role does not already have.
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
# setsid, and an immediate proof-of-life upload. The first streamed run wrote
# NOTHING to S3 for its whole life while the survey ran perfectly: the run was
# visible only through `aws ec2 get-console-output`, which needs no SSH and is
# now the primary monitor. The cause was never isolated, so this does three
# things rather than guess: detaches properly, writes a marker before the
# first sleep so a dead streamer is distinguishable from a slow one within a
# minute, and logs its own failures somewhere they can be read.
setsid nohup /home/ec2-user/stream.sh "$BUCKET" >/var/log/pretium-stream.log 2>&1 < /dev/null &

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

# --samples on BOTH, and they must match. It is one top-level argument that
# `run` re-reads from its own default of 4000, and `plan` used to write nothing,
# so `plan --samples 1000` followed by a bare `run` forecast 48000 tasks and
# then ran 192000 under a different plan fingerprint printed one line later.
# The dead-man switch is sized off that forecast, which is how launch 1 was
# killed by its own timeout at 63.9% complete.
python tools/calibration/atlas_survey.py plan --samples "$SAMPLES" --base "$BASE" --only "$ONLY" \
  --out /home/ec2-user/out
python tools/calibration/atlas_survey.py run --samples "$SAMPLES" --base "$BASE" --only "$ONLY" \
  --out /home/ec2-user/out --workers 94
python tools/calibration/atlas_survey.py collect --base "$BASE" --only "$ONLY" --out /home/ec2-user/out
WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|BUCKET_PLACEHOLDER|${BUCKET}|" /home/ec2-user/run.sh
sed -i "s|\$SAMPLES|${SAMPLES}|g; s|\$BASE|${BASE}|g; s|\$ONLY|${ONLY}|g" /home/ec2-user/run.sh
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
for f in tasks.jsonl meta.json; do
  [ -f "/home/ec2-user/out/$f" ] && aws s3 cp "/home/ec2-user/out/$f" "$BUCKET/partial/$f" || true
done
aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true
aws s3 cp /home/ec2-user/out/atlas-survey.json "$BUCKET/" || true
aws s3 cp /home/ec2-user/out/atlas-report.txt "$BUCKET/" || true

shutdown -h now
