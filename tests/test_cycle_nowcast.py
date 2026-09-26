"""The market's cycle nowcast (`cycle_nowcast_accuracy`) and the corporate
spread's blend (`corporate_spread_cycle`): the phase re-anchor (r13 audit).

With both at 0.0, which every preset ships, the market prices the TRUE cycle
phase: the earnings anticipation's phase term jumps by `g_next - g` at the
close of every true turn (the repricing prints it as a re-mark of about 4 per
cent), and the first meeting after a turn re-anchors the corporate spread to
the new phase's multiplier (111 bp on average on pt-v20). Off zero the market
prices a filtered belief over the phase, and the spread's daily move carries
the meeting formula's whole change, so a meeting finds the level where it is.

These tests hold that both are off everywhere and inert at 0.0, that the
engine refuses values outside their domains, that a true turn is not a close
re-mark and the first meeting after it takes no step, that a pin is public
news priced exactly as pt-v20 prices it, and that the belief and its
generator are carried in the snapshot and both state hashes only while set.
The belief's occupancy and its stream are held by the engine's unit tests
(`the_cycle_nowcast_tracks_the_occupancy_on_its_own_stream`).
"""

import struct

import pytest

import tradefloor as tf
from tradefloor import manifest

UNIVERSE = list(tf.Universe.random(3, seed=3))
ARM = {"cycle_nowcast_accuracy": 0.4, "corporate_spread_cycle": 0.75}
# Seed 7 on this roster at 26 ticks a session turns four times in 480
# sessions (peak to contraction, to trough, to recovery, to expansion), each
# with a meeting after it inside the window.
SEED, SESSIONS, TICKS = 7, 480, 26


def floats(raw):
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def engine(seed=SEED, **dials):
    return tf.Engine(seed=seed, universe=UNIVERSE,
                     model=tf.ModelParams.from_preset("pt-v20", **dials))


def history(**dials):
    """Per close: the true phase, the anticipation A - e, the spread in
    per cent and whether a meeting was held."""
    e = engine(**dials)
    snap = e.state_snapshot()
    last = snap["central_bank"]["last_meeting_date"]
    rows = []
    for day in range(SESSIONS):
        e.run_days(1, ticks_per_day=TICKS, record=False, first_day=day)
        snap = e.state_snapshot()
        econ = snap["economy"]
        meeting = snap["central_bank"]["last_meeting_date"] != last
        last = snap["central_bank"]["last_meeting_date"]
        rows.append((econ["cycle_phase"], e.earnings_anticipation,
                     econ["corporate_bond_yield"] - econ["treasury_yield_10y"],
                     meeting))
    return rows


@pytest.fixture(scope="module")
def runs():
    return {"ctl": history(), "arm": history(**ARM)}


def turn_moves(rows):
    """|dA| in per cent at the closes a true turn happened on, and at the rest."""
    turns, ordinary = [], []
    for (p0, a0, _, _), (p1, a1, _, _) in zip(rows, rows[1:]):
        (turns if p1 != p0 else ordinary).append(100 * abs(a1 - a0))
    return turns, ordinary


def meeting_steps(rows):
    """|spread change| in bp at the first meeting after each true turn."""
    steps, turned = [], False
    for (p0, _, s0, _), (p1, _, s1, meeting) in zip(rows, rows[1:]):
        turned = turned or p1 != p0
        if meeting and turned:
            steps.append(100 * abs(s1 - s0))
            turned = False
    return steps


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    d = tf.ModelParams.from_preset(preset).to_dict()
    assert d["cycle_nowcast_accuracy"] == 0.0
    assert d["corporate_spread_cycle"] == 0.0


def test_at_zero_nothing_moves_and_nothing_is_carried():
    a = engine()
    b = engine(cycle_nowcast_accuracy=0.0, corporate_spread_cycle=0.0)
    assert b.model.fingerprint == "pt-v20"
    for e in (a, b):
        e.run_days(20, record=False)
    assert floats(a.prices()) == floats(b.prices())
    assert a.state_hash() == b.state_hash()
    snap = b.state_snapshot()
    assert "cycle_nowcast" not in snap["economy"]
    assert "cycle_nowcast_rng" not in snap


@pytest.mark.parametrize("value", [-0.1, 0.1, 0.2, 1.1])
def test_the_accuracy_is_zero_or_a_report_that_carries_something(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", cycle_nowcast_accuracy=value)


@pytest.mark.parametrize("value", [-0.1, 1.5])
def test_the_blend_is_a_share(value):
    with pytest.raises(Exception):
        tf.ModelParams.from_preset("pt-v20", corporate_spread_cycle=value)


def test_each_dial_moves_the_market():
    base = engine()
    base.run_days(20, record=False)
    for dials in ({"cycle_nowcast_accuracy": 0.4}, {"corporate_spread_cycle": 0.75}):
        e = engine(**dials)
        e.run_days(20, record=False)
        assert floats(e.prices()) != floats(base.prices()), dials


def test_a_true_turn_is_not_a_close_re_mark(runs):
    # pt-v20 as graded: the turns into contraction, trough and recovery move
    # A by 3-4 per cent at their own close, more than any ordinary close.
    turns, ordinary = turn_moves(runs["ctl"])
    assert len(turns) >= 4
    assert max(turns) > 3.0 and max(turns) > max(ordinary)
    # Under the nowcast the turn's close is an ordinary close: each well
    # under a per cent, half a per cent on average, none above the largest
    # move the belief's reports make on the closes without a turn.
    turns, ordinary = turn_moves(runs["arm"])
    assert len(turns) >= 4
    assert max(turns) < 1.0, turns
    assert sum(turns) / len(turns) < 0.5, turns
    assert max(turns) <= max(ordinary), (turns, max(ordinary))


def test_the_first_meeting_after_a_turn_takes_no_step(runs):
    ctl = meeting_steps(runs["ctl"])
    arm = meeting_steps(runs["arm"])
    assert len(ctl) >= 4 and len(arm) >= 4
    assert max(ctl) > 50.0, ctl
    assert max(arm) < 25.0, arm


def test_a_pinned_phase_is_priced_exactly_as_pt_v20_prices_it():
    # A pin is public news: the belief goes one-hot on the pinned phase and
    # pi . g is then g of that phase, the anticipation pt-v20 prices; the
    # close takes no report, so it holds there through the close.
    for phase in ("contraction", "trough", "expansion"):
        pair = [engine(), engine(**ARM)]
        for e in pair:
            e.run_days(3, record=False)
            e.pin_macro(cycle=phase)
        assert pair[0].earnings_anticipation == pair[1].earnings_anticipation
        pi = pair[1].state_snapshot()["economy"]["cycle_nowcast"]
        k = ("expansion", "peak", "contraction", "trough", "recovery").index(phase)
        assert pi == [1.0 if j == k else 0.0 for j in range(5)]
        for e in pair:
            e.run_days(1, record=False, first_day=3)
        assert pair[1].state_snapshot()["economy"]["cycle_nowcast"] == pi


def test_the_belief_is_carried_and_hashed_only_while_set():
    e = engine(**ARM)
    e.run_days(10, record=False)
    snap = e.state_snapshot()
    pi = snap["economy"]["cycle_nowcast"]
    assert len(pi) == 5 and abs(sum(pi) - 1.0) < 1e-12
    assert len(snap["cycle_nowcast_rng"]) == 5
    # The Python twin of the state hash covers it, as the engine's does.
    assert manifest.state_hash(snap) == e.state_hash()
    edited = e.state_snapshot()
    edited["economy"]["cycle_nowcast"] = [0.2] * 5
    assert manifest.state_hash(edited) != manifest.state_hash(snap)
    half = e.state_snapshot()
    del half["cycle_nowcast_rng"]
    with pytest.raises(Exception):
        manifest.state_hash(half)

    # A restore reproduces the market, belief and generator included.
    twin = engine(**ARM)
    twin.restore_state(snap)
    assert twin.state_hash() == e.state_hash()
    for x in (e, twin):
        x.run_days(5, record=False, first_day=10)
    assert floats(e.prices()) == floats(twin.prices())
    assert (e.state_snapshot()["economy"]["cycle_nowcast"]
            == twin.state_snapshot()["economy"]["cycle_nowcast"])

    # Refused where the dial is off; re-seeded one-hot where the snapshot
    # carries none.
    with pytest.raises(Exception):
        engine().restore_state(snap)
    bare = e.state_snapshot()
    del bare["economy"]["cycle_nowcast"]
    del bare["cycle_nowcast_rng"]
    fresh = engine(**ARM)
    fresh.restore_state(bare)
    k = ("expansion", "peak", "contraction", "trough",
         "recovery").index(bare["economy"]["cycle_phase"])
    assert (fresh.state_snapshot()["economy"]["cycle_nowcast"]
            == [1.0 if j == k else 0.0 for j in range(5)])
