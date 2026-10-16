"""The 10-year's own term premium (`treasury_term_premium_sd`, `treasury_term_premium_half_life`).

Every preset ships both at 0.0, which is left out of the digest, and every known-answer digest is the one it was
(tests/test_known_answer.py). The state's normals are `GameRng::keyed(key, TERM_PREMIUM_TAG, day)` (the burn-in's
days under `day | 1 << 40`, the opening's under the tag's high word), fresh generators that take nothing from any
stream. These hold the mechanism on pt-v20: at 0.0 the run is the one that stood; the state is an AR(1) at the
half-life on draws of its own key, the same whatever else the model sets; it reaches the 10-year through the anchor's
5 per cent pull; the snapshot and both state hashes carry it only with the sd set, and a restore reproduces the run;
and it takes no draw.
"""

import struct

import numpy as np
import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3, bonds=True))
SD, HL = 0.06, 49.0
TP = {"treasury_term_premium_sd": SD, "treasury_term_premium_half_life": HL}


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20", **dials))


def level(e):
    return floats(e.state_snapshot()["term_premium"])[0]


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["treasury_term_premium_sd"] == 0.0 and d["treasury_term_premium_half_life"] == 0.0


@pytest.mark.parametrize("dials", [dict(treasury_term_premium_sd=-0.01, treasury_term_premium_half_life=HL),
                                   dict(treasury_term_premium_sd=0.6, treasury_term_premium_half_life=HL),
                                   dict(treasury_term_premium_sd=SD, treasury_term_premium_half_life=2521.0)])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_the_state_is_carried_only_with_the_sd():
    assert "term_premium" not in engine().state_snapshot()
    assert "term_premium" not in engine(treasury_term_premium_half_life=HL).state_snapshot()
    assert "term_premium" in engine(**TP).state_snapshot()


def test_at_zero_it_is_the_run_that_stood():
    a, b = engine(), engine(treasury_term_premium_sd=0.0)
    for x in (a, b):
        x.run_days(30, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()


def test_no_draw_from_any_stream():
    off, tiny = engine(), engine(treasury_term_premium_sd=1e-12, treasury_term_premium_half_life=HL)
    for x in (off, tiny):
        x.run_days(60, record=False)
    assert dict(tiny.stream_positions()) == dict(off.stream_positions())
    assert off.draws_consumed == tiny.draws_consumed
    a, b = np.array(floats(off.prices())), np.array(floats(tiny.prices()))
    assert np.all(np.abs(a - b) <= 1e-6 * np.maximum(1.0, np.abs(a)))


def test_the_state_is_an_ar1_on_its_own_key():
    # The path is the key's: the same with or without the flight to quality.
    keep = 0.5 ** (1.0 / HL)
    a, b = engine(**TP), engine(flight_to_quality_gain=0.0, **TP)
    xa, xb = [level(a)], [level(b)]
    for day in range(300):
        for e, xs in ((a, xa), (b, xb)):
            e.run_days(1, record=False, first_day=day)
            xs.append(level(e))
    assert xa == xb
    x = np.array(xa)
    z = (x[1:] - keep * x[:-1]) / SD
    assert abs(z.mean()) < 0.25 and 0.8 < z.std() < 1.2
    assert abs(np.corrcoef(z[1:], z[:-1])[0, 1]) < 0.2
    # Another seed is another key.
    assert level(engine(seed=8, **TP)) != xa[0]


def test_at_half_life_zero_the_state_has_no_persistence():
    e = engine(treasury_term_premium_sd=SD)
    xs = [level(e)]
    for day in range(200):
        e.run_days(1, record=False, first_day=day)
        xs.append(level(e))
    x = np.array(xs)
    assert 0.8 < x.std() / SD < 1.2 and abs(np.corrcoef(x[1:], x[:-1])[0, 1]) < 0.2


def test_the_ten_year_takes_it_through_the_anchor():
    # With the flight to quality off, between meetings the 10-year's difference from the run without the premium
    # closes 5 per cent a session toward tonight's state: d' = 0.95 d + 0.05 x'.
    dials = dict(flight_to_quality_gain=0.0)
    off, on = engine(**dials), engine(**TP, **dials)
    d = on.state_snapshot()["economy"]["treasury_yield_10y"] - off.state_snapshot()["economy"]["treasury_yield_10y"]
    checked = 0
    for day in range(120):
        m0 = on.state_snapshot()["central_bank"]["last_meeting_date"]
        for e in (off, on):
            e.run_days(1, record=False, first_day=day)
        son, soff = on.state_snapshot(), off.state_snapshot()
        dn = son["economy"]["treasury_yield_10y"] - soff["economy"]["treasury_yield_10y"]
        if son["central_bank"]["last_meeting_date"] == m0 \
                and soff["central_bank"]["last_meeting_date"] == off.state_snapshot()["central_bank"]["last_meeting_date"] \
                and 0.6 < son["economy"]["treasury_yield_10y"] < 11.9:
            assert dn == pytest.approx(0.95 * d + 0.05 * level(on), abs=2e-3)
            checked += 1
        d = dn
    assert checked > 60


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    e = engine(**TP)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert level(e) != 0.0
    assert manifest.state_hash(snap) == e.state_hash()
    w = list(floats(snap["term_premium"]))
    moved = {**snap, "term_premium": struct.pack("<3d", w[0] + 1.0, w[1], w[2])}
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(seed=99, **TP)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert floats(e.prices()) == floats(twin.prices())
    assert e.state_hash() == twin.state_hash()


def test_on_the_ten_year_moves():
    base, live = engine(), engine(**TP)
    for x in (base, live):
        x.run_days(30, record=False)
    assert base.state_snapshot()["economy"]["treasury_yield_10y"] != \
        live.state_snapshot()["economy"]["treasury_yield_10y"]
    assert base.state_hash() != live.state_hash()
