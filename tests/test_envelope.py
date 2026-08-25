"""The realism envelope as data: intervals, and the membership check."""

import pytest

import pretium
from pretium import envelope as env
from pretium.facts import REAL_MARKETS, band_distance


def test_the_envelope_describes_the_preset_that_actually_ships():
    """If this fails, every constant in the module describes another model."""
    assert pretium.model_preset()["name"] == env.PRESET


def test_the_certified_panel_covers_every_measured_statistic():
    # A statistic in the panel but absent here would be uncertified and
    # unmentioned, which is the silent kind of gap this module exists to
    # make impossible.
    assert sorted(env.CERTIFIED) == sorted(REAL_MARKETS)
    assert sorted(env.BANDS_504) == sorted(REAL_MARKETS)
    assert sorted(env.MEASURED_504) == sorted(REAL_MARKETS)


def test_twelve_of_fourteen_are_in_band_at_the_certified_horizon():
    """Nine of ten until 2026-08-25, when three conditional correlation
    statistics joined the panel. Two of them are in band; the third,
    sector_excess_corr, is 15 seed-sd out and is gap 7. The count is pinned
    so that a change to it is a decision, not a drift."""
    in_band = [k for k, v in env.CERTIFIED.items()
               if band_distance(v, *REAL_MARKETS[k]) == 0]
    assert len(in_band) == 12
    assert "corr_persistence_acf1" in in_band  # a band that admits everything at 252 days, and says so
    assert "volume_change_acf1" not in in_band
    assert "sector_excess_corr" not in in_band


def test_every_gap_says_what_it_forbids():
    # A gap nobody can act on is trivia. The `forbids` field is the whole
    # point of publishing the list.
    assert env.GAPS
    for gap in env.GAPS:
        assert gap.forbids.strip(), gap.id
        assert gap.detail.strip(), gap.id
        for name in gap.statistics:
            assert name in REAL_MARKETS, (gap.id, name)


def test_a_one_year_question_on_certified_statistics_is_inside():
    v = env.check(horizon_days=252,
                  statistics=["return_acf1", "abs_return_acf1"])
    assert v.inside
    assert bool(v) is True
    assert not v.gaps


def test_a_multi_year_question_is_outside_and_says_which_measurement():
    v = env.check(horizon_days=756, statistics=["abs_return_acf1"])
    assert not v.inside
    assert bool(v) is False
    assert any("504" in r for r in v.reasons)
    assert any(g.id == "horizon" for g in v.gaps)


def test_long_memory_is_outside_even_within_the_certified_horizon():
    """The decay-shape gap is not a horizon gap: it applies at 252 days too,
    because the curve is already negative by lag 30 there."""
    v = env.check(horizon_days=252, statistics=["abs_return_acf20"])
    assert not v.inside
    assert any(g.id == "decay-shape" for g in v.gaps)


def test_a_concentrated_roster_is_outside():
    v = env.check(horizon_days=252, sector_concentrated=True)
    assert not v.inside
    assert any(g.id == "roster-concentration" for g in v.gaps)


def test_the_structural_statistic_is_always_outside():
    v = env.check(horizon_days=10, statistics=["volume_change_acf1"])
    assert not v.inside
    assert any(g.id == "volume-change" for g in v.gaps)


def test_an_unknown_statistic_is_refused_not_ignored():
    # Silently dropping an unrecognised name would grant a certification
    # nobody measured.
    with pytest.raises(pretium.ValidationError):
        env.check(horizon_days=252, statistics=["sharpe_ratio"])


def test_a_nonsense_horizon_is_refused():
    with pytest.raises(pretium.ValidationError):
        env.check(horizon_days=0)


def test_intervals_refuse_a_single_panel():
    # A spread over one observation is not a spread.
    panel = {k: 0.0 for k in REAL_MARKETS}
    with pytest.raises(pretium.ValidationError):
        env.intervals([panel])


def test_intervals_report_the_spread_and_both_containment_tests():
    # Two synthetic panels straddling the volatility ceiling: the median
    # sits inside the band, the range does not.
    lo, hi = REAL_MARKETS["annualised_vol_pct"]
    panels = []
    for v in (hi - 1.0, hi + 6.0, hi - 3.0):
        p = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
        p["annualised_vol_pct"] = v
        panels.append(p)
    rows = env.intervals(panels)
    row = rows["annualised_vol_pct"]
    assert row["low"] == hi - 3.0 and row["high"] == hi + 6.0
    assert band_distance(row["median"], lo, hi) == 0, "median must be in band"
    assert row["distance"] == 0
    assert row["extremes_straddle"], "max is above the ceiling"
    assert row["seeds"] == 3
    assert env.report_intervals(rows)


def test_the_published_artifact_matches_the_module():
    """`docs/envelope.json` is what the envelope page tells readers to cite.

    It drifted: the module gained a sixth gap -- certification was measured
    on a sector-balanced roster, which no real index is -- and the artifact
    was not regenerated with it. A reader citing the file would have quoted
    five gaps from a model that has six, and the page directs them to the
    file precisely BECAUSE prose goes stale.

    Asserted so the citable thing cannot be the wrong thing.
    """
    import json
    from pathlib import Path

    doc = json.loads(
        (Path(__file__).resolve().parent.parent / "docs" / "envelope.json")
        .read_text(encoding="utf-8")
    )
    assert doc["preset"] == env.PRESET
    assert doc["certified_horizon_days"] == env.CERTIFIED_HORIZON_DAYS
    assert [g["id"] for g in doc["gaps"]] == [g.id for g in env.GAPS], (
        "docs/envelope.json's gap list has drifted from pretium.envelope.GAPS; "
        "regenerate it from envelope.certified()"
    )


def test_certified_serialises_for_a_manifest():
    d = env.certified()
    assert d["preset"] == env.PRESET
    assert d["certified_horizon_days"] == env.CERTIFIED_HORIZON_DAYS
    assert len(d["statistics"]) == len(REAL_MARKETS)
    assert len(d["gaps"]) == len(env.GAPS)
    assert all(g["forbids"] for g in d["gaps"])


# -- the objective / certification reconciliation ---------------------------


def test_score_reads_a_panel_against_its_own_horizon():
    """The wrong-ruler error, made hard to commit.

    `excess_kurtosis` at 5.23 is comfortably inside the 252-day band of
    1.6-41 and OUT of the horizon-matched 504-day band of 7.1-22. Scoring a
    504-day panel against the 252-day bands has been done repeatedly in this
    project; the horizon picks the ruler here so a caller cannot pair one
    with the other by accident.
    """
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    near = env.score(panel, horizon_days=252)
    far = env.score(panel, horizon_days=504)
    assert near["ruler"] == "REAL_MARKETS"
    assert far["ruler"] == "REAL_MARKETS_504"
    assert near["statistics"]["excess_kurtosis"]["in_band"]
    assert not far["statistics"]["excess_kurtosis"]["in_band"], (
        "5.23 is inside the 252-day band and outside the 504-day one; a "
        "score that missed that is measuring with the wrong ruler"
    )


def test_score_reports_room_not_just_membership():
    # A statistic barely inside is one seed away from not being, and the
    # band loss is flat inside a band so it cannot see the difference.
    rows = env.score({k: env.CERTIFIED[k] for k in REAL_MARKETS})["statistics"]
    for name, row in rows.items():
        if row["in_band"]:
            assert row["room_sd"] is None or row["room_sd"] >= 0, name


def test_the_shipped_preset_does_not_regress_itself():
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    assert env.regressions(panel) == []


def test_a_panel_that_loses_a_statistic_is_named():
    """pt-v4's actual failure, pinned.

    It halves the dual-horizon objective and is the first vector to close
    the thin-tails gap -- and it surrenders `return_acf1` at the certified
    horizon. It was called a win twice before anyone counted the panel
    (CALIBRATION-FOLLOWUPS §33), which is why this is a function now.
    """
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    low, high = REAL_MARKETS["return_acf1"]
    panel["return_acf1"] = high + 0.014      # pt-v4 measures 0.0739 vs 0.06
    assert env.regressions(panel) == ["return_acf1"]


def test_a_structural_statistic_is_never_counted_as_a_regression():
    """`volume_change_acf1` is out of band by design and excluded from the
    objective. A candidate can neither be blamed for it nor credited with
    it, and counting it would make every candidate look like a regression."""
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    panel["volume_change_acf1"] = -99.0
    assert env.regressions(panel) == []


def test_regressions_refuses_a_horizon_it_has_no_baseline_for():
    # CERTIFIED is measured at 252. Comparing a 504-day panel against it
    # would be the wrong-ruler error wearing a different hat.
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    with pytest.raises(pretium.ValidationError):
        env.regressions(panel, horizon_days=504)


def test_an_unknown_statistic_is_refused_by_score():
    with pytest.raises(pretium.ValidationError):
        env.score({"sharpe_ratio": 1.0})
