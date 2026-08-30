#!/bin/bash
# Dead-man switch first, before anything that can fail. Runbook §10.
#
# 90 minutes. The measurement itself is about 2.8 core-hours, which is under
# a minute on 94 workers; everything else is provisioning and the Rust
# build, which have measured at 12 and 10 minutes. 90 leaves room for a slow
# build without leaving a 96-core box running on a job that has hung.
shutdown -h +90

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/preset-panel-060
# The release branch: this run measures the panel for the preset 0.6.0 makes
# the default, and `envelope.py` on that branch is what the numbers land in.
BRANCH=release/0.6.0
# 94, not 96. Two cores left for the streamer and the OS; a pool sized to
# the full core count starves its own parent and the progress line stops.
WORKERS=94
# pt-v14 is measured beside pt-v16 on purpose. The envelope needs the new
# default, and the changelog needs the outgoing one to state the move
# honestly: without both, a reader whose numbers changed has no comparison.
PRESETS=pt-v14,pt-v16

dnf -y install gcc git tar gzip python3.11 python3.11-devel awscli-2

# S3 PREFLIGHT before anything expensive. Trap 6: a launch without
# --iam-instance-profile computes perfectly and delivers nothing, and looks
# healthy from outside until the certificate never appears.
echo "preflight $(date -u)" > /tmp/PREFLIGHT-S3
if ! aws s3 cp /tmp/PREFLIGHT-S3 "$BUCKET/PREFLIGHT-S3"; then
  echo "ABORTING: cannot write to $BUCKET. Almost certainly a missing"
  echo "--iam-instance-profile Name=pretium-calib-profile on run-instances."
  shutdown -h now
  exit 1
fi

# Stream progress while the run is going, not after it. `cp`, never `sync`:
# the role has PutObject/GetObject and no ListBucket, so sync lists the
# destination and fails silently (trap 7).
cat > /home/ec2-user/stream.sh <<'STREAM'
#!/bin/bash
BUCKET="$1"
while true; do
  date -u +"%Y-%m-%dT%H:%M:%SZ" > /tmp/stream-alive
  aws s3 cp /tmp/stream-alive "$BUCKET/STREAM-ALIVE" 2>&1 || echo "STREAM UPLOAD FAILED"
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
  [ -f /home/ec2-user/out/preset-panel.json ] && \
    aws s3 cp /home/ec2-user/out/preset-panel.json "$BUCKET/partial/preset-panel.json" || true
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

# The repository and the package are both `tradefloor` since 0.5.0. This
# script still said `pretium` in three places until 0.6.0, which would have
# failed at the install rather than the clone, because GitHub redirects a
# renamed repository and PyPI does not rename a wheel.
git clone --depth 1 --branch BRANCH_PLACEHOLDER \
    https://github.com/simoncoombes/tradefloor.git src
cd src

uv venv --python 3.11 .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
# numpy AND pyarrow: facts.measure reads the daily bars through Arrow.
uv pip install numpy pyarrow maturin pytest
maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall tradefloor

# A missing dependency should cost seconds, not a Rust build.
python -c "import numpy, pyarrow, tradefloor; print('PREFLIGHT OK', tradefloor.version())"

# The default this run exists to measure. A box that built the wrong branch
# would produce a clean-looking panel for the previous default.
python -c "
import tradefloor as tf, sys
name = tf.model_preset()['name']
print('DEFAULT PRESET', name, 'VERSION', tf.version())
sys.exit(0 if name == 'pt-v16' else 'expected pt-v16 as the default')
"

# The tool has to exist on the branch we cloned. Trap 16: a run once died on
# `unknown model parameter` because the box cloned a branch that did not
# carry the code, and that failure arrived forty minutes in.
test -f tools/calibration/preset_panel.py

# Determinism gate before the measurement. A Graviton build that disagrees
# with macos-arm64 invalidates every number downstream of it.
python -m pytest tests/test_known_answer.py -q

mkdir -p /home/ec2-user/out

python tools/calibration/preset_panel.py \
  --only PRESETS_PLACEHOLDER \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/preset-panel.json
WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|WORKERS_PLACEHOLDER|${WORKERS}|" /home/ec2-user/run.sh
sed -i "s|PRESETS_PLACEHOLDER|${PRESETS}|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh

# Run as ec2-user so uv lands in /home/ec2-user/.local/bin rather than root's.
set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

# The artefact's existence on disk is the test, and the marker names THIS
# run's artefact rather than some previous run's.
if [ -f /home/ec2-user/out/preset-panel.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS no preset-panel.json" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /var/log/pretium-stream.log "$BUCKET/stream.log" || true
aws s3 cp /home/ec2-user/out/preset-panel.json "$BUCKET/" || true
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
