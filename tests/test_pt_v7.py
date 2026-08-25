"""pt-v7: pt-v6 with industries that survive a crisis.

Six coefficients move from pt-v6 and nothing else does. What these tests pin
is the identity contract every preset carries: it is exactly the vector the
calibration record describes (CALIBRATION-FOLLOWUPS.md §62), it answers to
its own name and to no other, every earlier preset reproduces bit for bit,
and it is not the default.
"""

from __future__ import annotations

import pretium as pt

MOVED = {
    "sector_factor_sigma": 0.012,
    "crisis_blend_source": 1.0,
    "sector_vix_coupling": 0.25,
    "crisis_blend_cap": 0.98,
    "market_vol_ceiling_multiple": 16.0,
}


def test_it_is_pt_v6_with_five_coefficients_moved() -> None:
    v6 = pt.ModelParams.from_preset("pt-v6").to_dict()
    v7 = pt.ModelParams.from_preset("pt-v7").to_dict()
    for name, value in MOVED.items():
        assert v7[name] == value, name
    assert v7["idio_sigma_scale"] == v6["idio_sigma_scale"] * 0.9
    for name in pt.ModelParams.settable():
        if name in MOVED or name == "idio_sigma_scale":
            continue
        assert v7[name] == v6[name], f"{name} moved and should not have"


def test_it_has_its_own_identity() -> None:
    assert pt.ModelParams.from_preset("pt-v7").fingerprint == "pt-v7"
    rebuilt = pt.ModelParams.from_preset(
        "pt-v6", idio_sigma_scale=pt.ModelParams.from_preset("pt-v6").to_dict()["idio_sigma_scale"] * 0.9, **MOVED)
    assert rebuilt.fingerprint == "pt-v7", (
        "the same coefficients set by hand on pt-v6 do not resolve to pt-v7, "
        "so the preset is not the vector the record describes"
    )


def test_it_is_a_different_market() -> None:
    u = pt.Universe.random(20, seed=7)
    a = pt.Engine(seed=3, universe=u, model="pt-v6"); a.run_days(10)
    b = pt.Engine(seed=3, universe=u, model="pt-v7"); b.run_days(10)
    assert list(a.prices()) != list(b.prices())


def test_the_earlier_presets_are_untouched() -> None:
    """Registering a preset must not move any other. The known-answer test
    pins the default; this pins the rest at the cheapest scale."""
    u = pt.Universe.random(20, seed=7)
    for name in ("pt-v1", "pt-v3", "pt-v5", "pt-v6"):
        e = pt.Engine(seed=3, universe=u, model=name)
        e.run_days(5)
        assert e.model_fingerprint == name


def test_it_is_not_the_default() -> None:
    from pretium import envelope
    assert envelope.PRESET == "pt-v3"
    e = pt.Engine(seed=1, universe=pt.Universe.random(3, seed=1))
    assert e.model_fingerprint == "pt-v3"
