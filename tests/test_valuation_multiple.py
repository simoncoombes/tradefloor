"""The valuation multiple (`valuation_multiple_share`, `valuation_multiple_half_life`).

Every preset ships both at 0.0, which is left out of the digest, and every known-answer digest is the one it was
(tests/test_known_answer.py). Off zero each name carries a log multiple `m`: that share of its permanent market moves
joins `m` instead of the fair-value level `v`, fair value reads `exp(m)`, the earnings the market P/E reads do not,
and each close multiplies `m` by `0.5^(1/H)`. The stationary opening opens every name at its beta times one draw of
`GameRng::keyed(key, VALUATION_MULTIPLE_TAG, u64::MAX)`, with `v` taking the rest of the premium. These hold it on
pt-v20: at 0.0 the run is the one that stood; the opening moves no price and no stream; the multiple is the same
draw scaled by each name's beta; fair value and the P/E read it and the pull decays it; and the snapshot and both
state hashes carry it only with the share set, and a restore reproduces the run.
"""

import math
import struct

import numpy as np
import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
BETAS = np.array([x.beta for x in UNIVERSE])
HL = 1779.0
ON = {"valuation_multiple_share": 1.0, "valuation_multiple_half_life": HL}


def floats(raw):
    return np.array(struct.unpack("<%dd" % (len(raw) // 8), raw))


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE, model=tf.ModelParams.from_preset("pt-v20", **dials))


def multiples(e):
    return floats(e.state_snapshot()["valuation_multiple"])


def first_tick(e):
    e.open_market()
    e.run_session(9, 30, 3, 1)
    return e


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["valuation_multiple_share"] == 0.0 and d["valuation_multiple_half_life"] == 0.0


@pytest.mark.parametrize("dials", [dict(valuation_multiple_share=-0.1, valuation_multiple_half_life=HL),
                                   dict(valuation_multiple_share=1.1, valuation_multiple_half_life=HL),
                                   dict(valuation_multiple_share=1.0, valuation_multiple_half_life=-1.0),
                                   dict(valuation_multiple_share=1.0, valuation_multiple_half_life=100001.0),
                                   dict(valuation_multiple_share=1.0)])
def test_out_of_range_is_refused(dials):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **dials)


def test_at_zero_it_is_the_run_that_stood():
    # The half-life alone names the model (its digest), so its hash differs by that and its prices do not.
    a, b, c = engine(), engine(valuation_multiple_share=0.0), engine(valuation_multiple_half_life=HL)
    for x in (a, b, c):
        x.run_days(30, record=False)
    assert list(floats(a.prices())) == list(floats(b.prices())) == list(floats(c.prices()))
    assert a.state_hash() == b.state_hash()
    assert dict(a.stream_positions()) == dict(c.stream_positions())


def test_the_multiples_are_carried_only_with_the_share():
    assert "valuation_multiple" not in engine().state_snapshot()
    assert "valuation_multiple" not in engine(valuation_multiple_half_life=HL).state_snapshot()
    assert "valuation_multiple" in engine(**ON).state_snapshot()


def test_the_opening_moves_no_price_and_no_stream():
    off, on = first_tick(engine()), first_tick(engine(**ON))
    assert list(floats(on.prices())) == list(floats(off.prices()))
    for x in (off, on):
        x.run_session(9, 31, 3, 389)
        x.close_market()
        x.run_days(40, record=False, first_day=1)
    assert dict(on.stream_positions()) == dict(off.stream_positions())
    assert on.draws_consumed == off.draws_consumed


def test_every_name_opens_at_its_beta_times_one_keyed_draw():
    e = first_tick(engine(**ON))
    ratio = multiples(e) / BETAS
    # One tick of the market's moves has joined the multiple since the opening.
    assert np.ptp(ratio) < 1e-3 and abs(ratio.mean()) > 1e-3
    # The level takes the rest of the premium: v + m is the level the run without the multiple opens at.
    off = first_tick(engine())
    v_on = floats(e.state_snapshot()["fair_value_offset"])
    v_off = floats(off.state_snapshot()["fair_value_offset"])
    assert np.allclose(v_on + multiples(e), v_off, atol=1e-5)
    # Another seed is another draw; the same seed is the same draw.
    assert multiples(first_tick(engine(seed=8, **ON)))[0] != multiples(e)[0]
    assert list(multiples(first_tick(engine(**ON)))) == list(multiples(e))


def test_its_spread_at_the_opening_is_the_stationary_one():
    p = tf.ModelParams.from_preset("pt-v20").to_dict()
    a = 0.5 ** (1.0 / HL)
    sd = p["fair_value_market_share"] * p["market_factor_sigma"] / math.sqrt(1.0 - a * a)
    m0 = np.array([multiples(first_tick(engine(seed=s, **ON)))[1] / BETAS[1] for s in range(40)])
    assert 0.65 < m0.std() / sd < 1.35 and abs(m0.mean()) < 3.0 * sd / math.sqrt(40)


def bumped(e, name, delta):
    """A twin of `e` restored from its snapshot with name `name`'s multiple moved by `delta`."""
    snap = e.state_snapshot()
    m = multiples(e)
    m[name] += delta
    twin = engine(seed=99, **ON)
    twin.restore_state({**snap, "valuation_multiple": struct.pack("<%dd" % len(m), *m)})
    return twin


def test_fair_value_reads_the_multiple():
    # Fair value is `base exp(v) exp(m) + A`, the accrued dividend A outside the multiple, so a name's fair value
    # moves by exp(delta) less A's small share; no other name's moves.
    e = engine(**ON)
    e.run_days(5, record=False)
    delta = 0.05
    ratio = np.array(bumped(e, 2, delta).fair_values()) / np.array(e.fair_values())
    assert ratio[2] == pytest.approx(math.exp(delta), rel=2e-3) and ratio[2] > 1.0
    assert np.allclose(np.delete(ratio, 2), 1.0, rtol=1e-12, atol=0.0)


def test_the_close_pulls_the_multiple_at_its_half_life():
    e = engine(**ON)
    e.run_days(5, record=False)
    delta = 0.05
    twin = bumped(e, 2, delta)
    for x in (e, twin):
        x.run_days(1, record=False, first_day=5)
    gap = multiples(twin)[2] - multiples(e)[2]
    assert gap == pytest.approx(delta * 0.5 ** (1.0 / HL), rel=1e-3)


def test_the_market_pe_carries_the_multiple_and_earnings_do_not():
    e = engine(**ON)
    e.run_days(5, record=False)
    delta = 0.05
    snap = e.state_snapshot()
    m = multiples(e) + delta
    twin = engine(seed=99, **ON)
    twin.restore_state({**snap, "valuation_multiple": struct.pack("<%dd" % len(m), *m)})
    for x in (e, twin):
        x.run_days(1, record=False, first_day=5)
    pe = [x.state_snapshot()["economy"]["market_pe"] for x in (e, twin)]
    assert pe[1] / pe[0] == pytest.approx(math.exp(delta * 0.5 ** (1.0 / HL)), rel=2e-3)


def test_the_hashes_agree_and_a_restore_reproduces_the_run():
    e = engine(**ON)
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    assert manifest.state_hash(snap) == e.state_hash()
    m = multiples(e)
    m[0] += 1.0
    moved = {**snap, "valuation_multiple": struct.pack("<%dd" % len(m), *m)}
    assert manifest.state_hash(moved) != e.state_hash()
    twin = engine(seed=99, **ON)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(4, record=False, first_day=6)
    assert list(floats(e.prices())) == list(floats(twin.prices()))
    assert e.state_hash() == twin.state_hash()


def test_on_the_prices_move():
    off, on = engine(), engine(**ON)
    for x in (off, on):
        x.run_days(30, record=False)
    assert list(floats(off.prices())) != list(floats(on.prices()))
    assert off.state_hash() != on.state_hash()
