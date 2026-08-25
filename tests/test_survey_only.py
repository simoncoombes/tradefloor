"""A restricted survey must still hand every task the whole parameter set.

`atlas_survey.py --only a,b,c --base pt-v6` surveys a few axes around a base
other than pt-v3. The trap it has to avoid is calibration trap #1: the panel
evaluator builds `ModelParams.from_preset("pt-v1", **overrides)`, so any
parameter a vector does not set falls through to pt-v1, and a six-axis survey
would silently be measuring pt-v1 with six pt-v6 numbers on top. The driver
completes every vector with the base preset's coordinates on the axes it does
not survey; this pins that completion, and that the plan's identity changes
with the base and the restriction.
"""

from __future__ import annotations

import pytest

import pretium


def _driver():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                           / "tools" / "calibration"))
    import atlas_survey
    return atlas_survey


ONLY = ("sector_factor_sigma", "sector_vix_coupling", "crisis_blend_source",
        "crisis_blend_cap", "crisis_blend_ramp", "idio_sigma_scale")


@pytest.fixture
def restricted():
    d = _driver()
    saved = (d.BASE_PRESET, d.ONLY)
    d.BASE_PRESET, d.ONLY = "pt-v6", ONLY
    try:
        yield d
    finally:
        d.BASE_PRESET, d.ONLY = saved


def test_every_vector_sets_every_parameter(restricted):
    axes, vectors, feasibility = restricted.build_plan(8, restricted.DEFAULT_PLAN_SEED)
    assert [a.name for a in axes] == sorted(ONLY) or {a.name for a in axes} == set(ONLY)
    settable = set(pretium.ModelParams.settable())
    base = pretium.ModelParams.from_preset("pt-v6").to_dict()
    for vec in vectors:
        params = restricted.vector_to_params(vec)
        assert set(params) == settable, (
            "a restricted vector did not set every parameter; whatever it "
            "missed falls through to evaluate_panel's pt-v1 base"
        )
        for name, value in params.items():
            if name in ONLY:
                continue
            assert value == pytest.approx(base[name], rel=0, abs=1e-12), (
                f"{name} is not surveyed and is not at pt-v6's value: "
                f"{value} against {base[name]}"
            )


def test_the_restriction_and_the_base_change_the_plan_identity(restricted):
    axes, _, _ = restricted.build_plan(8, restricted.DEFAULT_PLAN_SEED)
    narrow = restricted.plan_fingerprint(axes, 8, restricted.DEFAULT_PLAN_SEED)
    restricted.ONLY = None
    restricted.BASE_PRESET = "pt-v3"
    full_axes = restricted.survey_axes()
    full = restricted.plan_fingerprint(full_axes, 8, restricted.DEFAULT_PLAN_SEED)
    assert narrow != full
    assert len(full_axes) > len(axes)


def test_an_unknown_axis_is_refused(restricted):
    restricted.ONLY = ("sector_factor_sigma", "no_such_axis")
    with pytest.raises(RuntimeError, match="no_such_axis"):
        restricted.survey_axes()


def test_a_negative_base_value_gets_an_ordered_box(restricted):
    """pt-v6 ships jump_mean_market negative; the multiplicative box around it
    came out inverted and the axis constructor refused it."""
    restricted.ONLY = None
    for a in restricted.survey_axes():
        assert a.low < a.high, a.name
