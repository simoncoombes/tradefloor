"""The fork figure has to be real engine output, and the same every run.

``examples/fork-futures/fork_futures.py`` draws the figure the project
README shows: one market, forked into a control and three packaged
scenarios, with a buy-and-hold portfolio carried on each branch. A figure
on the front page that the code no longer produces is a claim nobody can
check, so these run the script and read what it wrote.

The committed copy under ``figure/`` is compared with a new run only when
its caption names the installed version. A run with agent orders in it
replays exactly on the same release and is promised no further than that,
so a copy drawn on an earlier release is a record of that release.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

import tradefloor as tf

STUDY = Path(__file__).resolve().parent.parent / "examples" / "fork-futures"
sys.path.insert(0, str(STUDY))

study = pytest.importorskip("fork_futures")

THEMES = ("light", "dark")


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    first = tmp_path_factory.mktemp("first")
    second = tmp_path_factory.mktemp("second")
    return (first, study.main(out=first)), (second, study.main(out=second))


def test_the_study_reruns_to_the_same_bytes(runs):
    """`fork_futures.py`, run twice, writes the same two files."""
    (first, _), (second, _) = runs
    for theme in THEMES:
        name = f"fork-futures-{theme}.svg"
        assert (first / name).read_bytes() == (second / name).read_bytes()


def test_the_branches_start_identical_and_then_differ(runs):
    (_, result), _ = runs
    assert result["identical_at_fork"] == {
        name: True for name in study.SCENARIOS}
    assert len(result["shared"]) == study.SHARED_DAYS + 1
    control = result["branches"]["control"]
    assert len(control) == study.BRANCH_DAYS
    for name in study.SCENARIOS:
        assert result["branches"][name] != control, (
            f"{name} never left the control branch, so the scenario did "
            "not reach the market")


def test_the_figure_states_where_it_came_from(runs):
    """Each file is ASCII, names every branch and carries the version,
    preset and seed the library reported for the run."""
    (out, result), _ = runs
    preset = tf.ModelParams.from_preset().fingerprint
    for theme in THEMES:
        text = (out / f"fork-futures-{theme}.svg").read_bytes().decode(
            "ascii")
        assert f"tradefloor {tf.__version__}, preset {preset}, " in text
        assert f"seed {study.SEED}," in text
        for name in study.BRANCHES:
            level = result["branches"][name][-1]
            assert f">{name}<tspan" in text
            assert f">{level:.1f}</tspan>" in text
        # No background rectangle: the page shows through, which is what
        # lets one file sit on a light page and the other on a dark one.
        assert "<rect" not in text


def test_the_two_palettes_differ_only_in_colour(runs):
    (out, _), _ = runs
    light, dark = ((out / f"fork-futures-{theme}.svg").read_text(
        encoding="ascii") for theme in THEMES)
    colour = re.compile(r"#[0-9a-f]{6}")
    assert light != dark
    assert colour.sub("#", light) == colour.sub("#", dark)


def test_the_committed_figure_matches_a_run_on_its_own_version(runs):
    (out, _), _ = runs
    for theme in THEMES:
        name = f"fork-futures-{theme}.svg"
        committed = (STUDY / "figure" / name).read_text(encoding="ascii")
        drawn_on = re.search(r"tradefloor ([^,\s]+), preset", committed)
        assert drawn_on, f"figure/{name} carries no caption"
        if drawn_on.group(1) != tf.__version__:
            pytest.skip(
                f"figure/{name} was drawn on tradefloor "
                f"{drawn_on.group(1)} and this is {tf.__version__}; "
                "regenerate it with `python "
                "examples/fork-futures/fork_futures.py --out "
                "examples/fork-futures/figure`")
        assert committed == (out / name).read_text(encoding="ascii"), (
            f"figure/{name} differs from what the script draws on the "
            "version its caption names")
