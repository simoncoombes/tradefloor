#!/bin/bash
# The parameterised gates box (pt-v17 era): one template, launched by
# fleet.py with its placeholder tokens substituted, replacing the
# per-run hand-cloned scripts. One box measures one seed BLOCK for one
# candidate file.
#
# __WHEEL__ is either "pypi:<spec>" (install from PyPI -- valid while the
# branch's engine is bit-identical to the published wheel) or "s3:<key>"
# (a wheel a devcheck run built from the branch; the 12-minute rustup +
# maturin provisioning becomes a 5-second download). The KAT runs either
# way: a wheel that is not the branch's engine fails it, which is the
# point.
#
# Dead-man switch first. Runbook §10.
shutdown -h +__DEADMAN_MIN__

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=__BUCKET_RUN__
BRANCH=__BRANCH__
BLOCK=__BLOCK__
SEEDS=__SEEDS__
WORKERS=__WORKERS__
WHEEL="__WHEEL__"
CANDS_KEY=__CANDS_KEY__

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
BLOCK="$2"
while true; do
  date -u +"%Y-%m-%dT%H:%M:%SZ" > /tmp/stream-alive
  aws s3 cp /tmp/stream-alive "$BUCKET/STREAM-ALIVE-$BLOCK" 2>&1 || echo "STREAM UPLOAD FAILED"
  aws s3 cp /var/log/pretium-run.log "$BUCKET/run-$BLOCK.log" || true
  sleep 45
done
STREAM
chown ec2-user:ec2-user /home/ec2-user/stream.sh
chmod +x /home/ec2-user/stream.sh
setsid nohup /home/ec2-user/stream.sh "$BUCKET" "$BLOCK" >/var/log/pretium-stream.log 2>&1 < /dev/null &

if ! aws s3 cp "s3://dia-test-101631415962-us-east-2-an/pretium-calib/in/__CANDS_KEY__" \
      /home/ec2-user/candidates.json; then
  echo "ABORTING: cannot fetch the candidate list"
  shutdown -h now
  exit 1
fi
chown ec2-user:ec2-user /home/ec2-user/candidates.json

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
    # Keep the wheel's own filename: uv refuses a wheel whose name has
    # no version, and `cp` to wheel.whl renamed it into exactly that.
    WHEEL_KEY="${WHEEL#s3:}"
    WHEEL_FILE="/home/ec2-user/$(basename "$WHEEL_KEY")"
    aws s3 cp "s3://dia-test-101631415962-us-east-2-an/pretium-calib/${WHEEL_KEY}" "$WHEEL_FILE"
    uv pip install "$WHEEL_FILE"
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

# The gate must be able to fail: a wheel that is not this branch's engine
# dies HERE, before any block is spent on it.
python -m pytest tests/test_known_answer.py -q

mkdir -p /home/ec2-user/out
python tools/calibration/gate_batch.py \
  --candidates /home/ec2-user/candidates.json \
  --seeds SEEDS_PLACEHOLDER --seed-start BLOCK_PLACEHOLDER \
  --workers WORKERS_PLACEHOLDER \
  --out "/home/ec2-user/out/gates-BLOCK_PLACEHOLDER.json"
WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|WHEEL_PLACEHOLDER|${WHEEL}|" /home/ec2-user/run.sh
sed -i "s|SEEDS_PLACEHOLDER|${SEEDS}|" /home/ec2-user/run.sh
sed -i "s|BLOCK_PLACEHOLDER|${BLOCK}|g" /home/ec2-user/run.sh
sed -i "s|WORKERS_PLACEHOLDER|${WORKERS}|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh
mkdir -p /home/ec2-user/out
chown ec2-user:ec2-user /home/ec2-user/out

set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

if [ -s "/home/ec2-user/out/gates-${BLOCK}.json" ]; then
  echo "CERTIFIED exit=$STATUS block=$BLOCK" > /tmp/DONE
else
  echo "FAILED exit=$STATUS block=$BLOCK no gates artefact" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run-${BLOCK}.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE-${BLOCK}" || true

shutdown -h now
