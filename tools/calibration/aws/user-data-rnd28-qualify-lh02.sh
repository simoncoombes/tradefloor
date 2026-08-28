#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd28-qualify-lh02
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
if ! aws s3 cp "s3://dia-test-101631415962-us-east-2-an/pretium-calib/in/cands-rnd28.json" \
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

# lh02 clears four of five seed blocks against d3-f10's three and pt-v12's
# two, and it is the first vector to beat d3-f10 on anything without losing
# something else. Across the five blocks it reads crisis co-movement
# 0.677-0.720, inside its real range everywhere, where pt-v12 BREACHES the
# ceiling at 0.728 on block 301; crisis lever 5.90-6.12 against pt-v12's
# 5.80-6.12; and a driven ratio of 1.490 against 1.551.
#
# It gets most of p970's driven-window gain without p970's lever
# regression, because it pairs the persistence that costs the lever with
# the funded jump coupling that buys it back -- the interaction round 24's
# survey found and hand search never tried.
#
# Its single miss is block 501 on `abs_return_acf5`, which pt-v12 and
# d3-f10 miss there too, so that is a property of the block rather than of
# the candidate.
#
# This qualifies it on the three things the gate does not cover: the
# section 8 overfitting control with the base beside it, the concentrated
# rosters, and the bucketed driven comparison -- the last because the gate
# reports one aggregate ratio and round 17 showed the defect is half a flat
# gain error and half a VIX 30-45 spike. Whether lh02 fixes the spike or
# only lowers the level is not visible in any number measured so far.
export PRETIUM_S8_WORKERS=44
for spec in "v12:" \
            "lh02:market_vol_alpha=0.300730582,market_vol_beta=0.66999041,jump_intensity_idio=0.0068895346,jump_sigma_idio=0.08369236,jump_intensity_market=0.0565753337,jump_vix_coupling=0.2626,idio_sigma_scale=0.5688,garch_vix_coupling=0.0269" \
            "lh16:market_vol_alpha=0.300324795,market_vol_beta=0.669086366,jump_intensity_idio=0.0059752957,jump_sigma_idio=0.09631724,jump_intensity_market=0.0606177349,jump_vix_coupling=0.1807,idio_sigma_scale=0.5011,garch_vix_coupling=0.3772"; do
  name="${spec%%:*}"; ov="${spec#*:}"
  ( python tools/calibration/section8_check.py --preset pt-v12 --overrides "$ov" \
      > "/home/ec2-user/out/s8-${name}.txt" 2>&1 \
    || echo "S8 RUN FAILED exit=$?" >> "/home/ec2-user/out/s8-${name}.txt" ) &
done
wait
grep -H "VERDICT" /home/ec2-user/out/s8-*.txt || true

# 40 workers, not 190: driven_buckets holds a bar table per job (trap 17).
python tools/calibration/driven_buckets.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 --seed-start 201 --workers 40 \
  --out /home/ec2-user/out/buckets-201.json

for spec in "v12:::" \
            "lh02:pt-v12:market_vol_alpha=0.300730582,market_vol_beta=0.66999041,jump_intensity_idio=0.0068895346,jump_sigma_idio=0.08369236,jump_intensity_market=0.0565753337,jump_vix_coupling=0.2626,idio_sigma_scale=0.5688,garch_vix_coupling=0.0269" \
            "lh16:pt-v12:market_vol_alpha=0.300324795,market_vol_beta=0.669086366,jump_intensity_idio=0.0059752957,jump_sigma_idio=0.09631724,jump_intensity_market=0.0606177349,jump_vix_coupling=0.1807,idio_sigma_scale=0.5011,garch_vix_coupling=0.3772"; do
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
if [ -f /home/ec2-user/out/s8-v12.txt ] && [ -f /home/ec2-user/out/roster-lh02.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
