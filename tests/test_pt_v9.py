"""pt-v9: pt-v8 with a market that frightens itself.

Eight coefficients move from pt-v8 and nothing else does (calibration record
§68 to §71). What these tests pin is the identity contract and the property
the preset exists for: the VIX reads the day rather than the closing minute.
"""

from __future__ import annotations

import pretium as pt

MOVED = {
    "jump_sigma_market": 0.02,
    "momentum_theta": 0.018551562499999993,
    "vix_cycle_amplitude": 0.6,
    "vix_return_clamp": 15.0,
    "vix_return_gain": 17.0,
    "vix_return_gain_up": 17.0,
    "vix_return_source": 1.0,
    "vix_target_shock_cap": 45.0,
}


def test_it_is_pt_v8_with_eight_coefficients_moved() -> None:
    v8 = pt.ModelParams.from_preset("pt-v8").to_dict()
    v9 = pt.ModelParams.from_preset("pt-v9").to_dict()
    for name, value in MOVED.items():
        assert v9[name] == value, name
    for name in v8:
        if name in MOVED or name == "name":
            continue
        assert v9[name] == v8[name], f"{name} moved and should not have"


def test_it_has_its_own_identity() -> None:
    assert pt.ModelParams.from_preset("pt-v9").fingerprint == "pt-v9"
    assert pt.ModelParams.from_preset("pt-v8", **MOVED).fingerprint == "pt-v9"


def test_the_fear_channel_reads_the_day() -> None:
    """The defect the preset exists for: at source 0.0 the VIX reacts to the
    final tick of the session, which is uncorrelated with the session."""
    v9 = pt.ModelParams.from_preset("pt-v9").to_dict()
    assert v9["vix_return_source"] == 1.0
    assert pt.ModelParams.from_preset("pt-v8").to_dict()["vix_return_source"] == 0.0
    # Symmetric by measurement: the asymmetric gain turned every down day into
    # a level rather than an episode (§71).
    assert v9["vix_return_gain"] == v9["vix_return_gain_up"]


def test_it_is_a_different_market() -> None:
    u = pt.Universe.random(20, seed=7)
    a = pt.Engine(seed=3, universe=u, model="pt-v8"); a.run_days(10)
    b = pt.Engine(seed=3, universe=u, model="pt-v9"); b.run_days(10)
    assert list(a.prices()) != list(b.prices())


def test_the_earlier_presets_are_untouched() -> None:
    u = pt.Universe.random(20, seed=7)
    for name in ("pt-v1", "pt-v3", "pt-v6", "pt-v7", "pt-v8"):
        e = pt.Engine(seed=3, universe=u, model=name)
        e.run_days(5)
        assert e.model_fingerprint == name


def test_it_is_not_the_default() -> None:
    from pretium import envelope
    assert envelope.PRESET == "pt-v3"
    e = pt.Engine(seed=1, universe=pt.Universe.random(3, seed=1))
    assert e.model_fingerprint == "pt-v3"
