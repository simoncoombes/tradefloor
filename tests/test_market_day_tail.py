"""The day's market t scale (`market_day_tail_df`, `market_day_tail_state_share`;
0.8.5, sim/r17-d1tail).

`market_day_tail_df` is 0.0 on every shipped preset. Off zero, each open
draws `m = (nu - 2) / X`, `X` a chi-square with `nu` degrees of freedom (a
Marsaglia-Tsang gamma on the overnight stream, after the night's normals),
and the session's market sigma, the night's and every tick's, is the
state's times `sqrt(m)`: the day's market factor is a unit-variance Student
t at the state's variance. `E[m] = 1`. The close clears `m` to 1.0. The
variance state reads the day factor times `m^(-(1 - share) / 2)`, share
`market_day_tail_state_share`. These tests hold the default, the domain, the
draw's law, where the scale lives in the snapshot and the state hash, a
restore taken between the open and the close, and the state share.
"""

import math
import statistics
import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=7, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def one_day(e):
    e.run_session(9, 30, 3, 390)
    e.close_market()


@pytest.mark.parametrize("preset", tf.preset_names())
def test_the_day_tail_is_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["market_day_tail_df"] == 0.0
    assert d["market_day_tail_state_share"] == 0.0


def test_the_dials_are_refused_outside_their_domains():
    tf.ModelParams.from_preset("pt-v20", market_day_tail_df=3.0)
    tf.ModelParams.from_preset("pt-v20", market_day_tail_df=6.5,
                               market_day_tail_state_share=0.4)
    for bad in (-1.0, 1.0, 2.9, 200.5):
        with pytest.raises(Exception):
            tf.ModelParams.from_preset("pt-v20", market_day_tail_df=bad)
    for bad in (-0.1, 1.1):
        with pytest.raises(Exception):
            tf.ModelParams.from_preset("pt-v20", market_day_tail_df=7.0,
                                       market_day_tail_state_share=bad)


def test_off_no_scale_is_drawn_or_carried():
    e = engine()
    e.open_market()
    snap = e.state_snapshot()
    assert "market_day_scale" not in snap
    assert manifest.state_hash(snap) == e.state_hash()


def test_the_scale_lives_from_the_open_to_the_close():
    e = engine(market_day_tail_df=7.0)
    e.open_market()
    snap = e.state_snapshot()
    assert snap["market_day_scale"] > 0.0 and snap["market_day_scale"] != 1.0
    # The Python mirror of the state hash reads it too.
    assert manifest.state_hash(snap) == e.state_hash()
    one_day(e)
    assert "market_day_scale" not in e.state_snapshot()


def test_the_scale_is_an_inverse_chi_square_with_mean_one():
    # (nu - 2) / chi2(nu) is inverse-gamma: mean 1, variance 2 / (nu - 4).
    nu = 9.0
    e = engine(market_day_tail_df=nu)
    draws = []
    for _ in range(1500):
        e.open_market()
        draws.append(e.state_snapshot()["market_day_scale"])
        e.close_market()
    mean = statistics.fmean(draws)
    sd = math.sqrt(2.0 / (nu - 4.0))
    assert abs(mean - 1.0) < 4.0 * sd / math.sqrt(len(draws))
    # The share of days drawn at under half the variance: P(X > 2 (nu - 2)),
    # 0.12233 for a chi-square with 9 degrees of freedom above 14.
    low = sum(1 for m in draws if m < 0.5) / len(draws)
    p = 0.12233
    assert abs(low - p) < 4.0 * math.sqrt(p * (1 - p) / len(draws))
    assert max(draws) > 2.5


def test_the_draw_is_deterministic_and_moves_only_the_overnight_stream():
    a, b = engine(market_day_tail_df=6.0), engine(market_day_tail_df=6.0)
    off = engine()
    for e in (a, b, off):
        e.open_market()
    assert a.state_snapshot()["market_day_scale"] == b.state_snapshot()["market_day_scale"]
    # The other streams' positions at the open are the ones the model
    # without the dial reaches.
    pa, po = a.stream_positions(), off.stream_positions()
    assert pa.keys() == po.keys()
    moved = [k for k in pa if pa[k] != po[k]]
    assert moved and all("overnight" in str(k).lower() for k in moved), moved


def test_a_snapshot_between_the_open_and_the_close_restores_to_the_same_close():
    dials = {"market_day_tail_df": 5.0, "market_day_tail_state_share": 1.0}
    a = engine(**dials)
    a.run_days(4, record=False)
    a.open_market()
    snap = a.state_snapshot()
    assert snap["market_day_scale"] != 1.0
    b = engine(**dials)
    b.restore_state(snap)
    assert b.state_hash() == a.state_hash()
    for e in (a, b):
        one_day(e)
    assert b.state_hash() == a.state_hash()
    assert floats(b.prices()) == floats(a.prices())
    # A restore that dropped the scale would draw the session at the
    # state's own variance and close elsewhere.
    c = engine(**dials)
    lost = dict(snap)
    del lost["market_day_scale"]
    c.restore_state(lost)
    one_day(c)
    assert floats(c.prices()) != floats(a.prices())


def test_the_state_share_sets_what_the_close_reads():
    # One day, the same draws: at share 0 the close reads the day as if
    # drawn at the state's variance, at 1 as it landed. The next session's
    # variance differs unless the day's scale was exactly 1.
    out = {}
    for share in (0.0, 1.0):
        e = engine(seed=11, market_day_tail_df=4.0, market_day_tail_state_share=share)
        e.open_market()
        m = e.state_snapshot()["market_day_scale"]
        one_day(e)
        out[share] = (m, e.state_snapshot()["market_variance"])
    assert out[0.0][0] == out[1.0][0] != 1.0
    assert out[0.0][1] != out[1.0][1]
