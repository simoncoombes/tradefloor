"""How the test suite is split so it can run in CI at all.

The whole suite is about 2,900 seconds of pytest in one process on a GitHub
runner (PR #282's first suite run, 2026-10-10: the four old batches read
392-576s, 1,189-1,203s, 773-978s and 304-390s across 3.11 and 3.13). That is
too slow to put in front of every pull request in one job, so it is split.

THE LONG POLE IS NOT `test_facts.py`. It used to be, at nine minutes of a
half-hour suite, and it had a batch of its own for that reason. By 0.10.2
it was 304-390s and its batch finished first, while `f-p`, holding the
futures, the market and the MCP files, ran twenty minutes and set the wall
time of the whole workflow. The split below is by measured cost.

Two things share the work out. Within a batch, pytest-xdist runs the tests
on every core the runner has (`-n auto`, four on a GitHub runner), so one
expensive file is spread over four workers rather than holding a batch
behind it. Across batches, the cut points are where the measured cost
divides into four nearly equal parts: 837, 693, 775 and 603 seconds of
single-process time on that run, against 2,908 in all. The longest single
test is `test_facts.py`'s leverage effect at 114s, which bounds how fast any
batch can go however it is split.

The batches are still alphabetical ranges rather than a list of files,
because a list of files needs editing every time a file is added and a range
only needs to stay exhaustive. A file sorts into exactly one range, so it is
in exactly one batch by construction. Re-balance by moving a cut point when
the `--durations` table in the batch logs says one batch has drifted well
past the others; the comment on `STARTS` says how to read it.

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

#: Batch name -> where its range starts, as a prefix of the file name after
#: `test_`. A file belongs to the last batch whose start sorts at or before
#: its name, so every file is in exactly one. The starts must be in order.
#:
#: Chosen on single-process cost per file, measured from the timestamps on
#: pytest's progress lines in each batch log and averaged over 3.11 and
#: 3.13. `fe` keeps `test_facts.py` (309s) in the first batch;
#: `mc` falls between `test_market_*` and `test_mcp*`.
STARTS: dict[str, str] = {
    "a-fa": "",
    "fe-ma": "fe",
    "mc-r": "mc",
    "s-z": "s",
}

BATCHES: tuple[str, ...] = tuple(STARTS)


def batch_of(name: str) -> str:
    """The batch a test file named `name` (`test_<something>.py`) is in."""
    stem = name[len("test_"):]
    return [b for b, start in STARTS.items() if stem >= start][-1]


def files(batch: str) -> list[str]:
    """The test files in `batch`, as repository-relative paths."""
    if batch not in BATCHES:
        raise SystemExit(
            f"unknown batch {batch!r}; the batches are {', '.join(BATCHES)}")
    everything = sorted(p.name for p in TESTS.glob("test_*.py"))
    return [f"tests/{name}" for name in everything if batch_of(name) == batch]


def uncovered() -> list[str]:
    """Test files that no batch would run. Always empty, and asserted so."""
    covered = {name for batch in BATCHES for name in files(batch)}
    return sorted({f"tests/{p.name}" for p in TESTS.glob("test_*.py")}
                  - covered)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(f"usage: {sys.argv[0]} <{'|'.join(BATCHES)}>")
    print(" ".join(files(sys.argv[1])))
