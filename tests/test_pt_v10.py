"""pt-v10: pt-v9 with volume that remembers.

The first preset holding all fourteen realism statistics in band at the
certified 252-day horizon, on training seeds and on a held-out universe
(calibration record §73). Two coefficients move from pt-v9.
"""

from __future__ import annotations

import pretium as pt

MOVED = {
    "volume_innovation_sigma": 0.21,
    "volume_persistence": 0.7,
}


def test_it_is_pt_v9_with_two_coefficients_moved() -> None:
    v9 = pt.ModelParams.from_preset("pt-v9").to_dict()
    v10 = pt.ModelParams.from_preset("pt-v10").to_dict()
    for name, value in MOVED.items():
        assert v10[name] == value, name
    for name in v9:
        if name in MOVED or name == "name":
            continue
        assert v10[name] == v9[name], f"{name} moved and should not have"


def test_it_has_its_own_identity() -> None:
    assert pt.ModelParams.from_preset("pt-v10").fingerprint == "pt-v10"
    assert pt.ModelParams.from_preset("pt-v9", **MOVED).fingerprint == "pt-v10"


def test_the_volume_state_is_on() -> None:
    """The mechanism the preset exists for: an AR(1) that shipped switched
    off since pt-v1, because turning it on used to spend a passing
    statistic. On this base both bands are reachable together."""
    v10 = pt.ModelParams.from_preset("pt-v10").to_dict()
    assert v10["volume_persistence"] > 0.0 and v10["volume_innovation_sigma"] > 0.0
    for older in ("pt-v1", "pt-v3", "pt-v8", "pt-v9"):
        d = pt.ModelParams.from_preset(older).to_dict()
        assert d["volume_persistence"] < 0.5, older


def test_it_is_a_different_market() -> None:
    u = pt.Universe.random(20, seed=7)
    a = pt.Engine(seed=3, universe=u, model="pt-v9"); a.run_days(10)
    b = pt.Engine(seed=3, universe=u, model="pt-v10"); b.run_days(10)
    assert list(a.prices()) != list(b.prices())


def test_the_earlier_presets_are_untouched() -> None:
    u = pt.Universe.random(20, seed=7)
    for name in ("pt-v1", "pt-v3", "pt-v7", "pt-v8", "pt-v9"):
        e = pt.Engine(seed=3, universe=u, model=name)
        e.run_days(5)
        assert e.model_fingerprint == name


def test_it_is_the_default() -> None:
    """It became the default on 2026-08-26, the era boundary in §75. An
    engine built with no model runs pt-v10 and the envelope certifies it."""
    from pretium import envelope
    assert envelope.PRESET == "pt-v10"
    e = pt.Engine(seed=1, universe=pt.Universe.random(3, seed=1))
    assert e.model_fingerprint == "pt-v10"
    assert pt.ModelParams.from_preset().fingerprint == "pt-v10"
