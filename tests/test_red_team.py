"""Tests for tools/calibration/red_team.py.

The toy grids here use a six-name roster (`ROSTER_N` monkeypatched down
from the tool's real 40) and a thirty-five-day run, the smallest settings
that still clear `facts.measure`'s default `min_observations=30`. Nothing
under test depends on the roster being 40 names or the run being a full
year; the search's own module docstring explains why the roster size is a
module constant rather than a `search()` parameter, which is what makes
this monkeypatch the way to shrink it.

`workers > 1` is deliberately NOT exercised by calling `red_team.search`
directly: on this platform, spawning a `ProcessPoolExecutor` from code a
test RUNNER launched re-executes the runner's own `__main__` to bootstrap
the pool worker rather than this module's, which hangs rather than
failing cleanly (confirmed by hand; not reproduced here on purpose --
red_team.py's `ROSTER_N` and `search` docstrings carry the full
explanation). `test_cli_pooled_workers_agree_with_the_sequential_path`
below drives the CLI as a subprocess instead, which is both the
supported way to use `workers > 1` and a clean way to test it.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "tools" / "calibration"))

import red_team  # noqa: E402
import tradefloor  # noqa: E402
from tradefloor import envelope  # noqa: E402

pytest.importorskip("pyarrow", reason="facts.measure reads bars via Arrow")

#: Small enough for a sub-second cell at a six-name roster, large enough to
#: clear facts.measure's default min_observations=30.
DAYS = 35

TOOL = str(Path(__file__).resolve().parent.parent
          / "tools" / "calibration" / "red_team.py")


@pytest.fixture
def tiny_roster(monkeypatch):
    """Shrink the search's fixed roster size for a fast toy grid."""
    monkeypatch.setattr(red_team, "ROSTER_N", 6)


def test_grid_order_is_the_same_list_twice():
    a = red_team._grid_order([1, 2, 3], [10, 20], ["recession", "rate_shock"])
    b = red_team._grid_order([1, 2, 3], [10, 20], ["recession", "rate_shock"])
    assert a == b
    assert len(a) == 3 * 2 * 2
    assert set(a) == {(s, r, sc) for s in (1, 2, 3) for r in (10, 20)
                      for sc in ("recession", "rate_shock")}


def test_toy_search_worst_case_reproduces_from_its_manifest(tiny_roster):
    result = red_team.search("pt-v16", seeds=[1, 2], roster_seeds=[1],
                             scenarios=["recession"], days=DAYS, budget=2)

    # annualised_vol_pct is a marginal statistic (not a dependence one) and
    # facts.SEED_SD always carries an entry for it, so it is ranked on
    # every successful cell regardless of roster size.
    finding = result.worst["annualised_vol_pct"]

    # Route 1: the manifest's own check. reproduce() replays the run and
    # raises on any digest mismatch, so a clean return past this line IS
    # the pass -- nothing further needs asserting.
    finding.manifest.reproduce()

    # Route 2: re-measure from the finding's own recorded inputs, entirely
    # outside the manifest machinery, and check the SAME statistic reads
    # the SAME value. Two independent reproductions agreeing is stronger
    # evidence than either alone.
    universe = tradefloor.Universe.random(red_team.ROSTER_N,
                                          seed=finding.roster_seed)
    scenario = tradefloor.Scenario.load(finding.scenario)
    panel = tradefloor.facts.measure(seed=finding.seed, universe=universe,
                                     days=DAYS, scenario=scenario,
                                     model="pt-v16")
    assert panel[finding.statistic] == finding.value


def test_toy_search_report_states_budget_and_unsearched_axes(tiny_roster):
    # Three seeds, two roster seeds, two scenarios: twelve cells. A budget
    # of three must cut the grid short, so some axis value cannot appear
    # in any cell that ran.
    result = red_team.search(
        "pt-v16", seeds=[1, 2, 3], roster_seeds=[1, 2],
        scenarios=["recession", "rate_shock"], days=DAYS, budget=3)

    assert result.budget["cells_run"] == 3
    assert result.budget["cells_planned"] == 12
    unsearched = result.budget["unsearched"]
    assert any(unsearched[axis] for axis in
              ("seeds", "roster_seeds", "scenarios")), (
        "3 of 12 cells should leave at least one axis value unseen"
    )

    report = result.report()
    assert "Search budget" in report
    assert "3 of 12 planned" in report
    assert "unsearched" in report
    # The specific unseen values named above must actually appear, not just
    # the word "unsearched" -- a table that says the word without the list
    # would pass a substring check for the wrong reason.
    for axis in ("seeds", "roster_seeds", "scenarios"):
        for value in unsearched[axis]:
            assert str(value) in report


def test_search_is_the_same_walk_on_a_repeat_call(tiny_roster):
    kwargs = dict(preset="pt-v16", seeds=[1, 2], roster_seeds=[1],
                 scenarios=["recession"], days=DAYS, budget=2)
    first = red_team.search(**kwargs)
    second = red_team.search(**kwargs)

    def signature(result):
        return {
            stat: (f.seed, f.roster_seed, f.scenario, f.value)
            for stat, f in result.worst.items()
        }

    assert signature(first) == signature(second)
    assert first.budget["unsearched"] == second.budget["unsearched"]


def test_search_rejects_an_unknown_scenario():
    with pytest.raises(tradefloor.ValidationError):
        red_team.search("pt-v16", seeds=[1], roster_seeds=[1],
                        scenarios=["not-a-real-scenario"], days=DAYS,
                        budget=1)


def test_search_rejects_a_non_positive_budget():
    with pytest.raises(tradefloor.ValidationError):
        red_team.search("pt-v16", seeds=[1], roster_seeds=[1],
                        scenarios=["recession"], days=DAYS, budget=0)


def test_search_rejects_an_empty_axis():
    with pytest.raises(tradefloor.ValidationError):
        red_team.search("pt-v16", seeds=[], roster_seeds=[1],
                        scenarios=["recession"], days=DAYS, budget=1)


def test_matching_gap_files_a_statistic_that_is_covered_at_every_horizon():
    # decay-shape carries abs_return_acf20 with beyond_days=None, so it
    # applies regardless of the horizon searched.
    gap = red_team._matching_gap("abs_return_acf20", days=252)
    assert gap is not None
    assert gap.id == "decay-shape"


def test_matching_gap_respects_the_horizon_gate():
    # horizon's statistics include return_acf1, gated at
    # CERTIFIED_HORIZON_DAYS (252): it must not fire AT that horizon and
    # must fire past it.
    assert envelope.CERTIFIED_HORIZON_DAYS == 252
    assert red_team._matching_gap("return_acf1", days=252) is None
    gap = red_team._matching_gap("return_acf1", days=504)
    assert gap is not None
    assert gap.id == "horizon"


def test_matching_gap_excludes_roster_concentration():
    # cross_sectional_corr is one of roster-concentration's three
    # statistics, but this search always runs a balanced Universe.random()
    # roster, never a concentrated one, so it must never match -- at
    # either horizon, since the gap's own beyond_days is None.
    assert red_team._matching_gap("cross_sectional_corr", days=252) is None
    assert red_team._matching_gap("cross_sectional_corr", days=504) is None


def test_matching_gap_returns_none_for_an_uncovered_statistic():
    # leverage_effect is in facts.REAL_MARKETS but in no GAPS entry's
    # statistics tuple at any horizon.
    assert red_team._matching_gap("leverage_effect", days=252) is None
    assert red_team._matching_gap("leverage_effect", days=504) is None


def test_report_files_one_finding_under_a_gap_and_one_as_new():
    covered = red_team.Finding(
        statistic="abs_return_acf20", value=-0.09, band=(-0.04, 0.08),
        scaled_distance=3.5, seed=1, roster_seed=1, scenario="recession",
        manifest=None,
    )
    uncovered = red_team.Finding(
        statistic="leverage_effect", value=-0.9, band=(-0.16, 0.0),
        scaled_distance=12.0, seed=2, roster_seed=1, scenario="recession",
        manifest=None,
    )
    worst = {covered.statistic: covered, uncovered.statistic: uncovered}
    budget = {
        "preset": "pt-v16", "days": 252, "roster_size": 40,
        "requested_budget": 2, "cells_run": 2, "cells_planned": 2,
        "seeds": [1, 2], "roster_seeds": [1], "scenarios": ["recession"],
        "unsearched": {"seeds": [], "roster_seeds": [], "scenarios": []},
        "fixed": {"macro": "engine default (nothing pinned beyond a "
                          "scenario)",
                 "scenario_magnitude": "shipped default for each "
                                       "scenario"},
        "workers": 1, "wall_s": 0.01,
    }
    result = red_team.Search(worst=worst, budget=budget)
    report = result.report()

    assert "abs_return_acf20" in report
    assert "-> decay-shape" in report
    assert "leverage_effect" in report
    assert "-> NEW" in report
    # "a copy away": the drafted text is a Gap(...) call naming the
    # uncovered statistic, ready to paste into envelope.GAPS.
    assert 'id="<name-this>"' in report
    assert 'statistics=("leverage_effect",)' in report


def test_manifest_filename_is_stable_between_report_and_disk():
    # Search.report() names a finding's manifest by this function; main()
    # writes the file under the same name. If they diverge, the report
    # points at a file that does not exist.
    assert (red_team._manifest_filename("leverage_effect")
           == "manifest-leverage_effect.json")


def test_held_out_seed_marker_renders_only_for_a_held_out_seed():
    # gate_pick.HELDOUT is seeds 1-30. One finding on a held-out seed, one
    # on a seed the calibration search could have drawn from -- the report
    # must mark the first and not the second.
    import gate_pick

    assert 5 in gate_pick.HELDOUT
    assert 50 not in gate_pick.HELDOUT

    held = red_team.Finding(
        statistic="leverage_effect", value=-0.9, band=(-0.16, 0.0),
        scaled_distance=12.0, seed=5, roster_seed=1, scenario="recession",
        manifest=None,
    )
    not_held = red_team.Finding(
        statistic="return_acf1", value=0.5, band=(-0.08, 0.06),
        scaled_distance=9.0, seed=50, roster_seed=1, scenario="recession",
        manifest=None,
    )
    budget = {
        "preset": "pt-v16", "days": 252, "roster_size": 40,
        "requested_budget": 2, "cells_run": 2, "cells_planned": 2,
        "seeds": [5, 50], "roster_seeds": [1], "scenarios": ["recession"],
        "unsearched": {"seeds": [], "roster_seeds": [], "scenarios": []},
        "fixed": {"macro": "engine default (nothing pinned beyond a "
                          "scenario)",
                 "scenario_magnitude": "shipped default for each "
                                       "scenario"},
        "workers": 1, "wall_s": 0.01,
    }
    report = red_team.Search(
        worst={held.statistic: held, not_held.statistic: not_held},
        budget=budget,
    ).report()

    held_line = next(line for line in report.splitlines()
                     if line.startswith("leverage_effect"))
    not_held_line = next(line for line in report.splitlines()
                         if line.startswith("return_acf1"))
    assert "[held-out seed]" in held_line
    assert "[held-out seed]" not in not_held_line


def test_cli_pooled_workers_agree_with_the_sequential_path(tmp_path):
    # Drives the CLI as a subprocess at both worker counts, on the true
    # ROSTER_N (40) rather than the tiny_roster fixture -- see this file's
    # module docstring for why this runs through the CLI rather than
    # calling search(..., workers=2) in-process.
    args = ["--preset", "pt-v16", "--seeds", "1,2", "--rosters", "1",
           "--scenarios", "recession", "--days", str(DAYS), "--budget", "2"]
    out_seq = tmp_path / "sequential"
    out_pooled = tmp_path / "pooled"

    for out_dir, workers in ((out_seq, "1"), (out_pooled, "2")):
        proc = subprocess.run(
            [sys.executable, TOOL, *args, "--workers", workers,
             "--out", str(out_dir)],
            capture_output=True, text=True, timeout=180,
        )
        assert proc.returncode == 0, (
            f"--workers {workers} exited {proc.returncode}\n"
            f"{proc.stderr[-2000:]}"
        )

    seq_budget = json.loads((out_seq / "budget.json").read_text())
    pooled_budget = json.loads((out_pooled / "budget.json").read_text())

    # workers changes nothing about which cells run.
    assert seq_budget["unsearched"] == pooled_budget["unsearched"]
    assert seq_budget["cells_run"] == pooled_budget["cells_run"]
    assert seq_budget["roster_size"] == pooled_budget["roster_size"] == 40

    # And the SAME cell wins: the pooled run's manifest carries the true
    # roster size (this is the ROSTER_N-through-the-job-tuple fix; before
    # it, a pooled worker's fresh import would read the module's own
    # default rather than any value the caller resolved) and the same
    # winning seed as the sequential run.
    seq_manifest = tradefloor.RunManifest.from_json(
        (out_seq / "manifest-annualised_vol_pct.json").read_text())
    pooled_manifest = tradefloor.RunManifest.from_json(
        (out_pooled / "manifest-annualised_vol_pct.json").read_text())
    assert len(pooled_manifest.universe) == 40
    assert seq_manifest.seed == pooled_manifest.seed
    assert (seq_manifest.universe.fingerprint
           == pooled_manifest.universe.fingerprint)
