"""Enforce the commitments in `CONTENT.md` that a grep can enforce.

Why this file exists. `CONTENT.md` has said since the project began that "the
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
import sys
import typing

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]

# The control-byte rule lives in the house-style checker, which is a script
# rather than a package, so its directory goes on the path. `check.py` reads
# `tools/presets/record.py` the same way. Imported rather than reimplemented,
# so the rule the checker applies and the rule this file walks the tree with
# are one piece of code.
sys.path.insert(0, str(ROOT / "tools" / "prose"))

import prose  # noqa: E402

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

#: `CONTENT.md`: "ASCII punctuation only (no em dashes, en dashes, typographic
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
    "CONTENT.md",
    "RELEASING.md",
    "SECURITY.md",
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
    "examples/integrations/finrobot/README.md",
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
    """`CONTENT.md`: the repository must not reference the commercial product.

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
        "CONTENT.md commits this repository not to reference the commercial\n"
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
    """`CONTENT.md`: ASCII punctuation only, the voice approved on the README.

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


#: `CONTENT.md`: 'no "X is not the Y, it is the Z" constructions'. Banned in
#: the same sentence as the ASCII rule above, by the same owner, and unchecked
#: until now -- so it drifted, into 312 sentences across 116 files, while the
#: rule it was written beside held.
#:
#: Three shapes, and the third is the one that spreads. An antithesis states
#: the negative to make the positive sound considered. A trailing clause tells
#: the reader the sentence mattered, in place of a sentence that shows it. And
#: "exactly" or "the whole point" asserts emphasis a measurement should carry
#: on its own.
BANNED_CONSTRUCTIONS = {
    r"\b(?:is|was|are|were)\s+not\s+(?:a|an|the)?[^.;\n]{0,70}?,"
    r"\s*(?:it|they|that|this)\s+(?:is|was|are|were)\b":
        'an "X is not the Y, it is the Z" antithesis',
    r",\s*(?:and|but)\s+(?:it|that|this)\s+(?:is|was)\b":
        'a trailing ", and that is ..." clause',
    r"\bwhich\s+is\s+(?:what|the\s+point|why)\b":
        'a trailing "which is what/why ..." clause',
    r"\bthe\s+(?:whole|entire)\s+point\b":
        '"the whole point"',
}

#: `exactly` is NOT on the list. It earns its place in an arithmetic claim --
#: "exactly half", "exactly 1.0 at any coupling", "exactly the vector" -- and
#: a guard that could not tell those from emphasis would be answered by
#: deleting the word from a precision claim, which is worse than the tic.
#:
#: Neither is "rather than a", which reads as the tic and mostly is not: 178
#: of the 490 raw matches at the sweep were ordinary comparatives like
#: "carries the training seeds rather than a subset of them".
#:
#: The trailing-clause pattern cannot see content, so it would flag
#: `tests/test_pt_v6.py`'s "One coefficient, and it is exactly half" if this
#: list ever reached that file. Widen the tuple and that sentence needs
#: rewriting, or this needs a carve-out. Stated here rather than discovered.


@pytest.mark.parametrize("path", ASCII_PROSE)
def test_the_prose_surfaces_avoid_the_banned_constructions(path):
    """`CONTENT.md` bans them; nothing checked, so they came back.

    Scoped to the same list the ASCII rule uses, for the same stated reason:
    a repo-wide assertion that failed on arrival would be skipped instead of
    fixed. The library docstrings, the tests and the tools were swept at the
    same time as this landed and are clean today; widening this tuple is how
    the commitment widens to them.

    `CONTENT.md` itself is exempt below, because the sentence that carries the
    ban has to quote the construction in order to name it -- the same
    exemption this file already takes for the origin patterns.
    """
    if path == "CONTENT.md":
        pytest.skip("CONTENT.md quotes the construction in order to ban it")
    text = read(path)
    assert text is not None, f"{path} is missing"

    found = []
    for pattern, description in BANNED_CONSTRUCTIONS.items():
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(pattern, line, re.IGNORECASE):
                found.append(f"{path}:{i}: {description}\n      {line.strip()}")
    assert not found, (
        "CONTENT.md bans these constructions. State the positive and stop; a "
        "clause explaining that the previous clause mattered can go.\n  "
        + "\n  ".join(found[:12])
    )


def test_the_construction_guard_would_actually_catch_one():
    """Guards the guard, as the origin matcher above is guarded.

    A pattern set that matched nothing would pass every file by vacuum and
    report the commitment kept.

    These sentences have to CONTAIN the constructions, so any blanket rewrite
    over the tree has to skip this file. One did not, turned the third example
    into a clean sentence, and this test caught it.
    """
    caught = [
        "The reason is not modesty, it is that aggregation destroys it.",
        "Declared order is kept, and it is what breaks a tie.",
        "It reads the rate directly, which is what makes it macro-aware.",
        "The whole point of the convention is that 4.5 means 450%.",
    ]
    for sentence in caught:
        assert any(re.search(p, sentence, re.IGNORECASE)
                   for p in BANNED_CONSTRUCTIONS), sentence

    # And the shapes it must leave alone. "which is how" is ordinary English
    # for naming a mechanism, and a sentence can state a limit without the
    # antithesis.
    for sentence in [
        "A fork is a copy of the engine, which is how the log survives it.",
        "The horizon chooses the ruler.",
        "One coefficient, exactly half.",
        "It is not certified beyond 252 days.",
    ]:
        assert not any(re.search(p, sentence, re.IGNORECASE)
                       for p in BANNED_CONSTRUCTIONS), sentence


def test_both_manifests_declare_the_dual_licence():
    """`CONTENT.md`: dual-licensed MIT OR Apache-2.0, at the user's option.

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
            f"{manifest} does not declare license = {expected}. CONTENT.md "
            "commits the project to MIT OR Apache-2.0 in both manifests."
        )


# --------------------------------------------------------------------------
# Control bytes
# --------------------------------------------------------------------------
#
# The rule beside this one is about punctuation a reader can see. This one is
# about bytes nobody can. 0x08 is below 128, so a file carrying one is ASCII
# and passes `test_the_prose_surfaces_use_ascii_punctuation` above, every
# grep for a typographic character, and every review. Two literal backspaces
# inside a regex in a test made that assertion match nothing, and the suite
# reported green.
#
# The rule itself lives in `tools/prose/prose.py`, beside the other things
# that read a file and report on it, so the checker a person runs before
# pushing reports it too and `tradefloor-docs` gets it when it vendors that
# file. This is the same rule over a wider walk.


def every_tracked_file() -> list[str]:
    """Every tracked file, with nothing excluded.

    `tracked_text_files` above drops `EXCLUDED_PREFIXES`, and both entries on
    that list are there for the origin-disclosure guard: the goldens record
    the origin in their metadata, and this file has to contain the banned
    strings in order to match them. A control byte is a different question
    and neither reason carries over, so this walk excludes nothing and this
    file holds to the rule it states.

    The prose surfaces are ten files. The backspaces were in a test, so a
    walk scoped to prose would have shipped a guard that could not catch the
    thing it was written for.
    """
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
        check=True
    )
    return [f for f in out.stdout.split("\n") if f]


class TreeScan(typing.NamedTuple):
    """What one walk of the tree saw, including everything it could not read.

    Three ways a file leaves the scan without being checked, and each one
    gets its own list. A single "skipped" bucket, or no bucket at all, is how
    a walk reports green over a file it never opened.
    """

    findings: list[str]
    seen: list[str]
    undecodable: list[str]
    missing: list[str]


def scan_tree(paths: list[str] | None = None) -> TreeScan:
    """Apply the control-byte rule to each path, and account for all of them.

    Bytes rather than `read_text`, so a carriage return survives to the rule
    instead of being translated to a newline on the way in.
    """
    findings: list[str] = []
    seen: list[str] = []
    undecodable: list[str] = []
    missing: list[str] = []
    for path in (every_tracked_file() if paths is None else paths):
        p = ROOT / path
        if not p.is_file():
            missing.append(path)
            continue
        try:
            text = p.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            undecodable.append(path)
            continue
        seen.append(path)
        findings += prose.control_bytes(path, text)
    return TreeScan(findings, seen, undecodable, missing)


def test_no_tracked_file_carries_a_control_byte():
    """Any byte below 0x20 outside tab, newline and carriage return, and DEL.

    At `f47c149` this walk reads 592 files and reports nothing, for the C0
    range and for DEL alike, so the rule arrives green over the whole tree
    rather than over a list chosen to make it pass.
    """
    scan = scan_tree()

    # A walk that read nothing would report green, which is the shape of the
    # failure this guard exists to catch one level down. Named files rather
    # than a count, so the check survives the tree growing.
    for required in ("README.md", "tools/prose/prose.py",
                     "tests/test_brand_commitments.py", "rust/src/engine.rs"):
        assert required in scan.seen, f"the walk missed {required}"
    assert len(scan.seen) > 400, f"the walk read only {len(scan.seen)} files"

    # A tracked file that is not on disk was counted by neither the scan nor
    # the two checks above, because both survive one file going missing. It
    # is named here instead, since a silent skip and a pass are the same
    # colour and this walk is the argument that nothing is excluded.
    assert not scan.missing, (
        "these files are tracked and not on disk, so the scan could not "
        "read them:\n  " + "\n  ".join(scan.missing[:20])
    )

    # Every tracked file decodes as UTF-8 today. A binary one arriving is a
    # decision about scope rather than something to pass over quietly.
    assert not scan.undecodable, (
        "these tracked files are not UTF-8, so the scan passed over them. "
        "Decide whether they belong in this walk rather than leaving them "
        "skipped:\n  " + "\n  ".join(scan.undecodable[:20])
    )

    assert not scan.findings, (
        f"{len(scan.findings)} control bytes in tracked files. They are "
        "invisible in an editor and in a diff, and they make anything "
        "quoting them, such as a regex in a test, match something other "
        "than what it reads as.\n  " + "\n  ".join(scan.findings[:20])
    )


def test_a_tracked_file_missing_from_disk_is_named():
    """The skip the guard above used to make in silence.

    Deleting one of the six files the checker walks by default left the guard
    passing and saying nothing, because the named-file assertions and the
    count both survive one file going missing. The path is collected and
    named now, so the walk accounts for every entry `git ls-files` gives it.
    """
    scan = scan_tree(["README.md", "no/such/tracked/file.md"])
    assert scan.missing == ["no/such/tracked/file.md"]
    assert scan.seen == ["README.md"]
    assert not scan.findings


def test_a_file_that_cannot_be_decoded_is_named(tmp_path):
    """The other silent skip, kept honest the same way."""
    blob = tmp_path / "blob.bin"
    blob.write_bytes(bytes([0xFF, 0xFE, 0x00, 0x01]))
    scan = scan_tree([str(blob)])
    assert scan.undecodable == [str(blob)]
    assert not scan.seen


def test_the_walk_would_catch_a_control_byte(tmp_path):
    """The guard above, proved against a file that carries one.

    Built with `chr()` rather than written literally, so this file stays
    clean and needs no exclusion from the rule it enforces.
    """
    planted = tmp_path / "planted.py"
    planted.write_text(
        f'assert re.search(r"{chr(8)}total{chr(8)}", text)\n',
        encoding="utf-8")
    findings = prose.control_bytes(
        "planted.py", planted.read_text(encoding="utf-8"))
    assert len(findings) == 2, findings
    assert all("0x08" in f for f in findings)
    assert "planted.py:1:" in findings[0]


def test_the_ascii_punctuation_rule_does_not_see_a_control_byte():
    """Why this rule is separate from the one beside it.

    `NON_ASCII_PUNCTUATION` lists five characters above 127. A backspace and
    a DEL are both below 128, so a file holding either satisfies that rule
    completely. The two rules cover different things and neither implies the
    other.
    """
    for code in (0x08, 0x7F):
        line = f"a line with a {chr(code)} byte in it"
        assert not [ch for ch in NON_ASCII_PUNCTUATION if ch in line]
        assert prose.control_bytes("f.md", line), f"0x{code:02X}"
