"""The scoring rule, and the artefact each of its guards refuses.

Every test here NAMES the thing it rejects and then BUILDS it. That is the
charter's rule and it is the load-bearing half: five guards in this project
were found inert in one day, and what they had in common is that none of
them could say which construction it caught. "Guards the objective" is not
checkable; "scores a panel one rounding quantum inside every band edge the
same as a panel at every centre" is.

Nothing here pins a measured result. The two-sided assertions are
derivations -- a term against its own closed form, a `z` against
`centre_distance`'s `z_r`, a band against `band_from_windows` on the windows
it was built from -- so a defect changes a relationship rather than a
literal, and the test fails honestly instead of being retuned.
"""
from __future__ import annotations

import math
import statistics

import pytest

from tradefloor import facts, loss
from tradefloor._core import ValidationError
from tradefloor.facts import (AGGREGATE, MEDIAN_SE_FACTOR, REAL_MARKETS_504,
                              REAL_MARKETS_ADJUSTMENTS_504,
                              REAL_MARKETS_PROVENANCE,
                              REAL_MARKETS_WINDOWS_504,
                              REAL_PERSISTENCE_WINDOWS_504, SHAPE,
                              TWO_SIGNIFICANT_FIGURES, WINDOW_HORIZONS,
                              band_from_windows, centre_distance, real_centre,
                              real_centre_df, real_centre_se, real_windows,
                              rule_row, trimmed_sd)


def rounding_quantum(value: float, key: str) -> float:
    """`round_outward`'s own step at this edge, so the test uses the band's."""
    if key in TWO_SIGNIFICANT_FIGURES:
        return 10.0 ** (math.floor(math.log10(abs(value))) - 1)
    return 0.01


# --------------------------------------------------------------------------
# The form
# --------------------------------------------------------------------------


def test_rule_reduces_to_the_square_near_the_centre():
    """REFUSES: a term that has traded the square's curvature for robustness.

    The whole reason a bounded-influence form is safe to search on is that it
    is the quadratic where the search spends its time. A term that flattened
    near zero would buy robustness with the identifiability the objective
    exists to provide, and it would look fine on every far-out case.

    Three derivations, no literals. At a large `nu` the term IS `z^2` to the
    order its own expansion gives, `(nu + 1) ln(1 + z^2/nu) = z^2 + (z^2 -
    z^4/2)/nu + O(nu^-2)`, so the tolerance is derived and not chosen. Its
    second derivative at zero is `2 (nu + 1) / nu`, which is STRICTLY ABOVE
    the square's 2 at every finite `nu` -- and note the direction: near the
    centre the rule is steeper than the quadratic, not flatter. The design
    note's §10e says the term is "below `z^2` for `|z| >= 1`" at `nu = 5`; it
    is not, and cannot be, because it starts above. The crossover at `nu = 5`
    sits between 1.2 and 1.5, and the property that actually matters is the
    RATIO: doubling `z` multiplies the square by four and the rule by less,
    at every `nu` and every `z`. That is the bounded influence, and it is
    asserted here rather than the note's inequality.
    """
    for df in (1e4, 1e6, 1e8):
        for z in (0.0, 0.5, 1.0, 2.0, 3.0):
            first_order = abs(z ** 2 - z ** 4 / 2) / df
            assert loss.rule_term(z, df) == pytest.approx(
                z * z, abs=2 * first_order + 1e-12)

    # Steeper than the square near the centre, and further below it the
    # further out the row sits.
    assert loss.rule_term(1.0, 5.0) > 1.0
    assert loss.rule_term(1.2, 5.0) > 1.2 ** 2
    assert loss.rule_term(1.5, 5.0) < 1.5 ** 2
    for df in (2.0, 5.0, 8.0, 30.0):
        for z in (2.0, 4.0, 6.0, 12.0):
            assert loss.rule_term(z, df) < z * z
            # The square's ratio on a doubling is exactly four; the rule's
            # is strictly less, which is the whole of "bounded influence".
            assert (loss.rule_term(2 * z, df) / loss.rule_term(z, df)) < 4.0

    for df in (2.0, 5.0, 8.0, 30.0, 199.0):
        h = 1e-4
        second = (loss.rule_term(h, df) - 2 * loss.rule_term(0.0, df)
                  + loss.rule_term(-h, df)) / (h * h)
        assert second == pytest.approx(2 * (df + 1) / df, rel=1e-6)
        assert second > 2.0

    with pytest.raises(ValidationError):
        loss.rule_term(1.0, 0.0)


def test_the_rule_refuses_the_compensation():
    """REFUSES: the trade a quadratic objective takes and a bounded one does not.

    The construction is the charter's central sentence made arithmetic: one
    row the model cannot reach, at `z = 6` on `nu = 5`, and three rows it
    holds at the centre on `nu = 8`. Move the dials so the far row improves
    to 4 and the three others drift to 1.5. Under a sum of squares that is a
    net GAIN and the optimiser takes it, and the far row's miss is now spread
    across four rows where no single one of them looks wrong. Under the rule
    it is a net LOSS and the miss stays visible at its own `|z|`.

    Both sides are computed here rather than quoted, so the test fails if the
    term's shape changes and not if the note's arithmetic is retyped.
    """
    far_before, far_after = 6.0, 4.0
    near_before, near_after = 0.0, 1.5
    df_far, df_near = 5.0, 8.0

    gauss_before = far_before ** 2 + 3 * near_before ** 2
    gauss_after = far_after ** 2 + 3 * near_after ** 2
    rule_before = (loss.rule_term(far_before, df_far)
                   + 3 * loss.rule_term(near_before, df_near))
    rule_after = (loss.rule_term(far_after, df_far)
                  + 3 * loss.rule_term(near_after, df_near))

    assert gauss_after < gauss_before, "the square would decline the trade"
    assert rule_after > rule_before, "the rule would take the trade"
    # And the far row is still visibly far under the rule: its own term after
    # the move is the largest single term on the panel.
    assert (loss.rule_term(far_after, df_far)
            > 3 * loss.rule_term(near_after, df_near))


# --------------------------------------------------------------------------
# The plateau
# --------------------------------------------------------------------------


def constant_panels(values: dict, seeds: int = 30) -> list[dict]:
    """`seeds` identical per-seed panels, so the model term is exactly zero.

    A degenerate panel on purpose: it isolates the tape side, and it is the
    only construction under which `S` at the centres is exactly 0 rather than
    a small positive number that a test would have to tolerate.
    """
    return [dict(values) for _ in range(seeds)]


def test_two_medians_both_in_band_are_not_equal():
    """REFUSES: a panel one rounding quantum inside every band edge.

    THIS IS THE ARTEFACT, built rather than described: fourteen rows each
    sitting one of the band rule's own rounding quanta inside its ceiling.
    Every row is in band, the pass count reads 14 of 14, and it is the same
    14 of 14 a panel at every tape centre reads. 1,139 of the 1,648 records
    this programme has measured are tied at exactly that count.

    The rule separates them, and the assertion is the derivation rather than
    the gap: the centre panel scores exactly zero, every per-row term of the
    edge panel is strictly positive, and the edge panel's `S` is the sum of
    those terms. A rule that returned a number without the per-row terms
    adding up to it would fail here, and so would one flat inside the band.
    """
    centres = {row: rule_row(row, horizon_days=504)["centre"] for row in SHAPE}
    edges = {}
    for row in SHAPE:
        lo, hi = REAL_MARKETS_504[row]
        edges[row] = hi - rounding_quantum(hi, row)
        assert lo <= edges[row] <= hi, row

    at_centre = loss.scoring_rule(constant_panels(centres),
                                  horizon_days=504, rows=SHAPE)
    at_edge = loss.scoring_rule(constant_panels(edges),
                                horizon_days=504, rows=SHAPE)

    # The count cannot tell them apart.
    assert all(at_centre["rows"][r]["in_band"] for r in SHAPE)
    assert all(at_edge["rows"][r]["in_band"] for r in SHAPE)
    assert (sum(at_centre["rows"][r]["in_band"] for r in SHAPE)
            == sum(at_edge["rows"][r]["in_band"] for r in SHAPE) == len(SHAPE))

    # The rule can.
    assert at_centre["S"] == pytest.approx(0.0, abs=1e-12)
    assert all(at_edge["rows"][r]["term"] > 0 for r in SHAPE)
    assert at_edge["S"] == pytest.approx(
        sum(at_edge["rows"][r]["term"] for r in SHAPE), rel=1e-12)
    assert at_edge["S"] > at_centre["S"]
    assert not at_centre["blind"] and not at_edge["blind"]

    # And the band position is reported and NEVER summed: the edge panel sits
    # at position ~1 on every row and the rule's terms are not that number.
    for row in SHAPE:
        assert at_edge["rows"][row]["band_position"] > 0.5


# --------------------------------------------------------------------------
# The blind row
# --------------------------------------------------------------------------


def test_a_blind_row_is_listed_and_never_substituted(monkeypatch):
    """REFUSES: a rule table with one row's standard error missing.

    Two failures are possible here and both are silent. The rule could DROP
    the row, and then `S` is a sum over thirteen rows published under the
    name of a sum over fourteen. Or it could reach for a neighbour's error,
    which is the substitution `SEED_SD_LEVEL_PROVENANCE` already rules out
    for the seed scale.

    The construction: remove `cross_sectional_corr`'s tape error and put its
    graded value FAR from its centre, so a substituted error would show up as
    a large term. Then move its neighbour's error by a factor of ten and
    check the blind row still contributes nothing -- the neighbour's own term
    moves, and the blind row's does not appear.
    """
    blind_row, neighbour = "cross_sectional_corr", "sector_excess_corr"
    real_se = real_centre_se

    def no_se_for_one(key, *, horizon_days=facts.TRADING_DAYS_PER_YEAR):
        if key == blind_row:
            return None
        return real_se(key, horizon_days=horizon_days)

    monkeypatch.setattr(facts, "real_centre_se", no_se_for_one)

    rows = (blind_row, neighbour)
    values = {blind_row: rule_row(neighbour, horizon_days=504)["centre"],
              neighbour: rule_row(neighbour, horizon_days=504)["centre"] + 0.05}
    panels = constant_panels(values)

    out = loss.scoring_rule(panels, horizon_days=504, rows=rows)
    assert blind_row in out["blind"]
    assert "se" in out["blind"][blind_row]
    assert out["rows"][blind_row]["term"] is None
    assert out["rows"][blind_row]["z"] is None
    # The band verdict and position survive; only the term is withheld.
    assert out["rows"][blind_row]["in_band"] is not None
    assert out["rows"][blind_row]["band_position"] is not None
    assert out["scored"] == [neighbour]
    assert out["S"] == pytest.approx(out["rows"][neighbour]["term"], rel=1e-12)

    def tenfold(key, *, horizon_days=facts.TRADING_DAYS_PER_YEAR):
        if key == blind_row:
            return None
        return 10.0 * real_se(key, horizon_days=horizon_days)

    monkeypatch.setattr(facts, "real_centre_se", tenfold)
    moved = loss.scoring_rule(panels, horizon_days=504, rows=rows)
    assert moved["rows"][neighbour]["term"] != out["rows"][neighbour]["term"]
    assert moved["rows"][blind_row]["term"] is None
    assert moved["S"] == pytest.approx(
        moved["rows"][neighbour]["term"], rel=1e-12)
    assert moved["rule_fingerprint"] != out["rule_fingerprint"]


def test_the_pooled_fear_row_is_refused_by_name_until_its_bootstrap_lands():
    """REFUSES: a seventeen-row score published over sixteen rows.

    `fear_gauge_dn3` has a centre on the record -- the pooled tape median,
    +5.73 over the 107 sessions since 1990 -- and no error: the window-block
    bootstrap that would give it one has never been run. `rule_row` therefore
    raises, naming the row, naming which of the three is missing, and naming
    what settles it. `require=False` is the same refusal as data, which is
    what lets `scoring_rule` list the row rather than parse a message.
    """
    with pytest.raises(ValidationError) as exc:
        rule_row("fear_gauge_dn3", horizon_days=252)
    message = str(exc.value)
    assert "fear_gauge_dn3" in message
    assert "se" in message
    assert "fear_band.py" in message

    soft = rule_row("fear_gauge_dn3", horizon_days=252, require=False)
    assert soft["missing"] == ("se",)
    assert soft["centre"] is not None and soft["df"] is not None

    # Sixteen of the eighteen graded rows have all three, at both horizons.
    for horizon in WINDOW_HORIZONS:
        table = loss.rule_table(horizon)
        complete = [k for k, v in table.items() if not v["missing"]]
        assert sorted(k for k, v in table.items() if v["missing"]) == [
            "fear_gauge_dn3"]
        assert len(complete) == len(table) - 1


# --------------------------------------------------------------------------
# The frozen tables
# --------------------------------------------------------------------------


def test_the_frozen_tables_are_not_read():
    """REFUSES: a corpus score that took pt-v1's seed noise by omission.

    `SEED_SD_504` is the frozen denominator of every published room figure
    and it is not any candidate's own noise: against the vectors the corpus
    holds it runs 0.46x to 6.04x the truth, so a row it understates six-fold
    is weighted thirty-six times too heavily. The guard is the signature --
    `se_model` and `df_model` have no default, so the omission cannot
    happen -- and passing the table on purpose warns by name.
    """
    medians = {row: rule_row(row, horizon_days=504)["centre"] for row in SHAPE}

    with pytest.raises(TypeError):
        loss.scoring_rule_from_medians(medians, horizon_days=504)
    with pytest.raises(TypeError):
        loss.scoring_rule_from_medians(medians, horizon_days=504,
                                       se_model=facts.SEED_SD_504)

    with pytest.warns(RuntimeWarning, match="SEED_SD_504"):
        out = loss.scoring_rule_from_medians(
            medians, horizon_days=504, se_model=facts.SEED_SD_504,
            df_model=29, rows=SHAPE)
    # It still SCORES -- the guard is the naming, not a refusal -- and at the
    # tape centres it reads exactly zero whatever denominator it was handed,
    # which is the property that makes the warning necessary rather than
    # sufficient: the wrong scale is invisible in the number.
    assert out["blind"] == {}
    assert out["scored"] == sorted(SHAPE)
    assert out["S"] == pytest.approx(0.0, abs=1e-12)
    off = {k: v + 0.5 * facts.SEED_SD_504[k] for k, v in medians.items()}
    with pytest.warns(RuntimeWarning):
        frozen = loss.scoring_rule_from_medians(
            off, horizon_days=504, se_model=facts.SEED_SD_504,
            df_model=29, rows=SHAPE)
    own = loss.scoring_rule_from_medians(
        off, horizon_days=504,
        se_model={k: 3.0 * v for k, v in facts.SEED_SD_504.items()},
        df_model=1433, rows=SHAPE)
    assert frozen["S"] != own["S"], (
        "the model term must move the score, or the warning guards nothing")
    # A copy of the table warns too: the guard is the VALUES, not the object.
    with pytest.warns(RuntimeWarning, match="SEED_SD_504"):
        loss.scoring_rule_from_medians(
            medians, horizon_days=504, se_model=dict(facts.SEED_SD_504),
            df_model=29, rows=SHAPE)
    # A candidate's own errors do not warn.
    import warnings as _w
    with _w.catch_warnings():
        _w.simplefilter("error")
        loss.scoring_rule_from_medians(
            medians, horizon_days=504,
            se_model={k: 1.5 * v for k, v in facts.SEED_SD_504.items()},
            df_model=1433, rows=SHAPE)


# --------------------------------------------------------------------------
# The estimator
# --------------------------------------------------------------------------


def test_the_centre_is_the_rows_own_estimator():
    """REFUSES: a tape centre computed by a different estimator than the row.

    The error has been made on this panel twice, and both times the answer
    looked plausible. The construction that catches it is a row whose two
    estimators disagree materially on the same tape.

    `index_tail_dn3_pct` is the derivable case, and it is a factor of three:
    the row is graded as a POOLED RATE, so its centre is the MEAN of the 35
    window rates, 1.2132; the median of the same 35 windows is 0.397, because
    thirteen of them hold no session at or below -3 per cent at all. Both are
    computed here from the committed window table.

    `fear_gauge_dn3` is the recorded case. The model's row is pooled across
    every seed's sessions, so the like-for-like tape quantity is the median
    of the 107 real sessions, +5.73 -- and the middle of the row's provenance
    triple, +5.30, is the median of the ten WINDOW medians the BAND was built
    from. Two estimators of two quantities; `rule_row` takes the first.
    """
    tail = "index_tail_dn3_pct"
    windows = real_windows(tail, horizon_days=252)
    assert AGGREGATE[tail] == "pooled_rate"
    assert rule_row(tail, horizon_days=252)["centre"] == pytest.approx(
        statistics.fmean(windows), rel=1e-12)
    assert statistics.median(windows) < 0.5 * statistics.fmean(windows)

    dn3 = "fear_gauge_dn3"
    assert AGGREGATE[dn3] == "pooled"
    pooled = rule_row(dn3, horizon_days=252, require=False)["centre"]
    window_median = REAL_MARKETS_PROVENANCE[dn3]["windows"][1]
    assert pooled == REAL_MARKETS_PROVENANCE[dn3]["centre"]
    assert pooled != window_median
    # And the row's own band was built from the window medians, so the two
    # constructions coexist: the band grades, the centre scores.
    assert REAL_MARKETS_PROVENANCE[dn3]["windows"][0] < window_median


def test_the_rule_at_252_reproduces_centre_distance():
    """REFUSES: a `z` that is not the centre diagnostic's own quantity.

    `centre_distance` has computed `(median - centre) / sqrt(se_m^2 +
    se_real^2)` since the four correlation rows joined the window table, and
    the rule's per-row `z` is that number with the horizon opened up. If the
    two ever disagree, one of them has changed estimator, and the objective
    and the diagnostic would then be reporting different things under one
    name. Thirty constructed per-seed readings, one shape row, to 1e-12.
    """
    row = "abs_return_acf20"
    centre = real_centre(row)
    spread = trimmed_sd(real_windows(row))
    values = [centre + spread * math.sin(i) / 3 for i in range(30)]
    panels = [{row: v} for v in values]

    out = loss.scoring_rule(panels, horizon_days=252, rows=(row,))
    diag = centre_distance(values, row)
    assert diag["z_r"] is not None
    assert out["rows"][row]["z"] == pytest.approx(diag["z_r"], rel=1e-12)
    assert out["rows"][row]["se_model"] == pytest.approx(diag["se_m"],
                                                         rel=1e-12)
    assert out["rows"][row]["se_real"] == pytest.approx(diag["se_real"],
                                                        rel=1e-12)


# --------------------------------------------------------------------------
# The 504 windows
# --------------------------------------------------------------------------


def derived_band(key: str, values):
    """`BAND_RULE` on the non-crisis members of `values`."""
    return band_from_windows(key, values)


def test_the_504_bands_derive_from_the_504_windows():
    """REFUSES: a shipped 504-bar band the promoted windows cannot produce.

    A band table with no windows under it is a table nothing can check, and
    that is what the library carried at this horizon until now: fourteen
    edges and no evidence base in the package. The check is the same one the
    252-bar table has -- every edge is `band_from_windows` on the non-crisis
    windows, or it is named in the horizon's adjustments table with a reason.

    The construction that must fail: move one window value by a band width
    and the derived edge no longer matches the shipped one. Run without it,
    the assertion is satisfied by a table of the right shape and the wrong
    contents.
    """
    assert REAL_MARKETS_ADJUSTMENTS_504 == {}, (
        "an adjusted 504 edge must be named here with its reason")
    for key, band in REAL_MARKETS_504.items():
        windows = real_windows(key, horizon_days=504)
        assert windows is not None, key
        got = derived_band(key, windows)
        assert got == pytest.approx(band, rel=1e-9), key

    # Teeth: perturb one window and the derivation stops matching.
    key = "cross_sectional_corr"
    windows = list(real_windows(key, horizon_days=504))
    lo, hi = REAL_MARKETS_504[key]
    windows[0] += (hi - lo)
    assert derived_band(key, windows) != pytest.approx(
        REAL_MARKETS_504[key], rel=1e-9)


def test_a_horizon_with_no_window_table_is_refused_by_name():
    """REFUSES: a 756-day panel answered from the 252-bar windows.

    This is what `real_windows` DID: it took `horizon_days` and ignored it
    for every row but the index tail, so a 756-day caller got the 252-bar
    readings back with no indication. The readings are not interchangeable --
    `abs_return_acf20`'s tape centre is 0.020 over 252 bars and 0.029 over
    504 -- so the answer was wrong by more than its own standard error and
    looked like a number.
    """
    for horizon in WINDOW_HORIZONS:
        assert real_windows("abs_return_acf20", horizon_days=horizon)
    for horizon in (60, 180, 756, 1008):
        with pytest.raises(ValidationError) as exc:
            real_windows("abs_return_acf20", horizon_days=horizon)
        assert str(sorted(WINDOW_HORIZONS)) in str(exc.value)

    at_252 = real_centre("abs_return_acf20", horizon_days=252)
    at_504 = real_centre("abs_return_acf20", horizon_days=504)
    se_252 = real_centre_se("abs_return_acf20", horizon_days=252)
    assert abs(at_504 - at_252) > se_252

    # The index tail row keeps its own two-horizon table and answers at both.
    assert real_windows("index_tail_dn3_pct", horizon_days=504) is not None


def test_the_promoted_windows_are_the_reference_panel_the_bands_came_from():
    """REFUSES: a 504 window table that has drifted from its own crisis flag.

    Three properties, each catching a different construction: the crisis
    window is the widest one on volatility (a mislabelled `crisis_index`
    would put a calm window there), the persistence row is NOT in the shape
    table (reading it there would take a dispersion over six windows of a
    quantity measured on five), and the window labels are contiguous, so no
    real period falls between two of them.
    """
    values = REAL_MARKETS_WINDOWS_504["values"]
    crisis = REAL_MARKETS_WINDOWS_504["crisis_index"]
    vol = values["annualised_vol_pct"]
    assert vol[crisis] == max(vol), (
        "the excluded window is not the crisis one")

    assert "corr_persistence_acf1" not in values
    assert set(REAL_PERSISTENCE_WINDOWS_504["values"]) == {
        "corr_persistence_acf1"}
    assert len(real_windows("corr_persistence_acf1", horizon_days=504)) == 4
    assert len(real_windows("annualised_vol_pct", horizon_days=504)) == 5

    labels = REAL_MARKETS_WINDOWS_504["windows"]
    for earlier, later in zip(labels, labels[1:]):
        assert earlier.split("..")[1] < later.split("..")[0], (earlier, later)


# --------------------------------------------------------------------------
# The recorded degrees of freedom, each re-derived
# --------------------------------------------------------------------------


def test_the_recorded_degrees_of_freedom_are_derivations():
    """REFUSES: a `centre_df` typed in beside a claim rather than derived.

    A number reads as derived because of where it sits, which is how a
    fourth-moment condition for a process the model does not run came to be
    checked and believed. So each of the four is re-derived here from the
    components the record itself carries.
    """
    # The level row: Welch over the two components it was combined from.
    level = REAL_MARKETS_PROVENANCE["index_drift_pct"]
    (_, se_a, n_a), (_, se_b, n_b) = level["centre_components"]
    se = math.sqrt(se_a ** 2 + se_b ** 2)
    df = se ** 4 / (se_a ** 4 / (n_a - 1) + se_b ** 4 / (n_b - 1))
    assert se == pytest.approx(level["centre_se"], abs=0.01)
    assert round(df) == level["centre_df"]
    assert real_centre_df("index_drift_pct") == level["centre_df"]

    # The -1 per cent fear row: the panel's own error form on its recorded
    # across-window trimmed sd, and n - 2 because the trim spends a window.
    dn1 = REAL_MARKETS_PROVENANCE["fear_gauge_dn1"]
    assert dn1["centre_se"] == pytest.approx(
        MEDIAN_SE_FACTOR * dn1["trimmed_sd"] / math.sqrt(dn1["n_windows"]),
        rel=1e-12)
    assert dn1["centre_df"] == dn1["n_windows"] - 2

    # The -3 per cent fear row: a block bootstrap over its ten windows.
    dn3 = REAL_MARKETS_PROVENANCE["fear_gauge_dn3"]
    assert dn3["centre_df"] == dn3["n_windows"] - 1

    # The tail row: recorded at 252 and DERIVED at both, which is the point.
    # A fixed 34 would be right at one horizon and wrong at the other.
    tail = "index_tail_dn3_pct"
    assert real_centre_df(tail, horizon_days=252) == (
        REAL_MARKETS_PROVENANCE[tail]["centre_df"])
    assert real_centre_df(tail, horizon_days=252) == len(
        real_windows(tail, horizon_days=252)) - 1
    assert real_centre_df(tail, horizon_days=504) == len(
        real_windows(tail, horizon_days=504)) - 1
    assert (real_centre_df(tail, horizon_days=504)
            != real_centre_df(tail, horizon_days=252))

    # And every shape row's df is its window count less the two the trimmed
    # median spends, at both horizons.
    for horizon in WINDOW_HORIZONS:
        for row in SHAPE:
            assert real_centre_df(row, horizon_days=horizon) == len(
                real_windows(row, horizon_days=horizon)) - 2


def test_the_welch_combination_and_the_fingerprint():
    """REFUSES: a score that cannot say which tape table produced it.

    The tape moves -- the 504-bar windows landed after the corpus was
    measured, and `fear_gauge_dn3`'s error is still missing -- so two scores
    are only comparable when the table behind them is the same one. The
    fingerprint is that identity, and it must move when any centre, error or
    df does and not otherwise.
    """
    a = loss.rule_table(252)
    b = loss.rule_table(504)
    assert loss.rule_fingerprint(a) != loss.rule_fingerprint(b)
    assert loss.rule_fingerprint(a) == loss.rule_fingerprint(loss.rule_table(252))

    moved = {k: dict(v) for k, v in a.items()}
    moved["annualised_vol_pct"]["centre"] += 1e-9
    assert loss.rule_fingerprint(moved) != loss.rule_fingerprint(a)

    # Welch is an identity and is asserted as one: equal errors on equal
    # degrees of freedom give twice the df; a dominant term takes its own.
    se, df = loss._welch(1.0, 5.0, 1.0, 5.0)
    assert se == pytest.approx(math.sqrt(2.0), rel=1e-12)
    assert df == pytest.approx(10.0, rel=1e-12)
    se, df = loss._welch(1.0, 4.0, 1e-6, 1e9)
    assert df == pytest.approx(4.0, rel=1e-3)
