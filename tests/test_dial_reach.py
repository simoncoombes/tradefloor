"""Every narrow-reach claim names a real dial and survives a short probe.

A dial listed in a narrow channel lets a screen read the baseline's rows
for every protocol that does not open that channel. A wrong entry means a
screen reports a row it never measured, so each claim is checked here as
far as a short run can check it: the dial exists, its channel is known, and
an untraded, newsless run with the dial moved is identical to the last bit.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

CAL = pathlib.Path(__file__).resolve().parent.parent / "tools" / "calibration"
sys.path.insert(0, str(CAL))

import dial_reach  # noqa: E402

tradefloor = pytest.importorskip("tradefloor")


def test_every_entry_is_a_settable_dial_with_a_known_channel_and_a_reason():
    settable = set(tradefloor.ModelParams.settable())
    for dial, (channel, reason) in dial_reach.DIALS.items():
        assert dial in settable, f"{dial} is not a ModelParams dial"
        assert channel in dial_reach.CHANNELS and channel != "market"
        assert reason.strip()


def test_a_dial_with_no_entry_reaches_everything():
    protocols = {"untraded": {"market"}, "traded": {"market", "order_flow"}}
    assert dial_reach.reachable(["market_factor_sigma"], protocols) == {"untraded", "traded"}
    assert dial_reach.reachable(["order_flow_coefficient"], protocols) == {"traded"}
    assert dial_reach.reachable([], protocols) == set()


def test_changed_dials_treat_a_missing_silent_switch_as_zero():
    old = {"a": 1.0, "name": "x"}
    assert dial_reach.changed_dials({"a": 1.0, "new_switch": 0.0}, old) == []
    assert dial_reach.changed_dials({"a": 1.0, "new_switch": 0.5}, old) == ["new_switch"]
    assert dial_reach.changed_dials({"a": 2.0}, old) == ["a"]


def test_no_gate_kind_opens_a_narrow_channel():
    """The gate's kinds are untraded and newsless, so narrow dials skip them all."""
    narrow = list(dial_reach.DIALS)
    assert dial_reach.reachable(narrow, dial_reach.GATE_PROTOCOLS) == set()


#: Values each narrow dial is moved to for the probe. A dial whose move needs
#: a companion to pass the preset's own checks takes the companion here.
PROBE = {
    "order_flow_coefficient": 80.0,
    "order_flow_impact_law": 1.0,
    "order_flow_depth_law": 1.0,
    "informed_flow_fraction": 0.8,
    "fill_impact_coefficient": 0.6,
    "book_depth_coefficient": 1.4,
    "book_depth_exponent": 1.0,
    "book_depth_reach": 1.8,
    "book_refill_half_life": 40.0,
    "book_resting": 0.0,
    "news_market_weight": 0.75,
    "news_sector_weight": 1.05,
    "news_peer_weight": 0.4,
    "news_peer_weight_down": 0.4,
    "news_peer_vix_coupling": 12.0,
}

#: Narrow dials the probe cannot move alone, with the reason.
UNPROBED = {
    "book_shared": "turning it off orphans book_refill_half_life, which pt-v20 sets, "
                   "and the preset refuses the vector",
}


def test_the_probe_table_covers_every_narrow_dial():
    assert set(PROBE) | set(UNPROBED) == set(dial_reach.DIALS)


@pytest.mark.parametrize("dial", sorted(PROBE))
def test_a_narrow_dial_leaves_an_untraded_run_identical(dial):
    assert dial_reach.probe(dial, PROBE[dial], days=60, names=5, seeds=(1,)), (
        f"{dial} moved an untraded, newsless run: its channel claim is wrong")


def test_the_probe_does_see_a_market_dial():
    """Otherwise every probe above would pass for the wrong reason."""
    assert not dial_reach.probe("market_factor_sigma", 0.02, days=60, names=5, seeds=(1,))
