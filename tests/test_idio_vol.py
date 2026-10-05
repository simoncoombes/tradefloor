"""The per-name idiosyncratic variance state (`idio_vol_alpha`, `idio_vol_beta`,
`idio_vol_jump_bump`).

A ratio `s` of unconditional mean one multiplies the variance of each name's
own idiosyncratic draw, `s' = (1 - a - b) + a u^2 + b s + c (I - lambda)` at
the close. These tests hold that it is off on every shipped preset and inert
there (no snapshot key, no hash term), that it moves the market when set, that
the ratio's mean stays one with the jump channel on, that the jump channel
reads nothing when no own jump can arrive, that a snapshot, a restore and a
fork carry it, that the Python state hash agrees with the engine's, and that
a partial or mis-sized state and an out-of-range dial are refused.
"""

import struct

import numpy as np
import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(12, seed=3))
DIALS = ("idio_vol_alpha", "idio_vol_beta", "idio_vol_jump_bump")
KEYS = ("idio_variance", "idio_jump_pending", "idio_jump_var_pending")
ON = dict(idio_vol_alpha=0.2, idio_vol_beta=0.5, idio_vol_jump_bump=1.0)


def f64(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(model, seed=7, universe=UNIVERSE):
    return tf.Engine(seed=seed, universe=universe, model=model)


def prices(model, days=20):
    e = engine(model)
    e.run_days(days, record=False)
    return f64(e.prices())


# pt-v21, the default from 0.10.0, sets these; the test after this one
# holds its values.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v21"])
def test_off_on_every_shipped_preset(preset):
    values = tf.ModelParams.from_preset(preset).to_dict()
    assert [values[d] for d in DIALS] == [0.0, 0.0, 0.0]


def test_pt_v21_ships_them_on():
    """pt-v21, the default from 0.10.0, ships them at the values its grade
    read."""
    d = tf.ModelParams.from_preset("pt-v21").to_dict()
    assert {n: d[n] for n in (
        "idio_vol_alpha",
        "idio_vol_beta",
        "idio_vol_jump_bump",
    )} == {
        "idio_vol_alpha": 0.25,
        "idio_vol_beta": 0.5,
        "idio_vol_jump_bump": 1.0,
    }


@pytest.mark.parametrize("preset", ["pt-v19", "pt-v20"])
def test_off_the_snapshot_carries_no_state(preset):
    e = engine(tf.ModelParams.from_preset(preset))
    e.run_days(3, record=False)
    snap = e.state_snapshot()
    assert not set(KEYS) & set(snap)
    assert manifest.state_hash(snap) == e.state_hash()


def test_zero_set_explicitly_is_the_preset():
    base = tf.ModelParams.from_preset("pt-v20")
    zero = tf.ModelParams.from_preset("pt-v20", idio_vol_alpha=0.0,
                                      idio_vol_beta=0.0, idio_vol_jump_bump=0.0)
    assert prices(base) == prices(zero)


@pytest.mark.parametrize("dial", ["idio_vol_alpha", "idio_vol_jump_bump"])
def test_the_shock_and_the_jump_channel_each_move_the_market(dial):
    base = tf.ModelParams.from_preset("pt-v20")
    moved = tf.ModelParams.from_preset("pt-v20", **{dial: 0.3})
    assert prices(base) != prices(moved)


def test_persistence_alone_leaves_the_ratio_at_one():
    # beta alone switches the state on but has nothing to persist: from one,
    # (1 - b) + b * 1 is one again, so every draw is multiplied by 1.0.
    base = tf.ModelParams.from_preset("pt-v20")
    moved = tf.ModelParams.from_preset("pt-v20", idio_vol_beta=0.3)
    assert prices(base) == prices(moved)
    e = engine(moved)
    e.run_days(5, record=False)
    assert set(f64(e.state_snapshot()["idio_variance"])) == {1.0}
    moved = tf.ModelParams.from_preset("pt-v20", idio_vol_alpha=0.1, idio_vol_beta=0.3)
    assert prices(moved) != prices(tf.ModelParams.from_preset("pt-v20", idio_vol_alpha=0.1))


def test_the_jump_channel_reads_nothing_without_own_jumps():
    # With no idiosyncratic jump there is no realised jump (I = 0) and no
    # rate (lambda = 0), so the bump alone leaves every ratio at one and the
    # draws are multiplied by exactly 1.0.
    base = tf.ModelParams.from_preset("pt-v20", jump_intensity_idio=0.0)
    bump = tf.ModelParams.from_preset("pt-v20", jump_intensity_idio=0.0,
                                      idio_vol_jump_bump=2.0)
    assert prices(base) == prices(bump)


def test_the_ratio_has_a_mean_of_one():
    # The re-centred jump channel keeps the mean at one; the rejected form
    # that fed the jump into u^2 read 0.86 here, held down by the ceiling.
    e = engine(tf.ModelParams.from_preset("pt-v20", **ON), seed=203,
               universe=list(tf.Universe.random(40, seed=3)))
    ratios = []
    for day in range(110):
        e.run_days(1, record=False)
        if day >= 10:
            ratios.append(f64(e.state_snapshot()["idio_variance"]))
    ratios = np.asarray(ratios)
    assert abs(ratios.mean() - 1.0) < 0.03, ratios.mean()
    assert ratios.std() > 0.1
    p = tf.ModelParams.from_preset("pt-v20").to_dict()
    assert ratios.min() >= p["garch_floor_multiple"]
    assert ratios.max() <= p["garch_ceiling_multiple"]


def test_an_own_jump_raises_the_next_sessions_ratio():
    # The session after an own jump is realised, the bump adds c (1 - lambda)
    # to the ratio; on a quiet state (alpha, beta 0) that is the whole move.
    model = tf.ModelParams.from_preset("pt-v20", idio_vol_jump_bump=2.0,
                                       jump_intensity_idio=0.2)
    e = engine(model, seed=11)
    after, other = [], []
    for _ in range(40):
        pending = np.asarray(f64(e.state_snapshot().get(
            "idio_jump_pending", b"\0" * 8 * len(UNIVERSE))))
        e.run_days(1, record=False)
        ratio = np.asarray(f64(e.state_snapshot()["idio_variance"]))
        after.extend(ratio[pending != 0.0])
        other.extend(ratio[pending == 0.0])
    assert after and other
    assert np.mean(after) > 2.0
    assert np.mean(other) < 1.0


def twin(model, days, scale, name=0):
    """An engine run `days` sessions, and a copy restored from its snapshot
    with name `name`'s ratio multiplied by `scale`, nothing else touched."""
    a = engine(model)
    a.run_days(days, record=False)
    snap = a.state_snapshot()
    ratio = list(f64(snap["idio_variance"]))
    ratio[name] *= scale
    b = engine(model)
    b.restore_state(dict(snap, idio_variance=struct.pack("<%dd" % len(ratio), *ratio)))
    return a, b


def test_the_tick_scales_the_own_draw_by_the_root_of_the_ratio():
    # The core effect. With name 0's ratio four times its twin's, the same
    # own draws are exactly twice the size and their variance unit
    # (noise_own_scale2) exactly four times, while the market and sector
    # parts and every other name are bit-identical. Prices alone cannot
    # show this: the jump channel moves them with the tick's scaling gone.
    a, b = twin(tf.ModelParams.from_preset("pt-v20", **ON), 4, 4.0)
    for e in (a, b):
        e.open_market()
        e.run_session(9, 30, 3, 120)
    sa, sb = a.state_snapshot(), b.state_snapshot()
    n = len(UNIVERSE)
    unit_a, unit_b = np.asarray(f64(sa["noise_own_scale2"])), np.asarray(f64(sb["noise_own_scale2"]))
    parts_a = np.asarray(f64(sa["noise_parts"])).reshape(n, 3)
    parts_b = np.asarray(f64(sb["noise_parts"])).reshape(n, 3)
    assert unit_a[0] > 0.0 and parts_a[0, 2] != 0.0
    assert unit_b[0] == 4.0 * unit_a[0]
    assert parts_b[0, 2] == 2.0 * parts_a[0, 2]
    assert np.array_equal(unit_b[1:], unit_a[1:])
    assert np.array_equal(parts_b[1:], parts_a[1:])
    assert np.array_equal(parts_b[:, :2], parts_a[:, :2])


def test_the_overnight_own_draw_scales_by_the_root_of_the_ratio():
    # The night's own draw reads the same ratio. With the market and sector
    # parts fixed, name 0's opening move grows by its own part times
    # sqrt(k) - 1: twice as much at k = 9 as at k = 4. No other name moves.
    model = tf.ModelParams.from_preset("pt-v20", overnight_variance_ratio=0.3, **ON)
    moves = []
    for k in (1.0, 4.0, 9.0):
        _, b = twin(model, 4, k)
        b.open_market()
        moves.append(np.asarray(f64(b.state_snapshot()["pending_overnight"])))
    at1, at4, at9 = moves
    assert at4[0] != at1[0]
    assert (at9[0] - at1[0]) == pytest.approx(2.0 * (at4[0] - at1[0]), rel=1e-9)
    assert np.array_equal(at4[1:], at1[1:]) and np.array_equal(at9[1:], at1[1:])


def test_the_snapshot_carries_the_state_and_hashes_like_the_engine():
    e = engine(tf.ModelParams.from_preset("pt-v20", **ON))
    e.run_days(6, record=False)
    snap = e.state_snapshot()
    for key in KEYS:
        assert len(snap[key]) == 8 * len(UNIVERSE)
    assert manifest.state_hash(snap) == e.state_hash()
    # The state is in the hash: a ratio moved moves it.
    ratio = list(f64(snap["idio_variance"]))
    ratio[0] += 0.25
    bent = dict(snap, idio_variance=struct.pack("<%dd" % len(ratio), *ratio))
    assert manifest.state_hash(bent) != e.state_hash()


def test_a_restored_snapshot_continues_like_its_parent():
    model = tf.ModelParams.from_preset("pt-v20", **ON)
    a = engine(model)
    a.run_days(5, record=False)
    b = engine(model)
    b.restore_state(a.state_snapshot())
    assert b.state_hash() == a.state_hash()
    for e in (a, b):
        e.run_days(5, record=False)
    assert a.prices() == b.prices()
    assert a.state_hash() == b.state_hash()


def test_a_fork_continues_like_its_parent_mid_day():
    a = engine(tf.ModelParams.from_preset("pt-v20", **ON))
    a.run_days(4, record=False)
    a.open_market()
    a.run_session(9, 30, 3, 120)
    b, = tf.branch(a, 1)
    for e in (a, b):
        e.run_session(11, 30, 3, 270)
        e.close_market()
        e.run_days(3, record=False)
    assert a.prices() == b.prices()
    assert a.state_hash() == b.state_hash()


@pytest.mark.parametrize("missing", KEYS[1:])
def test_a_partial_state_is_refused(missing):
    e = engine(tf.ModelParams.from_preset("pt-v20", **ON))
    e.run_days(3, record=False)
    snap = e.state_snapshot()
    del snap[missing]
    with pytest.raises(tf.ValidationError, match="idiosyncratic variance"):
        manifest.state_hash(snap)
    with pytest.raises(tf.ValidationError, match=missing):
        engine(tf.ModelParams.from_preset("pt-v20", **ON)).restore_state(snap)


def test_a_state_of_the_wrong_width_is_refused():
    e = engine(tf.ModelParams.from_preset("pt-v20", **ON))
    e.run_days(3, record=False)
    snap = e.state_snapshot()
    snap["idio_variance"] = snap["idio_variance"][:-8]
    with pytest.raises(tf.ValidationError, match="positional"):
        engine(tf.ModelParams.from_preset("pt-v20", **ON)).restore_state(snap)


@pytest.mark.parametrize("values", [
    dict(idio_vol_alpha=-0.1),
    dict(idio_vol_beta=-0.1),
    dict(idio_vol_alpha=0.5, idio_vol_beta=0.5),
    dict(idio_vol_alpha=0.3, idio_vol_beta=0.8),
    dict(idio_vol_jump_bump=-0.5),
    dict(idio_vol_jump_bump=4.5),
])
def test_out_of_range_values_are_refused(values):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", **values)
