"""What these markets look like statistically, and where they do not look real.

The mismatches are the point. A simulator you cannot characterise is one you
cannot reason about, and a conclusion drawn here transfers only as far as the
properties it depends on do.

Every figure is measured, and since 2026-08-22 every band is derived: from a
real reference panel measured with this module's own estimators, reconciled
against retrieved literature, with the provenance carried as data in
`facts.REAL_MARKETS_PROVENANCE`. The bands are deliberately wide -- they pin
the model against drift, not against a target it was tuned to hit -- and
they are wide by measured seed noise, not by convenience.
"""

import pytest

pytest.importorskip("pyarrow")

import tradefloor
from tradefloor.facts import compare_to_real_markets, measure, report

# Forty names, not thirty. Excess kurtosis is a fourth-moment estimate and it
# needs the sample: at 30 names x 180 days (5,370 returns) it read +2.79, just
# outside the real-market band, and at 40 x 180 (7,160 returns) it reads +5.28,
# comfortably inside. The property did not change -- the estimator was noisy.
# Measuring a tail statistic on too few observations and calling the result a
# model property is the mistake this comment exists to prevent repeating.
UNIVERSE = tradefloor.Universe.random(40, seed=111)


@pytest.fixture(scope="module")
def facts():
    # 252 days, not 180. The envelope certifies at 252 and every band in
    # `REAL_MARKETS` was derived from 252-day windows; 180 was a speed
    # shortcut that happened to agree with the bands under pt-v12 and stopped
    # agreeing under pt-v14, whose `volume_abs_return_corr` sits nearer the
    # floor (see PT_V14's "one regression, stated and accepted"). A fixture
    # measuring at a horizon nothing certifies was asserting a claim the
    # project does not make.
    return measure(seed=3, universe=UNIVERSE, days=252)


# --------------------------------------------------------------------------
# What matches
# --------------------------------------------------------------------------


def test_returns_are_fat_tailed():
    # The most robust fact about asset returns, and the one a Gaussian
    # simulator gets wrong. The re-derived band (+1.6 to +41) is wide
    # because a fourth moment on 252 days is noise-dominated, but the
    # fact itself -- excess kurtosis clearly above Gaussian -- is what
    # this test pins.
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


def test_return_autocorrelation_no_longer_disqualifies_an_agent_ranking():
    """The caveat this test used to pin, now retired by measurement.

    It used to read: "+0.235 at lag one, six seeds of six, +0.221 to +0.240 --
    the mismatch that qualifies every agent ranking this library produces",
    and it instructed whoever saw it move near zero to take the caveat out.
    pt-v3 moved it. Six seeds at this horizon now read a median of +0.065,
    ranging +0.017 to +0.196, against +0.235 before.

    So momentum is no longer mechanically profitable here, and the ranking it
    used to invalidate has rearranged to match: mean-reversion now dominates
    the leaderboard and momentum sits barely above random
    (`test_a_real_difference_separates_and_a_median_gap_may_not`).

    What is still true, and worth keeping pinned, is that the mispricing
    process underneath it has not gone away -- it is weaker, not absent, and
    at this 180-day horizon the statistic is still marginally above its band
    even though it sits inside it at the calibrated 252 days (+0.048 on
    thirty seeds). This asserts the size of the improvement, so a regression
    toward the old model trips it.
    """
    # SIX seeds and a median, not one seed. On one seed at 180 days this
    # statistic has a seed-sd near 0.09, so the threshold below is within
    # noise of the reading and a change of default flips it: seed 3 read
    # +0.164 the day pt-v12 became the default while the thirty-seed median
    # was +0.024, which is a better model failing a worse test. The property
    # this test is named for is a property of the distribution.
    import statistics
    median = statistics.median(
        measure(seed=s, universe=UNIVERSE, days=180)["return_acf1"]
        for s in (1, 2, 3, 4, 5, 6))
    assert median < 0.15, (
        "return autocorrelation has risen back toward the pt-v1 era; the "
        "retired 'momentum is mechanically profitable' caveat may be live "
        f"again (six-seed median {median:.4f})"
    )


def test_the_autocorrelation_is_the_mispricing_process_showing_through():
    """The structural half of the claim, from the model's own function.

    `impulse_response` reports what the AR(2) mispricing process does to a
    shock over time, and it does not decay monotonically -- it RISES, to 1.284
    by day two, before reverting. A shock today is amplified tomorrow.

    That is positive serial correlation by construction, so the measured
    return autocorrelation above is the process showing through in prices
    rather than a coincidence of one seed. Two independent measurements of the
    same mechanism, which makes it an explanation instead of an
    observation.
    """
    response = tradefloor.impulse_response(12)
    assert response[0] == pytest.approx(1.0)
    assert response[1] > response[0]
    assert max(response) > 1.2
    # And it is still stationary, so the amplification reverts rather than
    # running away.
    assert all(modulus < 1.0 for modulus in tradefloor.characteristic_root_moduli())


def test_volatility_clustering_is_in_band_at_short_lags_and_dies_too_fast():
    # This test has now pinned four eras of the same statistic. It pinned
    # clustering BELOW an inherited 0.15-0.35 band; then the GJR
    # recalibration raised |r| acf(1) into that band; then the band
    # re-derivation showed the inherited band was the LONG-SAMPLE textbook
    # value (S&P over 66 years reads ~0.3) where a 252-day window of real
    # large caps reads 0.04-0.18, which left the model reading ABOVE the
    # honest band at lags one and five. pt-v3 brings both inside it: lag one
    # reads 0.130 here against a 0.02-0.22 band, lag five 0.052 against
    # 0.02-0.09.
    #
    # What did NOT get fixed is the SHAPE, the finding worth
    # keeping. The model's clustering is gone by lag twenty -- it reads
    # negative here -- where real markets stay weakly positive out to lag
    # sixty (a 252-day real median of +0.020 at lag 20). Measured across the
    # whole curve the model is 2.6-3.2x real out to lag five and crosses to
    # nothing by twenty: a log-log decay slope of -1.33 against real markets'
    # -0.44. Exponential memory imitating hyperbolic memory. It is
    # structural, no parameter setting fixes it, and the reason the
    # 504-day horizon is the one axis still not perfectly in band.
    # Measured at the CERTIFIED horizon, not at 180 days as this used to be.
    # The bands come from 252-day windows of real data, and clustering is
    # strongly horizon-dependent: on the 2026-08-26 default the same seed
    # reads lag five at 0.098 over 180 days, just past a 0.09 ceiling, and
    # 0.087 over 252. Scoring a 180-day panel against a 252-day band was
    # measuring with the wrong ruler, which is the mistake this file's own
    # header warns about.
    facts = measure(seed=3, universe=UNIVERSE, days=252)
    verdicts = compare_to_real_markets(facts)
    assert verdicts["abs_return_acf1"]["matches"]
    assert verdicts["abs_return_acf5"]["matches"]
    # The shape defect, pinned, and NARROWED at 0.6.0. On this seed pt-v14
    # read lag twenty at -0.0071: the clustering was gone and had crossed to
    # negative. pt-v16 reads +0.0221 against a real 252-day median of +0.020,
    # so the curve now stays weakly positive where real markets do. The
    # slow-variance mixture pt-v15 turned on is the mechanism.
    #
    # Still pinned as a defect rather than a fix, because the decay SLOPE has
    # not been re-measured: `envelope.DECAY_252` and `DECAY_SLOPE` describe
    # pt-v14 and the gap text goes with them. One seed at one lag narrows the
    # claim; it does not retire it.
    assert 0.0 < facts["abs_return_acf20"] < 0.03
    assert facts["abs_return_acf20"] < facts["abs_return_acf5"] < facts[
        "abs_return_acf1"]


def test_volatility_is_in_band_so_raw_percentages_mean_something_now():
    # Was `test_volatility_is_high_so_prefer_ratios_to_raw_percentages`, and
    # it pinned 41.3% annualised against a real 15-36%. That was a property
    # of the deliberately dispersed generated universes AND of an
    # uncalibrated variance process; pt-v3 reads 24.8% here, inside the band,
    # with a six-seed median of 23.8%.
    #
    # Ratios are still the safer quantity to publish -- capture against the
    # Oracle, shortfall in basis points -- because they survive a universe
    # change that a raw percentage does not. But the reason to prefer them is
    # no longer that the level is wrong.
    # Six seeds and a median, for the reason given in the return-acf test
    # above: seed 3 at 180 days read 36.1% against a band ending at 36.0 the
    # day pt-v12 became the default, while its thirty-seed 252-day median is
    # 32.8%. A band check on one short run is a coin toss near the edge.
    import statistics
    runs = [measure(seed=s, universe=UNIVERSE, days=180) for s in (1, 2, 3, 4, 5, 6)]
    median = statistics.median(f["annualised_vol_pct"] for f in runs)
    verdict = compare_to_real_markets(
        {**runs[2], "annualised_vol_pct": median})["annualised_vol_pct"]
    assert verdict["matches"], (
        f"annualised volatility left its band at a six-seed median {median:.1f}%"
    )


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
    for label in tradefloor.facts.LABELS.values():
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
    share grew without being paid for). The re-derived band (0.08 to
    0.56, a real decade's own calm-market spread) is wider than the
    inherited 0.25-0.35 was -- real windows sat outside that one on both
    sides -- so the verdict here is now robustly "matches" rather than
    edge-of-band, on the held-out seeds as well as the published ones.
    The explicit range assertion stays tighter than the band because it
    pins the CALIBRATED near-band-centre regime, not mere membership.
    """
    assert 0.15 < facts["cross_sectional_corr"] < 0.45
    verdict = compare_to_real_markets(facts)["cross_sectional_corr"]
    assert verdict["matches"]


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
    # Measured ACROSS SEEDS, not on one.
    #
    # This test asserted a single seed until 2026-08-28 and passed because
    # that seed suited the preset of the day. It never was a single-seed
    # property: at 252 days pt-v12 leaves the band on seed 6 (0.6603, over
    # the ceiling) and pt-v14 on seeds 3 and 9 (under the floor). Either
    # preset could have failed this on a different fixture seed.
    #
    # The envelope certifies a MEDIAN over thirty seeds, so that is what the
    # claim is and what gets asserted. The per-seed rate is bounded too,
    # because a median can sit mid-band while most seeds sit outside it --
    # which the median alone would not catch.
    from statistics import median
    vals = [measure(seed=s, universe=UNIVERSE, days=252)["volume_abs_return_corr"]
            for s in range(1, 13)]
    mid = median(vals)
    assert 0.46 < mid < 0.66, f"median {mid:.4f} outside the band"
    outside = sum(1 for v in vals if not 0.46 <= v <= 0.66)
    assert outside <= 3, f"{outside} of {len(vals)} seeds outside the band"


def test_the_leverage_effect_is_real_since_the_gjr_term(facts):
    """Bad news raises volatility more than good news, as it should.

    The effect was absent BY CONSTRUCTION while the variance process was a
    symmetric GARCH(1,1) -- squaring the return discards its sign -- and
    this test used to pin that absence. The GJR asymmetry term (GAMMA =
    0.34, garch.rs) made it real: the published six-seed median reads
    -0.085, every seed negative. Under the re-derived band (-0.16 to
    0.00 -- per-name Pearson leverage is a WEAK effect in real data, and
    the inherited -0.30/-0.10 band demanded index-strength from a
    single-name estimator) that median is in band. What this fixture pins
    is that the effect exists and points the right way on a single seed,
    not band membership of a noisy single-seed estimate.
    """
    assert facts["leverage_effect"] < -0.02


def test_a_weak_leverage_effect_would_read_as_weak_not_as_too_high():
    """The wording trap in the statistics whose bands sit at or below zero.

    A leverage effect of +0.05 against a band of -0.16 to 0.00 is
    numerically ABOVE the band and semantically ABSENT -- reversed, even.
    Reporting the numeric direction would print "too high" for a missing
    effect, which states the opposite of the finding. The re-derived band
    top is exactly 0.00 (the mechanical top was positive and was clamped,
    because every retrieved source agrees on the sign), so the wording
    branch keys on `high <= 0` and this value must exercise it. The live
    fixture does not (the GJR term made the effect real and the honest
    band contains it), so the trap is pinned on a synthetic panel -- the
    wording rule has to survive the statistic being healthy.
    """
    facts = measure(seed=3, universe=UNIVERSE, days=180)
    weakened = dict(facts)
    weakened["leverage_effect"] = +0.05
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
    of PURELY independent daily noise around whatever the level is. Real
    volume shocks partly persist, so real markets read about -0.22 to -0.30
    at this estimator -- negative, because differencing a noisy level is
    negatively autocorrelated as arithmetic, but not the -0.5 of noise with
    no persistence at all. (The band this row is judged against was
    RELOCATED at the re-derivation: the inherited one said real markets sit
    near zero here, and no observed real window does.)

    The upper bound on the level doubles as the tripwire for the divergence
    coming back: a compounding level drives the level autocorrelation toward
    +1, so this test fails if `avg_volume` ever feeds back on itself again.
    """
    import statistics

    import pyarrow as pa

    from tradefloor.facts import _autocorrelation, _correlation, _daily_series

    engine = tradefloor.Engine(seed=5, universe=UNIVERSE)
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

    from tradefloor.facts import _daily_series

    engine = tradefloor.Engine(seed=5, universe=UNIVERSE)
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
    facts = measure(seed=2, universe=tradefloor.Universe.random(1, seed=9), days=60)
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

    engine = tradefloor.Engine(seed=5, universe=UNIVERSE)
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
    with pytest.raises(tradefloor.ValidationError):
        measure(seed=1, universe=UNIVERSE, days=1)
    with pytest.raises(tradefloor.ValidationError, match="more days"):
        measure(seed=1, universe=UNIVERSE, days=5, min_observations=100)


def test_the_report_names_the_mismatches_rather_than_scoring_them():
    # A single "realism score" would average a property the model reproduces
    # well against one it gets frankly wrong, and knowing WHICH is the whole
    # value of the exercise.
    text = report(measure(seed=3, universe=UNIVERSE, days=180))
    assert "TOO HIGH" in text
    assert "matches" in text
    assert "momentum is mechanically" in text


def test_correlation_persistence_is_reported_and_judged_with_its_noise_stated():
    """The fourteenth statistic: present, None when the run is too short for
    six windows, a float when it is not, and in every judged table.

    It joined REAL_MARKETS on 2026-08-25 with a thirty-seed baseline and a
    reference band at both horizons (CALIBRATION-FOLLOWUPS.md §64). Twelve
    non-overlapping 21-day windows in a year is a noisy series and the real
    windows scatter from -0.05 to +0.40, so the 252-day band is wide enough
    to admit every preset; the 504-day band is the ruler. Its seed sd is the
    largest in the table, so it sits outside the objective.
    """
    short = measure(seed=2, universe=UNIVERSE, days=60)
    assert "corr_persistence_acf1" in short
    assert short["corr_persistence_acf1"] is None
    year = measure(seed=2, universe=UNIVERSE, days=252)
    assert isinstance(year["corr_persistence_acf1"], float)
    assert -1.0 <= year["corr_persistence_acf1"] <= 1.0
    lo, hi = tradefloor.facts.REAL_MARKETS["corr_persistence_acf1"]
    assert lo < 0.0 < hi, "the 252-day band is wide by measurement, and pinned so"
    assert tradefloor.facts.REAL_MARKETS_504["corr_persistence_acf1"][0] > 0.0
    # The largest seed sd of any correlation-type statistic at both horizons
    # (volatility and kurtosis are in other units). Relative to its band it
    # is NOT the noisiest: abs_return_acf5 is, because the 252-day band here
    # is so wide. Both facts are why it sits outside the objective.
    corr_type = [k for k in tradefloor.facts.REAL_MARKETS
                 if k not in ("annualised_vol_pct", "excess_kurtosis")]
    for table in (tradefloor.facts.SEED_SD, tradefloor.facts.SEED_SD_504):
        assert max(corr_type, key=table.get) == "corr_persistence_acf1"
    assert "corr_persistence_acf1" in compare_to_real_markets(year)
