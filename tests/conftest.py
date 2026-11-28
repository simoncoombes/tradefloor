"""Suite-wide pytest configuration.

Two things are here: the opening cache's size for a test run (see
`OPENING_CACHE_ENTRIES`), and the two gate markers, registered so that neither
raises an unknown-marker warning and so that `--markers` prints what each
one means to anybody who runs it.

WHY THE MARKERS EXIST. The suite was carrying two different gates in one exit
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

import tradefloor

#: Engines the opening cache keeps during a test run, where the library's
#: default is 16.
#:
#: Every build on the default preset plays a 504-session prehistory, and the
#: cache hands a later build from the same seed, universe and model a copy
#: instead. Sixteen is too few for a suite that walks the same seeds twice:
#: `test_flow_impact.py`'s buyer and seller each run seeds 1 to 32, so with
#: sixteen the seller finds every one of them evicted. Measured on those two
#: tests: 64 hits and 64 misses at 16, 96 hits and 32 misses at 512, and half
#: the time. The cache's own memory bound is its 2,000-name budget, which a
#: larger count does not move, so this costs at most the few megabytes that
#: budget allows.
#:
#: A served engine is the engine a cold build produces, which is what
#: `tests/test_opening_cache.py` asserts, so no test reads a different market
#: for this. That file sets its own capacity and puts this one back.
OPENING_CACHE_ENTRIES = 512


def pytest_configure(config):
    tradefloor.Engine.set_opening_cache_capacity(OPENING_CACHE_ENTRIES)
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
