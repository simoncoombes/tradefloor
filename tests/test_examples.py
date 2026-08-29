"""The example notebooks must still run.

A committed notebook carries its output, which is what makes it readable
without a kernel -- and also what lets it rot silently. The output says the
code worked on the day it was written; nothing says it works now. A reader
who trusts a stale notebook loses an afternoon to an API that moved.

`depth()` gaining a required `side` argument is the concrete case: the
notebook read correctly, the output looked right, and the code raised
`TypeError`. It was caught because the notebooks are executed rather than
written and hoped over.

Opt-in because executing eight notebooks takes about a minute and needs
`jupyter`, which the library does not depend on. Set `PRETIUM_SLOW_TESTS=1`
to run it; the release check does.
"""

import os
import sys
from pathlib import Path

import pytest

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"
NOTEBOOKS = sorted(EXAMPLES.glob("0*.ipynb"))
# `[0-9]*` and not `0*`: the examples are numbered, and the tenth one is not
# `0`-prefixed. Under the old glob example 10 onward was silently unchecked,
# which is the failure mode this file exists to prevent, applied to itself.
SCRIPTS = sorted(EXAMPLES.glob("[0-9]*.py"))

#: Executing notebooks is slow and needs jupyter, so those tests are opt-in.
#: The syntax check on the scripts is not -- a rename that missed a
#: reference should fail on every run, not only when someone remembers the
#: flag.
SLOW = pytest.mark.skipif(
    not os.environ.get("PRETIUM_SLOW_TESTS"),
    reason="executing the examples is slow; set PRETIUM_SLOW_TESTS=1 to run",
)


@SLOW
def test_there_are_notebooks_to_check():
    """Guards the guard. A glob that matched nothing would make every test
    below pass by vacuum, and the suite would report the notebooks healthy
    while checking none of them."""
    assert len(NOTEBOOKS) >= 4, f"found {[p.name for p in NOTEBOOKS]}"


@SLOW
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


@SLOW
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


# -- the scripts -----------------------------------------------------------


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_the_script_at_least_compiles(path):
    """Cheap, and runs by default rather than behind the slow-test flag.

    A rename that missed a reference, or an API that moved under an example,
    shows up here in milliseconds. It is not a claim that the script works
    -- that is the test below -- only that it is still valid Python.
    """
    import ast
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


@SLOW
def test_the_research_workflow_runs_end_to_end():
    """The example the README points at, run whole.

    It asserts its own structural gates as it goes -- the TCA fill
    saturation and the pinned-scenario ripple among them -- so a silent
    behavioural change fails here rather than in a reader's terminal.
    """
    import subprocess
    script = EXAMPLES / "07-research-workflow.py"
    if not script.exists():                     # renamed? say so clearly
        pytest.fail(f"{script.name} is missing; examples/ has "
                    f"{[p.name for p in SCRIPTS]}")
    done = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "total" in done.stdout.lower()


def test_the_forking_demo_runs_end_to_end():
    """The forking demo, run whole, and NOT behind the slow flag.

    It takes about two seconds, and what it checks -- that a fork starts where
    its source stood, carries its source's history, does not reach its source
    or its siblings, and replays from the checkpoint -- is the guarantee the
    library makes about experiments. Something that central should be checked
    on every run rather than when someone remembers a flag.

    It asserts its own gates and exits non-zero if any fails, so this reads
    the return code and then confirms the summary line, because a script that
    printed FAIL and exited zero would be the more dangerous failure.
    """
    import subprocess
    script = EXAMPLES / "10-forking-a-market.py"
    if not script.exists():
        pytest.fail(f"{script.name} is missing; examples/ has "
                    f"{[p.name for p in SCRIPTS]}")
    done = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=600)
    assert done.returncode == 0, done.stdout[-3000:] + done.stderr[-3000:]
    assert "fork test          PASS" in done.stdout, done.stdout[-2000:]
    assert "FAIL" not in done.stdout


@SLOW
def test_the_claude_example_refuses_without_its_extra():
    """It cannot be run here -- it needs an API key and spends money per
    decision -- so what is checked is that it fails the way a reader should
    experience it: a sentence naming the extra, not a traceback."""
    import os
    import subprocess
    script = EXAMPLES / "08-claude-agent.py"
    if not script.exists():
        pytest.skip(f"{script.name} not present")
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    done = subprocess.run([sys.executable, str(script)],
                          capture_output=True, text=True, timeout=120, env=env)
    combined = done.stdout + done.stderr
    assert "pretium[claude]" in combined or "ANTHROPIC_API_KEY" in combined, (
        f"expected a readable refusal, got:\n{combined[-1500:]}"
    )
    assert "Traceback" not in done.stdout
