#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +150

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/r86
# `main`, and the run needs 55e1909 or later: that is 0.4.1, where pt-v14
# stops reporting a 68.26-day mispricing half-life it never ran. `v14ship`
# below is built from the FROZEN preset, so a branch without the fix would
# put the misreported metadata into the control's provenance. The behaviour
# is identical either way, which is the point of running both controls.
# Verified with `git show origin/main:...` before launch, runbook trap 16.
BRANCH=preset/pt-v15
# 64, not 96. remeasure uses a THREAD pool, not processes, so the ceiling is
# how much of the engine runs with the GIL released rather than the core
# count. 64 is eight times the laptop's 8 with headroom left for the serial
# wall-clock groups that follow.
WORKERS=94   # c8g.24xlarge alongside round 33

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
if ! aws s3 cp "s3://dia-test-101631415962-us-east-2-an/pretium-calib/in/cands-r86.json" \
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

# Qualifying d35 over the blocks it has never seen.
#
# d35 is round 83's lead: pt-v14 plus the two-component variance mixture at
# slow timescale rho 0.98. On four blocks it compressed the crisis
# co-movement RANGE to 0.0476, below pt-v12's 0.0551, while holding 4/4
# panel. Four blocks is exactly the evidential shape that produced src90c,
# the pair's lever gain and the dispersion gap, all three later withdrawn.
# This is the other nine.
#
# FOUR candidates, not two, because 0.4.1 uncovered that the shipped pt-v14
# is not the vector the 13-block comparison measured. The frozen preset is a
# `const fn` and could not recompute `mispricing_phi` from the half-life the
# search chose, so it runs the inherited 60-day decay. The difference is at
# most 3.2% of a band width, but a qualification cannot rest on "at most":
#
#   v14ship  the frozen preset, which is what users have and therefore the
#            baseline a successor must actually beat
#   v14ref   pt-v12 + the 15 searched overrides, the vector the 13-block
#            evidence was gathered on, kept so this run is comparable to it
#   d35      v14ref + the mixture, the lead exactly as round 83 measured it
#   d35ship  v14ship + the mixture, which is what a real pt-v15 built from
#            today's default would be
#
# If d35 beats v14ref but d35ship does not beat v14ship, the mixture's gain
# was riding on the half-life and not on the timescale, and the lead dies.
# No previous round could have asked that question.
# Two questions in one run.
#
# ONE: is there a damp holding thirteen of thirteen on BOTH crisis
# instruments? Round 85 measured 0.32 at co-movement 13/13 and lever 11/13,
# and 0.374 at 12/13 and 13/13. Nothing between them has been measured, so if
# a cell holds both it lives at 0.33 or 0.34. Both endpoints are re-measured
# as harness controls: a sweep that cannot reproduce its own previous cells is
# measuring the harness rather than the model.
#
# TWO: what does `daily_credit_floor_gain` cost? It ships inert at 0.0 and
# corrects a corporate spread that inverts between central bank meetings,
# measured down to 0.4216 against a floor of 0.8. Turning it on is a
# trajectory change, so it needs a preset boundary, and this is the boundary
# being built. `v14floor` isolates the floor on the shipped preset; `d34floor`
# is the floor and the mixture together, which is what pt-v15 would actually
# be. If the floor costs panel blocks the two have to be separated.

# NOT 101. That is the calibration block, and gate_batch refuses it:
# "a confirmation block that shares seeds with discovery confirms nothing".
# The first attempt at this run listed it first, the guard fired, and `set -e`
# took the other nine blocks down with it. Hence `|| true` below: one refused
# or failed block must not destroy the nine that would have worked.
for BLOCK in 201 301 401 501 601 701 801 901 1001 1101 1201 1301 1401; do
  python tools/calibration/gate_batch.py \
    --candidates /home/ec2-user/candidates.json \
    --seeds 30 --seed-start "$BLOCK" \
    --workers WORKERS_PLACEHOLDER \
    --out "/home/ec2-user/out/gates-${BLOCK}.json" || true
  aws s3 cp "/home/ec2-user/out/gates-${BLOCK}.json" "BUCKET_PLACEHOLDER/" || true
done


WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|BUCKET_PLACEHOLDER|${BUCKET}|" /home/ec2-user/run.sh
sed -i "s|WORKERS_PLACEHOLDER|${WORKERS}|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh

set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

# The artefact's existence is the test, and the marker names THIS run's
# artefact rather than some previous run's.
if [ -f /home/ec2-user/out/gates-1401.json ] && [ -f /home/ec2-user/out/gates-1301.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
