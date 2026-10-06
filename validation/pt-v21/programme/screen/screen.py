"""screen.py: the row-reach map, staged screen plans, stage decisions and costs.

    python programme/screen/screen.py reach --changed DIAL[,DIAL...] [--mechanism ID ...]
    python programme/screen/screen.py reach --arm 'NAME@BASE:d=v,...' --baseline-arm 'BASE@BASE:...'
    python programme/screen/screen.py map [--out programme/screen/reach-map.json]
    python programme/screen/screen.py plan --arms ARMS --baseline-arm LINE --stage A|B [--n 10] [--set A|B] --out PLAN
    python programme/screen/screen.py cost (--plan PLAN | --stage full|A|B --arms-count N) [--cap DOLLARS]
    python programme/screen/screen.py decide READINGS.json --stage A|B [--n 10] [--N 30] [--out NEXT.json]
    python programme/screen/screen.py verify MERGED_BOX --arm NAME --baseline NAME --stages S1,S2

The data is in three files beside this one: rows.json (the 148 gated rows,
their stages and channels), stages.json (the grade job split into stages,
with seeds and timings) and reach.json (dial and mechanism entries). The
statistics are lib/staged_rule.py and the cache key is lib/result_cache.py,
both copied from the engine's tools/calibration (lib/VENDORED.txt).

Nothing here launches a box or touches S3. `plan` writes the shard list a
screen box runs (box/screen_box.py); `cost` prices it before launch.

A phase's screens, in order:

1. `reach` for each candidate: the rows its changed dials can move, and its
   stage-A target rows.
2. `plan --stage A` for every candidate of the phase in one file, then
   `cost --plan` against the phase's cap, then `box.sh RUN BRANCH PIN PLAN`
   (one box; `split` shares a big plan across several). The box reads every
   shard it can from the cache (out/cache/ in the bucket) and measures the
   rest, uploading each shard as it finishes.
3. On the desk: collect, grade the merged folder as a grade box is graded
   (r13reg/grade_all.py, margins.py), `readings.py` per arm, then
   `decide --stage A`: a candidate with a row clearly out is dead.
4. `plan --stage B` for the survivors (and the baseline, whose shards are
   cache hits after the first phase), `decide --stage B` after each look;
   `next_n` names the stages that need more seeds, and the next plan runs
   only those.
5. The last stage-B screen of a phase runs with SCREEN_VERIFY=1 on every
   stage, and `verify` compares each candidate's unreachable stages with the
   baseline's: a difference is a bug in reach.json, and the screens that
   read the baseline for those rows are re-run.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import pathlib
import subprocess
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "lib"))
import staged_rule as R  # noqa: E402

ROWS = json.loads((HERE / "rows.json").read_text(encoding="utf-8"))
STAGES = json.loads((HERE / "stages.json").read_text(encoding="utf-8"))
REACH = json.loads((HERE / "reach.json").read_text(encoding="utf-8"))
#: The 148 rows the pt-v20 grade gated, and the pt-v21 rows registered since
#: (rows.json "pending_rows"), which the screen measures in its own stages.
GRADED_ROWS = [r["id"] for r in ROWS["rows"]]
ROW_BY_ID = {r["id"]: r for r in ROWS["rows"] + ROWS.get("pending_rows", [])}
ALL_ROWS = GRADED_ROWS + [r["id"] for r in ROWS.get("pending_rows", [])]
STAGE_ORDER = list(STAGES["stages"])


# -- reach -------------------------------------------------------------------

def dial_entry(dial: str) -> dict:
    """A dial's entry; a dial with none acts on every market."""
    mech = next(({**m, "mechanism": mid} for mid, m in REACH["mechanisms"].items()
                 if dial in m.get("dials", ())), None)
    if dial in REACH["dials"]:
        e = dict(REACH["dials"][dial])
        if "targets" not in e and mech and mech.get("targets"):
            e["targets"] = mech["targets"]       # the mechanism's stage-A rows
        return e
    if mech:
        return mech
    return {"channel": "market", "reason": "no entry: assumed to reach every row"}


def rows_of_entry(entry: dict) -> list[str]:
    ch = entry["channel"]
    rows = [r["id"] for r in ROWS["rows"] + ROWS.get("pending_rows", []) if ch in r["channels"]]
    rows += [r for r in entry.get("rows", ()) if r not in rows]
    return [r for r in ALL_ROWS if r in rows]


def entry_stages(entry: dict, rows) -> set[str]:
    """The stages an entry must re-measure for the given rows it reaches.

    A row reached through a channel needs only its stages that open that
    channel; a row the entry names explicitly needs all of its stages.
    """
    ch = entry["channel"]
    explicit = set(entry.get("rows", ()))
    out = set()
    for r in rows:
        for s in ROW_BY_ID[r]["stages"]:
            if r in explicit or ch in STAGES["stages"][s]["channels"]:
                out.add(s)
    return out


def reach(changed: list[str], mechanisms: list[str] = ()) -> dict:
    """Rows and stages the changed dials (and named mechanisms) can move."""
    entries = [(d, dial_entry(d)) for d in changed]
    entries += [(m, REACH["mechanisms"][m]) for m in mechanisms]
    reached: dict[str, list[str]] = {}
    targets: list[str] = []
    stages: set[str] = set()
    target_stages: set[str] = set()
    for name, e in entries:
        rs = rows_of_entry(e)
        for r in rs:
            reached.setdefault(r, []).append(name)
        stages |= entry_stages(e, rs)
        tg = e.get("targets")
        tg = [r for r in (tg if tg else rs) if r in rs]
        target_stages |= entry_stages(e, tg)
        targets += [r for r in tg if r not in targets]
    rows = [r for r in ALL_ROWS if r in reached]
    return {"changed": changed, "mechanisms": list(mechanisms), "rows": rows,
            "by_row": reached, "targets": [r for r in ALL_ROWS if r in targets],
            "stages": order(stages), "target_stages": order(target_stages),
            "unreached_rows": [r for r in ALL_ROWS if r not in reached],
            "unreached_stages": [s for s in STAGE_ORDER if s not in order(stages)]}


def order(stages) -> list[str]:
    need = set(stages)
    if "c10" in need:
        need.add("box")              # c10 reads the box stage's long run
    return [s for s in STAGE_ORDER if s in need]


def stages_for(rows) -> list[str]:
    return order({s for r in rows for s in ROW_BY_ID[r]["stages"]})


def parse_arm(line: str) -> tuple[str, str | None, dict[str, float]]:
    """'NAME@BASE:d=v,...' -> (NAME, BASE or None, {d: v})."""
    line = line.split("#", 1)[0].strip()
    head, _, body = line.partition(":")
    name, _, base = head.partition("@")
    dials = {}
    for kv in body.split(","):
        if kv.strip():
            k, _, v = kv.partition("=")
            dials[k.strip()] = float(v)
    return name.strip(), (base.strip() or None), dials


def changed_between(arm: str, baseline: str) -> tuple[list[str], str | None]:
    """Dials that differ between two arm lines; a different base preset moves
    every dial it sets, so it is reported as the whole-model change it is."""
    _, b1, d1 = parse_arm(arm)
    _, b2, d2 = parse_arm(baseline)
    if b1 != b2:
        return ["*base*"], f"bases differ ({b1} against {b2}): every row is reached"
    out = sorted(k for k in set(d1) | set(d2) if d1.get(k, 0.0) != d2.get(k, 0.0))
    return out, None


def reach_map() -> dict:
    out = {"what": "Generated by screen.py map from reach.json and rows.json: every dial "
                   "with an entry, the rows it can move and the stages that measure them. "
                   "A dial not listed reaches all 148 rows.",
           "rows_total": len(ALL_ROWS), "dials": {}, "mechanisms": {}}
    for d, e in REACH["dials"].items():
        rs = rows_of_entry(e)
        out["dials"][d] = {"channel": e["channel"], "reason": e["reason"], "rows": rs,
                           "stages": order(entry_stages(e, rs)), "targets": e.get("targets", rs)}
    for m, e in REACH["mechanisms"].items():
        rs = rows_of_entry(e)
        out["mechanisms"][m] = {"dials": e.get("dials", []), "channel": e["channel"],
                                "reason": e["reason"], "rows": rs, "stages": order(entry_stages(e, rs)),
                                "targets": e.get("targets", []), "new_rows": e.get("new_rows", [])}
    return out


# -- seeds -------------------------------------------------------------------

def jobs_text(ref: str | None = None) -> str:
    """The grade job, from git (default the ref stages.json names)."""
    ref = ref or STAGES["jobs_ref"]
    root = HERE.parent.parent
    return subprocess.run(["git", "-C", str(root), "show", f"{ref}:{STAGES['jobs_file']}"],
                          check=True, capture_output=True, text=True).stdout


def seed_defaults(text: str) -> dict[str, str]:
    """Each seed variable's held-out default, from the job's own export lines."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("export ") and ":-" in line and "${" in line:
            var = line.split()[1].split("=")[0]
            val = line.split(":-", 1)[1].split("}", 1)[0]
            out[var] = val
    return out


def parse_seeds(spec: str) -> list[int]:
    out = []
    for part in str(spec).replace(" ", "").split(","):
        if part:
            lo, _, hi = part.partition("-")
            out += range(int(lo), int(hi or lo) + 1)
    return out


def format_seeds(seeds: list[int]) -> str:
    out, i = [], 0
    while i < len(seeds):
        j = i
        while j + 1 < len(seeds) and seeds[j + 1] == seeds[j] + 1:
            j += 1
        out.append(str(seeds[i]) if i == j else f"{seeds[i]}-{seeds[j]}")
        i = j + 1
    return ",".join(out)


def shift(spec: str, by: int) -> str:
    return format_seeds([s + by for s in parse_seeds(spec)])


def stage_seed_env(stage: str, n: int | None, defaults: dict[str, str], *,
                   set_offset: int = 0) -> dict[str, str]:
    """The seed variables for a stage at n seeds per list (None: the screen's full count).

    A list variable keeps its first n seeds, so a smaller run's seeds are the
    start of a larger one's and the staged rule's nested argument holds. A
    pooled list (more than 90 seeds) keeps the same share of its seeds as n
    is of 30. A start variable cannot shrink: its count is in the stage's own
    lines.
    """
    env = {}
    for var, spec in STAGES["stages"][stage]["seed_vars"].items():
        val = defaults.get(var, spec.get("default"))
        if set_offset:
            val = shift(val, set_offset) if not val.isdigit() else str(int(val) + set_offset)
        if n is not None and spec["scalable"]:
            seeds = parse_seeds(val)
            keep = n if len(seeds) <= 90 else math.ceil(len(seeds) * n / 30)
            val = format_seeds(seeds[:min(len(seeds), keep)])
        env[var] = val
    return env


def seed_fraction(stage: str, env: dict[str, str], defaults: dict[str, str]) -> float:
    """The share of the stage's full-screen work a seed environment asks for."""
    vs = STAGES["stages"][stage]["seed_vars"]
    if not vs:
        return 1.0
    fr = []
    for var, spec in vs.items():
        if spec["scalable"]:
            fr.append(len(parse_seeds(env[var])) / len(parse_seeds(defaults.get(var, spec.get("default")))))
        else:
            fr.append(1.0)
    return sum(fr) / len(fr)


# -- plans and cost ------------------------------------------------------------

#: Marks a stage that keeps the registered screen's own seed counts.
REGISTERED = object()


def plan(arms: list[str], baseline_arm: str, stage: str, *, n: int | None = 10,
         defaults: dict[str, str], set_offset: int = 0, targets_only: bool = True,
         cached: set | None = None, next_n: dict | None = None,
         every_stage: bool = False, only: list | None = None,
         full_stages: list | None = None, skip: list | None = None,
         registered: dict | None = None) -> dict:
    """The shards one screen session runs.

    Stage A: for each candidate, only the stages its target rows need, at n
    seeds. Stage B: every stage the candidate's changes reach, at n seeds per
    list (30 by the plan; None is the registered screen's own counts); stages
    they cannot reach are read from the baseline's cache entries, so the
    baseline arm runs every stage once.
    """
    shards, notes = [], []
    base_name = parse_arm(baseline_arm)[0]
    cached = cached or set()
    want: dict[str, set] = {}
    for i, line in enumerate([baseline_arm] + arms):
        name = parse_arm(line)[0]
        if i == 0:
            st = [] if stage == "A" else STAGE_ORDER
            why = "baseline: every stage once, so candidates can read what they do not reach"
        else:
            changed, wide = changed_between(line, baseline_arm)
            r = reach([] if wide else changed) if not wide else reach(["*base*"])
            st = (r["target_stages"] if targets_only else r["stages"]) if stage == "A" else r["stages"]
            why = wide or f"changed {changed}; reaches {len(r['rows'])} rows"
        want[name] = set(STAGE_ORDER) if every_stage else set(st)
        if skip:
            want[name] -= set(skip)
        if only is not None:
            want[name] &= set(only)
        notes.append({"arm": name, "stages": [s for s in STAGE_ORDER if s in want[name]], "why": why})
        if next_n is not None and i > 0:
            # A later look: only the stages decide named for this arm, at their counts.
            want[name] &= set(next_n.get(name, {}))
        for s in STAGE_ORDER:
            if s not in want[name]:
                continue
            look_n = next_n.get(name, {}).get(s) if next_n is not None and i > 0 else None
            if look_n is None and full_stages and s in full_stages:
                look_n = REGISTERED      # the screen's own counts for this stage
            env = stage_seed_env(s, None if look_n is REGISTERED else (look_n if look_n is not None else n),
                                 defaults, set_offset=set_offset)
            if registered is not None:
                # A registered grade: every shard carries every exam variable and REGISTERED_GRADE, so the
                # job's seed-plan check sees exactly grade-seeds.json whatever the stage runs.
                env = dict(registered)
            key = (name, s, json.dumps(env, sort_keys=True))
            if key in cached:
                continue
            shards.append({"arm": name, "line": line, "stage": s, "env": env,
                           "fraction": round(seed_fraction(s, env, defaults), 4)})
    # c10 reads the box stage's output, so it runs after it in the same session.
    shards.sort(key=lambda x: (STAGE_ORDER.index(x["stage"]), x["arm"]))
    return {"stage": stage, "n": n, "baseline": base_name,
            "set_offset": set_offset, "arms": notes, "shards": shards}


def shard_cost(sh: dict) -> float:
    """Core-minutes on a c8g core for one shard, from g15's stage minutes."""
    st = STAGES["stages"][sh["stage"]]
    return st["g15_minutes"] * st["cores_per_arm"] * sh["fraction"]


def shard_minutes(sh: dict, cores: int, efficiency: float, parallel: int) -> float:
    """A shard's box-minutes.

    Packed (parallel > 1): its core-minutes over the box's cores. One shard at
    a time (parallel 1, screen_box.py's default): at least the stage's floor,
    the wall time of one wave of its longest task (stages.json, measured on
    box v21v2), because a stage on few seeds cannot fill the box.
    """
    packed = shard_cost(sh) / (cores * efficiency)
    if parallel > 1:
        return packed
    floor = STAGES["stages"][sh["stage"]].get("floor_minutes_n10") or 0.0
    return max(packed, floor)


def cost(shards: list[dict], *, box: str = "c8g.16xlarge", boxes: int = 1,
         efficiency: float = 0.85, wheel: bool = True, parallel: int = 1) -> dict:
    """Box-minutes and dollars for a shard list.

    Core-minutes are g15's stage minutes times the cores one arm keeps busy
    times the seed fraction. With shards run one at a time each takes at
    least its stage's floor (`shard_minutes`); packed, they share `boxes`
    boxes at `efficiency` of their cores. Each box adds its boot.
    """
    cores = STAGES["box"]["cores"][box]
    price = STAGES["box"]["price_per_hour"][box]
    core_min = sum(shard_cost(s) for s in shards)
    boot = STAGES["box"]["boot_minutes_wheel" if wheel else "boot_minutes_build"]
    run_min = sum(shard_minutes(s, cores, efficiency, parallel) for s in shards)
    box_min = run_min + boot * boxes if shards else 0.0
    by_stage = {}
    for s in shards:
        by_stage[s["stage"]] = by_stage.get(s["stage"], 0.0) + shard_minutes(s, cores, efficiency, parallel)
    return {"box": box, "boxes": boxes, "core_minutes": round(core_min, 1),
            "box_minutes": round(box_min, 1), "dollars": round(box_min / 60 * price, 2),
            "by_stage_box_minutes": {k: round(v, 1) for k, v in by_stage.items()},
            "price_per_hour": price, "efficiency": efficiency, "parallel": parallel}


def full_shards(arms_count: int, stage: str, n: int, defaults: dict[str, str],
                stages: list[str] | None = None) -> list[dict]:
    out = []
    for a in range(arms_count):
        for s in stages or STAGE_ORDER:
            env = stage_seed_env(s, None if stage == "full" else n, defaults)
            out.append({"arm": f"arm{a}", "stage": s, "env": env,
                        "fraction": seed_fraction(s, env, defaults)})
    return out


def split(pl: dict, boxes: int) -> list[dict]:
    """Share a plan's shards across boxes, longest first onto the least loaded.

    An arm's c10 and xsec shards ride with its box shard, whose long run they read.
    """
    groups: dict[tuple, list] = {}
    for sh in pl["shards"]:
        key = (sh["arm"], "box") if sh["stage"] in ("box", "c10", "xsec") else (sh["arm"], sh["stage"], id(sh))
        groups.setdefault(key, []).append(sh)
    cores = STAGES["box"]["cores"]["c8g.16xlarge"]

    def minutes(g):          # what the box spends on the group, floors included
        return sum(shard_minutes(x, cores, 0.85, 1) for x in g)
    loads = [[0.0, []] for _ in range(boxes)]
    for g in sorted(groups.values(), key=lambda g: -minutes(g)):
        tgt = min(loads, key=lambda x: x[0])
        tgt[0] += minutes(g)
        tgt[1] += g
    out = []
    for i, (load, shards) in enumerate(loads):
        shards.sort(key=lambda x: (STAGE_ORDER.index(x["stage"]), x["arm"]))
        part = {**pl, "shards": shards, "part": f"{i + 1}/{boxes}"}
        part["estimate"] = cost(shards)
        out.append(part)
    return out


# -- decisions -----------------------------------------------------------------

def baseline_passes(reading: dict) -> bool | None:
    """Whether a baseline reading sits inside its row's band (None: cannot tell)."""
    meta = ROW_BY_ID.get(reading["id"])
    if meta is None:
        return None
    if reading.get("pass") is not None and (reading.get("se") is None or meta["kind"] in ("allseeds", "count")):
        return bool(reading["pass"])
    lo, hi = _band_edges(reading.get("band") or meta["band"])
    v = reading.get("value")
    if v is None or (lo is None and hi is None) or not isinstance(v, (int, float)) or math.isnan(v):
        return None
    return (lo is None or v >= lo) and (hi is None or v <= hi)


def judged_rows(readings: list[dict], targets: list[str] | None,
                baseline: list[dict] | None) -> tuple[list[dict], list[dict]]:
    """Split a candidate's readings into the rows it is judged on and the rest.

    A candidate is judged on (a) its target rows and (b) rows the baseline
    passes, where a miss would be a regression it caused. A row the baseline
    already fails, and the candidate does not target, belongs to another
    mechanism: it is reported, never judged. With no targets and no baseline
    every row is judged, as before.
    """
    if targets is None and baseline is None:
        return list(readings), []
    tg = set(targets or ())
    base = {b["id"]: baseline_passes(b) for b in (baseline or [])}
    judged, reported = [], []
    for r in readings:
        rid = r["id"]
        if rid in tg:
            judged.append(r)
        elif base.get(rid) is True:
            judged.append({**r, "regression_check": True})
        else:
            why = ("the baseline fails it too; not this candidate's row" if base.get(rid) is False
                   else "no baseline reading to call a miss a regression")
            reported.append({**r, "why_not_judged": why})
    return judged, reported


def decide(readings: list[dict], *, stage: str, n: int, N: int, block: int = 10,
           gamma: float = R.GAMMA, gamma_settle: float = R.GAMMA_SETTLE,
           margin_se: float = 0.0, targets: list[str] | None = None,
           baseline: list[dict] | None = None, complete: bool = False) -> dict:
    """Kill, settle or extend, row by row, from one look's readings.

    Each reading is {"id", "value", "se", "n", "bad", "pass"}. A mean or
    pooled row with an se is judged by staged_rule; count and every-seed rows
    by their exact rules; a row read whole by its grader (composite, or no
    se) cannot kill before the full block, and stays open until then. With
    `targets` and `baseline` only the rows `judged_rows` picks can kill.
    """
    looks = 1 if stage == "A" else max(1, math.ceil((N - n) / block))
    picked, reported = judged_rows([r for r in readings if r["id"] in ROW_BY_ID], targets, baseline)
    live = picked
    K = max(1, len(live))
    out = []
    for r in live:
        meta = ROW_BY_ID[r["id"]]
        kind = meta["kind"]
        # complete: every reading is its row's full block (a row's own seed count may
        # be below N, as the scenario desk's 12), so each is decided on its reading.
        n_r = N if complete else (r.get("n") or n)
        band = r.get("band") or meta["band"]
        lo, hi = _band_edges(band)
        c_k = R.kill_threshold(n_r, N, rows=K, looks=looks, gamma=gamma) if n_r < N else 0.0
        c_s = R.settle_threshold(n_r, N, rows=K, looks=looks, gamma=gamma_settle) if n_r < N else 0.0
        if kind in ("count",) and r.get("bad") is not None:
            d = R.decide(R.Row(r["id"], "count", max_bad_share=r.get("max_bad_share", 1 / 30)),
                         n=n_r, N=N, bad=r["bad"], c_kill=c_k, c_settle=c_s)
        elif kind == "allseeds" and r.get("bad") is not None:
            d = R.decide(R.Row(r["id"], "allseeds"), n=n_r, N=N, bad=r["bad"], c_kill=c_k, c_settle=c_s)
        elif kind == "deterministic" and r.get("pass") is not None:
            d = R.Decision(r["id"], "in" if r["pass"] else "out", n_r, N, r.get("value"), None,
                           "no seed noise")
        elif kind in ("mean", "pooled") and r.get("se") is not None and (lo is not None or hi is not None):
            d = R.decide(R.Row(r["id"], "mean", lo, hi, margin_se), n=n_r, N=N,
                         value=r.get("value"), se=r["se"], c_kill=c_k, c_settle=c_s)
        else:
            state = "open" if n_r < N else ("in" if r.get("pass") else "out")
            d = R.Decision(r["id"], state, n_r, N, r.get("value"), r.get("se"),
                           "read whole by its grader" if n_r < N else "full block")
        out.append(d)
    state = R.candidate_state(out)
    grow = {}
    if state != "dead":
        for d in out:
            if d.state == "open" and d.n < N:
                for s in ROW_BY_ID[d.row]["stages"]:
                    grow[s] = max(grow.get(s, 0), min(N, d.n + block))
    return {"state": state, "decisions": [vars(d) for d in out], "next_n": grow,
            "kill_line": R.kill_threshold(n, N, rows=K, looks=looks, gamma=gamma) if n < N else 0.0,
            "settle_line": R.settle_threshold(n, N, rows=K, looks=looks, gamma=gamma_settle) if n < N else 0.0,
            "rows_judged": K, "looks": looks,
            "regression_rows": [r["id"] for r in picked if r.get("regression_check")],
            "reported": [{"row": r["id"], "value": r.get("value"), "why": r["why_not_judged"]}
                         for r in reported]}


def _band_edges(band) -> tuple:
    """(lo, hi) from a rows.json band, when it is one numeric interval."""
    if isinstance(band, list) and len(band) == 2 and all(
            b is None or isinstance(b, (int, float)) for b in band):
        return band[0], band[1]
    if isinstance(band, list) and len(band) == 1 and isinstance(band[0], list):
        return tuple(band[0])
    if isinstance(band, str):
        # ">= -13", "<= 0.1": a one-sided band written as text in the verdict.
        b = band.strip()
        for op, side in ((">=", 0), ("<=", 1)):
            if b.startswith(op):
                try:
                    x = float(b[2:].strip())
                except ValueError:
                    return None, None
                return (x, None) if side == 0 else (None, x)
    return None, None


# -- verify --------------------------------------------------------------------

def stage_files(box: pathlib.Path, stage: str, arm: str) -> dict[str, str]:
    """A digest of every per-arm output of a stage, less what names the arm.

    JSON is compared after dropping the keys that identify an arm (its dials,
    fingerprint, label); .npz by its arrays, since the zip's own bytes carry
    write times; anything else by its bytes with the arm's name taken out.
    """
    out = {}
    for pat in STAGES["stages"][stage]["arm_paths"]:
        glob = pat.replace("{ARM}", arm)
        for p in sorted(box.glob(glob)):
            files = [p] if p.is_file() else sorted(q for q in p.rglob("*") if q.is_file())
            for f in files:
                rel = str(f.relative_to(box)).replace(arm, "{ARM}")
                if f.suffix in (".log", ".txt") or f.name.endswith(".err"):
                    continue          # logs carry times and names
                out[rel] = _content_digest(f, arm)
    return out


def _content_digest(f: pathlib.Path, arm: str) -> str:
    if f.suffix == ".json":
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            pass
        else:
            text = json.dumps(_drop_identity(d), sort_keys=True, default=str)
            return hashlib.sha256(text.replace(arm, "{ARM}").encode()).hexdigest()
    if f.suffix == ".npz":
        import numpy as np  # noqa: PLC0415
        h = hashlib.sha256()
        with np.load(f, allow_pickle=False) as z:
            for k in sorted(z.files):
                if k in IDENTITY_ARRAYS:
                    continue
                a = z[k]
                h.update(k.encode() + str(a.dtype).encode() + str(a.shape).encode())
                h.update(_strip_arm(a.tobytes(), arm) if a.dtype.kind in "SU" else a.tobytes())
        return h.hexdigest()
    return hashlib.sha256(_strip_arm(f.read_bytes(), arm)).hexdigest()


def _strip_arm(data: bytes, arm: str) -> bytes:
    return data.replace(arm.encode(), b"{ARM}")


def arm_json(box: pathlib.Path, arm: str) -> dict[str, object]:
    """Every arm-keyed JSON entry the stages write into shared files."""
    out = {}
    for f in sorted(box.glob("*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        for key in ("arms", "per_seed", "per_arm"):
            if isinstance(d, dict) and isinstance(d.get(key), dict) and arm in d[key]:
                out[f"{f.name}:{key}"] = d[key][arm]
        if isinstance(d, dict) and arm in d and isinstance(d[arm], dict):
            out[f"{f.name}:top"] = d[arm]
        if isinstance(d, dict) and isinstance(d.get("histories"), list):
            out[f"{f.name}:histories"] = [h for h in d["histories"] if h.get("arm") == arm]
    return out


SHARED_FILE_STAGE = {"edge.json": "edge", "c4a.json": "c4", "c4b.json": "c4", "xsec.json": "xsec",
                     "driven2020.json": "driven2020", "driven2022.json": "driven2022",
                     "c10.json": "c10", "r7-event.json": "r7", "r7-eval.json": "r7",
                     "recession.json": "recession", "probe.json": "probe", "sf.json": "sf",
                     "sf-rec.json": "sf", "r7pre.json": "r7pre"}


def verify(box: pathlib.Path, arm: str, baseline: str, stages: list[str]) -> list[dict]:
    """Every output of the named stages on which the arm and the baseline differ.

    The stages are the ones the reach map says the arm's changes cannot
    reach, measured anyway on a verify run. Their outputs must be the
    baseline's bit for bit, apart from the arm's name and its dial list;
    any difference names a row the map wrongly marks unreachable.
    """
    bad = []
    for s in stages:
        a, b = stage_files(box, s, arm), stage_files(box, s, baseline)
        for rel in sorted(set(a) | set(b)):
            if a.get(rel) != b.get(rel):
                bad.append({"stage": s, "file": rel, "rows": _rows_of_stage(s)})
    ja, jb = arm_json(box, arm), arm_json(box, baseline)
    for k in sorted(set(ja) | set(jb)):
        st = SHARED_FILE_STAGE.get(k.split(":")[0])
        if st not in stages:
            continue
        x, y = _drop_identity(ja.get(k)), _drop_identity(jb.get(k))
        if x != y:
            bad.append({"stage": st, "file": k, "rows": _rows_of_stage(st)})
    return bad


#: Keys that name an arm or time a run rather than measure it. The first
#: verify run on a box (v21v2, 2026-10-04) read 244 false disagreements from
#: the last three: the per-seed `model_fingerprint` the cert cells and the
#: D1 pool record, and the `elapsed_s` wall times of the long run and the
#: crisis extension.
IDENTITY_KEYS = ("dials", "fingerprint", "arm", "name", "line", "over", "overrides",
                 "changed_dials", "label", "model_fingerprint", "elapsed_s", "elapsed")

#: Arrays in an .npz that name the arm: r14gen.py stores the arm's dial list
#: and `Engine.state_hash()`, which folds in the model's digest. Every
#: market array beside them is still compared.
IDENTITY_ARRAYS = ("dials", "hash")


def _drop_identity(v):
    """An arm's entry less what names it: its dials, fingerprint and label."""
    if isinstance(v, dict):
        return {k: _drop_identity(x) for k, x in v.items()
                if k not in IDENTITY_KEYS}
    if isinstance(v, list):
        return [_drop_identity(x) for x in v]
    return v


def _rows_of_stage(stage: str) -> list[str]:
    return [r["id"] for r in ROWS["rows"] + ROWS.get("pending_rows", []) if stage in r["stages"]]


# -- the command line ------------------------------------------------------------

def _print_reach(r: dict) -> None:
    print(f"changed: {', '.join(r['changed']) or '-'}"
          f"{'  mechanisms: ' + ', '.join(r['mechanisms']) if r['mechanisms'] else ''}")
    for d in r["changed"]:
        e = dial_entry(d)
        print(f"  {d}: channel {e['channel']}: {e['reason']}")
    print(f"rows to measure: {len(r['rows'])} of {len(ALL_ROWS)}")
    print(f"  stages: {', '.join(r['stages']) or '-'}")
    print(f"  stage-A targets ({len(r['targets'])}): {', '.join(r['targets']) or '-'}")
    print(f"  stage-A stages: {', '.join(r['target_stages']) or '-'}")
    print(f"read from the baseline's cache: {len(r['unreached_rows'])} rows, "
          f"stages {', '.join(r['unreached_stages']) or '-'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("reach")
    a.add_argument("--changed", default="")
    a.add_argument("--mechanism", action="append", default=[])
    a.add_argument("--arm")
    a.add_argument("--baseline-arm")
    a.add_argument("--json", action="store_true")
    m = sub.add_parser("map")
    m.add_argument("--out", default=str(HERE / "reach-map.json"))
    p = sub.add_parser("plan")
    p.add_argument("--arms", required=True, help="file of candidate arm lines")
    p.add_argument("--baseline-arm", required=True)
    p.add_argument("--stage", choices=("A", "B"), required=True)
    p.add_argument("--n", type=int, default=None,
                   help="seeds per list variable: stage A 10, stage B 30 by default")
    p.add_argument("--full", action="store_true",
                   help="the registered screen's own seed counts (90 long-run histories, ...)")
    p.add_argument("--set", choices=("A", "B"), default="A", help="held-out set (B is +20000)")
    p.add_argument("--every-stage", action="store_true",
                   help="every arm runs every stage: the verify session, whose unreachable "
                        "stages screen.py verify compares with the baseline's")
    p.add_argument("--only-stages", default=None, help="comma list: keep only these stages")
    p.add_argument("--skip-stages", default=None,
                   help="comma list: leave these stages out (deferred to the verify or grade run)")
    p.add_argument("--full-stages", default=None,
                   help="comma list: these stages keep the registered counts (rows pooled over "
                        "270, 360 or 400 seeds, R7a over 90) whatever --n says")
    p.add_argument("--registered", default=None, metavar="GRADE_SEEDS_JSON",
                   help="a registered grade: every shard's env is the file's variables plus REGISTERED_GRADE")
    p.add_argument("--registered-grade", default=None, help="the registration commit (with --registered)")
    p.add_argument("--next", default=None,
                   help="JSON {arm: {stage: seeds}} from decide's next_n: a later look runs only these")
    p.add_argument("--baseline-cached", action="store_true",
                   help="the baseline's stages are already in the cache (leave them out of the plan)")
    p.add_argument("--all-reached", action="store_true",
                   help="stage A on every reached row, not only the targets")
    p.add_argument("--jobs-ref", default=None)
    p.add_argument("--out", required=True)
    c = sub.add_parser("cost")
    c.add_argument("--plan")
    c.add_argument("--stage", choices=("full", "A", "B"), default="full")
    c.add_argument("--arms-count", type=int, default=1)
    c.add_argument("--n", type=int, default=10)
    c.add_argument("--stages", default=None, help="comma list (default every stage)")
    c.add_argument("--box", default="c8g.16xlarge")
    c.add_argument("--boxes", type=int, default=1)
    c.add_argument("--no-wheel", action="store_true", help="price a box that compiles the engine")
    c.add_argument("--parallel", type=int, default=1,
                   help="shards at once on the box (SCREEN_PARALLEL); above 1 the packed model")
    c.add_argument("--cap", type=float, default=None, help="exit 1 when the estimate is over this")
    c.add_argument("--jobs-ref", default=None)
    d = sub.add_parser("decide")
    d.add_argument("readings")
    d.add_argument("--stage", choices=("A", "B"), required=True)
    d.add_argument("--n", type=int, default=10)
    d.add_argument("--N", type=int, default=30)
    d.add_argument("--block", type=int, default=10)
    d.add_argument("--gamma", type=float, default=R.GAMMA)
    d.add_argument("--margin-se", type=float, default=0.0)
    d.add_argument("--out")
    d.add_argument("--arm", default=None,
                   help="the candidate's arm line: its target rows come from reach.json")
    d.add_argument("--baseline-arm", default=None, help="the baseline's arm line (with --arm)")
    d.add_argument("--targets", default=None, help="comma list of target rows (instead of --arm)")
    d.add_argument("--complete", action="store_true",
                   help="every reading is its row's full block (stage B's last look)")
    d.add_argument("--baseline-readings", default=None,
                   help="the baseline's readings (readings.py): rows it passes are regression checks")
    sp = sub.add_parser("split")
    sp.add_argument("plan")
    sp.add_argument("--boxes", type=int, required=True)
    v = sub.add_parser("verify")
    v.add_argument("box")
    v.add_argument("--arm", required=True)
    v.add_argument("--baseline", required=True)
    v.add_argument("--stages", required=True)
    args = ap.parse_args(argv)

    if args.cmd == "reach":
        if args.arm:
            changed, wide = changed_between(args.arm, args.baseline_arm)
            if wide:
                print(wide)
        else:
            changed = [x for x in args.changed.split(",") if x]
        r = reach(changed, args.mechanism)
        if args.json:
            print(json.dumps(r, indent=1))
        else:
            _print_reach(r)
        return 0
    if args.cmd == "map":
        pathlib.Path(args.out).write_text(json.dumps(reach_map(), indent=1) + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
        return 0
    if args.cmd == "plan":
        defaults = seed_defaults(jobs_text(args.jobs_ref))
        arms = [ln for ln in pathlib.Path(args.arms).read_text().splitlines()
                if ln.split("#", 1)[0].strip()]
        n = None if args.full else (args.n or (10 if args.stage == "A" else 30))
        pl = plan(arms, args.baseline_arm, args.stage, n=n, defaults=defaults,
                  set_offset=20000 if args.set == "B" else 0,
                  targets_only=not args.all_reached,
                  next_n=json.loads(pathlib.Path(args.next).read_text()) if args.next else None,
                  every_stage=args.every_stage,
                  full_stages=args.full_stages.split(",") if args.full_stages else None,
                  skip=args.skip_stages.split(",") if args.skip_stages else None,
                  registered=({**json.loads(pathlib.Path(args.registered).read_text())["variables"],
                               "REGISTERED_GRADE": args.registered_grade} if args.registered else None),
                  only=args.only_stages.split(",") if args.only_stages else None)
        if args.baseline_cached:
            pl["shards"] = [x for x in pl["shards"] if x["arm"] != pl["baseline"]]
        pl["estimate"] = cost(pl["shards"])
        pathlib.Path(args.out).write_text(json.dumps(pl, indent=1) + "\n", encoding="utf-8")
        for note in pl["arms"]:
            print(f"{note['arm']}: {', '.join(note['stages']) or '(nothing)'}  [{note['why']}]")
        e = pl["estimate"]
        print(f"{len(pl['shards'])} shards, {e['core_minutes']} core-min, "
              f"{e['box_minutes']} box-min on {e['box']}, about ${e['dollars']}")
        return 0
    if args.cmd == "cost":
        if args.plan:
            shards = json.loads(pathlib.Path(args.plan).read_text())["shards"]
        else:
            defaults = seed_defaults(jobs_text(args.jobs_ref))
            stages = args.stages.split(",") if args.stages else None
            shards = full_shards(args.arms_count, args.stage, args.n, defaults, stages)
        e = cost(shards, box=args.box, boxes=args.boxes, wheel=not args.no_wheel,
                 parallel=args.parallel)
        print(json.dumps(e, indent=1))
        if args.cap is not None and e["dollars"] > args.cap:
            print(f"OVER CAP: about ${e['dollars']} against ${args.cap}")
            return 1
        return 0
    if args.cmd == "decide":
        readings = json.loads(pathlib.Path(args.readings).read_text())
        targets = None
        if args.targets:
            targets = args.targets.split(",")
        elif args.arm:
            ch, wide = changed_between(args.arm, args.baseline_arm)
            targets = reach(["*base*"] if wide else ch)["targets"]
        base = (json.loads(pathlib.Path(args.baseline_readings).read_text())
                if args.baseline_readings else None)
        res = decide(readings, stage=args.stage, n=args.n, N=args.N, block=args.block,
                     gamma=args.gamma, margin_se=args.margin_se, targets=targets, baseline=base,
                     complete=args.complete)
        print(f"candidate: {res['state'].upper()}  (kill line {res['kill_line']:.2f} se, "
              f"settle line {res['settle_line']:.2f} se, {res['rows_judged']} rows, "
              f"{res['looks']} look(s))")
        for dd in res["decisions"]:
            print(f"  {dd['state']:4s} {dd['row']:32s} n {dd['n']}/{dd['N']}  {dd['why']}")
        if res["next_n"]:
            print("next look: " + ", ".join(f"{s} {n}" for s, n in res["next_n"].items()))
        if res["regression_rows"]:
            print("regression checks (the baseline passes them): " + ", ".join(res["regression_rows"]))
        for r in res["reported"]:
            v = r["value"]
            print(f"  not judged {r['row']:24s} {round(v, 4) if isinstance(v, float) else v}  ({r['why']})")
        if args.out:
            pathlib.Path(args.out).write_text(json.dumps(res, indent=1, default=float) + "\n")
        return 0
    if args.cmd == "split":
        pl = json.loads(pathlib.Path(args.plan).read_text())
        stem = pathlib.Path(args.plan)
        for i, part in enumerate(split(pl, args.boxes), 1):
            to = stem.with_name(f"{stem.stem}-box{i}{stem.suffix}")
            to.write_text(json.dumps(part, indent=1) + "\n", encoding="utf-8")
            e = part["estimate"]
            print(f"{to}: {len(part['shards'])} shards, {e['box_minutes']} box-min, about ${e['dollars']}")
        return 0
    if args.cmd == "verify":
        bad = verify(pathlib.Path(args.box), args.arm, args.baseline, args.stages.split(","))
        for b in bad:
            print(f"DISAGREES {b['stage']} {b['file']}: rows {', '.join(b['rows'])}")
        print(f"{len(bad)} disagreement(s) between {args.arm} and {args.baseline} on "
              f"{args.stages}. On a stage the reach map calls unreachable, each one is a "
              "reach-map bug; on a reached stage it is the change at work.")
        return 3 if bad else 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
