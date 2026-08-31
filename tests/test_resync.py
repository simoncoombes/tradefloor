"""The inventory re-pointer must not report clean over a subject it cannot see.

`RELEASING.md` step 4b tells a releaser to run `resync.py --report` before
believing a MOVED row. At 0.6.1 that instruction returned "0 / 0 / 0" against
an inventory whose pages had been gone since 0.5.0, because two things were
wrong at once and each hid the other.

The default `--figures` was a pinned `out-0.3.0/figures.json`, a run whose
gate had zero MOVED rows. So the tool walked no rows and opened no pages,
whatever state the inventory was in. Pointed at a real run it did the
opposite and died on the first missing page, which is not a report either.

Both are the same failure as the three-of-thirty smoke test that wore a
"Full run" header: a check that cannot fail teaches the releaser to skim it.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RESYNC = REPO / "tools" / "remeasure" / "resync.py"


def test_the_default_figures_path_is_not_a_pinned_release():
    """A default naming one release's output can only ever describe that one.

    `out-0.3.0/figures.json` sat here through three releases. Because that
    run finished with zero MOVED rows, the tool had nothing to walk and said
    so, which reads exactly like an inventory in good order.
    """
    source = RESYNC.read_text(encoding="utf-8")
    match = re.search(r'"--figures",\s*default="([^"]+)"', source)
    assert match, "resync.py no longer declares a --figures default"
    default = match.group(1)
    assert not re.search(r"out-\d+\.\d+\.\d+/", default), (
        f"--figures defaults to {default!r}, which pins one release's report. "
        "Point it at the current run instead."
    )


def _run(tmp_path: Path, page: str) -> subprocess.CompletedProcess:
    """Drive resync against one MOVED row citing `page`."""
    figures = tmp_path / "figures.json"
    figures.write_text(json.dumps({"figures": [
        {"id": "spec.rebalance_mid", "status": "MOVED", "measured": 6.07},
    ]}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(RESYNC), "--report", "--figures", str(figures)],
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )


def test_an_unreadable_page_is_reported_rather_than_raised(tmp_path):
    """A page no root holds is a fact about the inventory, not a crash.

    Raising ended the run at the first missing page, so a releaser saw a
    traceback rather than the count of rows the gate could not adjudicate.
    """
    done = _run(tmp_path, "docs/model-presets.md")
    assert "Traceback" not in done.stderr, done.stderr[-1200:]
    assert "no root holds" in done.stdout, done.stdout[-1200:]


def test_a_wholly_blind_report_does_not_exit_zero(tmp_path):
    """Every row unreadable means the tool saw nothing, which is not clean.

    Exiting zero here is how a gate becomes decoration: the release checklist
    ticks, and nothing has been checked.
    """
    done = _run(tmp_path, "docs/model-presets.md")
    assert done.returncode != 0, (
        "resync reported success having read none of the pages it was asked "
        f"about. stdout:\n{done.stdout[-1200:]}"
    )
    assert "MOVED rows considered" in done.stdout, done.stdout[-1200:]
