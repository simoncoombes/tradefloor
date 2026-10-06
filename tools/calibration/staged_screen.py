"""Staged screens: few seeds on the rows a change can reach, then the full block.

The pieces are in three modules. `result_cache` stores every measured
reading under (build, model, protocol, seed). `dial_reach` says which
protocols a set of changed dials can move. `staged_rule` says when a row is
clearly out (the candidate dies), clearly in (its protocol may stop
gathering seeds) or still open. This module runs them together over many
candidates in one process pool.

Stage A measures, for each candidate, only the rows whose protocols its
changed dials reach (or a named target subset of them) on the first n0
seeds, and kills the candidate when any row is clearly out of band against
the full block's verdict (`staged_rule`, rate GAMMA).

Stage B measures every row. A protocol the changed dials cannot reach is
read from the baseline model's cache entries on all N seeds, at no cost.
A reachable protocol starts from n0 seeds (stage A's, read from the cache)
and grows by one block at a time while any row it feeds is open, up to N.

Verify mode is stage B with every protocol measured on every seed,
cached or not. Every reading that disagrees with a cached or baseline entry
is reported. On a baseline entry that is a reach claim the measurement
refutes; on the candidate's own entry it is a determinism failure.

The caller supplies the protocols, the rows and a picklable `measure`
function of (payload, protocol name, seed, settings). `gate_batch --staged`
is one such caller.
"""
from __future__ import annotations

import dataclasses
import math
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Iterable, Mapping, Sequence

import dial_reach
import staged_rule as R
from result_cache import Key, ResultCache, differences


@dataclasses.dataclass(frozen=True)
class Protocol:
    name: str
    cache_id: str
    seeds: tuple
    opens: frozenset = frozenset({"market"})


@dataclasses.dataclass(frozen=True)
class RowSpec:
    """A graded row: which protocols it reads and how it turns them into a value.

    `estimate` maps {protocol name: [reading per seed]} to the row's value.
    `band` is (lo, hi) or a function of the same readings giving it. For a
    `count` or `allseeds` row, `bad` counts the bad seeds or rules.
    """
    id: str
    protocols: tuple
    estimate: Callable[[Mapping[str, list]], float | None] | None = None
    band: Any = (None, None)
    kind: str = "mean"
    margin_se: float = 0.0
    bad: Callable[[Mapping[str, list]], int] | None = None
    max_bad_share: float | None = None


@dataclasses.dataclass
class Candidate:
    label: str
    payload: Any
    build: str
    params: str
    values: dict
    baseline: tuple | None = None        # (build, params, values) or None
    targets: tuple | None = None         # stage A: only these row ids


@dataclasses.dataclass
class Outcome:
    label: str
    state: str = "open"
    seeds: dict = dataclasses.field(default_factory=dict)
    decisions: list = dataclasses.field(default_factory=list)
    counts: dict = dataclasses.field(default_factory=lambda: {"cached": 0, "baseline": 0, "measured": 0})
    disagreements: list = dataclasses.field(default_factory=list)
    changed: list = dataclasses.field(default_factory=list)
    reachable: list = dataclasses.field(default_factory=list)
    rows: list = dataclasses.field(default_factory=list)


def _band(row: RowSpec, readings: Mapping[str, list]) -> tuple:
    return row.band(readings) if callable(row.band) else row.band


def row_state(row: RowSpec, readings: Mapping[str, list], *, n: int, N: int,
              c_kill: float, c_settle: float, boot_draws: int = 200) -> R.Decision:
    """The row's decision from its readings, with a seed-bootstrap error."""
    lo, hi = _band(row, readings)
    spec = R.Row(row.id, row.kind, lo, hi, row.margin_se, row.max_bad_share)
    if row.kind in ("count", "allseeds"):
        return R.decide(spec, n=n, N=N, bad=row.bad(readings), c_kill=c_kill,
                        c_settle=c_settle)
    value = row.estimate(readings)
    if row.kind == "deterministic":
        return R.decide(spec, n=n, N=N, value=value, c_kill=c_kill, c_settle=c_settle)
    m = min(len(v) for v in readings.values())

    def est(idx: Sequence[int]) -> float | None:
        return row.estimate({p: [v[i] for i in idx] for p, v in readings.items()})

    se = R.bootstrap_se(list(range(m)), est, draws=boot_draws) if m > 1 else math.inf
    return R.decide(spec, n=n, N=N, value=value, se=se, c_kill=c_kill, c_settle=c_settle)


def _measure_one(args):
    measure, payload, name, seed, settings = args
    return measure(payload, name, seed, settings)


def run(candidates: Sequence[Candidate], protocols: Sequence[Protocol],
        rows: Sequence[RowSpec], *, cache: ResultCache,
        measure: Callable[[Any, str, Any, Mapping], Any], settings: Mapping | None = None,
        stage: str = "B", n0: int = 10, N: int = 30, block: int = 10,
        gamma: float = R.GAMMA, gamma_settle: float = R.GAMMA_SETTLE,
        verify: bool = False, workers: int = 1, log: Callable[[str], None] = print,
        ) -> list[Outcome]:
    """Run one stage for every candidate; see the module docstring."""
    if stage not in ("A", "B"):
        raise ValueError(stage)
    settings = dict(settings or {})
    by_name = {p.name: p for p in protocols}
    outs: dict[str, Outcome] = {}
    plan: dict[str, dict] = {}            # label -> {"rows", "seeds": {proto: n}, "free": set}
    for c in candidates:
        o = Outcome(c.label)
        if c.baseline is not None:
            o.changed = dial_reach.changed_dials(c.values, c.baseline[2])
            reach = dial_reach.reachable(o.changed, {p.name: p.opens for p in protocols})
            if c.baseline[0] != c.build:
                # Another build: only the reach map and verify mode vouch for
                # reading its entries, and a new switch is changed by definition.
                log(f"{c.label}: baseline build {c.baseline[0][:12]} is not this build "
                    f"{c.build[:12]}; unreachable rows read across builds")
        else:
            reach = set(by_name)
        o.reachable = sorted(reach)
        if stage == "A":
            live = [r for r in rows if set(r.protocols) & reach]
            if c.targets is not None:
                live = [r for r in live if r.id in c.targets]
        else:
            live = list(rows)
        o.rows = [r.id for r in live]
        protos = sorted({p for r in live for p in r.protocols})
        seeds = {}
        free = set()
        for p in protos:
            full = len(by_name[p].seeds)
            stop = min(N, full)
            if verify:
                seeds[p] = stop
            elif p not in reach and c.baseline is not None:
                seeds[p] = stop           # baseline reads cost nothing
                free.add(p)
            else:
                seeds[p] = min(n0, stop)
        plan[c.label] = {"rows": live, "seeds": seeds, "free": free}
        outs[c.label] = o
        log(f"{c.label}: stage {stage}, {len(live)} rows on {len(protos)} protocols; "
            f"changed {o.changed or '(none)'}; reachable {o.reachable}")

    readings: dict[str, dict[str, dict]] = {c.label: {} for c in candidates}
    stage_N = {c.label: {p: min(N, len(by_name[p].seeds)) for p in plan[c.label]["seeds"]}
               for c in candidates}
    # Interim looks at which a row can be killed or settled. The look at N
    # itself is exact (stage B's own verdict), so it is not counted.
    looks = 1 if stage == "A" else max(1, math.ceil((N - n0) / block))
    reach_of = {c.label: set(outs[c.label].reachable) for c in candidates}
    active = {c.label for c in candidates}
    while active:
        # 1. Gather every (candidate, protocol, seed) this look needs.
        needs = []                     # (c, p, s, own, base, own_hit)
        for c in candidates:
            if c.label not in active:
                continue
            o, pl = outs[c.label], plan[c.label]
            for p, n in pl["seeds"].items():
                proto = by_name[p]
                have = readings[c.label].setdefault(p, {})
                for s in proto.seeds[:n]:
                    if s in have:
                        continue
                    own = Key(c.build, c.params, proto.cache_id, s)
                    base = (Key(c.baseline[0], c.baseline[1], proto.cache_id, s)
                            if c.baseline is not None and p not in reach_of[c.label]
                            else None)
                    if base == own:
                        base = None
                    hit = cache.get(own)
                    if not verify and hit is not None:
                        have[s] = hit
                        o.counts["cached"] += 1
                        continue
                    needs.append((c, p, s, own, base, hit))
        # A need whose baseline is cached reads it. One whose baseline another
        # candidate measures in this look (the baseline's own run, or a twin
        # with the same coefficients) waits for that reading. The rest run,
        # once per key however many candidates share it.
        measured_keys = {}
        links = []
        own_needs = {n[3] for n in needs}
        for c, p, s, own, base, hit in needs:
            bhit = cache.get(base) if base is not None else None
            if not verify and bhit is not None:
                readings[c.label][p][s] = bhit
                outs[c.label].counts["baseline"] += 1
                continue
            if not verify and base is not None and base in own_needs:
                links.append((c, p, s, base))
                continue
            expect = (own, hit) if hit is not None else ((base, bhit) if bhit is not None else None)
            measured_keys.setdefault(own, []).append((c, p, s, expect))
        jobs, slots = [], []
        for key, users in measured_keys.items():
            c, p, s, _ = users[0]
            jobs.append((measure, c.payload, p, s, settings))
            slots.append((key, users))
        # 2. Measure them in one pool, so every core works across candidates.
        fresh = {}
        if jobs:
            log(f"  measuring {len(jobs)} task(s) on {workers} worker(s)")
            if workers > 1:
                with ProcessPoolExecutor(workers) as ex:
                    results = list(ex.map(_measure_one, jobs))
            else:
                results = [_measure_one(j) for j in jobs]
            for (own, users), value in zip(slots, results):
                fresh[own] = value
                for c, p, s, expect in users:
                    o = outs[c.label]
                    if expect is not None:
                        diff = differences(expect[1], value)
                        if diff:
                            o.disagreements.append({
                                "protocol": p, "seed": s,
                                "against": "own entry" if expect[0] == own else "baseline entry",
                                "differences": diff[:5]})
                    readings[c.label][p][s] = value
                    o.counts["measured"] += 1
                if cache.get(own) is None:
                    cache.put(own, value, meta={"label": users[0][0].label})
        for c, p, s, base in links:
            readings[c.label][p][s] = fresh[base]
            outs[c.label].counts["baseline"] += 1
        # 3. Decide every live row, then extend, stop or kill.
        for c in candidates:
            if c.label not in active:
                continue
            o, pl = outs[c.label], plan[c.label]
            K = max(1, len(pl["rows"]))
            ds, by_proto = [], {p: [] for p in pl["seeds"]}
            for r in pl["rows"]:
                n_r = min(pl["seeds"][p] for p in r.protocols)
                N_r = min(stage_N[c.label][p] for p in r.protocols)
                rd = {p: [readings[c.label][p][s] for s in by_name[p].seeds[:n_r]]
                      for p in r.protocols}
                c_kill = R.kill_threshold(n_r, N_r, rows=K, looks=looks, gamma=gamma) \
                    if n_r < N_r else 0.0
                c_set = R.settle_threshold(n_r, N_r, rows=K, looks=looks, gamma=gamma_settle) \
                    if n_r < N_r else 0.0
                d = row_state(r, rd, n=n_r, N=N_r, c_kill=c_kill, c_settle=c_set)
                ds.append(d)
                for p in r.protocols:
                    by_proto[p].append(d)
            o.decisions = ds
            o.seeds = dict(pl["seeds"])
            state = R.candidate_state(ds)
            if state == "dead":
                o.state = "dead"
                active.discard(c.label)
                continue
            if stage == "A":
                o.state = "survives" if state != "dead" else "dead"
                active.discard(c.label)
                continue
            grow = {}
            for p, dlist in by_proto.items():
                n = pl["seeds"][p]
                if n < stage_N[c.label][p] and any(d.state == "open" for d in dlist):
                    grow[p] = min(stage_N[c.label][p], n + block)
            if not grow:
                o.state = "passes" if state == "settled" else "open"
                active.discard(c.label)
                continue
            pl["seeds"].update(grow)
    return [outs[c.label] for c in candidates]


def report(o: Outcome) -> str:
    lines = [f"=== {o.label}: {o.state.upper()}  readings measured {o.counts['measured']}, "
             f"cached {o.counts['cached']}, baseline {o.counts['baseline']}",
             f"  changed {o.changed or '-'}; reachable protocols {o.reachable or '-'}",
             "  seeds " + ", ".join(f"{p} {n}" for p, n in sorted(o.seeds.items()))]
    for d in o.decisions:
        v = "-" if d.value is None else f"{d.value:.4g}"
        se = "" if d.se is None or not math.isfinite(d.se) else f" se {d.se:.3g}"
        lines.append(f"  {d.state:4s} {d.row:40s} {v}{se}  n {d.n}/{d.N}  {d.why}")
    for x in o.disagreements:
        lines.append(f"  DISAGREES {x['protocol']} seed {x['seed']} vs {x['against']}: "
                     f"{'; '.join(x['differences'])}")
    return "\n".join(lines)


def outcome_json(o: Outcome) -> dict:
    return {"label": o.label, "state": o.state, "seeds": o.seeds, "counts": o.counts,
            "changed": o.changed, "reachable": o.reachable, "rows": o.rows,
            "decisions": [dataclasses.asdict(d) for d in o.decisions],
            "disagreements": o.disagreements}


def cached_only(protocols: Iterable[Protocol], rows: Iterable[RowSpec]) -> list[str]:
    """Rows no protocol feeds: a definition error worth refusing early."""
    names = {p.name for p in protocols}
    return [r.id for r in rows if not set(r.protocols) <= names]
