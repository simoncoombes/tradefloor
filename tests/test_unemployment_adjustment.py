"""Unemployment's partial adjustment (`unemployment_adjustment_half_life`).

At each monthly release the unemployment rate moves by what the phase's
trend and Okun's law on the day's growth ask for, the drive. Off, the whole
drive arrives at once, so the first release after a contraction begins
rises about 1.2 pp, four times the spread of a release otherwise, and
announces the turn. Under the dial the rate moves by an impulse that closes
`1 - 0.5^(month / half_life)` of its gap to the drive at each release, so a
recession's rise builds over months, as it does in the data.

These tests hold: the dial is inert at 0.0 on every preset; under it the
first release after a pinned contraction barely moves where it rises
without, and the rise builds release on release; and the snapshot, the
state hash, a fork and a replayed checkpoint carry the impulse only while
the dial is set.
"""

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

UNIVERSE = list(tf.Universe.random(4, seed=1))
HALF_LIFE = 84.0
MONTH = 21


def adjusted(half_life=HALF_LIFE, preset="pt-v20"):
    return tf.ModelParams.from_preset(
        preset, unemployment_adjustment_half_life=float(half_life))


def economy(engine):
    return engine.state_snapshot()["economy"]


def unemployment_path(model, days, seed=2, contraction_on=3):
    """The unemployment rate (fractional) after every close of a market
    pinned into a contraction before session `contraction_on`, with growth
    pinned to -1 per cent as the turn's shock would leave it (a pinned
    phase keeps its age, so the engine's own shock does not fire)."""
    e = tf.Engine(seed=seed, universe=UNIVERSE, model=model)
    out = [e.macro_fields["unemployment_rate"]]
    for d in range(1, days + 1):
        if d == contraction_on:
            e.pin_macro(cycle="contraction", gdp_growth=-0.01)
        e.run_days(1)
        out.append(e.macro_fields["unemployment_rate"])
    return out


def releases(path):
    """Each monthly release's change, in pp, zeros included."""
    return [(path[d] - path[d - 1]) * 100 for d in range(MONTH, len(path), MONTH)]


@pytest.mark.parametrize("preset", tf.preset_names())
def test_off_on_every_shipped_preset(preset):
    assert tf.ModelParams.from_preset(preset).to_dict()[
        "unemployment_adjustment_half_life"] == 0.0
    e = tf.Engine(seed=1, universe=UNIVERSE, model=preset)
    e.run_days(2)
    assert "unemployment_impulse" not in economy(e)


def test_the_impulse_opens_at_its_drive_and_moves_only_at_a_release():
    e = tf.Engine(seed=3, universe=UNIVERSE, model=adjusted())
    impulses = [economy(e)["unemployment_impulse"]]
    for _ in range(2 * MONTH + 2):
        e.run_days(1)
        impulses.append(economy(e)["unemployment_impulse"])
    moved = [d for d in range(1, len(impulses)) if impulses[d] != impulses[d - 1]]
    assert moved == [d for d in range(1, len(impulses)) if d % MONTH == 0]


def test_a_contraction_arrives_in_unemployment_over_months():
    """Pinned into a contraction: without the dial the first release takes
    the whole step; with it, the impulse turns from the expansion's fall
    toward the contraction's rise over months, so the first release barely
    moves and the rise builds release on release."""
    off = releases(unemployment_path(tf.ModelParams.from_preset("pt-v20"), 6 * MONTH))
    on = releases(unemployment_path(adjusted(), 6 * MONTH))
    assert off[0] > 0.5
    assert on[0] < 0.2 * off[0]
    # The rise builds: the releases climb through the contraction (it turns
    # to trough about four months in, at this seed), and no release carries
    # a step like the one the drive takes whole.
    assert on[3] > on[2] > on[1]
    assert max(on) < 0.5 * max(off)


def test_the_snapshot_and_the_hash_carry_the_impulse_only_while_set():
    off = tf.Engine(seed=4, universe=UNIVERSE, model="pt-v20")
    off.run_days(3)
    assert state_hash(off.state_snapshot()) == off.state_hash()

    on = tf.Engine(seed=4, universe=UNIVERSE, model=adjusted())
    on.run_days(MONTH + 2)
    snap = on.state_snapshot()
    assert state_hash(snap) == on.state_hash()
    other = on.state_snapshot()
    other["economy"]["unemployment_impulse"] += 0.1
    assert state_hash(other) != state_hash(snap)
    twin = tf.Engine(seed=4, universe=UNIVERSE, model=adjusted())
    twin.restore_state(other)
    assert twin.state_hash() == state_hash(other) != on.state_hash()


def test_a_restored_engine_a_fork_and_a_checkpoint_continue_as_the_parent():
    parent = tf.Engine(seed=5, universe=UNIVERSE, model=adjusted())
    parent.pin_macro(cycle="contraction")
    parent.run_days(MONTH + 3)
    child = tf.Engine(seed=5, universe=UNIVERSE, model=adjusted())
    child.restore_state(parent.state_snapshot())
    fork, = tf.branch(parent, 1)
    resumed = tf.Checkpoint.of(parent, universe=UNIVERSE, seed=5).resume()
    for engine in (child, fork, resumed):
        assert engine.state_hash() == parent.state_hash()
    parent.run_days(2 * MONTH)
    for engine in (child, fork, resumed):
        engine.run_days(2 * MONTH)
        assert engine.state_hash() == parent.state_hash()
        assert engine.macro_fields == parent.macro_fields


def test_a_snapshot_without_the_impulse_reseeds_it():
    on = tf.Engine(seed=6, universe=UNIVERSE, model=adjusted())
    on.run_days(MONTH + 1)
    snap = on.state_snapshot()
    del snap["economy"]["unemployment_impulse"]
    fresh = tf.Engine(seed=6, universe=UNIVERSE, model=adjusted())
    fresh.restore_state(snap)
    assert "unemployment_impulse" in economy(fresh)


def test_a_restore_refuses_an_impulse_where_the_dial_is_off():
    on = tf.Engine(seed=7, universe=UNIVERSE, model=adjusted())
    snap = on.state_snapshot()
    del snap["model_fingerprint"]
    with pytest.raises(tf.ValidationError, match="unemployment_adjustment_half_life is 0"):
        tf.Engine(seed=7, universe=UNIVERSE, model="pt-v20").restore_state(snap)


def test_a_restore_refuses_a_non_finite_impulse():
    on = tf.Engine(seed=8, universe=UNIVERSE, model=adjusted())
    snap = on.state_snapshot()
    snap["economy"]["unemployment_impulse"] = float("nan")
    with pytest.raises(tf.ValidationError, match="unemployment_impulse"):
        tf.Engine(seed=8, universe=UNIVERSE, model=adjusted()).restore_state(snap)


@pytest.mark.parametrize("value", [-1.0, 2521.0, float("nan")])
def test_a_half_life_out_of_range_is_refused(value):
    with pytest.raises(tf.ValidationError, match="unemployment_adjustment_half_life"):
        tf.ModelParams.from_preset("pt-v20", unemployment_adjustment_half_life=value)
