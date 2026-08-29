"""No searchable parameter may be frozen by its own box.

`default_box` sizes a search box multiplicatively around the shipped value.
For a parameter that ships at **0.0** that box is `(0.0, 0.0)` — a single
point. A search over it runs for hours, explores nothing, and reports that
the incumbent is unbeatable, which is indistinguishable from a search that
genuinely found nothing.

This has cost two runs. CALIBRATION-FOLLOWUPS §24 lost a 96-core search to
an inverted box on `market_vol_ceiling_multiple`; §21's "the optimiser sold
the lever" diagnosis was itself wrong for the same reason. And seven of the
nine parameters wanted for the jump/volume search — every jump coefficient
and two volume ones — would have been frozen the same way had the boxes not
been checked before launching.

So the property is asserted rather than remembered.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                       / "tools" / "calibration"))

import tradefloor as pt  # noqa: E402

lib = pytest.importorskip("instrumentlib",
                          reason="calibration tooling is not packaged")


#: Parameters a search must NOT include, so a frozen box is correct rather
#: than a defect. `regime_stress_points` is untestable on the standard panel
#: (CALIBRATION-FOLLOWUPS §25): the 252-day panel never leaves the expansion
#: phase, whose stress intensity is 0.0 by construction, so the cycle cannot
#: reach the market at any value.
#:
#: It also cannot be given a range without breaking something else. Its
#: natural span is 0-20 VIX-like points, and `deviation()` returns a RAW
#: difference for an "abs" parameter, so a box edge at 20 costs 400 against
#: a median squared deviation of 1.0 -- the regulariser bug §24 lost a run
#: to, where the search optimises the penalty instead of the market. Giving
#: it a comparable penalty needs `deviation()` to normalise by box width for
#: every "abs" parameter, which is a change to how lambda means what it
#: means and wants measuring on its own.
UNSEARCHABLE = {"regime_stress_points"}


def _box(name):
    return lib.default_box(name, lib.shipped_values().get(name))


def test_no_settable_parameter_has_a_degenerate_box():
    """The whole surface, not just the ones someone happens to search.

    Checking only today's parameter set is what let this recur: the box is
    computed at search time from a spec written long before, so the failure
    surfaces on the run that first reaches for the parameter.
    """
    frozen = []
    for name in pt.ModelParams.settable():
        if name in UNSEARCHABLE:
            continue
        low, high = _box(name)
        if not low < high:
            frozen.append((name, lib.shipped_values().get(name), (low, high)))
    assert not frozen, (
        "these parameters would be explored at a single point by any search "
        "that included them, while looking exactly like a search that ran: "
        f"{frozen}"
    )


def test_a_parameter_shipping_at_zero_carries_an_explicit_range():
    """The multiplicative default cannot serve a zero, so the spec must say.

    Stated separately from the box test because it names the CAUSE. A future
    parameter added at 0.0 with no `hard_range` fails here with the reason
    attached, rather than failing the general test with a bare list.
    """
    missing = [
        name for name in pt.ModelParams.settable()
        if lib.shipped_values().get(name) == 0.0
        and name not in UNSEARCHABLE
        and "hard_range" not in lib.PARAM_SPECS.get(name, {})
    ]
    assert not missing, (
        "a parameter that ships at 0.0 has no multiplicative box; give it an "
        f"explicit hard_range in PARAM_SPECS: {missing}"
    )


def test_every_hard_range_is_ordered_and_contains_the_shipped_value():
    """§24's actual failure: `hard_range` (0.0, 0.999) against a shipped 8.0
    produced the box [2.0, 0.999] — inverted, so every search coordinate
    mapped to one point and the parameter was frozen at a value nobody
    chose."""
    ship = lib.shipped_values()
    for name, spec in lib.PARAM_SPECS.items():
        rng = spec.get("hard_range")
        if rng is None:
            continue
        low, high = rng
        assert low < high, f"{name}: hard_range {rng} is inverted"
        value = ship.get(name)
        if value is not None:
            assert low <= value <= high, (
                f"{name}: ships at {value}, outside its own hard_range {rng}"
            )


@pytest.mark.parametrize("name", [
    "jump_intensity_market", "jump_intensity_idio", "jump_mean_market",
    "jump_sigma_market", "jump_sigma_idio",
    "volume_persistence", "volume_innovation_sigma", "volume_variance_gain",
    "universe_stress_weight", "universe_stress_decay",
])
def test_the_inert_mechanisms_are_reachable_by_a_search(name):
    """These ship at 0.0 *by design* — they are built mechanisms nobody has
    turned on. That is why they must be reachable: the reason
    of measuring them is to find out whether they should be."""
    low, high = _box(name)
    assert low < high, f"{name} is frozen at a point"
