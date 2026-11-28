# B9 start-up pass chance: draw many 30-history + 20-opening sets (the grade's layout) from a big pool on non-grade seeds
import sys, json, numpy as np
sys.path.insert(0, ".")
import grade_xsec as G, desk
from multiprocessing import Pool
arm = open(sys.argv[1]).read().strip(); name, _, b = arm.partition("@"); base, _, ds = b.partition(":")
dials = {k: float(v) for k, v in (x.split("=") for x in ds.split(","))}
NH, NO, W = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
if __name__ == "__main__":
    with Pool(W) as p:
        H = p.map(desk.run, [(base, name, dials, s, 492, 40, 111) for s in range(30001, 30001 + NH)])
        O = p.map(G.opening, [(base, name, dials, 95000 + i, 96000 + i) for i in range(1, NO + 1)])
    hf = np.array([h["b9_first60"] for h in H]); hs = [np.array(h["b9_steady60"]) for h in H]
    of = np.array([x["b9_first60"] for x in O]); os_ = [np.array(x["b9_steady60"]) for x in O]
    json.dump({"hf": hf.tolist(), "hs": [x.tolist() for x in hs], "of": of.tolist(), "os": [x.tolist() for x in os_]}, open(sys.argv[5], "w"))
    rng = np.random.default_rng(1); r = []
    for _ in range(20000):
        i = rng.choice(NH, 30, replace=False); j = rng.choice(NO, 20, replace=False)
        f = np.r_[hf[i], of[j]]; st = np.concatenate([hs[k] for k in i] + [os_[k] for k in j])
        r.append(f.std(ddof=1) / st.std(ddof=1))
    r = np.array(r)
    allf = np.r_[hf, of]; allst = np.concatenate(hs + os_)
    print(name, "pool H%d O%d ratio %.3f | sets: median %.3f  P(<=1.5) %.3f  P(>=2/3) %.3f  q10 %.3f q90 %.3f" % (
        NH, NO, allf.std(ddof=1) / allst.std(ddof=1), np.median(r), (r <= 1.5).mean(), (r >= 2/3).mean(), *np.quantile(r, [.1, .9])))
