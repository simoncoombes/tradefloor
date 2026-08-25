#!/bin/bash
# Dead-man switch first, before anything that can fail. §10 of the runbook:
# two failed launches cost six cents precisely because this line ran first.
shutdown -h +90

exec > >(tee /var/log/pretium-run.log) 2>&1
set -x

BUCKET=s3://dia-test-101631415962-us-east-2-an/pretium-calib/out/jumpsplit
BRANCH=feat/jump-momentum-decoupling

dnf -y install gcc git tar gzip python3.11 python3.11-devel awscli-2

cat > /home/ec2-user/run.sh <<'WORK'
#!/bin/bash
# set -e INSIDE the work block. Launch 1 of the previous run wrote CERTIFIED
# over a search that never happened, because every step failed, execution
# continued, and the last line was the marker. The marker must describe what
# happened, not that the script reached the end.
set -euxo pipefail

cd /home/ec2-user
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

curl -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"

# The repository is public now, so clone the branch rather than shipping a
# presigned tarball. That also removes the runbook's two-worktree trap: the
# engine change and the tooling change are the same commit here.
git clone --depth 1 --branch BRANCH_PLACEHOLDER \
    https://github.com/simoncoombes/pretium.git src
cd src

uv venv --python 3.11 .venv
export VIRTUAL_ENV=$PWD/.venv
export PATH="$VIRTUAL_ENV/bin:$PATH"
# numpy AND pyarrow: facts.measure reads the daily bars through Arrow, which
# launch 2 of the previous run discovered after a full Rust build.
uv pip install numpy pyarrow maturin pytest
maturin build --release --out dist --features python
uv pip install --no-index --find-links dist --force-reinstall pretium

# Preflight: a missing dependency should cost seconds, not a Rust build.
python -c "import numpy, pyarrow, pretium; print('PREFLIGHT OK', pretium.version())"

# Determinism gate before the search. A Graviton build that disagrees with
# macos-arm64 invalidates everything downstream of it.
# The determinism gate, and it must be able to FAIL. The first four runs
# carried a probe calling a function that does not exist, printing "KAT got
# None", and a pytest invocation with no pytest installed whose "|| true"
# swallowed the ModuleNotFoundError. Four runs of numbers were trusted
# without the check that was there to guard them.
python -m pytest tests/test_known_answer.py -q

# Warm start from the confirmed candidate: pt-v4 with the jump decoupled.
python - <<'CERT'
import json, pathlib, pretium as pt
settable = set(pt.ModelParams.settable())
v4 = pt.ModelParams.from_preset("pt-v4").to_dict()
vec = {k: v for k, v in v4.items() if k in settable}
vec["jump_momentum_share"] = 0.0
pathlib.Path("start.json").write_text(json.dumps({"best_vector": vec}))
print("warm start written, overrides:", len(vec))
CERT

mkdir -p /home/ec2-user/out
python tools/calibration/calibrate.py \
  --params PARAMS_PLACEHOLDER \
  --start-from start.json \
  --dual-horizon 504 \
  --workers 94 \
  --out /home/ec2-user/out/calibrate-jump-split.json
WORK

sed -i "s|BRANCH_PLACEHOLDER|${BRANCH}|" /home/ec2-user/run.sh
sed -i "s|PARAMS_PLACEHOLDER|jump_intensity_market,jump_intensity_idio,jump_mean_market,jump_sigma_market,jump_sigma_idio,volume_persistence,volume_innovation_sigma,volume_variance_gain,market_factor_sigma,jump_momentum_share|" /home/ec2-user/run.sh
chown ec2-user:ec2-user /home/ec2-user/run.sh
chmod +x /home/ec2-user/run.sh

# Run as ec2-user so uv lands in /home/ec2-user/.local/bin rather than root's.
set +e
sudo -u ec2-user bash /home/ec2-user/run.sh
STATUS=$?
set -e

# The certificate's existence on disk is the test, not the script reaching
# its last line.
if [ -f /home/ec2-user/out/calibrate-jump-split.json ]; then
  echo "CERTIFIED exit=$STATUS" > /tmp/DONE
else
  echo "FAILED exit=$STATUS no certificate" > /tmp/DONE
fi

aws s3 cp /var/log/pretium-run.log "$BUCKET/run.log" || true
aws s3 cp /tmp/DONE "$BUCKET/DONE" || true
aws s3 cp /home/ec2-user/out/calibrate-jump-split.json "$BUCKET/" || true
aws s3 cp /home/ec2-user/src/start.json "$BUCKET/" || true

shutdown -h now
