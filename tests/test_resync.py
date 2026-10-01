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


def _register(tmp_path: Path, rows: list[dict]) -> Path:
    """A register of our own, inside a documentation checkout of our own.

    These tests used to read whatever register the repository carried, so
    they tested that register's contents as much as the tool.
    """
    path = tmp_path / "docs-repo" / "tools" / "remeasure" / "inventory.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"figures": rows}), encoding="utf-8")
    return path


def _run(tmp_path: Path, page: str) -> subprocess.CompletedProcess:
    """Drive resync against one MOVED row citing `page`."""
    register = _register(tmp_path, [{
        "id": "spec.rebalance_mid", "file": page, "line": 1,
        "label": "the same signal rebalanced six times a day",
        "published": 9.79, "compare": {"kind": "round", "decimals": 2},
    }])
    figures = tmp_path / "figures.json"
    figures.write_text(json.dumps({"figures": [
        {"id": "spec.rebalance_mid", "status": "MOVED", "measured": 6.07},
    ]}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(RESYNC), "--inventory", str(register),
         "--report", "--figures", str(figures)],
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


# ---------------------------------------------------------------------------
# the line check
# ---------------------------------------------------------------------------
#
# The register cites built pages by line. A page edit shifts lines without
# moving a value, so a row can reproduce for releases while pointing at the
# wrong paragraph. Each row carries an anchor, the words around the figure as
# a reader sees them, and `--lines` checks the cited line still shows them.

_PAGE = """<!doctype html>
<meta name="description" content="The median is -6.24 bps and other words">
<div id="pt-root"><main>
<p>An opening paragraph.</p>
<p>The median is <b>-6.24</b> bps and seven of the eight seeds are negative.</p>
</main></div>
<script id="pt-template" type="text/x-pt-template">
<p>The median is -6.24 bps and seven of the eight seeds are negative.</p>
</script>
"""


def _lines_run(tmp_path: Path, line: int, anchor: str, *extra: str):
    register = _register(tmp_path, [{
        "id": "tca.roundtrip_cost", "file": "docs/execution-cost.html",
        "line": line, "anchor": anchor, "label": "the median",
        "published": -6.24, "compare": {"kind": "round", "decimals": 2},
    }])
    page = tmp_path / "docs-repo" / "docs" / "execution-cost.html"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(_PAGE, encoding="utf-8")
    done = subprocess.run(
        [sys.executable, str(RESYNC), "--inventory", str(register), "--lines",
         *extra],
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )
    return done, json.loads(register.read_text(encoding="utf-8"))["figures"][0]


def test_an_anchor_on_its_line_is_ok(tmp_path):
    done, _ = _lines_run(tmp_path, 5, "The median is -6.24 bps")
    assert done.returncode == 0, done.stdout[-1200:]
    assert "ok 1," in done.stdout, done.stdout[-1200:]


def test_an_anchor_that_moved_is_re_pointed_by_apply(tmp_path):
    """Tags are dropped before matching, and the head's description and the
    template's copy of the page do not count as places the figure is."""
    done, row = _lines_run(tmp_path, 4, "The median is -6.24 bps")
    assert "moved 1," in done.stdout, done.stdout[-1200:]
    assert row["line"] == 4, "a report must not write the register"

    done, row = _lines_run(tmp_path, 4, "The median is -6.24 bps", "--apply")
    assert done.returncode == 0, done.stdout[-1200:]
    assert row["line"] == 5


def test_an_anchor_that_is_gone_needs_a_human(tmp_path):
    """The sentence was rewritten. Only a reader can say what it now claims."""
    done, row = _lines_run(tmp_path, 5, "The median is -8.40 bps", "--apply")
    assert done.returncode != 0, done.stdout[-1200:]
    assert "lost 1," in done.stdout, done.stdout[-1200:]
    assert row["line"] == 5
