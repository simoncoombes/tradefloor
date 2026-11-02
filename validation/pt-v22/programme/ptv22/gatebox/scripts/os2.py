"""First-close OS1/OS2 per arm, with macro state at open and first close.
usage: os2.py ARMFILE_OR_LINE NAME SEEDS OUTDIR [extra dials k=v,...]"""
import json, sys, time, pathlib
from concurrent.futures import ProcessPoolExecutor

FIELDS = ("vix", "oil_price", "inflation_rate", "federal_funds_rate", "gdp_growth", "cycle", "unemployment_rate", "fear_greed_index", "corporate_bond_yield")

def parse(text):
    head, _, tail = text.partition(":")
    name, _, base = head.partition("@")
    return base or "pt-v20", {k: float(v) for k, v in (kv.split("=") for kv in tail.split(",") if kv)}

def task(spec):
    base, dials, seed = spec
    import tradefloor as tf
    p = tf.ModelParams.from_preset(base, **dials)
    u = list(tf.Universe.random(40, seed=111))
    e = tf.Engine(seed=seed, universe=u, model=p)
    m0 = {k: e.macro_fields[k] for k in FIELDS}
    e.run_days(1, record=False)
    m1 = {k: e.macro_fields[k] for k in FIELDS}
    return {"seed": seed, "open": m0, "close": m1}

if __name__ == "__main__":
    line = sys.argv[1]
    if pathlib.Path(line).exists():
        line = pathlib.Path(line).read_text().strip()
    base, dials = parse(line)
    if len(sys.argv) > 5 and sys.argv[5]:
        for kv in sys.argv[5].split(","):
            k, v = kv.split("="); dials[k] = float(v)
    name = sys.argv[2]
    a, b = map(int, sys.argv[3].split("-"))
    out = pathlib.Path(sys.argv[4]); out.mkdir(parents=True, exist_ok=True)
    workers = int(sys.argv[6]) if len(sys.argv) > 6 else 3
    t = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        res = list(ex.map(task, [(base, dials, s) for s in range(a, b + 1)], chunksize=4))
    json.dump({"name": name, "base": base, "dials": dials, "rows": res}, open(out / f"{name}.json", "w"))
    print(name, len(res), f"{time.time()-t:.0f}s", flush=True)
