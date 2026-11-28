"""Credit's repricing lag (`corporate_spread_equity_lag`).

Every preset ships it at 0.0, which is left out of the digest, and every
known-answer digest is the one it was (tests/test_known_answer.py). These hold
the mechanism on pt-v20 with credit's leverage term set: at 0.0 the run is the
gain's own run; off zero the priced gap steps toward the gap at the half-life
and the spread's daily move off a meeting reads it; a meeting re-anchors the
spread to the formula with the priced gap in its base; the snapshot and both
state hashes carry the priced gap only while the lag and the gain are set,
and a restore reproduces the run; and the dial takes no draw.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
GAIN = {"corporate_spread_vix_cut": 1.0, "corporate_spread_equity_gain": 1.5,
        "corporate_spread_equity_half_life": 126.0}
LAG = 5.0
# The meeting formula's cycle multipliers (`economy::central_bank::spread_multiplier_of`).
MULT = {"contraction": 2.8, "trough": 3.5, "recovery": 1.4, "peak": 1.1, "expansion": 1.0}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()["corporate_spread_equity_lag"] == 0.0


@pytest.mark.parametrize("value", [-1.0, 253.0])
def test_out_of_range_is_refused(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", corporate_spread_equity_lag=value, **GAIN)


def test_the_priced_gap_is_carried_only_with_the_lag_and_the_gain():
    assert "spread_priced_gap" not in engine(**GAIN).state_snapshot()["economy"]
    assert "spread_priced_gap" not in engine(corporate_spread_equity_lag=LAG).state_snapshot()["economy"]
    assert "spread_priced_gap" in engine(corporate_spread_equity_lag=LAG, **GAIN).state_snapshot()["economy"]


def test_at_zero_it_is_the_gains_own_run():
    a, b = engine(**GAIN), engine(corporate_spread_equity_lag=0.0, **GAIN)
    for x in (a, b):
        x.run_days(30, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()


def test_the_priced_gap_follows_the_gap_and_the_spread_reads_it():
    keep = 0.5 ** (1.0 / LAG)
    g = GAIN["corporate_spread_equity_gain"]
    e = engine(corporate_spread_equity_lag=LAG, **GAIN)
    before = e.state_snapshot()
    off = on = 0
    for day in range(60):
        e.run_days(1, record=False, first_day=day)
        now = e.state_snapshot()
        eb, en = before["economy"], now["economy"]
        want_p = eb["spread_priced_gap"] + (1.0 - keep) * (en["spread_equity_gap"] - eb["spread_priced_gap"])
        assert en["spread_priced_gap"] == pytest.approx(want_p, abs=1e-12)
        spread = en["corporate_bond_yield"] - en["treasury_yield_10y"]
        if now["central_bank"]["last_meeting_date"] != before["central_bank"]["last_meeting_date"]:
            want = min(max((1.0 + g * en["spread_priced_gap"]) * MULT[en["cycle_phase"]], 0.8), 6.0)
            assert spread == pytest.approx(want, abs=1e-12)
            on += 1
        elif spread > 0.8 + 1e-9:
            moved = (en["corporate_bond_yield"] - eb["corporate_bond_yield"]) \
                - (en["treasury_yield_10y"] - eb["treasury_yield_10y"])
            want = g * MULT[eb["cycle_phase"]] * (en["spread_priced_gap"] - eb["spread_priced_gap"])
            assert moved == pytest.approx(want, abs=1e-12)
            off += 1
        before = now
    assert on >= 1 and off > 30
    assert en["spread_priced_gap"] != en["spread_equity_gap"]


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    dials = dict(corporate_spread_equity_lag=LAG, **GAIN)
    e = engine(**dials)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert snap["economy"]["spread_priced_gap"] != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    moved = {**snap, "economy": {**snap["economy"],
                                 "spread_priced_gap": snap["economy"]["spread_priced_gap"] + 1.0}}
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(**dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def test_no_draw_and_the_market_moves():
    base, live = engine(**GAIN), engine(corporate_spread_equity_lag=LAG, **GAIN)
    for x in (base, live):
        x.run_days(30, record=False)
    assert base.draws_consumed == live.draws_consumed
    assert base.state_snapshot()["economy"]["corporate_bond_yield"] != \
        live.state_snapshot()["economy"]["corporate_bond_yield"]
