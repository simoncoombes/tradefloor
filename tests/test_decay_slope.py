"""The decay exponent, which the survey measured the region for and never reported.

CALIBRATION-FOLLOWUPS §56a: `atlas_survey.TRANSFORMED_AXES` already spans
`garch_persistence` over (0.21, 0.99), and a vector at 0.94 moves the decay
slope more than a third of the way from the shipped preset's -0.95 toward
real markets' -0.436. Four thousand samples were scored and filed without
anyone learning that, because the exponent is not one of the ten panel
statistics.

It is now an output, and it costs nothing: the slope is a pure function of
`abs_return_acf1`, `_acf5` and `_acf20`, which every surveyed vector already
produces.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools" / "calibration"))
import atlas_survey  # noqa: E402


def _panel(a1: float, a5: float, a20: float, days: int = 504) -> dict[str, float]:
    return {
        f"abs_return_acf1_{days}": a1,
        f"abs_return_acf5_{days}": a5,
        f"abs_return_acf20_{days}": a20,
    }


def test_it_reproduces_the_shipped_preset_slope() -> None:
    """pt-v6's own 504-day panel medians, measured at thirty seeds (§54).

    -0.917 was arrived at independently by `decay_curve.py`. The survey's
    helper has to agree with it or one of the two is measuring something
    else.
    """
    got = atlas_survey.decay_slope(_panel(0.2015, 0.1032, 0.0124), 504)
    assert got == pytest.approx(-0.917, abs=0.005)


def test_a_flatter_curve_reads_as_a_flatter_slope() -> None:
    """Direction, which is the whole point of recording it.

    Real markets decay more slowly than the model, so their slope is closer
    to zero. A helper that got the sign or the ordering wrong would make the
    gap look closed by whatever made it worse.
    """
    steep = atlas_survey.decay_slope(_panel(0.2015, 0.1032, 0.0124), 504)
    flat = atlas_survey.decay_slope(_panel(0.1071, 0.0518, 0.0286), 504)
    assert steep < flat < 0.0


def test_a_pure_power_law_recovers_its_own_exponent() -> None:
    """The fit is a fit, so give it something with a known answer."""
    for exponent in (-0.2, -0.436, -0.9):
        panel = _panel(*(1.0 * lag ** exponent for lag in atlas_survey.DECAY_LAGS))
        assert atlas_survey.decay_slope(panel, 504) == pytest.approx(exponent, abs=1e-9)


@pytest.mark.parametrize("a1,a5,a20", [(0.2, 0.0, 0.01), (0.2, 0.1, -0.01), (0.0, 0.1, 0.01)])
def test_non_positive_autocorrelations_give_none_rather_than_a_number(
    a1: float, a5: float, a20: float
) -> None:
    """A fast-decaying vector goes negative at lag 20 and has no logarithm.

    Returning a number there would be worse than returning nothing: the
    survey would rank vectors on an exponent fitted to a value that does not
    exist.
    """
    assert atlas_survey.decay_slope(_panel(a1, a5, a20), 504) is None


def test_a_missing_statistic_gives_none() -> None:
    assert atlas_survey.decay_slope({"abs_return_acf1_504": 0.2}, 504) is None


def test_the_horizon_is_respected() -> None:
    """252 and 504 are different curves and must not be silently mixed."""
    panel = _panel(0.2015, 0.1032, 0.0124, days=504)
    assert atlas_survey.decay_slope(panel, 504) is not None
    assert atlas_survey.decay_slope(panel, 252) is None
