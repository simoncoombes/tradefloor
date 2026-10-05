"""V1, the long-horizon variance ratio (audit major 5), proposed row.

    python v1.py OUTDIR [--arms A,B,...] [--out v1.json] [--boot 1000]

Reads the long run's free histories, OUTDIR/longrun/ARM/SEED-free.npz (the
cap-weighted certified-roster index at every close, as B7 and B9 read it).
Drops each history's first 252 sessions (the calm opening, as the pooled
long-run rows do). Daily log returns; overlapping h-session sums; the mean
removed is the POOLED mean over every history of the arm (the model's
expected return is one number, so no per-history small-sample bias).

    VR(h) = mean over histories and windows of (sum - h mu)^2 / (h var1)
    V1a   = VR(504) / VR(252)     two years against one
    V1b   = VR(1260) / VR(252)    five years against one

Both relative to one year, so the row reads how much of a year's variance
survives to two and five years, not the short-horizon level (B7, B9 and C4a
read that). The CI is a bootstrap over histories.

Bands (proposed): V1a [0.75, 1.15], V1b [0.55, 1.20]; see the proposal.
"""
import argparse, glob, json, os
import numpy as np

H = (252, 504, 1260)
BANDS = {"V1a": (0.75, 1.15), "V1b": (0.55, 1.20)}
BURN = 252


def parts(lrs, mu):
    num = np.zeros((len(lrs), len(H))); cnt = np.zeros((len(lrs), len(H)))
    v1 = np.array([np.sum((x - mu) ** 2) for x in lrs]); n1 = np.array([len(x) for x in lrs], float)
    for i, x in enumerate(lrs):
        c = np.concatenate([[0.0], np.cumsum(x - mu)])
        for j, h in enumerate(H):
            a = c[h:] - c[:-h]
            num[i, j] = np.sum(a * a); cnt[i, j] = len(a)
    return num, cnt, v1, n1


def vr(num, cnt, v1, n1, idx):
    var1 = v1[idx].sum() / n1[idx].sum()
    return (num[idx].sum(0) / cnt[idx].sum(0)) / (np.array(H) * var1)


def arm(adir, boot, rng):
    lrs = []
    for f in sorted(glob.glob(os.path.join(adir, "*-free.npz"))):
        lv = np.load(f)["level"][BURN:]
        lrs.append(np.diff(np.log(lv)))
    if not lrs:
        return None
    mu = np.mean(np.concatenate(lrs))
    P = parts(lrs, mu)
    n = len(lrs)
    pt = vr(*P, np.arange(n))
    bs = np.array([vr(*P, rng.integers(0, n, n)) for _ in range(boot)])
    out = {"histories": n, "vr": {str(h): float(v) for h, v in zip(H, pt)}}
    for key, j in (("V1a", 1), ("V1b", 2)):
        r = bs[:, j] / bs[:, 0]
        lo, hi = BANDS[key]
        val = float(pt[j] / pt[0])
        out[key] = {"value": val, "ci95": [float(np.percentile(r, 2.5)), float(np.percentile(r, 97.5))],
                    "band": [lo, hi], "pass": bool(lo <= val <= hi)}
    out["pass"] = out["V1a"]["pass"] and out["V1b"]["pass"]
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir"); ap.add_argument("--arms", default="")
    ap.add_argument("--out"); ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args()
    root = os.path.join(a.outdir, "longrun")
    names = a.arms.split(",") if a.arms else sorted(d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d)))
    rng = np.random.default_rng(20260925)
    res = {}
    for n in names:
        r = arm(os.path.join(root, n), a.boot, rng)
        if r is None:
            continue
        res[n] = r
        print(f"{n:14s} n={r['histories']:3d}  VR 1y {r['vr']['252']:.3f} 2y {r['vr']['504']:.3f} 5y {r['vr']['1260']:.3f}  "
              f"V1a {r['V1a']['value']:.3f} [{r['V1a']['ci95'][0]:.2f},{r['V1a']['ci95'][1]:.2f}] {'pass' if r['V1a']['pass'] else 'FAIL'}  "
              f"V1b {r['V1b']['value']:.3f} [{r['V1b']['ci95'][0]:.2f},{r['V1b']['ci95'][1]:.2f}] {'pass' if r['V1b']['pass'] else 'FAIL'}")
    if a.out:
        json.dump({"kind": "v1", "bands": BANDS, "burn": BURN, "arms": res}, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
