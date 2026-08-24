"""The example notebooks must still run.

A committed notebook carries its output, which is what makes it readable
without a kernel -- and also what lets it rot silently. The output says the
code worked on the day it was written; nothing says it works now. A reader
who trusts a stale notebook loses an afternoon to an API that moved.

`depth()` gaining a required `side` argument is the concrete case: the
notebook read correctly, the output looked right, and the code raised
`TypeError`. It was caught because the notebooks are executed rather than
written and hoped over.

Opt-in because executing four notebooks takes about a minute -- longer than
the rest of the suite -- and needs `jupyter`, which the library does not
depend on. Set `PRETIUM_SLOW_TESTS=1` to run it; the release check does.
"""

import os
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOTEBOOKS = sorted(EXAMPLES.glob("0*.ipynb"))

pytestmark = pytest.mark.skipif(
    not os.environ.get("PRETIUM_SLOW_TESTS"),
    reason="executing the notebooks takes ~1 minute; "
           "set PRETIUM_SLOW_TESTS=1 to run",
)


def test_there_are_notebooks_to_check():
    """Guards the guard. A glob that matched nothing would make every test
    below pass by vacuum, and the suite would report the notebooks healthy
    while checking none of them."""
    assert len(NOTEBOOKS) >= 4, f"found {[p.name for p in NOTEBOOKS]}"


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_notebook_executes_without_error(path):
    nbformat = pytest.importorskip("nbformat")
    pytest.importorskip("nbclient")
    from nbclient import NotebookClient

    nb = nbformat.read(path, as_version=4)
    # Run in the notebook's own directory, as a reader would, and never write
    # back: a test that rewrote the committed output would hide the drift it
    # exists to find.
    client = NotebookClient(nb, timeout=900, kernel_name="python3",
                            resources={"metadata": {"path": str(EXAMPLES)}})
    client.execute()


@pytest.mark.parametrize("path", NOTEBOOKS, ids=lambda p: p.name)
def test_the_committed_notebook_carries_its_output(path):
    """A notebook committed with empty cells is a listing, not an example.

    The point of shipping the output is that the page is readable on GitHub
    without a kernel, so an unexecuted commit defeats the reason these exist.
    """
    nbformat = pytest.importorskip("nbformat")
    nb = nbformat.read(path, as_version=4)
    code = [c for c in nb.cells if c.cell_type == "code"]
    assert code, f"{path.name} has no code cells"

    empty = [i for i, c in enumerate(code) if not c.get("outputs")]
    assert not empty, (
        f"{path.name}: code cells {empty} carry no output. Regenerate with "
        f"`jupyter nbconvert --to notebook --execute --inplace "
        f"examples/0*.ipynb`"
    )

    errors = [o for c in code for o in c.get("outputs", [])
              if o.get("output_type") == "error"]
    assert not errors, (
        f"{path.name}: committed output contains "
        f"{errors[0].get('ename')} -- the notebook was committed broken"
    )
