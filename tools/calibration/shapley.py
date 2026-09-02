"""Shapley shares of a preset change across named mechanism groups.

Given a base preset, a target preset and named groups of dial overrides,
the panel runs on every subset of the groups at the same seeds and roster,
and each group receives its Shapley share of the change in every realism
statistic in `facts.REAL_MARKETS`. The shares sum to the target's panel
minus the base's, statistic by statistic, and the run enumerates every
subset, so the sum is an identity rather than an estimate.

Usage:

    python tools/calibration/shapley.py --base pt-v14 --target pt-v16 \\
        --seeds 1-30 --days 252 --workers 94 --out out/shapley
    python tools/calibration/shapley.py --check   # the declaration only

`--groups path.json` replaces the built-in declaration with a JSON object
of the same shape, `{"group": {"dial": value}}`, for another base and
target pair.

## The groups

Until a preset carries its own mechanism set, `GROUPS` is declared by hand
from the `pt_v15` and `pt_v16` constructors in `rust/src/params.rs`, and
`verify_declaration` checks it against the preset table before anything
runs: every dial is a settable `ModelParams` field, no dial sits in two
groups, and `ModelParams.from_preset(base, **union)` carries the target's
fingerprint. A declaration that fails any of those is refused, because a
share under a wrong declaration is a number with a wrong label.

The design note lists `sector_loading_beta_slope` under `asymmetry` as
well as under `sector_slope`. The dial is one field and holds one value,
so it sits in `sector_slope` at the target's 0.7, and `asymmetry` holds
the two dials `pt_v16` introduces on its own. The interaction column of
the report is where the two groups' dependence on each other shows.

## What a share means under common random numbers

Each subset is measured by `facts.measure` at the same seeds and roster.
The market stream's draw schedule is a function of market status, roster
and sector count and of nothing a preset reaches (`Engine.draws_by_stream`
states the split), so every subset at one seed transforms the same noise
sequence, and `instrumentlib.crn_streams` checks the market draw count
across every vector per seed before any share is computed. A count that
differs stops the run: a difference between two subsets is a parameter
effect only under common random numbers. The economy stream may branch,
because the macro chain draws on macro state and every dial here reaches
macro state through the market; those branches are part of the effect,
and the JSON records them as data.

The value of a subset for a statistic is the median over seeds, which is
how the envelope states a panel, and the Shapley share of a group is the
average over orderings of its marginal `v(S + g) - v(S)`. A median is an
order statistic: the seed at the median of `v(S)` can differ from the seed
at the median of `v(S + g)`, so a difference of medians re-admits some of
the seed noise the pairing removed. The per-seed shares keep the pairing:
the marginal at one seed is the response of one noise path to the group's
dials. The JSON carries every per-seed share, and the report prints their
median, their spread and the count of seeds whose share carries the
headline's sign. A headline share whose sign a minority of seeds carries is
a statement about the panel the envelope would print rather than about the
mechanism.

The report separates each share into a main effect, the group's marginal
against the base alone, and an interaction, the rest of the share. A group
that is inert alone and live in company shows as a small main effect and a
large interaction. The `last` column is the group's marginal removed from
the target, the other end of the same ordering.

## What it cannot do

It cannot rank a group against the real bands on its own: a share is a
slice of a difference and a band is a location. The report prints each
share beside `envelope.CERTIFIED` and the band, and says which statistic a
group moved into or out of band, alone against the base and last against
the target, so a reader sees the location and the slice together.

It cannot attribute below the group. A dial's own share needs a declaration
with one dial per group, and the exact enumeration stops at nine groups
(512 subsets); above that the tool refuses and names the budget, since a
sampled Shapley needs its own error statement.

It cannot separate two mechanisms that share a dial, and it cannot see an
intermediate preset's value of a dial: only the base's value and the
target's are in play, so `pt-v15`'s slope of 0.5 is not a subset here.

Every number is measured at the roster, seeds and horizon on the command
line. The bands are `facts.REAL_MARKETS` at 252 days and
`envelope.BANDS_504` at 504, and a run at any other horizon is reported
against the 252-day ruler with a caveat computed from the run.

## Runs

A smoke at `Universe.random(6, seed=1)`, seeds 1 and 2, 40 days, 128
subsets and 256 panels, on the tree at f47c149 with the 0.6.1 core, 43
seconds of wall time on eight workers, proves the pipeline and measures
nothing: the CRN guard passed on both seeds, the shares summed to target
minus base on each of the eleven statistics the roster could measure,
`corr_asymmetry_lagged`, `sector_excess_corr` and `corr_persistence_acf1`
were unmeasured at six names and 40 days, and `qe` contributed 0.0 to the
bit on every statistic and seed, since the QE channel its dial scales
never fired in 40 days. Its figures are in the pull request that added
this tool and nowhere else.

The pt-v14 to pt-v16 measurement runs on AWS through
`tools/calibration/aws/user-data-shapley.sh` (`Universe.random(40,
seed=111)`, seeds 1 to 30, 252 days, 128 subsets, 3,840 panels) and is
pending; its figures replace this paragraph when it lands.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from fractions import Fraction
from pathlib import Path

# Own directory first, for instrumentlib; the source tree appended, never
# inserted ahead of site-packages. gate_batch.py records why: a source copy
# without a compiled core shadows an installed wheel on a fresh box.
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.append(str(Path(__file__).resolve().parent.parent.parent / "python"))

import instrumentlib  # noqa: E402

#: The change the built-in declaration divides.
BASE = "pt-v14"
TARGET = "pt-v16"

#: The mechanism groups, declared by hand from `params.rs`. `pt_v15` sets
#: six dials on `pt_v14`: the four slow variance dials and the credit floor
#: (`slow_vol`) and the sector loading slope at 0.5. `pt_v16` sets fourteen
#: on `pt_v15`: the QE gain (`qe`), the two asymmetry dials (`asymmetry`),
#: the slope again at 0.7 (`sector_slope`, at its target value), the six
#: sigma literals of the joint trim (`trim`), the volume response
#: (`volume_response`) and the three fear-gap dials (`fear_gap`). Nineteen
#: dials, each in one group; `verify_declaration` holds this table to the
#: preset table rather than to this comment.
GROUPS: dict[str, dict[str, float]] = {
    "slow_vol": {
        "market_vol_slow_weight": 0.35,
        "market_vol_slow_persistence": 0.98,
        "market_vol_slow_gain": 0.05,
        "market_vol_slow_vix_damp": 0.374,
        "daily_credit_floor_gain": 1.0,
    },
    "sector_slope": {
        "sector_loading_beta_slope": 0.7,
    },
    "qe": {
        "qe_pe_gain": 0.0,
    },
    "asymmetry": {
        "vix_cycle_amplitude": 0.85,
        "market_beta_down_asym": 0.025,
    },
    "trim": {
        "market_factor_sigma": 0.007593024924589399,
        "idio_sigma_scale": 0.5125981926,
        "jump_sigma_idio": 0.0752080062,
        "jump_sigma_market": 0.0024597567320385947,
        "endogenous_news_sigma": 0.01751004376,
        "sector_factor_sigma": 0.008583053614,
    },
    "volume_response": {
        "volume_move_response": 1.0,
    },
    "fear_gap": {
        "vix_realised_vol_weight": 0.3,
        "vix_decay_ratio": 0.6,
        "vix_mean_reversion": 0.06,
    },
}

#: Exact enumeration stops here: 512 subsets. A sampled Shapley above it
#: would need its own error statement, which this tool does not carry.
MAX_GROUPS = 9

#: The certified roster, from the shared instrument constants.
ROSTER_N = instrumentlib.PANEL_UNIVERSE_N
ROSTER_SEED = instrumentlib.PANEL_UNIVERSE_SEED

#: How far the sum of shares may sit from `v(all) - v(none)`. Exact
#: enumeration leaves rounding only, so this is a self-check on the
#: arithmetic rather than a tolerance on an estimate.
TOLERANCE = 1e-9

#: The seed count the envelope states its panel on.
ENVELOPE_SEEDS = 30


# ---------------------------------------------------------------------------
# The lattice
# ---------------------------------------------------------------------------

def subsets(groups: dict[str, dict[str, float]]) -> list[frozenset[str]]:
    """Every subset of the group names, smallest first.

    Refuses more than `MAX_GROUPS` groups and names the budget, since the
    Shapley below enumerates every one of these.
    """
    names = list(groups)
    if len(names) > MAX_GROUPS:
        raise ValueError(
            f"{len(names)} groups is above the exact-enumeration budget of "
            f"{MAX_GROUPS} (2**{MAX_GROUPS} subsets); merge groups rather "
            "than sampling"
        )
    if not names:
        raise ValueError("no groups declared; nothing to attribute")
    out: list[frozenset[str]] = []
    for size in range(len(names) + 1):
        for combo in itertools.combinations(names, size):
            out.append(frozenset(combo))
    return out


def union(groups: dict[str, dict[str, float]],
          subset: frozenset[str]) -> dict[str, float]:
    """The overrides a subset applies, in declaration order."""
    out: dict[str, float] = {}
    for name, dials in groups.items():
        if name in subset:
            out.update(dials)
    return out


def label(groups: dict[str, dict[str, float]], subset: frozenset[str]) -> str:
    """A subset's name in the JSON and the report: `base` for the empty set."""
    members = [name for name in groups if name in subset]
    return "+".join(members) if members else "base"


def parse_seeds(text: str) -> list[int]:
    """`1-30`, `1,2,5` or `7`, as a sorted list without repeats."""
    seeds: set[int] = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            seeds.update(range(int(lo), int(hi) + 1))
        else:
            seeds.add(int(part))
    if not seeds:
        raise ValueError(f"no seeds in {text!r}")
    return sorted(seeds)


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

def verify_declaration(groups: dict[str, dict[str, float]], base: str,
                       target: str | None = None) -> dict:
    """Hold the declaration to the preset table, or refuse it.

    Refused: a dial that is not a settable `ModelParams` field, a dial in
    two groups, a value that is not a number, and, when `target` is given,
    a union of all groups that does not reproduce the target from the
    base: a dial the target moves that no group declares, a dial a group
    declares that the target leaves at the base value, a value that
    differs from the target's, or a fingerprint that differs.

    Returns the ownership map, the union and the base's values of every
    declared dial, which the report prints as the group table.
    """
    import tradefloor

    settable = set(tradefloor.ModelParams.settable())
    owner: dict[str, str] = {}
    problems: list[str] = []
    for name, dials in groups.items():
        if not isinstance(dials, dict):
            problems.append(f"{name}: a group is a dict of dial overrides")
            continue
        for dial, value in dials.items():
            if dial not in settable:
                problems.append(
                    f"{name}: {dial} is not a settable ModelParams field")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                problems.append(f"{name}: {dial} = {value!r} is not a number")
            if dial in owner:
                problems.append(
                    f"{dial} is declared in both {owner[dial]} and {name}; "
                    "a dial holds one value, so it belongs to one group")
            else:
                owner[dial] = name
    if problems:
        raise ValueError("declaration refused:\n  " + "\n  ".join(problems))

    everything = union(groups, frozenset(groups))
    base_values = tradefloor.ModelParams.from_preset(base).to_dict()
    result = {
        "base": base,
        "target": target,
        "owner": owner,
        "union": everything,
        "base_values": {dial: base_values[dial] for dial in everything},
        "inert": sorted(dial for dial, value in everything.items()
                        if base_values[dial] == value),
    }
    if target is None:
        return result

    target_model = tradefloor.ModelParams.from_preset(target)
    target_values = target_model.to_dict()
    moved = {dial for dial in settable
             if target_values[dial] != base_values[dial]}
    for dial in sorted(moved - set(everything)):
        problems.append(f"{target} moves {dial} and no group declares it")
    for dial in sorted(set(everything) - moved):
        problems.append(
            f"{owner[dial]}: {dial} is declared and {target} leaves it at "
            f"the {base} value {base_values[dial]!r}")
    for dial, value in everything.items():
        if dial in moved and target_values[dial] != value:
            problems.append(
                f"{owner[dial]}: {dial} = {value!r} where {target} sets "
                f"{target_values[dial]!r}")
    full = tradefloor.ModelParams.from_preset(base, **everything)
    if full.fingerprint != target_model.fingerprint:
        problems.append(
            f"from_preset({base!r}, **union) carries fingerprint "
            f"{full.fingerprint} and {target} carries "
            f"{target_model.fingerprint}")
    if problems:
        raise ValueError("declaration refused:\n  " + "\n  ".join(problems))
    result["target_values"] = {dial: target_values[dial]
                               for dial in everything}
    result["fingerprint"] = full.fingerprint
    return result


def declaration_table(groups: dict[str, dict[str, float]],
                      verified: dict) -> str:
    """The group table: group, dial, base value, declared value."""
    rows = [f"{'group':16s} {'dial':30s} {'base':>22s} {'declared':>22s}"]
    for name, dials in groups.items():
        for dial, value in dials.items():
            rows.append(
                f"{name:16s} {dial:30s} "
                f"{verified['base_values'][dial]!r:>22s} {value!r:>22s}")
    count = sum(len(dials) for dials in groups.values())
    tail = f"{count} dials in {len(groups)} groups"
    if verified.get("fingerprint"):
        tail += (f"; from_preset({verified['base']!r}, **union) carries "
                 f"{verified['fingerprint']}")
    rows.append(tail)
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# The evaluation core
# ---------------------------------------------------------------------------

def evaluate_subset(job: tuple) -> dict:
    """One (base, overrides, seed) panel, in a worker process.

    The same shim as `instrumentlib.evaluate_panel`, with the base preset
    a job argument rather than `pt-v1`: `facts.measure` takes the model
    through its own `model=` argument, and the `Engine` symbol is
    intercepted only to hand the engine back so its draw counts can be
    read for the CRN guard. `job` is `(base, overrides, seed, days,
    universe_n, universe_seed)`.
    """
    base, overrides, seed, days, universe_n, universe_seed = job
    import tradefloor
    import tradefloor.facts as facts

    model = tradefloor.ModelParams.from_preset(base, **overrides)
    universe = tradefloor.Universe.random(universe_n, seed=universe_seed)

    engines: list = []
    original = facts.Engine

    def engine_capture(**kwargs):
        engine = original(**kwargs)
        engines.append(engine)
        return engine

    started = time.perf_counter()
    facts.Engine = engine_capture
    try:
        panel = facts.measure(seed=seed, universe=universe, days=days,
                              model=model)
    finally:
        facts.Engine = original
    elapsed = time.perf_counter() - started

    engine = engines[0]
    assert engine.model_fingerprint == model.fingerprint, (
        "the engine ran a different model than the subset asked for; "
        "recording this panel would mislabel it"
    )
    assert panel["model_fingerprint"] == model.fingerprint, (
        "the panel reports a different model than the subset asked for"
    )
    numeric = {
        key: value for key, value in panel.items()
        if isinstance(value, (int, float)) and key != "seed"
    }
    unmeasured = sorted(key for key in facts.REAL_MARKETS
                        if panel.get(key) is None)
    return {
        "base": base,
        "fingerprint": model.fingerprint,
        "overrides": overrides,
        "seed": seed,
        "seconds": elapsed,
        "draws_consumed": engine.draws_consumed,
        "draws_by_stream": dict(engine.draws_by_stream()),
        "panel": numeric,
        "unmeasured": unmeasured,
    }


def plan(base: str, groups: dict[str, dict[str, float]], seeds: list[int],
         days: int, universe: tuple[int, int],
         target: str | None = None) -> dict:
    """The run's identity, for the resume check and the JSON.

    Carries the era fingerprint beside the version string, because the
    manifest module records a day on which three trajectory changes landed
    while the version string held still.
    """
    import tradefloor
    from tradefloor import manifest

    body = {
        "base": base,
        "target": target,
        "groups": groups,
        "seeds": list(seeds),
        "days": days,
        "roster": {"n": universe[0], "seed": universe[1]},
        "version": tradefloor.version(),
        "era": manifest.era_fingerprint(),
    }
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True).encode("utf-8")).hexdigest()
    return {"plan": digest, **body}


def _read_rows(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def evaluate(base: str, groups: dict[str, dict[str, float]],
             seeds: list[int], days: int, universe: tuple[int, int], *,
             workers: int = 1, out: str | Path | None = None,
             target: str | None = None,
             progress=print) -> dict[frozenset[str], list[dict]]:
    """The panel of every subset at every seed, one row per seed.

    `universe` is `(n, seed)` for `Universe.random`, which is what a
    worker can be handed. Two subsets whose unions are the same vector
    share one measurement, since the library is deterministic in the
    vector; that is what makes a group with no dials contribute zero to
    the bit rather than to a tolerance.

    With `out`, every finished row is appended to `out/tasks.jsonl` and
    the plan is written to `out/meta.json`, so a run that dies resumes
    from its rows. A directory holding another plan's rows is refused.
    """
    verify_declaration(groups, base, target)
    lattice = subsets(groups)
    vectors: dict[str, dict[str, float]] = {}
    vector_of: dict[frozenset[str], str] = {}
    for subset in lattice:
        overrides = union(groups, subset)
        key = instrumentlib.vector_key(overrides)
        vectors.setdefault(key, overrides)
        vector_of[subset] = key

    identity = plan(base, groups, seeds, days, universe, target)
    done: dict[tuple[str, int], dict] = {}
    tasks_path = meta_path = None
    if out is not None:
        out = Path(out)
        out.mkdir(parents=True, exist_ok=True)
        tasks_path = out / "tasks.jsonl"
        meta_path = out / "meta.json"
        if meta_path.exists():
            previous = json.loads(meta_path.read_text(encoding="utf-8"))
            if previous.get("plan") != identity["plan"]:
                raise ValueError(
                    f"{out} holds rows for plan {previous.get('plan')} and "
                    f"this run is plan {identity['plan']}; choose another "
                    "--out rather than mixing two plans")
            for row in _read_rows(tasks_path):
                done[(instrumentlib.vector_key(row["overrides"]),
                      row["seed"])] = row
            if done:
                progress(f"resuming from {len(done)} rows in {tasks_path}")
        meta_path.write_text(json.dumps(identity, indent=1, sort_keys=True)
                             + "\n", encoding="utf-8")

    jobs = [(base, overrides, seed, days, universe[0], universe[1])
            for key, overrides in vectors.items() for seed in seeds
            if (key, seed) not in done]
    total = len(vectors) * len(seeds)
    progress(f"{len(vectors)} vectors x {len(seeds)} seeds = {total} panels, "
             f"{len(jobs)} to run on {workers} worker(s)")

    started = time.perf_counter()
    last_report = started
    finished = 0

    def record(row: dict) -> None:
        nonlocal finished, last_report
        done[(instrumentlib.vector_key(row["overrides"]), row["seed"])] = row
        finished += 1
        if tasks_path is not None:
            with open(tasks_path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        now = time.perf_counter()
        # A line per minute at least, so a streamed log shows life, and a
        # line per tenth so a short run shows its shape.
        if (now - last_report >= 60 or finished == len(jobs)
                or finished % max(1, len(jobs) // 10) == 0):
            rate = finished / max(now - started, 1e-9)
            remaining = (len(jobs) - finished) / rate if rate else 0.0
            progress(f"{finished}/{len(jobs)} panels, {now - started:.0f}s "
                     f"elapsed, {remaining:.0f}s remaining")
            last_report = now

    if workers <= 1:
        for job in jobs:
            record(evaluate_subset(job))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(evaluate_subset, job) for job in jobs]
            for future in as_completed(futures):
                record(future.result())

    missing = [(key, seed) for key in vectors for seed in seeds
               if (key, seed) not in done]
    if missing:
        raise RuntimeError(f"{len(missing)} panels were not measured")
    return {subset: [done[(vector_of[subset], seed)] for seed in seeds]
            for subset in lattice}


def distinct_rows(rows_by_subset: dict[frozenset[str], list[dict]]
                  ) -> list[dict]:
    """One row per (vector, seed), the guard's input."""
    seen: dict[tuple[str, int], dict] = {}
    for rows in rows_by_subset.values():
        for row in rows:
            seen.setdefault(
                (instrumentlib.vector_key(row["overrides"]), row["seed"]),
                row)
    return list(seen.values())


def guard(rows: list[dict]) -> dict:
    """The CRN guard across every vector per seed, before any share.

    `instrumentlib.crn_streams` raises when the market draw count differs
    between vectors at one seed, which would make every marginal across
    that pair re-alignment noise rather than a parameter effect. Economy
    and external deviations come back as data.
    """
    return instrumentlib.crn_streams(rows)


# ---------------------------------------------------------------------------
# Values and shares
# ---------------------------------------------------------------------------

def measured_statistics(rows_by_subset: dict[frozenset[str], list[dict]]
                        ) -> tuple[list[str], list[str]]:
    """The `REAL_MARKETS` statistics numeric in every row, and the rest."""
    from tradefloor import facts

    measured: list[str] = []
    unmeasured: list[str] = []
    for key in facts.REAL_MARKETS:
        present = all(isinstance(row["panel"].get(key), (int, float))
                      for rows in rows_by_subset.values() for row in rows)
        (measured if present else unmeasured).append(key)
    return measured, unmeasured


def median_values(rows_by_subset: dict[frozenset[str], list[dict]],
                  stats: list[str]) -> dict[frozenset[str], dict[str, float]]:
    """The value of each subset: the median over seeds, per statistic."""
    return {
        subset: {key: statistics.median(row["panel"][key] for row in rows)
                 for key in stats}
        for subset, rows in rows_by_subset.items()
    }


def seed_values(rows_by_subset: dict[frozenset[str], list[dict]],
                stats: list[str]
                ) -> dict[int, dict[frozenset[str], dict[str, float]]]:
    """The value of each subset at each seed, the paired table."""
    out: dict[int, dict[frozenset[str], dict[str, float]]] = {}
    for subset, rows in rows_by_subset.items():
        for row in rows:
            out.setdefault(row["seed"], {})[subset] = {
                key: row["panel"][key] for key in stats}
    return out


def shapley(values: dict[frozenset[str], dict[str, float]],
            groups: list[str] | None = None) -> dict[str, dict[str, float]]:
    """Exact Shapley shares over the complete lattice.

    `values` maps every subset of the groups to its statistics. The share
    of group g on a statistic is the sum over subsets S without g of
    `|S|! (n - |S| - 1)! / n!` times `v(S + g) - v(S)`. The weights are
    exact fractions, so the shares sum to `v(all) - v(none)` to rounding,
    and the function checks that before returning.
    """
    everything: frozenset[str] = frozenset().union(*values)
    names = list(groups) if groups is not None else sorted(everything)
    if set(names) != set(everything):
        raise ValueError(
            f"groups {names} and the lattice's members {sorted(everything)} "
            "differ")
    n = len(names)
    if n > MAX_GROUPS:
        raise ValueError(
            f"{n} groups is above the exact-enumeration budget of "
            f"{MAX_GROUPS}")
    expected = 2 ** n
    if len(values) != expected:
        raise ValueError(
            f"{len(values)} subsets for {n} groups; exact enumeration "
            f"needs all {expected}")
    stats = list(values[frozenset()])
    weight = {size: float(Fraction(math.factorial(size)
                                   * math.factorial(n - size - 1),
                                   math.factorial(n)))
              for size in range(n)}
    shares: dict[str, dict[str, float]] = {}
    for name in names:
        others = [other for other in names if other != name]
        totals = {key: 0.0 for key in stats}
        for size in range(len(others) + 1):
            for combo in itertools.combinations(others, size):
                without = frozenset(combo)
                with_g = without | {name}
                for key in stats:
                    totals[key] += weight[size] * (
                        values[with_g][key] - values[without][key])
        shares[name] = totals
    for key in stats:
        total = sum(shares[name][key] for name in names)
        change = values[everything][key] - values[frozenset()][key]
        if abs(total - change) > TOLERANCE * max(1.0, abs(change)):
            raise AssertionError(
                f"{key}: shares sum to {total!r} against a change of "
                f"{change!r}; the enumeration is incomplete or the values "
                "are not finite")
    return shares


def effects(values: dict[frozenset[str], dict[str, float]],
            names: list[str]) -> dict[str, dict[str, dict[str, float]]]:
    """Main and last marginals per group, the two ends of the orderings.

    `main` is `v({g}) - v(none)`, the group alone against the base;
    `last` is `v(all) - v(all - g)`, the group removed from the target.
    """
    everything = frozenset(names)
    none = frozenset()
    stats = list(values[none])
    return {
        "main": {name: {key: values[frozenset([name])][key]
                        - values[none][key] for key in stats}
                 for name in names},
        "last": {name: {key: values[everything][key]
                        - values[everything - {name}][key] for key in stats}
                 for name in names},
    }


def _in_band(value: float, band: tuple[float, float]) -> bool:
    from tradefloor.facts import band_distance

    return band_distance(value, band[0], band[1]) == 0


def band_moves(values: dict[frozenset[str], dict[str, float]],
               names: list[str], bands: dict[str, tuple[float, float]]
               ) -> dict[str, dict[str, dict[str, str | None]]]:
    """Which statistic a group moved into or out of band, in two contexts.

    `alone` compares the group on the base against the base; `last`
    compares the target against the target without the group. `in`
    means the statistic entered the band with the group, `out` means it
    left, and None means its band verdict did not change.
    """
    everything = frozenset(names)
    none = frozenset()
    out: dict[str, dict[str, dict[str, str | None]]] = {}
    for name in names:
        out[name] = {}
        for key, band in bands.items():
            if key not in values[none]:
                continue
            verdicts = {}
            for context, (before, after) in (
                    ("alone", (none, frozenset([name]))),
                    ("last", (everything - {name}, everything))):
                was = _in_band(values[before][key], band)
                now = _in_band(values[after][key], band)
                verdicts[context] = (None if was == now
                                     else ("in" if now else "out"))
            out[name][key] = verdicts
    return out


def spread(seed_shares: dict[str, dict[str, dict[int, float]]],
           shares: dict[str, dict[str, float]]) -> dict:
    """The per-seed shares summarised beside the headline.

    `median`, `lo` and `hi` describe the per-seed shares: the 25th and
    75th percentiles from four seeds up, the minimum and maximum below
    that, named in `kind`. `agree` counts the seeds whose share carries
    the sign of the headline share, out of `of`.
    """
    out: dict[str, dict[str, dict]] = {}
    for name, by_stat in seed_shares.items():
        out[name] = {}
        for key, by_seed in by_stat.items():
            series = [by_seed[seed] for seed in sorted(by_seed)]
            if len(series) >= 4:
                quartiles = statistics.quantiles(series, n=4)
                lo, hi, kind = quartiles[0], quartiles[2], "quartiles"
            else:
                lo, hi, kind = min(series), max(series), "range"
            headline = shares[name][key]
            sign = (headline > 0) - (headline < 0)
            agree = sum(1 for value in series
                        if ((value > 0) - (value < 0)) == sign)
            out[name][key] = {
                "median": statistics.median(series), "lo": lo, "hi": hi,
                "kind": kind, "agree": agree, "of": len(series),
            }
    return out


def ruler(days: int) -> tuple[str, dict[str, tuple[float, float]]]:
    """The bands a horizon is scored against, and their name."""
    from tradefloor import envelope, facts

    if days == 2 * envelope.CERTIFIED_HORIZON_DAYS:
        return "envelope.BANDS_504", dict(envelope.BANDS_504)
    return "facts.REAL_MARKETS", dict(facts.REAL_MARKETS)


def caveats(*, seeds: list[int], days: int, universe: tuple[int, int],
            unmeasured: list[str], crn: dict, ruler_name: str,
            values: dict[frozenset[str], dict[str, float]],
            names: list[str], inert: list[str] = ()) -> list[str]:
    """The caveats this run earns, computed from the run.

    Each fires on a property of the request or the result, in the way the
    MCP server's caveat engine does, so a report never carries a caveat
    that a different run earned.
    """
    from tradefloor import envelope, facts

    out: list[str] = []
    if len(seeds) < ENVELOPE_SEEDS:
        out.append(
            f"{len(seeds)} seed(s) against the {ENVELOPE_SEEDS} the "
            "envelope states its panel on; the per-seed spread column is "
            "the evidence for each share, and the medians are medians of "
            f"{len(seeds)}")
    if days != envelope.CERTIFIED_HORIZON_DAYS:
        out.append(
            f"{days} days against the certified horizon of "
            f"{envelope.CERTIFIED_HORIZON_DAYS}; band verdicts use "
            f"{ruler_name}, and the certified column was measured at "
            f"{envelope.CERTIFIED_HORIZON_DAYS} days")
    if tuple(universe) != (ROSTER_N, ROSTER_SEED):
        out.append(
            f"roster Universe.random({universe[0]}, seed={universe[1]}) "
            f"against the certified Universe.random({ROSTER_N}, "
            f"seed={ROSTER_SEED}); the certified column is a different "
            "measurement of a different roster")
    if unmeasured:
        out.append(
            "unmeasured at this roster and horizon, so carrying no share: "
            + ", ".join(unmeasured))
    branched = sorted({d["seed"] for d in crn.get("economy_deviations", [])})
    if branched:
        out.append(
            f"the economy stream branched on {len(branched)} of "
            f"{len(seeds)} seed(s) for some vectors; the macro chain drew "
            "differently there, which is part of the effect and is "
            "recorded in the JSON under crn")
    everything = frozenset(names)
    small = []
    for key in values[frozenset()]:
        sd = facts.SEED_SD.get(key)
        change = values[everything][key] - values[frozenset()][key]
        if sd and abs(change) < sd:
            small.append(f"{key} ({change:+.4f} against a seed sd of {sd})")
    if small:
        out.append(
            "the whole change is under one across-seed sd at the baseline "
            "(facts.SEED_SD) on: " + "; ".join(small)
            + "; the shares there divide a change smaller than one seed's "
            "noise")
    if inert:
        out.append(
            "inert at this roster and horizon, with a share of 0.0 to the "
            "bit on every statistic and seed: " + ", ".join(inert)
            + "; the dials changed no panel, so the mechanism never fired "
            "here rather than fired and measured small")
    return out


def summarise(rows_by_subset: dict[frozenset[str], list[dict]],
              groups: dict[str, dict[str, float]], *, base: str,
              target: str | None, seeds: list[int], days: int,
              universe: tuple[int, int], crn: dict) -> dict:
    """Everything the report prints and the JSON carries."""
    from tradefloor import envelope

    names = list(groups)
    measured, unmeasured = measured_statistics(rows_by_subset)
    medians = median_values(rows_by_subset, measured)
    per_seed = seed_values(rows_by_subset, measured)
    shares = shapley(medians, names)
    seed_shares: dict[str, dict[str, dict[int, float]]] = {
        name: {key: {} for key in measured} for name in names}
    for seed, table in per_seed.items():
        for name, by_stat in shapley(table, names).items():
            for key, value in by_stat.items():
                seed_shares[name][key][seed] = value
    ruler_name, bands = ruler(days)
    ends = effects(medians, names)
    everything = frozenset(names)
    # A group whose per-seed share is 0.0 to the bit everywhere changed no
    # panel: its dials were never read on a path that reached a price.
    inert = [name for name in names
             if all(value == 0.0 for by_seed in seed_shares[name].values()
                    for value in by_seed.values())]
    text_key = {subset: label(groups, subset) for subset in rows_by_subset}
    return {
        "base": base,
        "target": target,
        "groups": groups,
        "roster": {"n": universe[0], "seed": universe[1]},
        "seeds": list(seeds),
        "days": days,
        "statistics": measured,
        "unmeasured": unmeasured,
        "inert": inert,
        "subsets": {text_key[subset]: {
            "members": sorted(subset),
            "overrides": union(groups, subset),
            "vector": instrumentlib.vector_key(union(groups, subset)),
        } for subset in rows_by_subset},
        "values": {text_key[s]: v for s, v in medians.items()},
        "seed_values": {str(seed): {text_key[s]: v for s, v in table.items()}
                        for seed, table in per_seed.items()},
        "shares": shares,
        "main": ends["main"],
        "last": ends["last"],
        "interaction": {name: {key: shares[name][key]
                               - ends["main"][name][key]
                               for key in measured} for name in names},
        "seed_shares": {name: {key: {str(seed): value
                                     for seed, value in by_seed.items()}
                               for key, by_seed in by_stat.items()}
                        for name, by_stat in seed_shares.items()},
        "spread": spread(seed_shares, shares),
        "change": {key: medians[everything][key] - medians[frozenset()][key]
                   for key in measured},
        "ruler": ruler_name,
        "bands": {key: list(bands[key]) for key in measured},
        "certified": {"preset": envelope.PRESET,
                      "horizon_days": envelope.CERTIFIED_HORIZON_DAYS,
                      "values": {key: envelope.CERTIFIED[key]
                                 for key in measured}},
        "band_moves": band_moves(medians, names, bands),
        "crn": crn,
        "caveats": caveats(seeds=seeds, days=days, universe=universe,
                           unmeasured=unmeasured, crn=crn,
                           ruler_name=ruler_name, values=medians,
                           names=names, inert=inert),
    }


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def report(summary: dict, *, provenance: dict | None = None,
           wall_seconds: float | None = None) -> str:
    """The shares beside the certified value and the band, per statistic."""
    from tradefloor import facts

    groups = summary["groups"]
    names = list(groups)
    seeds = summary["seeds"]
    roster = summary["roster"]
    count = len(summary["subsets"])
    lines = [
        f"mechanism Shapley: {summary['base']} to "
        f"{summary['target'] or 'the union of all groups'}, "
        f"{len(names)} groups, {count} subsets",
        f"roster Universe.random({roster['n']}, seed={roster['seed']}), "
        f"seeds {seeds[0]}-{seeds[-1]} ({len(seeds)}), {summary['days']} "
        f"days, {count * len(seeds)} panels"
        + (f", {wall_seconds:.0f}s wall" if wall_seconds else ""),
    ]
    if provenance:
        lines.append(
            f"commit {provenance.get('git_rev')}, tradefloor "
            f"{provenance.get('version')}, era "
            f"{str(provenance.get('era', ''))[:8]}, "
            f"{provenance.get('platform')}")
    crn = summary["crn"]
    branched = sorted({d["seed"] for d in crn.get("economy_deviations", [])})
    lines.append(
        f"CRN guard: {crn.get('guarded_stream')} draws equal across all "
        f"vectors on every seed; economy stream branched on "
        f"{len(branched)} of {len(seeds)} seed(s)")
    certified = summary["certified"]
    lines.append(
        f"ruler {summary['ruler']}; certified column: envelope.CERTIFIED "
        f"for {certified['preset']} at {certified['horizon_days']} days, "
        f"{ENVELOPE_SEEDS} seeds")
    if summary["caveats"]:
        lines.append("")
        lines.append("caveats:")
        lines += [f"  - {text}" for text in summary["caveats"]]
    lines += [
        "",
        "columns: share = the group's Shapley share of target minus base; "
        "main = the group alone against the base; last = the target minus "
        "the target without the group; interact = share minus main; "
        "seeds = median [lo, hi] of the per-seed shares and the seeds "
        "carrying the share's sign; alone/last = the statistic moved into "
        "(in) or out of (out) band in that context.",
    ]
    kind = next((v["kind"] for by_stat in summary["spread"].values()
                 for v in by_stat.values()), "quartiles")
    lines.append(
        "[lo, hi] are the 25th and 75th percentiles of the per-seed shares."
        if kind == "quartiles" else
        "[lo, hi] are the minimum and maximum of the per-seed shares.")
    header = (f"  {'group':16s} {'share':>10s} {'main':>10s} {'last':>10s} "
              f"{'interact':>10s}   {'seeds median [lo, hi]':>28s}  "
              f"{'sign':>5s}  {'alone':>5s} {'last':>5s}")
    none, everything = "base", label(groups, frozenset(names))
    for key in summary["statistics"]:
        base_value = summary["values"][none][key]
        target_value = summary["values"][everything][key]
        lo, hi = summary["bands"][key]
        verdict = (f"base {'in' if _in_band(base_value, (lo, hi)) else 'OUT'}"
                   f", target "
                   f"{'in' if _in_band(target_value, (lo, hi)) else 'OUT'}")
        lines += [
            "",
            f"{facts.LABELS.get(key, key):22s} base {base_value:9.4f}   "
            f"target {target_value:9.4f}   certified "
            f"{certified['values'][key]:9.4f}   band {lo:.2f} to {hi:.2f}   "
            f"{verdict}",
            header,
        ]
        for name in names:
            share = summary["shares"][name][key]
            sp = summary["spread"][name][key]
            moves = summary["band_moves"][name][key]
            lines.append(
                f"  {name:16s} {share:>10.4f} "
                f"{summary['main'][name][key]:>10.4f} "
                f"{summary['last'][name][key]:>10.4f} "
                f"{summary['interaction'][name][key]:>10.4f}   "
                f"{sp['median']:>9.4f} [{sp['lo']:8.4f}, {sp['hi']:8.4f}]  "
                f"{sp['agree']:>2d}/{sp['of']:<2d}  "
                f"{moves['alone'] or '-':>5s} {moves['last'] or '-':>5s}")
        total = sum(summary["shares"][name][key] for name in names)
        lines.append(
            f"  {'sum of shares':16s} {total:>10.4f}   target minus base "
            f"{summary['change'][key]:.4f}")
    if summary["unmeasured"]:
        lines += ["", "unmeasured, so carrying no share: "
                  + ", ".join(summary["unmeasured"])]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The command line
# ---------------------------------------------------------------------------

def load_groups(path: str | None) -> dict[str, dict[str, float]]:
    """The built-in declaration, or a JSON file of the same shape."""
    if path is None:
        return {name: dict(dials) for name, dials in GROUPS.items()}
    text = Path(path).read_text(encoding="utf-8")
    loaded = json.loads(text)
    if not isinstance(loaded, dict) or not all(
            isinstance(dials, dict) for dials in loaded.values()):
        raise ValueError(
            f"{path}: expected an object of the shape "
            '{"group": {"dial": value}}')
    return {str(name): {str(dial): value for dial, value in dials.items()}
            for name, dials in loaded.items()}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=(
            "Shapley shares of a preset change across named mechanism "
            "groups, on every subset at the same seeds. See the module "
            "docstring for what a share means under common random numbers."
        ))
    ap.add_argument("--base", default=BASE,
                    help=f"the preset every subset starts from ({BASE})")
    ap.add_argument("--target", default=TARGET,
                    help="the preset the union of all groups must "
                         f"reproduce ({TARGET})")
    ap.add_argument("--groups", default=None,
                    help="a JSON file {group: {dial: value}} replacing the "
                         "built-in declaration")
    ap.add_argument("--seeds", default="1-30",
                    help="a range 1-30, a list 1,2,5 or one seed (1-30)")
    ap.add_argument("--days", type=int, default=instrumentlib.PANEL_DAYS,
                    help=f"trading days per panel ({instrumentlib.PANEL_DAYS})")
    ap.add_argument("--names", type=int, default=ROSTER_N,
                    help=f"roster size for Universe.random ({ROSTER_N})")
    ap.add_argument("--universe-seed", type=int, default=ROSTER_SEED,
                    help=f"roster seed for Universe.random ({ROSTER_SEED})")
    ap.add_argument("--workers", type=int, default=1,
                    help="worker processes; 1 runs in this process (1)")
    ap.add_argument("--out", default=None,
                    help="directory for tasks.jsonl, meta.json, "
                         "shapley.json and report.txt")
    ap.add_argument("--check", action="store_true",
                    help="verify the declaration against the preset table, "
                         "print the group table and stop")
    args = ap.parse_args(argv)

    groups = load_groups(args.groups)
    verified = verify_declaration(groups, args.base, args.target)
    print(f"{args.base} to {args.target}")
    print(declaration_table(groups, verified))
    if args.check:
        return 0
    if args.out is None:
        ap.error("--out is required for a run")

    import tradefloor
    from tradefloor import manifest

    seeds = parse_seeds(args.seeds)
    universe = (args.names, args.universe_seed)
    started = time.perf_counter()
    rows_by_subset = evaluate(args.base, groups, seeds, args.days, universe,
                              workers=args.workers, out=args.out,
                              target=args.target)
    rows = distinct_rows(rows_by_subset)
    crn = guard(rows)
    summary = summarise(rows_by_subset, groups, base=args.base,
                        target=args.target, seeds=seeds, days=args.days,
                        universe=universe, crn=crn)
    wall = time.perf_counter() - started
    provenance = {**instrumentlib.provenance(),
                  "version": tradefloor.version(),
                  "era": manifest.era_fingerprint()}
    text = report(summary, provenance=provenance, wall_seconds=wall)
    print()
    print(text)
    out = Path(args.out)
    instrumentlib.write_json(out / "shapley.json", {
        "provenance": provenance,
        "wall_seconds": wall,
        "panel_seconds": sum(row["seconds"] for row in rows),
        **summary,
        "panels": rows,
    })
    (out / "report.txt").write_text(text + "\n", encoding="utf-8")
    print(f"written: {out / 'report.txt'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
