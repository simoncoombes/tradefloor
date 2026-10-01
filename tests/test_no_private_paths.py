"""No tracked file outside `validation/` cites the project's private material.

This repository is public. The project's design notes, its run outputs and
its scratch files are not, and a path into them is a citation nobody outside
can follow. Up to 0.8.5 the changelog, the provenance table, the preset
records, the Rust comments and the tests cited about 900 such paths. 0.8.6
removed them; this test keeps them out.

`validation/` is skipped. It publishes pt-v20's grade in the layout the grade
ran in, so `validation/pt-v20/programme/...` is a public path, and the same
`programme/` prefix anywhere else is the private one.

Each pattern is a kind of private location:

- `programme/` not preceded by `validation/pt-v20/`: the design notes' tree.
- `tradefloor-design`: the private repository's name.
- `/private/tmp` and `scratchpad`: a working machine's temporary files.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

PATTERNS = {
    "programme/": re.compile(r"(?<!validation/pt-v20/)programme/"),
    "tradefloor-design": re.compile(r"tradefloor-design", re.I),
    "/private/tmp": re.compile(r"/private/tmp"),
    "scratchpad": re.compile(r"scratchpad", re.I),
}

#: The only files that may still match, by pattern, each with the reason.
#: An entry that no longer matches anything fails the test as well, so the
#: list cannot outlive the reason for it.
ALLOWED = {
    "tests/test_no_private_paths.py": {
        p: "this file states the patterns" for p in PATTERNS
    },
    "tools/presets/record.py": {
        "programme/": "`public_paths` rewrites the `programme/` paths in a "
                      "published grade's verdict to `validation/pt-v20/"
                      "programme/` when it writes a record, so it has to "
                      "match the prefix",
    },
    "tests/test_validation.py": {
        "programme/": "runs the recorded commands from inside "
                      "`validation/pt-v20/`, where the paths are relative to "
                      "that folder, and feeds `programme/` paths to "
                      "`record.public_paths` to check the rewrite",
    },
    "tests/test_rust_crate.py": {
        "tradefloor-design": "asserts the crate manifest does not name the "
                             "private repository",
    },
}


def tracked_matches() -> dict[tuple[str, str], list[str]]:
    """Every (file, pattern) hit outside `validation/`, with the lines."""
    out = subprocess.run(
        ["git", "grep", "-n", "-I", "-i", "-E",
         "programme/|tradefloor-design|/private/tmp|scratchpad",
         "--", ".", ":!validation"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode not in (0, 1):
        pytest.skip(f"git grep failed: {out.stderr.strip()[:200]}")
    hits: dict[tuple[str, str], list[str]] = {}
    for line in out.stdout.splitlines():
        path, _, rest = line.partition(":")
        for name, pattern in PATTERNS.items():
            if pattern.search(rest):
                hits.setdefault((path, name), []).append(line)
    return hits


@pytest.fixture(scope="module")
def matches():
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout: the check is over tracked files")
    return tracked_matches()


def test_no_tracked_file_cites_a_private_path(matches):
    stray = [line
             for (path, name), lines in sorted(matches.items())
             if name not in ALLOWED.get(path, {})
             for line in lines]
    assert not stray, (
        "private paths in tracked files; state the fact without the path, "
        "or point at the published copy under validation/:\n  "
        + "\n  ".join(stray[:40]))


def test_every_allowance_is_still_needed(matches):
    stale = [f"{path}: {name}"
             for path, names in sorted(ALLOWED.items())
             for name in names
             if (path, name) not in matches]
    assert not stale, (
        "allow-list entries that match nothing any more; remove them:\n  "
        + "\n  ".join(stale))
