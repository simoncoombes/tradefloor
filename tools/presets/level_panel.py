"""Measure one preset on `facts.LEVEL_PROTOCOL`, one panel per seed.

    python tools/presets/level_panel.py pt-v19 out/j-level-pt-v19.json --workers 90

`preset_panel.py` measures the fourteen SHAPE rows with the roster HELD at
`Universe.random(40, seed=111)`, and `record.py --panel` turns those into
`envelope.CERTIFIED` and `MEASURED_504`. The other two published blocks --
`envelope.CERTIFIED_LEVEL` and `CERTIFIED_CRISIS` -- are certified on a
different protocol, where the roster VARIES with the seed, and the shape
panel cannot produce them: a level is a property of the roster as much as
of the model, and one draw cannot say what the model does.

That is why this file exists separately, and why it reads the protocol out
of `facts.LEVEL_PROTOCOL` rather than restating it. The seed list, the
horizon and the roster rule are the library's, so a protocol change moves
the measurement instead of leaving the tool quietly on the old one.

    seeds    facts.LEVEL_PROTOCOL["seeds"]    101-130
    days     facts.LEVEL_PROTOCOL["days"]     252
    roster   Universe.random(40, seed=<seed>) per seed

The output is the per-seed panel file `level_rows.py` aggregates and
`envelope.certify` grades. Nothing is aggregated here: the estimator for a
row is the row's own (`facts.AGGREGATE`) and choosing one here would put a
second estimator under the same name.

REFUSES a preset whose fingerprint is not its own name. A preset composed
from `--set` overrides carries a `custom-` fingerprint, and a record is
written onto the preset the artefact NAMES; a measurement that has to be
relabelled by hand to reach a record is the failure this whole path exists
to stop.

Memory, not cores, bounds the pool: `facts.measure` keeps every session's
ticks, so a worker holds about 1 GB at 252 days and about 2 GB at 504.
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

#: The roster the protocol draws. Forty names, the seed varying with the
#: measurement seed -- `facts.LEVEL_PROTOCOL["roster"]` spells the same rule
#: as a string, and `main` checks this against it rather than trusting it.
UNIVERSE_N = 40


def run_one(job):
    """One seed's panel. Top level, because a process pool pickles it."""
    preset, seed, days = job
    import tradefloor as tf
    from tradefloor import facts

    universe = list(tf.Universe.random(UNIVERSE_N, seed=seed))
    started = time.time()
    panel = facts.measure(seed=seed, universe=universe, days=days, model=preset)
    row = {k: v for k, v in panel.items()
           if isinstance(v, (int, float, str, list)) or v is None}
    row["seed"] = seed
    row["seconds"] = time.time() - started
    return row


def git_head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                             capture_output=True, text=True, check=True)
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("preset", help="the preset to measure, BY NAME")
    ap.add_argument("out", help="where to write the per-seed panels")
    ap.add_argument("--days", type=int, default=None,
                    help="override the protocol's horizon; for the 504 ruler "
                         "a note quotes, NOT for the certified block")
    ap.add_argument("--seeds", default=None,
                    help="override the protocol's seeds, as A-B or a list; "
                         "for a smoke run, not for a certified block")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--commit", default=None)
    args = ap.parse_args()

    import tradefloor as tf
    from tradefloor import facts

    protocol = facts.LEVEL_PROTOCOL
    if "seed=<seed>" not in protocol["roster"]:
        raise SystemExit(
            "REFUSED: facts.LEVEL_PROTOCOL now draws its roster as %r and this "
            "tool draws Universe.random(%d, seed=<seed>). The protocol moved "
            "and the tool did not." % (protocol["roster"], UNIVERSE_N))

    # BY NAME, and the fingerprint proves it. `from_preset` on a build that
    # does not carry the preset raises; a build that carries it under a
    # composed vector answers a `custom-` fingerprint, and either way the
    # artefact would name a preset it did not measure.
    try:
        model = tf.ModelParams.from_preset(args.preset)
    except Exception as exc:                      # noqa: BLE001 - reported
        raise SystemExit("REFUSED: %s is not selectable on this build (%s)"
                         % (args.preset, exc))
    if model.fingerprint != args.preset:
        raise SystemExit(
            "REFUSED: %s resolves to fingerprint %s, not its own name, so an "
            "artefact naming it would not describe what ran"
            % (args.preset, model.fingerprint))

    days = args.days if args.days is not None else protocol["days"]
    if args.seeds is None:
        seeds = list(protocol["seeds"])
    elif "-" in args.seeds:
        lo, hi = args.seeds.split("-")
        seeds = list(range(int(lo), int(hi) + 1))
    else:
        seeds = [int(x) for x in args.seeds.split(",")]
    off_protocol = sorted(
        ([] if days == protocol["days"] else ["days"])
        + ([] if tuple(seeds) == tuple(protocol["seeds"]) else ["seeds"]))

    print("preset %s  fingerprint %s  version %s" %
          (args.preset, model.fingerprint, tf.version()))
    print("protocol %s, %d days, seeds %d-%d, %d workers%s"
          % (protocol["roster"], days, seeds[0], seeds[-1], args.workers,
             "  OFF PROTOCOL: " + ", ".join(off_protocol) if off_protocol else ""),
          flush=True)

    jobs = [(args.preset, s, days) for s in seeds]
    rows = []
    started = time.time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        for row in pool.map(run_one, jobs):
            rows.append(row)
            print("  seed %d  drift %+.3f  vol %.2f  tail %s/%s  (%.0fs)" % (
                row["seed"], row.get("index_drift_pct", float("nan")),
                row.get("annualised_vol_pct", float("nan")),
                row.get("index_tail_dn3_hits"), row.get("index_tail_dn3_sessions"),
                row["seconds"]), flush=True)

    # Every seed's panel must carry the fingerprint the preflight checked, or
    # the pool measured something other than what the header claims.
    wrong = sorted({r.get("model_fingerprint") for r in rows} - {model.fingerprint})
    if wrong:
        raise SystemExit(
            "REFUSED: %d seed(s) came back on fingerprint(s) %s, not %s"
            % (len(rows), ", ".join(map(str, wrong)), model.fingerprint))

    drift = [r["index_drift_pct"] for r in rows if r.get("index_drift_pct") is not None]
    doc = {
        "preset": args.preset,
        "model_fingerprint": model.fingerprint,
        # The vector itself, not only its name. `record.py --level-rows`
        # refuses a block whose coefficient VALUES moved since, and it can
        # only do that if the artefact says what it ran.
        "coefficients": {k: v for k, v in sorted(model.to_dict().items())},
        "package_version": tf.version(),
        "commit": args.commit or git_head(),
        "protocol": "facts.LEVEL_PROTOCOL, roster varying with the seed",
        "roster_per_seed": True,
        "universe": "Universe.random(%d, seed=<seed>)" % UNIVERSE_N,
        "days": days,
        "seeds": seeds,
        "off_protocol": off_protocol,
        "wall_seconds": round(time.time() - started, 1),
        "workers": args.workers,
        "summary": {
            "index_drift_pct_mean": statistics.fmean(drift) if drift else None,
            "index_drift_pct_sd": statistics.stdev(drift) if len(drift) > 1 else None,
            "index_drift_pct_se": (statistics.stdev(drift) / len(drift) ** 0.5
                                   if len(drift) > 1 else None),
            "n": len(drift),
        },
        "rows": rows,
    }
    path = pathlib.Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8", newline="\n")
    hits = sum(r.get("index_tail_dn3_hits") or 0 for r in rows)
    sessions = sum(r.get("index_tail_dn3_sessions") or 0 for r in rows)
    print("SUMMARY %s: index_drift_pct mean %+.4f sd %.4f over %d seeds; "
          "tail %d/%d = %.4f pooled"
          % (args.preset, doc["summary"]["index_drift_pct_mean"] or float("nan"),
             doc["summary"]["index_drift_pct_sd"] or 0.0, len(drift), hits, sessions,
             100.0 * hits / sessions if sessions else float("nan")), flush=True)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
