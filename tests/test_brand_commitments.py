"""Enforce the commitments in `PRODUCT.md` that a grep can enforce.

Why this file exists. `PRODUCT.md` has said since the project began that "the
repository must not reference the commercial product the engine was ported
from". Nothing checked it. On 2026-08-24 a brief for that product's own
website was committed to `tools/docs/`, naming the product ten times,
including in its filename and in the commit message that added it, and it sat
in the public repository for three days until a documentation sweep happened
to read the surrounding sentence. Alongside it, eighteen modules published to
crates.io and docs.rs carried the original's file paths and line numbers.

A commitment nobody checks is a preference. These tests make the checkable
parts of it fail loudly instead.

**Scope, stated honestly.** Only `tests/test_known_answer.py` runs in CI, so
this file runs in the local suite that `RELEASING.md` step 3 requires, not on
every push. That is a checklist item rather than a gate, and calling it a gate
would overstate it.
"""

from __future__ import annotations

import base64
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The corpus is excluded because its metadata still carries the origin: 46 of
#: 47 files record the generator and the source module inside `meta`. Removing
#: those means editing each file and recomputing the `bytes` and `sha256` that
#: `index.json` records for it, in lockstep, which is a separate change with
#: its own verification. Narrow the exclusion to nothing when that lands.
EXCLUDED_PREFIXES = (
    "rust/goldens/",
    # This file, necessarily. It has to contain the banned strings in order
    # to test for them, and `test_the_guard_would_actually_catch_a_disclosure`
    # below is where they live. Excluding it was not the original design: the
    # guard found ITSELF the moment it was first staged, which is the most
    # convincing evidence available that the matcher works.
    "tests/test_brand_commitments.py",
)

#: The product name, stored encoded so that this guard is not itself the
#: reference it exists to forbid. Decoded at runtime, never written out.
_PRODUCT = base64.b64decode("bWFyZ2luY2FsbA==").decode()

#: Origin locators. Symbol names from the reference are deliberately NOT here:
#: `simulateMarketTick` and `updateEconomyDaily` help a maintainer read the
#: port and disclose almost nothing. What is banned is anything that says
#: WHERE the original lives or WHAT it is written in.
ORIGIN_PATTERNS = {
    "the commercial product this engine was extracted from": _PRODUCT,
    "an origin source path": r"src/lib/engine",
    "an origin build command": r"npx tsx",
    "an origin script directory": r"scripts/rust-port",
    "the origin's language": r"TypeScript",
}

#: The one sentence that legitimately contains the origin's language name
#: while having nothing to do with the origin: it is about what DOWNSTREAM
#: consumers of the wasm build get. Allowed as a phrase rather than by file,
#: so a genuinely new reference in the same file still fails.
WASM_CONSUMER_ALLOWANCE = (
    # A Babel preset list names a syntax plugin, not this engine's history.
    # Rewording it would break the JSX loader in the design handoff, which
    # is the one place where the word is an API argument rather than prose.
    'presets: ["react", "typescript"]',
    "wasm-bindgen emits a",
    "consumers get the signatures without anything extra",
    "Nothing in the shipped tooling is",
)

#: `PRODUCT.md`: "ASCII punctuation only (no em dashes, en dashes, typographic
#: minus signs or arrows)", set as the project voice and "established and
#: approved across the README".
#:
#: Applied to the prose surfaces a reader actually meets, not the whole tree.
#: The Rust doc comments carry roughly 500 of these and predate the
#: commitment; sweeping them is a cleanup, and a test that fails on arrival
#: teaches people to skip the suite. These seven files are clean today and
#: this keeps them that way.
ASCII_PROSE = (
    "README.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "PRODUCT.md",
    "RELEASING.md",
    "rust/README.md",
    "rust/goldens/README.md",
    # `docs/` is not on this list because there is no `docs/` here any more:
    # the documentation site moved to its own repository, and the three pages
    # that used to be checked here (glossary, two-loops, principles) went with
    # it. They were left named, so this test failed on three missing files
    # from the day of the move -- which is the same shape as the ignore rule
    # and the stub path that also kept pointing at where things used to be.
    # The commitment for those pages belongs in the repository that now holds
    # them; widening this list is still how it widens for anything here.
    # And the two study READMEs, written after the commitment. Widening
    # this list is how it widens, so a study that holds to it joins.
    "examples/rate-shock/README.md",
    "examples/finrobot/README.md",
)

NON_ASCII_PUNCTUATION = {
    "—": "em dash",
    "–": "en dash",
    "−": "typographic minus",
    "→": "rightwards arrow",
    "←": "leftwards arrow",
}


def tracked_text_files() -> list[str]:
    """Every tracked file, so the walk cannot miss a directory.

    `git ls-files` rather than `rglob`, because the tree carries `target/`,
    `.venv` and a 134 MB corpus that no glob should wander into, and because
    an untracked scratch file is not part of the repository's promise.
    """
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    )
    return [
        f for f in out.stdout.split("\n")
        if f and not f.startswith(EXCLUDED_PREFIXES)
    ]


def read(path: str) -> str | None:
    try:
        return (ROOT / path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return None


def _is_wasm_consumer_line(line: str) -> bool:
    return any(phrase in line for phrase in WASM_CONSUMER_ALLOWANCE)


@pytest.mark.parametrize("label,pattern", list(ORIGIN_PATTERNS.items()))
def test_the_repository_does_not_disclose_the_engine_s_origin(label, pattern):
    """`PRODUCT.md`: the repository must not reference the commercial product.

    Reported per pattern rather than as one pass/fail, so a failure names
    which disclosure it found instead of making the reader grep for it.
    """
    offenders = []
    for path in tracked_text_files():
        text = read(path)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if not re.search(pattern, line, re.IGNORECASE):
                continue
            if _is_wasm_consumer_line(line):
                continue
            offenders.append(f"{path}:{i}")

    assert not offenders, (
        f"{len(offenders)} tracked line(s) disclose {label}.\n"
        "PRODUCT.md commits this repository not to reference the commercial\n"
        "product the engine was ported from. Say 'the reference\n"
        "implementation' instead; keep symbol names, drop paths, line\n"
        "numbers, languages and build commands.\n  "
        + "\n  ".join(offenders[:40])
    )


def test_the_guard_would_actually_catch_a_disclosure():
    """A guard that cannot fail is decoration.

    The banned strings do not appear in this repository by construction, so
    without this every pattern above would pass against an empty haystack and
    a typo in one of them would be invisible. This proves the matcher works.
    """
    for label, pattern in ORIGIN_PATTERNS.items():
        sample = {
            "the commercial product this engine was extracted from":
                f"see {_PRODUCT}.io for the original",
            "an origin source path": "ported from `src/lib/engine/market.ts:1230`",
            "an origin build command": "run: npx tsx generate.ts",
            "an origin script directory": "see scripts/rust-port/vectors.ts",
            "the origin's language": "ported from its TypeScript original",
        }[label]
        assert re.search(pattern, sample, re.IGNORECASE), (
            f"the {label!r} pattern no longer matches the thing it bans"
        )
        assert not _is_wasm_consumer_line(sample)


def test_the_wasm_consumer_allowance_is_narrow():
    """The allowance must not swallow a real disclosure in the same file.

    It is written as a phrase rather than a file path for exactly this
    reason: `docs/wasm.html` is allowed to say what a downstream consumer
    gets, and is not allowed to say what the engine was ported from.
    """
    assert _is_wasm_consumer_line(
        "wasm-bindgen emits a .d.ts alongside the module, so TypeScript "
        "consumers get the signatures without anything extra."
    )
    assert not _is_wasm_consumer_line(
        "The engine was ported from its TypeScript original."
    )


@pytest.mark.parametrize("path", ASCII_PROSE)
def test_the_prose_surfaces_use_ascii_punctuation(path):
    """`PRODUCT.md`: ASCII punctuation only, the voice approved on the README.

    Scoped to the files that hold to it today. Widening this list is the way
    to widen the commitment; the alternative, a repo-wide assertion that fails
    on arrival, would just get skipped.
    """
    text = read(path)
    assert text is not None, f"{path} is missing"
    found = []
    for i, line in enumerate(text.splitlines(), 1):
        for ch, name in NON_ASCII_PUNCTUATION.items():
            if ch in line:
                found.append(f"{path}:{i}: {name}")
    assert not found, (
        "non-ASCII punctuation in a prose surface. Use ' -- ' for an em dash, "
        "'-' for a minus and '->' for an arrow.\n  " + "\n  ".join(found[:20])
    )


def test_both_manifests_declare_the_dual_licence():
    """`PRODUCT.md`: dual-licensed MIT OR Apache-2.0, at the user's option.

    Two manifests declare it and they are edited independently, so this is
    the kind of pair that drifts silently. crates.io and PyPI each render
    their own, and a reader comparing them would find the project unsure of
    its own terms.
    """
    expected = '"MIT OR Apache-2.0"'
    for manifest in ("pyproject.toml", "rust/Cargo.toml"):
        text = read(manifest)
        assert text is not None, f"{manifest} is missing"
        assert re.search(rf"^license\s*=\s*{re.escape(expected)}", text, re.M), (
            f"{manifest} does not declare license = {expected}. PRODUCT.md "
            "commits the project to MIT OR Apache-2.0 in both manifests."
        )
