#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The 0.2.0 run took 473s at 8 workers on a laptop; this runs the
# concurrent groups at 64 and the wall-clock groups serially afterwards, so
# the work is minutes. The rest is provisioning and the Rust build.
shutdown -h +100

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/rnd20-transient
# `dev`, and the run needs eb44efa or later: gate_batch and macro_range only
# learned --seed-start there. Without it both silently re-measure on the
# calibration seeds and the run confirms nothing while looking identical.
# Verified with `git show origin/dev:...` before launch, runbook trap 16.
BRANCH=dev
# 64, not 96. remeasure uses a THREAD pool, not processes, so the ceiling is
# how much of the engine runs with the GIL released rather than the core
# count. 64 is eight times the laptop's 8 with headroom left for the serial
# wall-clock groups that follow.
# 40, not 190. driven_buckets holds a full pyarrow bar table per job -- 504
# days x 40 instruments -- where a panel job holds a summary, so it cannot
# use the worker count the gate launchers inherit. At 190 on a 96-vCPU box
# the pool died with BrokenProcessPool partway through the first candidate
# and the run delivered nothing. This is the second resource ceiling in this
# programme reached by copying a launcher rather than sizing one.
WORKERS=40

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
  "label": "p0963",
  "base": "pt-v12",
  "overrides": {}
 },
 {
  "label": "p0900",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.27882113,
   "market_vol_beta": 0.62117887
  }
 },
 {
  "label": "p0940",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.29121318,
   "market_vol_beta": 0.64878682
  }
 },
 {
  "label": "p0980",
  "base": "pt-v12",
  "overrides": {
   "market_vol_alpha": 0.30360523,
   "market_vol_beta": 0.67639477
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

# Round 19 ruled out both static candidates and left a tell. Raising
# `market_vol_vix_coupling` lifted VIX 30-45 by +4.36 and 45+ by only
# +2.70, and a pure LEVEL effect compounds with VIX so it would lift the
# top bucket most. Lifting the middle most says the 45+ days never reach
# their target: the top is DYNAMICALLY limited.
#
# `market_vol_alpha + market_vol_beta` is 0.9631, an 18.4-day half-life,
# and the 2020-21 path spends 23 sessions above VIX 45 -- about one
# half-life. The model cannot converge on a spike that short.
# `scenario_response.py` has said the same from the other side since the
# pt-v3 boundary: steady-state lever 95.2% retained, transient 27.6%.
#
# Persistence is swept by scaling alpha and beta together, which holds
# their ratio and therefore the GARCH's shape, and moves only the speed.
#
# THE DISCRIMINATING PREDICTION, and why both measurements run here. If the
# 45+ shortfall is transient, lowering persistence raises the DRIVEN 45+
# bucket and leaves the HELD sweep alone -- a held run has 252 days to
# converge and does not care how fast it got there. If both move, then
# persistence is changing the steady state too and the level-versus-
# transient separation this programme has drawn since round 15 is wrong.
# One instance, both rulers, so the answer cannot be assembled from two
# runs that differ in something else.
python tools/calibration/driven_buckets.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 --seed-start 201 \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/driven-201.json

# The held sweep is the control: a steady state should be indifferent to how
# fast it was reached. 190 workers is safe here -- vix_transfer holds a
# panel summary per job, not a bar table (trap 17).
python tools/calibration/vix_transfer.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds 30 --seed-start 201 \
  --workers 190 \
  --out /home/ec2-user/out/held-201.json

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
if [ -f /home/ec2-user/out/driven-201.json ] && [ -f /home/ec2-user/out/held-201.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS incomplete artefacts" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
