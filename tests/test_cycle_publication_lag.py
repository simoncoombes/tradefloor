"""The published business-cycle phase (`cycle_publication_lag`).

The phase the engine reports -- `macro_fields["cycle"]`, `macro_state.cycle`
and what reads them, a World's trace rows among them -- is the phase of
`cycle_publication_lag` sessions before, as the NBER dates a recession about
a year after it began. The true phase stays internal: prices, draws and the
earnings cycle read it, a pin sets it at once, and `state_snapshot()` carries
it for a restore. These tests hold: the dial is inert at 0.0 on every preset;
under it the published phase is the true phase N closes earlier, with the
opening phase published until N sessions have closed; a turn, natural or
pinned, is announced N sessions late; the lag moves no price; and the
snapshot, the state hash, a fork and a replayed checkpoint carry the history
only while the dial is set.
"""

import struct

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(4, seed=1))
LAG = 25


def lagged(lag=LAG, preset="pt-v20"):
    return tf.ModelParams.from_preset(preset, cycle_publication_lag=float(lag))


def true_phase(engine):
    return engine.state_snapshot()["economy"]["cycle_phase"]


def prices(engine):
    raw = engine.prices()
    return struct.unpack("<%dd" % (len(raw) // 8), raw)


def walk(engine, days, pins=None):
    """Close `days` sessions, pinning `pins[day]` before that day's session.
    Returns the true and published phase after construction and after every
    close, so `true[d]` and `published[d]` are both read after `d` closes."""
    pins = pins or {}
    true, published = [true_phase(engine)], [engine.macro_fields["cycle"]]
    for d in range(1, days + 1):
        if d in pins:
            engine.pin_macro(cycle=pins[d])
        engine.run_days(1)
        true.append(true_phase(engine))
        published.append(engine.macro_fields["cycle"])
        assert engine.macro_state.cycle == published[-1]
    return true, published


# Every preset through pt-v19. pt-v20 sets cycle_publication_lag to 252 since its graded
# arm (2026-09-26; design repository, programme/ptv20-registration.md),
# which the test below holds. Was parametrized over every preset.
@pytest.mark.parametrize("preset", [p for p in tf.preset_names() if p != "pt-v20"])
def test_off_on_every_shipped_preset(preset):
    """0.0 on every preset: the phase reported is the phase the economy is
    in, a pin reads straight back, and the snapshot carries no history."""
    assert tf.ModelParams.from_preset(preset).to_dict()["cycle_publication_lag"] == 0.0
    e = tf.Engine(seed=1, universe=UNIVERSE, model=preset)
    e.pin_macro(cycle="contraction")
    assert e.macro_fields["cycle"] == "contraction"
    assert e.macro_state.cycle == "contraction"
    assert "cycle_history" not in e.state_snapshot()["economy"]


def test_pt_v20_sets_the_graded_arms_value():
    assert tf.ModelParams.from_preset("pt-v20").to_dict()["cycle_publication_lag"] == 252.0


def test_the_published_phase_is_the_true_phase_lag_sessions_earlier():
    """Pinned turns and the natural turns that follow them: after every
    close the published phase is the true phase `LAG` closes before, and
    the opening phase until `LAG` sessions have closed."""
    e = tf.Engine(seed=2, universe=UNIVERSE, model=lagged())
    true, published = walk(e, 260, pins={10: "contraction", 150: "peak"})
    for d in range(len(true)):
        assert published[d] == true[max(d - LAG, 0)], d
    # The walk has turns in it, or it proved nothing.
    turns = [d for d in range(1, len(true)) if true[d] != true[d - 1]]
    assert len(turns) >= 3, turns


def test_a_turn_is_announced_lag_sessions_after_it_happens():
    """What a market log's turn events read: the day the published phase
    changes. Every true turn appears in the published series exactly `LAG`
    sessions later, in order, and none earlier."""
    e = tf.Engine(seed=3, universe=UNIVERSE, model=lagged())
    true, published = walk(e, 300, pins={40: "contraction"})
    turns = [(d, true[d]) for d in range(1, len(true)) if true[d] != true[d - 1]]
    announced = [(d, published[d]) for d in range(1, len(published))
                 if published[d] != published[d - 1]]
    assert turns and turns[0] == (40, "contraction")
    expected = [(d + LAG, phase) for d, phase in turns if d + LAG < len(published)]
    assert announced == expected
    # The pinned contraction is the true phase at once and is not reported
    # until it is published.
    assert all(published[d] != "contraction" for d in range(40, 40 + LAG))


def test_a_pin_is_the_true_phase_at_once():
    """A scenario that sets the phase sets the one the economy runs on;
    what an observer reads follows."""
    e = tf.Engine(seed=4, universe=UNIVERSE, model=lagged())
    opening = e.macro_fields["cycle"]
    e.pin_macro(cycle="trough" if opening != "trough" else "peak")
    assert true_phase(e) != opening
    assert e.macro_fields["cycle"] == opening


def test_the_lag_moves_no_price_and_no_draw():
    """Only what is reported moves: the same market under lag 0 and lag N
    trades the same prices, takes the same draws and lives the same true
    phase path, through a pinned turn."""
    runs = {}
    for lag in (0, LAG):
        e = tf.Engine(seed=5, universe=UNIVERSE, model=lagged(lag))
        true, _ = walk(e, 80, pins={20: "contraction"})
        runs[lag] = (prices(e), e.draws_consumed, true)
    assert runs[0] == runs[LAG]


class Flat:
    def act(self, observation):
        return {}


def test_the_trace_rows_of_a_world_report_the_published_phase():
    world = tf.World(seed=6, universe=UNIVERSE, model=lagged(3), agent=Flat(),
                     steps_per_day=1, ticks_per_step=10)
    opening = world.engine.macro_fields["cycle"]
    true = [true_phase(world.engine)]
    world.engine.pin_macro(cycle="contraction")
    assert world.engine.macro_fields["cycle"] == opening
    for _ in range(8):
        world.run(1)
        true.append(true_phase(world.engine))
    phases = [row["macro"]["cycle"] for row in world.trace]
    # Day d's row is read before its session opens, after d closes, so it
    # reports the phase as it stood after d - 3 closes, and the opening
    # until three have closed.
    assert phases == [true[max(d - 3, 0)] for d in range(8)]
    assert true[1] != opening and phases[:4] == [opening] * 4


def test_the_snapshot_and_the_hash_carry_the_history_only_while_set():
    # pt-v20 with the dial off: pt-v20 itself sets it since its graded arm (2026-09-26); was model="pt-v20"
    off = tf.Engine(seed=7, universe=UNIVERSE, model=lagged(0))
    off.run_days(3)
    assert "cycle_history" not in off.state_snapshot()["economy"]
    assert state_hash(off.state_snapshot()) == off.state_hash()

    on = tf.Engine(seed=7, universe=UNIVERSE, model=lagged())
    on.pin_macro(cycle="contraction")
    on.run_days(3)
    snap = on.state_snapshot()
    history = snap["economy"]["cycle_history"]
    assert len(history) == LAG + 1
    assert history[-1] == "contraction" and history[0] != "contraction"
    # The Python twin agrees with the engine, and the history is in both:
    # a snapshot alike but for one phase of history hashes apart.
    assert state_hash(snap) == on.state_hash()
    other = on.state_snapshot()
    other["economy"]["cycle_history"] = ["trough"] * (LAG + 1)
    assert state_hash(other) != state_hash(snap)
    twin = tf.Engine(seed=7, universe=UNIVERSE, model=lagged())
    twin.restore_state(other)
    assert twin.state_hash() == state_hash(other) != on.state_hash()


def test_a_restored_engine_continues_as_the_one_it_copied():
    parent = tf.Engine(seed=8, universe=UNIVERSE, model=lagged())
    walk(parent, 30, pins={5: "contraction"})
    child = tf.Engine(seed=8, universe=UNIVERSE, model=lagged())
    child.restore_state(parent.state_snapshot())
    assert child.state_hash() == parent.state_hash()
    assert child.macro_fields["cycle"] == parent.macro_fields["cycle"]
    assert walk(child, 40) == walk(parent, 40)
    assert child.state_hash() == parent.state_hash()


def test_a_fork_and_a_replayed_checkpoint_carry_the_history():
    parent = tf.Engine(seed=9, universe=UNIVERSE, model=lagged())
    walk(parent, 30, pins={5: "contraction"})
    fork, = tf.branch(parent, 1)
    mark = tf.Checkpoint.of(parent, universe=UNIVERSE, seed=9)
    resumed = mark.resume()
    for engine in (fork, resumed):
        assert engine.state_hash() == parent.state_hash()
    expected = walk(parent, 40)
    assert walk(fork, 40) == expected
    assert walk(resumed, 40) == expected


def test_a_restore_refuses_a_history_that_does_not_fit():
    on = tf.Engine(seed=10, universe=UNIVERSE, model=lagged())
    snap = on.state_snapshot()
    snap["economy"]["cycle_history"] = snap["economy"]["cycle_history"][1:]
    with pytest.raises(tf.ValidationError, match="cycle_publication_lag"):
        tf.Engine(seed=10, universe=UNIVERSE, model=lagged()).restore_state(snap)


def test_a_snapshot_without_a_history_reseeds_from_its_phase():
    """A snapshot written without the history, under the dial, publishes
    the phase it restores until the lag has elapsed again."""
    on = tf.Engine(seed=11, universe=UNIVERSE, model=lagged())
    on.pin_macro(cycle="contraction")
    on.run_days(1)
    snap = on.state_snapshot()
    del snap["economy"]["cycle_history"]
    fresh = tf.Engine(seed=11, universe=UNIVERSE, model=lagged())
    fresh.restore_state(snap)
    assert fresh.macro_fields["cycle"] == snap["economy"]["cycle_phase"]


@pytest.mark.parametrize("value", [-1.0, 2.5, 2521.0, float("nan")])
def test_a_lag_that_is_not_a_whole_number_of_sessions_is_refused(value):
    with pytest.raises(tf.ValidationError, match="cycle_publication_lag"):
        tf.ModelParams.from_preset("pt-v20", cycle_publication_lag=value)


def test_a_cycle_intervention_reads_the_true_phase():
    """An operation's audit trail records the phase the economy ran on, the
    value `pin_macro` writes, not the one published months later."""
    from tradefloor.interventions import TARGETS

    e = tf.Engine(seed=4, universe=UNIVERSE, model=lagged())
    opening = e.macro_fields["cycle"]
    pinned = "trough" if opening != "trough" else "peak"
    e.pin_macro(cycle=pinned)
    assert TARGETS["macro.cycle"].read(e) == pinned
    assert e.macro_fields["cycle"] == opening
