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

# The checker is a script rather than a package, so its rules are reachable
# only by putting its directory on the path. `tools/release/check.py` reads
# `tools/presets/record.py` the same way. Importing it lets the tests below
# state the rule itself, rather than only what the command line prints.
sys.path.insert(0, str(CHECKER.parent))

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
                                  "RELEASING.md", "SECURITY.md"])
def test_the_prose_files_the_checker_defaults_to_all_exist(name):
    """A checker that silently skips a missing file reports green.

    `prose.py` builds its default list by existence, so a renamed file would
    drop out of the check rather than fail it. This is the guard for that,
    and it is the same class as the stub path that skipped ninety-nine tests
    while reporting green.
    """
    assert (ROOT / name).is_file()


# --------------------------------------------------------------------------
# Control bytes
# --------------------------------------------------------------------------
#
# 0x08 is below 128, so a file carrying one is ASCII and passes every check
# in this repository that asks whether it is. Two literal backspaces inside
# a regex in a test made that assertion match nothing, and the suite
# reported green, because the bytes were invisible in the editor, in the
# diff and in the review.
#
# Every control byte in these tests is built with `chr()`. Writing a literal
# one would make this file fail the rule it states, and a test that has to
# be excluded from its own guard is weaker than one that does not.

BACKSPACE = chr(8)


def run_checker(*paths) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CHECKER), *map(str, paths)],
                          cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8")


def test_a_control_byte_in_a_paragraph_is_reported(tmp_path):
    page = tmp_path / "page.md"
    page.write_text(
        f"A paragraph carrying a {BACKSPACE} byte, with words after it.\n",
        encoding="utf-8")
    out = run_checker(page)
    assert "control byte 0x08" in out.stdout, out.stdout
    assert "page.md:1:" in out.stdout, out.stdout


def test_a_control_byte_in_a_heading_or_a_fence_is_reported(tmp_path):
    """The reason this rule reads the raw file.

    Every other rule reads parsed paragraphs. `from_markdown` skips fenced
    blocks entirely and routes headings down a different path, so a byte in
    either place reaches none of them.
    """
    page = tmp_path / "page.md"
    page.write_text(
        f"# A heading holding a {BACKSPACE} byte\n"
        "\n"
        "```\n"
        f"a fenced line holding a {BACKSPACE} byte\n"
        "```\n",
        encoding="utf-8")
    out = run_checker(page)
    assert out.stdout.count("control byte 0x08") == 2, out.stdout
    assert "page.md:1:" in out.stdout, "the heading byte went unreported"
    assert "page.md:4:" in out.stdout, "the fenced byte went unreported"


def test_the_report_names_the_line_the_column_and_the_byte(tmp_path):
    """A finding has to say where to look, since the byte cannot be seen."""
    page = tmp_path / "page.md"
    page.write_text(f"one\ntwo\nab{BACKSPACE}cd\n", encoding="utf-8")
    out = run_checker(page)
    assert "page.md:3: control byte 0x08 at column 3" in out.stdout, out.stdout


def test_the_excerpt_escapes_the_byte_rather_than_printing_it(tmp_path):
    """A raw control byte written to a terminal is acted on, not shown."""
    page = tmp_path / "page.md"
    page.write_text(f"a line with a {BACKSPACE} byte in it\n", encoding="utf-8")
    out = run_checker(page)
    assert "\\x08" in out.stdout, "the excerpt should escape the byte"
    assert BACKSPACE not in out.stdout, "a raw control byte reached the report"


def test_tab_newline_and_return_are_allowed(tmp_path):
    """The three a text file legitimately holds.

    This repository is LF everywhere, because `.gitattributes` sets
    `* text=auto eol=lf`, and no tracked file carries a CRLF or a lone CR.
    The checker path cannot see a carriage return either, since `read_text`
    translates newlines before the rule reads the text, which is why this
    test cannot pin the allowance and `test_the_rule_covers_the_range_it_claims`
    states it against `control_bytes` directly. The entry earns its place
    from the callers that read BYTES: the tree walk in
    `tests/test_brand_commitments.py`, and the copy of this checker that
    `tradefloor-docs` vendors into a tree with no such attribute.
    """
    page = tmp_path / "page.md"
    page.write_text("a line\twith a tab\r\nand a second line here\n",
                    encoding="utf-8")
    out = run_checker(page)
    assert "control byte" not in out.stdout, out.stdout


def test_a_carriage_return_is_allowed_when_a_caller_reads_bytes():
    """The allowance, stated where it can actually be reached.

    `control_bytes` takes decoded text, so a caller that reads bytes and
    decodes them itself hands it a carriage return intact. Removing 0x0D
    from `ALLOWED_CONTROL` makes this fail, where no test going through the
    command line could tell the difference.
    """
    from prose import control_bytes  # noqa: PLC0415

    text = b"line one\r\nline two\r\n".decode("utf-8")
    assert "\r" in text, "the fixture must carry a real carriage return"
    assert not control_bytes("f.md", text)


def test_a_finding_exits_non_zero(tmp_path):
    """A hook or a CI step that tests the status has to see a finding.

    The checker printed a finding and returned 0 until now, so any caller
    trusting the status passed over one. `tools/release/check.py` reads the
    last line instead and caught them either way. `tradefloor-docs` vendors
    this file, so its build now fails on a finding rather than printing it.
    """
    page = tmp_path / "page.md"
    page.write_text(f"A paragraph with a {BACKSPACE} byte, and more words.\n",
                    encoding="utf-8")
    out = run_checker(page)
    assert "control byte 0x08" in out.stdout, out.stdout
    assert out.returncode == 1, "a finding must exit non-zero"


def test_a_style_finding_exits_non_zero_too(tmp_path):
    """The status follows findings, rather than this one rule."""
    page = tmp_path / "page.md"
    page.write_text("# Why this heading is a sentence\n\n"
                    "A paragraph with enough words to pass the length rule.\n",
                    encoding="utf-8")
    out = run_checker(page)
    assert "noun phrase" in out.stdout, out.stdout
    assert out.returncode == 1


def test_a_clean_file_exits_zero(tmp_path):
    page = tmp_path / "page.md"
    page.write_text("A clean paragraph with enough words to pass every rule.\n",
                    encoding="utf-8")
    out = run_checker(page)
    assert out.stdout.strip().endswith("0 findings across 1 files"), out.stdout
    assert out.returncode == 0


def test_the_rule_covers_the_range_it_claims():
    """Every byte from 0x00 to 0x1F, tab, newline and return excepted, and
    DEL at 0x7F.

    Stated over the whole range rather than on one example, because a rule
    written for the backspace that prompted it would pass the next byte.
    """
    from prose import ALLOWED_CONTROL, DEL, control_bytes  # noqa: PLC0415

    assert ALLOWED_CONTROL == {0x09, 0x0A, 0x0D}
    assert DEL == 0x7F
    for code in [*range(0x00, 0x20), DEL]:
        findings = control_bytes("f.md", f"text {chr(code)} more text")
        if code in ALLOWED_CONTROL:
            assert not findings, f"0x{code:02X} should be allowed"
        else:
            assert len(findings) == 1, f"0x{code:02X} went unreported"
            assert f"0x{code:02X}" in findings[0]


def test_del_is_reported_although_it_is_not_below_0x20():
    """The byte a range written as "below 0x20" passes.

    DEL sits above the printable characters, so a rule phrased as a single
    lower range misses it, and it is as invisible and as ASCII as the
    backspace that prompted this one.
    """
    from prose import control_bytes  # noqa: PLC0415

    findings = control_bytes("f.md", f"a line with a {chr(0x7F)} in it")
    assert len(findings) == 1, findings
    assert "control byte 0x7F" in findings[0]


def test_printable_ascii_and_ordinary_text_report_nothing():
    """The other side of the boundary, so the rule cannot be vacuously wide.

    0x20 is the space and 0x7E is the tilde, and both bound the printable
    range this rule must leave alone.
    """
    from prose import control_bytes  # noqa: PLC0415

    printable = "".join(chr(c) for c in range(0x20, 0x7F))
    assert not control_bytes("f.md", printable)
    assert not control_bytes("f.md", "Ordinary prose, with punctuation.")


def test_nothing_above_the_printable_range_is_reported():
    """The upper bound, so this rule and the non-ASCII one stay apart.

    `NON_ASCII_PUNCTUATION` in `tests/test_brand_commitments.py` owns
    everything above 127, and this rule owns two ranges below it. A rule
    that fired above the tilde would report an em dash twice and would flag
    every accented letter in the tree, so the two ranges it does cover are
    pinned from both sides.
    """
    from prose import control_bytes  # noqa: PLC0415

    for code in range(0x80, 0x100):
        assert not control_bytes("f.md", f"text {chr(code)} more text"), \
            f"0x{code:02X} is reported and belongs to the non-ASCII rule"


def test_the_published_prose_carries_no_control_byte():
    """The rule over the files the checker walks by default.

    `test_the_house_style_holds_across_the_published_prose` already fails on
    any finding, so this would fail there too. It is separate so the reason
    a run went red is readable from the test name.
    """
    from prose import control_bytes  # noqa: PLC0415

    findings = []
    for name in ("README.md", "CHANGELOG.md", "CONTRIBUTING.md", "CONTENT.md",
                 "RELEASING.md", "SECURITY.md"):
        findings += control_bytes(name, (ROOT / name).read_text(
            encoding="utf-8"))
    assert not findings, "\n".join(findings)
