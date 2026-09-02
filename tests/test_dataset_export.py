"""The dataset exporter: ``tools/dataset/export.py``.

Loaded by file path, the way ``tests/test_atlas_verdict.py`` loads a
``tools/calibration`` script, because nothing under ``tools/`` is a
package. The module is registered in ``sys.modules`` before it executes:
without that, ``dataclasses`` on Python 3.13 raises inside
``@dataclass(frozen=True)`` on :class:`export.Written`, because its
internal ``KW_ONLY`` check looks the defining module up by name in
``sys.modules`` and finds nothing there for a module that was never
registered.

Every run in this file uses a small roster and a short horizon, so the
whole file finishes in a few seconds rather than minutes: one canonical
toy run (``Universe.random(8, seed=99)``, seed 42, twenty days, no
scenario, preset ``pt-v16``) is built once per test session and reused by
every test that only needs to read its output.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pa = pytest.importorskip("pyarrow", reason="pyarrow is a test-only dependency")

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "dataset" / "export.py"

_SPEC = importlib.util.spec_from_file_location("dataset_export", SCRIPT)
export = importlib.util.module_from_spec(_SPEC)
sys.modules["dataset_export"] = export
_SPEC.loader.exec_module(export)

import tradefloor as tf  # noqa: E402


# --------------------------------------------------------------------------
# The canonical toy run, built once
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def toy_written(tmp_path_factory) -> "export.Written":
    """One export, on the roster and horizon this module's docstring
    quotes measured numbers for."""
    out = tmp_path_factory.mktemp("p10-toy")
    universe = tf.Universe.random(8, seed=99)
    return export.export(42, universe=universe, days=20, scenario=None,
                         model="pt-v16", out=out)


@pytest.fixture(scope="module")
def toy_tables(toy_written) -> dict:
    return {name: pa.ipc.open_file(path).read_all()
           for name, path in toy_written.files.items()
           if path.suffix == ".arrow"}


# --------------------------------------------------------------------------
# export() writes the seven files the design note names
# --------------------------------------------------------------------------

def test_export_writes_the_seven_named_files(toy_written):
    assert set(toy_written.files) == {
        "bars", "truth", "prints", "macro", "labels", "manifest", "ledger",
    }
    for path in toy_written.files.values():
        assert path.exists()
        assert path.is_file()


def test_files_live_one_directory_per_seed(toy_written):
    # "One directory per seed with fixed file names" (the design note's
    # decision), so a reader can join on (day, instrument_id) without a
    # catalogue: every file for seed 42 sits beside the others under a
    # directory named "42".
    parents = {path.parent for path in toy_written.files.values()}
    assert parents == {toy_written.files["bars"].parent}
    assert parents.pop().name == "42"


def test_export_refuses_zero_days(tmp_path):
    universe = tf.Universe.random(3, seed=1)
    with pytest.raises(tf.ValidationError, match="days"):
        export.export(1, universe=universe, days=0, scenario=None,
                      model="pt-v16", out=tmp_path)


# --------------------------------------------------------------------------
# Reproduction: bars, truth, prints and macro re-derive from the manifest
# --------------------------------------------------------------------------

def test_bars_truth_prints_macro_reproduce_from_the_manifest(
    toy_written, toy_tables,
):
    """"every file re-derives from its manifest: reproduce() passes and
    the re-exported tables are byte-identical" (the design note's Tests
    section), checked by content equality rather than by comparing the
    written FILES' raw bytes.

    ``prints.arrow``'s raw bytes are not a safe comparison across two
    writes of the identical table: its schema carries metadata built from
    a Rust ``std::collections::HashMap``
    (``rust/src/python_arrow.rs::prints_schema``), whose iteration order
    -- and therefore the byte order the Arrow IPC footer serialises the
    metadata in -- is randomised per process. Two writes of the SAME
    table can therefore differ byte for byte while
    ``pyarrow.Table.equals`` reports them identical, which is what this
    test asserts instead. ``bars``, ``truth`` and ``macro`` carry no such
    metadata and this project's own writers add none, so those three are
    also checked directly against the files on disk.
    """
    manifest_text = toy_written.files["manifest"].read_text(encoding="utf-8")
    manifest = tf.RunManifest.from_json(manifest_text)
    rebuilt = manifest.reproduce()

    for name, call in (("bars", lambda e: e.bars(grain="day")),
                       ("truth", lambda e: e.truth()),
                       ("prints", lambda e: e.prints()),
                       ("macro", lambda e: e.macro_table())):
        again = export._read_table(call(rebuilt))
        assert again.equals(toy_tables[name]), (
            f"{name}.arrow did not reproduce from the manifest")


def test_running_export_twice_is_fully_deterministic(tmp_path):
    universe = tf.Universe.random(5, seed=7)
    out1, out2 = tmp_path / "a", tmp_path / "b"
    w1 = export.export(9, universe=universe, days=6, scenario=None,
                       model="pt-v16", out=out1)
    w2 = export.export(9, universe=universe, days=6, scenario=None,
                       model="pt-v16", out=out2)
    for name in ("bars", "truth", "macro", "labels", "manifest", "ledger"):
        assert w1.files[name].read_bytes() == w2.files[name].read_bytes(), (
            f"{name} was not byte-identical across two runs of the same "
            "seed, universe, days and model")
    # prints.arrow: content-identical, not necessarily byte-identical.
    # See test_bars_truth_prints_macro_reproduce_from_the_manifest.
    t1 = pa.ipc.open_file(w1.files["prints"]).read_all()
    t2 = pa.ipc.open_file(w2.files["prints"]).read_all()
    assert t1.equals(t2)
    assert w1.card["market_digest"] == w2.card["market_digest"]
    assert w1.card["day_ledger_root"] == w2.card["day_ledger_root"]


def test_written_files_carry_no_carriage_return(toy_written, tmp_path):
    """The defect `tests/test_portable_writes.py` exists to catch
    elsewhere in this library: a text file written through Python's text
    mode gains a CRLF on Windows and hashes differently from the same
    file written on Linux. manifest.json, ledger.json and card.md are
    all written through `export._write_text`, which encodes and writes
    bytes for exactly this reason.

    manifest.json and ledger.json are single-line canonical JSON and
    carry no ``\\n`` at all, so a CR check against them cannot fail for
    this defect -- there is no newline for text mode to turn into a
    CRLF. card.md is genuinely multi-line, so it is checked here too,
    built directly with :func:`export.card` rather than through the
    CLI, and read with ``read_bytes`` rather than ``read_text`` --
    ``read_text`` normalises ``\\r\\n`` back to ``\\n`` on read, on the
    same machine that would have written it, which is the exact masking
    case ``_write_bytes``'s own docstring names as not being evidence.
    """
    cr = chr(13).encode("ascii")
    for name in ("manifest", "ledger"):
        assert cr not in toy_written.files[name].read_bytes()

    card_path = tmp_path / "card.md"
    text = export.card([toy_written])
    assert "\n" in text, "card.md must be multi-line for this check to mean anything"
    export._write_text(card_path, text)
    assert cr not in card_path.read_bytes()


# --------------------------------------------------------------------------
# labels.arrow agrees with truth() and macro.arrow row by row
# --------------------------------------------------------------------------

def test_labels_jump_is_the_row_by_row_sum_of_truths_jump_column(
    toy_written, toy_tables,
):
    import pyarrow.compute as pc

    expected = (
        toy_tables["truth"].select(["day", "instrument_id", "jump"])
        .group_by(["day", "instrument_id"], use_threads=False)
        .aggregate([("jump", "sum")])
        .sort_by([("day", "ascending"), ("instrument_id", "ascending")])
    )
    labels = toy_tables["labels"].sort_by(
        [("day", "ascending"), ("instrument_id", "ascending")])
    assert labels.column("day").to_pylist() == \
        expected.column("day").to_pylist()
    assert labels.column("instrument_id").to_pylist() == \
        expected.column("instrument_id").to_pylist()
    assert labels.column("jump").to_pylist() == \
        expected.column("jump_sum").to_pylist()
    # The re-derived total is a real, non-trivial number, not a
    # vacuous zero every preset would pass.
    assert pc.sum(labels.column("jump")).as_py() != 0.0


def test_labels_has_one_row_per_day_instrument_pair(toy_written, toy_tables):
    labels = toy_tables["labels"]
    assert labels.num_rows == 20 * 8
    pairs = set(zip(labels.column("day").to_pylist(),
                    labels.column("instrument_id").to_pylist()))
    assert pairs == {(d, i) for d in range(20) for i in range(8)}


def test_labels_regime_decomposes_into_a_cycle_name_and_the_crisis_flag(
    toy_written, toy_tables,
):
    """"the labels table agrees with ... the macro table's cycle column
    row by row" (the design note's Tests section). ``macro.arrow`` has no
    ``cycle`` column on this build -- see this package's pull request,
    "Where the design note did not fit". This checks the same claim
    against what IS on the build: every ``regime`` value names one of the
    five cycle phases ``Engine.macro_state.cycle`` can return, and the
    ``-crisis`` suffix is present on a day if and only if that day's
    ``universe_stress`` in ``macro.arrow`` is above zero.
    """
    labels = toy_tables["labels"]
    macro = toy_tables["macro"]
    stress_by_day = dict(zip(macro.column("day").to_pylist(),
                             macro.column("universe_stress").to_pylist()))

    for day, regime in zip(labels.column("day").to_pylist(),
                           labels.column("regime").to_pylist()):
        crisis = stress_by_day[day] > 0.0
        base = regime[: -len("-crisis")] if regime.endswith("-crisis") \
            else regime
        assert base in export.CYCLE_NAMES
        assert regime.endswith("-crisis") == crisis, (
            f"day {day}: regime {regime!r}, universe_stress "
            f"{stress_by_day[day]}")


def test_labels_regime_shows_the_crisis_suffix_exactly_on_crisis_days(
    tmp_path,
):
    """``toy_written``'s twenty days never enter a crisis (this module's
    docstring states every regime read ``expansion`` in that window), so
    the test above cannot tell a ``_regime`` that always drops the
    ``-crisis`` suffix from a correct one -- the equality it checks is
    vacuously true when ``crisis`` is False on every row. This drives a
    VIX shock into a real crisis window and checks both directions on a
    single run: the suffix present on every crisis day, and absent on
    every day that is not, so neither "always append" nor "never append"
    can pass it.
    """
    universe = tf.Universe.random(6, seed=3)
    scenario = tf.Scenario("crisis-toy").shock(
        "macro.vix", operation="add", value=60.0, at=3, duration=4)
    written = export.export(1, universe=universe, days=15,
                            scenario=scenario, model="pt-v16", out=tmp_path)
    labels = pa.ipc.open_file(written.files["labels"]).read_all()
    macro = pa.ipc.open_file(written.files["macro"]).read_all()
    stress_by_day = dict(zip(macro.column("day").to_pylist(),
                             macro.column("universe_stress").to_pylist()))

    crisis_days = {d for d, s in stress_by_day.items() if s > 0.0}
    calm_days = {d for d, s in stress_by_day.items() if s == 0.0}
    assert crisis_days, "the shock produced no crisis day; strengthen it"
    assert calm_days, "the shock left no calm day to contrast against"

    for day, regime in zip(labels.column("day").to_pylist(),
                           labels.column("regime").to_pylist()):
        if day in crisis_days:
            assert regime.endswith("-crisis"), (
                f"day {day} has universe_stress "
                f"{stress_by_day[day]} but regime {regime!r} carries no "
                "-crisis suffix")
        else:
            assert not regime.endswith("-crisis"), (
                f"day {day} is calm (universe_stress "
                f"{stress_by_day[day]}) but regime {regime!r} carries a "
                "spurious -crisis suffix")


def test_scenario_firing_counts_what_scenario_apply_returns(tmp_path):
    universe = tf.Universe.random(6, seed=3)
    scenario = tf.Scenario("toy-shock").shock(
        "macro.vix", operation="add", value=40.0, at=0, duration=5)
    written = export.export(1, universe=universe, days=10,
                            scenario=scenario, model="pt-v16", out=tmp_path)
    labels = pa.ipc.open_file(written.files["labels"]).read_all()
    by_day = dict(zip(labels.column("day").to_pylist(),
                      labels.column("scenario_firing").to_pylist()))
    # Every instrument on a firing day reads the SAME count (broadcast),
    # and there is at least one firing and one quiet day in this window.
    assert set(by_day.values()) == {0.0, 1.0}
    assert any(v == 1.0 for v in by_day.values())
    assert any(v == 0.0 for v in by_day.values())


def test_a_shared_scenario_is_not_mutated_by_export(tmp_path):
    """A Scenario carries a clock and an audit trail for the run it is
    applied to (`Scenario.apply`'s own docstring). `export` must be safe
    to call for many seeds from one shared scenario -- the CLI's worker
    pool does exactly that -- so the object passed in must read back
    unchanged afterwards.
    """
    universe = tf.Universe.random(4, seed=2)
    scenario = tf.Scenario("shared").shock(
        "macro.vix", operation="add", value=20.0, at=0, duration=3)
    assert scenario.log == ()

    export.export(1, universe=universe, days=5, scenario=scenario,
                 model="pt-v16", out=tmp_path / "a")
    assert scenario.log == (), "export() drove the caller's scenario object"

    export.export(2, universe=universe, days=5, scenario=scenario,
                 model="pt-v16", out=tmp_path / "b")
    assert scenario.log == ()


def test_regime_rejects_a_cycle_name_it_does_not_know():
    with pytest.raises(tf.ValidationError, match="cycle"):
        export._regime("midsummer", False)


def test_regime_appends_crisis_only_when_asked():
    assert export._regime("expansion", False) == "expansion"
    assert export._regime("expansion", True) == "expansion-crisis"


# --------------------------------------------------------------------------
# dtype and identifier rules (tests/test_arrow.py's two checks, reused)
# --------------------------------------------------------------------------

def test_every_float_column_is_f64_across_every_written_table(toy_tables):
    for name, table in toy_tables.items():
        for field in table.schema:
            if pa.types.is_floating(field.type):
                assert field.type == pa.float64(), (
                    f"{name}.arrow: {field.name} is not f64")


def test_identifiers_are_unsigned_integers(toy_tables):
    for name, table in toy_tables.items():
        for id_field in ("day", "instrument_id", "tick", "bar"):
            if id_field in table.column_names:
                assert pa.types.is_unsigned_integer(
                    table.schema.field(id_field).type
                ), f"{name}.arrow: {id_field} is not an unsigned integer"


def test_labels_instrument_id_indexes_into_the_roster(toy_tables):
    labels = toy_tables["labels"]
    assert set(labels.column("instrument_id").to_pylist()) == set(range(8))


# --------------------------------------------------------------------------
# The card
# --------------------------------------------------------------------------

def test_card_states_the_preset_fingerprint_and_every_envelope_gap(
    toy_written,
):
    from tradefloor import envelope

    text = export.card([toy_written])
    panel = envelope.certified()
    assert toy_written.card["model_fingerprint"] in text
    assert panel["preset"] in text
    for gap in panel["gaps"]:
        assert gap["id"] in text, f"gap {gap['id']!r} missing from the card"


def test_card_names_prints_absent_when_the_engine_lacks_it(toy_written):
    import copy

    class NoPrintsEngine:
        def __init__(self, inner):
            self._inner = inner

        def __getattr__(self, name):
            if name == "prints":
                raise AttributeError(name)
            return getattr(self._inner, name)

    engine = tf.Engine(seed=1, universe=tf.Universe.random(3, seed=1))
    prints, info = export._maybe_read_prints(NoPrintsEngine(engine))
    assert prints is None
    assert info["present"] is False

    without = copy.deepcopy(toy_written.card)
    without["prints"] = info
    without["rows"] = {**without["rows"], "prints": None}
    stand_in = export.Written(seed=toy_written.seed, files=dict(
        (k, v) for k, v in toy_written.files.items() if k != "prints"
    ), card=without)
    text = export.card([stand_in])
    assert "absent" in text
    assert info["reason"] in text


def test_card_refuses_an_empty_batch():
    with pytest.raises(tf.ValidationError, match="at least one"):
        export.card([])


def test_card_refuses_a_batch_that_mixes_models(toy_written, tmp_path):
    other_universe = tf.Universe.random(8, seed=99)
    other = export.export(43, universe=other_universe, days=20,
                          scenario=None, model="pt-v14", out=tmp_path)
    with pytest.raises(tf.ValidationError, match="one model"):
        export.card([toy_written, other])


def test_card_names_the_cli_command_when_the_cli_attached_it(toy_written):
    import copy

    stamped = copy.deepcopy(toy_written.card)
    stamped["invocation"] = {
        "preset": "pt-v16", "seeds": "42", "roster_seed": 99,
        "scenario": "none", "workers": 1, "out": "data/",
    }
    stand_in = export.Written(seed=toy_written.seed, files=toy_written.files,
                              card=stamped)
    text = export.card([stand_in])
    assert "python tools/dataset/export.py" in text
    assert "--roster-seed 99" in text
    assert "--names 8" in text


def test_export_records_each_tables_own_column_names(toy_written):
    columns = toy_written.card["columns"]
    assert columns["bars"] == ["day", "bar", "instrument_id", "open",
                               "high", "low", "close", "volume"]
    assert set(columns["truth"]) >= {"day", "tick", "instrument_id",
                                     "mispricing_s", "jump"}
    assert columns["prints"] == toy_written.card["prints"]["columns"]
    assert "universe_stress" in columns["macro"]
    assert columns["labels"] == ["day", "instrument_id", "jump", "regime",
                                 "scenario_firing"]


def test_card_documents_a_column_it_does_not_yet_know_the_unit_for(
    toy_written,
):
    """`_UNITS` is not updated ahead of `Engine.prints()` gaining a
    `clamp` column, on purpose: this proves the fallback path a real new
    column will take, by injecting one under a name `_UNITS` has never
    heard of and checking the card still lists it rather than dropping it.
    """
    import copy

    stamped = copy.deepcopy(toy_written.card)
    stamped["columns"]["prints"] = ["day", "tick", "instrument_id",
                                    "print", "model_price", "shock",
                                    "absorbed", "clamp"]
    stamped["prints"] = {**stamped["prints"],
                         "columns": stamped["columns"]["prints"]}
    stand_in = export.Written(seed=toy_written.seed, files=toy_written.files,
                              card=stamped)
    text = export.card([stand_in])
    assert "| clamp | " in text
    assert export._UNKNOWN_UNIT in text
    # every column _UNITS DOES know is still there, unaffected
    assert "| shock | " in text
    assert "| absorbed | " in text


def test_card_names_the_intraday_volume_gap_under_bars(toy_written):
    text = export.card([toy_written])
    idx = text.index("### bars.arrow")
    nxt = text.index("### truth.arrow")
    assert "volume" in text[idx:nxt] and "day total" in text[idx:nxt]


# --------------------------------------------------------------------------
# The worker pool: sweep.py's bounded window
# --------------------------------------------------------------------------

def test_the_worker_pool_never_exceeds_the_requested_width():
    """"the exporter's worker loop is sweep.py's bounded window and memory
    stays O(workers) on a toy run" (the design note's Tests section).

    Measuring process memory directly would be slow and flaky across
    platforms, so this checks the property the memory claim rests on: at
    most `workers` calls are ever in flight, which is what bounds how many
    of `export`'s tables can be resident at once. A stub stands in for
    `export` -- real ones are checked for real end to end in
    `test_a_toy_batch_matches_running_each_seed_by_hand` below -- and each
    call sleeps briefly so overlapping calls have a chance to overlap,
    which also lets this test prove concurrency actually happened rather
    than merely never exceeding a bound nothing approached.
    """
    lock = threading.Lock()
    state = {"current": 0, "peak": 0}

    def slow(seed: int) -> int:
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        time.sleep(0.05)
        with lock:
            state["current"] -= 1
        return seed

    seeds = list(range(1, 9))
    results = export._run_batch(seeds, slow, workers=3)
    assert results == seeds  # seed order, not completion order
    assert state["peak"] <= 3
    assert state["peak"] >= 2, (
        "no overlap was observed; this run cannot tell a bounded window "
        "apart from an accidentally serial one")


def test_the_deque_itself_bounds_outstanding_submissions(monkeypatch):
    """The test above cannot tell `_run_batch`'s own bounded-deque logic
    (`export.py`, ``if len(pending) == workers: break``) apart from
    ``ThreadPoolExecutor(max_workers=workers)`` alone doing the job: both
    are set to the SAME ``workers`` value, so a mutation to either one
    alone leaves the other still capping peak concurrency at 3, and the
    test above still passes.

    This isolates the deque by replacing ``ThreadPoolExecutor`` with a
    wrapper whose own concurrency cap is twenty times wider, so nothing
    but `_run_batch`'s own submission logic can be bounding how many
    futures are SUBMITTED-BUT-NOT-YET-COLLECTED at once -- the quantity
    that actually determines how many of `export`'s in-progress tables
    can be resident, since a future submitted but not started still holds
    its result once the call finishes, until `.result()` is read.
    """
    outstanding = {"current": 0, "peak": 0}
    lock = threading.Lock()

    class _CountingFuture:
        def __init__(self, inner):
            self._inner = inner

        def result(self, *args, **kwargs):
            try:
                return self._inner.result(*args, **kwargs)
            finally:
                with lock:
                    outstanding["current"] -= 1

    class _WideExecutor:
        """`_run_batch`'s own interface (`submit`, a context manager),
        wrapping a real pool at twenty times the requested width so its
        cap is never what binds concurrency."""

        def __init__(self, max_workers: int) -> None:
            self._inner = ThreadPoolExecutor(max_workers=max_workers * 20)

        def submit(self, fn, *args):
            with lock:
                outstanding["current"] += 1
                outstanding["peak"] = max(outstanding["peak"],
                                          outstanding["current"])
            return _CountingFuture(self._inner.submit(fn, *args))

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return self._inner.__exit__(*exc_info)

    monkeypatch.setattr(export, "ThreadPoolExecutor", _WideExecutor)

    def slow(seed: int) -> int:
        time.sleep(0.03)
        return seed

    seeds = list(range(1, 9))
    results = export._run_batch(seeds, slow, workers=3)
    assert results == seeds
    assert outstanding["peak"] <= 3, (
        f"{outstanding['peak']} futures were outstanding at once against "
        "a requested width of 3, with the executor's own cap widened out "
        "of the way -- the deque's own backpressure is not bounding "
        "submission")


def test_the_worker_pool_refuses_zero_workers():
    with pytest.raises(tf.ValidationError, match="workers"):
        export._run_batch([1, 2], lambda s: s, workers=0)


def test_a_single_worker_runs_sequentially_and_in_order():
    seen: list[int] = []

    def record(seed: int) -> int:
        seen.append(seed)
        return seed * 10

    results = export._run_batch([3, 1, 2], record, workers=1)
    assert seen == [3, 1, 2]
    assert results == [30, 10, 20]


def test_a_toy_batch_matches_running_each_seed_by_hand(tmp_path):
    universe = tf.Universe.random(4, seed=1)

    def run_one(seed: int) -> export.Written:
        return export.export(seed, universe=universe, days=3,
                             scenario=None, model="pt-v16",
                             out=tmp_path / "batch")

    batch = export._run_batch([1, 2, 3], run_one, workers=2)
    assert [w.seed for w in batch] == [1, 2, 3]
    for w in batch:
        by_hand = export.export(w.seed, universe=universe, days=3,
                                scenario=None, model="pt-v16",
                                out=tmp_path / "by_hand")
        assert w.files["bars"].read_bytes() == \
            by_hand.files["bars"].read_bytes()


# --------------------------------------------------------------------------
# Seed range parsing
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("42", [42]),
    ("1-3", [1, 2, 3]),
    ("1-3,7,10-12", [1, 2, 3, 7, 10, 11, 12]),
    ("5,1,3", [1, 3, 5]),
    ("2-2", [2]),
])
def test_parse_seeds(text, expected):
    assert export._parse_seeds(text) == expected


def test_parse_seeds_refuses_an_empty_string():
    with pytest.raises(tf.ValidationError):
        export._parse_seeds("")


def test_parse_seeds_refuses_a_backwards_range():
    with pytest.raises(tf.ValidationError, match="ends before"):
        export._parse_seeds("9-3")


# --------------------------------------------------------------------------
# The command line
# --------------------------------------------------------------------------

def test_help_renders():
    # The failure mode `tests/test_tool_help.py` guards against for
    # `tools/calibration/*.py`: a literal percent sign in a help string
    # crashes argparse's `%`-based expansion. That file globs only
    # `tools/calibration`, so this script needs its own check.
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert "usage:" in proc.stdout
    # The description is the module docstring's opening paragraph. A
    # split on the first LINE break rather than the first blank line
    # would cut it mid-sentence, since the opening sentence wraps onto a
    # second physical line in the source.
    assert "cannot give you." in proc.stdout


def test_first_paragraph_joins_a_wrapped_opening_sentence():
    doc = "Line one wraps\nonto line two.\n\nA second paragraph."
    assert export._first_paragraph(doc) == "Line one wraps onto line two."


def test_the_cli_runs_a_small_batch_end_to_end(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--preset", "pt-v16",
         "--seeds", "1-2", "--names", "4", "--roster-seed", "5",
         "--days", "3", "--scenario", "none", "--workers", "2",
         "--out", str(tmp_path)],
        capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    for seed in (1, 2):
        seed_dir = tmp_path / str(seed)
        assert seed_dir.is_dir()
        assert {p.name for p in seed_dir.iterdir()} == {
            "bars.arrow", "truth.arrow", "prints.arrow", "macro.arrow",
            "labels.arrow", "manifest.json", "ledger.json",
        }
    card_path = tmp_path / "card.md"
    assert card_path.exists()
    text = card_path.read_text(encoding="utf-8")
    assert "--roster-seed 5" in text
    assert "--seeds 1-2" in text
