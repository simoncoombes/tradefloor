"""d1pool_box.py OUTDIR ARMS_FILE [--workers N] -- D1's tail and drift rows as owner decisions 11 and 12 grade them.

The certification's varying-roster protocol (certrun_box.py `vary`, facts.LEVEL_PROTOCOL: facts.measure on
Universe.random(40, seed=s), market seed s, 252 days, pt-v20 with the arm's dials) on every seed of D1POOL_SEEDS
(seedplan protocol d1pool; 360 seeds, default 201-400,431-590, which skips the old exam block 401-430). One file per
arm, OUTDIR/ARM.json, with each seed's panel rows the level rows read (the tail row's hits and sessions, so the
desk pools them as facts.aggregate_panels does: 100 x the sum of hits over the sum of sessions, and index_drift_pct as
the mean over seeds). certgrade_box_hr.py grades index_tail_dn3_pct (decision 11, 2026-09-28) and index_drift_pct
(decision 12, 2026-09-29) on these pooled readings, with the certification's bands, in place of the 30-seed cert
reading, which it reports beside them.
"""
import json, multiprocessing as mp, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import seedplan  # noqa: E402

KEEP = ("seed", "model_fingerprint", "days", "instruments", "index_tail_dn3_hits", "index_tail_dn3_sessions", "index_tail_dn3_pct", "index_tail_up3_hits",
        "index_tail_up3_sessions", "index_drift_pct", "annualised_vol_pct", "index_excess_kurtosis",
        "fear_gauge_dn1", "fear_gauge_dn3", "fear_gauge_dn3_sessions")


def parse_arm(line):
    head, _, body = line.partition(":")
    name, _, base = head.strip().partition("@")
    return name.strip(), base.strip() or "pt-v20", {k.strip(): float(v) for k, v in
                                                     (p.split("=", 1) for p in body.split(",") if p.strip())}


def job(spec):
    name, base, ov, seed = spec
    import tradefloor as tf
    from tradefloor import facts
    m = tf.ModelParams.from_preset(base, **ov)
    p = facts.measure(seed=seed, universe=list(tf.Universe.random(40, seed=seed)), days=252, model=m)
    return name, seed, {k: p.get(k) for k in KEEP}


def main(argv):
    out, arms_file = argv[1], argv[2]
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 90
    seeds = seedplan.parse(os.environ.get("D1POOL_SEEDS") or "201-400,431-590")
    seedplan.guard(seeds, "d1pool")
    arms = [parse_arm(l) for l in (x.split("#")[0].strip() for x in open(arms_file)) if l]
    os.makedirs(out, exist_ok=True)
    import tradefloor as tf
    specs = [(n, b, ov, s) for n, b, ov in arms for s in seeds]
    got = {n: {} for n, _, _ in arms}
    t0 = time.time()
    with mp.get_context("spawn").Pool(workers) as pool:
        for n, s, p in pool.imap_unordered(job, specs, chunksize=2):
            got[n][s] = p
    for n, b, ov in arms:
        panels = [got[n][s] for s in seeds]
        hits = sum(p["index_tail_dn3_hits"] for p in panels); ses = sum(p["index_tail_dn3_sessions"] for p in panels)
        rec = {"kind": "d1pool", "arm": n, "base": b, "dials": ov, "fingerprint": tf.ModelParams.from_preset(b, **ov).fingerprint,
               "protocol": "vary: Universe.random(40, seed=s), market seed s, 252 days (facts.LEVEL_PROTOCOL)",
               "seeds": seedplan.fmt(seeds), "n": len(seeds), "panels": panels}
        json.dump(rec, open(os.path.join(out, f"{n}.json"), "w"))
        print(f"d1pool {n}: {len(seeds)} seeds, index_tail_dn3 {100 * hits / ses:.3f} ({hits} of {ses})", flush=True)
    print(f"d1pool done in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv)
