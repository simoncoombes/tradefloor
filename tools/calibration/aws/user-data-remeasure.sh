#!/bin/bash
# The published-figure gate (RELEASING.md step 4) on one spot box.
#
# Launched by fleet.py in tradefloor-design, which substitutes __BRANCH__,
# __BUCKET_RUN__, __DEADMAN_MIN__ and __REGISTER_KEY__ and refuses to launch
# with a placeholder left over:
#
#   tar czf register.tgz -C "$TRADEFLOOR_DOCS" tools/remeasure/inventory.json \
#       tools/docs/learn/experiments.json tools/docs/learn/preset-records.json
#   python fleet.py upload --file register.tgz --key in/<run>-register.tgz
#   python fleet.py launch --run <run> --user-data <this file> \
#       --type c8g.24xlarge --var BRANCH=<branch> --var DEADMAN_MIN=90 \
#       --var REGISTER_KEY=in/<run>-register.tgz
#
# The register lives in tradefloor-docs, which is private, so the box gets it
# from S3 rather than by cloning. The tarball carries the data files the
# register's bound rows read, laid out as in the docs checkout, because a
# bound row's published value comes from those files and remeasure.py stops
# before measuring when it cannot read them.
#
# Dead-man switch first, before anything that can fail. Runbook §10. 90
# minutes: the 0.8.0 run measured for six minutes at 64 workers, and the rest
# is provisioning and the Rust build.
shutdown -h +__DEADMAN_MIN__

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=__BUCKET_RUN__
BRANCH=__BRANCH__
# The bucket root the upload key is relative to: .../pretium-calib
REGISTER_URL="${BUCKET%/out/*}/__REGISTER_KEY__"
# The repository and the package were both renamed from `pretium` at
# 0.5.x. GitHub redirects the old clone URL, so this script kept
# cloning successfully and failed two lines later at `pip install
# pretium` -- which is why the rename survived here unnoticed until
# the 0.6.1 pass. The bucket path keeps its `pretium-calib` prefix
# deliberately: that is a real S3 location with existing history.
# 64, not 96. remeasure uses a THREAD pool, not processes, so the ceiling is
# how much of the engine runs with the GIL released rather than the core
# count. 64 is eight times the laptop's 8 with headroom left for the serial
# wall-clock groups that follow.
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
  sleep 45
done
STREAM
chown ec2-user:ec2-user /home/ec2-user/stream.sh
chmod +x /home/ec2-user/stream.sh
setsid nohup /home/ec2-user/stream.sh "$BUCKET" >/var/log/pretium-stream.log 2>&1 < /dev/null &

# The register, before anything expensive: a run that builds for ten minutes
# and then finds no register has spent its money on nothing.
mkdir -p /home/ec2-user/docs /home/ec2-user/out
if ! aws s3 cp "$REGISTER_URL" /tmp/register.tgz \
   || ! tar xzf /tmp/register.tgz -C /home/ec2-user/docs \
   || [ ! -f /home/ec2-user/docs/tools/remeasure/inventory.json ]; then
  echo "ABORTING: no register at $REGISTER_URL"
  echo "FAILED no register" > /tmp/DONE
  aws s3 cp /tmp/DONE "$BUCKET/DONE" || true
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
  shutdown -h now
  exit 1
fi
sha256sum /home/ec2-user/docs/tools/remeasure/inventory.json \
  > /home/ec2-user/out/register-sha256.txt
chown -R ec2-user:ec2-user /home/ec2-user/docs /home/ec2-user/out

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
git rev-parse HEAD > /home/ec2-user/out/commit.txt

uv venv --python 3.11 .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
# measures.py imports pyarrow directly and tradefloor.baselines needs numpy.
uv pip install numpy pyarrow maturin pytest
maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall tradefloor

python -c "import numpy, pyarrow, tradefloor; print('PREFLIGHT OK', tradefloor.version())"

# The gate must be able to fail. Four earlier runs in this project's history
# carried a probe calling a function that does not exist, behind a `|| true`
# that swallowed the error, so the check never ran at all.
test -f tools/remeasure/remeasure.py
python -m pytest tests/test_known_answer.py -q

# NO --only. The whole point of this run is that the report on disk was a
# three-of-thirty smoke test wearing a "Full run" header, so a partial here
# would reproduce the defect it exists to correct. remeasure.py now labels a
# partial run as PARTIAL, which is the check that this was a full one.
TRADEFLOOR_DOCS=/home/ec2-user/docs python tools/remeasure/remeasure.py \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out
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
if [ -f /home/ec2-user/out/REPORT.md ] && [ -f /home/ec2-user/out/figures.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS no report" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
