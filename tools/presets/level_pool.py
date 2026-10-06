"""Measure a preset's pooled level read, `facts.LEVEL_POOL`, and record it.

    python tools/presets/level_pool.py measure pt-v20 out/pool --workers 8
    python tools/presets/level_pool.py record out/pool/pt-v20.json
    python tools/presets/level_pool.py record out/pool/pt-v20.json --check

`measure` runs `facts.LEVEL_PROTOCOL`'s varying-roster protocol on every
seed of `facts.LEVEL_POOL`: `facts.measure` on `Universe.random(40,
seed=s)` with market seed `s`, 252 days, the preset BY NAME. It keeps each
seed's level rows and the tail row's two counts, in the form the long-run
grade's pooled reads are published in, so a file from here and one of
those can be compared seed by seed (`--compare`).

`record` pools the file by the library's own estimators
(`facts.aggregate_panels`, `envelope.tail_block`) and writes the result
into `python/tradefloor/presets/<preset>.json` as
`level_protocol["pooled"]`, stamped with the record's coefficient digest
so `record.py --level-rows` can carry it while the preset is unchanged
(`restamp.py` keeps the stamp in step with an inert dial). It refuses a
file whose seeds are not `facts.LEVEL_POOL`'s, whose runs do not carry the
preset's own fingerprint, or whose preset's coefficients have moved since
the record was measured.
Nothing else in the record is touched.

Memory bounds the pool: a 252-day run holds about 1 GB.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import statistics
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[2]
RECORDS = ROOT / "python" / "tradefloor" / "presets"

#: Each seed's fields, as the grade's pooled reads keep them.
KEEP = ("seed", "model_fingerprint", "days", "instruments",
        "index_tail_dn3_hits", "index_tail_dn3_sessions",
        "index_tail_dn3_pct", "index_tail_up3_hits",
        "index_tail_up3_sessions", "index_drift_pct", "annualised_vol_pct",
        "index_excess_kurtosis", "fear_gauge_dn1", "fear_gauge_dn3",
        "fear_gauge_dn3_sessions")
UNIVERSE_N = 40


def run_one(job):
    """One seed's kept fields. Top level, because a process pool pickles it."""
    preset, seed, days = job
    import tradefloor as tf
    from tradefloor import facts

    model = tf.ModelParams.from_preset(preset)
    universe = list(tf.Universe.random(UNIVERSE_N, seed=seed))
    panel = facts.measure(seed=seed, universe=universe, days=days, model=model)
    return {k: panel.get(k) for k in KEEP}


def git_head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def measure(args) -> int:
    import tradefloor as tf
    from tradefloor import facts

    pool = facts.LEVEL_POOL
    if pool["roster"] != "Universe.random(%d, seed=<seed>)" % UNIVERSE_N:
        raise SystemExit("REFUSED: facts.LEVEL_POOL draws %r and this tool "
                         "draws Universe.random(%d, seed=<seed>)"
                         % (pool["roster"], UNIVERSE_N))
    model = tf.ModelParams.from_preset(args.preset)
    if model.fingerprint != args.preset:
        raise SystemExit("REFUSED: %s resolves to fingerprint %s, not its "
                         "own name" % (args.preset, model.fingerprint))
    seeds = list(pool["seeds"])
    print("preset %s  version %s  %d seeds (%s)  %d days  %d workers"
          % (args.preset, tf.version(), len(seeds), pool["seed_list"],
             pool["days"], args.workers), flush=True)
    started = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        panels = list(ex.map(run_one, [(args.preset, s, pool["days"])
                                       for s in seeds], chunksize=2))
    wrong = sorted({str(p["model_fingerprint"]) for p in panels}
                   - {args.preset})
    if wrong:
        raise SystemExit("REFUSED: runs came back on %s" % ", ".join(wrong))
    doc = {
        "kind": "level_pool",
        "preset": args.preset,
        "fingerprint": model.fingerprint,
        "package_version": tf.version(),
        "commit": git_head(),
        "date": time.strftime("%Y-%m-%d"),
        "protocol": "facts.LEVEL_POOL: facts.LEVEL_PROTOCOL's run, "
                    "Universe.random(40, seed=s), market seed s, 252 days",
        "seeds": pool["seed_list"],
        "n": len(seeds),
        "wall_seconds": round(time.time() - started, 1),
        "workers": args.workers,
        "panels": panels,
    }
    out = pathlib.Path(args.out) / f"{args.preset}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps(doc), encoding="utf-8", newline="\n")
    summary = pooled(panels)
    print("%s: index_tail_dn3_pct %.4f (%d of %d, se %.4f); index_drift_pct "
          "%.4f (se %.4f) over %d seeds"
          % (args.preset, summary["index_tail_dn3_pct"]["value"],
             summary["index_tail_dn3_pct"]["hits"],
             summary["index_tail_dn3_pct"]["sessions"],
             summary["index_tail_dn3_pct"]["se"],
             summary["index_drift_pct"]["value"],
             summary["index_drift_pct"]["se"], len(panels)), flush=True)
    if args.compare:
        compare(panels, args.compare)
    print("wrote", out)
    return 0


def dumps(doc: dict) -> str:
    """The file's spelling: one key per line, and one seed's panel per line."""
    head = {k: v for k, v in doc.items() if k != "panels"}
    lines = ["{"] + [f" {json.dumps(k)}: {json.dumps(v)}," for k, v in head.items()]
    lines.append(' "panels": [')
    rows = [f"  {json.dumps(p)}" for p in doc["panels"]]
    lines.append(",\n".join(rows))
    lines += [" ]", "}"]
    return "\n".join(lines) + "\n"


def pooled(panels) -> dict:
    """The two rows by their own estimators, with their standard errors.

    The standard errors are the grade's (`cert_reg18.py`'s `pooled_se`):
    the across-seed sd of the hits over root n and the mean session count
    for the tail row, the across-seed sd over root n for the drift row.
    """
    from tradefloor import facts

    agg = facts.aggregate_panels(panels, facts.LEVEL_POOL["rows"])
    hits = [p["index_tail_dn3_hits"] for p in panels]
    sessions = [p["index_tail_dn3_sessions"] for p in panels]
    drift = [p["index_drift_pct"] for p in panels]
    n = len(panels)
    return {
        "index_tail_dn3_pct": {
            "value": agg["index_tail_dn3_pct"],
            "hits": sum(hits), "sessions": sum(sessions),
            "se": 100.0 * statistics.stdev(hits) / n ** 0.5
                  / statistics.fmean(sessions),
        },
        "index_drift_pct": {
            "value": agg["index_drift_pct"],
            "sd": statistics.stdev(drift),
            "se": statistics.stdev(drift) / n ** 0.5,
        },
    }


def compare(panels, path) -> None:
    """Seed by seed against a published pooled read of the same preset."""
    other = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    theirs = {p["seed"]: p for p in other["panels"]}
    ours = {p["seed"]: p for p in panels}
    if sorted(theirs) != sorted(ours):
        print("COMPARE: the seed sets differ")
        return
    differ = [s for s in ours
              if any(ours[s].get(k) != theirs[s].get(k) for k in KEEP)]
    print("COMPARE %s: %d of %d seeds identical in every kept field"
          % (path, len(ours) - len(differ), len(ours)))


def place_pooled(level_protocol: dict, block: dict) -> dict:
    """`level_protocol` with `pooled` set, placed after `certified_crisis`."""
    out = {}
    for k, v in level_protocol.items():
        if k == "pooled":
            continue
        out[k] = v
        if k == "certified_crisis":
            out["pooled"] = block
    out.setdefault("pooled", block)
    return out


def record(args) -> int:
    import tradefloor as tf
    from tradefloor import envelope, facts

    sys.path.insert(0, str(ROOT))
    from tools.presets.record import coefficient_digest, recorded_values

    doc = json.loads(pathlib.Path(args.file).read_text(encoding="utf-8"))
    name = doc.get("preset") or doc["arm"]
    panels = doc["panels"]
    seeds = [int(p["seed"]) for p in panels]
    if seeds != list(facts.LEVEL_POOL["seeds"]):
        raise SystemExit("REFUSED: %s's seeds are not facts.LEVEL_POOL's %s"
                         % (args.file, facts.LEVEL_POOL["seed_list"]))
    fps = {str(p.get("model_fingerprint")) for p in panels}
    if fps != {name}:
        raise SystemExit("REFUSED: %s's runs carry %s, not %s"
                         % (args.file, sorted(fps), name))
    path = RECORDS / f"{name}.json"
    rec = json.loads(path.read_text(encoding="utf-8"))
    values = recorded_values(tf.ModelParams.from_preset(name).to_dict())
    if coefficient_digest(values) != rec["coefficient_digest"]:
        raise SystemExit("REFUSED: %s's record was measured on other "
                         "coefficients than this build's %s" % (name, name))
    if "level_protocol" not in rec:
        raise SystemExit("REFUSED: %s's record carries no level_protocol "
                         "block, so it has no one-year level rows to read "
                         "pooled" % name)
    rows = pooled(panels)
    tail = envelope.tail_block(panels, stationary_opening=None)
    block = {
        "rows": rows,
        # The vector the runs were measured on, by the record's own digest,
        # so `record.py --level-rows` can carry the block onto a rewritten
        # level block while the preset is unchanged and drop it once it is
        # not.
        "coefficient_digest": rec["coefficient_digest"],
        "seeds": doc["seeds"],
        "n": len(panels),
        "days": facts.LEVEL_POOL["days"],
        "roster": facts.LEVEL_POOL["roster"],
        "estimator": facts.LEVEL_POOL["estimator"],
        "tail": {k: tail[k] for k in (
            "zero_share", "five_or_more_share", "max_hits", "se_m",
            "real_centre", "se_real", "z_r")},
        "measured": {
            "package_version": args.package_version or doc.get("package_version"),
            "commit": args.commit or doc.get("commit"),
            "date": args.date or doc.get("date"),
            "protocol": doc.get("protocol"),
            "source": args.source or str(args.file),
        },
    }
    lp = rec["level_protocol"]
    if lp.get("pooled") == block:
        print("%s: level_protocol.pooled already current" % name)
        return 0
    print("%s: index_tail_dn3_pct %.4f, index_drift_pct %.4f over %d seeds"
          % (name, rows["index_tail_dn3_pct"]["value"],
             rows["index_drift_pct"]["value"], len(panels)))
    if args.check:
        return 1
    rec["level_protocol"] = place_pooled(lp, block)
    path.write_text(json.dumps(rec, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8", newline="\n")
    print("wrote", path)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure", help="run the pooled read for one preset")
    m.add_argument("preset", help="the preset, BY NAME")
    m.add_argument("out", help="directory for <preset>.json")
    m.add_argument("--workers", type=int, default=4)
    m.add_argument("--compare", help="a published pooled read of the same "
                                     "preset to compare seed by seed")
    r = sub.add_parser("record", help="write a pooled read into its record")
    r.add_argument("file", help="a file `measure` wrote, or a published "
                                "pooled read of the same form")
    r.add_argument("--source", help="what to name as the read's source")
    r.add_argument("--commit", help="the engine commit, for a file that does "
                                    "not carry its own")
    r.add_argument("--package-version", help="the package version, likewise")
    r.add_argument("--date", help="the measurement date, likewise")
    r.add_argument("--check", action="store_true",
                   help="report what would change and write nothing")
    args = ap.parse_args()
    return measure(args) if args.cmd == "measure" else record(args)


if __name__ == "__main__":
    sys.exit(main())
