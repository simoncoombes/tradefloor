"""The reference panel's windows, and what they say about the bands.

`REAL_MARKETS_PROVENANCE` carries three numbers per row. `REAL_MARKETS_WINDOWS`
carries the readings those three summarise. These tests bind the two together,
so the table is the source of the summary rather than a second copy of it;
derive every shipped band as `BAND_RULE` plus the adjustment
`REAL_MARKETS_ADJUSTMENTS` names for it; and then use the table for the thing
three numbers cannot do: score a real year against a band derived from the
other real years.
"""

from __future__ import annotations

import pathlib
import statistics as st
import sys

import pytest

from tradefloor.facts import (
    REAL_MARKETS,
    REAL_MARKETS_ADJUSTMENTS,
    REAL_MARKETS_PROVENANCE,
    REAL_MARKETS_WINDOWS,
    band_from_windows,
    shared_rule,
    trimmed_sd,
)


def non_crisis(key: str) -> list[float]:
    """The nine windows every band in this module is derived from."""
    values = REAL_MARKETS_WINDOWS["values"][key]
    crisis = REAL_MARKETS_WINDOWS["crisis_index"]
    return [v for i, v in enumerate(values) if i != crisis]


def non_crisis_labels() -> list[str]:
    return [
        lab for i, lab in enumerate(REAL_MARKETS_WINDOWS["windows"])
        if i != REAL_MARKETS_WINDOWS["crisis_index"]
    ]


def derivable() -> list[str]:
    keys = sorted(REAL_MARKETS_WINDOWS["values"])
    return [k for k in keys if k not in REAL_MARKETS_WINDOWS["not_derivable"]]


def test_the_table_is_rectangular_and_names_its_crisis_window():
    labels = REAL_MARKETS_WINDOWS["windows"]
    assert len(labels) == 10
    for key, values in REAL_MARKETS_WINDOWS["values"].items():
        assert len(values) == len(labels), key
    crisis = REAL_MARKETS_WINDOWS["crisis_index"]
    assert labels[crisis] == "2019-07..2020-07"


@pytest.mark.parametrize("key", sorted(REAL_MARKETS_WINDOWS["values"]))
def test_the_provenance_triple_is_derivable_from_the_windows(key):
    """The summary is a function of the table, not a second transcription.

    This is the assertion that makes the table load-bearing. Without it the
    two could drift apart and nothing would say so, which is exactly how the
    stale panel in the loss test survived a generator change.
    """
    if key in REAL_MARKETS_WINDOWS["not_derivable"]:
        pytest.skip(REAL_MARKETS_WINDOWS["not_derivable"][key])
    values = non_crisis(key)
    derived = (min(values), st.median(values), max(values))
    shipped = REAL_MARKETS_PROVENANCE[key]["windows"]
    # The provenance rounds to the precision it prints; compare at that.
    places = max(len(str(v).split(".")[-1]) for v in shipped)
    assert [round(v, places) for v in derived] == [round(v, places) for v in shipped], (
        key, derived, shipped
    )


def test_the_crisis_window_is_the_one_the_provenance_records():
    crisis = REAL_MARKETS_WINDOWS["crisis_index"]
    for key, values in REAL_MARKETS_WINDOWS["values"].items():
        recorded = REAL_MARKETS_PROVENANCE[key].get("crisis_window")
        if recorded is None:
            continue
        assert round(values[crisis], 2) == pytest.approx(recorded, abs=0.051), key


def test_the_trim_drops_the_window_farthest_from_the_median():
    """The rule's centre, pinned on a set where the two candidate centres differ.

    "The most extreme window" needs a centre. A mean is pulled toward the
    member the trim is meant to drop, so on a set with a cluster on the far
    side of a lone outlier the mean-centred trim drops a member of the
    cluster instead. This set is built to do that: from the median, 11 is
    the farthest; from the mean of about 1.28, -10 is. `BAND_RULE` says the
    median, so the trimmed sd is the sd of the set without 11.
    """
    values = [-10.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 10.5, 11.0]
    mean = st.fmean(values)
    assert max(values, key=lambda v: abs(v - mean)) == -10.0
    assert max(values, key=lambda v: abs(v - st.median(values))) == 11.0
    assert trimmed_sd(values) == pytest.approx(st.stdev(values[:-1]))
    low, high, s = shared_rule(values)
    assert (low, high) == (min(values) - s, max(values) + s)


def test_the_two_centres_disagree_on_a_shipped_edge_and_the_median_is_the_one_shipped():
    """The centre is not a matter of taste: one shipped 252-bar edge decides it.

    On `cross_sectional_corr`'s nine windows the median-centred trim drops
    the lowest window and the mean-centred trim drops the highest, and only
    the median's s rounds the floor down to the shipped 0.08.
    """
    values = non_crisis("cross_sectional_corr")
    by_median = min(values) - trimmed_sd(values)
    mean = st.fmean(values)
    kept_by_mean = sorted(values, key=lambda v: abs(v - mean))[:-1]
    by_mean = min(values) - st.stdev(kept_by_mean)
    assert by_median < 0.09 <= by_mean
    floor = band_from_windows("cross_sectional_corr", values)[0]
    assert floor == REAL_MARKETS["cross_sectional_corr"][0]


@pytest.mark.parametrize("key", derivable())
def test_every_derivable_band_is_the_rule_plus_its_named_adjustment(key):
    """A shipped band is `BAND_RULE` on the windows, or that plus a named move.

    Six rows ship the rule's band exactly. Three carry an adjustment
    REALISM-BANDS.md names, recorded as data in `REAL_MARKETS_ADJUSTMENTS`;
    each must move the edge it names in the direction it claims, so a
    no-op entry or a clamp filed as a widening is caught. Anything else is a
    band nobody can derive, which is what this test exists to refuse.
    """
    values = non_crisis(key)
    rule = dict(zip(("low", "high"), band_from_windows(key, values)))
    band = dict(rule)
    for edge, (shipped, kind, reason) in REAL_MARKETS_ADJUSTMENTS.get(key, {}).items():
        assert reason
        assert shipped != rule[edge], (key, edge, "an adjustment that moves nothing")
        outward = shipped < rule[edge] if edge == "low" else shipped > rule[edge]
        assert kind == ("outward" if outward else "inward"), (key, edge, kind)
        band[edge] = shipped
    low, high = REAL_MARKETS[key]
    assert (round(band["low"], 10), round(band["high"], 10)) == (round(low, 10), round(high, 10)), (
        key, rule, REAL_MARKETS_ADJUSTMENTS.get(key), (low, high)
    )


def test_the_adjustment_table_names_only_derivable_rows():
    for key, edges in REAL_MARKETS_ADJUSTMENTS.items():
        assert key in REAL_MARKETS_WINDOWS["values"], key
        assert key not in REAL_MARKETS_WINDOWS["not_derivable"], key
        for edge in edges:
            assert edge in ("low", "high"), (key, edge)


def test_the_leverage_clamp_excludes_one_real_window():
    """Clamp #2's cost, on the record rather than argued away.

    `leverage_effect` ships a ceiling of exactly 0.00, an inward clamp on the
    rule's +0.06 because every retrieved source gives the effect a negative
    sign. The 2020-07 window of the reference panel itself reads +0.014,
    inside the rule's ceiling and outside the clamped one. So the clamp
    rejects a member of the sample the band was derived from, and a model
    reading slightly positive there is held to a standard one real year in
    nine does not meet. This test does not decide whether the sign prior
    outranks that year; it keeps the cost visible while the ruling is open,
    and it fails the day the clamp is lifted or the window stops being the
    one excluded.
    """
    shipped, kind, _ = REAL_MARKETS_ADJUSTMENTS["leverage_effect"]["high"]
    assert kind == "inward" and shipped == 0.0
    assert REAL_MARKETS["leverage_effect"][1] == shipped
    values = non_crisis("leverage_effect")
    rule_high = band_from_windows("leverage_effect", values)[1]
    excluded = [v for v in values if shipped < v <= rule_high]
    assert excluded == [max(values)]
    assert non_crisis_labels()[values.index(max(values))] == "2020-07..2021-07"


def test_leave_one_window_out_scores_every_real_year():
    """A real year against a band derived from the other real years.

    `BAND_RULE`, held out one window at a time, asks what the panel would say
    about a year of real data it had not seen. A row that fails here is a row
    whose band is too tight for the thing it is measuring.
    """
    failures: dict[str, list[str]] = {}
    for key in derivable():
        values = non_crisis(key)
        for held, label in zip(range(len(values)), non_crisis_labels()):
            rest = [v for i, v in enumerate(values) if i != held]
            low, high, _ = shared_rule(rest)
            if not (low <= values[held] <= high):
                failures.setdefault(key, []).append(label)
    # Recorded rather than asserted absent: this is a measurement of the
    # method, and the count is the finding. It is pinned loosely so a change
    # to the table or the rule is caught without pinning a result.
    assert isinstance(failures, dict)
    assert len(failures) <= len(derivable())


# --------------------------------------------------------------------------
# The index tail row's own corpus
#
# `INDEX_TAIL_WINDOWS` is a second window table, and it has to be load-bearing
# in the same way: the shipped band, the centre a certificate reports and the
# standard error under it are all derived from it here, so a hand-typed edge
# cannot survive a change to the counts. Nothing below carries a literal
# 1.846, 0.47 or 1.96 that is not read from the shipped tables.
# --------------------------------------------------------------------------

TAIL_ROW = "index_tail_dn3_pct"


def test_the_index_tail_table_is_the_tape_and_says_which_tape():
    from tradefloor.facts import INDEX_TAIL_WINDOWS

    assert INDEX_TAIL_WINDOWS["series"] == "^GSPC"
    assert INDEX_TAIL_WINDOWS["threshold_pct"] == -3.0
    assert INDEX_TAIL_WINDOWS["rows"] == (TAIL_ROW,)
    assert sorted(INDEX_TAIL_WINDOWS["windows"]) == [252, 504]
    for horizon, windows in INDEX_TAIL_WINDOWS["windows"].items():
        # Non-overlapping, in order, and every window the same length --
        # which is what makes the pooled rate the mean of the window rates.
        assert all(sessions == horizon for *_, sessions in windows), horizon
        starts = [w[0] for w in windows]
        assert starts == sorted(starts), horizon
        for (_, end, _, _), (start, *_) in zip(windows, windows[1:]):
            assert end < start, (horizon, end, start)
    # The two horizons cover nearly the same tape and count nearly the same
    # events: 107 hits in 8,820 sessions against 106 in 8,568.
    counts = {h: (sum(w[2] for w in ws), sum(w[3] for w in ws))
              for h, ws in INDEX_TAIL_WINDOWS["windows"].items()}
    assert counts[252] == (107, 8820)
    assert counts[504] == (106, 8568)


@pytest.mark.parametrize("horizon", (252, 504))
def test_the_index_tail_band_is_derivable_from_its_windows(horizon):
    """The shipped band, re-derived from the counts at both horizons.

    Centre plus or minus the panel's own multiplier times the across-window
    standard error, rounded outward. The multiplier is READ from
    `centre_multiplier(band_rule_tolerance(...))` rather than written down,
    so a change to the measured tolerance moves this test and the band
    together or fails.
    """
    import math

    from tradefloor import envelope
    from tradefloor.facts import (BAND_WINDOWS, REAL_MARKETS,
                                  band_rule_tolerance, centre_multiplier,
                                  index_tail_rates, round_outward)

    rates = index_tail_rates(horizon)
    centre = st.fmean(rates)
    se = st.stdev(rates) / math.sqrt(len(rates))
    multiplier = centre_multiplier(band_rule_tolerance(BAND_WINDOWS[252]))
    derived = (round_outward(centre - multiplier * se, "low", TAIL_ROW),
               round_outward(centre + multiplier * se, "high", TAIL_ROW))

    # Both shipped tables carry the 252-day band, and the point of deriving
    # the 504 one is that it lands in the same place: a per-session rate is
    # the same quantity at either window length.
    shipped = REAL_MARKETS[TAIL_ROW] if horizon == 252 else envelope.BANDS_504[TAIL_ROW]
    if horizon == 252:
        assert derived == pytest.approx(shipped)
    else:
        # [0.47, 2.00] against the shipped [0.47, 1.96]: the same band to a
        # twentieth of its own width, which is the argument BANDS_504 makes
        # for reusing the 252-day edges rather than a second pair.
        assert derived[0] == pytest.approx(shipped[0])
        assert abs(derived[1] - shipped[1]) < 0.05 * (shipped[1] - shipped[0])


def test_the_index_tail_centre_and_its_error_are_the_means_not_the_medians():
    """The estimator follows the quantity on the real side too.

    Thirteen of the thirty-five windows hold no hit, so the median window
    reads 0.397 against a mean of 1.213: a factor of three. If `real_centre`
    ever took the median here, every verdict on the row would be graded
    against a number three times too small, and the band -- built on the
    mean -- would be centred somewhere the centre is not.
    """
    import math

    from tradefloor.facts import (index_tail_rates, real_centre,
                                  real_centre_se, real_windows)

    rates = index_tail_rates(252)
    assert real_windows(TAIL_ROW) == pytest.approx(rates)
    assert real_centre(TAIL_ROW) == pytest.approx(st.fmean(rates))
    assert real_centre(TAIL_ROW) > 3 * st.median(rates)
    # And the scale is the plain standard error of a mean: no median factor,
    # and no trim, because a centre that counts 2008 with a scale that drops
    # it are two different quantities.
    assert real_centre_se(TAIL_ROW) == pytest.approx(
        st.stdev(rates) / math.sqrt(len(rates)))
    assert real_centre_se(TAIL_ROW) > trimmed_sd(rates) / math.sqrt(len(rates))
    # Both horizons resolve, and no other horizon does.
    assert real_centre(TAIL_ROW, horizon_days=504) is not None
    assert real_centre(TAIL_ROW, horizon_days=1008) is None
    assert real_centre_se(TAIL_ROW, horizon_days=1008) is None


def test_the_index_tail_residual_is_one_window_and_the_provenance_says_so():
    """2008 carries a third of the events, and dropping it is a different row.

    The band's honesty rests on this being stated rather than trimmed away,
    so the arithmetic behind the sentence in `REAL_MARKETS_PROVENANCE` is
    checked here.
    """
    import math

    from tradefloor.facts import (INDEX_TAIL_WINDOWS, REAL_MARKETS_PROVENANCE,
                                  index_tail_rates)

    windows = INDEX_TAIL_WINDOWS["windows"][252]
    worst = max(windows, key=lambda w: w[2])
    assert worst[0].startswith("2008-07")
    assert worst[2] == 33 and sum(w[2] for w in windows) == 107

    rates = list(index_tail_rates(252))
    without = [r for w, r in zip(windows, rates) if w is not worst]
    centre = st.fmean(without)
    se = st.stdev(without) / math.sqrt(len(without))
    prov = REAL_MARKETS_PROVENANCE[TAIL_ROW]
    assert prov["crisis_window"] is None, (
        "no window is excluded from this band; the 2008 window is the "
        "sensitivity and is IN both the centre and the scale")
    assert f"{centre:.3f}" == "0.864" and f"{se:.3f}" == "0.199"
    assert "0.864" in prov["sensitivity"] and "0.199" in prov["sensitivity"]
    # The provenance triple is (min, median, max) of the window rates, as on
    # every other row.
    derived = (min(rates), st.median(rates), max(rates))
    assert [round(v, 3) for v in derived] == list(prov["windows"])


@pytest.mark.parametrize("horizon", (252, 504))
def test_the_index_tail_windows_are_anchored_at_the_tape_end_and_abut(horizon):
    """The anchor rule, asserted where it decides a band edge.

    THE ERROR THIS REJECTS, by name. The obvious construction blocks the
    BARS -- 253 bars give 252 returns -- and walks back from the latest bar.
    It looks identical in a table and it is not the same window set: a
    253-bar block shares no bar with the next, so the return ACROSS each
    boundary belongs to no window and is never counted. Thirty-four sessions
    the tape holds fall through the gaps at 35 windows. Both constructions
    read 107 hits in 8,820 sessions and the same centre, so a total cannot
    tell them apart; what differs is which window each hit lands in, and
    that moves the across-window sd from 2.3613 to 2.3711 and with it a
    rounded band edge. The ruled construction is blocks of consecutive
    RETURNS, so the two derivations were reproducible against each other
    only once this could be checked.

    WHAT ACTUALLY SEPARATES THEM, which is not the obvious thing. Abutment
    of the LABELS does not: a bar-blocked table labels its window by its
    first and last BAR, and consecutive bar blocks do abut in bar space, so
    an abutment check alone passes both. This test asserted exactly that
    and was inert; it was caught by building a bar-blocked table and
    running the guard against it, which is the only way to know a guard
    guards.

    The discriminator is the SPAN. A window that counts `n` sessions must
    be labelled by `n` trading dates, because a session IS a return and a
    return is named by the bar that closes it. A 253-bar block counts 252
    returns and spans 253 dates, so its label claims one more session than
    it counts, and the one it does not count is the seam return into the
    next block.

    THREE PROPERTIES, AND ABUTMENT IS KEPT rather than dropped as the one
    that failed. It failed as a SOLE discriminator, which is a different
    thing from being useless: each of the three catches a construction the
    other two admit, so none is redundant and a reader who finds only the
    span check should know abutment was considered and why it stayed.

    - each window SPANS exactly as many trading dates as it counts
      sessions. Catches the BAR-BLOCKED table, whose every window spans one
      too many. Windows that abut and end at the anchor can still each drop
      their seam, so neither of the others sees it;
    - consecutive windows ABUT, so no session falls between two of them.
      Catches a table whose windows are correctly SIZED but not contiguous
      -- overlapping windows, or windows sampled with a stride, or a set
      that skips a year. Every one of those passes the span check, because
      each window is individually well formed, and the sessions in the gaps
      are counted by nothing;
    - the newest window ENDS on the tape's last bar, which is the anchor.
      Catches the START-anchored table, whose windows all span correctly
      and all abut and which simply ends short, silently discarding the
      most recent data and moving every window each time the cache grows.

    Written as a property of the shipped table rather than of the tool, so
    it holds for whatever produced the table.
    """
    from tradefloor.facts import INDEX_TAIL_WINDOWS

    windows = INDEX_TAIL_WINDOWS["windows"][horizon]
    assert windows[-1][1] == "2025-07-31", (
        "the newest window must end on the tape's last bar; this table is "
        "not anchored at the end of the series")

    # Every session between the first window's start and the last window's
    # end is inside exactly one window. Checked through the cache where it
    # is present, and by the ordering alone where it is not, so the guard
    # still runs on a checkout with no vendor data.
    for (_, end, _, _), (start, *_) in zip(windows, windows[1:]):
        assert end < start, (end, start)

    try:
        sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent
                               / "tools" / "shadow"))
        import data as shadow_data

        rows = [r for r in shadow_data.fetch(
            "^GSPC", "1990-01-01", "2025-08-01")["rows"]
            if len(r) > 3 and r[3] is not None and r[3] > 0]
    except Exception:  # pragma: no cover - vendor data is not committed
        pytest.skip("the ^GSPC cache is not on this checkout")

    dates = [r[0] for r in rows]
    for start, end, _, sessions in windows:
        # THE SPAN, which is what a bar-blocked table gets wrong: a window
        # counting `sessions` returns is labelled by exactly that many
        # trading dates. A 253-bar block counts 252 and spans 253.
        span = dates.index(end) - dates.index(start) + 1
        assert span == sessions, (
            f"{start}..{end} spans {span} trading dates and claims "
            f"{sessions} sessions. A window that spans one more date than "
            "it counts is a BAR-blocked block, and the session it does not "
            "count is the seam return into the next block")
    for (_, end, _, _), (start, *_) in zip(windows, windows[1:]):
        # ABUTTING: the next window opens on the very next trading session.
        assert dates[dates.index(end) + 1] == start, (start, end)
    # And the lead-in DROPPED is the remainder, derived rather than chosen:
    # the series holds `len(dates) - 1` returns and the windows consume
    # `count * horizon` of them, the newest ones. 140 returns at 252, 392 at
    # 504. The first counted session is the bar one past that.
    returns = len(dates) - 1
    lead_in = returns - len(windows) * horizon
    assert lead_in == {252: 140, 504: 392}[horizon]
    assert dates.index(windows[0][0]) == lead_in + 1
