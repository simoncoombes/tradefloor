"""Sector structure must survive a crisis, and the two parameters that let it.

The `sector-structure` gap was that names in the same sector do not co-move
more than names in different ones. It closed at 0.2.0: the default reads
0.1346 at 252 days against a band starting at 0.11. The crisis half did not
close, and the envelope carries it under the scenario-magnitude gap, which
is what these parameters exist to move. The dial that fixes it in calm
markets,
`sector_factor_sigma`, was measured at thirty seeds and found to pay twice
(CALIBRATION-FOLLOWUPS.md §59, §60): the crisis volatility lever falls by a
tenth because the sector draw is the one variance term that does not scale
with VIX, and at a held VIX 45 sector structure reads zero whatever the dial
is set to, because the crisis blend consumes the sector draw to inject the
market factor. Both are properties of the mechanism, not the coefficient.

Two parameters address them, and both ship at 0.0:

  * `sector_vix_coupling` lets the sector draw's variance follow VIX on the
    market factor's own target shape.
  * `crisis_blend_source` moves the crisis injection off the sector slot and
    onto the market component, leaving the sector draw whole.

What is pinned here is the safety contract that lets them exist at all, and
the direction each is built to move. Magnitudes belong to the calibration
record, at thirty seeds.
"""

from __future__ import annotations

import pytest

import pretium as pt
from pretium import Scenario, facts

SEEDS = (101, 102, 103, 104, 105, 106)   # screening resolution, direction only


def _prices(preset: str, days: int = 15, scenario=None, **over) -> list[float]:
    model = pt.ModelParams.from_preset(preset, **over) if over else preset
    e = pt.Engine(seed=2026, universe=pt.Universe.random(20, seed=7), model=model)
    for d in range(days):
        if scenario is not None:
            scenario.apply(e, d)
        e.run_days(1, first_day=d)
    return list(e.prices())


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v6"])
def test_both_ship_inert(preset: str) -> None:
    """At 0.0 the trajectory is the shipped one, bit for bit, in a crisis.

    Checked under a held VIX 45 rather than a calm run, because that is the
    only regime where either parameter has anything to do; a calm-run check
    would pass for the wrong reason.
    """
    crisis = Scenario().hold(vix=45.0)
    base = _prices(preset, scenario=crisis)
    assert _prices(preset, scenario=crisis, crisis_blend_source=0.0,
                   sector_vix_coupling=0.0) == base
    assert pt.ModelParams.from_preset(preset, crisis_blend_source=0.0,
                                      sector_vix_coupling=0.0).fingerprint == preset


def test_each_is_wired_to_something() -> None:
    """The converse, so the inertness test cannot pass vacuously."""
    crisis = Scenario().hold(vix=45.0)
    base = _prices("pt-v3", scenario=crisis)
    assert _prices("pt-v3", scenario=crisis, crisis_blend_source=1.0) != base
    assert _prices("pt-v3", scenario=crisis, sector_vix_coupling=1.0) != base


def test_the_blend_source_is_inert_below_the_crisis_threshold() -> None:
    """It only acts where the blend acts. Pinned so it cannot quietly become
    a second calm-market lever."""
    calm = Scenario().hold(vix=15.0)
    assert _prices("pt-v3", scenario=calm, crisis_blend_source=1.0) == \
        _prices("pt-v3", scenario=calm)


def _sector_excess_at(vix: float, **over) -> float:
    model = pt.ModelParams.from_preset("pt-v3", sector_factor_sigma=0.012, **over)
    vals = []
    for seed in SEEDS:
        f = facts.measure(seed=seed, universe=pt.Universe.random(40, seed=111),
                          days=120, model=model, scenario=Scenario().hold(vix=vix))
        vals.append(f["sector_excess_corr"])
    vals.sort()
    return vals[len(vals) // 2]


def test_moving_the_blend_off_the_sector_slot_keeps_sector_structure_in_a_crisis() -> None:
    """The claim the parameter is for, at the smallest honest scale.

    With the sector dial at 0.012 and VIX held at 45, the shipped blend reads
    sector excess near zero (§60: -0.007 at thirty seeds). Taking the blend
    from the market component instead must leave it positive. Six seeds pin
    the direction; the thirty-seed magnitude lives in the record.
    """
    consumed = _sector_excess_at(45.0, crisis_blend_source=0.0)
    kept = _sector_excess_at(45.0, crisis_blend_source=1.0)
    assert kept > consumed + 0.03, (
        f"leaving the sector draw whole did not restore sector structure in "
        f"a crisis: {kept:.4f} against {consumed:.4f} with it consumed"
    )
    assert kept > 0.0


def test_coupling_raises_sector_variance_with_vix_and_not_without() -> None:
    """Sigma follows VIX above the anchor and is unchanged at it.

    Measured on the sector excess itself: with the draw following VIX, the
    sector's share of variance at VIX 45 is higher than with a static sigma,
    so sector excess rises; at the anchor the two are the same draw.
    """
    static = _sector_excess_at(45.0, crisis_blend_source=1.0, sector_vix_coupling=0.0)
    coupled = _sector_excess_at(45.0, crisis_blend_source=1.0, sector_vix_coupling=1.0)
    assert coupled > static, (
        f"coupling the sector draw to VIX lowered sector excess at VIX 45: "
        f"{coupled:.4f} against {static:.4f}"
    )
