#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/v14e-qualify
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
if ! aws s3 cp "s3://dia-test-101631415962-us-east-2-an/pretium-calib/in/cands-v14e.json" \
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

# r15-70 clears five seed blocks of five with 14 of 14 at both horizons on
# every one, crisis co-movement inside its real range everywhere pt-v12
# breaches the ceiling on one, and the best crisis lever of any candidate --
# 6.07 to 6.28 against a real 6.16, where pt-v12 runs 5.80 to 6.12. Its
# driven ratio of 1.355 against pt-v12's 1.551 is the largest movement this
# programme has produced on the axis the envelope calls the worst in the
# model.
#
# It does that with FIFTEEN dials against n19's thirty-three, beating it on
# both axes, and every one of the fifteen traces to a measured mechanism
# rather than a lucky sample:
#
#   8  persistence and funded jump coupling -- persistence spends the
#      crisis lever and the coupling buys it back (rounds 21, 24)
#   5  the crisis-threshold group, whose effect round 33 watched land in
#      the VIX 25-30 bucket specifically, 1.62 to 1.51
#   1  market_vol_vix_anchor -- the variance target scales with
#      (VIX/anchor)^2, so it rescales the curve rather than a point
#   1  endogenous_news_sigma -- pays the anchor's lever cost and lowers the
#      driven ratio at the same time
#
# What it has never had is section 8 and the concentrated rosters. c05 and
# c28 were qualified on both in rounds 33 and 35; r15-70 has not been near
# either. This closes that, and it is the last measurement standing between
# the evidence and a decision.
export PRETIUM_S8_WORKERS=44
for spec in "v12:" "s23:market_vol_alpha=0.28035004,market_vol_beta=0.69244622,jump_intensity_idio=0.0068895346,jump_sigma_idio=0.08745117,jump_intensity_market=0.0565753337,jump_vix_coupling=0.2626,idio_sigma_scale=0.59604441,garch_vix_coupling=0.14219611,crisis_vix_threshold=30.88325108,crisis_blend_gain=0.8275881,sector_factor_sigma=0.010617335,sector_loading=0.58821442,mispricing_half_life_days=68.25733542,market_vol_vix_anchor=15.98426471,endogenous_news_sigma=0.020360516"; do
  name="${spec%%:*}"; ov="${spec#*:}"
  ( python tools/calibration/section8_check.py --preset pt-v12 --overrides "$ov" \
      > "/home/ec2-user/out/s8-${name}.txt" 2>&1 \
    || echo "S8 RUN FAILED exit=$?" >> "/home/ec2-user/out/s8-${name}.txt" ) &
done
wait

# 40 workers, not 94. driven_buckets holds a pyarrow bar table per job and
# a higher count is a BrokenProcessPool, not a faster run (runbook trap 17).
for BLK in 201 401 1101; do
  python tools/calibration/driven_buckets.py \
    --candidates /home/ec2-user/candidates.json \
    --seeds 30 --seed-start $BLK --workers 40 \
    --out "/home/ec2-user/out/buckets-${BLK}.json"
done

for spec in "v12::" "s23:pt-v12:market_vol_alpha=0.28035004,market_vol_beta=0.69244622,jump_intensity_idio=0.0068895346,jump_sigma_idio=0.08745117,jump_intensity_market=0.0565753337,jump_vix_coupling=0.2626,idio_sigma_scale=0.59604441,garch_vix_coupling=0.14219611,crisis_vix_threshold=30.88325108,crisis_blend_gain=0.8275881,sector_factor_sigma=0.010617335,sector_loading=0.58821442,mispricing_half_life_days=68.25733542,market_vol_vix_anchor=15.98426471,endogenous_news_sigma=0.020360516"; do
  name="${spec%%:*}"; rest="${spec#*:}"; base="${rest%%:*}"; ov="${rest#*:}"
  if [ -z "$base" ]; then
    python tools/calibration/roster_shapes.py --seeds 30 --workers 94 \
      --out "/home/ec2-user/out/roster-${name}.json" 2>&1 | tee "/home/ec2-user/out/roster-${name}.txt"
  else
    python tools/calibration/roster_shapes.py --seeds 30 --preset "$base" --overrides "$ov" \
      --workers 94 --out "/home/ec2-user/out/roster-${name}.json" 2>&1 | tee "/home/ec2-user/out/roster-${name}.txt"
  fi
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
if [ -f /home/ec2-user/out/s8-s23.txt ] && [ -f /home/ec2-user/out/roster-s23.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
