"""Every calibration tool must be able to print its own help.

`calibrate.py --help` crashed with `TypeError: must be real number, not
dict` for an unknown length of time. The cause was a literal percent sign in
prose: argparse expands a help string through `help % params`, so
`"13% gain"` is read as a `%g` float conversion and handed the defaults
dict.

That is a nasty failure mode for this directory specifically. These tools
are entry points a person reaches for once, having read a section number in
the calibration record, and `--help` is the first thing they type. A
traceback there reads as "this is broken" rather than "this string has a
percent in it", and nothing else exercises the help formatter.

So it is checked. The tools are invoked as subprocesses because that is how
a human runs them, and because importing them would miss the argparse
formatting that only happens on demand.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
#: Every directory under `tools/` holding scripts a reader runs. The glob
#: covered `calibration` alone, so `tools/shadow/shadow.py --help` worked
#: and nothing kept it working.
TOOL_DIRS = (ROOT / "tools" / "calibration", ROOT / "tools" / "shadow")


def _entry_points() -> list[pathlib.Path]:
    """Tool scripts that parse arguments, so have a help to print."""
    out = []
    paths = sorted(p for d in TOOL_DIRS if d.is_dir() for p in d.glob("*.py"))
    for path in paths:
        if path.name.startswith("_"):
            continue
        text = path.read_text(encoding="utf-8")
        if "add_argument(" in text and "__main__" in text:
            out.append(path)
    return out


ENTRY_POINTS = _entry_points()


def test_there_are_entry_points_to_check() -> None:
    """Guard against the parametrisation silently collapsing to nothing."""
    assert ENTRY_POINTS, (
        f"no argparse entry points found under {TOOLS}; this file would pass "
        "vacuously and the help formatter would go unchecked"
    )


@pytest.mark.parametrize("script", ENTRY_POINTS, ids=lambda p: p.name)
def test_help_renders(script: pathlib.Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(script), "--help"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"{script.name} --help exited {proc.returncode}.\n"
        "A literal percent in a help string is the usual cause: argparse "
        "expands help through `help % params`, so '13% gain' is read as a "
        "%g conversion. Double it to '13%%'.\n\n"
        f"{proc.stderr[-1200:]}"
    )
    assert "usage:" in proc.stdout, (
        f"{script.name} --help printed no usage line"
    )
