#!/bin/bash
# devcheck — the per-change gate, on a spot box, because nothing builds
# locally in the pt-v17 era (round 149's standing order). One instance,
# ~$0.20-0.40: full Rust build, `cargo test --workspace --all-targets`
# (the 0.6.0 crates failure was an integration test the local filter never
# compiled — run what the package will run), the Python suite, and the
# known-answer digest printed where the console watcher can read it.
#
# Launched by fleet.py, which substitutes __BRANCH__ / __BUCKET_RUN__ /
# __DEADMAN_MIN__ and refuses to launch with a placeholder left over.
#
# Dead-man switch first, before anything that can fail. Runbook §10.
shutdown -h +__DEADMAN_MIN__

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=__BUCKET_RUN__
BRANCH=__BRANCH__

dnf -y install gcc git tar gzip python3.11 python3.11-devel awscli-2

# S3 PREFLIGHT before anything expensive. Trap 6: a launch without
# --iam-instance-profile computes perfectly and delivers nothing.
echo "preflight $(date -u)" > /tmp/PREFLIGHT-S3
if ! aws s3 cp /tmp/PREFLIGHT-S3 "$BUCKET/PREFLIGHT-S3"; then
  echo "ABORTING: cannot write to $BUCKET (missing instance profile?)"
  shutdown -h now
  exit 1
fi

# Stream while the run is going. `cp`, never `sync` (trap 7: no ListBucket).
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
uv pip install numpy pyarrow maturin pytest

# Stage 1: the crate's own tests, every target. This is what the packaged
# crate compiles; a struct that gained a field fails HERE, not on a tag.
( cd rust && cargo test --workspace --all-targets --release ) \
    2>&1 | tee /home/ec2-user/out/cargo-test.log
echo "cargo-test exit ${PIPESTATUS[0]}" | tee -a /home/ec2-user/out/cargo-test.log

maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall tradefloor
python -c "import numpy, pyarrow, tradefloor; print('PREFLIGHT OK', tradefloor.version())"
# The wheel is the run's build artefact: gate boxes install THIS file from
# S3 instead of spending twelve minutes rebuilding it per box per wave.
cp dist/*.whl /home/ec2-user/out/

# Stage 2: the known answer, printed for the console watcher and kept as an
# artefact. A digest that moved when no engine change was intended is the
# whole conversation.
python tests/known_answer.py 2>&1 | tee /home/ec2-user/out/kat.txt

# Stage 3: the Python suite.
python -m pytest tests/ -q 2>&1 | tee /home/ec2-user/out/pytest.log

WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh
mkdir -p /home/ec2-user/out
chown ec2-user:ec2-user /home/ec2-user/out

set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

# The artefacts' existence is the test, and the marker names THIS run's
# artefacts. All three stages present + exit 0 = CERTIFIED.
if [ "$STATUS" -eq 0 ] && [ -s /home/ec2-user/out/kat.txt ] \
   && [ -s /home/ec2-user/out/cargo-test.log ] && [ -s /home/ec2-user/out/pytest.log ]; then
  echo "CERTIFIED exit=$STATUS branch=$BRANCH" > /tmp/DONE
else
  echo "FAILED exit=$STATUS branch=$BRANCH see cargo-test.log/pytest.log" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
for f in /home/ec2-user/out/*; do aws s3 cp "$f" "$BUCKET/" || true; done
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true

shutdown -h now
