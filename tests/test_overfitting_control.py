"""§8's flip test: in band on train, meaningfully out on validation.

The control that decides whether a calibration candidate is overfitting. It
lived inline in `calibrate.main` and could not be exercised, which is part of
why it carried a defect through several searches — a control nobody can test
is one nobody checks.

Two things it must do, and they pull against each other. It has to catch a
candidate that holds a statistic in band on the training seeds and loses it
on a validation axis. And it must not fire on a statistic that was sitting
on a band edge and landed a few hundredths of a seed-standard-deviation the
other side of it, because that is noise and calling it overfitting rejects
good candidates for a coin flip.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "tools" / "calibration"))

calibrate = pytest.importorskip("calibrate",
                                reason="calibration tooling is not packaged")
statistic_flips = calibrate.statistic_flips

KEYS = ["cross_sectional_corr"]
MARGIN = 0.5


def stat(*, distance, room_sd=None, measured=0.3, band=(0.08, 0.56)):
    return {"distance": distance, "room_sd": room_sd,
            "measured": measured, "band": band}


def flips(train_distance, valid_distance, room_sd, margin=MARGIN):
    return statistic_flips(
        {"cross_sectional_corr": stat(distance=train_distance)},
        {"cross_sectional_corr": stat(distance=valid_distance,
                                      room_sd=room_sd)},
        KEYS, margin, axis="holdout_seeds")


# -- what it must catch ----------------------------------------------------


def test_a_statistic_lost_on_validation_is_flagged():
    """The signal §8 exists for: in band on train, clearly out on a
    validation axis."""
    assert flips(0.0, 0.05, room_sd=-1.5)


@pytest.mark.parametrize("room_sd", [-0.51, -1.0, -3.0, -20.0])
def test_it_fires_once_the_statistic_is_past_the_tolerance(room_sd):
    assert flips(0.0, 0.05, room_sd=room_sd), room_sd


def test_the_flag_carries_what_is_needed_to_judge_it():
    # A flag saying only "this statistic" leaves a reader unable to tell a
    # real regression from a rounding accident, which is the confusion this
    # whole control had.
    row = flips(0.0, 0.05, room_sd=-2.0)[0]
    assert row["statistic"] == "cross_sectional_corr"
    assert row["axis"] == "holdout_seeds"
    assert row["room_sd"] == -2.0
    assert row["tolerance_sd"] == -MARGIN


# -- what it must not fire on ----------------------------------------------


@pytest.mark.parametrize("room_sd", [-0.001, -0.036, -0.073, -0.2, -0.49])
def test_a_statistic_a_hair_outside_its_band_is_noise(room_sd):
    """The measured cases from CALIBRATION-FOLLOWUPS §34.

    The constrained jump/volume search was rejected for `excess_kurtosis` at
    0.073 seed-sd below its floor and `abs_return_acf5` at 0.036 above its
    ceiling. A statistic 0.07 sd outside a band is not distinguishable from
    one 0.07 sd inside, and rejecting a candidate on that difference is
    reading noise.
    """
    assert not flips(0.0, 0.001, room_sd=room_sd), room_sd


def test_a_statistic_out_of_band_on_train_too_is_not_a_flip():
    # Out on both axes is a failure the LOSS already counts. It is not
    # overfitting, and counting it here would double-charge it.
    assert not flips(0.02, 0.05, room_sd=-5.0)


def test_a_statistic_in_band_on_validation_is_never_flagged():
    assert not flips(0.0, 0.0, room_sd=+2.0)


# -- the properties that keep it honest ------------------------------------


def test_the_boundary_is_the_margin_and_nothing_else():
    """Just inside the tolerance passes, just outside fires. Pinned because
    the whole defect was an implicit tolerance of zero."""
    assert not flips(0.0, 0.001, room_sd=-(MARGIN - 0.01))
    assert flips(0.0, 0.001, room_sd=-(MARGIN + 0.01))


def test_a_statistic_with_no_noise_scale_is_flagged_not_skipped():
    """`room_sd` is None when a statistic has no seed-sd. Falling through to
    'pass' would let an unmeasurable statistic through silently, and an
    unmeasurable statistic is not a safe one."""
    assert flips(0.0, 0.05, room_sd=None)


def test_a_larger_margin_is_more_permissive_monotonically():
    # Guards against an inverted comparison, which would turn the tolerance
    # into a trigger and reject everything.
    room = -1.0
    assert flips(0.0, 0.05, room, margin=0.5)
    assert flips(0.0, 0.05, room, margin=0.9)
    assert not flips(0.0, 0.05, room, margin=1.5)


def test_the_default_margin_matches_the_search_s_own():
    """One definition of 'near an edge' in the file, not two. The search
    pulls bands 0.5 seed-sd inward; the flip test tolerates the same."""
    assert calibrate.DEFAULT_MARGIN_SD == MARGIN
