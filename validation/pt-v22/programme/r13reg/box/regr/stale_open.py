"""Probe 1e: macro news published at the open before prices move.

The macro step runs after the close, and its results (policy rate,
corporate yield, cycle phase) are readable at the next open, when an agent
in tf.evaluate acts, at the previous close's price. Fair value reads the
new corporate yield from the first tick. This measures:

  1. the index's return in the first 65-minute bar and the rest of the day
     after a published policy-rate hike or cut;
  2. the correlation of the overnight change in the published corporate
     yield with the next session's index return, and a rule that holds the
     roster only when that change is <= 0, against buy-and-hold;
  3. the cycle rule again with one extra session of delay (trade at the
     close of the day the phase is first read), to show the edge is not
     only the opening timing.
"""
import numpy as np

from strategies import load, index_series, run_weights, ew, ts_weights, CYCLES

ms = load("long")
b0h, resth, b0c, restc = [], [], [], []
cors = []; ex_y = []; ex_cyc_delay = []; ex_cyc = []
for m in ms:
    ix = index_series(m)
    b = np.exp(np.log(m["b65"]).mean(2))       # D x 6 index by bar
    op = np.exp(np.log(m["open"]).mean(1))
    fed = m["m_federal_funds_rate"]; y = m["m_corporate_bond_yield"]
    for d in range(1, len(ix)):
        ch = fed[d] - fed[d - 1]
        if abs(ch) > 1e-9:
            bar0 = np.log(b[d, 0] / op[d]); rest = np.log(b[d, 5] / b[d, 0])
            (b0h if ch > 0 else b0c).append(bar0); (resth if ch > 0 else restc).append(rest)
    dy = np.diff(y)                               # dy[d-1] = y[d] - y[d-1], read at the open of d
    r = np.diff(np.log(ix))                       # r[d-1] = session d
    cors.append(np.corrcoef(dy, r)[0, 1])
    on = np.r_[dy <= 0, True]                     # W[d-1] decided at the open of d
    W = ts_weights(m, on)
    rr = run_weights(m, W); bh = run_weights(m, ew(m))
    yrs = len(rr) / 252
    ex_y.append(((np.prod(1 + rr)) ** (1 / yrs) - (np.prod(1 + bh)) ** (1 / yrs)) * 100)
    cy = m["cyc"]
    bad = np.isin(cy, [CYCLES.index("contraction"), CYCLES.index("trough")])
    for shift, store in ((1, ex_cyc), (0, ex_cyc_delay)):
        onc = ~np.r_[bad[shift:], [bad[-1]] * shift] if shift else ~bad
        rr = run_weights(m, ts_weights(m, onc))
        store.append(((np.prod(1 + rr)) ** (1 / yrs) - (np.prod(1 + bh)) ** (1 / yrs)) * 100)
print(f"after a published hike: first bar {np.mean(b0h)*1e4:+.1f}bp, rest of day {np.mean(resth)*1e4:+.1f}bp (n={len(b0h)})")
print(f"after a published cut:  first bar {np.mean(b0c)*1e4:+.1f}bp, rest of day {np.mean(restc)*1e4:+.1f}bp (n={len(b0c)})")
print(f"corr(overnight change in corporate yield, next session index return): mean {np.mean(cors):+.3f}")
ex_y = np.array(ex_y)
print(f"hold only when the published corporate yield did not rise overnight: {ex_y.mean():+.2f} pts/yr vs buy-and-hold, beat {np.sum(ex_y>0)}/{len(ex_y)}")
for name, x in (("cycle rule, read at the open and traded then", ex_cyc), ("cycle rule, traded at that day's close (one session later)", ex_cyc_delay)):
    x = np.array(x)
    print(f"{name}: {x.mean():+.2f} pts/yr, median {np.median(x):+.2f}, beat {np.sum(x>0)}/{len(x)}")
