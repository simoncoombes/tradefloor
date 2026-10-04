"""Every public Rust struct with public fields must be `#[non_exhaustive]`.

A struct that other crates can write as a literal breaks every one of those
crates the day it gains a field, and `cargo semver-checks` then refuses the
patch release that adds it. From 0.10.0 every such struct in the crate is
`#[non_exhaustive]` and comes with a constructor or `Default`, so a field can
be added in any release. This test keeps a new struct from undoing that: it
fails while the struct is still on a branch, rather than at the release.

The scan is textual, over `rust/src` outside the private Python modules. A
struct counts when it is `pub`, has named fields, and every field is `pub`;
one with a private field is already unbuildable outside the crate.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "rust" / "src"


def _exhaustive_public_structs() -> list[str]:
    found = []
    for path in sorted(SRC.rglob("*.rs")):
        if path.name.startswith("python"):
            continue  # private modules, built only for the wheel
        lines = path.read_text(encoding="utf-8").split("\n")
        for i, line in enumerate(lines):
            m = re.match(r"pub struct (\w+)(<[^>]*>)?\s*\{\s*$", line)
            if not m:
                continue
            attrs = []
            j = i - 1
            while j >= 0 and lines[j].lstrip().startswith(("#[", "///")):
                attrs.append(lines[j])
                j -= 1
            if any("non_exhaustive" in a for a in attrs):
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


def test_the_scan_finds_a_struct_it_should() -> None:
    # The scan has to see the structs it guards, or it passes on nothing.
    text = (SRC / "market" / "hours.rs").read_text(encoding="utf-8")
    assert re.search(r"#\[non_exhaustive\]\npub struct GameTime \{", text)
