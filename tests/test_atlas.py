"""The atlas: sampling, sensitivity, marginals, the frontier, attribution.

Most tests here run on synthetic surveys -- axes over the unit interval and
outputs that are known functions of them -- because every analysis method's
correctness question ("is this front right?", "does a curved monotone
relationship read as strong?") has a hand-checkable answer there and none
anywhere else. The driver-facing tests at the bottom guard the survey
configuration in `tools/calibration/atlas_survey.py` against the specific
box mistake that wasted the most recent 96-core run.
"""

import json
import math

import pytest

import pretium
from pretium import atlas
from pretium.atlas import Axis, Survey


def unit_axes(*names):
    return [Axis(name, 0.0, 1.0) for name in names]


def synthetic(fn, names=("x1", "x2", "x3"), samples=240, seed=7):
    """A survey of `fn(vector) -> outputs` over unit axes, no noise added:
    what the analysis reads is exactly what the function put there."""
    return atlas.survey(unit_axes(*names), fn, samples, seed=seed)


# -- sampling ---------------------------------------------------------------

def test_the_hypercube_is_actually_stratified_on_every_axis():
    """The property the whole design leans on: one point per stratum per
    axis, so a few thousand points stay informative in fifty dimensions.
    A merely-uniform sample would pass a histogram test and still clump."""
    n = 64
    points = atlas.latin_hypercube(n, 3, seed=11)
    for dim in range(3):
        strata = sorted(int(p[dim] * n) for p in points)
        assert strata == list(range(n)), f"axis {dim} missed a stratum"


def test_the_shuffle_is_a_real_permutation_not_a_bystander():
    """Stratification is invariant under ANY permutation, so the test
    above passes with the Fisher-Yates loop deleted outright -- identity
    permutation, perfectly correlated columns -- and with a Sattolo
    off-by-one. Found by review; this pins the shuffle itself.

    Two properties, each killing a specific breakage:

    - Fixed points. A uniform permutation of 32 strata has one fixed
      point in expectation, so 60 independent draws have ~60. The
      identity has 32 EVERY time (1,920 total); Sattolo's variant has
      exactly zero, always. Both land far outside [10, 150].
    - Column independence. Two axes' rank columns must be uncorrelated;
      the deleted-shuffle failure makes them identical (r = 1).
    """
    total_fixed = 0
    for seed in range(60):
        points = atlas.latin_hypercube(32, 1, seed)
        strata = [int(p[0] * 32) for p in points]
        total_fixed += sum(1 for i, s in enumerate(strata) if i == s)
    assert 10 <= total_fixed <= 150, total_fixed

    points = atlas.latin_hypercube(2000, 2, seed=17)
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = math.sqrt(sum((x - mx) ** 2 for x in xs)
                    * sum((y - my) ** 2 for y in ys))
    assert abs(num / den) < 0.1, "columns must be independent draws"


def test_the_plan_is_reproducible_from_its_seed_alone():
    # A survey that cannot be re-derived from (axes, samples, seed) is a
    # measurement nobody can check. GameRng is used for exactly this.
    axes = [Axis("a", 0.0, 10.0), Axis("b", 0.1, 1000.0, log=True)]
    first = atlas.plan(axes, 32, seed=5)
    again = atlas.plan(axes, 32, seed=5)
    other = atlas.plan(axes, 32, seed=6)
    assert first == again
    assert first != other
    for vector in first:
        assert 0.0 <= vector["a"] <= 10.0
        assert 0.1 <= vector["b"] <= 1000.0


def test_a_log_axis_spreads_points_across_decades_not_the_top_one():
    axis = Axis("s", 0.1, 1000.0, log=True)
    values = [axis.at(u / 100.0) for u in range(101)]
    below_one = sum(1 for v in values if v < 1.0)
    # Linear sampling would put ~0.09% of points below 1.0; log sampling
    # puts a quarter of them there (one decade of four).
    assert below_one >= 20
    assert axis.at(0.5) == pytest.approx(math.sqrt(0.1 * 1000.0))
    assert axis.unit(axis.at(0.37)) == pytest.approx(0.37)


def test_a_tiny_survey_is_refused_not_averaged():
    with pytest.raises(pretium.ValidationError):
        atlas.plan(unit_axes("x"), 4)


def test_axis_validation_refuses_an_empty_or_impossible_range():
    with pytest.raises(pretium.ValidationError):
        Axis("x", 1.0, 1.0)
    with pytest.raises(pretium.ValidationError):
        Axis("x", 0.0, 1.0, log=True)  # zero is infinitely far in log space


# -- axes_for ---------------------------------------------------------------

def test_the_default_box_is_a_quarter_to_four_times_the_preset():
    (axis,) = atlas.axes_for(["market_factor_sigma"], preset="pt-v3")
    ship = pretium.ModelParams.from_preset("pt-v3").market_factor_sigma
    assert axis.low == pytest.approx(ship * 0.25)
    assert axis.high == pytest.approx(ship * 4.0)


def test_a_zero_shipped_parameter_is_refused_rather_than_guessed():
    """Nineteen parameters ship inert at 0.0. A multiplicative box around
    zero is the single point zero, and inventing a range instead would
    decide -- silently, in library code -- what the map can see."""
    with pytest.raises(pretium.ValidationError):
        atlas.axes_for(["jump_intensity_market"], preset="pt-v3")
    # With an explicit range the same parameter is fine.
    (axis,) = atlas.axes_for(["jump_intensity_market"], preset="pt-v3",
                             ranges={"jump_intensity_market": (0.0, 0.25)})
    assert (axis.low, axis.high) == (0.0, 0.25)


def test_a_typoed_range_override_is_refused_not_silently_dropped():
    """The crisisearch4 shape of failure with worse ergonomics: a caller
    who widens a box under a misspelled name would otherwise get the
    default box, silently, and believe the optimum was searched."""
    with pytest.raises(pretium.ValidationError):
        atlas.axes_for(["crisis_blend_ramp"], preset="pt-v3",
                       ranges={"crisis_blend_rampp": (0.35, 50.0)})
    with pytest.raises(pretium.ValidationError):
        atlas.axes_for(["crisis_blend_ramp"], preset="pt-v3",
                       log=["crisis_blend_rampp"])


def test_an_unknown_parameter_and_a_duplicate_are_refused():
    with pytest.raises(pretium.ValidationError):
        atlas.axes_for(["sharpe_ratio"], preset="pt-v3")
    with pytest.raises(pretium.ValidationError):
        atlas.axes_for(["garch_alpha", "garch_alpha"], preset="pt-v3")


def test_a_range_excluding_the_shipped_value_warns_out_loud():
    # Legal -- surveying a far region deliberately is a real use -- but a
    # survey that cannot see the running model deserves a said-out-loud.
    with pytest.warns(UserWarning, match="excludes the pt-v3 value"):
        atlas.axes_for(["crisis_blend_ramp"], preset="pt-v3",
                       ranges={"crisis_blend_ramp": (8.0, 50.0)})


# -- recording and the error path ------------------------------------------

def test_a_vector_that_breaks_the_model_is_recorded_not_fatal():
    """A sweep once lost everything to a late failure; a region that
    raises is a fact about the model and must cost one row, not the run."""
    def measure(v):
        if v["x1"] > 0.8:
            raise pretium.ValidationError("no instrument produced returns")
        return {"y": v["x1"]}

    result = synthetic(measure, samples=100, seed=3)
    assert len(result.rows) == 100, "an error must not shorten the table"
    errors = result.errors()
    assert errors and all("ValidationError" in r["error"] for r in errors)
    # Stratification makes the count exact: x1 > 0.8 is a fifth of strata.
    assert len(errors) == 20
    sens = result.sensitivity("y")
    assert sens["rows_used"] == 80
    assert sens["rows_error"] == 20


def test_the_survey_measures_exactly_the_vectors_it_planned():
    # `plan` exists so a caller can feasibility-check before spending;
    # that only means something if `survey` then measures the same points.
    axes = unit_axes("x1", "x2")
    planned = atlas.plan(axes, 16, seed=9)
    result = atlas.survey(axes, lambda v: {"y": v["x1"]}, 16, seed=9)
    assert [r["parameters"] for r in result.rows] == planned
    assert [r["index"] for r in result.rows] == list(range(16))


def test_record_refuses_ambiguous_and_malformed_rows():
    s = Survey(axes=unit_axes("x1"))
    with pytest.raises(pretium.ValidationError):
        s.record(0, {"x1": 0.5})  # neither outputs nor error
    with pytest.raises(pretium.ValidationError):
        s.record(0, {"x1": 0.5}, outputs={"y": 1.0}, error="also broken")
    with pytest.raises(pretium.ValidationError):
        s.record(0, {"wrong": 0.5}, outputs={"y": 1.0})
    with pytest.raises(pretium.ValidationError):
        s.record(0, {"x1": 0.5, "extra": 1.0}, outputs={"y": 1.0})


# -- sensitivity ------------------------------------------------------------

def test_a_curved_monotone_response_reads_at_full_strength():
    """The reason Spearman and not Pearson: these responses saturate, and
    the strongly-saturating driver is exactly the one a linear measure
    would under-rank. Under ranks, any strict monotone reads 1.0."""
    result = synthetic(lambda v: {"y": math.tanh(8.0 * (v["x1"] - 0.5))})
    sens = result.sensitivity("y")
    assert sens["correlations"]["x1"] == pytest.approx(1.0)
    assert abs(sens["correlations"]["x2"]) < 0.2
    assert abs(sens["correlations"]["x3"]) < 0.2
    assert list(sens["correlations"])[0] == "x1", "ranked by |rho|"
    # The provenance the numbers must not travel without.
    assert sens["rows_used"] == 240
    assert sens["rows_total"] == 240


def test_an_unmeasured_output_name_is_refused_as_a_probable_typo():
    # An empty answer to a misspelled output would read as a finding.
    result = synthetic(lambda v: {"y": v["x1"]})
    with pytest.raises(pretium.ValidationError, match="no row measures"):
        result.sensitivity("yy")
    with pytest.raises(pretium.ValidationError):
        result.pareto({"yy": "min"})


def test_too_few_usable_rows_are_refused_not_correlated():
    axes = unit_axes("x1")
    s = Survey(axes=axes)
    for i in range(6):
        s.record(i, {"x1": i / 6.0}, outputs={"y": float(i)})
    with pytest.raises(pretium.ValidationError):
        s.sensitivity("y")


def test_where_asks_the_targeted_interaction_question():
    """A parameter that acts only in combination reads near zero on the
    full sample -- the documented blind spot -- and `where=` is the one-
    hypothesis-at-a-time way to see it without an all-pairs noise dredge."""
    result = synthetic(
        lambda v: {"y": (v["x1"] - 0.5) * (v["x2"] - 0.5)}, samples=400)
    marginal = result.sensitivity("y")["correlations"]["x1"]
    assert abs(marginal) < 0.15, "the pure interaction hides from a marginal"
    high = result.sensitivity("y", where={"x2": (0.5, 1.0)})
    low = result.sensitivity("y", where={"x2": (0.0, 0.5)})
    assert high["correlations"]["x1"] > 0.5
    assert low["correlations"]["x1"] < -0.5
    assert high["rows_used"] + low["rows_used"] == 400
    with pytest.raises(pretium.ValidationError):
        result.sensitivity("y", where={"x9": (0.0, 0.5)})


def test_unidentified_lists_the_parameters_a_search_would_waste():
    result = synthetic(lambda v: {"y": v["x1"] ** 3})
    assert result.unidentified(["y"]) == ["x2", "x3"]
    # The threshold is explicit when passed; nothing clears 1.1.
    assert result.unidentified(["y"], threshold=1.1) == ["x1", "x2", "x3"]


# -- profile ----------------------------------------------------------------

def test_profile_bins_are_exactly_uniform_under_stratified_sampling():
    """Latin hypercube stratification makes the bin counts exact, not just
    near-uniform -- so a lopsided count in a real survey is a signal
    (dropped rows, a filter), never sampling luck."""
    result = synthetic(lambda v: {"y": v["x1"]}, samples=240)
    prof = result.profile("x1", "y", bins=12)
    assert [b["n"] for b in prof["bins"]] == [20] * 12
    # And the marginal of the identity is the identity, within its bin.
    for b in prof["bins"]:
        assert b["low"] <= b["median"] <= b["high"]
        assert b["p10"] <= b["median"] <= b["p90"]


def test_profile_bins_a_log_axis_in_its_own_geometry():
    # Equal-width linear bins over a log-sampled axis would put almost
    # every row in the first bin and read as a sampling bug.
    axes = [Axis("s", 0.1, 1000.0, log=True)]
    result = atlas.survey(axes, lambda v: {"y": v["s"]}, 120, seed=2)
    prof = result.profile("s", "y", bins=12)
    assert [b["n"] for b in prof["bins"]] == [10] * 12
    assert prof["log"] is True


def test_a_marginal_too_thin_for_its_bins_is_refused():
    result = synthetic(lambda v: {"y": v["x1"]}, samples=30)
    with pytest.raises(pretium.ValidationError):
        result.profile("x1", "y", bins=12)


def test_profile_counts_an_off_axis_value_instead_of_clamping_it():
    # `record` and `load` accept rows from anywhere, so an off-axis value
    # is reachable; clamping it into an edge bin (the old behaviour)
    # silently moved a measurement to a place it was not taken.
    result = synthetic(lambda v: {"y": v["x1"]}, names=("x1",), samples=48)
    result.record(48, {"x1": 5.0}, outputs={"y": 5.0})
    prof = result.profile("x1", "y", bins=12)
    assert prof["rows_outside_range"] == 1
    assert sum(b["n"] for b in prof["bins"]) == 48, "not smuggled into a bin"


# -- pareto -----------------------------------------------------------------

def hand_survey(points):
    s = Survey(axes=unit_axes("x1"))
    for i, (a, b) in enumerate(points):
        s.record(i, {"x1": i / max(len(points), 2)},
                 outputs={"a": a, "b": b})
    return s


def test_the_pareto_front_is_right_on_a_hand_checkable_case():
    s = hand_survey([(1.0, 1.0), (2.0, 0.0), (0.0, 2.0),
                     (1.5, 1.5), (2.0, 2.0)])
    result = s.pareto({"a": "min", "b": "min"})
    front = {(r["outputs"]["a"], r["outputs"]["b"]) for r in result["front"]}
    assert front == {(1.0, 1.0), (2.0, 0.0), (0.0, 2.0)}
    assert result["dominated"] == 2
    assert result["rows_considered"] == 5


def test_the_front_respects_a_max_direction():
    s = hand_survey([(1.0, 1.0), (2.0, 0.0), (0.0, 2.0),
                     (1.5, 1.5), (2.0, 2.0)])
    result = s.pareto({"a": "min", "b": "max"})
    front = {(r["outputs"]["a"], r["outputs"]["b"]) for r in result["front"]}
    assert front == {(0.0, 2.0)}, "one point is best on both axes at once"
    with pytest.raises(pretium.ValidationError):
        s.pareto({"a": "minimise"})


def test_tied_rows_share_the_front_and_errored_rows_never_reach_it():
    s = hand_survey([(1.0, 1.0), (1.0, 1.0)])
    s.record(2, {"x1": 0.9}, error="broke")
    result = s.pareto({"a": "min", "b": "min"})
    assert len(result["front"]) == 2, "identical rows dominate neither"
    assert result["rows_considered"] == 2, "the errored row is not eligible"
    assert result["rows_total"] == 3


def test_report_front_names_each_points_trade():
    """Every rejected search was a trade discovered after the run; the
    rendered front exists to put each point's trade on the page before
    anything is run. Wording stays neutral -- best/worst on named outputs
    -- because atlas has no opinion about what the outputs mean."""
    s = hand_survey([(1.0, 1.0), (2.0, 0.0), (0.0, 2.0), (1.5, 1.5)])
    text = s.report_front({"a": "min", "b": "min"})
    assert "3 of 4 usable rows" in text
    assert "front-best a" in text and "front-best b" in text
    assert "screening resolution" in text


# -- attribution ------------------------------------------------------------

def test_attribution_decomposes_an_additive_difference_correctly():
    """The method exists because a two-parameter candidate was explained
    with one confident sentence that was right about one parameter and
    backwards about the other. On an additive surface the decomposition
    is exact up to bin noise, and both signs must come out right."""
    result = synthetic(lambda v: {"y": 2.0 * v["x1"] - v["x2"]},
                       samples=480, seed=13)
    a = {"x1": 0.2, "x2": 0.3, "x3": 0.5}
    b = {"x1": 0.7, "x2": 0.6, "x3": 0.5}
    att = result.attribution(a, b, "y")
    assert att["contributions"]["x1"]["delta"] == pytest.approx(1.0, abs=0.2)
    assert att["contributions"]["x2"]["delta"] == pytest.approx(-0.3, abs=0.2)
    assert "x3" not in att["contributions"], "an unchanged parameter is silent"
    assert att["predicted_delta"] == pytest.approx(0.7, abs=0.3)
    assert "additivity" in att["assumes"]
    assert "x1" in att["summary"] and "screening resolution" in att["summary"]


def test_attribution_reports_measured_delta_and_residual_when_given():
    result = synthetic(lambda v: {"y": v["x1"]}, samples=240)
    att = result.attribution({"x1": 0.2}, {"x1": 0.8}, "y",
                             measured=(0.2, 0.8))
    assert att["measured_delta"] == pytest.approx(0.6)
    assert att["residual"] == pytest.approx(
        att["measured_delta"] - att["predicted_delta"])


def test_attribution_refuses_extrapolation_and_one_sided_vectors():
    result = synthetic(lambda v: {"y": v["x1"]}, samples=240)
    with pytest.raises(pretium.ValidationError, match="outside the surveyed"):
        result.attribution({"x1": 0.5}, {"x1": 1.5}, "y")
    with pytest.raises(pretium.ValidationError, match="one vector"):
        result.attribution({"x1": 0.5, "x2": 0.1}, {"x1": 0.6}, "y")
    with pytest.raises(pretium.ValidationError):
        result.attribution({"x9": 0.5}, {"x9": 0.6}, "y")


def test_a_lone_row_in_a_bin_cannot_certify_a_driver_of_pure_noise():
    """The review's demonstration, pinned: a one-row bin has p10 == p90,
    and an early `_bin_noise` read that as INFINITELY PRECISE (sd 0.0) --
    so on a 48-row survey of pure noise, attribution reported the lone
    first-bin row as a confirmed driver (delta +1.24, se 0.26,
    within_noise empty). Thin bins are exactly what a `where=` filter or
    an errored region produces, so the failure aims at real use. A bin
    under four rows now carries an infinite noise scale, and nothing read
    from it can clear a gate."""
    s = Survey(axes=unit_axes("x1"))
    xs = [0.02] + [0.5 + 0.5 * (i + 0.5) / 47 for i in range(47)]
    for i, x in enumerate(xs):
        s.record(i, {"x1": x}, outputs={"y": math.sin(i * 12.9898)})
    att = s.attribution({"x1": 0.02}, {"x1": 0.75}, "y")
    assert att["contributions"]["x1"]["se"] == math.inf
    assert att["within_noise"] == ["x1"]
    assert "no single parameter's contribution clears its noise" \
        in att["summary"]


def test_attribution_on_an_interacting_surface_owns_its_blindness():
    """Verified failure mode: on y = x1*x2 the marginal attributes an
    x1 move at E[x2], understating or overstating by whatever the true
    x2 is -- and the ONLY honest signal is the residual, which needs
    measured endpoints. Without them the summary must say the assumption
    went unchecked, because 'B beats A because...' with no measured check
    behind it is the exact sentence class that put a confidently wrong
    mechanism claim in a design document."""
    result = synthetic(lambda v: {"y": v["x1"] * v["x2"]},
                       samples=480, seed=13)
    a = {"x1": 0.2, "x2": 0.8, "x3": 0.5}
    b = {"x1": 0.8, "x2": 0.8, "x3": 0.5}
    blind = result.attribution(a, b, "y")
    assert blind["measured_delta"] is None and blind["residual"] is None
    assert "UNCHECKED" in blind["summary"]
    # True delta is 0.6 * 0.8 = 0.48; the marginal predicts ~0.6 * E[x2].
    checked = result.attribution(a, b, "y", measured=(0.16, 0.64))
    assert checked["predicted_delta"] == pytest.approx(0.30, abs=0.08)
    assert checked["residual"] == pytest.approx(
        0.48 - checked["predicted_delta"])
    assert checked["residual"] > 0.1, "the interaction lands in the residual"
    assert "residual" in checked["summary"]


def test_a_within_noise_contribution_is_flagged_not_ranked():
    # x2 moves y by a hair against a unit-wide spread from x1: real
    # arithmetic would print "+0.001" to four decimals; the flag is the
    # honest rendering at screening resolution.
    result = synthetic(lambda v: {"y": v["x1"] + 0.001 * v["x2"]},
                       samples=240)
    att = result.attribution({"x1": 0.5, "x2": 0.1},
                             {"x1": 0.5, "x2": 0.9}, "y")
    assert att["within_noise"] == ["x2"]
    assert "within noise: x2" in att["summary"]


# -- explain ----------------------------------------------------------------

def test_explain_names_the_driver_its_shape_and_the_inert_list():
    result = synthetic(
        lambda v: {"y": -((v["x1"] - 0.55) ** 2)}, samples=480, seed=21)
    text = result.explain("y")
    assert "x1" in text
    assert "interior maximum" in text, (
        "the shape is the decision: an interior optimum says 'ship a tuned "
        "value' where a monotone slide says 'the mechanism is net harmful'"
    )
    assert "no monotone effect measured" in text
    assert "x2" in text and "x3" in text
    assert "not an elasticity" in text, "the caveat must travel with the text"
    assert "480 of 480" in text, "and so must the row count"


def test_explain_says_so_when_nothing_moves_the_output():
    result = synthetic(lambda v: {"y": 1.0})
    text = result.explain("y")
    assert "nothing moves this output" in text


def test_explain_degrades_between_the_sensitivity_and_marginal_floors():
    # `sensitivity` stands on 8 rows; a 12-bin marginal needs 36. In
    # between, explain used to die with profile's refusal -- a confusing
    # error from a method the caller never called. The correlation is
    # still worth printing; only the shape word is absent, and it says so.
    s = Survey(axes=unit_axes("x1"))
    for i in range(12):
        s.record(i, {"x1": i / 12.0}, outputs={"y": i / 12.0})
    text = s.explain("y")
    assert "x1" in text
    assert "shape not read" in text


def test_explain_carries_the_meta_resolution_note():
    result = synthetic(lambda v: {"y": v["x1"]})
    result.meta["resolution"] = "six seeds is a screening resolution"
    assert "six seeds is a screening resolution" in result.explain("y")


# -- confirm ----------------------------------------------------------------

def screened(seeds=(101, 102, 103)):
    """A survey that knows which seeds measured it, as the driver records."""
    result = synthetic(lambda v: {"y": v["x1"]}, names=("x1",), samples=32)
    result.meta["seeds"] = list(seeds)
    return result


def test_confirmation_on_the_surveys_own_seeds_is_refused_not_warned():
    """The seven-retraction mistake, made structurally impossible: a
    candidate was declared shippable on a +0.1297 gap whose discovery AND
    validation both used seeds 101-130 -- re-measuring reproduced the same
    fluctuation and called it confirmation. On fresh blocks the gap read
    -0.0315, +0.0209, +0.0233. A warning is a thing people read past, so
    overlap raises."""
    s = screened(seeds=range(101, 131))
    with pytest.raises(pretium.ValidationError, match="confirm itself"):
        s.confirm({"x1": 0.8}, {"x1": 0.2},
                  lambda v, seed: {"y": v["x1"]},
                  seed_blocks=[range(101, 131)])
    # Disjoint blocks pass the same gate.
    ok = s.confirm({"x1": 0.8}, {"x1": 0.2},
                   lambda v, seed: {"y": v["x1"]},
                   seed_blocks=[range(201, 206), range(301, 306)])
    assert ok["outputs"]["y"]["consistent_sign"]


def test_a_survey_without_a_seed_record_cannot_confirm():
    # The overlap check runs on records, not on trust: a survey that does
    # not know its own seeds cannot prove disjointness, so it refuses.
    result = synthetic(lambda v: {"y": v["x1"]}, names=("x1",), samples=32)
    with pytest.raises(pretium.ValidationError, match="meta\\['seeds'\\]"):
        result.confirm({"x1": 0.8}, {"x1": 0.2},
                       lambda v, seed: {"y": v["x1"]},
                       seed_blocks=[[201, 202]])


def test_blocks_sharing_a_seed_or_identical_vectors_are_refused():
    s = screened()
    with pytest.raises(pretium.ValidationError, match="more than one"):
        s.confirm({"x1": 0.8}, {"x1": 0.2},
                  lambda v, seed: {"y": v["x1"]},
                  seed_blocks=[[201, 202], [202, 203]])
    with pytest.raises(pretium.ValidationError, match="identical"):
        s.confirm({"x1": 0.5}, {"x1": 0.5},
                  lambda v, seed: {"y": v["x1"]},
                  seed_blocks=[[201, 202]])


def test_a_real_effect_reads_as_consistent_across_fresh_blocks():
    s = screened()

    def measure(v, seed):  # a genuine 0.6 effect under mild path noise
        return {"y": v["x1"] + 0.01 * math.sin(seed)}

    out = s.confirm({"x1": 0.8}, {"x1": 0.2}, measure,
                    seed_blocks=[range(201, 211), range(301, 311),
                                 range(401, 411)])
    row = out["outputs"]["y"]
    assert row["consistent_sign"] and not row["reverses_sign"]
    assert row["mean_gap"] == pytest.approx(0.6, abs=0.02)
    assert len(row["gaps"]) == 3
    assert "consistent sign in 3/3 blocks" in out["summary"]
    assert "nothing here is proof" in out["summary"]


def test_a_path_effect_reads_as_a_sign_reversal_not_a_finding():
    s = screened()

    def measure(v, seed):  # the "effect" is a property of the seed block
        return {"y": (0.1 if seed < 300 else -0.1) * v["x1"]}

    out = s.confirm({"x1": 0.8}, {"x1": 0.2}, measure,
                    seed_blocks=[range(201, 206), range(301, 306)])
    row = out["outputs"]["y"]
    assert row["reverses_sign"] and not row["consistent_sign"]
    assert "sign REVERSES" in out["summary"]
    assert "path luck" in out["summary"]


def test_a_single_block_confirmation_says_what_it_cannot_say():
    s = screened()
    out = s.confirm({"x1": 0.8}, {"x1": 0.2},
                    lambda v, seed: {"y": v["x1"]},
                    seed_blocks=[range(201, 206)])
    assert "a number, not a confirmation" in out["summary"]
    assert "single block cannot distinguish" in out["summary"]


def test_a_measurement_hole_fails_the_confirmation_loudly():
    # Unlike a survey row, there is no downstream analysis to skip a bad
    # seed honestly -- a block with quiet holes would carry the full
    # block's authority.
    s = screened()

    def measure(v, seed):
        return {"y": float("nan") if seed == 203 else v["x1"]}

    with pytest.raises(pretium.ValidationError, match="seed 203"):
        s.confirm({"x1": 0.8}, {"x1": 0.2}, measure,
                  seed_blocks=[range(201, 206)])


def test_a_hole_at_the_first_seed_is_a_refusal_not_a_smaller_answer():
    """The review's exact hole: the output set was once derived from the
    FIRST candidate measurement alone, so an output non-finite there --
    and only there -- silently vanished from the whole confirmation while
    the same NaN at any later seed raised. The suite was blind to it
    because its only hole test put the NaN at seed 203."""
    s = screened()

    def measure(v, seed):
        y = float("nan") if seed == 201 else v["x1"]
        return {"y": y, "z": v["x1"]}

    with pytest.raises(pretium.ValidationError, match="seed 201") as err:
        s.confirm({"x1": 0.8}, {"x1": 0.2}, measure,
                  seed_blocks=[range(201, 206)])
    assert "'y'" in str(err.value), "the refusal names the holed output"


def test_an_empty_seed_record_cannot_vouch_for_disjointness():
    """`meta['seeds'] = []` once slid past a presence check, ran a vacuous
    overlap test, and the summary then PRINTED 'checked, not trusted' over
    a check that had checked nothing. An empty record is the absence of a
    record wearing the key."""
    result = synthetic(lambda v: {"y": v["x1"]}, names=("x1",), samples=32)
    result.meta["seeds"] = []
    with pytest.raises(pretium.ValidationError, match="missing or empty"):
        result.confirm({"x1": 0.8}, {"x1": 0.2},
                       lambda v, seed: {"y": v["x1"]},
                       seed_blocks=[[201, 202]])


def test_seed_types_are_normalised_before_the_overlap_check():
    # '101' beside 101 defeats a set intersection; the gate normalises
    # both sides so a JSON round trip cannot re-open it. Anything that is
    # not a seed at all is refused.
    s = screened(seeds=(101, 102, 103))
    with pytest.raises(pretium.ValidationError, match="confirm itself"):
        s.confirm({"x1": 0.8}, {"x1": 0.2},
                  lambda v, seed: {"y": v["x1"]},
                  seed_blocks=[["101", 202]])
    with pytest.raises(pretium.ValidationError, match="not a\\s+seed"):
        s.confirm({"x1": 0.8}, {"x1": 0.2},
                  lambda v, seed: {"y": v["x1"]},
                  seed_blocks=[["twelve"]])


def test_a_vanishing_gap_is_never_described_as_a_consistent_sign():
    """With gaps [0.0, +0.6] the structured result says consistent_sign
    False; the prose once said 'consistent sign in 2/2 blocks' beside it.
    A rendered sentence contradicting the data it travels with is the
    worst artifact this module can produce, because the sentence is the
    part people quote."""
    s = screened()

    def measure(v, seed):  # the effect exists only in the second block
        return {"y": v["x1"] if seed >= 300 else 0.0}

    out = s.confirm({"x1": 0.8}, {"x1": 0.2}, measure,
                    seed_blocks=[range(201, 206), range(301, 306)])
    row = out["outputs"]["y"]
    assert row["gaps"][0] == 0.0 and row["gaps"][1] == pytest.approx(0.6)
    assert not row["consistent_sign"]
    assert "vanishes in 1 of 2 blocks" in out["summary"]
    assert "consistent sign" not in out["summary"]


# -- persistence ------------------------------------------------------------

def test_a_saved_survey_reloads_and_answers_identically(tmp_path):
    # Measured once, reused by every future question -- the whole point of
    # persistence is that the reloaded map is the same map.
    result = synthetic(lambda v: {"y": math.tanh(4.0 * v["x1"])})
    result.meta["note"] = "survives the round trip"
    path = str(tmp_path / "survey.json")
    result.save(path)
    loaded = Survey.load(path)
    assert loaded.axes == result.axes
    assert loaded.rows == result.rows
    assert loaded.meta["note"] == "survives the round trip"
    assert loaded.sensitivity("y") == result.sensitivity("y")
    with open(path, encoding="utf-8") as handle:
        json.load(handle)  # it is real JSON, not repr


def test_save_writes_strict_json_even_when_an_output_is_nan(tmp_path):
    """Python's serialiser emits a bare `NaN` token that only Python reads
    back; `jq` and every non-Python consumer refuse the file, and a survey
    only its author's runtime can open is not a shared measurement. A
    non-finite output is stored as null -- the analysis already treats the
    two identically -- and allow_nan=False enforces it."""
    def measure(v):
        return {"y": float("nan") if v["x1"] > 0.5 else v["x1"]}

    result = synthetic(measure, names=("x1",), samples=32)
    path = str(tmp_path / "s.json")
    result.save(path)
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    assert "NaN" not in text
    json.loads(text, parse_constant=lambda c: pytest.fail(
        f"non-strict JSON token {c!r} in a saved survey"))
    loaded = Survey.load(path)
    assert loaded.sensitivity("y")["rows_nonfinite"] == 16


# -- the survey driver's configuration ---------------------------------------

def _driver():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                           / "tools" / "calibration"))
    import atlas_survey
    return atlas_survey


def test_the_reparameterisation_round_trips_the_preset_exactly():
    """The driver samples the two variance processes as (persistence,
    share) so every planned vector is stationary by construction. That is
    only sound if the translation is exact: pt-v3 through the round trip
    must come back bit-comparable, or the map would be centred on a model
    that is not the one shipping."""
    drv = _driver()
    settable = pretium.ModelParams.settable()
    ship = pretium.ModelParams.from_preset("pt-v3").to_dict()
    params = {n: float(ship[n]) for n in settable}
    back = drv.vector_to_params(drv.params_to_vector(params))
    assert set(back) == set(params)
    for name in settable:
        assert back[name] == pytest.approx(params[name], rel=1e-12), name


def test_the_survey_covers_every_settable_parameter():
    # The fourth registry of the settable surface, guarded like the other
    # three: a parameter missing here would be silently unsurveyed.
    drv = _driver()
    axes = {a.name for a in drv.survey_axes()}
    settable = set(pretium.ModelParams.settable())
    covered = (axes - {a.name for a in drv.TRANSFORMED_AXES}) \
        | set(drv.REPARAMETERISED)
    assert covered == settable
    assert len(axes) == len(settable)


def test_the_crisis_ranges_reach_past_the_known_optimum():
    """The failure atlas exists to prevent, pinned: crisisearch4 found
    nothing because the [1/4x, 4x] box capped crisis_blend_ramp at 5.6
    with the best known value at 6.0. The surveyed range must hold the
    best known values with room past them -- ramp to 50, where the
    mechanism is effectively off, so the map can show whether there is an
    interior optimum or the mechanism is net harmful."""
    drv = _driver()
    by_name = {a.name: a for a in drv.survey_axes()}
    ramp = by_name["crisis_blend_ramp"]
    assert ramp.low <= 6.0 <= ramp.high
    assert ramp.high >= 50.0
    assert ramp.log, "a 143x range surveyed linearly starves the low decades"
    cap = by_name["crisis_blend_cap"]
    assert cap.low <= 0.98 <= cap.high
    assert cap.high >= 1.0


def test_every_planned_vector_passes_the_stationarity_gate():
    """A sweep once ran two non-stationary vectors and reported the best
    as the day's result. The driver's boxes are chosen so the whole plan
    is feasible by construction -- checked here on a small plan with the
    real gate, not assumed from the construction."""
    drv = _driver()
    axes, vectors, feasibility = drv.build_plan(64, drv.DEFAULT_PLAN_SEED)
    assert len(vectors) == 64
    violations = [v for v in feasibility if v]
    assert violations == [], violations


def test_the_planned_ranges_all_contain_the_base_preset():
    # survey_axes() raises on this internally; exercised here so a range
    # edit that orphans the preset fails in CI rather than at launch.
    drv = _driver()
    drv.survey_axes()


def test_resume_skips_model_refusals_but_can_retry_infrastructure():
    """A model refusal is deterministic: re-running reproduces it, so it
    is final. An OOM under 88-way contention is not a property of the
    vector -- except that it clusters in the expensive corners, so
    recording it as permanent quietly biases every marginal against a
    REGION while the drop tally looks benign. Rows without a recorded
    kind (older files) are retried too: the safe default for an unknown
    failure is to look again."""
    drv = _driver()
    rows = [
        {"id": "0:panel252:101", "index": 0, "panel": {}},
        {"id": "0:infeasible", "index": 0, "kind": "infeasible",
         "violation": "garch"},
        {"id": "0:panel252:102", "index": 0,
         "error": "ValidationError: no returns", "error_kind": "model"},
        {"id": "0:panel252:103", "index": 0,
         "error": "MemoryError:", "error_kind": "infrastructure"},
        {"id": "0:panel252:104", "index": 0,
         "error": "RuntimeError: ?"},  # a row from before kinds existed
    ]
    assert drv.completed_ids(rows) == {r["id"] for r in rows}
    assert drv.completed_ids(rows, retry_errors=True) == {
        "0:panel252:101", "0:infeasible", "0:panel252:102"}


def test_error_kinds_separate_the_model_from_the_machine():
    drv = _driver()
    assert drv.error_kind(pretium.ValidationError("no returns")) == "model"
    assert drv.error_kind(MemoryError()) == "infrastructure"
    assert drv.error_kind(OSError(12, "cannot allocate")) == "infrastructure"
    assert drv.error_kind(RuntimeError("?")) == "unclassified"


def test_read_rows_drops_a_torn_final_line_with_a_warning(tmp_path, capsys):
    # A killed run can die mid-write; the torn line must not poison the
    # resume, and must not vanish silently either.
    drv = _driver()
    path = tmp_path / "tasks.jsonl"
    path.write_text(json.dumps({"id": "a"}) + "\n"
                    + json.dumps({"id": "b"}) + "\n"
                    + '{"id": "c", "pan')
    rows = drv.read_rows(path)
    assert [r["id"] for r in rows] == ["a", "b"]
    assert "torn write" in capsys.readouterr().out


def test_collect_builds_the_survey_from_streamed_rows_mid_flight(tmp_path):
    """`cmd_run`'s row contract exercised against `cmd_collect` on
    fabricated rows -- the streamed-file seam the 96-core run depends on,
    previously covered only by a manual smoke run. Covers: a retried
    success replacing its earlier error (keeping the stale error would
    undo --retry-errors), a model-refused vector recorded with its kind,
    pending vectors counted rather than invented, and the artifacts being
    written mid-flight."""
    import argparse
    from pretium.facts import REAL_MARKETS

    drv = _driver()
    axes = drv.survey_axes()
    samples, plan_seed = 8, drv.DEFAULT_PLAN_SEED
    fingerprint = drv.plan_fingerprint(axes, samples, plan_seed)
    outdir = tmp_path / "atlas"
    outdir.mkdir()
    (outdir / "meta.json").write_text(json.dumps(
        drv.build_meta(axes, samples, plan_seed, fingerprint)))

    panel = {k: (low + high) / 2.0 for k, (low, high) in REAL_MARKETS.items()}
    rows = [{"id": "0:shock:101", "index": 0, "kind": "shock", "seed": 101,
             "error": "MemoryError: 88 workers", "error_kind": "infrastructure"}]
    for days in drv.HORIZONS:
        for s in drv.SCREEN_SEEDS:
            rows.append({"id": f"0:panel{days}:{s}", "index": 0,
                         "kind": "panel", "days": days, "seed": s,
                         "panel": dict(panel),
                         "draws_by_stream": {"market": 1000}})
    for vix in drv.sr.HELD_VIX:
        for s in drv.SCREEN_SEEDS:
            rows.append({"id": f"0:held{vix:g}:{s}", "index": 0,
                         "kind": "held", "seed": s, "vix": vix,
                         "vol": 0.01 * (1.0 + vix / 20.0), "corr": 0.3})
    for s in drv.SCREEN_SEEDS:  # the retried successes, appended later
        rows.append({"id": f"0:shock:{s}", "index": 0, "kind": "shock",
                     "seed": s, "ratio": 1.1})
    rows.append({"id": "1:panel252:101", "index": 1, "kind": "panel",
                 "seed": 101, "error": "ValidationError: no instrument "
                 "produced 30 daily returns", "error_kind": "model"})
    with open(outdir / "tasks.jsonl", "w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")

    assert drv.cmd_collect(argparse.Namespace(out=str(outdir))) == 0
    survey = atlas.Survey.load(str(outdir / "atlas-survey.json"))
    by_index = {r["index"]: r for r in survey.rows}
    measured = by_index[0]
    assert "error" not in measured, "the retried success must win"
    for key in ("loss", "loss_252", "loss_504",
                "shock_ratio_median", "vol_lever", "corr_blend"):
        assert key in measured["outputs"], key
    assert measured["outputs"]["shock_ratio_median"] == pytest.approx(1.1)
    assert measured["outputs"]["vol_lever"] == pytest.approx(
        (1.0 + 65 / 20.0) / (1.0 + 5 / 20.0))
    assert by_index[1]["error"].startswith("[model]")
    assert survey.meta["vectors_pending"] == samples - 2
    assert survey.meta["errors_by_kind"] == {"model": 1}
    assert survey.meta["crn_market_deviation_count"] == 0
    assert (outdir / "atlas-report.txt").exists()
