"""A band table that is registered and read by nothing has to fail a test.

On 2026-09-14 `ruling-the-ruler-is-the-universal-band` made the release bar
every row in band against the universal whole-tape band. The band existed.
`facts.REAL_MARKETS_UNIVERSAL` and `_504` were registered in
`facts._KNOWN_TABLES`, they were absent from both `RULERS_BY_HORIZON` tables,
and no library lookup returned them, so `envelope.score`, `envelope.certify`,
`loss.scoring_rule` and `facts.report` could not produce a universal-basis
verdict at all. The bar was ruled against one object and computed against
another, and nothing in `tests/` disagreed.

That silence is what this file removes. `envelope.RULERS_BY_BASIS` and the
composed `facts.REAL_MARKETS_RULED` tables repaired the routing; until this
file landed, not one test in `tests/` referred to `RULERS_BY_BASIS`,
`REAL_MARKETS_RULED`, `RULED_UNREADABLE` or `BAND_EDGE_LIVENESS`, so the
repair was held in place by a guard in the design repository and by nothing
here.

Every test below constructs the failure it is meant to catch. Registration is
checked BAND FOR BAND rather than key for key, because at `b66e691`
`facts.REAL_MARKETS` already held every shape row's KEY under a different
decade band, and counting that as reach would call the defect its own repair.

WHAT THIS FILE DOES NOT DO. It asserts no band EDGE. Every number it reads
comes out of the tables themselves, so a band that is re-derived moves here
without an edit and a band that is edited under an unchanged name does not
pass as unchanged. The one count it does assert is 31 of 38 cells, which is
the claim under review and which changes only when a ruling lands.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "tools" / "calibration"))

import tradefloor.envelope as envelope  # noqa: E402
import tradefloor.facts as facts  # noqa: E402
from tradefloor import ValidationError  # noqa: E402

#: The nineteen rows the ruling names, in the partition the library grades.
GRADED = (tuple(facts.SHAPE) + tuple(facts.LEVEL) + tuple(facts.CRISIS)
          + tuple(facts.PERSISTENCE))

#: The two horizons the bar reads. 19 rows by 2 horizons is the 38 cells.
HORIZONS = (facts.CERTIFIED_HORIZON_DAYS, 504)

#: The three cells with no ruled band, each with the entry that holds it.
#: Written out rather than read from `RULED_UNREADABLE`, because a test that
#: reads the table it is checking passes whatever the table says. When a
#: ruling lands this tuple is edited in the same commit as the table, and the
#: edit is the record that the count moved on purpose.
#:
#: THE TWO `vix_ar1_debiased` CELLS NAMED A CLOSED BLOCKER until 2026-09-18.
#: `vix-ar1-band-not-adopted` was closed on 2026-09-15 by the design repo's
#: `closes-vix-ar1-band-not-adopted`, on Simon's `ruling-vix-ar1-band-adopted`.
#: The CELLS did not move, because the ruling adopted a band that never landed
#: in these tables, so the count below is unchanged and the row count is still
#: seven. What changed is only which entry is honestly named: a closed blocker
#: quoted as live reads as a decision still owed to Simon, and it is not -- the
#: work owed is the table entry. The row count moving is what the comment above
#: is about; this edit does not move it.
#:
#: SEVEN BECAME FIVE ON 2026-09-19. `fear_gauge_dn1`'s two cells left this
#: tuple when its whole-tape band, ruled on 2026-09-15 by
#: `ruling-nineteen-rows-with-dn3-re-derived`, was composed into
#: `REAL_MARKETS_RULED` and `_504` as `facts.RULED_FEAR_DN1_BAND`. That is
#: the row count moving on purpose, in the same commit as the table.
#:
#: FIVE BECAME THREE the same day. `fear_gauge_dn3` kept its shipped
#: whole-record ruler on Simon's ruling of 2026-09-19 ("the model dictates
#: the performance, not the measurement") and it was composed in as
#: `facts.RULED_FEAR_DN3_BAND`. The blocker four-level-rows-unbanded holds
#: no cell any more; the three left are each behind a table entry owed.
THREE = (
    (252, "vix_ar1_debiased", "vix-ar1-ruled-band-not-in-the-tables"),
    (504, "corr_persistence_acf1", "corr-persistence-504-unbanded"),
    (504, "vix_ar1_debiased", "vix-ar1-ruled-band-not-in-the-tables"),
)


def mid_band_panel(days: int) -> dict[str, float]:
    """A reading for all nineteen rows, mid-band on whichever ruler has one.

    Built from the DECADE table where the ruled table has no band, so the
    three unreadable cells carry a value that the decade ruler would grade as
    IN. A fallback from the ruled basis to the decade one therefore shows up
    here as a verdict rather than as an error, which is the failure mode the
    absent-band tests below are written against.
    """
    shipped = (envelope.BANDS_504 if days == 504 else facts.REAL_MARKETS)
    out: dict[str, float] = {}
    for key in GRADED:
        band = facts.ruled_band(key, days) or shipped.get(key)
        if band is None:
            # `vix_ar1_debiased` has no band on either ruler. Its own
            # instrument is bounded above by 1 + 4/n, so 0.95 is a reading
            # the engine produces rather than a value invented here.
            out[key] = 0.95
            continue
        out[key] = (band[0] + band[1]) / 2.0
    return out


# --------------------------------------------------------------------------
# 1. Registration is not reach
# --------------------------------------------------------------------------

def live_lookups() -> dict[int, list[tuple[str, dict]]]:
    """Every band table a library lookup actually returns, per horizon.

    The lookups themselves, not a list of names: a table that stops being
    returned drops out of here without anything being edited.
    """
    out: dict[int, list[tuple[str, dict]]] = {d: [] for d in HORIZONS}
    for days, row in facts.RULERS_BY_HORIZON.items():
        out.setdefault(int(days), []).append(
            (row["bands_name"], dict(row["bands"])))
    for basis, by_horizon in envelope.RULERS_BY_BASIS.items():
        for days, entry in by_horizon.items():
            table, _noise, name = entry
            out.setdefault(int(days), []).append(
                (f"envelope.RULERS_BY_BASIS[{basis!r}] -> {name}",
                 dict(table)))
    for days, table in facts.RULED_BY_HORIZON.items():
        out.setdefault(int(days), []).append(
            (f"facts.RULED_BY_HORIZON[{days}]", dict(table)))
    return out


def rows_reached_by_nothing(registered, reachable, unreadable):
    """The rows of `registered` that no lookup returns and nothing names.

    A pure function of three arguments, so the same check runs against the
    live library and against a reconstruction of `b66e691`. A check that can
    only read today's globals cannot be shown to refuse the defect it was
    written for, and that is the sixth measurement defect in another form.

    `registered` and `reachable` are `(name, days, {row: band})` triples;
    `unreadable` maps a horizon to the rows named absent there.
    """
    lost = []
    for name, days, table in registered:
        carried: dict[str, list] = {}
        for via, other_days, other in reachable:
            if int(other_days) != int(days):
                continue
            for row, band in other.items():
                carried.setdefault(row, []).append(
                    ((float(band[0]), float(band[1])), via))
        named = set(unreadable.get(int(days), ()))
        for row, band in table.items():
            want = (float(band[0]), float(band[1]))
            if any(got == want for got, _ in carried.get(row, [])):
                continue
            if row in named:
                continue
            lost.append(
                f"{name} at {days}d: {row} is registered at {want} and no "
                f"lookup returns that band; the bands lookups do return for "
                f"it are {carried.get(row, [])} and nothing names it absent")
    return lost


def registered_tables():
    """Every registered table that grades. A table with no basis grades none."""
    out = []
    for table, days, name in facts._KNOWN_TABLES:
        try:
            facts.band_basis(name)
        except ValidationError:
            continue
        out.append((name, int(days), dict(table)))
    return out


def reachable_tables():
    """Every band table a library lookup actually returns, as triples."""
    out = []
    for days, entries in live_lookups().items():
        for via, table in entries:
            out.append((via, days, table))
    return out


def test_every_registered_band_table_is_reached_band_for_band():
    """The defect itself: a registered table no lookup can return.

    A registered band table passes three ways. A lookup returns it. Or every
    row it holds is carried, with the SAME band, into a table a lookup does
    return at that horizon. Or a row carried by nothing is NAMED in
    `RULED_UNREADABLE`, which is absence with a reason.
    """
    lost = rows_reached_by_nothing(
        registered_tables(), reachable_tables(), facts.RULED_UNREADABLE)
    assert not lost, (
        "a band table is registered and read by nothing, which is the "
        "defect this file exists for:\n  " + "\n  ".join(lost))


def test_the_same_check_refuses_the_state_at_b66e691():
    """The refusing control, on the real tables rather than on a fixture.

    At `b66e691` the universal tables were registered in `_KNOWN_TABLES`,
    both `RULERS_BY_HORIZON` entries held the decade tables, and no composed
    ruled table or `RULED_UNREADABLE` existed. `facts.REAL_MARKETS` carried
    every shape row's KEY under a DIFFERENT band, so a key-for-key reach
    check would have called the defect its own repair. Reconstructed here
    from the live tables, which is what makes the control real: the bands
    are the ones the library holds now.
    """
    b66e691_registered = [
        ("facts.REAL_MARKETS", 252, dict(facts.REAL_MARKETS)),
        ("facts.REAL_MARKETS_UNIVERSAL", 252,
         dict(facts.REAL_MARKETS_UNIVERSAL)),
        ("facts.REAL_MARKETS_UNIVERSAL_504", 504,
         dict(facts.REAL_MARKETS_UNIVERSAL_504)),
    ]
    b66e691_reachable = [
        ("facts.RULERS_BY_HORIZON[252]", 252, dict(facts.REAL_MARKETS)),
        ("facts.RULERS_BY_HORIZON[504]", 504, dict(facts.REAL_MARKETS_504)),
    ]
    lost = rows_reached_by_nothing(b66e691_registered, b66e691_reachable, {})
    universal_lost = [line for line in lost if "UNIVERSAL" in line]
    assert len(universal_lost) == 28, (
        f"the check found {len(universal_lost)} unreachable universal rows "
        f"at b66e691 and there were 28, fourteen at each horizon; a check "
        f"that does not refuse that state cannot be said to hold the repair "
        f"in place")
    assert not [line for line in lost
                if line.startswith("facts.REAL_MARKETS at")], (
        "facts.REAL_MARKETS was returned by a lookup at b66e691 and the "
        "check has to pass it, or it is a ban on registration rather than a "
        "check on reach")


def test_reach_is_band_for_band_and_not_key_for_key():
    """A key-for-key check would have passed `b66e691`, so measure the gap."""
    universal = dict(facts.REAL_MARKETS_UNIVERSAL)
    decade = dict(facts.REAL_MARKETS)
    shared = [k for k in universal if k in decade]
    assert len(shared) == 14, (
        "the two tables no longer share the fourteen shape rows, so this "
        "test is no longer constructing the b66e691 state")
    differ = [k for k in shared
              if tuple(universal[k]) != tuple(map(float, decade[k]))]
    assert len(differ) >= 12, (
        f"only {len(differ)} of {len(shared)} shape rows carry a different "
        f"band on the two rulers; a key-for-key reach check would be nearly "
        f"indistinguishable from a band-for-band one and this file's first "
        f"test would have little left to catch")


# --------------------------------------------------------------------------
# 2. Thirty-five of thirty-eight, and the three by name
# --------------------------------------------------------------------------

def test_the_ruled_band_reaches_thirty_five_of_the_thirty_eight_cells():
    """The count the blocker turns on, walked through the library."""
    readable = [(d, r) for d in HORIZONS for r in GRADED
                if facts.ruled_band(r, d) is not None]
    unreadable = [(d, r) for d in HORIZONS for r in GRADED
                  if facts.ruled_band(r, d) is None]
    assert len(readable) + len(unreadable) == 38
    assert len(readable) == 35, (
        f"the ruled band reaches {len(readable)} of 38 cells, not 35. If a "
        f"ruling landed, the THREE tuple in this file moves in the same "
        f"commit; unreadable today: {sorted(unreadable)}")


@pytest.mark.parametrize("days,row,blocker", THREE)
def test_each_unreachable_cell_is_named_with_a_reason(days, row, blocker):
    """Absence with a reason, not a missing key a reader has to infer."""
    assert facts.ruled_band(row, days) is None, (
        f"{row} at {days}d now has a ruled band. The blocker {blocker} has "
        f"moved and this file's THREE tuple has not")
    reason = facts.RULED_UNREADABLE.get(days, {}).get(row)
    assert reason, (
        f"{row} at {days}d has no ruled band and RULED_UNREADABLE[{days}] "
        f"does not say why, so a consumer has to infer absence from a "
        f"missing key")
    assert len(reason) > 40, f"{row} at {days}d carries no usable reason"


def test_no_graded_row_is_dropped_from_the_ruled_table_in_silence():
    """The 504 control: one line out of `RULED_UNREADABLE` has to fail here.

    `facts.py` names `RULED_UNREADABLE[504]['corr_persistence_acf1']` as the
    single line that changes when the row-definition ruling lands. Delete it
    without adding the band and this test is what refuses.
    """
    for days in HORIZONS:
        absent = {r for r in GRADED if facts.ruled_band(r, days) is None}
        named = set(facts.RULED_UNREADABLE.get(days, {}))
        assert absent == named, (
            f"at {days}d the rows with no ruled band are {sorted(absent)} "
            f"and the rows NAMED as having none are {sorted(named)}; a row "
            f"in the first set and not the second is dropped in silence, "
            f"and one in the second and not the first is named absent while "
            f"a lookup returns it")


# --------------------------------------------------------------------------
# 3. The producer reads the ruled band, and never fills from the decade one
# --------------------------------------------------------------------------

@pytest.mark.parametrize("days", HORIZONS)
def test_score_grades_against_the_ruled_table_when_asked_for_it(days):
    """`envelope.score(basis="ruled")` reads the composed ruled table."""
    scored = envelope.score(mid_band_panel(days), horizon_days=days,
                            basis="ruled")
    assert scored["ruler"] == ("facts.REAL_MARKETS_RULED_504" if days == 504
                               else "facts.REAL_MARKETS_RULED")
    assert scored["basis"] == "ruled"
    assert scored["basis_detail"] == facts.band_basis(scored["ruler"])
    for row in GRADED:
        want = facts.ruled_band(row, days)
        got = scored["statistics"][row]["band"]
        assert (tuple(got) if got else None) == want, (
            f"{row} at {days}d was graded against {got} and the ruled band "
            f"is {want}")


@pytest.mark.parametrize("days,row,blocker", THREE)
def test_an_unreadable_cell_is_never_filled_from_the_decade_table(
        days, row, blocker):
    """The fallback that would make a blocker disappear without a ruling.

    One of the three cells HAS a decade band, so a producer that fell back
    would print a verdict rather than raise, and the ship blocker would read
    as cleared. The cell has to come back with no band, no verdict and the
    reason attached.
    """
    scored = envelope.score(mid_band_panel(days), horizon_days=days,
                            basis="ruled")
    cell = scored["statistics"][row]
    assert cell["band"] is None, (
        f"{row} at {days}d came back graded against {cell['band']} under "
        f"blocker {blocker}, and it has no ruled band")
    assert cell["in_band"] is None
    assert cell["unreadable"], f"{row} at {days}d is absent with no reason"
    assert row in scored["unreadable"]


@pytest.mark.parametrize("days", HORIZONS)
def test_the_unreadable_cells_are_named_and_never_folded_into_the_total(days):
    """`of` counts what was graded. `unreadable_of` counts what was not."""
    scored = envelope.score(mid_band_panel(days), horizon_days=days,
                            basis="ruled")
    expected = sorted(r for d, r, _ in THREE if d == days)
    assert sorted(scored["unreadable"]) == expected
    assert scored["unreadable_of"] == len(expected)
    assert scored["of"] == len(GRADED) - len(expected), (
        "an unreadable cell is inside the denominator, so a count of "
        "'19 of 19' can be written about a bar that tested fewer")
    assert scored["in_band"] <= scored["of"]


@pytest.mark.parametrize("days", HORIZONS)
def test_the_edge_form_says_which_row_set_it_counted(days):
    """Two counts in one block, over two row sets, each naming its own.

    `published_edge_form` counts the horizon's whole graded set and
    `unreadable_of` counts the rows the caller handed in. `certify` grades
    `aggregate_panels(panels, keys=facts.SHAPE)`, so every certificate the
    library produces reads `unreadable_of` 0 at 252 beside an edge form
    saying "3 of 3 unreadable". Both are right about different row sets, and
    a reader who takes them as one count reads a bar that tested fourteen as
    a bar that tested nineteen.
    """
    shape_only = {k: v for k, v in mid_band_panel(days).items()
                  if k in facts.SHAPE}
    scored = envelope.score(shape_only, horizon_days=days, basis="ruled")
    assert scored["panel_rows"] == len(facts.SHAPE)
    assert scored["edge_form_rows"] == len(GRADED)
    assert scored["edge_form_rows"] != scored["panel_rows"], (
        "a SHAPE-only panel and the horizon's graded set are the same size, "
        "so this test no longer constructs the disagreement it guards")
    # The form's unreadable count is the horizon's, not the panel's.
    horizon_unreadable = len(facts.RULED_UNREADABLE.get(days, {}))
    assert f"{horizon_unreadable} of {horizon_unreadable} unreadable" in (
        scored["edge_form"])
    assert scored["unreadable_of"] <= horizon_unreadable


def test_the_edge_form_is_absent_on_a_basis_that_is_not_the_bar():
    """A published bar form under a ruler the bar does not read is a claim."""
    scored = envelope.score(mid_band_panel(252), horizon_days=252,
                            basis="shipped")
    assert scored["edge_form"] is None
    assert scored["edge_form_rows"] is None


# --------------------------------------------------------------------------
# 4. The default is the bar, and the shipped basis is still reachable
# --------------------------------------------------------------------------

def test_the_default_basis_is_the_band_the_project_ruled():
    """CHANGED 2026-09-15, and the change is what this file exists for.

    This asserted `DEFAULT_BAND_BASIS == "shipped"` under the docstring
    "which band the bar reads is a ruling, and a default is not one". The
    ruling is `ruling-the-ruler-is-the-universal-band` and it was made on
    2026-09-14; what the default was doing was carrying the retired decade
    table to every caller that did not know a basis argument had been
    added, which is every caller written before it was. The two facts the
    old test was really binding survive as the two lines below it: the
    shipped row is still the object `RULERS_BY_HORIZON` names, and the
    default is one of the two bases rather than a third thing.
    """
    assert envelope.DEFAULT_BAND_BASIS == "ruled"
    assert envelope.DEFAULT_BAND_BASIS == envelope.BAR_BAND_BASIS
    assert envelope.RULERS_BY_BASIS["shipped"] is envelope.RULERS_BY_HORIZON
    # ONE definition, not two. `facts` needs the same default for
    # `compare_to_real_markets` and `report`, and two modules each holding
    # their own is how the library came to grade a 504-day panel two ways.
    assert envelope.DEFAULT_BAND_BASIS is facts.DEFAULT_BAND_BASIS


@pytest.mark.parametrize("days", HORIZONS)
def test_the_shipped_tables_grade_exactly_what_they_graded_before(days):
    """Naming `shipped` still gets the decade tables, unmoved.

    The default no longer does, so this names the basis. What it guards is
    that moving the default did not edit the shipped tables underneath it:
    a caller with a decade-band number on the record can still reproduce it
    by asking for the basis it was taken at.
    """
    panel = mid_band_panel(days)
    shipped = envelope.score(panel, horizon_days=days, basis="shipped")
    expected = envelope.BANDS_504 if days == 504 else facts.REAL_MARKETS
    assert shipped["basis"] == "shipped"
    assert shipped["ruler"] == ("envelope.BANDS_504" if days == 504
                               else "facts.REAL_MARKETS")
    for row in GRADED:
        got = shipped["statistics"][row]["band"]
        want = expected.get(row)
        assert (tuple(got) if got else None) == (
            (float(want[0]), float(want[1])) if want else None)


@pytest.mark.parametrize("days", HORIZONS)
def test_an_unnamed_basis_is_the_ruled_one_and_they_are_not_the_same(days):
    """The default IS the ruled basis, and it grades differently.

    The second half is the one that matters. A default that named `ruled`
    and returned the decade tables would satisfy the first assertion and
    nothing else here, and that is exactly the defect this branch found one
    storey down: the ruling was adopted against one object and computed
    against another.
    """
    panel = mid_band_panel(days)
    default = envelope.score(panel, horizon_days=days)
    ruled = envelope.score(panel, horizon_days=days, basis="ruled")
    shipped = envelope.score(panel, horizon_days=days, basis="shipped")
    assert default == ruled
    assert default["ruler"] != shipped["ruler"]
    assert default["basis_detail"] != shipped["basis_detail"]
    # The 504 table is a row short and says which, rather than printing a
    # count against a denominator nobody tested.
    if days == 504:
        assert "corr_persistence_acf1" in default["unreadable"]
        assert default["shape_of"] == shipped["shape_of"] - 1


def test_a_basis_nobody_derived_is_refused_rather_than_guessed():
    """The same refusal `rulers_for_horizon` makes for an unmeasured horizon."""
    with pytest.raises(ValidationError) as caught:
        envelope.score(mid_band_panel(252), basis="universal")
    assert "band basis" in str(caught.value)
    assert "ruled" in str(caught.value) and "shipped" in str(caught.value)


# --------------------------------------------------------------------------
# 5. The composed table carries the universal bands unchanged
# --------------------------------------------------------------------------

def test_the_composed_table_carries_the_universal_bands_band_for_band():
    """Composition may add rows. It may not edit one under the same name."""
    pairs = ((facts.REAL_MARKETS_UNIVERSAL, facts.REAL_MARKETS_RULED, 252),
             (facts.REAL_MARKETS_UNIVERSAL_504, facts.REAL_MARKETS_RULED_504,
              504))
    for universal, ruled, days in pairs:
        for row, band in universal.items():
            if row not in ruled:
                assert row in facts.RULED_UNREADABLE.get(days, {}), (
                    f"{row} is in the universal table at {days}d, out of "
                    f"the composed one, and named absent by nothing")
                continue
            assert tuple(ruled[row]) == tuple(band), (
                f"{row} reads {tuple(band)} on the universal table at "
                f"{days}d and {tuple(ruled[row])} on the composed one; a "
                f"band was edited under an unchanged name")


def test_the_rows_the_composition_adds_are_the_four_off_panel_rows():
    """The only rows the composed table adds over its universal component.

    Two whole-record index rows and, since 2026-09-19, the two fear rows,
    whose ^VIX series the 32-name equity panel cannot carry.
    """
    added_252 = set(facts.REAL_MARKETS_RULED) - set(
        facts.REAL_MARKETS_UNIVERSAL)
    added_504 = set(facts.REAL_MARKETS_RULED_504) - set(
        facts.REAL_MARKETS_UNIVERSAL_504)
    four = {"index_drift_pct", "index_tail_dn3_pct", "fear_gauge_dn1",
            "fear_gauge_dn3"}
    assert added_252 == four
    assert added_504 == four
    assert facts.REAL_MARKETS_RULED["fear_gauge_dn1"] == (0.39, 3.03)
    assert facts.REAL_MARKETS_RULED_504["fear_gauge_dn1"] == (0.59, 2.73)
    # The -3 per cent row keeps its shipped ruler at both horizons, and
    # the ruled table reads the same object the shipped tables do.
    assert facts.REAL_MARKETS_RULED["fear_gauge_dn3"] == (2.60, 9.58)
    assert facts.REAL_MARKETS_RULED_504["fear_gauge_dn3"] == (2.60, 9.58)
    assert (facts.REAL_MARKETS_RULED["fear_gauge_dn3"]
            == tuple(facts.REAL_MARKETS["fear_gauge_dn3"])
            == tuple(envelope.BANDS_504["fear_gauge_dn3"]))


def test_every_ruled_table_states_what_it_is():
    """A table that can grade a record has to be able to say what it is."""
    for name in ("facts.REAL_MARKETS_RULED", "facts.REAL_MARKETS_RULED_504"):
        basis = facts.band_basis(name)
        for field in ("era", "roster", "rule", "rows"):
            assert basis.get(field), f"{name} records no {field}"
