"""The crisis epicentre: which sector carries a crisis.

A crisis EPISODE starts on the session whose VIX is above
`crisis_vix_threshold` with no episode running, and ends after
`crisis_epicentre_end_sessions` consecutive sessions back under it. At the
start of each episode one epicentre is drawn from the sector table's
`crisis_weight`, `none` being the remainder and a draw in its own right. While
the episode runs, the names in that sector carry an extra multiple on the
parts of their return that are NOT the market factor.

The dial ships at 0.0 and the branch is not taken, so this file's first job is
the bit-identity: at the shipped value nothing runs, no state moves and no
draw is taken on any stream. The rest of it is the mechanism, one behaviour at
a time. Magnitudes -- the per-sector lever, the graded rows -- belong to the
calibration record and not here.
"""

from __future__ import annotations

import pytest

import tradefloor as tf
from tradefloor import Scenario, noise

#: Small and fixed. The epicentre is a per-SESSION property, so the questions
#: here are about days rather than about how many names there are, and a
#: twelve-name roster runs them in under a second.
UNIVERSE = tuple(tf.Universe.random(12, seed=111))
SEED = 4242
TICKS = 26

#: The DERIVED extra: the median of the tape's three epicentre episodes
#: (2.43 in 2008-09, 1.93 in 2011, 1.41 in 2020). See
#: `ModelParams::crisis_epicentre_extra`.
EXTRA = 1.93

#: Over the 30.88 threshold pt-v19 ships, and well clear of it.
CRISIS_VIX = 65.0
#: Under it.
CALM_VIX = 12.0


def engine(model=None, macro=None):
    return tf.Engine(seed=SEED, universe=list(UNIVERSE), model=model,
                     macro_state=macro)


def live(**over):
    over.setdefault("crisis_epicentre_extra", EXTRA)
    return tf.ModelParams.from_preset("pt-v19", **over)


def run_days(e, vixes, scenario=None, first=0):
    """One session per entry in `vixes`, at that VIX."""
    for i, vix in enumerate(vixes):
        sc = scenario if scenario is not None else Scenario()
        # A fresh pin each day rather than a held one: this drives the VIX
        # up and down, which is exactly what a `hold` refuses to do.
        e.pin_macro(vix=vix)
        if scenario is not None:
            scenario.apply(e, first + i)
        e.open_market()
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    return e


def prices(e):
    return e.column("price")


# ---------------------------------------------------------------------------
# 1. Inert at the shipped value
# ---------------------------------------------------------------------------

def test_the_shipped_value_is_bit_identical_and_takes_no_draw():
    """0.0 is the branch not taken, everywhere.

    Not "close": the same bytes. The mechanism's whole licence to exist is
    that a preset written before it reproduces exactly, and the two halves of
    that are the trajectory and the draw schedule. A market run under a VIX
    of 65 for thirty sessions is the hardest case, because it is inside a
    crisis for every one of them.
    """
    vixes = [CRISIS_VIX] * 30
    base = run_days(engine(tf.ModelParams.from_preset("pt-v19")), vixes)
    # The same preset spelled through a dial that is already at its shipped
    # value. If `from_preset` and `from_preset(dial=shipped)` differed, this
    # test would be measuring the spelling.
    same = run_days(engine(tf.ModelParams.from_preset(
        "pt-v19", crisis_epicentre_extra=0.0)), vixes)

    assert prices(same) == prices(base)
    assert same.state_snapshot()["columns"] == base.state_snapshot()["columns"]
    assert same.draws_by_stream() == base.draws_by_stream()
    # No episode, so nothing for a snapshot to carry.
    assert base.crisis_episode == (False, 0, None)
    assert base.state_snapshot()["crisis_in_episode"] is False
    assert base.state_snapshot()["crisis_epicentre"] == -1


def test_the_hysteresis_is_inert_while_the_extra_is_zero():
    """`crisis_epicentre_end_sessions` is read inside an episode and there
    are no episodes at 0.0, so no value of it can move anything."""
    vixes = [CRISIS_VIX] * 5 + [CALM_VIX] * 5 + [CRISIS_VIX] * 5
    base = run_days(engine(tf.ModelParams.from_preset("pt-v19")), vixes)
    other = run_days(engine(tf.ModelParams.from_preset(
        "pt-v19", crisis_epicentre_end_sessions=2.0)), vixes)
    assert prices(other) == prices(base)


# ---------------------------------------------------------------------------
# 2. The draw, and the stream it is on
# ---------------------------------------------------------------------------

def test_the_draw_happens_only_on_the_new_stream():
    """One uniform per EPISODE, on `crisis_epicentre`, and nowhere else.

    This is the property that lets the mechanism exist at all. A draw on any
    stream a price already reads would shift every later draw on it and move
    every recorded trajectory the moment the code landed, inert dial or not.
    """
    e = engine(live())
    for stream in noise.STREAMS:
        e.trace_draws(stream, -1000, 1000)
    # Two episodes: a crisis, a month of calm long enough to end it, then a
    # second crisis.
    run_days(e, [CRISIS_VIX] * 3 + [CALM_VIX] * 22 + [CRISIS_VIX] * 3)

    log = e.draw_log("crisis_epicentre", -1000, 1000)
    assert len(log) == 2, log
    # Uniforms, not normals, and both from the one site. An entry is
    # `((stream, kind, index), value, day, site, tag)`.
    assert {entry[0][1] for entry in log} == {"uniform"}
    assert {entry[3] for entry in log} == {"crisis_epicentre_u"}
    # One at the first session and one at the session the second episode
    # starts, which is the 26th (0-based 25).
    assert [entry[2] for entry in log] == [0, 25]

    # And the schedule everywhere else is the schedule the shipped preset
    # runs, draw for draw.
    base = engine(tf.ModelParams.from_preset("pt-v19"))
    for stream in noise.STREAMS:
        base.trace_draws(stream, -1000, 1000)
    run_days(base, [CRISIS_VIX] * 3 + [CALM_VIX] * 22 + [CRISIS_VIX] * 3)
    for stream in noise.STREAMS:
        if stream == "crisis_epicentre":
            assert base.draw_log(stream, -1000, 1000) == []
            continue
        assert (len(e.draw_log(stream, -1000, 1000))
                == len(base.draw_log(stream, -1000, 1000))), stream


def test_the_weights_are_the_sector_table_and_none_is_the_remainder():
    """`financial_services` 0.6, `none` 0.4, everything else 0.0.

    Read off the outcome rather than off the table, because the table is what
    a future edit would change and this is the claim that would have to
    change with it. Sixty-four seeds, each entering one episode.
    """
    drawn = []
    for seed in range(200, 264):
        e = tf.Engine(seed=seed, universe=list(UNIVERSE), model=live())
        run_days(e, [CRISIS_VIX])
        drawn.append(e.crisis_episode[2])
    assert set(drawn) == {"financial_services", "none"}, sorted(set(drawn))
    share = drawn.count("financial_services") / len(drawn)
    # 0.6 on 64 draws has a standard error of 0.061, so this is the weight
    # within three of them. A band rather than a point, because the claim is
    # the weight and not this seed block.
    assert 0.42 <= share <= 0.78, share


# ---------------------------------------------------------------------------
# 3. The episode's start and end
# ---------------------------------------------------------------------------

def test_the_episode_starts_on_the_crossing_and_ends_after_the_hysteresis():
    """Up over the threshold starts it; 21 sessions under it ends it."""
    e = engine(live())
    run_days(e, [CALM_VIX] * 3)
    assert e.crisis_episode == (False, 0, None)

    run_days(e, [CRISIS_VIX])
    in_episode, under, who = e.crisis_episode
    assert in_episode and under == 0 and who is not None

    # Twenty sessions under the threshold is not enough: the shipped
    # hysteresis is twenty-one.
    run_days(e, [CALM_VIX] * 20)
    assert e.crisis_episode[0] is True
    assert e.crisis_episode[1] == 20
    assert e.crisis_episode[2] == who

    run_days(e, [CALM_VIX])
    assert e.crisis_episode == (False, 0, None)


def test_a_session_back_over_the_threshold_resets_the_counter():
    """Which is what makes one crisis one episode rather than a run of
    scattered days, and what the counter exists for."""
    e = engine(live())
    run_days(e, [CRISIS_VIX] + [CALM_VIX] * 15)
    assert e.crisis_episode[1] == 15
    first = e.crisis_episode[2]
    run_days(e, [CRISIS_VIX])
    assert e.crisis_episode[1] == 0
    # The same episode, so the same epicentre: it was not redrawn.
    assert e.crisis_episode[2] == first
    run_days(e, [CALM_VIX] * 20)
    assert e.crisis_episode[0] is True


def test_the_hysteresis_dial_moves_where_the_episode_ends():
    e = engine(live(crisis_epicentre_end_sessions=3.0))
    run_days(e, [CRISIS_VIX] + [CALM_VIX] * 2)
    assert e.crisis_episode[0] is True
    run_days(e, [CALM_VIX])
    assert e.crisis_episode == (False, 0, None)


# ---------------------------------------------------------------------------
# 4. The pin
# ---------------------------------------------------------------------------

def test_a_pinned_epicentre_takes_no_draw():
    """The pin REPLACES the draw rather than overriding its result.

    So a pinned run consumes nothing at all on the epicentre's stream, and
    that is checked here rather than inferred: a pin that drew and then threw
    the value away would read identically on the epicentre and differently on
    every later episode.
    """
    e = engine(live())
    e.trace_draws("crisis_epicentre", -1000, 1000)
    sc = Scenario().hold(vix=CRISIS_VIX, epicentre="financial_services")
    for day in range(3):
        sc.apply(e, day)
        e.open_market()
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    assert e.crisis_episode == (True, 0, "financial_services")
    assert e.draw_log("crisis_epicentre", -1000, 1000) == []


def test_none_is_a_pinnable_value_and_not_the_absence_of_one():
    e = engine(live())
    e.trace_draws("crisis_epicentre", -1000, 1000)
    sc = Scenario().hold(vix=CRISIS_VIX, epicentre="none")
    for day in range(3):
        sc.apply(e, day)
        e.open_market()
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    assert e.crisis_episode == (True, 0, "none")
    assert e.draw_log("crisis_epicentre", -1000, 1000) == []


def test_a_pin_to_none_prices_as_no_epicentre_does():
    """`none` is a crisis nobody is at the centre of, so it must price
    exactly as the mechanism switched off does -- not merely close to it."""
    sc = Scenario().hold(vix=CRISIS_VIX, epicentre="none")
    pinned = engine(live())
    for day in range(8):
        sc.apply(pinned, day)
        pinned.open_market()
        pinned.run_session(9, 30, 3, TICKS)
        pinned.close_market()
    off = run_days(engine(tf.ModelParams.from_preset("pt-v19")),
                   [CRISIS_VIX] * 8)
    assert prices(pinned) == prices(off)


def test_a_misspelt_epicentre_is_refused_where_it_is_written():
    with pytest.raises(Exception) as caught:
        Scenario().hold(epicentre="financials")
    assert "financials" in str(caught.value)


# ---------------------------------------------------------------------------
# 5. What the multiple reaches, and what it does not
# ---------------------------------------------------------------------------

def _by_sector(e):
    out = {}
    for i, u in enumerate(UNIVERSE):
        out.setdefault(u.sector, []).append(i)
    return out


def test_only_the_epicentre_sector_moves():
    """The names of one sector move and every other name is bit-identical.

    The strongest statement the mechanism can make about its own reach, and
    the one that says the market component was left alone: the market factor
    and the sector draws are the same numbers in both runs, so a name outside
    the epicentre sees an unchanged tick.
    """
    sectors = _by_sector(engine(live()))
    target = next(s for s, idx in sectors.items() if idx)
    sc = Scenario().hold(vix=CRISIS_VIX, epicentre=target)

    def go(model):
        e = engine(model)
        for day in range(6):
            sc.apply(e, day)
            e.open_market()
            e.run_session(9, 30, 3, TICKS)
            e.close_market()
        import struct
        n = len(UNIVERSE)
        return struct.unpack("<%dd" % n, e.column("price"))

    off = go(tf.ModelParams.from_preset("pt-v19"))
    on = go(live())
    moved = {i for i in range(len(UNIVERSE)) if on[i] != off[i]}
    assert moved == set(sectors[target]), (target, moved, sectors)


def test_the_multiple_is_the_solve_and_not_the_dial():
    """`g = sqrt((e^2 - m) / (1 - m))`, with `m` the market factor's measured
    share. At the derived extra that is 2.3407 and not 1.93.

    Checked through the engine's own arithmetic rather than restated: the
    extra at which the gain is exactly one is `sqrt(m)`-free, since
    `e = 1` gives `g^2 = (1 - m) / (1 - m) = 1`.
    """
    e = engine(live(crisis_epicentre_extra=1.0))
    sectors = _by_sector(e)
    target = next(iter(sectors))
    sc = Scenario().hold(vix=CRISIS_VIX, epicentre=target)
    for day in range(4):
        sc.apply(e, day)
        e.open_market()
        e.run_session(9, 30, 3, TICKS)
        e.close_market()
    off = run_days(engine(tf.ModelParams.from_preset("pt-v19")),
                   [CRISIS_VIX] * 4)
    # An extra of exactly 1.0 solves to a gain of exactly 1.0, so the
    # epicentre's names are multiplied by one -- and the whole market is
    # bit-identical to the mechanism switched off.
    assert prices(e) == prices(off)


def test_an_extra_under_the_market_share_is_refused():
    """The market factor carries 0.3916 of a name's variance and is not
    scaled, so an extra at or below sqrt(0.3916) asks the non-market parts to
    carry a negative variance. Refused where it is set, not at the tick."""
    with pytest.raises(Exception) as caught:
        tf.ModelParams.from_preset("pt-v19", crisis_epicentre_extra=0.5)
    assert "crisis_epicentre_extra" in str(caught.value)


# ---------------------------------------------------------------------------
# 6. Snapshot and restore
# ---------------------------------------------------------------------------

def test_a_restored_snapshot_continues_like_a_copy_mid_episode():
    """`branch` copies the engine and is complete by construction;
    `state_snapshot` is a written list of fields. Mid-EPISODE on purpose:
    the four fields the episode adds are exactly the ones an engine outside
    an episode has nothing of, and a restore taken on a calm day would pass
    with none of them carried.

    The continuation runs the episode to its end and starts a second one, so
    the epicentre, the counter and the stream position all have to be right
    rather than merely present.
    """
    parent = engine(live())
    run_days(parent, [CRISIS_VIX] * 2 + [CALM_VIX] * 5)
    assert parent.crisis_episode[0] is True
    assert parent.crisis_episode[1] == 5

    reference, = tf.branch(parent, 1)
    restored = engine(live())
    restored.restore_state(parent.state_snapshot())
    assert restored.crisis_episode == parent.crisis_episode

    tail = [CALM_VIX] * 20 + [CRISIS_VIX] * 3
    run_days(reference, tail)
    run_days(restored, tail)
    assert restored.crisis_episode == reference.crisis_episode
    # A SECOND episode was entered in the tail, so the stream position was
    # carried and not merely the flag.
    assert restored.crisis_episode[0] is True
    assert prices(restored) == prices(reference)


def test_a_snapshot_carries_the_pin():
    """A fork that dropped it would resume drawing its own epicentre
    part-way through a pinned experiment."""
    parent = engine(live())
    sc = Scenario().hold(vix=CRISIS_VIX, epicentre="financial_services")
    sc.apply(parent, 0)
    run_days(parent, [CRISIS_VIX])
    restored = engine(live())
    restored.restore_state(parent.state_snapshot())
    assert restored.state_snapshot()["crisis_epicentre_pin"] == \
        parent.state_snapshot()["crisis_epicentre_pin"]
    # The pin survives into a NEW episode, which is where it is read.
    tail = [CALM_VIX] * 21 + [CRISIS_VIX]
    run_days(restored, tail)
    assert restored.crisis_episode == (True, 0, "financial_services")


def test_the_manifest_hash_covers_the_episode():
    """Two engines alike in every column and in different episodes hash
    apart, on both sides of the boundary."""
    from tradefloor import manifest

    a = engine(live())
    run_days(a, [CRISIS_VIX])
    snapshot = a.state_snapshot()
    base = manifest.state_hash(snapshot)
    for key, value in (("crisis_in_episode", False),
                       ("crisis_sessions_under", 7),
                       ("crisis_epicentre", 3),
                       ("crisis_epicentre_pin", 1)):
        moved = dict(snapshot)
        moved[key] = value
        assert manifest.state_hash(moved) != base, key
