"""The state snapshot's versioned contract.

`Engine.state_snapshot()` writes a dict and `Engine.restore_state` reads it
back. Until this contract, the restore read every field only when the dict
carried it, and kept whatever the engine held for a field it did not. So a
snapshot missing `economy.oil_last_opec_day` restored without a word into an
engine that differed from the original, while `manifest.state_hash` refused
the same dict by name (issue #184).

The contract:

- Every snapshot carries ``state_schema``, the layout version
  (`Engine.STATE_SCHEMA`, 1).
- At the current version every key is required, and a missing or unknown
  key is refused by name, at the top level, in the economy, in the central
  bank and in the columns. Keys a snapshot carries only under a model dial
  are required exactly when this engine's model sets that dial.
- A snapshot with no ``state_schema`` is accepted when it carries every key
  of version 1, which is what tradefloor 0.8.5 to 0.8.8 wrote. A snapshot
  written before 0.8.5 is refused: it carries no session tick and its day
  label and valuation clock were one field it did not carry, so a restore
  would have to guess them.
- A newer or unknown version is refused, and so is a malformed value: a
  wrong type, a non-finite scalar, a buffer of the wrong length.
- A refused restore changes nothing.

The fixtures in ``tests/fixtures/snapshots`` were written by the released
wheels of 0.8.1, 0.8.5 and 0.8.8 (``write_fixture.py`` there), so the
legacy cases are what those builds actually wrote.
"""

import hashlib
import json
import math
import pathlib
import subprocess
import sys
import textwrap

import pytest

import tradefloor as tf
from tradefloor.manifest import state_hash

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import snapshot_codec  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "snapshots"
UNIVERSE = tf.Universe.random(6, seed=5)
TICKS = 78


def _engine(preset="pt-v20", universe=UNIVERSE, seed=3):
    return tf.Engine(seed=seed, universe=universe,
                     model=tf.ModelParams.from_preset(preset))


def _run(engine, days, first=0):
    for day in range(first, first + days):
        engine.open_market()
        engine.run_session(9, 30, day % 5, TICKS)
        engine.close_market()


def _mid_day(preset="pt-v20"):
    engine = _engine(preset)
    _run(engine, 3)
    engine.open_market()
    engine.run_session(9, 30, 3, 40)
    return engine


def _closed(preset="pt-v20"):
    engine = _engine(preset)
    _run(engine, 3)
    return engine


def _copy(snapshot):
    return snapshot_codec.loads(snapshot_codec.dumps(snapshot))


# --------------------------------------------------------------------------
# The current version
# --------------------------------------------------------------------------

def test_a_snapshot_carries_its_schema_version():
    snapshot = _closed().state_snapshot()
    assert tf.Engine.STATE_SCHEMA == 1
    assert snapshot["state_schema"] == tf.Engine.STATE_SCHEMA


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v3"])
@pytest.mark.parametrize("make", [_closed, _mid_day], ids=["closed", "mid-day"])
def test_a_round_trip_keeps_the_state_hash(preset, make):
    original = make(preset)
    snapshot = original.state_snapshot()
    restored = _engine(preset)
    restored.restore_state(snapshot)
    assert restored.state_hash() == original.state_hash() == state_hash(snapshot)
    assert snapshot_codec.dumps(restored.state_snapshot()) == snapshot_codec.dumps(snapshot)


def test_the_version_key_moves_no_state_hash():
    """The version is outside the hash, so every leaf written before it was
    carried still checks."""
    snapshot = _closed().state_snapshot()
    without = dict(snapshot)
    del without["state_schema"]
    assert state_hash(without) == state_hash(snapshot)


# --------------------------------------------------------------------------
# Missing and unknown keys
# --------------------------------------------------------------------------

@pytest.mark.parametrize("field", [
    "oil_last_opec_day", "phase_gdp_target", "vix", "gdp_trend", "cycle_phase",
])
def test_a_snapshot_missing_an_economy_field_is_refused_by_name(field):
    snapshot = _closed().state_snapshot()
    del snapshot["economy"][field]
    target = _engine()
    with pytest.raises(tf.ValidationError, match=field):
        target.restore_state(snapshot)


def test_a_snapshot_missing_a_central_bank_field_is_refused_by_name():
    snapshot = _closed().state_snapshot()
    del snapshot["central_bank"]["hawkish_dovish_score"]
    with pytest.raises(tf.ValidationError, match="hawkish_dovish_score"):
        _engine().restore_state(snapshot)


def _top_level_keys():
    # Without the version, which a snapshot written before it was recorded
    # also lacks: that case is the legacy rule's, below. And without the
    # derived VIX anchor, which a snapshot written before #268 lacks: that
    # case is the next test's.
    return sorted(set(_mid_day().state_snapshot()) - {"state_schema", "vix_anchor"})


@pytest.mark.parametrize("key", _top_level_keys())
def test_a_snapshot_missing_any_top_level_key_is_refused_by_name(key):
    snapshot = _mid_day().state_snapshot()
    del snapshot[key]
    with pytest.raises(tf.ValidationError, match=key):
        _engine().restore_state(snapshot)


def test_a_snapshot_without_the_derived_vix_anchor_restores_with_the_engines_own():
    """A snapshot from before #268 carries no anchor; the restore keeps the
    one the engine derived, which is what every restore read until then."""
    snapshot = _mid_day().state_snapshot()
    assert "vix_anchor" in snapshot
    del snapshot["vix_anchor"]
    _engine().restore_state(snapshot)


@pytest.mark.parametrize("where", ["top", "economy", "central_bank", "columns"])
def test_an_unknown_key_is_refused_by_name(where):
    snapshot = _closed().state_snapshot()
    block = snapshot if where == "top" else snapshot[where]
    block["something_new"] = b"" if where == "columns" else 1.0
    with pytest.raises(tf.ValidationError, match="something_new"):
        _engine().restore_state(snapshot)


def test_a_dial_gated_key_follows_the_model():
    """`fair_value_offset` is carried exactly when the model can move a
    level. pt-v20 can, so a pt-v20 snapshot without it is refused; pt-v3
    cannot, so a pt-v3 snapshot with `vix_anchor_slow` is refused."""
    snapshot = _closed("pt-v20").state_snapshot()
    del snapshot["fair_value_offset"]
    del snapshot["opening_z"]
    with pytest.raises(tf.ValidationError, match="fair_value_offset"):
        _engine("pt-v20").restore_state(snapshot)
    snapshot = _closed("pt-v3").state_snapshot()
    snapshot["vix_anchor_slow"] = 0.0
    with pytest.raises(tf.ValidationError, match="vix_anchor_memory"):
        _engine("pt-v3").restore_state(snapshot)


# --------------------------------------------------------------------------
# Versions
# --------------------------------------------------------------------------

@pytest.mark.parametrize("version, words", [
    (2, "newer"),
    (99, "newer"),
    (0, "version"),
    (-1, "version"),
    ("1", "integer"),
    (1.0, "integer"),
    (True, "integer"),
])
def test_a_newer_or_unknown_version_is_refused(version, words):
    snapshot = _closed().state_snapshot()
    snapshot["state_schema"] = version
    with pytest.raises(tf.ValidationError, match=words):
        _engine().restore_state(snapshot)


def _fixture(name):
    doc = json.loads((FIXTURES / name).read_text())
    universe = tf.Universe.from_json(json.dumps(doc["universe"]))
    engine = tf.Engine(seed=doc["seed"], universe=universe,
                       model=tf.ModelParams.from_preset(doc["preset"]))
    return doc, engine, snapshot_codec.decode(doc["snapshot"])


def _continue_fixture(doc, engine):
    if doc["stop"] == "midday":
        engine.run_session(12, 50, 3, TICKS - 40)
        engine.close_market()
    engine.open_market()
    engine.run_session(9, 30, 4, TICKS)
    engine.close_market()


@pytest.mark.parametrize("name", sorted(
    p.name for p in FIXTURES.glob("v0.8.[58]-*.json")))
def test_an_unversioned_snapshot_from_0_8_5_to_0_8_8_restores_and_continues(name):
    """0.8.5 to 0.8.8 wrote every key of version 1 and no version. Such a
    snapshot restores to the state its own hash describes, and the run
    continues to the state the writing release itself reached."""
    doc, engine, snapshot = _fixture(name)
    assert "state_schema" not in snapshot
    engine.restore_state(snapshot)
    assert engine.state_hash() == state_hash(snapshot)
    _continue_fixture(doc, engine)
    assert engine.state_hash() == doc["continued"]["state_hash"]
    assert (hashlib.sha256(engine.column("price")).hexdigest()
            == doc["continued"]["price_sha256"])


def test_a_snapshot_written_before_0_8_5_is_refused_with_the_reason():
    doc, engine, snapshot = _fixture("v0.8.1-pt-v19-close.json")
    before = engine.state_hash()
    with pytest.raises(tf.ValidationError, match="session_tick") as raised:
        engine.restore_state(snapshot)
    assert "0.8.5" in str(raised.value)
    assert engine.state_hash() == before


def test_a_caller_who_knows_a_missing_field_can_supply_it():
    """A build before 0.8.5 was tagged wrote no `session_tick`. The restore
    will not guess it, and says that a caller who knows it can write it in;
    the completed dict restores."""
    doc, engine, snapshot = _fixture("v0.8.8-pt-v20-midday.json")
    held = snapshot.pop("session_tick")
    with pytest.raises(tf.ValidationError, match="write it into the dict"):
        engine.restore_state(snapshot)
    snapshot["session_tick"] = held
    engine.restore_state(snapshot)
    assert engine.state_hash() == state_hash(snapshot)


# --------------------------------------------------------------------------
# Malformed values
# --------------------------------------------------------------------------

def _edit(path, value):
    def apply(snapshot):
        block = snapshot
        for key in path[:-1]:
            block = block[key]
        block[path[-1]] = value(block[path[-1]]) if callable(value) else value
    return apply


@pytest.mark.parametrize("path, value, words", [
    (("economy", "gdp"), "high", "economy.gdp"),
    (("economy", "vix"), math.nan, "economy.vix"),
    (("economy", "oil_price"), math.inf, "economy.oil_price"),
    (("economy", "oil_last_opec_day"), 1.5, "economy.oil_last_opec_day"),
    (("economy", "gdp_trend"), [1.0, 2.0, 3.0], "gdp_trend"),
    (("economy", "cycle_phase"), "boom", "cycle phase"),
    (("central_bank", "target_inflation"), math.nan, "central_bank.target_inflation"),
    (("central_bank", "forward_guidance"), "loud", "forward guidance"),
    (("volume_state",), math.nan, "volume_state"),
    (("nominal_output_base",), "x", "nominal_output_base"),
    (("market_open",), "yes", "market_open"),
    (("day_count",), -1, "day_count"),
    (("rng",), lambda v: v[:27], "rng"),
    (("draw_counts",), lambda v: v[:18], "draw_counts"),
    (("draw_counts",), lambda v: [-1.0] + list(v[1:]), "draw_counts"),
    (("draw_overlay",), [(99, 0, 1, 0.5)], "draw_overlay"),
    (("market_variance",), lambda v: v[:4], "market_variance"),
    (("attribution",), lambda v: v[:-8], "attribution"),
    (("tick_components",), lambda v: v[:-8], "tick_components"),
    (("noise_parts",), lambda v: v + b"\0", "noise_parts"),
    (("volume_idio",), [1.0, 2.0], "volume_idio"),
    (("columns", "price"), lambda v: v[:-8], "price"),
    (("session_news",), [{"ticker": None}], "session_news"),
    (("crisis_epicentre_pin",), "none", "crisis_epicentre_pin"),
])
def test_a_malformed_value_is_refused_and_changes_nothing(path, value, words):
    snapshot = _mid_day().state_snapshot()
    _edit(path, value)(snapshot)
    target = _closed()
    before = target.state_hash()
    with pytest.raises(tf.ValidationError, match=words):
        target.restore_state(snapshot)
    assert target.state_hash() == before


def test_a_snapshot_onto_another_roster_size_is_refused():
    snapshot = _closed().state_snapshot()
    target = _engine(universe=tf.Universe.random(5, seed=5))
    with pytest.raises(tf.ValidationError, match="roster"):
        target.restore_state(snapshot)


def test_a_refused_restore_late_in_the_dict_changes_nothing():
    """The central bank is read after the columns. A bad value there used to
    raise with the columns, the generators and the economy already
    overwritten, leaving an engine that was neither."""
    snapshot = _mid_day().state_snapshot()
    snapshot["central_bank"]["forward_guidance"] = "loud"
    target = _closed()
    before = target.state_snapshot()
    with pytest.raises(tf.ValidationError):
        target.restore_state(snapshot)
    assert snapshot_codec.dumps(target.state_snapshot()) == snapshot_codec.dumps(before)


# --------------------------------------------------------------------------
# A resumed run across a process boundary
# --------------------------------------------------------------------------

_RESUME = textwrap.dedent("""
    import json, sys
    sys.path.insert(0, {tests!r})
    import tradefloor as tf
    import snapshot_codec
    doc = json.loads(open({path!r}).read())
    engine = tf.Engine(seed=doc["seed"],
                       universe=tf.Universe.from_json(doc["universe"]),
                       model=tf.ModelParams.from_preset(doc["preset"]))
    engine.restore_state(snapshot_codec.loads(doc["snapshot"]))
    out = []
    if doc["finish"]:
        engine.run_session(*doc["finish"])
        engine.close_market()
        out.append(snapshot_codec.dumps(engine.state_snapshot()))
    for day in range(doc["more"]):
        engine.open_market()
        if doc.get("pins"):
            engine.pin_macro(**doc["pins"][day])
        engine.run_session(9, 30, (4 + day) % 5, {ticks})
        engine.close_market()
        out.append(snapshot_codec.dumps(engine.state_snapshot()))
    print(json.dumps(out))
""")


#: The rest of a day stopped after forty ticks.
_FINISH = [12, 50, 3, TICKS - 40]


def _states_after(engine, finish, more, pins=None):
    out = []
    if finish:
        engine.run_session(*finish)
        engine.close_market()
        out.append(snapshot_codec.dumps(engine.state_snapshot()))
    for day in range(more):
        engine.open_market()
        if pins:
            engine.pin_macro(**pins[day])
        engine.run_session(9, 30, (4 + day) % 5, TICKS)
        engine.close_market()
        out.append(snapshot_codec.dumps(engine.state_snapshot()))
    return out


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v3"])
@pytest.mark.parametrize("mid_day", [False, True], ids=["closed", "mid-day"])
def test_a_resumed_run_in_a_fresh_process_matches_an_uninterrupted_one(
        preset, mid_day, tmp_path):
    """Run, snapshot, write JSON, restore in a new interpreter, run on, and
    compare the whole state after every close with a run that never
    stopped: every column, the economy, the central bank, the generators."""
    more = 3
    control = _mid_day(preset) if mid_day else _closed(preset)
    paused = _mid_day(preset) if mid_day else _closed(preset)
    doc = {"seed": 3, "preset": preset, "universe": UNIVERSE.to_json(),
           "finish": _FINISH if mid_day else None, "more": more,
           "snapshot": snapshot_codec.dumps(paused.state_snapshot())}
    path = tmp_path / "paused.json"
    path.write_text(json.dumps(doc))
    script = _RESUME.format(tests=str(pathlib.Path(__file__).resolve().parent),
                            path=str(path), ticks=TICKS)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, check=True)
    resumed = json.loads(done.stdout)
    expected = _states_after(control, _FINISH if mid_day else None, more)
    assert len(resumed) == len(expected) == more + int(mid_day)
    assert resumed == expected


#: A scenario in flight: a crisis pinned mid-day, with a forced close
#: pending, then a path of pins the caller goes on driving. The snapshot
#: carries what the pins left in the engine (today's pins, the pending
#: close, the epicentre pin); the path itself is the caller's, as it is for
#: `run_scenario`, so both runs apply the same remaining days.
_PINS = [{"vix": 38.0, "corporate_bond_yield": 0.075},
         {"vix": 31.0},
         {"cycle": "contraction"}]


@pytest.mark.parametrize("preset", ["pt-v20", "pt-v3"])
def test_a_scenario_in_flight_resumes_in_a_fresh_process(preset, tmp_path):
    def pinned():
        engine = _mid_day(preset)
        engine.pin_macro(vix=45.0, corporate_bond_yield=0.08,
                         epicentre="energy", vix_sets_variance=True)
        engine.run_session(12, 50, 3, 4)
        return engine

    control, paused = pinned(), pinned()
    snapshot = paused.state_snapshot()
    assert snapshot["vix_sets_variance_pending"]
    assert snapshot["crisis_epicentre_pin"] != -2
    doc = {"seed": 3, "preset": preset, "universe": UNIVERSE.to_json(),
           "finish": [13, 10, 3, TICKS - 44], "more": 3, "pins": _PINS,
           "snapshot": snapshot_codec.dumps(snapshot)}
    path = tmp_path / "paused.json"
    path.write_text(json.dumps(doc))
    script = _RESUME.format(tests=str(pathlib.Path(__file__).resolve().parent),
                            path=str(path), ticks=TICKS)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, check=True)
    expected = _states_after(control, [13, 10, 3, TICKS - 44], 3, _PINS)
    assert json.loads(done.stdout) == expected


def test_a_checkpoint_resumed_in_a_fresh_process_matches_an_uninterrupted_one(tmp_path):
    """A `Checkpoint` is the order log, and resuming one replays it. The
    resumed run reaches the parent's state hash and runs on identically."""
    control, paused = _closed(), _closed()
    mark = tf.Checkpoint.of(paused, universe=UNIVERSE, seed=3)
    path = tmp_path / "mark.json"
    path.write_text(mark.to_json())
    script = textwrap.dedent(f"""
        import json, sys
        sys.path.insert(0, {str(pathlib.Path(__file__).resolve().parent)!r})
        import tradefloor as tf
        import snapshot_codec
        engine = tf.Checkpoint.from_json(open({str(path)!r}).read()).resume()
        out = [engine.state_hash()]
        for day in range(3):
            engine.open_market()
            engine.run_session(9, 30, (4 + day) % 5, {TICKS})
            engine.close_market()
            out.append(snapshot_codec.dumps(engine.state_snapshot()))
        print(json.dumps(out))
    """)
    done = subprocess.run([sys.executable, "-c", script], capture_output=True,
                          text=True, check=True)
    resumed = json.loads(done.stdout)
    assert resumed[0] == control.state_hash()
    assert resumed[1:] == _states_after(control, None, 3)


def test_a_branch_matches_an_uninterrupted_run():
    control, parent = _mid_day(), _mid_day()
    (fork,) = tf.branch(parent, 1)
    assert _states_after(fork, _FINISH, 2) == _states_after(control, _FINISH, 2)


# --------------------------------------------------------------------------
# Every dial-gated key at once
# --------------------------------------------------------------------------

#: A model that turns on every dial a snapshot key is gated on.
EVERY_DIAL = dict(
    garch_cascade_components=3.0,
    cycle_publication_lag=5.0,
    gdp_publication_lag=21.0,
    unemployment_adjustment_half_life=84.0,
    earnings_cycle_depth=0.05,
    fair_value_vix_discount=0.01,
    fair_value_vix_half_life=10.0,
    qe_pe_stock_gain=2.0,
)


def _every_dial():
    model = tf.ModelParams.from_preset("pt-v20", **EVERY_DIAL)
    universe = tf.Universe(list(UNIVERSE)).with_bonds()
    engine = tf.Engine(seed=3, universe=universe, model=model)
    eps, book, growth = engine.fundamentals()
    engine.set_fundamentals([e * 1.1 for e in eps], book, growth)
    # The host's input to the VIX target, fading through the run.
    engine.set_vix_target_premium(3.0, 13.0)
    engine.set_vix_target_floor(18.0)
    _run(engine, 2)
    engine.open_market()
    engine.submit("fund", engine.tickers[0], 100.0, limit_price=1.0)
    engine.pin_macro(qe_assets_ratio=1.6)
    engine.run_session(9, 30, 2, 40)
    return model, universe, engine


def test_a_model_with_every_gated_key_round_trips():
    model, universe, original = _every_dial()
    snapshot = original.state_snapshot()
    for key in ("vix_anchor_slow", "fair_value_offset", "opening_z",
                "garch_cascade", "rates", "book", "fundamentals",
                "vix_target_premium", "vix_target_floor"):
        assert key in snapshot, key
    for key in ("earnings_cycle", "vix_feedback", "qe_assets_ratio",
                "cycle_history", "unemployment_impulse", "gdp_publication"):
        assert key in snapshot["economy"], key
    restored = tf.Engine(seed=9, universe=universe, model=model)
    restored.restore_state(_copy(snapshot))
    assert restored.state_hash() == original.state_hash() == state_hash(snapshot)
    assert snapshot_codec.dumps(restored.state_snapshot()) == snapshot_codec.dumps(snapshot)


@pytest.mark.parametrize("where, key, dial", [
    ("top", "garch_cascade", "garch_cascade_components"),
    ("top", "rates", "carries none"),
    ("economy", "earnings_cycle", "earnings_cycle_depth"),
    ("economy", "vix_feedback", "fair_value_vix_half_life"),
    ("economy", "qe_assets_ratio", "qe_pe_stock_gain"),
    ("economy", "cycle_history", "cycle_publication_lag"),
    ("economy", "unemployment_impulse", "unemployment_adjustment_half_life"),
    ("economy", "gdp_publication", "gdp_publication_lag"),
])
def test_a_gated_key_the_model_needs_is_refused_with_its_dial(where, key, dial):
    model, universe, original = _every_dial()
    snapshot = original.state_snapshot()
    del (snapshot if where == "top" else snapshot["economy"])[key]
    target = tf.Engine(seed=3, universe=universe, model=model)
    with pytest.raises(tf.ValidationError, match=key) as raised:
        target.restore_state(snapshot)
    assert dial in str(raised.value)


def test_the_qe_asset_stock_survives_a_restore():
    """`qe_assets_ratio` moves under QE and reaches the fair value through
    `qe_pe_stock_gain`. The snapshot did not carry it, so a restored engine
    kept the 1.0 it was built with and priced every name differently from
    the original from its next tick, with the state hash unchanged."""
    model = tf.ModelParams.from_preset("pt-v20", qe_pe_stock_gain=2.0)
    original = tf.Engine(seed=3, universe=UNIVERSE, model=model)
    _run(original, 2)
    original.pin_macro(qe_assets_ratio=1.8)
    snapshot = original.state_snapshot()
    restored = tf.Engine(seed=3, universe=UNIVERSE, model=model)
    restored.restore_state(snapshot)
    for engine in (original, restored):
        _run(engine, 2, first=2)
    assert restored.column("price") == original.column("price")


# --------------------------------------------------------------------------
# The day ledger
# --------------------------------------------------------------------------

def test_a_ledger_of_snapshots_with_every_buffer_round_trips_through_json():
    """`DayLedger.to_json` encoded a fixed list of byte buffers and missed
    `garch_cascade` and the three `fundamentals` columns, so a ledger of a
    run that carried either raised `TypeError` on the way out."""
    model = tf.ModelParams.from_preset("pt-v20", garch_cascade_components=3.0)
    engine = tf.Engine(seed=3, universe=UNIVERSE, model=model)
    eps, book, growth = engine.fundamentals()
    engine.set_fundamentals([e * 1.1 for e in eps], book, growth)
    ledger = tf.manifest.DayLedger()
    for day in range(3):
        engine.open_market()
        engine.run_session(9, 30, day % 5, TICKS)
        engine.close_market()
        ledger.close(engine)
    text = ledger.to_json()
    assert "state_schema" not in text
    loaded = tf.manifest.DayLedger.from_json(text)
    assert [state_hash(s) for s in loaded.snapshots] == loaded.leaves == ledger.leaves
    restored = tf.Engine(seed=3, universe=UNIVERSE, model=model)
    restored.restore_state(loaded.snapshots[-1])
    assert restored.state_hash() == engine.state_hash()


# --------------------------------------------------------------------------
# A counterfactual world's checkpoint
# --------------------------------------------------------------------------

def test_a_world_checkpoint_carries_the_build_and_its_era():
    """`World.checkpoint` built its `Checkpoint` without the version and the
    era digest, so `resume` skipped the era check and a run under the
    default preset would resume under whatever default a later build had."""
    from tradefloor.counterfactual import World

    class Idle:
        def act(self, obs):
            return {}

    world = World(seed=3, universe=list(UNIVERSE), agent=Idle(),
                  steps_per_day=2, ticks_per_step=20)
    world.run(days=1)
    mark = world.checkpoint()
    assert mark.written_by == tf.__version__
    assert mark.era == tf.manifest.era_fingerprint()
    payload = json.loads(mark.to_json())
    payload["era"] = "0" * 64
    with pytest.raises(tf.ValidationError, match="era digest"):
        tf.Checkpoint.from_json(json.dumps(payload)).resume()
