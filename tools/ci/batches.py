"""How the test suite is split so it can run in CI at all.

The whole suite is about 2,900 seconds of pytest in one process on a GitHub
runner (PR #282's first suite run, 2026-10-10, on the old split: a-e 392s
and 576s on 3.11 and 3.13, f-p 1,189s and 1,203s, q-z 773s and 978s, facts
304s and 390s). That is too slow for one job in front of every pull request,
so it is split.

`f-p` WAS THE LONG POLE, NOT `test_facts.py`. This docstring called facts
the long pole from when it was nine minutes of a half-hour suite, and by
0.10.2 its batch finished first while `f-p`, holding the futures, market
and MCP files, ran twenty minutes and set the wall time of the workflow.

Two things share the work out now.

Within a batch, pytest-xdist runs the tests on every core the runner has
(`-n auto`, four on a GitHub runner), so a batch takes roughly a third of
its single-process time. Not a quarter: a runner's four vCPUs are two
cores with hyper-threading, and every test reads about 1.5 times slower
with four workers beside it.

Across batches, the ranges are cut where the measured cost of the files
outside `test_facts.py` divides evenly: 683, 637, 637 and 603 seconds of
single-process time on that run, against 2,560 in all.

`test_facts.py` keeps a batch of its own for a narrower reason than before.
Its two longest tests, the volume and leverage effects at 81s and 114s
single-process, are adjacent in the file, and xdist hands a worker
neighbouring tests, so they run one after the other on one worker wherever
the file goes. In a batch of four ranges that chain ended the first one:
PR #282's second run put the file in an `a-fa` range and that batch's last
two per cent took 93s on 3.11 and 280s on 3.13, against 4-5 minutes for
the whole of every other batch. Alone, the chain is the batch, and it is
about as long as the others.

The ranges are alphabetical rather than a list of files, because a list of
files needs editing every time a file is added and a range only needs to
stay exhaustive. Re-balance by moving a cut when the `--durations` table in
the batch logs says one batch has drifted well past the others.

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

#: The file with a batch of its own, for the reason the docstring gives.
ALONE = "test_facts.py"

#: Range batch name -> the first letter of its range, of the file name after
#: `test_`. A file belongs to the last range whose start sorts at or before
#: its name, so every file but `ALONE` is in exactly one. The starts must
#: begin at "" and be in order, which `tests/test_packaging.py` checks.
STARTS: dict[str, str] = {
    "a-f": "",
    "g-m": "g",
    "n-r": "n",
    "s-z": "s",
}

BATCHES: tuple[str, ...] = (*STARTS, "facts")


def range_of(name: str) -> str:
    """The range batch a test file named `name` (`test_<x>.py`) sorts into."""
    stem = name[len("test_"):]
    return [b for b, start in STARTS.items() if stem >= start][-1]


def files(batch: str) -> list[str]:
    """The test files in `batch`, as repository-relative paths."""
    if batch not in BATCHES:
        raise SystemExit(
            f"unknown batch {batch!r}; the batches are {', '.join(BATCHES)}")
    everything = sorted(p.name for p in TESTS.glob("test_*.py"))
    if batch == "facts":
        chosen = [ALONE] if ALONE in everything else []
    else:
        chosen = [name for name in everything
                  if name != ALONE and range_of(name) == batch]
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
