"""Credit's own state (`corporate_spread_noise_sd`, `corporate_spread_noise_half_life`).

Every preset ships both at 0.0, which is left out of the digest, and every known-answer digest is the one it was
(tests/test_known_answer.py). The state's normals are `GameRng::keyed(key, SPREAD_NOISE_TAG, day)` (the opening's
under the tag's high word), fresh generators that take nothing from any stream. These hold the mechanism on pt-v20
with the corporate yield's daily move and credit's leverage term set: at 0.0 the run is the one that stood; the state
is an AR(1) at the half-life on draws of its own key, the same whatever else the model sets; the spread's daily move
off a meeting carries its step and a meeting re-anchors the spread to the formula with it in the base; the snapshot
and both state hashes carry it only with the sd set, and a restore reproduces the run; and it takes no draw.
"""

import struct

import numpy as np
import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
BASE = {"corporate_yield_daily": 1.0, "corporate_spread_vix_cut": 1.0, "corporate_spread_equity_gain": 1.5,
        "corporate_spread_equity_half_life": 126.0}
SD, HL = 0.025, 16.0
NOISE = {"corporate_spread_noise_sd": SD, "corporate_spread_noise_half_life": HL}
# The meeting formula's cycle multipliers (`economy::central_bank::spread_multiplier_of`).
MULT = {"contraction": 2.8, "trough": 3.5, "recovery": 1.4, "peak": 1.1, "expansion": 1.0}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20", **dials))


def level(e):
    return floats(e.state_snapshot()["spread_noise"])[0]


# pt-v22 ships its arm's values, which tests/test_validation.py holds.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v22"])
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["corporate_spread_noise_sd"] == 0.0 and d["corporate_spread_noise_half_life"] == 0.0


@pytest.mark.parametrize("dials", [dict(corporate_spread_noise_sd=-0.01, corporate_spread_noise_half_life=HL),
                                   dict(corporate_spread_noise_sd=0.6, corporate_spread_noise_half_life=HL),
                                   dict(corporate_spread_noise_sd=SD, corporate_spread_noise_half_life=2521.0)])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_at_half_life_zero_the_state_has_no_persistence():
    e = engine(corporate_spread_noise_sd=SD, **BASE)
    xs = [level(e)]
    for day in range(200):
        e.run_days(1, record=False, first_day=day)
        xs.append(level(e))
    x = np.array(xs)
    assert 0.8 < x.std() / SD < 1.2 and abs(np.corrcoef(x[1:], x[:-1])[0, 1]) < 0.2


def test_the_state_is_carried_only_with_the_sd():
    assert "spread_noise" not in engine(**BASE).state_snapshot()
    assert "spread_noise" not in engine(corporate_spread_noise_half_life=HL, **BASE).state_snapshot()
    assert "spread_noise" in engine(**NOISE, **BASE).state_snapshot()


def test_at_zero_it_is_the_run_that_stood():
    a, b = engine(**BASE), engine(corporate_spread_noise_sd=0.0, **BASE)
    for x in (a, b):
        x.run_days(30, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()


def test_no_draw_from_any_stream():
    off, tiny = engine(**BASE), engine(corporate_spread_noise_sd=1e-12, corporate_spread_noise_half_life=HL, **BASE)
    for x in (off, tiny):
        x.run_days(60, record=False)
    assert dict(tiny.stream_positions()) == dict(off.stream_positions())
    assert off.draws_consumed == tiny.draws_consumed
    a, b = np.array(floats(off.prices())), np.array(floats(tiny.prices()))
    assert np.all(np.abs(a - b) <= 1e-6 * np.maximum(1.0, np.abs(a)))


def test_the_state_is_an_ar1_on_its_own_key():
    # The path is the key's: the same with or without the corporate yield's daily move and the leverage term.
    keep = 0.5 ** (1.0 / HL)
    a, b = engine(**NOISE, **BASE), engine(**NOISE)
    xa, xb = [level(a)], [level(b)]
    for day in range(250):
        for e, xs in ((a, xa), (b, xb)):
            e.run_days(1, record=False, first_day=day)
            xs.append(level(e))
    assert xa == xb
    x = np.array(xa)
    z = (x[1:] - keep * x[:-1]) / SD
    assert abs(z.mean()) < 0.25 and 0.8 < z.std() < 1.2
    assert abs(np.corrcoef(z[1:], z[:-1])[0, 1]) < 0.2
    # Another seed is another key.
    assert level(engine(seed=8, **NOISE)) != xa[0]


def test_the_spread_moves_with_the_state_and_a_meeting_reanchors_it():
    g = BASE["corporate_spread_equity_gain"]
    e = engine(**NOISE, **BASE)
    before = e.state_snapshot()
    off = on = 0
    for day in range(80):
        e.run_days(1, record=False, first_day=day)
        now = e.state_snapshot()
        eb, en = before["economy"], now["economy"]
        xb, xn = floats(before["spread_noise"])[0], floats(now["spread_noise"])[0]
        spread = en["corporate_bond_yield"] - en["treasury_yield_10y"]
        if now["central_bank"]["last_meeting_date"] != before["central_bank"]["last_meeting_date"]:
            want = min(max((1.0 + (g * en["spread_equity_gap"] + xn)) * MULT[en["cycle_phase"]], 0.8), 6.0)
            assert spread == pytest.approx(want, abs=1e-12)
            on += 1
        elif spread > 0.8 + 1e-9 and eb["cycle_phase"] == en["cycle_phase"]:
            moved = (en["corporate_bond_yield"] - eb["corporate_bond_yield"]) \
                - (en["treasury_yield_10y"] - eb["treasury_yield_10y"])
            want = MULT[eb["cycle_phase"]] * (g * (en["spread_equity_gap"] - eb["spread_equity_gap"]) + (xn - xb))
            assert moved == pytest.approx(want, abs=1e-12)
            off += 1
        before = now
    assert on >= 1 and off > 30


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    dials = dict(**NOISE, **BASE)
    e = engine(**dials)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert level(e) != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    w = list(floats(snap["spread_noise"]))
    moved = {**snap, "spread_noise": struct.pack("<3d", w[0] + 1.0, w[1], w[2])}
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(seed=99, **dials)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def test_on_the_corporate_yield_moves():
    base, live = engine(**BASE), engine(**NOISE, **BASE)
    for x in (base, live):
        x.run_days(30, record=False)
    assert base.state_snapshot()["economy"]["corporate_bond_yield"] != \
        live.state_snapshot()["economy"]["corporate_bond_yield"]
    assert base.state_hash() != live.state_hash()
