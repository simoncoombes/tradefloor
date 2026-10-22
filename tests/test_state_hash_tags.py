"""Every optional part of the state hash sits behind a tag of its own.

`Engine::state_hash` writes a part that only some models carry (the bank's
stress clock, the index futures, the options) behind a number, so two states
that carry different parts cannot hash alike. `manifest.state_hash` writes
the same tags from a snapshot. A tag is a number in the hashed bytes and
nothing else: no draw, seed or cache reads it, so a new tag moves no price.

Branches add parts in parallel and each picks a tag, and two of them once
picked the same one. This test fails when two parts share a tag, or when the
Python twin's tags are not the engine's, so a clash shows on the branch that
makes it rather than after both have merged. Pick a tag no open branch uses.

The scan is textual: the engine's tags are the literals in
`hash_f64(&mut buf, N.0)` inside `state_hash_with_pending`; the twin's are
the literals `_f64(buf, ...)` writes inside `state_hash`, directly or from a
loop over literal tuples.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENGINE = ROOT / "rust" / "src" / "engine.rs"
MANIFEST = ROOT / "python" / "tradefloor" / "manifest.py"

#: Tags two parts share, each with the reason it stays. pt-v20 and pt-v21
#: set both `fed_stress_hold` and `pinned_vix_variance_share`, so moving
#: either part would change every state hash and ledger leaf their runs have
#: written. The two are written at different places in the hash, the clock
#: after tag 8 and the pinned move after tag 9.
SHARED = {
    10: ("the stress hold's clock (fed_stress_hold) and today's pinned VIX "
         "move (pinned_vix_variance_share)"),
}


def _engine_tags() -> list[tuple[int, int, str]]:
    """`(tag, line, condition)` for each tag the engine's state hash writes."""
    lines = ENGINE.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines)
                 if "pub fn state_hash_with_pending(" in line)
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = next(i for i in range(start + 1, len(lines))
               if lines[i] == " " * indent + "}")
    found = []
    for i in range(start, end):
        m = re.search(r"hash_f64\(&mut buf, (\d+)\.0\)", lines[i])
        if not m:
            continue
        condition = ""
        for j in range(i - 1, max(i - 12, start), -1):
            if lines[j].strip().startswith("if "):
                condition = lines[j].strip()
                break
        found.append((int(m.group(1)), i + 1, condition))
    return found


def _twin_tags() -> list[int]:
    """The tags `manifest.state_hash` writes."""
    tree = ast.parse(MANIFEST.read_text(encoding="utf-8"))
    func = next(node for node in ast.walk(tree)
                if isinstance(node, ast.FunctionDef) and node.name == "state_hash")

    def writes(node: ast.AST) -> list[ast.expr]:
        return [call.args[1] for call in ast.walk(node)
                if isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name) and call.func.id == "_f64"
                and len(call.args) == 2]

    found = [int(arg.value) for arg in writes(func)
             if isinstance(arg, ast.Constant) and isinstance(arg.value, float)]
    # A loop over literal tuples, one of whose names is the tag.
    for loop in ast.walk(func):
        if not (isinstance(loop, ast.For) and isinstance(loop.target, ast.Tuple)
                and isinstance(loop.iter, (ast.Tuple, ast.List))):
            continue
        names = [t.id if isinstance(t, ast.Name) else None for t in loop.target.elts]
        for arg in writes(loop):
            if isinstance(arg, ast.Name) and arg.id in names:
                k = names.index(arg.id)
                for row in loop.iter.elts:
                    value = row.elts[k] if isinstance(row, ast.Tuple) else None
                    if isinstance(value, ast.Constant) and isinstance(value.value, float):
                        found.append(int(value.value))
    return found


def test_the_scan_finds_the_engines_tags():
    tags = {tag for tag, _, _ in _engine_tags()}
    # The stress level and the margin, the first and the last of phase 1's.
    assert {8, 41, 54} <= tags, sorted(tags)


def test_no_two_parts_of_the_state_hash_share_a_tag():
    by_tag: dict[int, list[str]] = {}
    for tag, line, condition in _engine_tags():
        by_tag.setdefault(tag, []).append(f"engine.rs:{line} {condition}")
    clashes = {tag: where for tag, where in by_tag.items()
               if len(where) > 1 and tag not in SHARED}
    assert not clashes, (
        "parts of the state hash share a tag; give the newer one a tag no "
        "other part, here or on an open branch, uses:\n"
        + "\n".join(f"  {tag}: " + "; ".join(where)
                    for tag, where in sorted(clashes.items())))
    for tag in SHARED:
        assert len(by_tag.get(tag, [])) == 2, (
            f"tag {tag} is listed as shared by two parts but the engine writes "
            f"it {len(by_tag.get(tag, []))} times; update SHARED")


def test_the_python_twin_writes_the_engines_tags():
    engine = Counter(tag for tag, _, _ in _engine_tags())
    twin = Counter(_twin_tags())
    assert twin == engine, (
        f"manifest.state_hash writes tags the engine does not "
        f"({sorted((twin - engine).elements())}) or misses some it does "
        f"({sorted((engine - twin).elements())})")
