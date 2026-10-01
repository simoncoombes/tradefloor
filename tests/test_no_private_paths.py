"""No tracked file cites the project's private material or its cloud account.

This repository is public. The project's design notes, its run outputs and
its scratch files are not, and a path into them is a citation nobody outside
can follow. Up to 0.8.5 the changelog, the provenance table, the preset
records, the Rust comments and the tests cited about 900 such paths, and the
box scripts and their logs named the S3 bucket the boxes wrote to, whose name
carries the AWS account id. 0.8.6 removed them; these tests keep them out.

The path check skips `validation/`. It publishes pt-v20's grade in the
layout the grade ran in, so `validation/pt-v20/programme/...` is a public
path, and the same `programme/` prefix anywhere else is the private one.

Each path pattern is a kind of private location:

- `programme/` not preceded by `validation/pt-v20/`: the design notes' tree.
- `tradefloor-design`: the private repository's name.
- `/private/tmp` and `scratchpad`: a working machine's temporary files.
- `vix-dynamics.md`: the design note most cited by name, kept out except
  where a digest holds it in place.

The account check covers every tracked file, `validation/` included. It
compares hashes rather than the values, so this file does not publish what
it looks for. Box scripts read the bucket from `TRADEFLOOR_BOX_BUCKET`, and
logs carry `s3://<bucket>`.
"""

from __future__ import annotations

import hashlib
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
    "vix-dynamics.md": re.compile(r"vix-dynamics\.md"),
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
    "tools/mechanism/mechanisms/jumps.py": {
        "vix-dynamics.md": "the jumps mechanism's `Note` cites it, and the "
                           "note is inside the specification digest every "
                           "preset record carries; rewording it moves that "
                           "digest, so the owner ruled to keep it",
    },
    "rust/src/engine.rs": {
        "vix-dynamics.md": "the generated `mechanism:jumps` block carries the "
                           "same note, and has to match jumps.py's "
                           "specification byte for byte",
    },
}

#: sha256 of the AWS account id and of the bucket name the boxes used.
ACCOUNT_SHA = "b91f7ce0a5df2d9e16556419c1d55e66dcb1124f059ea3896162856824099fd2"
BUCKET_SHA = "e218530e70b5b919cc8ca9447dcc265513da1b10d58906d57e23c99994b05694"


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8")
    if out.returncode not in (0, 1):
        pytest.skip(f"git failed: {out.stderr.strip()[:200]}")
    return out.stdout


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture(scope="module")
def git_checkout():
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout: the check is over tracked files")


@pytest.fixture(scope="module")
def matches(git_checkout):
    """Every (file, pattern) hit outside `validation/`, with the lines."""
    out = _git("grep", "-n", "-I", "-i", "-E",
               "programme/|tradefloor-design|/private/tmp|scratchpad|"
               r"vix-dynamics\.md",
               "--", ".", ":!validation")
    hits: dict[tuple[str, str], list[str]] = {}
    for line in out.splitlines():
        path, _, rest = line.partition(":")
        for name, pattern in PATTERNS.items():
            if pattern.search(rest):
                hits.setdefault((path, name), []).append(line)
    return hits


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


def test_no_tracked_file_names_the_cloud_account(git_checkout):
    """Every twelve-digit run and every S3 bucket name, hashed and compared."""
    found = []
    for line in _git("grep", "-I", "-o", "-w", "-E", "[0-9]{12}").splitlines():
        path, _, token = line.rpartition(":")
        if _sha(token) == ACCOUNT_SHA:
            found.append(f"{path}: the AWS account id")
    for line in _git("grep", "-I", "-o", "-E",
                     "s3://[a-z0-9.-]+").splitlines():
        path, _, token = line.partition(":")
        if _sha(token[len("s3://"):]) == BUCKET_SHA:
            found.append(f"{path}: the box bucket")
    assert not found, (
        "the cloud account's id or bucket in tracked files; box scripts read "
        "the bucket from TRADEFLOOR_BOX_BUCKET, and logs carry "
        "s3://<bucket>:\n  " + "\n  ".join(sorted(set(found))[:40]))
