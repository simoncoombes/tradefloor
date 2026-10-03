"""A known-answer digest may move only with a version bump and a new note.

    python tools/ci/kat_history.py                 # against the newest v* tag
    python tools/ci/kat_history.py --against v0.8.5

`tests/test_known_answer.py` compares each digest with the baseline committed
beside it. That catches a trajectory that moved and nobody re-based, but not
one that moved and somebody did: running `known_answer.py` and committing the
new JSON passes it, with the version and the note left as they were. Then a
result recorded against the old digest and one recorded against the new
carry the same version number, and nothing says why the market changed.

This compares each baseline with the same file at the newest release tag. A
digest that differs from the release must come with a version above the
release's and a note that differs from the release's. Between releases a
baseline may be re-based more than once under one bump, which is how the
project has always worked: the version says "not the market 0.8.1 shipped",
and the note says what moved.

The rules per file:

- `known_answer.json`: a new `simulationSha256` or `bondsSha256` needs a
  higher `katVersion`. Each also needs its own note to change (`note`,
  `bondsNote`). `metadataSha256` covers the reported preset, not the
  market, and may be re-based alone with a new `note` and no bump.
- `known_answer_presets.json`: a released preset's row is frozen. Changing
  one needs a higher `presetKatVersion` and a new note, and removing one is
  never allowed. A new preset adds its row freely.
- `known_answer_book.json`, `known_answer_seed64.json`,
  `known_answer_traded.json`: any change to a digest or to what the run is
  needs a higher version and a new note.

It reads JSON from the tree and from git, and imports nothing from the
package, so it runs without a build. CI runs it on every pull request with
full history; without a tag to compare against it fails rather than passes.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: file -> (version field, [(fields that move together, note field,
#: whether a change needs a version bump)]). A field listed in no group may
#: change only with a bump and a new `note`.
RULES: dict[str, tuple[str, list[tuple[tuple[str, ...], str, bool]]]] = {
    "tests/known_answer.json": ("katVersion", [
        (("simulationSha256",), "note", True),
        (("bondsSha256",), "bondsNote", True),
        (("metadataSha256",), "note", False),
    ]),
    "tests/known_answer_book.json": ("bookKatVersion", []),
    "tests/known_answer_presets.json": ("presetKatVersion", []),
    "tests/known_answer_seed64.json": ("seed64KatVersion", []),
    "tests/known_answer_traded.json": ("tradedKatVersion", []),
}

#: Fields that are derived from others or are the explanation itself.
DERIVED = {"sha256", "bytes", "note", "bondsNote"}


def problems(name: str, old: dict | None, new: dict) -> list[str]:
    """What is wrong with `new` given the released `old`, as sentences."""
    if old is None:
        return []
    version_key, groups = RULES[name]
    bumped = new.get(version_key, 0) > old.get(version_key, 0)
    out = []
    if new.get(version_key, 0) < old.get(version_key, 0):
        out.append(f"{name}: {version_key} went down, "
                   f"{old[version_key]} to {new[version_key]}")

    def need(what: str, note: str, bump: bool) -> None:
        if bump and not bumped:
            out.append(f"{name}: {what} moved since the release and "
                       f"{version_key} is still {new.get(version_key)}; bump "
                       f"it in the generator and the JSON")
        if new.get(note) == old.get(note):
            out.append(f"{name}: {what} moved since the release and `{note}` "
                       f"was not changed to say why")

    grouped = {field for fields, _, _ in groups for field in fields}
    for fields, note, bump in groups:
        moved = [f for f in fields if new.get(f) != old.get(f)]
        if moved:
            need(", ".join(moved), note, bump)

    if name.endswith("known_answer_presets.json"):
        old_rows, new_rows = old.get("presets", {}), new.get("presets", {})
        gone = sorted(set(old_rows) - set(new_rows))
        if gone:
            out.append(f"{name}: released preset rows were removed: "
                       f"{', '.join(gone)}")
        changed = sorted(p for p in old_rows
                         if p in new_rows and new_rows[p] != old_rows[p])
        if changed:
            need(f"the rows for {', '.join(changed)}", "note", True)
        grouped |= {"presets"}

    rest = sorted(k for k in set(old) | set(new)
                  if k not in grouped and k not in DERIVED
                  and k != version_key and old.get(k) != new.get(k))
    if rest:
        need(", ".join(rest), "note", True)
    return out


def newest_tag(ref: str = "HEAD") -> str:
    done = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match", "v[0-9]*", ref],
        cwd=ROOT, capture_output=True, text=True)
    if done.returncode != 0:
        raise SystemExit(
            "no release tag is reachable from HEAD, so there is nothing to "
            "compare the known answers with. In CI, check out with "
            "`fetch-depth: 0`.\n" + done.stderr.strip())
    return done.stdout.strip()


def at(ref: str, name: str) -> dict | None:
    done = subprocess.run(["git", "show", f"{ref}:{name}"], cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8")
    return json.loads(done.stdout) if done.returncode == 0 else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--against", help="a git ref (default: the newest v* tag)")
    args = ap.parse_args(argv)
    ref = args.against or newest_tag()
    found = []
    for name in RULES:
        new = json.loads((ROOT / name).read_text(encoding="utf-8"))
        old = at(ref, name)
        state = "new since" if old is None else "checked against"
        print(f"  {name}: {state} {ref}")
        found += problems(name, old, new)
    for line in found:
        print(f"FAIL  {line}")
    if not found:
        print(f"ok    every known answer is {ref}'s, or moved with a bump "
              f"and a note")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
