#!/bin/bash
# The parameterised fear-statistics box (pt-v17 era): fear_screen.py on a
# named --grid, wheel provisioning as the gates template. The fear stats
# (P(VIX>30), same-day corr, rv-tracking, spike asym, AR1) are the
# instruments the panels cannot see; a mechanism screen that touches the
# VIX runs one of these beside its card boxes.
#
# Dead-man switch first. Runbook §10.
shutdown -h +__DEADMAN_MIN__

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=__BUCKET_RUN__
BRANCH=__BRANCH__
GRID=__GRID__
WORKERS=__WORKERS__
WHEEL="__WHEEL__"

dnf -y install git tar gzip python3.11 python3.11-devel awscli-2

echo "preflight $(date -u)" > /tmp/PREFLIGHT-S3
if ! aws s3 cp /tmp/PREFLIGHT-S3 "$BUCKET/PREFLIGHT-S3"; then
  echo "ABORTING: cannot write to $BUCKET (missing instance profile?)"
  shutdown -h now
  exit 1
fi

cat > /home/ec2-user/stream.sh <<'STREAM'
#!/bin/bash
BUCKET="$1"
while true; do
  date -u +"%Y-%m-%dT%H:%M:%SZ" > /tmp/stream-alive
  aws s3 cp /tmp/stream-alive "$BUCKET/STREAM-ALIVE-fear" 2>&1 || echo "STREAM UPLOAD FAILED"
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run-fear.log" || true
  sleep 45
done
STREAM
chown ec2-user:ec2-user /home/ec2-user/stream.sh
chmod +x /home/ec2-user/stream.sh
setsid nohup /home/ec2-user/stream.sh "$BUCKET" >/var/log/pretium-stream.log 2>&1 < /dev/null &

cat > /home/ec2-user/run.sh <<'WORK'
#!/bin/bash
set -euxo pipefail
cd /home/ec2-user
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

git clone --depth 1 --branch BRANCH_PLACEHOLDER \
    https://github.com/simoncoombes/tradefloor.git src
cd src
git rev-parse HEAD > /home/ec2-user/out/commit.txt

uv venv --python 3.11 .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
uv pip install numpy pyarrow pytest

WHEEL="WHEEL_PLACEHOLDER"
case "$WHEEL" in
  s3:*)
    aws s3 cp "s3://dia-test-101631415962-us-east-2-an/pretium-calib/${WHEEL#s3:}" /home/ec2-user/wheel.whl
    uv pip install /home/ec2-user/wheel.whl
    ;;
  pypi:*)
    uv pip install "${WHEEL#pypi:}"
    ;;
  build)
    curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
    uv pip install maturin
    maturin build --release --out dist --features python
    uv pip install --no-index --find-links dist --force-reinstall tradefloor
    ;;
  *)
    echo "unknown wheel spec: $WHEEL"; exit 1;;
esac

python -c "import numpy, pyarrow, tradefloor; print('PREFLIGHT OK', tradefloor.version())"
python -m pytest tests/test_known_answer.py -q

mkdir -p /home/ec2-user/out
python tools/calibration/fear_screen.py \
  --grid GRID_PLACEHOLDER \
  --workers WORKERS_PLACEHOLDER \
  --out /home/ec2-user/out/fear-GRID_PLACEHOLDER.json
WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|WHEEL_PLACEHOLDER|${WHEEL}|" /home/ec2-user/run.sh
sed -i "s|GRID_PLACEHOLDER|${GRID}|g" /home/ec2-user/run.sh
sed -i "s|WORKERS_PLACEHOLDER|${WORKERS}|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh
mkdir -p /home/ec2-user/out
chown ec2-user:ec2-user /home/ec2-user/out

set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

if [ -s "/home/ec2-user/out/fear-${GRID}.json" ]; then
  echo "CERTIFIED exit=$STATUS grid=$GRID" > /tmp/DONE
else
  echo "FAILED exit=$STATUS grid=$GRID no fear artefact" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run-fear.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE-fear" || true

shutdown -h now
