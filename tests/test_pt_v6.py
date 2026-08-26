"""pt-v6: pt-v5 with the herding term halved.

`momentum_theta` multiplies yesterday's change in mispricing into today's,
which is return continuation by construction. pt-v5 fixed one-year
continuation by stopping jumps feeding it and left the two-year reading out
of band. Halving the term fixes the two-year reading.

These pin the identity and the arithmetic. The realism claims live in
CALIBRATION-FOLLOWUPS §49 to §51 and are measured on thirty seeds, which is
too slow to assert here; what is asserted here is that the preset IS what
those sections measured.
"""

from __future__ import annotations

import pretium as pt


def test_it_is_exactly_pt_v5_with_theta_halved() -> None:
    """One coefficient, and it is exactly half.

    A literal that drifted from the value it claims to halve would be a
    vector nobody calibrated wearing a name somebody did.
    """
    v5 = pt.ModelParams.from_preset("pt-v5").to_dict()
    v6 = pt.ModelParams.from_preset("pt-v6").to_dict()
    differing = [k for k in v5 if k != "name" and v5[k] != v6[k]]
    assert differing == ["momentum_theta"], (
        f"pt-v6 should differ from pt-v5 in momentum_theta alone, got {differing}"
    )
    assert v6["momentum_theta"] * 2 == v5["momentum_theta"]


def test_it_has_its_own_identity() -> None:
    assert pt.ModelParams.from_preset("pt-v6").fingerprint == "pt-v6"
    assert pt.ModelParams.from_preset("pt-v5").fingerprint == "pt-v5"


def test_it_is_a_different_market() -> None:
    """Registering a preset that reproduced an existing one would be a lie."""
    u = pt.Universe.random(20, seed=7)

    def prices(preset: str) -> list[float]:
        e = pt.Engine(seed=2026, universe=u, model=preset)
        e.run_days(10)
        return list(e.prices())

    assert prices("pt-v6") != prices("pt-v5")
    assert prices("pt-v6") != prices("pt-v3")


def test_the_earlier_presets_are_untouched() -> None:
    """The whole reason coefficient changes arrive as new presets.

    Every published result citing pt-v3 depends on pt-v3 still running the
    way it ran, and adding a preset must not disturb one.
    """
    u = pt.Universe.random(20, seed=7)
    for preset, expected_fp in (("pt-v1", "pt-v1"), ("pt-v3", "pt-v3"),
                                ("pt-v4", "pt-v4"), ("pt-v5", "pt-v5")):
        assert pt.ModelParams.from_preset(preset).fingerprint == expected_fp
    e1 = pt.Engine(seed=2026, universe=u, model="pt-v3")
    e1.run_days(10)
    e2 = pt.Engine(seed=2026, universe=u, model="pt-v3")
    e2.run_days(10)
    assert list(e1.prices()) == list(e2.prices())


def test_it_is_not_the_default() -> None:
    """Passing the controls is not certification.

    The envelope certifies pt-v3 at 252 days. pt-v6 clears §8 and scenario
    response, and that is a different claim from being certified.
    """
    # The PROPERTY, not the literal. This read `== "pt-v10"` and had to be
    # edited at every era boundary; asserting what the test is named for
    # survives the next one.
    assert pt.model_preset()["name"] != "pt-v6"


def test_the_ar2_stays_stationary() -> None:
    """Halving theta cannot break stationarity, but assert rather than argue.

    The mispricing process is an AR(2) with a1 = phi + theta and a2 = -theta,
    stationary for 0 <= phi, theta < 1. A preset that violated it would
    produce a market that diverges rather than one that is merely wrong.
    """
    d = pt.ModelParams.from_preset("pt-v6").to_dict()
    phi, theta = d["mispricing_phi"], d["momentum_theta"]
    assert 0.0 <= theta < 1.0
    assert 0.0 <= phi < 1.0
    a1, a2 = phi + theta, -theta
    assert a2 > -1.0 and a1 + a2 < 1.0 and a2 - a1 < 1.0
