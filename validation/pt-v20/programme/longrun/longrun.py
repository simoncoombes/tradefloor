"""longrun -- the long-horizon and crash check of a model version against
the tape. See README.md.

  python longrun.py measure --arm NAME [--set dial=value,...] [--base pt-v19]
                    [--seeds 30] [--years 21] [--workers N]
                    --engine PATH --out DIR
  python longrun.py measure --arms-file FILE ...    many arms in one pool (box);
                    a line NAME@BASE:... puts that arm on BASE instead of --base
  python longrun.py report DIR [DIR ...] [--out FILE] [--boot 200] [--boot-model 100]
  python longrun.py import-crashcheck RUNS_DIR --arm NAME --out DIR
                    [--replay-dir DIR]              convert the crash check's runs

MEASURE, per arm and seed (seeds 101.., the certified roster
Universe.random(40, seed=111), market seed = seed):
  free    `years` x 252 sessions from the calm opening, the cap-weighted
          roster index and the VIX at every close (crashcheck/freerun.py);
  gfc     252-session burn, then the real VIX of 2007-06-01..2009-12-31
          imposed session by session (Scenario path), index, VIX and every
          name's close (crashcheck/replay.py);
  covid   the same over 2020-01-02..2021-06-30.
Each (seed, part) is its own task and its own files, DIR/ARM/SEED-PART.npz
(float64 series) then DIR/ARM/SEED-PART.json (the completion mark; for a
replay, its scalar measures). A rerun skips every part whose .json exists,
so a killed run resumes. DIR/ARM/meta.json holds the dials, the base, the
engine commit and build; a rerun with other dials into the same arm is
refused.

On a box, REPO (the engine checkout) and WORKERS are read from the
environment, as ptv19r1/run.py does; --engine and --workers override.
"""
import argparse, array, json, math, os, pathlib, random, statistics as st
import subprocess, sys, time
from concurrent.futures import ProcessPoolExecutor, as_completed

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import estimators as E  # noqa: E402
import tape as T  # noqa: E402

BURN = 252
ROSTER = (40, 111)
PARTS = ("free", "gfc", "covid")
CYCLES = ("expansion", "peak", "contraction", "trough", "recovery")
MACRO_KEYS = ("vix", "federal_funds_rate", "corporate_bond_yield", "inflation_rate", "gdp_growth",
              "unemployment_rate", "oil_price", "treasury_yield_2y", "treasury_yield_10y", "fear_greed_index")

# ============================================================== measure

_TF = {}


def _engine(path):
    """Import tradefloor from the engine worktree, the way run.py does: put
    PATH/python first only when a built _core sits there (a desk editable
    install); on a box the wheel is in the venv and the bare tree would
    shadow it."""
    if "tf" not in _TF:
        p = str(pathlib.Path(path) / "python")
        if p not in sys.path and any(pathlib.Path(p, "tradefloor").glob("_core*.so")):
            sys.path.insert(0, p)
        import tradefloor as tf
        _TF["tf"] = tf
    return _TF["tf"]


def _task(spec):
    engine, base, dials, seed, part, days, path = spec
    tf = _engine(engine)
    t0 = time.time()
    u = list(tf.Universe.random(ROSTER[0], seed=ROSTER[1]))
    shares = [float(i.shares_outstanding) for i in u]
    model = tf.ModelParams.from_preset(base, **dials) if dials else tf.ModelParams.from_preset(base)
    e = tf.Engine(seed=seed, universe=u, model=model)
    lvl, vix = [], []
    if part == "free":
        # The published macro series at each close, for C10 (the eleventh
        # registration): what a trader reads after the close, as floats, the
        # phase as its index in CYCLES.
        macro = {k: [] for k in MACRO_KEYS}
        cyc = []
        for d in range(days):
            e.run_days(1, record=False, first_day=d)
            p = array.array("d"); p.frombytes(e.prices())
            lvl.append(sum(a * b for a, b in zip(p, shares)))
            vix.append(e.state_snapshot()["economy"]["vix"])
            mf = e.macro_fields
            for k in MACRO_KEYS:
                macro[k].append(float(mf[k]))
            cyc.append(CYCLES.index(mf["cycle"]))
        return seed, part, {"level": lvl, "vix": vix, "cyc": cyc, **{"m_" + k: v for k, v in macro.items()}}, \
            {"elapsed_s": time.time() - t0}
    e.run_days(BURN, record=False)
    scen = tf.Scenario.from_json(json.dumps({"schema": 1, "label": part, "days": len(path),
                                             "path": [{"day": i, "vix": v} for i, v in enumerate(path)]}))
    names = [[] for _ in u]
    for i in range(len(path)):
        scen.apply(e, i)
        e.run_days(1, record=False, first_day=BURN + i)
        p = array.array("d"); p.frombytes(e.prices())
        lvl.append(sum(a * b for a, b in zip(p, shares)))
        vix.append(e.state_snapshot()["economy"]["vix"])
        for k, x in enumerate(p):
            names[k].append(x)
    calm_end = next(i for i, v in enumerate(path) if v > 30)
    m = E.measures(lvl, names, calm_end, paths=False)
    m["vix_tracking"] = st.median(abs(a - b) for a, b in zip(vix, path))
    m["elapsed_s"] = time.time() - t0
    return seed, part, {"level": lvl, "vix": vix, "names": names}, m


def _save(adir, seed, part, series, meta):
    import numpy as np
    tmp = adir / f".{seed}-{part}.tmp.npz"
    np.savez_compressed(tmp, **{k: np.asarray(v, dtype=np.float64) for k, v in series.items()})
    os.replace(tmp, adir / f"{seed}-{part}.npz")
    tj = adir / f".{seed}-{part}.tmp.json"
    json.dump(meta | {"seed": seed, "part": part}, open(tj, "w"))
    os.replace(tj, adir / f"{seed}-{part}.json")


def parse_arm_head(head, default_base):
    """`NAME` or `NAME@BASE`: an arm's name and the preset its dials sit on.

    `NAME@BASE` lets one arms file measure a preset BY NAME beside arms on
    another base (e.g. `pt-v20@pt-v20:` next to `pt-v19:`); a bare NAME takes
    --base, as before."""
    name, _, base = head.strip().partition("@")
    return name.strip(), (base.strip() or default_base)


def _parse_set(text):
    return {k.strip(): float(v) for k, v in (x.split("=", 1) for x in (text or "").split(",") if x.strip())}


def _build_info(engine, tf):
    run = lambda *a: subprocess.run(["git", "-C", engine, *a], capture_output=True, text=True).stdout.strip()
    so = next(iter(pathlib.Path(tf.__file__).parent.glob("_core*.so")), None)
    return {"commit": run("rev-parse", "HEAD"), "branch": run("rev-parse", "--abbrev-ref", "HEAD"),
            "rust_dirty": run("status", "--porcelain", "--", "rust/"),
            "engine_file": tf.__file__, "so": str(so) if so else None,
            "so_mtime": time.strftime("%F %T", time.localtime(so.stat().st_mtime)) if so else None,
            "version": getattr(tf, "version", lambda: None)(), "python": sys.version.split()[0]}


def measure(a):
    engine = a.engine or os.environ.get("REPO")
    if not engine:
        sys.exit("--engine PATH or REPO in the environment is required")
    workers = a.workers or int(os.environ.get("WORKERS", "3"))
    arms = {}   # name -> (base, dials)
    if a.arms_file:
        for ln in open(a.arms_file):
            ln = ln.split("#", 1)[0].strip()
            if ln:
                head, body = ln.split(":", 1)
                name, base = parse_arm_head(head, a.base)
                arms[name] = (base, _parse_set(body))
    if a.arm:
        name, base = parse_arm_head(a.arm, a.base)
        arms[name] = (base, _parse_set(a.set))
    if not arms:
        sys.exit("give --arm NAME [--set ...] or --arms-file FILE")
    tf = _engine(engine)
    build = _build_info(engine, tf)
    out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
    tp = T.load()
    paths = {k: tp["windows"][k]["vix"] for k in ("gfc", "covid")}
    if a.seed_list:
        # "101-130,401-430,701-730": several seed sets pooled into one arm, so
        # a tail row is not read off one lucky or unlucky set of thirty.
        seeds = []
        for part in a.seed_list.split(","):
            lo, _, hi = part.partition("-")
            seeds += list(range(int(lo), int(hi or lo) + 1))
    else:
        seeds = list(range(a.first_seed, a.first_seed + a.seeds))
    todo = []
    for name, (base, dials) in arms.items():
        base_d = tf.ModelParams.from_preset(base).to_dict()
        d = tf.ModelParams.from_preset(base, **dials).to_dict() if dials else base_d
        landed = all(d.get(k) == v for k, v in dials.items())
        changed = sorted(k for k in d if d.get(k) != base_d.get(k) and k != "name")
        # Coefficients the params DERIVE from a dial move with it and are not
        # a second change: the mispricing half-life sets the daily and
        # per-tick phi. Anything else that moved unasked still refuses.
        derived = {"mispricing_half_life_days": {"mispricing_phi", "s_phi_tick"}}
        allowed = set(dials).union(*(derived.get(k, set()) for k in dials))
        if not landed or not (set(dials) <= set(changed) <= allowed):
            print(f"REFUSED {name}: landed={landed} changed={changed}", flush=True); continue
        adir = out / name; adir.mkdir(exist_ok=True)
        meta = {"kind": "longrun.arm", "arm": name, "base": base, "dials": dials, "years": a.years,
                "roster": list(ROSTER), "burn_replay": BURN, "windows": T.WINDOWS,
                "fingerprint": str(tf.ModelParams.from_preset(base, **dials).fingerprint),
                "build": build}
        mp = adir / "meta.json"
        if mp.exists():
            old = json.load(open(mp))
            if (old["base"], old["dials"], old["years"]) != (base, dials, a.years):
                print(f"REFUSED {name}: {mp} holds base {old['base']} dials {old['dials']} years "
                      f"{old['years']}; use another --out or arm name", flush=True); continue
            if old["build"]["commit"] != build["commit"]:
                print(f"REFUSED {name}: {mp} was measured on engine {old['build']['commit']}, "
                      f"this is {build['commit']}", flush=True); continue
            meta["seeds"] = sorted(set(old.get("seeds", [])) | set(seeds))
        else:
            meta["seeds"] = seeds
        json.dump(meta, open(mp, "w"), indent=1)
        for s in seeds:
            for part in PARTS:
                if not (adir / f"{s}-{part}.json").exists():
                    todo.append((name, (engine, base, dials, s, part, a.years * 252, paths.get(part))))
    # the long free runs first, so the pool drains evenly
    todo.sort(key=lambda x: (x[1][4] != "free", x[1][3]))
    print(f"BUILD {json.dumps(build)}", flush=True)
    print(f"{len(todo)} tasks, {workers} workers, arms {list(arms)}", flush=True)
    t = time.time()
    with ProcessPoolExecutor(workers) as ex:
        fut = {ex.submit(_task, spec): name for name, spec in todo}
        for f in as_completed(fut):
            seed, part, series, m = f.result()
            _save(out / fut[f], seed, part, series, m)
            print(f"{fut[f]} {seed} {part} {m['elapsed_s']:.0f}s  ({time.time() - t:.0f}s)", flush=True)
    return 0


# ============================================================== import

def import_crashcheck(a):
    """The crash check's runs/ARM/SEED.json (free, 21 years) and its
    replay-*.json (replay scalars per seed; the series were not kept)."""
    src = pathlib.Path(a.runs_dir); adir = pathlib.Path(a.out) / a.arm
    adir.mkdir(parents=True, exist_ok=True)
    rdir = pathlib.Path(a.replay_dir) if a.replay_dir else src.parents[1]
    seeds = []
    for f in sorted(src.glob("*.json")):
        r = json.load(open(f)); s = int(r["seed"]); seeds.append(s)
        _save(adir, s, "free", {"level": r["level"], "vix": r["vix"]}, {"imported_from": str(f)})
        years = len(r["level"]) // 252
    for ep in ("gfc", "covid"):
        p = rdir / f"replay-{ep}.json"
        if not p.exists():
            continue
        R = json.load(open(p))
        for s, m in R["arms"].get(a.arm, {}).items():
            keep = {k: v for k, v in m.items() if not k.endswith("_path")}
            json.dump(keep | {"seed": int(s), "part": ep, "imported_from": str(p)}, open(adir / f"{s}-{ep}.json", "w"))
    json.dump({"kind": "longrun.arm", "arm": a.arm, "base": "pt-v19", "dials": None, "years": years,
               "roster": list(ROSTER), "seeds": seeds, "windows": T.WINDOWS,
               "build": {"commit": "0b61f04 (crashcheck import, probe/anchor-blend)"},
               "imported_from": str(src)}, open(adir / "meta.json", "w"), indent=1)
    print(f"imported {len(seeds)} seeds into {adir}")
    return 0


# ============================================================== report

def load_arm(adir, max_seeds=None, max_days=None):
    import numpy as np
    meta = json.load(open(adir / "meta.json"))
    runs, rep = [], {"gfc": [], "covid": []}
    for j in sorted(adir.glob("*-free.json"), key=lambda p: int(p.name.split("-")[0])):
        s = int(j.name.split("-")[0])
        with np.load(adir / f"{s}-free.npz") as z:
            lv, vx = z["level"].tolist(), z["vix"].tolist()
        if max_days:
            lv, vx = lv[:max_days], vx[:max_days]
        runs.append({"seed": s, "level": lv, "vix": vx})
    if max_seeds:
        runs = runs[:max_seeds]
    for ep in rep:
        for j in sorted(adir.glob(f"*-{ep}.json"), key=lambda p: int(p.name.split("-")[0])):
            rep[ep].append(json.load(open(j)))
    return meta, runs, rep


def model_part(level, vix):
    n = len(level)
    bounds = [(k * 252, min((k + 1) * 252, n - 1)) for k in range(math.ceil((n - 1) / 252))]
    rw = E.rows_one(vix[BURN:])
    return {"pooled": E.pooled_part(level, vix), "stats": E.stats_part(level[BURN:], vix[BURN:]),
            "vix": vix[BURN:], "rowsums": E.row_sums(rw), "levels": E.vix_levels_part(vix[BURN:]),
            "years": E.year_stats(level, bounds)}


def _flatten(pool, levels, stats, t, years):
    out = dict(pool)
    for lo, v in levels.items():
        out[f"lvl_{lo}"] = v
    for k in ("spells30_per_decade", "spell30_mean_len", "spells40_per_decade", "spell40_mean_len",
              "fear_dn1", "fear_dn3", "ceiling_hits"):
        out[k] = stats[k]
    for lo, v in E.halflives(t).items():
        out[f"hl_{lo}"] = v
        # the reversion fraction per session behind the half-life: the
        # half-life is unbounded as it nears 0, so z-scores are taken on this
        out[f"hk_{lo}"] = t[lo][2] if t[lo] is not None and t[lo][2] == t[lo][2] else None
    out.update(years)
    return out


def model_measures(parts, exact=True):
    pool = E.pooled_combine([p["pooled"] for p in parts], exact=exact)
    levels = E.vix_levels_combine([p["levels"] for p in parts])
    stats = E.stats_combine([p["stats"] for p in parts], exact=exact)
    if exact:
        tb = E.table(E.rows([p["vix"] for p in parts]))
    else:
        tb = E.table_from_sums([p["rowsums"] for p in parts])
    years = {}
    for k in range(max(len(p["years"]) for p in parts)):
        ys = [p["years"][k] for p in parts if len(p["years"]) > k and p["years"][k][2] > 0]
        if ys:
            years[f"yret_{k + 1}"] = st.fmean(y[0] for y in ys)
            years[f"ycrash_{k + 1}"] = 100 * sum(y[1] for y in ys) / sum(y[2] for y in ys)
    return _flatten(pool, levels, stats, tb, years)


def _tape_years(dates):
    """Session indices i >= 1 grouped by calendar year: {year: [i, ...]};
    session i carries the return level[i] / level[i-1] - 1, return index i-1."""
    g = {}
    for i in range(1, len(dates)):
        g.setdefault(dates[i][:4], []).append(i)
    return g


def tape_measures(level, vix, years, year_bounds):
    s = E.summarise(level, vix, years)
    levels = E.vix_levels_combine([E.vix_levels_part(vix)])
    stats = E.stats([(level, vix)])
    tb = E.table(E.rows([vix]))
    ys = E.year_stats(level, year_bounds)
    yr = {"yret": st.fmean(y[0] for y in ys), "ycrash": 100 * sum(y[1] for y in ys) / sum(y[2] for y in ys)}
    return _flatten(s, levels, stats, tb, yr)


def tape_side(B, seed=20260923):
    """The tape's measures and their block-bootstrap standard errors: the
    tape's calendar years resampled with replacement, the returns and VIX of
    the drawn years concatenated, the index rebuilt from the returns, every
    measure recomputed. Full years only enter the by-year means. Cached."""
    cache = HERE / "data" / f"tape-side-B{B}-s{seed}.json"
    if cache.exists():
        return json.load(open(cache))
    tp = T.load()
    lvl, vix, dates = tp["level"], tp["vix"], tp["dates"]
    groups = _tape_years(dates)
    full = {y for y, ix in groups.items() if len(ix) >= 240}
    bounds = [(ix[0] - 1, ix[-1]) for y, ix in groups.items() if y in full]
    point = tape_measures(lvl, vix, tp["years"], bounds)
    per_session_year = (len(lvl) - 1) / tp["years"]
    rng = random.Random(seed)
    keys = list(groups)
    draws = []
    for b in range(B):
        pick = [rng.choice(keys) for _ in keys]
        L, V, bb = [1.0], [vix[groups[pick[0]][0] - 1]], []
        for y in pick:
            a0 = len(L)
            for i in groups[y]:
                L.append(L[-1] * (lvl[i] / lvl[i - 1])); V.append(vix[i])
            if y in full:
                bb.append((a0 - 1, len(L) - 1))
        draws.append(tape_measures(L, V, (len(L) - 1) / per_session_year, bb))
    se = {}
    for k, v in point.items():
        xs = [d.get(k) for d in draws]
        xs = [x for x in xs if isinstance(x, (int, float)) and x == x]
        se[k] = st.stdev(xs) if len(xs) >= 0.8 * B else None
    rep = {}
    for ep, w in tp["windows"].items():
        rep[ep] = E.measures(w["index"], w["names"], w["calm_end"], paths=False)
    out = {"point": point, "se": se, "B": B, "seed": seed, "years": tp["years"], "replay": rep,
           "blocks": len(keys), "full_years": len(full)}
    json.dump(out, open(cache, "w"), indent=1)
    return out


def model_side(runs, B, seed=7):
    parts = [model_part(r["level"], r["vix"]) for r in runs]
    point = model_measures(parts)
    rng = random.Random(seed)
    draws = [model_measures([rng.choice(parts) for _ in parts], exact=False) for _ in range(B)]
    se = {}
    for k in point:
        xs = [d.get(k) for d in draws]
        xs = [x for x in xs if isinstance(x, (int, float)) and x == x]
        se[k] = st.stdev(xs) if len(xs) >= 0.8 * B and len(xs) > 1 else None
    return point, se


ROWS = [
    ("INDEX", None, None),
    ("ann_vol_pct", "index annual volatility, %", "%.1f"),
    ("ann_return_pct", "index annual return, % (model: median seed)", "%.1f"),
    ("DRAWDOWNS (from the running peak)", None, None),
    ("dd10_per_decade", "10% drawdowns per decade", "%.2f"),
    ("dd10_depth_median", "  median depth", "%.2f"),
    ("dd10_to_trough_median", "  sessions peak to trough", "%.0f"),
    ("dd10_recover_median", "  sessions trough to recovery", "%.0f"),
    ("dd20_per_decade", "20% bear markets per decade", "%.2f"),
    ("dd20_depth_median", "  median depth", "%.2f"),
    ("dd20_depth_max", "  deepest", "%.2f"),
    ("dd20_to_trough_median", "  sessions peak to trough", "%.0f"),
    ("dd20_recover_median", "  sessions trough to recovery", "%.0f"),
    ("dd20_peak_vix_median", "  peak VIX in the fall", "%.1f"),
    ("dd20_worst_rv21_median", "  worst month's volatility, %", "%.1f"),
    ("TAIL SESSIONS", None, None),
    ("days_below_-3_per_decade", "sessions under -3% per decade", "%.1f"),
    ("days_below_-5_per_decade", "sessions under -5% per decade", "%.1f"),
    ("days_below_-7_per_decade", "sessions under -7% per decade", "%.2f"),
    ("worst_session_pct", "worst session, %", "%.1f"),
    ("VIX LEVELS (share of sessions)", None, None),
    ("lvl_0", "VIX < 15", "%.3f"), ("lvl_15", "VIX 15-20", "%.3f"), ("lvl_20", "VIX 20-25", "%.3f"),
    ("lvl_25", "VIX 25-30", "%.3f"), ("lvl_30", "VIX 30-40", "%.3f"), ("lvl_40", "VIX 40-60", "%.3f"),
    ("lvl_60", "VIX 60+", "%.4f"),
    ("vix_share_above_30", "VIX > 30", "%.3f"), ("vix_share_above_40", "VIX > 40", "%.3f"),
    ("vix_max_per_decade_median", "highest VIX per decade (median)", "%.1f"),
    ("VIX FEAR SPELLS (spells within 10 sessions merged)", None, None),
    ("spells30_per_decade", "spells above 30 per decade", "%.1f"),
    ("spell30_mean_len", "  mean length, sessions", "%.0f"),
    ("spells40_per_decade", "spells above 40 per decade", "%.1f"),
    ("spell40_mean_len", "  mean length, sessions", "%.0f"),
    ("VIX HALF-LIFE BY LEVEL (sessions; distance from trailing 1-year median)", None, None),
    ("hl_0", "VIX < 15", "%.1f"), ("hl_15", "VIX 15-20", "%.1f"), ("hl_20", "VIX 20-25", "%.1f"),
    ("hl_25", "VIX 25-30", "%.1f"), ("hl_30", "VIX 30-40", "%.1f"), ("hl_40", "VIX 40-60", "%.1f"),
    ("hl_60", "VIX 60+", "%.1f"),
    ("SAME-DAY FEAR (mean VIX change, points)", None, None),
    ("fear_dn1", "on index -0.5..-1.5% sessions", "%.2f"),
    ("fear_dn3", "on index < -3% sessions", "%.2f"),
]


def _fmt(f, v):
    return "-" if v is None or (isinstance(v, float) and v != v) else f % v


def _z(m, t, se):
    if m is None or t is None or not se or m != m or t != t:
        return None
    return (m - t) / se


def _flag(z):
    if z is None:
        return "     "
    mark = "!!" if abs(z) >= 3 else "! " if abs(z) >= 2 else "  "
    return "%+5.1f%s" % (z, mark) if abs(z) < 100 else "%+5.0f%s" % (z, mark)


def _arm_dirs(paths):
    out = []
    for d in paths:
        d = pathlib.Path(d)
        if (d / "meta.json").exists():
            out.append(d)
        else:
            out += sorted(x for x in d.iterdir() if (x / "meta.json").exists())
    return out


def _q(v, p):
    return v[min(len(v) - 1, int(p * len(v)))]


def report(a):
    L = []
    say = lambda s="": (print(s), L.append(s))
    tape = tape_side(a.boot)
    tp, tse = tape["point"], tape["se"]
    arms = {}
    for d in _arm_dirs(a.dirs):
        meta, runs, rep = load_arm(d)
        if not runs and not rep["gfc"]:
            continue
        name = meta["arm"] if meta["arm"] not in arms else f"{meta['arm']}@{d.parent.name}"
        point, se = model_side(runs, a.boot_model) if runs else ({}, {})
        arms[name] = {"meta": meta, "n": len(runs), "point": point, "se": se, "replay": rep,
                      "years": (len(runs[0]["level"]) // 252) if runs else 0}
    names = list(arms)
    W = 26
    say("LONGRUN REPORT  %s" % time.strftime("%F %T"))
    say("tape: S&P 500 / VIX %s..%s, %.1f years; standard errors by block bootstrap over its %d calendar "
        "years (B=%d)" % (T.load()["dates"][0], T.load()["dates"][-1], tape["years"], tape["blocks"], tape["B"]))
    for n in names:
        m = arms[n]["meta"]
        say("arm %-10s base %s dials %s; %d seeds x %d years (first year discarded in pooled rows); engine %s"
            % (n, m.get("base"), json.dumps(m.get("dials")), arms[n]["n"], arms[n]["years"],
               str(m.get("build", {}).get("commit", "?"))[:24]))
    say("cell: model value ±its seed-bootstrap SE (B=%d), then z = (model - tape) / tape SE; ! |z|>=2, !! |z|>=3."
        % a.boot_model)
    say("half-life rows: z is taken on the per-session reversion fraction behind the half-life, which stays "
        "finite where the half-life does not.")
    say()
    head = "   %-46s %16s " % ("", "tape [SE]") + "".join("%*s" % (W, n) for n in names)
    say(head)
    for k, label, f in ROWS:
        if label is None:
            say(k); continue
        t, s = tp.get(k), tse.get(k)
        zk = "hk_" + k[3:] if k.startswith("hl_") else k
        cells = []
        for n in names:
            P, S = arms[n]["point"], arms[n]["se"]
            m = P.get(k)
            cell = _fmt(f, m) + ("" if S.get(k) is None else "±" + _fmt(f, S[k]))
            cells.append("%*s" % (W, cell + " " + _flag(_z(P.get(zk), tp.get(zk), tse.get(zk)))))
        say("   %-46s %16s " % (label, _fmt(f, t) + " [" + _fmt(f, s) + "]") + "".join(cells))
    # by year since the start
    maxy = max([arms[n]["years"] for n in names] + [0])
    if maxy:
        for key, label, f in (("yret", "RETURN BY YEAR SINCE START, % (tape: mean calendar year)", "%.1f"),
                              ("ycrash", "SESSIONS UNDER -3% BY YEAR SINCE START, % of sessions "
                                         "(tape: all full years)", "%.2f")):
            say(label)
            t, s = tp.get(key), tse.get(key)
            for y in range(1, maxy + 1):
                cells = []
                for n in names:
                    m = arms[n]["point"].get(f"{key}_{y}"); ms = arms[n]["se"].get(f"{key}_{y}")
                    cells.append("%*s" % (W, _fmt(f, m) + ("" if ms is None else "±" + _fmt(f, ms))
                                          + " " + _flag(_z(m, t, s))))
                say("   %-46s %16s " % ("year %d%s" % (y, " (burn, from the calm opening)" if y == 1 else ""),
                                        _fmt(f, t) + " [" + _fmt(f, s) + "]") + "".join(cells))
    # replay
    for ep in ("gfc", "covid"):
        w = T.WINDOWS[ep]
        say()
        say("REPLAY %s %s..%s, the real VIX imposed after a 252-session burn; model median [10th, 90th pct] "
            "over seeds, (z) = (median - real) / seed SD" % (ep, w[0], w[1]))
        real = tape["replay"][ep]
        for k, label in (("max_drawdown", "maximum drawdown"), ("peak_rv21", "worst month's volatility, %"),
                         ("calm_rv21", "calm volatility, %"), ("corr_calm", "stock correlation, calm"),
                         ("corr_peak", "stock correlation, peak"), ("vix_tracking", "VIX tracking error, points")):
            row = "   %-30s real %7s" % (label, ("%.3f" % real[k]) if k in real else "-")
            for n in names:
                v = sorted(m[k] for m in arms[n]["replay"][ep] if k in m)
                if not v:
                    row += "  %s -" % n; continue
                med = st.median(v); sd = st.stdev(v) if len(v) > 1 else None
                z = _z(med, real.get(k), sd)
                row += "  %s %.3f [%.3f, %.3f]%s" % (n, med, _q(v, 0.1), _q(v, 0.9),
                                                     "" if z is None else " (%+.1f)" % z)
                arms[n].setdefault("replay_summary", {}).setdefault(ep, {})[k] = {
                    "median": med, "p10": _q(v, 0.1), "p90": _q(v, 0.9), "sd": sd, "n": len(v), "z": z}
            say(row)
    out = pathlib.Path(a.out) if a.out else pathlib.Path(a.dirs[0]) / "report.txt"
    out.write_text("\n".join(L) + "\n")
    json.dump({"tape": tape, "arms": {n: {k: v for k, v in x.items() if k != "replay"} for n, x in arms.items()}},
              open(out.with_suffix(".json"), "w"), indent=1, default=str)
    print(f"wrote {out} and {out.with_suffix('.json')}", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--arm"); m.add_argument("--set", default="")
    m.add_argument("--arms-file", help="lines NAME[@BASE]:dial=value,... ; an empty body is the base")
    m.add_argument("--base", default="pt-v19")
    m.add_argument("--seeds", type=int, default=30); m.add_argument("--first-seed", type=int, default=101)
    m.add_argument("--seed-list", default="", help="comma list of seed ranges, e.g. 101-130,401-430; overrides --seeds/--first-seed")
    m.add_argument("--years", type=int, default=21)
    m.add_argument("--workers", type=int, default=None)
    m.add_argument("--engine", default=None); m.add_argument("--out", required=True)
    r = sub.add_parser("report")
    r.add_argument("dirs", nargs="+"); r.add_argument("--out")
    r.add_argument("--boot", type=int, default=200, help="tape block-bootstrap draws")
    r.add_argument("--boot-model", type=int, default=100, help="model seed-bootstrap draws")
    i = sub.add_parser("import-crashcheck")
    i.add_argument("runs_dir"); i.add_argument("--arm", required=True); i.add_argument("--out", required=True)
    i.add_argument("--replay-dir")
    a = ap.parse_args()
    return {"measure": measure, "report": report, "import-crashcheck": import_crashcheck}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
