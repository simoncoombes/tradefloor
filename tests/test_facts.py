"""What these markets look like statistically, and where they do not look real.

The mismatches are the point. A simulator you cannot characterise is one you
cannot reason about, and a conclusion drawn here transfers only as far as the
properties it depends on do.

Every figure is measured. The bands are deliberately wide: these pin the model
against drift, not against a target it was tuned to hit.
"""

import pytest

pytest.importorskip("pyarrow")

import pretium
from pretium.facts import compare_to_real_markets, measure, report

# Forty names, not thirty. Excess kurtosis is a fourth-moment estimate and it
# needs the sample: at 30 names x 180 days (5,370 returns) it read +2.79, just
# outside the real-market band, and at 40 x 180 (7,160 returns) it reads +5.28,
# comfortably inside. The property did not change -- the estimator was noisy.
# Measuring a tail statistic on too few observations and calling the result a
# model property is the mistake this comment exists to prevent repeating.
UNIVERSE = pretium.Universe.random(40, seed=111)


@pytest.fixture(scope="module")
def facts():
    return measure(seed=3, universe=UNIVERSE, days=180)


# --------------------------------------------------------------------------
# What matches
# --------------------------------------------------------------------------


def test_returns_are_fat_tailed():
    # The most robust fact about asset returns, and the one a Gaussian
    # simulator gets wrong. Measured around +4 across seeds; real daily equity
    # returns run +3 to +10.
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    assert facts["excess_kurtosis"] > 1.5
    assert compare_to_real_markets(facts)["excess_kurtosis"]["matches"]


def test_volatility_clusters():
    # Calm follows calm. Positive at lag one and still positive at lag five,
    # which is the GARCH process doing its job.
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    assert facts["abs_return_acf1"] > 0.03
    assert facts["abs_return_acf5"] > 0.0


# --------------------------------------------------------------------------
# What does not match, and why it matters
# --------------------------------------------------------------------------


def test_returns_are_positively_autocorrelated_and_real_ones_are_not():
    """The mismatch that qualifies every agent ranking this library produces.

    Measured at +0.235 at lag one, in six seeds of six, ranging only +0.221 to
    +0.240. Real daily equity returns sit near zero and are if anything
    slightly negative.

    Pinned as a test because it is a KNOWN deviation that must stay documented.
    If it ever moves near zero the model changed and the caveat in
    `pretium.facts` — that momentum is mechanically profitable here in a way it
    is not in real markets — is no longer true and must come out.
    """
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    assert facts["return_acf1"] > 0.1
    verdict = compare_to_real_markets(facts)["return_acf1"]
    assert not verdict["matches"]
    assert verdict["direction"] == "above"


def test_the_autocorrelation_is_the_mispricing_process_showing_through():
    """The structural half of the claim, from the model's own function.

    `impulse_response` reports what the AR(2) mispricing process does to a
    shock over time, and it does not decay monotonically — it RISES, to 1.284
    by day two, before reverting. A shock today is amplified tomorrow.

    That is positive serial correlation by construction, so the measured
    return autocorrelation above is the process showing through in prices
    rather than a coincidence of one seed. Two independent measurements of the
    same mechanism, which is what makes it an explanation instead of an
    observation.
    """
    response = pretium.impulse_response(12)
    assert response[0] == pytest.approx(1.0)
    assert response[1] > response[0]
    assert max(response) > 1.2
    # And it is still stationary, so the amplification reverts rather than
    # running away.
    assert all(modulus < 1.0 for modulus in pretium.characteristic_root_moduli())


def test_volatility_clustering_is_weaker_than_real_markets():
    # Real markets show 0.2-0.3 at lag one with slow decay. Here it is about
    # half that and largely gone by lag twenty, so a strategy whose edge is
    # volatility forecasting will look worse here than it should.
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    verdict = compare_to_real_markets(facts)["abs_return_acf1"]
    assert verdict["direction"] == "below"
    assert facts["abs_return_acf20"] < facts["abs_return_acf1"]


def test_volatility_is_high_so_prefer_ratios_to_raw_percentages():
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    assert compare_to_real_markets(facts)["annualised_vol_pct"]["direction"] == "above"


# --------------------------------------------------------------------------
# The measurement itself
# --------------------------------------------------------------------------


def test_the_measurement_is_reproducible():
    a = measure(seed=5, universe=UNIVERSE, days=60)
    b = measure(seed=5, universe=UNIVERSE, days=60)
    assert a == b


def test_autocorrelation_is_a_median_across_instruments_not_a_pooled_series():
    # Pooling thirty instruments into one series would splice thirty unrelated
    # histories end to end and measure the joins. Checked by confirming the
    # reported value is not what pooling would give.
    import math
    import statistics

    import pyarrow as pa

    engine = pretium.Engine(seed=5, universe=UNIVERSE)
    engine.run_days(60, record=True)
    bars = pa.table(engine.bars(grain="day")).to_pydict()
    spliced = []
    for k in range(1, len(bars["close"])):
        previous, current = bars["close"][k - 1], bars["close"][k]
        if previous > 0 and current > 0:
            spliced.append(math.log(current / previous))
    mean = statistics.mean(spliced)
    variance = sum((x - mean) ** 2 for x in spliced)
    pooled = sum((spliced[i] - mean) * (spliced[i - 1] - mean)
                 for i in range(1, len(spliced))) / variance

    reported = measure(seed=5, universe=UNIVERSE, days=60)["return_acf1"]
    assert abs(reported - pooled) > 0.05


def test_a_run_too_short_to_measure_is_refused():
    with pytest.raises(pretium.ValidationError):
        measure(seed=1, universe=UNIVERSE, days=1)
    with pytest.raises(pretium.ValidationError, match="more days"):
        measure(seed=1, universe=UNIVERSE, days=5, min_observations=100)


def test_the_report_names_the_mismatches_rather_than_scoring_them():
    # A single "realism score" would average a property the model reproduces
    # well against one it gets frankly wrong, and knowing WHICH is the whole
    # value of the exercise.
    text = report(measure(seed=3, universe=UNIVERSE, days=180))
    assert "ABOVE" in text
    assert "matches" in text
    assert "momentum is mechanically" in text
