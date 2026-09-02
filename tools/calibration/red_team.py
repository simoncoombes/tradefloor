"""A search over seeds, rosters and scenarios for the panel's worst miss.

`tradefloor.envelope.CERTIFIED` reports one number per statistic: the
median over thirty seeds on the certified roster, no scenario applied. That
number is real, but it is a central tendency, and a caller asking "how bad
can this get" is asking a different question than "what does it read on an
ordinary day". This searches for the second question's answer: the worst
`facts.compare_to_real_markets` miss a stressed run produces, reported
beside the median so both numbers sit on one page.

## Method

Every cell is one `facts.measure(seed=seed, universe=universe,
scenario=scenario, days=days, model=preset)` panel, scored by
`facts.compare_to_real_markets`. The grid is `seeds` x `roster_seeds` x
`scenarios`, with each roster seed drawing its own `Universe.random(40,
seed=roster_seed)`; `days`, `preset` and the roster size are fixed for
every cell in the run, not searched. Cells are walked in the fixed order
`_grid_order` builds -- a `random.Random(0)` shuffle of the plain grid
product, seeded with a constant rather than anything wall-clock or
process-derived, so the same three axis lists produce the same walk on
every machine -- and cut to the first `budget` of them.

Ranking is by `scaled_distance`: the band miss `facts.compare_to_real_markets`
already reports, in units of `facts.SEED_SD`. A raw band distance would let a
naturally noisy statistic dominate the ranking on spread alone;
`scaled_distance` is the same seed-noise normalisation `tradefloor.loss` uses
for exactly that reason.

## What a cell's manifest is, and how it is captured

`facts.measure` builds an `Engine` and discards it once the panel is read
off the bars table, so there is nothing left to write a
`tradefloor.RunManifest` from once the call returns. `_measure_cell`
captures it the way `tools/calibration/instrumentlib.py`'s
`evaluate_panel` does: `facts.Engine` is substituted for the call's
duration with a wrapper that keeps the instance before returning it, and
restored in a `finally:` whether or not the run raises. Every statistic is
still computed by `facts.measure`'s own code; the substitution intercepts
only the constructor call, purely to keep a reference this module needs
and the public API does not expose. Since a scenario's pins and shocks
reach the engine through calls that land in `Engine.order_log` (the same
log `tradefloor.replay` replays), the captured engine's manifest carries
the realised scenario path without this module doing anything scenario-
specific to record it.

A manifest is built for every cell that runs, not only the ones that end
up worst: the dominant cost is the simulation itself (a 40-name, 252-day
panel is seconds; the manifest is an incremental JSON encode of an order
log holding four entries a day), and building it unconditionally means the
worst-per-statistic reduction can happen after the fact in the caller,
with no shared state a worker pool would need to coordinate.

## What this measures, and what it cannot claim

It reports the worst miss found within a stated grid and a stated budget
of cells. It cannot report a bound: a miss the grid does not contain, or
that the budget did not reach, is not found and the report says nothing
about it either way. `Search.report()`'s budget table names what ran, what
the full grid would have been, and which individual seed, roster seed and
scenario values never appeared in any cell that ran.

Three things are fixed rather than searched, listed here so the choice
is on the page rather than only in the budget table a run produces:

- **days, preset and roster size** are constants of one run (`--days`,
  `--preset`, and `ROSTER_N` = 40 respectively). A worst-miss search over
  those too would be a different, considerably larger search.
- **the macro path** is the engine's own default: no state is pinned
  beyond what a scenario pins.
- **scenario magnitude** is whatever the shipped YAML specifies. Every
  scenario applies from day zero at its shipped strength; this search asks
  which SCENARIO produces the worst miss, not how a scenario's own
  strength changes it. `envelope.GAPS`'s `scenario-magnitude` entry is the
  place that caveat already lives, and it is about exactly this axis.

Held-out seeds (`gate_pick.HELDOUT`, seeds 1-30) are inside the grid like
any other seed. A finding that lands on one is flagged in the report,
because those are seeds the calibration search never drew, and a miss on
one is evidence the calibration did not have.

## Reproducing a finding

```python
from tools.calibration import red_team

result = red_team.search("pt-v16", seeds=range(1, 201),
                         roster_seeds=range(1, 21),
                         scenarios=tradefloor.Scenario.available(),
                         days=252, budget=600, workers=8)
worst = result.worst["leverage_effect"]
worst.manifest.reproduce()          # replays the exact cell, or raises
```

## Usage

```
python tools/calibration/red_team.py --preset pt-v16 --seeds 1-200 \\
    --rosters 20 --scenarios all --days 252 --budget 600 --workers N \\
    --out out/red-team
```

Writes `report.txt` (the three tables `Search.report()` builds),
`budget.json` (`Search.budget` as data) and one `manifest-<statistic>.json`
per statistic in `Search.worst`, into `--out`.

## Dependencies

None beyond the library itself. Reuses `tools/calibration/instrumentlib.py`'s
Engine-capture shim (as a pattern, not a call: `evaluate_panel` always
builds its model from the `pt-v1` base plus deviation overrides and takes
no scenario, which does not fit a preset-plus-scenario grid), and
`tools/calibration/gate_pick.py`'s `HELDOUT` seed set.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

# The source tree is APPENDED, never inserted ahead of site-packages: see
# gate_batch.py's comment on the AWS launch this protects against. The
# tool's own directory IS inserted first, so `import gate_pick` finds its
# sibling module before anything else on the path could shadow it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))

import gate_pick  # noqa: E402
import tradefloor as tf  # noqa: E402
from tradefloor import envelope, facts  # noqa: E402

#: The roster size every cell shares. Fixed, not searched -- see the module
#: docstring's "What this measures" section. A test that needs a fast grid
#: overrides this module attribute directly (`red_team.ROSTER_N = 6`)
#: rather than threading a parameter through `search`, which keeps
#: `search`'s signature exactly the four axes, the horizon and the budget.
ROSTER_N = 40


def _grid_order(seeds: Sequence[int], roster_seeds: Sequence[int],
                scenarios: Sequence[str]) -> list[tuple[int, int, str]]:
    """Every (seed, roster_seed, scenario) cell, in a fixed seeded order.

    `random.Random(0)` shuffles the plain nested product. The 0 is a
    constant, not a parameter: determinism here means the SAME three axis
    lists produce the SAME walk on every call and every machine, which is
    what lets a budget cut the grid short and still be a command someone
    else can re-run and get the same cells from. Changing an axis list
    changes the grid being shuffled and therefore the walk, exactly as it
    should; what stays fixed is the shuffle, not the outcome.
    """
    grid = [(s, r, sc) for s in seeds for r in roster_seeds
           for sc in scenarios]
    random.Random(0).shuffle(grid)
    return grid


def _measure_cell(job: tuple[str, int, int, str, int]) -> dict[str, Any]:
    """One grid cell: build its inputs fresh, measure, capture a manifest.

    `job` is `(preset, seed, roster_seed, scenario_name, days)`. Runs
    standalone -- under `ProcessPoolExecutor` when `search` is called with
    `workers > 1`, or directly in the caller's process otherwise -- and
    builds its own `Universe` and `Scenario` rather than receiving either
    as an argument. A loaded `Scenario` is stateful (`Scenario.apply`'s
    docstring: "one scenario object driving two runs AT ONCE shares one
    clock between them"), so sharing one instance across cells would need
    either strict sequencing or a fresh `.copy()` per cell; building fresh
    from the name every call sidesteps the question entirely; it is the
    default constructor cost of one YAML read.

    Returns the per-statistic verdicts from `facts.compare_to_real_markets`
    and the cell's manifest, already serialised to JSON so the result
    crosses a process boundary as plain data.
    """
    preset, seed, roster_seed, scenario_name, days = job
    universe = tf.Universe.random(ROSTER_N, seed=roster_seed)
    scenario = tf.Scenario.load(scenario_name)

    # The instrumentlib.evaluate_panel shim: substitute facts.Engine for the
    # call's duration to keep the instance `facts.measure` would otherwise
    # discard, and restore it in `finally:` so a raised exception (or the
    # next cell run in this same worker process) never sees the
    # substitution. Every statistic below is computed by facts.measure's
    # own unmodified code; only the constructor call is intercepted.
    captured: list[Any] = []
    original_engine = facts.Engine

    def _engine_capture(**kwargs: Any) -> Any:
        engine = original_engine(**kwargs)
        captured.append(engine)
        return engine

    facts.Engine = _engine_capture
    try:
        panel = facts.measure(seed=seed, universe=universe, days=days,
                              scenario=scenario, model=preset)
    finally:
        facts.Engine = original_engine

    engine = captured[0]
    manifest = tf.RunManifest.of(
        engine, seed=seed, universe=universe, scenario=scenario,
        universe_source={"generator": "Universe.random", "n": ROSTER_N,
                         "seed": roster_seed},
        label=(f"P4 red team: {preset} seed={seed} roster_seed={roster_seed} "
              f"scenario={scenario_name}"),
    )

    verdicts = facts.compare_to_real_markets(panel)
    return {
        "seed": seed,
        "roster_seed": roster_seed,
        "scenario": scenario_name,
        "verdicts": verdicts,
        "manifest_json": manifest.to_json(),
    }


@dataclass(frozen=True)
class Finding:
    """The worst-scoring cell found for one statistic.

    `value` and `band` are `facts.compare_to_real_markets`'s `measured` and
    `real_range` for this statistic at this cell; `scaled_distance` is its
    `scaled_distance`, the ranking statistic. `manifest.reproduce()` replays
    exactly this cell.
    """

    statistic: str
    value: float
    band: tuple[float, float]
    scaled_distance: float
    seed: int
    roster_seed: int
    scenario: str
    manifest: "tf.RunManifest"


def _manifest_filename(statistic: str) -> str:
    """The name a finding's manifest is written under, in `report()` text
    and on disk alike, so the two never disagree about what a reader will
    find."""
    return f"manifest-{statistic}.json"


#: Gaps `_matching_gap` will not file a finding under, even when the
#: statistic name matches, because this search cannot be the finding they
#: describe.
#:
#: `roster-concentration`'s three statistics name what a SECTOR-CONCENTRATED
#: roster costs beyond one year; every cell here runs on a balanced
#: `Universe.random()` draw (its own docstring: sectors assigned round-
#: robin), so a miss on one of its three statistics is not that gap's
#: finding, whatever the horizon.
#:
#: `scenario-magnitude` and `macro-range` carry `statistics=()` in
#: `envelope.GAPS` -- they gate on a boolean a caller asserts
#: (`scenario_magnitude=`, `macro_regime=` in `envelope.check`), not on any
#: panel row, so statistic-membership matching can never reach them; they
#: are named here only so a reader of this constant does not wonder where
#: they went.
_EXCLUDED_GAP_IDS = frozenset({"roster-concentration"})


def _matching_gap(statistic: str, days: int) -> envelope.Gap | None:
    """The `envelope.GAPS` entry a finding on `statistic` at `days` already
    falls under, or None if it does not fall under any.

    Matches on `Gap.statistics` membership, gated by `Gap.beyond_days`: a
    gap that sets it (`horizon`, at `CERTIFIED_HORIZON_DAYS`) applies only
    past that many days; one left at the dataclass default of None
    (`decay-shape`) applies at every horizon. `_EXCLUDED_GAP_IDS` removes
    the one gap whose applicability depends on something this search does
    not vary.
    """
    for gap in envelope.GAPS:
        if gap.id in _EXCLUDED_GAP_IDS:
            continue
        if statistic not in gap.statistics:
            continue
        if gap.beyond_days is not None and days <= gap.beyond_days:
            continue
        return gap
    return None


def _draft_gap(finding: Finding) -> str:
    """`finding` as a `Gap(...)` call, so filing it in `envelope.GAPS` is a
    copy away.

    `id`, `summary`'s claim, `detail`'s mechanism and `forbids` need a
    reader who can say WHY the miss happens -- this function reports what
    was measured, not why, and marks the fields it cannot fill rather than
    guess at them.
    """
    lo, hi = finding.band
    return (
        "Gap(\n"
        '    id="<name-this>",\n'
        f'    summary="{finding.statistic} misses its band under '
        f'{finding.scenario}",\n'
        "    detail=(\n"
        f'        "{finding.statistic} read {finding.value:.4f} against a '
        f'band of {lo:g} to {hi:g} ({finding.scaled_distance:.2f} seed-sd '
        f'out), under {finding.scenario}, seed {finding.seed}, roster seed '
        f'{finding.roster_seed}. <describe the mechanism, measured, not '
        f'guessed>"\n'
        "    ),\n"
        '    forbids="<name what this miss should stop a reader trusting>",\n'
        f'    statistics=("{finding.statistic}",),\n'
        ")"
    )


def _span(values: Sequence[int]) -> str:
    """A contiguous run of ints as "lo-hi"; anything else as the sorted
    list, so a 200-seed run reads as one span instead of 200 numbers."""
    ordered = sorted(values)
    if not ordered:
        return "none"
    if ordered == list(range(ordered[0], ordered[-1] + 1)):
        return f"{ordered[0]}-{ordered[-1]}"
    return str(ordered)


def _report_worst(worst: dict[str, Finding], budget: dict[str, Any]) -> str:
    preset = budget["preset"]
    lines = ["Worst miss per statistic, beside the envelope's median"]
    if preset != envelope.PRESET:
        lines.append(
            f"({preset!r} was searched; envelope.CERTIFIED is measured on "
            f"the shipped {envelope.PRESET!r}, so the median column below is "
            "a different model from the worst column)"
        )
    if not worst:
        lines.append("no statistic had a scaled_distance to rank (empty "
                     "search, or every band-distance denominator missing)")
        return "\n".join(lines)
    head = (f"{'statistic':24s} {'median':>10s} {'worst':>10s} "
           f"{'band':>16s} {'scaled':>7s} {'seed':>6s} {'roster':>7s} "
           f"scenario              manifest")
    lines.append(head)
    lines.append("-" * len(head))
    for finding in sorted(worst.values(), key=lambda f: -f.scaled_distance):
        median = envelope.CERTIFIED.get(finding.statistic)
        median_s = f"{median:.4f}" if median is not None else "n/a"
        lo, hi = finding.band
        band_s = f"({lo:g}, {hi:g})"
        held = " [held-out seed]" if finding.seed in gate_pick.HELDOUT else ""
        lines.append(
            f"{finding.statistic:24s} {median_s:>10s} {finding.value:>10.4f} "
            f"{band_s:>16s} {finding.scaled_distance:>7.2f} "
            f"{finding.seed:>6d} {finding.roster_seed:>7d} "
            f"{finding.scenario:<22s}{_manifest_filename(finding.statistic)}"
            f"{held}"
        )
    return "\n".join(lines)


def _report_budget(budget: dict[str, Any]) -> str:
    lines = ["Search budget"]
    lines.append(f"  preset                {budget['preset']} (fixed)")
    lines.append(f"  days                  {budget['days']} (fixed)")
    lines.append(f"  roster size           {budget['roster_size']} (fixed)")
    lines.append(f"  macro                 {budget['fixed']['macro']} (fixed)")
    lines.append(
        f"  scenario magnitude    {budget['fixed']['scenario_magnitude']} "
        "(fixed)"
    )
    lines.append(f"  requested budget      {budget['requested_budget']} cells")
    lines.append(
        f"  cells run             {budget['cells_run']} of "
        f"{budget['cells_planned']} planned in the full grid"
    )
    lines.append(f"  workers               {budget['workers']}")
    lines.append(f"  wall time             {budget['wall_s']:.1f}s")
    lines.append(
        f"  seeds searched         {len(budget['seeds'])}: "
        f"{_span(budget['seeds'])}"
    )
    lines.append(
        f"  roster seeds searched  {len(budget['roster_seeds'])}: "
        f"{_span(budget['roster_seeds'])}"
    )
    lines.append(
        f"  scenarios searched     {len(budget['scenarios'])}: "
        f"{', '.join(sorted(budget['scenarios']))}"
    )
    unsearched = budget["unsearched"]
    for axis, label in (("seeds", "seeds"), ("roster_seeds", "roster seeds"),
                        ("scenarios", "scenarios")):
        values = unsearched[axis]
        if values:
            shown = (_span(values) if axis != "scenarios"
                    else ", ".join(sorted(values)))
            lines.append(f"  unsearched {label:<15s} {shown}")
        else:
            lines.append(
                f"  unsearched {label:<15s} none; every requested value "
                "appeared in at least one cell that ran"
            )
    if budget["cells_run"] < budget["cells_planned"]:
        lines.append(
            "  the budget cut the grid short: cells beyond it were never "
            "run, and a miss among them is not in this report"
        )
    return "\n".join(lines)


def _report_gaps(worst: dict[str, Finding], budget: dict[str, Any]) -> str:
    days = budget["days"]
    lines = ["Findings against envelope.GAPS"]
    if not worst:
        lines.append("  no findings to file")
        return "\n".join(lines)
    for finding in sorted(worst.values(), key=lambda f: f.statistic):
        gap = _matching_gap(finding.statistic, days)
        if gap is not None:
            lines.append(f"  {finding.statistic:24s} -> {gap.id} "
                         f"({gap.summary})")
        else:
            lines.append(f"  {finding.statistic:24s} -> NEW, not covered by "
                         "an existing gap")
            lines.append("")
            for draft_line in _draft_gap(finding).splitlines():
                lines.append(f"    {draft_line}")
            lines.append("")
    return "\n".join(lines)


@dataclass(frozen=True)
class Search:
    """The result of one `search` call: the worst finding per statistic and
    what the search did and did not cover."""

    #: One `Finding` per statistic that had a `scaled_distance` to rank
    #: (every `facts.REAL_MARKETS` key `facts.SEED_SD` covers).
    worst: dict[str, Finding]
    #: What ran and what did not: see `search`'s docstring and
    #: `_report_budget` for the shape.
    budget: dict[str, Any]

    def report(self) -> str:
        """Three tables: the worst miss beside the envelope's median, the
        search budget and its unsearched axes, and each finding filed
        under an existing `envelope.GAPS` entry or drafted as a new one.

        Reads only `self.worst` and `self.budget` -- nothing here re-
        measures or re-opens a manifest.
        """
        return "\n\n".join([
            _report_worst(self.worst, self.budget),
            _report_budget(self.budget),
            _report_gaps(self.worst, self.budget),
        ])


def search(preset: str, seeds: Sequence[int], roster_seeds: Sequence[int],
          scenarios: Sequence[str], days: int, budget: int, *,
          workers: int = 1) -> Search:
    """The panel's worst miss against `facts.REAL_MARKETS`, over a fixed grid.

    Walks `seeds` x `roster_seeds` x `scenarios` in the order `_grid_order`
    builds, running the first `budget` cells: each is
    `facts.measure(seed=seed, universe=Universe.random(ROSTER_N,
    seed=roster_seed), scenario=Scenario.load(scenario), days=days,
    model=preset)`, scored by `facts.compare_to_real_markets` and ranked by
    its `scaled_distance`. `days`, `preset` and the roster size are the same
    for every cell; only the seed, the roster seed and the scenario vary.
    See the module docstring for what stays fixed and why, and for how each
    cell's `tradefloor.RunManifest` is captured.

    `workers` is not one of the four searched axes -- it does not change
    which cells run or in what order, only whether `_measure_cell` runs
    under a `ProcessPoolExecutor` (`workers > 1`) or directly in this
    process (the default). It is a keyword-only addition to the four-axis,
    horizon-and-budget shape the design settled on, kept separate from
    them for that reason.

    Raises `tradefloor.ValidationError` if `scenarios` names anything
    outside `tradefloor.Scenario.available()`, if `budget` is not positive,
    or if any of `seeds`, `roster_seeds`, `scenarios` is empty.
    """
    seeds = list(seeds)
    roster_seeds = list(roster_seeds)
    scenarios = list(scenarios)

    available = set(tf.Scenario.available())
    unknown = sorted(set(scenarios) - available)
    if unknown:
        raise tf.ValidationError(
            f"unknown scenarios {unknown}; tradefloor.Scenario.available() "
            f"is {sorted(available)}"
        )
    if budget < 1:
        raise tf.ValidationError(f"budget must be positive, got {budget}")
    if not seeds or not roster_seeds or not scenarios:
        raise tf.ValidationError(
            "seeds, roster_seeds and scenarios must each be non-empty: got "
            f"{len(seeds)}, {len(roster_seeds)}, {len(scenarios)}"
        )

    grid = _grid_order(seeds, roster_seeds, scenarios)
    cells = grid[:budget]
    jobs = [(preset, seed, roster_seed, scenario, days)
           for seed, roster_seed, scenario in cells]

    started = time.perf_counter()
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(_measure_cell, jobs, chunksize=1))
    else:
        results = [_measure_cell(job) for job in jobs]
    elapsed = time.perf_counter() - started

    worst: dict[str, Finding] = {}
    for result in results:
        for statistic, verdict in result["verdicts"].items():
            scaled = verdict["scaled_distance"]
            if scaled is None:
                # facts.SEED_SD has no entry for this statistic, so there is
                # no noise-normalised distance to rank it by. The raw
                # band_distance still exists on `verdict` but is not
                # comparable across statistics, which is the whole reason
                # scaled_distance is the ranking field.
                continue
            current = worst.get(statistic)
            if current is not None and current.scaled_distance >= scaled:
                continue
            worst[statistic] = Finding(
                statistic=statistic,
                value=verdict["measured"],
                band=verdict["real_range"],
                scaled_distance=scaled,
                seed=result["seed"],
                roster_seed=result["roster_seed"],
                scenario=result["scenario"],
                manifest=tf.RunManifest.from_json(result["manifest_json"]),
            )

    seen_seeds = {cell[0] for cell in cells}
    seen_rosters = {cell[1] for cell in cells}
    seen_scenarios = {cell[2] for cell in cells}
    budget_report: dict[str, Any] = {
        "preset": preset,
        "days": days,
        "roster_size": ROSTER_N,
        "requested_budget": budget,
        "cells_run": len(cells),
        "cells_planned": len(grid),
        "seeds": seeds,
        "roster_seeds": roster_seeds,
        "scenarios": scenarios,
        "unsearched": {
            "seeds": sorted(set(seeds) - seen_seeds),
            "roster_seeds": sorted(set(roster_seeds) - seen_rosters),
            "scenarios": sorted(set(scenarios) - seen_scenarios),
        },
        "fixed": {
            "macro": "engine default (nothing pinned beyond a scenario)",
            "scenario_magnitude": "shipped default for each scenario",
        },
        "workers": workers,
        "wall_s": elapsed,
    }
    return Search(worst=worst, budget=budget_report)


def _parse_int_list(text: str) -> list[int]:
    """"1-200" as an inclusive range, or "1,4,9" as an explicit list."""
    text = text.strip()
    if "-" in text and "," not in text:
        lo_s, _, hi_s = text.partition("-")
        return list(range(int(lo_s), int(hi_s) + 1))
    return [int(item) for item in text.split(",") if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search seeds, rosters and scenarios for the panel's "
                    "worst miss against facts.REAL_MARKETS."
    )
    parser.add_argument("--preset", default=envelope.PRESET,
                        help="model preset every cell runs, e.g. pt-v16")
    parser.add_argument("--seeds", default="1-200",
                        help="simulation seeds: '1-200' or '1,2,5'")
    parser.add_argument("--rosters", type=int, default=20,
                        help="roster seeds 1..N; each draws its own "
                             "Universe.random(40, seed=that seed)")
    parser.add_argument("--scenarios", default="all",
                        help="'all', or a comma list from "
                             "tradefloor.Scenario.available()")
    parser.add_argument("--days", type=int,
                        default=envelope.CERTIFIED_HORIZON_DAYS,
                        help="trading days per cell")
    parser.add_argument("--budget", type=int, required=True,
                        help="grid cells to run, cut from the full seeded "
                             "walk over seeds x rosters x scenarios")
    parser.add_argument("--workers", type=int, default=1,
                        help="worker processes; 1 runs in this process")
    parser.add_argument("--out", required=True,
                        help="directory for report.txt, budget.json and "
                             "one manifest per statistic")
    args = parser.parse_args()

    seeds = _parse_int_list(args.seeds)
    roster_seeds = list(range(1, args.rosters + 1))
    scenarios = (list(tf.Scenario.available()) if args.scenarios == "all"
                else [s.strip() for s in args.scenarios.split(",")
                     if s.strip()])

    print(f"red team: preset={args.preset} seeds={len(seeds)} "
         f"rosters={len(roster_seeds)} scenarios={len(scenarios)} "
         f"days={args.days} budget={args.budget} workers={args.workers}",
         flush=True)

    result = search(args.preset, seeds, roster_seeds, scenarios, args.days,
                    args.budget, workers=args.workers)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    for finding in result.worst.values():
        manifest_path = out_dir / _manifest_filename(finding.statistic)
        manifest_path.write_text(finding.manifest.to_json(), encoding="utf-8")

    report_text = result.report()
    (out_dir / "report.txt").write_text(report_text, encoding="utf-8")
    with open(out_dir / "budget.json", "w", encoding="utf-8") as fh:
        json.dump(result.budget, fh, indent=1)

    print(report_text, flush=True)
    print(f"\nwrote {out_dir} in {result.budget['wall_s']:.1f}s", flush=True)


if __name__ == "__main__":
    sys.exit(main())
