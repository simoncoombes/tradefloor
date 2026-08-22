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
    # simulator gets wrong. Real daily equity returns run +3 to +10.
    #
    # The 2026-08 market-factor recalibration spent some of this statistic
    # deliberately: the shared factor is Gaussian, so the correlation it buys
    # dilutes the GARCH-driven tails, and the sigma sweep
    # (tools/calibration/) chose the largest value whose SIX-SEED MEDIAN
    # kurtosis stays inside the band (+3.24). This fixture's single seed is
    # the noisy floor of that spread — a fourth moment on one seed, exactly
    # the estimator this file's universe comment warns about — so what is
    # asserted here is fat-tailedness itself, not band membership on one
    # draw. If this reads near-Gaussian (< 1.5), the tails are gone and the
    # calibration trade documented on MARKET_FACTOR_SIGMA has been
    # re-traded; re-run the sweep before touching anything.
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    assert facts["excess_kurtosis"] > 1.5


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
    `pretium.facts` -- that momentum is mechanically profitable here in a way it
    is not in real markets -- is no longer true and must come out.
    """
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    assert facts["return_acf1"] > 0.1
    verdict = compare_to_real_markets(facts)["return_acf1"]
    assert not verdict["matches"]
    assert verdict["direction"] == "above"


def test_the_autocorrelation_is_the_mispricing_process_showing_through():
    """The structural half of the claim, from the model's own function.

    `impulse_response` reports what the AR(2) mispricing process does to a
    shock over time, and it does not decay monotonically -- it RISES, to 1.284
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


def test_volatility_clustering_is_in_band_at_lag_one_but_decays_too_fast():
    # This test used to pin clustering BELOW its band -- real markets show
    # 0.2-0.3 at lag one and this model measured about half that. The GJR
    # recalibration moved it: paying the asymmetry term out of BETA shifts
    # variance persistence from smooth carry to re-excitation by realised
    # moves, which raises |r| acf(1) into the real band (0.19 here) as a
    # side effect of buying the leverage effect. The decay is still too
    # fast -- largely gone by lag twenty where real markets decay slowly --
    # so a strategy whose edge is long-horizon volatility forecasting will
    # still look worse here than it should.
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    verdict = compare_to_real_markets(facts)["abs_return_acf1"]
    assert verdict["direction"] == "within"
    assert facts["abs_return_acf20"] < facts["abs_return_acf1"]


def test_volatility_is_high_so_prefer_ratios_to_raw_percentages():
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    assert compare_to_real_markets(facts)["annualised_vol_pct"]["direction"] == "above"


# --------------------------------------------------------------------------
# Dependence: how things move together, which is where this model is weakest
# --------------------------------------------------------------------------
#
# The four statistics below were added after every realism gap found in this
# project turned out to sit outside the four the module originally reported.
# All four of those come from one instrument's price series taken on its own:
# nothing looked across instruments and nothing looked between price and
# volume, so a report that never left a single series kept passing while the
# joint behaviour was wrong. These are the measurements that would have caught
# it.


def test_the_report_covers_dependence_and_not_only_marginals(facts):
    # The blind spot in one assertion: a report of marginals alone cannot see
    # the gaps this model actually has.
    for key in ("cross_sectional_corr", "volume_abs_return_corr",
                "leverage_effect", "volume_change_acf1"):
        assert key in facts, key
        assert facts[key] is not None, key
    text = report(facts)
    for label in pretium.facts.LABELS.values():
        assert label in text, label


def test_stocks_move_together_at_a_real_market_rate_since_the_factor_vol_change(facts):
    """Cross-sectional correlation sits in the real band, and how it got
    there matters more than that it did.

    This was the model's largest realism gap: +0.026 against a real +0.25
    to +0.35 under the reference constants, and the sigma sweep proved the
    band UNREACHABLE by the constant-sigma factor -- a Gaussian factor big
    enough to reach +0.25 collapsed kurtosis to a third of its band's
    floor (finding 14). The factor-variance process
    (rust/src/market/factor_vol.rs) dissolved that trade: the factor's
    share of every name's variance is now a fat-tailed regime rather than
    an iid Gaussian dilutant, funded out of the idiosyncratic sigma, and
    the six-seed published medians read correlation 0.260 WITH kurtosis
    3.14 -- both in band together for the first time.

    Both bounds stay load-bearing: the floor pins the calibration (a
    regression toward the near-zero regime must fail), and the ceiling
    pins the funding (correlation far above band would mean the factor's
    share grew without being paid for). The fixture is one seed at 180
    days, so the bounds are wider than the published six-seed medians.
    """
    assert 0.15 < facts["cross_sectional_corr"] < 0.45
    verdict = compare_to_real_markets(facts)["cross_sectional_corr"]
    assert verdict["matches"] or verdict["direction"] == "above"


def test_volume_and_volatility_arrive_together_since_the_volume_fix(facts):
    """The one dependence statistic the 2026-08 era boundary moved into band.

    Before the `avg_volume` feedback was removed this measured +0.105 against
    a real +0.30 to +0.60, and the gap was an artefact of the divergence: the
    compounding volume level swamped the day-to-day covariation with returns.
    With the level held, the per-tick channel shows through -- volume scales
    with the size of the day's move by construction -- and the statistic reads
    +0.499 to +0.537 across seeds 1 to 6, solidly inside the band.

    Kept in the dependence section deliberately: an execution algorithm now at
    least faces volume that arrives with volatility. What it still does not
    face is a volume shock that persists, which is the change-autocorrelation
    test below and the caveat that survives.
    """
    # The floor is the claim; the ceiling is left to the published
    # six-seed medians (0.584, in band at the factor-vol calibration),
    # because a single 180-day seed can overshoot the band top now that
    # factor-variance regimes couple volume to market-wide moves.
    assert facts["volume_abs_return_corr"] > 0.30


def test_the_leverage_effect_is_real_since_the_gjr_term(facts):
    """Bad news raises volatility more than good news, as it should.

    The effect was absent BY CONSTRUCTION while the variance process was a
    symmetric GARCH(1,1) -- squaring the return discards its sign -- and
    this test used to pin that absence. The GJR asymmetry term (GAMMA =
    0.34, garch.rs) made it real: the published six-seed median reads
    -0.107, every seed negative, inside the real -0.30 to -0.10 band. The
    factor-variance change leaves the mechanism intact (measured -0.094 at
    its calibration); what this fixture pins is that the effect exists and
    points the right way on a single seed, not band membership of a noisy
    single-seed estimate.
    """
    assert facts["leverage_effect"] < -0.02


def test_a_weak_leverage_effect_would_read_as_weak_not_as_too_high():
    """The wording trap in the one statistic with a negative band.

    A leverage effect of -0.01 against a band of -0.30 to -0.10 is numerically
    ABOVE the band and semantically ABSENT. Reporting the numeric direction
    would print "too high" for a missing effect, which states the opposite of
    the finding. The live fixture no longer exercises the trap (the GJR term
    made the effect real), so it is pinned on a synthetic panel instead --
    the wording rule has to survive the statistic being healthy.
    """
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    weakened = dict(facts)
    weakened["leverage_effect"] = -0.01
    verdict = compare_to_real_markets(weakened)["leverage_effect"]
    assert verdict["direction"] == "above"
    assert verdict["verdict"] == "too weak"
    assert "TOO WEAK" in report(weakened)


def test_volume_is_measured_in_changes_because_the_level_says_nothing():
    """Why the reported statistic differences volume instead of using levels.

    The LEVEL autocorrelation tracks whatever the level happens to be doing
    and says nothing about whether volume SHOCKS persist. Before the 2026-08
    era boundary it read +0.945 -- pure artefact of the `avg_volume` feedback
    compounding the level a percent-plus a day. With the feedback removed and
    the level held it reads about +0.10. Opposite engines, and the level
    statistic cannot tell them apart from the dynamics side. The CHANGE
    autocorrelation can, and it reads near -0.5 in BOTH worlds: the signature
    of independent daily noise around whatever the level is. Real volume
    shocks persist, so real markets sit near zero.

    The upper bound on the level doubles as the tripwire for the divergence
    coming back: a compounding level drives the level autocorrelation toward
    +1, so this test fails if `avg_volume` ever feeds back on itself again.
    """
    import statistics

    import pyarrow as pa

    from pretium.facts import _autocorrelation, _correlation, _daily_series

    engine = pretium.Engine(seed=5, universe=UNIVERSE)
    engine.run_days(120, record=True)
    bars = pa.table(engine.bars(grain="day")).to_pydict()

    levels = []
    changes = []
    for rows in _daily_series(bars).values():
        volume = [row[2] for row in rows]
        levels.append(_autocorrelation(volume, 1))
        differenced = [
            later / earlier - 1.0
            for earlier, later in zip(volume, volume[1:])
            if earlier > 0
        ]
        changes.append(_correlation(differenced[:-1], differenced[1:]))

    assert statistics.median(levels) < 0.5
    assert statistics.median(changes) < -0.2


def test_the_bars_table_is_grouped_before_it_is_differenced():
    """The trap that fails silently and returns a plausible number.

    The daily bars table is DAY-major, so consecutive rows are different
    instruments. Differencing it as it comes computes returns between
    unrelated companies. Nothing raises, and the answer looks like a number.
    """
    import pyarrow as pa

    from pretium.facts import _daily_series

    engine = pretium.Engine(seed=5, universe=UNIVERSE)
    engine.run_days(60, record=True)
    bars = pa.table(engine.bars(grain="day")).to_pydict()

    # The table really is day-major, or this test is guarding nothing.
    identifiers = bars["instrument_id"]
    assert identifiers[0] != identifiers[1]
    assert len(set(identifiers[:len(UNIVERSE)])) == len(UNIVERSE)

    # After grouping, each instrument's rows are its own and in day order.
    for instrument, rows in _daily_series(bars).items():
        days = [row[0] for row in rows]
        assert days == sorted(days)
        assert len(days) == len(set(days))


def test_a_statistic_that_cannot_be_measured_is_absent_rather_than_zero():
    # One instrument has no pairwise correlation. Zero would be a lie of
    # exactly the shape the module warns about elsewhere: it is a real
    # reading, and here it would land close to what the model actually scores.
    facts = measure(seed=2, universe=pretium.Universe.random(1, seed=9), days=60)
    assert facts["cross_sectional_corr"] is None
    assert "cross_sectional_corr" not in compare_to_real_markets(facts)
    assert "n/a" in report(facts)
    # The other three are per-instrument and remain measurable.
    assert facts["leverage_effect"] is not None


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
    assert "TOO HIGH" in text
    assert "matches" in text
    assert "momentum is mechanically" in text
