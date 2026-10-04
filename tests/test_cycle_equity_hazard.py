"""The market's fall in the business cycle's hazard (`cycle_equity_hazard`,
`cycle_equity_hazard_knee`; 0.8.5, sim/r17-b12).

Both dials ship at 0.0 on every preset, and there the cycle's ladder is the one
that stood (the Rust tests in economy/cycle.rs hold that to the bit). Off zero,
in an expansion and at a peak, the monthly hazard gains `cycle_equity_hazard`
per unit of the index's log fall below its slow average beyond the knee: the
gap credit's leverage term reads (`EconomyState::spread_equity_gap`), averaged
at `corporate_spread_equity_half_life`, which the hazard runs even with
`corporate_spread_equity_gain` at 0.0. `cycle_equity_hazard_opening` adds a
fixed monthly hazard in those phases only while the economy runs alone before
day zero (the stationary opening's law and the macro burn-in), as the stand-in
for an index the economy does not have there. These tests hold the default, the
domain, that the gap is carried while the hazard is set, that a gap under the
knee leaves the market as it was and takes no draw, that a hazard above it
ends an expansion sooner, and that a snapshot restores to the same
run.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("cycle_equity_hazard", "cycle_equity_hazard_knee", "cycle_equity_hazard_opening")
HALF = {"corporate_spread_equity_half_life": 126.0}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert all(values[name] == 0.0 for name in DIALS)


@pytest.mark.parametrize("dials", [
    {"cycle_equity_hazard": -0.5, **HALF},
    {"cycle_equity_hazard": 20.5, **HALF},
    {"cycle_equity_hazard_knee": -0.1},
    {"cycle_equity_hazard_knee": 1.1},
    {"cycle_equity_hazard_opening": -0.01},
    {"cycle_equity_hazard_opening": 1.5},
    # The gap is averaged at the half-life, so the hazard needs one.
    {"cycle_equity_hazard": 5.0},
])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_the_gap_is_carried_while_the_hazard_is_set():
    assert "spread_equity_gap" not in engine().state_snapshot()["economy"]
    assert "spread_equity_gap" not in engine(cycle_equity_hazard_knee=0.1, **HALF) \
        .state_snapshot()["economy"]
    e = engine(cycle_equity_hazard=5.0, **HALF)
    assert "spread_equity_gap" in e.state_snapshot()["economy"]
    e.run_days(10, record=False)
    assert e.state_snapshot()["economy"]["spread_equity_gap"] != 0.0


def test_a_gap_under_the_knee_leaves_the_market_as_it_was():
    # The knee at 1.0, a log fall the gap does not reach in a year: the gap
    # runs and is carried, and the market is the default's to the bit.
    base, knee = engine(), engine(cycle_equity_hazard=20.0, cycle_equity_hazard_knee=1.0, **HALF)
    for x in (base, knee):
        x.run_days(252, record=False)
    assert floats(base.prices()) == floats(knee.prices())
    assert base.draws_consumed == knee.draws_consumed
    assert knee.state_snapshot()["economy"]["spread_equity_gap"] < 1.0


def phases(e, days):
    out = []
    for d in range(days):
        e.run_days(1, record=False, first_day=d)
        out.append(e.state_snapshot()["economy"]["cycle_phase"])
    return out


def test_a_fall_past_the_knee_ends_the_expansion_sooner():
    # With the knee at 0.0 the gap passes it on any session the index sits
    # under its slow average, so the strongest hazard ends an expansion
    # earlier than the default on some seed. The dial takes no draw of its
    # own (the knee test above), but a phase that moves sooner moves the
    # draws the new phase takes, so the counts are not compared here.
    days = 504
    earlier = 0
    for seed in (11, 12, 13, 14):
        base, live = engine(seed=seed), engine(seed=seed, cycle_equity_hazard=20.0, **HALF)
        pb, pl = phases(base, days), phases(live, days)
        first_b = next((i for i, p in enumerate(pb) if p != pb[0]), days)
        first_l = next((i for i, p in enumerate(pl) if p != pl[0]), days)
        if pb[0] == "expansion" and first_l < first_b:
            earlier += 1
    assert earlier >= 1


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    dials = {"cycle_equity_hazard": 8.0, "cycle_equity_hazard_knee": 0.05, **HALF}
    e = engine(**dials)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert snap["economy"]["spread_equity_gap"] != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    moved = {**snap, "economy": {**snap["economy"],
                                 "spread_equity_gap": snap["economy"]["spread_equity_gap"] + 1.0}}
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def opening_phases(n, **dials):
    return [engine(seed=s, **dials).state_snapshot()["economy"]["cycle_phase"] for s in range(n)]


def test_the_opening_stand_in_moves_the_opening():
    # Off, the opening is the default's to the bit; on, a strong stand-in
    # opens fewer runs in an expansion (the law and the burn-in both shorten
    # it). The stand-in takes no draw of its own, but a burn-in that lives
    # through other phases takes the draws those phases take.
    base = opening_phases(40)
    assert opening_phases(40, cycle_equity_hazard_opening=0.0) == base
    on = opening_phases(40, cycle_equity_hazard_opening=0.2)
    assert on != base
    assert on.count("expansion") < base.count("expansion")
