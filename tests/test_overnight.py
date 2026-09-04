"""The overnight process, held to its contract.

Nothing moved a price between sessions before `overnight_variance_ratio`:
the price after `open_market` was the price after the previous
`close_market` on every name-night. Above zero the open realises what
changed between the sessions and adds a draw on `s` with the session's own
factor composition, on a stream of its own. Four things about it can be
wrong quietly, and each is stated here: the dial could move a preset that
does not set it, the gap could land inside the session band it is meant to
sit outside, the move could vanish from the tape's decomposition, and a
checkpoint could drop the stream and restore a different night.
"""

import math
import statistics
import struct

import pytest

import tradefloor as tf

pa = pytest.importorskip("pyarrow", reason="bars are read through Arrow")

UNIVERSE = tf.Universe.random(6, seed=11)
RATIO = 0.3


def arr(buf):
    return list(struct.unpack("<%dd" % (len(buf) // 8), buf))


def run(model, days=4, ticks=120, seed=7):
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=model)
    opens, prev = [], []
    for day in range(days):
        e.open_market()
        opens.append(arr(e.column("open")))
        prev.append(arr(e.column("previous_close")))
        e.run_session(9, 30, 3, ticks)
        e.record(day)
        e.close_market()
    return e, opens, prev


def daily(e):
    t = pa.table(e.bars(grain="day")).to_pydict()
    return {(d, i): (o, c) for d, i, o, c in
            zip(t["day"], t["instrument_id"], t["open"], t["close"])}


def test_the_dial_ships_at_zero_on_every_preset():
    assert "overnight_variance_ratio" in tf.ModelParams.settable()
    for name in ("pt-v1", "pt-v10", "pt-v16", "pt-v18"):
        assert tf.ModelParams.from_preset(name).to_dict()["overnight_variance_ratio"] == 0.0
    # Naming the zero keeps the preset's own fingerprint.
    assert tf.ModelParams.from_preset("pt-v16", overnight_variance_ratio=0.0).fingerprint == "pt-v16"
    assert tf.ModelParams.from_preset("pt-v16", overnight_variance_ratio=RATIO).fingerprint.startswith("custom-")


def test_at_zero_no_price_moves_between_sessions_and_the_stream_still_draws():
    """The draws are taken unconditionally, so the schedule cannot depend on
    the dial; at zero they move nothing. The stream is not among the three
    `draws_consumed` counts, which is what keeps the known-answer digests
    where they were: `tests/known_answer.py` is the check on that."""
    e, opens, _ = run("pt-v16")
    bars = daily(e)
    for d in range(1, 4):
        for i in range(len(UNIVERSE)):
            assert bars[(d, i)][0] == bars[(d - 1, i)][1], (d, i)
    snapshot = e.state_snapshot()
    counts = snapshot["draw_counts"]
    assert len(counts) == 16
    # One normal for the market, one per sector the ENGINE keeps (the
    # sector table, not the roster's own), one per name, at each of the
    # four opens, and no uniforms at all; the day mark carries the count.
    n, sectors = len(UNIVERSE), e.day_marks()[-1]["sectors"]
    assert counts[14] == 0
    assert counts[15] == 4 * (1 + sectors + n)


def test_above_zero_the_open_is_a_gap_the_session_band_anchors_on():
    model = tf.ModelParams.from_preset("pt-v16", overnight_variance_ratio=RATIO)
    e, opens, prev = run(model)
    bars = daily(e)
    n = len(UNIVERSE)
    # The first session opens at the universe's own prices: a name has no
    # `s` before its first tick, so there is nothing to move.
    plain, _, _ = run("pt-v16", days=1)
    assert opens[0] == arr(plain.column("open"))
    # From the second day the open differs from the previous close on
    # every name, and the day's marks anchor on that open, so the +/-25%
    # session band leaves the gap outside it by construction.
    gapped = sum(1 for d in range(1, 4) for i in range(n)
                 if bars[(d, i)][0] != bars[(d - 1, i)][1])
    assert gapped == 3 * n
    for d in range(4):
        assert prev[d] == opens[d]
        for i in range(n):
            assert bars[(d, i)][0] == opens[d][i]
    # And the share read off the bars is positive where it was exactly zero.
    nights, sessions = [], []
    for d in range(1, 4):
        for i in range(n):
            o, c = bars[(d, i)]
            nights.append(math.log(o / bars[(d - 1, i)][1]))
            sessions.append(math.log(c / o))
    vg, vi = statistics.pvariance(nights), statistics.pvariance(sessions)
    assert vg > 0.0 and vg / (vg + vi) > 0.02


def test_the_night_is_on_the_tape_and_the_columns_still_sum():
    """The move lands on `s` at the open, before any tick, so the tape
    books it on the day's first row under its own column, as the jump is
    booked on the next day's first row. Without that column the truth
    table would not reconstruct the first tick of any day."""
    assert "overnight" in tf.Engine.FACTORS
    model = tf.ModelParams.from_preset("pt-v16", overnight_variance_ratio=RATIO)
    e, _, _ = run(model, days=2, ticks=60)
    n = len(UNIVERSE)
    table = pa.table(e.truth(day=1)).to_pydict()
    # The first row of the day carries the night and every later row
    # carries zero.
    first = table["overnight"][:n]
    later = table["overnight"][n:]
    assert sum(1 for v in first if v != 0.0) == n
    assert all(v == 0.0 for v in later)
    # The engine's own attribution agrees with the tape.
    booked = arr(e.attribution("overnight"))
    for i in range(n):
        assert booked[i] == pytest.approx(first[i], abs=1e-15)
    # And the ten columns sum to the change in `s` from the second row on,
    # which is the identity the tape exists for.
    s = table["mispricing_s"]
    for k in range(n, len(s)):
        total = math.fsum(table[name][k] for name in tf.Engine.FACTORS)
        assert abs((s[k] - s[k - n]) - total) < 1e-12, k


def test_a_snapshot_carries_the_overnight_stream_and_restores_the_same_night():
    model = tf.ModelParams.from_preset("pt-v16", overnight_variance_ratio=RATIO)
    e = tf.Engine(seed=7, universe=UNIVERSE, model=model)
    for day in range(2):
        e.open_market()
        e.run_session(9, 30, 3, 60)
        e.close_market()
    snapshot = e.state_snapshot()
    assert len(snapshot["rng"]) == 3 * 8
    twin = tf.Engine(seed=7, universe=UNIVERSE, model=model)
    twin.restore_state(snapshot)
    e.open_market()
    twin.open_market()
    assert arr(e.column("open")) == arr(twin.column("open"))
    e.run_session(9, 30, 3, 60)
    twin.run_session(9, 30, 3, 60)
    assert arr(e.column("price")) == arr(twin.column("price"))
    # A checkpoint from before the stream existed restores too, with this
    # engine's own seed-derived position standing in for the missing one.
    short = dict(snapshot)
    short["rng"] = list(snapshot["rng"])[:21]
    old = tf.Engine(seed=7, universe=UNIVERSE, model=model)
    old.restore_state(short)
    old.open_market()
    assert len(arr(old.column("open"))) == len(UNIVERSE)


def test_the_night_leaves_the_momentum_roll_alone():
    """A gap is not herding: the move is added to `mispricing_s_prev_close`
    as well as to `s`, so the close's momentum reads the session's own
    travel and not the night before it."""
    model = tf.ModelParams.from_preset("pt-v16", overnight_variance_ratio=RATIO)
    e = tf.Engine(seed=7, universe=UNIVERSE, model=model)
    e.open_market()
    e.run_session(9, 30, 3, 60)
    e.close_market()
    before = arr(e.column("mispricing_s_prev_close"))
    s_before = arr(e.column("mispricing_s"))
    e.open_market()
    after = arr(e.column("mispricing_s_prev_close"))
    s_after = arr(e.column("mispricing_s"))
    for i in range(len(UNIVERSE)):
        assert s_after[i] != s_before[i]
        assert after[i] - before[i] == pytest.approx(s_after[i] - s_before[i], abs=1e-15)
