"""Probe 5b: sign and size of extreme pins, paired against the same seed.

Each pin is written every day from day 20 to 79 (pin_macro is a one-day
write, so it is re-asserted), then released. Reports the cap-weighted
index's log difference from the unpinned twin at day 79 (end of the pin)
and day 250. Also index volatility against roster size.
"""
import os
from multiprocessing import Pool

import numpy as np
import tradefloor as tf

# r14 screen: AUDIT_DIALS (k=v,...) runs the probe on pt-v20 with those dials, not the default preset
import os as _aos
_AD = {k: float(v) for k, v in (x.split("=") for x in _aos.environ.get("AUDIT_DIALS", "").split(",") if x)}
_ARM = tf.ModelParams.from_preset("pt-v20", **_AD)
def _armE(*a, **k):
    if k.get("model") is None:
        k["model"] = _ARM
    return tf.Engine(*a, **k)
def _armEval(*a, **k):
    if k.get("model") is None:
        k["model"] = _ARM
    return tf.evaluate(*a, **k)

from lib import f64

PINS = {
    "corporate yield 0%": dict(corporate_bond_yield=0.0),
    "corporate yield 12%": dict(corporate_bond_yield=0.12),
    "corporate yield 25%": dict(corporate_bond_yield=0.25),
    "policy rate 0% ": dict(federal_funds_rate=0.0),
    "policy rate 20%": dict(federal_funds_rate=0.2),
    "VIX 10": dict(vix=10.0, vix_sets_variance=True),
    "VIX 80": dict(vix=80.0, vix_sets_variance=True),
    "inflation -5%": dict(inflation_rate=-0.05),
    "inflation 15%": dict(inflation_rate=0.15),
    "cycle contraction": dict(cycle="contraction"),
    "cycle expansion": dict(cycle="expansion"),
}


def run(args):
    name, seed = args
    u = tf.Universe.random(40, seed=111)
    cap = np.array([x.shares_outstanding for x in u])
    res = []
    for arm in (0, 1):
        e = _armE(seed=seed, universe=u)
        ix = []
        for d in range(250):
            if arm and 20 <= d < 80:
                e.pin_macro(**PINS[name])
            e.run_days(1)
            ix.append(np.log((f64(e.prices())[:40] * cap).sum()))
        res.append(np.array(ix))
    return name, res[1] - res[0]


def vol_n(args):
    n, seed = args
    u = tf.Universe.random(n, seed=seed)
    e = _armE(seed=seed, universe=u)
    cap = np.array([x.shares_outstanding for x in u])
    ix = []; vx = []
    for d in range(252):
        e.run_days(1); ix.append(np.log((f64(e.prices())[:n] * cap).sum())); vx.append(e.macro_fields["vix"])
    return n, np.std(np.diff(ix)) * np.sqrt(252), np.mean(vx)


if __name__ == "__main__":
    with Pool(int(os.environ.get("AUDIT_PROCS", "4"))) as p:
        NSEED = int(os.environ.get("AUDIT_PIN_SEEDS", "6"))
        p0 = int(_aos.environ.get("AUDIT_SEED_PROBE", "2401"))   # thirteenth registration's seed variable
        out = p.map(run, [(n, s) for n in PINS for s in range(p0, p0 + NSEED)])
        for n in PINS:
            x = np.array([o[1] for o in out if o[0] == n]) * 100
            print(f"{n:22s} index vs twin at day 79 {x[:, 79].mean():+6.1f}% (seeds {np.round(x[:, 79], 1)}), at day 249 {x[:, 249].mean():+6.1f}%")
        if os.environ.get("AUDIT_SKIP_VOLN"):
            raise SystemExit(0)
        vv = p.map(vol_n, [(n, s) for n in (10, 40, 100, 200) for s in range(11, 17)])
        for n in (10, 40, 100, 200):
            a = np.array([(v[1], v[2]) for v in vv if v[0] == n])
            print(f"N={n:3d}: cap-weighted index vol {a[:, 0].mean()*100:.1f}% (median {np.median(a[:,0])*100:.1f}), mean VIX {a[:, 1].mean():.1f}")
