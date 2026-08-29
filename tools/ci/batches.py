"""How the test suite is split so it can run in CI at all.

The whole suite takes over half an hour in one process and `test_facts.py`
alone is nine minutes of that, because it runs real simulations to score
stylised facts. That is too slow to put in front of every pull request, which
is why for a long time it ran nowhere but a developer's machine -- the
reproducibility subset in `determinism.yml` is what a pull request gates on,
and it is deliberately small.

Split four ways it finishes in about the time of its longest batch. The split
is alphabetical rather than by cost, with the one expensive file pulled out,
because a cost-balanced split would need re-balancing every time a file grew
and an alphabetical one only needs to stay exhaustive.

Exhaustive is the property that matters. A test file in no batch would run
nowhere and nothing would say so, which is the failure this project keeps
finding in its own guards, so `tests/test_packaging.py` asserts that every
file is in exactly one batch.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
TESTS = ROOT / "tests"

#: The expensive one, alone, so the other three are not held behind it.
SLOWEST = "test_facts.py"

#: Batch name -> the first letters of the file names it covers. `test_facts.py`
#: is excluded from its alphabetical home and given its own batch.
LETTERS: dict[str, str] = {
    "a-e": "abcde",
    "f-p": "fghijklmnop",
    "q-z": "qrstuvwxyz",
}

BATCHES: tuple[str, ...] = (*LETTERS, "facts")


def files(batch: str) -> list[str]:
    """The test files in `batch`, as repository-relative paths."""
    if batch not in BATCHES:
        raise SystemExit(
            f"unknown batch {batch!r}; the batches are {', '.join(BATCHES)}")
    everything = sorted(p.name for p in TESTS.glob("test_*.py"))
    if batch == "facts":
        chosen = [SLOWEST] if SLOWEST in everything else []
    else:
        letters = LETTERS[batch]
        chosen = [name for name in everything
                  if name != SLOWEST
                  and name[len("test_"):][:1].lower() in letters]
    return [f"tests/{name}" for name in chosen]


def uncovered() -> list[str]:
    """Test files that no batch would run. Always empty, and asserted so."""
    covered = {name for batch in BATCHES for name in files(batch)}
    return sorted({f"tests/{p.name}" for p in TESTS.glob("test_*.py")}
                  - covered)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <{'|'.join(BATCHES)}>")
    print(" ".join(files(sys.argv[1])))
