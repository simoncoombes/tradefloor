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

import tradefloor
from tradefloor.facts import (
    REAL_MARKETS,
    SEED_SD,
    SEED_SD_LEVEL_PROVENANCE,
    band_distance,
    compare_to_real_markets,
    fingerprint_of,
    measure,
)
from tradefloor.loss import (
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


#: The roster the pinned panels below were measured on, as committed
#: day-zero instrument data rather than as a call into the generator.
#:
#: The generator is not what this file tests. `THIRTY_SEED_PANELS` pins what
#: one MODEL produces on one roster, and reading the roster from
#: `Universe.random` made those panels hostage to a component they were
#: never measuring: the universe generator was reconciled so a drawn roster
#: opens at its own fair value, every roster re-rolled, and all fourteen
#: pinned values went stale at once on a change that touched no coefficient
#: and no estimator. The roster fingerprinted 5d8de78b55aad752 when the
#: table was measured and fingerprints 9be68b9bc37e7978 now.
#:
#: What the file still catches is unchanged, because only the roster left
#: the dependency chain. A moved coefficient, a moved generator stream and
#: a moved estimator each change what `measure` returns on a roster held
#: fixed, so each still fails the live half below. What it stops reporting
#: is a re-rolled roster, which was never its subject.
ROSTER_FIXTURE = Path(__file__).parent / "fixtures" / "panel-roster-40.json"

#: The fixture's own fingerprint, so a corrupted or truncated file fails
#: here rather than silently measuring a different market. Pinned against
#: the fixture and NOT against the generator, since re-deriving it from
#: `Universe.random` would put back the dependency this removes.
ROSTER_FINGERPRINT = (
    "9be68b9bc37e79785765df2f395a9348650a4e9293507680532293fdf78808dd"
)


def panel_roster():
    """The committed roster, rebuilt from its day-zero instrument data."""
    universe = tradefloor.Universe.from_json(
        ROSTER_FIXTURE.read_text(encoding="utf-8"))
    assert fingerprint_of(list(universe)) == ROSTER_FINGERPRINT, (
        "tests/fixtures/panel-roster-40.json does not describe the roster "
        "the panels below were measured on")
    return universe


# Same roster and length as test_facts, for the same estimator reasons.
UNIVERSE = panel_roster()


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
    check `tradefloor.facts.band_distance` for exactly that form.
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
    from tradefloor.facts import _verdict

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


def test_the_loss_covers_nine_statistics_and_reports_fifteen():
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
    # Structural now also holds the three conditional correlation statistics
    # added 2026-08-25. They enter here by default, outside the objective,
    # until each has been shown to be something a lever can move; promoting
    # one is a decision recorded in loss.py, not a side effect of measuring it.
    # The level row `index_drift_pct` is structural for now and is the one
    # row here meant to leave: it joins the live targets when SEED_SD
    # carries its seed sd on the pinned protocol, so that a search charges
    # for the level it moves. Until then it is reported and not summed.
    assert set(STRUCTURAL) == {
        "volume_change_acf1", "corr_asymmetry", "corr_asymmetry_lagged",
        "sector_excess_corr", "corr_persistence_acf1", "index_drift_pct",
        "fear_gauge_dn1", "fear_gauge_dn3",
    }
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
    # The committed sweep artifact predates the three conditional correlation
    # statistics, so those rows carry no distance here. The two structural
    # rows that are out of band on the shipped model must read as such.
    assert rows["volume_change_acf1"]["distance"] > 0.0
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
        tradefloor.loss, "LIVE_TARGETS",
        tradefloor.loss.LIVE_TARGETS + ("volume_change_acf1",),
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
    # form the API offers, deliberately.
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
    with pytest.raises(tradefloor.ValidationError):
        band_distance_loss(panel, seed_sd=missing)
    degenerate = dict(SEED_SD, return_acf1=0.0)
    with pytest.raises(tradefloor.ValidationError):
        band_distance_loss(panel, seed_sd=degenerate)


def test_a_live_statistic_that_could_not_be_measured_refuses():
    # If a candidate breaks a statistic's measurability, "contributes zero"
    # would make unmeasurable an attractive direction for the search.
    panel = {key: (low + high) / 2 for key, (low, high) in REAL_MARKETS.items()}
    panel["cross_sectional_corr"] = None
    with pytest.raises(tradefloor.ValidationError):
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
        38.13105584, 42.62003743, 37.84787998, 53.2059933, 36.22769618,
        38.15346413, 40.38035763, 49.0733938, 43.62725438, 47.41991402,
        40.30596765, 40.18698882, 39.94496737, 65.63394852, 51.97417429,
        45.37580433, 37.27104918, 42.08878572, 37.00361452, 40.34859632,
        47.9051766, 39.51425527, 35.73373937, 37.45765671, 46.5820262,
        48.35798633, 43.89508608, 38.30033811, 41.87950223, 49.01905173],
    "excess_kurtosis": [
        3.612888656, 2.748534998, 3.668334698, 6.58701944, 2.498845867,
        4.568387835, 3.454967948, 3.549206065, 4.359564161, 6.248260109,
        3.569749631, 2.9153075, 2.650052042, 4.969915442, 5.320722763,
        4.329546059, 3.346373914, 2.692976892, 3.332279017, 2.472453935,
        6.17168614, 2.974538517, 3.574522587, 4.005672184, 3.910043366,
        3.736085389, 2.418453339, 3.380112932, 3.429857791, 5.940251987],
    "return_acf1": [
        0.231692224, 0.2129750635, 0.2381443862, 0.4098778892, 0.2145185234,
        0.1483431853, 0.2520344559, 0.3407986237, 0.1937985748, 0.271896582,
        0.2179151112, 0.2946836053, 0.216963995, 0.2661987872, 0.2415870818,
        0.2238132543, 0.203654859, 0.2819744843, 0.238900599, 0.258148517,
        0.2526596822, 0.2299275916, 0.2324934883, 0.2458268633, 0.1656659369,
        0.2668691374, 0.296988669, 0.2671626381, 0.2977995193, 0.3392485346],
    "abs_return_acf1": [
        0.2026048121, 0.2013740022, 0.1976643786, 0.4346389254, 0.1882275973,
        0.250311008, 0.281379882, 0.3739016666, 0.3554834864, 0.3841250065,
        0.1857737793, 0.2922875238, 0.2093993041, 0.5040197051, 0.3943579881,
        0.3488343368, 0.1746247876, 0.2338289935, 0.1872732804, 0.219601895,
        0.4089440771, 0.1651221593, 0.1675753479, 0.1907666267, 0.3280192777,
        0.2768732283, 0.2517504974, 0.2176892788, 0.2170736263, 0.4176756935],
    "abs_return_acf5": [
        0.06894233179, 0.09199425023, 0.07321505308, 0.1749632809, 0.09758656306,
        0.08912778437, 0.1741348602, 0.2284449957, 0.1070754636, 0.1366085762,
        0.1310171386, 0.1437065222, 0.09369870309, 0.2881347051, 0.1111167513,
        0.05603797877, 0.06946408806, 0.08363817702, 0.08141510373, 0.06297642633,
        0.07187735695, 0.08361931958, 0.07082738507, 0.08514259741, 0.2492208144,
        0.1491141606, 0.09057678671, 0.1033564534, 0.1262507967, 0.1182148318],
    "abs_return_acf20": [
        0.005488901424, 0.02272547476, 0.01820281187, 0.01320307966, 0.02232001578,
        0.02960182036, 0.007749938163, 0.0783842746, -0.01260589597, 0.03098749784,
        0.02844924575, 0.008694997644, 0.01181469142, 0.2463942304, 0.03556092954,
        0.0136407183, 0.04628782275, 0.02269892963, 0.029758941, -0.006670081475,
        0.01644998059, -0.002941668537, 0.002385410342, 0.0003557631723, 0.01426462986,
        -0.007641456557, -0.02048962427, -0.006772069648, -0.0009767554565, 0.008822913636],
    "cross_sectional_corr": [
        0.2065845572, 0.2212371058, 0.2390215653, 0.510068563, 0.2092590986,
        0.2372113357, 0.287321622, 0.415541564, 0.355654591, 0.4072235343,
        0.2472077657, 0.3305706912, 0.2370985616, 0.6093591507, 0.4722220028,
        0.3891439049, 0.2105140324, 0.2543563034, 0.1885312887, 0.2759571865,
        0.407831365, 0.2177773014, 0.1947024068, 0.2119323622, 0.3973002747,
        0.3776042709, 0.2991213788, 0.2262070873, 0.2761382074, 0.4388711032],
    "volume_abs_return_corr": [
        0.5432209498, 0.5284253122, 0.5530857764, 0.6490341763, 0.5471915818,
        0.5780955025, 0.6106518375, 0.6274443565, 0.6158715979, 0.6110636219,
        0.566922488, 0.6188088516, 0.569735706, 0.7029729654, 0.6641143698,
        0.6011969977, 0.5453100456, 0.5467354455, 0.569986869, 0.5593595011,
        0.6049816275, 0.5597228556, 0.567360681, 0.5541046294, 0.6225989392,
        0.6281514014, 0.5745665806, 0.5535180421, 0.5707966384, 0.6307872303],
    "leverage_effect": [
        -0.04774969067, -0.03427921163, -0.0785969604, -0.1069219928, -0.0789327849,
        -0.1062182481, -0.1254517008, 0.0679557079, 0.028062552, -0.06555448623,
        -0.05799571904, 0.01387820037, -0.07282539442, 0.135279838, 0.05372229606,
        -0.009922274446, -0.08045746446, -0.1093615983, -0.0849367981, -0.1328589784,
        -0.2454668273, -0.1117165344, -0.07732000289, -0.06629606055, 0.02494021607,
        -0.101594925, -0.1140634482, -0.1160848523, -0.01490119024, 0.06489236468],
    "volume_change_acf1": [
        -0.4328511491, -0.4498959323, -0.4477568732, -0.454353961, -0.4508209319,
        -0.4686090244, -0.4564303958, -0.4581826252, -0.4207901013, -0.4539712707,
        -0.4413851603, -0.4523436078, -0.4449707932, -0.4490063076, -0.4612559012,
        -0.4322401736, -0.4375998227, -0.4387663742, -0.4527291433, -0.4477042211,
        -0.4334755679, -0.4523533349, -0.4471265544, -0.4413889793, -0.4705032121,
        -0.4412554709, -0.4452554364, -0.4400271345, -0.4504962278, -0.4575765377],
    "corr_asymmetry": [
        -0.02500499896, -0.0130533321, 0.05506721695, 0.1207931263, -0.05929369554,
        0.05941275897, 0.05624237293, -0.0391725452, -0.3297954067, 0.2451592757,
        0.001255269354, -0.2883356429, -0.1035286947, -0.2074189273, -0.1877061932,
        -0.155387316, -0.005850766419, -0.06658656877, 0.02190626323, 0.0208426393,
        0.4204702582, -0.04961709222, -0.00831015242, -0.008173129882, -0.01183671648,
        0.1190556439, 0.05691087609, -0.007650900542, -0.1770076286, -0.4191977857],
    "corr_asymmetry_lagged": [
        -0.1114529922, -0.09580392144, -0.04613153656, -0.01938098231, -0.1288511586,
        -0.1423378079, 0.04145401144, -0.3398147648, -0.1741386959, -0.2774486269,
        -0.0676858313, -0.2905281568, -0.02692512343, -0.1694193771, -0.08021634282,
        -0.2504488142, -0.07430118232, 0.001552731414, -0.04660418366, -0.0005591480077,
        0.2391147801, -0.01518196884, -0.04752511711, -0.04000214096, -0.2083029501,
        -0.03931338844, 0.002790934043, -0.08702551158, -0.08291358646, -0.2230892106],
    "sector_excess_corr": [
        -0.008811840618, -0.01325234941, 0.00552986737, -0.01058210978, -0.002494446641,
        -0.0009118186611, -0.01161631157, -0.005593666192, -0.003502962232, -0.004792523036,
        -0.006412359257, -0.01250834462, -0.007565606885, -0.01185684478, -0.01169223811,
        -0.003939876981, -0.003221444752, -0.003288713667, 0.002393589047, 0.006923686013,
        -0.007253660924, -0.005828777988, -0.005956335547, -0.01564996377, -0.005157351487,
        -0.01365928451, -0.003040723628, 0.0106730778, 0.003959594081, -0.00770167475],
    "corr_persistence_acf1": [
        -0.3182233312, 0.01679693675, 0.2291315986, -0.1496177278, -0.3213158597,
        -0.3694646385, -0.3237142947, 0.6071671167, 0.2441630361, 0.4067875171,
        0.083950604, -0.1614237413, 0.04397495609, 0.4883383236, 0.07062437765,
        0.2803643228, 0.1588789216, -0.0002432821278, -0.2817546301, -0.3486498923,
        0.06751704843, 0.09322538522, -0.5099551626, -0.03227377743, 0.1801109718,
        -0.2715167067, -0.2058593593, -0.1890258437, 0.2687012725, 0.1884511226],
}

# The thirty per-seed panels the level row's SEED_SD entry was measured
# from, on its own protocol: pt-v1, Universe.random(40, seed=s) with market
# seed s, 252 days, seeds 101-130 (facts.LEVEL_PROTOCOL), the pt-v1 arm of
# the box run era-level of 2026-09-04 at 6326337. Committed as data for the
# same reason as the table above; the live re-measurement below is what
# keeps it honest.
THIRTY_SEED_LEVEL_PANELS = {
    "seeds": tuple(range(101, 131)),
    "index_drift_pct": [
        6.555978522, 8.769086434, 10.32963337, -1.437036827, -20.460801,
        -1.722901744, 6.035332724, -1.050153126, 1.90984402, 2.755458971,
        1.501244373, 13.29726623, 10.90805558, 5.411631874, -10.07679501,
        -5.771630368, -8.114883963, -8.460163246, -6.406017545, -22.06291507,
        -4.812104226, -4.085363997, 3.770262104, -1.882990479, 2.040531268,
        -5.531987442, 1.644564451, -4.599167669, 25.44166539, 7.678302214],
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

    universe = panel_roster()
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
            if key in SEED_SD_LEVEL_PROVENANCE["rows"]:
                continue  # its own protocol; the test below re-derives it
            # The tolerance is a FLOOR rather than slack, and tightening it
            # breaks this suite on a machine that is not the one the table
            # was measured on. The market is bit-reproducible across
            # platforms and the statistics derived from it are not: the same
            # panel computed under CPython 3.11.16 on Linux and 3.13.12 on
            # Windows differs by up to 8.4e-15 relative on excess kurtosis,
            # while all three known-answer digests match on both. Within one
            # interpreter the paths agree to the bit, which was checked
            # directly.
            #
            # Nowhere else in this repository carries that floor, so a
            # reader who finds this loose does not have to go looking. The
            # determinism gate compares digests rather than statistics and
            # is exact, correctly, because the known-answer buffer holds
            # engine output and no derived statistic. The preset record test
            # compares two committed artefacts, so no machine differs from
            # another. Every other approximate comparison over a measurement
            # computes both sides in one process. This assertion is the only
            # one with a live engine run on one side and a committed number
            # on the other, which is the shape that can carry the floor.
            assert panel[key] == pytest.approx(
                THIRTY_SEED_PANELS[key][position], rel=1e-6, abs=1e-9
            ), (seed, key)
    for key, shipped in SEED_SD.items():
        if key in SEED_SD_LEVEL_PROVENANCE["rows"]:
            continue
        derived = st.stdev(THIRTY_SEED_PANELS[key])
        assert derived == pytest.approx(shipped, rel=1e-4), key


def test_the_level_rows_seed_sd_is_reproducible_on_its_own_protocol():
    """The level row's SEED_SD entry, recomputed the way it was measured.

    The entry is on `facts.LEVEL_PROTOCOL`, the roster drawn per seed, so
    the test above cannot cover it: it holds the panel roster. Two of the
    thirty seeds are re-run live on `Universe.random(40, seed=s)` at pt-v1
    and held to the committed per-seed table, and the sd is re-derived
    from that table. The first assertion is the one that matters when a
    row is added: every SEED_SD entry is re-derived by this test or the
    one above, so a new entry has to be placed in one of the two tables
    and cannot slip between them.
    """
    import statistics as st

    from tradefloor.facts import LEVEL, LEVEL_PROTOCOL

    level_rows = {k for k in THIRTY_SEED_LEVEL_PANELS if k != "seeds"}
    held_rows = {k for k in THIRTY_SEED_PANELS if k != "seeds"}
    assert set(SEED_SD) == held_rows | level_rows
    assert held_rows.isdisjoint(level_rows)
    assert set(SEED_SD_LEVEL_PROVENANCE["rows"]) == level_rows <= set(LEVEL)
    assert THIRTY_SEED_LEVEL_PANELS["seeds"] == LEVEL_PROTOCOL["seeds"]
    for seed in (101, 130):
        position = THIRTY_SEED_LEVEL_PANELS["seeds"].index(seed)
        universe = tradefloor.Universe.random(40, seed=seed)
        panel = measure(seed=seed, universe=universe,
                        days=LEVEL_PROTOCOL["days"], model="pt-v1")
        assert panel["model_fingerprint"] == "pt-v1"
        for key in level_rows:
            # The same floor as the test above, for the same reason.
            assert panel[key] == pytest.approx(
                THIRTY_SEED_LEVEL_PANELS[key][position], rel=1e-6, abs=1e-9
            ), (seed, key)
    for key in level_rows:
        derived = st.stdev(THIRTY_SEED_LEVEL_PANELS[key])
        assert derived == pytest.approx(SEED_SD[key], rel=1e-4), key


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
    with pytest.raises(tradefloor.ValidationError):
        seed_sd_from_panels([panel])
    gappy = [dict(panel), dict(panel, leverage_effect=None)]
    with pytest.raises(tradefloor.ValidationError):
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
        if key not in SEED_SD:
            # The level row has no seed sd until it is measured on the
            # pinned protocol, so its distance is reported in its own units
            # and not in noise units.
            assert row["scaled_distance"] is None, key
            continue
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
    text = tradefloor.facts.report(facts)
    assert "loss" not in text.lower()
    assert "score" not in text.lower()
