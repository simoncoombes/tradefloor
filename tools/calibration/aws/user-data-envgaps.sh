#!/bin/bash
# envelope.py's hand-written gap figures, re-measured on pt-v20, on one spot
# box. Modelled on user-data-remeasure.sh: dead-man switch, S3 preflight,
# streamed log, build, run, upload, shutdown.
#
# Launched by fleet.py in tradefloor-design, which substitutes __BRANCH__,
# __PIN__, __BUCKET_RUN__, __DEADMAN_MIN__ and __SCRIPTS_KEY__:
#
#   tar czf scripts.tgz scripts        # jobs.sh, decay.py, driven.py
#   python fleet.py upload --file scripts.tgz --key in/<run>-scripts.tgz
#   python fleet.py launch --run <run> --user-data <this file> \
#       --type c8g.24xlarge --var BRANCH=release/0.8.5 --var PIN=<sha> \
#       --var DEADMAN_MIN=60 --var SCRIPTS_KEY=in/<run>-scripts.tgz
#
# The scripts that ran are kept beside the results in
# tools/calibration/results/envgaps-pt-v20-2026-09-24/.
shutdown -h +__DEADMAN_MIN__

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=__BUCKET_RUN__
BRANCH=__BRANCH__
SCRIPTS_URL="${BUCKET%/out/*}/__SCRIPTS_KEY__"

dnf -y install gcc git tar gzip python3.11 python3.11-devel awscli-2

# S3 preflight before anything expensive (a launch without the instance
# profile computes everything and delivers nothing).
echo "preflight $(date -u)" > /tmp/PREFLIGHT-S3
if ! aws s3 cp /tmp/PREFLIGHT-S3 "$BUCKET/PREFLIGHT-S3"; then
  echo "ABORTING: cannot write to $BUCKET"
  shutdown -h now
  exit 1
fi

# Stream while running. `cp`, never `sync`: the role has no ListBucket.
cat > /home/ec2-user/stream.sh <<'STREAM'
#!/bin/bash
BUCKET="$1"
while true; do
  date -u +"%Y-%m-%dT%H:%M:%SZ" > /tmp/stream-alive
  aws s3 cp /tmp/stream-alive "$BUCKET/STREAM-ALIVE" 2>&1 || echo "STREAM UPLOAD FAILED"
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
  for f in /home/ec2-user/out/*.log /home/ec2-user/out/status.txt; do
    [ -f "$f" ] && aws s3 cp "$f" "$BUCKET/" >/dev/null 2>&1
  done
  sleep 45
done
STREAM
chmod +x /home/ec2-user/stream.sh
setsid nohup /home/ec2-user/stream.sh "$BUCKET" >/var/log/pretium-stream.log 2>&1 < /dev/null &

# The scripts, before the build: a run that builds and then finds nothing to
# run has spent its money on nothing.
mkdir -p /home/ec2-user/out
if ! aws s3 cp "$SCRIPTS_URL" /home/ec2-user/scripts.tgz \
   || ! tar xzf /home/ec2-user/scripts.tgz -C /home/ec2-user \
   || [ ! -f /home/ec2-user/scripts/jobs.sh ]; then
  echo "ABORTING: no scripts at $SCRIPTS_URL"
  echo "FAILED no scripts" > /tmp/DONE
  aws s3 cp /tmp/DONE "$BUCKET/DONE" || true
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
  shutdown -h now
  exit 1
fi
sha256sum /home/ec2-user/scripts.tgz > /home/ec2-user/out/scripts-sha256.txt
aws s3 cp /home/ec2-user/scripts.tgz "$BUCKET/scripts-as-run.tgz" || true
chown -R ec2-user:ec2-user /home/ec2-user/scripts /home/ec2-user/scripts.tgz /home/ec2-user/out

cat > /home/ec2-user/run.sh <<'WORK'
#!/bin/bash
set -euxo pipefail
cd /home/ec2-user
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

git clone --branch BRANCH_PLACEHOLDER https://github.com/simoncoombes/tradefloor.git src
cd src
git checkout --detach PIN_PLACEHOLDER
git rev-parse HEAD > /home/ec2-user/out/commit.txt

uv venv --python 3.11 .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
uv pip install numpy pyarrow maturin pytest
maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall tradefloor
python -c "import numpy, pyarrow, tradefloor; print('PREFLIGHT OK', tradefloor.version(), tradefloor.model_preset()['name'])"

# The gate must be able to fail: the build has to reproduce the committed
# known answer before anything it measures is worth reading.
python tests/known_answer.py 2>&1 | tee /home/ec2-user/out/known-answer.txt
python -m pytest tests/test_known_answer.py -q 2>&1 | tail -5 | tee /home/ec2-user/out/gate.txt

export REPO=$PWD SCR=/home/ec2-user/scripts OUT=/home/ec2-user/out
bash "$SCR/jobs.sh" 2>&1 | tee $OUT/jobs.log
WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|PIN_PLACEHOLDER|__PIN__|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh

set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

# The marker names THIS run's artefacts.
if [ -f /home/ec2-user/out/long-horizon.json ] && [ -f /home/ec2-user/out/decay.json ] \
   && [ -f /home/ec2-user/out/driven.json ] && [ -f /home/ec2-user/out/macro.json ] \
   && [ -f /home/ec2-user/out/memory-vs-drift.json ]; then
  echo "OK exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS missing artefacts, see run.log" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
