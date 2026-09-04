"""The realism envelope as data: intervals, and the membership check."""

from unittest import mock

import pytest

import tradefloor
from tradefloor import envelope as env
from tradefloor.facts import REAL_MARKETS, band_distance


def test_the_envelope_describes_the_preset_that_actually_ships():
    """If this fails, every constant in the module describes another model."""
    assert tradefloor.model_preset()["name"] == env.PRESET


def test_the_certified_panel_covers_every_measured_statistic():
    # A statistic in the panel but absent here would be uncertified and
    # unmentioned, which is the silent kind of gap this module exists to
    # make impossible.
    from tradefloor.facts import SHAPE, LEVEL, CRISIS
    # The split: the shape rows fill the certified table, the level and
    # crisis rows have tables of their own, and the 504-day bands cover
    # every graded row while the 504-day measurements cover the shape rows.
    assert sorted(env.CERTIFIED) == sorted(SHAPE)
    assert set(env.CERTIFIED_LEVEL) <= set(LEVEL)
    assert set(env.CERTIFIED_CRISIS) <= set(CRISIS)
    assert sorted(env.BANDS_504) == sorted(REAL_MARKETS)
    assert sorted(env.MEASURED_504) == sorted(SHAPE)


def test_all_fourteen_are_in_band_at_the_certified_horizon():
    """Nine of ten until 2026-08-25, when the panel grew to fourteen, then
    twelve of fourteen on pt-v3. Since the 2026-08-26 era boundary the
    default is pt-v10 and every statistic is in band, including the
    volume-change row no earlier preset held. Pinned so that a change to it
    is a decision, not a drift."""
    in_band = [k for k, v in env.CERTIFIED.items()
               if band_distance(v, *REAL_MARKETS[k]) == 0]
    # Fourteen SHAPE rows. The level row is graded and held red in its own
    # table, and it never joins this count.
    assert len(in_band) == 14, sorted(set(env.CERTIFIED) - set(in_band))
    from tradefloor.facts import LEVEL, CRISIS
    # Which of the new rows the default preset is EXPECTED to fail, named
    # rather than assumed of all of them. The -1 per cent fear row passes at
    # pt-v16 and was predicted to, before it was measured: one bucket cannot
    # separate a low gain from a saturating channel, and a row at -1 per cent
    # alone would have scored this defect as passing for three eras. That it
    # passes is the ARGUMENT for the second bucket, not a sign the band moved.
    EXPECTED_RED = {"index_drift_pct", "fear_gauge_dn3"}
    for k, v in list(env.CERTIFIED_LEVEL.items()) + list(env.CERTIFIED_CRISIS.items()):
        assert k in LEVEL + CRISIS
        red = band_distance(v, *REAL_MARKETS[k]) != 0
        if k in EXPECTED_RED:
            assert red, (
                f"{k} reads in band at the default preset; the row is held "
                "red until the level is right, and a pass here means the "
                "band moved")
        else:
            assert not red, (
                f"{k} reads OUT of band at the default preset and was "
                "expected in. Either the measurement moved or this row "
                "belongs in EXPECTED_RED, and which it is decides whether "
                "the second bucket is still earning its place")
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
    with pytest.raises(tradefloor.ValidationError):
        env.check(horizon_days=252, statistics=["sharpe_ratio"])


def test_a_nonsense_horizon_is_refused():
    with pytest.raises(tradefloor.ValidationError):
        env.check(horizon_days=0)


def test_intervals_refuse_a_single_panel():
    # A spread over one observation is not a spread.
    panel = {k: 0.0 for k in REAL_MARKETS}
    with pytest.raises(tradefloor.ValidationError):
        env.intervals([panel])


def test_intervals_report_the_spread_and_both_containment_tests():
    # Two synthetic panels straddling the volatility ceiling: the median
    # sits inside the band, the range does not.
    lo, hi = REAL_MARKETS["annualised_vol_pct"]
    panels = []
    for v in (hi - 1.0, hi + 6.0, hi - 3.0):
        p = {k: env.CERTIFIED[k] for k in env.CERTIFIED}
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

    # The documentation site moved to its own repository and took
    # `docs/envelope.json` with it, so this check has nowhere to run here
    # and has failed on a missing file since the move.
    #
    # A skip and not a deletion, and the reason says exactly where the
    # check belongs, because the drift it catches is real: the module
    # gained a sixth gap and the published artifact kept quoting five.
    # Someone has to run this against the docs checkout. A silent skip
    # would let that be nobody.
    artifact = Path(__file__).resolve().parent.parent / "docs" / "envelope.json"
    if not artifact.exists():
        pytest.skip(
            "docs/envelope.json is not in this repository: the docs site "
            "lives in simoncoombes/tradefloor-docs. This check belongs "
            "there, against that checkout, and until it runs there the "
            "published envelope artifact is unverified."
        )
    doc = json.loads(artifact.read_text(encoding="utf-8"))
    assert doc["preset"] == env.PRESET
    assert doc["certified_horizon_days"] == env.CERTIFIED_HORIZON_DAYS
    assert [g["id"] for g in doc["gaps"]] == [g.id for g in env.GAPS], (
        "docs/envelope.json's gap list has drifted from tradefloor.envelope.GAPS; "
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

    # The package directory, read from the imported module rather than
    # spelled out: this line said "pretium" for a week after the rename and
    # the test failed on a missing file. Asking the module where it lives
    # cannot go stale the next time the package moves.
    src = Path(env.__file__).read_text(encoding="utf-8")
    note = src[:src.index("CERTIFIED: dict[str, float] = {")]
    note = note[note.rindex("#: Measured at the certified horizon"):]

    panel = {k: env.CERTIFIED[k] for k in env.CERTIFIED}
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
    assert len(d["statistics"]) == (len(env.CERTIFIED) + len(env.CERTIFIED_LEVEL)
                                    + len(env.CERTIFIED_CRISIS))
    assert set(d["statistics"]) | set(d["unmeasured"]) == set(REAL_MARKETS)
    assert set(d["groups"]) == {"shape", "level", "crisis"}
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
    panel = {k: env.CERTIFIED[k] for k in env.CERTIFIED}
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
    rows = env.score({k: env.CERTIFIED[k] for k in env.CERTIFIED})["statistics"]
    for name, row in rows.items():
        if row["in_band"]:
            assert row["room_sd"] is None or row["room_sd"] >= 0, name


def test_the_shipped_preset_does_not_regress_itself():
    panel = {k: env.CERTIFIED[k] for k in env.CERTIFIED}
    assert env.regressions(panel) == []


def test_a_panel_that_loses_a_statistic_is_named():
    """pt-v4's actual failure, pinned.

    It halves the dual-horizon objective and is the first vector to close
    the thin-tails gap, which was retired at 0.2.0 when the shipped preset
    closed it too -- and it surrenders `return_acf1` at the certified
    horizon. It was called a win twice before anyone counted the panel
    (CALIBRATION-FOLLOWUPS §33), so this is a function now.
    """
    panel = {k: env.CERTIFIED[k] for k in env.CERTIFIED}
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
    panel = {k: env.CERTIFIED[k] for k in env.CERTIFIED}
    panel["volume_change_acf1"] = -99.0
    assert env.regressions(panel) == ["volume_change_acf1"]


def test_a_row_the_shipped_preset_does_not_hold_cannot_be_lost():
    """The condition that always did the work, asserted directly.

    Nothing can be blamed on a candidate for a row the baseline misses too,
    which keeps this function from calling every candidate a
    regression the moment a statistic leaves the shipped panel.
    """
    panel = {k: env.CERTIFIED[k] for k in env.CERTIFIED}
    low, _ = REAL_MARKETS["return_acf1"]
    baseline_miss = dict(env.CERTIFIED, return_acf1=low - 1.0)
    with mock.patch.object(env, "CERTIFIED", baseline_miss):
        panel["return_acf1"] = low - 2.0
        assert env.regressions(panel) == []


def test_regressions_refuses_a_horizon_it_has_no_baseline_for():
    # CERTIFIED is measured at 252. Comparing a 504-day panel against it
    # would be the wrong-ruler error wearing a different hat.
    panel = {k: env.CERTIFIED[k] for k in env.CERTIFIED}
    with pytest.raises(tradefloor.ValidationError):
        env.regressions(panel, horizon_days=504)


def test_an_unknown_statistic_is_refused_by_score():
    with pytest.raises(tradefloor.ValidationError):
        env.score({"sharpe_ratio": 1.0})


#: The lags the log-log fit behind `REAL_DECAY_SLOPE` runs over. Two of them,
#: 2 and 3, are absent from `REAL_DECAY`, so the fit cannot be reproduced from
#: the module alone: it returns -0.4615 against the shipped -0.436. The
#: committed curve is the only source for them.
_SLOPE_LAGS = (1, 2, 3, 5, 8, 12, 20)


def _committed_curve():
    """`decay-curve-504.json`, which tradefloor.dev cites as the real side."""
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "decay-curve-504.json"
    assert path.exists(), (
        f"{path.name} is missing from the repository root. tradefloor.dev's "
        "realism-metrics page cites it as the source of REAL_DECAY and "
        "REAL_DECAY_SLOPE, and nothing else in the library carries lags 2 "
        "and 3 of the real curve."
    )
    return json.loads(path.read_text(encoding="utf-8"))


def _log_log_slope(curve, lags):
    """Least-squares slope through (log lag, log autocorrelation)."""
    import math

    xs = [math.log(lag) for lag in lags]
    ys = [math.log(curve[lag]) for lag in lags]
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum(
        (x - mx) ** 2 for x in xs
    )


def test_the_committed_curve_reproduces_its_own_median():
    """The median block is derived, so it is recomputed here rather than read.

    The file ships six windows and describes the median as covering the five
    that carry no crisis flag. It shipped no flags at all until now, which
    made that sentence unverifiable: a reader could not tell which window was
    excluded, and neither could a test.
    """
    import statistics

    data = _committed_curve()
    noncrisis = [w for w in data["real_windows"] if not w.get("crisis")]
    assert len(noncrisis) == 5, (
        f"the median is described as five non-crisis windows, and "
        f"{len(noncrisis)} of {len(data['real_windows'])} carry no crisis flag"
    )
    for lag, published in data["real_median_noncrisis"].items():
        recomputed = statistics.median(w["curve"][lag] for w in noncrisis)
        assert recomputed == pytest.approx(published, abs=1e-12), (
            f"lag {lag}: real_median_noncrisis says {published}, the "
            f"non-crisis windows give {recomputed}"
        )


def test_real_decay_is_the_committed_curve_rounded():
    """`REAL_DECAY` is the published artifact rounded to four places.

    tradefloor.dev tells a reader that the real side of the decay comparison
    is reproducible from the tree and names this file. Nothing executed that
    claim until now, so an edit to either side could carry the other out of
    agreement while the suite stayed green.
    """
    curve = _committed_curve()["real_median_noncrisis"]
    for lag, value in env.REAL_DECAY.items():
        assert round(curve[str(lag)], 4) == value, (
            f"REAL_DECAY[{lag}] is {value}; decay-curve-504.json rounds to "
            f"{round(curve[str(lag)], 4)}"
        )


def test_real_decay_slope_is_the_seven_lag_fit_on_that_curve():
    """`REAL_DECAY_SLOPE` needs two lags the module does not carry.

    `REAL_DECAY` holds five of the seven lags the fit runs over, skipping 2
    and 3. Fitting the five returns -0.4615 against the shipped -0.436, so
    the committed curve earns its place at the repository root. Both figures
    reach a reader: the gap messages quote the slope as about 2.2x steeper
    than real markets.
    """
    curve = {
        int(lag): value
        for lag, value in _committed_curve()["real_median_noncrisis"].items()
    }
    fitted = _log_log_slope(curve, _SLOPE_LAGS)
    assert round(fitted, 3) == env.REAL_DECAY_SLOPE, (
        f"REAL_DECAY_SLOPE is {env.REAL_DECAY_SLOPE}; the fit over lags "
        f"{list(_SLOPE_LAGS)} of decay-curve-504.json gives {fitted:.4f}"
    )

    from_module_only = _log_log_slope(
        env.REAL_DECAY, [l for l in _SLOPE_LAGS if l in env.REAL_DECAY]
    )
    assert round(from_module_only, 3) != env.REAL_DECAY_SLOPE, (
        "REAL_DECAY now reproduces the slope on its own, so the committed "
        "curve is no longer load-bearing for it. Say so in the file's note "
        "before this assertion is deleted."
    )


def test_the_model_slope_needs_no_committed_file():
    """`DECAY_SLOPE` is the same fit over `DECAY_252`, which carries all seven.

    The asymmetry is the point. `DECAY_252` holds every lag the fit needs, so
    the model side of the comparison stays checkable with no committed
    artifact, while the real side needs one.

    The agreement is approximate, and the tolerance says why. `DECAY_252` is
    published rounded to four places and the full-precision panel it came
    from is not in this repository, so the fit over the rounded values
    returns -0.9522 where the constant reads -0.953. The real side rounds
    exactly, because there the full-precision source IS committed.
    """
    fitted = _log_log_slope(env.DECAY_252, _SLOPE_LAGS)
    assert fitted == pytest.approx(env.DECAY_SLOPE, abs=2e-3), (
        f"DECAY_SLOPE is {env.DECAY_SLOPE}; the fit over the published "
        f"DECAY_252 gives {fitted:.4f}"
    )
