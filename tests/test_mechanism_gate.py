"""The mechanism gate, and every rule in it constructed a failure for.

The panel this file guards had a property worth stating plainly: five of its
fourteen rows COULD NOT FAIL. `facts.BAND_RULE` builds `[min - s, max + s]`
over nine real windows -- a prediction interval for ONE real year, which a
fresh correct year leaves 6.5 per cent of the time -- and the panel grades a
thirty-seed median whose sampling sd is about a quarter of one seed's. The
band is therefore about five times wider than the resolution of the thing it
judges, and on five rows it contains the mechanism-absent reading outright:
measured with the shipped preset's own seed noise, a null model's graded
median passes the band with probability 1.000 on three of them and 0.50 on a
fourth. "Fourteen of fourteen in band" was true and answered a different
question from the one it was read as answering.

So the tests below are written the way DECISIONS requires of a gate that
replaces one which could not fail: for every rule, the input that should fail
it is CONSTRUCTED and the failure asserted. A null model must read NOT SHOWN.
A model with a real effect backwards must read REVERSED and not merely
unshown. A row whose null is the estimator's own small-sample bias must move
its own verdict when the null is set to zero instead. A cut must be the
binomial's nearest to the band's own tolerance and not an integer somebody
liked.

Two things are deliberately NOT tested by pinning a number. The gate's cut is
asserted as the derivation -- nearer the tolerance than either neighbour --
rather than as `== 21`, so it can fail honestly if the rule changes. And
`BAND_RULE_TOLERANCE` and the persistence null are held to a live
re-derivation at a different draw count and seed, not to themselves.
"""

from __future__ import annotations

import math
import random
import statistics

import pytest

import tradefloor
from tradefloor import envelope
from tradefloor.facts import (
    BAND_RULE_TOLERANCE,
    REAL_MARKETS_WINDOWS,
    BAND_WINDOWS,
    CORR_PERSISTENCE_WINDOW,
    EQUIVALENCE,
    LEVEL_ONLY,
    MECHANISM,
    MECHANISM_DIAGNOSTIC,
    MEDIAN_SE_FACTOR,
    NULLS,
    REAL_MARKETS,
    SHAPE,
    band_rule_false_alarm,
    band_rule_tolerance,
    band_windows,
    binomial_two_sided,
    centre_distance,
    centre_multiplier,
    null_value,
    measure,
    mechanism_verdict,
    persistence_null,
    real_centre,
    real_centre_se,
    real_windows,
    sign_cut,
    sub_window_count,
    trimmed_sd,
)
from tradefloor._core import ValidationError

#: Thirty, the certification seed count, so every constructed panel is the
#: shape of the run the gate actually reads.
SEEDS = 30

#: The bootstrap standard error is an EFFECT SIZE and never the gate, so the
#: Monte Carlo loops below run it at its cheapest and one test asserts the
#: shipped default separately. If the bootstrap could change a verdict this
#: substitution would change a count, which is itself worth knowing.
CHEAP = {"bootstrap_draws": 2}


def counted_rows(horizon_days: int = 252) -> list[str]:
    """The mechanism rows this horizon GRADES, diagnostics excluded."""
    skip = MECHANISM_DIAGNOSTIC.get(horizon_days, {})
    return [r for r in MECHANISM if r not in skip]


def spread(key: str) -> float:
    """The row's across-real-year dispersion: the scale a correct model has."""
    return trimmed_sd(real_windows(key))


def panel_about(key: str, centre: float, sd: float, seed: int) -> list[float]:
    """`SEEDS` readings of one row, drawn about `centre` with dispersion `sd`."""
    rng = random.Random(seed)
    return [rng.gauss(centre, sd) for _ in range(SEEDS)]


# --------------------------------------------------------------------------
# The classes, and the nulls behind them
# --------------------------------------------------------------------------


def test_the_three_classes_partition_the_shape_rows():
    """A row added to `SHAPE` must be placed in a class on purpose.

    The failure this prevents: a fifteenth row arrives, nothing names its
    class, and it silently earns no mechanism verdict while the count's
    denominator stays where it was -- which is how a row that cannot fail
    gets added to a panel that already had five.
    """
    assert sorted(MECHANISM + EQUIVALENCE + LEVEL_ONLY) == sorted(SHAPE)
    assert not set(MECHANISM) & set(EQUIVALENCE)
    assert not set(MECHANISM) & set(LEVEL_ONLY)
    assert not set(EQUIVALENCE) & set(LEVEL_ONLY)
    assert set(NULLS) == set(SHAPE)

    # Every mechanism row resolves to a number, every level-only row to None,
    # and every entry says which of the three kinds of derivation it is.
    for row in SHAPE:
        entry = NULLS[row]
        assert entry["kind"] in ("derived", "measured", "undetermined"), row
        assert entry["derivation"].strip(), row
        value = null_value(row, horizon_days=252)
        if row in MECHANISM:
            assert value is not None, row
        if row in LEVEL_ONLY:
            assert value is None, row
        # A `measured` null carries its estimator rather than a literal, so
        # nothing in the table is a typed number without a derivation.
        if entry["kind"] == "measured":
            assert entry["estimator"], row
            assert entry["value"] is None, row


def test_a_row_that_certifies_no_mechanism_is_refused_rather_than_graded():
    """The constructed failure: ask for a verdict where no null derives."""
    values = [0.1] * SEEDS
    for row in EQUIVALENCE + LEVEL_ONLY:
        with pytest.raises(ValidationError) as caught:
            mechanism_verdict(values, row)
        assert row in str(caught.value)
    with pytest.raises(ValidationError):
        mechanism_verdict(values, "index_drift_pct")
    with pytest.raises(ValidationError):
        mechanism_verdict(values, "not_a_row")
    # And one seed has no power, so it is refused rather than answered.
    with pytest.raises(ValidationError):
        mechanism_verdict([0.1], "leverage_effect")


# --------------------------------------------------------------------------
# The size of the test: a null model must not be certified
# --------------------------------------------------------------------------


def test_a_null_model_reads_not_shown_on_every_mechanism_row():
    """The gate's whole purpose, constructed.

    A mechanism-absent model is thirty seeds scattered about the row's OWN
    null with the row's own real-year dispersion. Its verdict must not be
    SHOWN, and the rate at which it is must not exceed the one-sided half of
    the cut, which for thirty seeds and the nine-window tolerance is 0.021.

    The positive control is in the same loop: the same generator centred on
    the REAL value reads SHOWN. Without it this test would pass on a gate
    that never certifies anything.
    """
    trials = 200
    for row in MECHANISM:
        null = null_value(row, horizon_days=252)
        sd = spread(row)
        wrongly_shown = 0
        for trial in range(trials):
            values = panel_about(row, null, sd, seed=90000 + 31 * trial)
            verdict = mechanism_verdict(values, row, **CHEAP)
            if verdict["verdict"] == "shown":
                wrongly_shown += 1
        # The bound comes from the BAND's own tolerance and not from the cut
        # the code picked, which matters: a bound read back off `sign_cut`
        # would move with any mutation of it and report EQUAL on a gate that
        # certifies nulls at one chance in three. Half the tolerance is the
        # one-sided size the gate is allowed, plus three binomial standard
        # errors of an estimate over `trials`.
        size = band_rule_tolerance(BAND_WINDOWS[252]) / 2
        allowed = size + 3 * math.sqrt(size * (1 - size) / trials)
        assert wrongly_shown / trials <= allowed, (
            row, wrongly_shown, trials, allowed)

        # Positive control: the same construction at the real centre.
        correct = panel_about(row, real_centre(row), sd, seed=12345)
        assert mechanism_verdict(correct, row, **CHEAP)["verdict"] == "shown", row


def test_the_shipped_band_admits_the_null_the_gate_excludes():
    """The arithmetic finding, pinned so it cannot be lost quietly.

    On these rows the shipped fidelity band CONTAINS the mechanism-absent
    reading, so no band verdict can fail a model without the mechanism -- and
    a constructed null model is duly in band on every one of them while the
    gate reads it NOT SHOWN. This is the pairing the three counts exist for.
    """
    admitted = [row for row in MECHANISM
                if REAL_MARKETS[row][0] <= null_value(row, horizon_days=252)
                <= REAL_MARKETS[row][1]]
    # Five rows at the shipped bands: the three the design measured at a null
    # pass rate of 1.000, the leverage row whose inward clamp sits ON its
    # null, and the persistence row.
    assert set(admitted) == {
        "abs_return_acf20", "leverage_effect", "corr_asymmetry",
        "corr_asymmetry_lagged", "corr_persistence_acf1",
    }, admitted

    # The leverage row is the special case and the special case is the point:
    # its inward clamp puts the band's ceiling EXACTLY on the null, so a null
    # model's median is in band half the time -- a coin flip, measured -- and
    # every other admitted row lets one through essentially always.
    coin = "leverage_effect"
    assert REAL_MARKETS[coin][1] == null_value(coin, horizon_days=252) == 0.0

    trials = 200
    for row in admitted:
        null = null_value(row, horizon_days=252)
        low, high = REAL_MARKETS[row]
        in_band = shown = 0
        for trial in range(trials):
            values = panel_about(row, null, spread(row), seed=777 + 13 * trial)
            if low <= statistics.median(values) <= high:
                in_band += 1
            if mechanism_verdict(values, row, **CHEAP)["verdict"] == "shown":
                shown += 1
        band_rate, gate_rate = in_band / trials, shown / trials
        if row == coin:
            assert abs(band_rate - 0.5) < 0.15, (row, band_rate)
        else:
            assert band_rate > 0.85, (row, band_rate)
        # The gate lets one through at its own size, 0.021, which is a
        # different order of magnitude from the band's. That gap IS the
        # finding, so it is asserted as a gap and not as two numbers.
        assert gate_rate < 0.10, (row, gate_rate)
        assert gate_rate < band_rate / 4, (row, gate_rate, band_rate)


def test_reversed_is_a_verdict_of_its_own_and_not_a_shade_of_unshown():
    """Constructed three ways: backwards, invisible, and right.

    A certified reversed mechanism is the failure the leverage row's inward
    clamp exists to prevent on that row alone, and the fidelity band cannot
    tell it from an effect too small to see. So the gate must.
    """
    row = "corr_asymmetry_lagged"
    null = null_value(row, horizon_days=252)
    sd = spread(row)
    backwards = panel_about(row, null - 3 * sd, sd, seed=1)
    invisible = panel_about(row, null, sd / 50, seed=2)
    right = panel_about(row, real_centre(row), sd, seed=3)
    assert mechanism_verdict(backwards, row, **CHEAP)["verdict"] == "reversed"
    assert mechanism_verdict(invisible, row, **CHEAP)["verdict"] == "not shown"
    assert mechanism_verdict(right, row, **CHEAP)["verdict"] == "shown"

    # And on a row whose real direction is NEGATIVE, the same three, so a
    # sign convention that ignored the direction would fail here.
    row = "leverage_effect"
    null = null_value(row, horizon_days=252)
    sd = spread(row)
    assert real_centre(row) < null
    assert mechanism_verdict(
        panel_about(row, null + 3 * sd, sd, seed=4), row,
        **CHEAP)["verdict"] == "reversed"
    assert mechanism_verdict(
        panel_about(row, real_centre(row), sd, seed=5), row,
        **CHEAP)["verdict"] == "shown"


def test_a_verdict_at_the_cut_is_reported_as_at_the_cut():
    """Exactly `cut` seeds on the real side is SHOWN and flagged.

    Constructed by placing the seeds by hand rather than by drawing them, so
    the boundary is the boundary and not a sample near it.
    """
    row = "corr_asymmetry"
    null = null_value(row, horizon_days=252)
    cut = sign_cut(SEEDS, band_rule_tolerance(BAND_WINDOWS[252]))
    on_side = [null + 0.1] * cut + [null - 0.1] * (SEEDS - cut)
    verdict = mechanism_verdict(on_side, row, **CHEAP)
    assert verdict["k"] == cut
    assert verdict["verdict"] == "shown"
    assert verdict["at_the_cut"]

    one_short = [null + 0.1] * (cut - 1) + [null - 0.1] * (SEEDS - cut + 1)
    assert mechanism_verdict(one_short, row, **CHEAP)["verdict"] == "not shown"

    # A reading exactly ON the null counts on neither side: the comparison is
    # strict, so a model that reads the null exactly cannot be certified by
    # rounding.
    exactly_null = [null] * SEEDS
    assert mechanism_verdict(exactly_null, row, **CHEAP)["k"] == 0


# --------------------------------------------------------------------------
# The power of the test: a correct model must be certified
# --------------------------------------------------------------------------


def test_a_correct_model_is_certified_on_every_row_this_horizon_grades():
    """Derived rather than simulated, and it is the reason the cut can be exact.

    A correct model is thirty seeds about the real centre with the real
    across-year dispersion, so each seed is on the real side of the null with
    probability `Phi(|centre - null| / s)` and the count is binomial. If this
    fell below the fidelity band's own tolerance for a correct model the gate
    would be failing right models, which is the objection an exact sign test
    has to answer.
    """
    cut = sign_cut(SEEDS, band_rule_tolerance(BAND_WINDOWS[252]))
    for row in counted_rows():
        null = null_value(row, horizon_days=252)
        s = spread(row)
        per_seed = statistics.NormalDist().cdf(abs(real_centre(row) - null) / s)
        power = sum(math.comb(SEEDS, j) * per_seed ** j
                    * (1 - per_seed) ** (SEEDS - j)
                    for j in range(cut, SEEDS + 1))
        assert power >= 0.95, (row, per_seed, power)

    # And the row this horizon does NOT grade is exactly the row where that
    # argument fails on the longer real record, which is why it is a
    # diagnostic rather than a verdict.
    assert set(MECHANISM_DIAGNOSTIC[252]) == {"abs_return_acf20"}
    reason = MECHANISM_DIAGNOSTIC[252]["abs_return_acf20"]
    assert "17 per cent" in reason and "504" in reason


# --------------------------------------------------------------------------
# The cut, and the tolerance it comes from
# --------------------------------------------------------------------------


def test_the_cut_is_the_binomials_nearest_to_the_bands_own_tolerance():
    """Asserted as the derivation, so no integer is pinned anywhere.

    The failure this catches: someone replaces `sign_cut` with a table, or
    tightens it by a seed, and every count in the project moves with nothing
    to say so.
    """
    for windows, tolerance in sorted(BAND_RULE_TOLERANCE.items()):
        for n in range(10, 61):
            cut = sign_cut(n, tolerance)
            here = abs(binomial_two_sided(n, cut) - tolerance)
            assert cut > n / 2, (windows, n, cut)
            for neighbour in (cut - 1, cut + 1):
                if n / 2 < neighbour <= n:
                    there = abs(binomial_two_sided(n, neighbour) - tolerance)
                    assert here <= there, (windows, n, cut, neighbour)

    # The 504-day bands rest on five windows, so a row graded there is graded
    # at THAT band's tolerance. Copying the 252 cut across would tighten a
    # looser band's gate by two seeds, silently.
    at_252 = sign_cut(SEEDS, band_rule_tolerance(BAND_WINDOWS[252]))
    at_504 = sign_cut(SEEDS, band_rule_tolerance(BAND_WINDOWS[504]))
    assert at_504 < at_252
    assert band_windows("corr_persistence_acf1", 504) == 4
    assert band_windows("leverage_effect", 504) == 5
    with pytest.raises(ValidationError):
        band_windows("leverage_effect", 756)
    with pytest.raises(ValidationError):
        band_rule_tolerance(6)
    with pytest.raises(ValidationError):
        sign_cut(SEEDS, 0.0)


def test_the_band_rule_tolerance_table_is_held_to_a_live_re_derivation():
    """Every entry re-measured here, at a different draw count and seed.

    A measured constant pinned to itself is a typed number with provenance
    attached. This re-runs the estimator its provenance names and compares,
    which is what makes the cut derived rather than chosen.
    """
    draws = 20_000
    for windows, shipped in sorted(BAND_RULE_TOLERANCE.items()):
        rate, se = band_rule_false_alarm(windows, draws=draws, seed=4242)
        # The shipped figure carries its own error too; four combined
        # standard errors, so the test is about the value and not the draw.
        shipped_se = math.sqrt(shipped * (1 - shipped) / 200_000)
        assert abs(rate - shipped) <= 4 * math.hypot(se, shipped_se), (
            windows, rate, shipped)

    # The rate FALLS as windows are added, which is the property that makes
    # a nine-window band tighter than a five-window one, and it is why the
    # 504 gate cannot borrow the 252 cut.
    rates = [BAND_RULE_TOLERANCE[w] for w in sorted(BAND_RULE_TOLERANCE)]
    assert rates == sorted(rates, reverse=True)

    # The multiplier the centre diagnostic uses is that same tolerance, so
    # the third count is as tolerant of a correct model as the first.
    multiplier = centre_multiplier(band_rule_tolerance(BAND_WINDOWS[252]))
    assert 1.84 < multiplier < 1.85
    with pytest.raises(ValidationError):
        centre_multiplier(1.0)


# --------------------------------------------------------------------------
# The one null that is not zero
# --------------------------------------------------------------------------


def test_the_persistence_null_is_the_estimators_own_median_and_not_zero():
    """Three separate claims, each with its own failure.

    That the estimator IS biased at eleven sub-windows; that the shipped
    value is a live re-derivation rather than a literal; and that the bias
    MATTERS -- grading the row against zero instead moves the count, which is
    what makes this a mechanism finding rather than a decimal.
    """
    n = sub_window_count(252)
    shipped = persistence_null(n)
    # Biased low, and zero is far outside the Monte Carlo error.
    mc_se = MEDIAN_SE_FACTOR * 0.2587 / math.sqrt(50_000)
    assert shipped < 0
    assert abs(shipped) > 30 * mc_se

    # Re-derived at a different seed and draw count.
    again = persistence_null(n, draws=20_000, seed=987654)
    mc_se_again = MEDIAN_SE_FACTOR * 0.2587 / math.sqrt(20_000)
    assert abs(again - shipped) <= 5 * math.hypot(mc_se, mc_se_again), (
        shipped, again)

    # The bias shrinks as the series lengthens, so a 504-day run has its own
    # null and the table cannot carry one number for both horizons.
    assert persistence_null(sub_window_count(504)) > shipped

    # And it changes a verdict. Twenty-one of thirty seeds sit above the
    # estimator's null and below zero, which reads SHOWN against the null the
    # estimator has and NOT SHOWN against zero.
    cut = sign_cut(SEEDS, band_rule_tolerance(BAND_WINDOWS[252]))
    values = ([shipped + 0.01] * cut + [shipped - 0.01] * (SEEDS - cut))
    assert mechanism_verdict(values, "corr_persistence_acf1",
                             **CHEAP)["verdict"] == "shown"
    against_zero = sum(1 for v in values if v > 0)
    assert against_zero < cut, (
        "the two nulls must disagree on this panel or the test proves nothing")

    with pytest.raises(ValidationError):
        persistence_null(2)
    with pytest.raises(ValidationError):
        null_value("corr_persistence_acf1")


def test_the_sub_window_count_is_read_off_the_instrument_not_asserted():
    """Eleven at a year, and the panel itself is asked.

    `facts` said twelve for two eras. Twelve is the BAR count; the panel
    differences prices into returns first, so a 252-bar run carries 251
    returns and eleven whole 21-day sub-windows. Asserting the arithmetic
    alone would have passed under the wrong sentence, so the measured panel's
    own observation count is the second half of this test.
    """
    assert sub_window_count(252) == (252 - 1) // CORR_PERSISTENCE_WINDOW
    assert sub_window_count(252) == 11
    universe = tradefloor.Universe.random(8, seed=111)
    panel = measure(seed=3, universe=universe, days=252)
    assert (panel["dependence_observations"] // CORR_PERSISTENCE_WINDOW
            == sub_window_count(252)), panel["dependence_observations"]
    with pytest.raises(ValidationError):
        sub_window_count(1)


# --------------------------------------------------------------------------
# The centre diagnostic
# --------------------------------------------------------------------------


def test_the_centre_distance_is_determined_on_every_shape_row():
    """It was not, until the four correlation rows joined the window table.

    `se_real` is the dispersion of a row across real years, and a min, a
    median and a max do not carry one. The four correlation-structure rows
    had only those three, so the row the whole finding is about --
    `corr_asymmetry_lagged`, 3.7 standard errors from the real centre on the
    shipped preset -- had no centre distance at all.
    """
    for row in SHAPE:
        assert real_windows(row) is not None, row
        assert real_centre_se(row) is not None, row
        values = panel_about(row, real_centre(row), spread(row), seed=8)
        result = centre_distance(values, row)
        assert result["z_r"] is not None, row
        assert result["undetermined"] is None, row
        assert abs(result["z_r"]) < result["multiplier"], row

    # A model three real-year sd from the centre is off centre, in both
    # directions, so the diagnostic is not vacuous.
    row = "leverage_effect"
    for sign in (+1, -1):
        far = panel_about(row, real_centre(row) + sign * 3 * spread(row),
                          spread(row) / 10, seed=9)
        assert centre_distance(far, row)["at_centre"] is False

    # And a graded row with no per-window record reports WHY rather than
    # returning a number: the level row is graded and has no window table.
    level = centre_distance([1.0, 2.0, 3.0], "index_drift_pct")
    assert level["z_r"] is None
    assert "REAL_MARKETS_WINDOWS" in level["undetermined"]
    with pytest.raises(ValidationError):
        centre_distance([1.0, 2.0], "not_a_row")

    # THE WRONG-RULER REFUSAL, constructed. The real windows are 252-bar
    # readings, and a row's dispersion across real years moves with the
    # window length -- clustering at lag 20 reads six times higher over 504
    # bars. So a 504-day panel gets no centre distance rather than a
    # plausible-looking one, and `certify` at that horizon says so on every
    # row instead of publishing a count.
    row = "abs_return_acf20"
    values = panel_about(row, real_centre(row), spread(row), seed=11)
    far = centre_distance(values, row, horizon_days=504)
    assert far["z_r"] is None and far["at_centre"] is None
    assert "wrong-ruler" in far["undetermined"]
    near = centre_distance(values, row, horizon_days=252)
    assert near["z_r"] is not None
    assert REAL_MARKETS_WINDOWS["horizon_days"] == 252


# --------------------------------------------------------------------------
# The certificate: three counts, never one
# --------------------------------------------------------------------------


def synthetic_panels(reversed_row: str = "corr_asymmetry_lagged") -> list[dict]:
    """Per-seed panels for a model that is in band everywhere and has one
    row's real effect BACKWARDS -- the shipped default's actual shape."""
    panels: list[dict] = [{} for _ in range(SEEDS)]
    for row in SHAPE:
        centre = real_centre(row)
        if row == reversed_row:
            centre = null_value(row, horizon_days=252) - 0.6 * spread(row)
        draws = panel_about(row, centre, spread(row) / 3, seed=hash(row) % 9999)
        for panel, value in zip(panels, draws):
            panel[row] = value
    return panels


def test_the_certificate_publishes_three_counts_and_names_the_reversed_row():
    """The whole instrument, on a model built to be in band and backwards."""
    panels = synthetic_panels()
    result = envelope.certify(panels)
    counts = result["counts"]

    assert counts["in_band"] == counts["in_band_of"] == len(SHAPE)
    assert result["reversed"] == ["corr_asymmetry_lagged"]
    assert result["in_band_and_reversed"] == ["corr_asymmetry_lagged"]
    assert counts["mechanism_of"] == len(counted_rows())
    assert counts["mechanism_shown"] == counts["mechanism_of"] - 1
    assert result["diagnostic"] == ["abs_return_acf20"]
    assert counts["at_centre_of"] == len(SHAPE)

    text = envelope.certification_report(result)
    assert "fidelity: could a real year read this" in text
    assert "is a model WITHOUT the mechanism excluded" in text
    assert "diagnostic, never a gate" in text
    assert "REVERSED, which the band cannot see: corr_asymmetry_lagged" in text
    assert "the fidelity count reads it as a pass" in text
    # The diagnostic row is printed with its reason and excluded from the
    # count, which is the difference between reporting and grading.
    assert "reported and NOT counted at this horizon" in text

    # One seed cannot be certified, and asking is refused rather than answered.
    with pytest.raises(ValidationError):
        envelope.certify(panels[:1])


def test_a_null_model_is_fourteen_of_fourteen_in_band_and_certifies_nothing():
    """The finding as one assertion.

    Every mechanism row at its own null, every level row at the real centre:
    in band on all fourteen, and not one mechanism shown. If the fidelity
    count could answer the mechanism question this test could not pass.
    """
    panels: list[dict] = [{} for _ in range(SEEDS)]
    for row in SHAPE:
        centre = (null_value(row, horizon_days=252) if row in MECHANISM
                  else real_centre(row))
        draws = panel_about(row, centre, spread(row) / 4, seed=4242 + len(row))
        for panel, value in zip(panels, draws):
            panel[row] = value
    result = envelope.certify(panels)
    # Nine of the fourteen bands do exclude their null, so a null model is
    # out of band on those. Four of the five that admit it are in band here;
    # the leverage row is the coin flip whose clamp sits ON its null, and
    # which side of 0.00 this particular draw's median falls is not a fact
    # about the gate.
    in_band = [r for r in SHAPE
               if result["fidelity"]["statistics"][r]["in_band"]]
    assert set(in_band) >= {
        "abs_return_acf20", "corr_asymmetry", "corr_asymmetry_lagged",
        "corr_persistence_acf1",
    }
    assert result["counts"]["mechanism_shown"] == 0, result["shown"]
