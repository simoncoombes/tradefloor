"""The realism envelope as data: intervals, and the membership check."""

from unittest import mock

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


def test_all_fourteen_are_in_band_at_the_certified_horizon():
    """Nine of ten until 2026-08-25, when the panel grew to fourteen, then
    twelve of fourteen on pt-v3. Since the 2026-08-26 era boundary the
    default is pt-v10 and every statistic is in band, including the
    volume-change row no earlier preset held. Pinned so that a change to it
    is a decision, not a drift."""
    in_band = [k for k, v in env.CERTIFIED.items()
               if band_distance(v, *REAL_MARKETS[k]) == 0]
    assert len(in_band) == 14, sorted(set(env.CERTIFIED) - set(in_band))
    assert "volume_change_acf1" in in_band
    assert "sector_excess_corr" in in_band


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


def test_the_volume_change_row_is_now_inside_at_both_horizons():
    """Three eras of one statistic, and this test has pinned all of them.

    It was outside at every horizon before 0.2.0. pt-v10 brought it inside at
    252 days alongside `volume_abs_return_corr`, which every earlier preset
    had to trade against it, and this test then pinned "inside at 252, out at
    504". pt-v12 brought it inside at 504 too: -0.2656 and -0.2572 against
    bands of -0.32..-0.20 and -0.29..-0.21.

    So the `volume-change` gap is retired, and `check` must stop reporting it
    at BOTH horizons. A retired gap that a `check` still returns would deny a
    caller a certification the measurements support, which is the same class
    of error as granting one they do not (§114 and the two gaps retired at
    the previous boundary for the same reason).
    """
    inside = env.check(horizon_days=252, statistics=["volume_change_acf1"])
    assert inside.inside

    # At 504 the `horizon` gap still fires -- the certified horizon is 252 and
    # that is a statement about what was CERTIFIED, not about this row. What
    # must no longer fire, at either horizon, is `volume-change`.
    far = env.check(horizon_days=504, statistics=["volume_change_acf1"])
    assert [g.id for g in far.gaps] == ["horizon"]

    for verdict in (inside, far):
        assert not any(g.id == "volume-change" for g in verdict.gaps)
    assert not any(g.id == "volume-change" for g in env.GAPS)

    # And the horizon gap's own reason must not claim a row misses while
    # quoting a number inside the band it prints beside it.
    assert "missing" not in " ".join(far.reasons), far.reasons


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


def test_the_certified_comment_matches_the_certified_numbers():
    """A comment that contradicts the data below it is worse than no comment.

    The note above `CERTIFIED` read "nine of ten in band" until 2026-08-26.
    It described pt-v3, and it survived two era boundaries and four
    statistics joining the panel, because nothing tests a comment. A reader
    checking whether to trust this library reads that line.

    So the count is asserted against what `score` actually returns. If a
    future preset changes it, this fails and the comment gets updated on
    purpose rather than left to rot.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "python" / "pretium"
           / "envelope.py").read_text(encoding="utf-8")
    note = src[:src.index("CERTIFIED: dict[str, float] = {")]
    note = note[note.rindex("#: Measured at the certified horizon"):]

    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    scored = env.score(panel, horizon_days=env.CERTIFIED_HORIZON_DAYS)
    words = {14: "ALL FOURTEEN", 13: "thirteen of fourteen",
             12: "twelve of fourteen"}
    expected = words.get(scored["in_band"])
    assert expected is not None, (
        f"{scored['in_band']} of {scored['of']} in band and this test has no "
        "wording for it; add one rather than deleting the assertion")
    assert expected in note, (
        f"the note above CERTIFIED does not say {expected!r}, but the panel "
        f"holds {scored['in_band']} of {scored['of']}:\n{note}")


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

    A SYNTHETIC panel, not `CERTIFIED`. This test used the shipped preset's
    own `volume_change_acf1` as the demonstration, which worked only while
    that statistic happened to sit between the two bands; pt-v12 moved it
    inside both and the test started asserting something false about a
    better model. What is under test is `score`'s choice of ruler, which has
    nothing to do with which preset ships, so the example is now a literal.

    `excess_kurtosis` at 5.23 is comfortably inside the 252-day band of
    1.6-41 and OUT of the horizon-matched 504-day band of 7.1-22. Scoring a
    504-day panel against the 252-day bands has been done repeatedly in this
    project; the horizon picks the ruler here so a caller cannot pair one
    with the other by accident.
    """
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    panel["excess_kurtosis"] = 5.23
    near = env.score(panel, horizon_days=252)
    far = env.score(panel, horizon_days=504)
    assert near["ruler"] == "REAL_MARKETS"
    assert far["ruler"] == "REAL_MARKETS_504"
    assert near["statistics"]["excess_kurtosis"]["in_band"]
    assert not far["statistics"]["excess_kurtosis"]["in_band"], (
        "5.23 is inside the 252-day kurtosis band and outside the tighter "
        "504-day one; a score that missed that is measuring with the wrong "
        "ruler"
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
    the thin-tails gap, which was retired at 0.2.0 when the shipped preset
    closed it too -- and it surrenders `return_acf1` at the certified
    horizon. It was called a win twice before anyone counted the panel
    (CALIBRATION-FOLLOWUPS §33), which is why this is a function now.
    """
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    low, high = REAL_MARKETS["return_acf1"]
    panel["return_acf1"] = high + 0.014      # pt-v4 measures 0.0739 vs 0.06
    assert env.regressions(panel) == ["return_acf1"]


def test_a_structural_statistic_the_shipped_preset_holds_is_a_regression():
    """The inverse of what this test asserted before 0.2.0, and why.

    `volume_change_acf1` sits outside the calibration objective, and while
    the shipped preset was itself out of band on it, losing it cost a
    candidate nothing that was ever held. pt-v10 holds all fourteen at the
    certified horizon. A candidate that drops one is now giving up something
    that ships, whether or not the objective was pointed at it.
    """
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    panel["volume_change_acf1"] = -99.0
    assert env.regressions(panel) == ["volume_change_acf1"]


def test_a_row_the_shipped_preset_does_not_hold_cannot_be_lost():
    """The condition that always did the work, asserted directly.

    Nothing can be blamed on a candidate for a row the baseline misses too,
    which is what keeps this function from calling every candidate a
    regression the moment a statistic leaves the shipped panel.
    """
    panel = {k: env.CERTIFIED[k] for k in REAL_MARKETS}
    low, _ = REAL_MARKETS["return_acf1"]
    baseline_miss = dict(env.CERTIFIED, return_acf1=low - 1.0)
    with mock.patch.object(env, "CERTIFIED", baseline_miss):
        panel["return_acf1"] = low - 2.0
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
