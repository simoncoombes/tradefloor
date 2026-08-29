"""Contagion should know what regime it is in.

The peer weights are constants, so news transferred as hard in a quiet July
as in March 2020. Measuring that is what made endogenous news unusable
(CALIBRATION-FOLLOWUPS.md §104): calm-market sector excess is already in
band at +0.166 against a 0.11-to-0.22 ceiling, and constant transfer pushed
it to +0.256 at BOTH horizons, in exchange for the crisis figure that was
actually wanted. A mechanism that cannot tell a crisis from a Tuesday cannot
be aimed at one.

The property that makes this dial safe is the one pinned hardest below: the
crisis spike is zero under the VIX threshold, so a calm market is untouched
at ANY coupling.
"""

from __future__ import annotations

import pytest

import tradefloor as pt
from tradefloor import Scenario, facts

NEWS = dict(endogenous_news_intensity=0.10, endogenous_news_sigma=0.04,
            news_peer_weight=0.5, news_peer_weight_down=0.5)


def _prices(vix: float, days: int = 12, **over):
    m = pt.ModelParams.from_preset("pt-v11", **over)
    e = pt.Engine(seed=23, universe=pt.Universe.random(20, seed=11), model=m)
    s = Scenario().hold(vix=vix)
    for d in range(days):
        s.apply(e, d)
        e.run_days(1, first_day=d)
    return list(e.prices())


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v10", "pt-v11"])
def test_it_ships_inert(preset: str) -> None:
    """At zero the trajectory is the shipped one, bit for bit, in a crisis."""
    own = pt.ModelParams.from_preset(preset).to_dict()["news_peer_vix_coupling"]
    m = pt.ModelParams.from_preset(preset, news_peer_vix_coupling=own)
    assert m.fingerprint == preset

    def run(model):
        e = pt.Engine(seed=23, universe=pt.Universe.random(20, seed=11), model=model)
        s = Scenario().hold(vix=45.0)
        for d in range(12):
            s.apply(e, d)
            e.run_days(1, first_day=d)
        return list(e.prices())

    assert run(m) == run(preset)


def test_a_calm_market_is_untouched_at_any_coupling() -> None:
    """The property the whole dial rests on.

    `crisis_spike` is zero below `crisis_vix_threshold`, so the coupling
    multiplies zero and the peer weight is exactly its base. That is what
    lets this raise crisis contagion without touching the calm panel, which
    is the failure that made constant transfer unusable.
    """
    calm = _prices(12.0, **NEWS)
    for c in (0.5, 2.0, 8.0):
        assert _prices(12.0, news_peer_vix_coupling=c, **NEWS) == calm, c


def test_it_moves_a_crisis() -> None:
    """Above the gate the coupling must actually do something."""
    base = _prices(45.0, **NEWS)
    assert _prices(45.0, news_peer_vix_coupling=2.0, **NEWS) != base


def test_it_does_nothing_without_news_to_transfer() -> None:
    """No announcer, no contagion, however hard the coupling is turned up.

    Measured on pt-v10, which carries no news. pt-v11 does, so it cannot
    answer this question about itself.
    """
    def run(**over):
        m = pt.ModelParams.from_preset("pt-v10", **over) if over else "pt-v10"
        e = pt.Engine(seed=23, universe=pt.Universe.random(20, seed=11), model=m)
        s = Scenario().hold(vix=45.0)
        for d in range(12):
            s.apply(e, d)
            e.run_days(1, first_day=d)
        return list(e.prices())

    assert run(news_peer_vix_coupling=4.0) == run()


def test_it_raises_crisis_sector_structure() -> None:
    """The direction it exists for, measured where it is meant to act."""
    u = pt.Universe.random(40, seed=111)
    crisis = Scenario().hold(vix=45.0)

    def sector_excess(coupling: float) -> float:
        m = pt.ModelParams.from_preset("pt-v11", news_peer_vix_coupling=coupling,
                                       **NEWS)
        return facts.measure(seed=6, universe=u, days=126, model=m,
                             scenario=crisis)["sector_excess_corr"]

    assert sector_excess(3.0) > sector_excess(0.0)


def test_it_is_settable_and_fingerprinted() -> None:
    assert "news_peer_vix_coupling" in pt.ModelParams.settable()
    m = pt.ModelParams.from_preset("pt-v11", news_peer_vix_coupling=1.5)
    assert m.fingerprint.startswith("custom-")
    assert m.to_dict()["news_peer_vix_coupling"] == 1.5
