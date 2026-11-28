"""60-year stability (no pandas: the box venv has none) on the certified roster (Universe.random(40, seed=111)), per arm and seed, pooled.
usage: stab60.py OUTDIR WORKERS YEARS SEED_FIRST NSEEDS ARM:dial=v,... [ARM:...]
Writes OUTDIR/ARM-SEED.npz: daily closes (float32), VIX, and per year the tick lag-1 autocorrelation of each
name's prints on the year's last session (mean and min over names) and the mean |tick return|."""
import os, sys, time, json
import numpy as np
from concurrent.futures import ProcessPoolExecutor, as_completed

def run(spec):
    import tradefloor as tf, pyarrow as pa
    out, arm, dials, seed, years = spec
    f64 = lambda b: np.frombuffer(b, dtype="<f8").copy()
    t0 = time.time()
    U = tf.Universe.random(40, seed=111)
    m = tf.ModelParams.from_preset("pt-v20", **dials) if dials else tf.ModelParams.from_preset("pt-v20")
    e = tf.Engine(seed=seed, universe=U, model=m)
    D = years * 252; N = len(e.tickers)
    closes = np.full((D, N), np.nan, dtype=np.float32); vix = np.full(D, np.nan, dtype=np.float32)
    ac_mean, ac_min, absr = [], [], []
    p0 = f64(e.prices())
    for d in range(D):
        last = (d % 252) == 251
        e.run_days(1, record=last, first_day=d)
        closes[d] = f64(e.prices()); vix[d] = e.macro_fields["vix"]
        if last:
            t = pa.table(e.prints())
            tk = np.asarray(t.column("tick").to_numpy(zero_copy_only=False))
            ids = np.asarray(t.column("instrument_id").to_numpy(zero_copy_only=False))
            px = np.asarray(t.column("print").to_numpy(zero_copy_only=False), dtype=float)
            ut, ti = np.unique(tk, return_inverse=True); ui, ii = np.unique(ids, return_inverse=True)
            pp = np.full((len(ut), len(ui)), np.nan); pp[ti, ii] = px
            with np.errstate(all="ignore"):
                r = np.diff(np.log(pp), axis=0)
                ac = [np.corrcoef(r[:-1, i], r[1:, i])[0, 1] for i in range(r.shape[1]) if np.std(r[:, i]) > 0]
            ac_mean.append(float(np.nanmean(ac)) if ac else np.nan); ac_min.append(float(np.nanmin(ac)) if ac else np.nan)
            absr.append(float(np.nanmean(np.abs(r))))
            e.clear_recording()
    np.savez_compressed(os.path.join(out, f"{arm}-{seed}.npz"), closes=closes, vix=vix, p0=p0,
                        ac_mean=np.array(ac_mean), ac_min=np.array(ac_min), absr=np.array(absr),
                        fp=np.array(e.model_fingerprint), elapsed=np.array(time.time() - t0))
    return arm, seed, time.time() - t0

def main():
    out, workers, years, s0, ns = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), int(sys.argv[5])
    os.makedirs(out, exist_ok=True)
    specs = []
    for a in sys.argv[6:]:
        name, _, body = a.partition(":")
        dials = {k: float(v) for k, v in (kv.split("=") for kv in body.split(",") if kv)}
        for s in range(s0, s0 + ns):
            if not os.path.exists(os.path.join(out, f"{name}-{s}.npz")):
                specs.append((out, name, dials, s, years))
    t0 = time.time()
    with ProcessPoolExecutor(workers) as ex:
        for f in as_completed([ex.submit(run, sp) for sp in specs]):
            a, s, t = f.result()
            print(a, s, round(t), "s", round(time.time() - t0), flush=True)

if __name__ == "__main__":
    main()
