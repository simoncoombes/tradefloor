"""Suite-wide pytest configuration.

The only thing here is the two gate markers, registered so that neither
raises an unknown-marker warning and so that `--markers` prints what each
one means to anybody who runs it.

WHY THEY EXIST. The suite was carrying two different gates in one exit
code. "May the shipped preset be released?" and "is this tree sound enough
to run a box against?" are different questions, and until 2026-09-14 a
single `pytest` exit code answered both. Because the first answer is NO by
construction until the release ships, the second answer was permanently no
as well, and the launch guard that reads it refused every machine run for a
reason that had nothing to do with the tree.

The repair is to name the category a red belongs to, on the test, where a
reader can check it against that test's own docstring. `tests/
test_gate_selection.py` pins which tests carry which marker and says what
would have to change for the set to move.
"""
from __future__ import annotations


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "ship_bar: this test is the release bar in executable form. It is "
        "red when the shipped preset does not clear the bar, which is a "
        "fact about the preset and not about the tree. A box gate deselects "
        "it; a release gate must not.")
    config.addinivalue_line(
        "markers",
        "needs_live_model: this test replays a recording of a real language "
        "model and can only be repaired by making a fresh live call, which "
        "costs money outside this repository. It is red when the world the "
        "recording was made in has moved, which is a fact about the "
        "artefact and not about the tree.")
