"""The Zumbach asymmetry, and the theorem that makes it a null control.

`zumbach_asymmetry` is worth having twice over: as a target, because real
equity data is positive at scales of days to weeks and this engine's variance
laws read only past squares, and as a NULL CONTROL, because it is exactly zero
in population for that whole class of laws. A reading away from zero therefore
identifies a variance that reads a signed return with memory beyond a day.

These tests check the theorem numerically rather than citing it. They generate
processes inside and outside its class and assert which side of the line each
lands on, so a future change to the estimator that broke the null would fail
here rather than be discovered as an unexplained panel reading.
"""

from __future__ import annotations

import math
import random
import statistics as st

import pytest

from tradefloor.facts import zumbach_asymmetry, zumbach_asymmetry_pooled


def garch(n_obs: int, rng: random.Random, a=0.09, b=0.89, gamma=0.0, w=0.02):
    """GARCH(1,1), optionally with a one-day GJR sign term.

    Both are inside the theorem's class at gamma 0: the variance reads past
    squares and nothing else. The GJR term reads one day of sign, which the
    certificate calls a candidate mover and which measures small.
    """
    var = w / max(1e-6, 1.0 - a - b - gamma / 2.0)
    out, prev = [], 0.0
    for _ in range(n_obs):
        var = w + a * prev * prev + gamma * prev * prev * (prev < 0) + b * var
        prev = math.sqrt(var) * rng.gauss(0.0, 1.0)
        out.append(prev)
    return out


def signed_trend_reader(n_obs: int, rng: random.Random, share=0.9):
    """A variance reading a six-scale SIGNED sum: outside the class.

    This is the shape of the quadratic rough Heston the research record names
    as the only mover of this row. It is here as the positive control, so the
    test shows the estimator can see what it is for and not only that it reads
    zero on things it should.
    """
    scales = [1, 3, 9, 27, 81, 243]
    decay = [math.exp(-1.0 / T) for T in scales]
    weight = [T ** -(0.5 - 0.1) for T in scales]
    total = sum(weight)
    weight = [x / total for x in weight]
    norm = sum(
        weight[k] * weight[l] / (1.0 - decay[k] * decay[l])
        for k in range(6)
        for l in range(6)
    )
    state = [0.0] * 6
    base, out, z_prev = 0.02, [], 0.0
    for _ in range(n_obs):
        state = [decay[k] * state[k] + z_prev for k in range(6)]
        z_sum = sum(weight[k] * state[k] for k in range(6))
        var = base * ((1.0 - share) + share * z_sum * z_sum / norm)
        var = min(max(var, 1e-8), base * 40.0)
        z_prev = rng.gauss(0.0, 1.0)
        out.append(math.sqrt(var) * z_prev)
    return out


def median_over_seeds(gen, n, seeds=4, n_obs=20_000):
    return st.median(
        [zumbach_asymmetry(gen(n_obs, random.Random(s)), n) for s in range(seeds)]
    )


@pytest.mark.parametrize("n", [5, 10, 20])
def test_a_squares_only_variance_reads_zero(n):
    """The theorem's own case. A symmetric GARCH must not move this row."""
    assert abs(median_over_seeds(garch, n)) < 0.05


@pytest.mark.parametrize("n", [5, 10, 20])
def test_a_signed_trend_variance_moves_it(n):
    """The positive control: the estimator can see what it exists to see."""
    moved = median_over_seeds(signed_trend_reader, n)
    assert moved > 0.05


def test_the_signed_reader_is_well_clear_of_the_null():
    """Discrimination, not merely two thresholds either side of a line."""
    null = abs(median_over_seeds(garch, 10))
    moved = median_over_seeds(signed_trend_reader, 10)
    assert moved > 3.0 * max(null, 0.01)


def test_a_short_series_has_no_reading_rather_than_a_zero():
    assert zumbach_asymmetry([0.1, -0.2, 0.3], 10) is None
    assert zumbach_asymmetry([], 5) is None
    assert zumbach_asymmetry([0.01] * 100, 0) is None


def test_a_constant_series_has_no_reading():
    """A flat series is undefined here, as it is for any correlation."""
    assert zumbach_asymmetry([0.0] * 500, 5) is None


def test_pooling_forms_windows_within_a_name_and_never_across_two():
    """The join between two names must not become a window.

    Two identical names pooled must read what one of them reads. Concatenating
    the returns instead would build windows spanning the boundary, and those
    windows are of a quantity nobody computed.
    """
    rng = random.Random(11)
    one = garch(4000, rng)
    alone = zumbach_asymmetry_pooled([one], 5)
    together = zumbach_asymmetry_pooled([one, one], 5)
    assert alone is not None and together is not None
    assert together == pytest.approx(alone, abs=1e-9)

    # And the bug this guards against is not hypothetical: concatenating the
    # two series instead, so windows may straddle the join, gives a different
    # answer. Pooling by terms is what makes the duplicate a no-op.
    concatenated = zumbach_asymmetry(list(one) + list(one), 5)
    assert concatenated != pytest.approx(together, abs=1e-9)


def test_the_pooled_form_centres_each_name_and_the_bare_form_does_not():
    """A real difference between the two, stated rather than discovered.

    `zumbach_asymmetry` reads the series it is given. The pooled form divides
    each name by its own sd, which is scale-invariant and changes nothing, and
    also CENTRES it, which changes the trend terms whenever the mean is not
    zero. On a drifting series the two therefore differ, and neither is wrong.
    """
    drifting = [x + 0.05 for x in garch(3000, random.Random(17))]
    bare = zumbach_asymmetry(drifting, 5)
    pooled = zumbach_asymmetry_pooled([drifting], 5)
    assert bare is not None and pooled is not None
    assert bare != pytest.approx(pooled, abs=1e-6)


def test_pooling_standardises_so_a_loud_name_does_not_dominate():
    rng = random.Random(13)
    quiet = garch(3000, rng)
    loud = [x * 50.0 for x in garch(3000, random.Random(14))]
    pooled = zumbach_asymmetry_pooled([quiet, loud], 5)
    scaled = zumbach_asymmetry_pooled([quiet, [x * 1000.0 for x in loud]], 5)
    assert pooled is not None
    assert scaled == pytest.approx(pooled, abs=1e-9)
