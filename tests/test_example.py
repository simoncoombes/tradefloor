"""Run the shipped example the way a user runs it.

Not a duplicate of the unit tests. Running the library end to end, in the shape
a researcher would actually use, is what caught the two worst defects this
project has had:

  - `run_many` hung forever under any process without an importable
    ``__main__`` -- which is every notebook and every REPL. Every unit test
    passed, because under pytest ``__main__`` is importable.
  - the order log did not compare equal to itself after a JSON round trip, so
    an archived experiment differed from the run that produced it. Replay
    worked, so nothing functional failed.

Both were invisible to unit tests by construction. This exists so the whole
path stays exercised, and it is why the example asserts rather than only
printing.
"""

import subprocess
import sys
from pathlib import Path

import pytest

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "research_workflow.py"


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example not present")
def test_the_research_workflow_runs_and_its_assertions_hold():
    # As a subprocess, from a directory that is not the repo root, so the
    # INSTALLED package is what runs -- the same discipline the determinism
    # gate uses. A test that accidentally exercised the source tree would say
    # nothing about the wheel.
    result = subprocess.run(
        [sys.executable, str(EXAMPLE)],
        cwd=EXAMPLE.parent, capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for expected in ("universe:", "swept", "evaluated", "shortfall",
                     "untouched instruments moved: none",
                     "ground truth:", "replayed it to identical prices"):
        assert expected in result.stdout, (expected, result.stdout)


@pytest.mark.skipif(not EXAMPLE.exists(), reason="example not present")
def test_the_workflow_reports_the_same_numbers_twice():
    # The whole pipeline is one seeded computation. If any stage leaked
    # non-determinism -- a dict iteration order, a thread scheduling
    # difference in the sweep, a float accumulated in completion order --
    # this is where it would show.
    sys.path.insert(0, str(EXAMPLE.parent))
    import research_workflow

    first = research_workflow.main()
    second = research_workflow.main()
    assert first == second
