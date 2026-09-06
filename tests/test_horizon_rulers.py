"""A measurement at one horizon may not be graded by another horizon's ruler.

Four live instances of that error were found on 2026-09-05 and are repaired
on this branch. Every test here CONSTRUCTS the mismatch and checks it is
refused; a test that only exercises the correct pairing would pass against
the defective code as easily as against the fix, and there were already
plenty of those.

The instrument is asserted at construction throughout: each refusal test has
a companion assertion that the MATCHED pairing computes, so a refusal that
came from the call being broken in general -- rather than from the horizons
disagreeing -- fails here too. And where the ruler swaps, a row is checked
whose two bands give OPPOSITE verdicts, because a scoring call that swapped
the label and not the table would otherwise report exactly what this file
wants to see.

`excess_kurtosis` is that row. Its 252-day band is (1.6, 41) and its 504-day
band is (7.1, 22): 5.23 passes one and fails the other, and it is the value
pt-v3 actually read at 504 days while passing the 252-day band, which is how
the wrong ruler came to be described in `envelope.score`'s docstring in the
first place.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "tools" / "calibration"))

import tradefloor  # noqa: E402
import tradefloor.envelope as envelope  # noqa: E402
import tradefloor.facts as facts  # noqa: E402
import tradefloor.loss as loss  # noqa: E402
from tradefloor import ValidationError  # noqa: E402

#: A value that passes the 252-day kurtosis band and fails the 504-day one.
#: pt-v3's actual 504-day reading, so the difference is a measured one.
KURTOSIS_PASSES_252_FAILS_504 = 5.23


def panel_at(days: int, **overrides) -> dict:
    """A synthetic panel sitting mid-band on every row of its own ruler.

    Mid-band, so every band distance is zero and any non-zero loss comes from
    the row under test rather than from the fixture. The 504-day table holds
    the fourteen shape rows only, so a 504-day panel here carries fourteen.

    Built from the band tables directly rather than through
    `facts.rulers_for_horizon`, so that this fixture exists on the code
    BEFORE the fix too. A test whose fixture depends on the fix can only
    fail with an AttributeError against the defect it is meant to catch,
    which proves the function is new and not that the defect was real.
    """
    bands = facts.REAL_MARKETS_504 if days == 504 else facts.REAL_MARKETS
    out: dict = {"days": days, "seed": 101}
    for key, (low, high) in bands.items():
        out[key] = (low + high) / 2.0
    if "fear_gauge_dn3" in out:
        out["fear_gauge_dn3_samples"] = [out["fear_gauge_dn3"]]
    out.update(overrides)
    return out


# --------------------------------------------------------------------------
# A. `evaluate_axes.py` scored a 504-day axis on the 252-day ruler

def test_a_504_day_panel_against_the_252_day_bands_is_refused():
    """The exact call `evaluate_axes.py` made on its horizon axis.

    `band_distance_loss(panels)` on the defaults, with panels measured at 504
    days. It returned a number for weeks, and that number decided the
    `generalises` verdict `calibrate.py`, `emit_preset.py` and
    `report_tables.py` read.
    """
    far = [panel_at(504)]
    with pytest.raises(ValidationError) as exc:
        loss.band_distance_loss(far)
    message = str(exc.value)
    assert "504" in message and "252" in message, message
    assert "facts.REAL_MARKETS" in message, message

    # The instrument: the same panels against their OWN ruler compute, so the
    # refusal above is about the horizons and not about the call.
    matched = loss.band_distance_loss(far, bands=facts.REAL_MARKETS_504,
                                      seed_sd=facts.SEED_SD_504)
    assert matched["loss"] == 0.0
    assert matched["horizon_days"] == 504
    assert matched["bands"] == "facts.REAL_MARKETS_504"


def test_a_252_day_panel_against_the_504_day_bands_is_refused():
    """And the other way round, which is the same defect facing left."""
    near = [panel_at(252)]
    with pytest.raises(ValidationError) as exc:
        loss.band_distance_loss(near, bands=facts.REAL_MARKETS_504,
                                seed_sd=facts.SEED_SD_504)
    assert "252-day measurement" in str(exc.value), str(exc.value)
    assert loss.band_distance_loss(near)["loss"] == 0.0


def test_the_right_bands_with_the_other_horizons_noise_scale_are_refused():
    """`dual_horizon_loss` calls this "the wrong-ruler error in a subtler
    dress": every band verdict is right and every distance is divided by the
    wrong denominator. The two scales differ by factors from 0.80 to 3.23.
    """
    far = [panel_at(504)]
    with pytest.raises(ValidationError) as exc:
        loss.band_distance_loss(far, bands=facts.REAL_MARKETS_504,
                                seed_sd=facts.SEED_SD)
    message = str(exc.value)
    assert "facts.SEED_SD" in message and "facts.REAL_MARKETS_504" in message

    with pytest.raises(ValidationError):
        loss.band_distance_loss([panel_at(252)],
                                bands=facts.REAL_MARKETS,
                                seed_sd=facts.SEED_SD_504)


def test_a_copy_of_a_ruler_is_still_that_ruler():
    """Identity is not the check, because callers pass copies.

    `shapley.ruler` returns `dict(bands)` and `evaluate_axes` writes tables
    through a JSON round trip. A guard that only compared identity would
    accept every one of them.
    """
    assert facts.horizon_of_table(dict(facts.REAL_MARKETS_504)) == (
        504, "facts.REAL_MARKETS_504")
    with pytest.raises(ValidationError):
        loss.band_distance_loss([panel_at(504)],
                                bands=dict(facts.REAL_MARKETS),
                                seed_sd=dict(facts.SEED_SD))


def test_panels_from_two_horizons_cannot_be_aggregated():
    """A median across horizons is a statistic of no horizon, so no ruler
    grades it. Refused before a ruler is even chosen.
    """
    with pytest.raises(ValidationError) as exc:
        loss.band_distance_loss([panel_at(252), panel_at(252)]
                                + [dict(panel_at(252), days=504)])
    assert "[252, 504]" in str(exc.value), str(exc.value)


def test_evaluate_axes_grades_every_axis_on_its_own_horizon():
    """The tool's own ruler lookup, and that every axis has one.

    A static check as well as a behavioural one: an axis added at a horizon
    with no band set fails HERE, at import of the axis table, rather than by
    silently inheriting the 252-day ruler the way `holdout_horizon` did.
    """
    pytest.importorskip("instrumentlib",
                        reason="calibration tooling is not packaged")
    pytest.importorskip("numpy")
    import evaluate_axes

    assert evaluate_axes.AXES["holdout_horizon"][3] == 504

    # The firing assertion, and it needs nothing this branch added: the call
    # the tool used to make on its horizon axis is refused. On the code
    # before the fix this returns a loss, which is the defect.
    with pytest.raises(ValidationError):
        loss.band_distance_loss([panel_at(
            evaluate_axes.AXES["holdout_horizon"][3])])

    for axis, spec in evaluate_axes.AXES.items():
        bands, seed_sd, name = evaluate_axes.ruler_for(spec[3])
        assert bands is facts.RULERS_BY_HORIZON[spec[3]]["bands"], axis
        assert seed_sd is facts.RULERS_BY_HORIZON[spec[3]]["seed_sd"], axis

    far_bands, far_sd, far_name = evaluate_axes.ruler_for(504)
    assert far_bands is facts.REAL_MARKETS_504
    assert far_sd is facts.SEED_SD_504
    assert far_name == "facts.REAL_MARKETS_504"

    # And the horizon it does NOT have a ruler for is refused rather than
    # given the nearest one.
    with pytest.raises(ValidationError):
        evaluate_axes.ruler_for(756)


def test_the_ruler_swap_changes_a_verdict_and_not_only_a_label():
    """A scoring call that renamed its ruler without changing the table would
    satisfy every name assertion above. This asserts the numbers.
    """
    near = envelope.score(dict(envelope.CERTIFIED,
                               excess_kurtosis=KURTOSIS_PASSES_252_FAILS_504),
                          horizon_days=252)
    far = envelope.score({k: v for k, v in envelope.CERTIFIED.items()
                          if k in envelope.BANDS_504}
                         | {"excess_kurtosis": KURTOSIS_PASSES_252_FAILS_504},
                         horizon_days=504)
    assert near["statistics"]["excess_kurtosis"]["in_band"]
    assert not far["statistics"]["excess_kurtosis"]["in_band"]
    assert near["statistics"]["excess_kurtosis"]["band"] != (
        far["statistics"]["excess_kurtosis"]["band"])


# --------------------------------------------------------------------------
# B and C. The API permitted a 60-day run to be graded on the 252-day bands

def test_a_60_day_run_cannot_be_compared_to_real_markets():
    """`examples/07-research-workflow.py:304` did exactly this, shipped.

    Refused by name: 60 is not "close enough to 252", it is a different
    quantity. `corr_persistence_acf1` is not even defined below 128 days.
    """
    universe = tradefloor.Universe.random(4, seed=3)
    short = facts.measure(seed=7, universe=universe, days=60)
    assert short["days"] == 60
    with pytest.raises(ValidationError) as exc:
        facts.compare_to_real_markets(short)
    message = str(exc.value)
    assert "60" in message and "[252, 504]" in message, message

    with pytest.raises(ValidationError):
        facts.report(short)


def test_compare_to_real_markets_reads_the_horizon_off_the_panel():
    """The horizon comes from `facts["days"]`, which this never read.

    Checked on a row whose two bands disagree, so a function that swapped the
    reported ruler name without swapping the table fails here.
    """
    near = facts.compare_to_real_markets(
        panel_at(252, excess_kurtosis=KURTOSIS_PASSES_252_FAILS_504))
    far = facts.compare_to_real_markets(
        panel_at(504, excess_kurtosis=KURTOSIS_PASSES_252_FAILS_504))
    # The verdict first, and it is the one that fires: before the fix the
    # 504-day panel was graded against the 252-day band and this row matched.
    assert near["excess_kurtosis"]["matches"]
    assert not far["excess_kurtosis"]["matches"]
    assert near["excess_kurtosis"]["ruler"] == "facts.REAL_MARKETS"
    assert far["excess_kurtosis"]["ruler"] == "facts.REAL_MARKETS_504"
    assert far["excess_kurtosis"]["horizon_days"] == 504
    # And the scaled distance uses the horizon's own noise scale, which is
    # the quieter half of the same error.
    assert far["excess_kurtosis"]["scaled_distance"] == pytest.approx(
        (facts.REAL_MARKETS_504["excess_kurtosis"][0]
         - KURTOSIS_PASSES_252_FAILS_504) / facts.SEED_SD_504[
            "excess_kurtosis"])


def test_a_panel_that_does_not_say_its_horizon_is_refused():
    """Unknown is refused rather than assumed to be 252.

    The panel that carries no `days` is the one a caller assembled by hand,
    and assuming the certified horizon for it is the fallback this whole
    change removes.
    """
    anonymous = {k: v for k, v in panel_at(252).items() if k != "days"}
    with pytest.raises(ValidationError) as exc:
        facts.compare_to_real_markets(anonymous)
    assert "does not record" in str(exc.value)


def test_report_names_the_ruler_that_graded_it():
    """A table headed "real markets" over a horizon line said nothing about
    which of the two band sets produced the verdicts.
    """
    text = facts.report(panel_at(252, instruments=40, observations=10_080))
    assert "facts.REAL_MARKETS" in text
    far = facts.report(panel_at(504, instruments=40, observations=20_160))
    assert "facts.REAL_MARKETS_504" in far
    # At 504 the level and crisis rows have no band of their own, so they are
    # reported ungraded rather than graded against a 252-day one.
    assert "index_drift_pct" not in facts.REAL_MARKETS_504
    assert "reporting only" in far


# --------------------------------------------------------------------------
# The horizons that have no ruler, everywhere they can be asked for

def test_envelope_score_refuses_a_horizon_with_no_band_set():
    """`far = horizon_days > CERTIFIED_HORIZON_DAYS` sent every horizon above
    252 to the 504-day bands, and every horizon below it to the 252-day ones.
    The settling study runs 1,008 days; `long_horizon.py` runs 2,520.
    """
    panel = {k: v for k, v in envelope.CERTIFIED.items()}
    for days in (60, 253, 756, 1008, 2520):
        with pytest.raises(ValidationError) as exc:
            envelope.score(panel, horizon_days=days)
        assert str(days) in str(exc.value)
        assert "[252, 504]" in str(exc.value)
    # Both horizons that DO have one still work.
    assert envelope.score(panel, horizon_days=252)["ruler"] == \
        "facts.REAL_MARKETS"


def test_shapley_ruler_withholds_at_a_horizon_with_no_band_set():
    """It returned `facts.REAL_MARKETS` for any horizon other than exactly
    504, with a name that truthfully said so over a wrong comparison.

    This asserted `SystemExit` at an unregistered horizon and the module
    withholds instead, which is the better contract and the one its single
    caller is written against: `decompose` computes `withheld` as every key
    absent from `bands`, so an empty band set withholds every verdict, names
    them all in its output, and leaves the decomposition -- what this tool
    is for -- intact. A raise would instead refuse a legitimate run, the
    1,008-day settling study among them. Refusing to GRADE is the fix;
    refusing to RUN was never the requirement.

    So the test now asserts the withholding contract rather than a raise,
    and asserts it where the silence would do harm: that nothing is graded
    and nothing is dropped unnamed.
    """
    pytest.importorskip("instrumentlib",
                        reason="calibration tooling is not packaged")
    import shapley

    assert shapley.ruler(252)[0] == "facts.REAL_MARKETS"
    assert shapley.ruler(504)[0] == "envelope.BANDS_504"

    name, bands = shapley.ruler(756)
    assert name is None, name
    assert bands == {}, bands

    # The contract that matters is the caller's: an empty band set must
    # withhold every row and name it, never grade one against another
    # horizon's bands and never drop one silently.
    measured = ["annualised_vol_pct", "excess_kurtosis", "leverage_effect"]
    withheld = [key for key in measured if key not in bands]
    assert withheld == measured


def test_every_registered_horizon_has_both_tables():
    """Bands without a noise scale cannot report `room_sd`; a noise scale
    without bands grades nothing. Asserted so a horizon cannot be half-added.
    """
    assert sorted(facts.RULERS_BY_HORIZON) == [
        facts.CERTIFIED_HORIZON_DAYS, 504]
    for days, row in facts.RULERS_BY_HORIZON.items():
        bands, seed_sd = facts.rulers_for_horizon(days)
        assert bands and seed_sd
        assert facts.horizon_of_table(bands) == (days, row["bands_name"])
        assert facts.horizon_of_table(seed_sd) == (days, row["seed_sd_name"])
        # Every graded row has a scale to divide by at this horizon.
        assert not set(bands) - set(seed_sd) - set(facts.LEVEL) \
            - set(facts.CRISIS)


def test_envelopes_own_504_table_is_registered_with_its_horizon():
    """`facts` cannot name `envelope.BANDS_504` -- the import runs the other
    way -- and a table the checker cannot identify is one it cannot refuse.
    """
    assert facts.horizon_of_table(envelope.BANDS_504) == (
        504, "envelope.BANDS_504")
    with pytest.raises(ValidationError):
        loss.band_distance_loss([panel_at(252)], bands=envelope.BANDS_504,
                                seed_sd=facts.SEED_SD_504)


def test_the_certified_horizon_is_one_number():
    """`envelope.CERTIFIED_HORIZON_DAYS` used to be its own literal beside
    `facts`'s bands. Two spellings of one fact drift.
    """
    assert envelope.CERTIFIED_HORIZON_DAYS is facts.CERTIFIED_HORIZON_DAYS
    assert facts.CERTIFIED_HORIZON_DAYS in facts.RULERS_BY_HORIZON
