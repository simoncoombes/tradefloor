"""Consumers that fold per-seed panels, held to the rows' own estimators.

Three calibration tools medianed every graded row over the seeds as if
every row were a shape row: `calibrate.py` over every key of a panel,
`long_horizon.py` and `atlas_survey.py` over `facts.REAL_MARKETS`. The
pooled fear row is None on a seed with no session at -3 percent and carries
a sample list beside it, and a median over either raises TypeError, so each
tool stopped on the first real panel after the crisis rows joined it. The
level row is a mean rather than a median. Each tool now reads the graded
rows through `facts.aggregate_panels`, and these tests feed the seam the
panels a real run produces; `tests/test_atlas.py` covers the atlas collect
the same way.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "tools" / "calibration"))

from tradefloor.facts import REAL_MARKETS, SHAPE, LEVEL, CRISIS  # noqa: E402


def _panels(count: int = 4) -> list[dict]:
    """Per-seed panels the way `facts.measure` reports them, fabricated."""
    panels = []
    for i in range(count):
        panel = {k: (low + high) / 2.0 + 0.01 * i
                 for k, (low, high) in REAL_MARKETS.items()}
        panel["seed"] = 101 + i
        panel["universe_fingerprint"] = "f" * 64
        panel["model_fingerprint"] = "pt-v1"
        panel["days"] = 252
        panel["skew"] = -0.1 * i
        panel["fear_gauge_dn1_sessions"] = 12
        if i == 0:
            panel["fear_gauge_dn3"] = None
            panel["fear_gauge_dn3_samples"] = []
            panel["fear_gauge_dn3_sessions"] = 0
        else:
            panel["fear_gauge_dn3"] = 3.0 + i
            panel["fear_gauge_dn3_samples"] = [2.0 + i, 3.0 + i, 4.0 + i]
            panel["fear_gauge_dn3_sessions"] = 3
        panels.append(panel)
    return panels


def test_calibrate_reads_a_panel_set_by_each_rows_estimator():
    calibrate = pytest.importorskip("calibrate")
    import statistics

    panels = _panels()
    medians = calibrate.panel_medians(panels)
    for key in SHAPE:
        assert medians[key] == pytest.approx(
            statistics.median(p[key] for p in panels)), key
    assert medians["index_drift_pct"] == pytest.approx(
        statistics.fmean(p["index_drift_pct"] for p in panels))
    pooled = [x for p in panels for x in p["fear_gauge_dn3_samples"]]
    assert medians["fear_gauge_dn3"] == pytest.approx(statistics.median(pooled))
    assert medians["skew"] == pytest.approx(
        statistics.median(p["skew"] for p in panels))
    # Identity fields, counts and sample lists are not statistics.
    for key in ("seed", "universe_fingerprint", "model_fingerprint", "days",
                "fear_gauge_dn3_samples", "fear_gauge_dn3_sessions"):
        assert key not in medians, key
    # And a tier with no session at -3 percent on any seed has no value for
    # the pooled row rather than a None to subtract from.
    empty = [dict(p, fear_gauge_dn3=None, fear_gauge_dn3_samples=[])
             for p in panels]
    assert "fear_gauge_dn3" not in calibrate.panel_medians(empty)


def test_long_horizon_tables_count_the_shape_rows_and_print_the_rest_beside():
    long_horizon = pytest.importorskip("long_horizon")

    acc = {d: _panels() for d in long_horizon.HORIZONS}
    # One horizon with no session at -3 percent on any seed: n/a, not a crash.
    acc[2520] = [dict(p, fear_gauge_dn3=None, fear_gauge_dn3_samples=[])
                 for p in acc[2520]]
    med, lines = long_horizon.tables(acc)
    assert "fear_gauge_dn3" in med[252]
    assert "fear_gauge_dn3" not in med[2520]
    text = "\n".join(lines)
    row = next(line for line in lines if line.startswith("fear_gauge_dn3"))
    assert "n/a" in row
    counts = [line for line in lines if "shape rows in band" in line]
    assert len(counts) == len(long_horizon.HORIZONS)
    for line in counts:
        assert f"/{len(SHAPE)} shape rows in band" in line
        for key in LEVEL + CRISIS:
            assert key in line, (key, line)
    assert "fear_gauge_dn3 n/a" in text
    # A band midpoint sits in band, so the fabricated shape rows all pass
    # and the count says so without folding the other rows in.
    assert f"{len(SHAPE)}/{len(SHAPE)} shape rows in band" in counts[0]
