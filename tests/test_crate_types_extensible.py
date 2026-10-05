"""Every public Rust struct with public fields, and every public enum that
can gain a variant, must be `#[non_exhaustive]`.

A struct that other crates can write as a literal breaks every one of those
crates the day it gains a field, and `cargo semver-checks` then refuses the
patch release that adds it. From 0.10.0 every such struct in the crate is
`#[non_exhaustive]` and comes with a constructor or `Default`, so a field can
be added in any release. This test keeps a new struct from undoing that: it
fails while the struct is still on a branch, rather than at the release.

An enum that other crates match on exhaustively breaks each of them the day
it gains a variant, in the same way. Every public enum is `#[non_exhaustive]`
unless its variants are closed by definition. Those few are listed in
`CLOSED_ENUMS` with the reason, and a new enum goes on that list only for a
reason of the same kind.

The scan is textual, over `rust/src` outside the private Python modules. A
struct counts when it is `pub`, has named fields, and every field is `pub`;
one with a private field is already unbuildable outside the crate.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "rust" / "src"

# Public enums whose variant set cannot grow, so a host may match on them
# without a wildcard arm.
CLOSED_ENUMS = {
    # A trade has two sides.
    "Side": "order_book.rs",
}


def _attributes(lines: list[str], i: int) -> list[str]:
    attrs = []
    j = i - 1
    while j >= 0 and lines[j].lstrip().startswith(("#[", "///")):
        attrs.append(lines[j])
        j -= 1
    return attrs


def _sources() -> list[Path]:
    # The python* modules are private, built only for the wheel.
    return [p for p in sorted(SRC.rglob("*.rs")) if not p.name.startswith("python")]


def _exhaustive_public_structs() -> list[str]:
    found = []
    for path in _sources():
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"pub struct (\w+)(<[^>]*>)?\s*\{\s*$", line)
            if not m:
                continue
            if any("non_exhaustive" in a for a in _attributes(lines, i)):
                continue
            fields = []
            k = i + 1
            while not lines[k].startswith("}"):
                code = lines[k].strip()
                if re.match(r"(pub(\([^)]*\))?\s+)?\w+\s*:", code):
                    fields.append(code)
                k += 1
            if fields and all(re.match(r"pub\s+\w+\s*:", f) for f in fields):
                rel = path.relative_to(SRC.parent.parent)
                found.append(f"{rel}:{i + 1} {m.group(1)}")
    return found


def test_every_public_struct_with_public_fields_is_non_exhaustive() -> None:
    found = _exhaustive_public_structs()
    assert not found, (
        "these public structs can be built as literals outside the crate, so "
        "adding a field to one breaks every host that does. Mark each "
        "#[non_exhaustive] and give it a constructor or Default (rust/README.md, "
        "\"Upgrading to 0.10.0\"):\n  " + "\n  ".join(found)
    )


def _exhaustive_public_enums() -> list[str]:
    found = []
    for path in _sources():
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"pub enum (\w+)", line)
            if not m or any("non_exhaustive" in a for a in _attributes(lines, i)):
                continue
            if CLOSED_ENUMS.get(m.group(1)) == path.relative_to(SRC).as_posix():
                continue
            rel = path.relative_to(SRC.parent.parent)
            found.append(f"{rel}:{i + 1} {m.group(1)}")
    return found


def test_every_public_enum_that_can_grow_is_non_exhaustive() -> None:
    found = _exhaustive_public_enums()
    assert not found, (
        "these public enums can be matched without a wildcard arm outside the "
        "crate, so adding a variant to one breaks every host that does. Mark "
        "each #[non_exhaustive], or add it to CLOSED_ENUMS with the reason its "
        "variants cannot grow:\n  " + "\n  ".join(found)
    )


def test_every_closed_enum_still_exists_and_is_exhaustive() -> None:
    for name, file in CLOSED_ENUMS.items():
        lines = (SRC / file).read_text(encoding="utf-8").split("\n")
        at = [i for i, line in enumerate(lines) if line.startswith(f"pub enum {name} ")]
        assert at, f"CLOSED_ENUMS names {name} in {file}, which no longer defines it"
        assert not any("non_exhaustive" in a for a in _attributes(lines, at[0]))


def test_the_scan_finds_a_struct_it_should() -> None:
    # The scan has to see the structs it guards, or it passes on nothing.
    text = (SRC / "market" / "hours.rs").read_text(encoding="utf-8")
    assert re.search(r"#\[non_exhaustive\]\npub struct GameTime \{", text)
    text = (SRC / "economy" / "central_bank.rs").read_text(encoding="utf-8")
    assert re.search(r"#\[non_exhaustive\]\npub enum Decision \{", text)
