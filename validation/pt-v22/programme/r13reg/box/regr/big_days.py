"""New probe: index days beyond +/-5% (close to close), per decade, and the VIX on them;
the same on the real S&P 500 / VIX tape 1990-2025 (design repo longrun/data/tape.json.gz)."""
import gzip, json
import numpy as np
from strategies import load, index_series

ms = load("long")
up = dn = 0; vup = []; vdn = []; yrs = 0; cy_up = []; cy_dn = []
for m in ms:
    ix = index_series(m); r = np.diff(np.log(ix)); v = m["m_vix"]
    yrs += len(r) / 252
    last = np.exp(np.log(m["b65"][:, 5, :]).mean(1))
    rem = np.log(ix / last)[1:]
    for k in np.where(r > np.log(1.05))[0]:
        up += 1; vup.append(max(v[k + 1], v[k + 2] if k + 2 < len(v) else 0)); cy_up.append(abs(rem[k]) > 0.5 * abs(r[k]))
    for k in np.where(r < np.log(0.95))[0]:
        dn += 1; vdn.append(max(v[k + 1], v[k + 2] if k + 2 < len(v) else 0)); cy_dn.append(abs(rem[k]) > 0.5 * abs(r[k]))
print(f"sim ({len(ms)} x 21y, geometric equal-weight index): up >5% {up / yrs * 10:.1f}/decade (median VIX at next open {np.median(vup) if vup else np.nan:.1f}, share with VIX<25 {np.mean(np.array(vup) < 25) if vup else np.nan:.2f}, share where the close re-mark is most of the move {np.mean(cy_up) if cy_up else np.nan:.2f}); "
      f"down >5% {dn / yrs * 10:.1f}/decade (median VIX {np.median(vdn) if vdn else np.nan:.1f}, share with VIX<25 {np.mean(np.array(vdn) < 25) if vdn else np.nan:.2f}, re-mark-dominated {np.mean(cy_dn) if cy_dn else np.nan:.2f})")
d = json.load(gzip.open("/Users/simoncoombes/Dev/tfd-wt-ptv20/programme/longrun/data/tape.json.gz"))
lv = np.array(d["level"], float); vx = np.array(d["vix"], float); r = np.diff(np.log(lv)); ty = len(r) / 252
u = np.where(r > np.log(1.05))[0]; w = np.where(r < np.log(0.95))[0]
print(f"real S&P 500 1990-2025: up >5% {len(u) / ty * 10:.1f}/decade (median VIX that close {np.median(vx[u + 1]):.1f}, share with VIX<25 {np.mean(vx[u + 1] < 25):.2f}); "
      f"down >5% {len(w) / ty * 10:.1f}/decade (median VIX {np.median(vx[w + 1]):.1f}, share VIX<25 {np.mean(vx[w + 1] < 25):.2f})")
