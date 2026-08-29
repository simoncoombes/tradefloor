"""pt-v8: pt-v7 with the market factor's variance given a memory.

Seven coefficients move from pt-v7 and nothing else does (calibration record
§64, survey4 vector 406). The identity contract: exactly that vector, answers
to its own name only, every earlier preset reproduces bit for bit, not the
default.
"""

from __future__ import annotations

import tradefloor as pt

MOVED = {
    "market_factor_sigma": 0.008829098749522557,
    "market_vol_alpha": 0.2983752950979363,
    "market_vol_beta": 0.6647431226131493,
    "idio_sigma_scale": 0.6525931444846045,
    "jump_intensity_market": 0.07195215610657195,
    "jump_sigma_market": 0.0028601822465565054,
    "sector_factor_sigma": 0.011863939388471967,
}


def test_it_is_pt_v7_with_seven_coefficients_moved() -> None:
    v7 = pt.ModelParams.from_preset("pt-v7").to_dict()
    v8 = pt.ModelParams.from_preset("pt-v8").to_dict()
    for name, value in MOVED.items():
        assert v8[name] == value, name
    for name in v7:
        if name in MOVED or name == "name":
            continue
        assert v8[name] == v7[name], f"{name} moved and should not have"


def test_it_has_its_own_identity() -> None:
    assert pt.ModelParams.from_preset("pt-v8").fingerprint == "pt-v8"
    rebuilt = pt.ModelParams.from_preset("pt-v7", **MOVED)
    assert rebuilt.fingerprint == "pt-v8", (
        "the same coefficients set by hand on pt-v7 do not resolve to pt-v8, "
        "so the preset is not the vector the record describes"
    )


def test_the_factor_garch_moved_toward_a_fourth_moment_and_did_not_reach_it() -> None:
    """pt-v7's factor GARCH has 3a^2 + 2ab + b^2 = 1.42, no fourth moment.
    pt-v8 lowers alpha's share from 0.47 to 0.31 and the index to 1.11, which
    is still above 1: the memory gain is a thirty-seed measurement (§64), not
    a consequence of the moment condition, and the ceiling clamp still does
    work in a crisis. Pinned so the claim cannot quietly become "has one"."""
    def index(name):
        d = pt.ModelParams.from_preset(name).to_dict()
        a, b = d["market_vol_alpha"], d["market_vol_beta"]
        assert a + b < 1.0
        return 3 * a * a + 2 * a * b + b * b
    assert index("pt-v7") > 1.4
    assert 1.0 < index("pt-v8") < 1.2


def test_it_is_a_different_market() -> None:
    u = pt.Universe.random(20, seed=7)
    a = pt.Engine(seed=3, universe=u, model="pt-v7"); a.run_days(10)
    b = pt.Engine(seed=3, universe=u, model="pt-v8"); b.run_days(10)
    assert list(a.prices()) != list(b.prices())


def test_the_earlier_presets_are_untouched() -> None:
    u = pt.Universe.random(20, seed=7)
    for name in ("pt-v1", "pt-v3", "pt-v5", "pt-v6", "pt-v7"):
        e = pt.Engine(seed=3, universe=u, model=name)
        e.run_days(5)
        assert e.model_fingerprint == name


def test_it_is_not_the_default() -> None:
    from tradefloor import envelope
    # The PROPERTY, not the literal: what this test is named for is that
    # pt-v8 is not the certified preset, and that survives an era boundary
    # where a hard-coded successor's name does not.
    assert envelope.PRESET != "pt-v8"
    e = pt.Engine(seed=1, universe=pt.Universe.random(3, seed=1))
    assert e.model_fingerprint != "pt-v8"
