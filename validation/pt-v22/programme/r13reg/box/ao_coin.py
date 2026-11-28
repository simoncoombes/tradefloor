"""ao_coin.py OUT.json ARM DIALS [--workers N] -- AO1 as owner decision 10 (2026-09-28) grades it: the book coin.

For every seed of the pooled arrival-order block (AOPOOL_FIRST, 400 seeds; seedplan protocol aopool) the arm's engine
(pt-v20 with the arm's dials, roster Universe.random(20, seed=111), as e7_arm.py builds it) is asked for the arrival
order of two labels, a and b, at every (day, step) of DAYS days x 6 steps. AO1 is the share of those keys where a
arrives first; the grade needs at least 10^6 keys (400 x 420 x 6 = 1,008,000) and the share within 0.5 +- 0.002.
The per-seed counts are written too, so the desk can check the count and the seeds.
"""
import json, multiprocessing as mp, os, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import seedplan  # noqa: E402

DAYS, STEPS = 420, 6


def one(args):
    seed, dials = args
    import tradefloor as tf
    u = tf.Universe.random(20, seed=111)
    e = tf.Engine(seed=seed, universe=u, model=tf.ModelParams.from_preset("pt-v20", **dials))
    k = sum(e.arrival_order(d, s, ["a", "b"])[0] == "a" for d in range(DAYS) for s in range(STEPS))
    return seed, k


def main(argv):
    out, arm = argv[1], argv[2]
    dials = {k: float(v) for k, v in (x.split("=") for x in (argv[3] if len(argv) > 3 else "").split(",") if x)}
    workers = int(argv[argv.index("--workers") + 1]) if "--workers" in argv else 16
    seeds = seedplan.expand(os.environ.get("AOPOOL_FIRST") or "43001", 400)
    seedplan.guard(seeds, "aopool")
    t0 = time.time()
    with mp.get_context("spawn").Pool(workers) as pool:
        per = dict(pool.map(one, [(s, dials) for s in seeds]))
    import tradefloor as tf
    n = DAYS * STEPS * len(seeds)
    rec = {"kind": "ao_coin", "arm": arm, "dials": dials,
           "fingerprint": tf.ModelParams.from_preset("pt-v20", **dials).fingerprint,
           "seeds": seedplan.fmt(seeds), "days": DAYS, "steps": STEPS, "keys": n,
           "a_first": sum(per.values()), "per_seed": {str(s): per[s] for s in seeds},
           "elapsed_s": round(time.time() - t0, 1)}
    json.dump(rec, open(out, "w"), indent=1)
    print(f"ao_coin {arm}: a first {rec['a_first']}/{n} = {rec['a_first'] / n:.5f}", flush=True)


if __name__ == "__main__":
    main(sys.argv)
