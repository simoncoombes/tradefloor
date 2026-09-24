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
        # The index tail row is a pooled RATE and is aggregated from these
        # two counts, not from the rate above. Seed zero holds none, which
        # is the shape a third of real years have and the shape that broke
        # the three tools this file exists for.
        panel["index_tail_dn3_hits"] = 0 if i == 0 else i + 1
        panel["index_tail_dn3_sessions"] = 251
        panel["index_tail_dn3_pct"] = (
            100.0 * panel["index_tail_dn3_hits"] / 251)
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


# --------------------------------------------------------------------------
# A GRADED ROW THAT A RUN CANNOT READ
#
# `crisis_sector_dispersion` landed on 2026-09-22 with a ruled band at both
# horizons and a refusal: a window holding fewer than
# `facts.CRISIS_DISPERSION_MIN_SESSIONS` sessions above
# `facts.CRISIS_VIX_THRESHOLD` produces no reading at all, and
# `facts.crisis_statistics` reports the reason under `<row>_blind` instead
# of a number. Measured on pt-v19, the certified roster and seeds 101-104:
# 0, 1, 0 and 10 crisis sessions at 252 days against the thirty the row
# needs.
#
# That is the same shape as the pooled fear row above -- a graded row absent
# on some seeds and possibly on all of them -- with one difference that
# matters: this row HAS a band, so it reaches `_count_in_band`, and a row
# with a band and no reading is neither in band nor a miss. These bind the
# three seams it travels: the per-seed panel keeps the absence with its
# reason, the median omits the row, and the count names it and drops it from
# BOTH sides of the fraction.
# --------------------------------------------------------------------------


def _preset_panel():
    return pytest.importorskip("preset_panel")


def _cell(reads: int, seeds: int = 4) -> list[dict]:
    """Per-seed panels shaped the way `preset_panel._job` returns them."""
    from tradefloor.facts import DISPERSION

    row = DISPERSION[0]
    rows = []
    for i in range(seeds):
        panel = {k: (low + high) / 2.0
                 for k, (low, high) in REAL_MARKETS.items()}
        if i < reads:
            panel[row] = 1.10 + 0.01 * i
            panel[row + "_blind"] = None
        else:
            panel[row] = None
            panel[row + "_blind"] = (
                f"{row} is ABSENT for this window: it holds {i} sessions "
                f"with the volatility index above 30.883")
        panel["days"], panel["burn"] = 252, 0
        rows.append(panel)
    return rows


def test_an_absent_graded_row_is_out_of_both_sides_of_the_count():
    import statistics

    from tradefloor.facts import DISPERSION

    preset_panel = _preset_panel()
    row = DISPERSION[0]
    assert row in preset_panel.PANEL and row in preset_panel.ABSENT_OK

    bands = {k: (low, high) for k, (low, high) in REAL_MARKETS.items()}
    bands[row] = (1.0, 2.0)

    # Some seeds read: the row is graded, on the median of those seeds only.
    rows = _cell(reads=2)
    panel = preset_panel._median_panel(rows)
    assert panel[row] == pytest.approx(statistics.median([1.10, 1.11]))
    n, misses, unreadable, absent = preset_panel._count_in_band(panel, bands)
    assert absent == [] and misses == [] and unreadable == []
    assert n == len(preset_panel.PANEL)

    # NO seed reads: the row is absent, named, and in neither the numerator
    # nor the denominator. Not a miss -- `misses` is what a reader acts on --
    # and not a silent pass.
    rows = _cell(reads=0)
    panel = preset_panel._median_panel(rows)
    assert row not in panel
    n, misses, unreadable, absent = preset_panel._count_in_band(panel, bands)
    assert absent == [row]
    assert row not in misses and row not in unreadable
    assert n == len(preset_panel.PANEL) - 1
    assert n + len(misses) + len(unreadable) + len(absent) == len(
        preset_panel.PANEL)


def test_the_absence_carries_the_readable_seed_count_and_the_reason():
    """"Absent" with no count cannot be told from a run that stopped
    measuring the row, which is the `not_shown`-shrinks failure in another
    spelling. `preset_panel._absence` is what keeps the two apart."""
    from tradefloor.facts import DISPERSION

    preset_panel = _preset_panel()
    row = DISPERSION[0]

    block = preset_panel._absence(_cell(reads=2))[row]
    assert (block["read"], block["seeds"]) == (2, 4)
    assert block["value"] is not None
    assert block["estimator"].startswith("median")
    assert block["reasons"] and all("ABSENT" in r for r in block["reasons"])

    empty = preset_panel._absence(_cell(reads=0))[row]
    assert (empty["read"], empty["seeds"]) == (0, 4)
    assert empty["value"] is None
    # The library's own sentences, deduplicated: four seeds, four distinct
    # session counts, four reasons. A block with a count and no reason would
    # say that nothing read and not why.
    assert len(empty["reasons"]) == 4


def test_a_shape_row_is_never_absent_from_the_panel():
    """The other half of the rule, asserted so the `.get` cannot spread.

    `_job` fetches a shape row with `p[k]` and an `ABSENT_OK` row with
    `.get`. A shape row is a property of the returns and reads on every run;
    if one could go missing the same way, the "14 of 14" denominator would
    shrink without anything failing.
    """
    from tradefloor.facts import SHAPE

    preset_panel = _preset_panel()
    assert not set(SHAPE) & set(preset_panel.ABSENT_OK)
    assert set(preset_panel.ABSENT_OK) < set(preset_panel.PANEL)
