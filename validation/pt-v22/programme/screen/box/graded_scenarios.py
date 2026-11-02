"""graded_scenarios.py TOOL [ARGS ...]: run a grade tool on the scenario files the rows were banded on.

The registered SF, S and C4b rows were banded on the pt-v20 grade's engine
(sim/r21 caaa4c6e), whose packaged scenarios were reshaped: credit written on
the corporate spread, not held on the yield's level. The pt-v21 base ships
dev's packaged files, in which oil_price_spike, policy_regime_shift,
curve_shock, rate_shock and recession hold the corporate yield's level, so its
daily sd after onset reads 0.00 bp and SF5 fails on every arm (box s1a,
2026-10-04; on the desk, R20M curve and oil: 0.00 on the base's files against
4.3 to 8.8 bp on caaa4c6e's).

This runs TOOL with the graded files in place of the packaged ones, and
nothing else changed:

* `tradefloor.Scenario.load(name)` reads `graded-scenarios/NAME.yml` beside
  this file when it exists (shim/sitecustomize.py, so worker processes get
  it too);
* REPO, which desk_sf.py builds its scenario paths from, points at an
  overlay whose python/tradefloor/scenarios is graded-scenarios/ and whose
  tools/ is the engine checkout's;
* recession_rows.py's output is corrected afterwards to name the graded file
  and its sha256, since the tool records the packaged path it believes it read.

graded-scenarios/SOURCE.json records the engine ref and every file's sha256.
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
GRADED = HERE / "graded-scenarios"


def overlay(repo: pathlib.Path) -> pathlib.Path:
    root = pathlib.Path(os.environ.get("TMPDIR", "/tmp")) / f"graded-overlay-{os.getpid()}"
    (root / "python" / "tradefloor").mkdir(parents=True, exist_ok=True)
    link = root / "python" / "tradefloor" / "scenarios"
    if not link.exists():
        link.symlink_to(GRADED)
    if not (root / "tools").exists():
        (root / "tools").symlink_to(repo / "tools")
    return root


def main(argv: list[str]) -> int:
    tool, args = pathlib.Path(argv[0]).resolve(), argv[1:]
    repo = pathlib.Path(os.environ.get("REPO", ".")).resolve()
    env = dict(os.environ)
    env["GRADED_SCENARIOS"] = str(GRADED)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(HERE / "shim")] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p])
    env["REPO"] = str(overlay(repo))
    # A child process, not this one: every worker it spawns starts with the
    # same environment, so each loads the shim, on fork or on spawn.
    rc = subprocess.run([sys.executable, str(tool), *args], env=env).returncode
    if rc == 0 and tool.name == "recession_rows.py" and args:
        # It records the packaged file it believes it loaded; record the one it did.
        out = pathlib.Path(args[0])
        d = json.loads(out.read_text())
        name = d.get("scenario", "recession")
        f = GRADED / f"{name}.yml"
        d["scenario_file"] = str(f)
        d["scenario_sha256"] = hashlib.sha256(f.read_bytes()).hexdigest()
        d["scenario_graded_from"] = json.loads((GRADED / "SOURCE.json").read_text())["engine_ref"]
        out.write_text(json.dumps(d, indent=1, default=float))
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
