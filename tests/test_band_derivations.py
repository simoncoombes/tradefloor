"""Every shipped band edge, rebuilt from committed readings or named as a literal.

WHY THIS FILE EXISTS. On 2026-09-15 an agent adopting a new `fear_gauge_dn1`
band registered a prediction that one to five existing tests would assert the
old literal and fail. Zero did. The band lived in two places, and nothing in a
3,158-test suite read either one, so a typo in it would have shipped green.
`FEAR_DN1_WINDOWS` and four tests in `test_reference_windows.py` closed that
for one row of nineteen. This file is the audit of the rest.

What a derivation test has to do to be worth anything: rebuild the band from
readings and compare. A test that restates the constant passes whatever the
constant says. So every assertion below starts from a window table and ends at
a shipped edge, and where no readings exist the row is named in
`UNDERIVED` with what committing them would cost, rather than given a test
that reads the number back to itself.
"""

from __future__ import annotations

import statistics as st

import pytest

from tradefloor import envelope, facts
from tradefloor.facts import (BAND_BASIS, BAND_RULE_FIXED_MULTIPLIER,
                              BAND_RULE_FIXED_TOLERANCE, REAL_MARKETS,
                              REAL_MARKETS_504, REAL_MARKETS_UNIVERSAL,
                              REAL_MARKETS_UNIVERSAL_504,
                              REAL_MARKETS_UNIVERSAL_ADJUSTMENTS,
                              UNIVERSAL_WINDOWS, ValidationError,
                              band_from_windows, band_from_windows_fixed,
                              round_outward, trimmed_sd, universal_windows,
                              universal_window_is_crisis)

UNIVERSAL = {252: REAL_MARKETS_UNIVERSAL, 504: REAL_MARKETS_UNIVERSAL_504}
HORIZONS = (252, 504)


def multiplier(horizon: int) -> float:
    """The `fixed` rule's t at the window count this table actually has.

    Read from `BAND_RULE_FIXED_MULTIPLIER` on the DERIVED non-crisis count
    rather than from the band table's own basis entry, so a change to the
    crisis rule looks up a different multiplier and moves the band.
    """
    n = len(universal_windows("annualised_vol_pct", horizon))
    return BAND_RULE_FIXED_MULTIPLIER[n]


# --------------------------------------------------------------------------
# The universal panel: 32 names, four eras, fifty-six edges
# --------------------------------------------------------------------------


def test_the_universal_table_is_rectangular_and_names_its_tape():
    first, last = UNIVERSAL_WINDOWS["tape"]
    for horizon in HORIZONS:
        labels = UNIVERSAL_WINDOWS["windows"][horizon]
        assert len(labels) == {252: 38, 504: 19}[horizon]
        for key, values in UNIVERSAL_WINDOWS["values"][horizon].items():
            assert len(values) == len(labels), (horizon, key)
        assert sorted(UNIVERSAL_WINDOWS["values"][horizon]) == sorted(
            UNIVERSAL[horizon])
        assert labels[0].split("..")[0] > first
        assert labels[-1].split("..")[1] == last


@pytest.mark.parametrize("horizon", HORIZONS)
def test_the_universal_windows_are_end_anchored_and_abut(horizon):
    """The anchor rule as a property of the shipped table.

    The newest window ends on the tape's last bar and consecutive windows do
    not overlap, which is `INDEX_TAIL_WINDOWS`'s construction. A table that
    ended short would discard the most recent data and move every window each
    time the pull was refreshed, and `FEAR_DN1_WINDOWS` is the one table here
    that does that, deliberately and with its own test saying so.
    """
    labels = UNIVERSAL_WINDOWS["windows"][horizon]
    assert labels[-1].split("..")[1] == UNIVERSAL_WINDOWS["tape"][1]
    for older, newer in zip(labels, labels[1:]):
        assert older.split("..")[1] < newer.split("..")[0], (older, newer)


@pytest.mark.parametrize("horizon", HORIZONS)
def test_the_crisis_drops_are_the_named_sessions_and_leave_the_recorded_count(
        horizon):
    """Three drops a horizon, each holding a session the table names.

    The drop count is the difference between the band the library ships and
    one built on three more windows, and `BAND_BASIS` publishes the count it
    was built at. Here the count is derived from the dates and checked
    against what `BAND_BASIS` claims, so the two cannot drift.
    """
    labels = UNIVERSAL_WINDOWS["windows"][horizon]
    dropped = [lab for lab in labels if universal_window_is_crisis(lab)]
    assert len(dropped) == 3, dropped
    for label in dropped:
        start, end = label.split("..")
        assert any(start <= date <= end
                   for date in UNIVERSAL_WINDOWS["crisis_dates"]), label
    kept = len(labels) - len(dropped)
    assert kept == len(universal_windows("leverage_effect", horizon))
    basis = BAND_BASIS[UNIVERSAL_WINDOWS["tables"][horizon]]
    assert basis["n_windows"] == kept
    assert basis["rows"] == len(UNIVERSAL[horizon])


@pytest.mark.parametrize("horizon", HORIZONS)
@pytest.mark.parametrize("key", sorted(REAL_MARKETS_UNIVERSAL))
def test_every_universal_band_is_the_fixed_rule_plus_its_named_adjustment(
        key, horizon):
    """The fifty-six edges, rebuilt from the readings.

    THIS IS THE TEST THE UNIVERSAL TABLES DID NOT HAVE. Before
    `UNIVERSAL_WINDOWS` landed, both tables were typed numbers whose readings
    lived in the design repository, and nothing in this suite read either
    one. Every edge below is `band_from_windows_fixed` on the non-crisis
    windows at the multiplier for that count, plus whichever of the two
    inward clamps `REAL_MARKETS_UNIVERSAL_ADJUSTMENTS` names for the row.

    REFUSES: an edge nobody derived, which is what both tables were made of.
    """
    values = universal_windows(key, horizon)
    rule = dict(zip(("low", "high"),
                    band_from_windows_fixed(key, values, multiplier(horizon))))
    band = dict(rule)
    moves = REAL_MARKETS_UNIVERSAL_ADJUSTMENTS[horizon].get(key, {})
    for edge, (shipped, kind, reason) in moves.items():
        assert reason
        assert shipped != rule[edge], (key, edge, "a move that moves nothing")
        outward = shipped < rule[edge] if edge == "low" else shipped > rule[edge]
        assert kind == ("outward" if outward else "inward"), (key, edge, kind)
        band[edge] = shipped
    low, high = UNIVERSAL[horizon][key]
    assert (round(band["low"], 10), round(band["high"], 10)) == (
        round(low, 10), round(high, 10)), (key, horizon, rule, moves)


def test_the_adjustment_table_names_only_rows_with_readings():
    for horizon, rows in REAL_MARKETS_UNIVERSAL_ADJUSTMENTS.items():
        for key, edges in rows.items():
            assert key in UNIVERSAL_WINDOWS["values"][horizon], (horizon, key)
            for edge in edges:
                assert edge in ("low", "high"), (horizon, key, edge)
    assert REAL_MARKETS_UNIVERSAL_ADJUSTMENTS[504] == {}, (
        "the 504 universal band carries no move, the same as "
        "REAL_MARKETS_ADJUSTMENTS_504, and both clamps are measured as "
        "non-binding there")


def test_the_two_clamps_cost_what_the_table_says_they_cost():
    """Clamp #2 rejects seven real years here against one on the decade band.

    The cost is the argument against the clamp, so it is measured from the
    readings rather than quoted. At 504 bars neither clamp binds: no window
    reads a positive leverage effect and none reads clustering under 0.02,
    which is why that horizon's adjustment table is empty.
    """
    leverage = universal_windows("leverage_effect", 252)
    clustering = universal_windows("abs_return_acf1", 252)
    assert sum(1 for v in leverage if v > 0) == 7
    assert max(leverage) == pytest.approx(0.023361, abs=5e-7)
    assert sum(1 for v in clustering if v < 0.02) == 1
    assert min(clustering) == pytest.approx(0.003106, abs=5e-7)

    assert max(universal_windows("leverage_effect", 504)) < 0.0
    assert min(universal_windows("abs_return_acf1", 504)) > 0.02


def test_the_campbell_ceiling_is_absent_because_it_would_round_inward():
    """The retired literature move, asserted as arithmetic rather than prose.

    `REAL_MARKETS_ADJUSTMENTS` moves the decade band's volatility ceiling out
    to Campbell's 36.0. REALISM-BANDS.md allows a literature move OUTWARD
    only, and the universal rule's own ceiling is past 36.0, so applying it
    would tighten the band. `BAND_BASIS` states this in a sentence; this is
    the sentence as arithmetic.
    """
    campbell = facts.REAL_MARKETS_ADJUSTMENTS["annualised_vol_pct"]["high"][0]
    ceiling = band_from_windows_fixed(
        "annualised_vol_pct", universal_windows("annualised_vol_pct", 252),
        multiplier(252))[1]
    assert ceiling > campbell == 36.0
    assert REAL_MARKETS_UNIVERSAL["annualised_vol_pct"][1] == ceiling


def test_one_universal_floor_turns_on_the_fifth_decimal_place():
    """Why the readings carry six decimal places and not four.

    `volume_abs_return_corr`'s 252-bar floor comes out of the rule at
    0.35003030, three parts in a hundred thousand above 0.35, and
    `round_outward` floors it to 0.35. Round the same readings to four places
    and the floor becomes 0.34. This pins the precision claim in the table's
    own comment, so a later reformatting that shortens the numbers fails here
    rather than shipping a different band.
    """
    key = "volume_abs_return_corr"
    values = universal_windows(key, 252)
    t = multiplier(252)
    raw = st.median(values) - t * trimmed_sd(values)
    assert 0.35 < raw < 0.3501
    assert band_from_windows_fixed(key, values, t)[0] == pytest.approx(0.35)

    coarse = [round(v, 4) for v in values]
    assert band_from_windows_fixed(key, coarse, t)[0] == pytest.approx(0.34)


def test_the_504_universal_windows_end_with_the_forty_name_panels_six():
    """Two panels, one anchor: the overlap is label for label.

    `REAL_MARKETS_WINDOWS_504` is the certified forty over six 504-bar
    windows and this table is 32 of them over nineteen. If both walk backward
    from the same last bar at the same length, the newest six labels are the
    same six, and they are. A table cut on a different anchor or a different
    length would not overlap like this, so this is the cheap check that the
    two window sets are comparable at all.
    """
    forty = facts.REAL_MARKETS_WINDOWS_504["windows"]
    assert UNIVERSAL_WINDOWS["windows"][504][-len(forty):] == tuple(forty)
    covid = forty[facts.REAL_MARKETS_WINDOWS_504["crisis_index"]]
    assert universal_window_is_crisis(covid), covid


def test_a_horizon_or_a_row_with_no_universal_readings_is_refused_by_name():
    with pytest.raises(ValidationError) as unknown_horizon:
        universal_windows("annualised_vol_pct", 756)
    assert "756" in str(unknown_horizon.value)

    with pytest.raises(ValidationError) as unknown_row:
        universal_windows("index_drift_pct", 252)
    assert "index_drift_pct" in str(unknown_row.value)


def test_the_fixed_rule_is_not_the_spread_rule():
    """`band_from_windows_fixed` is a second rule and not a rename of the first.

    `BAND_RULES` names both. On the universal panel's own readings the two
    disagree on the volatility row by more than 8 points at the ceiling,
    which is the reason a band has to carry the rule it was built with and
    not only its window count.
    """
    values = universal_windows("annualised_vol_pct", 252)
    spread = band_from_windows("annualised_vol_pct", values)
    fixed = band_from_windows_fixed("annualised_vol_pct", values,
                                    multiplier(252))
    assert fixed != spread
    assert spread[1] - fixed[1] > 8.0
    assert BAND_BASIS[UNIVERSAL_WINDOWS["tables"][252]]["rule"] == "fixed"


# --------------------------------------------------------------------------
# The multiplier the fixed rule's width is made of
# --------------------------------------------------------------------------


def test_the_basis_multiplier_is_the_table_entry_for_its_own_window_count():
    """One multiplier, not three spellings of one.

    `BAND_RULE_FIXED_MULTIPLIER` holds t per window count,
    `BAND_BASIS[table]["multiplier"]` repeats it, and the band is built from
    it. This binds the second to the first at the count the table's own
    readings produce.
    """
    for horizon in HORIZONS:
        basis = BAND_BASIS[UNIVERSAL_WINDOWS["tables"][horizon]]
        assert basis["multiplier"] == pytest.approx(multiplier(horizon))
        assert basis["tolerance"] == BAND_RULE_FIXED_TOLERANCE


@pytest.mark.parametrize("n_windows", (16, 35))
def test_the_fixed_multiplier_holds_the_tolerance_it_was_solved_for(n_windows):
    """The two multipliers that set every universal edge, re-measured.

    `BAND_RULE_TOLERANCE` is held to a live re-derivation by
    `test_mechanism_gate.py`; its `fixed` twin was read by no test at all
    until this one, while carrying the width of all fifty-six universal band
    edges. `band_rule_fixed_false_alarm` is the re-derivation the module
    documents and nothing called.

    WHAT THIS RESOLVES, stated because 20,000 draws is not 200,000. The
    binomial error here is about 0.0017, so the check catches a multiplier
    taken from the wrong row of the table (n=9's 2.982334 at n=35 reads
    0.00995, seventy-eight standard errors low) and a five per cent error. It
    does NOT resolve a two per cent one. The full solve is the function's own
    200,000-draw default and it takes about a minute a row.
    """
    rate, se = facts.band_rule_fixed_false_alarm(
        n_windows, BAND_RULE_FIXED_MULTIPLIER[n_windows], draws=20_000)
    assert abs(rate - BAND_RULE_FIXED_TOLERANCE) < 5 * se, (
        n_windows, rate, se, BAND_RULE_FIXED_TOLERANCE)

    wrong = facts.band_rule_fixed_false_alarm(
        n_windows, BAND_RULE_FIXED_MULTIPLIER[9], draws=2_000)[0]
    assert abs(wrong - BAND_RULE_FIXED_TOLERANCE) > 0.02, (
        "the check must be able to tell one row of the multiplier table from "
        "another, or it is not checking the multiplier")


# --------------------------------------------------------------------------
# `envelope.BANDS_504`, the table a 504-day panel is actually graded against
# --------------------------------------------------------------------------


def test_the_504_shape_rows_are_one_table_and_not_two_transcriptions():
    """`BANDS_504`'s fourteen shape rows against `facts.REAL_MARKETS_504`.

    THE HOLE THIS CLOSES. `REAL_MARKETS_504` is re-derived from
    `REAL_MARKETS_WINDOWS_504` by `test_scoring_rule.py`. `BANDS_504` is a
    separate hand-typed copy of the same fourteen bands, and it is the one
    `envelope.score` grades a 504-day panel with, so a typo there would have
    moved a published verdict while the derivation test went on passing
    against the other table. Nothing bound the two: `test_envelope.py`
    compared their KEY SETS and not their values.

    Compared to ten decimal places rather than exactly, because the two were
    typed independently and two rows differ in the last bit of a float
    (`excess_kurtosis` 7.1000000000000005 against 7.1). That is the
    signature of two transcriptions rather than one, which is the thing this
    test exists to stop mattering.
    """
    for key, band in REAL_MARKETS_504.items():
        shipped = envelope.BANDS_504[key]
        assert (round(shipped[0], 10), round(shipped[1], 10)) == (
            round(band[0], 10), round(band[1], 10)), key

    extra = set(envelope.BANDS_504) - set(REAL_MARKETS_504)
    assert extra == {"index_drift_pct", "fear_gauge_dn1", "fear_gauge_dn3",
                     "index_tail_dn3_pct"}


def test_the_504_rows_carried_from_252_are_carried_and_not_retyped():
    """Three of the four extra rows carry their 252-day band, and it is the same one.

    `BANDS_504` argues each carry in its own comment: the level band's width
    is the centre's uncertainty, the tail row is a per-session rate, and the
    -3 per cent fear row's windows are conditioned on a session count that
    re-cutting would change. `fear_gauge_dn1` is the one that does NOT carry,
    because its windows exist at both lengths, and it is excluded here by
    name for that reason rather than by tolerance.
    """
    for key in ("index_drift_pct", "fear_gauge_dn3", "index_tail_dn3_pct"):
        assert envelope.BANDS_504[key] == pytest.approx(REAL_MARKETS[key]), key
    assert envelope.BANDS_504["fear_gauge_dn1"] != pytest.approx(
        REAL_MARKETS["fear_gauge_dn1"])


def test_the_basis_row_count_is_the_table_and_not_a_sentence():
    """`BAND_BASIS["envelope.BANDS_504"]` counts its own rows.

    Its `rows` field is `len(BANDS_504)` and so it cannot go stale, but the
    note beside it says "seventeen" and the table has had eighteen rows since
    `index_tail_dn3_pct` landed on 2026-09-06. The count that a reader acts
    on is the field; this pins the field to the table so the two can never
    disagree, and the stale word in the note is recorded here rather than in
    a comment nothing reads.
    """
    basis = BAND_BASIS["envelope.BANDS_504"]
    assert basis["rows"] == len(envelope.BANDS_504) == len(REAL_MARKETS)
    assert basis["rows"] >= 18, (
        "the note beside this field says seventeen and the table has had "
        "eighteen rows since index_tail_dn3_pct landed on 2026-09-06")


# --------------------------------------------------------------------------
# `abs_return_acf5`, whose readings are in its provenance and not in the table
# --------------------------------------------------------------------------

ACF5 = "abs_return_acf5"


def test_the_acf5_band_is_derivable_after_all_from_its_own_eight_windows():
    """The one row `test_reference_windows.py` skips, derived from elsewhere.

    `REAL_MARKETS_WINDOWS["not_derivable"]` is right that this row's band
    does not come from that table: its provenance summarises "a different
    window set, an eight-value list rather than these nine". What nobody
    noticed is that the eight-value list IS the readings. Every other row's
    `windows` entry is a (min, median, max) triple; this row's is the eight
    non-crisis windows themselves, 0.011 to 0.099, and the band is the spread
    rule on them with the floor clamped above zero exactly as the row's
    `supersedes` text says.

    So the row was never an underivable literal. It was a derivable one with
    no test, which is the same failure mode wearing a label.
    """
    windows = facts.REAL_MARKETS_PROVENANCE[ACF5]["windows"]
    assert len(windows) == 8
    assert windows == tuple(sorted(windows))

    rule = band_from_windows(ACF5, list(windows))
    shipped = REAL_MARKETS[ACF5]
    assert rule[1] == pytest.approx(shipped[1]), (
        "the ceiling is the rule on the eight windows, unadjusted")
    assert rule[0] == pytest.approx(-0.01)
    assert shipped[0] == 0.01, (
        "the floor is clamped above zero: a negative floor re-opens the hole "
        "the row exists to close, admitting lag-1 clustering with no lag-5 "
        "memory behind it")
    assert rule[0] < 0.0 < shipped[0] <= min(windows), (
        "the clamp must sit below every observed window and above zero")


def test_the_acf5_readings_and_the_panel_table_are_different_measurements():
    """Recorded because the row is graded on one set and diagnosed on another.

    `REAL_MARKETS_WINDOWS["values"]["abs_return_acf5"]` holds ten readings
    from the reference panel and the band comes from the eight in the
    provenance. The two sets do not overlap in range: the nine non-crisis
    panel readings run 0.034 to 0.073 and the eight run 0.011 to 0.099, so
    the panel's MEDIAN sits above the band's own ceiling-side windows. The
    module comment says the row's centre and dispersion come from the panel
    table and its band does not, and this is that split measured.

    OPEN, and named as open: one row graded against one measurement and
    diagnosed against another is a ruling nobody has made, not a bug this
    file can fix.
    """
    panel = [v for i, v in enumerate(
        facts.REAL_MARKETS_WINDOWS["values"][ACF5])
        if i != facts.REAL_MARKETS_WINDOWS["crisis_index"]]
    band_windows = list(facts.REAL_MARKETS_PROVENANCE[ACF5]["windows"])
    assert st.median(panel) > st.median(band_windows) * 2
    assert ACF5 in facts.REAL_MARKETS_WINDOWS["not_derivable"]


# --------------------------------------------------------------------------
# The rows no test in this suite can rebuild, and what each would cost
# --------------------------------------------------------------------------

#: Band constants rebuilt from committed SUMMARY statistics and not from
#: readings, with what the readings would cost.
#:
#: The distinction this dict exists to keep: a test that starts from
#: `DRIFT_LEGS` catches a mistyped BAND and cannot catch a mistyped LEG,
#: because the per-year figures the legs summarise are in no committed file.
#: That is weaker than what `UNIVERSAL_WINDOWS` or `INDEX_TAIL_WINDOWS` give
#: and stronger than a literal, and calling it either of the other two would
#: be the mislabelling this whole file is about.
SUMMARY_DERIVED: dict[str, str] = {
    "facts.REAL_MARKETS['index_drift_pct']":
        "the 75 calendar-year ^GSPC price returns and the 22 RSP premia. "
        "Committing them is 98 + 22 + 19 annual figures and one re-run of "
        "tools/calibration/index_band.py, which needs a ^GSPC cache back to "
        "1927 plus the RSP and ^SPXEW pulls; this checkout holds ^GSPC from "
        "1990 alone",
    "facts.RULED_DRIFT_BAND":
        "as REAL_MARKETS['index_drift_pct'], on the 98-year cap-weighted leg "
        "rather than the 75-year one. Same readings, same cost, same one "
        "re-run",
}

#: Band constants with no derivation at all, and why.
#:
#: A row here is a DECISION and not an oversight: the readings behind it are
#: not in this package, and inventing a derivation would produce a test that
#: reads the number back to itself.
UNDERIVED: dict[str, str] = {
    "facts.REAL_MARKETS['fear_gauge_dn3']":
        "the -3 per cent fear row's window medians. Its own provenance says "
        "they do not exist in this package. HELD: this row is being "
        "re-derived elsewhere as this file lands and is deliberately "
        "untouched here, so its cost is that derivation's to state",
}


def test_the_two_registries_are_the_whole_of_what_is_not_rebuilt():
    """The registries have to be complete or they are decoration.

    Every band constant this library ships is either rebuilt from readings by
    a test (in this file, in `test_reference_windows.py` or in
    `test_scoring_rule.py`), or named in `SUMMARY_DERIVED`, or named in
    `UNDERIVED`. This holds the two lists to constants that exist, so
    renaming or deleting one without its entry fails here.
    """
    for registry in (SUMMARY_DERIVED, UNDERIVED):
        for name in registry:
            assert registry[name]
            if "[" in name:
                table, _, row = name.partition("['")
                assert row.rstrip("']") in getattr(
                    facts, table.split(".", 1)[1]), name
            else:
                assert getattr(facts, name.split(".", 1)[1]) is not None, name
    assert set(SUMMARY_DERIVED) == {
        "facts.REAL_MARKETS['index_drift_pct']", "facts.RULED_DRIFT_BAND"}
    assert set(UNDERIVED) == {"facts.REAL_MARKETS['fear_gauge_dn3']"}
    assert not set(SUMMARY_DERIVED) & set(UNDERIVED)


def test_the_ruled_table_is_the_universal_one_plus_the_five_off_panel_rows():
    """What the RELEASE BAR's own band table is made of, counted.

    `envelope.BAR_BAND_BASIS` is "ruled", so `REAL_MARKETS_RULED` is the
    table the bar is read on. Fourteen of its eighteen rows at 252 are the
    universal bands this file rebuilds from `UNIVERSAL_WINDOWS`; the tail row
    comes from `RULED_TAIL_WINDOWS` and the drift row from `DRIFT_LEGS`, one
    of them readings and one of them a summary; and since 2026-09-19 the -1
    per cent fear row comes from `FEAR_DN1_WINDOWS` through the row's own
    provenance block, which `test_reference_windows.py` re-derives, and the
    -3 per cent row keeps the shipped whole-record ruler `UNDERIVED` above
    already names as the one band this file cannot rebuild.
    """
    assert envelope.BAR_BAND_BASIS == "ruled"
    ruled = facts.REAL_MARKETS_RULED
    from_universal = [k for k in ruled if k in REAL_MARKETS_UNIVERSAL]
    rest = sorted(set(ruled) - set(from_universal))
    assert len(from_universal) == 14
    # `crisis_sector_dispersion` joined on 2026-09-22. Its ruler is ^VIX
    # against the same 32 names the universal panel is measured on, so it
    # is composed in here rather than carried there, exactly as the two
    # fear rows are.
    assert rest == ["crisis_sector_dispersion", "fear_gauge_dn1",
                    "fear_gauge_dn3", "index_drift_pct",
                    "index_tail_dn3_pct"]
    assert ruled["fear_gauge_dn3"] == facts.RULED_FEAR_DN3_BAND == (2.60, 9.58)
    assert ruled["index_drift_pct"] == facts.RULED_DRIFT_BAND
    assert ruled["index_tail_dn3_pct"] == facts.RULED_TAIL_BAND
    assert ruled["fear_gauge_dn1"] == facts.RULED_FEAR_DN1_BAND[252]
    assert ruled["fear_gauge_dn1"] == facts.REAL_MARKETS["fear_gauge_dn1"]


# --------------------------------------------------------------------------
# `RULED_TAIL_BAND`: the 1928 tail row, 97 windows and a block bootstrap
# --------------------------------------------------------------------------

TAIL = "index_tail_dn3_pct"


@pytest.mark.parametrize("horizon,count,hits", ((252, 97, 364), (504, 48, 360)))
def test_the_whole_record_tail_windows_join_the_shipped_ones(
        horizon, count, hits):
    """62 old windows plus the shipped 35, with no window written down twice.

    `RULED_TAIL_WINDOWS` holds only the pre-1990 part and
    `INDEX_TAIL_WINDOWS` holds the rest, so the two bands that stand on this
    series share a spelling of every window they have in common. The join has
    to abut: a gap or an overlap at the seam would change the count and the
    centre with it.
    """
    windows = facts.ruled_tail_windows(horizon)
    assert len(windows) == count
    assert sum(w[2] for w in windows) == hits
    assert sum(w[3] for w in windows) == count * horizon
    assert all(w[3] == horizon for w in windows)

    older = facts.RULED_TAIL_WINDOWS["windows"][horizon]
    newer = facts.INDEX_TAIL_WINDOWS["windows"][horizon]
    assert windows == tuple(older) + tuple(newer)
    assert older[-1][1] < newer[0][0], (older[-1], newer[0])
    for a, b in zip(windows, windows[1:]):
        assert a[1] < b[0], (a, b)
    assert windows[-1][1] == "2025-07-31"


@pytest.mark.parametrize("horizon", HORIZONS)
def test_the_ruled_tail_band_is_derivable_from_its_windows(horizon):
    """Both edges of the bar's tail band, rebuilt from the readings.

    Centre and multiplier are DERIVED: the centre is the mean of the window
    rates and the multiplier is `centre_multiplier(band_rule_tolerance(9))`,
    the same term the shipped 1990 band uses. Only the block-3 bootstrap
    error is read from the table, and the test below re-runs that too.

    REFUSES: a typed edge on `RULED_TAIL_BAND`, which is what it was until
    `RULED_TAIL_WINDOWS` landed, on a band that grades the release bar.
    """
    rates = facts.ruled_tail_rates(horizon)
    centre = st.fmean(rates)
    t = facts.centre_multiplier(facts.band_rule_tolerance(9))
    se = facts.RULED_TAIL_WINDOWS["block_bootstrap_se"][horizon]
    derived = (round_outward(centre - t * se, "low", TAIL),
               round_outward(centre + t * se, "high", TAIL))
    shipped = (facts.RULED_TAIL_BAND if horizon == 252
               else facts.RULED_TAIL_BAND_504_CHECK)
    assert derived == pytest.approx(shipped), (horizon, centre, se)


def test_the_block_bootstrap_error_is_the_one_the_table_records():
    """The one input the band derivation reads instead of deriving, re-run.

    200,000 draws at seed 20260905 on each horizon, which is the draw count
    and the seed the table records, so this reproduces the figures exactly
    rather than agreeing with them to a tolerance. About ten seconds a
    horizon, and it is the difference between the band standing on 97
    readings and standing on 96 readings plus a typed error bar.
    """
    table = facts.RULED_TAIL_WINDOWS
    for horizon, recorded in table["block_bootstrap_se"].items():
        se = facts.moving_block_bootstrap_se(
            facts.ruled_tail_rates(horizon), table["block_length"],
            draws=table["bootstrap_draws"], seed=table["bootstrap_seed"])
        assert se == pytest.approx(recorded, abs=5e-10), horizon


def test_the_clustering_correction_is_the_one_this_span_needs():
    """Why the 1928 band uses a bootstrap error where the 1990 band uses sd/sqrt(n).

    `REAL_MARKETS_PROVENANCE` licenses the iid form on two diagnostics: the
    lag-1 autocorrelation of the window counts, and the bootstrap agreeing
    with the iid error. On the 1990 span those read +0.06 and 3 per cent. On
    this span they read +0.60 and 43 per cent, so `sd/sqrt(97)` is about 40
    per cent too small at both edges and the iid band would be [0.89, 2.09],
    a band 30 per cent narrower than the one that ships.
    """
    rates = facts.ruled_tail_rates(252)
    iid = st.stdev(rates) / len(rates) ** 0.5
    block = facts.RULED_TAIL_WINDOWS["block_bootstrap_se"][252]
    assert block / iid == pytest.approx(1.428, abs=0.001)

    mean = st.fmean(rates)
    var = sum((x - mean) ** 2 for x in rates)
    lag1 = sum((rates[i] - mean) * (rates[i - 1] - mean)
               for i in range(1, len(rates))) / var
    assert lag1 == pytest.approx(0.5952, abs=5e-5)

    t = facts.centre_multiplier(facts.band_rule_tolerance(9))
    iid_band = (round_outward(mean - t * iid, "low", TAIL),
                round_outward(mean + t * iid, "high", TAIL))
    assert iid_band == pytest.approx((0.89, 2.09))
    shipped = facts.RULED_TAIL_BAND
    assert shipped[1] - shipped[0] > 1.3 * (iid_band[1] - iid_band[0])


def test_the_tail_row_reads_a_factor_of_twenty_five_between_its_eras():
    """The cost the band's own comment states, measured from the readings.

    A band this wide discriminates less and will not separate pt-v19 from
    pt-v18. The reason is in the windows: the -3 per cent session rate runs
    6.38 per cent over 1928-40 and 0.25 over 1960-89.
    """
    windows = facts.ruled_tail_windows(252)
    eras = {}
    for start, _, hits, sessions in windows:
        for label, lo, hi in (("1928-40", "1928", "1940"),
                              ("1941-59", "1941", "1959"),
                              ("1960-89", "1960", "1989"),
                              ("1990-25", "1990", "2025")):
            if lo <= start[:4] <= hi:
                got = eras.setdefault(label, [0, 0])
                got[0] += hits
                got[1] += sessions
    rates = {k: 100.0 * h / s for k, (h, s) in eras.items()}
    assert rates["1928-40"] == pytest.approx(6.38, abs=0.005)
    assert rates["1941-59"] == pytest.approx(0.61, abs=0.005)
    assert rates["1960-89"] == pytest.approx(0.25, abs=0.005)
    assert rates["1990-25"] == pytest.approx(1.21, abs=0.005)
    assert rates["1928-40"] / rates["1960-89"] > 25.0


def test_the_shipped_1990_tail_band_still_derives_from_the_joined_table():
    """Promoting the older windows must not have moved the newer ones.

    `test_reference_windows.py` derives `REAL_MARKETS[index_tail_dn3_pct]`
    from `INDEX_TAIL_WINDOWS` alone. This asserts the same table is the tail
    of the joined one, so the 1990 band and the 1928 band are two cuts of one
    series rather than two series.
    """
    joined = facts.ruled_tail_windows(252)
    assert joined[-35:] == tuple(facts.INDEX_TAIL_WINDOWS["windows"][252])
    assert sum(w[2] for w in joined[-35:]) == 107
    assert st.fmean(facts.index_tail_rates(252)) == pytest.approx(
        1.2131519, abs=5e-8)


# --------------------------------------------------------------------------
# `index_drift_pct`: derived from summaries, and the summaries are the floor
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name,legs", sorted(
    facts.DRIFT_LEGS["bands"].items()))
def test_both_drift_bands_are_the_rule_on_their_legs(name, legs):
    """Four edges rebuilt from `DRIFT_LEGS`, on the two spans.

    WHAT THIS CATCHES: a typo in `REAL_MARKETS["index_drift_pct"]` or in
    `RULED_DRIFT_BAND`, which were typed pairs with no input in this package
    until the legs landed.

    WHAT IT CATCHES ONLY PARTLY, and the reason the row is in
    `SUMMARY_DERIVED` rather than treated as closed: a typo in a LEG is
    caught only when it moves a rounded edge, which takes about 0.05 points a
    year on a mean. The 75 and 98 annual returns the legs summarise are
    committed nowhere, so the chain bottoms out at a mean and an sd rather
    than at readings, and a small error in either is invisible here.
    """
    cap_weighted, premium = legs
    derived = facts.drift_leg_band(cap_weighted, premium)
    shipped = (REAL_MARKETS["index_drift_pct"] if "REAL_MARKETS" in name
               else facts.RULED_DRIFT_BAND)
    assert derived == pytest.approx(shipped), (name, legs)


def test_each_drift_leg_carries_no_standard_error_of_its_own():
    """`se` is `sd / sqrt(n)` and is not stored, so it cannot disagree.

    The design repository's drift-band.json carries `se` beside `sd` and `n`
    on every leg and the three agree to ten places. Storing all three here
    would be a third number saying what two already say, and a band edge
    moves if the stored one drifts.
    """
    import math
    for name, leg in facts.DRIFT_LEGS.items():
        if not isinstance(leg, dict) or "mean" not in leg:
            continue
        assert "se" not in leg, name
        assert leg["n"] == leg["last_year"] - leg["first_year"] + 1, name
        assert leg["sd"] > 0.0 and math.isfinite(leg["mean"]), name


def test_the_resolution_term_decides_neither_drift_edge():
    """The width is two centre standard errors at both spans, not the floor.

    `DRIFT_LEGS["resolution"]` is 2.373 and both spans give a centre standard
    error over 2.24, so twice that is 4.50 and 4.60 and the floor never
    binds. It matters because the resolution's `model_sd` of 6.5 is the
    2026-09-03 reading and `SEED_SD["index_drift_pct"]` has since been
    measured at 9.55716 on the varying roster. Re-deriving with the newer
    figure moves the resolution to 3.49 and still changes no edge, which is
    why the band was not re-derived when the seed sd moved.
    """
    import math
    resolution = facts.DRIFT_LEGS["resolution"]
    assert resolution["half_width"] == pytest.approx(
        2.0 * resolution["model_sd"] / math.sqrt(resolution["seeds"]))
    for cap_weighted, premium in facts.DRIFT_LEGS["bands"].values():
        cw, prem = facts.DRIFT_LEGS[cap_weighted], facts.DRIFT_LEGS[premium]
        centre_se = math.hypot(cw["sd"] / math.sqrt(cw["n"]),
                               prem["sd"] / math.sqrt(prem["n"]))
        assert 2.0 * centre_se > resolution["half_width"]
        newer = 2.0 * facts.SEED_SD["index_drift_pct"] / math.sqrt(30)
        assert 2.0 * centre_se > newer, (cap_weighted, newer)


def test_the_premium_leg_is_shorter_than_the_span_it_is_applied_to():
    """The mismatch the row makes, asserted so it stays visible.

    22 calendar years of equal-weight premium applied to a 75-year or 98-year
    cap-weighted mean. No longer equal-weight record exists, so this is named
    and not closed. The cross-check leg reads the premium 0.47 points a year
    more negative over its 19 years, which is inside its own standard error
    and is why the row uses RSP.
    """
    import math
    rsp, spxew = facts.DRIFT_LEGS["rsp"], facts.DRIFT_LEGS["spxew"]
    for cap_weighted, _ in facts.DRIFT_LEGS["bands"].values():
        assert facts.DRIFT_LEGS[cap_weighted]["n"] > 3 * rsp["n"]
    gap = rsp["mean"] - spxew["mean"]
    assert gap == pytest.approx(0.47, abs=0.005)
    assert gap < spxew["sd"] / math.sqrt(spxew["n"])
