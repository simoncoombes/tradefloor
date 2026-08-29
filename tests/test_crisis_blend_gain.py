"""A crisis must be able to load names onto the market factor harder.

The crisis blend adds `crisis_blend_source * gain * crisis_spike *
market_factor` to a name's market component, and `gain` was the literal 0.5.
The spike is capped at `crisis_blend_cap` (0.98), so the extra market
loading a crisis could ever produce was 0.49 of beta, fixed in the source.

That ceiling is why crisis co-movement could not be raised. Measured at
thirty seeds (CALIBRATION-FOLLOWUPS.md §94 to §96), every route to a
real-sized crisis lever adds variance that is NOT the market factor, and
crisis-state cross-sectional correlation IS the market factor's share of
total variance, so the two traded against each other. The one channel that
should have raised both was already pinned at its cap, because the spike
saturates for any crisis worth the name.

Pinned here: the dial ships inert, the anchor of the old behaviour is 0.5,
and raising it raises crisis co-movement. Magnitudes belong to the
calibration record, at thirty seeds.
"""

from __future__ import annotations

import pytest

import tradefloor as pt
from tradefloor import Scenario, facts


def _prices(preset: str, vix: float = 45.0, days: int = 10, **over) -> list[float]:
    model = pt.ModelParams.from_preset(preset, **over) if over else preset
    e = pt.Engine(seed=31, universe=pt.Universe.random(20, seed=5), model=model)
    s = Scenario().hold(vix=vix)
    for d in range(days):
        s.apply(e, d)
        e.run_days(1, first_day=d)
    return list(e.prices())


@pytest.mark.parametrize("preset", ["pt-v3", "pt-v10", "pt-v11"])
def test_setting_it_to_its_own_value_is_inert(preset: str) -> None:
    """Re-stating a preset's own gain changes nothing, bit for bit, in a crisis.

    Checked under a held VIX 45: the crisis blend does nothing below the
    threshold, so a calm check would pass for the wrong reason.

    Written against each preset's OWN value rather than the literal 0.5,
    the value every preset carried until pt-v11 moved it to 0.8. A test
    that hardcodes the old default silently stops testing inertness the day
    a preset adopts the dial.
    """
    own = pt.ModelParams.from_preset(preset).to_dict()["crisis_blend_gain"]
    base = _prices(preset)
    assert _prices(preset, crisis_blend_gain=own) == base
    assert pt.ModelParams.from_preset(preset, crisis_blend_gain=own).fingerprint == preset


def test_the_old_literal_is_still_what_the_earlier_presets_carry() -> None:
    """0.5 was the hard-coded value, and pt-v3 and pt-v10 must still be it."""
    for preset in ("pt-v3", "pt-v10"):
        assert pt.ModelParams.from_preset(preset).to_dict()["crisis_blend_gain"] == 0.5
    assert pt.ModelParams.from_preset("pt-v11").to_dict()["crisis_blend_gain"] == 0.8


def test_it_does_nothing_below_the_crisis_gate() -> None:
    """No crisis, no blend, whatever the gain.

    `crisis_spike` is zero unless the VIX is above `crisis_vix_threshold`,
    and the gain multiplies the spike, so a calm market cannot see this dial
    at all. That is what makes it safe to raise.
    """
    calm = _prices("pt-v10", vix=12.0)
    assert _prices("pt-v10", vix=12.0, crisis_blend_gain=2.0) == calm


def test_raising_the_gain_raises_crisis_co_movement() -> None:
    """The direction the dial exists to set.

    Cross-sectional correlation under a held VIX 45 is the market factor's
    share of variance. Loading names onto that factor harder must raise it.
    """
    u = pt.Universe.random(40, seed=111)
    crisis = Scenario().hold(vix=45.0)

    def xs(gain: float) -> float:
        m = pt.ModelParams.from_preset("pt-v10", crisis_blend_gain=gain)
        return facts.measure(seed=4, universe=u, days=126, model=m,
                             scenario=crisis)["cross_sectional_corr"]

    assert xs(1.5) > xs(0.5), (xs(0.5), xs(1.5))


def test_it_is_settable_and_fingerprinted() -> None:
    assert "crisis_blend_gain" in pt.ModelParams.settable()
    m = pt.ModelParams.from_preset("pt-v10", crisis_blend_gain=1.25)
    assert m.fingerprint.startswith("custom-")
    assert m.to_dict()["crisis_blend_gain"] == 1.25
