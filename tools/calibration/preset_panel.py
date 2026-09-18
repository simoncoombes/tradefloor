"""Measure EVERY shipped preset on ONE ruler, so they can be ranked.

Why this exists. The site's preset table is twelve rows of prose, and each
row quotes the panel count that was current when that preset shipped: pt-v5
says "9 of 10", pt-v7 says "twelve of thirteen", pt-v10 says "all fourteen".
Those are three different rulers. The panel grew from ten statistics to
thirteen to fourteen, so a reader comparing the rows is comparing counts that
do not mean the same thing, and a table that ranks them would rank the
rulers rather than the presets.

This measures all twelve on the fourteen-statistic panel, at thirty seeds,
on the certified roster, at both horizons, on a universe none of them was
tuned on, and under a held crisis. One method, one ruler, one run.

Every number the ranking table publishes comes from here. Nothing is carried
forward from a calibration record, because that is how the current
table drifted.

The method, spelled out because "check which one you are reading" is a
standing warning on the realism page:

  panel_252     Universe.random(40, seed=111), 252 days, seeds 101-130,
                scored against facts.REAL_MARKETS.
  panel_504     the same roster and seeds at 504 days, scored against
                facts.REAL_MARKETS_504 -- the horizon-matched ruler. Scoring
                a 504-day run against the 252-day bands is trap 2 of the
                runbook and the scales differ by 0.8x to 3.2x.
  heldout_universe
                Universe.random(60, seed=909), 252 days, seeds 101-130. A
                roster no preset was calibrated on.
  heldout_seeds Universe.random(40, seed=111), 252 days, seeds 1-30. THIRTY,
                against the six gate_pick screens on: trap 15, where a six-seed
                read called pt-v10 13/14 and thirty called it 14/14 because
                corr_persistence_acf1 has an across-seed sd of 0.28.

Both 252-day cells now keep their PER-SEED panels and carry a mechanism
certificate beside the band count (`envelope.certify`). The band count answers
"could a real year read this" and cannot answer "is a model without the
mechanism excluded" -- on five of the fourteen rows the band contains the
mechanism-absent reading outright -- and the second question needs the
per-seed readings rather than their median, so the aggregation that used to
happen here threw away the only thing that could answer it. The two cells
differ in which seeds they draw, so a mechanism verdict that holds on one and
not the other is trap 15 in the second count.
  crisis_lever  annualised volatility under a held VIX 65 divided by the same
                under a held VIX 5, on the CERTIFIED roster over 252 days at
                thirty seeds, AFTER a 252-session burn at the pin. This is
                deliberately NOT scenario_response's held-VIX half, which
                pins 120 days on a 20-name roster and answers a different
                question with a similar-looking number.

                The burn arrived on 2026-09-14 and it is the eighth
                measurement defect of `programme/results/measurement-integrity.md`
                closed. Every `crisis_lever` block committed before that
                date was read from a cold open, is low by four to eight per
                cent, and is low by a DIFFERENT amount on each preset --
                see `LEVER_BURN`. Those blocks are not corrected in place;
                they will move the next time a preset is re-recorded, and
                the artefact now carries `crisis_lever_burn` so the two
                generations can be told apart.

Usage:
    python tools/calibration/preset_panel.py --workers 190 \\
        --out /home/ec2-user/out/preset-panel.json
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import pathlib
import statistics
import sys
import time

import tradefloor
import tradefloor.facts as facts
from tradefloor import Scenario, envelope

#: The published method. Both are what `tradefloor.envelope` certifies against.
ROSTER_N, ROSTER_SEED = 40, 111
HELDOUT_N, HELDOUT_SEED = 60, 909

#: Thirty, everywhere. See the module docstring on trap 15.
TRAIN_SEEDS = tuple(range(101, 131))
HELDOUT_SEEDS = tuple(range(1, 31))

#: The fourteen. Taken from `envelope.CERTIFIED` rather than written out, so
#: a fifteenth statistic joins this table by being added to the envelope.
PANEL = tuple(envelope.CERTIFIED)

#: Rows this tool MEASURES AND REPORTS WITHOUT GRADING, carried BESIDE the
#: panel and never inside it.
#:
#: `facts.measure` computes `vix_ar1_debiased` on every seed of every run --
#: `persistence_statistics` at `facts.py:3925`, which is
#: `debias_ar1(level_ar1(vix_levels(macro_table())), days)` -- and this
#: collector threw it away, because `_job` trimmed every panel to `PANEL`.
#: The row was already paid for in engine time and discarded at the seam.
#:
#: WHY IT IS NOT IN `PANEL`, and this was checked rather than assumed.
#: `PANEL` is `envelope.CERTIFIED` and three separate things hold it there:
#:
#:   1. `tests/test_preset_records.py` asserts `sorted(preset_panel.PANEL)
#:      == sorted(envelope.CERTIFIED)` outright. Widening `PANEL` fails that
#:      test, which is the project saying the panel IS the certified roster.
#:   2. `_count_in_band` iterates `PANEL`, and this row has NO band on any
#:      of the three bases -- `shipped`, `universal` and `ruled` all lack it
#:      (`facts.REAL_MARKETS`, `facts.REAL_MARKETS_UNIVERSAL`,
#:      `facts.REAL_MARKETS_RULED` and their 504 partners). It would land in
#:      `unreadable_252`, `unreadable_504`, `unreadable_heldout_universe`
#:      and `unreadable_heldout_seeds` on every preset and every basis, so
#:      four graded list fields per preset would change value.
#:   3. `record.py` copies `panel_252` and `panel_504` verbatim into the
#:      committed record, and `test_preset_records.py` asserts
#:      `set(record["panel_252"]) == set(envelope.CERTIFIED)`. A fifteenth
#:      key in the median panel makes all eighteen records need regenerating
#:      to stay green, which is a restamp and a different change.
#:
#: So the row is carried in its own `reported` block. Nothing here is read
#: by `_count_in_band`, by `envelope.certify` (which selects
#: `facts.SHAPE`), by `_median_panel`, or by `record.py`. It is additive at
#: every seam.
REPORTED = (facts.VIX_AR1_ROW,)

#: WHY THE REPORTED ROW CARRIES NO BAND COUNT, said in the artefact rather
#: than left for a reader to infer from an absent field.
#:
#: `vix_ar1_debiased` is UNGRADED here and it is ungraded in the library
#: too: `facts.RULED_UNREADABLE` holds it at both horizons with the reason
#: that its band is DERIVED AND NOT ADOPTED -- `vix-ar1-band-derivation.md`
#: section 9 names three rulings that have to land first -- and no band
#: table this tool can grade against carries the row at all.
#:
#: `facts.BAND_EDGE_LIVENESS` records, separately, that the row's CEILING is
#: dead: `debias_ar1` is bounded above by `1 + 4/n`, so at 252 no reading
#: can reach the derived ceiling. That is the library's statement and it is
#: reproduced into the artefact from `facts.edge_liveness`, not restated
#: here, because a band and a one-sided rule are rulings and not fields.
#:
#: What the row DOES have is a tape ruler: `facts.REAL_VIX_AR1_WINDOWS`,
#: the real windowed readings at each horizon. Those are carried beside the
#: model's readings so a reader can compare, and no verdict is computed
#: from the comparison.
REPORTED_NOT_GRADED = (
    "vix_ar1_debiased is MEASURED AND REPORTED HERE, NOT GRADED. It is not "
    "in envelope.CERTIFIED, so it is not in PANEL, so it is in no in_band "
    "count, no misses list, no unreadable list, no mechanism block and no "
    "preset record. No band table this tool grades against carries the row "
    "-- facts.REAL_MARKETS, facts.REAL_MARKETS_UNIVERSAL and "
    "facts.REAL_MARKETS_RULED all lack it at both horizons -- and "
    "facts.RULED_UNREADABLE records why: the band is derived and not "
    "adopted, pending the three rulings named in "
    "vix-ar1-band-derivation.md section 9. THERE IS NO BAND FOR THIS ROW "
    "AND NONE IS INVENTED HERE. The block carries the real tape's windowed "
    "readings (facts.REAL_VIX_AR1_WINDOWS) so the model's reading can be "
    "compared by eye, and the row's edge liveness "
    "(facts.BAND_EDGE_LIVENESS), which records that the ceiling is dead by "
    "debias_ar1's own 1 + 4/n bound. Neither is a verdict."
)

#: WHY THIS TOOL EMITS NO INDEX TAIL FIGURE, deliberately and by ruling.
#:
#: `index_tail_dn3_pct` is certified on `facts.LEVEL_PROTOCOL`, where the
#: ROSTER varies with the seed, because a crash rate is a property of the
#: roster's concentration as much as of the model. This tool runs the held
#: roster, `Universe.random(40, seed=111)`. The two protocols read
#: measurably different numbers on the same preset: pt-v16 reads 1.2749
#: percent with the roster varying against 1.124 held, a gap of 13.4
#: percent.
#:
#: So a tail figure measured here would be a held-roster number sitting in
#: a preset record under the same row name as the varying-roster number in
#: `envelope.CERTIFIED_CRISIS`, and somebody would compare them. Annotating
#: it would not prevent that: this project's three worst ruler errors were
#: all labelled somewhere and compared anyway -- the real VIX AR1 documented
#: as a whole-span estimate and scored against 252-day windows,
#: `crisisprobe-frontier`'s band declaring itself CHOSEN in its own source
#: and still producing a charter verdict, and `REAL_TAIL3 = 107/9236`
#: carrying its provenance in `facts.py` while eight scripts divided GSPC
#: hits by a VIX session count.
#:
#: ABSENT WITH A REASON is the honest state. The per-seed panels therefore
#: do not carry the row's counts, `envelope.certify` finds nothing to build
#: a tail block from, and `certification_record` carries `tail: None`. This
#: string travels in the artefact's `method` so a reader of a record learns
#: why the field is empty and what would fill it.
TAIL_NOT_MEASURED = (
    "index_tail_dn3_pct is NOT measured by this tool. It is certified on "
    "facts.LEVEL_PROTOCOL, where the roster varies with the seed, and this "
    "tool holds Universe.random(40, seed=111); the two protocols differ by "
    "13.4 percent on pt-v16 (1.2749 varying against 1.124 "
    "held). A held-roster figure under this row's name would be compared "
    "with the certified one, so none is emitted. What would fill it: a "
    "level-protocol arm in this tool, which is a box job and a Phase 1 "
    "follow-on rather than part of the row's own branch."
)

#: The crisis lever's two endpoints, and the real-market figure it is read
#: against (17.2% annualised below VIX 12 against 106.1% above VIX 45, from
#: `real_vix_lever.py`; the ratio is 6.16).
LEVER_LO, LEVER_HI = 5.0, 65.0
REAL_LEVER = 6.16

#: Sessions traded and discarded before the lever's window, and this is the
#: EIGHTH measurement defect of `measurement-integrity.md`, fixed.
#:
#: `Scenario().hold(vix=)` pins the VIX from day zero. The factor variance
#: does not start there: it opens at the preset's unconditional level and
#: relaxes toward the pinned target at the preset's own
#: `alpha + beta + gamma/2`. The opening level is ABOVE the target at VIX 5
#: and BELOW it at VIX 65, so the two transients run in OPPOSITE directions
#: and their ratio -- the lever -- is biased low; and because the relaxation
#: rate is a coefficient, the bias differs BETWEEN PRESETS, which is the
#: part that makes a lever table unreadable rather than merely low.
#:
#: MEASURED on the box `metalever2`, thirty seeds, one 504-day pinned run
#: per seed and pin, read on year one and year two: the low pin settles DOWN
#: (pt-v18 -2.84 +/- 1.64 at VIX 5) and the high pin UP (+6.12 +/- 3.88 at
#: VIX 65), and the lever moves 6.5258 -> 7.0581 on pt-v18 (+8.2 per cent)
#: and 2.7981 -> 2.9157 on the then-pt-v19 (+4.2 per cent). The ORDERING
#: survived, so nothing on the record flipped; the levels were all low.
#:
#: 252 and not some rounder number because 252 is what was measured: year
#: two of that run is the settled reading, and `facts.measure(days=252,
#: burn=252)` reproduces it to the bit, the burn being the same traded
#: sessions with the recorder switched off. The real figure the lever is
#: read against (6.16, `real_vix_lever.py`) is a regime-conditional STEADY
#: STATE, so a settled window is also the only like-for-like comparison.
LEVER_BURN = 252


def _commit() -> str | None:
    """The checkout this measurement ran in, or None outside a checkout."""
    import subprocess
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def presets() -> list[str]:
    """Every shipped preset, READ from the engine rather than probed for.

    This used to probe `pt-v1`, `pt-v2`, ... and stop at the first name that
    did not resolve. `pt-v17` does not exist -- the recomposition era
    reserves the number -- so the probe stopped at sixteen on a build that
    ships seventeen, and a commissioned run measured every preset EXCEPT
    `pt-v18`, the one it was commissioned for. It cost a box and it printed
    nothing wrong on the way: "16 presets, 2880 measurements" is what a
    correct run of a sixteen-preset build looks like.

    A guessed list fails quietly and a read list cannot, so this reads the
    same list the engine's own error messages are built from.
    """
    return list(tradefloor.preset_names())


def _roster(n: int, seed: int):
    return tradefloor.Universe.random(n, seed=seed)


def _job(spec):
    """One measurement in a worker. Returns (key, preset, seed, panel)."""
    key, preset, seed = spec
    if key == "panel_252":
        p = facts.measure(seed=seed, universe=_roster(ROSTER_N, ROSTER_SEED),
                          days=252, model=preset)
    elif key == "panel_504":
        p = facts.measure(seed=seed, universe=_roster(ROSTER_N, ROSTER_SEED),
                          days=504, model=preset)
    elif key == "heldout_universe":
        p = facts.measure(seed=seed, universe=_roster(HELDOUT_N, HELDOUT_SEED),
                          days=252, model=preset)
    elif key == "heldout_seeds":
        p = facts.measure(seed=seed, universe=_roster(ROSTER_N, ROSTER_SEED),
                          days=252, model=preset)
    elif key in ("lever_lo", "lever_hi"):
        vix = LEVER_LO if key == "lever_lo" else LEVER_HI
        p = facts.measure(seed=seed, universe=_roster(ROSTER_N, ROSTER_SEED),
                          days=252, burn=LEVER_BURN, model=preset,
                          scenario=Scenario().hold(vix=vix))
    else:
        raise ValueError(key)
    # `PANEL` is the certified row list; `days` and `burn` are the window
    # the rows were read over, and they travel with every panel because a
    # row without its window is not comparable with the same row measured
    # over a different one. See `crisis_lever_burn` below.
    # `PANEL` is the certified row list and `REPORTED` the measured-but-
    # ungraded one, kept apart on purpose: everything that grades, counts or
    # records reads `PANEL`, so a reported row cannot reach a verdict by
    # being in the same dict. `.get` on the reported rows, because a row the
    # engine did not emit on this run must arrive as None rather than as a
    # KeyError that kills a 190-worker pool.
    return key, preset, seed, {k: p[k] for k in PANEL} | {
        k: p.get(k) for k in REPORTED} | {
        "days": p["days"], "burn": p["burn"]}


def _median_panel(rows: list[dict]) -> dict:
    # Each row by its own estimator: medians for the shape rows and a mean
    # for the level row, which is what the band's width was set against.
    # Only the certified rows, so the shape of a `panel_252` block in a
    # committed record is exactly the shape `record.py` has always read.
    # The WINDOW is reported separately by `_window_of`.
    return {k: facts.aggregate_value(k, [r[k] for r in rows if r.get(k) is not None])
            for k in PANEL if any(r.get(k) is not None for r in rows)}


def _reported_panel(rows: list[dict]) -> dict:
    """The median of the REPORTED rows for one cell, by the row's own estimator.

    Deliberately a second function rather than a wider `_median_panel`.
    `_median_panel` builds `panel_252` and `panel_504`, which `record.py`
    copies verbatim into a committed record and `test_preset_records.py`
    checks key-for-key against `envelope.CERTIFIED`; widening it by one key
    would make all eighteen committed records need regenerating. This
    output goes to a field of its own that nothing grades.
    """
    return {k: facts.aggregate_value(k, [r[k] for r in rows
                                         if r.get(k) is not None])
            for k in REPORTED if any(r.get(k) is not None for r in rows)}


def _reported_block(cells: dict[str, list[dict]], tables: dict[int, dict]) -> dict:
    """The ungraded rows, their real-tape ruler, and the fact that they have
    no band, in ONE field that no count reads.

    `tables` is the `{252: t252, 504: t504}` this run grades against, and it
    is consulted so the artefact ASSERTS the absence rather than assuming
    it: if a basis ever does carry a band for a reported row, the block says
    so instead of silently claiming there is none.
    """
    out: dict = {
        "rows": list(REPORTED),
        "not_graded": REPORTED_NOT_GRADED,
        "cells": {name: _reported_panel(rows) for name, rows in cells.items()},
        "per_seed": "carried in per_seed_252 and per_seed_heldout_seeds, "
                    "under the row's own key, beside the certified rows",
        "band": {},
        "real_tape": {},
        "edge_liveness": {},
    }
    for row in REPORTED:
        # THE ABSENCE, PER HORIZON, READ FROM THE TABLES THIS RUN USED.
        out["band"][row] = {
            str(days): (list(t[row]) if row in t else None)
            for days, t in sorted(tables.items())
        }
        out["band"][row]["reason"] = {
            str(days): facts.RULED_UNREADABLE.get(days, {}).get(row)
            for days in sorted(tables)
        }
        windows = facts.REAL_VIX_AR1_WINDOWS if row == facts.VIX_AR1_ROW else {}
        out["real_tape"][row] = {
            str(days): {
                "n_windows": len(vals),
                "median": statistics.median(vals),
                "min": min(vals), "max": max(vals),
            } for days, vals in sorted(windows.items())
        }
        out["edge_liveness"][row] = facts.edge_liveness(row)
    return out


def _window_of(rows: list[dict]) -> tuple[int, int]:
    """The `(days, burn)` every seed of a cell ran, or a refusal.

    Carried, never aggregated. An aggregate over runs of two different
    lengths -- or over a settled and a cold-open reading -- is a number with
    no protocol, and the eighth measurement defect is what happens when a
    figure like that is shipped without its window written down beside it.
    """
    windows = {(int(r["days"]), int(r["burn"])) for r in rows}
    if len(windows) != 1:
        raise SystemExit(
            f"REFUSED: these {len(rows)} per-seed panels carry "
            f"{sorted(windows)} for (days, burn). A median across windows "
            f"is not a measurement of any of them")
    return windows.pop()


def _count_in_band(panel: dict, bands: dict) -> tuple[int, list[str], list[str]]:
    """How many sit inside their band, which do not, and which have none.

    THE THIRD LIST IS THE ONE THAT MATTERS AND IT USED TO BE A `KeyError`.
    A basis does not carry every row: `facts.REAL_MARKETS_RULED_504` holds
    thirteen of `PANEL`'s fourteen, because `corr_persistence_acf1` is held
    out of the ruled band at 504 on two recorded grounds
    (`facts.RULED_UNREADABLE[504]`). A row the basis cannot read is named
    here and taken OUT of the denominator, so `in_band` plus `misses` plus
    `unreadable` is the panel and a count of "13" is never printed against a
    fourteen that was not tested.
    """
    misses, unreadable = [], []
    for k in PANEL:
        band = bands.get(k)
        if band is None:
            unreadable.append(k)
            continue
        lo, hi = band
        if not (lo <= panel[k] <= hi):
            misses.append(k)
    return len(PANEL) - len(misses) - len(unreadable), misses, unreadable


#: The band tables this tool will grade against, by basis name. The seam is
#: FOUR call sites and this dict is all four of them: `--band-basis` picks a
#: row and every `_count_in_band` reads it.
#:
#: The key is the basis's own short name and the value is the pair of table
#: symbols, which `facts.band_basis` resolves to an era, a window count and a
#: rule. Nothing here is a band; a band is a thing with a provenance and the
#: point of this dict is that the provenance travels with it.
#:
#: `ruled` IS THE BASIS THE RULING NAMES and `universal` is its component.
#: `ruling-the-ruler-is-the-universal-band` makes the bar every row against
#: the universal whole-tape band; `facts.REAL_MARKETS_RULED` is that table
#: composed with the two whole-record rows, and it is what
#: `envelope.RULERS_BY_BASIS['ruled']` returns, so grading here and grading
#: in the library read the same object.
#:
#: The two differ on `PANEL` at 504 and only there. `universal` grades
#: `corr_persistence_acf1` against (-0.38, 0.88), a band carried on the
#: WALKED six-window protocol rather than the shipped sub-window one, so it
#: is a band for a different quantity; `ruled` drops the row and
#: `_count_in_band` names it unreadable. A count taken on `universal` at 504
#: therefore includes one cell the ruled band refuses to grade.
BAND_BASES = {
    "shipped": ("facts.REAL_MARKETS", "facts.REAL_MARKETS_504"),
    "universal": ("facts.REAL_MARKETS_UNIVERSAL",
                  "facts.REAL_MARKETS_UNIVERSAL_504"),
    "ruled": ("facts.REAL_MARKETS_RULED", "facts.REAL_MARKETS_RULED_504"),
}


def _tables(basis: str) -> tuple[dict, dict, dict]:
    """The (252, 504, method-stamp) triple for a band basis.

    The stamp is the BASIS and not the symbol. A record that says
    `"bands_252": "facts.REAL_MARKETS"` records a name: swap that dict's
    contents and the record still asserts the same provenance while every
    count under it changes. That is how today's defect stayed invisible, and
    it is why the era, the window count and the rule go in beside the name.
    """
    if basis not in BAND_BASES:
        raise SystemExit(
            f"REFUSED: {basis!r} is not a band basis this tool knows; the "
            f"bases are {sorted(BAND_BASES)}")
    name252, name504 = BAND_BASES[basis]
    t252 = getattr(facts, name252.split(".", 1)[1])
    t504 = getattr(facts, name504.split(".", 1)[1])
    stamp = {}
    for field, name in (("bands_252", name252), ("bands_504", name504)):
        b = facts.band_basis(name)
        stamp[field] = name
        stamp[field + "_basis"] = {
            "era": b["era"], "roster": b["roster"],
            "n_windows": b["n_windows"], "rule": b["rule"],
            "tolerance": b["tolerance"], "rows": b["rows"],
        }
    stamp["band_basis"] = basis
    return t252, t504, stamp


def rescore(artefact: str, basis: str, out: str, records_dir: str) -> int:
    """Re-score a retained artefact against a different band basis.

    Desk cost. It re-reads the panels the measuring run already retained and
    re-runs only the band-derived half -- the four `_count_in_band` calls and
    the basis stamp. It re-measures nothing, so no draw schedule is touched
    and the known-answer digest cannot move.

    IT REFUSES A PRESET WHOSE ARTEFACT IS NOT THE ONE ITS RECORD WAS BUILT
    FROM, and that refusal is the point rather than an inconvenience. Re-
    scoring `pt-v19` out of a panel measured on a different `pt-v19` would
    write a record whose counts and whose panel came from two vectors, which
    is defect-26 with the arrow reversed. The check is exact: the artefact's
    panel must reproduce the committed record's own `panel_252` and
    `panel_504` blocks to the bit.
    """
    doc = json.loads(pathlib.Path(artefact).read_text(encoding="utf-8"))
    t252, t504, stamp = _tables(basis)
    ship252, ship504, _ = _tables("shipped")
    refused, done = [], []
    for name in sorted(doc["presets"]):
        cell = doc["presets"][name]
        rec_path = pathlib.Path(records_dir) / f"{name}.json"
        if not rec_path.exists():
            refused.append(f"{name}: no committed record at {rec_path}")
            continue
        rec = json.loads(rec_path.read_text(encoding="utf-8"))
        drift = [k for k in PANEL
                 if cell["panel_252"][k] != rec["panel_252"][k]
                 or cell["panel_504"][k] != rec["panel_504"][k]]
        if drift:
            # The artefact is a DIFFERENT measurement of this preset's name.
            # Its panels are refused; the record's own retained panel blocks
            # are the only copy of the measurement the record publishes, so
            # the 252 and 504 cells are re-scored off those and the cells
            # that need per-seed rows are marked unrescorable rather than
            # filled from the wrong run.
            refused.append(
                f"{name}: this artefact's panel is not the one the committed "
                f"record was built from -- {len(drift)} of {len(PANEL)} rows "
                f"differ, worst "
                + max(((abs(cell['panel_252'][k] - rec['panel_252'][k]), k)
                       for k in drift))[1]
                + ". The record's own panel blocks were used for 252 and 504; "
                  "the held-out cells have no retained per-seed rows on this "
                  "preset's own measurement and are left unscored")
            cell = dict(cell)
            cell["panel_252"] = dict(rec["panel_252"])
            cell["panel_504"] = dict(rec["panel_504"])
            cell["per_seed_252"] = None
            cell["per_seed_heldout_seeds"] = None
            # Same status as the per-seed rows: the reported block is this
            # ARTEFACT's measurement of a preset the record was not built
            # from, so it is dropped rather than carried under a record it
            # does not belong to.
            cell["reported"] = None
            cell["panel_source"] = (
                f"the committed record {rec_path.name}, not {artefact}: no "
                f"retained preset-panel artefact carries the measurement this "
                f"record publishes")
            doc["presets"][name] = cell
        hos = (_median_panel(cell["per_seed_heldout_seeds"])
               if cell.get("per_seed_heldout_seeds") else None)
        hou = cell.get("panel_heldout_universe")
        (cell["in_band_252"], cell["misses_252"],
         cell["unreadable_252"]) = _count_in_band(cell["panel_252"], t252)
        (cell["in_band_504"], cell["misses_504"],
         cell["unreadable_504"]) = _count_in_band(cell["panel_504"], t504)
        if hos is None:
            cell["in_band_heldout_seeds"] = None
            cell["misses_heldout_seeds"] = None
            cell["unreadable_heldout_seeds"] = None
        else:
            (cell["in_band_heldout_seeds"], cell["misses_heldout_seeds"],
             cell["unreadable_heldout_seeds"]) = _count_in_band(hos, t252)
        if hou is None:
            # The measuring run kept the count and threw the panel away, so
            # this cell cannot be re-scored and must not be carried forward
            # under a basis it was not read at. Stated, not guessed.
            cell["in_band_heldout_universe"] = None
            cell["misses_heldout_universe"] = None
            cell["unreadable_heldout_universe"] = None
            cell["heldout_universe_not_rescorable"] = (
                "the retained artefact carries this cell's COUNT and not its "
                "panel, so a re-score has nothing to read. What would fix it: "
                "retain panel_heldout_universe the way panel_252 is retained")
        else:
            (cell["in_band_heldout_universe"],
             cell["misses_heldout_universe"],
             cell["unreadable_heldout_universe"]) = _count_in_band(hou, t252)
        done.append(name)
    doc["method"] = dict(doc.get("method", {})) | stamp
    doc["rescored_from"] = {
        "artefact": str(artefact),
        "was": {k: v for k, v in _tables("shipped")[2].items()},
        "refused": refused,
        "note": "band-derived fields only. The panels, the per-seed rows, "
                "the mechanism blocks and the lever are the measuring run's "
                "and are unchanged; nothing here re-measures anything",
    }
    pathlib.Path(out).write_text(json.dumps(doc, indent=1), encoding="utf-8")
    print(f"re-scored {len(done)} of {len(doc['presets'])} presets against "
          f"{basis}: {stamp['bands_252']} / {stamp['bands_504']}")
    for r in refused:
        print(f"  REFUSED {r}")
    print(f"wrote {out}")
    return 1 if refused else 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", help="comma-separated presets, for a smoke test")
    ap.add_argument("--seeds", type=int,
                    help="cap the seed count. FOR SMOKE TESTS ONLY: every "
                         "published count here is a thirty-seed median, and "
                         "a count without its seed count is trap 15.")
    # THE LIBRARY'S NAME, NOT A SECOND DEFAULT. This read the literal
    # "shipped", so on 2026-09-15 the library graded against the ruled band
    # and this tool still wrote decade-band counts into artefacts that stamp
    # a basis, which is one storey down from the defect the stamp exists for.
    # Reading `facts.DEFAULT_BAND_BASIS` means the tool follows the ruling
    # rather than restating it. It changes what a NEW measurement writes and
    # nothing already on disk: `record.py` reads an artefact's counts, not
    # these tables, so the eighteen committed records are untouched until
    # somebody re-measures on purpose.
    ap.add_argument("--band-basis", default=facts.DEFAULT_BAND_BASIS,
                    choices=sorted(BAND_BASES),
                    help="which band basis to grade against. The default is "
                         f"{facts.DEFAULT_BAND_BASIS!r}, the band "
                         "ruling-the-ruler-is-the-universal-band names; "
                         "`shipped` is the 2015-2025 decade band the "
                         "committed records were taken at, and `universal` "
                         "is the shape-row component of the ruled table "
                         "without its two whole-record rows")
    ap.add_argument("--rescore", metavar="ARTEFACT",
                    help="re-score a retained preset-panel artefact against "
                         "--band-basis and write it to --out. Desk cost: it "
                         "re-reads panels and re-runs only the band half, so "
                         "no draw schedule is touched")
    ap.add_argument("--records", default=str(
        pathlib.Path(__file__).resolve().parents[2]
        / "python" / "tradefloor" / "presets"),
        help="where the committed records live, which --rescore checks its "
             "artefact against before re-scoring a preset")
    args = ap.parse_args()

    if args.rescore:
        sys.exit(rescore(args.rescore, args.band_basis, args.out,
                         args.records))
    t252, t504, band_stamp = _tables(args.band_basis)

    names = args.only.split(",") if args.only else presets()
    train = TRAIN_SEEDS[:args.seeds] if args.seeds else TRAIN_SEEDS
    heldout = HELDOUT_SEEDS[:args.seeds] if args.seeds else HELDOUT_SEEDS
    started = time.time()

    specs = []
    for preset in names:
        for seed in train:
            specs += [("panel_252", preset, seed),
                      ("panel_504", preset, seed),
                      ("heldout_universe", preset, seed),
                      ("lever_lo", preset, seed),
                      ("lever_hi", preset, seed)]
        for seed in heldout:
            specs.append(("heldout_seeds", preset, seed))

    print(f"{len(names)} presets, {len(specs)} measurements, "
          f"{args.workers} workers", flush=True)

    # A pool, and therefore a __main__ guard at the bottom of this file:
    # macOS spawns rather than forks, so a module-level pool re-imports the
    # module in every worker and forks bombs. Trap 5 of the runbook.
    # KEYED BY SEED, not appended in completion order. imap_unordered yields
    # as workers finish, so a bare append makes per_seed_* row order a race:
    # two runs of the SAME build put the held-out rows in different orders
    # (measured 2026-09-16, 18 of 30 rows at the same position over six
    # adjacent swaps), and anything pairing two artefacts BY POSITION is then
    # pairing different seeds. The medians this file reports never cared --
    # a median is order-invariant -- which is why it went unnoticed.
    collected_by_seed: dict[tuple[str, str], dict[int, dict]] = {}
    done = 0
    with mp.Pool(args.workers) as pool:
        for key, preset, seed, panel in pool.imap_unordered(_job, specs,
                                                            chunksize=1):
            collected_by_seed.setdefault((key, preset), {})[seed] = panel
            done += 1
            if done % 100 == 0:
                rate = done / max(time.time() - started, 1e-9)
                print(f"  {done}/{len(specs)}  {rate:.1f}/s  "
                      f"eta {(len(specs) - done) / max(rate, 1e-9) / 60:.1f}m",
                      flush=True)

    # Sorted by seed, so the row order is a property of the run's seed list
    # and not of how the pool happened to schedule it.
    collected: dict[tuple[str, str], list[dict]] = {
        key: [panel for _, panel in sorted(by_seed.items())]
        for key, by_seed in collected_by_seed.items()
    }

    results = {}
    for preset in names:
        p252 = _median_panel(collected[("panel_252", preset)])
        p504 = _median_panel(collected[("panel_504", preset)])
        phou = _median_panel(collected[("heldout_universe", preset)])
        phos = _median_panel(collected[("heldout_seeds", preset)])
        lo = _median_panel(collected[("lever_lo", preset)])
        hi = _median_panel(collected[("lever_hi", preset)])
        lever_days, lever_burn = _window_of(collected[("lever_lo", preset)])
        if (lever_days, lever_burn) != _window_of(collected[("lever_hi", preset)]):
            raise SystemExit(
                "REFUSED: the lever's two pins ran different windows, so "
                "their ratio is not a lever")

        # The mechanism certificate, from the per-seed panels rather than
        # from their median. Both 252-day cells, because the count that
        # matters is per protocol and not per preset.
        # Whether every run of this preset opens at phase age zero, read off
        # the preset rather than assumed. It decides nothing here today,
        # because these panels carry no tail counts and the tail block comes
        # back None (`TAIL_NOT_MEASURED`); it is passed so that the day this
        # tool grows a level-protocol arm, the block it produces is counted
        # or not counted on the run's own opening rather than on a default.
        stationary = bool(tradefloor.ModelParams.from_preset(preset)
                          .to_dict().get("cycle_stationary_opening", 0.0))
        cert252 = envelope.certify(collected[("panel_252", preset)],
                                   stationary_opening=stationary)
        certhos = envelope.certify(collected[("heldout_seeds", preset)],
                                   stationary_opening=stationary)

        # The measured-but-ungraded rows, from the same per-seed panels the
        # graded ones come from. Built BEFORE the band counts and used by
        # none of them: nothing below this line reads `reported`.
        reported = _reported_block({
            "panel_252": collected[("panel_252", preset)],
            "panel_504": collected[("panel_504", preset)],
            "heldout_universe": collected[("heldout_universe", preset)],
            "heldout_seeds": collected[("heldout_seeds", preset)],
        }, {252: t252, 504: t504})

        n252, miss252, unr252 = _count_in_band(p252, t252)
        n504, miss504, unr504 = _count_in_band(p504, t504)
        nhou, misshou, unrhou = _count_in_band(phou, t252)
        nhos, misshos, unrhos = _count_in_band(phos, t252)

        results[preset] = {
            "panel_252": p252,
            "panel_504": p504,
            # Retained for the same reason `panel_252` is, and it was the one
            # cell that was not. Every artefact written before 2026-09-14 kept
            # `in_band_heldout_universe` -- a COUNT read at whatever band the
            # run happened to grade against -- and threw the panel the count
            # came from away, so that cell cannot be re-scored at any other
            # basis: 252 of the 1,008 shape-row band verdicts on the eighteen
            # committed records are unreachable from anything retained, and
            # `--rescore` has to write null there rather than carry a
            # decade-band count forward under a record stamped otherwise.
            # This line does not repair the artefacts already written; it
            # stops the next one from having the same hole.
            "panel_heldout_universe": phou,
            "in_band_252": n252, "misses_252": miss252,
            "in_band_504": n504, "misses_504": miss504,
            "in_band_heldout_universe": nhou, "misses_heldout_universe": misshou,
            "in_band_heldout_seeds": nhos, "misses_heldout_seeds": misshos,
            # The rows this basis could not read, named beside the count
            # rather than folded into it. Empty on `shipped` and `universal`;
            # `ruled` names `corr_persistence_acf1` at 504.
            "unreadable_252": unr252, "unreadable_504": unr504,
            "unreadable_heldout_universe": unrhou,
            "unreadable_heldout_seeds": unrhos,
            "mechanism_252": envelope.certification_record(cert252),
            "mechanism_heldout_seeds": envelope.certification_record(certhos),
            # MEASURED AND REPORTED, NOT GRADED. `facts.measure` computes
            # `vix_ar1_debiased` on every seed of every run and this
            # collector used to trim it away at `_job`. It is retained here
            # and per seed. It is in no count, no miss list, no unreadable
            # list, no mechanism block and no preset record; the block
            # states in its own text that the row has no band, and does not
            # supply one. See `REPORTED` and `REPORTED_NOT_GRADED`.
            "reported": reported,
            # Kept so the certificate above is re-derivable from this
            # artefact alone: a count without the readings under it is an
            # assertion, and this is the file a committed record is built
            # from.
            "per_seed_252": collected[("panel_252", preset)],
            "per_seed_heldout_seeds": collected[("heldout_seeds", preset)],
            "annualised_vol_pct": p252["annualised_vol_pct"],
            "vol_at_vix_5": lo["annualised_vol_pct"],
            "vol_at_vix_65": hi["annualised_vol_pct"],
            "crisis_lever": hi["annualised_vol_pct"] / lo["annualised_vol_pct"],
            # The lever's WINDOW, beside the lever. Read from the panels
            # rather than from `LEVER_BURN`, so the number in the artefact
            # is the one the runs actually used and not the one the module
            # currently declares. A lever read from a cold open and a lever
            # read from a settled one are different measurements of the
            # same name; every `crisis_lever` block written before
            # 2026-09-14 is the first kind and says nothing, which is the
            # eighth measurement defect.
            "crisis_lever_window_days": lever_days,
            "crisis_lever_burn": lever_burn,
        }
        r = results[preset]
        mc = r["mechanism_252"]["counts"]
        # The denominator is what the basis could READ, not the panel's
        # length. Printing "13/14" when the fourteenth row has no band on
        # this basis is the miss that a reader cannot tell from a failure.
        print(f"{preset:8s} 252:{n252:2d}/{len(PANEL) - len(unr252):<2d} "
              f"504:{n504:2d}/{len(PANEL) - len(unr504):<2d} "
              f"hoU:{nhou:2d}/{len(PANEL) - len(unrhou):<2d} "
              f"hoS:{nhos:2d}/{len(PANEL) - len(unrhos):<2d} "
              f"vol:{r['annualised_vol_pct']:5.1f}%  "
              f"lever:{r['crisis_lever']:.2f}x  "
              f"mech:{mc['mechanism_shown']:2d}/{mc['mechanism_of']}  "
              f"centre:{mc['at_centre']:2d}/{mc['at_centre_of']}"
              # Printed with an explicit UNGRADED marker, so an operator
              # reading the console cannot take it for a panel row.
              + "  vixar1(ungraded):"
              + "/".join(f"{r['reported']['cells'][c].get(facts.VIX_AR1_ROW, float('nan')):.4f}"
                         for c in ("panel_252", "panel_504"))
              + (f"  REVERSED:{','.join(r['mechanism_252']['reversed'])}"
                 if r["mechanism_252"]["reversed"] else ""), flush=True)

    out = {
        "pretium_version": tradefloor.version(),
        # The commit that MEASURED this, read here rather than stamped by
        # whoever writes a record from it later. `record.py` used to take
        # `git rev-parse HEAD` in its own working directory, which is the
        # measuring checkout only when the record is written on the box --
        # write one anywhere else and the record names a commit that did not
        # produce it.
        "commit": _commit(),
        # The ENGINE's default, not the envelope's claim about it. This field
        # read `envelope.PRESET` until 0.6.0, so at an era boundary, which is
        # exactly when this tool runs, the artefact labelled itself with the
        # preset the envelope still described rather than the one measured.
        "default_preset": tradefloor.model_preset()["name"],
        "envelope_preset": envelope.PRESET,
        "wall_s": time.time() - started,
        "workers": args.workers,
        "panel": list(PANEL),
        "method": {
            "roster": f"Universe.random({ROSTER_N}, seed={ROSTER_SEED})",
            "heldout_universe": f"Universe.random({HELDOUT_N}, seed={HELDOUT_SEED})",
            "train_seeds": f"{train[0]}-{train[-1]} ({len(train)})",
            "heldout_seeds": f"{heldout[0]}-{heldout[-1]} ({len(heldout)})",
            # The basis, not only the name. See `_tables`.
            **band_stamp,
            "index_tail_not_measured": TAIL_NOT_MEASURED,
            "reported_not_graded": REPORTED_NOT_GRADED,
            "crisis_lever": (
                f"annualised vol at held VIX {LEVER_HI:.0f} over held VIX "
                f"{LEVER_LO:.0f}, certified roster, 252 days, thirty seeds, "
                f"after {LEVER_BURN} discarded sessions at the pin. The burn "
                f"is the eighth measurement defect of measurement-integrity.md "
                f"repaired: without it each pin's window averages the factor "
                f"variance's walk toward the pinned target, the two pins walk "
                f"in opposite directions, and the ratio reads 4 to 8 per cent "
                f"low by an amount that is a property of the preset"
            ),
            "crisis_lever_burn": LEVER_BURN,
            "real_crisis_lever": REAL_LEVER,
            "mechanism": (
                "facts.mechanism_verdict per row on the per-seed panels of "
                "the cell: an exact sign test against the row's "
                "mechanism-absent reading (facts.NULLS) at the cut the "
                "band's own false-alarm rate gives (facts.sign_cut). Three "
                "counts, never one: in band, mechanism shown, at real centre"
            ),
        },
        "presets": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1)
    print(f"wrote {args.out} in {out['wall_s']:.0f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
