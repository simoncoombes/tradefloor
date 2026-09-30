"""What a snapshot, a restore, a state hash and a replay must carry.

The 0.8.5 audit found four pieces of engine state that moved prices or fill
stamps and that none of those four surfaces carried:

1. The day number. `run_days(first_day=N)` and `set_day(N)` were documented
   as labels, but the buyback factor read the same field as its elapsed time,
   so `first_day=1000` repriced every name on pt-v20 (0.17 in log price within
   thirty days) and the order log had no entry saying so. A replay of that
   log built the default run instead.
2. The day stamp and the session tick after a restore. A restore set the day
   to `day_count`, which is a day ahead of the original once a day has
   closed, and dropped the tick count. A pin after a boundary restore
   re-marked prices off the wrong elapsed time, and a fill after it was
   stamped day 4 tick 0 where the original said day 3 tick 390.
3. Fundamentals written by `set_fundamentals`. A restore brought back the
   construction earnings and the state hash could not tell the two apart, so
   the hash check a resume makes passed on a market 0.35 in log price away
   twenty days later.
4. The variance cascade (`garch_cascade_components` at 1 or more). Off on
   every shipped preset, and unsnapshotted on a custom model that turns it on.

Every test here fails on 5b56d0b and passes after the fix. Each runs a small
market for tens of days, so they are ordinary tests rather than slow ones.
"""

import struct

import pytest

import tradefloor as tf

UNIVERSE = tf.Universe.random(8, seed=111)


def _prices(engine):
    raw = engine.prices()
    return struct.unpack(f"<{len(raw) // 8}d", raw)


def _engine(model=None, universe=UNIVERSE, seed=7):
    kwargs = {} if model is None else {"model": model}
    return tf.Engine(seed=seed, universe=universe, **kwargs)


# --------------------------------------------------------------------------
# 1. The day number is a label
# --------------------------------------------------------------------------

def test_first_day_is_a_label_and_moves_no_price():
    """`first_day=1000` numbered the days and priced the market as though a
    thousand days had passed. It now prices exactly as the default does."""
    plain = _engine()
    plain.run_days(30, record=False)
    labelled = _engine()
    labelled.run_days(30, record=False, first_day=1000)
    assert _prices(labelled) == _prices(plain)
    # The label is still what the draw log and the day marks carry.
    assert [m["day"] for m in labelled.day_marks()][:2] == [1000, 1001]


def test_first_day_is_logged_and_a_replay_numbers_the_days_the_same_way():
    """The label goes into the order log, so a replay of a relabelled run
    rebuilds that run, hash for hash. Before, the log held no label and the
    replay was the default run."""
    labelled = _engine()
    labelled.run_days(5, record=False, first_day=1000)
    opens = [e for e in labelled.order_log if e["op"] == "open_market"]
    assert [e.get("day") for e in opens] == [1000, 1001, 1002, 1003, 1004]
    replayed = tf.replay(labelled.order_log, seed=7, universe=UNIVERSE)
    assert replayed.state_hash() == labelled.state_hash()
    assert replayed.day_marks() == labelled.day_marks()


def test_a_run_numbered_from_the_counter_logs_and_hashes_as_it_did():
    """The default run logs no label and snapshots no day key, so every log
    and every leaf written before the fix is the one it was."""
    engine = _engine()
    engine.run_days(3, record=False)
    assert all("day" not in e for e in engine.order_log
               if e["op"] == "open_market")
    snapshot = engine.state_snapshot()
    assert "current_day" not in snapshot and "elapsed_days" not in snapshot
    assert tf.manifest.state_hash(snapshot) == engine.state_hash()


def test_set_day_mid_session_moves_no_price_and_is_logged():
    """`set_day(5000)` mid-day moved the next session's prices by 0.21 in log
    with the state hash unchanged. Now it moves no price, is in the order
    log, and is in the state hash because the fills that follow carry it."""
    a, b = _engine(), _engine()
    for engine in (a, b):
        engine.run_days(10, record=False)
        engine.open_market()
    b.set_day(5000)
    assert b.order_log[-1] == {"op": "set_day", "day": 5000}
    assert a.state_hash() != b.state_hash()
    snapshot = b.state_snapshot()
    assert snapshot["current_day"] == 5000
    assert tf.manifest.state_hash(snapshot) == b.state_hash()
    for engine in (a, b):
        engine.run_session(9, 30, 3, 390)
        engine.close_market()
    assert _prices(a) == _prices(b)
    replayed = tf.replay(b.order_log, seed=7, universe=UNIVERSE)
    assert replayed.state_hash() == b.state_hash()


@pytest.mark.parametrize("call", [
    lambda e: e.set_day(-5),
    lambda e: e.open_market(day=-1),
])
def test_a_negative_day_is_refused(call):
    with pytest.raises(tf.ValidationError):
        call(_engine())


# --------------------------------------------------------------------------
# 2. The day stamp and the session tick survive a restore
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pin", [
    {"corporate_bond_yield": 0.08},
    {"cycle": "contraction"},
])
def test_restore_at_a_day_boundary_then_pin_matches_the_original(pin):
    """A pin re-marks prices off the valuation's clock. After a boundary
    restore that clock was a day ahead, so a pinned corporate yield put the
    restored market 5.7e-05 in log off the original, and it never came
    back."""
    original = _engine()
    original.run_days(40, record=False)
    restored = _engine()
    restored.restore_state(original.state_snapshot())
    assert restored.state_hash() == original.state_hash()
    for engine in (original, restored):
        engine.pin_macro(**pin)
    assert _prices(restored) == _prices(original)
    for engine in (original, restored):
        engine.run_days(20, record=False)
    assert restored.state_hash() == original.state_hash()


def test_restore_at_a_day_boundary_then_submit_stamps_the_fill_as_the_original():
    """A fill after a boundary restore was stamped day 4 tick 0 where the
    original stamped day 3 tick 390, and the hashes parted there."""
    universe = tf.Universe.random(3, seed=11)
    original = _engine(universe=universe, seed=5)
    original.run_days(4, record=False)
    restored = _engine(universe=universe, seed=5)
    restored.restore_state(original.state_snapshot())
    ticker = universe[0].ticker
    fill_a = original.submit("x", ticker, 1556.0)
    fill_r = restored.submit("x", ticker, 1556.0)
    assert fill_r == fill_a
    for engine in (original, restored):
        engine.run_days(10, record=False)
    assert restored.state_hash() == original.state_hash()


def test_restore_mid_session_then_submit_carries_the_tick():
    """Mid-session the day number was right and the tick was 0 instead of
    the ticks the day had run."""
    universe = tf.Universe.random(3, seed=11)
    original = _engine(universe=universe, seed=5)
    original.run_days(4, record=False)
    original.open_market()
    original.run_session(9, 30, 3, 50)
    snapshot = original.state_snapshot()
    assert snapshot["session_tick"] == 50
    restored = _engine(universe=universe, seed=5)
    restored.restore_state(snapshot)
    ticker = universe[0].ticker
    assert restored.submit("x", ticker, 1556.0) == original.submit(
        "x", ticker, 1556.0)
    for engine in (original, restored):
        engine.run_session(9, 30, 3, 340)
        engine.close_market()
    assert restored.state_hash() == original.state_hash()


# --------------------------------------------------------------------------
# 3. Fundamentals written by set_fundamentals
# --------------------------------------------------------------------------

def test_set_fundamentals_is_in_the_hash_the_snapshot_and_the_restore():
    untouched = _engine(seed=5)
    moved = _engine(seed=5)
    for engine in (untouched, moved):
        engine.run_days(10, record=False)
    eps, book, growth = (list(c) for c in moved.fundamentals())
    moved.set_fundamentals([x * 2 for x in eps], book, growth)
    # Two engines alike in every column and valuing different earnings are
    # not the same state.
    assert moved.state_hash() != untouched.state_hash()
    snapshot = moved.state_snapshot()
    assert "fundamentals" in snapshot
    assert tf.manifest.state_hash(snapshot) == moved.state_hash()
    restored = _engine(seed=5)
    restored.restore_state(snapshot)
    assert list(restored.fundamentals()[0]) == [x * 2 for x in eps]
    assert restored.state_hash() == moved.state_hash()
    for engine in (moved, restored):
        engine.run_days(20, record=False)
    assert _prices(restored) == _prices(moved)


def test_a_snapshot_without_fundamentals_restores_the_construction_figures():
    """Absent means the snapshot's engine had not moved them, so a restore
    onto an engine that had puts the construction figures back."""
    clean = _engine(seed=5)
    clean.run_days(3, record=False)
    snapshot = clean.state_snapshot()
    assert "fundamentals" not in snapshot
    target = _engine(seed=5)
    eps, book, growth = (list(c) for c in target.fundamentals())
    target.set_fundamentals([x * 3 for x in eps], book, growth)
    target.restore_state(snapshot)
    assert target.fundamentals() == clean.fundamentals()
    assert target.state_hash() == clean.state_hash()


def test_an_earnings_impulse_survives_a_resume():
    """The public route to the same fault: a one-off earnings shock, then a
    resume by snapshot at day 30, as the hosted server resumes. The hash
    check passed and the restored market valued on the pre-shock
    earnings."""
    universe = tf.Universe.random(8, seed=111)
    shock = dict(operation="multiply", value=0.6, at=10, shape="impulse")
    original = tf.Engine(seed=301, universe=universe)
    scenario = tf.Scenario().shock("market.earnings", **shock)
    for day in range(30):
        scenario.apply(original, day)
        original.open_market()
        original.run_session(9, 30, 3, 65)
        original.close_market()
    restored = tf.Engine(seed=301, universe=universe)
    restored.restore_state(original.state_snapshot())
    assert restored.fundamentals() == original.fundamentals()
    again = tf.Scenario().shock("market.earnings", **shock)
    for day in range(30, 40):
        for engine, sc in ((original, scenario), (restored, again)):
            sc.apply(engine, day)
            engine.open_market()
            engine.run_session(9, 30, 3, 65)
            engine.close_market()
    assert restored.state_hash() == original.state_hash()


# --------------------------------------------------------------------------
# 4. The variance cascade on a model that runs it
# --------------------------------------------------------------------------

def test_the_variance_cascade_is_snapshotted_and_hashed():
    model = tf.ModelParams.from_preset("pt-v20", garch_cascade_components=3.0)
    universe = tf.Universe.random(6, seed=111)
    original = tf.Engine(seed=4, universe=universe, model=model)
    original.run_days(30, record=False)
    snapshot = original.state_snapshot()
    assert "garch_cascade" in snapshot
    assert tf.manifest.state_hash(snapshot) == original.state_hash()
    restored = tf.Engine(seed=4, universe=universe, model=model)
    restored.restore_state(snapshot)
    for engine in (original, restored):
        engine.run_days(20, record=False)
    assert restored.state_hash() == original.state_hash()
    assert _prices(restored) == _prices(original)


def test_a_model_without_the_cascade_carries_none():
    engine = _engine()
    engine.run_days(2, record=False)
    assert "garch_cascade" not in engine.state_snapshot()


# --------------------------------------------------------------------------
# What the four gaps did to a ledger
# --------------------------------------------------------------------------

@pytest.mark.parametrize("label, write", [
    ("set_fundamentals", lambda e: e.set_fundamentals(
        [x * 0.5 for x in e.fundamentals()[0]], *e.fundamentals()[1:])),
    ("pin corporate yield", lambda e: e.pin_macro(corporate_bond_yield=0.08)),
    ("pin cycle", lambda e: e.pin_macro(cycle="contraction")),
])
def test_verify_passes_a_run_that_wrote_to_the_market_between_days(label, write):
    """`tf.manifest.verify` restores the day before and re-runs the day. It
    marked honest days FAILED after each of these writes: the restore lost
    the fundamentals, and set the day one ahead, so a pin re-marked prices
    off the wrong elapsed time."""
    universe = tf.Universe.random(8, seed=111)
    engine = tf.Engine(seed=42, universe=universe)
    ledger = tf.DayLedger()
    days = 12
    for day in range(days):
        if day == 5:
            write(engine)
        engine.run_days(1, record=False, ledger=ledger)
    manifest = tf.RunManifest.of(engine, seed=42, universe=universe,
                                 ledger=ledger)
    report = tf.manifest.verify(manifest, ledger, days, seed=1)
    failed = [line for line in report.describe().splitlines()
              if "FAILED day" in line]
    assert failed == [], f"{label}: {failed}"
