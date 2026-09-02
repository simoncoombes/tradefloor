"""The per-name volume array follows the roster, and what that reaches.

`volume_idio` holds one state per company and is positional against the
roster, like `attribution`, `tick_components`, `tick_fundamental` and
`tick_anchor`. Those four are appended and removed by `add_company` and
`remove_company`, and `open_market` resizes them again at every open.
`volume_idio` was in neither place until issue #148, so its width stayed at
whatever the engine was constructed with.

Three things read that width.

`update_volume_idio` draws once per SLOT at every close, before the zero
check, so the per-day draw count from the volume-idio stream was the stale
width. The tick reads a company's state at that company's own index, so
after a delisting every survivor read its old neighbour's state. And
`state_snapshot` carries the array, which `restore_state` refuses at a width
its roster disagrees with.

## The authorised change

Resizing the array changes the number of draws a listing or delisting run
takes from the volume-idio stream, and therefore that stream's position in
every later snapshot of such a run. That was authorised for this fix on
2026-09-02. Nothing else moves: the other six streams are independent by
construction, all sixteen shipped presets hold `volume_idio_sigma` and
`volume_idio_persistence` at 0.0 so every entry stays exactly 0.0 and no
shipped price path moves, and a run with a fixed roster takes the draws it
always took.

The pre-fix engine is expressed here as a draw COUNT rather than as a digest
copied out of a run. A run's volume-idio position is a pure function of its
seed and the number of draws it has taken, which
`test_the_stream_position_is_a_function_of_the_draw_count` establishes
first. The pre-fix count is the construction width once per day, so a
fixed-roster run of that width and that many days reaches the position the
pre-fix build reached, and the tests compare against that.
"""

from __future__ import annotations

import math
import struct

import pytest

import tradefloor as tf

SEED = 42
UNIVERSE_SEED = 99

#: Order of the streams in `state_snapshot()["rng"]`, three numbers each.
STREAMS = ("market", "economy", "external", "jumps", "volume", "news",
           "volume_idio")

IPO = tf.Instrument("IPO", "energy", initial_price=33.0,
                    shares_outstanding=5e7, eps=1.5)


def engine(n: int) -> tf.Engine:
    return tf.Engine(seed=SEED, universe=tf.Universe.random(n,
                                                            seed=UNIVERSE_SEED))


def stream_state(snapshot: dict, name: str) -> tuple:
    """One stream's (state, increment, spare), comparable across runs.

    The spare is NaN whenever no Box-Muller half is cached, and NaN compares
    unequal to itself, so it goes in as a marker rather than as a float.
    """
    base = 3 * STREAMS.index(name)
    return tuple("unset" if math.isnan(v) else struct.pack("<d", v)
                 for v in snapshot["rng"][base:base + 3])


def widths(engine: tf.Engine) -> tuple[int, int]:
    """(roster, per-name volume states) as the snapshot reports them."""
    snapshot = engine.state_snapshot()
    return len(engine), len(snapshot["volume_idio"]) // 8


def states(engine: tf.Engine) -> list[float]:
    raw = engine.state_snapshot()["volume_idio"]
    return list(struct.unpack(f"<{len(raw) // 8}d", raw))


def mutating_run() -> tf.Engine:
    """Eight names, then nine, then eight, one day under each width."""
    e = engine(8)
    e.run_days(1)
    e.list_instrument(IPO)
    e.run_days(1)
    e.delist(0)
    e.run_days(1)
    return e


# --------------------------------------------------------------------------
# The width follows the roster
# --------------------------------------------------------------------------

def test_a_listing_widens_the_snapshots_per_name_volume_array():
    e = engine(8)
    e.run_days(1)
    assert widths(e) == (8, 8)
    e.list_instrument(IPO)
    assert widths(e) == (9, 9)


def test_a_delisting_narrows_the_snapshots_per_name_volume_array():
    e = engine(8)
    e.run_days(1)
    e.delist(0)
    assert widths(e) == (7, 7)


def test_the_width_tracks_the_roster_through_a_whole_run():
    e = engine(8)
    seen = [widths(e)]
    e.run_days(1)
    e.list_instrument(IPO)
    seen.append(widths(e))
    e.run_days(1)
    e.delist(0)
    seen.append(widths(e))
    e.run_days(1)
    seen.append(widths(e))
    assert seen == [(8, 8), (9, 9), (8, 8), (8, 8)]


# --------------------------------------------------------------------------
# The snapshot round trip
# --------------------------------------------------------------------------

def _restored_copy(source: tf.Engine, edits) -> tf.Engine:
    """A second engine carrying the same edits, restored from `source`.

    `restore_state` compares tickers, so the target has to reach the same
    roster before it can accept the snapshot at all.
    """
    target = engine(8)
    edits(target)
    target.restore_state(source.state_snapshot())
    return target


def test_a_snapshot_round_trips_after_a_listing():
    e = engine(8)
    e.run_days(1)
    e.list_instrument(IPO)
    e.run_days(1)

    restored = _restored_copy(e, lambda t: t.list_instrument(IPO))
    assert widths(restored) == widths(e) == (9, 9)
    assert restored.prices() == e.prices()
    assert states(restored) == states(e)


def test_a_snapshot_round_trips_after_a_delisting():
    e = engine(8)
    e.run_days(1)
    e.delist(0)
    e.run_days(1)

    restored = _restored_copy(e, lambda t: t.delist(0))
    assert widths(restored) == widths(e) == (7, 7)
    assert restored.prices() == e.prices()
    assert states(restored) == states(e)


def test_a_restored_engine_continues_the_same_market():
    """A round trip that is checked by running on, rather than by comparing
    the fields that were just copied."""
    e = engine(8)
    e.run_days(1)
    e.list_instrument(IPO)
    e.run_days(1)

    restored = _restored_copy(e, lambda t: t.list_instrument(IPO))
    e.run_days(1)
    restored.run_days(1)
    assert restored.prices() == e.prices()
    assert widths(restored) == widths(e)


# --------------------------------------------------------------------------
# The old checkpoints that cannot be restored
# --------------------------------------------------------------------------

def test_a_snapshot_at_a_stale_width_is_refused():
    """A checkpoint of a roster-changing run, written before this fix.

    It carries the array at the width the engine was constructed with. The
    states are positional, so padding or truncating would attach each one to
    whichever company now sits at that index, and the restored market would
    continue plausibly under states belonging to other names. The failure is
    kept and the message says which two numbers disagree.
    """
    e = engine(8)
    e.run_days(1)
    e.list_instrument(IPO)
    e.run_days(1)

    stale = e.state_snapshot()
    stale["volume_idio"] = stale["volume_idio"][: 8 * 8]  # the pre-fix width

    target = engine(8)
    target.list_instrument(IPO)
    with pytest.raises(tf.ValidationError) as caught:
        target.restore_state(stale)

    message = str(caught.value)
    assert "#148" in message, "the error must name the issue"
    # The bound phrases, not the bare digits. "8" in message is satisfied by
    # the "#148" whatever the widths are, and the two format arguments are
    # both integers and positional, so swapping them gives a message that
    # reads backwards and passes the looser form.
    assert "carries 8" in message, \
        "the error must name the width the snapshot carries"
    assert "holds 9" in message, \
        "the error must name the width the roster holds"
    assert widths(target) == (9, 9), \
        "the refusal leaves the array at the roster's width"


def test_a_refused_restore_leaves_the_engine_partly_written():
    """The width guard is the boundary of the restore, not a rollback.

    Everything `restore_state` writes BEFORE the guard holds the snapshot's
    value, and everything it writes AFTER holds the engine's own, because
    the error propagates out of the guard and the later writes are attempted
    and never reached. The rule is positional, so it stays true as writes
    are added on either side, and reading `restore_state` in order is what
    says which side a field is on.

    One witness per side, rather than a list that goes stale when a write
    moves. The price columns stand for the before side. The day counter
    stands for the after side, because it is the last write `restore_state`
    makes, so it moves under any reordering that pulls a write forward past
    the guard.

    Anyone adding a second witness on the after side should check that the
    two engines differ on it first. The central bank is also written after
    the guard and does not move at all over a run this short, so an
    assertion on it would pass here without testing anything.

    An engine that caught this error holds one run's market beside another
    run's macro state. Asserted rather than described, because the changelog
    and `set_volume_idio` both tell a reader to drop it, and a reader
    deserves to know what they are holding.
    """
    donor = engine(8)
    donor.run_days(1)
    donor.list_instrument(IPO)
    donor.run_days(5)
    stale = donor.state_snapshot()
    donor_snap = donor.state_snapshot()
    donor_rng = stream_state(donor_snap, "market")
    stale["volume_idio"] = stale["volume_idio"][: 8 * 8]

    target = engine(8)
    target.list_instrument(IPO)
    target.run_days(1)
    own = target.state_snapshot()
    own_prices = target.prices()
    own_states = states(target)
    assert own["day_count"] != donor_snap["day_count"], \
        "the two engines must differ on the day counter or the check is vacuous"

    with pytest.raises(tf.ValidationError):
        target.restore_state(stale)

    after = target.state_snapshot()

    # Before the guard, so taken from the snapshot.
    assert target.prices() == donor.prices(), \
        "the price columns are written before the guard"
    assert target.prices() != own_prices
    assert stream_state(after, "market") == donor_rng, \
        "the generator positions are written before the guard"

    # The guard itself, and everything after it.
    assert states(target) == own_states, \
        "the per-name volume array is the write that refused"
    assert after["day_count"] == own["day_count"], \
        "the day counter is written after the guard, so it stays"
    assert widths(target) == (9, 9)


def test_a_snapshot_at_the_matching_width_is_accepted():
    """The guard is about the width and not about the array being present."""
    e = engine(8)
    e.run_days(1)
    e.list_instrument(IPO)
    e.run_days(1)

    target = engine(8)
    target.list_instrument(IPO)
    target.restore_state(e.state_snapshot())
    assert states(target) == states(e)


# --------------------------------------------------------------------------
# What the change reaches
# --------------------------------------------------------------------------

def test_the_stream_position_is_a_function_of_the_draw_count():
    """The identity every claim below rests on.

    The volume-idio stream is derived from the root seed alone and is drawn
    once per slot per day, so two runs on one seed that have taken the same
    number of draws hold the same position, whatever roster or day count
    produced it. Twenty-four draws is eight names for three days, twelve for
    two, and twenty-four for one.
    """
    def position(n: int, days: int) -> tuple:
        e = engine(n)
        e.run_days(days)
        return stream_state(e.state_snapshot(), "volume_idio")

    assert position(8, 3) == position(12, 2) == position(24, 1)
    assert position(8, 3) != position(25, 1)


def test_a_roster_change_moves_the_volume_idio_stream_and_the_width():
    """The authorised change, stated against the pre-fix draw count.

    The pre-fix engine drew the CONSTRUCTION width every day whatever the
    roster did, so a fixed eight-name run of three days reached the position
    it reached on this run. The fixed engine draws the roster's width, which
    over eight names, then nine, then eight is twenty-five draws.
    """
    mutated = mutating_run().state_snapshot()

    pre_fix = engine(8)
    pre_fix.run_days(3)                       # 8 draws a day, 24 in all
    post_fix = engine(25)
    post_fix.run_days(1)                      # 8 + 9 + 8, in one day

    reached = stream_state(mutated, "volume_idio")
    assert reached != stream_state(pre_fix.state_snapshot(), "volume_idio"), \
        "the run took the construction width every day, as it did before"
    assert reached == stream_state(post_fix.state_snapshot(), "volume_idio"), \
        "the run did not take the roster's width every day"


def test_a_fixed_roster_run_takes_the_draws_it_always_took():
    """The other half. A run that lists and delists nothing cannot reach
    either changed function, and its per-day count was the roster's width
    before the fix as it is after."""
    e = engine(8)
    e.run_days(3)
    reference = engine(24)
    reference.run_days(1)
    assert stream_state(e.state_snapshot(), "volume_idio") == \
        stream_state(reference.state_snapshot(), "volume_idio")
    assert widths(e) == (8, 8)


def test_no_other_stream_moves_with_the_per_name_volume_state():
    """The six other streams are independent of this one by construction.

    Switching the per-name volume process on changes every state in the
    array and every volume the book quotes. It changes no other stream's
    POSITION, because each stream serves one domain and the market's own
    schedule is a function of the roster rather than of any drawn value. So
    a change to the volume-idio array reaches the volume-idio stream and
    stops there.
    """
    def run(model):
        e = tf.Engine(seed=SEED,
                      universe=tf.Universe.random(8, seed=UNIVERSE_SEED),
                      model=model)
        e.run_days(1)
        e.list_instrument(IPO)
        e.run_days(1)
        return e.state_snapshot()

    off = run("pt-v16")
    on = run(tf.ModelParams.from_preset("pt-v16",
                                        volume_idio_persistence=0.8,
                                        volume_idio_sigma=0.25))

    for name in STREAMS:
        if name == "volume_idio":
            continue
        assert stream_state(off, name) == stream_state(on, name), name
    assert off["columns"]["volume"] != on["columns"]["volume"], \
        "the arm with the process on must trade different volume"


def test_every_shipped_preset_holds_the_states_at_zero_across_a_roster_change():
    """Why no shipped price path moves.

    The update returns before writing at zero sigma and zero persistence, so
    every entry stays exactly 0.0 whatever the width is, and the tick's
    per-name multiplier is exactly 1.0 for a slot holding 0.0 and for a slot
    that is not there.
    """
    checked = 0
    for i in range(1, 100):
        try:
            model = tf.ModelParams.from_preset(f"pt-v{i}").to_dict()
        except tf.ValidationError:
            break
        assert model["volume_idio_sigma"] == 0.0, f"pt-v{i}"
        assert model["volume_idio_persistence"] == 0.0, f"pt-v{i}"
        checked += 1
    assert checked >= 16, "the preset list is shorter than the shipped one"

    e = mutating_run()
    assert widths(e) == (8, 8)
    assert states(e) == [0.0] * 8
