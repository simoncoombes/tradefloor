"""The prose this repository publishes, checked mechanically.

`tools/prose/prose.py` is the canonical copy of the house-style checker and
`tradefloor-docs` vendors it, the way it vendors `_core.pyi` and `params.rs`.
It lived only in the docs repo until 0.6.x, which meant the library's own
README, changelog and runbook were never checked by the rule set that binds
them.

The checker sees the mechanical half of the style: headings that are not noun
phrases, definitions by negation, phrases telling the reader something is
significant, rhetorical questions, em-dash asides, three short sentences in a
row, and paragraphs ending on a very short one. It cannot see register, so a
clean run is not a passing grade.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CHECKER = ROOT / "tools" / "prose" / "prose.py"

#: Everything above this is what `release.yml` publishes as the GitHub release
#: note and what the release-notes page renders.
MARKER = "<!-- release-note-ends -->"

#: Words, above the marker. Set on 2026-08-30 after the 0.6.0 section reached
#: 1,257 words against a median of 139 across the twelve before it, which is
#: an unreadable release page and was not what any reader needed.
#:
#: 250 rather than the median, because 0.4.0 (233) and 0.2.0 (240) both moved
#: the default preset and explained themselves inside it. A release that
#: cannot say what it did in 250 words has detail to move below the marker,
#: which is what the marker is for.
BUDGET = 250


def sections(text: str) -> list[tuple[str, str]]:
    parts = re.split(r"(?m)^(## .+)$", text)
    return [(parts[i][3:].strip(), parts[i + 1])
            for i in range(1, len(parts), 2)]


def test_the_house_style_holds_across_the_published_prose():
    """`prose.py` over README, CHANGELOG, CONTRIBUTING, PRODUCT, RELEASING."""
    out = subprocess.run([sys.executable, str(CHECKER)], cwd=ROOT,
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith("0 findings across 6 files"), (
        "the house style is broken somewhere in this repository's prose:\n"
        + out.stdout
    )


@pytest.mark.xfail(strict=True, reason=(
    "The 0.6.0 section is 1,257 words against the 250-word budget this test "
    "sets. Trimming it is a writing job and is not done yet. STRICT on "
    "purpose: the day the section fits, this test passes, the strict marker "
    "turns that pass into a failure, and whoever sees it deletes the marker. "
    "A budget nobody is forced to un-exempt is a budget that never applies."))
def test_the_newest_changelog_section_fits_the_release_note_budget():
    """The newest section only.

    The sections below it were published as GitHub release notes under their
    tags. Three of them run past this budget, and rewriting a release note
    after the fact edits a record somebody may have read rather than
    improving it. The budget binds what is being written now.
    """
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    name, body = sections(text)[0]
    lead = body.split(MARKER)[0]
    words = len(lead.split())
    assert words <= BUDGET, (
        f"the {name} release note is {words} words against a budget of "
        f"{BUDGET}. Move detail below {MARKER}, where it is kept but not "
        "published as the release note."
    )


def test_every_section_that_carries_the_marker_carries_it_once():
    """Two markers would silently truncate the note at the first."""
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    for name, body in sections(text):
        assert body.count(MARKER) <= 1, f"{name} carries the marker twice"


@pytest.mark.parametrize("name", ["README.md", "CHANGELOG.md",
                                  "CONTRIBUTING.md", "CONTENT.md",
                                  "PRODUCT.md", "RELEASING.md"])
def test_the_prose_files_the_checker_defaults_to_all_exist(name):
    """A checker that silently skips a missing file reports green.

    `prose.py` builds its default list by existence, so a renamed file would
    drop out of the check rather than fail it. This is the guard for that,
    and it is the same class as the stub path that skipped ninety-nine tests
    while reporting green.
    """
    assert (ROOT / name).is_file()
