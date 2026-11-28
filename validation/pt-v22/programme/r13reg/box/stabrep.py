"""Decade table for stab60.py output: per arm, pooled over seeds and the 40 names of the certified roster.
usage: stabrep.py DIR"""
import sys, glob, collections, numpy as np
d = sys.argv[1]
arms = collections.defaultdict(list)
for f in sorted(glob.glob(f"{d}/*.npz")):
    arms[f.split("/")[-1].rsplit("-", 1)[0]].append(f)
for arm, fs in arms.items():
    Z = [np.load(f) for f in fs]
    D = Z[0]["closes"].shape[0]; Y = D // 252
    print(f"\n{arm}: {len(fs)} seeds x 40 names x {Y} years, fingerprint {Z[0]['fp']}, mean elapsed {np.mean([z['elapsed'] for z in Z]):.0f} s")
    print("  years    vol(med) name-days|r|>20%   max|r|  at floor  at cap  tick-AC mean  tick-AC worst  tick|r|   idx ann.ret  VIX mean")
    for k in range(0, Y, 10):
        sl = slice(k * 252, min((k + 10) * 252, D))
        vols, big, mx, fl, cp, ret, vx = [], 0, 0.0, 0, 0, [], []
        n = 0
        for z in Z:
            c = z["closes"].astype(float); p0 = z["p0"]
            lc = np.log(np.vstack([p0[None, :], c]))
            r = np.diff(lc, axis=0)[sl]
            vols.append(np.nanmedian(np.nanstd(r, axis=0)) * np.sqrt(252))
            big += int(np.sum(np.abs(r) > 0.2)); n += r.size; mx = max(mx, float(np.nanmax(np.abs(r))))
            cc = c[sl]; fl += int(np.sum(cc <= 0.0101)); cp += int(np.sum(cc >= 49999))
            ew = np.nanmean(r, axis=1); ret.append(np.sum(ew) / (r.shape[0] / 252)); vx.append(np.nanmean(z["vix"][sl]))
        ys = slice(k, min(k + 10, Y))
        acm = np.nanmean([np.nanmean(z["ac_mean"][ys]) for z in Z]); acw = np.nanmin([np.nanmin(z["ac_min"][ys]) for z in Z])
        ar = np.nanmean([np.nanmean(z["absr"][ys]) for z in Z])
        print(f"  {k:3d}-{min(k+10,Y):3d}  {np.median(vols):7.3f}  {big:7d} ({100*big/n:5.2f}%)  {mx:6.2f}  {100*fl/n:7.3f}% {100*cp/n:6.3f}%  {acm:+11.3f}  {acw:+12.3f}  {ar:8.1e}  {100*np.median(ret):+9.2f}  {np.mean(vx):7.1f}")
