"""Years of simulated markets, written to disk with the truth a real market
cannot give you.

    python tools/dataset/export.py --preset pt-v16 --seeds 1-50 --names 40 \
        --roster-seed 111 --days 252 --scenario none --workers 4 --out data/

## What one seed writes

:func:`export` runs one seed to the requested horizon and writes seven files
under ``<out>/<seed>/``:

- ``bars.arrow``, day-grain OHLCV (``Engine.bars(grain="day")``): one row
  per instrument per day.
- ``truth.arrow`` (``Engine.truth()``): the ground truth, one row per
  instrument per tick.
- ``prints.arrow`` (``Engine.prints()``), when the installed build carries
  it. Absent otherwise, and the card names the absence rather than the
  file silently not existing.
- ``macro.arrow`` (``Engine.macro_table()``): one row per day.
- ``labels.arrow``: one row per ``(day, instrument_id)``. See "The label
  columns" below.
- ``manifest.json`` (:meth:`tradefloor.RunManifest.of`, carrying a
  :class:`tradefloor.DayLedger`).
- ``ledger.json`` (the ledger itself).

One directory per seed with these fixed names, so a reader joins
``truth.arrow`` against ``labels.arrow`` on ``(day, instrument_id)``
without a catalogue.

## The label columns

``labels.arrow`` answers three questions a raw price series cannot: did a
jump fire, what regime was the economy in, and did the scenario intervene.
None of the three is derived from price.

``jump`` sums ``truth()``'s own ``jump`` component over a day's ticks for
one instrument. ``apply_jumps`` writes at most one nonzero tick a day, so
the sum recovers that single value; it is written as a sum, not a lookup,
so the claim "this equals ``truth()``'s jump slot" is a row-by-row identity
a reader can check by re-deriving it, which :func:`_labels_table` is built
to make easy.

``regime`` names the economy's cycle phase (``expansion``, ``peak``,
``contraction``, ``trough`` or ``recovery``, read from
:attr:`Engine.macro_state` before the day's close) with a ``-crisis``
suffix appended on a day whose recorded ``universe_stress`` is above zero.
``universe_stress`` is VIX points above the crisis threshold, already
carried on ``macro.arrow`` and already documented there as the model's own
regime signal, rather than an estimate of one. See "Where the design note
did not fit" in this package's pull request for why the crisis half of the
label reads ``universe_stress`` rather than the correlation-blend weight
the design note named: the blend weight is computed per tick inside
``rust/src/market/tick.rs`` and has no Python accessor, and recomputing its
formula here would be a second implementation of engine arithmetic, which
is exactly what this project's porting discipline exists to avoid.

``scenario_firing`` is the count of interventions
:meth:`tradefloor.Scenario.apply` fired on that day, 0 for every day of a
run with no scenario.

All three broadcast one value to every instrument in the roster on days
where the underlying signal is not per-instrument (``regime`` and
``scenario_firing``), which is what lets a reader join the whole table on
``(day, instrument_id)`` alone.

## What this does not do

It does not mutate the roster: no run here lists or delists an instrument,
so the per-slot width question ``tradefloor.manifest.state_hash`` raises
about ``volume_idio`` (tradefloor issue #148) never arises for a ledger
this package writes. It does not turn on
``Engine.settle_depth_counterfactual``, so ``prints.arrow`` carries
whatever base column set the installed build ships, without
``unbounded_print`` or ``liquidity_share`` -- never a fixed count, since
``Engine.prints()`` is due a ``clamp`` column beside ``shock`` and
``absorbed`` (see :data:`_UNITS`, which resolves an unlisted column
against every table's own schema rather than typing one out). It
discards intraday volume: ``bars.arrow`` sums each instrument's volume
to one figure per day, and neither ``truth.arrow`` nor ``prints.arrow``
carries a volume column at any grain, so how volume distributed across a
session is not recoverable from this dataset; the card names this gap
under its ``bars.arrow`` column table. It does not publish anything:
:func:`export` writes to a path you give it and :func:`card` returns text;
nothing here uploads, and the tests that exercise this module write to a
pytest ``tmp_path`` and nothing else.

## A ledger without snapshots

:func:`export` writes its :class:`~tradefloor.DayLedger` with
``snapshots=False``. A years-long, many-seed export is exactly the case
the module docstring on ``DayLedger`` measures against: with the states,
252 days at 30 ticks a day writes 4.88 MB per seed; without them, 16.9 kB.
A reader who samples this dataset's ledgers with
:func:`tradefloor.manifest.verify` pays ``d + 1`` day-runs for a checked
day ``d`` rather than one, which :class:`~tradefloor.Verification` reports
plainly through its own caveats.

## Measured

On ``Universe.random(8, seed=99)``, seed 42, twenty days at 390 ticks a
day, no scenario, preset ``pt-v16``, at commit ``57c9273`` (this package's
integration base):

    bars    160 rows,    19,362 bytes
    truth 62,400 rows, 6,757,842 bytes
    prints 62,400 rows, 2,756,338 bytes
    macro    20 rows,       3,434 bytes
    labels  160 rows,       7,010 bytes

``labels.arrow``'s ``jump`` column summed to 0.025649230808638512 across
all 160 rows on this run, every regime read ``expansion`` (no crisis day
in this window) and every ``scenario_firing`` read 0.0 (no scenario).
:func:`test_dataset_export.test_labels_jump_is_the_row_by_row_sum_of_truths_jump_column`
checks the jump figure by re-deriving it from ``truth.arrow`` rather than
by trusting the number above.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

import pyarrow as pa

import tradefloor as tf
from tradefloor import envelope
from tradefloor._core import Engine, Instrument, ModelParams, ValidationError

ROOT = Path(__file__).resolve().parents[2]

#: Ticks in one simulated trading day. Not a CLI flag: the exporter always
#: writes a full session, the same count `envelope` certifies against and
#: `sweep.py` defaults to, so a reader never has to ask what a "day" in
#: this dataset covers.
TICKS_PER_DAY = 390

#: Session clock. `run_days` and `sweep.py` default to the same three
#: values; named here rather than re-typed so the two cannot drift apart.
HOUR, MINUTE, DAY_OF_WEEK = 9, 30, 3

#: The five names `Engine.macro_state.cycle` returns, per `_core.pyi`'s
#: `CycleName`. Declared here so a name this build does not recognise
#: fails `_regime` by name instead of writing a label nothing can parse.
CYCLE_NAMES = ("expansion", "peak", "contraction", "trough", "recovery")


@dataclass(frozen=True)
class Written:
    """What one call to :func:`export` produced.

    ``files`` maps each of the seven artefact names (``bars``, ``truth``,
    ``prints``, ``macro``, ``labels``, ``manifest``, ``ledger``) to the
    path written, with ``prints`` absent from the mapping on a build that
    does not carry ``Engine.prints``. ``card`` is this seed's own share of
    the facts :func:`card` renders into markdown: row counts, byte counts,
    fingerprints and the scenario it ran, plus whatever the caller of
    :func:`export` chooses to add afterwards under a ``"invocation"`` key
    (the CLI in this module does, so the rendered card can name the
    command that reproduces the whole batch; see :func:`main`).
    """

    seed: int
    files: dict[str, Path]
    card: dict[str, Any]


def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                         text=True, encoding="utf-8")
    return out.stdout.strip() if out.returncode == 0 else ""


def _write_bytes(path: Path, data: bytes) -> int:
    """Write ``data`` and return its length.

    Always binary. A manifest or a card written through Python's text mode
    would gain a CRLF on Windows and hash differently from the same file
    written on Linux -- the exact defect ``tests/test_portable_writes.py``
    exists to catch elsewhere in this library, for the same reason a
    written-then-reread digest is not evidence against it: the checkout
    that reads it back is on the machine that wrote it.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return len(data)


def _write_text(path: Path, text: str) -> int:
    return _write_bytes(path, text.encode("utf-8"))


def _write_table(path: Path, table: pa.Table) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pa.OSFile(str(path), "wb") as sink:
        with pa.ipc.new_file(sink, table.schema) as writer:
            writer.write_table(table)
    return path.stat().st_size


def _read_table(stream: Any) -> pa.Table:
    """Materialise one of the engine's Arrow streams.

    ``RecordBatchReader.from_stream`` reads the C Stream Interface the
    engine exposes without a copy at the boundary; what happens on this
    side is the one that matters for memory, and this reads it whole
    because every table here is written once and also used to build
    ``labels.arrow``, so re-streaming it a second time would cost a second
    pass over the engine's buffers for nothing. `tools/remeasure/measures.py`
    reads the macro table the same way.
    """
    return pa.RecordBatchReader.from_stream(stream).read_all()


def _regime(cycle: str, crisis: bool) -> str:
    """The value ``labels.arrow`` writes for one day's regime.

    ``cycle`` must be one of :data:`CYCLE_NAMES`, checked here rather than
    left to fail downstream as an unfamiliar string a reader has to trace
    back to its source.
    """
    if cycle not in CYCLE_NAMES:
        raise ValidationError(
            f"macro_state.cycle returned {cycle!r}, which is not one of "
            f"{CYCLE_NAMES}. Either this build added a cycle phase this "
            "function does not know, or the value did not come from "
            "Engine.macro_state."
        )
    return f"{cycle}-crisis" if crisis else cycle


def _labels_table(truth: pa.Table, macro: pa.Table, cycles: Sequence[str],
                  firings: Sequence[int], days: int) -> pa.Table:
    """Build ``labels.arrow``: one row per ``(day, instrument_id)``.

    ``cycles`` and ``firings`` are indexed by day (``cycles[d]`` is the
    cycle phase captured before day ``d``'s close, ``firings[d]`` is how
    many interventions fired on day ``d``), both filled by :func:`export`'s
    own day loop rather than recovered from the written tables, because
    neither survives a replay of a single reconstructed engine: a replayed
    engine holds the state at the end of the run, not the day-by-day
    history a label table needs. See "Where the design note did not fit"
    in this package's pull request.
    """
    if len(cycles) != days or len(firings) != days:
        raise ValidationError(
            f"labels needs one cycle name and one firing count per day: "
            f"got {len(cycles)} cycles and {len(firings)} firing counts for "
            f"{days} days."
        )

    jumps = (
        truth.select(["day", "instrument_id", "jump"])
        .group_by(["day", "instrument_id"], use_threads=False)
        .aggregate([("jump", "sum")])
        .sort_by([("day", "ascending"), ("instrument_id", "ascending")])
    )
    day_col = jumps.column("day").to_pylist()
    instrument_col = jumps.column("instrument_id").to_pylist()
    jump_col = jumps.column("jump_sum").to_pylist()

    stress_by_day = dict(zip(macro.column("day").to_pylist(),
                             macro.column("universe_stress").to_pylist()))
    missing = sorted(set(day_col) - set(stress_by_day))
    if missing:
        raise ValidationError(
            f"truth.arrow names days {missing} that macro.arrow does not "
            "carry a universe_stress row for. The two tables were not "
            "built from the same recorded run."
        )

    regime_col = [_regime(cycles[d], stress_by_day[d] > 0.0) for d in day_col]
    firing_col = [float(firings[d]) for d in day_col]

    return pa.table({
        "day": pa.array(day_col, type=pa.uint32()),
        "instrument_id": pa.array(instrument_col, type=pa.uint32()),
        "jump": pa.array(jump_col, type=pa.float64()),
        "regime": pa.array(regime_col, type=pa.string()),
        "scenario_firing": pa.array(firing_col, type=pa.float64()),
    })


def _maybe_read_prints(engine: Any) -> "tuple[pa.Table | None, dict[str, Any]]":
    """``prints.arrow``'s table and card facts, or ``(None, ...)`` when the
    installed build's ``Engine`` carries no ``prints`` method.

    Split out from :func:`export` so the absent branch is reachable by a
    plain object in a test, without deleting a method off the compiled
    :class:`~tradefloor.Engine` class -- which would mutate a class every
    other test on this machine imports.
    """
    if not hasattr(engine, "prints"):
        return None, {
            "present": False,
            "reason": "this build's Engine carries no prints() method",
        }
    prints = _read_table(engine.prints())
    meta = prints.schema.metadata or {}
    info = {
        "present": True,
        "columns": prints.column_names,
        "depth_counterfactual": meta.get(b"depth_counterfactual", b"")
        .decode("utf-8"),
        "caveat": meta.get(b"caveat", b"").decode("utf-8"),
    }
    return prints, info


def export(seed: int, *, universe: Sequence[Instrument], days: int,
          scenario: "tf.Scenario | None", model: "str | ModelParams | None",
          out: Path) -> Written:
    """Run one seed and write its seven files under ``out / str(seed)``.

    ``scenario``, when given, is copied before it drives this run.
    :meth:`tradefloor.Scenario.apply` anchors a hold or a ramp, and its
    audit trail, to the run it is applied to and identifies that run by
    its own clock -- "one scenario object driving two runs at once shares
    one clock between them", in the module's own words. A caller sweeping
    many seeds from one shared :class:`~tradefloor.Scenario` (the CLI in
    this module does, under ``--workers``) must therefore see this
    function never write to that shared object, which is what makes
    calling it from several worker threads safe.

    Raises :class:`~tradefloor.ValidationError` for ``days < 1``; every
    other input is validated by :class:`~tradefloor.Engine` itself.
    """
    if days < 1:
        raise ValidationError(f"days must be at least 1, got {days}")

    roster = tf.Universe(list(universe))
    run_scenario = scenario.copy() if scenario is not None else None

    engine = Engine(seed=seed, universe=roster, model=model)
    ledger = tf.DayLedger(snapshots=False)
    cycles: list[str] = []
    firings: list[int] = []

    for day in range(days):
        if run_scenario is not None:
            fired = run_scenario.apply(engine, day)
            firings.append(len(fired))
        else:
            firings.append(0)
        engine.open_market()
        engine.run_session(HOUR, MINUTE, DAY_OF_WEEK, TICKS_PER_DAY)
        # Before the close, for the reason `sweep.py` records here too: the
        # close advances the macro chain into the next day, and both the
        # tape and the regime for day `day` have to carry what day `day`
        # actually traded under.
        engine.record(day)
        cycles.append(engine.macro_state.cycle)
        engine.close_market()
        # After the close: a leaf commits to the state the NEXT day starts
        # from, which is what makes day d checkable from day d - 1.
        ledger.close(engine)

    seed_dir = out / str(seed)
    files: dict[str, Path] = {}
    rows: dict[str, int | None] = {}
    written_bytes: dict[str, int] = {}
    # Each table's OWN column names, read off the table rather than typed
    # out, so a column the engine adds (`Engine.prints()` is due a `clamp`
    # column) reaches the card the next time this runs, with no edit here.
    columns: dict[str, list[str] | None] = {}

    bars = _read_table(engine.bars(grain="day"))
    files["bars"] = seed_dir / "bars.arrow"
    written_bytes["bars"] = _write_table(files["bars"], bars)
    rows["bars"] = bars.num_rows
    columns["bars"] = bars.column_names

    truth = _read_table(engine.truth())
    files["truth"] = seed_dir / "truth.arrow"
    written_bytes["truth"] = _write_table(files["truth"], truth)
    rows["truth"] = truth.num_rows
    columns["truth"] = truth.column_names

    prints, prints_info = _maybe_read_prints(engine)
    if prints is not None:
        files["prints"] = seed_dir / "prints.arrow"
        written_bytes["prints"] = _write_table(files["prints"], prints)
        rows["prints"] = prints.num_rows
    else:
        rows["prints"] = None
    columns["prints"] = prints_info.get("columns")

    macro = _read_table(engine.macro_table())
    files["macro"] = seed_dir / "macro.arrow"
    written_bytes["macro"] = _write_table(files["macro"], macro)
    rows["macro"] = macro.num_rows
    columns["macro"] = macro.column_names

    labels = _labels_table(truth, macro, cycles, firings, days)
    files["labels"] = seed_dir / "labels.arrow"
    written_bytes["labels"] = _write_table(files["labels"], labels)
    rows["labels"] = labels.num_rows
    columns["labels"] = labels.column_names

    manifest = tf.RunManifest.of(engine, seed=seed, universe=roster,
                                 scenario=run_scenario, ledger=ledger)
    files["manifest"] = seed_dir / "manifest.json"
    written_bytes["manifest"] = _write_text(files["manifest"],
                                            manifest.to_json())

    files["ledger"] = seed_dir / "ledger.json"
    written_bytes["ledger"] = _write_text(files["ledger"], ledger.to_json())

    card = {
        "seed": seed,
        "days": days,
        "model_fingerprint": engine.model_fingerprint,
        "universe": {"size": len(roster), "fingerprint": roster.fingerprint},
        "scenario": ({"name": run_scenario.name or None,
                     "source": run_scenario.source,
                     "fingerprint": run_scenario.fingerprint}
                    if run_scenario is not None else None),
        "market_digest": manifest.result["digest"],
        "day_ledger_root": ledger.root(),
        "rows": rows,
        "bytes": written_bytes,
        "columns": columns,
        "prints": prints_info,
    }
    return Written(seed=seed, files=files, card=card)


# -- the data card -----------------------------------------------------------


# Column -> unit, by table. Looked up against each table's OWN recorded
# column list (`Written.card["columns"][table]`, filled in `export`) rather
# than iterated in its own right, so a column this dict does not yet name
# still renders in the card -- with `_UNKNOWN_UNIT` in place of a unit --
# instead of silently vanishing from the documentation the day the engine
# grows one. `Engine.prints()` is due a `clamp` column beside `shock` and
# `absorbed` (tradefloor issue tracked in P2's review); this dict is not
# updated for it ahead of that landing, on purpose, so the fallback path
# is exercised by that column for real rather than only by a test.
_UNITS: dict[str, dict[str, str]] = {
    "bars": {
        "day": "day index, 0-based",
        "bar": "bucket index within the day (always 0 at day grain)",
        "instrument_id": "index into the roster",
        "open": "dollars", "high": "dollars", "low": "dollars",
        "close": "dollars", "volume": "shares",
    },
    "truth": {
        "day": "day index", "tick": "tick index within the day",
        "instrument_id": "index into the roster",
        "mispricing_s": "log deviation from fair value",
        "fundamental_value": "dollars", "anchor_price": "dollars",
        "reversion": "log-return contribution",
        "momentum": "log-return contribution",
        "crowd_lean": "log-return contribution",
        "company_news": "log-return contribution",
        "order_flow_impact": "log-return contribution",
        "short_squeeze_effect": "log-return contribution",
        "random_noise": "log-return contribution",
        "circuit_breaker": "log-return contribution",
        "jump": "log-return contribution",
    },
    "prints": {
        "day": "day index", "tick": "tick index within the day",
        "instrument_id": "index into the roster",
        "print": "dollars", "model_price": "dollars",
        "shock": "log distance, last print to model price",
        "absorbed": "log distance, model price to print",
    },
    "macro": {
        "day": "day index", "vix": "points",
        "federal_funds_rate": "fractional",
        "corporate_bond_yield": "fractional",
        "inflation_rate": "fractional",
        "unemployment_rate": "fractional",
        "gdp_growth": "fractional",
        "qe_pe_boost": "fractional",
        "fear_greed_index": "points, 0 to 100",
        "universe_stress": "VIX points above the crisis threshold",
    },
    "labels": {
        "day": "day index", "instrument_id": "index into the roster",
        "jump": "log-return contribution, summed over the day",
        "regime": "cycle phase name, with a -crisis suffix",
        "scenario_firing": "count of interventions fired that day",
    },
}

_UNKNOWN_UNIT = "not documented here yet; see the engine's own schema"

_LABEL_DEFINITIONS = (
    ("jump", "The day's sum of truth.arrow's own jump column for this "
             "instrument. Zero on a day nothing fired."),
    ("regime", "The economy's cycle phase (expansion, peak, contraction, "
              "trough or recovery), captured before the day's close, with "
              "-crisis appended when that day's universe_stress in "
              "macro.arrow is above zero."),
    ("scenario_firing", "How many interventions Scenario.apply fired on "
                        "that day. Zero throughout a run with no "
                        "scenario."),
)


def card(written: Sequence[Written]) -> str:
    """Render the data card for a batch of :func:`export` calls, as markdown.

    Reads :func:`tradefloor.envelope.certified` at call time rather than
    copying its numbers into this function, so the card states the panel
    and the gaps the installed build actually carries.

    Raises :class:`~tradefloor.ValidationError` on an empty batch, and when
    the seeds do not share one preset, roster or day count -- a card that
    silently reported one of several disagreeing runs would misdescribe
    the rest.
    """
    if not written:
        raise ValidationError("card() needs at least one Written; got none")

    fingerprints = {w.card["model_fingerprint"] for w in written}
    universes = {w.card["universe"]["fingerprint"] for w in written}
    day_counts = {w.card["days"] for w in written}
    if len(fingerprints) > 1 or len(universes) > 1 or len(day_counts) > 1:
        raise ValidationError(
            "card() describes one batch: one model, one roster, one day "
            f"count. Got model fingerprints {sorted(fingerprints)}, "
            f"universe fingerprints {[u[:12] for u in sorted(universes)]}, "
            f"day counts {sorted(day_counts)}."
        )

    first = written[0]
    seeds = sorted(w.seed for w in written)
    days = first.card["days"]
    model_fp = first.card["model_fingerprint"]
    uni = first.card["universe"]
    scen = first.card["scenario"]
    invocation = first.card.get("invocation")
    panel = envelope.certified()

    out: list[str] = []
    out.append("# tradefloor truth-labelled dataset")
    out.append("")
    out.append(
        f"{len(seeds)} run{'s' if len(seeds) != 1 else ''} of "
        f"{uni['size']} instruments over {days} days each, model "
        f"{model_fp!r}, roster fingerprint {uni['fingerprint'][:12]}...."
    )
    out.append("")

    out.append("## Run")
    out.append("")
    out.append("| field | value |")
    out.append("|---|---|")
    out.append(f"| model fingerprint | `{model_fp}` |")
    out.append(f"| roster | {uni['size']} instruments, "
               f"`{uni['fingerprint'][:16]}...` |")
    out.append(f"| seeds | {', '.join(str(s) for s in seeds)} |")
    out.append(f"| days per seed | {days} |")
    out.append(f"| ticks per day | {TICKS_PER_DAY} |")
    out.append(
        "| scenario | "
        + (f"{scen['name'] or scen['source'] or 'unnamed'} "
           f"(`{scen['fingerprint'][:12]}...`)" if scen else "none")
        + " |"
    )
    out.append(f"| tradefloor | {tf.version()} |")
    out.append(f"| commit | `{_git('rev-parse', 'HEAD') or 'unknown'}` |")
    out.append("")

    out.append("## Files")
    out.append("")
    out.append("| seed | bars | truth | prints | macro | labels | "
               "market digest | ledger root |")
    out.append("|---|---:|---:|---:|---:|---:|---|---|")
    for w in sorted(written, key=lambda w: w.seed):
        r = w.card["rows"]
        out.append(
            f"| {w.seed} | {r['bars']} | {r['truth']} | "
            f"{r['prints'] if r['prints'] is not None else 'absent'} | "
            f"{r['macro']} | {r['labels']} | "
            f"`{w.card['market_digest'][:12]}...` | "
            f"`{w.card['day_ledger_root'][:12]}...` |"
        )
    out.append("")
    if not first.card["prints"]["present"]:
        out.append(
            f"prints.arrow is absent: {first.card['prints']['reason']}."
        )
        out.append("")

    out.append("## Realism envelope")
    out.append("")
    out.append(
        f"Preset `{panel['preset']}`, certified to "
        f"{panel['certified_horizon_days']} days."
    )
    out.append("")
    out.append("| statistic | measured | band | in band |")
    out.append("|---|---:|---|:---:|")
    for name, row in sorted(panel["statistics"].items()):
        band = f"{row['band'][0]} to {row['band'][1]}"
        out.append(f"| {name} | {row['measured']} | {band} | "
                   f"{'yes' if row['in_band'] else 'no'} |")
    out.append("")
    if panel["gaps"]:
        out.append("Gaps in force:")
        out.append("")
        for gap in panel["gaps"]:
            out.append(f"- **{gap['id']}**: {gap['summary']} "
                       f"({gap['forbids']})")
        out.append("")

    out.append("## Columns")
    out.append("")
    recorded_columns = first.card.get("columns") or {}
    # Whether prints.arrow is skipped here is decided by THIS flag alone --
    # the same one the Files table and the "is absent" paragraph above read
    # -- rather than by whether `recorded_columns["prints"]` happens to be
    # `None`. The two used to be two independent signals that `export`'s own
    # code kept in sync but nothing enforced, so a `Written` assembled by
    # hand with the presence flag off and a stale `columns["prints"]` left
    # over rendered a full prints.arrow column table directly under a
    # paragraph saying the file does not exist.
    prints_present = bool((first.card.get("prints") or {}).get("present"))
    for table_name in ("bars", "truth", "prints", "macro", "labels"):
        if table_name == "prints" and not prints_present:
            continue  # reported as absent above; no schema to list
        columns = recorded_columns.get(table_name)
        if columns is None:
            # A `Written` built without a "columns" entry (`export` always
            # records one; a hand-built `Written` in a test may not) falls
            # back to the units table's own keys, in the order declared.
            columns = list(_UNITS[table_name])
        out.append(f"### {table_name}.arrow")
        out.append("")
        out.append("| column | unit |")
        out.append("|---|---|")
        for column in columns:
            out.append(
                f"| {column} | {_UNITS[table_name].get(column, _UNKNOWN_UNIT)} |"
            )
        out.append("")
        if table_name == "bars":
            out.append(
                "volume here is a day total. Neither truth.arrow nor "
                "prints.arrow carries a volume column at any grain, so "
                "how volume distributed across the session is not in "
                "this dataset."
            )
            out.append("")

    out.append("## Label definitions")
    out.append("")
    for name, definition in _LABEL_DEFINITIONS:
        out.append(f"- **{name}**: {definition}")
    out.append("")

    out.append("## Re-deriving this data")
    out.append("")
    if invocation is not None:
        parts = [
            "python tools/dataset/export.py",
            f"--preset {invocation['preset']}",
            f"--seeds {invocation['seeds']}",
            f"--names {uni['size']}",
            f"--roster-seed {invocation['roster_seed']}",
            f"--days {days}",
            f"--scenario {invocation['scenario']}",
            f"--workers {invocation['workers']}",
            f"--out {invocation['out']}",
        ]
        out.append("```")
        out.append(" \\\n    ".join(parts))
        out.append("```")
    else:
        out.append(
            "This card was rendered from `Written` objects that carry no "
            "`\"invocation\"` entry, which only the command-line entry "
            "point in this module attaches. What is known: model "
            f"fingerprint `{model_fp}`, roster fingerprint "
            f"`{uni['fingerprint'][:16]}...` ({uni['size']} instruments), "
            f"seeds {', '.join(str(s) for s in seeds)}, {days} days, "
            "scenario " + (scen['name'] or scen['source'] or 'unnamed'
                          if scen else "none") + ". The roster's own seed "
            "is not recoverable from its fingerprint; run "
            "tools/dataset/export.py directly to get a card that names "
            "the full command."
        )
    out.append("")

    return "\n".join(out)


# -- the command line ---------------------------------------------------------


def _parse_seeds(text: str) -> list[int]:
    """``"1-3,7,10-12"`` -> ``[1, 2, 3, 7, 10, 11, 12]``, sorted and unique.

    A single seed and a single range are both valid inputs, matching the
    usage line in this module's docstring.
    """
    seeds: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part[1:]:
            head, _, tail = part.partition("-")
            lo, hi = int(head), int(tail)
            if hi < lo:
                raise ValidationError(
                    f"seed range {part!r} ends before it starts")
            seeds.update(range(lo, hi + 1))
        else:
            seeds.add(int(part))
    if not seeds:
        raise ValidationError(f"no seeds parsed from {text!r}")
    return sorted(seeds)


def _run_batch(seeds: Sequence[int], run_one: "Callable[[int], Written]",
               *, workers: int) -> list[Written]:
    """Run ``run_one`` over ``seeds``, at most ``workers`` in flight at once.

    The bounded-deque pattern `python/tradefloor/sweep.py` documents and
    uses: a plain ``ThreadPoolExecutor.map`` over every seed would submit
    all of them before the caller saw the first result, holding every
    finished :class:`Written` (and the tables one call to :func:`export`
    builds to write) until the last one completes. This keeps at most
    ``workers`` calls to :func:`export` alive at once, so peak memory
    tracks ``workers`` rather than ``len(seeds)`` -- the same trade the
    docstring on :func:`~tradefloor.sweep` names for the same reason.
    Results come back in seed order.
    """
    if workers < 1:
        raise ValidationError(f"workers must be at least 1, got {workers}")
    if workers == 1:
        return [run_one(seed) for seed in seeds]

    results: list[Written | None] = [None] * len(seeds)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending: deque = deque()
        upcoming = iter(enumerate(seeds))
        for index, seed in upcoming:
            pending.append((index, pool.submit(run_one, seed)))
            if len(pending) == workers:
                break
        while pending:
            index, future = pending.popleft()
            results[index] = future.result()
            nxt = next(upcoming, None)
            if nxt is not None:
                next_index, next_seed = nxt
                pending.append((next_index, pool.submit(run_one, next_seed)))
    assert all(r is not None for r in results)
    return results  # type: ignore[return-value]


def _first_paragraph(doc: str) -> str:
    """The module docstring's opening paragraph, one line for argparse.

    ``__doc__.splitlines()[0]`` would cut the sentence at the first line
    break instead of the first blank line, and this docstring's own
    opening sentence wraps onto a second physical line -- exactly the
    truncation this avoids.
    """
    paragraph = doc.strip().split("\n\n", 1)[0]
    return " ".join(paragraph.split())


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=_first_paragraph(__doc__))
    ap.add_argument("--preset", default=envelope.PRESET,
                    help=f"model preset name (default {envelope.PRESET!r})")
    ap.add_argument("--seeds", required=True,
                    help="a seed, a range (1-50), or a comma list of "
                         "either")
    ap.add_argument("--names", type=int, required=True,
                    help="roster size")
    ap.add_argument("--roster-seed", type=int, required=True,
                    help="seed for Universe.random")
    ap.add_argument("--days", type=int, required=True)
    ap.add_argument("--scenario", default="none",
                    help="'none', or a path to a scenario YAML file")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args(argv)

    seeds = _parse_seeds(args.seeds)
    universe = tf.Universe.random(args.names, seed=args.roster_seed)
    scenario = (None if args.scenario == "none"
               else tf.Scenario.from_yaml(args.scenario))
    out = args.out

    def run_one(seed: int) -> Written:
        return export(seed, universe=universe, days=args.days,
                     scenario=scenario, model=args.preset, out=out)

    invocation = {
        "preset": args.preset, "seeds": args.seeds, "roster_seed":
        args.roster_seed, "scenario": args.scenario,
        "workers": args.workers, "out": str(out),
    }
    written = _run_batch(seeds, run_one, workers=args.workers)
    for w in written:
        w.card["invocation"] = invocation
        print(f"seed {w.seed}: wrote {len(w.files)} files under "
             f"{out / str(w.seed)}", flush=True)

    card_path = out / "card.md"
    _write_text(card_path, card(written))
    print(f"wrote {card_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
