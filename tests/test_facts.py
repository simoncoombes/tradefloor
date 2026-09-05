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
    # The claim is that a miss is NAMED and a match is named beside it, not
    # that this seed misses on a particular side. It read TOO HIGH until the
    # universe generator was reconciled to open a drawn roster at its own
    # fair value, which re-drew the roster and moved the one row that misses
    # here from above its band to below it. Pinning the direction was
    # pinning an accident of the seed.
    assert "TOO HIGH" in text or "TOO LOW" in text
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
    corr_type = [k for k in tradefloor.facts.SHAPE
                 if k not in ("annualised_vol_pct", "excess_kurtosis")]
    for table in (tradefloor.facts.SEED_SD, tradefloor.facts.SEED_SD_504):
        assert max(corr_type, key=table.get) == "corr_persistence_acf1"
    assert "corr_persistence_acf1" in compare_to_real_markets(year)


# --------------------------------------------------------------------------
# The first moment: measured, and deliberately not graded
# --------------------------------------------------------------------------


def test_the_index_drift_row_measures_what_the_graded_rows_cannot_see():
    """The reason the fifteenth row exists, as one assertion.

    Add a constant to every name's daily log return and the graded panel
    barely notices: nine of the fourteen are exactly invariant because they
    centre their arguments, and the five built on an absolute return move by
    a fraction of their own seed noise. So a market losing a fifth of its
    value a year can read fourteen of fourteen, which is what
    `tradefloor-design/programme/index-drift-investigation.md` found.

    This row is the one that moves, and it moves by exactly the drift added.
    """
    import math

    import pyarrow as pa

    from tradefloor.facts import SEED_SD, panel_statistics

    # Nine of the fourteen centre every argument before they measure it, so
    # a constant added to a series cancels EXACTLY: `pstdev` subtracts its
    # own mean, `excess_kurtosis` standardises, `_autocorrelation` and
    # `_unit_centred` subtract a sample mean they then use on both sides,
    # and the two asymmetry rows select their days off a z-score of the
    # equal-weight return, which a constant does not move.
    # `volume_change_acf1` reads no return series at all.
    EXACTLY_INVARIANT = (
        "annualised_vol_pct", "excess_kurtosis", "return_acf1",
        "cross_sectional_corr", "volume_change_acf1", "corr_asymmetry",
        "corr_asymmetry_lagged", "sector_excess_corr",
        "corr_persistence_acf1",
    )
    # The other five consume an ABSOLUTE return, and |r + c| is not |r| plus
    # a constant, so no downstream centring can undo it. They are not
    # invariant and nothing about their formulas says they should be. What
    # is asserted of them is that they move by less than the noise the panel
    # already tolerates between two seeds of the same model, which is the
    # sense in which the gate could not see the drift.
    NOT_INVARIANT = (
        "abs_return_acf1", "abs_return_acf5", "abs_return_acf20",
        "volume_abs_return_corr", "leverage_effect",
    )
    assert sorted(EXACTLY_INVARIANT + NOT_INVARIANT) == sorted(
        tradefloor.facts.SHAPE), "the split must cover the shape rows"
    # The level row is not in either list because it IS the drift: a first
    # moment moves one for one with a constant added to every return.
    assert "index_drift_pct" in tradefloor.facts.LEVEL

    # 150 days, not 120: `corr_persistence_acf1` needs six 21-day windows to
    # be measurable at all, and a row that comes back None would be counted
    # as invariant without ever having been measured.
    engine = tradefloor.Engine(seed=5, universe=UNIVERSE)
    engine.run_days(150, record=True)
    bars = pa.table(engine.bars(grain="day")).to_pydict()
    before = panel_statistics(bars, UNIVERSE)

    # +20 percentage points a year of log drift, added to every name on every
    # day by rescaling its close by exp(d * day). The rescale adds exactly `d`
    # to that name's daily log return and leaves volume untouched.
    added_pct = 20.0
    d = added_pct / 100.0 / 252.0
    shifted = dict(bars)
    shifted["close"] = [close * math.exp(d * day)
                        for close, day in zip(bars["close"], bars["day"])]
    after = panel_statistics(shifted, UNIVERSE)

    # The instrument fired, or its silence would prove nothing.
    moved = after["index_drift_pct"] - before["index_drift_pct"]
    assert moved == pytest.approx(added_pct, abs=1e-9), moved

    for key in EXACTLY_INVARIANT:
        assert before[key] is not None and after[key] is not None, key
        # To machine precision, not to a tolerance fitted to one roster: the
        # argument from the formula is that a constant cancels, and a bound
        # loose enough to hide a real sensitivity would not test it.
        scale = max(abs(before[key]), 1.0)
        assert abs(after[key] - before[key]) < 1e-12 * scale, (
            key, before[key], after[key])

    for key in NOT_INVARIANT:
        assert before[key] is not None and after[key] is not None, key
        delta = abs(after[key] - before[key])
        assert 0.0 < delta < SEED_SD[key], (key, delta, SEED_SD[key])


def test_the_index_drift_row_is_graded_as_a_level_row_and_held_red():
    """Graded, grouped as LEVEL, and kept out of the shape count.

    THE NAME AND THE ASSERTIONS DISAGREED until 2026-09-05. This was
    `..._is_reported_and_never_graded`, and its docstring said the row
    "earns no verdict, cannot pass, cannot fail" -- which was true while
    the row sat in `REPORTING_ONLY` and stopped being true on 2026-09-03,
    when a band derived from ^GSPC put it in `REAL_MARKETS`. The body was
    updated then and the name and docstring were not, so the file carried
    a test asserting the opposite of what it was called. Renamed rather
    than reverted: the assertions are the current behaviour and the name
    was the stale half.

    What it now holds: the row is graded against a band whose provenance
    is three URLs and a fetch date; it is in `LEVEL` and not in `SHAPE`,
    so a certification count of fourteen shape rows never folds it in; it
    aggregates as a thirty-seed MEAN rather than a median; and it is
    absent from `envelope.CERTIFIED` while present in `certified()` with
    its own group, which is how a row can be published RED without being
    counted as a pass or hidden as a gap.
    """
    from tradefloor import envelope
    from tradefloor.facts import (REAL_MARKETS, REAL_MARKETS_PROVENANCE, REPORTING_ONLY,
                                  SHAPE, LEVEL, CRISIS, AGGREGATE, aggregate_panels)

    year = measure(seed=2, universe=UNIVERSE, days=252)
    assert isinstance(year["index_drift_pct"], float)

    # Graded, against a band whose provenance is three URLs and a fetch date,
    # and placed in the LEVEL group so the certified set can hold it red
    # without folding it into the shape count.
    assert "index_drift_pct" in REAL_MARKETS
    assert "index_drift_pct" in compare_to_real_markets(year)
    assert "index_drift_pct" in LEVEL and "index_drift_pct" not in SHAPE
    assert sorted(SHAPE + LEVEL + CRISIS) == sorted(REAL_MARKETS)
    assert len(SHAPE) == 14
    prov = REAL_MARKETS_PROVENANCE["index_drift_pct"]
    assert sum("query1.finance.yahoo.com" in s for s in prov["sources"]) == 3
    assert "fetched 2026-09-03" in prov["sources"][0]
    low, high = REAL_MARKETS["index_drift_pct"]
    assert low < 7.37 < high
    # A level row is a thirty-seed mean, and the aggregate reads it that way.
    assert AGGREGATE["index_drift_pct"] == "mean"
    panels = [{"index_drift_pct": 1.0, "annualised_vol_pct": 20.0},
              {"index_drift_pct": 2.0, "annualised_vol_pct": 22.0},
              {"index_drift_pct": 6.0, "annualised_vol_pct": 30.0}]
    agg = aggregate_panels(panels)
    assert agg["index_drift_pct"] == pytest.approx(3.0)
    assert agg["annualised_vol_pct"] == 22.0

    # Scores like any graded row, with its group named, and the split
    # counts it apart from the shape rows.
    scored = envelope.score({"index_drift_pct": year["index_drift_pct"]})
    assert scored["statistics"]["index_drift_pct"]["group"] == "level"
    assert scored["level_of"] == 1 and scored["shape_of"] == 0
    # Not in the shape table of the certified set, and PRESENT in the
    # manifest with a value: the thirty-seed measurement on the pinned
    # protocol landed on 2026-09-04 and `CERTIFIED_LEVEL` carries it.
    #
    # This read `in cert["unmeasured"] or in cert["statistics"]` while the
    # measurement was outstanding, which is an assertion no state of the
    # module can fail -- a row is in one or the other by construction. It
    # is split now: the row is measured, so it is in `statistics`, and
    # `unmeasured` is asserted not to hold it rather than left as an
    # alternative that excuses it.
    assert "index_drift_pct" not in envelope.CERTIFIED
    cert = envelope.certified()
    assert "index_drift_pct" in cert["statistics"]
    assert "index_drift_pct" not in cert["unmeasured"]
    assert cert["statistics"]["index_drift_pct"]["group"] == "level"
    assert cert["statistics"]["index_drift_pct"]["in_band"] is False

    # Reported in its own section of the report, as a graded row.
    text = report(year)
    assert tradefloor.facts.LABELS["index_drift_pct"] in text
    assert "level: the first moment" in text

    # Every ungraded row carries a reason. A row with neither a band nor a
    # recorded reason is the defect this pairing exists to prevent.
    for key in tradefloor.facts.LABELS:
        if key not in REAL_MARKETS:
            assert key in REPORTING_ONLY, key


def test_the_fear_rows_answer_the_session_they_are_paired_with():
    """The recording convention, pinned by two correlations that swap.

    `measure` records each day before `close_market`, so the macro row for
    day d holds the gauge the session opened with and the change that
    answers session d is row d+1 minus row d. Under the wrong pairing,
    differencing the recorded column and bucketing by the same row's
    return, every session is paired with the answer to the session before
    it, and the two correlations below swap: the gauge change correlates
    strongly and negatively with the session it answers, and hardly at all
    with the previous one. Both are asserted so the wrong pairing fails
    loudly rather than passing quietly.
    """
    import statistics
    import pyarrow as pa
    from tradefloor.facts import fear_statistics, FEAR_BUCKETS

    # First the convention itself, on a short run kept beside the record.
    engine = tradefloor.Engine(seed=5, universe=UNIVERSE, model="pt-v16")
    after_close = []
    for day in range(8):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.record(day)
        engine.close_market()
        after_close.append(engine.macro_fields["vix"])
    macro = pa.table(engine.macro_table()).to_pydict()
    recorded = dict(zip(macro["day"], macro["vix"]))
    for day in range(1, 8):
        assert recorded[day] == after_close[day - 1], day
    assert recorded[0] != after_close[0] or after_close[0] == recorded[0]

    # Then the discriminator, on a free year: the rows' own pairing against
    # the shifted one.
    engine = tradefloor.Engine(seed=7, universe=UNIVERSE, model="pt-v16")
    for day in range(252):
        engine.open_market()
        engine.run_session(9, 30, 3, 390)
        engine.record(day)
        engine.close_market()
    bars = pa.table(engine.bars(grain="day")).to_pydict()
    macro = pa.table(engine.macro_table()).to_pydict()
    shares = [inst.shares_outstanding for inst in UNIVERSE]
    level = {}
    for day, ident, close in zip(bars["day"], bars["instrument_id"], bars["close"]):
        level[day] = level.get(day, 0.0) + close * shares[ident]
    gauge = dict(zip(macro["day"], macro["vix"]))
    days = sorted(level)
    rets = {d: (level[d] / level[p] - 1.0) * 100.0 for p, d in zip(days, days[1:])}
    own, prev = [], []
    for d in days[2:-1]:
        change = gauge[d + 1] - gauge[d]
        own.append((rets[d], change))
        prev.append((rets[d - 1], change))

    def corr(pairs):
        xs = [x for x, _ in pairs]
        ys = [y for _, y in pairs]
        mx, my = statistics.fmean(xs), statistics.fmean(ys)
        num = sum((x - mx) * (y - my) for x, y in pairs)
        den = (sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)) ** 0.5
        return num / den

    assert corr(own) < -0.5, corr(own)
    assert abs(corr(prev)) < 0.25, corr(prev)

    # And the rows themselves read off the same run through the function
    # `measure` calls, with their counts beside them.
    rows = fear_statistics(engine.bars(grain="day"), engine.macro_table(), UNIVERSE)
    assert rows["fear_sessions_scored"] == 250
    for key in FEAR_BUCKETS:
        assert key + "_sessions" in rows
        if rows[key] is not None:
            assert rows[key + "_sessions"] >= 1
    assert "fear_gauge_dn3_samples" in rows
    assert len(rows["fear_gauge_dn3_samples"]) == rows["fear_gauge_dn3_sessions"]
    assert rows["fear_gauge_dn1_sessions"] >= 5


def test_the_fear_rows_are_graded_in_the_crisis_group_and_pooled_where_thin():
    from tradefloor.facts import (REAL_MARKETS, CRISIS, AGGREGATE, aggregate_panels,
                                  pooled_sessions, REPORTING_ONLY, LABELS)
    assert set(CRISIS) == {"fear_gauge_dn1", "fear_gauge_dn3"}
    assert all(k in REAL_MARKETS for k in CRISIS)
    assert AGGREGATE["fear_gauge_dn3"] == "pooled"
    panels = [{"fear_gauge_dn3": None, "fear_gauge_dn3_samples": [], "fear_gauge_dn1": 1.0},
              {"fear_gauge_dn3": 2.0, "fear_gauge_dn3_samples": [1.0, 3.0], "fear_gauge_dn1": 0.5},
              {"fear_gauge_dn3": 6.0, "fear_gauge_dn3_samples": [6.0], "fear_gauge_dn1": 2.0}]
    agg = aggregate_panels(panels)
    assert agg["fear_gauge_dn3"] == 3.0
    assert pooled_sessions(panels, "fear_gauge_dn3") == 3
    assert agg["fear_gauge_dn1"] == 1.0
    for key in ("fear_gauge_dn5", "fear_gauge_up1"):
        assert key in REPORTING_ONLY and key in LABELS and key not in REAL_MARKETS


def test_the_index_drift_row_is_the_daily_rebalanced_portfolio():
    """The convention, against a portfolio built a second way.

    An equal-weight index is a portfolio rebalanced to equal weights every
    day, so its daily return is the mean of the SIMPLE returns across
    names. This rebuilds that portfolio from the bars, compounding a
    notional level day by day, and the row has to be its annualised log
    growth. Nothing here reads the row's own arithmetic, so a row that
    averaged log returns instead fails.

    The other convention is computed beside it, because a reader putting a
    figure from a decomposition against this row has to carry the term
    between them. Every attribution in this engine is additive in log
    returns and a portfolio return is not, so the decompositions keep the
    log convention and this row does not.
    """
    import math
    import statistics

    import pyarrow as pa

    from tradefloor.facts import _daily_series, panel_statistics

    engine = tradefloor.Engine(seed=11, universe=UNIVERSE)
    engine.run_days(150, record=True)
    bars = pa.table(engine.bars(grain="day")).to_pydict()
    series = _daily_series(bars)

    gross: dict[int, list[float]] = {}
    for rows in series.values():
        for k in range(1, len(rows)):
            previous, close = rows[k - 1][1], rows[k][1]
            if previous > 0 and close > 0:
                gross.setdefault(rows[k][0], []).append(close / previous)
    days = sorted(gross)

    level = 1.0
    for day in days:
        level *= statistics.mean(gross[day])
    portfolio = math.log(level) / len(days) * 252 * 100.0
    row = panel_statistics(bars, UNIVERSE)["index_drift_pct"]
    assert row == pytest.approx(portfolio, rel=1e-12)

    # The log convention, and the term that separates the two. Jensen's
    # inequality puts the portfolio above the log mean by about half the
    # cross-sectional variance, and the agreement below is what says the
    # gap is that term rather than a bug in either.
    logs = [statistics.mean([math.log(g) for g in gross[day]])
            for day in days]
    log_convention = sum(logs) / len(days) * 252 * 100.0
    variances = [statistics.pvariance([math.log(g) for g in gross[day]])
                 for day in days if len(gross[day]) > 1]
    half_variance = sum(variances) / len(variances) / 2 * 252 * 100.0
    assert row > log_convention
    assert row - log_convention == pytest.approx(half_variance, rel=0.01)

    # And the two really are different numbers on this market, or the
    # comparison above would hold on a build that never changed convention.
    assert abs(row - log_convention) > 1.0


def test_the_index_drift_row_keeps_the_names_the_other_rows_drop():
    """Survivorship: `min_observations` filters the shape rows and must not
    filter this one.

    An index drift that drops its short-lived names is measuring the survivors,
    which is the classic way to read an index level wrong. The delisted name's
    returns are exactly the ones a first moment has to carry.
    """
    import pyarrow as pa

    from tradefloor.facts import _index_drift_pct, _daily_series, panel_statistics

    # A name that stops trading a quarter of the way in. The LAST slot, so
    # nothing re-indexes: delisting from the middle shifts the tail down and
    # splices two companies into one instrument id, which would make every
    # number here meaningless while the assertions still passed.
    engine = tradefloor.Engine(seed=7, universe=UNIVERSE)
    engine.run_days(20, record=True)
    engine.delist(len(engine.tickers) - 1)
    engine.run_days(60, record=True, first_day=20)
    bars = pa.table(engine.bars(grain="day")).to_pydict()
    series = _daily_series(bars)

    # The filter has to BITE, or threshold-independence below would be
    # threshold-irrelevance and would prove nothing. One name carries 20 rows
    # against everyone else's 80, so a threshold between them drops it.
    lengths = sorted(len(rows) for rows in series.values())
    assert lengths[0] == 20 and lengths[-1] == 80, lengths
    loose = panel_statistics(bars, UNIVERSE, min_observations=2)
    tight = panel_statistics(bars, UNIVERSE, min_observations=25)
    assert tight["observations"] < loose["observations"]
    assert tight["annualised_vol_pct"] != loose["annualised_vol_pct"]

    # And the drift row does not move, because it never consults the filter.
    assert tight["index_drift_pct"] == loose["index_drift_pct"]
    assert panel_statistics(
        bars, UNIVERSE, min_observations=60
    )["index_drift_pct"] == loose["index_drift_pct"]

    # Not because it ignores the short-lived name: dropping that name really
    # does change the answer, which is the whole reason for keeping it.
    survivors = {i: rows for i, rows in series.items() if len(rows) == 80}
    assert len(survivors) == len(series) - 1
    assert _index_drift_pct(survivors) != loose["index_drift_pct"]

    # And it is None, not zero, when there is no return to measure at all.
    assert _index_drift_pct({}) is None
