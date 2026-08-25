"""The calibration objective: two-sided band distance, diagonally weighted.

Half of this file exists for the statistics whose bands sit at or below
zero. The leverage effect's band is -0.16 to 0.00 and the volume-change
band is -0.32 to -0.20, and a one-sided max(0, value - high) distance
would score a leverage effect of -0.5 -- a large overshoot -- as satisfying
the band. The two-sided form penalises it. The tests here put cases on both
sides of that band, inside it, and on its boundaries, so a future refactor
that quietly reverts to the one-sided form fails HERE rather than silently
inverting the search direction for one statistic while everything else
passes.

The other half pins the two deliberate choices in the loss: membership (the
structurally unreachable statistic is reported but excluded, and promoting
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
# MARKET_FACTOR_SIGMA. The shipped SEED_SD is no longer derived from this
# file (it is re-measured on thirty seeds and pinned by live re-measurement
# below); these panels remain the fixed realistic panel set the membership
# and weighting tests exercise the loss on.
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
    """Both sides of a non-positive band are exits, and both are charged.

    +0.05 (the effect is reversed) sits ABOVE the band and is 0.05 from
    the weak edge at 0.00; -0.5 (the effect is far too strong) sits BELOW
    it and is 0.34 from the strong edge at -0.16. A loss that charges only
    one of them would tell a search that overshooting is free, and the
    search would oblige.
    """
    low, high = LEVERAGE_BAND
    assert (low, high) == (-0.16, 0.00)
    assert band_distance(+0.05, low, high) == pytest.approx(0.05)
    assert band_distance(-0.5, low, high) == pytest.approx(0.34)


def test_an_overshot_leverage_effect_is_not_scored_as_satisfying_the_band():
    """The regression this file exists to catch, stated as its own test.

    Under the one-sided refactor max(0, value - high), every value below
    high scores zero -- so -0.5 would read as inside a band whose strong
    edge is -0.16, and the search direction for this statistic would invert
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
        > band_distance(-0.25, low, high)
        > 0.0
    )


def test_inside_the_leverage_band_and_on_its_boundaries_is_free():
    low, high = LEVERAGE_BAND
    assert band_distance(-0.08, low, high) == 0.0
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


def test_the_loss_covers_nine_statistics_and_reports_ten():
    # Five live targets: lag-5 clustering joined when the instrument found
    # the corner with in-band lag-1 clustering and zero memory behind it.
    assert set(LIVE_TARGETS) == {
        "annualised_vol_pct", "return_acf1", "abs_return_acf1",
        "abs_return_acf5", "cross_sectional_corr",
    }
    # Four constraints: leverage joined when the re-derived band showed the
    # GJR-backed model inside it, and lag-20 clustering is here -- briefly
    # promoted to a live target and reverted, because real markets' own
    # dispersion in it is six times the model's defect and no panel
    # statistic can carry that. See the note in loss.py.
    assert set(CONSTRAINTS) == {
        "excess_kurtosis", "volume_abs_return_corr",
        "leverage_effect", "abs_return_acf20",
    }
    # The structurally unreachable remainder, derived as the complement of
    # the membership tuples so nothing else needs editing when one is
    # promoted.
    assert set(STRUCTURAL) == {"volume_change_acf1"}
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

    # Now drive the volume-change autocorrelation to -0.9 on every panel:
    # a catastrophic exit far past the strong edge of its band. The loss
    # must not move by any amount.
    overshot = [dict(panel, volume_change_acf1=-0.9) for panel in panels]
    assert band_distance_loss(overshot)["loss"] == result["loss"]


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_promoting_a_structural_statistic_is_one_tuple(monkeypatch):
    """Appending a key to LIVE_TARGETS is the whole promotion.

    The seam is no longer hypothetical -- the GJR term promoted leverage
    to a constraint, and the lag-5 finding promoted abs_return_acf5 to a
    live target -- so what this pins is that the seam still works on the
    one structural row left, should volume dynamics ever be modelled.
    Membership is consulted at call time, so the simulated edit here
    exercises the real path rather than a copy of it.
    """
    monkeypatch.setattr(
        pretium.loss, "LIVE_TARGETS",
        pretium.loss.LIVE_TARGETS + ("volume_change_acf1",),
    )
    panels = baseline_panels()
    result = band_distance_loss(panels)
    row = result["statistics"]["volume_change_acf1"]
    assert row["role"] == "live target"
    assert row["contribution"] is not None and row["contribution"] > 0.0
    # And with it live, an overshoot far past the strong edge costs more
    # than the baseline exit does: the search direction the two-sided
    # distance protects.
    overshot = band_distance_loss(
        [dict(panel, volume_change_acf1=-0.9) for panel in panels]
    )
    assert overshot["loss"] > result["loss"]


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_in_band_constraints_contribute_zero_until_a_candidate_breaks_them():
    panels = baseline_panels()
    rows = band_distance_loss(panels)["statistics"]
    for key in CONSTRAINTS:
        assert rows[key]["role"] == "constraint"
        assert rows[key]["contribution"] == 0.0
    # Drive kurtosis under its floor of 1.6 -- toward the Gaussian tails
    # the sigma sweep showed correlation wants to trade for -- and the
    # constraint pushes back.
    broken = band_distance_loss(
        [dict(panel, excess_kurtosis=1.0) for panel in panels]
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
    provenance = result["seed_sd_provenance"]
    assert provenance["seeds"] == tuple(range(101, 131))
    assert provenance["model_fingerprint"] == "pt-v1"
    assert result["panels"] == 6


@pytest.mark.skipif(not RESULTS.exists(), reason="sweep results not present")
def test_noise_scaling_keeps_the_autocorrelations_in_the_objective():
    """The silent choice made loud: unweighted, this loss is a vol objective.

    On these committed panels, pooled volatility exits its band by ~21
    points while return acf(1) exits by ~0.20 -- so unweighted squared
    distances put 99.99% of the loss on volatility and the search would
    never feel an autocorrelation move. In units of each statistic's own
    seed noise the acf exits are of the same order as the vol exit, and
    this asserts they carry a material share.
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
    panel["volume_change_acf1"] = None
    result = band_distance_loss(panel)
    assert result["statistics"]["volume_change_acf1"]["measured"] is None
    assert result["statistics"]["volume_change_acf1"]["contribution"] is None


# --------------------------------------------------------------------------
# The seed sds: re-derived, provenance pinned, substitution is a parameter
# --------------------------------------------------------------------------


# The thirty per-seed panels SEED_SD was measured from: pt-v1,
# Universe.random(40, seed=111), 252 days, seeds 101-130 (the phase-2
# instrument's protocol, so these scales and its Jacobian rows are directly
# comparable). Committed as data, to ten significant figures, so the sd can
# be re-derived here without thirty engine runs per test session; the live
# re-measurement below is what keeps the table honest.
THIRTY_SEED_PANELS = {
    "seeds": tuple(range(101, 131)),
    "annualised_vol_pct": [
        38.18632944, 42.79428246, 37.91946902, 53.26710913, 36.21848504,
        38.23974831, 40.53503957, 49.15559974, 43.70283337, 47.48908576,
        40.503218, 40.17077358, 39.99539251, 65.69412721, 52.16308466,
        45.44135654, 37.32625251, 42.0924504, 37.09347366, 40.39357768,
        47.90369479, 39.54153506, 35.77346285, 37.52072273, 46.73692305,
        48.49823281, 44.01975476, 38.50315844, 42.05540329, 49.11686486],
    "excess_kurtosis": [
        3.638843155, 2.750206646, 3.691982928, 6.549814813, 2.470930107,
        4.526603422, 3.424621754, 3.56712604, 4.33710213, 6.247684829,
        3.506726237, 2.932622762, 2.628140385, 4.965934606, 5.299448644,
        4.304005924, 3.327437839, 2.647639729, 3.32097945, 2.404438587,
        6.154611528, 2.966467248, 3.552254561, 3.987744621, 3.903208315,
        3.743228875, 2.441780311, 3.406791305, 3.421337918, 5.845891151],
    "return_acf1": [
        0.2313842053, 0.2108137332, 0.2442806969, 0.4195189844,
        0.2166049096, 0.1509738007, 0.2521637997, 0.3435540496,
        0.2100231826, 0.264854019, 0.2142301908, 0.288296022,
        0.2105850166, 0.2707716984, 0.2415494118, 0.2238868696,
        0.2000519669, 0.2824383265, 0.2383527516, 0.2614362834,
        0.2504151408, 0.2276572148, 0.2346179263, 0.2419919832,
        0.1672991594, 0.2690075897, 0.2966667889, 0.258518836,
        0.2944942237, 0.3423054977],
    "abs_return_acf1": [
        0.1958030428, 0.2018404476, 0.2016135035, 0.4342557239,
        0.1928664511, 0.247745566, 0.286715099, 0.3624894922,
        0.3451480597, 0.3872881988, 0.1870995103, 0.2871876458,
        0.20782846, 0.4884397791, 0.3905846166, 0.3529686417,
        0.172915549, 0.2274189582, 0.1812526882, 0.2101670658,
        0.4126292712, 0.1744352137, 0.1633961125, 0.1786627806,
        0.3246564945, 0.272913713, 0.258270428, 0.2124093481,
        0.2360955857, 0.4240123276],
    "abs_return_acf5": [
        0.07774000724, 0.09282183288, 0.07900680123, 0.1710625647,
        0.09151436655, 0.08120008499, 0.1689304333, 0.2217903545,
        0.1107904803, 0.1357089124, 0.1322602624, 0.1377420353,
        0.09472076948, 0.2864417876, 0.1162430886, 0.06046582111,
        0.07204755802, 0.08775470148, 0.07632578027, 0.06190197311,
        0.07935966715, 0.08978491717, 0.07043920142, 0.0805173092,
        0.2485882562, 0.143019528, 0.08825796906, 0.1035297665,
        0.1277739877, 0.1151940511],
    "abs_return_acf20": [
        -0.0006382519934, 0.01622618614, 0.01078156949, 0.01133892274,
        0.01626228872, 0.03238826332, 0.005455395667, 0.08436165458,
        -0.01162986262, 0.03151713055, 0.02583235513, 0.006155429226,
        0.01679272317, 0.2432212627, 0.03359856818, 0.001587083811,
        0.03236807214, 0.01712817226, 0.02725002042, -0.005413980642,
        0.01421954004, -0.0002625477135, -0.01024764318, -0.001841854129,
        0.01209027048, -0.001659122215, -0.02316631288, -0.007009005858,
        0.001692402256, 0.007146197969],
    "cross_sectional_corr": [
        0.2060100205, 0.2177915368, 0.2372357545, 0.5094375301,
        0.2075391909, 0.2327121855, 0.2859591948, 0.4126752568,
        0.3516239979, 0.4049271085, 0.2450341758, 0.3297689326,
        0.2353676072, 0.6094568434, 0.4706936069, 0.3879955449,
        0.207093535, 0.2543545418, 0.1882087146, 0.2740375392,
        0.4081702826, 0.2162303014, 0.1932920766, 0.2107149697,
        0.3963129908, 0.3763463048, 0.2994668093, 0.2239769851,
        0.2755966892, 0.4373563567],
    "volume_abs_return_corr": [
        0.5393955165, 0.5329923867, 0.5466777216, 0.6555727095,
        0.5448657956, 0.5629431505, 0.6078316678, 0.6275541649,
        0.6139085997, 0.6117802256, 0.5678888058, 0.627548682,
        0.5596018978, 0.7081447219, 0.6665751576, 0.605969972,
        0.5494346141, 0.5485762944, 0.5623274173, 0.5612633445,
        0.598294934, 0.5554788398, 0.5682736471, 0.5563002011,
        0.6077100054, 0.629320838, 0.5781123674, 0.5500933134,
        0.5672240227, 0.6371868647],
    "leverage_effect": [
        -0.05375339193, -0.0347317908, -0.07161572446, -0.09693569254,
        -0.06928597907, -0.09611163537, -0.1318609868, 0.06024649991,
        0.02211485756, -0.06761333579, -0.06803516368, 0.02751296276,
        -0.05766917908, 0.1312723149, 0.06302306272, -0.01091343115,
        -0.08783164932, -0.0984120263, -0.06775374452, -0.1342227912,
        -0.2503385533, -0.1267541439, -0.07732706389, -0.06924930498,
        0.02158365617, -0.09922276958, -0.1125201214, -0.104548374,
        -0.02255939527, 0.05535548513],
    "volume_change_acf1": [
        -0.4398052046, -0.4541903519, -0.4525321339, -0.4467006431,
        -0.4526913981, -0.4681299565, -0.4524711848, -0.4546922165,
        -0.42430572, -0.4464246704, -0.4440773052, -0.4673796964,
        -0.4459517493, -0.4477442446, -0.4599886679, -0.4406453228,
        -0.4348848643, -0.4448791891, -0.4506027005, -0.4511965671,
        -0.4381135315, -0.4582469152, -0.4477784396, -0.4465225235,
        -0.4755706407, -0.4503070863, -0.4447755028, -0.4492209396,
        -0.4600876586, -0.4571319669],
}


def test_the_shipped_seed_sds_are_reproducible_by_re_measurement():
    """SEED_SD is a measurement, and this recomputes it from its protocol.

    The engine is deterministic per seed, so the provenance can be pinned
    to live re-measurement rather than to a committed artifact: two of the
    thirty seeds are re-run end to end here (the cheapest check that the
    model, the universe and the estimator are still the ones the constants
    were measured on -- a changed coefficient or RNG stream fails the
    panel comparison before it could silently reprice the loss), and the
    shipped sd is re-derived from the committed per-seed table those live
    panels must match. If this fails, the model or measurement changed:
    re-measure all thirty seeds and update SEED_SD, its provenance and
    this table together. Do not edit SEED_SD by hand: it is not a tuning
    knob.
    """
    import statistics as st

    universe = pretium.Universe.random(40, seed=111)
    for seed in (101, 130):
        position = THIRTY_SEED_PANELS["seeds"].index(seed)
        # pt-v1 NAMED, not the default. SEED_SD is a noise scale measured
        # once on pt-v1 and deliberately frozen there: it is the
        # denominator of every "room in seed-sds" figure this project has
        # published, and re-deriving it at each era boundary would silently
        # restate every one of them. The pt-v3 boundary is exactly where
        # that would have happened, because this line used to read the
        # default.
        panel = measure(seed=seed, universe=universe, days=252, model="pt-v1")
        assert panel["model_fingerprint"] == "pt-v1"
        for key in SEED_SD:
            assert panel[key] == pytest.approx(
                THIRTY_SEED_PANELS[key][position], rel=1e-6, abs=1e-9
            ), (seed, key)
    for key, shipped in SEED_SD.items():
        derived = st.stdev(THIRTY_SEED_PANELS[key])
        assert derived == pytest.approx(shipped, rel=1e-4), key


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
    # The trap case: a leverage effect ABOVE its non-positive band must
    # read "too weak" with its distance to the weak edge at 0.00. The live
    # fixture stopped exercising it -- the GJR term made the effect real,
    # and the re-derived band contains this fixture's reading -- so the
    # trap is pinned on a weakened copy of the same panel: the wording
    # rule has to survive the statistic being healthy.
    weakened = dict(facts)
    weakened["leverage_effect"] = +0.05
    leverage = compare_to_real_markets(weakened)["leverage_effect"]
    assert leverage["verdict"] == "too weak"
    assert leverage["band_distance"] == pytest.approx(
        0.05 - LEVERAGE_BAND[1]
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
