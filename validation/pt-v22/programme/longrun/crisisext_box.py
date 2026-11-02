"""D1's crisis sector dispersion needs at least ten readings (ptv20-registration.md,
owner 2026-09-26): the box side.

    python crisisext_box.py --out OUT [--workers N]

Run by box-jobs.sh after the four certification cells are written. OUT holds
cert/cells (panel_252), cert/cells504 (panel_504), heldseeds/cells
(heldout_seeds) and heldu/cells (heldout_universe), as certrun_box.py writes
them. For every cell file it counts the seeds whose `crisis_sector_dispersion`
was read. A cell with 10 or more is not extended. A cell with fewer is
extended, for this row only, with further seeds in blocks of 30 from 1001
upward (1001-1030, 1031-1060, ... up to 1241-1270, 300 seeds in all), stopping
at the first block that brings the count to 10 or more.

"300 IN ALL" IS THE CELL'S OWN 30 AND NINE BLOCKS OF 30. 1001-1270 is 270
seeds; with the cell's own 30 that is the rule's 300, its "under 1 in 30
two-year histories" (10 in 300), and its "no seed from 1001 to 1270 has been
run". So BLOCKS is 9 and no seed above 1270 is ever run.

The extension seeds run the cell's own protocol, read from the cell file:
the same base preset and dials (the fingerprint is checked), the same held
roster (`held_roster`: Universe.random(n, seed=r)), the same days and no burn,
through facts.measure, which is what certrun_box.py's `held` job calls. Only
the row, its absence reason and the two fingerprints are kept, so no other
row can be graded off an extension seed. (The row itself is separable --
facts.crisis_statistics reads only the bars and the VIX -- but the market
simulation is about nine tenths of a measurement, so calling facts.measure
costs about a tenth more and keeps the instrument the cell's own, not a copy
of its session loop.)

Every cell that still needs a block gets it in the same round, so the pool
works on all of them at once; each cell stops on its own count.

Writes OUT/<sub>/crisisext[504]/ext-<cell>.json for every cell, extended or
not (`blocks` empty when the cell's own seeds gave 10), which
certgrade_box.py reads. The grade itself (the median over every reading, and
fewer than 10 after 300 seeds fails) is certgrade_box.py's.
"""
import argparse, glob, json, multiprocessing as mp, os, pathlib, subprocess, sys, time

ENGINE = os.environ.get("REPO", "/Users/simoncoombes/Dev/tradefloor")
ROW = "crisis_sector_dispersion"
# The registered rule. Not arguments: the numbers were set before any
# extended reading was taken.
NEED = 10
BLOCK = 30
FIRST = 1001
BLOCKS = 9           # 1001-1270: 270 seeds, 300 in all with the cell's own 30
# (label, sub, days): the four cells D1 grades, where certrun_box.py writes them.
CELLS = (("panel_252", "cert", 252), ("panel_504", "cert", 504),
         ("heldout_seeds", "heldseeds", 252), ("heldout_universe", "heldu", 252))

tf = facts = ModelParams = None


def _bootstrap():
    global tf, facts, ModelParams
    p = ENGINE + "/python"
    # as certrun_box.py: the source tree only where a built _core sits in it
    if p not in sys.path and any(pathlib.Path(p, "tradefloor").glob("_core*.so")):
        sys.path.insert(0, p)
    import tradefloor as _tf
    from tradefloor import facts as _f, ModelParams as _M
    tf, facts, ModelParams = _tf, _f, _M


def block_seeds(k):
    """Block k (0-based): 1001-1030, 1031-1060, ..."""
    lo = FIRST + BLOCK * k
    return list(range(lo, lo + BLOCK))


def cell_dir(sub, days):
    return f"{sub}/" + ("cells" if days == 252 else f"cells{days}")


def ext_dir(sub, days):
    return f"{sub}/" + ("crisisext" if days == 252 else f"crisisext{days}")


def own_readings(cell):
    return {p["seed"]: p[ROW] for p in cell["held"] if p.get(ROW) is not None}


def _job(spec):
    """One extension seed on a cell's protocol: facts.measure as certrun_box.py's
    `held` job calls it, keeping the row and its provenance only."""
    label, name, seed, base, ov, days, n, rseed = spec
    model = ModelParams.from_preset(base, **ov) if ov else ModelParams.from_preset(base)
    held = list(tf.Universe.random(n, seed=rseed))
    p = facts.measure(seed=seed, universe=held, days=days, model=model)
    return label, name, seed, {"seed": seed, ROW: p.get(ROW), ROW + "_blind": p.get(ROW + "_blind"),
                               "days": p["days"], "burn": p["burn"],
                               "model_fingerprint": p.get("model_fingerprint"),
                               "universe_fingerprint": p.get("universe_fingerprint")}


def plan(out):
    """Every cell file under OUT -> the extension state it starts from."""
    todo = []
    for label, sub, days in CELLS:
        for f in sorted(glob.glob(str(out / cell_dir(sub, days) / "cell-*.json"))):
            if f.endswith(".refused.json"):
                continue
            c = json.load(open(f))
            if c.get("days") != days:
                sys.exit(f"REFUSED: {f} says days {c.get('days')}, its directory says {days}")
            if not c.get("held_roster"):
                sys.exit(f"REFUSED: {f} does not record its held roster; the extension cannot repeat its protocol")
            todo.append({"label": label, "sub": sub, "days": days, "file": f, "cell": c,
                         "path": out / ext_dir(sub, days) / f"ext-{c['cell']}.json"})
    return todo


def check_protocol(t, commit):
    """The extension must be the cell's instrument: same engine, same model."""
    c = t["cell"]
    ov = c.get("overrides") or {}
    fp = (ModelParams.from_preset(c["base"], **ov) if ov else ModelParams.from_preset(c["base"])).fingerprint
    why = []
    if c.get("fingerprint") != fp:
        why.append(f"fingerprint {fp} here, {c.get('fingerprint')} in the cell")
    if commit is not None and c.get("commit") != commit:
        why.append(f"engine {commit} here, {c.get('commit')} in the cell")
    return why


def run(todo, measure_map, commit=None, say=print, now=time.time):
    """The rule, round by round. measure_map(specs) yields _job's results; the
    tests hand it a mock. Returns {(label, cell): record}."""
    recs = {}
    for t in todo:
        c = t["cell"]
        own = own_readings(c)
        rec = {"kind": "crisisext", "rule": {"row": ROW, "need": NEED, "block": BLOCK, "first": FIRST,
                                            "blocks": BLOCKS, "last_seed": FIRST + BLOCK * BLOCKS - 1},
               "label": t["label"], "cell": c["cell"], "cell_file": t["file"], "days": t["days"],
               "base": c["base"], "overrides": c.get("overrides") or {}, "fingerprint": c.get("fingerprint"),
               "commit": c.get("commit"), "held_roster": c["held_roster"],
               "own_seeds": [p["seed"] for p in c["held"]], "own_readings": len(own),
               "blocks": [], "status": None, "total_readings": len(own)}
        t["rec"] = rec
        recs[(t["label"], c["cell"])] = rec
        if len(own) >= NEED:
            rec["status"] = "not needed"
            write(t)
    active = [t for t in todo if t["rec"]["status"] is None]
    for t in active:
        c = t["cell"]
        # every held panel of a cell reads the same roster and model: carry them for the check
        t["ufp"] = {p.get("universe_fingerprint") for p in c["held"]}
        t["mfp"] = {p.get("model_fingerprint") for p in c["held"]}
        refused = check_protocol(t, commit) if ModelParams is not None else []
        if refused:
            t["rec"]["status"] = "refused: " + "; ".join(refused)
            write(t)
            say(f"REFUSED {t['label']} {c['cell']}: {t['rec']['status']}")
    active = [t for t in active if t["rec"]["status"] is None]
    for t in active:
        say(f"  {t['label']:17s} {t['cell']['cell']}: {t['rec']['own_readings']} readings from "
            f"{len(t['rec']['own_seeds'])} own seeds, extending")
    for k in range(BLOCKS):
        if not active:
            break
        seeds = block_seeds(k)
        specs = []
        for t in active:
            c = t["cell"]
            n, rseed = c["held_roster"]
            specs += [(t["label"], c["cell"], s, c["base"], c.get("overrides") or {}, t["days"], n, rseed)
                      for s in seeds]
        t0 = now()
        got = {}
        for label, name, seed, r in measure_map(specs):
            got[(label, name, seed)] = r
        still = []
        for t in active:
            rec, name = t["rec"], t["cell"]["cell"]
            rows = [got[(t["label"], name, s)] for s in seeds]
            for r in rows:
                if r["days"] != t["days"] or r["burn"] != 0:
                    sys.exit(f"REFUSED: seed {r['seed']} of {name} read days {r['days']} burn {r['burn']}")
                if t.get("mfp") and r["model_fingerprint"] not in t["mfp"]:
                    sys.exit(f"REFUSED: seed {r['seed']} of {name} ran model {r['model_fingerprint']}, the cell {t['mfp']}")
                if t.get("ufp") and r["universe_fingerprint"] not in t["ufp"]:
                    sys.exit(f"REFUSED: seed {r['seed']} of {name} ran roster {r['universe_fingerprint']}, the cell {t['ufp']}")
            read = {r["seed"]: r[ROW] for r in rows if r[ROW] is not None}
            rec["total_readings"] += len(read)
            rec["blocks"].append({"seeds": [seeds[0], seeds[-1]], "readings": {str(s): v for s, v in read.items()},
                                  "absent": len(rows) - len(read),
                                  "count_after": rec["total_readings"], "elapsed_s": round(now() - t0, 1)})
            say(f"  {t['label']:17s} {name}: block {seeds[0]}-{seeds[-1]} read {len(read)}, "
                f"count {rec['total_readings']}")
            if rec["total_readings"] >= NEED:
                rec["status"] = "reached"
            elif k == BLOCKS - 1:
                rec["status"] = "cap"
            else:
                still.append(t)
            if rec["status"]:
                write(t)
        active = still
    return recs


def write(t):
    t["path"].parent.mkdir(parents=True, exist_ok=True)
    json.dump(t["rec"], open(t["path"], "w"), indent=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="the box's OUT, holding cert/, heldseeds/, heldu/")
    ap.add_argument("--workers", type=int, default=int(os.environ.get("CERT_WORKERS", "48")))
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    _bootstrap()
    commit = subprocess.run(["git", "-C", ENGINE, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip() or None
    todo = plan(out)
    # a cell whose record is already written is done (a restarted job skips it)
    done = [t for t in todo if t["path"].exists()]
    for t in done:
        print(f"  {t['label']:17s} {t['cell']['cell']}: {t['path'].name} exists, skipped", flush=True)
    todo = [t for t in todo if not t["path"].exists()]
    print(f"crisis extension: {len(todo)} cells, need {NEED} readings, blocks of {BLOCK} from {FIRST}, "
          f"last {FIRST + BLOCK * BLOCKS - 1}, {a.workers} workers, engine {commit}", flush=True)
    t_all = time.time()
    with mp.get_context("spawn").Pool(a.workers, initializer=_bootstrap) as pool:
        recs = run(todo, lambda specs: pool.imap_unordered(_job, specs), commit=commit,
                   say=lambda s: print(s, flush=True))
    for (label, name), r in recs.items():
        used = len(r["blocks"])
        print(f"  {label:17s} {name:12s} own {r['own_readings']:2d}  blocks {used:2d}  total {r['total_readings']:3d}  "
              f"{r['status']}", flush=True)
    print(f"crisis extension done in {time.time() - t_all:.0f}s", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
