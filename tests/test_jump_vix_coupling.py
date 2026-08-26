"""A jump's arrival rate should know what regime it is in.

Gap: both jump intensities are per-day probabilities that ignore the VIX, so
the number of jump days in a dead-calm market and in a panic is the same.
Decomposing the nine attribution components under a pinned VIX measured the
cost (CALIBRATION-FOLLOWUPS.md §84): jumps carry 40.5% of the variance of a
market pinned at VIX 5 and 1.1% of one pinned at VIX 65, on 3003 and 2998
jump day-cells. Real markets cluster their jumps into crises, and this is
also the floor under the calm end of the crisis lever.

What is pinned here is the safety contract that lets the dial exist, and the
direction it is built to move. Magnitudes belong to the calibration record,
at thirty seeds.
"""

from __future__ import annotations

import pyarrow as pa
import pytest

import pretium as pt
from pretium import Scenario


def _prices(vix: float, days: int = 12, **over) -> list[float]:
    model = pt.ModelParams.from_preset("pt-v10", **over) if over else "pt-v10"
    e = pt.Engine(seed=99, universe=pt.Universe.random(20, seed=7), model=model)
    s = Scenario().hold(vix=vix)
    for d in range(days):
        s.apply(e, d)
        e.run_days(1, first_day=d)
    return list(e.prices())


def _jump_cells(vix: float, days: int = 60, **over) -> int:
    """How many instrument-days a jump actually fired on."""
    model = pt.ModelParams.from_preset("pt-v10", **over) if over else "pt-v10"
    e = pt.Engine(seed=4, universe=pt.Universe.random(25, seed=3), model=model)
    s = Scenario().hold(vix=vix)
    for d in range(days):
        s.apply(e, d)
        e.run_days(1, first_day=d)
    t = pa.table(e.truth())
    return sum(1 for v in t["jump"].to_pylist() if v != 0.0)


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v10"])
def test_it_ships_inert(preset: str) -> None:
    """At 0.0 the trajectory is the shipped one, bit for bit, in a crisis.

    Checked under a held VIX 45 rather than a calm run, because a coupling
    to the VIX has nothing to do at the anchor and a calm check would pass
    for the wrong reason.
    """
    e = pt.Engine(seed=99, universe=pt.Universe.random(20, seed=7), model=preset)
    crisis = Scenario().hold(vix=45.0)
    for d in range(12):
        crisis.apply(e, d)
        e.run_days(1, first_day=d)
    base = list(e.prices())

    m = pt.ModelParams.from_preset(preset, jump_vix_coupling=0.0)
    e2 = pt.Engine(seed=99, universe=pt.Universe.random(20, seed=7), model=m)
    for d in range(12):
        crisis.apply(e2, d)
        e2.run_days(1, first_day=d)
    assert list(e2.prices()) == base
    assert m.fingerprint == preset


def test_the_anchor_is_a_fixed_point() -> None:
    """At the VIX the map is anchored on, the rate is the shipped rate.

    `1 - c + c * (vix / anchor)^2` is exactly 1 when vix == anchor, for any
    coupling. So a market pinned there is bit-identical however this dial is
    set, which is what makes the two ends comparable.
    """
    anchor = pt.ModelParams.from_preset("pt-v10").to_dict()["market_vol_vix_anchor"]
    base = _prices(anchor)
    assert _prices(anchor, jump_vix_coupling=1.0) == base


def test_coupling_moves_jump_days_with_the_regime() -> None:
    """The direction, which is the whole point: rarer when calm, denser in a panic.

    Uncoupled, the two counts are the same process at two VIX levels and
    differ only by sampling. Coupled, the calm market should jump strictly
    less than the shipped one and the panic strictly more.
    """
    calm_off, panic_off = _jump_cells(5.0), _jump_cells(65.0)
    calm_on = _jump_cells(5.0, jump_vix_coupling=1.0)
    panic_on = _jump_cells(65.0, jump_vix_coupling=1.0)

    assert calm_on < calm_off, (calm_on, calm_off)
    assert panic_on > panic_off, (panic_on, panic_off)
    # And the coupled market must separate the regimes, which the shipped
    # one cannot do at all.
    assert panic_on > calm_on * 3, (calm_on, panic_on)


def test_it_is_settable_and_fingerprinted() -> None:
    assert "jump_vix_coupling" in pt.ModelParams.settable()
    m = pt.ModelParams.from_preset("pt-v10", jump_vix_coupling=0.5)
    assert m.fingerprint.startswith("custom-")
    assert m.to_dict()["jump_vix_coupling"] == 0.5
