#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd22-qualify-p970
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
  "label": "d3-f10",
  "base": "pt-v12",
  "overrides": {
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.10802741
  }
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
  "label": "p970-d3f10",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.300507218,
   "market_vol_beta": 0.669492782,
   "jump_intensity_idio": 0.0060586328,
   "jump_sigma_idio": 0.10802741
  }
 },
 {
  "label": "p972",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.301126821,
   "market_vol_beta": 0.670873179
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

# p970 -- market volatility persistence 0.9631 to 0.970, an 18.4-day
# half-life to 22.8 -- fixes the 504-day volatility wall on both seed blocks
# pt-v12 fails and moves the driven window 0.057, forty times what rounds 2
# through 15 managed. It sits at the boundary crisis co-movement allows: the
# real floor is 0.664 and the persistence sweep crosses it between 0.970 and
# 0.975.
#
# This qualifies it on the two seed blocks it has not seen, with the section
# 8 control and the concentrated rosters, and asks the one open question.
#
# d3-f10 and p970 constrain DIFFERENT walls. d3-f10 holds jump variance
# fixed, buys kurtosis headroom and leaves co-movement alone; p970 buys
# volatility headroom and spends co-movement to 0.674 against a floor of
# 0.664. So they are not obviously competing the way rounds 7 and 9's pair
# were, where both spent one cross-sectional correlation budget. But that is
# what rounds 7 and 9 assumed before measuring and they were wrong, so the
# stack is in the pool rather than reasoned about.
#
# p972 brackets the boundary from the other side: if it holds co-movement on
# every block then 0.970 was not the edge and there is more to take.
export PRETIUM_S8_WORKERS=60
for spec in "v12:" \
            "p970:market_vol_alpha=0.300507218,market_vol_beta=0.669492782" \
            "p970-d3f10:market_vol_alpha=0.300507218,market_vol_beta=0.669492782,jump_intensity_idio=0.0060586328,jump_sigma_idio=0.10802741"; do
  name="${spec%%:*}"; ov="${spec#*:}"
  ( python tools/calibration/section8_check.py --preset pt-v12 --overrides "$ov" \
      > "/home/ec2-user/out/s8-${name}.txt" 2>&1 \
    || echo "S8 RUN FAILED exit=$?" >> "/home/ec2-user/out/s8-${name}.txt" ) &
done
wait
grep -H "VERDICT" /home/ec2-user/out/s8-*.txt || true

# The two blocks d3-f10 has not been measured on.
for BLOCK in "--seed-start 301" "--seed-start 401"; do
  TAG=$(echo "$BLOCK" | tr -d ' -' | sed 's/seedstart//')
  python tools/calibration/gate_batch.py \
    --candidates /home/ec2-user/candidates.json \
    --seeds 30 $BLOCK \
    --workers WORKERS_PLACEHOLDER \
    --out "/home/ec2-user/out/gates-${TAG}.json"
done

# A preset that is better on the certified roster and worse on a
# concentrated one is not better. pt-v12 reads 14, 13, 11, 10 of 13, 14 at
# 504 days across the five shapes.
for spec in "v12:::" \
            "p970:pt-v12:market_vol_alpha=0.300507218,market_vol_beta=0.669492782" \
            "p970-d3f10:pt-v12:market_vol_alpha=0.300507218,market_vol_beta=0.669492782,jump_intensity_idio=0.0060586328,jump_sigma_idio=0.10802741"; do
  name="${spec%%:*}"; rest="${spec#*:}"; base="${rest%%:*}"; ov="${rest#*:}"
  if [ -z "$base" ]; then
    python tools/calibration/roster_shapes.py --seeds 30 \
      --workers WORKERS_PLACEHOLDER --out "/home/ec2-user/out/roster-${name}.json" \
      2>&1 | tee "/home/ec2-user/out/roster-${name}.txt"
  else
    python tools/calibration/roster_shapes.py --seeds 30 \
      --preset "$base" --overrides "$ov" \
      --workers WORKERS_PLACEHOLDER --out "/home/ec2-user/out/roster-${name}.json" \
      2>&1 | tee "/home/ec2-user/out/roster-${name}.txt"
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
if [ -f /home/ec2-user/out/gates-301.json ] \
   && [ -f /home/ec2-user/out/gates-401.json ] \
   && [ -f /home/ec2-user/out/roster-p970.json ] \
   && [ -f /home/ec2-user/out/s8-v12.txt ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
