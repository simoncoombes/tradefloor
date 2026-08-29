"""`plan` and `run` must agree about how big the survey is.

`--samples` is a single top-level argument and both subcommands read it, so
`plan --samples 1000` followed by a bare `run` builds a DIFFERENT plan at the
tool's default of 4000. Until 2026-08-25 `plan` wrote nothing to disk, so
nothing could notice: the forecast said "vectors 1000, tasks 48000, ~71
core-hours", the run did 4000 vectors and 192000 tasks, and the two plan
fingerprints were printed one line apart in the same log and read by nobody.

That is not a cosmetic mismatch. The operator sizes the dead-man switch off
the forecast, and the first survey launch was killed by its own
`shutdown -h +90` at 63.9% complete, having been forecast at a quarter of what
it ran. Four times the work, a quarter of the budget, and 91 minutes of a
96-core box produced one log file and no measurement.

`cmd_run` already had exactly the right refusal, comparing an existing
`meta.json`'s fingerprint against the configuration it was asked for. It never
fired because there was no `meta.json` to compare against. `plan --out` now
writes one.
"""

from __future__ import annotations

import json
import pathlib
import subprocess
import sys

TOOL = (pathlib.Path(__file__).resolve().parent.parent
        / "tools" / "calibration" / "atlas_survey.py")


def _run(*args: str, cwd: pathlib.Path | None = None):
    return subprocess.run([sys.executable, str(TOOL), *args],
                          capture_output=True, text=True, timeout=300, cwd=cwd)


def test_plan_writes_the_plan_it_forecast(tmp_path: pathlib.Path) -> None:
    """Without this file on disk the mismatch below cannot be detected."""
    out = tmp_path / "survey"
    proc = _run("plan", "--samples", "8", "--out", str(out))
    assert proc.returncode == 0, proc.stderr[-800:]

    meta = out / "meta.json"
    assert meta.exists(), (
        "plan --out wrote no meta.json, so a run at a different --samples has "
        "nothing to disagree with and will silently measure a different plan"
    )
    doc = json.loads(meta.read_text())
    assert doc["samples"] == 8
    assert doc["plan_fingerprint"] in proc.stdout


def test_run_refuses_a_different_sample_count(tmp_path: pathlib.Path) -> None:
    """The refusal that the 2026-08-25 survey needed and did not get.

    `run` here deliberately omits `--samples`, as the AWS
    launcher did, so it falls back to the tool default and builds a different
    plan.
    """
    out = tmp_path / "survey"
    assert _run("plan", "--samples", "8", "--out", str(out)).returncode == 0

    proc = _run("run", "--out", str(out), "--workers", "2")
    assert proc.returncode == 2, (
        "run at the default sample count did not refuse against a meta.json "
        f"written for 8. It exited {proc.returncode} and would have measured "
        "a different plan than the one forecast.\n\n" + proc.stdout[-800:]
    )
    assert "REFUSING" in proc.stdout


def test_plan_twice_at_the_same_size_is_fine(tmp_path: pathlib.Path) -> None:
    """The guard must not make re-planning an identical survey an error."""
    out = tmp_path / "survey"
    assert _run("plan", "--samples", "8", "--out", str(out)).returncode == 0
    again = _run("plan", "--samples", "8", "--out", str(out))
    assert again.returncode == 0, (
        "re-planning the same survey into the same directory was refused; the "
        "guard is meant to catch a CHANGED plan, not a repeated one.\n\n"
        + again.stdout[-500:]
    )
