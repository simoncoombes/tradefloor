"""The survey classifier must not turn a strength into a recommendation.

`atlas_verdict.py` ranks parameters by the ABSOLUTE rank correlation, so a
parameter that reliably makes every metric worse scores exactly as high as
one that helps. It once printed both as `[MOVES] ... keep and SEARCH it`.

The live case: `market_vol_slow_vix_damp` reads rho 0.274 against the crisis
lever in the survey -- real, strong, reproducible -- and CALIBRATION-FOLLOWUPS
§14 measured that same effect making BOTH scenario metrics monotonically
worse at every fast persistence and every weight tested. The reading and the
refutation are the same fact seen twice; the old label let one overwrite the
other.

These tests pin the fix, because it is a wording fix and wording is exactly
what regresses silently -- nothing crashes when a label starts lying.
"""

import importlib.util
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "atlas_verdict",
    Path(__file__).resolve().parent.parent
    / "tools" / "calibration" / "atlas_verdict.py",
)
verdict = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(verdict)


class _Survey:
    """The only surface `classify` touches."""

    def __init__(self, correlations):
        self._c = correlations

    def sensitivity(self, output):
        return {"correlations": self._c.get(output, {})}


def _classify(param, rho, threshold=0.05):
    s = _Survey({"vol_lever": {param: rho}})
    return verdict.classify(s, param, ["vol_lever"], threshold)


def test_a_refuted_parameter_is_not_reported_as_a_candidate():
    """The whole point. rho 0.274 is a strong reading AND a settled negative."""
    r = _classify("market_vol_slow_vix_damp", 0.2737)
    assert r["verdict"] == "effect detected, direction refuted"
    assert "keep at 0.0" in r["action"]
    assert "§14" in r["action"], "the action must cite what refuted it"


def test_refutation_survives_an_arbitrarily_strong_reading():
    # A bigger number must not promote a refuted parameter back to candidate:
    # strength is not direction, and that is the entire failure this guards.
    for rho in (0.0, 0.5, 0.99, -0.99):
        r = _classify("market_vol_slow_vix_damp", rho)
        assert r["verdict"] == "effect detected, direction refuted", rho


def test_every_refuted_entry_carries_its_evidence():
    # A refutation with no citation is an assertion, and this registry
    # overrides a live measurement -- it has to say on whose authority.
    assert verdict.REFUTED_DIRECTION
    for param, why in verdict.REFUTED_DIRECTION.items():
        assert "§" in why, param
        assert len(why) > 40, param


def test_no_verdict_reads_as_advice():
    """`MOVES` and `keep and SEARCH it` both read as endorsements.

    The classifier cannot endorse: it never sees the sign against a goal,
    only the strength against an output.
    """
    banned = ("moves something", "MOVES", "keep and search")
    for param, rho in [("jump_intensity_market", 0.34),
                       ("news_peer_weight", 0.01),
                       ("regime_stress_points", -0.04),
                       ("market_vol_slow_vix_damp", 0.27)]:
        r = _classify(param, rho)
        text = f"{r['verdict']} {r['action']}".lower()
        for phrase in banned:
            assert phrase.lower() not in text, (param, phrase)


def test_a_live_reading_says_detection_is_not_direction():
    r = _classify("jump_intensity_market", 0.3410)
    assert r["verdict"] == "effect detected"
    assert "not a direction" in r["action"].lower()


def test_the_sign_is_preserved_not_absorbed():
    # Printing |rho| is what let strength pass for usefulness, so the sign
    # has to survive classification for the report to be able to show it.
    assert _classify("jump_mean_market", -0.2322)["rho"] == pytest.approx(-0.2322)
    assert _classify("jump_intensity_market", 0.3410)["rho"] == pytest.approx(0.3410)


def test_untestable_beats_every_other_reading():
    # `regime_stress_points` cannot fire on the standard panel at any value,
    # so both a flat reading and a strong one are artifacts of the panel.
    for rho in (0.0, 0.8):
        r = _classify("regime_stress_points", rho)
        assert r["verdict"] == "untestable here"


def test_below_threshold_is_a_statement_about_the_measurement():
    r = _classify("news_peer_weight", 0.0365)
    assert r["verdict"] == "below screening noise"
    assert "EXCLUDE" in r["action"], "the actionable half is search exclusion"


def test_the_refuted_registry_names_real_parameters():
    # A typo here would silently disable a refutation.
    import pretium as pt

    settable = set(pt.ModelParams.settable())
    for param in verdict.REFUTED_DIRECTION:
        assert param in settable, param
    for spec in verdict.MECHANISMS.values():
        for param in spec["params"]:
            assert param in settable, param
