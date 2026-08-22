"""The calibration objective: two-sided band distance, diagonally weighted.

Half of this file exists for one statistic. The leverage effect's band is
NEGATIVE (-0.30 to -0.10), and a one-sided max(0, value - high) distance
would score a leverage effect of -0.5 -- a large overshoot -- as satisfying
the band. The two-sided form penalises it. The tests here put cases on both
sides of that band, inside it, and on its boundaries, so a future refactor
that quietly reverts to the one-sided form fails HERE rather than silently
inverting the search direction for one statistic while everything else
passes.

The other half pins the two deliberate choices in the loss: membership (the
structurally unreachable statistics are reported but excluded, and promoting
one is a one-tuple edit) and weighting (diagonal, in units of each
statistic's own seed noise, with no unweighted path to fall back to).
"""

import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

import pretium
from pretium.facts import (
    REAL_MARKETS,
    SEED_SD,
    band_distance,
    compare_to_real_markets,
    measure,
)
from pretium.loss import (
    CONSTRAINTS,
    LIVE_TARGETS,
    STRUCTURAL,
    band_distance_loss,
    seed_sd_from_panels,
)

LEVERAGE_BAND = REAL_MARKETS["leverage_effect"]

# The committed post-split sweep re-run: per-seed panels at the shipped
# MARKET_FACTOR_SIGMA, the measurement SEED_SD was derived from. Reading it
# here is what makes the shipped constants reproducible rather than asserted.
RESULTS = (
    Path(__file__).resolve().parent.parent
    / "tools" / "calibration" / "results"
    / "market-factor-sigma-2026-08-21-post-rng-split.json"
)


def baseline_panels():
    sweep = json.loads(RESULTS.read_text())["sweep"]["0.0075"]["panels"]
    return [sweep[seed] for seed in sorted(sweep, key=int)]


# Same roster and length as test_facts, for the same estimator reasons.
UNIVERSE = pretium.Universe.random(40, seed=111)


@pytest.fixture(scope="module")
def facts():
    return measure(seed=3, universe=UNIVERSE, days=180)


# --------------------------------------------------------------------------
# The negative-band trap
# --------------------------------------------------------------------------


def test_the_leverage_band_penalises_both_exits():
    """Both sides of a negative band are exits, and both are charged.

    -0.004 (the effect is absent) sits ABOVE the band and is 0.096 from the
    weak edge; -0.5 (the effect is far too strong) sits BELOW it and is 0.2
    from the strong edge. A loss that charges only one of them would tell a
    search that overshooting is free, and the search would oblige.
    """
    low, high = LEVERAGE_BAND
    assert band_distance(-0.004, low, high) == pytest.approx(0.096)
    assert band_distance(-0.5, low, high) == pytest.approx(0.2)


def test_an_overshot_leverage_effect_is_not_scored_as_satisfying_the_band():
    """The regression this file exists to catch, stated as its own test.

    Under the one-sided refactor max(0, value - high), every value below
    high scores zero -- so -0.5 would read as inside a band whose strong
    edge is -0.30, and the search direction for this statistic would invert
    while every other statistic's tests still passed. If this test fails,
    check `pretium.facts.band_distance` for exactly that form.
    """
    low, high = LEVERAGE_BAND
    assert band_distance(-0.5, low, high) > 0.0
    # And the deeper the overshoot, the larger the penalty: a gradient a
    # minimiser can actually follow back toward the band.
    assert (
        band_distance(-0.6, low, high)
        > band_distance(-0.5, low, high)
        > band_distance(-0.35, low, high)
        > 0.0
    )


def test_inside_the_leverage_band_and_on_its_boundaries_is_free():
    low, high = LEVERAGE_BAND
    assert band_distance(-0.20, low, high) == 0.0
    # Boundaries are IN the band, matching _verdict's `low <= value <= high`.
    assert band_distance(low, low, high) == 0.0
    assert band_distance(high, low, high) == 0.0


def test_band_distance_agrees_with_the_verdict_wording_on_every_band():
    """d_k == 0 exactly where `_verdict` says "matches", for all eight bands.

    The trap lives in two places -- verdict wording and loss arithmetic --
    and facts.py solved the wording first. This pins the two solutions to
    each other, on a grid that straddles every band's edges, so they cannot
    drift apart under separate refactors.
    """
    from pretium.facts import _verdict

    for key, (low, high) in REAL_MARKETS.items():
        width = high - low
        for value in (
            low - width, low - 1e-9, low, low + width / 3,
            high, high + 1e-9, high + width,
        ):
            distance = band_distance(value, low, high)
            verdict = _verdict(value, low, high)
            assert (distance == 0.0) == (verdict == "matches"), (key, value)
            # Outside, the distance is to the NEAREST edge, whatever the
            # band's sign.
            if value < low:
                assert distance == pytest.approx(low - value), (key, value)
            elif value > high:
                assert distance == pytest.approx(value - high), (key, value)


# --------------------------------------------------------------------------
# Membership: reported is not the same as optimised against
# --------------------------------------------------------------------------


def test_the_loss_covers_six_statistics_and_reports_eight():
    assert set(LIVE_TARGETS) == {
        "annualised_vol_pct", "return_acf1",
        "abs_return_acf1", "cross_sectional_corr",
    }
    assert set(CONSTRAINTS) == {"excess_kurtosis", "volume_abs_return_corr"}
    # The structurally unreachable pair, derived as the complement of the
    # membership tuples so nothing else needs editing when one is promoted.
    assert set(STRUCTURAL) == {"leverage_effect", "volume_change_acf1"}
    assert set(LIVE_TARGETS) | set(CONSTRAINTS) | set(STRUCTURAL) == set(
        REAL_MARKETS
    )


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_structural_statistics_ride_along_but_cannot_steer():
    """Moving a structural statistic, however far, moves the loss not at all.

    This is the identifiability gate as arithmetic: an optimiser pointed at
    a target no parameter reaches distorts every other parameter chasing it.
    The structural rows still appear in every result -- they are the
    standing falsification verdict -- but with contribution None, and the
    sum of the contributions that ARE numbers is exactly the loss.
    """
    panels = baseline_panels()
    result = band_distance_loss(panels)

    rows = result["statistics"]
    assert set(rows) == set(REAL_MARKETS)
    for key in STRUCTURAL:
        assert rows[key]["role"] == "structural"
        assert rows[key]["contribution"] is None
        assert rows[key]["distance"] > 0.0  # out of band, and visibly so
    contributions = [
        row["contribution"] for row in rows.values()
        if row["contribution"] is not None
    ]
    assert len(contributions) == len(LIVE_TARGETS) + len(CONSTRAINTS)
    assert result["loss"] == pytest.approx(sum(contributions))

    # Now overshoot leverage to -0.5 on every panel: a catastrophic exit on
    # the other side of its band. The loss must not move by any amount.
    overshot = [dict(panel, leverage_effect=-0.5) for panel in panels]
    assert band_distance_loss(overshot)["loss"] == result["loss"]


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_promoting_a_structural_statistic_is_one_tuple(monkeypatch):
    """Appending a key to LIVE_TARGETS is the whole promotion.

    The GJR asymmetry term is expected to make leverage reachable; when it
    lands, this is the edit. Membership is consulted at call time, so the
    simulated edit here exercises the real path rather than a copy of it.
    """
    monkeypatch.setattr(
        pretium.loss, "LIVE_TARGETS",
        pretium.loss.LIVE_TARGETS + ("leverage_effect",),
    )
    panels = baseline_panels()
    result = band_distance_loss(panels)
    row = result["statistics"]["leverage_effect"]
    assert row["role"] == "live target"
    assert row["contribution"] is not None and row["contribution"] > 0.0
    # And with it live, the -0.5 overshoot now costs more than the absent
    # effect does: the search direction the two-sided distance protects.
    overshot = band_distance_loss(
        [dict(panel, leverage_effect=-0.5) for panel in panels]
    )
    assert overshot["loss"] > result["loss"]


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_in_band_constraints_contribute_zero_until_a_candidate_breaks_them():
    panels = baseline_panels()
    rows = band_distance_loss(panels)["statistics"]
    for key in CONSTRAINTS:
        assert rows[key]["role"] == "constraint"
        assert rows[key]["contribution"] == 0.0
    # Drive kurtosis under its floor of 3 -- the trade the sigma sweep
    # showed correlation wants to make -- and the constraint pushes back.
    broken = band_distance_loss(
        [dict(panel, excess_kurtosis=2.0) for panel in panels]
    )
    assert broken["statistics"]["excess_kurtosis"]["contribution"] > 0.0
    assert broken["loss"] > band_distance_loss(panels)["loss"]


# --------------------------------------------------------------------------
# Weighting: diagonal, visible, and with no accidental unweighted form
# --------------------------------------------------------------------------


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_the_report_says_which_weighting_was_used():
    # Hansen's efficient weighting is the aspiration; the diagonal is the
    # honest version eight moments from thirty seeds can support. Whichever
    # is in force must be readable off the result, not inferred from code.
    result = band_distance_loss(baseline_panels())
    assert result["weighting"] == "diagonal"
    assert result["seed_sd_provenance"]["git_rev"] == "ad91026"
    assert result["panels"] == 6


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_noise_scaling_keeps_the_autocorrelations_in_the_objective():
    """The silent choice made loud: unweighted, this loss is a vol objective.

    At the baseline, pooled volatility exits its band by ~22 points while
    return acf(1) exits by ~0.21 -- so unweighted squared distances put
    99.99% of the loss on volatility and the search would never feel an
    autocorrelation move. In units of each statistic's own seed noise the
    acf exits are of the same order as the vol exit, and this asserts they
    carry a material share.
    """
    result = band_distance_loss(baseline_panels())
    rows = result["statistics"]
    vol = rows["annualised_vol_pct"]["contribution"]
    others = result["loss"] - vol
    assert vol > 0.0
    assert others > 0.25 * result["loss"]
    # The unweighted counterfactual, computed from the same rows: not a
    # form the API offers, which is the point.
    unweighted_vol = rows["annualised_vol_pct"]["distance"] ** 2
    unweighted_rest = sum(
        rows[key]["distance"] ** 2
        for key in (*LIVE_TARGETS, *CONSTRAINTS)
        if key != "annualised_vol_pct"
    )
    assert unweighted_rest < 0.001 * unweighted_vol


def test_there_is_no_unweighted_fallback():
    # A statistic in the loss with no seed sd, or a degenerate one, refuses
    # rather than quietly entering the sum with weight one.
    panel = {key: (low + high) / 2 for key, (low, high) in REAL_MARKETS.items()}
    missing = {k: v for k, v in SEED_SD.items() if k != "return_acf1"}
    with pytest.raises(pretium.ValidationError):
        band_distance_loss(panel, seed_sd=missing)
    degenerate = dict(SEED_SD, return_acf1=0.0)
    with pytest.raises(pretium.ValidationError):
        band_distance_loss(panel, seed_sd=degenerate)


def test_a_live_statistic_that_could_not_be_measured_refuses():
    # If a candidate breaks a statistic's measurability, "contributes zero"
    # would make unmeasurable an attractive direction for the search.
    panel = {key: (low + high) / 2 for key, (low, high) in REAL_MARKETS.items()}
    panel["cross_sectional_corr"] = None
    with pytest.raises(pretium.ValidationError):
        band_distance_loss(panel)
    # A structural statistic degrades instead: it was never in the sum, and
    # a reporting gap must not block the evaluation.
    panel["cross_sectional_corr"] = 0.30
    panel["leverage_effect"] = None
    result = band_distance_loss(panel)
    assert result["statistics"]["leverage_effect"]["measured"] is None
    assert result["statistics"]["leverage_effect"]["contribution"] is None


# --------------------------------------------------------------------------
# The seed sds: re-derived, provenance pinned, substitution is a parameter
# --------------------------------------------------------------------------


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_the_shipped_seed_sds_are_reproducible_from_the_committed_sweep():
    """SEED_SD is a measurement, and this recomputes it from its source.

    The shipped values come from the post-RNG-split re-run at the shipped
    sigma -- NOT from CALIBRATION.md's table, which was measured pre-split
    and, per the provenance note in facts.py, at the old 0.003 baseline.
    If this fails, either the results file changed (re-derive and update
    SEED_SD and its provenance note together) or SEED_SD was edited by hand
    (do not: it is not a tuning knob).
    """
    derived = seed_sd_from_panels(baseline_panels())
    assert set(derived) == set(SEED_SD)
    for key, value in SEED_SD.items():
        assert derived[key] == pytest.approx(value, rel=1e-4), key


def test_substituting_the_seed_sds_is_a_parameter_not_an_edit():
    # Phase 2 re-estimates on thirty seeds at the new era; the new scales
    # arrive through the keyword, and the result says they are not shipped.
    panel = {key: (low + high) / 2 for key, (low, high) in REAL_MARKETS.items()}
    panel["annualised_vol_pct"] = 45.0  # 10 points above the band
    doubled = {key: sd * 2 for key, sd in SEED_SD.items()}
    shipped = band_distance_loss(panel)
    rescaled = band_distance_loss(panel, seed_sd=doubled)
    assert rescaled["loss"] == pytest.approx(shipped["loss"] / 4)
    assert rescaled["seed_sd_provenance"] == "caller-supplied"


def test_seed_sd_estimation_refuses_thin_or_gappy_panels():
    panel = {key: (low + high) / 2 for key, (low, high) in REAL_MARKETS.items()}
    with pytest.raises(pretium.ValidationError):
        seed_sd_from_panels([panel])
    gappy = [dict(panel), dict(panel, leverage_effect=None)]
    with pytest.raises(pretium.ValidationError):
        seed_sd_from_panels(gappy)


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_a_sequence_of_panels_is_aggregated_by_median():
    # Section 6.1's m_k is the panel MEDIAN -- one seed's excursion must not
    # drag the objective the way a mean would.
    panels = baseline_panels()
    result = band_distance_loss(panels)
    import statistics as st

    for key in LIVE_TARGETS:
        expected = st.median(panel[key] for panel in panels)
        assert result["statistics"][key]["measured"] == pytest.approx(expected)


# --------------------------------------------------------------------------
# The public panel: per-statistic distances, still no aggregate
# --------------------------------------------------------------------------


def test_the_panel_rows_carry_their_band_distances(facts):
    """compare_to_real_markets gains d_k and d_k/s_k, per row.

    Per-statistic fields make the safety property actionable -- anyone can
    see what their preset did to each statistic, in its own units and in
    noise units -- without reintroducing the aggregate the function refuses.
    """
    rows = compare_to_real_markets(facts)
    for key, row in rows.items():
        assert row["band_distance"] >= 0.0
        assert (row["band_distance"] == 0.0) == row["matches"], key
        assert row["scaled_distance"] == pytest.approx(
            row["band_distance"] / SEED_SD[key]
        ), key
    # The trap case, on a real measurement: leverage is absent here, above
    # a negative band, and its distance is to the weak edge.
    leverage = rows["leverage_effect"]
    assert leverage["verdict"] == "too weak"
    assert leverage["band_distance"] == pytest.approx(
        leverage["measured"] - LEVERAGE_BAND[1]
    )


def test_the_loss_is_a_breakdown_with_the_scalar_inside_it(facts):
    """An optimisation device, not a realism score.

    The result is the full eight-row breakdown with the scalar as one field
    of it, so nothing downstream can print "realism: 958.3" without having
    deliberately thrown the structure away. The published artifact stays
    `facts.report`, which still scores nothing.
    """
    result = band_distance_loss(facts)
    assert isinstance(result, dict)
    assert set(result["statistics"]) == set(REAL_MARKETS)
    assert result["loss"] >= 0.0
    text = pretium.facts.report(facts)
    assert "loss" not in text.lower()
    assert "score" not in text.lower()
